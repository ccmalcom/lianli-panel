"""The main window.

Layout: template library on the left, the daemon's own render in the middle,
inspector on the right. Later tasks fill the left and right panes in; this task
establishes the wiring -- load from the daemon, render, show.

The daemon is treated as unreliable ON PURPOSE. After a replug its encoder can
be dead while every IPC call still returns ok, so a failure to load is a banner
and an empty draft, never a traceback at startup.
"""
from __future__ import annotations

from PySide6.QtWidgets import (QApplication, QHBoxLayout, QMainWindow,
                               QMessageBox, QToolBar, QVBoxLayout, QWidget)

from .. import health
from .. import apply as apply_mod
from .. import snapshot
from ..apply import read_templates
from ..ipc import DaemonError
from ..model import validate
from .canvas import Canvas
from .draft import Draft
from .inspector import Inspector
from .preview import PreviewWorker
from .sidebar import TemplateList, WidgetList
from .status import BannerStack, HealthPoller

TITLE = "lianli-panel"


class MainWindow(QMainWindow):
    def __init__(self, client, *, health_poller=None) -> None:
        super().__init__()
        self.client = client
        self.setWindowTitle(TITLE)
        self.resize(1500, 700)
        self.frame_bytes: bytes | None = None

        self.banner = BannerStack()

        self.template_list = TemplateList()
        self.template_list.chosen.connect(self._choose_template)
        self.template_list.made_live.connect(self._set_live)
        self.template_list.created.connect(self._new_template)
        self.template_list.duplicated.connect(self._duplicate_template)
        self.template_list.renamed.connect(self._rename_template)
        self.template_list.deleted.connect(self._delete_template)

        self.canvas = Canvas()
        self.canvas.selection_changed.connect(self._select)
        self.canvas.edit_started.connect(self._begin_edit)
        self.canvas.geometry_changed.connect(self._move_widget)
        self.canvas.edit_finished.connect(self.rerender)

        self.inspector = Inspector()
        self.inspector.changed.connect(self._field_edited)
        self.inspector.structure_changed.connect(self._structure_edited)

        self.widget_list = WidgetList()
        self.widget_list.selected.connect(self._select_from_list)
        self.widget_list.reordered.connect(self._reorder)
        self.widget_list.deleted.connect(self._delete_widget)
        self.widget_list.duplicated.connect(self._duplicate_widget)

        left = QVBoxLayout()
        left.addWidget(self.template_list)
        left.addWidget(self.widget_list, 1)
        left_holder = QWidget()
        left_holder.setLayout(left)
        left_holder.setMaximumWidth(300)

        self.inspector.setMaximumWidth(360)

        body = QHBoxLayout()
        body.addWidget(left_holder)
        body.addWidget(self.canvas, 1)
        body.addWidget(self.inspector)

        root = QVBoxLayout()
        root.addWidget(self.banner)
        root.addLayout(body, 1)
        holder = QWidget()
        holder.setLayout(root)
        self.setCentralWidget(holder)

        self.worker = PreviewWorker(client, parent=self)
        self.worker.rendered.connect(self.set_frame)
        self.worker.failed.connect(self._render_failed)

        # Injectable so tests do not shell out to journalctl. The connection is
        # made BEFORE the first poll or the first report is lost.
        self.health = health_poller or HealthPoller(parent=self)
        self.health.reported.connect(self._health_reported)
        self.health.poll()

        bar = QToolBar()
        bar.addAction("Apply", self.apply_now)
        bar.addAction("Revert", self.revert)
        bar.addAction("Refresh (runs sensors)", self.refresh_live)
        bar.addAction("Re-check panel", self.health.poll)
        bar.addAction("Undo", lambda: (self.draft.undo(), self._refresh_lists()))
        bar.addAction("Redo", lambda: (self.draft.redo(), self._refresh_lists()))
        self.addToolBar(bar)

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
        self.banner.clear("daemon")
        live = next((e.get("template_id") for e in config.get("lcds") or []), None)
        self.draft = Draft(templates, live)
        self._refresh_lists()

    def rerender(self) -> None:
        current = self.draft.current()
        if current is not None:
            self.worker.request(current.to_json())

    # --- slots -------------------------------------------------------------

    def _choose_template(self, template_id: str) -> None:
        if template_id and template_id != self.draft.current_id:
            self.draft.current_id = template_id
            self.draft.selection = None
            self._refresh_lists()

    # --- library -----------------------------------------------------------

    def _set_live(self, tid: str) -> None:
        self.draft.set_live(tid)
        self._refresh_lists()

    def _new_template(self) -> None:
        self.draft.current_id = self.draft.add_template("new template")
        self._refresh_lists()

    def _duplicate_template(self, tid: str) -> None:
        self.draft.current_id = self.draft.duplicate_template(tid)
        self._refresh_lists()

    def _rename_template(self, tid: str, name: str) -> None:
        self.draft.rename_template(tid, name)
        self._refresh_lists()

    def _delete_template(self, tid: str) -> None:
        try:
            self.draft.delete_template(tid)
        except ValueError as exc:
            QMessageBox.warning(self, "Cannot delete", str(exc))
            return
        self._refresh_lists()

    # --- applying ----------------------------------------------------------

    def apply_now(self) -> None:
        """The ONLY write path. Snapshot, then apply_templates, which sends the
        whole set and follows SetLcdTemplates with SetLcdMedia as one
        transaction."""
        current = self.draft.current()
        if current is not None:
            errors = [p for p in validate(current) if p.level == "error"]
            if errors:
                listing = "\n".join(f"{p.widget_id}: {p.message}" for p in errors)
                if QMessageBox.question(
                        self, "Apply anyway?",
                        f"This template has errors:\n\n{listing}") != QMessageBox.Yes:
                    return
        try:
            snap = snapshot.take(self.client)
        except Exception as exc:               # a snapshot must never block a fix
            snap = None
            self._warn(f"could not snapshot before applying: {exc}")
        try:
            apply_mod.apply_templates(
                self.client, self.draft.payload(), self.draft.live_id,
                base_hash=self.draft.base_hash,
                lcd_entry_fallback=apply_mod.lcd_entry_fallback())
        except apply_mod.ConflictError as exc:
            if QMessageBox.question(
                    self, "The daemon's templates changed",
                    f"{exc}\n\nOverwrite their change with this draft?"
            ) != QMessageBox.Yes:
                return
            self.draft.base_hash = apply_mod.read_templates(self.client)[1]
            self.apply_now()
            return
        except apply_mod.ApplyFailed as exc:
            QMessageBox.critical(self, "Apply failed", str(exc))
            return
        self.draft.mark_applied(self.draft.payload())
        self.health.poll()
        self.statusBar().showMessage(
            f"applied · live: {self.draft.live_id}"
            + (f" · snapshot {snap.name}" if snap else ""), 10000)

    def revert(self) -> None:
        newest = snapshot.latest()
        if newest is None:
            QMessageBox.information(self, "Revert", "no snapshots yet")
            return
        data = snapshot.load(newest)
        entry = next(iter(data.get("lcds") or []), None)
        if entry is None or entry.get("template_id") is None:
            QMessageBox.warning(self, "Revert",
                                f"snapshot {newest.name} records no live template")
            return
        if QMessageBox.question(
                self, "Revert?",
                f"Restore templates and the LCD entry from {newest.name}?\n\n"
                "NOT restored: RGB configuration, ring state, and the thermal "
                "service's on/off state — the poller re-drives the ring every "
                "~2s and would overwrite them within seconds."
        ) != QMessageBox.Yes:
            return
        apply_mod.apply_templates(self.client, data["templates"],
                                  entry["template_id"],
                                  lcd_entry_fallback=entry)
        self.load()

    def refresh_live(self) -> None:
        """Explicitly execute command sources once. Automatic renders never do:
        gaming-dash spawns 16 subprocesses per render, and graph.sh writes the
        state file the LIVE panel's sparkline reads."""
        current = self.draft.current()
        if current is None:
            return
        if not self.worker.refresh_live(current.to_json()):
            self.statusBar().showMessage("a render is already in flight", 3000)

    def set_frame(self, jpeg: bytes) -> None:
        self.banner.clear("render")
        self.frame_bytes = jpeg
        self.canvas.set_frame(jpeg)

    def _select(self, widget_id: str) -> None:
        self.draft.selection = widget_id or None
        self.inspector.set_widget(self.draft.widget(widget_id) if widget_id else None)

    def _select_from_list(self, widget_id: str) -> None:
        self.canvas.set_selection(widget_id or None)
        self._select(widget_id)

    def _field_edited(self) -> None:
        self.draft.dirty = True
        self.rerender()

    def _structure_edited(self) -> None:
        self.draft.dirty = True
        self.inspector.set_widget(self.draft.widget(self.draft.selection or ""))
        self.rerender()

    def _reorder(self, widget_id: str, delta: int) -> None:
        self.draft.reorder_widget(widget_id, delta)
        self._refresh_lists()

    def _delete_widget(self, widget_id: str) -> None:
        self.draft.delete_widget(widget_id)
        self.inspector.set_widget(None)
        self._refresh_lists()

    def _duplicate_widget(self, widget_id: str) -> None:
        self.draft.duplicate_widget(widget_id)
        self._refresh_lists()

    def _refresh_lists(self) -> None:
        self.template_list.set_draft(self.draft)
        self.widget_list.set_draft(self.draft)
        self.canvas.set_widgets(self.draft.rects())
        self.rerender()

    def _begin_edit(self) -> None:
        """One checkpoint per drag, taken on press. Without this a single drag
        leaves ~40 undo entries and ctrl-Z stops being usable."""
        self.draft.checkpoint()

    def _move_widget(self, wid: str, x: float, y: float,
                     w: float, h: float) -> None:
        self.draft.set_geometry(wid, x, y, w, h, checkpoint=False)
        self.canvas.set_widgets(self.draft.rects())
        self.rerender()

    def _render_failed(self, message: str) -> None:
        self._warn(f"preview render failed: {message}", key="render")

    def _warn(self, message: str, key: str = "daemon") -> None:
        self.banner.show_banner(key, message)

    # --- health and interlock ----------------------------------------------

    def _health_reported(self, report, vendor_pids) -> None:
        if report.ok:
            self.banner.clear("health")
        else:
            self.banner.show_banner(
                "health",
                f"{report.reason}\n\nThis is a heuristic read of the journal, "
                "not a read of the device — it can be wrong in both directions.",
                "error",
                action=("Copy the fix", self._copy_restart_hint))
        if vendor_pids:
            pids = ", ".join(str(p) for p in vendor_pids)
            self.banner.show_banner(
                "vendor-gui", f"{health.VENDOR_GUI_WARNING} (pid {pids})",
                "warn", action=("Re-check config.lcds", self.verify_lcd_entry))
        else:
            self.banner.clear("vendor-gui")

    def _copy_restart_hint(self) -> None:
        QApplication.clipboard().setText(
            health.RESTART_HINT.split("#")[0].strip())
        self.statusBar().showMessage(
            "restart command copied to the clipboard", 8000)

    def verify_lcd_entry(self) -> None:
        """READ-ONLY. Applying is what repairs the entry; this only says
        whether it needs repairing, so pressing it while lianli-gui is still
        open cannot make anything worse."""
        try:
            config = self.client.call("GetConfig") or {}
        except DaemonError as exc:
            self._warn(f"could not read the config: {exc}", key="config")
            return
        problem = health.config_lcds_problem(config, apply_mod.LCD_SERIAL)
        if problem is None:
            self.banner.clear("config")
            self.statusBar().showMessage(
                "config.lcds still carries this panel's entry", 8000)
            return
        self.banner.show_banner("config", problem, "error")

    def closeEvent(self, event) -> None:
        if self.isVisible() and self.draft.dirty and QMessageBox.question(
                self, "Unapplied changes",
                "This draft has changes that were never applied. Close anyway?"
        ) != QMessageBox.Yes:
            event.ignore()
            return
        self.worker.stop()
        self.health.stop()
        super().closeEvent(event)
