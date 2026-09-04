# Installation and asset layout

## Why assets moved off `/var/tmp`

`/usr/lib/tmpfiles.d/tmp.conf` carries `q /var/tmp 1777 root root 30d`, so
systemd ages out anything under `/var/tmp` after 30 days of inactivity. The
sensor scripts (`fps.sh`, `ram_gb.sh`, `vram_gb.sh`, ...) are read every
second by the running templates, so their atime keeps them alive — but
`build_template.py` and `apply.sh` are touched only by hand. After 30 quiet
days they can vanish, taking with them the ability to rebuild the dash,
while the panel keeps running and reports nothing wrong.

## Where things live now

Two directories, split by who writes them:

| Directory | Owner | Mode | Contents |
|---|---|---|---|
| `/usr/local/share/lianli-panel` | `root:root` | `755` | scripts shipped with the app (`fps.sh`, `ram_gb.sh`, `vram_gb.sh`, `graph.sh`, `idle.sh`, `prune.sh`, `restore-sensor-mode.sh`, `rgb.sh`, `thermal-rgb.py`, `apply.sh`, `build_template.py`, `ipc.py`) |
| `/var/lib/lianli-panel` | `chase:chase` | `755` | sensors authored from the GUI, and the thermal poller's live-reloadable config (`thermal-rgb.json`) |

Both directories are outside `$HOME` on purpose: the daemon runs as uid
`lianli` and cannot traverse `/home/chase` (mode `0700`). A single
root-owned tree would have made every new sensor a root step, which
contradicts the goal of authoring sensors from the GUI — hence the split.

`build_template.py` is retained under `/usr/local/share/lianli-panel` as a
seed script, not deleted. It documents how `gaming-dash` was originally
derived and is a reference for building future templates, even though it
is not consumed at runtime.

## Running the migration

Root commands, run once:

```bash
sudo sh tools/migrate_assets.sh
```

This creates both directories with the correct ownership/mode and copies
`/var/tmp/lianli-stats/bin/*` into `/usr/local/share/lianli-panel`,
re-owning the copies to `root:root`. It leaves the originals under
`/var/tmp` in place — verify the panel still works against the new
location, repoint any template that references the old `/var/tmp` path,
and only then remove the originals by hand.

## Cleaning up leftover probe logs

`/var/tmp/lianli-stats/logs/` accumulates CSV output from ad hoc sensor
probes (PresentMon-style capture logs, seed launcher runs) that are not
part of the running templates and are not touched by the migration script.
These can be removed once you no longer need them:

```bash
sudo rm -rf /var/tmp/lianli-stats/logs/*
```

`/var/tmp/lianli-stats/state/` holds small per-poller marker files
(some owned by `chase`, some written by the daemon as `lianli`) used for
staleness checks; leave it in place — it is small and self-maintaining.

## Verifying the daemon can read the new location

If `sudo -u lianli` is available:

```bash
sudo -u lianli test -r /usr/local/share/lianli-panel/fps.sh && echo "SHIP readable by lianli"
sudo -u lianli test -x /var/lib/lianli-panel && echo "USER_DIR traversable by lianli"
```

Otherwise, use the authoritative sensor probe, which runs as `lianli` by
construction:

```bash
./.venv/bin/python -m lianli_panel.cli sensor-test /usr/local/share/lianli-panel/vram_gb.sh -o /tmp/reachable.jpg
```

A non-zero number in the resulting JPEG means the daemon can read the new
location.
