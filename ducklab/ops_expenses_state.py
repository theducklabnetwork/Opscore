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

from decimal import Decimal

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_charts
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError, User,
)
from ducklab.auth_pages import app_service
from ducklab.input_validation import (
    parse_client_id, parse_decimal, parse_money_cents,
)
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import (
    PAYMENT_SOURCES, PAYMENT_STATUSES, VERIFICATIONS, ValidationError,
)
from ducklab.operations_write_repository import RecordNotFoundError
from ducklab.ops_shared_state import (
    SESSION_EXPIRED, SharedOpsState, auth_message,
)

PERIOD_OPTIONS = [
    ("incurred", "Incurred This Month"),
    ("paid", "Paid This Month"),
    ("outstanding", "Pending"),
    ("all", "All"),
]
# register sort select: Title Case label -> sort key
SORT_LABELS = {"incurred": "Incurred", "paid": "Paid", "amount": "Amount"}

MAX_ROWS = 100

# All status colors: ds.status_color (design.py shared map)

# cleared with × while a percentage is typed (also used by Monthly Setup,
# ops_setup_state._parse_budget_rows)
BUDGET_CLEAR_CONFLICT = "Enter a percentage, or use × to clear"


def _pct_str(fraction) -> str:
    """0.025 -> '2.5'; trims trailing zeros; None -> ''."""
    if fraction is None:
        return ""
    s = f"{float(fraction) * 100:g}"
    return s


class ExpensesState(SharedOpsState):
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
    # Expense Mix donut: paid by category (hidden when nothing is paid)
    mix_ready: bool = False
    mix_fig_dark: go.Figure = go.Figure()
    mix_fig_light: go.Figure = go.Figure()
    validation_summary: str = ""
    # active sales-target basis for % budgets (progressive ladder)
    budget_basis_label: str = ""
    budget_basis_cents: str = ""     # formatted RM or "" when unavailable
    budget_pcts: dict[str, str] = {}  # category name -> saved pct (e.g. "2.5")

    # register
    all_expense_rows: list[dict[str, str]] = []
    total_rows: str = "0"
    filter_period: str = "incurred"
    filter_category: str = ""       # "" = all; otherwise str(category_id)
    filter_status: str = ""
    filter_verification: str = ""
    search_text: str = ""
    filter_paid_date: str = ""      # ISO; set from Daily (?paid=), "" = off
    sort_key: str = "incurred"      # incurred | paid | amount
    sort_desc: bool = True
    # "Needs Review: N": the register filtered to this month's lines whose
    # data check is not OK (same scope as kpi.expense_review_counts)
    filter_review: bool = False
    needs_review_count: int = 0

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
    b_amount: str = ""            # budget percentage text (e.g. "2.5")
    b_clear: bool = False     # cleared with ×: budget -> NULL
    b_error: str = ""
    b_notice: str = ""
    b_saving: bool = False

    # reference lists for selects
    category_names: list[str] = []

    @rx.var
    def filter_category_options(self) -> list[str]:
        return ["All Categories", *self.category_names]

    @rx.var
    def sort_label(self) -> str:
        return SORT_LABELS.get(self.sort_key, "Incurred")

    @rx.var
    def b_has_saved(self) -> bool:
        """The selected category has a saved budget (the "×" shows)."""
        return bool(self.budget_pcts.get(self.b_category, ""))

    @rx.var
    def period_options(self) -> list[str]:
        return [v for _, v in PERIOD_OPTIONS]

    @rx.var
    def filter_period_label(self) -> str:
        return dict(PERIOD_OPTIONS)[self.filter_period]

    @rx.var
    def filter_paid_label(self) -> str:
        if not self.filter_paid_date:
            return ""
        return "Paid " + date.fromisoformat(
            self.filter_paid_date).strftime("%d %b %Y")

    @rx.event
    def clear_filter_paid_date(self):
        self.filter_paid_date = ""

    @rx.event
    def set_filter_period_label(self, label: str):
        self.filter_paid_date = ""
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
        """Category id by name in the selected store. Authentication /
        authorization errors propagate to the calling handler."""
        user = self._actor()
        cats = app_service.get_expense_categories(
            user, self._selected_store_id())
        for c in cats:
            if c.name == name:
                return c.id
        return None

    @rx.var
    def expense_rows(self) -> list[dict[str, str]]:
        """Filtered/sorted register rows (deterministic ordering)."""
        rows = list(self.all_expense_rows)
        month = self._selected_month()
        if self.filter_review and month:
            end = f"{month}-31"   # ISO compare: on or before month end
            rows = [r for r in rows if r["validation"] != "OK" and (
                r["incurred_iso"][:7] == month or r["paid_iso"][:7] == month
                or (r["status"] == "Pending" and r["incurred_iso"]
                    and r["incurred_iso"] <= end))]
        elif self.filter_paid_date:
            # one paid date (from Daily): replaces the period filter
            rows = [r for r in rows if r["paid_iso"] == self.filter_paid_date]
        elif self.filter_period == "paid" and month:
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
        self.filter_paid_date = ""
        self.exp_form_open = False
        self.exp_notice = ""
        self._clear_shared_status()
        yield ExpensesState.resync_selection()
        yield ExpensesState.load_expenses_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self.filter_paid_date = ""
        self.exp_form_open = False
        self.exp_notice = ""
        self._clear_shared_status()
        yield ExpensesState.resync_selection()
        yield ExpensesState.load_expenses_data()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @rx.event
    def load_expenses(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        self.filter_paid_date = _paid_param(
            self.router.page.params.get("paid", ""))
        if self.filter_paid_date:
            self.filter_period = "all"     # the select matches the chip
        yield ExpensesState.load_selectors()
        yield ExpensesState.load_expenses_data()

    @rx.event
    def load_expenses_data(self) -> None:
        self.all_expense_rows = []
        self.exp_kpis = {}
        self.budget_rows = []
        self.budget_fig_dark = go.Figure()
        self.budget_fig_light = go.Figure()
        self._clear_mix()
        self.validation_summary = ""
        self.needs_review_count = 0
        self.filter_review = False   # a fresh load shows the period view
        self.exp_month_finalized = False
        self.has_categories = True
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None or not self.has_stores:
            return
        self.budget_basis_label = ""
        self.budget_basis_cents = ""
        self.budget_pcts = {}
        try:
            user = self._actor()
            finalized = bool(
                app_service.list_report_versions(user, store_id, month))
            cats = app_service.get_expense_categories(user, store_id)
            records = app_service.get_expense_transactions(user, store_id)
            pcts = app_service.get_expense_budgets(user, store_id, month)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.can_write_expenses = self.can_manage_budgets = False
            self.error_message = auth_message(exc)
            self.report_status = "error"
            return
        # Deactivated stores are read-only: hide every write control
        # (AppService rejects the writes server-side regardless).
        self.can_write_expenses = (authz.can_write_operations(user)
                                   and self.selected_store_active)
        self.can_manage_budgets = (authz.can_manage_config(user)
                                   and self.selected_store_active)
        self.exp_month_finalized = finalized
        self.has_categories = bool(cats)
        self.category_names = [c.name for c in cats]
        self.budget_pcts = {
            c.name: ("" if pcts.get(c.id) is None
                     else _pct_str(pcts[c.id]))
            for c in cats
        }
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
            self.budget_basis_label = e.budget_basis_label
            self.budget_basis_cents = (
                "" if e.budget_basis_cents is None
                else ds.fmt_rm(e.budget_basis_cents))
            util = kpi.budget_utilization(e.paid_cents, e.total_budget_cents)
            variance = kpi.budget_variance_cents(e.total_budget_cents,
                                                 e.paid_cents)
            # Each card decides from its own data (engine counts):
            # Paid: no Paid lines in month -> "No payments yet".
            # Incurred: no valid lines incurred in month -> "Not reported
            #   yet" (even when nothing is paid, amounts still show).
            # Pending: any Pending line in scope -> the amount; none ->
            #   genuine zero "RM 0.00" (not missing data).
            self.exp_kpis = {
                "paid": ("No payments yet" if e.paid_count == 0
                         else ds.fmt_rm(e.paid_cents)),
                "incurred": ("Not reported yet" if e.incurred_count == 0
                             else ds.fmt_rm(e.incurred_cents)),
                "open": ds.fmt_rm(e.open_cents),
                # Total budget None -> "—" (NULL), never RM 0.00
                "budget": ("—" if e.total_budget_cents is None
                           else ds.fmt_rm(e.total_budget_cents)),
                "utilization": ds.fmt_pct(util),
                "variance": ds.fmt_rm(variance),
                # Expense Monitor total row H25, labelled to distinguish
                # it from the Overview's HQ status (Summary!E40).
                "status": e.budget_control_status,
                "status_label": "Budget control (vs category budgets)",
                "status_tip": ("Expense Monitor!H25: No budget / OVER "
                               "BUDGET / Watch (paid ≥ 80% of budget) / "
                               "Within budget"),
                "status_color": ds.status_color(e.budget_control_status),
            }
            # compact variants for narrow tiles (exact value in a tooltip);
            # text states ("No payments yet") compact to a dash
            for key, cents, has in (
                    ("paid", e.paid_cents, e.paid_count > 0),
                    ("incurred", e.incurred_cents, e.incurred_count > 0),
                    ("open", e.open_cents, True),
                    ("budget", e.total_budget_cents, True),
                    ("variance", variance, True)):
                self.exp_kpis[f"{key}_compact"] = ds.fmt_rm_compact(
                    cents if has else None, whole_below=True)
            non_ok = {k: v for k, v in e.validation_counts.items()
                      if k != "OK" and v}
            self.validation_summary = (
                "  •  ".join(f"{k}: {v}" for k, v in sorted(non_ok.items()))
                if non_ok else "All transactions pass validation.")
            self.needs_review_count = sum(non_ok.values())
            self.budget_rows = []
            for c in e.categories:
                paid = c.paid_mtd_cents
                util = kpi.budget_utilization(paid, c.budget_cents)
                remaining = kpi.budget_variance_cents(c.budget_cents, paid)
                self.budget_rows.append({
                    "category_id": str(c.category_id),
                    "name": c.name,
                    "pct": self.budget_pcts.get(c.name, ""),
                    "budget": ds.fmt_rm(c.budget_cents),
                    "paid": ds.fmt_rm(paid),
                    "remaining": ds.fmt_rm(remaining),
                    "util": ds.fmt_pct(util),
                    "bar": (f"{min(float(util) * 100, 100):.0f}"
                            if util is not None else "0"),
                    "status": c.budget_status,
                    "color": ds.status_color(c.budget_status),
                })
            # Plotly budget-vs-actual figures (same data as the table)
            names = [c.name for c in e.categories]
            budgets = [c.budget_cents for c in e.categories]
            paid = [c.paid_mtd_cents for c in e.categories]
            self.budget_fig_dark = ops_charts.budget_vs_actual(
                names, budgets, paid, dark=True)
            self.budget_fig_light = ops_charts.budget_vs_actual(
                names, budgets, paid, dark=False)
            mix = kpi.expense_mix(list(zip(names, paid)))
            self.mix_ready = e.paid_count > 0 and bool(mix)
            if self.mix_ready:
                self.mix_fig_dark = ops_charts.expense_donut(mix, dark=True)
                self.mix_fig_light = ops_charts.expense_donut(mix,
                                                              dark=False)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.error_message = auth_message(exc)
            self.report_status = "error"
            self.exp_kpis = {}
            self.budget_rows = []
            self.budget_fig_dark = go.Figure()
            self.budget_fig_light = go.Figure()
        except ConfigNotFoundError:
            self.exp_kpis = {}
            self.budget_rows = []
            self.budget_fig_dark = go.Figure()
            self.budget_fig_light = go.Figure()

    def _clear_mix(self) -> None:
        self.mix_ready = False
        self.mix_fig_dark = go.Figure()
        self.mix_fig_light = go.Figure()

    def _expense_row(self, rec, cat_names, known_ids) -> dict[str, str]:
        e = rec.input
        validation = kpi.expense_validation(e, known_ids)
        return {
            "id": str(rec.id),
            "incurred": e.incurred_date.strftime("%d %b %Y")
                if e.incurred_date else "—",
            "incurred_iso": e.incurred_date.isoformat()
                if e.incurred_date else "",
            # phones: day + month only (the year is the selected month's)
            "incurred_short": e.incurred_date.strftime("%d %b")
                if e.incurred_date else "—",
            "paid": e.paid_date.strftime("%d %b %Y") if e.paid_date else "—",
            "paid_iso": e.paid_date.isoformat() if e.paid_date else "",
            "category": cat_names.get(e.category_id, "Unknown")
                if e.category_id is not None else "—",
            "category_id": str(e.category_id or ""),
            "description": e.description or "",
            "amount": ds.fmt_rm(e.amount_cents),
            "amount_compact": ds.fmt_rm_compact(e.amount_cents,
                                                whole_below=True),
            "amount_cents": (f"{abs(e.amount_cents):012d}"
                             if e.amount_cents is not None else ""),
            "paid_by": e.paid_by or "—",
            "status": e.status or "",
            "status_color": ds.status_color(e.status or ""),
            "receipt": rec.receipt_ref or "",
            "verification": e.verification or "",
            "ver_color": ds.status_color(e.verification or ""),
            "recorded_by": e.recorded_by or "—",
            "remarks": rec.remarks or "",
            "validation": validation,
            "vcolor": ds.status_color(validation),
        }

    # ------------------------------------------------------------------
    # form open/close
    # ------------------------------------------------------------------
    @rx.event
    def open_expense_form(self, expense_id: str = "") -> None:
        # Validate the client-sent id and load the record BEFORE touching
        # any editing state; failures leave the dialog closed.
        try:
            wanted = parse_client_id(expense_id, "transaction")
            user = self._actor()
            store_id = self._selected_store_id()
            rec, cat_names = None, {}
            if wanted is not None:
                rec = next((r for r in app_service.get_expense_transactions(
                    user, store_id) if r.id == wanted), None)
                if rec is None:
                    raise ValidationError(
                        "Unknown transaction — reopen it from the list.")
                cat_names = {c.id: c.name for c in
                             app_service.get_expense_categories(
                                 user, store_id)}
        except ValidationError as exc:
            self.exp_form_error = str(exc)
            return
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.exp_form_error = auth_message(exc)
            return
        self.exp_form_error = ""
        self.exp_notice = ""
        self.editing_id = str(wanted) if wanted is not None else ""
        values = {a: "" for a in self._exp_form_values()}
        values["e_status"] = "Pending"
        values["e_verification"] = "Unverified"
        if rec is not None:
            e = rec.input
            values.update({
                "e_incurred": e.incurred_date.isoformat()
                    if e.incurred_date else "",
                "e_paid": e.paid_date.isoformat() if e.paid_date else "",
                "e_category": cat_names.get(e.category_id, ""),
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
            values["e_recorded_by"] = user.display_name
            month = self._selected_month()
            if month:
                values["e_incurred"] = f"{month}-01"
        for a, v in values.items():
            setattr(self, a, v)
        self.exp_original = dict(values)
        self.exp_form_open = True

    @rx.event
    def close_expense_form(self) -> None:
        self.exp_form_open = False
        self.exp_form_error = ""

    # --- explicit setters (Reflex 0.10: no auto set_ handlers) ----------
    @rx.event
    def set_filter_period(self, v: str): self.filter_period = v
    @rx.event
    def set_filter_category(self, v: str):
        self.filter_category = "" if v.lower() == "all categories" else v
    @rx.event
    def set_filter_category_name(self, name: str):
        if name.lower() == "all categories":
            self.filter_category = ""
            return
        try:
            self.filter_category = str(self._category_id(name) or "")
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.filter_category = ""
            self.error_message = auth_message(exc)
            self.report_status = "error"
    @rx.event
    def set_filter_status(self, v: str):
        self.filter_status = "" if v.lower() == "all statuses" else v
    @rx.event
    def set_filter_verification(self, v: str):
        self.filter_verification = ("" if v.lower() == "all verifications"
                                    else v)
    @rx.event
    def set_search_text(self, v: str): self.search_text = v
    @rx.event
    def set_sort_key(self, v: str):
        # accepts the key ("amount") or the select label ("Amount")
        self.sort_key = v.lower() if v.lower() in SORT_LABELS else v

    @rx.event
    def show_needs_review(self):
        """"Needs Review: N" -> the register filtered to those lines."""
        self.filter_review = True
        return rx.scroll_to("expense-register")

    @rx.event
    def clear_needs_review(self):
        self.filter_review = False
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
    def set_b_category(self, v: str):
        self.b_category = v
        # prefill the saved percentage for the chosen category
        self.b_amount = self.budget_pcts.get(v, "")
        self.b_clear = False
    @rx.event
    def set_b_amount(self, v: str):
        self.b_amount = v
        if v.strip():
            self.b_clear = False   # typing an amount undoes the ×
    @rx.event
    def set_b_clear(self, v: bool): self.b_clear = bool(v)

    @rx.event
    def clear_budget(self):
        """× in Budget (%): the same as the old "Clear saved value"
        checkbox with the amount blank (save -> NULL, "No budget")."""
        self.b_amount = ""
        self.b_clear = True

    @rx.event
    def clear_budget_x(self):
        """Older name for clear_budget."""
        self.b_amount = ""
        self.b_clear = True

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
            editing = parse_client_id(self.editing_id, "transaction")
            fields = self._parse_expense_form()
            user = self._actor()
        except (ValueError, ValidationError) as exc:
            self.exp_form_error = str(exc)
            self.exp_form_open = True
            return
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.exp_form_error = auth_message(exc)
            self.exp_form_open = True
            return
        self.exp_saving = True
        yield
        try:
            if editing is not None:
                # partial update: only changed fields are written
                changed = self._changed_fields(fields)
                if not changed:
                    self.exp_notice = "No changes to save."
                    yield ExpensesState.load_expenses_data()
                    return
                app_service.update_expense(
                    user, store_id, editing, **changed)
            else:
                app_service.record_expense(user, store_id, **fields)
        except AuthenticationError:
            self.exp_form_error = SESSION_EXPIRED
            self.exp_form_open = True
            return
        except AuthorizationError:
            self.exp_form_error = "Your role cannot modify expenses."
            self.exp_form_open = True
            return
        except (ValidationError, RecordNotFoundError) as exc:
            self.exp_form_error = str(exc)
            self.exp_form_open = True
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.exp_form_error = f"Save failed: {exc}"
            self.exp_form_open = True
            return
        finally:
            self.exp_saving = False
        verb = "updated" if self.editing_id else "recorded"
        self.exp_notice = (f"Expense {verb} and persisted. KPIs and budgets "
                           f"recalculated live.")
        yield ExpensesState.load_expenses_data()
        yield ExpensesState.resync_selection()

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
            amount = parse_money_cents(self.e_amount)
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
        try:
            user = self._actor()
            category_id = self._category_id(self.b_category)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.b_error = auth_message(exc)
            return
        if category_id is None:
            self.b_error = f"Unknown category: {self.b_category!r}"
            return
        try:
            pct = _budget_save_value(self.b_amount, self.b_clear)
        except ValueError as exc:
            self.b_error = str(exc)
            return
        self.b_saving = True
        yield
        try:
            app_service.set_expense_budget(user, store_id, month,
                                           category_id, pct)
        except AuthenticationError:
            self.b_error = SESSION_EXPIRED
            return
        except AuthorizationError:
            self.b_error = ("Only super admins and administrators can "
                            "manage budgets.")
            return
        except (ValidationError, RecordNotFoundError) as exc:
            self.b_error = str(exc)
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.b_error = f"Save failed: {exc}"
            return
        finally:
            self.b_saving = False
        self.b_clear = False
        self.b_notice = (f"Budget for {self.b_category} cleared "
                         f"(No budget for {month})." if pct is None else
                         f"Budget for {self.b_category} saved "
                         f"({_pct_str(pct)}% for {month}).")
        yield ExpensesState.load_expenses_data()
        yield ExpensesState.resync_selection()


def _paid_param(raw) -> str:
    """?paid=YYYY-MM-DD from Daily Operations; anything else is ignored."""
    try:
        return date.fromisoformat(str(raw)).isoformat()
    except ValueError:
        return ""


def _budget_save_value(pct_raw: str, clear: bool) -> Optional[float]:
    """Budget form -> fraction to save. Cleared with × -> None (NULL = "No
    budget"); cleared while a percentage is typed is rejected; otherwise a
    percentage is required. Returns a fraction (2.5 -> 0.025)."""
    if clear:
        if pct_raw.strip():
            raise ValueError(BUDGET_CLEAR_CONFLICT)
        return None
    if not pct_raw.strip():
        raise ValueError(
            "Enter a budget %, or use × to clear it. A missing budget "
            "is distinct from 0%.")
    pct = parse_decimal(pct_raw, "Budget %", Decimal(0), Decimal(100))
    return float(pct / 100)
