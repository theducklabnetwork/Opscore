"""Stock Management page: loss KPIs, trend and composition charts, category
breakdown with investigation, daily loss table and previous-month
comparison. Read-only — edits go through the existing Daily Operations deep
link.

Layout (ui kit): headline tiles, charts, then the detail tables.
"""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab import ui
from ducklab.ops_shell import shell
from ducklab.ops_stock_state import StockState

S = StockState

# daily loss statuses (kpi-derived, stock specific) + the unreported chip
_DAY_STATUSES = ("Loss", "Net negative", "Zero", "No sales",
                 ds.DAY_NOT_REPORTED)


# ---------------------------------------------------------------------------
# 1. KPI tiles
# ---------------------------------------------------------------------------

def _tile(label, value, id=None, **kw) -> rx.Component:
    # plain label + full value; status chip stays, no sub-lines
    return ui.kpi_tile(label, value, id=id, size_narrow="4", **kw)


def _kpi_section() -> rx.Component:
    return rx.vstack(
        ui.kpi_grid(
            _tile("Total Loss", S.kpi_total_loss),
            _tile("Loss Ratio", S.kpi_ratio, id="stock-ratio",
                  chip=(S.st_status, S.st_status_color),
                  chip_id="stock-status"),
            _tile("Missing", S.kpi_missing),
            _tile("Extra", S.kpi_extra),
        ),
        rx.cond(
            S.st_finalized,
            rx.callout(
                "A finalized snapshot exists for this month. These are live "
                "figures and may now differ from the finalized report.",
                icon="info", color_scheme="amber", variant="soft",
                width="100%", id="finalized-warning"),
        ),
        spacing="3", width="100%", id="stock-kpi-section",
    )


# ---------------------------------------------------------------------------
# 2. Charts (reported days only — gaps, no interpolation)
# ---------------------------------------------------------------------------

def _charts() -> rx.Component:
    """Trend + composition when there is enough data; otherwise ONE compact
    "Not enough data yet" card instead of two stacked empty ones."""
    return rx.cond(
        S.st_chart_ready,
        rx.vstack(
            ui.section_card(
                rx.cond(S.is_dark, ops_charts.chart(S.chart_fig_dark),
                        ops_charts.chart(S.chart_fig_light)),
                title="Daily Loss", id="trend-section"),
            ui.section_card(
                rx.cond(S.is_dark, ops_charts.chart(S.comp_fig_dark),
                        ops_charts.chart(S.comp_fig_light)),
                title="Loss by Category", id="stock-composition"),
            spacing=ui.GAP, width="100%",
        ),
        ui.section_card(ui.not_enough_data(), title="Daily Loss",
                        id="trend-section"),
    )


# ---------------------------------------------------------------------------
# 3. Category breakdown (+ inline investigation)
# ---------------------------------------------------------------------------

def _category_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.hstack(
            rx.text(row["label"], weight="medium", color=ds.ACCENT_TEXT,
                    cursor="pointer",
                    on_click=S.select_category(row["code"])),
            rx.cond(row["selected"] == "1",
                    rx.icon("chevron-right", size=14, color=ds.ACCENT_TEXT)),
            spacing="1", align="center")),
        ui.td(rx.text(row["name"], size="2"), wrap=True,
              style={"minWidth": "120px", "maxWidth": "240px"}),
        ui.td(row["total"], numeric=True),
        ui.td(row["share"], numeric=True),
        ui.td(row["days"], numeric=True),
        ui.td(ui.status_chip(row["treatment"], row["treatment_color"])),
    )


def _investigation() -> rx.Component:
    return rx.cond(
        S.selected_code != "",
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.text(S.selected_code_name, size="3", weight="medium"),
                    rx.spacer(),
                    rx.text("Total ", size="2", color=ds.TEXT_MUTED),
                    rx.text(S.selected_code_total, size="2",
                            weight="medium"),
                    width="100%", align="center", wrap="wrap",
                ),
                rx.cond(
                    S.inv_rows.length() > 0,
                    rx.vstack(
                        rx.foreach(
                            S.inv_rows,
                            lambda r: rx.hstack(
                                rx.link(r["label"], href=r["href"],
                                        size="2"),
                                rx.spacer(),
                                rx.text(r["amount"], size="2"),
                                width="100%",
                            )),
                        spacing="1", width="100%"),
                    rx.text("No Entries", size="2", color=ds.TEXT_FAINT),
                ),
                spacing="2", align="start", width="100%",
            ),
            # a quiet inset (not a nested card): top rule only
            border_top=f"1px solid {ui.CARD_BORDER}", padding_top="1em",
            width="100%", id="investigation-panel",
        ),
    )


def _category_section() -> rx.Component:
    return ui.section_card(
        ui.data_table(
            ["Code", "Description", ("Total", True), ("Share", True),
             ("Days", True), "Treatment"],
            rx.foreach(S.cat_rows, _category_row),
            phone=("Code", "Total", "Treatment"),
        ),
        _investigation(),
        title="Categories",
        id="category-section",
    )


# ---------------------------------------------------------------------------
# 4. Daily table (unreported days collapsed)
# ---------------------------------------------------------------------------

# phones show Date, Total Loss, Status and Edit
_DAY_COLS = ["Date", ("Sales", True),
             ("Total Loss", True), ("Ratio", True), ("Missing", True),
             ("Extra", True), "Status", ""]


def _day_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.hstack(rx.text(row["label"], weight="medium"),
                        rx.text(row["weekday"], size="1",
                                color=ds.TEXT_FAINT, class_name="ph-hide"),
                        spacing="2", align="baseline")),
        ui.td(row["sales"], numeric=True),
        ui.td(row["loss"], numeric=True),
        ui.td(row["ratio"], numeric=True),
        ui.td(row["missing"], numeric=True),
        ui.td(row["extra"], numeric=True),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(rx.link("Edit", href="/ops/daily?date=" + row["date"],
                      size="2")),
    )


def _daily_section() -> rx.Component:
    return ui.section_card(
        ui.data_table(
            _DAY_COLS,
            *ui.day_rows(S.day_rows, S.st_rows_reported, S.st_hidden_count,
                         S.st_hidden_label, S.st_show_hidden,
                         S.toggle_stock_hidden_days, _day_row,
                         len(_DAY_COLS), toggle_id="stock-hidden-toggle"),
            phone=("Date", "Total Loss", "Status", ""),
        ),
        ui.status_legend(_DAY_STATUSES),
        title="Daily Stock Loss", id="daily-section",
    )


# ---------------------------------------------------------------------------
# 5. Month comparison
# ---------------------------------------------------------------------------

def _comparison_section() -> rx.Component:
    return ui.section_card(
        rx.cond(
            S.cmp_rows.length() > 0,
            ui.data_table(
                ["Metric", (S.cmp_label_prev, True), ("This Month", True)],
                rx.foreach(S.cmp_rows, lambda r: rx.el.tr(
                    ui.td(r["metric"]),
                    ui.td(r["prev"], numeric=True),
                    ui.td(rx.text(r["cur"], weight="medium"),
                          numeric=True),
                )),
            ),
            ui.empty_state("No Previous Month", icon="git-compare"),
        ),
        title="Comparison", id="comparison-section",
    )


# ---------------------------------------------------------------------------
# page
# ---------------------------------------------------------------------------

def stock_page() -> rx.Component:
    return shell(
        "Stock Management",
        rx.vstack(
            rx.cond(S.st_error != "", ds.error_callout(S.st_error)),
            rx.cond(
                ~S.st_config_exists,
                ui.empty_state("Stock monitoring unavailable", S.st_error,
                               icon="boxes"),
            ),
            rx.cond(
                S.st_loaded,
                rx.vstack(
                    _kpi_section(),
                    _charts(),
                    _category_section(),
                    _daily_section(),
                    _comparison_section(),
                    spacing=ui.GAP, width="100%",
                ),
            ),
            spacing=ui.GAP, width="100%",
        ),
        on_store=S.select_store,
        on_month=S.select_month,
    )
