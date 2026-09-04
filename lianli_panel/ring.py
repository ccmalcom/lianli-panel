"""LED ring control and the thermal poller's configuration.

TWO DAEMON BUGS SHAPE THIS MODULE:

1. SetConfig PERSISTS RGB SETTINGS BUT NEVER APPLIES THEM. Only SetRgbEffect
   reaches the hardware. This is why the vendor GUI's RGB page saves correctly,
   reads back correctly, and changes nothing. Always SetRgbEffect to apply.

2. The ring reports supported_modes ["Off","Static","Direct"] -- no hardware
   effects at all. So a rainbow on the ring always means NOTHING IS DRIVING IT;
   that is the firmware default, not a mode anyone selected.

The ring's device_id is derived from its USB path (hid:0416:8050:1-8.3) and
changes on every replug into a different port. The LCD's is a stable serial.
So the ring is resolved at runtime; a hardcoded id fails after any replug.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

RING_PID = 0x8050
THERMAL_CONFIG_PATH = Path("/var/lib/lianli-panel/thermal-rgb.json")

RGB_APPLY_WARNING = (
    "The thermal poller re-drives the ring every ~2s and will overwrite a "
    "static colour. Stop lianli-thermal-rgb.service first:\n"
    "  systemctl --user stop lianli-thermal-rgb.service"
)


def find_ring(client) -> str:
    for dev in client.call("ListDevices") or []:
        if dev.get("has_rgb") and dev.get("pid") == RING_PID:
            return dev["device_id"]
    raise RuntimeError("no LED ring found; is the screen plugged in?")


def _apply(client, effect: dict) -> None:
    full = {"speed": 2, "brightness": 4, "direction": "Clockwise",
            "scope": "All", "disabled": False, **effect}
    client.call("SetRgbEffect",
                {"device_id": find_ring(client), "zone": 0, "effect": full})


def set_static(client, rgb: tuple[int, int, int], brightness: int = 4) -> None:
    if not all(0 <= int(c) <= 255 for c in rgb):
        raise ValueError(f"colour components must be 0-255, got {rgb!r}")
    _apply(client, {"mode": "Static", "colors": [[int(c) for c in rgb]],
                    "brightness": brightness})


def set_off(client) -> None:
    _apply(client, {"mode": "Off", "colors": [[0, 0, 0]]})


# --- thermal poller config -------------------------------------------------


@dataclass
class ThermalConfig:
    """Defaults are the poller's current module-level constants, so the poller
    behaves identically when this file is absent."""
    cool_c: float = 45.0
    hot_c: float = 85.0
    poll_ms: int = 2000
    min_delta_c: float = 1.0
    force_refresh_s: int = 60
    brightness: int = 4

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, obj: dict) -> "ThermalConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in obj.items() if k in known})


def load_thermal(path: Path | None = None) -> ThermalConfig:
    path = Path(path) if path is not None else THERMAL_CONFIG_PATH
    try:
        return ThermalConfig.from_json(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, TypeError):
        return ThermalConfig()


def save_thermal(cfg: ThermalConfig, path: Path | None = None) -> None:
    if cfg.cool_c >= cfg.hot_c:
        raise ValueError(
            f"cool_c ({cfg.cool_c}) must be below hot_c ({cfg.hot_c}); the hue "
            "sweep runs from green at cool to red at hot")
    path = Path(path) if path is not None else THERMAL_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.to_json(), indent=1))
