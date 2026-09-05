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
