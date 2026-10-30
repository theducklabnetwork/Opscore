"""DuckLab operations application: monthly report state (/ops/report).

All data flows through AppService (authenticated + authorized). Report data
is formatted server-side into plain Python structures so monetary values
never pass through binary floats, and client-supplied roles/store IDs are
never trusted — the session token is revalidated on every event.

The finalization/correction workflow and report export live in
ducklab/ops_finalize.py; the event handlers here are thin delegates.

Selectors, store-active status, the shared error surface and tour handlers
live on the flat shared base ducklab/ops_shared_state.SharedOpsState
(Phase 3.6); the names other modules import from here stay re-exported so
import paths remain stable.
"""

from __future__ import annotations

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_finalize
from ducklab import report_presenter
from ducklab.auth_pages import app_service
from ducklab.auth_service import AuthenticationError, AuthorizationError
from ducklab.operations_repository import (
    ConfigNotFoundError, InvalidMonthError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_snapshots import SnapshotNotFoundError

# Canonical home is ducklab/ops_shared_state (Phase 3.6); re-exported here
# so existing imports (ducklab modules and tests) keep working.
from ducklab.ops_shared_state import (  # noqa: F401
    ACCESS_DENIED, SESSION_EXPIRED, SharedOpsState, _refresh_store_active,
    auth_message, month_label,
)


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class OpsState(SharedOpsState):
    """Monthly report state (/ops/report): report data, versions,
    finalization — on top of the shared selectors."""

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
    finalized_note: str = ""

    # --- internals ---------------------------------------------------------
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
        """On-load for /ops/report: auth guard + store/month init + report."""
        guard = self.require_auth()
        if guard is not None:
            return guard
        self._populate_selectors(self._actor())
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
        yield from ops_finalize.open_finalize(self, "finalize")

    @rx.event
    def open_correct(self) -> None:
        """Corrections always snapshot live data, not the selected version."""
        yield from ops_finalize.open_finalize(self, "correct")

    @rx.event
    def set_finalize_dialog_open(self, open: bool) -> None:
        ops_finalize.set_finalize_dialog_open(self, open)

    @rx.event
    def save_finalization(self) -> None:
        """Create a new immutable snapshot and explicitly select its head."""
        yield from ops_finalize.save_finalization(self)

    @rx.event
    def export_report(self) -> rx.event.EventSpec | None:
        """Download the selected live or finalized report as Excel."""
        return ops_finalize.export_report(self)

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
        self.versions = [report_presenter._version_label(v)
                         for v in version_rows]
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
                    wanted = report_presenter._version_for_label(
                        self.selected_version_label, version_rows)
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
# Report presentation compatibility wrappers
# ---------------------------------------------------------------------------

def _serialize_live(rep, user=None) -> dict:
    return report_presenter.serialize_live(rep, user=user)


def _serialize_snapshot(snap: dict, user=None) -> dict:
    return report_presenter.serialize_snapshot(snap, user=user)
