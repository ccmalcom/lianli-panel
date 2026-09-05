"""Lighting state: the ring, both brightnesses, and the poller's config.

Qt-free on purpose. Everything the Lighting tab decides happens here, so it can
be tested without a display, a daemon, or systemd.

TWO DIFFERENT BRIGHTNESSES live in this module and they are not
interchangeable:

  ring_brightness   0-4. An RgbEffect field. Reaches the ring through
                    SetRgbEffect in static mode, and through thermal-rgb.json's
                    "brightness" when the poller is driving.
  screen_brightness 0-255. The LCD backlight. Reaches the panel through
                    SetLcdBrightness AND persists in config.lcds[].brightness.
                    None means "never set" -- the daemon omits the key while it
                    is null, so None is the panel's own default, not zero.

MODE OWNERSHIP is the rule the diff exists to enforce:

  thermal        the POLLER owns the ring. This app must send NO SetRgbEffect.
                 Anything it sent would be overwritten within ~2s, so the user
                 would see their colour flash and then change to something else.
  static / off   this app owns the ring, and the poller must be STOPPED first
                 for the same reason.

And one consequence that is easy to miss: when the poller has just been
stopped, whatever it last pushed is what is lit. "Nothing changed in the draft"
is not a reason to skip the effect -- the hardware is not where the draft
thinks it is.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field, replace

from .ring import ThermalConfig

MODES = ("off", "static", "thermal")

RING_BRIGHTNESS_MAX = 4
SCREEN_BRIGHTNESS_MAX = 255
POLL_MS_FLOOR = 200


@dataclass
class Problem:
    level: str           # error | warning
    field: str
    message: str


@dataclass
class LightingState:
    mode: str = "thermal"
    color: tuple[int, int, int] = (255, 255, 255)
    ring_brightness: int = 4
    screen_brightness: int | None = None
    thermal: ThermalConfig = field(default_factory=ThermalConfig)

    def copy(self) -> "LightingState":
        return replace(self, thermal=copy.deepcopy(self.thermal))

    def to_json(self) -> dict:
        return {
            "mode": self.mode,
            "color": list(self.color),
            "ring_brightness": self.ring_brightness,
            "screen_brightness": self.screen_brightness,
            "thermal": self.thermal.to_json(),
        }

    @classmethod
    def from_json(cls, obj: dict) -> "LightingState":
        colour = obj.get("color") or [255, 255, 255]
        return cls(
            mode=obj.get("mode", "thermal"),
            color=tuple(int(c) for c in colour[:3]),
            ring_brightness=int(obj.get("ring_brightness", 4)),
            screen_brightness=(None if obj.get("screen_brightness") is None
                               else int(obj["screen_brightness"])),
            thermal=ThermalConfig.from_json(obj.get("thermal") or {}),
        )


def problems(state: LightingState) -> list[Problem]:
    out: list[Problem] = []

    if state.mode not in MODES:
        out.append(Problem("error", "mode",
                           f"unknown mode {state.mode!r}; expected one of "
                           f"{', '.join(MODES)}"))

    if len(state.color) != 3 or not all(
            isinstance(c, int) and 0 <= c <= 255 for c in state.color):
        out.append(Problem("error", "color",
                           f"colour components must be three integers 0-255, "
                           f"got {state.color!r}"))

    if not 0 <= state.ring_brightness <= RING_BRIGHTNESS_MAX:
        out.append(Problem("error", "ring_brightness",
                           f"ring brightness is 0-{RING_BRIGHTNESS_MAX}, got "
                           f"{state.ring_brightness}"))

    if state.screen_brightness is not None and not (
            0 <= state.screen_brightness <= SCREEN_BRIGHTNESS_MAX):
        out.append(Problem("error", "screen_brightness",
                           f"screen brightness is 0-{SCREEN_BRIGHTNESS_MAX}, "
                           f"got {state.screen_brightness}"))

    t = state.thermal
    if t.cool_c >= t.hot_c:
        out.append(Problem(
            "error", "cool_c",
            f"cool ({t.cool_c}) must be below hot ({t.hot_c}); the hue sweep "
            "runs from green at cool to red at hot and has nowhere to go"))

    if t.poll_ms < POLL_MS_FLOOR:
        out.append(Problem(
            "warning", "poll_ms",
            f"{t.poll_ms}ms is below {POLL_MS_FLOOR}ms; the poller streams "
            "nvidia-smi at this interval and re-stats its config file each "
            "time. It will work, but it buys nothing the ring can show"))

    if t.min_delta_c < 0:
        out.append(Problem("error", "min_delta_c",
                           "the minimum delta cannot be negative"))

    if t.force_refresh_s < 1:
        out.append(Problem("error", "force_refresh_s",
                           "the forced refresh interval must be at least 1s"))

    return out


@dataclass(frozen=True)
class LightingDiff:
    poller_config: bool = False
    unit_action: str | None = None       # "start" | "stop" | None
    ring_effect: bool = False
    screen_brightness: bool = False

    @property
    def empty(self) -> bool:
        return not (self.poller_config or self.unit_action
                    or self.ring_effect or self.screen_brightness)


def diff(base: LightingState, draft: LightingState, *,
         poller_running: bool) -> LightingDiff:
    """What actually needs sending, given where the hardware currently is.

    `poller_running` is not part of either state: it is a live fact read from
    systemd at Apply time. A draft that says "thermal" while the unit is dead
    still needs a start, even though nothing in the draft changed.
    """
    poller_config = draft.thermal != base.thermal

    wants_poller = draft.mode == "thermal"
    if wants_poller and not poller_running:
        unit_action = "start"
    elif not wants_poller and poller_running:
        unit_action = "stop"
    else:
        unit_action = None

    ring_effect = False
    if draft.mode in ("static", "off"):
        changed = (base.mode != draft.mode
                   or tuple(base.color) != tuple(draft.color)
                   or base.ring_brightness != draft.ring_brightness)
        # After a stop, the ring is showing whatever the poller last pushed, so
        # the app's own state is not on the hardware and must be re-sent.
        ring_effect = changed or unit_action == "stop"

    screen_brightness = (draft.screen_brightness is not None
                         and draft.screen_brightness != base.screen_brightness)

    return LightingDiff(poller_config=poller_config, unit_action=unit_action,
                        ring_effect=ring_effect,
                        screen_brightness=screen_brightness)
