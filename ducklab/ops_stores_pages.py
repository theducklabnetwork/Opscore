"""Store Management + Monthly Setup pages at /ops/stores."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.ops_shell import shell
from ducklab.ops_stores_state import (
    SETUP_MANPOWER, SETUP_PAYROLL, SETUP_SALES, SETUP_STOCK, StoresState,
)

S = StoresState


# ----------------------------------------------------------------------
# Stores table
# ----------------------------------------------------------------------
def _store_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text(row["code"], weight="medium")),
        ui.td(row["name"]),
        ui.td(row["concept"]),
        ui.td(row["zone"]),
        ui.td(row["zone_pic"]),
        ui.td(row["branch_manager"]),
        ui.td(ui.status_chip(row["active_label"],
                             rx.cond(row["active"] == "1", "green", "gray"))),
        ui.td(rx.hstack(
            rx.button("Edit", size="1", variant="soft",
                      on_click=S.open_store_form(row["id"]),
                      disabled=~S.can_manage_stores,
                      id=f"store-edit-{row['code']}"),
            rx.button(rx.cond(row["active"] == "1", "Deactivate", "Activate"),
                      size="1", variant="soft", color_scheme="gray",
                      on_click=S.toggle_store_active(row["id"]),
                      disabled=~S.can_manage_stores,
                      id=f"store-toggle-{row['code']}"),
            rx.button("Users", size="1", variant="soft",
                      on_click=S.open_assign(row["id"]),
                      disabled=~S.can_assign_users,
                      id=f"store-users-{row['code']}"),
            spacing="2")),
    )


def _stores_table() -> rx.Component:
    return ui.section_card(
        rx.cond(
            S.store_rows.length() > 0,
            ui.data_table(
                ["Code", "Name", "Concept", "Zone", "Zone PIC",
                 "Branch manager", "Status", "Actions"],
                rx.foreach(S.store_rows, _store_row),
                phone=("Code", "Status", "Actions"),
            ),
            ui.empty_state("No stores", "Create your first store to begin.",
                           icon="store"),
        ),
        ui.note("Deactivation preserves all historical records."),
        title="Stores",
        extra=rx.button(rx.icon("plus", size=14), "New store",
                        on_click=S.open_store_form(""),
                        disabled=~S.can_manage_stores, id="store-new"),
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
                _sf_field("Code", S.sf_code, S.set_sf_code, "sf-code",
                          "e.g. A01", required=True),
                _sf_field("Name", S.sf_name, S.set_sf_name, "sf-name",
                          required=True),
                _sf_field("Concept", S.sf_concept, S.set_sf_concept,
                          "sf-concept"),
            ),
        ),
        ui.form_section(
            "Area & people",
            ui.form_grid(
                _sf_field("Zone", S.sf_zone, S.set_sf_zone, "sf-zone"),
                _sf_field("Zone PIC", S.sf_zone_pic, S.set_sf_zone_pic,
                          "sf-zone-pic"),
                _sf_field("Branch manager", S.sf_branch_manager,
                          S.set_sf_branch_manager, "sf-bm"),
            ),
        ),
        open=S.sf_open,
        on_open_change=lambda v: S.close_store_form(),
        title=rx.cond(S.sf_editing != "", "Edit Store", "New Store"),
        subtitle="Deactivation preserves all historical records.",
        close_id="store-form-close",
        notices=[rx.cond(S.sf_error != "", ds.error_callout(S.sf_error))],
        actions=ui.dialog_actions(
            save_label="Save Store", on_save=S.save_store,
            saving=S.sf_saving, save_id="store-form-save",
            cancel_id="store-form-cancel"),
        width="600px",
    )


# ----------------------------------------------------------------------
# User assignment panel
# ----------------------------------------------------------------------
def _assigned_user(r) -> rx.Component:
    return rx.hstack(
        rx.text(r["label"], size="2", color=ds.TEXT),
        rx.cond(r["active"] == "0", ui.status_chip("disabled", "gray")),
        rx.spacer(),
        rx.button("Unassign", size="1", variant="soft", color_scheme="red",
                  on_click=S.unassign_user(r["id"]),
                  id=f"unassign-{r['id']}"),
        width="100%", align="center", padding_y="0.5em",
        border_bottom=f"1px solid {ui.ROW_BORDER}",
    )


def _assign_panel() -> rx.Component:
    return rx.cond(
        S.assign_store_id != "",
        ui.section_card(
            rx.cond(
                S.assign_rows.length() > 0,
                rx.vstack(rx.foreach(S.assign_rows, _assigned_user),
                          spacing="0", width="100%"),
                rx.text("No users assigned yet.", size="2",
                        color=ds.TEXT_MUTED),
            ),
            rx.hstack(
                rx.select(S.assign_options, value=S.assign_selected,
                          on_change=S.select_assign_user,
                          placeholder="Select an existing user…",
                          id="assign-select"),
                rx.button("Assign", on_click=S.assign_user,
                          disabled=S.assign_selected == "",
                          id="assign-submit"),
                spacing="3", align="center", wrap="wrap"),
            title="User assignment",
            subtitle="Assign existing users to this store. User creation is "
                     "handled by an administrator elsewhere.",
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
        rx.input(value=getattr(S, attr), on_change=getattr(S, "set_" + attr),
                 placeholder="—", id="setup-" + field.replace("_", "-"),
                 disabled=~S.can_edit_setup))


def _setup_grid(specs) -> rx.Component:
    return ui.form_grid(*[_setup_field(spec) for spec in specs], columns="3")


def _levels_panel() -> rx.Component:
    return rx.cond(
        S.setup_levels.length() > 0,
        rx.grid(
            rx.foreach(S.setup_levels,
                       lambda lv: ui.stat(lv["level"], lv["value"],
                                          size="4")),
            columns=rx.breakpoints(initial="2", md="4"), spacing="4",
            width="100%", padding="0.9em 1em", border_radius="10px",
            background=ds.SURFACE_HOVER,
        ),
        ui.note("Calculated Level 1–4 targets appear here once Level 1 base "
                "is saved."),
    )


def _budget_row(row) -> rx.Component:
    return ui.form_field(
        row["name"],
        rx.input(
            # "×" beside a saved budget: clears it (save writes NULL)
            ui.clear_x((row["original"] != "") & (row["clear"] != "1")
                       & S.can_edit_setup,
                       S.clear_budget(row["id"]),
                       id=f"setup-budget-{row['id']}-clear"),
            value=row["value"],
            on_change=lambda v: S.set_budget_value(row["id"], v),
            placeholder="—", id=f"setup-budget-{row['id']}",
            disabled=~S.can_edit_setup),
    )


def _setup_section() -> rx.Component:
    return ui.section_card(
        rx.cond(
            S.setup_finalized,
            rx.callout(
                "A finalized snapshot exists for this month. Edits change "
                "live configuration only — the finalized report is "
                "unchanged.",
                icon="lock", color_scheme="violet", variant="soft",
                width="100%")),
        rx.cond(S.setup_notice != "",
                rx.callout(S.setup_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%")),
        rx.cond(S.setup_error != "", ds.error_callout(S.setup_error)),
        ui.form_stack(
            ui.form_section(
                "Sales targets",
                _setup_grid(SETUP_SALES[:2]),
                _setup_grid(SETUP_SALES[2:5]),
                rx.hstack(
                    rx.button("Use HQ defaults (+10% / +20% / +30%)",
                              on_click=S.use_hq_defaults, variant="soft",
                              size="2", type="button", id="setup-hq-levels",
                              disabled=~S.can_edit_setup),
                    rx.cond(S.setup_levels_missing,
                            ui.status_chip(ds.LEVELS_MISSING, "amber",
                                           id="setup-levels-missing",
                                           wrap=True)),
                    spacing="3", align="center", wrap="wrap"),
                _levels_panel(),
                _setup_grid(SETUP_SALES[5:]),
                hint="Levels 2–4 uplift is required when a Level 1 target "
                     "is set. Last year / last month are the bases for the "
                     "growth comparisons.",
            ),
            ui.form_section("Manpower", _setup_grid(SETUP_MANPOWER)),
            ui.form_section("Stock", _setup_grid(SETUP_STOCK)),
            ui.form_section(
                "Expense budgets (RM)",
                rx.cond(
                    S.budget_rows.length() > 0,
                    rx.grid(rx.foreach(S.budget_rows, _budget_row),
                            columns=rx.breakpoints(initial="1", sm="3"),
                            spacing="4", width="100%"),
                    ui.note("No expense categories configured for this "
                            "store.")),
            ),
            ui.form_section("Month-end inputs", _setup_grid(SETUP_PAYROLL),
                            hint="Leave blank until payroll closes: it then "
                                 "shows as awaiting payroll, never RM 0."),
        ),
        rx.hstack(
            rx.button(
                rx.cond(S.setup_saving, rx.spinner(size="1"),
                        rx.icon("save", size=14)),
                "Save Setup", color_scheme="indigo", on_click=S.save_setup,
                disabled=S.setup_saving | ~S.can_edit_setup, id="setup-save"),
            rx.cond(~S.can_edit_setup,
                    ui.note("You have read-only access for this store.")),
            spacing="3", align="center", width="100%",
            border_top=f"1px solid {ui.CARD_BORDER}", padding_top="1em",
        ),
        title="Monthly setup",
        subtitle="Leave a field blank if it is not set; enter 0 only for a "
                 "genuine zero. Targets and budgets feed the Stock, "
                 "Manpower, Expenses and Monthly Report pages.",
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
                    S.stores_notice != "",
                    rx.callout(S.stores_notice, icon="circle-check",
                               color_scheme="green", variant="soft",
                               width="100%")),
                rx.cond(S.stores_error != "",
                        ds.error_callout(S.stores_error)),
                _stores_table(),
                _assign_panel(),
                _setup_section(),
                spacing=ui.GAP, width="100%",
            ),
            _store_form_dialog(),
        ),
        on_store=S.select_store,
        on_month=S.select_month,
        description="Stores, who can access them, and each store's monthly "
                    "targets, budgets and month-end inputs.",
    )
