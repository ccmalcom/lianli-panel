"""The widget list.

Its job is DRAW ORDER, which is array order and is load-bearing: only the last
widget covering a rect is visible, which is the whole cover-bar trick. The list
shows the order top-to-bottom as drawn, flags cover bars, and surfaces the
warnings from draft.cover_warnings when a reorder breaks one.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QInputDialog, QLabel, QListWidget,
                               QPushButton, QRadioButton, QVBoxLayout, QWidget)

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


class TemplateList(QWidget):
    """The library.

    'Live' is not a property of a template -- it is the template_id field on
    the LCD's entry in config.lcds, so the radio button here is re-pointing
    that field on the next apply, not flipping a flag on the template.
    """
    chosen = Signal(str)
    made_live = Signal(str)
    created = Signal()
    duplicated = Signal(str)
    renamed = Signal(str, str)
    deleted = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._chosen)
        self.live_button = QRadioButton("live on the panel")
        self.live_button.clicked.connect(self._make_live)

        new, dup = QPushButton("new"), QPushButton("duplicate")
        ren, rm = QPushButton("rename"), QPushButton("delete")
        new.clicked.connect(self.created.emit)
        dup.clicked.connect(lambda: self._with_current(self.duplicated.emit))
        ren.clicked.connect(self._rename)
        rm.clicked.connect(lambda: self._with_current(self.deleted.emit))

        buttons = QHBoxLayout()
        for b in (new, dup, ren, rm):
            buttons.addWidget(b)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("templates"))
        root.addWidget(self.list)
        root.addWidget(self.live_button)
        root.addLayout(buttons)

    def set_draft(self, draft) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for t in draft.templates:
            mark = " ●" if t.id == draft.live_id else ""
            self.list.addItem(f"{t.id}{mark}")
        ids = [t.id for t in draft.templates]
        if draft.current_id in ids:
            self.list.setCurrentRow(ids.index(draft.current_id))
        self.list.blockSignals(False)
        self.live_button.setChecked(draft.current_id == draft.live_id)

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.text().removesuffix(" ●") if item else None

    def _chosen(self, text: str) -> None:
        if text:
            self.chosen.emit(text.removesuffix(" ●"))

    def _with_current(self, fn) -> None:
        tid = self.current_id()
        if tid:
            fn(tid)

    def _make_live(self) -> None:
        self._with_current(self.made_live.emit)

    def _rename(self) -> None:
        tid = self.current_id()
        if not tid:
            return
        name, ok = QInputDialog.getText(self, "Rename template", "New name:")
        if ok and name:
            self.renamed.emit(tid, name)
