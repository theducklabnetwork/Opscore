"""Overview dashboard (/ops): four-pillar strip, command-center cards,
needs-attention panel, and the shared sales pace chart.

Every value and status comes from assemble_month / kpi.py via
AppService.preview_month — nothing is recomputed here. Payroll-derived
figures follow the Manpower module's rule: only roles with
authz.can_manage_config see salary data; others get "Restricted" and the
figures are never placed in state. "Today" is Asia/Kuala_Lumpur and
injectable for tests.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import plotly.graph_objects as go
import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_charts
from ducklab.auth_service import AuthenticationError, AuthorizationError
from ducklab.auth_pages import app_service
from ducklab.operations_repository import (
    ConfigNotFoundError, InvalidMonthError, RepositoryError,
    StoreNotFoundError,
)
from ducklab.ops_actions_state import ActionsState
from ducklab.ops_state import OpsState

RATIO_STATUS_COLOR = {"Within target": "green", "Above target": "red",
                      "Awaiting payroll": "gray", "Awaiting data": "gray",
                      "No target": "gray"}
STATUS_COLOR = {"Achieved": "green", "Below target": "red",
                "Awaiting data": "gray", "No target": "gray"}
STOCK_COLOR = {"Within target": "green", "Over target": "red",
               "No target": "gray"}
TURNOVER_COLOR = {"Normal": "green", "HIGH TURNOVER": "red",
                  "Awaiting data": "gray"}


def _today() -> date:
    return datetime.now(ZoneInfo("Asia/Kuala_Lumpur")).date()


class OverviewState(ActionsState):
    """Overview dashboard state (inherits selectors + guards)."""

    loaded: bool = False
    error: str = ""
    finalized_notice: str = ""

    # four pillars (each: value / reference / status / status_color / href)
    pill_sales: dict[str, str] = {}
    pill_expenses: dict[str, str] = {}
    pill_manpower: dict[str, str] = {}
    pill_stock: dict[str, str] = {}

    # command-center cards
    cc: dict[str, str] = {}

    # needs attention: [{icon, text, href}]
    attention: list[dict[str, str]] = []

    # pace chart (same data as /ops/sales)
    pace_fig_dark: go.Figure = go.Figure()
    pace_fig_light: go.Figure = go.Figure()
    pace_has_data: bool = False

    def _clear_overview(self) -> None:
        self.loaded = False
        self.error = ""
        self.finalized_notice = ""
        self.pill_sales = {}
        self.pill_expenses = {}
        self.pill_manpower = {}
        self.pill_stock = {}
        self.cc = {}
        self.attention = []
        self.pace_fig_dark = go.Figure()
        self.pace_fig_light = go.Figure()
        self.pace_has_data = False

    # --- selector overrides -------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._clear_overview()
        yield OverviewState.load_overview_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_overview()
        yield OverviewState.load_overview_data()

    # --- loading ------------------------------------------------------------
    @rx.event
    def load_overview(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield OpsState.load_ops()
        yield OverviewState.load_overview_data()

    @rx.event
    def load_overview_data(self) -> None:
        self._clear_overview()
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            return  # has_stores=False handled by the page's empty state
        try:
            user = self._actor()
        except AuthenticationError:
            self.error = "Your session has expired. Please sign in again."
            return
        try:
            data = compute_overview(user, store_id, month, _today())
        except ConfigNotFoundError:
            self.error = ("No monthly configuration exists for this store "
                          "and month. An administrator must configure the "
                          "month before reporting.")
            return
        except AuthorizationError:
            self.error = "You do not have access to this store."
            return
        except (StoreNotFoundError, InvalidMonthError, RepositoryError) as exc:
            self.error = str(exc)
            return
        for k, v in data.items():
            setattr(self, k, v)
        self.loaded = True


def compute_overview(user, store_id: int, month: str, today: date) -> dict:
    """Pure overview computation (testable without a Reflex runtime).

    Returns a dict of OverviewState fields. Every value/status comes from
    assemble_month via AppService.preview_month — nothing recomputed.
    Raises ConfigNotFoundError/AuthorizationError/... for the caller.
    """
    rep = app_service.preview_month(user, store_id, month, as_of=today)
    finalized_notice = ""
    try:
        versions = app_service.list_report_versions(user, store_id, month)
        if versions:
            finalized_notice = (
                "A finalized snapshot exists for this month — this "
                "overview always shows live figures.")
    except AuthorizationError:
        pass

    s, e, m, st = rep.sales, rep.expenses, rep.manpower, rep.stock
    cfg = rep.config
    dim = s.days_in_month
    entered = s.days_entered
    can_payroll = authz.can_manage_config(user)

    # --- four pillars ---------------------------------------------------
    l1_status = s.status_by_level.get("level1", "Awaiting data")
    out = {}
    out["pill_sales"] = {
        "value": ds.fmt_rm0(int(s.month_end_sales_cents)
                            if s.month_end_sales_cents is not None
                            else None),
        "reference": "Level 1 " + ds.fmt_rm0(s.targets.get("level1")),
        "status": l1_status,
        "status_color": STATUS_COLOR.get(l1_status, "gray"),
    }
    exp_status = e.total_expenses_status
    exp_color = {"Within budget": "green", "Over budget": "red",
                 "No budget": "gray", "No payments yet": "gray"}[exp_status]
    out["pill_expenses"] = {
        "value": ds.fmt_pct(e.expense_share_of_sales),
        "reference": "Paid / sales, budget "
                     + ds.fmt_rm0(e.total_budget_cents),
        "status": exp_status,
        "status_color": exp_color,
    }
    if can_payroll:
        out["pill_manpower"] = {
            "value": ds.fmt_pct(m.salary_ratio_incl_support),
            "reference": "Target " + ds.fmt_pct(cfg.salary_ratio_target)
                         + " · turnover "
                         + ds.fmt_pct(m.turnover_pct),
            "status": m.salary_ratio_status
                      + " · " + m.turnover_status,
            "status_color": RATIO_STATUS_COLOR.get(
                m.salary_ratio_status, "gray"),
            "turnover_color": TURNOVER_COLOR.get(m.turnover_status,
                                                 "gray"),
        }
    else:
        # Restricted: never place payroll figures in state. Turnover is
        # headcount-based and follows the Manpower module (always shown).
        out["pill_manpower"] = {
            "value": "Restricted",
            "reference": "Turnover " + ds.fmt_pct(m.turnover_pct),
            "status": "Restricted · " + m.turnover_status,
            "status_color": "gray",
            "turnover_color": TURNOVER_COLOR.get(m.turnover_status,
                                                 "gray"),
        }
    out["pill_stock"] = {
        "value": ds.fmt_pct(st.loss_pct_of_sales),
        "reference": "Target " + ds.fmt_pct(cfg.stock_loss_target),
        "status": st.status,
        "status_color": STOCK_COLOR.get(st.status, "gray"),
    }

    # --- command-center cards -------------------------------------------
    complete = entered == dim
    y, mnum = (int(x) for x in month.split("-"))
    month_end = date(y, mnum, dim)
    in_progress = today <= month_end
    if entered == 0:
        completion = "Awaiting entry"
    elif complete:
        completion = "Month complete"
    else:
        completion = "In progress"
    l1 = s.targets.get("level1")
    proj_ach = None
    if s.month_end_sales_cents is not None and l1:
        proj_ach = kpi.achievement(int(s.month_end_sales_cents), l1)
    needs_review = sum(v for k, v in e.validation_counts.items()
                       if k != "OK")
    # action counts for this store/month scope (raised in month or open)
    actions = app_service.get_actions(user, store_id)
    visible = [a for a in actions
               if (a.raised_date
                   and a.raised_date.strftime("%Y-%m") == month)
               or (a.input.status or "Open") in
               ("Open", "In progress", "Blocked")]
    acounts = kpi.action_counts([a.input for a in visible], as_of=today)
    out["cc"] = {
        "sales_mtd": ds.fmt_rm(s.sales_mtd_cents),
        "budget_l1": ds.fmt_rm0(l1),
        "achievement": ds.fmt_pct(s.mtd_achievement_l1),
        "days": f"{entered} / {dim}",
        "run_rate": ds.fmt_rm0(int(s.required_daily_run_rate)
                               if s.required_daily_run_rate is not None
                               else None),
        "needed": ds.fmt_rm0(int(s.needed_per_remaining_day)
                             if s.needed_per_remaining_day is not None
                             else None),
        "avg_day": ds.fmt_rm0(int(s.sales_mtd_cents / entered)
                              if entered else None),
        "paid": ds.fmt_rm(e.paid_cents),
        "incurred": ds.fmt_rm(e.incurred_cents),
        "open_exp": ds.fmt_rm(e.open_cents),
        "review": str(needs_review),
        "stock_loss": ds.fmt_rm(st.total_loss_cents),
        "stock_pct": ds.fmt_pct(st.loss_pct_of_sales),
        "completion": completion,
        "proj_achievement": ds.fmt_pct(proj_ach),
        "actions_open": str(kpi.open_actions(acounts)),
        "actions_overdue": str(acounts["overdue"]),
    }

    # --- needs attention (existing flags only) --------------------------
    items: list[dict[str, str]] = []
    if complete and l1_status == "Below target":
        items.append({"icon": "trending-down",
                      "text": "Sales below Level 1 for a complete month",
                      "href": "/ops/sales"})
    over_budget = [c.name for c in e.categories
                   if c.budget_status == "OVER BUDGET"]
    for name in over_budget:
        items.append({"icon": "receipt",
                      "text": f"Expense category OVER BUDGET: {name}",
                      "href": "/ops/expenses"})
    if needs_review:
        items.append({"icon": "flag",
                      "text": f"{needs_review} expense line(s) need "
                              "review",
                      "href": "/ops/expenses"})
    if acounts["overdue"]:
        items.append({"icon": "list-checks",
                      "text": f"{acounts['overdue']} overdue action(s)",
                      "href": "/ops/actions"})
    if m.turnover_status == "HIGH TURNOVER":
        items.append({"icon": "users",
                      "text": "HIGH TURNOVER this month",
                      "href": "/ops/manpower"})
    if st.status == "Over target":
        items.append({"icon": "boxes",
                      "text": "Stock loss over target",
                      "href": "/ops/stock"})
    if not complete and in_progress:
        items.append({"icon": "calendar-clock",
                      "text": f"{dim - entered} unreported day(s) — "
                              "month still in progress",
                      "href": "/ops/daily"})
    out["attention"] = items

    # --- pace chart (same computation as /ops/sales) --------------------
    days = app_service.get_daily_records(user, store_id, month)
    budget = kpi.required_daily_run_rate(l1, dim)
    entered_map = {d.date.day: d.sales_cents for d in days
                   if d.sales_cents is not None}
    last_reported = max(entered_map, default=0)
    cum, cumulative = 0, {}
    for day in sorted(entered_map):
        cum += entered_map[day]
        cumulative[day] = cum
    p_days, p_actual, p_budget, running = [], [], [], 0
    for day in range(1, dim + 1):
        if day in cumulative:
            running = cumulative[day]
        p_days.append(day)
        p_budget.append(int(budget * day) if budget is not None else None)
        p_actual.append(running if day <= last_reported and last_reported
                        else None)
    out["pace_has_data"] = last_reported > 0
    out["pace_fig_dark"] = ops_charts.pace_chart(p_days, p_actual, p_budget, dark=True)
    out["pace_fig_light"] = ops_charts.pace_chart(p_days, p_actual, p_budget, dark=False)
    
    out["finalized_notice"] = finalized_notice
    return out
