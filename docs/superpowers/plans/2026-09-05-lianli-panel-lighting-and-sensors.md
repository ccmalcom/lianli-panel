# lianli-panel Lighting and Sensors Implementation Plan (Plan B)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish the editor — a sensor editor with an honest two-tier command test harness, a lighting page owning the LED ring, both brightnesses and the thermal poller's configuration, and one Apply that commits all of it as a staged transaction.

**Architecture:** Two new Qt-free modules (`lighting.py`, `gui/links.py`) plus extensions to `ring.py` and `apply.py` carry every decision; two new Qt tabs are thin views over them. `lighting.py` sits in the core package rather than under `gui/` because `apply.py` consumes it and core must not import the GUI layer. The unified Apply is a *staged* transaction ordered cheap-and-reversible first, dangerous-and-verified last, so a lighting failure never leaves the template set touched. Almost all of the non-Qt logic Plan B needs already exists: `sensors.py` (library, diagnostic tier, `render_authoritative`, static checks) and `ring.py` (`find_ring`, `set_static`, `set_off`, `ThermalConfig`) shipped with the core plan, and the poller already re-reads its config on mtime change.

**Tech Stack:** Python 3.14.7, PySide6 6.11.1 (system RPM `python3-pyside6`, visible through the venv's `--system-site-packages`), the existing `lianli_panel` core, pytest 9.1.1. No new dependencies. No network at runtime.

**Spec:** `docs/superpowers/specs/2026-09-04-lianli-panel-gui-design.md` — read it before starting. This plan argues from it and does not restate its reasoning. The sections that matter here are "The five surfaces" (sensor editor, LED ring, brightness), "Sensor validation", and "Decisions a plan needs that the design above left open".

**Predecessors:**
- `docs/superpowers/plans/2026-09-04-lianli-panel-core.md`, complete.
- `docs/superpowers/plans/2026-09-04-lianli-panel-editor-gui.md` (Plan A), complete as of commit `6b171d8`, plus the follow-up commit `576ea2e` that fixed `SetLcdMedia`'s key format. Read that plan's ledger at `.superpowers/sdd/2026-09-04-lianli-panel-editor-gui/progress.md` before starting — its closing section carries corrections that this plan depends on.

**Scope fixed by Plan A.** Plan A's "What Plan B covers" section named: the sensor editor, the LED ring page, brightness, the thermal poller's configuration UI, surfacing `model.validate`'s warnings, and retiring `build_template.py`. All six are here. Nothing from the spec falls between the two plans.

---

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.14.7**, run as `./.venv/bin/python`. The venv already exists with `--system-site-packages`; PySide6 and Pillow are system RPMs and are **not** pip-installable into a clean venv here. Never recreate the venv.
- **No new third-party dependencies.** In particular there is no `pytest-qt`; Qt tests construct objects directly and call methods. Anything needing `pip install` is controller work and must be raised, not attempted.
- **Qt tests run headless:** `QT_QPA_PLATFORM=offscreen`. Exactly one `QApplication` per process — tests use the shared `qapp` fixture from `tests/conftest.py`, never construct their own.
- **`lianli_panel/gui/geometry.py`, `draft.py`, `forms.py`, `interaction.py`, `gui/links.py`, and `lianli_panel/lighting.py` MUST NOT import PySide6.** A test asserts this for each. They are where the logic lives precisely so it can be tested without a display or a daemon.
- **This PySide6 build (6.11.1) rejects bare/unscoped `Qt.*` enum constants** at call sites that older PySide6 accepted. Plan A hit this twice (`QPixmap.scaled`, mouse-button and key enums). Write `Qt.Orientation.Horizontal`, `QMessageBox.StandardButton.Yes`, `QColorDialog.ColorDialogOption.ShowAlphaChannel` — the fully scoped form — and treat any `Qt.<EnumMember>` copied from a plan sketch as a thing to check against this build.
- **`SetLcdTemplates` replaces the ENTIRE stored template set.** Always send the whole library. This is why the draft owns the set rather than one template.
- **`SetLcdTemplates` alone does not update the panel.** Always follow with `SetLcdMedia`. `apply.apply_templates` (and, from Task 4, `apply.apply_all`) is the only permitted path; never call either method directly from the GUI.
- **`SetLcdMedia`'s `device_id` is `"serial:<serial>"`, not the bare id `ListDevices` reports.** `apply.entry_key()` is the only place that format is built. Passing the bare id appends a duplicate `lcds` entry and silently discards the template switch — this cost a whole follow-up session; see hazard 4 in `apply.py`'s module docstring.
- **`SetLcdBrightness`'s `device_id` is the BARE id, the opposite of `SetLcdMedia`'s.** It is matched against `ActiveTarget::device_identity`, which is the detected device id. Task 3 documents this as a hazard in `ring.py`.
- **`SetLcdBrightness` cannot fail.** `crates/lianli-daemon/src/ipc/server.rs:258` pushes the request onto a channel and replies `{"applied": true}` before anything touches the device. A `device_id` matching nothing produces a `warn!` in the journal and nothing else. A successful reply is not evidence.
- **`RenderTemplatePreview` executes `command` sources** — twice per widget per render, as uid `lianli`. Automatic/debounced renders MUST go through `render.PreviewRenderer` with `live=False`. Only an explicit user action sends the real thing. The sensor probe is such an explicit action and is confirmed before it runs.
- **The daemon SILENTLY IGNORES unknown JSON fields.** A misspelled field name is dropped, not rejected, and a key this app fails to preserve is permanently deleted on the next save with no error anywhere. This is also why the sensor↔widget link cannot live inside the template.
- **`/var/lib/lianli/config.json` is the truth; `GetConfig` is not.** `GetConfig` returns `ipc_state.config`, and the dedup-on-reload only lands in the service's own copy. Measured live: `t+0.0s IPC=2 DISK=2`, `t+0.2s IPC=1 DISK=2`. Any verification step that needs the real config reads the file.
- **Tests MUST NOT call mutating daemon methods.** No `Set*`, `Save*`, `Delete*`, `Apply*`, `Install*`, `Bind*`, `Unbind*`, `Reboot*`, `SwitchDisplayMode` against the live socket. `RenderTemplatePreview`, `Get*` and `List*` are safe. Tests that need mutation use `FakeClient` from `tests/conftest.py`.
- **Tests MUST NOT shell out to `systemctl` or `journalctl`.** Both are injected: `health.check`/`vendor_gui_pids` through `HealthPoller`, and from Task 3 the systemd calls through a `runner` parameter.
- **Never read, print, or store values from any `.env` file.**
- **Commit messages:** plain, no `Co-Authored-By` trailer. End each with:
  `Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4`

---

## What the daemon source says, and where to check it

Plan B's brightness design rests on three facts read out of the daemon source. A checkout was present at
`/tmp/claude-1000/-home-chase-Documents-Code-lianli-panel/9c03d711-.../scratchpad/lianli-src` when this plan was written — **that is a scratchpad and may be gone.** If a task needs to re-verify, the installed binary is `/usr/bin/lianli-daemon` at tag `v0.8.8`, and the facts are:

| Fact | Source | Consequence |
| --- | --- | --- |
| `SetLcdBrightness { device_id: String, brightness: u8 }` matches `target.device_identity`, which is `candidate.device_id` — the bare `hid:...` | `service/mod.rs:800-816`, `service/media.rs:432-435` | Opposite key format to `SetLcdMedia`. Task 3. |
| The IPC handler replies `{"applied": true}` unconditionally, before the event is processed | `ipc/server.rs:258-267` | Brightness has no error path at all. Task 4 marks the stage unverifiable. |
| `LcdConfig.brightness: Option<u8>`, `skip_serializing_if = "Option::is_none"`, applied at target creation | `lianli-shared/src/config.rs:47-48`, `service/media.rs:443-451` | Brightness IS persistent config, and it rides the `SetLcdMedia` entry the app already writes. Task 4. |
| `set_effect` returns `IpcResponse::error` on an unknown device or a controller failure, and writes nothing to the config | `ipc/rgb.rs:20-35` | `SetRgbEffect` failures ARE visible; its successes are not, because there is no read-back. Task 3. |

**Why brightness is absent from `GetConfig` today:** `skip_serializing_if` omits it while it is `None`, and nothing has ever set it. Once set it appears, and the Lighting tab reads it back — the one lighting value that has a real read-back, unlike the ring.

---

## Execution: who runs which task

The routing rule from both predecessor plans is unchanged, and it was derived from three observed limits of Codex's sandbox, not from task difficulty:

| Limit | Observed | Consequence |
| --- | --- | --- |
| The daemon socket is unreachable | `PermissionError: [Errno 1]` on connect | Every step that talks to the daemon is controller work |
| No network | `pip install` cannot run | No task may introduce a dependency |
| Writes are workspace-scoped | — | `/var/tmp`, `/var/lib`, `/usr/local/share`, `~/.config`, `~/.local/share` and systemd unit edits are controller work |

**Codex writes the code and makes the unit tests green. The controller owns every step that touches the socket, the hardware, systemd, the network, or a path outside the repo — and owns every "launch the app and look at it" step.**

### Routing

| Task | Runs as | Why |
| --- | --- | --- |
| 1 Live-entry selection fix | **Codex** ← best fit | Pure function over a config dict; 6 tests; no I/O |
| 2 Lighting state module | **Codex** ← best fit | Pure dataclasses and a diff; 20 tests; no I/O |
| 3 Ring, brightness and unit primitives | **Split** | Codex: steps 1–9 (`FakeClient`, injected `runner`). Controller: step 10, first real `SetLcdBrightness` |
| 4 The staged transaction | **Codex** ← best fit | Pure orchestration over injected ops; 18 tests; the highest-value unit in the plan |
| 5 Sensor link map | **Codex** ← best fit | Pure; validation-on-load is all dict comparison |
| 6 Lighting tab | **Split** | Codex: steps 1–8 (offscreen). Controller: step 9, the ring actually changing colour |
| 7 Sensors tab | **Split** | Codex: steps 1–9 (`FakeClient`). Controller: step 10, a real authoritative probe |
| 8 Window integration | **Split** | Codex: steps 1–10. Controller: steps 11–12, **the first unified Apply against the panel** |
| 9 Vendor the poller into the repo | **Controller** | `/usr/local/share`, a systemd unit, and `sudo` |
| 10 Retire `build_template.py` | **Controller** | `/usr/local/share`, and it needs a live template to seed from |
| 11 End-to-end acceptance | **Controller + Chase** | The physical screen and the physical ring |

### Handoff batches

Per `chase-workflow:controller-budget`: a controller's cost is context floor × turn count, so **hand off after Task 2, after Task 5, after Task 7, and after Task 8.** Do not start the next task in a batch boundary session. The SDD ledger under `.superpowers/sdd/2026-09-05-lianli-panel-lighting-and-sensors/` is the state of record; write it after every task, not at the end.

### Gate commands

Give each dispatch only its own test files:

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_<module>.py -v
```

**Verify the gate matches tests before sending it** — a pytest path that matches nothing still exits 0 and reads as a pass:

```bash
./.venv/bin/pytest --collect-only -q tests/test_<module>.py
```

### Techniques that already cost a session to learn

- **`journalctl` needs no sudo.** The unit is `lianli-daemon-system.service`, so a bare `journalctl -u lianli-daemon` says "No entries" and reads as a permissions problem. Use `journalctl _COMM=lianli-daemon`. `Prepared custom template for LCD[...]: <id>` is the authoritative statement of what is on the panel.
- **`QTest.mouseClick` needs the window activated** under `QT_QPA_PLATFORM=offscreen`: `QTest.qWaitForWindowExposed(w)` then `w.activateWindow()`, or the click is silently swallowed and looks exactly like a broken signal connection. For a `QRadioButton` it needs `pos=QPoint(8, 11)` (the indicator), not the widget centre. `.click()` always works but skips the event pipeline.
- **No Wayland input-injection tool is installed** (`ydotool`/`xdotool`/`kdotool` all absent). `PySide6.QtTest.QTest` is the working substitute for driving the real app.
- **Modal dialogs are tested by monkeypatching `QMessageBox.question`**, not by injecting live input. A live-modal-injection attempt hung during Plan A and had to be killed.

---

## File Structure

**Created:**

| File | Responsibility |
| --- | --- |
| `lianli_panel/lighting.py` | `LightingState`, the baseline diff, and lighting validation. Qt-free, and in the core package because `apply.py` consumes it. |
| `lianli_panel/gui/links.py` | The sensor↔widget side-car map: load, save, validate-on-load, bind, propagate. Qt-free, and GUI-only — nothing in core knows about it. |
| `lianli_panel/gui/lighting_tab.py` | The Lighting view. Thin over `lighting.py`. |
| `lianli_panel/gui/sensors_tab.py` | The Sensors view. Thin over `sensors.py`, `links.py` and `forms.py`. |
| `tools/thermal-rgb.py` | The poller, brought under version control (Task 9). |
| `tools/lianli-thermal-rgb.service` | Its unit, pointed off `/var/tmp` (Task 9). |
| `tests/test_lighting.py`, `tests/test_gui_links.py`, `tests/test_apply_all.py`, `tests/test_gui_lighting_tab.py`, `tests/test_gui_sensors_tab.py`, `tests/test_gui_window_tabs.py` | One gate per task. |

**Modified:**

| File | Change |
| --- | --- |
| `lianli_panel/apply.py` | `live_template_id()`; `brightness` folded into the `SetLcdMedia` entry; `apply_all()` and its stage machinery. |
| `lianli_panel/ring.py` | `set_lcd_brightness()`, poller unit helpers, `RingState` persistence. |
| `lianli_panel/snapshot.py` | `_thermal_active` replaced by `ring.poller_active` (DRY); snapshot gains the poller config and the ring's last-set state. |
| `lianli_panel/cli.py` | `list` uses `live_template_id`; `ring` gains `thermal`, `brightness` and `poller` subcommands. |
| `lianli_panel/gui/window.py` | `QTabWidget`; unified Apply; validate *warnings* surfaced; close prompt covers lighting dirtiness. |
| `lianli_panel/gui/forms.py` | `source_fields_for(src: dict)` so a bare source dict can drive a form. |
| `lianli_panel/gui/preview.py` | `ProbeWorker` — one-shot off-thread render for the authoritative sensor probe. |
| `docs/gui.md` | The two new tabs, and what Apply now means. |

---

## Task 1: The live template is the entry for THIS panel

Plan A's follow-up left this open by name: "`cli.py:77` and `window.py` read the live template from the *first* `lcds` entry rather than the one matching the panel's serial. Harmless with one LCD and no duplicates, wrong with either. Not fixed — flagged for a decision."

Both conditions are real on this machine. The array held two entries for the same serial for a full day before anyone noticed.

**Files:**
- Modify: `lianli_panel/apply.py` (add `live_template_id`, after `entry_key`)
- Modify: `lianli_panel/cli.py:77`
- Modify: `lianli_panel/gui/window.py:121`
- Test: `tests/test_apply.py`

**Interfaces:**
- Consumes: `apply.LCD_SERIAL`, the existing `entry_key` conventions.
- Produces: `apply.live_template_id(config: dict, serial: str) -> str | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_apply.py`:

```python
from lianli_panel.apply import live_template_id


def test_live_template_comes_from_the_entry_matching_the_serial():
    config = {"lcds": [
        {"serial": "hid:other", "template_id": "someone-elses"},
        {"serial": "hid:513b5a7acadc4203", "template_id": "gaming-dash"},
    ]}
    assert live_template_id(config, "hid:513b5a7acadc4203") == "gaming-dash"


def test_live_template_is_not_simply_the_first_entry():
    """The defect this function exists to fix: lcds[0] can belong to another
    device entirely."""
    config = {"lcds": [
        {"serial": "hid:other", "template_id": "someone-elses"},
        {"serial": "hid:513b5a7acadc4203", "template_id": "gaming-dash"},
    ]}
    assert live_template_id(config, "hid:513b5a7acadc4203") != "someone-elses"


def test_duplicate_entries_resolve_to_the_first_like_the_daemon_does():
    """AppConfig::load collapses duplicate serials keeping the FIRST. Reading
    the second would report a template the panel is not rendering."""
    config = {"lcds": [
        {"serial": "hid:513b5a7acadc4203", "template_id": "stale"},
        {"serial": "hid:513b5a7acadc4203", "template_id": "just-applied"},
    ]}
    assert live_template_id(config, "hid:513b5a7acadc4203") == "stale"


def test_no_entry_for_this_panel_is_none_not_a_guess():
    """lianli-gui wipes the array. Returning lcds[0] here would report another
    device's template as this panel's."""
    config = {"lcds": [{"serial": "hid:other", "template_id": "someone-elses"}]}
    assert live_template_id(config, "hid:513b5a7acadc4203") is None


def test_an_empty_or_missing_lcds_array_is_none():
    assert live_template_id({"lcds": []}, "hid:513b5a7acadc4203") is None
    assert live_template_id({}, "hid:513b5a7acadc4203") is None


def test_an_entry_with_no_template_id_is_none_not_a_keyerror():
    config = {"lcds": [{"serial": "hid:513b5a7acadc4203", "type": "sensor"}]}
    assert live_template_id(config, "hid:513b5a7acadc4203") is None
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
./.venv/bin/pytest tests/test_apply.py -v -k live_template
```

Expected: FAIL — `ImportError: cannot import name 'live_template_id'`.

- [ ] **Step 3: Implement it**

In `lianli_panel/apply.py`, immediately after `entry_key`:

```python
def live_template_id(config: dict, serial: str) -> str | None:
    """The template_id of the entry for THIS panel.

    NOT lcds[0]. The array can hold entries for other LCDs, and this daemon has
    been observed carrying two entries for the SAME serial for a day at a time
    (hazard 4 above). Reading the first entry is right only when there is
    exactly one LCD and no duplicate -- and the duplicate case is precisely the
    one where a wrong answer matters.

    When the serial appears more than once, the FIRST match wins, because that
    is what AppConfig::load does when it collapses duplicates. Mirroring the
    daemon's own rule is the point: this must report what the panel renders,
    not what the config wishes it rendered.

    Returns None when no entry matches -- which is the lianli-gui-wiped case,
    and is reported as "no entry for this panel" rather than papered over with
    another device's template.
    """
    for entry in config.get("lcds") or []:
        if entry.get("serial") == serial:
            return entry.get("template_id")
    return None
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_apply.py -v
```

Expected: PASS, including the pre-existing `test_apply.py` tests.

- [ ] **Step 5: Use it in the CLI**

`lianli_panel/cli.py`, replacing line 77:

```python
            live = apply_mod.live_template_id(config, apply_mod.LCD_SERIAL)
```

- [ ] **Step 6: Use it in the window**

`lianli_panel/gui/window.py`, replacing line 121 inside `load()`:

```python
        live = apply_mod.live_template_id(config, apply_mod.LCD_SERIAL)
```

`window.py` already imports `apply as apply_mod`, so no import change is needed.

- [ ] **Step 7: Run the full suite**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q
```

Expected: every test passes. `tests/test_gui_smoke.py`'s `CONFIG` uses serial `hid:513b5a7acadc4203`, which matches `apply.LCD_SERIAL`, so the smoke tests keep resolving `gaming-dash` as live.

- [ ] **Step 8: Commit**

```bash
git add lianli_panel/apply.py lianli_panel/cli.py lianli_panel/gui/window.py tests/test_apply.py
git commit -m "fix: read the live template from this panel's lcds entry

Both the CLI and the window took lcds[0], which is right only with one LCD
and no duplicate entries -- and the duplicate case is the one where being
wrong matters. Matches the daemon's own first-match-wins dedup rule.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

---

## Task 2: Lighting state, the diff, and what makes it invalid

The Lighting tab decides three things that are easy to get subtly wrong: which of the two brightnesses a control means, whether the poller owns the ring right now, and what actually needs sending. All three live here, in a module with no Qt and no I/O, so they can be tested exhaustively.

**Files:**
- Create: `lianli_panel/lighting.py`
- Test: `tests/test_lighting.py`

This module is core, not GUI. `apply.py` consumes it in Task 4, and core importing `lianli_panel.gui.*` would invert the layering for no gain — it has no more to do with Qt than `ring.py` does.

**Interfaces:**
- Consumes: `ring.ThermalConfig` (already exists, with `cool_c`, `hot_c`, `poll_ms`, `min_delta_c`, `force_refresh_s`, `brightness` and its `to_json`/`from_json`).
- Produces:
  - `lighting.MODES = ("off", "static", "thermal")`
  - `lighting.LightingState` — `mode: str`, `color: tuple[int,int,int]`, `ring_brightness: int`, `screen_brightness: int | None`, `thermal: ThermalConfig`; `to_json()`, `from_json(obj)`, `copy()`
  - `lighting.Problem(level: str, field: str, message: str)`
  - `lighting.problems(state) -> list[Problem]`
  - `lighting.LightingDiff` — `poller_config: bool`, `unit_action: str | None`, `ring_effect: bool`, `screen_brightness: bool`; property `empty: bool`
  - `lighting.diff(base, draft, *, poller_running: bool) -> LightingDiff`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_lighting.py`:

```python
"""The Lighting tab's whole decision surface, with no Qt and no daemon.

The rules being pinned here are the ones that produce a visible wrong result
rather than an error: sending SetRgbEffect while the poller owns the ring
(colour flashes then changes back), or setting the ring's brightness when the
user moved the screen's slider.
"""
import pytest

from lianli_panel import lighting
from lianli_panel.ring import ThermalConfig


def base(**kw):
    defaults = dict(mode="thermal", color=(255, 255, 255), ring_brightness=4,
                    screen_brightness=None, thermal=ThermalConfig())
    defaults.update(kw)
    return lighting.LightingState(**defaults)


# --- state -----------------------------------------------------------------


def test_state_round_trips_through_json():
    s = base(mode="static", color=(10, 20, 30), ring_brightness=2,
             screen_brightness=180,
             thermal=ThermalConfig(cool_c=40.0, hot_c=90.0))
    assert lighting.LightingState.from_json(s.to_json()) == s


def test_from_json_ignores_keys_it_does_not_know():
    obj = base().to_json()
    obj["invented_by_a_future_version"] = True
    assert lighting.LightingState.from_json(obj).mode == "thermal"


def test_copy_does_not_share_the_thermal_config():
    s = base()
    other = s.copy()
    other.thermal.hot_c = 99.0
    assert s.thermal.hot_c == 85.0


def test_screen_brightness_none_means_never_set():
    """The daemon omits config.lcds[].brightness while it is null, so None is
    'the panel is at its own default', not 'zero'."""
    assert base().screen_brightness is None


# --- validation ------------------------------------------------------------


def test_an_unknown_mode_is_an_error():
    problems = lighting.problems(base(mode="rainbow"))
    assert any(p.level == "error" and p.field == "mode" for p in problems)


def test_a_colour_component_above_255_is_an_error():
    problems = lighting.problems(base(mode="static", color=(300, 0, 0)))
    assert any(p.level == "error" and p.field == "color" for p in problems)


def test_ring_brightness_above_four_is_an_error():
    problems = lighting.problems(base(ring_brightness=9))
    assert any(p.level == "error" and p.field == "ring_brightness"
               for p in problems)


def test_screen_brightness_above_255_is_an_error():
    problems = lighting.problems(base(screen_brightness=300))
    assert any(p.level == "error" and p.field == "screen_brightness"
               for p in problems)


def test_cool_above_hot_is_an_error_naming_the_sweep():
    problems = lighting.problems(
        base(thermal=ThermalConfig(cool_c=90.0, hot_c=50.0)))
    match = [p for p in problems if p.field == "cool_c"]
    assert match and match[0].level == "error"
    assert "sweep" in match[0].message


def test_a_very_short_poll_interval_is_a_warning_not_a_block():
    """The poller re-reads its config on this cadence and streams nvidia-smi at
    it. 50ms is a bad idea, not an impossible one."""
    problems = lighting.problems(base(thermal=ThermalConfig(poll_ms=50)))
    match = [p for p in problems if p.field == "poll_ms"]
    assert match and match[0].level == "warning"


def test_a_valid_state_has_no_problems():
    assert lighting.problems(base()) == []


# --- diff ------------------------------------------------------------------


def test_no_change_needs_nothing_sent():
    d = lighting.diff(base(), base(), poller_running=True)
    assert d.empty


def test_editing_the_poller_config_marks_only_the_poller_config():
    draft = base(thermal=ThermalConfig(hot_c=90.0))
    d = lighting.diff(base(), draft, poller_running=True)
    assert d.poller_config
    assert d.unit_action is None
    assert not d.ring_effect


def test_switching_to_static_stops_the_poller_and_drives_the_ring():
    d = lighting.diff(base(mode="thermal"), base(mode="static"),
                      poller_running=True)
    assert d.unit_action == "stop"
    assert d.ring_effect


def test_switching_to_thermal_starts_the_poller_and_sends_no_effect():
    """In thermal mode the poller owns the ring. Anything this app sent would
    be overwritten within ~2s -- the user would see a flash, then a different
    colour, and reasonably conclude the app is broken."""
    d = lighting.diff(base(mode="static"), base(mode="thermal"),
                      poller_running=False)
    assert d.unit_action == "start"
    assert not d.ring_effect


def test_no_unit_action_when_the_poller_is_already_where_it_should_be():
    d = lighting.diff(base(mode="thermal"), base(mode="thermal"),
                      poller_running=True)
    assert d.unit_action is None


def test_the_poller_is_started_when_thermal_is_selected_but_it_is_down():
    """Nothing changed in the draft, but the unit died. Apply should fix it."""
    d = lighting.diff(base(mode="thermal"), base(mode="thermal"),
                      poller_running=False)
    assert d.unit_action == "start"


def test_staying_static_but_changing_colour_re_sends_the_effect():
    d = lighting.diff(base(mode="static", color=(255, 0, 0)),
                      base(mode="static", color=(0, 255, 0)),
                      poller_running=False)
    assert d.ring_effect
    assert d.unit_action is None


def test_staying_static_with_no_change_sends_nothing():
    d = lighting.diff(base(mode="static"), base(mode="static"),
                      poller_running=False)
    assert not d.ring_effect


def test_stopping_the_poller_always_re_sends_the_effect():
    """The poller was just driving the ring, so whatever it last pushed is what
    is lit -- the app's own last-set value is not on the hardware any more, and
    'nothing changed in the draft' is not a reason to skip the send."""
    d = lighting.diff(base(mode="static", color=(255, 0, 0)),
                      base(mode="static", color=(255, 0, 0)),
                      poller_running=True)
    assert d.unit_action == "stop"
    assert d.ring_effect


def test_ring_brightness_change_re_sends_the_effect_in_static_mode():
    d = lighting.diff(base(mode="static", ring_brightness=4),
                      base(mode="static", ring_brightness=1),
                      poller_running=False)
    assert d.ring_effect


def test_ring_brightness_change_in_thermal_mode_is_poller_config_not_an_effect():
    """thermal-rgb.json carries its own brightness; the poller applies it on
    its next push. Sending SetRgbEffect here would be overwritten anyway."""
    d = lighting.diff(
        base(mode="thermal", thermal=ThermalConfig(brightness=4)),
        base(mode="thermal", thermal=ThermalConfig(brightness=1)),
        poller_running=True)
    assert d.poller_config
    assert not d.ring_effect


def test_screen_brightness_is_tracked_separately_from_the_ring():
    d = lighting.diff(base(screen_brightness=100),
                      base(screen_brightness=200), poller_running=True)
    assert d.screen_brightness
    assert not d.ring_effect


def test_setting_screen_brightness_for_the_first_time_counts_as_a_change():
    d = lighting.diff(base(screen_brightness=None),
                      base(screen_brightness=200), poller_running=True)
    assert d.screen_brightness


def test_clearing_screen_brightness_sends_nothing():
    """There is no 'unset the brightness' call. Leaving it alone is correct."""
    d = lighting.diff(base(screen_brightness=200),
                      base(screen_brightness=None), poller_running=True)
    assert not d.screen_brightness


def test_the_module_does_not_import_pyside6():
    """This module is dispatchable precisely because it has no display
    dependency. An accidental PySide6 import moves it out of Codex's reach."""
    assert "PySide6" not in open(lighting.__file__).read()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
./.venv/bin/pytest tests/test_lighting.py -v
```

Expected: FAIL — `ImportError: cannot import name 'lighting' from 'lianli_panel'`.

- [ ] **Step 3: Implement the module**

Create `lianli_panel/lighting.py`:

```python
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
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_lighting.py -v
```

Expected: PASS, 25 tests.

- [ ] **Step 5: Commit**

```bash
git add lianli_panel/lighting.py tests/test_lighting.py
git commit -m "feat: add Qt-free lighting state, validation and apply diff

Mode ownership is the load-bearing rule: in thermal mode the poller owns the
ring and this app must send no effect at all, and after a stop the ring is
showing whatever the poller last pushed, so an unchanged draft still needs
re-sending.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

---

## Task 3: Ring, brightness and unit primitives

Everything that talks to the ring, the backlight, or systemd lands here, so `apply.py` can orchestrate without knowing any of the device-level traps. The systemd calls take an injectable `runner` because the global constraints forbid tests shelling out to `systemctl`.

**Files:**
- Modify: `lianli_panel/ring.py`
- Modify: `lianli_panel/snapshot.py:27-35` (replace `_thermal_active`) and `:50-59` (payload)
- Test: `tests/test_ring.py`, `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `ring.find_ring`, `ring._apply`, `ring.set_static`, `ring.set_off`, `ring.ThermalConfig` (all existing).
- Produces:
  - `ring.THERMAL_UNIT = "lianli-thermal-rgb.service"`, `ring.LCD_BRIGHTNESS_MAX = 255`
  - `ring.set_lcd_brightness(client, device_id: str, brightness: int) -> None`
  - `ring.set_mode(client, mode: str, color=(255,255,255), brightness: int = 4) -> None`
  - `ring.poller_active(runner=None) -> bool`
  - `ring.stop_poller(runner=None) -> None`, `ring.start_poller(runner=None) -> None`
  - `ring.RingState(mode, color, brightness, set_at)`, `ring.load_ring_state(path=None)`, `ring.save_ring_state(state, path=None)`

- [ ] **Step 1: Write the failing tests for brightness and modes**

Append to `tests/test_ring.py`:

```python
import pytest

from lianli_panel import ring
from tests.conftest import FakeClient

RING_DEVICE = {"device_id": "hid:0416:8050:1-12.3", "has_rgb": True,
               "pid": 0x8050}
LCD_BARE_ID = "hid:513b5a7acadc4203"


def ring_client():
    return FakeClient({"ListDevices": [RING_DEVICE], "SetRgbEffect": None,
                       "SetLcdBrightness": {"applied": True}})


def test_set_lcd_brightness_uses_the_bare_device_id():
    """THE trap this function exists to contain. SetLcdMedia wants
    "serial:hid:..."; SetLcdBrightness wants the bare "hid:..." because it is
    matched against ActiveTarget::device_identity. Getting it backwards fails
    SILENTLY -- the reply is still {"applied": true}."""
    client = ring_client()
    ring.set_lcd_brightness(client, LCD_BARE_ID, 180)
    method, params = client.calls[-1]
    assert method == "SetLcdBrightness"
    assert params["device_id"] == LCD_BARE_ID
    assert not params["device_id"].startswith("serial:")
    assert params["brightness"] == 180


def test_set_lcd_brightness_rejects_a_value_above_255():
    with pytest.raises(ValueError, match="0-255"):
        ring.set_lcd_brightness(ring_client(), LCD_BARE_ID, 300)


def test_set_lcd_brightness_rejects_a_negative_value():
    with pytest.raises(ValueError, match="0-255"):
        ring.set_lcd_brightness(ring_client(), LCD_BARE_ID, -1)


def test_set_mode_off_sends_the_off_effect():
    client = ring_client()
    ring.set_mode(client, "off")
    method, params = client.calls[-1]
    assert method == "SetRgbEffect"
    assert params["effect"]["mode"] == "Off"


def test_set_mode_static_sends_the_colour_and_brightness():
    client = ring_client()
    ring.set_mode(client, "static", (10, 20, 30), 2)
    _, params = client.calls[-1]
    assert params["effect"]["mode"] == "Static"
    assert params["effect"]["colors"] == [[10, 20, 30]]
    assert params["effect"]["brightness"] == 2


def test_set_mode_thermal_refuses_rather_than_fighting_the_poller():
    """A caller that reaches here has a bug: in thermal mode the poller owns
    the ring and re-drives it every ~2s, so anything sent flashes and is gone."""
    client = ring_client()
    with pytest.raises(ValueError, match="poller"):
        ring.set_mode(client, "thermal")
    assert client.methods() == []
```

- [ ] **Step 2: Write the failing tests for the poller unit**

Append to `tests/test_ring.py`:

```python
class FakeRun:
    """Stands in for subprocess.run. Tests must never shell out to systemctl."""

    def __init__(self, stdout="", returncode=0, raises=None):
        self.stdout, self.returncode, self.raises = stdout, returncode, raises
        self.stderr = ""
        self.calls = []

    def __call__(self, args, timeout=None):
        self.calls.append(list(args))
        if self.raises is not None:
            raise self.raises
        return self


def test_poller_active_is_true_only_for_the_word_active():
    assert ring.poller_active(runner=FakeRun("active\n")) is True
    assert ring.poller_active(runner=FakeRun("inactive\n")) is False
    assert ring.poller_active(runner=FakeRun("failed\n")) is False


def test_poller_active_asks_the_user_manager_about_the_right_unit():
    run = FakeRun("active\n")
    ring.poller_active(runner=run)
    assert run.calls == [["systemctl", "--user", "is-active",
                          "lianli-thermal-rgb.service"]]


def test_poller_active_is_false_when_systemctl_cannot_be_run():
    """An unreadable status is not a running poller. Guessing 'active' here
    would make Apply skip the start it needs."""
    assert ring.poller_active(runner=FakeRun(raises=OSError("no systemctl"))) \
        is False


def test_stop_poller_stops_the_unit():
    run = FakeRun()
    ring.stop_poller(runner=run)
    assert run.calls == [["systemctl", "--user", "stop",
                          "lianli-thermal-rgb.service"]]


def test_start_poller_starts_the_unit():
    run = FakeRun()
    ring.start_poller(runner=run)
    assert run.calls == [["systemctl", "--user", "start",
                          "lianli-thermal-rgb.service"]]


def test_a_failing_systemctl_raises_and_names_the_unit():
    run = FakeRun(returncode=1)
    run.stderr = "Unit lianli-thermal-rgb.service not found."
    with pytest.raises(RuntimeError, match="lianli-thermal-rgb.service"):
        ring.stop_poller(runner=run)
```

- [ ] **Step 3: Write the failing tests for the ring's last-set state**

Append to `tests/test_ring.py`:

```python
def test_ring_state_round_trips(tmp_path):
    path = tmp_path / "ring-state.json"
    ring.save_ring_state(ring.RingState("static", (1, 2, 3), 3), path)
    loaded = ring.load_ring_state(path)
    assert loaded.mode == "static"
    assert loaded.color == (1, 2, 3)
    assert loaded.brightness == 3


def test_saving_stamps_when_it_was_set(tmp_path):
    """The Lighting tab shows this timestamp beside the colour, because
    'what this app last sent' is a claim that needs a time on it."""
    path = tmp_path / "ring-state.json"
    ring.save_ring_state(ring.RingState("static", (1, 2, 3), 3), path)
    assert ring.load_ring_state(path).set_at is not None


def test_a_missing_state_file_reads_as_never_set(tmp_path):
    state = ring.load_ring_state(tmp_path / "nothing.json")
    assert state.set_at is None


def test_a_corrupt_state_file_reads_as_never_set(tmp_path):
    path = tmp_path / "ring-state.json"
    path.write_text("{ this is not json")
    assert ring.load_ring_state(path).set_at is None
```

- [ ] **Step 4: Run the tests to verify they fail**

```bash
./.venv/bin/pytest tests/test_ring.py -v
```

Expected: FAIL — `AttributeError: module 'lianli_panel.ring' has no attribute 'set_lcd_brightness'`.

- [ ] **Step 5: Implement the brightness and mode calls**

In `lianli_panel/ring.py`, add `import subprocess` and `from datetime import datetime` to the imports, then add after `set_off`:

```python
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
```

- [ ] **Step 6: Implement the poller unit helpers**

Add to `lianli_panel/ring.py`:

```python
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
```

- [ ] **Step 7: Implement the ring's last-set state**

Add to `lianli_panel/ring.py`:

```python
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
```

- [ ] **Step 8: Make the snapshot use these, and stop it shelling out in tests**

`lianli_panel/snapshot.py` has its own `_thermal_active` that shells out to the same command — and because `apply_now` snapshots first, **every existing apply test in `tests/test_gui_smoke.py` already runs `systemctl` for real**, against the global constraint. Replace it with the injectable version, and record the poller's config and the ring's last-set state so a snapshot describes the lighting it claims to describe.

Delete lines 27-35 (`def _thermal_active(): ...`), add `from . import ring` to the imports, and change `take`:

```python
def take(client, root: Path | None = None, keep: int = 20,
         poller_active=None) -> Path:
    """`poller_active` is injectable for the same reason apply.LightingOps is:
    the real one runs systemctl, and the GUI's apply path snapshots first, so
    leaving it hardcoded makes every apply test shell out."""
    root = Path(root) if root is not None else SNAPSHOT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    poller_active = poller_active or ring.poller_active

    templates, digest = read_templates(client)
    config = client.call("GetConfig") or {}

    try:
        rgb_state = json.loads(RGB_STATE_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        rgb_state = None

    last_set = ring.load_ring_state()
    payload = {
        "taken_at": datetime.now().astimezone().isoformat(),
        "templates": templates,
        "templates_hash": digest,
        "lcds": config.get("lcds") or [],
        "rgb_config": config.get("rgb") or {},
        "rgb_state_file": rgb_state,
        "thermal_service_active": poller_active(),
        "thermal_config": ring.load_thermal().to_json(),
        "ring_last_set": {
            "mode": last_set.mode,
            "color": list(last_set.color),
            "brightness": last_set.brightness,
            "set_at": last_set.set_at,
        },
        "note": NOTE,
    }
```

The rest of `take` is unchanged.

Add to `tests/test_snapshot.py`:

```python
def test_a_snapshot_records_the_poller_config_and_the_rings_last_set_state(
        tmp_path, monkeypatch):
    """Plan A's snapshot recorded only whether the unit was active, which is
    not enough to describe -- let alone restore -- the lighting."""
    from lianli_panel import ring, snapshot
    monkeypatch.setattr(ring, "load_thermal",
                        lambda path=None: ring.ThermalConfig(hot_c=90.0))
    monkeypatch.setattr(ring, "load_ring_state",
                        lambda path=None: ring.RingState("static", (1, 2, 3), 2,
                                                         "2026-09-05T12:00:00"))
    client = FakeClient({"GetLcdTemplates": [], "GetConfig": {"lcds": []}})
    data = snapshot.load(snapshot.take(client, root=tmp_path,
                                       poller_active=lambda: True))
    assert data["thermal_service_active"] is True
    assert data["thermal_config"]["hot_c"] == 90.0
    assert data["ring_last_set"]["mode"] == "static"
    assert data["ring_last_set"]["color"] == [1, 2, 3]
```

- [ ] **Step 9: Run the tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_ring.py tests/test_snapshot.py -v
```

Expected: PASS. Then the whole suite:

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q
```

- [ ] **Step 10 (CONTROLLER — the first real `SetLcdBrightness`)**

Codex cannot reach the socket. This is the first time this call has ever been made on this machine, and the whole point of step 5's docstring is that it will report success either way — so the *journal* is the evidence, not the reply.

First, a deliberately wrong key, to confirm the failure mode is what the source says:

```bash
journalctl _COMM=lianli-daemon -f &
PYTHONPATH=. ./.venv/bin/python -c "
from lianli_panel.ipc import Client
from lianli_panel import ring
ring.set_lcd_brightness(Client(), 'serial:hid:513b5a7acadc4203', 200)
print('reply was accepted')
"
```

Expected: prints `reply was accepted`, and the journal shows `Failed to set LCD brightness` or nothing at all — **no error reaches Python.** If instead this errors, the daemon differs from the source read for this plan; stop and record that in the ledger before continuing.

Then the correct key, and look at the screen:

```bash
PYTHONPATH=. ./.venv/bin/python -c "
from lianli_panel.ipc import Client
from lianli_panel import ring, apply as a
c = Client()
ring.set_lcd_brightness(c, a.find_lcd(c), 60)
"
```

Expected: **the panel visibly dims.** Then restore it:

```bash
PYTHONPATH=. ./.venv/bin/python -c "
from lianli_panel.ipc import Client
from lianli_panel import ring, apply as a
c = Client()
ring.set_lcd_brightness(c, a.find_lcd(c), 255)
"
```

Record in the ledger: whether the wrong key really was silent, what the panel's default brightness looks like, and the value that reads as "normal" — Task 6's slider needs a sensible default and there is no read-back for it until Task 4 writes one into the config.

- [ ] **Step 11: Commit**

```bash
git add lianli_panel/ring.py lianli_panel/snapshot.py tests/test_ring.py tests/test_snapshot.py
git commit -m "feat: add LCD brightness, poller unit control and ring state

SetLcdBrightness takes the BARE device id, the opposite of SetLcdMedia, and
replies {applied: true} before it touches the device -- so it cannot report
failure and its success is not evidence. Both are documented at the call site.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

---

## Task 4: The staged transaction

One Apply now commits templates, the poller's config, the poller's unit state, the ring, and both brightnesses. That is five things that fail independently, so the order is chosen so that **nothing touches the template set until every cheap, reversible, verifiable thing has already succeeded.**

Every side effect arrives through an injected `LightingOps`, which is what makes this — the most consequential code in the plan — fully testable with no daemon, no systemd, and no filesystem.

**Files:**
- Modify: `lianli_panel/apply.py`
- Test: `tests/test_apply_all.py`

**Interfaces:**
- Consumes: `apply.apply_templates`, `apply.read_templates`, `apply.find_lcd`, `apply.entry_key`, `lighting.diff`, `lighting.problems`, `ring.*` from Task 3.
- Produces:
  - `apply.StageResult(name: str, status: str, detail: str = "")` — status is one of `done`, `skipped`, `rolled back`, `failed`, `unverifiable`
  - `apply.PartialApply(ApplyFailed)` with a `.stages: list[StageResult]`
  - `apply.LightingOps` and `apply.default_ops()`
  - `apply.apply_templates(..., brightness: int | None = None)` — new keyword
  - `apply.apply_all(client, *, templates, live_id, base_lighting, draft_lighting, base_hash=None, device_id=None, lcd_entry_fallback=None, ops=None) -> list[StageResult]`
- Stage names, used verbatim by Task 8's details dialog: `"poller config"`, `"poller unit"`, `"ring effect"`, `"templates"`, `"screen brightness"`, `"ring state file"`.

- [ ] **Step 1: Write the test harness**

Create `tests/test_apply_all.py`:

```python
"""The unified Apply.

ORDER is what most of these tests are about, and a per-mock call list cannot
express "before", so the client and every lighting op append to ONE shared log.
The two orderings that produce a visibly wrong result rather than an error:

  * the poller must be STOPPED before the ring is driven, or it overwrites the
    colour within ~2s and the user sees their choice flash and change
  * nothing may touch the template set until every lighting stage has succeeded,
    because the template set is the only thing here whose rollback is a second
    whole-set write
"""
import pytest

from lianli_panel import apply as apply_mod
from lianli_panel import lighting
from lianli_panel.ipc import DaemonError
from lianli_panel.ring import ThermalConfig
from tests.conftest import FakeClient

LCD_BARE_ID = "hid:513b5a7acadc4203"

TEMPLATES = [{"id": "dash", "name": "Dash", "base_width": 1920,
              "base_height": 480, "rotated": True,
              "background": {"type": "color", "rgb": [0, 0, 0, 255]},
              "widgets": []}]

CONFIG = {"lcds": [{"serial": LCD_BARE_ID, "type": "custom",
                    "template_id": "dash", "orientation": 90.0}]}


def state(**kw):
    defaults = dict(mode="thermal", color=(255, 255, 255), ring_brightness=4,
                    screen_brightness=None, thermal=ThermalConfig())
    defaults.update(kw)
    return lighting.LightingState(**defaults)


def harness(*, poller_running=True, fail=None, responses=None):
    """Returns (client, ops, log, world).

    `fail` names one operation -- an IPC method or an op name -- that raises.
    `world` is the mutable state the ops read and write, so a test can assert
    on what was actually left behind after a rollback.
    """
    log = []
    world = {"thermal": ThermalConfig(), "running": poller_running,
             "ring": None, "brightness": None, "ring_state": None}

    class LoggingClient(FakeClient):
        def call(self, method, params=None):
            log.append(("ipc", method))
            if fail == method:
                raise DaemonError(f"{method} refused")
            return super().call(method, params)

    base = {"GetLcdTemplates": TEMPLATES, "GetConfig": CONFIG,
            "ListDevices": [{"device_id": LCD_BARE_ID, "has_lcd": True}],
            "SetLcdTemplates": None, "SetLcdMedia": None,
            "SetRgbEffect": None, "SetLcdBrightness": {"applied": True}}
    base.update(responses or {})
    client = LoggingClient(base)

    def guard(name):
        if fail == name:
            raise RuntimeError(f"{name} refused")

    def read_thermal():
        return world["thermal"]

    def write_thermal(cfg):
        log.append(("write_thermal", cfg.hot_c))
        guard("write_thermal")
        world["thermal"] = cfg

    def stop_poller():
        log.append(("stop_poller", None))
        guard("stop_poller")
        world["running"] = False

    def start_poller():
        log.append(("start_poller", None))
        guard("start_poller")
        world["running"] = True

    def set_ring(_client, mode, color, brightness):
        log.append(("set_ring", mode))
        guard("set_ring")
        world["ring"] = (mode, color, brightness)

    def set_lcd_brightness(_client, device_id, value):
        log.append(("set_lcd_brightness", device_id))
        guard("set_lcd_brightness")
        world["brightness"] = (device_id, value)

    def save_ring_state(mode, color, brightness):
        log.append(("save_ring_state", mode))
        world["ring_state"] = (mode, color, brightness)

    ops = apply_mod.LightingOps(
        read_thermal=read_thermal, write_thermal=write_thermal,
        poller_active=lambda: world["running"],
        stop_poller=stop_poller, start_poller=start_poller,
        set_ring=set_ring, set_lcd_brightness=set_lcd_brightness,
        save_ring_state=save_ring_state)
    return client, ops, log, world


def run(client, ops, base, draft, **kw):
    return apply_mod.apply_all(
        client, templates=TEMPLATES, live_id="dash", base_lighting=base,
        draft_lighting=draft, device_id=LCD_BARE_ID, ops=ops, **kw)
```

- [ ] **Step 2: Write the ordering and stage tests**

Append to `tests/test_apply_all.py`:

```python
def test_no_lighting_change_sends_only_the_template_calls():
    client, ops, log, _ = harness()
    run(client, ops, state(), state())
    assert ("set_ring", "static") not in log
    assert ("stop_poller", None) not in log
    assert ("ipc", "SetLcdTemplates") in log
    assert ("ipc", "SetLcdMedia") in log


def test_the_poller_config_is_written_before_the_unit_is_stopped():
    """Otherwise the poller gets one last cycle with the old config, which is
    visible on the ring as a colour that briefly disagrees with the settings."""
    client, ops, log, _ = harness()
    run(client, ops, state(),
        state(mode="static", thermal=ThermalConfig(hot_c=90.0)))
    assert log.index(("write_thermal", 90.0)) < log.index(("stop_poller", None))


def test_the_poller_is_stopped_before_the_ring_is_driven():
    """THE ordering rule. Driving first means the poller overwrites the colour
    within ~2s and the user watches their choice disappear."""
    client, ops, log, _ = harness(poller_running=True)
    run(client, ops, state(), state(mode="static", color=(255, 0, 0)))
    assert log.index(("stop_poller", None)) < log.index(("set_ring", "static"))


def test_every_lighting_stage_happens_before_the_template_set_is_touched():
    client, ops, log, _ = harness()
    run(client, ops, state(), state(mode="static", color=(255, 0, 0)))
    assert log.index(("set_ring", "static")) < log.index(("ipc", "SetLcdTemplates"))


def test_thermal_mode_never_drives_the_ring_itself():
    client, ops, log, _ = harness(poller_running=False)
    run(client, ops, state(mode="static"), state(mode="thermal"))
    assert ("start_poller", None) in log
    assert not any(e[0] == "set_ring" for e in log)


def test_the_stage_list_reports_every_stage_including_the_skipped_ones():
    client, ops, _, _ = harness()
    stages = run(client, ops, state(), state())
    reported = {s.name for s in stages}
    assert reported >= {"poller config", "poller unit", "ring effect",
                        "templates", "screen brightness"}
    assert {s.status for s in stages if s.name == "ring effect"} == {"skipped"}
```

- [ ] **Step 3: Write the brightness tests**

Append to `tests/test_apply_all.py`:

```python
def test_brightness_is_folded_into_the_entry_setlcdmedia_already_sends():
    """This is what makes brightness PERSIST. SetLcdBrightness alone is lost on
    the next daemon restart; config.lcds[].brightness is applied at target
    creation."""
    client, ops, _, _ = harness()
    run(client, ops, state(), state(screen_brightness=200))
    media = [p for m, p in client.calls if m == "SetLcdMedia"][-1]
    assert media["config"]["brightness"] == 200


def test_setlcdmedia_still_uses_the_serial_prefixed_key():
    """Regression guard for the bug that cost a whole session. The two calls in
    this transaction take DIFFERENT key formats."""
    client, ops, _, _ = harness()
    run(client, ops, state(), state(screen_brightness=200))
    media = [p for m, p in client.calls if m == "SetLcdMedia"][-1]
    assert media["device_id"] == f"serial:{LCD_BARE_ID}"


def test_setlcdbrightness_uses_the_bare_id_not_the_serial_prefixed_one():
    client, ops, _, world = harness()
    run(client, ops, state(), state(screen_brightness=200))
    assert world["brightness"] == (LCD_BARE_ID, 200)


def test_the_brightness_stage_says_it_is_unverifiable():
    """It replies ok before touching the device. Reporting it as 'done' would
    be a claim this app cannot support."""
    client, ops, _, _ = harness()
    stages = run(client, ops, state(), state(screen_brightness=200))
    stage = [s for s in stages if s.name == "screen brightness"][0]
    assert stage.status == "unverifiable"
    assert "persisted" in stage.detail


def test_unchanged_brightness_is_not_re_sent():
    client, ops, _, world = harness()
    run(client, ops, state(screen_brightness=200), state(screen_brightness=200))
    assert world["brightness"] is None
```

- [ ] **Step 4: Write the rollback tests**

Append to `tests/test_apply_all.py`:

```python
def test_a_failed_ring_effect_rolls_back_the_unit_and_the_config():
    client, ops, log, world = harness(poller_running=True, fail="set_ring")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(),
            state(mode="static", thermal=ThermalConfig(hot_c=90.0)))
    assert world["running"] is True            # the unit was restarted
    assert world["thermal"].hot_c == 85.0      # the file was rewritten
    assert ("ipc", "SetLcdTemplates") not in log


def test_a_failed_template_stage_rolls_back_every_lighting_stage():
    client, ops, _, world = harness(poller_running=True, fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply) as exc:
        run(client, ops, state(), state(mode="static", color=(255, 0, 0)))
    assert world["running"] is True
    assert {s.name for s in exc.value.stages if s.status == "rolled back"} \
        >= {"poller unit"}


def test_rolling_back_the_ring_re_sends_the_previous_static_colour():
    client, ops, _, world = harness(poller_running=False, fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(mode="static", color=(255, 0, 0)),
            state(mode="static", color=(0, 255, 0)))
    assert world["ring"][0] == "static"
    assert world["ring"][1] == (255, 0, 0)


def test_rolling_back_to_thermal_restarts_the_poller_rather_than_sending():
    """There is nothing to re-send when the poller owned the ring before --
    restarting it IS the undo, and set_mode would refuse 'thermal' anyway."""
    client, ops, log, world = harness(poller_running=True, fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(mode="thermal"), state(mode="static"))
    assert world["running"] is True
    assert [e for e in log if e[0] == "set_ring"] == [("set_ring", "static")]


def test_a_partial_apply_names_what_was_rolled_back():
    client, ops, _, _ = harness(fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply) as exc:
        run(client, ops, state(), state(mode="static"))
    assert any(s.status == "rolled back" for s in exc.value.stages)


def test_the_ring_state_file_is_written_only_on_full_success():
    client, ops, _, world = harness()
    run(client, ops, state(), state(mode="static", color=(1, 2, 3)))
    assert world["ring_state"] == ("static", (1, 2, 3), 4)


def test_the_ring_state_file_is_not_written_when_a_stage_failed():
    client, ops, _, world = harness(fail="SetLcdMedia")
    with pytest.raises(apply_mod.PartialApply):
        run(client, ops, state(), state(mode="static"))
    assert world["ring_state"] is None
```

- [ ] **Step 5: Write the pre-flight tests**

Append to `tests/test_apply_all.py`:

```python
def test_a_conflict_aborts_before_any_lighting_stage_runs():
    """A conflict must not leave the poller stopped. The check therefore
    happens before stage 1, not inside apply_templates where it used to be the
    only check."""
    client, ops, log, world = harness()
    with pytest.raises(apply_mod.ConflictError):
        run(client, ops, state(), state(mode="static"),
            base_hash="a-hash-from-another-lifetime")
    assert world["running"] is True
    assert not any(e[0] in ("stop_poller", "set_ring", "write_thermal")
                   for e in log)


def test_invalid_lighting_is_refused_before_anything_is_sent():
    client, ops, log, _ = harness()
    with pytest.raises(apply_mod.ApplyFailed, match="cool"):
        run(client, ops, state(),
            state(thermal=ThermalConfig(cool_c=90.0, hot_c=50.0)))
    assert log == []


def test_a_live_id_outside_the_set_is_refused_before_anything_is_sent():
    client, ops, log, _ = harness()
    with pytest.raises(apply_mod.ApplyFailed, match="not in the set"):
        apply_mod.apply_all(client, templates=TEMPLATES, live_id="ghost",
                            base_lighting=state(), draft_lighting=state(),
                            device_id=LCD_BARE_ID, ops=ops)
    assert log == []


def test_a_lighting_warning_does_not_block_the_apply():
    """poll_ms below the floor is a bad idea, not an impossible one. Blocking
    on warnings would make the app refuse settings the poller accepts."""
    client, ops, _, world = harness()
    run(client, ops, state(), state(thermal=ThermalConfig(poll_ms=50)))
    assert world["thermal"].poll_ms == 50
```

- [ ] **Step 6: Run the tests to verify they fail**

```bash
./.venv/bin/pytest tests/test_apply_all.py -v
```

Expected: FAIL — `AttributeError: module 'lianli_panel.apply' has no attribute 'LightingOps'`.

- [ ] **Step 7: Add the brightness keyword to `apply_templates`**

In `lianli_panel/apply.py`, change the signature and the entry construction:

```python
def apply_templates(client, templates: list[dict], live_id: str, *,
                    base_hash: str | None = None,
                    device_id: str | None = None,
                    lcd_entry_fallback: dict | None = None,
                    brightness: int | None = None) -> None:
```

and inside, after `entry["template_id"] = live_id`:

```python
    if brightness is not None:
        # The PERSISTENT half of brightness. LcdConfig.brightness is applied at
        # target creation, so this is what survives a daemon restart;
        # SetLcdBrightness is only the "apply it now" half and persists
        # nothing. Riding the entry SetLcdMedia already sends means brightness
        # inherits a write path that is verified rather than inventing a
        # second one.
        entry["brightness"] = int(brightness)
```

- [ ] **Step 8: Implement the stage machinery**

Add to `lianli_panel/apply.py`, after `apply_templates`:

```python
@dataclass
class StageResult:
    """What one stage of a unified apply did.

    status is one of:
      done          it happened and the result can be read back
      unverifiable  it was sent and the daemon cannot tell us whether it landed
      skipped       nothing to do
      rolled back   it happened, a later stage failed, and it was undone
      failed        it did not happen, or its rollback did not
    """
    name: str
    status: str
    detail: str = ""


class PartialApply(ApplyFailed):
    """An apply stopped partway. `stages` says exactly how far it got."""

    def __init__(self, message: str, stages: list[StageResult]) -> None:
        super().__init__(message)
        self.stages = stages


@dataclass
class LightingOps:
    """Every side effect apply_all can have, injected.

    This exists so the ordering rules -- which are the whole point of the
    staged design -- can be tested without systemd, a daemon, or a filesystem.
    """
    read_thermal: Callable[[], Any]
    write_thermal: Callable[[Any], None]
    poller_active: Callable[[], bool]
    stop_poller: Callable[[], None]
    start_poller: Callable[[], None]
    set_ring: Callable[[Any, str, tuple, int], None]
    set_lcd_brightness: Callable[[Any, str, int], None]
    save_ring_state: Callable[[str, tuple, int], None]


def default_ops() -> LightingOps:
    from . import ring
    return LightingOps(
        read_thermal=ring.load_thermal,
        write_thermal=ring.save_thermal,
        poller_active=ring.poller_active,
        stop_poller=ring.stop_poller,
        start_poller=ring.start_poller,
        set_ring=ring.set_mode,
        set_lcd_brightness=ring.set_lcd_brightness,
        save_ring_state=lambda mode, color, brightness: ring.save_ring_state(
            ring.RingState(mode=mode, color=color, brightness=brightness)),
    )
```

Add `from dataclasses import dataclass` and `from typing import Any, Callable` to the imports at the top of `apply.py`.

- [ ] **Step 9: Implement `apply_all`**

Add to `lianli_panel/apply.py`:

```python
def apply_all(client, *, templates: list[dict], live_id: str,
              base_lighting, draft_lighting,
              base_hash: str | None = None,
              device_id: str | None = None,
              lcd_entry_fallback: dict | None = None,
              ops: LightingOps | None = None) -> list[StageResult]:
    """Commit templates and lighting as one staged transaction.

    ORDER IS THE DESIGN. Stages run cheap-and-reversible first and
    dangerous-and-verified last, so that:

      * nothing touches the template set until every lighting stage has already
        succeeded -- the template set is the only thing here whose rollback is
        itself a whole-set write;
      * the poller is stopped BEFORE the ring is driven, or it overwrites the
        colour within ~2s and the user watches their choice disappear;
      * the poller's config is written BEFORE it is stopped or started, so it
        never gets a cycle with settings the user has already changed.

    Two stages are honest about not being verifiable, rather than reporting
    success they cannot support: the ring has no read-back at all, and
    SetLcdBrightness replies ok before it touches the device.
    """
    from . import lighting as lighting_mod

    ops = ops or default_ops()

    if not any(t.get("id") == live_id for t in templates):
        raise ApplyFailed(f"live template {live_id!r} is not in the set being sent")

    errors = [p for p in lighting_mod.problems(draft_lighting)
              if p.level == "error"]
    if errors:
        raise ApplyFailed("the lighting settings are invalid: "
                          + "; ".join(f"{p.field}: {p.message}" for p in errors))

    device_id = device_id or find_lcd(client)

    # BEFORE stage 1, not inside apply_templates. A conflict must not leave the
    # poller stopped and the ring driven by an apply that then refuses to run.
    _, current_hash = read_templates(client)
    if base_hash is not None and current_hash != base_hash:
        raise ConflictError(
            "the daemon's template set changed since this draft was opened — "
            "another process (apply.sh, lianli-gui, or a second editor) wrote to "
            "it. Applying now would discard that change.")

    plan = lighting_mod.diff(base_lighting, draft_lighting,
                             poller_running=ops.poller_active())

    stages: list[StageResult] = []
    undo: list[tuple[str, Callable[[], None]]] = []

    def unwind() -> None:
        for name, action in reversed(undo):
            try:
                action()
            except Exception as exc:
                stages.append(StageResult(name, "failed",
                                          f"rollback failed: {exc}"))
            else:
                stages.append(StageResult(name, "rolled back"))

    # --- L1: the poller's config file --------------------------------------
    if plan.poller_config:
        previous_thermal = ops.read_thermal()
        try:
            ops.write_thermal(draft_lighting.thermal)
        except Exception as exc:
            stages.append(StageResult("poller config", "failed", str(exc)))
            raise PartialApply(
                f"could not write the poller's config ({exc}). Nothing else was "
                "changed and the template set was not touched.", stages)
        stages.append(StageResult("poller config", "done"))
        undo.append(("poller config",
                     lambda: ops.write_thermal(previous_thermal)))
    else:
        stages.append(StageResult("poller config", "skipped"))

    # --- L2: the poller's unit ---------------------------------------------
    if plan.unit_action:
        forward = ops.stop_poller if plan.unit_action == "stop" else ops.start_poller
        inverse = ops.start_poller if plan.unit_action == "stop" else ops.stop_poller
        try:
            forward()
        except Exception as exc:
            stages.append(StageResult("poller unit", "failed", str(exc)))
            unwind()
            raise PartialApply(
                f"could not {plan.unit_action} the thermal poller ({exc}). The "
                "template set was not touched.", stages)
        stages.append(StageResult("poller unit", "done", plan.unit_action))
        undo.append(("poller unit", inverse))
    else:
        stages.append(StageResult("poller unit", "skipped"))

    # --- L3: the ring itself ------------------------------------------------
    if plan.ring_effect:
        try:
            ops.set_ring(client, draft_lighting.mode,
                         tuple(draft_lighting.color),
                         draft_lighting.ring_brightness)
        except Exception as exc:
            stages.append(StageResult("ring effect", "failed", str(exc)))
            unwind()
            raise PartialApply(
                f"the ring rejected the effect ({exc}). The template set was "
                "not touched.", stages)
        stages.append(StageResult(
            "ring effect", "done",
            "sent — the ring has no read-back, so this is what was sent, not "
            "proof of what is lit"))
        if base_lighting.mode in ("static", "off"):
            undo.append(("ring effect", lambda: ops.set_ring(
                client, base_lighting.mode, tuple(base_lighting.color),
                base_lighting.ring_brightness)))
        # If the previous mode was thermal there is nothing to re-send: the
        # poller owned the ring, and L2's undo (restarting it) IS the rollback.
    else:
        stages.append(StageResult("ring effect", "skipped"))

    # --- T: the template set, and the persistent half of brightness ---------
    try:
        apply_templates(client, templates, live_id, base_hash=base_hash,
                        device_id=device_id,
                        lcd_entry_fallback=lcd_entry_fallback,
                        brightness=draft_lighting.screen_brightness)
    except (ApplyFailed, ConflictError) as exc:
        stages.append(StageResult("templates", "failed", str(exc)))
        unwind()
        # Deliberately NOT re-raised as ConflictError: by now the poller may be
        # stopped and the ring driven, so the caller must be told what was
        # rolled back rather than offered a naive "overwrite anyway?" retry.
        raise PartialApply(
            f"{exc} The lighting changes made before it were rolled back.",
            stages)
    stages.append(StageResult("templates", "done"))

    # --- B: make brightness take effect now ---------------------------------
    if plan.screen_brightness:
        try:
            ops.set_lcd_brightness(client, device_id,
                                   draft_lighting.screen_brightness)
        except Exception as exc:
            stages.append(StageResult("screen brightness", "failed", str(exc)))
        else:
            stages.append(StageResult(
                "screen brightness", "unverifiable",
                "SetLcdBrightness replies ok before it touches the device and "
                "has no read-back, so this is not evidence. The value was "
                "persisted in config.lcds[].brightness by the stage above, so "
                "it is correct after any daemon restart regardless."))
    else:
        stages.append(StageResult("screen brightness", "skipped"))

    # Deliberately last, and deliberately not rolled back: this is bookkeeping
    # about what was sent, and it is only true once everything above succeeded.
    try:
        ops.save_ring_state(draft_lighting.mode, tuple(draft_lighting.color),
                            draft_lighting.ring_brightness)
    except Exception as exc:
        stages.append(StageResult("ring state file", "failed", str(exc)))

    return stages
```

- [ ] **Step 10: Run the tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_apply_all.py -v
```

Expected: PASS, 22 tests. Then:

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q
```

Expected: every existing test still passes — `apply_templates`'s new keyword is optional and defaults to `None`, so Plan A's call sites are unaffected.

- [ ] **Step 11: Commit**

```bash
git add lianli_panel/apply.py tests/test_apply_all.py
git commit -m "feat: commit templates and lighting as one staged transaction

Cheap-and-reversible stages run first so nothing touches the template set
until every lighting stage has succeeded; the poller is stopped before the
ring is driven, or it overwrites the colour within ~2s. Two stages report
themselves unverifiable rather than claiming a success they cannot support.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

---

## Task 5: The sensor↔widget link map

A named sensor expands to an inline source object on the widget. The link back **cannot live in the template**: the daemon parses templates into Rust structs and silently drops keys it does not know, so a `"_sensor": "gpu-temp"` marker is gone by the next `GetLcdTemplates`.

So the map is a side-car file — which goes stale the moment `apply.sh`, `lianli-gui`, or a second copy of this app edits a template, and has no way to be told. It is therefore **validated on load rather than trusted**: a link survives only if the widget's actual source still equals the library's source for that name. Being told a widget is unlinked when it was linked is a small annoyance. Being told it is bound to `gpu-temp` when its `cmd` is something else is the bug this validation exists to prevent.

**Files:**
- Create: `lianli_panel/gui/links.py`
- Test: `tests/test_gui_links.py`

**Interfaces:**
- Consumes: `sensors.Sensor` (has `.name` and `.source`).
- Produces:
  - `links.LINKS_PATH`
  - `links.load(path=None) -> dict[tuple[str, str], str]` keyed `(template_id, widget_id)`
  - `links.save(links, path=None) -> None`
  - `links.bind(links, template_id, widget_id, name) -> dict`, `links.unbind(links, template_id, widget_id) -> dict`
  - `links.widgets_using(links, name) -> list[tuple[str, str]]`
  - `links.rename_sensor(links, old, new) -> dict`
  - `links.Dropped(link, name, reason)`
  - `links.validate(links, templates, sensors) -> tuple[dict, list[Dropped]]`
  - `links.source_of(templates, template_id, widget_id) -> dict | None`
  - `links.propagate(templates, links, name, source) -> list[tuple[str, str]]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_links.py`:

```python
"""The sensor<->widget map, and the validation that keeps it honest.

The map cannot live in the template -- the daemon drops unknown JSON fields --
so it is a side-car file, and a side-car file goes stale silently. Every test
about validate() is really a test that this app would rather say "(custom)"
than claim a binding it cannot substantiate.
"""
from lianli_panel.gui import links
from lianli_panel.sensors import Sensor

CMD = {"type": "command", "cmd": "/var/lib/lianli-panel/gpu.sh"}
OTHER = {"type": "command", "cmd": "/var/lib/lianli-panel/cpu.sh"}


def templates(source=None):
    return [{"id": "dash", "name": "Dash", "base_width": 1920,
             "base_height": 480, "rotated": True,
             "background": {"type": "color", "rgb": [0, 0, 0, 255]},
             "widgets": [
                 {"id": "gpu-text", "x": 0.0, "y": 0.0, "width": 10.0,
                  "height": 10.0,
                  "kind": {"type": "value_text", "font_size": 30.0,
                           "color": [255, 255, 255, 255],
                           "source": dict(source if source is not None else CMD)}},
                 {"id": "a-label", "x": 0.0, "y": 0.0, "width": 10.0,
                  "height": 10.0,
                  "kind": {"type": "label", "text": "GPU", "font_size": 20.0,
                           "color": [255, 255, 255, 255]}},
             ]}]


def library(**kw):
    base = {"gpu-temp": Sensor("gpu-temp", dict(CMD))}
    base.update(kw)
    return base


# --- storage ---------------------------------------------------------------


def test_links_round_trip_through_the_file(tmp_path):
    path = tmp_path / "sensor-links.json"
    links.save({("dash", "gpu-text"): "gpu-temp"}, path)
    assert links.load(path) == {("dash", "gpu-text"): "gpu-temp"}


def test_a_missing_file_is_an_empty_map(tmp_path):
    assert links.load(tmp_path / "nothing.json") == {}


def test_a_corrupt_file_is_an_empty_map_not_a_crash(tmp_path):
    path = tmp_path / "sensor-links.json"
    path.write_text("{ not json")
    assert links.load(path) == {}


def test_bind_and_unbind_do_not_mutate_the_map_they_were_given():
    original = {}
    bound = links.bind(original, "dash", "gpu-text", "gpu-temp")
    assert original == {}
    assert bound == {("dash", "gpu-text"): "gpu-temp"}
    assert links.unbind(bound, "dash", "gpu-text") == {}


def test_unbinding_something_that_was_never_bound_is_not_an_error():
    assert links.unbind({}, "dash", "gpu-text") == {}


def test_widgets_using_finds_every_widget_bound_to_one_sensor():
    m = {("dash", "gpu-text"): "gpu-temp", ("dash", "b"): "gpu-temp",
         ("dash", "c"): "cpu-temp"}
    assert sorted(links.widgets_using(m, "gpu-temp")) == [("dash", "b"),
                                                          ("dash", "gpu-text")]


def test_renaming_a_sensor_moves_every_link_to_the_new_name():
    m = {("dash", "gpu-text"): "gpu-temp", ("dash", "c"): "cpu-temp"}
    renamed = links.rename_sensor(m, "gpu-temp", "gpu")
    assert renamed[("dash", "gpu-text")] == "gpu"
    assert renamed[("dash", "c")] == "cpu-temp"


# --- validation ------------------------------------------------------------


def test_a_link_whose_source_still_matches_survives():
    kept, dropped = links.validate({("dash", "gpu-text"): "gpu-temp"},
                                   templates(), library())
    assert kept == {("dash", "gpu-text"): "gpu-temp"}
    assert dropped == []


def test_a_link_is_dropped_when_something_else_edited_the_source():
    """The whole reason validation exists. apply.sh or lianli-gui rewrote the
    widget; the map still says gpu-temp; believing it would mislabel a command
    the user never chose."""
    kept, dropped = links.validate({("dash", "gpu-text"): "gpu-temp"},
                                   templates(source=OTHER), library())
    assert kept == {}
    assert len(dropped) == 1
    assert "no longer matches" in dropped[0].reason


def test_a_link_is_dropped_when_the_sensor_left_the_library():
    kept, dropped = links.validate({("dash", "gpu-text"): "gpu-temp"},
                                   templates(), {})
    assert kept == {}
    assert "library" in dropped[0].reason


def test_a_link_is_dropped_when_the_widget_is_gone():
    kept, dropped = links.validate({("dash", "vanished"): "gpu-temp"},
                                   templates(), library())
    assert kept == {}
    assert "widget" in dropped[0].reason


def test_a_link_is_dropped_when_the_template_is_gone():
    kept, dropped = links.validate({("deleted", "gpu-text"): "gpu-temp"},
                                   templates(), library())
    assert kept == {}
    assert "template" in dropped[0].reason


def test_a_link_is_dropped_when_the_widget_no_longer_has_a_source():
    """A label has no source. Changing a value_text into one is a normal edit."""
    kept, dropped = links.validate({("dash", "a-label"): "gpu-temp"},
                                   templates(), library())
    assert kept == {}
    assert "source" in dropped[0].reason


def test_validation_reports_the_dropped_link_by_name_so_it_can_be_shown():
    _, dropped = links.validate({("dash", "gpu-text"): "gpu-temp"},
                                templates(source=OTHER), library())
    assert dropped[0].link == ("dash", "gpu-text")
    assert dropped[0].name == "gpu-temp"


# --- propagation -----------------------------------------------------------


def test_propagate_rewrites_every_linked_widget_and_says_which():
    tpls = templates()
    touched = links.propagate(tpls, {("dash", "gpu-text"): "gpu-temp"},
                              "gpu-temp", OTHER)
    assert touched == [("dash", "gpu-text")]
    assert tpls[0]["widgets"][0]["kind"]["source"] == OTHER


def test_propagate_gives_each_widget_its_own_copy_of_the_source():
    """Sharing one dict across widgets means editing one silently edits them
    all -- and the daemon would then receive aliased objects."""
    tpls = templates()
    m = {("dash", "gpu-text"): "gpu-temp"}
    links.propagate(tpls, m, "gpu-temp", OTHER)
    tpls[0]["widgets"][0]["kind"]["source"]["cmd"] = "changed"
    assert OTHER["cmd"] == "/var/lib/lianli-panel/cpu.sh"


def test_propagate_ignores_links_for_other_sensors():
    tpls = templates()
    touched = links.propagate(tpls, {("dash", "gpu-text"): "cpu-temp"},
                              "gpu-temp", OTHER)
    assert touched == []
    assert tpls[0]["widgets"][0]["kind"]["source"] == CMD


def test_source_of_returns_none_for_a_widget_without_one():
    assert links.source_of(templates(), "dash", "a-label") is None


def test_the_module_does_not_import_pyside6():
    assert "PySide6" not in open(links.__file__).read()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
./.venv/bin/pytest tests/test_gui_links.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.links'`.

- [ ] **Step 3: Implement the module**

Create `lianli_panel/gui/links.py`:

```python
"""Which widget is bound to which named sensor.

THE LINK CANNOT LIVE IN THE TEMPLATE. The daemon parses templates into Rust
structs and silently drops keys it does not recognise, so a "_sensor" marker
stamped onto a source object is gone by the next GetLcdTemplates -- not with an
error, just gone. So the map is a side-car file keyed by (template_id,
widget_id).

A side-car map goes stale the moment anything else edits a template -- apply.sh,
lianli-gui, or a second copy of this app -- and it has no way to be told. So it
is VALIDATED ON LOAD rather than trusted: a link survives only if the widget's
source still equals the library's source for that name.

Being told a widget is unlinked when it was linked is a small annoyance. Being
told it is bound to "gpu-temp" when its cmd is something else entirely is the
bug this validation exists to prevent, and it is the kind that survives review
because everything looks right.

STORED FORMAT is nested by template so the file stays readable by hand:

    {"gaming-dash": {"gpu-text": "gpu-temp"}}
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

LINKS_PATH = Path("~/.config/lianli-panel/sensor-links.json").expanduser()

Link = tuple[str, str]      # (template_id, widget_id)


@dataclass
class Dropped:
    link: Link
    name: str
    reason: str


def load(path: Path | None = None) -> dict[Link, str]:
    path = Path(path) if path is not None else LINKS_PATH
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[Link, str] = {}
    if not isinstance(raw, dict):
        return {}
    for template_id, widgets in raw.items():
        if isinstance(widgets, dict):
            for widget_id, name in widgets.items():
                if isinstance(name, str):
                    out[(str(template_id), str(widget_id))] = name
    return out


def save(links: dict[Link, str], path: Path | None = None) -> None:
    path = Path(path) if path is not None else LINKS_PATH
    nested: dict[str, dict[str, str]] = {}
    for (template_id, widget_id), name in links.items():
        nested.setdefault(template_id, {})[widget_id] = name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(nested, indent=1, sort_keys=True))


def bind(links: dict[Link, str], template_id: str, widget_id: str,
         name: str) -> dict[Link, str]:
    return {**links, (template_id, widget_id): name}


def unbind(links: dict[Link, str], template_id: str,
           widget_id: str) -> dict[Link, str]:
    return {k: v for k, v in links.items() if k != (template_id, widget_id)}


def widgets_using(links: dict[Link, str], name: str) -> list[Link]:
    return [link for link, bound in links.items() if bound == name]


def rename_sensor(links: dict[Link, str], old: str,
                  new: str) -> dict[Link, str]:
    return {k: (new if v == old else v) for k, v in links.items()}


def _widget(templates: list[dict], template_id: str,
            widget_id: str) -> dict | None:
    for tpl in templates:
        if tpl.get("id") != template_id:
            continue
        for widget in tpl.get("widgets") or []:
            if widget.get("id") == widget_id:
                return widget
        return None
    return None


def source_of(templates: list[dict], template_id: str,
              widget_id: str) -> dict | None:
    widget = _widget(templates, template_id, widget_id)
    if widget is None:
        return None
    source = (widget.get("kind") or {}).get("source")
    return source if isinstance(source, dict) else None


def validate(links: dict[Link, str], templates: list[dict],
             sensors: dict) -> tuple[dict[Link, str], list[Dropped]]:
    """Keep only the links the templates still support.

    Every drop reason is phrased so the UI can show it verbatim: the user needs
    to know their binding went away and why, not merely that a widget now reads
    "(custom)".
    """
    known = {tpl.get("id") for tpl in templates}
    kept: dict[Link, str] = {}
    dropped: list[Dropped] = []

    for link, name in links.items():
        template_id, widget_id = link
        if template_id not in known:
            dropped.append(Dropped(link, name,
                                   f"template {template_id!r} no longer exists"))
            continue
        if name not in sensors:
            dropped.append(Dropped(
                link, name,
                f"sensor {name!r} is no longer in the library"))
            continue
        if _widget(templates, template_id, widget_id) is None:
            dropped.append(Dropped(
                link, name,
                f"widget {widget_id!r} is gone from template {template_id!r}"))
            continue
        source = source_of(templates, template_id, widget_id)
        if source is None:
            dropped.append(Dropped(
                link, name,
                f"widget {widget_id!r} no longer has a source"))
            continue
        if source != sensors[name].source:
            dropped.append(Dropped(
                link, name,
                f"widget {widget_id!r} no longer matches sensor {name!r} — "
                "something outside this app edited it"))
            continue
        kept[link] = name

    return kept, dropped


def propagate(templates: list[dict], links: dict[Link, str], name: str,
              source: dict) -> list[Link]:
    """Rewrite every widget bound to `name`, in place. Returns what it touched.

    Each widget gets its OWN copy: sharing one dict across widgets means a
    later edit to one silently edits them all, and the daemon would receive
    aliased objects it has no reason to expect.
    """
    touched: list[Link] = []
    for link, bound in links.items():
        if bound != name:
            continue
        widget = _widget(templates, link[0], link[1])
        if widget is None or not isinstance(widget.get("kind"), dict):
            continue
        widget["kind"]["source"] = copy.deepcopy(source)
        touched.append(link)
    return touched
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
./.venv/bin/pytest tests/test_gui_links.py -v
```

Expected: PASS, 20 tests.

- [ ] **Step 5: Commit**

```bash
git add lianli_panel/gui/links.py tests/test_gui_links.py
git commit -m "feat: add the sensor-to-widget link map, validated on load

The link cannot live in the template -- the daemon drops unknown fields -- so
it is a side-car file, and a side-car file goes stale silently. A link
survives only if the widget's source still equals the library's; otherwise the
widget reads (custom) and the reason is shown.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

---

## Task 6: The Lighting tab

A view and nothing more. **It never talks to the daemon** — it emits `test_requested` and the window does the sending — which is what keeps it testable offscreen with no client at all.

**Files:**
- Create: `lianli_panel/gui/lighting_tab.py`
- Modify: `lianli_panel/lighting.py` (add `interlock_text`)
- Test: `tests/test_gui_lighting_tab.py`, `tests/test_lighting.py`

**Interfaces:**
- Consumes: `lighting.LightingState`, `lighting.problems`, `lighting.MODES`, `ring.RingState`, `ring.RING_READBACK_NOTE`, and `inspector.ColorButton` (already exists — a `QPushButton` that opens `QColorDialog` and emits `changed(list)` with RGBA).
- Produces:
  - `lighting.interlock_text(mode: str, poller_running: bool) -> str`
  - `lighting_tab.LightingTab(parent=None)` with signals `changed()` and `test_requested()`, and methods `state()`, `set_state(s)`, `set_poller_running(bool)`, `set_last_set(RingState)`, `set_problems(list)`

- [ ] **Step 1: Write the failing test for the interlock copy**

Append to `tests/test_lighting.py`:

```python
def test_the_interlock_warns_before_apply_not_after():
    """The user needs to know Apply will stop a service BEFORE they press it.
    Telling them afterwards is an apology, not a warning."""
    text = lighting.interlock_text("static", poller_running=True)
    assert "stop" in text.lower()
    assert "lianli-thermal-rgb" in text


def test_the_interlock_says_thermal_will_start_the_service():
    text = lighting.interlock_text("thermal", poller_running=False)
    assert "start" in text.lower()


def test_the_interlock_says_nothing_when_no_unit_change_is_needed():
    assert lighting.interlock_text("thermal", poller_running=True) == ""
    assert lighting.interlock_text("static", poller_running=False) == ""
```

- [ ] **Step 2: Implement `interlock_text`**

Add to `lianli_panel/lighting.py`:

```python
def interlock_text(mode: str, poller_running: bool) -> str:
    """What Apply will do to lianli-thermal-rgb.service, said in advance.

    Static and Off conflict with the poller, which re-drives the ring every
    ~2s. Applying either stops it. Saying so only afterwards would make the
    app look like it had a side effect nobody asked for.
    """
    if mode == "thermal" and not poller_running:
        return ("Applying will START lianli-thermal-rgb.service, which then "
                "drives the ring from the hotter of CPU and GPU.")
    if mode in ("static", "off") and poller_running:
        return ("Applying will STOP lianli-thermal-rgb.service first — it "
                "re-drives the ring every ~2s and would overwrite this colour "
                "within seconds.")
    return ""
```

- [ ] **Step 3: Write the failing tests for the tab**

Create `tests/test_gui_lighting_tab.py`:

```python
"""The Lighting tab, offscreen.

The tab takes NO client. Every side effect belongs to the window, which is what
lets these tests construct it with nothing and still exercise the whole surface.
"""
import pytest

from lianli_panel import lighting, ring


@pytest.fixture
def tab(qapp):
    from lianli_panel.gui.lighting_tab import LightingTab
    return LightingTab()


def a_state(**kw):
    defaults = dict(mode="static", color=(10, 20, 30), ring_brightness=2,
                    screen_brightness=180,
                    thermal=ring.ThermalConfig(cool_c=40.0, hot_c=90.0,
                                               poll_ms=1000))
    defaults.update(kw)
    return lighting.LightingState(**defaults)


def test_the_tab_needs_no_client():
    """A view that can reach the daemon is a view that can surprise the user
    mid-edit. Every send belongs to the window."""
    from lianli_panel.gui.lighting_tab import LightingTab
    assert LightingTab() is not None


def test_state_round_trips_through_the_controls(tab):
    tab.set_state(a_state())
    assert tab.state() == a_state()


def test_an_unset_screen_brightness_round_trips_as_none(tab):
    """None is 'the panel's own default', which is not the same as 0 -- and 0
    is a black screen the user cannot read to undo it."""
    tab.set_state(a_state(screen_brightness=None))
    assert tab.state().screen_brightness is None


def test_populating_the_controls_does_not_report_a_user_edit(tab):
    """set_state runs on every load and every revert. If it emitted, the draft
    would be dirty the moment the app opened."""
    seen = []
    tab.changed.connect(lambda: seen.append(1))
    tab.set_state(a_state())
    assert seen == []


def test_editing_a_control_reports_a_change(tab):
    tab.set_state(a_state())
    seen = []
    tab.changed.connect(lambda: seen.append(1))
    tab.ring_brightness.setValue(1)
    assert seen


def test_test_on_ring_is_disabled_in_thermal_mode(tab):
    """In thermal mode the poller owns the ring; a test send would flash and
    vanish, which reads as a broken button."""
    tab.set_state(a_state(mode="thermal"))
    assert not tab.test_button.isEnabled()


def test_test_on_ring_is_enabled_in_static_mode(tab):
    tab.set_state(a_state(mode="static"))
    assert tab.test_button.isEnabled()


def test_pressing_test_on_ring_asks_the_window_to_send(tab):
    tab.set_state(a_state(mode="static"))
    seen = []
    tab.test_requested.connect(lambda: seen.append(1))
    tab.test_button.click()
    assert seen == [1]


def test_the_interlock_is_shown_before_apply(tab):
    tab.set_state(a_state(mode="static"))
    tab.set_poller_running(True)
    assert "STOP" in tab.interlock.text()


def test_the_interlock_clears_when_no_unit_change_is_needed(tab):
    tab.set_state(a_state(mode="static"))
    tab.set_poller_running(False)
    assert tab.interlock.text() == ""


def test_the_poller_status_is_shown_because_it_is_the_one_readable_fact(tab):
    tab.set_poller_running(True)
    assert "active" in tab.poller_status.text().lower()
    tab.set_poller_running(False)
    assert "active" not in tab.poller_status.text().lower()


def test_the_last_set_colour_is_labelled_as_not_a_read_back(tab):
    """Showing a swatch without this caption would imply the app knows what
    the ring is lit with. It cannot; GetZoneColors fails on this device."""
    tab.set_last_set(ring.RingState("static", (1, 2, 3), 2,
                                    "2026-09-05T12:00:00+01:00"))
    text = tab.last_set.text()
    assert "2026-09-05" in text
    assert "read back" in text.lower()


def test_a_ring_never_set_by_this_app_says_so(tab):
    tab.set_last_set(ring.RingState())
    assert "never" in tab.last_set.text().lower()


def test_problems_are_shown_with_their_message(tab):
    tab.set_problems([lighting.Problem("error", "cool_c", "cool must be below hot")])
    assert "cool must be below hot" in tab.problems.text()


def test_no_problems_clears_the_line(tab):
    tab.set_problems([lighting.Problem("error", "cool_c", "bad")])
    tab.set_problems([])
    assert tab.problems.text() == ""


def test_the_two_brightnesses_are_separate_controls(tab):
    """They are 0-4 and 0-255, they reach different hardware, and confusing
    them sets the screen to 4/255 -- effectively black."""
    tab.set_state(a_state(ring_brightness=2, screen_brightness=180))
    assert tab.ring_brightness.value() == 2
    assert tab.screen_brightness.value() == 180
    assert tab.ring_brightness is not tab.screen_brightness
```

- [ ] **Step 4: Run the tests to verify they fail**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_lighting_tab.py tests/test_lighting.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.lighting_tab'`.

- [ ] **Step 5: Give `ColorButton` the accessors the tab needs**

`inspector.ColorButton` stores `self._rgba` and has no public reader or setter, so the Lighting tab would have to reach into a private attribute. Add both methods next to `_paint`, before writing the tab:

```python
    def rgba(self) -> list[int]:
        return list(self._rgba)

    def set_rgba(self, rgba: list[int]) -> None:
        """Sets without emitting: callers use this to POPULATE, and an emit
        here would report a user edit that never happened."""
        self._rgba = [int(c) for c in (list(rgba) + [255, 255, 255, 255])[:4]]
        self._paint()
```

- [ ] **Step 6: Implement the tab**

Create `lianli_panel/gui/lighting_tab.py`:

```python
"""The Lighting tab: the ring, both brightnesses, and the poller's settings.

THIS VIEW NEVER TALKS TO THE DAEMON. It emits `changed` when the user edits
something and `test_requested` when they explicitly ask to see a colour on the
ring; the window owns every send. That is not tidiness -- it is what makes a
tab full of hardware controls testable offscreen with no client at all.

THE TWO BRIGHTNESSES ARE DELIBERATELY IN SEPARATE GROUPS. One is 0-4 and drives
the LED ring; the other is 0-255 and drives the panel's backlight. Confusing
them sets the screen to 4/255, which is a black screen the user then cannot
read well enough to undo.

Screen brightness has a "set it" checkbox because the daemon's own state has
three values, not two: a number, or absent-meaning-the-panel's-own-default.
Collapsing absent to 0 would offer "off" as the default.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QDoubleSpinBox,
                               QFormLayout, QGroupBox, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from .. import lighting
from ..ring import RING_READBACK_NOTE, RingState, ThermalConfig
from .inspector import ColorButton


class LightingTab(QWidget):
    changed = Signal()
    test_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._loading = False
        self._poller_running = False

        # --- ring ----------------------------------------------------------
        self.mode_buttons = QButtonGroup(self)
        self.off = QRadioButton("Off")
        self.static = QRadioButton("Static colour")
        self.thermal = QRadioButton("Thermal sweep (the poller drives it)")
        for i, button in enumerate((self.off, self.static, self.thermal)):
            self.mode_buttons.addButton(button, i)
            button.toggled.connect(self._mode_toggled)

        self.color = ColorButton([255, 255, 255, 255])
        self.color.changed.connect(self._edited)
        self.ring_brightness = QSpinBox()
        self.ring_brightness.setRange(0, lighting.RING_BRIGHTNESS_MAX)
        self.ring_brightness.valueChanged.connect(self._edited)

        self.test_button = QPushButton("Test on ring")
        self.test_button.setToolTip(
            "Sends this colour to the ring immediately, without applying "
            "anything else. The only control on this tab with a side effect "
            "before Apply.")
        self.test_button.clicked.connect(self.test_requested)

        self.poller_status = QLabel()
        self.last_set = QLabel()
        self.last_set.setWordWrap(True)
        self.interlock = QLabel()
        self.interlock.setWordWrap(True)
        self.interlock.setStyleSheet("color:#ffeec9; background:#54471a;")

        ring_box = QVBoxLayout()
        for button in (self.off, self.static, self.thermal):
            ring_box.addWidget(button)
        colour_row = QHBoxLayout()
        colour_row.addWidget(QLabel("Colour"))
        colour_row.addWidget(self.color, 1)
        colour_row.addWidget(QLabel("Ring brightness"))
        colour_row.addWidget(self.ring_brightness)
        colour_row.addWidget(self.test_button)
        ring_box.addLayout(colour_row)
        ring_box.addWidget(self.poller_status)
        ring_box.addWidget(self.last_set)
        ring_box.addWidget(self.interlock)
        ring_group = QGroupBox("LED ring")
        ring_group.setLayout(ring_box)

        # --- screen --------------------------------------------------------
        self.screen_set = QCheckBox("Set the panel's brightness")
        self.screen_set.toggled.connect(self._screen_toggled)
        self.screen_brightness = QSlider(Qt.Orientation.Horizontal)
        self.screen_brightness.setRange(0, lighting.SCREEN_BRIGHTNESS_MAX)
        self.screen_brightness.valueChanged.connect(self._screen_moved)
        self.screen_value = QSpinBox()
        self.screen_value.setRange(0, lighting.SCREEN_BRIGHTNESS_MAX)
        self.screen_value.valueChanged.connect(self.screen_brightness.setValue)

        screen_row = QHBoxLayout()
        screen_row.addWidget(self.screen_brightness, 1)
        screen_row.addWidget(self.screen_value)
        screen_box = QVBoxLayout()
        screen_box.addWidget(self.screen_set)
        screen_box.addLayout(screen_row)
        screen_box.addWidget(QLabel(
            "0-255, and unrelated to the ring's 0-4. Applying persists it in "
            "config.lcds so it survives a daemon restart."))
        screen_group = QGroupBox("Panel brightness")
        screen_group.setLayout(screen_box)

        # --- poller --------------------------------------------------------
        self.cool_c = QDoubleSpinBox()
        self.cool_c.setRange(0.0, 150.0)
        self.hot_c = QDoubleSpinBox()
        self.hot_c.setRange(0.0, 150.0)
        self.poll_ms = QSpinBox()
        self.poll_ms.setRange(50, 60000)
        self.min_delta_c = QDoubleSpinBox()
        self.min_delta_c.setRange(0.0, 50.0)
        self.force_refresh_s = QSpinBox()
        self.force_refresh_s.setRange(1, 3600)
        self.thermal_brightness = QSpinBox()
        self.thermal_brightness.setRange(0, lighting.RING_BRIGHTNESS_MAX)
        for control in (self.cool_c, self.hot_c, self.poll_ms,
                        self.min_delta_c, self.force_refresh_s,
                        self.thermal_brightness):
            control.valueChanged.connect(self._edited)

        poller_form = QFormLayout()
        poller_form.addRow("Green at (°C)", self.cool_c)
        poller_form.addRow("Red at (°C)", self.hot_c)
        poller_form.addRow("Poll interval (ms)", self.poll_ms)
        poller_form.addRow("Ignore changes below (°C)", self.min_delta_c)
        poller_form.addRow("Force a refresh every (s)", self.force_refresh_s)
        poller_form.addRow("Ring brightness while sweeping",
                           self.thermal_brightness)
        poller_group = QGroupBox("Thermal poller")
        poller_group.setLayout(poller_form)
        poller_note = QLabel(
            "Written to /var/lib/lianli-panel/thermal-rgb.json on Apply. The "
            "poller re-reads it when the file's mtime changes, so edits take "
            "effect within one poll — no unit restart.")
        poller_note.setWordWrap(True)
        poller_form.addRow(poller_note)

        self.problems = QLabel()
        self.problems.setWordWrap(True)
        self.problems.setStyleSheet("color:#ffd9d9; background:#5a1d1d;")

        root = QVBoxLayout(self)
        root.addWidget(ring_group)
        root.addWidget(screen_group)
        root.addWidget(poller_group)
        root.addWidget(self.problems)
        root.addStretch(1)

        self.set_state(lighting.LightingState())
        self.set_problems([])

    # --- state -------------------------------------------------------------

    def state(self) -> lighting.LightingState:
        return lighting.LightingState(
            mode=self._mode(),
            color=tuple(self.color.rgba()[:3]),
            ring_brightness=self.ring_brightness.value(),
            screen_brightness=(self.screen_brightness.value()
                               if self.screen_set.isChecked() else None),
            thermal=ThermalConfig(
                cool_c=self.cool_c.value(), hot_c=self.hot_c.value(),
                poll_ms=self.poll_ms.value(),
                min_delta_c=self.min_delta_c.value(),
                force_refresh_s=self.force_refresh_s.value(),
                brightness=self.thermal_brightness.value()),
        )

    def set_state(self, s: lighting.LightingState) -> None:
        """Populates without reporting an edit. Called on load and on revert;
        emitting here would make the draft dirty the moment the app opens."""
        self._loading = True
        try:
            {"off": self.off, "static": self.static,
             "thermal": self.thermal}.get(s.mode, self.thermal).setChecked(True)
            self.color.set_rgba(list(s.color) + [255])
            self.ring_brightness.setValue(s.ring_brightness)
            self.screen_set.setChecked(s.screen_brightness is not None)
            self.screen_brightness.setValue(s.screen_brightness or 0)
            self.screen_value.setValue(s.screen_brightness or 0)
            self.cool_c.setValue(s.thermal.cool_c)
            self.hot_c.setValue(s.thermal.hot_c)
            self.poll_ms.setValue(s.thermal.poll_ms)
            self.min_delta_c.setValue(s.thermal.min_delta_c)
            self.force_refresh_s.setValue(s.thermal.force_refresh_s)
            self.thermal_brightness.setValue(s.thermal.brightness)
        finally:
            self._loading = False
        self._refresh_mode_dependent()

    def set_poller_running(self, running: bool) -> None:
        self._poller_running = bool(running)
        self.poller_status.setText(
            "lianli-thermal-rgb.service: ACTIVE — it is driving the ring now"
            if running else
            "lianli-thermal-rgb.service: not running")
        self._refresh_mode_dependent()

    def set_last_set(self, state: RingState) -> None:
        if state.set_at is None:
            self.last_set.setText(
                "This app has never set the ring. " + RING_READBACK_NOTE)
            return
        self.last_set.setText(
            f"Last set by this app: {state.mode} "
            f"{tuple(state.color)} at brightness {state.brightness}, "
            f"{state.set_at}. " + RING_READBACK_NOTE)

    def set_problems(self, problems: list) -> None:
        self.problems.setText("\n".join(
            f"{p.level}: {p.field} — {p.message}" for p in problems))
        self.problems.setVisible(bool(problems))

    # --- internals ---------------------------------------------------------

    def _mode(self) -> str:
        if self.off.isChecked():
            return "off"
        if self.static.isChecked():
            return "static"
        return "thermal"

    def _refresh_mode_dependent(self) -> None:
        mode = self._mode()
        self.test_button.setEnabled(mode in ("static", "off"))
        self.interlock.setText(
            lighting.interlock_text(mode, self._poller_running))
        self.interlock.setVisible(bool(self.interlock.text()))

    def _mode_toggled(self, checked: bool) -> None:
        if not checked:
            return
        self._refresh_mode_dependent()
        self._edited()

    def _screen_toggled(self, _checked: bool) -> None:
        self._edited()

    def _screen_moved(self, value: int) -> None:
        if self.screen_value.value() != value:
            self.screen_value.setValue(value)
        self._edited()

    def _edited(self, *_args) -> None:
        if self._loading:
            return
        self.changed.emit()
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_lighting_tab.py tests/test_lighting.py tests/test_gui_smoke.py -v
```

Expected: PASS. The smoke tests are included because `ColorButton` changed and the inspector uses it.

- [ ] **Step 8: Commit**

```bash
git add lianli_panel/gui/lighting_tab.py lianli_panel/lighting.py lianli_panel/gui/inspector.py tests/test_gui_lighting_tab.py tests/test_lighting.py
git commit -m "feat: add the Lighting tab

The two brightnesses get separate groups because confusing 0-4 with 0-255
sets the screen to black. The interlock says what Apply will do to the poller
before it is pressed, and the last-set colour is captioned as what was sent
rather than what is lit.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

- [ ] **Step 9 (CONTROLLER — the ring actually changing colour)**

Tests prove the tab reports the right state; they prove nothing about the ring. Launch the app and use the tab:

```bash
QT_QPA_PLATFORM=wayland ./.venv/bin/lianli-panel-gui
```

Note the tab is not wired into the window until Task 8, so drive it directly instead:

```bash
PYTHONPATH=. ./.venv/bin/python -c "
from PySide6.QtWidgets import QApplication
from lianli_panel.gui.lighting_tab import LightingTab
from lianli_panel import lighting, ring
from lianli_panel.ipc import Client
app = QApplication([])
tab = LightingTab()
tab.set_poller_running(ring.poller_active())
tab.set_last_set(ring.load_ring_state())
tab.show()
app.exec()
"
```

Check by eye, and record each in the ledger:
1. The poller status line matches reality (`systemctl --user is-active lianli-thermal-rgb.service`).
2. Selecting **Static** shows the amber interlock naming the service; selecting **Thermal sweep** while it is running shows nothing.
3. **Test on ring** is greyed in thermal mode and live in static.
4. The colour button opens a real colour dialog and the swatch follows.

Then confirm the interlock is telling the truth — stop the poller by hand, send a static colour, and watch:

```bash
systemctl --user stop lianli-thermal-rgb.service
PYTHONPATH=. ./.venv/bin/python -c "
from lianli_panel.ipc import Client
from lianli_panel import ring
ring.set_mode(Client(), 'static', (255, 0, 0), 4)
"
```

Expected: **the ring goes red and stays red.** Now the negative control, which is the reason the interlock exists:

```bash
systemctl --user start lianli-thermal-rgb.service
PYTHONPATH=. ./.venv/bin/python -c "
from lianli_panel.ipc import Client
from lianli_panel import ring
ring.set_mode(Client(), 'static', (255, 0, 255), 4)
"
```

Expected: the ring flashes magenta and returns to its thermal colour **within ~2s**. Record the observed time — Task 11 quotes it, and if the ring does *not* revert, the poller is not running the config this plan assumes and Task 9 must be brought forward.

Leave the poller running.

---

## Task 7: The Sensors tab

The spec's two-tier harness, built. The tiers are not equivalent and the UI must never let them look it: the authoritative tier is the daemon rendering the command as uid `lianli`; the diagnostic tier runs as `chase` and will happily succeed on `$HOME` paths the daemon cannot traverse.

**Files:**
- Create: `lianli_panel/gui/sensors_tab.py`
- Modify: `lianli_panel/gui/forms.py` (add `source_fields_for`)
- Modify: `lianli_panel/gui/preview.py` (add `ProbeWorker`)
- Test: `tests/test_gui_sensors_tab.py`, `tests/test_gui_forms.py`

**Interfaces:**
- Consumes: `sensors.Sensor`, `sensors.load`, `sensors.save`, `sensors.run_diagnostic`, `sensors.static_checks`, `sensors.render_authoritative`, `sensors.USER_SCRIPT_DIR`, `schema.SOURCE_NAMES`, `forms.FieldSpec`.
- Produces:
  - `forms.source_fields_for(src: dict) -> list[FieldSpec]`
  - `preview.ProbeWorker(client, parent=None)` with `done(bytes)`, `failed(str)`, `probe(cmd) -> bool`, `stop()`
  - `sensors_tab.SensorsTab(parent=None)` with signals `library_changed()`, `bind_requested(str)`, `probe_requested(str)`, and methods `set_library(dict)`, `library()`, `set_target(template_id, widget_id, description)`, `show_probe(bytes)`, `show_probe_error(str)`, `current_name()`

**A ruling this task makes, stated up front.** The *diagnostic* tier runs `subprocess.run` on the UI thread, behind a confirmation that says it may block for up to its 10-second timeout. The *authoritative* tier goes off-thread through `ProbeWorker`, because it is an IPC round trip that also spawns the command twice inside the daemon. Making the diagnostic async too would need a second worker for a deliberate, one-at-a-time action the user just confirmed — not worth the machinery, but it is a choice, not an oversight.

- [ ] **Step 1: Write the failing test for `source_fields_for`**

Append to `tests/test_gui_forms.py`:

```python
def test_source_fields_can_be_derived_from_a_bare_source_dict():
    """The sensor editor edits sources that are not attached to a widget yet,
    so it cannot go through source_fields(w)."""
    from lianli_panel.gui import forms
    fields = forms.source_fields_for({"type": "hwmon", "name": "coretemp",
                                      "label": "temp1"})
    names = {f.name for f in fields}
    assert {"name", "label"} <= names
    assert all(f.name != "type" for f in fields)


def test_source_fields_for_an_unknown_type_still_offers_its_own_keys():
    """A daemon upgrade must degrade to reduced functionality, not data loss."""
    from lianli_panel.gui import forms
    fields = forms.source_fields_for({"type": "invented_later", "knob": 3})
    assert {f.name for f in fields} == {"knob"}
```

- [ ] **Step 2: Implement `source_fields_for`**

In `lianli_panel/gui/forms.py`, replace `source_fields`:

```python
def source_fields_for(src: dict) -> list[FieldSpec]:
    """Fields for a bare source dict, with no widget around it.

    The sensor editor edits sources that are not attached to anything yet, so
    it cannot go through a Widget. Same rules, same schema, one less layer.
    """
    return _fields(src, SOURCE_TYPES.get(src.get("type", "")), True)


def source_fields(w: Widget) -> list[FieldSpec]:
    return source_fields_for(w.source or {})
```

- [ ] **Step 3: Write the failing tests for `ProbeWorker`**

Create the top of `tests/test_gui_sensors_tab.py`:

```python
"""The Sensors tab and its probe worker.

The two test tiers are not equivalent and the UI must never let them look it.
Several tests here exist only to pin the labelling, because a diagnostic that
passes while the real sensor fails is exactly the outcome the spec's
"labelled as not authoritative" wording is defending against.
"""
import base64
import time

import pytest

from lianli_panel.sensors import Diagnostic, Sensor
from tests.conftest import FakeClient

JPEG = base64.b64encode(b"\xff\xd8\xff\xd9").decode()


def probe_client(**overrides):
    responses = {"RenderTemplatePreview": {"jpeg_base64": JPEG}}
    responses.update(overrides)
    return FakeClient(responses)


def wait(qapp, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        qapp.processEvents()
        time.sleep(0.005)
    return pred()


def test_the_probe_worker_returns_the_rendered_jpeg(qapp):
    from lianli_panel.gui.preview import ProbeWorker
    worker = ProbeWorker(probe_client())
    got = []
    worker.done.connect(got.append)
    assert worker.probe("echo 42")
    assert wait(qapp, lambda: got)
    assert got[0].startswith(b"\xff\xd8")
    worker.stop()


def test_the_probe_worker_reports_a_daemon_failure(qapp):
    from lianli_panel.ipc import DaemonError
    from lianli_panel.gui.preview import ProbeWorker
    worker = ProbeWorker(probe_client(
        RenderTemplatePreview=DaemonError("encoder is dead")))
    failures = []
    worker.failed.connect(failures.append)
    worker.probe("echo 42")
    assert wait(qapp, lambda: failures)
    assert "encoder is dead" in failures[0]
    worker.stop()


def test_the_probe_worker_refuses_to_stack_two_probes(qapp):
    """Each probe executes the command inside the daemon. Queueing them would
    turn an impatient double-click into two real executions."""
    from lianli_panel.gui.preview import ProbeWorker
    worker = ProbeWorker(probe_client())
    assert worker.probe("echo 1") is True
    assert worker.probe("echo 2") is False
    worker.stop()
```

- [ ] **Step 4: Implement `ProbeWorker`**

Add to `lianli_panel/gui/preview.py`:

```python
class _ProbeJob(QObject):
    done = Signal(bytes)
    failed = Signal(str)

    def __init__(self, client) -> None:
        super().__init__()
        self._client = client

    @Slot(str)
    def run(self, cmd: str) -> None:
        from .. import sensors
        try:
            self.done.emit(sensors.render_authoritative(self._client, cmd))
        except Exception as exc:
            self.failed.emit(str(exc))


class ProbeWorker(QObject):
    """One authoritative sensor probe, off the UI thread.

    DELIBERATELY NOT PreviewWorker. This render must EXECUTE the command --
    that is the entire point of the authoritative tier -- so it must never
    share the canvas's coalescer, where it would compete with drag renders for
    the in-flight slot and could be debounced away entirely.

    probe() returns False rather than queueing while one is in flight: every
    probe really runs the command inside the daemon, so an impatient
    double-click would otherwise be two real executions of something the user
    was warned might have side effects.
    """
    done = Signal(bytes)
    failed = Signal(str)
    _submit = Signal(str)

    def __init__(self, client, parent=None) -> None:
        super().__init__(parent)
        self._busy = False
        self._thread = QThread()
        self._job = _ProbeJob(client)
        self._job.moveToThread(self._thread)
        self._submit.connect(self._job.run)
        self._job.done.connect(self._on_done)
        self._job.failed.connect(self._on_failed)
        self._thread.start()

    def probe(self, cmd: str) -> bool:
        if self._busy:
            return False
        self._busy = True
        self._submit.emit(cmd)
        return True

    def _on_done(self, jpeg: bytes) -> None:
        self._busy = False
        self.done.emit(jpeg)

    def _on_failed(self, message: str) -> None:
        self._busy = False
        self.failed.emit(message)

    def stop(self) -> None:
        self._thread.quit()
        self._thread.wait(2000)
```

- [ ] **Step 5: Write the failing tests for the tab**

Append to `tests/test_gui_sensors_tab.py`:

```python
@pytest.fixture
def tab(qapp):
    from lianli_panel.gui.sensors_tab import SensorsTab
    t = SensorsTab()
    t.set_library({
        "gpu-temp": Sensor("gpu-temp", {"type": "command",
                                        "cmd": "/var/lib/lianli-panel/gpu.sh"}),
        "cpu-temp": Sensor("cpu-temp", {"type": "hwmon", "name": "coretemp",
                                        "label": "temp1"}),
    })
    return t


def accept(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.Yes)


def decline(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: QMessageBox.StandardButton.No)


def test_the_library_is_listed(tab):
    assert tab.names() == ["cpu-temp", "gpu-temp"]


def test_selecting_a_sensor_shows_its_type_and_fields(tab):
    tab.select("cpu-temp")
    assert tab.type_box.currentText() == "hwmon"
    assert "name" in tab.field_names()


def test_changing_the_type_rebuilds_the_fields_from_the_schema(tab):
    tab.select("cpu-temp")
    tab.type_box.setCurrentText("network_rx")
    assert "iface" in tab.field_names()
    assert "name" not in tab.field_names()


def test_the_command_group_appears_only_for_command_sources(tab):
    tab.select("cpu-temp")
    assert not tab.command_group.isVisible()
    tab.select("gpu-temp")
    assert tab.command_group.isVisible()


def test_a_home_path_is_flagged_while_it_is_being_typed(tab):
    """The daemon runs as lianli and /home/chase is mode 0700, so this is the
    single most common way a hand-written sensor silently reads nothing."""
    tab.select("gpu-temp")
    tab.cmd.setText("/home/chase/scripts/gpu.sh")
    assert "0700" in tab.static_notes.text() or \
        "cannot traverse" in tab.static_notes.text()


def test_a_var_lib_path_is_not_flagged(tab):
    tab.select("gpu-temp")
    tab.cmd.setText("/var/lib/lianli-panel/gpu.sh")
    assert tab.static_notes.text() == ""


def test_the_authoritative_test_asks_before_executing(tab, monkeypatch):
    """Both tiers really run the command. The spec requires a warning before
    testing something with side effects."""
    from PySide6.QtWidgets import QMessageBox
    asked = []

    def record(*args, **kwargs):
        asked.append(args)
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", record)
    tab.select("gpu-temp")
    tab.test_button.click()
    assert asked
    assert any("side effects" in str(a) for a in asked[0])


def test_declining_the_confirmation_runs_nothing(tab, monkeypatch):
    decline(monkeypatch)
    requested = []
    tab.probe_requested.connect(requested.append)
    tab.select("gpu-temp")
    tab.test_button.click()
    assert requested == []


def test_accepting_the_confirmation_asks_the_window_to_probe(tab, monkeypatch):
    accept(monkeypatch)
    requested = []
    tab.probe_requested.connect(requested.append)
    tab.select("gpu-temp")
    tab.cmd.setText("/var/lib/lianli-panel/gpu.sh")
    tab.test_button.click()
    assert requested == ["/var/lib/lianli-panel/gpu.sh"]


def test_the_authoritative_result_is_labelled_as_the_authority(tab):
    tab.show_probe(b"\xff\xd8\xff\xd9")
    assert "authoritative" in tab.probe_caption.text().lower()
    assert "lianli" in tab.probe_caption.text()


def test_the_diagnostic_result_is_labelled_as_not_authoritative(tab,
                                                               monkeypatch):
    """It runs as the wrong uid and will succeed on paths the daemon cannot
    reach. Presenting it as equivalent is the failure mode."""
    accept(monkeypatch)
    monkeypatch.setattr(
        "lianli_panel.sensors.run_diagnostic",
        lambda cmd, timeout=10.0: Diagnostic("42\n", "", 0, 42.0, []))
    tab.select("gpu-temp")
    tab.diagnose_button.click()
    assert "not authoritative" in tab.diagnostic_caption.text().lower()


def test_the_diagnostic_shows_raw_stdout_beside_the_parsed_value(tab,
                                                                monkeypatch):
    """nvidia-smi prints its usage errors to STDOUT, so a parse-each-line loop
    swallows them. Showing raw stdout is how that becomes visible."""
    accept(monkeypatch)
    monkeypatch.setattr(
        "lianli_panel.sensors.run_diagnostic",
        lambda cmd, timeout=10.0: Diagnostic(
            "Invalid combination of input arguments\n", "", 0, None,
            ["first token 'Invalid' does not parse as a number"]))
    tab.select("gpu-temp")
    tab.diagnose_button.click()
    text = tab.diagnostic_output.toPlainText()
    assert "Invalid combination" in text
    assert "does not parse" in text


def test_creating_a_sensor_adds_it_and_reports_the_change(tab):
    seen = []
    tab.library_changed.connect(lambda: seen.append(1))
    tab.create("new-sensor")
    assert "new-sensor" in tab.names()
    assert seen


def test_deleting_a_sensor_removes_it(tab):
    tab.select("cpu-temp")
    tab.delete_selected()
    assert "cpu-temp" not in tab.names()


def test_renaming_a_sensor_keeps_its_source(tab):
    tab.select("cpu-temp")
    tab.rename_selected("cpu")
    assert tab.library()["cpu"].source["name"] == "coretemp"
    assert "cpu-temp" not in tab.names()


def test_binding_is_disabled_with_nothing_selected_on_the_editor_tab(tab):
    tab.set_target(None, None, "")
    assert not tab.bind_button.isEnabled()
    assert "select" in tab.target_label.text().lower()


def test_binding_names_the_widget_it_would_bind_to(tab):
    """Tabs mean the canvas is not on screen, so the tab has to say out loud
    what 'the selected widget' currently is."""
    tab.set_target("gaming-dash", "gpu-text", "value_text")
    assert "gaming-dash" in tab.target_label.text()
    assert "gpu-text" in tab.target_label.text()
    assert tab.bind_button.isEnabled()


def test_binding_asks_the_window_to_bind_the_selected_sensor(tab):
    tab.set_target("gaming-dash", "gpu-text", "value_text")
    tab.select("gpu-temp")
    requested = []
    tab.bind_requested.connect(requested.append)
    tab.bind_button.click()
    assert requested == ["gpu-temp"]
```

- [ ] **Step 6: Implement the tab**

Create `lianli_panel/gui/sensors_tab.py`. Follow the tests above exactly for the public names (`names`, `select`, `create`, `rename_selected`, `delete_selected`, `library`, `set_library`, `set_target`, `field_names`, `show_probe`, `show_probe_error`, and the widget attributes `type_box`, `command_group`, `cmd`, `static_notes`, `test_button`, `diagnose_button`, `diagnostic_output`, `diagnostic_caption`, `probe_caption`, `bind_button`, `target_label`).

```python
"""The sensor editor, and the two-tier command harness.

THE TIERS ARE NOT EQUIVALENT, and the UI must never let them look it:

  AUTHORITATIVE  a throwaway one-widget template whose value_text source is the
                 candidate, sent to RenderTemplatePreview. The daemon runs it
                 as uid lianli, under exactly the conditions the real sensor
                 faces, and returns the number as an image. This is the only
                 tier that proves anything.

  DIAGNOSTIC     the same command run as the current user, capturing stdout,
                 stderr and exit status -- which the image cannot show. Richer,
                 and WRONG UID: it succeeds happily on $HOME paths the daemon
                 cannot traverse. Every label here says so.

BOTH TIERS EXECUTE THE COMMAND, so both are behind a confirmation. Neither ever
runs on the debounced preview path.

Raw stdout is shown beside the parsed value because the invisible failure is a
tool that prints its errors to STDOUT: nvidia-smi rejects -lms2000 that way, and
a parse-each-line-as-data loop reads the usage message as a sample.
"""
```

Build it as: a `QListWidget` of names on the left with New / Duplicate / Rename / Delete; on the right a `QComboBox` of `schema.SOURCE_NAMES` above a `QFormLayout` rebuilt from `forms.source_fields_for(src)` on every type change; below that a `command_group` visible only when the type is `command`, holding the `cmd` line edit, a live `static_notes` label fed by `sensors.static_checks(cmd)` on every keystroke, the two buttons, the `probe_caption` + image label, and the `diagnostic_caption` + read-only `diagnostic_output`; and at the bottom the `target_label` and `bind_button`.

Its imports are `from .. import sensors`, `from ..sensors import Sensor, USER_SCRIPT_DIR`, `from ..schema import SOURCE_NAMES`, `from . import forms`, and the Qt widgets it uses. Call `sensors.run_diagnostic` through the module (`sensors.run_diagnostic(...)`, never a `from`-imported name) so the tests above can monkeypatch it.

Required copy, verbatim:

```python
PROBE_CONFIRM = (
    "This runs the command for real, as uid lianli, inside the daemon.\n\n"
    "If it has side effects — writing a file, resetting a counter, changing a "
    "device — they will happen now.\n\nRun it?")

DIAGNOSTIC_CONFIRM = (
    "This runs the command for real, as you, not as lianli.\n\n"
    "It can block this window for up to 10 seconds, and it will succeed on "
    "paths under your home directory that the daemon can never reach.\n\nRun it?")

PROBE_CAPTION = (
    "AUTHORITATIVE — the daemon rendered this as uid lianli, under exactly the "
    "conditions the real sensor faces. If the number is wrong here, it is "
    "wrong on the panel.")

DIAGNOSTIC_CAPTION = (
    "NOT AUTHORITATIVE — this ran as you, not as lianli. It sees files under "
    f"your home directory that the daemon cannot, so a pass here is not a pass "
    f"on the panel. Scripts the daemon must read belong in "
    f"{USER_SCRIPT_DIR}.")
```

`_diagnose()` writes into `diagnostic_output`:

```python
    def _render_diagnostic(self, d) -> None:
        lines = [f"exit status: {d.exit_code}",
                 f"parsed value: {d.parsed}",
                 "",
                 "stdout (raw — the daemon reads the FIRST WHITESPACE TOKEN of "
                 "this, so an error printed here is read as data):",
                 d.stdout.rstrip() or "(nothing)",
                 "",
                 "stderr:",
                 d.stderr.rstrip() or "(nothing)"]
        if d.problems:
            lines += ["", "problems:"] + [f"  ! {p}" for p in d.problems]
        self.diagnostic_output.setPlainText("\n".join(lines))
```

`set_target`:

```python
    def set_target(self, template_id, widget_id, description: str) -> None:
        """Which widget 'Bind' would bind to. With tabs the canvas is off
        screen, so this has to be said out loud rather than assumed."""
        self._target = (template_id, widget_id)
        if template_id is None or widget_id is None:
            self.target_label.setText(
                "Nothing selected — select a widget on the Editor tab to bind "
                "a sensor to it.")
            self.bind_button.setEnabled(False)
            return
        self.target_label.setText(
            f"Selected on the Editor tab: {template_id} / {widget_id}"
            + (f" ({description})" if description else ""))
        self.bind_button.setEnabled(True)
```

- [ ] **Step 7: Run the tests to verify they pass**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_sensors_tab.py tests/test_gui_forms.py -v
```

Expected: PASS.

- [ ] **Step 8: Run the whole suite**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q
```

- [ ] **Step 9: Commit**

```bash
git add lianli_panel/gui/sensors_tab.py lianli_panel/gui/forms.py lianli_panel/gui/preview.py tests/test_gui_sensors_tab.py tests/test_gui_forms.py
git commit -m "feat: add the sensor editor and its two-tier test harness

The authoritative tier renders the candidate as uid lianli through the daemon;
the diagnostic tier runs as the user and is labelled as not authoritative
everywhere it appears. Raw stdout is shown beside the parsed value because a
tool that prints errors to stdout is otherwise read as data.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

- [ ] **Step 10 (CONTROLLER — a real authoritative probe)**

Codex cannot reach the socket, so nothing so far has proved the harness works. Use the CLI path first, which exercises the same `sensors` functions:

```bash
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli sensor-test \
  '/usr/local/share/lianli-panel/vram_gb.sh' -o /tmp/probe-good.jpg
```

Expected: a diagnostic block with `exit 0` and a parsed number, and a JPEG showing that number. Open it and confirm the rendered digits match the parsed value.

Then a command the daemon genuinely cannot run, which is the case the whole tier exists for:

```bash
echo 'echo 42' > ~/probe-unreadable.sh && chmod +x ~/probe-unreadable.sh
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli sensor-test \
  "$HOME/probe-unreadable.sh" -o /tmp/probe-bad.jpg
```

Expected: the **diagnostic passes** (it runs as `chase`, who can read the file) while the **authoritative image shows 0 or an error** (the daemon runs as `lianli` and cannot traverse `/home/chase`, mode 0700), and the static check names the path. That divergence is the entire justification for two tiers — record both outcomes verbatim in the ledger. Then `rm ~/probe-unreadable.sh`.

Finally check the journal for what the daemon thought it was doing:

```bash
journalctl _COMM=lianli-daemon --since "5 minutes ago" | tail -30
```

---

## Task 8: Tabs, and one Apply that commits everything

**Files:**
- Modify: `lianli_panel/gui/window.py`
- Test: `tests/test_gui_window_tabs.py`

**Interfaces:**
- Consumes: everything from Tasks 1–7.
- Produces: `MainWindow(client, *, health_poller=None, lighting_ops=None)` — `lighting_ops` defaults to `apply_mod.default_ops()` and exists so tests never shell out to `systemctl`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_window_tabs.py`:

```python
"""The window with three tabs and one Apply.

These are smoke tests in the same sense as test_gui_smoke.py: they catch import
errors, bad signal signatures and null derefs. They are NOT evidence the app
works -- that is steps 11 and 12, run by the controller against the panel.
"""
import pytest

from lianli_panel import apply as apply_mod
from lianli_panel import lighting
from lianli_panel.ring import RingState, ThermalConfig
from tests.test_gui_smoke import make_client, stub_poller

CONFIG_WITH_BRIGHTNESS = {
    "lcds": [{"serial": "hid:513b5a7acadc4203", "type": "custom",
              "template_id": "gaming-dash", "orientation": 90,
              "brightness": 200}]}


def fake_ops(**over):
    world = {"running": True, "thermal": ThermalConfig(), "ring": None,
             "brightness": None, "ring_state": None}
    ops = apply_mod.LightingOps(
        read_thermal=lambda: world["thermal"],
        write_thermal=lambda cfg: world.update(thermal=cfg),
        poller_active=lambda: world["running"],
        stop_poller=lambda: world.update(running=False),
        start_poller=lambda: world.update(running=True),
        set_ring=lambda c, m, col, b: world.update(ring=(m, col, b)),
        set_lcd_brightness=lambda c, d, v: world.update(brightness=(d, v)),
        save_ring_state=lambda m, col, b: world.update(ring_state=(m, col, b)))
    for key, value in over.items():
        setattr(ops, key, value)
    return ops, world


@pytest.fixture
def win(qapp):
    from lianli_panel.gui.window import MainWindow
    ops, world = fake_ops()
    w = MainWindow(make_client(GetConfig=CONFIG_WITH_BRIGHTNESS),
                   health_poller=stub_poller(), lighting_ops=ops)
    w._test_world = world
    yield w
    w.close()


def test_the_window_has_the_three_tabs(win):
    labels = [win.tabs.tabText(i) for i in range(win.tabs.count())]
    assert labels == ["Editor", "Sensors", "Lighting"]


def test_the_lighting_tab_loads_the_brightness_the_daemon_has_stored(win):
    """config.lcds[].brightness is the one lighting value with a real
    read-back. Ignoring it would show a default the panel is not using."""
    assert win.lighting_tab.state().screen_brightness == 200


def test_a_panel_that_has_never_had_a_brightness_set_shows_none(qapp):
    from lianli_panel.gui.window import MainWindow
    ops, _ = fake_ops()
    w = MainWindow(make_client(), health_poller=stub_poller(),
                   lighting_ops=ops)
    assert w.lighting_tab.state().screen_brightness is None
    w.close()


def test_the_lighting_tab_shows_whether_the_poller_is_running(win):
    assert "ACTIVE" in win.lighting_tab.poller_status.text()


def test_apply_routes_through_apply_all(win, monkeypatch, tmp_path):
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    calls = []
    monkeypatch.setattr(apply_mod, "apply_all",
                        lambda *a, **k: calls.append(k) or [])
    win.apply_now()
    assert calls and "draft_lighting" in calls[0]


def test_apply_sends_the_lighting_edits_made_on_the_tab(win, monkeypatch,
                                                        tmp_path):
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    win.lighting_tab.set_state(lighting.LightingState(
        mode="static", color=(255, 0, 0), ring_brightness=3))
    win.apply_now()
    assert win._test_world["ring"] == ("static", (255, 0, 0), 3)


def test_a_validate_warning_is_shown_and_does_not_block(win, monkeypatch,
                                                        tmp_path):
    """Plan A computed warnings and threw them away. A no-catch-all-range
    warning means values above the last threshold have no colour -- worth
    saying, not worth refusing."""
    from lianli_panel.model import Problem
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    monkeypatch.setattr("lianli_panel.gui.window.validate",
                        lambda t: [Problem("warning", "cpu", "no catch-all range")])
    applied = []
    monkeypatch.setattr(apply_mod, "apply_all",
                        lambda *a, **k: applied.append(1) or [])
    win.apply_now()
    assert applied
    assert "no catch-all range" in win.banner.text()


def test_a_partial_apply_reports_every_stage(win, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr("lianli_panel.snapshot.SNAPSHOT_ROOT", tmp_path)
    shown = []
    monkeypatch.setattr(QMessageBox, "critical",
                        lambda *a, **k: shown.append(a[2]))

    def boom(*a, **k):
        raise apply_mod.PartialApply("SetLcdMedia refused", [
            apply_mod.StageResult("poller unit", "rolled back"),
            apply_mod.StageResult("templates", "failed", "refused")])

    monkeypatch.setattr(apply_mod, "apply_all", boom)
    win.apply_now()
    assert shown and "poller unit" in shown[0] and "rolled back" in shown[0]


def test_closing_prompts_when_only_the_lighting_changed(win, monkeypatch):
    """The draft is clean, but stopping the poller and driving the ring is
    still unapplied work."""
    from PySide6.QtWidgets import QMessageBox
    asked = []
    monkeypatch.setattr(QMessageBox, "question",
                        lambda *a, **k: asked.append(1)
                        or QMessageBox.StandardButton.Yes)
    win.show()
    win.lighting_tab.set_state(lighting.LightingState(mode="off"))
    win.lighting_tab.changed.emit()
    win.close()
    assert asked


def test_the_sensors_tab_target_follows_the_editor_selection(win):
    win._select("cpu")
    assert "cpu" in win.sensors_tab.target_label.text()


def test_deselecting_clears_the_sensors_tab_target(win):
    win._select("cpu")
    win._select("")
    assert not win.sensors_tab.bind_button.isEnabled()


def test_binding_a_sensor_rewrites_the_widgets_source_and_dirties_the_draft(win):
    from lianli_panel.sensors import Sensor
    win.sensors_tab.set_library(
        {"cpu-pct": Sensor("cpu-pct", {"type": "cpu_usage"})})
    win._select("cpu")
    win._bind_sensor("cpu-pct")
    assert win.draft.widget("cpu").source == {"type": "cpu_usage"}
    assert win.draft.dirty


def test_a_stale_link_is_dropped_and_the_reason_is_shown(win, monkeypatch):
    """The side-car map cannot know that something else edited the template,
    so it is validated on load and says what it dropped."""
    from lianli_panel.gui import links
    from lianli_panel.sensors import Sensor
    monkeypatch.setattr(links, "load", lambda path=None: {
        ("gaming-dash", "cpu"): "cpu-pct"})
    win.sensors_tab.set_library(
        {"cpu-pct": Sensor("cpu-pct", {"type": "command", "cmd": "elsewhere"})})
    win.revalidate_links()
    assert "cpu-pct" in win.banner.text()
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_window_tabs.py -v
```

Expected: FAIL — `TypeError: MainWindow.__init__() got an unexpected keyword argument 'lighting_ops'`.

- [ ] **Step 3: Take the injectable ops and load the lighting state**

In `lianli_panel/gui/window.py`, extend `__init__`'s signature and add the lighting load. The ops are injectable for the same reason `health_poller` is: the real ones shell out to `systemctl`, and tests must not.

```python
    def __init__(self, client, *, health_poller=None, lighting_ops=None) -> None:
        ...
        self.ops = lighting_ops or apply_mod.default_ops()
        self.base_lighting = lighting.LightingState()
        self.links: dict = {}
```

and add to `load()`, after the draft is built:

```python
        entry = next((e for e in config.get("lcds") or []
                      if e.get("serial") == apply_mod.LCD_SERIAL), {})
        state = lighting.LightingState(
            mode=ring.load_ring_state().mode,
            color=ring.load_ring_state().color,
            ring_brightness=ring.load_ring_state().brightness,
            # The one lighting value with a real read-back. None means the
            # daemon has never been told, and 0 would be a black screen.
            screen_brightness=entry.get("brightness"),
            thermal=ring.load_thermal())
        self.base_lighting = state.copy()
        self.lighting_tab.set_state(state)
        self.lighting_tab.set_last_set(ring.load_ring_state())
        self.lighting_tab.set_poller_running(self.ops.poller_active())
        self.revalidate_links()
```

Assign `last = ring.load_ring_state()` once above rather than calling it three times.

- [ ] **Step 4: Build the tab bar**

Replace the central-widget construction. The Editor tab is exactly today's layout, moved into a `QWidget`:

```python
        editor = QWidget()
        editor.setLayout(body)

        self.sensors_tab = SensorsTab()
        self.sensors_tab.library_changed.connect(self._library_changed)
        self.sensors_tab.bind_requested.connect(self._bind_sensor)
        self.sensors_tab.probe_requested.connect(self._probe_sensor)
        self.sensors_tab.set_library(sensors.load())

        self.lighting_tab = LightingTab()
        self.lighting_tab.changed.connect(self._lighting_edited)
        self.lighting_tab.test_requested.connect(self._test_ring)

        self.tabs = QTabWidget()
        self.tabs.addTab(editor, "Editor")
        self.tabs.addTab(self.sensors_tab, "Sensors")
        self.tabs.addTab(self.lighting_tab, "Lighting")

        root = QVBoxLayout()
        root.addWidget(self.banner)
        root.addWidget(self.tabs, 1)
```

`body` is the existing `QHBoxLayout`. Add the imports: `QTabWidget` to the `QtWidgets` list, and `from .. import lighting, ring, sensors`, `from .lighting_tab import LightingTab`, `from .sensors_tab import SensorsTab`, `from . import links`.

- [ ] **Step 5: Track lighting dirtiness and keep the target in sync**

```python
    def _lighting_edited(self) -> None:
        state = self.lighting_tab.state()
        self.lighting_tab.set_problems(lighting.problems(state))

    def lighting_dirty(self) -> bool:
        return self.lighting_tab.state() != self.base_lighting

    def _test_ring(self) -> None:
        """The ONE control that reaches the hardware before Apply, and only
        because the user pressed a button captioned as doing exactly that --
        the same rule as the Editor tab's 'Refresh (runs sensors)'."""
        state = self.lighting_tab.state()
        try:
            ring.set_mode(self.client, state.mode, tuple(state.color),
                          state.ring_brightness)
        except (DaemonError, ValueError, RuntimeError) as exc:
            self._warn(f"could not drive the ring: {exc}", key="ring")
            return
        self.banner.clear("ring")
        self.statusBar().showMessage(
            "sent to the ring — it cannot be read back, so look at it", 8000)
```

Extend `_select` so the Sensors tab always knows what the Editor tab has selected:

```python
    def _select(self, widget_id: str) -> None:
        self.draft.selection = widget_id or None
        widget = self.draft.widget(widget_id) if widget_id else None
        self.inspector.set_widget(widget)
        self.sensors_tab.set_target(
            self.draft.current_id if widget else None,
            widget_id or None,
            widget.kind_type if widget else "")
```

- [ ] **Step 6: Wire the sensor library, binding and probing**

```python
    def _library_changed(self) -> None:
        sensors.save(self.sensors_tab.library())
        self.revalidate_links()

    def _bind_sensor(self, name: str) -> None:
        widget_id = self.draft.selection
        sensor = self.sensors_tab.library().get(name)
        if widget_id is None or sensor is None:
            return
        widget = self.draft.widget(widget_id)
        if widget is None:
            return
        self.draft.checkpoint()
        widget.kind["source"] = copy.deepcopy(sensor.source)
        self.links = links.bind(self.links, self.draft.current_id, widget_id,
                                name)
        links.save(self.links)
        self.draft.dirty = True
        self.inspector.set_widget(widget)
        self.rerender()
        self.statusBar().showMessage(f"{widget_id} now reads {name}", 8000)

    def _probe_sensor(self, cmd: str) -> None:
        if not self.probe.probe(cmd):
            self.statusBar().showMessage("a probe is already running", 3000)

    def revalidate_links(self) -> None:
        """A side-car map cannot know that apply.sh or lianli-gui edited a
        template, so it is checked against the templates every time either
        changes. A dropped link is SHOWN -- silently forgetting one looks
        identical to never having made it."""
        kept, dropped = links.validate(links.load(), self.draft.payload(),
                                       self.sensors_tab.library())
        self.links = kept
        if dropped:
            self.banner.show_banner("links", "\n".join(
                f"{d.link[0]}/{d.link[1]} is no longer bound to {d.name}: "
                f"{d.reason}" for d in dropped), "warn")
        else:
            self.banner.clear("links")
```

Add `import copy` to the module imports, and construct the probe worker beside the preview worker:

```python
        self.probe = ProbeWorker(client, parent=self)
        self.probe.done.connect(self.sensors_tab.show_probe)
        self.probe.failed.connect(self.sensors_tab.show_probe_error)
```

`ProbeWorker` joins the existing `from .preview import PreviewWorker` import, and `self.probe.stop()` joins `closeEvent`'s teardown.

- [ ] **Step 7: Surface validate's warnings**

Plan A's `apply_now` filtered to `level == "error"` and discarded the rest. Replace that block:

```python
        current = self.draft.current()
        if current is not None:
            problems = validate(current)
            warnings = [p for p in problems if p.level == "warning"]
            if warnings:
                # Computed and thrown away until now. "No catch-all range"
                # means values above the last threshold have no colour at all
                # -- worth saying, not worth refusing.
                self.banner.show_banner("validate", "\n".join(
                    f"{p.widget_id}: {p.message}" for p in warnings), "warn")
            else:
                self.banner.clear("validate")
            errors = [p for p in problems if p.level == "error"]
            if errors:
                listing = "\n".join(f"{p.widget_id}: {p.message}" for p in errors)
                if QMessageBox.question(
                        self, "Apply anyway?",
                        f"This template has errors:\n\n{listing}"
                ) != QMessageBox.StandardButton.Yes:
                    return
```

- [ ] **Step 8: Route Apply through `apply_all`**

First give the existing snapshot call the injectable Task 3 added, so apply tests stop shelling out to `systemctl`:

```python
            snap = snapshot.take(self.client,
                                 poller_active=self.ops.poller_active)
```

Then replace the `apply_mod.apply_templates(...)` call and its handlers:

```python
        draft_lighting = self.lighting_tab.state()
        try:
            stages = apply_mod.apply_all(
                self.client, templates=self.draft.payload(),
                live_id=self.draft.live_id,
                base_lighting=self.base_lighting,
                draft_lighting=draft_lighting,
                base_hash=self.draft.base_hash,
                lcd_entry_fallback=apply_mod.lcd_entry_fallback(),
                ops=self.ops)
        except apply_mod.ConflictError as exc:
            if QMessageBox.question(
                    self, "The daemon's templates changed",
                    f"{exc}\n\nOverwrite their change with this draft?"
            ) != QMessageBox.StandardButton.Yes:
                return
            self.draft.base_hash = apply_mod.read_templates(self.client)[1]
            self.apply_now()
            return
        except apply_mod.PartialApply as exc:
            QMessageBox.critical(self, "Apply did not complete", str(exc)
                                 + "\n\n" + self._stage_report(exc.stages))
            self.load()
            return
        except apply_mod.ApplyFailed as exc:
            QMessageBox.critical(self, "Apply failed", str(exc))
            return

        self.draft.mark_applied(self.draft.payload())
        self.base_lighting = draft_lighting.copy()
        self.lighting_tab.set_poller_running(self.ops.poller_active())
        self.lighting_tab.set_last_set(ring.load_ring_state())
        self.health.poll()
        self.statusBar().showMessage(
            f"applied · live: {self.draft.live_id} · "
            + self._stage_summary(stages)
            + (f" · snapshot {snap.name}" if snap else ""), 12000)
```

`ConflictError` must be caught **before** `PartialApply` and `ApplyFailed` in source order, and `PartialApply` before `ApplyFailed`, since `PartialApply` subclasses it. Add the two reporters:

```python
    @staticmethod
    def _stage_summary(stages) -> str:
        done = [s.name for s in stages if s.status in ("done", "unverifiable")]
        return ("lighting unchanged" if not done
                else "committed: " + ", ".join(done))

    @staticmethod
    def _stage_report(stages) -> str:
        """Every stage, including the skipped ones. After a partial apply the
        user's first question is 'what state is my machine in now', and a
        summary that mentions only failures cannot answer it."""
        return "\n".join(
            f"{s.name}: {s.status}" + (f" — {s.detail}" if s.detail else "")
            for s in stages)
```

- [ ] **Step 9: Extend the close prompt**

```python
    def closeEvent(self, event) -> None:
        unapplied = self.draft.dirty or self.lighting_dirty()
        if self.isVisible() and unapplied and QMessageBox.question(
                self, "Unapplied changes",
                "This draft has changes that were never applied. Close anyway?"
        ) != QMessageBox.StandardButton.Yes:
            event.ignore()
            return
        self.worker.stop()
        self.probe.stop()
        self.health.stop()
        super().closeEvent(event)
```

- [ ] **Step 10: Run the tests**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_window_tabs.py tests/test_gui_smoke.py -v
```

Expected: PASS, and the Plan A smoke tests unchanged. Then the whole suite:

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q
```

- [ ] **Step 11: Commit**

```bash
git add lianli_panel/gui/window.py tests/test_gui_window_tabs.py
git commit -m "feat: three tabs, and one Apply that commits templates and lighting

Apply now routes through apply_all and reports every stage, including the two
that cannot be verified. validate's warnings are shown instead of discarded,
and closing prompts when only the lighting is dirty.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

- [ ] **Step 12 (CONTROLLER — the first unified Apply against the panel)**

**Snapshot first, by hand, and record the hash:**

```bash
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli snapshot
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli list
```

Launch the app and, in this order:

1. **Editor tab** — move one widget 20px. Do not apply yet.
2. **Lighting tab** — confirm the poller status matches `systemctl --user is-active`, and that the brightness slider shows whatever Task 3 step 10 left (or is unchecked if brightness has never been set).
3. Select **Static**, pick a colour, and confirm the amber interlock appears naming the service.
4. Press **Test on ring**. Expected: the ring flashes and the poller takes it back within ~2s, because Apply has not run and the service is still up. That is correct behaviour and the status bar says the ring cannot be read back.
5. Press **Apply**.

Expected, all four:
- the status bar reads `applied · live: gaming-dash · committed: poller unit, ring effect, templates`
- `systemctl --user is-active lianli-thermal-rgb.service` now reports **inactive**
- the ring holds the chosen colour and **does not** revert
- `journalctl _COMM=lianli-daemon -n 20` shows `Prepared custom template for LCD[serial:hid:...]`

Then check the config **on disk**, not through `GetConfig`:

```bash
sudo jq '.lcds' /var/lib/lianli/config.json
```

Expected: exactly ONE entry for this serial. More than one means the `entry_key` regression is back and everything after this step is suspect.

6. Set the brightness slider to something obviously different and Apply again. Expected: the panel visibly changes, and `sudo jq '.lcds[0].brightness' /var/lib/lianli/config.json` returns that number — the persistence half working is what makes the unverifiable half acceptable.
7. Select **Thermal sweep** and Apply. Expected: the service comes back up and the ring resumes sweeping.
8. Change `hot_c` to 60 and Apply. Expected: within one poll the ring shifts markedly redder at the same temperature, with **no unit restart** — and `journalctl --user -u lianli-thermal-rgb -n 5` shows the poller's own `config loaded: {...}` line.

**Restore to the snapshot taken at the start** and confirm the template hash matches, exactly as Plan A's Task 11 did.

- [ ] **Step 13: Write the ledger entry**

Record: the observed status-bar text, whether the on-disk `lcds` array stayed single, the brightness value that reads as normal, the measured time for the ring to revert in step 4, and whether the poller logged `config loaded` in step 8.

---

## Task 9: Vendor the poller into the repo, and give the CLI its controls

Two things are wrong today and neither is a code defect. The poller is the only part of this system with no copy under version control — it exists at `/var/tmp/lianli-stats/bin/thermal-rgb.py` and `/usr/local/share/lianli-panel/thermal-rgb.py` and nowhere else. And the unit still `ExecStart`s the `/var/tmp` copy, which `/usr/lib/tmpfiles.d/tmp.conf` ages out after 30 days (`q /var/tmp 1777 root root 30d`) — the exact hazard `tools/migrate_assets.sh` was written to close, left half-closed. The script's own `sys.path.insert(0, "/var/tmp/lianli-stats/bin")` for its `ipc` import is a second, quieter instance of it.

The CLI additions belong here because the controller needs a non-GUI way to drive the poller while testing this.

**Files:**
- Create: `tools/thermal-rgb.py`, `tools/lianli-thermal-rgb.service`
- Modify: `lianli_panel/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Produces: `lianli-panel ring thermal`, `lianli-panel ring brightness <0-255>`, `lianli-panel poller [show|set --cool-c ... --hot-c ...]`

- [ ] **Step 1: Bring the script under version control unchanged**

```bash
cp /usr/local/share/lianli-panel/thermal-rgb.py tools/thermal-rgb.py
diff /var/tmp/lianli-stats/bin/thermal-rgb.py tools/thermal-rgb.py
```

If they differ, the `/var/tmp` copy is the one the running unit executes — **take that one instead** and record the divergence in the ledger before continuing.

- [ ] **Step 2: Remove its own `/var/tmp` dependency**

In `tools/thermal-rgb.py`, replace:

```python
sys.path.insert(0, "/var/tmp/lianli-stats/bin")
from ipc import call
```

with:

```python
# /var/tmp is aged out after 30 days by systemd-tmpfiles (tmp.conf: q /var/tmp
# 1777 root root 30d). This service runs for months at a time, so importing
# from there is a time bomb with no error path -- the ring simply stops one
# day. /usr/local/share is where migrate_assets.sh put the shipped scripts.
sys.path.insert(0, "/usr/local/share/lianli-panel")
from ipc import call
```

Update the `Documentation=` line in its docstring reference the same way.

- [ ] **Step 3: Write the unit file**

Create `tools/lianli-thermal-rgb.service`, copying the installed unit and repointing both paths:

```ini
[Unit]
Description=Drive the Lian Li LED ring from the hotter of CPU and GPU temperature
Documentation=file:/usr/local/share/lianli-panel/thermal-rgb.py
After=graphical-session.target

[Service]
Type=simple
# The system daemon may still be starting; wait for its IPC socket first.
ExecStart=/bin/sh -c 'i=0; while [ ! -S /run/lianli/lianli-daemon.sock ] && [ $i -lt 60 ]; do sleep 1; i=$((i+1)); done; exec /usr/bin/python3 /usr/local/share/lianli-panel/thermal-rgb.py'
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

- [ ] **Step 4: Add the CLI commands**

In `lianli_panel/cli.py`'s `build_parser`, extend the `ring` subparser and add `poller`:

```python
    rsub.add_parser("thermal", help="hand the ring back to the poller")
    rb = rsub.add_parser("brightness", help="set the PANEL's backlight, 0-255")
    rb.add_argument("value", type=int)

    po = sub.add_parser("poller", help="the thermal poller's configuration")
    posub = po.add_subparsers(dest="poller_cmd", required=True)
    posub.add_parser("show")
    ps = posub.add_parser("set")
    ps.add_argument("--cool-c", type=float)
    ps.add_argument("--hot-c", type=float)
    ps.add_argument("--poll-ms", type=int)
    ps.add_argument("--min-delta-c", type=float)
    ps.add_argument("--force-refresh-s", type=int)
    ps.add_argument("--brightness", type=int, help="the RING's, 0-4")
```

and in `main`, extend the `ring` branch and add `poller`:

```python
        if args.cmd == "ring":
            if args.ring_cmd == "off":
                ring.set_off(client)
                print("ring off")
            elif args.ring_cmd == "static":
                ring.set_static(client, (args.r, args.g, args.b))
                print(f"ring static {args.r},{args.g},{args.b}")
            elif args.ring_cmd == "brightness":
                # NOT the ring's brightness. This is the panel's backlight, and
                # the call cannot report failure -- see ring.set_lcd_brightness.
                ring.set_lcd_brightness(client, apply_mod.find_lcd(client),
                                        args.value)
                print(f"panel brightness {args.value} sent (SetLcdBrightness "
                      "cannot report failure; look at the screen)")
                return 0
            else:
                ring.start_poller()
                print("thermal poller started; it now owns the ring")
                return 0
            print(ring.RGB_APPLY_WARNING)
            return 0

        if args.cmd == "poller":
            cfg = ring.load_thermal()
            if args.poller_cmd == "show":
                print(json.dumps(cfg.to_json(), indent=1))
                print("active" if ring.poller_active() else "not running")
                return 0
            for name in ("cool_c", "hot_c", "poll_ms", "min_delta_c",
                         "force_refresh_s", "brightness"):
                value = getattr(args, name, None)
                if value is not None:
                    setattr(cfg, name, value)
            ring.save_thermal(cfg)
            print(json.dumps(cfg.to_json(), indent=1))
            print("the poller re-reads this within one poll interval; no "
                  "restart needed")
            return 0
```

Note `ring static` no longer stops the poller for you — that remains `apply_all`'s job, and `RGB_APPLY_WARNING` already tells the CLI user what to do.

Also add `ValueError` to the exception tuple `main` catches, or `poller set --cool-c 90 --hot-c 50` tracebacks instead of exiting 2 — `ring.save_thermal` raises `ValueError`, which is not in Plan A's list:

```python
    except (DaemonError, apply_mod.ApplyFailed, apply_mod.ConflictError,
            RuntimeError, ValueError) as exc:
```

- [ ] **Step 5: Write the CLI tests**

`tests/test_cli.py` currently imports only `pytest` and `build_parser`, so add `from tests.conftest import FakeClient` at the top. Extend its existing `test_parser_exposes_every_subcommand` with the new forms:

```python
                 ["ring", "thermal"], ["ring", "brightness", "120"],
                 ["poller", "show"], ["poller", "set", "--hot-c", "90"],
```

Then append:

```python
def test_ring_brightness_targets_the_panel_not_the_ring(monkeypatch, capsys):
    """Two things called brightness, one command name. The test exists so a
    later refactor cannot quietly point this at the ring."""
    from lianli_panel import cli, ring
    sent = []
    monkeypatch.setattr(ring, "set_lcd_brightness",
                        lambda c, d, v: sent.append((d, v)))
    monkeypatch.setattr(cli, "Client", lambda: FakeClient(
        {"ListDevices": [{"device_id": "hid:abc", "has_lcd": True}]}))
    assert cli.main(["ring", "brightness", "120"]) == 0
    assert sent == [("hid:abc", 120)]


def test_poller_set_writes_only_the_fields_given(monkeypatch, tmp_path,
                                                 capsys):
    from lianli_panel import cli, ring
    path = tmp_path / "thermal-rgb.json"
    monkeypatch.setattr(ring, "THERMAL_CONFIG_PATH", path)
    monkeypatch.setattr(cli, "Client", lambda: FakeClient({}))
    cli.main(["poller", "set", "--hot-c", "90"])
    written = ring.load_thermal(path)
    assert written.hot_c == 90.0
    assert written.cool_c == 45.0        # untouched default


def test_poller_set_refuses_an_inverted_sweep(monkeypatch, tmp_path):
    from lianli_panel import cli, ring
    monkeypatch.setattr(ring, "THERMAL_CONFIG_PATH",
                        tmp_path / "thermal-rgb.json")
    monkeypatch.setattr(cli, "Client", lambda: FakeClient({}))
    assert cli.main(["poller", "set", "--cool-c", "90", "--hot-c", "50"]) == 2
```

- [ ] **Step 6: Run the tests**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_cli.py -v
```

- [ ] **Step 7 (CONTROLLER): install and repoint**

```bash
sudo install -o root -g root -m 755 tools/thermal-rgb.py \
  /usr/local/share/lianli-panel/thermal-rgb.py
install -m 644 tools/lianli-thermal-rgb.service \
  ~/.config/systemd/user/lianli-thermal-rgb.service
systemctl --user daemon-reload
systemctl --user restart lianli-thermal-rgb.service
systemctl --user status lianli-thermal-rgb.service --no-pager
```

Expected: `active (running)`, and `ExecStart` now naming `/usr/local/share`. Watch it for one cycle:

```bash
journalctl --user -u lianli-thermal-rgb -n 20 --no-pager
```

Expected: no `ModuleNotFoundError` for `ipc` — that is what step 2 was for. Then confirm the ring is still sweeping by eye.

- [ ] **Step 8 (CONTROLLER): prove the /var/tmp copy is no longer load-bearing**

```bash
mv /var/tmp/lianli-stats/bin/thermal-rgb.py /var/tmp/lianli-stats/bin/thermal-rgb.py.disabled
systemctl --user restart lianli-thermal-rgb.service
sleep 5
systemctl --user is-active lianli-thermal-rgb.service
```

Expected: `active`. If it fails, something still reads `/var/tmp` — find it before continuing. Leave the file renamed; Task 11 decides whether to delete it.

- [ ] **Step 9: Exercise the CLI against the real poller**

```bash
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli poller show
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli poller set --hot-c 60
journalctl --user -u lianli-thermal-rgb -n 5 --no-pager
```

Expected: the poller logs `config loaded: {...}` with `hot_c: 60.0` **without a restart**. Put it back with `--hot-c 85`.

- [ ] **Step 10: Commit**

```bash
git add tools/thermal-rgb.py tools/lianli-thermal-rgb.service lianli_panel/cli.py tests/test_cli.py
git commit -m "feat: bring the thermal poller under version control

The only copies lived in /var/tmp -- which systemd-tmpfiles ages out after 30
days -- and in /usr/local/share, and the unit ExecStarted the /var/tmp one.
The script's own sys.path insert pointed there too. Adds the CLI commands
needed to drive the poller without the GUI.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

---

## Task 10: Retire `build_template.py` to a documented seed script

13.5KB of template-construction Python that predates the editor. It is not dead — a fresh machine with an empty daemon has no template to edit — but it is no longer how templates are authored, and leaving it undocumented invites someone to edit `gaming-dash` there and lose it on the next Apply.

**Files:**
- Create: `tools/seed_template.py`
- Modify: `docs/gui.md`

- [ ] **Step 1: Bring it under version control**

```bash
cp /usr/local/share/lianli-panel/build_template.py tools/seed_template.py
diff /var/tmp/lianli-stats/bin/build_template.py tools/seed_template.py
```

Record any divergence in the ledger.

- [ ] **Step 2: Give it a header that says what it is now**

Prepend to `tools/seed_template.py`, above its existing docstring:

```python
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
```

- [ ] **Step 3: Verify it still produces a valid template**

```bash
./.venv/bin/python tools/seed_template.py > /tmp/seed.json
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli validate /tmp/seed.json
```

Expected: exit 0, or warnings only. If it emits errors, record them — the script is being kept as a bootstrap, and a bootstrap that produces an invalid template is worth knowing about even if it is not fixed here. **Do not apply it.**

- [ ] **Step 4: Document the two new tabs**

Add to `docs/gui.md`:

```markdown
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
```

- [ ] **Step 5: Commit**

```bash
git add tools/seed_template.py docs/gui.md
git commit -m "docs: retire build_template.py to a documented seed script

It is a bootstrap for a machine with no template, not an editor. Editing a
live template there loses the change on the next Apply, because the GUI
replaces the whole set.

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

---

## Task 11: End-to-end acceptance, and closing the plan out

Controller plus Chase. Tests passing is not evidence; the completion criterion is the app launched, changes made in it, and those changes visible on the physical panel and the physical ring.

- [ ] **Step 1: Snapshot and record the baseline**

```bash
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli snapshot
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli list
sudo jq '.lcds' /var/lib/lianli/config.json
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli poller show
```

Write the template hash and the `lcds` array into the ledger. Everything below gets restored to this.

- [ ] **Step 2: Full suite**

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest -q
```

Record the count. Plan A finished at 204; this plan adds roughly 120.

- [ ] **Step 3: Launch and walk all three tabs**

```bash
./.venv/bin/lianli-panel-gui
```

or from the desktop entry. Confirm the title bar, no banners on a healthy panel, and that all three tabs open.

- [ ] **Step 4: Author a real sensor, end to end**

In the **Sensors** tab: create a sensor, set its type to `command`, and point it at a script under `/var/lib/lianli-panel/`. Write the script from the GUI's own directory, not `$HOME`:

```bash
printf '#!/bin/sh\ncat /sys/class/hwmon/hwmon*/temp1_input 2>/dev/null | head -1 | cut -c1-2 || echo 0\n' \
  > /var/lib/lianli-panel/probe-demo.sh
chmod +x /var/lib/lianli-panel/probe-demo.sh
```

Then: run the **diagnostic**, run the **authoritative** test, and confirm the rendered number matches the parsed one. Select a `value_text` widget on the Editor tab, return to Sensors, confirm the target label names it, and **Bind**. Apply.

Expected on the panel: that widget now shows the sensor's value. Confirm in the journal:

```bash
journalctl _COMM=lianli-daemon -n 20 --no-pager
```

- [ ] **Step 5: Prove the link map is honest**

With the binding in place, edit the sensor's `cmd` in the library **without** re-binding, close the app, reopen it. Expected: a warn banner saying the widget is no longer bound and why. That is the validation working — a side-car map that stayed quiet here would be the failure this design exists to avoid.

- [ ] **Step 6: Drive the ring through all three modes**

Off → Apply (ring dark, service stopped). Static red → Apply (ring red, holds). Thermal → Apply (service up, sweep resumes). At each step confirm `systemctl --user is-active lianli-thermal-rgb.service` agrees with the tab.

- [ ] **Step 7: Prove a partial apply rolls back**

Make the template stage fail on purpose while a lighting change is pending — the cleanest way is to hold a conflicting write: in a second terminal, apply a different template with the CLI *after* the GUI's draft was opened, then press Apply in the GUI with a lighting change staged.

Expected: the conflict is caught **before** the poller is touched (Task 4's pre-flight), so the dialog offers to overwrite and `systemctl --user is-active` is unchanged. Record what actually happened — if the poller was stopped, the pre-flight check is in the wrong place and that is a real defect, not a test artefact.

- [ ] **Step 8: The stray-command-source check Plan A never ran**

Plan A's Task 11 step 10 was left undone for want of journal access, and the ledger's own correction says that access was never actually missing. Run it now, after the app has been open through steps 3–7:

```bash
journalctl _COMM=lianli-daemon --since "60 minutes ago" \
  | grep -ci "nvidia-smi\|fps.sh\|graph.sh" || true
```

Expected: `0`, apart from anything the explicit **Refresh (runs sensors)** and sensor probes caused. A steady stream means the debounced path is executing commands and the panel's own sparkline data is being corrupted by the editor.

- [ ] **Step 9: Restore the baseline and verify**

Restore the step 1 snapshot, put `poller show` back to its recorded values, return the ring to thermal, and confirm:

```bash
PYTHONPATH=. ./.venv/bin/python -m lianli_panel.cli list
sudo jq '.lcds' /var/lib/lianli/config.json
```

Expected: the same template hash as step 1, and **one** `lcds` entry. Remove `/var/lib/lianli-panel/probe-demo.sh`.

- [ ] **Step 10: Decide the fate of `/var/tmp/lianli-stats`**

Task 9 step 8 renamed the poller there and proved nothing reads it. Check what else still points at that tree:

```bash
PYTHONPATH=. ./.venv/bin/python -c "
from lianli_panel.ipc import Client
from lianli_panel import render
import json
for t in Client().call('GetLcdTemplates') or []:
    for cmd in render.command_sources(t):
        if '/var/tmp' in cmd:
            print(t['id'], cmd)
"
```

Any hit is a live template still executing a script from a directory systemd ages out. Repoint those to `/usr/local/share/lianli-panel/` **through the GUI**, apply, and re-run. Do not delete `/var/tmp/lianli-stats` in this session even when the list is empty — record that it is now safe to and let Chase do it.

- [ ] **Step 11: Commit whatever the walkthrough changed**

```bash
git add -A
git commit -m "docs: record the Plan B acceptance run

Claude-Session: https://claude.ai/code/session_018BVvqx72RHiuZz5VJiUoK4"
```

- [ ] **Step 12: Write the closing ledger entry**

Record, at minimum:

1. The full suite count, and anything skipped and why.
2. Whether the authoritative and diagnostic tiers actually diverged on the `$HOME` case (Task 7 step 10) — if they did not, the two-tier design is not earning its complexity and that is worth saying.
3. The result of step 7's partial-apply test: was the poller left untouched?
4. The step 8 count of stray command-source executions.
5. Whether anything still references `/var/tmp/lianli-stats`, and whether it is now safe to delete.
6. Anything about this daemon that contradicted the source read recorded at the top of this plan — especially `SetLcdBrightness`'s key format and its inability to fail.

---

## Spec coverage

Every Plan B item named at the end of Plan A, and where it lands:

| From the spec / Plan A's handoff | Task |
| --- | --- |
| Sensor editor: create a named sensor of any of the 14 source types | 7 (`source_fields_for` drives the form from `schema.SOURCE_TYPES`) |
| Authoritative tier — throwaway one-widget template through `RenderTemplatePreview` | 7 (already implemented in `sensors.render_authoritative`; wired and labelled here) |
| Diagnostic tier — run as the user, labelled as not authoritative | 7 |
| Checks: exit status, first token parses as `f32`, readable by `lianli` | 7 (`sensors.run_diagnostic` + `static_checks`, shown live) |
| Reporting an unreadable `$HOME` path by name | 7, verified live at step 10 |
| Warning before running a command with side effects | 7 (`PROBE_CONFIRM`, `DIAGNOSTIC_CONFIRM`) |
| Never putting a candidate on the debounced preview path | 7 (`ProbeWorker` is separate from `PreviewWorker` by design) |
| Raw stdout beside the parsed value | 7 |
| GUI-authored scripts in `/var/lib/lianli-panel/` | 7 (`USER_SCRIPT_DIR` in the copy), 11 step 4 |
| LED ring: Off / Static / thermal-sweep | 2 (`MODES`), 6, 8 |
| Colour picking for static; hue endpoints and temperature bounds for the sweep | 6 |
| Static and Off stop the poller first **and say so** | 2 (`interlock_text`), 4 (stage L2 before L3), 6 |
| Selecting thermal-sweep starts it again | 2, 4 |
| Ring device id resolved at runtime from `ListDevices` | already in `ring.find_ring`; unchanged |
| No colour read-back — show the last value set and say so | 3 (`RingState`, `RING_READBACK_NOTE`), 6 |
| Thermal poller config moves to `/var/lib/lianli-panel/thermal-rgb.json` | already done in `ring.ThermalConfig`; UI in 6, write path in 4 |
| Poller re-reads on mtime change, no restart | already in the poller; verified live in 9 step 9 |
| Constants become the defaults when the file is absent | already in `ThermalConfig`'s field defaults |
| Brightness (`SetLcdBrightness`) | 3 (the call), 4 (both halves), 6 (the control) |
| Surfacing `model.validate`'s warnings | 8 step 7 |
| Retiring `build_template.py` to a documented seed script | 10 |
| Plan A's open defect: live template read from `lcds[0]` | 1 |
| Plan A's open hazard: the poller lives only outside version control | 9 |

**Explicitly still out of scope**, from the spec's own follow-ups and unchanged by this plan:

- Auditing whether existing `command` sensors have native equivalents among the 14 source types (`hwmon` for CPU temp in particular), to remove per-second subprocess spawns. Task 11 step 10 finds the `/var/tmp` ones but does not convert any.
- Any colour read-back path for the ring. `GetZoneColors` does not work on this device; the app shows the last value it set and says so.
