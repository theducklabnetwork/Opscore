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


def _tile(label, value, id=None) -> rx.Component:
    # plain label + full value; exact money ("RM 120,000.00") fits a
    # two-up phone tile two sizes down
    return ui.kpi_tile(label, value, id=id, size_narrow="4")


def _kpi_summary() -> rx.Component:
    """Headline tiles. Achievement is MTD / Level 1. Projected Sales and
    Projected Achievement only while the month is incomplete. Expenses is
    paid this month (paid date)."""
    sales = _tile("Sales", K["mtd"], id="kpi-mtd")
    achievement = _tile("Achievement", K["achievement"],
                        id="kpi-achievement")
    tail = [_tile("Daily Sales (Avg.)", K["avg_daily"]),
            _tile("Transactions", K["transactions"]),
            _tile("Units Sold", K["units"]),
            _tile("Total Loss", K["loss"]),
            _tile("Expenses", K["expenses"], id="kpi-expenses")]
    return rx.vstack(
        rx.heading("Monthly Summary", size="4", weight="medium",
                   color=ds.TEXT),
        rx.cond(
            DailyState.month_finalized,
            rx.callout(
                "Finalized snapshot exists — edits affect live calculations "
                "only, never preserved snapshots.",
                icon="lock", color_scheme="violet", variant="soft",
                width="100%"),
        ),
        # complete 4 + 3, incomplete 3 + 3 + 3
        rx.cond(
            K["complete"] == "1",
            ui.kpi_grid(sales, achievement, *tail),
            ui.kpi_grid(
                _tile("Sales", K["mtd"], id="kpi-mtd"),
                _tile("Achievement", K["achievement"], id="kpi-achievement"),
                _tile("Projected Sales", K["projected"], id="kpi-projected"),
                _tile("Projected Achievement", K["proj_achievement"],
                      id="kpi-proj-achievement"),
                _tile("Daily Sales (Avg.)", K["avg_daily"]),
                _tile("Transactions", K["transactions"]),
                _tile("Units Sold", K["units"]),
                _tile("Total Loss", K["loss"]),
                _tile("Expenses", K["expenses"], id="kpi-expenses"),
                columns=rx.breakpoints(initial="2", md="3"),
            ),
        ),
        spacing="4", width="100%", id="daily-kpis",
    )


_COLS = ["Date", ("Sales", True), ("Trans.", True), ("Units", True),
         ("SPR", True), ("UPT", True), ("ASP", True), ("Staff", True),
         # Sales per Staff (also on Manpower) hides at 768-1535px so the
         # table fits beside Expenses
         ("Sales per Staff", True, True), ("Stock Loss", True),
         ("Expenses", True), "Status", ""]


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
        ui.td(row["sales_per_staff"], numeric=True, xl_only=True),
        ui.td(rx.cond(row["loss_missing"] == "1",
                      rx.text("—", color=ds.TEXT_FAINT), row["loss"]),
              numeric=True),
        # read-only: paid that day; opens Expenses filtered to the date
        ui.td(rx.cond(row["expenses"] == "—",
                      rx.text("—", color=ds.TEXT_FAINT),
                      rx.link(row["expenses"],
                              href="/ops/expenses?paid=" + row["iso"],
                              class_name="day-expenses")),
              numeric=True),
        ui.td(ui.status_chip(row["status"], row["status_color"])),
        ui.td(
            rx.cond(
                DailyState.can_write,
                rx.button(
                    rx.icon("pencil", size=13, class_name="ph-hide"), "Edit",
                    size="1",
                    variant="soft",
                    on_click=DailyState.open_entry(row["iso"]),
                ),
                rx.button(
                    rx.icon("eye", size=13, class_name="ph-hide"), "View",
                    size="1",
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
            rx.icon("plus", size=15), "New Entry", size="2",
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
            phone=("Date", "Sales", "Status", ""),
        ),
        ui.status_legend(),
        title="Daily Records", id="daily-table",
    )


# ---------------------------------------------------------------------------
# Entry form
# ---------------------------------------------------------------------------

_W = ~DailyState.can_write  # read-only: every control disabled


def _num_field(label, attr, setter, fid, mode="numeric") -> rx.Component:
    # no placeholder; "×" clears a saved value (same as the old checkbox)
    return ui.form_field(
        label,
        rx.input(
            ui.clear_x(DailyState.can_clear[attr] & DailyState.can_write,
                       DailyState.clear_saved_field(attr),
                       id=f"{fid}-clear"),
            value=getattr(DailyState, attr),
            on_change=getattr(DailyState, setter),
            placeholder="", input_mode=mode, id=fid, disabled=_W),
    )


def _sales_section() -> rx.Component:
    return ui.form_section(
        "Sales",
        ui.form_grid(
            _num_field("Sales (RM)", "f_sales", "set_f_sales", "f-sales",
                       mode="decimal"),
            _num_field("Transactions", "f_transactions",
                       "set_f_transactions", "f-transactions"),
            _num_field("Units Sold", "f_units", "set_f_units", "f-units"),
            columns="3",
        ),
    )


def _summary(label, value, id=None) -> rx.Component:
    """Header chip: "Effective Staff: 8.50" / "Total Loss: RM 70.00"."""
    return ui.status_chip(label + ": " + value, "gray", id=id)


def _manpower_section() -> rx.Component:
    fields = [
        ("f_ft_working", "set_f_ft_working", "Full-Time Staff", "f-ft"),
        ("f_pt_working", "set_f_pt_working", "Part-Time Staff", "f-pt"),
        ("f_new_hires", "set_f_new_hires", "New Hires", "f-hires"),
        ("f_resigned", "set_f_resigned", "Resignations", "f-resigned"),
        ("f_leave_annual", "set_f_leave_annual", "Annual Leave",
         "f-leave-annual"),
        ("f_leave_mc", "set_f_leave_mc", "Medical Leave", "f-leave-mc"),
        ("f_leave_other", "set_f_leave_other", "Other Paid Leave",
         "f-leave-other"),
        ("f_leave_unpaid", "set_f_leave_unpaid", "Unpaid Leave",
         "f-leave-unpaid"),
    ]
    return ui.form_section(
        "Manpower",
        ui.form_grid(*[_num_field(label, attr, setter, fid)
                       for attr, setter, label, fid in fields],
                     columns="3"),
        extra=_summary("Effective Staff", DailyState.form_effective_staff,
                       id="effective-staff"),
    )


def _stock_input(code: dict) -> rx.Component:
    # the code is the label; its description is a tooltip on the code
    return ui.form_field(
        rx.tooltip(rx.text(code["label"], size="2", weight="medium",
                           color=ds.TEXT, cursor="help",
                           id=f"stock-label-{code['code']}"),
                   content=code["name"]),
        rx.input(
            ui.clear_x(DailyState.can_clear_stock[code["code"]]
                       & DailyState.can_write,
                       DailyState.clear_saved_stock(code["code"]),
                       id=f"stock-{code['code']}-clear"),
            value=DailyState.stock_form[code["code"]],
            on_change=lambda v: DailyState.set_stock_amount(code["code"], v),
            placeholder="", input_mode="decimal",
            id=f"stock-{code['code']}", disabled=_W,
        ),
    )


def _code_grid(codes) -> rx.Component:
    return rx.grid(rx.foreach(codes, _stock_input),
                   columns=rx.breakpoints(initial="2", sm="4"),
                   spacing="4", width="100%")


def _group_title(text: str) -> rx.Component:
    return rx.text(text, size="2", weight="medium", color=ds.TEXT_MUTED)


def _stock_section() -> rx.Component:
    return ui.form_section(
        "Stock Loss",
        _group_title("Total Loss"),
        _code_grid(DailyState.contributing_codes),
        rx.box(_group_title("Information Only"), padding_top="0.5em"),
        _code_grid(DailyState.informational_codes),
        extra=_summary("Total Loss", DailyState.form_total_loss,
                       id="total-loss"),
    )


def _expenses_line() -> rx.Component:
    # read-only: expenses are entered on the Expenses page
    return ui.form_section(
        "Expenses",
        extra=rx.text(DailyState.form_expenses, size="2", weight="medium",
                      color=ds.TEXT, id="entry-expenses"),
    )


def _entry_dialog() -> rx.Component:
    """Centered daily entry dialog (kit pattern: pinned header + footer,
    only the sections scroll)."""
    return ui.form_dialog(
        _sales_section(),
        _manpower_section(),
        _stock_section(),
        _expenses_line(),
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
        actions=_record_day_button(),
        tour_key="daily",
    )
