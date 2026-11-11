"""Tests for the offline admin password-recovery script.

Uses a disposable temp DB and drives scripts/recover_admin_password.py with
mocked interactive input. Verifies the password actually rotates (new one
verifies, old one fails), live sessions are revoked, a backup is written,
other accounts/data are untouched, and the safety guards hold.
"""

import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService

ROOT = Path(__file__).resolve().parent.parent
OLD_PW = "OldPassword1"
NEW_PW = "NewPassword9"


def _load():
    spec = importlib.util.spec_from_file_location(
        "recover_admin_password", ROOT / "scripts" / "recover_admin_password.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class RecoverAdminPasswordTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "store_ops.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", OLD_PW)
        self.mod = _load()

    def tearDown(self):
        self._tmp.cleanup()

    def _login(self, email, password):
        return self.app.auth.login(email, password)

    def _run(self, email, pw1, pw2, db=None):
        inputs = [email]
        with mock.patch.dict("os.environ",
                             {"DUCKLAB_DB_PATH": str(db or self.db)}), \
             mock.patch("builtins.input", side_effect=inputs), \
             mock.patch("getpass.getpass", side_effect=[pw1, pw2]):
            return self.mod.main()

    def test_successful_recovery_rotates_password_and_revokes_sessions(self):
        token, _ = self._login("admin@ducklab.com", OLD_PW)
        rc = self._run("admin@ducklab.com", NEW_PW, NEW_PW)
        self.assertEqual(rc, 0)
        # New password works, old one fails.
        self._login("admin@ducklab.com", NEW_PW)
        with self.assertRaises(Exception):
            self._login("admin@ducklab.com", OLD_PW)
        # The prior session was revoked.
        conn = sqlite3.connect(self.db)
        try:
            live = conn.execute(
                "SELECT COUNT(*) FROM auth_sessions WHERE token_hash = ? "
                "AND revoked_at IS NULL",
                (__import__("hashlib").sha256(token.encode()).hexdigest(),)
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(live, 0)
        # An audit event was recorded.
        conn = sqlite3.connect(self.db)
        try:
            n = conn.execute(
                "SELECT COUNT(*) FROM auth_audit_events "
                "WHERE event_type = 'password_change' AND outcome = 'success'"
            ).fetchone()[0]
        finally:
            conn.close()
        self.assertGreaterEqual(n, 1)

    def test_backup_written(self):
        rc = self._run("admin@ducklab.com", NEW_PW, NEW_PW)
        self.assertEqual(rc, 0)
        backups = list(self.db.parent.glob("store_ops.db.bak-*"))
        self.assertEqual(len(backups), 1)
        self.assertGreater(backups[0].stat().st_size, 0)

    def test_unknown_email_refused(self):
        rc = self._run("nobody@ducklab.com", NEW_PW, NEW_PW)
        self.assertEqual(rc, 1)
        # Original password still works (nothing changed).
        self._login("admin@ducklab.com", OLD_PW)
        self.assertEqual(list(self.db.parent.glob("*.bak-*")), [])

    def test_password_mismatch_refused(self):
        rc = self._run("admin@ducklab.com", NEW_PW, "Different1")
        self.assertEqual(rc, 1)
        self._login("admin@ducklab.com", OLD_PW)  # unchanged
        self.assertEqual(list(self.db.parent.glob("*.bak-*")), [])

    def test_other_accounts_and_data_untouched(self):
        other = self.app.create_user(self.admin, "viewer@ducklab.com",
                                     "Viewer", "ViewerPass1")
        sid = self.app.register_store(self.admin, "A01", "Alpha")
        rc = self._run("admin@ducklab.com", NEW_PW, NEW_PW)
        self.assertEqual(rc, 0)
        # Other account still logs in with its original password.
        self._login("viewer@ducklab.com", "ViewerPass1")
        # Store and the default seeded categories survive.
        self.assertEqual(self.app.get_store(self.admin, sid).code, "A01")
        self.assertEqual(len(self.app.get_expense_categories(self.admin, sid)), 10)

    def test_symlinked_database_refused(self):
        link = Path(self._tmp.name) / "link.db"
        try:
            link.symlink_to(self.db)
        except (OSError, NotImplementedError):
            self.skipTest("symlinks not permitted on this platform")
        with self.assertRaises(SystemExit):
            self._run("admin@ducklab.com", NEW_PW, NEW_PW, db=link)

    def test_missing_database_refused(self):
        missing = Path(self._tmp.name) / "nope.db"
        rc = self._run("admin@ducklab.com", NEW_PW, NEW_PW, db=missing)
        self.assertEqual(rc, 1)


if __name__ == "__main__":
    unittest.main()
