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

SORT_OPTIONS = ["Incurred", "Paid", "Amount"]


E = ExpensesState
K = E.exp_kpis


# ---------------------------------------------------------------------------
# 1. KPI tiles
# ---------------------------------------------------------------------------

def _tile(label, value, **kw) -> rx.Component:
    # plain label + full value (RM 350,819.00); no sub-lines
    return ui.kpi_tile(label, value, size_narrow="4", **kw)


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
            _tile("Paid", K["paid"]),
            _tile("Incurred", K["incurred"]),
            _tile("Pending", K["open"]),
            _tile("Budget", K["budget"]),
            rx.box(_tile("Utilization", K["utilization"],
                         chip=(K["status"], K["status_color"])),
                   id="kpi-warning", height="100%"),
            _tile("Variance", K["variance"]),
            columns=rx.breakpoints(initial="2", md="3"),
        ),
        # opens the register filtered to the lines that need review
        rx.cond(
            E.needs_review_count > 0,
            rx.button(
                rx.icon("flag", size=13),
                "Needs Review: " + E.needs_review_count.to(str),
                size="1", variant="soft", color_scheme="amber",
                on_click=E.show_needs_review, id="needs-review"),
        ),
        spacing="3", width="100%", id="expense-kpis",
    )


# ---------------------------------------------------------------------------
# 2. Category budget monitor: chart + table + budget editor, one section
# ---------------------------------------------------------------------------

def _budget_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["name"], weight="medium")),
        ui.td(row["budget"], numeric=True),
        ui.td(row["paid"], numeric=True),
        ui.td(row["remaining"], numeric=True),
        ui.td(rx.hstack(
            rx.box(
                rx.box(width=row["bar"] + "%", height="100%",
                       background=rx.color(row["color"], 9),
                       border_radius="3px"),
                width="80px", height="6px", background=ui.ROW_BORDER,
                border_radius="3px", overflow="hidden",
            ),
            rx.text(row["util"], size="1", color=ds.TEXT_MUTED),
            spacing="2", align="center")),
        ui.td(ui.status_chip(row["status"], row["color"])),
    )


def _budget_amount() -> rx.Component:
    # "×" clears a saved budget (same as the old "Clear saved value"
    # checkbox); once cleared, "↺" undoes it before saving
    return rx.input(
        rx.input.slot(
            rx.cond(
                E.b_clear,
                rx.tooltip(rx.icon_button(
                    rx.icon("undo-2", size=12), size="1", variant="ghost",
                    color_scheme="gray", radius="full", type="button",
                    on_click=E.set_b_clear(False), id="b-clear-undo",
                    aria_label="Undo clear"), content="Undo"),
                ui.clear_x(E.b_has_saved, E.clear_budget_x, id="b-clear"),
            ),
            side="right"),
        value=E.b_amount, on_change=E.set_b_amount, input_mode="decimal",
        id="b-amount", disabled=E.b_clear, width="100%",
    )


def _budget_editor() -> rx.Component:
    return rx.cond(
        E.can_manage_budgets & E.has_categories,
        rx.vstack(
            rx.divider(),
            rx.text("Set Budget", size="2", weight="medium"),
            rx.hstack(
                ui.form_field("Category",
                              rx.select(E.category_names,
                                        value=E.b_category,
                                        on_change=E.set_b_category,
                                        id="b-category")),
                ui.form_field("Budget (RM)", _budget_amount()),
                rx.button(
                    rx.cond(E.b_saving, rx.spinner(size="1"),
                            rx.icon("save", size=14)),
                    "Save", on_click=E.save_budget,
                    disabled=E.b_saving, id="b-save"),
                spacing="3", align="end", wrap="wrap", width="100%",
                style={"& > .rx-Stack": {"width": "14em"}},
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
                    ["Category", ("Budget", True), ("Paid", True),
                     ("Remaining", True), "Utilization", "Status"],
                    rx.foreach(E.budget_rows, _budget_row),
                    phone=("Category", "Paid", "Status"),
                ),
                spacing="4", width="100%",
            ),
            ui.empty_state("No Categories", icon="folders"),
        ),
        _budget_editor(),
        title="Budget vs Paid",
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
                  placeholder="All Categories", id="filter-category"),
        rx.select(["All Statuses", *PAYMENT_STATUSES],
                  on_change=E.set_filter_status, placeholder="All Statuses",
                  id="filter-status"),
        rx.select(["All Verifications", *VERIFICATIONS],
                  on_change=E.set_filter_verification,
                  placeholder="All Verifications",
                  id="filter-verification"),
        rx.input(rx.input.slot(rx.icon("search", size=14)),
                 value=E.search_text, on_change=E.set_search_text,
                 placeholder="Search",
                 id="filter-search", min_width="14em", flex="1"),
        rx.cond(
            E.filter_review,
            rx.button("Needs Review", rx.icon("x", size=12), size="1",
                      variant="soft", color_scheme="amber",
                      on_click=E.clear_needs_review,
                      id="needs-review-clear"),
        ),
        rx.hstack(
            rx.text("Sort", size="2", color=ds.TEXT_MUTED),
            rx.select(SORT_OPTIONS, value=E.sort_label,
                      on_change=E.set_sort_key, id="filter-sort"),
            rx.icon_button(
                rx.cond(E.sort_desc, rx.icon("arrow-down", size=14),
                        rx.icon("arrow-up", size=14)),
                variant="soft", on_click=E.toggle_sort_dir, id="sort-dir"),
            spacing="2", align="center"),
        spacing="3", align="center", wrap="wrap", width="100%",
    )


# Paid by / Receipt ref / Recorded by hide at 768-1535px (all in the
# dialog) so the table fits at desktop widths; Edit is pinned either way
_REG_COLS = ["Incurred", "Paid", "Category", "Description", ("Amount", True),
             ("Paid By", False, True), "Status",
             ("Receipt Ref", False, True), ""]


def _register_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["incurred"], class_name="ph-hide"),
              rx.text(row["incurred_short"], class_name="ph-only")),
        ui.td(row["paid"]),
        ui.td(row["category"]),
        ui.td(rx.text(row["description"], size="2"),
              # the Category column is off on phones: muted second line
              rx.text(row["category"], size="1", color=ds.TEXT_FAINT,
                      class_name="ph-only",
                      # ellipsis without widening the column: zero width
                      # for the table's minimum, then fill the cell
                      style={"whiteSpace": "nowrap", "overflow": "hidden",
                             "textOverflow": "ellipsis",
                             "overflowWrap": "normal", "width": "0",
                             "minWidth": "100%"}),
              wrap=True,
              title=row["description"],
              style={"minWidth": "140px", "maxWidth": "220px",
                     ui.PHONE: {"minWidth": "0"}}),
        # phones: compact money (exact value in the tooltip)
        ui.td(rx.text(row["amount"], class_name="ph-hide"),
              rx.tooltip(rx.text(row["amount_compact"], class_name="ph-only"),
                         content=row["amount"]),
              numeric=True),
        ui.td(row["paid_by"], xl_only=True),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(row["receipt"], xl_only=True),
        ui.td(rx.cond(
            E.can_write_expenses,
            rx.button(rx.icon("pencil", size=13, class_name="ph-hide"),
                      "Edit", size="1", variant="soft",
                      on_click=E.open_expense_form(row["id"])),
            rx.button(rx.icon("eye", size=13, class_name="ph-hide"),
                      "View", size="1",
                      variant="ghost",
                      on_click=E.open_expense_form(row["id"])),
        )),
    )


def _register() -> rx.Component:
    return ui.section_card(
        _filters(),
        rx.cond(
            E.total_rows == "0",
            ui.empty_state("No Transactions", icon="receipt"),
            ui.data_table(_REG_COLS,
                          rx.foreach(E.expense_rows, _register_row),
                          phone=("Incurred", "Description", "Amount",
                                 "Status", ""),
                          phone_labels={"Incurred": "Date"}),
        ),
        title="Transactions",
        extra=rx.cond(
            E.can_write_expenses,
            rx.button(rx.icon("plus", size=14), "Add Expense",
                      on_click=E.open_expense_form(""), id="add-expense"),
        ),
        id="expense-register",
    )


# ---------------------------------------------------------------------------
# Expense dialog (kit entry dialog, same pattern as Daily Operations)
# ---------------------------------------------------------------------------

def _expense_dialog() -> rx.Component:
    ro = ~E.can_write_expenses
    return ui.form_dialog(
        ui.form_section(
            "Transaction",
            ui.form_grid(
                ui.form_field("Incurred Date",
                              rx.input(type="date", value=E.e_incurred,
                                       on_change=E.set_e_incurred,
                                       id="e-incurred", disabled=ro)),
                ui.form_field("Paid Date",
                              rx.input(type="date", value=E.e_paid,
                                       on_change=E.set_e_paid, id="e-paid",
                                       disabled=ro)),
                ui.form_field("Category",
                              rx.select(E.category_names,
                                        value=E.e_category,
                                        on_change=E.set_e_category,
                                        id="e-category", disabled=ro)),
                ui.form_field("Amount (RM)",
                              rx.input(value=E.e_amount,
                                       on_change=E.set_e_amount,
                                       input_mode="decimal", id="e-amount",
                                       disabled=ro)),
            ),
            ui.form_field("Description",
                          rx.text_area(value=E.e_description,
                                       on_change=E.set_e_description,
                                       id="e-description", disabled=ro)),
        ),
        ui.form_section(
            "Payment",
            ui.form_grid(
                ui.form_field("Status",
                              rx.select(list(PAYMENT_STATUSES),
                                        value=E.e_status,
                                        on_change=E.set_e_status,
                                        id="e-status", disabled=ro)),
                ui.form_field("Paid By",
                              rx.select(list(PAYMENT_SOURCES),
                                        value=E.e_paid_by,
                                        on_change=E.set_e_paid_by,
                                        id="e-paid-by", disabled=ro)),
                ui.form_field("Verification",
                              rx.select(list(VERIFICATIONS),
                                        value=E.e_verification,
                                        on_change=E.set_e_verification,
                                        id="e-verification", disabled=ro)),
                ui.form_field("Receipt Ref",
                              rx.input(value=E.e_receipt,
                                       on_change=E.set_e_receipt,
                                       id="e-receipt", disabled=ro)),
                ui.form_field("Recorded By",
                              rx.input(value=E.e_recorded_by,
                                       on_change=E.set_e_recorded_by,
                                       id="e-recorded-by", disabled=ro)),
            ),
            ui.form_field("Remarks",
                          rx.text_area(value=E.e_remarks,
                                       on_change=E.set_e_remarks,
                                       id="e-remarks", disabled=ro)),
        ),
        open=E.exp_form_open,
        on_open_change=lambda v: E.close_expense_form(),
        title=rx.cond(E.editing_id == "", "Add Expense", "Edit Expense"),
        close_id="exp-close",
        notices=[
            rx.cond(ro, rx.callout(
                "You have read-only access. Contact an administrator to "
                "modify expenses.",
                icon="lock", color_scheme="gray", variant="soft",
                width="100%")),
            rx.cond(E.exp_form_error != "",
                    ds.error_callout(E.exp_form_error)),
        ],
        actions=ui.dialog_actions(
            save_label="Save Expense", on_save=E.save_expense,
            saving=E.exp_saving, save_id="exp-save", cancel_id="exp-cancel",
            can_save=E.can_write_expenses),
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
    )
