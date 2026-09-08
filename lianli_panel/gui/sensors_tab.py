"""The sensor editor, and the two-tier command harness.

THE TIERS ARE NOT EQUIVALENT, and the UI must never let them look it:

  AUTHORITATIVE  a throwaway one-widget template whose value_text source is the
                 candidate, sent to RenderTemplatePreview. The daemon runs it
                 as uid lianli, under exactly the conditions the real sensor
                 faces, and returns the number as an image. This is the only
                 tier that proves anything.

  DIAGNOSTIC     the same command run as the current user, capturing stdout,
                 stderr and exit status -- which the image cannot show. Richer,
                 and WRONG UID: it succeeds happily on $HOME paths the daemon
                 cannot traverse. Every label here says so.

BOTH TIERS EXECUTE THE COMMAND, so both are behind a confirmation. Neither ever
runs on the debounced preview path.

Raw stdout is shown beside the parsed value because the invisible failure is a
tool that prints its errors to STDOUT: nvidia-smi rejects -lms2000 that way, and
a parse-each-line-as-data loop reads the usage message as a sample.
"""
from __future__ import annotations

import copy
import json

from PySide6.QtCore import Signal
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListWidget,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QVBoxLayout, QWidget)

from .. import sensors
from ..sensors import Sensor, USER_SCRIPT_DIR
from ..schema import SOURCE_NAMES
from . import forms


PROBE_CONFIRM = (
    "This runs the command for real, as uid lianli, inside the daemon.\n\n"
    "If it has side effects — writing a file, resetting a counter, changing a "
    "device — they will happen now.\n\nRun it?")

DIAGNOSTIC_CONFIRM = (
    "This runs the command for real, as you, not as lianli.\n\n"
    "It can block this window for up to 10 seconds, and it will succeed on "
    "paths under your home directory that the daemon can never reach.\n\nRun it?")

PROBE_CAPTION = (
    "AUTHORITATIVE — the daemon rendered this as uid lianli, under exactly the "
    "conditions the real sensor faces. If the number is wrong here, it is "
    "wrong on the panel.")

DIAGNOSTIC_CAPTION = (
    "NOT AUTHORITATIVE — this ran as you, not as lianli. It sees files under "
    f"your home directory that the daemon cannot, so a pass here is not a pass "
    f"on the panel. Scripts the daemon must read belong in "
    f"{USER_SCRIPT_DIR}.")


class SensorsTab(QWidget):
    library_changed = Signal()
    bind_requested = Signal(str)
    probe_requested = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._library: dict[str, Sensor] = {}
        self._target = (None, None)
        self._loading = False
        self._editors: dict[str, QWidget] = {}

        self.sensor_list = QListWidget()
        self.sensor_list.currentTextChanged.connect(self._selected)
        self.new_button = QPushButton("New")
        self.duplicate_button = QPushButton("Duplicate")
        self.rename_button = QPushButton("Rename")
        self.delete_button = QPushButton("Delete")
        self.new_button.clicked.connect(self._new)
        self.duplicate_button.clicked.connect(self._duplicate)
        self.rename_button.clicked.connect(self._rename)
        self.delete_button.clicked.connect(self.delete_selected)

        library_buttons = QHBoxLayout()
        for button in (self.new_button, self.duplicate_button,
                       self.rename_button, self.delete_button):
            library_buttons.addWidget(button)
        library_layout = QVBoxLayout()
        library_layout.addWidget(QLabel("Sensors"))
        library_layout.addWidget(self.sensor_list)
        library_layout.addLayout(library_buttons)

        self.type_box = QComboBox()
        self.type_box.addItems(list(SOURCE_NAMES))
        self.type_box.currentTextChanged.connect(self._type_changed)
        self.form = QFormLayout()

        self.cmd = QLineEdit()
        self.cmd.textChanged.connect(self._command_changed)
        self.static_notes = QLabel()
        self.static_notes.setWordWrap(True)
        self.static_notes.setStyleSheet("color:#ffd9d9; background:#5a1d1d;")
        self.test_button = QPushButton("Authoritative test")
        self.diagnose_button = QPushButton("Run local diagnostic")
        self.test_button.clicked.connect(self._probe)
        self.diagnose_button.clicked.connect(self._diagnose)

        harness_buttons = QHBoxLayout()
        harness_buttons.addWidget(self.test_button)
        harness_buttons.addWidget(self.diagnose_button)
        command_layout = QVBoxLayout()
        command_form = QFormLayout()
        command_form.addRow("Command", self.cmd)
        command_layout.addLayout(command_form)
        command_layout.addWidget(self.static_notes)
        command_layout.addLayout(harness_buttons)

        self.probe_caption = QLabel(PROBE_CAPTION)
        self.probe_caption.setWordWrap(True)
        self.probe_image = QLabel()
        self.probe_image.setMinimumHeight(60)
        command_layout.addWidget(self.probe_caption)
        command_layout.addWidget(self.probe_image)

        self.diagnostic_caption = QLabel(DIAGNOSTIC_CAPTION)
        self.diagnostic_caption.setWordWrap(True)
        self.diagnostic_output = QPlainTextEdit()
        self.diagnostic_output.setReadOnly(True)
        command_layout.addWidget(self.diagnostic_caption)
        command_layout.addWidget(self.diagnostic_output)

        self.command_group = QGroupBox("Command test harness")
        self.command_group.setLayout(command_layout)
        self.command_group.hide()

        self.target_label = QLabel()
        self.target_label.setWordWrap(True)
        self.bind_button = QPushButton("Bind selected sensor")
        self.bind_button.clicked.connect(self._bind)

        editor_layout = QVBoxLayout()
        editor_layout.addWidget(QLabel("Source type"))
        editor_layout.addWidget(self.type_box)
        editor_layout.addLayout(self.form)
        editor_layout.addWidget(self.command_group)
        editor_layout.addWidget(self.target_label)
        editor_layout.addWidget(self.bind_button)
        editor_layout.addStretch(1)

        root = QHBoxLayout(self)
        root.addLayout(library_layout, 1)
        root.addLayout(editor_layout, 2)

        self.type_box.setEnabled(False)
        self.set_target(None, None, "")

    # --- library ---------------------------------------------------------

    def set_library(self, library: dict) -> None:
        """Replace the editable library without reporting a user change."""
        self._library = {
            str(name): Sensor(str(name), copy.deepcopy(sensor.source))
            for name, sensor in library.items()
        }
        self._refresh_list()

    def library(self) -> dict[str, Sensor]:
        return self._library

    def names(self) -> list[str]:
        return [self.sensor_list.item(i).text()
                for i in range(self.sensor_list.count())]

    def current_name(self) -> str:
        item = self.sensor_list.currentItem()
        return item.text() if item is not None else ""

    def select(self, name: str) -> None:
        matches = self.sensor_list.findItems(name, self._match_exact())
        if matches:
            self.sensor_list.setCurrentItem(matches[0])

    def create(self, name: str) -> None:
        name = name.strip()
        if not name or name in self._library:
            return
        source_type = SOURCE_NAMES[0] if SOURCE_NAMES else "constant"
        self._library[name] = Sensor(name, self._source_for_type(source_type))
        self._refresh_list(name)
        self.library_changed.emit()

    def duplicate_selected(self, name: str) -> None:
        current = self.current_name()
        name = name.strip()
        if not current or not name or name in self._library:
            return
        self._library[name] = Sensor(
            name, copy.deepcopy(self._library[current].source))
        self._refresh_list(name)
        self.library_changed.emit()

    def rename_selected(self, name: str) -> None:
        current = self.current_name()
        name = name.strip()
        if not current or not name or name == current or name in self._library:
            return
        sensor = self._library.pop(current)
        sensor.name = name
        self._library[name] = sensor
        self._refresh_list(name)
        self.library_changed.emit()

    def delete_selected(self) -> None:
        current = self.current_name()
        if not current:
            return
        del self._library[current]
        self._refresh_list()
        self.library_changed.emit()

    @staticmethod
    def _match_exact():
        from PySide6.QtCore import Qt
        return Qt.MatchFlag.MatchExactly

    def _refresh_list(self, selected: str | None = None) -> None:
        self.sensor_list.blockSignals(True)
        self.sensor_list.clear()
        self.sensor_list.addItems(sorted(self._library))
        if selected in self._library:
            self.sensor_list.setCurrentRow(sorted(self._library).index(selected))
        self.sensor_list.blockSignals(False)
        self._load_selected()

    def _new(self) -> None:
        name, ok = QInputDialog.getText(self, "New sensor", "Sensor name:")
        if ok:
            self.create(name)

    def _duplicate(self) -> None:
        if not self.current_name():
            return
        name, ok = QInputDialog.getText(
            self, "Duplicate sensor", "Name for the copy:")
        if ok:
            self.duplicate_selected(name)

    def _rename(self) -> None:
        if not self.current_name():
            return
        name, ok = QInputDialog.getText(self, "Rename sensor", "New name:")
        if ok:
            self.rename_selected(name)

    # --- source form -----------------------------------------------------

    def field_names(self) -> list[str]:
        names = list(self._editors)
        sensor = self._current_sensor()
        if sensor is not None and sensor.source.get("type") == "command":
            names.append("cmd")
        return names

    def _current_sensor(self) -> Sensor | None:
        return self._library.get(self.current_name())

    def _selected(self, _name: str) -> None:
        self._load_selected()

    def _load_selected(self) -> None:
        sensor = self._current_sensor()
        self._loading = True
        try:
            self._clear_form()
            self.type_box.setEnabled(sensor is not None)
            if sensor is None:
                self.command_group.hide()
                self.cmd.clear()
                self.static_notes.clear()
                return

            source_type = str(sensor.source.get("type", ""))
            if self.type_box.findText(source_type) < 0:
                self.type_box.addItem(source_type)
            self.type_box.setCurrentText(source_type)
            self.cmd.setText(str(sensor.source.get("cmd", "")))
            self.command_group.setVisible(source_type == "command")
            self._rebuild_form(sensor.source)
            self._update_static_notes()
        finally:
            self._loading = False

    def _clear_form(self) -> None:
        self._editors.clear()
        while self.form.count():
            item = self.form.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _rebuild_form(self, source: dict) -> None:
        self._clear_form()
        for spec in forms.source_fields_for(source):
            # Command has a dedicated live-check row in the two-tier harness.
            if spec.name == "cmd" and source.get("type") == "command":
                continue
            editor = self._make_editor(spec)
            label = QLabel(spec.name + (" *" if spec.required else ""))
            if spec.note:
                label.setToolTip(spec.note)
                label.setStyleSheet("color:#c9a227;")
            self.form.addRow(label, editor)
            self._editors[spec.name] = editor

    def _make_editor(self, spec: forms.FieldSpec) -> QWidget:
        if spec.kind == "number":
            editor = QDoubleSpinBox()
            editor.setRange(-1_000_000.0, 1_000_000.0)
            editor.setDecimals(6)
            editor.setValue(float(spec.value or 0.0))
            editor.valueChanged.connect(
                lambda value, name=spec.name: self._write_field(name, value))
            return editor
        if spec.kind == "bool":
            editor = QCheckBox()
            editor.setChecked(bool(spec.value))
            editor.toggled.connect(
                lambda value, name=spec.name: self._write_field(name, value))
            return editor

        if spec.kind in ("json", "color"):
            editor = QPlainTextEdit(json.dumps(spec.value))
            editor.setMaximumHeight(70)
            editor.textChanged.connect(
                lambda name=spec.name, control=editor:
                self._write_json_field(name, control.toPlainText()))
            return editor

        if spec.kind == "font":
            value = (spec.value or {}).get("path", "")
            editor = QLineEdit(str(value))
            editor.textChanged.connect(
                lambda value, name=spec.name:
                self._write_field(name, {"path": value}))
            return editor

        editor = QLineEdit("" if spec.value is None else str(spec.value))
        editor.textChanged.connect(
            lambda value, name=spec.name: self._write_field(name, value))
        return editor

    def _write_json_field(self, name: str, raw: str) -> None:
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return
        self._write_field(name, value)

    def _write_field(self, name: str, value) -> None:
        if self._loading:
            return
        sensor = self._current_sensor()
        if sensor is None:
            return
        sensor.source[name] = value
        self.library_changed.emit()

    def _type_changed(self, source_type: str) -> None:
        if self._loading:
            return
        sensor = self._current_sensor()
        if sensor is None or sensor.source.get("type") == source_type:
            return
        sensor.source = self._source_for_type(source_type)
        self._loading = True
        try:
            self.cmd.setText(str(sensor.source.get("cmd", "")))
            self.command_group.setVisible(source_type == "command")
            self._rebuild_form(sensor.source)
            self._update_static_notes()
        finally:
            self._loading = False
        self.library_changed.emit()

    @staticmethod
    def _source_for_type(source_type: str) -> dict:
        source = {"type": source_type}
        for spec in forms.source_fields_for(source):
            if spec.required:
                source[spec.name] = copy.deepcopy(spec.value)
        return source

    # --- two-tier command harness ---------------------------------------

    def _command_changed(self, cmd: str) -> None:
        self._update_static_notes()
        if self._loading:
            return
        sensor = self._current_sensor()
        if sensor is None or sensor.source.get("type") != "command":
            return
        sensor.source["cmd"] = cmd
        self.library_changed.emit()

    def _update_static_notes(self) -> None:
        self.static_notes.setText("\n".join(sensors.static_checks(self.cmd.text())))

    def _probe(self) -> None:
        answer = QMessageBox.question(
            self, "Run authoritative sensor test?", PROBE_CONFIRM,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            self.probe_requested.emit(self.cmd.text())

    def _diagnose(self) -> None:
        answer = QMessageBox.question(
            self, "Run local sensor diagnostic?", DIAGNOSTIC_CONFIRM,
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            result = sensors.run_diagnostic(self.cmd.text(), timeout=10.0)
        except Exception as exc:
            self.diagnostic_caption.setText(DIAGNOSTIC_CAPTION)
            self.diagnostic_output.setPlainText(f"diagnostic failed: {exc}")
            return
        self.diagnostic_caption.setText(DIAGNOSTIC_CAPTION)
        self._render_diagnostic(result)

    def _render_diagnostic(self, d) -> None:
        lines = [f"exit status: {d.exit_code}",
                 f"parsed value: {d.parsed}",
                 "",
                 "stdout (raw — the daemon reads the FIRST WHITESPACE TOKEN of "
                 "this, so an error printed here is read as data):",
                 d.stdout.rstrip() or "(nothing)",
                 "",
                 "stderr:",
                 d.stderr.rstrip() or "(nothing)"]
        if d.problems:
            lines += ["", "problems:"] + [f"  ! {p}" for p in d.problems]
        self.diagnostic_output.setPlainText("\n".join(lines))

    def show_probe(self, jpeg: bytes) -> None:
        self.probe_caption.setText(PROBE_CAPTION)
        pixmap = QPixmap()
        if pixmap.loadFromData(jpeg):
            self.probe_image.setText("")
            self.probe_image.setPixmap(pixmap)
        else:
            self.probe_image.setPixmap(QPixmap())
            self.probe_image.setText("The daemon returned an unreadable image.")

    def show_probe_error(self, message: str) -> None:
        self.probe_caption.setText(PROBE_CAPTION)
        self.probe_image.setPixmap(QPixmap())
        self.probe_image.setText(f"Authoritative probe failed: {message}")

    # --- binding ---------------------------------------------------------

    def set_target(self, template_id, widget_id, description: str) -> None:
        """Which widget 'Bind' would bind to. With tabs the canvas is off
        screen, so this has to be said out loud rather than assumed."""
        self._target = (template_id, widget_id)
        if template_id is None or widget_id is None:
            self.target_label.setText(
                "Nothing selected — select a widget on the Editor tab to bind "
                "a sensor to it.")
            self.bind_button.setEnabled(False)
            return
        self.target_label.setText(
            f"Selected on the Editor tab: {template_id} / {widget_id}"
            + (f" ({description})" if description else ""))
        self.bind_button.setEnabled(True)

    def _bind(self) -> None:
        name = self.current_name()
        if name:
            self.bind_requested.emit(name)
