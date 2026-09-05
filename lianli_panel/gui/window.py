"""The main window.

Layout: template library on the left, the daemon's own render in the middle,
inspector on the right. Later tasks fill the left and right panes in; this task
establishes the wiring -- load from the daemon, render, show.

The daemon is treated as unreliable ON PURPOSE. After a replug its encoder can
be dead while every IPC call still returns ok, so a failure to load is a banner
and an empty draft, never a traceback at startup.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import (QHBoxLayout, QLabel, QListWidget, QMainWindow,
                               QVBoxLayout, QWidget)

from ..apply import read_templates
from ..ipc import DaemonError
from .draft import Draft
from .preview import PreviewWorker

TITLE = "lianli-panel"


class MainWindow(QMainWindow):
    def __init__(self, client) -> None:
        super().__init__()
        self.client = client
        self.setWindowTitle(TITLE)
        self.resize(1500, 700)
        self.frame_bytes: bytes | None = None

        self.banner = QLabel("")
        self.banner.setWordWrap(True)
        self.banner.setStyleSheet(
            "background:#5a1d1d; color:#ffd9d9; padding:6px;")
        self.banner.hide()

        self.template_list = QListWidget()
        self.template_list.setMaximumWidth(240)
        self.template_list.currentTextChanged.connect(self._choose_template)

        self.canvas = QLabel("no frame yet")
        self.canvas.setMinimumSize(960, 240)
        self.canvas.setStyleSheet("background:#000;")

        body = QHBoxLayout()
        body.addWidget(self.template_list)
        body.addWidget(self.canvas, 1)

        root = QVBoxLayout()
        root.addWidget(self.banner)
        root.addLayout(body, 1)
        holder = QWidget()
        holder.setLayout(root)
        self.setCentralWidget(holder)

        self.worker = PreviewWorker(client, parent=self)
        self.worker.rendered.connect(self.set_frame)
        self.worker.failed.connect(self._render_failed)

        self.draft = Draft([], None)
        self.load()

    # --- daemon ------------------------------------------------------------

    def load(self) -> None:
        try:
            templates, _ = read_templates(self.client)
            config = self.client.call("GetConfig") or {}
        except DaemonError as exc:
            self.draft = Draft([], None)
            self._warn(f"the daemon did not answer: {exc}. Nothing is loaded "
                       f"and nothing can be applied.")
            return
        live = next((e.get("template_id") for e in config.get("lcds") or []), None)
        self.draft = Draft(templates, live)
        self.template_list.clear()
        self.template_list.addItems([t.id for t in self.draft.templates])
        if self.draft.current_id:
            self.template_list.setCurrentRow(
                [t.id for t in self.draft.templates].index(self.draft.current_id))
        self.rerender()

    def rerender(self) -> None:
        current = self.draft.current()
        if current is not None:
            self.worker.request(current.to_json())

    # --- slots -------------------------------------------------------------

    def _choose_template(self, template_id: str) -> None:
        if template_id and template_id != self.draft.current_id:
            self.draft.current_id = template_id
            self.draft.selection = None
            self.rerender()

    def set_frame(self, jpeg: bytes) -> None:
        self.frame_bytes = jpeg
        image = QImage.fromData(jpeg, "JPEG")
        if not image.isNull():
            self.canvas.setPixmap(QPixmap.fromImage(image).scaled(
                self.canvas.size(), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation))

    def _render_failed(self, message: str) -> None:
        self._warn(f"preview render failed: {message}")

    def _warn(self, message: str) -> None:
        self.banner.setText(message)
        self.banner.show()

    def closeEvent(self, event) -> None:
        self.worker.stop()
        super().closeEvent(event)
