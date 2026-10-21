"""Store Management + Monthly Setup pages at /ops/stores."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab.ops_shell import shell
from ducklab.ops_stores_state import (
    SETUP_MANPOWER, SETUP_PAYROLL, SETUP_SALES, SETUP_STOCK, StoresState,
)


# ----------------------------------------------------------------------
# Stores table
# ----------------------------------------------------------------------
def _store_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(rx.text(row["code"], weight="bold", size="2")),
        rx.table.cell(row["name"]),
        rx.table.cell(row["concept"]),
        rx.table.cell(row["zone"]),
        rx.table.cell(row["zone_pic"]),
        rx.table.cell(row["branch_manager"]),
        rx.table.cell(
            rx.badge(row["active_label"],
                     color_scheme=rx.cond(row["active"] == "1", "green", "gray"),
                     variant="soft", size="1")),
        rx.table.cell(
            rx.hstack(
                rx.button("Edit", size="1", variant="soft",
                          on_click=StoresState.open_store_form(row["id"]),
                          disabled=~StoresState.can_manage_stores,
                          id=f"store-edit-{row['code']}"),
                rx.button(rx.cond(row["active"] == "1", "Deactivate", "Activate"),
                          size="1", variant="soft", color_scheme="amber",
                          on_click=StoresState.toggle_store_active(row["id"]),
                          disabled=~StoresState.can_manage_stores,
                          id=f"store-toggle-{row['code']}"),
                rx.button("Users", size="1", variant="soft",
                          color_scheme="indigo",
                          on_click=StoresState.open_assign(row["id"]),
                          disabled=~StoresState.can_assign_users,
                          id=f"store-users-{row['code']}"),
                spacing="2"),
        ),
    )


def _stores_table() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            ds.section_heading("Stores", "store"),
            rx.spacer(),
            rx.button(rx.icon("plus", size=14), "New Store",
                      color_scheme="indigo", size="2",
                      on_click=StoresState.open_store_form(""),
                      disabled=~StoresState.can_manage_stores,
                      id="store-new"),
            width="100%", align="center",
        ),
        rx.cond(
            StoresState.store_rows.length() > 0,
            rx.scroll_area(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Code"),
                            rx.table.column_header_cell("Name"),
                            rx.table.column_header_cell("Concept"),
                            rx.table.column_header_cell("Zone"),
                            rx.table.column_header_cell("Zone PIC"),
                            rx.table.column_header_cell("Branch Manager"),
                            rx.table.column_header_cell("Status"),
                            rx.table.column_header_cell(""),
                        )),
                    rx.table.body(
                        rx.foreach(StoresState.store_rows, _store_row)),
                    variant="surface", width="100%",
                ),
                type="auto", scrollbars="horizontal",
                style={"maxWidth": "100%"},
            ),
            ds.empty_state("No stores", "Create your first store to begin.",
                           icon="store"),
        ),
        spacing="3", width="100%",
    )


def _store_form_dialog() -> rx.Component:
    def _input(label: str, state_value, setter, fid: str,
               placeholder: str = "") -> rx.Component:
        return rx.vstack(
            rx.text(label, size="1", color=ds.TEXT_MUTED),
            rx.input(value=state_value, on_change=setter,
                     placeholder=placeholder, id=fid, width="100%"),
            spacing="1", align="start", width="100%")

    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                rx.hstack(
                    rx.heading(rx.cond(StoresState.sf_editing != "",
                                       "Edit Store", "New Store"), size="4"),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.icon_button(rx.icon("x", size=16), variant="ghost",
                                       id="store-form-close")),
                    width="100%", align="center"),
                rx.cond(StoresState.sf_error != "",
                        ds.error_callout(StoresState.sf_error)),
                rx.grid(
                    _input("Code *", StoresState.sf_code,
                           StoresState.set_sf_code, "sf-code",
                           "e.g. A01"),
                    _input("Name *", StoresState.sf_name,
                           StoresState.set_sf_name, "sf-name"),
                    _input("Concept", StoresState.sf_concept,
                           StoresState.set_sf_concept, "sf-concept"),
                    _input("Zone", StoresState.sf_zone,
                           StoresState.set_sf_zone, "sf-zone"),
                    _input("Zone PIC", StoresState.sf_zone_pic,
                           StoresState.set_sf_zone_pic, "sf-zone-pic"),
                    _input("Branch Manager", StoresState.sf_branch_manager,
                           StoresState.set_sf_branch_manager, "sf-bm"),
                    columns=rx.breakpoints(initial="1", sm="2"),
                    spacing="3", width="100%",
                ),
                rx.text("Deactivation preserves all historical records.",
                        size="1", color=ds.TEXT_FAINT),
                rx.hstack(
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button("Cancel", variant="soft",
                                  color_scheme="gray", id="store-form-cancel")),
                    rx.button(
                        rx.cond(StoresState.sf_saving, rx.spinner(size="1"),
                                rx.icon("save", size=14)),
                        "Save Store", color_scheme="indigo",
                        on_click=StoresState.save_store,
                        disabled=StoresState.sf_saving,
                        id="store-form-save"),
                    width="100%", align="center"),
                spacing="4", width="100%",
            ),
            width="min(560px, 92vw)",
            style={"maxWidth": "min(560px, 92vw)"},
            background=ds.BG, padding="1.5em",
        ),
        open=StoresState.sf_open,
        on_open_change=lambda v: StoresState.close_store_form(),
    )


# ----------------------------------------------------------------------
# User assignment panel
# ----------------------------------------------------------------------
def _assign_panel() -> rx.Component:
    return rx.cond(
        StoresState.assign_store_id != "",
        rx.card(
            rx.vstack(
                ds.section_heading("User Assignment", "user-cog"),
                rx.text(
                    "Assign existing users to this store. User creation is "
                    "handled by an administrator elsewhere.",
                    size="1", color=ds.TEXT_FAINT),
                rx.cond(
                    StoresState.assign_rows.length() > 0,
                    rx.vstack(
                        rx.foreach(
                            StoresState.assign_rows,
                            lambda r: rx.hstack(
                                rx.text(r["label"], size="2"),
                                rx.cond(r["active"] == "0",
                                        rx.badge("disabled",
                                                 color_scheme="gray",
                                                 variant="soft", size="1")),
                                rx.spacer(),
                                rx.button("Unassign", size="1", variant="soft",
                                          color_scheme="red",
                                          on_click=StoresState.unassign_user(
                                              r["id"]),
                                          id=f"unassign-{r['id']}"),
                                width="100%", align="center")),
                        spacing="2", width="100%"),
                    rx.text("No users assigned yet.", size="2",
                            color=ds.TEXT_MUTED)),
                rx.hstack(
                    rx.select(
                        StoresState.assign_options,
                        value=StoresState.assign_selected,
                        on_change=StoresState.select_assign_user,
                        placeholder="Select an existing user…",
                        id="assign-select",
                    ),
                    rx.button("Assign", size="2", color_scheme="indigo",
                              on_click=StoresState.assign_user,
                              disabled=StoresState.assign_selected == "",
                              id="assign-submit"),
                    spacing="2", align="center", wrap="wrap"),
                spacing="3", align="start", width="100%",
            ),
            width="100%",
        ),
    )


# ----------------------------------------------------------------------
# Monthly Setup
# ----------------------------------------------------------------------
def _setup_field(spec) -> rx.Component:
    attr = spec[0]
    label = spec[3]
    fid = "setup-" + spec[1].replace("_", "-")
    return rx.vstack(
        rx.text(label, size="1", color=ds.TEXT_MUTED),
        rx.input(value=getattr(StoresState, attr),
                 on_change=getattr(StoresState, "set_" + attr),
                 placeholder="—", id=fid, width="100%",
                 disabled=~StoresState.can_edit_setup),
        spacing="1", align="start", width="100%")


def _setup_group(title: str, icon: str, fields) -> rx.Component:
    return rx.vstack(
        ds.section_heading(title, icon),
        rx.grid(
            *[_setup_field(spec) for spec in fields],
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            spacing="3", width="100%",
        ),
        spacing="2", align="start", width="100%")


def _levels_panel() -> rx.Component:
    return rx.cond(
        StoresState.setup_levels.length() > 0,
        rx.hstack(
            rx.foreach(
                StoresState.setup_levels,
                lambda lv: rx.card(
                    rx.vstack(
                        rx.text(lv["level"], size="1", color=ds.TEXT_MUTED),
                        rx.text(lv["value"], size="3", weight="bold"),
                        spacing="0", align="start"),
                    size="1")),
            spacing="3", wrap="wrap"),
        rx.text("Calculated Level 1–4 targets appear here once Level 1 base "
                "is saved.", size="1", color=ds.TEXT_FAINT),
    )


def _budget_row(row) -> rx.Component:
    return rx.vstack(
        rx.text(row["name"], size="1", color=ds.TEXT_MUTED),
        rx.input(value=row["value"],
                 on_change=lambda v: StoresState.set_budget_value(row["id"], v),
                 placeholder="—", width="100%",
                 id=f"setup-budget-{row['id']}",
                 disabled=~StoresState.can_edit_setup),
        spacing="1", align="start", width="100%")


def _setup_section() -> rx.Component:
    return rx.vstack(
        ds.section_heading("Monthly Setup", "settings-2"),
        rx.text(
            "Blank = not set (stored as NULL). An explicit 0 is stored as 0. "
            "Targets and budgets feed the Stock, Manpower, Expenses and "
            "Monthly Report pages.",
            size="1", color=ds.TEXT_FAINT),
        rx.cond(
            StoresState.setup_finalized,
            rx.callout(
                "A finalized snapshot exists for this month. Edits change "
                "live configuration only — the finalized report is "
                "unchanged.",
                icon="lock", color_scheme="amber", variant="soft",
                width="100%")),
        rx.cond(StoresState.setup_notice != "",
                rx.callout(StoresState.setup_notice, icon="check",
                           color_scheme="green", variant="soft",
                           width="100%")),
        rx.cond(StoresState.setup_error != "",
                ds.error_callout(StoresState.setup_error)),
        _setup_group("Sales Targets", "trending-up", SETUP_SALES),
        _levels_panel(),
        rx.divider(),
        _setup_group("Manpower", "users", SETUP_MANPOWER),
        rx.divider(),
        _setup_group("Stock", "boxes", SETUP_STOCK),
        rx.divider(),
        ds.section_heading("Expense Budgets", "receipt"),
        rx.cond(
            StoresState.budget_rows.length() > 0,
            rx.grid(
                rx.foreach(StoresState.budget_rows, _budget_row),
                columns=rx.breakpoints(initial="1", sm="2", lg="3"),
                spacing="3", width="100%"),
            rx.text("No expense categories configured for this store.",
                    size="1", color=ds.TEXT_FAINT)),
        rx.divider(),
        _setup_group("Month-End Inputs", "calculator", SETUP_PAYROLL),
        rx.hstack(
            rx.button(
                rx.cond(StoresState.setup_saving, rx.spinner(size="1"),
                        rx.icon("save", size=14)),
                "Save Setup", color_scheme="indigo",
                on_click=StoresState.save_setup,
                disabled=StoresState.setup_saving
                | ~StoresState.can_edit_setup,
                id="setup-save"),
            rx.cond(~StoresState.can_edit_setup,
                    rx.text("You have read-only access for this store.",
                            size="1", color=ds.TEXT_FAINT)),
            spacing="3", align="center"),
        spacing="4", align="start", width="100%",
    )


# ----------------------------------------------------------------------
# Page
# ----------------------------------------------------------------------
def stores_page() -> rx.Component:
    return shell(
        "Store Management",
        rx.fragment(
            rx.vstack(
                rx.cond(
                    StoresState.stores_notice != "",
                    rx.callout(StoresState.stores_notice, icon="check",
                               color_scheme="green", variant="soft",
                               width="100%")),
                rx.cond(
                    StoresState.stores_error != "",
                    ds.error_callout(StoresState.stores_error)),
                _stores_table(),
                _assign_panel(),
                rx.divider(),
                _setup_section(),
                spacing="4", width="100%",
            ),
            _store_form_dialog(),
        ),
        on_store=StoresState.select_store,
        on_month=StoresState.select_month,
    )
