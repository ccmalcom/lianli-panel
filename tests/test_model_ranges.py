import pytest

from lianli_panel.model import (
    Problem, Template, Widget, pct_to_raw, raw_to_pct,
    range_thresholds_raw, set_range_threshold_raw, validate, widget_span,
)


def gauge(ranges, vmin=20.0, vmax=100.0, wid="g"):
    return Widget(id=wid, x=0.0, y=0.0, width=10.0, height=10.0,
                  kind={"type": "radial_gauge",
                        "source": {"type": "constant", "value": 1.0},
                        "value_min": vmin, "value_max": vmax, "ranges": ranges})


def tpl(widgets):
    return Template(id="t", name="T", base_width=1920, base_height=480,
                    rotated=True, background={"type": "color", "rgb": [0, 0, 0, 255]},
                    widgets=widgets)


# --- conversion ------------------------------------------------------------

def test_sixty_percent_of_twenty_to_hundred_is_sixty_eight():
    assert pct_to_raw(60.0, 20.0, 100.0) == pytest.approx(68.0)


def test_raw_to_pct_is_the_inverse():
    assert raw_to_pct(68.0, 20.0, 100.0) == pytest.approx(60.0)


def test_roundtrip_is_stable_across_representative_values():
    for raw in (20.0, 33.3, 68.0, 99.9, 100.0):
        assert pct_to_raw(raw_to_pct(raw, 20.0, 100.0), 20.0, 100.0) == pytest.approx(raw)


def test_degenerate_span_normalises_to_zero():
    assert raw_to_pct(50.0, 40.0, 40.0) == 0.0
    assert pct_to_raw(75.0, 40.0, 40.0) == 40.0


def test_values_outside_the_span_clamp_like_the_daemon():
    # The daemon clamps the unit interval to [0,1] before scaling.
    assert raw_to_pct(5.0, 20.0, 100.0) == 0.0
    assert raw_to_pct(500.0, 20.0, 100.0) == 100.0


# --- reading and writing thresholds ---------------------------------------

def test_thresholds_are_reported_in_real_units():
    w = gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255},
               {"max": None, "color": [1, 1, 1], "alpha": 255}])
    assert range_thresholds_raw(w) == [pytest.approx(60.0), None]


def test_setting_a_threshold_writes_back_a_percentage():
    w = gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255},
               {"max": None, "color": [1, 1, 1], "alpha": 255}])
    set_range_threshold_raw(w, 0, 68.0)
    assert w.kind["ranges"][0]["max"] == pytest.approx(60.0)


def test_untouched_ranges_are_not_rewritten():
    """Float drift on save would break the lossless-round-trip promise."""
    original = {"max": 33.333333333333336, "color": [0, 0, 0], "alpha": 255}
    w = gauge([dict(original), {"max": None, "color": [1, 1, 1], "alpha": 255}])
    set_range_threshold_raw(w, 1, None)
    assert w.kind["ranges"][0] == original


def test_widget_without_a_span_has_no_thresholds():
    w = Widget(id="l", x=0.0, y=0.0, width=1.0, height=1.0,
               kind={"type": "label", "text": "hi"})
    assert widget_span(w) is None
    assert range_thresholds_raw(w) == []


# --- validation ------------------------------------------------------------

def _messages(problems):
    return " | ".join(p.message for p in problems)


def test_reversed_span_is_an_error():
    problems = validate(tpl([gauge([{"max": None, "color": [0, 0, 0], "alpha": 255}],
                                   vmin=100.0, vmax=20.0)]))
    assert any(p.level == "error" for p in problems)
    assert "value_min" in _messages(problems)


def test_unsorted_range_maxima_is_an_error():
    problems = validate(tpl([gauge([{"max": 80.0, "color": [0, 0, 0], "alpha": 255},
                                    {"max": 30.0, "color": [0, 0, 0], "alpha": 255},
                                    {"max": None, "color": [1, 1, 1], "alpha": 255}])]))
    assert any("ascending" in p.message for p in problems)


def test_maximum_outside_zero_to_one_hundred_is_an_error():
    problems = validate(tpl([gauge([{"max": 140.0, "color": [0, 0, 0], "alpha": 255},
                                    {"max": None, "color": [1, 1, 1], "alpha": 255}])]))
    assert any("0..100" in p.message for p in problems)


def test_missing_catch_all_is_a_warning():
    problems = validate(tpl([gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255}])]))
    assert any(p.level == "warning" and "catch-all" in p.message for p in problems)


def test_two_catch_alls_is_an_error():
    problems = validate(tpl([gauge([{"max": None, "color": [0, 0, 0], "alpha": 255},
                                    {"max": None, "color": [1, 1, 1], "alpha": 255}])]))
    assert any(p.level == "error" and "catch-all" in p.message for p in problems)


def test_duplicate_widget_ids_is_an_error():
    problems = validate(tpl([gauge([{"max": None, "color": [0, 0, 0], "alpha": 255}], wid="d"),
                             gauge([{"max": None, "color": [0, 0, 0], "alpha": 255}], wid="d")]))
    assert any("duplicate" in p.message for p in problems)


def test_a_clean_template_reports_nothing():
    assert validate(tpl([gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255},
                                {"max": None, "color": [1, 1, 1], "alpha": 255}])])) == []
