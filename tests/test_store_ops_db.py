"""Tests for the Store Operations Monitoring database layer (store_ops_db).

Uses only temporary databases; never touches placement_tracker.db or the real
store_ops.db. Run: python -m unittest tests.test_store_ops_db -v
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ducklab import store_ops_db

PROTOTYPE_DB = Path(store_ops_db.__file__).parent / "placement_tracker.db"


class StoreOpsDbTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "test_store_ops.db"
        store_ops_db.init_db(self.db_path)
        self.conn = store_ops_db.connect(self.db_path)

    def tearDown(self) -> None:
        self.conn.close()
        self._tmp.cleanup()

    # -- helpers -----------------------------------------------------------
    def add_store(self, code="ZI68", name="SLP") -> int:
        cur = self.conn.execute(
            "INSERT INTO stores (code, name, concept, zone, zone_pic, "
            "branch_manager) VALUES (?,?,?,?,?,?)",
            (code, name, "DIY", "KV4", "NURDIN AHMAD", "AZRUL HAZRIQ"),
        )
        return cur.lastrowid

    def add_category(self, store_id: int, name="Electricity") -> int:
        cur = self.conn.execute(
            "INSERT INTO expense_categories (store_id, name) VALUES (?,?)",
            (store_id, name),
        )
        return cur.lastrowid

    def add_config(self, store_id: int, month="2026-09") -> int:
        cur = self.conn.execute(
            "INSERT INTO monthly_configs (store_id, month, opening_headcount_ft, "
            "part_timer_factor) VALUES (?,?,?,?)",
            (store_id, month, 9, 0.5),
        )
        return cur.lastrowid

    def add_daily(self, store_id: int, date="2026-09-01", sales_cents=None) -> int:
        cur = self.conn.execute(
            "INSERT INTO daily_records (store_id, date, sales_cents) VALUES (?,?,?)",
            (store_id, date, sales_cents),
        )
        return cur.lastrowid


class TestCreation(StoreOpsDbTestCase):
    def test_database_file_created(self):
        self.assertTrue(self.db_path.exists())

    def test_all_tables_exist(self):
        tables = {
            r[0]
            for r in self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        expected = {
            "schema_migrations", "stores", "expense_categories",
            "stock_loss_codes", "monthly_configs", "monthly_expense_budgets",
            "daily_records", "daily_stock_losses", "expense_transactions",
            "actions", "monthly_snapshots",
        }
        self.assertTrue(expected.issubset(tables), expected - tables)

    def test_schema_version_recorded(self):
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 1)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1
        )

    def test_idempotent_initialization(self):
        store_ops_db.init_db(self.db_path)
        store_ops_db.init_db(self.db_path)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0], 1)
        # Reference data not duplicated.
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM stock_loss_codes").fetchone()[0], 13
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0], 1
        )

    def test_foreign_keys_enabled(self):
        self.assertEqual(self.conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)


class TestStores(StoreOpsDbTestCase):
    def test_unique_store_code(self):
        self.add_store()
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_store()  # same code

    def test_store_defaults_active(self):
        sid = self.add_store()
        row = self.conn.execute("SELECT active FROM stores WHERE id=?", (sid,)).fetchone()
        self.assertEqual(row[0], 1)


class TestMonthlyConfigs(StoreOpsDbTestCase):
    def test_unique_store_month(self):
        sid = self.add_store()
        self.add_config(sid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_config(sid)  # same store + month

    def test_same_month_different_stores_allowed(self):
        s1 = self.add_store("A01", "Store A")
        s2 = self.add_store("B02", "Store B")
        self.add_config(s1)
        self.add_config(s2)  # must not clash

    def test_month_end_inputs_nullable(self):
        sid = self.add_store()
        cid = self.add_config(sid)
        row = self.conn.execute(
            "SELECT salary_basic_allowance_cents, overtime_cents, "
            "support_team_cost_cents FROM monthly_configs WHERE id=?", (cid,)
        ).fetchone()
        self.assertEqual(tuple(row), (None, None, None))  # awaiting payroll


class TestDailyRecords(StoreOpsDbTestCase):
    def test_unique_store_date(self):
        sid = self.add_store()
        self.add_daily(sid)
        with self.assertRaises(sqlite3.IntegrityError):
            self.add_daily(sid)

    def test_null_vs_zero_sales(self):
        sid = self.add_store()
        unreported = self.add_daily(sid, "2026-09-01", None)
        zero_day = self.add_daily(sid, "2026-09-02", 0)
        rows = dict(
            self.conn.execute(
                "SELECT id, sales_cents FROM daily_records"
            ).fetchall()
        )
        self.assertIsNone(rows[unreported])   # not reported
        self.assertEqual(rows[zero_day], 0)   # reported zero-sales day


class TestBudgetsAndCrossStore(StoreOpsDbTestCase):
    def test_budget_insert_valid(self):
        sid = self.add_store()
        cfg = self.add_config(sid)
        cat = self.add_category(sid)
        self.conn.execute(
            "INSERT INTO monthly_expense_budgets (store_id, monthly_config_id, "
            "category_id, budget_cents) VALUES (?,?,?,?)",
            (sid, cfg, cat, 320000),
        )

    def test_cross_store_config_rejected(self):
        s1 = self.add_store("A01", "Store A")
        s2 = self.add_store("B02", "Store B")
        cfg_s1 = self.add_config(s1)
        cat_s2 = self.add_category(s2)
        # budget belongs to store 1 but category belongs to store 2
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO monthly_expense_budgets (store_id, monthly_config_id, "
                "category_id, budget_cents) VALUES (?,?,?,?)",
                (s1, cfg_s1, cat_s2, 100),
            )

    def test_cross_store_expense_category_rejected(self):
        s1 = self.add_store("A01", "Store A")
        s2 = self.add_store("B02", "Store B")
        cat_s2 = self.add_category(s2)
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO expense_transactions (store_id, category_id, status) "
                "VALUES (?,?, 'Pending')",
                (s1, cat_s2),
            )

    def test_category_rename_keeps_id(self):
        sid = self.add_store()
        cat = self.add_category(sid, "Electricity")
        self.conn.execute(
            "UPDATE expense_categories SET name='Power' WHERE id=?", (cat,)
        )
        row = self.conn.execute(
            "SELECT id, name FROM expense_categories WHERE id=?", (cat,)
        ).fetchone()
        self.assertEqual((row[0], row[1]), (cat, "Power"))


class TestExpenseTransactions(StoreOpsDbTestCase):
    def test_lifecycle_pending_paid_cancelled(self):
        sid = self.add_store()
        cat = self.add_category(sid)
        for status in ("Pending", "Paid", "Cancelled"):
            self.conn.execute(
                "INSERT INTO expense_transactions (store_id, incurred_date, "
                "category_id, amount_cents, status) VALUES (?,?,?,?,?)",
                (sid, "2026-09-05", cat, 8500, status),
            )
        rows = self.conn.execute(
            "SELECT status FROM expense_transactions WHERE store_id=?", (sid,)
        ).fetchall()
        self.assertEqual({r[0] for r in rows}, {"Pending", "Paid", "Cancelled"})

    def test_draft_row_without_paid_date_allowed(self):
        # Workbook validation ladder must detect "Missing paid date" itself.
        sid = self.add_store()
        self.conn.execute(
            "INSERT INTO expense_transactions (store_id, status) VALUES (?, 'Paid')",
            (sid,),
        )
        row = self.conn.execute(
            "SELECT paid_date, amount_cents FROM expense_transactions WHERE store_id=?",
            (sid,),
        ).fetchone()
        self.assertIsNone(row[0])

    def test_invalid_status_rejected(self):
        sid = self.add_store()
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO expense_transactions (store_id, status) VALUES (?, 'Bogus')",
                (sid,),
            )

    def test_cross_month_dates_stored_as_entered(self):
        sid = self.add_store()
        self.conn.execute(
            "INSERT INTO expense_transactions (store_id, incurred_date, paid_date, "
            "status) VALUES (?,?,?, 'Paid')",
            (sid, "2026-08-28", "2026-09-03"),  # incurred Aug, paid Sep
        )
        row = self.conn.execute(
            "SELECT incurred_date, paid_date FROM expense_transactions"
        ).fetchone()
        self.assertEqual(tuple(row), ("2026-08-28", "2026-09-03"))


class TestStockLossCodes(StoreOpsDbTestCase):
    def test_all_13_codes_seeded(self):
        rows = self.conn.execute(
            "SELECT code, contribution_sign FROM stock_loss_codes ORDER BY code"
        ).fetchall()
        self.assertEqual(len(rows), 13)
        signs = {r[0]: r[1] for r in rows}
        for code in ("DMO", "DMC", "DMP", "MISSING"):
            self.assertEqual(signs[code], 1)
        self.assertEqual(signs["EXTRA"], -1)
        for code in ("2DMP", "DPI", "DMW", "DDR", "DMN", "DME", "DMQ", "DST"):
            self.assertEqual(signs[code], 0)

    def test_daily_stock_loss_linked(self):
        sid = self.add_store()
        rec = self.add_daily(sid)
        self.conn.execute(
            "INSERT INTO daily_stock_losses (daily_record_id, code, amount_cents) "
            "VALUES (?,?,?)",
            (rec, "DMO", 7000),
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO daily_stock_losses (daily_record_id, code, amount_cents) "
                "VALUES (?,?,?)",
                (rec, "BOGUS", 1),
            )


class TestSnapshots(StoreOpsDbTestCase):
    def test_version_integrity_and_no_overwrite(self):
        sid = self.add_store()
        cur = self.conn.execute(
            "INSERT INTO monthly_snapshots (store_id, month, version, kpi_json, "
            "finalized) VALUES (?,?,?,?,1)",
            (sid, "2026-09", 1, '{"sales": 35081900}'),
        )
        v1 = cur.lastrowid
        # Same store/month/version rejected.
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO monthly_snapshots (store_id, month, version, kpi_json) "
                "VALUES (?,?,?,?)",
                (sid, "2026-09", 1, "{}"),
            )
        # Correction = new version superseding the old one.
        cur2 = self.conn.execute(
            "INSERT INTO monthly_snapshots (store_id, month, version, kpi_json) "
            "VALUES (?,?,?,?)",
            (sid, "2026-09", 2, '{"sales": 35081901}'),
        )
        self.conn.execute(
            "UPDATE monthly_snapshots SET superseded_by=? WHERE id=?",
            (cur2.lastrowid, v1),
        )
        rows = self.conn.execute(
            "SELECT version, finalized, superseded_by FROM monthly_snapshots "
            "ORDER BY version"
        ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][2], cur2.lastrowid)


class TestPrototypePreservation(unittest.TestCase):
    def test_placement_tracker_db_untouched(self):
        before = PROTOTYPE_DB.stat() if PROTOTYPE_DB.exists() else None
        with tempfile.TemporaryDirectory() as tmp:
            store_ops_db.init_db(Path(tmp) / "x.db")
        after = PROTOTYPE_DB.stat() if PROTOTYPE_DB.exists() else None
        if before is None:
            self.assertIsNone(after)
        else:
            self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)
            self.assertEqual(before.st_size, after.st_size)
        # The module must point at a different file.
        self.assertNotEqual(store_ops_db.DB_PATH.name, "placement_tracker.db")


if __name__ == "__main__":
    unittest.main()
