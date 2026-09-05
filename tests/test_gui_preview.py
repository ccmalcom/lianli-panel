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
