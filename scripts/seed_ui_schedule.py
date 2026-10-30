#!/usr/bin/env python3
"""Seed a disposable UI database for the weekly-schedule browser QA
(scripts/browser_smoke_workflows.py).

Data contract (asserted by the test — keep in sync):
  * Store A01 "Alpha Pavilion", October 2026 configured (levels, staffing).
  * Staff: Alice A001 (Cashier, Full-Time), Bob B002 (Cashier, Part-Time).
  * Schedule week of Mon 2026-10-05: Alice + Bob both on M900, target 2.
  * Daily records: Oct 1 (RM 12,000) and Oct 5 (RM 13,000) — Oct 5 is the
    row used for the schedule-preview/apply dialog.
  * Users: admin@ducklab.com / AdminPass1234 (super admin).

Writes ONLY to $DUCKLAB_UI_DB (default /tmp/opencode/ui-workflows.db). The
file is deleted first. Never touches the real store_ops.db.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DB = os.environ.get("DUCKLAB_UI_DB", "/tmp/opencode/ui-workflows.db")


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
    app.configure_month(
        admin, a, "2026-10",
        level1_base_cents=rm(210000), level2_pct=0.1, level3_pct=0.2,
        level4_pct=0.3,
        opening_headcount_ft=10, budget_headcount=12,
        turnover_threshold=0.45, stock_loss_target=0.015,
        salary_ratio_target=0.06818, part_timer_factor=0.5,
        salary_basic_allowance_cents=rm(12000), overtime_cents=rm(1000))
    # daily records: Oct 5 is the schedule-apply dialog row
    app.record_daily(admin, a, date(2026, 10, 1), sales_cents=rm(12000),
                     ft_working=8, pt_working=2)
    app.record_daily(admin, a, date(2026, 10, 5), sales_cents=rm(13000),
                     ft_working=9, pt_working=3)
    # weekly schedule: week of Mon 2026-10-05 (first week option for Oct)
    alice = app.save_staff(admin, a, None, name="Alice", staff_code="A001",
                           position="Cashier", employment_type="Full-Time")
    bob = app.save_staff(admin, a, None, name="Bob", staff_code="B002",
                         position="Cashier", employment_type="Part-Time")
    app.set_schedule_entry(admin, a, alice, date(2026, 10, 5), "M900")
    app.set_schedule_entry(admin, a, bob, date(2026, 10, 5), "M900")
    app.set_schedule_target(admin, a, date(2026, 10, 5), 2)
    print(f"seeded disposable DB at {DB} (store {a})")


if __name__ == "__main__":
    main()
