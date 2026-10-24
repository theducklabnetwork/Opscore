"""Settings page (/ops/settings): Account, Reference Data and About tabs.

Tab visibility is role-driven: TABS lists every tab with the roles that
may see it, so later tabs plug in by adding one entry. Account and About
are for every authenticated user; Reference Data (expense categories +
Daily Sales Contribution import) is for configuration managers
(Super Admin and Administrator).
"""

from __future__ import annotations

import os
import subprocess
import tempfile

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import dsc_import
from ducklab import store_ops_db, ui
from ducklab.auth_pages import app_service
from ducklab.auth_service import (
    AuthError, AuthenticationError, AuthorizationError, ValidationError,
)
from ducklab.operations_repository import StoreNotFoundError
from ducklab.operations_write_repository import (
    DuplicateRecordError, RecordNotFoundError,
)
from ducklab.ops_shell import shell
from ducklab.ops_state import OpsState, SESSION_EXPIRED, parse_client_id
from ducklab.session_transport import DB_PATH

APP_NAME = "Opscore"

# (key, label, roles that see the tab; None = every authenticated user).
# Later tabs plug in here (e.g. ("users", "Users", {authz.SUPER_ADMIN})).
# Trigger labels for new tabs also need a branch in the rx.match in
# settings_page (a dict cannot be indexed by a Var).
TABS = (
    ("account", "Account", None),
    ("reference", "Reference Data", authz.CONFIG_MANAGERS),
    ("about", "About", None),
)

PASSWORD_RULES = ("At least 10 characters, with letters and digits. "
                  "Changing your password signs out all other sessions.")

DSC_UPLOAD_ID = "dsc-upload"
DSC_DESCRIPTION = (
    "The workbook's DSC and SPR sheets replace the Daily Sales "
    "Contribution shares and SPR/IPR benchmarks for every store. The "
    "import is all-or-nothing: a rejected workbook changes nothing.")


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
    """Account + Reference Data + About. Identity from the session,
    re-validated on every event."""

    account_email: str = ""
    account_name: str = ""
    is_super_admin: bool = False
    can_manage_reference: bool = False
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

    # --- Reference Data: expense categories (per selected store) --------
    ref_store_label: str = ""
    ref_categories: list[dict[str, str]] = []
    ref_error: str = ""
    ref_notice: str = ""
    cat_new_name: str = ""
    cat_saving: bool = False
    rename_open: bool = False
    rename_id: str = ""
    rename_name: str = ""
    rename_error: str = ""

    # --- Reference Data: DSC workbook import ----------------------------
    ref_pairs: int = 0
    ref_benchmark_months: int = 0
    ref_imported_at: str = ""        # "" = never imported
    ref_has_data: bool = False
    dsc_pending_name: str = ""       # original filename awaiting confirm
    dsc_confirm_open: bool = False
    dsc_error: str = ""
    dsc_result: list[str] = []       # summary + warnings after import
    dsc_importing: bool = False
    _dsc_pending_path: str = ""      # server-side temp file (never client)

    @rx.event
    def load_settings(self) -> rx.event.EventSpec | None:
        try:
            user = self._actor()
        except AuthenticationError:
            return rx.redirect("/login")
        self.account_email = user.email
        self.account_name = user.display_name
        self.is_super_admin = user.is_super_admin
        self.can_manage_reference = authz.can_manage_config(user)
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
        if self.can_manage_reference:
            self._load_reference(user)

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

    # --- internals --------------------------------------------------------
    def _load_reference(self, user) -> None:
        """Reference tab data: store list (shared selector), categories
        for the selected store and the DSC "what's loaded" summary."""
        if not self.stores:
            self.stores = [
                {**s, "label": f"{s['code']} — {s['name']}"}
                for s in app_service.list_my_stores(user)
            ]
            self.store_options = [s["label"] for s in self.stores]
            self.has_stores = bool(self.stores)
            if self.stores and self._selected_store_id() is None:
                self.selected_store_label = self.stores[0]["label"]
        self.ref_store_label = self.selected_store_label
        self.ref_error = self.ref_notice = ""
        self.dsc_error = ""
        self.dsc_result = []
        self._load_categories(user)
        self._load_dsc_info(user)

    def _load_categories(self, user) -> None:
        self.ref_categories = []
        store_id = self._selected_store_id()
        if store_id is None:
            return
        try:
            cats = app_service.get_expense_categories(user, store_id)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError):
            return
        self.ref_categories = [
            {"id": str(c.id), "name": c.name,
             "first": "1" if i == 0 else "",
             "last": "1" if i == len(cats) - 1 else ""}
            for i, c in enumerate(cats)
        ]

    def _load_dsc_info(self, user) -> None:
        try:
            info = app_service.reference_data_info(user)
        except (AuthenticationError, AuthorizationError):
            return
        self.ref_pairs = info["pairs"]
        self.ref_benchmark_months = info["benchmark_months"]
        self.ref_imported_at = (
            ds.fmt_kl_datetime(info["imported_at"]) if info["imported_at"]
            else "")
        self.ref_has_data = bool(info["pairs"] or info["benchmark_months"])

    def _auth_fail(self, exc: Exception) -> None:
        self.ref_error = SESSION_EXPIRED if isinstance(
            exc, AuthenticationError) else "You do not have access."

    # --- Reference Data events --------------------------------------------
    @rx.event
    def select_ref_store(self, label: str) -> None:
        if label == self.ref_store_label:
            return
        self.ref_store_label = label
        self.selected_store_label = label
        self.ref_error = self.ref_notice = ""
        try:
            user = self._actor()
        except AuthenticationError as exc:
            self._auth_fail(exc)
            return
        self._load_categories(user)

    @rx.event
    def set_cat_new_name(self, v: str) -> None:
        self.cat_new_name = v
        self.ref_error = self.ref_notice = ""

    @rx.event
    def add_category(self) -> None:
        self.ref_error = self.ref_notice = ""
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            if store_id is None:
                self.ref_error = "Select a store first."
                return
            self.cat_saving = True
            # new categories go last in the ordered list
            order = len(self.ref_categories)
            app_service.add_expense_category(
                user, store_id, self.cat_new_name, sort_order=order)
        except (AuthenticationError, AuthorizationError) as exc:
            self._auth_fail(exc)
        except (ValidationError, DuplicateRecordError,
                StoreNotFoundError) as exc:
            self.ref_error = str(exc)
        else:
            self.cat_new_name = ""
            self.ref_notice = "Category added."
            self._load_categories(user)
        finally:
            self.cat_saving = False

    @rx.event
    def open_rename(self, category_id: str) -> None:
        try:
            cid = parse_client_id(category_id, "category", required=True)
        except ValidationError as exc:
            self.ref_error = str(exc)
            return
        row = next((c for c in self.ref_categories
                    if c["id"] == str(cid)), None)
        if row is None:
            self.ref_error = "Unknown category — reload the page."
            return
        self.rename_id = str(cid)
        self.rename_name = row["name"]
        self.rename_error = ""
        self.rename_open = True

    @rx.event
    def set_rename_open(self, v: bool) -> None:
        self.rename_open = v

    @rx.event
    def set_rename_name(self, v: str) -> None:
        self.rename_name = v
        self.rename_error = ""

    @rx.event
    def save_rename(self) -> None:
        self.rename_error = ""
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            if store_id is None:
                self.rename_error = "Select a store first."
                return
            app_service.rename_expense_category(
                user, store_id, parse_client_id(self.rename_id, "category",
                                                required=True),
                self.rename_name.strip())
        except (AuthenticationError, AuthorizationError) as exc:
            self.rename_error = SESSION_EXPIRED if isinstance(
                exc, AuthenticationError) else "You do not have access."
        except (ValidationError, DuplicateRecordError,
                RecordNotFoundError) as exc:
            self.rename_error = str(exc)
        else:
            self.rename_open = False
            self.ref_notice = "Category renamed."
            self._load_categories(user)

    @rx.event
    def move_category(self, category_id: str, direction: str) -> None:
        self.ref_error = self.ref_notice = ""
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            if store_id is None:
                self.ref_error = "Select a store first."
                return
            app_service.move_expense_category(
                user, store_id,
                parse_client_id(category_id, "category", required=True),
                -1 if direction == "up" else 1)
        except (AuthenticationError, AuthorizationError) as exc:
            self._auth_fail(exc)
        except (ValidationError, RecordNotFoundError) as exc:
            self.ref_error = str(exc)
        else:
            self._load_categories(user)

    # --- DSC workbook import ------------------------------------------------
    @rx.event
    async def handle_dsc_upload(self, files: list[rx.UploadFile]) -> None:
        """Stage the uploaded workbook; replacing existing data asks for
        confirmation before anything is written."""
        self.dsc_error = ""
        self.dsc_result = []
        if not files:
            return
        upload = files[0]
        try:
            self._actor()  # re-validate the session before touching data
        except AuthenticationError as exc:
            self._auth_fail(exc)
            return
        self._discard_pending()
        data = await upload.read()
        fd, path = tempfile.mkstemp(
            suffix=".xlsx", prefix="ducklab-dsc-")
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        self._dsc_pending_path = path
        self.dsc_pending_name = upload.filename or "workbook.xlsx"
        if self.ref_has_data:
            self.dsc_confirm_open = True
        else:
            return SettingsState.confirm_dsc_import

    def _discard_pending(self) -> None:
        if self._dsc_pending_path:
            try:
                os.unlink(self._dsc_pending_path)
            except OSError:
                pass
        self._dsc_pending_path = ""

    @rx.event
    def cancel_dsc_import(self) -> None:
        self._discard_pending()
        self.dsc_pending_name = ""
        self.dsc_confirm_open = False

    @rx.event
    def set_dsc_confirm_open(self, v: bool) -> None:
        if not v:
            self.cancel_dsc_import()

    @rx.event
    def confirm_dsc_import(self) -> None:
        self.dsc_error = ""
        self.dsc_result = []
        self.dsc_confirm_open = False
        path = self._dsc_pending_path
        name = self.dsc_pending_name
        if not path:
            self.dsc_error = "Choose a workbook first."
            return
        self.dsc_importing = True
        try:
            user = self._actor()
            result = app_service.import_dsc_workbook(user, path)
        except (AuthenticationError, AuthorizationError) as exc:
            self._auth_fail(exc)
            self.dsc_error = self.ref_error
            self.ref_error = ""
        except dsc_import.DscImportError as exc:
            self.dsc_error = f"Import failed; nothing changed.\n{exc}"
        except OSError as exc:
            self.dsc_error = f"Import failed; nothing changed.\n{exc}"
        else:
            self.dsc_result = [
                f"Imported {result['shares']} day shares "
                f"({result['pairs']} State/Concept pairs) and "
                f"{result['benchmarks']} SPR/IPR benchmarks "
                f"({result['benchmark_months']} Concept/State months) "
                f"from {name}.",
                *[f"Warning: {w} (stores cannot match it)"
                  for w in result["warnings"]],
            ]
            self._load_dsc_info(user)
        finally:
            self.dsc_importing = False
            self._discard_pending()
            self.dsc_pending_name = ""


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


# ---------------------------------------------------------------------------
# Reference Data tab
# ---------------------------------------------------------------------------

def _category_row(cat) -> rx.Component:
    return rx.hstack(
        rx.text(cat["name"], size="2", color=ds.TEXT),
        rx.spacer(),
        rx.icon_button(
            rx.icon("arrow-up", size=14), size="1", variant="soft",
            color_scheme="gray", on_click=S.move_category(cat["id"], "up"),
            disabled=cat["first"] != "", aria_label="Move up",
            id=f"cat-up-{cat['id']}"),
        rx.icon_button(
            rx.icon("arrow-down", size=14), size="1", variant="soft",
            color_scheme="gray",
            on_click=S.move_category(cat["id"], "down"),
            disabled=cat["last"] != "", aria_label="Move down",
            id=f"cat-down-{cat['id']}"),
        rx.button("Rename", size="1", variant="soft", color_scheme="gray",
                  on_click=S.open_rename(cat["id"]),
                  id=f"cat-rename-{cat['id']}"),
        width="100%", align="center", spacing="2",
        padding_y="0.4em", border_bottom=f"1px solid {ui.ROW_BORDER}",
    )


def _categories_section() -> rx.Component:
    return ui.form_section(
        "Expense Categories",
        rx.hstack(
            rx.text("Store", size="2", color=ds.TEXT_MUTED),
            rx.select(S.store_options, value=S.ref_store_label,
                      on_change=S.select_ref_store, id="ref-store"),
            spacing="3", align="center"),
        rx.cond(S.ref_categories.length() > 0,
                rx.vstack(rx.foreach(S.ref_categories, _category_row),
                          spacing="0", width="100%"),
                ui.note("No expense categories configured for this store.")),
        rx.hstack(
            rx.input(placeholder="New category name",
                     value=S.cat_new_name, on_change=S.set_cat_new_name,
                     id="cat-new-name"),
            rx.button(rx.icon("plus", size=14), "Add Category",
                      color_scheme="indigo", on_click=S.add_category,
                      disabled=S.cat_new_name.strip() == "",
                      id="cat-add"),
            spacing="3", align="center", width="100%"),
        hint=("Categories are listed in this order on the Expenses page. "
              "Rename keeps history (transactions use the stable ID); "
              "categories are never deleted."),
        id="ref-categories",
    )


def _dsc_section() -> rx.Component:
    return ui.form_section(
        "Daily Sales Contribution",
        rx.vstack(
            _about_row("State/Concept pairs", S.ref_pairs.to_string(),
                       "ref-pairs"),
            _about_row("Benchmark months", S.ref_benchmark_months.to_string(),
                       "ref-months"),
            _about_row("Last import",
                       rx.cond(S.ref_imported_at != "", S.ref_imported_at,
                               "Never imported"),
                       "ref-imported-at"),
            spacing="0", width="100%"),
        rx.upload(
            rx.vstack(
                rx.button("Choose DSC Workbook (.xlsx)", variant="soft",
                          color_scheme="gray", type="button",
                          id="dsc-choose"),
                rx.text("… or drop it here", size="1",
                        color=ds.TEXT_FAINT),
                spacing="1", align="center"),
            id=DSC_UPLOAD_ID, multiple=False,
            accept={".xlsx": ["application/vnd.openxmlformats-"
                              "officedocument.spreadsheetml.sheet"]},
            max_files=1, no_keyboard=True,
            border=f"1px dashed {ui.CARD_BORDER}", border_radius=ui.RADIUS,
            padding="1em", width="100%",
        ),
        rx.hstack(
            rx.button(
                rx.cond(S.dsc_importing, rx.spinner(size="1"),
                        rx.icon("upload", size=14)),
                "Import Workbook", color_scheme="indigo",
                on_click=S.handle_dsc_upload(
                    rx.upload_files(upload_id=DSC_UPLOAD_ID)),
                disabled=S.dsc_importing, id="dsc-import"),
            rx.cond(S.dsc_pending_name != "",
                    rx.text(S.dsc_pending_name, size="2",
                            color=ds.TEXT_MUTED)),
            spacing="3", align="center"),
        hint=DSC_DESCRIPTION,
        id="ref-dsc",
    )


def _dsc_confirm_dialog() -> rx.Component:
    return rx.alert_dialog.root(
        rx.alert_dialog.content(
            rx.alert_dialog.title("Replace Reference Data?"),
            rx.alert_dialog.description(
                "The existing Daily Sales Contribution shares and "
                "SPR/IPR benchmarks will be replaced. This cannot be "
                "undone."),
            rx.hstack(
                rx.alert_dialog.cancel(
                    rx.button("Cancel", variant="soft",
                              color_scheme="gray", id="dsc-cancel")),
                rx.alert_dialog.action(
                    rx.button("Replace Data", color_scheme="red",
                              on_click=S.confirm_dsc_import,
                              id="dsc-confirm")),
                justify="end", spacing="3"),
        ),
        open=S.dsc_confirm_open, on_open_change=S.set_dsc_confirm_open,
    )


def _rename_dialog() -> rx.Component:
    return ui.form_dialog(
        ui.form_section(
            "Category name",
            ui.form_field(
                "Name",
                rx.input(value=S.rename_name, on_change=S.set_rename_name,
                         required=True, id="rename-name"),
                required=True),
            id="rename-section"),
        open=S.rename_open, on_open_change=S.set_rename_open,
        title="Rename Category",
        subtitle="History is kept: transactions use the stable ID.",
        notices=(rx.cond(S.rename_error != "",
                         ds.error_callout(S.rename_error)),),
        actions=[
            rx.dialog.close(rx.button("Cancel", variant="soft",
                                      color_scheme="gray",
                                      id="rename-cancel")),
            rx.button("Save", color_scheme="indigo",
                      on_click=S.save_rename,
                      disabled=S.rename_name.strip() == "",
                      id="rename-save"),
        ],
        close_id="rename-close", width="420px",
    )


def _reference_tab() -> rx.Component:
    return ui.section_card(
        rx.cond(S.ref_error != "", ds.error_callout(S.ref_error)),
        rx.cond(S.ref_notice != "",
                rx.callout(S.ref_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%", id="ref-notice")),
        ui.form_stack(
            _categories_section(),
            rx.vstack(
                _dsc_section(),
                rx.cond(S.dsc_error != "",
                        rx.callout(S.dsc_error, icon="triangle-alert",
                                   color_scheme="red", variant="soft",
                                   width="100%", id="dsc-error",
                                   white_space="pre-wrap")),
                rx.foreach(
                    S.dsc_result,
                    lambda line: rx.callout(
                        line,
                        icon=rx.cond(line.contains("Warning"),
                                     "triangle-alert", "circle-check"),
                        color_scheme=rx.cond(line.contains("Warning"),
                                             "amber", "green"),
                        variant="soft", width="100%",
                        class_name="dsc-result")),
                spacing="3", width="100%", align="stretch"),
        ),
        _rename_dialog(),
        _dsc_confirm_dialog(),
        title="Reference Data",
        subtitle="Expense categories per store and the Daily Sales "
                 "Contribution workbook that drives daily targets.",
        id="settings-reference",
    )


def settings_page() -> rx.Component:
    return shell(
        "Settings",
        rx.tabs.root(
            rx.tabs.list(
                rx.foreach(
                    S.visible_tabs,
                    lambda key: rx.tabs.trigger(
                        rx.match(
                            key,
                            ("account", "Account"),
                            ("reference", "Reference Data"),
                            "About"),
                        value=key,
                        id=f"settings-tab-{key}")),
            ),
            rx.tabs.content(_account_tab(), value="account"),
            rx.tabs.content(_reference_tab(), value="reference"),
            rx.tabs.content(_about_tab(), value="about"),
            default_value="account",
            width="100%",
        ),
        description="Your account and application information.",
    )
