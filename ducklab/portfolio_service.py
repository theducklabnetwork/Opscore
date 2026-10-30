"""Read-only aggregation across the stores accessible to one user.

Store KPIs are calculated only by ``AppService.preview_month`` (and therefore
``operations_service``/``kpi``). This service adds configured-store values and
isolates missing setup or per-store failures so they cannot break the portfolio.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional

from ducklab import authz
from ducklab.auth_service import AuthorizationError
from ducklab.operations_repository import RepositoryError


@dataclass
class PortfolioAlert:
    kind: str
    store: str
    text: str
    href: str


@dataclass
class PortfolioStore:
    store_id: int
    code: str
    name: str
    configured: bool
    error: str = ""
    sales_cents: Optional[int] = None
    achievement: Optional[Decimal] = None
    expenses_cents: Optional[int] = None
    incurred_expenses_cents: Optional[int] = None
    open_expenses_cents: Optional[int] = None
    salary_cents: Optional[int] = None
    salary_ratio: Optional[Decimal] = None
    closing_headcount: Optional[int] = None
    stock_loss_cents: Optional[int] = None
    stock_loss_ratio: Optional[Decimal] = None
    days_reported: int = 0
    days_in_month: int = 0
    open_actions: int = 0
    overdue_actions: int = 0
    total_actions: int = 0
    completion_pct: Optional[Decimal] = None
    alerts: list[PortfolioAlert] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "OK" if self.configured else "Setup Required"


@dataclass
class PortfolioReport:
    month: str
    rows: list[PortfolioStore]
    store_count: int
    configured_count: int
    days_reported: int
    store_days: int
    sales_cents: Optional[int]
    expenses_cents: Optional[int]
    stock_loss_cents: Optional[int]
    salary_cents: Optional[int]
    salary_ratio: Optional[Decimal]
    payroll_visible: bool
    open_actions: int
    overdue_actions: int
    alerts: list[PortfolioAlert] = field(default_factory=list)
    payroll_status: str = ""
    failures: list[str] = field(default_factory=list)


def _alert_kind(alert: dict[str, str]) -> str:
    """Display grouping only; conditions are evaluated by dashboard_attention."""
    href = alert["href"]
    if href == "/ops/expenses" and "OVER BUDGET" in alert["text"]:
        return "Expense Over Budget"
    return {
        "/ops/daily": "Unreported Days",
        "/ops/stock": "Stock Loss Over Target",
        "/ops/manpower": "High Turnover",
        "/ops/actions": "Overdue Actions",
    }.get(href)


def _total(values) -> Optional[int]:
    """An empty or entirely unreported set is None, never a synthetic zero."""
    values = list(values)
    return sum(values) if values else None


class PortfolioService:
    def __init__(self, app_service):
        self.app = app_service

    def build(self, user, month: str, today: Optional[date] = None) -> PortfolioReport:
        """Aggregate live monthly reports, using the same reporting date as
        the single-store Dashboard. Authorization failures are not swallowed.
        """
        authz.require_active(user)
        # Imported lazily to keep the aggregation boundary easy to fake in tests.
        from ducklab import kpi
        from ducklab.ops_overview_state import dashboard_attention

        as_of = kpi.reporting_date(month, today)
        stores = self.app.list_my_stores(user)
        alert_today = today or kpi.kl_today()
        rows: list[PortfolioStore] = []
        for store in stores:
            row = PortfolioStore(store["id"], store["code"], store["name"], False)
            rows.append(row)
            try:
                report = self.app.preview_month(
                    user, store["id"], month, as_of=as_of)
            except AuthorizationError:
                raise
            except (RepositoryError, ValueError, ArithmeticError) as exc:
                # Setup errors and corrupt store-local data must not take down
                # other stores. No diagnostic details are exposed to the UI.
                row.error = str(exc)
                if store["active"]:
                    row.alerts.append(PortfolioAlert(
                        kind="Setup Required",
                        store=f"{row.code} — {row.name}",
                        text="Setup Required",
                        href=f"/ops?store={row.store_id}",
                    ))
                continue
            s, e, m, st = (report.sales, report.expenses,
                           report.manpower, report.stock)
            row.configured = True
            row.sales_cents = s.sales_mtd_cents
            row.achievement = s.achievement_by_level.get("level1")
            row.expenses_cents = e.paid_cents
            row.incurred_expenses_cents = e.incurred_cents
            row.open_expenses_cents = e.open_cents
            row.salary_cents = m.total_salary_cents
            row.closing_headcount = m.closing_headcount
            row.stock_loss_cents = st.total_loss_cents
            row.stock_loss_ratio = st.loss_pct_of_sales
            row.days_reported = s.days_entered
            row.days_in_month = s.days_in_month
            # The per-store ratio is present only when its own payroll exists;
            # portfolio ratios below always use summed money, never this field.
            row.salary_ratio = m.salary_ratio_incl_support
            if store["active"]:
                row.open_actions = report.actions.open
                row.overdue_actions = report.actions.counts["overdue"]
                row.total_actions = report.actions.counts["total"]
                row.completion_pct = report.actions.completion_pct
                for alert in dashboard_attention(report, alert_today):
                    kind = _alert_kind(alert)
                    if kind is None:
                        continue
                    row.alerts.append(PortfolioAlert(
                        kind=_alert_kind(alert),
                        store=f"{row.code} — {row.name}",
                        text=alert["text"],
                        href=f"/ops?store={row.store_id}",
                    ))

        configured = [r for r in rows if r.configured]
        # Role policy is global in authz: anyone without payroll permission
        # gets no payroll values, rather than values for a permitted subset.
        active = [r for r in rows if r.configured]
        payroll_visible = authz.can_manage_config(user)
        salary_total = _total(r.salary_cents for r in configured
                              if r.salary_cents is not None)
        sales_total = _total(r.sales_cents for r in configured
                             if r.sales_cents is not None)
        salary_ratio = kpi.ratio(salary_total, sales_total)
        if not payroll_visible:
            payroll_status = "Restricted"
            salary_total = None
            salary_ratio = None
            for row in rows:
                row.salary_cents = None
                row.salary_ratio = None
        elif any(r.salary_cents is None for r in configured):
            payroll_status = "Partial"
        else:
            payroll_status = ""
        return PortfolioReport(
            month=month,
            rows=rows,
            store_count=len(rows),
            configured_count=len(configured),
            days_reported=sum(r.days_reported for r in configured),
            store_days=sum(r.days_in_month for r in configured),
            sales_cents=sales_total,
            expenses_cents=_total(r.expenses_cents for r in configured
                                  if r.expenses_cents is not None),
            stock_loss_cents=_total(r.stock_loss_cents for r in configured
                                    if r.stock_loss_cents is not None),
            salary_cents=salary_total,
            salary_ratio=salary_ratio,
            payroll_visible=payroll_visible,
            open_actions=sum(r.open_actions for r in active),
            overdue_actions=sum(r.overdue_actions for r in active),
            alerts=[a for r in rows for a in r.alerts],
            payroll_status=payroll_status,
            failures=[r.code for r in rows if not r.configured],
        )
