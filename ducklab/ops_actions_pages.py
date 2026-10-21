"""Action Tracker page: cards, filters, actions table, Add/Edit dialog."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab.ops_actions_state import (
    FILTER_PILLARS, FILTER_PRIORITIES, FILTER_STATUSES, PILLARS, PRIORITIES,
    ACTION_STATUSES, ActionsState,
)
from ducklab.ops_shell import shell


# ---------------------------------------------------------------------------
# 1. Cards
# ---------------------------------------------------------------------------

def _cards() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Actions Overview", "list-checks"),
            rx.grid(
                ds.kpi_card("Open (incl. In progress, Blocked)",
                            ActionsState.n_open, icon="circle-dot"),
                rx.card(
                    rx.vstack(
                        rx.hstack(
                            rx.icon("alarm-clock", size=16,
                                    color=ds.ERROR),
                            rx.text("Overdue", size="2",
                                    color=ds.TEXT_MUTED, weight="medium"),
                            spacing="2", align="center"),
                        rx.heading(ActionsState.n_overdue, size="6"),
                        rx.cond(
                            ActionsState.n_overdue != "0",
                            rx.badge("Needs attention", color_scheme="red",
                                     variant="soft", size="1",
                                     id="overdue-attention")),
                        spacing="1", align="start"),
                    id="card-overdue"),
                ds.kpi_card("Completed", ActionsState.n_done,
                            icon="circle-check", accent="green"),
                columns=rx.breakpoints(initial="1", sm="3"),
                spacing="3", width="100%",
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="action-cards",
    )


# ---------------------------------------------------------------------------
# 2. Filters + table
# ---------------------------------------------------------------------------

def _filters() -> rx.Component:
    return rx.hstack(
        rx.select(FILTER_STATUSES, value=ActionsState.f_status,
                  on_change=ActionsState.set_f_status, size="2",
                  id="f-status"),
        rx.select(FILTER_PILLARS, value=ActionsState.f_pillar,
                  on_change=ActionsState.set_f_pillar, size="2",
                  id="f-pillar"),
        rx.select(FILTER_PRIORITIES, value=ActionsState.f_priority,
                  on_change=ActionsState.set_f_priority, size="2",
                  id="f-priority"),
        rx.hstack(
            rx.checkbox(checked=ActionsState.f_overdue_only,
                        on_change=ActionsState.set_f_overdue_only,
                        id="f-overdue"),
            rx.text("Overdue only", size="2"),
            spacing="2", align="center"),
        rx.spacer(),
        rx.cond(
            ActionsState.can_write_actions,
            rx.button(rx.icon("plus", size=14), "Add Action",
                      color_scheme="indigo", on_click=ActionsState.open_add,
                      id="add-action")),
        wrap="wrap", spacing="3", width="100%", align="center",
    )


def _row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(row["raised"]),
        rx.table.cell(row["pillar"]),
        rx.table.cell(
            rx.vstack(
                rx.text(row["issue"], size="2"),
                rx.cond(
                    row["carried"],
                    rx.badge("Carried over", size="1", variant="outline",
                             color_scheme="violet")),
                spacing="1", align="start"),
            min_width="220px"),
        rx.table.cell(rx.badge(row["priority"],
                               color_scheme=row["priority_color"],
                               variant="soft", size="1")),
        rx.table.cell(rx.badge(row["status"],
                               color_scheme=row["status_color"],
                               variant="soft", size="1")),
        rx.table.cell(row["pic"]),
        rx.table.cell(
            rx.hstack(
                rx.text(row["due"], size="2"),
                rx.cond(row["overdue"],
                        rx.badge("Overdue", color_scheme="red", size="1",
                                 variant="soft")),
                spacing="2", align="center")),
        rx.table.cell(row["completion"]),
        rx.table.cell(
            rx.cond(
                ActionsState.can_write_actions,
                rx.button("Edit", size="1", variant="soft",
                          on_click=lambda: ActionsState.open_edit(row["id"]),
                          class_name="act-edit"))),
    )


def _table() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Actions", "clipboard-list"),
            rx.text(
                "Shows actions raised in the selected month plus any open "
                "action raised earlier (carried-over lines are labelled). "
                "Actions are never part of finalized-month snapshots.",
                size="1", color=ds.TEXT_FAINT),
            rx.cond(
                ActionsState.loaded & (ActionsState.rows.length() > 0),
                rx.scroll_area(
                    rx.table.root(
                        rx.table.header(rx.table.row(
                            rx.table.column_header_cell("Raised"),
                            rx.table.column_header_cell("Pillar"),
                            rx.table.column_header_cell("Issue"),
                            rx.table.column_header_cell("Priority"),
                            rx.table.column_header_cell("Status"),
                            rx.table.column_header_cell("Person in charge"),
                            rx.table.column_header_cell("Due"),
                            rx.table.column_header_cell("Completed"),
                            rx.table.column_header_cell(""),
                        )),
                        rx.table.body(
                            rx.foreach(ActionsState.filtered_rows, _row)),
                        variant="surface", width="100%",
                    ),
                    type="auto", scrollbars="horizontal",
                    style={"maxWidth": "100%"},
                ),
                ds.empty_state("No actions for this month",
                               "Raise a follow-up with the Add Action button; "
                               "open items from earlier months appear here "
                               "automatically.", icon="list-checks"),
            ),
            rx.cond(ActionsState.notice != "",
                    rx.callout(ActionsState.notice, icon="circle-check",
                               color_scheme="green", variant="soft",
                               id="act-notice")),
            spacing="3", width="100%",
        ),
        width="100%", id="actions-table",
    )


# ---------------------------------------------------------------------------
# 3. Add/Edit dialog (centered pattern, like Daily Operations)
# ---------------------------------------------------------------------------

def _field(label, control, required=False) -> rx.Component:
    return rx.vstack(
        rx.hstack(rx.text(label, size="1", color=ds.TEXT_MUTED),
                  rx.cond(required,
                          rx.text("*", color=ds.ERROR, size="1")),
                  spacing="1"),
        control,
        spacing="1", align="start", width="100%")


def _dialog() -> rx.Component:
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.hstack(
                    rx.heading(ActionsState.form_title, size="4"),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.icon_button(rx.icon("x", size=16),
                                       variant="ghost", id="a-close")),
                    width="100%", align="center", flex_shrink="0"),
                rx.cond(
                    ~ActionsState.can_write_actions,
                    rx.callout(
                        "You have read-only access. Contact an "
                        "administrator to modify actions.",
                        icon="lock", color_scheme="gray", variant="soft",
                        width="100%"),
                ),
                rx.cond(ActionsState.form_error != "",
                        ds.error_callout(ActionsState.form_error)),
                rx.scroll_area(
                    rx.vstack(
                        _field("Issue / observation",
                               rx.text_area(value=ActionsState.a_issue,
                                            on_change=ActionsState.set_a_issue,
                                            id="a-issue", rows="3"),
                               required=True),
                        rx.grid(
                            _field("Pillar",
                                   rx.select(PILLARS,
                                             value=ActionsState.a_pillar,
                                             on_change=ActionsState.set_a_pillar,
                                             id="a-pillar")),
                            _field("Priority",
                                   rx.select(PRIORITIES,
                                             value=ActionsState.a_priority,
                                             on_change=ActionsState.set_a_priority,
                                             id="a-priority")),
                            _field("Status",
                                   rx.select(ACTION_STATUSES,
                                             value=ActionsState.a_status,
                                             on_change=ActionsState.set_a_status,
                                             id="a-status")),
                            columns=rx.breakpoints(initial="1", sm="3"),
                            spacing="3", width="100%"),
                        _field("Person in charge",
                               rx.input(value=ActionsState.a_pic,
                                        on_change=ActionsState.set_a_pic,
                                        id="a-pic"),
                               required=True),
                        rx.grid(
                            _field("Raised date",
                                   rx.input(type="date",
                                            value=ActionsState.a_raised,
                                            on_change=ActionsState.set_a_raised,
                                            id="a-raised")),
                            _field("Due date",
                                   rx.input(type="date",
                                            value=ActionsState.a_due,
                                            on_change=ActionsState.set_a_due,
                                            id="a-due"),
                                   required=True),
                            rx.cond(
                                ActionsState.a_status == "Done",
                                _field("Completion date (defaults to today)",
                                       rx.input(type="date",
                                                value=ActionsState.a_completion,
                                                on_change=ActionsState.set_a_completion,
                                                id="a-completion"))),
                            columns=rx.breakpoints(initial="1", sm="3"),
                            spacing="3", width="100%"),
                        _field("Corrective action / next step",
                               rx.text_area(value=ActionsState.a_corrective,
                                            on_change=ActionsState.set_a_corrective,
                                            id="a-corrective", rows="2")),
                        _field("Remarks",
                               rx.text_area(value=ActionsState.a_remarks,
                                            on_change=ActionsState.set_a_remarks,
                                            id="a-remarks", rows="2")),
                        spacing="4", width="100%",
                    ),
                    flex_grow="1", min_height="0", type="auto", width="100%",
                ),
                rx.hstack(
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button("Cancel", variant="soft",
                                  color_scheme="gray", id="a-cancel")),
                    rx.cond(
                        ActionsState.can_write_actions,
                        rx.button(
                            rx.cond(ActionsState.saving,
                                    rx.spinner(size="1"),
                                    rx.icon("save", size=14)),
                            "Save Action", color_scheme="indigo",
                            on_click=ActionsState.save_action,
                            disabled=ActionsState.saving,
                            id="a-save")),
                    width="100%", align="center", flex_shrink="0",
                    padding_top="2"),
                spacing="4", width="100%", height="100%",
            ),
            style={"maxWidth": "min(720px, 92vw)", "width": "100%",
                   "maxHeight": "85vh", "display": "flex"},
            on_escape_key_down=ActionsState.set_form_open(False),
        ),
        open=ActionsState.form_open,
        on_open_change=ActionsState.set_form_open,
    )


def actions_page() -> rx.Component:
    return shell(
        "Action Tracker",
        rx.vstack(
            rx.text(
                "Follow-up actions per store. Owner and due date are "
                "required for accountability; Overdue = due date before "
                "today and not Done/Cancelled.",
                size="2", color=ds.TEXT_MUTED),
            rx.cond(
                ActionsState.loaded,
                rx.vstack(
                    _cards(),
                    _filters(),
                    _table(),
                    spacing="5", width="100%",
                ),
            ),
            _dialog(),
            spacing="4", width="100%",
        ),
        on_store=ActionsState.select_store,
        on_month=ActionsState.select_month,
    )
