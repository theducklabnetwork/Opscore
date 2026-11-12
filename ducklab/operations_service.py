"""Monthly operations assembly service.

Ties the read-only repository to the existing KPI engine: fetch real records
for one store/month, map them to KPI input dataclasses, invoke the verified
calculation functions, and return a typed structured result.

This module contains NO calculation logic of its own — every formula lives
in ducklab/kpi.py. It performs no database writes.

Live vs snapshot: `assemble_month` always returns a LIVE recalculated
report. Previously finalized snapshots are retrievable separately via
`OperationsRepository.get_snapshot` and are never mixed in here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ducklab import kpi
from ducklab.kpi import MonthConfig
from ducklab.operations_repository import (
    ExpenseCategory, OperationsRepository, Store,
)


@dataclass
class SalesSection:
    days_in_month: int
    days_entered: int
    sales_mtd_cents: int
    month_end_sales_cents: Optional[Decimal]
    targets: dict[str, Optional[int]]
    achievement_by_level: dict[str, Optional[Decimal]]
    status_by_level: dict[str, str]
    growth_last_year: Optional[Decimal]
    growth_last_month: Optional[Decimal]
    transactions: int
    units: int
    spr: Optional[Decimal]
    upt: Optional[Decimal]
    asp: Optional[Decimal]
    required_daily_run_rate: Optional[Decimal]
    needed_per_remaining_day: Optional[Decimal]
    mtd_achievement_l1: Optional[Decimal]
    average_per_day: Optional[Decimal]          # MTD / days entered
    gap_by_level: dict[str, Optional[Decimal]]  # RM gap per Level 1-4
    growth_status_last_year: str
    growth_status_last_month: str
    projection_state: str  # "actual" | "projected" | "early_estimate"


@dataclass
class ExpenseCategoryRow:
    category_id: int
    name: str
    budget_cents: Optional[int]
    paid_mtd_cents: int
    budget_status: str


@dataclass
class ExpensesSection:
    paid_cents: int
    incurred_cents: int
    open_cents: int
    total_budget_cents: Optional[int]
    expense_share_of_sales: Optional[Decimal]
    total_expenses_status: str
    budget_control_status: str  # Expense Monitor total row H25
    paid_count: int      # Paid lines with paid date in month (E40 basis)
    incurred_count: int  # valid non-cancelled lines incurred in month
    pending_count: int   # valid Pending lines incurred by month end
    validation_counts: dict[str, int]
    categories: list[ExpenseCategoryRow] = field(default_factory=list)
    # Active sales-target basis for % budgets (progressive ladder)
    budget_basis_label: str = ""
    budget_basis_cents: Optional[int] = None


@dataclass
class ManpowerSection:
    opening_headcount: Optional[int]
    hires: int
    resigned: int
    closing_headcount: Optional[int]
    headcount_variance: Optional[int]
    headcount_status: str
    total_salary_cents: Optional[int]
    salary_ratio_store: Optional[Decimal]
    salary_ratio_incl_support: Optional[Decimal]
    salary_ratio_status: str
    salary_budget_cents: Optional[Decimal]
    average_effective_staff: Optional[Decimal]
    sales_per_staff: Optional[Decimal]
    turnover_pct: Optional[Decimal]
    turnover_status: str
    leave_days: int
    # part-timers worked but no factor is set: effective staff and sales
    # per staff are None ("Factor not set"), never FT-only
    pt_factor_missing: bool = False


@dataclass
class StockSection:
    totals_by_code: dict[str, int]
    total_loss_cents: int
    loss_pct_of_sales: Optional[Decimal]
    status: str


@dataclass
class ActionsSection:
    counts: dict[str, int]
    open: int
    completion_pct: Optional[Decimal]


@dataclass
class MonthlyOperationsReport:
    """LIVE recalculated report and its assembled source records.

    ``days`` is shared with Dashboard charts; no chart or consumer recomputes
    report formulas. It is excluded from dataclass equality for backwards-
    compatible report comparisons and is never part of snapshot payloads.
    """
    store: Store
    month: str
    config: MonthConfig
    sales: SalesSection
    expenses: ExpensesSection
    manpower: ManpowerSection
    stock: StockSection
    actions: ActionsSection
    is_snapshot: bool = False
    days: list[kpi.DailyInput] = field(default_factory=list, compare=False)


def assemble_month(
    repo: OperationsRepository,
    store_id: int,
    month: str,
    as_of: Optional[date] = None,
) -> MonthlyOperationsReport:
    """Build the live monthly report for one store.

    Raises StoreNotFoundError / ConfigNotFoundError / InvalidMonthError /
    RepositoryError from the repository layer. ``as_of`` is the evaluation
    date for action overdue calculations (defaults to month end for
    determinism — no hidden system-clock calls).
    """
    store = repo.get_store(store_id)
    cfg = repo.get_month_config(store_id, month)
    _, month_end, days_in_month = kpi.month_bounds(month)
    if as_of is None:
        as_of = month_end

    days = repo.get_daily_records(store_id, month)
    expenses = repo.get_expense_transactions(store_id)
    categories = repo.get_expense_categories(store_id)
    budgets = repo.get_expense_budgets(store_id, month)
    signs = repo.get_contribution_signs()
    # One scope rule (kpi.actions_in_month_scope): raised on or before
    # month end AND (raised in the month OR still open) — carried over.
    actions = kpi.actions_in_month_scope(
        repo.get_actions(store_id), month)

    # --- Sales ---
    entered = kpi.days_with_sales_entered(days)
    mtd = kpi.sales_mtd_cents(days)
    month_end_sales = kpi.month_end_sales_cents(days, days_in_month)
    targets = kpi.sales_target_levels_cents(cfg)
    transactions = sum(d.transactions or 0 for d in days)
    units = sum(d.units or 0 for d in days)
    sales = SalesSection(
        days_in_month=days_in_month,
        days_entered=entered,
        sales_mtd_cents=mtd,
        month_end_sales_cents=month_end_sales,
        targets=targets,
        # Exact Decimal projection everywhere — no int() truncation before
        # the status/achievement/growth checks (kpi accepts Decimal).
        achievement_by_level={
            k: kpi.achievement(month_end_sales, t) for k, t in targets.items()
        },
        status_by_level={
            k: kpi.sales_status(month_end_sales, t) for k, t in targets.items()
        },
        growth_last_year=kpi.growth(month_end_sales, cfg.sales_last_year_cents),
        growth_last_month=kpi.growth(month_end_sales,
                                     cfg.sales_last_month_cents),
        average_per_day=kpi.average_per_reported_day(mtd, entered),
        gap_by_level={
            k: kpi.target_gap_cents(month_end_sales, t)
            for k, t in targets.items()
        },
        growth_status_last_year=kpi.growth_status(
            kpi.growth(month_end_sales, cfg.sales_last_year_cents), entered),
        growth_status_last_month=kpi.growth_status(
            kpi.growth(month_end_sales, cfg.sales_last_month_cents), entered),
        projection_state=kpi.projection_state(entered, days_in_month),
        transactions=transactions,
        units=units,
        spr=kpi.spr_cents(mtd, transactions),
        upt=kpi.upt(units, transactions),
        asp=kpi.asp_cents(mtd, units),
        required_daily_run_rate=kpi.required_daily_run_rate(
            targets["level1"], days_in_month),
        needed_per_remaining_day=kpi.sales_needed_per_remaining_day(
            targets["level1"], mtd, entered, days_in_month),
        mtd_achievement_l1=kpi.mtd_achievement(mtd, targets["level1"]),
    )

    # --- Expenses ---
    exp_inputs = [e.input for e in expenses]
    totals = kpi.expense_totals_cents(exp_inputs, month, store_id)
    known_ids = {c.id for c in categories}
    validation_counts = kpi.expense_review_counts(exp_inputs, month,
                                                  store_id, known_ids)
    # Budgets are stored as a % of the ACTIVE sales target (progressive
    # ladder: Minimum RTM floor, stepping up Levels 1-4 as recorded sales
    # reach each target). Convert pct -> effective cents before the KPI
    # checks; everything downstream stays in cents.
    active_label, active_target = kpi.active_sales_target_cents(cfg, mtd)
    budget_cents_by_cat = {
        c.id: kpi.budget_from_pct(budgets.get(c.id), active_target)
        for c in categories
    }
    # Total budget: sum of non-NULL budgets; None when NONE is set
    # (workbook: blank -> "No budget", never 0 -> "Over budget").
    budget_values = [b for b in budget_cents_by_cat.values()
                     if b is not None]
    total_budget = sum(budget_values) if budget_values else None
    category_paid = kpi.category_paid_totals_cents(
        exp_inputs, month, store_id)
    cat_rows = [
        ExpenseCategoryRow(
            category_id=c.id,
            name=c.name,
            budget_cents=budget_cents_by_cat.get(c.id),
            paid_mtd_cents=category_paid.get(c.id, 0),
            budget_status=kpi.budget_status(
                category_paid.get(c.id, 0), budget_cents_by_cat.get(c.id)),
        )
        for c in categories
    ]
    expenses_section = ExpensesSection(
        paid_cents=totals["paid"],
        incurred_cents=totals["incurred"],
        open_cents=totals["open"],
        total_budget_cents=total_budget,
        expense_share_of_sales=kpi.expense_share_of_sales(totals["paid"], mtd),
        paid_count=kpi.paid_count_in_month(exp_inputs, month, store_id),
        incurred_count=kpi.incurred_count_in_month(exp_inputs, month,
                                                   store_id),
        pending_count=kpi.pending_count(exp_inputs, month, store_id),
        total_expenses_status=kpi.total_expenses_status(
            exp_inputs, month, store_id, totals["paid"], total_budget),
        budget_control_status=kpi.budget_control_status(
            totals["paid"], total_budget),
        validation_counts=validation_counts,
        categories=cat_rows,
        budget_basis_label=active_label,
        budget_basis_cents=active_target,
    )

    # --- Manpower ---
    hires = sum(d.new_hires_ft or 0 for d in days)
    resigned = sum(d.resigned_ft or 0 for d in days)
    closing = kpi.closing_headcount(cfg.opening_headcount_ft, hires, resigned)
    hc_var, hc_status = kpi.headcount_variance_status(closing, cfg.budget_headcount)
    manpower = ManpowerSection(
        opening_headcount=cfg.opening_headcount_ft,
        hires=hires,
        resigned=resigned,
        closing_headcount=closing,
        headcount_variance=hc_var,
        headcount_status=hc_status,
        total_salary_cents=kpi.total_salary_cents(cfg),
        salary_ratio_store=kpi.salary_ratio(cfg, mtd),
        salary_ratio_incl_support=kpi.salary_ratio(cfg, mtd, include_support=True),
        salary_ratio_status=kpi.salary_ratio_status(cfg, mtd, include_support=True),
        salary_budget_cents=kpi.salary_budget_cents(mtd, cfg.salary_ratio_target),
        average_effective_staff=kpi.average_effective_staff(days, cfg.part_timer_factor),
        sales_per_staff=kpi.sales_per_staff_cents(days, cfg.part_timer_factor),
        turnover_pct=kpi.turnover_pct(resigned, cfg.opening_headcount_ft),
        turnover_status=kpi.turnover_status(
            resigned, cfg.opening_headcount_ft, cfg.turnover_threshold),
        leave_days=kpi.leave_days(days),
        pt_factor_missing=kpi.pt_factor_missing(days, cfg.part_timer_factor),
    )

    # --- Stock ---
    stock_totals = kpi.monthly_stock_totals(days)
    total_loss = kpi.total_loss_cents(stock_totals, signs)
    stock = StockSection(
        totals_by_code=stock_totals,
        total_loss_cents=total_loss,
        loss_pct_of_sales=kpi.stock_loss_pct(total_loss, mtd),
        status=kpi.stock_loss_status(total_loss, mtd, cfg.stock_loss_target),
    )

    # --- Actions ---
    counts = kpi.action_counts([a.input for a in actions], as_of=as_of)  # noqa: E501
    actions_section = ActionsSection(
        counts=counts,
        open=kpi.open_actions(counts),
        completion_pct=kpi.completion_pct(counts),
    )

    return MonthlyOperationsReport(
        store=store, month=month, config=cfg, sales=sales,
        expenses=expenses_section, manpower=manpower, stock=stock,
        actions=actions_section, days=days,
    )
