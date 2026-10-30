"""Tests for operations_repository + operations_service (temp DBs only).

Run: python -m unittest tests.test_operations_repository -v
"""

import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import kpi, store_ops_db
from ducklab.operations_repository import (
    ConfigNotFoundError, InvalidMonthError, OperationsRepository,
    RepositoryError, StoreNotFoundError,
)
from ducklab.operations_service import assemble_month


def rm(v) -> int:
    return int(Decimal(str(v)) * 100)


class RepoFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "repo_test.db"
        store_ops_db.init_db(self.db_path)
        self.conn = store_ops_db.connect(self.db_path)
        self.conn.isolation_level = None  # autocommit: repo reads see test inserts
        self.repo = OperationsRepository(self.db_path)

    def tearDown(self):
        self.conn.close()
        self._tmp.cleanup()

    def add_store(self, code, name, active=1):
        cur = self.conn.execute(
            "INSERT INTO stores (code, name, concept, zone, zone_pic, "
            "branch_manager, active) VALUES (?,?,?,?,?,?,?)",
            (code, name, "DIY", "Z01", "Staff A " + code, "Staff B " + code, active),
        )
        return cur.lastrowid

    def add_config(self, store_id, month="2026-09", **kw):
        cols = ["store_id", "month"] + list(kw)
        vals = [store_id, month] + list(kw.values())
        cur = self.conn.execute(
            f"INSERT INTO monthly_configs ({','.join(cols)}) "
            f"VALUES ({','.join('?' for _ in cols)})", vals,
        )
        return cur.lastrowid


class TestStores(RepoFixture):
    def test_list_and_lookup(self):
        s1 = self.add_store("S01", "Store A")
        s2 = self.add_store("AB12", "Other Store", active=0)
        stores = self.repo.list_stores()
        self.assertEqual([s.code for s in stores], ["AB12", "S01"])
        self.assertEqual(self.repo.get_store(s1).name, "Store A")
        self.assertEqual(self.repo.get_store_by_code("AB12").zone, "Z01")
        # Inactive stores remain accessible for historical reporting.
        inactive = self.repo.get_store(s2)
        self.assertFalse(inactive.active)
        self.assertEqual(inactive.branch_manager, "Staff B AB12")

    def test_unknown_store(self):
        with self.assertRaises(StoreNotFoundError):
            self.repo.get_store(999)
        with self.assertRaises(StoreNotFoundError):
            self.repo.get_store_by_code("NOPE")


class TestMonthConfig(RepoFixture):
    def test_lookup_and_nullable_payroll(self):
        sid = self.add_store("S01", "Store A")
        self.add_config(sid, "2026-09", level1_base_cents=rm(200000),
                        salary_ratio_target=0.06818, opening_headcount_ft=9)
        cfg = self.repo.get_month_config(sid, "2026-09")
        self.assertEqual(cfg.level1_base_cents, rm(200000))
        self.assertEqual(cfg.salary_ratio_target, Decimal("0.06818"))  # REAL->Decimal safe
        self.assertIsNone(cfg.salary_basic_allowance_cents)  # awaiting payroll

    def test_missing_config_not_substituted(self):
        sid = self.add_store("S01", "Store A")
        self.add_config(sid, "2026-09")
        with self.assertRaises(ConfigNotFoundError):
            self.repo.get_month_config(sid, "2026-10")
        with self.assertRaises(ConfigNotFoundError):
            self.repo.get_month_config(999, "2026-09")

    def test_invalid_month_rejected(self):
        sid = self.add_store("S01", "Store A")
        for bad in ("2026-13", "2026-9", "junk", ""):
            with self.assertRaises(InvalidMonthError):
                self.repo.get_month_config(sid, bad)

    def test_store_specific_configs(self):
        s1 = self.add_store("A01", "A")
        s2 = self.add_store("B02", "B")
        self.add_config(s1, "2026-09", level1_base_cents=rm(100000))
        self.add_config(s2, "2026-09", level1_base_cents=rm(200000))
        self.assertEqual(self.repo.get_month_config(s1, "2026-09").level1_base_cents,
                         rm(100000))
        self.assertEqual(self.repo.get_month_config(s2, "2026-09").level1_base_cents,
                         rm(200000))


class TestDailyRecords(RepoFixture):
    def test_retrieval_and_null_vs_zero(self):
        sid = self.add_store("S01", "Store A")
        other = self.add_store("B02", "B")
        self.conn.execute(
            "INSERT INTO daily_records (store_id, date, sales_cents, transactions, "
            "units, ft_working, pt_working) VALUES (?,?,?,?,?,?,?)",
            (sid, "2026-09-02", 0, 0, 0, 7, 1))          # reported zero-sales day
        self.conn.execute(
            "INSERT INTO daily_records (store_id, date, sales_cents) VALUES (?,?,?)",
            (sid, "2026-09-01", None))                     # unreported
        self.conn.execute(
            "INSERT INTO daily_records (store_id, date, sales_cents) VALUES (?,?,?)",
            (other, "2026-09-01", rm(999)))                # foreign store
        self.conn.execute(
            "INSERT INTO daily_records (store_id, date, sales_cents) VALUES (?,?,?)",
            (sid, "2026-10-01", rm(500)))                  # different month
        days = self.repo.get_daily_records(sid, "2026-09")
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0].date, date(2026, 9, 1))   # chronological
        self.assertIsNone(days[0].sales_cents)
        self.assertEqual(days[1].sales_cents, 0)
        self.assertEqual(days[1].ft_working, 7)

    def test_empty_month_returns_empty(self):
        sid = self.add_store("S01", "Store A")
        self.assertEqual(self.repo.get_daily_records(sid, "2026-09"), [])


class TestExpenses(RepoFixture):
    def setUp(self):
        super().setUp()
        self.sid = self.add_store("S01", "Store A")
        self.other = self.add_store("B02", "B")
        self.cat = self.conn.execute(
            "INSERT INTO expense_categories (store_id, name) VALUES (?,?)",
            (self.sid, "Electricity")).lastrowid
        self.cat_other = self.conn.execute(
            "INSERT INTO expense_categories (store_id, name) VALUES (?,?)",
            (self.other, "Electricity")).lastrowid

    def add_expense(self, store_id, cat, incurred, paid, amount, status="Paid",
                    verification="Verified", recorded_by="Staff A",
                    paid_by="Petty Cash", desc="x"):
        cur = self.conn.execute(
            "INSERT INTO expense_transactions (store_id, incurred_date, paid_date, "
            "category_id, description, amount_cents, paid_by, status, verification, "
            "recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (store_id, incurred, paid, cat, desc, amount, paid_by, status,
             verification, recorded_by))
        return cur.lastrowid

    def test_categories_store_scoped(self):
        cats = self.repo.get_expense_categories(self.sid)
        self.assertEqual([c.id for c in cats], [self.cat])
        self.assertNotEqual(self.cat, self.cat_other)

    def test_budgets(self):
        cfg = self.add_config(self.sid, "2026-09")
        self.conn.execute(
            "INSERT INTO monthly_expense_budgets (store_id, monthly_config_id, "
            "category_id, budget_cents) VALUES (?,?,?,?)",
            (self.sid, cfg, self.cat, 400000))
        budgets = self.repo.get_expense_budgets(self.sid, "2026-09")
        self.assertEqual(budgets, {self.cat: 400000})
        self.assertEqual(self.repo.get_expense_budgets(self.other, "2026-09"), {})

    def test_cross_month_transaction_retrieved_once(self):
        # Incurred Aug 28, paid Sep 3: single row, visible to both period calcs.
        self.add_expense(self.sid, self.cat, "2026-08-28", "2026-09-03", rm(100))
        rows = self.repo.get_expense_transactions(self.sid)
        self.assertEqual(len(rows), 1)
        e = rows[0].input
        self.assertEqual(e.incurred_date, date(2026, 8, 28))
        self.assertEqual(e.paid_date, date(2026, 9, 3))
        inputs = [r.input for r in rows]
        self.assertEqual(kpi.expense_totals_cents(inputs, "2026-09", self.sid)["paid"],
                         rm(100))
        self.assertEqual(kpi.expense_totals_cents(inputs, "2026-08", self.sid)["incurred"],
                         rm(100))

    def test_incomplete_records_not_filtered(self):
        self.add_expense(self.sid, self.cat, "2026-09-05", None, rm(280),
                         status="Paid", recorded_by=None)
        rows = self.repo.get_expense_transactions(self.sid)
        self.assertEqual(len(rows), 1)
        status = kpi.expense_validation(rows[0].input, {self.cat})
        self.assertEqual(status, "Missing paid date")  # ladder sees draft rows

    def test_store_isolation(self):
        self.add_expense(self.sid, self.cat, "2026-09-01", "2026-09-02", rm(50))
        self.add_expense(self.other, self.cat_other, "2026-09-01", "2026-09-02", rm(999))
        rows = self.repo.get_expense_transactions(self.sid)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].input.amount_cents, rm(50))


class TestStock(RepoFixture):
    def test_codes_and_signs(self):
        signs = self.repo.get_contribution_signs()
        self.assertEqual(len(signs), 13)
        self.assertEqual(signs["EXTRA"], -1)
        codes = {c.code for c in self.repo.get_stock_loss_codes()}
        self.assertEqual(codes, {"DMO", "DMC", "DMP", "2DMP", "DPI", "DMW", "DDR",
                                 "DMN", "DME", "DMQ", "DST", "MISSING", "EXTRA"})

    def test_daily_losses_attached(self):
        sid = self.add_store("S01", "Store A")
        rec = self.conn.execute(
            "INSERT INTO daily_records (store_id, date) VALUES (?,?)",
            (sid, "2026-09-01")).lastrowid
        self.conn.execute(
            "INSERT INTO daily_stock_losses (daily_record_id, code, amount_cents) "
            "VALUES (?,?,?)", (rec, "DMO", rm(70)))
        self.conn.execute(
            "INSERT INTO daily_stock_losses (daily_record_id, code, amount_cents) "
            "VALUES (?,?,?)", (rec, "EXTRA", rm(5)))
        days = self.repo.get_daily_records(sid, "2026-09")
        self.assertEqual(days[0].stock_losses, {"DMO": rm(70), "EXTRA": rm(5)})
        self.assertEqual(kpi.total_loss_cents(days[0].stock_losses,
                                              self.repo.get_contribution_signs()),
                         rm(65))


class TestActions(RepoFixture):
    def test_month_and_unscoped_retrieval(self):
        sid = self.add_store("S01", "Store A")
        self.conn.execute(
            "INSERT INTO actions (store_id, raised_date, pillar, issue, priority, "
            "status, pic, due_date) VALUES (?,?,?,?,?,?,?,?)",
            (sid, "2026-09-10", "Sales Performance", "Low pace", "High", "Open",
             "Staff A", "2026-09-20"))
        self.conn.execute(
            "INSERT INTO actions (store_id, raised_date, pillar, status) "
            "VALUES (?,?,?,?)",
            (sid, "2026-08-05", "Stock Management", "Done"))
        in_sep = self.repo.get_actions(sid, "2026-09")
        all_actions = self.repo.get_actions(sid)
        self.assertEqual(len(in_sep), 1)
        self.assertEqual(len(all_actions), 2)
        self.assertEqual(in_sep[0].input.status, "Open")
        self.assertEqual(in_sep[0].input.due_date, date(2026, 9, 20))
        self.assertEqual(in_sep[0].priority, "High")


class TestSnapshots(RepoFixture):
    def test_latest_version_and_specific_version(self):
        sid = self.add_store("S01", "Store A")
        self.conn.execute(
            "INSERT INTO monthly_snapshots (store_id, month, version, kpi_json, "
            "finalized) VALUES (?,?,?,?,1)", (sid, "2026-09", 1, '{"v":1}'))
        self.conn.execute(
            "INSERT INTO monthly_snapshots (store_id, month, version, kpi_json) "
            "VALUES (?,?,?,?)", (sid, "2026-09", 2, '{"v":2}'))
        self.assertEqual(self.repo.get_snapshot(sid, "2026-09")["version"], 2)
        self.assertEqual(self.repo.get_snapshot(sid, "2026-09", version=1)["kpi_json"],
                         '{"v":1}')
        self.assertTrue(self.repo.get_snapshot(sid, "2026-09", version=1)["finalized"])
        self.assertIsNone(self.repo.get_snapshot(sid, "2026-10"))


class TestEndToEndIntegration(RepoFixture):
    """Two stores, full pipeline: repository -> KPI engine -> typed report."""

    def _seed_store(self, code, base_cents, sales_rm, pt_factor=0.5):
        sid = self.add_store(code, f"Store {code}")
        cfg = self.add_config(
            sid, "2026-09", min_rtm_target_cents=rm(200000),
            level1_base_cents=base_cents, level2_pct=0.1, level3_pct=0.2,
            level4_pct=0.3, sales_last_year_cents=rm(100000),
            sales_last_month_cents=rm(90000), salary_ratio_target=0.06818,
            budget_headcount=9, turnover_threshold=0.45, stock_loss_target=0.015,
            opening_headcount_ft=9, part_timer_factor=pt_factor,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
        cat = self.conn.execute(
            "INSERT INTO expense_categories (store_id, name) VALUES (?,?)",
            (sid, "Electricity")).lastrowid
        self.conn.execute(
            "INSERT INTO monthly_expense_budgets (store_id, monthly_config_id, "
            "category_id, budget_cents) VALUES (?,?,?,?)",
            (sid, cfg, cat, rm(4000)))
        for i, s in enumerate(sales_rm):
            rec = self.conn.execute(
                "INSERT INTO daily_records (store_id, date, sales_cents, transactions, "
                "units, ft_working, pt_working) VALUES (?,?,?,?,?,?,?)",
                (sid, f"2026-09-{i+1:02d}", rm(s), 100, 300, 7, 1)).lastrowid
            self.conn.execute(
                "INSERT INTO daily_stock_losses (daily_record_id, code, amount_cents) "
                "VALUES (?,?,?)", (rec, "DMO", rm(10)))
        return sid, cat

    def test_two_store_pipeline(self):
        s1, cat1 = self._seed_store("A01", rm(200000), [10000] * 30)
        s2, cat2 = self._seed_store("B02", rm(150000), [2000] * 30, pt_factor=0.4)
        # Expenses: s1 paid-in-month, plus a cross-month item for s1; s2 foreign.
        self.conn.execute(
            "INSERT INTO expense_transactions (store_id, incurred_date, paid_date, "
            "category_id, description, amount_cents, paid_by, status, verification, "
            "recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (s1, "2026-09-01", "2026-09-02", cat1, "bill", rm(3000), "HQ / Central",
             "Paid", "Verified", "Staff A"))
        self.conn.execute(
            "INSERT INTO expense_transactions (store_id, incurred_date, paid_date, "
            "category_id, description, amount_cents, paid_by, status, verification, "
            "recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (s1, "2026-08-28", "2026-09-03", cat1, "aug item", rm(100), "Petty Cash",
             "Paid", "Verified", "Staff A"))
        self.conn.execute(
            "INSERT INTO expense_transactions (store_id, incurred_date, paid_date, "
            "category_id, description, amount_cents, paid_by, status, verification, "
            "recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (s2, "2026-09-01", "2026-09-02", cat2, "bill", rm(500), "Petty Cash",
             "Paid", "Verified", "Staff B"))
        # Actions
        self.conn.execute(
            "INSERT INTO actions (store_id, raised_date, pillar, status, due_date) "
            "VALUES (?,?,?,?,?)",
            (s1, "2026-09-01", "Sales Performance", "Open", "2026-09-05"))
        self.conn.execute(
            "INSERT INTO actions (store_id, raised_date, pillar, status) "
            "VALUES (?,?,?,?)", (s2, "2026-09-01", "Stock Management", "Done"))

        r1 = assemble_month(self.repo, s1, "2026-09", as_of=date(2026, 9, 30))
        r2 = assemble_month(self.repo, s2, "2026-09", as_of=date(2026, 9, 30))

        # Sales pillar: 30 x 10000 = 300,000 vs target 200,000 -> Achieved.
        self.assertEqual(r1.sales.sales_mtd_cents, rm(300000))
        self.assertEqual(r1.sales.status_by_level["level1"], "Achieved")
        self.assertEqual(r2.sales.sales_mtd_cents, rm(60000))
        self.assertEqual(r2.sales.status_by_level["level1"], "Below target")

        # Expenses pillar: cross-month paid item counts in September for s1.
        self.assertEqual(r1.expenses.paid_cents, rm(3100))
        self.assertEqual(r2.expenses.paid_cents, rm(500))
        self.assertEqual(r1.expenses.categories[0].name, "Electricity")

        # Manpower pillar: salary 11,000 / 300,000 = 3.67% within target.
        self.assertEqual(r1.manpower.total_salary_cents, rm(11000))
        self.assertEqual(r1.manpower.salary_ratio_status, "Within target")
        self.assertEqual(r1.manpower.average_effective_staff, Decimal("7.5"))
        self.assertEqual(r2.manpower.average_effective_staff, Decimal("7.4"))  # 0.4 factor

        # Stock pillar: 30 days x RM10 DMO = 300.
        self.assertEqual(r1.stock.total_loss_cents, rm(300))
        self.assertEqual(r1.stock.status, "Within target")

        # Actions pillar: s1 has 1 open overdue (due 9/5 < 9/30); s2 has 1 done.
        self.assertEqual(r1.actions.open, 1)
        self.assertEqual(r1.actions.counts["overdue"], 1)
        self.assertEqual(r2.actions.open, 0)
        self.assertEqual(r2.actions.completion_pct, Decimal(1))

        # Isolation: changing store changes results; no contamination.
        self.assertNotEqual(r1.sales.sales_mtd_cents, r2.sales.sales_mtd_cents)
        self.assertNotEqual(r1.expenses.paid_cents, r2.expenses.paid_cents)
        self.assertFalse(r1.is_snapshot)

    def test_historical_month_still_queryable(self):
        sid, _ = self._seed_store("A01", rm(200000), [10000] * 30)
        self.add_config(sid, "2026-08", level1_base_cents=rm(250000),
                        opening_headcount_ft=8)
        self.conn.execute(
            "UPDATE stores SET active = 0 WHERE id = ?", (sid,))  # archived store
        r = assemble_month(self.repo, sid, "2026-08", as_of=date(2026, 8, 31))
        self.assertEqual(r.sales.sales_mtd_cents, 0)  # no daily rows in Aug
        self.assertEqual(r.sales.days_entered, 0)
        self.assertIsNone(r.sales.month_end_sales_cents)
        self.assertFalse(r.store.active)

    def test_empty_dataset_safe(self):
        sid = self.add_store("EMPTY", "Empty Store")
        self.add_config(sid, "2026-09", opening_headcount_ft=5)
        r = assemble_month(self.repo, sid, "2026-09")
        self.assertEqual(r.sales.sales_mtd_cents, 0)
        self.assertEqual(r.expenses.paid_cents, 0)
        self.assertEqual(r.stock.total_loss_cents, 0)
        self.assertEqual(r.actions.open, 0)
        self.assertIsNone(r.manpower.salary_ratio_store)
        self.assertEqual(r.manpower.salary_ratio_status, "Awaiting Payroll")


if __name__ == "__main__":
    unittest.main()
