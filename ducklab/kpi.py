"""Pure-Python KPI calculation engine for Store Operations Monitoring.

Reproduces the business rules of Store_Operations_Monitoring_v4.xlsx.
No database access, no UI, no system clock calls (except where an explicit
date is passed in). All functions are deterministic and side-effect free.

Conventions
-----------
- Money inputs are INTEGER cents (matching store_ops_db storage). Monetary
  results are returned as Decimal RM unless suffixed ``_cents``.
- REAL factors from SQLite (percentages, PT factor) must pass through
  ``to_decimal`` — never ``Decimal(float)``.
- None means "not reported / awaiting input" and is never coerced to zero.
  An explicit 0 is a legitimate reported value (blank-vs-zero rule).
- Stock-loss amounts follow the workbook's blank-means-zero convention:
  absent codes contribute 0 to totals.
- Division-by-zero / missing inputs return None (mirrors Excel "" results).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

# --------------------------------------------------------------------------
# Conversion boundaries
# --------------------------------------------------------------------------

def to_decimal(value) -> Optional[Decimal]:
    """Decimal-safe conversion of SQLite REAL / int / str factors.

    Floats are routed through repr() (shortest round-trip) so that the stored
    REAL 0.06818 becomes Decimal('0.06818'), not its binary expansion.
    """
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, float):
        return Decimal(repr(value))
    return Decimal(value)


def cents_to_rm(cents: Optional[int]) -> Optional[Decimal]:
    """INTEGER cents -> Decimal RM. None preserved."""
    if cents is None:
        return None
    return Decimal(cents) / 100


def ratio(num_cents: Optional[int], den_cents: Optional[int]) -> Optional[Decimal]:
    """Money/money ratio with zero/missing-denominator guard."""
    if num_cents is None or den_cents is None or den_cents == 0:
        return None
    return Decimal(num_cents) / Decimal(den_cents)


# --------------------------------------------------------------------------
# Input records (typed, storage-aligned)
# --------------------------------------------------------------------------

@dataclass
class DailyInput:
    """One row of the Daily sheet. None = not reported."""
    date: date
    sales_cents: Optional[int] = None
    transactions: Optional[int] = None
    units: Optional[int] = None
    resigned_ft: Optional[int] = None
    new_hires_ft: Optional[int] = None
    ft_working: Optional[int] = None
    pt_working: Optional[int] = None
    leave_annual: Optional[int] = None
    leave_mc: Optional[int] = None
    leave_other_paid: Optional[int] = None
    leave_unpaid: Optional[int] = None
    stock_losses: dict[str, int] = field(default_factory=dict)  # code -> cents


@dataclass
class ExpenseInput:
    """One row of the Expense Register."""
    incurred_date: Optional[date]
    paid_date: Optional[date]
    category_id: Optional[int]
    description: Optional[str]
    amount_cents: Optional[int]
    paid_by: Optional[str]
    status: Optional[str]          # Pending | Paid | Cancelled
    verification: Optional[str]    # Unverified | Verified | Disputed
    recorded_by: Optional[str]
    store_id: int = 0


@dataclass
class MonthConfig:
    """Setup sheet values for one store/month."""
    month: str                              # 'YYYY-MM'
    min_rtm_target_cents: Optional[int] = None
    level1_base_cents: Optional[int] = None
    level2_pct: Optional[Decimal] = None
    level3_pct: Optional[Decimal] = None
    level4_pct: Optional[Decimal] = None
    sales_last_year_cents: Optional[int] = None
    sales_last_month_cents: Optional[int] = None
    salary_ratio_target: Optional[Decimal] = None
    budget_headcount: Optional[int] = None
    turnover_threshold: Optional[Decimal] = None
    stock_loss_target: Optional[Decimal] = None
    opening_headcount_ft: Optional[int] = None
    part_timer_factor: Optional[Decimal] = None
    salary_basic_allowance_cents: Optional[int] = None   # None = awaiting payroll
    overtime_cents: Optional[int] = None
    support_team_cost_cents: Optional[int] = None


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def month_bounds(month: str) -> tuple[date, date, int]:
    """'YYYY-MM' -> (first day, last day, days in month). Real calendar length."""
    y, m = int(month[:4]), int(month[5:7])
    days = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, days), days


def _in_month(d: Optional[date], month: str) -> bool:
    return d is not None and d.strftime("%Y-%m") == month


# --------------------------------------------------------------------------
# 4. SALES PERFORMANCE
# --------------------------------------------------------------------------

def days_with_sales_entered(days: list[DailyInput]) -> int:
    """Summary!B14 = COUNT(Daily!C7:C37). Blank (None) not counted; 0 counted."""
    return sum(1 for d in days if d.sales_cents is not None)


def sales_mtd_cents(days: list[DailyInput]) -> int:
    """Summary!B15 = SUM(Daily!C7:C37) — blank days contribute nothing."""
    return sum(d.sales_cents or 0 for d in days)


def sales_target_levels_cents(cfg: MonthConfig) -> dict[str, Optional[int]]:
    """Setup!B21-B24. Level 4 = MAX(min RTM target, base x (1+level4 pct))."""
    base = cfg.level1_base_cents
    if base is None:
        return {"level1": None, "level2": None, "level3": None, "level4": None}
    b = Decimal(base)
    l2 = b * (1 + (cfg.level2_pct or 0))
    l3 = b * (1 + (cfg.level3_pct or 0))
    l4_raw = b * (1 + (cfg.level4_pct or 0))
    floor = Decimal(cfg.min_rtm_target_cents or 0)
    return {
        "level1": base,
        "level2": l2,
        "level3": l3,
        "level4": max(floor, l4_raw),
    }


def month_end_sales_cents(days: list[DailyInput], days_in_month: int) -> Optional[Decimal]:
    """Summary!B16: actual if complete, else projected = MTD / entered x days.

    The workbook applies NO rounding to the projection (float arithmetic);
    we return an exact Decimal without quantizing — rounding is a
    presentation concern. Returns None when no days entered (workbook: "").
    Complete months return the exact integer MTD (as Decimal cents).
    """
    entered = days_with_sales_entered(days)
    if entered == 0 or days_in_month == 0:
        return None
    mtd = sales_mtd_cents(days)
    if entered >= days_in_month:
        return Decimal(mtd)
    return Decimal(mtd) / entered * days_in_month


def achievement(actual_cents: Optional[int], target_cents) -> Optional[Decimal]:
    """Summary!D17-D20: actual / target. None when target missing/zero."""
    if actual_cents is None or target_cents is None:
        return None
    t = Decimal(int(target_cents)) if not isinstance(target_cents, Decimal) else target_cents
    if t == 0:
        return None
    return Decimal(actual_cents) / t


def sales_status(actual_cents: Optional[int], target_cents) -> str:
    """Summary!E17-E20: Awaiting data / No target / Achieved / Below target."""
    if actual_cents is None:
        return "Awaiting data"
    if target_cents is None or Decimal(int(target_cents)) == 0:
        return "No target"
    return "Achieved" if Decimal(actual_cents) >= Decimal(int(target_cents)) else "Below target"


def growth(actual_cents: Optional[int], reference_cents: Optional[int]) -> Optional[Decimal]:
    """Summary!D21/D22: actual/ref - 1 (growth vs last year / last month)."""
    if actual_cents is None or not reference_cents:
        return None
    return Decimal(actual_cents) / Decimal(reference_cents) - 1


def required_daily_run_rate(target_cents: Optional[int], days_in_month: int) -> Optional[Decimal]:
    """Daily Dashboard!E8: Level 1 budget / days in month."""
    if target_cents is None or days_in_month == 0:
        return None
    return Decimal(target_cents) / days_in_month


def sales_needed_per_remaining_day(
    target_cents: Optional[int], mtd_cents: int, entered: int, days_in_month: int
) -> Optional[Decimal]:
    """Daily Dashboard!H8: MAX(0, (L1 - MTD) / (days - entered)); "" when complete."""
    if entered == 0 or days_in_month == 0 or entered >= days_in_month or target_cents is None:
        return None
    remaining = Decimal(target_cents) - mtd_cents
    return max(Decimal(0), remaining / (days_in_month - entered))


def spr_cents(sales_cents: int, transactions: int) -> Optional[Decimal]:
    """Sales per receipt = sales / transactions (Summary!B25)."""
    if transactions == 0:
        return None
    return Decimal(sales_cents) / transactions


def upt(units: int, transactions: int) -> Optional[Decimal]:
    """Units per transaction (Summary!B26)."""
    if transactions == 0:
        return None
    return Decimal(units) / transactions


def asp_cents(sales_cents: int, units: int) -> Optional[Decimal]:
    """Average selling price = sales / units (Summary!B27)."""
    if units == 0:
        return None
    return Decimal(sales_cents) / units


# --------------------------------------------------------------------------
# 5. EXPENSES MANAGEMENT
# --------------------------------------------------------------------------

# Register column L (Expense Register!L9) — exact precedence ladder.
def expense_validation(e: ExpenseInput, known_category_ids: set[int]) -> str:
    if (
        e.incurred_date is None
        or e.category_id is None
        or not e.description
        or e.amount_cents is None
        or e.status is None
    ):
        return "Incomplete"
    if e.category_id not in known_category_ids:
        return "Unknown category"
    if e.status == "Paid" and e.paid_date is None:
        return "Missing paid date"
    if e.status != "Paid" and e.paid_date is not None:
        return "Check paid date"
    if e.status == "Paid" and not e.paid_by:
        return "Missing paid by"
    if not e.recorded_by:
        return "Missing recorder"
    if e.verification == "Disputed":
        return "Disputed"
    if e.verification != "Verified":
        return "Unverified"
    return "OK"


def _is_core_valid(e: ExpenseInput) -> bool:
    """Shared base of Register columns M/N/O: dates/category/desc/amount valid."""
    return (
        e.incurred_date is not None
        and e.category_id is not None
        and bool(e.description)
        and e.amount_cents is not None
    )


def paid_value_cents(e: ExpenseInput) -> int:
    """Register column M: counts only when Paid with valid paid date + core fields."""
    if e.status == "Paid" and e.paid_date is not None and _is_core_valid(e):
        return e.amount_cents
    return 0


def pending_value_cents(e: ExpenseInput) -> int:
    """Register column N: Pending with valid core fields."""
    if e.status == "Pending" and _is_core_valid(e):
        return e.amount_cents
    return 0


def incurred_value_cents(e: ExpenseInput) -> int:
    """Register column O: any non-Cancelled, non-blank status with valid core."""
    if e.status not in (None, "Cancelled") and _is_core_valid(e):
        return e.amount_cents
    return 0


def expense_totals_cents(
    expenses: list[ExpenseInput], month: str, store_id: int
) -> dict[str, int]:
    """Month aggregates.

    - paid:     sum of paid values with PAID date inside month (HQ rule).
    - incurred: sum of incurred values with INCURRED date inside month.
    - open:     sum of pending values (this store's register only).
    Rows from other stores are excluded regardless of input.
    """
    paid = incurred = open_ = 0
    for e in expenses:
        if e.store_id != store_id:
            continue
        pv = paid_value_cents(e)
        if pv and _in_month(e.paid_date, month):
            paid += pv
        iv = incurred_value_cents(e)
        if iv and _in_month(e.incurred_date, month):
            incurred += iv
        open_ += pending_value_cents(e)
    return {"paid": paid, "incurred": incurred, "open": open_}


def category_paid_mtd_cents(
    expenses: list[ExpenseInput], month: str, store_id: int, category_id: int
) -> int:
    """Expense Monitor!C15-24: paid-date-basis SUMIFS per category."""
    total = 0
    for e in expenses:
        if e.store_id != store_id or e.category_id != category_id:
            continue
        pv = paid_value_cents(e)
        if pv and _in_month(e.paid_date, month):
            total += pv
    return total


def budget_status(paid_cents: int, budget_cents: Optional[int]) -> str:
    """Expense Monitor!H15-24: No budget / OVER BUDGET / Watch (>=80%) / Within."""
    if budget_cents is None:
        return "No budget"
    if paid_cents > budget_cents:
        return "OVER BUDGET"
    if budget_cents > 0 and Decimal(paid_cents) / Decimal(budget_cents) >= Decimal("0.8"):
        return "Watch"
    return "Within budget"


def expense_share_of_sales(paid_cents: int, sales_cents: Optional[int]) -> Optional[Decimal]:
    """Summary!D30-40: expense line as % of sales."""
    if not sales_cents:
        return None
    return Decimal(paid_cents) / Decimal(sales_cents)


# --------------------------------------------------------------------------
# 6. MANPOWER EFFICIENCY
# --------------------------------------------------------------------------

def effective_staff(ft_working: Optional[int], pt_working: Optional[int],
                    pt_factor) -> Optional[Decimal]:
    """Daily!AP: FT + PT x factor. None when BOTH attendance fields blank."""
    if ft_working is None and pt_working is None:
        return None
    f = to_decimal(pt_factor) or Decimal(0)
    return Decimal(ft_working or 0) + Decimal(pt_working or 0) * f


def closing_headcount(opening: Optional[int], hires: int, resigned: int) -> Optional[int]:
    """Summary!B50 = opening + hires - resignations."""
    if opening is None:
        return None
    return opening + hires - resigned


def headcount_variance_status(closing: Optional[int], budget: Optional[int]) -> tuple[Optional[int], str]:
    """Summary!D51/E51: (variance, Vacancy | Over budget | On budget | ...)."""
    if closing is None:
        return None, "Awaiting HC"
    if not budget:
        return None, "No target"
    var = closing - budget
    if var < 0:
        return var, "Vacancy"
    if var > 0:
        return var, "Over budget"
    return var, "On budget"


def total_salary_cents(cfg: MonthConfig) -> Optional[int]:
    """Summary!B43 = basic+allowance + overtime (N() treats blank as 0)."""
    if cfg.salary_basic_allowance_cents is None:
        return None  # awaiting payroll
    return cfg.salary_basic_allowance_cents + (cfg.overtime_cents or 0)


def salary_ratio(cfg: MonthConfig, sales_cents: Optional[int],
                 include_support: bool = False) -> Optional[Decimal]:
    """Summary!B44/B45: salary (opt. + support team) / sales. None = awaiting."""
    salary = total_salary_cents(cfg)
    if salary is None or not sales_cents:
        return None
    if include_support:
        salary += cfg.support_team_cost_cents or 0
    return Decimal(salary) / Decimal(sales_cents)


def salary_ratio_status(cfg: MonthConfig, sales_cents: Optional[int],
                        include_support: bool = False) -> str:
    """Summary!E44/E45: Awaiting payroll / Awaiting data / Within target / Failure."""
    if cfg.salary_basic_allowance_cents is None:
        return "Awaiting payroll"
    r = salary_ratio(cfg, sales_cents, include_support)
    if r is None or cfg.salary_ratio_target is None:
        return "Awaiting data"
    return "Within target" if r <= cfg.salary_ratio_target else "Failure: over target"


def salary_budget_cents(sales_cents: Optional[int], ratio_target) -> Optional[Decimal]:
    """Setup!B43: actual sales x salary ratio target, in cents, unrounded.

    The workbook computes an unrounded float product; we return the exact
    Decimal product in cents (no quantization invented).
    """
    t = to_decimal(ratio_target)
    if sales_cents is None or t is None:
        return None
    return Decimal(sales_cents) * t


def turnover_pct(resigned: int, opening: Optional[int]) -> Optional[Decimal]:
    """Summary!B53 = resignations / opening headcount."""
    if not opening:
        return None
    return Decimal(resigned) / Decimal(opening)


def turnover_status(resigned: int, opening: Optional[int], threshold) -> str:
    """Summary!E53: Awaiting HC / HIGH TURNOVER / Normal."""
    t = turnover_pct(resigned, opening)
    th = to_decimal(threshold)
    if t is None:
        return "Awaiting HC"
    if th is not None and t > th:
        return "HIGH TURNOVER"
    return "Normal"


def average_effective_staff(days: list[DailyInput], pt_factor) -> Optional[Decimal]:
    """Summary!B52: staff-days / days with staffing entered."""
    effs = [effective_staff(d.ft_working, d.pt_working, pt_factor) for d in days]
    reported = [e for e in effs if e is not None]
    if not reported:
        return None
    return sum(reported) / len(reported)


def sales_per_staff_cents(days: list[DailyInput], pt_factor) -> Optional[Decimal]:
    """Summary!B54/Daily!AQ38: sales on staffed days / total effective staff-days."""
    total_sales = 0
    total_staff = Decimal(0)
    for d in days:
        e = effective_staff(d.ft_working, d.pt_working, pt_factor)
        if e is not None and e > 0:
            total_staff += e
            total_sales += d.sales_cents or 0
    if total_staff == 0:
        return None
    return Decimal(total_sales) / total_staff


def leave_days(days: list[DailyInput]) -> int:
    """Summary!B55: SUM of annual + MC + other paid + unpaid leave (blank = 0)."""
    return sum(
        (d.leave_annual or 0) + (d.leave_mc or 0)
        + (d.leave_other_paid or 0) + (d.leave_unpaid or 0)
        for d in days
    )


# --------------------------------------------------------------------------
# 7. STOCK MANAGEMENT
# --------------------------------------------------------------------------

def total_loss_cents(stock_losses: dict[str, int],
                     signs: dict[str, int]) -> int:
    """Total Loss = DMO + DMC + DMP + Missing - Extra (blank code = 0).

    ``signs`` comes from stock_loss_codes.contribution_sign; informational
    codes (sign 0) are excluded.
    """
    total = 0
    for code, cents in stock_losses.items():
        sign = signs.get(code, 0)
        total += sign * cents
    return total


def monthly_stock_totals(days: list[DailyInput]) -> dict[str, int]:
    """Monthly totals per stock code (Summary!B58-B72)."""
    totals: dict[str, int] = {}
    for d in days:
        for code, cents in d.stock_losses.items():
            totals[code] = totals.get(code, 0) + cents
    return totals


def stock_loss_pct(loss_cents: int, sales_cents: Optional[int]) -> Optional[Decimal]:
    """Summary!D63: Total Loss / sales."""
    if not sales_cents:
        return None
    return Decimal(loss_cents) / Decimal(sales_cents)


def stock_loss_status(loss_cents: int, sales_cents: Optional[int], target) -> str:
    """Summary!E63: Within target / Over target (blank if no target/data)."""
    t = to_decimal(target)
    p = stock_loss_pct(loss_cents, sales_cents)
    if t is None or p is None:
        return ""
    return "Within target" if p <= t else "Over target"


# --------------------------------------------------------------------------
# 8. ACTION TRACKER
# --------------------------------------------------------------------------

@dataclass
class ActionInput:
    status: Optional[str]      # Open | In progress | Blocked | Done | Cancelled
    due_date: Optional[date] = None
    pillar: Optional[str] = None


def action_counts(actions: list[ActionInput], as_of: date) -> dict[str, int]:
    """Action Tracker!B4/E4/H4. ``as_of`` is explicit — no system clock.

    Overdue (E4): due < as_of, due set, status not Done/Cancelled, pillar set.
    """
    counts = {"open": 0, "in_progress": 0, "blocked": 0, "done": 0,
              "cancelled": 0, "overdue": 0, "total": 0}
    for a in actions:
        counts["total"] += 1
        key = (a.status or "").lower().replace(" ", "_")
        if key in ("open", "in_progress", "blocked", "done", "cancelled"):
            counts[key] += 1
        if (
            a.due_date is not None
            and a.due_date < as_of
            and a.status not in ("Done", "Cancelled")
            and a.pillar
        ):
            counts["overdue"] += 1
    return counts


def open_actions(counts: dict[str, int]) -> int:
    """Action Tracker!B4 = Open + In progress + Blocked."""
    return counts["open"] + counts["in_progress"] + counts["blocked"]


def completion_pct(counts: dict[str, int]) -> Optional[Decimal]:
    """Done / total. None when there are no actions."""
    if counts["total"] == 0:
        return None
    return Decimal(counts["done"]) / counts["total"]
