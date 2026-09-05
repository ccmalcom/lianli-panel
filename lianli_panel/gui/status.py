"""Banners, and the two background checks that raise them.

Both conditions here are invisible to the rest of the app because neither
appears in a return value:

  * the panel's handle is DEAD while every IPC call still returns ok (the h264
    encoder died on replug and the daemon never reopened the screen)
  * lianli-gui is running, and it wipes config.lcds on every config write

So both are polled. health.check() shells out to journalctl with a 20s timeout,
which is a visible freeze on the UI thread, so the probes run on a QThread --
the same shape as preview.PreviewWorker, for the same reason.

The banner is a STACK, not a label. The single QLabel it replaces showed only
the last message and never cleared: a health warning would erase a render
error, and a condition that resolved stayed on screen until restart. Each
source owns a key, sets it, and clears it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from .. import health

LEVELS = {
    "error": "background:#5a1d1d; color:#ffd9d9;",
    "warn": "background:#54471a; color:#ffeec9;",
    "info": "background:#1d3a5a; color:#d9e9ff;",
}


class BannerStack(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: dict[str, QWidget] = {}
        self._texts: dict[str, str] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.hide()

    def show_banner(self, key: str, text: str, level: str = "error",
                    action: tuple[str, object] | None = None) -> None:
        """`action` is (caption, callable) -- a button in the banner itself,
        because a warning the user cannot act on from where they read it is a
        warning they learn to ignore."""
        self.clear(key)
        row = QWidget()
        row.setStyleSheet(LEVELS.get(level, LEVELS["error"]))
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box = QHBoxLayout(row)
        box.setContentsMargins(8, 4, 8, 4)
        box.addWidget(label, 1)
        if action is not None:
            caption, handler = action
            button = QPushButton(caption)
            button.clicked.connect(handler)
            box.addWidget(button)
        self._layout.addWidget(row)
        self._rows[key] = row
        self._texts[key] = text
        self.show()

    def clear(self, key: str) -> None:
        row = self._rows.pop(key, None)
        self._texts.pop(key, None)
        if row is not None:
            self._layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        if not self._rows:
            self.hide()

    def keys(self) -> list[str]:
        return list(self._rows)

    def text(self) -> str:
        """Every banner, newline-joined. Reading the whole stack is what a test
        actually wants, and it keeps Task 5's smoke test working unchanged."""
        return "\n".join(self._texts.values())


class _Probe(QObject):
    done = Signal(object, object)

    def __init__(self, check, scan) -> None:
        super().__init__()
        self._check, self._scan = check, scan

    @Slot()
    def run(self) -> None:
        try:
            report = self._check()
        except Exception as exc:
            # A failed CHECK is not a healthy panel. Saying so is the whole
            # point -- silently reporting ok here would hide the one failure
            # this module exists to surface.
            report = health.PanelHealth(
                False, f"the panel health check itself failed: {exc}")
        try:
            pids = self._scan()
        except Exception:
            pids = []                    # a scan failure is not a vendor GUI
        self.done.emit(report, pids)


class HealthPoller(QObject):
    reported = Signal(object, object)    # PanelHealth, list[int]
    _submit = Signal()

    def __init__(self, parent=None, *, check=health.check,
                 scan=health.vendor_gui_pids, interval_ms: int = 60000) -> None:
        super().__init__(parent)
        self._busy = False
        self._thread = QThread()
        self._probe = _Probe(check, scan)
        self._probe.moveToThread(self._thread)
        self._submit.connect(self._probe.run)
        self._probe.done.connect(self._on_done)
        self._thread.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        if interval_ms > 0:
            self._timer.start(interval_ms)

    def poll(self) -> None:
        """Dropped, not queued, while one is in flight: a journal read of a
        long-lived boot is not instant, and queued polls would arrive as a
        burst of stale reports."""
        if self._busy:
            return
        self._busy = True
        self._submit.emit()

    def _on_done(self, report, pids) -> None:
        self._busy = False
        self.reported.emit(report, pids)

    def stop(self) -> None:
        self._timer.stop()
        self._thread.quit()
        self._thread.wait(2000)
