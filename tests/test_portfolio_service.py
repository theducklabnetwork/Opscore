"""All Stores aggregation, coverage, isolation and payroll confidentiality."""

import tempfile
import unittest
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.portfolio_service import PortfolioService


def rm(value):
    return int(Decimal(str(value)) * 100)


class PortfolioTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self.tmp.name) / "portfolio.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.a = self.app.register_store(self.admin, "A01", "Alpha")
        self.b = self.app.register_store(self.admin, "B02", "Beta")
        self.c = self.app.register_store(self.admin, "C03", "Gamma")
        for sid, target, salary in ((self.a, 10000, 500), (self.b, 20000, 1500)):
            self.app.configure_month(
                self.admin, sid, "2026-10", level1_base_cents=rm(target),
                level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                salary_basic_allowance_cents=rm(salary), overtime_cents=0)
        for day in (1, 2):
            self.app.record_daily(self.admin, self.a, date(2026, 10, day),
                                  sales_cents=rm(1000), stock_losses={"DMO": rm(10)})
        self.app.record_daily(self.admin, self.b, date(2026, 10, 1),
                              sales_cents=rm(3000), stock_losses={"DMO": rm(30)})
        self.viewer = self.app.create_user(
            self.admin, "viewer@ducklab.com", "Viewer", "ViewerPass123!")
        self.app.assign_role(self.admin, self.viewer.id, "viewer")
        self.app.assign_store(self.admin, self.viewer.id, self.a)
        self.app.assign_store(self.admin, self.viewer.id, self.b)
        self.viewer = self.app.auth.get_user(self.viewer.id)

    def tearDown(self):
        self.tmp.cleanup()

    def test_aggregation_weighted_ratio_and_coverage(self):
        result = PortfolioService(self.app).build(
            self.admin, "2026-10", date(2026, 10, 21))
        self.assertEqual(result.sales_cents, rm(5000))
        self.assertEqual(result.salary_cents, rm(2000))
        self.assertEqual(result.salary_ratio, Decimal("0.4"))
        self.assertEqual(result.stock_loss_cents, rm(50))
        self.assertEqual(result.expenses_cents, 0)  # reported zero is not None
        self.assertEqual((result.configured_count, result.store_count), (2, 3))
        self.assertEqual((result.days_reported, result.store_days), (3, 62))
        self.assertEqual(result.rows[2].status, "Setup Required")

    def test_ratio_is_not_average_of_store_ratios(self):
        result = PortfolioService(self.app).build(self.admin, "2026-10")
        # Store ratios are 25% and 50%; their average is 37.5%, portfolio 40%.
        self.assertEqual(result.salary_ratio, Decimal("0.4"))

    def test_missing_setup_and_store_failure_do_not_break_others(self):
        original = self.app.preview_month

        def fail_beta(user, store_id, month, **kwargs):
            if store_id == self.b:
                raise ConfigNotFoundError("missing")
            return original(user, store_id, month, **kwargs)

        with mock.patch.object(self.app, "preview_month", side_effect=fail_beta):
            result = PortfolioService(self.app).build(self.admin, "2026-10")
        self.assertEqual(result.sales_cents, rm(2000))
        self.assertEqual(result.configured_count, 1)
        self.assertEqual([r.status for r in result.rows],
                         ["OK", "Setup Required", "Setup Required"])

    def test_none_payroll_is_not_zero_and_is_partial(self):
        self.app.record_payroll(self.admin, self.b, "2026-10",
                                salary_basic_allowance_cents=None,
                                overtime_cents=None, support_team_cost_cents=None)
        result = PortfolioService(self.app).build(self.admin, "2026-10")
        self.assertEqual(result.salary_cents, rm(500))
        self.assertEqual(result.payroll_status, "Partial")
        self.assertIsNone(result.rows[1].salary_cents)
        self.assertIsNone(result.rows[1].salary_ratio)

    def test_only_assigned_stores_and_no_payroll_leak_for_viewer(self):
        result = PortfolioService(self.app).build(
            self.viewer, "2026-10", date(2026, 10, 21))
        self.assertEqual([r.store_id for r in result.rows], [self.a, self.b])
        self.assertIsNone(result.salary_cents)
        self.assertIsNone(result.salary_ratio)
        self.assertEqual(result.payroll_status, "Restricted")
        self.assertTrue(all(r.salary_cents is None for r in result.rows))
        self.assertNotIn("total_salary", str(result.rows))
        self.assertNotIn("Decimal('0.25')", str(result.rows))

    def test_permission_failure_is_not_silently_redacted(self):
        stranger = self.app.create_user(
            self.admin, "other@ducklab.com", "Other", "OtherPass123!")
        self.app.assign_role(self.admin, stranger.id, "viewer")
        stranger = self.app.auth.get_user(stranger.id)
        self.assertEqual(PortfolioService(self.app).build(stranger, "2026-10").rows, [])
        with mock.patch.object(self.app, "preview_month",
                               side_effect=AuthorizationError("revoked")):
            with self.assertRaises(AuthorizationError):
                PortfolioService(self.app).build(self.viewer, "2026-10")

    def test_alerts_reuse_dashboard_logic_and_link_to_stores(self):
        today = date(2026, 10, 21)
        self.app.configure_month(
            self.admin, self.a, "2026-10", opening_headcount_ft=1,
            min_rtm_target_cents=rm(100),
            turnover_threshold=0.01, stock_loss_target=0.001)
        # Reuse the default "Electricity" category seeded by create_store().
        category = next(
            c.id for c in self.app.get_expense_categories(self.admin, self.a)
            if c.name == "Electricity")
        self.app.set_expense_budget(
            self.admin, self.a, "2026-10", category, 0.01)  # 1% x RM 100 = RM 1
        self.app.record_expense(
            self.admin, self.a, incurred_date=date(2026, 10, 1),
            paid_date=date(2026, 10, 1), category_id=category,
            description="Bill", amount_cents=rm(2), paid_by="Petty Cash",
            status="Paid", verification="Verified", recorded_by="DEV")
        self.app.record_daily(self.admin, self.a, date(2026, 10, 2),
                              resigned_ft=1)
        self.app.save_action(
            self.admin, self.a, None, today,
            raised_date=today - timedelta(days=2),
            pillar="General Operations", issue="Overdue", priority="High",
            status="Open", pic="DEV", due_date=today - timedelta(days=1),
            corrective_action=None, completion_date=None, remarks=None)

        result = PortfolioService(self.app).build(self.admin, "2026-10", today)
        kinds = {a.kind for a in result.alerts}
        self.assertEqual(kinds, {
            "Unreported Days", "Expense Over Budget",
            "Stock Loss Over Target", "High Turnover", "Overdue Actions",
            "Setup Required",
        })
        expected_hrefs = {f"/ops?store={sid}" for sid in (self.a, self.b, self.c)}
        self.assertEqual({a.href for a in result.alerts}, expected_hrefs)
        self.assertEqual((result.open_actions, result.overdue_actions), (1, 1))

        from ducklab.ops_overview_state import compute_portfolio
        with mock.patch("ducklab.portfolio_service.PortfolioService.build",
                        return_value=result):
            data = compute_portfolio(self.admin, "2026-10", today)
        groups = {g["kind"]: g["items"] for g in data["portfolio_alerts"]}
        self.assertEqual(groups["Setup Required"][0]["store"], "C03 — Gamma")
        self.assertIn({"label": "Open Actions", "value": "1",
                       "coverage": "Active stores"}, data["portfolio_summary"])
        self.assertIn({"label": "Overdue Actions", "value": "1",
                       "coverage": "Active stores"}, data["portfolio_summary"])

    def test_inactive_stores_excluded_from_current_alerts_and_actions(self):
        today = date(2026, 10, 21)
        self.app.save_action(
            self.admin, self.a, None, today,
            raised_date=today, pillar="General Operations", issue="Open",
            priority="High", status="Open", pic="DEV", due_date=today,
            corrective_action=None, completion_date=None, remarks=None)
        self.app.set_store_active(self.admin, self.a, False)
        self.app.set_store_active(self.admin, self.c, False)
        result = PortfolioService(self.app).build(self.admin, "2026-10", today)
        self.assertEqual(result.sales_cents, rm(5000))
        self.assertEqual((result.open_actions, result.overdue_actions), (0, 0))
        self.assertEqual({a.store for a in result.alerts}, {"B02 — Beta"})
        self.assertTrue(all(a.href == f"/ops?store={self.b}"
                            for a in result.alerts))

    def test_report_portfolio_details_and_payroll_redaction(self):
        from ducklab.ops_overview_state import compute_portfolio
        result = PortfolioService(self.app).build(
            self.admin, "2026-10", date(2026, 10, 21))
        with mock.patch("ducklab.portfolio_service.PortfolioService.build",
                        return_value=result):
            admin = compute_portfolio(self.admin, "2026-10", date(2026, 10, 21))
        row = admin["portfolio_rows"][0]
        self.assertIn("paid · RM 0.00 incurred · RM 0.00 open",
                      row["expense_detail"])
        self.assertEqual(row["salary"], "RM 500.00")
        self.assertEqual(row["salary_ratio"], "25.0%")
        self.assertIn("open", row["actions"])
        self.assertIn("stock_ratio", row)
        self.assertNotIn("Level Reached", str(admin))
        self.assertNotIn("growth", str(admin))

        result = PortfolioService(self.app).build(
            self.viewer, "2026-10", date(2026, 10, 21))
        with mock.patch("ducklab.portfolio_service.PortfolioService.build",
                        return_value=result):
            viewer = compute_portfolio(self.viewer, "2026-10", date(2026, 10, 21))
        for row in viewer["portfolio_rows"]:
            self.assertEqual(row["salary"], "Restricted")
            self.assertEqual(row["salary_ratio"], "Restricted")
        self.assertNotIn("RM 500.00", str(viewer))
        self.assertNotIn("25.0%", str(viewer))

    def test_report_is_live_only_and_uses_separate_state(self):
        from ducklab.ops_pages import report_page
        from ducklab.ops_state import OpsState
        from ducklab import ops_state
        page = str(report_page())
        for text in ("All Stores Summary", "Store Comparison", "Manpower",
                     "Stock Loss", "Actions", "all_stores"):
            self.assertIn(text, page)
        class ReportState(OpsState):
            pass

        state = ReportState()
        values = {
            "has_stores": True,
            "selected_store_label": "All Stores",
            "selected_month_label": "October 2026",
            "report_mode": "finalized",
            "month_options": ["October 2026"],
        }
        for key, value in values.items():
            state.__dict__[key] = value
        result = PortfolioService(self.app).build(
            self.admin, "2026-10", date(2026, 10, 21))
        with mock.patch.object(ops_state.kpi, "kl_today",
                               return_value=date(2026, 10, 21)), \
             mock.patch("ducklab.portfolio_service.PortfolioService.build",
                        return_value=result), \
             mock.patch.object(ReportState, "_actor", return_value=self.admin), \
             mock.patch.object(ops_state.app_service, "list_report_versions") as versions:
            list(ReportState.refresh_report.fn(state))
        self.assertTrue(state.all_stores)
        self.assertEqual(state.report_mode, "live")
        self.assertEqual(state.report_status, "ok")
        self.assertEqual(state.report, {})
        self.assertEqual(state.versions, [])
        self.assertFalse(state.can_finalize)
        self.assertEqual(len(state.portfolio_rows), 3)
        versions.assert_not_called()

    def test_dashboard_option_and_comparison_links(self):
        from ducklab.ops_pages import dashboard_page
        page = str(dashboard_page())
        for text in ("All Stores", "Salary Ratio", "Store Comparison",
                     "Days Reported"):
            self.assertIn(text, page)
        for text in ("Total Sales", "Total Expenses", "Total Stock Loss",
                     "Total Salary Cost"):
            self.assertIn(text, str(__import__("ducklab.ops_overview_state", fromlist=["compute_portfolio"]).compute_portfolio.__code__.co_consts))
        self.assertIn("portfolio-comparison", page)


if __name__ == "__main__":
    unittest.main()
