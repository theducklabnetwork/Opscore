"""Database schema inspection for the release pipeline.

Read-only helpers used by deploy tooling to detect the schema version of a
DuckLab store-ops database and determine migration compatibility. Never
modifies the database; all connections are opened read-only.

Migration policy (see deploy/RELEASES.md):
  * The application code declares SCHEMA_VERSION in ducklab/store_ops_db.py.
  * init_db() applies pending migrations in ascending order and is
    idempotent and non-destructive.
  * A release may only auto-migrate forward. Code rollback never restores an
    older database; older code must be verified compatible with a newer
    schema before rollback is allowed (older code ignores newer columns
    under the current additive-only convention).
  * A non-additive migration (drop/rename/retype) requires an explicit,
    operator-approved migration plan before deployment.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class DbSchemaInfo:
    path: str
    user_version: int
    migrations_applied: tuple[int, ...]
    integrity_ok: bool


def inspect(db_path: str) -> DbSchemaInfo:
    """Return schema info for the DB at db_path (read-only)."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        user_version = conn.execute("PRAGMA user_version").fetchone()[0]
        try:
            rows = conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            ).fetchall()
            applied = tuple(int(r[0]) for r in rows)
        except sqlite3.OperationalError:
            applied = ()
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        return DbSchemaInfo(db_path, user_version, applied, integrity)
    finally:
        conn.close()


def app_schema_version() -> int:
    """The schema version this application code will migrate to."""
    from ducklab.store_ops_db import SCHEMA_VERSION
    return SCHEMA_VERSION


def check_compatibility(db_path: str) -> tuple[bool, str]:
    """Verify the DB schema is compatible with this application code.

    Returns (ok, message). ok=False means the release pipeline must STOP and
    require an explicit operator-approved migration plan.
    """
    info = inspect(db_path)
    if not info.integrity_ok:
        return False, f"integrity_check FAILED for {db_path}"
    app_v = app_schema_version()
    if info.user_version > app_v:
        return False, (
            f"database schema v{info.user_version} is NEWER than this code "
            f"(v{app_v}); refusing to run older code against a newer schema "
            f"without an explicit compatibility review"
        )
    if info.user_version < app_v:
        return True, (
            f"database schema v{info.user_version} will be migrated to "
            f"v{app_v} on first service start (idempotent, non-destructive); "
            f"a verified backup is REQUIRED before activation"
        )
    return True, f"database schema v{info.user_version} matches code v{app_v}"


if __name__ == "__main__":
    # CLI: `python -m ducklab.db_inspect [db_path]` prints the DB's
    # user_version (a single integer) for use by release tooling.
    import os
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else os.environ.get(
        "DUCKLAB_DB_PATH", "/var/lib/ducklab/store_ops.db")
    print(inspect(path).user_version)
