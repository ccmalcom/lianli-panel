"""The Lighting tab: the ring, both brightnesses, and the poller's settings.

THIS VIEW NEVER TALKS TO THE DAEMON. It emits `changed` when the user edits
something and `test_requested` when they explicitly ask to see a colour on the
ring; the window owns every send. That is not tidiness -- it is what makes a
tab full of hardware controls testable offscreen with no client at all.

THE TWO BRIGHTNESSES ARE DELIBERATELY IN SEPARATE GROUPS. One is 0-4 and drives
the LED ring; the other is 0-255 and drives the panel's backlight. Confusing
them sets the screen to 4/255, which is a black screen the user then cannot
read well enough to undo.

Screen brightness has a "set it" checkbox because the daemon's own state has
three values, not two: a number, or absent-meaning-the-panel's-own-default.
Collapsing absent to 0 would offer "off" as the default.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import lighting
from ..ring import RING_READBACK_NOTE, RingState, ThermalConfig
from .inspector import ColorButton


class LightingTab(QWidget):
    changed = Signal()
    test_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._poller_running = False

        # --- ring ----------------------------------------------------------
        self.mode_buttons = QButtonGroup(self)
        self.off = QRadioButton("Off")
        self.static = QRadioButton("Static colour")
        self.thermal = QRadioButton("Thermal sweep (the poller drives it)")
        for i, button in enumerate((self.off, self.static, self.thermal)):
            self.mode_buttons.addButton(button, i)
            button.toggled.connect(self._mode_toggled)

        self.color = ColorButton([255, 255, 255, 255])
        self.color.changed.connect(self._edited)
        self.ring_brightness = QSpinBox()
        self.ring_brightness.setRange(0, lighting.RING_BRIGHTNESS_MAX)
        self.ring_brightness.valueChanged.connect(self._edited)

        self.test_button = QPushButton("Test on ring")
        self.test_button.setToolTip(
            "Sends this colour to the ring immediately, without applying "
            "anything else. The only control on this tab with a side effect "
            "before Apply.")
        self.test_button.clicked.connect(self.test_requested)

        self.poller_status = QLabel()
        self.last_set = QLabel()
        self.last_set.setWordWrap(True)
        self.interlock = QLabel()
        self.interlock.setWordWrap(True)
        self.interlock.setStyleSheet("color:#ffeec9; background:#54471a;")

        ring_box = QVBoxLayout()
        for button in (self.off, self.static, self.thermal):
            ring_box.addWidget(button)
        colour_row = QHBoxLayout()
        colour_row.addWidget(QLabel("Colour"))
        colour_row.addWidget(self.color, 1)
        colour_row.addWidget(QLabel("Ring brightness"))
        colour_row.addWidget(self.ring_brightness)
        colour_row.addWidget(self.test_button)
        ring_box.addLayout(colour_row)
        ring_box.addWidget(self.poller_status)
        ring_box.addWidget(self.last_set)
        ring_box.addWidget(self.interlock)
        ring_group = QGroupBox("LED ring")
        ring_group.setLayout(ring_box)

        # --- screen --------------------------------------------------------
        self.screen_set = QCheckBox("Set the panel's brightness")
        self.screen_set.toggled.connect(self._screen_toggled)
        self.screen_brightness = QSlider(Qt.Orientation.Horizontal)
        self.screen_brightness.setRange(0, lighting.SCREEN_BRIGHTNESS_MAX)
        self.screen_brightness.valueChanged.connect(self._screen_moved)
        self.screen_value = QSpinBox()
        self.screen_value.setRange(0, lighting.SCREEN_BRIGHTNESS_MAX)
        self.screen_value.valueChanged.connect(self.screen_brightness.setValue)

        screen_row = QHBoxLayout()
        screen_row.addWidget(self.screen_brightness, 1)
        screen_row.addWidget(self.screen_value)
        screen_box = QVBoxLayout()
        screen_box.addWidget(self.screen_set)
        screen_box.addLayout(screen_row)
        screen_box.addWidget(QLabel(
            "0-255, and unrelated to the ring's 0-4. Applying persists it in "
            "config.lcds so it survives a daemon restart."))
        screen_group = QGroupBox("Panel brightness")
        screen_group.setLayout(screen_box)

        # --- poller --------------------------------------------------------
        self.cool_c = QDoubleSpinBox()
        self.cool_c.setRange(0.0, 150.0)
        self.hot_c = QDoubleSpinBox()
        self.hot_c.setRange(0.0, 150.0)
        self.poll_ms = QSpinBox()
        self.poll_ms.setRange(50, 60000)
        self.min_delta_c = QDoubleSpinBox()
        self.min_delta_c.setRange(0.0, 50.0)
        self.force_refresh_s = QSpinBox()
        self.force_refresh_s.setRange(1, 3600)
        self.thermal_brightness = QSpinBox()
        self.thermal_brightness.setRange(0, lighting.RING_BRIGHTNESS_MAX)
        for control in (self.cool_c, self.hot_c, self.poll_ms,
                        self.min_delta_c, self.force_refresh_s,
                        self.thermal_brightness):
            control.valueChanged.connect(self._edited)

        poller_form = QFormLayout()
        poller_form.addRow("Green at (°C)", self.cool_c)
        poller_form.addRow("Red at (°C)", self.hot_c)
        poller_form.addRow("Poll interval (ms)", self.poll_ms)
        poller_form.addRow("Ignore changes below (°C)", self.min_delta_c)
        poller_form.addRow("Force a refresh every (s)", self.force_refresh_s)
        poller_form.addRow("Ring brightness while sweeping",
                           self.thermal_brightness)
        poller_group = QGroupBox("Thermal poller")
        poller_group.setLayout(poller_form)
        poller_note = QLabel(
            "Written to /var/lib/lianli-panel/thermal-rgb.json on Apply. The "
            "poller re-reads it when the file's mtime changes, so edits take "
            "effect within one poll — no unit restart.")
        poller_note.setWordWrap(True)
        poller_form.addRow(poller_note)

        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color:#ffd9d9; background:#5a1d1d;")

        root = QVBoxLayout(self)
        root.addWidget(ring_group)
        root.addWidget(screen_group)
        root.addWidget(poller_group)
        root.addWidget(self.problems)
        root.addStretch(1)

        self.set_state(lighting.LightingState())
        self.set_problems([])

    # --- state -------------------------------------------------------------

    def state(self) -> lighting.LightingState:
        return lighting.LightingState(
            mode=self._mode(),
            color=tuple(self.color.rgba()[:3]),
            ring_brightness=self.ring_brightness.value(),
            screen_brightness=(self.screen_brightness.value()
                               if self.screen_set.isChecked() else None),
            thermal=ThermalConfig(
                cool_c=self.cool_c.value(), hot_c=self.hot_c.value(),
                poll_ms=self.poll_ms.value(),
                min_delta_c=self.min_delta_c.value(),
                force_refresh_s=self.force_refresh_s.value(),
                brightness=self.thermal_brightness.value()),
        )

    def set_state(self, s: lighting.LightingState) -> None:
        """Populates without reporting an edit. Called on load and on revert;
        emitting here would make the draft dirty the moment the app opens."""
        self._loading = True
        try:
            {"off": self.off, "static": self.static,
             "thermal": self.thermal}.get(s.mode, self.thermal).setChecked(True)
            self.color.set_rgba(list(s.color) + [255])
            self.ring_brightness.setValue(s.ring_brightness)
            self.screen_set.setChecked(s.screen_brightness is not None)
            self.screen_brightness.setValue(s.screen_brightness or 0)
            self.screen_value.setValue(s.screen_brightness or 0)
            self.cool_c.setValue(s.thermal.cool_c)
            self.hot_c.setValue(s.thermal.hot_c)
            self.poll_ms.setValue(s.thermal.poll_ms)
            self.min_delta_c.setValue(s.thermal.min_delta_c)
            self.force_refresh_s.setValue(s.thermal.force_refresh_s)
            self.thermal_brightness.setValue(s.thermal.brightness)
        finally:
            self._loading = False
        self._refresh_mode_dependent()

    def set_poller_running(self, running: bool) -> None:
        self._poller_running = bool(running)
        self.poller_status.setText(
            "lianli-thermal-rgb.service: ACTIVE — it is driving the ring now"
            if running else
            "lianli-thermal-rgb.service: not running")
        self._refresh_mode_dependent()

    def set_last_set(self, state: RingState) -> None:
        if state.set_at is None:
            self.last_set.setText(
                "This app has never set the ring. " + RING_READBACK_NOTE)
            return
        self.last_set.setText(
            f"Last set by this app: {state.mode} "
            f"{tuple(state.color)} at brightness {state.brightness}, "
            f"{state.set_at}. " + RING_READBACK_NOTE)

    def set_problems(self, problems: list) -> None:
        self.problems.setText("\n".join(
            f"{p.level}: {p.field} — {p.message}" for p in problems))
        self.problems.setVisible(bool(problems))

    # --- internals ---------------------------------------------------------

    def _mode(self) -> str:
        if self.off.isChecked():
            return "off"
        if self.static.isChecked():
            return "static"
        return "thermal"

    def _refresh_mode_dependent(self) -> None:
        mode = self._mode()
        self.test_button.setEnabled(mode in ("static", "off"))
        self.interlock.setText(
            lighting.interlock_text(mode, self._poller_running))
        self.interlock.setVisible(bool(self.interlock.text()))

    def _mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._refresh_mode_dependent()
        self._edited()

    def _screen_toggled(self, _checked: bool) -> None:
        self._edited()

    def _screen_moved(self, value: int) -> None:
        if self.screen_value.value() != value:
            self.screen_value.setValue(value)
        self._edited()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        self.changed.emit()
