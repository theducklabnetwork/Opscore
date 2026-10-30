#!/usr/bin/env python3
"""Import the AB01 demo CSVs through the authenticated application service.

Usage:
    scripts/import_demo.py --db /path/to/store_ops.db daily.csv setup.csv

The database must already contain its first account. Money columns are in RM;
uplifts are whole percentages; holidays are ISO dates separated by semicolons.
Blank daily values remain NULL. Re-running updates the same natural keys.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ducklab.input_validation import parse_count, parse_money_cents  # noqa: E402

STORE = {
    "store_code": "AB01",
    "store_name": "DEMO",
    "concept": "Outlet",
    "state": "WP",
}
DAILY_HEADERS = ("store_code", "date", "sales", "transactions", "items_sold")
SETUP_HEADERS = (
    "store_code", "store_name", "concept", "state", "month", "base_target",
    "level2_uplift_pct", "level3_uplift_pct", "level4_uplift_pct", "level4_min",
    "holidays",
)


def _rows(path: Path, headers: tuple[str, ...]) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != list(headers):
            raise ValueError(f"{path}: expected headers: {','.join(headers)}")
        rows = list(reader)
    if not rows:
        raise ValueError(f"{path}: no data rows")
    return rows


def _money(value: str) -> int | None:
    return parse_money_cents(value) if value.strip() else None


def _count(value: str, label: str) -> int | None:
    return parse_count(value, label) if value.strip() else None


def _month(value: str) -> str:
    parsed = date.fromisoformat(value.strip() + "-01")
    return parsed.strftime("%Y-%m")


def import_demo(db_path: Path, daily_path: Path, setup_path: Path) -> dict[str, int]:
    from ducklab.app_service import AppService
    from ducklab.operations_repository import StoreNotFoundError

    daily_rows = _rows(daily_path, DAILY_HEADERS)
    setup_rows = _rows(setup_path, SETUP_HEADERS)
    for row in setup_rows:
        for field, expected in STORE.items():
            if row[field].strip() != expected:
                raise ValueError(f"setup.csv: {field} must be {expected}")

    app = AppService(db_path)
    account = app.auth.get_user(1)
    with app.transaction():
        try:
            store = app.read_repo.get_store_by_code(STORE["store_code"])
        except StoreNotFoundError:
            store_id = app.register_store(
                account, STORE["store_code"], STORE["store_name"],
                concept=STORE["concept"], state=STORE["state"])
        else:
            store_id = store.id
            app.update_store_details(
                account, store_id, name=STORE["store_name"],
                concept=STORE["concept"], state=STORE["state"])
        if store_id not in account.store_ids:
            app.assign_store(account, account.id, store_id)

        for row in setup_rows:
            month = _month(row["month"])
            fields = {
                "level1_base_cents": _money(row["base_target"]),
                "level2_pct": float(Decimal(row["level2_uplift_pct"]) / 100),
                "level3_pct": float(Decimal(row["level3_uplift_pct"]) / 100),
                "level4_pct": float(Decimal(row["level4_uplift_pct"]) / 100),
                "min_rtm_target_cents": _money(row["level4_min"]),
            }
            holidays = [date.fromisoformat(value.strip())
                        for value in row["holidays"].split(";") if value.strip()]
            app.configure_month_with_budgets(
                account, store_id, month, fields, {}, holidays)

        for row in daily_rows:
            if row["store_code"].strip() != STORE["store_code"]:
                raise ValueError("daily.csv: store_code must be AB01")
            app.record_daily(
                account, store_id, date.fromisoformat(row["date"].strip()),
                sales_cents=_money(row["sales"]),
                transactions=_count(row["transactions"], "transactions"),
                units=_count(row["items_sold"], "items_sold"),
            )

    setup_months = {_month(row["month"]) for row in setup_rows}
    months = sorted(setup_months |
                    {date.fromisoformat(row["date"]).strftime("%Y-%m")
                     for row in daily_rows})
    return {
        "stores": 1,
        "auth_user_store_assignments": 1,
        "monthly_configs": len(setup_months),
        "public_holidays": sum(
            len(app.get_public_holidays(account, store_id, month))
            for month in months),
        "daily_records": sum(
            len(app.get_daily_records(account, store_id, month))
            for month in months),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True,
                        help="database to import into (never inferred)")
    parser.add_argument("daily_csv", type=Path)
    parser.add_argument("setup_csv", type=Path)
    args = parser.parse_args(argv)
    try:
        counts = import_demo(args.db.resolve(), args.daily_csv, args.setup_csv)
    except Exception as exc:
        print(f"Import failed; nothing changed.\n{exc}", file=sys.stderr)
        return 1
    print(f"Database: {args.db.resolve()}")
    for table, count in counts.items():
        print(f"{table}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
