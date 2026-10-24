"""Settings page (/ops/settings): Account and About tabs.

Tab visibility is role-driven: TABS lists every tab with the roles that
may see it, so later tabs plug in by adding one entry. Account and About
are for every authenticated user.
"""

from __future__ import annotations

import os
import subprocess

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import store_ops_db, ui
from ducklab.auth_pages import app_service
from ducklab.auth_service import (
    AuthError, AuthenticationError, ValidationError,
)
from ducklab.ops_state import OpsState, SESSION_EXPIRED
from ducklab.session_transport import DB_PATH

APP_NAME = "Opscore"

# (key, label, roles that see the tab; None = every authenticated user).
# Later tabs plug in here (e.g. ("users", "Users", {authz.SUPER_ADMIN})).
TABS = (
    ("account", "Account", None),
    ("about", "About", None),
)

PASSWORD_RULES = ("At least 10 characters, with letters and digits. "
                  "Changing your password signs out all other sessions.")


def app_version() -> str:
    """Release id (deploy sets DUCKLAB_RELEASE_ID), else short git commit."""
    release = os.environ.get("DUCKLAB_RELEASE_ID", "").strip()
    if release and release != "dev":
        return release
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5,
        )
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        pass
    return "Unknown"


class SettingsState(OpsState):
    """Account + About. Identity comes from the session, re-validated."""

    account_email: str = ""
    account_name: str = ""
    is_super_admin: bool = False
    visible_tabs: list[str] = []

    # About (populated on load)
    about_version: str = ""
    about_schema: str = ""
    about_db_path: str = ""

    # change-password form
    pw_current: str = ""
    pw_new: str = ""
    pw_confirm: str = ""
    pw_error: str = ""
    pw_notice: str = ""
    pw_saving: bool = False

    @rx.event
    def load_settings(self) -> rx.event.EventSpec | None:
        try:
            user = self._actor()
        except AuthenticationError:
            return rx.redirect("/login")
        self.account_email = user.email
        self.account_name = user.display_name
        self.is_super_admin = user.is_super_admin
        self.visible_tabs = [
            key for key, _label, roles in TABS
            if roles is None or authz.has_role(user, frozenset(roles))
        ]
        self.about_version = app_version()
        self.about_schema = str(store_ops_db.SCHEMA_VERSION)
        # DB path is operational detail: Super Admin only.
        self.about_db_path = str(DB_PATH) if user.is_super_admin else ""
        self.pw_current = self.pw_new = self.pw_confirm = ""
        self.pw_error = self.pw_notice = ""

    @rx.event
    def set_pw_current(self, v: str) -> None:
        self.pw_current = v
        self.pw_error = ""
        self.pw_notice = ""

    @rx.event
    def set_pw_new(self, v: str) -> None:
        self.pw_new = v
        self.pw_error = ""
        self.pw_notice = ""

    @rx.event
    def set_pw_confirm(self, v: str) -> None:
        self.pw_confirm = v
        self.pw_error = ""
        self.pw_notice = ""

    @rx.event
    def change_password(self) -> None:
        self.pw_error = ""
        self.pw_notice = ""
        if self.pw_new != self.pw_confirm:
            self.pw_error = "New passwords do not match."
            return
        try:
            user = self._actor()
        except AuthenticationError:
            self.pw_error = SESSION_EXPIRED
            return
        try:
            app_service.change_password(
                user, user.id, self.pw_new, current_password=self.pw_current)
        except AuthenticationError:
            # self-change re-authentication failed
            self.pw_error = "Current password is incorrect."
        except ValidationError as exc:
            self.pw_error = str(exc)
        except AuthError as exc:
            self.pw_error = str(exc)
        else:
            self.pw_current = self.pw_new = self.pw_confirm = ""
            self.pw_notice = ("Password changed. Other sessions were "
                              "signed out; this tab stays signed in until "
                              "you sign out.")


S = SettingsState


def _password_field(label, value, setter, fid) -> rx.Component:
    return ui.form_field(
        label,
        rx.input(type="password", value=value, on_change=setter,
                 required=True, id=fid),
        required=True)


def _account_tab() -> rx.Component:
    return ui.section_card(
        ui.form_grid(
            ui.form_field("Name", rx.input(value=S.account_name,
                                           read_only=True, id="acct-name")),
            ui.form_field("Email", rx.input(value=S.account_email,
                                            read_only=True, id="acct-email")),
            columns="2"),
        ui.form_section(
            "Change password",
            _password_field("Current password", S.pw_current,
                            S.set_pw_current, "pw-current"),
            _password_field("New password", S.pw_new, S.set_pw_new, "pw-new"),
            _password_field("Confirm new password", S.pw_confirm,
                            S.set_pw_confirm, "pw-confirm"),
            rx.cond(S.pw_error != "", ds.error_callout(S.pw_error)),
            rx.cond(S.pw_notice != "",
                    rx.callout(S.pw_notice, icon="circle-check",
                               color_scheme="green", variant="soft",
                               width="100%", id="pw-notice")),
            rx.hstack(
                rx.button(
                    rx.cond(S.pw_saving, rx.spinner(size="1"),
                            rx.icon("key-round", size=14)),
                    "Change Password", color_scheme="indigo",
                    on_click=S.change_password, disabled=S.pw_saving,
                    id="pw-save"),
                spacing="3", align="center"),
            hint=PASSWORD_RULES, id="pw-section"),
        title="Account",
        subtitle="Your sign-in identity. Name and email are managed by "
                 "an administrator.",
        id="settings-account",
    )


def _about_row(label, value, vid) -> rx.Component:
    return rx.hstack(
        rx.text(label, size="2", color=ds.TEXT_MUTED, weight="medium"),
        rx.spacer(),
        rx.text(value, size="2", color=ds.TEXT, id=vid,
                style={"overflowWrap": "anywhere"}),
        width="100%", align="baseline", spacing="4",
        padding_y="0.5em", border_bottom=f"1px solid {ui.ROW_BORDER}",
    )


def _about_tab() -> rx.Component:
    return ui.section_card(
        rx.vstack(
            _about_row("App", APP_NAME, "about-app"),
            _about_row("Version", S.about_version, "about-version"),
            _about_row("Schema version", S.about_schema, "about-schema"),
            rx.cond(S.is_super_admin,
                    _about_row("Database path", S.about_db_path,
                               "about-db-path")),
            spacing="0", width="100%"),
        title="About",
        subtitle="Version and schema information for support.",
        id="settings-about",
    )


def settings_page() -> rx.Component:
    return ui.shell(
        "Settings",
        rx.tabs.root(
            rx.tabs.list(
                rx.foreach(
                    S.visible_tabs,
                    lambda key: rx.tabs.trigger(
                        rx.cond(key == "account", "Account", "About"),
                        value=key,
                        id=f"settings-tab-{key}")),
            ),
            rx.tabs.content(_account_tab(), value="account"),
            rx.tabs.content(_about_tab(), value="about"),
            default_value="account",
            width="100%",
        ),
        description="Your account and application information.",
    )
