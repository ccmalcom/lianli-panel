"""Preview client.

MEASURED, NOT ASSUMED: RenderTemplatePreview touches no hardware, but it is not
pure. It executes `command` sources TWICE per widget per render, as uid lianli.
A probe rendered 5 times produced 10 executions, and the file the command wrote
came out owned by lianli.

gaming-dash has 8 command widgets, so one preview of it spawns 16 subprocesses
-- several of them nvidia-smi -- and takes ~0.30s. At ~3.3 previews/sec while
dragging that is ~53 spawns/sec. Worse, graph.sh writes the state file the LIVE
panel's sparkline reads, so previewing would corrupt what the screen displays.

Therefore: automatic renders NEVER execute command sources. They are swapped for
`constant` sources -- one of the daemon's own 14 source types -- so the render
path is identical and no subprocess runs. Live values are an explicit action.
"""
from __future__ import annotations

import base64
import copy
from typing import Any

WIDTH, HEIGHT = 1920, 480


def _sources(tpl_json: dict):
    for w in tpl_json.get("widgets", []):
        kind = w.get("kind")
        if isinstance(kind, dict):
            src = kind.get("source")
            if isinstance(src, dict):
                yield kind, src


def command_sources(tpl_json: dict) -> list[str]:
    seen: list[str] = []
    for _, src in _sources(tpl_json):
        if src.get("type") == "command":
            cmd = src.get("cmd")
            if isinstance(cmd, str) and cmd not in seen:
                seen.append(cmd)
    return seen


def substitute_commands(tpl_json: dict, values: dict[str, float],
                        default: float = 0.0) -> dict:
    out = copy.deepcopy(tpl_json)
    for kind, src in _sources(out):
        if src.get("type") == "command":
            kind["source"] = {"type": "constant",
                              "value": float(values.get(src.get("cmd"), default))}
    return out


class PreviewRenderer:
    def __init__(self, client, width: int = WIDTH, height: int = HEIGHT) -> None:
        self.client = client
        self.width = width
        self.height = height
        self.last_values: dict[str, float] = {}

    def render(self, tpl_json: dict, live: bool = False) -> bytes:
        payload = tpl_json if live else substitute_commands(tpl_json, self.last_values)
        data: Any = self.client.call("RenderTemplatePreview", {
            "template": payload, "width": self.width, "height": self.height,
        })
        return base64.b64decode(data["jpeg_base64"])


class Coalescer:
    """At most one render in flight; never drop the newest request.

    THREE WAYS A HELD REQUEST GETS RELEASED, and the third is easy to forget:
      finish() -- a render completed and something newer is waiting
      due()    -- the debounce window elapsed with nothing in flight
      request()-- the window had already elapsed, so it fires immediately

    due() is not optional. A request arriving AFTER finish() but INSIDE the
    debounce window has no in-flight render to release it, so without a polled
    due() it would stay pending forever and the final state of a drag would
    never render -- the exact failure this class exists to prevent. The Qt layer
    polls due() on a short timer.
    """

    def __init__(self, interval_s: float = 0.25) -> None:
        self.interval_s = interval_s
        self.in_flight = False
        self.pending = False
        self._last_fire = float("-inf")

    def request(self, now: float) -> bool:
        if self.in_flight or (now - self._last_fire) < self.interval_s:
            self.pending = True
            return False
        self._fire(now)
        return True

    def due(self, now: float) -> bool:
        """True when a held request may now be sent."""
        if not self.pending or self.in_flight:
            return False
        if (now - self._last_fire) < self.interval_s:
            return False
        self.pending = False
        self._fire(now)
        return True

    def finish(self, now: float) -> bool:
        self.in_flight = False
        return self.due(now)

    def _fire(self, now: float) -> None:
        self.in_flight = True
        self._last_fire = now
