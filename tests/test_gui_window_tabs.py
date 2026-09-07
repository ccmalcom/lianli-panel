"""The window with three tabs and one Apply.

These are smoke tests in the same sense as test_gui_smoke.py: they catch import
errors, bad signal signatures and null derefs. They are NOT evidence the app
works -- that is steps 11 and 12, run by the controller against the panel.
"""
import pytest

from lianli_panel import apply as apply_mod
from lianli_panel import lighting
from lianli_panel.ring import RingState, ThermalConfig
from tests.test_gui_smoke import make_client, stub_poller

CONFIG_WITH_BRIGHTNESS = {
    "lcds": [{"serial": "hid:513b5a7acadc4203", "type": "custom",
              "template_id": "gaming-dash", "orientation": 90,
              "brightness": 200}]}


def fake_ops(**over):
    world = {"running": True, "thermal": ThermalConfig(), "ring": None,
             "brightness": None, "ring_state": None}
    ops = apply_mod.LightingOps(
        read_thermal=lambda: world["thermal"],
        write_thermal=lambda cfg: world.update(thermal=cfg),
        poller_active=lambda: world["running"],
        stop_poller=lambda: world.update(running=False),
        start_poller=lambda: world.update(running=True),
        set_ring=lambda c, m, col, b: world.update(ring=(m, col, b)),
        set_lcd_brightness=lambda c, d, v: world.update(brightness=(d, v)),
        save_ring_state=lambda m, col, b: world.update(ring_state=(m, col, b)))
    for key, value in over.items():
        setattr(ops, key, value)
    return ops, world


@pytest.fixture
def win(qapp):
    from lianli_panel.gui.window import MainWindow
    ops, world = fake_ops()
    w = MainWindow(make_client(GetConfig=CONFIG_WITH_BRIGHTNESS),
                   health_poller=stub_poller(), lighting_ops=ops)
    w._test_world = world
    yield w
    w.close()


def test_the_window_has_the_three_tabs(win):
    labels = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert labels == ["Editor", "Sensors", "Lighting"]


def test_the_lighting_tab_loads_the_brightness_the_daemon_has_stored(win):
    """config.lcds[].brightness is the one lighting value with a real
    read-back. Ignoring it would show a default the panel is not using."""
    assert win.lighting_tab.state().screen_brightness == 200


def test_a_panel_that_has_never_had_a_brightness_set_shows_none(qapp):
    from lianli_panel.gui.window import MainWindow
    ops, _ = fake_ops()
    w = MainWindow(make_client(), health_poller=stub_poller(),
                   lighting_ops=ops)
    assert w.lighting_tab.state().screen_brightness is None
    w.close()


def test_the_lighting_tab_shows_whether_the_poller_is_running(win):
    assert "ACTIVE" in win.lighting_tab.poller_status.text()


def test_apply_routes_through_apply_all(win, monkeypatch, tmp_path):
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(apply_mod, "apply_all",
                        lambda *a, **k: calls.append(k) or [])
    win.apply_now()
    assert calls and "draft_lighting" in calls[0]


def test_apply_sends_the_lighting_edits_made_on_the_tab(win, monkeypatch,
                                                        tmp_path):
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    win.lighting_tab.set_state(lighting.LightingState(
        mode="static", color=(255, 0, 0), ring_brightness=3))
    win.apply_now()
    assert win._test_world["ring"] == ("static", (255, 0, 0), 3)


def test_a_validate_warning_is_shown_and_does_not_block(win, monkeypatch,
                                                        tmp_path):
    """Plan A computed warnings and threw them away. A no-catch-all-range
    warning means values above the last threshold have no colour -- worth
    saying, not worth refusing."""
    from lianli_panel.model import Problem
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    monkeypatch.setattr("lianli_panel.gui.window.validate",
                        lambda t: [Problem("warning", "cpu", "no catch-all range")])
    applied = []
    monkeypatch.setattr(apply_mod, "apply_all",
                        lambda *a, **k: applied.append(1) or [])
    win.apply_now()
    assert applied
    assert "no catch-all range" in win.banner.text()


def test_a_partial_apply_reports_every_stage(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda *a, **k: shown.append(a[2]))

    def boom(*a, **k):
        raise apply_mod.PartialApply("SetLcdMedia refused", [
            apply_mod.StageResult("poller unit", "rolled back"),
            apply_mod.StageResult("templates", "failed", "refused")])

    monkeypatch.setattr(apply_mod, "apply_all", boom)
    win.apply_now()
    assert shown and "poller unit" in shown[0] and "rolled back" in shown[0]


def test_closing_prompts_when_only_the_lighting_changed(win, monkeypatch):
    """The draft is clean, but stopping the poller and driving the ring is
    still unapplied work."""
    from PySide6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(1)
                        or QMessageBox.StandardButton.Yes)
    win.show()
    win.lighting_tab.set_state(lighting.LightingState(mode="off"))
    win.lighting_tab.changed.emit()
    win.close()
    assert asked


def test_the_sensors_tab_target_follows_the_editor_selection(win):
    win._select("cpu")
    assert "cpu" in win.sensors_tab.target_label.text()


def test_deselecting_clears_the_sensors_tab_target(win):
    win._select("cpu")
    win._select("")
    assert not win.sensors_tab.bind_button.isEnabled()


def test_binding_a_sensor_rewrites_the_widgets_source_and_dirties_the_draft(
        win, monkeypatch, tmp_path):
    from lianli_panel.sensors import Sensor
    monkeypatch.setattr("lianli_panel.gui.links.LINKS_PATH",
                        tmp_path / "links.json")
    win.sensors_tab.set_library(
        {"cpu-pct": Sensor("cpu-pct", {"type": "cpu_usage"})})
    win._select("cpu")
    win._bind_sensor("cpu-pct")
    assert win.draft.widget("cpu").source == {"type": "cpu_usage"}
    assert win.draft.dirty


def test_a_stale_link_is_dropped_and_the_reason_is_shown(win, monkeypatch):
    """The side-car map cannot know that something else edited the template,
    so it is validated on load and says what it dropped."""
    from lianli_panel.gui import links
    from lianli_panel.sensors import Sensor
    monkeypatch.setattr(links, "load", lambda path=None: {
        ("gaming-dash", "cpu"): "cpu-pct"})
    win.sensors_tab.set_library(
        {"cpu-pct": Sensor("cpu-pct", {"type": "command", "cmd": "elsewhere"})})
    win.revalidate_links()
    assert "cpu-pct" in win.banner.text()
