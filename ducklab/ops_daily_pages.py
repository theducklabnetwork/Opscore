"""Daily Operations page: monthly KPI summary, day-by-day records table,
and the daily entry drawer (sales / manpower / stock loss).
"""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab import ui
from ducklab.ops_daily_state import (
    MANPOWER_FIELDS, SALES_FIELDS, DailyState,
)
from ducklab.ops_shell import shell



K = DailyState.day_kpis
_PROJ_CHIP = (K["proj_status"], K["proj_color"])


def _kpi_summary() -> rx.Component:
    """Headline tiles. Achievement is MTD / Level 1; the projected figures
    carry the engine's projection label (as on the Overview)."""
    return rx.vstack(
        rx.cond(
            DailyState.month_finalized,
            rx.callout(
                "Finalized snapshot exists — edits affect live calculations "
                "only, never preserved snapshots.",
                icon="lock", color_scheme="violet", variant="soft",
                width="100%"),
        ),
        # same tiles and names as Sales; the projection chip + early-estimate
        # tag appear once, on the projected-achievement tile
        ui.kpi_grid(
            ui.kpi_tile("Sales MTD", K["mtd"],
                        compact=K["mtd_compact"], exact=K["mtd"],
                        sub=K["reported_days"], id="kpi-mtd"),
            ui.kpi_tile("Achievement to date", K["achievement"],
                        sub=K["target_sub"], id="kpi-achievement"),
            ui.kpi_tile("Month-end sales", K["projected"],
                        compact=K["projected_compact"],
                        exact=K["projected_exact"],
                        sub=K["projected_sub"], id="kpi-projected"),
            ui.kpi_tile("Projected achievement", K["proj_achievement"],
                        chip=_PROJ_CHIP, tag=K["early_tag"],
                        tag_tip=K["early_tip"],
                        sub=K["proj_achievement_sub"],
                        id="kpi-proj-achievement"),
            ui.kpi_tile("Avg daily sales", K["avg_daily"],
                        exact=K["avg_daily_exact"]),
            ui.kpi_tile("Transactions", K["transactions"]),
            ui.kpi_tile("Units sold", K["units"]),
            ui.kpi_tile("Total loss", K["loss"]),
        ),
        spacing="4", width="100%", id="daily-kpis",
    )


_COLS = ["Date", ("Sales", True), ("Txns", True), ("Units", True),
         ("SPR", True), ("UPT", True), ("ASP", True), ("Staff", True),
         ("Sales/Staff", True), ("Stock Loss", True), "Status", ""]


def _day_row(row) -> rx.Component:
    return rx.el.tr(
        ui.td(rx.hstack(rx.text(row["date"], weight="medium"),
                        rx.text(row["weekday"], size="1",
                                color=ds.TEXT_FAINT),
                        spacing="2", align="baseline")),
        ui.td(row["sales"], numeric=True),
        ui.td(row["transactions"], numeric=True),
        ui.td(row["units"], numeric=True),
        ui.td(row["spr"], numeric=True),
        ui.td(row["upt"], numeric=True),
        ui.td(row["asp"], numeric=True),
        ui.td(row["staff"], numeric=True),
        ui.td(row["sales_per_staff"], numeric=True),
        ui.td(rx.cond(row["loss_missing"] == "1",
                      rx.text("—", color=ds.TEXT_FAINT), row["loss"]),
              numeric=True),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(
            rx.cond(
                DailyState.can_write,
                rx.button(
                    rx.icon("pencil", size=13), "Edit", size="1",
                    variant="soft",
                    on_click=DailyState.open_entry(row["iso"]),
                ),
                rx.button(
                    rx.icon("eye", size=13), "View", size="1",
                    variant="ghost",
                    on_click=DailyState.open_entry(row["iso"]),
                ),
            ),
        ),
    )


def _record_day_button() -> rx.Component:
    """Header action: opens the entry dialog for today (if in the month and
    not entered) or the first unreported day. Disabled with a tooltip for
    read-only users and deactivated stores. The box keeps the tooltip
    working on a disabled button."""
    return rx.tooltip(
        rx.box(rx.button(
            rx.icon("plus", size=15), "Record a day", size="2",
            on_click=DailyState.open_entry(DailyState.record_day_iso),
            disabled=~DailyState.record_day_enabled, id="record-day")),
        content=DailyState.record_day_tip,
    )


def _records_table() -> rx.Component:
    return ui.section_card(
        ui.data_table(
            _COLS,
            *ui.day_rows(DailyState.day_rows, DailyState.day_rows_reported,
                         DailyState.hidden_count, DailyState.hidden_label,
                         DailyState.show_hidden,
                         DailyState.toggle_hidden_days, _day_row, len(_COLS),
                         toggle_id="daily-hidden-toggle"),
        ),
        ui.status_legend(
            note="Status reflects all entered fields, not sales alone. "
                 "Unreported days have no saved record; show them to "
                 "enter data."),
        title="Daily Records", id="daily-table",
    )


# ---------------------------------------------------------------------------
# Entry form
# ---------------------------------------------------------------------------

_W = ~DailyState.can_write  # read-only: every control disabled


def _num_field(label, attr, setter, fid, placeholder="—",
               mode="numeric") -> rx.Component:
    return ui.form_field(
        label,
        rx.input(value=getattr(DailyState, attr),
                 on_change=getattr(DailyState, setter),
                 placeholder=placeholder, input_mode=mode, id=fid,
                 disabled=_W),
        clear=ui.clear_box(DailyState.clear_fields[attr],
                           lambda v: DailyState.set_clear_field(attr, v),
                           disabled=_W),
    )


def _sales_section() -> rx.Component:
    return ui.form_section(
        "Sales",
        ui.form_grid(
            _num_field("Daily sales (RM)", "f_sales", "set_f_sales",
                       "f-sales", placeholder="0.00", mode="decimal"),
            _num_field("Transactions", "f_transactions",
                       "set_f_transactions", "f-transactions"),
            _num_field("Units sold", "f_units", "set_f_units", "f-units"),
            columns="3",
        ),
        hint="Blank fields leave saved values untouched. Tick “Clear saved "
             "value” to deliberately reset a field to unreported. Enter 0 "
             "to record a genuine zero.",
    )


def _summary(label, value, color="gray") -> rx.Component:
    return rx.hstack(
        rx.text(label, size="1", color=ds.TEXT_MUTED),
        ui.status_chip(value, color),
        spacing="2", align="center")


def _manpower_section() -> rx.Component:
    fields = [
        ("f_ft_working", "set_f_ft_working", "Full-time staff working", "f-ft"),
        ("f_pt_working", "set_f_pt_working", "Part-time staff working", "f-pt"),
        ("f_new_hires", "set_f_new_hires", "New hires (FT)", "f-hires"),
        ("f_resigned", "set_f_resigned", "Resignations (FT)", "f-resigned"),
        ("f_leave_annual", "set_f_leave_annual", "Annual leave", "f-leave-annual"),
        ("f_leave_mc", "set_f_leave_mc", "Medical leave", "f-leave-mc"),
        ("f_leave_other", "set_f_leave_other", "Other paid leave", "f-leave-other"),
        ("f_leave_unpaid", "set_f_leave_unpaid", "Unpaid leave", "f-leave-unpaid"),
    ]
    return ui.form_section(
        "Manpower",
        ui.form_grid(*[_num_field(label, attr, setter, fid)
                       for attr, setter, label, fid in fields],
                     columns="3"),
        extra=_summary("Effective staff:", DailyState.form_effective_staff),
        hint="Effective staff = FT + PT × the configured part-timer factor "
             "for this store/month. Blank attendance stays unreported.",
    )


def _stock_input(code: dict) -> rx.Component:
    return ui.form_field(
        rx.hstack(rx.text(code["code"], size="2", weight="medium",
                          color=ds.TEXT),
                  rx.text(code["name"], size="1", color=ds.TEXT_FAINT),
                  spacing="2", align="baseline", wrap="wrap"),
        rx.input(
            value=DailyState.stock_form[code["code"]],
            on_change=lambda v: DailyState.set_stock_amount(code["code"], v),
            placeholder="0.00", input_mode="decimal",
            id=f"stock-{code['code']}", disabled=_W,
        ),
        clear=ui.clear_box(
            DailyState.clear_stock[code["code"]],
            lambda v: DailyState.set_clear_stock(code["code"], v),
            disabled=_W),
    )


def _stock_section() -> rx.Component:
    return ui.form_section(
        "Stock loss (RM)",
        rx.text("Counts toward total loss (DMO + DMC + DMP + Missing − "
                "Extra)", size="2", color=ds.TEXT_MUTED),
        rx.grid(rx.foreach(DailyState.contributing_codes, _stock_input),
                columns=rx.breakpoints(initial="1", sm="3"),
                spacing="4", width="100%"),
        rx.text("Informational only — excluded from total loss", size="2",
                color=ds.TEXT_MUTED, padding_top="0.5em"),
        rx.grid(rx.foreach(DailyState.informational_codes, _stock_input),
                columns=rx.breakpoints(initial="1", sm="3"),
                spacing="4", width="100%"),
        extra=_summary("Total loss:", DailyState.form_total_loss),
    )


def _entry_dialog() -> rx.Component:
    """Centered daily entry dialog (kit pattern: pinned header + footer,
    only the sections scroll)."""
    return ui.form_dialog(
        _sales_section(),
        _manpower_section(),
        _stock_section(),
        open=DailyState.form_open,
        on_open_change=lambda v: DailyState.close_entry(),
        title=DailyState.form_title,
        subtitle=DailyState.form_day_label,
        close_id="entry-close",
        notices=[
            rx.cond(_W, rx.callout(
                "You have read-only access. Contact an administrator to "
                "modify operational records.",
                icon="lock", color_scheme="gray", variant="soft",
                width="100%")),
            rx.cond(DailyState.form_error != "",
                    ds.error_callout(DailyState.form_error)),
        ],
        status=rx.cond(DailyState.form_dirty & DailyState.can_write,
                       rx.text("Unsaved changes", size="1",
                               color=ds.WARNING)),
        actions=ui.dialog_actions(
            save_label="Save Entry", on_save=DailyState.save_entry,
            saving=DailyState.saving, save_id="entry-save",
            cancel_id="entry-cancel", can_save=DailyState.can_write),
    )


def daily_page() -> rx.Component:
    return shell(
        "Daily Operations",
        rx.vstack(
            rx.cond(
                DailyState.save_notice != "",
                rx.callout(DailyState.save_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%", id="save-notice"),
            ),
            # errors raised while the entry dialog is closed (bad date,
            # session/access) and data-load failures
            rx.cond(
                ~DailyState.form_open & (DailyState.form_error != ""),
                ds.error_callout(DailyState.form_error),
            ),
            rx.cond(
                DailyState.report_status == "error",
                ds.error_callout(DailyState.error_message),
            ),
            rx.cond(
                DailyState.report_status == "empty",
                ds.empty_state("No store or month selected",
                               DailyState.error_message,
                               icon="store"),
                rx.vstack(
                    _kpi_summary(),
                    _records_table(),
                    spacing=ui.GAP, width="100%",
                ),
            ),
            _entry_dialog(),
            spacing=ui.GAP, width="100%",
        ),
        on_store=DailyState.select_store,
        on_month=DailyState.select_month,
        description="Record daily sales, staffing and stock losses. "
                    "Saving recalculates the live KPIs immediately.",
        actions=_record_day_button(),
    )
