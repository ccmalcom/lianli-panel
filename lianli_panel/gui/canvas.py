"""The canvas.

WHAT YOU SEE IS THE DAEMON'S OWN RENDER. This widget draws the JPEG from
RenderTemplatePreview and overlays selection only -- it does not reimplement a
single one of the 12 widget kinds. That is the architectural bet of the whole
app: no second renderer, so nothing to drift from what the panel shows.

During a drag the selection rectangle moves live over a frame that may be up to
~0.3s stale. That is the accepted cost.

Every widget's outline is drawn faintly, always. Cover bars and self-gating
widgets are invisible in the render by design, and an outline is the only way
to see that something is there to click.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import geometry as geo
from .interaction import CanvasController

HANDLE_PX = 7.0
NUDGE = 1.0
NUDGE_FAST = 10.0


class Canvas(QWidget):
    selection_changed = Signal(str)
    edit_started = Signal()
    geometry_changed = Signal(str, float, float, float, float)
    edit_finished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 160)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(False)
        self.controller = CanvasController()
        self._image: QImage | None = None
        self._view = geo.fit(float(self.width() or 1), float(self.height() or 1))

    # --- inputs ------------------------------------------------------------

    def set_frame(self, jpeg: bytes) -> None:
        image = QImage.fromData(jpeg, "JPEG")
        if not image.isNull():
            self._image = image
            self.update()

    def set_widgets(self, rects: list[tuple[str, geo.Rect]]) -> None:
        self.controller.set_widgets(rects)
        self.update()

    def set_selection(self, wid: str | None) -> None:
        self.controller.selection = wid
        self.update()

    # --- painting ----------------------------------------------------------

    def _refresh_view(self) -> None:
        self._view = geo.fit(float(self.width()), float(self.height()))

    def paintEvent(self, event) -> None:
        self._refresh_view()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        frame = self._view.to_view(geo.Rect(0, 0, geo.BASE_W, geo.BASE_H))
        if self._image is not None:
            p.drawImage(
                int(frame.left),
                int(frame.top),
                self._image.scaled(
                    int(frame.width),
                    int(frame.height),
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                ),
            )
        outline = QPen(QColor(120, 160, 220, 70))
        for wid, r in self.controller.rects:
            v = self._view.to_view(r)
            p.setPen(
                QPen(QColor(230, 140, 60, 140)) if geo.offscreen(r)
                else outline
            )
            p.drawRect(int(v.left), int(v.top), int(v.width), int(v.height))
        selected = self.controller.rect(self.controller.selection)
        if selected is not None:
            v = self._view.to_view(selected)
            p.setPen(QPen(QColor(90, 170, 255), 2))
            p.drawRect(int(v.left), int(v.top), int(v.width), int(v.height))
            p.setBrush(QColor(90, 170, 255))
            for hx, hy in self._handle_points(v):
                p.drawRect(
                    int(hx - HANDLE_PX / 2),
                    int(hy - HANDLE_PX / 2),
                    int(HANDLE_PX),
                    int(HANDLE_PX),
                )
        p.end()

    @staticmethod
    def _handle_points(v: geo.Rect) -> list[tuple[float, float]]:
        mx, my = v.left + v.width / 2, v.top + v.height / 2
        return [
            (v.left, v.top),
            (mx, v.top),
            (v.right, v.top),
            (v.right, my),
            (v.right, v.bottom),
            (mx, v.bottom),
            (v.left, v.bottom),
            (v.left, my),
        ]

    # --- interaction, in model units --------------------------------------

    def press_model(self, x: float, y: float) -> None:
        before = self.controller.selection
        selection = self.controller.press(x, y)
        if selection != before:
            self.selection_changed.emit(selection or "")
        if self.controller.dragging:
            self.edit_started.emit()
        self.update()

    def move_model(self, x: float, y: float) -> None:
        moved = self.controller.move(x, y)
        if moved is not None:
            self._emit(*moved)

    def release_model(self) -> None:
        final = self.controller.release()
        if final is not None:
            self._emit(*final)
            self.edit_finished.emit()
        self.update()

    def _emit(self, wid: str, rect: geo.Rect) -> None:
        x, y, w, h = geo.to_centre(rect)  # the model stores CENTRES
        self.geometry_changed.emit(wid, x, y, w, h)
        self.update()

    # --- Qt event adapters, three lines each ------------------------------

    def _model_point(self, event) -> tuple[float, float]:
        self._refresh_view()
        pos = event.position()
        return self._view.to_model_point(pos.x(), pos.y())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.press_model(*self._model_point(event))

    def mouseMoveEvent(self, event) -> None:
        self.move_model(*self._model_point(event))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self.release_model()

    def keyPressEvent(self, event) -> None:
        step = (
            NUDGE_FAST
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier
            else NUDGE
        )
        deltas = {
            Qt.Key.Key_Left: (-step, 0.0),
            Qt.Key.Key_Right: (step, 0.0),
            Qt.Key.Key_Up: (0.0, -step),
            Qt.Key.Key_Down: (0.0, step),
        }
        delta = deltas.get(event.key())
        if delta is None:
            super().keyPressEvent(event)
            return
        moved = self.controller.nudge(*delta)
        if moved is not None:
            self.edit_started.emit()
            self._emit(*moved)
            self.edit_finished.emit()
