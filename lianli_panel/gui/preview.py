"""Rendering off the UI thread.

A preview is a ~0.30s blocking IPC round trip. On the UI thread that is a
visible freeze on every drag frame, so the call lives on a QThread and results
come back as signals.

The debounce and in-flight rules are render.Coalescer's, not this module's.
What this module adds is the POLL: a request arriving after a render finished
but inside the debounce window has nothing to release it, so due() must be
asked on a timer or the final state of a drag never renders.

request() is the automatic path and always substitutes command sources for
constants. refresh_live() is the only path that executes them, and it exists
because the user asked for it explicitly.
"""
from __future__ import annotations

import time

from PySide6.QtCore import QObject, QThread, QTimer, Signal, Slot

from ..render import Coalescer, PreviewRenderer


class _Job(QObject):
    done = Signal(bytes)
    failed = Signal(str)

    def __init__(self, renderer: PreviewRenderer) -> None:
        super().__init__()
        self.renderer = renderer

    @Slot(dict, bool)
    def run(self, payload: dict, live: bool) -> None:
        try:
            self.done.emit(self.renderer.render(payload, live=live))
        except Exception as exc:               # any daemon or decode failure
            self.failed.emit(str(exc))


class PreviewWorker(QObject):
    rendered = Signal(bytes)
    failed = Signal(str)
    _submit = Signal(dict, bool)

    def __init__(self, client, parent=None, interval_s: float = 0.25,
                 poll_ms: int = 50) -> None:
        super().__init__(parent)
        self.renderer = PreviewRenderer(client)
        self.coalescer = Coalescer(interval_s)
        self._payload: dict | None = None

        self._thread = QThread()
        self._job = _Job(self.renderer)
        self._job.moveToThread(self._thread)
        self._submit.connect(self._job.run)
        self._job.done.connect(self._on_done)
        self._job.failed.connect(self._on_failed)
        self._thread.start()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._timer.start(poll_ms)

    def request(self, payload: dict) -> None:
        """Debounced. Command sources are substituted for constants."""
        self._payload = payload
        if self.coalescer.request(time.monotonic()):
            self._submit.emit(payload, False)

    def refresh_live(self, payload: dict) -> bool:
        """EXECUTES command sources as uid lianli. Explicit user action only.

        Returns False when a render is already in flight, so the caller can say
        'busy' instead of stacking a second 0.3s call behind the first.
        """
        if self.coalescer.in_flight:
            return False
        self._payload = payload
        self.coalescer.request(time.monotonic())
        self._submit.emit(payload, True)
        return True

    def _poll(self) -> None:
        if self.coalescer.due(time.monotonic()) and self._payload is not None:
            self._submit.emit(self._payload, False)

    def _on_done(self, jpeg: bytes) -> None:
        if self.coalescer.finish(time.monotonic()) and self._payload is not None:
            self._submit.emit(self._payload, False)
        self.rendered.emit(jpeg)

    def _on_failed(self, message: str) -> None:
        self.coalescer.finish(time.monotonic())
        self.failed.emit(message)

    def stop(self) -> None:
        self._timer.stop()
        self._thread.quit()
        self._thread.wait(2000)
