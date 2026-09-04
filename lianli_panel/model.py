"""Template model.

Round-trip fidelity is a correctness requirement, not a nicety: the daemon
silently ignores fields it does not recognise, so any key this model drops on
load is permanently deleted on the next save with no error anywhere.

Widget order IS draw order. Only the last widget covering a rect is visible,
which is how the cover-bar visibility trick works. Never reorder implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BASE_W, BASE_H = 1920, 480

_WIDGET_KEYS = ("id", "x", "y", "width", "height", "kind")
_TEMPLATE_KEYS = ("id", "name", "base_width", "base_height", "rotated",
                  "background", "widgets")


@dataclass
class Widget:
    id: str
    x: float
    y: float
    width: float
    height: float
    kind: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kind_type(self) -> str:
        return self.kind.get("type", "")

    @property
    def source(self) -> dict[str, Any] | None:
        src = self.kind.get("source")
        return src if isinstance(src, dict) else None

    @classmethod
    def from_json(cls, obj: dict) -> "Widget":
        return cls(
            id=obj["id"],
            x=obj["x"], y=obj["y"], width=obj["width"], height=obj["height"],
            kind=obj.get("kind") or {},
            extra={k: v for k, v in obj.items() if k not in _WIDGET_KEYS},
        )

    def to_json(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id, "x": self.x, "y": self.y,
            "width": self.width, "height": self.height, "kind": self.kind,
        }
        out.update(self.extra)
        return out


@dataclass
class Template:
    id: str
    name: str
    base_width: int
    base_height: int
    rotated: bool
    background: dict[str, Any]
    widgets: list[Widget]
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: dict) -> "Template":
        return cls(
            id=obj["id"], name=obj["name"],
            base_width=obj["base_width"], base_height=obj["base_height"],
            rotated=obj["rotated"], background=obj["background"],
            widgets=[Widget.from_json(w) for w in obj.get("widgets", [])],
            extra={k: v for k, v in obj.items() if k not in _TEMPLATE_KEYS},
        )

    def to_json(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id, "name": self.name,
            "base_width": self.base_width, "base_height": self.base_height,
            "rotated": self.rotated, "background": self.background,
            "widgets": [w.to_json() for w in self.widgets],
        }
        out.update(self.extra)
        return out

    def widget(self, widget_id: str) -> Widget | None:
        return next((w for w in self.widgets if w.id == widget_id), None)


# --- range conversion ------------------------------------------------------
#
# CONFIRMED BY DISASSEMBLY of the installed daemon:
#   unit = clamp((value - value_min) / (value_max - value_min), 0, 1)
#   percentage = unit * 100
# and range selection picks the FIRST range whose `max >= percentage`, with a
# null `max` acting as the fallback. So a range `max` is a percentage of the
# widget's own span, NEVER a raw sensor reading. A "60" on a 20..100 gauge
# means 68 degrees. Getting this wrong renders plausible, wrong colours with no
# error anywhere, which is why every UI field is in real units and converts here.


def raw_to_pct(raw: float, vmin: float, vmax: float) -> float:
    span = vmax - vmin
    if span == 0:
        return 0.0
    unit = (raw - vmin) / span
    return max(0.0, min(1.0, unit)) * 100.0


def pct_to_raw(pct: float, vmin: float, vmax: float) -> float:
    span = vmax - vmin
    if span == 0:
        return vmin
    return vmin + (pct / 100.0) * span


def widget_span(w: Widget) -> tuple[float, float] | None:
    lo, hi = w.kind.get("value_min"), w.kind.get("value_max")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        return float(lo), float(hi)
    return None


def _ranges(w: Widget) -> list[dict]:
    r = w.kind.get("ranges")
    return r if isinstance(r, list) else []


def range_thresholds_raw(w: Widget) -> list[float | None]:
    """Thresholds in real units. None marks the catch-all range."""
    span = widget_span(w)
    if span is None:
        return []
    lo, hi = span
    out: list[float | None] = []
    for entry in _ranges(w):
        m = entry.get("max")
        out.append(None if m is None else pct_to_raw(float(m), lo, hi))
    return out


def set_range_threshold_raw(w: Widget, index: int, raw: float | None) -> None:
    """Write one threshold back as a percentage.

    Only the named index is touched. Re-encoding untouched ranges would drift
    their stored floats on every save and break lossless round-tripping.
    """
    span = widget_span(w)
    if span is None:
        raise ValueError(f"widget {w.id!r} has no value_min/value_max span")
    lo, hi = span
    entries = _ranges(w)
    if not 0 <= index < len(entries):
        raise IndexError(f"widget {w.id!r} has no range at index {index}")
    entries[index]["max"] = None if raw is None else raw_to_pct(raw, lo, hi)


# --- validation ------------------------------------------------------------


@dataclass
class Problem:
    level: str  # "error" | "warning"
    widget_id: str
    message: str


def validate(t: Template) -> list[Problem]:
    problems: list[Problem] = []

    seen: set[str] = set()
    for w in t.widgets:
        if w.id in seen:
            problems.append(Problem("error", w.id, f"duplicate widget id {w.id!r}"))
        seen.add(w.id)

    for w in t.widgets:
        span = widget_span(w)
        if span is not None and span[0] > span[1]:
            problems.append(Problem(
                "error", w.id,
                f"value_min ({span[0]}) is greater than value_max ({span[1]})"))

        entries = _ranges(w)
        if not entries:
            continue

        maxima = [e.get("max") for e in entries]
        nulls = [i for i, m in enumerate(maxima) if m is None]

        if len(nulls) > 1:
            problems.append(Problem(
                "error", w.id,
                f"{len(nulls)} catch-all ranges (max: null); only the first is reachable"))
        elif not nulls:
            problems.append(Problem(
                "warning", w.id,
                "no catch-all range (max: null); values above the last threshold "
                "have no colour"))
        elif nulls[0] != len(maxima) - 1:
            problems.append(Problem(
                "error", w.id,
                f"catch-all range at index {nulls[0]} makes the "
                f"{len(maxima) - nulls[0] - 1} range(s) after it unreachable"))

        numeric = [m for m in maxima if m is not None]
        for m in numeric:
            if not 0.0 <= float(m) <= 100.0:
                problems.append(Problem(
                    "error", w.id,
                    f"range max {m} is outside 0..100 — it is a percentage of the "
                    f"widget's own span, not a raw reading"))
                break
        if numeric != sorted(numeric):
            problems.append(Problem(
                "error", w.id,
                "range maxima are not in ascending order; the first match wins, "
                "so later ranges are unreachable"))

    return problems
