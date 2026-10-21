"""Sales Performance state (/ops/sales). Read-only; every metric comes from
kpi.py via the authorized AppService preview — no formulas reimplemented
here. Identity from the server-side session; cleared on store/month switch.
"""

from __future__ import annotations

from decimal import Decimal

import plotly.graph_objects as go
import reflex as rx

from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_charts
from ducklab.auth_service import AuthenticationError, AuthorizationError
from ducklab.auth_pages import app_service
from ducklab.ops_stores_state import StoresState

_STATUS_COLOR = {
    "Achieved": "green", "Below target": "red",
    "No target": "gray", "Awaiting data": "gray",
    "Growth": "green", "Decline": "red",
    "No reference": "gray",
}


def _fmt_dec_rm(value) -> str:
    return "—" if value is None else ds.fmt_rm(int(value))


def _fmt_rm0(value) -> str:
    """Whole-ringgit KPI display (compact, avoids card wrap)."""
    return "—" if value is None else ds.fmt_rm0(int(value))


class SalesState(StoresState):
    """Sales Performance page state."""

    sp_error: str = ""
    sp_finalized: bool = False

    # KPI cards
    sp_sales_mtd: str = "—"
    sp_month_end: str = "—"
    sp_month_end_label: str = "Month-End Sales"
    sp_l1_achievement: str = "—"
    sp_needed: str = "—"
    sp_needed_sub: str = ""
    sp_days: str = "—"
    sp_avg_day: str = "—"

    # target ladder + comparisons
    sp_ladder: list[dict[str, str]] = []
    sp_comparisons: list[dict[str, str]] = []

    # basket
    sp_spr: str = "Not available"
    sp_upt: str = "Not available"
    sp_asp: str = "Not available"

    # daily table + pace chart
    sp_rows: list[dict[str, str]] = []
    sp_chart: list[dict] = []
    sp_fig_dark: go.Figure = go.Figure()                    # Plotly pace (dark theme)
    sp_fig_light: go.Figure = go.Figure()                   # Plotly pace (light theme)

    def _clear_sales(self) -> None:
        self.sp_error = ""
        self.sp_finalized = False
        self.sp_sales_mtd = self.sp_month_end = "—"
        self.sp_month_end_label = "Month-End Sales"
        self.sp_l1_achievement = "—"
        self.sp_needed = "—"
        self.sp_needed_sub = ""
        self.sp_days = "—"
        self.sp_avg_day = "—"
        self.sp_ladder = []
        self.sp_comparisons = []
        self.sp_spr = self.sp_upt = self.sp_asp = "Not available"
        self.sp_rows = []
        self.sp_chart = []
        self.sp_fig_dark = go.Figure()
        self.sp_fig_light = go.Figure()

    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._clear_all_modules()
        self._clear_sales()
        yield SalesState.refresh_report()
        yield SalesState.load_sales_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_all_modules()
        self._clear_sales()
        yield SalesState.refresh_report()
        yield SalesState.load_sales_data()

    def _clear_all_modules(self) -> None:
        for fn in (self._clear_report, self._clear_manpower,
                   self._clear_stock, self._clear_setup,
                   self._clear_stores):
            fn()

    @rx.event
    def load_sales(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        from ducklab.ops_state import OpsState
        yield OpsState.load_ops()
        yield SalesState.load_sales_data()

    @rx.event
    def load_sales_data(self) -> None:
        self._clear_sales()
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            month = self._selected_month()
            if store_id is None or month is None:
                return
            rep = app_service.preview_month(user, store_id, month)
            days = app_service.get_daily_records(user, store_id, month)
            self.sp_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
        except (AuthorizationError, AuthenticationError) as exc:
            self.sp_error = (str(exc) if not isinstance(exc, AuthenticationError)
                             else "Your session has expired. Please sign in again.")
            return
        except Exception as exc:  # repository errors -> inline panel
            self.sp_error = str(exc)
            return

        s = rep.sales
        entered, dim = s.days_entered, s.days_in_month
        self.sp_sales_mtd = ds.fmt_rm0(s.sales_mtd_cents)
        complete = entered >= dim and dim > 0
        self.sp_month_end_label = ("Month-End Sales (Actual)" if complete
                                   else "Month-End Sales (Projected)")
        self.sp_month_end = _fmt_rm0(s.month_end_sales_cents)
        self.sp_l1_achievement = ds.fmt_pct(s.achievement_by_level.get("level1"))
        self.sp_needed = _fmt_rm0(s.needed_per_remaining_day)
        run_rate = s.required_daily_run_rate
        self.sp_needed_sub = ("Required run-rate: "
                              + (_fmt_rm0(run_rate) if run_rate is not None
                                 else "No target"))
        self.sp_days = f"{entered} / {dim}"
        avg = (Decimal(s.sales_mtd_cents) / entered) if entered else None
        self.sp_avg_day = _fmt_rm0(avg)

        self.sp_ladder = [
            {"level": f"Level {n}", "target": ds.fmt_rm(s.targets.get(name)),
             "achievement": ds.fmt_pct(s.achievement_by_level.get(name)),
             "status": s.status_by_level.get(name, "Awaiting data"),
             "color": _STATUS_COLOR.get(
                 s.status_by_level.get(name, ""), "gray"),
             "gap": self._gap(s, name)}
            for n, name in enumerate(
                ("level1", "level2", "level3", "level4"), start=1)
        ]
        self.sp_comparisons = [
            {"label": "vs Same Month Last Year",
             "growth": ds.fmt_pct(s.growth_last_year),
             "status": self._growth_status(s.growth_last_year, entered),
             "color": _STATUS_COLOR.get(
                 self._growth_status(s.growth_last_year, entered), "gray")},
            {"label": "vs Last Month",
             "growth": ds.fmt_pct(s.growth_last_month),
             "status": self._growth_status(s.growth_last_month, entered),
             "color": _STATUS_COLOR.get(
                 self._growth_status(s.growth_last_month, entered), "gray")},
        ]

        self.sp_spr = (ds.fmt_rm(s.spr) if s.spr is not None
                       else "Not available")
        self.sp_upt = (f"{float(s.upt):.2f}" if s.upt is not None
                       else "Not available")
        self.sp_asp = (ds.fmt_rm(s.asp) if s.asp is not None
                       else "Not available")

        self.sp_rows = self._daily_rows(days, dim, s.targets.get("level1"))
        self.sp_chart = self._pace_chart(days, dim, s.targets.get("level1"))
        pts = self.sp_chart
        p_days = [p["day"] for p in pts]
        p_actual = [p["actual"] for p in pts]
        p_budget = [p["budget"] for p in pts]
        self.sp_fig_dark = ops_charts.pace_chart(p_days, p_actual, p_budget,
                                                 dark=True)
        self.sp_fig_light = ops_charts.pace_chart(p_days, p_actual, p_budget,
                                                  dark=False)

    def _gap(self, sales, name) -> str:
        target = sales.targets.get(name)
        if target is None or sales.month_end_sales_cents is None:
            return "—"
        gap = Decimal(sales.month_end_sales_cents) - Decimal(int(target))
        sign = "" if gap >= 0 else "−"
        return f"{sign}{ds.fmt_rm(abs(int(gap)))}"

    @staticmethod
    def _growth_status(growth, entered: int) -> str:
        if entered == 0:
            return "Awaiting data"
        if growth is None:
            return "No reference"
        return "Growth" if growth >= 0 else "Decline"

    def _daily_rows(self, days, dim: int, level1) -> list[dict[str, str]]:
        from datetime import date as _date, timedelta
        budget = kpi.required_daily_run_rate(level1, dim)
        by_day = {d.date: d for d in days}
        first = days[0].date.replace(day=1) if days else None
        if first is None:
            return []
        rows = []
        for i in range(dim):
            day = first + timedelta(days=i)
            d = by_day.get(day)
            reported = d is not None and d.sales_cents is not None
            if d is not None:
                spr = kpi.spr_cents(d.sales_cents, d.transactions) \
                    if reported and d.transactions else None
                upt = kpi.upt(d.units or 0, d.transactions) \
                    if reported and d.transactions else None
                asp = kpi.asp_cents(d.sales_cents, d.units) \
                    if reported and d.units else None
            else:
                spr = upt = asp = None
            vs = None
            if reported and budget is not None:
                vs = Decimal(d.sales_cents) - budget
            status = ("Not reported" if not reported
                      else "Zero sales" if d.sales_cents == 0 else "Reported")
            rows.append({
                "iso": day.isoformat(),
                "date": day.strftime("%d %b"),
                "weekday": day.strftime("%a"),
                "sales": ds.fmt_rm(d.sales_cents) if reported else "—",
                "transactions": str(d.transactions) if reported and
                    d.transactions is not None else "—",
                "units": str(d.units) if reported and
                    d.units is not None else "—",
                "spr": ds.fmt_rm(spr) if spr is not None else "—",
                "upt": f"{float(upt):.2f}" if upt is not None else "—",
                "asp": ds.fmt_rm(asp) if asp is not None else "—",
                "vs_budget": ("−" if vs is not None and vs < 0 else "")
                    + (ds.fmt_rm(abs(int(vs))) if vs is not None else "—"),
                "status": status,
                "status_color": {"Not reported": "gray", "Zero sales":
                                 "amber", "Reported": "green"}[status],
            })
        return rows

    def _pace_chart(self, days, dim: int, level1) -> list[dict]:
        budget = kpi.required_daily_run_rate(level1, dim)
        entered = {d.date.day: d.sales_cents for d in days
                   if d.sales_cents is not None}
        last_reported = max(entered, default=0)
        cum = 0
        cumulative = {}
        for day in sorted(entered):
            cum += entered[day]
            cumulative[day] = cum
        out = []
        running = 0
        for day in range(1, dim + 1):
            if day in cumulative:
                running = cumulative[day]
            out.append({
                "day": day,
                "budget": int(budget * day) if budget is not None else None,
                # actual line stops at the last reported day (no extension)
                "actual": running if day <= last_reported and last_reported
                          else None,
            })
        return out
