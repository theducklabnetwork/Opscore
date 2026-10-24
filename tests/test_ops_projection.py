"""Task 19a tests: engine-owned growth/gap/average/budget-control logic,
sales_status Decimal precision, projection_state boundaries, pages read
engine values. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_projection.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import kpi
from ducklab.app_service import AppService


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "p.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")

    def tearDown(self):
        self._tmp.cleanup()

    def _config(self, month, **kw):
        base = dict(level1_base_cents=rm(200000), level2_pct=0.1,
                    level3_pct=0.2, level4_pct=0.3, opening_headcount_ft=9,
                    stock_loss_target=0.015, salary_ratio_target=0.06818,
                    part_timer_factor=0.5, turnover_threshold=0.05)
        base.update(kw)
        self.app.configure_month(self.admin, self.sid, month, **base)

    def _days(self, month, n, sales=1000):
        y, m = (int(x) for x in month.split("-"))
        for d in range(1, n + 1):
            self.app.record_daily(self.admin, self.sid, date(y, m, d),
                                  sales_cents=rm(sales), ft_working=4,
                                  pt_working=2)

    # growth status ------------------------------------------------------
    def test_growth_status_variants(self):
        self._config("2026-10", sales_last_year_cents=rm(180000))
        # no reference for last month
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        self.assertEqual(rep.sales.growth_status_last_year, "Awaiting data")
        self._days("2026-10", 10, sales=7000)   # 70,000 MTD -> proj 217,000
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        # projected 217,000 vs 180,000 -> Growth; vs last month None -> No ref
        self.assertEqual(rep.sales.growth_status_last_year, "Growth")
        self.assertEqual(rep.sales.growth_status_last_month, "No reference")
        self._config("2026-10", sales_last_month_cents=rm(300000))
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        self.assertEqual(rep.sales.growth_status_last_month, "Decline")

    # gap per level --------------------------------------------------------
    def test_gap_per_level(self):
        self._config("2026-10", level2_pct=0.10)
        self._days("2026-10", 31, sales=7000)  # complete: actual = 217,000
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 31))
        gaps = rep.sales.gap_by_level
        self.assertEqual(gaps["level1"], rm(217000) - rm(200000))
        self.assertEqual(gaps["level2"], rm(217000) - rm(220000))
        self.assertEqual(rep.sales.projection_state, "actual")

    # average per reported day --------------------------------------------
    def test_average_per_reported_day(self):
        self._config("2026-10")
        self._days("2026-10", 10, sales=700)
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        self.assertEqual(rep.sales.average_per_day, rm(700))
        # no days -> None
        self._config("2026-11")
        rep2 = self.app.preview_month(self.admin, self.sid, "2026-11",
                                      as_of=date(2026, 11, 1))
        self.assertIsNone(rep2.sales.average_per_day)

    # budget-control status (Expense Monitor H25) --------------------------
    def test_budget_control_status_boundaries(self):
        self._config("2026-10")
        cat = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                            sort_order=1)
        # None budget -> "No budget"
        self.app.record_expense(self.admin, self.sid,
                                incurred_date=date(2026, 10, 2),
                                paid_date=date(2026, 10, 3), category_id=cat,
                                description="p", amount_cents=rm(80),
                                paid_by="Petty Cash", status="Paid",
                                verification="Verified", recorded_by="DEV",
                                receipt_ref="R")
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        self.assertEqual(rep.expenses.budget_control_status, "No budget")
        # exactly 80% -> Watch
        self.app.set_expense_budget(self.admin, self.sid, "2026-10", cat,
                                    rm(100))
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        self.assertEqual(rep.expenses.budget_control_status, "Watch")
        # over -> OVER BUDGET
        self.app.set_expense_budget(self.admin, self.sid, "2026-10", cat,
                                    rm(79))
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        self.assertEqual(rep.expenses.budget_control_status, "OVER BUDGET")
        # under 80% -> Within budget
        self.app.set_expense_budget(self.admin, self.sid, "2026-10", cat,
                                    rm(200))
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 21))
        self.assertEqual(rep.expenses.budget_control_status, "Within budget")

    # sales_status Decimal precision ----------------------------------------
    def test_sales_status_decimal_precision(self):
        # 1051 vs 1051.05 must be "Below target" (no int() truncation)
        self.assertEqual(kpi.sales_status(1051, Decimal("1051.05")),
                         "Below target")
        self.assertEqual(kpi.sales_status(1051, Decimal("1051.00")),
                         "Achieved")
        # A fractional-cent target is not truncated before comparison.
        self.assertEqual(kpi.sales_status(rm(10000), rm(10000.30)),
                         "Below target")

    # projection_state boundaries ------------------------------------------
    def test_projection_state_boundaries(self):
        self.assertEqual(kpi.projection_state(6, 31), "early_estimate")
        self.assertEqual(kpi.projection_state(7, 31), "projected")
        self.assertEqual(kpi.projection_state(31, 31), "actual")
        self.assertEqual(kpi.projection_state(5, 30), "early_estimate")
        self.assertEqual(kpi.projection_state(30, 30), "actual")

    # pages read engine values ----------------------------------------------
    def test_no_local_formulas_left(self):
        """Sales/Report/Manpower pages contain no leftover growth/gap/
        average arithmetic — engine values only."""
        import inspect
        import ducklab.ops_sales_state as sales_mod
        import ducklab.ops_manpower_state as mp_mod
        src_s = inspect.getsource(sales_mod)
        src_m = inspect.getsource(mp_mod)
        self.assertNotIn("_growth_status", src_s)
        self.assertNotIn("sales_mtd_cents / entered", src_s)
        self.assertNotIn("rec.sales_cents) / eff", src_m)

    def test_sales_projection_rounds_half_up_for_display(self):
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        st._set_money("month_end", Decimal("100.50"))
        self.assertEqual(st.sp_month_end_exact, "RM 1.01")
        st._set_money("month_end", Decimal("100.49"))
        self.assertEqual(st.sp_month_end_exact, "RM 1.00")

    def test_sales_gap_rounds_half_up_for_display(self):
        from types import SimpleNamespace
        from ducklab.ops_sales_state import SalesState
        sales = SimpleNamespace(gap_by_level={
            "level1": Decimal("-100.50"), "level2": Decimal("100.49")})
        self.assertEqual(SalesState._gap(sales, "level1"), "−RM 1.01")
        self.assertEqual(SalesState._gap(sales, "level2"), "RM 1.00")

    # projection label helper -------------------------------------------------
    def test_projection_style_labels(self):
        from ducklab import design as ds
        lbl, color, tag = ds.projection_style("projected", "Achieved")
        self.assertEqual((lbl, color, tag), ("Projected: Achieved", "amber", ""))
        lbl, color, tag = ds.projection_style("early_estimate", "Below target")
        self.assertEqual((lbl, color, tag),
                         ("Projected: Below target", "amber", "Early estimate"))
        lbl, color, tag = ds.projection_style("actual", "Achieved")
        self.assertEqual((lbl, color, tag), ("Achieved", "green", ""))
        lbl, color, tag = ds.projection_style("projected", "Awaiting data")
        self.assertEqual((lbl, color, tag), ("Awaiting data", "gray", ""))


if __name__ == "__main__":
    unittest.main()

class StatusColorMapTests(unittest.TestCase):
    def test_pages_define_no_local_status_maps(self):
        """No ops page/state module defines its own status->color map;
        everything goes through design.STATUS_COLOR / ds.status_color."""
        import re
        from pathlib import Path
        offenders = []
        for f in Path("ducklab").glob("ops_*_state.py"):
            src = f.read_text()
            for m in re.finditer(r"^(\w*COLOR\w*)\s*=\s*\{", src, re.M):
                offenders.append(f"{f.name}:{m.group(1)}")
        for f in Path("ducklab").glob("ops_*_pages.py"):
            src = f.read_text()
            for m in re.finditer(r"^(\w*COLOR\w*)\s*=\s*\{", src, re.M):
                offenders.append(f"{f.name}:{m.group(1)}")
        self.assertEqual(offenders, [])

    def test_every_kpi_status_has_a_color(self):
        from ducklab import design as ds
        statuses = {
            # sales
            "Achieved", "Below target", "Awaiting data", "No target",
            "Growth", "Decline", "No reference",
            # expenses
            "Within budget", "Over budget", "OVER BUDGET", "Watch",
            "No budget", "No payments yet",
            # expense lines / transactions
            "OK", "Incomplete", "Unknown category", "Missing paid date",
            "Check paid date", "Missing paid by", "Missing recorder",
            "Disputed", "Unverified", "Verified",
            "Paid", "Pending", "Cancelled",
            # manpower
            "Within target", "Failure: over target", "Over target",
            "Awaiting payroll", "Awaiting HC", "Normal", "HIGH TURNOVER",
            "Vacancy", "On budget", "Over budget (+1)", "Under budget",
            # actions + priorities
            "Open", "In progress", "Blocked", "Done",
            "Low", "Medium", "High", "Critical",
            # stock (kpi.stock_status_label) / day display strings
            "No sales recorded", "Factor not set",
            "Not reported", "Zero", "Loss",
            "Net negative", "No sales",
            "complete", "partial", "unreported",
            # misc
            "Restricted",
        }
        missing = [st for st in statuses if st not in ds.STATUS_COLOR]
        self.assertEqual(missing, [])

class ExpensesEmptyStateTests(unittest.TestCase):
    """Task 19c: each Expenses KPI card decides from its own data."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "e.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.month = "2026-10"
        self.app.configure_month(
            self.admin, self.sid, self.month, level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            turnover_threshold=0.05)
        self.cat = self.app.add_expense_category(self.admin, self.sid,
                                                 "Elec", sort_order=1)

    def tearDown(self):
        self._tmp.cleanup()

    def _cards(self):
        rep = self.app.preview_month(self.admin, self.sid, self.month,
                                     as_of=date(2026, 10, 21))
        e = rep.expenses
        return {
            "paid": "No payments yet" if e.paid_count == 0 else e.paid_cents,
            "incurred": ("Not reported yet" if e.incurred_count == 0
                         else e.incurred_cents),
            "open": e.open_cents,
        }

    def test_pending_lines_without_payments(self):
        for i, amt in enumerate((100, 250), start=1):
            self.app.record_expense(
                self.admin, self.sid, incurred_date=date(2026, 10, i),
                paid_date=None, category_id=self.cat, description=f"p{i}",
                amount_cents=rm(amt), paid_by=None, status="Pending",
                verification="Unverified", recorded_by="DEV",
                receipt_ref=f"R{i}")
        cards = self._cards()
        self.assertEqual(cards["paid"], "No payments yet")
        self.assertEqual(cards["incurred"], rm(350))   # amount shows
        self.assertEqual(cards["open"], rm(350))       # amount shows

    def test_all_empty_month(self):
        cards = self._cards()
        self.assertEqual(cards["paid"], "No payments yet")
        self.assertEqual(cards["incurred"], "Not reported yet")
        self.assertEqual(cards["open"], 0)             # genuine zero
