"""Excel export of live and finalized monthly operations reports.

All KPI values come from ``operations_service`` / ``kpi``. This module only
arranges those results and their source records in an openpyxl workbook.
"""

from __future__ import annotations

from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from ducklab import kpi
from ducklab.operations_repository import OperationsRepository
from ducklab.operations_service import MonthlyOperationsReport

MONEY_FORMAT = '"RM" #,##0.00'
PERCENT_FORMAT = "0.00%"
DATE_FORMAT = "yyyy-mm-dd"


def export_filename(report: MonthlyOperationsReport,
                    snapshot: dict | None = None) -> str:
    if snapshot is not None:
        return (f"opscore-{report.store.code}-{report.month}-finalized-"
                f"v{snapshot['version']}.xlsx")
    return f"opscore-{report.store.code}-{report.month}.xlsx"


def _money(cents):
    value = kpi.cents_to_rm(cents)
    return None if value is None else float(value)


def _decimal(value):
    return None if value is None else float(value)


def _sheet(workbook, title, headers, rows, formats=None, widths=None):
    sheet = workbook.create_sheet(title)
    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="4F46E5")
    sheet.freeze_panes = "A2"
    formats = formats or {}
    for row in rows:
        sheet.append(row)
    for column, number_format in formats.items():
        for cell in sheet[column][1:]:
            if cell.value is not None:
                cell.number_format = number_format
    for index, header in enumerate(headers, 1):
        width = (widths or {}).get(header, min(max(len(header) + 3, 12), 28))
        sheet.column_dimensions[get_column_letter(index)].width = width
    return sheet


def _summary_rows(report, include_payroll, snapshot=None):
    money_rows = {
        "Sales MTD", "Month-End Sales", "Level 1 Target", "Sales per Receipt",
        "Average Selling Price", "Expenses Paid", "Expenses Incurred",
        "Expenses Pending", "Expense Budget", "Salary Cost", "Total Stock Loss",
    }
    percentage_rows = {
        "Level 1 Achievement", "Growth vs Last Year", "Growth vs Last Month",
        "Expense Ratio", "Salary Ratio", "Total Salary Ratio", "Turnover",
        "Stock Loss Ratio", "Action Completion",
    }
    sales = report.sales
    expenses = report.expenses
    manpower = report.manpower
    stock = report.stock
    actions = report.actions
    rows = [
        ("Sales MTD", _money(sales.sales_mtd_cents)),
        ("Month-End Sales", _money(sales.month_end_sales_cents)),
        ("Level 1 Target", _money(sales.targets.get("level1"))),
        ("Level 1 Achievement", _decimal(
            sales.achievement_by_level.get("level1"))),
        ("Growth vs Last Year", _decimal(sales.growth_last_year)),
        ("Growth vs Last Month", _decimal(sales.growth_last_month)),
        ("Sales per Receipt", _money(sales.spr)),
        ("Units per Transaction", _decimal(sales.upt)),
        ("Average Selling Price", _money(sales.asp)),
        ("Days Reported", sales.days_entered),
        ("Days in Month", sales.days_in_month),
        ("Expenses Paid", _money(expenses.paid_cents)),
        ("Expenses Incurred", _money(expenses.incurred_cents)),
        ("Expenses Pending", _money(expenses.open_cents)),
        ("Expense Budget", _money(expenses.total_budget_cents)),
        ("Expense Ratio", _decimal(expenses.expense_share_of_sales)),
        ("Opening Headcount", manpower.opening_headcount),
        ("Hires", manpower.hires),
        ("Resignations", manpower.resigned),
        ("Closing Headcount", manpower.closing_headcount),
        ("Salary Cost", _money(manpower.total_salary_cents)
         if include_payroll else None),
        ("Salary Ratio", _decimal(manpower.salary_ratio_store)
         if include_payroll else None),
        ("Total Salary Ratio", _decimal(manpower.salary_ratio_incl_support)
         if include_payroll else None),
        ("Turnover", _decimal(manpower.turnover_pct)),
        ("Leave Days", manpower.leave_days),
        ("Average Effective Staff", _decimal(manpower.average_effective_staff)),
        ("Total Stock Loss", _money(stock.total_loss_cents)),
        ("Stock Loss Ratio", _decimal(stock.loss_pct_of_sales)),
        ("Actions Total", actions.counts["total"]),
        ("Actions Open", actions.open),
        ("Actions In Progress", actions.counts.get("in_progress", 0)),
        ("Actions Blocked", actions.counts.get("blocked", 0)),
        ("Actions Completed", actions.counts.get("done", 0)),
        ("Actions Overdue", actions.counts.get("overdue", 0)),
        ("Action Completion", _decimal(actions.completion_pct)),
    ]
    if snapshot is not None:
        from ducklab import design as ds
        rows = [
            ("Store", f"{report.store.code} — {report.store.name}"),
            ("Month", report.month),
            ("Mode", "Finalized"),
            ("Version", snapshot["version"]),
            ("Finalized Date and Time", ds.fmt_kl_datetime(snapshot["created_at"])),
            ("Status", "Superseded" if snapshot["superseded_by"] is not None
             else "Current"),
        ] + [row for row in rows if not row[0].startswith("Action")]
    return [(label, value, PERCENT_FORMAT if label in percentage_rows
             else MONEY_FORMAT if label in money_rows else "0.00"
             if label in {"Units per Transaction", "Average Effective Staff"}
             else None) for label, value in rows]


def build_workbook(report: MonthlyOperationsReport,
                   repo: OperationsRepository,
                   include_payroll: bool = True,
                   snapshot: dict | None = None) -> Workbook:
    """Build an export; finalized reports never read current records."""
    workbook = Workbook()
    workbook.remove(workbook.active)
    summary = _sheet(
        workbook, "Summary", ["KPI", "Value"],
        _summary_rows(report, include_payroll, snapshot),
        widths={"KPI": 30, "Value": 22},
    )
    for row in summary.iter_rows(min_row=2):
        if row[2].value is not None:
            row[1].number_format = row[2].value
    summary.delete_cols(3)

    if snapshot is None:
        _source_record_sheets(workbook, report, repo)
    _summary_detail_sheets(workbook, report, repo, include_payroll)
    if snapshot is None:
        _action_sheet(workbook, report, repo)
    return workbook


def _source_record_sheets(workbook, report, repo):
    store_id, month = report.store.id, report.month
    _, month_end, days_in_month = kpi.month_bounds(month)
    start = month_end.replace(day=1)
    days = repo.get_daily_records(store_id, month)
    by_date = {day.date: day for day in days}
    signs = repo.get_contribution_signs()
    daily_rows = []
    for day_number in range(1, days_in_month + 1):
        current = start.replace(day=day_number)
        day = by_date.get(current)
        values = [None] * 11 if day is None else [
            _money(day.sales_cents), day.transactions, day.units,
            day.resigned_ft, day.new_hires_ft, day.ft_working, day.pt_working,
            day.leave_annual, day.leave_mc, day.leave_other_paid, day.leave_unpaid,
        ]
        loss = (_money(kpi.total_loss_cents(day.stock_losses, signs))
                if day is not None and day.stock_losses else None)
        daily_rows.append([current, *values, loss])
    _sheet(
        workbook, "Daily Sales",
        ["Date", "Sales", "Transactions", "Units", "Resigned FT", "New Hires FT",
         "FT Working", "PT Working", "Annual Leave", "MC", "Other Paid Leave",
         "Unpaid Leave", "Total Loss"],
        daily_rows,
        formats={"A": DATE_FORMAT, "B": MONEY_FORMAT, "M": MONEY_FORMAT},
        widths={"Date": 14, "Other Paid Leave": 19},
    )

    category_names = {c.id: c.name for c in repo.get_expense_categories(store_id)}
    expense_rows = []
    for record in repo.get_expense_transactions(store_id):
        item = record.input
        in_scope = (
            (item.incurred_date is not None and start <= item.incurred_date <= month_end)
            or (item.paid_date is not None and start <= item.paid_date <= month_end)
            or (item.status == "Pending" and item.incurred_date is not None
                and item.incurred_date <= month_end)
        )
        if in_scope:
            expense_rows.append([
                item.incurred_date, item.paid_date,
                category_names.get(item.category_id), item.description,
                _money(item.amount_cents), item.paid_by, item.status,
                item.verification, item.recorded_by, record.receipt_ref, record.remarks,
            ])
    _sheet(
        workbook, "Expenses",
        ["Incurred Date", "Paid Date", "Category", "Description", "Amount",
         "Paid By", "Status", "Verification", "Recorded By", "Receipt Ref", "Remarks"],
        expense_rows,
        formats={"A": DATE_FORMAT, "B": DATE_FORMAT, "E": MONEY_FORMAT},
        widths={"Incurred Date": 16, "Paid Date": 14, "Category": 22,
                "Description": 32, "Recorded By": 20, "Receipt Ref": 20, "Remarks": 30},
    )


def _summary_detail_sheets(workbook, report, repo, include_payroll):
    manpower = report.manpower
    salary_rows = [
        ("Opening Headcount", manpower.opening_headcount),
        ("Hires", manpower.hires),
        ("Resignations", manpower.resigned),
        ("Closing Headcount", manpower.closing_headcount),
        ("Headcount Variance", manpower.headcount_variance),
        ("Headcount Status", manpower.headcount_status),
        ("Salary Cost", _money(manpower.total_salary_cents) if include_payroll else None),
        ("Salary Ratio", _decimal(manpower.salary_ratio_store) if include_payroll else None),
        ("Total Salary Ratio", _decimal(manpower.salary_ratio_incl_support)
         if include_payroll else None),
        ("Salary Status", manpower.salary_ratio_status if include_payroll else "Restricted"),
        ("Turnover", _decimal(manpower.turnover_pct)),
        ("Turnover Status", manpower.turnover_status),
        ("Leave Days", manpower.leave_days),
        ("Average Effective Staff", _decimal(manpower.average_effective_staff)),
        ("Sales per Staff", _money(manpower.sales_per_staff)),
    ]
    manpower_sheet = _sheet(workbook, "Manpower", ["KPI", "Value"], salary_rows,
                            widths={"KPI": 28, "Value": 26})
    for row in manpower_sheet.iter_rows(min_row=2):
        if row[0].value in {"Salary Cost", "Sales per Staff"}:
            row[1].number_format = MONEY_FORMAT
        elif row[0].value in {"Salary Ratio", "Total Salary Ratio", "Turnover"}:
            row[1].number_format = PERCENT_FORMAT

    stock_names = {c.code: c.name for c in repo.get_stock_loss_codes()}
    stock_rows = [[code, stock_names.get(code), _money(total)]
                  for code, total in sorted(report.stock.totals_by_code.items())]
    stock_rows.extend([["TOTAL", "Total Loss", _money(report.stock.total_loss_cents)]])
    _sheet(workbook, "Stock Loss", ["Code", "Description", "Amount"], stock_rows,
           formats={"C": MONEY_FORMAT}, widths={"Description": 34, "Amount": 18})


def _action_sheet(workbook, report, repo):
    store_id, month = report.store.id, report.month
    action_rows = [[
        action.raised_date, action.input.pillar, action.issue, action.priority,
        action.input.status, action.pic, action.input.due_date,
        action.corrective_action, action.completion_date, action.remarks,
    ] for action in kpi.actions_in_month_scope(repo.get_actions(store_id), month)]
    _sheet(
        workbook, "Actions",
        ["Raised Date", "Pillar", "Issue", "Priority", "Status", "PIC", "Due Date",
         "Corrective Action", "Completion Date", "Remarks"],
        action_rows,
        formats={"A": DATE_FORMAT, "G": DATE_FORMAT, "I": DATE_FORMAT},
        widths={"Raised Date": 15, "Pillar": 24, "Issue": 36, "PIC": 20,
                "Due Date": 14, "Corrective Action": 36, "Completion Date": 18,
                "Remarks": 30},
    )


def export_bytes(report: MonthlyOperationsReport, repo: OperationsRepository,
                 include_payroll: bool = True,
                 snapshot: dict | None = None) -> bytes:
    output = BytesIO()
    build_workbook(report, repo, include_payroll, snapshot).save(output)
    return output.getvalue()
