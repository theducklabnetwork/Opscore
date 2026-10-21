"""Tests for scripts/import_workbook_dev.py: dev-DB path refusal,
blank-vs-zero import rule, and idempotency. Disposable temp DBs and
synthetic workbooks only — never the real workbook or dev.db.
Run: python -m pytest tests/test_import_workbook.py -v
"""

import subprocess
import sys
import tempfile
import unittest
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
    wb.create_sheet("Expense Register")
    wb.save(path)


class TestImportScript(unittest.TestCase):
    def run_script(self, *args) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            capture_output=True, text=True, cwd=ROOT)

    def test_refuses_non_dev_db_path(self):
        # the script's public entry only writes to dev.db; exercise the
        # refusal by calling do_import directly with a foreign path
        sys.path.insert(0, str(ROOT / "scripts"))
        from import_workbook_dev import do_import
        with tempfile.TemporaryDirectory() as tmp:
            wb = Path(tmp) / "w.xlsx"
            make_workbook(wb, blank_sales=True)
            with self.assertRaises(SystemExit) as ctx:
                do_import(Path(tmp) / "other.db", wb, replace=False)
            self.assertIn("Refusing DB path", str(ctx.exception))

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
