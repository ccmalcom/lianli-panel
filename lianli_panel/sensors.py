"""Named sensors, and testing a candidate command honestly.

The daemon has no `sensors` key in its config -- a source is declared inline on
each widget -- so a reusable named sensor is a GUI-side concept that expands to
an inline source object on save.

TESTING IS TWO-TIER, and the tiers are NOT equivalent:

  AUTHORITATIVE -- render a one-widget template whose source is the candidate
    command. The daemon runs it as uid lianli, under exactly the conditions the
    real sensor will face, and returns the number as an image. No privileges
    needed and no new daemon method. This is the only tier that proves anything.

  DIAGNOSTIC -- run the command as the current user to capture stdout, stderr
    and exit status, which the rendered image cannot show. Richer, but it runs
    as the WRONG UID and will happily succeed on paths under $HOME that the
    daemon cannot reach. Never present it as proof.

Both tiers EXECUTE the command. Warn before probing something with side effects.
"""
from __future__ import annotations

import base64
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

LIBRARY_PATH = Path("~/.config/lianli-panel/sensors.json").expanduser()
USER_SCRIPT_DIR = Path("/var/lib/lianli-panel")
FONT = "/usr/share/fonts/google-noto/NotoSansMono-Bold.ttf"


@dataclass
class Sensor:
    name: str
    source: dict


def load(path: Path | None = None) -> dict[str, Sensor]:
    path = Path(path) if path is not None else LIBRARY_PATH
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    return {name: Sensor(name, src) for name, src in raw.items()}


def save(sensors: dict[str, Sensor], path: Path | None = None) -> None:
    path = Path(path) if path is not None else LIBRARY_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({s.name: s.source for s in sensors.values()}, indent=1))


# --- diagnostic tier -------------------------------------------------------


@dataclass
class Diagnostic:
    stdout: str
    stderr: str
    exit_code: int
    parsed: float | None
    problems: list[str]


def run_diagnostic(cmd: str, timeout: float = 10.0) -> Diagnostic:
    try:
        proc = subprocess.run(["sh", "-c", cmd], capture_output=True,
                              text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return Diagnostic("", "", -1, None,
                          [f"timed out after {timeout}s — the daemon would stall "
                           "on this every update interval"])
    except OSError as exc:
        return Diagnostic("", str(exc), -1, None, [f"could not run: {exc}"])

    problems: list[str] = []
    if proc.returncode != 0:
        problems.append(
            f"exit status {proc.returncode} — the daemon requires 0, and will "
            "read nothing from this sensor")

    token = proc.stdout.split()[0] if proc.stdout.split() else None
    parsed: float | None = None
    if token is None:
        problems.append("no output — the daemon reads the first whitespace token")
    else:
        try:
            parsed = float(token)
        except ValueError:
            problems.append(
                f"first token {token!r} does not parse as a number. Note that some "
                "tools print errors to STDOUT (nvidia-smi does), so this may be an "
                "error message being read as data")

    problems.extend(static_checks(cmd))
    return Diagnostic(proc.stdout, proc.stderr, proc.returncode, parsed, problems)


def static_checks(cmd: str) -> list[str]:
    problems: list[str] = []
    if "/home/" in cmd:
        problems.append(
            "references a path under /home/chase, which is mode 0700 — the daemon "
            f"runs as uid lianli and CANNOT traverse it. Move the script to "
            f"{USER_SCRIPT_DIR}.")
    if "/var/tmp/" in cmd:
        problems.append(
            "references /var/tmp, which systemd-tmpfiles ages out after 30 days "
            f"of inactivity. Move the script to {USER_SCRIPT_DIR}.")
    return problems


# --- authoritative tier ----------------------------------------------------


def _probe_template(cmd: str) -> dict:
    return {
        "id": "sensor-probe", "name": "sensor probe",
        "base_width": 1920, "base_height": 480, "rotated": True,
        "background": {"type": "color", "rgb": [10, 13, 20, 255]},
        "widgets": [{
            "id": "value", "x": 960.0, "y": 240.0, "width": 1600.0, "height": 300.0,
            "kind": {
                "type": "value_text",
                "source": {"type": "command", "cmd": cmd},
                "format": "{:.2}", "unit": "",
                "font": {"path": FONT}, "font_size": 200.0,
                "color": [255, 255, 255, 255], "align": "center",
                "value_min": 0.0, "value_max": 100.0,
                "ranges": [{"max": None, "color": [255, 255, 255], "alpha": 255}],
                "letter_spacing": 0.0,
            },
        }],
    }


def render_authoritative(client, cmd: str) -> bytes:
    """Render the candidate command exactly as the daemon will read it.

    Executes the command as uid lianli. Warn the user before calling this on
    anything with side effects.
    """
    data = client.call("RenderTemplatePreview", {
        "template": _probe_template(cmd), "width": 1920, "height": 480,
    })
    return base64.b64decode(data["jpeg_base64"])
