#!/usr/bin/env python3
"""Seed an Expenses Management QA dataset into the dev DB.

--dev: targets ONLY /home/duck/project/ducklab/.devdata/dev.db (refuses any
other path), is idempotent, labels the store "DEV", and assigns it to the
Super Admin (id=1).

Store X01 (October 2026): budgets set; ~15 register lines covering Paid,
Pending, Cancelled, missing paid date, Unverified, Disputed, a cash
overage (negative), and one category over budget.
"""

import argparse
import sqlite3
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

DEV_DB = Path("/home/duck/project/ducklab/.devdata/dev.db")


def rm(v):
    return int(Decimal(str(v)) * 100)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dev", action="store_true",
                    help="write to the dev DB (only allowed target)")
    args = ap.parse_args()
    if not args.dev:
        print("Refusing: this seed targets ONLY .devdata/dev.db; pass --dev")
        return 2

    from ducklab.app_service import AppService
    from ducklab.operations_write_repository import DuplicateRecordError

    app = AppService(DEV_DB)
    admin = app.auth.get_user(1)

    try:
        sid = app.register_store(admin, "X01", "Expenses QA DEV",
                                 concept="Mall", zone="DEV")
    except DuplicateRecordError:
        sid = app.read_repo.get_store_by_code("X01").id
    if sid not in admin.store_ids:
        app.assign_store(admin, admin.id, sid)

    app.configure_month(
        admin, sid, "2026-10", min_rtm_target_cents=rm(200000),
        level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
        part_timer_factor=0.5, stock_loss_target=0.015)
    # one sale so "expenses % of sales" is defined
    app.record_daily(admin, sid, date(2026, 10, 1),
                     sales_cents=rm(10000))
    cats = {}
    for i, name in enumerate(("Electricity", "Water", "Consumables",
                              "Repairs"), start=1):
        try:
            cats[name] = app.add_expense_category(admin, sid, name,
                                                  sort_order=i)
        except DuplicateRecordError:
            cats[name] = next(c.id for c in
                              app.get_expense_categories(admin, sid)
                              if c.name == name)

    # budgets (% of the RM 200,000 Minimum RTM floor): Electricity 2%
    # (RM 4,000, over), Water 0.25% (RM 500), Consumables 0.4% (RM 800,
    # watch at >=80%), Repairs blank (No budget)
    app.set_expense_budget(admin, sid, "2026-10", cats["Electricity"],
                           0.02)
    app.set_expense_budget(admin, sid, "2026-10", cats["Water"], 0.0025)
    app.set_expense_budget(admin, sid, "2026-10", cats["Consumables"],
                           0.004)

    # wipe prior seeded lines for idempotency (dev-only table wipe by store)
    conn = sqlite3.connect(DEV_DB)
    conn.execute("DELETE FROM expense_transactions WHERE store_id = ?", (sid,))
    conn.execute("DELETE FROM daily_records WHERE store_id = ?"
                 " AND date LIKE '2026-10-%'", (sid,))
    conn.commit()
    conn.close()

    rec = app.record_expense
    E, W, C, R = (cats["Electricity"], cats["Water"], cats["Consumables"],
                  cats["Repairs"])
    lines = [
        # paid, verified (electricity — drives category over budget)
        dict(incurred_date=date(2026, 10, 2), paid_date=date(2026, 10, 3),
             category_id=E, description="Utility bill (Oct)", amount_cents=rm(3000),
             status="Paid", paid_by="Store Account", receipt_ref="RCP-3001",
             verification="Verified", recorded_by="Staff A"),
        dict(incurred_date=date(2026, 10, 9), paid_date=date(2026, 10, 10),
             category_id=E, description="Generator fuel", amount_cents=rm(700),
             status="Paid", paid_by="Petty Cash", receipt_ref="RCP-3005",
             verification="Verified", recorded_by="Staff A"),
        # water — within budget
        dict(incurred_date=date(2026, 10, 4), paid_date=date(2026, 10, 5),
             category_id=W, description="Water utility", amount_cents=rm(300),
             status="Paid", paid_by="Store Account", receipt_ref="RCP-3002",
             verification="Verified", recorded_by="Staff A"),
        # consumables — watch band (>=80% of RM 800)
        dict(incurred_date=date(2026, 10, 6), paid_date=date(2026, 10, 7),
             category_id=C, description="Cleaning supplies",
             amount_cents=rm(650), status="Paid", paid_by="Petty Cash",
             receipt_ref="RCP-3003", verification="Verified",
             recorded_by="Staff A"),
        # pending item (open)
        dict(incurred_date=date(2026, 10, 8), category_id=C,
             description="Packaging (awaiting invoice)", amount_cents=rm(185),
             status="Pending", recorded_by="Staff A"),
        # cancelled (excluded from totals)
        dict(incurred_date=date(2026, 10, 8), category_id=C,
             description="Duplicate entry — cancelled", amount_cents=rm(999),
             status="Cancelled", recorded_by="Staff A"),
        # paid but missing paid date (data check)
        dict(incurred_date=date(2026, 10, 11), category_id=W,
             description="Water deposit", amount_cents=rm(120),
             status="Paid", paid_by="Petty Cash", receipt_ref="RCP-3006",
             verification="Verified", recorded_by="Staff A"),
        # unverified
        dict(incurred_date=date(2026, 10, 12), paid_date=date(2026, 10, 12),
             category_id=R, description="Door hinge fix", amount_cents=rm(90),
             status="Paid", paid_by="Petty Cash", receipt_ref="RCP-3007",
             verification="Unverified", recorded_by="Staff A"),
        # disputed
        dict(incurred_date=date(2026, 10, 13), paid_date=date(2026, 10, 14),
             category_id=R, description="Disputed plumbing charge",
             amount_cents=rm(450), status="Paid", paid_by="HQ / Central",
             receipt_ref="RCP-3008", verification="Disputed",
             recorded_by="HQ"),
        # cash overage (negative)
        dict(incurred_date=date(2026, 10, 15), paid_date=date(2026, 10, 15),
             category_id=C, description="Cash overage at count",
             amount_cents=-rm(60), status="Paid", paid_by="Store Account",
             receipt_ref="ADJ-1001", verification="Verified",
             recorded_by="Staff A"),
        # cross-month: incurred Sep, paid Oct (paid-date attribution)
        dict(incurred_date=date(2026, 9, 28), paid_date=date(2026, 10, 1),
             category_id=C, description="September consumables settled Oct",
             amount_cents=rm(100), status="Paid", paid_by="Other",
             receipt_ref="RCP-2990", verification="Verified",
             recorded_by="Staff A"),
        # a few routine paid lines to reach ~15
        dict(incurred_date=date(2026, 10, 16), paid_date=date(2026, 10, 17),
             category_id=C, description="Receipt paper rolls",
             amount_cents=rm(45), status="Paid", paid_by="Petty Cash",
             receipt_ref="RCP-3009", verification="Verified",
             recorded_by="Staff A"),
        dict(incurred_date=date(2026, 10, 17), paid_date=date(2026, 10, 18),
             category_id=W, description="Filter replacement",
             amount_cents=rm(80), status="Paid", paid_by="Store Account",
             receipt_ref="RCP-3010", verification="Verified",
             recorded_by="Staff A"),
        dict(incurred_date=date(2026, 10, 18), category_id=R,
             description="Lighting quote (pending)", amount_cents=rm(220),
             status="Pending", recorded_by="Staff A"),
    ]
    for line in lines:
        rec(admin, sid, **line)

    print(f"Seeded X01 (id={sid}) expenses into {DEV_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
