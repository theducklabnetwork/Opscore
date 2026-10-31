"""Task O1 — first launch: Create Account bootstrap + guided-tour flag.

Temp DBs only. Run: python -m unittest tests.test_first_launch_o1 -v
"""

import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import AuthService, ValidationError

PW = "Sup3rSecure!x"
PW2 = "An0therGood42"


class FirstLaunchFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "first_launch.db"
        self.app = AppService(self.db_path)
        self.auth: AuthService = self.app.auth

    def tearDown(self):
        self._tmp.cleanup()

    def conn(self):
        return store_ops_db.connect(self.db_path)


class TestCreateAccountBootstrap(FirstLaunchFixture):
    def test_zero_users_initially(self):
        self.assertEqual(self.auth.user_count(), 0)

    def test_bootstrap_creates_super_admin_and_signs_in(self):
        u = self.auth.bootstrap_first_admin("owner@ducklab.com", "Owner", PW)
        self.assertIn("super_admin", u.roles)
        self.assertEqual(self.auth.user_count(), 1)
        # The account can sign in immediately.
        _token, user = self.auth.login("owner@ducklab.com", PW)
        self.assertEqual(user.id, u.id)

    def test_bootstrap_refused_once_any_user_exists(self):
        self.auth.bootstrap_first_admin("owner@ducklab.com", "Owner", PW)
        with self.assertRaises(ValidationError):
            self.auth.bootstrap_first_admin("second@ducklab.com", "Two", PW2)

    def test_bootstrap_refused_with_non_admin_user_present(self):
        # Any user — not only a super admin — closes the bootstrap window.
        self.auth.bootstrap_first_admin("owner@ducklab.com", "Owner", PW)
        admin = self.auth.get_user(
            self.auth.login("owner@ducklab.com", PW)[1].id)
        self.auth.create_user("viewer@ducklab.com", "Viewer", PW2,
                              actor=admin)
        with self.assertRaises(ValidationError):
            self.auth.bootstrap_first_admin("late@ducklab.com", "Late", PW2)

    def test_bootstrap_audit_event_recorded(self):
        u = self.auth.bootstrap_first_admin("owner@ducklab.com", "Owner", PW)
        with closing(self.conn()) as conn:
            rows = conn.execute(
                "SELECT event_type FROM auth_audit_events WHERE user_id = ? "
                "AND event_type = 'bootstrap_first_admin'", (u.id,)).fetchall()
        self.assertEqual(len(rows), 1)


class TestTourFlag(FirstLaunchFixture):
    def setUp(self):
        super().setUp()
        self.admin = self.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)

    def test_new_users_start_with_tour_not_done(self):
        self.assertFalse(self.admin.tour_done)
        self.assertFalse(self.auth.get_user(self.admin.id).tour_done)

    def test_mark_tour_done_persists(self):
        updated = self.auth.mark_tour_done(self.admin.id)
        self.assertTrue(updated.tour_done)
        self.assertTrue(self.auth.get_user(self.admin.id).tour_done)
        # Column value in the DB.
        with closing(self.conn()) as conn:
            row = conn.execute(
                "SELECT tour_done FROM auth_users WHERE id = ?",
                (self.admin.id,)).fetchone()
        self.assertEqual(row["tour_done"], 1)

    def test_tour_done_is_per_user(self):
        other = self.auth.create_user("two@ducklab.com", "Two", PW2,
                                      actor=self.admin)
        self.auth.mark_tour_done(self.admin.id)
        self.assertFalse(self.auth.get_user(other.id).tour_done)

    def test_reset_tour_replays(self):
        self.auth.mark_tour_done(self.admin.id)
        reset = self.auth.reset_tour(self.admin.id)
        self.assertFalse(reset.tour_done)
        self.assertFalse(self.auth.get_user(self.admin.id).tour_done)

    def test_tour_flag_survives_reinit(self):
        # init_auth runs on every AuthService construction; the additive
        # migration must not clobber an existing flag.
        self.auth.mark_tour_done(self.admin.id)
        again = AuthService(self.db_path)
        self.assertTrue(again.get_user(self.admin.id).tour_done)

    def test_tour_done_audited(self):
        self.auth.mark_tour_done(self.admin.id)
        self.auth.reset_tour(self.admin.id)
        with closing(self.conn()) as conn:
            rows = conn.execute(
                "SELECT event_type FROM auth_audit_events WHERE user_id = ? "
                "AND event_type IN ('tour_done', 'tour_reset') ORDER BY id",
                (self.admin.id,)).fetchall()
        self.assertEqual([r["event_type"] for r in rows],
                         ["tour_done", "tour_reset"])


class TestTourAssets(unittest.TestCase):
    """Bundled driver.js + tour wiring (no CDN)."""

    def test_driver_js_and_css_are_bundled(self):
        root = Path(__file__).resolve().parent.parent
        driver_js = (root / "assets" / "driver.js").read_text()
        driver_css = (root / "assets" / "driver.css").read_text()
        self.assertIn("driver.js", driver_js[:120])
        self.assertIn("driver-popover", driver_css)
        # No remote fetches: the tour must be fully self-contained.
        tour_js = (root / "assets" / "tour.js").read_text()
        self.assertNotIn("https://", tour_js)
        self.assertIn("/driver.js", tour_js)

    def test_tour_steps_match_spec(self):
        root = Path(__file__).resolve().parent.parent
        tour_js = (root / "assets" / "tour.js").read_text()
        for title, desc in [
            ("Welcome to Opscore",
             "Your store's sales, expenses, manpower and stock in one place."),
            ("Store Management",
             "Start here. Add your store and set its monthly targets."),
            ("Daily Operations",
             "Key in each day's sales, manpower and stock loss here."),
            ("Dashboard", "Your month at a glance."),
            ("Sales Performance", "Targets, daily progress and basket."),
            ("Expenses Management", "Budgets and paid expenses."),
            ("Manpower Efficiency", "Hours, payroll and productivity."),
            ("Stock Management", "Stock loss by category."),
            ("Monthly Report", "The full month, ready to share."),
            ("Action Tracker", "Follow up issues until they're closed."),
            ("Settings", "Password, reference data and backups."),
            ("All Set", "Replay this tour anytime from Settings."),
        ]:
            self.assertIn(title, tour_js)
            self.assertIn(desc, tour_js)
        # Step x of y progress + navigation buttons.
        self.assertIn("Step {{current}} of {{total}}", tour_js)
        self.assertIn('"Next"', tour_js)
        self.assertIn('"Back"', tour_js)

    def test_shell_nav_ids_for_tour_targets(self):
        root = Path(__file__).resolve().parent.parent
        shell = (root / "ducklab" / "ops_shell.py").read_text()
        self.assertIn('id="nav-" + href.strip("/").replace("/", "-")',
                      shell)

    def test_replay_button_in_settings_account(self):
        root = Path(__file__).resolve().parent.parent
        settings = (root / "ducklab" / "ops_settings_pages.py").read_text()
        self.assertIn('id="tour-replay"', settings)
        self.assertIn("Replay Tour", settings)


if __name__ == "__main__":
    unittest.main()
