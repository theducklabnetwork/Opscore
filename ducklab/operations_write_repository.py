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
from datetime import date, datetime
from zoneinfo import ZoneInfo

# Audit timestamps: explicit Asia/Kuala_Lumpur offset (+08:00), never
# naive local time.
KL_TZ = ZoneInfo("Asia/Kuala_Lumpur")
from pathlib import Path
from typing import Optional

from ducklab import kpi, store_ops_db
from ducklab.expense_category_defaults import DEFAULT_EXPENSE_CATEGORIES
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
# Store dropdowns; matched to the Daily Sales Contribution by exact text.
STATES = ("Johor", "Kedah", "Kelantan", "Melaka", "N9", "Pahang", "Penang",
          "Perak", "Perlis", "Sabah", "Sarawak", "Selangor", "Terengganu",
          "WP")
CONCEPTS = ("Outlet", "Mall", "Retail", "Mini Mall", "Ringgit Mall", "Toys",
            "Express Toys")


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


def _check_budget_pct(category_id: int, pct) -> None:
    """Budget rule shared by both budget writers: a fraction of the active
    sales target (0.025 = 2.5%) or None (no budget entered). Non-negative,
    at most 100% (1.0)."""
    if pct is None:
        return
    if isinstance(pct, bool) or not isinstance(pct, (int, float)):
        raise ValidationError("budget_pct must be a number or None")
    if not (0 <= float(pct) <= 1):
        raise ValidationError(
            f"Budget for category {category_id} must be between 0% and 100%")


# Level 1 base target + the three uplifts that derive Levels 2-4.
LEVEL_FIELDS = ("level1_base_cents", "level2_pct", "level3_pct", "level4_pct")

# Config fields that hold money (cents) or headcounts: never negative.
_NON_NEGATIVE_CONFIG = (
    "min_rtm_target_cents", "level1_base_cents", "sales_last_year_cents",
    "sales_last_month_cents", "salary_basic_allowance_cents",
    "overtime_cents", "support_team_cost_cents", "budget_headcount",
    "bd_target_cents",
    "opening_headcount_ft", "target_transactions", "target_spr_cents",
)


def _check_config_values(fields: dict) -> None:
    """Range checks shared by both config writers (Manpower + Setup)."""
    for name in _NON_NEGATIVE_CONFIG:
        v = fields.get(name)
        if v is not None and v < 0:
            raise ValidationError(f"{name} cannot be negative")
    for pct in ("level2_pct", "level3_pct", "level4_pct",
                "salary_ratio_target", "turnover_threshold",
                "stock_loss_target", "part_timer_factor"):
        v = fields.get(pct)
        if v is not None and not (0 <= float(v) <= 10):
            raise ValidationError(f"{pct} out of range: {v}")
    v = fields.get("target_ipr")
    if v is not None and not (0 <= float(v) <= 1000):
        raise ValidationError(f"target_ipr out of range: {v}")


def _check_store_choices(fields: dict) -> None:
    """State / Concept must be one of the dropdown options (or blank).
    A stored legacy concept stays until the store is edited."""
    if fields.get("state") is not None and fields["state"] not in STATES:
        raise ValidationError("State: pick an option from the list")
    if fields.get("concept") is not None \
            and fields["concept"] not in CONCEPTS:
        raise ValidationError("Concept: pick an option from the list")


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
                     branch_manager: Optional[str] = None,
                     state: Optional[str] = None) -> int:
        if not code or not name:
            raise ValidationError("Store code and name are required")
        _check_store_choices({"state": state, "concept": concept})
        conn = self._connect()
        try:
            cur = conn.execute(
                "INSERT INTO stores (code, name, concept, zone, zone_pic, "
                "branch_manager, state) VALUES (?,?,?,?,?,?,?)",
                (code, name, concept, zone, zone_pic, branch_manager, state),
            )
            store_id = cur.lastrowid
            # Seed the ten default expense categories immediately so that a
            # fresh store never starts with an empty Expenses module. This
            # runs in the same transaction as the store insert.
            conn.executemany(
                "INSERT INTO expense_categories (store_id, name, sort_order) "
                "VALUES (?, ?, ?)",
                [(store_id, cat_name, order)
                 for order, (cat_name, _pct)
                 in enumerate(DEFAULT_EXPENSE_CATEGORIES)],
            )
            conn.commit()
            return store_id
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        except sqlite3.OperationalError as exc:  # e.g. database is locked
            conn.rollback()
            raise WriteError(f"Database operational error: {exc}") from exc
        finally:
            conn.close()

    def update_store(self, store_id: int, **fields) -> None:
        """Partial update of store details (name/concept/zone/zone_pic/
        branch_manager/state). Unknown fields rejected."""
        allowed = {"name", "concept", "zone", "zone_pic", "branch_manager",
                   "state"}
        bad = set(fields) - allowed
        if bad:
            raise ValidationError(f"Unknown store fields: {sorted(bad)}")
        _check_store_choices(fields)
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
        "target_transactions", "target_spr_cents", "target_ipr",
        "bd_target_cents",
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
        _check_config_values(fields)
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

    def get_level_fields(self, store_id: int, month: str) -> dict:
        """Saved Level 1 base + Level 2-4 uplifts for one store/month
        ({} when the month has no config yet)."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT level1_base_cents, level2_pct, level3_pct, level4_pct "
                "FROM monthly_configs WHERE store_id = ? AND month = ?",
                (store_id, month)).fetchone()
        finally:
            conn.close()
        if row is None:
            return {}
        return dict(zip(LEVEL_FIELDS, row))

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

    def save_month_config_with_budgets(self, store_id: int, month: str,
                                       fields: dict,
                                       budgets: dict[int, Optional[float]],
                                       holidays: Optional[list[date]] = None,
                                       ) -> dict:
        """Atomic: monthly config + per-category budgets (+ the month's
        public holidays, replaced as a set; None = unchanged) in ONE
        transaction — either everything is written or nothing."""
        _check_month(month)
        for d in holidays or []:
            if not isinstance(d, date) or d.isoformat()[:7] != month:
                raise ValidationError(
                    f"Public holiday {d} is not in {month}")
        bad = set(fields) - set(self._CONFIG_FIELDS)
        if bad:
            raise ValidationError(f"Unknown config fields: {sorted(bad)}")
        _check_config_values(fields)
        for cat_id, pct in budgets.items():
            _check_budget_pct(cat_id, pct)
        conn = self._connect()
        try:
            conn.execute("BEGIN")
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
            for cat_id, pct in budgets.items():
                cat = conn.execute(
                    "SELECT 1 FROM expense_categories WHERE id = ? AND store_id = ?",
                    (cat_id, store_id),
                ).fetchone()
                if cat is None:
                    raise RecordNotFoundError(
                        f"Unknown expense category {cat_id} for this store")
                # Same table/columns/conflict rule as save_expense_budget
                # (the Expenses page writer).
                conn.execute(
                    "INSERT INTO monthly_expense_budgets (store_id, "
                    "monthly_config_id, category_id, budget_pct) "
                    "VALUES (?,?,?,?) "
                    "ON CONFLICT (monthly_config_id, category_id) "
                    "DO UPDATE SET budget_pct = excluded.budget_pct",
                    (store_id, config_id, cat_id, pct),
                )
            if holidays is not None:
                start, month_end, _ = kpi.month_bounds(month)
                next_month = month_end.fromordinal(month_end.toordinal() + 1)
                conn.execute(
                    "DELETE FROM public_holidays WHERE store_id = ? "
                    "AND date >= ? AND date < ?",
                    (store_id, start.isoformat(), next_month.isoformat()))
                conn.executemany(
                    "INSERT INTO public_holidays (store_id, date) "
                    "VALUES (?, ?)",
                    [(store_id, d.isoformat()) for d in sorted(set(holidays))])
                self._sync_abm_holidays(conn, store_id)
            conn.commit()
            return {
                "config_id": config_id,
                "finalized_snapshot_exists": self.finalized_snapshot_exists(
                    store_id, month),
            }
        except (WriteError, ValidationError, RecordNotFoundError):
            conn.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise WriteError(str(exc)) from exc
        except sqlite3.Error as exc:
            conn.rollback()
            raise WriteError(f"Database error: {exc}") from exc
        finally:
            conn.close()

    def save_expense_budget(self, store_id: int, month: str, category_id: int,
                            budget_pct: Optional[float]) -> int:
        """Create/update one category budget for a store/month. Idempotent
        on UNIQUE(monthly_config_id, category_id); never duplicates."""
        _check_month(month)
        _check_budget_pct(category_id, budget_pct)
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
                "category_id, budget_pct) VALUES (?,?,?,?) "
                "ON CONFLICT (monthly_config_id, category_id) "
                "DO UPDATE SET budget_pct = excluded.budget_pct",
                (store_id, cfg[0], category_id, budget_pct),
            )
            conn.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError(str(exc)) from exc
        except sqlite3.Error as exc:
            conn.rollback()
            raise WriteError(f"Database error: {exc}") from exc
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Daily records
    # ------------------------------------------------------------------
    _DAILY_FIELDS = (
        "sales_cents", "transactions", "units", "resigned_ft", "new_hires_ft",
        "ft_working", "pt_working", "leave_annual", "leave_mc",
        "leave_other_paid", "leave_emergency", "leave_unpaid",
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
        stock_losses: dict[str, int],
        stock_remove: Optional[list[str]] = None, **daily_fields
    ) -> int:
        """Atomic: daily record + its stock-loss entries, or nothing.

        ``stock_remove`` codes are DELETEd back to "no entry" (NULL) —
        never written as 0."""
        self._validate_daily_fields(daily_fields)
        for code, cents in stock_losses.items():
            if not isinstance(cents, int):
                raise ValidationError("Stock-loss amounts must be integer cents")
            if cents < 0:
                raise ValidationError(
                    f"Stock-loss amount for {code} cannot be negative "
                    f"(offsets are recorded via Extra, not negative values)")
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
            for code in stock_remove or []:
                conn.execute(
                    "DELETE FROM daily_stock_losses "
                    "WHERE daily_record_id = ? AND code = ?",
                    (record_id, code),
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
    # Manpower schedule
    # ------------------------------------------------------------------
    @staticmethod
    def _require_text(value, label: str) -> str:
        value = (value or "").strip()
        if not value:
            raise ValidationError(f"{label} is required")
        return value

    def save_staff(self, store_id: int, staff_id: Optional[int], name: str,
                   staff_code: str, position: str,
                   employment_type: str = "Not Set") -> int:
        name = self._require_text(name, "Name")
        staff_code = self._require_text(staff_code, "Staff ID")
        position = self._require_text(position, "Position")
        if employment_type not in ("Full-Time", "Part-Time", "Not Set"):
            raise ValidationError("Employment Type must be Full-Time, Part-Time, or Not Set")
        conn = self._connect()
        try:
            if staff_id is None:
                cur = conn.execute(
                    "INSERT INTO staff (store_id, name, staff_code, position, employment_type) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (store_id, name, staff_code, position, employment_type))
                staff_id = cur.lastrowid
            else:
                cur = conn.execute(
                    "UPDATE staff SET name = ?, staff_code = ?, position = ?, "
                    "employment_type = ? WHERE id = ? AND store_id = ?",
                    (name, staff_code, position, employment_type, staff_id, store_id))
                if not cur.rowcount:
                    raise RecordNotFoundError("Staff member not found in this store")
            self._sync_abm_holidays(conn, store_id)
            conn.commit()
            return staff_id
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError("Staff ID already exists in this store") from exc
        finally:
            conn.close()

    @staticmethod
    def _sync_abm_holidays(conn: sqlite3.Connection, store_id: int) -> None:
        """Enforce OPH on ABM holiday cells; remove only automatic OPH."""
        conn.execute(
            "DELETE FROM schedule_entries WHERE store_id = ? "
            "AND assignment_source = 'auto_holiday' AND ("
            "code != 'OPH' OR work_date NOT IN "
            "(SELECT date FROM public_holidays WHERE store_id = ?) OR "
            "staff_id NOT IN (SELECT id FROM staff WHERE store_id = ? "
            "AND position = 'ABM'))",
            (store_id, store_id, store_id))
        conn.execute(
            "INSERT INTO schedule_entries "
            "(store_id, staff_id, work_date, code, assignment_source) "
            "SELECT s.store_id, s.id, h.date, 'OPH', 'auto_holiday' "
            "FROM staff s JOIN public_holidays h ON h.store_id = s.store_id "
            "WHERE s.store_id = ? AND s.position = 'ABM' "
            "ON CONFLICT(staff_id, work_date) DO UPDATE SET "
            "code = 'OPH', assignment_source = 'auto_holiday'",
            (store_id,))

    def delete_staff(self, store_id: int, staff_id: int) -> None:
        cur = self._execute(
            "DELETE FROM staff WHERE id = ? AND store_id = ?",
            (staff_id, store_id))
        if not cur.rowcount:
            raise RecordNotFoundError("Staff member not found in this store")

    def save_shift_code(self, original_code: Optional[str], code: str,
                        label: str, start_time: Optional[str],
                        end_time: Optional[str], color: str) -> None:
        code = self._require_text(code, "Code").upper()
        label = self._require_text(label, "Label")
        color = self._require_text(color, "Color")
        if bool(start_time) != bool(end_time):
            raise ValidationError("Start and End must both be set or both be blank")
        for value, field in ((start_time, "Start"), (end_time, "End")):
            if value:
                try:
                    datetime.strptime(value, "%H:%M")
                except ValueError:
                    raise ValidationError(f"{field} must be a valid time") from None
        conn = self._connect()
        try:
            if original_code is None:
                conn.execute(
                    "INSERT INTO shift_codes "
                    "(code, label, start_time, end_time, color) VALUES (?, ?, ?, ?, ?)",
                    (code, label, start_time or None, end_time or None, color))
            else:
                cur = conn.execute(
                    "UPDATE shift_codes SET label = ?, start_time = ?, "
                    "end_time = ?, color = ? WHERE code = ?",
                    (label, start_time or None, end_time or None, color,
                     original_code))
                if not cur.rowcount:
                    raise RecordNotFoundError("Shift code not found")
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise DuplicateRecordError("Shift code already exists") from exc
        finally:
            conn.close()

    def set_shift_code_legend(self, code: str, included: bool) -> None:
        cur = self._execute(
            "UPDATE shift_codes SET show_in_legend = ? WHERE code = ?",
            (int(bool(included)), code))
        if not cur.rowcount:
            raise RecordNotFoundError("Shift code not found")

    def delete_shift_code(self, code: str) -> None:
        cur = self._execute("DELETE FROM shift_codes WHERE code = ?", (code,))
        if not cur.rowcount:
            raise RecordNotFoundError("Shift code not found")

    def set_schedule_entry(self, store_id: int, staff_id: int,
                           work_date: date, code: str) -> None:
        conn = self._connect()
        try:
            owned = conn.execute(
                "SELECT 1 FROM staff WHERE id = ? AND store_id = ?",
                (staff_id, store_id)).fetchone()
            if not owned:
                raise RecordNotFoundError("Staff member not found in this store")
            holiday_abm = conn.execute(
                "SELECT 1 FROM staff s JOIN public_holidays h "
                "ON h.store_id = s.store_id AND h.date = ? "
                "WHERE s.id = ? AND s.store_id = ? AND s.position = 'ABM'",
                (work_date.isoformat(), staff_id, store_id)).fetchone()
            if holiday_abm:
                code = "OPH"
            source = "auto_holiday" if holiday_abm else "manual"
            if code:
                conn.execute(
                    "INSERT INTO schedule_entries "
                    "(store_id, staff_id, work_date, code, assignment_source) "
                    "VALUES (?, ?, ?, ?, ?) "
                    "ON CONFLICT(staff_id, work_date) DO UPDATE SET "
                    "code = excluded.code, assignment_source = excluded.assignment_source",
                    (store_id, staff_id, work_date.isoformat(), code, source))
            else:
                conn.execute(
                    "DELETE FROM schedule_entries WHERE store_id = ? "
                    "AND staff_id = ? AND work_date = ?",
                    (store_id, staff_id, work_date.isoformat()))
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            raise ValidationError("Unknown shift code or invalid schedule entry") from exc
        finally:
            conn.close()

    def set_schedule_target(self, store_id: int, week_start: date,
                            target: Optional[int]) -> None:
        if target is not None and target < 0:
            raise ValidationError("Target Manpower cannot be negative")
        conn = self._connect()
        try:
            if target is None:
                conn.execute(
                    "DELETE FROM schedule_targets WHERE store_id = ? AND week_start = ?",
                    (store_id, week_start.isoformat()))
            else:
                conn.execute(
                    "INSERT INTO schedule_targets "
                    "(store_id, week_start, target_manpower) VALUES (?, ?, ?) "
                    "ON CONFLICT(store_id, week_start) "
                    "DO UPDATE SET target_manpower = excluded.target_manpower",
                    (store_id, week_start.isoformat(), target))
            conn.commit()
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

    def create_action(self, store_id: int, actor_id: Optional[int] = None,
                      **fields) -> int:
        self._validate_action_fields(fields)
        conn = self._connect()
        try:
            self._require_store(conn, store_id)
            values = {k: _iso(v) if k.endswith("_date") else v
                      for k, v in fields.items()}
            values["created_by"] = actor_id
            values["created_at"] = datetime.now(KL_TZ).isoformat(timespec="seconds")
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

    def update_action(self, store_id: int, action_id: int,
                      actor_id: Optional[int] = None, **fields) -> None:
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
            values["updated_by"] = actor_id
            values["updated_at"] = datetime.now(KL_TZ).isoformat(timespec="seconds")
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
