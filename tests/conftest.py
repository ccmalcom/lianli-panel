import pytest


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
