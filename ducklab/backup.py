"""Backup & restore for the Opscore store-ops database (Settings > Data).

Super Admin only (enforced by the caller via authz.require_user_manager).

Backup: a consistent copy produced with SQLite's online backup API —
safe while the app is running, no file-copy races with open writers.

Restore: a temporary copy of the uploaded file is validated and migrated
before anything is replaced. It must be a real SQLite database, pass
PRAGMA integrity_check and PRAGMA foreign_key_check, contain the tables
and columns this application needs, and not carry a newer schema version.

Before migrations and restores, the current database is copied (backup API
again) into a ``backups/`` folder next to the database file. The newest ten
automatic copies are listed for the UI and retained. Only a fully validated
and migrated temporary copy atomically replaces the live database.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ducklab import store_ops_db

BACKUP_DIRNAME = "backups"
AUTOMATIC_BACKUP_RETENTION = 10
log = logging.getLogger(__name__)

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

# Tables and identifiers used throughout the application, not just their
# container tables. Columns added by old migrations are checked after those
# migrations have run on the temporary restore copy.
EXPECTED_COLUMNS = {
    "schema_migrations": ("version", "applied_at"),
    "stores": (
        "id", "code", "name", "concept", "zone", "zone_pic",
        "branch_manager", "active", "state",
    ),
    "expense_categories": (
        "id", "store_id", "name", "sort_order",
    ),
    "stock_loss_codes": ("code", "name", "contribution_sign"),
    "monthly_configs": (
        "id", "store_id", "month", "min_rtm_target_cents",
        "level1_base_cents", "level2_pct", "level3_pct", "level4_pct",
        "sales_last_year_cents", "sales_last_month_cents",
        "salary_ratio_target", "budget_headcount", "turnover_threshold",
        "stock_loss_target", "opening_headcount_ft", "part_timer_factor",
        "salary_basic_allowance_cents", "overtime_cents",
        "support_team_cost_cents", "target_transactions",
        "target_spr_cents", "target_ipr",
    ),
    "monthly_expense_budgets": (
        "id", "store_id", "monthly_config_id", "category_id",
        "budget_pct",
    ),
    "daily_records": (
        "id", "store_id", "date", "sales_cents", "transactions", "units",
        "resigned_ft", "new_hires_ft", "ft_working", "pt_working",
        "leave_annual", "leave_mc", "leave_other_paid", "leave_unpaid",
    ),
    "daily_stock_losses": (
        "id", "daily_record_id", "code", "amount_cents",
    ),
    "expense_transactions": (
        "id", "store_id", "incurred_date", "paid_date", "category_id",
        "description", "amount_cents", "paid_by", "status", "receipt_ref",
        "verification", "recorded_by", "remarks",
    ),
    "actions": (
        "id", "store_id", "raised_date", "pillar", "issue", "priority",
        "status", "pic", "due_date", "corrective_action", "completion_date",
        "remarks", "created_by", "created_at", "updated_by", "updated_at",
    ),
    "monthly_snapshots": (
        "id", "store_id", "month", "version", "kpi_json", "finalized",
        "superseded_by", "created_at",
    ),
    "auth_users": (
        "id", "email", "display_name", "password_hash", "is_active",
        "password_changed_at", "created_at", "updated_at", "last_login_at",
    ),
    "auth_roles": ("id", "name"),
    "auth_user_roles": ("user_id", "role_id", "assigned_at"),
    "auth_sessions": (
        "id", "token_hash", "user_id", "created_at", "expires_at",
        "revoked_at",
    ),
}


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


def _validate_schema(conn: sqlite3.Connection) -> None:
    """Check the tables and columns required by the application."""
    tables = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")
    }
    missing = [t for t in EXPECTED_TABLES if t not in tables]
    if missing:
        raise BackupError("Missing expected tables: " + ", ".join(missing))

    missing_columns = []
    for table, expected in EXPECTED_COLUMNS.items():
        columns = {
            r[1] for r in conn.execute(f'PRAGMA table_info("{table}")')
        }
        missing_columns.extend(
            f"{table}.{column}" for column in expected
            if column not in columns
        )
    if missing_columns:
        raise BackupError(
            "Missing expected columns: " + ", ".join(missing_columns))


def validate_restore(path: Path | str) -> int:
    """Validate an uploaded .db for restore; returns its schema version.

    Raises BackupError with a user-safe message on any rejection. This
    function is read-only; restore migrations run on a temporary copy.
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
        try:
            violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        except sqlite3.Error as exc:
            raise BackupError(f"Foreign-key check failed: {exc}")
        if violations:
            raise BackupError(
                "Foreign-key check failed: the file contains "
                f"{len(violations)} violation(s).")
        try:
            _validate_schema(conn)
            version = conn.execute("PRAGMA user_version").fetchone()[0]
        except sqlite3.Error as exc:
            raise BackupError(f"Schema validation failed: {exc}")
    finally:
        conn.close()
    if version > store_ops_db.SCHEMA_VERSION:
        raise BackupError(
            f"Schema version v{version} is newer than this app "
            f"(v{store_ops_db.SCHEMA_VERSION}); upgrade the app first.")
    return version


def _backups_dir(db_path: Path | str) -> Path:
    return Path(db_path).parent / BACKUP_DIRNAME


def _unique_backup_path(dest_dir: Path, stem: str) -> Path:
    """Reserve a new backup name without overwriting a same-second copy."""
    for number in range(1000):
        suffix = f"-{number}" if number else ""
        dest = dest_dir / f"{stem}{suffix}.db"
        try:
            fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            continue
        os.close(fd)
        return dest
    raise BackupError("Could not allocate a unique automatic backup filename.")


def _save_automatic_backup(db_path: Path | str, stem: str) -> Path:
    dest_dir = _backups_dir(db_path)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = _unique_backup_path(dest_dir, stem)
    try:
        create_backup(db_path, dest)
    except (OSError, sqlite3.Error, BackupError) as exc:
        try:
            dest.unlink()
        except OSError:
            pass
        raise BackupError(f"Could not create automatic backup: {exc}") from exc
    prune_automatic_backups(db_path)
    return dest


def save_pre_restore_backup(db_path: Path | str,
                            now: datetime | None = None) -> Path:
    """Copy the current database into backups/ before a restore."""
    now = now or datetime.now(timezone.utc)
    return _save_automatic_backup(
        db_path, f"pre-restore-{now.strftime('%Y%m%d-%H%M%S')}")


def save_pre_migration_backup(db_path: Path | str, from_version: int,
                              to_version: int,
                              now: datetime | None = None) -> Path:
    """Save the original database before any pending migration begins."""
    now = now or datetime.now(timezone.utc)
    return _save_automatic_backup(
        db_path,
        f"pre-migration-v{from_version}-to-v{to_version}-"
        f"{now.strftime('%Y%m%d-%H%M%S')}")


def restore(db_path: Path | str, upload_path: Path | str) -> int:
    """Validate and migrate a temporary copy, then atomically replace db_path.

    The caller must already have saved a pre-restore backup and obtained
    user confirmation. No failure before os.replace can modify the live
    database. Returns the uploaded file's pre-migration schema version.
    """
    db_path, upload_path = Path(db_path), Path(upload_path)
    temp_path = None
    try:
        # Keep the temporary file on the same filesystem so os.replace is
        # atomic. Never open the live path until every check has passed.
        fd, temp_name = tempfile.mkstemp(
            dir=db_path.parent, prefix=f".{db_path.name}.restore-",
            suffix=".db")
        os.close(fd)
        temp_path = Path(temp_name)
        try:
            shutil.copyfile(upload_path, temp_path)
        except (OSError, shutil.SameFileError) as exc:
            raise BackupError(f"Cannot copy the uploaded file: {exc}")

        version = validate_restore(temp_path)
        try:
            store_ops_db.init_db(temp_path, backup_before_migration=False)
            validate_restore(temp_path)
        except (sqlite3.Error, store_ops_db.SchemaVersionError) as exc:
            raise BackupError(f"Database migration failed: {exc}")
        os.replace(temp_path, db_path)
        temp_path = None
        return version
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass


_SAFE_NAME = re.compile(
    r"^(?:pre-restore-\d{8}-\d{6}|"
    r"pre-migration-v\d+-to-v\d+-\d{8}-\d{6})(?:-\d+)?\.db$")


def list_automatic_backups(db_path: Path | str, limit: int | None = None
                           ) -> list[BackupFile]:
    """Newest first; only recognized automatic backup files, never links."""
    dest_dir = _backups_dir(db_path)
    out: list[BackupFile] = []
    if dest_dir.is_symlink() or not dest_dir.is_dir():
        return out
    try:
        candidates = list(dest_dir.iterdir())
    except OSError:
        return out
    for p in candidates:
        if p.is_symlink() or not p.is_file() or not _SAFE_NAME.fullmatch(p.name):
            continue
        try:
            stat = p.stat()
            created = datetime.fromtimestamp(stat.st_mtime)
            out.append(BackupFile(p.name, p, stat.st_size, created))
        except OSError:
            continue
    out.sort(key=lambda b: b.name, reverse=True)
    return out if limit is None else out[:limit]


def prune_automatic_backups(db_path: Path | str,
                            keep: int = AUTOMATIC_BACKUP_RETENTION) -> None:
    """Best-effort deletion of old automatic copies after a new copy exists."""
    for item in list_automatic_backups(db_path)[keep:]:
        try:
            item.path.unlink()
        except OSError as exc:
            log.warning("Could not remove old automatic backup %s: %s",
                        item.path, exc)
