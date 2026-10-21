"""Action Tracker module (/ops/actions): cards, filtered table, dialog form.

All reads/writes go through AppService (session re-validated per event).
Counts and overdue come from kpi.action_counts/open_actions — never
recomputed here. "Today" is Asia/Kuala_Lumpur and injectable for tests.
Actions are per store and never part of finalized-month snapshots.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Optional
from zoneinfo import ZoneInfo

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab.auth_service import AuthenticationError, AuthorizationError
from ducklab.auth_pages import app_service
from ducklab.operations_write_repository import (
    ACTION_STATUSES, PILLARS, PRIORITIES, ValidationError,
    RecordNotFoundError, WriteError,
)
from ducklab.ops_stores_state import StoresState
from ducklab.ops_state import OpsState

OPEN_STATUSES = ("Open", "In progress", "Blocked")

# Priority/status colors: ds.status_color (design.py shared map)

FILTER_STATUSES = ["All statuses", *ACTION_STATUSES]
FILTER_PILLARS = ["All pillars", *PILLARS]
FILTER_PRIORITIES = ["All priorities", *PRIORITIES]


def _today() -> date:
    return kpi.kl_today()


class ActionsState(StoresState):
    """Action Tracker state (inherits selectors + guards)."""

    can_write_actions: bool = False

    # cards
    n_open: str = "0"
    n_overdue: str = "0"
    n_done: str = "0"

    rows: list[dict[str, str]] = []
    loaded: bool = False

    # filters
    f_status: str = "All statuses"
    f_pillar: str = "All pillars"
    f_priority: str = "All priorities"
    f_overdue_only: bool = False

    # dialog form
    form_open: bool = False
    editing_id: str = ""          # "" = new action
    form_title: str = "Add action"
    a_raised: str = ""
    a_pillar: str = "General Operations"
    a_issue: str = ""
    a_priority: str = "Medium"
    a_status: str = "Open"
    a_pic: str = ""
    a_due: str = ""
    a_corrective: str = ""
    a_completion: str = ""
    a_remarks: str = ""
    form_error: str = ""
    saving: bool = False
    notice: str = ""

    def _clear_actions(self) -> None:
        self.n_open = self.n_overdue = self.n_done = "0"
        self.rows = []
        self.loaded = False
        self.form_open = False
        self.editing_id = ""
        self.form_error = ""
        self.notice = ""
        self.f_status = "All statuses"
        self.f_pillar = "All pillars"
        self.f_priority = "All priorities"
        self.f_overdue_only = False

    # --- selector overrides -------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._clear_actions()
        self._clear_report()
        yield ActionsState.load_actions_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_actions()
        self._clear_report()
        yield ActionsState.load_actions_data()

    # --- loading ------------------------------------------------------------
    @rx.event
    def load_actions(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield OpsState.load_ops()
        yield ActionsState.load_actions_data()

    @rx.event
    def load_actions_data(self) -> None:
        self._clear_actions()
        self.loaded = False
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
        self.can_write_actions = (authz.can_write_operations(user)
                                  and self.selected_store_active)
        try:
            all_actions = app_service.get_actions(user, store_id)
        except AuthorizationError:
            self.error_message = "No access to this store."
            return
        today = _today()
        visible = kpi.actions_in_month_scope(all_actions, month)
        counts = kpi.action_counts([a.input for a in visible], as_of=today)
        self.n_open = str(kpi.open_actions(counts))
        self.n_overdue = str(counts["overdue"])
        self.n_done = str(counts["done"])

        rows = []
        for a in visible:
            st = a.input.status or "Open"
            due = a.input.due_date
            overdue = kpi.is_overdue(a.input, today)
            carried = _raised_month(a) != month
            rows.append({
                "id": str(a.id),
                "raised": a.raised_date.strftime("%d %b %Y")
                    if a.raised_date else "—",
                "raised_iso": a.raised_date.isoformat()
                    if a.raised_date else "",
                "pillar": a.input.pillar or "—",
                "issue": a.issue or "—",
                "priority": a.priority or "—",
                "priority_color": ds.status_color(a.priority or ""),
                "status": st,
                "status_color": ds.status_color(st),
                "pic": a.pic or "—",
                "due": due.strftime("%d %b %Y") if due else "—",
                "due_iso": due.isoformat() if due else "9999-12-31",
                "overdue": "1" if overdue else "",
                "carried": "1" if carried else "",
                "completion": a.completion_date.strftime("%d %b %Y")
                    if a.completion_date else "—",
                "corrective": a.corrective_action or "",
                "remarks": a.remarks or "",
            })
        # default sort: overdue first, then due date ascending
        rows.sort(key=lambda r: (not r["overdue"], r["due_iso"],
                                 r["raised_iso"]))
        self.rows = rows
        self.loaded = True

    # --- filters ------------------------------------------------------------
    @rx.event
    def set_f_status(self, v: str) -> None:
        self.f_status = v

    @rx.event
    def set_f_pillar(self, v: str) -> None:
        self.f_pillar = v

    @rx.event
    def set_f_priority(self, v: str) -> None:
        self.f_priority = v

    @rx.event
    def set_f_overdue_only(self, v) -> None:
        self.f_overdue_only = bool(v)

    @rx.var
    def filtered_rows(self) -> list[dict[str, str]]:
        out = []
        for r in self.rows:
            if self.f_status != "All statuses" and r["status"] != self.f_status:
                continue
            if self.f_pillar != "All pillars" and r["pillar"] != self.f_pillar:
                continue
            if self.f_priority != "All priorities" \
                    and r["priority"] != self.f_priority:
                continue
            if self.f_overdue_only and not r["overdue"]:
                continue
            out.append(r)
        return out

    # --- form ---------------------------------------------------------------
    @rx.event
    def open_add(self) -> None:
        self.editing_id = ""
        self.form_title = "Add action"
        today = _today()
        self.a_raised = today.isoformat()
        self.a_pillar = "General Operations"
        self.a_issue = ""
        self.a_priority = "Medium"
        self.a_status = "Open"
        self.a_pic = ""
        self.a_due = ""
        self.a_corrective = ""
        self.a_completion = ""
        self.a_remarks = ""
        self.form_error = ""
        self.form_open = True

    @rx.event
    def open_edit(self, action_id: str) -> None:
        store_id = self._selected_store_id()
        if store_id is None:
            return
        try:
            user = self._actor()
            row = next((a for a in app_service.get_actions(user, store_id)
                        if str(a.id) == str(action_id)), None)
        except (AuthenticationError, AuthorizationError):
            self.form_error = "Session expired or no access."
            return
        if row is None:
            return
        self.editing_id = str(row.id)
        self.form_title = "Edit action"
        self.a_raised = row.raised_date.isoformat() if row.raised_date else ""
        self.a_pillar = row.input.pillar or "General Operations"
        self.a_issue = row.issue or ""
        self.a_priority = row.priority or "Medium"
        self.a_status = row.input.status or "Open"
        self.a_pic = row.pic or ""
        self.a_due = row.input.due_date.isoformat() if row.input.due_date else ""
        self.a_corrective = row.corrective_action or ""
        self.a_completion = row.completion_date.isoformat() \
            if row.completion_date else ""
        self.a_remarks = row.remarks or ""
        self.form_error = ""
        self.form_open = True

    @rx.event
    def set_a_raised(self, v: str) -> None:
        self.a_raised = v

    @rx.event
    def set_a_pillar(self, v: str) -> None:
        self.a_pillar = v

    @rx.event
    def set_a_issue(self, v: str) -> None:
        self.a_issue = v

    @rx.event
    def set_a_priority(self, v: str) -> None:
        self.a_priority = v

    @rx.event
    def set_a_status(self, v: str) -> None:
        self.a_status = v

    @rx.event
    def set_a_pic(self, v: str) -> None:
        self.a_pic = v

    @rx.event
    def set_a_due(self, v: str) -> None:
        self.a_due = v

    @rx.event
    def set_a_corrective(self, v: str) -> None:
        self.a_corrective = v

    @rx.event
    def set_a_completion(self, v: str) -> None:
        self.a_completion = v

    @rx.event
    def set_a_remarks(self, v: str) -> None:
        self.a_remarks = v

    @rx.event
    def set_form_open(self, v) -> None:
        self.form_open = bool(v)

    @rx.event
    def save_action(self):
        if not self.can_write_actions:
            self.form_error = "You have read-only access."
            return
        store_id = self._selected_store_id()
        if store_id is None:
            self.form_error = "Select a store first."
            return
        self.saving = True
        self.form_error = ""
        yield
        try:
            user = self._actor()
            app_service.save_action(
                user, store_id,
                int(self.editing_id) if self.editing_id else None,
                _today(),
                raised_date=_pdate(self.a_raised),
                pillar=self.a_pillar, issue=self.a_issue,
                priority=self.a_priority, status=self.a_status,
                pic=self.a_pic, due_date=_pdate(self.a_due),
                corrective_action=self.a_corrective or None,
                completion_date=_pdate(self.a_completion),
                remarks=self.a_remarks or None)
        except ValidationError as exc:
            self.form_error = str(exc)
            self.saving = False
            return
        except (AuthenticationError, AuthorizationError):
            self.form_error = "Session expired or insufficient permission."
            self.saving = False
            return
        except (WriteError, RecordNotFoundError) as exc:
            self.form_error = f"Could not save: {exc}"
            self.saving = False
            return
        self.saving = False
        self.form_open = False
        self.notice = "Action saved."
        yield
        yield ActionsState.load_actions_data()


def _pdate(s: str) -> Optional[date]:
    s = (s or "").strip()
    return date.fromisoformat(s) if s else None


def _raised_month(a) -> Optional[str]:
    return a.raised_date.strftime("%Y-%m") if a.raised_date else None


def _is_open(a) -> bool:
    return (a.input.status or "Open") in OPEN_STATUSES
