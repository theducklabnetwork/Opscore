"""Tests for workbook DB selection, blank-vs-zero, replacement
transactions and idempotency. Disposable temp DBs and synthetic workbooks
only — never the real workbook or dev.db.
Run: python -m pytest tests/test_import_workbook.py -v
"""

import importlib.util
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
from contextlib import closing
from datetime import date
from pathlib import Path

ROOT = Path(__file__).parent.parent
SCRIPT = ROOT / "scripts" / "import_workbook_dev.py"


def make_workbook(path: Path, blank_sales: bool) -> None:
    """Tiny synthetic workbook: one store, one month, two daily rows —
    row 7 with a typed 0 sale, row 8 with a blank sale (blank_sales=True)
    or a 500 sale (blank_sales=False)."""
    import openpyxl
    wb = openpyxl.Workbook()
    setup = wb.active
    setup.title = "Setup"
    setup["B5"] = "TST1"
    setup["B6"] = "Test Store One"
    setup["B11"] = date(2026, 10, 1)
    setup["B16"] = 100000
    setup["B17"], setup["B18"], setup["B19"] = 0.1, 0.2, 0.3
    setup["B47"] = 0.5
    setup["A33"] = "Electricity"
    setup["B33"] = 0.025   # budget % of the active sales target
    daily = wb.create_sheet("Daily")
    daily["A7"] = date(2026, 10, 1)
    daily["C7"] = 0          # typed zero -> stored 0
    daily["D7"] = 0
    daily["A8"] = date(2026, 10, 2)
    if not blank_sales:
        daily["C8"] = 500    # blank_sales=True -> cell left blank -> NULL
    wb.create_sheet("Summary")
    register = wb.create_sheet("Expense Register")
    register["A9"] = date(2026, 10, 1)
    register["C9"] = "Electricity"
    register["D9"] = "Test bill"
    register["E9"] = 10
    register["G9"] = "Pending"
    wb.save(path)


class TestImportScript(unittest.TestCase):
    def run_script(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, cwd=ROOT)

    def test_db_path_from_environment_and_missing_file(self):
        # Load the release copy afresh even if another test imported the
        # development checkout's module under the same name.
        spec = importlib.util.spec_from_file_location(
            "import_workbook_dev_isolated", SCRIPT)
        imp = importlib.util.module_from_spec(spec)
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "custom.db"
            with mock.patch.dict(os.environ, {"DUCKLAB_DB_PATH": str(db)}):
                spec.loader.exec_module(imp)
                self.assertEqual(imp.DEV_DB, db)
            with self.assertRaises(SystemExit) as ctx:
                imp.do_import(db, Path(tmp) / "missing.xlsx", replace=False)
            self.assertIn(f"Database not found: {db}", str(ctx.exception))
            self.assertIn("DUCKLAB_DB_PATH", str(ctx.exception))
            with mock.patch.dict(os.environ, {}, clear=True):
                spec.loader.exec_module(imp)
                self.assertEqual(imp.DEV_DB, imp.ROOT / ".devdata" / "dev.db")

    def test_replace_updates_daily_record_and_stock(self):
        """Replacement uses daily_record_id and replaces both linked rows."""
        with tempfile.TemporaryDirectory() as tmp:
            wb = Path(tmp) / "w.xlsx"
            make_workbook(wb, blank_sales=False)
            sys.path.insert(0, str(ROOT / "scripts"))
            import import_workbook_dev as imp
            db = Path(tmp) / "disp.db"
            from ducklab.app_service import AppService
            app = AppService(db)
            admin = app.auth.bootstrap_first_admin("a@x.dev", "A", "Sup3rSecure!x")
            first = imp.do_import(db, wb, replace=False)
            sid = first["store_id"]
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                record_id = conn.execute(
                    "SELECT id FROM daily_records WHERE store_id=? LIMIT 1",
                    (sid,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO daily_stock_losses "
                    "(daily_record_id, code, amount_cents) VALUES (?, 'DMO', 70)",
                    (record_id,))
                conn.commit()
            second = imp.do_import(db, wb, replace=True)
            self.assertEqual(second["daily_rows"], 2)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM daily_stock_losses").fetchone()[0], 0)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM daily_records WHERE store_id=?",
                    (sid,)).fetchone()[0], 2)

    def test_failed_replace_rolls_back_wipe_and_import(self):
        """A failure after replacement preserves every original row."""
        with tempfile.TemporaryDirectory() as tmp:
            wb = Path(tmp) / "w.xlsx"
            make_workbook(wb, blank_sales=False)
            sys.path.insert(0, str(ROOT / "scripts"))
            import import_workbook_dev as imp
            db = Path(tmp) / "disp.db"
            from ducklab.app_service import AppService
            app = AppService(db)
            admin = app.auth.bootstrap_first_admin("a@x.dev", "A", "Sup3rSecure!x")
            result = imp.do_import(db, wb, replace=False)
            sid = result["store_id"]
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                record_id = conn.execute(
                    "SELECT id FROM daily_records WHERE store_id=? LIMIT 1",
                    (sid,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO daily_stock_losses "
                    "(daily_record_id, code, amount_cents) VALUES (?, 'DMO', 70)",
                    (record_id,))
                before = conn.execute(
                    "SELECT id, date, sales_cents FROM daily_records "
                    "WHERE store_id=? ORDER BY date", (sid,)).fetchall()
                conn.commit()
            with mock.patch.object(
                    imp, "_expense_exists", side_effect=RuntimeError("late failure")):
                with self.assertRaisesRegex(RuntimeError, "late failure"):
                    imp.do_import(db, wb, replace=True)
            with closing(sqlite3.connect(db)) as conn:
                conn.execute("BEGIN IMMEDIATE")
                self.assertEqual(conn.execute(
                    "SELECT id, date, sales_cents FROM daily_records "
                    "WHERE store_id=? ORDER BY date", (sid,)).fetchall(), before)
                self.assertEqual(conn.execute(
                    "SELECT daily_record_id, code, amount_cents "
                    "FROM daily_stock_losses").fetchall(),
                    [(record_id, "DMO", 70)])

    def test_transaction_restores_all_service_db_paths(self):
        """Services remain usable with their original paths after a failure."""
        with tempfile.TemporaryDirectory() as tmp:
            from ducklab.app_service import AppService
            db = Path(tmp) / "disp.db"
            app = AppService(db)
            services = (app.read_repo, app.write.repo, app.auth,
                        app.snapshots, app.snapshots.repo)
            with self.assertRaisesRegex(RuntimeError, "stop import"):
                with app.transaction():
                    self.assertTrue(all(s._db_path is not db for s in services))
                    raise RuntimeError("stop import")
            self.assertEqual([s._db_path for s in services], [db] * 5)
            admin = app.auth.bootstrap_first_admin(
                "a@x.dev", "A", "Sup3rSecure!x")
            sid = app.register_store(admin, "AFTER", "Usable after rollback")
            self.assertEqual(app.read_repo.get_store(sid).code, "AFTER")

    def test_inner_rollback_prevents_commit_without_exception(self):
        """A swallowed repository error cannot commit earlier writes."""
        with tempfile.TemporaryDirectory() as tmp:
            from ducklab.app_service import AppService
            db = Path(tmp) / "disp.db"
            app = AppService(db)
            admin = app.auth.bootstrap_first_admin(
                "a@x.dev", "A", "Sup3rSecure!x")
            original_create = app.write.repo.create_store

            def fail_after_write(*args, **kwargs):
                store_id = original_create(*args, **kwargs)
                app.write.repo._connect().rollback()
                return store_id

            with mock.patch.object(
                    app.write.repo, "create_store", side_effect=fail_after_write):
                with app.transaction():
                    app.register_store(admin, "UNDO", "Must roll back")
            self.assertEqual([s._db_path for s in
                              (app.read_repo, app.write.repo, app.auth,
                               app.snapshots, app.snapshots.repo)], [db] * 5)
            with self.assertRaises(Exception) as ctx:
                app.read_repo.get_store_by_code("UNDO")
            self.assertIn("Unknown store code", str(ctx.exception))

    def test_transaction_commits_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            from ducklab.app_service import AppService
            db = Path(tmp) / "disp.db"
            app = AppService(db)
            admin = app.auth.bootstrap_first_admin(
                "a@x.dev", "A", "Sup3rSecure!x")
            with app.transaction():
                app.register_store(admin, "COMMIT", "Committed together")
            self.assertEqual(app.read_repo.get_store_by_code("COMMIT").name,
                             "Committed together")

    def test_nested_transaction_is_rejected_without_corrupting_outer_scope(self):
        with tempfile.TemporaryDirectory() as tmp:
            from ducklab.app_service import AppService
            db = Path(tmp) / "disp.db"
            app = AppService(db)
            admin = app.auth.bootstrap_first_admin(
                "a@x.dev", "A", "Sup3rSecure!x")
            with app.transaction() as conn:
                app.register_store(admin, "OUTER", "Outer transaction")
                with self.assertRaisesRegex(RuntimeError, "Nested or overlapping"):
                    with app.transaction():
                        pass
                self.assertIs(app.read_repo._db_path, conn)
            self.assertEqual(app.read_repo._db_path, db)
            self.assertFalse(app._transaction_active)
            self.assertEqual(app.read_repo.get_store_by_code("OUTER").name,
                             "Outer transaction")

    def test_overlapping_transaction_is_rejected_and_original_scope_restored(self):
        with tempfile.TemporaryDirectory() as tmp:
            from ducklab.app_service import AppService
            db = Path(tmp) / "disp.db"
            app = AppService(db)
            outer = app.transaction()
            outer_conn = outer.__enter__()
            overlapping = app.transaction()
            try:
                with self.assertRaisesRegex(RuntimeError, "Nested or overlapping"):
                    overlapping.__enter__()
            finally:
                outer.__exit__(None, None, None)
                overlapping.__exit__(None, None, None)
            self.assertEqual(app.read_repo._db_path, db)
            self.assertFalse(app._transaction_active)

    def test_caught_sql_error_prevents_outer_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            from ducklab.app_service import AppService
            db = Path(tmp) / "disp.db"
            app = AppService(db)
            admin = app.auth.bootstrap_first_admin(
                "a@x.dev", "A", "Sup3rSecure!x")
            with app.transaction():
                app.register_store(admin, "UNDO", "Must roll back")
                try:
                    app.write.repo._connect().execute(
                        "INSERT INTO stores (code, name) VALUES (?, ?)",
                        ("UNDO", "Duplicate"))
                except sqlite3.IntegrityError:
                    pass
            with self.assertRaisesRegex(Exception, "Unknown store code"):
                app.read_repo.get_store_by_code("UNDO")

    def test_blank_vs_zero_import_rule(self):
        """Typed 0 is stored as 0; a blank cell stays NULL."""
        with tempfile.TemporaryDirectory() as tmp:
            wb = Path(tmp) / "w.xlsx"
            make_workbook(wb, blank_sales=True)
            sys.path.insert(0, str(ROOT / "scripts"))
            import import_workbook_dev as imp
            # import into a disposable DB by monkeypatching the allowed path
            db = Path(tmp) / "disp.db"
            from ducklab.app_service import AppService
            app = AppService(db)
            app.auth.bootstrap_first_admin("a@x.dev", "A", "Sup3rSecure!x")
            imp.DEV_DB = db  # redirect guard for this disposable DB
            result = imp.do_import(db, wb, replace=False)
            # row 7 (typed 0) imported; row 8 fully blank -> no record
            # (the app represents an unreported day by the absence of a row)
            self.assertEqual(result["daily_rows"], 1)
            sid = result["store_id"]
            days = app.get_daily_records(
                app.auth.get_user(1), sid, "2026-10")
            by_date = {d.date: d for d in days}
            self.assertEqual(by_date[date(2026, 10, 1)].sales_cents, 0)
            self.assertNotIn(date(2026, 10, 2), by_date)  # blank stayed NULL

    def test_idempotent(self):
        """Second run imports nothing new (daily upsert + expense natural key)."""
        with tempfile.TemporaryDirectory() as tmp:
            wb = Path(tmp) / "w.xlsx"
            make_workbook(wb, blank_sales=False)
            sys.path.insert(0, str(ROOT / "scripts"))
            import import_workbook_dev as imp
            db = Path(tmp) / "disp.db"
            from ducklab.app_service import AppService
            app = AppService(db)
            app.auth.bootstrap_first_admin("a@x.dev", "A", "Sup3rSecure!x")
            imp.DEV_DB = db
            first = imp.do_import(db, wb, replace=False)
            second = imp.do_import(db, wb, replace=False)
            self.assertEqual(first["daily_rows"], 2)
            self.assertEqual(second["daily_rows"], 2)      # upsert, no dup
            self.assertEqual(second["expense_rows"], 0)    # nothing new
            sid = first["store_id"]
            days = app.get_daily_records(
                app.auth.get_user(1), sid, "2026-10")
            self.assertEqual(len(days), 2)                 # no duplicates
            self.assertEqual(len({d.date for d in days}), 2)


if __name__ == "__main__":
    unittest.main()
