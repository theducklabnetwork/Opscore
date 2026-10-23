"""Read-only repository layer for Store Operations Monitoring.

Thin data-access module bridging store_ops_db (SQLite) and the KPI engine.
No writes, no Reflex, no calculation logic. All queries are parameterized and
scoped by store_id. Money stays in integer cents; NULL stays None.

Action carry-forward policy: actions are retrieved by the month they were
RAISED in (`get_actions(store_id, month)`), or unfiltered per store
(`get_actions(store_id)`). The workbook has no cross-month carry-forward
rule for actions; overdue/open status is computed by the KPI engine against
an explicit as-of date. This is documented, not silently invented.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Optional

from ducklab import store_ops_db
from ducklab.kpi import (
    ActionInput, DailyInput, ExpenseInput, MonthConfig, to_decimal,
)


class RepositoryError(Exception):
    """Base error for repository failures."""


class StoreNotFoundError(RepositoryError):
    """Unknown store id or code."""


class ConfigNotFoundError(RepositoryError):
    """No monthly configuration for the requested store/month."""


class InvalidMonthError(RepositoryError):
    """Reporting month is not a valid 'YYYY-MM' string."""


def _parse_date(text: Optional[str]) -> Optional[date]:
    """ISO 'YYYY-MM-DD' -> date; None preserved; invalid text raises."""
    if text is None:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise RepositoryError(f"Invalid date stored in database: {text!r}") from exc


def _validate_month(month: str) -> None:
    try:
        y, m = int(month[:4]), int(month[5:7])
        if len(month) != 7 or month[4] != "-" or not (1 <= m <= 12):
            raise ValueError
    except (ValueError, TypeError, IndexError) as exc:
        raise InvalidMonthError(f"Invalid reporting month: {month!r}") from exc


@dataclass(frozen=True)
class Store:
    id: int
    code: str
    name: str
    concept: Optional[str]
    zone: Optional[str]
    zone_pic: Optional[str]
    branch_manager: Optional[str]
    active: bool


@dataclass(frozen=True)
class ExpenseCategory:
    id: int
    store_id: int
    name: str
    sort_order: int


@dataclass(frozen=True)
class ExpenseRecord:
    """Register row with repository metadata, mapped to ExpenseInput."""
    id: int
    input: ExpenseInput
    receipt_ref: Optional[str]
    remarks: Optional[str]


@dataclass(frozen=True)
class ActionRecord:
    id: int
    raised_date: Optional[date]
    input: ActionInput
    issue: Optional[str]
    priority: Optional[str]
    pic: Optional[str]
    corrective_action: Optional[str]
    completion_date: Optional[date]
    remarks: Optional[str]


@dataclass(frozen=True)
class StockLossCode:
    code: str
    name: str
    contribution_sign: int


class OperationsRepository:
    """Read-only access to one store-operations database."""

    def __init__(self, db_path: Path | str = store_ops_db.DB_PATH):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        try:
            return store_ops_db.connect(self._db_path)
        except sqlite3.Error as exc:
            raise RepositoryError(f"Cannot open database {self._db_path}: {exc}") from exc

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Stores
    # ------------------------------------------------------------------
    def list_stores(self) -> list[Store]:
        """All stores, including inactive (historical reporting)."""
        rows = self._query("SELECT * FROM stores ORDER BY code")
        return [self._to_store(r) for r in rows]

    def get_store(self, store_id: int) -> Store:
        rows = self._query("SELECT * FROM stores WHERE id = ?", (store_id,))
        if not rows:
            raise StoreNotFoundError(f"Unknown store id: {store_id}")
        return self._to_store(rows[0])

    def get_store_by_code(self, code: str) -> Store:
        rows = self._query("SELECT * FROM stores WHERE code = ?", (code,))
        if not rows:
            raise StoreNotFoundError(f"Unknown store code: {code!r}")
        return self._to_store(rows[0])

    @staticmethod
    def _to_store(r: sqlite3.Row) -> Store:
        return Store(
            id=r["id"], code=r["code"], name=r["name"], concept=r["concept"],
            zone=r["zone"], zone_pic=r["zone_pic"],
            branch_manager=r["branch_manager"], active=bool(r["active"]),
        )

    # ------------------------------------------------------------------
    # Monthly configuration
    # ------------------------------------------------------------------
    def get_month_config(self, store_id: int, month: str) -> MonthConfig:
        """Exact store/month lookup. Never substitutes another month."""
        _validate_month(month)
        rows = self._query(
            "SELECT * FROM monthly_configs WHERE store_id = ? AND month = ?",
            (store_id, month),
        )
        if not rows:
            raise ConfigNotFoundError(
                f"No monthly configuration for store {store_id} in {month}"
            )
        r = rows[0]
        return MonthConfig(
            month=r["month"],
            min_rtm_target_cents=r["min_rtm_target_cents"],
            level1_base_cents=r["level1_base_cents"],
            level2_pct=to_decimal(r["level2_pct"]),
            level3_pct=to_decimal(r["level3_pct"]),
            level4_pct=to_decimal(r["level4_pct"]),
            sales_last_year_cents=r["sales_last_year_cents"],
            sales_last_month_cents=r["sales_last_month_cents"],
            salary_ratio_target=to_decimal(r["salary_ratio_target"]),
            budget_headcount=r["budget_headcount"],
            turnover_threshold=to_decimal(r["turnover_threshold"]),
            stock_loss_target=to_decimal(r["stock_loss_target"]),
            opening_headcount_ft=r["opening_headcount_ft"],
            part_timer_factor=to_decimal(r["part_timer_factor"]),
            salary_basic_allowance_cents=r["salary_basic_allowance_cents"],
            overtime_cents=r["overtime_cents"],
            support_team_cost_cents=r["support_team_cost_cents"],
        )

    # ------------------------------------------------------------------
    # Daily records
    # ------------------------------------------------------------------
    def get_daily_records(self, store_id: int, month: str) -> list[DailyInput]:
        """Saved records only, chronological. No artificial rows created."""
        _validate_month(month)
        rows = self._query(
            "SELECT * FROM daily_records WHERE store_id = ? "
            "AND strftime('%Y-%m', date) = ? ORDER BY date",
            (store_id, month),
        )
        losses = self._daily_stock_loss_map(
            [r["id"] for r in rows]
        )
        return [
            DailyInput(
                date=_parse_date(r["date"]),
                sales_cents=r["sales_cents"],
                transactions=r["transactions"],
                units=r["units"],
                resigned_ft=r["resigned_ft"],
                new_hires_ft=r["new_hires_ft"],
                ft_working=r["ft_working"],
                pt_working=r["pt_working"],
                leave_annual=r["leave_annual"],
                leave_mc=r["leave_mc"],
                leave_other_paid=r["leave_other_paid"],
                leave_unpaid=r["leave_unpaid"],
                stock_losses=losses.get(r["id"], {}),
            )
            for r in rows
        ]

    def _daily_stock_loss_map(self, record_ids: list[int]) -> dict[int, dict[str, int]]:
        if not record_ids:
            return {}
        placeholders = ",".join("?" for _ in record_ids)
        rows = self._query(
            f"SELECT daily_record_id, code, amount_cents FROM daily_stock_losses "
            f"WHERE daily_record_id IN ({placeholders}) ORDER BY code",
            tuple(record_ids),
        )
        out: dict[int, dict[str, int]] = {}
        for r in rows:
            out.setdefault(r["daily_record_id"], {})[r["code"]] = r["amount_cents"]
        return out

    # ------------------------------------------------------------------
    # Stock-loss reference codes
    # ------------------------------------------------------------------
    def get_stock_loss_codes(self) -> list[StockLossCode]:
        """Reference codes. The display name comes from the workbook
        reference (store_ops_db.STOCK_LOSS_CODES) so a wording change
        reaches databases seeded earlier without a data migration."""
        names = {c: n for c, n, _ in store_ops_db.STOCK_LOSS_CODES}
        rows = self._query("SELECT * FROM stock_loss_codes ORDER BY code")
        return [StockLossCode(r["code"], names.get(r["code"], r["name"]),
                              r["contribution_sign"]) for r in rows]

    def get_contribution_signs(self) -> dict[str, int]:
        return {c.code: c.contribution_sign for c in self.get_stock_loss_codes()}

    # ------------------------------------------------------------------
    # Expenses
    # ------------------------------------------------------------------
    def get_expense_categories(self, store_id: int) -> list[ExpenseCategory]:
        rows = self._query(
            "SELECT * FROM expense_categories WHERE store_id = ? ORDER BY sort_order, id",
            (store_id,),
        )
        return [ExpenseCategory(r["id"], r["store_id"], r["name"], r["sort_order"])
                for r in rows]

    def get_expense_budgets(self, store_id: int, month: str) -> dict[int, Optional[int]]:
        """category_id -> budget_cents (None = no budget entered)."""
        _validate_month(month)
        rows = self._query(
            "SELECT b.category_id, b.budget_cents FROM monthly_expense_budgets b "
            "JOIN monthly_configs c ON c.id = b.monthly_config_id "
            "WHERE b.store_id = ? AND c.month = ?",
            (store_id, month),
        )
        return {r["category_id"]: r["budget_cents"] for r in rows}

    def get_expense_transactions(self, store_id: int) -> list[ExpenseRecord]:
        """ALL transactions for the store, ordered by incurred date.

        Not filtered by month: the KPI engine applies the paid-date and
        incurred-date month rules itself, and cross-month transactions must
        reach it. Incomplete drafts are included so the validation ladder
        can classify them. Stable category IDs are used, never names.
        """
        rows = self._query(
            "SELECT * FROM expense_transactions WHERE store_id = ? "
            "ORDER BY incurred_date, id",
            (store_id,),
        )
        return [
            ExpenseRecord(
                id=r["id"],
                input=ExpenseInput(
                    incurred_date=_parse_date(r["incurred_date"]),
                    paid_date=_parse_date(r["paid_date"]),
                    category_id=r["category_id"],
                    description=r["description"],
                    amount_cents=r["amount_cents"],
                    paid_by=r["paid_by"],
                    status=r["status"],
                    verification=r["verification"],
                    recorded_by=r["recorded_by"],
                    store_id=store_id,
                ),
                receipt_ref=r["receipt_ref"],
                remarks=r["remarks"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    def get_actions(self, store_id: int, month: Optional[str] = None) -> list[ActionRecord]:
        """Actions for a store; optionally only those RAISED in a month.

        Cross-month open actions are NOT silently carried forward (no
        verified workbook rule); callers choose the scope explicitly.
        """
        if month is not None:
            _validate_month(month)
            rows = self._query(
                "SELECT * FROM actions WHERE store_id = ? "
                "AND strftime('%Y-%m', raised_date) = ? ORDER BY raised_date, id",
                (store_id, month),
            )
        else:
            rows = self._query(
                "SELECT * FROM actions WHERE store_id = ? ORDER BY raised_date, id",
                (store_id,),
            )
        return [
            ActionRecord(
                id=r["id"],
                raised_date=_parse_date(r["raised_date"]),
                input=ActionInput(
                    status=r["status"], due_date=_parse_date(r["due_date"]),
                    pillar=r["pillar"],
                ),
                issue=r["issue"], priority=r["priority"], pic=r["pic"],
                corrective_action=r["corrective_action"],
                completion_date=_parse_date(r["completion_date"]),
                remarks=r["remarks"],
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Monthly snapshots (read-only; versioning preserved)
    # ------------------------------------------------------------------
    def get_snapshot(self, store_id: int, month: str,
                     version: Optional[int] = None) -> Optional[dict]:
        """Latest (or specific) snapshot version. Never mutates."""
        _validate_month(month)
        if version is None:
            rows = self._query(
                "SELECT * FROM monthly_snapshots WHERE store_id = ? AND month = ? "
                "ORDER BY version DESC LIMIT 1",
                (store_id, month),
            )
        else:
            rows = self._query(
                "SELECT * FROM monthly_snapshots WHERE store_id = ? AND month = ? "
                "AND version = ?",
                (store_id, month, version),
            )
        if not rows:
            return None
        r = rows[0]
        return {
            "id": r["id"], "store_id": r["store_id"], "month": r["month"],
            "version": r["version"], "kpi_json": r["kpi_json"],
            "finalized": bool(r["finalized"]),
            "superseded_by": r["superseded_by"], "created_at": r["created_at"],
        }
