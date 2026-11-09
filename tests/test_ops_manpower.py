"""Tests for Manpower Efficiency: headcount, staffing, leave, payroll,
salary ratio, configuration, RBAC and payroll confidentiality. Temp DBs only.

Run: python -m pytest tests/test_ops_manpower.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import kpi
from ducklab.app_service import AppService
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.operations_write_repository import ValidationError


def rm(v):
    return int(Decimal(str(v)) * 100)


class ManpowerFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "mp.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.sb = self.app.register_store(self.admin, "B02", "Beta")
        for sid in (self.sa, self.sb):
            self.app.configure_month(
                self.admin, sid, "2026-09", level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                opening_headcount_ft=10, budget_headcount=12,
                turnover_threshold=0.45, stock_loss_target=0.015,
                salary_ratio_target=0.06818, part_timer_factor=0.5,
                salary_basic_allowance_cents=rm(10000),
                overtime_cents=rm(1000),
                support_team_cost_cents=rm(500))
            self.app.configure_month(
                self.admin, sid, "2026-10", level1_base_cents=rm(210000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                opening_headcount_ft=11, budget_headcount=11,
                turnover_threshold=0.45, stock_loss_target=0.015,
                salary_ratio_target=0.06818, part_timer_factor=0.25,
                salary_basic_allowance_cents=rm(10500),
                overtime_cents=rm(800))

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores):
        u = self.app.create_user(self.admin, email, "T", "Passw0rd!xyz")
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)


class TestHeadcount(ManpowerFixture):
    def test_opening_hires_resignations_closing(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              new_hires_ft=2)
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 5),
                              resigned_ft=1)
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        m = rep.manpower
        self.assertEqual(m.opening_headcount, 11)
        self.assertEqual(m.hires, 2)
        self.assertEqual(m.resigned, 1)
        self.assertEqual(m.closing_headcount, 12)
        # budget 11 -> over budget by 1
        self.assertEqual(m.headcount_variance, 1)
        self.assertEqual(m.headcount_status, "Over budget")

    def test_vacancy_status(self):
        rep = self.app.preview_month(self.admin, self.sa, "2026-09",
                                     as_of=date(2026, 9, 30))
        # opening 10, no movement -> closing 10 vs budget 12 -> Vacancy -2
        self.assertEqual(rep.manpower.headcount_variance, -2)
        self.assertEqual(rep.manpower.headcount_status, "Vacancy")

    def test_on_budget_status(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 3),
                              new_hires_ft=2)
        rep = self.app.preview_month(self.admin, self.sa, "2026-09",
                                     as_of=date(2026, 9, 30))
        self.assertEqual(rep.manpower.headcount_variance, 0)
        self.assertEqual(rep.manpower.headcount_status, "On budget")

    def test_zero_opening_headcount(self):
        self.app.configure_month(self.admin, self.sa, "2026-10",
                                 opening_headcount_ft=0)
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 2),
                              resigned_ft=1)
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        self.assertIsNone(rep.manpower.turnover_pct)
        self.assertEqual(rep.manpower.turnover_status, "Awaiting HC")

    def test_missing_opening_headcount(self):
        self.app.configure_month(self.admin, self.sa, "2026-10",
                                 opening_headcount_ft=None)
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        self.assertIsNone(rep.manpower.closing_headcount)
        self.assertEqual(rep.manpower.headcount_status, "Awaiting HC")


class TestStaffing(ManpowerFixture):
    def test_effective_staff_with_configured_factor(self):
        # Oct: factor 0.25 (store-specific, not hardcoded 0.5)
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              ft_working=8, pt_working=4)
        days = self.app.get_daily_records(self.admin, self.sa, "2026-10")
        eff = kpi.effective_staff(days[0].ft_working, days[0].pt_working,
                                  Decimal("0.25"))
        self.assertEqual(eff, Decimal("9"))

    def test_missing_attendance_vs_zero(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              ft_working=0, pt_working=0)     # reported zero
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 2),
                              sales_cents=rm(100))             # no attendance
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        # only the reported day counts; zero attendance is a real 0
        self.assertEqual(rep.manpower.average_effective_staff, Decimal(0))

    def test_average_and_sales_per_staff(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              ft_working=8, pt_working=4,
                              sales_cents=rm(900))
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 2),
                              ft_working=6, sales_cents=rm(600))
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        m = rep.manpower
        # factor 0.25: day1 eff 9, day2 eff 6 -> avg 7.5
        self.assertEqual(m.average_effective_staff, Decimal("7.5"))
        # sales/staff-days: 1500 / 15 = RM 100
        self.assertEqual(int(m.sales_per_staff), rm(100))


class TestLeave(ManpowerFixture):
    def test_leave_totals(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              leave_annual=1, leave_mc=2,
                              leave_other_paid=1, leave_unpaid=1)
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 2),
                              leave_mc=1)
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        self.assertEqual(rep.manpower.leave_days, 6)


class TestPayroll(ManpowerFixture):
    def test_total_salary_and_ratios(self):
        # September: basic 10,000 + OT 1,000 + support 500
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(300000))
        rep = self.app.preview_month(self.admin, self.sa, "2026-09",
                                     as_of=date(2026, 9, 30))
        m = rep.manpower
        self.assertEqual(m.total_salary_cents, rm(11000))
        self.assertEqual(m.salary_ratio_store,
                         Decimal(rm(11000)) / Decimal(rm(300000)))
        self.assertEqual(m.salary_ratio_incl_support,
                         Decimal(rm(11500)) / Decimal(rm(300000)))
        self.assertEqual(m.salary_ratio_status, "Within target")
        self.assertEqual(m.salary_budget_cents,
                         Decimal(rm(300000)) * Decimal("0.06818"))

    def test_awaiting_payroll(self):
        self.app.record_payroll(self.admin, self.sa, "2026-10", None, None,
                                None)
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              sales_cents=rm(100))
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        self.assertIsNone(rep.manpower.total_salary_cents)
        self.assertEqual(rep.manpower.salary_ratio_status, "Awaiting Payroll")

    def test_explicit_null_clearing(self):
        cfg = self.app.get_month_config(self.admin, self.sa, "2026-10")
        self.assertEqual(cfg.salary_basic_allowance_cents, rm(10500))
        self.app.record_payroll(self.admin, self.sa, "2026-10", None, None,
                                None)
        cfg = self.app.get_month_config(self.admin, self.sa, "2026-10")
        self.assertIsNone(cfg.salary_basic_allowance_cents)
        self.assertIsNone(cfg.overtime_cents)
        self.assertIsNone(cfg.support_team_cost_cents)

    def test_sub_cent_rejected(self):
        # UI boundary: parse_money_cents refuses sub-cent precision before any
        # value reaches the repository (integer-cent storage).
        from ducklab.input_validation import parse_money_cents
        with self.assertRaises(ValueError):
            parse_money_cents("10.001")
        with self.assertRaises(ValueError):
            parse_money_cents("RM 1.234")
        self.assertEqual(parse_money_cents("1,234.56"), rm("1234.56"))

    def test_payroll_recalc_and_store_isolation(self):
        for sid in (self.sa, self.sb):
            self.app.record_daily(self.admin, sid, date(2026, 10, 1),
                                  sales_cents=rm(100000))
        before_b = self.app.preview_month(self.admin, self.sb, "2026-10",
                                          as_of=date(2026, 10, 31))
        self.app.record_payroll(self.admin, self.sa, "2026-10",
                                rm(20000), rm(2000), rm(1000))
        after_a = self.app.preview_month(self.admin, self.sa, "2026-10",
                                         as_of=date(2026, 10, 31))
        after_b = self.app.preview_month(self.admin, self.sb, "2026-10",
                                         as_of=date(2026, 10, 31))
        self.assertEqual(after_a.manpower.total_salary_cents, rm(22000))
        self.assertEqual(after_b.manpower.total_salary_cents,
                         before_b.manpower.total_salary_cents)  # unchanged

    def test_turnover_threshold_not_hardcoded(self):
        self.app.configure_month(self.admin, self.sa, "2026-09",
                                 turnover_threshold=0.05)
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 2),
                              resigned_ft=1)
        rep = self.app.preview_month(self.admin, self.sa, "2026-09",
                                     as_of=date(2026, 9, 30))
        # 1/10 = 10% > 5% configured threshold
        self.assertEqual(rep.manpower.turnover_status, "HIGH TURNOVER")


class TestConfiguration(ManpowerFixture):
    def test_partial_update_preserves_unrelated(self):
        self.app.configure_month(self.admin, self.sa, "2026-10",
                                 budget_headcount=15)
        cfg = self.app.get_month_config(self.admin, self.sa, "2026-10")
        self.assertEqual(cfg.budget_headcount, 15)
        self.assertEqual(cfg.level1_base_cents, rm(210000))   # preserved
        self.assertEqual(cfg.part_timer_factor, Decimal("0.25"))
        self.assertEqual(cfg.salary_basic_allowance_cents, rm(10500))

    def test_finalized_snapshot_warning_and_preservation(self):
        self.app.finalize_month(self.admin, self.sa, "2026-09",
                                as_of=date(2026, 9, 30))
        result = self.app.configure_month(self.admin, self.sa, "2026-09",
                                          budget_headcount=20)
        self.assertTrue(result["finalized_snapshot_exists"])
        snap = self.app.get_finalized_report(self.admin, self.sa, "2026-09")
        # snapshot headcount budget preserved (version content untouched)
        self.assertIn("manpower", snap["report"])

    def test_no_implicit_config_creation(self):
        with self.assertRaises(ConfigNotFoundError):
            self.app.get_month_config(self.admin, self.sa, "2027-01")


class TestManpowerSecurity(ManpowerFixture):
    def test_viewer_cannot_configure(self):
        v = self.user("v@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(v, self.sa, "2026-10", budget_headcount=5)

    def test_viewer_cannot_write_payroll(self):
        v = self.user("v2@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(
                v, self.sa, "2026-10", salary_basic_allowance_cents=rm(1))

    def test_branch_manager_cannot_write_payroll(self):
        bm = self.user("bm@ducklab.com", ["branch_manager"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(
                bm, self.sa, "2026-10", salary_basic_allowance_cents=rm(1))

    def test_cross_store_config_rejected(self):
        bm = self.user("bm2@ducklab.com", ["administrator"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(bm, self.sb, "2026-10",
                                     budget_headcount=5)

    def test_disabled_user_rejected(self):
        adm = self.user("a2@ducklab.com", ["administrator"], [self.sa])
        self.app.set_user_active(self.admin, adm.id, False)
        adm = self.app.auth.get_user(adm.id)
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(adm, self.sa, "2026-10",
                                     budget_headcount=5)

    def test_expired_session_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.app.authenticate("not-a-real-token")

    def test_payroll_visibility_policy_is_config_manager_only(self):
        from ducklab import authz
        v = self.user("v3@ducklab.com", ["viewer"], [self.sa])
        bm = self.user("bm3@ducklab.com", ["branch_manager"], [self.sa])
        zm = self.user("zm@ducklab.com", ["zone_manager"], [self.sa])
        ad = self.user("ad@ducklab.com", ["administrator"], [self.sa])
        # Restrictive documented policy: only config managers see payroll.
        self.assertFalse(authz.can_manage_config(v))
        self.assertFalse(authz.can_manage_config(bm))
        self.assertFalse(authz.can_manage_config(zm))
        self.assertTrue(authz.can_manage_config(ad))
        self.assertTrue(authz.can_manage_config(self.admin))

    def test_daily_staffing_via_authorized_write_service(self):
        bm = self.user("bm4@ducklab.com", ["branch_manager"], [self.sa])
        rid = self.app.record_daily(bm, self.sa, date(2026, 10, 3),
                                    ft_working=7, pt_working=2,
                                    leave_mc=1)
        self.assertIsInstance(rid, int)
        days = self.app.get_daily_records(self.admin, self.sa, "2026-10")
        rec = next(d for d in days if d.date == date(2026, 10, 3))
        self.assertEqual(rec.ft_working, 7)
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(bm, self.sb, date(2026, 10, 3),
                                  ft_working=1)

    def test_finalized_snapshot_unchanged_by_payroll_edit(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 9, 1),
                              sales_cents=rm(300000))
        self.app.finalize_month(self.admin, self.sa, "2026-09",
                                as_of=date(2026, 9, 30))
        before = self.app.get_finalized_report(self.admin, self.sa, "2026-09")
        self.app.record_payroll(self.admin, self.sa, "2026-09",
                                rm(99999), rm(0), rm(0))
        after = self.app.get_finalized_report(self.admin, self.sa, "2026-09")
        self.assertEqual(before["report"]["manpower"]["total_salary_cents"],
                         after["report"]["manpower"]["total_salary_cents"])


class TestStateValidation(ManpowerFixture):
    """Form-level parsing used by the manpower configuration editor."""

    def test_deactivated_store_hides_payroll_editing(self):
        import ducklab.ops_manpower_state as mod
        from ducklab.ops_manpower_state import ManpowerState

        st = ManpowerState()
        st.selected_store_active = False
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ManpowerState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ManpowerState, "_selected_store_id",
                                  lambda _: self.sa), \
                mock.patch.object(ManpowerState, "_selected_month",
                                  lambda _: "2026-10"):
            st.load_manpower_data()

        self.assertFalse(st.can_manage_manpower)
        self.assertTrue(st.can_view_payroll)

    def test_parse_helpers(self):
        from ducklab.input_validation import parse_decimal
        self.assertEqual(parse_decimal("0.5", "x", Decimal(0), Decimal(2)),
                         Decimal("0.5"))
        with self.assertRaises(ValueError):
            parse_decimal("3", "x", Decimal(0), Decimal(2))
        with self.assertRaises(ValueError):
            parse_decimal("abc", "x", Decimal(0), Decimal(2))
        from ducklab.input_validation import parse_count, parse_money_cents
        self.assertEqual(parse_money_cents("12,000.00"), rm("12000.00"))
        with self.assertRaises(ValueError):
            parse_money_cents("10.001")   # sub-cent
        with self.assertRaises(ValueError):
            parse_money_cents("nope")
        with self.assertRaises(ValueError):
            parse_count("-1", "Headcount")


if __name__ == "__main__":
    unittest.main()
