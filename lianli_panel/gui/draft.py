"""The in-memory draft.

NOTHING reaches the daemon until Apply. There is no auto-save.

The draft holds the WHOLE template set, not the template being edited, because
SetLcdTemplates replaces the entire stored set -- an editor that held one
template would delete every other one on save. base_hash is captured when the
draft opens and handed to apply_templates, which refuses to write when the
daemon's set has moved underneath it (apply.sh, lianli-gui, or a second copy of
this app).

Undo is snapshot-based: whole-payload deep copies. The set is ~20KB of JSON and
edits are human-paced, so the simple thing is fast enough, and it cannot drift
from the model the way a command-log can.
"""
from __future__ import annotations

import copy

from ..apply import templates_hash
from ..model import Template, Widget
from . import geometry as geo

UNDO_DEPTH = 50


def _unique(existing: set[str], base: str) -> str:
    if base not in existing:
        return base
    n = 2
    while f"{base}-{n}" in existing:
        n += 1
    return f"{base}-{n}"


class Draft:
    def __init__(self, templates: list[dict], live_id: str | None) -> None:
        self.templates = [Template.from_json(t) for t in templates]
        self.live_id = live_id
        self.base_hash = templates_hash(templates)
        self.current_id = live_id or (self.templates[0].id if self.templates else None)
        self.selection: str | None = None
        self.dirty = False
        self._undo: list[tuple[list[dict], str | None, str | None]] = []
        self._redo: list[tuple[list[dict], str | None, str | None]] = []

    # --- reading -----------------------------------------------------------

    def payload(self) -> list[dict]:
        return [t.to_json() for t in self.templates]

    def current(self) -> Template | None:
        return next((t for t in self.templates if t.id == self.current_id), None)

    def widget(self, wid: str) -> Widget | None:
        t = self.current()
        return t.widget(wid) if t else None

    def rects(self) -> list[tuple[str, geo.Rect]]:
        """In draw order, which is what hit_test expects."""
        t = self.current()
        if t is None:
            return []
        return [(w.id, geo.to_rect(w.x, w.y, w.width, w.height)) for w in t.widgets]

    # --- undo --------------------------------------------------------------

    def _state(self) -> tuple[list[dict], str | None, str | None]:
        return (copy.deepcopy(self.payload()), self.live_id, self.current_id)

    def _restore(self, state) -> None:
        payload, live, current = state
        self.templates = [Template.from_json(t) for t in payload]
        self.live_id, self.current_id = live, current

    def checkpoint(self) -> None:
        self._undo.append(self._state())
        del self._undo[:-UNDO_DEPTH]
        self._redo.clear()

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(self._state())
        self._restore(self._undo.pop())
        self.dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(self._state())
        self._restore(self._redo.pop())
        self.dirty = True
        return True

    def _touch(self, checkpoint: bool = True) -> None:
        if checkpoint:
            self.checkpoint()
        self.dirty = True

    # --- widget edits ------------------------------------------------------

    def set_geometry(self, wid: str, x: float, y: float, w: float, h: float,
                     *, checkpoint: bool = True) -> None:
        """checkpoint=False for the intermediate frames of a drag; the caller
        checkpoints once on press so one drag is one undo step."""
        target = self.widget(wid)
        if target is None:
            raise KeyError(f"no widget {wid!r} in template {self.current_id!r}")
        if checkpoint:
            self.checkpoint()
        target.x, target.y, target.width, target.height = x, y, w, h
        self.dirty = True

    def delete_widget(self, wid: str) -> None:
        t = self.current()
        if t is None:
            return
        self._touch()
        t.widgets = [w for w in t.widgets if w.id != wid]
        if self.selection == wid:
            self.selection = None

    def duplicate_widget(self, wid: str) -> str:
        t = self.current()
        source = t.widget(wid) if t else None
        if t is None or source is None:
            raise KeyError(f"no widget {wid!r}")
        self._touch()
        clone = Widget.from_json(copy.deepcopy(source.to_json()))
        clone.id = _unique({w.id for w in t.widgets}, f"{wid}-copy")
        clone.x += 20.0
        clone.y += 20.0
        t.widgets.append(clone)          # on top: array order IS draw order
        return clone.id

    def reorder_widget(self, wid: str, delta: int) -> None:
        """Draw order is load-bearing: only the last widget covering a rect is
        visible. Moving one is a real edit, not a cosmetic list sort."""
        t = self.current()
        if t is None:
            return
        ids = [w.id for w in t.widgets]
        if wid not in ids:
            raise KeyError(f"no widget {wid!r}")
        i = ids.index(wid)
        j = max(0, min(len(ids) - 1, i + delta))
        if i == j:
            return
        self._touch()
        t.widgets.insert(j, t.widgets.pop(i))

    # --- template edits ---------------------------------------------------

    def add_template(self, name: str) -> str:
        self._touch()
        tid = _unique({t.id for t in self.templates},
                      name.lower().replace(" ", "-") or "template")
        self.templates.append(Template(
            id=tid, name=name, base_width=int(geo.BASE_W),
            base_height=int(geo.BASE_H), rotated=True,
            background={"type": "color", "rgb": [10, 13, 20, 255]}, widgets=[]))
        return tid

    def duplicate_template(self, tid: str) -> str:
        source = next((t for t in self.templates if t.id == tid), None)
        if source is None:
            raise KeyError(f"no template {tid!r}")
        self._touch()
        clone = Template.from_json(copy.deepcopy(source.to_json()))
        clone.id = _unique({t.id for t in self.templates}, f"{tid}-copy")
        clone.name = f"{source.name} copy"
        self.templates.append(clone)
        return clone.id

    def rename_template(self, tid: str, name: str) -> None:
        target = next((t for t in self.templates if t.id == tid), None)
        if target is None:
            raise KeyError(f"no template {tid!r}")
        self._touch()
        target.name = name               # the id never changes; template_id points at it

    def delete_template(self, tid: str) -> None:
        if len(self.templates) <= 1:
            raise ValueError("cannot delete the last template; the panel would "
                             "have nothing to render")
        self._touch()
        self.templates = [t for t in self.templates if t.id != tid]
        if self.live_id == tid:
            self.live_id = self.templates[0].id
        if self.current_id == tid:
            self.current_id = self.templates[0].id

    def set_live(self, tid: str) -> None:
        if not any(t.id == tid for t in self.templates):
            raise KeyError(f"no template {tid!r}")
        self._touch()
        self.live_id = tid

    # --- after a successful apply -----------------------------------------

    def mark_applied(self, templates: list[dict]) -> None:
        """Rebase on what the daemon now holds, so the next apply's conflict
        check compares against the right thing."""
        self.base_hash = templates_hash(templates)
        self.dirty = False


# --- cover-bar analysis ----------------------------------------------------
#
# There is NO conditional visibility. Two mechanisms fake it, and both depend
# on array order:
#   * a horizontal_bar with value_max == 1 is an opaque on/off COVER
#   * a widget with an alpha-0 SensorRange gates ITSELF
# The working stack is [needs covering] -> [cover] -> [self-gating]. Reorder a
# cover behind what it covers and the hidden thing reappears -- which renders
# perfectly and simply looks wrong, so nothing reports it but this.


def _is_cover(w: Widget) -> bool:
    return w.kind_type == "horizontal_bar" and w.kind.get("value_max") == 1


def _self_gates(w: Widget) -> bool:
    ranges = w.kind.get("ranges")
    if not isinstance(ranges, list):
        return False
    return any(isinstance(r, dict) and r.get("alpha") == 0 for r in ranges)


def _overlaps(a: Widget, b: Widget) -> bool:
    ra = geo.to_rect(a.x, a.y, a.width, a.height)
    rb = geo.to_rect(b.x, b.y, b.width, b.height)
    return not (ra.right <= rb.left or rb.right <= ra.left
                or ra.bottom <= rb.top or rb.bottom <= ra.top)


def cover_warnings(t: Template) -> list[str]:
    out: list[str] = []
    for i, cover in enumerate(t.widgets):
        if not _is_cover(cover):
            continue
        before = [w for w in t.widgets[:i] if _overlaps(cover, w)]
        after = [w for w in t.widgets[i + 1:]
                 if _overlaps(cover, w) and not _self_gates(w)]
        for w in after:
            out.append(
                f"{w.id!r} overlaps the cover {cover.id!r} but is drawn AFTER "
                f"it, so the cover cannot hide it. Move the cover later in the "
                f"widget list.")
        if len(before) > 1:
            names = ", ".join(repr(w.id) for w in before)
            out.append(
                f"the cover {cover.id!r} sits over {len(before)} widgets "
                f"({names}); only one widget per rect can be conditionally "
                f"hidden — the rest are hidden unconditionally.")
    return out
