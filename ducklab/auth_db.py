"""Authentication & RBAC schema for the store operations database.

Additive, idempotent migration on top of store_ops_db. New tables only —
no existing operational tables or records are touched.

Entities: auth_users, auth_roles, auth_user_roles,
auth_user_store_assignments, auth_sessions, auth_audit_events,
auth_page_tours, auth_recovery_keys.

Security notes:
- Sessions are stored as SHA-256 hashes of the bearer token; raw tokens
  never touch the database (or logs).
- No default/admin accounts are created here. First-admin bootstrap is an
  explicit operator action (see auth_service.bootstrap_first_admin).
"""

from __future__ import annotations

from pathlib import Path

from ducklab import store_ops_db

AUTH_SCHEMA = """
CREATE TABLE IF NOT EXISTS auth_users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,           -- normalized lowercase
    display_name TEXT NOT NULL,
    password_hash TEXT NOT NULL,          -- argon2id encoded string
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    password_changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS auth_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE             -- super_admin/administrator/...
);

CREATE TABLE IF NOT EXISTS auth_user_roles (
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    role_id INTEGER NOT NULL REFERENCES auth_roles(id),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, role_id)
);

CREATE TABLE IF NOT EXISTS auth_user_store_assignments (
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    store_id INTEGER NOT NULL REFERENCES stores(id),
    is_active INTEGER NOT NULL DEFAULT 1 CHECK (is_active IN (0, 1)),
    assigned_at TEXT NOT NULL DEFAULT (datetime('now')),
    revoked_at TEXT,
    PRIMARY KEY (user_id, store_id)
);

CREATE TABLE IF NOT EXISTS auth_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash TEXT NOT NULL UNIQUE,      -- sha256 of bearer token
    user_id INTEGER NOT NULL REFERENCES auth_users(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT NOT NULL,
    revoked_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id);

CREATE TABLE IF NOT EXISTS auth_audit_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER REFERENCES auth_users(id),   -- may be NULL (failed login)
    event_type TEXT NOT NULL,
    outcome TEXT NOT NULL,                       -- success / failure
    metadata TEXT NOT NULL DEFAULT '{}',         -- JSON, never secrets
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_auth_audit_user ON auth_audit_events(user_id);

CREATE TABLE IF NOT EXISTS auth_page_tours (
    user_id INTEGER NOT NULL REFERENCES auth_users(id) ON DELETE CASCADE,
    page_key TEXT NOT NULL,
    completed_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, page_key)
);

-- One active recovery key per user. Only the SHA-256 hash of the key is
-- stored; the raw key lives solely with the account owner (recovery kit).
-- A key is single-use: used_at is set when it resets a password, and a
-- replacement key must be issued before the account can be recovered again.
CREATE TABLE IF NOT EXISTS auth_recovery_keys (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL UNIQUE REFERENCES auth_users(id)
        ON DELETE CASCADE,
    key_hash TEXT NOT NULL,                 -- sha256 of the recovery key
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    used_at TEXT                            -- set on use (single-use)
);
"""

ROLES = (
    "super_admin",
    "administrator",
    "zone_manager",
    "branch_manager",
    "viewer",
)


def init_auth(db_path: Path | str = store_ops_db.DB_PATH) -> None:
    """Create auth tables and seed role names. Idempotent; additive only."""
    store_ops_db.init_db(db_path)  # ensure base schema exists first
    conn = store_ops_db.connect(db_path)
    try:
        conn.executescript(AUTH_SCHEMA)
        for role in ROLES:
            conn.execute(
                "INSERT OR IGNORE INTO auth_roles (name) VALUES (?)", (role,)
            )
        _migrate_auth_users(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate_auth_users(conn) -> None:
    """Additive column migrations on auth_users (idempotent)."""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(auth_users)")}
    if "tour_done" not in cols:
        conn.execute(
            "ALTER TABLE auth_users ADD COLUMN tour_done "
            "INTEGER NOT NULL DEFAULT 0 CHECK (tour_done IN (0, 1))"
        )
