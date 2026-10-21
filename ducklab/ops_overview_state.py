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

    # four pillars (values pre-formatted; progress floats 0..1)
    pill_sales: dict = {}
    pill_expenses: dict = {}
    pill_manpower: dict = {}
    pill_stock: dict = {}

    # command-center cards
    cc: dict[str, str] = {}

    # needs attention: [{icon, text, href}]
    attention: list[dict[str, str]] = []

    # sales targets panel: [{label, target, pct, progress, status, ...}]
    targets: list[dict] = []

    # "At a glance" secondary figures: [{label, value, title, href}]
    glance: list[dict[str, str]] = []

    header_context: str = ""
    in_progress: str = ""

    # sparklines (sales + stock), one figure per theme
    spark_sales_dark: go.Figure = go.Figure()
    spark_sales_light: go.Figure = go.Figure()
    spark_stock_dark: go.Figure = go.Figure()
    spark_stock_light: go.Figure = go.Figure()

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
        self.targets = []
        self.glance = []
        self.header_context = ""
        self.in_progress = ""
        self.spark_sales_dark = go.Figure()
        self.spark_sales_light = go.Figure()
        self.spark_stock_dark = go.Figure()
        self.spark_stock_light = go.Figure()

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
    complete = entered == dim
    y, mnum = (int(x) for x in month.split("-"))
    month_end = date(y, mnum, dim)
    in_progress = today <= month_end
    l1 = s.targets.get("level1")
    out = {}

    def proj_ach_of(cents, target):
        return kpi.achievement(cents, target) if cents is not None else None

    needs_review = sum(v for k, v in e.validation_counts.items()
                       if k != "OK")
    if entered == 0:
        completion = "Awaiting entry"
    elif complete:
        completion = "Month complete"
    else:
        completion = "In progress"
    # Sales hero: MTD, progress vs Level 1 with projected marker; the
    # status chip is prefixed "Projected:" while the month is incomplete.
    proj_cents = (int(s.month_end_sales_cents)
                  if s.month_end_sales_cents is not None else None)
    sales_reported = entered > 0
    out["pill_sales"] = {
        "reported": "1" if sales_reported else "",
        "value": ops_charts.fmt_money_compact(s.sales_mtd_cents),
        "title": ds.fmt_rm(s.sales_mtd_cents),
        "progress": (min(float(s.sales_mtd_cents / l1), 1.0)
                     if l1 and sales_reported else 0.0),
        "marker": (min(float(proj_cents / l1), 1.0)
                   if l1 and proj_cents else 0.0),
        "progress_color": "green" if l1_status == "Achieved" else "indigo",
        "status": (l1_status if complete
                   else f"Projected: {l1_status}"),
        "status_color": STATUS_COLOR.get(l1_status, "gray"),
        "context": ("Projected "
                    + ops_charts.fmt_money_compact(proj_cents)
                    + " · " + ops_charts.fmt_pct1(proj_ach_of(proj_cents, l1))
                    + " of Level 1"),
    }
    exp_status = e.total_expenses_status
    exp_color = {"Within budget": "green", "Over budget": "red",
                 "No budget": "gray", "No payments yet": "gray"}[exp_status]
    paid_count = kpi.paid_count_in_month([x.input for x in
                                          app_service.get_expense_transactions(
                                              user, store_id)], month,
                                         store_id)
    exp_reported = paid_count > 0
    out["pill_expenses"] = {
        "reported": "1" if exp_reported else "",
        "value": ops_charts.fmt_money_compact(e.paid_cents),
        "title": ds.fmt_rm(e.paid_cents),
        "progress": (min(float(e.paid_cents / e.total_budget_cents), 1.0)
                     if e.total_budget_cents else 0.0),
        "marker": 0.0,
        "progress_color": ("red" if exp_status == "Over budget"
                           else "green"),
        "status": exp_status,
        "status_color": exp_color,
        "context": ("Budget "
                    + ops_charts.fmt_money_compact(e.total_budget_cents)
                    + " · " + ops_charts.fmt_pct1(e.expense_share_of_sales)
                    + " of sales"),
    }
    if can_payroll:
        ratio = m.salary_ratio_incl_support
        target = cfg.salary_ratio_target
        out["pill_manpower"] = {
            "reported": "1" if ratio is not None else "",
            "value": ops_charts.fmt_pct1(ratio),
            "title": ds.fmt_pct(ratio),
            "progress": (min(float(ratio / target), 1.0)
                         if ratio is not None and target else 0.0),
            "marker": 0.0,
            "progress_color": ("red" if m.salary_ratio_status
                               == "Above target" else "green"),
            "status": m.salary_ratio_status,
            "status_color": RATIO_STATUS_COLOR.get(m.salary_ratio_status,
                                                   "gray"),
            "context": ("Target " + ops_charts.fmt_pct1(target)
                        + " · turnover " + ops_charts.fmt_pct1(m.turnover_pct)
                        + " (" + m.turnover_status + ")"),
        }
    else:
        # Restricted: never place payroll figures in state. Turnover is
        # headcount-based and follows the Manpower module (always shown).
        out["pill_manpower"] = {
            "reported": "1",
            "value": "Restricted",
            "title": "Restricted",
            "progress": 0.0,
            "marker": 0.0,
            "progress_color": "indigo",
            "status": "Restricted",
            "status_color": "gray",
            "context": ("Turnover " + ops_charts.fmt_pct1(m.turnover_pct)
                        + " (" + m.turnover_status + ")"),
        }
    stock_reported = st.total_loss_cents != 0 or sales_reported
    out["pill_stock"] = {
        "reported": "1" if stock_reported else "",
        "value": ops_charts.fmt_pct1(st.loss_pct_of_sales),
        "title": ds.fmt_pct(st.loss_pct_of_sales),
        "progress": (min(float(st.loss_pct_of_sales / cfg.stock_loss_target),
                         1.0)
                     if st.loss_pct_of_sales is not None
                     and cfg.stock_loss_target else 0.0),
        "marker": 0.0,
        "progress_color": ("red" if st.status == "Over target"
                           else "green"),
        "status": st.status,
        "status_color": STOCK_COLOR.get(st.status, "gray"),
        "context": ("Target " + ops_charts.fmt_pct1(cfg.stock_loss_target)
                    + " · loss "
                    + ops_charts.fmt_money_compact(st.total_loss_cents)),
    }

    # Sales targets panel: Level 1-4 progress vs engine achievement/status
    out["targets"] = [
        {"label": f"Level {i}",
         "target": ops_charts.fmt_money_compact(t),
         "pct": ops_charts.fmt_pct1(
             kpi.achievement(proj_cents, t)),
         "progress": (min(float(kpi.achievement(proj_cents, t)), 1.0)
                      if kpi.achievement(proj_cents, t) is not None else 0.0),
         "status": s.status_by_level.get(name, "Awaiting data"),
         "status_color": STATUS_COLOR.get(
             s.status_by_level.get(name, ""), "gray"),
         "progress_color": ("green" if s.status_by_level.get(name)
                            == "Achieved" else "indigo")}
        for i, name in enumerate(("level1", "level2", "level3", "level4"), 1)
        for t in [s.targets.get(name)]
    ]

    # --- secondary figures ("At a glance") --------------------------------
    # action counts for this store/month scope (raised in month or open)
    actions = app_service.get_actions(user, store_id)
    visible = [a for a in actions
               if (a.raised_date
                   and a.raised_date.strftime("%Y-%m") == month)
               or (a.input.status or "Open") in
               ("Open", "In progress", "Blocked")]
    acounts = kpi.action_counts([a.input for a in visible], as_of=today)
    rr = int(s.required_daily_run_rate) \
        if s.required_daily_run_rate is not None else None
    nd = int(s.needed_per_remaining_day) \
        if s.needed_per_remaining_day is not None else None
    avg = int(s.sales_mtd_cents / entered) if entered else None
    glance_pairs = [
        ("Required daily run-rate",
         ops_charts.fmt_money_compact(rr), ds.fmt_rm0(rr), "/ops/sales"),
        ("Needed per remaining day",
         ops_charts.fmt_money_compact(nd), ds.fmt_rm0(nd), "/ops/sales"),
        ("Average per reported day",
         ops_charts.fmt_money_compact(avg), ds.fmt_rm0(avg), "/ops/sales"),
        ("Incurred expenses",
         ops_charts.fmt_money_compact(e.incurred_cents),
         ds.fmt_rm(e.incurred_cents), "/ops/expenses"),
        ("Open expenses",
         ops_charts.fmt_money_compact(e.open_cents),
         ds.fmt_rm(e.open_cents), "/ops/expenses"),
        ("Expense lines to review", str(needs_review), "", "/ops/expenses"),
        ("Open actions", str(kpi.open_actions(acounts)), "", "/ops/actions"),
        ("Overdue actions", str(acounts["overdue"]), "", "/ops/actions"),
    ]
    out["glance"] = [
        {"label": label, "value": value, "title": title, "href": href}
        for label, value, title, href in glance_pairs
    ]
    out["cc"] = {  # kept for backward-compat with existing tests
        "sales_mtd": ds.fmt_rm(s.sales_mtd_cents),
        "achievement": ds.fmt_pct(s.mtd_achievement_l1),
        "proj_achievement": ds.fmt_pct(proj_ach_of(proj_cents, l1)),
        "paid": ds.fmt_rm(e.paid_cents),
        "days": f"{entered} / {dim}",
        "completion": completion,
        "actions_open": str(kpi.open_actions(acounts)),
        "actions_overdue": str(acounts["overdue"]),
    }
    # header context line: "18 of 31 days reported · month in progress"
    prog = ("month in progress" if in_progress and not complete
            else ("month complete" if complete else "month ended"))
    out["header_context"] = (f"{entered} of {dim} days reported · {prog}")
    out["in_progress"] = "1" if not complete else ""

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

    # sparklines: raw daily sales (RM) and raw daily total stock loss (RM),
    # same gap rules as daily_line — no new aggregation.
    sales_daily = [None] * dim
    loss_daily = [None] * dim
    for d in days:
        if d.sales_cents is not None:
            sales_daily[d.date.day - 1] = d.sales_cents / 100
        losses = [v for v in d.stock_losses.values() if v is not None]
        if losses:
            loss_daily[d.date.day - 1] = sum(losses) / 100
    spark_days = list(range(1, dim + 1))
    out["spark_sales_dark"] = ops_charts.sparkline(spark_days, sales_daily,
                                                   dark=True)
    out["spark_sales_light"] = ops_charts.sparkline(spark_days, sales_daily,
                                                    dark=False)
    out["spark_stock_dark"] = ops_charts.sparkline(spark_days, loss_daily,
                                                   dark=True)
    out["spark_stock_light"] = ops_charts.sparkline(spark_days, loss_daily,
                                                    dark=False)
    
    out["finalized_notice"] = finalized_notice
    return out
