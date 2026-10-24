"""Task 22a tests: remaining page/state logic moved into kpi.py (stock
status, budget utilization/variance, ratio-of-target, pace series,
vs-run-rate, reported totals), exact Overview projection display, Report
card labels and part-timer factor label, the missing part-timer factor
decision, the pending-expense month-end cut-off, budget "clear" + amount
conflicts, and the dev cleanup symlink refusal. Expected values are
hand-computed literals. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_task22a.py -v
"""

import importlib.util
import os
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab import kpi
from ducklab.app_service import AppService

TODAY = date(2026, 10, 21)
ROOT = Path(__file__).resolve().parent.parent
CLEAR_CONFLICT = "Enter an amount, or use × to clear"


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t22a.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")

    def tearDown(self):
        self._tmp.cleanup()

    def preview(self, month="2026-10"):
        return self.app.preview_month(self.admin, self.sid, month,
                                      as_of=TODAY)

    def bind(self, module, state_cls, month="2026-10"):
        """Drive a page state's handlers against this disposable DB."""
        sid, admin = self.sid, self.admin
        return [
            mock.patch.object(module, "app_service", self.app),
            mock.patch.object(state_cls, "_actor", lambda self: admin),
            mock.patch.object(state_cls, "_selected_store_id",
                              lambda self: sid),
            mock.patch.object(state_cls, "_selected_month",
                              lambda self: month),
        ]

    def run_bound(self, patches, fn):
        for p in patches:
            p.start()
        try:
            return fn()
        finally:
            for p in reversed(patches):
                p.stop()


# 1 ---------------------------------------------------------------------------
class KpiHelperTests(unittest.TestCase):
    def test_budget_utilization_and_variance(self):
        self.assertEqual(kpi.budget_utilization(320000, 400000),
                         Decimal("0.8"))
        self.assertIsNone(kpi.budget_utilization(100, None))   # no budget
        self.assertIsNone(kpi.budget_utilization(100, 0))      # zero budget
        self.assertEqual(kpi.budget_variance_cents(400000, 320000), 80000)
        self.assertEqual(kpi.budget_variance_cents(100, 150), -50)
        self.assertEqual(kpi.budget_variance_cents(0, 0), 0)
        self.assertIsNone(kpi.budget_variance_cents(None, 150))

    def test_ratio_of_target(self):
        self.assertEqual(kpi.ratio_of_target(Decimal("0.05"),
                                             Decimal("0.0625")),
                         Decimal("0.8"))
        self.assertEqual(kpi.ratio_of_target(0.03, 0.015), Decimal(2))
        self.assertIsNone(kpi.ratio_of_target(None, Decimal("0.1")))
        self.assertIsNone(kpi.ratio_of_target(Decimal("0.1"), None))
        self.assertIsNone(kpi.ratio_of_target(Decimal("0.1"), 0))

    def test_salary_budget_variance_exact(self):
        # budget 1,200,000.5 cents - salary 1,100,000 = 100,000.5 (no int())
        self.assertEqual(
            kpi.salary_budget_variance_cents(Decimal("1200000.5"), 1100000),
            Decimal("100000.5"))
        self.assertIsNone(kpi.salary_budget_variance_cents(None, 1))
        self.assertIsNone(kpi.salary_budget_variance_cents(Decimal(1), None))

    def test_daily_vs_run_rate(self):
        # RM 7,000 day vs RM 200,000 / 31 = 645,161.29... cents per day
        self.assertEqual(
            kpi.daily_vs_run_rate_cents(
                700000, Decimal("645161.2903225806451612903226")),
            Decimal("54838.7096774193548387096774"))
        self.assertIsNone(kpi.daily_vs_run_rate_cents(None, Decimal(1)))
        self.assertIsNone(kpi.daily_vs_run_rate_cents(100, None))

    def test_reported_total(self):
        self.assertIsNone(kpi.reported_total([None, None]))
        self.assertIsNone(kpi.reported_total([]))
        self.assertEqual(kpi.reported_total([None, 3, 0]), 3)
        self.assertEqual(kpi.reported_total([0]), 0)   # reported zero

    def test_pace_series(self):
        days = [kpi.DailyInput(date(2026, 10, 1), sales_cents=100000),
                kpi.DailyInput(date(2026, 10, 2), ft_working=3),  # no sales
                kpi.DailyInput(date(2026, 10, 3), sales_cents=50000)]
        # Level 1 RM 10,000 over 4 days = 250,000 cents per day
        self.assertEqual(kpi.pace_series(days, 4, 1000000), [
            {"day": 1, "actual": 100000, "budget": 250000},
            {"day": 2, "actual": 100000, "budget": 500000},
            {"day": 3, "actual": 150000, "budget": 750000},
            {"day": 4, "actual": None, "budget": 1000000},
        ])
        # 1,000 cents / 3 days: 333.33 -> 333, 666.67 -> 667 (half-up)
        self.assertEqual(
            [p["budget"] for p in kpi.pace_series([], 3, 1000)],
            [333, 667, 1000])
        self.assertEqual(kpi.pace_series(days, 2, None), [
            {"day": 1, "actual": 100000, "budget": None},
            {"day": 2, "actual": 100000, "budget": None},
        ])

    def test_stock_status_label_ladder(self):
        target = Decimal("0.015")
        # 200 / 2,000 = 10.0% > 1.5%
        self.assertEqual(kpi.stock_status_label(20000, 200000, target, 20),
                         "Over target")
        # 20 / 2,000 = 1.0% <= 1.5%
        self.assertEqual(kpi.stock_status_label(2000, 200000, target, 20),
                         "Within target")
        self.assertEqual(kpi.stock_status_label(0, 0, target, 0),
                         "Awaiting data")
        self.assertEqual(kpi.stock_status_label(500, 0, target, 0),
                         "No sales recorded")
        self.assertEqual(kpi.stock_status_label(0, 0, target, 2),
                         "No sales recorded")
        self.assertEqual(kpi.stock_status_label(500, 200000, None, 3),
                         "No target")
        # the workbook status itself is unchanged (Summary!E63 parity)
        self.assertEqual(kpi.stock_loss_status(500, 200000, None), "")

    def test_projection_and_factor_labels(self):
        self.assertEqual(ds.projection_label("actual", "Growth vs Last Year"),
                         "Growth vs Last Year")
        self.assertEqual(ds.projection_label("projected", "Growth vs Last Year"),
                         "Growth vs Last Year (projected)")
        self.assertEqual(
            ds.projection_label("early_estimate", "Achievement vs Target"),
            "Achievement vs Target (early estimate)")
        self.assertEqual(ds.pt_factor_label(Decimal("0.5")), "FT + 0.5 × PT")
        self.assertEqual(ds.pt_factor_label(0.75), "FT + 0.75 × PT")
        self.assertEqual(ds.pt_factor_label(None), "Factor not set")


# 2 ---------------------------------------------------------------------------
class PartTimerFactorTests(Fixture):
    def test_engine_without_factor(self):
        # no part-timers: factor not needed -> FT only
        self.assertEqual(kpi.effective_staff(7, 0, None), Decimal(7))
        self.assertEqual(kpi.effective_staff(7, None, None), Decimal(7))
        # part-timers worked, factor NULL -> None (never FT-only)
        self.assertIsNone(kpi.effective_staff(7, 2, None))
        self.assertEqual(kpi.effective_staff(7, 2, Decimal("0.5")),
                         Decimal(8))
        ft_only = [kpi.DailyInput(date(2026, 10, 1), sales_cents=70000,
                                  ft_working=6, pt_working=0),
                   kpi.DailyInput(date(2026, 10, 2), sales_cents=70000,
                                  ft_working=8)]
        self.assertFalse(kpi.pt_factor_missing(ft_only, None))
        self.assertEqual(kpi.average_effective_staff(ft_only, None),
                         Decimal(7))                 # (6 + 8) / 2
        self.assertEqual(kpi.sales_per_staff_cents(ft_only, None),
                         Decimal(10000))             # 140,000 / 14
        with_pt = ft_only + [kpi.DailyInput(date(2026, 10, 3),
                                            sales_cents=70000,
                                            ft_working=6, pt_working=2)]
        self.assertTrue(kpi.pt_factor_missing(with_pt, None))
        self.assertFalse(kpi.pt_factor_missing(with_pt, Decimal("0.5")))
        self.assertIsNone(kpi.average_effective_staff(with_pt, None))
        self.assertIsNone(kpi.sales_per_staff_cents(with_pt, None))

    def _month(self, factor, pt):
        fields = {"level1_base_cents": rm(100000), "opening_headcount_ft": 9, "level2_pct": 0.1, "level3_pct": 0.2, "level4_pct": 0.3}
        if factor is not None:
            fields["part_timer_factor"] = factor
        self.app.configure_month(self.admin, self.sid, "2026-10", **fields)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000), ft_working=6,
                              pt_working=pt)

    def test_report_factor_not_set(self):
        from ducklab.ops_state import _serialize_live
        self._month(None, 2)
        rep = self.preview()
        self.assertTrue(rep.manpower.pt_factor_missing)
        self.assertIsNone(rep.manpower.average_effective_staff)
        self.assertIsNone(rep.manpower.sales_per_staff)
        mp = _serialize_live(rep, user=self.admin)["manpower"]
        self.assertEqual(mp["effective_staff"], "Factor not set")
        self.assertEqual(mp["pt_factor_label"], "Factor not set")

    def test_report_no_part_timers_needs_no_factor(self):
        from ducklab.ops_state import _serialize_live
        self._month(None, 0)
        rep = self.preview()
        self.assertFalse(rep.manpower.pt_factor_missing)
        self.assertEqual(rep.manpower.average_effective_staff, Decimal(6))
        mp = _serialize_live(rep, user=self.admin)["manpower"]
        self.assertEqual(mp["effective_staff"], "6.00")
        self.assertEqual(mp["pt_factor_label"], "Factor not set")

    def test_report_configured_factor_label(self):
        from ducklab.ops_state import _serialize_live
        self._month(0.5, 2)
        mp = _serialize_live(self.preview(), user=self.admin)["manpower"]
        self.assertEqual(mp["effective_staff"], "7.00")   # 6 + 2 x 0.5
        self.assertEqual(mp["pt_factor_label"], "FT + 0.5 × PT")

    def test_finalized_report_factor_not_set(self):
        from ducklab.ops_state import _serialize_snapshot
        self._month(None, 2)
        self.app.finalize_month(self.admin, self.sid, "2026-10",
                                as_of=TODAY)
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-10")
        mp = _serialize_snapshot(snap, user=self.admin)["manpower"]
        self.assertEqual(mp["effective_staff"], "Factor not set")
        self.assertEqual(mp["pt_factor_label"], "Factor not set")

    def test_manpower_page(self):
        import ducklab.ops_manpower_state as mod
        from ducklab.ops_manpower_state import ManpowerState
        self._month(None, 2)
        st = ManpowerState()
        self.run_bound(self.bind(mod, ManpowerState),
                       st.load_manpower_data)
        self.assertEqual(st.eff_kpis["avg_effective"], "Factor not set")
        self.assertEqual(st.eff_kpis["sales_per_staff"], "Factor not set")
        self.assertEqual(st.staff_rows[0]["effective"], "Factor not set")
        self.assertEqual(st.staff_rows[0]["sales_per_staff"],
                         "Factor not set")
        self.assertEqual(st.staff_rows[1]["effective"], "—")  # unreported

    def test_manpower_page_without_part_timers(self):
        import ducklab.ops_manpower_state as mod
        from ducklab.ops_manpower_state import ManpowerState
        self._month(None, 0)
        st = ManpowerState()
        self.run_bound(self.bind(mod, ManpowerState),
                       st.load_manpower_data)
        self.assertEqual(st.eff_kpis["avg_effective"], "6.00")
        # RM 1,000 / 6 staff = RM 166.666... -> RM 166.66
        self.assertEqual(st.eff_kpis["sales_per_staff"], "RM 166.66")
        self.assertEqual(st.staff_rows[0]["effective"], "6.00")

    def test_daily_rows_and_form(self):
        from ducklab.ops_daily_state import DailyState
        self._month(None, 2)
        st = DailyState()
        rec = self.app.get_daily_records(self.admin, self.sid, "2026-10")[0]
        row = st._day_row(rec.date, rec, {}, None)
        self.assertEqual(row["staff"], "Factor not set")
        self.assertEqual(row["sales_per_staff"], "Factor not set")
        st.pt_factor = None
        st.f_ft_working, st.f_pt_working = "6", "2"
        self.assertEqual(st.form_effective_staff, "Factor not set")
        st.f_pt_working = "0"
        self.assertEqual(st.form_effective_staff, "6.00")


# 3 ---------------------------------------------------------------------------
class StockStatusEverywhereTests(Fixture):
    """One stock status (kpi.stock_status_label) on Stock, Report and
    Overview."""

    def _config(self, target=0.015):
        fields = {"level1_base_cents": rm(100000), "level2_pct": 0.1, "level3_pct": 0.2, "level4_pct": 0.3}
        if target is not None:
            fields["stock_loss_target"] = target
        self.app.configure_month(self.admin, self.sid, "2026-10", **fields)

    def _all_three(self):
        import ducklab.ops_overview_state as ov_mod
        import ducklab.ops_stock_state as stock_mod
        from ducklab.ops_overview_state import compute_overview
        from ducklab.ops_state import _serialize_live
        from ducklab.ops_stock_state import StockState
        st = StockState()
        self.run_bound(self.bind(stock_mod, StockState), st.load_stock_data)
        report = _serialize_live(self.preview(), user=self.admin)
        with mock.patch.object(ov_mod, "app_service", self.app):
            ov = compute_overview(self.admin, self.sid, "2026-10", TODAY)
        return st.st_status, report["stock"]["status"], ov["pill_stock"]["status"]

    def test_awaiting_data(self):
        self._config()
        self.assertEqual(self._all_three(), ("Awaiting data",) * 3)

    def test_no_sales_recorded(self):
        self._config()
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              stock_losses={"DMO": rm(5)})
        self.assertEqual(self._all_three(), ("No sales recorded",) * 3)

    def test_no_target(self):
        self._config(target=None)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000),
                              stock_losses={"DMO": rm(5)})
        self.assertEqual(self._all_three(), ("No target",) * 3)

    def test_within_and_over_target(self):
        self._config()
        # RM 10 / RM 1,000 = 1.0% <= 1.5%
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000),
                              stock_losses={"DMO": rm(10)})
        self.assertEqual(self._all_three(), ("Within target",) * 3)
        # RM 20 / RM 1,000 = 2.0% > 1.5%
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              stock_losses={"DMO": rm(20)})
        self.assertEqual(self._all_three(), ("Over target",) * 3)

    def test_finalized_report_status(self):
        from ducklab.ops_state import _serialize_snapshot
        self._config(target=None)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000))
        self.app.finalize_month(self.admin, self.sid, "2026-10",
                                as_of=TODAY)
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-10")
        self.assertEqual(
            _serialize_snapshot(snap, user=self.admin)["stock"]["status"],
            "No target")


# 4 ---------------------------------------------------------------------------
class OverviewProjectionTests(Fixture):
    def setUp(self):
        super().setUp()
        # Oct: 7 of 31 days, MTD 700,002 cents. Projection 700,002 / 7 x 31
        # = 3,100,008.857... cents; Level 2 = 2,480,007 x 1.25 =
        # 3,100,008.75 -> Achieved (int() truncation would say 99.99998%).
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=2480007,
            level2_pct=0.25, level3_pct=0.5, level4_pct=0.75)
        for d in range(1, 7):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=100000)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 7),
                              sales_cents=100002)

    def _overview(self):
        import ducklab.ops_overview_state as ov_mod
        from ducklab.ops_overview_state import compute_overview
        with mock.patch.object(ov_mod, "app_service", self.app):
            return compute_overview(self.admin, self.sid, "2026-10", TODAY)

    def test_no_truncation(self):
        ov = self._overview()
        level2 = ov["targets"][1]
        self.assertEqual(level2["label"], "Level 2")
        self.assertEqual(level2["status"], "Achieved")
        self.assertEqual(level2["progress"], 1.0)
        self.assertEqual(level2["pct"], "100.0%")
        # Level 1: 3,100,008.857 / 2,480,007 = 125.0000043%
        self.assertEqual(ov["cc"]["proj_achievement"], "125.0%")
        self.assertEqual(ov["pill_sales"]["over_label"], "+25%")
        self.assertEqual(ov["pill_sales"]["fill_pct"], 1.0)
        # MTD 700,002 / 2,480,007 = 28.2258%
        self.assertAlmostEqual(ov["pill_sales"]["mtd_pct"],
                               0.28225807427156455, places=15)

    def test_pace_series_shared_with_sales(self):
        from ducklab.ops_sales_state import SalesState
        days = self.app.get_daily_records(self.admin, self.sid, "2026-10")
        # run-rate 2,480,007 / 31 = 80,000.2258 cents/day
        pace = SalesState._pace_chart(None, days, 31, 2480007)
        self.assertEqual(pace[0], {"day": 1, "actual": 100000,
                                   "budget": 80000})
        self.assertEqual(pace[6], {"day": 7, "actual": 700002,
                                   "budget": 560002})  # 560,001.58 -> up
        self.assertEqual(pace[7], {"day": 8, "actual": None,
                                   "budget": 640002})  # 640,001.81
        ov = self._overview()
        self.assertTrue(ov["pace_has_data"])
        budget_trace, actual_trace = ov["pace_fig_dark"].data[:2]
        self.assertEqual(budget_trace.y[0], 800.0)      # RM, cents / 100
        self.assertEqual(actual_trace.y[6], 7000.02)
        self.assertIsNone(actual_trace.y[7])


# 5 ---------------------------------------------------------------------------
class ReportCardLabelTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(100000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)

    def _sales(self):
        from ducklab.ops_state import _serialize_live
        return _serialize_live(self.preview(), user=self.admin)["sales"]

    def _labels(self, sales):
        return (sales["achievement_label"], sales["growth_year_label"],
                sales["growth_month_label"], sales["early_estimate_tag"])

    def test_early_estimate(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000))
        self.assertEqual(self._labels(self._sales()), (
            "Achievement vs Target (early estimate)",
            "Growth vs Last Year (early estimate)",
            "Growth vs Last Month (early estimate)",
            "Early estimate: 1 of 31 days"))

    def test_projected(self):
        for d in range(1, 8):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(1000))
        self.assertEqual(self._labels(self._sales()), (
            "Achievement vs Target (projected)",
            "Growth vs Last Year (projected)",
            "Growth vs Last Month (projected)", ""))

    def test_actual_complete_month(self):
        for d in range(1, 32):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(1000))
        self.assertEqual(self._labels(self._sales()), (
            "Achievement vs Target", "Growth vs Last Year",
            "Growth vs Last Month", ""))

    def test_finalized_labels(self):
        from ducklab.ops_state import _serialize_snapshot
        for d in range(1, 8):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(1000))
        self.app.finalize_month(self.admin, self.sid, "2026-10",
                                as_of=TODAY)
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-10")
        sales = _serialize_snapshot(snap, user=self.admin)["sales"]
        self.assertEqual(self._labels(sales), (
            "Achievement vs Target (projected)",
            "Growth vs Last Year (projected)",
            "Growth vs Last Month (projected)", ""))


# 6 ---------------------------------------------------------------------------
class DailyMonthTotalsTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(100000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)

    def _kpis(self):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        days = self.app.get_daily_records(self.admin, self.sid, "2026-10")
        return self.run_bound(self.bind(mod, DailyState),
                              lambda: st._monthly_kpis(days))

    def test_dash_when_never_reported(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000))
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 2),
                              sales_cents=rm(500))
        k = self._kpis()
        self.assertEqual((k["transactions"], k["units"]), ("—", "—"))
        self.assertEqual(k["mtd"], "RM 1,500.00")

    def test_reported_values_and_zero(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000), transactions=5, units=0)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 2),
                              sales_cents=rm(500))
        k = self._kpis()
        self.assertEqual((k["transactions"], k["units"]), ("5", "0"))


# 7 ---------------------------------------------------------------------------
class PendingCutOffTests(unittest.TestCase):
    def test_pending_counted_from_incurred_month(self):
        line = kpi.ExpenseInput(
            incurred_date=date(2026, 9, 15), paid_date=None, category_id=1,
            description="pending", amount_cents=4000, paid_by=None,
            status="Pending", verification="Unverified", recorded_by="M",
            store_id=1)
        undated = kpi.ExpenseInput(
            incurred_date=None, paid_date=None, category_id=1,
            description="no date", amount_cents=999, paid_by=None,
            status="Pending", verification=None, recorded_by="M",
            store_id=1)
        exps = [line, undated]
        self.assertEqual(kpi.expense_totals_cents(exps, "2026-08", 1)["open"],
                         0)
        self.assertEqual(kpi.expense_totals_cents(exps, "2026-09", 1)["open"],
                         4000)
        self.assertEqual(kpi.expense_totals_cents(exps, "2026-12", 1)["open"],
                         4000)
        self.assertEqual(kpi.pending_count(exps, "2026-08", 1), 0)
        self.assertEqual(kpi.pending_count(exps, "2026-09", 1), 1)


# 8 ---------------------------------------------------------------------------
class BudgetClearConflictTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(1000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        self.cat = self.app.add_expense_category(self.admin, self.sid,
                                                 "Elec", sort_order=1)
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat, rm(500))

    def budgets(self):
        return self.app.get_expense_budgets(self.admin, self.sid, "2026-10")

    def test_monthly_setup_rows(self):
        from ducklab.ops_stores_state import _parse_budget_rows
        row = {"id": str(self.cat), "name": "Elec", "value": "600.00",
               "original": "500.00", "clear": "1"}
        self.assertEqual(_parse_budget_rows([row]), ({}, [
            "Budget for Elec: Enter an amount, or use × to clear"]))
        # ticked, amount left as saved -> clear (NULL)
        row["value"] = "500.00"
        self.assertEqual(_parse_budget_rows([row]), ({self.cat: None}, []))
        # ticked, amount blanked -> clear (NULL)
        row["value"] = ""
        self.assertEqual(_parse_budget_rows([row]), ({self.cat: None}, []))

    def test_monthly_setup_save_writes_nothing(self):
        import ducklab.ops_stores_state as mod
        from ducklab.ops_stores_state import StoresState
        st = StoresState()
        st.budget_rows = [{"id": str(self.cat), "name": "Elec",
                           "value": "600.00", "original": "500.00",
                           "clear": "1"}]
        st.setup_original = {}
        self.run_bound(self.bind(mod, StoresState),
                       lambda: list(st.save_setup()))
        self.assertEqual(
            st.setup_error,
            "Budget for Elec: Enter an amount, or use × to clear")
        self.assertEqual(self.budgets(), {self.cat: 50000})   # unchanged

    def test_expenses_page_save_writes_nothing(self):
        import ducklab.ops_expenses_state as mod
        from ducklab.ops_expenses_state import ExpensesState
        st = ExpensesState()
        st.b_category, st.b_amount, st.b_clear = "Elec", "4000.00", True
        self.run_bound(self.bind(mod, ExpensesState),
                       lambda: list(st.save_budget()))
        self.assertEqual(st.b_error, CLEAR_CONFLICT)
        self.assertEqual(self.budgets(), {self.cat: 50000})   # unchanged


# 9 ---------------------------------------------------------------------------
class DevCleanupSymlinkTests(unittest.TestCase):
    def setUp(self):
        spec = importlib.util.spec_from_file_location(
            "dev_cleanup", ROOT / "scripts" / "dev_cleanup.py")
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.real = tmp / "other.db"
        conn = sqlite3.connect(self.real)
        conn.execute("CREATE TABLE stores (id INTEGER, code TEXT)")
        conn.execute("INSERT INTO stores VALUES (1, 'QA1')")
        conn.commit()
        conn.close()
        self.link = tmp / "dev.db"
        os.symlink(self.real, self.link)

    def tearDown(self):
        self._tmp.cleanup()

    def test_symlinked_dev_db_refused(self):
        with mock.patch.object(self.mod, "DEV_DB", self.link):
            with self.assertRaises(ValueError) as ctx:
                self.mod.resolve_target(self.link)
            self.assertEqual(str(ctx.exception),
                             f"Refusing {self.link}: dev.db is a symlink")
            self.assertEqual(
                self.mod.main(["--db", str(self.link), "--apply"]), 2)
        conn = sqlite3.connect(self.real)
        try:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM stores").fetchone()[0], 1)
        finally:
            conn.close()

    def test_regular_dev_db_still_accepted(self):
        with mock.patch.object(self.mod, "DEV_DB", self.real):
            self.assertEqual(self.mod.resolve_target(self.real),
                             self.real.resolve())


if __name__ == "__main__":
    unittest.main()
