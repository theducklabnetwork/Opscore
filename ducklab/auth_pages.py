"""DuckLab login page and authenticated session state.

Session transport: the session token lives in a server-issued, HttpOnly,
SameSite=Lax cookie set by POST /api/session (see session_transport.py).
It is never stored in Reflex state, client storage, or event payloads.
On every protected event, handlers resolve the authenticated user from
the connection's server-side request headers via `_actor()` — logout,
expiry, deactivation, and password changes take effect immediately.
"""

from __future__ import annotations

from datetime import date

import reflex as rx

from ducklab import auth_service
from ducklab import ops_charts
from ducklab import platform_utils
from ducklab.app_service import AppService
from ducklab.auth_service import AuthenticationError
from ducklab.session_transport import (
    DB_PATH, SESSION_COOKIE, read_session_token,
)

# The application service is a singleton against the configured ops DB.
# DUCKLAB_DB_PATH overrides the default store_ops.db location.
app_service = AppService(DB_PATH)


class AuthState(rx.State):
    """Authentication state: login form + cookie-backed session."""

    email: str = ""
    password: str = ""
    show_password: bool = False
    loading: bool = False
    error: str = ""
    display_name: str = ""
    is_dark: bool = True   # theme, kept in sync from the client (charts)

    # first-launch bootstrap (Create Account); -1 = not yet checked
    bootstrap_name: str = ""
    bootstrap_email: str = ""
    bootstrap_password: str = ""
    bootstrap_confirm: str = ""
    has_users: bool = True
    # Raw recovery key held in memory exactly once (bootstrap success,
    # recovery success, or settings regeneration) until the operator saves
    # the kit. Never persisted or re-requested from the server.
    recovery_key: str = ""
    recovery_email: str = ""     # account the pending key belongs to
    recovery_kit_source: str = ""  # "bootstrap" | "recovery" | "settings"
    kit_error: str = ""

    # recovery flow ("recover" | "reset" | "done" | "")
    recovery_mode: str = ""
    recovery_key_input: str = ""
    recovery_password: str = ""
    recovery_confirm: str = ""

    @rx.event
    async def toggle_is_dark(self) -> None:
        """Toggle the server theme and restyle charts without reloading data."""
        self.is_dark = not self.is_dark
        # Chart-capable states register themselves in ops_charts at import
        # time (registry, Phase 3.6); inactive pages have no source data and
        # are no-ops.
        for cls in ops_charts.CHART_STATES:
            state = await self.get_state(cls)
            state._apply_chart_theme(self.is_dark)

    def _actor(self) -> auth_service.User:
        """Authenticated identity from trusted server-side context only.

        The session token is read from the HttpOnly cookie carried on the
        WebSocket connection's request headers (never from client state),
        then re-validated against the database on every call.
        """
        return app_service.authenticate(read_session_token(self))

    @rx.event
    def set_email(self, value: str) -> None:
        self.email = value

    @rx.event
    def set_password(self, value: str) -> None:
        self.password = value

    @rx.event
    def set_bootstrap_name(self, value: str) -> None:
        self.bootstrap_name = value

    @rx.event
    def set_bootstrap_email(self, value: str) -> None:
        self.bootstrap_email = value

    @rx.event
    def set_bootstrap_password(self, value: str) -> None:
        self.bootstrap_password = value

    @rx.event
    def set_bootstrap_confirm(self, value: str) -> None:
        self.bootstrap_confirm = value

    @rx.event
    def toggle_password(self) -> None:
        self.show_password = not self.show_password

    @rx.event
    def sign_in(self) -> rx.event.EventSpec | None:
        """Login via the HttpOnly session endpoint; the cookie is set by
        the server response, never by JavaScript."""
        self.loading = True
        self.error = ""
        yield
        yield rx.call_script(
            _LOGIN_JS,
            callback=AuthState.login_result,
        )

    @rx.event
    def login_result(self, result: dict) -> rx.event.EventSpec | None:
        self.loading = False
        self.password = ""
        try:
            ok = isinstance(result, dict) and bool(result.get("ok"))
        except Exception:
            ok = False
        if not ok:
            self.error = "Invalid email or password"
            return None
        # Reset the whole state tree before redirecting: a direct /login
        # (without sign-out) must never hydrate the next session with the
        # previous user's data in this tab.
        self.reset()
        self.password = ""
        # The /api/session endpoint already validated credentials and set
        # the HttpOnly cookie. The current WebSocket connection was opened
        # before login, so its cached request headers do not carry the
        # cookie yet — full navigation reconnects it.
        return rx.call_script("window.location.assign('/ops')")

    @rx.event
    def sign_out(self) -> rx.event.EventSpec:
        try:
            token = read_session_token(self)
            if token:
                app_service.auth.logout(token)
        finally:
            # Reset the whole state tree: nothing from this session may
            # remain visible to the next user of this browser tab.
            self.reset()
        yield rx.call_script(_LOGOUT_JS)
        # Full navigation drops the authenticated socket connection.
        yield rx.call_script("window.location.assign('/login')")

    @rx.event
    def require_auth(self) -> rx.event.EventSpec | None:
        """On-load guard for protected routes. Identity comes from the
        server-side cookie header and is re-validated on every page
        load/event — stale client-side state is never trusted."""
        try:
            user = self._actor()
        except AuthenticationError:
            return rx.redirect("/login")
        self.display_name = user.display_name

    @rx.event
    def root_redirect(self) -> rx.event.EventSpec:
        """Root route (/): send authenticated users to the dashboard and
        everyone else to the login page. Identity is resolved server-side
        from the HttpOnly session cookie — never from client state."""
        try:
            self._actor()
        except AuthenticationError:
            return rx.redirect("/login")
        return rx.redirect("/ops")

    @rx.event
    def check_bootstrap(self) -> None:
        """On /login load: when the database has no users yet, show the
        Create Account screen instead of the sign-in form."""
        self.error = ""
        self.has_users = app_service.auth.user_count() > 0

    @rx.event
    def create_account(self) -> rx.event.EventSpec | None:
        """First-launch bootstrap: create the first (Super Admin) account.

        The handler refuses immediately once any user exists; the session
        endpoint and AuthService.bootstrap_first_admin re-check server-side
        too, so direct calls are equally refused."""
        self.error = ""
        if app_service.auth.user_count():
            self.has_users = True
            self.error = "An account already exists. Please sign in."
            return None
        if self.bootstrap_password != self.bootstrap_confirm:
            self.error = "Passwords do not match"
            return None
        self.loading = True
        yield
        yield rx.call_script(
            _BOOTSTRAP_JS,
            callback=AuthState.bootstrap_result,
        )

    @rx.event
    def bootstrap_result(self, result) -> rx.event.EventSpec | None:
        self.loading = False
        self.bootstrap_password = ""
        self.bootstrap_confirm = ""
        try:
            ok = isinstance(result, dict) and bool(result.get("ok"))
            error = str(result.get("error") or "") if isinstance(
                result, dict) else ""
            key = str(result.get("recovery_key") or "") if isinstance(
                result, dict) else ""
        except Exception:
            ok, error, key = False, "", ""
        if not ok:
            self.has_users = app_service.auth.user_count() > 0
            self.error = error or (
                "Could not create the account. Please try again.")
            return None
        if not key:
            # The account was created, but without its one-time key payload
            # we must not silently return to (or leave) the bootstrap form.
            # Sign-in still works; Settings can issue a replacement key.
            self.has_users = True
            self.error = (
                "Account created, but the recovery key was not received. "
                "Please sign in, then generate a key in Settings → Account.")
            return None
        # The endpoint created the Super Admin account and set the
        # HttpOnly session cookie. The recovery key is shown exactly once:
        # the operator must save the kit before entering the dashboard.
        self.recovery_key = key
        self.recovery_email = self.bootstrap_email
        self.recovery_kit_source = "bootstrap"
        self.kit_error = ""
        self.bootstrap_name = self.bootstrap_email = ""
        return None

    # ------------------------------------------------------------------
    # recovery kit (shown once after bootstrap / recovery / regeneration)
    # ------------------------------------------------------------------
    @rx.event
    def save_recovery_kit(self) -> rx.event.EventSpec | None:
        """Write the recovery kit into the per-user data directory."""
        self.kit_error = ""
        if not self.recovery_key:
            self.kit_error = "No recovery key to save."
            return None
        try:
            platform_utils.write_recovery_kit(self.recovery_key,
                                              self.recovery_email)
        except OSError:
            self.kit_error = (
                "Could not write the kit file. Use Download Kit instead, "
                "or copy the key and keep it private.")
        return None

    @rx.event
    def download_recovery_kit(self) -> rx.event.EventSpec:
        """Offer the same kit as a browser download (no server file)."""
        body = (
            "Opscore account recovery kit\n"
            "============================\n\n"
            f"Account: {self.recovery_email}\n"
            f"Recovery key: {self.recovery_key}\n\n"
            "Use this key on the sign-in page under 'Forgot password?' to "
            "set a new password. The key is single-use: after a successful "
            "recovery a replacement key is issued. Keep this file private; "
            "do not upload, email, or sync it to cloud storage.\n"
        )
        return rx.download(data=body.encode("utf-8"),
                           filename=platform_utils.RECOVERY_KIT_NAME)

    @rx.event
    def finish_bootstrap(self) -> rx.event.EventSpec:
        """Leave the recovery-kit screen after the operator saved it."""
        # Full navigation re-handshakes the WebSocket with the new cookie.
        self.recovery_key = ""
        self.recovery_kit_source = ""
        return rx.call_script("window.location.assign('/ops')")

    @rx.event
    def clear_recovery_kit(self) -> None:
        """Post-recovery kit dismissal: back to the plain sign-in form."""
        self.recovery_key = ""
        self.recovery_email = ""
        self.recovery_kit_source = ""
        self.kit_error = ""
        self.recovery_mode = ""
        self.error = ""

    # ------------------------------------------------------------------
    # forgot-password recovery (single-use recovery key)
    # ------------------------------------------------------------------
    @rx.event
    def open_recovery(self) -> None:
        self.recovery_mode = "recover"
        self.error = ""
        self.recovery_key_input = ""
        self.recovery_password = self.recovery_confirm = ""
        self.show_password = False

    @rx.event
    def close_recovery(self, *args) -> None:
        self.recovery_mode = ""
        self.recovery_key_input = ""
        self.recovery_password = self.recovery_confirm = ""
        self.error = ""

    @rx.event
    def set_recovery_key_input(self, value: str) -> None:
        self.recovery_key_input = value

    @rx.event
    def set_recovery_password(self, value: str) -> None:
        self.recovery_password = value

    @rx.event
    def set_recovery_confirm(self, value: str) -> None:
        self.recovery_confirm = value

    @rx.event
    def submit_recovery_key(self) -> rx.event.EventSpec | None:
        self.error = ""
        if not self.recovery_key_input.strip():
            self.error = "Enter your recovery key."
            return None
        self.recovery_mode = "reset"
        return None

    @rx.event
    def submit_recovery(self) -> rx.event.EventSpec | None:
        self.error = ""
        if self.recovery_password != self.recovery_confirm:
            self.error = "Passwords do not match"
            return None
        if len(self.recovery_password) < auth_service.MIN_PASSWORD_LENGTH:
            self.error = PASSWORD_RULES
            return None
        self.loading = True
        yield
        yield rx.call_script(
            _RECOVERY_JS,
            callback=AuthState.recovery_result,
        )

    @rx.event
    def recovery_result(self, result) -> rx.event.EventSpec | None:
        self.loading = False
        self.recovery_password = self.recovery_confirm = ""
        self.recovery_key_input = ""
        try:
            ok = isinstance(result, dict) and bool(result.get("ok"))
            error = str(result.get("error") or "") if isinstance(
                result, dict) else ""
            key = str(result.get("recovery_key") or "") if isinstance(
                result, dict) else ""
        except Exception:
            ok, error, key = False, "", ""
        if not ok:
            self.error = error or "Invalid recovery key"
            self.recovery_mode = "recover"
            return None
        # Password reset succeeded and all sessions were revoked. Show the
        # replacement key once; the user then returns to sign in normally.
        self.recovery_key = key
        self.recovery_email = ""
        self.recovery_kit_source = "recovery"
        self.kit_error = ""
        self.recovery_mode = "done"
        return None


# fetch() to the session endpoint. Production single-port serves frontend +
# /api/* same-origin (works behind any domain/proxy). Dev has no /api proxy on
# the Vite port, so on connection failure we fall back to the backend port
# (:8000), which is allow-listed via DUCKLAB_FRONTEND_ORIGINS.
# `credentials: "include"` attaches/accepts the HttpOnly cookie; the token
# never appears in JS — only a success boolean crosses the boundary.
_LOGIN_JS = """
(async () => {
  const attempt = (base) => fetch(base + '/api/session', {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'login',
                          email: document.getElementById('login-email')?.value ?? '',
                          password: document.getElementById('login-password')?.value ?? ''}),
  }).catch(() => null);
  let r = await attempt(window.location.origin);
  if (!r || r.status === 405) {  // dev: Vite port has no /api proxy -> :8000
    r = await attempt(window.location.protocol + '//' +
                      window.location.hostname + ':8000');
  }
  return {ok: !!(r && r.ok)};
})()
"""

_RECOVERY_JS = """
(async () => {
  const attempt = (base) => fetch(base + '/api/session', {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'recover',
                          recovery_key: document.getElementById('recovery-key')?.value ?? '',
                          password: document.getElementById('recovery-password')?.value ?? ''}),
  }).catch(() => null);
  let r = await attempt(window.location.origin);
  if (!r || r.status === 405) {  // dev: Vite port has no /api proxy -> :8000
    r = await attempt(window.location.protocol + '//' +
                      window.location.hostname + ':8000');
  }
  if (!r) return {ok: false};
  let body = {};
  try { body = await r.json(); } catch (e) {}
  if (r.ok) return {ok: true, recovery_key: body.recovery_key || ''};
  return {ok: false, error: body.error || ''};
})()
"""

_LOGOUT_JS = """
(async () => {
  const attempt = (base) => fetch(base + '/api/session', {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'logout'}),
  }).catch(() => null);
  const r = await attempt(window.location.origin);
  if (!r || r.status === 405) {
    await attempt(window.location.protocol + '//' +
                  window.location.hostname + ':8000');
  }
})()
"""

_BOOTSTRAP_JS = """
(async () => {
  const attempt = (base) => fetch(base + '/api/session', {
    method: 'POST',
    credentials: 'include',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({action: 'bootstrap',
                          name: document.getElementById('bootstrap-name')?.value ?? '',
                          email: document.getElementById('bootstrap-email')?.value ?? '',
                          password: document.getElementById('bootstrap-password')?.value ?? ''}),
  }).catch(() => null);
  let r = await attempt(window.location.origin);
  if (!r || r.status === 405) {  // dev: Vite port has no /api proxy -> :8000
    r = await attempt(window.location.protocol + '//' +
                      window.location.hostname + ':8000');
  }
  if (!r) return {ok: false};
  let body = {};
  try { body = await r.json(); } catch (e) {}
  if (r.ok) return {ok: true, recovery_key: body.recovery_key || ''};
  return {ok: false, error: body.error || ''};
})()
"""


# ---------------------------------------------------------------------------
# UI
# ---------------------------------------------------------------------------

def _brand() -> rx.Component:
    return rx.image(
        src=rx.asset("opscore-wordmark.svg"),
        alt="Opscore",
        width="100%",
        height="auto",
        display="block",
    )


def _field(label: str, **input_props) -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="medium", color=rx.color("slate", 12)),
        rx.input(size="3", width="100%", variant="surface", **input_props),
        spacing="1",
        align="start",
        width="100%",
    )


PASSWORD_RULES = ("At least 10 characters, with letters and digits.")


def _password_input(label: str, value, setter, fid: str,
                    placeholder: str = "••••••••••") -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="medium",
                color=rx.color("slate", 12)),
        rx.box(
            rx.input(
                placeholder=placeholder,
                type=rx.cond(AuthState.show_password, "text", "password"),
                required=True,
                size="3",
                width="100%",
                variant="surface",
                value=value,
                on_change=setter,
                padding_right="2.5em",
                id=fid,
            ),
            rx.icon_button(
                rx.cond(AuthState.show_password,
                        rx.icon("eye-off", size=16),
                        rx.icon("eye", size=16)),
                variant="ghost",
                size="2",
                on_click=AuthState.toggle_password,
                type="button",
                position="absolute",
                right="6px",
                top="50%",
                transform="translateY(-50%)",
                aria_label="Toggle password visibility",
            ),
            position="relative",
            width="100%",
        ),
        spacing="1",
        align="start",
        width="100%",
    )


def _error_callout() -> rx.Component:
    return rx.cond(
        AuthState.error != "",
        rx.callout(
            AuthState.error,
            icon="triangle-alert",
            color_scheme="red",
            variant="soft",
            width="100%",
            role="alert",
        ),
    )


def _bootstrap_form() -> rx.Component:
    """First-launch Create Account screen (only while zero users exist)."""
    return rx.form(
        rx.vstack(
            rx.heading("Create Account", size="5"),
            rx.text(
                "Welcome to Opscore. Create the first account — "
                "it becomes the Super Admin.",
                size="2", color=rx.color("slate", 11),
            ),
            _field(
                "Name",
                placeholder="Your name",
                required=True,
                value=AuthState.bootstrap_name,
                on_change=AuthState.set_bootstrap_name,
                name="name",
                id="bootstrap-name",
            ),
            _field(
                "Email",
                placeholder="you@example.com",
                type="email",
                required=True,
                value=AuthState.bootstrap_email,
                on_change=AuthState.set_bootstrap_email,
                name="email",
                id="bootstrap-email",
            ),
            _password_input("Password", AuthState.bootstrap_password,
                            AuthState.set_bootstrap_password,
                            "bootstrap-password"),
            _password_input("Confirm Password",
                            AuthState.bootstrap_confirm,
                            AuthState.set_bootstrap_confirm,
                            "bootstrap-confirm"),
            rx.text(PASSWORD_RULES, size="1",
                    color=rx.color("slate", 10)),
            _error_callout(),
            rx.button(
                rx.cond(AuthState.loading, "Creating…", "Create Account"),
                type="submit",
                size="3",
                width="100%",
                loading=AuthState.loading,
                color_scheme="indigo",
                id="bootstrap-submit",
            ),
            spacing="4",
            width="100%",
        ),
        on_submit=lambda _: AuthState.create_account(),
    )


def login_page() -> rx.Component:
    return rx.box(
        rx.box(
            rx.color_mode.button(),
            position="absolute",
            top="1em",
            right="1em",
        ),
        rx.center(
        rx.card(
            rx.vstack(
                _brand(),
                rx.cond(
                    AuthState.recovery_key != "",
                    _recovery_kit_panel(),
                    rx.cond(
                        AuthState.has_users,
                        _sign_in_form(),
                        _bootstrap_form(),
                    ),
                ),
                rx.cond(
                    (AuthState.has_users)
                    & (AuthState.recovery_key == ""),
                    rx.text(
                        "Private access. Accounts are provisioned by your "
                        "administrator.",
                        size="1",
                        color=rx.color("slate", 10),
                        text_align="center",
                    ),
                ),
                spacing="5",
                width="100%",
            ),
            width="min(420px, 92vw)",
            padding="2.5em",
            variant="surface",
        ),
        min_height="100vh",
        width="100%",
        background=rx.color("slate", 1),
        padding="1em",
        ),
        position="relative",
    )


def _recovery_kit_panel() -> rx.Component:
    """Shown exactly once while AuthState.recovery_key is set: after
    bootstrap (Continue to dashboard) and after a successful recovery
    (Back to sign in)."""
    return rx.vstack(
        rx.hstack(
            rx.icon("key-round", size=20, color=rx.color("indigo", 9)),
            rx.heading("Save Your Recovery Key", size="5"),
            spacing="2",
        ),
        rx.text(
            "This key can reset your password if you forget it. It is "
            "single-use and shown only once. Save the kit file now and "
            "keep it private — Opscore cannot display it again.",
            size="2", color=rx.color("slate", 11),
        ),
        rx.code(AuthState.recovery_key, size="2", width="100%",
                style={"overflowWrap": "anywhere", "userSelect": "all"}),
        rx.cond(AuthState.kit_error != "",
                rx.callout(AuthState.kit_error, icon="triangle-alert",
                           color_scheme="red", variant="soft",
                           width="100%", role="alert")),
        rx.vstack(
            rx.button("Save Kit to Data Folder",
                      on_click=AuthState.save_recovery_kit,
                      color_scheme="indigo", size="3", width="100%",
                      id="kit-save"),
            rx.button("Download Kit",
                      on_click=AuthState.download_recovery_kit,
                      variant="soft", color_scheme="indigo", size="3",
                      width="100%", id="kit-download"),
            rx.cond(
                AuthState.recovery_kit_source == "bootstrap",
                rx.button("Continue to Dashboard",
                          on_click=AuthState.finish_bootstrap,
                          color_scheme="indigo", size="3", width="100%",
                          id="kit-done-bootstrap"),
                rx.button("Back to Sign In",
                          on_click=AuthState.clear_recovery_kit,
                          variant="ghost", size="3", width="100%",
                          id="kit-done-recovery"),
            ),
            spacing="3", width="100%",
        ),
        spacing="4",
        width="100%",
    )


def _sign_in_form() -> rx.Component:
    return rx.form(
        rx.vstack(
            _field(
                "Email",
                 placeholder="you@example.com",
                type="email",
                required=True,
                value=AuthState.email,
                on_change=AuthState.set_email,
                name="email",
                id="login-email",
            ),
            rx.vstack(
                rx.text("Password", size="2", weight="medium",
                        color=rx.color("slate", 12)),
                rx.box(
                    rx.input(
                        placeholder="••••••••••",
                        type=rx.cond(AuthState.show_password,
                                     "text", "password"),
                        required=True,
                        size="3",
                        width="100%",
                        variant="surface",
                        value=AuthState.password,
                        on_change=AuthState.set_password,
                        padding_right="2.5em",
                        name="password",
                        id="login-password",
                    ),
                    rx.icon_button(
                        rx.cond(AuthState.show_password,
                                rx.icon("eye-off", size=16),
                                rx.icon("eye", size=16)),
                        variant="ghost",
                        size="2",
                        on_click=AuthState.toggle_password,
                        type="button",
                        position="absolute",
                        right="6px",
                        top="50%",
                        transform="translateY(-50%)",
                        aria_label="Toggle password visibility",
                    ),
                    position="relative",
                    width="100%",
                ),
                spacing="1",
                align="start",
                width="100%",
            ),
            _error_callout(),
            rx.button(
                rx.cond(AuthState.loading, "Signing in…", "Sign In"),
                type="submit",
                size="3",
                width="100%",
                loading=AuthState.loading,
                color_scheme="indigo",
                id="login-submit",
            ),
            rx.link(
                "Forgot password?",
                size="2",
                color=rx.color("indigo", 9),
                on_click=AuthState.open_recovery,
                id="forgot-password",
                cursor="pointer",
                text_align="center",
            ),
            _recovery_dialog(),
            spacing="4",
            width="100%",
        ),
        on_submit=lambda _: AuthState.sign_in(),
    )


def _recovery_dialog() -> rx.Component:
    """Forgot-password flow driven by a single-use recovery key."""
    return rx.dialog.root(
        rx.dialog.content(
            rx.dialog.title("Recover Account"),
            rx.cond(
                AuthState.recovery_mode == "recover",
                rx.vstack(
                    rx.text(
                        "Enter the recovery key from your recovery kit. "
                        "It is single-use: a successful recovery issues a "
                        "replacement key.",
                        size="2", color=rx.color("slate", 11),
                    ),
                    _field(
                        "Recovery Key",
                        placeholder="DLAB-XXXXX-XXXXX-…",
                        required=True,
                        value=AuthState.recovery_key_input,
                        on_change=AuthState.set_recovery_key_input,
                        id="recovery-key",
                    ),
                    _error_callout(),
                    rx.hstack(
                        rx.button("Cancel", variant="soft",
                                  color_scheme="gray",
                                  on_click=AuthState.close_recovery),
                        rx.button("Continue", color_scheme="indigo",
                                  on_click=AuthState.submit_recovery_key,
                                  id="recovery-key-submit"),
                        spacing="3", justify="end", width="100%",
                    ),
                    spacing="4", width="100%",
                ),
                rx.form(
                    rx.vstack(
                        rx.text(
                            "Choose a new password. This immediately "
                            "consumes the recovery key and signs out all "
                            "sessions.",
                            size="2", color=rx.color("slate", 11),
                        ),
                        _field(
                            "New Password",
                            placeholder="••••••••••",
                            type="password",
                            required=True,
                            value=AuthState.recovery_password,
                            on_change=AuthState.set_recovery_password,
                            id="recovery-password",
                        ),
                        _field(
                            "Confirm New Password",
                            placeholder="••••••••••",
                            type="password",
                            required=True,
                            value=AuthState.recovery_confirm,
                            on_change=AuthState.set_recovery_confirm,
                            id="recovery-confirm",
                        ),
                        rx.text(PASSWORD_RULES, size="1",
                                color=rx.color("slate", 10)),
                        _error_callout(),
                        rx.hstack(
                            rx.button("Cancel", variant="soft",
                                      color_scheme="gray", type="button",
                                      on_click=AuthState.close_recovery),
                            rx.button(
                                rx.cond(AuthState.loading,
                                        "Resetting…", "Reset Password"),
                                type="submit",
                                loading=AuthState.loading,
                                color_scheme="indigo",
                                id="recovery-submit"),
                            spacing="3", justify="end", width="100%",
                        ),
                        spacing="4", width="100%",
                    ),
                    on_submit=lambda _: AuthState.submit_recovery(),
                ),
            ),
            max_width="440px",
        ),
        open=AuthState.recovery_mode != "",
        on_open_change=lambda _open: AuthState.close_recovery(),
    )


def ops_landing_page() -> rx.Component:
    """Temporary protected landing page (operational dashboard comes later)."""
    return rx.center(
        rx.card(
            rx.vstack(
                rx.hstack(
                    rx.icon("shield-check", size=24,
                            color=rx.color("indigo", 9)),
                    rx.heading("Opscore Operations", size="6"),
                    spacing="2",
                ),
                rx.text(
                    "Signed in as ",
                    rx.text.strong(AuthState.display_name),
                    size="3",
                ),
                rx.text(
                    "The operations dashboard is under construction. "
                    "This protected area verifies authenticated access only.",
                    size="2", color=rx.color("slate", 11),
                ),
                rx.button(
                    rx.icon("log-out", size=16),
                    "Sign Out",
                    on_click=AuthState.sign_out,
                    variant="soft",
                    color_scheme="red",
                    id="logout-button",
                ),
                spacing="4",
                align="start",
                width="100%",
            ),
            width="min(480px, 92vw)",
            padding="2em",
        ),
        min_height="100vh",
        width="100%",
        background=rx.color("slate", 1),
        padding="1em",
    )


def root_page() -> rx.Component:
    """Root route (/): a minimal shell whose on_load redirects to /ops when
    authenticated or /login otherwise (see AuthState.root_redirect)."""
    return rx.center(
        rx.spinner(size="3"),
        min_height="100vh",
        width="100%",
        background=rx.color("slate", 1),
    )
