"""Manpower Efficiency module: workforce, payroll and staffing monitoring.

All reads/writes go through AppService with the session token revalidated
on every event. Every calculation comes from the verified KPI engine
(kpi.py) via the live month assembly — no formula is duplicated here.

Payroll confidentiality policy (documented, restrictive by default):
salary figures and the salary-ratio detail are available only to
configuration managers (super_admin + administrator). Other readers see
the payroll section as restricted and never receive salary values in the
serialized state. The unresolved HQ business rule — exactly which
management positions are included in Basic Salary + Allowances — is
preserved from the workbook: the module stores and displays the
aggregate workbook inputs and does not decompose them by position.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Optional

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.auth_pages import app_service
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import ValidationError
from ducklab.ops_daily_state import _parse_count, _parse_money
from ducklab.ops_expenses_state import ExpensesState
from ducklab.ops_state import (
    ACCESS_DENIED, SESSION_EXPIRED, OpsState, auth_message, month_label,
    parse_client_id,
)

# All status colors: ds.status_color (design.py shared map)


# Shared kpi parser: rejects "NaN"/"inf" (is_finite) like Monthly Setup.
_parse_decimal = kpi.parse_decimal

PAYROLL_LABELS = {
    "p_salary": "Basic salary + allowances",
    "p_overtime": "Overtime",
    "p_support": "Support team cost",
}


def _pct_str(value) -> str:
    """Decimal fraction -> editable percent string ('0.06818' -> '6.818')."""
    if value is None:
        return ""
    return f"{float(value) * 100:g}"


def _config_form_values(cfg, can_payroll: bool) -> dict[str, str]:
    """Config editor values. The salary ratio target and payroll figures
    are filled ONLY for configuration managers (consistent with Stores and
    Overview); everyone else gets blanks."""
    values = {
        "c_opening": (str(cfg.opening_headcount_ft)
                      if cfg.opening_headcount_ft is not None else ""),
        "c_budget": (str(cfg.budget_headcount)
                     if cfg.budget_headcount is not None else ""),
        "c_pt_factor": (f"{float(cfg.part_timer_factor):g}"
                        if cfg.part_timer_factor is not None else ""),
        "c_turnover": _pct_str(cfg.turnover_threshold),
        "c_ratio_target": "", "p_salary": "", "p_overtime": "",
        "p_support": "",
    }
    if can_payroll:
        values.update({
            "c_ratio_target": _pct_str(cfg.salary_ratio_target),
            "p_salary": (f"{cfg.salary_basic_allowance_cents / 100:.2f}"
                         if cfg.salary_basic_allowance_cents is not None
                         else ""),
            "p_overtime": (f"{cfg.overtime_cents / 100:.2f}"
                           if cfg.overtime_cents is not None else ""),
            "p_support": (f"{cfg.support_team_cost_cents / 100:.2f}"
                          if cfg.support_team_cost_cents is not None
                          else ""),
        })
    return values


class ManpowerState(ExpensesState):
    """Manpower Efficiency page state (inherits selectors + guards)."""

    can_view_payroll: bool = False       # payroll confidentiality gate
    can_manage_manpower: bool = False    # config editor gate
    mp_finalized: bool = False
    mp_config_exists: bool = True
    mp_error: str = ""
    mp_notice: str = ""

    # KPI groups (preformatted strings); each figure appears once on the page
    hc_kpis: dict[str, str] = {}         # headcount + turnover
    eff_kpis: dict[str, str] = {}        # staffing efficiency (+ sales MTD)
    pay_kpis: dict[str, str] = {}        # payroll (restricted values)
    leave_kpis: dict[str, str] = {}      # leave totals
    pt_factor_label: str = "—"

    # daily staffing rows (unreported days collapsed)
    staff_rows: list[dict[str, str]] = []
    staff_rows_reported: list[dict[str, str]] = []
    staff_hidden_count: int = 0
    staff_hidden_label: str = ""
    staff_show_hidden: bool = False
    staffing_note: str = ""

    # --- monthly configuration editor -----------------------------------
    cfg_open: bool = False
    cfg_error: str = ""
    cfg_saving: bool = False
    c_opening: str = ""
    c_budget: str = ""
    c_pt_factor: str = ""
    c_turnover: str = ""        # percent
    c_ratio_target: str = ""    # percent
    p_salary: str = ""
    p_overtime: str = ""
    p_support: str = ""
    cfg_original: dict[str, str] = {}

    # --- weekly schedule --------------------------------------------------
    can_write_schedule: bool = False
    schedule_error: str = ""
    schedule_notice: str = ""
    schedule_staff: list[dict[str, str]] = []
    schedule_codes: list[dict[str, str]] = []
    schedule_entries: dict[str, str] = {}
    schedule_week_options: list[str] = []
    schedule_week: str = ""
    schedule_days: list[dict[str, str]] = []
    schedule_target: str = ""
    schedule_form_open: bool = False
    schedule_form_kind: str = "staff"
    schedule_form_title: str = ""
    schedule_editing_id: str = ""
    sf_name: str = ""
    sf_staff_code: str = ""
    sf_position: str = ""
    sc_code: str = ""
    sc_label: str = ""
    sc_start: str = ""
    sc_end: str = ""
    sc_color: str = "#3b82f6"
    schedule_form_error: str = ""
    schedule_staff_confirmation: str = ""
    schedule_saving: bool = False
    schedule_subtab: str = "builder"

    def _clear_schedule(self) -> None:
        self.can_write_schedule = False
        self.schedule_error = self.schedule_notice = ""
        self.schedule_staff = []
        self.schedule_codes = []
        self.schedule_entries = {}
        self.schedule_week_options = []
        self.schedule_week = ""
        self.schedule_days = []
        self.schedule_target = ""
        self.schedule_form_open = False
        self.schedule_form_error = ""
        self.schedule_saving = False

    @rx.var
    def schedule_week_choices(self) -> list[dict[str, str]]:
        choices = []
        for value in self.schedule_week_options:
            start = date.fromisoformat(value)
            end = start.fromordinal(start.toordinal() + 6)
            choices.append({
                "value": value,
                "label": (f"{start.day} {calendar.month_abbr[start.month]} - "
                          f"{end.day} {calendar.month_abbr[end.month]} {end.year}"),
            })
        return choices

    @rx.var
    def schedule_week_label(self) -> str:
        return next((choice["label"] for choice in self.schedule_week_choices
                     if choice["value"] == self.schedule_week), "")

    @rx.var
    def schedule_code_values(self) -> list[str]:
        return ["", *[c["code"] for c in self.schedule_codes]]

    @rx.var
    def schedule_headcounts(self) -> list[int]:
        timed = {c["code"] for c in self.schedule_codes if c["start_time"]}
        return [sum(self.schedule_entries.get(f"{s['id']}:{d['iso']}") in timed
                    for s in self.schedule_staff) for d in self.schedule_days]

    @rx.var
    def schedule_month_label(self) -> str:
        month = self._selected_month()
        return month_label(month) if month else ""

    @rx.var
    def schedule_generated_days(self) -> list[dict[str, str]]:
        return [{**d,
                 "short_date": f"{int(d['iso'][8:10])}/{calendar.month_abbr[int(d['iso'][5:7])]}" if d["iso"] else "",
                 "weekday": d["label"].split()[0] if d["iso"] else ""}
                for d in self.schedule_days]

    @rx.var
    def schedule_legend(self) -> list[dict[str, str]]:
        order = ("M900", "M945", "F900", "F945", "F101", "A121",
                 "ODM", "ODA", "OOD", "ORD", "AL")
        codes = {c["code"]: c for c in self.schedule_codes}
        legend = []
        for code in order:
            if code not in codes:
                continue
            c = codes[code]
            detail = c["label"]
            if c["start_time"] and c["end_time"]:
                start = datetime.strptime(c["start_time"], "%H:%M").strftime("%I:%M %p").lstrip("0")
                end = datetime.strptime(c["end_time"], "%H:%M").strftime("%I:%M %p").lstrip("0")
                detail = f"{start} - {end}"
            legend.append({**c, "detail": detail})
        return legend

    @rx.var
    def schedule_target_display(self) -> str:
        return self.schedule_target if self.schedule_target.strip() else "—"

    @rx.var
    def schedule_generated_rows(self) -> list[dict[str, list[dict[str, str]]]]:
        return [{**row, "number": str(i)} for i, row in enumerate(self.schedule_grid_rows, 1)]

    @rx.var
    def schedule_day_summaries(self) -> list[dict[str, str]]:
        counts = self.schedule_headcounts
        target = int(self.schedule_target) if self.schedule_target.isdigit() else None
        return [{**d, "headcount": str(counts[i]),
                 "below_target": "1" if target is not None and counts[i] < target else ""}
                for i, d in enumerate(self.schedule_days)]

    @rx.var
    def schedule_grid_rows(self) -> list[dict[str, list[dict[str, str]]]]:
        return [{**s, "detail": f"{s['staff_code']} · {s['position']}", "cells": [{
            "date": d["iso"], "code": self.schedule_entries.get(f"{s['id']}:{d['iso']}", ""),
            "color": next((c["color"] for c in self.schedule_codes
                           if c["code"] == self.schedule_entries.get(f"{s['id']}:{d['iso']}", "")),
                          "#ffffff")} for d in self.schedule_days]}
                for s in self.schedule_staff]

    @rx.event
    def generate_schedule(self):
        self.schedule_subtab = "generated"

    @rx.event
    def export_schedule_csv(self):
        self.schedule_error = self.schedule_notice = ""
        if not self.can_write_schedule:
            self.schedule_error = "You have read-only access."
            return
        store_id = self._selected_store_id()
        if store_id is None or not self.schedule_week:
            self.schedule_error = "Select a store and week first."
            return
        try:
            # Revalidate the actor and store access; never trust client rows alone.
            user = self._actor()
            week_start = date.fromisoformat(self.schedule_week)
            data = app_service.get_schedule(
                user, store_id, week_start, week_start.fromordinal(week_start.toordinal() + 6))
            store = next(store for store in user.stores if store.id == store_id)
            store_part = "".join(c if c.isalnum() or c in "-_" else "_" for c in store.code)
            folder = Path.home() / "Documents/Opscore Schedules" / store_part / self.schedule_week
            folder.mkdir(parents=True, exist_ok=True)
            entries = {(row["staff_id"], row["work_date"]): row["code"]
                       for row in data["entries"] if row["code"]}
            count = 0
            for staff in data["staff"]:
                rows = [(day.isoformat(), entries[(staff["id"], day.isoformat())])
                        for i in range(7)
                        if (day := week_start.fromordinal(week_start.toordinal() + i))
                        and (staff["id"], day.isoformat()) in entries]
                if not rows:
                    continue
                staff_code = "".join(c if c.isalnum() or c in "-_" else "_"
                                     for c in staff["staff_code"])
                with (folder / f"{staff_code}_{self.schedule_week}.csv").open(
                        "w", newline="", encoding="utf-8") as handle:
                    writer = csv.writer(handle)
                    writer.writerow(("date", "shift_code"))
                    writer.writerows(rows)
                count += 1
            self.schedule_notice = f"{count} Files Saved: {folder.resolve()}"
        except AuthenticationError:
            self.schedule_error = SESSION_EXPIRED
        except (AuthorizationError, StoreNotFoundError):
            self.schedule_error = ACCESS_DENIED
        except (OSError, RepositoryError):
            self.schedule_error = "Could not export schedule CSV files."

    @rx.event
    def set_schedule_subtab(self, value: str):
        self.schedule_subtab = value

    @rx.event
    def print_schedule(self):
        return rx.call_script("window.print()")

    @rx.event
    def select_schedule_week(self, value: str):
        self.schedule_week = value
        yield ManpowerState.load_schedule_data()

    @rx.event
    def load_schedule_data(self) -> None:
        self.schedule_error = self.schedule_notice = ""
        self.schedule_staff = []
        self.schedule_codes = []
        self.schedule_entries = {}
        self.schedule_days = []
        self.schedule_target = ""
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            return
        try:
            user = self._actor()
            self.can_write_schedule = (authz.can_write_operations(user)
                                       and self.selected_store_active)
            weeks = kpi.month_weeks(month)
            self.schedule_week_options = [week[0].isoformat() for week in weeks if week[0]]
            if self.schedule_week not in self.schedule_week_options:
                self.schedule_week = self.schedule_week_options[0]
            week = next(w for w in weeks if w[0] and w[0].isoformat() == self.schedule_week)
            self.schedule_days = [{"iso": d.isoformat() if d else "",
                                   "label": f"{calendar.day_name[i][:3]} {d.day}" if d else calendar.day_name[i][:3]}
                                  for i, d in enumerate(week)]
            data = app_service.get_schedule(user, store_id, week[0], week[-1])
            self.schedule_staff = [{k: str(v) for k, v in row.items()} for row in data["staff"]]
            self.schedule_codes = [{k: str(v or "") for k, v in row.items()} for row in data["codes"]]
            self.schedule_entries = {f"{r['staff_id']}:{r['work_date']}": r["code"]
                                     for r in data["entries"]}
            target = app_service.get_schedule_target(user, store_id, week[0])
            self.schedule_target = "" if target is None else str(target)
        except AuthenticationError:
            self.schedule_error = SESSION_EXPIRED
        except (AuthorizationError, StoreNotFoundError):
            self.schedule_error = ACCESS_DENIED
        except RepositoryError as exc:
            self.schedule_error = str(exc)

    @rx.event
    def set_schedule_cell(self, staff_id: str, work_date: str, code: str):
        if not self.can_write_schedule:
            self.schedule_error = "You have read-only access."
            return
        try:
            store_id = self._selected_store_id()
            app_service.set_schedule_entry(self._actor(), store_id,
                                           parse_client_id(staff_id, "staff"),
                                           date.fromisoformat(work_date), code)
            key = f"{staff_id}:{work_date}"
            self.schedule_entries = {**self.schedule_entries, key: code}
            self.schedule_error = ""
        except (AuthenticationError, AuthorizationError, ValidationError,
                ValueError, RepositoryError) as exc:
            self.schedule_error = str(exc) if isinstance(exc, (ValidationError, RepositoryError)) else "Invalid schedule change or insufficient permission."

    @rx.event
    def set_schedule_target(self, value: str):
        self.schedule_target = value
        if not self.can_write_schedule:
            return
        try:
            target = int(value) if value.strip() else None
            if target is not None and target < 0:
                raise ValueError
            app_service.set_schedule_target(
                self._actor(), self._selected_store_id(),
                date.fromisoformat(self.schedule_week), target)
            self.schedule_error = ""
        except (ValueError, ValidationError):
            self.schedule_error = "Target Manpower must be a non-negative whole number."
        except (AuthenticationError, AuthorizationError, RepositoryError):
            self.schedule_error = "Could not save Target Manpower."

    @rx.event
    def open_schedule_staff(self, staff_id: str = ""):
        self.schedule_form_kind = "staff"
        self.schedule_form_title = "Edit Staff" if staff_id else "Add Staff"
        self.schedule_editing_id = staff_id
        row = next((s for s in self.schedule_staff if s["id"] == staff_id), {})
        self.sf_name = row.get("name", "")
        self.sf_staff_code = row.get("staff_code", "")
        position = row.get("position", "")
        self.sf_position = position if position in ("ABM", "ASV", "SV", "RA") else ""
        self.schedule_form_error = ""
        self.schedule_staff_confirmation = ""
        self.schedule_form_open = True

    @rx.event
    def open_schedule_code(self, code: str = ""):
        self.schedule_form_kind = "code"
        self.schedule_form_title = "Edit Code" if code else "Add Code"
        self.schedule_editing_id = code
        row = next((c for c in self.schedule_codes if c["code"] == code), {})
        self.sc_code = row.get("code", "")
        self.sc_label = row.get("label", "")
        self.sc_start = row.get("start_time", "")
        self.sc_end = row.get("end_time", "")
        self.sc_color = row.get("color", "#3b82f6")
        self.schedule_form_error = ""
        self.schedule_form_open = True

    @rx.event
    def set_schedule_form_open(self, value):
        self.schedule_form_open = bool(value)

    @rx.event
    def set_sf_name(self, value: str): self.sf_name = value
    @rx.event
    def set_sf_staff_code(self, value: str): self.sf_staff_code = value
    @rx.event
    def set_sf_position(self, value: str): self.sf_position = value
    @rx.event
    def set_sc_code(self, value: str): self.sc_code = value
    @rx.event
    def set_sc_label(self, value: str): self.sc_label = value
    @rx.event
    def set_sc_start(self, value: str): self.sc_start = value
    @rx.event
    def set_sc_end(self, value: str): self.sc_end = value
    @rx.event
    def set_sc_color(self, value: str): self.sc_color = value

    @rx.event
    def save_schedule_form_add_another(self):
        yield ManpowerState.save_schedule_form(True)

    @rx.event
    def save_schedule_form(self, add_another: bool = False):
        staff_name = self.sf_name.strip()
        if not self.can_write_schedule:
            self.schedule_form_error = "You have read-only access."
            return
        self.schedule_saving = True
        self.schedule_form_error = ""
        yield
        try:
            store_id = self._selected_store_id()
            user = self._actor()
            if self.schedule_form_kind == "staff":
                app_service.save_staff(user, store_id,
                    parse_client_id(self.schedule_editing_id, "staff") if self.schedule_editing_id else None,
                    name=self.sf_name, staff_code=self.sf_staff_code, position=self.sf_position)
            else:
                app_service.save_shift_code(user, store_id,
                    self.schedule_editing_id or None, code=self.sc_code,
                    label=self.sc_label, start_time=self.sc_start or None,
                    end_time=self.sc_end or None, color=self.sc_color)
        except (ValidationError, RepositoryError, ValueError) as exc:
            self.schedule_form_error = str(exc)
            return
        except (AuthenticationError, AuthorizationError):
            self.schedule_form_error = "Session expired or insufficient permission."
            return
        finally:
            self.schedule_saving = False
        self.schedule_notice = "Schedule Settings Saved"
        if add_another and self.schedule_form_kind == "staff":
            self.schedule_staff_confirmation = f"{staff_name} Added"
            self.sf_name = ""
            self.sf_staff_code = ""
            yield ManpowerState.load_schedule_data()
            yield rx.call_script("document.getElementById('schedule-staff-name')?.focus()")
        else:
            self.schedule_form_open = False
            yield ManpowerState.load_schedule_data()

    @rx.event
    def delete_schedule_staff(self, staff_id: str):
        try:
            app_service.delete_staff(self._actor(), self._selected_store_id(),
                                     parse_client_id(staff_id, "staff"))
        except (AuthenticationError, AuthorizationError, RepositoryError, ValueError) as exc:
            self.schedule_error = str(exc)
            return
        yield ManpowerState.load_schedule_data()

    @rx.event
    def delete_schedule_code(self, code: str):
        try:
            app_service.delete_shift_code(self._actor(), self._selected_store_id(), code)
        except (AuthenticationError, AuthorizationError, RepositoryError) as exc:
            self.schedule_error = str(exc)
            return
        yield ManpowerState.load_schedule_data()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _clear_manpower(self) -> None:
        """Drop every manpower value (incl. payroll) before reloading.

        Called on store/month switch so a store's sensitive figures are
        never shown alongside, or leaked into, another store's context.
        """
        self.hc_kpis = {}
        self.eff_kpis = {}
        self.pay_kpis = {}
        self.leave_kpis = {}
        self.staff_rows = []
        self.staff_rows_reported = []
        self.staff_hidden_count = 0
        self.staff_hidden_label = ""
        self.staffing_note = ""
        self.pt_factor_label = "—"
        self.mp_error = ""
        self.mp_notice = ""
        self.mp_finalized = False
        self.mp_config_exists = True
        self.can_view_payroll = False
        self.can_manage_manpower = False
        self.cfg_open = False
        self.cfg_error = ""
        self.cfg_saving = False
        self._clear_schedule()

    @rx.event
    def toggle_staff_hidden_days(self):
        self.staff_show_hidden = not self.staff_show_hidden

    # ------------------------------------------------------------------
    # selector overrides: keep manpower data in sync with store/month
    # ------------------------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._clear_manpower()
        self._clear_report()
        yield ManpowerState.refresh_report()
        yield ManpowerState.load_manpower_data()
        yield ManpowerState.load_schedule_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_manpower()
        self._clear_report()
        yield ManpowerState.refresh_report()
        yield ManpowerState.load_manpower_data()
        yield ManpowerState.load_schedule_data()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @rx.event
    def load_manpower(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield OpsState.load_ops()
        yield ManpowerState.load_manpower_data()
        yield ManpowerState.load_schedule_data()

    @rx.event
    def load_manpower_data(self) -> None:
        self._clear_manpower()
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None or not self.has_stores:
            return
        try:
            user = self._actor()
        except AuthenticationError:
            self.error_message = self.mp_error = SESSION_EXPIRED
            self.report_status = "error"
            return
        # Month configuration is required for manpower monitoring.
        try:
            finalized = bool(
                app_service.list_report_versions(user, store_id, month))
            cfg = app_service.get_month_config(user, store_id, month)
        except AuthenticationError:
            self.mp_config_exists = False
            self.mp_error = SESSION_EXPIRED
            return
        except ConfigNotFoundError:
            self.mp_config_exists = False
            self.mp_error = (
                f"No monthly configuration exists for {month_label(month)}. "
                "An administrator must configure this store/month (targets, "
                "headcount and payroll settings) before manpower monitoring "
                "is available. No staffing targets are assumed.")
            return
        except (AuthorizationError, StoreNotFoundError):
            self.mp_config_exists = False
            self.mp_error = ACCESS_DENIED
            return
        self.can_view_payroll = authz.can_manage_config(user)
        self.can_manage_manpower = authz.can_manage_config(user)
        self.mp_finalized = finalized
        self.pt_factor_label = (
            f"{float(cfg.part_timer_factor):g}"
            if cfg.part_timer_factor is not None else "not configured")

        # Live assembled month: every figure from the verified KPI engine.
        try:
            rep = app_service.preview_month(user, store_id, month)
            days = app_service.get_daily_records(user, store_id, month)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.mp_error = auth_message(exc)
            return
        except RepositoryError as exc:
            self.mp_error = str(exc)
            return
        m = rep.manpower
        s = rep.sales

        self.hc_kpis = {
            "opening": ds.fmt_num(m.opening_headcount),
            "hires": ds.fmt_num(m.hires),
            "resigned": ds.fmt_num(m.resigned),
            "closing": ds.fmt_num(m.closing_headcount),
            "budget": ds.fmt_num(cfg.budget_headcount),
            # "+2" / "-1"; zero reads "0" (never "+0")
            "variance": ("—" if m.headcount_variance is None
                         else f"{m.headcount_variance:+,}"
                         if m.headcount_variance else "0"),
            "status": m.headcount_status,
            "status_color": ds.status_color(m.headcount_status),
            # turnover lives with headcount movement (shown once)
            "turnover": ds.fmt_pct(m.turnover_pct),
            "turnover_status": m.turnover_status,
            "turnover_color": ds.status_color(m.turnover_status),
            "turnover_sub": ("Resignations ÷ opening · threshold "
                             + ds.fmt_pct(cfg.turnover_threshold)),
        }
        factor_missing = m.pt_factor_missing
        self.eff_kpis = {
            "avg_effective": (kpi.PT_FACTOR_NOT_SET if factor_missing
                              else ds.fmt_num(
                                  float(m.average_effective_staff)
                                  if m.average_effective_staff is not None
                                  else None)),
            "sales_per_staff": (kpi.PT_FACTOR_NOT_SET if factor_missing
                                else ds.fmt_rm(
                                    int(m.sales_per_staff)
                                    if m.sales_per_staff is not None
                                    else None)),
            "turnover": ds.fmt_pct(m.turnover_pct),
            "turnover_status": m.turnover_status,
            "turnover_color": ds.status_color(m.turnover_status),
            "threshold": ds.fmt_pct(cfg.turnover_threshold),
            "sales_mtd": ds.fmt_rm(s.sales_mtd_cents),
            "completeness": (f"{s.days_entered} of {s.days_in_month} "
                             "days reported"),
        }
        month_complete = s.days_entered >= s.days_in_month
        # Payroll block: restricted values are never populated for
        # non-authorized roles (not merely hidden client-side).
        if self.can_view_payroll:
            budget = m.salary_budget_cents
            total = m.total_salary_cents
            variance = kpi.salary_budget_variance_cents(budget, total)
            used = kpi.ratio_of_target(m.salary_ratio_incl_support,
                                       cfg.salary_ratio_target)
            ratio_used_pct = (min(float(used) * 100, 100.0)
                              if used is not None else None)
            self.pay_kpis = {
                "basic": ds.fmt_rm(cfg.salary_basic_allowance_cents),
                "overtime": ds.fmt_rm(cfg.overtime_cents),
                "total": ds.fmt_rm(total),
                "support": ds.fmt_rm(cfg.support_team_cost_cents),
                "ratio": ds.fmt_pct(m.salary_ratio_store),
                "ratio_support": ds.fmt_pct(m.salary_ratio_incl_support),
                "target": ds.fmt_pct(cfg.salary_ratio_target),
                "budget": ds.fmt_rm(budget),       # one money format
                "variance": ds.fmt_rm(variance),
                # incomplete month: the ratio uses sales to date, so the
                # status is labelled "Month to date" (engine value as is)
                "status": (f"Month to date: {m.salary_ratio_status}"
                           if not month_complete and m.salary_ratio_status
                           in (kpi.SALARY_WITHIN, kpi.SALARY_FAILURE)
                           else m.salary_ratio_status),
                "mtd": "" if month_complete else "1",
                "status_color": ds.status_color(m.salary_ratio_status),
                # bar in the status colour (red on failure), as the Overview
                "bar_color": ("red" if m.salary_ratio_status
                              == kpi.SALARY_FAILURE else "green"
                              if m.salary_ratio_status == kpi.SALARY_WITHIN
                              else "gray"),
                "ratio_bar": (f"{ratio_used_pct:.0f}"
                              if ratio_used_pct is not None else "0"),
                "sales": ds.fmt_rm(s.sales_mtd_cents),
            }
        else:
            self.pay_kpis = {"status": "Restricted", "status_color": "gray"}

        # --- daily staffing rows + leave totals --------------------------
        by_date = {d.date: d for d in days}
        y, mo = int(month[:4]), int(month[5:7])
        n_days = calendar.monthrange(y, mo)[1]
        rows: list[dict[str, str]] = []
        for day_num in range(1, n_days + 1):
            d = date(y, mo, day_num)
            rows.append(self._staff_row(d, by_date.get(d),
                                        cfg.part_timer_factor))
        self.staff_rows = rows
        self.staff_rows_reported = [r for r in rows
                                    if r["status"] != ds.DAY_NOT_REPORTED]
        reported = len(self.staff_rows_reported)
        self.staff_hidden_count = n_days - reported
        self.staff_hidden_label = ds.days_not_reported_label(
            self.staff_hidden_count)
        self.staffing_note = (
            f"{reported} of {n_days} days have staffing data. Days without "
            "attendance are not reported — never zero staff.")
        self.leave_kpis = {
            "annual": ds.fmt_num(sum(d.leave_annual or 0 for d in days)),
            "mc": ds.fmt_num(sum(d.leave_mc or 0 for d in days)),
            "other_paid": ds.fmt_num(sum(d.leave_other_paid or 0
                                         for d in days)),
            "unpaid": ds.fmt_num(sum(d.leave_unpaid or 0 for d in days)),
            "total": ds.fmt_num(m.leave_days),
        }

    def _staff_row(self, d: date, rec, factor) -> dict[str, str]:
        base = {
            "iso": d.isoformat(),
            "date": f"{d.day:02d}",
            "weekday": calendar.day_name[d.weekday()][:3],
        }
        if rec is None:
            return {**base, "ft": "—", "pt": "—", "effective": "—",
                    "hires": "—", "resigned": "—", "leave": "—",
                    "sales": "—", "sales_per_staff": "—",
                    "status": ds.DAY_NOT_REPORTED,
                    "status_color": ds.status_color(ds.DAY_NOT_REPORTED)}
        eff = kpi.effective_staff(rec.ft_working, rec.pt_working, factor)
        factor_missing = factor is None and bool(rec.pt_working)
        leave = sum(v or 0 for v in (
            rec.leave_annual, rec.leave_mc, rec.leave_other_paid,
            rec.leave_unpaid))
        leave_reported = any(v is not None for v in (
            rec.leave_annual, rec.leave_mc, rec.leave_other_paid,
            rec.leave_unpaid))
        sps = kpi.daily_sales_per_staff_cents(rec, factor)
        filled = sum(v is not None for v in (
            rec.ft_working, rec.pt_working, rec.new_hires_ft,
            rec.resigned_ft, rec.leave_annual, rec.leave_mc,
            rec.leave_other_paid, rec.leave_unpaid))
        # same chips as every page; completeness counts staffing fields
        status = (ds.DAY_COMPLETE if filled >= 3
                  else ds.DAY_PARTIAL if filled else ds.DAY_NOT_REPORTED)
        return {
            **base,
            "ft": ds.fmt_num(rec.ft_working),
            "pt": ds.fmt_num(rec.pt_working),
            "effective": (kpi.PT_FACTOR_NOT_SET if factor_missing
                          else f"{float(eff):,.2f}" if eff is not None
                          else "—"),
            "hires": ds.fmt_num(rec.new_hires_ft),
            "resigned": ds.fmt_num(rec.resigned_ft),
            "leave": (ds.fmt_num(leave) if leave_reported else "—"),
            "sales": ds.fmt_rm(rec.sales_cents),
            "sales_per_staff": (kpi.PT_FACTOR_NOT_SET if factor_missing
                                else f"RM {sps / 100:,.2f}" if sps is not None
                                else "—"),
            "status": status,
            "status_color": ds.status_color(status),
        }

    # ------------------------------------------------------------------
    # navigation: edit daily staffing via the Daily Operations workflow
    # ------------------------------------------------------------------
    @rx.event
    def edit_day(self, iso_date: str):
        """Open the existing Daily Operations entry for this store/date."""
        return rx.redirect(f"/ops/daily?date={iso_date}")

    # ------------------------------------------------------------------
    # configuration editor
    # ------------------------------------------------------------------
    @rx.event
    def open_config(self) -> None:
        self.cfg_error = ""
        store_id = self._selected_store_id()
        month = self._selected_month()
        values = {a: "" for a in (
            "c_opening", "c_budget", "c_pt_factor", "c_turnover",
            "c_ratio_target", "p_salary", "p_overtime", "p_support")}
        if store_id is not None and month is not None:
            try:
                user = self._actor()
                # Event handlers can be invoked directly over the socket:
                # payroll fields and the salary ratio target load ONLY for
                # configuration managers.
                can_payroll = authz.can_manage_config(user)
                cfg = app_service.get_month_config(user, store_id, month)
                values.update(_config_form_values(cfg, can_payroll))
            except ConfigNotFoundError:
                pass  # save_config explains that setup must come first
            except (AuthenticationError, AuthorizationError,
                    StoreNotFoundError) as exc:
                # never open an empty dialog for an auth failure
                self.cfg_open = False
                self.cfg_error = auth_message(exc)
                return
        for a, v in values.items():
            setattr(self, a, v)
        self.cfg_original = dict(values)
        self.cfg_open = True

    @rx.event
    def close_config(self) -> None:
        self.cfg_open = False
        self.cfg_error = ""

    @rx.event
    def set_c_opening(self, v: str): self.c_opening = v
    @rx.event
    def set_c_budget(self, v: str): self.c_budget = v
    @rx.event
    def set_c_pt_factor(self, v: str): self.c_pt_factor = v
    @rx.event
    def set_c_turnover(self, v: str): self.c_turnover = v
    @rx.event
    def set_c_ratio_target(self, v: str): self.c_ratio_target = v
    @rx.event
    def set_p_salary(self, v: str): self.p_salary = v
    @rx.event
    def set_p_overtime(self, v: str): self.p_overtime = v
    @rx.event
    def set_p_support(self, v: str): self.p_support = v

    @rx.event
    def save_config(self) -> None:
        """Save manpower configuration + payroll through AppService.

        Partial-update semantics: only changed staffing fields are sent;
        payroll fields are sent verbatim (blank = explicit NULL clear).
        Unrelated monthly configuration fields are never touched.
        """
        self.cfg_error = ""
        self.mp_notice = ""
        store_id = self._selected_store_id()
        month = self._selected_month()
        if store_id is None or month is None:
            self.cfg_error = "Select a store and month first."
            return
        # parse + validate
        try:
            fields = self._parse_config_form()
        except ValueError as exc:
            self.cfg_error = str(exc)
            return
        try:
            user = self._actor()
        except AuthenticationError:
            self.cfg_error = SESSION_EXPIRED
            return
        # Server-side: payroll/config writes require can_manage_config,
        # even when this event is invoked directly over the socket.
        if not authz.can_manage_config(user):
            self.cfg_error = (
                "Only super admins and administrators can manage monthly "
                "configuration and payroll.")
            return
        # Never create a configuration implicitly to enable manpower edits.
        try:
            app_service.get_month_config(user, store_id, month)
        except ConfigNotFoundError:
            self.cfg_error = (
                "No monthly configuration exists for this store/month. "
                "Month setup (sales targets etc.) must be completed first; "
                "manpower editing never creates hidden defaults.")
            return
        except (AuthorizationError, StoreNotFoundError):
            self.cfg_error = ACCESS_DENIED
            return
        except AuthenticationError:
            self.cfg_error = SESSION_EXPIRED
            return
        if not fields:
            self.cfg_open = False
            self.mp_notice = "No changes to save."
            return
        self.cfg_saving = True
        yield
        try:
            result = app_service.configure_month(
                user, store_id, month, **fields)
        except AuthenticationError:
            self.cfg_error = SESSION_EXPIRED
            return
        except AuthorizationError:
            self.cfg_error = (
                "Only super admins and administrators can manage monthly "
                "configuration and payroll.")
            return
        except ValidationError as exc:
            self.cfg_error = str(exc)
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.cfg_error = f"Save failed: {exc}"
            return
        finally:
            self.cfg_saving = False
        self.cfg_open = False
        note = ("Configuration saved and persisted. KPIs, salary ratios and "
                "live Monthly Reports recalculated.")
        if result.get("finalized_snapshot_exists"):
            note += (" A finalized snapshot exists for this month: live "
                     "figures now differ from the preserved snapshot, which "
                     "remains untouched.")
        self.mp_notice = note
        yield ManpowerState.load_manpower_data()
        yield ManpowerState.refresh_report()

    def _parse_config_form(self) -> dict:
        """Changed staffing fields + verbatim payroll fields.

        Blank payroll input = explicit clear to NULL (awaiting payroll).
        Blank staffing fields = leave unchanged (partial update).
        """
        fields: dict = {}
        orig = self.cfg_original
        if self.c_opening != orig.get("c_opening", ""):
            if self.c_opening.strip():
                fields["opening_headcount_ft"] = _parse_count(
                    self.c_opening, "Opening headcount")
            else:
                fields["opening_headcount_ft"] = None
        if self.c_budget != orig.get("c_budget", ""):
            if self.c_budget.strip():
                fields["budget_headcount"] = _parse_count(
                    self.c_budget, "Budget headcount")
            else:
                fields["budget_headcount"] = None
        if self.c_pt_factor != orig.get("c_pt_factor", ""):
            if self.c_pt_factor.strip():
                fields["part_timer_factor"] = float(_parse_decimal(
                    self.c_pt_factor, "Part-time conversion factor",
                    Decimal(0), Decimal(2)))
            else:
                fields["part_timer_factor"] = None
        if self.c_turnover != orig.get("c_turnover", ""):
            if self.c_turnover.strip():
                pct = _parse_decimal(self.c_turnover,
                                     "Turnover threshold (%)",
                                     Decimal(0), Decimal(100))
                fields["turnover_threshold"] = float(pct / 100)
            else:
                fields["turnover_threshold"] = None
        if self.c_ratio_target != orig.get("c_ratio_target", ""):
            if self.c_ratio_target.strip():
                pct = _parse_decimal(self.c_ratio_target,
                                     "Salary ratio target (%)",
                                     Decimal(0), Decimal(100))
                fields["salary_ratio_target"] = float(pct / 100)
            else:
                fields["salary_ratio_target"] = None
        # Payroll: verbatim (blank -> None = awaiting payroll). Only sent
        # when the values differ from what was loaded.
        payroll_map = (
            ("p_salary", "salary_basic_allowance_cents"),
            ("p_overtime", "overtime_cents"),
            ("p_support", "support_team_cost_cents"),
        )
        for attr, col in payroll_map:
            raw = getattr(self, attr)
            if raw == orig.get(attr, ""):
                continue
            cents = _parse_money(raw) if raw.strip() else None
            if cents is not None and cents < 0:
                raise ValueError(f"{PAYROLL_LABELS[attr]} cannot be negative")
            fields[col] = cents
        return fields
