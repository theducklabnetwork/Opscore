"""All Stores aggregation, coverage, isolation and payroll confidentiality."""

import tempfile
import unittest
from datetime import date
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
