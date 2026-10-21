"""Task 15 tests: overview-only compact money formatter, the per-pillar
"not reported yet" decision, and the "Projected:" status prefix rule.
Disposable temp DBs only.

Run: python -m pytest tests/test_ops_overview_visual.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import ops_charts
from ducklab.app_service import AppService
from ducklab.ops_overview_state import compute_overview

TODAY = date(2026, 10, 21)


def rm(v):
    return int(Decimal(str(v)) * 100)


class CompactMoneyTests(unittest.TestCase):
    def test_compact_money(self):
        f = ops_charts.fmt_money_compact
        self.assertEqual(f(19130000), "RM 191.3k")      # 191,300 -> 191.3k
        self.assertEqual(f(66000), "RM 660.00")         # 660 exact (2dp)
        self.assertEqual(f(-19130000), "-RM 191.3k")    # negatives
        self.assertEqual(f(-500), "-RM 5.00")
        self.assertEqual(f(None), "—")
        self.assertEqual(f(123456789), "RM 1.2M")
        self.assertEqual(f(9999900), "RM 100.0k")   # 99,999 RM
        self.assertEqual(f(123456), "RM 1,234.56")

    def test_pct1(self):
        self.assertEqual(ops_charts.fmt_pct1(Decimal("0.91095")), "91.1%")
        self.assertEqual(ops_charts.fmt_pct1(None), "—")


class VisualFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "v.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sa, "2026-10", level1_base_cents=rm(200000),
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            turnover_threshold=0.05,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
        self.app.configure_month(
            self.admin, self.sa, "2026-09", level1_base_cents=rm(200000),
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            turnover_threshold=0.05,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))

    def tearDown(self):
        self._tmp.cleanup()

    def overview(self, month="2026-10"):
        import ducklab.ops_overview_state as mod
        from ducklab import auth_pages
        orig_app, orig_mod = auth_pages.app_service, mod.app_service
        auth_pages.app_service = mod.app_service = self.app
        try:
            return compute_overview(self.admin, self.sa, month, TODAY)
        finally:
            auth_pages.app_service = orig_app
            mod.app_service = orig_mod

    # 2 ------------------------------------------------------------------
    def test_not_reported_decision_per_pillar(self):
        # nothing entered at all -> all pillars "not reported" (except
        # manpower, which reports from config+payroll: ratio is None here
        # since sales are 0 -> salary_ratio_incl_support None)
        ov = self.overview()
        self.assertEqual(ov["pill_sales"]["reported"], "")
        self.assertEqual(ov["pill_expenses"]["reported"], "")
        self.assertEqual(ov["pill_stock"]["reported"], "")
        # enter one day of sales -> sales + stock reported, expenses not
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              sales_cents=rm(5000),
                              stock_losses={"DMO": rm(50)})
        ov = self.overview()
        self.assertEqual(ov["pill_sales"]["reported"], "1")
        self.assertEqual(ov["pill_stock"]["reported"], "1")
        self.assertEqual(ov["pill_expenses"]["reported"], "")

    # 3 ------------------------------------------------------------------
    def test_projected_prefix_only_when_incomplete(self):
        for d in range(1, 21):  # partial October
            self.app.record_daily(self.admin, self.sa, date(2026, 10, d),
                                  sales_cents=rm(9565))
        ov = self.overview()
        self.assertTrue(ov["pill_sales"]["status"].startswith("Projected:"))
        self.assertEqual(ov["in_progress"], "1")
        # complete September (evaluated as of October) -> no prefix
        for d in range(1, 31):
            self.app.record_daily(self.admin, self.sa, date(2026, 9, d),
                                  sales_cents=rm(9000))
        ov = self.overview(month="2026-09")
        self.assertFalse(
            ov["pill_sales"]["status"].startswith("Projected:"))
        self.assertEqual(ov["in_progress"], "")


if __name__ == "__main__":
    unittest.main()
