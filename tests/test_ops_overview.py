"""Tests for the /ops overview: values pass through assemble_month,
statuses unchanged, payroll restriction, needs-attention flags,
in-progress unreported-days, multi-store isolation, auth, empty state.
Disposable temp DBs only.

Run: python -m pytest tests/test_ops_overview.py -v
"""

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from ducklab import design as ds
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.ops_overview_state import compute_overview

TODAY = date(2026, 10, 21)  # injected


def rm(v):
    return int(Decimal(str(v)) * 100)


class OverviewFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ov.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.sb = self.app.register_store(self.admin, "B02", "Beta")
        self.month = "2026-10"
        for sid in (self.sa, self.sb):
            self.app.configure_month(
                self.admin, sid, self.month, level1_base_cents=rm(200000),
                opening_headcount_ft=9, stock_loss_target=0.015,
                salary_ratio_target=0.06818, part_timer_factor=0.5,
                turnover_threshold=0.05,
                salary_basic_allowance_cents=rm(10000),
                overtime_cents=rm(1000))
        self.cat = self.app.add_expense_category(
            self.admin, self.sa, "Electricity", sort_order=1)
        self.app.set_expense_budget(self.admin, self.sa, self.month,
                                    self.cat, rm(100))  # tiny -> over budget
        # sales below target + expenses over budget + review line + overdue
        # action + high turnover + stock over target = all flags on A01
        for d in range(1, TODAY.day):  # partial month (in progress)
            self.app.record_daily(
                self.admin, self.sa, date(2026, 10, d),
                sales_cents=rm(100), stock_losses={"DMO": rm(10)})
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 2),
                              resigned_ft=5)  # turnover 5/9 = 56% > threshold
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 2),
            paid_date=date(2026, 10, 3), category_id=self.cat,
            description="Bill", amount_cents=rm(150), paid_by="Petty Cash",
            status="Paid", verification="Verified", recorded_by="DEV",
            receipt_ref="R1")
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 2),
            paid_date=None, category_id=self.cat,
            description="No payer", amount_cents=rm(20),
            paid_by=None, status="Pending", verification="Unverified",
            recorded_by="DEV", receipt_ref="R2")  # review line
        self.app.save_action(
            self.admin, self.sa, None, TODAY,
            raised_date=TODAY - timedelta(days=4),
            pillar="General Operations", issue="Overdue thing",
            priority="High", status="Open", pic="DEV",
            due_date=TODAY - timedelta(days=1), corrective_action=None,
            completion_date=None, remarks=None)

    def tearDown(self):
        self._tmp.cleanup()

    def overview(self, user=None, store=None, month=None):
        # compute_overview uses the module-level app_service bound to the
        # app's default DB; bind it to the disposable DB for this call.
        import ducklab.ops_overview_state as mod
        from ducklab import auth_pages
        orig_app, orig_mod = auth_pages.app_service, mod.app_service
        auth_pages.app_service = mod.app_service = self.app
        try:
            return compute_overview(user or self.admin, store or self.sa,
                                    month or self.month, TODAY)
        finally:
            auth_pages.app_service = orig_app
            mod.app_service = orig_mod

    # 1 ------------------------------------------------------------------
    def test_values_equal_assemble_month(self):
        ov = self.overview()
        rep = self.app.preview_month(self.admin, self.sa, self.month,
                                     as_of=TODAY)
        self.assertEqual(ov["cc"]["sales_mtd"],
                         ds.fmt_rm(rep.sales.sales_mtd_cents))
        self.assertEqual(ov["cc"]["paid"], ds.fmt_rm(rep.expenses.paid_cents))
        self.assertEqual(ov["cc"]["stock_loss"],
                         ds.fmt_rm(rep.stock.total_loss_cents))
        self.assertEqual(ov["cc"]["days"],
                         f"{rep.sales.days_entered} / "
                         f"{rep.sales.days_in_month}")
        self.assertEqual(ov["cc"]["actions_overdue"], "1")

    # 2 ------------------------------------------------------------------
    def test_statuses_passed_through_unchanged(self):
        ov = self.overview()
        rep = self.app.preview_month(self.admin, self.sa, self.month,
                                     as_of=TODAY)
        self.assertEqual(ov["pill_sales"]["status"],
                         rep.sales.status_by_level["level1"])
        self.assertEqual(ov["pill_stock"]["status"], rep.stock.status)
        self.assertIn(rep.manpower.turnover_status,
                      ov["pill_manpower"]["status"])

    # 3 ------------------------------------------------------------------
    def test_payroll_hidden_without_permission(self):
        viewer = self.app.create_user(self.admin, "v@ducklab.com", "V",
                                      "ViewerPass123!")
        self.app.assign_role(self.admin, viewer.id, "viewer")
        self.app.assign_store(self.admin, viewer.id, self.sa)
        viewer = self.app.auth.get_user(viewer.id)  # re-fetch assignments
        ov = self.overview(user=viewer)
        self.assertEqual(ov["pill_manpower"]["value"], "Restricted")
        self.assertIn("Restricted", ov["pill_manpower"]["status"])
        # serialized result carries no salary figures
        import json
        dumped = json.dumps({k: v for k, v in ov.items()
                             if k in ("pill_manpower", "cc")}, default=str)
        rep = self.app.preview_month(self.admin, self.sa, self.month,
                                     as_of=TODAY)
        if rep.manpower.salary_ratio_incl_support is not None:
            self.assertNotIn(
                f"{float(rep.manpower.salary_ratio_incl_support) * 100:.2f}%",
                dumped)
        self.assertNotIn("100,000", dumped)  # salary total never leaks

    # 4 ------------------------------------------------------------------
    def test_needs_attention_flags_crafted_month(self):
        ov = self.overview()
        joined = " | ".join(i["text"] for i in ov["attention"])
        self.assertIn("OVER BUDGET", joined)
        self.assertIn("need", joined)             # review lines
        self.assertIn("overdue action", joined)
        self.assertIn("HIGH TURNOVER", joined)
        self.assertIn("Stock loss over target", joined)
        self.assertIn("unreported day", joined)   # in-progress month
        self.assertGreaterEqual(len(ov["attention"]), 6)

    # 5 ------------------------------------------------------------------
    def test_clean_month_reports_nothing(self):
        self.app.configure_month(
            self.admin, self.sb, "2026-09", level1_base_cents=rm(200000),
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
        for d in range(1, 31):  # complete, on-target September for B02
            self.app.record_daily(self.admin, self.sb, date(2026, 9, d),
                                  sales_cents=rm(20000))
        ov = self.overview(store=self.sb, month="2026-09")
        self.assertEqual(ov["attention"], [])

    # 6 ------------------------------------------------------------------
    def test_unreported_days_only_for_in_progress_month(self):
        self.app.configure_month(
            self.admin, self.sb, "2026-09", level1_base_cents=rm(200000),
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
        for d in range(1, 29):  # incomplete September (already ended)
            self.app.record_daily(self.admin, self.sb, date(2026, 9, d),
                                  sales_cents=rm(20000))
        ov = self.overview(store=self.sb, month="2026-09")
        texts = " | ".join(i["text"] for i in ov["attention"])
        self.assertNotIn("unreported day", texts)

    # 7 ------------------------------------------------------------------
    def test_multi_store_isolation(self):
        ov_a = self.overview(store=self.sa)
        ov_b = self.overview(store=self.sb)
        self.assertNotEqual(ov_a["cc"]["sales_mtd"], ov_b["cc"]["sales_mtd"])
        self.assertNotEqual(ov_a["cc"]["actions_open"],
                            ov_b["cc"]["actions_open"])

    # 8 ------------------------------------------------------------------
    def test_unauthorized_read_rejected(self):
        stranger = self.app.create_user(self.admin, "s@ducklab.com", "S",
                                        "StrangerPass123!")
        with self.assertRaises(AuthorizationError):
            self.overview(user=stranger)

    # 9 ------------------------------------------------------------------
    def test_empty_state_without_store(self):
        # page keeps the "no stores assigned" empty state; compute_overview
        # is never called without a store (loader guards on selectors)
        # here: no month config -> ConfigNotFoundError path on a fresh store
        from ducklab.operations_repository import ConfigNotFoundError
        sc = self.app.register_store(self.admin, "C03", "Gamma")
        with self.assertRaises(ConfigNotFoundError):
            self.overview(store=sc, month="2026-11")


if __name__ == "__main__":
    unittest.main()
