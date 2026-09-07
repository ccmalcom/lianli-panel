"""The unified Apply.

ORDER is what most of these tests are about, and a per-mock call list cannot
express "before", so the client and every lighting op append to ONE shared log.
The two orderings that produce a visibly wrong result rather than an error:

  * the poller must be STOPPED before the ring is driven, or it overwrites the
    colour within ~2s and the user sees their choice flash and change
  * nothing may touch the template set until every lighting stage has succeeded,
    because the template set is the only thing here whose rollback is a second
    whole-set write
"""
import pytest

from lianli_panel import apply as apply_mod
from lianli_panel import lighting
from lianli_panel.ipc import DaemonError
from lianli_panel.ring import ThermalConfig
from tests.conftest import FakeClient

LCD_BARE_ID = "hid:513b5a7acadc4203"

TEMPLATES = [{"id": "dash", "name": "Dash", "base_width": 1920,
              "base_height": 480, "rotated": True,
              "background": {"type": "color", "rgb": [0, 0, 0, 255]},
              "widgets": []}]

CONFIG = {"lcds": [{"serial": LCD_BARE_ID, "type": "custom",
                    "template_id": "dash", "orientation": 90.0}]}


def state(**kw):
    defaults = dict(mode="thermal", color=(255, 255, 255), ring_brightness=4,
                    screen_brightness=None, thermal=ThermalConfig())
    defaults.update(kw)
    return lighting.LightingState(**defaults)


def harness(*, poller_running=True, fail=None, responses=None):
    """Returns (client, ops, log, world).

    `fail` names one operation -- an IPC method or an op name -- that raises.
    `world` is the mutable state the ops read and write, so a test can assert
    on what was actually left behind after a rollback.
    """
    log = []
    world = {"thermal": ThermalConfig(), "running": poller_running,
             "ring": None, "brightness": None, "ring_state": None}

    class LoggingClient(FakeClient):
        def call(self, method, params=None):
            log.append(("ipc", method))
            if fail == method:
                raise DaemonError(f"{method} refused")
            return super().call(method, params)

    base = {"GetLcdTemplates": TEMPLATES, "GetConfig": CONFIG,
            "ListDevices": [{"device_id": LCD_BARE_ID, "has_lcd": True}],
            "SetLcdTemplates": None, "SetLcdMedia": None,
            "SetRgbEffect": None, "SetLcdBrightness": {"applied": True}}
    base.update(responses or {})
    client = LoggingClient(base)

    def guard(name):
        if fail == name:
            raise RuntimeError(f"{name} refused")

    def read_thermal():
        return world["thermal"]

    def write_thermal(cfg):
        log.append(("write_thermal", cfg.hot_c))
        guard("write_thermal")
        world["thermal"] = cfg

    def stop_poller():
        log.append(("stop_poller", None))
        guard("stop_poller")
        world["running"] = False

    def start_poller():
        log.append(("start_poller", None))
        guard("start_poller")
        world["running"] = True

    def set_ring(_client, mode, color, brightness):
        log.append(("set_ring", mode))
        guard("set_ring")
        world["ring"] = (mode, color, brightness)

    def set_lcd_brightness(_client, device_id, value):
        log.append(("set_lcd_brightness", device_id))
        guard("set_lcd_brightness")
        world["brightness"] = (device_id, value)

    def save_ring_state(mode, color, brightness):
        log.append(("save_ring_state", mode))
        world["ring_state"] = (mode, color, brightness)

    ops = apply_mod.LightingOps(
        read_thermal=read_thermal, write_thermal=write_thermal,
        poller_active=lambda: world["running"],
        stop_poller=stop_poller, start_poller=start_poller,
        set_ring=set_ring, set_lcd_brightness=set_lcd_brightness,
        save_ring_state=save_ring_state)
    return client, ops, log, world


def run(client, ops, base, draft, **kw):
    return apply_mod.apply_all(
        client, templates=TEMPLATES, live_id="dash", base_lighting=base,
        draft_lighting=draft, device_id=LCD_BARE_ID, ops=ops, **kw)


def test_no_lighting_change_sends_only_the_template_calls():
    client, ops, log, _ = harness()
    run(client, ops, state(), state())
    assert ("set_ring", "static") not in log
    assert ("stop_poller", None) not in log
    assert ("ipc", "SetLcdTemplates") in log
    assert ("ipc", "SetLcdMedia") in log


def test_the_poller_config_is_written_before_the_unit_is_stopped():
    """Otherwise the poller gets one last cycle with the old config, which is
    visible on the ring as a colour that briefly disagrees with the settings."""
    client, ops, log, _ = harness()
    run(client, ops, state(),
        state(mode="static", thermal=ThermalConfig(hot_c=90.0)))
    assert log.index(("write_thermal", 90.0)) < log.index(("stop_poller", None))


def test_the_poller_is_stopped_before_the_ring_is_driven():
    """THE ordering rule. Driving first means the poller overwrites the colour
    within ~2s and the user watches their choice disappear."""
    client, ops, log, _ = harness(poller_running=True)
    run(client, ops, state(), state(mode="static", color=(255, 0, 0)))
    assert log.index(("stop_poller", None)) < log.index(("set_ring", "static"))


def test_every_lighting_stage_happens_before_the_template_set_is_touched():
    client, ops, log, _ = harness()
    run(client, ops, state(), state(mode="static", color=(255, 0, 0)))
    assert log.index(("set_ring", "static")) < log.index(("ipc", "SetLcdTemplates"))


def test_thermal_mode_never_drives_the_ring_itself():
    client, ops, log, _ = harness(poller_running=False)
    run(client, ops, state(mode="static"), state(mode="thermal"))
    assert ("start_poller", None) in log
    assert not any(e[0] == "set_ring" for e in log)


def test_the_stage_list_reports_every_stage_including_the_skipped_ones():
    client, ops, _, _ = harness()
    stages = run(client, ops, state(), state())
    reported = {s.name for s in stages}
    assert reported >= {"poller config", "poller unit", "ring effect",
                        "templates", "screen brightness"}
    assert {s.status for s in stages if s.name == "ring effect"} == {"skipped"}


def test_brightness_is_folded_into_the_entry_setlcdmedia_already_sends():
    """This is what makes brightness PERSIST. SetLcdBrightness alone is lost on
    the next daemon restart; config.lcds[].brightness is applied at target
    creation."""
    client, ops, _, _ = harness()
    run(client, ops, state(), state(screen_brightness=200))
    media = [p for m, p in client.calls if m == "SetLcdMedia"][-1]
    assert media["config"]["brightness"] == 200


def test_setlcdmedia_still_uses_the_serial_prefixed_key():
    """Regression guard for the bug that cost a whole session. The two calls in
    this transaction take DIFFERENT key formats."""
    client, ops, _, _ = harness()
    run(client, ops, state(), state(screen_brightness=200))
    media = [p for m, p in client.calls if m == "SetLcdMedia"][-1]
    assert media["device_id"] == f"serial:{LCD_BARE_ID}"


def test_setlcdbrightness_uses_the_bare_id_not_the_serial_prefixed_one():
    client, ops, _, world = harness()
    run(client, ops, state(), state(screen_brightness=200))
    assert world["brightness"] == (LCD_BARE_ID, 200)


def test_the_brightness_stage_says_it_is_unverifiable():
    """It replies ok before touching the device. Reporting it as 'done' would
    be a claim this app cannot support."""
    client, ops, _, _ = harness()
    stages = run(client, ops, state(), state(screen_brightness=200))
    stage = [s for s in stages if s.name == "screen brightness"][0]
    assert stage.status == "unverifiable"
    assert "persisted" in stage.detail


def test_unchanged_brightness_is_not_re_sent():
    client, ops, _, world = harness()
    run(client, ops, state(screen_brightness=200), state(screen_brightness=200))
    assert world["brightness"] is None


def test_a_failed_ring_effect_rolls_back_the_unit_and_the_config():
    client, ops, log, world = harness(poller_running=True, fail="set_ring")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(),
            state(mode="static", thermal=ThermalConfig(hot_c=90.0)))
    assert world["running"] is True            # the unit was restarted
    assert world["thermal"].hot_c == 85.0      # the file was rewritten
    assert ("ipc", "SetLcdTemplates") not in log


def test_a_failed_template_stage_rolls_back_every_lighting_stage():
    client, ops, _, world = harness(poller_running=True, fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply) as exc:
        run(client, ops, state(), state(mode="static", color=(255, 0, 0)))
    assert world["running"] is True
    assert {s.name for s in exc.value.stages if s.status == "rolled back"} \
        >= {"poller unit"}


def test_rolling_back_the_ring_re_sends_the_previous_static_colour():
    client, ops, _, world = harness(poller_running=False, fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(mode="static", color=(255, 0, 0)),
            state(mode="static", color=(0, 255, 0)))
    assert world["ring"][0] == "static"
    assert world["ring"][1] == (255, 0, 0)


def test_rolling_back_to_thermal_restarts_the_poller_rather_than_sending():
    """There is nothing to re-send when the poller owned the ring before --
    restarting it IS the undo, and set_mode would refuse 'thermal' anyway."""
    client, ops, log, world = harness(poller_running=True, fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(mode="thermal"), state(mode="static"))
    assert world["running"] is True
    assert [e for e in log if e[0] == "set_ring"] == [("set_ring", "static")]


def test_a_partial_apply_names_what_was_rolled_back():
    client, ops, _, _ = harness(fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply) as exc:
        run(client, ops, state(), state(mode="static"))
    assert any(s.status == "rolled back" for s in exc.value.stages)


def test_the_ring_state_file_is_written_only_on_full_success():
    client, ops, _, world = harness()
    run(client, ops, state(), state(mode="static", color=(1, 2, 3)))
    assert world["ring_state"] == ("static", (1, 2, 3), 4)


def test_the_ring_state_file_is_not_written_when_a_stage_failed():
    client, ops, _, world = harness(fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(), state(mode="static"))
    assert world["ring_state"] is None


def test_a_conflict_aborts_before_any_lighting_stage_runs():
    """A conflict must not leave the poller stopped. The check therefore
    happens before stage 1, not inside apply_templates where it used to be the
    only check."""
    client, ops, log, world = harness()
    with pytest.raises(apply_mod.ConflictError):
        run(client, ops, state(), state(mode="static"),
            base_hash="a-hash-from-another-lifetime")
    assert world["running"] is True
    assert not any(e[0] in ("stop_poller", "set_ring", "write_thermal")
                   for e in log)


def test_invalid_lighting_is_refused_before_anything_is_sent():
    client, ops, log, _ = harness()
    with pytest.raises(apply_mod.ApplyFailed, match="cool"):
        run(client, ops, state(),
            state(thermal=ThermalConfig(cool_c=90.0, hot_c=50.0)))
    assert log == []


def test_a_live_id_outside_the_set_is_refused_before_anything_is_sent():
    client, ops, log, _ = harness()
    with pytest.raises(apply_mod.ApplyFailed, match="not in the set"):
        apply_mod.apply_all(client, templates=TEMPLATES, live_id="ghost",
                            base_lighting=state(), draft_lighting=state(),
                            device_id=LCD_BARE_ID, ops=ops)
    assert log == []


def test_a_lighting_warning_does_not_block_the_apply():
    """poll_ms below the floor is a bad idea, not an impossible one. Blocking
    on warnings would make the app refuse settings the poller accepts."""
    client, ops, _, world = harness()
    run(client, ops, state(), state(thermal=ThermalConfig(poll_ms=50)))
    assert world["thermal"].poll_ms == 50
