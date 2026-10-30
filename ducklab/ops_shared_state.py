"""Shared operations state: the flat base every /ops page state inherits.

Contains only genuinely shared concerns:

* selector data (stores/months) and the selected store's active flag,
  re-read from AppService on every change so a store deactivated since
  page load is shown read-only immediately;
* the shared status/error surface (``report_status`` + ``error_message``)
  that pages render for load failures;
* guided-tour handlers.

Authentication and token handling live on the parent ``AuthState``.
The monthly report itself (data, versions, finalization, export) stays on
``OpsState``; page states inherit directly from this class, never from
another page's state (Phase 3.6 flat shape).
"""

from __future__ import annotations

import calendar
from typing import Optional

import reflex as rx

from ducklab import authz
from ducklab import kpi
from ducklab import report_presenter
from ducklab.auth_pages import AuthState, app_service
from ducklab.auth_service import AuthenticationError, AuthorizationError, User
from ducklab.operations_repository import (
    ConfigNotFoundError, InvalidMonthError, RepositoryError, StoreNotFoundError,
)


SESSION_EXPIRED = "Your session has expired. Please sign in again."
ACCESS_DENIED = "You do not have access to this store."


def auth_message(exc: Exception) -> str:
    """User message for an auth failure raised inside a page handler."""
    if isinstance(exc, AuthenticationError):
        return SESSION_EXPIRED
    return ACCESS_DENIED


def _page_tour_script(page_key: str, user: User) -> rx.event.EventSpec:
    """Launch a page tour with only the Settings tabs this role may see."""
    if page_key == "settings":
        if authz.can_manage_users(user):
            role = "super_admin"
        elif authz.can_manage_config(user):
            role = "administrator"
        else:
            role = "limited"
        return rx.call_script(
            f"window.ducklabTour && window.ducklabTour.start('settings', "
            f"'{role}')")
    return rx.call_script(
        f"window.ducklabTour && window.ducklabTour.start({page_key!r})")


def month_label(month: str) -> str:
    return report_presenter.month_label(month)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class SharedOpsState(AuthState):
    """Flat shared base for all /ops page states: selectors, store-active
    status, shared error surface, tours."""

    # selectors
    stores: list[dict] = []
    store_options: list[str] = []       # display labels "CODE — Name"
    dashboard_store_options: list[str] = []
    selected_store_label: str = ""
    month_options: list[str] = []       # labels
    selected_month_label: str = ""
    has_stores: bool = True
    selected_store_active: bool = True

    # shared status/error surface for page-level load failures
    # status: idle | loading | ok | empty | error
    report_status: str = "idle"
    error_message: str = ""

    # --- internals ---------------------------------------------------------
    def _sync_store_active(self) -> None:
        self.selected_store_active = next(
            (bool(s.get("active", True)) for s in self.stores
             if s["label"] == self.selected_store_label), True)

    def _selected_store_id(self) -> Optional[int]:
        for s in self.stores:
            if s["label"] == self.selected_store_label:
                return s["id"]
        return None

    def _selected_month(self) -> Optional[str]:
        for m in self._month_choices():
            if m[1] == self.selected_month_label:
                return m[0]
        return None

    def _month_choices(self) -> list[tuple[str, str]]:
        # Past 12 months including the current one, newest first.
        today = kpi.kl_today()
        out = []
        y, m = today.year, today.month
        for _ in range(12):
            out.append((f"{y:04d}-{m:02d}", f"{calendar.month_name[m]} {y}"))
            m -= 1
            if m == 0:
                m, y = 12, y - 1
        return out

    def _clear_shared_status(self) -> None:
        self.report_status = "idle"
        self.error_message = ""

    def _populate_selectors(self, user) -> None:
        stores = [
            {**s, "label": f"{s['code']} — {s['name']}"}
            for s in app_service.list_my_stores(user)
        ]
        self.stores = stores
        self.store_options = [s["label"] for s in stores]
        self.dashboard_store_options = ["All Stores", *self.store_options]
        self.has_stores = bool(stores)
        self._sync_store_active()
        self.month_options = [label for _, label in self._month_choices()]
        if stores and self._selected_store_id() is None:
            self.selected_store_label = stores[0]["label"]
        if self._selected_month() is None:
            self.selected_month_label = self.month_options[0]

    # --- tours --------------------------------------------------------------
    @rx.event
    def tour_finished(self, _result=None) -> None:
        """Guided tour closed (finished or skipped): persist for this user.

        Triggered by assets/tour.js clicking the hidden #tour-done-trigger
        button in the app shell. The server-side user identity comes from
        the session cookie, never from the client payload."""
        try:
            user = self._actor()
        except AuthenticationError:
            return
        if not user.tour_done:
            app_service.auth.mark_tour_done(user.id)

    @rx.event
    def maybe_start_page_tour(self, page_key: str) -> rx.event.EventSpec | None:
        """Start a page tour on its first visit, after the welcome tour."""
        try:
            user = self._actor()
        except AuthenticationError:
            return rx.redirect("/login")
        if (not user.tour_done or page_key not in app_service.auth.PAGE_TOUR_KEYS
                or app_service.auth.is_page_tour_done(user.id, page_key)):
            return None
        return _page_tour_script(page_key, user)

    @rx.event
    def replay_page_tour(self, page_key: str) -> rx.event.EventSpec | None:
        """Replay on demand without changing the stored completion flag."""
        try:
            user = self._actor()
        except AuthenticationError:
            return rx.redirect("/login")
        if page_key not in app_service.auth.PAGE_TOUR_KEYS:
            return None
        return _page_tour_script(page_key, user)

    @rx.event
    def page_tour_finished(self, page_key: str, _result=None) -> None:
        """Persist finish or skip for this user and page."""
        try:
            user = self._actor()
        except AuthenticationError:
            return
        if page_key in app_service.auth.PAGE_TOUR_KEYS:
            app_service.auth.mark_page_tour_done(user.id, page_key)

    # --- loading + store/month changes --------------------------------------
    @rx.event
    def load_selectors(self) -> rx.event.EventSpec | None:
        """On-load for every /ops page: auth guard + store/month init, then
        the shared status re-sync (report data loads on the report page)."""
        guard = self.require_auth()
        if guard is not None:
            return guard
        self._populate_selectors(self._actor())
        yield SharedOpsState.resync_selection()

    @rx.event
    def resync_selection(self) -> None:
        """Re-read the selected store's active flag and refresh the shared
        status surface after a store/month change outside the report page.

        Mirrors the store/month-dependent part of the report refresh: the
        active flag comes from AppService on every change, and the status
        messages match the report's empty/error outcomes for the live
        selection. The monthly report data itself is loaded only by the
        report page, which fully refreshes on its own on_load/on_change."""
        if not self.has_stores:
            self.report_status = "empty"
            self.error_message = "No stores are assigned to your account."
            return
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            self.report_status = "empty"
            return
        try:
            user = self._actor()
            self.selected_store_active, lookup_error = _refresh_store_active(
                app_service, user, store_id, self.stores,
                self.selected_store_label)
        except AuthenticationError:
            self.report_status = "error"
            self.error_message = SESSION_EXPIRED
            return
        if lookup_error:
            self.report_status = "error"
            self.error_message = lookup_error
            return
        self.report_status = "loading"
        yield
        try:
            app_service.preview_month(user, store_id, month)
            self.report_status = "ok"
            self.error_message = ""
        except ConfigNotFoundError:
            self.report_status = "empty"
            self.error_message = (
                f"No monthly configuration exists for {month_label(month)}. "
                "An administrator must configure the month before "
                "reporting.")
        except (StoreNotFoundError, InvalidMonthError) as exc:
            self.report_status = "error"
            self.error_message = str(exc)
        except AuthorizationError:
            self.report_status = "error"
            self.error_message = "You do not have access to this report."
        except AuthenticationError:
            self.report_status = "error"
            self.error_message = SESSION_EXPIRED
        except RepositoryError as exc:
            self.report_status = "error"
            self.error_message = f"Report unavailable: {exc}"
        except Exception:
            self.report_status = "error"
            self.error_message = "Report unavailable."

    @rx.event
    def select_store(self, label: str) -> None:
        """Shared default: set the store and re-sync (pages that override
        this reload their own data afterwards)."""
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._sync_store_active()
        self._clear_shared_status()
        yield SharedOpsState.resync_selection()

    @rx.event
    def select_month(self, label: str) -> None:
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_shared_status()
        yield SharedOpsState.resync_selection()


# ---------------------------------------------------------------------------
# Refresh helpers (pure; testable without a Reflex runtime)
# ---------------------------------------------------------------------------

def _refresh_store_active(service, user, store_id: int, stores: list[dict],
                          label: str) -> tuple[bool, str]:
    """-> (store active?, error message). Looks the store up through
    AppService (access checked first) and updates the cached store entry.
    A failed lookup marks the store inactive (read-only) — never a stale
    "active" flag from the cached list. AuthenticationError propagates."""
    try:
        active = bool(service.get_store(user, store_id).active)
        error = ""
    except StoreNotFoundError:
        active, error = False, "This store no longer exists."
    except AuthorizationError:
        active, error = False, "You do not have access to this store."
    for s in stores:
        if s["label"] == label:
            s["active"] = active
    return active, error
