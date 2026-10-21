"""Sales Performance page (/ops/sales)."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab.ops_sales_state import SalesState
from ducklab.ops_shell import shell


def _kpi_cards() -> rx.Component:
    return rx.grid(
        ds.kpi_card("Sales MTD", SalesState.sp_sales_mtd, icon="banknote"),
        ds.kpi_card(SalesState.sp_month_end_label, SalesState.sp_month_end,
                    icon="chart-line"),
        ds.kpi_card("Level 1 Achievement", SalesState.sp_l1_achievement,
                    icon="target"),
        ds.kpi_card("Needed / Remaining Day", SalesState.sp_needed,
                    icon="calendar-clock", sub=SalesState.sp_needed_sub),
        ds.kpi_card("Days Entered", SalesState.sp_days, icon="calendar-check"),
        ds.kpi_card("Avg / Reported Day", SalesState.sp_avg_day,
                    icon="trending-up"),
        columns=rx.breakpoints(initial="2", sm="3", lg="6"),
        spacing="3", width="100%",
    )


def _ladder_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(row["level"], weight="bold", size="2")),
        rx.table.cell(row["target"]),
        rx.table.cell(row["achievement"]),
        rx.table.cell(row["gap"]),
        rx.table.cell(rx.hstack(rx.badge(row["status"], color_scheme=row["color"],
                               variant="soft", size="1"),
                      rx.cond(row["early_tag"] != "",
                              rx.tooltip(
                                  rx.badge(row["early_tag"], color_scheme="blue",
                                           variant="outline", size="1"),
                                  content=row["early_tip"])),
                      spacing="1")),
    )


def _comparison_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(row["label"], weight="bold", size="2")),
        rx.table.cell("—"),
        rx.table.cell(row["growth"]),
        rx.table.cell("—"),
        rx.table.cell(rx.hstack(rx.badge(row["status"], color_scheme=row["color"],
                               variant="soft", size="1"),
                      rx.cond(row["early_tag"] != "",
                              rx.tooltip(
                                  rx.badge(row["early_tag"], color_scheme="blue",
                                           variant="outline", size="1"),
                                  content=row["early_tip"])),
                      spacing="1")),
    )


def _target_ladder() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Target Ladder & Comparisons", "target"),
            rx.scroll_area(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Level"),
                            rx.table.column_header_cell("Target"),
                            rx.table.column_header_cell("Achievement"),
                            rx.table.column_header_cell("Gap"),
                            rx.table.column_header_cell("Status"),
                        )),
                    rx.table.body(
                        rx.foreach(SalesState.sp_ladder, _ladder_row),
                        rx.foreach(SalesState.sp_comparisons,
                                   _comparison_row),
                    ),
                    variant="surface", width="100%",
                ),
                type="auto", scrollbars="horizontal",
                style={"maxWidth": "100%"},
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="ladder-section",
    )


def _basket() -> rx.Component:
    return rx.vstack(
        ds.section_heading("Basket Metrics", "shopping-basket"),
        rx.grid(
            ds.kpi_card("Sales per Receipt (SPR)", SalesState.sp_spr),
            ds.kpi_card("Units per Transaction (UPT)", SalesState.sp_upt),
            ds.kpi_card("Average Selling Price (ASP)", SalesState.sp_asp),
            columns=rx.breakpoints(initial="1", sm="3"),
            spacing="3", width="100%",
        ),
        spacing="3", width="100%", id="basket-section",
    )


def _daily_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(row["date"], weight="medium", size="2")),
        rx.table.cell(row["weekday"]),
        rx.table.cell(row["sales"]),
        rx.table.cell(row["transactions"]),
        rx.table.cell(row["units"]),
        rx.table.cell(row["spr"]),
        rx.table.cell(row["upt"]),
        rx.table.cell(row["asp"]),
        rx.table.cell(row["vs_budget"]),
        rx.table.cell(rx.badge(row["status"],
                               color_scheme=row["status_color"],
                               variant="soft", size="1")),
        rx.table.cell(
            rx.link("Edit", href="/ops/daily?date=" + row["iso"], size="1"),
        ),
        id="sales-row-" + row["iso"],
    )


def _daily_table() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Daily Sales", "calendar-check"),
            rx.cond(
                SalesState.sp_rows.length() > 0,
                rx.scroll_area(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Date"),
                                rx.table.column_header_cell("Day"),
                                rx.table.column_header_cell("Sales"),
                                rx.table.column_header_cell("Trans."),
                                rx.table.column_header_cell("Units"),
                                rx.table.column_header_cell("SPR"),
                                rx.table.column_header_cell("UPT"),
                                rx.table.column_header_cell("ASP"),
                                rx.table.column_header_cell("vs Budget"),
                                rx.table.column_header_cell("Status"),
                                rx.table.column_header_cell(""),
                            )),
                        rx.table.body(
                            rx.foreach(SalesState.sp_rows, _daily_row)),
                        variant="surface", width="100%",
                    ),
                    type="auto", scrollbars="horizontal",
                    style={"maxWidth": "100%"},
                ),
                ds.empty_state("No daily records yet",
                               "Enter daily sales in Daily Operations.",
                               icon="calendar"),
            ),
            rx.text(
                "Blank = not reported; an entered 0 counts as reported. "
                "vs Budget = sales minus the Level 1 daily run-rate.",
                size="1", color=ds.TEXT_FAINT),
            spacing="3", width="100%",
        ),
        width="100%", id="sales-daily-section",
    )


def _pace_chart() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Pace vs Level 1 Budget", "chart-line"),
            rx.cond(
                SalesState.sp_chart.length() > 0,
                rx.cond(SalesState.is_dark,
                        ops_charts.chart(SalesState.sp_fig_dark),
                        ops_charts.chart(SalesState.sp_fig_light)),
                ds.empty_state("Awaiting data",
                               "Cumulative actuals stop at the last reported "
                               "day; unreported days are never interpolated.",
                               icon="chart-line"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="pace-section",
    )


def sales_page() -> rx.Component:
    return shell(
        "Sales Performance",
        rx.vstack(
            rx.text(
                "Sales results against the monthly Level 1–4 targets and "
                "reference periods. Figures follow the workbook rules: the "
                "month-end figure is projected until every day is entered.",
                size="2", color=ds.TEXT_MUTED),
            rx.cond(SalesState.sp_finalized,
                    rx.callout(
                        "This month has a finalized snapshot. Figures below "
                        "are live previews; the finalized report is "
                        "unchanged.",
                        icon="lock", color_scheme="amber", variant="soft",
                        width="100%")),
            rx.cond(SalesState.sp_error != "",
                    ds.error_callout(SalesState.sp_error)),
            _kpi_cards(),
            _target_ladder(),
            _basket(),
            _pace_chart(),
            _daily_table(),
            spacing="4", width="100%",
        ),
        on_store=SalesState.select_store,
        on_month=SalesState.select_month,
    )
