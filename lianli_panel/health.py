"""Whether the panel is actually reachable, inferred from the journal.

WHY THIS IS NOT OBVIOUS: after the screen is replugged, the daemon logs
"Wired device topology changed", reopens the LED RING, and never reopens the
screen -- the h264 encoder died and the restart guard refuses to retry one that
ran under 10s. The unit stays active(running) and every IPC call returns
{"status":"ok"} into a dead handle. SetLcdMedia logs "Prepared custom template"
while the panel shows the firmware splash.

Two traps, both of which an earlier draft of this fell into:

  1. Searching "since the daemon started" never expires -- the daemon does NOT
     restart on replug, so the original open line stays in the window forever
     and the check reports healthy in exactly the failure it exists to catch.

  2. TWO log lines match 'Universal Screen 8.8" ... opened', and the other one
     is the LED ring -- the device that DOES come back. A loose match reports
     healthy precisely when the screen is dead.

So: match the LCD line on its module and shape, and compare TIMESTAMPS rather
than testing presence.

This is a heuristic. It reads log text, not device state. Only a successful
post-replug open or an end-to-end acknowledgement would be proof; say so in
the UI rather than implying certainty.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

RESTART_HINT = (
    "sudo systemctl restart lianli-daemon-system.service   "
    "# then re-apply RGB"
)

# Must carry the lcd::core module AND the panel geometry. The ring line lives in
# winusb::led and says "LED Ring opened: 60 LEDs", so it cannot match this.
_OPEN = re.compile(r"winusb::lcd::core:.*opened:\s*480x1920")
_DISCONNECT = re.compile(r"topology changed|disconnect(ed)?|device removed",
                         re.IGNORECASE)
# Fractional seconds are OPTIONAL in this pattern but REQUIRED in practice:
# check() asks for short-iso-precise. Plain short-iso is whole-second, and a
# real replug logs the disconnect and the reopen inside the SAME second, which
# would compare equal and be read as healthy. Order is the tie-breaker anyway
# (see below), but microseconds make the common case decidable on timestamp.
_TS = re.compile(
    r"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?[+-]\d{2}:\d{2})")


@dataclass
class PanelHealth:
    ok: bool
    reason: str
    last_open: datetime | None = None
    last_disconnect: datetime | None = None


def _stamp(line: str) -> datetime | None:
    m = _TS.match(line)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1))
    except ValueError:
        return None


def parse_journal(lines: Iterable[str]) -> PanelHealth:
    """Journal lines are consumed in order, oldest first.

    ORDER IS THE TIE-BREAKER, not the timestamp. A real replug logs this:

      17:59:47  H264 chunk write failed: ... (it may have been disconnected)
      17:59:48  reopen failed: ... (it may have been disconnected)
      17:59:48  Wired device topology changed (+0 -1): re-initializing
      18:00:10  Wired device topology changed (+1 -0): re-initializing
      18:00:10  Universal Screen 8.8" LED Ring opened: 60 LEDs

    Note the last two share a second. Comparing timestamps alone would call
    that pair equal; whichever came LAST in the stream is what actually
    happened last, so the winner is tracked by sequence rather than by clock.
    """
    last_open: datetime | None = None
    last_disconnect: datetime | None = None
    last_event: str | None = None   # "open" | "disconnect"

    for line in lines:
        ts = _stamp(line)
        if ts is None:
            continue
        if _OPEN.search(line):
            last_open, last_event = ts, "open"
        elif _DISCONNECT.search(line):
            last_disconnect, last_event = ts, "disconnect"

    if last_open is None:
        return PanelHealth(
            False,
            "no LCD open event in the journal — the panel has not been opened "
            f"since this daemon started.\n{RESTART_HINT}",
            None, last_disconnect)

    if last_disconnect is not None and last_event == "disconnect":
        return PanelHealth(
            False,
            f"the panel was disconnected at {last_disconnect:%H:%M:%S} and has not "
            f"reopened since (last open {last_open:%H:%M:%S}). IPC calls will still "
            f"return ok into a dead handle.\n{RESTART_HINT}",
            last_open, last_disconnect)

    return PanelHealth(
        True,
        f"panel opened at {last_open:%Y-%m-%d %H:%M:%S} with no later disconnect "
        "(heuristic: read from the journal, not from the device)",
        last_open, last_disconnect)


def check(unit: str = "lianli-daemon-system.service") -> PanelHealth:
    try:
        # short-iso-precise, NOT short-iso: the latter is whole-second, and a
        # replug's disconnect and reopen land in the same second.
        out = subprocess.run(
            ["journalctl", "-u", unit, "-b", "--no-pager", "-o",
             "short-iso-precise"],
            capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return PanelHealth(False, f"could not read the journal: {exc}")
    if out.returncode != 0:
        return PanelHealth(False, f"journalctl exited {out.returncode}: "
                                  f"{out.stderr.strip()[:200]}")
    return parse_journal(out.stdout.splitlines())
