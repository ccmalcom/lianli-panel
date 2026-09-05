import pytest

from lianli_panel.apply import (
    ApplyFailed, ConflictError, apply_templates, read_templates, templates_hash,
)
from lianli_panel.ipc import DaemonError

A = {"id": "a", "name": "A", "widgets": []}
B = {"id": "b", "name": "B", "widgets": []}
DEV = "hid:513b5a7acadc4203"


def _config(templates_live_id="a"):
    return {"lcds": [{"serial": DEV, "type": "custom",
                      "template_id": templates_live_id, "orientation": 90.0}]}


def _client(fake_client, stored):
    fake_client.responses["GetLcdTemplates"] = stored
    fake_client.responses["GetConfig"] = _config()
    fake_client.responses["SetLcdTemplates"] = None
    fake_client.responses["SetLcdMedia"] = None
    return fake_client


def test_hash_is_order_sensitive_because_draw_order_matters():
    assert templates_hash([A, B]) != templates_hash([B, A])


def test_hash_ignores_key_ordering():
    assert templates_hash([{"id": "a", "name": "A"}]) == \
           templates_hash([{"name": "A", "id": "a"}])


def test_apply_sends_the_whole_library_not_just_the_live_one(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A, B], live_id="b", device_id=DEV)
    sent = next(p for m, p in c.calls if m == "SetLcdTemplates")
    assert [t["id"] for t in sent["templates"]] == ["a", "b"]


def test_apply_calls_set_media_after_set_templates(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A], live_id="a", device_id=DEV)
    methods = [m for m in c.methods() if m.startswith("SetLcd")]
    assert methods == ["SetLcdTemplates", "SetLcdMedia"]


def test_apply_points_the_lcd_entry_at_the_live_template(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A, B], live_id="b", device_id=DEV)
    params = next(p for m, p in c.calls if m == "SetLcdMedia")
    assert params["config"]["template_id"] == "b"
    assert params["config"]["type"] == "custom"


def test_set_media_addresses_the_entry_by_the_daemons_own_key(fake_client):
    """SetLcdMedia's device_id selects which config.lcds entry to overwrite, and
    the daemon compares it against LcdConfig::device_id() -- "serial:<serial>",
    not the bare id ListDevices reports. Sending the bare id never matches, so
    the daemon appends a second entry instead of replacing the first; the next
    config load drops the duplicate and keeps the FIRST, silently discarding
    the switch. Verified live against lianli-daemon 0.8.8."""
    c = _client(fake_client, [A])
    apply_templates(c, [A, B], live_id="b", device_id=DEV)
    params = next(p for m, p in c.calls if m == "SetLcdMedia")
    assert params["device_id"] == f"serial:{DEV}"


def test_set_media_keys_an_index_only_entry_by_index(fake_client):
    """An entry with no serial is keyed "index:<n>" by the same daemon method."""
    c = _client(fake_client, [A])
    c.responses["GetConfig"] = {"lcds": []}
    fallback = {"index": 0, "type": "custom", "orientation": 90.0}
    apply_templates(c, [A], live_id="a", device_id=DEV, lcd_entry_fallback=fallback)
    params = next(p for m, p in c.calls if m == "SetLcdMedia")
    assert params["device_id"] == "index:0"


def test_conflict_when_the_stored_set_changed_under_us(fake_client):
    c = _client(fake_client, [A, B])
    with pytest.raises(ConflictError):
        apply_templates(c, [A], live_id="a", device_id=DEV,
                        base_hash=templates_hash([A]))
    assert "SetLcdTemplates" not in c.methods()


def test_matching_base_hash_applies_normally(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A, B], live_id="a", device_id=DEV,
                    base_hash=templates_hash([A]))
    assert "SetLcdTemplates" in c.methods()


def test_failed_set_media_restores_the_previous_template_set(fake_client):
    c = _client(fake_client, [A])
    c.responses["SetLcdMedia"] = DaemonError("device busy")
    with pytest.raises(ApplyFailed):
        apply_templates(c, [A, B], live_id="b", device_id=DEV)
    sets = [p["templates"] for m, p in c.calls if m == "SetLcdTemplates"]
    assert len(sets) == 2
    assert [t["id"] for t in sets[1]] == ["a"]      # rolled back


def test_missing_lcd_entry_is_restored_from_the_fallback(fake_client):
    """lianli-gui wipes the lcds array; the entry must be rebuilt, not invented."""
    c = _client(fake_client, [A])
    c.responses["GetConfig"] = {"lcds": []}
    fallback = {"serial": DEV, "type": "custom", "orientation": 90.0}
    apply_templates(c, [A], live_id="a", device_id=DEV, lcd_entry_fallback=fallback)
    params = next(p for m, p in c.calls if m == "SetLcdMedia")
    assert params["config"]["orientation"] == 90.0


def test_missing_lcd_entry_with_no_fallback_fails_loudly(fake_client):
    c = _client(fake_client, [A])
    c.responses["GetConfig"] = {"lcds": []}
    with pytest.raises(ApplyFailed, match="no LCD entry"):
        apply_templates(c, [A], live_id="a", device_id=DEV)


def test_read_templates_returns_the_set_and_its_hash(fake_client):
    fake_client.responses["GetLcdTemplates"] = [A]
    templates, digest = read_templates(fake_client)
    assert templates == [A] and digest == templates_hash([A])


from lianli_panel.apply import live_template_id


def test_live_template_comes_from_the_entry_matching_the_serial():
    config = {"lcds": [
        {"serial": "hid:other", "template_id": "someone-elses"},
        {"serial": "hid:513b5a7acadc4203", "template_id": "gaming-dash"},
    ]}
    assert live_template_id(config, "hid:513b5a7acadc4203") == "gaming-dash"


def test_live_template_is_not_simply_the_first_entry():
    """The defect this function exists to fix: lcds[0] can belong to another
    device entirely."""
    config = {"lcds": [
        {"serial": "hid:other", "template_id": "someone-elses"},
        {"serial": "hid:513b5a7acadc4203", "template_id": "gaming-dash"},
    ]}
    assert live_template_id(config, "hid:513b5a7acadc4203") != "someone-elses"


def test_duplicate_entries_resolve_to_the_first_like_the_daemon_does():
    """AppConfig::load collapses duplicate serials keeping the FIRST. Reading
    the second would report a template the panel is not rendering."""
    config = {"lcds": [
        {"serial": "hid:513b5a7acadc4203", "template_id": "stale"},
        {"serial": "hid:513b5a7acadc4203", "template_id": "just-applied"},
    ]}
    assert live_template_id(config, "hid:513b5a7acadc4203") == "stale"


def test_no_entry_for_this_panel_is_none_not_a_guess():
    """lianli-gui wipes the array. Returning lcds[0] here would report another
    device's template as this panel's."""
    config = {"lcds": [{"serial": "hid:other", "template_id": "someone-elses"}]}
    assert live_template_id(config, "hid:513b5a7acadc4203") is None


def test_an_empty_or_missing_lcds_array_is_none():
    assert live_template_id({"lcds": []}, "hid:513b5a7acadc4203") is None
    assert live_template_id({}, "hid:513b5a7acadc4203") is None


def test_an_entry_with_no_template_id_is_none_not_a_keyerror():
    config = {"lcds": [{"serial": "hid:513b5a7acadc4203", "type": "sensor"}]}
    assert live_template_id(config, "hid:513b5a7acadc4203") is None
