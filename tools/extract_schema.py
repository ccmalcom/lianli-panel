#!/usr/bin/env python3
"""Extract the daemon's template schema by probing its serde error messages.

Two mechanisms, with different completeness guarantees:

  Required fields  -- authoritative. Send a variant with no fields and read
                      `missing field \\`x\\``; add a placeholder for x; repeat
                      until the render succeeds. The loop terminates because
                      each pass fixes exactly one field.

  Optional fields  -- NOT exhaustive. The daemon ignores unknown fields, so
                      there is no way to ask "what else may I send?". These are
                      harvested from templates already stored on the daemon and
                      labelled as observed, not complete.

Run:  ./.venv/bin/python tools/extract_schema.py > lianli_panel/schema.py
"""
from __future__ import annotations

import json
import re
import sys

from lianli_panel.ipc import Client, DaemonError

KINDS = ("label", "value_text", "radial_gauge", "vertical_bar", "horizontal_bar",
         "speedometer", "core_bars", "image", "video", "sparkline",
         "clock_digital", "clock_analog")
SOURCES = ("constant", "command", "hwmon", "nvidia_gpu", "amd_gpu_usage",
           "wireless_coolant", "cpu_usage", "mem_usage", "mem_used", "mem_free",
           "network_rx", "network_tx", "disk_read", "disk_write")

FONT = "/usr/share/fonts/google-noto/NotoSansMono-Bold.ttf"
MISSING = re.compile(r"missing field `([^`]+)`")

# Placeholders by field name. The daemon TYPE-CHECKS, so a wrong type produces
# "invalid type" rather than "missing field" -- which the loop cannot act on, so
# it stalls and returns a PARTIAL field list. That failure is silent unless the
# extractor exits nonzero, which is why it does.
#
# The bare-float default is only safe for genuinely numeric fields. Anything
# taking a string, bool, integer or colour array needs an entry here. The list
# below covers every non-float required field found in the daemon's serde
# variants; extend it rather than letting the default absorb a new one.
PLACEHOLDERS = {
    # nested objects
    "source": {"type": "constant", "value": 1.0},
    "font": {"path": FONT},
    "ranges": [{"max": None, "color": [255, 255, 255], "alpha": 255}],
    # colour arrays
    "color": [255, 255, 255, 255],
    "background_color": [0, 0, 0, 0],
    "gauge_background_color": [60, 60, 60],
    "line_color": [255, 255, 255],
    "fill_color": [255, 255, 255, 80],
    "border_color": [255, 255, 255],
    "needle_color": [255, 0, 0],
    "needle_border_color": [0, 0, 0],
    "tick_color": [200, 200, 200],
    "face_color": [20, 20, 20],
    "hour_hand_color": [255, 255, 255],
    "minute_hand_color": [255, 255, 255],
    "second_hand_color": [255, 0, 0],
    "colors": [[255, 255, 255]],
    # strings
    "text": "x",
    "format": "{:.0}",
    "unit": "",
    "align": "center",
    "path": FONT,
    "cmd": "echo 1",
    "name": "coretemp",
    "label": "x",
    "metric": "temp",            # nvidia_gpu enum
    "iface": "lo",               # network_rx / network_tx
    "device": "sda",             # disk_read / disk_write
    "device_id": "hid:probe",    # wireless_coolant
    "fit": "contain",            # image / video enum
    # integers
    "gpu_index": 0,
    "card_index": 0,
    "tick_count": 8,
    "history_length": 60,
    # booleans
    "loop_playback": False,
    "show_labels": False,
    "auto_range": False,
    "show_gauge": True,
    "show_needle": True,
    "show_seconds": True,
    # floats
    "value": 1.0,
}
NUMERIC_DEFAULT = 1.0


def envelope(widget_kind: dict) -> dict:
    return {
        "id": "probe", "name": "probe",
        "base_width": 1920, "base_height": 480, "rotated": True,
        "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "widgets": [{"id": "w", "x": 100.0, "y": 100.0,
                     "width": 100.0, "height": 100.0, "kind": widget_kind}],
    }


STALLED: list[str] = []


def required_fields(client: Client, build, label: str) -> tuple[str, ...]:
    """Add placeholders until the template validates. Returns fields in order.

    A stall means a placeholder had the WRONG TYPE: the daemon answered
    "invalid type" instead of "missing field", which the loop cannot act on, so
    the field list here is partial. Recorded so main() can exit nonzero -- a
    partial schema that looks successful is worse than no schema.
    """
    found: list[str] = []
    for _ in range(60):
        try:
            client.call("RenderTemplatePreview",
                        {"template": envelope(build(found)), "width": 1920, "height": 480})
            return tuple(found)
        except DaemonError as exc:
            m = MISSING.search(str(exc))
            if not m:
                STALLED.append(f"{label}: {exc} (found so far: {found})")
                print(f"  STALLED {label}: {exc}", file=sys.stderr)
                return tuple(found)
            field = m.group(1)
            if field in found:
                STALLED.append(f"{label}: repeated {field} — placeholder rejected")
                print(f"  STALLED {label}: repeated {field}: {exc}", file=sys.stderr)
                return tuple(found)
            found.append(field)
    STALLED.append(f"{label}: exceeded 60 iterations")
    return tuple(found)


def fill(kind_type: str, fields: list[str]) -> dict:
    out: dict = {"type": kind_type}
    for f in fields:
        out[f] = PLACEHOLDERS.get(f, NUMERIC_DEFAULT)
    return out


def observed_optional(client: Client) -> tuple[dict, dict]:
    """Harvest field names actually present on stored templates."""
    kinds: dict[str, set] = {}
    sources: dict[str, set] = {}
    for tpl in client.call("GetLcdTemplates") or []:
        for w in tpl.get("widgets", []):
            k = w.get("kind") or {}
            if isinstance(k, dict) and "type" in k:
                kinds.setdefault(k["type"], set()).update(x for x in k if x != "type")
                s = k.get("source")
                if isinstance(s, dict) and "type" in s:
                    sources.setdefault(s["type"], set()).update(x for x in s if x != "type")
    return kinds, sources


def main() -> None:
    client = Client()
    seen_kinds, seen_sources = observed_optional(client)

    kind_specs = {}
    for kind in KINDS:
        print(f"probing kind {kind}", file=sys.stderr)
        req = required_fields(client, lambda fs, k=kind: fill(k, fs), f"kind {kind}")
        opt = tuple(sorted(seen_kinds.get(kind, set()) - set(req)))
        kind_specs[kind] = (req, opt)

    src_specs = {}
    for src in SOURCES:
        print(f"probing source {src}", file=sys.stderr)

        def build(fs, s=src):
            source = {"type": s}
            for f in fs:
                source[f] = PLACEHOLDERS.get(f, NUMERIC_DEFAULT)
            return {"type": "value_text", "source": source, "format": "{:.0}",
                    "unit": "", "font": {"path": FONT}, "font_size": 20.0,
                    "color": [255, 255, 255, 255], "align": "center",
                    "value_min": 0.0, "value_max": 100.0,
                    "ranges": [{"max": None, "color": [255, 255, 255], "alpha": 255}],
                    "letter_spacing": 0.0}

        # The outer value_text fields are already satisfied, so any reported
        # missing field belongs to the source being probed.
        req = required_fields(client, build, f"source {src}")
        opt = tuple(sorted(seen_sources.get(src, set()) - set(req)))
        src_specs[src] = (req, opt)

    if STALLED:
        print(f"\n{len(STALLED)} variant(s) STALLED — the schema is PARTIAL:",
              file=sys.stderr)
        for s in STALLED:
            print(f"  {s}", file=sys.stderr)
        print("\nAdd a correctly-typed entry to PLACEHOLDERS for each field named "
              "above and re-run. Do NOT commit a partial schema.", file=sys.stderr)
        raise SystemExit(1)

    emit(kind_specs, src_specs)


def emit(kind_specs: dict, src_specs: dict) -> None:
    def block(specs: dict) -> str:
        lines = []
        for name, (req, opt) in specs.items():
            lines.append(f"    {name!r}: VariantSpec({name!r}, {req!r}, {opt!r}),")
        return "\n".join(lines)

    print('"""Daemon template schema. GENERATED by tools/extract_schema.py.')
    print()
    print("Required fields are authoritative (serde reports them).")
    print("observed_optional is NOT exhaustive: the daemon silently ignores")
    print("unknown fields, so there is no way to enumerate optional ones.")
    print('Regenerate after a daemon upgrade; do not hand-edit.')
    print('"""')
    print("from __future__ import annotations")
    print()
    print("from dataclasses import dataclass")
    print()
    print()
    print("@dataclass(frozen=True)")
    print("class VariantSpec:")
    print("    name: str")
    print("    required: tuple[str, ...]")
    print("    observed_optional: tuple[str, ...]")
    print()
    print()
    print("WIDGET_KINDS: dict[str, VariantSpec] = {")
    print(block(kind_specs))
    print("}")
    print()
    print("SOURCE_TYPES: dict[str, VariantSpec] = {")
    print(block(src_specs))
    print("}")
    print()
    print("KIND_NAMES = tuple(WIDGET_KINDS)")
    print("SOURCE_NAMES = tuple(SOURCE_TYPES)")


if __name__ == "__main__":
    main()
