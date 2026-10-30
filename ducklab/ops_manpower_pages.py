"""Manpower Efficiency page: workforce KPIs, headcount monitoring, salary
ratio monitoring, daily staffing overview, leave monitoring, and the
monthly configuration/payroll editor."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.ops_manpower_state import ManpowerState
from ducklab.ops_shell import shell
from ducklab.ops_schedule_pages import schedule_tab


M = ManpowerState
HC, EFF, PAY, LEAVE = M.hc_kpis, M.eff_kpis, M.pay_kpis, M.leave_kpis


# ---------------------------------------------------------------------------
# Groups (each figure appears once): Headcount, Staffing efficiency,
# Salary ratio (payroll-gated), Leave
# ---------------------------------------------------------------------------

def _headcount_section() -> rx.Component:
    return ui.section_card(
        ui.stat_grid(
            ui.stat("Opening Headcount", HC["opening"], size="6"),
            ui.stat("New Hires", HC["hires"], size="6"),
            ui.stat("Resignations", HC["resigned"], size="6"),
            ui.stat("Closing Headcount", HC["closing"], size="6"),
            ui.stat("Budget Headcount", HC["budget"], size="6"),
            ui.stat("Variance", HC["variance"], size="6"),
            ui.stat("Turnover", HC["turnover"], size="6",
                    chip=(HC["turnover_status"], HC["turnover_color"])),
        ),
        rx.cond(
            HC["status"] == "Vacancy",
            rx.callout("Vacancy", icon="triangle-alert",
                       color_scheme="amber", variant="soft", width="100%",
                       id="vacancy-warning"),
        ),
        rx.cond(
            HC["status"] == "Over budget",
            rx.callout("Over Budget", icon="triangle-alert",
                       color_scheme="red", variant="soft", width="100%"),
        ),
        title="Headcount",
        id="headcount-section",
    )


def _efficiency_section() -> rx.Component:
    return ui.section_card(
        ui.stat_grid(
            ui.stat("Sales", EFF["sales_mtd"], size="6"),
            ui.stat("Average Effective Staff", EFF["avg_effective"],
                    size="6"),
            ui.stat("Sales per Staff", EFF["sales_per_staff"], size="6"),
            columns=rx.breakpoints(initial="1", sm="3"),
        ),
        title="Productivity", id="efficiency-section",
    )


def _ratio_label(label: str):
    """"(To Date)" while the month is incomplete (sales so far)."""
    return rx.cond(PAY["mtd"] == "1", label + " (To Date)", label)


def _payroll_section() -> rx.Component:
    ratio_bar = rx.box(
        rx.box(width=PAY["ratio_bar"] + "%", height="100%",
               background=rx.color(PAY["bar_color"], 9),
               border_radius="3px", id="ratio-bar"),
        width="100%", height="6px", background=ui.ROW_BORDER,
        border_radius="3px", overflow="hidden",
    )
    visible = rx.vstack(
        ui.stat_grid(
            ui.stat(_ratio_label("Total Salary Ratio"),
                    PAY["ratio_support"], size="6"),
            ui.stat(_ratio_label("Salary Ratio"), PAY["ratio"], size="6"),
            ui.stat("Target", PAY["target"], size="6"),
            ui.stat("Salary Cost", PAY["total"], size="6"),
        ),
        ratio_bar,
        rx.divider(),
        ui.stat_grid(
            ui.stat("Basic + Allowance", PAY["basic"], size="5"),
            ui.stat("Overtime", PAY["overtime"], size="5"),
            ui.stat("Support Cost", PAY["support"], size="5"),
            ui.stat("Salary Budget", PAY["budget"], size="5"),
            ui.stat("Variance", PAY["variance"], size="5"),
            columns=rx.breakpoints(initial="2", md="5"),
        ),
        rx.cond(
            PAY["status"] == "Awaiting Payroll",
            rx.callout("Awaiting Payroll", icon="clock",
                       color_scheme="gray", variant="soft", width="100%",
                       id="awaiting-payroll"),
        ),
        spacing="4", width="100%",
    )
    return ui.section_card(
        rx.cond(
            M.can_view_payroll,
            visible,
            rx.callout("Payroll Restricted", icon="lock",
                       color_scheme="gray", variant="soft", width="100%",
                       id="payroll-restricted"),
        ),
        rx.cond(
            M.mp_finalized,
            rx.callout(
                "A finalized snapshot exists for this month. Edits affect "
                "live results only; preserved snapshots are never modified.",
                icon="archive", color_scheme="violet", variant="soft",
                width="100%"),
        ),
        title="Salary",
        extra=rx.cond(
            M.can_manage_manpower,
            rx.button(rx.icon("settings-2", size=14), "Edit Payroll",
                      size="1", variant="soft", on_click=M.open_config,
                      id="open-config"),
        ),
        id="payroll-section",
    )


def _leave_section() -> rx.Component:
    return ui.section_card(
        ui.stat_grid(
            ui.stat("Annual Leave", LEAVE["annual"], size="6"),
            ui.stat("Medical Leave", LEAVE["mc"], size="6"),
            ui.stat("Other Paid Leave", LEAVE["other_paid"], size="6"),
            ui.stat("Unpaid Leave", LEAVE["unpaid"], size="6"),
            ui.stat("Total Leave", LEAVE["total"], size="6"),
            columns=rx.breakpoints(initial="2", md="5"),
        ),
        title="Leave", id="leave-section",
    )


# ---------------------------------------------------------------------------
# Daily staffing table (unreported days collapsed)
# ---------------------------------------------------------------------------

_STAFF_COLS = ["Date", ("FT", True), ("PT", True), ("Effective", True),
               ("Hires", True), ("Resignations", True), ("Leave", True),
               ("Sales", True), ("Sales per Staff", True), "Status", ""]


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
                          ds.DAY_NOT_REPORTED)),
        title="Daily Staffing", id="staffing-table",
    )


# ---------------------------------------------------------------------------
# Configuration & payroll editor dialog (kit entry dialog; Daily pattern)
# ---------------------------------------------------------------------------

def _input(attr, fid, mode="decimal") -> rx.Component:
    return rx.input(value=getattr(M, attr), on_change=getattr(M, "set_" + attr),
                    input_mode=mode, id=fid)


def _config_dialog() -> rx.Component:
    return ui.form_dialog(
        ui.form_section(
            "Workforce",
            ui.form_grid(
                ui.form_field("Opening Headcount",
                              _input("c_opening", "c-opening", "numeric")),
                ui.form_field("Budget Headcount",
                              _input("c_budget", "c-budget", "numeric")),
                ui.form_field("Part-Time Factor",
                              _input("c_pt_factor", "c-pt-factor")),
                ui.form_field("Turnover Threshold (%)",
                              _input("c_turnover", "c-turnover")),
                ui.form_field("Salary Ratio Target (%)",
                              _input("c_ratio_target", "c-ratio-target")),
            ),
        ),
        ui.form_section(
            "Payroll (RM)",
            ui.form_grid(
                ui.form_field("Basic + Allowance",
                              _input("p_salary", "p-salary")),
                ui.form_field("Overtime", _input("p_overtime", "p-overtime")),
                ui.form_field("Support Cost",
                              _input("p_support", "p-support")),
            ),
        ),
        open=M.cfg_open,
        on_open_change=lambda v: M.close_config(),
        title="Edit Payroll",
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
            save_label="Save", on_save=M.save_config,
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
            rx.tabs.root(
                rx.tabs.list(
                    rx.tabs.trigger("Overview", value="overview", id="manpower-tab-overview"),
                    rx.tabs.trigger("Schedule", value="schedule", id="manpower-tab-schedule"),
                ),
                rx.tabs.content(
                    rx.cond(
                        ~M.mp_config_exists,
                        ui.empty_state("Monthly Configuration Required",
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
                    value="overview",
                ),
                rx.tabs.content(schedule_tab(), value="schedule"),
                default_value="overview", width="100%",
            ),
            _config_dialog(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=M.select_store,
        on_month=M.select_month,
        tour_key="manpower",
    )
