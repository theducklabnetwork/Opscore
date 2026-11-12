"""Task O2 — per-page guided tours and replay.

Temp DBs only. Run: python -m pytest tests/test_page_tours_o2.py -v
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ducklab import ops_shared_state, ops_state
from ducklab.app_service import AppService
from ducklab.auth_service import ValidationError
from ducklab.ops_state import OpsState

PW = "Sup3rSecure!x"
PW2 = "An0therGood42"


def script(event):
    return event.args[0][1]._var_value


class PageTourFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "page_tours.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)
        self.state = object.__new__(OpsState)
        self._patches = [
            mock.patch.object(ops_shared_state, "app_service", self.app),
            mock.patch.object(ops_state, "app_service", self.app),
            mock.patch.object(OpsState, "_actor", lambda _: self.admin),
        ]
        for patch in self._patches:
            patch.start()

    def tearDown(self):
        for patch in reversed(self._patches):
            patch.stop()
        self._tmp.cleanup()


class TestPageTourFlags(PageTourFixture):
    def test_done_is_per_page_and_per_user(self):
        self.app.auth.mark_page_tour_done(self.admin.id, "stores")
        self.assertTrue(self.app.auth.is_page_tour_done(
            self.admin.id, "stores"))
        self.assertFalse(self.app.auth.is_page_tour_done(
            self.admin.id, "daily"))
        other = self.app.auth.create_user(
            "two@ducklab.com", "Two", PW2, actor=self.admin)
        self.assertFalse(self.app.auth.is_page_tour_done(
            other.id, "stores"))

    def test_completion_is_idempotent_and_survives_reinit(self):
        for _ in range(2):
            OpsState.page_tour_finished.fn(self.state, "daily")
        self.assertTrue(self.app.auth.is_page_tour_done(
            self.admin.id, "daily"))
        again = AppService(self.app.auth._db_path)
        self.assertTrue(again.auth.is_page_tour_done(
            self.admin.id, "daily"))

    def test_welcome_and_page_flags_are_independent(self):
        self.app.auth.mark_tour_done(self.admin.id)
        self.assertFalse(self.app.auth.is_page_tour_done(
            self.admin.id, "stores"))
        OpsState.page_tour_finished.fn(self.state, "stores")
        self.assertTrue(self.app.auth.get_user(self.admin.id).tour_done)

    def test_unknown_page_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.auth.mark_page_tour_done(self.admin.id, "unknown")


class TestPageTourLaunch(PageTourFixture):
    def test_first_visit_starts_tour(self):
        self.app.auth.mark_tour_done(self.admin.id)
        self.admin = self.app.auth.get_user(self.admin.id)
        event = OpsState.maybe_start_page_tour.fn(self.state, "daily")
        self.assertEqual(
            script(event),
            "window.ducklabTour && window.ducklabTour.start('daily')")

    def test_page_tour_never_starts_while_welcome_is_pending(self):
        self.assertIsNone(
            OpsState.maybe_start_page_tour.fn(self.state, "daily"))

    def test_completed_page_does_not_autostart(self):
        self.app.auth.mark_tour_done(self.admin.id)
        self.admin = self.app.auth.get_user(self.admin.id)
        self.app.auth.mark_page_tour_done(self.admin.id, "daily")
        self.assertIsNone(
            OpsState.maybe_start_page_tour.fn(self.state, "daily"))

    def test_replay_runs_even_after_completion(self):
        self.app.auth.mark_tour_done(self.admin.id)
        self.app.auth.mark_page_tour_done(self.admin.id, "stores")
        event = OpsState.replay_page_tour.fn(self.state, "stores")
        self.assertEqual(
            script(event),
            "window.ducklabTour && window.ducklabTour.start('stores')")
        self.assertTrue(self.app.auth.is_page_tour_done(
            self.admin.id, "stores"))

    def test_replay_does_not_require_welcome_completion(self):
        event = OpsState.replay_page_tour.fn(self.state, "stores")
        self.assertIn("start('stores')", script(event))
        self.assertFalse(self.app.auth.get_user(self.admin.id).tour_done)


class TestPageTourAssets(unittest.TestCase):
    def test_page_steps_targets_and_mobile_behavior(self):
        root = Path(__file__).resolve().parent.parent
        tour_js = (root / "assets" / "tour.js").read_text()
        self.assertEqual(tour_js.count("var PAGE_TOURS"), 1)
        self.assertIn('start: start', tour_js)
        self.assertIn('return { popover: step.popover };', tour_js)
        for target, title, description in [
            ("#stores-table", "Stores",
             "Add your store and keep its details up to date."),
            ("#setup-section", "Monthly Setup",
             "Set the month's targets, levels, budgets and month-end inputs."),
            ("#setup-holidays-field", "Public Holidays",
             "Mark holidays so daily targets use the right share."),
            ("#record-day", "New Entry",
             "Record a day's sales, manpower and stock loss."),
            ("#daily-kpis", "Monthly Summary",
             "See sales, achievement, transactions, units, loss and expenses "
             "for the month."),
            ("#daily-table", "Daily Records",
             "Review and edit each day's recorded figures."),
        ]:
            self.assertIn(target, tour_js)
            self.assertIn(title, tour_js)
            self.assertIn(description, tour_js)

    def test_section_titles_and_holiday_target(self):
        root = Path(__file__).resolve().parent.parent
        stores = (root / "ducklab" / "ops_stores_pages.py").read_text()
        for title in ("Stores", "User Assignment", "Area & People",
                      "Monthly Setup", "Sales Targets", "Daily Sales",
                      "Manpower", "Stock", "Expense Budgets (%)",
                      "Month-End Inputs"):
            self.assertIn(title, stores)
        self.assertIn('id="setup-holidays-field"', stores)
        daily = (root / "ducklab" / "ops_daily_pages.py").read_text()
        self.assertIn('rx.heading("Monthly Summary"', daily)

    def test_headers_have_replay_buttons(self):
        root = Path(__file__).resolve().parent.parent
        shell = (root / "ducklab" / "ops_shell.py").read_text()
        self.assertIn('id=f"tour-replay-{tour_key}"', shell)
        for page in ("ops_stores_pages.py", "ops_daily_pages.py"):
            source = (root / "ducklab" / page).read_text()
            self.assertIn('tour_key="', source)


if __name__ == "__main__":
    unittest.main()
