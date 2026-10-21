"""Manpower Efficiency page: workforce KPIs, headcount monitoring, salary
ratio monitoring, daily staffing overview, leave monitoring, and the
monthly configuration/payroll editor."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab.ops_manpower_state import ManpowerState
from ducklab.ops_shell import shell


# ---------------------------------------------------------------------------
# KPI sections
# ---------------------------------------------------------------------------

def _headcount_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Headcount Monitoring", "users",
                extra=rx.badge(ManpowerState.hc_kpis["status"],
                               color_scheme=ManpowerState.hc_kpis["status_color"],
                               variant="soft", size="2", id="hc-status")),
            rx.grid(
                ds.kpi_card("Opening Headcount",
                            ManpowerState.hc_kpis["opening"], icon="log-in"),
                ds.kpi_card("New Hires", ManpowerState.hc_kpis["hires"],
                            icon="user-plus", accent="green"),
                ds.kpi_card("Resignations",
                            ManpowerState.hc_kpis["resigned"],
                            icon="user-minus", accent="red"),
                ds.kpi_card("Closing Headcount",
                            ManpowerState.hc_kpis["closing"], icon="log-out"),
                ds.kpi_card("Budgeted Headcount",
                            ManpowerState.hc_kpis["budget"], icon="target"),
                ds.kpi_card("Headcount Variance",
                            ManpowerState.hc_kpis["variance"],
                            sub="Closing − budget; Vacancy / On budget / Over budget"),
                columns=rx.breakpoints(initial="2", sm="3", lg="6"),
                spacing="3", width="100%",
            ),
            rx.cond(
                ManpowerState.hc_kpis["status"] == "Vacancy",
                rx.callout(
                    "Vacancy: closing headcount is below the budgeted "
                    "headcount for this store/month. Review hiring progress "
                    "in Daily Operations.",
                    icon="triangle-alert", color_scheme="amber",
                    variant="soft", width="100%", id="vacancy-warning"),
            ),
            rx.cond(
                ManpowerState.hc_kpis["status"] == "Over budget",
                rx.callout(
                    "Over budget: closing headcount exceeds the configured "
                    "budget headcount.",
                    icon="triangle-alert", color_scheme="red",
                    variant="soft", width="100%"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="headcount-section",
    )


def _efficiency_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Staffing Efficiency & Turnover", "gauge",
                extra=rx.badge(ManpowerState.eff_kpis["turnover_status"],
                               color_scheme=ManpowerState.eff_kpis["turnover_color"],
                               variant="soft", size="2")),
            rx.grid(
                ds.kpi_card("Avg Effective Working Staff",
                            ManpowerState.eff_kpis["avg_effective"],
                            icon="calculator",
                            sub="PT factor: " + ManpowerState.pt_factor_label),
                ds.kpi_card("Sales per Effective Staff",
                            ManpowerState.eff_kpis["sales_per_staff"],
                            icon="banknote"),
                ds.kpi_card("Employee Turnover",
                            ManpowerState.eff_kpis["turnover"],
                            icon="repeat",
                            sub="Resignations ÷ opening headcount"),
                ds.kpi_card("Turnover Threshold",
                            ManpowerState.eff_kpis["threshold"],
                            icon="sliders-horizontal"),
                columns=rx.breakpoints(initial="2", sm="4"),
                spacing="3", width="100%",
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="efficiency-section",
    )


def _payroll_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Salary Ratio & Payroll Monitoring", "wallet",
                extra=rx.badge(ManpowerState.pay_kpis["status"],
                               color_scheme=ManpowerState.pay_kpis["status_color"],
                               variant="soft", size="2", id="ratio-status")),
            rx.cond(
                ManpowerState.can_view_payroll,
                rx.vstack(
                    rx.grid(
                        ds.kpi_card("Basic Salary + Allowances",
                                    ManpowerState.pay_kpis["basic"],
                                    icon="banknote"),
                        ds.kpi_card("Overtime",
                                    ManpowerState.pay_kpis["overtime"],
                                    icon="clock"),
                        ds.kpi_card("Total Salary Cost",
                                    ManpowerState.pay_kpis["total"],
                                    icon="wallet"),
                        ds.kpi_card("Support Team Cost",
                                    ManpowerState.pay_kpis["support"],
                                    icon="handshake"),
                        ds.kpi_card("Actual Sales (MTD)",
                                    ManpowerState.pay_kpis["sales"],
                                    icon="trending-up"),
                        columns=rx.breakpoints(initial="2", sm="3", lg="5"),
                        spacing="3", width="100%",
                    ),
                    rx.grid(
                        ds.kpi_card("Salary Ratio",
                                    ManpowerState.pay_kpis["ratio"],
                                    icon="percent"),
                        ds.kpi_card("Ratio incl. Support",
                                    ManpowerState.pay_kpis["ratio_support"],
                                    icon="percent"),
                        ds.kpi_card("Ratio Target",
                                    ManpowerState.pay_kpis["target"],
                                    icon="target"),
                        ds.kpi_card("Salary Budget",
                                    ManpowerState.pay_kpis["budget"],
                                    icon="scale"),
                        ds.kpi_card("Budget Variance",
                                    ManpowerState.pay_kpis["variance"],
                                    sub="Budget − actual salary cost"),
                        columns=rx.breakpoints(initial="2", sm="3", lg="5"),
                        spacing="3", width="100%",
                    ),
                    rx.vstack(
                        rx.hstack(
                            rx.text("Ratio utilization vs target", size="1",
                                    color=ds.TEXT_MUTED),
                            rx.spacer(),
                            rx.text(ManpowerState.pay_kpis["ratio_support"],
                                    size="1", weight="bold"),
                            width="100%",
                        ),
                        rx.box(
                            rx.box(
                                width=ManpowerState.pay_kpis["ratio_bar"] + "%",
                                height="100%",
                                background=ds.ACCENT,
                                border_radius="4px",
                            ),
                            width="100%", height="10px",
                            background=ds.BORDER, border_radius="4px",
                            overflow="hidden",
                        ),
                        spacing="1", width="100%",
                    ),
                    rx.cond(
                        ManpowerState.pay_kpis["status"] == "Awaiting payroll",
                        rx.callout(
                            "Awaiting payroll: no basic salary has been "
                            "recorded for this store/month. Missing payroll "
                            "is not RM 0 — enter month-end payroll below.",
                            icon="clock", color_scheme="gray", variant="soft",
                            width="100%", id="awaiting-payroll"),
                    ),
                    spacing="3", width="100%",
                ),
                rx.callout(
                    "Payroll figures are restricted to administrators. Your "
                    "role can monitor headcount, staffing and turnover, but "
                    "salary values are never sent to your session.",
                    icon="lock", color_scheme="gray", variant="soft",
                    width="100%", id="payroll-restricted"),
            ),
            rx.cond(
                ManpowerState.can_manage_manpower,
                rx.hstack(
                    rx.button(rx.icon("settings-2", size=14),
                              "Edit Monthly Configuration & Payroll",
                              color_scheme="indigo", variant="soft",
                              on_click=ManpowerState.open_config,
                              id="open-config"),
                ),
            ),
            rx.cond(
                ManpowerState.mp_finalized,
                rx.callout(
                    "A finalized snapshot exists for this month. Editing "
                    "configuration or payroll updates live results only; "
                    "preserved snapshots are never modified.",
                    icon="archive", color_scheme="violet", variant="soft",
                    width="100%"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="payroll-section",
    )


def _productivity_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Staffing Productivity", "chart-line"),
            rx.grid(
                ds.kpi_card("Monthly Sales (MTD)",
                            ManpowerState.prod_kpis["sales_mtd"],
                            icon="banknote"),
                ds.kpi_card("Avg Effective Working Staff",
                            ManpowerState.prod_kpis["avg_effective"]),
                ds.kpi_card("Sales per Effective Staff",
                            ManpowerState.prod_kpis["sales_per_staff"],
                            icon="trending-up"),
                ds.kpi_card("Reporting Completeness",
                            ManpowerState.prod_kpis["completeness"],
                            icon="calendar-check"),
                columns=rx.breakpoints(initial="2", sm="4"),
                spacing="3", width="100%",
            ),
            rx.text(
                "Sales per effective staff uses sales on staffed days ÷ "
                "effective staff-days; unstaffed or unreported days never "
                "distort the average.",
                size="1", color=ds.TEXT_FAINT),
            spacing="3", width="100%",
        ),
        width="100%", id="productivity-section",
    )


def _leave_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Leave Monitoring", "calendar-off"),
            rx.grid(
                ds.kpi_card("Annual Leave",
                            ManpowerState.leave_kpis["annual"], icon="sun"),
                ds.kpi_card("Medical Leave (MC)",
                            ManpowerState.leave_kpis["mc"], icon="stethoscope"),
                ds.kpi_card("Other Paid Leave",
                            ManpowerState.leave_kpis["other_paid"],
                            icon="calendar-plus"),
                ds.kpi_card("Unpaid Leave",
                            ManpowerState.leave_kpis["unpaid"],
                            icon="calendar-x"),
                ds.kpi_card("Total Leave Days",
                            ManpowerState.leave_kpis["total"], icon="sigma"),
                columns=rx.breakpoints(initial="2", sm="3", lg="5"),
                spacing="3", width="100%",
            ),
            rx.text(
                "Aggregated daily leave counts from Daily Operations. The "
                "workbook tracks store-level totals, not individual employee "
                "leave applications.",
                size="1", color=ds.TEXT_FAINT),
            spacing="3", width="100%",
        ),
        width="100%", id="leave-section",
    )


# ---------------------------------------------------------------------------
# Daily staffing table
# ---------------------------------------------------------------------------

def _staff_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.hstack(rx.text(row["date"], weight="bold"),
                      rx.text(row["weekday"], size="1", color=ds.TEXT_FAINT),
                      spacing="2", align="baseline"),
            white_space="nowrap"),
        rx.table.cell(row["ft"]),
        rx.table.cell(row["pt"]),
        rx.table.cell(row["effective"], weight="bold"),
        rx.table.cell(row["hires"]),
        rx.table.cell(row["resigned"]),
        rx.table.cell(row["leave"]),
        rx.table.cell(row["sales"], white_space="nowrap"),
        rx.table.cell(row["sales_per_staff"], white_space="nowrap"),
        rx.table.cell(
            rx.badge(row["status"], color_scheme=row["status_color"],
                     variant="soft", size="1")),
        rx.table.cell(
            rx.button(rx.icon("pencil", size=13), "Edit Staffing", size="1",
                      variant="soft",
                      on_click=ManpowerState.edit_day(row["iso"])),
        ),
        align="center",
    )


def _staffing_table() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Daily Staffing Overview", "calendar-days"),
            rx.scroll_area(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Date"),
                            rx.table.column_header_cell("FT Working"),
                            rx.table.column_header_cell("PT Working"),
                            rx.table.column_header_cell("Effective"),
                            rx.table.column_header_cell("Hires"),
                            rx.table.column_header_cell("Resigned"),
                            rx.table.column_header_cell("Leave"),
                            rx.table.column_header_cell("Sales"),
                            rx.table.column_header_cell("Sales / Staff"),
                            rx.table.column_header_cell("Status"),
                            rx.table.column_header_cell(""),
                        )
                    ),
                    rx.table.body(rx.foreach(ManpowerState.staff_rows,
                                             _staff_row)),
                    variant="surface", width="100%",
                ),
                type="auto", scrollbars="horizontal",
                style={"maxWidth": "100%"},
            ),
            rx.text(ManpowerState.staffing_note, size="1",
                    color=ds.TEXT_FAINT),
            rx.hstack(
                rx.icon("info", size=13, color=ds.TEXT_FAINT),
                rx.text(
                    "Edit Staffing opens Daily Operations for the same store "
                    "and date — staffing data is saved once, in one place, "
                    "through the authorized daily write service.",
                    size="1", color=ds.TEXT_FAINT),
                spacing="2", align="center", wrap="wrap",
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="staffing-table",
    )


def _movement_section() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Employee Movement Summary", "arrow-left-right"),
            rx.grid(
                ds.kpi_card("Opening Headcount",
                            ManpowerState.move_kpis["opening"]),
                ds.kpi_card("Monthly New Hires",
                            ManpowerState.move_kpis["hires"],
                            accent="green"),
                ds.kpi_card("Monthly Resignations",
                            ManpowerState.move_kpis["resigned"],
                            accent="red"),
                ds.kpi_card("Closing Headcount",
                            ManpowerState.move_kpis["closing"]),
                ds.kpi_card("Monthly Turnover",
                            ManpowerState.move_kpis["turnover"],
                            sub=ManpowerState.move_kpis["turnover_status"]),
                columns=rx.breakpoints(initial="2", sm="3", lg="5"),
                spacing="3", width="100%",
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="movement-section",
    )


# ---------------------------------------------------------------------------
# Configuration & payroll editor dialog (centered; Daily pattern)
# ---------------------------------------------------------------------------

def _field(label: str, comp: rx.Component, hint: str = "") -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="medium"),
        comp,
        rx.cond(hint != "", rx.text(hint, size="1", color=ds.TEXT_FAINT)),
        spacing="1", align="start", width="100%",
    )


def _config_dialog() -> rx.Component:
    return rx.dialog.root(
            rx.dialog.content(
                rx.vstack(
                    rx.hstack(
                        rx.vstack(
                            rx.heading("Monthly Configuration & Payroll",
                                       size="4"),
                            rx.text(
                                "Only changed staffing fields are saved; "
                                "blank payroll fields explicitly clear to "
                                "awaiting-payroll (NULL), never RM 0.",
                                size="1", color=ds.TEXT_MUTED),
                            spacing="0", align="start"),
                        rx.spacer(),
                        rx.dialog.close(
                            rx.icon_button(rx.icon("x", size=16),
                                           variant="ghost",
                                           id="cfg-close")),
                        width="100%", align="center", flex_shrink="0"),
                    rx.cond(
                        ManpowerState.mp_finalized,
                        rx.callout(
                            "This month has a finalized snapshot. Changes "
                            "affect live results only; the preserved snapshot "
                            "is never modified.",
                            icon="archive", color_scheme="violet",
                            variant="soft", width="100%"),
                    ),
                    rx.cond(ManpowerState.cfg_error != "",
                            ds.error_callout(ManpowerState.cfg_error)),
                    rx.scroll_area(
                        rx.vstack(
                            ds.section_heading("Workforce", "users"),
                            rx.grid(
                                _field("Opening FT headcount",
                                       rx.input(value=ManpowerState.c_opening,
                                                on_change=ManpowerState.set_c_opening,
                                                input_mode="numeric",
                                                id="c-opening"),
                                       "Blank = leave unchanged."),
                                _field("Budgeted headcount",
                                       rx.input(value=ManpowerState.c_budget,
                                                on_change=ManpowerState.set_c_budget,
                                                input_mode="numeric",
                                                id="c-budget")),
                                _field("Part-time conversion factor",
                                       rx.input(value=ManpowerState.c_pt_factor,
                                                on_change=ManpowerState.set_c_pt_factor,
                                                input_mode="decimal",
                                                placeholder="e.g. 0.5",
                                                id="c-pt-factor"),
                                       "Effective staff = FT + PT × factor."),
                                _field("Turnover threshold (%)",
                                       rx.input(value=ManpowerState.c_turnover,
                                                on_change=ManpowerState.set_c_turnover,
                                                input_mode="decimal",
                                                placeholder="e.g. 45",
                                                id="c-turnover")),
                                _field("Salary ratio target (%)",
                                       rx.input(value=ManpowerState.c_ratio_target,
                                                on_change=ManpowerState.set_c_ratio_target,
                                                input_mode="decimal",
                                                placeholder="e.g. 6.818",
                                                id="c-ratio-target")),
                                columns="2",
                                spacing="3", width="100%"),
                            rx.divider(),
                            ds.section_heading("Month-end Payroll (RM)",
                                               "wallet"),
                            rx.grid(
                                _field("Basic salary + allowances",
                                       rx.input(value=ManpowerState.p_salary,
                                                on_change=ManpowerState.set_p_salary,
                                                input_mode="decimal",
                                                placeholder="e.g. 13275.75",
                                                id="p-salary"),
                                       "Blank = awaiting payroll (NULL)."),
                                _field("Overtime",
                                       rx.input(value=ManpowerState.p_overtime,
                                                on_change=ManpowerState.set_p_overtime,
                                                input_mode="decimal",
                                                placeholder="e.g. 4198.50",
                                                id="p-overtime")),
                                _field("Support team cost",
                                       rx.input(value=ManpowerState.p_support,
                                                on_change=ManpowerState.set_p_support,
                                                input_mode="decimal",
                                                placeholder="Optional",
                                                id="p-support")),
                                columns="2",
                                spacing="3", width="100%"),
                            rx.text(
                                "Payroll inputs are workbook month-end "
                                "aggregates. Which management positions are "
                                "included in Basic Salary + Allowances "
                                "follows the workbook definition and remains "
                                "subject to HQ confirmation.",
                                size="1", color=ds.TEXT_FAINT),
                            spacing="4", width="100%"),
                        flex_grow="1", min_height="0", type="auto",
                        width="100%"),
                    rx.hstack(
                        rx.spacer(),
                        rx.dialog.close(
                            rx.button("Cancel", variant="soft",
                                      color_scheme="gray", id="cfg-cancel")),
                        rx.button(
                            rx.cond(ManpowerState.cfg_saving,
                                    rx.spinner(size="1"),
                                    rx.icon("save", size=14)),
                            "Save Configuration", color_scheme="indigo",
                            on_click=ManpowerState.save_config,
                            disabled=ManpowerState.cfg_saving,
                            id="cfg-save"),
                        width="100%", align="center", flex_shrink="0",
                        border_top=f"1px solid {ds.BORDER}",
                        padding_top="0.75em"),
                    spacing="4", width="100%", height="100%",
                    min_height="0",
                ),
                width="min(720px, 92vw)",
                style={
                    "maxWidth": "min(720px, 92vw)",
                    "maxHeight": "85vh",
                    "display": "flex",
                    "flexDirection": "column",
                },
                background=ds.BG, padding="1.5em",
            ),
        open=ManpowerState.cfg_open,
        on_open_change=lambda v: ManpowerState.close_config(),
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def manpower_page() -> rx.Component:
    return shell(
        "Manpower Efficiency",
        rx.vstack(
            rx.text(
                "Monitor workforce size, staffing efficiency, leave, "
                "turnover and salary ratios against configured targets. "
                "Daily staffing data comes from Daily Operations; monthly "
                "payroll and configuration are managed here by "
                "administrators.",
                size="2", color=ds.TEXT_MUTED),
            rx.cond(
                ManpowerState.mp_notice != "",
                rx.callout(ManpowerState.mp_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%", id="mp-notice"),
            ),
            rx.cond(
                ~ManpowerState.mp_config_exists,
                ds.empty_state("Monthly configuration required",
                               ManpowerState.mp_error, icon="settings"),
                rx.vstack(
                    _headcount_section(),
                    _efficiency_section(),
                    _payroll_section(),
                    _productivity_section(),
                    _staffing_table(),
                    _movement_section(),
                    _leave_section(),
                    spacing="5", width="100%",
                ),
            ),
            _config_dialog(),
            spacing="4", width="100%",
        ),
        on_store=ManpowerState.select_store,
        on_month=ManpowerState.select_month,
    )
