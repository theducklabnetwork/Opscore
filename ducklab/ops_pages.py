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
                    title="Welcome to DuckLab Operations",
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
                            ui.note(O.header_context),
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
        description="The month at a glance for the selected store: the four "
                    "pillars, sales pace and what needs attention.",
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
    100% width with a "+X%" label when the projection exceeds target."""
    return rx.hstack(
        _track(
            rx.box(width=(pill["mtd_pct"].to(float) * 100).to(str) + "%",
                   height="100%",
                   background=rx.color(pill["progress_color"], 9)),
            rx.box(width=((pill["fill_pct"].to(float)
                           - pill["mtd_pct"].to(float)) * 100).to(str) + "%",
                   height="100%",
                   background=rx.color(pill["progress_color"], 9),
                   opacity="0.35"),
            flex="1",
        ),
        rx.cond(pill["over_label"] != "",
                rx.text(pill["over_label"], size="1", color=ds.TEXT_FAINT,
                        white_space="nowrap")),
        spacing="2", align="center", width="100%",
    )


def _pillar_chip(pill) -> rx.Component:
    chip = ui.status_chip(pill["status"], pill["status_color"])
    return rx.hstack(
        rx.cond(pill["tooltip"] != "", rx.tooltip(chip,
                                                  content=pill["tooltip"]),
                chip),
        ui.early_tag(pill["early_tag"], pill["early_tip"]),
        spacing="1", wrap="wrap",
    )


def _hero_card(title, pill, href, spark=None, bar=None) -> rx.Component:
    """One pillar tile: label, status chip, big value, one visual, one
    context line; a compact "Not reported yet" state when empty. The
    whole tile links to its module."""
    if bar is None:
        bar = _plain_bar(pill)
    reported = rx.vstack(
        _pillar_chip(pill),
        rx.tooltip(
            rx.text(pill["value"], size=rx.breakpoints(initial="6", lg="7"),
                    weight="medium", color=ds.TEXT, white_space="nowrap",
                    style={"fontVariantNumeric": "tabular-nums",
                           "letterSpacing": "-0.01em", "lineHeight": "1.15"}),
            content=pill["title"]),
        bar,
        *([spark] if spark is not None else []),
        rx.text(pill["context"], size="1", color=ds.TEXT_FAINT),
        spacing="2", align="start", width="100%",
    )
    empty = rx.vstack(
        rx.text("Not reported yet", size="2", color=ds.TEXT_MUTED,
                weight="medium"),
        ui.note("Open this module to enter data"),
        spacing="1", align="start", width="100%",
    )
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
    """Sparkline once 3 days are reported; before that a small note
    (a 1-2 day sparkline is only a sliver)."""
    return rx.cond(
        ready,
        rx.box(rx.cond(O.is_dark, ops_charts.sparkline_chart(dark_fig),
                       ops_charts.sparkline_chart(light_fig)),
               width="100%"),
        rx.hstack(rx.icon("chart-line", size=14, color=ds.TEXT_FAINT),
                  ui.note("Not enough data yet"),
                  spacing="2", align="center", padding_y="0.25em"),
    )


def _hero_row() -> rx.Component:
    return rx.grid(
        _hero_card("Sales MTD", O.pill_sales, "/ops/sales",
                   spark=_spark(O.spark_sales_ready, O.spark_sales_dark,
                                O.spark_sales_light),
                   bar=_sales_bar(O.pill_sales)),
        _hero_card("Expenses paid MTD", O.pill_expenses, "/ops/expenses"),
        _hero_card("Manpower — salary ratio", O.pill_manpower,
                   "/ops/manpower"),
        _hero_card("Stock loss % of sales", O.pill_stock, "/ops/stock",
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
        "Pace vs Level Targets",
        rx.cond(O.is_dark,
                ops_charts.chart(O.pace_fig_dark, height="340px"),
                ops_charts.chart(O.pace_fig_light, height="340px")),
        ready=O.pace_has_data,
        subtitle="Cumulative sales (RM) against each level's daily "
                 "run-rate (Level 1 bold)",
        detail="Cumulative actuals stop at the last reported day; "
               "unreported days are never interpolated.",
        id="overview-pace",
    )
    targets = ui.section_card(
        rx.foreach(O.targets, _target_row),
        rx.cond(O.levels_missing != "",
                ui.status_chip(ds.LEVELS_MISSING, "amber",
                               id="overview-levels-missing", wrap=True)),
        rx.box(border_top=f"1px solid {ui.ROW_BORDER}", width="100%"),
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
            rx.text("Nothing needs attention", size="2",
                    color=ds.TEXT_MUTED),
        ),
        title="Needs attention",
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
        title="At a glance", id="at-a-glance", height="100%",
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
        rx.cond(
            R.report_mode == "live",
            ui.status_chip("LIVE — recalculated from current records",
                           "gray"),
            ui.status_chip("FINALIZED — preserved snapshot", "violet"),
        ),
        spacing="3", align="center", wrap="wrap",
    )


def _grid(*stats, columns) -> rx.Component:
    return ui.stat_grid(*stats, columns=columns)


_COLS5 = rx.breakpoints(initial="2", md="5")


def _proj_label(label: str):
    """Projection-derived figure label: "(projected)" until the month is
    complete; the early-estimate state shows once, as the section chip."""
    return rx.cond(R.sales["projection_state"] == "actual", label,
                   label + " (projected)")


def _sales_section() -> rx.Component:
    sales = R.sales
    return ui.section_card(
        _grid(
            _stat("Monthly sales (MTD)", sales["mtd"], size="6"),
            _stat(rx.cond(sales["projection_state"] == "actual",
                            "Month-end sales (actual)",
                            "Projected month-end"),
                    sales["projected"], size="6"),
            _stat("Level 1 target", sales["target_l1"], size="6"),
            _stat(_proj_label("Achievement vs target"),
                    sales["achievement_l1"], size="6"),
            columns=rx.breakpoints(initial="2", md="4"),
        ),
        rx.box(border_top=f"1px solid {ui.ROW_BORDER}", width="100%"),
        _grid(
            _stat(_proj_label("Growth vs last year"),
                    sales["growth_year"], size="5"),
            _stat(_proj_label("Growth vs last month"),
                    sales["growth_month"], size="5"),
            _stat("SPR", sales["spr"], size="5", sub="Sales per receipt"),
            _stat("UPT", sales["upt"], size="5",
                    sub="Units per transaction"),
            _stat("ASP", sales["asp"], size="5",
                    sub="Average selling price"),
            columns=_COLS5,
        ),
        title="Sales performance",
        subtitle=sales["completeness"],
        extra=rx.hstack(
            ui.status_chip(sales["status_l1"], R.report_colors["sales"]),
            rx.cond(sales["projection_state"] == "early_estimate",
                    ui.early_tag("Early estimate",
                                 sales["early_estimate_tag"])),
            spacing="1", align="center",
        ),
        id="sales-section",
    )


def _expenses_section() -> rx.Component:
    exp = R.expenses
    return ui.section_card(
        _grid(
            _stat("Paid expenses", exp["paid"], size="6",
                    sub=exp["paid_note"]),
            _stat("Incurred expenses", exp["incurred"], size="6",
                    sub=exp["incurred_note"]),
            _stat("Pending / open", exp["pending"], size="6"),
            _stat("Total budget", exp["budget"], size="6"),
            _stat("Share of sales", exp["share"], size="6"),
            columns=_COLS5,
        ),
        rx.cond(
            R.report_category_rows.length() > 0,
            ui.data_table(
                ["Category", ("Paid (MTD)", True), ("Budget", True),
                 "Status"],
                rx.foreach(R.report_category_rows, lambda c: rx.el.tr(
                    ui.td(rx.text(c["name"], weight="medium")),
                    ui.td(c["paid"], numeric=True),
                    ui.td(c["budget"], numeric=True),
                    ui.td(ui.status_chip(c["status"], c["color"])),
                )),
                phone=("Category", "Paid (MTD)", "Status"),
            ),
        ),
        title="Expenses management",
        extra=rx.cond(exp["hq_status"] != "",
                      ui.status_chip(exp["hq_status"],
                                     R.report_colors["expenses"])),
        id="expenses-section",
    )


def _manpower_section() -> rx.Component:
    mp = R.manpower
    return ui.section_card(
        _grid(
            _stat("Opening headcount", mp["opening"], size="6"),
            _stat("Hires", mp["hires"], size="6"),
            _stat("Resigned", mp["resigned"], size="6"),
            _stat("Closing headcount", mp["closing"], size="6"),
            _stat("Effective staff", mp["effective_staff"], size="6",
                    sub=mp["pt_factor_label"]),
            columns=_COLS5,
        ),
        rx.box(border_top=f"1px solid {ui.ROW_BORDER}", width="100%"),
        _grid(
            _stat("Salary cost", mp["salary"], size="6",
                    sub=rx.cond(mp["salary"] == "—",
                                "Awaiting payroll", "")),
            _stat("Salary ratio", mp["ratio"], size="6"),
            _stat("Ratio incl. support", mp["ratio_support"], size="6"),
            _stat("Turnover", mp["turnover"], size="6",
                    sub=mp["turnover_status"]),
            _stat("Leave days", mp["leave_days"], size="6"),
            columns=_COLS5,
        ),
        rx.cond((mp["ratio_mtd"] == "1") & (mp["ratio"] != "Restricted"),
                ui.note("Month to date: the ratio uses sales entered so far "
                        "and is only final once the month's sales are "
                        "complete.")),
        title="Manpower efficiency",
        extra=ui.status_chip(mp["ratio_status"],
                             R.report_colors["manpower"]),
        id="manpower-section",
    )


def _stock_section() -> rx.Component:
    st = R.stock
    return ui.section_card(
        _grid(
            _stat("Total loss", st["total_loss"], size="6"),
            _stat("Loss % of sales", st["loss_pct"], size="6"),
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
        title="Stock management",
        extra=ui.status_chip(st["status"], R.report_colors["stock"]),
        id="stock-section",
    )


def _actions_section() -> rx.Component:
    act = R.actions
    return ui.section_card(
        _grid(
            _stat("Total actions", act["total"], size="6"),
            _stat("Open", act["open"], size="6"),
            _stat("In progress", act["in_progress"], size="6"),
            _stat("Blocked", act["blocked"], size="6"),
            _stat("Completed", act["done"], size="6"),
            _stat("Overdue", act["overdue"], size="6"),
            columns=rx.breakpoints(initial="2", sm="3", lg="6"),
        ),
        title="Action tracker summary",
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
        description="The month's figures for the selected store: Live "
                    "recalculates from current records; Finalized shows a "
                    "preserved snapshot.",
    )
