"""Daily Operations module: records overview, entry form, KPI summary.

All reads/writes go through AppService with the session token revalidated
on every event. Form values are strings; parsing into integer cents/counts
happens server-side with explicit blank-vs-zero semantics. Money never
passes through binary floats.
"""

from __future__ import annotations

import calendar
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Optional

import reflex as rx

from ducklab import design as ds
from ducklab import kpi
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError, User,
)
from ducklab.auth_pages import app_service
from ducklab.input_validation import parse_count, parse_money_cents
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import ValidationError
from ducklab.ops_state import (
    SESSION_EXPIRED, OpsState, auth_message, month_label,
)

# ---------------------------------------------------------------------------
# Field specs: (state attr, daily_records column, kind, label)
# kind: "money" (RM string -> cents) | "count" (whole number)
# ---------------------------------------------------------------------------

SALES_FIELDS = [
    ("f_sales", "sales_cents", "money", "Sales"),
    ("f_transactions", "transactions", "count", "Transactions"),
    ("f_units", "units", "count", "Units Sold"),
]

MANPOWER_FIELDS = [
    ("f_ft_working", "ft_working", "count", "Full-Time Staff"),
    ("f_pt_working", "pt_working", "count", "Part-Time Staff"),
    ("f_new_hires", "new_hires_ft", "count", "New Hires"),
    ("f_resigned", "resigned_ft", "count", "Resignations"),
    ("f_leave_annual", "leave_annual", "count", "Annual Leave"),
    ("f_leave_mc", "leave_mc", "count", "Medical Leave"),
    ("f_leave_other", "leave_other_paid", "count", "Other Paid Leave"),
    ("f_leave_unpaid", "leave_unpaid", "count", "Unpaid Leave"),
]

CLEARABLE = {"sales_cents", "transactions", "units"}


class DailyState(OpsState):
    """Daily Operations page state (inherits selectors from OpsState)."""

    stock_codes: list[dict[str, str]] = []   # code/name/sign (sign as str)
    # loaded via AppService.get_month_config; None -> unset (shows "—")
    pt_factor: Optional[float] = None
    day_rows: list[dict[str, str]] = []      # overview table rows
    day_rows_reported: list[dict[str, str]] = []   # days with any entry
    hidden_count: int = 0                    # unreported days (collapsed)
    hidden_label: str = ""
    show_hidden: bool = False
    # "Record a day" header button: today if in month and not entered,
    # else the first unreported day; disabled for read-only / no target
    record_day_iso: str = ""
    record_day_enabled: bool = False
    record_day_tip: str = ""
    day_kpis: dict[str, str] = {}
    # paid expenses by paid date (ISO -> "RM ..."); entered on Expenses only
    day_expenses: dict[str, str] = {}
    can_write: bool = False
    month_finalized: bool = False

    # --- entry form -------------------------------------------------------
    form_open: bool = False
    form_date: str = ""           # ISO date
    form_title: str = ""
    form_error: str = ""
    saving: bool = False
    save_notice: str = ""
    # sales / manpower field values ("" = untouched/blank)
    f_sales: str = ""
    f_transactions: str = ""
    f_units: str = ""
    f_ft_working: str = ""
    f_pt_working: str = ""
    f_new_hires: str = ""
    f_resigned: str = ""
    f_leave_annual: str = ""
    f_leave_mc: str = ""
    f_leave_other: str = ""
    f_leave_unpaid: str = ""
    # explicit clear checkboxes: every value-bearing field (sales +
    # manpower) and every stock-loss code. A ticked field writes NULL —
    # a ticked stock code is REMOVED back to "no entry" (NULL), never 0.
    clear_fields: dict = {}
    clear_stock: dict = {}
    # stock-loss amounts, RM strings keyed by code
    stock_form: dict[str, str] = {}
    stock_original: dict[str, str] = {}
    # original values for unsaved-changes detection
    form_original: dict[str, str] = {}
    # Schedule preview is form-only until the manager explicitly applies it.
    schedule_preview: dict[str, str] = {}
    schedule_preview_date: str = ""

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _form_values(self) -> dict[str, str]:
        return {attr: getattr(self, attr)
                for attr, _, _, _ in SALES_FIELDS + MANPOWER_FIELDS}

    @rx.var
    def form_dirty(self) -> bool:
        if not self.form_open:
            return False
        if self._form_values() != self.form_original:
            return True
        if self.stock_form != self.stock_original:
            return True
        return any(self.clear_fields.values()) or any(self.clear_stock.values())

    @rx.var
    def contributing_codes(self) -> list[dict[str, str]]:
        return [c for c in self.stock_codes if c["sign"] != "0"]

    @rx.var
    def informational_codes(self) -> list[dict[str, str]]:
        return [c for c in self.stock_codes if c["sign"] == "0"]

    @rx.var
    def form_total_loss(self) -> str:
        """Live Total Loss preview using the verified KPI engine."""
        losses: dict[str, int] = {}
        signs: dict[str, int] = {}
        for c in self.stock_codes:
            signs[c["code"]] = int(c["sign"])
            v = self.stock_form.get(c["code"], "").strip()
            if v:
                try:
                    losses[c["code"]] = parse_money_cents(v)
                except ValueError:
                    return "—"
        return ds.fmt_rm(kpi.total_loss_cents(losses, signs))

    @rx.var
    def form_day_label(self) -> str:
        if not self.form_date:
            return ""
        try:
            d = date.fromisoformat(self.form_date)
        except ValueError:
            return ""
        return f"{calendar.day_name[d.weekday()]}, {d.day} {calendar.month_name[d.month]} {d.year}"

    @rx.var
    def form_expenses(self) -> str:
        """That day's paid expenses (read-only line in the entry dialog)."""
        return self.day_expenses.get(self.form_date, "—")

    @rx.var
    def schedule_preview_visible(self) -> bool:
        return (bool(self.schedule_preview)
                and self.schedule_preview_date == self.form_date)

    @rx.var
    def schedule_preview_warning(self) -> str:
        count = int(self.schedule_preview.get("not_set", "0"))
        return (f"{count} scheduled staff have Employment Type Not Set and "
                "are excluded from Full-Time and Part-Time counts."
                if count else "")

    # ------------------------------------------------------------------
    # Apply Schedule: preview first; confirmation changes form fields only.
    # ------------------------------------------------------------------
    def _clear_schedule_preview(self) -> None:
        self.schedule_preview = {}
        self.schedule_preview_date = ""

    @rx.event
    def preview_schedule(self) -> None:
        self.form_error = ""
        self._clear_schedule_preview()
        store_id = self._selected_store_id()
        month = self._selected_month()
        error = _entry_date_error(self.form_date, month)
        if store_id is None and not error:
            error = "Select a store and month first."
        if error:
            self.form_error = error
            return
        try:
            preview = app_service.get_daily_schedule_preview(
                self._actor(), store_id, date.fromisoformat(self.form_date))
        except AuthenticationError:
            self.form_error = SESSION_EXPIRED
            return
        except (AuthorizationError, StoreNotFoundError) as exc:
            self.form_error = auth_message(exc)
            return
        self.schedule_preview = {key: str(value)
                                 for key, value in preview.items()}
        self.schedule_preview_date = self.form_date

    @rx.event
    def cancel_schedule_preview(self) -> None:
        self._clear_schedule_preview()

    @rx.event
    def apply_schedule_preview(self) -> None:
        if not self.schedule_preview_visible:
            return
        for attr, col, _, _ in MANPOWER_FIELDS:
            if col in self.schedule_preview:
                setattr(self, attr, self.schedule_preview[col])
                self.clear_fields = {**self.clear_fields, attr: False}
        self._clear_schedule_preview()

    @rx.var
    def form_effective_staff(self) -> str:
        """FT + PT × configured factor (preview; stored values unchanged)."""
        try:
            ft = parse_count(self.f_ft_working, "ft_working")
            pt = parse_count(self.f_pt_working, "pt_working")
        except ValueError:
            return "—"
        if self.pt_factor is None and pt:
            return kpi.PT_FACTOR_NOT_SET
        eff = kpi.effective_staff(ft, pt, self.pt_factor)
        return f"{float(eff):,.2f}" if eff is not None else "—"

    @rx.event
    def toggle_hidden_days(self):
        self.show_hidden = not self.show_hidden

    # ------------------------------------------------------------------
    # selector overrides: keep daily data in sync with store/month
    # ------------------------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self.form_open = False
        self._clear_schedule_preview()
        self.save_notice = ""
        self._clear_report()
        yield DailyState.refresh_report()
        yield DailyState.load_daily_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self.form_open = False
        self._clear_schedule_preview()
        self.save_notice = ""
        self._clear_report()
        yield DailyState.refresh_report()
        yield DailyState.load_daily_data()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @rx.event
    def load_daily(self) -> rx.event.EventSpec | None:
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield OpsState.load_ops()
        yield DailyState.load_daily_data()
        yield DailyState.open_deep_linked_day()

    @rx.event
    def open_deep_linked_day(self) -> None:
        """Open the entry form for ?date=YYYY-MM-DD when it belongs to the
        selected month (used by Manpower Efficiency's Edit Staffing)."""
        iso = self.router.page.params.get("date", "")
        if not iso:
            return
        try:
            d = date.fromisoformat(iso)
        except ValueError:
            return
        month = self._selected_month()
        if month is None or d.strftime("%Y-%m") != month:
            return
        yield DailyState.open_entry(iso)

    @rx.event
    def load_daily_data(self) -> None:
        """Reload day rows + KPI summary for the current selection."""
        self.day_rows = []
        self.day_rows_reported = []
        self.hidden_count = 0
        self.hidden_label = ""
        self.record_day_iso = ""
        self.record_day_enabled = False
        self.record_day_tip = ""
        self.day_kpis = {}
        self.day_expenses = {}
        self.month_finalized = False
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None or not self.has_stores:
            return
        try:
            user = self._actor()
            from ducklab import authz
            self.can_write = (authz.can_write_operations(user)
                              and self.selected_store_active)
            if not self.stock_codes:
                order = {c: i for i, c in enumerate(ds.STOCK_CODE_ORDER)}
                self.stock_codes = [
                    {"code": c["code"], "name": c["name"],
                     "label": ds.STOCK_CODE_LABELS.get(c["code"], c["code"]),
                     "sign": str(c["contribution_sign"])}
                    for c in sorted(app_service.list_stock_loss_codes(user),
                                    key=lambda c: (order.get(c["code"], 99),
                                                   c["code"]))
                ]
            try:
                factor = app_service.get_month_config(
                    user, store_id, month).part_timer_factor
            except ConfigNotFoundError:
                factor = None
            days = app_service.get_daily_records(user, store_id, month)
            paid_by_day = kpi.paid_cents_by_day(
                [r.input for r in app_service.get_expense_transactions(
                    user, store_id)], month, store_id)
            self.month_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
            # signs from the reference codes already loaded via AppService
            signs = {c["code"]: int(c["sign"]) for c in self.stock_codes}
            day_kpis = self._monthly_kpis(days)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.can_write = False
            self.error_message = auth_message(exc)
            self.report_status = "error"
            return
        self.pt_factor = float(factor) if factor is not None else None

        by_date = {d.date: d for d in days}
        y, m = int(month[:4]), int(month[5:7])
        n_days = calendar.monthrange(y, m)[1]
        rows: list[dict[str, str]] = []
        for day_num in range(1, n_days + 1):
            d = date(y, m, day_num)
            rec = by_date.get(d)
            row = self._day_row(d, rec, signs, factor)
            paid = paid_by_day.get(d)
            row["expenses"] = ds.fmt_rm(paid) if paid is not None else "—"
            rows.append(row)
        self.day_rows = rows
        self.day_expenses = {d.isoformat(): ds.fmt_rm(c)
                             for d, c in paid_by_day.items()}
        # tile: paid this month, by paid date ("—" when nothing was paid)
        day_kpis["expenses"] = (ds.fmt_rm(sum(paid_by_day.values()))
                                if paid_by_day else "—")
        self.day_rows_reported = [r for r in rows
                                  if r["status"] != ds.DAY_NOT_REPORTED]
        self.hidden_count = len(rows) - len(self.day_rows_reported)
        self.hidden_label = ds.days_not_reported_label(self.hidden_count)
        self.day_kpis = day_kpis
        self._set_record_day(rows, authz.can_write_operations(user))

    def _set_record_day(self, rows, role_can_write: bool) -> None:
        """Target + enabled state + tooltip of the "Record a day" button."""
        open_days = [r["iso"] for r in rows
                     if r["status"] == ds.DAY_NOT_REPORTED]
        today = kpi.kl_today().isoformat()
        target = today if today in open_days else (
            open_days[0] if open_days else "")
        self.record_day_iso = target
        if not self.selected_store_active:
            self.record_day_tip = ("This store is deactivated — records "
                                   "are read-only.")
        elif not role_can_write:
            self.record_day_tip = ("Your role has read-only access to "
                                   "daily records.")
        elif not target:
            self.record_day_tip = "Every day of this month is entered."
        else:
            d = date.fromisoformat(target)
            self.record_day_tip = (f"Opens {calendar.day_name[d.weekday()]}"
                                   f" {d.day} {calendar.month_name[d.month]}")
        self.record_day_enabled = bool(
            target and role_can_write and self.selected_store_active)

    def _day_row(self, d: date, rec, signs, factor) -> dict[str, str]:
        base = {
            "iso": d.isoformat(),
            "date": f"{d.day:02d}",
            "weekday": calendar.day_name[d.weekday()][:3],
        }
        if ds.day_status(rec) == ds.DAY_NOT_REPORTED:
            return {**base, "status": ds.DAY_NOT_REPORTED,
                    "status_color": ds.status_color(ds.DAY_NOT_REPORTED),
                    "sales": "—",
                    "transactions": "—", "units": "—", "spr": "—",
                    "upt": "—", "asp": "—", "staff": "—",
                    "sales_per_staff": "—", "loss": "—", "loss_missing": "1"}
        sales = rec.sales_cents
        spr = kpi.spr_cents(sales, rec.transactions) \
            if sales is not None and rec.transactions else None
        upt = kpi.upt(rec.units, rec.transactions) \
            if rec.units is not None and rec.transactions else None
        asp = kpi.asp_cents(sales, rec.units) \
            if sales is not None and rec.units else None
        eff = kpi.effective_staff(rec.ft_working, rec.pt_working, factor)
        sps = kpi.daily_sales_per_staff_cents(rec, factor)
        factor_missing = factor is None and bool(rec.pt_working)
        status = ds.day_status(rec)
        return {
            **base, "status": status,
            "status_color": ds.status_color(status),
            "sales": ds.fmt_rm(sales),
            "transactions": ds.fmt_num(rec.transactions),
            "units": ds.fmt_num(rec.units),
            "spr": (f"RM {spr / 100:,.2f}" if spr is not None else "—"),
            "upt": ds.fmt_num(float(upt) if upt is not None else None),
            "asp": (f"RM {asp / 100:,.2f}" if asp is not None else "—"),
            "staff": (kpi.PT_FACTOR_NOT_SET if factor_missing
                      else f"{float(eff):,.2f}" if eff is not None else "—"),
            "sales_per_staff": (kpi.PT_FACTOR_NOT_SET if factor_missing
                                else f"RM {sps / 100:,.2f}" if sps is not None
                                else "—"),
            "loss": ds.fmt_rm(kpi.total_loss_cents(rec.stock_losses, signs)),
            "loss_missing": "0" if rec.stock_losses else "1",
        }

    def _monthly_kpis(self, days) -> dict[str, str]:
        """Month summary cards. Auth failures propagate to the caller."""
        month = self._selected_month()
        # "—" when nothing was reported (blank is not zero)
        out = {
            "transactions": ds.fmt_num(
                kpi.reported_total(d.transactions for d in days)),
            "units": ds.fmt_num(kpi.reported_total(d.units for d in days)),
        }
        try:
            rep = app_service.preview_month(
                self._actor(), self._selected_store_id(), month)
            s = rep.sales
            proj = (int(s.month_end_sales_cents)
                    if s.month_end_sales_cents is not None else None)
            # projected figures carry the engine's projection label exactly
            # as the Overview: "Projected: <status>" + "Early estimate"
            proj_status, proj_color, early_tag = ds.projection_style(
                s.projection_state,
                s.status_by_level.get("level1", "Awaiting data"))
            out.update({
                "target": ds.fmt_rm(s.targets.get("level1")),
                # Achievement = month-to-date sales / Level 1 (Daily
                # Dashboard!H5); the projection is a separate tile
                # a shortfall is never rounded up (ds.fmt_achievement):
                # MTD by kpi.sales_status vs Level 1, projection by the
                # engine's Level 1 status
                "achievement": (ds.fmt_achievement(
                    s.mtd_achievement_l1,
                    kpi.sales_status(s.sales_mtd_cents,
                                     s.targets.get("level1")))
                                if s.days_entered else "—"),
                "proj_achievement": ds.fmt_achievement(
                    s.achievement_by_level.get("level1"),
                    s.status_by_level.get("level1", "")),
                "projected": ds.fmt_rm(proj),        # one money format
                "projected_compact": ds.fmt_rm_compact(proj,
                                                       whole_below=True),
                "projected_exact": ds.fmt_rm(proj),
                "proj_status": proj_status,
                "proj_color": proj_color,
                "early_tag": early_tag,
                "early_tip": (ds.EARLY_ESTIMATE_TIP.format(
                    n=s.days_entered, m=s.days_in_month)
                    if early_tag else ""),
                # complete month: no projected tiles (they would repeat)
                "complete": "1" if s.projection_state == "actual" else "",
                "projected_sub": ("Actual"
                                  if s.projection_state == "actual"
                                  else "Projected"),
                "proj_achievement_sub": (
                    "Month-end vs Level 1"
                    if s.projection_state == "actual"
                    else "Projected month-end vs Level 1"),
                "loss": ds.fmt_rm(rep.stock.total_loss_cents),
            })
        except (ConfigNotFoundError, StoreNotFoundError, RepositoryError):
            # no month configuration yet: target-based cards stay blank
            s = None
            out.update({"target": "—", "achievement": "—",
                        "proj_achievement": "—", "projected": "—",
                        "projected_compact": "—", "projected_exact": "—",
                        "proj_status": "", "proj_color": "gray",
                        "proj_achievement_sub": "", "projected_sub": "",
                        "early_tag": "", "early_tip": "", "loss": "—",
                        "complete": ""})
        summary = _sales_summary(days, s)
        out.update(summary)
        # compact variants for narrow tiles (exact value in the tooltip)
        if s is not None:
            mtd, avg = s.sales_mtd_cents, s.average_per_day
        else:
            mtd = kpi.sales_mtd_cents(days)
            avg = kpi.average_per_reported_day(
                mtd, kpi.days_with_sales_entered(days))
        entered = summary["mtd"] != "—"
        out["mtd_compact"] = ds.fmt_rm_compact(mtd if entered else None,
                                               whole_below=True)
        out["avg_daily_exact"] = ds.fmt_rm(
            int(avg) if entered and avg is not None else None)
        level1 = s.targets.get("level1") if s is not None else None
        out["target_sub"] = ("Level 1 target " + ds.fmt_rm0(level1)
                             if level1 is not None else "No Level 1 target")
        return out


    # --- explicit field setters (Reflex 0.10: no auto set_ handlers) --------
    @rx.event
    def set_f_sales(self, v: str): self.f_sales = v
    @rx.event
    def set_f_transactions(self, v: str): self.f_transactions = v
    @rx.event
    def set_f_units(self, v: str): self.f_units = v
    @rx.event
    def set_f_ft_working(self, v: str): self.f_ft_working = v
    @rx.event
    def set_f_pt_working(self, v: str): self.f_pt_working = v
    @rx.event
    def set_f_new_hires(self, v: str): self.f_new_hires = v
    @rx.event
    def set_f_resigned(self, v: str): self.f_resigned = v
    @rx.event
    def set_f_leave_annual(self, v: str): self.f_leave_annual = v
    @rx.event
    def set_f_leave_mc(self, v: str): self.f_leave_mc = v
    @rx.event
    def set_f_leave_other(self, v: str): self.f_leave_other = v
    @rx.event
    def set_f_leave_unpaid(self, v: str): self.f_leave_unpaid = v
    @rx.event
    def set_clear_field(self, attr: str, v: bool):
        self.clear_fields = {**self.clear_fields, attr: v}

    @rx.event
    def set_clear_stock(self, code: str, v: bool):
        self.clear_stock = {**self.clear_stock, code: v}

    # "×" beside a field with a saved value: the same as ticking "Clear
    # saved value" with the field blank (save writes NULL / removes the
    # stock code; never 0)
    @rx.event
    def clear_saved_field(self, attr: str):
        setattr(self, attr, "")
        self.clear_fields = {**self.clear_fields, attr: True}

    @rx.event
    def clear_saved_stock(self, code: str):
        self.stock_form[code] = ""
        self.clear_stock = {**self.clear_stock, code: True}

    @rx.var
    def can_clear(self) -> dict[str, bool]:
        """Field attr -> the "×" shows: a saved value not yet cleared."""
        return {attr: bool(v) and not self.clear_fields.get(attr)
                for attr, v in self.form_original.items()}

    @rx.var
    def can_clear_stock(self) -> dict[str, bool]:
        return {code: bool(v) and not self.clear_stock.get(code)
                for code, v in self.stock_original.items()}

    # Back-compat shims for the three original sales checkboxes.
    @rx.event
    def set_c_sales(self, v: bool): self.set_clear_field("f_sales", v)
    @rx.event
    def set_c_transactions(self, v: bool):
        self.set_clear_field("f_transactions", v)
    @rx.event
    def set_c_units(self, v: bool): self.set_clear_field("f_units", v)

    # ------------------------------------------------------------------
    # form open/close
    # ------------------------------------------------------------------
    @rx.event
    def open_entry(self, iso_date: str) -> None:
        # Validate the client-sent date BEFORE touching any form state.
        store_id = self._selected_store_id()
        month = self._selected_month()
        error = _entry_date_error(iso_date, month)
        rec = None
        if not error and store_id is not None:
            try:
                user = self._actor()
                d = date.fromisoformat(iso_date)
                days = app_service.get_daily_records(user, store_id, month)
                rec = next((r for r in days if r.date == d), None)
            except (AuthenticationError, AuthorizationError,
                    StoreNotFoundError) as exc:
                error = auth_message(exc)
        if error:
            self.form_error = error
            return
        self.form_error = ""
        self.save_notice = ""
        self._clear_schedule_preview()
        self.form_date = iso_date
        self.form_title = "Daily Entry"
        values = {attr: "" for attr, _, _, _ in SALES_FIELDS + MANPOWER_FIELDS}
        self.stock_form = {c["code"]: "" for c in self.stock_codes}
        if rec is not None:
            for attr, col, kind, _ in SALES_FIELDS + MANPOWER_FIELDS:
                v = getattr(rec, col)
                if v is not None:
                    values[attr] = (f"{v / 100:.2f}" if kind == "money"
                                    else str(v))
            for code, cents in rec.stock_losses.items():
                self.stock_form[code] = f"{cents / 100:.2f}"
        for attr, v in values.items():
            setattr(self, attr, v)
        self.clear_fields = {}
        self.clear_stock = {}
        self.form_original = dict(values)
        self.stock_original = dict(self.stock_form)
        self.form_open = True

    @rx.event
    def close_entry(self) -> None:
        self.form_open = False
        self.form_error = ""
        self._clear_schedule_preview()

    @rx.event
    def set_stock_amount(self, code: str, value: str) -> None:
        self.stock_form[code] = value

    # ------------------------------------------------------------------
    # save
    # ------------------------------------------------------------------
    @rx.event
    def save_entry(self) -> None:
        self.form_error = ""
        self.save_notice = ""
        self.form_open = False  # close immediately; reopen on failure
        if not self.form_date:
            return
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            self.form_error = "Select a store and month first."
            return
        error = _entry_date_error(self.form_date, month)
        if error:
            self.form_error = error
            self.form_open = True
            return
        d = date.fromisoformat(self.form_date)

        # parse + validate
        fields: dict = {}
        try:
            for attr, col, kind, label in SALES_FIELDS + MANPOWER_FIELDS:
                raw = getattr(self, attr).strip()
                if raw:
                    fields[col] = (parse_money_cents(raw) if kind == "money"
                                   else parse_count(raw, label))
                elif self.clear_fields.get(attr):
                    fields[col] = None  # explicit clear -> NULL
        except ValueError as exc:
            self.form_error = str(exc)
            self.form_open = True
            return
        # Stock: ticked clear checkbox REMOVES the code back to "no entry"
        # (NULL), never written as 0.
        stock: dict[str, int] = {}
        stock_remove: list[str] = []
        for c in self.stock_codes:
            code = c["code"]
            raw = self.stock_form.get(code, "").strip()
            orig = self.stock_original.get(code, "").strip()
            if raw:
                try:
                    stock[code] = parse_money_cents(raw)
                except ValueError as exc:
                    self.form_error = f"{c['label']}: {exc}"
                    self.form_open = True
                    return
            elif self.clear_stock.get(code) and orig:
                stock_remove.append(code)
        if (not fields and not stock and not stock_remove
                and not any(self.clear_fields.values())):
            self.form_error = "Nothing to save — enter at least one value."
            self.form_open = True
            return

        try:
            user = self._actor()
        except AuthenticationError:
            self.form_error = SESSION_EXPIRED
            self.form_open = True
            return
        self.saving = True
        yield
        try:
            kwargs: dict = dict(fields)
            if stock:
                kwargs["stock_losses"] = stock
            if stock_remove:
                kwargs["stock_remove"] = stock_remove
            app_service.record_daily(user, store_id, d, **kwargs)
        except AuthenticationError:
            self.form_error = SESSION_EXPIRED
            self.form_open = True
            return
        except AuthorizationError:
            self.form_error = "Your role cannot modify operational records."
            self.form_open = True
            return
        except ValidationError as exc:
            self.form_error = str(exc)
            self.form_open = True
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.form_error = f"Save failed: {exc}"
            self.form_open = True
            return
        finally:
            self.saving = False
        # read-after-write: reload from persisted state
        self.save_notice = (
            f"Saved {self.form_day_label}. KPIs recalculated live.")
        yield DailyState.load_daily_data
        yield DailyState.refresh_report


# ---------------------------------------------------------------------------
# parsing helpers (server-side; never silently blank -> 0)
# ---------------------------------------------------------------------------

def _entry_date_error(iso_date, month: Optional[str]) -> str:
    """'' when ``iso_date`` is a real date inside the selected month;
    otherwise the message to show (the form is not opened)."""
    try:
        d = date.fromisoformat(str(iso_date))
    except ValueError:
        return "Invalid date — choose a day from the list."
    if month is None:
        return "Select a store and month first."
    if d.strftime("%Y-%m") != month:
        return (f"{d.isoformat()} is outside the selected month "
                f"({month_label(month)}).")
    return ""


def _sales_summary(days, sales=None) -> dict[str, str]:
    """MTD / average / reported-days cards. Uses the report's figures
    (SalesSection) when available, else the same kpi engine functions on
    the saved records (no month config yet). Exact Decimal — no float
    math; "—" when no sales days are entered."""
    if sales is not None:
        entered = sales.days_entered
        mtd = sales.sales_mtd_cents
        avg = sales.average_per_day
    else:
        entered = kpi.days_with_sales_entered(days)
        mtd = kpi.sales_mtd_cents(days)
        avg = kpi.average_per_reported_day(mtd, entered)
    return {
        "mtd": ds.fmt_rm(mtd if entered else None),
        "avg_daily": ds.fmt_rm(int(avg) if entered and avg is not None
                               else None),           # one money format
        "reported_days": f"{entered} sales days reported",
    }
