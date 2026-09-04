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
