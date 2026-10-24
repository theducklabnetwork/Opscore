"""Sales Performance page (/ops/sales).

Layout (ui kit): headline KPI tiles, basket metrics, pace chart, then the
detail tables (target ladder, daily sales with unreported days collapsed).
"""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab import ui
from ducklab.ops_sales_state import SalesState
from ducklab.ops_shell import shell

S = SalesState
_PROJ_CHIP = (S.sp_proj_status, S.sp_proj_color)


def _levels_warning(id: str) -> rx.Component:
    """Months saved before four levels were required."""
    return rx.cond(S.sp_levels_missing,
                   ui.status_chip(ds.LEVELS_MISSING, "amber", id=id,
                                  wrap=True))


def _level_item(n: int) -> rx.Component:
    # "Level 1".."Level 4"; "L1".."L4" on phones (< 480px)
    return rx.segmented_control.item(
        rx.el.span(f"Level {n}",
                   display=rx.breakpoints(initial="none", xs="inline")),
        rx.el.span(f"L{n}",
                   display=rx.breakpoints(initial="inline", xs="none")),
        value=str(n),
    )


def _compare_control() -> rx.Component:
    # Sales Performance only; Overview and Monthly Report stay on Level 1
    return rx.hstack(
        rx.text("Target:", size="2", color=ds.TEXT_MUTED, weight="medium"),
        rx.segmented_control.root(
            *[_level_item(n) for n in (1, 2, 3, 4)],
            value=S.sp_compare,
            on_change=S.select_compare,
            size="1", id="compare-level",
        ),
        _levels_warning("levels-missing"),
        spacing="3", align="center", wrap="wrap", width="100%",
    )


# --- headline tiles: built per layout (a component is placed once). Plain
# label + full value (RM 350,819.00); status chips stay, no sub-lines -------
def _tile(label, value, id=None, **kw) -> rx.Component:
    # a size down on wide screens too: exact money fits five across
    return ui.kpi_tile(label, value, id=id, size="6", size_narrow="4", **kw)


def _achievement() -> rx.Component:
    # selected level's engine status chip (actual or "Projected: ...")
    return _tile("Achievement", S.sp_l1_achievement, id="sales-achievement",
                 chip=_PROJ_CHIP, tag=S.sp_early_tag, tag_tip=S.sp_early_tip)


def _level_reached() -> rx.Component:
    return _tile("Level Reached", S.sp_level, id="sales-level",
                 chip=(S.sp_level_chip, S.sp_level_chip_color),
                 tag=S.sp_early_tag, tag_tip=S.sp_early_tip)


def _avg_day(span_md: str) -> rx.Component:
    # closes the last row: full width on phones (2 columns)
    return rx.box(
        _tile("Daily Sales (Avg.)", S.sp_avg_day_exact),
        grid_column=rx.breakpoints(initial="span 2", md=span_md),
        min_width="0", id="sales-avg-day",
    )


def _kpi_tiles() -> rx.Component:
    # complete month: 4 tiles (4 across; 2 + 2 on phones);
    # incomplete: 5 tiles with Projected Sales (5 across; 2 + 2 + 1 wide)
    complete = ui.kpi_grid(
        _tile("Sales", S.sp_sales_mtd_exact, id="sales-mtd"),
        _achievement(),
        _level_reached(),
        rx.box(_tile("Daily Sales (Avg.)", S.sp_avg_day_exact),
               min_width="0", id="sales-avg-day"),
        columns=rx.breakpoints(initial="2", md="4"),
        id="sales-kpis",
    )
    incomplete = ui.kpi_grid(
        _tile("Sales", S.sp_sales_mtd_exact, id="sales-mtd"),
        _tile("Projected Sales", S.sp_month_end_exact, id="sales-projected"),
        _achievement(),
        _level_reached(),
        _avg_day("auto"),
        columns=rx.breakpoints(initial="2", md="5"),
        id="sales-kpis",
    )
    return rx.cond(S.sp_complete, complete, incomplete)


def _state_average(value, pct, neg, id: str) -> rx.Component:
    # the imported State benchmark beside the store's own figure, with the
    # difference in % (red when below); "—" when there is no benchmark
    return rx.vstack(
        rx.text("State Average", size="2", color=ds.TEXT_MUTED,
                weight="medium"),
        rx.hstack(
            rx.text(value, size="4", weight="medium", color=ds.TEXT,
                    white_space="nowrap"),
            rx.cond(pct != "—",
                    rx.text(pct, size="2", white_space="nowrap",
                            color=rx.cond(neg, ds.ERROR, ds.TEXT_MUTED))),
            spacing="2", align="baseline", wrap="wrap"),
        spacing="1", align="start", min_width="0", id=id,
    )


def _basket_pair(label, value, avg, pct, neg, id: str) -> rx.Component:
    return rx.vstack(
        ui.stat(label, value, size="6"),
        _state_average(avg, pct, neg, id),
        spacing="3", align="start", min_width="0", width="100%",
    )


def _basket() -> rx.Component:
    return ui.section_card(
        rx.grid(
            _basket_pair("SPR", S.sp_spr, S.sp_spr_state, S.sp_spr_state_pct,
                         S.sp_spr_state_neg, "basket-spr-state"),
            _basket_pair("IPR", S.sp_upt, S.sp_ipr_state, S.sp_ipr_state_pct,
                         S.sp_ipr_state_neg, "basket-ipr-state"),
            ui.stat("ASP", S.sp_asp, size="6"),
            # two across on phones (ASP wraps), three from tablet up
            columns=rx.breakpoints(initial="2", sm="3"), spacing="5",
            width="100%",
        ),
        title="Basket", id="basket-section",
    )


def _themed(dark, light, height: str = "340px") -> rx.Component:
    return rx.cond(S.is_dark, ops_charts.chart(dark, height),
                   ops_charts.chart(light, height))


def _pace_chart() -> rx.Component:
    return ui.chart_card(
        "Sales Trend",
        _themed(S.sp_fig_dark, S.sp_fig_light),
        ready=S.sp_chart_ready,
        id="pace-section",
    )


def _daily_vs_target() -> rx.Component:
    # hidden when daily targets are unavailable (no Level 1 / no DSC match)
    return rx.cond(S.sp_dvt_ready, ui.section_card(
        _themed(S.sp_dvt_dark, S.sp_dvt_light),
        title="Daily Sales vs Target", id="dvt-section"))


def _sales_by_day() -> rx.Component:
    return ui.chart_card(
        "Sales by Day",
        _themed(S.sp_weekday_dark, S.sp_weekday_light, "280px"),
        ready=S.sp_weekday_ready, id="weekday-section")


def _sales_calendar() -> rx.Component:
    return ui.chart_card(
        "Sales Calendar",
        _themed(S.sp_cal_dark, S.sp_cal_light, "300px"),
        ready=S.sp_cal_ready, id="calendar-section")


def _day_charts() -> rx.Component:
    # side by side from desktop up, stacked on phones / tablets
    return rx.grid(
        _sales_by_day(), _sales_calendar(),
        columns=rx.breakpoints(initial="1", lg="2"),
        spacing=ui.GAP, width="100%",
    )


def _status_cell(status, color, tag="", tip="") -> rx.Component:
    # chips may wrap so Level / Achievement / Status fit on phones
    return ui.td(rx.hstack(ui.status_chip(status, color, wrap=True),
                           ui.early_tag(tag, tip), spacing="1",
                           wrap="wrap"), wrap=True)


def _ladder_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["level"], weight="medium")),
        ui.td(row["target"], numeric=True),
        ui.td(row["achievement"], numeric=True),
        ui.td(row["gap"], numeric=True),
        _status_cell(row["status"], row["color"], row["early_tag"],
                     row["early_tip"]),
    )


def _comparison_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["label"], weight="medium"), wrap=True),
        ui.td("—", numeric=True),
        ui.td(row["growth"], numeric=True),
        ui.td("—", numeric=True),
        _status_cell(row["status"], row["color"], row["early_tag"],
                     row["early_tip"]),
    )


def _target_ladder() -> rx.Component:
    return ui.section_card(
        ui.data_table(
            ["Level", ("Target", True), ("Achievement", True),
             ("Gap", True), "Status"],
            rx.foreach(S.sp_ladder, _ladder_row),
            rx.foreach(S.sp_comparisons, _comparison_row),
            phone=("Level", "Achievement", "Status"),
        ),
        title="Targets",
        id="ladder-section",
    )


_DAILY_COLS = ["Date", "PH", ("Contribution", True), ("Target", True),
               ("Adjusted Target", True), ("Sales", True),
               ("Transactions", True), ("SPR", True), ("IPR", True),
               "Result", ""]


def _pct_cell(value, pct, neg) -> rx.Component:
    # figure with its % vs target beside it, red when below target
    return ui.td(
        rx.hstack(
            rx.text(value),
            rx.cond(pct != "—",
                    rx.text(pct, size="1",
                            color=rx.cond(neg == "1", ds.ERROR,
                                          ds.TEXT_MUTED))),
            spacing="2", align="baseline", justify="end"),
        numeric=True)


def _daily_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.hstack(rx.text(row["date"], weight="medium"),
                        rx.text(row["weekday"], size="1",
                                color=ds.TEXT_FAINT),
                        spacing="2", align="baseline")),
        ui.td(rx.cond(row["ph"] != "", ui.status_chip(row["ph"], "violet"))),
        ui.td(row["contribution"], numeric=True),
        ui.td(row["target"], numeric=True),
        ui.td(row["adjusted"], numeric=True),
        ui.td(row["sales"], numeric=True),
        _pct_cell(row["transactions"], row["txn_pct"], row["txn_pct_neg"]),
        _pct_cell(row["spr"], row["spr_pct"], row["spr_pct_neg"]),
        _pct_cell(row["ipr"], row["ipr_pct"], row["ipr_pct_neg"]),
        ui.td(rx.cond(row["result"] != "",
                      ui.status_chip(row["result"], row["result_color"]))),
        ui.td(rx.link("Edit", href="/ops/daily?date=" + row["iso"],
                      size="2")),
        id="sales-row-" + row["iso"],
    )


# eleven columns: tighter cells from tablet up so Result stays clear of
# the pinned Edit column at 1440px (phones keep the kit's own padding)
_DAILY_TIGHT = {"@media (min-width: 640px)": {
    "& table th, & table td": {"paddingLeft": "8px", "paddingRight": "8px"}}}


def _daily_table() -> rx.Component:
    return ui.section_card(
        rx.cond(
            S.sp_rows.length() > 0,
            rx.box(
                ui.data_table(
                    _DAILY_COLS,
                    *ui.day_rows(S.sp_rows, S.sp_rows_reported,
                                 S.sp_hidden_count, S.sp_hidden_label,
                                 S.sp_show_hidden, S.toggle_hidden_days,
                                 _daily_row, len(_DAILY_COLS),
                                 toggle_id="sales-hidden-toggle"),
                    phone=("Date", "Sales", "Result", ""),
                ),
                style=_DAILY_TIGHT, width="100%", min_width="0"),
            ui.empty_state("No Records", icon="calendar"),
        ),
        ui.note("Targets split Level 1 by the Daily Sales Contribution for "
                "the store's State and Concept; a shortfall carries to the "
                "next reported day."),
        title="Daily Sales", id="sales-daily-section",
    )


def sales_page() -> rx.Component:
    return shell(
        "Sales Performance",
        rx.vstack(
            rx.cond(S.sp_finalized,
                    rx.callout(
                        "This month has a finalized snapshot. Figures below "
                        "are live previews; the finalized report is "
                        "unchanged.",
                        icon="lock", color_scheme="amber", variant="soft",
                        width="100%")),
            rx.cond(S.sp_error != "", ds.error_callout(S.sp_error)),
            _compare_control(),
            _kpi_tiles(),
            _basket(),
            _pace_chart(),
            _daily_vs_target(),
            _day_charts(),
            _target_ladder(),
            _daily_table(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=S.select_store,
        on_month=S.select_month,
    )
