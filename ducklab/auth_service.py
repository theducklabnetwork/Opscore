"""Authentication service: users, passwords (Argon2id), sessions, audit.

Server-side only. Never returns password hashes or raw session tokens.
Password crypto is delegated entirely to argon2-cffi (Argon2id, per-library
salts, constant-time verification) — no custom cryptography.

Session model:
- Bearer token = 32 random bytes (urlsafe). Only its SHA-256 hash is stored.
- Sessions expire (default 12h) and are revocable; disabling a user or
  changing their password revokes all of their sessions.
- Login is rate-limited per normalized email (in-process limiter) and
  failures produce a generic message to avoid account enumeration.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHash, VerificationError, VerifyMismatchError

from ducklab import auth_db, store_ops_db

SESSION_TTL = timedelta(hours=12)
LOGIN_MAX_ATTEMPTS = 5
LOGIN_WINDOW_SECONDS = 300
MIN_PASSWORD_LENGTH = 10

_ph = PasswordHasher()  # argon2id, RFC-recommended defaults

# Dummy hash used to equalize timing when the email is unknown (prevents
# account enumeration via response-time differences). Generated with the
# same argon2id parameters; the plaintext is irrelevant and never stored.
_DUMMY_HASH = (
    "$argon2id$v=19$m=65536,t=3,p=4$TnJKrBhLCNBtd8CRZz3O9w$"
    "d8CTxv5Q7EtcXNkPsuoaiiR2IFWciO0Lf3mcxUw3vlA"
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Base auth failure."""


class AuthenticationError(AuthError):
    """Generic invalid-credentials / inactive / expired session failure.

    Message is intentionally non-specific to prevent account enumeration.
    """


class AuthorizationError(AuthError):
    """Caller lacks the required permission or store access."""


class ValidationError(AuthError):
    """Malformed input (email, weak password, ...)."""


class NotFoundError(AuthError):
    """Referenced user/role/store does not exist."""


class RateLimitError(AuthenticationError):
    """Too many recent failed attempts."""


@dataclass(frozen=True)
class User:
    id: int
    email: str
    display_name: str
    is_active: bool
    roles: tuple[str, ...]
    store_ids: tuple[int, ...]
    last_login_at: Optional[str]

    @property
    def is_super_admin(self) -> bool:
        return "super_admin" in self.roles


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().strftime("%Y-%m-%d %H:%M:%S")


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class _RateLimiter:
    """Sliding-window per-key failure counter.

    Backends:
    - SQLite-backed (db_path given): failure timestamps persist in the
      `auth_login_failures` table, so limits survive restarts and are
      shared across processes/workers using the same database.
    - In-memory (db_path=None): legacy behavior for unit tests.

    Keys are normalized emails or client IPs. We deliberately do not
    trust forwarded IP headers unless proxy trust is explicitly enabled
    (see ducklab/security.py). A distributed limiter (Redis) remains an
    option for multi-host deployments; single-host multi-worker is
    covered by the SQLite backend.
    """

    def __init__(self, max_attempts: int, window: int,
                 db_path: Path | str | None = None):
        self._max = max_attempts
        self._window = window
        self._db_path = db_path
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        conn = store_ops_db.connect(self._db_path)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS auth_login_failures ("
            "key TEXT NOT NULL, failed_at REAL NOT NULL)")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_login_failures_key "
            "ON auth_login_failures(key, failed_at)")
        return conn

    def _now(self) -> float:
        # Persisted records use wall time (CLOCK_MONOTONIC differs across
        # reboots); in-memory records use monotonic time.
        return time.monotonic() if self._db_path is None else time.time()

    def _prune_sql(self, conn) -> None:
        conn.execute("DELETE FROM auth_login_failures WHERE failed_at < ?",
                     (time.time() - self._window,))

    def _count(self, key: str) -> int:
        cutoff = self._now() - self._window
        if self._db_path is None:
            with self._lock:
                attempts = [t for t in self._failures.get(key, [])
                            if time.monotonic() - t < self._window]
                self._failures[key] = attempts
                return len(attempts)
        conn = self._conn()
        try:
            self._prune_sql(conn)
            n = conn.execute(
                "SELECT COUNT(*) FROM auth_login_failures "
                "WHERE key = ? AND failed_at >= ?", (key, cutoff)
            ).fetchone()[0]
            conn.commit()
            return n
        finally:
            conn.close()

    def check(self, key: str) -> None:
        if self._count(key) >= self._max:
            raise RateLimitError("Invalid email or password")

    def record_failure(self, key: str) -> None:
        if self._db_path is None:
            with self._lock:
                self._failures.setdefault(key, []).append(time.monotonic())
            return
        conn = self._conn()
        try:
            self._prune_sql(conn)
            conn.execute(
                "INSERT INTO auth_login_failures (key, failed_at) "
                "VALUES (?,?)", (key, self._now()))
            conn.commit()
        finally:
            conn.close()

    def clear(self, key: str) -> None:
        if self._db_path is None:
            with self._lock:
                self._failures.pop(key, None)
            return
        conn = self._conn()
        try:
            conn.execute("DELETE FROM auth_login_failures WHERE key = ?",
                         (key,))
            conn.commit()
        finally:
            conn.close()


class AuthService:
    def __init__(self, db_path: Path | str = store_ops_db.DB_PATH,
                 limiter: Optional[_RateLimiter] = None):
        self._db_path = db_path
        auth_db.init_auth(db_path)
        self._limiter = limiter or _RateLimiter(LOGIN_MAX_ATTEMPTS,
                                                LOGIN_WINDOW_SECONDS)

    # ------------------------------------------------------------------
    # connection helper
    # ------------------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        return store_ops_db.connect(self._db_path)

    # ------------------------------------------------------------------
    # audit
    # ------------------------------------------------------------------
    def _audit(self, conn: sqlite3.Connection, event_type: str,
               outcome: str, user_id: Optional[int] = None,
               metadata: Optional[dict] = None) -> None:
        # Never log passwords, tokens, or hashes — callers pass only
        # non-sensitive context (emails of *created* users, role names, etc.).
        safe = dict(metadata or {})
        for banned in ("password", "token", "password_hash", "token_hash"):
            safe.pop(banned, None)
        conn.execute(
            "INSERT INTO auth_audit_events (user_id, event_type, outcome, "
            "metadata) VALUES (?,?,?,?)",
            (user_id, event_type, outcome, json.dumps(safe, sort_keys=True)),
        )

    # ------------------------------------------------------------------
    # user loading
    # ------------------------------------------------------------------
    def _load_user(self, conn: sqlite3.Connection, user_id: int) -> User:
        row = conn.execute(
            "SELECT id, email, display_name, is_active, last_login_at "
            "FROM auth_users WHERE id = ?", (user_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(f"User {user_id} not found")
        roles = tuple(r[0] for r in conn.execute(
            "SELECT r.name FROM auth_roles r "
            "JOIN auth_user_roles ur ON ur.role_id = r.id "
            "WHERE ur.user_id = ? ORDER BY r.name", (user_id,)))
        stores = tuple(r[0] for r in conn.execute(
            "SELECT store_id FROM auth_user_store_assignments "
            "WHERE user_id = ? AND is_active = 1 ORDER BY store_id",
            (user_id,)))
        return User(id=row["id"], email=row["email"],
                    display_name=row["display_name"],
                    is_active=bool(row["is_active"]), roles=roles,
                    store_ids=stores, last_login_at=row["last_login_at"])

    def get_user(self, user_id: int) -> User:
        conn = self._connect()
        try:
            return self._load_user(conn, user_id)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # password rules
    # ------------------------------------------------------------------
    @staticmethod
    def validate_password(password: str) -> None:
        if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
            raise ValidationError(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise ValidationError("Password must contain letters and digits")

    # ------------------------------------------------------------------
    # authentication & sessions
    # ------------------------------------------------------------------
    def check_rate_limit(self, key: str) -> None:
        """Public pre-check so transports can rate-limit before any work."""
        self._limiter.check(key)

    def login(self, email: str, password: str,
              limiter_key: Optional[str] = None) -> tuple[str, User]:
        """Verify credentials; return (raw_session_token, user).

        Raises AuthenticationError with a generic message for unknown email,
        wrong password, disabled account, or rate limiting. `limiter_key`
        (e.g. client IP) is an additional rate-limit bucket — failures
        count against both the email bucket and the extra key.
        """
        norm = normalize_email(email)
        self._limiter.check(norm)
        if limiter_key:
            self._limiter.check(limiter_key)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, password_hash, is_active FROM auth_users "
                "WHERE email = ?", (norm,)
            ).fetchone()
            if row is None:
                # Verify against a fixed dummy hash so timing does not leak
                # whether the account exists.
                try:
                    _ph.verify(_DUMMY_HASH, password or "")
                except (VerifyMismatchError, VerificationError, InvalidHash):
                    pass
                self._record_login_failure(norm, limiter_key)
                self._audit(conn, "login", "failure", None,
                            {"reason": "unknown_email"})
                conn.commit()
                raise AuthenticationError("Invalid email or password")
            try:
                _ph.verify(row["password_hash"], password or "")
            except (VerifyMismatchError, VerificationError, InvalidHash):
                self._record_login_failure(norm, limiter_key)
                self._audit(conn, "login", "failure", row["id"],
                            {"reason": "bad_password"})
                conn.commit()
                raise AuthenticationError("Invalid email or password")
            if not row["is_active"]:
                self._record_login_failure(norm, limiter_key)
                self._audit(conn, "login", "failure", row["id"],
                            {"reason": "disabled"})
                conn.commit()
                raise AuthenticationError("Invalid email or password")
            # Transparent hash upgrade if argon2 parameters improved.
            if _ph.check_needs_rehash(row["password_hash"]):
                conn.execute(
                    "UPDATE auth_users SET password_hash = ?, "
                    "updated_at = ? WHERE id = ?",
                    (_ph.hash(password), _now_iso(), row["id"]))
            token = secrets.token_urlsafe(32)
            expires = _now() + SESSION_TTL
            conn.execute(
                "INSERT INTO auth_sessions (token_hash, user_id, expires_at) "
                "VALUES (?,?,?)",
                (_hash_token(token), row["id"],
                 expires.strftime("%Y-%m-%d %H:%M:%S")))
            conn.execute("UPDATE auth_users SET last_login_at = ? WHERE id = ?",
                         (_now_iso(), row["id"]))
            self._audit(conn, "login", "success", row["id"])
            conn.commit()
            # Clear limiter buckets outside the DB transaction: the SQL
            # limiter uses its own connection, and SQLite would deadlock
            # on a second writer while this transaction is open.
            self._limiter.clear(norm)
            if limiter_key:
                self._limiter.clear(limiter_key)
            return token, self._load_user(conn, row["id"])
        finally:
            conn.close()

    def _record_login_failure(self, norm_email: str,
                              limiter_key: Optional[str]) -> None:
        self._limiter.record_failure(norm_email)
        if limiter_key:
            self._limiter.record_failure(limiter_key)

    def authenticate_token(self, token: str) -> User:
        """Resolve a raw session token to an active user, or raise."""
        if not token:
            raise AuthenticationError("Not authenticated")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT s.id, s.user_id, s.expires_at, s.revoked_at, "
                "u.is_active FROM auth_sessions s "
                "JOIN auth_users u ON u.id = s.user_id "
                "WHERE s.token_hash = ?", (_hash_token(token),)
            ).fetchone()
            if row is None or row["revoked_at"] is not None:
                raise AuthenticationError("Not authenticated")
            if datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S") \
                    .replace(tzinfo=timezone.utc) <= _now():
                raise AuthenticationError("Not authenticated")
            if not row["is_active"]:
                raise AuthenticationError("Not authenticated")
            return self._load_user(conn, row["user_id"])
        finally:
            conn.close()

    def logout(self, token: str) -> None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT user_id FROM auth_sessions WHERE token_hash = ? "
                "AND revoked_at IS NULL", (_hash_token(token),)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at = ? "
                    "WHERE token_hash = ?", (_now_iso(), _hash_token(token)))
                self._audit(conn, "logout", "success", row["user_id"])
                conn.commit()
        finally:
            conn.close()

    def revoke_user_sessions(self, actor: User, user_id: int,
                             reason: str = "revoked") -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (_now_iso(), user_id))
            self._audit(conn, "session_revocation", "success", user_id,
                        {"by": actor.id, "reason": reason})
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # user management (service layer; permission checks live in
    # authz_service, but super-admin invariants are enforced here too)
    # ------------------------------------------------------------------
    def create_user(self, email: str, display_name: str, password: str,
                    actor: Optional[User] = None) -> User:
        norm = normalize_email(email)
        if not EMAIL_RE.match(norm):
            raise ValidationError("Invalid email address")
        if not display_name.strip():
            raise ValidationError("Display name required")
        self.validate_password(password)
        conn = self._connect()
        try:
            pw_hash = _ph.hash(password)
            cur = conn.execute(
                "INSERT INTO auth_users (email, display_name, password_hash) "
                "VALUES (?,?,?)", (norm, display_name.strip(), pw_hash))
            uid = cur.lastrowid
            self._audit(conn, "user_create", "success", uid,
                        {"by": actor.id if actor else None, "email": norm})
            conn.commit()
            return self._load_user(conn, uid)
        except sqlite3.IntegrityError:
            conn.rollback()
            raise ValidationError("Email already registered")
        finally:
            conn.close()

    def list_users(self) -> list[User]:
        conn = self._connect()
        try:
            ids = [r[0] for r in conn.execute(
                "SELECT id FROM auth_users ORDER BY email")]
            return [self._load_user(conn, i) for i in ids]
        finally:
            conn.close()

    def set_display_name(self, actor: User, user_id: int, name: str) -> User:
        if not name.strip():
            raise ValidationError("Display name required")
        conn = self._connect()
        try:
            self._load_user(conn, user_id)
            conn.execute(
                "UPDATE auth_users SET display_name = ?, updated_at = ? "
                "WHERE id = ?", (name.strip(), _now_iso(), user_id))
            self._audit(conn, "user_update", "success", user_id,
                        {"by": actor.id, "field": "display_name"})
            conn.commit()
            return self._load_user(conn, user_id)
        finally:
            conn.close()

    def _active_super_admin_count(self, conn) -> int:
        return conn.execute(
            "SELECT COUNT(DISTINCT u.id) FROM auth_users u "
            "JOIN auth_user_roles ur ON ur.user_id = u.id "
            "JOIN auth_roles r ON r.id = ur.role_id "
            "WHERE r.name = 'super_admin' AND u.is_active = 1").fetchone()[0]

    def _is_super_admin(self, conn, user_id: int) -> bool:
        return conn.execute(
            "SELECT 1 FROM auth_user_roles ur JOIN auth_roles r "
            "ON r.id = ur.role_id WHERE ur.user_id = ? "
            "AND r.name = 'super_admin'", (user_id,)).fetchone() is not None

    def set_active(self, actor: User, user_id: int, active: bool) -> User:
        conn = self._connect()
        try:
            self._load_user(conn, user_id)
            if not active and self._is_super_admin(conn, user_id) \
                    and self._active_super_admin_count(conn) <= 1:
                raise ValidationError("Cannot disable the last active Super Admin")
            conn.execute(
                "UPDATE auth_users SET is_active = ?, updated_at = ? "
                "WHERE id = ?", (1 if active else 0, _now_iso(), user_id))
            if not active:
                conn.execute(
                    "UPDATE auth_sessions SET revoked_at = ? "
                    "WHERE user_id = ? AND revoked_at IS NULL",
                    (_now_iso(), user_id))
            self._audit(conn, "user_activate" if active else "user_deactivate",
                        "success", user_id, {"by": actor.id})
            conn.commit()
            return self._load_user(conn, user_id)
        finally:
            conn.close()

    def change_password(self, actor: User, user_id: int, new_password: str,
                        *, self_change: bool = False,
                        current_password: Optional[str] = None) -> None:
        """Change a password and revoke all of that user's sessions.

        Self-changes require re-authentication via current_password.
        Admin resets (self_change=False) also revoke sessions so the user
        must log in with the new password.
        """
        self.validate_password(new_password)
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT password_hash FROM auth_users WHERE id = ?",
                (user_id,)).fetchone()
            if row is None:
                raise NotFoundError(f"User {user_id} not found")
            if self_change:
                try:
                    _ph.verify(row["password_hash"], current_password or "")
                except (VerifyMismatchError, VerificationError, InvalidHash):
                    self._audit(conn, "password_change", "failure", user_id,
                                {"reason": "reauth_failed"})
                    conn.commit()
                    raise AuthenticationError("Invalid email or password")
            conn.execute(
                "UPDATE auth_users SET password_hash = ?, updated_at = ?, "
                "password_changed_at = ? WHERE id = ?",
                (_ph.hash(new_password), _now_iso(), _now_iso(), user_id))
            conn.execute(
                "UPDATE auth_sessions SET revoked_at = ? "
                "WHERE user_id = ? AND revoked_at IS NULL",
                (_now_iso(), user_id))
            self._audit(conn, "password_change", "success", user_id,
                        {"by": actor.id, "self": self_change})
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # roles & store assignments (low level; authorization is in
    # authz_service — these enforce only integrity invariants)
    # ------------------------------------------------------------------
    def _role_id(self, conn, role: str) -> int:
        row = conn.execute("SELECT id FROM auth_roles WHERE name = ?",
                           (role,)).fetchone()
        if row is None:
            raise NotFoundError(f"Unknown role {role!r}")
        return row[0]

    def assign_role(self, actor: User, user_id: int, role: str) -> User:
        conn = self._connect()
        try:
            rid = self._role_id(conn, role)
            self._load_user(conn, user_id)
            conn.execute(
                "INSERT OR IGNORE INTO auth_user_roles (user_id, role_id) "
                "VALUES (?,?)", (user_id, rid))
            self._audit(conn, "role_assign", "success", user_id,
                        {"by": actor.id, "role": role})
            conn.commit()
            return self._load_user(conn, user_id)
        finally:
            conn.close()

    def revoke_role(self, actor: User, user_id: int, role: str) -> User:
        conn = self._connect()
        try:
            rid = self._role_id(conn, role)
            self._load_user(conn, user_id)
            if role == "super_admin" and self._is_super_admin(conn, user_id) \
                    and self._active_super_admin_count(conn) <= 1:
                raise ValidationError("Cannot revoke the last active Super Admin")
            conn.execute(
                "DELETE FROM auth_user_roles WHERE user_id = ? AND role_id = ?",
                (user_id, rid))
            self._audit(conn, "role_revoke", "success", user_id,
                        {"by": actor.id, "role": role})
            conn.commit()
            return self._load_user(conn, user_id)
        finally:
            conn.close()

    def assign_store(self, actor: User, user_id: int, store_id: int) -> User:
        conn = self._connect()
        try:
            self._load_user(conn, user_id)
            if conn.execute("SELECT 1 FROM stores WHERE id = ?",
                            (store_id,)).fetchone() is None:
                raise NotFoundError(f"Store {store_id} not found")
            conn.execute(
                "INSERT INTO auth_user_store_assignments (user_id, store_id, "
                "is_active, revoked_at) VALUES (?,?,1,NULL) "
                "ON CONFLICT (user_id, store_id) DO UPDATE SET "
                "is_active = 1, revoked_at = NULL", (user_id, store_id))
            self._audit(conn, "store_assign", "success", user_id,
                        {"by": actor.id, "store_id": store_id})
            conn.commit()
            return self._load_user(conn, user_id)
        finally:
            conn.close()

    def revoke_store(self, actor: User, user_id: int, store_id: int) -> User:
        conn = self._connect()
        try:
            self._load_user(conn, user_id)
            conn.execute(
                "UPDATE auth_user_store_assignments SET is_active = 0, "
                "revoked_at = ? WHERE user_id = ? AND store_id = ?",
                (_now_iso(), user_id, store_id))
            self._audit(conn, "store_revoke", "success", user_id,
                        {"by": actor.id, "store_id": store_id})
            conn.commit()
            return self._load_user(conn, user_id)
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # first-admin bootstrap (operator-initiated; refuses once any
    # super_admin exists)
    # ------------------------------------------------------------------
    def bootstrap_first_admin(self, email: str, display_name: str,
                              password: str) -> User:
        conn = self._connect()
        try:
            existing = conn.execute(
                "SELECT COUNT(*) FROM auth_user_roles ur JOIN auth_roles r "
                "ON r.id = ur.role_id WHERE r.name = 'super_admin'"
            ).fetchone()[0]
            if existing:
                raise ValidationError(
                    "Bootstrap refused: a Super Admin already exists")
        finally:
            conn.close()
        user = self.create_user(email, display_name, password, actor=None)
        conn = self._connect()
        try:
            rid = self._role_id(conn, "super_admin")
            conn.execute(
                "INSERT INTO auth_user_roles (user_id, role_id) VALUES (?,?)",
                (user.id, rid))
            self._audit(conn, "bootstrap_first_admin", "success", user.id)
            conn.commit()
            return self._load_user(conn, user.id)
        finally:
            conn.close()
