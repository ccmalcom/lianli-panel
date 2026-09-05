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
