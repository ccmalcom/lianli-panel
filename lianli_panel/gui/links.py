"""Which widget is bound to which named sensor.

THE LINK CANNOT LIVE IN THE TEMPLATE. The daemon parses templates into Rust
structs and silently drops keys it does not recognise, so a "_sensor" marker
stamped onto a source object is gone by the next GetLcdTemplates -- not with an
error, just gone. So the map is a side-car file keyed by (template_id,
widget_id).

A side-car map goes stale the moment anything else edits a template -- apply.sh,
lianli-gui, or a second copy of this app -- and it has no way to be told. So it
is VALIDATED ON LOAD rather than trusted: a link survives only if the widget's
source still equals the library's source for that name.

Being told a widget is unlinked when it was linked is a small annoyance. Being
told it is bound to "gpu-temp" when its cmd is something else entirely is the
bug this validation exists to prevent, and it is the kind that survives review
because everything looks right.

STORED FORMAT is nested by template so the file stays readable by hand:

    {"gaming-dash": {"gpu-text": "gpu-temp"}}
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from pathlib import Path

LINKS_PATH = Path("~/.config/lianli-panel/sensor-links.json").expanduser()

Link = tuple[str, str]      # (template_id, widget_id)


@dataclass
class Dropped:
    link: Link
    name: str
    reason: str


def load(path: Path | None = None) -> dict[Link, str]:
    path = Path(path) if path is not None else LINKS_PATH
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[Link, str] = {}
    if not isinstance(raw, dict):
        return {}
    for template_id, widgets in raw.items():
        if isinstance(widgets, dict):
            for widget_id, name in widgets.items():
                if isinstance(name, str):
                    out[(str(template_id), str(widget_id))] = name
    return out


def save(links: dict[Link, str], path: Path | None = None) -> None:
    path = Path(path) if path is not None else LINKS_PATH
    nested: dict[str, dict[str, str]] = {}
    for (template_id, widget_id), name in links.items():
        nested.setdefault(template_id, {})[widget_id] = name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(nested, indent=1, sort_keys=True))


def bind(links: dict[Link, str], template_id: str, widget_id: str,
         name: str) -> dict[Link, str]:
    return {**links, (template_id, widget_id): name}


def unbind(links: dict[Link, str], template_id: str,
           widget_id: str) -> dict[Link, str]:
    return {k: v for k, v in links.items() if k != (template_id, widget_id)}


def widgets_using(links: dict[Link, str], name: str) -> list[Link]:
    return [link for link, bound in links.items() if bound == name]


def rename_sensor(links: dict[Link, str], old: str,
                  new: str) -> dict[Link, str]:
    return {k: (new if v == old else v) for k, v in links.items()}


def _widget(templates: list[dict], template_id: str,
            widget_id: str) -> dict | None:
    for tpl in templates:
        if tpl.get("id") != template_id:
            continue
        for widget in tpl.get("widgets") or []:
            if widget.get("id") == widget_id:
                return widget
        return None
    return None


def source_of(templates: list[dict], template_id: str,
              widget_id: str) -> dict | None:
    widget = _widget(templates, template_id, widget_id)
    if widget is None:
        return None
    source = (widget.get("kind") or {}).get("source")
    return source if isinstance(source, dict) else None


def validate(links: dict[Link, str], templates: list[dict],
             sensors: dict) -> tuple[dict[Link, str], list[Dropped]]:
    """Keep only the links the templates still support.

    Every drop reason is phrased so the UI can show it verbatim: the user needs
    to know their binding went away and why, not merely that a widget now reads
    "(custom)".
    """
    known = {tpl.get("id") for tpl in templates}
    kept: dict[Link, str] = {}
    dropped: list[Dropped] = []

    for link, name in links.items():
        template_id, widget_id = link
        if template_id not in known:
            dropped.append(Dropped(link, name,
                                   f"template {template_id!r} no longer exists"))
            continue
        if name not in sensors:
            dropped.append(Dropped(
                link, name,
                f"sensor {name!r} is no longer in the library"))
            continue
        if _widget(templates, template_id, widget_id) is None:
            dropped.append(Dropped(
                link, name,
                f"widget {widget_id!r} is gone from template {template_id!r}"))
            continue
        source = source_of(templates, template_id, widget_id)
        if source is None:
            dropped.append(Dropped(
                link, name,
                f"widget {widget_id!r} no longer has a source"))
            continue
        if source != sensors[name].source:
            dropped.append(Dropped(
                link, name,
                f"widget {widget_id!r} no longer matches sensor {name!r} — "
                "something outside this app edited it"))
            continue
        kept[link] = name

    return kept, dropped


def propagate(templates: list[dict], links: dict[Link, str], name: str,
              source: dict) -> list[Link]:
    """Rewrite every widget bound to `name`, in place. Returns what it touched.

    Each widget gets its OWN copy: sharing one dict across widgets means a
    later edit to one silently edits them all, and the daemon would receive
    aliased objects it has no reason to expect.
    """
    touched: list[Link] = []
    for link, bound in links.items():
        if bound != name:
            continue
        widget = _widget(templates, link[0], link[1])
        if widget is None or not isinstance(widget.get("kind"), dict):
            continue
        widget["kind"]["source"] = copy.deepcopy(source)
        touched.append(link)
    return touched
