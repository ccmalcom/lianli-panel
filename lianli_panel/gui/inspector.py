"""The inspector.

Every field here comes from forms.py, which derives them from the schema
extracted from the daemon -- not from what gaming-dash happens to use, which
covers only 7 of 12 kinds.

Range thresholds are shown and typed in REAL UNITS (68, not 60). The stored
value is a percentage of the widget's own span. This is the highest-value
invariant in the app because getting it wrong fails SILENTLY: the panel
renders, nothing errors, the colours are simply wrong.

Changing kind or source type DROPS the fields the new variant does not know.
That is unavoidable -- but it is shown before it happens, never silent.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)
from PySide6.QtGui import QColor

from ..model import Widget
from ..schema import KIND_NAMES, SOURCE_NAMES
from . import forms


class ColorButton(QPushButton):
    changed = Signal(list)

    def __init__(self, rgba: list[int]) -> None:
        super().__init__()
        self._rgba = [int(c) for c in (list(rgba) + [255, 255, 255, 255])[:4]]
        self.clicked.connect(self._pick)
        self._paint()

    def _paint(self) -> None:
        r, g, b, a = self._rgba
        self.setText(f"{r},{g},{b},{a}")
        self.setStyleSheet(f"background: rgb({r},{g},{b});")

    def _pick(self) -> None:
        r, g, b, a = self._rgba
        chosen = QColorDialog.getColor(
            QColor(r, g, b, a), self, "Colour",
            QColorDialog.ShowAlphaChannel)
        if chosen.isValid():
            self._rgba = [chosen.red(), chosen.green(), chosen.blue(),
                          chosen.alpha()]
            self._paint()
            self.changed.emit(list(self._rgba))


class Inspector(QWidget):
    changed = Signal()
    structure_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.widget: Widget | None = None
        self.editors: dict[str, QWidget] = {}

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(list(KIND_NAMES))
        self.kind_combo.activated.connect(self._change_kind)

        self.source_combo = QComboBox()
        self.source_combo.addItems(list(SOURCE_NAMES))
        self.source_combo.activated.connect(self._change_source)

        self.form = QFormLayout()
        self.raw = QPlainTextEdit()
        self.raw.setPlaceholderText("raw kind JSON")
        self.raw.hide()
        self.raw.focusOutEvent = self._raw_committed   # type: ignore[assignment]

        self.ranges = QTableWidget(0, 3)
        self.ranges.setHorizontalHeaderLabels(["threshold", "colour", "alpha"])
        self.ranges.itemChanged.connect(self._range_edited)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("kind"))
        root.addWidget(self.kind_combo)
        root.addWidget(QLabel("source"))
        root.addWidget(self.source_combo)
        root.addLayout(self.form)
        root.addWidget(self.raw)
        root.addWidget(QLabel("ranges (real units)"))
        root.addWidget(self.ranges)
        root.addStretch(1)

    # --- population --------------------------------------------------------

    def set_widget(self, w: Widget | None) -> None:
        self.widget = w
        self._clear()
        if w is None:
            return
        unknown = forms.is_unknown_kind(w)
        self.kind_combo.setEnabled(not unknown)
        if not unknown:
            self.kind_combo.setCurrentText(w.kind_type)
        self.raw.setVisible(unknown)
        if unknown:
            self.raw.setPlainText(json.dumps(w.kind, indent=1))
            return
        src = w.source or {}
        self.source_combo.setVisible(bool(src))
        if src:
            self.source_combo.setCurrentText(str(src.get("type", "")))
        for spec in forms.kind_fields(w):
            self._add_row(spec, target="kind")
        for spec in forms.source_fields(w):
            self._add_row(spec, target="source")
        self._fill_ranges(w)

    def _clear(self) -> None:
        self.editors.clear()
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.ranges.blockSignals(True)
        self.ranges.setRowCount(0)
        self.ranges.blockSignals(False)

    def _add_row(self, spec: forms.FieldSpec, target: str) -> None:
        editor = self._editor(spec, target)
        if editor is None:
            return
        label = QLabel(spec.name + (" *" if spec.required else ""))
        if spec.note:
            label.setToolTip(spec.note)
            label.setStyleSheet("color:#c9a227;")
        self.form.addRow(label, editor)
        self.editors[spec.name] = editor

    def _editor(self, spec: forms.FieldSpec, target: str):
        def write(value):
            obj = self.widget.kind if target == "kind" else self.widget.source
            if obj is not None:
                obj[spec.name] = value
                self.changed.emit()

        if spec.kind == "number":
            e = QDoubleSpinBox()
            e.setRange(-100000.0, 100000.0)
            e.setDecimals(2)
            e.setValue(float(spec.value or 0.0))
            e.valueChanged.connect(write)
            return e
        if spec.kind == "bool":
            e = QCheckBox()
            e.setChecked(bool(spec.value))
            e.toggled.connect(write)
            return e
        if spec.kind == "color":
            e = ColorButton(spec.value or [255, 255, 255, 255])
            e.changed.connect(write)
            return e
        if spec.kind == "font":
            e = QLineEdit(str((spec.value or {}).get("path", "")))
            e.editingFinished.connect(lambda: write({"path": e.text()}))
            return e
        if spec.kind == "json":
            e = QPlainTextEdit(json.dumps(spec.value))
            e.setMaximumHeight(70)
            return e
        e = QLineEdit("" if spec.value is None else str(spec.value))
        e.editingFinished.connect(lambda: write(e.text()))
        return e

    # --- ranges ------------------------------------------------------------

    def _fill_ranges(self, w: Widget) -> None:
        rows = forms.range_rows(w)
        self.ranges.blockSignals(True)
        self.ranges.setRowCount(len(rows))
        for row in rows:
            unit = f" {row.unit}" if row.unit else ""
            text = "—" if row.threshold is None else f"{row.threshold:.4g}{unit}"
            item = QTableWidgetItem(text)
            if row.threshold is None:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip("the catch-all range; it has no threshold")
            self.ranges.setItem(row.index, 0, item)
            swatch = ColorButton(list(row.color) + [row.alpha if row.alpha
                                                    is not None else 255])
            swatch.changed.connect(
                lambda rgba, i=row.index: self._set_range_color(i, rgba))
            self.ranges.setCellWidget(row.index, 1, swatch)
            self.ranges.setItem(row.index, 2, QTableWidgetItem(
                "" if row.alpha is None else str(row.alpha)))
        self.ranges.blockSignals(False)

    def _set_range_color(self, index: int, rgba: list[int]) -> None:
        entry = self.widget.kind["ranges"][index]
        entry["color"] = rgba[:3]
        entry["alpha"] = rgba[3]
        self.changed.emit()

    def _range_edited(self, item: QTableWidgetItem) -> None:
        if self.widget is None:
            return
        entry = self.widget.kind.get("ranges", [])[item.row()]
        if item.column() == 2:
            entry["alpha"] = int(float(item.text() or 255))
            self.changed.emit()
            return
        raw_text = item.text().split()[0] if item.text().strip() else ""
        try:
            raw = float(raw_text)
        except ValueError:
            return
        # False means unchanged: writing anyway would re-encode a percentage
        # the user never touched and drift the stored float on every save.
        if forms.set_threshold(self.widget, item.row(), raw):
            self.changed.emit()

    # --- structural changes ------------------------------------------------

    def _confirm(self, change: forms.Change, what: str) -> bool:
        if not change.dropped:
            return True
        listing = ", ".join(f"{k}={v!r}" for k, v in change.dropped.items())
        answer = QMessageBox.question(
            self, f"Change {what}?",
            f"The new {what} does not have these fields; they will be "
            f"dropped:\n\n{listing}\n\nContinue?")
        return answer == QMessageBox.Yes

    def _change_kind(self) -> None:
        if self.widget is None:
            return
        target = self.kind_combo.currentText()
        if target == self.widget.kind_type:
            return
        preview = forms.change_kind(
            Widget.from_json(json.loads(json.dumps(self.widget.to_json()))),
            target)
        if not self._confirm(preview, "kind"):
            self.kind_combo.setCurrentText(self.widget.kind_type)
            return
        forms.change_kind(self.widget, target)
        self.structure_changed.emit()

    def _change_source(self) -> None:
        if self.widget is None or self.widget.source is None:
            return
        target = self.source_combo.currentText()
        if target == self.widget.source.get("type"):
            return
        preview = forms.change_source(
            Widget.from_json(json.loads(json.dumps(self.widget.to_json()))),
            target)
        if not self._confirm(preview, "source"):
            self.source_combo.setCurrentText(str(self.widget.source.get("type")))
            return
        forms.change_source(self.widget, target)
        self.structure_changed.emit()

    def _raw_committed(self, event) -> None:
        QPlainTextEdit.focusOutEvent(self.raw, event)
        if self.widget is None:
            return
        try:
            self.widget.kind = json.loads(self.raw.toPlainText())
        except json.JSONDecodeError:
            return
        self.changed.emit()
