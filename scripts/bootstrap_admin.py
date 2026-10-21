#!/usr/bin/env python3
"""One-time first-administrator bootstrap for DuckLab Operations.

Operator-initiated only: refuses to run if any Super Admin already exists,
and never overwrites existing accounts. Credentials are collected
interactively; the password is not echoed.

Usage:
    cd /home/duck/project/ducklab
    DUCKLAB_DB_PATH=/path/to/store_ops.db .venv/bin/python scripts/bootstrap_admin.py

Environment:
    DUCKLAB_DB_PATH   Path to the operations database (default: ducklab/store_ops.db)
"""

from __future__ import annotations

import getpass
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ducklab import auth_service  # noqa: E402
from ducklab.auth_service import AuthService, AuthError  # noqa: E402


def main() -> int:
    db_path = os.environ.get("DUCKLAB_DB_PATH") or auth_service.store_ops_db.DB_PATH
    print(f"DuckLab first-admin bootstrap\nDatabase: {db_path}\n")
    email = input("Admin email: ").strip()
    name = input("Display name: ").strip()
    pw1 = getpass.getpass("Password (min 10 chars, letters+digits): ")
    pw2 = getpass.getpass("Confirm password: ")
    if pw1 != pw2:
        print("Passwords do not match.", file=sys.stderr)
        return 1
    try:
        user = AuthService(db_path).bootstrap_first_admin(email, name, pw1)
    except AuthError as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        return 1
    print(f"Super Admin created: {user.email} (id={user.id})")
    print("Log in at /login. Keep these credentials safe — there is no "
          "self-service password reset until outbound email is configured.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
