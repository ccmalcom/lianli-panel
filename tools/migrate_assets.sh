#!/bin/sh
# Move daemon-readable assets out of /var/tmp.
#
# WHY: /usr/lib/tmpfiles.d/tmp.conf carries `q /var/tmp 1777 root root 30d`, so
# systemd ages out /var/tmp after 30 days. The sensor scripts are read every
# second so their atime keeps them alive, but build_template.py and apply.sh are
# touched only by hand -- after 30 quiet days they can vanish, taking the ability
# to rebuild the dash, while the panel keeps running and reports nothing wrong.
#
# The destination must stay outside $HOME: the daemon runs as uid lianli and
# cannot traverse /home/chase (mode 0700).
#
# Two directories, split by who writes them:
#   /usr/local/share/lianli-panel  root:root 755  scripts shipped with the app
#   /var/lib/lianli-panel          chase:chase 755  sensors the GUI authors
#
# RUN AS ROOT.
set -eu

SHIP=/usr/local/share/lianli-panel
USER_DIR=/var/lib/lianli-panel
SRC=/var/tmp/lianli-stats

install -d -o root -g root -m 755 "$SHIP"
install -d -o chase -g chase -m 755 "$USER_DIR"

if [ -d "$SRC/bin" ]; then
    cp -a "$SRC/bin/." "$SHIP/"
    chown -R root:root "$SHIP"
    chmod -R a+rX "$SHIP"
    echo "copied $SRC/bin -> $SHIP"
fi

echo
echo "Done. Originals left in place -- verify the panel still works, then remove"
echo "them by hand. Any template referencing $SRC must be repointed to $SHIP"
echo "before the originals are deleted."
