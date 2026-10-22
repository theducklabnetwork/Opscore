"""Manpower Efficiency page: workforce KPIs, headcount monitoring, salary
ratio monitoring, daily staffing overview, leave monitoring, and the
monthly configuration/payroll editor."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.ops_manpower_state import ManpowerState
from ducklab.ops_shell import shell


M = ManpowerState
HC, EFF, PAY, LEAVE = M.hc_kpis, M.eff_kpis, M.pay_kpis, M.leave_kpis


# ---------------------------------------------------------------------------
# Groups (each figure appears once): Headcount, Staffing efficiency,
# Salary ratio (payroll-gated), Leave
# ---------------------------------------------------------------------------

def _headcount_section() -> rx.Component:
    return ui.section_card(
        ui.stat_grid(
            ui.stat("Opening headcount", HC["opening"], size="6"),
            ui.stat("New hires", HC["hires"], size="6"),
            ui.stat("Resignations", HC["resigned"], size="6"),
            ui.stat("Closing headcount", HC["closing"], size="6"),
            ui.stat("Budgeted headcount", HC["budget"], size="6"),
            ui.stat("Headcount variance", HC["variance"], size="6",
                    sub="Closing − budget"),
            ui.stat("Turnover", HC["turnover"], size="6",
                    chip=(HC["turnover_status"], HC["turnover_color"]),
                    sub=HC["turnover_sub"]),
        ),
        rx.cond(
            HC["status"] == "Vacancy",
            rx.callout(
                "Vacancy: closing headcount is below the budgeted headcount "
                "for this store/month. Review hiring progress in Daily "
                "Operations.",
                icon="triangle-alert", color_scheme="amber", variant="soft",
                width="100%", id="vacancy-warning"),
        ),
        rx.cond(
            HC["status"] == "Over budget",
            rx.callout(
                "Over budget: closing headcount exceeds the configured "
                "budget headcount.",
                icon="triangle-alert", color_scheme="red", variant="soft",
                width="100%"),
        ),
        title="Headcount",
        extra=ui.status_chip(HC["status"], HC["status_color"], id="hc-status"),
        id="headcount-section",
    )


def _efficiency_section() -> rx.Component:
    return ui.section_card(
        ui.stat_grid(
            ui.stat("Sales MTD", EFF["sales_mtd"], size="6",
                    sub=EFF["completeness"]),
            ui.stat("Avg effective staff", EFF["avg_effective"], size="6",
                    sub="Part-timer factor " + M.pt_factor_label),
            ui.stat("Sales per effective staff", EFF["sales_per_staff"],
                    size="6"),
            columns=rx.breakpoints(initial="1", sm="3"),
        ),
        ui.note("Sales per effective staff = sales on staffed days ÷ "
                "effective staff-days; unstaffed or unreported days never "
                "distort the average."),
        title="Staffing efficiency", id="efficiency-section",
    )


def _payroll_section() -> rx.Component:
    ratio_bar = rx.vstack(
        rx.hstack(
            rx.text("Ratio incl. support vs target", size="1",
                    color=ds.TEXT_MUTED),
            rx.spacer(),
            rx.text(PAY["ratio_support"] + " of " + PAY["target"], size="1",
                    color=ds.TEXT_MUTED),
            width="100%",
        ),
        rx.box(
            rx.box(width=PAY["ratio_bar"] + "%", height="100%",
                   background=rx.color(PAY["bar_color"], 9),
                   border_radius="3px", id="ratio-bar"),
            width="100%", height="6px", background=ui.ROW_BORDER,
            border_radius="3px", overflow="hidden",
        ),
        spacing="1", width="100%",
    )
    visible = rx.vstack(
        ui.stat_grid(
            ui.stat("Salary ratio incl. support", PAY["ratio_support"],
                    size="6"),
            ui.stat("Salary ratio (store)", PAY["ratio"], size="6"),
            ui.stat("Ratio target", PAY["target"], size="6"),
            ui.stat("Total salary cost", PAY["total"], size="6"),
        ),
        ratio_bar,
        rx.cond(PAY["mtd"] == "1",
                ui.note("Month to date: the ratio uses sales entered so far "
                        "and is only final once the month's sales are "
                        "complete.")),
        rx.divider(),
        ui.stat_grid(
            ui.stat("Basic salary + allowances", PAY["basic"], size="5"),
            ui.stat("Overtime", PAY["overtime"], size="5"),
            ui.stat("Support team cost", PAY["support"], size="5"),
            ui.stat("Salary budget", PAY["budget"], size="5",
                    sub="Variance " + PAY["variance"]
                        + " (budget − actual)"),
        ),
        rx.cond(
            PAY["status"] == "Awaiting payroll",
            rx.callout(
                "Awaiting payroll: no basic salary has been recorded for "
                "this store/month. Missing payroll is not RM 0 — enter "
                "month-end payroll below.",
                icon="clock", color_scheme="gray", variant="soft",
                width="100%", id="awaiting-payroll"),
        ),
        spacing="4", width="100%",
    )
    return ui.section_card(
        rx.cond(
            M.can_view_payroll,
            visible,
            rx.callout(
                "Payroll figures are restricted to administrators. Your role "
                "can monitor headcount, staffing and turnover, but salary "
                "values are never sent to your session.",
                icon="lock", color_scheme="gray", variant="soft",
                width="100%", id="payroll-restricted"),
        ),
        rx.cond(
            M.mp_finalized,
            rx.callout(
                "A finalized snapshot exists for this month. Editing "
                "configuration or payroll updates live results only; "
                "preserved snapshots are never modified.",
                icon="archive", color_scheme="violet", variant="soft",
                width="100%"),
        ),
        title="Salary ratio",
        extra=rx.hstack(
            ui.status_chip(PAY["status"], PAY["status_color"],
                           id="ratio-status"),
            rx.cond(
                M.can_manage_manpower,
                rx.button(rx.icon("settings-2", size=14),
                          "Edit configuration & payroll", size="1",
                          variant="soft", on_click=M.open_config,
                          id="open-config"),
            ),
            spacing="3", align="center", wrap="wrap",
        ),
        id="payroll-section",
    )


def _leave_section() -> rx.Component:
    return ui.section_card(
        ui.stat_grid(
            ui.stat("Annual leave", LEAVE["annual"], size="6"),
            ui.stat("Medical leave (MC)", LEAVE["mc"], size="6"),
            ui.stat("Other paid leave", LEAVE["other_paid"], size="6"),
            ui.stat("Unpaid leave", LEAVE["unpaid"], size="6"),
            ui.stat("Total leave days", LEAVE["total"], size="6"),
            columns=rx.breakpoints(initial="2", md="5"),
        ),
        ui.note("Store-level daily leave counts from Daily Operations (not "
                "individual leave applications)."),
        title="Leave", id="leave-section",
    )


# ---------------------------------------------------------------------------
# Daily staffing table (unreported days collapsed)
# ---------------------------------------------------------------------------

_STAFF_COLS = ["Date", ("FT", True), ("PT", True), ("Effective", True),
               ("Hires", True), ("Resigned", True), ("Leave", True),
               ("Sales", True), ("Sales / staff", True), "Status", ""]


def _staff_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.hstack(rx.text(row["date"], weight="medium"),
                        rx.text(row["weekday"], size="1",
                                color=ds.TEXT_FAINT),
                        spacing="2", align="baseline")),
        ui.td(row["ft"], numeric=True),
        ui.td(row["pt"], numeric=True),
        ui.td(rx.text(row["effective"], weight="medium"), numeric=True),
        ui.td(row["hires"], numeric=True),
        ui.td(row["resigned"], numeric=True),
        ui.td(row["leave"], numeric=True),
        ui.td(row["sales"], numeric=True),
        ui.td(row["sales_per_staff"], numeric=True),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(rx.button(rx.icon("pencil", size=13, class_name="ph-hide"),
                        "Edit",
                        rx.el.span("staffing", class_name="ph-hide"),
                        size="1", variant="soft",
                        on_click=M.edit_day(row["iso"]))),
    )


def _staffing_table() -> rx.Component:
    return ui.section_card(
        ui.data_table(
            _STAFF_COLS,
            *ui.day_rows(M.staff_rows, M.staff_rows_reported,
                         M.staff_hidden_count, M.staff_hidden_label,
                         M.staff_show_hidden, M.toggle_staff_hidden_days,
                         _staff_row, len(_STAFF_COLS),
                         toggle_id="staff-hidden-toggle"),
            phone=("Date", "Effective", "Status", ""),
        ),
        ui.status_legend((ds.DAY_COMPLETE, ds.DAY_PARTIAL,
                          ds.DAY_NOT_REPORTED), note=M.staffing_note),
        ui.note("Edit staffing opens Daily Operations for the same store and "
                "date — staffing is saved once, in one place."),
        title="Daily staffing", id="staffing-table",
    )


# ---------------------------------------------------------------------------
# Configuration & payroll editor dialog (kit entry dialog; Daily pattern)
# ---------------------------------------------------------------------------

def _input(attr, fid, mode="decimal", placeholder="") -> rx.Component:
    return rx.input(value=getattr(M, attr), on_change=getattr(M, "set_" + attr),
                    input_mode=mode, placeholder=placeholder, id=fid)


def _config_dialog() -> rx.Component:
    return ui.form_dialog(
        ui.form_section(
            "Workforce",
            ui.form_grid(
                ui.form_field("Opening FT headcount",
                              _input("c_opening", "c-opening", "numeric"),
                              "Leave blank to keep the saved value."),
                ui.form_field("Budgeted headcount",
                              _input("c_budget", "c-budget", "numeric")),
                ui.form_field("Part-time conversion factor",
                              _input("c_pt_factor", "c-pt-factor",
                                     placeholder="e.g. 0.5"),
                              "Effective staff = FT + PT × factor."),
                ui.form_field("Turnover threshold (%)",
                              _input("c_turnover", "c-turnover",
                                     placeholder="e.g. 45")),
                ui.form_field("Salary ratio target (%)",
                              _input("c_ratio_target", "c-ratio-target",
                                     placeholder="e.g. 6.818")),
            ),
        ),
        ui.form_section(
            "Month-end payroll (RM)",
            ui.form_grid(
                ui.form_field("Basic salary + allowances",
                              _input("p_salary", "p-salary",
                                     placeholder="e.g. 13275.75"),
                              "Leave blank until payroll closes."),
                ui.form_field("Overtime",
                              _input("p_overtime", "p-overtime",
                                     placeholder="e.g. 4198.50")),
                ui.form_field("Support team cost",
                              _input("p_support", "p-support",
                                     placeholder="Optional")),
            ),
            hint="Payroll inputs are workbook month-end aggregates. Which "
                 "management positions are included in Basic Salary + "
                 "Allowances follows the workbook definition and remains "
                 "subject to HQ confirmation.",
        ),
        open=M.cfg_open,
        on_open_change=lambda v: M.close_config(),
        title="Monthly Configuration & Payroll",
        subtitle="Only changed staffing fields are saved. Leave payroll "
                 "blank until it closes: it then shows as awaiting payroll, "
                 "never RM 0.",
        close_id="cfg-close",
        notices=[
            rx.cond(M.mp_finalized, rx.callout(
                "This month has a finalized snapshot. Changes affect live "
                "results only; the preserved snapshot is never modified.",
                icon="archive", color_scheme="violet", variant="soft",
                width="100%")),
            rx.cond(M.cfg_error != "", ds.error_callout(M.cfg_error)),
        ],
        actions=ui.dialog_actions(
            save_label="Save Configuration", on_save=M.save_config,
            saving=M.cfg_saving, save_id="cfg-save", cancel_id="cfg-cancel"),
    )


# ---------------------------------------------------------------------------
# Page
# ---------------------------------------------------------------------------

def manpower_page() -> rx.Component:
    return shell(
        "Manpower Efficiency",
        rx.vstack(
            rx.cond(
                M.mp_notice != "",
                rx.callout(M.mp_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%", id="mp-notice"),
            ),
            rx.cond(
                ~M.cfg_open & (M.cfg_error != ""),
                ds.error_callout(M.cfg_error),
            ),
            rx.cond(
                M.mp_config_exists & (M.mp_error != ""),
                ds.error_callout(M.mp_error),
            ),
            rx.cond(
                ~M.mp_config_exists,
                ui.empty_state("Monthly configuration required",
                               M.mp_error, icon="settings"),
                rx.vstack(
                    _headcount_section(),
                    _efficiency_section(),
                    _payroll_section(),
                    _leave_section(),
                    _staffing_table(),
                    spacing=ui.GAP, width="100%",
                ),
            ),
            _config_dialog(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=M.select_store,
        on_month=M.select_month,
        description="Headcount, staffing efficiency, salary ratio and leave "
                    "against the month's targets. Daily staffing comes from "
                    "Daily Operations; payroll is managed here by "
                    "administrators.",
    )
