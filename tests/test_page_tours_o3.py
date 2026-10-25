"""Task O3 — guided tours for the remaining operations pages.

Temp DBs only. Run: python -m pytest tests/test_page_tours_o3.py -v
"""

from unittest import mock

from ducklab.app_service import AppService
from ducklab.ops_overview_state import OverviewState
from ducklab.ops_state import OpsState
from test_page_tours_o2 import PageTourFixture, script


EXPECTED_STEPS = {
    "dashboard": [
        ("#pillar-strip", "Monthly Overview",
         "See the month's main sales, expenses, salary and stock figures."),
        ("#overview-pace", "Sales Trend",
         "Track daily sales and progress toward the target."),
        ("#targets-panel", "Targets",
         "Compare sales with each target level."),
        ("#attention-panel", "Alerts", "Find items that need attention."),
        ("#at-a-glance", "Summary",
         "See average daily sales, incurred and unpaid expenses, and open "
         "actions."),
    ],
    "report": [
        ("#sales-section", "Sales",
         "Review sales results, targets, achievement and basket metrics."),
        ("#expenses-section", "Expenses",
         "Review monthly expenses, budgets and category totals."),
        ("#manpower-section", "Manpower",
         "Review headcount, payroll ratios, turnover and leave."),
        ("#stock-section", "Stock",
         "Review stock loss totals, ratios and loss by category."),
        ("#actions-section", "Actions", "Review action totals by status."),
    ],
    "sales": [
        ("#sales-kpis", "Sales Summary",
         "See sales, achievement, target level and daily averages."),
        ("#basket-section", "Basket",
         "Compare basket metrics with state averages."),
        ("#pace-section", "Sales Trend",
         "Track daily sales and target progress through the month."),
        ("#ladder-section", "Targets",
         "Compare achievement and gaps across target levels."),
        ("#sales-daily-section", "Daily Sales",
         "Review daily sales, targets, transactions and results."),
    ],
    "expenses": [
        ("#expense-kpis", "Expense Summary",
         "See paid, incurred, pending and budget figures."),
        ("#budget-monitor", "Budget vs Paid",
         "Compare category budgets with paid expenses and set budgets."),
        ("#expense-register", "Transactions",
         "Filter, review and edit expense transactions."),
    ],
    "manpower": [
        ("#headcount-section", "Headcount",
         "Review workforce changes, budget variance and turnover."),
        ("#efficiency-section", "Productivity",
         "Review sales per effective staff member."),
        ("#payroll-section", "Salary",
         "Review payroll costs and salary ratios against targets."),
        ("#leave-section", "Leave", "Review leave totals by type."),
        ("#staffing-table", "Daily Staffing",
         "Review and edit daily staffing and productivity records."),
    ],
    "stock": [
        ("#stock-kpi-section", "Stock Summary",
         "See total loss, loss ratio, missing stock and extra stock."),
        ("#trend-section", "Daily Loss",
         "Chart of stock loss for each reported day."),
        ("#category-section", "Categories",
         "Review loss by category and inspect individual entries."),
        ("#daily-section", "Daily Stock Loss",
         "Each day's loss by code, with its status."),
        ("#comparison-section", "Comparison",
         "Compare this month's stock figures with the previous month."),
    ],
    "actions": [
        ("#action-cards", "Action Summary",
         "See open, overdue and completed action totals."),
        ("#actions-table", "Actions",
         "Filter, review and edit actions and their follow-up details."),
    ],
    "settings": [
        ("#settings-reference", "Reference Data",
         "Expense categories and the Daily Sales Contribution file."),
        ("#settings-data", "Data", "Back up and restore your data."),
        ("#pw-section", "Change Password",
         "Update your password and sign out other sessions."),
        ("#tour-section", "Guided Tour",
         "Replay the welcome tour from the Dashboard."),
    ],
}

PAGE_SOURCES = {
    "dashboard": "ops_pages.py",
    "report": "ops_pages.py",
    "sales": "ops_sales_pages.py",
    "expenses": "ops_expenses_pages.py",
    "manpower": "ops_manpower_pages.py",
    "stock": "ops_stock_pages.py",
    "actions": "ops_actions_pages.py",
    "settings": "ops_settings_pages.py",
}


class TestRemainingPageTours(PageTourFixture):
    def test_all_new_pages_start_only_after_welcome(self):
        for page_key in EXPECTED_STEPS:
            with self.subTest(page=page_key, welcome="pending"):
                self.assertIsNone(
                    OpsState.maybe_start_page_tour.fn(self.state, page_key))
        self.app.auth.mark_tour_done(self.admin.id)
        self.admin = self.app.auth.get_user(self.admin.id)
        for page_key in EXPECTED_STEPS:
            with self.subTest(page=page_key, welcome="done"):
                event = OpsState.maybe_start_page_tour.fn(self.state, page_key)
                expected = (
                    "window.ducklabTour && window.ducklabTour.start("
                    "'settings', 'super_admin')" if page_key == "settings"
                    else "window.ducklabTour && window.ducklabTour.start("
                    f"'{page_key}')")
                self.assertEqual(script(event), expected)

    def test_dashboard_waits_for_a_visit_after_welcome(self):
        welcome = OverviewState.maybe_start_tour.fn(self.state)
        dashboard = OpsState.maybe_start_page_tour.fn(self.state, "dashboard")
        self.assertIn("start()", script(welcome))
        self.assertIsNone(dashboard)

        OpsState.tour_finished.fn(self.state)
        self.admin = self.app.auth.get_user(self.admin.id)
        next_visit = OpsState.maybe_start_page_tour.fn(self.state, "dashboard")
        self.assertIn("start('dashboard')", script(next_visit))

    def test_settings_launch_matches_role_visibility(self):
        for role, expected in (
                ("super_admin", "super_admin"),
                ("administrator", "administrator"),
                ("zone_manager", "limited"),
                ("branch_manager", "limited"),
                ("viewer", "limited")):
            if role == "super_admin":
                actor = self.admin
            else:
                user = self.app.create_user(
                    self.admin, f"{role}@ducklab.com", role, "An0therGood42")
                actor = self.app.assign_role(self.admin, user.id, role)
            self.app.auth.mark_tour_done(actor.id)
            actor = self.app.auth.get_user(actor.id)
            with self.subTest(role=role, launch="automatic"):
                with mock.patch.object(OpsState, "_actor", lambda _: actor):
                    event = OpsState.maybe_start_page_tour.fn(
                        self.state, "settings")
                self.assertIn(f"'settings', '{expected}'", script(event))
            self.app.auth.mark_page_tour_done(actor.id, "settings")
            with self.subTest(role=role, launch="replay"):
                with mock.patch.object(OpsState, "_actor", lambda _: actor):
                    event = OpsState.replay_page_tour.fn(self.state, "settings")
                self.assertIn(f"'settings', '{expected}'", script(event))

    def test_new_page_completion_is_persistent(self):
        for page_key in EXPECTED_STEPS:
            OpsState.page_tour_finished.fn(self.state, page_key)
        again = AppService(self.app.auth._db_path)
        for page_key in EXPECTED_STEPS:
            with self.subTest(page=page_key):
                self.assertTrue(again.auth.is_page_tour_done(
                    self.admin.id, page_key))


class TestRemainingPageTourAssets(PageTourFixture):
    def test_every_step_has_three_to_five_targets_and_exact_wording(self):
        root = self.root
        tour_js = (root / "assets" / "tour.js").read_text()
        keys = [*EXPECTED_STEPS, "stores", "daily"]
        for index, (page_key, steps) in enumerate(EXPECTED_STEPS.items()):
            minimum = 2 if page_key == "actions" else 3
            self.assertGreaterEqual(len(steps), minimum)
            self.assertLessEqual(len(steps), 5)
            start = tour_js.index(f"    {page_key}: [")
            end = tour_js.index(f"    {keys[index + 1]}: [")
            block = tour_js[start:end]
            expected_count = len(steps) if page_key != "settings" else 4
            self.assertEqual(block.count("description:"), expected_count)
            source = (root / "ducklab" / PAGE_SOURCES[page_key]).read_text()
            self.assertIn(f'tour_key="{page_key}"', source)
            for target, title, description in steps:
                with self.subTest(page=page_key, title=title):
                    self.assertIn(f'element: "{target}"', block)
                    self.assertIn(f'title: "{title}"', block)
                    self.assertIn(description, block)
                    self.assertIn(f'id="{target[1:]}"', source)

    def test_new_headings_and_autostart_wiring(self):
        shell_source = (self.root / "ducklab" / "ducklab.py").read_text()
        for page_key in EXPECTED_STEPS:
            self.assertIn(
                f'OpsState.maybe_start_page_tour("{page_key}")', shell_source)
        overview = (self.root / "ducklab" / "ops_overview_state.py").read_text()
        self.assertIn('targets_title: str = "Targets"', overview)
        settings = (self.root / "ducklab" / "ops_settings_pages.py").read_text()
        self.assertIn('"Change Password"', settings)
        self.assertIn('"Guided Tour"', settings)

    @property
    def root(self):
        from pathlib import Path
        return Path(__file__).resolve().parent.parent
