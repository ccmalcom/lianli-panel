# lianli-panel Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the headless core of lianli-panel — a correct, tested Python library and CLI for driving the Lian Li Universal Screen 8.8" over the daemon's IPC socket.

**Architecture:** Four layers, none of which import Qt: `ipc` (socket transport), `model` (template dataclasses, unit conversion, validation), `render` (preview client with command-source substitution), and a set of task modules (`apply`, `health`, `snapshot`, `sensors`, `ring`). The GUI in the follow-up plan consumes these unchanged. This plan is independently useful: on completion it correctly replaces `apply.sh` and `rgb.sh`, both of which have known bugs.

**Tech Stack:** Python 3.14.7, stdlib `socket`/`json`/`subprocess`, Pillow 12.3.0 (system RPM), pytest 9.1.1 (venv). No network at runtime. No third-party runtime dependencies.

**Spec:** `docs/superpowers/specs/2026-09-04-lianli-panel-gui-design.md` — read it before starting. This plan argues from it and does not restate its reasoning.

## Global Constraints

Every task's requirements implicitly include this section.

- **Python 3.14.7.** The venv MUST be created with `--system-site-packages`; PySide6 and Pillow are system RPMs and are not pip-installable into a clean venv here.
- **`SensorRange.max` is a percentage of the widget's own `value_min..value_max` span**, not a raw reading. Confirmed by disassembly: clamp to `[0,1]`, multiply by `100.0`, first range with `max >= percentage` wins, null `max` is the fallback.
- **Widget `x`/`y` are the widget's CENTRE**, not top-left. Confirmed: `left = scaled_x - rendered_width / 2`.
- **Templates are authored at 1920×480**, landscape. The panel is physically 480×1920 and the daemon rotates.
- **`SetLcdTemplates` replaces the ENTIRE stored template set.** Always send the whole library.
- **`SetLcdTemplates` alone does not update the panel.** Always follow with `SetLcdMedia`.
- **`RenderTemplatePreview` requires `template`, `width` AND `height`.** Omitting a dimension fails with ``missing field `width` ``.
- **`RenderTemplatePreview` executes `command` sources** — twice per widget per render, as uid `lianli`. Never render a command-bearing template on an automatic/debounced path.
- **The daemon runs as uid `lianli` (971) and cannot traverse `/home/chase` (mode 0700).** Anything it must read lives outside `$HOME`.
- **The daemon SILENTLY IGNORES unknown JSON fields.** A misspelled field name is dropped, not rejected. Never rely on the daemon to catch a field-name mistake.
- **Tests MUST NOT call mutating daemon methods.** No `Set*`, `Save*`, `Delete*`, `Apply*`, `Install*`, `Bind*`, `Unbind*`, `Reboot*`, `SwitchDisplayMode` against the live socket. `RenderTemplatePreview`, `Get*` and `List*` are safe. Tests that need mutation use the `FakeClient` from Task 1.
- **Never read, print, or store values from any `.env` file.**
- **Commit messages:** plain, no `Co-Authored-By` trailer. End each with:
  `Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne`

---

### Task 1: Repo scaffold and IPC client

**Files:**
- Create: `pyproject.toml`, `lianli_panel/__init__.py`, `lianli_panel/ipc.py`, `tests/conftest.py`
- Test: `tests/test_ipc.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `lianli_panel.ipc.Client(sock_path: str = DEFAULT_SOCK, timeout: float = 30.0)` with `call(method: str, params: dict | None = None) -> Any` returning the `data` payload; exceptions `DaemonError(message: str)`, `DaemonDown(DaemonError)`, `DaemonRefused(DaemonError)`. Constant `DEFAULT_SOCK = "/run/lianli/lianli-daemon.sock"`. Test double `tests.conftest.FakeClient` with `.responses: dict[str, Any]`, `.calls: list[tuple[str, dict]]`, and the same `call()` signature.

- [ ] **Step 1: Create the venv and project skeleton**

```bash
cd ~/Documents/Code/lianli-panel
python3 -m venv --system-site-packages .venv
./.venv/bin/pip install -q pytest
./.venv/bin/python -c "import PySide6, PIL, pytest; print('ok')"
mkdir -p lianli_panel tests
touch lianli_panel/__init__.py
```

Expected final line: `ok`

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[project]
name = "lianli-panel"
version = "0.1.0"
requires-python = ">=3.13"

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 3: Write the failing test**

`tests/test_ipc.py`:

```python
import json
import socket
import threading

import pytest

from lianli_panel.ipc import Client, DaemonError, DaemonDown


def _serve(path, payload, captured):
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)

    def run():
        conn, _ = srv.accept()
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
        captured.append(json.loads(buf.decode()))
        conn.sendall(json.dumps(payload).encode())
        conn.close()
        srv.close()

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_call_returns_data_payload(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    t = _serve(path, {"status": "ok", "data": [1, 2, 3]}, captured)
    assert Client(path).call("ListDevices") == [1, 2, 3]
    t.join(timeout=5)
    assert captured[0] == {"method": "ListDevices"}


def test_params_are_sent_when_given(tmp_path):
    path = str(tmp_path / "s.sock")
    captured = []
    t = _serve(path, {"status": "ok", "data": None}, captured)
    Client(path).call("Ping", {"a": 1})
    t.join(timeout=5)
    assert captured[0] == {"method": "Ping", "params": {"a": 1}}


def test_error_status_raises_with_message(tmp_path):
    path = str(tmp_path / "s.sock")
    t = _serve(path, {"status": "error", "message": "nope"}, [])
    with pytest.raises(DaemonError, match="nope"):
        Client(path).call("Bad")
    t.join(timeout=5)


def test_missing_socket_raises_daemon_down(tmp_path):
    with pytest.raises(DaemonDown):
        Client(str(tmp_path / "absent.sock")).call("Ping")
```

- [ ] **Step 4: Run the test to verify it fails**

Run: `./.venv/bin/pytest tests/test_ipc.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.ipc'`

- [ ] **Step 5: Implement `lianli_panel/ipc.py`**

```python
"""Transport for the lianli daemon's newline-delimited JSON IPC socket.

One call per connection: send a single JSON line, half-close, read until EOF.
"""
from __future__ import annotations

import errno
import json
import socket
from typing import Any

DEFAULT_SOCK = "/run/lianli/lianli-daemon.sock"


class DaemonError(Exception):
    """The daemon answered, and the answer was an error."""


class DaemonDown(DaemonError):
    """The socket is absent — the daemon is not running."""


class DaemonRefused(DaemonError):
    """The socket exists but will not accept a connection from this process."""


class Client:
    def __init__(self, sock_path: str = DEFAULT_SOCK, timeout: float = 30.0) -> None:
        self.sock_path = sock_path
        self.timeout = timeout

    def call(self, method: str, params: dict | None = None) -> Any:
        req: dict[str, Any] = {"method": method}
        if params is not None:
            req["params"] = params
        raw = self._roundtrip(json.dumps(req) + "\n")
        try:
            reply = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DaemonError(f"unparseable reply to {method}: {raw[:200]!r}") from exc
        if reply.get("status") != "ok":
            raise DaemonError(reply.get("message", f"{method} failed"))
        return reply.get("data")

    def _roundtrip(self, payload: str) -> str:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(self.sock_path)
        except FileNotFoundError as exc:
            raise DaemonDown(f"no socket at {self.sock_path}") from exc
        except ConnectionRefusedError as exc:
            raise DaemonDown(f"socket at {self.sock_path} refused") from exc
        except PermissionError as exc:
            # Seen inside sandboxes: mode 0666 but connect() still returns EPERM.
            raise DaemonRefused(f"not permitted to connect to {self.sock_path}") from exc
        except OSError as exc:
            if exc.errno in (errno.ENOENT, errno.ECONNREFUSED):
                raise DaemonDown(str(exc)) from exc
            raise
        try:
            s.sendall(payload.encode())
            s.shutdown(socket.SHUT_WR)
            chunks = []
            while True:
                chunk = s.recv(1 << 16)
                if not chunk:
                    break
                chunks.append(chunk)
            return b"".join(chunks).decode()
        finally:
            s.close()
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `./.venv/bin/pytest tests/test_ipc.py -v`
Expected: PASS, 4 passed

- [ ] **Step 7: Add the shared test double**

`tests/conftest.py`:

```python
import pytest


class FakeClient:
    """Stands in for ipc.Client. Records calls; returns canned data.

    Used everywhere a test would otherwise mutate the real device.
    """

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        value = self.responses.get(method)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(params or {})
        return value

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def fake_client():
    return FakeClient()
```

- [ ] **Step 8: Verify against the real daemon (read-only)**

Run:
```bash
./.venv/bin/python -c "
from lianli_panel.ipc import Client
c = Client()
print('devices:', [d['name'] for d in c.call('ListDevices')])
print('templates:', [t['id'] for t in c.call('GetLcdTemplates')])
"
```
Expected: lists the two devices (`Universal Screen 8.8"`, `... LED Ring`) and at least `gaming-dash`. If this raises `DaemonDown`, stop — the daemon is not running and later tasks cannot be verified.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml lianli_panel tests
git commit -F - <<'MSG'
feat: add IPC client for the lianli daemon socket

One call per connection: send a JSON line, half-close, read to EOF.
Unwraps the status envelope and raises DaemonError on status != ok.

Distinguishes DaemonDown (no socket) from DaemonRefused (socket present
but connect() returns EPERM, which is what a sandbox does even though the
socket is mode 0666).

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 2: Schema extraction tool

The inspector forms in the follow-up plan need every variant's fields. `gaming-dash` exercises only 7 of 12 widget kinds and 5 of 14 source types, so it cannot be the source of truth. This task builds a tool that asks the daemon directly.

**Method and its limit:** the daemon reports a missing required field as ``missing field `x` ``, so adding placeholders in a loop discovers **all required fields**. It **silently ignores unknown fields**, so optional fields cannot be discovered this way — those come from observed templates and from string literals in the binary, and are marked as non-exhaustive.

**Files:**
- Create: `tools/extract_schema.py`, `lianli_panel/schema.py` (generated, committed)
- Test: `tests/test_schema.py`

**Interfaces:**
- Consumes: `lianli_panel.ipc.Client`.
- Produces: `lianli_panel.schema.WIDGET_KINDS: dict[str, VariantSpec]`, `SOURCE_TYPES: dict[str, VariantSpec]`, where `VariantSpec` is a dataclass with `name: str`, `required: tuple[str, ...]`, `observed_optional: tuple[str, ...]`. Also `lianli_panel.schema.KIND_NAMES: tuple[str, ...]` and `SOURCE_NAMES: tuple[str, ...]`.

- [ ] **Step 1: Write the failing test**

`tests/test_schema.py`:

```python
from lianli_panel import schema


def test_all_twelve_widget_kinds_present():
    assert set(schema.KIND_NAMES) == {
        "label", "value_text", "radial_gauge", "vertical_bar", "horizontal_bar",
        "speedometer", "core_bars", "image", "video", "sparkline",
        "clock_digital", "clock_analog",
    }


def test_all_fourteen_source_types_present():
    assert set(schema.SOURCE_NAMES) == {
        "constant", "command", "hwmon", "nvidia_gpu", "amd_gpu_usage",
        "wireless_coolant", "cpu_usage", "mem_usage", "mem_used", "mem_free",
        "network_rx", "network_tx", "disk_read", "disk_write",
    }


def test_constant_source_requires_value():
    assert schema.SOURCE_TYPES["constant"].required == ("value",)


def test_radial_gauge_requires_span_and_ranges():
    req = set(schema.WIDGET_KINDS["radial_gauge"].required)
    assert {"source", "value_min", "value_max"} <= req


def test_every_variant_spec_is_populated():
    for name, spec in schema.WIDGET_KINDS.items():
        assert spec.name == name


def test_no_variant_extracted_an_empty_required_list():
    """Guards the extractor's silent-partial failure mode: a stall returns a
    SHORT field tuple, and asserting only on names or on the 12/14 counts would
    pass with every list empty.

    Every widget kind draws something and every source produces a value, so no
    variant here legitimately has zero required fields. If a future daemon adds
    a genuinely field-less variant, exempt it BY NAME rather than weakening
    this to a >= 0 check."""
    empty = [n for n, s in {**schema.WIDGET_KINDS, **schema.SOURCE_TYPES}.items()
             if not s.required]
    assert empty == [], f"extraction stalled for: {empty}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_schema.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.schema'`

- [ ] **Step 3: Write the extractor**

`tools/extract_schema.py`:

```python
#!/usr/bin/env python3
"""Extract the daemon's template schema by probing its serde error messages.

Two mechanisms, with different completeness guarantees:

  Required fields  -- authoritative. Send a variant with no fields and read
                      `missing field \\`x\\``; add a placeholder for x; repeat
                      until the render succeeds. The loop terminates because
                      each pass fixes exactly one field.

  Optional fields  -- NOT exhaustive. The daemon ignores unknown fields, so
                      there is no way to ask "what else may I send?". These are
                      harvested from templates already stored on the daemon and
                      labelled as observed, not complete.

Run:  ./.venv/bin/python tools/extract_schema.py > lianli_panel/schema.py
"""
from __future__ import annotations

import json
import re
import sys

from lianli_panel.ipc import Client, DaemonError

KINDS = ("label", "value_text", "radial_gauge", "vertical_bar", "horizontal_bar",
         "speedometer", "core_bars", "image", "video", "sparkline",
         "clock_digital", "clock_analog")
SOURCES = ("constant", "command", "hwmon", "nvidia_gpu", "amd_gpu_usage",
           "wireless_coolant", "cpu_usage", "mem_usage", "mem_used", "mem_free",
           "network_rx", "network_tx", "disk_read", "disk_write")

FONT = "/usr/share/fonts/google-noto/NotoSansMono-Bold.ttf"
MISSING = re.compile(r"missing field `([^`]+)`")

# Placeholders by field name. The daemon TYPE-CHECKS, so a wrong type produces
# "invalid type" rather than "missing field" -- which the loop cannot act on, so
# it stalls and returns a PARTIAL field list. That failure is silent unless the
# extractor exits nonzero, which is why it does.
#
# The bare-float default is only safe for genuinely numeric fields. Anything
# taking a string, bool, integer or colour array needs an entry here. The list
# below covers every non-float required field found in the daemon's serde
# variants; extend it rather than letting the default absorb a new one.
PLACEHOLDERS = {
    # nested objects
    "source": {"type": "constant", "value": 1.0},
    "font": {"path": FONT},
    "ranges": [{"max": None, "color": [255, 255, 255], "alpha": 255}],
    # colour arrays
    "color": [255, 255, 255, 255],
    "background_color": [0, 0, 0, 0],
    "gauge_background_color": [60, 60, 60],
    "line_color": [255, 255, 255],
    "fill_color": [255, 255, 255, 80],
    "border_color": [255, 255, 255],
    "needle_color": [255, 0, 0],
    "needle_border_color": [0, 0, 0],
    "tick_color": [200, 200, 200],
    "face_color": [20, 20, 20],
    "hour_hand_color": [255, 255, 255],
    "minute_hand_color": [255, 255, 255],
    "second_hand_color": [255, 0, 0],
    "colors": [[255, 255, 255]],
    # strings
    "text": "x",
    "format": "{:.0}",
    "unit": "",
    "align": "center",
    "path": FONT,
    "cmd": "echo 1",
    "name": "coretemp",
    "label": "x",
    "metric": "temp",            # nvidia_gpu enum
    "iface": "lo",               # network_rx / network_tx
    "device": "sda",             # disk_read / disk_write
    "device_id": "hid:probe",    # wireless_coolant
    "fit": "contain",            # image / video enum
    # integers
    "gpu_index": 0,
    "card_index": 0,
    "tick_count": 8,
    "history_length": 60,
    # booleans
    "loop_playback": False,
    "show_labels": False,
    "auto_range": False,
    "show_gauge": True,
    "show_needle": True,
    "show_seconds": True,
    # floats
    "value": 1.0,
}
NUMERIC_DEFAULT = 1.0


def envelope(widget_kind: dict) -> dict:
    return {
        "id": "probe", "name": "probe",
        "base_width": 1920, "base_height": 480, "rotated": True,
        "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "widgets": [{"id": "w", "x": 100.0, "y": 100.0,
                     "width": 100.0, "height": 100.0, "kind": widget_kind}],
    }


STALLED: list[str] = []


def required_fields(client: Client, build, label: str) -> tuple[str, ...]:
    """Add placeholders until the template validates. Returns fields in order.

    A stall means a placeholder had the WRONG TYPE: the daemon answered
    "invalid type" instead of "missing field", which the loop cannot act on, so
    the field list here is partial. Recorded so main() can exit nonzero -- a
    partial schema that looks successful is worse than no schema.
    """
    found: list[str] = []
    for _ in range(60):
        try:
            client.call("RenderTemplatePreview",
                        {"template": envelope(build(found)), "width": 1920, "height": 480})
            return tuple(found)
        except DaemonError as exc:
            m = MISSING.search(str(exc))
            if not m:
                STALLED.append(f"{label}: {exc} (found so far: {found})")
                print(f"  STALLED {label}: {exc}", file=sys.stderr)
                return tuple(found)
            field = m.group(1)
            if field in found:
                STALLED.append(f"{label}: repeated {field} — placeholder rejected")
                print(f"  STALLED {label}: repeated {field}: {exc}", file=sys.stderr)
                return tuple(found)
            found.append(field)
    STALLED.append(f"{label}: exceeded 60 iterations")
    return tuple(found)


def fill(kind_type: str, fields: list[str]) -> dict:
    out: dict = {"type": kind_type}
    for f in fields:
        out[f] = PLACEHOLDERS.get(f, NUMERIC_DEFAULT)
    return out


def observed_optional(client: Client) -> tuple[dict, dict]:
    """Harvest field names actually present on stored templates."""
    kinds: dict[str, set] = {}
    sources: dict[str, set] = {}
    for tpl in client.call("GetLcdTemplates") or []:
        for w in tpl.get("widgets", []):
            k = w.get("kind") or {}
            if isinstance(k, dict) and "type" in k:
                kinds.setdefault(k["type"], set()).update(x for x in k if x != "type")
                s = k.get("source")
                if isinstance(s, dict) and "type" in s:
                    sources.setdefault(s["type"], set()).update(x for x in s if x != "type")
    return kinds, sources


def main() -> None:
    client = Client()
    seen_kinds, seen_sources = observed_optional(client)

    kind_specs = {}
    for kind in KINDS:
        print(f"probing kind {kind}", file=sys.stderr)
        req = required_fields(client, lambda fs, k=kind: fill(k, fs), f"kind {kind}")
        opt = tuple(sorted(seen_kinds.get(kind, set()) - set(req)))
        kind_specs[kind] = (req, opt)

    src_specs = {}
    for src in SOURCES:
        print(f"probing source {src}", file=sys.stderr)

        def build(fs, s=src):
            source = {"type": s}
            for f in fs:
                source[f] = PLACEHOLDERS.get(f, NUMERIC_DEFAULT)
            return {"type": "value_text", "source": source, "format": "{:.0}",
                    "unit": "", "font": {"path": FONT}, "font_size": 20.0,
                    "color": [255, 255, 255, 255], "align": "center",
                    "value_min": 0.0, "value_max": 100.0,
                    "ranges": [{"max": None, "color": [255, 255, 255], "alpha": 255}],
                    "letter_spacing": 0.0}

        # The outer value_text fields are already satisfied, so any reported
        # missing field belongs to the source being probed.
        req = required_fields(client, build, f"source {src}")
        opt = tuple(sorted(seen_sources.get(src, set()) - set(req)))
        src_specs[src] = (req, opt)

    if STALLED:
        print(f"\n{len(STALLED)} variant(s) STALLED — the schema is PARTIAL:",
              file=sys.stderr)
        for s in STALLED:
            print(f"  {s}", file=sys.stderr)
        print("\nAdd a correctly-typed entry to PLACEHOLDERS for each field named "
              "above and re-run. Do NOT commit a partial schema.", file=sys.stderr)
        raise SystemExit(1)

    emit(kind_specs, src_specs)


def emit(kind_specs: dict, src_specs: dict) -> None:
    def block(specs: dict) -> str:
        lines = []
        for name, (req, opt) in specs.items():
            lines.append(f"    {name!r}: VariantSpec({name!r}, {req!r}, {opt!r}),")
        return "\n".join(lines)

    print('"""Daemon template schema. GENERATED by tools/extract_schema.py.')
    print()
    print("Required fields are authoritative (serde reports them).")
    print("observed_optional is NOT exhaustive: the daemon silently ignores")
    print("unknown fields, so there is no way to enumerate optional ones.")
    print('Regenerate after a daemon upgrade; do not hand-edit.')
    print('"""')
    print("from __future__ import annotations")
    print()
    print("from dataclasses import dataclass")
    print()
    print()
    print("@dataclass(frozen=True)")
    print("class VariantSpec:")
    print("    name: str")
    print("    required: tuple[str, ...]")
    print("    observed_optional: tuple[str, ...]")
    print()
    print()
    print("WIDGET_KINDS: dict[str, VariantSpec] = {")
    print(block(kind_specs))
    print("}")
    print()
    print("SOURCE_TYPES: dict[str, VariantSpec] = {")
    print(block(src_specs))
    print("}")
    print()
    print("KIND_NAMES = tuple(WIDGET_KINDS)")
    print("SOURCE_NAMES = tuple(SOURCE_TYPES)")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Generate the schema module**

Run:
```bash
./.venv/bin/python tools/extract_schema.py > /tmp/schema.py
echo "extractor exit: $?"
```

The extractor **exits 1 if any variant stalled**, so a nonzero exit means the
schema is partial — read the `STALLED` lines on stderr, add a correctly-typed
entry to `PLACEHOLDERS` for each field named, and re-run. Do not continue with a
partial schema, and do not redirect straight over `lianli_panel/schema.py`: a
failed run would truncate it to a stub that still imports.

Only once it exits 0:

```bash
mv /tmp/schema.py lianli_panel/schema.py
./.venv/bin/python -c "
from lianli_panel import schema
print(len(schema.KIND_NAMES), len(schema.SOURCE_NAMES))
for n, s in {**schema.WIDGET_KINDS, **schema.SOURCE_TYPES}.items():
    print(f'{n:20} required={s.required}')
"
```
Expected: `12 14`, then a line per variant with a **non-empty** `required` tuple.
Counting 12 and 14 proves only that the loop ran, not that extraction succeeded —
a stalled variant is still counted. Read the tuples.

- [ ] **Step 5: Run the test to verify it passes**

Run: `./.venv/bin/pytest tests/test_schema.py -v`
Expected: PASS, 6 passed

- [ ] **Step 6: Commit**

```bash
git add tools/extract_schema.py lianli_panel/schema.py tests/test_schema.py
git commit -F - <<'MSG'
feat: extract daemon template schema by probing serde errors

The stored gaming-dash template covers only 7 of 12 widget kinds and 5 of
14 source types, so it cannot drive the inspector forms.

Required fields are discovered authoritatively: send a variant with no
fields, read "missing field `x`", add a placeholder, repeat.

Optional fields cannot be discovered the same way -- the daemon silently
ignores unknown fields rather than listing what it accepts -- so those are
harvested from stored templates and labelled non-exhaustive.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 3: Template model with lossless round-trip

**Files:**
- Create: `lianli_panel/model.py`
- Test: `tests/test_model_roundtrip.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `lianli_panel.model.Template` and `Widget` dataclasses. `Template.from_json(obj: dict) -> Template`, `Template.to_json() -> dict`, `Template.widget(widget_id: str) -> Widget | None`. `Widget` fields: `id: str`, `x: float`, `y: float`, `width: float`, `height: float`, `kind: dict`, `extra: dict`. `Widget.kind_type -> str` property. Module constant `BASE_W, BASE_H = 1920, 480`.

**Why lossless matters:** the daemon ignores unknown fields, so anything this model drops on load is silently deleted on the next save. Preservation must be recursive, not just at widget level.

- [ ] **Step 1: Write the failing test**

`tests/test_model_roundtrip.py`:

```python
import json
from pathlib import Path

import pytest

from lianli_panel.model import Template, Widget

REAL = Path("/var/tmp/lianli-stats/gaming-dash.json")


def test_roundtrip_preserves_real_template_exactly():
    if not REAL.exists():
        pytest.skip("gaming-dash.json not present")
    original = json.loads(REAL.read_text())
    assert Template.from_json(original).to_json() == original


def test_unknown_fields_survive_at_every_level():
    src = {
        "id": "t", "name": "T", "base_width": 1920, "base_height": 480,
        "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "zz_top": "keep me",
        "widgets": [{
            "id": "w", "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0,
            "zz_widget": "keep me too",
            "kind": {"type": "value_text", "zz_kind": "and me",
                     "source": {"type": "constant", "value": 1.0, "zz_src": "me as well"}},
        }],
    }
    assert Template.from_json(src).to_json() == src


def test_widget_lookup_and_kind_type():
    src = {
        "id": "t", "name": "T", "base_width": 1920, "base_height": 480,
        "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "widgets": [{"id": "w1", "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0,
                     "kind": {"type": "radial_gauge"}}],
    }
    tpl = Template.from_json(src)
    assert tpl.widget("w1").kind_type == "radial_gauge"
    assert tpl.widget("absent") is None


def test_widget_order_is_draw_order_and_is_preserved():
    src = {
        "id": "t", "name": "T", "base_width": 1920, "base_height": 480,
        "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
        "widgets": [
            {"id": f"w{i}", "x": 1.0, "y": 2.0, "width": 3.0, "height": 4.0,
             "kind": {"type": "label"}} for i in range(5)
        ],
    }
    tpl = Template.from_json(src)
    assert [w.id for w in tpl.widgets] == ["w0", "w1", "w2", "w3", "w4"]
    assert [w["id"] for w in tpl.to_json()["widgets"]] == ["w0", "w1", "w2", "w3", "w4"]
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_model_roundtrip.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.model'`

- [ ] **Step 3: Implement the dataclasses**

`lianli_panel/model.py`:

```python
"""Template model.

Round-trip fidelity is a correctness requirement, not a nicety: the daemon
silently ignores fields it does not recognise, so any key this model drops on
load is permanently deleted on the next save with no error anywhere.

Widget order IS draw order. Only the last widget covering a rect is visible,
which is how the cover-bar visibility trick works. Never reorder implicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BASE_W, BASE_H = 1920, 480

_WIDGET_KEYS = ("id", "x", "y", "width", "height", "kind")
_TEMPLATE_KEYS = ("id", "name", "base_width", "base_height", "rotated",
                  "background", "widgets")


@dataclass
class Widget:
    id: str
    x: float
    y: float
    width: float
    height: float
    kind: dict[str, Any]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def kind_type(self) -> str:
        return self.kind.get("type", "")

    @property
    def source(self) -> dict[str, Any] | None:
        src = self.kind.get("source")
        return src if isinstance(src, dict) else None

    @classmethod
    def from_json(cls, obj: dict) -> "Widget":
        return cls(
            id=obj["id"],
            x=obj["x"], y=obj["y"], width=obj["width"], height=obj["height"],
            kind=obj.get("kind") or {},
            extra={k: v for k, v in obj.items() if k not in _WIDGET_KEYS},
        )

    def to_json(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id, "x": self.x, "y": self.y,
            "width": self.width, "height": self.height, "kind": self.kind,
        }
        out.update(self.extra)
        return out


@dataclass
class Template:
    id: str
    name: str
    base_width: int
    base_height: int
    rotated: bool
    background: dict[str, Any]
    widgets: list[Widget]
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, obj: dict) -> "Template":
        return cls(
            id=obj["id"], name=obj["name"],
            base_width=obj["base_width"], base_height=obj["base_height"],
            rotated=obj["rotated"], background=obj["background"],
            widgets=[Widget.from_json(w) for w in obj.get("widgets", [])],
            extra={k: v for k, v in obj.items() if k not in _TEMPLATE_KEYS},
        )

    def to_json(self) -> dict:
        out: dict[str, Any] = {
            "id": self.id, "name": self.name,
            "base_width": self.base_width, "base_height": self.base_height,
            "rotated": self.rotated, "background": self.background,
            "widgets": [w.to_json() for w in self.widgets],
        }
        out.update(self.extra)
        return out

    def widget(self, widget_id: str) -> Widget | None:
        return next((w for w in self.widgets if w.id == widget_id), None)
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_model_roundtrip.py -v`
Expected: PASS, 4 passed

Note: `test_roundtrip_preserves_real_template_exactly` compares dicts, so key order does not matter, but every key and value must survive. If it fails, print the symmetric difference of the flattened key sets rather than eyeballing 31 widgets.

- [ ] **Step 5: Commit**

```bash
git add lianli_panel/model.py tests/test_model_roundtrip.py
git commit -F - <<'MSG'
feat: add Template/Widget model with lossless JSON round-trip

Unknown keys are preserved recursively at template, widget, kind and source
level. This is a correctness requirement: the daemon ignores fields it does
not recognise, so anything dropped on load is silently deleted on save.

Widget order is draw order and is never reordered implicitly -- the
cover-bar visibility trick depends on it.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 4: Percentage conversion and validation

This is the highest-value correctness work in the plan. `SensorRange.max` is a percentage of the widget's own span; every UI field shows real units. A mistake here renders wrong colours with no error anywhere.

**Files:**
- Modify: `lianli_panel/model.py` (append)
- Test: `tests/test_model_ranges.py`

**Interfaces:**
- Consumes: `Template`, `Widget` from Task 3.
- Produces, all in `lianli_panel.model`:
  - `pct_to_raw(pct: float, vmin: float, vmax: float) -> float`
  - `raw_to_pct(raw: float, vmin: float, vmax: float) -> float`
  - `widget_span(w: Widget) -> tuple[float, float] | None`
  - `range_thresholds_raw(w: Widget) -> list[float | None]`
  - `set_range_threshold_raw(w: Widget, index: int, raw: float | None) -> None`
  - `Problem` dataclass: `level: str` (`"error"` / `"warning"`), `widget_id: str`, `message: str`
  - `validate(t: Template) -> list[Problem]`

- [ ] **Step 1: Write the failing test**

`tests/test_model_ranges.py`:

```python
import pytest

from lianli_panel.model import (
    Problem, Template, Widget, pct_to_raw, raw_to_pct,
    range_thresholds_raw, set_range_threshold_raw, validate, widget_span,
)


def gauge(ranges, vmin=20.0, vmax=100.0, wid="g"):
    return Widget(id=wid, x=0.0, y=0.0, width=10.0, height=10.0,
                  kind={"type": "radial_gauge",
                        "source": {"type": "constant", "value": 1.0},
                        "value_min": vmin, "value_max": vmax, "ranges": ranges})


def tpl(widgets):
    return Template(id="t", name="T", base_width=1920, base_height=480,
                    rotated=True, background={"type": "color", "rgb": [0, 0, 0, 255]},
                    widgets=widgets)


# --- conversion ------------------------------------------------------------

def test_sixty_percent_of_twenty_to_hundred_is_sixty_eight():
    assert pct_to_raw(60.0, 20.0, 100.0) == pytest.approx(68.0)


def test_raw_to_pct_is_the_inverse():
    assert raw_to_pct(68.0, 20.0, 100.0) == pytest.approx(60.0)


def test_roundtrip_is_stable_across_representative_values():
    for raw in (20.0, 33.3, 68.0, 99.9, 100.0):
        assert pct_to_raw(raw_to_pct(raw, 20.0, 100.0), 20.0, 100.0) == pytest.approx(raw)


def test_degenerate_span_normalises_to_zero():
    assert raw_to_pct(50.0, 40.0, 40.0) == 0.0
    assert pct_to_raw(75.0, 40.0, 40.0) == 40.0


def test_values_outside_the_span_clamp_like_the_daemon():
    # The daemon clamps the unit interval to [0,1] before scaling.
    assert raw_to_pct(5.0, 20.0, 100.0) == 0.0
    assert raw_to_pct(500.0, 20.0, 100.0) == 100.0


# --- reading and writing thresholds ---------------------------------------

def test_thresholds_are_reported_in_real_units():
    w = gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255},
               {"max": None, "color": [1, 1, 1], "alpha": 255}])
    assert range_thresholds_raw(w) == [pytest.approx(60.0), None]


def test_setting_a_threshold_writes_back_a_percentage():
    w = gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255},
               {"max": None, "color": [1, 1, 1], "alpha": 255}])
    set_range_threshold_raw(w, 0, 68.0)
    assert w.kind["ranges"][0]["max"] == pytest.approx(60.0)


def test_untouched_ranges_are_not_rewritten():
    """Float drift on save would break the lossless-round-trip promise."""
    original = {"max": 33.333333333333336, "color": [0, 0, 0], "alpha": 255}
    w = gauge([dict(original), {"max": None, "color": [1, 1, 1], "alpha": 255}])
    set_range_threshold_raw(w, 1, None)
    assert w.kind["ranges"][0] == original


def test_widget_without_a_span_has_no_thresholds():
    w = Widget(id="l", x=0.0, y=0.0, width=1.0, height=1.0,
               kind={"type": "label", "text": "hi"})
    assert widget_span(w) is None
    assert range_thresholds_raw(w) == []


# --- validation ------------------------------------------------------------

def _messages(problems):
    return " | ".join(p.message for p in problems)


def test_reversed_span_is_an_error():
    problems = validate(tpl([gauge([{"max": None, "color": [0, 0, 0], "alpha": 255}],
                                   vmin=100.0, vmax=20.0)]))
    assert any(p.level == "error" for p in problems)
    assert "value_min" in _messages(problems)


def test_unsorted_range_maxima_is_an_error():
    problems = validate(tpl([gauge([{"max": 80.0, "color": [0, 0, 0], "alpha": 255},
                                    {"max": 30.0, "color": [0, 0, 0], "alpha": 255},
                                    {"max": None, "color": [1, 1, 1], "alpha": 255}])]))
    assert any("ascending" in p.message for p in problems)


def test_maximum_outside_zero_to_one_hundred_is_an_error():
    problems = validate(tpl([gauge([{"max": 140.0, "color": [0, 0, 0], "alpha": 255},
                                    {"max": None, "color": [1, 1, 1], "alpha": 255}])]))
    assert any("0..100" in p.message for p in problems)


def test_missing_catch_all_is_a_warning():
    problems = validate(tpl([gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255}])]))
    assert any(p.level == "warning" and "catch-all" in p.message for p in problems)


def test_two_catch_alls_is_an_error():
    problems = validate(tpl([gauge([{"max": None, "color": [0, 0, 0], "alpha": 255},
                                    {"max": None, "color": [1, 1, 1], "alpha": 255}])]))
    assert any(p.level == "error" and "catch-all" in p.message for p in problems)


def test_duplicate_widget_ids_is_an_error():
    problems = validate(tpl([gauge([{"max": None, "color": [0, 0, 0], "alpha": 255}], wid="d"),
                             gauge([{"max": None, "color": [0, 0, 0], "alpha": 255}], wid="d")]))
    assert any("duplicate" in p.message for p in problems)


def test_a_clean_template_reports_nothing():
    assert validate(tpl([gauge([{"max": 50.0, "color": [0, 0, 0], "alpha": 255},
                                {"max": None, "color": [1, 1, 1], "alpha": 255}])])) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_model_ranges.py -v`
Expected: FAIL — `ImportError: cannot import name 'pct_to_raw'`

- [ ] **Step 3: Append the implementation to `lianli_panel/model.py`**

```python
# --- range conversion ------------------------------------------------------
#
# CONFIRMED BY DISASSEMBLY of the installed daemon:
#   unit = clamp((value - value_min) / (value_max - value_min), 0, 1)
#   percentage = unit * 100
# and range selection picks the FIRST range whose `max >= percentage`, with a
# null `max` acting as the fallback. So a range `max` is a percentage of the
# widget's own span, NEVER a raw sensor reading. A "60" on a 20..100 gauge
# means 68 degrees. Getting this wrong renders plausible, wrong colours with no
# error anywhere, which is why every UI field is in real units and converts here.


def raw_to_pct(raw: float, vmin: float, vmax: float) -> float:
    span = vmax - vmin
    if span == 0:
        return 0.0
    unit = (raw - vmin) / span
    return max(0.0, min(1.0, unit)) * 100.0


def pct_to_raw(pct: float, vmin: float, vmax: float) -> float:
    span = vmax - vmin
    if span == 0:
        return vmin
    return vmin + (pct / 100.0) * span


def widget_span(w: Widget) -> tuple[float, float] | None:
    lo, hi = w.kind.get("value_min"), w.kind.get("value_max")
    if isinstance(lo, (int, float)) and isinstance(hi, (int, float)):
        return float(lo), float(hi)
    return None


def _ranges(w: Widget) -> list[dict]:
    r = w.kind.get("ranges")
    return r if isinstance(r, list) else []


def range_thresholds_raw(w: Widget) -> list[float | None]:
    """Thresholds in real units. None marks the catch-all range."""
    span = widget_span(w)
    if span is None:
        return []
    lo, hi = span
    out: list[float | None] = []
    for entry in _ranges(w):
        m = entry.get("max")
        out.append(None if m is None else pct_to_raw(float(m), lo, hi))
    return out


def set_range_threshold_raw(w: Widget, index: int, raw: float | None) -> None:
    """Write one threshold back as a percentage.

    Only the named index is touched. Re-encoding untouched ranges would drift
    their stored floats on every save and break lossless round-tripping.
    """
    span = widget_span(w)
    if span is None:
        raise ValueError(f"widget {w.id!r} has no value_min/value_max span")
    lo, hi = span
    entries = _ranges(w)
    if not 0 <= index < len(entries):
        raise IndexError(f"widget {w.id!r} has no range at index {index}")
    entries[index]["max"] = None if raw is None else raw_to_pct(raw, lo, hi)


# --- validation ------------------------------------------------------------


@dataclass
class Problem:
    level: str  # "error" | "warning"
    widget_id: str
    message: str


def validate(t: Template) -> list[Problem]:
    problems: list[Problem] = []

    seen: set[str] = set()
    for w in t.widgets:
        if w.id in seen:
            problems.append(Problem("error", w.id, f"duplicate widget id {w.id!r}"))
        seen.add(w.id)

    for w in t.widgets:
        span = widget_span(w)
        if span is not None and span[0] > span[1]:
            problems.append(Problem(
                "error", w.id,
                f"value_min ({span[0]}) is greater than value_max ({span[1]})"))

        entries = _ranges(w)
        if not entries:
            continue

        maxima = [e.get("max") for e in entries]
        nulls = [i for i, m in enumerate(maxima) if m is None]

        if len(nulls) > 1:
            problems.append(Problem(
                "error", w.id,
                f"{len(nulls)} catch-all ranges (max: null); only the first is reachable"))
        elif not nulls:
            problems.append(Problem(
                "warning", w.id,
                "no catch-all range (max: null); values above the last threshold "
                "have no colour"))
        elif nulls[0] != len(maxima) - 1:
            problems.append(Problem(
                "error", w.id,
                f"catch-all range at index {nulls[0]} makes the "
                f"{len(maxima) - nulls[0] - 1} range(s) after it unreachable"))

        numeric = [m for m in maxima if m is not None]
        for m in numeric:
            if not 0.0 <= float(m) <= 100.0:
                problems.append(Problem(
                    "error", w.id,
                    f"range max {m} is outside 0..100 — it is a percentage of the "
                    f"widget's own span, not a raw reading"))
                break
        if numeric != sorted(numeric):
            problems.append(Problem(
                "error", w.id,
                "range maxima are not in ascending order; the first match wins, "
                "so later ranges are unreachable"))

    return problems
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_model_ranges.py -v`
Expected: PASS, 16 passed

- [ ] **Step 5: Check the real template validates clean**

Run:
```bash
./.venv/bin/python -c "
import json
from lianli_panel.model import Template, validate
t = Template.from_json(json.load(open('/var/tmp/lianli-stats/gaming-dash.json')))
for p in validate(t):
    print(f'{p.level:8} {p.widget_id:12} {p.message}')
print('problems:', len(validate(t)))
"
```
Expected: `problems: 0`. A hand-built template that has driven the panel for days should be clean. **If anything is reported, do not "fix" the template — investigate whether the validator is wrong.** The template is known-good; the validator is new.

- [ ] **Step 6: Commit**

```bash
git add lianli_panel/model.py tests/test_model_ranges.py
git commit -F - <<'MSG'
feat: convert range thresholds between real units and percentages

SensorRange.max is a percentage of the widget's own value_min..value_max
span, not a raw reading -- confirmed by disassembly of range_color_blended,
which clamps the unit interval to [0,1] and multiplies by 100. A "60" on a
20..100 gauge means 68 degrees.

This fails silently when wrong: the panel renders, nothing errors, the
colours are merely incorrect. So every UI field is in real units and
converts here.

Only the edited range index is written back; re-encoding untouched ranges
would drift stored floats on every save.

Validation covers reversed spans, unsorted maxima, out-of-0..100 maxima,
missing/duplicate/misplaced catch-alls, and duplicate widget ids.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 5: Preview renderer with command substitution

**Files:**
- Create: `lianli_panel/render.py`
- Test: `tests/test_render.py`

**Interfaces:**
- Consumes: `ipc.Client`, `model.Template`.
- Produces, in `lianli_panel.render`:
  - `substitute_commands(tpl_json: dict, values: dict[str, float], default: float = 0.0) -> dict` — deep copy with every `{"type":"command","cmd":X}` source replaced by `{"type":"constant","value":values.get(X, default)}`
  - `command_sources(tpl_json: dict) -> list[str]` — every distinct `cmd` string
  - `PreviewRenderer(client, width=1920, height=480)` with `render(tpl_json: dict, live: bool = False) -> bytes` returning JPEG bytes, and attribute `last_values: dict[str, float]`
  - `Coalescer(interval_s: float = 0.25)` with `request(now: float) -> bool`, `finish(now: float) -> bool`, `pending: bool`

- [ ] **Step 1: Write the failing test**

`tests/test_render.py`:

```python
import base64
import json

import pytest

from lianli_panel.render import (
    Coalescer, PreviewRenderer, command_sources, substitute_commands,
)

TPL = {
    "id": "t", "name": "T", "base_width": 1920, "base_height": 480,
    "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
    "widgets": [
        {"id": "a", "x": 1.0, "y": 1.0, "width": 1.0, "height": 1.0,
         "kind": {"type": "value_text",
                  "source": {"type": "command", "cmd": "/bin/fps.sh"}}},
        {"id": "b", "x": 1.0, "y": 1.0, "width": 1.0, "height": 1.0,
         "kind": {"type": "value_text",
                  "source": {"type": "nvidia_gpu", "gpu_index": 0, "metric": "temp"}}},
        {"id": "c", "x": 1.0, "y": 1.0, "width": 1.0, "height": 1.0,
         "kind": {"type": "value_text",
                  "source": {"type": "command", "cmd": "/bin/fps.sh"}}},
    ],
}


def test_command_sources_are_deduplicated():
    assert command_sources(TPL) == ["/bin/fps.sh"]


def test_substitution_replaces_only_command_sources():
    out = substitute_commands(TPL, {"/bin/fps.sh": 144.0})
    assert out["widgets"][0]["kind"]["source"] == {"type": "constant", "value": 144.0}
    assert out["widgets"][2]["kind"]["source"] == {"type": "constant", "value": 144.0}
    assert out["widgets"][1]["kind"]["source"]["type"] == "nvidia_gpu"


def test_substitution_uses_default_for_unknown_commands():
    out = substitute_commands(TPL, {}, default=7.0)
    assert out["widgets"][0]["kind"]["source"] == {"type": "constant", "value": 7.0}


def test_substitution_does_not_mutate_the_input():
    before = json.dumps(TPL, sort_keys=True)
    substitute_commands(TPL, {"/bin/fps.sh": 1.0})
    assert json.dumps(TPL, sort_keys=True) == before


def test_automatic_render_sends_no_command_sources(fake_client):
    jpeg = b"\xff\xd8fake"
    fake_client.responses["RenderTemplatePreview"] = {
        "jpeg_base64": base64.b64encode(jpeg).decode()
    }
    assert PreviewRenderer(fake_client).render(TPL) == jpeg

    sent = fake_client.calls[0][1]["template"]
    types = [w["kind"]["source"]["type"] for w in sent["widgets"]]
    assert "command" not in types


def test_live_render_sends_command_sources_untouched(fake_client):
    fake_client.responses["RenderTemplatePreview"] = {
        "jpeg_base64": base64.b64encode(b"x").decode()
    }
    PreviewRenderer(fake_client).render(TPL, live=True)
    sent = fake_client.calls[0][1]["template"]
    assert sent["widgets"][0]["kind"]["source"]["type"] == "command"


def test_render_always_sends_width_and_height(fake_client):
    fake_client.responses["RenderTemplatePreview"] = {
        "jpeg_base64": base64.b64encode(b"x").decode()
    }
    PreviewRenderer(fake_client).render(TPL)
    params = fake_client.calls[0][1]
    assert params["width"] == 1920 and params["height"] == 480


# --- coalescing ------------------------------------------------------------

def test_first_request_fires_immediately():
    assert Coalescer(0.25).request(now=100.0) is True


def test_request_while_in_flight_is_held_not_dropped():
    c = Coalescer(0.25)
    c.request(now=100.0)
    assert c.request(now=100.01) is False
    assert c.pending is True


def test_held_request_fires_when_the_previous_one_finishes():
    c = Coalescer(0.25)
    c.request(now=100.0)
    c.request(now=100.01)
    assert c.finish(now=100.3) is True
    assert c.pending is False


def test_finish_with_nothing_pending_fires_nothing():
    c = Coalescer(0.25)
    c.request(now=100.0)
    assert c.finish(now=100.3) is False


def test_requests_inside_the_debounce_window_collapse_to_one():
    c = Coalescer(0.25)
    c.request(now=100.0)
    c.finish(now=100.1)
    fired = [c.request(now=100.1 + i * 0.01) for i in range(10)]
    assert fired.count(True) == 0  # all inside the 250ms window
    assert c.pending is True


def test_a_request_held_by_the_debounce_window_still_fires_eventually():
    """REGRESSION. A request arriving after finish() but inside the debounce
    window has no in-flight render to release it. Without a polled due() it
    stays pending forever and the final state of a drag never renders."""
    c = Coalescer(0.25)
    c.request(now=100.0)
    c.finish(now=100.1)
    assert c.request(now=100.15) is False
    assert c.pending is True
    assert c.due(now=100.20) is False   # still inside the window
    assert c.due(now=100.30) is True    # window elapsed, so it fires
    assert c.pending is False


def test_due_does_nothing_when_nothing_is_held():
    c = Coalescer(0.25)
    assert c.due(now=200.0) is False


def test_due_does_not_fire_while_a_render_is_in_flight():
    c = Coalescer(0.25)
    c.request(now=100.0)
    c.request(now=100.01)
    assert c.due(now=101.0) is False    # in flight, despite the window elapsing
    assert c.pending is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_render.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.render'`

- [ ] **Step 3: Implement `lianli_panel/render.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_render.py -v`
Expected: PASS, 15 passed

- [ ] **Step 5: Prove the substitution really prevents execution**

This is the whole point of the task; assert it against the real daemon rather than trusting the unit test.

Run:
```bash
./.venv/bin/python - <<'PY'
import time
from pathlib import Path
from lianli_panel.ipc import Client
from lianli_panel.render import PreviewRenderer

LOG = Path("/var/tmp/lianli-subst-probe.log")
tpl = {"id": "p", "name": "p", "base_width": 1920, "base_height": 480,
       "rotated": True, "background": {"type": "color", "rgb": [0, 0, 0, 255]},
       "widgets": [{"id": "w", "x": 960.0, "y": 240.0, "width": 400.0, "height": 100.0,
         "kind": {"type": "value_text",
                  "source": {"type": "command", "cmd": f"echo x >> {LOG}; echo 42"},
                  "format": "{:.0}", "unit": "",
                  "font": {"path": "/usr/share/fonts/google-noto/NotoSansMono-Bold.ttf"},
                  "font_size": 80.0, "color": [255, 255, 255, 255], "align": "center",
                  "value_min": 0.0, "value_max": 100.0,
                  "ranges": [{"max": None, "color": [255, 255, 255], "alpha": 255}],
                  "letter_spacing": 0.0}}]}

r = PreviewRenderer(Client())
def lines():
    return len(LOG.read_text().splitlines()) if LOG.exists() else 0

before = lines()
for _ in range(5):
    r.render(tpl)                 # automatic path
auto = lines() - before
for _ in range(5):
    r.render(tpl, live=True)      # explicit live path
live = lines() - before - auto
print(f"executions during 5 automatic renders: {auto}   (MUST be 0)")
print(f"executions during 5 live renders:      {live}   (expect 10 = 2/render)")
assert auto == 0, "command source executed on the automatic path"
print("OK")
PY
```
Expected: `executions during 5 automatic renders: 0`, `... 5 live renders: 10`, then `OK`.

The probe file is created by uid `lianli` in a sticky directory, so it cannot be deleted by this user. Leave it; note it for cleanup with `sudo rm /var/tmp/lianli-subst-probe.log`.

- [ ] **Step 6: Commit**

```bash
git add lianli_panel/render.py tests/test_render.py
git commit -F - <<'MSG'
feat: add preview renderer that never executes command sources

RenderTemplatePreview touches no hardware but is not pure: measured, it runs
command sources twice per widget per render as uid lianli. gaming-dash has 8
such widgets, so one preview spawns 16 subprocesses at ~0.30s, and graph.sh
mutates the state file the live panel's sparkline reads.

Automatic renders therefore swap command sources for constant ones -- a
native daemon source type, so the render path is unchanged. Live values are
an explicit, separate call.

Coalescer holds rather than drops the newest request, so the final state of
a drag always renders.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 6: Panel health check

The first draft of the spec got this wrong twice. Read the spec's "Is it actually on the screen?" section before starting.

**Files:**
- Create: `lianli_panel/health.py`
- Test: `tests/test_health.py`

**Interfaces:**
- Consumes: nothing (subprocess only).
- Produces, in `lianli_panel.health`: `PanelHealth` dataclass with `ok: bool`, `reason: str`, `last_open: datetime | None`, `last_disconnect: datetime | None`; `parse_journal(lines: Iterable[str]) -> PanelHealth`; `check(unit: str = "lianli-daemon-system.service") -> PanelHealth`; constant `RESTART_HINT: str`.

- [ ] **Step 1: Write the failing test**

`tests/test_health.py`:

```python
from lianli_panel.health import parse_journal

OPEN_LCD = ('2026-09-03T10:54:37-04:00 host lianli-daemon[1148]: 0.60s  INFO '
            'lianli_devices::winusb::lcd::core: Universal Screen 8.8" opened: '
            '480x1920 at bus 1 addr 6 serial 513b5a7acadc4203')
OPEN_RING = ('2026-09-03T10:54:37-04:00 host lianli-daemon[1148]: 0.55s  INFO '
             'lianli_devices::winusb::led: Universal Screen 8.8" LED Ring opened: '
             '60 LEDs (0416:8050)')
TOPOLOGY = ('2026-09-03T18:20:01-04:00 host lianli-daemon[1148]: 100.0s  INFO '
            'lianli_devices::winusb: Wired device topology changed')
OPEN_LCD_LATER = OPEN_LCD.replace("10:54:37", "18:21:05")


def test_lcd_open_with_no_disconnect_is_healthy():
    h = parse_journal([OPEN_RING, OPEN_LCD])
    assert h.ok is True
    assert h.last_open is not None


def test_ring_open_alone_is_not_healthy():
    """The ring reopens on replug and the screen does not. Matching the shared
    'Universal Screen 8.8" ... opened' prefix would report healthy here, which
    is exactly the failure this check exists to catch."""
    h = parse_journal([OPEN_RING])
    assert h.ok is False
    assert h.last_open is None


def test_disconnect_after_the_last_open_is_unhealthy():
    h = parse_journal([OPEN_RING, OPEN_LCD, TOPOLOGY])
    assert h.ok is False
    assert "topology" in h.reason.lower() or "disconnect" in h.reason.lower()


def test_reopen_after_a_disconnect_is_healthy_again():
    h = parse_journal([OPEN_LCD, TOPOLOGY, OPEN_LCD_LATER])
    assert h.ok is True


def test_ring_reopening_after_a_disconnect_does_not_clear_it():
    ring_later = OPEN_RING.replace("10:54:37", "18:21:05")
    h = parse_journal([OPEN_LCD, TOPOLOGY, ring_later])
    assert h.ok is False


def test_no_open_line_at_all_is_unhealthy():
    h = parse_journal([])
    assert h.ok is False
    assert h.last_open is None


def test_precise_timestamps_with_microseconds_parse():
    """check() asks journalctl for short-iso-precise, so the parser must accept
    fractional seconds. A regex written for whole seconds matches nothing here
    and every line is silently skipped."""
    precise = OPEN_LCD.replace("10:54:37-04:00", "10:54:37.687280-04:00")
    assert parse_journal([precise]).ok is True


def test_same_second_disconnect_after_open_is_still_unhealthy():
    """A real replug logs the topology change and the ring reopen inside the
    SAME second, so timestamp comparison alone calls them equal. Stream order
    decides."""
    open_same = OPEN_LCD.replace("10:54:37", "17:59:48")
    topo_same = TOPOLOGY.replace("18:20:01", "17:59:48")
    assert parse_journal([open_same, topo_same]).ok is False


def test_same_second_reopen_after_disconnect_is_healthy():
    topo_same = TOPOLOGY.replace("18:20:01", "17:59:48")
    open_same = OPEN_LCD.replace("10:54:37", "17:59:48")
    assert parse_journal([topo_same, open_same]).ok is True


def test_real_replug_sequence_is_unhealthy():
    """Verbatim shape of the 2026-09-02 replug: the encoder dies, the topology
    changes twice, and only the LED RING reopens. The screen never does."""
    seq = [
        OPEN_LCD,
        '2026-09-02T17:59:47-04:00 host lianli-daemon[1148]: INFO x: H264 chunk '
        'write failed: USB error: No such device (it may have been disconnected)',
        '2026-09-02T17:59:48-04:00 host lianli-daemon[1148]: INFO x: reopen '
        'failed: USB error: No such device (it may have been disconnected)',
        '2026-09-02T17:59:48-04:00 host lianli-daemon[1148]: INFO x: Wired '
        'device topology changed (+0 -1): re-initializing',
        '2026-09-02T18:00:10-04:00 host lianli-daemon[1148]: INFO x: Wired '
        'device topology changed (+1 -0): re-initializing',
        OPEN_RING.replace("10:54:37", "18:00:10"),
    ]
    assert parse_journal(seq).ok is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_health.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.health'`

- [ ] **Step 3: Implement `lianli_panel/health.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_health.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Check it against the live journal**

Run: `./.venv/bin/python -c "from lianli_panel.health import check; h = check(); print(h.ok); print(h.reason)"`

Expected: `True` and a reason naming the open time, assuming the panel has not been replugged since the daemon started. If it reports `False`, verify by looking at the physical screen before assuming the check is wrong.

- [ ] **Step 6: Commit**

```bash
git add lianli_panel/health.py tests/test_health.py
git commit -F - <<'MSG'
feat: add panel health check that survives a replug

After a replug the daemon reopens the LED ring but never the screen, stays
active(running), and returns ok into a dead handle -- so "Prepared custom
template" proves nothing.

Two traps this avoids, both of which an earlier draft hit:
- The daemon does not restart on replug, so "since daemon start" never
  expires and always finds the original open line.
- Two log lines match 'Universal Screen 8.8" ... opened' and the other is
  the LED ring, the one device that DOES come back.

Matches the lcd::core module plus 480x1920, and compares the newest open
against the newest disconnect by timestamp. Reported as a heuristic.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 7: Transactional apply with conflict detection

**Files:**
- Create: `lianli_panel/apply.py`
- Test: `tests/test_apply.py`

**Interfaces:**
- Consumes: `ipc.Client`, `ipc.DaemonError`.
- Produces, in `lianli_panel.apply`: `templates_hash(templates: list[dict]) -> str`; `ConflictError(Exception)`; `ApplyFailed(Exception)`; `LCD_SERIAL = "hid:513b5a7acadc4203"`; `find_lcd(client) -> str`; `read_templates(client) -> tuple[list[dict], str]`; `apply_templates(client, templates: list[dict], live_id: str, *, base_hash: str | None = None, device_id: str | None = None, lcd_entry_fallback: dict | None = None) -> None`.

- [ ] **Step 1: Write the failing test**

`tests/test_apply.py`:

```python
import pytest

from lianli_panel.apply import (
    ApplyFailed, ConflictError, apply_templates, read_templates, templates_hash,
)
from lianli_panel.ipc import DaemonError

A = {"id": "a", "name": "A", "widgets": []}
B = {"id": "b", "name": "B", "widgets": []}
DEV = "hid:513b5a7acadc4203"


def _config(templates_live_id="a"):
    return {"lcds": [{"serial": DEV, "type": "custom",
                      "template_id": templates_live_id, "orientation": 90.0}]}


def _client(fake_client, stored):
    fake_client.responses["GetLcdTemplates"] = stored
    fake_client.responses["GetConfig"] = _config()
    fake_client.responses["SetLcdTemplates"] = None
    fake_client.responses["SetLcdMedia"] = None
    return fake_client


def test_hash_is_order_sensitive_because_draw_order_matters():
    assert templates_hash([A, B]) != templates_hash([B, A])


def test_hash_ignores_key_ordering():
    assert templates_hash([{"id": "a", "name": "A"}]) == \
           templates_hash([{"name": "A", "id": "a"}])


def test_apply_sends_the_whole_library_not_just_the_live_one(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A, B], live_id="b", device_id=DEV)
    sent = next(p for m, p in c.calls if m == "SetLcdTemplates")
    assert [t["id"] for t in sent["templates"]] == ["a", "b"]


def test_apply_calls_set_media_after_set_templates(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A], live_id="a", device_id=DEV)
    methods = [m for m in c.methods() if m.startswith("SetLcd")]
    assert methods == ["SetLcdTemplates", "SetLcdMedia"]


def test_apply_points_the_lcd_entry_at_the_live_template(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A, B], live_id="b", device_id=DEV)
    params = next(p for m, p in c.calls if m == "SetLcdMedia")
    assert params["config"]["template_id"] == "b"
    assert params["config"]["type"] == "custom"


def test_conflict_when_the_stored_set_changed_under_us(fake_client):
    c = _client(fake_client, [A, B])
    with pytest.raises(ConflictError):
        apply_templates(c, [A], live_id="a", device_id=DEV,
                        base_hash=templates_hash([A]))
    assert "SetLcdTemplates" not in c.methods()


def test_matching_base_hash_applies_normally(fake_client):
    c = _client(fake_client, [A])
    apply_templates(c, [A, B], live_id="a", device_id=DEV,
                    base_hash=templates_hash([A]))
    assert "SetLcdTemplates" in c.methods()


def test_failed_set_media_restores_the_previous_template_set(fake_client):
    c = _client(fake_client, [A])
    c.responses["SetLcdMedia"] = DaemonError("device busy")
    with pytest.raises(ApplyFailed):
        apply_templates(c, [A, B], live_id="b", device_id=DEV)
    sets = [p["templates"] for m, p in c.calls if m == "SetLcdTemplates"]
    assert len(sets) == 2
    assert [t["id"] for t in sets[1]] == ["a"]      # rolled back


def test_missing_lcd_entry_is_restored_from_the_fallback(fake_client):
    """lianli-gui wipes the lcds array; the entry must be rebuilt, not invented."""
    c = _client(fake_client, [A])
    c.responses["GetConfig"] = {"lcds": []}
    fallback = {"serial": DEV, "type": "custom", "orientation": 90.0}
    apply_templates(c, [A], live_id="a", device_id=DEV, lcd_entry_fallback=fallback)
    params = next(p for m, p in c.calls if m == "SetLcdMedia")
    assert params["config"]["orientation"] == 90.0


def test_missing_lcd_entry_with_no_fallback_fails_loudly(fake_client):
    c = _client(fake_client, [A])
    c.responses["GetConfig"] = {"lcds": []}
    with pytest.raises(ApplyFailed, match="no LCD entry"):
        apply_templates(c, [A], live_id="a", device_id=DEV)


def test_read_templates_returns_the_set_and_its_hash(fake_client):
    fake_client.responses["GetLcdTemplates"] = [A]
    templates, digest = read_templates(fake_client)
    assert templates == [A] and digest == templates_hash([A])
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_apply.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.apply'`

- [ ] **Step 3: Implement `lianli_panel/apply.py`**

```python
"""Applying templates to the panel.

THREE HAZARDS, all previously hit by hand-written scripts:

1. SetLcdTemplates REPLACES THE ENTIRE STORED SET. The existing apply.sh sends
   only [gaming-dash], which is harmless with one template and silently deletes
   every other one the moment there are two. Always send the whole library.

2. SetLcdTemplates ALONE DOES NOT UPDATE THE PANEL. It replaces the stored
   template while the live renderer keeps what it last prepared. SetLcdMedia
   must follow to force a re-prepare. These are one code path here so the first
   cannot be called without the second.

3. lianli-gui WIPES THE lcds ARRAY every time it writes config, because it
   cannot represent template mode. The entry is restored from a caller-supplied
   known-good copy -- never invented, because a wrong orientation or serial
   would render sideways or not at all.

The two calls are not atomic. If SetLcdMedia fails, the stored set has moved on
while the panel still shows the old frame, so the previous set is restored.
"""
from __future__ import annotations

import hashlib
import json

from .ipc import DaemonError

LCD_SERIAL = "hid:513b5a7acadc4203"


class ConflictError(Exception):
    """The stored template set changed since this draft was read."""


class ApplyFailed(Exception):
    """The apply did not complete; the panel was left unchanged."""


def templates_hash(templates: list[dict]) -> str:
    """Order-sensitive digest. Widget and template order are both meaningful."""
    blob = json.dumps(templates, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode()).hexdigest()


def read_templates(client) -> tuple[list[dict], str]:
    templates = client.call("GetLcdTemplates") or []
    return templates, templates_hash(templates)


def find_lcd(client) -> str:
    """Resolve the LCD's device id. Its serial is stable across replugs, unlike
    the LED ring's, which is derived from the USB path."""
    for dev in client.call("ListDevices") or []:
        if dev.get("has_lcd"):
            return dev["device_id"]
    raise ApplyFailed("no LCD device found; is the screen plugged in?")


def _lcd_entry(client, device_id: str, fallback: dict | None) -> dict:
    config = client.call("GetConfig") or {}
    for entry in config.get("lcds") or []:
        if entry.get("serial") == device_id:
            return dict(entry)
    if fallback is None:
        raise ApplyFailed(
            f"no LCD entry for {device_id} in config.lcds and no known-good "
            "fallback was supplied. lianli-gui wipes this array; restore it from "
            "a saved copy rather than guessing orientation and serial.")
    return dict(fallback)


def apply_templates(client, templates: list[dict], live_id: str, *,
                    base_hash: str | None = None,
                    device_id: str | None = None,
                    lcd_entry_fallback: dict | None = None) -> None:
    if not any(t.get("id") == live_id for t in templates):
        raise ApplyFailed(f"live template {live_id!r} is not in the set being sent")

    device_id = device_id or find_lcd(client)

    previous, current_hash = read_templates(client)
    if base_hash is not None and current_hash != base_hash:
        raise ConflictError(
            "the daemon's template set changed since this draft was opened — "
            "another process (apply.sh, lianli-gui, or a second editor) wrote to "
            "it. Applying now would discard that change.")

    entry = _lcd_entry(client, device_id, lcd_entry_fallback)
    entry["type"] = "custom"
    entry["template_id"] = live_id

    client.call("SetLcdTemplates", {"templates": templates})
    try:
        client.call("SetLcdMedia", {"device_id": device_id, "config": entry})
    except DaemonError as exc:
        try:
            client.call("SetLcdTemplates", {"templates": previous})
        except DaemonError as rollback_exc:
            raise ApplyFailed(
                f"SetLcdMedia failed ({exc}) AND the rollback failed "
                f"({rollback_exc}). The stored template set may be inconsistent; "
                "re-apply from a snapshot.") from exc
        raise ApplyFailed(
            f"SetLcdMedia failed ({exc}); the previous template set was restored "
            "and the panel is unchanged.") from exc
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_apply.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Commit**

```bash
git add lianli_panel/apply.py tests/test_apply.py
git commit -F - <<'MSG'
feat: apply templates transactionally with conflict detection

SetLcdTemplates replaces the ENTIRE stored set, so the whole library is
always sent -- apply.sh sends only [gaming-dash], which silently deletes
every other template as soon as a second one exists.

SetLcdTemplates alone does not update the panel; SetLcdMedia must follow.
Both live in one code path so the first cannot be called without the second.

The two calls are not atomic, so a failed SetLcdMedia restores the previous
set and reports the panel unchanged.

Conflict detection compares a hash of the stored set against the one read
when the draft was opened, so a concurrent write by apply.sh or lianli-gui
is refused rather than clobbered.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 8: Snapshots with retention

**Files:**
- Create: `lianli_panel/snapshot.py`
- Test: `tests/test_snapshot.py`

**Interfaces:**
- Consumes: `ipc.Client`, `apply.read_templates`.
- Produces, in `lianli_panel.snapshot`: `SNAPSHOT_ROOT = Path("~/.local/share/lianli-panel/snapshots").expanduser()`; `take(client, root: Path | None = None, keep: int = 20) -> Path`; `load(path: Path) -> dict`; `prune(root: Path, keep: int = 20) -> list[Path]`; `latest(root: Path | None = None) -> Path | None`.

**Snapshot contents** (per the spec — it stores what is *configured*, and does not claim to capture what the ring is physically showing, because `GetZoneColors` fails on this device):

```json
{
  "taken_at": "2026-09-04T17:30:00-04:00",
  "templates": [ ... ],
  "templates_hash": "...",
  "lcds": [ ... ],
  "rgb_config": { ... },
  "rgb_state_file": { ... } | null,
  "thermal_service_active": true,
  "note": "configured state only; the ring's actual colour cannot be read back (GetZoneColors fails on this device)"
}
```

- [ ] **Step 1: Write the failing test**

`tests/test_snapshot.py`:

```python
import json
from pathlib import Path

from lianli_panel.snapshot import latest, load, prune, take

A = {"id": "a", "name": "A", "widgets": []}


def _client(fake_client):
    fake_client.responses["GetLcdTemplates"] = [A]
    fake_client.responses["GetConfig"] = {
        "lcds": [{"serial": "hid:x", "template_id": "a"}],
        "rgb": {"devices": [{"device_id": "hid:ring", "zones": []}]},
    }
    return fake_client


def test_take_writes_a_readable_snapshot(tmp_path, fake_client):
    path = take(_client(fake_client), root=tmp_path)
    data = load(path)
    assert data["templates"] == [A]
    assert data["lcds"][0]["template_id"] == "a"
    assert "taken_at" in data


def test_snapshot_records_the_template_hash(tmp_path, fake_client):
    from lianli_panel.apply import templates_hash
    data = load(take(_client(fake_client), root=tmp_path))
    assert data["templates_hash"] == templates_hash([A])


def test_snapshot_disclaims_ring_readback(tmp_path, fake_client):
    data = load(take(_client(fake_client), root=tmp_path))
    assert "cannot be read back" in data["note"]


def test_prune_keeps_only_the_newest_n(tmp_path):
    for i in range(25):
        d = tmp_path / f"2026-09-04T00-00-{i:02d}"
        d.mkdir(parents=True)
        (d / "snapshot.json").write_text("{}")
    removed = prune(tmp_path, keep=20)
    assert len(removed) == 5
    assert len(list(tmp_path.iterdir())) == 20
    assert (tmp_path / "2026-09-04T00-00-24").exists()
    assert not (tmp_path / "2026-09-04T00-00-00").exists()


def test_prune_is_a_noop_below_the_limit(tmp_path):
    (tmp_path / "2026-09-04T00-00-01").mkdir(parents=True)
    assert prune(tmp_path, keep=20) == []


def test_take_prunes_as_it_goes(tmp_path, fake_client):
    c = _client(fake_client)
    for _ in range(3):
        take(c, root=tmp_path, keep=2)
    assert len(list(tmp_path.iterdir())) == 2


def test_latest_returns_the_newest(tmp_path):
    for name in ("2026-09-04T00-00-01", "2026-09-04T00-00-09"):
        d = tmp_path / name
        d.mkdir(parents=True)
        (d / "snapshot.json").write_text("{}")
    assert latest(tmp_path).name == "2026-09-04T00-00-09"


def test_latest_on_an_empty_root_is_none(tmp_path):
    assert latest(tmp_path) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_snapshot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.snapshot'`

- [ ] **Step 3: Implement `lianli_panel/snapshot.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_snapshot.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Take one real snapshot**

Run:
```bash
./.venv/bin/python -c "
from lianli_panel.ipc import Client
from lianli_panel.snapshot import take, load
p = take(Client())
d = load(p)
print(p)
print('templates:', [t['id'] for t in d['templates']])
print('lcds:', len(d['lcds']), '| thermal active:', d['thermal_service_active'])
"
```
Expected: a path under `~/.local/share/lianli-panel/snapshots/`, `templates: ['gaming-dash']`, `lcds: 1`.

- [ ] **Step 6: Commit**

```bash
git add lianli_panel/snapshot.py tests/test_snapshot.py
git commit -F - <<'MSG'
feat: snapshot configured state before applies, keeping the newest 20

Retention is bounded from the start rather than discovered later as
unbounded growth; take() prunes as it goes.

A snapshot stores CONFIGURED state and says so. It cannot capture what the
ring is physically showing: GetZoneColors fails on this device, and the
three available sources (daemon config, rgb-state.json, and whatever the
thermal poller last pushed) currently disagree with each other.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 9: Sensor library and two-tier probe

**Files:**
- Create: `lianli_panel/sensors.py`
- Test: `tests/test_sensors.py`

**Interfaces:**
- Consumes: `ipc.Client` only. It deliberately does **not** use `render.PreviewRenderer`: that class substitutes command sources away, which is the opposite of what a sensor probe needs. `render_authoritative` calls `RenderTemplatePreview` directly so the command really runs.
- Produces, in `lianli_panel.sensors`:
  - `LIBRARY_PATH = Path("~/.config/lianli-panel/sensors.json").expanduser()`
  - `USER_SCRIPT_DIR = Path("/var/lib/lianli-panel")`
  - `Sensor` dataclass: `name: str`, `source: dict`
  - `load(path=None) -> dict[str, Sensor]`, `save(sensors, path=None) -> None`
  - `Diagnostic` dataclass: `stdout: str`, `stderr: str`, `exit_code: int`, `parsed: float | None`, `problems: list[str]`
  - `run_diagnostic(cmd: str, timeout: float = 10.0) -> Diagnostic`
  - `render_authoritative(client, cmd: str) -> bytes` — JPEG of the value as the daemon reads it
  - `static_checks(cmd: str) -> list[str]`

- [ ] **Step 1: Write the failing test**

`tests/test_sensors.py`:

```python
import base64
import json

from lianli_panel.sensors import (
    Sensor, load, run_diagnostic, render_authoritative, save, static_checks,
)


# --- library persistence ---------------------------------------------------

def test_save_then_load_roundtrips(tmp_path):
    path = tmp_path / "sensors.json"
    save({"gpu_fan": Sensor("gpu_fan", {"type": "command", "cmd": "echo 1"})}, path)
    out = load(path)
    assert out["gpu_fan"].source["cmd"] == "echo 1"


def test_load_of_a_missing_file_is_empty(tmp_path):
    assert load(tmp_path / "absent.json") == {}


# --- diagnostic tier -------------------------------------------------------

def test_diagnostic_parses_the_first_token():
    d = run_diagnostic("echo '42.5 extra junk'")
    assert d.parsed == 42.5 and d.exit_code == 0


def test_diagnostic_reports_a_nonzero_exit():
    d = run_diagnostic("echo 1; exit 3")
    assert d.exit_code == 3
    assert any("exit" in p for p in d.problems)


def test_diagnostic_reports_unparseable_output():
    d = run_diagnostic("echo not-a-number")
    assert d.parsed is None
    assert any("parse" in p for p in d.problems)


def test_diagnostic_reports_empty_output():
    d = run_diagnostic("true")
    assert d.parsed is None
    assert any("no output" in p for p in d.problems)


def test_diagnostic_captures_stdout_errors():
    """nvidia-smi prints usage errors to STDOUT, so a parse-each-line loop
    swallows them and the sensor degrades while looking healthy."""
    d = run_diagnostic("echo 'Invalid combination of input arguments'")
    assert "Invalid combination" in d.stdout
    assert d.parsed is None


# --- static checks ---------------------------------------------------------

def test_home_path_is_flagged_as_unreachable():
    problems = static_checks("/home/chase/bin/fps.sh")
    assert any("/home/chase" in p for p in problems)


def test_path_outside_home_is_not_flagged_for_traversal():
    assert not any("/home/chase" in p for p in static_checks("/var/lib/x/fps.sh"))


def test_var_tmp_path_is_flagged_for_ageing():
    assert any("30 days" in p or "aged" in p for p in static_checks("/var/tmp/x/f.sh"))


# --- authoritative tier ----------------------------------------------------

def test_authoritative_probe_renders_a_one_widget_template(fake_client):
    fake_client.responses["RenderTemplatePreview"] = {
        "jpeg_base64": base64.b64encode(b"\xff\xd8jpeg").decode()
    }
    assert render_authoritative(fake_client, "echo 42") == b"\xff\xd8jpeg"
    params = fake_client.calls[0][1]
    widgets = params["template"]["widgets"]
    assert len(widgets) == 1
    assert widgets[0]["kind"]["source"] == {"type": "command", "cmd": "echo 42"}
    assert params["width"] == 1920 and params["height"] == 480
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_sensors.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.sensors'`

- [ ] **Step 3: Implement `lianli_panel/sensors.py`**

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_sensors.py -v`
Expected: PASS, 11 passed

- [ ] **Step 5: Prove the authoritative tier catches what the diagnostic misses**

This is the point of having two tiers — demonstrate the divergence on the real daemon.

Run:
```bash
mkdir -p ~/sensor-probe-demo && printf '#!/bin/sh\necho 77\n' > ~/sensor-probe-demo/t.sh && chmod +x ~/sensor-probe-demo/t.sh
./.venv/bin/python - <<'PY'
from pathlib import Path
from lianli_panel.ipc import Client
from lianli_panel.sensors import run_diagnostic, render_authoritative

cmd = str(Path("~/sensor-probe-demo/t.sh").expanduser())
d = run_diagnostic(cmd)
print("diagnostic (runs as chase): parsed =", d.parsed, "exit =", d.exit_code)
for p in d.problems:
    print("  problem:", p)

jpeg = render_authoritative(Client(), cmd)
Path("/tmp/sensor-probe.jpg").write_bytes(jpeg)
print("authoritative render written to /tmp/sensor-probe.jpg —",
      len(jpeg), "bytes. Open it: the daemon reads 0, not 77.")
PY
```
Expected: the diagnostic parses `77.0` and exits 0 **but reports the `/home/` problem**, while the rendered image shows `0` — the daemon cannot traverse `/home/chase`. Open `/tmp/sensor-probe.jpg` and confirm visually. This single case is why the diagnostic tier is labelled non-authoritative.

Clean up: `rm -rf ~/sensor-probe-demo /tmp/sensor-probe.jpg`

- [ ] **Step 6: Commit**

```bash
git add lianli_panel/sensors.py tests/test_sensors.py
git commit -F - <<'MSG'
feat: add sensor library with a two-tier command probe

The daemon has no sensors config key -- sources are inline per widget -- so
named reusable sensors are a client-side concept that expands on save.

Testing is two-tier and the tiers are not equivalent. The authoritative tier
renders a one-widget template, which the daemon executes as uid lianli, so
it needs no privileges and no new daemon method. The diagnostic tier runs
the command as the current user for stdout/stderr/exit status, and is
explicitly labelled non-authoritative because it runs as the wrong uid and
succeeds on $HOME paths the daemon cannot reach.

Static checks flag /home (mode 0700, untraversable by the daemon) and
/var/tmp (aged out after 30 days).

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 10: LED ring control and thermal poller config

**Files:**
- Create: `lianli_panel/ring.py`
- Test: `tests/test_ring.py`

**Interfaces:**
- Consumes: `ipc.Client`.
- Produces, in `lianli_panel.ring`:
  - `RING_PID = 0x8050`; `find_ring(client) -> str`
  - `ThermalConfig` dataclass with `cool_c: float = 45.0`, `hot_c: float = 85.0`, `poll_ms: int = 2000`, `min_delta_c: float = 1.0`, `force_refresh_s: int = 60`, `brightness: int = 4`; `to_json()`, `from_json(dict)`
  - `THERMAL_CONFIG_PATH = Path("/var/lib/lianli-panel/thermal-rgb.json")`
  - `load_thermal(path=None) -> ThermalConfig`, `save_thermal(cfg, path=None) -> None`
  - `set_static(client, rgb: tuple[int, int, int], brightness: int = 4) -> None`
  - `set_off(client) -> None`
  - `RGB_APPLY_WARNING: str`

- [ ] **Step 1: Write the failing test**

`tests/test_ring.py`:

```python
import json

import pytest

from lianli_panel.ring import (
    ThermalConfig, find_ring, load_thermal, save_thermal, set_off, set_static,
)

DEVICES = [
    {"device_id": "hid:513b5a7acadc4203", "name": "Universal Screen 8.8\"",
     "has_lcd": True, "has_rgb": False, "pid": 41096},
    {"device_id": "hid:0416:8050:1-8.3", "name": "LED Ring",
     "has_lcd": False, "has_rgb": True, "pid": 0x8050},
]


def test_ring_is_resolved_at_runtime_not_hardcoded(fake_client):
    """The ring's id is derived from its USB path and changes on every replug
    into a different port, unlike the LCD's stable serial."""
    fake_client.responses["ListDevices"] = DEVICES
    assert find_ring(fake_client) == "hid:0416:8050:1-8.3"


def test_missing_ring_raises(fake_client):
    fake_client.responses["ListDevices"] = [DEVICES[0]]
    with pytest.raises(RuntimeError, match="no LED ring"):
        find_ring(fake_client)


def test_static_uses_set_rgb_effect_not_set_config(fake_client):
    """SetConfig persists RGB but never applies it -- only SetRgbEffect
    reaches the hardware."""
    fake_client.responses["ListDevices"] = DEVICES
    fake_client.responses["SetRgbEffect"] = None
    set_static(fake_client, (0, 200, 255))
    assert "SetRgbEffect" in fake_client.methods()
    params = next(p for m, p in fake_client.calls if m == "SetRgbEffect")
    assert params["effect"]["mode"] == "Static"
    assert params["effect"]["colors"] == [[0, 200, 255]]


def test_off_sends_mode_off(fake_client):
    fake_client.responses["ListDevices"] = DEVICES
    fake_client.responses["SetRgbEffect"] = None
    set_off(fake_client)
    params = next(p for m, p in fake_client.calls if m == "SetRgbEffect")
    assert params["effect"]["mode"] == "Off"


def test_colour_components_are_validated(fake_client):
    fake_client.responses["ListDevices"] = DEVICES
    with pytest.raises(ValueError):
        set_static(fake_client, (0, 300, 0))


# --- thermal poller config -------------------------------------------------

def test_defaults_match_the_pollers_current_constants():
    c = ThermalConfig()
    assert (c.cool_c, c.hot_c, c.poll_ms) == (45.0, 85.0, 2000)
    assert (c.min_delta_c, c.force_refresh_s, c.brightness) == (1.0, 60, 4)


def test_missing_config_file_yields_defaults(tmp_path):
    assert load_thermal(tmp_path / "absent.json") == ThermalConfig()


def test_config_roundtrips(tmp_path):
    path = tmp_path / "thermal.json"
    save_thermal(ThermalConfig(cool_c=40.0, hot_c=90.0), path)
    assert load_thermal(path).hot_c == 90.0


def test_unknown_keys_in_the_file_are_ignored(tmp_path):
    path = tmp_path / "thermal.json"
    path.write_text(json.dumps({"cool_c": 30.0, "future_key": "x"}))
    assert load_thermal(path).cool_c == 30.0


def test_cool_must_be_below_hot(tmp_path):
    with pytest.raises(ValueError):
        save_thermal(ThermalConfig(cool_c=90.0, hot_c=40.0), tmp_path / "t.json")
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_ring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.ring'`

- [ ] **Step 3: Implement `lianli_panel/ring.py`**

```python
"""LED ring control and the thermal poller's configuration.

TWO DAEMON BUGS SHAPE THIS MODULE:

1. SetConfig PERSISTS RGB SETTINGS BUT NEVER APPLIES THEM. Only SetRgbEffect
   reaches the hardware. This is why the vendor GUI's RGB page saves correctly,
   reads back correctly, and changes nothing. Always SetRgbEffect to apply.

2. The ring reports supported_modes ["Off","Static","Direct"] -- no hardware
   effects at all. So a rainbow on the ring always means NOTHING IS DRIVING IT;
   that is the firmware default, not a mode anyone selected.

The ring's device_id is derived from its USB path (hid:0416:8050:1-8.3) and
changes on every replug into a different port. The LCD's is a stable serial.
So the ring is resolved at runtime; a hardcoded id fails after any replug.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

RING_PID = 0x8050
THERMAL_CONFIG_PATH = Path("/var/lib/lianli-panel/thermal-rgb.json")

RGB_APPLY_WARNING = (
    "The thermal poller re-drives the ring every ~2s and will overwrite a "
    "static colour. Stop lianli-thermal-rgb.service first:\n"
    "  systemctl --user stop lianli-thermal-rgb.service"
)


def find_ring(client) -> str:
    for dev in client.call("ListDevices") or []:
        if dev.get("has_rgb") and dev.get("pid") == RING_PID:
            return dev["device_id"]
    raise RuntimeError("no LED ring found; is the screen plugged in?")


def _apply(client, effect: dict) -> None:
    full = {"speed": 2, "brightness": 4, "direction": "Clockwise",
            "scope": "All", "disabled": False, **effect}
    client.call("SetRgbEffect",
                {"device_id": find_ring(client), "zone": 0, "effect": full})


def set_static(client, rgb: tuple[int, int, int], brightness: int = 4) -> None:
    if not all(0 <= int(c) <= 255 for c in rgb):
        raise ValueError(f"colour components must be 0-255, got {rgb!r}")
    _apply(client, {"mode": "Static", "colors": [[int(c) for c in rgb]],
                    "brightness": brightness})


def set_off(client) -> None:
    _apply(client, {"mode": "Off", "colors": [[0, 0, 0]]})


# --- thermal poller config -------------------------------------------------


@dataclass
class ThermalConfig:
    """Defaults are the poller's current module-level constants, so the poller
    behaves identically when this file is absent."""
    cool_c: float = 45.0
    hot_c: float = 85.0
    poll_ms: int = 2000
    min_delta_c: float = 1.0
    force_refresh_s: int = 60
    brightness: int = 4

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, obj: dict) -> "ThermalConfig":
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in obj.items() if k in known})


def load_thermal(path: Path | None = None) -> ThermalConfig:
    path = Path(path) if path is not None else THERMAL_CONFIG_PATH
    try:
        return ThermalConfig.from_json(json.loads(path.read_text()))
    except (OSError, json.JSONDecodeError, TypeError):
        return ThermalConfig()


def save_thermal(cfg: ThermalConfig, path: Path | None = None) -> None:
    if cfg.cool_c >= cfg.hot_c:
        raise ValueError(
            f"cool_c ({cfg.cool_c}) must be below hot_c ({cfg.hot_c}); the hue "
            "sweep runs from green at cool to red at hot")
    path = Path(path) if path is not None else THERMAL_CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg.to_json(), indent=1))
```

- [ ] **Step 4: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_ring.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Teach the poller to read the config file**

Modify `/var/tmp/lianli-stats/bin/thermal-rgb.py`. Replace the module-level constants block with a loader that re-reads on mtime change. Keep the constants as the defaults so the poller runs unchanged when the file is absent.

Add near the top, after the existing imports:

```python
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
```

**Where `reload_config()` goes, precisely.** `main()` has two nested loops:

```python
while True:                      # respawn loop — runs once per nvidia-smi process
    proc = gpu_stream()
    for line in proc.stdout:     # sample loop — runs once per ~2s sample
```

Call it inside the **inner** `for line in proc.stdout:` loop. Putting it on the
outer `while True:` would reload only when the long-lived `nvidia-smi` child
exits — which is approximately never — so edits would appear to be ignored.

Then read `_cfg["cool_c"]` etc. in place of the old constants. Delete the old
constant names rather than leaving them bound to stale values, so a missed
substitution raises `NameError` instead of silently using an outdated threshold.

**`poll_ms` is the exception and needs the respawn loop.** `gpu_stream()` bakes
it into the child at spawn time:

```python
f"--loop-ms={POLL_MS}"
```

so changing it cannot affect a stream that is already running. Handle it
explicitly — break out of the sample loop when it changes, letting the outer
loop respawn with the new interval:

```python
        for line in proc.stdout:
            cfg = reload_config()
            if cfg["poll_ms"] != spawned_poll_ms:
                print(f"poll_ms {spawned_poll_ms} -> {cfg['poll_ms']}; "
                      "restarting nvidia-smi", flush=True)
                proc.terminate()
                break            # outer while True respawns with the new value
```

capturing `spawned_poll_ms = _cfg["poll_ms"]` immediately before
`proc = gpu_stream()`, and passing that value into the spawn. Without this the
GUI would offer a poll-interval control that silently does nothing until the
service is restarted.

- [ ] **Step 6: Verify the poller reloads live**

Run:
```bash
systemctl --user restart lianli-thermal-rgb.service
sudo mkdir -p /var/lib/lianli-panel && sudo chown chase:chase /var/lib/lianli-panel
./.venv/bin/python -c "
from lianli_panel.ring import ThermalConfig, save_thermal
save_thermal(ThermalConfig(cool_c=40.0, hot_c=80.0))
print('wrote config')
"
sleep 5
journalctl --user -u lianli-thermal-rgb.service -n 5 --no-pager
```
Expected: a `config loaded: {...'cool_c': 40.0...}` line **without** a service restart.

Then verify the `poll_ms` special case separately, since it is the one setting
that cannot apply to a running stream:

```bash
./.venv/bin/python -c "
from lianli_panel.ring import ThermalConfig, save_thermal, load_thermal
c = load_thermal(); c.poll_ms = 3000; save_thermal(c); print('poll_ms -> 3000')
"
sleep 6
journalctl --user -u lianli-thermal-rgb.service -n 5 --no-pager
```
Expected: a `poll_ms 2000 -> 3000; restarting nvidia-smi` line. If that does not
appear, the reload is on the wrong loop.

Restore the defaults afterwards so the ring keeps its tuned behaviour:
`./.venv/bin/python -c "from lianli_panel.ring import ThermalConfig, save_thermal; save_thermal(ThermalConfig())"`

**This step writes to hardware** — the poller drives the physical ring via
`SetRgbEffect`, so changing its thresholds changes the ring's colour. Take a
snapshot first (Task 8) if the current ring behaviour matters.

The `sudo mkdir`/`chown` is a root step — hand it to Chase to run rather than attempting it.

- [ ] **Step 7: Commit**

```bash
git add lianli_panel/ring.py tests/test_ring.py
git commit -F - <<'MSG'
feat: add LED ring control and thermal poller configuration

SetConfig persists RGB settings but never applies them -- only SetRgbEffect
reaches the hardware, which is why the vendor GUI's RGB page appears to work
and changes nothing. This module always applies via SetRgbEffect.

The ring's device_id comes from its USB path and changes on every replug
into a different port, so it is resolved at runtime rather than hardcoded.

The thermal poller had no configuration at all -- six module-level
constants. It now re-reads a JSON file when the mtime changes. The loop
already wakes every 2s on the nvidia-smi stream, so the stat() is free,
edits apply live with no restart, and the defaults are the old constants so
behaviour is unchanged when the file is absent.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 11: CLI entry point

Makes the core usable and independently valuable before any GUI exists — a correct replacement for `apply.sh` and `rgb.sh`.

**Files:**
- Create: `lianli_panel/cli.py`
- Modify: `pyproject.toml` (add `[project.scripts]`)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: every module above.
- Produces: `lianli_panel.cli.main(argv: list[str] | None = None) -> int`. Subcommands: `status`, `list`, `validate <file|id>`, `apply <id>`, `preview <id> [-o PATH] [--live]`, `sensor-test <cmd>`, `ring {off|static R G B}`, `snapshot`, `revert`.

- [ ] **Step 1: Write the failing test**

`tests/test_cli.py`:

```python
import pytest

from lianli_panel.cli import build_parser


def test_parser_exposes_every_subcommand():
    p = build_parser()
    for argv in (["status"], ["list"], ["apply", "x"], ["preview", "x"],
                 ["sensor-test", "echo 1"], ["ring", "off"],
                 ["ring", "static", "0", "1", "2"], ["snapshot"], ["revert"],
                 ["validate", "f.json"]):
        assert p.parse_args(argv) is not None


def test_ring_static_requires_three_components():
    p = build_parser()
    with pytest.raises(SystemExit):
        p.parse_args(["ring", "static", "0", "1"])


def test_preview_defaults_to_substituted_not_live():
    assert build_parser().parse_args(["preview", "x"]).live is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `./.venv/bin/pytest tests/test_cli.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'lianli_panel.cli'`

- [ ] **Step 3: Implement `lianli_panel/cli.py`**

```python
"""Command line for the lianli-panel core.

A correct replacement for apply.sh and rgb.sh: it sends the whole template
library rather than one entry, always follows SetLcdTemplates with SetLcdMedia,
snapshots before applying, and reports whether the panel is actually reachable.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import apply as apply_mod
from . import health, ring, sensors, snapshot
from .ipc import Client, DaemonError
from .model import Template, validate
from .render import PreviewRenderer


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lianli-panel")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="is the panel actually reachable?")
    sub.add_parser("list", help="list stored templates")
    sub.add_parser("snapshot", help="snapshot the current configured state")
    sub.add_parser("revert", help="restore the newest snapshot")

    v = sub.add_parser("validate", help="validate a template file or stored id")
    v.add_argument("target")

    a = sub.add_parser("apply", help="make a stored template live")
    a.add_argument("template_id")

    pv = sub.add_parser("preview", help="render a template to a JPEG")
    pv.add_argument("template_id")
    pv.add_argument("-o", "--out", default="/tmp/lianli-preview.jpg")
    pv.add_argument("--live", action="store_true",
                    help="execute command sources (spawns subprocesses as lianli)")

    st = sub.add_parser("sensor-test", help="probe a candidate command sensor")
    st.add_argument("command")
    st.add_argument("-o", "--out", default="/tmp/lianli-sensor.jpg")

    r = sub.add_parser("ring", help="control the LED ring")
    rsub = r.add_subparsers(dest="ring_cmd", required=True)
    rsub.add_parser("off")
    rs = rsub.add_parser("static")
    rs.add_argument("r", type=int)
    rs.add_argument("g", type=int)
    rs.add_argument("b", type=int)

    return p


def _stored(client, template_id: str) -> dict:
    for t in client.call("GetLcdTemplates") or []:
        if t.get("id") == template_id:
            return t
    raise SystemExit(f"no stored template with id {template_id!r}")


def _lcd_fallback() -> dict | None:
    """Newest snapshotted config.lcds entry, for rebuilding a wiped array.

    Walks snapshots newest-first because the most recent one may itself have
    been taken while the array was empty. Returns None if no snapshot has ever
    recorded an entry, in which case apply_templates raises rather than
    inventing an orientation and serial.
    """
    root = snapshot.SNAPSHOT_ROOT
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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    client = Client()

    try:
        if args.cmd == "status":
            h = health.check()
            print(("PANEL OK: " if h.ok else "PANEL PROBLEM: ") + h.reason)
            return 0 if h.ok else 1

        if args.cmd == "list":
            templates, digest = apply_mod.read_templates(client)
            config = client.call("GetConfig") or {}
            live = next((e.get("template_id") for e in config.get("lcds") or []), None)
            for t in templates:
                mark = "*" if t.get("id") == live else " "
                print(f"{mark} {t.get('id'):<20} {len(t.get('widgets', []))} widgets")
            print(f"\nset hash: {digest[:16]}")
            return 0

        if args.cmd == "validate":
            target = Path(args.target)
            raw = json.loads(target.read_text()) if target.exists() \
                else _stored(client, args.target)
            problems = validate(Template.from_json(raw))
            for p in problems:
                print(f"{p.level:8} {p.widget_id:14} {p.message}")
            print(f"{len(problems)} problem(s)")
            return 1 if any(p.level == "error" for p in problems) else 0

        if args.cmd == "apply":
            snap = snapshot.take(client)
            print(f"snapshot: {snap}")
            templates, digest = apply_mod.read_templates(client)
            # The snapshot just taken records config.lcds, so it is the natural
            # source for the fallback. Without this, apply fails outright once
            # lianli-gui has wiped the array -- the exact hazard apply.py claims
            # to handle. Prefer the newest snapshot that actually has an entry,
            # since the one just taken reflects the wiped state too.
            apply_mod.apply_templates(
                client, templates, args.template_id, base_hash=digest,
                lcd_entry_fallback=_lcd_fallback())
            h = health.check()
            print(f"applied {args.template_id}")
            print(("panel OK: " if h.ok else "WARNING: ") + h.reason)
            return 0 if h.ok else 1

        if args.cmd == "preview":
            tpl = _stored(client, args.template_id)
            jpeg = PreviewRenderer(client).render(tpl, live=args.live)
            Path(args.out).write_bytes(jpeg)
            mode = "live (commands executed)" if args.live else "substituted"
            print(f"wrote {args.out} ({len(jpeg)} bytes, {mode})")
            return 0

        if args.cmd == "sensor-test":
            d = sensors.run_diagnostic(args.command)
            print(f"-- diagnostic (runs as you, NOT authoritative) --")
            print(f"exit {d.exit_code}  parsed {d.parsed}")
            if d.stdout.strip():
                print(f"stdout: {d.stdout.strip()[:200]}")
            if d.stderr.strip():
                print(f"stderr: {d.stderr.strip()[:200]}")
            for p in d.problems:
                print(f"  ! {p}")
            jpeg = sensors.render_authoritative(client, args.command)
            Path(args.out).write_bytes(jpeg)
            print(f"-- authoritative: {args.out} — this is what the daemon reads --")
            return 0

        if args.cmd == "snapshot":
            print(snapshot.take(client))
            return 0

        if args.cmd == "revert":
            newest = snapshot.latest()
            if newest is None:
                print("no snapshots")
                return 1
            data = snapshot.load(newest)
            entry = next(iter(data.get("lcds") or []), None)
            if entry is None or entry.get("template_id") is None:
                print(f"snapshot {newest.name} records no live template")
                return 1
            live = entry["template_id"]
            # Restore the snapshotted LCD entry too, not just the templates --
            # orientation and serial live there, and reusing the CURRENT entry
            # would silently keep a wiped or edited one.
            apply_mod.apply_templates(client, data["templates"], live,
                                      lcd_entry_fallback=entry)
            print(f"restored templates and LCD entry from {newest.name} "
                  f"(live: {live})")
            # Say plainly what was NOT restored. Re-applying RGB here would
            # fight the thermal poller, which re-drives the ring every ~2s.
            print("NOT restored: RGB configuration, ring state, and the thermal "
                  "service's on/off state. Reverting those would be overwritten "
                  "by lianli-thermal-rgb.service within seconds; stop it first "
                  "and use `ring` if you need them back.")
            if data.get("thermal_service_active"):
                print(f"  (thermal service was active when {newest.name} "
                      "was taken)")
            return 0

        if args.cmd == "ring":
            if args.ring_cmd == "off":
                ring.set_off(client)
                print("ring off")
            else:
                ring.set_static(client, (args.r, args.g, args.b))
                print(f"ring static {args.r},{args.g},{args.b}")
            print(ring.RGB_APPLY_WARNING)
            return 0

    except (DaemonError, apply_mod.ApplyFailed, apply_mod.ConflictError,
            RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Add the console script to `pyproject.toml`**

```toml
[project.scripts]
lianli-panel = "lianli_panel.cli:main"
```

Declaring the entry point does not create it — the project must be installed for
the wrapper to appear on PATH:

```bash
./.venv/bin/pip install -e . --no-deps
./.venv/bin/lianli-panel --help
```

`--no-deps` because there are no runtime dependencies to resolve and the sandbox
in which tasks may run has no network. Expected: the usage text listing every
subcommand. Without this step `./.venv/bin/lianli-panel` does not exist and only
`python -m lianli_panel.cli` works.

- [ ] **Step 5: Run to verify it passes**

Run: `./.venv/bin/pytest tests/test_cli.py -v`
Expected: PASS, 3 passed

- [ ] **Step 6: Exercise the read-only subcommands against the real daemon**

Run:
```bash
./.venv/bin/python -m lianli_panel.cli status
./.venv/bin/python -m lianli_panel.cli list
./.venv/bin/python -m lianli_panel.cli validate gaming-dash
./.venv/bin/python -m lianli_panel.cli preview gaming-dash -o /tmp/dash.jpg
```
Expected: `PANEL OK`, `gaming-dash` marked live with 31 widgets, `0 problem(s)`, and a JPEG written. **Open `/tmp/dash.jpg` and look at it** — it should resemble the physical panel, except that command-driven values (FPS, VRAM, RAM) read 0 because they were substituted. That difference is the substitution working, not a bug.

Do not run `apply` or `ring` here; Task 12 covers hardware changes.

- [ ] **Step 7: Commit**

```bash
git add lianli_panel/cli.py tests/test_cli.py pyproject.toml
git commit -F - <<'MSG'
feat: add CLI covering status, apply, preview, sensors and ring

Makes the core independently useful before any GUI exists, and correctly
replaces apply.sh and rgb.sh: sends the whole template library rather than a
single entry, always pairs SetLcdTemplates with SetLcdMedia, snapshots
before applying, and reports whether the panel is actually reachable rather
than trusting an ok status.

preview defaults to substituted sources; --live is opt-in because it spawns
subprocesses as uid lianli.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

### Task 12: Asset relocation and end-to-end hardware verification

This task changes the physical panel, and it is the only one whose completion criterion is a look at the screen.

It is **not** the only task that writes to hardware: Task 10 restarts the thermal poller and changes its thresholds, and that poller drives the LED ring via `SetRgbEffect`. Everything else before this point is confined to previews, `FakeClient`, and read-only calls.

**Files:**
- Create: `docs/install.md`, `tools/migrate_assets.sh`
- Modify: none in `lianli_panel/`

- [ ] **Step 1: Write the migration script**

`tools/migrate_assets.sh`:

```bash
#!/bin/sh
# Move daemon-readable assets out of /var/tmp.
#
# WHY: /usr/lib/tmpfiles.d/tmp.conf carries `q /var/tmp 1777 root root 30d`, so
# systemd ages out /var/tmp after 30 days. The sensor scripts are read every
# second so their atime keeps them alive, but build_template.py and apply.sh are
# touched only by hand -- after 30 quiet days they can vanish, taking the ability
# to rebuild the dash, while the panel keeps running and reports nothing wrong.
#
# The destination must stay outside $HOME: the daemon runs as uid lianli and
# cannot traverse /home/chase (mode 0700).
#
# Two directories, split by who writes them:
#   /usr/local/share/lianli-panel  root:root 755  scripts shipped with the app
#   /var/lib/lianli-panel          chase:chase 755  sensors the GUI authors
#
# RUN AS ROOT.
set -eu

SHIP=/usr/local/share/lianli-panel
USER_DIR=/var/lib/lianli-panel
SRC=/var/tmp/lianli-stats

install -d -o root -g root -m 755 "$SHIP"
install -d -o chase -g chase -m 755 "$USER_DIR"

if [ -d "$SRC/bin" ]; then
    cp -a "$SRC/bin/." "$SHIP/"
    chown -R root:root "$SHIP"
    chmod -R a+rX "$SHIP"
    echo "copied $SRC/bin -> $SHIP"
fi

echo
echo "Done. Originals left in place -- verify the panel still works, then remove"
echo "them by hand. Any template referencing $SRC must be repointed to $SHIP"
echo "before the originals are deleted."
```

- [ ] **Step 2: Hand the root steps to Chase**

These need root and must not be attempted by an agent. Present them exactly:

```bash
sudo sh ~/Documents/Code/lianli-panel/tools/migrate_assets.sh
```

Wait for confirmation that it ran before continuing.

- [ ] **Step 3: Verify both directories are readable by the daemon's uid**

Run:
```bash
sudo -u lianli test -r /usr/local/share/lianli-panel/fps.sh && echo "SHIP readable by lianli" || echo "SHIP NOT readable"
sudo -u lianli test -x /var/lib/lianli-panel && echo "USER_DIR traversable by lianli" || echo "USER_DIR NOT traversable"
```

If `sudo -u lianli` is unavailable, use the authoritative sensor probe instead — it runs as `lianli` by construction:

```bash
./.venv/bin/python -m lianli_panel.cli sensor-test /usr/local/share/lianli-panel/vram_gb.sh -o /tmp/reachable.jpg
```
Open `/tmp/reachable.jpg`: a non-zero number means the daemon can read the new location.

- [ ] **Step 4: Snapshot before touching the panel**

Run: `./.venv/bin/python -m lianli_panel.cli snapshot`
Expected: a path is printed. **Do not proceed without this** — Step 5 is the first hardware write.

- [ ] **Step 5: Apply the existing template through the new code path**

Run:
```bash
./.venv/bin/python -m lianli_panel.cli status
./.venv/bin/python -m lianli_panel.cli apply gaming-dash
```
Expected: `applied gaming-dash` then `panel OK: ...`.

- [ ] **Step 6: LOOK AT THE PHYSICAL SCREEN**

This is the completion criterion for the whole plan. Tests passing is not evidence the app works.

Confirm on the panel itself:
- The dash is rendering, not the Lian Li firmware splash.
- GPU and CPU temperature rings show plausible live values.
- The clock is ticking (proving the render loop is live, not a frozen frame).

If the panel shows the splash, `status` will say so; the fix is
`sudo systemctl restart lianli-daemon-system.service`, then restart the ring driver so the LED comes back under control:
`systemctl --user restart lianli-thermal-rgb.service`.

(There is no `ring apply` subcommand — Task 11 defines only `ring off` and `ring static R G B`. The poller owns the ring's colour now, so restarting it is the equivalent of the old `rgb.sh apply`. A rainbow ring means nothing is driving it.)

Then confirm the journal shows a real open, not just a prepare:

```bash
journalctl -u lianli-daemon-system.service -n 30 --no-pager -o short-iso | grep -E 'lcd::core|Prepared'
```
`Prepared custom template` alone proves nothing — the LCD open line is the evidence.

- [ ] **Step 7: Verify revert works**

Run:
```bash
./.venv/bin/python -m lianli_panel.cli revert
./.venv/bin/python -m lianli_panel.cli status
```
Expected: the snapshot is restored and the panel still renders. Look at the screen again.

- [ ] **Step 8: Write `docs/install.md`**

Record: the two directories and why they are split, the root commands, the `/var/tmp` ageing rationale, the `sudo rm` cleanup for any leftover probe logs, and that `build_template.py` is retained as a seed script documenting how `gaming-dash` was derived rather than deleted.

- [ ] **Step 9: Commit**

```bash
git add tools/migrate_assets.sh docs/install.md
git commit -F - <<'MSG'
feat: relocate daemon assets off /var/tmp and verify end to end

/var/tmp is aged out after 30 days by systemd-tmpfiles. The sensor scripts
survive on atime because they run every second, but build_template.py and
apply.sh are touched only by hand and can vanish -- taking the ability to
rebuild the dash while the panel keeps running and reports nothing wrong.

Split by writer: /usr/local/share/lianli-panel (root, shipped scripts) and
/var/lib/lianli-panel (chase, GUI-authored sensors). A root-owned tree alone
would have made every new sensor a root step, contradicting the goal of
authoring sensors from the GUI.

Verified against the hardware, not just the test suite: applied through the
new code path and confirmed the dash renders live on the physical panel with
a real lcd::core open line in the journal.

Claude-Session: https://claude.ai/code/session_01XsUauWCJRxPswbkc8E2Zne
MSG
```

---

## Self-Review

**Spec coverage.** Every section of the design spec maps to a task: the preview-as-canvas bet and command substitution (5), data ownership and the template library's whole-set write (7), schema reference (2), enforced invariants (3, 4, 7), sensor validation (9), panel health (6), safety and snapshots (7, 8), asset relocation (12), ring and poller config (10). The five *surfaces* are deliberately absent — they are Qt views and belong to the follow-up plan. This plan builds every interface those views consume.

**Deferred to the GUI plan:** canvas hit-testing and drag rules, inspector forms driven by `schema.py`, the draft/dirty lifecycle, the `lianli-gui`-running banner, and the theme. Each depends only on interfaces frozen here.

**Known gap, stated rather than hidden.** `schema.py`'s `observed_optional` is not exhaustive, because the daemon ignores unknown fields and cannot be asked to enumerate them. The inspector will therefore render required fields confidently and optional ones only where a stored template has demonstrated them. Task 2's docstring says so; the GUI plan must not assume completeness.

**Verification.** Tasks 1, 4, 5, 6, 8, 9, 10, 11 and 12 each end with a check against the real daemon, not just pytest. Task 5's is the most important — it proves the substitution actually prevents execution rather than merely being coded to.

**Two tasks write to hardware.** Task 10 drives the LED ring (the thermal poller calls `SetRgbEffect`), and Task 12 drives the panel. Task 12 is the only one whose completion criterion is looking at the screen. Everything else is confined to previews, `FakeClient`, and read-only calls.
