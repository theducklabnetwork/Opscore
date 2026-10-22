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


# phones scroll sideways with Edit pinned; Pillar hides at 768-1535px
_COLS = ["Raised", ("Pillar", False, True), "Issue",
         "Priority",
         "Status",
         "Person in charge", "Due", "Completed",
         ""]


def _row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(row["raised"]),
        ui.td(row["pillar"], xl_only=True),
        ui.td(rx.vstack(
            rx.text(row["issue"], size="2"),
            rx.cond(row["carried"],
                    rx.badge("Carried over", size="1", variant="outline",
                             color_scheme="violet", radius="full")),
            spacing="1", align="start"),
            wrap=True, style={"minWidth": "min(220px, 42vw)"}),
        ui.td(ui.status_chip(row["priority"], row["priority_color"])),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(row["pic"]),
        ui.td(rx.hstack(
            rx.text(row["due"], size="2"),
            rx.cond(row["overdue"], ui.status_chip("Overdue", "red")),
            spacing="2", align="center")),
        ui.td(row["completion"]),
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
            ui.data_table(_COLS, rx.foreach(A.filtered_rows, _row),
                          hidden_note="Pillar shows from 1536px wide and "
                                      "in each action's Edit dialog."),
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
# 3. Add/Edit dialog (kit entry dialog, like Daily Operations)
# ---------------------------------------------------------------------------

def _dialog() -> rx.Component:
    return ui.form_dialog(
        ui.form_section(
            "Issue",
            ui.form_field("Issue / observation",
                          rx.text_area(value=A.a_issue,
                                       on_change=A.set_a_issue,
                                       id="a-issue", rows="3"),
                          required=True),
            ui.form_grid(
                ui.form_field("Pillar",
                              rx.select(PILLARS, value=A.a_pillar,
                                        on_change=A.set_a_pillar,
                                        id="a-pillar")),
                ui.form_field("Priority",
                              rx.select(PRIORITIES, value=A.a_priority,
                                        on_change=A.set_a_priority,
                                        id="a-priority")),
                ui.form_field("Status",
                              rx.select(ACTION_STATUSES, value=A.a_status,
                                        on_change=A.set_a_status,
                                        id="a-status")),
                columns="3"),
        ),
        ui.form_section(
            "Owner & dates",
            ui.form_grid(
                ui.form_field("Person in charge",
                              rx.input(value=A.a_pic, on_change=A.set_a_pic,
                                       id="a-pic"),
                              required=True),
                ui.form_field("Raised date",
                              rx.input(type="date", value=A.a_raised,
                                       on_change=A.set_a_raised,
                                       id="a-raised")),
                ui.form_field("Due date",
                              rx.input(type="date", value=A.a_due,
                                       on_change=A.set_a_due, id="a-due"),
                              required=True),
                rx.cond(
                    A.a_status == "Done",
                    ui.form_field("Completion date",
                                  rx.input(type="date", value=A.a_completion,
                                           on_change=A.set_a_completion,
                                           id="a-completion"),
                                  "Defaults to today.")),
                columns="3"),
        ),
        ui.form_section(
            "Follow-up",
            ui.form_field("Corrective action / next step",
                          rx.text_area(value=A.a_corrective,
                                       on_change=A.set_a_corrective,
                                       id="a-corrective", rows="2")),
            ui.form_field("Remarks",
                          rx.text_area(value=A.a_remarks,
                                       on_change=A.set_a_remarks,
                                       id="a-remarks", rows="2")),
        ),
        open=A.form_open,
        on_open_change=A.set_form_open,
        on_escape_key_down=A.set_form_open(False),
        title=A.form_title,
        subtitle="Person in charge and due date are required.",
        close_id="a-close",
        notices=[
            rx.cond(~A.can_write_actions, rx.callout(
                "You have read-only access. Contact an administrator to "
                "modify actions.",
                icon="lock", color_scheme="gray", variant="soft",
                width="100%")),
            rx.cond(A.form_error != "", ds.error_callout(A.form_error)),
        ],
        actions=ui.dialog_actions(
            save_label="Save Action", on_save=A.save_action,
            saving=A.saving, save_id="a-save", cancel_id="a-cancel",
            can_save=A.can_write_actions),
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
