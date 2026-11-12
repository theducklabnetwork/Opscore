"""Monthly Setup state (Store Management workflow).

All operations go through AppService with the session token revalidated on
every event; monthly config requires config managers with store access.
Identity never comes from client input. Blank form fields are stored as
NULL; an explicit 0 is stored as 0.
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Optional

import reflex as rx

from ducklab import authz
from ducklab import design as ds
from ducklab import kpi
from ducklab.input_validation import (
    parse_count, parse_decimal, parse_money_cents,
)
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.auth_pages import app_service
from ducklab.operations_repository import (
    ConfigNotFoundError, RepositoryError, StoreNotFoundError,
)
from ducklab.operations_write_repository import (
    DuplicateRecordError, ValidationError,
)
from ducklab.ops_shared_state import (
    SESSION_EXPIRED, SharedOpsState, auth_message,
)
from ducklab.ops_expenses_state import BUDGET_CLEAR_CONFLICT

# Monthly setup field specs: (state attr, config field, kind, label)
# kind: "money" (RM -> cents) | "pct" (percent -> fraction) | "count"
SETUP_SALES = [
    ("s_min_rtm", "min_rtm_target_cents", "money", "Minimum RTM (RM)"),
    ("s_level1", "level1_base_cents", "money", "Level 1 Base (RM)"),
    ("s_level2", "level2_pct", "pct", "Level 2 Uplift (%)"),
    ("s_level3", "level3_pct", "pct", "Level 3 Uplift (%)"),
    ("s_level4", "level4_pct", "pct", "Level 4 Uplift (%)"),
    ("s_last_year", "sales_last_year_cents", "money",
     "Sales Last Year (RM)"),
    ("s_last_month", "sales_last_month_cents", "money",
     "Sales Last Month (RM)"),
]
SETUP_MANPOWER = [
    ("s_salary_ratio", "salary_ratio_target", "pct",
     "Salary Ratio Target (%)"),
    ("s_budget_hc", "budget_headcount", "count", "Budget Headcount"),
    ("s_turnover", "turnover_threshold", "pct", "Turnover Threshold (%)"),
    ("s_opening_hc", "opening_headcount_ft", "count",
     "Opening Headcount"),
    ("s_pt_factor", "part_timer_factor", "factor",
     "Part-Timer Factor"),
]
SETUP_STOCK = [
    ("s_stock_target", "stock_loss_target", "pct",
     "Stock Loss Target (%)"),
]
SETUP_PAYROLL = [
    ("s_salary", "salary_basic_allowance_cents", "money",
     "Basic + Allowance (RM)"),
    ("s_overtime", "overtime_cents", "money", "Overtime (RM)"),
    ("s_support", "support_team_cost_cents", "money",
     "Support Cost (RM)"),
]
# Daily Sales targets: monthly transactions, SPR (RM) and IPR (items)
SETUP_DAILY = [
    ("s_target_txn", "target_transactions", "count", "Target Transactions"),
    ("s_target_spr", "target_spr_cents", "money", "Target BV (RM)"),
    ("s_target_ipr", "target_ipr", "ipr", "Target UPT"),
]
SETUP_ALL = (SETUP_SALES + SETUP_DAILY + SETUP_MANPOWER + SETUP_STOCK
             + SETUP_PAYROLL)

log = logging.getLogger("ducklab.ops_setup_state")


def _money_str(cents) -> str:
    return "" if cents is None else f"{cents / 100:.2f}"


def _factor_str(factor) -> str:
    """Raw decimal factor (workbook Setup!B47, e.g. 0.5) — NOT a %."""
    return "" if factor is None else f"{float(factor):g}"


# "Use HQ defaults": Minimum RTM in RM and Level 2-4 uplift in percent,
# filled visibly into the form (never applied silently on save)
HQ_DEFAULT_UPLIFTS = {"s_min_rtm": "200000.00", "s_level2": "10",
                      "s_level3": "20", "s_level4": "30"}


def _pct_str(ratio) -> str:
    return "" if ratio is None else f"{float(ratio) * 100:g}"


def _count_str(v) -> str:
    return "" if v is None else str(v)


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y - (m == 1):04d}-{12 if m == 1 else m - 1:02d}"


class SetupState(SharedOpsState):
    """Monthly Setup workflow state."""

    # --- monthly setup form ---------------------------------------------
    can_edit_setup: bool = False
    has_previous_setup: bool = False
    setup_finalized: bool = False
    setup_notice: str = ""
    setup_error: str = ""
    setup_saving: bool = False
    setup_levels: list[dict[str, str]] = []  # read-only L1-L4
    setup_levels_missing: bool = False  # saved L1 without a L2-4 uplift
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
    s_target_txn: str = ""
    s_target_spr: str = ""
    s_target_ipr: str = ""
    s_holidays: list[str] = []               # ISO dates in the month
    s_holidays_original: list[str] = []
    s_holiday_min: str = ""                  # date picker bounds (month)
    s_holiday_max: str = ""
    budget_rows: list[dict[str, str]] = []   # per-category budget inputs

    def _clear_setup(self) -> None:
        self.setup_original = {}
        self.has_previous_setup = False
        self.setup_finalized = False
        self.setup_notice = ""
        self.setup_error = ""
        self.setup_saving = False
        self.setup_levels = []
        self.setup_levels_missing = False
        self.budget_rows = []
        self.s_holidays = []
        self.s_holidays_original = []
        self.s_holiday_min = self.s_holiday_max = ""
        for attr, _, _, _ in SETUP_ALL:
            setattr(self, attr, "")

    @rx.event
    def reset_setup(self) -> None:
        """Clear the Monthly Setup form (store/month switch)."""
        self._clear_setup()

    # ------------------------------------------------------------------
    # loading
    # ------------------------------------------------------------------
    @rx.event
    def load_setup_data(self) -> None:
        """Load the Monthly Setup form for the selected store/month."""
        try:
            user = self._actor()
        except AuthenticationError:
            self.setup_error = SESSION_EXPIRED
            return
        store_id = self._selected_store_id()
        month = self._selected_month()
        self.can_edit_setup = (
            self.selected_store_active and
            store_id is not None and authz.can_manage_config(user)
            and authz.can_access_store(user, store_id))
        if store_id is None or month is None:
            self.has_previous_setup = False
            return
        try:
            try:
                app_service.get_month_config(
                    user, store_id, _prev_month(month))
                self.has_previous_setup = True
            except ConfigNotFoundError:
                self.has_previous_setup = False
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
             "value": _pct_str(budgets.get(c.id)),
             "original": _pct_str(budgets.get(c.id)),
             "clear": ""}
            for c in cats
        ]

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
                    cents = parse_money_cents(raw)
                    if cents < 0:
                        raise ValueError("negative")
                    out[field] = cents
                elif kind == "count":
                    out[field] = parse_count(raw, label)
                elif kind == "factor":
                    out[field] = float(parse_decimal(
                        raw, label, Decimal(0), Decimal(2)))
                elif kind == "ipr":
                    out[field] = float(parse_decimal(
                        raw, label, Decimal(0), Decimal(1000)))
                else:  # pct -> fraction (repository range 0..10 = 1000%)
                    out[field] = float(parse_decimal(
                        raw, label, Decimal(0), Decimal(1000)) / 100)
            except ValueError:
                errors.append(
                    f"{label}: enter a non-negative number (or leave blank)")
        return out, errors

    def _parse_holidays(self) -> Optional[list[date]]:
        """The picked public holidays, or None when unchanged."""
        if sorted(self.s_holidays) == sorted(self.s_holidays_original):
            return None
        return [date.fromisoformat(d) for d in sorted(self.s_holidays)]

    def _parse_budgets(self):
        """Validate every budget row before ANY write; -> (updates, errors)."""
        return _parse_budget_rows(self.budget_rows)

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
    def set_s_target_txn(self, value: str) -> None:
        self.s_target_txn = value

    @rx.event
    def set_s_target_spr(self, value: str) -> None:
        self.s_target_spr = value

    @rx.event
    def set_s_target_ipr(self, value: str) -> None:
        self.s_target_ipr = value

    @rx.event
    def add_holiday(self, value: str) -> None:
        """Date picker: add one public holiday in the selected month."""
        month = self._selected_month()
        try:
            day = date.fromisoformat(value)
        except (TypeError, ValueError):
            return
        if month is None or day.strftime("%Y-%m") != month:
            self.setup_error = "Public Holidays: pick a date in this month."
            return
        if value not in self.s_holidays:
            self.s_holidays = sorted([*self.s_holidays, value])

    @rx.event
    def remove_holiday(self, value: str) -> None:
        self.s_holidays = [d for d in self.s_holidays if d != value]

    @rx.var
    def target_txn_placeholder(self) -> str:
        if self.s_target_txn.strip():
            return "—"
        try:
            level1 = parse_money_cents(self.s_level1)
            target_bv = parse_money_cents(self.s_target_spr)
        except ValueError:
            return "—"
        return _count_str(kpi.effective_target_transactions(
            None, level1, target_bv)) or "—"

    @rx.var
    def holiday_chips(self) -> list[dict[str, str]]:
        return [{"iso": d, "label": date.fromisoformat(d).strftime("%d %b")}
                for d in self.s_holidays]

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
            holidays = self._parse_holidays()
            if not fields and not budgets and holidays is None:
                raise ValidationError("Nothing to save — no changes made.")
            result = app_service.configure_month_with_budgets(
                user, store_id, month, fields, budgets, holidays)
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
        except Exception as exc:
            # Never crash the handler silently: log with traceback and
            # tell the user nothing was saved (the write is atomic).
            log.exception("Monthly setup save failed")
            self.setup_error = (f"Save failed ({type(exc).__name__}); "
                                "nothing was saved.")
        finally:
            self.setup_saving = False
        yield
        yield SetupState.load_setup_data()

    @rx.event
    def use_hq_defaults(self) -> None:
        """Fill Minimum RTM and Level 2-4 uplift with the HQ defaults
        in the form; nothing is saved until Save."""
        for attr, value in HQ_DEFAULT_UPLIFTS.items():
            setattr(self, attr, value)

    @rx.event
    def copy_previous_month(self) -> None:
        """Pre-fill the form from last month; Save Setup persists changes."""
        self.setup_error = ""
        self.setup_notice = ""
        try:
            user = self._actor()
            store_id = self._selected_store_id()
            month = self._selected_month()
            if (not self.can_edit_setup or not self.selected_store_active
                    or store_id is None or month is None
                    or not authz.can_manage_config(user)
                    or not authz.can_access_store(user, store_id)):
                raise AuthorizationError("You cannot edit this setup.")
            previous_month = _prev_month(month)
            cfg = app_service.get_month_config(user, store_id, previous_month)
            budgets = app_service.get_expense_budgets(
                user, store_id, previous_month)
            self.has_previous_setup = True
        except ConfigNotFoundError:
            self.has_previous_setup = False
            return
        except (AuthenticationError, AuthorizationError,
                StoreNotFoundError) as exc:
            self.setup_error = auth_message(exc)
            return

        for attr, fmt in (
                ("s_min_rtm", _money_str),
                ("s_level2", _pct_str), ("s_level3", _pct_str),
                ("s_level4", _pct_str), ("s_target_spr", _money_str),
                ("s_target_ipr", _factor_str), ("s_salary_ratio", _pct_str),
                ("s_budget_hc", _count_str), ("s_turnover", _pct_str),
                ("s_pt_factor", _factor_str), ("s_stock_target", _pct_str)):
            field = next(spec[1] for spec in SETUP_ALL if spec[0] == attr)
            setattr(self, attr, fmt(getattr(cfg, field)))
        self.budget_rows = [
            {**row, "value": _pct_str(budgets.get(int(row["id"]))),
             "clear": "1" if row["original"] and int(row["id"]) not in budgets
             else ""}
            for row in self.budget_rows
        ]
        self.setup_notice = "Copied last month's setup. Save Setup to apply."

    @rx.event
    def set_budget_value(self, category_id: str, value: str) -> None:
        # typing an amount undoes the ×
        self.budget_rows = [
            {**r, "value": value,
             "clear": "" if value.strip() else r.get("clear", "")}
            if r["id"] == category_id else r
            for r in self.budget_rows
        ]

    @rx.event
    def clear_budget(self, category_id: str) -> None:
        """× beside a saved budget: blank it; save writes NULL."""
        self.budget_rows = [
            {**r, "value": "", "clear": "1"} if r["id"] == category_id
            else r
            for r in self.budget_rows
        ]

    @rx.event
    def set_budget_clear(self, category_id: str, v: bool) -> None:
        self.budget_rows = [
            {**r, "clear": "1" if v else ""} if r["id"] == category_id else r
            for r in self.budget_rows
        ]


def _parse_budget_rows(rows) -> tuple[dict[int, Optional[float]], list[str]]:
    """Budget rows -> (updates, errors); pure, so it is testable.

    updates = {category_id: fraction | None} only for rows the user changed.
    Cleared with × writes NULL ("No budget"), never 0. Cleared AND a
    different percentage typed is rejected (nothing saved for that row).
    Untouched rows send nothing. Blanking a saved budget WITHOUT the × is
    rejected (a missing budget is distinct from 0%)."""
    updates: dict[int, Optional[float]] = {}
    errors: list[str] = []
    for row in rows:
        raw = row["value"].strip()
        if row.get("clear"):
            if raw and raw != row.get("original", ""):
                errors.append(f"Budget for {row['name']}: "
                              f"{BUDGET_CLEAR_CONFLICT}")
                continue
            if row.get("original", ""):
                updates[int(row["id"])] = None
                continue
        if raw == row.get("original", ""):
            continue
        if raw == "":
            errors.append(
                f"Budget for {row['name']}: enter a budget %, or use "
                "× to clear it (No budget).")
            continue
        try:
            pct = parse_decimal(raw, "Budget %", Decimal(0), Decimal(100))
        except ValueError:
            errors.append(
                f"Budget for {row['name']}: percentage between 0 and 100")
            continue
        updates[int(row["id"])] = float(pct / 100)
    return updates, errors
