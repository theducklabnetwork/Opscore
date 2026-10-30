"""Store Management page state: store CRUD, selectors and page loading.

Workflows extracted for maintainability (behavior unchanged):
- user assignment -> ducklab.ops_assignment_state (AssignmentState)
- Monthly Setup   -> ducklab.ops_setup_state (SetupState)

All operations go through AppService with the session token revalidated on
every event; authorization uses the existing authz rules (store management:
super_admin only). Identity never comes from client input.
"""

from __future__ import annotations

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab.input_validation import parse_client_id
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.auth_pages import app_service
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import (
    CONCEPTS, DuplicateRecordError, RecordNotFoundError, ValidationError,
)
from ducklab.ops_setup_state import (
    SetupState, _count_str, _factor_str, _money_str, _pct_str,
)
from ducklab.ops_state import SESSION_EXPIRED, OpsState, auth_message


class StoresState(SetupState):
    """Store Management page state (store form + selectors + loader)."""

    # --- store form drawer ---------------------------------------------
    sf_open: bool = False           # store form drawer
    sf_editing: str = ""            # store id as str, "" = create
    sf_code: str = ""
    sf_name: str = ""
    sf_concept: str = ""
    sf_state: str = ""
    sf_zone: str = ""
    sf_zone_pic: str = ""
    sf_branch_manager: str = ""
    sf_error: str = ""
    sf_saving: bool = False

    # ------------------------------------------------------------------
    # clearing
    # ------------------------------------------------------------------
    def _clear_stores(self) -> None:
        self.stores_error = ""
        self.stores_notice = ""
        self.sf_open = False
        self.sf_editing = ""
        self.sf_code = self.sf_name = self.sf_concept = self.sf_state = ""
        self.sf_zone = self.sf_zone_pic = self.sf_branch_manager = ""
        self.sf_error = ""
        self.sf_saving = False
        self.assign_store_id = ""
        self.assign_rows = []
        self.assign_options = []
        self.assign_selected = ""

    # ------------------------------------------------------------------
    # selectors: clear prior-store form data on switch
    # ------------------------------------------------------------------
    @rx.event
    def select_store(self, label: str):
        if label == self.selected_store_label:
            return
        self.selected_store_label = label
        self._clear_manpower()
        self._clear_stock()
        self._clear_setup()
        self._clear_stores()
        self._clear_report()
        yield StoresState.refresh_report()
        yield StoresState.load_page_data()

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_manpower()
        self._clear_stock()
        self._clear_setup()
        self._clear_report()
        yield StoresState.refresh_report()
        yield StoresState.load_page_data()

    @rx.event
    def load_stores(self):
        guard = self.require_auth()
        if guard is not None:
            return guard
        yield OpsState.load_ops()
        yield StoresState.load_page_data()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @rx.event
    def load_page_data(self) -> None:
        # NOTE: notices/errors from a just-completed save are preserved
        # here; store/month switches clear them via select_store/select_month.
        try:
            user = self._actor()
        except AuthenticationError:
            self.error_message = self.stores_error = SESSION_EXPIRED
            self.report_status = "error"
            return
        self.can_manage_stores = authz.can_manage_users(user)
        self.can_assign_users = authz.can_manage_users(user)
        store_id = self._selected_store_id()
        month = self._selected_month()
        self.can_edit_setup = (
            self.selected_store_active and
            store_id is not None and authz.can_manage_config(user)
            and authz.can_access_store(user, store_id))

        # stores table (detail via read repo; super admin sees all)
        try:
            stores = [app_service.read_repo.get_store(s["id"])
                      for s in app_service.list_my_stores(user)]
        except (AuthorizationError, RepositoryError) as exc:
            self.stores_error = str(exc)
            return
        self.store_rows = [
            {"id": str(s.id), "code": s.code, "name": s.name,
             "concept": s.concept or "—", "state": s.state or "—",
             "zone": s.zone or "—",
             "zone_pic": s.zone_pic or "—",
             "branch_manager": s.branch_manager or "—",
             "active": "1" if s.active else "0",
             "active_label": "Active" if s.active else "Disabled"}
            for s in stores
        ]

        # monthly setup for the current selection
        if store_id is None or month is None:
            return
        try:
            try:
                cfg = app_service.get_month_config(user, store_id, month)
            except ConfigNotFoundError:
                cfg = None
            self.setup_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
            cats = app_service.get_expense_categories(user, store_id)
            budgets = app_service.get_expense_budgets(user, store_id, month)
            holidays = app_service.get_public_holidays(user, store_id, month)
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            # never show blank setup fields for an auth failure
            self._clear_setup()
            self.can_edit_setup = False
            self.setup_error = auth_message(exc)
            return
        if cfg is not None:
            # Payroll values (salary/overtime/support/ratio target) enter
            # state ONLY when the user may edit setup (can_edit_setup,
            # which is gated on authz.can_manage_config).
            payroll_vals = (
                {"s_salary_ratio": _pct_str(cfg.salary_ratio_target),
                 "s_salary": _money_str(cfg.salary_basic_allowance_cents),
                 "s_overtime": _money_str(cfg.overtime_cents),
                 "s_support": _money_str(cfg.support_team_cost_cents)}
                if self.can_edit_setup else
                {"s_salary_ratio": "", "s_salary": "", "s_overtime": "",
                 "s_support": ""})
            vals = {
                "s_min_rtm": _money_str(cfg.min_rtm_target_cents),
                "s_level1": _money_str(cfg.level1_base_cents),
                "s_level2": _pct_str(cfg.level2_pct),
                "s_level3": _pct_str(cfg.level3_pct),
                "s_level4": _pct_str(cfg.level4_pct),
                "s_last_year": _money_str(cfg.sales_last_year_cents),
                "s_last_month": _money_str(cfg.sales_last_month_cents),
                "s_budget_hc": _count_str(cfg.budget_headcount),
                "s_turnover": _pct_str(cfg.turnover_threshold),
                "s_opening_hc": _count_str(cfg.opening_headcount_ft),
                "s_pt_factor": _factor_str(cfg.part_timer_factor),
                "s_stock_target": _pct_str(cfg.stock_loss_target),
                "s_target_txn": _count_str(cfg.target_transactions),
                "s_target_spr": _money_str(cfg.target_spr_cents),
                "s_target_ipr": _factor_str(cfg.target_ipr),
            }
            vals.update(payroll_vals)
            for attr, v in vals.items():
                setattr(self, attr, v)
            self.setup_original = dict(vals)
            levels = kpi.sales_target_levels_cents(cfg)
            self.setup_levels_missing = kpi.levels_missing(cfg)
            self.setup_levels = [
                {"level": f"Level {n}",
                 "value": ds.fmt_rm(levels.get(f"level{n}"))}
                for n in (1, 2, 3, 4)
            ]
        self.s_holidays = [d.isoformat() for d in holidays]
        self.s_holidays_original = list(self.s_holidays)
        first, last, _ = kpi.month_bounds(month)
        self.s_holiday_min, self.s_holiday_max = (first.isoformat(),
                                                  last.isoformat())
        # expense budgets (rows always shown when categories exist)
        self.budget_rows = [
            {"id": str(c.id), "name": c.name,
             "value": _money_str(budgets.get(c.id)),
             "original": _money_str(budgets.get(c.id)),
             "clear": ""}
            for c in cats
        ]

    # ------------------------------------------------------------------
    # store form events
    # ------------------------------------------------------------------
    @rx.event
    def open_store_form(self, store_id: str = "") -> None:
        try:
            sid = parse_client_id(store_id, "store")
        except ValidationError as exc:
            self.stores_error = str(exc)
            return
        row = None
        if sid is not None:
            row = next((r for r in self.store_rows if r["id"] == str(sid)),
                       None)
            if row is None:
                self.stores_error = "Unknown store — reopen it from the list."
                return
        self.sf_open = True
        self.sf_error = ""
        self.sf_editing = str(sid) if sid is not None else ""
        if sid is not None:
            if row:
                self.sf_code = row["code"]
                self.sf_name = row["name"]
                self.sf_concept = "" if row["concept"] == "—" else row["concept"]
                self.sf_state = "" if row["state"] == "—" else row["state"]
                self.sf_zone = "" if row["zone"] == "—" else row["zone"]
                self.sf_zone_pic = ("" if row["zone_pic"] == "—"
                                    else row["zone_pic"])
                self.sf_branch_manager = ("" if row["branch_manager"] == "—"
                                          else row["branch_manager"])
        else:
            self.sf_code = self.sf_name = self.sf_concept = self.sf_state = ""
            self.sf_zone = self.sf_zone_pic = self.sf_branch_manager = ""

    @rx.var
    def sf_concept_hint(self) -> str:
        """A saved concept that is no longer an option stays until the
        store is edited; then an option must be picked."""
        if self.sf_concept in ("", *CONCEPTS):
            return ""
        return f"Current: {self.sf_concept}. Pick an option."

    @rx.event
    def close_store_form(self) -> None:
        self.sf_open = False

    @rx.event
    def save_store(self) -> None:
        self.sf_error = ""
        self.sf_saving = True
        yield
        try:
            editing = parse_client_id(self.sf_editing, "store")
            user = self._actor()
            details = {
                k: (v.strip() or None)
                for k, v in (("concept", self.sf_concept),
                             ("state", self.sf_state),
                             ("zone", self.sf_zone),
                             ("zone_pic", self.sf_zone_pic),
                             ("branch_manager", self.sf_branch_manager))
            }
            if editing is not None:
                app_service.update_store_details(
                    user, editing, name=self.sf_name.strip(), **details)
            else:
                if not self.sf_code.strip() or not self.sf_name.strip():
                    raise ValidationError("Store code and name are required")
                app_service.register_store(
                    user, self.sf_code.strip(), self.sf_name.strip(),
                    **details)
            self.sf_open = False
            self.stores_notice = "Store saved."
        except DuplicateRecordError:
            self.sf_error = f"Store code '{self.sf_code.strip()}' already exists."
        except (StoreNotFoundError, RecordNotFoundError):
            self.sf_error = "This store no longer exists."
        except (ValidationError, AuthorizationError, AuthenticationError) as exc:
            self.sf_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        except RepositoryError as exc:
            self.sf_error = f"Save failed: {exc}"
        finally:
            self.sf_saving = False
        yield StoresState.load_page_data()

    @rx.event
    def toggle_store_active(self, store_id: str) -> None:
        try:
            sid = parse_client_id(store_id, "store", required=True)
            user = self._actor()
            row = next((r for r in self.store_rows if r["id"] == str(sid)),
                       None)
            if row is None:
                raise ValidationError("Unknown store — reopen it from the list.")
            app_service.set_store_active(user, sid, row["active"] != "1")
            self.stores_notice = (
                f"Store {row['code']} "
                + ("deactivated." if row["active"] == "1" else "activated."))
        except ValidationError as exc:
            self.stores_error = str(exc)
        except (StoreNotFoundError, RecordNotFoundError):
            self.stores_error = "This store no longer exists."
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        yield StoresState.load_page_data()

    @rx.event
    def set_sf_code(self, value: str) -> None:
        self.sf_code = value

    @rx.event
    def set_sf_name(self, value: str) -> None:
        self.sf_name = value

    @rx.event
    def set_sf_concept(self, value: str) -> None:
        self.sf_concept = value

    @rx.event
    def set_sf_state(self, value: str) -> None:
        self.sf_state = value

    @rx.event
    def set_sf_zone(self, value: str) -> None:
        self.sf_zone = value

    @rx.event
    def set_sf_zone_pic(self, value: str) -> None:
        self.sf_zone_pic = value

    @rx.event
    def set_sf_branch_manager(self, value: str) -> None:
        self.sf_branch_manager = value
