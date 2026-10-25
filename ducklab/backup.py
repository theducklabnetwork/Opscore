"""Backup & restore for the Opscore store-ops database (Settings > Data).

Super Admin only (enforced by the caller via authz.require_user_manager).

Backup: a consistent copy produced with SQLite's online backup API —
safe while the app is running, no file-copy races with open writers.

Restore: the uploaded file is validated before anything is replaced —
it must be a real SQLite database, pass PRAGMA integrity_check, contain
the tables this application needs, and not carry a schema version newer
than this code (older schemas migrate on next startup via init_db()).

Before any restore, the current database is copied (backup API again)
into a ``backups/`` folder next to the database file; those automatic
pre-restore copies are listed for the UI. Restore then swaps the
database content using the backup API into a live destination file.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ducklab import store_ops_db

BACKUP_DIRNAME = "backups"

# Minimum set of tables a valid Opscore database must carry. The auth
# tables (logins/passwords) live in the same database file, so a restore
# also replaces identities — the UI warns about that.
EXPECTED_TABLES = (
    "schema_migrations",
    "stores",
    "expense_categories",
    "stock_loss_codes",
    "monthly_configs",
    "monthly_expense_budgets",
    "daily_records",
    "daily_stock_losses",
    "expense_transactions",
    "actions",
    "monthly_snapshots",
    "auth_users",
    "auth_roles",
    "auth_user_roles",
    "auth_sessions",
)


class BackupError(Exception):
    """Any backup/restore failure; message is safe to show in the UI."""


@dataclass(frozen=True)
class BackupFile:
    name: str
    path: Path
    size_bytes: int
    created_at: datetime


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def create_backup(db_path: Path | str, dest: Path | str) -> Path:
    """Consistent copy of db_path at dest via the SQLite backup API."""
    db_path, dest = Path(db_path), Path(dest)
    if not db_path.exists():
        raise BackupError(f"Database not found: {db_path}")
    src = _connect(db_path)
    try:
        dst = _connect(dest)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return dest


def download_name(now: datetime | None = None) -> str:
    """opscore-backup-YYYY-MM-DD-HHMM.db (local time)."""
    now = now or datetime.now()
    return f"opscore-backup-{now.strftime('%Y-%m-%d-%H%M')}.db"


def validate_restore(path: Path | str) -> int:
    """Validate an uploaded .db for restore; returns its schema version.

    Raises BackupError with a user-safe message on any rejection.
    """
    path = Path(path)
    if not path.exists():
        raise BackupError("Uploaded file not found.")
    try:
        with open(path, "rb") as fh:
            header = fh.read(16)
    except OSError as exc:
        raise BackupError(f"Cannot read the uploaded file: {exc}")
    if header != b"SQLite format 3\x00":
        raise BackupError("Not a SQLite database file.")
    try:
        conn = _connect(path)
    except sqlite3.Error as exc:
        raise BackupError(f"Cannot open the file as SQLite: {exc}")
    try:
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        except sqlite3.Error as exc:
            raise BackupError(f"Integrity check failed: {exc}")
        if not ok:
            raise BackupError("Integrity check failed: the file is corrupt.")
        tables = {
            r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = [t for t in EXPECTED_TABLES if t not in tables]
        if missing:
            raise BackupError(
                "Missing expected tables: " + ", ".join(missing))
        version = conn.execute("PRAGMA user_version").fetchone()[0]
    finally:
        conn.close()
    if version > store_ops_db.SCHEMA_VERSION:
        raise BackupError(
            f"Schema version v{version} is newer than this app "
            f"(v{store_ops_db.SCHEMA_VERSION}); upgrade the app first.")
    return version


def _backups_dir(db_path: Path | str) -> Path:
    return Path(db_path).parent / BACKUP_DIRNAME


def save_pre_restore_backup(db_path: Path | str,
                            now: datetime | None = None) -> Path:
    """Copy the current database into backups/ before a restore."""
    now = now or datetime.now(timezone.utc)
    dest_dir = _backups_dir(db_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"pre-restore-{now.strftime('%Y%m%d-%H%M%S')}.db"
    return create_backup(db_path, dest)


def restore(db_path: Path | str, upload_path: Path | str) -> int:
    """Replace db_path content with the validated upload (backup API).

    Returns the restored schema version. The caller must already have
    saved a pre-restore backup and obtained user confirmation.
    """
    db_path, upload_path = Path(db_path), Path(upload_path)
    version = validate_restore(upload_path)
    src = _connect(upload_path)
    try:
        dst = _connect(db_path)
        try:
            src.backup(dst)
        finally:
            dst.close()
    finally:
        src.close()
    return version


_SAFE_NAME = re.compile(r"^pre-restore-\d{8}-\d{6}\.db$")


def list_pre_restore_backups(db_path: Path | str, limit: int = 5
                             ) -> list[BackupFile]:
    """Newest first; only files written by save_pre_restore_backup."""
    dest_dir = _backups_dir(db_path)
    out: list[BackupFile] = []
    if not dest_dir.is_dir():
        return out
    for p in dest_dir.iterdir():
        if not p.is_file() or not _SAFE_NAME.match(p.name):
            continue
        try:
            stat = p.stat()
            created = datetime.fromtimestamp(stat.st_mtime)
            out.append(BackupFile(p.name, p, stat.st_size, created))
        except OSError:
            continue
    out.sort(key=lambda b: b.name, reverse=True)
    return out[:limit]
