"""Weekly staff scheduling workflow (roster builder, codes, generated view).

This module holds the scheduling slice of the Manpower workflow. It plugs
into the page-state chain between ExpensesState and ManpowerState, so the
public interface is unchanged — pages and tests keep referencing every
state var and event handler on ManpowerState.

All reads/writes go through AppService with the session token revalidated
on every event; store access is enforced by authz. CSV export writes
per-staff files below Documents/Opscore Schedules/<store-code>/<week-start>/.
"""

from __future__ import annotations

import calendar
import csv
from datetime import date, datetime, timedelta

import reflex as rx

from ducklab import authz
from ducklab import kpi
from ducklab import platform_utils
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.auth_pages import app_service
from ducklab.input_validation import parse_client_id
from ducklab.operations_repository import (
    RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import ValidationError
from ducklab.ops_shared_state import (
    ACCESS_DENIED, SESSION_EXPIRED, SharedOpsState, month_label,
)

# All status colors: ds.status_color (design.py shared map)


class ScheduleState(SharedOpsState):
    """Weekly schedule state (flat base: shared selectors + guards)."""

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
    sf_employment_type: str = "Not Set"
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

    @rx.event
    def reset_schedule(self) -> None:
        """Clear the whole schedule panel (store/month switch)."""
        self._clear_schedule()

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
            week_end = week_start + timedelta(days=6)
            data = app_service.get_schedule(user, store_id, week_start, week_end)
            store = app_service.get_store(user, store_id)
            store_part = "".join(c if c.isalnum() or c in "-_" else "_" for c in store.code)
            folder = (platform_utils.documents_dir() / "Opscore Schedules"
                      / store_part / self.schedule_week)
            folder.mkdir(parents=True, exist_ok=True)
            entries = {(row["staff_id"], row["work_date"]): row["code"]
                       for row in data["entries"] if row["code"]}
            count = 0
            for staff in data["staff"]:
                rows = [(day.isoformat(), entries[(staff["id"], day.isoformat())])
                        for i in range(7)
                        if (day := week_start + timedelta(days=i))
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
            self.schedule_notice = f"{count} files saved: {folder.resolve()}"
        except AuthenticationError:
            self.schedule_error = SESSION_EXPIRED
        except (AuthorizationError, StoreNotFoundError):
            self.schedule_error = ACCESS_DENIED
        except ValueError:
            self.schedule_error = (
                "Invalid schedule week. Select a valid week and try again.")
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
        yield ScheduleState.load_schedule_data()

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
        self.sf_employment_type = row.get("employment_type", "Not Set")
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
    def set_sf_employment_type(self, value: str): self.sf_employment_type = value
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
        yield ScheduleState.save_schedule_form(True)

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
                    name=self.sf_name, staff_code=self.sf_staff_code, position=self.sf_position,
                    employment_type=self.sf_employment_type)
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
        self.schedule_notice = "Schedule settings saved."
        if add_another and self.schedule_form_kind == "staff":
            self.schedule_staff_confirmation = f"{staff_name} added."
            self.sf_name = ""
            self.sf_staff_code = ""
            self.sf_employment_type = "Not Set"
            yield ScheduleState.load_schedule_data()
            yield rx.call_script("document.getElementById('schedule-staff-name')?.focus()")
        else:
            self.schedule_form_open = False
            yield ScheduleState.load_schedule_data()

    @rx.event
    def delete_schedule_staff(self, staff_id: str):
        try:
            app_service.delete_staff(self._actor(), self._selected_store_id(),
                                     parse_client_id(staff_id, "staff"))
        except (AuthenticationError, AuthorizationError, RepositoryError, ValueError) as exc:
            self.schedule_error = str(exc)
            return
        yield ScheduleState.load_schedule_data()

    @rx.event
    def delete_schedule_code(self, code: str):
        try:
            app_service.delete_shift_code(self._actor(), self._selected_store_id(), code)
        except (AuthenticationError, AuthorizationError, RepositoryError) as exc:
            self.schedule_error = str(exc)
            return
        yield ScheduleState.load_schedule_data()
