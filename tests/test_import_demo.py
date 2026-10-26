"""Demo CSV import: blanks stay NULL and reruns add no rows."""

import csv
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import import_demo


class TestImportDemo(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "demo.db"
        self.daily_csv = self.tmp / "daily.csv"
        self.setup_csv = self.tmp / "setup.csv"
        self._write_csv(self.daily_csv, import_demo.DAILY_HEADERS, [
            ["AB01", "2026-10-01", "1234.50", "25", "70"],
            ["AB01", "2026-10-02", "", "", ""],
        ])
        self._write_csv(self.setup_csv, import_demo.SETUP_HEADERS, [
            ["AB01", "DEMO", "Outlet", "WP", "2026-10", "100000",
             "10", "20", "30", "120000", "2026-10-05;2026-10-20"],
        ])
        from ducklab.app_service import AppService
        self.app = AppService(self.db)
        self.account = self.app.auth.bootstrap_first_admin(
            "admin@example.test", "Admin", "Sup3rSecure!x")

    def tearDown(self):
        self._tmp.cleanup()

    @staticmethod
    def _write_csv(path, headers, rows):
        with path.open("w", newline="", encoding="utf-8") as stream:
            writer = csv.writer(stream)
            writer.writerow(headers)
            writer.writerows(rows)

    def _run(self):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "import_demo.py"),
             "--db", str(self.db), str(self.daily_csv), str(self.setup_csv)],
            capture_output=True, text=True, cwd=self.tmp)

    def _counts(self):
        tables = ("stores", "auth_user_store_assignments", "monthly_configs",
                  "public_holidays", "daily_records")
        with sqlite3.connect(self.db) as conn:
            return {table: conn.execute(
                f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in tables}

    def test_blank_daily_values_stay_null(self):
        result = import_demo.import_demo(self.db, self.daily_csv, self.setup_csv)
        store_id = self.app.read_repo.get_store_by_code("AB01").id
        store = self.app.get_store(self.account, store_id)
        self.assertEqual((store.code, store.name, store.concept, store.state),
                         ("AB01", "DEMO", "Outlet", "WP"))
        self.assertIn(store_id, self.app.auth.get_user(self.account.id).store_ids)
        config = self.app.get_month_config(self.account, store_id, "2026-10")
        self.assertEqual(config.level1_base_cents, 10_000_000)
        self.assertEqual([config.level2_pct, config.level3_pct, config.level4_pct],
                         [Decimal("0.1"), Decimal("0.2"), Decimal("0.3")])
        self.assertEqual(config.min_rtm_target_cents, 12_000_000)
        self.assertEqual(self.app.get_public_holidays(
            self.account, store_id, "2026-10"),
            [date(2026, 10, 5), date(2026, 10, 20)])
        days = {day.date: day for day in self.app.get_daily_records(
            self.account, store_id, "2026-10")}
        self.assertEqual((days[date(2026, 10, 1)].sales_cents,
                          days[date(2026, 10, 1)].transactions,
                          days[date(2026, 10, 1)].units), (123450, 25, 70))
        self.assertEqual((days[date(2026, 10, 2)].sales_cents,
                          days[date(2026, 10, 2)].transactions,
                          days[date(2026, 10, 2)].units), (None, None, None))

    def test_running_twice_adds_nothing(self):
        first = self._run()
        self.assertEqual(first.returncode, 0, first.stderr)
        before = self._counts()
        second = self._run()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self._counts(), before)
        self.assertEqual(before["daily_records"], 2)
        self.assertIn("monthly_configs: 1", second.stdout)


if __name__ == "__main__":
    unittest.main()
