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
