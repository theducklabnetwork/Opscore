"""Task 17 tests: reporting date, total-budget None, engine action counts
in reports, one action scope rule, is_overdue boundary/pillar, review-count
scope, shared status-color map coverage, stock sparkline = Total Loss per
day, month choices use kl_today. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_fixes.py -v
"""

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ducklab import design as ds
from ducklab import kpi
from ducklab.app_service import AppService

TODAY = date(2026, 10, 21)


def rm(v):
    return int(Decimal(str(v)) * 100)


class ReportingDateTests(unittest.TestCase):
    def test_clamp_past_current_future(self):
        today = date(2026, 10, 21)
        self.assertEqual(kpi.reporting_date("2026-09", today),
                         date(2026, 9, 30))     # past: month end
        self.assertEqual(kpi.reporting_date("2026-10", today),
                         today)                  # current: today
        self.assertEqual(kpi.reporting_date("2026-11", today),
                         date(2026, 11, 1))      # future: month start

    def test_month_choices_use_kl_today(self):
        import inspect
        from ducklab.ops_state import OpsState
        src = inspect.getsource(OpsState._month_choices)
        self.assertIn("kpi.kl_today()", src)
        self.assertNotIn("date.today()", src)


class ActionRuleTests(unittest.TestCase):
    def _action(self, raised, status="Open", due=None, pillar="General"):
        class A:  # minimal record shape
            def __init__(self):
                self.raised_date = raised
                self.input = kpi.ActionInput(status=status, due_date=due,
                                             pillar=pillar)
        return A()

    def test_scope_excludes_actions_raised_after_month(self):
        later = self._action(date(2026, 11, 2))     # raised after Oct
        in_m = self._action(date(2026, 10, 5))
        scoped = kpi.actions_in_month_scope([later, in_m], "2026-10")
        self.assertEqual([a.raised_date for a in scoped],
                         [date(2026, 10, 5)])

    def test_scope_carries_over_open_action(self):
        old_open = self._action(date(2026, 8, 12), status="Open")
        old_done = self._action(date(2026, 8, 12), status="Done")
        scoped = kpi.actions_in_month_scope([old_open, old_done], "2026-10")
        self.assertEqual(len(scoped), 1)
        self.assertEqual(scoped[0].input.status, "Open")

    def test_is_overdue_boundary_and_pillar(self):
        # due == as_of: NOT overdue
        a = kpi.ActionInput(status="Open", due_date=TODAY, pillar="General")
        self.assertFalse(kpi.is_overdue(a, TODAY))
        self.assertTrue(kpi.is_overdue(a, TODAY + timedelta(days=1)))
        # pillar required (workbook checks the PILLAR column)
        b = kpi.ActionInput(status="Open", due_date=TODAY - timedelta(days=1),
                            pillar=None)
        self.assertFalse(kpi.is_overdue(b, TODAY))
        # Done/Cancelled never overdue
        c = kpi.ActionInput(status="Done",
                            due_date=TODAY - timedelta(days=5),
                            pillar="General")
        self.assertFalse(kpi.is_overdue(c, TODAY))


class EngineFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "f.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.month = "2026-10"
        self.app.configure_month(
            self.admin, self.sa, self.month, level1_base_cents=rm(200000),
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            turnover_threshold=0.05,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))

    def tearDown(self):
        self._tmp.cleanup()

    # 4 ------------------------------------------------------------------
    def test_total_budget_none_when_all_null(self):
        self.app.add_expense_category(self.admin, self.sa, "Electricity",
                                      sort_order=1)  # no budget set
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              sales_cents=rm(100))
        # pay something so the count-based "No payments yet" rule passes,
        # then the total budget must be None -> "No budget" (not Over)
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 2),
            paid_date=date(2026, 10, 3), category_id=self.app.get_expense_categories(self.admin, self.sa)[0].id,
            description="paid", amount_cents=rm(10), paid_by="Petty Cash",
            status="Paid", verification="Verified", recorded_by="DEV",
            receipt_ref="R0")
        rep = self.app.preview_month(self.admin, self.sa, self.month,
                                     as_of=TODAY)
        self.assertIsNone(rep.expenses.total_budget_cents)
        self.assertEqual(rep.expenses.total_expenses_status, "No budget")

    # 5 ------------------------------------------------------------------
    def test_report_action_counts_equal_engine(self):
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              sales_cents=rm(100))
        self.app.save_action(
            self.admin, self.sa, None, TODAY, raised_date=TODAY,
            pillar="General Operations", issue="x", priority="High",
            status="Open", pic="DEV", due_date=TODAY - timedelta(days=1),
            corrective_action=None, completion_date=None, remarks=None)
        rep = self.app.preview_month(self.admin, self.sa, self.month,
                                     as_of=TODAY)
        from ducklab.ops_state import _serialize_live, _serialize_snapshot
        live = _serialize_live(rep, user=self.admin)
        self.assertEqual(live["actions"]["open"], rep.actions.open)
        engine_total = sum(v for k, v in rep.actions.counts.items()
                           if k != "overdue")
        self.assertEqual(live["actions"]["total"], engine_total)
        self.app.finalize_month(self.admin, self.sa, self.month, as_of=TODAY)
        snap = self.app.get_finalized_report(self.admin, self.sa, self.month)
        out = _serialize_snapshot(snap, user=self.admin)
        self.assertEqual(out["actions"]["open"],
                         snap["report"]["actions"]["open"])

    # 6 ------------------------------------------------------------------
    def test_review_count_scope_excludes_other_month(self):
        cat = self.app.add_expense_category(self.admin, self.sa, "Elec",
                                            sort_order=1)
        # September line fully closed in September (paid in Sept, but
        # Unverified -> would be a review line if counted): NOT in
        # October's scope (neither incurred nor paid in October, not a
        # carried pending line).
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 9, 5),
            paid_date=date(2026, 9, 6), category_id=cat, description="old",
            amount_cents=rm(10), paid_by="Petty Cash", status="Paid",
            verification="Unverified", recorded_by="DEV", receipt_ref="R1")
        # October pending line: IS in scope
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 5),
            paid_date=None, category_id=cat, description="new",
            amount_cents=rm(20), paid_by=None, status="Pending",
            verification="Unverified", recorded_by="DEV", receipt_ref="R2")
        rep = self.app.preview_month(self.admin, self.sa, self.month,
                                     as_of=TODAY)
        review = sum(v for k, v in rep.expenses.validation_counts.items()
                     if k != "OK")
        self.assertEqual(review, 1)
        # September's report includes the old line
        self.app.configure_month(
            self.admin, self.sa, "2026-09", level1_base_cents=rm(200000),
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            turnover_threshold=0.05,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
        rep9 = self.app.preview_month(self.admin, self.sa, "2026-09",
                                      as_of=date(2026, 9, 30))
        review9 = sum(v for k, v in rep9.expenses.validation_counts.items()
                      if k != "OK")
        self.assertEqual(review9, 1)

    # 7 ------------------------------------------------------------------
    def test_status_color_map_covers_every_kpi_status(self):
        statuses = {
            "Achieved", "Below target", "Awaiting data", "No target",
            "Within budget", "Over budget", "OVER BUDGET", "Watch",
            "No budget", "No payments yet",
            "Within target", "Failure: over target", "Over target",
            "Awaiting payroll", "Awaiting HC", "Normal", "HIGH TURNOVER",
            "On budget", "Over budget (+1)", "Under budget",
            "OK", "Incomplete", "Unknown category", "Missing paid date",
            "Check paid date", "Missing paid by", "Missing recorder",
            "Disputed", "Unverified", "Paid", "Pending", "Cancelled",
            "Open", "In progress", "Blocked", "Done", "Restricted",
        }
        missing = [s for s in statuses if s not in ds.STATUS_COLOR]
        self.assertEqual(missing, [])

    # 8 ------------------------------------------------------------------
    def test_sparkline_equals_total_loss_per_day(self):
        signs = self.app.read_repo.get_contribution_signs()
        # DMO (+) 100, EXTRA (-) 30 on day 1 -> total loss 70 (signed)
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              sales_cents=rm(1000),
                              stock_losses={"DMO": rm(100), "EXTRA": rm(30)})
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 2),
                              sales_cents=rm(1000),
                              stock_losses={"DMP": rm(50)})
        import ducklab.ops_overview_state as mod
        from ducklab import auth_pages
        orig_app, orig_mod = auth_pages.app_service, mod.app_service
        auth_pages.app_service = mod.app_service = self.app
        try:
            ov = mod.compute_overview(self.admin, self.sa, self.month, TODAY)
        finally:
            auth_pages.app_service = orig_app
            mod.app_service = orig_mod
        days = self.app.get_daily_records(self.admin, self.sa, self.month)
        expected = [kpi.total_loss_cents(d.stock_losses, signs) / 100
                    if d.stock_losses else None for d in days]
        got = ov["spark_stock_dark"].data[0].y
        self.assertAlmostEqual(got[0], expected[0])
        self.assertAlmostEqual(got[1], expected[1])
        self.assertEqual(list(got[2:]), [None] * 29)


if __name__ == "__main__":
    unittest.main()
