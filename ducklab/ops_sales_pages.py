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


def _basket() -> rx.Component:
    return ui.section_card(
        rx.grid(
            ui.stat("SPR", S.sp_spr, size="6"),
            ui.stat("UPT", S.sp_upt, size="6"),
            ui.stat("ASP", S.sp_asp, size="6"),
            columns="3", spacing="5", width="100%",
        ),
        title="Basket", id="basket-section",
    )


def _pace_chart() -> rx.Component:
    return ui.chart_card(
        "Sales Trend",
        rx.cond(S.is_dark, ops_charts.chart(S.sp_fig_dark),
                ops_charts.chart(S.sp_fig_light)),
        ready=S.sp_chart_ready,
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


_DAILY_COLS = ["Date", ("Sales", True), ("Transactions", True),
               ("Units", True), ("SPR", True), ("UPT", True), ("ASP", True),
               ("Variance", True), "Status", ""]


def _daily_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.hstack(rx.text(row["date"], weight="medium"),
                        rx.text(row["weekday"], size="1",
                                color=ds.TEXT_FAINT),
                        spacing="2", align="baseline")),
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
                phone=("Date", "Sales", "Status", ""),
            ),
            ui.empty_state("No Records", icon="calendar"),
        ),
        ui.status_legend(),
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
            _target_ladder(),
            _daily_table(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=S.select_store,
        on_month=S.select_month,
    )
