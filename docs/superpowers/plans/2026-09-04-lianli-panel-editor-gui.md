# lianli-panel Editor GUI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the editing half of the lianli-panel desktop app — a PySide6 window where the panel's live render is the canvas, widgets are dragged and edited by hand, and the whole template library is applied transactionally.

**Architecture:** Five Qt-free modules carry every decision the editor makes (`geometry`, `draft`, `forms`, `interaction`, plus the already-built core), and the Qt layer is a thin view over them. That split is not stylistic: Codex's sandbox cannot reach the daemon socket, so logic that is testable with plain pytest can be dispatched while everything touching hardware stays with the controller. The canvas displays the daemon's own `RenderTemplatePreview` output rather than reimplementing 12 widget renderers.

**Tech Stack:** Python 3.14.7, PySide6 6.11.1 (system RPM `python3-pyside6`, visible through the venv's `--system-site-packages`), the existing `lianli_panel` core, pytest 9.1.1. No new dependencies. No network at runtime.

**Spec:** `docs/superpowers/specs/2026-09-04-lianli-panel-gui-design.md` — read it before starting. This plan argues from it and does not restate its reasoning.

**Predecessor:** `docs/superpowers/plans/2026-09-04-lianli-panel-core.md`, complete as of commit `8ea696d`. Every module it produced is consumed here unchanged unless a task says otherwise.

**Successor:** Plan B covers the sensor editor, the LED ring page, brightness, and the thermal poller's configuration UI. Its scope is fixed at the end of this document so nothing from the spec is lost between plans.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.14.7**, run as `./.venv/bin/python`. The venv already exists with `--system-site-packages`; PySide6 and Pillow are system RPMs and are **not** pip-installable into a clean venv here. Never recreate the venv.
- **No new third-party dependencies.** In particular there is no `pytest-qt`; Qt tests construct objects directly and call methods. Anything needing `pip install` is controller work and must be raised, not attempted.
- **Qt tests run headless:** `QT_QPA_PLATFORM=offscreen`. Exactly one `QApplication` per process — tests use the shared `qapp` fixture from `tests/conftest.py`, never construct their own.
- **`lianli_panel/gui/geometry.py`, `draft.py`, `forms.py` and `interaction.py` MUST NOT import PySide6.** A test asserts this. They are where the logic lives precisely so it can be tested without a display or a daemon.
- **Widget `x`/`y` are the widget's CENTRE**, not top-left. Confirmed: `left = scaled_x - rendered_width / 2`. Conversion happens in `geometry.py` and nowhere else.
- **Templates are authored at 1920×480**, landscape. The panel is physically 480×1920 and the daemon rotates. The canvas is fixed to that aspect.
- **Widget array order IS draw order.** Only the last widget covering a rect is visible. Never reorder implicitly — the cover-bar visibility trick depends on it.
- **`SensorRange.max` is a percentage of the widget's own `value_min..value_max` span**, not a raw reading. Every UI field is in real units; `model.py` converts. Confirmed by disassembly: clamp to `[0,1]`, ×100, first range with `max >= percentage` wins, null `max` is the fallback.
- **`SetLcdTemplates` replaces the ENTIRE stored template set.** Always send the whole library. This is why the draft owns the set rather than one template.
- **`SetLcdTemplates` alone does not update the panel.** Always follow with `SetLcdMedia`. `apply.apply_templates` is the only permitted path; never call either method directly from the GUI.
- **`RenderTemplatePreview` executes `command` sources** — twice per widget per render, as uid `lianli`. Automatic/debounced renders MUST go through `render.PreviewRenderer` with `live=False`, which substitutes them for `constant`. Only an explicit user action sends the real thing.
- **The daemon SILENTLY IGNORES unknown JSON fields.** A misspelled field name is dropped, not rejected, and a key this app fails to preserve is permanently deleted on the next save with no error anywhere.
- **Tests MUST NOT call mutating daemon methods.** No `Set*`, `Save*`, `Delete*`, `Apply*`, `Install*`, `Bind*`, `Unbind*`, `Reboot*`, `SwitchDisplayMode` against the live socket. `RenderTemplatePreview`, `Get*` and `List*` are safe. Tests that need mutation use `FakeClient` from `tests/conftest.py`.
- **Never read, print, or store values from any `.env` file.**
- **Commit messages:** plain, no `Co-Authored-By` trailer. End each with:
  `Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r`

---

## Execution: who runs which task

The routing rule from the core plan is unchanged, and it was derived from three
observed limits of Codex's sandbox, not from task difficulty:

| Limit | Observed | Consequence |
| --- | --- | --- |
| The daemon socket is unreachable | `PermissionError: [Errno 1]` on connect | Every step that talks to the daemon is controller work |
| No network | `pip install` cannot run | No task may introduce a dependency |
| Writes are workspace-scoped | — | `/var/tmp`, `/var/lib`, `~/.local/share` edits are controller work |

**Codex writes the code and makes the unit tests green. The controller owns
every step that touches the socket, the hardware, the network, or a path
outside the repo — and owns every "launch the app and look at it" step.**

### Routing

| Task | Runs as | Why |
| --- | --- | --- |
| 1 Fixture + geometry | **Split** | Controller: step 1 (copies from `/var/tmp`). Codex: steps 2–8 |
| 2 Draft state | **Codex** ← best fit | Pure; 16 tests; no I/O |
| 3 Inspector forms | **Codex** ← best fit | Pure; reads `schema.py` only |
| 4 Canvas interaction | **Codex** ← best fit | Pure state machine; 11 tests |
| 5 Preview worker + skeleton window | **Split** | Codex: steps 1–7 (`FakeClient`, offscreen). Controller: step 8, first launch against the daemon |
| 6 Canvas view | **Split** | Codex: steps 1–5. Controller: step 6, drag a widget in the real app |
| 7 Inspector + widget list | **Split** | Codex: steps 1–7. Controller: step 8 |
| 8 Library, apply, revert | **Split** | Codex: steps 1–8. Controller: steps 9–10, **the first write to the panel from the GUI** |
| 9 Span-preserving thresholds | **Codex** ← best fit | Pure `forms.py` plus a two-line inspector hook; 22+15 tests |
| 10 Health and interlock banners | **Split** | Codex: steps 1–12. Controller: step 13, the live journal and the vendor GUI |
| 11 Packaging + end-to-end | **Controller + Chase** | `pip install -e`, `~/.local/share/applications`, and a look at the physical screen |

### Handoff batches

Per `chase-workflow:controller-budget`: a controller's cost is context floor ×
turn count, so **hand off after Task 3, after Task 6, after Task 8, and after
Task 10.** Do not start the next task in a batch boundary session. The SDD
ledger under `.superpowers/sdd/` is the state of record; write it after every
task, not at the end.

### Before the first dispatch

Task 1 step 1 must be complete and committed — the fixture it copies is used by
Tasks 2 and 3, and Codex cannot read `/var/tmp`. Dispatches run in the repo
directory, **not** an isolated worktree: `.venv/` is gitignored and would not
exist in one, so every gate would fail on a missing `pytest`.

### Gate commands

Give each dispatch only its own test files:

```bash
QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_<module>.py -v
```

**Verify the gate matches tests before sending it** — a pytest path that matches
nothing still exits 0 and reads as a pass:

```bash
./.venv/bin/pytest --collect-only -q tests/test_gui_<module>.py
```

Confirm the collected count equals the count the task states. A silent
zero-match gate produces no red X, just a green run that proves nothing.

`QT_QPA_PLATFORM=offscreen` is harmless on the pure-logic files and required on
the Qt ones; set it on every gate so no dispatch has to reason about which is
which.

### Dispatch brief

Lift the task text **verbatim** from this plan. Add:

```
Work in /home/chase/Documents/Code/lianli-panel (not a worktree).
Implement ONLY Task N steps <range>. Do not start any other task.

Gate: QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_<x>.py -v
      (expect <N> passed)

Do NOT attempt any step marked as controller work: anything calling the daemon
socket at /run/lianli/lianli-daemon.sock, anything under /var/tmp, /var/lib or
~/.local/share, anything needing network, sudo, or a real display. The socket
returns EPERM in your sandbox. If a step needs one, say so and stop -- never
fabricate a result.

Do not add a dependency. There is no pytest-qt and pip cannot run here.

Treat every code block in the task as an UNVERIFIED SKETCH: it has not been run.
If it does not work, report the deviation rather than silently adapting, and do
not invent a type to work around one that does not exist.

Record what you actually observe, not what this plan predicts.

Do not run git commit. Chase commits by hand.
```

The controller then re-runs the gate itself, reviews the diff, and commits.
A Codex report of green is a claim, not verification.

### What the controller must not delegate

The "launch it and look" steps are the completion criterion, not decoration.
Per standing preference, **tests passing is not evidence the app works.** Task 8
step 10 in particular is the first time this app writes to the panel; skipping
it because the unit tests were green would leave the apply path — the one that
can destroy a template library — unverified.

---

## File structure

```
lianli_panel/gui/__init__.py        package marker
lianli_panel/gui/geometry.py        centre<->corner, view fit, hit-test, resize  [NO Qt]
lianli_panel/gui/draft.py           the in-memory template set being edited      [NO Qt]
lianli_panel/gui/forms.py           schema -> inspector field descriptors        [NO Qt]
lianli_panel/gui/interaction.py     canvas press/move/release state machine      [NO Qt]
lianli_panel/gui/preview.py         threaded render worker + debounce timer      Qt
lianli_panel/gui/canvas.py          paints the frame, selection, handles         Qt
lianli_panel/gui/inspector.py       builds editors from forms.FieldSpec          Qt
lianli_panel/gui/sidebar.py         template library + widget list               Qt
lianli_panel/gui/status.py          banner stack + threaded health/GUI poller    Qt
lianli_panel/gui/window.py          wiring, apply/revert, banners                Qt
lianli_panel/gui/app.py             main(), QApplication, dark palette           Qt
tools/lianli-panel.desktop          the launcher entry                           Task 11
docs/gui.md                         how to run it and what is not obvious        Task 11
tests/fixtures/gaming-dash.json     the real 31-widget template, in-repo
tests/test_gui_geometry.py          12 tests
tests/test_gui_draft.py             17 tests  (16 + the no-Qt assertion)
tests/test_gui_forms.py             22 tests  (16 at Task 3, +6 at Task 9)
tests/test_gui_interaction.py       13 tests
tests/test_gui_preview.py            5 tests
tests/test_gui_status.py             9 tests
tests/test_gui_smoke.py             grows 4 -> 20 across Tasks 5-10
tests/test_health.py                grows 10 -> 16 at Task 10
```

The Qt files stay small because the decisions are elsewhere. If one grows past
~250 lines, that is a signal logic has leaked into the view.

---

### Task 1: Fixture, GUI package, and canvas geometry

**Files:**

- Create: `tests/fixtures/gaming-dash.json`, `lianli_panel/gui/__init__.py`, `lianli_panel/gui/geometry.py`
- Modify: `tests/conftest.py`, `tests/test_model_roundtrip.py:8-13`
- Test: `tests/test_gui_geometry.py`

**Interfaces:**

- Consumes: nothing.
- Produces: `geometry.Rect(left, top, width, height)` with `.right`, `.bottom`, `.contains(x, y)`; `geometry.to_rect(x, y, width, height) -> Rect`; `geometry.to_centre(Rect) -> (x, y, width, height)`; `geometry.View(scale, offset_x, offset_y)` with `.to_view(Rect) -> Rect`, `.to_model_point(vx, vy) -> (x, y)`, `.to_model_delta(dvx, dvy) -> (dx, dy)`; `geometry.fit(view_w, view_h) -> View`; `geometry.hit_test(rects, x, y, after=None) -> str | None`; `geometry.handle_at(rect, x, y, tol) -> str | None`; `geometry.resize(rect, handle, dx, dy, min_size=8.0) -> Rect`; `geometry.nudge(rect, dx, dy) -> Rect`; `geometry.offscreen(rect) -> bool`; constants `BASE_W=1920`, `BASE_H=480`, `MIN_SIZE=8.0`, `HANDLE_TOL=6.0`, `HANDLES`.
- Also produces: the `qapp` and `dash_json` fixtures in `tests/conftest.py`, used by every later task.

- [ ] **Step 1: CONTROLLER — copy the real template into the repo as a fixture**

`tests/test_model_roundtrip.py` currently reads `/var/tmp/lianli-stats/gaming-dash.json` and **skips** when it is absent. That file lives in the tree systemd ages out after 30 days — the exact hazard `docs/install.md` documents — so the plan's central round-trip test is one tmpfiles sweep away from silently passing while testing nothing.

```bash
mkdir -p tests/fixtures
cp /var/tmp/lianli-stats/gaming-dash.json tests/fixtures/gaming-dash.json
./.venv/bin/python -c "import json;d=json.load(open('tests/fixtures/gaming-dash.json'));print(d['id'], len(d['widgets']), 'widgets')"
```

Expected: `gaming-dash 31 widgets`. If the file is gone, recover it from the daemon instead — it is the live template set:

```bash
./.venv/bin/python -c "
from lianli_panel.ipc import Client; import json
t = [x for x in Client().call('GetLcdTemplates') if x['id']=='gaming-dash'][0]
open('tests/fixtures/gaming-dash.json','w').write(json.dumps(t, indent=1))"
```

- [ ] **Step 2: Repoint the round-trip test at the fixture**

In `tests/test_model_roundtrip.py`, replace the path and the skip:

```python
REAL = Path(__file__).parent / "fixtures" / "gaming-dash.json"
```

and delete the `pytest.skip("gaming-dash.json not present")` branch — the file is now in the repo, so its absence is a failure, not a reason to pass.

Run: `./.venv/bin/pytest tests/test_model_roundtrip.py -v`
Expected: the same tests as before, now with nothing skipped.

- [ ] **Step 3: Add the shared fixtures**

Append to `tests/conftest.py`:

```python
import json
from pathlib import Path

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="session")
def qapp():
    """One QApplication per process. Qt aborts on a second one, and pytest
    runs every test file in the same process."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dash_json():
    """The real 31-widget gaming-dash template: every tricky construct in one
    document -- cover bars, self-gating alpha-0 ranges, command sources."""
    return json.loads((FIXTURES / "gaming-dash.json").read_text())
```

`QT_QPA_PLATFORM=offscreen` must be set in the environment; the gate command sets it.

- [ ] **Step 4: Write the failing geometry tests**

`tests/test_gui_geometry.py`:

```python
"""Canvas geometry. The centre-origin conversion is the reason this module
exists: getting it wrong offsets every widget by half its size, which looks
like a rendering bug rather than a coordinate bug."""
import pytest

from lianli_panel.gui import geometry as geo


def test_centre_origin_round_trip():
    r = geo.to_rect(960.0, 240.0, 200.0, 100.0)
    assert (r.left, r.top) == (860.0, 190.0)
    assert geo.to_centre(r) == (960.0, 240.0, 200.0, 100.0)


def test_rect_edges_and_contains():
    r = geo.Rect(10.0, 20.0, 100.0, 50.0)
    assert (r.right, r.bottom) == (110.0, 70.0)
    assert r.contains(10.0, 20.0) and r.contains(110.0, 70.0)
    assert not r.contains(9.0, 20.0)


def test_fit_letterboxes_to_the_panel_aspect():
    v = geo.fit(1920.0, 960.0)              # twice as tall as the panel
    assert v.scale == 1.0
    assert v.offset_x == 0.0
    assert v.offset_y == 240.0              # centred vertically


def test_fit_uses_the_limiting_axis():
    v = geo.fit(960.0, 960.0)
    assert v.scale == 0.5
    assert v.offset_y == pytest.approx(360.0)


def test_view_maps_a_point_back_to_model_space():
    v = geo.fit(960.0, 960.0)
    assert v.to_model_point(v.offset_x, v.offset_y) == (0.0, 0.0)
    assert v.to_model_delta(10.0, 10.0) == (20.0, 20.0)


def test_hit_test_picks_the_topmost():
    rects = [("under", geo.Rect(0, 0, 100, 100)),
             ("over", geo.Rect(0, 0, 100, 100))]
    assert geo.hit_test(rects, 50, 50) == "over"


def test_hit_test_cycles_down_the_stack():
    """Cover bars sit directly on top of what they hide, so the widget
    underneath is unselectable without this."""
    rects = [("a", geo.Rect(0, 0, 100, 100)),
             ("b", geo.Rect(0, 0, 100, 100)),
             ("c", geo.Rect(0, 0, 100, 100))]
    assert geo.hit_test(rects, 50, 50) == "c"
    assert geo.hit_test(rects, 50, 50, after="c") == "b"
    assert geo.hit_test(rects, 50, 50, after="b") == "a"
    assert geo.hit_test(rects, 50, 50, after="a") == "c"


def test_hit_test_returns_none_off_every_rect():
    rects = [("a", geo.Rect(0, 0, 10, 10))]
    assert geo.hit_test(rects, 50, 50) is None
    assert geo.hit_test(rects, 50, 50, after="a") is None


def test_handle_at_finds_corners_and_edges():
    r = geo.Rect(100, 100, 200, 200)
    assert geo.handle_at(r, 100, 100, tol=6) == "nw"
    assert geo.handle_at(r, 300, 300, tol=6) == "se"
    assert geo.handle_at(r, 200, 100, tol=6) == "n"
    assert geo.handle_at(r, 300, 200, tol=6) == "e"
    assert geo.handle_at(r, 200, 200, tol=6) is None
    assert geo.handle_at(r, 50, 50, tol=6) is None


def test_resize_clamps_to_min_size():
    r = geo.Rect(0, 0, 20, 20)
    out = geo.resize(r, "se", -100, -100, min_size=8.0)
    assert (out.width, out.height) == (8.0, 8.0)


def test_resize_from_the_west_handle_holds_the_right_edge():
    r = geo.Rect(100, 0, 100, 50)
    out = geo.resize(r, "w", 40, 0)
    assert out.left == 140.0
    assert out.right == 200.0


def test_offscreen_flags_widgets_outside_the_panel():
    assert not geo.offscreen(geo.Rect(0, 0, 1920, 480))
    assert geo.offscreen(geo.Rect(-1, 0, 100, 100))
    assert geo.offscreen(geo.Rect(1900, 0, 100, 100))
```

- [ ] **Step 5: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui'`

- [ ] **Step 6: Write the geometry module**

`lianli_panel/gui/__init__.py` is empty. `lianli_panel/gui/geometry.py`:

```python
"""Canvas geometry.

WIDGET x/y ARE THE CENTRE, not the top-left: the daemon computes
left = scaled_x - rendered_width / 2. Every centre<->corner conversion in the
app happens here so there is exactly one place to get it wrong.

Templates are authored at 1920x480 landscape; the panel is physically 480x1920
and the daemon rotates. The canvas is therefore fixed to the landscape aspect
and letterboxes inside whatever space the window gives it.

Tolerances and deltas passed in are MODEL units. The view converts before
calling -- a 6px grab radius on screen is 12 model units at scale 0.5.
"""
from __future__ import annotations

from dataclasses import dataclass

BASE_W, BASE_H = 1920.0, 480.0
MIN_SIZE = 8.0
HANDLE_TOL = 6.0
HANDLES = ("nw", "n", "ne", "e", "se", "s", "sw", "w")


@dataclass(frozen=True)
class Rect:
    left: float
    top: float
    width: float
    height: float

    @property
    def right(self) -> float:
        return self.left + self.width

    @property
    def bottom(self) -> float:
        return self.top + self.height

    def contains(self, x: float, y: float) -> bool:
        return self.left <= x <= self.right and self.top <= y <= self.bottom


def to_rect(x: float, y: float, width: float, height: float) -> Rect:
    return Rect(x - width / 2.0, y - height / 2.0, width, height)


def to_centre(r: Rect) -> tuple[float, float, float, float]:
    return (r.left + r.width / 2.0, r.top + r.height / 2.0, r.width, r.height)


@dataclass(frozen=True)
class View:
    scale: float
    offset_x: float
    offset_y: float

    def to_view(self, r: Rect) -> Rect:
        return Rect(self.offset_x + r.left * self.scale,
                    self.offset_y + r.top * self.scale,
                    r.width * self.scale, r.height * self.scale)

    def to_model_point(self, vx: float, vy: float) -> tuple[float, float]:
        return ((vx - self.offset_x) / self.scale,
                (vy - self.offset_y) / self.scale)

    def to_model_delta(self, dvx: float, dvy: float) -> tuple[float, float]:
        return (dvx / self.scale, dvy / self.scale)


def fit(view_w: float, view_h: float,
        base_w: float = BASE_W, base_h: float = BASE_H) -> View:
    scale = min(view_w / base_w, view_h / base_h)
    return View(scale,
                (view_w - base_w * scale) / 2.0,
                (view_h - base_h * scale) / 2.0)


def hit_test(rects: list[tuple[str, Rect]], x: float, y: float,
             after: str | None = None) -> str | None:
    """Topmost first. Array order is draw order, so the LAST match is on top.

    `after` walks one step DOWN the stack, which is the only way to reach a
    widget hidden under a cover bar -- the cover occupies the same rect and
    would otherwise swallow every click.
    """
    hits = [wid for wid, r in rects if r.contains(x, y)][::-1]
    if not hits:
        return None
    if after is None or after not in hits:
        return hits[0]
    return hits[(hits.index(after) + 1) % len(hits)]


def handle_at(rect: Rect, x: float, y: float,
              tol: float = HANDLE_TOL) -> str | None:
    if not (rect.left - tol <= x <= rect.right + tol
            and rect.top - tol <= y <= rect.bottom + tol):
        return None
    vertical = "n" if abs(y - rect.top) <= tol else \
               "s" if abs(y - rect.bottom) <= tol else ""
    horizontal = "w" if abs(x - rect.left) <= tol else \
                 "e" if abs(x - rect.right) <= tol else ""
    return (vertical + horizontal) or None


def resize(rect: Rect, handle: str, dx: float, dy: float,
           min_size: float = MIN_SIZE) -> Rect:
    left, top, w, h = rect.left, rect.top, rect.width, rect.height
    if "w" in handle:
        left += dx
        w -= dx
    if "e" in handle:
        w += dx
    if "n" in handle:
        top += dy
        h -= dy
    if "s" in handle:
        h += dy
    # Clamp against the OPPOSITE edge, so dragging a west handle past the east
    # one pins the left edge rather than inverting the rect.
    if w < min_size:
        if "w" in handle:
            left = rect.right - min_size
        w = min_size
    if h < min_size:
        if "n" in handle:
            top = rect.bottom - min_size
        h = min_size
    return Rect(left, top, w, h)


def nudge(rect: Rect, dx: float, dy: float) -> Rect:
    return Rect(rect.left + dx, rect.top + dy, rect.width, rect.height)


def offscreen(rect: Rect, base_w: float = BASE_W, base_h: float = BASE_H) -> bool:
    """Partly-off-canvas widgets are legal; the UI flags them, it does not
    forbid them."""
    return (rect.left < 0 or rect.top < 0
            or rect.right > base_w or rect.bottom > base_h)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_geometry.py -v`
Expected: 12 passed

Then confirm nothing regressed: `./.venv/bin/pytest -q`
Expected: the full suite green, with `test_model_roundtrip.py` no longer skipping.

- [ ] **Step 8: Commit**

```bash
git add tests/fixtures/gaming-dash.json tests/conftest.py tests/test_model_roundtrip.py \
        lianli_panel/gui/__init__.py lianli_panel/gui/geometry.py tests/test_gui_geometry.py
git commit -m "feat: add GUI package with centre-origin canvas geometry

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

---

### Task 2: The draft — an editable template set

**Files:**

- Create: `lianli_panel/gui/draft.py`
- Test: `tests/test_gui_draft.py`

**Interfaces:**

- Consumes: `model.Template`, `model.Widget`, `apply.templates_hash`, `geometry.Rect`/`to_rect`.
- Produces: `draft.Draft(templates: list[dict], live_id: str | None)` with attributes `templates: list[Template]`, `live_id`, `current_id`, `base_hash`, `selection`, `dirty`; methods `payload() -> list[dict]`, `current() -> Template | None`, `widget(wid) -> Widget | None`, `rects() -> list[tuple[str, Rect]]`, `checkpoint()`, `undo() -> bool`, `redo() -> bool`, `set_geometry(wid, x, y, w, h, *, checkpoint=True)`, `delete_widget(wid)`, `duplicate_widget(wid) -> str`, `reorder_widget(wid, delta)`, `add_template(name) -> str`, `duplicate_template(tid) -> str`, `rename_template(tid, name)`, `delete_template(tid)`, `set_live(tid)`, `mark_applied(templates: list[dict])`; module function `cover_warnings(t: Template) -> list[str]`.

- [ ] **Step 1: Write the failing draft tests**

`tests/test_gui_draft.py`:

```python
"""The draft is the only thing between an edit and the daemon.

Two properties matter more than the rest: it holds the WHOLE template set
(because SetLcdTemplates replaces the whole set), and it does not lose a byte
of any field this app does not understand.
"""
import copy

import pytest

from lianli_panel.gui.draft import Draft, cover_warnings
from lianli_panel.model import Template


def _tpl(tid, widgets=None):
    return {"id": tid, "name": tid, "base_width": 1920, "base_height": 480,
            "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
            "widgets": widgets or []}


def _w(wid, x=100.0, y=100.0, w=50.0, h=50.0, kind=None):
    return {"id": wid, "x": x, "y": y, "width": w, "height": h,
            "kind": kind or {"type": "label", "text": wid, "font_size": 20.0,
                             "color": [255, 255, 255, 255]}}


def test_draft_starts_clean_with_a_base_hash():
    d = Draft([_tpl("a")], live_id="a")
    assert d.dirty is False
    assert len(d.base_hash) == 64
    assert d.current_id == "a"


def test_set_geometry_marks_the_draft_dirty():
    d = Draft([_tpl("a", [_w("one")])], live_id="a")
    d.set_geometry("one", 200.0, 300.0, 60.0, 70.0)
    assert d.dirty is True
    assert d.widget("one").x == 200.0
    assert d.widget("one").height == 70.0


def test_payload_round_trips_unknown_fields(dash_json):
    """The daemon silently ignores unknown keys, so a key dropped here is
    deleted forever with no error anywhere."""
    d = Draft([dash_json], live_id="gaming-dash")
    assert d.payload() == [dash_json]


def test_editing_one_widget_leaves_every_other_byte_identical(dash_json):
    before = copy.deepcopy(dash_json)
    d = Draft([dash_json], live_id="gaming-dash")
    target = before["widgets"][3]["id"]
    d.set_geometry(target, 1.0, 2.0, 3.0, 4.0)
    after = d.payload()[0]
    for i, w in enumerate(after["widgets"]):
        if w["id"] == target:
            continue
        assert w == before["widgets"][i]


def test_delete_widget_preserves_draw_order():
    d = Draft([_tpl("a", [_w("one"), _w("two"), _w("three")])], live_id="a")
    d.delete_widget("two")
    assert [w.id for w in d.current().widgets] == ["one", "three"]


def test_reorder_widget_moves_it_in_draw_order():
    d = Draft([_tpl("a", [_w("one"), _w("two"), _w("three")])], live_id="a")
    d.reorder_widget("one", +1)
    assert [w.id for w in d.current().widgets] == ["two", "one", "three"]
    d.reorder_widget("one", -1)
    assert [w.id for w in d.current().widgets] == ["one", "two", "three"]
    d.reorder_widget("one", -1)          # already first: a no-op, not an error
    assert [w.id for w in d.current().widgets] == ["one", "two", "three"]


def test_duplicate_widget_gets_a_unique_id_and_lands_on_top():
    d = Draft([_tpl("a", [_w("one")])], live_id="a")
    new_id = d.duplicate_widget("one")
    assert new_id != "one"
    assert [w.id for w in d.current().widgets] == ["one", new_id]


def test_duplicate_template_gets_a_unique_id():
    d = Draft([_tpl("a")], live_id="a")
    new_id = d.duplicate_template("a")
    assert new_id != "a"
    assert {t.id for t in d.templates} == {"a", new_id}
    assert d.live_id == "a"              # duplicating never changes what is live


def test_delete_live_template_repoints_live_id():
    """Allowed, but the same apply must re-point config.lcds.template_id --
    leaving it pointing at a deleted template is how the panel goes blank."""
    d = Draft([_tpl("a"), _tpl("b")], live_id="a")
    d.delete_template("a")
    assert d.live_id == "b"


def test_delete_the_last_template_refuses():
    d = Draft([_tpl("a")], live_id="a")
    with pytest.raises(ValueError, match="last template"):
        d.delete_template("a")


def test_undo_restores_the_previous_geometry():
    d = Draft([_tpl("a", [_w("one", x=10.0)])], live_id="a")
    d.set_geometry("one", 99.0, 10.0, 50.0, 50.0)
    assert d.undo() is True
    assert d.widget("one").x == 10.0
    assert d.redo() is True
    assert d.widget("one").x == 99.0


def test_a_drag_coalesces_into_one_undo_step():
    """checkpoint=False on the intermediate moves. Without this, one drag
    leaves 40 undo entries and ctrl-Z becomes useless."""
    d = Draft([_tpl("a", [_w("one", x=10.0)])], live_id="a")
    d.checkpoint()
    for x in (20.0, 30.0, 40.0):
        d.set_geometry("one", x, 10.0, 50.0, 50.0, checkpoint=False)
    assert d.undo() is True
    assert d.widget("one").x == 10.0


def test_mark_applied_clears_dirty_and_rebases_the_hash():
    d = Draft([_tpl("a")], live_id="a")
    d.rename_template("a", "renamed")
    assert d.dirty is True
    d.mark_applied(d.payload())
    assert d.dirty is False
    assert d.base_hash != ""


def test_rename_template_keeps_the_id():
    d = Draft([_tpl("a")], live_id="a")
    d.rename_template("a", "Gaming Dash")
    assert d.templates[0].id == "a"
    assert d.templates[0].name == "Gaming Dash"


def test_cover_warning_when_a_covered_widget_is_drawn_after_the_cover():
    cover = _w("cover", kind={"type": "horizontal_bar", "value_max": 1,
                              "value_min": 0, "source": {"type": "constant", "value": 1},
                              "background_color": [0, 0, 0, 255]})
    hidden = _w("hidden")
    t = Template.from_json(_tpl("a", [cover, hidden]))
    warnings = cover_warnings(t)
    assert any("hidden" in w and "cover" in w for w in warnings)


def test_no_cover_warning_for_a_self_gating_widget():
    """The documented stack is [needs covering] -> [cover] -> [self-gating].
    A widget with an alpha-0 range hides itself and is SUPPOSED to sit last."""
    cover = _w("cover", kind={"type": "horizontal_bar", "value_max": 1,
                              "value_min": 0, "source": {"type": "constant", "value": 1},
                              "background_color": [0, 0, 0, 255]})
    gated = _w("gated", kind={"type": "value_text", "font_size": 20.0,
                              "color": [255, 255, 255, 255],
                              "source": {"type": "constant", "value": 0},
                              "value_min": 0, "value_max": 100,
                              "ranges": [{"max": 10, "color": [0, 0, 0], "alpha": 0},
                                         {"max": None, "color": [255, 255, 255], "alpha": 255}]})
    t = Template.from_json(_tpl("a", [cover, gated]))
    assert cover_warnings(t) == []


def test_cover_warning_when_two_widgets_share_one_cover():
    """Only one widget per rect can be conditionally hidden -- the second is
    hidden unconditionally, which reads as 'my widget vanished'."""
    a = _w("a")
    b = _w("b")
    cover = _w("cover", kind={"type": "horizontal_bar", "value_max": 1,
                              "value_min": 0, "source": {"type": "constant", "value": 1},
                              "background_color": [0, 0, 0, 255]})
    t = Template.from_json(_tpl("t", [a, b, cover]))
    assert any("only one" in w for w in cover_warnings(t))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_draft.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.draft'`

- [ ] **Step 3: Write the draft module**

`lianli_panel/gui/draft.py`:

```python
"""The in-memory draft.

NOTHING reaches the daemon until Apply. There is no auto-save.

The draft holds the WHOLE template set, not the template being edited, because
SetLcdTemplates replaces the entire stored set -- an editor that held one
template would delete every other one on save. base_hash is captured when the
draft opens and handed to apply_templates, which refuses to write when the
daemon's set has moved underneath it (apply.sh, lianli-gui, or a second copy of
this app).

Undo is snapshot-based: whole-payload deep copies. The set is ~20KB of JSON and
edits are human-paced, so the simple thing is fast enough, and it cannot drift
from the model the way a command-log can.
"""
from __future__ import annotations

import copy
from typing import Any

from ..apply import templates_hash
from ..model import Template, Widget
from . import geometry as geo

UNDO_DEPTH = 50


def _unique(existing: set[str], base: str) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


class Draft:
    def __init__(self, templates: list[dict], live_id: str | None) -> None:
        self.templates = [Template.from_json(t) for t in templates]
        self.live_id = live_id
        self.base_hash = templates_hash(templates)
        self.current_id = live_id or (self.templates[0].id if self.templates else None)
        self.selection: str | None = None
        self.dirty = False
        self._undo: list[tuple[list[dict], str | None, str | None]] = []
        self._redo: list[tuple[list[dict], str | None, str | None]] = []

    # --- reading -----------------------------------------------------------

    def payload(self) -> list[dict]:
        return [t.to_json() for t in self.templates]

    def current(self) -> Template | None:
        return next((t for t in self.templates if t.id == self.current_id), None)

    def widget(self, wid: str) -> Widget | None:
        t = self.current()
        return t.widget(wid) if t else None

    def rects(self) -> list[tuple[str, geo.Rect]]:
        """In draw order, which is what hit_test expects."""
        t = self.current()
        if t is None:
            return []
        return [(w.id, geo.to_rect(w.x, w.y, w.width, w.height)) for w in t.widgets]

    # --- undo --------------------------------------------------------------

    def _state(self) -> tuple[list[dict], str | None, str | None]:
        return (copy.deepcopy(self.payload()), self.live_id, self.current_id)

    def _restore(self, state) -> None:
        payload, live, current = state
        self.templates = [Template.from_json(t) for t in payload]
        self.live_id, self.current_id = live, current

    def checkpoint(self) -> None:
        self._undo.append(self._state())
        del self._undo[:-UNDO_DEPTH]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._state())
        self._restore(self._undo.pop())
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._state())
        self._restore(self._redo.pop())
        self.dirty = True
        return True

    def _touch(self, checkpoint: bool = True) -> None:
        if checkpoint:
            self.checkpoint()
        self.dirty = True

    # --- widget edits ------------------------------------------------------

    def set_geometry(self, wid: str, x: float, y: float, w: float, h: float,
                     *, checkpoint: bool = True) -> None:
        """checkpoint=False for the intermediate frames of a drag; the caller
        checkpoints once on press so one drag is one undo step."""
        target = self.widget(wid)
        if target is None:
            raise KeyError(f"no widget {wid!r} in template {self.current_id!r}")
        if checkpoint:
            self.checkpoint()
        target.x, target.y, target.width, target.height = x, y, w, h
        self.dirty = True

    def delete_widget(self, wid: str) -> None:
        t = self.current()
        if t is None:
            return
        self._touch()
        t.widgets = [w for w in t.widgets if w.id != wid]
        if self.selection == wid:
            self.selection = None

    def duplicate_widget(self, wid: str) -> str:
        t = self.current()
        source = t.widget(wid) if t else None
        if t is None or source is None:
            raise KeyError(f"no widget {wid!r}")
        self._touch()
        clone = Widget.from_json(copy.deepcopy(source.to_json()))
        clone.id = _unique({w.id for w in t.widgets}, f"{wid}-copy")
        clone.x += 20.0
        clone.y += 20.0
        t.widgets.append(clone)          # on top: array order IS draw order
        return clone.id

    def reorder_widget(self, wid: str, delta: int) -> None:
        """Draw order is load-bearing: only the last widget covering a rect is
        visible. Moving one is a real edit, not a cosmetic list sort."""
        t = self.current()
        if t is None:
            return
        ids = [w.id for w in t.widgets]
        if wid not in ids:
            raise KeyError(f"no widget {wid!r}")
        i = ids.index(wid)
        j = max(0, min(len(ids) - 1, i + delta))
        if i == j:
            return
        self._touch()
        t.widgets.insert(j, t.widgets.pop(i))

    # --- template edits ----------------------------------------------------

    def add_template(self, name: str) -> str:
        self._touch()
        tid = _unique({t.id for t in self.templates},
                      name.lower().replace(" ", "-") or "template")
        self.templates.append(Template(
            id=tid, name=name, base_width=int(geo.BASE_W),
            base_height=int(geo.BASE_H), rotated=True,
            background={"type": "color", "rgb": [10, 13, 20, 255]}, widgets=[]))
        return tid

    def duplicate_template(self, tid: str) -> str:
        source = next((t for t in self.templates if t.id == tid), None)
        if source is None:
            raise KeyError(f"no template {tid!r}")
        self._touch()
        clone = Template.from_json(copy.deepcopy(source.to_json()))
        clone.id = _unique({t.id for t in self.templates}, f"{tid}-copy")
        clone.name = f"{source.name} copy"
        self.templates.append(clone)
        return clone.id

    def rename_template(self, tid: str, name: str) -> None:
        target = next((t for t in self.templates if t.id == tid), None)
        if target is None:
            raise KeyError(f"no template {tid!r}")
        self._touch()
        target.name = name               # the id never changes; template_id points at it

    def delete_template(self, tid: str) -> None:
        if len(self.templates) <= 1:
            raise ValueError("cannot delete the last template; the panel would "
                             "have nothing to render")
        self._touch()
        self.templates = [t for t in self.templates if t.id != tid]
        if self.live_id == tid:
            self.live_id = self.templates[0].id
        if self.current_id == tid:
            self.current_id = self.templates[0].id

    def set_live(self, tid: str) -> None:
        if not any(t.id == tid for t in self.templates):
            raise KeyError(f"no template {tid!r}")
        self._touch()
        self.live_id = tid

    # --- after a successful apply -----------------------------------------

    def mark_applied(self, templates: list[dict]) -> None:
        """Rebase on what the daemon now holds, so the next apply's conflict
        check compares against the right thing."""
        self.base_hash = templates_hash(templates)
        self.dirty = False


# --- cover-bar analysis ----------------------------------------------------
#
# There is NO conditional visibility. Two mechanisms fake it, and both depend
# on array order:
#   * a horizontal_bar with value_max == 1 is an opaque on/off COVER
#   * a widget with an alpha-0 SensorRange gates ITSELF
# The working stack is [needs covering] -> [cover] -> [self-gating]. Reorder a
# cover behind what it covers and the hidden thing reappears -- which renders
# perfectly and simply looks wrong, so nothing reports it but this.


def _is_cover(w: Widget) -> bool:
    return w.kind_type == "horizontal_bar" and w.kind.get("value_max") == 1


def _self_gates(w: Widget) -> bool:
    ranges = w.kind.get("ranges")
    if not isinstance(ranges, list):
        return False
    return any(isinstance(r, dict) and r.get("alpha") == 0 for r in ranges)


def _overlaps(a: Widget, b: Widget) -> bool:
    ra = geo.to_rect(a.x, a.y, a.width, a.height)
    rb = geo.to_rect(b.x, b.y, b.width, b.height)
    return not (ra.right <= rb.left or rb.right <= ra.left
                or ra.bottom <= rb.top or rb.bottom <= ra.top)


def cover_warnings(t: Template) -> list[str]:
    out: list[str] = []
    for i, cover in enumerate(t.widgets):
        if not _is_cover(cover):
            continue
        before = [w for w in t.widgets[:i] if _overlaps(cover, w)]
        after = [w for w in t.widgets[i + 1:]
                 if _overlaps(cover, w) and not _self_gates(w)]
        for w in after:
            out.append(
                f"{w.id!r} overlaps the cover {cover.id!r} but is drawn AFTER "
                f"it, so the cover cannot hide it. Move the cover later in the "
                f"widget list.")
        if len(before) > 1:
            names = ", ".join(repr(w.id) for w in before)
            out.append(
                f"the cover {cover.id!r} sits over {len(before)} widgets "
                f"({names}); only one widget per rect can be conditionally "
                f"hidden — the rest are hidden unconditionally.")
    return out
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_draft.py -v`
Expected: 16 passed

- [ ] **Step 5: Assert the module stays Qt-free**

Add to `tests/test_gui_draft.py`:

```python
def test_draft_does_not_import_qt():
    """The whole point of this module is being testable without a display.
    An accidental PySide6 import here moves it out of Codex's reach."""
    import lianli_panel.gui.draft as mod
    src = open(mod.__file__).read()
    assert "PySide6" not in src
```

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_draft.py -v`
Expected: 17 passed

- [ ] **Step 6: Commit**

```bash
git add lianli_panel/gui/draft.py tests/test_gui_draft.py
git commit -m "feat: add editable draft holding the whole template set

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

---

### Task 3: Inspector field descriptors from the extracted schema

**Files:**

- Create: `lianli_panel/gui/forms.py`
- Test: `tests/test_gui_forms.py`

**Interfaces:**

- Consumes: `schema.WIDGET_KINDS`, `schema.SOURCE_TYPES`, `schema.VariantSpec`, `model.Widget`, `model.widget_span`, `model.range_thresholds_raw`, `model.set_range_threshold_raw`.
- Produces: `forms.FieldSpec(name, kind, required, value, note)` where `kind` is one of `"number" | "text" | "bool" | "color" | "font" | "json"`; `forms.kind_fields(w) -> list[FieldSpec]`; `forms.source_fields(w) -> list[FieldSpec]`; `forms.is_unknown_kind(w) -> bool`; `forms.is_unknown_source(w) -> bool`; `forms.Change(dropped: dict, added: list[str])`; `forms.change_kind(w, new_type) -> Change`; `forms.change_source(w, new_type) -> Change`; `forms.RangeRow(index, threshold, color, alpha, unit)`; `forms.range_rows(w) -> list[RangeRow]`; `forms.set_threshold(w, index, raw) -> bool`; `forms.add_range(w, raw) -> int`; `forms.remove_range(w, index)`.

- [ ] **Step 1: Write the failing forms tests**

`tests/test_gui_forms.py`:

```python
"""What the inspector shows for a widget.

gaming-dash uses 7 of 12 kinds and 5 of 14 source types, so the existing
template cannot be the source of truth for these forms -- schema.py, extracted
from the daemon by serde probing, is.
"""
import pytest

from lianli_panel.gui import forms
from lianli_panel.model import Widget
from lianli_panel.schema import WIDGET_KINDS


def _widget(kind):
    return Widget(id="w", x=0.0, y=0.0, width=10.0, height=10.0, kind=kind)


LABEL = {"type": "label", "text": "hi", "font_size": 20.0,
         "color": [255, 255, 255, 255]}
GAUGE = {"type": "radial_gauge", "source": {"type": "cpu_usage"},
         "value_min": 20.0, "value_max": 100.0, "start_angle": 135.0,
         "sweep_angle": 270.0, "background_color": [0, 0, 0, 255],
         "unit": "C",
         "ranges": [{"max": 60.0, "color": [0, 255, 0], "alpha": 255},
                    {"max": None, "color": [255, 0, 0], "alpha": 255}]}


def test_every_schema_kind_yields_its_required_fields():
    for name, spec in WIDGET_KINDS.items():
        w = _widget({"type": name})
        shown = {f.name for f in forms.kind_fields(w)}
        missing = [r for r in spec.required
                   if r not in ("source", "ranges") and r not in shown]
        assert missing == [], f"{name} is missing {missing}"


def test_required_fields_are_marked_required():
    fields = {f.name: f for f in forms.kind_fields(_widget(LABEL))}
    assert fields["text"].required is True
    assert fields["align"].required is False


def test_a_field_the_schema_never_saw_is_still_editable():
    """observed_optional is not exhaustive -- the daemon silently ignores
    unknown keys, so there is no way to enumerate them. A key present on the
    widget must appear in the form or it becomes uneditable dead weight."""
    w = _widget({**LABEL, "shadow_blur": 3.0})
    field = next(f for f in forms.kind_fields(w) if f.name == "shadow_blur")
    assert field.value == 3.0
    assert "not in the extracted schema" in field.note


def test_color_fields_are_detected_from_the_value_shape():
    fields = {f.name: f for f in forms.kind_fields(_widget(LABEL))}
    assert fields["color"].kind == "color"
    assert fields["font_size"].kind == "number"
    assert fields["text"].kind == "text"


def test_font_fields_are_detected_by_name():
    w = _widget({**LABEL, "font": {"path": "/usr/share/fonts/x.ttf"}})
    fields = {f.name: f for f in forms.kind_fields(w)}
    assert fields["font"].kind == "font"


def test_an_unknown_kind_falls_back_to_raw_json():
    """A daemon upgrade that adds a widget kind must degrade to reduced
    functionality, never to data loss."""
    w = _widget({"type": "hologram", "spin": 3})
    assert forms.is_unknown_kind(w) is True
    assert forms.is_unknown_kind(_widget(LABEL)) is False


def test_source_fields_come_from_the_source_schema():
    w = _widget({**GAUGE, "source": {"type": "hwmon", "name": "k10temp",
                                     "label": "Tctl"}})
    fields = {f.name: f for f in forms.source_fields(w)}
    assert fields["name"].required is True
    assert fields["label"].value == "Tctl"


def test_change_kind_reports_what_it_dropped():
    w = _widget(dict(GAUGE))
    change = forms.change_kind(w, "label")
    assert w.kind["type"] == "label"
    assert "start_angle" in change.dropped
    assert change.dropped["start_angle"] == 135.0


def test_change_kind_defaults_the_new_required_fields():
    w = _widget(dict(LABEL))
    change = forms.change_kind(w, "vertical_bar")
    assert set(("source", "value_min", "value_max", "background_color")) <= set(w.kind)
    assert "value_max" in change.added


def test_change_kind_carries_fields_the_new_variant_also_has():
    w = _widget(dict(LABEL))
    forms.change_kind(w, "value_text")
    assert w.kind["color"] == [255, 255, 255, 255]
    assert w.kind["font_size"] == 20.0


def test_change_source_reports_drops_and_keeps_the_rest_of_the_widget():
    w = _widget({**GAUGE, "source": {"type": "command", "cmd": "/x/fps.sh"}})
    change = forms.change_source(w, "cpu_usage")
    assert w.kind["source"] == {"type": "cpu_usage"}
    assert change.dropped == {"cmd": "/x/fps.sh"}
    assert w.kind["value_min"] == 20.0


def test_range_rows_are_in_real_units():
    """60 on a 20..100 span is 68 degrees. This conversion failing SILENTLY --
    plausible colours, no error anywhere -- is why the UI never shows a raw
    percentage."""
    rows = forms.range_rows(_widget(GAUGE))
    assert rows[0].threshold == pytest.approx(68.0)
    assert rows[0].unit == "C"
    assert rows[1].threshold is None


def test_set_threshold_writes_a_percentage_back():
    w = _widget({**GAUGE, "ranges": [dict(GAUGE["ranges"][0]),
                                     dict(GAUGE["ranges"][1])]})
    assert forms.set_threshold(w, 0, 84.0) is True
    assert w.kind["ranges"][0]["max"] == pytest.approx(80.0)


def test_set_threshold_ignores_an_unchanged_value():
    """Re-encoding a percentage the user never touched drifts the stored float
    on every save and breaks the lossless round trip."""
    w = _widget({**GAUGE, "ranges": [dict(GAUGE["ranges"][0]),
                                     dict(GAUGE["ranges"][1])]})
    assert forms.set_threshold(w, 0, 68.0) is False
    assert w.kind["ranges"][0]["max"] == 60.0


def test_add_and_remove_range():
    w = _widget({**GAUGE, "ranges": [dict(GAUGE["ranges"][0]),
                                     dict(GAUGE["ranges"][1])]})
    i = forms.add_range(w, 92.0)
    assert i == 1                       # inserted BEFORE the catch-all
    assert w.kind["ranges"][-1]["max"] is None
    forms.remove_range(w, 1)
    assert len(w.kind["ranges"]) == 2


def test_forms_does_not_import_qt():
    import lianli_panel.gui.forms as mod
    assert "PySide6" not in open(mod.__file__).read()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_forms.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.forms'`

- [ ] **Step 3: Write the forms module**

`lianli_panel/gui/forms.py`:

```python
"""Turning the extracted schema into inspector fields.

schema.py's `required` tuples are AUTHORITATIVE -- serde reports them. Its
`observed_optional` tuples are NOT exhaustive: the daemon silently ignores
unknown fields, so no probe can enumerate the optional ones. The form therefore
shows three things, in this order:

  1. every required field of the variant   (from the schema)
  2. every optional field ever observed    (from the schema)
  3. every field actually on this widget   (so a key the schema never saw stays
                                            editable rather than becoming dead
                                            weight that only round-trips)

Range thresholds are shown and typed in REAL UNITS. The stored value is a
percentage of the widget's own value_min..value_max span, and getting that
backwards renders plausible, wrong colours with no error anywhere.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from ..model import (Widget, pct_to_raw, range_thresholds_raw,
                     set_range_threshold_raw, widget_span)
from ..schema import SOURCE_TYPES, WIDGET_KINDS

# Handled by dedicated UI, never as a generic row.
SPECIAL = ("type", "source", "ranges")

# Sane starting values when switching to a variant that requires a field the
# old one did not have. Anything absent falls back to 0.0.
_DEFAULTS: dict[str, Any] = {
    "text": "", "font_size": 40.0, "color": [255, 255, 255, 255],
    "background_color": [0, 0, 0, 0], "needle_color": [255, 80, 80, 255],
    "tick_color": [140, 140, 140, 255], "value_min": 0.0, "value_max": 100.0,
    "start_angle": 135.0, "sweep_angle": 270.0, "path": "",
    "source": {"type": "constant", "value": 0.0},
    "name": "", "label": "", "iface": "", "device": "", "device_id": "",
    "cmd": "", "value": 0.0,
}

NOTE_UNSCHEMAD = ("not in the extracted schema; present on this widget and "
                  "preserved on save")


@dataclass
class FieldSpec:
    name: str
    kind: str            # number | text | bool | color | font | json
    required: bool
    value: Any
    note: str = ""


@dataclass
class Change:
    dropped: dict[str, Any] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)


@dataclass
class RangeRow:
    index: int
    threshold: float | None      # REAL units; None is the catch-all
    color: list[int]
    alpha: int | None
    unit: str


def _field_kind(name: str, value: Any) -> str:
    if name == "font" or (isinstance(value, dict) and "path" in value):
        return "font"
    if name.endswith("color") or (
            isinstance(value, list) and 3 <= len(value) <= 4
            and all(isinstance(c, (int, float)) and not isinstance(c, bool)
                    for c in value)):
        return "color"
    if isinstance(value, bool):          # before the number check: bool IS int
        return "bool"
    if isinstance(value, (int, float)):
        return "number"
    if value is None or isinstance(value, str):
        return "text"
    return "json"


def _fields(obj: dict, spec, defaults_from_required: bool) -> list[FieldSpec]:
    names: list[str] = []
    if spec is not None:
        names = [n for n in spec.required if n not in SPECIAL]
        names += [n for n in spec.observed_optional
                  if n not in SPECIAL and n not in names]
    for n in obj:
        if n not in SPECIAL and n not in names:
            names.append(n)
    known = set(spec.required) | set(spec.observed_optional) if spec else set()
    out: list[FieldSpec] = []
    for n in names:
        value = obj.get(n, copy.deepcopy(_DEFAULTS.get(n, 0.0))
                        if defaults_from_required else None)
        out.append(FieldSpec(
            name=n, kind=_field_kind(n, value),
            required=bool(spec and n in spec.required), value=value,
            note="" if n in known else NOTE_UNSCHEMAD))
    return out


def kind_fields(w: Widget) -> list[FieldSpec]:
    return _fields(w.kind, WIDGET_KINDS.get(w.kind_type), True)


def source_fields(w: Widget) -> list[FieldSpec]:
    src = w.source or {}
    return _fields(src, SOURCE_TYPES.get(src.get("type", "")), True)


def is_unknown_kind(w: Widget) -> bool:
    return w.kind_type not in WIDGET_KINDS


def is_unknown_source(w: Widget) -> bool:
    src = w.source
    return src is not None and src.get("type") not in SOURCE_TYPES


def _switch(obj: dict, new_type: str, spec) -> tuple[dict, Change]:
    if spec is None:                     # unknown target: keep everything
        return {**obj, "type": new_type}, Change()
    known = set(spec.required) | set(spec.observed_optional)
    carried = {k: v for k, v in obj.items() if k != "type" and k in known}
    dropped = {k: v for k, v in obj.items() if k != "type" and k not in known}
    out = {"type": new_type, **carried}
    added: list[str] = []
    for n in spec.required:
        if n not in out:
            out[n] = copy.deepcopy(_DEFAULTS.get(n, 0.0))
            added.append(n)
    return out, Change(dropped, added)


def change_kind(w: Widget, new_type: str) -> Change:
    """Fields the new variant does not know are dropped -- but REPORTED, so the
    UI can show what it is about to lose instead of losing it silently."""
    w.kind, change = _switch(w.kind, new_type, WIDGET_KINDS.get(new_type))
    return change


def change_source(w: Widget, new_type: str) -> Change:
    src = w.source or {}
    new_src, change = _switch(src, new_type, SOURCE_TYPES.get(new_type))
    w.kind["source"] = new_src
    return change


# --- ranges, in real units -------------------------------------------------


def _entries(w: Widget) -> list[dict]:
    r = w.kind.get("ranges")
    if not isinstance(r, list):
        r = []
        w.kind["ranges"] = r
    return r


def range_rows(w: Widget) -> list[RangeRow]:
    unit = w.kind.get("unit") or ""
    thresholds = range_thresholds_raw(w)
    rows: list[RangeRow] = []
    for i, entry in enumerate(_entries(w)):
        rows.append(RangeRow(
            index=i,
            threshold=thresholds[i] if i < len(thresholds) else None,
            color=list(entry.get("color") or [255, 255, 255]),
            alpha=entry.get("alpha"), unit=str(unit)))
    return rows


def set_threshold(w: Widget, index: int, raw: float | None) -> bool:
    """Returns False and writes NOTHING when the value did not change.

    Re-encoding an untouched threshold would drift its stored float on every
    save -- a percentage the user never typed, changing on its own.
    """
    current = range_thresholds_raw(w)
    if index < len(current):
        both_null = current[index] is None and raw is None
        if both_null or (current[index] is not None and raw is not None
                         and abs(current[index] - raw) < 1e-9):
            return False
    set_range_threshold_raw(w, index, raw)
    return True


def add_range(w: Widget, raw: float) -> int:
    """Inserted before the catch-all: a range after `max: null` is unreachable,
    because the first range whose max >= percentage wins."""
    entries = _entries(w)
    span = widget_span(w)
    if span is None:
        raise ValueError(f"widget {w.id!r} has no value_min/value_max span")
    at = next((i for i, e in enumerate(entries) if e.get("max") is None),
              len(entries))
    entries.insert(at, {"max": None, "color": [255, 255, 255], "alpha": 255})
    set_range_threshold_raw(w, at, raw)
    return at


def remove_range(w: Widget, index: int) -> None:
    entries = _entries(w)
    if not 0 <= index < len(entries):
        raise IndexError(f"widget {w.id!r} has no range at index {index}")
    del entries[index]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_forms.py -v`
Expected: 16 passed

- [ ] **Step 5: Commit**

```bash
git add lianli_panel/gui/forms.py tests/test_gui_forms.py
git commit -m "feat: derive inspector fields from the extracted daemon schema

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

> **HANDOFF POINT.** Three tasks done. Update the SDD ledger and end the
> session; a fresh controller picks up at Task 4. Do not start Task 4 here.

---

### Task 4: Canvas interaction state machine

**Files:**

- Create: `lianli_panel/gui/interaction.py`
- Test: `tests/test_gui_interaction.py`

**Interfaces:**

- Consumes: `geometry.Rect`, `geometry.hit_test`, `geometry.handle_at`, `geometry.resize`, `geometry.nudge`, `geometry.MIN_SIZE`, `geometry.HANDLE_TOL`.
- Produces: `interaction.CanvasController(min_size=8.0, handle_tol=6.0)` with attributes `rects`, `selection`, `dragging`; methods `set_widgets(rects)`, `rect(wid) -> Rect | None`, `press(x, y) -> str | None`, `move(x, y) -> tuple[str, Rect] | None`, `release() -> tuple[str, Rect] | None`, `nudge(dx, dy) -> tuple[str, Rect] | None`. All coordinates are MODEL units.

- [ ] **Step 1: Write the failing interaction tests**

`tests/test_gui_interaction.py`:

```python
"""Press / move / release, with no Qt anywhere.

This is where clicking, dragging and cycling are decided, so that canvas.py is
only paint plus three event adapters -- and so this logic is testable without a
display.
"""
from lianli_panel.gui.geometry import Rect
from lianli_panel.gui.interaction import CanvasController

A = ("a", Rect(0, 0, 100, 100))
B = ("b", Rect(0, 0, 100, 100))
FAR = ("far", Rect(500, 200, 100, 100))


def _ctl(*rects):
    c = CanvasController()
    c.set_widgets(list(rects))
    return c


def test_press_selects_the_topmost():
    c = _ctl(A, B)
    assert c.press(50, 50) == "b"


def test_repeat_press_cycles_down_the_stack():
    c = _ctl(A, B)
    assert c.press(50, 50) == "b"
    c.release()
    assert c.press(50, 50) == "a"
    c.release()
    assert c.press(50, 50) == "b"


def test_pressing_somewhere_else_restarts_at_the_top():
    c = _ctl(A, B, FAR)
    c.press(50, 50)
    c.release()
    c.press(550, 250)
    c.release()
    assert c.press(50, 50) == "b"


def test_press_on_empty_space_clears_the_selection():
    c = _ctl(A)
    c.press(50, 50)
    c.release()
    assert c.press(900, 400) is None
    assert c.selection is None


def test_drag_moves_the_selected_widget():
    c = _ctl(A)
    c.press(50, 50)
    wid, rect = c.move(70, 90)
    assert wid == "a"
    assert (rect.left, rect.top) == (20, 40)


def test_drag_on_a_handle_resizes_instead_of_moving():
    c = _ctl(A)
    c.press(50, 50)                       # select it first
    c.release()
    c.press(100, 100)                     # the se corner
    wid, rect = c.move(150, 150)
    assert (rect.left, rect.top) == (0, 0)
    assert (rect.width, rect.height) == (150, 150)


def test_resize_respects_the_minimum_size():
    c = _ctl(A)
    c.press(50, 50)
    c.release()
    c.press(100, 100)
    _, rect = c.move(-500, -500)
    assert (rect.width, rect.height) == (8.0, 8.0)


def test_move_without_a_press_does_nothing():
    c = _ctl(A)
    assert c.move(10, 10) is None


def test_release_returns_the_final_rect_once():
    c = _ctl(A)
    c.press(50, 50)
    c.move(60, 60)
    wid, rect = c.release()
    assert wid == "a" and rect.left == 10
    assert c.release() is None
    assert c.dragging is False


def test_release_without_a_drag_returns_none():
    c = _ctl(A)
    assert c.release() is None


def test_nudge_moves_the_selection():
    c = _ctl(A)
    c.press(50, 50)
    c.release()
    wid, rect = c.nudge(10, 0)
    assert wid == "a" and rect.left == 10


def test_nudge_without_a_selection_returns_none():
    assert _ctl(A).nudge(1, 0) is None


def test_interaction_does_not_import_qt():
    import lianli_panel.gui.interaction as mod
    assert "PySide6" not in open(mod.__file__).read()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_interaction.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.interaction'`

- [ ] **Step 3: Write the interaction module**

`lianli_panel/gui/interaction.py`:

```python
"""Canvas interaction, with no Qt.

Everything here is in MODEL units (the 1920x480 authoring space). The view
converts screen pixels before calling, which is also why the grab tolerance is
passed in rather than assumed: 6 screen pixels is 12 model units at scale 0.5.

Repeated presses in the same spot cycle DOWNWARD through overlapping widgets.
That is not a nicety -- cover bars sit exactly on top of what they hide, so
without cycling the hidden widget can never be selected at all.
"""
from __future__ import annotations

from . import geometry as geo

CYCLE_TOL = 3.0          # model units; a press this close counts as "same spot"


class CanvasController:
    def __init__(self, min_size: float = geo.MIN_SIZE,
                 handle_tol: float = geo.HANDLE_TOL) -> None:
        self.min_size = min_size
        self.handle_tol = handle_tol
        self.rects: list[tuple[str, geo.Rect]] = []
        self.selection: str | None = None
        self.dragging = False
        self._handle: str | None = None
        self._origin: tuple[float, float] | None = None
        self._start: geo.Rect | None = None
        self._live: geo.Rect | None = None
        self._last_press: tuple[float, float] | None = None

    def set_widgets(self, rects: list[tuple[str, geo.Rect]]) -> None:
        self.rects = list(rects)
        if self.selection is not None and \
                not any(wid == self.selection for wid, _ in self.rects):
            self.selection = None

    def rect(self, wid: str | None) -> geo.Rect | None:
        return next((r for w, r in self.rects if w == wid), None)

    def _same_spot(self, x: float, y: float) -> bool:
        if self._last_press is None:
            return False
        px, py = self._last_press
        return abs(x - px) <= CYCLE_TOL and abs(y - py) <= CYCLE_TOL

    def press(self, x: float, y: float) -> str | None:
        handle = None
        selected = self.rect(self.selection)
        if selected is not None:
            handle = geo.handle_at(selected, x, y, self.handle_tol)
        if handle is None:
            after = self.selection if self._same_spot(x, y) else None
            self.selection = geo.hit_test(self.rects, x, y, after=after)
        self._handle = handle
        self._origin = (x, y)
        self._last_press = (x, y)
        self._start = self.rect(self.selection)
        self._live = self._start
        self.dragging = self._start is not None
        return self.selection

    def move(self, x: float, y: float) -> tuple[str, geo.Rect] | None:
        if not self.dragging or self._start is None or self._origin is None:
            return None
        dx, dy = x - self._origin[0], y - self._origin[1]
        if self._handle:
            self._live = geo.resize(self._start, self._handle, dx, dy,
                                    self.min_size)
        else:
            self._live = geo.nudge(self._start, dx, dy)
        return (self.selection, self._live)   # type: ignore[return-value]

    def release(self) -> tuple[str, geo.Rect] | None:
        if not self.dragging:
            return None
        self.dragging = False
        self._handle = None
        out = (self.selection, self._live) if self._live is not None else None
        return out                            # type: ignore[return-value]

    def nudge(self, dx: float, dy: float) -> tuple[str, geo.Rect] | None:
        current = self.rect(self.selection)
        if self.selection is None or current is None:
            return None
        return (self.selection, geo.nudge(current, dx, dy))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_interaction.py -v`
Expected: 13 passed

- [ ] **Step 5: Commit**

```bash
git add lianli_panel/gui/interaction.py tests/test_gui_interaction.py
git commit -m "feat: add canvas interaction state machine with click cycling

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

---

### Task 5: Threaded preview worker and the first window

At the end of this task the app launches, lists the daemon's templates, and shows the panel's own render. Nothing is editable yet.

**Files:**

- Create: `lianli_panel/gui/preview.py`, `lianli_panel/gui/window.py`, `lianli_panel/gui/app.py`
- Test: `tests/test_gui_preview.py`, `tests/test_gui_smoke.py`

**Interfaces:**

- Consumes: `render.PreviewRenderer`, `render.Coalescer`, `ipc.Client`, `ipc.DaemonDown`, `apply.read_templates`, `draft.Draft`.
- Produces: `preview.PreviewWorker(client, parent=None, interval_s=0.25, poll_ms=50)` with signals `rendered(bytes)`, `failed(str)` and methods `request(payload: dict)`, `refresh_live(payload: dict) -> bool`, `stop()`; `window.MainWindow(client)` with attributes `draft`, `worker`, `banner`, methods `load()`, `set_frame(jpeg: bytes)`; `app.main(argv=None) -> int`.

- [ ] **Step 1: Write the failing preview tests**

`tests/test_gui_preview.py`:

```python
"""The render worker.

Two things are being protected here. First, a 0.3s blocking IPC call must not
run on the UI thread. Second -- and this is the one that costs real damage --
an automatic render must never execute a command source: RenderTemplatePreview
runs them twice per widget per render as uid lianli, and graph.sh writes the
state file the LIVE panel's sparkline reads.
"""
import base64
import time

import pytest

from tests.conftest import FakeClient
from lianli_panel.gui.preview import PreviewWorker

JPEG = base64.b64encode(b"\xff\xd8\xff\xd9").decode()


def _tpl(tid="t", cmd="/usr/local/share/lianli-panel/fps.sh"):
    return {"id": tid, "name": tid, "base_width": 1920, "base_height": 480,
            "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
            "widgets": [{"id": "v", "x": 10.0, "y": 10.0, "width": 10.0,
                         "height": 10.0,
                         "kind": {"type": "value_text", "font_size": 10.0,
                                  "color": [255, 255, 255, 255],
                                  "source": {"type": "command", "cmd": cmd}}}]}


def _wait(qapp, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        qapp.processEvents()
        time.sleep(0.005)
    return pred()


@pytest.fixture
def worker(qapp):
    client = FakeClient({"RenderTemplatePreview": {"jpeg_base64": JPEG}})
    w = PreviewWorker(client, interval_s=0.05, poll_ms=10)
    yield w, client
    w.stop()


def test_worker_renders_and_emits_the_jpeg(qapp, worker):
    w, _ = worker
    got = []
    w.rendered.connect(got.append)
    w.request(_tpl())
    assert _wait(qapp, lambda: got), "no render arrived"
    assert got[0].startswith(b"\xff\xd8")


def test_an_automatic_render_never_sends_a_command_source(qapp, worker):
    w, client = worker
    got = []
    w.rendered.connect(got.append)
    w.request(_tpl())
    assert _wait(qapp, lambda: got)
    sent = client.calls[0][1]["template"]
    source = sent["widgets"][0]["kind"]["source"]
    assert source["type"] == "constant"


def test_refresh_live_sends_the_command_verbatim(qapp, worker):
    w, client = worker
    got = []
    w.rendered.connect(got.append)
    assert w.refresh_live(_tpl()) is True
    assert _wait(qapp, lambda: got)
    source = client.calls[0][1]["template"]["widgets"][0]["kind"]["source"]
    assert source["type"] == "command"


def test_a_failed_render_emits_failed(qapp):
    client = FakeClient({"RenderTemplatePreview": RuntimeError("daemon down")})
    w = PreviewWorker(client, interval_s=0.05, poll_ms=10)
    errors = []
    w.failed.connect(errors.append)
    w.request(_tpl())
    assert _wait(qapp, lambda: errors)
    assert "daemon down" in errors[0]
    w.stop()


def test_the_last_request_of_a_burst_is_rendered(qapp, worker):
    """A request arriving after a render finished but inside the debounce
    window has no in-flight render to release it. Without the due() poll the
    final state of a drag would never render -- the exact bug Coalescer's
    docstring warns about."""
    w, client = worker
    got = []
    w.rendered.connect(got.append)
    for tid in ("first", "second", "third"):
        w.request(_tpl(tid))
    assert _wait(qapp, lambda: len(got) >= 2, timeout=5.0)
    ids = [c[1]["template"]["id"] for c in client.calls]
    assert ids[-1] == "third"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_preview.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.preview'`

- [ ] **Step 3: Write the preview worker**

`lianli_panel/gui/preview.py`:

```python
"""Rendering off the UI thread.

A preview is a ~0.30s blocking IPC round trip. On the UI thread that is a
visible freeze on every drag frame, so the call lives on a QThread and results
come back as signals.

The debounce and in-flight rules are render.Coalescer's, not this module's.
What this module adds is the POLL: a request arriving after a render finished
but inside the debounce window has nothing to release it, so due() must be
asked on a timer or the final state of a drag never renders.

request() is the automatic path and always substitutes command sources for
constants. refresh_live() is the only path that executes them, and it exists
because the user asked for it explicitly.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from ..render import Coalescer, PreviewRenderer


class _Job(QObject):
    done = Signal(bytes)
    failed = Signal(str)

    def __init__(self, renderer: PreviewRenderer) -> None:
        super().__init__()
        self.renderer = renderer

    @Slot(dict, bool)
    def run(self, payload: dict, live: bool) -> None:
        try:
            self.done.emit(self.renderer.render(payload, live=live))
        except Exception as exc:               # any daemon or decode failure
            self.failed.emit(str(exc))


class PreviewWorker(QObject):
    rendered = Signal(bytes)
    failed = Signal(str)
    _submit = Signal(dict, bool)

    def __init__(self, client, parent=None, interval_s: float = 0.25,
                 poll_ms: int = 50) -> None:
        super().__init__(parent)
        self.renderer = PreviewRenderer(client)
        self.coalescer = Coalescer(interval_s)
        self._payload: dict | None = None

        self._thread = QThread()
        self._job = _Job(self.renderer)
        self._job.moveToThread(self._thread)
        self._submit.connect(self._job.run)
        self._job.done.connect(self._on_done)
        self._job.failed.connect(self._on_failed)
        self._thread.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(poll_ms)

    def request(self, payload: dict) -> None:
        """Debounced. Command sources are substituted for constants."""
        self._payload = payload
        if self.coalescer.request(time.monotonic()):
            self._submit.emit(payload, False)

    def refresh_live(self, payload: dict) -> bool:
        """EXECUTES command sources as uid lianli. Explicit user action only.

        Returns False when a render is already in flight, so the caller can say
        'busy' instead of stacking a second 0.3s call behind the first.
        """
        if self.coalescer.in_flight:
            return False
        self._payload = payload
        self.coalescer.request(time.monotonic())
        self._submit.emit(payload, True)
        return True

    def _poll(self) -> None:
        if self.coalescer.due(time.monotonic()) and self._payload is not None:
            self._submit.emit(self._payload, False)

    def _on_done(self, jpeg: bytes) -> None:
        if self.coalescer.finish(time.monotonic()) and self._payload is not None:
            self._submit.emit(self._payload, False)
        self.rendered.emit(jpeg)

    def _on_failed(self, message: str) -> None:
        self.coalescer.finish(time.monotonic())
        self.failed.emit(message)

    def stop(self) -> None:
        self._timer.stop()
        self._thread.quit()
        self._thread.wait(2000)
```

- [ ] **Step 4: Run the preview tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_preview.py -v`
Expected: 5 passed

If a test hangs, the cause is almost certainly a queued connection with no event loop — `_wait` calls `processEvents()`, so check `moveToThread` ordering before changing the test.

- [ ] **Step 5: Write the failing window smoke tests**

`tests/test_gui_smoke.py`:

```python
"""The window is exercised by constructing and driving it, not by asserting on
widget trees. These are smoke tests: they catch import errors, bad signal
signatures and null derefs. They are NOT evidence the app works -- that is the
controller launching it and looking at the panel.
"""
import base64
import time

import pytest

from tests.conftest import FakeClient

JPEG = base64.b64encode(b"\xff\xd8\xff\xd9").decode()

TEMPLATES = [
    {"id": "gaming-dash", "name": "Gaming Dash", "base_width": 1920,
     "base_height": 480, "rotated": True,
     "background": {"type": "color", "rgb": [10, 13, 20, 255]},
     "widgets": [{"id": "cpu", "x": 100.0, "y": 100.0, "width": 80.0,
                  "height": 40.0,
                  "kind": {"type": "value_text", "font_size": 30.0,
                           "color": [255, 255, 255, 255],
                           "source": {"type": "cpu_usage"},
                           "value_min": 0.0, "value_max": 100.0,
                           "unit": "%",
                           "ranges": [{"max": 60.0, "color": [0, 255, 0], "alpha": 255},
                                      {"max": None, "color": [255, 0, 0], "alpha": 255}]}}]},
    {"id": "spare", "name": "Spare", "base_width": 1920, "base_height": 480,
     "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
     "widgets": []},
]

CONFIG = {"lcds": [{"serial": "hid:513b5a7acadc4203", "type": "custom",
                    "template_id": "gaming-dash", "orientation": 90}]}


def make_client(**overrides):
    responses = {
        "GetLcdTemplates": TEMPLATES,
        "GetConfig": CONFIG,
        "ListDevices": [{"device_id": "hid:513b5a7acadc4203", "has_lcd": True}],
        "RenderTemplatePreview": {"jpeg_base64": JPEG},
    }
    responses.update(overrides)
    return FakeClient(responses)


def wait(qapp, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        qapp.processEvents()
        time.sleep(0.005)
    return pred()


@pytest.fixture
def win(qapp):
    from lianli_panel.gui.window import MainWindow
    w = MainWindow(make_client())
    yield w
    w.close()


def test_window_constructs(win):
    assert win.draft is not None


def test_window_lists_the_templates(win):
    assert [t.id for t in win.draft.templates] == ["gaming-dash", "spare"]
    assert win.draft.live_id == "gaming-dash"


def test_window_shows_the_first_frame(qapp, win):
    assert wait(qapp, lambda: win.frame_bytes is not None)


def test_window_survives_a_dead_daemon(qapp):
    """The daemon being down must degrade to a banner, not a traceback on
    startup -- it is down often enough (encoder death after a replug) that
    crashing here would be the app's most common behaviour."""
    from lianli_panel.ipc import DaemonDown
    from lianli_panel.gui.window import MainWindow
    w = MainWindow(make_client(GetLcdTemplates=DaemonDown("no socket")))
    assert w.draft.templates == []
    assert "daemon" in w.banner.text().lower()
    w.close()
```

- [ ] **Step 6: Run the smoke tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.window'`

- [ ] **Step 7: Write the window skeleton and the entry point**

`lianli_panel/gui/window.py`:

```python
"""The main window.

Layout: template library on the left, the daemon's own render in the middle,
inspector on the right. Later tasks fill the left and right panes in; this task
establishes the wiring -- load from the daemon, render, show.

The daemon is treated as unreliable ON PURPOSE. After a replug its encoder can
be dead while every IPC call still returns ok, so a failure to load is a banner
and an empty draft, never a traceback at startup.
"""
from __future__ import annotations

from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QVBoxLayout, QWidget)

from ..ipc import DaemonError
from .draft import Draft
from .preview import PreviewWorker

TITLE = "lianli-panel"


class MainWindow(QMainWindow):
    def __init__(self, client) -> None:
        super().__init__()
        self.client = client
        self.setWindowTitle(TITLE)
        self.resize(1500, 700)
        self.frame_bytes: bytes | None = None

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            "background:#5a1d1d; color:#ffd9d9; padding:6px;")
        self.banner.hide()

        self.template_list = QListWidget()
        self.template_list.setMaximumWidth(240)
        self.template_list.currentTextChanged.connect(self._choose_template)

        self.canvas = QLabel("no frame yet")
        self.canvas.setMinimumSize(960, 240)
        self.canvas.setStyleSheet("background:#000;")

        body = QHBoxLayout()
        body.addWidget(self.template_list)
        body.addWidget(self.canvas, 1)

        root = QVBoxLayout()
        root.addWidget(self.banner)
        root.addLayout(body, 1)
        holder = QWidget()
        holder.setLayout(root)
        self.setCentralWidget(holder)

        self.worker = PreviewWorker(client, parent=self)
        self.worker.rendered.connect(self.set_frame)
        self.worker.failed.connect(self._render_failed)

        self.draft = Draft([], None)
        self.load()

    # --- daemon ------------------------------------------------------------

    def load(self) -> None:
        try:
            templates = self.client.call("GetLcdTemplates") or []
            config = self.client.call("GetConfig") or {}
        except DaemonError as exc:
            self.draft = Draft([], None)
            self._warn(f"the daemon did not answer: {exc}. Nothing is loaded "
                       f"and nothing can be applied.")
            return
        live = next((e.get("template_id") for e in config.get("lcds") or []), None)
        self.draft = Draft(templates, live)
        self.template_list.clear()
        self.template_list.addItems([t.id for t in self.draft.templates])
        if self.draft.current_id:
            self.template_list.setCurrentRow(
                [t.id for t in self.draft.templates].index(self.draft.current_id))
        self.rerender()

    def rerender(self) -> None:
        current = self.draft.current()
        if current is not None:
            self.worker.request(current.to_json())

    # --- slots -------------------------------------------------------------

    def _choose_template(self, template_id: str) -> None:
        if template_id and template_id != self.draft.current_id:
            self.draft.current_id = template_id
            self.draft.selection = None
            self.rerender()

    def set_frame(self, jpeg: bytes) -> None:
        self.frame_bytes = jpeg
        image = QImage.fromData(jpeg, "JPEG")
        if not image.isNull():
            self.canvas.setPixmap(QPixmap.fromImage(image).scaled(
                self.canvas.size(), aspectMode=1, mode=1))

    def _render_failed(self, message: str) -> None:
        self._warn(f"preview render failed: {message}")

    def _warn(self, message: str) -> None:
        self.banner.setText(message)
        self.banner.show()

    def closeEvent(self, event) -> None:
        self.worker.stop()
        super().closeEvent(event)
```

`lianli_panel/gui/app.py`:

```python
"""Entry point.

Dark palette to match the KDE desktop. The window is created even when the
daemon is unreachable -- see MainWindow.load.
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..ipc import Client
from .window import MainWindow


def _dark(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(24, 26, 30))
    p.setColor(QPalette.WindowText, QColor(226, 228, 232))
    p.setColor(QPalette.Base, QColor(18, 20, 24))
    p.setColor(QPalette.AlternateBase, QColor(30, 33, 38))
    p.setColor(QPalette.Text, QColor(226, 228, 232))
    p.setColor(QPalette.Button, QColor(38, 41, 47))
    p.setColor(QPalette.ButtonText, QColor(226, 228, 232))
    p.setColor(QPalette.Highlight, QColor(64, 120, 200))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(p)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    _dark(app)
    window = MainWindow(Client())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 8: Run both test files**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_preview.py tests/test_gui_smoke.py -v`
Expected: 9 passed (5 preview, 4 smoke)

- [ ] **Step 9: CONTROLLER — launch it against the real daemon**

This is the first time the app talks to the hardware. It only reads.

```bash
./.venv/bin/python -m lianli_panel.gui.app
```

Confirm, by looking at the window:

1. The template list shows the daemon's templates.
2. The canvas shows the gaming-dash render — the real dash, not a blank frame.
3. No banner is visible.
4. Selecting the other template re-renders.

Then confirm no command sources were executed by the automatic path:

```bash
sudo journalctl -u lianli-daemon-system.service --since "2 minutes ago" | grep -ci nvidia-smi || true
```

Expected: `0`. A non-zero count means `live=False` is not reaching the renderer, and Task 5 is not done.

- [ ] **Step 10: Commit**

```bash
git add lianli_panel/gui/preview.py lianli_panel/gui/window.py lianli_panel/gui/app.py \
        tests/test_gui_preview.py tests/test_gui_smoke.py
git commit -m "feat: render previews off the UI thread and show them in a window

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

---

### Task 6: The canvas — selection, drag, resize, nudge

**Files:**

- Create: `lianli_panel/gui/canvas.py`
- Modify: `lianli_panel/gui/window.py` (replace the `QLabel` placeholder)
- Test: `tests/test_gui_smoke.py`

**Interfaces:**

- Consumes: `geometry.fit`, `geometry.to_rect`, `geometry.to_centre`, `geometry.offscreen`, `interaction.CanvasController`, `draft.Draft.rects`.
- Produces: `canvas.Canvas(parent=None)` with signals `selection_changed(str)` (empty string means nothing selected), `edit_started()`, `geometry_changed(str, float, float, float, float)` carrying **centre-origin** model values, `edit_finished()`; methods `set_frame(jpeg: bytes)`, `set_widgets(rects)`, `set_selection(wid: str | None)`.

- [ ] **Step 1: Write the failing canvas smoke tests**

Append to `tests/test_gui_smoke.py`:

```python
def test_canvas_press_selects_a_widget(qapp, win):
    """The canvas is 1920x480 in model space; press in the middle of the only
    widget, which is centred at (100, 100)."""
    win.canvas.resize(1920, 480)
    win.canvas.press_model(100.0, 100.0)
    win.canvas.release_model()
    assert win.draft.selection == "cpu"


def test_canvas_drag_updates_the_draft_in_centre_origin(qapp, win):
    win.canvas.resize(1920, 480)
    win.canvas.press_model(100.0, 100.0)
    win.canvas.move_model(150.0, 100.0)
    win.canvas.release_model()
    w = win.draft.widget("cpu")
    assert w.x == 150.0                 # centre moved by the drag delta
    assert w.width == 80.0              # size untouched by a move
    assert win.draft.dirty is True
```

`press_model` / `move_model` / `release_model` take MODEL coordinates and are the same three calls the Qt event handlers make. They exist so the interaction can be driven without synthesising `QMouseEvent`s, whose constructor signature has changed between Qt versions.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: FAIL — `AttributeError: 'QLabel' object has no attribute 'press_model'`

- [ ] **Step 3: Write the canvas widget**

`lianli_panel/gui/canvas.py`:

```python
"""The canvas.

WHAT YOU SEE IS THE DAEMON'S OWN RENDER. This widget draws the JPEG from
RenderTemplatePreview and overlays selection only -- it does not reimplement a
single one of the 12 widget kinds. That is the architectural bet of the whole
app: no second renderer, so nothing to drift from what the panel shows.

During a drag the selection rectangle moves live over a frame that may be up to
~0.3s stale. That is the accepted cost.

Every widget's outline is drawn faintly, always. Cover bars and self-gating
widgets are invisible in the render by design, and an outline is the only way
to see that something is there to click.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import QWidget

from . import geometry as geo
from .interaction import CanvasController

HANDLE_PX = 7.0
NUDGE = 1.0
NUDGE_FAST = 10.0


class Canvas(QWidget):
    selection_changed = Signal(str)
    edit_started = Signal()
    geometry_changed = Signal(str, float, float, float, float)
    edit_finished = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(640, 160)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setMouseTracking(False)
        self.controller = CanvasController()
        self._image: QImage | None = None
        self._view = geo.fit(float(self.width() or 1), float(self.height() or 1))

    # --- inputs ------------------------------------------------------------

    def set_frame(self, jpeg: bytes) -> None:
        image = QImage.fromData(jpeg, "JPEG")
        if not image.isNull():
            self._image = image
            self.update()

    def set_widgets(self, rects: list[tuple[str, geo.Rect]]) -> None:
        self.controller.set_widgets(rects)
        self.update()

    def set_selection(self, wid: str | None) -> None:
        self.controller.selection = wid
        self.update()

    # --- painting ----------------------------------------------------------

    def _refresh_view(self) -> None:
        self._view = geo.fit(float(self.width()), float(self.height()))

    def paintEvent(self, event) -> None:
        self._refresh_view()
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(0, 0, 0))
        frame = self._view.to_view(geo.Rect(0, 0, geo.BASE_W, geo.BASE_H))
        if self._image is not None:
            p.drawImage(int(frame.left), int(frame.top),
                        self._image.scaled(int(frame.width), int(frame.height),
                                           Qt.IgnoreAspectRatio,
                                           Qt.SmoothTransformation))
        outline = QPen(QColor(120, 160, 220, 70))
        for wid, r in self.controller.rects:
            v = self._view.to_view(r)
            p.setPen(QPen(QColor(230, 140, 60, 140)) if geo.offscreen(r) else outline)
            p.drawRect(int(v.left), int(v.top), int(v.width), int(v.height))
        selected = self.controller.rect(self.controller.selection)
        if selected is not None:
            v = self._view.to_view(selected)
            p.setPen(QPen(QColor(90, 170, 255), 2))
            p.drawRect(int(v.left), int(v.top), int(v.width), int(v.height))
            p.setBrush(QColor(90, 170, 255))
            for hx, hy in self._handle_points(v):
                p.drawRect(int(hx - HANDLE_PX / 2), int(hy - HANDLE_PX / 2),
                           int(HANDLE_PX), int(HANDLE_PX))
        p.end()

    @staticmethod
    def _handle_points(v: geo.Rect) -> list[tuple[float, float]]:
        mx, my = v.left + v.width / 2, v.top + v.height / 2
        return [(v.left, v.top), (mx, v.top), (v.right, v.top),
                (v.right, my), (v.right, v.bottom), (mx, v.bottom),
                (v.left, v.bottom), (v.left, my)]

    # --- interaction, in model units --------------------------------------

    def press_model(self, x: float, y: float) -> None:
        before = self.controller.selection
        selection = self.controller.press(x, y)
        if selection != before:
            self.selection_changed.emit(selection or "")
        if self.controller.dragging:
            self.edit_started.emit()
        self.update()

    def move_model(self, x: float, y: float) -> None:
        moved = self.controller.move(x, y)
        if moved is not None:
            self._emit(*moved)

    def release_model(self) -> None:
        final = self.controller.release()
        if final is not None:
            self._emit(*final)
            self.edit_finished.emit()
        self.update()

    def _emit(self, wid: str, rect: geo.Rect) -> None:
        x, y, w, h = geo.to_centre(rect)      # the model stores CENTRES
        self.geometry_changed.emit(wid, x, y, w, h)
        self.update()

    # --- Qt event adapters, three lines each ------------------------------

    def _model_point(self, event) -> tuple[float, float]:
        self._refresh_view()
        pos = event.position()
        return self._view.to_model_point(pos.x(), pos.y())

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.press_model(*self._model_point(event))

    def mouseMoveEvent(self, event) -> None:
        self.move_model(*self._model_point(event))

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.release_model()

    def keyPressEvent(self, event) -> None:
        step = NUDGE_FAST if event.modifiers() & Qt.ShiftModifier else NUDGE
        deltas = {Qt.Key_Left: (-step, 0.0), Qt.Key_Right: (step, 0.0),
                  Qt.Key_Up: (0.0, -step), Qt.Key_Down: (0.0, step)}
        delta = deltas.get(event.key())
        if delta is None:
            super().keyPressEvent(event)
            return
        moved = self.controller.nudge(*delta)
        if moved is not None:
            self.edit_started.emit()
            self._emit(*moved)
            self.edit_finished.emit()
```

- [ ] **Step 4: Wire the canvas into the window**

In `lianli_panel/gui/window.py`, replace the `QLabel` canvas with the real one and connect it. Change the import line to add `from .canvas import Canvas`, then:

```python
        self.canvas = Canvas()
        self.canvas.selection_changed.connect(self._select)
        self.canvas.edit_started.connect(self._begin_edit)
        self.canvas.geometry_changed.connect(self._move_widget)
        self.canvas.edit_finished.connect(self.rerender)
```

and replace `set_frame` plus add the three slots:

```python
    def set_frame(self, jpeg: bytes) -> None:
        self.frame_bytes = jpeg
        self.canvas.set_frame(jpeg)

    def _select(self, widget_id: str) -> None:
        self.draft.selection = widget_id or None

    def _begin_edit(self) -> None:
        """One checkpoint per drag, taken on press. Without this a single drag
        leaves ~40 undo entries and ctrl-Z stops being usable."""
        self.draft.checkpoint()

    def _move_widget(self, wid: str, x: float, y: float,
                     w: float, h: float) -> None:
        self.draft.set_geometry(wid, x, y, w, h, checkpoint=False)
        self.canvas.set_widgets(self.draft.rects())
        self.rerender()
```

and in `load()` and `_choose_template()`, after the draft changes, push the rects:

```python
        self.canvas.set_widgets(self.draft.rects())
```

- [ ] **Step 5: Run the smoke tests**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: 6 passed

- [ ] **Step 6: CONTROLLER — drag a widget in the real app**

```bash
./.venv/bin/python -m lianli_panel.gui.app
```

Confirm by hand, and note what actually happens rather than what this plan predicts:

1. Every widget has a faint outline, including ones invisible in the render (the cover bars).
2. Clicking selects the topmost; clicking the same spot again selects the one underneath.
3. Dragging moves the rectangle immediately and the underlying image catches up within about a third of a second.
4. Corner handles resize; the rectangle never inverts when dragged past itself.
5. Arrow keys nudge by 1, shift-arrow by 10.
6. **Nothing has been applied** — the panel itself is unchanged. Verify: `./.venv/bin/python -m lianli_panel.cli list` still shows the same set hash it did before the app opened.

- [ ] **Step 7: Commit**

```bash
git add lianli_panel/gui/canvas.py lianli_panel/gui/window.py tests/test_gui_smoke.py
git commit -m "feat: make the daemon's render an editable canvas

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

> **HANDOFF POINT.** Six tasks done. Update the SDD ledger and end the session.

---

### Task 7: Inspector and the widget list

**Files:**

- Create: `lianli_panel/gui/inspector.py`, `lianli_panel/gui/sidebar.py`
- Modify: `lianli_panel/gui/window.py`
- Test: `tests/test_gui_smoke.py`

**Interfaces:**

- Consumes: `forms.*`, `model.Widget`, `draft.cover_warnings`, `draft.Draft`.
- Produces: `inspector.Inspector(parent=None)` with `set_widget(w: model.Widget | None)`, signal `changed()`, signal `structure_changed()` (kind or source type switched — the whole form rebuilds); `sidebar.WidgetList(parent=None)` with `set_draft(d: draft.Draft)`, signals `selected(str)`, `reordered(str, int)`, `deleted(str)`, `duplicated(str)`.

- [ ] **Step 1: Write the failing smoke tests**

Append to `tests/test_gui_smoke.py`:

```python
def test_inspector_populates_for_the_selected_widget(qapp, win):
    win.canvas.resize(1920, 480)
    win.canvas.press_model(100.0, 100.0)
    win.canvas.release_model()
    assert "font_size" in win.inspector.editors
    assert win.inspector.kind_combo.currentText() == "value_text"


def test_editing_a_field_marks_the_draft_dirty(qapp, win):
    win.canvas.press_model(100.0, 100.0)
    win.canvas.release_model()
    win.inspector.editors["font_size"].setValue(48.0)
    assert win.draft.widget("cpu").kind["font_size"] == 48.0
    assert win.draft.dirty is True


def test_range_row_shows_real_units_not_percentages(qapp, win):
    """The stored 60.0 is a percentage of the widget's own 0..100 span, which
    happens to be 60 here. On a 20..100 gauge the same 60 would show as 68."""
    win.canvas.press_model(100.0, 100.0)
    win.canvas.release_model()
    assert win.inspector.ranges.item(0, 0).text().startswith("60")
    assert win.inspector.ranges.item(1, 0).text() == "—"


def test_widget_list_reorder_changes_draw_order(qapp, win):
    win.draft.duplicate_widget("cpu")
    win.widget_list.set_draft(win.draft)
    order_before = [w.id for w in win.draft.current().widgets]
    win.widget_list.reordered.emit(order_before[0], +1)
    assert [w.id for w in win.draft.current().widgets] == list(reversed(order_before))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'inspector'`

- [ ] **Step 3: Write the inspector**

`lianli_panel/gui/inspector.py`:

```python
"""The inspector.

Every field here comes from forms.py, which derives them from the schema
extracted from the daemon -- not from what gaming-dash happens to use, which
covers only 7 of 12 kinds.

Range thresholds are shown and typed in REAL UNITS (68, not 60). The stored
value is a percentage of the widget's own span. This is the highest-value
invariant in the app because getting it wrong fails SILENTLY: the panel
renders, nothing errors, the colours are simply wrong.

Changing kind or source type DROPS the fields the new variant does not know.
That is unavoidable -- but it is shown before it happens, never silent.
"""
from __future__ import annotations

import json

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QCheckBox, QColorDialog, QComboBox,
                               QDoubleSpinBox, QFormLayout, QLabel, QLineEdit,
                               QMessageBox, QPlainTextEdit, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)
from PySide6.QtGui import QColor

from ..model import Widget
from ..schema import KIND_NAMES, SOURCE_NAMES
from . import forms


class ColorButton(QPushButton):
    changed = Signal(list)

    def __init__(self, rgba: list[int]) -> None:
        super().__init__()
        self._rgba = [int(c) for c in (list(rgba) + [255, 255, 255, 255])[:4]]
        self.clicked.connect(self._pick)
        self._paint()

    def _paint(self) -> None:
        r, g, b, a = self._rgba
        self.setText(f"{r},{g},{b},{a}")
        self.setStyleSheet(f"background: rgb({r},{g},{b});")

    def _pick(self) -> None:
        r, g, b, a = self._rgba
        chosen = QColorDialog.getColor(
            QColor(r, g, b, a), self, "Colour",
            QColorDialog.ShowAlphaChannel)
        if chosen.isValid():
            self._rgba = [chosen.red(), chosen.green(), chosen.blue(),
                          chosen.alpha()]
            self._paint()
            self.changed.emit(list(self._rgba))


class Inspector(QWidget):
    changed = Signal()
    structure_changed = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.widget: Widget | None = None
        self.editors: dict[str, QWidget] = {}

        self.kind_combo = QComboBox()
        self.kind_combo.addItems(list(KIND_NAMES))
        self.kind_combo.activated.connect(self._change_kind)

        self.source_combo = QComboBox()
        self.source_combo.addItems(list(SOURCE_NAMES))
        self.source_combo.activated.connect(self._change_source)

        self.form = QFormLayout()
        self.raw = QPlainTextEdit()
        self.raw.setPlaceholderText("raw kind JSON")
        self.raw.hide()
        self.raw.focusOutEvent = self._raw_committed   # type: ignore[assignment]

        self.ranges = QTableWidget(0, 3)
        self.ranges.setHorizontalHeaderLabels(["threshold", "colour", "alpha"])
        self.ranges.itemChanged.connect(self._range_edited)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("kind"))
        root.addWidget(self.kind_combo)
        root.addWidget(QLabel("source"))
        root.addWidget(self.source_combo)
        root.addLayout(self.form)
        root.addWidget(self.raw)
        root.addWidget(QLabel("ranges (real units)"))
        root.addWidget(self.ranges)
        root.addStretch(1)

    # --- population --------------------------------------------------------

    def set_widget(self, w: Widget | None) -> None:
        self.widget = w
        self._clear()
        if w is None:
            return
        unknown = forms.is_unknown_kind(w)
        self.kind_combo.setEnabled(not unknown)
        if not unknown:
            self.kind_combo.setCurrentText(w.kind_type)
        self.raw.setVisible(unknown)
        if unknown:
            self.raw.setPlainText(json.dumps(w.kind, indent=1))
            return
        src = w.source or {}
        self.source_combo.setVisible(bool(src))
        if src:
            self.source_combo.setCurrentText(str(src.get("type", "")))
        for spec in forms.kind_fields(w):
            self._add_row(spec, target="kind")
        for spec in forms.source_fields(w):
            self._add_row(spec, target="source")
        self._fill_ranges(w)

    def _clear(self) -> None:
        self.editors.clear()
        while self.form.count():
            item = self.form.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.ranges.blockSignals(True)
        self.ranges.setRowCount(0)
        self.ranges.blockSignals(False)

    def _add_row(self, spec: forms.FieldSpec, target: str) -> None:
        editor = self._editor(spec, target)
        if editor is None:
            return
        label = QLabel(spec.name + (" *" if spec.required else ""))
        if spec.note:
            label.setToolTip(spec.note)
            label.setStyleSheet("color:#c9a227;")
        self.form.addRow(label, editor)
        self.editors[spec.name] = editor

    def _editor(self, spec: forms.FieldSpec, target: str):
        def write(value):
            obj = self.widget.kind if target == "kind" else self.widget.source
            if obj is not None:
                obj[spec.name] = value
                self.changed.emit()

        if spec.kind == "number":
            e = QDoubleSpinBox()
            e.setRange(-100000.0, 100000.0)
            e.setDecimals(2)
            e.setValue(float(spec.value or 0.0))
            e.valueChanged.connect(write)
            return e
        if spec.kind == "bool":
            e = QCheckBox()
            e.setChecked(bool(spec.value))
            e.toggled.connect(write)
            return e
        if spec.kind == "color":
            e = ColorButton(spec.value or [255, 255, 255, 255])
            e.changed.connect(write)
            return e
        if spec.kind == "font":
            e = QLineEdit(str((spec.value or {}).get("path", "")))
            e.editingFinished.connect(lambda: write({"path": e.text()}))
            return e
        if spec.kind == "json":
            e = QPlainTextEdit(json.dumps(spec.value))
            e.setMaximumHeight(70)
            return e
        e = QLineEdit("" if spec.value is None else str(spec.value))
        e.editingFinished.connect(lambda: write(e.text()))
        return e

    # --- ranges ------------------------------------------------------------

    def _fill_ranges(self, w: Widget) -> None:
        rows = forms.range_rows(w)
        self.ranges.blockSignals(True)
        self.ranges.setRowCount(len(rows))
        for row in rows:
            unit = f" {row.unit}" if row.unit else ""
            text = "—" if row.threshold is None else f"{row.threshold:.4g}{unit}"
            item = QTableWidgetItem(text)
            if row.threshold is None:
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                item.setToolTip("the catch-all range; it has no threshold")
            self.ranges.setItem(row.index, 0, item)
            swatch = ColorButton(list(row.color) + [row.alpha if row.alpha
                                                    is not None else 255])
            swatch.changed.connect(
                lambda rgba, i=row.index: self._set_range_color(i, rgba))
            self.ranges.setCellWidget(row.index, 1, swatch)
            self.ranges.setItem(row.index, 2, QTableWidgetItem(
                "" if row.alpha is None else str(row.alpha)))
        self.ranges.blockSignals(False)

    def _set_range_color(self, index: int, rgba: list[int]) -> None:
        entry = self.widget.kind["ranges"][index]
        entry["color"] = rgba[:3]
        entry["alpha"] = rgba[3]
        self.changed.emit()

    def _range_edited(self, item: QTableWidgetItem) -> None:
        if self.widget is None:
            return
        entry = self.widget.kind.get("ranges", [])[item.row()]
        if item.column() == 2:
            entry["alpha"] = int(float(item.text() or 255))
            self.changed.emit()
            return
        raw_text = item.text().split()[0] if item.text().strip() else ""
        try:
            raw = float(raw_text)
        except ValueError:
            return
        # False means unchanged: writing anyway would re-encode a percentage
        # the user never touched and drift the stored float on every save.
        if forms.set_threshold(self.widget, item.row(), raw):
            self.changed.emit()

    # --- structural changes ------------------------------------------------

    def _confirm(self, change: forms.Change, what: str) -> bool:
        if not change.dropped:
            return True
        listing = ", ".join(f"{k}={v!r}" for k, v in change.dropped.items())
        answer = QMessageBox.question(
            self, f"Change {what}?",
            f"The new {what} does not have these fields; they will be "
            f"dropped:\n\n{listing}\n\nContinue?")
        return answer == QMessageBox.Yes

    def _change_kind(self) -> None:
        if self.widget is None:
            return
        target = self.kind_combo.currentText()
        if target == self.widget.kind_type:
            return
        preview = forms.change_kind(
            Widget.from_json(json.loads(json.dumps(self.widget.to_json()))),
            target)
        if not self._confirm(preview, "kind"):
            self.kind_combo.setCurrentText(self.widget.kind_type)
            return
        forms.change_kind(self.widget, target)
        self.structure_changed.emit()

    def _change_source(self) -> None:
        if self.widget is None or self.widget.source is None:
            return
        target = self.source_combo.currentText()
        if target == self.widget.source.get("type"):
            return
        preview = forms.change_source(
            Widget.from_json(json.loads(json.dumps(self.widget.to_json()))),
            target)
        if not self._confirm(preview, "source"):
            self.source_combo.setCurrentText(str(self.widget.source.get("type")))
            return
        forms.change_source(self.widget, target)
        self.structure_changed.emit()

    def _raw_committed(self, event) -> None:
        QPlainTextEdit.focusOutEvent(self.raw, event)
        if self.widget is None:
            return
        try:
            self.widget.kind = json.loads(self.raw.toPlainText())
        except json.JSONDecodeError:
            return
        self.changed.emit()
```

- [ ] **Step 4: Write the widget list**

`lianli_panel/gui/sidebar.py`:

```python
"""The widget list.

Its job is DRAW ORDER, which is array order and is load-bearing: only the last
widget covering a rect is visible, which is the whole cover-bar trick. The list
shows the order top-to-bottom as drawn, flags cover bars, and surfaces the
warnings from draft.cover_warnings when a reorder breaks one.
"""
from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QPushButton,
                               QVBoxLayout, QWidget)

from .draft import cover_warnings


class WidgetList(QWidget):
    selected = Signal(str)
    reordered = Signal(str, int)
    deleted = Signal(str)
    duplicated = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(
            lambda text: self.selected.emit(text.split("  ")[0]))
        self.warnings = QLabel("")
        self.warnings.setWordWrap(True)
        self.warnings.setStyleSheet("color:#c9a227;")

        up, down = QPushButton("▲"), QPushButton("▼")
        dup, rm = QPushButton("duplicate"), QPushButton("delete")
        up.clicked.connect(lambda: self._emit_move(-1))
        down.clicked.connect(lambda: self._emit_move(+1))
        dup.clicked.connect(lambda: self._emit(self.duplicated))
        rm.clicked.connect(lambda: self._emit(self.deleted))

        buttons = QHBoxLayout()
        for b in (up, down, dup, rm):
            buttons.addWidget(b)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("widgets (drawn top to bottom)"))
        root.addWidget(self.list)
        root.addLayout(buttons)
        root.addWidget(self.warnings)

    def set_draft(self, draft) -> None:
        template = draft.current()
        self.list.clear()
        if template is None:
            return
        for w in template.widgets:
            mark = "  [cover]" if (w.kind_type == "horizontal_bar"
                                   and w.kind.get("value_max") == 1) else ""
            self.list.addItem(f"{w.id}{mark}")
        self.warnings.setText("\n".join(cover_warnings(template)))

    def _current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.text().split("  ")[0] if item else None

    def _emit(self, signal) -> None:
        wid = self._current_id()
        if wid:
            signal.emit(wid)

    def _emit_move(self, delta: int) -> None:
        wid = self._current_id()
        if wid:
            self.reordered.emit(wid, delta)
```

- [ ] **Step 5: Wire both into the window**

In `lianli_panel/gui/window.py`, add the imports and build the right-hand pane:

```python
from .inspector import Inspector
from .sidebar import WidgetList
```

In `__init__`, after the canvas:

```python
        self.inspector = Inspector()
        self.inspector.changed.connect(self._field_edited)
        self.inspector.structure_changed.connect(self._structure_edited)

        self.widget_list = WidgetList()
        self.widget_list.selected.connect(self._select_from_list)
        self.widget_list.reordered.connect(self._reorder)
        self.widget_list.deleted.connect(self._delete_widget)
        self.widget_list.duplicated.connect(self._duplicate_widget)
```

Add them to the layout — `template_list` and `widget_list` stacked on the left, `inspector` on the right:

```python
        left = QVBoxLayout()
        left.addWidget(self.template_list)
        left.addWidget(self.widget_list, 1)
        left_holder = QWidget()
        left_holder.setLayout(left)
        left_holder.setMaximumWidth(300)

        self.inspector.setMaximumWidth(360)

        body = QHBoxLayout()
        body.addWidget(left_holder)
        body.addWidget(self.canvas, 1)
        body.addWidget(self.inspector)
```

And the slots:

```python
    def _select(self, widget_id: str) -> None:
        self.draft.selection = widget_id or None
        self.inspector.set_widget(self.draft.widget(widget_id) if widget_id else None)

    def _select_from_list(self, widget_id: str) -> None:
        self.canvas.set_selection(widget_id or None)
        self._select(widget_id)

    def _field_edited(self) -> None:
        self.draft.dirty = True
        self.rerender()

    def _structure_edited(self) -> None:
        self.draft.dirty = True
        self.inspector.set_widget(self.draft.widget(self.draft.selection or ""))
        self.rerender()

    def _reorder(self, widget_id: str, delta: int) -> None:
        self.draft.reorder_widget(widget_id, delta)
        self._refresh_lists()

    def _delete_widget(self, widget_id: str) -> None:
        self.draft.delete_widget(widget_id)
        self.inspector.set_widget(None)
        self._refresh_lists()

    def _duplicate_widget(self, widget_id: str) -> None:
        self.draft.duplicate_widget(widget_id)
        self._refresh_lists()

    def _refresh_lists(self) -> None:
        self.widget_list.set_draft(self.draft)
        self.canvas.set_widgets(self.draft.rects())
        self.rerender()
```

Call `self._refresh_lists()` at the end of `load()` and `_choose_template()` in place of the bare `canvas.set_widgets` added in Task 6.

Note the inspector edits the **same** `Widget` object the draft holds, so a field write lands in the draft directly; `_field_edited` only marks it dirty and re-renders. `draft.dirty = True` is deliberate here rather than a checkpoint per keystroke.

- [ ] **Step 6: Run the smoke tests**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: 10 passed

- [ ] **Step 7: Run the whole suite**

Run: `./.venv/bin/pytest -q`
Expected: everything green. Nothing in this task touches the core modules, so a failure there means the inspector mutated something it should have copied.

- [ ] **Step 8: CONTROLLER — edit fields in the real app**

```bash
./.venv/bin/python -m lianli_panel.gui.app
```

Confirm, and record what you actually see:

1. Selecting a widget fills the inspector; required fields are starred.
2. Changing a colour re-renders within ~0.3 s and the new colour is visible.
3. A `radial_gauge`'s range thresholds read in real units — for a 20..100 gauge, a stored `60` shows as `68`.
4. The widget list marks the cover bars, and moving one above what it covers raises the warning text.
5. Switching a widget's kind asks before dropping fields.
6. **Still nothing applied**: `./.venv/bin/python -m lianli_panel.cli list` shows the original set hash.

- [ ] **Step 9: Commit**

```bash
git add lianli_panel/gui/inspector.py lianli_panel/gui/sidebar.py \
        lianli_panel/gui/window.py tests/test_gui_smoke.py
git commit -m "feat: add schema-driven inspector and draw-order widget list

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

---

### Task 8: Template library, apply, and revert

This task is where the app first writes to the panel. Every hazard the core plan enumerated meets a UI here.

**Files:**

- Create: nothing
- Modify: `lianli_panel/gui/sidebar.py`, `lianli_panel/gui/window.py`, `lianli_panel/apply.py`, `lianli_panel/cli.py:64-83`
- Test: `tests/test_gui_smoke.py`, `tests/test_apply.py`

**Interfaces:**

- Consumes: `apply.apply_templates`, `apply.ConflictError`, `apply.ApplyFailed`, `apply.read_templates`, `snapshot.take`, `snapshot.latest`, `snapshot.load`, `model.validate`.
- Produces: `apply.lcd_entry_fallback(root=None) -> dict | None` (moved out of `cli._lcd_fallback`); `sidebar.TemplateList(parent=None)` with `set_draft(d)`, signals `chosen(str)`, `made_live(str)`, `created()`, `duplicated(str)`, `renamed(str, str)`, `deleted(str)`; `window.MainWindow.apply_now()`, `window.MainWindow.revert()`.

- [ ] **Step 1: Move the LCD-entry fallback into the apply module**

The GUI needs the same known-good `config.lcds` entry the CLI uses, and a second copy of this logic would be a second source of truth for the one path that can leave the panel unable to render. Cut `_lcd_fallback` out of `cli.py` and paste it into `apply.py` as a public function:

```python
def lcd_entry_fallback(root=None) -> dict | None:
    """Newest snapshotted config.lcds entry, for rebuilding a wiped array.

    Walks snapshots newest-first because the most recent one may itself have
    been taken while the array was empty -- lianli-gui wipes it on every config
    write. Returns None if no snapshot ever recorded an entry, in which case
    apply_templates raises rather than inventing an orientation and serial.
    """
    from . import snapshot
    root = root or snapshot.SNAPSHOT_ROOT
    if not root.exists():
        return None
    for d in sorted((p for p in root.iterdir() if p.is_dir()),
                    key=lambda p: p.name, reverse=True):
        try:
            entries = snapshot.load(d).get("lcds") or []
        except (OSError, ValueError):
            continue
        if entries:
            return entries[0]
    return None
```

The import is function-local on purpose: `snapshot` imports `apply`, and a module-level import would be circular.

In `cli.py`, delete `_lcd_fallback` and replace both call sites with `apply_mod.lcd_entry_fallback()`.

- [ ] **Step 2: Run the existing suite to prove the move was clean**

Run: `./.venv/bin/pytest -q`
Expected: everything green, unchanged counts.

- [ ] **Step 3: Write the failing apply smoke tests**

Append to `tests/test_gui_smoke.py`:

```python
def test_apply_snapshots_then_sends_templates_then_media(qapp, win, monkeypatch, tmp_path):
    """SetLcdTemplates alone does not update the panel -- it replaces the
    stored template while the live renderer keeps what it last prepared. The
    order below is the whole point of routing through apply_templates."""
    from lianli_panel.gui import window as win_mod
    monkeypatch.setattr(win_mod.snapshot, "take", lambda c, **k: tmp_path / "snap")
    monkeypatch.setattr(win_mod.apply_mod, "lcd_entry_fallback", lambda: CONFIG["lcds"][0])
    win.apply_now()
    methods = win.client.methods()
    assert methods.index("SetLcdTemplates") < methods.index("SetLcdMedia")
    sent = [c for c in win.client.calls if c[0] == "SetLcdTemplates"][0][1]
    assert [t["id"] for t in sent["templates"]] == ["gaming-dash", "spare"]


def test_apply_sends_the_whole_library_after_deleting_one(qapp, win, monkeypatch, tmp_path):
    from lianli_panel.gui import window as win_mod
    monkeypatch.setattr(win_mod.snapshot, "take", lambda c, **k: tmp_path / "snap")
    monkeypatch.setattr(win_mod.apply_mod, "lcd_entry_fallback", lambda: CONFIG["lcds"][0])
    win.draft.delete_template("spare")
    win.apply_now()
    sent = [c for c in win.client.calls if c[0] == "SetLcdTemplates"][0][1]
    assert [t["id"] for t in sent["templates"]] == ["gaming-dash"]


def test_a_conflicting_apply_writes_nothing_when_declined(qapp, win, monkeypatch, tmp_path):
    """Another process wrote to the set while this draft was open. A whole-set
    write would silently discard their change."""
    from PySide6.QtWidgets import QMessageBox
    from lianli_panel.gui import window as win_mod
    monkeypatch.setattr(win_mod.snapshot, "take", lambda c, **k: tmp_path / "snap")
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.No))
    win.draft.base_hash = "0" * 64
    win.apply_now()
    assert "SetLcdTemplates" not in win.client.methods()
```

- [ ] **Step 4: Run them to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: FAIL — `AttributeError: 'MainWindow' object has no attribute 'apply_now'`

- [ ] **Step 5: Add the template list to the sidebar**

Append to `lianli_panel/gui/sidebar.py`:

```python
from PySide6.QtWidgets import QInputDialog, QRadioButton


class TemplateList(QWidget):
    """The library.

    'Live' is not a property of a template -- it is the template_id field on
    the LCD's entry in config.lcds, so the radio button here is re-pointing
    that field on the next apply, not flipping a flag on the template.
    """
    chosen = Signal(str)
    made_live = Signal(str)
    created = Signal()
    duplicated = Signal(str)
    renamed = Signal(str, str)
    deleted = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.list = QListWidget()
        self.list.currentTextChanged.connect(self._chosen)
        self.live_button = QRadioButton("live on the panel")
        self.live_button.clicked.connect(self._make_live)

        new, dup = QPushButton("new"), QPushButton("duplicate")
        ren, rm = QPushButton("rename"), QPushButton("delete")
        new.clicked.connect(self.created.emit)
        dup.clicked.connect(lambda: self._with_current(self.duplicated.emit))
        ren.clicked.connect(self._rename)
        rm.clicked.connect(lambda: self._with_current(self.deleted.emit))

        buttons = QHBoxLayout()
        for b in (new, dup, ren, rm):
            buttons.addWidget(b)

        root = QVBoxLayout(self)
        root.addWidget(QLabel("templates"))
        root.addWidget(self.list)
        root.addWidget(self.live_button)
        root.addLayout(buttons)

    def set_draft(self, draft) -> None:
        self.list.blockSignals(True)
        self.list.clear()
        for t in draft.templates:
            mark = " ●" if t.id == draft.live_id else ""
            self.list.addItem(f"{t.id}{mark}")
        ids = [t.id for t in draft.templates]
        if draft.current_id in ids:
            self.list.setCurrentRow(ids.index(draft.current_id))
        self.list.blockSignals(False)
        self.live_button.setChecked(draft.current_id == draft.live_id)

    def current_id(self) -> str | None:
        item = self.list.currentItem()
        return item.text().removesuffix(" ●") if item else None

    def _chosen(self, text: str) -> None:
        if text:
            self.chosen.emit(text.removesuffix(" ●"))

    def _with_current(self, fn) -> None:
        tid = self.current_id()
        if tid:
            fn(tid)

    def _make_live(self) -> None:
        self._with_current(self.made_live.emit)

    def _rename(self) -> None:
        tid = self.current_id()
        if not tid:
            return
        name, ok = QInputDialog.getText(self, "Rename template", "New name:")
        if ok and name:
            self.renamed.emit(tid, name)
```

- [ ] **Step 6: Wire apply and revert into the window**

In `lianli_panel/gui/window.py`, add imports:

```python
from .. import apply as apply_mod
from .. import snapshot
from ..model import validate
from .sidebar import TemplateList, WidgetList
from PySide6.QtWidgets import QMessageBox, QToolBar
```

Replace the plain `QListWidget` template list with `TemplateList` and connect it:

```python
        self.template_list = TemplateList()
        self.template_list.chosen.connect(self._choose_template)
        self.template_list.made_live.connect(self._set_live)
        self.template_list.created.connect(self._new_template)
        self.template_list.duplicated.connect(self._duplicate_template)
        self.template_list.renamed.connect(self._rename_template)
        self.template_list.deleted.connect(self._delete_template)
```

Add a toolbar in `__init__`:

```python
        bar = QToolBar()
        bar.addAction("Apply", self.apply_now)
        bar.addAction("Revert", self.revert)
        bar.addAction("Refresh (runs sensors)", self.refresh_live)
        bar.addAction("Undo", lambda: (self.draft.undo(), self._refresh_lists()))
        bar.addAction("Redo", lambda: (self.draft.redo(), self._refresh_lists()))
        self.addToolBar(bar)
```

And the methods:

```python
    # --- library -----------------------------------------------------------

    def _set_live(self, tid: str) -> None:
        self.draft.set_live(tid)
        self._refresh_lists()

    def _new_template(self) -> None:
        self.draft.current_id = self.draft.add_template("new template")
        self._refresh_lists()

    def _duplicate_template(self, tid: str) -> None:
        self.draft.current_id = self.draft.duplicate_template(tid)
        self._refresh_lists()

    def _rename_template(self, tid: str, name: str) -> None:
        self.draft.rename_template(tid, name)
        self._refresh_lists()

    def _delete_template(self, tid: str) -> None:
        try:
            self.draft.delete_template(tid)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot delete", str(exc))
            return
        self._refresh_lists()

    # --- applying ----------------------------------------------------------

    def apply_now(self) -> None:
        """The ONLY write path. Snapshot, then apply_templates, which sends the
        whole set and follows SetLcdTemplates with SetLcdMedia as one
        transaction."""
        current = self.draft.current()
        if current is not None:
            errors = [p for p in validate(current) if p.level == "error"]
            if errors:
                listing = "\n".join(f"{p.widget_id}: {p.message}" for p in errors)
                if QMessageBox.question(
                        self, "Apply anyway?",
                        f"This template has errors:\n\n{listing}") != QMessageBox.Yes:
                    return
        try:
            snap = snapshot.take(self.client)
        except Exception as exc:               # a snapshot must never block a fix
            snap = None
            self._warn(f"could not snapshot before applying: {exc}")
        try:
            apply_mod.apply_templates(
                self.client, self.draft.payload(), self.draft.live_id,
                base_hash=self.draft.base_hash,
                lcd_entry_fallback=apply_mod.lcd_entry_fallback())
        except apply_mod.ConflictError as exc:
            if QMessageBox.question(
                    self, "The daemon's templates changed",
                    f"{exc}\n\nOverwrite their change with this draft?"
            ) != QMessageBox.Yes:
                return
            self.draft.base_hash = apply_mod.read_templates(self.client)[1]
            self.apply_now()
            return
        except apply_mod.ApplyFailed as exc:
            QMessageBox.critical(self, "Apply failed", str(exc))
            return
        self.draft.mark_applied(self.draft.payload())
        self.statusBar().showMessage(
            f"applied · live: {self.draft.live_id}"
            + (f" · snapshot {snap.name}" if snap else ""), 10000)

    def revert(self) -> None:
        newest = snapshot.latest()
        if newest is None:
            QMessageBox.information(self, "Revert", "no snapshots yet")
            return
        data = snapshot.load(newest)
        entry = next(iter(data.get("lcds") or []), None)
        if entry is None or entry.get("template_id") is None:
            QMessageBox.warning(self, "Revert",
                                f"snapshot {newest.name} records no live template")
            return
        if QMessageBox.question(
                self, "Revert?",
                f"Restore templates and the LCD entry from {newest.name}?\n\n"
                "NOT restored: RGB configuration, ring state, and the thermal "
                "service's on/off state — the poller re-drives the ring every "
                "~2s and would overwrite them within seconds."
        ) != QMessageBox.Yes:
            return
        apply_mod.apply_templates(self.client, data["templates"],
                                  entry["template_id"],
                                  lcd_entry_fallback=entry)
        self.load()

    def refresh_live(self) -> None:
        """Explicitly execute command sources once. Automatic renders never do:
        gaming-dash spawns 16 subprocesses per render, and graph.sh writes the
        state file the LIVE panel's sparkline reads."""
        current = self.draft.current()
        if current is None:
            return
        if not self.worker.refresh_live(current.to_json()):
            self.statusBar().showMessage("a render is already in flight", 3000)
```

Finally, guard closing a dirty draft:

```python
    def closeEvent(self, event) -> None:
        if self.draft.dirty and QMessageBox.question(
                self, "Unapplied changes",
                "This draft has changes that were never applied. Close anyway?"
        ) != QMessageBox.Yes:
            event.ignore()
            return
        self.worker.stop()
        super().closeEvent(event)
```

- [ ] **Step 7: Run the smoke tests**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: 13 passed

- [ ] **Step 8: Run the whole suite**

Run: `./.venv/bin/pytest -q`
Expected: green, including `tests/test_apply.py` and `tests/test_cli.py` after the fallback move.

- [ ] **Step 9: CONTROLLER — record the state of the panel before writing to it**

```bash
./.venv/bin/python -m lianli_panel.cli list
./.venv/bin/python -m lianli_panel.cli snapshot
./.venv/bin/python -m lianli_panel.cli status
```

Keep the set hash and the snapshot path. If `status` reports a problem, restart the daemon and re-check before continuing — applying into a dead handle returns `ok` and changes nothing on the screen, which would read as "the GUI's apply is broken".

- [ ] **Step 10: CONTROLLER — apply an edit from the GUI and confirm it on the physical panel**

```bash
./.venv/bin/python -m lianli_panel.gui.app
```

1. Move one visible widget — a clock or a label, something unmistakable — by roughly 40 px.
2. Press Apply.
3. **Look at the screen.** The widget has moved.
4. Check the templates survived whole:

```bash
./.venv/bin/python -m lianli_panel.cli list
```

Every template that existed before is still listed. A shrunken list means the whole-set write is broken and is the most damaging failure this app can have.

5. Press Revert, confirm, and check the panel returns to the snapshotted layout.

Record what actually happened. If the panel did not change but the CLI reports the new hash, that is the dead-encoder failure, not an apply bug — `sudo systemctl restart lianli-daemon-system.service`, re-apply RGB, and retry.

- [ ] **Step 11: Commit**

```bash
git add lianli_panel/apply.py lianli_panel/cli.py lianli_panel/gui/sidebar.py \
        lianli_panel/gui/window.py tests/test_gui_smoke.py
git commit -m "feat: apply the whole template library transactionally from the GUI

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

> **HANDOFF POINT.** Eight tasks done, and the editor is functional. Update the
> SDD ledger and end the session.

---

### Task 9: Changing a widget's span must not move its thresholds

A stored range `max` is a **percentage of that widget's own `value_min..value_max` span**. Every inspector field built in Task 7 writes straight into `widget.kind`, so typing a new `value_max` moves every colour boundary in real terms while no visible field changes: on a 0..100 gauge a stored `60` means 60 °C, and widening to 0..200 silently turns the same stored `60` into 120 °C. The spec settles the direction — **raw stays fixed; the user typed °C and means °C** — and nothing implements it yet.

This is the same silent-failure class as the conversion itself: the panel renders, nothing errors, the colours are simply wrong.

**Files:**

- Create: nothing
- Modify: `lianli_panel/gui/forms.py`, `lianli_panel/gui/inspector.py`
- Test: `tests/test_gui_forms.py` (16 → 22), `tests/test_gui_smoke.py` (13 → 15)

**Interfaces:**

- Consumes: `model.widget_span`, `model.range_thresholds_raw`, `model.set_range_threshold_raw`.
- Produces: `forms.SpanChange(rewritten: list[int], clamped: list[int])`; `forms.set_span(w, value_min, value_max) -> SpanChange`; `inspector.Inspector._write_span(name: str, value: float)`.

- [ ] **Step 1: Write the failing span tests**

Append to `tests/test_gui_forms.py`:

```python
def _gauge_with_ranges():
    """A fresh copy each time: these tests mutate the range dicts, and sharing
    GAUGE's would let one test's rewrite leak into the next."""
    return _widget({**GAUGE, "ranges": [dict(GAUGE["ranges"][0]),
                                        dict(GAUGE["ranges"][1])]})


def test_widening_the_span_holds_the_real_thresholds_still():
    """60% of 20..100 is 68 C. After widening to 20..180 the boundary must
    still MEAN 68 C, so the stored percentage has to drop to 30."""
    w = _gauge_with_ranges()
    change = forms.set_span(w, 20.0, 180.0)
    assert w.kind["value_max"] == 180.0
    assert w.kind["ranges"][0]["max"] == pytest.approx(30.0)
    assert forms.range_rows(w)[0].threshold == pytest.approx(68.0)
    assert change.rewritten == [0]
    assert change.clamped == []


def test_narrowing_the_span_past_a_threshold_clamps_and_says_so():
    """raw_to_pct clamps to [0,100], so a threshold outside the new span
    cannot be preserved. It collapses onto an endpoint -- reported, not lost
    quietly, because widening the span again will not bring it back."""
    w = _gauge_with_ranges()
    change = forms.set_span(w, 20.0, 50.0)
    assert change.clamped == [0]
    assert w.kind["ranges"][0]["max"] == pytest.approx(100.0)


def test_set_span_leaves_the_catch_all_null():
    w = _gauge_with_ranges()
    forms.set_span(w, 0.0, 200.0)
    assert w.kind["ranges"][1]["max"] is None


def test_set_span_writes_nothing_when_the_span_is_unchanged():
    """Re-encoding a percentage the user never touched drifts the stored float
    on every save -- the same rule set_threshold follows."""
    w = _gauge_with_ranges()
    change = forms.set_span(w, 20.0, 100.0)
    assert change.rewritten == []
    assert w.kind["ranges"][0]["max"] == 60.0


def test_a_degenerate_span_normalises_to_zero():
    """value_min == value_max is representable and must be defined rather than
    dividing by zero."""
    w = _gauge_with_ranges()
    change = forms.set_span(w, 50.0, 50.0)
    assert w.kind["ranges"][0]["max"] == 0.0
    assert change.clamped == [0]


def test_set_span_on_a_widget_with_no_ranges_just_writes_the_span():
    w = _widget({"type": "vertical_bar", "source": {"type": "cpu_usage"},
                 "value_min": 0.0, "value_max": 100.0,
                 "background_color": [0, 0, 0, 255]})
    change = forms.set_span(w, 10.0, 90.0)
    assert (w.kind["value_min"], w.kind["value_max"]) == (10.0, 90.0)
    assert change.rewritten == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_forms.py -v`
Expected: FAIL — `AttributeError: module 'lianli_panel.gui.forms' has no attribute 'set_span'`

- [ ] **Step 3: Add `set_span` to the forms module**

In `lianli_panel/gui/forms.py`, extend the model import to bring in `widget_span` (already imported) and add this after `set_threshold`:

```python
@dataclass
class SpanChange:
    rewritten: list[int] = field(default_factory=list)
    clamped: list[int] = field(default_factory=list)


def set_span(w: Widget, value_min: float, value_max: float) -> SpanChange:
    """Move value_min/value_max and hold the REAL thresholds still.

    Stored range maxima are percentages OF THIS SPAN, so writing value_max
    straight into the dict moves every colour boundary in real terms while no
    visible field changes. The spec settles the direction: raw stays fixed,
    because the user typed degrees and means degrees. So the percentages are
    re-encoded around the new span and the real numbers stay put.

    raw_to_pct CLAMPS to [0,100]. Narrowing a span past a threshold therefore
    cannot preserve it -- that threshold collapses onto an endpoint and the
    index is reported in `clamped`, so the UI can say the value is gone rather
    than let the user widen the span again and wonder why it did not come back.
    """
    new_min, new_max = float(value_min), float(value_max)
    if widget_span(w) == (new_min, new_max):
        # Unchanged: re-encoding untouched percentages drifts their stored
        # floats on every save and breaks the lossless round trip.
        return SpanChange()

    before = range_thresholds_raw(w)     # real units, under the OLD span
    w.kind["value_min"] = new_min
    w.kind["value_max"] = new_max

    change = SpanChange()
    lo, hi = min(new_min, new_max), max(new_min, new_max)
    for i, raw in enumerate(before):
        if raw is None:                  # the catch-all has no threshold
            continue
        set_range_threshold_raw(w, i, raw)
        change.rewritten.append(i)
        if raw < lo or raw > hi:
            change.clamped.append(i)
    return change
```

Note `field` is already imported from `dataclasses` at the top of the module (`Change` uses it).

- [ ] **Step 4: Run the forms tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_forms.py -v`
Expected: 22 passed

- [ ] **Step 5: Write the failing inspector smoke tests**

Append to `tests/test_gui_smoke.py`:

```python
def test_editing_value_max_in_the_inspector_holds_the_real_thresholds(qapp, win):
    """The cpu widget stores max=60 on a 0..100 span, i.e. 60%. Widening to
    0..200 must leave the boundary meaning 60, which is 30%. Writing value_max
    straight into the dict would leave 60% and move the boundary to 120 with
    nothing on screen to show for it."""
    win._select("cpu")
    win.inspector.editors["value_max"].setValue(200.0)
    ranges = win.draft.widget("cpu").kind["ranges"]
    assert ranges[0]["max"] == pytest.approx(30.0)
    assert ranges[1]["max"] is None


def test_editing_value_max_marks_the_draft_dirty(qapp, win):
    win._select("cpu")
    assert win.draft.dirty is False
    win.inspector.editors["value_max"].setValue(120.0)
    assert win.draft.dirty is True
```

- [ ] **Step 6: Run them to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: FAIL — `assert 60.0 == approx(30.0)`. The span field currently writes through the generic path, which is exactly the bug.

- [ ] **Step 7: Route the span fields through `set_span` in the inspector**

In `lianli_panel/gui/inspector.py`, add `widget_span` to the model import:

```python
from ..model import Widget, widget_span
```

Then, inside `_editor`, replace the `write` closure with one that intercepts the two span fields:

```python
        def write(value):
            obj = self.widget.kind if target == "kind" else self.widget.source
            if obj is None:
                return
            # value_min/value_max are NOT ordinary numbers: every range
            # threshold is a percentage of the span they define, so writing one
            # through the generic path moves every colour boundary in real
            # terms with nothing on screen to show for it.
            if target == "kind" and spec.name in ("value_min", "value_max"):
                self._write_span(spec.name, float(value))
                return
            obj[spec.name] = value
            self.changed.emit()
```

And add the method beside `_set_range_color`:

```python
    def _write_span(self, name: str, value: float) -> None:
        lo, hi = widget_span(self.widget) or (0.0, 100.0)
        lo, hi = (value, hi) if name == "value_min" else (lo, value)
        change = forms.set_span(self.widget, lo, hi)
        if change.clamped:
            QMessageBox.warning(
                self, "Thresholds clamped",
                f"{len(change.clamped)} range threshold(s) fall outside the new "
                f"{lo:g}..{hi:g} span and have been clamped to an endpoint. "
                "Widening the span again will not restore the old values — "
                "undo will.")
        self._fill_ranges(self.widget)   # the table still shows the old numbers
        self.changed.emit()
```

`_fill_ranges` is called because the range table was populated from the old span; without it the thresholds on screen stay correct by coincidence only until the next repopulate.

- [ ] **Step 8: Run both test files**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_forms.py tests/test_gui_smoke.py -v`
Expected: 37 passed (22 forms, 15 smoke)

- [ ] **Step 9: Run the whole suite**

Run: `./.venv/bin/pytest -q`
Expected: green. `tests/test_model_ranges.py` in particular must be untouched — this task changes who calls the conversion, not the conversion.

- [ ] **Step 10: Commit**

```bash
git add lianli_panel/gui/forms.py lianli_panel/gui/inspector.py \
        tests/test_gui_forms.py tests/test_gui_smoke.py
git commit -m "fix: hold real range thresholds fixed when a widget's span changes

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

---

### Task 10: Health and vendor-GUI interlock banners

Two conditions the rest of the app cannot see, because neither shows up in a return value:

- the panel's handle is **dead** while every IPC call still returns `ok` — the encoder died on replug and the daemon never reopened the screen
- **`lianli-gui` is running**, and it wipes `config.lcds` on every config write, after which the daemon has no LCD entry and the panel renders nothing

`health.py` already detects the first from the journal. This task adds the second, gets both off the UI thread, and replaces the single `QLabel` banner — which shows only the last message and never clears — with a keyed stack.

**Files:**

- Create: `lianli_panel/gui/status.py`
- Modify: `lianli_panel/health.py`, `lianli_panel/gui/window.py`
- Test: `tests/test_health.py` (10 → 16), `tests/test_gui_status.py` (new, 8), `tests/test_gui_smoke.py` (15 → 20)

**Interfaces:**

- Consumes: `health.check`, `health.PanelHealth`, `health.RESTART_HINT`, `apply.LCD_SERIAL`, `ipc.DaemonError`.
- Produces: `health.VENDOR_GUI_NAMES`, `health.VENDOR_GUI_WARNING`, `health.vendor_gui_pids(names=VENDOR_GUI_NAMES, proc_root="/proc") -> list[int]`, `health.config_lcds_problem(config: dict, serial: str) -> str | None`; `status.BannerStack(parent=None)` with `show_banner(key, text, level="error", action=None)`, `clear(key)`, `keys() -> list[str]`, `text() -> str`; `status.HealthPoller(parent=None, *, check=health.check, scan=health.vendor_gui_pids, interval_ms=60000)` with signal `reported(object, object)` and methods `poll()`, `stop()`; `window.MainWindow(client, *, health_poller=None)`, `window.MainWindow.verify_lcd_entry()`.

- [ ] **Step 1: Write the failing health tests**

Append to `tests/test_health.py`:

```python
from pathlib import Path

from lianli_panel.health import config_lcds_problem, vendor_gui_pids

SERIAL = "hid:513b5a7acadc4203"


def _proc(root: Path, pid: int, comm: str, exe: str | None = None) -> None:
    """One fake /proc/<pid>. The exe symlink is deliberately dangling: real
    /proc/<pid>/exe points at a path this test has no business creating, and
    vendor_gui_pids reads the link rather than following it."""
    d = root / str(pid)
    d.mkdir()
    (d / "comm").write_text(comm + "\n")
    if exe is not None:
        (d / "exe").symlink_to(exe)


def test_vendor_gui_found_by_comm(tmp_path):
    _proc(tmp_path, 101, "lianli-gui")
    _proc(tmp_path, 102, "bash")
    (tmp_path / "not-a-pid").mkdir()
    assert vendor_gui_pids(("lianli-gui",), tmp_path) == [101]


def test_vendor_gui_found_by_exe_when_comm_is_truncated(tmp_path):
    """The kernel truncates comm to 15 characters, so a longer binary name
    never compares equal to itself. The exe symlink carries the full name."""
    _proc(tmp_path, 201, "lianli-gui-lon", exe="/usr/bin/lianli-gui-longname")
    assert vendor_gui_pids(("lianli-gui-longname",), tmp_path) == [201]


def test_vendor_gui_ignores_an_unreadable_process(tmp_path):
    """/proc/<pid>/exe is unreadable for another user's processes, and a
    process can exit mid-scan. Neither may raise."""
    d = tmp_path / "301"
    d.mkdir()                       # no comm, no exe: a process that vanished
    assert vendor_gui_pids(("lianli-gui",), tmp_path) == []


def test_a_healthy_lcd_entry_reports_no_problem():
    config = {"lcds": [{"serial": SERIAL, "type": "custom",
                        "template_id": "gaming-dash", "orientation": 90}]}
    assert config_lcds_problem(config, SERIAL) is None


def test_an_empty_lcds_array_is_the_vendor_gui_wipe():
    assert "EMPTY" in config_lcds_problem({"lcds": []}, SERIAL)


def test_an_entry_in_media_mode_is_reported():
    config = {"lcds": [{"serial": SERIAL, "type": "media",
                        "template_id": "gaming-dash"}]}
    problem = config_lcds_problem(config, SERIAL)
    assert problem is not None and "custom" in problem
```

- [ ] **Step 2: Run them to verify they fail**

Run: `./.venv/bin/pytest tests/test_health.py -v`
Expected: FAIL — `ImportError: cannot import name 'config_lcds_problem'`

- [ ] **Step 3: Add process and config checks to `health.py`**

Append to `lianli_panel/health.py`, and add `import os` and `from pathlib import Path` to the imports at the top:

```python
# The vendor GUI. It cannot represent template mode, so every config write it
# performs drops the lcds array entirely -- see apply.py hazard 3.
VENDOR_GUI_NAMES = ("lianli-gui",)

VENDOR_GUI_WARNING = (
    "lianli-gui is running. It cannot represent template mode, so it WIPES "
    "config.lcds every time it writes config — after which the daemon has no "
    "LCD entry, the panel renders nothing, and no call reports an error. This "
    "app will not close it for you. Close it, then re-check the entry."
)


def vendor_gui_pids(names: Iterable[str] = VENDOR_GUI_NAMES,
                    proc_root: str | Path = "/proc") -> list[int]:
    """PIDs of the vendor GUI, read from /proc rather than by running pgrep.

    No subprocess: this is polled on a timer, and spawning a process every
    minute to ask a question /proc answers directly is pure waste.

    Matched two ways because neither is sufficient alone. `comm` is TRUNCATED
    TO 15 CHARACTERS by the kernel, so a longer binary name never compares
    equal to itself; the `exe` symlink carries the full path but is unreadable
    for processes owned by another user. Either match counts.

    Every read is guarded: a process can exit between listing the directory and
    reading its files, and a scan that raises would take the banner down with
    it.
    """
    wanted = set(names)
    truncated = {n[:15] for n in wanted}
    root = Path(proc_root)
    try:
        entries = sorted(root.iterdir(), key=lambda p: p.name)
    except OSError:
        return []

    found: list[int] = []
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            comm = (entry / "comm").read_text().strip()
        except OSError:
            comm = ""
        if comm and comm in truncated:
            found.append(int(entry.name))
            continue
        try:
            exe = os.path.basename(os.readlink(entry / "exe"))
        except OSError:
            continue
        if exe in wanted:
            found.append(int(entry.name))
    return found


def config_lcds_problem(config: dict, serial: str) -> str | None:
    """Whether config.lcds still carries a usable entry for this panel.

    None means fine; anything else is a sentence naming what is wrong. This is
    the state lianli-gui destroys, and nothing else reports it: IPC calls keep
    returning ok against a config that can no longer render anything.
    """
    entries = config.get("lcds") or []
    if not entries:
        return ("config.lcds is EMPTY — this is what lianli-gui does on every "
                "config write. The daemon has no LCD entry to render to. "
                "Applying from this app restores the entry from the newest "
                "snapshot; if no snapshot ever recorded one, the apply will "
                "refuse rather than guess an orientation and serial.")
    entry = next((e for e in entries if e.get("serial") == serial), None)
    if entry is None:
        return (f"config.lcds has {len(entries)} entr"
                f"{'y' if len(entries) == 1 else 'ies'} but none for {serial}. "
                "The panel this app edits is not the one the daemon is "
                "configured to draw on.")
    if entry.get("type") != "custom":
        return (f"the LCD entry is in {entry.get('type')!r} mode, not 'custom', "
                "so templates are ignored entirely. Applying from this app "
                "switches it back.")
    if not entry.get("template_id"):
        return ("the LCD entry names no template_id, so the panel renders "
                "nothing. Applying from this app re-points it.")
    return None
```

- [ ] **Step 4: Run the health tests to verify they pass**

Run: `./.venv/bin/pytest tests/test_health.py -v`
Expected: 16 passed

- [ ] **Step 5: Write the failing status tests**

`tests/test_gui_status.py`:

```python
"""Banners and the poller behind them.

The banner is a STACK, not a label, and that is the point of the module: the
single QLabel it replaces showed only the most recent message and never
cleared, so a health warning would erase a render error and a condition that
resolved stayed on screen until the app was restarted.
"""
import threading
import time

import pytest

from lianli_panel import health
from lianli_panel.gui.status import BannerStack, HealthPoller


def _wait(qapp, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        qapp.processEvents()
        time.sleep(0.005)
    return pred()


def test_a_stack_with_no_banners_is_hidden(qapp):
    assert BannerStack().isHidden()


def test_two_sources_do_not_overwrite_each_other(qapp):
    s = BannerStack()
    s.show_banner("health", "the panel handle is dead")
    s.show_banner("vendor-gui", "lianli-gui is running", "warn")
    assert sorted(s.keys()) == ["health", "vendor-gui"]
    assert "dead" in s.text() and "lianli-gui" in s.text()


def test_clearing_one_key_leaves_the_other(qapp):
    s = BannerStack()
    s.show_banner("health", "dead")
    s.show_banner("render", "render failed")
    s.clear("health")
    assert s.keys() == ["render"]
    assert "dead" not in s.text()


def test_clearing_the_last_banner_hides_the_stack(qapp):
    s = BannerStack()
    s.show_banner("health", "dead")
    s.clear("health")
    assert s.keys() == []
    assert s.isHidden()


def test_setting_the_same_key_twice_replaces_rather_than_stacks(qapp):
    s = BannerStack()
    s.show_banner("health", "first")
    s.show_banner("health", "second")
    assert s.keys() == ["health"]
    assert s.text() == "second"


def test_clearing_a_key_that_was_never_set_is_not_an_error(qapp):
    BannerStack().clear("nothing")


def test_the_poller_reports_what_the_probes_returned(qapp):
    report = health.PanelHealth(False, "the panel was disconnected")
    p = HealthPoller(check=lambda: report, scan=lambda: [42], interval_ms=0)
    got = []
    p.reported.connect(lambda r, pids: got.append((r, pids)))
    p.poll()
    assert _wait(qapp, lambda: got)
    assert got[0][0].reason == "the panel was disconnected"
    assert got[0][1] == [42]
    p.stop()


def test_a_probe_that_raises_becomes_an_unhealthy_report(qapp):
    """journalctl can be missing, slow, or refuse. That must degrade to a
    banner saying the CHECK failed -- never to a silent 'healthy', and never
    to an exception on a worker thread with no handler."""
    def boom():
        raise OSError("journalctl not found")

    p = HealthPoller(check=boom, scan=lambda: [], interval_ms=0)
    got = []
    p.reported.connect(lambda r, pids: got.append(r))
    p.poll()
    assert _wait(qapp, lambda: got)
    assert got[0].ok is False
    assert "journalctl not found" in got[0].reason
    p.stop()


def test_a_poll_while_one_is_running_is_dropped(qapp):
    """A journal read of a long-lived boot is not instant. Queued polls would
    stack on the one worker thread and deliver stale reports in a burst."""
    gate = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        gate.wait(3.0)
        return health.PanelHealth(True, "ok")

    p = HealthPoller(check=slow, scan=lambda: [], interval_ms=0)
    p.poll()
    assert _wait(qapp, lambda: calls, timeout=3.0)
    p.poll()
    p.poll()
    gate.set()
    time.sleep(0.1)
    qapp.processEvents()
    assert len(calls) == 1
    p.stop()
```

That is 9 tests, not 8 — `test_clearing_a_key_that_was_never_set_is_not_an_error` was added because `clear()` is called unconditionally on every report.

- [ ] **Step 6: Run them to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_status.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.gui.status'`

- [ ] **Step 7: Write the status module**

`lianli_panel/gui/status.py`:

```python
"""Banners, and the two background checks that raise them.

Both conditions here are invisible to the rest of the app because neither
appears in a return value:

  * the panel's handle is DEAD while every IPC call still returns ok (the h264
    encoder died on replug and the daemon never reopened the screen)
  * lianli-gui is running, and it wipes config.lcds on every config write

So both are polled. health.check() shells out to journalctl with a 20s timeout,
which is a visible freeze on the UI thread, so the probes run on a QThread --
the same shape as preview.PreviewWorker, for the same reason.

The banner is a STACK, not a label. The single QLabel it replaces showed only
the last message and never cleared: a health warning would erase a render
error, and a condition that resolved stayed on screen until restart. Each
source owns a key, sets it, and clears it.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, QObject, QThread, QTimer, Signal, Slot
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
                               QWidget)

from .. import health

LEVELS = {
    "error": "background:#5a1d1d; color:#ffd9d9;",
    "warn": "background:#54471a; color:#ffeec9;",
    "info": "background:#1d3a5a; color:#d9e9ff;",
}


class BannerStack(QWidget):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._rows: dict[str, QWidget] = {}
        self._texts: dict[str, str] = {}
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(2)
        self.hide()

    def show_banner(self, key: str, text: str, level: str = "error",
                    action: tuple[str, object] | None = None) -> None:
        """`action` is (caption, callable) -- a button in the banner itself,
        because a warning the user cannot act on from where they read it is a
        warning they learn to ignore."""
        self.clear(key)
        row = QWidget()
        row.setStyleSheet(LEVELS.get(level, LEVELS["error"]))
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        box = QHBoxLayout(row)
        box.setContentsMargins(8, 4, 8, 4)
        box.addWidget(label, 1)
        if action is not None:
            caption, handler = action
            button = QPushButton(caption)
            button.clicked.connect(handler)
            box.addWidget(button)
        self._layout.addWidget(row)
        self._rows[key] = row
        self._texts[key] = text
        self.show()

    def clear(self, key: str) -> None:
        row = self._rows.pop(key, None)
        self._texts.pop(key, None)
        if row is not None:
            self._layout.removeWidget(row)
            row.setParent(None)
            row.deleteLater()
        if not self._rows:
            self.hide()

    def keys(self) -> list[str]:
        return list(self._rows)

    def text(self) -> str:
        """Every banner, newline-joined. Reading the whole stack is what a test
        actually wants, and it keeps Task 5's smoke test working unchanged."""
        return "\n".join(self._texts.values())


class _Probe(QObject):
    done = Signal(object, object)

    def __init__(self, check, scan) -> None:
        super().__init__()
        self._check, self._scan = check, scan

    @Slot()
    def run(self) -> None:
        try:
            report = self._check()
        except Exception as exc:
            # A failed CHECK is not a healthy panel. Saying so is the whole
            # point -- silently reporting ok here would hide the one failure
            # this module exists to surface.
            report = health.PanelHealth(
                False, f"the panel health check itself failed: {exc}")
        try:
            pids = self._scan()
        except Exception:
            pids = []                    # a scan failure is not a vendor GUI
        self.done.emit(report, pids)


class HealthPoller(QObject):
    reported = Signal(object, object)    # PanelHealth, list[int]
    _submit = Signal()

    def __init__(self, parent=None, *, check=health.check,
                 scan=health.vendor_gui_pids, interval_ms: int = 60000) -> None:
        super().__init__(parent)
        self._busy = False
        self._thread = QThread()
        self._probe = _Probe(check, scan)
        self._probe.moveToThread(self._thread)
        self._submit.connect(self._probe.run)
        self._probe.done.connect(self._on_done)
        self._thread.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.poll)
        if interval_ms > 0:
            self._timer.start(interval_ms)

    def poll(self) -> None:
        """Dropped, not queued, while one is in flight: a journal read of a
        long-lived boot is not instant, and queued polls would arrive as a
        burst of stale reports."""
        if self._busy:
            return
        self._busy = True
        self._submit.emit()

    def _on_done(self, report, pids) -> None:
        self._busy = False
        self.reported.emit(report, pids)

    def stop(self) -> None:
        self._timer.stop()
        self._thread.quit()
        self._thread.wait(2000)
```

- [ ] **Step 8: Run the status tests to verify they pass**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_status.py -v`
Expected: 9 passed

- [ ] **Step 9: Write the failing window banner tests**

In `tests/test_gui_smoke.py`, add a stub poller and **change the `win` fixture to use it** — otherwise every smoke test shells out to `journalctl` at construction, which is slow and reads the live system journal:

```python
def stub_poller(report=None, pids=()):
    """The real poller runs journalctl. Tests must not."""
    from lianli_panel import health
    from lianli_panel.gui.status import HealthPoller
    report = report or health.PanelHealth(
        True, "panel opened at 12:00:00 with no later disconnect")
    return HealthPoller(check=lambda: report, scan=lambda: list(pids),
                        interval_ms=0)


@pytest.fixture
def win(qapp):
    from lianli_panel.gui.window import MainWindow
    w = MainWindow(make_client(), health_poller=stub_poller())
    yield w
    w.close()
```

Then append:

```python
def test_a_dead_panel_raises_the_health_banner(qapp):
    """IPC returns ok into a dead handle, so this banner is the only thing
    between 'my edit did nothing' and 'the panel has been unplugged since the
    last replug'."""
    from lianli_panel import health
    from lianli_panel.gui.window import MainWindow
    dead = health.PanelHealth(False, "the panel was disconnected at 17:59:48")
    w = MainWindow(make_client(), health_poller=stub_poller(dead))
    assert wait(qapp, lambda: "health" in w.banner.keys())
    assert "disconnected" in w.banner.text()
    assert "heuristic" in w.banner.text()
    w.close()


def test_a_healthy_panel_raises_no_banner(qapp, win):
    assert wait(qapp, lambda: win.banner.keys() == [])


def test_the_vendor_gui_gets_its_own_banner(qapp):
    from lianli_panel import health
    from lianli_panel.gui.window import MainWindow
    dead = health.PanelHealth(False, "the panel was disconnected")
    w = MainWindow(make_client(), health_poller=stub_poller(dead, pids=[4242]))
    assert wait(qapp, lambda: sorted(w.banner.keys()) == ["health", "vendor-gui"])
    assert "4242" in w.banner.text()
    w.close()


def test_verify_lcd_entry_reports_a_wiped_array(qapp, win):
    """What lianli-gui leaves behind. GetConfig still answers, the daemon still
    returns ok, and the panel renders nothing."""
    win.client.responses["GetConfig"] = {"lcds": []}
    win.verify_lcd_entry()
    assert "config" in win.banner.keys()
    assert "EMPTY" in win.banner.text()


def test_a_successful_render_clears_the_render_banner(qapp, win):
    win._render_failed("daemon went away")
    assert "render" in win.banner.keys()
    win.set_frame(b"\xff\xd8\xff\xd9")
    assert "render" not in win.banner.keys()
```

- [ ] **Step 10: Run them to verify they fail**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: FAIL — `TypeError: MainWindow.__init__() got an unexpected keyword argument 'health_poller'`

- [ ] **Step 11: Wire the banners into the window**

In `lianli_panel/gui/window.py`, add to the imports:

```python
from PySide6.QtWidgets import QApplication
from .. import health
from .status import BannerStack, HealthPoller
```

Replace the `QLabel` banner built in Task 5 with the stack — delete the three `self.banner = QLabel(...)` / `setWordWrap` / `setStyleSheet` / `hide()` lines and put:

```python
        self.banner = BannerStack()
```

`root.addWidget(self.banner)` is unchanged.

Change the constructor signature and start the poller after the preview worker is built:

```python
    def __init__(self, client, *, health_poller=None) -> None:
```

```python
        # Injectable so tests do not shell out to journalctl. The connection is
        # made BEFORE the first poll or the first report is lost.
        self.health = health_poller or HealthPoller(parent=self)
        self.health.reported.connect(self._health_reported)
        self.health.poll()
```

Add a toolbar action beside the others from Task 8:

```python
        bar.addAction("Re-check panel", self.health.poll)
```

Give `_warn` a key, and clear the ones that resolve:

```python
    def _warn(self, message: str, key: str = "daemon") -> None:
        self.banner.show_banner(key, message)
```

```python
    def _render_failed(self, message: str) -> None:
        self._warn(f"preview render failed: {message}", key="render")
```

In `set_frame`, first line — a frame that arrived is proof the last failure is over:

```python
        self.banner.clear("render")
```

In `load`, immediately after `GetConfig` succeeds (before building the draft):

```python
        self.banner.clear("daemon")
```

Then the new slots:

```python
    # --- health and interlock ----------------------------------------------

    def _health_reported(self, report, vendor_pids) -> None:
        if report.ok:
            self.banner.clear("health")
        else:
            self.banner.show_banner(
                "health",
                f"{report.reason}\n\nThis is a heuristic read of the journal, "
                "not a read of the device — it can be wrong in both directions.",
                "error",
                action=("Copy the fix", self._copy_restart_hint))
        if vendor_pids:
            pids = ", ".join(str(p) for p in vendor_pids)
            self.banner.show_banner(
                "vendor-gui", f"{health.VENDOR_GUI_WARNING} (pid {pids})",
                "warn", action=("Re-check config.lcds", self.verify_lcd_entry))
        else:
            self.banner.clear("vendor-gui")

    def _copy_restart_hint(self) -> None:
        QApplication.clipboard().setText(
            health.RESTART_HINT.split("#")[0].strip())
        self.statusBar().showMessage(
            "restart command copied to the clipboard", 8000)

    def verify_lcd_entry(self) -> None:
        """READ-ONLY. Applying is what repairs the entry; this only says
        whether it needs repairing, so pressing it while lianli-gui is still
        open cannot make anything worse."""
        try:
            config = self.client.call("GetConfig") or {}
        except DaemonError as exc:
            self._warn(f"could not read the config: {exc}", key="config")
            return
        problem = health.config_lcds_problem(config, apply_mod.LCD_SERIAL)
        if problem is None:
            self.banner.clear("config")
            self.statusBar().showMessage(
                "config.lcds still carries this panel's entry", 8000)
            return
        self.banner.show_banner("config", problem, "error")
```

At the end of `apply_now`, after `mark_applied`, re-check — an apply that returned `ok` into a dead handle is exactly the case the health banner exists for:

```python
        self.health.poll()
```

And in `closeEvent`, beside `self.worker.stop()`:

```python
        self.health.stop()
```

- [ ] **Step 12: Run the smoke tests, then the whole suite**

Run: `QT_QPA_PLATFORM=offscreen ./.venv/bin/pytest tests/test_gui_smoke.py -v`
Expected: 20 passed

Run: `./.venv/bin/pytest -q`
Expected: green throughout. If the run now takes noticeably longer, a test built a `MainWindow` without the stub poller and is waiting on `journalctl`.

- [ ] **Step 13: CONTROLLER — check both banners against the real system**

Codex cannot do any of this: it needs the journal, the vendor GUI, and a display.

First, the health banner in its healthy state:

```bash
./.venv/bin/python -m lianli_panel.cli status
./.venv/bin/python -m lianli_panel.gui.app
```

Expected: `status` reports healthy, and no health banner appears. Time the check while you are here — `health.check()` reads the whole boot's journal for the unit, so on a long uptime it may be slow:

```bash
time ./.venv/bin/python -c "from lianli_panel import health; print(health.check().ok)"
```

Record the number. Under ~2 s needs nothing; several seconds means the 60 s poll is doing real work each time and `journalctl -g` is worth trying in a follow-up. It runs on a worker thread either way, so this is an observation, not a blocker.

Then the vendor-GUI banner. **Take a snapshot first** — this step deliberately runs the program that wipes `config.lcds`:

```bash
./.venv/bin/python -m lianli_panel.cli snapshot
./.venv/bin/python -m lianli_panel.cli list          # record the set hash
```

With the app open, start the vendor GUI, and confirm the process name this app matches on is the real one:

```bash
lianli-gui &
./.venv/bin/python -c "from lianli_panel import health; print(health.vendor_gui_pids())"
```

Expected: a non-empty list, and the amber banner appears in the app within 60 s (or immediately on **Re-check panel**). **If the list is empty, `VENDOR_GUI_NAMES` is wrong** — read the real name and fix the constant, do not adjust the test to match a guess:

```bash
pgrep -a -f lianli-gui
cat /proc/$(pgrep -f lianli-gui | head -1)/comm
readlink /proc/$(pgrep -f lianli-gui | head -1)/exe
```

Then close the vendor GUI and press **Re-check config.lcds** in the banner:

```bash
./.venv/bin/python -m lianli_panel.cli list
```

Record whether the array survived. If it was wiped, the banner should say `EMPTY` and an Apply from the GUI restores the entry from the snapshot — verify that it does, and that the panel comes back. This is the interlock's whole purpose, so a wipe here is a successful test, not a setback.

- [ ] **Step 14: Commit**

```bash
git add lianli_panel/health.py lianli_panel/gui/status.py lianli_panel/gui/window.py \
        tests/test_health.py tests/test_gui_status.py tests/test_gui_smoke.py
git commit -m "feat: warn when the panel handle is dead or lianli-gui is running

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

> **HANDOFF POINT.** Ten tasks done. Update the SDD ledger and end the session;
> Task 11 is controller-and-Chase work from a fresh session.

---

### Task 11: Packaging, the desktop entry, and the end-to-end check

**Every step here is controller or Chase work.** Nothing in it can be delegated: it installs into the venv, writes outside the repo, launches a GUI from the desktop, and ends with a person looking at a physical screen. Per the standing preference, that last part is the completion criterion — the green suite is not.

**Files:**

- Create: `tools/lianli-panel.desktop`, `docs/gui.md`
- Modify: `pyproject.toml`, `lianli_panel/gui/app.py`
- Test: none new. The evidence is the panel.

**Interfaces:**

- Consumes: `gui.app.main`.
- Produces: the `lianli-panel-gui` console script and a desktop entry pointing at it.

- [ ] **Step 1: Add the GUI entry point and pin package discovery**

In `pyproject.toml`, add:

```toml
[project.gui-scripts]
lianli-panel-gui = "lianli_panel.gui.app:main"

# PySide6 is DELIBERATELY not a dependency. It is the system RPM
# python3-pyside6, visible through this venv's --system-site-packages; listing
# it here would make `pip install` try to fetch a wheel that does not need to
# exist, in an environment that is offline by design.
[tool.setuptools]
packages = ["lianli_panel", "lianli_panel.gui"]
```

`[tool.setuptools] packages` is explicit rather than left to flat-layout auto-discovery: `lianli_panel.gui` is a new subpackage, and discovery silently shipping the wrong set is the kind of failure that only appears at install time.

- [ ] **Step 2: Name the application so the desktop entry can find its window**

In `lianli_panel/gui/app.py`, inside `main`, immediately after `app = QApplication(...)`:

```python
    app.setApplicationName("lianli-panel")
    app.setApplicationDisplayName("Lian Li Panel Editor")
    app.setDesktopFileName("lianli-panel")     # matches lianli-panel.desktop
```

Without `setDesktopFileName` the window is not associated with its `.desktop` file under Wayland, so it shows a generic icon and does not group with its launcher.

- [ ] **Step 3: Reinstall editable and verify both scripts resolve**

```bash
./.venv/bin/pip install -e . --no-build-isolation
ls .venv/bin/lianli-panel .venv/bin/lianli-panel-gui
./.venv/bin/python -c "import lianli_panel.gui.app as a; print(a.main)"
```

Expected: both scripts exist and the import resolves. `--no-build-isolation` uses the setuptools already present instead of downloading a build environment; drop it if the network is available and it complains.

- [ ] **Step 4: Launch through the console script**

```bash
./.venv/bin/lianli-panel-gui
```

Expected: the same window as `python -m lianli_panel.gui.app`. If it starts but cannot import PySide6, the venv lost `--system-site-packages` — check `.venv/pyvenv.cfg` rather than trying to pip install Qt.

- [ ] **Step 5: Write the desktop entry**

`tools/lianli-panel.desktop`:

```ini
[Desktop Entry]
Type=Application
Version=1.5
Name=Lian Li Panel Editor
GenericName=LCD template editor
Comment=Edit the Universal Screen 8.8" template library and apply it to the panel
Exec=/home/chase/Documents/Code/lianli-panel/.venv/bin/lianli-panel-gui
Icon=video-display
Terminal=false
Categories=Utility;Settings;HardwareSettings;
Keywords=lianli;lcd;panel;rgb;
StartupNotify=true
StartupWMClass=lianli-panel
```

The `Exec` path is absolute and points into the venv on purpose: the desktop session's `PATH` does not contain it, and the system `python3` has PySide6 but not `lianli_panel`.

It lives in `tools/` rather than a new top-level directory because setuptools' flat-layout discovery already excludes `tools/`, and adding a top-level `packaging/` would put a fresh directory in front of it.

- [ ] **Step 6: Install and validate the entry**

```bash
install -Dm644 tools/lianli-panel.desktop ~/.local/share/applications/lianli-panel.desktop
desktop-file-validate ~/.local/share/applications/lianli-panel.desktop && echo VALID
update-desktop-database ~/.local/share/applications 2>/dev/null || true
```

Expected: `VALID` with no warnings. `desktop-file-validate` may be absent; if so, skip it and rely on the launch in the next step.

Note the vendor's own entry is `com.sgtaziz.lianlilinux.desktop` — a different file, so there is no collision, but both will now appear in the menu. They are different programs and one of them wipes `config.lcds`; that is worth knowing when picking from the launcher.

- [ ] **Step 7: Launch it from the desktop, not the terminal**

Open the application launcher, search for "Lian Li Panel Editor", and start it.

Confirm and record:

1. It launches with no terminal.
2. The window title bar and taskbar entry show the right name and icon (this is what `StartupWMClass` and `setDesktopFileName` are for).
3. The template list and the render appear, exactly as from the terminal.

A launch that works from the terminal but not from the menu is almost always the `Exec` path or a missing environment variable — run `gtk-launch lianli-panel` (or check `journalctl --user -b -n 50`) to see the actual error rather than guessing.

- [ ] **Step 8: Write the GUI documentation**

`docs/gui.md`:

```markdown
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
```

- [ ] **Step 9: CHASE — the end-to-end acceptance run**

This is the completion criterion for the whole plan. Do it at the machine, with the panel visible.

Record the starting state:

```bash
./.venv/bin/python -m lianli_panel.cli status
./.venv/bin/python -m lianli_panel.cli list          # record every id and the hash
./.venv/bin/python -m lianli_panel.cli snapshot      # record the path
```

Then, from the **application launcher**:

1. The dash renders on the canvas, and no banner is showing.
2. Select a widget with an unmistakable position — the clock, or the FPS label. Drag it ~40 px. The canvas re-renders.
3. Press **Apply**. **Look at the panel.** The widget has moved there too.
4. Press **Refresh (runs sensors)** once. The canvas shows live values, not the constants the automatic path substitutes.
5. Duplicate `gaming-dash`, rename the copy, make it live, Apply. **Look at the panel**: it is showing the copy.
6. Press **Revert**, confirm. **Look at the panel**: the original dash is back.
7. Make one edit and try to close the window. The unapplied-changes prompt appears.

Then confirm the library survived:

```bash
./.venv/bin/python -m lianli_panel.cli list
```

Every template that existed at the start is still listed. **A shrunken list is the most damaging failure this app can have** and means the whole-set write is broken — stop and restore from the snapshot recorded above.

If the panel does not change but the CLI reports a new hash, that is the dead-encoder failure rather than an apply bug:

```bash
sudo systemctl restart lianli-daemon-system.service
```

then re-apply RGB and retry. The health banner should have been showing in that case; note whether it was, because that is the one thing about it that cannot be tested any other way.

- [ ] **Step 10: Confirm the automatic path still spawns no sensors**

An hour of editing with the app open is the real test of the `live=False` substitution — 31 widgets at two executions each, several times a second, would be visible in the journal and in load:

```bash
sudo journalctl -u lianli-daemon-system.service --since "30 minutes ago" \
  | grep -ci "nvidia-smi\|fps.sh\|graph.sh" || true
```

Expected: `0`, apart from anything the explicit **Refresh** presses caused. A steady stream means the debounced path is executing commands and the panel's own sparkline data is being corrupted by the editor.

- [ ] **Step 11: Commit**

```bash
git add pyproject.toml lianli_panel/gui/app.py tools/lianli-panel.desktop docs/gui.md
git commit -m "feat: package the editor as a desktop application

Claude-Session: https://claude.ai/code/session_01E7x3W6jPsPGeU1rU4VkD7r"
```

- [ ] **Step 12: Close the plan out**

Write the SDD ledger entry for Task 11 and record, in it, three things Plan B needs:

1. Whether `health.check()` was slow enough to be worth optimising (step 13 of Task 10 measured it).
2. The real vendor-GUI process name, if `VENDOR_GUI_NAMES` had to change.
3. Whether the vendor GUI actually wiped `config.lcds` during Task 10's interlock test, and whether an Apply restored it.

---

## What Plan B covers

Fixed here so nothing from the spec falls between the two plans. Plan A built
the editing half; everything below is in the spec and is deliberately not in
Plan A.

**Sensor editor.** Create a named sensor of any of the 14 source types, and the
two-tier test harness for `command` sources:

- *Authoritative* — a throwaway one-widget template whose `value_text` source is
  the candidate, sent to `RenderTemplatePreview`. The daemon runs it as uid
  `lianli`, under exactly the conditions the real sensor faces, and returns the
  number as an image. No privileges, no new daemon method.
- *Diagnostic* — the same command run as `chase`, capturing raw stdout, stderr
  and exit status, which the image cannot show. **Labelled as not
  authoritative**: it runs as the wrong uid and succeeds on `$HOME` paths the
  daemon cannot traverse.

It checks exit status, that the first whitespace token parses as `f32`, and that
the command and any script it references are readable by `lianli` — reporting an
unreadable path by name, since `/home/chase` is mode 0700 and the daemon cannot
enter it. It warns before running a command with side effects, and never puts a
candidate on the debounced preview path. It shows raw stdout beside the parsed
value, because a tool that prints its errors to stdout (`nvidia-smi` rejecting
`-lms2000` does exactly this) is otherwise swallowed silently by a
parse-each-line-as-data loop. GUI-authored scripts are written to
`/var/lib/lianli-panel/`, which is why that directory is user-owned.

**LED ring.** Off / Static / thermal-sweep, driving
`lianli-thermal-rgb.service`. Colour picking for static; hue endpoints and
temperature bounds for the sweep. Static and Off conflict with the poller, which
overwrites the ring within ~2 s, so selecting either stops the unit first **and
says so**; selecting thermal-sweep starts it again. The ring's device id is
resolved at runtime from `ListDevices` (first device with `has_rgb` and
`pid == 0x8050`) because it is USB-path-derived and changes on every replug into
a different port — unlike the LCD, whose serial is stable. There is no colour
read-back (`GetZoneColors` fails with `zone 0 not found`), so the UI shows the
last value it set and says that is what it is showing.

**Thermal poller configuration.** `COOL_C`, `HOT_C`, `POLL_MS`, `MIN_DELTA_C`,
`FORCE_REFRESH_S` and `BRIGHTNESS` are module-level constants today. They move
to `/var/lib/lianli-panel/thermal-rgb.json`, which the GUI writes and the poller
re-reads when its mtime changes — it already wakes every 2 s on the `nvidia-smi`
stream, so the check is free, edits apply live, no unit restart is needed, and
the GUI needs no privileges it does not already have. The constants become the
defaults used when the file is absent, so the poller runs unchanged if the GUI
is never launched.

**Brightness.** `SetLcdBrightness`.

**Also carried forward:** surfacing `model.validate`'s *warnings* (Plan A's
apply gate only blocks on `level == "error"`), and retiring
`build_template.py` to a documented seed script now that templates are editable
in the GUI.

**Out of scope in both plans**, from the spec's own follow-ups:

- Auditing whether existing `command` sensors have native equivalents among the
  14 source types (`hwmon` for CPU temp in particular), to remove per-second
  subprocess spawns.
- Any colour read-back path for the ring. `GetZoneColors` does not work on this
  device; the app shows the last value it set and says so.
