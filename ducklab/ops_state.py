"""DuckLab operations application: shell, navigation, monthly report.

All data flows through AppService (authenticated + authorized). Report data
is formatted server-side into plain Python structures so monetary values
never pass through binary floats, and client-supplied roles/store IDs are
never trusted — the session token is revalidated on every event.
"""

from __future__ import annotations

import calendar
from datetime import date
from typing import Optional

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab import report_presenter
from ducklab.input_validation import parse_client_id
from ducklab.auth_pages import AuthState, app_service
from ducklab.auth_service import AuthenticationError, AuthorizationError, User
from ducklab.operations_repository import (
    ConfigNotFoundError, InvalidMonthError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_snapshots import (
    SnapshotNotFoundError, finalization_warnings, snapshot_to_report,
)
from ducklab.operations_write_repository import ValidationError


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

class OpsState(AuthState):
    """Authenticated operations state: selectors + monthly report."""

    # selectors
    stores: list[dict] = []
    store_options: list[str] = []       # display labels "CODE — Name"
    dashboard_store_options: list[str] = []
    selected_store_label: str = ""
    month_options: list[str] = []       # labels
    selected_month_label: str = ""
    has_stores: bool = True
    selected_store_active: bool = True

    # report mode
    report_mode: str = "live"           # live | finalized
    versions: list[str] = []            # labels "Version N — finalized at"
    selected_version_label: str = ""

    # Finalization/correction controls are convenience only; AppService
    # remains the authorization and version-allocation authority.
    can_finalize: bool = False
    live_preflight_ok: bool = False
    finalize_dialog_open: bool = False
    finalize_action: str = "finalize"       # finalize | correct
    finalize_month_label: str = ""
    finalize_next_version: int = 1
    finalize_warnings: list[str] = []
    finalize_error: str = ""
    finalize_saving: bool = False

    # Portfolio presentation is deliberately separate from the single-store
    # report structure: All Stores is always live and has no versions.
    all_stores: bool = False
    portfolio: dict[str, str] = {}
    portfolio_summary: list[dict[str, str]] = []
    portfolio_rows: list[dict[str, str]] = []

    # report data (plain dict, preformatted)
    report: dict[str, dict] = {}

    @rx.var
    def sales(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.report.get("sales", {}).items()}

    @rx.var
    def expenses(self) -> dict[str, str]:
        e = self.report.get("expenses", {})
        return {k: str(v) for k, v in e.items() if k != "categories"}

    @rx.var
    def manpower(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.report.get("manpower", {}).items()}

    @rx.var
    def stock(self) -> dict[str, str]:
        s = self.report.get("stock", {})
        return {k: str(v) for k, v in s.items() if k != "codes"}

    @rx.var
    def actions(self) -> dict[str, str]:
        return {k: str(v) for k, v in self.report.get("actions", {}).items()}

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

    @rx.var
    def report_heading(self) -> str:
        """Title line: "Store A (S01) · September 2026"."""
        s = self.report.get("store", {})
        title = s.get("title") or s.get("label", "")
        m = self.report.get("month", {}).get("label", "")
        return f"{title} · {m}" if title else ""
    @rx.var
    def report_colors(self) -> dict[str, str]:
        """Chip colours for the report's section statuses (display only;
        projected sales statuses are neutral amber, as ds.projection_style)."""
        sales = str(self.report.get("sales", {}).get("status_l1", ""))
        return {
            "sales": ("amber" if sales.startswith("Projected: ")
                      else ds.status_color(sales)),
            "manpower": ds.status_color(
                str(self.report.get("manpower", {}).get("ratio_status", ""))
                .removeprefix("Month to date: ")),
            "expenses": ds.status_color(
                str(self.report.get("expenses", {}).get("hq_status", ""))),
            "stock": ds.status_color(
                str(self.report.get("stock", {}).get("status", ""))),
        }

    @rx.var
    def report_category_rows(self) -> list[dict[str, str]]:
        return [{**c, "color": ds.status_color(c.get("status", ""))}
                for c in self.report_categories]

    # untyped detail vars accessed via to(...) casts in components
    report_categories: list[dict[str, str]] = []
    report_stock_codes: list[dict[str, str]] = []
    report_status: str = "idle"         # idle | loading | ok | empty | error
    error_message: str = ""
    finalized_note: str = ""

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

    def _clear_report(self) -> None:
        self.report = {}
        self.all_stores = False
        self.portfolio = {}
        self.portfolio_summary = []
        self.portfolio_rows = []
        self.report_categories = []
        self.report_stock_codes = []
        self.report_status = "idle"
        self.error_message = ""
        self.finalized_note = ""
        self.versions = []
        self.selected_version_label = ""
        self.live_preflight_ok = False
        self.finalize_dialog_open = False
        self.finalize_error = ""
        self.finalize_saving = False

    # --- events -------------------------------------------------------------
    @rx.event
    def load_ops(self) -> rx.event.EventSpec | None:
        """On-load for all /ops pages: auth guard + store/month init."""
        guard = self.require_auth()
        if guard is not None:
            return guard
        user = self._actor()
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
        return OpsState.refresh_report()

    @rx.event
    def select_store(self, label: str) -> None:
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._sync_store_active()
        self._clear_report()  # never show stale cross-store data
        yield OpsState.refresh_report()

    @rx.event
    def select_month(self, label: str) -> None:
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_report()
        yield OpsState.refresh_report()

    @rx.event
    def select_mode(self, mode) -> None:
        self.report_mode = str(mode)
        self.selected_version_label = ""
        yield OpsState.refresh_report()

    @rx.event
    def select_version(self, label: str) -> None:
        self.selected_version_label = label
        yield OpsState.refresh_report()

    @rx.event
    def open_finalize(self) -> None:
        """Preflight current live data; warnings are informational only."""
        yield from self._open_finalize("finalize")

    @rx.event
    def open_correct(self) -> None:
        """Corrections always snapshot live data, not the selected version."""
        yield from self._open_finalize("correct")

    def _open_finalize(self, action: str) -> None:
        self.finalize_dialog_open = True
        self.finalize_action = action
        self.finalize_error = ""
        self.finalize_warnings = []
        self.finalize_next_version = 1
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            month = self._selected_month()
            if store_id is None or month is None:
                raise SnapshotNotFoundError("Select a store and month first.")
            report = app_service.preview_month(user, store_id, month)
            rows = app_service.list_report_versions(user, store_id, month)
            self.finalize_month_label = month_label(month)
            self.finalize_next_version = max(
                (int(row["version"]) for row in rows), default=0) + 1
            self.finalize_warnings = finalization_warnings(report)
        except Exception as exc:
            self.finalize_error = _finalize_error(exc)
        finally:
            self.finalize_saving = False
        yield

    @rx.event
    def set_finalize_dialog_open(self, open: bool) -> None:
        self.finalize_dialog_open = bool(open)
        if not open:
            self.finalize_error = ""

    @rx.event
    def save_finalization(self) -> None:
        """Create a new immutable snapshot and explicitly select its head."""
        if self.finalize_saving:
            return
        self.finalize_saving = True
        self.finalize_error = ""
        yield
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            month = self._selected_month()
            if store_id is None or month is None:
                raise SnapshotNotFoundError("Select a store and month first.")
            save = (app_service.correct_month if self.finalize_action == "correct"
                    else app_service.finalize_month)
            result = save(user, store_id, month)
            # The service's allocated number is authoritative, even if another
            # operator finalized while this dialog was open.
            rows = app_service.list_report_versions(user, store_id, month)
            row = next(row for row in rows
                       if int(row["version"]) == int(result["version"]))
            self.finalize_dialog_open = False
            self._clear_report()
            self.report_mode = "finalized"
            self.selected_version_label = _version_label(row)
            version_rows = app_service.list_report_versions(user, store_id, month)
            self.versions = [_version_label(v) for v in version_rows]
            self.report_status = "loading"
            self._load_report(user, store_id, month, version_rows)
        except Exception as exc:
            # Keep both this dialog and the previously selected report intact.
            self.finalize_error = _finalize_error(exc)
        finally:
            self.finalize_saving = False

    @rx.event
    def export_report(self) -> rx.event.EventSpec | None:
        """Download the selected live or finalized report as Excel."""
        self.error_message = ""
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            self.error_message = "Select a store and month before exporting."
            return None
        try:
            from ducklab import report_export
            user = self._actor()
            snapshot = None
            if self.report_mode == "finalized":
                wanted = None
                if self.selected_version_label:
                    rows = app_service.list_report_versions(user, store_id, month)
                    wanted = _version_for_label(self.selected_version_label, rows)
                snapshot = app_service.get_finalized_report(
                    user, store_id, month, version=wanted)
                report = snapshot_to_report(snapshot)
            else:
                report = app_service.preview_month(user, store_id, month)
            data = report_export.export_bytes(
                report, app_service.read_repo,
                include_payroll=authz.can_manage_config(user), snapshot=snapshot)
            return rx.download(
                data=data,
                filename=report_export.export_filename(report, snapshot))
        except AuthenticationError:
            return rx.redirect("/login")
        except (AuthorizationError, ConfigNotFoundError, StoreNotFoundError,
                InvalidMonthError, SnapshotNotFoundError, RepositoryError) as exc:
            self.error_message = str(exc)
            return None
        except Exception as exc:
            self.error_message = str(exc) or "The report could not be exported."
            return None

    @rx.event
    def refresh_report(self) -> None:
        # Keep the chosen version across the reset (store/month/mode
        # changes clear it before calling here).
        version_label = self.selected_version_label
        self._clear_report()
        self.selected_version_label = version_label
        if not self.has_stores:
            self.report_status = "empty"
            self.error_message = "No stores are assigned to your account."
            return
        store_id = self._selected_store_id()
        month = self._selected_month()
        if month is None:
            self.report_status = "empty"
            return
        if self.selected_store_label == "All Stores":
            self.all_stores = True
            self.report_mode = "live"
            self.can_finalize = False
            self.selected_store_active = True
            self.report_status = "loading"
            yield
            try:
                from ducklab.ops_overview_state import compute_portfolio
                user = self._actor()
                data = compute_portfolio(user, month, kpi.kl_today())
                self.portfolio = data["portfolio"]
                self.portfolio_summary = data["portfolio_summary"]
                self.portfolio_rows = data["portfolio_rows"]
                self.report_status = "ok"
            except AuthenticationError:
                self.report_status = "error"
                self.error_message = "Your session has expired. Please sign in again."
            except AuthorizationError:
                self.report_status = "error"
                self.error_message = "You do not have access to this report."
            except Exception:
                self.report_status = "error"
                self.error_message = "Report unavailable."
            return
        if store_id is None:
            self.report_status = "empty"
            return
        # Re-read the selected store's active flag on EVERY refresh so a
        # store deactivated since page load is shown read-only immediately
        # (the cached store list from load_ops may be stale).
        try:
            user = self._actor()
            self.can_finalize = authz.can_finalize_reports(user)
            self.selected_store_active, lookup_error = _refresh_store_active(
                app_service, user, store_id, self.stores,
                self.selected_store_label)
        except AuthenticationError:
            self.report_status = "error"
            self.error_message = "Your session has expired. Please sign in again."
            return
        if lookup_error:
            self.report_status = "error"
            self.error_message = lookup_error
            return
        # Available finalized versions (metadata only).
        try:
            version_rows = app_service.list_report_versions(user, store_id, month)
        except AuthorizationError:
            version_rows = []
        self.versions = [_version_label(v) for v in version_rows]
        self.report_status = "loading"
        yield
        try:
            # This live calculation is a separate preflight from the selected
            # finalized report. It drives warnings and correction availability;
            # a healthy snapshot must never imply that live data can be saved.
            try:
                live = app_service.preview_month(user, store_id, month)
                self.live_preflight_ok = True
            except (ConfigNotFoundError, StoreNotFoundError, InvalidMonthError,
                    AuthorizationError, RepositoryError):
                live = None
                self.live_preflight_ok = False
            self._load_report(user, store_id, month, version_rows, live=live)
        finally:
            if self.report_status == "loading":  # unexpected failure
                self.report_status = "error"
                self.error_message = "Report unavailable."

    def _load_report(self, user, store_id: int, month: str,
                     version_rows: list[dict], *, live=None) -> None:
        try:
            if self.report_mode == "finalized":
                if not version_rows:
                    raise SnapshotNotFoundError(
                        "No finalized snapshot exists for this store and month")
                wanted = None
                if self.selected_version_label:
                    wanted = _version_for_label(self.selected_version_label,
                                                version_rows)
                snap = app_service.get_finalized_report(user, store_id, month,
                                                        version=wanted)
                data = _serialize_snapshot(snap, user=user)
                self.finalized_note = (
                    f"Finalized version {snap['version']} · "
                    f"{ds.fmt_kl_datetime(snap['created_at'])}"
                    + (" (superseded)" if snap["superseded_by"] else ""))
            else:
                if live is None:
                    live = app_service.preview_month(user, store_id, month)
                data = _serialize_live(live, user=user)
                self.finalized_note = ""
            self.report = {k: v for k, v in data.items()
                           if isinstance(v, dict)}
            self.report["mode"] = {"label": data["mode"]}
            self.report["store"] = {"label": data["store"],
                                    "title": data.get("store_title", "")}
            self.report["month"] = {"label": data["month"]}
            self.report_categories = data["expenses"]["categories"]
            self.report_stock_codes = data["stock"]["codes"]
            self.report_status = "ok"
        except SnapshotNotFoundError as exc:
            self.report_status = "empty"
            self.error_message = str(exc)
        except ConfigNotFoundError:
            self.report_status = "empty"
            self.error_message = (
                f"No monthly configuration exists for {month_label(month)}. "
                "An administrator must configure the month before reporting.")
        except (StoreNotFoundError, InvalidMonthError) as exc:
            self.report_status = "error"
            self.error_message = str(exc)
        except AuthorizationError:
            self.report_status = "error"
            self.error_message = "You do not have access to this report."
        except AuthenticationError:
            self.report_status = "error"
            self.error_message = "Your session has expired. Please sign in again."
        except RepositoryError as exc:
            self.report_status = "error"
            self.error_message = f"Report unavailable: {exc}"


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


def _finalize_error(exc: Exception) -> str:
    if isinstance(exc, (AuthenticationError, AuthorizationError)):
        return auth_message(exc)
    return str(exc) or "The report could not be finalized."


def _version_label(row: dict) -> str:
    """Version label based on the active-head link, never version order."""
    label = (f"Version {row['version']} — "
             f"{ds.fmt_kl_datetime(row['created_at'])}")
    if "superseded_by" in row:
        label += " — " + ("Superseded" if row["superseded_by"] is not None
                          else "Current")
    return label


def _version_for_label(label: str, version_rows: list[dict]) -> int:
    """Map a picker label back to its version number. Labels arrive over
    the socket, so anything not in the current list is rejected."""
    for row in version_rows:
        if _version_label(row) == label:
            return int(row["version"])
    raise SnapshotNotFoundError(
        "Unknown report version — choose one from the list.")


# ---------------------------------------------------------------------------
# Report presentation compatibility wrappers
# ---------------------------------------------------------------------------

def _serialize_live(rep, user=None) -> dict:
    return report_presenter.serialize_live(rep, user=user)


def _serialize_snapshot(snap: dict, user=None) -> dict:
    return report_presenter.serialize_snapshot(snap, user=user)
