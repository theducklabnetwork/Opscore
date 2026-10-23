"""Tests for Sales Performance: workbook rules for projection, targets,
growth, basket metrics, run-rates, and RBAC. Disposable temp DBs only.
Run: python -m pytest tests/test_ops_sales.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError


def rm(v):
    return int(Decimal(str(v)) * 100)


class SalesFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "sales.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.sb = self.app.register_store(self.admin, "B02", "Beta")
        self.app.configure_month(
            self.admin, self.sa, "2026-10",
            min_rtm_target_cents=rm(180000), level1_base_cents=rm(200000),
            level2_pct=0.05, level3_pct=0.10, level4_pct=0.15,
            sales_last_year_cents=rm(190000),
            sales_last_month_cents=rm(195000), part_timer_factor=0.5)

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores):
        u = self.app.create_user(self.admin, email, "T", "Passw0rd!xyz")
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)

    def preview(self, store=None, month="2026-10"):
        return self.app.preview_month(self.admin, store or self.sa, month,
                                      as_of=date(2026, 10, 31)).sales

    def rec(self, day, month="2026-10", store=None, **kw):
        return self.app.record_daily(self.admin, store or self.sa,
                                     date.fromisoformat(f"{month}-{day:02d}"),
                                     **kw)


class TestSalesRules(SalesFixture):
    def test_days_entered_counts_zero_not_blank(self):
        self.rec(1, sales_cents=rm(100))
        self.rec(2, sales_cents=0)          # explicit zero counts
        self.rec(4, sales_cents=rm(300))    # day 3 left blank
        s = self.preview()
        self.assertEqual(s.days_entered, 3)
        self.assertEqual(s.sales_mtd_cents, rm(400))

    def test_projection_partial_and_actual_complete(self):
        # 15 of 31 days, RM 90,000 -> projected = 90000/15*31 = 186,000
        for d in range(1, 16):
            self.rec(d, sales_cents=rm(6000))
        s = self.preview()
        self.assertEqual(s.month_end_sales_cents, Decimal(rm(186000)))
        # complete month -> actual, no projection
        for d in range(16, 32):
            self.rec(d, sales_cents=rm(6000))
        s2 = self.preview()
        self.assertEqual(s2.days_entered, 31)
        self.assertEqual(s2.month_end_sales_cents, Decimal(rm(186000)))

    def test_level_statuses(self):
        # 31 days of RM 7,000 -> RM 217,000 actual: L1 achieved (200k),
        # L4 = max(180k floor, 230k) = 230k -> below
        for d in range(1, 32):
            self.rec(d, sales_cents=rm(7000))
        s = self.preview()
        self.assertEqual(s.status_by_level["level1"], "Achieved")
        self.assertEqual(s.status_by_level["level4"], "Below target")
        self.assertEqual(s.targets["level4"], Decimal(rm(230000)))
        # no target configured
        self.app.configure_month(self.admin, self.sb, "2026-10")
        sb = self.app.preview_month(self.admin, self.sb, "2026-10",
                                    as_of=date(2026, 10, 31)).sales
        self.assertEqual(sb.status_by_level["level1"], "Awaiting data")
        self.app.record_daily(self.admin, self.sb, date(2026, 10, 1),
                              sales_cents=rm(1000))
        sb2 = self.app.preview_month(self.admin, self.sb, "2026-10",
                                     as_of=date(2026, 10, 31)).sales
        self.assertEqual(sb2.status_by_level["level1"], "No target")

    def test_growth_and_no_reference(self):
        # complete month at RM 200,000 -> vs LY 190k = +5.26%; vs LM +2.56%
        for d in range(1, 32):
            self.rec(d, sales_cents=rm(6451))  # ~199,981 total
        s = self.preview()
        self.assertIsNotNone(s.growth_last_year)
        self.assertGreater(s.growth_last_year, 0)
        # store without references
        self.app.configure_month(self.admin, self.sb, "2026-10",
                                 level1_base_cents=rm(100000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        self.app.record_daily(self.admin, self.sb, date(2026, 10, 1),
                              sales_cents=rm(1000))
        sb = self.app.preview_month(self.admin, self.sb, "2026-10",
                                    as_of=date(2026, 10, 31)).sales
        self.assertIsNone(sb.growth_last_year)
        self.assertIsNone(sb.growth_last_month)

    def test_basket_metrics_blank_on_zero_denominator(self):
        self.rec(1, sales_cents=rm(1000), transactions=0, units=0)
        s = self.preview()
        self.assertIsNone(s.spr)
        self.assertIsNone(s.upt)
        self.assertIsNone(s.asp)
        self.rec(2, sales_cents=rm(1000), transactions=50, units=200)
        s2 = self.preview()
        self.assertEqual(s2.spr, Decimal(rm(2000)) / 50)
        self.assertEqual(s2.upt, Decimal(200) / 50)
        self.assertEqual(s2.asp, Decimal(rm(2000)) / 200)

    def test_needed_per_remaining_day_edges(self):
        # nothing entered -> blank
        s0 = self.preview()
        self.assertIsNone(s0.needed_per_remaining_day)
        # partial: (200000 - 5000*5) / (31-5) per day
        for d in range(1, 6):
            self.rec(d, sales_cents=rm(5000))
        s = self.preview()
        expected = Decimal(rm(200000) - rm(25000)) / 26
        self.assertEqual(s.needed_per_remaining_day, expected)
        # target already exceeded -> 0
        self.rec(6, sales_cents=rm(300000))
        s2 = self.preview()
        self.assertEqual(s2.needed_per_remaining_day, Decimal(0))
        # complete month -> blank
        for d in range(7, 32):
            self.rec(d, sales_cents=rm(1000))
        s3 = self.preview()
        self.assertIsNone(s3.needed_per_remaining_day)

    def test_required_run_rate(self):
        # Level 1 RM 200,000 / 31 days = 645,161.29... cents per day
        s = self.preview()
        self.assertEqual(s.required_daily_run_rate,
                         Decimal("645161.2903225806451612903226"))

    def test_hand_computed_month_figures(self):
        # 3 days x RM 6,000 (100 tx, 300 units each), October = 31 days
        for d in (1, 2, 4):
            self.rec(d, sales_cents=rm(6000), transactions=100, units=300)
        s = self.preview()
        self.assertEqual(s.days_entered, 3)
        self.assertEqual(s.sales_mtd_cents, 1800000)          # RM 18,000
        # projection 18,000 / 3 x 31 = RM 186,000 exactly
        self.assertEqual(s.month_end_sales_cents, Decimal(18600000))
        self.assertEqual(s.spr, Decimal(6000))                # RM 60 / tx
        self.assertEqual(s.upt, Decimal(3))
        self.assertEqual(s.asp, Decimal(2000))                # RM 20 / unit


class TestSalesRbac(SalesFixture):
    def test_multi_store_isolation(self):
        self.rec(1, sales_cents=rm(5000))
        self.app.configure_month(self.admin, self.sb, "2026-10",
                                 level1_base_cents=rm(50000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        sb = self.app.preview_month(self.admin, self.sb, "2026-10",
                                    as_of=date(2026, 10, 31)).sales
        self.assertEqual(sb.days_entered, 0)

    def test_unauthorized_read_rejected(self):
        outsider = self.user("o@x.dev", ["viewer"], [self.sb])
        with self.assertRaises(AuthorizationError):
            self.app.preview_month(outsider, self.sa, "2026-10")

    def test_viewer_cannot_write(self):
        viewer = self.user("v@x.dev", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(viewer, self.sa, date(2026, 10, 1),
                                  sales_cents=rm(100))

    def test_finalized_snapshot_unchanged(self):
        self.rec(1, sales_cents=rm(5000))
        self.app.finalize_month(self.admin, self.sa, "2026-10",
                                as_of=date(2026, 10, 31))
        before = self.app.get_finalized_report(
            self.admin, self.sa, "2026-10")["kpi_json"]
        self.rec(2, sales_cents=rm(9000))  # live edit after finalization
        again = self.app.get_finalized_report(self.admin, self.sa, "2026-10")
        self.assertEqual(again["kpi_json"], before)

    def test_invalid_deep_link_date_rejected(self):
        """DailyState.open_deep_linked_day ignores malformed / wrong-month
        ?date= params without opening the form or raising."""
        from ducklab.ops_daily_state import DailyState
        state = DailyState.__new__(DailyState)
        class _Router:
            class page:
                params = {"date": "2026-13-99"}
        state.__dict__["router"] = _Router()
        # malformed date -> generator returns nothing, no exception
        gen = state.open_deep_linked_day()
        self.assertEqual(list(gen), [])
        # valid date but wrong month -> also ignored
        _Router.page.params = {"date": "2025-01-15"}
        state.__dict__["selected_month_label"] = "October 2026"
        self.assertEqual(list(state.open_deep_linked_day()), [])


if __name__ == "__main__":
    unittest.main()
