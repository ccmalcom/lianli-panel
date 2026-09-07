import json
from pathlib import Path

import pytest

from lianli_panel.snapshot import latest, load, prune, take
from tests.conftest import FakeClient

A = {"id": "a", "name": "A", "widgets": []}


@pytest.fixture(autouse=True)
def _isolate_lighting_files_and_systemd(monkeypatch, tmp_path):
    """Keep snapshot tests away from host state and the user systemd manager."""
    from lianli_panel import ring, snapshot
    monkeypatch.setattr(ring, "poller_active", lambda: False)
    monkeypatch.setattr(ring, "load_thermal", lambda path=None: ring.ThermalConfig())
    monkeypatch.setattr(ring, "load_ring_state", lambda path=None: ring.RingState())
    monkeypatch.setattr(snapshot, "RGB_STATE_FILE", tmp_path / "rgb-state.json")


def _client(fake_client):
    fake_client.responses["GetLcdTemplates"] = [A]
    fake_client.responses["GetConfig"] = {
        "lcds": [{"serial": "hid:x", "template_id": "a"}],
        "rgb": {"devices": [{"device_id": "hid:ring", "zones": []}]},
    }
    return fake_client


def test_take_writes_a_readable_snapshot(tmp_path, fake_client):
    path = take(_client(fake_client), root=tmp_path)
    data = load(path)
    assert data["templates"] == [A]
    assert data["lcds"][0]["template_id"] == "a"
    assert "taken_at" in data


def test_snapshot_records_the_template_hash(tmp_path, fake_client):
    from lianli_panel.apply import templates_hash
    data = load(take(_client(fake_client), root=tmp_path))
    assert data["templates_hash"] == templates_hash([A])


def test_snapshot_disclaims_ring_readback(tmp_path, fake_client):
    data = load(take(_client(fake_client), root=tmp_path))
    assert "cannot be read back" in data["note"]


def test_prune_keeps_only_the_newest_n(tmp_path):
    for i in range(25):
        d = tmp_path / f"2026-09-04T00-00-{i:02d}"
        d.mkdir(parents=True)
        (d / "snapshot.json").write_text("{}")
    removed = prune(tmp_path, keep=20)
    assert len(removed) == 5
    assert len(list(tmp_path.iterdir())) == 20
    assert (tmp_path / "2026-09-04T00-00-24").exists()
    assert not (tmp_path / "2026-09-04T00-00-00").exists()


def test_prune_is_a_noop_below_the_limit(tmp_path):
    (tmp_path / "2026-09-04T00-00-01").mkdir(parents=True)
    assert prune(tmp_path, keep=20) == []


def test_take_prunes_as_it_goes(tmp_path, fake_client):
    c = _client(fake_client)
    for _ in range(3):
        take(c, root=tmp_path, keep=2)
    assert len(list(tmp_path.iterdir())) == 2


def test_latest_returns_the_newest(tmp_path):
    for name in ("2026-09-04T00-00-01", "2026-09-04T00-00-09"):
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / "snapshot.json").write_text("{}")
    assert latest(tmp_path).name == "2026-09-04T00-00-09"


def test_latest_on_an_empty_root_is_none(tmp_path):
    assert latest(tmp_path) is None


def test_a_snapshot_records_the_poller_config_and_the_rings_last_set_state(
        tmp_path, monkeypatch):
    """Plan A's snapshot recorded only whether the unit was active, which is
    not enough to describe -- let alone restore -- the lighting."""
    from lianli_panel import ring, snapshot
    monkeypatch.setattr(ring, "load_thermal",
                        lambda path=None: ring.ThermalConfig(hot_c=90.0))
    monkeypatch.setattr(ring, "load_ring_state",
                        lambda path=None: ring.RingState("static", (1, 2, 3), 2,
                                                         "2026-09-05T12:00:00"))
    client = FakeClient({"GetLcdTemplates": [], "GetConfig": {"lcds": []}})
    data = snapshot.load(snapshot.take(client, root=tmp_path,
                                       poller_active=lambda: True))
    assert data["thermal_service_active"] is True
    assert data["thermal_config"]["hot_c"] == 90.0
    assert data["ring_last_set"]["mode"] == "static"
    assert data["ring_last_set"]["color"] == [1, 2, 3]
