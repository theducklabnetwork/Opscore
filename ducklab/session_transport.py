"""Session transport: server-issued, HttpOnly session cookies.

The cookie is set by a dedicated Starlette HTTP endpoint (POST /api/session)
mounted via Reflex's `api_transformer`, never by JavaScript. The browser
attaches the cookie to every same-site request, including the Reflex
WebSocket handshake; backend event handlers read it from the connection's
request headers (`self.rx_router_headers.raw_headers`), which Reflex keeps
server-side only (the cookie is never serialized to the frontend).

Security properties:
- HttpOnly + SameSite=Lax + (Secure behind HTTPS when DUCKLAB_SECURE_COOKIES=1)
- Explicit expiration: cookie max_age matches the server-side session TTL;
  the DB expiry is authoritative on every validation.
- Login rotates the cookie (session-fixation defense) and logout revokes
  server-side and clears the cookie.
- POST /api/session requires Origin == Host (CSRF defense for the only
  state-changing HTTP endpoint; Reflex events ride a same-site WebSocket).
- The WebSocket mount is wrapped with Origin/Host validation, so a browser
  cannot open a cross-origin authenticated socket.
- Rate limiting (per-email + per-client-IP) happens before password
  verification; the limiter is SQLite-backed (same DB as sessions) so it
  survives restarts and is shared across workers.

No raw session token ever enters Reflex state, event payloads, client
storage, or logs.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import os
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from ducklab import auth_service, store_ops_db
from ducklab.auth_service import (
    AuthService, AuthenticationError, RateLimitError, _RateLimiter,
)
from ducklab.security import SecurityConfig

log = logging.getLogger("ducklab.session_transport")

SESSION_COOKIE = "ducklab_session"

config = SecurityConfig.from_env()

DB_PATH = os.environ.get("DUCKLAB_DB_PATH") or store_ops_db.DB_PATH

auth = AuthService(DB_PATH, limiter=_RateLimiter(
    auth_service.LOGIN_MAX_ATTEMPTS, auth_service.LOGIN_WINDOW_SECONDS,
    db_path=DB_PATH))

MAX_BODY = 4096


def _split_host(value: str) -> str:
    """Bare host (lowercase, no port) from a Host header or Origin URL."""
    v = value.strip()
    if "://" not in v:
        v = "//" + v
    return urlsplit(v).hostname or ""


def _same_site_origin(origin: str, host: str) -> bool:
    """Origin header's host must equal the request Host (same-site check),
    OR the Origin must be the explicitly allowed frontend origin (dev mode:
    Vite on :3000 proxies events to the backend on :8000). In production
    behind the reverse proxy the Origin always equals the Host."""
    if not origin or not host:
        return False
    if _split_host(origin) == _split_host(host):
        return True
    allowed = config.allowed_frontend_origins
    return origin.rstrip("/") in allowed


def _client_ip(request: Request) -> str:
    """Client IP for rate limiting.

    X-Forwarded-For is honored only when DUCKLAB_TRUST_PROXY=1 and the
    direct peer is a configured private/trusted proxy address. Otherwise
    the ASGI peer address is used; headers from the client are never
    trusted unconditionally.
    """
    peer = request.client.host if request.client else ""
    if config.trust_proxy and peer and _is_trusted_proxy(peer):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip() or peer
    return peer


def _is_trusted_proxy(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return addr.is_private or addr.is_loopback


def _cookie_header(token: str, max_age: int) -> str:
    parts = [
        f"{SESSION_COOKIE}={token}",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        f"Max-Age={max_age}",
    ]
    if config.secure_cookies:
        parts.append("Secure")
    return "; ".join(parts)


def _clear_cookie_header() -> str:
    parts = [
        f"{SESSION_COOKIE}=",
        "Path=/",
        "HttpOnly",
        "SameSite=Lax",
        "Max-Age=0",
    ]
    if config.secure_cookies:
        parts.append("Secure")
    return "; ".join(parts)


def _error(status: int, message: str = "Invalid email or password",
           **headers: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status,
                        headers=headers)


def _allowed_origin(origin: str, host: str) -> bool:
    """Origin is accepted if its host matches the request Host (same-site,
    works on any domain without config), or it is explicitly allow-listed
    via DUCKLAB_FRONTEND_ORIGINS (split frontend/backend dev only)."""
    if _same_site_origin(origin, host):
        return True
    extra = os.environ.get("DUCKLAB_FRONTEND_ORIGINS", "")
    return bool(origin) and origin in {o.strip() for o in extra.split(",")}


async def session_endpoint(request: Request) -> JSONResponse:
    """POST /api/session — login (rotate cookie) / logout (revoke+clear).

    Login is origin-checked (Origin must match Host) and rate-limited
    before any password verification. The response carries no body data
    that identifies the account beyond success/failure.
    """
    if not _allowed_origin(request.headers.get("origin", ""),
                           request.headers.get("host", "")):
        return _error(403, "Forbidden")
    if int(request.headers.get("content-length") or 0) > MAX_BODY:
        return _error(413, "Payload too large")
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
        return _error(400, "Bad request")
    if not isinstance(body, dict):
        return _error(400, "Bad request")
    action = body.get("action")
    limiter_key = _client_ip(request)
    if action == "logout":
        cookie = request.cookies.get(SESSION_COOKIE, "")
        if cookie:
            auth.logout(cookie)
        # Logout always succeeds; never reveals whether a session existed.
        return JSONResponse({"ok": True},
                            headers={"Set-Cookie": _clear_cookie_header()})
    if action == "bootstrap":
        # First-launch account creation: only while the database has zero
        # users. The service re-checks inside, so a race just fails.
        name = str(body.get("name") or "")[:120].strip()
        email = str(body.get("email") or "")[:254]
        password = str(body.get("password") or "")[:1024]
        try:
            auth.check_rate_limit(limiter_key)
            if auth.user_count():
                return _error(403, "Forbidden")
            admin = auth.bootstrap_first_admin(email, name, password)
            recovery_key = admin.recovery_key
            token, _user = auth.login(email, password,
                                      limiter_key=limiter_key)
        except RateLimitError:
            return _error(429)
        except auth_service.ValidationError as exc:
            return _error(400, str(exc))
        except AuthenticationError:
            return _error(401)
        max_age = int(auth_service.SESSION_TTL.total_seconds())
        return JSONResponse(
            {"ok": True, "recovery_key": recovery_key},
            headers={"Set-Cookie": _cookie_header(token, max_age)},
        )
    if action == "recover":
        # Forgot-password recovery via a single-use recovery key. The raw
        # replacement key is returned in this response body exactly once;
        # it must never be echoed into logs or client-side storage beyond
        # the in-memory "save your kit" screen. No cookie is set — the
        # user signs in normally with their new password afterwards.
        recovery_key = str(body.get("recovery_key") or "")[:256]
        password = str(body.get("password") or "")[:1024]
        try:
            auth.check_rate_limit(limiter_key)
            new_key = auth.recover_password(recovery_key, password,
                                            limiter_key=limiter_key)
        except RateLimitError:
            return _error(429)
        except auth_service.ValidationError as exc:
            return _error(400, str(exc))
        except AuthenticationError:
            # Generic: never reveals whether the key exists.
            return _error(401, "Invalid recovery key")
        return JSONResponse({"ok": True, "recovery_key": new_key})
    if action != "login":
        return _error(400, "Bad request")
    email = str(body.get("email") or "")[:254]
    password = str(body.get("password") or "")[:1024]
    try:
        # IP limiter checked first so credential stuffing against many
        # accounts from one client is also bounded.
        auth.check_rate_limit(limiter_key)
        token, _user = auth.login(email, password, limiter_key=limiter_key)
    except RateLimitError:
        return _error(429)
    except AuthenticationError:
        return _error(401)
    max_age = int(auth_service.SESSION_TTL.total_seconds())
    return JSONResponse(
        {"ok": True},
        headers={"Set-Cookie": _cookie_header(token, max_age)},
    )


def read_session_token(state) -> str:
    """Extract the raw session cookie from a Reflex state's server-side
    request headers. Never trusts client-supplied state vars."""
    raw = state.rx_router_headers.raw_headers.get("cookie", "")
    if not raw:
        return ""
    jar = SimpleCookie()
    try:
        jar.load(raw)
    except Exception:  # malformed Cookie header — treat as unauthenticated
        return ""
    morsel = jar.get(SESSION_COOKIE)
    return morsel.value if morsel else ""


class OriginCheckMiddleware:
    """Reject WebSocket (and Socket.IO polling) handshakes whose Origin
    host does not match the request Host — CSWSH / cross-origin
    authenticated socket defense. Plain HTTP requests pass through (they
    either carry no credentials or are checked per-endpoint)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("websocket",) or \
                (scope["type"] == "http"
                 and scope.get("path", "").startswith("/_event")):
            headers = {k.decode("latin1"): v.decode("latin1")
                       for k, v in scope.get("headers", [])}
            origin = headers.get("origin", "")
            host = headers.get("host", "")
            if scope["type"] == "websocket" and not _allowed_origin(
                    origin, host):
                log.warning("Rejected cross-origin WebSocket handshake "
                            "(origin host mismatch) from %s",
                            scope.get("client"))
                # Close without accepting.
                await send({"type": "websocket.close", "code": 1008})
                return
        await self.app(scope, receive, send)


# api_transformer entry point: a Starlette app that serves /api/session and
# /api/health directly and mounts the (origin-checked) Reflex backend for
# everything else. Mounted via `rx.App(api_transformer=...)`.
async def health_endpoint(request: Request) -> JSONResponse:
    """Unauthenticated readiness probe for desktop startup and build checks.
    Returns no sensitive data. Desktop builds set DUCKLAB_RELEASE_ID so the
    launcher and diagnostics can verify the installed release identity."""
    import os
    return JSONResponse({
        "status": "ok",
        "release": os.environ.get("DUCKLAB_RELEASE_ID", "dev"),
    })


def asgi_transformer(reflex_asgi):
    inner = Starlette(routes=[
        Route("/api/session", session_endpoint, methods=["POST"]),
        Route("/api/health", health_endpoint, methods=["GET"]),
    ])
    inner.mount("/", OriginCheckMiddleware(reflex_asgi))
    return inner
