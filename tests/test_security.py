"""Security hardening regression tests: session transport, cookies,
CSRF, WebSocket origin checks, rate limiting, legacy route exposure.

Temp DBs only. Run: python -m unittest tests.test_security -v
"""

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

# Point the app DB at a temp file BEFORE importing app modules.
_TMP = tempfile.TemporaryDirectory()
os.environ["DUCKLAB_DB_PATH"] = str(Path(_TMP.name) / "sec_test.db")
os.environ.pop("DUCKLAB_SECURE_COOKIES", None)
os.environ.pop("DUCKLAB_TRUST_PROXY", None)

from starlette.testclient import TestClient  # noqa: E402

from ducklab import auth_service, session_transport  # noqa: E402
from ducklab.app_service import AppService  # noqa: E402
from ducklab.auth_service import (  # noqa: E402
    AuthenticationError, AuthorizationError, RateLimitError, _RateLimiter,
)
from ducklab.session_transport import (  # noqa: E402
    OriginCheckMiddleware, SESSION_COOKIE, _same_site_origin,
    asgi_transformer, read_session_token,
)


class TestSecurityEnv(unittest.TestCase):
    def test_secure_cookies_flag(self):
        with mock.patch.dict(os.environ, {"DUCKLAB_SECURE_COOKIES": "1"}):
            from ducklab.security import SecurityConfig
            self.assertTrue(SecurityConfig.from_env().secure_cookies)

    def test_trust_proxy_flag(self):
        with mock.patch.dict(os.environ, {"DUCKLAB_TRUST_PROXY": "1"}):
            from ducklab.security import SecurityConfig
            self.assertTrue(SecurityConfig.from_env().trust_proxy)

    def test_frontend_origins_empty_by_default(self):
        with mock.patch.dict(os.environ, clear=False) as env:
            env.pop("DUCKLAB_FRONTEND_ORIGINS", None)
            from ducklab.security import SecurityConfig
            self.assertEqual(SecurityConfig.from_env().allowed_frontend_origins, ())


PW = "Sup3rSecure!x"
PW2 = "An0therGood42"


def _make_app_service(db_path: Path) -> AppService:
    return AppService(db_path)


class EndpointFixture(unittest.TestCase):
    """HTTP-level tests against the /api/session endpoint."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._TMP2.name) if False else Path(self._tmp.name) / "t.db"
        self.app = _make_app_service(self.db_path)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)
        from starlette.applications import Starlette
        from starlette.routing import Route
        import ducklab.session_transport as st
        # Rewire the module singletons to the temp DB for this test.
        self._old_auth, self._old_db = st.auth, st.DB_PATH
        st.auth = self.app.auth
        st.DB_PATH = self.db_path
        inner = Starlette(routes=[Route("/api/session",
                                        st.session_endpoint,
                                        methods=["POST"])])
        self.client = TestClient(inner, base_url="http://opscore.test")

    def tearDown(self):
        import ducklab.session_transport as st
        st.auth, st.DB_PATH = self._old_auth, self._old_db
        self._tmp.cleanup()

    def _login(self, email="admin@ducklab.com", password=PW, origin=True):
        headers = {}
        if origin:
            headers["Origin"] = "http://opscore.test"
        return self.client.post(
            "/api/session",
            json={"action": "login", "email": email, "password": password},
            headers=headers)


class TestCookieAttributes(EndpointFixture):
    def test_httponly_samesite_path_maxage(self):
        r = self._login()
        self.assertEqual(r.status_code, 200)
        sc = r.headers["set-cookie"]
        self.assertIn("HttpOnly", sc)
        self.assertIn("SameSite=Lax", sc)
        self.assertIn("Path=/", sc)
        self.assertIn(f"Max-Age={int(auth_service.SESSION_TTL.total_seconds())}",
                      sc)

    def test_secure_attribute_with_env_flag(self):
        import ducklab.session_transport as st
        from ducklab.security import SecurityConfig
        old = st.config
        st.config = SecurityConfig(secure_cookies=True)
        try:
            r = self._login()
            self.assertIn("Secure", r.headers["set-cookie"])
        finally:
            st.config = old

    def test_no_token_in_response_body(self):
        r = self._login()
        self.assertNotIn(SESSION_COOKIE, r.text)
        token = r.cookies.get(SESSION_COOKIE)
        self.assertTrue(token)
        self.assertNotIn(token, r.text)


class TestSessionLifecycle(EndpointFixture):
    def test_logout_revokes_session(self):
        r = self._login()
        token = r.cookies.get(SESSION_COOKIE)
        self.assertTrue(token)
        self.app.auth.authenticate_token(token)  # valid
        self.client.post(
            "/api/session", json={"action": "logout"},
            headers={"Origin": "http://opscore.test",
                     "Cookie": f"{SESSION_COOKIE}={token}"})
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate_token(token)

    def test_logout_clears_cookie(self):
        r = self._login()
        token = r.cookies.get(SESSION_COOKIE)
        self.assertTrue(token)
        resp = self.client.post(
            "/api/session", json={"action": "logout"},
            headers={"Origin": "http://opscore.test",
                     "Cookie": f"{SESSION_COOKIE}={token}"})
        self.assertIn("Max-Age=0", resp.headers["set-cookie"])

    def test_password_change_invalidates_session(self):
        r = self._login()
        token = r.cookies.get(SESSION_COOKIE)
        self.app.auth.change_password(
            self.admin, self.admin.id, "N3wPassword!zz1",
            self_change=True, current_password=PW)
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate_token(token)

    def test_disabled_user_session_rejected(self):
        r = self._login()
        token = r.cookies.get(SESSION_COOKIE)
        # create a second admin so we can disable the first
        other = self.app.auth.create_user("b@ducklab.com", "B", PW2,
                                          actor=self.admin)
        self.app.auth.assign_role(self.admin, other.id, "super_admin")
        self.app.auth.set_active(other, self.admin.id, False)
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate_token(token)

    def test_missing_and_invalid_token_rejected(self):
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate_token("")
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate_token("not-a-real-token")

    def test_expired_session_rejected(self):
        import sqlite3
        from datetime import datetime, timedelta, timezone
        r = self._login()
        token = r.cookies.get(SESSION_COOKIE)
        conn = self.app.auth._connect()
        try:
            past = (datetime.now(timezone.utc) - timedelta(hours=1)
                    ).strftime("%Y-%m-%d %H:%M:%S")
            conn.execute(
                "UPDATE auth_sessions SET expires_at = ? "
                "WHERE token_hash = ?",
                (past, auth_service._hash_token(token)))
            conn.commit()
        finally:
            conn.close()
        with self.assertRaises(AuthenticationError):
            self.app.auth.authenticate_token(token)

    def test_session_fixation_prevented_cookie_rotates(self):
        r1 = self._login()
        r2 = self._login()
        self.assertNotEqual(r1.cookies.get(SESSION_COOKIE),
                            r2.cookies.get(SESSION_COOKIE))

    def test_wrong_password_rejected(self):
        r = self._login(password="Wr0ngPassword1")
        self.assertEqual(r.status_code, 401)
        self.assertNotIn("set-cookie", r.headers)


class TestCSRF(EndpointFixture):
    def test_login_rejected_without_matching_origin(self):
        r = self._login(origin=False)
        self.assertEqual(r.status_code, 403)

    def test_login_rejected_cross_site_origin(self):
        r = self.client.post(
            "/api/session",
            json={"action": "login", "email": "admin@ducklab.com",
                  "password": PW},
            headers={"Host": "opscore.test",
                     "Origin": "https://evil.example.com"})
        self.assertEqual(r.status_code, 403)
        self.assertNotIn("set-cookie", r.headers)

    def test_same_site_origin_helper(self):
        self.assertTrue(_same_site_origin("https://opscore.test",
                                          "opscore.test"))
        self.assertTrue(_same_site_origin("http://localhost:3000",
                                          "localhost:3000"))
        self.assertFalse(_same_site_origin("https://evil.com",
                                           "opscore.test"))
        self.assertFalse(_same_site_origin("", "opscore.test"))

    def test_allowed_frontend_origins_dev(self):
        """Dev-mode allow-list permits the Vite frontend origin only."""
        import os as _os
        from ducklab import security, session_transport
        _os.environ["DUCKLAB_FRONTEND_ORIGINS"] = "http://localhost:3000"
        try:
            session_transport.config = security.SecurityConfig.from_env()
            self.assertTrue(_same_site_origin("http://localhost:3000",
                                              "localhost:8000"))
            # other origins still rejected
            self.assertFalse(_same_site_origin("https://evil.com",
                                               "localhost:8000"))
            # a different port on the same host is rejected (no wildcard)
            self.assertFalse(_same_site_origin("http://localhost:9999",
                                               "example.com:8000"))
        finally:
            _os.environ.pop("DUCKLAB_FRONTEND_ORIGINS", None)
            session_transport.config = security.SecurityConfig.from_env()
        # without the env var, a genuinely cross-host origin is rejected
        self.assertFalse(_same_site_origin("https://evil.example.com",
                                           "localhost:8000"))


class TestRateLimiting(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "rl.db"

    def tearDown(self):
        self._tmp.cleanup()

    def test_persistent_limiter_blocks_after_max(self):
        lim = _RateLimiter(3, 300, db_path=self.db_path)
        svc = auth_service.AuthService(self.db_path, limiter=lim)
        for _ in range(3):
            with self.assertRaises(AuthenticationError):
                svc.login("nobody@ducklab.com", "x")
        with self.assertRaises(RateLimitError):
            svc.login("nobody@ducklab.com", "x")
        # New limiter instance over the same DB still sees the failures
        # (shared across processes / restarts).
        lim2 = _RateLimiter(3, 300, db_path=self.db_path)
        svc2 = auth_service.AuthService(self.db_path, limiter=lim2)
        with self.assertRaises(RateLimitError):
            svc2.login("nobody@ducklab.com", "x")

    def test_success_clears_failures(self):
        lim = _RateLimiter(2, 300, db_path=self.db_path)
        svc = auth_service.AuthService(self.db_path, limiter=lim)
        svc.bootstrap_first_admin("a@ducklab.com", "A", PW)
        with self.assertRaises(AuthenticationError):
            svc.login("a@ducklab.com", "bad1")
        svc.login("a@ducklab.com", PW)
        with self.assertRaises(AuthenticationError):
            svc.login("a@ducklab.com", "bad2")
        # Only one failure since the clear — not rate limited yet.
        svc.login("a@ducklab.com", PW)

    def test_ip_bucket_limits_across_accounts(self):
        lim = _RateLimiter(2, 300, db_path=self.db_path)
        svc = auth_service.AuthService(self.db_path, limiter=lim)
        for email in ("x@ducklab.com", "y@ducklab.com"):
            with self.assertRaises(AuthenticationError):
                svc.login(email, "bad", limiter_key="10.0.0.1")
        with self.assertRaises(RateLimitError):
            svc.login("z@ducklab.com", "bad", limiter_key="10.0.0.1")
        # Different IP unaffected.
        with self.assertRaises(AuthenticationError):
            svc.login("z@ducklab.com", "bad", limiter_key="10.0.0.2")


class TestEventAuthorization(unittest.TestCase):
    """Server-side authorization boundaries (existing model regression)."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "ev.db"
        self.app = _make_app_service(self.db_path)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)
        self.s1 = self.app.write.register_store("S1", "Store One")
        self.s2 = self.app.write.register_store("S2", "Store Two")
        self.viewer = self._user("v@ducklab.com", ("viewer",), (self.s1,))
        self.writer = self._user("w@ducklab.com", ("branch_manager",),
                                 (self.s1,))

    def tearDown(self):
        self._tmp.cleanup()

    def _user(self, email, roles, stores):
        u = self.app.auth.create_user(email, "U", PW2, actor=self.admin)
        for r in roles:
            self.app.auth.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.auth.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)

    def test_unauthorized_store_access(self):
        with self.assertRaises(AuthorizationError):
            self.app.get_daily_records(self.viewer, self.s2, "2026-09")
        with self.assertRaises(AuthorizationError):
            self.app.preview_month(self.viewer, self.s2, "2026-09")

    def test_viewer_write_rejected(self):
        from datetime import date
        with self.assertRaises(AuthorizationError):
            self.app.record_daily(self.viewer, self.s1, date(2026, 9, 1),
                                  sales_cents=100)
        with self.assertRaises(AuthorizationError):
            self.app.record_expense(self.viewer, self.s1,
                                    incurred_date=date(2026, 9, 1),
                                    category_id=1, amount_cents=100,
                                    description="x")

    def test_viewer_cannot_finalize(self):
        with self.assertRaises(AuthorizationError):
            self.app.finalize_month(self.viewer, self.s1, "2026-09")

    def test_writer_store_scoped(self):
        with self.assertRaises(AuthorizationError):
            self.app.get_daily_records(self.writer, self.s2, "2026-09")


class TestWebSocketOrigin(unittest.TestCase):
    def test_cross_origin_websocket_rejected(self):
        async def dummy_app(scope, receive, send):
            await send({"type": "websocket.accept"})

        mw = OriginCheckMiddleware(dummy_app)
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "websocket.connect"}

        scope = {
            "type": "websocket",
            "path": "/_event",
            "client": ("203.0.113.9", 1234),
            "headers": [
                (b"origin", b"https://evil.example.com"),
                (b"host", b"opscore.test"),
            ],
        }
        import asyncio
        asyncio.run(mw(scope, receive, send))
        self.assertEqual(sent[0]["type"], "websocket.close")
        self.assertEqual(sent[0]["code"], 1008)

    def test_same_origin_websocket_accepted(self):
        async def dummy_app(scope, receive, send):
            await send({"type": "websocket.accept"})

        mw = OriginCheckMiddleware(dummy_app)
        sent = []

        async def send(msg):
            sent.append(msg)

        async def receive():
            return {"type": "websocket.connect"}

        scope = {
            "type": "websocket",
            "path": "/_event",
            "client": ("127.0.0.1", 1234),
            "headers": [
                (b"origin", b"https://opscore.test"),
                (b"host", b"opscore.test"),
            ],
        }
        import asyncio
        asyncio.run(mw(scope, receive, send))
        self.assertEqual(sent[0]["type"], "websocket.accept")


class _FakeHeaders:
    def __init__(self, raw):
        self.raw_headers = raw


class _FakeState:
    def __init__(self, cookie_header):
        self.rx_router_headers = _FakeHeaders({"cookie": cookie_header})


class TestReadSessionToken(unittest.TestCase):
    def test_extracts_cookie(self):
        st = _FakeState(f"other=1; {SESSION_COOKIE}=abc123")
        self.assertEqual(read_session_token(st), "abc123")

    def test_missing_cookie(self):
        self.assertEqual(read_session_token(_FakeState("")), "")
        self.assertEqual(read_session_token(_FakeState("other=1")), "")

    def test_malformed_cookie_header(self):
        self.assertEqual(read_session_token(_FakeState(";;;")), "")


class TestLegacyRouteExposure(unittest.TestCase):
    def test_legacy_routes_not_mounted(self):
        import ducklab.ducklab as dl
        routes = {p.route.lstrip("/") for p in dl.app._unevaluated_pages.values()}
        for legacy in ("prototype", "placements", "tasks", "report"):
            self.assertNotIn(legacy, routes)
        # Root "/" is registered by Reflex as "index"; it must be the ops
        # root page (redirects to login/dashboard), not prototype content.
        self.assertIn("index", routes)
        for protected in ("login", "ops", "ops/report", "ops/daily",
                          "ops/expenses"):
            self.assertIn(protected, routes)

    def test_root_route_redirects_by_session(self):
        """The root page is the ops redirect page, not prototype content."""
        import ducklab.ducklab as dl
        root = [p for p in dl.app._unevaluated_pages.values()
                if p.route.lstrip("/") in ("", "index")]
        self.assertEqual(len(root), 1)
        # Its on_load must be the AuthState.root_redirect handler.
        self.assertIsNotNone(root[0].on_load)


if __name__ == "__main__":
    unittest.main()
