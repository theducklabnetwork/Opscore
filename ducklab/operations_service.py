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
    validation_counts: dict[str, int]
    categories: list[ExpenseCategoryRow] = field(default_factory=list)


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
    """LIVE recalculated report (not a finalized snapshot)."""
    store: Store
    month: str
    config: MonthConfig
    sales: SalesSection
    expenses: ExpensesSection
    manpower: ManpowerSection
    stock: StockSection
    actions: ActionsSection
    is_snapshot: bool = False


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
    actions = repo.get_actions(store_id, month)

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
        achievement_by_level={
            k: kpi.achievement(
                int(month_end_sales) if month_end_sales is not None else None, t)
            for k, t in targets.items()
        },
        status_by_level={
            k: kpi.sales_status(
                int(month_end_sales) if month_end_sales is not None else None, t)
            for k, t in targets.items()
        },
        growth_last_year=kpi.growth(
            int(month_end_sales) if month_end_sales is not None else None,
            cfg.sales_last_year_cents),
        growth_last_month=kpi.growth(
            int(month_end_sales) if month_end_sales is not None else None,
            cfg.sales_last_month_cents),
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
    validation_counts: dict[str, int] = {}
    for e in exp_inputs:
        status = kpi.expense_validation(e, known_ids)
        validation_counts[status] = validation_counts.get(status, 0) + 1
    budget_values = list(budgets.values())
    total_budget = sum(b for b in budget_values if b is not None) if budget_values else None
    cat_rows = [
        ExpenseCategoryRow(
            category_id=c.id,
            name=c.name,
            budget_cents=budgets.get(c.id),
            paid_mtd_cents=kpi.category_paid_mtd_cents(
                exp_inputs, month, store_id, c.id),
            budget_status=kpi.budget_status(
                kpi.category_paid_mtd_cents(exp_inputs, month, store_id, c.id),
                budgets.get(c.id)),
        )
        for c in categories
    ]
    expenses_section = ExpensesSection(
        paid_cents=totals["paid"],
        incurred_cents=totals["incurred"],
        open_cents=totals["open"],
        total_budget_cents=total_budget,
        expense_share_of_sales=kpi.expense_share_of_sales(totals["paid"], mtd),
        total_expenses_status=kpi.total_expenses_status(
            exp_inputs, month, store_id, totals["paid"], total_budget),
        validation_counts=validation_counts,
        categories=cat_rows,
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
    counts = kpi.action_counts([a.input for a in actions], as_of=as_of)
    actions_section = ActionsSection(
        counts=counts,
        open=kpi.open_actions(counts),
        completion_pct=kpi.completion_pct(counts),
    )

    return MonthlyOperationsReport(
        store=store, month=month, config=cfg, sales=sales,
        expenses=expenses_section, manpower=manpower, stock=stock,
        actions=actions_section,
    )
