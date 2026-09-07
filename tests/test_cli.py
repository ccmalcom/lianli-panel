import pytest

from lianli_panel.cli import build_parser
from tests.conftest import FakeClient


def test_parser_exposes_every_subcommand():
    p = build_parser()
    for argv in (["status"], ["list"], ["apply", "x"], ["preview", "x"],
                 ["sensor-test", "echo 1"], ["ring", "off"],
                 ["ring", "static", "0", "1", "2"], ["snapshot"], ["revert"],
                 ["validate", "f.json"],
                 ["ring", "thermal"], ["ring", "brightness", "120"],
                 ["poller", "show"], ["poller", "set", "--hot-c", "90"]):
        assert p.parse_args(argv) is not None


def test_ring_static_requires_three_components():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["ring", "static", "0", "1"])


def test_preview_defaults_to_substituted_not_live():
    assert build_parser().parse_args(["preview", "x"]).live is False


def test_ring_brightness_targets_the_panel_not_the_ring(monkeypatch, capsys):
    """Two things called brightness, one command name. The test exists so a
    later refactor cannot quietly point this at the ring."""
    from lianli_panel import cli, ring
    sent = []
    monkeypatch.setattr(ring, "set_lcd_brightness",
                        lambda c, d, v: sent.append((d, v)))
    monkeypatch.setattr(cli, "Client", lambda: FakeClient(
        {"ListDevices": [{"device_id": "hid:abc", "has_lcd": True}]}))
    assert cli.main(["ring", "brightness", "120"]) == 0
    assert sent == [("hid:abc", 120)]


def test_poller_set_writes_only_the_fields_given(monkeypatch, tmp_path,
                                                 capsys):
    from lianli_panel import cli, ring
    path = tmp_path / "thermal-rgb.json"
    monkeypatch.setattr(ring, "THERMAL_CONFIG_PATH", path)
    monkeypatch.setattr(cli, "Client", lambda: FakeClient({}))
    cli.main(["poller", "set", "--hot-c", "90"])
    written = ring.load_thermal(path)
    assert written.hot_c == 90.0
    assert written.cool_c == 45.0        # untouched default


def test_poller_set_refuses_an_inverted_sweep(monkeypatch, tmp_path):
    from lianli_panel import cli, ring
    monkeypatch.setattr(ring, "THERMAL_CONFIG_PATH",
                        tmp_path / "thermal-rgb.json")
    monkeypatch.setattr(cli, "Client", lambda: FakeClient({}))
    assert cli.main(["poller", "set", "--cool-c", "90", "--hot-c", "50"]) == 2
