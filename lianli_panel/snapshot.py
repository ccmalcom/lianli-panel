"""Snapshots taken before every apply, with a bounded retention policy.

Retention is defined up front rather than discovered later as unbounded growth:
the newest `keep` are retained and take() prunes as it goes.

WHAT A SNAPSHOT IS NOT: it does not capture what the LED ring is physically
showing. GetZoneColors fails on this device ("zone 0 not found"), so there is no
read-back path at all, and the three available sources disagree -- daemon config,
rgb-state.json, and whatever the thermal poller last pushed. The snapshot stores
CONFIGURED state and says so, rather than implying a fidelity it cannot deliver.
"""
from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .apply import read_templates

SNAPSHOT_ROOT = Path("~/.local/share/lianli-panel/snapshots").expanduser()
RGB_STATE_FILE = Path("/var/tmp/lianli-stats/rgb-state.json")
NOTE = ("configured state only; the ring's actual colour cannot be read back "
        "(GetZoneColors fails on this device)")


def _thermal_active() -> bool:
    import subprocess
    try:
        out = subprocess.run(
            ["systemctl", "--user", "is-active", "lianli-thermal-rgb.service"],
            capture_output=True, text=True, timeout=10)
        return out.stdout.strip() == "active"
    except (OSError, subprocess.TimeoutExpired):
        return False


def take(client, root: Path | None = None, keep: int = 20) -> Path:
    root = Path(root) if root is not None else SNAPSHOT_ROOT
    root.mkdir(parents=True, exist_ok=True)

    templates, digest = read_templates(client)
    config = client.call("GetConfig") or {}

    try:
        rgb_state = json.loads(RGB_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        rgb_state = None

    payload = {
        "taken_at": datetime.now().astimezone().isoformat(),
        "templates": templates,
        "templates_hash": digest,
        "lcds": config.get("lcds") or [],
        "rgb_config": config.get("rgb") or {},
        "rgb_state_file": rgb_state,
        "thermal_service_active": _thermal_active(),
        "note": NOTE,
    }

    stamp = datetime.now().strftime("%Y-%m-%dT%H-%M-%S-%f")
    target = root / stamp
    target.mkdir()
    (target / "snapshot.json").write_text(json.dumps(payload, indent=1))

    prune(root, keep=keep)
    return target


def load(path: Path) -> dict:
    path = Path(path)
    if path.is_dir():
        path = path / "snapshot.json"
    return json.loads(path.read_text())


def _snapshots(root: Path) -> list[Path]:
    return sorted((d for d in Path(root).iterdir() if d.is_dir()),
                  key=lambda d: d.name)


def prune(root: Path, keep: int = 20) -> list[Path]:
    entries = _snapshots(root)
    doomed = entries[:-keep] if len(entries) > keep else []
    for d in doomed:
        shutil.rmtree(d)
    return doomed


def latest(root: Path | None = None) -> Path | None:
    root = Path(root) if root is not None else SNAPSHOT_ROOT
    if not root.exists():
        return None
    entries = _snapshots(root)
    return entries[-1] if entries else None
