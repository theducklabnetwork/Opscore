"""Tests for the Store Operations Monitoring database layer (store_ops_db).

Uses only temporary databases; never touches placement_tracker.db or the real
store_ops.db. Run: python -m unittest tests.test_store_ops_db -v
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
    def add_store(self, code="S01", name="Store A") -> int:
        cur = self.conn.execute(
            "INSERT INTO stores (code, name, concept, zone, zone_pic, "
            "branch_manager) VALUES (?,?,?,?,?,?)",
            (code, name, "DIY", "Z01", "Staff A", "Staff B"),
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
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0],
                         store_ops_db.SCHEMA_VERSION)
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            store_ops_db.SCHEMA_VERSION,
        )

    def test_idempotent_initialization(self):
        store_ops_db.init_db(self.db_path)
        store_ops_db.init_db(self.db_path)
        self.assertEqual(self.conn.execute("PRAGMA user_version").fetchone()[0],
                         store_ops_db.SCHEMA_VERSION)
        # Reference data not duplicated.
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM stock_loss_codes").fetchone()[0], 13
        )
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0],
            store_ops_db.SCHEMA_VERSION,
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
            "category_id, budget_pct) VALUES (?,?,?,?)",
            (sid, cfg, cat, 0.025),
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
                "category_id, budget_pct) VALUES (?,?,?,?)",
                (s1, cfg_s1, cat_s2, 0.01),
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
            (sid, "2026-09", 1, '{"sales": 10000000}'),
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
            (sid, "2026-09", 2, '{"sales": 10000001}'),
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


class TestShiftCodesAndScheduleSources(StoreOpsDbTestCase):
    def test_new_database_seeds_shift_codes_in_display_order(self):
        rows = self.conn.execute(
            "SELECT code, label, start_time, end_time, color FROM shift_codes"
        ).fetchall()
        self.assertEqual([tuple(row) for row in rows], store_ops_db.SHIFT_CODES)
        self.assertEqual(
            [row["code"] for row in rows],
            ["M900", "F900", "M945", "F945", "F101", "A121", "ODM", "ODA",
             "OOD", "ORD", "AL", "RPL", "UPL", "EL", "MC", "OPH"],
        )
        for code in ("M900", "F900", "M945", "F945", "F101", "A121"):
            self.assertEqual(
                self.conn.execute(
                    "SELECT color FROM shift_codes WHERE code = ?", (code,)
                ).fetchone()[0],
                "#FFFFFF",
            )
        self.assertEqual(
            tuple(self.conn.execute(
                "SELECT label, start_time, end_time, color FROM shift_codes "
                "WHERE code = 'RPL'"
            ).fetchone()),
            ("Replacement Leave", None, None, "#f5d0fe"),
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM shift_codes WHERE show_in_legend = 1"
            ).fetchone()[0],
            len(store_ops_db.SHIFT_CODES),
        )

    def test_schedule_assignment_source_defaults_to_manual(self):
        store_id = self.add_store()
        staff_id = self.conn.execute(
            "INSERT INTO staff (store_id, name, staff_code, position) "
            "VALUES (?, 'Staff A', 'A01', 'Sales Assistant')",
            (store_id,),
        ).lastrowid
        entry_id = self.conn.execute(
            "INSERT INTO schedule_entries (store_id, staff_id, work_date, code) "
            "VALUES (?, ?, '2026-11-14', 'RPL')",
            (store_id, staff_id),
        ).lastrowid
        self.assertEqual(
            self.conn.execute(
                "SELECT assignment_source FROM schedule_entries WHERE id = ?",
                (entry_id,),
            ).fetchone()[0],
            "manual",
        )


class TestV12Migration(unittest.TestCase):
    def test_v11_upgrade_preserves_custom_colors_and_existing_sources(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v11.db"
            for version in range(1, 12):
                conn = store_ops_db.connect(db_path)
                getattr(store_ops_db, f"_apply_v{version}")(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
                conn.close()

            conn = store_ops_db.connect(db_path)
            conn.execute("UPDATE shift_codes SET color = '#123456' WHERE code = 'M900'")
            conn.execute("UPDATE shift_codes SET label = 'Custom Morning', "
                         "start_time = '08:00', end_time = '18:00' "
                         "WHERE code = 'F900'")
            store_id = conn.execute(
                "INSERT INTO stores (code, name) VALUES ('S01', 'Store A')"
            ).lastrowid
            staff_id = conn.execute(
                "INSERT INTO staff (store_id, name, staff_code, position) "
                "VALUES (?, 'Staff A', 'A01', 'Sales Assistant')",
                (store_id,),
            ).lastrowid
            conn.execute(
                "INSERT INTO schedule_entries (store_id, staff_id, work_date, code) "
                "VALUES (?, ?, '2026-11-14', 'F900')",
                (store_id, staff_id),
            )
            conn.commit()
            conn.close()

            store_ops_db.init_db(db_path, backup_before_migration=False)
            conn = store_ops_db.connect(db_path)
            try:
                colors = dict(conn.execute(
                    "SELECT code, color FROM shift_codes"
                ).fetchall())
                self.assertEqual(colors["M900"], "#123456")
                self.assertEqual(colors["F900"], "#FFFFFF")
                for code in ("M945", "F945", "F101", "A121"):
                    self.assertEqual(colors[code], "#FFFFFF")
                self.assertEqual(colors["RPL"], "#f5d0fe")
                self.assertEqual(
                    tuple(conn.execute(
                        "SELECT label, start_time, end_time FROM shift_codes "
                        "WHERE code = 'F900'"
                    ).fetchone()),
                    ("Custom Morning", "08:00", "18:00"),
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT assignment_source FROM schedule_entries"
                    ).fetchone()[0],
                    "manual",
                )
                self.assertEqual(
                    conn.execute("PRAGMA user_version").fetchone()[0], 17
                )
            finally:
                conn.close()

    def test_v11_upgrade_backfills_abm_public_holidays(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v11-holidays.db"
            for version in range(1, 12):
                conn = store_ops_db.connect(db_path)
                getattr(store_ops_db, f"_apply_v{version}")(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
                conn.close()
            conn = store_ops_db.connect(db_path)
            store_id = conn.execute(
                "INSERT INTO stores (code, name) VALUES ('S01', 'Store A')"
            ).lastrowid
            abm = conn.execute(
                "INSERT INTO staff (store_id, name, staff_code, position) "
                "VALUES (?, 'Manager', 'A01', 'ABM')", (store_id,)).lastrowid
            ra = conn.execute(
                "INSERT INTO staff (store_id, name, staff_code, position) "
                "VALUES (?, 'Assistant', 'A02', 'RA')", (store_id,)).lastrowid
            conn.execute(
                "INSERT INTO public_holidays (store_id, date) "
                "VALUES (?, '2026-12-25'), (?, '2026-12-26')",
                (store_id, store_id))
            conn.execute(
                "INSERT INTO schedule_entries (store_id, staff_id, work_date, code) "
                "VALUES (?, ?, '2026-12-26', 'M900')", (store_id, abm))
            conn.commit()
            conn.close()
            store_ops_db.init_db(db_path, backup_before_migration=False)
            conn = store_ops_db.connect(db_path)
            try:
                self.assertEqual([tuple(row) for row in conn.execute(
                    "SELECT work_date, code, assignment_source FROM schedule_entries "
                    "ORDER BY work_date")],
                    [("2026-12-25", "OPH", "auto_holiday"),
                     ("2026-12-26", "OPH", "auto_holiday")])
                self.assertIsNone(conn.execute(
                    "SELECT 1 FROM schedule_entries WHERE staff_id = ?",
                    (ra,)).fetchone())
            finally:
                conn.close()


class TestV14Migration(unittest.TestCase):
    def test_v14_upgrade_replaces_abm_holiday_shifts_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v13-holidays.db"
            for version in range(1, 14):
                conn = store_ops_db.connect(db_path)
                getattr(store_ops_db, f"_apply_v{version}")(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
                conn.close()
            conn = store_ops_db.connect(db_path)
            store_id = conn.execute(
                "INSERT INTO stores (code, name) VALUES ('S01', 'Store A')"
            ).lastrowid
            abm = conn.execute(
                "INSERT INTO staff (store_id, name, staff_code, position) "
                "VALUES (?, 'Manager', 'A01', 'ABM')", (store_id,)).lastrowid
            ra = conn.execute(
                "INSERT INTO staff (store_id, name, staff_code, position) "
                "VALUES (?, 'Assistant', 'A02', 'RA')", (store_id,)).lastrowid
            conn.execute(
                "INSERT INTO public_holidays (store_id, date) "
                "VALUES (?, '2026-12-25'), (?, '2026-12-26')",
                (store_id, store_id))
            conn.executemany(
                "INSERT INTO schedule_entries "
                "(store_id, staff_id, work_date, code) VALUES (?, ?, ?, 'M900')",
                [(store_id, abm, "2026-12-25"),
                 (store_id, abm, "2026-12-27"),
                 (store_id, ra, "2026-12-26")])
            conn.commit()
            conn.close()
            store_ops_db.init_db(db_path, backup_before_migration=False)
            conn = store_ops_db.connect(db_path)
            try:
                self.assertEqual([tuple(row) for row in conn.execute(
                    "SELECT staff_id, work_date, code, assignment_source "
                    "FROM schedule_entries ORDER BY staff_id, work_date")],
                    [(abm, "2026-12-25", "OPH", "auto_holiday"),
                     (abm, "2026-12-26", "OPH", "auto_holiday"),
                     (abm, "2026-12-27", "M900", "manual"),
                     (ra, "2026-12-26", "M900", "manual")])
            finally:
                conn.close()


class TestV13Migration(unittest.TestCase):
    def test_v12_upgrade_enables_existing_codes_in_view_legend(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "v12.db"
            for version in range(1, 13):
                conn = store_ops_db.connect(db_path)
                getattr(store_ops_db, f"_apply_v{version}")(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
                conn.close()
            store_ops_db.init_db(db_path, backup_before_migration=False)
            conn = store_ops_db.connect(db_path)
            try:
                self.assertEqual(
                    conn.execute("PRAGMA user_version").fetchone()[0], 17)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM shift_codes WHERE show_in_legend = 1"
                ).fetchone()[0], len(store_ops_db.SHIFT_CODES))
            finally:
                conn.close()


class TestV15AuthenticationMigration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "auth-migration.db"

    def tearDown(self):
        self.tmp.cleanup()

    def make_v14(self):
        for version in range(1, 15):
            conn = store_ops_db.connect(self.db_path)
            getattr(store_ops_db, f"_apply_v{version}")(conn)
            conn.execute(f"PRAGMA user_version = {version}")
            conn.commit()
            conn.close()

    def test_v14_auth_records_upgrade_losslessly(self):
        self.make_v14()
        conn = store_ops_db.connect(self.db_path)
        store_ops_db._apply_v15(conn)
        conn.execute("INSERT INTO auth_users (email, display_name, password_hash) "
                     "VALUES ('old@example.com', 'Old Account', 'hash')")
        user_id = conn.execute("SELECT id FROM auth_users").fetchone()[0]
        conn.execute("INSERT INTO auth_sessions (token_hash, user_id, expires_at) "
                     "VALUES ('session', ?, '2099-01-01')", (user_id,))
        conn.execute("INSERT INTO auth_recovery_keys (user_id, key_hash) "
                     "VALUES (?, 'recovery')", (user_id,))
        conn.execute("UPDATE auth_users SET tour_done = 1 WHERE id = ?", (user_id,))
        conn.execute("INSERT INTO auth_page_tours (user_id, page_key) VALUES (?, 'dashboard')",
                     (user_id,))
        conn.execute("INSERT INTO auth_roles (name) VALUES ('custom_role')")
        role_id = conn.execute("SELECT id FROM auth_roles WHERE name = 'custom_role'").fetchone()[0]
        conn.execute("INSERT INTO auth_user_roles VALUES (?, ?, datetime('now'))", (user_id, role_id))
        store_id = conn.execute("INSERT INTO stores (code, name) VALUES ('OLD', 'Old Store')").lastrowid
        conn.execute("INSERT INTO auth_user_store_assignments (user_id, store_id) VALUES (?, ?)",
                     (user_id, store_id))
        conn.execute("INSERT INTO auth_login_failures VALUES ('old@example.com', 123)")
        conn.execute("DELETE FROM schema_migrations WHERE version = 15")
        conn.execute("PRAGMA user_version = 14")
        conn.commit()
        conn.close()
        store_ops_db.init_db(self.db_path, backup_before_migration=False)
        conn = store_ops_db.connect(self.db_path)
        try:
            self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 17)
            self.assertEqual(conn.execute("SELECT email FROM auth_users").fetchone()[0],
                             "old@example.com")
            self.assertEqual(conn.execute("SELECT token_hash FROM auth_sessions").fetchone()[0],
                             "session")
            self.assertEqual(conn.execute("SELECT key_hash FROM auth_recovery_keys").fetchone()[0],
                             "recovery")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM auth_login_failures").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT tour_done FROM auth_users").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT page_key FROM auth_page_tours").fetchone()[0],
                             "dashboard")
            self.assertEqual(conn.execute("SELECT name FROM auth_roles WHERE name = 'custom_role'").fetchone()[0],
                             "custom_role")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM auth_user_store_assignments").fetchone()[0], 1)
            self.assertIn("tour_done", {
                row[1] for row in conn.execute("PRAGMA table_info(auth_users)")})
            before = list(conn.iterdump())
        finally:
            conn.close()
        store_ops_db.init_db(self.db_path)
        conn = store_ops_db.connect(self.db_path)
        self.assertEqual(list(conn.iterdump()), before)
        conn.close()


class TestSchemaConsistency(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "consistent.db"
        store_ops_db.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def assert_rejected_without_changes(self, setup):
        conn = sqlite3.connect(self.db_path)
        setup(conn)
        conn.commit()
        conn.close()
        before = self.db_path.read_bytes()
        with self.assertRaisesRegex(store_ops_db.SchemaVersionError, "no changes were made"):
            store_ops_db.init_db(self.db_path)
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertFalse((self.db_path.parent / "backups").exists())

    def test_missing_audit_row_is_rejected(self):
        self.assert_rejected_without_changes(
            lambda conn: conn.execute("DELETE FROM schema_migrations WHERE version = 2"))

    def test_future_audit_row_is_rejected(self):
        self.assert_rejected_without_changes(
            lambda conn: conn.execute("INSERT INTO schema_migrations VALUES (999, datetime('now'))"))

    def test_missing_audit_table_is_rejected(self):
        self.assert_rejected_without_changes(
            lambda conn: conn.execute("DROP TABLE schema_migrations"))

    def test_version_zero_database_initializes_normally(self):
        empty = self.db_path.with_name("empty.db")
        sqlite3.connect(empty).close()
        store_ops_db.init_db(empty)
        conn = sqlite3.connect(empty)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 17)
        conn.close()


class TestSQLiteWALAndLockWaiting(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "wal.db"
        store_ops_db.init_db(self.db_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_wal_timeout_and_foreign_keys_are_active(self):
        conn = store_ops_db.connect(self.db_path)
        self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "wal")
        self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 10000)
        self.assertEqual(conn.execute("PRAGMA foreign_keys").fetchone()[0], 1)
        conn.close()

    def test_competing_writer_waits_for_lock_instead_of_failing_immediately(self):
        import threading
        import time

        writer = store_ops_db.connect(self.db_path)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO stores (code, name) VALUES ('LOCK', 'Waiting')")
        finished = []

        def compete():
            competing = store_ops_db.connect(self.db_path)
            try:
                competing.execute("INSERT INTO stores (code, name) VALUES ('NEXT', 'Next')")
                competing.commit()
                finished.append(time.monotonic())
            finally:
                competing.close()

        started = time.monotonic()
        thread = threading.Thread(target=compete)
        thread.start()
        time.sleep(0.2)
        self.assertTrue(thread.is_alive())
        writer.commit()
        thread.join(timeout=2)
        self.assertFalse(thread.is_alive())
        self.assertGreaterEqual(finished[0] - started, 0.15)
        writer.close()

    def test_reader_can_read_while_writer_transaction_is_open(self):
        writer = store_ops_db.connect(self.db_path)
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("INSERT INTO stores (code, name) VALUES ('WAL', 'Writer')")
        reader = store_ops_db.connect(self.db_path)
        self.assertEqual(reader.execute("SELECT COUNT(*) FROM stores").fetchone()[0], 0)
        writer.commit()
        self.assertEqual(reader.execute("SELECT COUNT(*) FROM stores").fetchone()[0], 1)
        writer.close()
        reader.close()


class TestMigrationAtomicity(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "migration.db"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def database_state(self):
        conn = sqlite3.connect(self.db_path)
        try:
            return list(conn.iterdump())
        finally:
            conn.close()

    def test_failure_at_every_migration_boundary_rolls_back_and_retries(self):
        error = sqlite3.OperationalError("injected migration failure")
        for version in range(1, store_ops_db.SCHEMA_VERSION + 1):
            with self.subTest(version=version):
                original_apply = getattr(store_ops_db, f"_apply_v{version}")

                def fail_late(conn, apply_migration=original_apply):
                    apply_migration(conn)
                    raise error

                if self.db_path.exists():
                    self.db_path.unlink()
                if version > 1:
                    with mock.patch.object(
                        store_ops_db, f"_apply_v{version}", fail_late
                    ):
                        with self.assertRaisesRegex(
                            sqlite3.OperationalError, "injected migration failure"
                        ):
                            store_ops_db.init_db(self.db_path)
                    before = self.database_state()
                else:
                    before = ["BEGIN TRANSACTION;", "COMMIT;"]

                with mock.patch.object(
                    store_ops_db, f"_apply_v{version}", fail_late
                ):
                    with self.assertRaisesRegex(
                        sqlite3.OperationalError, "injected migration failure"
                    ):
                        store_ops_db.init_db(self.db_path)

                self.assertEqual(self.database_state(), before)
                store_ops_db.init_db(self.db_path)
                conn = store_ops_db.connect(self.db_path)
                try:
                    self.assertEqual(
                        conn.execute("PRAGMA user_version").fetchone()[0],
                        store_ops_db.SCHEMA_VERSION,
                    )
                    self.assertEqual(
                        conn.execute(
                            "SELECT COUNT(*) FROM schema_migrations"
                        ).fetchone()[0],
                        store_ops_db.SCHEMA_VERSION,
                    )
                    self.assertEqual(
                        conn.execute("PRAGMA integrity_check").fetchone()[0], "ok"
                    )
                finally:
                    conn.close()


class TestMigrationSafety(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "existing.db"
        conn = store_ops_db.connect(self.db_path)
        store_ops_db._apply_v1(conn)
        conn.execute("CREATE TABLE marker (value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('original')")
        conn.execute("PRAGMA user_version = 1")
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_existing_database_is_backed_up_before_migration(self):
        store_ops_db.init_db(self.db_path)
        copies = list((self.db_path.parent / "backups").glob(
            "pre-migration-v1-to-v17-*.db"))
        self.assertEqual(len(copies), 1)
        conn = sqlite3.connect(copies[0])
        self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0],
                         "original")
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 1)
        conn.close()

    def test_current_and_new_databases_do_not_create_migration_backups(self):
        current = self.db_path.with_name("current.db")
        store_ops_db.init_db(current)
        store_ops_db.init_db(current)
        self.assertFalse((self.db_path.parent / "backups").exists())

    def test_backup_failure_prevents_migration(self):
        from ducklab import backup
        before = self.db_path.read_bytes()
        with mock.patch.object(backup, "save_pre_migration_backup",
                               side_effect=backup.BackupError("disk full")):
            with self.assertRaisesRegex(backup.BackupError, "disk full"):
                store_ops_db.init_db(self.db_path)
        self.assertEqual(self.db_path.read_bytes(), before)

    def test_newer_schema_is_rejected_without_changes(self):
        conn = sqlite3.connect(self.db_path)
        conn.execute(f"PRAGMA user_version = {store_ops_db.SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        before = self.db_path.read_bytes()
        with self.assertRaisesRegex(store_ops_db.SchemaVersionError,
                                    "newer than this app"):
            store_ops_db.init_db(self.db_path)
        self.assertEqual(self.db_path.read_bytes(), before)
        self.assertFalse((self.db_path.parent / "backups").exists())

    def test_version_zero_existing_database_is_backed_up(self):
        empty = self.db_path.with_name("version-zero.db")
        store_ops_db.connect(empty).close()
        store_ops_db.init_db(empty)
        self.assertEqual(len(list((self.db_path.parent / "backups").glob(
            "pre-migration-v0-to-v17-*.db"))), 1)

    def test_backup_can_be_disabled_for_temporary_restore_copy(self):
        store_ops_db.init_db(self.db_path, backup_before_migration=False)
        self.assertFalse((self.db_path.parent / "backups").exists())


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
