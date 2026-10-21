"""Expenses Management module: register, budgets, payment/verification flows.

All reads/writes go through AppService with the session token revalidated
on every event. Money is parsed to integer cents server-side via the same
Decimal rules as Daily Operations; validation statuses come from the
verified KPI engine (kpi.expense_validation), never duplicated here.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import plotly.graph_objects as go
import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_charts
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError, User,
)
from ducklab.auth_pages import app_service
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import (
    PAYMENT_SOURCES, PAYMENT_STATUSES, VERIFICATIONS, ValidationError,
)
from ducklab.operations_write_repository import RecordNotFoundError
from ducklab.ops_daily_state import DailyState, _parse_money
from ducklab.ops_state import OpsState

PERIOD_OPTIONS = [
    ("incurred", "Incurred this month"),
    ("paid", "Paid this month"),
    ("outstanding", "Outstanding (pending)"),
    ("all", "All transactions"),
]

MAX_ROWS = 100

STATUS_COLOR = {"Paid": "green", "Pending": "amber", "Cancelled": "gray",
                "": "gray"}
VERIFICATION_COLOR = {"Verified": "green", "Unverified": "amber",
                      "Disputed": "red", "": "gray"}
VALIDATION_COLOR = {
    "OK": "green",
    "Incomplete": "gray",
    "Unknown category": "red",
    "Missing paid date": "orange",
    "Check paid date": "orange",
    "Missing paid by": "orange",
    "Missing recorder": "orange",
    "Disputed": "red",
    "Unverified": "amber",
}
BUDGET_COLOR = {"Within budget": "green", "Watch": "amber",
                "OVER BUDGET": "red", "No budget": "gray"}


class ExpensesState(DailyState):
    """Expenses page state (inherits selectors + guards)."""

    can_write_expenses: bool = False
    can_manage_budgets: bool = False
    exp_month_finalized: bool = False
    has_categories: bool = True

    # KPI cards + budget monitor + validation summary
    exp_kpis: dict[str, str] = {}
    budget_rows: list[dict[str, str]] = []
    budget_fig_dark: go.Figure = go.Figure()                 # Plotly budget-vs-actual (dark)
    budget_fig_light: go.Figure = go.Figure()                # Plotly budget-vs-actual (light)
    validation_summary: str = ""

    # register
    all_expense_rows: list[dict[str, str]] = []
    total_rows: str = "0"
    filter_period: str = "incurred"
    filter_category: str = ""       # "" = all; otherwise str(category_id)
    filter_status: str = ""
    filter_verification: str = ""
    search_text: str = ""
    sort_key: str = "incurred"      # incurred | paid | amount
    sort_desc: bool = True

    # --- expense form -----------------------------------------------------
    exp_form_open: bool = False
    exp_form_error: str = ""
    exp_saving: bool = False
    exp_notice: str = ""
    editing_id: str = ""            # "" = new transaction
    e_incurred: str = ""
    e_paid: str = ""
    e_category: str = ""            # category NAME (select shows labels)
    e_description: str = ""
    e_amount: str = ""
    e_paid_by: str = ""
    e_status: str = "Pending"
    e_receipt: str = ""
    e_verification: str = "Unverified"
    e_recorded_by: str = ""
    e_remarks: str = ""
    exp_original: dict[str, str] = {}

    # --- budget form -------------------------------------------------------
    b_category: str = ""
    b_amount: str = ""
    b_error: str = ""
    b_notice: str = ""
    b_saving: bool = False

    # reference lists for selects
    category_names: list[str] = []

    @rx.var
    def filter_category_options(self) -> list[str]:
        return ["All categories", *self.category_names]

    @rx.var
    def period_options(self) -> list[str]:
        return [v for _, v in PERIOD_OPTIONS]

    @rx.var
    def filter_period_label(self) -> str:
        return dict(PERIOD_OPTIONS)[self.filter_period]

    @rx.event
    def set_filter_period_label(self, label: str):
        for k, v in PERIOD_OPTIONS:
            if v == label:
                self.filter_period = k
                return

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _exp_form_values(self) -> dict[str, str]:
        return {a: getattr(self, a) for a in (
            "e_incurred", "e_paid", "e_category", "e_description",
            "e_amount", "e_paid_by", "e_status", "e_receipt",
            "e_verification", "e_recorded_by", "e_remarks")}

    def _category_id(self, name: str) -> Optional[int]:
        try:
            user = self._actor()
            cats = app_service.get_expense_categories(
                user, self._selected_store_id())
        except Exception:
            return None
        for c in cats:
            if c.name == name:
                return c.id
        return None

    @rx.var
    def expense_rows(self) -> list[dict[str, str]]:
        """Filtered/sorted register rows (deterministic ordering)."""
        rows = list(self.all_expense_rows)
        month = self._selected_month()
        if self.filter_period == "paid" and month:
            rows = [r for r in rows if r["paid_iso"][:7] == month]
        elif self.filter_period == "incurred" and month:
            rows = [r for r in rows if r["incurred_iso"][:7] == month]
        elif self.filter_period == "outstanding":
            rows = [r for r in rows if r["status"] == "Pending"]
        if self.filter_category:
            rows = [r for r in rows
                    if r["category_id"] == self.filter_category]
        if self.filter_status:
            rows = [r for r in rows if r["status"] == self.filter_status]
        if self.filter_verification:
            rows = [r for r in rows
                    if r["verification"] == self.filter_verification]
        q = self.search_text.strip().lower()
        if q:
            rows = [r for r in rows if q in r["description"].lower()
                    or q in r["receipt"].lower()]
        key = {"incurred": "incurred_iso", "paid": "paid_iso",
               "amount": "amount_cents"}[self.sort_key]
        rows.sort(key=lambda r: (r[key] == "", r[key]),
                  reverse=self.sort_desc)
        return rows[:MAX_ROWS]

    @rx.var
    def rows_note(self) -> str:
        n = len(self.all_expense_rows)
        shown = len(self.expense_rows)
        if shown < n:
            return f"Showing first {shown} of {n} transactions — refine filters."
        return f"{shown} transaction(s)"

    # ------------------------------------------------------------------
    # selector overrides: keep expense data in sync with store/month
    # ------------------------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self.exp_form_open = False
        self.exp_notice = ""
        self._clear_report()
        yield ExpensesState.refresh_report()
        yield ExpensesState.load_expenses_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self.exp_form_open = False
        self.exp_notice = ""
        self._clear_report()
        yield ExpensesState.refresh_report()
        yield ExpensesState.load_expenses_data()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @rx.event
    def load_expenses(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield OpsState.load_ops()
        yield ExpensesState.load_expenses_data()

    @rx.event
    def load_expenses_data(self) -> None:
        self.all_expense_rows = []
        self.exp_kpis = {}
        self.budget_rows = []
        self.budget_fig_dark = go.Figure()
        self.budget_fig_light = go.Figure()
        self.validation_summary = ""
        self.exp_month_finalized = False
        self.has_categories = True
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None or not self.has_stores:
            return
        try:
            user = self._actor()
        except AuthenticationError:
            self.error_message = "Your session has expired. Please sign in again."
            self.report_status = "error"
            return
        self.can_write_expenses = authz.can_write_operations(user)
        self.can_manage_budgets = authz.can_manage_config(user)
        try:
            self.exp_month_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
        except Exception:
            self.exp_month_finalized = False
        cats = app_service.get_expense_categories(user, store_id)
        self.has_categories = bool(cats)
        self.category_names = [c.name for c in cats]
        records = app_service.get_expense_transactions(user, store_id)
        cat_names = {c.id: c.name for c in cats}
        known_ids = set(cat_names)
        self.all_expense_rows = [
            self._expense_row(r, cat_names, known_ids) for r in records
        ]
        self.total_rows = str(len(self.all_expense_rows))
        # KPIs from the verified engine via the assembled month report
        try:
            rep = app_service.preview_month(user, store_id, month)
            e = rep.expenses
            util = ""
            if e.total_budget_cents:
                util = f"{100.0 * e.paid_cents / e.total_budget_cents:.1f}%"
            variance = None
            if e.total_budget_cents is not None:
                variance = e.total_budget_cents - e.paid_cents
            self.exp_kpis = {
                "paid": ds.fmt_rm(e.paid_cents),
                "incurred": ds.fmt_rm(e.incurred_cents),
                "open": ds.fmt_rm(e.open_cents),
                "budget": ds.fmt_rm(e.total_budget_cents),
                "utilization": util or "—",
                "variance": ds.fmt_rm(variance),
                # Expense Monitor total row H25, labelled to distinguish
                # it from the Overview's HQ status (Summary!E40).
                "status": e.budget_control_status,
                "status_label": "Budget control (vs category budgets)",
                "status_tip": ("Expense Monitor!H25: No budget / OVER "
                               "BUDGET / Watch (paid ≥ 80% of budget) / "
                               "Within budget"),
                "status_color": BUDGET_COLOR[e.budget_control_status],
            }
            non_ok = {k: v for k, v in e.validation_counts.items()
                      if k != "OK" and v}
            self.validation_summary = (
                "  •  ".join(f"{k}: {v}" for k, v in sorted(non_ok.items()))
                if non_ok else "All transactions pass validation.")
            self.budget_rows = []
            for c in e.categories:
                paid = c.paid_mtd_cents
                util_pct = (100.0 * paid / c.budget_cents
                            if c.budget_cents else None)
                remaining = (c.budget_cents - paid
                             if c.budget_cents is not None else None)
                self.budget_rows.append({
                    "category_id": str(c.category_id),
                    "name": c.name,
                    "budget": ds.fmt_rm(c.budget_cents),
                    "paid": ds.fmt_rm(paid),
                    "remaining": ds.fmt_rm(remaining),
                    "util": (f"{util_pct:.1f}%" if util_pct is not None
                             else "—"),
                    "bar": (f"{min(util_pct, 100):.0f}"
                            if util_pct is not None else "0"),
                    "status": c.budget_status,
                    "color": BUDGET_COLOR[c.budget_status],
                })
            # Plotly budget-vs-actual figures (same data as the table)
            names = [c.name for c in e.categories]
            budgets = [c.budget_cents for c in e.categories]
            paid = [c.paid_mtd_cents for c in e.categories]
            self.budget_fig_dark = ops_charts.budget_vs_actual(
                names, budgets, paid, dark=True)
            self.budget_fig_light = ops_charts.budget_vs_actual(
                names, budgets, paid, dark=False)
        except (ConfigNotFoundError, AuthorizationError, StoreNotFoundError):
            self.exp_kpis = {}
            self.budget_rows = []
            self.budget_fig_dark = go.Figure()
            self.budget_fig_light = go.Figure()

    def _expense_row(self, rec, cat_names, known_ids) -> dict[str, str]:
        e = rec.input
        validation = kpi.expense_validation(e, known_ids)
        return {
            "id": str(rec.id),
            "incurred": e.incurred_date.strftime("%d %b %Y")
                if e.incurred_date else "—",
            "incurred_iso": e.incurred_date.isoformat()
                if e.incurred_date else "",
            "paid": e.paid_date.strftime("%d %b %Y") if e.paid_date else "—",
            "paid_iso": e.paid_date.isoformat() if e.paid_date else "",
            "category": cat_names.get(e.category_id, "Unknown")
                if e.category_id is not None else "—",
            "category_id": str(e.category_id or ""),
            "description": e.description or "",
            "amount": ds.fmt_rm(e.amount_cents),
            "amount_cents": (f"{abs(e.amount_cents):012d}"
                             if e.amount_cents is not None else ""),
            "paid_by": e.paid_by or "—",
            "status": e.status or "",
            "status_color": STATUS_COLOR[e.status or ""],
            "receipt": rec.receipt_ref or "",
            "verification": e.verification or "",
            "ver_color": VERIFICATION_COLOR[e.verification or ""],
            "recorded_by": e.recorded_by or "—",
            "remarks": rec.remarks or "",
            "validation": validation,
            "vcolor": VALIDATION_COLOR[validation],
        }

    # ------------------------------------------------------------------
    # form open/close
    # ------------------------------------------------------------------
    @rx.event
    def open_expense_form(self, expense_id: str = "") -> None:
        self.exp_form_error = ""
        self.exp_notice = ""
        self.editing_id = expense_id
        values = {a: "" for a in self._exp_form_values()}
        values["e_status"] = "Pending"
        values["e_verification"] = "Unverified"
        if expense_id:
            try:
                user = self._actor()
                records = app_service.get_expense_transactions(
                    user, self._selected_store_id())
                rec = next((r for r in records
                            if str(r.id) == expense_id), None)
            except Exception:
                rec = None
            if rec is None:
                self.exp_form_error = "Transaction not found in this store."
                return
            e = rec.input
            values.update({
                "e_incurred": e.incurred_date.isoformat()
                    if e.incurred_date else "",
                "e_paid": e.paid_date.isoformat() if e.paid_date else "",
                "e_category": self._cat_name(e.category_id),
                "e_description": e.description or "",
                "e_amount": (f"{e.amount_cents / 100:.2f}"
                             if e.amount_cents is not None else ""),
                "e_paid_by": e.paid_by or "",
                "e_status": e.status or "Pending",
                "e_receipt": rec.receipt_ref or "",
                "e_verification": e.verification or "Unverified",
                "e_recorded_by": e.recorded_by or "",
                "e_remarks": rec.remarks or "",
            })
        else:
            try:
                user = self._actor()
                values["e_recorded_by"] = user.display_name
            except Exception:
                pass
            month = self._selected_month()
            if month:
                values["e_incurred"] = f"{month}-01"
        for a, v in values.items():
            setattr(self, a, v)
        self.exp_original = dict(values)
        self.exp_form_open = True

    def _cat_name(self, cid: Optional[int]) -> str:
        if cid is None:
            return ""
        try:
            user = self._actor()
            for c in app_service.get_expense_categories(
                    user, self._selected_store_id()):
                if c.id == cid:
                    return c.name
        except Exception:
            pass
        return ""

    @rx.event
    def close_expense_form(self) -> None:
        self.exp_form_open = False
        self.exp_form_error = ""

    # --- explicit setters (Reflex 0.10: no auto set_ handlers) ----------
    @rx.event
    def set_filter_period(self, v: str): self.filter_period = v
    @rx.event
    def set_filter_category(self, v: str):
        self.filter_category = "" if v == "All categories" else v
    @rx.event
    def set_filter_category_name(self, name: str):
        self.filter_category = "" if name == "All categories" else \
            str(self._category_id(name) or "")
    @rx.event
    def set_filter_status(self, v: str):
        self.filter_status = "" if v == "All statuses" else v
    @rx.event
    def set_filter_verification(self, v: str):
        self.filter_verification = "" if v == "All verifications" else v
    @rx.event
    def set_search_text(self, v: str): self.search_text = v
    @rx.event
    def set_sort_key(self, v: str): self.sort_key = v
    @rx.event
    def toggle_sort_dir(self):
        self.sort_desc = not self.sort_desc

    @rx.event
    def set_e_incurred(self, v: str): self.e_incurred = v
    @rx.event
    def set_e_paid(self, v: str): self.e_paid = v
    @rx.event
    def set_e_category(self, v: str): self.e_category = v
    @rx.event
    def set_e_description(self, v: str): self.e_description = v
    @rx.event
    def set_e_amount(self, v: str): self.e_amount = v
    @rx.event
    def set_e_paid_by(self, v: str): self.e_paid_by = v
    @rx.event
    def set_e_status(self, v: str): self.e_status = v
    @rx.event
    def set_e_receipt(self, v: str): self.e_receipt = v
    @rx.event
    def set_e_verification(self, v: str): self.e_verification = v
    @rx.event
    def set_e_recorded_by(self, v: str): self.e_recorded_by = v
    @rx.event
    def set_e_remarks(self, v: str): self.e_remarks = v

    @rx.event
    def set_b_category(self, v: str): self.b_category = v
    @rx.event
    def set_b_amount(self, v: str): self.b_amount = v

    # ------------------------------------------------------------------
    # save expense
    # ------------------------------------------------------------------
    @rx.event
    def save_expense(self) -> None:
        self.exp_form_error = ""
        self.exp_notice = ""
        self.exp_form_open = False  # close immediately; reopen on failure
        self.search_text = ""
        self.filter_status = ""
        self.filter_verification = ""
        self.filter_category = ""
        store_id = self._selected_store_id()
        if store_id is None:
            self.exp_form_error = "Select a store first."
            self.exp_form_open = True
            return
        # parse (blank -> None; incomplete drafts are allowed by design)
        try:
            fields = self._parse_expense_form()
        except ValueError as exc:
            self.exp_form_error = str(exc)
            self.exp_form_open = True
            return
        try:
            user = self._actor()
        except AuthenticationError:
            self.exp_form_error = "Your session has expired. Please sign in again."
            self.exp_form_open = True
            return
        self.exp_saving = True
        yield
        try:
            if self.editing_id:
                # partial update: only changed fields are written
                changed = self._changed_fields(fields)
                if not changed:
                    self.exp_saving = False
                    self.exp_notice = "No changes to save."
                    yield ExpensesState.load_expenses_data()
                    return
                app_service.update_expense(
                    user, store_id, int(self.editing_id), **changed)
            else:
                app_service.record_expense(user, store_id, **fields)
        except AuthorizationError:
            self.exp_saving = False
            self.exp_form_error = "Your role cannot modify expenses."
            self.exp_form_open = True
            return
        except (ValidationError, RecordNotFoundError) as exc:
            self.exp_saving = False
            self.exp_form_error = str(exc)
            self.exp_form_open = True
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.exp_saving = False
            self.exp_form_error = f"Save failed: {exc}"
            self.exp_form_open = True
            return
        self.exp_saving = False
        verb = "updated" if self.editing_id else "recorded"
        self.exp_notice = (f"Expense {verb} and persisted. KPIs and budgets "
                           f"recalculated live.")
        yield ExpensesState.load_expenses_data()
        yield ExpensesState.refresh_report()

    def _parse_expense_form(self) -> dict:
        def parse_date(raw: str, label: str) -> Optional[date]:
            raw = raw.strip()
            if not raw:
                return None
            try:
                return date.fromisoformat(raw)
            except ValueError:
                raise ValueError(f"{label} must be a valid date")

        amount = None
        if self.e_amount.strip():
            amount = _parse_money(self.e_amount)
        category_id = None
        if self.e_category.strip():
            category_id = self._category_id(self.e_category.strip())
            if category_id is None:
                raise ValueError(
                    f"Unknown expense category: {self.e_category!r}")
        status = self.e_status or None
        if status is not None and status not in PAYMENT_STATUSES:
            raise ValueError(f"Invalid payment status: {status}")
        verification = self.e_verification or None
        if verification is not None and verification not in VERIFICATIONS:
            raise ValueError(f"Invalid verification: {verification}")
        paid_by = self.e_paid_by or None
        if paid_by is not None and paid_by not in PAYMENT_SOURCES:
            raise ValueError(f"Invalid payment source: {paid_by}")
        return {
            "incurred_date": parse_date(self.e_incurred, "Incurred date"),
            "paid_date": parse_date(self.e_paid, "Paid date"),
            "category_id": category_id,
            "description": self.e_description.strip() or None,
            "amount_cents": amount,
            "paid_by": paid_by,
            "status": status,
            "receipt_ref": self.e_receipt.strip() or None,
            "verification": verification,
            "recorded_by": self.e_recorded_by.strip() or None,
            "remarks": self.e_remarks.strip() or None,
        }

    def _changed_fields(self, fields: dict) -> dict:
        """Diff parsed values against the originally loaded record."""
        form = self._exp_form_values()
        changed: dict = {}
        key_map = {
            "e_incurred": "incurred_date", "e_paid": "paid_date",
            "e_category": "category_id", "e_description": "description",
            "e_amount": "amount_cents", "e_paid_by": "paid_by",
            "e_status": "status", "e_receipt": "receipt_ref",
            "e_verification": "verification",
            "e_recorded_by": "recorded_by", "e_remarks": "remarks",
        }
        for attr, col in key_map.items():
            if form[attr] != self.exp_original.get(attr, ""):
                changed[col] = fields[col]
        return changed

    # ------------------------------------------------------------------
    # budget save
    # ------------------------------------------------------------------
    @rx.event
    def save_budget(self) -> None:
        self.b_error = ""
        self.b_notice = ""
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            self.b_error = "Select a store and month first."
            return
        if not self.b_category:
            self.b_error = "Choose a category."
            return
        category_id = self._category_id(self.b_category)
        if category_id is None:
            self.b_error = f"Unknown category: {self.b_category!r}"
            return
        if not self.b_amount.strip():
            self.b_error = ("Enter a budget amount. A missing budget is "
                            "distinct from RM 0 — leave unset instead.")
            return
        try:
            cents = _parse_money(self.b_amount)
        except ValueError as exc:
            self.b_error = str(exc)
            return
        try:
            user = self._actor()
        except AuthenticationError:
            self.b_error = "Your session has expired. Please sign in again."
            return
        self.b_saving = True
        yield
        try:
            app_service.set_expense_budget(user, store_id, month,
                                           category_id, cents)
        except AuthorizationError:
            self.b_saving = False
            self.b_error = ("Only super admins and administrators can "
                            "manage budgets.")
            return
        except (ValidationError, RecordNotFoundError) as exc:
            self.b_saving = False
            self.b_error = str(exc)
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.b_saving = False
            self.b_error = f"Save failed: {exc}"
            return
        self.b_saving = False
        self.b_notice = (f"Budget for {self.b_category} saved "
                         f"({ds.fmt_rm(cents)} for {month}).")
        yield ExpensesState.load_expenses_data()
        yield ExpensesState.refresh_report()
