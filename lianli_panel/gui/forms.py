"""Turning the extracted schema into inspector fields.

schema.py's `required` tuples are AUTHORITATIVE -- serde reports them. Its
`observed_optional` tuples are NOT exhaustive: the daemon silently ignores
unknown fields, so no probe can enumerate the optional ones. The form therefore
shows three things, in this order:

  1. every required field of the variant   (from the schema)
  2. every optional field ever observed    (from the schema)
  3. every field actually on this widget   (so a key the schema never saw stays
                                            editable rather than becoming dead
                                            weight that only round-trips)

Range thresholds are shown and typed in REAL UNITS. The stored value is a
percentage of the widget's own value_min..value_max span, and getting that
backwards renders plausible, wrong colours with no error anywhere.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..model import (Widget, range_thresholds_raw, set_range_threshold_raw,
                     widget_span)
from ..schema import SOURCE_TYPES, WIDGET_KINDS

# Handled by dedicated UI, never as a generic row.
SPECIAL = ("type", "source", "ranges")

# Sane starting values when switching to a variant that requires a field the
# old one did not have. Anything absent falls back to 0.0.
_DEFAULTS: dict[str, Any] = {
    "text": "", "font_size": 40.0, "color": [255, 255, 255, 255],
    "background_color": [0, 0, 0, 0], "needle_color": [255, 80, 80, 255],
    "tick_color": [140, 140, 140, 255], "value_min": 0.0, "value_max": 100.0,
    "start_angle": 135.0, "sweep_angle": 270.0, "path": "",
    "source": {"type": "constant", "value": 0.0},
    "name": "", "label": "", "iface": "", "device": "", "device_id": "",
    "cmd": "", "value": 0.0,
}

NOTE_UNSCHEMAD = ("not in the extracted schema; present on this widget and "
                  "preserved on save")


@dataclass
class FieldSpec:
    name: str
    kind: str            # number | text | bool | color | font | json
    required: bool
    value: Any
    note: str = ""


@dataclass
class Change:
    dropped: dict[str, Any] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)


@dataclass
class RangeRow:
    index: int
    threshold: float | None      # REAL units; None is the catch-all
    color: list[int]
    alpha: int | None
    unit: str


def _field_kind(name: str, value: Any) -> str:
    if name == "font" or (isinstance(value, dict) and "path" in value):
        return "font"
    if name.endswith("color") or (
            isinstance(value, list) and 3 <= len(value) <= 4
            and all(isinstance(c, (int, float)) and not isinstance(c, bool)
                    for c in value)):
        return "color"
    if isinstance(value, bool):          # before the number check: bool IS int
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if value is None or isinstance(value, str):
        return "text"
    return "json"


def _fields(obj: dict, spec, defaults_from_required: bool) -> list[FieldSpec]:
    names: list[str] = []
    if spec is not None:
        names = [n for n in spec.required if n not in SPECIAL]
        names += [n for n in spec.observed_optional
                  if n not in SPECIAL and n not in names]
    for n in obj:
        if n not in SPECIAL and n not in names:
            names.append(n)
    known = set(spec.required) | set(spec.observed_optional) if spec else set()
    out: list[FieldSpec] = []
    for n in names:
        value = obj.get(n, copy.deepcopy(_DEFAULTS.get(n, 0.0))
                        if defaults_from_required else None)
        out.append(FieldSpec(
            name=n, kind=_field_kind(n, value),
            required=bool(spec and n in spec.required), value=value,
            note="" if n in known else NOTE_UNSCHEMAD))
    return out


def kind_fields(w: Widget) -> list[FieldSpec]:
    return _fields(w.kind, WIDGET_KINDS.get(w.kind_type), True)


def source_fields(w: Widget) -> list[FieldSpec]:
    src = w.source or {}
    return _fields(src, SOURCE_TYPES.get(src.get("type", "")), True)


def is_unknown_kind(w: Widget) -> bool:
    return w.kind_type not in WIDGET_KINDS


def is_unknown_source(w: Widget) -> bool:
    src = w.source
    return src is not None and src.get("type") not in SOURCE_TYPES


def _switch(obj: dict, new_type: str, spec) -> tuple[dict, Change]:
    if spec is None:                     # unknown target: keep everything
        return {**obj, "type": new_type}, Change()
    known = set(spec.required) | set(spec.observed_optional)
    carried = {k: v for k, v in obj.items() if k != "type" and k in known}
    dropped = {k: v for k, v in obj.items() if k != "type" and k not in known}
    out = {"type": new_type, **carried}
    added: list[str] = []
    for n in spec.required:
        if n not in out:
            out[n] = copy.deepcopy(_DEFAULTS.get(n, 0.0))
            added.append(n)
    return out, Change(dropped, added)


def change_kind(w: Widget, new_type: str) -> Change:
    """Fields the new variant does not know are dropped -- but REPORTED, so the
    UI can show what it is about to lose instead of losing it silently."""
    w.kind, change = _switch(w.kind, new_type, WIDGET_KINDS.get(new_type))
    return change


def change_source(w: Widget, new_type: str) -> Change:
    src = w.source or {}
    new_src, change = _switch(src, new_type, SOURCE_TYPES.get(new_type))
    w.kind["source"] = new_src
    return change


# --- ranges, in real units -------------------------------------------------


def _entries(w: Widget) -> list[dict]:
    r = w.kind.get("ranges")
    if not isinstance(r, list):
        r = []
        w.kind["ranges"] = r
    return r


def range_rows(w: Widget) -> list[RangeRow]:
    unit = w.kind.get("unit") or ""
    thresholds = range_thresholds_raw(w)
    rows: list[RangeRow] = []
    for i, entry in enumerate(_entries(w)):
        rows.append(RangeRow(
            index=i,
            threshold=thresholds[i] if i < len(thresholds) else None,
            color=list(entry.get("color") or [255, 255, 255]),
            alpha=entry.get("alpha"), unit=str(unit)))
    return rows


def set_threshold(w: Widget, index: int, raw: float | None) -> bool:
    """Returns False and writes NOTHING when the value did not change.

    Re-encoding an untouched threshold would drift its stored float on every
    save -- a percentage the user never typed, changing on its own.
    """
    current = range_thresholds_raw(w)
    if index < len(current):
        both_null = current[index] is None and raw is None
        if both_null or (current[index] is not None and raw is not None
                         and abs(current[index] - raw) < 1e-9):
            return False
    set_range_threshold_raw(w, index, raw)
    return True


@dataclass
class SpanChange:
    rewritten: list[int] = field(default_factory=list)
    clamped: list[int] = field(default_factory=list)


def set_span(w: Widget, value_min: float, value_max: float) -> SpanChange:
    """Move value_min/value_max and hold the REAL thresholds still.

    Stored range maxima are percentages OF THIS SPAN, so writing value_max
    straight into the dict moves every colour boundary in real terms while no
    visible field changes. The spec settles the direction: raw stays fixed,
    because the user typed degrees and means degrees. So the percentages are
    re-encoded around the new span and the real numbers stay put.

    raw_to_pct CLAMPS to [0,100]. Narrowing a span past a threshold therefore
    cannot preserve it -- that threshold collapses onto an endpoint and the
    index is reported in `clamped`, so the UI can say the value is gone rather
    than let the user widen the span again and wonder why it did not come back.
    """
    new_min, new_max = float(value_min), float(value_max)
    if widget_span(w) == (new_min, new_max):
        # Unchanged: re-encoding untouched percentages drifts their stored
        # floats on every save and breaks the lossless round trip.
        return SpanChange()

    before = range_thresholds_raw(w)     # real units, under the OLD span
    w.kind["value_min"] = new_min
    w.kind["value_max"] = new_max

    change = SpanChange()
    lo, hi = min(new_min, new_max), max(new_min, new_max)
    for i, raw in enumerate(before):
        if raw is None:                  # the catch-all has no threshold
            continue
        set_range_threshold_raw(w, i, raw)
        change.rewritten.append(i)
        if raw < lo or raw > hi:
            change.clamped.append(i)
    return change


def add_range(w: Widget, raw: float) -> int:
    """Inserted before the catch-all: a range after `max: null` is unreachable,
    because the first range whose max >= percentage wins."""
    entries = _entries(w)
    span = widget_span(w)
    if span is None:
        raise ValueError(f"widget {w.id!r} has no value_min/value_max span")
    at = next((i for i, e in enumerate(entries) if e.get("max") is None),
              len(entries))
    entries.insert(at, {"max": None, "color": [255, 255, 255], "alpha": 255})
    set_range_threshold_raw(w, at, raw)
    return at


def remove_range(w: Widget, index: int) -> None:
    entries = _entries(w)
    if not 0 <= index < len(entries):
        raise IndexError(f"widget {w.id!r} has no range at index {index}")
    del entries[index]
