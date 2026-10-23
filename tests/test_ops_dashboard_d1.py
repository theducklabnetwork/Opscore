"""D1 tests: Dashboard (/ops) wording and layout (presentation only).
Plain one/two-word labels, full tile values, no chips/context lines on
the tiles, "Daily Target" / "Required Daily Sales" hidden once the month
is complete, "Alerts" and "Summary". Disposable temp DBs only.

Run: python -m pytest tests/test_ops_dashboard_d1.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService

TODAY = date(2026, 10, 21)


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "d1.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        for month in ("2026-09", "2026-10"):
            self.app.configure_month(
                self.admin, self.sid, month, level1_base_cents=rm(250000),
                level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)

    def tearDown(self):
        self._tmp.cleanup()

    def overview(self, month):
        import ducklab.ops_overview_state as ov_mod
        from ducklab.ops_overview_state import compute_overview
        with mock.patch.object(ov_mod, "app_service", self.app):
            return compute_overview(self.admin, self.sid, month, TODAY)


class DashboardDataTests(Fixture):
    def test_incomplete_month(self):
        for d in range(1, 11):  # 10 x 9,000 = 90,000
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(9000))
        ov = self.overview("2026-10")
        self.assertEqual(ov["pill_sales"]["value"], "RM 90,000")  # full
        self.assertEqual(ov["targets_title"], "Targets")
        # 250,000 / 31 = 8,064.52; (250,000 - 90,000) / 21 = 7,619.05
        self.assertEqual([(x["label"], x["value"])
                          for x in ov["targets_extra"]],
                         [("Daily Target", "RM 8,065"),
                          ("Required Daily Sales", "RM 7,619")])
        self.assertEqual([g["label"] for g in ov["glance"]],
                         ["Daily Sales (Avg.)", "Open Actions"])
        # alert text without dash or sentence
        self.assertIn({"icon": "calendar-clock", "text": "21 unreported days",
                       "href": "/ops/daily"}, ov["attention"])

    def test_complete_month_hides_daily_rows(self):
        for d in range(1, 31):  # 30 x 9,000 = 270,000
            self.app.record_daily(self.admin, self.sid, date(2026, 9, d),
                                  sales_cents=rm(9000))
        ov = self.overview("2026-09")
        self.assertEqual(ov["pill_sales"]["value"], "RM 270,000")
        self.assertEqual(ov["targets_title"], "Targets")
        self.assertEqual(ov["targets_extra"], [])

    def test_summary_expense_rows(self):
        cat = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                            sort_order=1)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(9000))
        for status, paid in (("Paid", date(2026, 10, 3)), ("Pending", None)):
            self.app.record_expense(
                self.admin, self.sid, incurred_date=date(2026, 10, 2),
                paid_date=paid, category_id=cat, description="Bill",
                amount_cents=rm(1234), paid_by="Petty Cash"
                if paid else None, status=status, verification="Verified",
                recorded_by="ABM")
        ov = self.overview("2026-10")
        self.assertEqual(ov["pill_expenses"]["value"], "RM 1,234")
        self.assertEqual([g["label"] for g in ov["glance"]],
                         ["Daily Sales (Avg.)", "Incurred", "Unpaid",
                          "Open Actions"])


class DashboardPageTests(unittest.TestCase):
    def test_wording(self):
        from ducklab.ops_pages import dashboard_page
        page = str(dashboard_page())
        for text in ('"Sales"', '"Expenses"', '"Salary Ratio"',
                     '"Stock Loss"', '"Sales Trend"', '"Alerts"',
                     '"Summary"'):
            self.assertIn(text, page)
        for gone in ("Sales MTD", "Expenses paid MTD", "salary ratio",
                     "% of sales", "Needs attention", "At a glance",
                     "The month at a glance", "Pace vs Level Targets",
                     "Open this module to enter data"):
            self.assertNotIn(gone, page)


if __name__ == "__main__":
    unittest.main()
