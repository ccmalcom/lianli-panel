# lianli-panel — a GUI for the Lian Li Universal Screen 8.8"

Design spec — 2026-09-04

## Problem

The Lian Li Universal Screen 8.8" on this machine is driven by a hand-built
template (`gaming-dash`) plus a set of shell sensors and a thermal RGB poller,
all applied over the daemon's IPC socket by scripts in `/var/tmp/lianli-stats`.
Changing anything — an accent colour, a threshold, where a gauge sits — means
editing `build_template.py` and re-running it. That is a code change, so every
adjustment currently requires Claude.

The vendor GUI is not an option. `lianli-gui` cannot represent template mode and
**wipes the `lcds` array every time it writes config**, destroying the panel
setup; its RGB page persists settings that never reach the hardware.

This spec designs a replacement: a native desktop app that makes the panel
configurable by hand, without writing code.

## Goals

- Edit the live panel — layout, colours, thresholds, sensor bindings — visually.
- Manage a library of templates and choose which one is live.
- Control the LED ring and panel brightness.
- Define new shell sensors safely, with the daemon's real constraints checked.
- Make the traps in this stack unhittable rather than merely documented.

## Non-goals

- Fan control. `ListDevices` reports `fan_count: null` for both devices on this
  machine; there are no fans on this controller.
- Replacing `lianli-daemon`. This is an IPC client, nothing more.
- Multi-device support. The design assumes one LCD and one LED ring, which is
  what this machine has. Device ids are resolved at runtime, not hardcoded, so
  a second device would degrade to "first match" rather than crash.
- Windows/macOS. Linux + KDE only.

## Chosen approach: the preview *is* the canvas

The daemon exposes `RenderTemplatePreview`, which takes a complete template plus
a width and height and returns the rendered frame as `jpeg_base64`. Measured
round-trip on this machine: **0.30 s for the full 31-widget gaming-dash at
1920×480.**

That number decides the architecture. The editor does **not** reimplement any
of the 12 widget kinds in Qt. It displays the daemon's own render as the canvas
and overlays only selection rectangles and drag handles. While a widget is being
dragged, its rectangle moves live over a slightly stale image; on release, and on
a 250 ms debounce during continuous edits, the frame is re-rendered.

Consequences, in both directions:

- What you arrange is literally what the panel will display. There is no second
  renderer to drift from the daemon's.
- The cover-bar and self-gating tricks in `gaming-dash` render correctly for
  free. A Qt reimplementation would have to special-case draw order, range
  alphas, and clamped bar fills to show them at all.
- Roughly 12 widget renderers' worth of code is never written.
- The cost is latency: during a drag the underlying image lags by up to ~0.3 s.
  This was raised explicitly and accepted.

Rejected alternatives: reimplementing the renderer in Qt (accurate dragging, but
a second renderer that drifts — and drift presents to the user as the editor
lying); and a forms-only editor with numeric x/y (smallest, but positioning by
typing coordinates was explicitly not wanted).

### RenderTemplatePreview is safe, but it is not pure

The preview call touches no hardware. It does, however, **execute `command`
sources**, and that was measured, not assumed:

- A template whose only widget had the source
  `sh -c 'echo x >> /var/tmp/lianli-probe.log; echo 42'` was rendered 5 times.
  The log gained **10 lines** — the command runs **twice per render per widget**
  (once seeding preview history, once rendering the frame).
- The log file was created owned by **`lianli`**, confirming commands run as the
  daemon's uid during a preview.
- `gaming-dash` has **8** command-source widgets, so one preview of it spawns
  **16 subprocesses**, several of them `nvidia-smi`.
- Median render for the full template: **0.303 s** (min 0.284, max 0.311).

At ~3.3 previews/second during a continuous drag that is **~53 subprocess spawns
per second**. Worse, one of these commands is stateful: `graph.sh` writes
`/var/tmp/lianli-stats/state/cpu.<uid>`, which the *live panel's* sparkline
samples. Previewing would perturb the data the real panel is displaying. A
client-side timeout also does not cancel a server-side `sh -c`, so slow commands
accumulate rather than being dropped.

**Rule: automatic previews never execute command sources.** Before a debounced
render, `render.py` rewrites every `command` source to
`{"type": "constant", "value": <last known>}`. `constant` is one of the daemon's
own 14 source types and takes a single `value` field (verified), so this uses
the identical render path — no fidelity loss in layout, colour, or range
selection, and zero subprocesses.

This is better than a mitigation. While positioning a widget you want the value
held still; a live FPS counter jittering between frames makes it *harder* to
judge alignment and colour bands. Live values are an explicit, rate-limited
**Refresh** action, not the default.

## Architecture

Four layers. The lower three contain no Qt and are testable without a daemon or
a screen attached.

```
qt/          views: canvas, inspector, sidebar, sensor editor, ring page
render.py    debounced preview client, in-flight-request guard
model.py     Template/Widget/Source dataclasses <-> JSON; schema rules
ipc.py       unix-socket transport to /run/lianli/lianli-daemon.sock
```

**`ipc.py`** grows from the existing `/var/tmp/lianli-stats/bin/ipc.py`:
newline-delimited JSON over `AF_UNIX`, one call per connection. Adds typed
errors, a connection-refused path that reports the daemon as down rather than
raising, and a timeout.

**`model.py`** owns the schema. Dataclasses for `Template`, `Widget`, and the
source variants, round-tripping to the daemon's JSON without loss — unknown
fields are preserved verbatim so a daemon upgrade that adds a field cannot cause
this app to silently strip it on save. This layer also owns the unit conversion
described under "Percentage ranges" below.

**`render.py`** wraps `RenderTemplatePreview`. Coalescing rules: at most one
request in flight; edits arriving during a request set a dirty flag and fire a
single re-render on completion; a 250 ms debounce on continuous input (drags,
slider scrubs, colour picker motion). This keeps a fast drag from queueing
dozens of 0.3 s renders. It also owns the command-source substitution described
above — automatic renders always go out with `command` sources replaced by
`constant`, and only an explicit Refresh sends the real thing.

**`qt/`** is PySide6 6.11.1, already installed system-wide as
`python3-pyside6`. Dark theme consistent with the KDE/Otto desktop.

### Data ownership

Templates live **in the daemon**, read with `GetLcdTemplates` and written with
`SetLcdTemplates`. The app is a view over daemon state, not a parallel store,
so there is no divergence to reconcile after an external change.

The sensor library is **ours**, in `~/.config/lianli-panel/sensors.json`. The
daemon has no `sensors` key in its config — verified against `GetConfig`, whose
top-level keys are `aio, default_fps, ene6k77, fan_curves, fans, hid_backend,
lcds, rgb, rgb_drift_detection_enabled, rgb_drift_detection_interval_ms,
thermal_alert, wireless_groups`. A source is declared inline on each widget.
So a named, reusable sensor is a GUI-side concept that expands to an inline
source object on save.

## Schema reference

Both enums below were obtained authoritatively by sending an invalid variant to
`RenderTemplatePreview` and reading serde's `expected one of` error, rather than
by guessing or by grepping `strings` output (which concatenates and produces a
misleading partial list).

**Widget kinds (12):** `label`, `value_text`, `radial_gauge`, `vertical_bar`,
`horizontal_bar`, `speedometer`, `core_bars`, `image`, `video`, `sparkline`,
`clock_digital`, `clock_analog`

**Source types (14):** `constant`, `command`, `hwmon`, `nvidia_gpu`,
`amd_gpu_usage`, `wireless_coolant`, `cpu_usage`, `mem_usage`, `mem_used`,
`mem_free`, `network_rx`, `network_tx`, `disk_read`, `disk_write`

The built-in source list is **richer than the current dash uses**. `hwmon` reads
any hwmon node directly, and `network_rx/tx` and `disk_read/write` are available
but unused today. Several existing command sensors may have native equivalents
that would remove a subprocess spawn per second. Auditing that is out of scope
here; it is recorded as a follow-up.

`RenderTemplatePreview` requires all three of `template`, `width`, `height` —
omitting the dimensions fails with `missing field 'width'`.

## The five surfaces

**Template library.** A sidebar listing templates from `GetLcdTemplates`, with
create, duplicate, rename, and delete, and a radio marking which is live on the
panel. Duplicate is the intended way to experiment without risking a working
dash.

"Live" is not a property of a template. It is the `template_id` field on the
LCD's entry in `config.lcds` (alongside `type: "custom"`), so switching which
template is live means rewriting that entry and re-issuing `SetLcdMedia` — the
same path as any other apply. If the entry is missing entirely, which is what
`lianli-gui` causes, the app restores it from a known-good copy rather than
constructing one.

**Canvas and inspector.** The preview render, with selection rectangles over it.
Click to select, drag to move, handles to resize, arrow keys to nudge. The
inspector edits the selected widget's fields: kind, source binding, geometry,
colours, ranges, fonts. A widget list beside it exposes **draw order**, which is
array order and is load-bearing — see "Cover bars" below.

**Sensor editor.** Create a named sensor of any of the 14 source types. For
`command` sources, a test harness — see "Sensor validation".

**LED ring.** Off / Static / thermal-sweep, driving the existing
`lianli-thermal-rgb.service`. Colour picking for static; the hue endpoints and
temperature bounds for the sweep.

The poller currently has no configuration: `COOL_C`, `HOT_C`, `POLL_MS`,
`MIN_DELTA_C`, `FORCE_REFRESH_S` and `BRIGHTNESS` are module-level constants.
It gains a config file that the GUI writes, and re-reads it when its mtime
changes. The poller already wakes every 2 s on the `nvidia-smi` stream, so an
mtime check costs nothing and edits apply live — no unit restart, and no need
for the GUI to hold privileges it otherwise does not need. The constants become
the defaults used when the file is absent, so the poller runs unchanged if the
GUI is never launched.

Static and Off modes conflict with the poller, which overwrites the ring within
~2 s. Selecting either stops `lianli-thermal-rgb.service` first and says so;
selecting thermal-sweep starts it again.

The ring's device id is resolved at runtime
from `ListDevices` (first device with `has_rgb` and `pid == 0x8050`) because it
is USB-path-derived (`hid:0416:8050:1-8.3`) and changes on every replug into a
different port. The LCD's id is a stable serial and is safe to match on.

**Brightness.** `SetLcdBrightness`.

## Enforced invariants

Each of these has already cost real debugging time on this machine. The app
makes them structurally unhittable rather than documenting them.

**Whole-set writes.** `SetLcdTemplates` **replaces the entire stored template
set**. The app always sends the full library. Today's `apply.sh` sends only
`[gaming-dash]`, which is harmless with one template and would silently delete
every other template the moment there were two — this is the single most
dangerous interaction with the library feature.

**Templates then media.** `SetLcdTemplates` alone does not update the panel; it
replaces the stored template while the live renderer keeps what it last
prepared. Every apply issues `SetLcdTemplates` followed by `SetLcdMedia`. This
is one code path with no way to call the first without the second.

**Percentage ranges.** `SensorRange.max` is a **percentage of that widget's own
`value_min..value_max` span**, not a raw reading — `drawing.rs::range_color`
computes `pct = unit_interval * 100`. A threshold of `60` on a 20..100 gauge
means 68 °C. The UI accepts and displays real units (°C, fps, GB) and `model.py`
converts on write and back on read. This is the highest-value invariant in the
list because it fails *silently*: the panel renders, nothing errors, the colours
are simply wrong.

Confirmed against the installed binary by disassembly:
`lianli_media::custom::helpers::drawing::range_color_blended` clamps the unit
interval to `[0,1]` and multiplies by `100.0`; the first range whose `max >=
percentage` wins, and a null `max` is the fallback. So

```
raw threshold = value_min + (range.max / 100) × (value_max − value_min)
```

and 60 % over a 20..100 span is 68 — the figure this spec has used throughout.

Edge cases the model layer must define, since the conversion is lossy at the
boundaries:

- `value_min == value_max` normalises to 0 %.
- Boundary equality belongs to the **earlier** range (`>=`, not `>`).
- Reversed min/max, unsorted range lists, maxima outside `0..100`, and more than
  one null catch-all are all representable in JSON and must be validated rather
  than silently re-ordered.
- When a widget's `value_min`/`value_max` changes, decide explicitly whether raw
  thresholds stay fixed (percentages move) or percentages stay fixed (raw
  thresholds move). **Raw stays fixed** — the user typed °C and means °C.
- Do **not** re-encode a percentage that the user never touched merely because
  the UI displayed it in real units. Float round-tripping would drift stored
  values on every save and break the lossless-round-trip promise. Only write
  back ranges whose displayed value actually changed.

**Centre-origin geometry.** Widget `x`/`y` are the widget's centre, not its
top-left. The canvas converts between centre-origin model coordinates and
top-left screen rectangles in exactly one place.

**Landscape authoring.** The panel is physically 480×1920, the LCD entry uses
`orientation: 90`, and the daemon's `render_dimensions()` swaps axes before
rendering and rotates afterwards. Templates are authored at **1920×480** and the
canvas is fixed to that aspect.

**Cover bars.** There is no conditional visibility — `Widget.visible` is a
static bool. Two mechanisms fake it: a sensor-driven widget can self-gate via a
`SensorRange` with `alpha: 0` in a value band, and a `horizontal_bar` with
`value_max: 1` acts as an on/off opaque cover. Only the last widget in a rect
wins, so the stack is always `[needs covering] -> [cover] -> [self-gating]`, and
**two widgets that both need covering cannot share a rect**. The app does not
try to author these automatically. It surfaces draw order in the widget list,
and warns when overlapping widgets are reordered such that a cover no longer
precedes what it covers.

## Sensor validation

Command sensors run via `sh -c` as **uid `lianli`**. The daemon takes the first
whitespace token of stdout, which must parse as `f32`, with exit status 0.

An earlier draft said the editor "test-runs the command as uid `lianli`". That
was not implementable: the GUI runs as `chase`, becoming uid 971 needs root, and
the daemon exposes no test-command method. The contradiction is resolved by
using the renderer itself as the harness.

**Two tiers, clearly labelled.**

*Authoritative* — build a throwaway one-widget template whose `value_text` source
is the candidate command, and call `RenderTemplatePreview`. The daemon executes
it as `lianli`, under exactly the conditions the real sensor will face, and
returns the rendered number as an image. The editor displays that crop. If the
command is unreadable, non-numeric, or exits non-zero, the rendered result shows
it. This needs no privileges and no new daemon method — it is the same mechanism
that proved command execution above.

*Diagnostic* — additionally run the command as `chase` to capture raw stdout,
stderr, and exit status, which the rendered image cannot show. This is richer
but **not authoritative**: it runs as the wrong uid and will succeed on paths
under `$HOME` that the daemon cannot reach. The UI labels it as such rather than
presenting the two as equivalent.

Because the authoritative tier actually executes the command, the editor warns
before testing a command with side effects, and never runs a candidate sensor on
the debounced preview path.

Between them they check:

- Exit status is 0.
- The first token parses as a float, and shows the parsed value.
- The command and any script it references are readable by uid `lianli`.
  `/home/chase` is mode 700, so **the daemon cannot traverse it** — anything it
  must read has to live outside `$HOME`. This is reported as a specific error
  naming the path, not a generic failure.
- A warning, not an error, if the script has a path that could exit non-zero or
  print nothing: a command sensor must print `0` on every failure path. This is
  a heuristic and is presented as a caution to check, not a verdict.

One failure mode is called out because it is invisible: **a tool that prints its
errors to stdout will be swallowed by a parse-each-line-as-data loop.**
`nvidia-smi` rejects `-lms2000` and prints the usage error to *stdout*, so the
reader receives it as a normal sample; the parse fails, the sensor degrades, and
everything still looks healthy. The test harness shows raw stdout alongside the
parsed value so a case like this is visible rather than inferred.

## Is it actually on the screen?

`Prepared custom template` in the journal **does not prove anything reached the
hardware.** After the panel is replugged, the daemon logs `Wired device topology
changed` and reopens the LED ring but never reopens the screen; the h264 encoder
has died and the restart guard refuses to retry an encoder that ran under 10 s.
The unit stays `active (running)` and IPC calls return `{"status":"ok"}` into a
dead handle. The panel keeps showing the firmware splash while every call
succeeds.

**The check described in the first draft of this spec did not work, in two
independent ways.** It said: search the journal since the daemon's last start
for `Universal Screen 8.8" opened`. Both halves are wrong.

*The daemon does not restart on replug.* It logs `Wired device topology changed`
and keeps running, so the `opened` line from the original start is still inside
the search window. The check would report healthy in exactly the failure it
exists to detect.

*Two different lines match that string*, and the wrong one is the ring:

```
winusb::led:       Universal Screen 8.8" LED Ring opened: 60 LEDs (0416:8050)
winusb::lcd::core: Universal Screen 8.8" opened: 480x1920 at bus 1 addr 6 serial 513b…
```

On replug the ring reopens and the screen does not — so a loose grep matches the
ring and reports the panel healthy in precisely the failure case.

**Corrected check.** Match the LCD open event on its module and shape, not on
the shared prefix: `lianli_devices::winusb::lcd::core` together with
`opened: 480x1920` and the serial. Then compare *timestamps* rather than testing
presence: the panel is considered up only if the newest LCD-open event is newer
than the newest topology-change or disconnect event. If a disconnect is more
recent, report the handle as dead and show the fix
(`sudo systemctl restart lianli-daemon-system.service`, then re-apply RGB).

This remains a **heuristic**, and the UI says so. Journal parsing infers state
from log text; only a successful post-replug open, or end-to-end acknowledgement
from the device, would be proof. It is still worth having — nothing in the
current tooling reports this at all, and it is the difference between "my edit
didn't work" and "the panel has been disconnected since the last replug."

## Safety

**Snapshots.** Every apply first writes the full template set and RGB state to
`~/.local/share/lianli-panel/snapshots/<timestamp>/`. One-click revert restores
the previous snapshot. Snapshots are pruned to the most recent 20 — a retention
policy defined now rather than discovered as unbounded growth later.

**Vendor GUI interlock.** A banner while `lianli-gui` is running, because it
wipes the `lcds` array on every config write. The app does not kill it; it warns
and offers to re-verify the `lcds` entry after it exits.

**Read-modify-write config.** Any `SetConfig` reads the current config, mutates,
and writes back whole — never constructs a config from scratch, which is how the
vendor GUI destroys the `lcds` array.

## Asset relocation

`/usr/lib/tmpfiles.d/tmp.conf` contains `q /var/tmp 1777 root root 30d`:
systemd ages out `/var/tmp` after 30 days. The entire current setup lives in
`/var/tmp/lianli-stats`.

The sensor scripts are read every second, so their atime keeps them alive. But
`build_template.py` and `apply.sh` are touched only when run by hand. After 30
quiet days they can be removed — taking with them the ability to rebuild the
dash, while the panel itself keeps running and gives no indication anything is
wrong.

Daemon-readable assets therefore move out of `/var/tmp`. The path must stay
outside `$HOME` because the daemon runs as uid `lianli` (uid 971, confirmed) and
cannot traverse `/home/chase`, which is mode 0700 (confirmed).

A root-owned `/usr/local/share/lianli-panel/` was the first choice, but it
breaks a stated goal: an **unprivileged GUI cannot write there**, and "define
new shell sensors from the GUI" requires somewhere to save the scripts it
authors. Root-owned assets would make every new sensor a root step.

So the tree is split by who writes it:

| Path | Owner | Mode | Holds |
|---|---|---|---|
| `/usr/local/share/lianli-panel/` | `root:root` | 755 | scripts shipped with the app |
| `/var/lib/lianli-panel/` | `chase:chase` | 755 | sensors and assets the GUI authors |

Both are outside `$HOME`, both readable by `lianli`, and neither is subject to
tmpfiles aging. Creating them needs root **once**; after that the GUI writes
only to the second and never needs privileges again. The exact commands are
handed over rather than run.

`build_template.py` is retired to a seed script once the GUI can edit templates
directly. It is kept, not deleted: it documents how `gaming-dash` was derived.

## Decisions a plan needs that the design above left open

**Editing lifecycle.** Edits accumulate in an in-memory draft; nothing reaches
the daemon until **Apply**. There is no auto-save. Closing with a dirty draft
prompts. Renaming or deleting the live template is allowed but re-points
`template_id` in the same apply.

**Concurrent modification.** "Templates live in the daemon" removes a stale
*store*, not a stale *draft*. `apply.sh`, `lianli-gui`, or a second copy of this
app can change the set while a draft is open, and a whole-set write would
silently discard their change. Immediately before writing, re-read
`GetLcdTemplates` and compare a hash against the one read at draft start; on
mismatch, prompt rather than overwrite.

**Partial apply.** `SetLcdTemplates` then `SetLcdMedia` is two calls and is not
atomic. If the first succeeds and the second fails, the stored set has moved on
while the panel still renders the old frame. Treat it as a transaction: on a
failed second call, restore the previous template set and report that the panel
was left unchanged.

**Snapshot contents.** "RGB state" is ambiguous here, and the three sources
currently disagree — daemon config says static white, `rgb-state.json` says Off,
and the running thermal service is driving colours that match neither. A
snapshot therefore stores the *configured* mode plus the poller's config and
whether its unit is active. It does **not** claim to capture what the ring is
physically showing: `GetZoneColors` fails on this device (`zone 0 not found`),
so there is no read-back path, and the UI says so rather than implying fidelity
it cannot deliver.

**Schema coverage.** `gaming-dash` exercises only 7 of 12 widget kinds and 5 of
14 source types, so the existing template cannot be the source of truth for the
inspector's forms. Each variant's fields, requiredness, and defaults are
extracted from the daemon by the same serde-probing technique used for the
enums — send a variant with a field omitted and read `missing field 'x'`. This
is a discrete, mechanical task and belongs early in the plan, because every
inspector form depends on its output.

**Unknown-field preservation is recursive.** Preserving unknown keys only at the
widget level is not enough; nested `kind`, `source`, `font`, and range objects
need the same treatment. When the user changes a widget's kind or source type,
fields belonging to the old variant are dropped rather than carried over — but
the drop is shown, not silent.

**Canvas interaction.** Hit-testing picks the topmost widget whose rect contains
the click, with repeated clicks cycling downward through overlaps — necessary
because cover bars sit directly on top of what they hide and would otherwise be
unselectable. Minimum drag size 8×8 px; arrow keys nudge 1 px, shift-arrow 10;
widgets may be positioned partly off-canvas but are flagged.

**Daemon compatibility.** Widget or source variants this app does not recognise
are preserved and editable as raw JSON rather than dropped, so a daemon upgrade
degrades to reduced functionality instead of data loss.

## Testing

`model.py` and `render.py` are covered by unit tests with a stub socket — round
-tripping the real `gaming-dash.json` (31 widgets, all the tricky constructs)
without loss is the central test, along with percentage-range conversion in both
directions and the render coalescing rules.

Qt views are exercised by driving the running app, not by asserting on widget
trees. Per standing preference: **tests passing is not evidence the app works.**
The completion criterion is the app launched, a change made in it, and that
change visible on the physical panel — confirmed by the `Universal Screen 8.8"
opened` journal line plus a look at the screen.

## Follow-ups, explicitly out of scope

- Audit whether existing `command` sensors have native equivalents among the 14
  source types (`hwmon` for CPU temp in particular) to remove per-second
  subprocess spawns.
- `GetZoneColors` does not work on this ring (`zone 0 not found`), so there is
  no colour read-back path. The app shows the last value it set, and says so.
