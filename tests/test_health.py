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


from pathlib import Path

from lianli_panel.health import config_lcds_problem, vendor_gui_pids

SERIAL = "hid:513b5a7acadc4203"


def _proc(root: Path, pid: int, comm: str, exe: str | None = None) -> None:
    """One fake /proc/<pid>. The exe symlink is deliberately dangling: real
    /proc/<pid>/exe points at a path this test has no business creating, and
    vendor_gui_pids reads the link rather than following it."""
    d = root / str(pid)
    d.mkdir()
    (d / "comm").write_text(comm + "\n")
    if exe is not None:
        (d / "exe").symlink_to(exe)


def test_vendor_gui_found_by_comm(tmp_path):
    _proc(tmp_path, 101, "lianli-gui")
    _proc(tmp_path, 102, "bash")
    (tmp_path / "not-a-pid").mkdir()
    assert vendor_gui_pids(("lianli-gui",), tmp_path) == [101]


def test_vendor_gui_found_by_exe_when_comm_is_truncated(tmp_path):
    """The kernel truncates comm to 15 characters, so a longer binary name
    never compares equal to itself. The exe symlink carries the full name."""
    _proc(tmp_path, 201, "lianli-gui-lon", exe="/usr/bin/lianli-gui-longname")
    assert vendor_gui_pids(("lianli-gui-longname",), tmp_path) == [201]


def test_vendor_gui_ignores_an_unreadable_process(tmp_path):
    """/proc/<pid>/exe is unreadable for another user's processes, and a
    process can exit mid-scan. Neither may raise."""
    d = tmp_path / "301"
    d.mkdir()                       # no comm, no exe: a process that vanished
    assert vendor_gui_pids(("lianli-gui",), tmp_path) == []


def test_a_healthy_lcd_entry_reports_no_problem():
    config = {"lcds": [{"serial": SERIAL, "type": "custom",
                        "template_id": "gaming-dash", "orientation": 90}]}
    assert config_lcds_problem(config, SERIAL) is None


def test_an_empty_lcds_array_is_the_vendor_gui_wipe():
    assert "EMPTY" in config_lcds_problem({"lcds": []}, SERIAL)


def test_an_entry_in_media_mode_is_reported():
    config = {"lcds": [{"serial": SERIAL, "type": "media",
                        "template_id": "gaming-dash"}]}
    problem = config_lcds_problem(config, SERIAL)
    assert problem is not None and "custom" in problem
