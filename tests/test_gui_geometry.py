"""Canvas geometry. The centre-origin conversion is the reason this module
exists: getting it wrong offsets every widget by half its size, which looks
like a rendering bug rather than a coordinate bug."""
import pytest

from lianli_panel.gui import geometry as geo


def test_centre_origin_round_trip():
    r = geo.to_rect(960.0, 240.0, 200.0, 100.0)
    assert (r.left, r.top) == (860.0, 190.0)
    assert geo.to_centre(r) == (960.0, 240.0, 200.0, 100.0)


def test_rect_edges_and_contains():
    r = geo.Rect(10.0, 20.0, 100.0, 50.0)
    assert (r.right, r.bottom) == (110.0, 70.0)
    assert r.contains(10.0, 20.0) and r.contains(110.0, 70.0)
    assert not r.contains(9.0, 20.0)


def test_fit_letterboxes_to_the_panel_aspect():
    v = geo.fit(1920.0, 960.0)              # twice as tall as the panel
    assert v.scale == 1.0
    assert v.offset_x == 0.0
    assert v.offset_y == 240.0              # centred vertically


def test_fit_uses_the_limiting_axis():
    v = geo.fit(960.0, 960.0)
    assert v.scale == 0.5
    assert v.offset_y == pytest.approx(360.0)


def test_view_maps_a_point_back_to_model_space():
    v = geo.fit(960.0, 960.0)
    assert v.to_model_point(v.offset_x, v.offset_y) == (0.0, 0.0)
    assert v.to_model_delta(10.0, 10.0) == (20.0, 20.0)


def test_hit_test_picks_the_topmost():
    rects = [("under", geo.Rect(0, 0, 100, 100)),
             ("over", geo.Rect(0, 0, 100, 100))]
    assert geo.hit_test(rects, 50, 50) == "over"


def test_hit_test_cycles_down_the_stack():
    """Cover bars sit directly on top of what they hide, so the widget
    underneath is unselectable without this."""
    rects = [("a", geo.Rect(0, 0, 100, 100)),
             ("b", geo.Rect(0, 0, 100, 100)),
             ("c", geo.Rect(0, 0, 100, 100))]
    assert geo.hit_test(rects, 50, 50) == "c"
    assert geo.hit_test(rects, 50, 50, after="c") == "b"
    assert geo.hit_test(rects, 50, 50, after="b") == "a"
    assert geo.hit_test(rects, 50, 50, after="a") == "c"


def test_hit_test_returns_none_off_every_rect():
    rects = [("a", geo.Rect(0, 0, 10, 10))]
    assert geo.hit_test(rects, 50, 50) is None
    assert geo.hit_test(rects, 50, 50, after="a") is None


def test_handle_at_finds_corners_and_edges():
    r = geo.Rect(100, 100, 200, 200)
    assert geo.handle_at(r, 100, 100, tol=6) == "nw"
    assert geo.handle_at(r, 300, 300, tol=6) == "se"
    assert geo.handle_at(r, 200, 100, tol=6) == "n"
    assert geo.handle_at(r, 300, 200, tol=6) == "e"
    assert geo.handle_at(r, 200, 200, tol=6) is None
    assert geo.handle_at(r, 50, 50, tol=6) is None


def test_resize_clamps_to_min_size():
    r = geo.Rect(0, 0, 20, 20)
    out = geo.resize(r, "se", -100, -100, min_size=8.0)
    assert (out.width, out.height) == (8.0, 8.0)


def test_resize_from_the_west_handle_holds_the_right_edge():
    r = geo.Rect(100, 0, 100, 50)
    out = geo.resize(r, "w", 40, 0)
    assert out.left == 140.0
    assert out.right == 200.0


def test_offscreen_flags_widgets_outside_the_panel():
    assert not geo.offscreen(geo.Rect(0, 0, 1920, 480))
    assert geo.offscreen(geo.Rect(-1, 0, 100, 100))
    assert geo.offscreen(geo.Rect(1900, 0, 100, 100))
