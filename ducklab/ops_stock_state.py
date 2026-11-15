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
    ConfigNotFoundError, InvalidMonthError, RepositoryError,
    StoreNotFoundError,
)
from ducklab.ops_shared_state import (
    ACCESS_DENIED, SESSION_EXPIRED, SharedOpsState, auth_message, month_label,
)

# All status colors: ds.status_color (design.py shared map)
TREATMENT = ds.TREATMENT


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    m -= 1
    if m == 0:
        y, m = y - 1, 12
    return f"{y:04d}-{m:02d}"


class StockState(SharedOpsState):
    """Stock Management page state (flat base: shared selectors + guards)."""

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
    kpi_total_loss_sub: str = ""
    kpi_ratio_sub: str = ""
    st_status: str = ""
    st_status_color: str = "gray"

    cat_rows: list[dict[str, str]] = []        # all 13 codes
    day_rows: list[dict[str, str]] = []        # daily table (every day)
    st_rows_reported: list[dict[str, str]] = []  # days with a record
    st_hidden_count: int = 0                   # unreported days (collapsed)
    st_hidden_label: str = ""
    st_show_hidden: bool = False
    chart_rows: list[dict] = []                # trend (reported days only)
    st_chart_ready: bool = False               # >= 3 days with entries
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
        self.kpi_total_loss_sub = ""
        self.kpi_ratio_sub = ""
        self.st_status = ""
        self.st_status_color = "gray"
        self.cat_rows = []
        self.day_rows = []
        self.st_rows_reported = []
        self.st_hidden_count = 0
        self.st_hidden_label = ""
        self.chart_rows = []
        self.st_chart_ready = False
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
        self._clear_stock()
        self._clear_shared_status()
        yield StockState.resync_selection()
        yield StockState.load_stock_data()

    @rx.event
    def select_month(self, label: str):
        split_change = self._is_month_part(label)
        if split_change:
            label = self.selected_month_label
        if not split_change and label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_stock()
        self._clear_shared_status()
        yield StockState.resync_selection()
        yield StockState.load_stock_data()

    @rx.event
    def load_stock(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield StockState.load_selectors()
        yield StockState.load_stock_data()

    @rx.event
    def toggle_stock_hidden_days(self):
        self.st_show_hidden = not self.st_show_hidden

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
        self.selected_code_name = (
            f"{ds.STOCK_CODE_LABELS.get(sel, sel)} · {name}")
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
            self.error_message = self.st_error = SESSION_EXPIRED
            self.report_status = "error"
            return

        try:
            finalized = bool(
                app_service.list_report_versions(user, store_id, month))
            cfg = app_service.get_month_config(user, store_id, month)
        except AuthenticationError:
            self.st_config_exists = False
            self.st_error = SESSION_EXPIRED
            return
        except ConfigNotFoundError:
            self.st_config_exists = False
            self.st_error = (
                f"No monthly configuration exists for {month_label(month)}. "
                "An administrator must configure this store/month before "
                "stock monitoring is available. No target is assumed.")
            return
        except (AuthorizationError, StoreNotFoundError):
            self.st_config_exists = False
            self.st_error = ACCESS_DENIED
            return
        self.st_finalized = finalized

        try:
            rep = app_service.preview_month(user, store_id, month)
            days = app_service.get_daily_records(user, store_id, month)
            codes = app_service.list_stock_loss_codes(user)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.st_error = auth_message(exc)
            return
        except RepositoryError as exc:
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
        self.kpi_total_loss_sub = (f"{days_with_loss} of {s.days_in_month} "
                                   "days with stock entries")
        # One status for Stock, Report and Overview (kpi.py)
        self.st_status = kpi.stock_status_label(
            st.total_loss_cents, sales_mtd, target, s.days_entered)
        self.st_status_color = ds.status_color(self.st_status)
        if target is None:
            self.kpi_target = "No target set"
            self.kpi_variance = "—"
        else:
            self.kpi_target = ds.fmt_pct(target)
            if ratio is None:
                self.kpi_variance = "—"
            else:
                var_pp = (ratio - kpi.to_decimal(target)) * 100
                self.kpi_variance = f"{float(var_pp):+.1f} pp"
        self.kpi_ratio_sub = (
            "No target set" if target is None
            else f"Target {self.kpi_target}"
            + ("" if self.kpi_variance == "—"
               else f" · {self.kpi_variance}"))

        # --- category breakdown ------------------------------------------
        rows: list[dict[str, str]] = []
        order = {k: i for i, k in enumerate(ds.STOCK_CODE_ORDER)}
        for c in sorted(codes, key=lambda c: (order.get(c["code"], 99),
                                              c["code"])):
            code, sign = c["code"], c["contribution_sign"]
            total = st.totals_by_code.get(code, 0)
            n_days = sum(1 for d in days if code in d.stock_losses)
            share = None
            if sign == 1 and st.total_loss_cents:
                share = Decimal(total) / Decimal(st.total_loss_cents)
            rows.append({
                "code": code, "name": c["name"],
                # "Missing" / "Extra" as words; the stored code is kept
                "label": ds.STOCK_CODE_LABELS.get(code, code),
                "total": ds.fmt_rm(total),
                "share": ds.fmt_pct(share) if sign == 1 else "—",
                "days": str(n_days),
                "treatment": TREATMENT[sign],
                "treatment_color": ds.status_color(TREATMENT[sign]),
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
        trend_labels: list[str] = []          # every calendar day (hover)
        trend_days: list[int] = []            # numeric x (day of month)
        trend_vals: list = []                 # RM loss or None (gap)
        for i in range(1, n_days + 1):
            d = date(y, m, i)
            rec = by_date.get(d)
            trend_labels.append(d.strftime("%d %b"))
            trend_days.append(i)
            if rec is None:
                drows.append({
                    "date": d.isoformat(), "label": d.strftime("%d %b"),
                    "weekday": d.strftime("%a"), "sales": "—", "loss": "—",
                    "ratio": "—", "missing": "—", "extra": "—",
                    "status": "Not Reported",
                    "status_color": ds.status_color("Not Reported"),
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
                "status_color": ds.status_color(status),
            })
            if rec.stock_losses:  # gaps for unreported days; no interpolation
                crows.append({"date": d.strftime("%d %b"),
                              "loss": day_loss / 100})
                trend_vals.append(day_loss / 100)
            else:
                trend_vals.append(None)
        self.day_rows = drows
        self.st_rows_reported = [r for r in drows
                                 if r["status"] != ds.DAY_NOT_REPORTED]
        self.st_hidden_count = len(drows) - len(self.st_rows_reported)
        self.st_hidden_label = ds.days_not_reported_label(
            self.st_hidden_count)
        self.chart_rows = crows
        self.st_chart_ready = len(crows) >= 3
        # Plotly figures, one per theme (identical data, styling only)
        self.chart_fig_dark = ops_charts.daily_line(
            trend_labels, trend_vals, dark=True, name="Total Loss",
            y_title="", day_numbers=trend_days)
        self.chart_fig_light = ops_charts.daily_line(
            trend_labels, trend_vals, dark=False, name="Total Loss",
            y_title="", day_numbers=trend_days)
        comp_items = [{"code": ds.STOCK_CODE_LABELS.get(c["code"], c["code"]),
                       "name": c["name"],
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
        except (ConfigNotFoundError, InvalidMonthError):
            # previous month not configured: no comparison to show
            self.cmp_rows = []
            self.cmp_incomplete = ""
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.cmp_rows = []
            self.cmp_incomplete = ""
            self.st_error = auth_message(exc)
