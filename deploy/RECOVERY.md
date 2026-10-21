# DuckLab Operations — Backup Restore / Recovery

Backups are consistent SQLite snapshots produced by the online backup API
(`/usr/local/bin/ducklab-backup` → `/var/lib/ducklab/backups/store_ops.<ts>.db`).
They are safe to take while the app is running (WAL-aware). The backup job runs
as the `ducklab` user; the backup directory is owned by `ducklab` (mode 700)
and backup files are mode 600 — the main app never needs root for backups.

## Restore procedure (live DB)

> Destructive to current state — confirm you have a fresh backup of the
> *current* DB before restoring an older one.

```bash
# 1. Stop the app so no writes occur during restore.
sudo systemctl stop ducklab

# 2. Safety-copy the CURRENT database aside (never lose it).
sudo -u ducklab cp -a /var/lib/ducklab/store_ops.db \
          /var/lib/ducklab/store_ops.pre-restore.$(date -u +%Y%m%dT%H%M%SZ).db

# 3. Restore the chosen backup into place.
sudo install -o ducklab -g ducklab -m 600 \
  /var/lib/ducklab/backups/store_ops.<timestamp>.db \
  /var/lib/ducklab/store_ops.db

# 4. Restart and verify.
sudo systemctl start ducklab
journalctl -u ducklab -f
curl -s http://127.0.0.1:3000/ping
```

## Verify a backup is restorable (non-destructive test)

Restore to a **disposable** path and integrity-check it — never touch the live DB:

```bash
TMP=$(mktemp -d)
cp /var/lib/ducklab/backups/store_ops.<timestamp>.db "$TMP/restore.db"
python3 - "$TMP/restore.db" <<'PY'
import sqlite3, sys
c = sqlite3.connect(sys.argv[1])
print("integrity:", c.execute("PRAGMA integrity_check").fetchone()[0])
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
