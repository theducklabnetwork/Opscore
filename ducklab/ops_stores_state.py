"""Store Management + Monthly Setup state.

All operations go through AppService with the session token revalidated on
every event; authorization uses the existing authz rules (store management
and user assignment: super_admin only; monthly config: config managers with
store access). Identity never comes from client input. Blank form fields are
stored as NULL; an explicit 0 is stored as 0.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.auth_pages import app_service
from ducklab.operations_repository import (
    RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import (
    DuplicateRecordError, ValidationError,
)
from ducklab.ops_stock_state import StockState
from ducklab.ops_state import OpsState, month_label

# Monthly setup field specs: (state attr, config field, kind, label)
# kind: "money" (RM -> cents) | "pct" (percent -> fraction) | "count"
SETUP_SALES = [
    ("s_min_rtm", "min_rtm_target_cents", "money", "Minimum RTM target (RM)"),
    ("s_level1", "level1_base_cents", "money", "Level 1 base target (RM)"),
    ("s_level2", "level2_pct", "pct", "Level 2 uplift (%)"),
    ("s_level3", "level3_pct", "pct", "Level 3 uplift (%)"),
    ("s_level4", "level4_pct", "pct", "Level 4 uplift (%)"),
    ("s_last_year", "sales_last_year_cents", "money",
     "Sales same month last year (RM)"),
    ("s_last_month", "sales_last_month_cents", "money",
     "Sales last month (RM)"),
]
SETUP_MANPOWER = [
    ("s_salary_ratio", "salary_ratio_target", "pct",
     "Salary ratio target (% of sales)"),
    ("s_budget_hc", "budget_headcount", "count", "Budget headcount"),
    ("s_turnover", "turnover_threshold", "pct", "High-turnover threshold (%)"),
    ("s_opening_hc", "opening_headcount_ft", "count",
     "Opening full-time headcount"),
    ("s_pt_factor", "part_timer_factor", "factor",
     "Part-timer conversion factor (e.g. 0.5)"),
]
SETUP_STOCK = [
    ("s_stock_target", "stock_loss_target", "pct",
     "Stock loss target (% of sales)"),
]
SETUP_PAYROLL = [
    ("s_salary", "salary_basic_allowance_cents", "money",
     "Basic salary + allowances (RM)"),
    ("s_overtime", "overtime_cents", "money", "Overtime (RM)"),
    ("s_support", "support_team_cost_cents", "money",
     "Support team cost (RM, optional)"),
]
SETUP_ALL = SETUP_SALES + SETUP_MANPOWER + SETUP_STOCK + SETUP_PAYROLL


def _money_str(cents) -> str:
    return "" if cents is None else f"{cents / 100:.2f}"


def _factor_str(factor) -> str:
    """Raw decimal factor (workbook Setup!B47, e.g. 0.5) — NOT a %."""
    return "" if factor is None else f"{float(factor):g}"


def _pct_str(ratio) -> str:
    return "" if ratio is None else f"{float(ratio) * 100:g}"


def _count_str(v) -> str:
    return "" if v is None else str(v)


class StoresState(StockState):
    """Store Management + Monthly Setup page state."""

    # --- stores table / form -------------------------------------------
    store_rows: list[dict[str, str]] = []
    can_manage_stores: bool = False
    can_assign_users: bool = False
    can_edit_setup: bool = False
    stores_error: str = ""
    stores_notice: str = ""

    sf_open: bool = False           # store form drawer
    sf_editing: str = ""            # store id as str, "" = create
    sf_code: str = ""
    sf_name: str = ""
    sf_concept: str = ""
    sf_zone: str = ""
    sf_zone_pic: str = ""
    sf_branch_manager: str = ""
    sf_error: str = ""
    sf_saving: bool = False

    # --- user assignment (per store) ------------------------------------
    assign_store_id: str = ""
    assign_rows: list[dict[str, str]] = []   # assigned users for that store
    assign_options: list[str] = []           # "email — name (roles)" to add
    assign_selected: str = ""
    _assign_all: list[dict] = []             # cache: all active users

    # --- monthly setup form ---------------------------------------------
    setup_finalized: bool = False
    setup_notice: str = ""
    setup_error: str = ""
    setup_saving: bool = False
    setup_levels: list[dict[str, str]] = []  # read-only L1-L4
    setup_original: dict = {}  # loaded values, for changed-only saves
    s_min_rtm: str = ""
    s_level1: str = ""
    s_level2: str = ""
    s_level3: str = ""
    s_level4: str = ""
    s_last_year: str = ""
    s_last_month: str = ""
    s_salary_ratio: str = ""
    s_budget_hc: str = ""
    s_turnover: str = ""
    s_opening_hc: str = ""
    s_pt_factor: str = ""
    s_stock_target: str = ""
    s_salary: str = ""
    s_overtime: str = ""
    s_support: str = ""
    budget_rows: list[dict[str, str]] = []   # per-category budget inputs

    # ------------------------------------------------------------------
    # clearing
    # ------------------------------------------------------------------
    def _clear_stores(self) -> None:
        self.stores_error = ""
        self.stores_notice = ""
        self.sf_open = False
        self.sf_editing = ""
        self.sf_code = self.sf_name = self.sf_concept = ""
        self.sf_zone = self.sf_zone_pic = self.sf_branch_manager = ""
        self.sf_error = ""
        self.sf_saving = False
        self.assign_store_id = ""
        self.assign_rows = []
        self.assign_options = []
        self.assign_selected = ""

    def _clear_setup(self) -> None:
        self.setup_original = {}
        self.setup_finalized = False
        self.setup_notice = ""
        self.setup_error = ""
        self.setup_saving = False
        self.setup_levels = []
        self.budget_rows = []
        for attr, _, _, _ in SETUP_ALL:
            setattr(self, attr, "")

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
            self.error_message = "Your session has expired. Please sign in again."
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
             "concept": s.concept or "—", "zone": s.zone or "—",
             "zone_pic": s.zone_pic or "—",
             "branch_manager": s.branch_manager or "—",
             "active": "1" if s.active else "0",
             "active_label": "Active" if s.active else "Inactive"}
            for s in stores
        ]

        # monthly setup for the current selection
        if store_id is None or month is None:
            return
        try:
            cfg = app_service.get_month_config(user, store_id, month)
            found = True
        except Exception:
            found = False
        try:
            self.setup_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
        except Exception:
            self.setup_finalized = False
        if found:
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
            }
            vals.update(payroll_vals)
            for attr, v in vals.items():
                setattr(self, attr, v)
            self.setup_original = dict(vals)
            levels = kpi.sales_target_levels_cents(cfg)
            self.setup_levels = [
                {"level": name.capitalize(),
                 "value": ds.fmt_rm(levels.get(name))}
                for name in ("level1", "level2", "level3", "level4")
            ]
        # expense budgets (rows always shown when categories exist)
        try:
            cats = app_service.get_expense_categories(user, store_id)
            budgets = app_service.get_expense_budgets(user, store_id, month)
            self.budget_rows = [
                {"id": str(c.id), "name": c.name,
                 "value": _money_str(budgets.get(c.id)),
                 "original": _money_str(budgets.get(c.id))}
                for c in cats
            ]
        except Exception:
            self.budget_rows = []

    # ------------------------------------------------------------------
    # store form events
    # ------------------------------------------------------------------
    @rx.event
    def open_store_form(self, store_id: str = "") -> None:
        self.sf_open = True
        self.sf_error = ""
        self.sf_editing = store_id
        if store_id:
            row = next((r for r in self.store_rows if r["id"] == store_id),
                       None)
            if row:
                self.sf_code = row["code"]
                self.sf_name = row["name"]
                self.sf_concept = "" if row["concept"] == "—" else row["concept"]
                self.sf_zone = "" if row["zone"] == "—" else row["zone"]
                self.sf_zone_pic = ("" if row["zone_pic"] == "—"
                                    else row["zone_pic"])
                self.sf_branch_manager = ("" if row["branch_manager"] == "—"
                                          else row["branch_manager"])
        else:
            self.sf_code = self.sf_name = self.sf_concept = ""
            self.sf_zone = self.sf_zone_pic = self.sf_branch_manager = ""

    @rx.event
    def close_store_form(self) -> None:
        self.sf_open = False

    @rx.event
    def save_store(self) -> None:
        self.sf_error = ""
        self.sf_saving = True
        yield
        try:
            user = self._actor()
            details = {
                k: (v.strip() or None)
                for k, v in (("concept", self.sf_concept),
                             ("zone", self.sf_zone),
                             ("zone_pic", self.sf_zone_pic),
                             ("branch_manager", self.sf_branch_manager))
            }
            if self.sf_editing:
                app_service.update_store_details(
                    user, int(self.sf_editing),
                    name=self.sf_name.strip(), **details)
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
        except (ValidationError, AuthorizationError, AuthenticationError) as exc:
            self.sf_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        finally:
            self.sf_saving = False
        yield StoresState.load_page_data()

    @rx.event
    def toggle_store_active(self, store_id: str) -> None:
        try:
            user = self._actor()
            row = next((r for r in self.store_rows if r["id"] == store_id),
                       None)
            if row is None:
                return
            if row["active"] == "1":
                app_service.set_store_active(user, int(store_id), False)
            else:
                app_service.set_store_active(user, int(store_id), True)
            self.stores_notice = (
                f"Store {row['code']} "
                + ("deactivated." if row["active"] == "1" else "activated."))
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        yield StoresState.load_page_data()

    # ------------------------------------------------------------------
    # user assignment
    # ------------------------------------------------------------------
    @rx.event
    def open_assign(self, store_id: str) -> None:
        self.assign_store_id = store_id
        self.assign_selected = ""
        yield StoresState.load_assign_rows()

    @rx.event
    def load_assign_rows(self) -> None:
        self.assign_rows = []
        self.assign_options = []
        if not self.assign_store_id:
            return
        try:
            user = self._actor()
            sid = int(self.assign_store_id)
            authz.require_store_grantable(user, sid)
            users = app_service.list_users(user)
            self._assign_all = [
                {"id": u.id, "email": u.email, "name": u.display_name,
                 "roles": ", ".join(u.roles), "stores": u.store_ids,
                 "is_active": u.is_active}
                for u in users
            ]
            assigned = [u for u in self._assign_all if sid in u["stores"]]
            self.assign_rows = [
                {"id": str(u["id"]),
                 "label": f"{u['email']} — {u['name']} ({u['roles']})",
                 "active": "1" if u["is_active"] else "0"}
                for u in assigned
            ]
            self.assign_options = [
                f"{u['email']} — {u['name']} ({u['roles']})"
                for u in self._assign_all
                if u["is_active"] and sid not in u["stores"]
            ]
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."

    @rx.event
    def select_assign_user(self, label: str) -> None:
        self.assign_selected = label

    @rx.event
    def assign_user(self) -> None:
        try:
            user = self._actor()
            sid = int(self.assign_store_id)
            target = next((u for u in self._assign_all
                           if f"{u['email']} — {u['name']} ({u['roles']})"
                           == self.assign_selected), None)
            if target is None:
                self.stores_error = "Select an existing user to assign."
                return
            app_service.assign_store(user, target["id"], sid)
            self.stores_notice = f"Assigned {target['email']}."
            self.assign_selected = ""
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        yield StoresState.load_assign_rows()

    @rx.event
    def unassign_user(self, user_id: str) -> None:
        try:
            user = self._actor()
            sid = int(self.assign_store_id)
            app_service.revoke_store(user, int(user_id), sid)
            self.stores_notice = "User unassigned."
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        yield StoresState.load_assign_rows()

    # ------------------------------------------------------------------
    # monthly setup save
    # ------------------------------------------------------------------
    def _parse_fields(self) -> dict:
        """Parse setup form strings -> config fields for ONE write.

        Only fields the user CHANGED are returned (blank fields the user
        never touched never overwrite a value saved elsewhere; a field the
        user cleared to blank writes NULL explicitly). ALL fields are
        validated first and every problem is reported together — nothing
        is written until the whole form is valid.
        """
        out: dict = {}
        errors: list[str] = []
        for attr, field, kind, label in SETUP_ALL:
            raw = getattr(self, attr).strip()
            if raw == self.setup_original.get(attr, ""):
                continue  # unchanged — do not send
            if raw == "":
                out[field] = None  # explicitly cleared
                continue
            try:
                if kind == "money":
                    cents = kpi.parse_money_cents(raw)
                    if cents < 0:
                        raise ValueError("negative")
                    out[field] = cents
                elif kind == "count":
                    out[field] = kpi.parse_count(raw, label)
                elif kind == "factor":
                    d = Decimal(raw)
                    if not d.is_finite() or not (0 <= d <= 2):
                        raise ValueError("range")
                    out[field] = float(d)
                else:  # pct -> fraction
                    d = Decimal(raw)
                    if not d.is_finite() or d < 0:
                        raise ValueError("range")
                    out[field] = float(d / 100)
            except (InvalidOperation, ValueError):
                errors.append(
                    f"{label}: enter a non-negative number (or leave blank)")
        return out, errors

    def _parse_budgets(self):
        """Validate every budget row before ANY write; -> (updates, errors).

        updates = {category_id: cents} only for rows the user changed
        (blank rows that were already blank are not sent)."""
        updates: dict[int, int | None] = {}
        errors: list[str] = []
        for row in self.budget_rows:
            raw = row["value"].strip()
            if raw == row.get("original", ""):
                continue
            cents = None
            if raw != "":
                try:
                    cents = kpi.parse_money_cents(raw)
                    if cents < 0:
                        raise ValueError("negative")
                except ValueError:
                    errors.append(
                        f"Budget for {row['name']}: non-negative number")
                    continue
            updates[int(row["id"])] = cents
        return updates, errors

    # ------------------------------------------------------------------
    # form field setters (explicit, matching the daily page pattern)
    # ------------------------------------------------------------------
    @rx.event
    def set_s_min_rtm(self, value: str) -> None:
        self.s_min_rtm = value

    @rx.event
    def set_s_level1(self, value: str) -> None:
        self.s_level1 = value

    @rx.event
    def set_s_level2(self, value: str) -> None:
        self.s_level2 = value

    @rx.event
    def set_s_level3(self, value: str) -> None:
        self.s_level3 = value

    @rx.event
    def set_s_level4(self, value: str) -> None:
        self.s_level4 = value

    @rx.event
    def set_s_last_year(self, value: str) -> None:
        self.s_last_year = value

    @rx.event
    def set_s_last_month(self, value: str) -> None:
        self.s_last_month = value

    @rx.event
    def set_s_salary_ratio(self, value: str) -> None:
        self.s_salary_ratio = value

    @rx.event
    def set_s_budget_hc(self, value: str) -> None:
        self.s_budget_hc = value

    @rx.event
    def set_s_turnover(self, value: str) -> None:
        self.s_turnover = value

    @rx.event
    def set_s_opening_hc(self, value: str) -> None:
        self.s_opening_hc = value

    @rx.event
    def set_s_pt_factor(self, value: str) -> None:
        self.s_pt_factor = value

    @rx.event
    def set_s_stock_target(self, value: str) -> None:
        self.s_stock_target = value

    @rx.event
    def set_s_salary(self, value: str) -> None:
        self.s_salary = value

    @rx.event
    def set_s_overtime(self, value: str) -> None:
        self.s_overtime = value

    @rx.event
    def set_s_support(self, value: str) -> None:
        self.s_support = value

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
    def set_sf_zone(self, value: str) -> None:
        self.sf_zone = value

    @rx.event
    def set_sf_zone_pic(self, value: str) -> None:
        self.sf_zone_pic = value

    @rx.event
    def set_sf_branch_manager(self, value: str) -> None:
        self.sf_branch_manager = value

    @rx.event
    def save_setup(self) -> None:
        self.setup_error = ""
        self.setup_notice = ""
        self.setup_saving = True
        yield
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            month = self._selected_month()
            if store_id is None or month is None:
                raise ValidationError("Select a store and month first.")
            # Parse and validate EVERYTHING first; one bad field or
            # budget rejects the whole save and nothing is written.
            fields, errors = self._parse_fields()
            budgets, budget_errors = self._parse_budgets()
            errors += budget_errors
            if errors:
                raise ValidationError("; ".join(errors))
            if not fields and not budgets:
                raise ValidationError("Nothing to save — no changes made.")
            result = app_service.configure_month_with_budgets(
                user, store_id, month, fields, budgets)
            self.setup_notice = (
                "Monthly setup saved."
                + (" Note: a finalized snapshot exists — the finalized report"
                   " is unchanged; these edits affect live figures only."
                   if result.get("finalized_snapshot_exists") else ""))
        except (ValidationError, AuthorizationError, DuplicateRecordError,
                RepositoryError) as exc:
            self.setup_error = str(exc)
        except AuthenticationError:
            self.setup_error = "Session expired — sign in again."
        finally:
            self.setup_saving = False
        yield
        yield StoresState.load_page_data()

    @rx.event
    def set_budget_value(self, category_id: str, value: str) -> None:
        self.budget_rows = [
            {**r, "value": value} if r["id"] == category_id else r
            for r in self.budget_rows
        ]
