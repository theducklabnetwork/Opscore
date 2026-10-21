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
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import ValidationError
from ducklab.ops_state import OpsState, month_label

# ---------------------------------------------------------------------------
# Field specs: (state attr, daily_records column, kind, label)
# kind: "money" (RM string -> cents) | "count" (whole number)
# ---------------------------------------------------------------------------

SALES_FIELDS = [
    ("f_sales", "sales_cents", "money", "Daily sales (RM)"),
    ("f_transactions", "transactions", "count", "Transactions"),
    ("f_units", "units", "count", "Units sold"),
]

MANPOWER_FIELDS = [
    ("f_ft_working", "ft_working", "count", "Full-time staff working"),
    ("f_pt_working", "pt_working", "count", "Part-time staff working"),
    ("f_new_hires", "new_hires_ft", "count", "New hires (FT)"),
    ("f_resigned", "resigned_ft", "count", "Resignations (FT)"),
    ("f_leave_annual", "leave_annual", "count", "Annual leave"),
    ("f_leave_mc", "leave_mc", "count", "Medical leave"),
    ("f_leave_other", "leave_other_paid", "count", "Other paid leave"),
    ("f_leave_unpaid", "leave_unpaid", "count", "Unpaid leave"),
]

CLEARABLE = {"sales_cents", "transactions", "units"}


class DailyState(OpsState):
    """Daily Operations page state (inherits selectors from OpsState)."""

    stock_codes: list[dict[str, str]] = []   # code/name/sign (sign as str)
    day_rows: list[dict[str, str]] = []      # overview table rows
    day_kpis: dict[str, str] = {}
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
    # explicit clear checkboxes (sales block only)
    c_sales: bool = False
    c_transactions: bool = False
    c_units: bool = False
    # stock-loss amounts, RM strings keyed by code
    stock_form: dict[str, str] = {}
    stock_original: dict[str, str] = {}
    # original values for unsaved-changes detection
    form_original: dict[str, str] = {}

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
        return self.c_sales or self.c_transactions or self.c_units

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
                    losses[c["code"]] = _parse_money(v)
                except ValueError:
                    return "—"
        return ds.fmt_rm(kpi.total_loss_cents(losses, signs))

    @rx.var
    def form_day_label(self) -> str:
        if not self.form_date:
            return ""
        d = date.fromisoformat(self.form_date)
        return f"{calendar.day_name[d.weekday()]}, {d.day} {calendar.month_name[d.month]} {d.year}"

    @rx.var
    def form_effective_staff(self) -> str:
        """FT + PT × configured factor (preview; stored values unchanged)."""
        try:
            ft = _parse_count(self.f_ft_working, "ft_working")
            pt = _parse_count(self.f_pt_working, "pt_working")
        except ValueError:
            return "—"
        factor = self._pt_factor()
        eff = kpi.effective_staff(ft, pt, factor)
        return f"{float(eff):,.2f}" if eff is not None else "—"

    def _pt_factor(self):
        try:
            cfg = app_service.read_repo.get_month_config(
                self._selected_store_id(), self._selected_month())
            return cfg.part_timer_factor
        except Exception:
            return None

    # ------------------------------------------------------------------
    # selector overrides: keep daily data in sync with store/month
    # ------------------------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self.form_open = False
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
        self.day_kpis = {}
        self.month_finalized = False
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None or not self.has_stores:
            return
        try:
            user = self._actor()
        except AuthenticationError:
            self.error_message = "Your session has expired. Please sign in again."
            self.report_status = "error"
            return
        from ducklab import authz
        self.can_write = authz.can_write_operations(user)
        if not self.stock_codes:
            self.stock_codes = [
                {"code": c["code"], "name": c["name"],
                 "sign": str(c["contribution_sign"])}
                for c in app_service.list_stock_loss_codes(user)
            ]
        try:
            days = app_service.get_daily_records(user, store_id, month)
        except (AuthorizationError, StoreNotFoundError):
            self.day_rows = []
            return
        try:
            signs = app_service.read_repo.get_contribution_signs()
        except Exception:
            signs = {}
        try:
            cfg = app_service.get_month_config(user, store_id, month)
            factor = cfg.part_timer_factor
        except ConfigNotFoundError:
            factor = None
        try:
            self.month_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
        except Exception:
            self.month_finalized = False

        by_date = {d.date: d for d in days}
        y, m = int(month[:4]), int(month[5:7])
        n_days = calendar.monthrange(y, m)[1]
        rows: list[dict[str, str]] = []
        for day_num in range(1, n_days + 1):
            d = date(y, m, day_num)
            rec = by_date.get(d)
            rows.append(self._day_row(d, rec, signs, factor))
        self.day_rows = rows
        self.day_kpis = self._monthly_kpis(days)

    def _day_row(self, d: date, rec, signs, factor) -> dict[str, str]:
        base = {
            "iso": d.isoformat(),
            "date": f"{d.day:02d}",
            "weekday": calendar.day_name[d.weekday()][:3],
        }
        if rec is None:
            return {**base, "status": "unreported", "sales": "—",
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
        sps = (Decimal(sales) / eff) if (sales is not None and eff) else None
        filled = sum(v is not None for v in (
            rec.sales_cents, rec.transactions, rec.units, rec.ft_working,
            rec.pt_working, rec.new_hires_ft, rec.resigned_ft,
            rec.leave_annual, rec.leave_mc, rec.leave_other_paid,
            rec.leave_unpaid)) + len(rec.stock_losses)
        status = "complete" if filled >= 3 else "partial"
        return {
            **base, "status": status,
            "sales": ds.fmt_rm(sales),
            "transactions": ds.fmt_num(rec.transactions),
            "units": ds.fmt_num(rec.units),
            "spr": (f"RM {spr / 100:,.2f}" if spr is not None else "—"),
            "upt": ds.fmt_num(float(upt) if upt is not None else None),
            "asp": (f"RM {asp / 100:,.2f}" if asp is not None else "—"),
            "staff": (f"{float(eff):,.2f}" if eff is not None else "—"),
            "sales_per_staff": (f"RM {sps / 100:,.2f}" if sps is not None
                                else "—"),
            "loss": ds.fmt_rm(kpi.total_loss_cents(rec.stock_losses, signs)),
            "loss_missing": "0" if rec.stock_losses else "1",
        }

    def _monthly_kpis(self, days) -> dict[str, str]:
        sales_days = [d for d in days if d.sales_cents is not None]
        sales_total = sum(d.sales_cents for d in sales_days)
        tx = sum(d.transactions or 0 for d in days)
        units = sum(d.units or 0 for d in days)
        month = self._selected_month()
        out = {
            "mtd": ds.fmt_rm(sales_total if days else None),
            "reported_days": f"{len(sales_days)} sales days reported",
            "avg_daily": ds.fmt_rm0(
                round(sales_total / len(sales_days)) if sales_days else None),
            "transactions": ds.fmt_num(tx if days else None),
            "units": ds.fmt_num(units if days else None),
        }
        try:
            rep = app_service.preview_month(
                self._actor(), self._selected_store_id(), month)
            s = rep.sales
            out.update({
                "target": ds.fmt_rm(s.targets.get("level1")),
                "achievement": ds.fmt_pct(s.achievement_by_level.get("level1")),
                "projected": ds.fmt_rm0(
                    int(s.month_end_sales_cents)
                    if s.month_end_sales_cents is not None else None),
                "loss": ds.fmt_rm(rep.stock.total_loss_cents),
            })
        except Exception:
            out.update({"target": "—", "achievement": "—",
                        "projected": "—", "loss": "—"})
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
    def set_c_sales(self, v: bool): self.c_sales = v
    @rx.event
    def set_c_transactions(self, v: bool): self.c_transactions = v
    @rx.event
    def set_c_units(self, v: bool): self.c_units = v

    # ------------------------------------------------------------------
    # form open/close
    # ------------------------------------------------------------------
    @rx.event
    def open_entry(self, iso_date: str) -> None:
        self.form_error = ""
        self.save_notice = ""
        self.form_date = iso_date
        self.form_title = "Daily Entry"
        store_id = self._selected_store_id()
        month = self._selected_month()
        values = {attr: "" for attr, _, _, _ in SALES_FIELDS + MANPOWER_FIELDS}
        self.stock_form = {c["code"]: "" for c in self.stock_codes}
        if store_id is not None and month is not None:
            try:
                user = self._actor()
                days = app_service.get_daily_records(user, store_id, month)
                d = date.fromisoformat(iso_date)
                rec = next((r for r in days if r.date == d), None)
            except Exception:
                rec = None
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
        self.c_sales = self.c_transactions = self.c_units = False
        self.form_original = dict(values)
        self.stock_original = dict(self.stock_form)
        self.form_open = True

    @rx.event
    def close_entry(self) -> None:
        self.form_open = False
        self.form_error = ""

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
        try:
            d = date.fromisoformat(self.form_date)
        except ValueError:
            self.form_error = "Invalid date."
            self.form_open = True
            return
        if d.strftime("%Y-%m") != month:
            self.form_error = (
                f"{d.isoformat()} is outside the selected month "
                f"({month_label(month)}).")
            self.form_open = True
            return

        # parse + validate
        fields: dict = {}
        clears = {"f_sales": self.c_sales, "f_transactions": self.c_transactions,
                  "f_units": self.c_units}
        try:
            for attr, col, kind, label in SALES_FIELDS + MANPOWER_FIELDS:
                raw = getattr(self, attr).strip()
                if raw:
                    fields[col] = (_parse_money(raw) if kind == "money"
                                   else _parse_count(raw, label))
                elif attr in clears and clears[attr]:
                    fields[col] = None  # explicit clear
        except ValueError as exc:
            self.form_error = str(exc)
            self.form_open = True
            return
        stock: dict[str, int] = {}
        for c in self.stock_codes:
            code = c["code"]
            raw = self.stock_form.get(code, "").strip()
            orig = self.stock_original.get(code, "").strip()
            if raw:
                try:
                    stock[code] = _parse_money(raw)
                except ValueError as exc:
                    self.form_error = f"{c['name']} ({code}): {exc}"
                    self.form_open = True
                    return
            elif orig:
                stock[code] = 0  # previously had a value, now cleared
        if not fields and not stock and not any(clears.values()):
            self.form_error = "Nothing to save — enter at least one value."
            self.form_open = True
            return

        try:
            user = self._actor()
        except AuthenticationError:
            self.form_error = "Your session has expired. Please sign in again."
            self.form_open = True
            return
        self.saving = True
        yield
        try:
            kwargs: dict = dict(fields)
            if stock:
                kwargs["stock_losses"] = stock
            app_service.record_daily(user, store_id, d, **kwargs)
        except AuthorizationError:
            self.saving = False
            self.form_error = "Your role cannot modify operational records."
            self.form_open = True
            return
        except ValidationError as exc:
            self.saving = False
            self.form_error = str(exc)
            self.form_open = True
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.saving = False
            self.form_error = f"Save failed: {exc}"
            self.form_open = True
            return
        # read-after-write: reload from persisted state
        self.saving = False
        self.save_notice = (
            f"Saved {self.form_day_label}. KPIs recalculated live.")
        yield DailyState.load_daily_data
        yield DailyState.refresh_report


# ---------------------------------------------------------------------------
# parsing helpers (server-side; never silently blank -> 0)
# ---------------------------------------------------------------------------

def _parse_money(raw: str) -> int:
    """'350819' or '350,819.00' -> integer cents. Raises ValueError."""
    cleaned = raw.replace(",", "").replace("RM", "").strip()
    try:
        dec = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {raw!r}")
    cents = int(dec * 100)
    if Decimal(cents) != dec * 100:
        raise ValueError(f"Amount has sub-cent precision: {raw!r}")
    return cents


def _parse_count(raw: str, label: str) -> int:
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{label} must be a whole number")
    if value < 0:
        raise ValueError(f"{label} cannot be negative")
    return value
