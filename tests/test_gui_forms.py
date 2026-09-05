"""What the inspector shows for a widget.

gaming-dash uses 7 of 12 kinds and 5 of 14 source types, so the existing
template cannot be the source of truth for these forms -- schema.py, extracted
from the daemon by serde probing, is.
"""
import pytest

from lianli_panel.gui import forms
from lianli_panel.model import Widget
from lianli_panel.schema import WIDGET_KINDS


def _widget(kind):
    return Widget(id="w", x=0.0, y=0.0, width=10.0, height=10.0, kind=kind)


LABEL = {"type": "label", "text": "hi", "font_size": 20.0,
         "color": [255, 255, 255, 255]}
GAUGE = {"type": "radial_gauge", "source": {"type": "cpu_usage"},
         "value_min": 20.0, "value_max": 100.0, "start_angle": 135.0,
         "sweep_angle": 270.0, "background_color": [0, 0, 0, 255],
         "unit": "C",
         "ranges": [{"max": 60.0, "color": [0, 255, 0], "alpha": 255},
                    {"max": None, "color": [255, 0, 0], "alpha": 255}]}


def test_every_schema_kind_yields_its_required_fields():
    for name, spec in WIDGET_KINDS.items():
        w = _widget({"type": name})
        shown = {f.name for f in forms.kind_fields(w)}
        missing = [r for r in spec.required
                   if r not in ("source", "ranges") and r not in shown]
        assert missing == [], f"{name} is missing {missing}"


def test_required_fields_are_marked_required():
    fields = {f.name: f for f in forms.kind_fields(_widget(LABEL))}
    assert fields["text"].required is True
    assert fields["align"].required is False


def test_a_field_the_schema_never_saw_is_still_editable():
    """observed_optional is not exhaustive -- the daemon silently ignores
    unknown keys, so there is no way to enumerate them. A key present on the
    widget must appear in the form or it becomes uneditable dead weight."""
    w = _widget({**LABEL, "shadow_blur": 3.0})
    field = next(f for f in forms.kind_fields(w) if f.name == "shadow_blur")
    assert field.value == 3.0
    assert "not in the extracted schema" in field.note


def test_color_fields_are_detected_from_the_value_shape():
    fields = {f.name: f for f in forms.kind_fields(_widget(LABEL))}
    assert fields["color"].kind == "color"
    assert fields["font_size"].kind == "number"
    assert fields["text"].kind == "text"


def test_font_fields_are_detected_by_name():
    w = _widget({**LABEL, "font": {"path": "/usr/share/fonts/x.ttf"}})
    fields = {f.name: f for f in forms.kind_fields(w)}
    assert fields["font"].kind == "font"


def test_an_unknown_kind_falls_back_to_raw_json():
    """A daemon upgrade that adds a widget kind must degrade to reduced
    functionality, never to data loss."""
    w = _widget({"type": "hologram", "spin": 3})
    assert forms.is_unknown_kind(w) is True
    assert forms.is_unknown_kind(_widget(LABEL)) is False


def test_source_fields_come_from_the_source_schema():
    w = _widget({**GAUGE, "source": {"type": "hwmon", "name": "k10temp",
                                     "label": "Tctl"}})
    fields = {f.name: f for f in forms.source_fields(w)}
    assert fields["name"].required is True
    assert fields["label"].value == "Tctl"


def test_change_kind_reports_what_it_dropped():
    w = _widget(dict(GAUGE))
    change = forms.change_kind(w, "label")
    assert w.kind["type"] == "label"
    assert "start_angle" in change.dropped
    assert change.dropped["start_angle"] == 135.0


def test_change_kind_defaults_the_new_required_fields():
    w = _widget(dict(LABEL))
    change = forms.change_kind(w, "vertical_bar")
    assert set(("source", "value_min", "value_max", "background_color")) <= set(w.kind)
    assert "value_max" in change.added


def test_change_kind_carries_fields_the_new_variant_also_has():
    w = _widget(dict(LABEL))
    forms.change_kind(w, "value_text")
    assert w.kind["color"] == [255, 255, 255, 255]
    assert w.kind["font_size"] == 20.0


def test_change_source_reports_drops_and_keeps_the_rest_of_the_widget():
    w = _widget({**GAUGE, "source": {"type": "command", "cmd": "/x/fps.sh"}})
    change = forms.change_source(w, "cpu_usage")
    assert w.kind["source"] == {"type": "cpu_usage"}
    assert change.dropped == {"cmd": "/x/fps.sh"}
    assert w.kind["value_min"] == 20.0


def test_range_rows_are_in_real_units():
    """60 on a 20..100 span is 68 degrees. This conversion failing SILENTLY --
    plausible colours, no error anywhere -- is why the UI never shows a raw
    percentage."""
    rows = forms.range_rows(_widget(GAUGE))
    assert rows[0].threshold == pytest.approx(68.0)
    assert rows[0].unit == "C"
    assert rows[1].threshold is None


def test_set_threshold_writes_a_percentage_back():
    w = _widget({**GAUGE, "ranges": [dict(GAUGE["ranges"][0]),
                                     dict(GAUGE["ranges"][1])]})
    assert forms.set_threshold(w, 0, 84.0) is True
    assert w.kind["ranges"][0]["max"] == pytest.approx(80.0)


def test_set_threshold_ignores_an_unchanged_value():
    """Re-encoding a percentage the user never touched drifts the stored float
    on every save and breaks the lossless round trip."""
    w = _widget({**GAUGE, "ranges": [dict(GAUGE["ranges"][0]),
                                     dict(GAUGE["ranges"][1])]})
    assert forms.set_threshold(w, 0, 68.0) is False
    assert w.kind["ranges"][0]["max"] == 60.0


def test_add_and_remove_range():
    w = _widget({**GAUGE, "ranges": [dict(GAUGE["ranges"][0]),
                                     dict(GAUGE["ranges"][1])]})
    i = forms.add_range(w, 92.0)
    assert i == 1                       # inserted BEFORE the catch-all
    assert w.kind["ranges"][-1]["max"] is None
    forms.remove_range(w, 1)
    assert len(w.kind["ranges"]) == 2


def test_forms_does_not_import_qt():
    import lianli_panel.gui.forms as mod
    assert "PySide6" not in open(mod.__file__).read()
