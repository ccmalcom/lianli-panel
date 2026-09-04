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
