"""Recovery-key flow: generation, hashing, single-use recovery, regeneration,
HTTP transport, and the recovery-kit file writer.

Temp DBs only. Run: python -m unittest tests.test_recovery_key -v
"""

import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ducklab import auth_service, platform_utils, store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import (
    AuthService, AuthenticationError, ValidationError,
    generate_recovery_key, is_recovery_key_format, normalize_recovery_key,
)

PW = "Sup3rSecure!x"
PW2 = "An0therGood42"
NEW_PW = "RecoveredPass99"


class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "recovery_test.db"
        self.app = AppService(self.db_path)
        self.auth: AuthService = self.app.auth
        self.admin = self.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)
        self.key = self.admin.recovery_key

    def tearDown(self):
        self._tmp.cleanup()

    def conn(self):
        return store_ops_db.connect(self.db_path)


class TestKeyFormat(unittest.TestCase):
    def test_generated_key_matches_format(self):
        for _ in range(50):
            key = generate_recovery_key()
            self.assertTrue(is_recovery_key_format(key), key)
            self.assertTrue(key.startswith("DLAB-"))
            self.assertEqual(len(key), 4 + 7 * 6)  # prefix + 7 groups

    def test_normalization_case_and_whitespace(self):
        key = generate_recovery_key()
        messy = "  " + key.lower().replace("-", " - ") + "\n"
        self.assertEqual(normalize_recovery_key(messy), key)

    def test_invalid_formats_rejected(self):
        for bad in ("", "DLAB", "DLAB-AAAAA", "DLAB-AAAAA-AAAAA",
                    "XXXX-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA",
                    "DLAB-AAAA!-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA-AAAAA"):
            self.assertFalse(is_recovery_key_format(bad), bad)

    def test_keys_are_random_and_distinct(self):
        keys = {generate_recovery_key() for _ in range(100)}
        self.assertEqual(len(keys), 100)


class TestBootstrapKey(RecoveryFixture):
    def test_bootstrap_returns_raw_key_once(self):
        self.assertTrue(is_recovery_key_format(self.key))

    def test_only_hash_stored(self):
        conn = self.conn()
        try:
            row = conn.execute(
                "SELECT key_hash, used_at FROM auth_recovery_keys "
                "WHERE user_id = ?", (self.admin.id,)).fetchone()
        finally:
            conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["key_hash"],
                         auth_service._hash_recovery_key(self.key))
        self.assertNotIn(self.key, row["key_hash"])
        self.assertIsNone(row["used_at"])

    def test_later_user_loads_do_not_carry_raw_key(self):
        again = self.auth.get_user(self.admin.id)
        self.assertEqual(again.recovery_key, "")
        _token, user = self.auth.login("admin@ducklab.com", PW)
        self.assertEqual(user.recovery_key, "")

    def test_has_recovery_key(self):
        self.assertTrue(self.auth.has_recovery_key(self.admin.id))


class TestRecoverPassword(RecoveryFixture):
    def test_successful_recovery_rotates_password(self):
        new_key = self.auth.recover_password(self.key, NEW_PW)
        self.auth.login("admin@ducklab.com", NEW_PW)
        with self.assertRaises(AuthenticationError):
            self.auth.login("admin@ducklab.com", PW)
        self.assertTrue(is_recovery_key_format(new_key))
        self.assertNotEqual(new_key, self.key)

    def test_key_is_single_use(self):
        self.auth.recover_password(self.key, NEW_PW)
        with self.assertRaises(AuthenticationError) as cm:
            self.auth.recover_password(self.key, "An0therTry42")
        self.assertEqual(str(cm.exception), "Invalid recovery key")

    def test_replacement_key_works_next_time(self):
        replacement = self.auth.recover_password(self.key, NEW_PW)
        self.auth.recover_password(replacement, "YetAn0therPass77")
        self.auth.login("admin@ducklab.com", "YetAn0therPass77")

    def test_recovery_revokes_sessions(self):
        token, _ = self.auth.login("admin@ducklab.com", PW)
        self.auth.recover_password(self.key, NEW_PW)
        with self.assertRaises(AuthenticationError):
            self.auth.authenticate_token(token)

    def test_wrong_key_generic_error(self):
        other = generate_recovery_key()
        with self.assertRaises(AuthenticationError) as cm:
            self.auth.recover_password(other, NEW_PW)
        self.assertEqual(str(cm.exception), "Invalid recovery key")
        # Original key untouched.
        self.auth.recover_password(self.key, NEW_PW)

    def test_malformed_key_rejected_without_db_write(self):
        with self.assertRaises(AuthenticationError):
            self.auth.recover_password("not-a-key", NEW_PW)
        # Key remains usable.
        self.auth.recover_password(self.key, NEW_PW)

    def test_weak_new_password_rejected(self):
        with self.assertRaises(ValidationError):
            self.auth.recover_password(self.key, "short1")
        # Key NOT consumed by a failed validation.
        self.auth.recover_password(self.key, NEW_PW)

    def test_key_normalized_on_recovery(self):
        messy = self.key.lower().replace("-", " - ")
        self.auth.recover_password(messy, NEW_PW)
        self.auth.login("admin@ducklab.com", NEW_PW)

    def test_recovery_audited_without_secrets(self):
        self.auth.recover_password(self.key, NEW_PW)
        conn = self.conn()
        try:
            rows = conn.execute(
                "SELECT event_type, metadata FROM auth_audit_events").fetchall()
        finally:
            conn.close()
        types = [r["event_type"] for r in rows]
        self.assertIn("password_recovery", types)
        self.assertIn("recovery_key_issue", types)
        joined = " ".join(r["metadata"] for r in rows)
        self.assertNotIn(self.key, joined)
        self.assertNotIn(NEW_PW, joined)
        self.assertNotIn(auth_service._hash_recovery_key(self.key), joined)

    def test_recovery_key_rate_limited(self):
        for _ in range(auth_service.LOGIN_MAX_ATTEMPTS):
            with self.assertRaises(AuthenticationError):
                self.auth.recover_password(generate_recovery_key(), NEW_PW,
                                           limiter_key="ip:test")
        # The next attempt — even with the CORRECT key — is limited.
        from ducklab.auth_service import RateLimitError
        with self.assertRaises(RateLimitError):
            self.auth.recover_password(self.key, NEW_PW, limiter_key="ip:test")


class TestRegenerateKey(RecoveryFixture):
    def test_regeneration_revokes_previous_key(self):
        new_key = self.auth.regenerate_recovery_key(self.admin.id)
        self.assertNotEqual(new_key, self.key)
        with self.assertRaises(AuthenticationError):
            self.auth.recover_password(self.key, NEW_PW)
        self.auth.recover_password(new_key, NEW_PW)

    def test_regenerate_unknown_user(self):
        from ducklab.auth_service import NotFoundError
        with self.assertRaises(NotFoundError):
            self.auth.regenerate_recovery_key(9999)

    def test_verify_password_helper(self):
        self.assertTrue(self.auth.verify_password("admin@ducklab.com", PW))
        self.assertFalse(self.auth.verify_password("admin@ducklab.com", "x"))
        self.assertFalse(self.auth.verify_password("ghost@ducklab.com", PW))
        # No session is created as a side effect.
        conn = self.conn()
        try:
            n = conn.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(n, 0)


class TestRecoveryKitFile(unittest.TestCase):
    def test_write_kit_contents_and_permissions(self):
        key = generate_recovery_key()
        with tempfile.TemporaryDirectory() as tmp:
            path = platform_utils.write_recovery_kit(
                key, "a@ducklab.com", directory=Path(tmp) / "kit")
            self.assertTrue(path.is_file())
            body = path.read_text(encoding="utf-8")
            self.assertIn(key, body)
            self.assertIn("a@ducklab.com", body)
            self.assertIn("single-use", body)
            if os.name == "posix":
                import stat
                mode = stat.S_IMODE(path.stat().st_mode)
                self.assertEqual(mode, 0o600)
                dmode = stat.S_IMODE(path.parent.stat().st_mode)
                self.assertEqual(dmode, 0o700)

    def test_write_kit_overwrites_previous(self):
        k1, k2 = generate_recovery_key(), generate_recovery_key()
        with tempfile.TemporaryDirectory() as tmp:
            p1 = platform_utils.write_recovery_kit(k1, "a@x.dev",
                                                   directory=Path(tmp))
            platform_utils.write_recovery_kit(k2, "a@x.dev",
                                              directory=Path(tmp))
            self.assertEqual(p1.read_text().count("DLAB-"), 1)
            self.assertIn(k2, p1.read_text())
            self.assertNotIn(k1, p1.read_text())


class TestRecoveryHTTP(RecoveryFixture):
    """HTTP-level tests for action=recover and the bootstrap key payload."""

    def setUp(self):
        super().setUp()
        from starlette.applications import Starlette
        from starlette.routing import Route
        from starlette.testclient import TestClient
        import ducklab.session_transport as st
        self._old_auth = st.auth
        st.auth = self.auth
        inner = Starlette(routes=[Route("/api/session",
                                        st.session_endpoint,
                                        methods=["POST"])])
        self.client = TestClient(inner, base_url="http://app.theducklab.com")

    def tearDown(self):
        import ducklab.session_transport as st
        st.auth = self._old_auth
        super().tearDown()

    def _post(self, payload):
        return self.client.post(
            "/api/session", json=payload,
            headers={"Origin": "http://app.theducklab.com"})

    def test_recover_success_returns_replacement_key(self):
        r = self._post({"action": "recover", "recovery_key": self.key,
                        "password": NEW_PW})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["ok"])
        self.assertTrue(is_recovery_key_format(body["recovery_key"]))
        # No session cookie is set by recovery.
        self.assertNotIn("set-cookie", r.headers)
        # New password signs in.
        self.auth.login("admin@ducklab.com", NEW_PW)

    def test_recover_invalid_key_generic_401(self):
        r = self._post({"action": "recover",
                        "recovery_key": generate_recovery_key(),
                        "password": NEW_PW})
        self.assertEqual(r.status_code, 401)
        self.assertEqual(r.json()["error"], "Invalid recovery key")

    def test_recover_weak_password_400(self):
        r = self._post({"action": "recover", "recovery_key": self.key,
                        "password": "short1"})
        self.assertEqual(r.status_code, 400)

    def test_recover_requires_matching_origin(self):
        r = self.client.post(
            "/api/session",
            json={"action": "recover", "recovery_key": self.key,
                  "password": NEW_PW})
        self.assertEqual(r.status_code, 403)

    def test_bootstrap_response_contains_recovery_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            fresh = AuthService(Path(tmp) / "fresh.db")
            import ducklab.session_transport as st
            old = st.auth
            st.auth = fresh
            try:
                r = self._post({"action": "bootstrap",
                                "name": "Root", "email": "root@ducklab.com",
                                "password": PW})
            finally:
                st.auth = old
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(is_recovery_key_format(body["recovery_key"]))


if __name__ == "__main__":
    unittest.main()
