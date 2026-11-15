"""V3 tests: deep-link date (literal), one negative money format, treatment
chip colours, salary-ratio bar colour, Expenses compact tile values.
Disposable temp DBs only.

Run: python -m pytest tests/test_ops_v3_design.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab import ops_charts
from ducklab.app_service import AppService


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "v3.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=rm(210000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=10, salary_ratio_target=0.06818,
            part_timer_factor=0.5, salary_basic_allowance_cents=rm(10500))

    def tearDown(self):
        self._tmp.cleanup()

    def bound(self, modname, clsname, fn_name, *args):
        import importlib
        mod = importlib.import_module(f"ducklab.{modname}")
        cls = getattr(mod, clsname)
        sid, admin = self.sid, self.admin
        patches = [
            mock.patch.object(mod, "app_service", self.app),
            mock.patch.object(cls, "_actor", lambda self: admin),
            mock.patch.object(cls, "_selected_store_id", lambda self: sid),
            mock.patch.object(cls, "_selected_month",
                              lambda self: "2026-10"),
        ]
        st = cls()
        st.has_stores = True
        for p in patches:
            p.start()
        try:
            out = getattr(st, fn_name)(*args)
            if out is not None and hasattr(out, "__next__"):
                for _ in out:
                    pass
        finally:
            for p in reversed(patches):
                p.stop()
        return st


class DeepLinkDateTests(Fixture):
    """Section 0: the Daily entry opens for exactly the linked date."""

    def test_entry_opens_for_literal_date(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 4),
                              sales_cents=rm(9900))
        for iso, label, sales in (
                ("2026-10-01", "Thursday, 1 October 2026", ""),
                ("2026-10-04", "Sunday, 4 October 2026", "9900.00"),
                ("2026-10-18", "Sunday, 18 October 2026", ""),
                ("2026-10-31", "Saturday, 31 October 2026", "")):
            st = self.bound("ops_daily_state", "DailyState", "open_entry",
                            iso)
            self.assertTrue(st.form_open, iso)
            self.assertEqual(st.form_date, iso)          # no day shift
            self.assertEqual(st.form_day_label, label)
            self.assertEqual(st.f_sales, sales)          # that day's record

    def test_date_outside_month_is_refused(self):
        st = self.bound("ops_daily_state", "DailyState", "open_entry",
                        "2026-11-01")
        self.assertFalse(st.form_open)
        self.assertIn("outside the selected month", st.form_error)


class NegativeMoneyTests(unittest.TestCase):
    def test_minus_before_rm_everywhere(self):
        m = "−"
        self.assertEqual(ds.fmt_rm(-8500), f"{m}RM 85.00")
        self.assertEqual(ds.fmt_rm0(-989800), f"{m}RM 9,898")
        self.assertEqual(ds.fmt_rm_compact(-2350000), f"{m}RM 23.5k")
        self.assertEqual(ds.fmt_rm_compact(-706666, whole_below=True),
                         f"{m}RM 7,067")
        self.assertEqual(ops_charts.fmt_rm(-8500.0), f"{m}RM 85.00")
        self.assertEqual(ops_charts.fmt_money_compact(-500), f"{m}RM 5")
        self.assertEqual(ds.fmt_rm(0), "RM 0.00")         # zero unsigned
        self.assertEqual(ds.fmt_rm(None), "—")


class ChipColourTests(unittest.TestCase):
    def test_treatment_chips_are_not_the_accent(self):
        colours = {t: ds.status_color(t)
                   for t in ("Included", "Offset", "Info Only")}
        # V5: Offset green (as the composition chart), the others gray
        self.assertEqual(colours, {"Included": "gray", "Offset": "green",
                                   "Info Only": "gray"})
        self.assertNotIn("indigo", colours.values())


class ManpowerBarTests(Fixture):
    def test_bar_uses_status_colour(self):
        # RM 10,500 salary vs RM 31,000 sales -> far over the 6.8% target.
        for d in range(1, 32):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(1000), ft_working=8)
        st = self.bound("ops_manpower_state", "ManpowerState",
                        "load_manpower_data")
        self.assertIn("Failure", st.pay_kpis["status"])
        self.assertEqual(st.pay_kpis["bar_color"], "red")

    def test_incomplete_month_bar_is_neutral(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(12000), ft_working=8)
        st = self.bound("ops_manpower_state", "ManpowerState",
                        "load_manpower_data")
        self.assertIn("Failure", st.pay_kpis["status"])
        self.assertEqual(st.pay_kpis["bar_color"], "gray")


class ExpensesTileTests(Fixture):
    def test_compact_values(self):
        self.app.add_expense_category(self.admin, self.sid, "Power")
        st = self.bound("ops_expenses_state", "ExpensesState",
                        "load_expenses_data")
        # nothing paid yet: text value, compact is a dash
        self.assertEqual(st.exp_kpis["paid"], "No payments yet")
        self.assertEqual(st.exp_kpis["paid_compact"], "—")
        self.assertEqual(st.exp_kpis["open"], "RM 0.00")
        self.assertEqual(st.exp_kpis["open_compact"], "RM 0")


if __name__ == "__main__":
    unittest.main()
