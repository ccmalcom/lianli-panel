"""The widget list.

Its job is DRAW ORDER, which is array order and is load-bearing: only the last
widget covering a rect is visible, which is the whole cover-bar trick. The list
shows the order top-to-bottom as drawn, flags cover bars, and surfaces the
warnings from draft.cover_warnings when a reorder breaks one.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QVBoxLayout, QWidget)

from .draft import cover_warnings


class WidgetList(QWidget):
    selected = Signal(str)
    reordered = Signal(str, int)
    deleted = Signal(str)
    duplicated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(
            lambda text: self.selected.emit(text.split("  ")[0]))
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet("color:#c9a227;")

        up, down = QPushButton("▲"), QPushButton("▼")
        dup, rm = QPushButton("duplicate"), QPushButton("delete")
        up.clicked.connect(lambda: self._emit_move(-1))
        down.clicked.connect(lambda: self._emit_move(+1))
        dup.clicked.connect(lambda: self._emit(self.duplicated))
        rm.clicked.connect(lambda: self._emit(self.deleted))

        buttons = QHBoxLayout()
        for b in (up, down, dup, rm):
            buttons.addWidget(b)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("widgets (drawn top to bottom)"))
        root.addWidget(self.list)
        root.addLayout(buttons)
        root.addWidget(self.warnings)

    def set_draft(self, draft) -> None:
        template = draft.current()
        self.list.clear()
        if template is None:
            return
        for w in template.widgets:
            mark = "  [cover]" if (w.kind_type == "horizontal_bar"
                                   and w.kind.get("value_max") == 1) else ""
            self.list.addItem(f"{w.id}{mark}")
        self.warnings.setText("\n".join(cover_warnings(template)))

    def _current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.text().split("  ")[0] if item else None

    def _emit(self, signal) -> None:
        wid = self._current_id()
        if wid:
            signal.emit(wid)

    def _emit_move(self, delta: int) -> None:
        wid = self._current_id()
        if wid:
            self.reordered.emit(wid, delta)
