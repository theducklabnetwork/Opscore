"""Stock Management page: loss KPIs, category breakdown with investigation,
daily loss table, trend chart, and previous-month comparison. Read-only —
edits go through the existing Daily Operations deep link."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab.ops_shell import shell
from ducklab.ops_stock_state import StockState


# ---------------------------------------------------------------------------
# 1. KPI cards
# ---------------------------------------------------------------------------

def _kpi_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Stock Loss Overview", "boxes",
                extra=rx.badge(StockState.st_status,
                               color_scheme=StockState.st_status_color,
                               variant="soft", size="2", id="stock-status")),
            rx.grid(
                ds.kpi_card("Total Loss", StockState.kpi_total_loss,
                            icon="package-x",
                            sub="DMO + DMC + DMP + Missing − Extra"),
                ds.kpi_card("Loss Ratio", StockState.kpi_ratio,
                            icon="percent", sub="Total Loss ÷ monthly sales"),
                ds.kpi_card("Target", StockState.kpi_target, icon="target",
                            sub="Variance: " + StockState.kpi_variance),
                ds.kpi_card("Missing", StockState.kpi_missing,
                            icon="package-search", accent="amber"),
                ds.kpi_card("Extra", StockState.kpi_extra, icon="package-plus",
                            accent="blue", sub="Offsets Total Loss"),
                ds.kpi_card("Days With Entries", StockState.kpi_days,
                            icon="calendar-check",
                            sub="days with stock entries / days in month"),
                columns=rx.breakpoints(initial="2", sm="3", lg="6"),
                spacing="3", width="100%",
            ),
            rx.cond(
                StockState.st_finalized,
                rx.callout(
                    "A finalized snapshot exists for this month. These are "
                    "live figures and may now differ from the finalized "
                    "report.",
                    icon="info", color_scheme="amber", variant="soft",
                    width="100%", id="finalized-warning"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="stock-kpi-section",
    )


# ---------------------------------------------------------------------------
# 2. Category breakdown (+ 3. inline investigation)
# ---------------------------------------------------------------------------

def _category_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.hstack(
                rx.text(row["code"], weight="bold", size="2",
                        color=ds.ACCENT_TEXT, cursor="pointer",
                        on_click=StockState.select_category(row["code"])),
                rx.cond(
                    row["selected"] == "1",
                    rx.icon("chevron-right", size=14,
                            color=ds.ACCENT_TEXT),
                ),
                spacing="1", align="center",
            ),
            white_space="nowrap"),
        rx.table.cell(row["name"], size="2"),
        rx.table.cell(row["total"], weight="bold", white_space="nowrap"),
        rx.table.cell(row["share"]),
        rx.table.cell(row["days"]),
        rx.table.cell(
            rx.badge(row["treatment"], variant="soft", size="1",
                     color_scheme=row["treatment_color"])),
    )


def _category_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Category Breakdown", "layers"),
            rx.text(
                "Total Loss counts only Damaged in outlet (DMO), Returned by "
                "customer (DMC), Expired (DMP) and Missing stock; Extra stock "
                "found reduces it. All other codes are informational and "
                "never affect Total Loss.",
                size="2", color=ds.TEXT_MUTED),
            rx.scroll_area(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Code"),
                            rx.table.column_header_cell("Description"),
                            rx.table.column_header_cell("Monthly Total"),
                            rx.table.column_header_cell("Share of Loss"),
                            rx.table.column_header_cell("Days"),
                            rx.table.column_header_cell("Treatment"),
                        )),
                    rx.table.body(
                        rx.foreach(StockState.cat_rows, _category_row)),
                    variant="surface", width="100%",
                ),
                type="auto", scrollbars="horizontal",
            ),
            rx.cond(
                StockState.selected_code != "",
                rx.box(
                    rx.vstack(
                        rx.hstack(
                            rx.icon("search", size=16, color=ds.ACCENT_TEXT),
                            rx.heading(StockState.selected_code_name, size="4"),
                            rx.spacer(),
                            rx.text("Monthly total: ", size="2",
                                    color=ds.TEXT_MUTED),
                            rx.text(StockState.selected_code_total,
                                    weight="bold", size="2"),
                            width="100%", align="center",
                        ),
                        rx.cond(
                            StockState.inv_rows.length() > 0,
                            rx.vstack(
                                rx.foreach(
                                    StockState.inv_rows,
                                    lambda r: rx.hstack(
                                        rx.link(r["label"], href=r["href"],
                                                color=ds.ACCENT_TEXT,
                                                size="2", underline="always"),
                                        rx.spacer(),
                                        rx.text(r["amount"], size="2",
                                                weight="medium"),
                                        width="100%",
                                    )),
                                spacing="1", width="100%"),
                            rx.text("No entries for this category this month.",
                                    size="2", color=ds.TEXT_FAINT),
                        ),
                        spacing="2", align="start", width="100%",
                    ),
                    background=ds.SURFACE, border=f"1px solid {ds.BORDER}",
                    border_radius="10px", padding="1em", width="100%",
                    id="investigation-panel",
                ),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="category-section",
    )


# ---------------------------------------------------------------------------
# 4. Daily table
# ---------------------------------------------------------------------------

def _day_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["label"], white_space="nowrap"),
        rx.table.cell(row["weekday"]),
        rx.table.cell(row["sales"], white_space="nowrap"),
        rx.table.cell(row["loss"], weight="bold", white_space="nowrap"),
        rx.table.cell(row["ratio"]),
        rx.table.cell(row["missing"], white_space="nowrap"),
        rx.table.cell(row["extra"], white_space="nowrap"),
        rx.table.cell(
            rx.badge(row["status"], variant="soft", size="1",
                     color_scheme=row["status_color"])),
        rx.table.cell(
            rx.link("Edit", href="/ops/daily?date=" + row["date"],
                    size="2", color=ds.ACCENT_TEXT, underline="always"),
            white_space="nowrap"),
    )


def _daily_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Daily Stock Loss", "calendar-days"),
            rx.scroll_area(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Date"),
                            rx.table.column_header_cell("Day"),
                            rx.table.column_header_cell("Sales"),
                            rx.table.column_header_cell("Total Loss"),
                            rx.table.column_header_cell("Ratio"),
                            rx.table.column_header_cell("Missing"),
                            rx.table.column_header_cell("Extra"),
                            rx.table.column_header_cell("Status"),
                            rx.table.column_header_cell(""),
                        )),
                    rx.table.body(rx.foreach(StockState.day_rows, _day_row)),
                    variant="surface", min_width="760px",
                ),
                type="auto", scrollbars="horizontal", width="100%",
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="daily-section",
    )


# ---------------------------------------------------------------------------
# 5. Trend chart (reported days only — gaps, no interpolation)
# ---------------------------------------------------------------------------

def _trend_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Daily Total Loss Trend", "chart-line"),
            rx.cond(
                StockState.chart_rows.length() > 0,
                rx.cond(StockState.is_dark,
                        ops_charts.chart(StockState.chart_fig_dark),
                        ops_charts.chart(StockState.chart_fig_light)),
                ds.empty_state("No stock entries yet",
                               "Days with stock entries will appear here; "
                               "unreported days stay as gaps.",
                               icon="chart-line"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="trend-section",
    )


# ---------------------------------------------------------------------------
# 6. Month comparison
# ---------------------------------------------------------------------------

def _comparison_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Month Comparison", "git-compare"),
            rx.cond(
                StockState.cmp_rows.length() > 0,
                rx.vstack(
                    rx.table.root(
                        rx.table.header(
                            rx.table.row(
                                rx.table.column_header_cell("Metric"),
                                rx.table.column_header_cell(
                                    StockState.cmp_label_prev),
                                rx.table.column_header_cell("This month"),
                            )),
                        rx.table.body(
                            rx.foreach(
                                StockState.cmp_rows,
                                lambda r: rx.table.row(
                                    rx.table.cell(r["metric"], size="2"),
                                    rx.table.cell(r["prev"],
                                                  white_space="nowrap"),
                                    rx.table.cell(r["cur"], weight="bold",
                                                  white_space="nowrap"),
                                ))),
                        variant="surface", width="100%",
                    ),
                    rx.cond(
                        StockState.cmp_incomplete != "",
                        rx.badge("Incomplete month: "
                                 + StockState.cmp_incomplete,
                                 color_scheme="amber", variant="soft",
                                 size="1", id="incomplete-month"),
                    ),
                    spacing="2", width="100%",
                ),
                rx.text("No previous-month data to compare.", size="2",
                        color=ds.TEXT_FAINT),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="comparison-section",
    )


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 5b. Category composition (in-loss vs offset vs info-only)
# ---------------------------------------------------------------------------

def _composition_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Loss Composition by Category",
                               "chart-column"),
            rx.cond(
                StockState.chart_rows.length() > 0,
                rx.cond(StockState.is_dark,
                        ops_charts.chart(StockState.comp_fig_dark),
                        ops_charts.chart(StockState.comp_fig_light)),
                ds.empty_state("No stock entries yet",
                               "Category composition appears once stock "
                               "entries exist.", icon="chart-column"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="stock-composition",
    )


def stock_page() -> rx.Component:
    return shell(
        "Stock Management",
        rx.vstack(
            rx.text(
                "Monitor stock losses by category against monthly sales. "
                "Stock entries are recorded in Daily Operations; this page "
                "is read-only and links directly to the relevant day.",
                size="2", color=ds.TEXT_MUTED),
            rx.cond(
                StockState.st_error != "",
                ds.error_callout(StockState.st_error),
            ),
            rx.cond(
                ~StockState.st_config_exists,
                ds.empty_state("Stock monitoring unavailable",
                               StockState.st_error, icon="boxes"),
            ),
            rx.cond(
                StockState.st_loaded,
                rx.vstack(
                    _kpi_section(),
                    _category_section(),
                    _daily_section(),
                    _trend_section(),
                    _composition_section(),
                    _comparison_section(),
                    spacing="5", width="100%",
                ),
            ),
            spacing="4", width="100%",
        ),
        on_store=StockState.select_store,
        on_month=StockState.select_month,
    )
