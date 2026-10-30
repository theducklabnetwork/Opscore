"""Store Management + Monthly Setup pages at /ops/stores."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.ops_shell import shell
from ducklab.operations_write_repository import CONCEPTS, STATES
from ducklab.ops_setup_state import (
    SETUP_DAILY, SETUP_MANPOWER, SETUP_PAYROLL, SETUP_SALES, SETUP_STOCK,
)
from ducklab.ops_assignment_state import AssignmentState
from ducklab.ops_setup_state import SetupState
from ducklab.ops_stores_state import StoresState

Stores = StoresState
Setup = SetupState
Assign = AssignmentState


# ----------------------------------------------------------------------
# Stores table
# ----------------------------------------------------------------------
def _store_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["code"], weight="medium")),
        ui.td(row["name"]),
        ui.td(row["concept"]),
        ui.td(row["state"]),
        ui.td(row["zone"]),
        ui.td(row["zone_pic"]),
        ui.td(row["branch_manager"]),
        ui.td(ui.status_chip(row["active_label"],
                             rx.cond(row["active"] == "1", "green", "gray"))),
        ui.td(rx.hstack(
            rx.button("Edit", size="1", variant="soft",
                      on_click=Stores.open_store_form(row["id"]),
                      disabled=~Stores.can_manage_stores,
                      id=f"store-edit-{row['code']}"),
            rx.button(rx.cond(row["active"] == "1", "Deactivate", "Activate"),
                      size="1", variant="soft", color_scheme="gray",
                      on_click=Stores.toggle_store_active(row["id"]),
                      disabled=~Stores.can_manage_stores,
                      id=f"store-toggle-{row['code']}"),
            rx.button("Users", size="1", variant="soft",
                      on_click=Assign.open_assign(row["id"]),
                      disabled=~Assign.can_assign_users,
                      id=f"store-users-{row['code']}"),
            spacing="2")),
    )


def _stores_table() -> rx.Component:
    return ui.section_card(
        rx.cond(
            Stores.store_rows.length() > 0,
            ui.data_table(
                ["Code", "Name", "Concept", "State", "Zone", "Zone PIC",
                 "Branch Manager", "Status", "Actions"],
                rx.foreach(Stores.store_rows, _store_row),
                phone=("Code", "Status", "Actions"),
            ),
            ui.empty_state("No Stores", "Create your first store to begin.",
                           icon="store"),
        ),
        ui.note("Deactivation preserves all historical records."),
        title="Stores",
        extra=rx.button(rx.icon("plus", size=14), "New Store",
                        on_click=Stores.open_store_form(""),
                        disabled=~Stores.can_manage_stores, id="store-new"),
        id="stores-table",
    )


def _sf_field(label, value, setter, fid, placeholder="",
              required=False) -> rx.Component:
    return ui.form_field(label, rx.input(value=value, on_change=setter,
                                         placeholder=placeholder, id=fid),
                         required=required)


def _store_form_dialog() -> rx.Component:
    return ui.form_dialog(
        ui.form_section(
            "Store",
            ui.form_grid(
                _sf_field("Code", Stores.sf_code, Stores.set_sf_code, "sf-code",
                          "e.g. A01", required=True),
                _sf_field("Name", Stores.sf_name, Stores.set_sf_name, "sf-name",
                          required=True),
                ui.form_field(
                    "Concept",
                    rx.select(list(CONCEPTS), value=Stores.sf_concept,
                              on_change=Stores.set_sf_concept,
                              placeholder="Select…", id="sf-concept"),
                    hint=Stores.sf_concept_hint),
                ui.form_field(
                    "State",
                    rx.select(list(STATES), value=Stores.sf_state,
                              on_change=Stores.set_sf_state,
                              placeholder="Select…", id="sf-state")),
            ),
        ),
        ui.form_section(
            "Area & People",
            ui.form_grid(
                _sf_field("Zone", Stores.sf_zone, Stores.set_sf_zone, "sf-zone"),
                _sf_field("Zone PIC", Stores.sf_zone_pic, Stores.set_sf_zone_pic,
                          "sf-zone-pic"),
                _sf_field("Branch Manager", Stores.sf_branch_manager,
                          Stores.set_sf_branch_manager, "sf-bm"),
            ),
        ),
        open=Stores.sf_open,
        on_open_change=lambda v: Stores.close_store_form(),
        title=rx.cond(Stores.sf_editing != "", "Edit Store", "New Store"),
        close_id="store-form-close",
        notices=[rx.cond(Stores.sf_error != "", ds.error_callout(Stores.sf_error))],
        actions=ui.dialog_actions(
            save_label="Save Store", on_save=Stores.save_store,
            saving=Stores.sf_saving, save_id="store-form-save",
            cancel_id="store-form-cancel"),
        width="600px",
    )


# ----------------------------------------------------------------------
# User assignment panel
# ----------------------------------------------------------------------
def _assigned_user(r) -> rx.Component:
    return rx.hstack(
        rx.text(r["label"], size="2", color=ds.TEXT),
        rx.cond(r["active"] == "0", ui.status_chip("Disabled", "gray")),
        rx.spacer(),
        rx.button("Unassign", size="1", variant="soft", color_scheme="red",
                  on_click=Assign.unassign_user(r["id"]),
                  id=f"unassign-{r['id']}"),
        width="100%", align="center", padding_y="0.5em",
        border_bottom=f"1px solid {ui.ROW_BORDER}",
    )


def _assign_panel() -> rx.Component:
    return rx.cond(
        Assign.assign_store_id != "",
        ui.section_card(
            rx.cond(
                Assign.assign_rows.length() > 0,
                rx.vstack(rx.foreach(Assign.assign_rows, _assigned_user),
                          spacing="0", width="100%"),
                rx.text("No users assigned yet.", size="2",
                        color=ds.TEXT_MUTED),
            ),
            rx.hstack(
                rx.select(Assign.assign_options, value=Assign.assign_selected,
                          on_change=Assign.select_assign_user,
                          placeholder="Select an existing user…",
                          id="assign-select"),
                rx.button("Assign", on_click=Assign.assign_user,
                          disabled=Assign.assign_selected == "",
                          id="assign-submit"),
                spacing="3", align="center", wrap="wrap"),
            title="User Assignment",
            subtitle="Assign existing users to this store.",
            id="assign-panel",
        ),
    )


# ----------------------------------------------------------------------
# Monthly Setup: one card, grouped sections, three equal columns
# ----------------------------------------------------------------------
def _setup_field(spec) -> rx.Component:
    attr, field, _kind, label = spec
    return ui.form_field(
        label,
        rx.input(value=getattr(Setup, attr),
                 on_change=getattr(Setup, "set_" + attr),
                 placeholder="—", id="setup-" + field.replace("_", "-"),
                 disabled=~Setup.can_edit_setup))


def _setup_grid(specs) -> rx.Component:
    return ui.form_grid(*[_setup_field(spec) for spec in specs], columns="3")


def _levels_panel() -> rx.Component:
    return rx.cond(
        Setup.setup_levels.length() > 0,
        rx.grid(
            rx.foreach(Setup.setup_levels,
                       lambda lv: ui.stat(lv["level"], lv["value"],
                                          size="4")),
            columns=rx.breakpoints(initial="2", md="4"), spacing="4",
            width="100%", padding="0.9em 1em", border_radius="10px",
            background=ds.SURFACE_HOVER,
        ),
        ui.note("Calculated Level 1–4 targets appear here once Level 1 base "
                "is saved."),
    )


def _holiday_chip(h) -> rx.Component:
    return rx.badge(
        h["label"],
        rx.cond(Setup.can_edit_setup,
                rx.icon("x", size=12, cursor="pointer",
                        on_click=Setup.remove_holiday(h["iso"]),
                        id="setup-ph-remove-" + h["iso"])),
        variant="soft", color_scheme="indigo", size="2",
        id="setup-ph-" + h["iso"],
    )


def _holidays_field() -> rx.Component:
    # pick a date to add it; × removes it (saved with Save Setup)
    return ui.form_field(
        "Public Holidays",
        rx.box(
            rx.vstack(
                rx.input(type="date", value="", min=Setup.s_holiday_min,
                         max=Setup.s_holiday_max, on_change=Setup.add_holiday,
                         id="setup-public-holidays",
                         disabled=~Setup.can_edit_setup),
                rx.cond(Setup.holiday_chips.length() > 0,
                        rx.hstack(rx.foreach(Setup.holiday_chips, _holiday_chip),
                                  spacing="2", wrap="wrap"),
                        ui.note("None")),
                spacing="2", width="100%"),
            id="setup-holidays-field",
        ),
    )


def _budget_row(row) -> rx.Component:
    return ui.form_field(
        row["name"],
        rx.input(
            # "×" beside a saved budget: clears it (save writes NULL)
            ui.clear_x((row["original"] != "") & (row["clear"] != "1")
                       & Setup.can_edit_setup,
                       Setup.clear_budget(row["id"]),
                       id=f"setup-budget-{row['id']}-clear"),
            value=row["value"],
            on_change=lambda v: Setup.set_budget_value(row["id"], v),
            placeholder="—", id=f"setup-budget-{row['id']}",
            disabled=~Setup.can_edit_setup),
    )


def _setup_section() -> rx.Component:
    return ui.section_card(
        rx.cond(
            Setup.setup_finalized,
            rx.callout(
                "A finalized snapshot exists for this month. Edits change "
                "live configuration only — the finalized report is "
                "unchanged.",
                icon="lock", color_scheme="violet", variant="soft",
                width="100%")),
        rx.cond(Setup.setup_notice != "",
                rx.callout(Setup.setup_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%")),
        rx.cond(Setup.setup_error != "", ds.error_callout(Setup.setup_error)),
        ui.form_stack(
            ui.form_section(
                "Sales Targets",
                _setup_grid(SETUP_SALES[:2]),
                _setup_grid(SETUP_SALES[2:5]),
                rx.hstack(
                    rx.button("Use HQ Defaults",
                              on_click=Setup.use_hq_defaults, variant="soft",
                              size="2", type="button", id="setup-hq-levels",
                              disabled=~Setup.can_edit_setup),
                    rx.cond(Setup.setup_levels_missing,
                            ui.status_chip(ds.LEVELS_MISSING, "amber",
                                           id="setup-levels-missing",
                                           wrap=True)),
                    spacing="3", align="center", wrap="wrap"),
                _levels_panel(),
                _setup_grid(SETUP_SALES[5:]),
                hint="Set Levels 2–4 when Level 1 has a target. HQ Defaults "
                     "add 10%, 20% and 30%. Last year and last month drive "
                     "growth comparisons.",
            ),
            ui.form_section(
                "Daily Sales",
                ui.form_grid(*[_setup_field(spec) for spec in SETUP_DAILY],
                             _holidays_field(), columns="3"),
                hint="Daily targets use the store's State and Concept "
                     "contributions.",
            ),
            ui.form_section("Manpower", _setup_grid(SETUP_MANPOWER)),
            ui.form_section("Stock", _setup_grid(SETUP_STOCK)),
            ui.form_section(
                "Expense Budgets (RM)",
                rx.cond(
                    Setup.budget_rows.length() > 0,
                    rx.grid(rx.foreach(Setup.budget_rows, _budget_row),
                            columns=rx.breakpoints(initial="1", sm="3"),
                            spacing="4", width="100%"),
                    ui.note("No expense categories configured for this "
                            "store.")),
            ),
            ui.form_section("Month-End Inputs", _setup_grid(SETUP_PAYROLL),
                            hint="Leave blank until payroll closes to show "
                                 "Awaiting Payroll, never RM 0."),
        ),
        rx.hstack(
            rx.button(
                rx.cond(Setup.setup_saving, rx.spinner(size="1"),
                        rx.icon("save", size=14)),
                "Save Setup", color_scheme="indigo", on_click=Setup.save_setup,
                disabled=Setup.setup_saving | ~Setup.can_edit_setup, id="setup-save"),
            rx.cond(~Setup.can_edit_setup,
                    ui.note("You have read-only access for this store.")),
            spacing="3", align="center", width="100%",
            border_top=f"1px solid {ui.CARD_BORDER}", padding_top="1em",
        ),
        title="Monthly Setup",
        subtitle="Leave blank if not set. Enter 0 only for a genuine zero.",
        id="setup-section",
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
                    (Stores.stores_notice != "") | (Assign.stores_notice != ""),
                    rx.callout(Stores.stores_notice + Assign.stores_notice,
                               icon="circle-check",
                               color_scheme="green", variant="soft",
                               width="100%")),
                rx.cond((Stores.stores_error != "") | (Assign.stores_error != ""),
                        ds.error_callout(Stores.stores_error
                                         + Assign.stores_error)),
                _stores_table(),
                _assign_panel(),
                _setup_section(),
                spacing=ui.GAP, width="100%",
            ),
            _store_form_dialog(),
        ),
        on_store=Stores.select_store,
        on_month=Stores.select_month,
        tour_key="stores",
    )
