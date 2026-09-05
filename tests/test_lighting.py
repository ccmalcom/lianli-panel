"""The Lighting tab's whole decision surface, with no Qt and no daemon.

The rules being pinned here are the ones that produce a visible wrong result
rather than an error: sending SetRgbEffect while the poller owns the ring
(colour flashes then changes back), or setting the ring's brightness when the
user moved the screen's slider.
"""
import pytest

from lianli_panel import lighting
from lianli_panel.ring import ThermalConfig


def base(**kw):
    defaults = dict(mode="thermal", color=(255, 255, 255), ring_brightness=4,
                    screen_brightness=None, thermal=ThermalConfig())
    defaults.update(kw)
    return lighting.LightingState(**defaults)


# --- state -----------------------------------------------------------------


def test_state_round_trips_through_json():
    s = base(mode="static", color=(10, 20, 30), ring_brightness=2,
             screen_brightness=180,
             thermal=ThermalConfig(cool_c=40.0, hot_c=90.0))
    assert lighting.LightingState.from_json(s.to_json()) == s


def test_from_json_ignores_keys_it_does_not_know():
    obj = base().to_json()
    obj["invented_by_a_future_version"] = True
    assert lighting.LightingState.from_json(obj).mode == "thermal"


def test_copy_does_not_share_the_thermal_config():
    s = base()
    other = s.copy()
    other.thermal.hot_c = 99.0
    assert s.thermal.hot_c == 85.0


def test_screen_brightness_none_means_never_set():
    """The daemon omits config.lcds[].brightness while it is null, so None is
    'the panel is at its own default', not 'zero'."""
    assert base().screen_brightness is None


# --- validation ------------------------------------------------------------


def test_an_unknown_mode_is_an_error():
    problems = lighting.problems(base(mode="rainbow"))
    assert any(p.level == "error" and p.field == "mode" for p in problems)


def test_a_colour_component_above_255_is_an_error():
    problems = lighting.problems(base(mode="static", color=(300, 0, 0)))
    assert any(p.level == "error" and p.field == "color" for p in problems)


def test_ring_brightness_above_four_is_an_error():
    problems = lighting.problems(base(ring_brightness=9))
    assert any(p.level == "error" and p.field == "ring_brightness"
               for p in problems)


def test_screen_brightness_above_255_is_an_error():
    problems = lighting.problems(base(screen_brightness=300))
    assert any(p.level == "error" and p.field == "screen_brightness"
               for p in problems)


def test_cool_above_hot_is_an_error_naming_the_sweep():
    problems = lighting.problems(
        base(thermal=ThermalConfig(cool_c=90.0, hot_c=50.0)))
    match = [p for p in problems if p.field == "cool_c"]
    assert match and match[0].level == "error"
    assert "sweep" in match[0].message


def test_a_very_short_poll_interval_is_a_warning_not_a_block():
    """The poller re-reads its config on this cadence and streams nvidia-smi at
    it. 50ms is a bad idea, not an impossible one."""
    problems = lighting.problems(base(thermal=ThermalConfig(poll_ms=50)))
    match = [p for p in problems if p.field == "poll_ms"]
    assert match and match[0].level == "warning"


def test_a_valid_state_has_no_problems():
    assert lighting.problems(base()) == []


# --- diff ------------------------------------------------------------------


def test_no_change_needs_nothing_sent():
    d = lighting.diff(base(), base(), poller_running=True)
    assert d.empty


def test_editing_the_poller_config_marks_only_the_poller_config():
    draft = base(thermal=ThermalConfig(hot_c=90.0))
    d = lighting.diff(base(), draft, poller_running=True)
    assert d.poller_config
    assert d.unit_action is None
    assert not d.ring_effect


def test_switching_to_static_stops_the_poller_and_drives_the_ring():
    d = lighting.diff(base(mode="thermal"), base(mode="static"),
                      poller_running=True)
    assert d.unit_action == "stop"
    assert d.ring_effect


def test_switching_to_thermal_starts_the_poller_and_sends_no_effect():
    """In thermal mode the poller owns the ring. Anything this app sent would
    be overwritten within ~2s -- the user would see a flash, then a different
    colour, and reasonably conclude the app is broken."""
    d = lighting.diff(base(mode="static"), base(mode="thermal"),
                      poller_running=False)
    assert d.unit_action == "start"
    assert not d.ring_effect


def test_no_unit_action_when_the_poller_is_already_where_it_should_be():
    d = lighting.diff(base(mode="thermal"), base(mode="thermal"),
                      poller_running=True)
    assert d.unit_action is None


def test_the_poller_is_started_when_thermal_is_selected_but_it_is_down():
    """Nothing changed in the draft, but the unit died. Apply should fix it."""
    d = lighting.diff(base(mode="thermal"), base(mode="thermal"),
                      poller_running=False)
    assert d.unit_action == "start"


def test_staying_static_but_changing_colour_re_sends_the_effect():
    d = lighting.diff(base(mode="static", color=(255, 0, 0)),
                      base(mode="static", color=(0, 255, 0)),
                      poller_running=False)
    assert d.ring_effect
    assert d.unit_action is None


def test_staying_static_with_no_change_sends_nothing():
    d = lighting.diff(base(mode="static"), base(mode="static"),
                      poller_running=False)
    assert not d.ring_effect


def test_stopping_the_poller_always_re_sends_the_effect():
    """The poller was just driving the ring, so whatever it last pushed is what
    is lit -- the app's own last-set value is not on the hardware any more, and
    'nothing changed in the draft' is not a reason to skip the send."""
    d = lighting.diff(base(mode="static", color=(255, 0, 0)),
                      base(mode="static", color=(255, 0, 0)),
                      poller_running=True)
    assert d.unit_action == "stop"
    assert d.ring_effect


def test_ring_brightness_change_re_sends_the_effect_in_static_mode():
    d = lighting.diff(base(mode="static", ring_brightness=4),
                      base(mode="static", ring_brightness=1),
                      poller_running=False)
    assert d.ring_effect


def test_ring_brightness_change_in_thermal_mode_is_poller_config_not_an_effect():
    """thermal-rgb.json carries its own brightness; the poller applies it on
    its next push. Sending SetRgbEffect here would be overwritten anyway."""
    d = lighting.diff(
        base(mode="thermal", thermal=ThermalConfig(brightness=4)),
        base(mode="thermal", thermal=ThermalConfig(brightness=1)),
        poller_running=True)
    assert d.poller_config
    assert not d.ring_effect


def test_screen_brightness_is_tracked_separately_from_the_ring():
    d = lighting.diff(base(screen_brightness=100),
                      base(screen_brightness=200), poller_running=True)
    assert d.screen_brightness
    assert not d.ring_effect


def test_setting_screen_brightness_for_the_first_time_counts_as_a_change():
    d = lighting.diff(base(screen_brightness=None),
                      base(screen_brightness=200), poller_running=True)
    assert d.screen_brightness


def test_clearing_screen_brightness_sends_nothing():
    """There is no 'unset the brightness' call. Leaving it alone is correct."""
    d = lighting.diff(base(screen_brightness=200),
                      base(screen_brightness=None), poller_running=True)
    assert not d.screen_brightness


def test_the_module_does_not_import_pyside6():
    """This module is dispatchable precisely because it has no display
    dependency. An accidental PySide6 import moves it out of Codex's reach."""
    assert "PySide6" not in open(lighting.__file__).read()
