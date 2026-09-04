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
