import json
from pathlib import Path

from lianli_panel.model import Template, Widget

REAL = Path(__file__).parent / "fixtures" / "gaming-dash.json"


def test_roundtrip_preserves_real_template_exactly():
    original = json.loads(REAL.read_text())
    assert Template.from_json(original).to_json() == original


def test_unknown_fields_survive_at_every_level():
    src = {
        "id": "t", "name": "T", "base_width": 1920, "base_height": 480,
        "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "zz_top": "keep me",
        "widgets": [{
            "id": "w", "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0,
            "zz_widget": "keep me too",
            "kind": {"type": "value_text", "zz_kind": "and me",
                     "source": {"type": "constant", "value": 1.0, "zz_src": "me as well"}},
        }],
    }
    assert Template.from_json(src).to_json() == src


def test_widget_lookup_and_kind_type():
    src = {
        "id": "t", "name": "T", "base_width": 1920, "base_height": 480,
        "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "widgets": [{"id": "w1", "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0,
                     "kind": {"type": "radial_gauge"}}],
    }
    tpl = Template.from_json(src)
    assert tpl.widget("w1").kind_type == "radial_gauge"
    assert tpl.widget("absent") is None


def test_widget_order_is_draw_order_and_is_preserved():
    src = {
        "id": "t", "name": "T", "base_width": 1920, "base_height": 480,
        "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "widgets": [
            {"id": f"w{i}", "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0,
             "kind": {"type": "label"}} for i in range(5)
        ],
    }
    tpl = Template.from_json(src)
    assert [w.id for w in tpl.widgets] == ["w0", "w1", "w2", "w3", "w4"]
    assert [w["id"] for w in tpl.to_json()["widgets"]] == ["w0", "w1", "w2", "w3", "w4"]
