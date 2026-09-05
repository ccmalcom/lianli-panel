"""Command line for the lianli-panel core.

A correct replacement for apply.sh and rgb.sh: it sends the whole template
library rather than one entry, always follows SetLcdTemplates with SetLcdMedia,
snapshots before applying, and reports whether the panel is actually reachable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import apply as apply_mod
from . import health, ring, sensors, snapshot
from .ipc import Client, DaemonError
from .model import Template, validate
from .render import PreviewRenderer


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lianli-panel")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="is the panel actually reachable?")
    sub.add_parser("list", help="list stored templates")
    sub.add_parser("snapshot", help="snapshot the current configured state")
    sub.add_parser("revert", help="restore the newest snapshot")

    v = sub.add_parser("validate", help="validate a template file or stored id")
    v.add_argument("target")

    a = sub.add_parser("apply", help="make a stored template live")
    a.add_argument("template_id")

    pv = sub.add_parser("preview", help="render a template to a JPEG")
    pv.add_argument("template_id")
    pv.add_argument("-o", "--out", default="/tmp/lianli-preview.jpg")
    pv.add_argument("--live", action="store_true",
                    help="execute command sources (spawns subprocesses as lianli)")

    st = sub.add_parser("sensor-test", help="probe a candidate command sensor")
    st.add_argument("command")
    st.add_argument("-o", "--out", default="/tmp/lianli-sensor.jpg")

    r = sub.add_parser("ring", help="control the LED ring")
    rsub = r.add_subparsers(dest="ring_cmd", required=True)
    rsub.add_parser("off")
    rs = rsub.add_parser("static")
    rs.add_argument("r", type=int)
    rs.add_argument("g", type=int)
    rs.add_argument("b", type=int)

    return p


def _stored(client, template_id: str) -> dict:
    for t in client.call("GetLcdTemplates") or []:
        if t.get("id") == template_id:
            return t
    raise SystemExit(f"no stored template with id {template_id!r}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = Client()

    try:
        if args.cmd == "status":
            h = health.check()
            print(("PANEL OK: " if h.ok else "PANEL PROBLEM: ") + h.reason)
            return 0 if h.ok else 1

        if args.cmd == "list":
            templates, digest = apply_mod.read_templates(client)
            config = client.call("GetConfig") or {}
            live = next((e.get("template_id") for e in config.get("lcds") or []), None)
            for t in templates:
                mark = "*" if t.get("id") == live else " "
                print(f"{mark} {t.get('id'):<20} {len(t.get('widgets', []))} widgets")
            print(f"\nset hash: {digest[:16]}")
            return 0

        if args.cmd == "validate":
            target = Path(args.target)
            raw = json.loads(target.read_text()) if target.exists() \
                else _stored(client, args.target)
            problems = validate(Template.from_json(raw))
            for p in problems:
                print(f"{p.level:8} {p.widget_id:14} {p.message}")
            print(f"{len(problems)} problem(s)")
            return 1 if any(p.level == "error" for p in problems) else 0

        if args.cmd == "apply":
            snap = snapshot.take(client)
            print(f"snapshot: {snap}")
            templates, digest = apply_mod.read_templates(client)
            # The snapshot just taken records config.lcds, so it is the natural
            # source for the fallback. Without this, apply fails outright once
            # lianli-gui has wiped the array -- the exact hazard apply.py claims
            # to handle. Prefer the newest snapshot that actually has an entry,
            # since the one just taken reflects the wiped state too.
            apply_mod.apply_templates(
                client, templates, args.template_id, base_hash=digest,
                lcd_entry_fallback=apply_mod.lcd_entry_fallback())
            h = health.check()
            print(f"applied {args.template_id}")
            print(("panel OK: " if h.ok else "WARNING: ") + h.reason)
            return 0 if h.ok else 1

        if args.cmd == "preview":
            tpl = _stored(client, args.template_id)
            jpeg = PreviewRenderer(client).render(tpl, live=args.live)
            Path(args.out).write_bytes(jpeg)
            mode = "live (commands executed)" if args.live else "substituted"
            print(f"wrote {args.out} ({len(jpeg)} bytes, {mode})")
            return 0

        if args.cmd == "sensor-test":
            d = sensors.run_diagnostic(args.command)
            print(f"-- diagnostic (runs as you, NOT authoritative) --")
            print(f"exit {d.exit_code}  parsed {d.parsed}")
            if d.stdout.strip():
                print(f"stdout: {d.stdout.strip()[:200]}")
            if d.stderr.strip():
                print(f"stderr: {d.stderr.strip()[:200]}")
            for p in d.problems:
                print(f"  ! {p}")
            jpeg = sensors.render_authoritative(client, args.command)
            Path(args.out).write_bytes(jpeg)
            print(f"-- authoritative: {args.out} — this is what the daemon reads --")
            return 0

        if args.cmd == "snapshot":
            print(snapshot.take(client))
            return 0

        if args.cmd == "revert":
            newest = snapshot.latest()
            if newest is None:
                print("no snapshots")
                return 1
            data = snapshot.load(newest)
            entry = next(iter(data.get("lcds") or []), None)
            if entry is None or entry.get("template_id") is None:
                print(f"snapshot {newest.name} records no live template")
                return 1
            live = entry["template_id"]
            # Restore the snapshotted LCD entry too, not just the templates --
            # orientation and serial live there, and reusing the CURRENT entry
            # would silently keep a wiped or edited one.
            apply_mod.apply_templates(client, data["templates"], live,
                                      lcd_entry_fallback=entry)
            print(f"restored templates and LCD entry from {newest.name} "
                  f"(live: {live})")
            # Say plainly what was NOT restored. Re-applying RGB here would
            # fight the thermal poller, which re-drives the ring every ~2s.
            print("NOT restored: RGB configuration, ring state, and the thermal "
                  "service's on/off state. Reverting those would be overwritten "
                  "by lianli-thermal-rgb.service within seconds; stop it first "
                  "and use `ring` if you need them back.")
            if data.get("thermal_service_active"):
                print(f"  (thermal service was active when {newest.name} "
                      "was taken)")
            return 0

        if args.cmd == "ring":
            if args.ring_cmd == "off":
                ring.set_off(client)
                print("ring off")
            else:
                ring.set_static(client, (args.r, args.g, args.b))
                print(f"ring static {args.r},{args.g},{args.b}")
            print(ring.RGB_APPLY_WARNING)
            return 0

    except (DaemonError, apply_mod.ApplyFailed, apply_mod.ConflictError,
            RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
