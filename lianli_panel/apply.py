"""Applying templates to the panel.

THREE HAZARDS, all previously hit by hand-written scripts:

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

The two calls are not atomic. If SetLcdMedia fails, the stored set has moved on
while the panel still shows the old frame, so the previous set is restored.
"""
from __future__ import annotations

import hashlib
import json

from .ipc import DaemonError

LCD_SERIAL = "hid:513b5a7acadc4203"


class ConflictError(Exception):
    """The stored template set changed since this draft was read."""


class ApplyFailed(Exception):
    """The apply did not complete; the panel was left unchanged."""


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
                    lcd_entry_fallback: dict | None = None) -> None:
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

    client.call("SetLcdTemplates", {"templates": templates})
    try:
        client.call("SetLcdMedia", {"device_id": device_id, "config": entry})
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
