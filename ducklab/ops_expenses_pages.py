"""Expenses Management page: KPI cards, budget monitor, transaction
register with filters, Add/Edit expense drawer, budget editor."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab.ops_expenses_state import (
    BUDGET_COLOR, ExpensesState, PAYMENT_SOURCES, PAYMENT_STATUSES,
    VALIDATION_COLOR, VERIFICATIONS,
)
from ducklab.ops_shell import shell

SORT_OPTIONS = ["incurred", "paid", "amount"]


def _kpi_cards() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Monthly Expense KPIs", "wallet",
                extra=rx.cond(
                    ExpensesState.exp_month_finalized,
                    rx.badge(
                        "Finalized snapshot exists — expense edits affect "
                        "live calculations only, never preserved snapshots",
                        color_scheme="violet", variant="soft", size="1"),
                ),
            ),
            rx.grid(
                ds.kpi_card("Paid (paid-date basis)",
                            ExpensesState.exp_kpis["paid"], icon="banknote"),
                ds.kpi_card("Incurred (incurred-date basis)",
                            ExpensesState.exp_kpis["incurred"], icon="receipt"),
                ds.kpi_card("Pending / Outstanding",
                            ExpensesState.exp_kpis["open"],
                            icon="clock", accent="amber"),
                ds.kpi_card("Total Budget", ExpensesState.exp_kpis["budget"],
                            icon="target"),
                ds.kpi_card("Budget Utilization",
                            ExpensesState.exp_kpis["utilization"],
                            icon="percent"),
                ds.kpi_card("Budget Variance",
                            ExpensesState.exp_kpis["variance"],
                            icon="scale"),
                rx.card(
                    rx.vstack(
                        rx.text("Warning Status", size="1",
                                color=ds.TEXT_MUTED),
                        rx.badge(ExpensesState.exp_kpis["status"],
                                 color_scheme=ExpensesState.exp_kpis["status_color"],
                                 size="2", variant="soft"),
                        spacing="1", align="start"),
                    id="kpi-warning"),
                columns=rx.breakpoints(initial="1", sm="2", lg="4", xl="7"),
                spacing="3", width="100%",
            ),
            rx.hstack(
                rx.icon("shield-check", size=13, color=ds.TEXT_FAINT),
                rx.text(ExpensesState.validation_summary, size="1",
                        color=ds.TEXT_MUTED),
                spacing="2", align="center", wrap="wrap",
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="expense-kpis",
    )


def _budget_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["name"], white_space="nowrap"),
        rx.table.cell(row["budget"], white_space="nowrap"),
        rx.table.cell(row["paid"], white_space="nowrap"),
        rx.table.cell(row["remaining"], white_space="nowrap"),
        rx.table.cell(
            rx.hstack(
                rx.box(
                    rx.box(
                        width=row["bar"] + "%",
                        height="100%",
                        background=ds.ACCENT,
                        border_radius="4px",
                    ),
                    width="90px", height="8px",
                    background=ds.BORDER,
                    border_radius="4px",
                    overflow="hidden",
                ),
                rx.text(row["util"], size="1"),
                spacing="2", align="center"),
        ),
        rx.table.cell(
            rx.badge(row["status"],
                     color_scheme=row["color"], variant="soft", size="1")),
        align="center",
    )


def _budget_monitor() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Category Budget Monitor", "chart-bar"),
            rx.cond(
                ExpensesState.has_categories,
                rx.cond(ExpensesState.is_dark,
                        ops_charts.chart(ExpensesState.budget_fig_dark),
                        ops_charts.chart(ExpensesState.budget_fig_light)),
            ),
            rx.cond(
                ExpensesState.has_categories,
                rx.scroll_area(
                    rx.table.root(
                        rx.table.header(rx.table.row(
                            rx.table.column_header_cell("Category"),
                            rx.table.column_header_cell("Budget"),
                            rx.table.column_header_cell("Paid"),
                            rx.table.column_header_cell("Remaining"),
                            rx.table.column_header_cell("Utilization"),
                            rx.table.column_header_cell("Status"),
                        )),
                        rx.table.body(
                            rx.foreach(ExpensesState.budget_rows,
                                       _budget_row)),
                        variant="surface", width="100%",
                    ),
                    type="auto", scrollbars="horizontal",
                    style={"maxWidth": "100%"},
                ),
                ds.empty_state(
                    "No expense categories configured",
                    "An administrator must set up expense categories and "
                    "monthly budgets before monitoring is available.",
                    icon="folders"),
            ),
            rx.cond(
                ExpensesState.can_manage_budgets & ExpensesState.has_categories,
                rx.vstack(
                    rx.divider(),
                    rx.text("Update monthly budget", size="2", weight="bold"),
                    rx.grid(
                        rx.vstack(
                            rx.text("Category", size="1", color=ds.TEXT_MUTED),
                            rx.select(
                                ExpensesState.category_names,
                                value=ExpensesState.b_category,
                                on_change=ExpensesState.set_b_category,
                                placeholder="Select category",
                                id="b-category"),
                            spacing="1", align="start", width="100%"),
                        rx.vstack(
                            rx.text("Monthly budget (RM)", size="1",
                                    color=ds.TEXT_MUTED),
                            rx.input(
                                value=ExpensesState.b_amount,
                                on_change=ExpensesState.set_b_amount,
                                placeholder="e.g. 3200.00",
                                input_mode="decimal", id="b-amount"),
                            spacing="1", align="start", width="100%"),
                        rx.vstack(
                            rx.text("\u00a0", size="1"),
                            rx.button(
                                rx.cond(ExpensesState.b_saving,
                                        rx.spinner(size="1"),
                                        rx.icon("save", size=14)),
                                "Save Budget", color_scheme="indigo",
                                on_click=ExpensesState.save_budget,
                                disabled=ExpensesState.b_saving,
                                id="b-save"),
                            spacing="1", align="start"),
                        columns=rx.breakpoints(initial="1", sm="3"),
                        spacing="3", width="100%",
                    ),
                    rx.cond(ExpensesState.b_error != "",
                            ds.error_callout(ExpensesState.b_error)),
                    rx.cond(
                        ExpensesState.b_notice != "",
                        rx.callout(ExpensesState.b_notice, icon="circle-check",
                                   color_scheme="green", variant="soft",
                                   id="b-notice")),
                    spacing="3", align="start", width="100%",
                ),
            ),
            rx.text(
                "Budgets belong to the selected store and month. Paid "
                "figures use the workbook's paid-date basis. Watch ≥ 80%; "
                "OVER BUDGET when paid exceeds budget; a missing budget is "
                "distinct from RM 0.",
                size="1", color=ds.TEXT_FAINT),
            spacing="3", width="100%",
        ),
        width="100%", id="budget-monitor",
    )


def _register_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["incurred"], white_space="nowrap"),
        rx.table.cell(row["paid"], white_space="nowrap"),
        rx.table.cell(row["category"], white_space="nowrap"),
        rx.table.cell(
            rx.text(row["description"], size="2",
                    style={"maxWidth": "220px", "overflow": "hidden",
                           "textOverflow": "ellipsis"}),
            title=row["description"]),
        rx.table.cell(row["amount"], white_space="nowrap"),
        rx.table.cell(row["paid_by"], white_space="nowrap"),
        rx.table.cell(rx.badge(row["status"],
                               color_scheme=row["status_color"],
                               variant="soft", size="1")),
        rx.table.cell(row["receipt"], white_space="nowrap"),
        rx.table.cell(rx.badge(row["verification"],
                               color_scheme=row["ver_color"],
                               variant="soft", size="1")),
        rx.table.cell(row["recorded_by"], white_space="nowrap"),
        rx.table.cell(
            rx.tooltip(
                rx.badge(row["validation"],
                         color_scheme=row["vcolor"], variant="soft", size="1"),
                content=row["remarks"])),
        rx.table.cell(
            rx.cond(
                ExpensesState.can_write_expenses,
                rx.button(rx.icon("pencil", size=13), "Edit", size="1",
                          variant="soft",
                          on_click=ExpensesState.open_expense_form(row["id"])),
                rx.button(rx.icon("eye", size=13), "View", size="1",
                          variant="ghost",
                          on_click=ExpensesState.open_expense_form(row["id"])),
            ),
        ),
        align="center",
    )


def _register() -> rx.Component:
    return rx.card(
        rx.vstack(
            rx.hstack(
                ds.section_heading("Transaction Register", "list"),
                rx.spacer(),
                rx.cond(
                    ExpensesState.can_write_expenses,
                    rx.button(rx.icon("plus", size=14), "Add Expense",
                              color_scheme="indigo",
                              on_click=ExpensesState.open_expense_form(""),
                              id="add-expense"),
                ),
                width="100%", align="center",
            ),
            rx.grid(
                rx.vstack(
                    rx.text("Period view", size="1", color=ds.TEXT_MUTED),
                    rx.select(
                        ExpensesState.period_options,
                        value=ExpensesState.filter_period_label,
                        on_change=ExpensesState.set_filter_period_label,
                        id="filter-period"),
                    spacing="1", align="start"),
                rx.vstack(
                    rx.text("Category", size="1", color=ds.TEXT_MUTED),
                    rx.select(
                        ExpensesState.filter_category_options,
                        on_change=ExpensesState.set_filter_category_name,
                        id="filter-category"),
                    spacing="1", align="start"),
                rx.vstack(
                    rx.text("Status", size="1", color=ds.TEXT_MUTED),
                    rx.select(
                        ["All statuses", *PAYMENT_STATUSES],
                        on_change=ExpensesState.set_filter_status,
                        id="filter-status"),
                    spacing="1", align="start"),
                rx.vstack(
                    rx.text("Verification", size="1", color=ds.TEXT_MUTED),
                    rx.select(
                        ["All verifications", *VERIFICATIONS],
                        on_change=ExpensesState.set_filter_verification,
                        id="filter-verification"),
                    spacing="1", align="start"),
                rx.vstack(
                    rx.text("Search description / receipt", size="1",
                            color=ds.TEXT_MUTED),
                    rx.input(
                        value=ExpensesState.search_text,
                        on_change=ExpensesState.set_search_text,
                        placeholder="Search…", id="filter-search"),
                    spacing="1", align="start"),
                rx.vstack(
                    rx.text("Sort", size="1", color=ds.TEXT_MUTED),
                    rx.hstack(
                        rx.select(
                            SORT_OPTIONS, value="incurred",
                            on_change=ExpensesState.set_sort_key,
                            id="filter-sort"),
                        rx.icon_button(
                            rx.cond(ExpensesState.sort_desc,
                                    rx.icon("arrow-down", size=14),
                                    rx.icon("arrow-up", size=14)),
                            variant="soft",
                            on_click=ExpensesState.toggle_sort_dir,
                            id="sort-dir"),
                        spacing="1"),
                    spacing="1", align="start"),
                columns=rx.breakpoints(initial="2", sm="3", lg="6"),
                spacing="3", width="100%",
            ),
            rx.scroll_area(
                rx.table.root(
                    rx.table.header(rx.table.row(
                        rx.table.column_header_cell("Incurred"),
                        rx.table.column_header_cell("Paid"),
                        rx.table.column_header_cell("Category"),
                        rx.table.column_header_cell("Description"),
                        rx.table.column_header_cell("Amount"),
                        rx.table.column_header_cell("Paid By"),
                        rx.table.column_header_cell("Status"),
                        rx.table.column_header_cell("Receipt Ref"),
                        rx.table.column_header_cell("Verification"),
                        rx.table.column_header_cell("Recorded By"),
                        rx.table.column_header_cell("Data Check"),
                        rx.table.column_header_cell(""),
                    )),
                    rx.table.body(rx.foreach(ExpensesState.expense_rows,
                                             _register_row)),
                    variant="surface", width="100%",
                ),
                type="auto", scrollbars="horizontal",
                style={"maxWidth": "100%"},
            ),
            rx.text(ExpensesState.rows_note, size="1", color=ds.TEXT_FAINT),
            rx.cond(
                ExpensesState.total_rows == "0",
                ds.empty_state(
                    "No expense transactions yet",
                    "Use Add Expense to record the first transaction for "
                    "this store.", icon="receipt"),
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="expense-register",
    )


# ---------------------------------------------------------------------------
# Expense drawer form
# ---------------------------------------------------------------------------

def _field(label: str, comp: rx.Component, hint: str = "") -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="medium"),
        comp,
        rx.cond(hint != "", rx.text(hint, size="1", color=ds.TEXT_FAINT)),
        spacing="1", align="start", width="100%",
    )


def _expense_drawer() -> rx.Component:
    ro = ~ExpensesState.can_write_expenses
    return rx.drawer.root(
        rx.drawer.portal(
            rx.drawer.content(
                rx.vstack(
                    rx.hstack(
                        rx.vstack(
                            rx.heading(
                                rx.cond(ExpensesState.editing_id == "",
                                        "Add Expense", "Edit Expense"),
                                size="4"),
                            rx.text(
                                "Incomplete transactions may be saved as "
                                "drafts; the Data Check column flags what "
                                "still needs attention.",
                                size="1", color=ds.TEXT_MUTED),
                            spacing="0", align="start"),
                        rx.spacer(),
                        rx.drawer.close(
                            rx.icon_button(rx.icon("x", size=16),
                                           variant="ghost",
                                           id="exp-close")),
                        width="100%", align="center"),
                    rx.cond(
                        ro,
                        rx.callout(
                            "You have read-only access. Contact an "
                            "administrator to modify expenses.",
                            icon="lock", color_scheme="gray", variant="soft",
                            width="100%"),
                    ),
                    rx.cond(ExpensesState.exp_form_error != "",
                            ds.error_callout(ExpensesState.exp_form_error)),
                    rx.scroll_area(
                        rx.vstack(
                            ds.section_heading("Transaction", "receipt"),
                            rx.grid(
                                _field("Incurred date",
                                       rx.input(type="date",
                                                value=ExpensesState.e_incurred,
                                                on_change=ExpensesState.set_e_incurred,
                                                id="e-incurred",
                                                disabled=ro)),
                                _field("Paid date",
                                       rx.input(type="date",
                                                value=ExpensesState.e_paid,
                                                on_change=ExpensesState.set_e_paid,
                                                id="e-paid",
                                                disabled=ro),
                                       "Required only when status is Paid."),
                                _field("Category",
                                       rx.select(
                                           ExpensesState.category_names,
                                           value=ExpensesState.e_category,
                                           on_change=ExpensesState.set_e_category,
                                           placeholder="Select category",
                                           id="e-category",
                                           disabled=ro)),
                                _field("Amount (RM)",
                                       rx.input(value=ExpensesState.e_amount,
                                                on_change=ExpensesState.set_e_amount,
                                                placeholder="0.00",
                                                input_mode="decimal",
                                                id="e-amount", disabled=ro),
                                       "Exact cents; RM125.50 = 12550¢."),
                                columns="2",
                                spacing="3", width="100%"),
                            _field("Description",
                                   rx.text_area(
                                       value=ExpensesState.e_description,
                                       on_change=ExpensesState.set_e_description,
                                       placeholder="What was purchased?",
                                       id="e-description", disabled=ro)),
                            rx.divider(),
                            ds.section_heading("Payment & Verification",
                                               "banknote"),
                            rx.grid(
                                _field("Payment status",
                                       rx.select(
                                           list(PAYMENT_STATUSES),
                                           value=ExpensesState.e_status,
                                           on_change=ExpensesState.set_e_status,
                                           id="e-status", disabled=ro)),
                                _field("Paid by",
                                       rx.select(
                                           list(PAYMENT_SOURCES),
                                           value=ExpensesState.e_paid_by,
                                           on_change=ExpensesState.set_e_paid_by,
                                           placeholder="Select source",
                                           id="e-paid-by", disabled=ro)),
                                _field("Verification",
                                       rx.select(
                                           list(VERIFICATIONS),
                                           value=ExpensesState.e_verification,
                                           on_change=ExpensesState.set_e_verification,
                                           id="e-verification", disabled=ro),
                                       "Paid does not imply Verified."),
                                _field("Receipt / transaction reference",
                                       rx.input(value=ExpensesState.e_receipt,
                                                on_change=ExpensesState.set_e_receipt,
                                                placeholder="e.g. RCP-1024",
                                                id="e-receipt", disabled=ro)),
                                _field("Recorded by",
                                       rx.input(value=ExpensesState.e_recorded_by,
                                                on_change=ExpensesState.set_e_recorded_by,
                                                placeholder="Your name",
                                                id="e-recorded-by",
                                                disabled=ro)),
                                columns="2",
                                spacing="3", width="100%"),
                            _field("Remarks",
                                   rx.text_area(
                                       value=ExpensesState.e_remarks,
                                       on_change=ExpensesState.set_e_remarks,
                                       placeholder="Optional notes",
                                       id="e-remarks", disabled=ro)),
                            spacing="4", width="100%"),
                        style={"maxHeight": "64vh", "minWidth": "620px"},
                        type="auto"),
                    rx.hstack(
                        rx.spacer(),
                        rx.drawer.close(
                            rx.button("Cancel", variant="soft",
                                      color_scheme="gray", id="exp-cancel")),
                        rx.cond(
                            ExpensesState.can_write_expenses,
                            rx.button(
                                rx.cond(ExpensesState.exp_saving,
                                        rx.spinner(size="1"),
                                        rx.icon("save", size=14)),
                                "Save Expense", color_scheme="indigo",
                                on_click=ExpensesState.save_expense,
                                disabled=ExpensesState.exp_saving,
                                id="exp-save")),
                        width="100%", align="center"),
                    spacing="4", width="100%",
                ),
                width="720px",
                style={"maxWidth": "100vw"},
                background=ds.BG, padding="1.5em",
            ),
        ),
        open=ExpensesState.exp_form_open,
        direction="right",
        on_open_change=lambda v: ExpensesState.close_expense_form(),
        modal=False,
    )


def expenses_page() -> rx.Component:
    return shell(
        "Expenses Management",
        rx.vstack(
            rx.text(
                "Record expenses, track payment and verification, and "
                "monitor category budgets. Paid figures use the workbook's "
                "paid-date basis; incurred figures use the incurred date.",
                size="2", color=ds.TEXT_MUTED),
            rx.cond(
                ExpensesState.exp_notice != "",
                rx.callout(ExpensesState.exp_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%", id="exp-notice"),
            ),
            rx.cond(
                ExpensesState.report_status == "empty",
                ds.empty_state("No store or month selected",
                               ExpensesState.error_message, icon="store"),
                rx.vstack(
                    _kpi_cards(),
                    _budget_monitor(),
                    _register(),
                    spacing="5", width="100%",
                ),
            ),
            _expense_drawer(),
            spacing="4", width="100%",
        ),
        on_store=ExpensesState.select_store,
        on_month=ExpensesState.select_month,
    )
