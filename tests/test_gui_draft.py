"""The draft is the only thing between an edit and the daemon.

Two properties matter more than the rest: it holds the WHOLE template set
(because SetLcdTemplates replaces the whole set), and it does not lose a byte
of any field this app does not understand.
"""
import copy

import pytest

from lianli_panel.gui.draft import Draft, cover_warnings
from lianli_panel.model import Template


def _tpl(tid, widgets=None):
    return {"id": tid, "name": tid, "base_width": 1920, "base_height": 480,
            "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
            "widgets": widgets or []}


def _w(wid, x=100.0, y=100.0, w=50.0, h=50.0, kind=None):
    return {"id": wid, "x": x, "y": y, "width": w, "height": h,
            "kind": kind or {"type": "label", "text": wid, "font_size": 20.0,
                             "color": [255, 255, 255, 255]}}


def test_draft_starts_clean_with_a_base_hash():
    d = Draft([_tpl("a")], live_id="a")
    assert d.dirty is False
    assert len(d.base_hash) == 64
    assert d.current_id == "a"


def test_set_geometry_marks_the_draft_dirty():
    d = Draft([_tpl("a", [_w("one")])], live_id="a")
    d.set_geometry("one", 200.0, 300.0, 60.0, 70.0)
    assert d.dirty is True
    assert d.widget("one").x == 200.0
    assert d.widget("one").height == 70.0


def test_payload_round_trips_unknown_fields(dash_json):
    """The daemon silently ignores unknown keys, so a key dropped here is
    deleted forever with no error anywhere."""
    d = Draft([dash_json], live_id="gaming-dash")
    assert d.payload() == [dash_json]


def test_editing_one_widget_leaves_every_other_byte_identical(dash_json):
    before = copy.deepcopy(dash_json)
    d = Draft([dash_json], live_id="gaming-dash")
    target = before["widgets"][3]["id"]
    d.set_geometry(target, 1.0, 2.0, 3.0, 4.0)
    after = d.payload()[0]
    for i, w in enumerate(after["widgets"]):
        if w["id"] == target:
            continue
        assert w == before["widgets"][i]


def test_delete_widget_preserves_draw_order():
    d = Draft([_tpl("a", [_w("one"), _w("two"), _w("three")])], live_id="a")
    d.delete_widget("two")
    assert [w.id for w in d.current().widgets] == ["one", "three"]


def test_reorder_widget_moves_it_in_draw_order():
    d = Draft([_tpl("a", [_w("one"), _w("two"), _w("three")])], live_id="a")
    d.reorder_widget("one", +1)
    assert [w.id for w in d.current().widgets] == ["two", "one", "three"]
    d.reorder_widget("one", -1)
    assert [w.id for w in d.current().widgets] == ["one", "two", "three"]
    d.reorder_widget("one", -1)          # already first: a no-op, not an error
    assert [w.id for w in d.current().widgets] == ["one", "two", "three"]


def test_duplicate_widget_gets_a_unique_id_and_lands_on_top():
    d = Draft([_tpl("a", [_w("one")])], live_id="a")
    new_id = d.duplicate_widget("one")
    assert new_id != "one"
    assert [w.id for w in d.current().widgets] == ["one", new_id]


def test_duplicate_template_gets_a_unique_id():
    d = Draft([_tpl("a")], live_id="a")
    new_id = d.duplicate_template("a")
    assert new_id != "a"
    assert {t.id for t in d.templates} == {"a", new_id}
    assert d.live_id == "a"              # duplicating never changes what is live


def test_delete_live_template_repoints_live_id():
    """Allowed, but the same apply must re-point config.lcds.template_id --
    leaving it pointing at a deleted template is how the panel goes blank."""
    d = Draft([_tpl("a"), _tpl("b")], live_id="a")
    d.delete_template("a")
    assert d.live_id == "b"


def test_delete_the_last_template_refuses():
    d = Draft([_tpl("a")], live_id="a")
    with pytest.raises(ValueError, match="last template"):
        d.delete_template("a")


def test_undo_restores_the_previous_geometry():
    d = Draft([_tpl("a", [_w("one", x=10.0)])], live_id="a")
    d.set_geometry("one", 99.0, 10.0, 50.0, 50.0)
    assert d.undo() is True
    assert d.widget("one").x == 10.0
    assert d.redo() is True
    assert d.widget("one").x == 99.0


def test_a_drag_coalesces_into_one_undo_step():
    """checkpoint=False on the intermediate moves. Without this, one drag
    leaves 40 undo entries and ctrl-Z becomes useless."""
    d = Draft([_tpl("a", [_w("one", x=10.0)])], live_id="a")
    d.checkpoint()
    for x in (20.0, 30.0, 40.0):
        d.set_geometry("one", x, 10.0, 50.0, 50.0, checkpoint=False)
    assert d.undo() is True
    assert d.widget("one").x == 10.0


def test_mark_applied_clears_dirty_and_rebases_the_hash():
    d = Draft([_tpl("a")], live_id="a")
    d.rename_template("a", "renamed")
    assert d.dirty is True
    d.mark_applied(d.payload())
    assert d.dirty is False
    assert d.base_hash != ""


def test_rename_template_keeps_the_id():
    d = Draft([_tpl("a")], live_id="a")
    d.rename_template("a", "Gaming Dash")
    assert d.templates[0].id == "a"
    assert d.templates[0].name == "Gaming Dash"


def test_cover_warning_when_a_covered_widget_is_drawn_after_the_cover():
    cover = _w("cover", kind={"type": "horizontal_bar", "value_max": 1,
                              "value_min": 0, "source": {"type": "constant", "value": 1},
                              "background_color": [0, 0, 0, 255]})
    hidden = _w("hidden")
    t = Template.from_json(_tpl("a", [cover, hidden]))
    warnings = cover_warnings(t)
    assert any("hidden" in w and "cover" in w for w in warnings)


def test_no_cover_warning_for_a_self_gating_widget():
    """The documented stack is [needs covering] -> [cover] -> [self-gating].
    A widget with an alpha-0 range hides itself and is SUPPOSED to sit last."""
    cover = _w("cover", kind={"type": "horizontal_bar", "value_max": 1,
                              "value_min": 0, "source": {"type": "constant", "value": 1},
                              "background_color": [0, 0, 0, 255]})
    gated = _w("gated", kind={"type": "value_text", "font_size": 20.0,
                              "color": [255, 255, 255, 255],
                              "source": {"type": "constant", "value": 0},
                              "value_min": 0, "value_max": 100,
                              "ranges": [{"max": 10, "color": [0, 0, 0], "alpha": 0},
                                         {"max": None, "color": [255, 255, 255], "alpha": 255}]})
    t = Template.from_json(_tpl("a", [cover, gated]))
    assert cover_warnings(t) == []


def test_cover_warning_when_two_widgets_share_one_cover():
    """Only one widget per rect can be conditionally hidden -- the second is
    hidden unconditionally, which reads as 'my widget vanished'."""
    a = _w("a")
    b = _w("b")
    cover = _w("cover", kind={"type": "horizontal_bar", "value_max": 1,
                              "value_min": 0, "source": {"type": "constant", "value": 1},
                              "background_color": [0, 0, 0, 255]})
    t = Template.from_json(_tpl("t", [a, b, cover]))
    assert any("only one" in w for w in cover_warnings(t))


def test_draft_does_not_import_qt():
    """The whole point of this module is being testable without a display.
    An accidental PySide6 import here moves it out of Codex's reach."""
    import lianli_panel.gui.draft as mod
    src = open(mod.__file__).read()
    assert "PySide6" not in src
