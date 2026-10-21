"""Stock Management module: stock-loss monitoring for one store/month.

All reads go through AppService with the session token revalidated on every
event. Every figure comes from the verified KPI engine (kpi.py) via the
live month assembly — no stock-loss formula is duplicated here. This module
performs no writes; editing happens only via the existing Daily Operations
entry form (deep link /ops/daily?date=YYYY-MM-DD).

Business rules (Store_Operations_Monitoring_v4.xlsx):
  Total Loss = DMO + DMC + DMP + Missing - Extra
  Informational codes (2DMP, DPI, DMW, DDR, DMN, DME, DMQ, DST) never count.
  Blank = not reported; explicit 0 = reported zero. NULL is never coerced.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal

import plotly.graph_objects as go
import reflex as rx

from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_charts
from ducklab.auth_service import AuthenticationError, AuthorizationError
from ducklab.auth_pages import app_service
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.ops_manpower_state import ManpowerState
from ducklab.ops_state import OpsState, month_label

STATUS_COLOR = {
    "Within Target": "green", "Above Target": "red", "No Target": "gray",
    "Awaiting Data": "gray", "No Sales Recorded": "amber",
}
DAY_STATUS_COLOR = {
    "Not reported": "gray", "Zero": "green", "Loss": "red",
    "Net negative": "blue", "No sales": "amber",
}
TREATMENT = {1: "Included", -1: "Offset", 0: "Info only"}


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}-{m:02d}"


class StockState(ManpowerState):
    """Stock Management page state (inherits selectors + auth guards)."""

    st_loaded: bool = False
    st_finalized: bool = False
    st_config_exists: bool = True
    st_error: str = ""

    kpi_total_loss: str = "—"
    kpi_ratio: str = "—"
    kpi_target: str = "No target set"
    kpi_variance: str = "—"
    kpi_missing: str = "—"
    kpi_extra: str = "—"
    kpi_days: str = "—"
    st_status: str = ""
    st_status_color: str = "gray"

    cat_rows: list[dict[str, str]] = []        # all 13 codes
    day_rows: list[dict[str, str]] = []        # daily table
    chart_rows: list[dict] = []                # trend (reported days only)
    chart_fig_dark: go.Figure = go.Figure()                  # Plotly trend (dark theme)
    chart_fig_light: go.Figure = go.Figure()                 # Plotly trend (light theme)
    comp_fig_dark: go.Figure = go.Figure()                   # Plotly composition (dark)
    comp_fig_light: go.Figure = go.Figure()                  # Plotly composition (light)

    selected_code: str = ""
    selected_code_name: str = ""
    selected_code_total: str = "—"
    inv_rows: list[dict[str, str]] = []        # investigation dates

    cmp_label_prev: str = ""
    cmp_rows: list[dict[str, str]] = []        # prev vs current comparison
    cmp_incomplete: str = ""

    def _clear_stock(self) -> None:
        """Drop every stock value before reloading (store/month switch)."""
        self.st_loaded = False
        self.st_finalized = False
        self.st_config_exists = True
        self.st_error = ""
        self.kpi_total_loss = "—"
        self.kpi_ratio = "—"
        self.kpi_target = "No target set"
        self.kpi_variance = "—"
        self.kpi_missing = "—"
        self.kpi_extra = "—"
        self.kpi_days = "—"
        self.st_status = ""
        self.st_status_color = "gray"
        self.cat_rows = []
        self.day_rows = []
        self.chart_rows = []
        self.chart_fig_dark = go.Figure()
        self.chart_fig_light = go.Figure()
        self.comp_fig_dark = go.Figure()
        self.comp_fig_light = go.Figure()
        self.selected_code = ""
        self.selected_code_name = ""
        self.selected_code_total = "—"
        self.inv_rows = []
        self.cmp_label_prev = ""
        self.cmp_rows = []
        self.cmp_incomplete = ""

    # ------------------------------------------------------------------
    # selectors / load events
    # ------------------------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._clear_manpower()
        self._clear_stock()
        self._clear_report()
        yield StockState.refresh_report()
        yield StockState.load_stock_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_manpower()
        self._clear_stock()
        self._clear_report()
        yield StockState.refresh_report()
        yield StockState.load_stock_data()

    @rx.event
    def load_stock(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield OpsState.load_ops()
        yield StockState.load_stock_data()

    @rx.event
    def select_category(self, code: str) -> None:
        """Select a category for the investigation panel (in-memory)."""
        self.selected_code = code
        self._fill_investigation()
        yield

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    def _fill_investigation(self) -> None:
        """Populate the investigation panel from already-loaded rows."""
        sel = self.selected_code
        if not sel:
            return
        name = next((r["name"] for r in self.cat_rows if r["code"] == sel),
                    sel)
        self.selected_code_name = f"{sel} — {name}"
        self.selected_code_total = next(
            (r["total"] for r in self.cat_rows if r["code"] == sel), "—")
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            month = self._selected_month()
            if store_id is None or month is None:
                return
            days = app_service.get_daily_records(user, store_id, month)
        except (AuthenticationError, AuthorizationError, RepositoryError):
            return
        self.inv_rows = [
            {"date": d.date.isoformat(),
             "label": d.date.strftime("%d %b %Y"),
             "amount": ds.fmt_rm(d.stock_losses[sel]),
             "href": f"/ops/daily?date={d.date.isoformat()}"}
            for d in days if sel in d.stock_losses
        ]

    @rx.event
    def load_stock_data(self) -> None:
        self._clear_stock()
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

        try:
            self.st_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
        except Exception:
            self.st_finalized = False

        try:
            cfg = app_service.get_month_config(user, store_id, month)
        except ConfigNotFoundError:
            self.st_config_exists = False
            self.st_error = (
                f"No monthly configuration exists for {month_label(month)}. "
                "An administrator must configure this store/month before "
                "stock monitoring is available. No target is assumed.")
            return
        except (AuthorizationError, StoreNotFoundError):
            self.st_config_exists = False
            self.st_error = "You do not have access to this store."
            return

        try:
            rep = app_service.preview_month(user, store_id, month)
            days = app_service.get_daily_records(user, store_id, month)
            codes = app_service.list_stock_loss_codes(user)
        except (AuthorizationError, StoreNotFoundError, RepositoryError) as exc:
            self.st_error = str(exc)
            return
        self.st_loaded = True

        st, s = rep.stock, rep.sales
        signs = {c["code"]: c["contribution_sign"] for c in codes}

        # --- KPI cards --------------------------------------------------
        sales_mtd = s.sales_mtd_cents
        target = cfg.stock_loss_target            # Decimal or None
        ratio = st.loss_pct_of_sales              # Decimal or None
        self.kpi_total_loss = ds.fmt_rm(st.total_loss_cents)
        self.kpi_ratio = ds.fmt_pct(ratio)
        self.kpi_missing = ds.fmt_rm(st.totals_by_code.get("MISSING", 0))
        self.kpi_extra = ds.fmt_rm(st.totals_by_code.get("EXTRA", 0))
        days_with_loss = sum(1 for d in days if d.stock_losses)
        self.kpi_days = f"{days_with_loss} / {s.days_in_month}"
        if s.days_entered == 0 and not days:
            self.st_status = "Awaiting Data"
        elif not sales_mtd:
            self.st_status = "No Sales Recorded"
        elif target is None:
            self.st_status = "No Target"
        elif ratio is not None and ratio <= target:
            self.st_status = "Within Target"
        else:
            self.st_status = "Above Target"
        self.st_status_color = STATUS_COLOR[self.st_status]
        if target is None:
            self.kpi_target = "No target set"
            self.kpi_variance = "—"
        else:
            self.kpi_target = ds.fmt_pct(target)
            if ratio is None:
                self.kpi_variance = "—"
            else:
                var_pp = (ratio - target) * 100
                self.kpi_variance = f"{float(var_pp):+.1f} pp"

        # --- category breakdown ------------------------------------------
        rows: list[dict[str, str]] = []
        for c in codes:
            code, sign = c["code"], c["contribution_sign"]
            total = st.totals_by_code.get(code, 0)
            n_days = sum(1 for d in days if code in d.stock_losses)
            share = None
            if sign == 1 and st.total_loss_cents:
                share = Decimal(total) / Decimal(st.total_loss_cents)
            rows.append({
                "code": code, "name": c["name"],
                "total": ds.fmt_rm(total),
                "share": ds.fmt_pct(share) if sign == 1 else "—",
                "days": str(n_days),
                "treatment": TREATMENT[sign],
                "treatment_color": {"Included": "indigo", "Offset": "blue",
                                    "Info only": "gray"}[TREATMENT[sign]],
                "selected": "1" if code == self.selected_code else "0",
            })
        self.cat_rows = rows

        # --- investigation panel (if a category was selected) ------------
        if self.selected_code:
            self._fill_investigation()

        # --- daily table + trend ------------------------------------------
        y, m = int(month[:4]), int(month[5:7])
        n_days = calendar.monthrange(y, m)[1]
        by_date = {d.date: d for d in days}
        drows: list[dict[str, str]] = []
        crows: list[dict] = []
        trend_labels: list[str] = []          # every calendar day
        trend_vals: list = []                 # RM loss or None (gap)
        for i in range(1, n_days + 1):
            d = date(y, m, i)
            rec = by_date.get(d)
            trend_labels.append(d.strftime("%d %b"))
            if rec is None:
                drows.append({
                    "date": d.isoformat(), "label": d.strftime("%d %b"),
                    "weekday": d.strftime("%a"), "sales": "—", "loss": "—",
                    "ratio": "—", "missing": "—", "extra": "—",
                    "status": "Not reported",
                    "status_color": DAY_STATUS_COLOR["Not reported"],
                })
                trend_vals.append(None)
                continue
            day_loss = kpi.total_loss_cents(rec.stock_losses, signs)
            dr = kpi.stock_loss_pct(day_loss, rec.sales_cents)
            if not rec.stock_losses and rec.sales_cents is None:
                status = "No sales"
            elif not rec.stock_losses:
                status = "Zero"
            elif day_loss < 0:
                status = "Net negative"
            elif day_loss > 0:
                status = "Loss"
            else:
                status = "Zero"
            drows.append({
                "date": d.isoformat(), "label": d.strftime("%d %b"),
                "weekday": d.strftime("%a"),
                "sales": ds.fmt_rm(rec.sales_cents),
                "loss": ds.fmt_rm(day_loss),
                "ratio": ds.fmt_pct(dr),
                "missing": ds.fmt_rm(rec.stock_losses.get("MISSING"))
                if "MISSING" in rec.stock_losses else "—",
                "extra": ds.fmt_rm(rec.stock_losses.get("EXTRA"))
                if "EXTRA" in rec.stock_losses else "—",
                "status": status,
                "status_color": DAY_STATUS_COLOR[status],
            })
            if rec.stock_losses:  # gaps for unreported days; no interpolation
                crows.append({"date": d.strftime("%d %b"),
                              "loss": day_loss / 100})
                trend_vals.append(day_loss / 100)
            else:
                trend_vals.append(None)
        self.day_rows = drows
        self.chart_rows = crows
        # Plotly figures, one per theme (identical data, styling only)
        self.chart_fig_dark = ops_charts.daily_line(
            trend_labels, trend_vals, dark=True, name="Total loss")
        self.chart_fig_light = ops_charts.daily_line(
            trend_labels, trend_vals, dark=False, name="Total loss")
        comp_items = [{"code": c["code"], "name": c["name"],
                       "total_cents": st.totals_by_code.get(c["code"], 0),
                       "sign": c["contribution_sign"]} for c in codes]
        self.comp_fig_dark = ops_charts.composition(comp_items, dark=True)
        self.comp_fig_light = ops_charts.composition(comp_items, dark=False)

        # --- month comparison ---------------------------------------------
        prev = _prev_month(month)
        self.cmp_label_prev = month_label(prev)
        try:
            rep_prev = app_service.preview_month(user, store_id, prev)
            p_st, p_s = rep_prev.stock, rep_prev.sales
            self.cmp_rows = [
                {"metric": "Total Loss",
                 "prev": ds.fmt_rm(p_st.total_loss_cents),
                 "cur": ds.fmt_rm(st.total_loss_cents)},
                {"metric": "Loss Ratio",
                 "prev": ds.fmt_pct(p_st.loss_pct_of_sales),
                 "cur": ds.fmt_pct(ratio)},
                {"metric": "Missing",
                 "prev": ds.fmt_rm(p_st.totals_by_code.get("MISSING", 0)),
                 "cur": self.kpi_missing},
                {"metric": "Extra",
                 "prev": ds.fmt_rm(p_st.totals_by_code.get("EXTRA", 0)),
                 "cur": self.kpi_extra},
            ]
            flags = []
            if p_s.days_entered < p_s.days_in_month:
                flags.append(f"{self.cmp_label_prev}: {p_s.days_entered} of "
                             f"{p_s.days_in_month} days reported")
            if s.days_entered < s.days_in_month:
                flags.append(f"{month_label(month)}: {s.days_entered} of "
                             f"{s.days_in_month} days reported")
            self.cmp_incomplete = " · ".join(flags)
        except Exception:
            self.cmp_rows = []
            self.cmp_incomplete = ""
