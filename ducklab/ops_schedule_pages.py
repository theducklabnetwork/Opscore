"""Weekly schedule builder and its staff/shift-code editors."""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.ops_manpower_state import ManpowerState

M = ManpowerState


def _schedule_cell(staff_id, cell) -> rx.Component:
    return ui.td(rx.select(
        M.schedule_code_values,
        value=cell["code"],
        on_change=lambda value: M.set_schedule_cell(staff_id, cell["date"], value),
        disabled=~M.can_write_schedule | (cell["date"] == ""),
        size="1",
        width="100%",
        style={"backgroundColor": cell["color"], "width": "100%"},
    ), style={"border": "1px solid #d4d4d8", "padding": "6px", "height": "58px"})


def _builder_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.vstack(rx.text(row["name"], weight="medium"),
                        rx.text(row["detail"],
                                size="1", color=ds.TEXT_MUTED), spacing="0"),
              style={"border": "1px solid #d4d4d8", "padding": "10px"}),
        rx.foreach(row["cells"], lambda cell: _schedule_cell(row["id"], cell)),
        style={"borderBottom": "1px solid #e4e4e7"},
    )


def _summary_row() -> rx.Component:
    return rx.el.tr(
        ui.td(rx.text("Headcount", weight="bold"),
              style={"padding": "10px", "border": "1px solid #d4d4d8"}),
        rx.foreach(
            M.schedule_day_summaries,
            lambda day: ui.td(
                rx.text(day["headcount"], weight="bold"),
                style={"backgroundColor": rx.cond(day["below_target"] == "1",
                                                   "#fee2e2", "transparent"),
                       "border": "1px solid #d4d4d8", "padding": "10px",
                       "textAlign": "center"},
            )),
    )


def _builder() -> rx.Component:
    return ui.section_card(
        rx.hstack(
            ui.form_field("Week", rx.select.root(
                rx.select.trigger(value=M.schedule_week_label, width="190px"),
                rx.select.content(rx.foreach(
                    M.schedule_week_choices,
                    lambda week: rx.select.item(week["label"], value=week["value"]))),
                value=M.schedule_week, on_change=M.select_schedule_week,
                id="schedule-week")),
            ui.form_field("Target Manpower",
                          rx.input(value=M.schedule_target,
                                   on_change=M.set_schedule_target,
                                   input_mode="numeric", placeholder="0",
                                   disabled=~M.can_write_schedule,
                                   id="schedule-target", width="110px")),
            rx.text("Changes Save Automatically", size="1", color=ds.TEXT_MUTED,
                    white_space="nowrap"),
            rx.spacer(),
            rx.button(rx.icon("file-text", size=14), "Generate",
                      on_click=M.generate_schedule, id="generate-schedule"),
            rx.tooltip(rx.icon_button(rx.icon("download", size=14), size="1",
                                     variant="soft", on_click=M.export_schedule_csv,
                                     id="export-schedule-csv", aria_label="Export CSV"),
                       content="Export CSV"),
            width="100%", align="end", flex_wrap="nowrap",
        ),
        rx.cond(
            M.schedule_staff.length() > 0,
            rx.box(rx.el.table(
                rx.el.thead(rx.el.tr(
                    ui.th("Staff"),
                    rx.foreach(M.schedule_days, lambda day: ui.th(day["label"])),
                )),
                rx.el.tbody(
                    rx.foreach(M.schedule_grid_rows, _builder_row),
                    _summary_row(),
                ),
                style={"width": "100%", "borderCollapse": "collapse"},
            ), width="100%", overflow_x="auto"),
            ui.empty_state("No Staff", "Add staff before building a schedule.", icon="users"),
        ),
        rx.flex(rx.foreach(M.schedule_legend, _generated_legend_item),
                gap="12px", wrap="wrap", width="100%"),
        title="Weekly Schedule",
        subtitle="Red days are below target manpower.",
        id="schedule-builder",
    )


def _generated_legend_item(code) -> rx.Component:
    return rx.hstack(
        rx.box(width="14px", height="14px", background=code["color"],
               border="1px solid #999"),
        rx.text(code["code"] + ": " + code["detail"], size="1"),
        spacing="1", align="center",
    )


def _generated_cell(cell) -> rx.Component:
    return rx.el.td(rx.text(cell["code"], size="1", weight="bold"),
                    class_name="generated-code", background=cell["color"])


def _generated_row(row) -> rx.Component:
    return rx.el.tr(
        rx.el.td(row["number"], class_name="generated-number"), rx.el.td(row["name"]),
        rx.el.td(row["staff_code"]), rx.el.td(row["position"]),
        rx.foreach(row["cells"], _generated_cell),
    )


def _generated() -> rx.Component:
    return rx.box(
        rx.button(rx.icon("printer", size=14), "Print",
                  on_click=M.print_schedule, class_name="schedule-print-button"),
        rx.box(
            rx.heading("Staff Weekly Planned Schedule", size="5", weight="bold",
                       text_align="center"),
            rx.hstack(
                rx.text("Store: " + M.selected_store_label),
                rx.text("Target Manpower: " + M.schedule_target_display),
                rx.text("Week: " + M.schedule_week_label),
                justify="between", width="100%",
            ),
            rx.box(
                rx.foreach(M.schedule_legend, _generated_legend_item),
                class_name="generated-legend",
            ),
            rx.el.table(
                rx.el.thead(rx.el.tr(
                    rx.el.th("No.", class_name="generated-number"),
                    rx.el.th("Name"), rx.el.th("Staff ID"),
                    rx.el.th("Position"),
                    rx.foreach(M.schedule_generated_days,
                               lambda day: rx.el.th(
                                   rx.text(day["weekday"], display="block"),
                                   rx.text(day["short_date"], display="block"))),
                )),
                rx.el.tbody(rx.foreach(M.schedule_generated_rows, _generated_row)),
                class_name="generated-schedule-table",
            ),
            id="generated-schedule-print", class_name="generated-schedule-print",
        ),
        width="100%", id="schedule-generated",
    )


def _staff_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(row["name"]), ui.td(row["staff_code"]), ui.td(row["position"]),
        ui.td(rx.vstack(
            rx.text(row["employment_type"], size="2"),
            rx.cond(row["employment_type"] == "Not Set",
                    rx.text("Set Employment Type", size="1", color_scheme="amber")),
            spacing="1",
        )),
        ui.td(rx.hstack(
            rx.button("Edit", size="1", variant="soft",
                      on_click=M.open_schedule_staff(row["id"])),
            rx.button("Delete", size="1", variant="soft", color_scheme="red",
                      on_click=M.delete_schedule_staff(row["id"])),
        )),
    )


def _staff_tab() -> rx.Component:
    return ui.section_card(
        rx.cond(M.schedule_staff.length() > 0,
                ui.data_table(["Name", "Staff ID", "Position", "Employment Type", ""],
                              rx.foreach(M.schedule_staff, _staff_row)),
                ui.empty_state("No Staff", icon="users")),
        title="Staff",
        extra=rx.cond(M.can_write_schedule,
                      rx.button(rx.icon("plus", size=14), "Add Staff",
                                on_click=M.open_schedule_staff(""))),
        id="schedule-staff",
    )


def _code_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(row["code"]), ui.td(row["label"]), ui.td(row["start_time"]),
        ui.td(row["end_time"]),
        ui.td(rx.hstack(rx.box(width="20px", height="20px", border_radius="4px",
                               background=row["color"]), rx.text(row["color"]))),
        ui.td(rx.hstack(
            rx.button("Edit", size="1", variant="soft",
                      on_click=M.open_schedule_code(row["code"])),
            rx.button("Delete", size="1", variant="soft", color_scheme="red",
                      on_click=M.delete_schedule_code(row["code"])),
        )),
    )


def _codes_tab() -> rx.Component:
    return ui.section_card(
        ui.data_table(["Code", "Label", "Start", "End", "Color", ""],
                      rx.foreach(M.schedule_codes, _code_row)),
        title="Shift Codes",
        extra=rx.cond(M.can_write_schedule,
                      rx.button(rx.icon("plus", size=14), "Add Code",
                                on_click=M.open_schedule_code(""))),
        id="schedule-codes",
    )


def _schedule_dialog() -> rx.Component:
    staff_fields = ui.form_grid(
        ui.form_field("Name", rx.input(value=M.sf_name, on_change=M.set_sf_name,
                                        id="schedule-staff-name"), required=True),
        ui.form_field("Staff ID", rx.input(value=M.sf_staff_code,
                                            on_change=M.set_sf_staff_code,
                                            id="schedule-staff-id"), required=True),
        ui.form_field("Position", rx.select(
            ["ABM", "ASV", "SV", "RA"], value=M.sf_position,
            on_change=M.set_sf_position, placeholder="Select position",
            id="schedule-staff-position"), required=True),
        ui.form_field("Employment Type", rx.select(
            ["Full-Time", "Part-Time", "Not Set"], value=M.sf_employment_type,
            on_change=M.set_sf_employment_type,
            id="schedule-staff-employment-type"), required=True),
        columns="4",
    )
    code_fields = ui.form_grid(
        ui.form_field("Code", rx.input(value=M.sc_code, on_change=M.set_sc_code,
                      read_only=M.schedule_editing_id != ""), required=True),
        ui.form_field("Label", rx.input(value=M.sc_label, on_change=M.set_sc_label), required=True),
        ui.form_field("Start", rx.input(type="time", value=M.sc_start, on_change=M.set_sc_start)),
        ui.form_field("End", rx.input(type="time", value=M.sc_end, on_change=M.set_sc_end)),
        ui.form_field("Color", rx.input(type="color", value=M.sc_color, on_change=M.set_sc_color), required=True),
        columns="3",
    )
    return ui.form_dialog(
        rx.cond(M.schedule_form_kind == "staff", staff_fields, code_fields),
        open=M.schedule_form_open,
        on_open_change=M.set_schedule_form_open,
        title=M.schedule_form_title,
        close_id="schedule-form-close",
        notices=[
            rx.cond(M.schedule_form_error != "",
                    ds.error_callout(M.schedule_form_error)),
            rx.cond(M.schedule_staff_confirmation != "",
                    rx.callout(M.schedule_staff_confirmation, icon="circle-check",
                               color_scheme="green", variant="soft")),
        ],
        actions=[
            rx.dialog.close(rx.button("Cancel", variant="soft",
                                      color_scheme="gray", id="schedule-form-cancel")),
            rx.cond((M.schedule_form_kind == "staff") & (M.schedule_editing_id == ""),
                    rx.button("Save & Add Another", variant="soft",
                              on_click=M.save_schedule_form_add_another,
                              disabled=M.schedule_saving | ~M.can_write_schedule,
                              id="schedule-form-save-add-another")),
            rx.cond(M.can_write_schedule,
                    rx.button(rx.cond(M.schedule_saving, rx.spinner(size="1"),
                                      rx.icon("save", size=14)), "Save",
                              color_scheme="indigo",
                              on_click=lambda: M.save_schedule_form(False),
                              disabled=M.schedule_saving, id="schedule-form-save")),
        ],
    )


def schedule_tab() -> rx.Component:
    return rx.vstack(
        rx.cond(M.schedule_notice != "", rx.callout(M.schedule_notice,
                icon="circle-check", color_scheme="green", variant="soft")),
        rx.cond(M.schedule_error != "", ds.error_callout(M.schedule_error)),
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger("Planner", value="builder", id="schedule-tab-builder"),
                rx.tabs.trigger("Staff", value="staff", id="schedule-tab-staff"),
                rx.tabs.trigger("Config", value="codes", id="schedule-tab-codes"),
                rx.tabs.trigger("View", value="generated", id="schedule-tab-generated"),
            ),
            rx.tabs.content(_builder(), value="builder"),
            rx.tabs.content(_staff_tab(), value="staff"),
            rx.tabs.content(_codes_tab(), value="codes"),
            rx.tabs.content(_generated(), value="generated"),
            value=M.schedule_subtab, on_change=M.set_schedule_subtab,
            default_value="builder", width="100%",
        ),
        _schedule_dialog(),
        spacing=ui.GAP, width="100%", id="manpower-schedule",
    )
