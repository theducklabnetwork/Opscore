# DuckLab Operations — Authentication & Deployment Notes

Production destination: **https://app.theducklab.com/** (invite-only; no
public registration).

> **Production deployment artifacts** (systemd service, Caddy config, backup
> script/timer, runbook, rollback, recovery, verification checklist) live in
> **`deploy/`** — prepared and validated, **not installed**. See
> `deploy/DEPLOYMENT.md` for the cutover runbook. This file documents the
> authentication/security architecture those artifacts rely on.

## Production runtime architecture

Single-port production: `reflex run --env prod --single-port
--backend-host 127.0.0.1 --backend-port 3000`. The granian backend serves the
compiled frontend, `/api/*`, the `/_event` WebSocket, and static assets on one
loopback port. Caddy reverse-proxies the public domain to it (single
`reverse_proxy 127.0.0.1:3000` — no path splitting; WebSocket upgrade is
automatic). Under HTTPS, Reflex's `getBackendURL` targets the same origin, so
the session cookie, CSRF Origin check, and WebSocket Origin check all align.

## Authentication architecture

- **Passwords**: Argon2id via `argon2-cffi` (25.1.0), per-user salts,
  constant-time verification, transparent rehash on parameter upgrades.
  Never stored or logged in plaintext; unknown-email logins verify against
  a dummy hash to equalize timing (anti-enumeration).
- **Sessions**: `secrets.token_urlsafe(32)` bearer token; only its
  SHA-256 hash is persisted (`auth_sessions.token_hash`). TTL 12h.
  The token is transported in a **server-issued, HttpOnly, SameSite=Lax
  cookie** set by `POST /api/session` (see `ducklab/session_transport.py`)
  — never exposed to JavaScript, Reflex state, event payloads, or logs.
  Logout, account deactivation, and password changes revoke sessions.
- **Authorization**: centralized in `ducklab/authz.py` (deny by default),
  enforced server-side by `ducklab/app_service.py` before any repository/
  service call. Store access comes only from `auth_user_store_assignments`;
  client-supplied roles/user IDs are never trusted.
- **Roles**: super_admin, administrator, zone_manager, branch_manager,
  viewer. Finalization/correction: super_admin + administrator (assigned
  stores). Config: super_admin + administrator. Writes: all except viewer.
  Reads: all roles, restricted to assigned stores (super admin: all).
- **Audit**: `auth_audit_events` (UTC timestamps, JSON metadata). Passwords,
  tokens, and hashes are stripped by construction and verified by tests.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DUCKLAB_DB_PATH` | recommended | Path to `store_ops.db` (default: inside package) |
| `DUCKLAB_SECURE_COOKIES` | **production: 1** | Marks the session cookie `Secure` (HTTPS only) |
| `DUCKLAB_TRUST_PROXY` | production behind proxy: 1 | Honor `X-Forwarded-For` from private/loopback proxies (rate-limit IP, logging). Never trust client headers otherwise. |
| `DUCKLAB_FRONTEND_ORIGINS` | dev only | Comma-separated extra Origins allowed to call `/api/session` (e.g. `http://localhost:3000`). Empty in production. |

No other secrets exist. There is no JWT signing key; session tokens are
random and server-side validated, so no persistent secret is required yet.

## First-admin bootstrap (one-time, operator-initiated)

```bash
DUCKLAB_DB_PATH=/path/to/store_ops.db \
  .venv/bin/python scripts/bootstrap_admin.py
```

Collects email/name/password interactively (password not echoed), refuses
to run if any Super Admin already exists. No default credentials, no
auto-created accounts.

## Password recovery (current policy)

Email-based reset is **deferred**: no trusted outbound mail delivery exists,
so no unauthenticated recovery endpoint is exposed. Recovery path:
a Super Admin sets a new password via the user-management service
(`AppService.change_password`), which revokes all of the user's sessions.
Admins communicate the new password out-of-band. Documented, not bypassed.

## Reverse proxy / TLS

- Terminate TLS at the proxy; run the Reflex backend behind it.
- Route `/api/*` (and the Reflex WebSocket `/_event`) to the backend; all
  other traffic to the frontend. Same origin — the session endpoint then
  shares the page's origin and no extra CORS config is needed.
- Set `DUCKLAB_SECURE_COOKIES=1` and `DUCKLAB_TRUST_PROXY=1`.
- Session cookie is `HttpOnly`, `SameSite=Lax`, `Path=/`, `Max-Age=43200`.
- **CSRF**: `/api/session` requires `Origin` host == request `Host` (or an
  explicitly allow-listed dev origin). The WebSocket mount rejects
  cross-origin handshakes (`OriginCheckMiddleware`). SameSite=Lax blocks
  cross-site credentialed POSTs from browsers.
- **Rate limiting**: per-email + per-client-IP, SQLite-backed
  (`auth_login_failures` table) — survives restarts and is shared across
  workers on the same host. For multi-host deployments, replace the
  limiter with a shared store (e.g. Redis).
- WebSocket events: every protected event re-validates the session token
  server-side from the connection's request headers, so revocation takes
  effect on the next event, not just on page load.

## Remaining limitations (deferred, documented)

1. **Single-host rate limiter** — SQLite backend covers single-host,
   multi-worker. Multi-host needs a shared store (Redis). Documented.
2. **No email delivery** — invitations/password resets are "admin creates
   and shares credentials out-of-band"; no reset tokens exist yet.
3. **No optimistic concurrency tokens** on operational record edits
   (last-write-wins within RBAC). Documented in the module tasks.
4. Operational (non-security) audit trails for data edits remain future
   work — only security events are audited in `auth_audit_events`.
