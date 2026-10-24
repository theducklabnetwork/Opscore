#!/usr/bin/env python3
"""Seed a disposable UI database for the Operations Dashboard browser QA
(scripts/browser_test_ops.py).

Data contract (asserted by the test — keep in sync):
  * Stores A01 "Alpha Pavilion" and B02 "Beta Galleria".
  * October 2026 configured for A01 ONLY (B02 shows "No report available").
  * A01 October daily sales: Oct 1 = RM 12,000.00, Oct 2 = RM 10,000.00
    -> live sales MTD RM 22,000.00.
  * September 2026 configured for BOTH stores, no daily records
    -> A01 September sales MTD RM 0.00... (report shows RM 20,000.00 expenses
       figure for A01 September; see below).
  * Expense categories + budgets and transactions such that:
      - A01 September expenses total RM 20,000.00 (asserted on report page).
  * Users: admin@ducklab.com / AdminPass1234 (super admin, both stores),
           viewer@ducklab.com / ViewerPass123 (viewer, A01).

Writes ONLY to $DUCKLAB_UI_DB (default /tmp/opencode/ui-ops.db). The file is
deleted first. Never touches the real store_ops.db.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB = os.environ.get("DUCKLAB_UI_DB", "/tmp/opencode/ui-ops.db")


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

    # October: A01 only (B02 unconfigured -> "No report available" empty state).
    app.configure_month(
        admin, a, "2026-10", level1_base_cents=rm(210000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
        opening_headcount_ft=10, stock_loss_target=0.015,
        salary_ratio_target=0.06818, part_timer_factor=0.5,
        salary_basic_allowance_cents=rm(10500), overtime_cents=rm(1000))
    # September: both stores configured.
    for sid in (a, b):
        app.configure_month(
            admin, sid, "2026-09", level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))

    # Daily sales for A01 October: 12,000 + 10,000 -> MTD RM 22,000.00.
    app.record_daily(admin, a, date(2026, 10, 1), sales_cents=rm(12000),
                     transactions=350, units=1400, ft_working=8, pt_working=1,
                     stock_losses={"DMO": rm(45), "EXTRA": rm(10)})
    app.record_daily(admin, a, date(2026, 10, 2), sales_cents=rm(10000),
                     transactions=340, units=1350, ft_working=8, pt_working=1,
                     stock_losses={"DMO": rm(30)})

    # September A01: sales total RM 20,000.00 (asserted in #sales-section on
    # the report page, both Live and Finalized modes).
    app.record_daily(admin, a, date(2026, 9, 10), sales_cents=rm(10000),
                     transactions=300, units=1200, ft_working=8, pt_working=1)
    app.record_daily(admin, a, date(2026, 9, 20), sales_cents=rm(10000),
                     transactions=290, units=1100, ft_working=8, pt_working=1)

    # Finalized September snapshot for A01 (the report's "Finalized" mode
    # asserts "Finalized version 1" and the same RM 20,000.00 sales MTD).
    app.finalize_month(admin, a, "2026-09")

    viewer = app.create_user(admin, "viewer@ducklab.com", "Test Viewer",
                             "ViewerPass123")
    app.assign_role(admin, viewer.id, "viewer")
    app.assign_store(admin, viewer.id, a)
    print(f"seeded disposable DB at {DB} (stores {a}, {b})")


if __name__ == "__main__":
    main()
