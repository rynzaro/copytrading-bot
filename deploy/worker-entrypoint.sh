#!/bin/sh
# Shared data dir: worker (uid 10001) and web (uid 10002) share group 10001. Keys stay worker-only (0700/0600).
# SQLite creates files 0644 and gives -wal/-shm the main file's mode, so create the DB 0660 up front.
set -e
umask 007
chmod 2770 "$DATA_DIR" 2>/dev/null || echo "warn: cannot chmod $DATA_DIR (owner should be 10001:10001, mode 2770)" >&2
db="$DATA_DIR/worker.db"
[ -e "$db" ] || : > "$db"
chmod 660 "$db" 2>/dev/null || true
exec python -m copytrade_worker
