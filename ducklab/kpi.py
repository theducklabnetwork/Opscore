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
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Optional
from zoneinfo import ZoneInfo

KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")

# Status names returned by this engine. Callers compare against these
# constants, never against retyped string literals. Values are the
# workbook strings (stored in snapshots; keys of design.STATUS_COLOR).
SALES_ACHIEVED = "Achieved"
SALES_BELOW = "Below target"
TOTAL_OVER_BUDGET = "Over budget"          # Summary!E40
CATEGORY_OVER_BUDGET = "OVER BUDGET"       # Expense Monitor!H15-25
SALARY_AWAITING_PAYROLL = "Awaiting payroll"
SALARY_AWAITING_DATA = "Awaiting data"
SALARY_WITHIN = "Within target"
SALARY_FAILURE = "Failure: over target"
TURNOVER_HIGH = "HIGH TURNOVER"
STOCK_WITHIN = "Within target"
STOCK_OVER_TARGET = "Over target"
# Stock display states before the workbook status (Summary!E63) applies.
STOCK_AWAITING_DATA = "Awaiting data"
STOCK_NO_SALES = "No sales recorded"
STOCK_NO_TARGET = "No target"
# Part-timers worked but no conversion factor is configured.
PT_FACTOR_NOT_SET = "Factor not set"


def kl_today() -> date:
    """One "today" for the whole app: Asia/Kuala_Lumpur."""
    return datetime.now(KL_TZ).date()


def reporting_date(month: str, today: Optional[date] = None) -> date:
    """Workbook Expense Monitor!B4: max(month start, min(today, month end)).

    For a past month: month end. For the current month: today. For a future
    month: month start (never before the month exists).
    """
    start, end, _ = month_bounds(month)
    t = kl_today() if today is None else today
    return max(start, min(t, end))

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
    # Daily Sales basket targets (monthly transactions; SPR in cents)
    target_transactions: Optional[int] = None
    target_spr_cents: Optional[int] = None
    target_ipr: Optional[Decimal] = None


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------

def month_bounds(month: str) -> tuple[date, date, int]:
    """'YYYY-MM' -> (first day, last day, days in month). Real calendar length."""
    y, m = int(month[:4]), int(month[5:7])
    days = calendar.monthrange(y, m)[1]
    return date(y, m, 1), date(y, m, days), days


# Input bounds: anything larger is a typo and would overflow SQLite INTEGER.
MAX_AMOUNT_RM = Decimal("100000000.00")   # RM 100,000,000.00
MAX_COUNT = 1_000_000


def parse_money_cents(raw: str) -> int:
    """Shared money parser for all data-entry pages: '350819' or
    '350,819.00' -> integer cents. Rejects sub-cent input, inf, nan and
    amounts above RM 100,000,000.00 ("Amount too large").
    Negative amounts are allowed here (refunds are valid money); callers
    that forbid negatives say so themselves. Raises ValueError."""
    cleaned = raw.replace(",", "").replace("RM", "").strip()
    try:
        dec = Decimal(cleaned)
    except InvalidOperation:
        raise ValueError(f"Invalid amount: {raw!r}")
    if not dec.is_finite():
        raise ValueError(f"Invalid amount: {raw!r}")
    # copy_abs() is exact (no context), so huge exponents cannot overflow
    if dec.copy_abs() > MAX_AMOUNT_RM:
        raise ValueError("Amount too large")
    cents = int(dec * 100)
    if Decimal(cents) != dec * 100:
        raise ValueError(f"Amount has sub-cent precision: {raw!r}")
    return cents


def parse_count(raw: str, label: str) -> int:
    """Whole, non-negative number. Raises ValueError with ``label``."""
    try:
        value = int(raw)
    except ValueError:
        raise ValueError(f"{label} must be a whole number")
    if value < 0:
        raise ValueError(f"{label} cannot be negative")
    if value > MAX_COUNT:
        raise ValueError(f"{label} is too large")
    return value


def parse_decimal(raw: str, label: str, lo: Decimal, hi: Decimal) -> Decimal:
    """Finite decimal within [lo, hi] (factors, percentages). "NaN" and
    "inf" are rejected like any non-number. Raises ValueError."""
    try:
        d = Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError(f"{label} must be a number")
    if not d.is_finite():
        raise ValueError(f"{label} must be a number")
    if not (lo <= d <= hi):
        raise ValueError(f"{label} must be between {lo} and {hi}")
    return d


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


def projection_state(days_entered: int, days_in_month: int) -> str:
    """Display rule (the workbook projection itself is unchanged):
    "actual" (month complete), "projected", or "early_estimate"
    (projected AND fewer than 7 days entered)."""
    if days_in_month <= 0:
        return "projected"
    if days_entered >= days_in_month:
        return "actual"
    return "projected" if days_entered >= 7 else "early_estimate"


def growth_status(growth: Optional[Decimal], days_entered: int) -> str:
    """Summary rows 21-22 status: Awaiting data (nothing entered),
    No reference (reference month figure missing), else Growth/Decline."""
    if days_entered == 0:
        return "Awaiting data"
    if growth is None:
        return "No reference"
    return "Growth" if growth >= 0 else "Decline"


def target_gap_cents(actual_cents, target_cents) -> Optional[Decimal]:
    """RM gap vs a target level (actual minus target); None when either
    side is missing. Exact Decimal — presentation rounds."""
    if actual_cents is None or target_cents is None:
        return None
    return to_decimal(actual_cents) - to_decimal(target_cents)


def average_per_reported_day(total_cents: int, days_entered: int) -> Optional[Decimal]:
    """MTD total / days with sales entered. None when none entered."""
    if not days_entered:
        return None
    return Decimal(total_cents) / Decimal(days_entered)


def daily_vs_run_rate_cents(sales_cents: Optional[int],
                            run_rate) -> Optional[Decimal]:
    """One day's sales minus the required daily run-rate (exact)."""
    if sales_cents is None or run_rate is None:
        return None
    return Decimal(sales_cents) - to_decimal(run_rate)


def pace_series(days: list[DailyInput], days_in_month: int,
                level1_cents) -> list[dict]:
    """Cumulative sales pace vs the Level 1 budget pace, one entry per
    calendar day: {"day", "actual", "budget"} in integer cents.

    actual: running MTD on reported days, None after the last reported day
    (no extension). budget: run-rate x day rounded half-up to the cent;
    None when no Level 1 target."""
    rate = required_daily_run_rate(level1_cents, days_in_month)
    entered = {d.date.day: d.sales_cents for d in days
               if d.sales_cents is not None}
    last_reported = max(entered, default=0)
    out, running = [], 0
    for day in range(1, days_in_month + 1):
        running += entered.get(day, 0)
        out.append({
            "day": day,
            "actual": running if day <= last_reported else None,
            "budget": (int((rate * day).quantize(Decimal(1), ROUND_HALF_UP))
                       if rate is not None else None),
        })
    return out


def level_pace_lines(targets: dict,
                     days_in_month: int) -> list[tuple[int, list[int]]]:
    """(level, cumulative budget pace per day in cents) for every distinct
    configured level (distinct_level_targets) — the same run-rate x day
    rule as pace_series' Level 1 budget."""
    return [(level, [p["budget"] for p in pace_series([], days_in_month, t)])
            for level, t in distinct_level_targets(targets)]


def reported_total(values) -> Optional[int]:
    """SUM of reported values; None when nothing was reported (blank is
    not zero)."""
    reported = [v for v in values if v is not None]
    return sum(reported) if reported else None


def budget_control_status(paid_cents: int, total_budget_cents: Optional[int]) -> str:
    """Expense Monitor total row H25: No budget / OVER BUDGET /
    Watch (paid >= 80% of budget) / Within budget."""
    return budget_status(paid_cents, total_budget_cents)


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
    if target_cents is None or to_decimal(target_cents) == 0:
        return "No target"
    # Compare as Decimal — no int() truncation: 1051 vs 1051.05 is
    # "Below target" (workbook float semantics, kept exact).
    return (SALES_ACHIEVED
            if to_decimal(actual_cents) >= to_decimal(target_cents)
            else SALES_BELOW)


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


def level_names(targets: dict) -> list[str]:
    """'level1'..'levelN' keys of a targets dict, in level order."""
    return sorted((k for k in targets if k.startswith("level")),
                  key=lambda k: int(k[5:]))


def highest_level_achieved(
    month_end_cents, targets: dict
) -> tuple[Optional[int], Optional[int], Optional[Decimal]]:
    """-> (highest level reached, next level, gap to next level in cents).

    A level is reached when its sales_status is Achieved (same rule as the
    target ladder; Level 4 keeps its min-RTM floor from
    sales_target_levels_cents). ``targets`` is that dict, or a subset of
    it (e.g. Level 1 only when no Level 2-4 uplift is set).
    level: None when no level is reached. next: None when the top level is
    reached or there is no target. gap: next target minus month-end
    (exact, positive = short); None when either side is missing.
    September 2026: 350,819 vs L4 350,819.30 -> (3, 4, Decimal('30')).
    """
    names = level_names(targets)
    if not names or targets[names[0]] is None:
        return None, None, None
    reached = None
    for name in names:
        if sales_status(month_end_cents, targets[name]) == SALES_ACHIEVED:
            reached = int(name[5:])
    later = [n for n in names if reached is None or int(n[5:]) > reached]
    if not later:
        return reached, None, None
    nxt = later[0]
    gap = target_gap_cents(targets[nxt], month_end_cents)
    return reached, int(nxt[5:]), gap


def needed_per_remaining_day_for_level(
    targets: dict, level: Optional[int], mtd_cents: int, entered: int,
    days_in_month: int,
) -> Optional[Decimal]:
    """Daily Dashboard!H8 for any level: MAX(0, (level target - MTD) /
    (days - entered)); None when complete, nothing entered, no such level
    or no target (same formula as sales_needed_per_remaining_day)."""
    if level is None:
        return None
    return sales_needed_per_remaining_day(
        targets.get(f"level{level}"), mtd_cents, entered, days_in_month)


def levels_missing(cfg: MonthConfig) -> bool:
    """True for a month saved before four levels were required: a Level 1
    base target but a blank Level 2/3/4 uplift. The workbook rule still
    applies (blank uplift = base x (1 + 0)); pages flag the month."""
    return cfg.level1_base_cents is not None and any(
        p is None for p in (cfg.level2_pct, cfg.level3_pct, cfg.level4_pct))


def drawn_level(targets: dict, level: int) -> int:
    """The pace-chart line that shows ``level``: itself, or the lower
    level with the same target when it is not drawn twice
    (distinct_level_targets)."""
    t = to_decimal(targets.get(f"level{level}"))
    for n, seen in distinct_level_targets(targets):
        if seen == t:
            return n
    return 1


def distinct_level_targets(targets: dict) -> list[tuple[int, Decimal]]:
    """(level, target) for each set level whose target differs from every
    lower level's — a level without an uplift (equal to Level 1) is not
    listed twice (pace chart lines)."""
    out: list[tuple[int, Decimal]] = []
    for name in level_names(targets):
        t = to_decimal(targets[name])
        if t is not None and all(t != seen for _, seen in out):
            out.append((int(name[5:]), t))
    return out


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
# 4b. DAILY TARGETS (Daily Sales Contribution)
# --------------------------------------------------------------------------

# Contribution day keys: weekday (date.weekday() order) or public holiday.
DSC_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
DSC_PH = "PH"
RESULT_ACHIEVED = "Achieved"
RESULT_NOT_ACHIEVED = "Not Achieved"


@dataclass
class DailyTarget:
    """One day of the Daily Sales table. Money in integer cents."""
    date: date
    public_holiday: bool
    share: Decimal
    target_cents: int
    adjusted_cents: Optional[int]     # None: unreported (not the next day)
    result: Optional[str]             # None: unreported


def day_share(day: date, shares: dict[str, Decimal],
              holidays) -> Optional[Decimal]:
    """The PH share on a public holiday when one exists, else the weekday
    share. None when the share is missing."""
    if day in holidays and shares.get(DSC_PH) is not None:
        return to_decimal(shares[DSC_PH])
    return to_decimal(shares.get(DSC_WEEKDAYS[day.weekday()]))


def daily_targets(month: str, level1_cents: Optional[int],
                  shares: dict[str, Decimal], holidays,
                  sales_by_day: dict[date, Optional[int]]
                  ) -> Optional[list[DailyTarget]]:
    """Daily Target = Level 1 x day share / sum of the month's day shares,
    rounded half-up to the cent (the figure shown is the one compared).

    Adjusted Target = Daily Target + shortfall carried from earlier
    reported days (their cumulative Daily Target - cumulative sales, when
    > 0); a surplus is never subtracted. Unreported days (no sales) are
    left out of the carry-forward: neither their target nor their sales
    count. Shown on reported days and on the next day after the last
    reported one; Result ("Achieved" when sales >= Adjusted Target) only
    on reported days.

    None when there is no Level 1 target or a day has no share (no
    State/Concept match) — the table then shows "—".
    """
    if level1_cents is None:
        return None
    start, _, dim = month_bounds(month)
    days = [date(start.year, start.month, n) for n in range(1, dim + 1)]
    day_shares = [day_share(d, shares, holidays) for d in days]
    if any(s is None for s in day_shares):
        return None
    total = sum(day_shares)
    if total <= 0:
        return None
    reported = [d for d in days if sales_by_day.get(d) is not None]
    next_day = (reported[-1].toordinal() + 1) if reported else days[0].toordinal()
    out, cum_target, cum_sales = [], 0, 0
    for d, share in zip(days, day_shares):
        target = int((Decimal(level1_cents) * share / total)
                     .quantize(Decimal(1), ROUND_HALF_UP))
        adjusted = target + max(0, cum_target - cum_sales)
        sales = sales_by_day.get(d)
        result = None
        if sales is not None:
            result = (RESULT_ACHIEVED if sales >= adjusted
                      else RESULT_NOT_ACHIEVED)
        show = sales is not None or d.toordinal() == next_day
        out.append(DailyTarget(d, d in holidays, share, target,
                               adjusted if show else None, result))
        if sales is not None:  # only reported days build the shortfall
            cum_target += target
            cum_sales += sales
    return out


def pct_vs_target(actual, target) -> Optional[Decimal]:
    """actual / target - 1 (SPR %, IPR %). None when either is missing or
    the target is zero."""
    a, t = to_decimal(actual), to_decimal(target)
    if a is None or not t:
        return None
    return a / t - 1


def transactions_pct(transactions: Optional[int],
                     target_transactions: Optional[int],
                     days_in_month: int) -> Optional[Decimal]:
    """Transactions / (monthly Target Transactions / days in month) - 1."""
    if target_transactions is None or not days_in_month:
        return None
    return pct_vs_target(transactions,
                         Decimal(target_transactions) / days_in_month)


def average_sales_by_weekday(days: list[DailyInput], holidays
                             ) -> list[Optional[Decimal]]:
    """Average sales (cents) per weekday, Mon..Sun, over reported days only
    (an explicit zero counts); public holidays are left out. None for a
    weekday with no reported day."""
    totals, counts = [0] * 7, [0] * 7
    for d in days:
        if d.sales_cents is None or d.date in holidays:
            continue
        totals[d.date.weekday()] += d.sales_cents
        counts[d.date.weekday()] += 1
    return [Decimal(t) / c if c else None for t, c in zip(totals, counts)]


def month_weeks(month: str) -> list[list[Optional[date]]]:
    """Calendar grid for the month: one row per week (Mon..Sun), days
    outside the month None."""
    start, _, dim = month_bounds(month)
    cells: list[Optional[date]] = [None] * start.weekday()
    cells += [date(start.year, start.month, n) for n in range(1, dim + 1)]
    cells += [None] * (-len(cells) % 7)
    return [cells[i:i + 7] for i in range(0, len(cells), 7)]


def benchmark_spr_cents(avg_spr_rm) -> Optional[Decimal]:
    """Imported State average SPR (RM) -> cents; None when blank."""
    value = to_decimal(avg_spr_rm)
    return None if value is None else value * 100


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


def incurred_count_in_month(
    expenses: list[ExpenseInput], month: str, store_id: int
) -> int:
    """COUNT of non-cancelled, core-valid lines INCURRED in the month
    (same per-line rule as Register column O, counted not summed)."""
    n = 0
    for e in expenses:
        if e.store_id != store_id:
            continue
        if e.status not in (None, "Cancelled") and _is_core_valid(e) \
                and _in_month(e.incurred_date, month):
            n += 1
    return n


def _pending_in_scope(e: ExpenseInput, month_end: date) -> bool:
    """Pending lines belong to a month when incurred on or before its end
    (same cut-off as expense_review_counts)."""
    return e.incurred_date is not None and e.incurred_date <= month_end


def pending_count(
    expenses: list[ExpenseInput], month: str, store_id: int
) -> int:
    """COUNT of Pending, core-valid lines on this store's register incurred
    on or before month end (same per-line rule as Register column N)."""
    _, month_end, _ = month_bounds(month)
    return sum(1 for e in expenses
               if e.store_id == store_id and e.status == "Pending"
               and _is_core_valid(e)
               and _pending_in_scope(e, month_end))


def expense_review_counts(
    expenses: list[ExpenseInput], month: str, store_id: int,
    known_category_ids: set[int],
) -> dict[str, int]:
    """Expense lines needing review, month-scoped (one rule for the
    Expenses page, Overview and finalization warnings):
    lines INCURRED or PAID in the month, plus PENDING lines incurred on
    or before month end (they still need attention for this month).
    """
    _, month_end, _ = month_bounds(month)
    counts: dict[str, int] = {}
    for e in expenses:
        if e.store_id != store_id:
            continue
        in_scope = (
            _in_month(e.incurred_date, month)
            or _in_month(e.paid_date, month)
            or (e.status == "Pending" and e.incurred_date is not None
                and e.incurred_date <= month_end)
        )
        if not in_scope:
            continue
        status = expense_validation(e, known_category_ids)
        counts[status] = counts.get(status, 0) + 1
    return counts


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


def paid_count_in_month(
    expenses: list[ExpenseInput], month: str, store_id: int
) -> int:
    """COUNT of Paid transactions with a paid date inside the month.

    Count-based (not sum-based): signed amounts can net to RM 0 even when
    payments exist. Used by Summary!E40.
    """
    count = 0
    for e in expenses:
        if e.store_id != store_id:
            continue
        if e.status == "Paid" and e.paid_date is not None \
                and _is_core_valid(e) and _in_month(e.paid_date, month):
            count += 1
    return count


def total_expenses_status(
    expenses: list[ExpenseInput], month: str, store_id: int,
    paid_cents: int, total_budget_cents: Optional[int],
) -> str:
    """Summary!E40: No payments yet / No budget / Within budget / Over budget.

    "No payments yet" is decided by the COUNT of Paid transactions with a
    paid date in the month (not the paid total).
    """
    if paid_count_in_month(expenses, month, store_id) == 0:
        return "No payments yet"
    if total_budget_cents is None:
        return "No budget"
    if paid_cents <= total_budget_cents:
        return "Within budget"
    return TOTAL_OVER_BUDGET


def mtd_achievement(sales_mtd_cents: int, target_cents) -> Optional[Decimal]:
    """Daily Dashboard!H5: MTD sales / Level 1 target."""
    return achievement(sales_mtd_cents, target_cents)


def expense_totals_cents(
    expenses: list[ExpenseInput], month: str, store_id: int
) -> dict[str, int]:
    """Month aggregates.

    - paid:     sum of paid values with PAID date inside month (HQ rule).
    - incurred: sum of incurred values with INCURRED date inside month.
    - open:     sum of pending values incurred on or before month end
                (this store's register only).
    Rows from other stores are excluded regardless of input.
    """
    _, month_end, _ = month_bounds(month)
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
        if _pending_in_scope(e, month_end):
            open_ += pending_value_cents(e)
    return {"paid": paid, "incurred": incurred, "open": open_}


def paid_cents_by_day(
    expenses: list[ExpenseInput], month: str, store_id: int
) -> dict[date, int]:
    """Daily!I-R: paid values summed by PAID date, per day of the month.
    Only days with at least one counted payment appear (a day whose
    signed amounts net to RM 0 still appears, with 0)."""
    out: dict[date, int] = {}
    for e in expenses:
        if e.store_id != store_id:
            continue
        pv = paid_value_cents(e)
        if pv and _in_month(e.paid_date, month):
            out[e.paid_date] = out.get(e.paid_date, 0) + pv
    return out


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


EXPENSE_MIX_OTHER = "Other"


def expense_mix(paid_by_category: list[tuple[str, int]], limit: int = 6
                ) -> list[tuple[str, int]]:
    """Expense Mix: categories with a paid amount this month, largest
    first (ties keep their order); past ``limit`` the rest sum into one
    "Other" slice. [] when nothing is paid."""
    items = sorted(((n, c) for n, c in paid_by_category if c and c > 0),
                   key=lambda item: -item[1])
    if len(items) <= limit:
        return items
    return items[:limit] + [(EXPENSE_MIX_OTHER,
                             sum(c for _, c in items[limit:]))]


def budget_status(paid_cents: int, budget_cents: Optional[int]) -> str:
    """Expense Monitor!H15-24: No budget / OVER BUDGET / Watch (>=80%) / Within."""
    if budget_cents is None:
        return "No budget"
    if paid_cents > budget_cents:
        return CATEGORY_OVER_BUDGET
    if budget_cents > 0 and Decimal(paid_cents) / Decimal(budget_cents) >= Decimal("0.8"):
        return "Watch"
    return "Within budget"


def budget_utilization(paid_cents: int,
                       budget_cents: Optional[int]) -> Optional[Decimal]:
    """Paid / budget. None when no budget (NULL) or a zero budget."""
    if not budget_cents:
        return None
    return Decimal(paid_cents) / Decimal(budget_cents)


def budget_variance_cents(budget_cents: Optional[int],
                          paid_cents: int) -> Optional[int]:
    """Budget remaining = budget - paid (negative = overspent). None when
    no budget is set (NULL is never treated as RM 0)."""
    if budget_cents is None:
        return None
    return budget_cents - paid_cents


def ratio_of_target(value, target) -> Optional[Decimal]:
    """value / target (share of a target used, e.g. salary ratio vs its
    target). None when either is missing or the target is zero."""
    v, t = to_decimal(value), to_decimal(target)
    if v is None or not t:
        return None
    return v / t


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
    """Daily!AP: FT + PT x factor. None when BOTH attendance fields blank,
    and None when part-timers worked but no factor is configured (never
    silently FT-only). Without part-timers the factor is not needed."""
    if ft_working is None and pt_working is None:
        return None
    if not pt_working:
        return Decimal(ft_working or 0)
    f = to_decimal(pt_factor)
    if f is None:
        return None
    return Decimal(ft_working or 0) + Decimal(pt_working) * f


def pt_factor_missing(days: list[DailyInput], pt_factor) -> bool:
    """True when part-timers worked on some day but no factor is set."""
    return pt_factor is None and any(d.pt_working for d in days)


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
        return SALARY_AWAITING_PAYROLL
    r = salary_ratio(cfg, sales_cents, include_support)
    if r is None or cfg.salary_ratio_target is None:
        return SALARY_AWAITING_DATA
    return SALARY_WITHIN if r <= cfg.salary_ratio_target else SALARY_FAILURE


def salary_budget_variance_cents(budget_cents,
                                 total_salary: Optional[int]) -> Optional[Decimal]:
    """Salary budget - actual total salary (exact; positive = under)."""
    if budget_cents is None or total_salary is None:
        return None
    return to_decimal(budget_cents) - total_salary


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
        return TURNOVER_HIGH
    return "Normal"


def average_effective_staff(days: list[DailyInput], pt_factor) -> Optional[Decimal]:
    """Summary!B52: staff-days / days with staffing entered. None when
    part-timers worked without a configured factor."""
    if pt_factor_missing(days, pt_factor):
        return None
    effs = [effective_staff(d.ft_working, d.pt_working, pt_factor) for d in days]
    reported = [e for e in effs if e is not None]
    if not reported:
        return None
    return sum(reported) / len(reported)


def daily_sales_per_staff_cents(d: DailyInput, pt_factor) -> Optional[Decimal]:
    """One day's sales per effective working staff (Daily!AQ per row)."""
    e = effective_staff(d.ft_working, d.pt_working, pt_factor)
    if d.sales_cents is None or e is None or e == 0:
        return None
    return Decimal(d.sales_cents) / e


def sales_per_staff_cents(days: list[DailyInput], pt_factor) -> Optional[Decimal]:
    """Summary!B54/Daily!AQ38: sales on staffed days / total effective
    staff-days. None when part-timers worked without a configured factor."""
    if pt_factor_missing(days, pt_factor):
        return None
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
    return STOCK_WITHIN if p <= t else STOCK_OVER_TARGET


def stock_status_label(loss_cents: int, sales_cents: Optional[int], target,
                       days_entered: int) -> str:
    """One stock status for Stock page, Report and Overview: the workbook
    status (Summary!E63) when it applies, else why it does not yet."""
    status = stock_loss_status(loss_cents, sales_cents, target)
    if status:
        return status
    if days_entered == 0 and not loss_cents:
        return STOCK_AWAITING_DATA  # nothing reported at all
    if not sales_cents:
        return STOCK_NO_SALES
    return STOCK_NO_TARGET


# --------------------------------------------------------------------------
# 8. ACTION TRACKER
# --------------------------------------------------------------------------

@dataclass
class ActionInput:
    status: Optional[str]      # Open | In progress | Blocked | Done | Cancelled
    due_date: Optional[date] = None
    pillar: Optional[str] = None


OPEN_STATUSES = ("Open", "In progress", "Blocked")


def is_overdue(a: ActionInput, as_of: date) -> bool:
    """Action Tracker!E4, per action: due date < as_of AND status not
    Done/Cancelled AND pillar present (the workbook checks the PILLAR
    column, not the issue text). Boundary: due == as_of is NOT overdue."""
    return (a.due_date is not None
            and a.due_date < as_of
            and a.status not in ("Done", "Cancelled")
            and bool(a.pillar))


def actions_in_month_scope(actions, month: str) -> list:
    """One scope rule for Overview / Actions page / Report: an action
    belongs to a month when it was raised on or before month end AND
    (raised in the month OR still Open/In progress/Blocked).

    Actions raised after the selected month are excluded. ``actions`` may
    be ActionRecords (with .raised_date/.input) or ActionInputs (with
    .raised_date) — the caller keeps one source and passes the same list
    everywhere."""
    _, month_end, _ = month_bounds(month)
    scoped = []
    for a in actions:
        raised = getattr(a, "raised_date", None)
        if raised is None or raised > month_end:
            continue
        status = (a.input.status if hasattr(a, "input") else a.status)             or "Open"
        if raised.strftime("%Y-%m") == month or status in OPEN_STATUSES:
            scoped.append(a)
    return scoped


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
        if is_overdue(a, as_of):
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
