"""Authentication & RBAC schema compatibility helpers.

The complete authentication schema is versioned as schema v15 in
``store_ops_db``. This module retains its public names for existing callers
without maintaining a second, independently-applied schema definition.

Sessions and recovery keys are stored only as SHA-256 hashes. No default
account is created; first-admin bootstrap is an explicit operator action.
"""

from __future__ import annotations

from pathlib import Path

from ducklab import store_ops_db

AUTH_SCHEMA = store_ops_db._AUTH_SCHEMA
ROLES = store_ops_db.AUTH_ROLES


def init_auth(db_path: Path | str = store_ops_db.DB_PATH) -> None:
    """Initialize base and authentication schema through the shared runner."""
    store_ops_db.init_db(db_path)
