"""Action Tracker page: cards, filters, actions table, Add/Edit dialog."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.ops_actions_state import (
    FILTER_PILLARS, FILTER_PRIORITIES, FILTER_STATUSES, PILLARS, PRIORITIES,
    ACTION_STATUSES, ActionsState,
)
from ducklab.ops_shell import shell


A = ActionsState


# ---------------------------------------------------------------------------
# 1. Tiles: Open, Overdue, Completed
# ---------------------------------------------------------------------------

def _cards() -> rx.Component:
    # keep the open count the first number in #action-cards (suite parses it)
    return ui.kpi_grid(
        ui.kpi_tile("Open", A.n_open, sub="Incl. In progress and Blocked"),
        rx.box(
            ui.kpi_tile("Overdue", A.n_overdue,
                        chip=(rx.cond(A.n_overdue != "0", "Needs attention",
                                      ""), "red"),
                        chip_id="overdue-attention",
                        sub="Due before today, not Done/Cancelled"),
            id="card-overdue", height="100%"),
        ui.kpi_tile("Completed", A.n_done),
        columns=rx.breakpoints(initial="3"),
        id="action-cards",
    )


# ---------------------------------------------------------------------------
# 2. Filters (one row) + actions table
# ---------------------------------------------------------------------------

def _filters() -> rx.Component:
    return rx.hstack(
        rx.select(FILTER_STATUSES, value=A.f_status,
                  on_change=A.set_f_status, size="2", id="f-status"),
        rx.select(FILTER_PILLARS, value=A.f_pillar,
                  on_change=A.set_f_pillar, size="2", id="f-pillar"),
        rx.select(FILTER_PRIORITIES, value=A.f_priority,
                  on_change=A.set_f_priority, size="2", id="f-priority"),
        rx.hstack(
            rx.checkbox(checked=A.f_overdue_only,
                        on_change=A.set_f_overdue_only, id="f-overdue"),
            rx.text("Overdue only", size="2"),
            spacing="2", align="center"),
        wrap="wrap", spacing="3", width="100%", align="center",
    )


# phones keep Issue, Priority, Status and Due
_COLS = [("Raised", False, True), ("Pillar", False, True, True), "Issue",
         "Priority",
         "Status",
         ("Person in charge", False, True), "Due", ("Completed", False, True),
         ""]


def _row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(row["raised"], wide_only=True),
        ui.td(row["pillar"], wide_only=True, xl_only=True),
        ui.td(rx.vstack(
            rx.text(row["issue"], size="2"),
            rx.cond(row["carried"],
                    rx.badge("Carried over", size="1", variant="outline",
                             color_scheme="violet", radius="full")),
            spacing="1", align="start"),
            wrap=True, style={"minWidth": "min(220px, 42vw)"}),
        ui.td(ui.status_chip(row["priority"], row["priority_color"])),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(row["pic"], wide_only=True),
        ui.td(rx.hstack(
            rx.text(row["due"], size="2"),
            rx.cond(row["overdue"], ui.status_chip("Overdue", "red")),
            spacing="2", align="center")),
        ui.td(row["completion"], wide_only=True),
        ui.td(rx.cond(
            A.can_write_actions,
            rx.button("Edit", size="1", variant="soft",
                      on_click=lambda: A.open_edit(row["id"]),
                      class_name="act-edit"))),
    )


def _table() -> rx.Component:
    return ui.section_card(
        _filters(),
        rx.cond(
            A.loaded & (A.rows.length() > 0),
            ui.data_table(_COLS, rx.foreach(A.filtered_rows, _row)),
            ui.empty_state("No actions for this month",
                           "Raise a follow-up with Add action; open items "
                           "from earlier months appear here automatically.",
                           icon="list-checks"),
        ),
        rx.cond(A.notice != "",
                rx.callout(A.notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           id="act-notice")),
        title="Actions",
        subtitle="Raised this month plus open actions from earlier months "
                 "(labelled Carried over). Never part of finalized "
                 "snapshots.",
        extra=rx.cond(
            A.can_write_actions,
            rx.button(rx.icon("plus", size=14), "Add action",
                      on_click=A.open_add, id="add-action")),
        id="actions-table",
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
            rx.cond(
                A.loaded,
                rx.vstack(_cards(), _table(), spacing=ui.GAP, width="100%"),
            ),
            _dialog(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=A.select_store,
        on_month=A.select_month,
        description="Follow-up actions per store. Owner and due date are "
                    "required; overdue = due before today and not "
                    "Done/Cancelled.",
    )
