"""Banners and the poller behind them.

The banner is a STACK, not a label, and that is the point of the module: the
single QLabel it replaces showed only the most recent message and never
cleared, so a health warning would erase a render error and a condition that
resolved stayed on screen until the app was restarted.
"""
import threading
import time

import pytest

from lianli_panel import health
from lianli_panel.gui.status import BannerStack, HealthPoller


def _wait(qapp, pred, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end and not pred():
        qapp.processEvents()
        time.sleep(0.005)
    return pred()


def test_a_stack_with_no_banners_is_hidden(qapp):
    assert BannerStack().isHidden()


def test_two_sources_do_not_overwrite_each_other(qapp):
    s = BannerStack()
    s.show_banner("health", "the panel handle is dead")
    s.show_banner("vendor-gui", "lianli-gui is running", "warn")
    assert sorted(s.keys()) == ["health", "vendor-gui"]
    assert "dead" in s.text() and "lianli-gui" in s.text()


def test_clearing_one_key_leaves_the_other(qapp):
    s = BannerStack()
    s.show_banner("health", "dead")
    s.show_banner("render", "render failed")
    s.clear("health")
    assert s.keys() == ["render"]
    assert "dead" not in s.text()


def test_clearing_the_last_banner_hides_the_stack(qapp):
    s = BannerStack()
    s.show_banner("health", "dead")
    s.clear("health")
    assert s.keys() == []
    assert s.isHidden()


def test_setting_the_same_key_twice_replaces_rather_than_stacks(qapp):
    s = BannerStack()
    s.show_banner("health", "first")
    s.show_banner("health", "second")
    assert s.keys() == ["health"]
    assert s.text() == "second"


def test_clearing_a_key_that_was_never_set_is_not_an_error(qapp):
    BannerStack().clear("nothing")


def test_the_poller_reports_what_the_probes_returned(qapp):
    report = health.PanelHealth(False, "the panel was disconnected")
    p = HealthPoller(check=lambda: report, scan=lambda: [42], interval_ms=0)
    got = []
    p.reported.connect(lambda r, pids: got.append((r, pids)))
    p.poll()
    assert _wait(qapp, lambda: got)
    assert got[0][0].reason == "the panel was disconnected"
    assert got[0][1] == [42]
    p.stop()


def test_a_probe_that_raises_becomes_an_unhealthy_report(qapp):
    """journalctl can be missing, slow, or refuse. That must degrade to a
    banner saying the CHECK failed -- never to a silent 'healthy', and never
    to an exception on a worker thread with no handler."""
    def boom():
        raise OSError("journalctl not found")

    p = HealthPoller(check=boom, scan=lambda: [], interval_ms=0)
    got = []
    p.reported.connect(lambda r, pids: got.append(r))
    p.poll()
    assert _wait(qapp, lambda: got)
    assert got[0].ok is False
    assert "journalctl not found" in got[0].reason
    p.stop()


def test_a_poll_while_one_is_running_is_dropped(qapp):
    """A journal read of a long-lived boot is not instant. Queued polls would
    stack on the one worker thread and deliver stale reports in a burst."""
    gate = threading.Event()
    calls = []

    def slow():
        calls.append(1)
        gate.wait(3.0)
        return health.PanelHealth(True, "ok")

    p = HealthPoller(check=slow, scan=lambda: [], interval_ms=0)
    p.poll()
    assert _wait(qapp, lambda: calls, timeout=3.0)
    p.poll()
    p.poll()
    gate.set()
    time.sleep(0.1)
    qapp.processEvents()
    assert len(calls) == 1
    p.stop()
