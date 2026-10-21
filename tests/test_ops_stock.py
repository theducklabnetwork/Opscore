"""Tests for Stock Management: Total Loss rules, ratio/target, NULL-vs-zero,
category treatment, month comparison, multi-store isolation and RBAC.
Temp DBs only. Run: python -m pytest tests/test_ops_stock.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import kpi
from ducklab.app_service import AppService
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.operations_repository import ConfigNotFoundError


def rm(v):
    return int(Decimal(str(v)) * 100)


TARGET = 0.015  # per-store/month config, never hardcoded in UI


class StockFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "stock.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.sb = self.app.register_store(self.admin, "B02", "Beta")
        for sid in (self.sa, self.sb):
            self.app.configure_month(
                self.admin, sid, "2026-09", level1_base_cents=rm(200000),
                stock_loss_target=TARGET, part_timer_factor=0.5)
            self.app.configure_month(
                self.admin, sid, "2026-10", level1_base_cents=rm(210000),
                stock_loss_target=TARGET, part_timer_factor=0.5)

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores):
        u = self.app.create_user(self.admin, email, "T", "Passw0rd!xyz")
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)

    def stock(self, store=None, month="2026-10"):
        rep = self.app.preview_month(self.admin, store or self.sa, month,
                                     as_of=date(2026, 10, 31))
        return rep.stock

    def rec(self, day, month="2026-10", store=None, sales=rm(10000), **kw):
        y, m = int(month[:4]), int(month[5:7])
        fields = {}
        if sales is not None:
            fields["sales_cents"] = sales
        if kw:
            fields["stock_losses"] = kw
        return self.app.record_daily(self.admin, store or self.sa,
                                     date(y, m, day), **fields)


class TestTotalLossRules(StockFixture):
    def test_total_loss_parity_with_kpi(self):
        self.rec(1, DMO=rm(100), DMC=rm(50), DMP=rm(25),
                 MISSING=rm(75), EXTRA=rm(30))
        losses = {"DMO": rm(100), "DMC": rm(50), "DMP": rm(25),
                  "MISSING": rm(75), "EXTRA": rm(30)}
        signs = self.app.read_repo.get_contribution_signs()
        expected = kpi.total_loss_cents(losses, signs)
        self.assertEqual(expected, rm(100) + rm(50) + rm(25) + rm(75) - rm(30))
        self.assertEqual(self.stock().total_loss_cents, expected)

    def test_info_only_codes_excluded(self):
        # 2DMP plus every other informational code must not affect Total Loss
        info = {"2DMP": rm(10), "DPI": rm(10), "DMW": rm(10), "DDR": rm(10),
                "DMN": rm(10), "DME": rm(10), "DMQ": rm(10), "DST": rm(10)}
        self.rec(1, DMO=rm(100), **info)
        self.assertEqual(self.stock().total_loss_cents, rm(100))
        totals = self.stock().totals_by_code
        for code in info:
            self.assertEqual(totals[code], rm(10))  # still tracked monthly

    def test_extra_subtracts_and_missing_adds(self):
        self.rec(1, EXTRA=rm(200))     # net negative
        self.assertEqual(self.stock().total_loss_cents, -rm(200))
        self.rec(2, MISSING=rm(150))
        self.assertEqual(self.stock().total_loss_cents, -rm(200) + rm(150))

    def test_blank_vs_explicit_zero(self):
        self.rec(1, DMO=0)             # explicit zero: reported, contributes 0
        st = self.stock()
        self.assertEqual(st.total_loss_cents, 0)
        self.assertEqual(st.totals_by_code.get("DMO"), 0)
        # day 2 never reported: no row, no fabricated values
        days = self.app.get_daily_records(self.admin, self.sa, "2026-10")
        self.assertEqual(len(days), 1)

    def test_zero_sales_ratio_none(self):
        self.rec(1, sales=0, DMO=rm(50))
        st = self.stock()
        self.assertIsNone(st.loss_pct_of_sales)
        self.assertEqual(st.total_loss_cents, rm(50))

    def test_ratio_calculation(self):
        self.rec(1, sales=rm(10000), DMO=rm(150))
        st = self.stock()
        self.assertEqual(st.loss_pct_of_sales, Decimal(rm(150)) / rm(10000))
        self.assertEqual(st.status, "Within target")  # 1.5% <= target

    def test_over_target_status(self):
        self.rec(1, sales=rm(10000), DMO=rm(200))  # 2% > 1.5%
        self.assertEqual(self.stock().status, "Over target")

    def test_no_target_status(self):
        self.app.configure_month(self.admin, self.sb, "2026-10",
                                 stock_loss_target=None)
        self.rec(1, store=self.sb, DMO=rm(500))
        rep = self.app.preview_month(self.admin, self.sb, "2026-10",
                                     as_of=date(2026, 10, 31))
        self.assertEqual(rep.stock.status, "")  # workbook: blank, no target


class TestMonthAndStores(StockFixture):
    def test_month_boundary_exclusion(self):
        self.rec(30, month="2026-09", DMO=rm(111))
        self.rec(1, DMO=rm(222))
        self.assertEqual(self.stock(month="2026-10").total_loss_cents, rm(222))
        self.assertEqual(self.stock(month="2026-09").total_loss_cents, rm(111))

    def test_multi_store_isolation(self):
        self.rec(1, DMO=rm(100))
        self.rec(1, store=self.sb, DMO=rm(999))
        self.assertEqual(self.stock().total_loss_cents, rm(100))
        self.assertEqual(self.stock(store=self.sb).total_loss_cents, rm(999))

    def test_previous_month_comparison_inputs(self):
        self.rec(15, month="2026-09", DMO=rm(90), EXTRA=rm(10))
        self.rec(15, DMO=rm(50), MISSING=rm(30))
        cur = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        prev = self.app.preview_month(self.admin, self.sa, "2026-09",
                                      as_of=date(2026, 9, 30))
        self.assertEqual(prev.stock.total_loss_cents, rm(80))
        self.assertEqual(cur.stock.total_loss_cents, rm(80))
        # partial months: entered days < days in month
        self.assertLess(prev.sales.days_entered, prev.sales.days_in_month)
        self.assertLess(cur.sales.days_entered, cur.sales.days_in_month)

    def test_negative_net_adjustment(self):
        self.rec(1, EXTRA=rm(500), DMO=rm(100))
        st = self.stock()
        self.assertEqual(st.total_loss_cents, -rm(400))  # negative kept
        self.assertEqual(st.loss_pct_of_sales,
                         Decimal(-rm(400)) / rm(10000))


class TestRbac(StockFixture):
    def test_unauthorized_store_read_rejected(self):
        v = self.user("v@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.get_daily_records(v, self.sb, "2026-10")
        with self.assertRaises(AuthorizationError):
            self.app.preview_month(v, self.sb, "2026-10")

    def test_viewer_write_rejected(self):
        v = self.user("v2@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(v, self.sa, date(2026, 10, 1),
                                  sales_cents=rm(1), stock_losses={"DMO": rm(1)})

    def test_invalid_deep_link_date_rejected(self):
        # the deep-link validator rejects non-ISO / out-of-month dates
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        st.router.page.params["date"] = "not-a-date"
        st.open_deep_linked_day()  # must not raise, must not open
        self.assertFalse(st.form_open)
        st.router.page.params["date"] = "2026-11-05"  # wrong month (no selection)
        st.open_deep_linked_day()
        self.assertFalse(st.form_open)


class TestSnapshots(StockFixture):
    def test_finalized_snapshot_unchanged_after_live_edit(self):
        self.rec(1, DMO=rm(100))
        self.app.finalize_month(self.admin, self.sa, "2026-10",
                                as_of=date(2026, 10, 31))
        before = self.app.get_finalized_report(
            self.admin, self.sa, "2026-10")["kpi_json"]
        # live edit after finalization
        self.rec(1, DMO=rm(900))
        again = self.app.get_finalized_report(self.admin, self.sa, "2026-10")
        self.assertEqual(again["kpi_json"], before)  # snapshot immutable
        # live report reflects the edit
        self.assertEqual(self.stock().total_loss_cents, rm(900))


if __name__ == "__main__":
    unittest.main()
