import json
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


class FakeClient:
    """Stands in for ipc.Client. Records calls; returns canned data.

    Used everywhere a test would otherwise mutate the real device.
    """

    def __init__(self, responses=None):
        self.responses = dict(responses or {})
        self.calls = []

    def call(self, method, params=None):
        self.calls.append((method, params or {}))
        value = self.responses.get(method)
        if isinstance(value, Exception):
            raise value
        if callable(value):
            return value(params or {})
        return value

    def methods(self):
        return [m for m, _ in self.calls]


@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture(scope="session")
def qapp():
    """One QApplication per process. Qt aborts on a second one, and pytest
    runs every test file in the same process."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def dash_json():
    """The real 31-widget gaming-dash template: every tricky construct in one
    document -- cover bars, self-gating alpha-0 ranges, command sources."""
    return json.loads((FIXTURES / "gaming-dash.json").read_text())
