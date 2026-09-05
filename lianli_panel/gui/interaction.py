"""Canvas interaction, with no Qt.

Everything here is in MODEL units (the 1920x480 authoring space). The view
converts screen pixels before calling, which is also why the grab tolerance is
passed in rather than assumed: 6 screen pixels is 12 model units at scale 0.5.

Repeated presses in the same spot cycle DOWNWARD through overlapping widgets.
That is not a nicety -- cover bars sit exactly on top of what they hide, so
without cycling the hidden widget can never be selected at all.
"""
from __future__ import annotations

from . import geometry as geo

CYCLE_TOL = 3.0          # model units; a press this close counts as "same spot"


class CanvasController:
    def __init__(self, min_size: float = geo.MIN_SIZE,
                 handle_tol: float = geo.HANDLE_TOL) -> None:
        self.min_size = min_size
        self.handle_tol = handle_tol
        self.rects: list[tuple[str, geo.Rect]] = []
        self.selection: str | None = None
        self.dragging = False
        self._handle: str | None = None
        self._origin: tuple[float, float] | None = None
        self._start: geo.Rect | None = None
        self._live: geo.Rect | None = None
        self._last_press: tuple[float, float] | None = None

    def set_widgets(self, rects: list[tuple[str, geo.Rect]]) -> None:
        self.rects = list(rects)
        if self.selection is not None and \
                not any(wid == self.selection for wid, _ in self.rects):
            self.selection = None

    def rect(self, wid: str | None) -> geo.Rect | None:
        return next((r for w, r in self.rects if w == wid), None)

    def _same_spot(self, x: float, y: float) -> bool:
        if self._last_press is None:
            return False
        px, py = self._last_press
        return abs(x - px) <= CYCLE_TOL and abs(y - py) <= CYCLE_TOL

    def press(self, x: float, y: float) -> str | None:
        handle = None
        selected = self.rect(self.selection)
        if selected is not None:
            handle = geo.handle_at(selected, x, y, self.handle_tol)
        if handle is None:
            after = self.selection if self._same_spot(x, y) else None
            self.selection = geo.hit_test(self.rects, x, y, after=after)
        self._handle = handle
        self._origin = (x, y)
        self._last_press = (x, y)
        self._start = self.rect(self.selection)
        self._live = self._start
        self.dragging = self._start is not None
        return self.selection

    def move(self, x: float, y: float) -> tuple[str, geo.Rect] | None:
        if not self.dragging or self._start is None or self._origin is None:
            return None
        dx, dy = x - self._origin[0], y - self._origin[1]
        if self._handle:
            self._live = geo.resize(self._start, self._handle, dx, dy,
                                    self.min_size)
        else:
            self._live = geo.nudge(self._start, dx, dy)
        return (self.selection, self._live)   # type: ignore[return-value]

    def release(self) -> tuple[str, geo.Rect] | None:
        if not self.dragging:
            return None
        self.dragging = False
        self._handle = None
        out = (self.selection, self._live) if self._live is not None else None
        return out                            # type: ignore[return-value]

    def nudge(self, dx: float, dy: float) -> tuple[str, geo.Rect] | None:
        current = self.rect(self.selection)
        if self.selection is None or current is None:
            return None
        return (self.selection, geo.nudge(current, dx, dy))
