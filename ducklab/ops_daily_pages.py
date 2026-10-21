"""Daily Operations page: monthly KPI summary, day-by-day records table,
and the daily entry drawer (sales / manpower / stock loss).
"""

from __future__ import annotations

import reflex as rx

from ducklab import design as ds
from ducklab.ops_daily_state import (
    MANPOWER_FIELDS, SALES_FIELDS, DailyState,
)
from ducklab.ops_shell import shell

STATUS_COLOR = {"complete": "green", "partial": "amber", "unreported": "gray"}


def _kpi_summary() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading(
                "Monthly Summary", "chart-line",
                extra=rx.cond(
                    DailyState.month_finalized,
                    rx.badge(
                        "Finalized snapshot exists — edits affect live "
                        "calculations only, never preserved snapshots",
                        color_scheme="violet", variant="soft", size="1"),
                ),
            ),
            rx.grid(
                ds.kpi_card("Month-to-Date Sales", DailyState.day_kpis["mtd"],
                            icon="banknote",
                            sub=DailyState.day_kpis["reported_days"]),
                ds.kpi_card("Level 1 Target", DailyState.day_kpis["target"],
                            icon="target"),
                ds.kpi_card("Achievement", DailyState.day_kpis["achievement"],
                            icon="percent"),
                ds.kpi_card("Projected Month-End",
                            DailyState.day_kpis["projected"],
                            icon="chart-line"),
                ds.kpi_card("Avg Daily Sales", DailyState.day_kpis["avg_daily"]),
                ds.kpi_card("Transactions", DailyState.day_kpis["transactions"]),
                ds.kpi_card("Units Sold", DailyState.day_kpis["units"]),
                ds.kpi_card("Total Loss", DailyState.day_kpis["loss"],
                            icon="triangle-alert", accent="red"),
                columns=rx.breakpoints(initial="2", sm="3", lg="6"),
                spacing="3", width="100%",
            ),
            spacing="4", width="100%",
        ),
        width="100%", id="daily-kpis",
    )


def _status_badge(row) -> rx.Component:
    return rx.badge(
        row["status"],
        color_scheme=rx.match(
            row["status"],
            ("complete", "green"), ("partial", "amber"), "gray",
        ),
        variant="soft", size="1",
    )


def _day_row(row) -> rx.Component:
    return rx.table.row(
        rx.table.cell(
            rx.hstack(rx.text(row["date"], weight="bold"),
                      rx.text(row["weekday"], size="1", color=ds.TEXT_FAINT),
                      spacing="2", align="baseline"),
            white_space="nowrap"),
        rx.table.cell(row["sales"], white_space="nowrap"),
        rx.table.cell(row["transactions"]),
        rx.table.cell(row["units"]),
        rx.table.cell(row["spr"], white_space="nowrap"),
        rx.table.cell(row["upt"]),
        rx.table.cell(row["asp"], white_space="nowrap"),
        rx.table.cell(row["staff"]),
        rx.table.cell(row["sales_per_staff"], white_space="nowrap"),
        rx.table.cell(
            rx.cond(row["loss_missing"] == "1",
                    rx.text("—", color=ds.TEXT_FAINT),
                    row["loss"]),
            white_space="nowrap"),
        rx.table.cell(_status_badge(row)),
        rx.table.cell(
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
        align="center",
    )


def _records_table() -> rx.Component:
    return rx.card(
        rx.vstack(
            ds.section_heading("Daily Records", "calendar-check"),
            rx.scroll_area(
                rx.table.root(
                    rx.table.header(
                        rx.table.row(
                            rx.table.column_header_cell("Date"),
                            rx.table.column_header_cell("Sales"),
                            rx.table.column_header_cell("Txns"),
                            rx.table.column_header_cell("Units"),
                            rx.table.column_header_cell("SPR"),
                            rx.table.column_header_cell("UPT"),
                            rx.table.column_header_cell("ASP"),
                            rx.table.column_header_cell("Staff"),
                            rx.table.column_header_cell("Sales/Staff"),
                            rx.table.column_header_cell("Stock Loss"),
                            rx.table.column_header_cell("Status"),
                            rx.table.column_header_cell(""),
                        ),
                    ),
                    rx.table.body(rx.foreach(DailyState.day_rows, _day_row)),
                    variant="surface", width="100%",
                ),
                type="auto", scrollbars="horizontal",
                style={"maxWidth": "100%"},
            ),
            rx.hstack(
                rx.badge("complete", color_scheme="green", variant="soft",
                         size="1"),
                rx.badge("partial", color_scheme="amber", variant="soft",
                         size="1"),
                rx.badge("unreported", color_scheme="gray", variant="soft",
                         size="1"),
                rx.text(
                    "Completeness reflects all entered fields, not sales "
                    "alone. Unreported dates are calendar placeholders — no "
                    "database rows exist for them.",
                    size="1", color=ds.TEXT_FAINT),
                spacing="2", align="center", wrap="wrap",
            ),
            spacing="3", width="100%",
        ),
        width="100%", id="daily-table",
    )


# ---------------------------------------------------------------------------
# Entry form
# ---------------------------------------------------------------------------

def _sales_section() -> rx.Component:
    return rx.vstack(
        ds.section_heading("Sales", "banknote"),
        rx.grid(
            rx.vstack(
                rx.text("Daily sales (RM)", size="2", weight="medium"),
                rx.input(value=DailyState.f_sales,
                         on_change=DailyState.set_f_sales,
                         placeholder="0.00", input_mode="decimal",
                         id="f-sales", disabled=~DailyState.can_write),
                rx.checkbox("Clear saved value", checked=DailyState.clear_fields["f_sales"],
                            on_change=lambda v: DailyState.set_clear_field("f_sales", v), size="1",
                            disabled=~DailyState.can_write),
                spacing="1", align="start", width="100%",
            ),
            rx.vstack(
                rx.text("Transactions", size="2", weight="medium"),
                rx.input(value=DailyState.f_transactions,
                         on_change=DailyState.set_f_transactions,
                         placeholder="—", input_mode="numeric", id="f-transactions",
                         disabled=~DailyState.can_write),
                rx.checkbox("Clear saved value",
                            checked=DailyState.clear_fields["f_transactions"],
                            on_change=lambda v: DailyState.set_clear_field("f_transactions", v), size="1",
                            disabled=~DailyState.can_write),
                spacing="1", align="start", width="100%",
            ),
            rx.vstack(
                rx.text("Units sold", size="2", weight="medium"),
                rx.input(value=DailyState.f_units,
                         on_change=DailyState.set_f_units,
                         placeholder="—", input_mode="numeric", id="f-units",
                         disabled=~DailyState.can_write),
                rx.checkbox("Clear saved value", checked=DailyState.clear_fields["f_units"],
                            on_change=lambda v: DailyState.set_clear_field("f_units", v), size="1",
                            disabled=~DailyState.can_write),
                spacing="1", align="start", width="100%",
            ),
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            spacing="3", width="100%",
        ),
        rx.text(
            "Blank fields leave saved values untouched. Tick “Clear saved "
            "value” to deliberately reset a field to unreported. Enter 0 to "
            "record a genuine zero.",
            size="1", color=ds.TEXT_FAINT),
        spacing="3", align="start", width="100%",
    )


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
    return rx.vstack(
        ds.section_heading(
            "Manpower", "users",
            extra=rx.hstack(
                rx.text("Effective staff:", size="1", color=ds.TEXT_FAINT),
                rx.badge(DailyState.form_effective_staff, variant="soft",
                         color_scheme="indigo"),
                spacing="1"),
        ),
        rx.grid(
            *[
                rx.vstack(
                    rx.text(label, size="2", weight="medium"),
                    rx.input(
                        value=getattr(DailyState, attr),
                        on_change=getattr(DailyState, setter),
                        placeholder="—", input_mode="numeric", id=fid,
                        disabled=~DailyState.can_write),
                    rx.checkbox(
                        "Clear saved value",
                        checked=DailyState.clear_fields[attr],
                        on_change=lambda v: DailyState.set_clear_field(attr, v),
                        size="1", disabled=~DailyState.can_write),
                    spacing="1", align="start", width="100%",
                )
                for attr, setter, label, fid in fields
            ],
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            spacing="3", width="100%",
        ),
        rx.text(
            "Effective staff = FT + PT × the configured part-timer factor "
            "for this store/month. Blank attendance stays unreported.",
            size="1", color=ds.TEXT_FAINT),
        spacing="3", align="start", width="100%",
    )


def _stock_input(code: dict) -> rx.Component:
    return rx.vstack(
        rx.hstack(rx.text(code["code"], size="2", weight="bold"),
                  rx.text(code["name"], size="1", color=ds.TEXT_FAINT),
                  spacing="2", align="baseline", wrap="wrap"),
        rx.input(
            value=DailyState.stock_form[code["code"]],
            on_change=lambda v: DailyState.set_stock_amount(code["code"], v),
            placeholder="0.00", input_mode="decimal",
            id=f"stock-{code['code']}",
            disabled=~DailyState.can_write,
        ),
        rx.checkbox(
            "Clear saved value",
            checked=DailyState.clear_stock[code["code"]],
            on_change=lambda v: DailyState.set_clear_stock(code["code"], v),
            size="1", disabled=~DailyState.can_write,
        ),
        spacing="1", align="start", width="100%",
    )


def _stock_section() -> rx.Component:
    return rx.vstack(
        ds.section_heading(
            "Stock Loss", "boxes",
            extra=rx.hstack(
                rx.text("Total Loss:", size="1", color=ds.TEXT_FAINT),
                rx.badge(DailyState.form_total_loss, variant="soft",
                         color_scheme="red"),
                spacing="1"),
        ),
        rx.text("Contributes to Total Loss "
                "(DMO + DMC + DMP + Missing − Extra)",
                size="2", weight="medium", color=ds.TEXT_MUTED),
        rx.grid(
            rx.foreach(DailyState.contributing_codes, _stock_input),
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            spacing="3", width="100%",
        ),
        rx.divider(),
        rx.text("Informational only — excluded from Total Loss",
                size="2", weight="medium", color=ds.TEXT_MUTED),
        rx.grid(
            rx.foreach(DailyState.informational_codes, _stock_input),
            columns=rx.breakpoints(initial="1", sm="2", lg="3"),
            spacing="3", width="100%",
        ),
        spacing="3", align="start", width="100%",
    )


def _entry_dialog() -> rx.Component:
    """Centered modal dialog for daily entry.

    Uses rx.dialog (Radix Dialog) instead of a right-side drawer: the drawer
    was anchored to the viewport edge and animated in with a transform, which
    both kept it off-center and shifted its position while opening. The
    dialog overlay + content are portaled to <body> (no transformed/filtered
    ancestor), so fixed positioning centers it reliably.
    """
    return rx.dialog.root(
        rx.dialog.content(
            rx.vstack(
                # header — stays visible while the body scrolls
                rx.hstack(
                    rx.vstack(
                        rx.heading(DailyState.form_title, size="4"),
                        rx.text(DailyState.form_day_label, size="2",
                                color=ds.TEXT_MUTED),
                        spacing="0", align="start"),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.icon_button(rx.icon("x", size=16),
                                       variant="ghost",
                                       id="entry-close")),
                    width="100%", align="center", flex_shrink="0"),
                rx.cond(
                    ~DailyState.can_write,
                    rx.callout(
                        "You have read-only access. Contact an "
                        "administrator to modify operational records.",
                        icon="lock", color_scheme="gray", variant="soft",
                        width="100%"),
                ),
                rx.cond(
                    DailyState.form_error != "",
                    ds.error_callout(DailyState.form_error),
                ),
                # scrollable body
                rx.scroll_area(
                    rx.vstack(
                        _sales_section(),
                        rx.divider(),
                        _manpower_section(),
                        rx.divider(),
                        _stock_section(),
                        spacing="4", width="100%",
                    ),
                    flex_grow="1", min_height="0", type="auto",
                    width="100%",
                ),
                # footer — stays visible while the body scrolls
                rx.hstack(
                    rx.cond(
                        DailyState.form_dirty & DailyState.can_write,
                        rx.text("Unsaved changes", size="1",
                                color=ds.WARNING)),
                    rx.spacer(),
                    rx.dialog.close(
                        rx.button("Cancel", variant="soft",
                                  color_scheme="gray", id="entry-cancel")),
                    rx.cond(
                        DailyState.can_write,
                        rx.button(
                            rx.cond(DailyState.saving,
                                    rx.spinner(size="1"),
                                    rx.icon("save", size=14)),
                            "Save Entry",
                            color_scheme="indigo",
                            on_click=DailyState.save_entry,
                            disabled=DailyState.saving,
                            id="entry-save")),
                    width="100%", align="center", flex_shrink="0",
                    border_top=f"1px solid {ds.BORDER}", padding_top="0.75em",
                ),
                spacing="4", width="100%", height="100%", min_height="0",
            ),
            width="min(720px, 92vw)",
            style={
                "maxWidth": "min(720px, 92vw)",
                "maxHeight": "85vh",
                "display": "flex",
                "flexDirection": "column",
            },
            background=ds.BG, padding="1.5em",
        ),
        open=DailyState.form_open,
        on_open_change=lambda v: DailyState.close_entry(),
    )


def daily_page() -> rx.Component:
    return shell(
        "Daily Operations",
        rx.vstack(
            rx.text(
                "Record daily sales, staffing, and stock losses. Saving "
                "recalculates the live KPIs immediately.",
                size="2", color=ds.TEXT_MUTED),
            rx.cond(
                DailyState.save_notice != "",
                rx.callout(DailyState.save_notice, icon="circle-check",
                           color_scheme="green", variant="soft",
                           width="100%", id="save-notice"),
            ),
            rx.cond(
                DailyState.report_status == "empty",
                ds.empty_state("No store or month selected",
                               DailyState.error_message,
                               icon="store"),
                rx.vstack(
                    _kpi_summary(),
                    _records_table(),
                    spacing="5", width="100%",
                ),
            ),
            _entry_dialog(),
            spacing="4", width="100%",
        ),
        on_store=DailyState.select_store,
        on_month=DailyState.select_month,
    )
