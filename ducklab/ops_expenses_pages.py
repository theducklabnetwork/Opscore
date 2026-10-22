"""Expenses Management page: KPI cards, budget monitor, transaction
register with filters, Add/Edit expense dialog, budget editor."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ops_charts
from ducklab import ui
from ducklab.ops_expenses_state import (
    ExpensesState, PAYMENT_SOURCES, PAYMENT_STATUSES, VERIFICATIONS,
)
from ducklab.ops_shell import shell

SORT_OPTIONS = ["incurred", "paid", "amount"]


E = ExpensesState
K = E.exp_kpis


# ---------------------------------------------------------------------------
# 1. KPI tiles
# ---------------------------------------------------------------------------

def _kpi_cards() -> rx.Component:
    return rx.vstack(
        rx.cond(
            E.exp_month_finalized,
            rx.callout(
                "Finalized snapshot exists — expense edits affect live "
                "calculations only, never preserved snapshots.",
                icon="lock", color_scheme="violet", variant="soft",
                width="100%"),
        ),
        ui.kpi_grid(
            ui.kpi_tile("Paid", K["paid"], compact=K["paid_compact"],
                        exact=K["paid"], sub="Paid-date basis"),
            ui.kpi_tile("Incurred", K["incurred"],
                        compact=K["incurred_compact"], exact=K["incurred"],
                        sub="Incurred-date basis"),
            ui.kpi_tile("Pending / outstanding", K["open"],
                        compact=K["open_compact"], exact=K["open"]),
            ui.kpi_tile("Total budget", K["budget"],
                        compact=K["budget_compact"], exact=K["budget"]),
            rx.box(ui.kpi_tile("Budget utilization", K["utilization"],
                               chip=(K["status"], K["status_color"]),
                               sub=K["status_label"]),
                   id="kpi-warning", height="100%"),
            ui.kpi_tile("Budget variance", K["variance"],
                        compact=K["variance_compact"], exact=K["variance"],
                        sub="Total budget − paid"),
            columns=rx.breakpoints(initial="2", md="3"),
        ),
        rx.hstack(
            rx.icon("shield-check", size=13, color=ds.TEXT_FAINT),
            ui.note(E.validation_summary),
            spacing="2", align="center", wrap="wrap",
        ),
        spacing="3", width="100%", id="expense-kpis",
    )


# ---------------------------------------------------------------------------
# 2. Category budget monitor: chart + table + budget editor, one section
# ---------------------------------------------------------------------------

def _budget_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["name"], weight="medium")),
        ui.td(row["budget"], numeric=True, wide_only=True),
        ui.td(row["paid"], numeric=True),
        ui.td(row["remaining"], numeric=True, wide_only=True),
        ui.td(rx.hstack(
            rx.box(
                rx.box(width=row["bar"] + "%", height="100%",
                       background=rx.color(row["color"], 9),
                       border_radius="3px"),
                width="80px", height="6px", background=ui.ROW_BORDER,
                border_radius="3px", overflow="hidden",
            ),
            rx.text(row["util"], size="1", color=ds.TEXT_MUTED),
            spacing="2", align="center"), wide_only=True),
        ui.td(ui.status_chip(row["status"], row["color"])),
    )


def _budget_editor() -> rx.Component:
    return rx.cond(
        E.can_manage_budgets & E.has_categories,
        rx.vstack(
            rx.divider(),
            rx.text("Update monthly budget", size="2", weight="medium"),
            rx.hstack(
                rx.select(E.category_names, value=E.b_category,
                          on_change=E.set_b_category,
                          placeholder="Select category", id="b-category"),
                rx.input(value=E.b_amount, on_change=E.set_b_amount,
                         placeholder="Monthly budget (RM)",
                         input_mode="decimal", id="b-amount",
                         disabled=E.b_clear, width="12em"),
                rx.checkbox("Clear saved value", checked=E.b_clear,
                            on_change=E.set_b_clear, size="1",
                            id="b-clear"),
                rx.button(
                    rx.cond(E.b_saving, rx.spinner(size="1"),
                            rx.icon("save", size=14)),
                    "Save budget", on_click=E.save_budget,
                    disabled=E.b_saving, id="b-save"),
                spacing="3", align="center", wrap="wrap", width="100%",
            ),
            rx.cond(E.b_error != "", ds.error_callout(E.b_error)),
            rx.cond(
                E.b_notice != "",
                rx.callout(E.b_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           id="b-notice")),
            spacing="3", align="start", width="100%",
        ),
    )


def _budget_monitor() -> rx.Component:
    return ui.section_card(
        rx.cond(
            E.has_categories,
            rx.vstack(
                rx.cond(E.is_dark, ops_charts.chart(E.budget_fig_dark),
                        ops_charts.chart(E.budget_fig_light)),
                ui.data_table(
                    ["Category", ("Budget", True, True), ("Paid", True),
                     ("Remaining", True, True), ("Utilization", False, True),
                     "Status"],
                    rx.foreach(E.budget_rows, _budget_row),
                ),
                spacing="4", width="100%",
            ),
            ui.empty_state(
                "No expense categories configured",
                "An administrator must set up expense categories and monthly "
                "budgets before monitoring is available.", icon="folders"),
        ),
        _budget_editor(),
        ui.note("Budgets belong to the selected store and month. Paid uses "
                "the paid-date basis. Watch ≥ 80%; OVER BUDGET when paid "
                "exceeds budget; a missing budget is not RM 0."),
        title="Category budget monitor",
        subtitle="Budget vs paid per category (RM)",
        id="budget-monitor",
    )


# ---------------------------------------------------------------------------
# 3. Transaction register: filters in one row + kit table
# ---------------------------------------------------------------------------

def _filters() -> rx.Component:
    return rx.hstack(
        rx.select(E.period_options, value=E.filter_period_label,
                  on_change=E.set_filter_period_label, id="filter-period"),
        rx.select(E.filter_category_options,
                  on_change=E.set_filter_category_name,
                  placeholder="All categories", id="filter-category"),
        rx.select(["All statuses", *PAYMENT_STATUSES],
                  on_change=E.set_filter_status, placeholder="All statuses",
                  id="filter-status"),
        rx.select(["All verifications", *VERIFICATIONS],
                  on_change=E.set_filter_verification,
                  placeholder="All verifications",
                  id="filter-verification"),
        rx.input(rx.input.slot(rx.icon("search", size=14)),
                 value=E.search_text, on_change=E.set_search_text,
                 placeholder="Search description / receipt",
                 id="filter-search", min_width="14em", flex="1"),
        rx.hstack(
            rx.text("Sort", size="2", color=ds.TEXT_MUTED),
            rx.select(SORT_OPTIONS, value="incurred",
                      on_change=E.set_sort_key, id="filter-sort"),
            rx.icon_button(
                rx.cond(E.sort_desc, rx.icon("arrow-down", size=14),
                        rx.icon("arrow-up", size=14)),
                variant="soft", on_click=E.toggle_sort_dir, id="sort-dir"),
            spacing="2", align="center"),
        spacing="3", align="center", wrap="wrap", width="100%",
    )


# Paid by / Receipt ref / Recorded by only on very wide screens (all in
# the dialog),
# so the Edit button stays in view at desktop widths
_REG_COLS = ["Incurred", "Paid", "Category", "Description", ("Amount", True),
             ("Paid by", False, True, True), "Status",
             ("Receipt ref", False, True, True), "Verification",
             ("Recorded by", False, True, True), "Data check", ""]


def _register_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(row["incurred"]),
        ui.td(row["paid"]),
        ui.td(row["category"]),
        ui.td(rx.text(row["description"], size="2"), wrap=True,
              title=row["description"],
              style={"minWidth": "140px", "maxWidth": "220px"}),
        ui.td(row["amount"], numeric=True),
        ui.td(row["paid_by"], wide_only=True, xl_only=True),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(row["receipt"], wide_only=True, xl_only=True),
        ui.td(ui.status_chip(row["verification"], row["ver_color"])),
        ui.td(row["recorded_by"], wide_only=True, xl_only=True),
        ui.td(rx.tooltip(ui.status_chip(row["validation"], row["vcolor"]),
                         content=row["remarks"])),
        ui.td(rx.cond(
            E.can_write_expenses,
            rx.button(rx.icon("pencil", size=13), "Edit", size="1",
                      variant="soft",
                      on_click=E.open_expense_form(row["id"])),
            rx.button(rx.icon("eye", size=13), "View", size="1",
                      variant="ghost",
                      on_click=E.open_expense_form(row["id"])),
        )),
    )


def _register() -> rx.Component:
    return ui.section_card(
        _filters(),
        rx.cond(
            E.total_rows == "0",
            ui.empty_state(
                "No expense transactions yet",
                "Use Add expense to record the first transaction for this "
                "store.", icon="receipt"),
            ui.data_table(_REG_COLS,
                          rx.foreach(E.expense_rows, _register_row)),
        ),
        ui.note(E.rows_note),
        title="Transaction register",
        extra=rx.cond(
            E.can_write_expenses,
            rx.button(rx.icon("plus", size=14), "Add expense",
                      on_click=E.open_expense_form(""), id="add-expense"),
        ),
        id="expense-register",
    )


# ---------------------------------------------------------------------------
# Expense dialog form (centered; same pattern as Daily Operations)
# ---------------------------------------------------------------------------

def _field(label: str, comp: rx.Component, hint: str = "") -> rx.Component:
    return rx.vstack(
        rx.text(label, size="2", weight="medium"),
        comp,
        rx.cond(hint != "", rx.text(hint, size="1", color=ds.TEXT_FAINT)),
        spacing="1", align="start", width="100%",
    )


def _expense_dialog() -> rx.Component:
    ro = ~ExpensesState.can_write_expenses
    return rx.dialog.root(
            rx.dialog.content(
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
                        rx.dialog.close(
                            rx.icon_button(rx.icon("x", size=16),
                                           variant="ghost",
                                           id="exp-close")),
                        width="100%", align="center", flex_shrink="0"),
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
                        flex_grow="1", min_height="0", type="auto",
                        width="100%"),
                    rx.hstack(
                        rx.spacer(),
                        rx.dialog.close(
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
        open=ExpensesState.exp_form_open,
        on_open_change=lambda v: ExpensesState.close_expense_form(),
    )


def expenses_page() -> rx.Component:
    return shell(
        "Expenses Management",
        rx.vstack(
            rx.cond(
                E.exp_notice != "",
                rx.callout(E.exp_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%", id="exp-notice"),
            ),
            rx.cond(
                ~E.exp_form_open & (E.exp_form_error != ""),
                ds.error_callout(E.exp_form_error),
            ),
            rx.cond(E.report_status == "error",
                    ds.error_callout(E.error_message)),
            rx.cond(
                E.report_status == "empty",
                ui.empty_state("No store or month selected",
                               E.error_message, icon="store"),
                rx.vstack(
                    _kpi_cards(),
                    _budget_monitor(),
                    _register(),
                    spacing=ui.GAP, width="100%",
                ),
            ),
            _expense_dialog(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=E.select_store,
        on_month=E.select_month,
        description="Record expenses, track payment and verification, and "
                    "monitor category budgets. Paid uses the paid date; "
                    "incurred uses the incurred date.",
    )
