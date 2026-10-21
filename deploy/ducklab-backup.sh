#!/usr/bin/env bash
# DuckLab Operations — SQLite backup (online, consistent).
#
# Uses SQLite's online backup API via the Python sqlite3 module, which is safe
# to run against a live database (including WAL mode) — it produces a
# consistent snapshot without naively copying a file mid-write.
#
# Install: /usr/local/bin/ducklab-backup (0755 root), run by ducklab-backup.timer
# Config:  reads the same /etc/ducklab/ducklab.env as the service.
#
# Env overrides (for testing):
#   DUCKLAB_DB_PATH       source database (default /var/lib/ducklab/store_ops.db)
#   DUCKLAB_BACKUP_DIR    destination dir   (default /var/lib/ducklab/backups)
#   DUCKLAB_BACKUP_KEEP   number of backups to retain (default 14)

set -euo pipefail

DB_PATH="${DUCKLAB_DB_PATH:-/var/lib/ducklab/store_ops.db}"
BACKUP_DIR="${DUCKLAB_BACKUP_DIR:-/var/lib/ducklab/backups}"
KEEP="${DUCKLAB_BACKUP_KEEP:-14}"

if [[ ! -f "$DB_PATH" ]]; then
    echo "ducklab-backup: source database not found: $DB_PATH" >&2
    exit 1
fi

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TMP_OUT="$BACKUP_DIR/.store_ops.$STAMP.db.tmp"
OUT="$BACKUP_DIR/store_ops.$STAMP.db"

# Consistent online backup via the SQLite backup API.
DB_PATH="$DB_PATH" TMP_OUT="$TMP_OUT" python3 - <<'PY'
import os, sqlite3, sys
src = os.environ["DB_PATH"]
dst = os.environ["TMP_OUT"]
s = sqlite3.connect(src)
try:
    d = sqlite3.connect(dst)
    try:
        s.backup(d)          # online backup API: consistent, WAL-safe
        d.commit()
    finally:
        d.close()
finally:
    s.close()
PY

chmod 600 "$TMP_OUT"
mv "$TMP_OUT" "$OUT"
echo "ducklab-backup: wrote $OUT"

# Retention: keep the newest KEEP backups, delete older ones.
ls -1t "$BACKUP_DIR"/store_ops.*.db 2>/dev/null | tail -n +"$((KEEP + 1))" | \
    while read -r old; do rm -f "$old"; echo "ducklab-backup: pruned $old"; done
