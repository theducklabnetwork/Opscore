"""DuckLab operations pages: dashboard landing + monthly report."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab.auth_pages import AuthState
from ducklab.ops_overview_state import OverviewState
from ducklab.ops_shell import shell
from ducklab.ops_state import OpsState


def dashboard_page() -> rx.Component:
    return shell(
        "Dashboard",
        rx.vstack(
            rx.cond(
                ~OpsState.has_stores,
                rx.card(
                    rx.vstack(
                        rx.heading("Welcome to DuckLab Operations", size="5"),
                        rx.text(
                            "No stores are assigned to your account. Ask an "
                            "administrator to assign you a store to see the "
                            "overview.",
                            size="2", color=ds.TEXT_MUTED,
                        ),
                        rx.link(
                            rx.button(rx.icon("file-chart-column", size=16),
                                      "Open Monthly Report",
                                      color_scheme="indigo"),
                            href="/ops/report",
                        ),
                        spacing="3", align="start",
                    ),
                    width="100%",
                ),
            ),
            rx.cond(
                OpsState.has_stores,
                rx.vstack(
                    rx.cond(
                        OverviewState.error != "",
                        ds.error_callout(OverviewState.error),
                    ),
                    rx.cond(
                        OverviewState.finalized_notice != "",
                        rx.callout(OverviewState.finalized_notice,
                                   icon="lock", color_scheme="violet",
                                   variant="soft", width="100%"),
                    ),
                    rx.cond(
                        OverviewState.loaded,
                        rx.vstack(
                            rx.text(OverviewState.header_context, size="2",
                                    color=ds.TEXT_MUTED),
                            _hero_row(),
                            _main_row(),
                            _bottom_row(),
                            spacing="4", width="100%",
                        ),
                    ),
                    spacing="3", width="100%",
                ),
            ),
            width="100%",
        ),
        on_store=OverviewState.select_store,
        on_month=OverviewState.select_month,
    )


# ---------------------------------------------------------------------------
# Overview sections (values pre-formatted by OverviewState; each figure
# appears exactly once on the page)
# ---------------------------------------------------------------------------

def _hero_card(icon, title, pill, href, spark=None) -> rx.Component:
    """One pillar hero card: label, big value, progress bar, status chip,
    one context line; compact "Not reported yet" state when empty."""
    reported = pill["reported"] != ""
    body_reported = rx.vstack(
        rx.hstack(
            rx.heading(pill["value"], size="7", weight="bold",
                       title=pill["title"]),
            rx.spacer(),
            rx.badge(pill["status"], color_scheme=pill["status_color"],
                     variant="soft", size="1"),
            align="center", width="100%",
        ),
        rx.progress(value=pill["progress"].to(int), max=1,
                    color_scheme=pill["progress_color"],
                    width="100%", height="6px"),
        rx.cond(
            spark is not None,
            spark,
            rx.text(pill["context"], size="1", color=ds.TEXT_FAINT),
        ),
        spacing="2", align="start", width="100%",
    )
    body_empty = rx.vstack(
        rx.spacer(),
        rx.text("Not reported yet", size="2", color=ds.TEXT_MUTED,
                weight="medium"),
        rx.text("Open this module to enter data", size="1",
                color=ds.TEXT_FAINT),
        rx.spacer(),
        spacing="1", align="start", width="100%",
    )
    return rx.link(
        rx.card(
            rx.vstack(
                rx.hstack(rx.icon(icon, size=16, color=ds.TEXT_MUTED),
                          rx.text(title, size="1", color=ds.TEXT_MUTED,
                                  weight="medium"),
                          spacing="2", align="center"),
                rx.cond(reported, body_reported, body_empty),
                spacing="2", align="start", width="100%",
            ),
            width="100%", height="100%",
        ),
        href=href, width="100%",
    )


def _hero_row() -> rx.Component:
    sales_spark = rx.vstack(
        rx.cond(OverviewState.is_dark,
                ops_charts.sparkline_chart(OverviewState.spark_sales_dark),
                ops_charts.sparkline_chart(OverviewState.spark_sales_light)),
        rx.text(OverviewState.pill_sales["context"], size="1",
                color=ds.TEXT_FAINT),
        spacing="1", width="100%",
    )
    stock_spark = rx.vstack(
        rx.cond(OverviewState.is_dark,
                ops_charts.sparkline_chart(OverviewState.spark_stock_dark),
                ops_charts.sparkline_chart(OverviewState.spark_stock_light)),
        rx.text(OverviewState.pill_stock["context"], size="1",
                color=ds.TEXT_FAINT),
        spacing="1", width="100%",
    )
    return rx.grid(
        _hero_card("trending-up", "Sales MTD", OverviewState.pill_sales,
                   "/ops/sales", spark=sales_spark),
        _hero_card("receipt", "Expenses paid MTD",
                   OverviewState.pill_expenses, "/ops/expenses"),
        _hero_card("users", "Manpower — salary ratio",
                   OverviewState.pill_manpower, "/ops/manpower"),
        _hero_card("boxes", "Stock loss % of sales",
                   OverviewState.pill_stock, "/ops/stock",
                   spark=stock_spark),
        columns=rx.breakpoints(initial="1", sm="2", lg="4"),
        spacing="3", width="100%", id="pillar-strip",
    )


def _target_row(t) -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.text(t["label"], size="1", color=ds.TEXT_MUTED,
                    weight="medium"),
            rx.spacer(),
            rx.text(t["target"], size="1", color=ds.TEXT_FAINT),
            rx.text(" · ", size="1", color=ds.TEXT_FAINT),
            rx.text(t["pct"], size="1", color=ds.TEXT_FAINT),
            rx.badge(t["status"], color_scheme=t["status_color"],
                     variant="soft", size="1"),
            align="center", width="100%",
        ),
        rx.progress(value=t["progress"], max=1,
                    color_scheme=t["progress_color"],
                    width="100%", height="4px"),
        spacing="1", width="100%",
    )


def _main_row() -> rx.Component:
    chart_card = rx.card(
        rx.vstack(
            ds.section_heading("Pace vs Level 1 Budget", "chart-line"),
            rx.cond(
                OverviewState.pace_has_data,
                rx.cond(
                    OverviewState.is_dark,
                    ops_charts.chart(OverviewState.pace_fig_dark,
                                     height="360px"),
                    ops_charts.chart(OverviewState.pace_fig_light,
                                     height="360px")),
                ds.empty_state(
                    "Awaiting data",
                    "Cumulative actuals stop at the last reported day; "
                    "unreported days are never interpolated.",
                    icon="chart-line"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="overview-pace",
    )
    targets_card = rx.card(
        rx.vstack(
            ds.section_heading("Sales Targets", "target"),
            rx.foreach(OverviewState.targets, _target_row),
            spacing="3", width="100%",
        ),
        width="100%", id="targets-panel",
    )
    return rx.grid(
        chart_card, targets_card,
        columns=rx.breakpoints(initial="1", lg="2fr 1fr"),
        spacing="3", width="100%",
    )


def _attention_panel() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                ds.section_heading("Needs Attention", "flag"),
                rx.spacer(),
                rx.badge(OverviewState.attention.length(), color_scheme="red",
                         variant="soft", size="1"),
                align="center", width="100%",
            ),
            rx.cond(
                OverviewState.attention.length() > 0,
                rx.vstack(
                    rx.foreach(
                        OverviewState.attention,
                        lambda item: rx.link(
                            rx.hstack(
                                rx.icon(item["icon"], size=16,
                                        color=ds.ERROR),
                                rx.text(item["text"], size="2"),
                                spacing="2", align="center"),
                            href=item["href"],
                        )),
                    spacing="2", align="start", width="100%"),
                rx.hstack(
                    rx.icon("circle-check", size=16, color=ds.SUCCESS),
                    rx.text("Nothing needs attention", size="2",
                            color=ds.TEXT_MUTED),
                    spacing="2", align="center"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", height="100%", id="attention-panel",
    )


def _glance_item(item) -> rx.Component:
    return rx.link(
        rx.hstack(
            rx.text(item["label"], size="1", color=ds.TEXT_MUTED),
            rx.spacer(),
            rx.text(item["value"], size="1", weight="medium",
                    title=item["title"]),
            align="center", width="100%",
        ),
        href=item["href"], width="100%",
    )


def _glance_panel() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("At a Glance", "gauge"),
            rx.grid(
                rx.foreach(OverviewState.glance, _glance_item),
                columns=rx.breakpoints(initial="1", sm="2"),
                spacing="2", width="100%",
            ),
            spacing="3", width="100%",
        ),
        width="100%", height="100%", id="at-a-glance",
    )


def _bottom_row() -> rx.Component:
    return rx.grid(
        _attention_panel(), _glance_panel(),
        columns=rx.breakpoints(initial="1", lg="2"),
        spacing="3", width="100%",
    )


# ---------------------------------------------------------------------------
# Monthly report sections
# ---------------------------------------------------------------------------

def _mode_controls() -> rx.Component:
    return rx.hstack(
        rx.segmented_control.root(
            rx.segmented_control.item("Live", value="live"),
            rx.segmented_control.item("Finalized", value="finalized"),
            value=OpsState.report_mode,
            on_change=OpsState.select_mode,
            id="report-mode",
        ),
        rx.cond(
            (OpsState.report_mode == "finalized") & (OpsState.versions.length() > 0),
            rx.select(
                OpsState.versions,
                value=OpsState.selected_version_label,
                placeholder="Latest version",
                on_change=OpsState.select_version,
                size="2", id="version-selector",
            ),
        ),
        rx.cond(
            OpsState.report_mode == "live",
            rx.badge("LIVE — recalculated from current records",
                     color_scheme="indigo", variant="soft"),
            rx.badge("FINALIZED — preserved snapshot",
                     color_scheme="green", variant="soft"),
        ),
        spacing="3", align="center", wrap="wrap",
    )


def _sales_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Sales Performance", "trending-up",
                extra=rx.hstack(
                    ds.status_badge(OpsState.sales["status_l1"]),
                    rx.text(OpsState.sales["completeness"],
                            size="1", color=ds.TEXT_FAINT),
                    spacing="2",
                ),
            ),
            rx.grid(
                ds.kpi_card("Monthly Sales (MTD)",
                            OpsState.sales["mtd"],
                            icon="banknote"),
                ds.kpi_card("Projected Month-End",
                            OpsState.sales["projected"],
                            icon="chart-line"),
                ds.kpi_card("Level 1 Target",
                            OpsState.sales["target_l1"],
                            icon="target"),
                ds.kpi_card("Achievement vs Target",
                            OpsState.sales["achievement_l1"],
                            icon="percent"),
                columns=rx.breakpoints(initial="1", sm="2", md="4"),
                spacing="3", width="100%",
            ),
            rx.grid(
                ds.kpi_card("Growth vs Last Year",
                            OpsState.sales["growth_year"]),
                ds.kpi_card("Growth vs Last Month",
                            OpsState.sales["growth_month"]),
                ds.kpi_card("SPR", OpsState.sales["spr"],
                            sub="Sales per receipt"),
                ds.kpi_card("UPT", OpsState.sales["upt"],
                            sub="Units per transaction"),
                ds.kpi_card("ASP", OpsState.sales["asp"],
                            sub="Average selling price"),
                columns=rx.breakpoints(initial="1", sm="2", md="5"),
                spacing="3", width="100%",
            ),
            spacing="4", width="100%",
        ),
        width="100%", id="sales-section",
    )


def _expenses_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Expenses Management", "receipt"),
            rx.grid(
                ds.kpi_card("Paid Expenses",
                            OpsState.expenses["paid"], icon="check"),
                ds.kpi_card("Incurred Expenses",
                            OpsState.expenses["incurred"],
                            icon="calendar"),
                ds.kpi_card("Pending / Open",
                            OpsState.expenses["pending"],
                            icon="clock"),
                ds.kpi_card("Total Budget",
                            OpsState.expenses["budget"],
                            icon="wallet"),
                ds.kpi_card("Share of Sales",
                            OpsState.expenses["share"],
                            icon="percent"),
                columns=rx.breakpoints(initial="1", sm="2", md="5"),
                spacing="3", width="100%",
            ),
            rx.cond(
                OpsState.report_categories.length() > 0,
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Category"),
                            rx.table.column_header_cell("Paid (MTD)"),
                            rx.table.column_header_cell("Budget"),
                            rx.table.column_header_cell("Status"),
                        )
                    ),
                    rx.table.body(
                        rx.foreach(
                            OpsState.report_categories,
                            lambda c: rx.table.row(
                                rx.table.cell(c["name"]),
                                rx.table.cell(c["paid"]),
                                rx.table.cell(c["budget"]),
                                rx.table.cell(ds.status_badge(c["status"])),
                            ),
                        )
                    ),
                    width="100%", variant="surface",
                ),
            ),
            spacing="4", width="100%",
        ),
        width="100%", id="expenses-section",
    )


def _manpower_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Manpower Efficiency", "users",
                extra=ds.status_badge(
                    OpsState.manpower["ratio_status"]),
            ),
            rx.grid(
                ds.kpi_card("Opening Headcount",
                            OpsState.manpower["opening"]),
                ds.kpi_card("Hires", OpsState.manpower["hires"]),
                ds.kpi_card("Resigned",
                            OpsState.manpower["resigned"]),
                ds.kpi_card("Closing Headcount",
                            OpsState.manpower["closing"]),
                ds.kpi_card("Effective Staff",
                            OpsState.manpower["effective_staff"],
                            sub="FT + 0.5 × PT"),
                columns=rx.breakpoints(initial="1", sm="2", md="5"),
                spacing="3", width="100%",
            ),
            rx.grid(
                ds.kpi_card("Salary Cost",
                            OpsState.manpower["salary"],
                            icon="wallet",
                            sub="— = awaiting payroll"),
                ds.kpi_card("Salary Ratio",
                            OpsState.manpower["ratio"]),
                ds.kpi_card("Ratio incl. Support",
                            OpsState.manpower["ratio_support"]),
                ds.kpi_card("Turnover",
                            OpsState.manpower["turnover"],
                            sub=OpsState.manpower["turnover_status"]),
                ds.kpi_card("Leave Days",
                            OpsState.manpower["leave_days"]),
                columns=rx.breakpoints(initial="1", sm="2", md="5"),
                spacing="3", width="100%",
            ),
            spacing="4", width="100%",
        ),
        width="100%", id="manpower-section",
    )


def _stock_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Stock Management", "boxes",
                extra=ds.status_badge(OpsState.stock["status"]),
            ),
            rx.grid(
                ds.kpi_card("Total Loss",
                            OpsState.stock["total_loss"],
                            icon="triangle-alert", accent="red"),
                ds.kpi_card("Loss % of Sales",
                            OpsState.stock["loss_pct"]),
                columns=rx.breakpoints(initial="1", sm="2", md="4"),
                spacing="3", width="100%",
            ),
            rx.cond(
                OpsState.report_stock_codes.length() > 0,
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Code"),
                            rx.table.column_header_cell("Total"),
                            rx.table.column_header_cell("Counts toward loss"),
                        )
                    ),
                    rx.table.body(
                        rx.foreach(
                            OpsState.report_stock_codes,
                            lambda c: rx.table.row(
                                rx.table.cell(c["code"]),
                                rx.table.cell(c["total"]),
                                rx.table.cell(
                                    rx.cond(c["counts"] == "1",
                                            ds.status_badge("included"),
                                            ds.status_badge("informational")),
                                ),
                            ),
                        )
                    ),
                    width="100%", variant="surface",
                ),
            ),
            spacing="4", width="100%",
        ),
        width="100%", id="stock-section",
    )


def _actions_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Action Tracker Summary", "list-checks"),
            rx.grid(
                ds.kpi_card("Total Actions",
                            OpsState.actions["total"],
                            icon="list"),
                ds.kpi_card("Open", OpsState.actions["open"],
                            accent="indigo"),
                ds.kpi_card("In Progress",
                            OpsState.actions["in_progress"],
                            accent="amber"),
                ds.kpi_card("Blocked",
                            OpsState.actions["blocked"],
                            accent="red"),
                ds.kpi_card("Completed",
                            OpsState.actions["done"],
                            accent="green"),
                ds.kpi_card("Overdue",
                            OpsState.actions["overdue"],
                            accent="red"),
                columns=rx.breakpoints(initial="1", sm="2", md="6"),
                spacing="3", width="100%",
            ),
            spacing="4", width="100%",
        ),
        width="100%", id="actions-section",
    )


def report_page() -> rx.Component:
    return shell(
        "Monthly Report",
        rx.vstack(
            _mode_controls(),
            rx.cond(
                OpsState.report_status == "loading",
                ds.loading_state("Calculating report…"),
            ),
            rx.cond(
                OpsState.report_status == "empty",
                ds.empty_state("No report available", OpsState.error_message,
                               icon="file-question"),
            ),
            rx.cond(
                OpsState.report_status == "error",
                ds.error_callout(OpsState.error_message),
            ),
            rx.cond(
                OpsState.report_status == "ok",
                rx.vstack(
                    rx.hstack(
                        rx.heading(OpsState.report_heading, size="4"),
                        rx.spacer(),
                        rx.cond(OpsState.finalized_note != "",
                                rx.text(OpsState.finalized_note, size="1",
                                        color=ds.TEXT_FAINT)),
                        wrap="wrap",
                    ),
                    _sales_section(),
                    _expenses_section(),
                    _manpower_section(),
                    _stock_section(),
                    _actions_section(),
                    spacing="5", width="100%",
                ),
            ),
            spacing="5", width="100%",
        ),
    )
