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
    this to a >= 0 check.

    Confirmed against the live daemon (not a stall -- STALLED was empty on
    extraction, and each of these rendered successfully on the FIRST probe
    attempt, before any "missing field" error could even occur):
      - clock_analog: a self-contained clock face; hand colours etc. are all
        optional with defaults.
      - nvidia_gpu, amd_gpu_usage, cpu_usage, mem_usage, mem_used, mem_free:
        single global metrics with no parameters to identify a specific
        device -- unlike hwmon/network_rx/network_tx/disk_read/disk_write,
        which all require naming which sensor, interface, or device.
    """
    FIELDLESS_BY_DESIGN = {
        "clock_analog", "nvidia_gpu", "amd_gpu_usage",
        "cpu_usage", "mem_usage", "mem_used", "mem_free",
    }
    empty = [n for n, s in {**schema.WIDGET_KINDS, **schema.SOURCE_TYPES}.items()
             if not s.required and n not in FIELDLESS_BY_DESIGN]
    assert empty == [], f"extraction stalled for: {empty}"
