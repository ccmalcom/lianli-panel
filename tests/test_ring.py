import json

import pytest

from lianli_panel import ring
from lianli_panel.ring import (
    ThermalConfig, find_ring, load_thermal, save_thermal, set_off, set_static,
)
from tests.conftest import FakeClient

DEVICES = [
    {"device_id": "hid:513b5a7acadc4203", "name": "Universal Screen 8.8\"",
     "has_lcd": True, "has_rgb": False, "pid": 41096},
    {"device_id": "hid:0416:8050:1-8.3", "name": "LED Ring",
     "has_lcd": False, "has_rgb": True, "pid": 0x8050},
]


def test_ring_is_resolved_at_runtime_not_hardcoded(fake_client):
    """The ring's id is derived from its USB path and changes on every replug
    into a different port, unlike the LCD's stable serial."""
    fake_client.responses["ListDevices"] = DEVICES
    assert find_ring(fake_client) == "hid:0416:8050:1-8.3"


def test_missing_ring_raises(fake_client):
    fake_client.responses["ListDevices"] = [DEVICES[0]]
    with pytest.raises(RuntimeError, match="no LED ring"):
        find_ring(fake_client)


def test_static_uses_set_rgb_effect_not_set_config(fake_client):
    """SetConfig persists RGB but never applies it -- only SetRgbEffect
    reaches the hardware."""
    fake_client.responses["ListDevices"] = DEVICES
    fake_client.responses["SetRgbEffect"] = None
    set_static(fake_client, (0, 200, 255))
    assert "SetRgbEffect" in fake_client.methods()
    params = next(p for m, p in fake_client.calls if m == "SetRgbEffect")
    assert params["effect"]["mode"] == "Static"
    assert params["effect"]["colors"] == [[0, 200, 255]]


def test_off_sends_mode_off(fake_client):
    fake_client.responses["ListDevices"] = DEVICES
    fake_client.responses["SetRgbEffect"] = None
    set_off(fake_client)
    params = next(p for m, p in fake_client.calls if m == "SetRgbEffect")
    assert params["effect"]["mode"] == "Off"


def test_colour_components_are_validated(fake_client):
    fake_client.responses["ListDevices"] = DEVICES
    with pytest.raises(ValueError):
        set_static(fake_client, (0, 300, 0))


# --- thermal poller config -------------------------------------------------

def test_defaults_match_the_pollers_current_constants():
    c = ThermalConfig()
    assert (c.cool_c, c.hot_c, c.poll_ms) == (45.0, 85.0, 2000)
    assert (c.min_delta_c, c.force_refresh_s, c.brightness) == (1.0, 60, 4)


def test_missing_config_file_yields_defaults(tmp_path):
    assert load_thermal(tmp_path / "absent.json") == ThermalConfig()


def test_config_roundtrips(tmp_path):
    path = tmp_path / "thermal.json"
    save_thermal(ThermalConfig(cool_c=40.0, hot_c=90.0), path)
    assert load_thermal(path).hot_c == 90.0


def test_unknown_keys_in_the_file_are_ignored(tmp_path):
    path = tmp_path / "thermal.json"
    path.write_text(json.dumps({"cool_c": 30.0, "future_key": "x"}))
    assert load_thermal(path).cool_c == 30.0


def test_cool_must_be_below_hot(tmp_path):
    with pytest.raises(ValueError):
        save_thermal(ThermalConfig(cool_c=90.0, hot_c=40.0), tmp_path / "t.json")


RING_DEVICE = {"device_id": "hid:0416:8050:1-12.3", "has_rgb": True,
               "pid": 0x8050}
LCD_BARE_ID = "hid:513b5a7acadc4203"


def ring_client():
    return FakeClient({"ListDevices": [RING_DEVICE], "SetRgbEffect": None,
                       "SetLcdBrightness": {"applied": True}})


def test_set_lcd_brightness_uses_the_bare_device_id():
    """THE trap this function exists to contain. SetLcdMedia wants
    "serial:hid:..."; SetLcdBrightness wants the bare "hid:..." because it is
    matched against ActiveTarget::device_identity. Getting it backwards fails
    SILENTLY -- the reply is still {"applied": true}."""
    client = ring_client()
    ring.set_lcd_brightness(client, LCD_BARE_ID, 180)
    method, params = client.calls[-1]
    assert method == "SetLcdBrightness"
    assert params["device_id"] == LCD_BARE_ID
    assert not params["device_id"].startswith("serial:")
    assert params["brightness"] == 180


def test_set_lcd_brightness_rejects_a_value_above_255():
    with pytest.raises(ValueError, match="0-255"):
        ring.set_lcd_brightness(ring_client(), LCD_BARE_ID, 300)


def test_set_lcd_brightness_rejects_a_negative_value():
    with pytest.raises(ValueError, match="0-255"):
        ring.set_lcd_brightness(ring_client(), LCD_BARE_ID, -1)


def test_set_mode_off_sends_the_off_effect():
    client = ring_client()
    ring.set_mode(client, "off")
    method, params = client.calls[-1]
    assert method == "SetRgbEffect"
    assert params["effect"]["mode"] == "Off"


def test_set_mode_static_sends_the_colour_and_brightness():
    client = ring_client()
    ring.set_mode(client, "static", (10, 20, 30), 2)
    _, params = client.calls[-1]
    assert params["effect"]["mode"] == "Static"
    assert params["effect"]["colors"] == [[10, 20, 30]]
    assert params["effect"]["brightness"] == 2


def test_set_mode_thermal_refuses_rather_than_fighting_the_poller():
    """A caller that reaches here has a bug: in thermal mode the poller owns
    the ring and re-drives it every ~2s, so anything sent flashes and is gone."""
    client = ring_client()
    with pytest.raises(ValueError, match="poller"):
        ring.set_mode(client, "thermal")
    assert client.methods() == []


class FakeRun:
    """Stands in for subprocess.run. Tests must never shell out to systemctl."""

    def __init__(self, stdout="", returncode=0, raises=None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.stderr = ""
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        if self.raises is not None:
            raise self.raises
        return self


def test_poller_active_is_true_only_for_the_word_active():
    assert ring.poller_active(runner=FakeRun("active\n")) is True
    assert ring.poller_active(runner=FakeRun("inactive\n")) is False
    assert ring.poller_active(runner=FakeRun("failed\n")) is False


def test_poller_active_asks_the_user_manager_about_the_right_unit():
    run = FakeRun("active\n")
    ring.poller_active(runner=run)
    assert run.calls == [["systemctl", "--user", "is-active",
                          "lianli-thermal-rgb.service"]]


def test_poller_active_is_false_when_systemctl_cannot_be_run():
    """An unreadable status is not a running poller. Guessing 'active' here
    would make Apply skip the start it needs."""
    assert ring.poller_active(runner=FakeRun(raises=OSError("no systemctl"))) \
        is False


def test_stop_poller_stops_the_unit():
    run = FakeRun()
    ring.stop_poller(runner=run)
    assert run.calls == [["systemctl", "--user", "stop",
                          "lianli-thermal-rgb.service"]]


def test_start_poller_starts_the_unit():
    run = FakeRun()
    ring.start_poller(runner=run)
    assert run.calls == [["systemctl", "--user", "start",
                          "lianli-thermal-rgb.service"]]


def test_a_failing_systemctl_raises_and_names_the_unit():
    run = FakeRun(returncode=1)
    run.stderr = "Unit lianli-thermal-rgb.service not found."
    with pytest.raises(RuntimeError, match="lianli-thermal-rgb.service"):
        ring.stop_poller(runner=run)


def test_ring_state_round_trips(tmp_path):
    path = tmp_path / "ring-state.json"
    ring.save_ring_state(ring.RingState("static", (1, 2, 3), 3), path)
    loaded = ring.load_ring_state(path)
    assert loaded.mode == "static"
    assert loaded.color == (1, 2, 3)
    assert loaded.brightness == 3


def test_saving_stamps_when_it_was_set(tmp_path):
    """The Lighting tab shows this timestamp beside the colour, because
    'what this app last sent' is a claim that needs a time on it."""
    path = tmp_path / "ring-state.json"
    ring.save_ring_state(ring.RingState("static", (1, 2, 3), 3), path)
    assert ring.load_ring_state(path).set_at is not None


def test_a_missing_state_file_reads_as_never_set(tmp_path):
    state = ring.load_ring_state(tmp_path / "nothing.json")
    assert state.set_at is None


def test_a_corrupt_state_file_reads_as_never_set(tmp_path):
    path = tmp_path / "ring-state.json"
    path.write_text("{ this is not json")
    assert ring.load_ring_state(path).set_at is None
