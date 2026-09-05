"""Press / move / release, with no Qt anywhere.

This is where clicking, dragging and cycling are decided, so that canvas.py is
only paint plus three event adapters -- and so this logic is testable without a
display.
"""
from lianli_panel.gui.geometry import Rect
from lianli_panel.gui.interaction import CanvasController

A = ("a", Rect(0, 0, 100, 100))
B = ("b", Rect(0, 0, 100, 100))
FAR = ("far", Rect(500, 200, 100, 100))


def _ctl(*rects):
    c = CanvasController()
    c.set_widgets(list(rects))
    return c


def test_press_selects_the_topmost():
    c = _ctl(A, B)
    assert c.press(50, 50) == "b"


def test_repeat_press_cycles_down_the_stack():
    c = _ctl(A, B)
    assert c.press(50, 50) == "b"
    c.release()
    assert c.press(50, 50) == "a"
    c.release()
    assert c.press(50, 50) == "b"


def test_pressing_somewhere_else_restarts_at_the_top():
    c = _ctl(A, B, FAR)
    c.press(50, 50)
    c.release()
    c.press(550, 250)
    c.release()
    assert c.press(50, 50) == "b"


def test_press_on_empty_space_clears_the_selection():
    c = _ctl(A)
    c.press(50, 50)
    c.release()
    assert c.press(900, 400) is None
    assert c.selection is None


def test_drag_moves_the_selected_widget():
    c = _ctl(A)
    c.press(50, 50)
    wid, rect = c.move(70, 90)
    assert wid == "a"
    assert (rect.left, rect.top) == (20, 40)


def test_drag_on_a_handle_resizes_instead_of_moving():
    c = _ctl(A)
    c.press(50, 50)                       # select it first
    c.release()
    c.press(100, 100)                     # the se corner
    wid, rect = c.move(150, 150)
    assert (rect.left, rect.top) == (0, 0)
    assert (rect.width, rect.height) == (150, 150)


def test_resize_respects_the_minimum_size():
    c = _ctl(A)
    c.press(50, 50)
    c.release()
    c.press(100, 100)
    _, rect = c.move(-500, -500)
    assert (rect.width, rect.height) == (8.0, 8.0)


def test_move_without_a_press_does_nothing():
    c = _ctl(A)
    assert c.move(10, 10) is None


def test_release_returns_the_final_rect_once():
    c = _ctl(A)
    c.press(50, 50)
    c.move(60, 60)
    wid, rect = c.release()
    assert wid == "a" and rect.left == 10
    assert c.release() is None
    assert c.dragging is False


def test_release_without_a_drag_returns_none():
    c = _ctl(A)
    assert c.release() is None


def test_nudge_moves_the_selection():
    c = _ctl(A)
    c.press(50, 50)
    c.release()
    wid, rect = c.nudge(10, 0)
    assert wid == "a" and rect.left == 10


def test_nudge_without_a_selection_returns_none():
    assert _ctl(A).nudge(1, 0) is None


def test_interaction_does_not_import_qt():
    import lianli_panel.gui.interaction as mod
    assert "PySide6" not in open(mod.__file__).read()
