#!/usr/bin/env python3
"""Seed a Sales Performance QA dataset for the dev DB.

--dev: targets ONLY /home/duck/project/ducklab/.devdata/dev.db (refuses any
other path), is idempotent, labels stores "DEV", and assigns the store to
the Super Admin (id=1).

Dataset: store S01 with
- October 2026: partial month (~2/3 entered), one explicit zero-sales day,
  one unreported day in the middle, targets + references configured
- September 2026: complete month (all 30 days)
- State / Concept Selangor / Mall with a synthetic Daily Sales
  Contribution for both months (illustrative shares, not real data),
  October basket targets, one public holiday and State Average
  SPR / IPR benchmarks (illustrative)
"""

import argparse
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

    # idempotent store creation
    try:
        sid = app.register_store(admin, "S01", "Sales QA DEV",
                                 concept="Mall", zone="DEV")
    except DuplicateRecordError:
        sid = app.read_repo.get_store_by_code("S01").id
    app.update_store_details(admin, sid, state="Selangor", concept="Mall")
    if sid not in admin.store_ids:
        app.assign_store(admin, admin.id, sid)

    # config: targets + references (re-saving identical values is harmless)
    app.configure_month(
        admin, sid, "2026-10",
        min_rtm_target_cents=rm(180000), level1_base_cents=rm(200000),
        level2_pct=0.05, level3_pct=0.10, level4_pct=0.15,
        sales_last_year_cents=rm(190000),
        sales_last_month_cents=rm(186000),
        part_timer_factor=0.5, stock_loss_target=0.015)
    app.configure_month(
        admin, sid, "2026-09",
        min_rtm_target_cents=rm(175000), level1_base_cents=rm(195000),
        level2_pct=0.05, level3_pct=0.10, level4_pct=0.15,
        sales_last_year_cents=rm(180000),
        sales_last_month_cents=rm(178000),
        part_timer_factor=0.5, stock_loss_target=0.015)

    # Daily targets: synthetic contribution shares (illustrative only),
    # basket targets and a public holiday on 14 Oct
    shares = {"Mon": 0.030, "Tue": 0.029, "Wed": 0.0295, "Thu": 0.026,
              "Fri": 0.031, "Sat": 0.039, "Sun": 0.0445, "PH": 0.045}
    import sqlite3
    conn = sqlite3.connect(DEV_DB)
    with conn:
        conn.executemany(
            "INSERT OR REPLACE INTO contribution_shares (state, concept, "
            "day, month, share) VALUES ('Selangor', 'Mall', ?, ?, ?)",
            [(k, m, v) for m in (9, 10) for k, v in shares.items()])
        # State Average SPR (RM) / IPR for the Basket (illustrative only)
        conn.executemany(
            "INSERT OR REPLACE INTO basket_benchmarks (concept, state, "
            "month, avg_spr, avg_ipr) VALUES ('Mall', 'Selangor', ?, ?, ?)",
            [(9, 20.50, 2.05), (10, 21.00, 1.85)])
    conn.close()
    app.configure_month_with_budgets(
        admin, sid, "2026-10",
        {"target_transactions": 9300, "target_spr_cents": rm(21),
         "target_ipr": 1.9}, {}, [date(2026, 10, 14)])

    # September: complete month, RM 6,200/day -> RM 186,000 actual
    for d in range(1, 31):
        app.record_daily(admin, sid, date(2026, 9, d),
                         sales_cents=rm(6200), transactions=310, units=620)

    # October: days 1-20 entered except day 11 (unreported hole);
    # day 5 is an explicit zero-sales day. Day 11 is DELETED on re-run so
    # the seed is fully idempotent even if a QA run filled the hole.
    conn = sqlite3.connect(DEV_DB)
    conn.execute("DELETE FROM daily_records WHERE store_id = ? AND date = ?",
                 (sid, "2026-10-11"))
    conn.commit(); conn.close()
    for d in list(range(1, 11)) + list(range(12, 21)):
        sales = 0 if d == 5 else 6400
        app.record_daily(admin, sid, date(2026, 10, d),
                         sales_cents=rm(sales),
                         transactions=0 if d == 5 else 320,
                         units=0 if d == 5 else 640)

    print(f"Seeded S01 (id={sid}) into {DEV_DB}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
