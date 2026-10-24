"""DuckLab operations pages: dashboard landing + monthly report."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab import ui
from ducklab.auth_pages import AuthState
from ducklab.ops_overview_state import OverviewState
from ducklab.ops_shell import shell
from ducklab.ops_state import OpsState


O = OverviewState


def dashboard_page() -> rx.Component:
    return shell(
        "Dashboard",
        rx.vstack(
            rx.cond(
                ~OpsState.has_stores,
                ui.section_card(
                    rx.text("No stores are assigned to your account. Ask an "
                            "administrator to assign you a store to see the "
                            "overview.", size="2", color=ds.TEXT_MUTED),
                    rx.link(rx.button("Open Monthly Report",
                                      color_scheme="indigo"),
                            href="/ops/report"),
                    title="Welcome to Opscore Operations",
                ),
            ),
            rx.cond(
                OpsState.has_stores,
                rx.vstack(
                    rx.cond(O.error != "", ds.error_callout(O.error)),
                    rx.cond(
                        O.finalized_notice != "",
                        rx.callout(O.finalized_notice, icon="lock",
                                   color_scheme="violet", variant="soft",
                                   width="100%"),
                    ),
                    rx.cond(
                        O.loaded,
                        rx.vstack(
                            _hero_row(),
                            _main_row(),
                            _bottom_row(),
                            spacing=ui.GAP, width="100%",
                        ),
                    ),
                    spacing="3", width="100%",
                ),
            ),
            width="100%",
        ),
        on_store=O.select_store,
        on_month=O.select_month,
    )


# ---------------------------------------------------------------------------
# Overview sections (values pre-formatted by OverviewState; each figure
# appears exactly once on the page)
# ---------------------------------------------------------------------------

def _track(*segments, height: str = "6px", **props) -> rx.Component:
    """Kit progress track (same bar as the Expenses/Manpower pages)."""
    return rx.box(*segments, display="flex", width="100%", height=height,
                  background=ui.ROW_BORDER, border_radius="3px",
                  overflow="hidden", flex_shrink="0", **props)


def _plain_bar(pill) -> rx.Component:
    """Single-segment bar (0..1 in pill["progress"])."""
    return _track(rx.box(
        width=(pill["progress"].to(float) * 100).to(str) + "%",
        height="100%", background=rx.color(pill["progress_color"], 9),
        border_radius="3px"))


def _sales_bar(pill) -> rx.Component:
    """Two segments: MTD solid, projected extension lighter; capped at
    100% width."""
    return _track(
        rx.box(width=(pill["mtd_pct"].to(float) * 100).to(str) + "%",
               height="100%",
               background=rx.color(pill["progress_color"], 9)),
        rx.box(width=((pill["fill_pct"].to(float)
                       - pill["mtd_pct"].to(float)) * 100).to(str) + "%",
               height="100%",
               background=rx.color(pill["progress_color"], 9),
               opacity="0.35"),
    )


def _hero_card(title, pill, href, spark=None, bar=None) -> rx.Component:
    """One pillar tile: small muted label, large full value, the coloured
    bar and (Sales, Stock) the sparkline; "No data" when nothing is
    reported. The whole tile links to its module."""
    if bar is None:
        bar = _plain_bar(pill)
    reported = rx.vstack(
        rx.tooltip(
            rx.text(pill["value"], size=rx.breakpoints(initial="6", lg="7"),
                    weight="medium", color=ds.TEXT, white_space="nowrap",
                    style={"fontVariantNumeric": "tabular-nums",
                           "letterSpacing": "-0.01em", "lineHeight": "1.15"}),
            content=pill["title"]),
        bar,
        *([spark] if spark is not None else []),
        spacing="3", align="start", width="100%",
    )
    empty = rx.text("No data", size=rx.breakpoints(initial="6", lg="7"),
                    weight="medium", color=ds.TEXT_FAINT,
                    style={"lineHeight": "1.15"})
    return rx.link(
        rx.vstack(
            rx.text(title, size="2", color=ds.TEXT_MUTED, weight="medium"),
            rx.cond(pill["reported"] != "", reported, empty),
            spacing="2", align="start", width="100%",
            height="100%", background=ui.CARD_BG,
            border=f"1px solid {ui.CARD_BORDER}", border_radius=ui.RADIUS,
            padding=["1em", "1.1em", "1.25em"],
            style={"transition": "border-color 120ms",
                   "&:hover": {"borderColor": rx.color("slate", 7)}},
        ),
        href=href, underline="none", color="inherit", width="100%",
        height="100%", display="block",
    )


def _spark(ready, dark_fig, light_fig) -> rx.Component:
    """Sparkline once 3 days are reported (a 1-2 day sparkline is only a
    sliver); nothing before that."""
    return rx.cond(
        ready,
        rx.box(rx.cond(O.is_dark, ops_charts.sparkline_chart(dark_fig),
                       ops_charts.sparkline_chart(light_fig)),
               width="100%"),
    )


def _hero_row() -> rx.Component:
    return rx.grid(
        _hero_card("Sales", O.pill_sales, "/ops/sales",
                   spark=_spark(O.spark_sales_ready, O.spark_sales_dark,
                                O.spark_sales_light),
                   bar=_sales_bar(O.pill_sales)),
        _hero_card("Expenses", O.pill_expenses, "/ops/expenses"),
        _hero_card("Salary Ratio", O.pill_manpower, "/ops/manpower"),
        _hero_card("Stock Loss", O.pill_stock, "/ops/stock",
                   spark=_spark(O.spark_stock_ready, O.spark_stock_dark,
                                O.spark_stock_light)),
        columns=rx.breakpoints(initial="1", sm="2", lg="4"),
        spacing="4", width="100%", id="pillar-strip",
    )


def _row(label, value, title="") -> rx.Component:
    """Label left, value right (one line)."""
    return rx.hstack(
        rx.text(label, size="2", color=ds.TEXT_MUTED),
        rx.spacer(),
        rx.text(value, size="2", weight="medium", color=ds.TEXT,
                title=title, white_space="nowrap",
                style={"fontVariantNumeric": "tabular-nums"}),
        align="center", width="100%", spacing="3",
    )


def _target_row(t) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text(t["label"], size="2", weight="medium", color=ds.TEXT),
            rx.spacer(),
            rx.text(t["target"], size="2", color=ds.TEXT_MUTED,
                    title=t["target"],
                    style={"fontVariantNumeric": "tabular-nums"}),
            rx.text(t["pct"], size="2", color=ds.TEXT, weight="medium",
                    style={"fontVariantNumeric": "tabular-nums"}),
            ui.status_chip(t["status"], t["status_color"]),
            align="center", width="100%", spacing="2", wrap="wrap",
        ),
        _track(rx.box(width=(t["progress"].to(float) * 100).to(str) + "%",
                      height="100%",
                      background=rx.color(t["progress_color"], 9),
                      border_radius="3px"), height="4px"),
        spacing="2", width="100%",
    )


def _main_row() -> rx.Component:
    pace = ui.chart_card(
        "Sales Trend",
        rx.cond(O.is_dark,
                ops_charts.chart(O.pace_fig_dark, height="340px"),
                ops_charts.chart(O.pace_fig_light, height="340px")),
        ready=O.pace_has_data,
        id="overview-pace",
    )
    targets = ui.section_card(
        rx.foreach(O.targets, _target_row),
        rx.cond(O.levels_missing != "",
                ui.status_chip(ds.LEVELS_MISSING, "amber",
                               id="overview-levels-missing", wrap=True)),
        # Daily Target / Required Daily Sales (empty once complete)
        rx.cond(O.targets_extra.length() > 0,
                rx.box(border_top=f"1px solid {ui.ROW_BORDER}",
                       width="100%")),
        rx.foreach(O.targets_extra,
                   lambda x: _row(x["label"], x["value"], x["title"])),
        title=O.targets_title,
        id="targets-panel",
    )
    return rx.grid(
        pace, targets,
        columns=rx.breakpoints(initial="1", lg="2fr 1fr"),
        spacing="4", width="100%",
    )


def _attention_item(item) -> rx.Component:
    return rx.link(
        rx.hstack(
            rx.box(width="6px", height="6px", border_radius="50%",
                   background=ds.ERROR, flex_shrink="0"),
            rx.text(item["text"], size="2", color=ds.TEXT),
            rx.spacer(),
            rx.icon("chevron-right", size=14, color=ds.TEXT_FAINT,
                    flex_shrink="0"),
            spacing="3", align="center", width="100%",
            padding_y="0.6em", border_bottom=f"1px solid {ui.ROW_BORDER}",
        ),
        href=item["href"], underline="none", width="100%",
    )


def _attention_panel() -> rx.Component:
    n = O.attention.length()
    return ui.section_card(
        rx.cond(
            n > 0,
            rx.vstack(rx.foreach(O.attention, _attention_item),
                      spacing="0", width="100%"),
            rx.text("None", size="2", color=ds.TEXT_MUTED),
        ),
        title="Alerts",
        extra=ui.status_chip(n.to(str), rx.cond(n > 0, "red", "gray")),
        id="attention-panel", height="100%",
    )


def _glance_item(item) -> rx.Component:
    return rx.link(
        rx.box(_row(item["label"], item["value"], item["title"]),
               padding_y="0.6em", border_bottom=f"1px solid {ui.ROW_BORDER}",
               width="100%"),
        href=item["href"], underline="none", width="100%",
    )


def _glance_panel() -> rx.Component:
    return ui.section_card(
        rx.grid(rx.foreach(O.glance, _glance_item),
                columns=rx.breakpoints(initial="1", sm="2"),
                spacing_x="6", spacing_y="0", width="100%"),
        title="Summary", id="at-a-glance", height="100%",
    )


def _bottom_row() -> rx.Component:
    return rx.grid(
        _attention_panel(), _glance_panel(),
        columns=rx.breakpoints(initial="1", lg="2"),
        spacing="4", width="100%",
    )


# ---------------------------------------------------------------------------
# Monthly report sections
# ---------------------------------------------------------------------------

R = OpsState


def _stat(label, value, **kw) -> rx.Component:
    """Report figure: exact values, a size smaller on phones so two-up
    money ("RM 210,000.00") fits its column."""
    return ui.stat(label, value, size_narrow="5", **kw)


def _mode_controls() -> rx.Component:
    return rx.hstack(
        rx.segmented_control.root(
            rx.segmented_control.item("Live", value="live"),
            rx.segmented_control.item("Finalized", value="finalized"),
            value=R.report_mode,
            on_change=R.select_mode,
            id="report-mode",
        ),
        rx.cond(
            (R.report_mode == "finalized") & (R.versions.length() > 0),
            rx.select(
                R.versions,
                value=R.selected_version_label,
                placeholder="Latest version",
                on_change=R.select_version,
                size="2", id="version-selector",
            ),
        ),
        spacing="3", align="center", wrap="wrap",
    )


def _grid(*stats, columns) -> rx.Component:
    return ui.stat_grid(*stats, columns=columns)


_COLS5 = rx.breakpoints(initial="2", md="5")
_RULE = rx.box(border_top=f"1px solid {ui.ROW_BORDER}", width="100%")


def _sales_section() -> rx.Component:
    # "Projected Sales" only while the month is incomplete (once complete
    # it would repeat Sales)
    sales = R.sales
    complete = sales["projection_state"] == "actual"
    return ui.section_card(
        rx.cond(
            complete,
            _grid(
                _stat("Sales", sales["mtd"], size="6"),
                _stat("Level 1 Target", sales["target_l1"], size="6"),
                _stat("Achievement", sales["achievement_l1"], size="6"),
                columns=rx.breakpoints(initial="2", md="3"),
            ),
            _grid(
                _stat("Sales", sales["mtd"], size="6"),
                _stat("Projected Sales", sales["projected"], size="6"),
                _stat("Level 1 Target", sales["target_l1"], size="6"),
                _stat("Projected Achievement", sales["achievement_l1"],
                      size="6"),
                columns=rx.breakpoints(initial="2", md="4"),
            ),
        ),
        _RULE,
        _grid(
            _stat("SSSG/Year", sales["growth_year"], size="5"),
            _stat("SSSG/Month", sales["growth_month"], size="5"),
            _stat("SPR", sales["spr"], size="5"),
            _stat("UPT", sales["upt"], size="5"),
            _stat("ASP", sales["asp"], size="5"),
            columns=_COLS5,
        ),
        title="Sales",
        id="sales-section",
    )


def _expenses_section() -> rx.Component:
    exp = R.expenses
    return ui.section_card(
        _grid(
            _stat("Paid", exp["paid"], size="6", sub=exp["paid_note"]),
            _stat("Incurred", exp["incurred"], size="6",
                  sub=exp["incurred_note"]),
            _stat("Pending", exp["pending"], size="6"),
            _stat("Budget", exp["budget"], size="6"),
            _stat("Expense Ratio", exp["share"], size="6"),
            columns=_COLS5,
        ),
        rx.cond(
            R.report_category_rows.length() > 0,
            ui.data_table(
                ["Category", ("Paid", True), ("Budget", True), "Status"],
                rx.foreach(R.report_category_rows, lambda c: rx.el.tr(
                    ui.td(rx.text(c["name"], weight="medium")),
                    ui.td(c["paid"], numeric=True),
                    ui.td(c["budget"], numeric=True),
                    ui.td(ui.status_chip(c["status"], c["color"])),
                )),
                phone=("Category", "Paid", "Status"),
            ),
        ),
        title="Expenses",
        id="expenses-section",
    )


def _manpower_section() -> rx.Component:
    mp = R.manpower
    # incomplete month: the ratios cover the days so far
    complete = R.sales["projection_state"] == "actual"
    return ui.section_card(
        _grid(
            _stat("Opening Headcount", mp["opening"], size="6"),
            _stat("Hires", mp["hires"], size="6"),
            _stat("Resignations", mp["resigned"], size="6"),
            _stat("Closing Headcount", mp["closing"], size="6"),
            _stat("Effective Staff", mp["effective_staff"], size="6"),
            columns=_COLS5,
        ),
        _RULE,
        _grid(
            _stat("Salary Cost", mp["salary"], size="6",
                  sub=rx.cond(mp["salary"] == "—",
                              "Awaiting Payroll", "")),
            _stat(rx.cond(complete, "Salary Ratio",
                          "Salary Ratio (To Date)"),
                  mp["ratio"], size="6"),
            _stat(rx.cond(complete, "Total Salary Ratio",
                          "Total Salary Ratio (To Date)"),
                  mp["ratio_support"], size="6"),
            _stat("Turnover", mp["turnover"], size="6"),
            _stat("Leave Days", mp["leave_days"], size="6"),
            columns=_COLS5,
        ),
        title="Manpower",
        id="manpower-section",
    )


def _stock_section() -> rx.Component:
    st = R.stock
    return ui.section_card(
        _grid(
            _stat("Total Loss", st["total_loss"], size="6"),
            _stat("Loss Ratio", st["loss_pct"], size="6"),
            columns=rx.breakpoints(initial="2", md="4"),
        ),
        rx.cond(
            R.report_stock_codes.length() > 0,
            ui.data_table(
                ["Code", ("Total", True), "Treatment"],
                rx.foreach(R.report_stock_codes, lambda c: rx.el.tr(
                    ui.td(rx.text(c["code"], weight="medium")),
                    ui.td(c["total"], numeric=True),
                    ui.td(ui.status_chip(c["treatment"],
                                         c["treatment_color"])),
                )),
            ),
        ),
        title="Stock",
        id="stock-section",
    )


def _actions_section() -> rx.Component:
    act = R.actions
    return ui.section_card(
        _grid(
            _stat("Total", act["total"], size="6"),
            _stat("Open", act["open"], size="6"),
            _stat("In Progress", act["in_progress"], size="6"),
            _stat("Blocked", act["blocked"], size="6"),
            _stat("Completed", act["done"], size="6"),
            _stat("Overdue", act["overdue"], size="6"),
            columns=rx.breakpoints(initial="2", sm="3", lg="6"),
        ),
        title="Actions",
        id="actions-section",
    )


def report_page() -> rx.Component:
    return shell(
        "Monthly Report",
        rx.vstack(
            _mode_controls(),
            rx.cond(R.report_status == "loading",
                    ds.loading_state("Calculating report…")),
            rx.cond(R.report_status == "empty",
                    ui.empty_state("No report available", R.error_message,
                                   icon="file-question")),
            rx.cond(R.report_status == "error",
                    ds.error_callout(R.error_message)),
            rx.cond(
                R.report_status == "ok",
                rx.vstack(
                    rx.hstack(
                        rx.heading(R.report_heading, size="4",
                                   weight="medium", color=ds.TEXT),
                        rx.spacer(),
                        rx.cond(R.finalized_note != "",
                                ui.note(R.finalized_note)),
                        width="100%", align="baseline", wrap="wrap",
                    ),
                    _sales_section(),
                    _expenses_section(),
                    _manpower_section(),
                    _stock_section(),
                    _actions_section(),
                    spacing=ui.GAP, width="100%",
                ),
            ),
            spacing=ui.GAP, width="100%",
        ),
    )
