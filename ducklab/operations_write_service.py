"""Write-side service layer for Store Operations Monitoring.

Application-facing validation and orchestration over
OperationsWriteRepository. No KPI formulas live here; after writes, the
existing read repository + operations_service.assemble_month recalculate
everything from source values.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from ducklab import store_ops_db
from ducklab.operations_write_repository import (
    LEVEL_FIELDS, OperationsWriteRepository, ValidationError, WriteError,
)

LEVELS_REQUIRED = ("Levels 2–4 uplift is required when a Level 1 target "
                   "is set")


class OperationsWriteService:
    def __init__(self, db_path: Path | str = store_ops_db.DB_PATH):
        self.repo = OperationsWriteRepository(db_path)

    # -- stores ---------------------------------------------------------
    def register_store(self, code: str, name: str, **details) -> int:
        return self.repo.create_store(code.strip(), name.strip(), **details)

    def update_store_details(self, store_id: int, **fields) -> None:
        self.repo.update_store(store_id, **fields)

    def deactivate_store(self, store_id: int) -> None:
        """Deactivation preserves every historical record (no deletion)."""
        self.repo.set_store_active(store_id, False)

    def activate_store(self, store_id: int) -> None:
        self.repo.set_store_active(store_id, True)

    # -- monthly configuration -------------------------------------------
    def _require_four_levels(self, store_id: int, month: str,
                             fields: dict) -> None:
        """Four levels required: a save that writes the Level 1 base or an
        uplift must leave the month with either no Level 1 target, or a
        Level 1 target AND all three uplifts (saved values + this save).
        Saves that touch no level field (payroll, stock target, budgets)
        leave older months without uplift unchanged and are allowed."""
        if not any(k in fields for k in LEVEL_FIELDS):
            return
        merged = {**self.repo.get_level_fields(store_id, month),
                  **{k: fields[k] for k in LEVEL_FIELDS if k in fields}}
        if merged.get("level1_base_cents") is not None and any(
                merged.get(k) is None for k in LEVEL_FIELDS[1:]):
            raise ValidationError(LEVELS_REQUIRED)

    def configure_month(self, store_id: int, month: str, **fields) -> dict:
        """Save config; report a risk flag if a finalized snapshot exists
        for this store/month (the snapshot itself is never touched)."""
        self._require_four_levels(store_id, month, fields)
        config_id = self.repo.save_month_config(store_id, month, **fields)
        return {
            "config_id": config_id,
            "finalized_snapshot_exists": self.repo.finalized_snapshot_exists(
                store_id, month),
        }

    def record_payroll(self, store_id: int, month: str,
                       salary_basic_allowance_cents: Optional[int],
                       overtime_cents: Optional[int],
                       support_team_cost_cents: Optional[int] = None) -> dict:
        """Month-end payroll inputs; None means still awaiting payroll."""
        return self.configure_month(
            store_id, month,
            salary_basic_allowance_cents=salary_basic_allowance_cents,
            overtime_cents=overtime_cents,
            support_team_cost_cents=support_team_cost_cents,
        )

    def configure_month_with_budgets(self, store_id: int, month: str,
                                     fields: dict,
                                     budgets: dict[int, Optional[int]],
                                     holidays: Optional[list[date]] = None,
                                     ) -> dict:
        """Atomic monthly setup save: config + budgets (+ public
        holidays) in one transaction."""
        self._require_four_levels(store_id, month, fields)
        return self.repo.save_month_config_with_budgets(
            store_id, month, fields, budgets, holidays)

    # -- categories & budgets ---------------------------------------------
    def add_expense_category(self, store_id: int, name: str,
                             sort_order: int = 0) -> int:
        return self.repo.create_expense_category(store_id, name.strip(),
                                                 sort_order)

    def rename_expense_category(self, store_id: int, category_id: int,
                                new_name: str) -> None:
        self.repo.update_expense_category(store_id, category_id,
                                          name=new_name.strip())

    def set_expense_budget(self, store_id: int, month: str, category_id: int,
                           budget_cents: Optional[int]) -> int:
        return self.repo.save_expense_budget(store_id, month, category_id,
                                             budget_cents)

    # -- daily operations ---------------------------------------------------
    def record_daily(self, store_id: int, day: date,
                     stock_losses: Optional[dict[str, int]] = None,
                     stock_remove: Optional[list[str]] = None,
                     **daily_fields) -> int:
        """Save one day (optionally with stock losses, atomically)."""
        if stock_losses or stock_remove:
            return self.repo.save_daily_with_stock_losses(
                store_id, day, stock_losses or {},
                stock_remove=stock_remove, **daily_fields)
        return self.repo.save_daily_record(store_id, day, **daily_fields)

    def correct_daily(self, store_id: int, day: date, **daily_fields) -> int:
        """Partial correction: only supplied fields change."""
        return self.repo.save_daily_record(store_id, day, **daily_fields)

    # -- expenses -----------------------------------------------------------
    def record_expense(self, store_id: int, **fields) -> int:
        return self.repo.create_expense(store_id, **fields)

    def mark_expense_paid(self, store_id: int, expense_id: int,
                          paid_date: date, paid_by: str) -> None:
        """Pending -> Paid. One row updated; never duplicated."""
        self.repo.update_expense(store_id, expense_id, status="Paid",
                                 paid_date=paid_date, paid_by=paid_by)

    def cancel_expense(self, store_id: int, expense_id: int) -> None:
        self.repo.update_expense(store_id, expense_id, status="Cancelled")

    def verify_expense(self, store_id: int, expense_id: int,
                       verification: str,
                       receipt_ref: Optional[str] = None) -> None:
        fields: dict = {"verification": verification}
        if receipt_ref is not None:
            fields["receipt_ref"] = receipt_ref
        self.repo.update_expense(store_id, expense_id, **fields)

    def update_expense(self, store_id: int, expense_id: int, **fields) -> None:
        self.repo.update_expense(store_id, expense_id, **fields)

    # -- actions --------------------------------------------------------------
    def raise_action(self, store_id: int, raised_date: date, pillar: str,
                     issue: str, priority: str, pic: Optional[str] = None,
                     due_date: Optional[date] = None,
                     corrective_action: Optional[str] = None,
                     actor_id: Optional[int] = None) -> int:
        return self.repo.create_action(
            store_id, actor_id, raised_date=raised_date, pillar=pillar,
            issue=issue, priority=priority, pic=pic, due_date=due_date,
            corrective_action=corrective_action, status="Open")

    def save_action(self, store_id: int, actor_id: int,
                    action_id: Optional[int], today: date,
                    raised_date: Optional[date], pillar: Optional[str],
                    issue: Optional[str], priority: Optional[str],
                    status: Optional[str], pic: Optional[str],
                    due_date: Optional[date], corrective_action: Optional[str],
                    completion_date: Optional[date],
                    remarks: Optional[str]) -> int:
        """Create or fully update an action, enforcing the workbook rules:
        owner and due date required; Done requires a completion date
        (defaults to today); non-Done clears completion; completion cannot
        precede raised. `today` is injected (Asia/Kuala_Lumpur at caller)."""
        if not issue or not issue.strip():
            raise ValidationError("Issue is required")
        if not pic or not pic.strip():
            raise ValidationError("Owner is required")
        if due_date is None:
            raise ValidationError("Due is required")
        status = status or "Open"
        if status == "Done":
            if completion_date is None:
                completion_date = today
        else:
            completion_date = None  # non-Done never keeps a completion date
        if completion_date is not None and raised_date is not None \
                and completion_date < raised_date:
            raise ValidationError(
                "Completed cannot be earlier than Raised")
        fields = dict(raised_date=raised_date or today, pillar=pillar,
                      issue=issue.strip(), priority=priority, status=status,
                      pic=pic.strip(), due_date=due_date,
                      corrective_action=corrective_action,
                      completion_date=completion_date, remarks=remarks)
        if action_id is None:
            return self.repo.create_action(store_id, actor_id, **fields)
        self.repo.update_action(store_id, action_id, actor_id, **fields)
        return action_id

    def update_action_status(self, store_id: int, action_id: int,
                             status: str,
                             completion_date: Optional[date] = None,
                             actor_id: Optional[int] = None) -> None:
        fields: dict = {"status": status}
        if completion_date is not None:
            fields["completion_date"] = completion_date
        self.repo.update_action(store_id, action_id, actor_id, **fields)

    def update_action(self, store_id: int, action_id: int, **fields) -> None:
        self.repo.update_action(store_id, action_id, None, **fields)
