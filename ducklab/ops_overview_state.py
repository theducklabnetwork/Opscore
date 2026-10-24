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

# Status colors come from the one shared map in design.py
# (ds.status_color) — keyed by the exact strings kpi.py returns.


def _today() -> date:
    return kpi.kl_today()


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
    targets_title: str = "Targets"
    targets_extra: list[dict[str, str]] = []
    levels_missing: str = ""

    # "At a glance" secondary figures: [{label, value, title, href}]
    glance: list[dict[str, str]] = []

    header_context: str = ""
    in_progress: str = ""

    # sparklines (sales + stock), one figure per theme
    spark_sales_dark: go.Figure = go.Figure()
    spark_sales_light: go.Figure = go.Figure()
    spark_stock_dark: go.Figure = go.Figure()
    spark_stock_light: go.Figure = go.Figure()
    # a sparkline needs 3 reported days; fewer -> "Not enough data yet"
    spark_sales_ready: bool = False
    spark_stock_ready: bool = False

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
        self.targets_title = "Targets"
        self.targets_extra = []
        self.levels_missing = ""
        self.glance = []
        self.header_context = ""
        self.in_progress = ""
        self.spark_sales_dark = go.Figure()
        self.spark_sales_light = go.Figure()
        self.spark_stock_dark = go.Figure()
        self.spark_stock_light = go.Figure()
        self.spark_sales_ready = False
        self.spark_stock_ready = False

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


def _bar(fraction) -> float:
    """Progress-bar width (0..1) for an engine fraction; 0 when None."""
    return min(float(fraction), 1.0) if fraction is not None else 0.0


def compute_overview(user, store_id: int, month: str, today: date) -> dict:
    """Pure overview computation (testable without a Reflex runtime).

    Returns a dict of OverviewState fields. Every value/status comes from
    assemble_month via AppService.preview_month — nothing recomputed.
    Raises ConfigNotFoundError/AuthorizationError/... for the caller.
    """
    # One overdue/reporting reference date for Report, Overview and
    # Actions: kpi.reporting_date (today for the current month, month end
    # for a past month). ``today`` itself only drives "in progress".
    as_of = kpi.reporting_date(month, today)
    rep = app_service.preview_month(user, store_id, month, as_of=as_of)
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
    pstate = s.projection_state
    complete = pstate == "actual"
    y, mnum = (int(x) for x in month.split("-"))
    month_end = date(y, mnum, dim)
    in_progress = today <= month_end
    l1 = s.targets.get("level1")
    out = {}

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
    # Exact engine values (no int() truncation): projection, MTD and
    # projected achievement come straight from assemble_month.
    proj_cents = s.month_end_sales_cents
    proj_ach = s.achievement_by_level.get("level1")
    sales_reported = entered > 0
    mtd_pct = (float(s.mtd_achievement_l1)
               if sales_reported and s.mtd_achievement_l1 is not None
               else 0.0)
    proj_pct = float(proj_ach) if proj_ach is not None else 0.0
    over = proj_pct > 1.0
    # projected statuses are not results: neutral (amber) until complete;
    # an "Early estimate" tag appears when fewer than 7 days are entered.
    sales_status_label, sales_chip_color, early_tag = ds.projection_style(
        pstate, l1_status)
    early_tip = (ds.EARLY_ESTIMATE_TIP.format(n=entered, m=dim)
                 if early_tag else "")
    # level reached (same rule and levels as /ops/sales): "Level 3
    # reached" / "Projected: Level 2"; nothing before data or without target
    level, _, _ = kpi.highest_level_achieved(s.month_end_sales_cents,
                                             s.targets)
    level_text = (" · " + ds.level_reached_text(level, pstate)
                  if sales_reported and l1 is not None else "")
    out["pill_sales"] = {
        "reported": "1" if sales_reported else "",
        "value": ds.fmt_rm0(s.sales_mtd_cents),   # full value on the tile
        "title": ds.fmt_rm(s.sales_mtd_cents),
        "mtd_pct": mtd_pct,
        "proj_pct": proj_pct,
        "fill_pct": min(proj_pct, 1.0),
        "over_label": (f"+{round((proj_pct - 1.0) * 100)}%" if over else ""),
        "progress_color": ("green" if complete
                           and l1_status == kpi.SALES_ACHIEVED
                           else "indigo"),
        "status": sales_status_label,
        "status_color": sales_chip_color,
        "early_tag": early_tag,
        "early_tip": early_tip,
        "status_label": "", "status_tip": "", "tooltip": "",
        "context": ("Projected "
                    + ops_charts.fmt_money_compact(proj_cents)
                    + " · " + ds.fmt_achievement(proj_ach, l1_status)
                    + " of Level 1" + level_text),
    }
    exp_status = e.total_expenses_status
    exp_color = ds.status_color(exp_status)
    paid_count = kpi.paid_count_in_month([x.input for x in
                                          app_service.get_expense_transactions(
                                              user, store_id)], month,
                                         store_id)
    exp_reported = paid_count > 0
    out["pill_expenses"] = {
        "reported": "1" if exp_reported else "",
        "value": ds.fmt_rm0(e.paid_cents),        # full value on the tile
        "title": ds.fmt_rm(e.paid_cents),
        "progress": _bar(kpi.budget_utilization(e.paid_cents,
                                                e.total_budget_cents)),
        "marker": 0.0,
        "progress_color": ("red" if exp_status == kpi.TOTAL_OVER_BUDGET
                           else "green"),
        "status": exp_status,
        "status_color": exp_color,
        "status_label": "HQ status (paid vs total budget)",
        "status_tip": ("Summary!E40: paid month-to-date vs the total "
                       "category budget, count-based"),
        "tooltip": ("HQ status (paid vs total budget) — Summary!E40: paid "
                    "month-to-date vs the total category budget, "
                    "count-based"),
        "early_tag": "", "early_tip": "",
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
            "progress": _bar(kpi.ratio_of_target(ratio, target)),
            "marker": 0.0,
            "progress_color": ("red" if m.salary_ratio_status
                               == kpi.SALARY_FAILURE else "green"),
            "status": m.salary_ratio_status,
            "status_color": ds.status_color(m.salary_ratio_status),
            "early_tag": "", "early_tip": "",
            "status_label": "", "status_tip": "", "tooltip": "",
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
            "early_tag": "", "early_tip": "",
            "status_label": "", "status_tip": "", "tooltip": "",
            "context": ("Turnover " + ops_charts.fmt_pct1(m.turnover_pct)
                        + " (" + m.turnover_status + ")"),
        }
    stock_reported = st.total_loss_cents != 0 or sales_reported
    stock_status = kpi.stock_status_label(
        st.total_loss_cents, s.sales_mtd_cents, cfg.stock_loss_target,
        entered)
    out["pill_stock"] = {
        "reported": "1" if stock_reported else "",
        "value": ops_charts.fmt_pct1(st.loss_pct_of_sales),
        "title": ds.fmt_pct(st.loss_pct_of_sales),
        "progress": _bar(kpi.ratio_of_target(st.loss_pct_of_sales,
                                             cfg.stock_loss_target)),
        "marker": 0.0,
        "progress_color": ("red" if st.status == kpi.STOCK_OVER_TARGET
                           else "green"),
        "status": stock_status,
        "status_color": ds.status_color(stock_status),
        "early_tag": "", "early_tip": "",
        "status_label": "", "status_tip": "", "tooltip": "",
        "context": ("Target " + ops_charts.fmt_pct1(cfg.stock_loss_target)
                    + " · loss "
                    + ops_charts.fmt_money_compact(st.total_loss_cents)),
    }

    # Sales targets panel: progress vs engine achievement/status, all four
    # levels. A month saved without a Level 2-4 uplift keeps the workbook
    # values (base x (1 + 0)) and gets the "Levels 2-4 missing" chip.
    level_names = ("level1", "level2", "level3", "level4")
    out["levels_missing"] = "1" if kpi.levels_missing(cfg) else ""
    out["targets_title"] = "Targets"
    out["targets"] = [
        {"label": f"Level {i}",
         "target": ops_charts.fmt_money_compact(t),
         "pct": ds.fmt_achievement(s.achievement_by_level.get(name),
                                   s.status_by_level.get(name, "")),
         "progress": _bar(s.achievement_by_level.get(name)),
         "status": s.status_by_level.get(name, "Awaiting data"),
         "status_color": (ds.status_color(s.status_by_level.get(name, ""))
                          if complete else
                          ("gray" if s.status_by_level.get(name, "Awaiting data")
                           in ("Awaiting data", "No target") else "amber")),
         "progress_color": ("green" if complete and s.status_by_level.get(name)
                            == kpi.SALES_ACHIEVED else "indigo")}
        for i, name in enumerate(level_names, 1)
        for t in [s.targets.get(name)]
    ]
    # Level 1 run-rate ("Daily Target") and needed per remaining day
    # ("Required Daily Sales") live in the targets panel; both are hidden
    # once the month is complete
    rr = s.required_daily_run_rate
    nd = s.needed_per_remaining_day
    out["targets_extra"] = [] if complete else [
        {"label": "Daily Target",
         "value": ops_charts.fmt_money_compact(rr),
         "title": ds.fmt_rm0(rr)},
        {"label": "Required Daily Sales",
         "value": ops_charts.fmt_money_compact(nd),
         "title": ds.fmt_rm0(nd)},
    ]

    # --- secondary figures ("At a glance") --------------------------------
    # action counts for this store/month scope (raised in month or open)
    actions = app_service.get_actions(user, store_id)
    visible = kpi.actions_in_month_scope(actions, month)
    acounts = kpi.action_counts([a.input for a in visible], as_of=as_of)
    avg = s.average_per_day
    # full values ("RM 10,000.00"), never the short form
    glance_pairs = [
        ("Daily Sales (Avg.)",
         ds.fmt_rm(int(avg) if avg is not None else None), "", "/ops/sales"),
    ]
    if exp_reported:
        glance_pairs += [
            ("Incurred", ds.fmt_rm(e.incurred_cents), "", "/ops/expenses"),
            ("Unpaid", ds.fmt_rm(e.open_cents), "", "/ops/expenses"),
        ]
    glance_pairs += [
        ("Open Actions", str(kpi.open_actions(acounts)), "", "/ops/actions"),
    ]
    out["glance"] = [
        {"label": label, "value": value, "title": title, "href": href}
        for label, value, title, href in glance_pairs
    ]
    out["cc"] = {  # kept for backward-compat with existing tests
        "sales_mtd": ds.fmt_rm(s.sales_mtd_cents),
        "achievement": ds.fmt_achievement(
            s.mtd_achievement_l1, kpi.sales_status(s.sales_mtd_cents, l1)),
        "proj_achievement": ds.fmt_achievement(proj_ach, l1_status),
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
    if complete and l1_status == kpi.SALES_BELOW:
        items.append({"icon": "trending-down",
                      "text": "Sales below Level 1 for a complete month",
                      "href": "/ops/sales"})
    over_budget = [c.name for c in e.categories
                   if c.budget_status == kpi.CATEGORY_OVER_BUDGET]
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
    if m.turnover_status == kpi.TURNOVER_HIGH:
        items.append({"icon": "users",
                      "text": "HIGH TURNOVER this month",
                      "href": "/ops/manpower"})
    if st.status == kpi.STOCK_OVER_TARGET:
        items.append({"icon": "boxes",
                      "text": "Stock loss over target",
                      "href": "/ops/stock"})
    if not complete and in_progress:
        items.append({"icon": "calendar-clock",
                      "text": (f"{dim - entered} unreported day"
                               + ("" if dim - entered == 1 else "s")),
                      "href": "/ops/daily"})
    out["attention"] = items

    # --- pace chart (same computation as /ops/sales) --------------------
    days = app_service.get_daily_records(user, store_id, month)
    pace = kpi.pace_series(days, dim, l1)
    p_days = [p["day"] for p in pace]
    p_actual = [p["actual"] for p in pace]
    p_budget = [p["budget"] for p in pace]
    out["pace_has_data"] = any(v is not None for v in p_actual)
    levels = kpi.level_pace_lines(s.targets, dim)
    # no axis title: the card title ("Sales Trend") says it
    out["pace_fig_dark"] = ops_charts.pace_chart(p_days, p_actual, p_budget,
                                                 dark=True, y_title="",
                                                 levels=levels)
    out["pace_fig_light"] = ops_charts.pace_chart(p_days, p_actual, p_budget,
                                                  dark=False, y_title="",
                                                  levels=levels)

    # sparklines: raw daily sales (RM) and per-day Total Loss (RM),
    # same gap rules as daily_line — no new aggregation.
    signs = app_service.read_repo.get_contribution_signs()
    sales_daily = [None] * dim
    loss_daily = [None] * dim
    for d in days:
        if d.sales_cents is not None:
            sales_daily[d.date.day - 1] = d.sales_cents / 100
        if d.stock_losses:
            # per-day Total Loss via kpi.total_loss_cents (same as the
            # Stock page: signed contributions, info-only codes excluded)
            loss_daily[d.date.day - 1] = kpi.total_loss_cents(
                d.stock_losses, signs) / 100
    spark_days = list(range(1, dim + 1))
    out["spark_sales_ready"] = sum(v is not None for v in sales_daily) >= 3
    out["spark_stock_ready"] = sum(v is not None for v in loss_daily) >= 3
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
