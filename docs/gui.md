# The panel editor

`lianli-panel-gui` edits the daemon's template library and applies it to the
Universal Screen 8.8".

## Running it

From the application launcher: **Lian Li Panel Editor**.
From a shell: `./.venv/bin/lianli-panel-gui`.

The desktop entry is `tools/lianli-panel.desktop`, installed to
`~/.local/share/applications/`. Its `Exec` is an absolute path into this
repo's venv; if the repo moves, edit that line.

## What it edits

The canvas is the daemon's own `RenderTemplatePreview` output, not a
reimplementation of its renderer, so what you see is what the panel draws.
Selection rectangles are overlaid on top of it.

Edits accumulate in an in-memory draft. **Nothing reaches the daemon until
Apply.** There is no auto-save, and closing with unapplied changes prompts.

## Tabs

**Editor** — the canvas, the template library, the inspector. Unchanged.

**Sensors** — named sensors, and the two-tier test harness for `command`
sources. The two tiers are not equivalent: *authoritative* renders the command
through the daemon as uid `lianli`, which is the only tier that proves
anything; *diagnostic* runs it as you and will succeed on `$HOME` paths the
daemon cannot traverse. Both really execute the command, so both ask first.

Scripts the daemon must read belong in `/var/lib/lianli-panel/`. `/home/chase`
is mode 0700 and uid `lianli` cannot enter it.

**Lighting** — the ring, both brightnesses, and the thermal poller's settings.

## What Apply means

One Apply commits everything, in stages, cheapest and most reversible first:

1. the poller's config file
2. starting or stopping `lianli-thermal-rgb.service`
3. the ring's colour (static and off only — in thermal mode the poller owns it)
4. the template set, and the persistent half of the panel's brightness
5. `SetLcdBrightness`, to make that brightness take effect now

Nothing touches the template set until every lighting stage has succeeded. If
one fails, the stages before it are rolled back and the dialog lists what
happened to each.

Two stages report themselves as unverifiable rather than claiming success:
the ring has no read-back at all (`GetZoneColors` fails on this device), and
`SetLcdBrightness` replies ok before it touches the device. Brightness is
still safe because it is *persisted* by stage 4, which is verifiable.

**Test on ring** is the only control that reaches the hardware before Apply,
and only because it says so. If the poller is running it will take the ring
back within ~2s — which is the interlock telling the truth, not a bug.

## Things that are not obvious

**Apply writes the whole library.** `SetLcdTemplates` replaces the entire
stored set, so every apply sends every template. This is why the editor holds
the library rather than one template.

**Apply is two calls.** `SetLcdTemplates` alone does not change the panel; the
live renderer keeps what it last prepared until `SetLcdMedia` follows. Both go
through one code path so the first cannot happen without the second, and a
failure of the second restores the previous set.

**Range thresholds are shown in real units.** The daemon stores a range's `max`
as a percentage of that widget's own `value_min..value_max` span — a stored
`60` on a 20..100 gauge means 68 °C. The editor converts in both directions,
and moving `value_min`/`value_max` holds the real thresholds still rather than
the percentages. Narrowing a span past a threshold clamps it, and the editor
says so.

**Draw order is load-bearing.** The widget list is in draw order, which is
array order: only the last widget covering a rect is visible. That is how cover
bars fake conditional visibility, and reordering one can make a hidden widget
reappear. The list flags covers and warns when a reorder breaks one.

**Refresh (runs sensors) is not the same as the automatic preview.**
`RenderTemplatePreview` executes `command` sources as uid `lianli`, twice per
widget per render. Automatic renders substitute constants for them; only that
button sends the real thing.

**"Live" is not a property of a template.** It is `template_id` on the LCD's
entry in `config.lcds`, so switching which template is live rewrites that entry
in the same apply.

## The banners

**Panel handle dead.** After a replug the daemon reopens the LED ring but not
the screen, the unit stays `active (running)`, and every IPC call returns `ok`
into a dead handle. The banner infers this from the journal. It is a heuristic
— it reads log text, not device state — and it says so. The fix it offers is
`sudo systemctl restart lianli-daemon-system.service`, then re-apply RGB.

**lianli-gui is running.** The vendor GUI cannot represent template mode, so it
wipes `config.lcds` on every config write. This app does not close it. Close it
yourself, then use **Re-check config.lcds** — that check is read-only; an Apply
is what restores the entry, from the newest snapshot that recorded one.

## Safety

Every apply first snapshots the full template set and the configured RGB state
to `~/.local/share/lianli-panel/snapshots/`, keeping the most recent 20.
**Revert** restores the newest.

Revert does **not** restore ring state: `GetZoneColors` fails on this device, so
there is no colour read-back path, and the thermal poller re-drives the ring
every ~2 s anyway. The snapshot records the *configured* mode, not what the
ring is physically showing.
