"""Applying templates to the panel.

FOUR HAZARDS, all previously hit by hand-written scripts:

1. SetLcdTemplates REPLACES THE ENTIRE STORED SET. The existing apply.sh sends
   only [gaming-dash], which is harmless with one template and silently deletes
   every other one the moment there are two. Always send the whole library.

2. SetLcdTemplates ALONE DOES NOT UPDATE THE PANEL. It replaces the stored
   template while the live renderer keeps what it last prepared. SetLcdMedia
   must follow to force a re-prepare. These are one code path here so the first
   cannot be called without the second.

3. lianli-gui WIPES THE lcds ARRAY every time it writes config, because it
   cannot represent template mode. The entry is restored from a caller-supplied
   known-good copy -- never invented, because a wrong orientation or serial
   would render sideways or not at all.

4. SetLcdMedia's device_id parameter is NOT the id ListDevices reports. It is
   matched against LcdConfig::device_id(), which is "serial:<serial>". Passing
   the bare "hid:..." never matches, so the daemon APPENDS a second lcds entry
   rather than replacing the first. Nothing errors: the reply is status ok, and
   the appended entry is even briefly visible over GetConfig. The next config
   load then collapses duplicate serials keeping the FIRST -- the stale one --
   so the template switch is silently discarded and the panel keeps rendering
   the old template. entry_key() is the only place this format is built.

The two calls are not atomic. If SetLcdMedia fails, the stored set has moved on
while the panel still shows the old frame, so the previous set is restored.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Callable

from .ipc import DaemonError

LCD_SERIAL = "hid:513b5a7acadc4203"


class ConflictError(Exception):
    """The stored template set changed since this draft was read."""


class ApplyFailed(Exception):
    """The apply did not complete; the panel was left unchanged."""


def lcd_entry_fallback(root=None) -> dict | None:
    """Newest snapshotted config.lcds entry, for rebuilding a wiped array.

    Walks snapshots newest-first because the most recent one may itself have
    been taken while the array was empty -- lianli-gui wipes it on every config
    write. Returns None if no snapshot ever recorded an entry, in which case
    apply_templates raises rather than inventing an orientation and serial.
    """
    from . import snapshot
    root = root or snapshot.SNAPSHOT_ROOT
    if not root.exists():
        return None
    for d in sorted((p for p in root.iterdir() if p.is_dir()),
                    key=lambda p: p.name, reverse=True):
        try:
            entries = snapshot.load(d).get("lcds") or []
        except (OSError, ValueError):
            continue
        if entries:
            return entries[0]
    return None


def templates_hash(templates: list[dict]) -> str:
    """Order-sensitive digest. Widget and template order are both meaningful."""
    blob = json.dumps(templates, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def read_templates(client) -> tuple[list[dict], str]:
    templates = client.call("GetLcdTemplates") or []
    return templates, templates_hash(templates)


def find_lcd(client) -> str:
    """Resolve the LCD's device id. Its serial is stable across replugs, unlike
    the LED ring's, which is derived from the USB path."""
    for dev in client.call("ListDevices") or []:
        if dev.get("has_lcd"):
            return dev["device_id"]
    raise ApplyFailed("no LCD device found; is the screen plugged in?")


def entry_key(entry: dict) -> str:
    """The daemon's own key for a config.lcds entry.

    Mirrors LcdConfig::device_id() in lianli-shared: "serial:<serial>", else
    "index:<n>". SetLcdMedia's device_id parameter is matched against THIS, not
    against the bare id ListDevices reports -- see apply_templates.
    """
    serial = entry.get("serial")
    if serial:
        return f"serial:{serial}"
    index = entry.get("index")
    if index is not None:
        return f"index:{index}"
    return "unknown"


def live_template_id(config: dict, serial: str) -> str | None:
    """The template_id of the entry for THIS panel.

    NOT lcds[0]. The array can hold entries for other LCDs, and this daemon has
    been observed carrying two entries for the SAME serial for a day at a time
    (hazard 4 above). Reading the first entry is right only when there is
    exactly one LCD and no duplicate -- and the duplicate case is precisely the
    one where a wrong answer matters.

    When the serial appears more than once, the FIRST match wins, because that
    is what AppConfig::load does when it collapses duplicates. Mirroring the
    daemon's own rule is the point: this must report what the panel renders,
    not what the config wishes it rendered.

    Returns None when no entry matches -- which is the lianli-gui-wiped case,
    and is reported as "no entry for this panel" rather than papered over with
    another device's template.
    """
    for entry in config.get("lcds") or []:
        if entry.get("serial") == serial:
            return entry.get("template_id")
    return None


def _lcd_entry(client, device_id: str, fallback: dict | None) -> dict:
    config = client.call("GetConfig") or {}
    for entry in config.get("lcds") or []:
        if entry.get("serial") == device_id:
            return dict(entry)
    if fallback is None:
        raise ApplyFailed(
            f"no LCD entry for {device_id} in config.lcds and no known-good "
            "fallback was supplied. lianli-gui wipes this array; restore it from "
            "a saved copy rather than guessing orientation and serial.")
    return dict(fallback)


def apply_templates(client, templates: list[dict], live_id: str, *,
                    base_hash: str | None = None,
                    device_id: str | None = None,
                    lcd_entry_fallback: dict | None = None,
                    brightness: int | None = None) -> None:
    if not any(t.get("id") == live_id for t in templates):
        raise ApplyFailed(f"live template {live_id!r} is not in the set being sent")

    device_id = device_id or find_lcd(client)

    previous, current_hash = read_templates(client)
    if base_hash is not None and current_hash != base_hash:
        raise ConflictError(
            "the daemon's template set changed since this draft was opened — "
            "another process (apply.sh, lianli-gui, or a second editor) wrote to "
            "it. Applying now would discard that change.")

    entry = _lcd_entry(client, device_id, lcd_entry_fallback)
    entry["type"] = "custom"
    entry["template_id"] = live_id
    if brightness is not None:
        # The PERSISTENT half of brightness. LcdConfig.brightness is applied at
        # target creation, so this is what survives a daemon restart;
        # SetLcdBrightness is only the "apply it now" half and persists
        # nothing. Riding the entry SetLcdMedia already sends means brightness
        # inherits a write path that is verified rather than inventing a
        # second one.
        entry["brightness"] = int(brightness)

    client.call("SetLcdTemplates", {"templates": templates})
    try:
        client.call("SetLcdMedia",
                    {"device_id": entry_key(entry), "config": entry})
    except DaemonError as exc:
        try:
            client.call("SetLcdTemplates", {"templates": previous})
        except DaemonError as rollback_exc:
            raise ApplyFailed(
                f"SetLcdMedia failed ({exc}) AND the rollback failed "
                f"({rollback_exc}). The stored template set may be inconsistent; "
                "re-apply from a snapshot.") from exc
        raise ApplyFailed(
            f"SetLcdMedia failed ({exc}); the previous template set was restored "
            "and the panel is unchanged.") from exc


@dataclass
class StageResult:
    """What one stage of a unified apply did.

    status is one of:
      done          it happened and the result can be read back
      unverifiable  it was sent and the daemon cannot tell us whether it landed
      skipped       nothing to do
      rolled back   it happened, a later stage failed, and it was undone
      failed        it did not happen, or its rollback did not
    """
    name: str
    status: str
    detail: str = ""


class PartialApply(ApplyFailed):
    """An apply stopped partway. `stages` says exactly how far it got."""

    def __init__(self, message: str, stages: list[StageResult]) -> None:
        super().__init__(message)
        self.stages = stages


@dataclass
class LightingOps:
    """Every side effect apply_all can have, injected.

    This exists so the ordering rules -- which are the whole point of the
    staged design -- can be tested without systemd, a daemon, or a filesystem.
    """
    read_thermal: Callable[[], Any]
    write_thermal: Callable[[Any], None]
    poller_active: Callable[[], bool]
    stop_poller: Callable[[], None]
    start_poller: Callable[[], None]
    set_ring: Callable[[Any, str, tuple, int], None]
    set_lcd_brightness: Callable[[Any, str, int], None]
    save_ring_state: Callable[[str, tuple, int], None]


def default_ops() -> LightingOps:
    from . import ring
    return LightingOps(
        read_thermal=ring.load_thermal,
        write_thermal=ring.save_thermal,
        poller_active=ring.poller_active,
        stop_poller=ring.stop_poller,
        start_poller=ring.start_poller,
        set_ring=ring.set_mode,
        set_lcd_brightness=ring.set_lcd_brightness,
        save_ring_state=lambda mode, color, brightness: ring.save_ring_state(
            ring.RingState(mode=mode, color=color, brightness=brightness)),
    )


def apply_all(client, *, templates: list[dict], live_id: str,
              base_lighting, draft_lighting,
              base_hash: str | None = None,
              device_id: str | None = None,
              lcd_entry_fallback: dict | None = None,
              ops: LightingOps | None = None) -> list[StageResult]:
    """Commit templates and lighting as one staged transaction.

    ORDER IS THE DESIGN. Stages run cheap-and-reversible first and
    dangerous-and-verified last, so that:

      * nothing touches the template set until every lighting stage has already
        succeeded -- the template set is the only thing here whose rollback is
        itself a whole-set write;
      * the poller is stopped BEFORE the ring is driven, or it overwrites the
        colour within ~2s and the user watches their choice disappear;
      * the poller's config is written BEFORE it is stopped or started, so it
        never gets a cycle with settings the user has already changed.

    Two stages are honest about not being verifiable, rather than reporting
    success they cannot support: the ring has no read-back at all, and
    SetLcdBrightness replies ok before it touches the device.
    """
    from . import lighting as lighting_mod

    ops = ops or default_ops()

    if not any(t.get("id") == live_id for t in templates):
        raise ApplyFailed(f"live template {live_id!r} is not in the set being sent")

    errors = [p for p in lighting_mod.problems(draft_lighting)
              if p.level == "error"]
    if errors:
        raise ApplyFailed("the lighting settings are invalid: "
                          + "; ".join(f"{p.field}: {p.message}" for p in errors))

    device_id = device_id or find_lcd(client)

    # BEFORE stage 1, not inside apply_templates. A conflict must not leave the
    # poller stopped and the ring driven by an apply that then refuses to run.
    _, current_hash = read_templates(client)
    if base_hash is not None and current_hash != base_hash:
        raise ConflictError(
            "the daemon's template set changed since this draft was opened — "
            "another process (apply.sh, lianli-gui, or a second editor) wrote to "
            "it. Applying now would discard that change.")

    plan = lighting_mod.diff(base_lighting, draft_lighting,
                             poller_running=ops.poller_active())

    stages: list[StageResult] = []
    undo: list[tuple[str, Callable[[], None]]] = []

    def unwind() -> None:
        for name, action in reversed(undo):
            try:
                action()
            except Exception as exc:
                stages.append(StageResult(name, "failed",
                                          f"rollback failed: {exc}"))
            else:
                stages.append(StageResult(name, "rolled back"))

    # --- L1: the poller's config file --------------------------------------
    if plan.poller_config:
        previous_thermal = ops.read_thermal()
        try:
            ops.write_thermal(draft_lighting.thermal)
        except Exception as exc:
            stages.append(StageResult("poller config", "failed", str(exc)))
            raise PartialApply(
                f"could not write the poller's config ({exc}). Nothing else was "
                "changed and the template set was not touched.", stages)
        stages.append(StageResult("poller config", "done"))
        undo.append(("poller config",
                     lambda: ops.write_thermal(previous_thermal)))
    else:
        stages.append(StageResult("poller config", "skipped"))

    # --- L2: the poller's unit ---------------------------------------------
    if plan.unit_action:
        forward = ops.stop_poller if plan.unit_action == "stop" else ops.start_poller
        inverse = ops.start_poller if plan.unit_action == "stop" else ops.stop_poller
        try:
            forward()
        except Exception as exc:
            stages.append(StageResult("poller unit", "failed", str(exc)))
            unwind()
            raise PartialApply(
                f"could not {plan.unit_action} the thermal poller ({exc}). The "
                "template set was not touched.", stages)
        stages.append(StageResult("poller unit", "done", plan.unit_action))
        undo.append(("poller unit", inverse))
    else:
        stages.append(StageResult("poller unit", "skipped"))

    # --- L3: the ring itself ------------------------------------------------
    if plan.ring_effect:
        try:
            ops.set_ring(client, draft_lighting.mode,
                         tuple(draft_lighting.color),
                         draft_lighting.ring_brightness)
        except Exception as exc:
            stages.append(StageResult("ring effect", "failed", str(exc)))
            unwind()
            raise PartialApply(
                f"the ring rejected the effect ({exc}). The template set was "
                "not touched.", stages)
        stages.append(StageResult(
            "ring effect", "done",
            "sent — the ring has no read-back, so this is what was sent, not "
            "proof of what is lit"))
        if base_lighting.mode in ("static", "off"):
            undo.append(("ring effect", lambda: ops.set_ring(
                client, base_lighting.mode, tuple(base_lighting.color),
                base_lighting.ring_brightness)))
        # If the previous mode was thermal there is nothing to re-send: the
        # poller owned the ring, and L2's undo (restarting it) IS the rollback.
    else:
        stages.append(StageResult("ring effect", "skipped"))

    # --- T: the template set, and the persistent half of brightness ---------
    try:
        apply_templates(client, templates, live_id, base_hash=base_hash,
                        device_id=device_id,
                        lcd_entry_fallback=lcd_entry_fallback,
                        brightness=draft_lighting.screen_brightness)
    except (ApplyFailed, ConflictError) as exc:
        stages.append(StageResult("templates", "failed", str(exc)))
        unwind()
        # Deliberately NOT re-raised as ConflictError: by now the poller may be
        # stopped and the ring driven, so the caller must be told what was
        # rolled back rather than offered a naive "overwrite anyway?" retry.
        raise PartialApply(
            f"{exc} The lighting changes made before it were rolled back.",
            stages)
    stages.append(StageResult("templates", "done"))

    # --- B: make brightness take effect now ---------------------------------
    if plan.screen_brightness:
        try:
            ops.set_lcd_brightness(client, device_id,
                                   draft_lighting.screen_brightness)
        except Exception as exc:
            stages.append(StageResult("screen brightness", "failed", str(exc)))
        else:
            stages.append(StageResult(
                "screen brightness", "unverifiable",
                "SetLcdBrightness replies ok before it touches the device and "
                "has no read-back, so this is not evidence. The value was "
                "persisted in config.lcds[].brightness by the stage above, so "
                "it is correct after any daemon restart regardless."))
    else:
        stages.append(StageResult("screen brightness", "skipped"))

    # Deliberately last, and deliberately not rolled back: this is bookkeeping
    # about what was sent, and it is only true once everything above succeeded.
    try:
        ops.save_ring_state(draft_lighting.mode, tuple(draft_lighting.color),
                            draft_lighting.ring_brightness)
    except Exception as exc:
        stages.append(StageResult("ring state file", "failed", str(exc)))

    return stages
