#!/usr/bin/env python3
"""Seed a disposable UI database for Manpower Efficiency browser QA.

Writes ONLY to $DUCKLAB_UI_DB (default /tmp/opencode/ui5.db). Never touches
the real store_ops.db. Safe to run repeatedly (file is deleted first).

Users:
  admin@ducklab.com / AdminPass1234     (super admin, all stores)
  manager@ducklab.com / ManagerPass123  (branch manager, store A01)
  viewer@ducklab.com / ViewerPass123    (viewer, store A01)
"""

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB = os.environ.get("DUCKLAB_UI_DB", "/tmp/opencode/ui5.db")


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
    # 2026-10: store A fully configured + payroll; store B awaiting payroll
    app.configure_month(
        admin, a, "2026-10", level1_base_cents=rm(210000),
        opening_headcount_ft=10, budget_headcount=12,
        turnover_threshold=0.45, stock_loss_target=0.015,
        salary_ratio_target=0.06818, part_timer_factor=0.5,
        salary_basic_allowance_cents=rm(13275.75),
        overtime_cents=rm(4198.50),
        support_team_cost_cents=rm(500))
    app.configure_month(
        admin, b, "2026-10", level1_base_cents=rm(180000),
        opening_headcount_ft=8, budget_headcount=8,
        turnover_threshold=0.45, stock_loss_target=0.015,
        salary_ratio_target=0.06818, part_timer_factor=0.5)
    # 2026-09: historical month for A (different headcount + payroll)
    app.configure_month(
        admin, a, "2026-09", level1_base_cents=rm(200000),
        opening_headcount_ft=9, budget_headcount=9,
        turnover_threshold=0.45, stock_loss_target=0.015,
        salary_ratio_target=0.06818, part_timer_factor=0.5,
        salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
    # daily staffing data, store A October (partial month)
    seed_days = [
        (1, dict(sales_cents=rm(11500), ft_working=8, pt_working=4,
                 leave_annual=1)),
        (2, dict(sales_cents=rm(12100), ft_working=9, pt_working=2,
                 new_hires_ft=1)),
        (3, dict(sales_cents=rm(10800), ft_working=8, pt_working=3,
                 leave_mc=1)),
        (4, dict(sales_cents=rm(9900), ft_working=7, pt_working=4,
                 resigned_ft=1, leave_unpaid=1)),
        (5, dict(sales_cents=rm(13200), ft_working=9, pt_working=3,
                 leave_other_paid=1)),
        (6, dict(sales_cents=rm(12400), ft_working=8, pt_working=2)),
        (7, dict(sales_cents=rm(11700), ft_working=8, pt_working=4)),
    ]
    for day, fields in seed_days:
        app.record_daily(admin, a, date(2026, 10, day), **fields)
    # store B staffing (isolation check)
    app.record_daily(admin, b, date(2026, 10, 1), sales_cents=rm(8000),
                     ft_working=6, pt_working=2)
    # September history for A
    app.record_daily(admin, a, date(2026, 9, 2), sales_cents=rm(10000),
                     ft_working=7, pt_working=2, new_hires_ft=1)
    app.record_daily(admin, a, date(2026, 9, 3), sales_cents=rm(10500),
                     ft_working=7, pt_working=2)
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
