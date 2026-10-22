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


def _kpi_tiles() -> rx.Component:
    # the projection chip + early-estimate tag appear once per page, on the
    # projected-achievement tile (same tiles and names as Daily)
    return ui.kpi_grid(
        ui.kpi_tile("Sales MTD", S.sp_sales_mtd,
                    compact=S.sp_sales_mtd_compact,
                    exact=S.sp_sales_mtd_exact, sub=S.sp_sales_mtd_sub),
        ui.kpi_tile("Achievement to date", S.sp_mtd_achievement,
                    sub=S.sp_mtd_achievement_sub),
        ui.kpi_tile("Month-end sales", S.sp_month_end,
                    compact=S.sp_month_end_compact,
                    exact=S.sp_month_end_exact, sub=S.sp_month_end_sub),
        ui.kpi_tile("Projected achievement", S.sp_l1_achievement,
                    chip=_PROJ_CHIP, tag=S.sp_early_tag,
                    tag_tip=S.sp_early_tip, sub=S.sp_l1_achievement_sub),
        ui.kpi_tile("Needed per remaining day", S.sp_needed,
                    compact=S.sp_needed_compact, exact=S.sp_needed_exact,
                    sub=S.sp_needed_sub),
        ui.kpi_tile("Avg per reported day", S.sp_avg_day,
                    compact=S.sp_avg_day_compact,
                    exact=S.sp_avg_day_exact),
        columns=rx.breakpoints(initial="2", md="3"),
        id="sales-kpis",
    )


def _basket() -> rx.Component:
    return ui.section_card(
        rx.grid(
            ui.stat("Sales per Receipt (SPR)", S.sp_spr, size="6"),
            ui.stat("Units per Transaction (UPT)", S.sp_upt, size="6"),
            ui.stat("Average Selling Price (ASP)", S.sp_asp, size="6"),
            columns=rx.breakpoints(initial="1", sm="3"),
            spacing="5", width="100%",
        ),
        title="Basket Metrics", id="basket-section",
    )


def _pace_chart() -> rx.Component:
    return ui.chart_card(
        "Pace vs Level 1 Budget",
        rx.cond(S.is_dark, ops_charts.chart(S.sp_fig_dark),
                ops_charts.chart(S.sp_fig_light)),
        ready=S.sp_chart_ready,
        subtitle="Cumulative sales (RM) against the Level 1 daily run-rate",
        detail="The pace chart appears once 3 days are reported. "
               "Unreported days are never interpolated.",
        id="pace-section",
    )


def _status_cell(status, color, tag="", tip="") -> rx.Component:
    # chips may wrap so Level / Achievement / Status fit on phones
    return ui.td(rx.hstack(ui.status_chip(status, color, wrap=True),
                           ui.early_tag(tag, tip), spacing="1",
                           wrap="wrap"), wrap=True)


def _ladder_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["level"], weight="medium")),
        ui.td(row["target"], numeric=True, wide_only=True),
        ui.td(row["achievement"], numeric=True),
        ui.td(row["gap"], numeric=True, wide_only=True),
        _status_cell(row["status"], row["color"], row["early_tag"],
                     row["early_tip"]),
    )


def _comparison_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["label"], weight="medium"), wrap=True),
        ui.td("—", numeric=True, wide_only=True),
        ui.td(row["growth"], numeric=True),
        ui.td("—", numeric=True, wide_only=True),
        _status_cell(row["status"], row["color"], row["early_tag"],
                     row["early_tip"]),
    )


def _target_ladder() -> rx.Component:
    return ui.section_card(
        ui.data_table(
            ["Level", ("Target", True, True), ("Achievement", True),
             ("Gap", True, True), "Status"],
            rx.foreach(S.sp_ladder, _ladder_row),
            rx.foreach(S.sp_comparisons, _comparison_row),
        ),
        rx.cond(~S.sp_levels_configured,
                ui.note("Levels 2-4: no uplift set in Monthly Setup")),
        title="Target Ladder & Comparisons",
        subtitle="Achievement and gap use the projected month-end until "
                 "every day is entered",
        id="ladder-section",
    )


_DAILY_COLS = ["Date", "Day", ("Sales", True), ("Trans.", True),
               ("Units", True), ("SPR", True), ("UPT", True), ("ASP", True),
               ("vs Budget", True), "Status", ""]


def _daily_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["date"], weight="medium")),
        ui.td(rx.text(row["weekday"], color=ds.TEXT_MUTED)),
        ui.td(row["sales"], numeric=True),
        ui.td(row["transactions"], numeric=True),
        ui.td(row["units"], numeric=True),
        ui.td(row["spr"], numeric=True),
        ui.td(row["upt"], numeric=True),
        ui.td(row["asp"], numeric=True),
        ui.td(row["vs_budget"], numeric=True),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(rx.link("Edit", href="/ops/daily?date=" + row["iso"],
                      size="2")),
        id="sales-row-" + row["iso"],
    )


def _daily_table() -> rx.Component:
    return ui.section_card(
        rx.cond(
            S.sp_rows.length() > 0,
            ui.data_table(
                _DAILY_COLS,
                *ui.day_rows(S.sp_rows, S.sp_rows_reported,
                             S.sp_hidden_count, S.sp_hidden_label,
                             S.sp_show_hidden, S.toggle_hidden_days,
                             _daily_row, len(_DAILY_COLS),
                             toggle_id="sales-hidden-toggle"),
            ),
            ui.empty_state("No daily records yet",
                           "Enter daily sales in Daily Operations.",
                           icon="calendar"),
        ),
        ui.status_legend(note="An entered 0 counts as reported. vs Budget "
                              "= sales minus the Level 1 daily run-rate."),
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
            _kpi_tiles(),
            _basket(),
            _pace_chart(),
            _target_ladder(),
            _daily_table(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=S.select_store,
        on_month=S.select_month,
        description="Sales against the monthly Level 1–4 targets. The "
                    "month-end figure is projected until every day is "
                    "entered.",
    )
