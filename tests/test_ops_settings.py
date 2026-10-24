"""S1 tests: /ops/settings — Account (change password) and About tabs.

Password flow through the real AuthService (success, wrong current,
mismatch, weak) and tab visibility by role. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_settings.py -v
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab import ops_settings_pages as mod
from ducklab.ops_settings_pages import SettingsState, app_version

PW = "Sup3rSecure!x"
PW2 = "An0therGood42"


class SettingsFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "settings.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles):
        u = self.app.create_user(self.admin, email, "T User", PW2)
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        return self.app.auth.get_user(u.id)

    def state(self, actor) -> SettingsState:
        st = SettingsState()
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(SettingsState, "_actor",
                                  lambda s_: actor):
            st.load_settings()
        return st


class TestLoad(SettingsFixture):
    def test_identity_loaded(self):
        st = self.state(self.admin)
        self.assertEqual(st.account_email, "admin@ducklab.com")
        self.assertEqual(st.account_name, "Root Admin")
        self.assertTrue(st.is_super_admin)

    def test_tabs_account_and_about_for_every_role(self):
        for roles in (("super_admin",), ("administrator",),
                      ("zone_manager",), ("branch_manager",), ("viewer",),
                      ()):
            actor = self.admin if roles == ("super_admin",) else \
                self.user(f"{roles and roles[0] or 'none'}@ducklab.com",
                          roles)
            st = self.state(actor)
            expected = ["account", "about"] if roles not in (
                ("super_admin",), ("administrator",)) else \
                ["account", "reference", "about"]
            self.assertEqual(st.visible_tabs, expected)

    def test_about_fields(self):
        st = self.state(self.admin)
        self.assertTrue(st.about_version)          # release id or git sha
        self.assertEqual(st.about_schema, str(store_ops_db.SCHEMA_VERSION))
        self.assertTrue(st.about_db_path)          # super admin sees path

    def test_db_path_hidden_for_non_super(self):
        u = self.user("v@ducklab.com", ("viewer",))
        st = self.state(u)
        self.assertFalse(st.is_super_admin)
        self.assertEqual(st.about_db_path, "")


class TestChangePassword(SettingsFixture):
    def change(self, actor, current, new, confirm=None) -> SettingsState:
        st = self.state(actor)
        st.pw_current = current
        st.pw_new = new
        st.pw_confirm = new if confirm is None else confirm
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(SettingsState, "_actor",
                                  lambda s_: actor):
            st.change_password()
        return st

    def test_success(self):
        u = self.user("u@ducklab.com", ("viewer",))
        st = self.change(u, PW2, "N3wPassword99")
        self.assertEqual(st.pw_error, "")
        self.assertIn("Password changed", st.pw_notice)
        # form cleared
        self.assertEqual((st.pw_current, st.pw_new, st.pw_confirm),
                         ("", "", ""))
        # new password works, old one does not
        self.app.auth.login("u@ducklab.com", "N3wPassword99")
        with self.assertRaises(Exception):
            self.app.auth.login("u@ducklab.com", PW2)

    def test_wrong_current(self):
        u = self.user("u2@ducklab.com", ("viewer",))
        st = self.change(u, "WrongPass123", "N3wPassword99")
        self.assertEqual(st.pw_error, "Current password is incorrect.")
        self.assertEqual(st.pw_notice, "")
        # old password still valid
        self.app.auth.login("u2@ducklab.com", PW2)

    def test_mismatch(self):
        u = self.user("u3@ducklab.com", ("viewer",))
        st = self.change(u, PW2, "N3wPassword99", confirm="Different99")
        self.assertEqual(st.pw_error, "New passwords do not match.")
        self.app.auth.login("u3@ducklab.com", PW2)

    def test_weak_password(self):
        u = self.user("u4@ducklab.com", ("viewer",))
        st = self.change(u, PW2, "short")
        self.assertIn("at least 10 characters", st.pw_error)
        st2 = self.change(u, PW2, "nodigitsinhere")
        self.assertIn("letters and digits", st2.pw_error)
        self.app.auth.login("u4@ducklab.com", PW2)

    def test_success_revokes_other_sessions(self):
        u = self.user("u5@ducklab.com", ("viewer",))
        token, _ = self.app.auth.login("u5@ducklab.com", PW2)
        self.change(u, PW2, "N3wPassword99")
        with self.assertRaises(Exception):
            self.app.auth.authenticate_token(token)


class TestVersion(unittest.TestCase):
    def test_version_from_env(self):
        with mock.patch.dict("os.environ",
                             {"DUCKLAB_RELEASE_ID": "2026.10.25"}):
            self.assertEqual(app_version(), "2026.10.25")

    def test_version_dev_env_falls_back(self):
        # "dev" is the unset marker: fall back to git or "Unknown".
        with mock.patch.dict("os.environ", {"DUCKLAB_RELEASE_ID": "dev"}):
            v = app_version()
        self.assertTrue(v)
        self.assertNotEqual(v, "dev")


if __name__ == "__main__":
    unittest.main()
