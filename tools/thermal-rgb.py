#!/usr/bin/env python3
"""Drive the Universal Screen 8.8" LED ring from the hotter of CPU and GPU.

Replaces the daemon's built-in thermal_alert, which was disabled because it
never worked: it logs "override [255,0,0]" but rides the same persist-but-never-
apply path as SetConfig, so the ring was never actually driven. See the
lianli-rgb-setconfig-bug note. Only SetRgbEffect reaches the hardware, so that
is all this does -- it deliberately does NOT call SetConfig, because persisting
is pointless when the colour is re-driven every couple of seconds anyway, and
SetConfig is a whole-config read-modify-write that would be wasteful at this
cadence.

Colour is a continuous hue sweep, not three discrete steps: hue 120 (green) at
COOL_C falling to hue 0 (red) at HOT_C, which passes through yellow at the
midpoint. Full saturation and value, so the ring stays bright throughout.

Temperatures come from sysfs for the CPU and from a single long-lived
`nvidia-smi` child for the GPU. There is no nvidia hwmon node on this driver, and
spawning nvidia-smi per poll would burn far more CPU than streaming from one.
"""
import colorsys
import subprocess
import sys
import time

# /var/tmp is aged out after 30 days by systemd-tmpfiles (tmp.conf: q /var/tmp
# 1777 root root 30d). This service runs for months at a time, so importing
# from there is a time bomb with no error path -- the ring simply stops one
# day. /usr/local/share is where migrate_assets.sh put the shipped scripts.
sys.path.insert(0, "/usr/local/share/lianli-panel")
from ipc import call

import json
import os

CONFIG_PATH = "/var/lib/lianli-panel/thermal-rgb.json"
_DEFAULTS = {
    "cool_c": 45.0, "hot_c": 85.0, "poll_ms": 2000,
    "min_delta_c": 1.0, "force_refresh_s": 60, "brightness": 4,
}
_cfg = dict(_DEFAULTS)
_cfg_mtime = None


def reload_config():
    """Re-read the config when its mtime changes.

    The loop already wakes every poll_ms on the nvidia-smi stream, so this stat()
    is free and edits apply live -- no unit restart, and the GUI needs no
    privileges it would not otherwise have.
    """
    global _cfg, _cfg_mtime
    try:
        mtime = os.stat(CONFIG_PATH).st_mtime
    except OSError:
        if _cfg_mtime is not None:
            print("config removed; reverting to defaults", flush=True)
            _cfg, _cfg_mtime = dict(_DEFAULTS), None
        return _cfg
    if mtime == _cfg_mtime:
        return _cfg
    try:
        with open(CONFIG_PATH) as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"config unreadable ({exc}); keeping previous values", flush=True)
        return _cfg
    merged = dict(_DEFAULTS)
    merged.update({k: v for k, v in loaded.items() if k in _DEFAULTS})
    if merged["cool_c"] >= merged["hot_c"]:
        print("config rejected: cool_c >= hot_c; keeping previous values", flush=True)
        return _cfg
    _cfg, _cfg_mtime = merged, mtime
    print(f"config loaded: {merged}", flush=True)
    return _cfg


def cpu_temp():
    """Package temperature from coretemp. hwmon numbering is not stable across
    boots, so the node is resolved by name rather than hardcoded."""
    import glob
    for path in glob.glob("/sys/class/hwmon/hwmon*"):
        try:
            with open(f"{path}/name") as fh:
                if fh.read().strip() != "coretemp":
                    continue
            with open(f"{path}/temp1_input") as fh:
                return int(fh.read().strip()) / 1000.0
        except (OSError, ValueError):
            continue
    return None


def ring_device():
    """The ring's device_id is derived from its USB path, so it changes on every
    replug into a different port. Resolve it from the daemon, never hardcode."""
    r = call("ListDevices", {})
    if r.get("status") != "ok":
        return None
    for d in r.get("data") or []:
        if d.get("has_rgb") and d.get("pid") == 0x8050:
            return d["device_id"]
    return None


def colour_for(temp_c):
    frac = (temp_c - _cfg["cool_c"]) / (_cfg["hot_c"] - _cfg["cool_c"])
    frac = max(0.0, min(1.0, frac))
    hue = (1.0 - frac) * 120.0 / 360.0        # 120 deg green -> 0 deg red
    r, g, b = colorsys.hsv_to_rgb(hue, 1.0, 1.0)
    return [round(r * 255), round(g * 255), round(b * 255)]


def push(device, rgb):
    effect = {"mode": "Static", "colors": [rgb], "speed": 2,
              "brightness": _cfg["brightness"], "direction": "Clockwise",
              "scope": "All", "disabled": False}
    r = call("SetRgbEffect", {"device_id": device, "zone": 0, "effect": effect})
    return r.get("status") == "ok"


def gpu_stream(poll_ms):
    return subprocess.Popen(
        ["nvidia-smi", "--query-gpu=temperature.gpu",
         "--format=csv,noheader,nounits", f"--loop-ms={poll_ms}"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)


def main():
    device = ring_device()
    last_temp = None
    last_push = 0.0
    complained = False

    while True:
        spawned_poll_ms = _cfg["poll_ms"]
        try:
            proc = gpu_stream(spawned_poll_ms)
        except OSError as exc:
            print(f"cannot start nvidia-smi: {exc}", flush=True)
            time.sleep(30)
            continue

        for line in proc.stdout:
            cfg = reload_config()
            if cfg["poll_ms"] != spawned_poll_ms:
                print(f"poll_ms {spawned_poll_ms} -> {cfg['poll_ms']}; "
                      "restarting nvidia-smi", flush=True)
                proc.terminate()
                break            # outer while True respawns with the new value

            line = line.strip()
            if line.replace(".", "", 1).isdigit():
                gpu = float(line)
                complained = False
            else:
                gpu = None
                # nvidia-smi reports usage errors on stdout, not stderr, so a
                # broken invocation looks like a sample and would silently
                # degrade this to CPU-only. Never fail quietly here.
                if line and not complained:
                    print(f"unexpected nvidia-smi output: {line!r} "
                          f"-- GPU temperature is NOT being read", flush=True)
                    complained = True
            cpu = cpu_temp()

            temps = [t for t in (cpu, gpu) if t is not None]
            if not temps:
                continue
            temp = max(temps)

            now = time.monotonic()
            moved = last_temp is None or abs(temp - last_temp) >= cfg["min_delta_c"]
            stale = now - last_push >= cfg["force_refresh_s"]
            if not (moved or stale):
                continue

            if device is None:
                device = ring_device()
                if device is None:
                    continue

            rgb = colour_for(temp)
            if push(device, rgb):
                # Logged so the journal is evidence the colour actually reached
                # the hardware -- SetRgbEffect returning ok is the only signal
                # there is, since GetZoneColors does not resolve zones here.
                print(f"cpu={cpu} gpu={gpu} -> max {temp:.0f}C rgb={rgb}",
                      flush=True)
                last_temp, last_push = temp, now
            else:
                # Most likely a replug changed the device_id; re-resolve next pass.
                device = None

        # Stream ended (driver reload, suspend/resume). Restart it.
        proc.wait()
        time.sleep(5)


if __name__ == "__main__":
    main()
