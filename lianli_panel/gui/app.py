"""Entry point.

Dark palette to match the KDE desktop. The window is created even when the
daemon is unreachable -- see MainWindow.load.
"""
from __future__ import annotations

import sys

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..ipc import Client
from .window import MainWindow


def _dark(app: QApplication) -> None:
    app.setStyle("Fusion")
    p = QPalette()
    p.setColor(QPalette.Window, QColor(24, 26, 30))
    p.setColor(QPalette.WindowText, QColor(226, 228, 232))
    p.setColor(QPalette.Base, QColor(18, 20, 24))
    p.setColor(QPalette.AlternateBase, QColor(30, 33, 38))
    p.setColor(QPalette.Text, QColor(226, 228, 232))
    p.setColor(QPalette.Button, QColor(38, 41, 47))
    p.setColor(QPalette.ButtonText, QColor(226, 228, 232))
    p.setColor(QPalette.Highlight, QColor(64, 120, 200))
    p.setColor(QPalette.HighlightedText, QColor(255, 255, 255))
    app.setPalette(p)


def main(argv: list[str] | None = None) -> int:
    app = QApplication(argv if argv is not None else sys.argv)
    _dark(app)
    window = MainWindow(Client())
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
