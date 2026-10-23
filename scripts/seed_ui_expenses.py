#!/usr/bin/env python3
"""Seed a disposable UI database for Expenses Management browser QA.

Writes ONLY to $DUCKLAB_UI_DB (default /tmp/opencode/ui4.db). Never touches
the real store_ops.db. Safe to run repeatedly (file is deleted first).
"""

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB = os.environ.get("DUCKLAB_UI_DB", "/tmp/opencode/ui4.db")


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
        for month, l1 in (("2026-10", 210000), ("2026-09", 200000)):
            app.configure_month(
                admin, sid, month, level1_base_cents=rm(l1), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                opening_headcount_ft=10, stock_loss_target=0.015,
                salary_ratio_target=0.06818, part_timer_factor=0.5,
                salary_basic_allowance_cents=rm(10500),
                overtime_cents=rm(1000))
    # categories per store
    elec_a = app.add_expense_category(admin, a, "Electricity", sort_order=1)
    water_a = app.add_expense_category(admin, a, "Water", sort_order=2)
    cons_a = app.add_expense_category(admin, a, "Consumables", sort_order=3)
    elec_b = app.add_expense_category(admin, b, "Electricity", sort_order=1)
    # budgets: A electricity RM3,200 (paid 2,850 -> Watch), A water RM500
    # (paid 600 -> OVER), B electricity RM1,500
    app.set_expense_budget(admin, a, "2026-10", elec_a, rm(3200))
    app.set_expense_budget(admin, a, "2026-10", water_a, rm(500))
    app.set_expense_budget(admin, b, "2026-10", elec_b, rm(1500))
    # transactions (store A, October)
    app.record_expense(
        admin, a, incurred_date=date(2026, 10, 2),
        paid_date=date(2026, 10, 3), category_id=elec_a,
        description="TNB electricity bill", amount_cents=rm(2850),
        status="Paid", paid_by="Store Account", receipt_ref="RCP-2001",
        verification="Verified", recorded_by="Branch Manager")
    app.record_expense(
        admin, a, incurred_date=date(2026, 10, 4),
        paid_date=date(2026, 10, 5), category_id=water_a,
        description="Water utility", amount_cents=rm(600),
        status="Paid", paid_by="Petty Cash", receipt_ref="RCP-2002",
        verification="Unverified", recorded_by="Branch Manager")
    app.record_expense(
        admin, a, incurred_date=date(2026, 10, 6), category_id=cons_a,
        description="Cleaning supplies (pending invoice)",
        amount_cents=rm(185), status="Pending", recorded_by="Branch Manager")
    # cross-month: incurred Sep, paid Oct
    app.record_expense(
        admin, a, incurred_date=date(2026, 9, 28),
        paid_date=date(2026, 10, 1), category_id=cons_a,
        description="September consumables settled in October",
        amount_cents=rm(100), status="Paid", paid_by="Other",
        receipt_ref="RCP-1990", verification="Verified",
        recorded_by="Branch Manager")
    # store B transaction (isolation check)
    app.record_expense(
        admin, b, incurred_date=date(2026, 10, 3),
        paid_date=date(2026, 10, 3), category_id=elec_b,
        description="B02 electricity", amount_cents=rm(500), status="Paid",
        paid_by="HQ / Central", verification="Verified", recorded_by="HQ")
    # users
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
