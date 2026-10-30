"""Task 22b tests: input robustness on socket-callable handlers — NaN/inf
factors, oversized amounts and counts, out-of-month / malformed dates,
non-numeric ids, negative payroll, specific exception handling (auth
failures show a message, never "—" or an empty dialog), and saving /
loading flags always reset. Every rejection asserts the exact exception
type and message, and saves read back that nothing was written.
Disposable temp DBs only.

Run: python -m pytest tests/test_ops_task22b.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService
from ducklab.input_validation import (
    parse_client_id, parse_count, parse_decimal, parse_money_cents,
)
from ducklab.auth_service import AuthenticationError
from ducklab.operations_write_repository import ValidationError

TODAY = date(2026, 10, 21)
SESSION = "Your session has expired. Please sign in again."
ACCESS = "You do not have access to this store."


def rm(v):
    return int(Decimal(str(v)) * 100)


def expired(self):
    raise AuthenticationError("Session expired")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "t22b.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=rm(100000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=9, part_timer_factor=0.5,
            salary_basic_allowance_cents=rm(10000))
        self.cat = self.app.add_expense_category(self.admin, self.sid,
                                                 "Elec", sort_order=1)

    def tearDown(self):
        self._tmp.cleanup()

    def revoked_viewer(self):
        """A viewer whose store assignment was revoked mid-session."""
        v = self.app.create_user(self.admin, "v@ducklab.com", "V",
                                 "ViewerPass123!")
        self.app.assign_role(self.admin, v.id, "viewer")
        self.app.assign_store(self.admin, v.id, self.sid)
        self.app.revoke_store(self.admin, v.id, self.sid)
        return self.app.auth.get_user(v.id)

    def bound(self, module, cls, fn, actor=None, month="2026-10"):
        """Call ``fn`` with the page state bound to this disposable DB."""
        actor_fn = actor if callable(actor) else (
            lambda self_, a=(actor or self.admin): a)
        sid = self.sid
        with mock.patch.object(module, "app_service", self.app), \
                mock.patch.object(cls, "_actor", actor_fn), \
                mock.patch.object(cls, "_selected_store_id",
                                  lambda self_: sid), \
                mock.patch.object(cls, "_selected_month",
                                  lambda self_: month):
            return fn()

    def config(self):
        return self.app.get_month_config(self.admin, self.sid, "2026-10")


# 1 ---------------------------------------------------------------------------
class NanFactorTests(Fixture):
    def test_shared_parser_rejects_nan_and_inf(self):
        for raw in ("NaN", "nan", "inf", "-Infinity", "sNaN"):
            with self.assertRaises(ValueError) as ctx:
                parse_decimal(raw, "Part-time conversion factor",
                                  Decimal(0), Decimal(2))
            self.assertEqual(str(ctx.exception),
                             "Part-time conversion factor must be a number")
        self.assertEqual(parse_decimal(" 0.75 ", "F", Decimal(0),
                                           Decimal(2)), Decimal("0.75"))

    def _save(self, **form):
        import ducklab.ops_manpower_state as mod
        from ducklab.ops_manpower_state import ManpowerState
        st = ManpowerState()
        st.cfg_original = {}
        for k, v in form.items():
            setattr(st, k, v)
        self.bound(mod, ManpowerState, lambda: list(st.save_config()))
        return st

    def test_manpower_nan_factor_is_form_error(self):
        st = self._save(c_pt_factor="NaN")
        self.assertEqual(st.cfg_error,
                         "Part-time conversion factor must be a number")
        self.assertFalse(st.cfg_saving)
        self.assertEqual(self.config().part_timer_factor, 0.5)  # unchanged

    def test_manpower_inf_turnover_is_form_error(self):
        st = self._save(c_turnover="inf")
        self.assertEqual(st.cfg_error,
                         "Turnover threshold (%) must be a number")
        self.assertIsNone(self.config().turnover_threshold)    # unchanged


# 2 ---------------------------------------------------------------------------
class OversizedInputTests(Fixture):
    def test_money_parser_bounds(self):
        self.assertEqual(parse_money_cents("100,000,000.00"),
                         10000000000)
        self.assertEqual(parse_money_cents("-100000000"), -10000000000)
        for raw in ("1e20", "100000000.01", "-100000000.01",
                    "1e999999999"):
            with self.assertRaises(ValueError) as ctx:
                parse_money_cents(raw)
            self.assertEqual(str(ctx.exception), "Amount too large")

    def test_count_parser_bound(self):
        self.assertEqual(parse_count("1000000", "Transactions"), 1000000)
        with self.assertRaises(ValueError) as ctx:
            parse_count("99999999999999999999", "Transactions")
        self.assertEqual(str(ctx.exception), "Transactions is too large")

    def test_daily_save_rejects_1e20(self):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        st.form_date = "2026-10-05"
        st.f_sales = "1e20"
        self.bound(mod, DailyState, lambda: list(st.save_entry()))
        self.assertEqual(st.form_error, "Amount too large")
        self.assertFalse(st.saving)
        self.assertTrue(st.form_open)          # reopened for correction
        self.assertEqual(
            self.app.get_daily_records(self.admin, self.sid, "2026-10"), [])

    def test_daily_save_rejects_huge_count(self):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        st.form_date = "2026-10-05"
        st.f_transactions = "99999999999999999999"
        self.bound(mod, DailyState, lambda: list(st.save_entry()))
        self.assertEqual(st.form_error, "Transactions is too large")
        self.assertFalse(st.saving)
        self.assertEqual(
            self.app.get_daily_records(self.admin, self.sid, "2026-10"), [])

    def test_monthly_setup_rejects_huge_and_nan_percent(self):
        import ducklab.ops_setup_state as mod
        from ducklab.ops_setup_state import SetupState
        for raw in ("1e999999999", "NaN"):
            st = SetupState()
            st.setup_original = {}
            st.s_level2 = raw
            self.bound(mod, SetupState, lambda: list(st.save_setup()))
            self.assertEqual(
                st.setup_error,
                "Level 2 Uplift (%): enter a non-negative number "
                "(or leave blank)")
            self.assertFalse(st.setup_saving)
            self.assertEqual(self.config().level2_pct,
                             Decimal("0.1"))              # unchanged

    def test_expense_save_rejects_1e20(self):
        import ducklab.ops_expenses_state as mod
        from ducklab.ops_expenses_state import ExpensesState
        st = ExpensesState()
        st.e_amount = "1e20"
        st.e_description = "huge"
        self.bound(mod, ExpensesState, lambda: list(st.save_expense()))
        self.assertEqual(st.exp_form_error, "Amount too large")
        self.assertFalse(st.exp_saving)
        self.assertEqual(
            self.app.get_expense_transactions(self.admin, self.sid), [])


# 3 ---------------------------------------------------------------------------
class DailyEntryDateTests(Fixture):
    def _open(self, iso, actor=None):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        self.bound(mod, DailyState, lambda: st.open_entry(iso), actor=actor)
        return st

    def test_out_of_month_date_rejected_without_state_change(self):
        st = self._open("2026-11-05")
        self.assertEqual(
            st.form_error,
            "2026-11-05 is outside the selected month (October 2026).")
        self.assertFalse(st.form_open)
        self.assertEqual(st.form_date, "")
        self.assertEqual(st.form_title, "")

    def test_malformed_date_rejected(self):
        st = self._open("not-a-date")
        self.assertEqual(st.form_error,
                         "Invalid date — choose a day from the list.")
        self.assertFalse(st.form_open)
        self.assertEqual(st.form_date, "")
        self.assertEqual(st.form_day_label, "")

    def test_valid_date_opens(self):
        st = self._open("2026-10-05")
        self.assertEqual(st.form_error, "")
        self.assertTrue(st.form_open)
        self.assertEqual(st.form_day_label, "Monday, 5 October 2026")

    def test_session_expired_does_not_open(self):
        st = self._open("2026-10-05", actor=expired)
        self.assertEqual(st.form_error, SESSION)
        self.assertFalse(st.form_open)

    def test_save_with_tampered_date_writes_nothing(self):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        st.form_date = "2026-09-30"   # not in the selected month
        st.f_sales = "100"
        self.bound(mod, DailyState, lambda: list(st.save_entry()))
        self.assertEqual(
            st.form_error,
            "2026-09-30 is outside the selected month (October 2026).")
        self.assertFalse(st.saving)
        self.assertEqual(
            self.app.get_daily_records(self.admin, self.sid, "2026-09"), [])


# 4 ---------------------------------------------------------------------------
class ClientIdTests(Fixture):
    def test_shared_helper(self):
        self.assertIsNone(parse_client_id("", "store"))
        self.assertIsNone(parse_client_id(None, "store"))
        self.assertEqual(parse_client_id(" 12 ", "store"), 12)
        for raw in ("abc", "1.5", "0", "-3", "1e3"):
            with self.assertRaises(ValidationError) as ctx:
                parse_client_id(raw, "transaction")
            self.assertEqual(str(ctx.exception),
                             "Unknown transaction — reopen it from the list.")
        with self.assertRaises(ValidationError) as ctx:
            parse_client_id("", "user", required=True)
        self.assertEqual(str(ctx.exception),
                         "Unknown user — reopen it from the list.")

    def _expenses(self):
        import ducklab.ops_expenses_state as mod
        from ducklab.ops_expenses_state import ExpensesState
        return mod, ExpensesState, ExpensesState()

    def test_expense_open_rejects_bad_id_before_editing_state(self):
        mod, cls, st = self._expenses()
        for raw in ("abc", "999"):
            self.bound(mod, cls, lambda: st.open_expense_form(raw))
            self.assertEqual(st.exp_form_error,
                             "Unknown transaction — reopen it from the list.")
            self.assertEqual(st.editing_id, "")
            self.assertFalse(st.exp_form_open)

    def test_expense_save_with_tampered_id_writes_nothing(self):
        mod, cls, st = self._expenses()
        st.editing_id = "abc"
        st.e_description = "x"
        self.bound(mod, cls, lambda: list(st.save_expense()))
        self.assertEqual(st.exp_form_error,
                         "Unknown transaction — reopen it from the list.")
        self.assertFalse(st.exp_saving)
        self.assertEqual(
            self.app.get_expense_transactions(self.admin, self.sid), [])

    def _stores(self):
        import ducklab.ops_stores_state as mod
        from ducklab.ops_stores_state import StoresState
        return mod, StoresState, StoresState()

    def test_store_form_rejects_bad_id(self):
        mod, cls, st = self._stores()
        for raw in ("x1", "5"):
            self.bound(mod, cls, lambda: st.open_store_form(raw))
            self.assertEqual(st.stores_error,
                             "Unknown store — reopen it from the list.")
            self.assertFalse(st.sf_open)
            self.assertEqual(st.sf_editing, "")

    def test_save_store_tampered_or_missing_store(self):
        mod, cls, st = self._stores()
        st.sf_editing, st.sf_name = "abc", "Renamed"
        self.bound(mod, cls, lambda: list(st.save_store()))
        self.assertEqual(st.sf_error,
                         "Unknown store — reopen it from the list.")
        self.assertFalse(st.sf_saving)
        st.sf_editing = "999"                      # parseable, not a store
        self.bound(mod, cls, lambda: list(st.save_store()))
        self.assertEqual(st.sf_error, "This store no longer exists.")
        self.assertFalse(st.sf_saving)
        self.assertEqual(self.app.get_store(self.admin, self.sid).name,
                         "Alpha")                  # unchanged

    def test_assignment_ids(self):
        mod, cls, st = self._stores()
        from ducklab.ops_assignment_state import AssignmentState
        st = AssignmentState()
        self.bound(mod, AssignmentState, lambda: list(st.open_assign("abc")))
        self.assertEqual(st.stores_error,
                         "Unknown store — reopen it from the list.")
        self.assertEqual(st.assign_store_id, "")
        v = self.app.create_user(self.admin, "v@ducklab.com", "V",
                                 "ViewerPass123!")
        self.app.assign_store(self.admin, v.id, self.sid)
        st.assign_store_id = str(self.sid)
        self.bound(mod, AssignmentState,
                   lambda: list(st.unassign_user("abc")))
        self.assertEqual(st.stores_error,
                         "Unknown user — reopen it from the list.")
        self.assertEqual(self.app.auth.get_user(v.id).store_ids,
                         (self.sid,))              # still assigned


# 5 ---------------------------------------------------------------------------
class NegativePayrollTests(Fixture):
    def test_repository_rejects_negative_payroll(self):
        for field in ("salary_basic_allowance_cents", "overtime_cents",
                      "support_team_cost_cents"):
            with self.assertRaises(ValidationError) as ctx:
                self.app.configure_month(self.admin, self.sid, "2026-10",
                                         **{field: -1})
            self.assertEqual(str(ctx.exception),
                             f"{field} cannot be negative")
        with self.assertRaises(ValidationError) as ctx:
            self.app.configure_month_with_budgets(
                self.admin, self.sid, "2026-10",
                {"overtime_cents": -500}, {self.cat: rm(10)})
        self.assertEqual(str(ctx.exception),
                         "overtime_cents cannot be negative")
        cfg = self.config()
        self.assertEqual(cfg.salary_basic_allowance_cents, 1000000)
        self.assertIsNone(cfg.overtime_cents)
        self.assertIsNone(cfg.support_team_cost_cents)
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, "2026-10"),
            {})                                    # atomic: no budget row

    def test_manpower_form_rejects_negative_payroll(self):
        import ducklab.ops_manpower_state as mod
        from ducklab.ops_manpower_state import ManpowerState
        st = ManpowerState()
        st.cfg_original = {}
        st.p_salary = "-100"
        self.bound(mod, ManpowerState, lambda: list(st.save_config()))
        self.assertEqual(st.cfg_error,
                         "Basic salary + allowances cannot be negative")
        self.assertFalse(st.cfg_saving)
        self.assertEqual(self.config().salary_basic_allowance_cents, 1000000)


# 6/7 -------------------------------------------------------------------------
class AuthFailureTests(Fixture):
    """Auth failures show a message — never "—" rows or an empty dialog."""

    def test_daily_load_revoked(self):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        self.bound(mod, DailyState, st.load_daily_data,
                   actor=self.revoked_viewer())
        self.assertEqual((st.report_status, st.error_message),
                         ("error", ACCESS))
        self.assertEqual(st.day_rows, [])
        self.assertFalse(st.can_write)

    def test_expenses_load_revoked_mid_session(self):
        import ducklab.ops_expenses_state as mod
        from ducklab.ops_expenses_state import ExpensesState
        st = ExpensesState()
        self.bound(mod, ExpensesState, st.load_expenses_data,
                   actor=self.revoked_viewer())
        self.assertEqual((st.report_status, st.error_message),
                         ("error", ACCESS))
        self.assertEqual(st.all_expense_rows, [])

    def test_expenses_budget_session_expired(self):
        import ducklab.ops_expenses_state as mod
        from ducklab.ops_expenses_state import ExpensesState
        st = ExpensesState()
        st.b_category, st.b_amount = "Elec", "100"
        self.bound(mod, ExpensesState, lambda: list(st.save_budget()),
                   actor=expired)
        self.assertEqual(st.b_error, SESSION)
        self.assertFalse(st.b_saving)
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, "2026-10"),
            {})

    def test_manpower_load_revoked(self):
        import ducklab.ops_manpower_state as mod
        from ducklab.ops_manpower_state import ManpowerState
        st = ManpowerState()
        self.bound(mod, ManpowerState, st.load_manpower_data,
                   actor=self.revoked_viewer())
        self.assertEqual(st.mp_error, ACCESS)
        self.assertEqual(st.hc_kpis, {})

    def test_manpower_config_dialog_not_opened(self):
        import ducklab.ops_manpower_state as mod
        from ducklab.ops_manpower_state import ManpowerState
        st = ManpowerState()
        self.bound(mod, ManpowerState, st.open_config, actor=expired)
        self.assertEqual(st.cfg_error, SESSION)
        self.assertFalse(st.cfg_open)
        self.bound(mod, ManpowerState, st.open_config,
                   actor=self.revoked_viewer())
        self.assertEqual(st.cfg_error, ACCESS)
        self.assertFalse(st.cfg_open)

    def test_stores_setup_revoked(self):
        import ducklab.ops_setup_state as mod
        from ducklab.ops_setup_state import SetupState
        st = SetupState()
        self.bound(mod, SetupState, st.load_setup_data,
                   actor=self.revoked_viewer())
        self.assertEqual(st.setup_error, ACCESS)
        self.assertEqual(st.budget_rows, [])
        self.assertFalse(st.can_edit_setup)

    def test_stock_load_revoked(self):
        import ducklab.ops_stock_state as mod
        from ducklab.ops_stock_state import StockState
        st = StockState()
        self.bound(mod, StockState, st.load_stock_data,
                   actor=self.revoked_viewer())
        self.assertEqual(st.st_error, ACCESS)
        self.assertFalse(st.st_loaded)

    def test_stock_session_expired(self):
        import ducklab.ops_stock_state as mod
        from ducklab.ops_stock_state import StockState
        st = StockState()
        self.bound(mod, StockState, st.load_stock_data, actor=expired)
        self.assertEqual(st.st_error, SESSION)


# 8 ---------------------------------------------------------------------------
class FlagResetTests(Fixture):
    def test_action_saving_reset_after_failed_save(self):
        import ducklab.ops_actions_state as mod
        from ducklab.ops_actions_state import ActionsState
        st = ActionsState()
        st.can_write_actions = True
        st.a_raised = "21/10/2026"                 # malformed date
        self.bound(mod, ActionsState, lambda: list(st.save_action()))
        self.assertEqual(st.form_error,
                         "Raised: enter a date as YYYY-MM-DD")
        self.assertFalse(st.saving)
        self.assertEqual(self.app.get_actions(self.admin, self.sid), [])

    def test_report_never_left_loading(self):
        import ducklab.ops_state as mod
        from ducklab.ops_state import OpsState
        st = OpsState()
        st.has_stores = True
        boom = mock.patch.object(self.app, "preview_month",
                                 side_effect=RuntimeError("db gone"))
        with boom:
            with self.assertRaises(RuntimeError):
                self.bound(mod, OpsState, lambda: list(st.refresh_report()))
        self.assertEqual((st.report_status, st.error_message),
                         ("error", "Report unavailable."))


if __name__ == "__main__":
    unittest.main()
