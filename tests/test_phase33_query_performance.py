"""Phase 3.3: indexed month queries and single-pass report/Dashboard reads.

Uses disposable databases and deterministic synthetic records only.
"""

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from ducklab import kpi, store_ops_db
from ducklab.app_service import AppService
from ducklab.kpi import DailyInput, ExpenseInput
from ducklab.operations_repository import OperationsRepository
from ducklab.operations_service import assemble_month
from ducklab.ops_overview_state import compute_overview
import ducklab.ops_overview_state as overview_module


class MonthRangeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "month-ranges.db"
        store_ops_db.init_db(self.db)
        self.conn = store_ops_db.connect(self.db)
        self.sid = self.conn.execute(
            "INSERT INTO stores (code, name) VALUES ('S01', 'Synthetic')").lastrowid
        self.conn.execute(
            "INSERT INTO monthly_configs (store_id, month) VALUES (?, '2024-02')",
            (self.sid,))
        for day in ("2024-01-31", "2024-02-01", "2024-02-29", "2024-03-01"):
            self.conn.execute(
                "INSERT INTO daily_records (store_id, date) VALUES (?, ?)",
                (self.sid, day))
            self.conn.execute(
                "INSERT INTO actions (store_id, raised_date) VALUES (?, ?)",
                (self.sid, day))
        for day in ("2024-02-01", "2024-02-29", "2024-03-01"):
            self.conn.execute(
                "INSERT INTO public_holidays (store_id, date) VALUES (?, ?)",
                (self.sid, day))
        self.conn.commit()
        self.repo = OperationsRepository(self.db)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_half_open_ranges_include_leap_day_and_exclude_neighbours(self):
        self.assertEqual(
            [d.date.isoformat() for d in self.repo.get_daily_records(self.sid, "2024-02")],
            ["2024-02-01", "2024-02-29"])
        self.assertEqual(
            [d.isoformat() for d in self.repo.get_public_holidays(self.sid, "2024-02")],
            ["2024-02-01", "2024-02-29"])
        self.assertEqual(
            [a.raised_date.isoformat() for a in self.repo.get_actions(self.sid, "2024-02")],
            ["2024-02-01", "2024-02-29"])

    def test_query_plans_use_indexes_without_duplicate_daily_or_holiday_indexes(self):
        cases = (
            ("daily_records", "date", "sqlite_autoindex_daily_records_1"),
            ("public_holidays", "date", "sqlite_autoindex_public_holidays_1"),
            ("actions", "raised_date", "idx_actions_store_raised_date"),
        )
        for table, column, index in cases:
            with self.subTest(table=table):
                plan = self.conn.execute(
                    f"EXPLAIN QUERY PLAN SELECT * FROM {table} "
                    f"WHERE store_id = ? AND {column} >= ? AND {column} < ?",
                    (self.sid, "2024-02-01", "2024-03-01")).fetchall()
                self.assertTrue(any(index in row["detail"] for row in plan), plan)
        names = {r[0] for r in self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'")}
        self.assertNotIn("idx_daily_records_store_date", names)
        self.assertNotIn("idx_public_holidays_store_date", names)

    def test_v9_migration_adds_only_actions_index(self):
        self.conn.close()
        legacy = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(legacy)
        conn.execute("PRAGMA user_version = 8")
        conn.executescript("""
            CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY);
            INSERT INTO schema_migrations VALUES (8);
            CREATE TABLE actions (
                id INTEGER PRIMARY KEY, store_id INTEGER, raised_date TEXT);
        """)
        conn.close()
        store_ops_db.init_db(legacy)
        self.conn = store_ops_db.connect(legacy)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 9)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'index' "
            "AND name = 'idx_actions_store_raised_date'").fetchone()[0], 1)
        store_ops_db.init_db(legacy)


class ExpenseAggregationTests(unittest.TestCase):
    @staticmethod
    def expense(**changes):
        values = dict(incurred_date=date(2026, 1, 31),
                      paid_date=date(2026, 2, 1), category_id=1,
                      description="Synthetic", amount_cents=100,
                      paid_by="Petty Cash", status="Paid",
                      verification="Verified", recorded_by="Tester", store_id=1)
        values.update(changes)
        return ExpenseInput(**values)

    def test_category_map_matches_individual_totals_and_cross_month_rules(self):
        rows = [self.expense(),
                self.expense(category_id=2, amount_cents=250,
                             incurred_date=date(2026, 2, 28),
                             paid_date=date(2026, 3, 1)),
                self.expense(category_id=2, amount_cents=50, paid_date=None,
                             status="Pending", paid_by=None),
                self.expense(amount_cents=900, store_id=2),
                self.expense(amount_cents=900, paid_date=None),
                self.expense(amount_cents=-25, paid_date=date(2026, 2, 28))]
        totals = kpi.category_paid_totals_cents(rows, "2026-02", 1)
        self.assertEqual(totals, {1: 75})
        for category in (1, 2, 3):
            self.assertEqual(
                kpi.category_paid_mtd_cents(rows, "2026-02", 1, category),
                totals.get(category, 0))
        self.assertEqual(kpi.expense_totals_cents(rows, "2026-02", 1),
                         {"paid": 75, "incurred": 250, "open": 50})
        self.assertEqual(kpi.expense_totals_cents(rows, "2026-01", 1)["incurred"], 1025)

    def test_report_reads_category_totals_once_per_report(self):
        rows = [self.expense(), self.expense(category_id=2, amount_cents=200)]
        original = kpi.category_paid_totals_cents
        calls = []
        def counted(*args):
            calls.append(args)
            return original(*args)
        repo = type("Repo", (), {
            "get_store": lambda *_: type("Store", (), {})(),
            "get_month_config": lambda *_: kpi.MonthConfig(month="2026-02"),
            "get_daily_records": lambda *_: [],
            "get_expense_transactions": lambda *_: [type("E", (), {"input": e})() for e in rows],
            "get_expense_categories": lambda *_: [
                type("C", (), {"id": 1, "name": "One"})(),
                type("C", (), {"id": 2, "name": "Two"})()],
            "get_expense_budgets": lambda *_: {},
            "get_contribution_signs": lambda *_: {},
            "get_actions": lambda *_: [],
        })()
        with patch("ducklab.operations_service.kpi.category_paid_totals_cents", counted):
            report = assemble_month(repo, 1, "2026-02")
        self.assertEqual(len(calls), 1)
        self.assertEqual([c.paid_mtd_cents for c in report.expenses.categories], [100, 200])


class DashboardReuseTests(unittest.TestCase):
    def test_dashboard_reuses_report_records_and_counts(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = AppService(Path(tmp) / "dashboard.db")
            admin = app.auth.bootstrap_first_admin(
                "admin@example.com", "Admin", "Sup3rSecure!x")
            sid = app.register_store(admin, "S01", "Synthetic")
            app.configure_month(admin, sid, "2026-02", level1_base_cents=100000,
                min_rtm_target_cents=100000, level2_pct=.1, level3_pct=.2,
                level4_pct=.3, salary_basic_allowance_cents=None)
            app.record_daily(admin, sid, date(2026, 2, 1), sales_cents=None)
            app.record_daily(admin, sid, date(2026, 2, 2), sales_cents=0)
            before = app.preview_month(admin, sid, "2026-02", as_of=date(2026, 2, 28))
            with patch.object(overview_module, "app_service", app), \
                 patch.object(app, "get_daily_records",
                              side_effect=AssertionError("duplicate daily read")), \
                 patch.object(app, "get_expense_transactions",
                              side_effect=AssertionError("duplicate expense read")), \
                 patch.object(app, "get_actions",
                              side_effect=AssertionError("duplicate action read")):
                dashboard = compute_overview(admin, sid, "2026-02", date(2026, 2, 28))
            after = app.preview_month(admin, sid, "2026-02", as_of=date(2026, 2, 28))
            self.assertEqual(before, after)
            self.assertEqual(dashboard["cc"]["sales_mtd"], "RM 0.00")
            self.assertEqual(dashboard["cc"]["days"], "1 / 28")
            self.assertEqual(dashboard["glance"][-1]["value"], "0")
            self.assertIsNone(before.manpower.total_salary_cents)
            self.assertEqual(before.manpower.salary_ratio_status, "Awaiting Payroll")
            self.assertEqual([d.sales_cents for d in after.days], [None, 0])


if __name__ == "__main__":
    unittest.main()
