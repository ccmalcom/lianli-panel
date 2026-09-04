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
