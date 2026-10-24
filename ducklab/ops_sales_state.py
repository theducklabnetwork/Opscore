"""Sales Performance state (/ops/sales). Read-only; every metric comes from
kpi.py via the authorized AppService preview — no formulas reimplemented
here. Identity from the server-side session; cleared on store/month switch.
"""

from __future__ import annotations

from decimal import ROUND_CEILING

import plotly.graph_objects as go
import reflex as rx

from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_charts
from ducklab.auth_service import AuthenticationError, AuthorizationError
from ducklab.auth_pages import app_service
from ducklab.operations_repository import RepositoryError
from ducklab.ops_stores_state import StoresState

# Status colors come from the shared map in design.py (ds.status_color).


def _fmt_dec_rm(value) -> str:
    return "—" if value is None else ds.fmt_rm(int(value))


def _fmt_rm0(value) -> str:
    """Whole-ringgit KPI display (compact, avoids card wrap)."""
    return "—" if value is None else ds.fmt_rm0(int(value))


class SalesState(StoresState):
    """Sales Performance page state."""

    sp_error: str = ""
    sp_finalized: bool = False

    # KPI tiles: value (wide screens), *_compact (narrow), *_exact (tooltip)
    sp_sales_mtd: str = "—"
    sp_sales_mtd_compact: str = "—"
    sp_sales_mtd_exact: str = "—"
    sp_sales_mtd_sub: str = ""
    # "Compare against: Level 1-4" (Sales Performance only), remembered
    # per viewer in the browser; default Level 1
    sp_compare: str = rx.LocalStorage("1", name="ducklab_sales_compare")
    sp_complete: bool = False               # every day of the month entered
    sp_mtd_achievement: str = "—"          # MTD / selected level (to date)
    sp_mtd_achievement_label: str = "Achievement to date vs Level 1"
    sp_mtd_achievement_sub: str = ""
    sp_month_end: str = "—"
    sp_month_end_compact: str = "—"
    sp_month_end_exact: str = "—"
    sp_month_end_label: str = "Month-End Sales"
    sp_month_end_sub: str = ""               # "Projected" / "Actual"
    sp_l1_achievement: str = "—"           # month-end / selected level
    sp_l1_achievement_label: str = "Projected achievement vs Level 1"
    sp_l1_achievement_sub: str = ""
    sp_needed: str = "—"
    sp_needed_compact: str = "—"
    sp_needed_exact: str = "—"
    sp_needed_sub: str = ""
    sp_needed_label: str = "Needed per remaining day"
    # level reached (kpi.highest_level_achieved on the month-end figure)
    sp_level: str = "—"
    sp_level_sub: str = ""
    sp_level_sub2: str = ""                 # achievement vs the next level
    sp_level_chip: str = ""                 # "Projected: Level 2" (incomplete)
    sp_level_chip_color: str = "gray"
    sp_days: str = "—"
    sp_avg_day: str = "—"
    sp_avg_day_compact: str = "—"
    sp_avg_day_exact: str = "—"
    # projection chip (ds.projection_style) shared by the projected tiles
    sp_proj_status: str = ""
    sp_proj_color: str = "gray"
    sp_early_tag: str = ""
    sp_early_tip: str = ""

    # target ladder + comparisons; months saved without a Level 2-4
    # uplift keep the workbook values and get a warning chip
    sp_levels_missing: bool = False
    sp_ladder: list[dict[str, str]] = []
    sp_comparisons: list[dict[str, str]] = []

    # basket
    sp_spr: str = "Not available"
    sp_upt: str = "Not available"
    sp_asp: str = "Not available"
    # State Average (imported benchmark for the store's State + Concept +
    # month) and the store's difference vs it; "—" without a benchmark
    sp_spr_state: str = "—"
    sp_spr_state_pct: str = "—"
    sp_spr_state_neg: bool = False
    sp_ipr_state: str = "—"
    sp_ipr_state_pct: str = "—"
    sp_ipr_state_neg: bool = False

    # daily table (reported days + one collapsed row) + pace chart
    sp_rows: list[dict[str, str]] = []
    sp_rows_reported: list[dict[str, str]] = []
    sp_hidden_count: int = 0
    sp_hidden_label: str = ""
    sp_show_hidden: bool = False
    sp_chart: list[dict] = []
    sp_chart_ready: bool = False            # >= 3 reported days
    sp_fig_dark: go.Figure = go.Figure()                    # Plotly pace (dark theme)
    sp_fig_light: go.Figure = go.Figure()                   # Plotly pace (light theme)
    # Daily Sales vs Target (hidden without daily targets), Sales by Day,
    # Sales Calendar
    sp_dvt_ready: bool = False
    sp_dvt_dark: go.Figure = go.Figure()
    sp_dvt_light: go.Figure = go.Figure()
    sp_weekday_ready: bool = False
    sp_weekday_dark: go.Figure = go.Figure()
    sp_weekday_light: go.Figure = go.Figure()
    sp_cal_ready: bool = False
    sp_cal_dark: go.Figure = go.Figure()
    sp_cal_light: go.Figure = go.Figure()

    def _clear_sales(self) -> None:
        self.sp_error = ""
        self.sp_finalized = False
        for name in ("sales_mtd", "month_end", "needed", "avg_day"):
            for suffix in ("", "_compact", "_exact"):
                setattr(self, f"sp_{name}{suffix}", "—")
        self.sp_sales_mtd_sub = ""
        self.sp_mtd_achievement = "—"
        self.sp_mtd_achievement_label = "Achievement to date vs Level 1"
        self.sp_mtd_achievement_sub = ""
        self.sp_complete = False
        self.sp_month_end_label = "Month-End Sales"
        self.sp_month_end_sub = ""
        self.sp_levels_missing = False
        self.sp_l1_achievement = "—"
        self.sp_l1_achievement_label = "Projected achievement vs Level 1"
        self.sp_l1_achievement_sub = ""
        self.sp_needed_sub = ""
        self.sp_needed_label = "Needed per remaining day"
        self.sp_level = "—"
        self.sp_level_sub = ""
        self.sp_level_sub2 = ""
        self.sp_level_chip = ""
        self.sp_level_chip_color = "gray"
        self.sp_days = "—"
        self.sp_proj_status = ""
        self.sp_proj_color = "gray"
        self.sp_early_tag = ""
        self.sp_early_tip = ""
        self.sp_ladder = []
        self.sp_comparisons = []
        self.sp_spr = self.sp_upt = self.sp_asp = "Not available"
        self.sp_spr_state = self.sp_spr_state_pct = "—"
        self.sp_ipr_state = self.sp_ipr_state_pct = "—"
        self.sp_spr_state_neg = self.sp_ipr_state_neg = False
        self.sp_rows = []
        self.sp_rows_reported = []
        self.sp_hidden_count = 0
        self.sp_hidden_label = ""
        self.sp_chart = []
        self.sp_chart_ready = False
        self.sp_fig_dark = go.Figure()
        self.sp_fig_light = go.Figure()
        self.sp_dvt_ready = self.sp_weekday_ready = self.sp_cal_ready = False
        for name in ("dvt", "weekday", "cal"):
            setattr(self, f"sp_{name}_dark", go.Figure())
            setattr(self, f"sp_{name}_light", go.Figure())

    @rx.event
    def select_compare(self, value: str | list[str]):
        """Compare against Level 1-4: re-derive the level-driven tiles and
        the highlighted pace line from the engine's per-level values."""
        self.sp_compare = str(ds.compare_level(value))
        yield SalesState.load_sales_data()

    @rx.event
    def toggle_hidden_days(self):
        self.sp_show_hidden = not self.sp_show_hidden

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
            shares = app_service.get_contribution_shares(user, store_id,
                                                         month)
            holidays = app_service.get_public_holidays(user, store_id, month)
            bench_spr, bench_ipr = app_service.get_basket_benchmark(
                user, store_id, month)
            self.sp_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
        except (AuthorizationError, AuthenticationError) as exc:
            self.sp_error = (str(exc) if not isinstance(exc, AuthenticationError)
                             else "Your session has expired. Please sign in again.")
            return
        except RepositoryError as exc:  # repository errors -> inline panel
            self.sp_error = str(exc)
            return

        s = rep.sales
        entered, dim = s.days_entered, s.days_in_month
        pstate = s.projection_state
        self._set_money("sales_mtd", s.sales_mtd_cents if entered else None)
        self.sp_sales_mtd_sub = f"{entered} of {dim} days reported"
        n = ds.compare_level(self.sp_compare)
        key = f"level{n}"
        target = s.targets.get(key)
        complete = pstate == "actual"
        self.sp_complete = complete
        # achievement to date: MTD / selected level (Level 1 = the budget
        # achievement, the engine's mtd_achievement_l1)
        self.sp_mtd_achievement_label = f"Achievement to date vs Level {n}"
        # same rounding as Daily: a shortfall is never rounded up
        self.sp_mtd_achievement = (
            ds.fmt_achievement(
                kpi.mtd_achievement(s.sales_mtd_cents, target),
                kpi.sales_status(s.sales_mtd_cents, target))
            if entered else "—")
        self.sp_mtd_achievement_sub = (
            f"Level {n} target " + ds.fmt_rm0(target)
            if target is not None else f"No Level {n} target")
        self.sp_month_end_label = ("Month-End Sales (Actual)" if complete
                                   else "Month-End Sales (Projected)")
        self.sp_month_end_sub = "Actual" if complete else "Projected"
        self._set_money("month_end", s.month_end_sales_cents)
        # one achievement tile for a complete month; projected otherwise
        self.sp_l1_achievement_label = (
            f"Achievement vs Level {n}" if complete
            else f"Projected achievement vs Level {n}")
        status = s.status_by_level.get(key, "Awaiting data")
        # a shortfall is never rounded up to 100.0% (ds.fmt_achievement)
        self.sp_l1_achievement = ds.fmt_achievement(
            s.achievement_by_level.get(key), status)
        gap = self._gap(s, key)
        self.sp_l1_achievement_sub = (
            "" if gap == "—" else
            ("Gap " if complete else "Projected gap ") + gap)
        # the engine status chip for the selected level: Achieved / Below
        # target (complete), "Projected: <status>" + "Early estimate"
        # (< 7 days) while incomplete — the same labels as the Overview
        (self.sp_proj_status, self.sp_proj_color,
         self.sp_early_tag) = ds.projection_style(pstate, status)
        self.sp_early_tip = (ds.EARLY_ESTIMATE_TIP.format(n=entered, m=dim)
                             if self.sp_early_tag else "")
        cfg = rep.config
        self.sp_levels_missing = kpi.levels_missing(cfg)
        level, nxt, short = kpi.highest_level_achieved(
            s.month_end_sales_cents, s.targets)
        self._set_level(level, nxt, short, len(s.targets), pstate,
                        entered, s.targets.get("level1") is None)
        # achievement vs the next level, unless that is the selected level
        # (the achievement tile already shows it)
        if nxt is not None and entered and nxt != n:
            self.sp_level_sub2 = (
                ds.fmt_achievement(
                    s.achievement_by_level.get(f"level{nxt}"),
                    s.status_by_level.get(f"level{nxt}", ""))
                + f" of Level {nxt}")

        # needed per remaining day / run-rate for the selected level (the
        # tile is not shown for a complete month)
        if not complete:
            self.sp_needed_label = f"Needed per remaining day for Level {n}"
            self._set_money("needed", kpi.needed_per_remaining_day_for_level(
                s.targets, n, s.sales_mtd_cents, entered, dim))
        run_rate = kpi.required_daily_run_rate(target, dim)
        self.sp_needed_sub = (f"Level {n} required run-rate: "
                              + (_fmt_rm0(run_rate) if run_rate is not None
                                 else "No target"))
        self.sp_days = f"{entered} / {dim}"
        self._set_money("avg_day", s.average_per_day)

        self.sp_ladder = [
            (lambda lbl, color, tag: {
             "level": f"Level {n}", "target": ds.fmt_rm(s.targets.get(name)),
             "achievement": ds.fmt_achievement(
                 s.achievement_by_level.get(name),
                 s.status_by_level.get(name, "")),
             "status": lbl, "color": color, "early_tag": tag,
             "early_tip": (ds.EARLY_ESTIMATE_TIP.format(n=entered, m=dim)
                           if tag else ""),
             "gap": self._gap(s, name)})(
                *ds.projection_style(
                    pstate, s.status_by_level.get(name, "Awaiting data")))
            for n, name in enumerate(
                ("level1", "level2", "level3", "level4"), start=1)
        ]
        # growth comparison statuses are projected figures too while the
        # month is incomplete
        # ("No reference" never gets the prefix or the tag)
        self.sp_comparisons = []
        for label, growth, gstatus in (
                ("SSSG/Year", s.growth_last_year,
                 s.growth_status_last_year),
                ("SSSG/Month", s.growth_last_month,
                 s.growth_status_last_month)):
            g_lbl, g_color, g_tag = ds.projection_style(pstate, gstatus)
            self.sp_comparisons.append({
                "label": label, "growth": ds.fmt_pct(growth),
                "status": g_lbl, "color": g_color, "early_tag": g_tag,
                "early_tip": (ds.EARLY_ESTIMATE_TIP.format(n=entered, m=dim)
                              if g_tag else "")})

        self.sp_spr = (ds.fmt_rm(s.spr) if s.spr is not None
                       else "Not available")
        self.sp_upt = (f"{float(s.upt):.2f}" if s.upt is not None
                       else "Not available")
        self.sp_asp = (ds.fmt_rm(s.asp) if s.asp is not None
                       else "Not available")
        state_spr = kpi.benchmark_spr_cents(bench_spr)
        self.sp_spr_state = ds.fmt_rm(state_spr)
        self.sp_spr_state_pct = ds.fmt_variance_pct(
            kpi.pct_vs_target(s.spr, state_spr))
        self.sp_spr_state_neg = self.sp_spr_state_pct.startswith(ds.MINUS)
        self.sp_ipr_state = ("—" if bench_ipr is None
                             else f"{float(bench_ipr):.2f}")
        self.sp_ipr_state_pct = ds.fmt_variance_pct(
            kpi.pct_vs_target(s.upt, bench_ipr))
        self.sp_ipr_state_neg = self.sp_ipr_state_pct.startswith(ds.MINUS)

        holidays = set(holidays)
        targets = (kpi.daily_targets(
            month, cfg.level1_base_cents, shares, holidays,
            {d.date: d.sales_cents for d in days}) if days else None)
        self.sp_rows = self._daily_rows(days, month, cfg, shares,
                                        holidays, targets)
        self.sp_rows_reported = [r for r in self.sp_rows
                                 if r["status"] != ds.DAY_NOT_REPORTED]
        self.sp_hidden_count = len(self.sp_rows) - len(self.sp_rows_reported)
        self.sp_hidden_label = ds.days_not_reported_label(
            self.sp_hidden_count)
        self.sp_chart = self._pace_chart(days, dim, s.targets.get("level1"))
        self.sp_chart_ready = entered >= 3
        pts = self.sp_chart
        p_days = [p["day"] for p in pts]
        p_actual = [p["actual"] for p in pts]
        p_budget = [p["budget"] for p in pts]
        levels = kpi.level_pace_lines(s.targets, dim)
        hl = kpi.drawn_level(s.targets, n)
        # y units live in the card subtitle (less chart clutter)
        self.sp_fig_dark = ops_charts.pace_chart(p_days, p_actual, p_budget,
                                                 dark=True, y_title="",
                                                 levels=levels, highlight=hl)
        self.sp_fig_light = ops_charts.pace_chart(p_days, p_actual, p_budget,
                                                  dark=False, y_title="",
                                                  levels=levels, highlight=hl)
        self._extra_charts(days, month, holidays, targets)

    def _extra_charts(self, days, month: str, holidays: set,
                      targets) -> None:
        """Daily Sales vs Target, Sales by Day and Sales Calendar, from the
        saved records and kpi.daily_targets (no figures recomputed)."""
        sales = {d.date: d.sales_cents for d in days
                 if d.sales_cents is not None}
        if not sales:
            return
        if targets:
            args = ([t.date.day for t in targets],
                    [sales.get(t.date) for t in targets],
                    [t.adjusted_cents if t.date in sales else None
                     for t in targets],
                    [t.result for t in targets])
            self.sp_dvt_ready = True
            self.sp_dvt_dark = ops_charts.daily_vs_target(*args, dark=True)
            self.sp_dvt_light = ops_charts.daily_vs_target(*args, dark=False)
        avgs = kpi.average_sales_by_weekday(days, holidays)
        self.sp_weekday_ready = any(v is not None for v in avgs)
        if self.sp_weekday_ready:
            self.sp_weekday_dark = ops_charts.weekday_bars(
                kpi.DSC_WEEKDAYS, avgs, dark=True)
            self.sp_weekday_light = ops_charts.weekday_bars(
                kpi.DSC_WEEKDAYS, avgs, dark=False)
        weeks = [[None if d is None else
                  (d.day, d.strftime("%a %d %b"), sales.get(d))
                  for d in week] for week in kpi.month_weeks(month)]
        self.sp_cal_ready = True
        self.sp_cal_dark = ops_charts.sales_calendar(weeks, dark=True)
        self.sp_cal_light = ops_charts.sales_calendar(weeks, dark=False)

    def _set_level(self, level, nxt, gap, total: int, pstate: str,
                   entered: int, no_target: bool) -> None:
        """"Level reached" tile from kpi.highest_level_achieved."""
        if no_target:
            self.sp_level = "No target"
            return
        if not entered:
            return
        self.sp_level = f"Level {level}" if level else "Below Level 1"
        if pstate != "actual":
            # "Projected: Level 2" — same amber chip as projection_style
            self.sp_level_chip = ds.level_reached_text(level, pstate)
            self.sp_level_chip_color = "amber"
        if nxt is None:
            self.sp_level_sub = "All levels reached"
        elif gap is not None:
            # a shortfall rounds up to the cent: never "RM 0.00 short"
            short = int(gap.to_integral_value(ROUND_CEILING))
            self.sp_level_sub = f"{ds.fmt_rm(short)} short of Level {nxt}"

    def _set_money(self, name: str, cents) -> None:
        """Whole-ringgit tile value + compact + exact (tooltip) variants."""
        cents = None if cents is None else int(cents)
        setattr(self, f"sp_{name}", ds.fmt_rm0(cents))
        setattr(self, f"sp_{name}_compact",
                ds.fmt_rm_compact(cents, whole_below=True))
        setattr(self, f"sp_{name}_exact", ds.fmt_rm(cents))

    @staticmethod
    def _gap(sales, name) -> str:
        """Display the engine's gap (kpi.target_gap_cents via
        assemble_month); no local arithmetic beyond sign formatting."""
        gap = sales.gap_by_level.get(name)
        if gap is None:
            return "—"
        sign = "" if gap >= 0 else "−"
        return f"{sign}{ds.fmt_rm(abs(int(gap)))}"

    def _daily_rows(self, days, month: str, cfg, shares: dict,
                    holidays: set, targets) -> list[dict[str, str]]:
        """Daily Sales table: day share, Daily / Adjusted Target and Result
        from kpi.daily_targets; basket figures with their % vs target."""
        from datetime import timedelta
        if not days:
            return []
        first, _, dim = kpi.month_bounds(month)
        by_day = {d.date: d for d in days}
        by_target = {t.date: t for t in targets or []}
        rows = []
        for i in range(dim):
            day = first + timedelta(days=i)
            d = by_day.get(day)
            reported = d is not None and d.sales_cents is not None
            txn = d.transactions if reported else None
            spr = (kpi.spr_cents(d.sales_cents, txn) if txn else None)
            ipr = (kpi.upt(d.units, txn)
                   if txn and d.units is not None else None)
            t = by_target.get(day)
            share = kpi.day_share(day, shares, holidays) if shares else None
            pcts = {
                "txn_pct": ds.fmt_variance_pct(
                    kpi.transactions_pct(txn, cfg.target_transactions, dim)),
                "spr_pct": ds.fmt_variance_pct(
                    kpi.pct_vs_target(spr, cfg.target_spr_cents)),
                "ipr_pct": ds.fmt_variance_pct(
                    kpi.pct_vs_target(ipr, cfg.target_ipr)),
            }
            result = t.result if t is not None else None
            rows.append({
                "iso": day.isoformat(),
                "date": day.strftime("%d %b"),
                "weekday": day.strftime("%a"),
                "ph": "PH" if day in holidays else "",
                "contribution": ds.fmt_share(share),
                "target": ds.fmt_rm(t.target_cents if t else None),
                "adjusted": ds.fmt_rm(t.adjusted_cents if t else None),
                "sales": ds.fmt_rm(d.sales_cents) if reported else "—",
                "transactions": ds.fmt_num(txn),
                "spr": ds.fmt_rm(spr) if spr is not None else "—",
                "ipr": f"{float(ipr):.2f}" if ipr is not None else "—",
                **pcts,
                # red when below target
                **{k + "_neg": "1" if v.startswith(ds.MINUS) else ""
                   for k, v in pcts.items()},
                "result": result or "",
                "result_color": ds.status_color(result or ""),
                # kept for the collapsed unreported days
                "status": (ds.day_status(d) if reported
                           else ds.DAY_NOT_REPORTED),
            })
        return rows

    def _pace_chart(self, days, dim: int, level1) -> list[dict]:
        """Cumulative pace points (kpi.pace_series; shared with Overview)."""
        return kpi.pace_series(days, dim, level1)
