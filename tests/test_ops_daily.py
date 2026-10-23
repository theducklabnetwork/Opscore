"""Tests for Daily Operations: parsing helpers + authorized read/write paths.

Temp DBs only. Run: python -m unittest tests.test_ops_daily -v
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from ducklab import kpi
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.ops_daily_state import _parse_count, _parse_money
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.operations_write_repository import ValidationError


def rm(v):
    from decimal import Decimal
    return int(Decimal(str(v)) * 100)


class DailyFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "daily.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.sb = self.app.register_store(self.admin, "B02", "Beta")
        for sid in (self.sa, self.sb):
            self.app.configure_month(
                self.admin, sid, "2026-09", level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                opening_headcount_ft=9, stock_loss_target=0.015,
                salary_ratio_target=0.06818, part_timer_factor=0.5,
                salary_basic_allowance_cents=rm(10000),
                overtime_cents=rm(1000))

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores):
        u = self.app.create_user(self.admin, email, "T", "Passw0rd!xyz")
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)


class TestParsing(unittest.TestCase):
    def test_money_exact(self):
        self.assertEqual(_parse_money("350819"), rm(350819))
        self.assertEqual(_parse_money("9,681.00"), rm(9681))
        self.assertEqual(_parse_money("RM 12.34"), rm(12.34))

    def test_money_rejects_sub_cent(self):
        with self.assertRaises(ValueError):
            _parse_money("1.005")

    def test_money_rejects_garbage(self):
        for bad in ("abc", "", "1,2,3.x"):
            with self.assertRaises(ValueError):
                _parse_money(bad)

    def test_count(self):
        self.assertEqual(_parse_count("7", "x"), 7)
        self.assertEqual(_parse_count("0", "x"), 0)
        with self.assertRaises(ValueError):
            _parse_count("-1", "x")
        with self.assertRaises(ValueError):
            _parse_count("1.5", "x")


class TestDailyWrites(DailyFixture):
    def test_create_then_read_after_write(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(9681), transactions=321,
                              units=1298, ft_working=7, pt_working=1,
                              stock_losses={"DMO": rm(70), "EXTRA": rm(5)})
        days = self.app.get_daily_records(self.admin, self.sa, "2026-09")
        self.assertEqual(len(days), 1)
        d = days[0]
        self.assertEqual(d.sales_cents, rm(9681))
        self.assertEqual(d.stock_losses, {"DMO": rm(70), "EXTRA": rm(5)})
        # KPI derivation from persisted values
        self.assertEqual(int(kpi.spr_cents(d.sales_cents, d.transactions)),
                         rm(30.15))
        signs = self.app.read_repo.get_contribution_signs()
        self.assertEqual(kpi.total_loss_cents(d.stock_losses, signs), rm(65))

    def test_partial_update_preserves_other_fields(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(100), ft_working=5,
                              stock_losses={"DMO": rm(10)})
        # edit staffing only: sales + stock must survive
        self.app.correct_daily(self.admin, self.sa, date(2026, 9, 1),
                               pt_working=2)
        d = self.app.get_daily_records(self.admin, self.sa, "2026-09")[0]
        self.assertEqual(d.sales_cents, rm(100))
        self.assertEqual(d.ft_working, 5)
        self.assertEqual(d.pt_working, 2)
        self.assertEqual(d.stock_losses, {"DMO": rm(10)})

    def test_explicit_zero_vs_missing(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=0)
        d = self.app.get_daily_records(self.admin, self.sa, "2026-09")[0]
        self.assertEqual(d.sales_cents, 0)            # reported zero
        self.assertIsNone(d.transactions)             # still unreported

    def test_explicit_clear(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(50))
        self.app.correct_daily(self.admin, self.sa, date(2026, 9, 1),
                               sales_cents=None)
        d = self.app.get_daily_records(self.admin, self.sa, "2026-09")[0]
        self.assertIsNone(d.sales_cents)

    def test_atomic_combined_save_rolls_back(self):
        with self.assertRaises(ValidationError):
            self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                                  sales_cents=rm(100),
                                  stock_losses={"BOGUS": rm(1)})
        # nothing persisted: no daily row at all
        self.assertEqual(
            self.app.get_daily_records(self.admin, self.sa, "2026-09"), [])

    def test_negative_count_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                                  ft_working=-1)

    def test_unknown_daily_field_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                                  bogus=1)

    def test_stock_loss_update_keeps_sales(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(100),
                              stock_losses={"DMO": rm(10)})
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              stock_losses={"DMO": rm(25), "MISSING": rm(5)})
        d = self.app.get_daily_records(self.admin, self.sa, "2026-09")[0]
        self.assertEqual(d.sales_cents, rm(100))
        self.assertEqual(d.stock_losses["DMO"], rm(25))
        self.assertEqual(d.stock_losses["MISSING"], rm(5))

    def test_month_isolation(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(100))
        self.assertEqual(
            self.app.get_daily_records(self.admin, self.sa, "2026-08"), [])

    def test_kpi_recalculation_after_write(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(100))
        r1 = self.app.preview_month(self.admin, self.sa, "2026-09",
                                    as_of=date(2026, 9, 30))
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 2),
                              sales_cents=rm(50))
        r2 = self.app.preview_month(self.admin, self.sa, "2026-09",
                                    as_of=date(2026, 9, 30))
        self.assertEqual(r1.sales.sales_mtd_cents, rm(100))
        self.assertEqual(r2.sales.sales_mtd_cents, rm(150))


class TestDailySecurity(DailyFixture):
    def test_viewer_cannot_write(self):
        v = self.user("v@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(v, self.sa, date(2026, 9, 1),
                                  sales_cents=rm(1))
        # but can read
        days = self.app.get_daily_records(v, self.sa, "2026-09")
        self.assertEqual(days, [])

    def test_cross_store_write_rejected(self):
        bm = self.user("bm@ducklab.com", ["branch_manager"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(bm, self.sb, date(2026, 9, 1),
                                  sales_cents=rm(1))

    def test_cross_store_read_rejected(self):
        v = self.user("v@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.get_daily_records(v, self.sb, "2026-09")

    def test_disabled_user_cannot_write(self):
        bm = self.user("bm@ducklab.com", ["branch_manager"], [self.sa])
        self.app.set_user_active(self.admin, bm.id, False)
        bm = self.app.auth.get_user(bm.id)
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(bm, self.sa, date(2026, 9, 1),
                                  sales_cents=rm(1))

    def test_expired_session_rejected(self):
        from ducklab.auth_service import AuthenticationError
        with self.assertRaises(AuthenticationError):
            self.app.authenticate("not-a-real-token")

    def test_branch_manager_can_write(self):
        bm = self.user("bm@ducklab.com", ["branch_manager"], [self.sa])
        self.app.record_daily(bm, self.sa, date(2026, 9, 1),
                              sales_cents=rm(250), stock_losses={"DMO": rm(5)})
        days = self.app.get_daily_records(bm, self.sa, "2026-09")
        self.assertEqual(days[0].sales_cents, rm(250))

    def test_finalized_month_still_editable_live(self):
        self.app.finalize_month(self.admin, self.sa, "2026-09",
                                as_of=date(2026, 9, 30))
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 3),
                              sales_cents=rm(999))
        live = self.app.preview_month(self.admin, self.sa, "2026-09",
                                      as_of=date(2026, 9, 30))
        snap = self.app.get_finalized_report(self.admin, self.sa, "2026-09")
        self.assertEqual(live.sales.sales_mtd_cents, rm(999))
        # snapshot payload untouched
        self.assertEqual(snap["report"]["sales"]["sales_mtd_cents"], 0)

    def test_unconfigured_month_read_raises(self):
        with self.assertRaises(ConfigNotFoundError):
            self.app.get_month_config(self.admin, self.sa, "2027-01")


if __name__ == "__main__":
    unittest.main()
