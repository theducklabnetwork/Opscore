#!/usr/bin/env python3
"""Seed a small, realistic stock-management dataset for UI QA.

Default: writes to the path passed as argv[1], or a fresh temp DB
(deleted first). Never touches the real store_ops.db.

--dev: targets ONLY /home/duck/project/ducklab/.devdata/dev.db (refuses any
other path), is idempotent, labels stores "DEV", and assigns both stores to
the Super Admin (id=1). Data uses October/November 2026 relative to the
current dev date.

Users (default mode):
  admin@ducklab.com / AdminPass1234   (super admin, all stores)
  viewer@ducklab.com / ViewerPass123  (viewer, store A01)
"""

import argparse
import os
import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEV_DB = Path("/home/duck/project/ducklab/.devdata/dev.db")


def rm(v):
    return int(Decimal(str(v)) * 100)


def seed(app, admin, label=""):
    name = f" {label}".rstrip()
    a = app.register_store(admin, "A01", f"Alpha Pavilion{name}".strip())
    b = app.register_store(admin, "B02", f"Beta Galleria{name}".strip())
    for sid in (a, b):
        for month, base in (("2026-09", 200000), ("2026-10", 210000)):
            app.configure_month(
                admin, sid, month, level1_base_cents=rm(base),
                stock_loss_target=0.015, part_timer_factor=0.5)

    def rec(store, y, m, d, sales=None, **losses):
        fields = {}
        if sales is not None:
            fields["sales_cents"] = rm(sales)
        if losses:
            fields["stock_losses"] = {k: rm(v) for k, v in losses.items()}
        app.record_daily(admin, store, date(y, m, d), **fields)

    # Store A, September 2026: complete-ish previous month (skip 2 days)
    for d in range(1, 29):
        losses = {}
        if d % 7 == 0:
            losses = {"DMO": 45, "EXTRA": 10}
        elif d % 9 == 0:
            losses = {"MISSING": 60}
        rec(a, 2026, 9, d, sales=9500 + (d % 5) * 400, **losses)

    # Store A, October 2026: partial month (1..18 reported, rest blank)
    rec(a, 2026, 10, 1, sales=11500, DMO=45, EXTRA=10)
    rec(a, 2026, 10, 2, sales=12100, DMC=32)
    rec(a, 2026, 10, 3, sales=10800, DMP=28, DPI=15)          # DPI info-only
    rec(a, 2026, 10, 4, sales=9900, MISSING=120, DMW=40)      # high loss day
    rec(a, 2026, 10, 5, sales=13200, EXTRA=85)                # net negative
    rec(a, 2026, 10, 6, sales=12400)                          # zero day
    rec(a, 2026, 10, 7, sales=11700, DMO=210, MISSING=95)     # high loss day
    rec(a, 2026, 10, 8, sales=10200, DMO=0)                   # explicit zero
    rec(a, 2026, 10, 9, sales=0, DMO=25)                      # zero sales
    rec(a, 2026, 10, 10, sales=11900, DMC=55, DMP=40,
        **{"2DMP": 30, "DDR": 22})                            # info-only
    for d in range(11, 19):
        rec(a, 2026, 10, d, sales=10500 + (d % 4) * 300,
            **({"DMO": 35} if d % 3 == 0 else {}))
    # days 19..31 unreported

    # Store B, October 2026: isolation check, few entries
    rec(b, 2026, 10, 1, sales=8000, DMO=90)
    rec(b, 2026, 10, 2, sales=8300, MISSING=50)

    # viewer user
    viewer = app.create_user(admin, "viewer@ducklab.com", "Test Viewer",
                             "ViewerPass123")
    app.assign_role(admin, viewer.id, "viewer")
    app.assign_store(admin, viewer.id, a)
    return a, b


def seed_dev():
    """Idempotent seeding of the dev DB; stores assigned to Super Admin (1)."""
    from ducklab import store_ops_db
    from ducklab.app_service import AppService
    app = AppService(DEV_DB)
    conn = store_ops_db.connect(DEV_DB)
    try:
        if conn.execute(
                "SELECT 1 FROM stores WHERE code = 'A01'").fetchone():
            conn.close()
            print(f"dev DB already seeded ({DEV_DB}) — nothing changed")
            return
    finally:
        conn.close()
    a, b = seed(app, app.auth.get_user(1), label="DEV")
    conn = store_ops_db.connect(DEV_DB)
    try:
        for sid in (a, b):
            conn.execute(
                "INSERT OR IGNORE INTO auth_user_store_assignments "
                "(user_id, store_id, is_active) VALUES (1, ?, 1)", (sid,))
        conn.commit()
    finally:
        conn.close()
    print(f"seeded DEV stores {a}, {b} into {DEV_DB} (assigned to user 1)")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("path", nargs="?", default=None,
                   help="target DB path (fresh temp DB if omitted)")
    p.add_argument("--dev", action="store_true",
                   help=f"seed ONLY the dev DB at {DEV_DB}")
    args = p.parse_args()

    if args.dev:
        if args.path:
            p.error("--dev refuses any explicit path")
        seed_dev()
        return

    db = args.path or str(Path(tempfile.mkdtemp()) / "stock_ui.db")
    if os.path.exists(db):
        os.remove(db)
    from ducklab.app_service import AppService
    app = AppService(db)
    admin = app.auth.bootstrap_first_admin(
        "admin@ducklab.com", "Test Admin", "AdminPass1234")
    a, b = seed(app, admin)
    print(f"seeded disposable DB at {db} (stores {a}, {b})")


if __name__ == "__main__":
    main()
