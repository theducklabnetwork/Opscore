"""Tests for workbook DB selection, blank-vs-zero, replacement
transactions and idempotency. Disposable temp DBs and synthetic workbooks
only — never the real workbook or dev.db.
Run: python -m pytest tests/test_import_workbook.py -v
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
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
    setup["B33"] = 1000
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
        sys.path.insert(0, str(ROOT / "scripts"))
        import importlib
        import import_workbook_dev as imp
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "custom.db"
            with mock.patch.dict(os.environ, {"DUCKLAB_DB_PATH": str(db)}):
                importlib.reload(imp)
                self.assertEqual(imp.DEV_DB, db)
            with self.assertRaises(SystemExit) as ctx:
                imp.do_import(db, Path(tmp) / "missing.xlsx", replace=False)
            self.assertIn(f"Database not found: {db}", str(ctx.exception))
            self.assertIn("DUCKLAB_DB_PATH", str(ctx.exception))
            with mock.patch.dict(os.environ, {}, clear=True):
                importlib.reload(imp)
                self.assertEqual(imp.DEV_DB, ROOT / ".devdata" / "dev.db")

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
            with sqlite3.connect(db) as conn:
                record_id = conn.execute(
                    "SELECT id FROM daily_records WHERE store_id=? LIMIT 1",
                    (sid,)).fetchone()[0]
                conn.execute(
                    "INSERT INTO daily_stock_losses "
                    "(daily_record_id, code, amount_cents) VALUES (?, 'DMO', 70)",
                    (record_id,))
            second = imp.do_import(db, wb, replace=True)
            self.assertEqual(second["daily_rows"], 2)
            with sqlite3.connect(db) as conn:
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
            with sqlite3.connect(db) as conn:
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
            with mock.patch.object(
                    imp, "_expense_exists", side_effect=RuntimeError("late failure")):
                with self.assertRaisesRegex(RuntimeError, "late failure"):
                    imp.do_import(db, wb, replace=True)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute(
                    "SELECT id, date, sales_cents FROM daily_records "
                    "WHERE store_id=? ORDER BY date", (sid,)).fetchall(), before)
                self.assertEqual(conn.execute(
                    "SELECT daily_record_id, code, amount_cents "
                    "FROM daily_stock_losses").fetchall(),
                    [(record_id, "DMO", 70)])

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
