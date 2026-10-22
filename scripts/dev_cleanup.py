#!/usr/bin/env python3
"""Remove leftover smoke/QA data from the DEV database.

  scripts/dev_cleanup.py            # dry run: list what would be removed
  scripts/dev_cleanup.py --apply    # delete, in ONE transaction

Targets ONLY <checkout>/.devdata/dev.db (refuses any other path).

Removes:
  - every store whose code is not in KEEP_STORE_CODES (Debug*, "Task Seven
    QA" and other earlier smoke-run stores) plus all their dependent rows
    (daily records + stock losses, month configs, expense categories /
    budgets / transactions, actions, snapshots, user store assignments);
  - QA users (email ending in QA_EMAIL_DOMAIN, created by smoke/browser
    runs, plus the test accounts in QA_EMAILS) with their roles, store
    assignments, sessions and audit events.

The dry run prints both lists and the kept stores/users for confirmation.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEV_DB = ROOT / ".devdata" / "dev.db"

KEEP_STORE_CODES = ("ZI68", "A01", "B02", "S01", "X01")
QA_EMAIL_DOMAIN = "@ducklab.dev"
QA_EMAILS = ("viewer@ducklab.com",)   # earlier test accounts

# Child-first delete order for one set of store ids.
STORE_TABLES = (
    ("daily_stock_losses",
     "daily_record_id IN (SELECT id FROM daily_records WHERE store_id IN ({ids}))"),
    ("daily_records", "store_id IN ({ids})"),
    ("monthly_expense_budgets", "store_id IN ({ids})"),
    ("expense_transactions", "store_id IN ({ids})"),
    ("expense_categories", "store_id IN ({ids})"),
    ("monthly_configs", "store_id IN ({ids})"),
    ("actions", "store_id IN ({ids})"),
    ("monthly_snapshots", "store_id IN ({ids})"),
    ("auth_user_store_assignments", "store_id IN ({ids})"),
)
USER_TABLES = (
    "auth_user_roles", "auth_user_store_assignments", "auth_sessions",
    "auth_audit_events",
)


def resolve_target(path) -> Path:
    """Only the checkout's dev DB is accepted."""
    p = Path(path).resolve()
    if p != DEV_DB.resolve():
        raise ValueError(f"Refusing {p}: dev_cleanup only targets {DEV_DB}")
    if not p.is_file():
        raise ValueError(f"Refusing {p}: file does not exist")
    return p


def _is_qa_user(email: str) -> bool:
    e = email.lower()
    return e.endswith(QA_EMAIL_DOMAIN) or e in QA_EMAILS


def _in(ids) -> str:
    return ",".join("?" for _ in ids)


def plan_cleanup(conn: sqlite3.Connection) -> dict:
    """Read-only: what would be removed (and what is kept)."""
    conn.row_factory = sqlite3.Row
    stores = [dict(r) for r in conn.execute(
        "SELECT id, code, name FROM stores ORDER BY id")]
    remove_stores = [s for s in stores if s["code"] not in KEEP_STORE_CODES]
    keep_stores = [s for s in stores if s["code"] in KEEP_STORE_CODES]
    users = [dict(r) for r in conn.execute(
        "SELECT id, email, display_name FROM auth_users ORDER BY id")]
    remove_users = [u for u in users if _is_qa_user(u["email"])]
    keep_users = [u for u in users if not _is_qa_user(u["email"])]

    sids = [s["id"] for s in remove_stores]
    uids = [u["id"] for u in remove_users]
    store_counts = {}
    for table, where in STORE_TABLES:
        store_counts[table] = (conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE "
            + where.format(ids=_in(sids)), sids).fetchone()[0]
            if sids else 0)
    user_counts = {
        table: (conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id IN ({_in(uids)})",
            uids).fetchone()[0] if uids else 0)
        for table in USER_TABLES
    }
    return {"remove_stores": remove_stores, "keep_stores": keep_stores,
            "remove_users": remove_users, "keep_users": keep_users,
            "store_counts": store_counts, "user_counts": user_counts}


def apply_cleanup(conn: sqlite3.Connection, plan: dict) -> None:
    """Delete everything in `plan` in ONE transaction (all or nothing)."""
    sids = [s["id"] for s in plan["remove_stores"]]
    uids = [u["id"] for u in plan["remove_users"]]
    conn.isolation_level = None
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("BEGIN IMMEDIATE")
    try:
        if sids:
            conn.execute(
                "UPDATE monthly_snapshots SET superseded_by = NULL "
                f"WHERE store_id IN ({_in(sids)})", sids)
            for table, where in STORE_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE "
                             + where.format(ids=_in(sids)), sids)
            conn.execute(f"DELETE FROM stores WHERE id IN ({_in(sids)})", sids)
        if uids:
            for table in USER_TABLES:
                conn.execute(f"DELETE FROM {table} WHERE user_id IN "
                             f"({_in(uids)})", uids)
            conn.execute(f"DELETE FROM auth_users WHERE id IN ({_in(uids)})",
                         uids)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def _print_plan(plan: dict) -> None:
    print(f"Stores to REMOVE ({len(plan['remove_stores'])}):")
    for s in plan["remove_stores"]:
        print(f"  id={s['id']:<4} {s['code']:<8} {s['name']}")
    print(f"Stores KEPT ({len(plan['keep_stores'])}): "
          + ", ".join(s["code"] for s in plan["keep_stores"]))
    print("Dependent store rows to remove:")
    for table, n in plan["store_counts"].items():
        print(f"  {table:<30} {n}")
    print(f"QA users to REMOVE ({len(plan['remove_users'])}, email ending "
          f"{QA_EMAIL_DOMAIN} or one of {', '.join(QA_EMAILS)}):")
    for u in plan["remove_users"]:
        print(f"  id={u['id']:<4} {u['email']}")
    print("Dependent user rows to remove:")
    for table, n in plan["user_counts"].items():
        print(f"  {table:<30} {n}")
    print(f"Users KEPT ({len(plan['keep_users'])}):")
    for u in plan["keep_users"]:
        print(f"  id={u['id']:<4} {u['email']} ({u['display_name']})")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=str(DEV_DB))
    ap.add_argument("--apply", action="store_true",
                    help="delete (default: dry run)")
    args = ap.parse_args(argv)
    try:
        db = resolve_target(args.db)
    except ValueError as exc:
        print(f"dev_cleanup: {exc}", file=sys.stderr)
        return 2
    conn = sqlite3.connect(str(db))
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        plan = plan_cleanup(conn)
        _print_plan(plan)
        if not args.apply:
            print("\nDRY RUN — nothing deleted. Re-run with --apply to delete.")
            return 0
        apply_cleanup(conn, plan)
        print("\nApplied: all of the above deleted in one transaction.")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
