#!/usr/bin/env python3
"""SEED SCRIPT — bootstrap only. This is NOT how templates are edited.

Templates are edited in lianli-panel-gui, which reads the live set from the
daemon and writes it back as a whole. This script exists for one case: a fresh
machine whose daemon has no template at all, where there is nothing for the
editor to open.

DO NOT edit a live template here. The GUI replaces the ENTIRE stored set on
every Apply, so a change made in this script is overwritten the next time
anyone touches the editor -- silently, with no error anywhere.

To seed a blank machine:
    ./.venv/bin/python tools/seed_template.py > /tmp/seed.json
    ./.venv/bin/python -m lianli_panel.cli validate /tmp/seed.json
then apply it once from the GUI or with apply.apply_templates.
"""
"""Generate the 'gaming-dash' LcdTemplate for the Lian Li Universal Screen 8.8".

Canvas is 1920x480: the panel is physically 480x1920, but the LCD entry uses
orientation 90, and the daemon's render_dimensions() swaps the axes before
rendering and rotates the finished frame afterwards. So we lay out landscape.

Widget x/y are the widget CENTRE, not its top-left corner.

The centre tile has two states. While a game runs it shows FRAMES PER SECOND
over a live framerate; while nothing is running it shows the date and a clock.
The template engine has NO conditional visibility -- Widget.visible is a static
bool -- so the switch is done with two mechanisms:

  1. Self-gating. A SensorRange carries an `alpha`, so a sensor-driven widget
     can be made fully transparent in a value band. The FPS readout has an
     alpha-0 band below 1 fps and so hides itself when no game is running.

  2. Cover bars. A clock_digital has a static `color` and cannot self-gate, so
     it is hidden by drawing an opaque bar over it. A bar whose value clamps to
     value_max fills its entire rect; one at zero draws nothing at all. That
     on/off fill is the switch.

Draw order is array order, and only the LAST widget in a rect can win. That is
why each pair is stacked [thing that needs covering] -> [cover] -> [self-gating
thing]: the FPS number is on top and gates itself, so nothing needs to cover
it. Two widgets that both need covering cannot share a rect, which is why the
date sits in the value block rather than in the heading row.
"""
import json
import re
import subprocess

W, H = 1920, 480

FONT = "/usr/share/fonts/google-noto/NotoSansMono-{}.ttf"
BOLD = {"path": FONT.format("Bold")}
MED = {"path": FONT.format("Medium")}

BG = [10, 13, 20, 255]
BG_RGB = BG[:3]              # cover bars take an RGB triple plus a range alpha
CYAN = [34, 211, 238]        # GPU column accent
VIOLET = [167, 139, 250]     # CPU column accent
MINT = [74, 222, 128]        # FPS accent
DIM = [110, 125, 150, 255]   # section headings, and the idle clock
RULE = [38, 48, 68]          # column dividers
AMBER = [255, 184, 0]
RED = [239, 68, 68]
WHITE = [255, 255, 255, 255]

# IMPORTANT: drawing.rs::range_color computes `pct = unit_interval * 100`, so a
# SensorRange `max` is a PERCENTAGE of that widget's own value_min..value_max
# span -- not a raw sensor reading. Everything below is authored in real units
# and converted, so the thresholds mean what they say.
TEMP_MIN, TEMP_MAX = 20.0, 100.0     # gauge + value span for both temperatures
FPS_MIN, FPS_MAX = 0.0, 240.0        # span for the FPS readout


def pct(raw, lo, hi):
    """Convert a raw threshold into the percentage-of-span the renderer wants."""
    return (raw - lo) / (hi - lo) * 100.0


def ram_total_gb():
    """Total RAM in GiB, read once at build time -- it never changes at runtime,
    so the '/ NN GB' captions are baked into the template as static labels."""
    with open("/proc/meminfo") as fh:
        for line in fh:
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1048576
    return 0.0


def vram_total_gb():
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5).stdout
        return int(re.search(r"\d+", out).group()) / 1024
    except Exception:
        return 0.0


RAM_TOTAL = ram_total_gb()
VRAM_TOTAL = vram_total_gb()


def thermal(accent):
    """Cool below 60 C, amber to 80 C, red beyond. Warm colours mean heat only."""
    return [
        {"max": pct(60.0, TEMP_MIN, TEMP_MAX), "color": accent, "alpha": 255},
        {"max": pct(80.0, TEMP_MIN, TEMP_MAX), "color": AMBER, "alpha": 255},
        {"max": None, "color": RED, "alpha": 255},
    ]


def usage(accent):
    """Amber past 70% full, red past 90%. Works unchanged for a ring reading a
    percentage and for a text widget reading GiB, because a range max is a
    percentage of the widget's own span either way."""
    return [
        {"max": 70.0, "color": accent, "alpha": 255},
        {"max": 90.0, "color": AMBER, "alpha": 255},
        {"max": None, "color": RED, "alpha": 255},
    ]


THERM = thermal(CYAN)
THERM_CPU = thermal(VIOLET)

# Low framerate is the one non-thermal thing worth alarming about: red under
# 30 fps, amber under 60, mint above. The leading band is the idle gate --
# below 1 fps the number renders at alpha 0 so the clock beneath shows through.
FPS_RANGES = [
    {"max": pct(1.0, FPS_MIN, FPS_MAX), "color": MINT, "alpha": 0},
    {"max": pct(30.0, FPS_MIN, FPS_MAX), "color": RED, "alpha": 255},
    {"max": pct(60.0, FPS_MIN, FPS_MAX), "color": AMBER, "alpha": 255},
    {"max": None, "color": MINT, "alpha": 255},
]


def flat(c):
    return [{"max": None, "color": c, "alpha": 255}]


SRC = {
    "gpu_temp": {"type": "nvidia_gpu", "gpu_index": 0, "metric": "temp"},
    "gpu_load": {"type": "nvidia_gpu", "gpu_index": 0, "metric": "usage"},
    "cpu_temp": {"type": "hwmon", "name": "coretemp", "label": "temp1",
                 "device_path": "coretemp.0"},
    "cpu_load": {"type": "cpu_usage"},
    "ram_gb": {"type": "command", "cmd": "/var/tmp/lianli-stats/bin/ram_gb.sh"},
    "vram_gb": {"type": "command", "cmd": "/var/tmp/lianli-stats/bin/vram_gb.sh"},
    "fps": {"type": "command", "cmd": "/var/tmp/lianli-stats/bin/fps.sh"},
    "idle": {"type": "command", "cmd": "/var/tmp/lianli-stats/bin/idle.sh"},
    "graph": {"type": "command", "cmd": "/var/tmp/lianli-stats/bin/graph.sh"},
}

widgets = []


def add(wid, kind, x, y, w, h, **extra):
    widgets.append({"id": wid, "kind": kind, "x": float(x), "y": float(y),
                    "width": float(w), "height": float(h), **extra})


def label(wid, text, x, y, w, h, size, color, spacing=0.0, font=MED, align="center"):
    add(wid, {"type": "label", "text": text, "font": font, "font_size": float(size),
              "color": color, "align": align, "letter_spacing": float(spacing)},
        x, y, w, h)


def value(wid, src, x, y, w, h, size, ranges, unit="", fmt="{:.0}",
          font=BOLD, align="center", vmin=0.0, vmax=100.0, interval=None):
    kind = {"type": "value_text", "source": SRC[src], "format": fmt, "unit": unit,
            "font": font, "font_size": float(size), "color": WHITE, "align": align,
            "value_min": vmin, "value_max": vmax, "ranges": ranges,
            "letter_spacing": 0.0}
    extra = {"update_interval_ms": interval} if interval else {}
    add(wid, kind, x, y, w, h, **extra)


def gauge(wid, src, x, y, size, ranges, vmin, vmax, interval=None):
    extra = {"update_interval_ms": interval} if interval else {}
    add(wid, {"type": "radial_gauge", "source": SRC[src],
              "value_min": vmin, "value_max": vmax,
              "start_angle": 90.0, "sweep_angle": 360.0, "inner_radius_pct": 0.88,
              "background_color": [26, 32, 46, 255], "ranges": ranges,
              "bg_corner_radius": 0.0, "value_corner_radius": 0.0,
              "gradient": False},
        x, y, size, size, **extra)


def hbar(wid, src, x, y, w, h, color, interval=None):
    extra = {"update_interval_ms": interval} if interval else {}
    add(wid, {"type": "horizontal_bar", "source": SRC[src],
              "value_min": 0.0, "value_max": 100.0,
              "background_color": [26, 32, 46, 255], "corner_radius": 4.0,
              "ranges": flat(color)}, x, y, w, h, **extra)


def cover(wid, src, x, y, w, h):
    """An opaque panel-coloured rectangle that appears only when `src` reads 1
    or more. value_max is 1, so any truthy reading clamps the bar to a full-width
    fill and a zero reading draws nothing -- the template engine's nearest thing
    to a conditional."""
    add(wid, {"type": "horizontal_bar", "source": SRC[src],
              "value_min": 0.0, "value_max": 1.0,
              "background_color": [0, 0, 0, 0], "corner_radius": 0.0,
              "ranges": flat(BG_RGB)}, x, y, w, h, update_interval_ms=1000)


def clock(wid, fmt, x, y, w, h, size, color, spacing=0.0, font=BOLD):
    add(wid, {"type": "clock_digital", "format": fmt, "font": font,
              "font_size": float(size), "color": color, "align": "center",
              "letter_spacing": float(spacing)}, x, y, w, h)


def divider(wid, x):
    add(wid, {"type": "vertical_bar", "source": {"type": "constant", "value": 100.0},
              "value_min": 0.0, "value_max": 100.0,
              "background_color": [0, 0, 0, 0], "corner_radius": 0.0,
              "ranges": flat(RULE)}, x, 240, 2, 300)


def column(prefix, cx, heading, accent, temp_src, temp_ranges,
           mem_gb_src, mem_label, mem_total, load_src):
    """One side column: heading, then a temperature ring and a memory ring side
    by side, then a single full-width load bar.

    The memory ring's arc and its readout share one GiB sensor, spanned 0..total
    so the arc is still a true fraction of capacity. Reading a percentage sensor
    for the arc would mean a second sample -- and a second nvidia-smi spawn every
    cycle for VRAM -- that could disagree with the figure printed inside it."""
    left = cx - 260          # column content spans 520px wide
    label(f"{prefix}-head", heading, cx, 44, 300, 40, 26, accent + [255], spacing=8)

    # -- temperature ring (left of the pair)
    tx = cx - 120
    gauge(f"{prefix}-ring", temp_src, tx, 185, 200, temp_ranges, TEMP_MIN, TEMP_MAX)
    value(f"{prefix}-temp", temp_src, tx, 176, 170, 70, 50, temp_ranges, unit="°",
          vmin=TEMP_MIN, vmax=TEMP_MAX)
    label(f"{prefix}-templbl", "TEMP", tx, 224, 170, 26, 16, DIM, spacing=3)

    # -- memory ring (right of the pair), reading used GiB against total
    mx = cx + 120
    gauge(f"{prefix}-memring", mem_gb_src, mx, 185, 200, usage(accent),
          0.0, mem_total, interval=2000)
    value(f"{prefix}-memval", mem_gb_src, mx, 176, 170, 70, 46, usage(accent),
          fmt="{:.1}", vmin=0.0, vmax=mem_total, interval=2000)
    label(f"{prefix}-memlbl", mem_label, mx, 224, 170, 26, 16, DIM, spacing=3)
    label(f"{prefix}-memtot", f"/ {mem_total:.1f} GB", mx, 300, 200, 24, 16,
          DIM, spacing=2)

    # -- load bar
    label(f"{prefix}-load-l", "LOAD", left + 60, 392, 120, 28, 19, DIM,
          spacing=2, align="left")
    hbar(f"{prefix}-load-b", load_src, cx + 40, 392, 300, 16, accent)
    value(f"{prefix}-load-v", load_src, cx + 225, 392, 80, 30, 22, flat(accent),
          unit="%", align="right", font=MED)


# ---- CPU column (left) -------------------------------------------------
column("cpu", 300, "CPU", VIOLET, "cpu_temp", THERM_CPU,
       "ram_gb", "RAM", RAM_TOTAL, "cpu_load")

# ---- GPU column (right) ------------------------------------------------
column("gpu", 1620, "GPU", CYAN, "gpu_temp", THERM,
       "vram_gb", "VRAM", VRAM_TOTAL, "gpu_load")

divider("rule-l", 620)
divider("rule-r", 1300)

# ---- FPS / clock centre panel -----------------------------------------
# Order matters here; see the module docstring. Heading first, then the bar
# that blanks it while idle. The heading row and the value block are kept as
# separate non-overlapping rects so each cover only reaches its own layer.
label("fps-head", "FRAMES PER SECOND", 960, 44, 620, 40, 24, MINT + [255],
      spacing=10)
cover("head-cover", "idle", 960, 44, 640, 48)

# Idle content, drawn underneath the framerate and hidden by a cover while a
# game runs. Date format is chrono's: %-d is a GNU extension it does not take.
clock("idle-date", "%a %b %d", 960, 104, 620, 44, 30, DIM, spacing=8, font=MED)
clock("idle-time", "%H:%M:%S", 960, 182, 620, 130, 100, DIM)
cover("value-cover", "fps", 960, 157, 640, 168)

value("fps-value", "fps", 960, 150, 520, 140, 122, FPS_RANGES, interval=1000,
      vmax=FPS_MAX)

# One sparkline whose command source switches: framerate while a game runs,
# CPU load while idle. Two stacked sparklines could not alternate -- each paints
# an opaque background, so with a fixed draw order the same one always wins.
add("fps-graph", {
    "type": "sparkline", "source": SRC["graph"],
    "value_min": 0.0, "value_max": 240.0,
    "auto_range": True, "history_length": 90,
    "line_width": 2.5,
    "line_color": MINT + [255], "fill_color": MINT + [70],
    "background_color": [17, 22, 34, 255],
    "ranges": [],
    "border_color": [38, 48, 68, 255], "border_width": 1.0,
    "corner_radius": 6.0, "padding": 6.0,
    "show_points": False, "point_radius": 2.5,
    "show_baseline": False, "baseline_value": 0.0,
    "baseline_color": [140, 140, 160, 160], "baseline_width": 1.0,
    "smooth": True, "scroll_rtl": False,
    "fill_from_ranges": False, "range_blend": False,
    "show_gridlines": True, "gridlines_horizontal": 3, "gridlines_vertical": 0,
    "gridline_color": [120, 130, 150, 70], "gridline_width": 1.0,
    "show_axis_labels": True, "axis_label_count": 3, "axis_labels_on_right": True,
    "axis_label_format": "{:.0}", "axis_label_font": MED, "axis_label_size": 13.0,
    "axis_label_color": [150, 165, 190, 200], "axis_label_padding": 5.0,
}, 960, 342, 620, 192, update_interval_ms=1000)

template = {
    "id": "gaming-dash",
    "name": "Gaming Dash",
    "base_width": W,
    "base_height": H,
    "rotated": True,
    "background": {"type": "color", "rgb": BG},
    "widgets": widgets,
}

path = "/var/tmp/lianli-stats/gaming-dash.json"
with open(path, "w") as fh:
    json.dump(template, fh, indent=1)
print(f"wrote {path}: {len(widgets)} widgets "
      f"(RAM {RAM_TOTAL:.1f} GB, VRAM {VRAM_TOTAL:.1f} GB)")
