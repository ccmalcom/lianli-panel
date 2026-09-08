"""The Lighting tab, offscreen.

The tab takes NO client. Every side effect belongs to the window, which is what
lets these tests construct it with nothing and still exercise the whole surface.
"""
import pytest

from lianli_panel import lighting, ring


@pytest.fixture
def tab(qapp):
    from lianli_panel.gui.lighting_tab import LightingTab
    return LightingTab()


def a_state(**kw):
    defaults = dict(mode="static", color=(10, 20, 30), ring_brightness=2,
                    screen_brightness=180,
                    thermal=ring.ThermalConfig(cool_c=40.0, hot_c=90.0,
                                               poll_ms=1000))
    defaults.update(kw)
    return lighting.LightingState(**defaults)


def test_the_tab_needs_no_client(qapp):
    """A view that can reach the daemon is a view that can surprise the user
    mid-edit. Every send belongs to the window."""
    from lianli_panel.gui.lighting_tab import LightingTab
    assert LightingTab() is not None


def test_state_round_trips_through_the_controls(tab):
    tab.set_state(a_state())
    assert tab.state() == a_state()


def test_an_unset_screen_brightness_round_trips_as_none(tab):
    """None is 'the panel's own default', which is not the same as 0 -- and 0
    is a black screen the user cannot read to undo it."""
    tab.set_state(a_state(screen_brightness=None))
    assert tab.state().screen_brightness is None


def test_populating_the_controls_does_not_report_a_user_edit(tab):
    """set_state runs on every load and every revert. If it emitted, the draft
    would be dirty the moment the app opened."""
    seen = []
    tab.changed.connect(lambda: seen.append(1))
    tab.set_state(a_state())
    assert seen == []


def test_editing_a_control_reports_a_change(tab):
    tab.set_state(a_state())
    seen = []
    tab.changed.connect(lambda: seen.append(1))
    tab.ring_brightness.setValue(1)
    assert seen


def test_test_on_ring_is_disabled_in_thermal_mode(tab):
    """In thermal mode the poller owns the ring; a test send would flash and
    vanish, which reads as a broken button."""
    tab.set_state(a_state(mode="thermal"))
    assert not tab.test_button.isEnabled()


def test_test_on_ring_is_enabled_in_static_mode(tab):
    tab.set_state(a_state(mode="static"))
    assert tab.test_button.isEnabled()


def test_pressing_test_on_ring_asks_the_window_to_send(tab):
    tab.set_state(a_state(mode="static"))
    seen = []
    tab.test_requested.connect(lambda: seen.append(1))
    tab.test_button.click()
    assert seen == [1]


def test_the_interlock_is_shown_before_apply(tab):
    tab.set_state(a_state(mode="static"))
    tab.set_poller_running(True)
    assert "STOP" in tab.interlock.text()


def test_the_interlock_clears_when_no_unit_change_is_needed(tab):
    tab.set_state(a_state(mode="static"))
    tab.set_poller_running(False)
    assert tab.interlock.text() == ""


def test_the_poller_status_is_shown_because_it_is_the_one_readable_fact(tab):
    tab.set_poller_running(True)
    assert "active" in tab.poller_status.text().lower()
    tab.set_poller_running(False)
    assert "active" not in tab.poller_status.text().lower()


def test_the_last_set_colour_is_labelled_as_not_a_read_back(tab):
    """Showing a swatch without this caption would imply the app knows what
    the ring is lit with. It cannot; GetZoneColors fails on this device."""
    tab.set_last_set(ring.RingState("static", (1, 2, 3), 2,
                                    "2026-09-05T12:00:00+01:00"))
    text = tab.last_set.text()
    assert "2026-09-05" in text
    assert "read back" in text.lower()


def test_a_ring_never_set_by_this_app_says_so(tab):
    tab.set_last_set(ring.RingState())
    assert "never" in tab.last_set.text().lower()


def test_problems_are_shown_with_their_message(tab):
    tab.set_problems([lighting.Problem("error", "cool_c", "cool must be below hot")])
    assert "cool must be below hot" in tab.problems.text()


def test_no_problems_clears_the_line(tab):
    tab.set_problems([lighting.Problem("error", "cool_c", "bad")])
    tab.set_problems([])
    assert tab.problems.text() == ""


def test_the_two_brightnesses_are_separate_controls(tab):
    """They are 0-4 and 0-255, they reach different hardware, and confusing
    them sets the screen to 4/255 -- effectively black."""
    tab.set_state(a_state(ring_brightness=2, screen_brightness=180))
    assert tab.ring_brightness.value() == 2
    assert tab.screen_brightness.value() == 180
    assert tab.ring_brightness is not tab.screen_brightness
