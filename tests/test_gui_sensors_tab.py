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
    assert tab.command_group.isHidden()
    tab.select("gpu-temp")
    assert not tab.command_group.isHidden()


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
