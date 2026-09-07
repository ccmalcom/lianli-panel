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
import subprocess
from dataclasses import asdict, dataclass, fields
from datetime import datetime
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


LCD_BRIGHTNESS_MAX = 255


def set_lcd_brightness(client, device_id: str, brightness: int) -> None:
    """Apply the LCD backlight NOW. Three things about this call are traps.

    1. device_id is the BARE id ListDevices reports ("hid:513b..."), NOT the
       "serial:"-prefixed key SetLcdMedia wants. The daemon matches it against
       ActiveTarget::device_identity, which is the detected device id. Two
       adjacent methods, opposite key formats.

    2. IT CANNOT FAIL. The IPC handler pushes the request onto a channel and
       replies {"applied": true} before anything touches the device; a
       device_id matching nothing produces a journal warn! and nothing else.
       A successful reply here is NOT evidence. The journal is
       (journalctl _COMM=lianli-daemon | grep 'Failed to set LCD brightness').

    3. IT PERSISTS NOTHING. The value is lost on the next daemon restart
       unless it is also written to config.lcds[].brightness -- which
       apply.apply_templates does, by folding it into the entry SetLcdMedia
       already sends. This call is only the "apply it now" half.
    """
    value = int(brightness)
    if not 0 <= value <= LCD_BRIGHTNESS_MAX:
        raise ValueError(
            f"LCD brightness is 0-{LCD_BRIGHTNESS_MAX}, got {brightness!r}")
    client.call("SetLcdBrightness",
                {"device_id": device_id, "brightness": value})


def set_mode(client, mode: str, color: tuple[int, int, int] = (255, 255, 255),
             brightness: int = 4) -> None:
    """Drive the ring for a mode this app owns.

    Refuses "thermal" deliberately: there the POLLER owns the ring and
    re-drives it every ~2s, so anything sent would flash and vanish. A caller
    that reaches here with "thermal" has a bug, and a silent no-op would hide
    it.
    """
    if mode == "off":
        set_off(client)
    elif mode == "static":
        set_static(client, color, brightness)
    else:
        raise ValueError(
            f"the thermal poller owns the ring in {mode!r} mode; stop "
            f"{THERMAL_UNIT} before driving it from here")


THERMAL_UNIT = "lianli-thermal-rgb.service"


def _run(args, timeout: float = 10.0):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def poller_active(runner=None) -> bool:
    """Whether lianli-thermal-rgb.service is running RIGHT NOW.

    This is the one lighting fact that can be read back honestly, which is why
    the diff takes it as a live argument rather than trusting stored state.

    An unreadable status reads as NOT running. Guessing "active" here would
    make Apply skip a start the ring actually needs.
    """
    runner = runner or _run
    try:
        out = runner(["systemctl", "--user", "is-active", THERMAL_UNIT])
    except (OSError, subprocess.TimeoutExpired):
        return False
    return (out.stdout or "").strip() == "active"


def _unit(action: str, runner=None) -> None:
    runner = runner or _run
    try:
        out = runner(["systemctl", "--user", action, THERMAL_UNIT])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"could not {action} {THERMAL_UNIT}: {exc}") from exc
    if out.returncode != 0:
        raise RuntimeError(
            f"systemctl --user {action} {THERMAL_UNIT} exited "
            f"{out.returncode}: {(out.stderr or '').strip()}")


def stop_poller(runner=None) -> None:
    _unit("stop", runner)


def start_poller(runner=None) -> None:
    _unit("start", runner)


RING_STATE_PATH = Path("~/.config/lianli-panel/ring-state.json").expanduser()

RING_READBACK_NOTE = (
    "the ring cannot be read back — GetZoneColors fails on this device with "
    "\"zone 0 not found\", and config.rgb holds only what SetConfig persisted, "
    "which SetRgbEffect never updates. This is what this app last SENT.")


@dataclass
class RingState:
    """What this app last sent to the ring, and when.

    NOT what the ring is showing. There is no read-back path on this device at
    all, and the thermal poller re-drives the ring every ~2s without telling
    anyone, so this is stale the moment the poller starts. The UI shows it with
    the timestamp and RING_READBACK_NOTE attached, rather than implying a
    fidelity it cannot deliver.
    """
    mode: str = "thermal"
    color: tuple[int, int, int] = (255, 255, 255)
    brightness: int = 4
    set_at: str | None = None       # ISO 8601; None means never set by this app


def load_ring_state(path: Path | None = None) -> RingState:
    path = Path(path) if path is not None else RING_STATE_PATH
    try:
        obj = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return RingState()
    colour = obj.get("color") or [255, 255, 255]
    return RingState(
        mode=obj.get("mode", "thermal"),
        color=tuple(int(c) for c in colour[:3]),
        brightness=int(obj.get("brightness", 4)),
        set_at=obj.get("set_at"),
    )


def save_ring_state(state: RingState, path: Path | None = None) -> None:
    path = Path(path) if path is not None else RING_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "mode": state.mode,
        "color": list(state.color),
        "brightness": state.brightness,
        "set_at": datetime.now().astimezone().isoformat(),
        "note": RING_READBACK_NOTE,
    }, indent=1))


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
