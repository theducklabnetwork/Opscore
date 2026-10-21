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
from datetime import date
from decimal import Decimal, InvalidOperation
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
from ducklab.ops_state import OpsState, month_label

# All status colors: ds.status_color (design.py shared map)


def _parse_decimal(raw: str, label: str, lo: Decimal, hi: Decimal) -> Decimal:
    try:
        d = Decimal(raw.strip())
    except (InvalidOperation, ValueError):
        raise ValueError(f"{label} must be a number")
    if not (lo <= d <= hi):
        raise ValueError(f"{label} must be between {lo} and {hi}")
    return d


def _pct_str(value) -> str:
    """Decimal fraction -> editable percent string ('0.06818' -> '6.818')."""
    if value is None:
        return ""
    return f"{float(value) * 100:g}"


class ManpowerState(ExpensesState):
    """Manpower Efficiency page state (inherits selectors + guards)."""

    can_view_payroll: bool = False       # payroll confidentiality gate
    can_manage_manpower: bool = False    # config editor gate
    mp_finalized: bool = False
    mp_config_exists: bool = True
    mp_error: str = ""
    mp_notice: str = ""

    # KPI groups (preformatted strings)
    hc_kpis: dict[str, str] = {}         # headcount
    eff_kpis: dict[str, str] = {}        # efficiency / turnover
    pay_kpis: dict[str, str] = {}        # payroll (restricted values)
    prod_kpis: dict[str, str] = {}       # productivity
    leave_kpis: dict[str, str] = {}      # leave totals
    move_kpis: dict[str, str] = {}       # movement summary
    pt_factor_label: str = "—"

    # daily staffing rows
    staff_rows: list[dict[str, str]] = []
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
        self.prod_kpis = {}
        self.leave_kpis = {}
        self.move_kpis = {}
        self.staff_rows = []
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

    @rx.event
    def select_month(self, label: str):
        if label == self.selected_month_label:
            return
        self.selected_month_label = label
        self._clear_manpower()
        self._clear_report()
        yield ManpowerState.refresh_report()
        yield ManpowerState.load_manpower_data()

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
            self.error_message = "Your session has expired. Please sign in again."
            self.report_status = "error"
            return
        self.can_view_payroll = authz.can_manage_config(user)
        self.can_manage_manpower = authz.can_manage_config(user)
        try:
            self.mp_finalized = bool(
                app_service.list_report_versions(user, store_id, month))
        except Exception:
            self.mp_finalized = False

        # Month configuration is required for manpower monitoring.
        try:
            cfg = app_service.get_month_config(user, store_id, month)
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
            self.mp_error = "You do not have access to this store."
            return
        self.pt_factor_label = (
            f"{float(cfg.part_timer_factor):g}"
            if cfg.part_timer_factor is not None else "not configured")

        # Live assembled month: every figure from the verified KPI engine.
        try:
            rep = app_service.preview_month(user, store_id, month)
        except (AuthorizationError, StoreNotFoundError, RepositoryError) as exc:
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
            "variance": ("—" if m.headcount_variance is None
                         else f"{m.headcount_variance:+,}"),
            "status": m.headcount_status,
            "status_color": ds.status_color(m.headcount_status),
        }
        self.eff_kpis = {
            "avg_effective": ds.fmt_num(
                float(m.average_effective_staff)
                if m.average_effective_staff is not None else None),
            "sales_per_staff": ds.fmt_rm(
                int(m.sales_per_staff)
                if m.sales_per_staff is not None else None),
            "turnover": ds.fmt_pct(m.turnover_pct),
            "turnover_status": m.turnover_status,
            "turnover_color": ds.status_color(m.turnover_status),
            "threshold": ds.fmt_pct(cfg.turnover_threshold),
        }
        # Payroll block: restricted values are never populated for
        # non-authorized roles (not merely hidden client-side).
        if self.can_view_payroll:
            budget = m.salary_budget_cents
            total = m.total_salary_cents
            variance = None
            if budget is not None and total is not None:
                variance = int(budget) - total
            ratio_used_pct = None
            if (m.salary_ratio_incl_support is not None
                    and cfg.salary_ratio_target):
                ratio_used_pct = min(
                    float(m.salary_ratio_incl_support
                          / cfg.salary_ratio_target) * 100, 100.0)
            self.pay_kpis = {
                "basic": ds.fmt_rm(cfg.salary_basic_allowance_cents),
                "overtime": ds.fmt_rm(cfg.overtime_cents),
                "total": ds.fmt_rm(total),
                "support": ds.fmt_rm(cfg.support_team_cost_cents),
                "ratio": ds.fmt_pct(m.salary_ratio_store),
                "ratio_support": ds.fmt_pct(m.salary_ratio_incl_support),
                "target": ds.fmt_pct(cfg.salary_ratio_target),
                "budget": ds.fmt_rm0(int(budget)
                                     if budget is not None else None),
                "variance": ds.fmt_rm0(variance),
                "status": m.salary_ratio_status,
                "status_color": ds.status_color(m.salary_ratio_status),
                "ratio_bar": (f"{ratio_used_pct:.0f}"
                              if ratio_used_pct is not None else "0"),
                "sales": ds.fmt_rm(s.sales_mtd_cents),
            }
        else:
            self.pay_kpis = {"status": "Restricted", "status_color": "gray"}
        self.prod_kpis = {
            "sales_mtd": ds.fmt_rm(s.sales_mtd_cents),
            "avg_effective": self.eff_kpis["avg_effective"],
            "sales_per_staff": self.eff_kpis["sales_per_staff"],
            "completeness": f"{s.days_entered} of {s.days_in_month} days reported",
        }
        self.move_kpis = {
            "opening": self.hc_kpis["opening"],
            "hires": self.hc_kpis["hires"],
            "resigned": self.hc_kpis["resigned"],
            "closing": self.hc_kpis["closing"],
            "turnover": self.eff_kpis["turnover"],
            "turnover_status": m.turnover_status,
        }

        # --- daily staffing rows + leave totals --------------------------
        try:
            days = app_service.get_daily_records(user, store_id, month)
        except (AuthorizationError, StoreNotFoundError):
            days = []
        by_date = {d.date: d for d in days}
        y, mo = int(month[:4]), int(month[5:7])
        n_days = calendar.monthrange(y, mo)[1]
        rows: list[dict[str, str]] = []
        for day_num in range(1, n_days + 1):
            d = date(y, mo, day_num)
            rows.append(self._staff_row(d, by_date.get(d),
                                        cfg.part_timer_factor))
        self.staff_rows = rows
        reported = sum(1 for r in rows if r["status"] != "unreported")
        self.staffing_note = (
            f"{reported} of {n_days} days have staffing data. Days without "
            "attendance are shown as unreported — never as zero staff.")
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
                    "status": "unreported",
                    "status_color": ds.status_color("unreported")}
        eff = kpi.effective_staff(rec.ft_working, rec.pt_working, factor)
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
        status = ("complete" if filled >= 3
                  else "partial" if filled else "unreported")
        return {
            **base,
            "ft": ds.fmt_num(rec.ft_working),
            "pt": ds.fmt_num(rec.pt_working),
            "effective": (f"{float(eff):,.2f}" if eff is not None else "—"),
            "hires": ds.fmt_num(rec.new_hires_ft),
            "resigned": ds.fmt_num(rec.resigned_ft),
            "leave": (ds.fmt_num(leave) if leave_reported else "—"),
            "sales": ds.fmt_rm(rec.sales_cents),
            "sales_per_staff": (f"RM {sps / 100:,.2f}"
                                if sps is not None else "—"),
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
                # payroll fields load ONLY for configuration managers.
                can_payroll = authz.can_manage_config(user)
                cfg = app_service.get_month_config(user, store_id, month)
                staffing = {
                    "c_opening": (str(cfg.opening_headcount_ft)
                                  if cfg.opening_headcount_ft is not None
                                  else ""),
                    "c_budget": (str(cfg.budget_headcount)
                                 if cfg.budget_headcount is not None else ""),
                    "c_pt_factor": (f"{float(cfg.part_timer_factor):g}"
                                    if cfg.part_timer_factor is not None
                                    else ""),
                    "c_turnover": _pct_str(cfg.turnover_threshold),
                    "c_ratio_target": _pct_str(cfg.salary_ratio_target),
                }
                payroll = {
                    "p_salary": (f"{cfg.salary_basic_allowance_cents / 100:.2f}"
                                 if cfg.salary_basic_allowance_cents
                                 is not None else ""),
                    "p_overtime": (f"{cfg.overtime_cents / 100:.2f}"
                                   if cfg.overtime_cents is not None else ""),
                    "p_support": (f"{cfg.support_team_cost_cents / 100:.2f}"
                                  if cfg.support_team_cost_cents is not None
                                  else ""),
                } if can_payroll else {}
                values.update(staffing)
                values.update(payroll)
            except Exception:
                pass
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
            self.cfg_error = "Your session has expired. Please sign in again."
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
        except AuthorizationError:
            self.cfg_error = "You do not have access to this store."
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
        except AuthorizationError:
            self.cfg_saving = False
            self.cfg_error = (
                "Only super admins and administrators can manage monthly "
                "configuration and payroll.")
            return
        except ValidationError as exc:
            self.cfg_saving = False
            self.cfg_error = str(exc)
            return
        except (RepositoryError, StoreNotFoundError) as exc:
            self.cfg_saving = False
            self.cfg_error = f"Save failed: {exc}"
            return
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
            fields[col] = _parse_money(raw) if raw.strip() else None
        return fields
