#!/usr/bin/env python3
"""Offline administrator password recovery for DuckLab Operations / Opscore.

Use when an operator is locked out and there is no self-service (email)
reset. This is the supported, repeatable form of an ad-hoc database edit:
it reuses AuthService so the new password is hashed with the same argon2id
parameters, all live sessions for the account are revoked, and the change
is written to the audit log. All other data (stores, records, other
accounts) is left untouched.

Safety:
  * Interactive only — the password is never accepted on the command line.
  * Backs up the database to <db>.bak-<timestamp> before writing.
  * Refuses to run against a database that is a symlink (prevents
    accidentally modifying a file outside the intended location).

Usage:
    DUCKLAB_DB_PATH=/path/to/store_ops.db python scripts/recover_admin_password.py

    # Desktop (Windows) install, with the bundled runtime:
    #   set DUCKLAB_DB_PATH=%LOCALAPPDATA%\\Opscore\\store_ops.db
    #   "%LOCALAPPDATA%\\Programs\\Opscore\\python\\python.exe" -I scripts\\recover_admin_password.py

Environment:
    DUCKLAB_DB_PATH   Path to the operations database
                      (default: ducklab/store_ops.db for a source checkout).
"""

from __future__ import annotations

import getpass
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ducklab import auth_service  # noqa: E402
from ducklab.auth_service import AuthError, AuthService  # noqa: E402


def _resolve_db() -> Path:
    raw = os.environ.get("DUCKLAB_DB_PATH") or str(auth_service.store_ops_db.DB_PATH)
    path = Path(raw)
    if path.is_symlink():
        raise SystemExit(f"Refusing a symlinked database: {path}")
    return path


def _backup(db_path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = db_path.with_name(f"{db_path.name}.bak-{stamp}")
    shutil.copyfile(db_path, backup)
    return backup


def _find_user_id(db_path: Path, email: str) -> int | None:
    norm = auth_service.normalize_email(email)
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT id FROM auth_users WHERE email = ?", (norm,)).fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def main() -> int:
    db_path = _resolve_db()
    print(f"Offline admin password recovery\nDatabase: {db_path}\n")
    if not db_path.is_file():
        print(f"Database not found: {db_path}", file=sys.stderr)
        return 1

    email = input("Account email: ").strip()
    user_id = _find_user_id(db_path, email)
    if user_id is None:
        # Deliberately do not say whether the account exists.
        print("No matching active account found.", file=sys.stderr)
        return 1

    pw1 = getpass.getpass("New password (min 10 chars, letters+digits): ")
    pw2 = getpass.getpass("Confirm new password: ")
    if pw1 != pw2:
        print("Passwords do not match.", file=sys.stderr)
        return 1

    backup = _backup(db_path)
    service = AuthService(db_path)
    try:
        # Offline self-recovery: the account is its own actor for the audit
        # trail. change_password hashes, revokes sessions, and audits.
        actor = service.get_user(user_id)
        service.change_password(actor, user_id, pw1, self_change=False)
    except AuthError as exc:
        print(f"Recovery failed: {exc}", file=sys.stderr)
        return 1

    print(f"Password reset for user id={user_id}. Backup: {backup}")
    print("All previous sessions were revoked. Log in at /login, then set a "
          "permanent password in Settings -> Change Password.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
