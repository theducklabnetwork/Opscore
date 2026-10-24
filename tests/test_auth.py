"""Tests for auth_db, auth_service, authz, and app_service.

Temp DBs only. Run: python -m unittest tests.test_auth -v
"""

import re
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ducklab import auth_db, store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import (
    AuthService, AuthenticationError, AuthorizationError, NotFoundError,
    RateLimitError, User, ValidationError, _RateLimiter,
)
from ducklab.operations_snapshots import SnapshotNotFoundError

PW = "Sup3rSecure!x"
PW2 = "An0therGood42"


class AuthFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "auth_test.db"
        self.app = AppService(self.db_path)
        self.auth: AuthService = self.app.auth
        self.admin = self.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)

    def tearDown(self):
        self._tmp.cleanup()

    def make_user(self, email="user@ducklab.com", roles=(), stores=(),
                  password=PW2) -> User:
        u = self.auth.create_user(email, "Test User", password, actor=self.admin)
        for r in roles:
            self.auth.assign_role(self.admin, u.id, r)
        for s in stores:
            self.auth.assign_store(self.admin, u.id, s)
        return self.auth.get_user(u.id)

    def make_store(self, code="A01") -> int:
        sid = self.app.write.register_store(code, f"Store {code}")
        self.app.write.configure_month(sid, "2026-09",
                                       level1_base_cents=20000000, level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                                       opening_headcount_ft=9)
        return sid

    def login(self, email, password):
        token, user = self.auth.login(email, password)
        return token, user

    def conn(self):
        return store_ops_db.connect(self.db_path)


# ---------------------------------------------------------------------------
class TestUserManagement(AuthFixture):
    def test_create_user_hashes_password(self):
        u = self.make_user()
        conn = self.conn()
        row = conn.execute("SELECT password_hash FROM auth_users WHERE id=?",
                           (u.id,)).fetchone()
        conn.close()
        self.assertTrue(row[0].startswith("$argon2id$"))
        self.assertNotIn(PW2, row[0])

    def test_email_normalized_and_unique(self):
        self.make_user("MixedCase@DuckLab.com")
        with self.assertRaises(ValidationError):
            self.auth.create_user(" mixedcase@ducklab.COM ", "Dup", PW2,
                                  actor=self.admin)

    def test_invalid_email_rejected(self):
        with self.assertRaises(ValidationError):
            self.auth.create_user("not-an-email", "X", PW2, actor=self.admin)

    def test_weak_passwords_rejected(self):
        for weak in ("short1", "nodigits" * 3, "12345678901"):
            with self.assertRaises(ValidationError):
                self.auth.create_user(f"w{len(weak)}@ducklab.com", "X", weak,
                                      actor=self.admin)

    def test_list_users_hides_hashes(self):
        self.make_user()
        users = self.auth.list_users()
        self.assertGreaterEqual(len(users), 2)
        self.assertFalse(hasattr(users[0], "password_hash"))


class TestAuthentication(AuthFixture):
    def test_login_success_returns_token_and_user(self):
        self.make_user()
        token, user = self.login("user@ducklab.com", PW2)
        self.assertTrue(len(token) > 32)
        self.assertEqual(user.email, "user@ducklab.com")

    def test_login_case_insensitive_email(self):
        self.make_user()
        token, _ = self.login("USER@DUCKLAB.COM", PW2)
        self.assertTrue(token)

    def test_wrong_password_generic_error(self):
        self.make_user()
        with self.assertRaises(AuthenticationError) as cm:
            self.auth.login("user@ducklab.com", "WrongPass123")
        self.assertEqual(str(cm.exception), "Invalid email or password")

    def test_unknown_email_same_generic_error(self):
        with self.assertRaises(AuthenticationError) as cm:
            self.auth.login("ghost@ducklab.com", PW2)
        self.assertEqual(str(cm.exception), "Invalid email or password")

    def test_disabled_user_rejected(self):
        u = self.make_user()
        self.auth.set_active(self.admin, u.id, False)
        with self.assertRaises(AuthenticationError):
            self.auth.login("user@ducklab.com", PW2)

    def test_rate_limiting(self):
        self.make_user()
        limiter = _RateLimiter(max_attempts=3, window=60)
        auth = AuthService(self.db_path, limiter=limiter)
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                auth.login("user@ducklab.com", "BadPass123")
        # Even the correct password is now refused within the window.
        with self.assertRaises(RateLimitError):
            auth.login("user@ducklab.com", PW2)

    def test_session_token_stored_hashed(self):
        self.make_user()
        token, _ = self.login("user@ducklab.com", PW2)
        conn = self.conn()
        raw = [r[0] for r in conn.execute("SELECT token_hash FROM auth_sessions")]
        conn.close()
        self.assertNotIn(token, raw)
        self.assertTrue(all(re.fullmatch(r"[0-9a-f]{64}", h) for h in raw))

    def test_authenticate_token_roundtrip(self):
        self.make_user()
        token, user = self.login("user@ducklab.com", PW2)
        again = self.auth.authenticate_token(token)
        self.assertEqual(again.id, user.id)

    def test_invalid_and_missing_tokens_rejected(self):
        for bad in ("", "nonsense-token", None):
            with self.assertRaises(AuthenticationError):
                self.auth.authenticate_token(bad)

    def test_expired_session_rejected(self):
        self.make_user()
        token, _ = self.login("user@ducklab.com", PW2)
        conn = self.conn()
        conn.execute("UPDATE auth_sessions SET expires_at = ?",
                     ((datetime.now(timezone.utc) - timedelta(hours=1))
                      .strftime("%Y-%m-%d %H:%M:%S"),))
        conn.commit(); conn.close()
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate_token(token)

    def test_logout_revokes(self):
        self.make_user()
        token, _ = self.login("user@ducklab.com", PW2)
        self.auth.logout(token)
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate_token(token)

    def test_deactivation_revokes_sessions(self):
        self.make_user()
        token, u = self.login("user@ducklab.com", PW2)
        self.auth.set_active(self.admin, u.id, False)
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate_token(token)

    def test_password_change_revokes_sessions(self):
        self.make_user()
        token, u = self.login("user@ducklab.com", PW2)
        self.auth.change_password(self.admin, u.id, "N3wPassword!")
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate_token(token)
        # New password works.
        self.login("user@ducklab.com", "N3wPassword!")

    def test_self_password_change_requires_current(self):
        self.make_user()
        _, u = self.login("user@ducklab.com", PW2)
        with self.assertRaises(AuthenticationError):
            self.auth.change_password(u, u.id, "N3wPassword!",
                                      self_change=True,
                                      current_password="Wrong1234")
        self.auth.change_password(u, u.id, "N3wPassword!",
                                  self_change=True, current_password=PW2)


class TestBootstrap(AuthFixture):
    def test_bootstrap_refused_when_super_admin_exists(self):
        with self.assertRaises(ValidationError):
            self.auth.bootstrap_first_admin("second@ducklab.com", "Second", PW2)

    def test_bootstrap_creates_super_admin(self):
        self.assertIn("super_admin", self.admin.roles)


class TestBootstrapCleanDB(unittest.TestCase):
    def test_bootstrap_on_empty_db(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "fresh.db"
            auth = AuthService(db)
            u = auth.bootstrap_first_admin("root@ducklab.com", "Root", PW)
            self.assertIn("super_admin", u.roles)
            # Login works immediately after bootstrap.
            token, user = auth.login("root@ducklab.com", PW)
            self.assertEqual(user.id, u.id)


class TestRolesAndStores(AuthFixture):
    def test_last_super_admin_protection(self):
        with self.assertRaises(ValidationError):
            self.auth.set_active(self.admin, self.admin.id, False)
        with self.assertRaises(ValidationError):
            self.auth.revoke_role(self.admin, self.admin.id, "super_admin")
        # A second super admin releases the protection.
        second = self.make_user("sa2@ducklab.com", roles=["super_admin"])
        self.auth.revoke_role(self.admin, second.id, "super_admin")  # ok
        self.assertNotIn("super_admin", self.auth.get_user(second.id).roles)

    def test_unknown_role_rejected(self):
        u = self.make_user()
        with self.assertRaises(NotFoundError):
            self.auth.assign_role(self.admin, u.id, "god_mode")

    def test_store_assignment_unknown_store(self):
        u = self.make_user()
        with self.assertRaises(NotFoundError):
            self.auth.assign_store(self.admin, u.id, 9999)

    def test_revoke_store(self):
        sid = self.make_store()
        u = self.make_user(stores=[sid])
        self.assertIn(sid, u.store_ids)
        u2 = self.auth.revoke_store(self.admin, u.id, sid)
        self.assertNotIn(sid, u2.store_ids)


class TestAuthorization(AuthFixture):
    def setUp(self):
        super().setUp()
        self.store_a = self.make_store("A01")
        self.store_b = self.make_store("B02")

    def user_with(self, role, stores):
        return self.make_user(f"{role}-{len(stores)}@ducklab.com",
                              roles=[role], stores=stores)

    def test_viewer_read_only(self):
        v = self.user_with("viewer", [self.store_a])
        report = self.app.preview_month(v, self.store_a, "2026-09",
                                        as_of=date(2026, 9, 30))
        self.assertIsNotNone(report)
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(v, self.store_a, date(2026, 9, 1),
                                  sales_cents=100)
        with self.assertRaises(AuthorizationError):
            self.app.finalize_month(v, self.store_a, "2026-09",
                                    as_of=date(2026, 9, 30))
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(v, self.store_a, "2026-10")

    def test_branch_manager_scoped(self):
        bm = self.user_with("branch_manager", [self.store_a])
        self.app.record_daily(bm, self.store_a, date(2026, 9, 1),
                              sales_cents=500)
        with self.assertRaises(AuthorizationError):  # cross-store write
            self.app.record_daily(bm, self.store_b, date(2026, 9, 1),
                                  sales_cents=500)
        with self.assertRaises(AuthorizationError):  # cross-store read
            self.app.preview_month(bm, self.store_b, "2026-09")
        with self.assertRaises(AuthorizationError):  # no finalize
            self.app.finalize_month(bm, self.store_a, "2026-09",
                                    as_of=date(2026, 9, 30))
        with self.assertRaises(AuthorizationError):  # no config
            self.app.configure_month(bm, self.store_a, "2026-10")

    def test_zone_manager_no_finalize_no_users(self):
        zm = self.user_with("zone_manager", [self.store_a, self.store_b])
        self.app.preview_month(zm, self.store_b, "2026-09")
        with self.assertRaises(AuthorizationError):
            self.app.finalize_month(zm, self.store_a, "2026-09",
                                    as_of=date(2026, 9, 30))
        with self.assertRaises(AuthorizationError):
            self.app.list_users(zm)

    def test_administrator_finalize_but_no_user_mgmt(self):
        adm = self.user_with("administrator", [self.store_a])
        self.app.finalize_month(adm, self.store_a, "2026-09",
                                as_of=date(2026, 9, 30))
        with self.assertRaises(AuthorizationError):
            self.app.finalize_month(adm, self.store_b, "2026-09",
                                    as_of=date(2026, 9, 30))
        with self.assertRaises(AuthorizationError):
            self.app.create_user(adm, "nope@ducklab.com", "X", PW2)
        with self.assertRaises(AuthorizationError):
            self.app.assign_role(adm, adm.id, "super_admin")

    def test_privilege_escalation_rejected(self):
        v = self.user_with("viewer", [self.store_a])
        with self.assertRaises(AuthorizationError):
            self.app.assign_role(v, v.id, "super_admin")
        with self.assertRaises(AuthorizationError):
            self.app.assign_store(v, v.id, self.store_b)

    def test_super_admin_all_stores(self):
        stores = self.app.list_my_stores(self.admin)
        self.assertEqual({s["id"] for s in stores},
                         {self.store_a, self.store_b})

    def test_scoped_store_listing(self):
        bm = self.user_with("branch_manager", [self.store_a])
        stores = self.app.list_my_stores(bm)
        self.assertEqual([s["id"] for s in stores], [self.store_a])

    def test_unauthorized_snapshot_access(self):
        self.app.finalize_month(self.admin, self.store_a, "2026-09",
                                as_of=date(2026, 9, 30))
        bm = self.user_with("branch_manager", [self.store_b])
        with self.assertRaises(AuthorizationError):
            self.app.get_finalized_report(bm, self.store_a, "2026-09")
        with self.assertRaises(AuthorizationError):
            self.app.list_report_versions(bm, self.store_a, "2026-09")

    def test_disabled_user_loses_all_permissions(self):
        bm = self.user_with("branch_manager", [self.store_a])
        self.auth.set_active(self.admin, bm.id, False)
        bm = self.auth.get_user(bm.id)
        with self.assertRaises(AuthorizationError):
            self.app.preview_month(bm, self.store_a, "2026-09")

    def test_self_disable_rejected_via_boundary(self):
        with self.assertRaises(ValidationError):
            self.app.set_user_active(self.admin, self.admin.id, False)


class TestAudit(AuthFixture):
    def events(self):
        conn = self.conn()
        rows = conn.execute(
            "SELECT event_type, outcome, metadata FROM auth_audit_events "
            "ORDER BY id").fetchall()
        conn.close()
        return [(r[0], r[1], r[2]) for r in rows]

    def test_audit_coverage_and_redaction(self):
        u = self.make_user()
        try:
            self.auth.login("user@ducklab.com", "BadPass123")
        except AuthenticationError:
            pass
        token, _ = self.login("user@ducklab.com", PW2)
        self.auth.logout(token)
        ev = self.events()
        types = [e[0] for e in ev]
        for expected in ("bootstrap_first_admin", "user_create",
                         "login", "logout"):
            self.assertIn(expected, types)
        self.assertIn(("login", "failure"),
                      [(t, o) for t, o, _ in ev if t == "login"])
        # No secrets anywhere in the audit trail.
        joined = " ".join(m for _, _, m in ev)
        for secret in (PW2, "BadPass123", token):
            self.assertNotIn(secret, joined)
            self.assertNotIn("$argon2", joined)


class TestEndToEnd(AuthFixture):
    def test_full_flow(self):
        sid = self.make_store()
        bm = self.make_user("bm@ducklab.com", roles=["branch_manager"],
                            stores=[sid])
        token, _ = self.login("bm@ducklab.com", PW2)
        actor = self.app.authenticate(token)
        # Authorized Branch Manager writes permitted data.
        self.app.record_daily(actor, sid, date(2026, 9, 1), sales_cents=1000000)
        # Authorized report access works.
        report = self.app.preview_month(actor, sid, "2026-09",
                                        as_of=date(2026, 9, 30))
        self.assertEqual(report.sales.sales_mtd_cents, 1000000)
        # Finalization by admin; then branch manager can read the snapshot.
        self.app.finalize_month(self.admin, sid, "2026-09",
                                as_of=date(2026, 9, 30))
        snap = self.app.get_finalized_report(actor, sid, "2026-09")
        self.assertEqual(snap["report"]["sales"]["sales_mtd_cents"], 1000000)
        # Logout removes protected access.
        self.auth.logout(token)
        with self.assertRaises(AuthenticationError):
            self.app.authenticate(token)


if __name__ == "__main__":
    unittest.main()
