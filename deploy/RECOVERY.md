# DuckLab Operations — Backup Restore / Recovery

Backups are consistent SQLite snapshots produced by the online backup API
(`/usr/local/bin/ducklab-backup` → `/var/lib/ducklab/backups/store_ops.<ts>.db`).
They are safe to take while the app is running (WAL-aware). The backup job runs
as the `ducklab` user; the backup directory is owned by `ducklab` (mode 700)
and backup files are mode 600 — the main app never needs root for backups.

## Restore procedure (application)

Use **Settings → Data → Restore Database** as Super Admin. Restore first keeps
an automatic pre-restore backup, then validates and migrates a temporary copy
of the uploaded database. Validation includes SQLite integrity, foreign keys,
required tables and columns, and schema compatibility. Only after every check
and pending migration succeeds does an atomic replacement update the live DB;
a failed restore leaves it unchanged. The pre-restore backup is retained.

## Verify a backup is restorable (non-destructive test)

Restore to a **disposable** path and integrity-check it — never touch the live DB:

```bash
TMP=$(mktemp -d)
cp /var/lib/ducklab/backups/store_ops.<timestamp>.db "$TMP/restore.db"
python3 - "$TMP/restore.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
print("foreign keys:", c.execute("PRAGMA foreign_key_check").fetchall())
print("users:", c.execute("SELECT COUNT(*) FROM auth_users").fetchone()[0])
print("schema_version:", c.execute("PRAGMA user_version").fetchone()[0])
c.close()
PY
rm -rf "$TMP"
```

## Retention

`DUCKLAB_BACKUP_KEEP` (default 14) nightly snapshots are kept; older ones are
pruned by the backup script. Adjust in `/etc/ducklab/ducklab.env`.

## Backup failure visibility

- `journalctl -u ducklab-backup` shows each run.
- `systemctl status ducklab-backup.service` shows the last result.
- Add an `OnFailure=` notification unit if alerting is required (deferred).
