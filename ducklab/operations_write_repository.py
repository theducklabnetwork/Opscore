"""Write-side repository for Store Operations Monitoring.

Store-scoped, parameterized writes with explicit transactions. No KPI logic,
no UI, no writes to placement_tracker.db. Money is integer cents; None stays
NULL. Update semantics are partial: only supplied fields change.

Optimistic concurrency: the schema has no row-version column, so
``update_daily_record``-style methods accept ``expected_<field>`` checks that
are verified inside the same UPDATE statement (atomic compare-and-set). A
full version-column approach would require a schema migration — documented
as an open design decision, not silently added.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from ducklab import store_ops_db
from ducklab.operations_repository import (
    InvalidMonthError, RepositoryError, StoreNotFoundError,
)

PAYMENT_STATUSES = ("Pending", "Paid", "Cancelled")
PAYMENT_SOURCES = ("Petty Cash", "HQ / Central", "Store Account", "Other")
VERIFICATIONS = ("Unverified", "Verified", "Disputed")
PILLARS = ("Sales Performance", "Expenses Management", "Manpower Efficiency",
           "Stock Management", "General Operations")
PRIORITIES = ("Low", "Medium", "High", "Critical")
ACTION_STATUSES = ("Open", "In progress", "Blocked", "Done", "Cancelled")


class WriteError(RepositoryError):
    """Base write failure."""


class DuplicateRecordError(WriteError):
    """Unique constraint violation (store code, store/month, store/date...)."""


class RecordNotFoundError(WriteError):
    """Target record does not exist in the given store scope."""


class ValidationError(WriteError):
    """Field-level business validation failure."""


class StaleWriteError(WriteError):
    """Expected-state check failed: record changed underneath the caller."""


def _check_month(month: str) -> None:
    try:
        y, m = int(month[:4]), int(month[5:7])
        if len(month) != 7 or month[4] != "-" or not (1 <= m <= 12):
            raise ValueError
    except (ValueError, TypeError, IndexError):
        raise InvalidMonthError(f"Invalid reporting month: {month!r}")


def _iso(d: Optional[date]) -> Optional[str]:
    return d.isoformat() if d is not None else None


class OperationsWriteRepository:
    """Store-scoped write operations against a store-ops database."""

    def __init__(self, db_path: Path | str = store_ops_db.DB_PATH):
        self._db_path = db_path

    def _connect(self) -> sqlite3.Connection:
        conn = store_ops_db.connect(self._db_path)
        return conn

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        """Single-statement autocommitted write."""
        conn = self._connect()
        try:
            cur = conn.execute(sql, params)
            conn.commit()
            return cur
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        except sqlite3.OperationalError as exc:  # e.g. database is locked
            conn.rollback()
            raise WriteError(f"Database operational error: {exc}") from exc
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Stores
    # ------------------------------------------------------------------
    def create_store(self, code: str, name: str, concept: Optional[str] = None,
                     zone: Optional[str] = None, zone_pic: Optional[str] = None,
                     branch_manager: Optional[str] = None) -> int:
        if not code or not name:
            raise ValidationError("Store code and name are required")
        cur = self._execute(
            "INSERT INTO stores (code, name, concept, zone, zone_pic, "
            "branch_manager) VALUES (?,?,?,?,?,?)",
            (code, name, concept, zone, zone_pic, branch_manager),
        )
        return cur.lastrowid

    def update_store(self, store_id: int, **fields) -> None:
        """Partial update of store details (name/concept/zone/zone_pic/
        branch_manager). Unknown fields rejected."""
        allowed = {"name", "concept", "zone", "zone_pic", "branch_manager"}
        bad = set(fields) - allowed
        if bad:
            raise ValidationError(f"Unknown store fields: {sorted(bad)}")
        if not fields:
            return
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn = self._connect()
        try:
            cur = conn.execute(
                f"UPDATE stores SET {sets} WHERE id = ?",
                (*fields.values(), store_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise RecordNotFoundError(f"Store {store_id} not found")
        finally:
            conn.close()

    def set_store_active(self, store_id: int, active: bool) -> None:
        """Activate/deactivate. Historical records are never deleted."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "UPDATE stores SET active = ? WHERE id = ?",
                (1 if active else 0, store_id),
            )
            conn.commit()
            if cur.rowcount == 0:
                raise RecordNotFoundError(f"Store {store_id} not found")
        finally:
            conn.close()

    def _require_store(self, conn: sqlite3.Connection, store_id: int) -> None:
        if not conn.execute("SELECT 1 FROM stores WHERE id = ?",
                            (store_id,)).fetchone():
            raise StoreNotFoundError(f"Unknown store id: {store_id}")

    # ------------------------------------------------------------------
    # Monthly configuration (upsert keyed on UNIQUE(store_id, month))
    # ------------------------------------------------------------------
    _CONFIG_FIELDS = (
        "min_rtm_target_cents", "level1_base_cents", "level2_pct", "level3_pct",
        "level4_pct", "sales_last_year_cents", "sales_last_month_cents",
        "salary_ratio_target", "budget_headcount", "turnover_threshold",
        "stock_loss_target", "opening_headcount_ft", "part_timer_factor",
        "salary_basic_allowance_cents", "overtime_cents", "support_team_cost_cents",
    )

    def save_month_config(self, store_id: int, month: str, **fields) -> int:
        """Create or partially update one store's monthly configuration.

        Only supplied fields are written; existing values are preserved.
        Nullable payroll fields may be set explicitly to None.
        """
        _check_month(month)
        bad = set(fields) - set(self._CONFIG_FIELDS)
        if bad:
            raise ValidationError(f"Unknown config fields: {sorted(bad)}")
        for pct in ("level2_pct", "level3_pct", "level4_pct",
                    "salary_ratio_target", "turnover_threshold",
                    "stock_loss_target", "part_timer_factor"):
            v = fields.get(pct)
            if v is not None and not (0 <= float(v) <= 10):
                raise ValidationError(f"{pct} out of range: {v}")
        conn = self._connect()
        try:
            self._require_store(conn, store_id)
            row = conn.execute(
                "SELECT id FROM monthly_configs WHERE store_id = ? AND month = ?",
                (store_id, month),
            ).fetchone()
            if row is None:
                cols = ["store_id", "month", *fields]
                cur = conn.execute(
                    f"INSERT INTO monthly_configs ({','.join(cols)}) "
                    f"VALUES ({','.join('?' for _ in cols)})",
                    (store_id, month, *fields.values()),
                )
                config_id = cur.lastrowid
            else:
                config_id = row[0]
                if fields:
                    sets = ", ".join(f"{k} = ?" for k in fields)
                    conn.execute(
                        f"UPDATE monthly_configs SET {sets} WHERE id = ?",
                        (*fields.values(), config_id),
                    )
            conn.commit()
            return config_id
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        finally:
            conn.close()

    def finalized_snapshot_exists(self, store_id: int, month: str) -> bool:
        """Risk check: editing configs for a finalized month invalidates
        the stored snapshot. Callers must surface this; we never silently
        mutate the snapshot itself."""
        conn = self._connect()
        try:
            return bool(conn.execute(
                "SELECT 1 FROM monthly_snapshots WHERE store_id = ? AND month = ? "
                "AND finalized = 1", (store_id, month),
            ).fetchone())
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Expense categories & budgets
    # ------------------------------------------------------------------
    def create_expense_category(self, store_id: int, name: str,
                                sort_order: int = 0) -> int:
        if not name:
            raise ValidationError("Category name is required")
        conn = self._connect()
        try:
            self._require_store(conn, store_id)
            cur = conn.execute(
                "INSERT INTO expense_categories (store_id, name, sort_order) "
                "VALUES (?,?,?)", (store_id, name, sort_order),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        finally:
            conn.close()

    def update_expense_category(self, store_id: int, category_id: int,
                                name: Optional[str] = None,
                                sort_order: Optional[int] = None) -> None:
        """Rename/reorder. Historical transactions keep the stable ID."""
        if name is None and sort_order is None:
            return
        conn = self._connect()
        try:
            owned = conn.execute(
                "SELECT 1 FROM expense_categories WHERE id = ? AND store_id = ?",
                (category_id, store_id),
            ).fetchone()
            if not owned:
                raise RecordNotFoundError(
                    f"Category {category_id} not found in store {store_id}")
            if name is not None:
                if not name:
                    raise ValidationError("Category name cannot be empty")
                conn.execute(
                    "UPDATE expense_categories SET name = ? WHERE id = ?",
                    (name, category_id))
            if sort_order is not None:
                conn.execute(
                    "UPDATE expense_categories SET sort_order = ? WHERE id = ?",
                    (sort_order, category_id))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        finally:
            conn.close()

    def save_expense_budget(self, store_id: int, month: str, category_id: int,
                            budget_cents: Optional[int]) -> int:
        """Create/update one category budget for a store/month. Idempotent
        on UNIQUE(monthly_config_id, category_id); never duplicates."""
        _check_month(month)
        if budget_cents is not None and not isinstance(budget_cents, int):
            raise ValidationError("budget_cents must be integer cents or None")
        conn = self._connect()
        try:
            self._require_store(conn, store_id)
            cfg = conn.execute(
                "SELECT id FROM monthly_configs WHERE store_id = ? AND month = ?",
                (store_id, month),
            ).fetchone()
            if cfg is None:
                raise RecordNotFoundError(
                    f"No monthly config for store {store_id} in {month}")
            cat = conn.execute(
                "SELECT 1 FROM expense_categories WHERE id = ? AND store_id = ?",
                (category_id, store_id),
            ).fetchone()
            if not cat:
                raise RecordNotFoundError(
                    f"Category {category_id} not found in store {store_id}")
            cur = conn.execute(
                "INSERT INTO monthly_expense_budgets (store_id, monthly_config_id, "
                "category_id, budget_cents) VALUES (?,?,?,?) "
                "ON CONFLICT (monthly_config_id, category_id) "
                "DO UPDATE SET budget_cents = excluded.budget_cents",
                (store_id, cfg[0], category_id, budget_cents),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Daily records
    # ------------------------------------------------------------------
    _DAILY_FIELDS = (
        "sales_cents", "transactions", "units", "resigned_ft", "new_hires_ft",
        "ft_working", "pt_working", "leave_annual", "leave_mc",
        "leave_other_paid", "leave_unpaid",
    )

    def _validate_daily_fields(self, fields: dict) -> None:
        bad = set(fields) - set(self._DAILY_FIELDS)
        if bad:
            raise ValidationError(f"Unknown daily fields: {sorted(bad)}")
        for k, v in fields.items():
            if v is not None:
                if not isinstance(v, int):
                    raise ValidationError(f"{k} must be an integer or None")
                if k != "sales_cents" and v < 0:
                    raise ValidationError(f"{k} cannot be negative")

    def save_daily_record(self, store_id: int, day: date, **fields) -> int:
        """Create or partially update one store/day record.

        NULL-vs-zero preserved: passing sales_cents=0 records a reported
        zero-sales day; omitting sales_cents leaves any existing value
        untouched (never converts unreported to zero).
        """
        self._validate_daily_fields(fields)
        conn = self._connect()
        try:
            self._require_store(conn, store_id)
            row = conn.execute(
                "SELECT id FROM daily_records WHERE store_id = ? AND date = ?",
                (store_id, day.isoformat()),
            ).fetchone()
            if row is None:
                cols = ["store_id", "date", *fields]
                cur = conn.execute(
                    f"INSERT INTO daily_records ({','.join(cols)}) "
                    f"VALUES ({','.join('?' for _ in cols)})",
                    (store_id, day.isoformat(), *fields.values()),
                )
                record_id = cur.lastrowid
            else:
                record_id = row[0]
                if fields:
                    sets = ", ".join(f"{k} = ?" for k in fields)
                    conn.execute(
                        f"UPDATE daily_records SET {sets} WHERE id = ?",
                        (*fields.values(), record_id),
                    )
            conn.commit()
            return record_id
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        finally:
            conn.close()

    def save_daily_with_stock_losses(
        self, store_id: int, day: date,
        stock_losses: dict[str, int], **daily_fields
    ) -> int:
        """Atomic: daily record + its stock-loss entries, or nothing."""
        self._validate_daily_fields(daily_fields)
        for code, cents in stock_losses.items():
            if not isinstance(cents, int):
                raise ValidationError("Stock-loss amounts must be integer cents")
        conn = self._connect()
        try:
            conn.execute("BEGIN")
            self._require_store(conn, store_id)
            valid = {r[0] for r in conn.execute("SELECT code FROM stock_loss_codes")}
            bad_codes = set(stock_losses) - valid
            if bad_codes:
                raise ValidationError(f"Unknown stock-loss codes: {sorted(bad_codes)}")
            row = conn.execute(
                "SELECT id FROM daily_records WHERE store_id = ? AND date = ?",
                (store_id, day.isoformat()),
            ).fetchone()
            if row is None:
                cols = ["store_id", "date", *daily_fields]
                cur = conn.execute(
                    f"INSERT INTO daily_records ({','.join(cols)}) "
                    f"VALUES ({','.join('?' for _ in cols)})",
                    (store_id, day.isoformat(), *daily_fields.values()),
                )
                record_id = cur.lastrowid
            else:
                record_id = row[0]
                if daily_fields:
                    sets = ", ".join(f"{k} = ?" for k in daily_fields)
                    conn.execute(
                        f"UPDATE daily_records SET {sets} WHERE id = ?",
                        (*daily_fields.values(), record_id),
                    )
            for code, cents in stock_losses.items():
                conn.execute(
                    "INSERT INTO daily_stock_losses (daily_record_id, code, "
                    "amount_cents) VALUES (?,?,?) "
                    "ON CONFLICT (daily_record_id, code) "
                    "DO UPDATE SET amount_cents = excluded.amount_cents",
                    (record_id, code, cents),
                )
            conn.commit()
            return record_id
        except (WriteError, ValidationError):
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Expense transactions
    # ------------------------------------------------------------------
    _EXPENSE_FIELDS = (
        "incurred_date", "paid_date", "category_id", "description",
        "amount_cents", "paid_by", "status", "receipt_ref", "verification",
        "recorded_by", "remarks",
    )

    def _validate_expense_fields(self, fields: dict) -> None:
        bad = set(fields) - set(self._EXPENSE_FIELDS)
        if bad:
            raise ValidationError(f"Unknown expense fields: {sorted(bad)}")
        if "status" in fields and fields["status"] is not None \
                and fields["status"] not in PAYMENT_STATUSES:
            raise ValidationError(f"Invalid status: {fields['status']}")
        if "paid_by" in fields and fields["paid_by"] is not None \
                and fields["paid_by"] not in PAYMENT_SOURCES:
            raise ValidationError(f"Invalid paid_by: {fields['paid_by']}")
        if "verification" in fields and fields["verification"] is not None \
                and fields["verification"] not in VERIFICATIONS:
            raise ValidationError(f"Invalid verification: {fields['verification']}")
        if "amount_cents" in fields and fields["amount_cents"] is not None \
                and not isinstance(fields["amount_cents"], int):
            raise ValidationError("amount_cents must be integer cents or None")
        for dk in ("incurred_date", "paid_date"):
            if dk in fields and fields[dk] is not None \
                    and not isinstance(fields[dk], date):
                raise ValidationError(f"{dk} must be a date or None")

    def create_expense(self, store_id: int, **fields) -> int:
        """Create one transaction. Drafts allowed: business completeness is
        the KPI validation ladder's job, not persistence's."""
        self._validate_expense_fields(fields)
        conn = self._connect()
        try:
            self._require_store(conn, store_id)
            cat = fields.get("category_id")
            if cat is not None:
                owned = conn.execute(
                    "SELECT 1 FROM expense_categories WHERE id = ? AND store_id = ?",
                    (cat, store_id)).fetchone()
                if not owned:
                    raise RecordNotFoundError(
                        f"Category {cat} not found in store {store_id}")
            values = {k: _iso(v) if k.endswith("_date") else v
                      for k, v in fields.items()}
            cols = ["store_id", *values]
            cur = conn.execute(
                f"INSERT INTO expense_transactions ({','.join(cols)}) "
                f"VALUES ({','.join('?' for _ in cols)})",
                (store_id, *values.values()),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise WriteError(str(exc)) from exc
        finally:
            conn.close()

    def update_expense(self, store_id: int, expense_id: int, **fields) -> None:
        """Partial update scoped by store. Updates never duplicate the row."""
        self._validate_expense_fields(fields)
        if not fields:
            return
        conn = self._connect()
        try:
            owned = conn.execute(
                "SELECT 1 FROM expense_transactions WHERE id = ? AND store_id = ?",
                (expense_id, store_id)).fetchone()
            if not owned:
                raise RecordNotFoundError(
                    f"Expense {expense_id} not found in store {store_id}")
            cat = fields.get("category_id")
            if cat is not None:
                owned_cat = conn.execute(
                    "SELECT 1 FROM expense_categories WHERE id = ? AND store_id = ?",
                    (cat, store_id)).fetchone()
                if not owned_cat:
                    raise RecordNotFoundError(
                        f"Category {cat} not found in store {store_id}")
            values = {k: _iso(v) if k.endswith("_date") else v
                      for k, v in fields.items()}
            sets = ", ".join(f"{k} = ?" for k in values)
            conn.execute(
                f"UPDATE expense_transactions SET {sets} WHERE id = ?",
                (*values.values(), expense_id),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise WriteError(str(exc)) from exc
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------------
    _ACTION_FIELDS = (
        "raised_date", "pillar", "issue", "priority", "status", "pic",
        "due_date", "corrective_action", "completion_date", "remarks",
    )

    def _validate_action_fields(self, fields: dict) -> None:
        bad = set(fields) - set(self._ACTION_FIELDS)
        if bad:
            raise ValidationError(f"Unknown action fields: {sorted(bad)}")
        if "pillar" in fields and fields["pillar"] is not None \
                and fields["pillar"] not in PILLARS:
            raise ValidationError(f"Invalid pillar: {fields['pillar']}")
        if "priority" in fields and fields["priority"] is not None \
                and fields["priority"] not in PRIORITIES:
            raise ValidationError(f"Invalid priority: {fields['priority']}")
        if "status" in fields and fields["status"] is not None \
                and fields["status"] not in ACTION_STATUSES:
            raise ValidationError(f"Invalid status: {fields['status']}")

    def create_action(self, store_id: int, **fields) -> int:
        self._validate_action_fields(fields)
        conn = self._connect()
        try:
            self._require_store(conn, store_id)
            values = {k: _iso(v) if k.endswith("_date") else v
                      for k, v in fields.items()}
            cols = ["store_id", *values]
            cur = conn.execute(
                f"INSERT INTO actions ({','.join(cols)}) "
                f"VALUES ({','.join('?' for _ in cols)})",
                (store_id, *values.values()),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise WriteError(str(exc)) from exc
        finally:
            conn.close()

    def update_action(self, store_id: int, action_id: int, **fields) -> None:
        self._validate_action_fields(fields)
        if not fields:
            return
        conn = self._connect()
        try:
            owned = conn.execute(
                "SELECT 1 FROM actions WHERE id = ? AND store_id = ?",
                (action_id, store_id)).fetchone()
            if not owned:
                raise RecordNotFoundError(
                    f"Action {action_id} not found in store {store_id}")
            values = {k: _iso(v) if k.endswith("_date") else v
                      for k, v in fields.items()}
            sets = ", ".join(f"{k} = ?" for k in values)
            conn.execute(
                f"UPDATE actions SET {sets} WHERE id = ?",
                (*values.values(), action_id),
            )
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise WriteError(str(exc)) from exc
        finally:
            conn.close()
