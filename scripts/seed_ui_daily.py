#!/usr/bin/env python3
"""Seed a disposable UI database for Daily Operations browser QA.

Writes ONLY to $DUCKLAB_UI_DB (default /tmp/opencode/ui3.db). Never touches
the real store_ops.db. Safe to run repeatedly (file is deleted first).
"""

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB = os.environ.get("DUCKLAB_UI_DB", "/tmp/opencode/ui3.db")


def rm(v):
    return int(Decimal(str(v)) * 100)


def main() -> None:
    if os.path.exists(DB):
        os.remove(DB)
    from ducklab.app_service import AppService
    app = AppService(DB)
    admin = app.auth.bootstrap_first_admin(
        "admin@ducklab.com", "Test Admin", "AdminPass1234")
    a = app.register_store(admin, "A01", "Alpha Pavilion")
    b = app.register_store(admin, "B02", "Beta Galleria")
    for sid in (a, b):
        app.configure_month(
            admin, sid, "2026-10", level1_base_cents=rm(210000),
            opening_headcount_ft=10, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            salary_basic_allowance_cents=rm(10500), overtime_cents=rm(1000))
        app.configure_month(
            admin, sid, "2026-09", level1_base_cents=rm(200000),
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
    # pre-existing record for edit scenarios
    app.record_daily(admin, a, date(2026, 10, 1), sales_cents=rm(12000),
                     transactions=350, units=1400, ft_working=8, pt_working=1,
                     stock_losses={"DMO": rm(45), "EXTRA": rm(10)})
    # second day: report MTD baseline 12,000 + 11,500 = RM 23,500.00
    # (browser_test_ops.py asserts this live MTD on /ops/report).
    app.record_daily(admin, a, date(2026, 10, 2), sales_cents=rm(11500),
                     transactions=340, units=1350, ft_working=8, pt_working=1,
                     stock_losses={"DMO": rm(30)})
    bm = app.create_user(admin, "manager@ducklab.com", "Branch Manager",
                         "ManagerPass123")
    app.assign_role(admin, bm.id, "branch_manager")
    app.assign_store(admin, bm.id, a)
    viewer = app.create_user(admin, "viewer@ducklab.com", "Test Viewer",
                             "ViewerPass123")
    app.assign_role(admin, viewer.id, "viewer")
    app.assign_store(admin, viewer.id, a)
    print(f"seeded disposable DB at {DB} (stores {a}, {b})")


if __name__ == "__main__":
    main()
