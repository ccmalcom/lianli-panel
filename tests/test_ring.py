import json

import pytest

from lianli_panel.ring import (
    ThermalConfig, find_ring, load_thermal, save_thermal, set_off, set_static,
)

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
