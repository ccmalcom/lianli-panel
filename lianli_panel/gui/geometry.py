"""Canvas geometry.

WIDGET x/y ARE THE CENTRE, not the top-left: the daemon computes
left = scaled_x - rendered_width / 2. Every centre<->corner conversion in the
app happens here so there is exactly one place to get it wrong.

Templates are authored at 1920x480 landscape; the panel is physically 480x1920
and the daemon rotates. The canvas is therefore fixed to the landscape aspect
and letterboxes inside whatever space the window gives it.

Tolerances and deltas passed in are MODEL units. The view converts before
calling -- a 6px grab radius on screen is 12 model units at scale 0.5.
"""
from __future__ import annotations

from dataclasses import dataclass

BASE_W, BASE_H = 1920.0, 480.0
MIN_SIZE = 8.0
HANDLE_TOL = 6.0
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


def to_rect(x: float, y: float, width: float, height: float) -> Rect:
    return Rect(x - width / 2.0, y - height / 2.0, width, height)


def to_centre(r: Rect) -> tuple[float, float, float, float]:
    return (r.left + r.width / 2.0, r.top + r.height / 2.0, r.width, r.height)


@dataclass(frozen=True)
class View:
    scale: float
    offset_x: float
    offset_y: float

    def to_view(self, r: Rect) -> Rect:
        return Rect(self.offset_x + r.left * self.scale,
                    self.offset_y + r.top * self.scale,
                    r.width * self.scale, r.height * self.scale)

    def to_model_point(self, vx: float, vy: float) -> tuple[float, float]:
        return ((vx - self.offset_x) / self.scale,
                (vy - self.offset_y) / self.scale)

    def to_model_delta(self, dvx: float, dvy: float) -> tuple[float, float]:
        return (dvx / self.scale, dvy / self.scale)


def fit(view_w: float, view_h: float,
        base_w: float = BASE_W, base_h: float = BASE_H) -> View:
    scale = min(view_w / base_w, view_h / base_h)
    return View(scale,
                (view_w - base_w * scale) / 2.0,
                (view_h - base_h * scale) / 2.0)


def hit_test(rects: list[tuple[str, Rect]], x: float, y: float,
             after: str | None = None) -> str | None:
    """Topmost first. Array order is draw order, so the LAST match is on top.

    `after` walks one step DOWN the stack, which is the only way to reach a
    widget hidden under a cover bar -- the cover occupies the same rect and
    would otherwise swallow every click.
    """
    hits = [wid for wid, r in rects if r.contains(x, y)][::-1]
    if not hits:
        return None
    if after is None or after not in hits:
        return hits[0]
    return hits[(hits.index(after) + 1) % len(hits)]


def handle_at(rect: Rect, x: float, y: float,
              tol: float = HANDLE_TOL) -> str | None:
    if not (rect.left - tol <= x <= rect.right + tol
            and rect.top - tol <= y <= rect.bottom + tol):
        return None
    vertical = "n" if abs(y - rect.top) <= tol else \
               "s" if abs(y - rect.bottom) <= tol else ""
    horizontal = "w" if abs(x - rect.left) <= tol else \
                 "e" if abs(x - rect.right) <= tol else ""
    return (vertical + horizontal) or None


def resize(rect: Rect, handle: str, dx: float, dy: float,
           min_size: float = MIN_SIZE) -> Rect:
    left, top, w, h = rect.left, rect.top, rect.width, rect.height
    if "w" in handle:
        left += dx
        w -= dx
    if "e" in handle:
        w += dx
    if "n" in handle:
        top += dy
        h -= dy
    if "s" in handle:
        h += dy
    # Clamp against the OPPOSITE edge, so dragging a west handle past the east
    # one pins the left edge rather than inverting the rect.
    if w < min_size:
        if "w" in handle:
            left = rect.right - min_size
        w = min_size
    if h < min_size:
        if "n" in handle:
            top = rect.bottom - min_size
        h = min_size
    return Rect(left, top, w, h)


def nudge(rect: Rect, dx: float, dy: float) -> Rect:
    return Rect(rect.left + dx, rect.top + dy, rect.width, rect.height)


def offscreen(rect: Rect, base_w: float = BASE_W, base_h: float = BASE_H) -> bool:
    """Partly-off-canvas widgets are legal; the UI flags them, it does not
    forbid them."""
    return (rect.left < 0 or rect.top < 0
            or rect.right > base_w or rect.bottom > base_h)
