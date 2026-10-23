"""Task 18 tests: data-entry integrity — part-timer factor parsing, atomic
setup save, shared money parser, Daily clears (manpower + stock), stock-loss
negatives, deactivated-store read-only, Daily factor via AppService.
Disposable temp DBs only.

Run: python -m pytest tests/test_ops_integrity.py -v
"""

import inspect
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import kpi
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_write_repository import ValidationError

TODAY = date(2026, 10, 21)


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "i.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.month = "2026-10"
        self.app.configure_month(
            self.admin, self.sid, self.month, level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            turnover_threshold=0.05)

    def tearDown(self):
        self._tmp.cleanup()

    # 1 ------------------------------------------------------------------
    def test_factor_saved_as_factor_not_percent(self):
        """0.5 saves as 0.5 (not 0.005); 3.0 rejected (0-2 range is the
        state's job; the repo range check caps at 10 — state enforces 2)."""
        self.app.configure_month(self.admin, self.sid, self.month,
                                 part_timer_factor=0.5)
        cfg = self.app.get_month_config(self.admin, self.sid, self.month)
        self.assertAlmostEqual(float(cfg.part_timer_factor), 0.5)
        # state-level parsing: factor kind accepts 0.5 raw, rejects 3.
        # Exercise the real parse code path with a plain stand-in object
        # (Reflex state can't be instantiated outside an app context).
        import types
        from ducklab.ops_stores_state import SETUP_ALL, StoresState
        st = types.SimpleNamespace(
            setup_original={attr: "" for attr, _, _, _ in SETUP_ALL},
            **{attr: "" for attr, _, _, _ in SETUP_ALL})
        st.s_pt_factor = "0.5"
        parse = StoresState._parse_fields.__wrapped__ if hasattr(
            StoresState._parse_fields, "__wrapped__") else StoresState._parse_fields
        fields, errors = parse(st)
        self.assertEqual(errors, [])
        self.assertAlmostEqual(fields["part_timer_factor"], 0.5)
        st.s_pt_factor = "3"
        fields, errors = parse(st)
        self.assertEqual(fields, {})
        self.assertTrue(errors)  # 0-2 range enforced in state

    # 2 ------------------------------------------------------------------
    def test_setup_save_atomic_bad_budget_writes_nothing(self):
        """A negative budget is rejected by the repository with a specific
        ValidationError; the config change in the same save is NOT
        written (one transaction)."""
        cat = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                            sort_order=1)
        with self.assertRaises(ValidationError) as ctx:
            self.app.configure_month_with_budgets(
                self.admin, self.sid, self.month,
                {"level1_base_cents": 12345600, "level2_pct": 0.1, "level3_pct": 0.2, "level4_pct": 0.3},
                {cat: -500})
        self.assertEqual(str(ctx.exception),
                         f"Budget for category {cat} cannot be negative")
        cfg = self.app.get_month_config(self.admin, self.sid, self.month)
        self.assertEqual(cfg.level1_base_cents, 20000000)  # unchanged
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, self.month),
            {})

    def test_setup_save_two_budgets_read_back(self):
        """Setup save writes budgets where the Expenses page reads them."""
        c1 = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                           sort_order=1)
        c2 = self.app.add_expense_category(self.admin, self.sid, "Water",
                                           sort_order=2)
        self.app.configure_month_with_budgets(
            self.admin, self.sid, self.month,
            {"level1_base_cents": 12345600, "level2_pct": 0.1, "level3_pct": 0.2, "level4_pct": 0.3}, {c1: 50000, c2: 12345})
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, self.month),
            {c1: 50000, c2: 12345})
        cfg = self.app.get_month_config(self.admin, self.sid, self.month)
        self.assertEqual(cfg.level1_base_cents, 12345600)
        rep = self.app.preview_month(self.admin, self.sid, self.month,
                                     as_of=TODAY)
        self.assertEqual(rep.expenses.total_budget_cents, 62345)
        self.assertEqual(
            {c.name: c.budget_cents for c in rep.expenses.categories},
            {"Elec": 50000, "Water": 12345})
        # second save updates in place (no duplicate rows)
        self.app.configure_month_with_budgets(
            self.admin, self.sid, self.month, {}, {c1: 60000})
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, self.month),
            {c1: 60000, c2: 12345})

    def test_expense_page_budget_negative_rejected(self):
        cat = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                            sort_order=1)
        with self.assertRaises(ValidationError) as ctx:
            self.app.set_expense_budget(self.admin, self.sid, self.month,
                                        cat, -1)
        self.assertEqual(str(ctx.exception),
                         f"Budget for category {cat} cannot be negative")
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, self.month),
            {})

    def test_setup_blank_budget_matches_expenses_page(self):
        """Blank budget rule = Expenses page: an untouched blank is not
        sent; blanking an existing budget WITHOUT ticking "Clear saved
        value" is rejected (never written as NULL or 0)."""
        import types
        from ducklab.ops_stores_state import StoresState
        st = types.SimpleNamespace(budget_rows=[
            {"id": "7", "name": "Elec", "value": "", "original": ""},
            {"id": "8", "name": "Water", "value": " ", "original": "500.00"},
            {"id": "9", "name": "Gas", "value": "123.45", "original": ""},
        ])
        updates, errors = StoresState._parse_budgets(st)
        self.assertEqual(updates, {9: 12345})
        self.assertEqual(errors, [
            "Budget for Water: enter a budget amount, or tick "
            "“Clear saved value” to remove it (No budget)."])

    # 3 ------------------------------------------------------------------
    def test_unchanged_blank_field_does_not_overwrite(self):
        """Blank fields the user never touched are NOT sent (None values
        in the dict are explicit clears only)."""
        self.app.configure_month(self.admin, self.sid, self.month,
                                 part_timer_factor=0.7)
        # empty fields dict: config untouched
        self.app.configure_month_with_budgets(
            self.admin, self.sid, self.month, {}, {})
        cfg = self.app.get_month_config(self.admin, self.sid, self.month)
        self.assertAlmostEqual(float(cfg.part_timer_factor), 0.7)
        # explicit None DOES clear (user ticked clear / erased on purpose)
        self.app.configure_month_with_budgets(
            self.admin, self.sid, self.month, {"part_timer_factor": None}, {})
        cfg = self.app.get_month_config(self.admin, self.sid, self.month)
        self.assertIsNone(cfg.part_timer_factor)

    # 4 ------------------------------------------------------------------
    def test_money_parser_rejects_subcent_inf_nan(self):
        for bad in ("10.005", "inf", "-inf", "nan"):
            with self.assertRaises(ValueError):
                kpi.parse_money_cents(bad)
        self.assertEqual(kpi.parse_money_cents("350,819.00"), 35081900)

    # 5/6 ----------------------------------------------------------------
    def test_daily_clears_manpower_and_stock(self):
        self.app.record_daily(
            self.admin, self.sid, date(2026, 10, 5),
            sales_cents=rm(1000), ft_working=6,
            stock_losses={"DMO": rm(100)})
        # clear ft_working (NULL) and remove DMO (row deleted, not 0)
        self.app.record_daily(
            self.admin, self.sid, date(2026, 10, 5),
            ft_working=None, stock_remove=["DMO"])
        day = self.app.get_daily_records(self.admin, self.sid,
                                         self.month)[0]
        self.assertIsNone(day.ft_working)          # cleared -> NULL
        self.assertEqual(day.stock_losses, {})     # removed, not 0
        # sales left untouched (blank = no change)
        self.assertEqual(day.sales_cents, rm(1000))
        # Total Loss identical for a cleared code and a never-entered code
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 6),
                              sales_cents=rm(1000))
        d5 = self.app.get_daily_records(self.admin, self.sid, self.month)[0]
        d6 = self.app.get_daily_records(self.admin, self.sid, self.month)[1]
        signs = self.app.read_repo.get_contribution_signs()
        self.assertEqual(kpi.total_loss_cents(d5.stock_losses, signs), 0)
        self.assertEqual(kpi.total_loss_cents(d6.stock_losses, signs), 0)

    # 7 ------------------------------------------------------------------
    def test_negative_stock_amount_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, 7),
                                  sales_cents=rm(100),
                                  stock_losses={"DMO": -500})

    # 8 ------------------------------------------------------------------
    def test_inactive_store_write_rejected_read_allowed(self):
        self.app.set_store_active(self.admin, self.sid, False)
        with self.assertRaises(AuthorizationError) as ctx:
            self.app.record_daily(self.admin, self.sid, date(2026, 10, 8),
                                  sales_cents=rm(50))
        self.assertIn("deactivated", str(ctx.exception))
        with self.assertRaises(AuthorizationError):
            self.app.configure_month_with_budgets(self.admin, self.sid,
                                                  self.month, {}, {})
        # reads still work
        rep = self.app.preview_month(self.admin, self.sid, self.month,
                                     as_of=TODAY)
        self.assertIsNotNone(rep)

    def test_inactive_store_rejects_config_payroll_finalize_writes(self):
        cat = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                            sort_order=1)
        self.app.set_expense_budget(self.admin, self.sid, self.month, cat,
                                    50000)
        self.app.finalize_month(self.admin, self.sid, self.month,
                                as_of=TODAY)
        self.app.set_store_active(self.admin, self.sid, False)
        attempts = {
            "record_payroll": lambda: self.app.record_payroll(
                self.admin, self.sid, self.month, 100000, 0),
            "add_expense_category": lambda: self.app.add_expense_category(
                self.admin, self.sid, "Water"),
            "rename_expense_category":
                lambda: self.app.rename_expense_category(
                    self.admin, self.sid, cat, "Power"),
            "set_expense_budget": lambda: self.app.set_expense_budget(
                self.admin, self.sid, self.month, cat, 70000),
            "finalize_month": lambda: self.app.finalize_month(
                self.admin, self.sid, "2026-09", as_of=TODAY),
            "correct_month": lambda: self.app.correct_month(
                self.admin, self.sid, self.month, as_of=TODAY),
        }
        for name, call in attempts.items():
            with self.subTest(name):
                with self.assertRaises(AuthorizationError) as ctx:
                    call()
                self.assertEqual(str(ctx.exception),
                                 "Store is deactivated: read-only")
        # nothing changed
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, self.month),
            {cat: 50000})
        self.assertEqual(
            [c.name for c in self.app.get_expense_categories(
                self.admin, self.sid)], ["Elec"])
        cfg = self.app.get_month_config(self.admin, self.sid, self.month)
        self.assertIsNone(cfg.salary_basic_allowance_cents)
        self.assertEqual(
            [v["version"] for v in self.app.list_report_versions(
                self.admin, self.sid, self.month)], [1])

    # 9 ------------------------------------------------------------------
    def test_daily_loads_factor_via_appservice(self):
        from ducklab.ops_daily_state import DailyState
        import ducklab.ops_daily_state as mod
        src = inspect.getsource(mod)
        self.assertIn("app_service.get_month_config", src)
        self.assertNotIn("read_repo.get_month_config", src)
        self.assertNotIn("def _pt_factor", src)


if __name__ == "__main__":
    unittest.main()
