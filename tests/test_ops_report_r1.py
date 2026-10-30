"""R1 tests: Monthly Report (/ops/report) wording (presentation only).
Title line "<name> (<code>) · <month>", one money format, plain Title
Case labels, no section-header chips, no sub-lines under values, and
"Projected Sales" only while the month is incomplete. Payroll gating and
Live/Finalized behaviour are covered by the existing tests.

Run: python -m pytest tests/test_ops_report_r1.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab.app_service import AppService
from ducklab.ops_state import _serialize_live, _serialize_snapshot


def rm(v):
    return int(Decimal(str(v)) * 100)


class SerializerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "r1.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "S01", "Store A")
        # Level 1 = 120,000.30; 30 days -> 120,000.00.
        self.app.configure_month(
            self.admin, self.sid, "2026-09", level1_base_cents=12000030,
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        for d in range(1, 31):
            self.app.record_daily(self.admin, self.sid, date(2026, 9, d),
                                  sales_cents=rm(4000))

    def tearDown(self):
        self._tmp.cleanup()

    def test_live_title_money_and_achievement(self):
        out = _serialize_live(self.app.preview_month(
            self.admin, self.sid, "2026-09"), user=self.admin)
        self.assertEqual(out["store_title"], "Store A (S01)")
        self.assertEqual(out["month"], "September 2026")
        s = out["sales"]
        self.assertEqual(s["mtd"], "RM 120,000.00")
        self.assertEqual(s["projected"], "RM 120,000.00")   # full format
        self.assertEqual(s["target_l1"], "RM 120,000.30")
        # 99.99991%: below target -> never shown as 100.0%
        self.assertEqual(s["achievement_l1"], "99.9%")

    def test_finalized_title_and_money(self):
        self.app.finalize_month(self.admin, self.sid, "2026-09")
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-09")
        out = _serialize_snapshot(snap, user=self.admin)
        self.assertEqual(out["store_title"], "Store A (S01)")
        self.assertEqual(out["sales"]["projected"], "RM 120,000.00")
        self.assertEqual(out["sales"]["achievement_l1"], "99.9%")


class HeadingTests(unittest.TestCase):
    def test_title_line(self):
        from ducklab.ops_state import OpsState
        st = OpsState()
        st.report = {"store": {"label": "S01 — Store A", "title": "Store A (S01)"},
                     "month": {"label": "September 2026"}}
        self.assertEqual(st.report_heading, "Store A (S01) · September 2026")


class PageWordingTests(unittest.TestCase):
    def test_report_upt_uses_report_upt_value(self):
        from ducklab import ops_pages as p
        section = str(p._sales_section())
        self.assertIn('"UPT"', section)
        self.assertIn('.sales_rx_state_?.["upt"]', section)
        self.assertNotIn('_stat("IPR", sales["upt"]', section)

    def test_labels(self):
        from ducklab.ops_pages import report_page
        page = str(report_page())
        for text in ("Sales", "Level 1 Target", "Achievement", "SSSG/Year",
                     "SSSG/Month", "SPR", "UPT", "ASP", "Projected Sales",
                     "Expenses", "Paid", "Incurred", "Pending", "Budget",
                     "Expense Ratio", "Category", "Status", "Manpower",
                     "Opening Headcount", "Hires", "Resignations",
                     "Closing Headcount", "Effective Staff", "Salary Cost",
                     "Salary Ratio", "Total Salary Ratio", "Turnover",
                     "Leave Days", "Stock", "Total Loss", "Loss Ratio",
                     "Code", "Total", "Treatment", "Actions", "Open",
                     "In Progress", "Blocked", "Completed", "Overdue",
                     "Live", "Finalized"):
            self.assertIn(f'"{text}"', page, text)
        for gone in ("LIVE —", "FINALIZED —", "Sales performance",
                     "Expenses management", "Manpower efficiency",
                     "Stock management", "Action tracker summary",
                     "Monthly sales (MTD)", "Paid (MTD)", "Pending / open",
                     "Loss % of sales", "Sales per receipt",
                     "Units per transaction", "Average selling price",
                     '["pt_factor_label"]', '["turnover_status"]',
                     '["completeness"]', '["hq_status"]',
                     '["ratio_status"]', "Month to date: the ratio",
                     "The month's figures"):
            self.assertNotIn(gone, page, gone)


if __name__ == "__main__":
    unittest.main()
