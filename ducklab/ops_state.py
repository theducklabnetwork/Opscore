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
from ducklab import store_ops_db
from ducklab.auth_pages import AuthState, app_service
from ducklab.auth_service import AuthenticationError, AuthorizationError, User
from ducklab.operations_repository import (
    ConfigNotFoundError, InvalidMonthError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_snapshots import SnapshotNotFoundError
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


def parse_client_id(raw, what: str, required: bool = False) -> Optional[int]:
    """Parse an id sent over the socket ('' -> None unless ``required``).
    Anything that is not a positive whole number is rejected with
    ValidationError before any state changes."""
    s = str(raw if raw is not None else "").strip()
    if not s and not required:
        return None
    try:
        value = int(s)
    except ValueError:
        value = 0
    if value <= 0:
        raise ValidationError(f"Unknown {what} — reopen it from the list.")
    return value


def month_label(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{calendar.month_name[m]} {y}"


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class OpsState(AuthState):
    """Authenticated operations state: selectors + monthly report."""

    # selectors
    stores: list[dict] = []
    store_options: list[str] = []       # display labels "CODE — Name"
    selected_store_label: str = ""
    month_options: list[str] = []       # labels
    selected_month_label: str = ""
    has_stores: bool = True
    selected_store_active: bool = True

    # report mode
    report_mode: str = "live"           # live | finalized
    versions: list[str] = []            # labels "Version N — finalized at"
    selected_version_label: str = ""

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
        self.report_categories = []
        self.report_stock_codes = []
        self.report_status = "idle"
        self.error_message = ""
        self.finalized_note = ""
        self.versions = []
        self.selected_version_label = ""

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
    def export_report(self) -> rx.event.EventSpec | None:
        """Download the selected live report as an Excel workbook."""
        self.error_message = ""
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            self.error_message = "Select a store and month before exporting."
            return None
        try:
            from ducklab import report_export
            user = self._actor()
            if self.report_mode != "live":
                self.error_message = "Switch to Live to export the report."
                return None
            report = app_service.preview_month(user, store_id, month)
            data = report_export.export_bytes(
                report, app_service.read_repo,
                include_payroll=authz.can_manage_config(user))
            return rx.download(
                data=data, filename=report_export.export_filename(report))
        except AuthenticationError:
            return rx.redirect("/login")
        except (AuthorizationError, ConfigNotFoundError, StoreNotFoundError,
                InvalidMonthError, SnapshotNotFoundError, RepositoryError) as exc:
            self.error_message = str(exc)
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
        if store_id is None or month is None:
            self.report_status = "empty"
            return
        # Re-read the selected store's active flag on EVERY refresh so a
        # store deactivated since page load is shown read-only immediately
        # (the cached store list from load_ops may be stale).
        try:
            user = self._actor()
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
            self._load_report(user, store_id, month, version_rows)
        finally:
            if self.report_status == "loading":  # unexpected failure
                self.report_status = "error"
                self.error_message = "Report unavailable."

    def _load_report(self, user, store_id: int, month: str,
                     version_rows: list[dict]) -> None:
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


def _version_label(row: dict) -> str:
    """Version picker label; created_at shown in Kuala Lumpur time."""
    return f"Version {row['version']} — {ds.fmt_kl_datetime(row['created_at'])}"


def _version_for_label(label: str, version_rows: list[dict]) -> int:
    """Map a picker label back to its version number. Labels arrive over
    the socket, so anything not in the current list is rejected."""
    for row in version_rows:
        if _version_label(row) == label:
            return int(row["version"])
    raise SnapshotNotFoundError(
        "Unknown report version — choose one from the list.")


# ---------------------------------------------------------------------------
# Serialization (server-side formatting; money as ints/strings, no floats)
# ---------------------------------------------------------------------------

def _payroll_gated(values: dict, user) -> dict:
    """Payroll fields (salary, ratios, status) enter serialized state ONLY
    for configuration managers; everyone else gets 'Restricted' markers and
    no figures. Turnover/headcount are headcount-based and always shown."""
    from ducklab import authz as _authz
    if user is not None and _authz.can_manage_config(user):
        return values
    return {**values, "salary": "Restricted", "ratio": "Restricted",
            "ratio_support": "Restricted", "ratio_status": "Restricted"}


_SIGNS = {code: sign for code, _name, sign in store_ops_db.STOCK_LOSS_CODES}


def _report_codes(totals: dict) -> list[dict]:
    """Report stock rows: the treatment label from the code's contribution
    sign (EXTRA reduces total loss: "Offset", never "Included")."""
    rows = []
    for code, total in sorted(totals.items()):
        if not total:
            continue
        sign = _SIGNS.get(code, 0)
        label = ds.TREATMENT[sign]
        rows.append({"code": code, "total": ds.fmt_rm(total),
                     "counts": "1" if sign else "0", "treatment": label,
                     "treatment_color": ds.status_color(label)})
    return rows


def _report_expenses(paid, incurred, open_, budget, share, paid_count,
                     incurred_count) -> dict:
    """Report expense figures with the Expenses page's empty states
    (engine counts): no Paid lines -> "No payments yet"; nothing incurred
    -> "Not reported yet"; pending is a genuine amount (RM 0.00 = zero).
    Counts are None for snapshots taken before they were recorded: the
    amounts then show as before."""
    no_paid = paid_count == 0
    no_incurred = incurred_count == 0
    # the state reads as the figure's sub-line under a dash (a long text
    # value would not fit a two-column phone tile)
    return {
        "paid": "—" if no_paid else ds.fmt_rm(paid),
        "paid_note": "No payments yet" if no_paid else "",
        "incurred": "—" if no_incurred else ds.fmt_rm(incurred),
        "incurred_note": "Not reported yet" if no_incurred else "",
        "pending": ds.fmt_rm(open_),
        "budget": ds.fmt_rm(budget),
        "share": "—" if no_paid else ds.fmt_pct(share),
    }


def _salary_status(status: str, complete: bool) -> str:
    """Same wording as the Manpower page: while the month is incomplete
    the ratio uses sales to date, so the status reads "Month to date: …"."""
    if not complete and status in (kpi.SALARY_WITHIN, kpi.SALARY_FAILURE):
        return f"Month to date: {status}"
    return status


def _projection_card_labels(pstate: str) -> dict:
    """Report card labels for projection-derived figures."""
    return {
        "achievement_label": ds.projection_label(pstate,
                                                 "Achievement vs Target"),
        "growth_year_label": ds.projection_label(pstate,
                                                 "Growth vs Last Year"),
        "growth_month_label": ds.projection_label(pstate,
                                                  "Growth vs Last Month"),
    }


def _effective_staff_display(value, factor_missing: bool) -> str:
    if factor_missing:
        return kpi.PT_FACTOR_NOT_SET
    return ds.fmt_num(float(value) if value is not None else None)


def _serialize_live(rep, user=None) -> dict:
    s, e, m, st, a = (rep.sales, rep.expenses, rep.manpower, rep.stock,
                      rep.actions)
    return {
        "mode": "live",
        "store": f"{rep.store.code} — {rep.store.name}",
        "store_title": f"{rep.store.name} ({rep.store.code})",
        "month": month_label(rep.month),
        "sales": {
            "mtd": ds.fmt_rm(s.sales_mtd_cents),
            "projected": ds.fmt_rm(int(s.month_end_sales_cents)
                                   if s.month_end_sales_cents is not None
                                   else None),
            "target_l1": ds.fmt_rm(s.targets.get("level1")),
            "achievement_l1": ds.fmt_achievement(
                s.achievement_by_level.get("level1"),
                s.status_by_level.get("level1", "")),
            "status_l1": ds.projection_style(
                s.projection_state,
                s.status_by_level.get("level1", "no target"))[0],
            "projection_state": s.projection_state,
            "early_estimate_tag": (
                f"Early estimate: {s.days_entered} of {s.days_in_month} days"
                if s.projection_state == "early_estimate" else ""),
            **_projection_card_labels(s.projection_state),
            "growth_year": ds.fmt_pct(s.growth_last_year),
            "growth_year_status": ds.projection_style(
                s.projection_state, s.growth_status_last_year)[0],
            "growth_month": ds.fmt_pct(s.growth_last_month),
            "growth_month_status": ds.projection_style(
                s.projection_state, s.growth_status_last_month)[0],
            "spr": ds.fmt_rm(int(s.spr) if s.spr is not None else None),
            "upt": ds.fmt_num(float(s.upt) if s.upt is not None else None),
            "asp": ds.fmt_rm(int(s.asp) if s.asp is not None else None),
            "completeness": f"{s.days_entered} of {s.days_in_month} days reported",
            "complete": s.days_entered == s.days_in_month,
        },
        "expenses": {
            **_report_expenses(e.paid_cents, e.incurred_cents, e.open_cents,
                               e.total_budget_cents, e.expense_share_of_sales,
                               e.paid_count, e.incurred_count),
            "budget_control_status": e.budget_control_status,
            "hq_status": e.total_expenses_status,
            "validation": dict(e.validation_counts),
            "categories": [
                {"name": c.name, "paid": ds.fmt_rm(c.paid_mtd_cents),
                 "budget": ds.fmt_rm(c.budget_cents),
                 "status": c.budget_status}
                for c in e.categories
            ],
        },
        "manpower": _payroll_gated({
            "opening": ds.fmt_num(m.opening_headcount),
            "hires": ds.fmt_num(m.hires),
            "resigned": ds.fmt_num(m.resigned),
            "closing": ds.fmt_num(m.closing_headcount),
            "salary": ds.fmt_rm(m.total_salary_cents),
            "ratio": ds.fmt_pct(m.salary_ratio_store),
            "ratio_support": ds.fmt_pct(m.salary_ratio_incl_support),
            "ratio_status": _salary_status(
                m.salary_ratio_status, s.days_entered == s.days_in_month),
            "ratio_mtd": "" if s.days_entered == s.days_in_month else "1",
            "turnover": ds.fmt_pct(m.turnover_pct),
            "turnover_status": m.turnover_status,
            "leave_days": ds.fmt_num(m.leave_days),
            "effective_staff": _effective_staff_display(
                m.average_effective_staff, m.pt_factor_missing),
            "pt_factor_label": ds.pt_factor_label(
                rep.config.part_timer_factor),
        }, user),
        "stock": {
            "total_loss": ds.fmt_rm(st.total_loss_cents),
            "loss_pct": ds.fmt_pct(st.loss_pct_of_sales),
            "status": kpi.stock_status_label(
                st.total_loss_cents, s.sales_mtd_cents,
                rep.config.stock_loss_target, s.days_entered),
            "codes": _report_codes(st.totals_by_code),
        },
        "actions": {
            # engine counts: total = kpi.action_counts "total"; open =
            # engine's Open + In progress + Blocked (kpi.open_actions).
            "total": a.counts["total"],
            "open": a.open,
            "in_progress": a.counts.get("in_progress", 0),
            "blocked": a.counts.get("blocked", 0),
            "done": a.counts.get("done", 0),
            "overdue": a.counts.get("overdue", 0),
            "completion": ds.fmt_pct(a.completion_pct),
        },
    }


def _serialize_snapshot(snap: dict, user=None) -> dict:
    rep = snap["report"]
    s, e, m, st, a = (rep["sales"], rep["expenses"], rep["manpower"],
                      rep["stock"], rep["actions"])
    counts = a.get("counts", {})
    cfg = rep.get("config") or {}
    store = rep.get("store", {})
    store_label = (f"{store['code']} — {store['name']}"
                   if isinstance(store, dict) and "code" in store else "")
    store_title = (f"{store['name']} ({store['code']})"
                   if isinstance(store, dict) and "code" in store else "")
    codes = _report_codes(st.get("totals_by_code", {}))
    # Same display rules as live: a finalized complete month is "actual"
    # (Month-End), partial months are labelled projected/early estimate.
    entered = s.get("days_entered") or 0
    dim = s.get("days_in_month") or 0
    pstate = kpi.projection_state(entered, dim)
    growth_year_status = s.get("growth_status_last_year") or kpi.growth_status(
        s.get("growth_last_year"), entered)
    growth_month_status = s.get("growth_status_last_month") or kpi.growth_status(
        s.get("growth_last_month"), entered)
    return {
        "mode": "finalized",
        "store": store_label,
        "store_title": store_title,
        "month": month_label(rep.get("month", snap["month"])),
        "sales": {
            "mtd": ds.fmt_rm(s.get("sales_mtd_cents")),
            "projected": ds.fmt_rm(
                int(s["month_end_sales_cents"])
                if s.get("month_end_sales_cents") is not None else None),
            "target_l1": ds.fmt_rm(s.get("targets", {}).get("level1")),
            "achievement_l1": ds.fmt_achievement(
                s.get("achievement_by_level", {}).get("level1"),
                s.get("status_by_level", {}).get("level1", "")),
            "status_l1": ds.projection_style(
                pstate,
                s.get("status_by_level", {}).get("level1", "no target"))[0],
            "projection_state": pstate,
            "early_estimate_tag": (
                f"Early estimate: {entered} of {dim} days"
                if pstate == "early_estimate" else ""),
            **_projection_card_labels(pstate),
            "growth_year": ds.fmt_pct(s.get("growth_last_year")),
            "growth_year_status": ds.projection_style(
                pstate, growth_year_status)[0],
            "growth_month": ds.fmt_pct(s.get("growth_last_month")),
            "growth_month_status": ds.projection_style(
                pstate, growth_month_status)[0],
            "spr": ds.fmt_rm(int(s["spr"]) if s.get("spr") is not None
                             else None),
            "upt": ds.fmt_num(float(s["upt"]) if s.get("upt") is not None
                              else None),
            "asp": ds.fmt_rm(int(s["asp"]) if s.get("asp") is not None
                             else None),
            "completeness": (f"{s.get('days_entered')} of "
                             f"{s.get('days_in_month')} days reported"),
            "complete": s.get("days_entered") == s.get("days_in_month"),
        },
        "expenses": {
            **_report_expenses(e.get("paid_cents"), e.get("incurred_cents"),
                               e.get("open_cents"),
                               e.get("total_budget_cents"),
                               e.get("expense_share_of_sales"),
                               e.get("paid_count"), e.get("incurred_count")),
            "hq_status": e.get("total_expenses_status", ""),
            "validation": dict(e.get("validation_counts", {})),
            "categories": [
                {"name": c["name"], "paid": ds.fmt_rm(c.get("paid_mtd_cents")),
                 "budget": ds.fmt_rm(c.get("budget_cents")),
                 "status": c.get("budget_status", "")}
                for c in e.get("categories", [])
            ],
        },
        "manpower": _payroll_gated({
            "opening": ds.fmt_num(m.get("opening_headcount")),
            "hires": ds.fmt_num(m.get("hires")),
            "resigned": ds.fmt_num(m.get("resigned")),
            "closing": ds.fmt_num(m.get("closing_headcount")),
            "salary": ds.fmt_rm(m.get("total_salary_cents")),
            "ratio": ds.fmt_pct(m.get("salary_ratio_store")),
            "ratio_support": ds.fmt_pct(m.get("salary_ratio_incl_support")),
            "ratio_status": _salary_status(
                m.get("salary_ratio_status", ""), entered == dim),
            "ratio_mtd": "" if entered == dim else "1",
            "turnover": ds.fmt_pct(m.get("turnover_pct")),
            "turnover_status": m.get("turnover_status", ""),
            "leave_days": ds.fmt_num(m.get("leave_days")),
            "effective_staff": _effective_staff_display(
                m.get("average_effective_staff"),
                m.get("pt_factor_missing", False)),
            "pt_factor_label": ds.pt_factor_label(
                cfg.get("part_timer_factor")),
        }, user),
        "stock": {
            "total_loss": ds.fmt_rm(st.get("total_loss_cents")),
            "loss_pct": ds.fmt_pct(st.get("loss_pct_of_sales")),
            "status": (kpi.stock_status_label(
                st.get("total_loss_cents") or 0, s.get("sales_mtd_cents"),
                cfg.get("stock_loss_target"), entered)
                if cfg else st.get("status", "")),
            "codes": codes,
        },
        "actions": {
            "total": counts.get("total", 0),
            "open": a.get("open"),
            "in_progress": counts.get("in_progress", 0),
            "blocked": counts.get("blocked", 0),
            "done": counts.get("done", 0),
            "overdue": counts.get("overdue", 0),
            "completion": ds.fmt_pct(a.get("completion_pct")),
        },
    }
