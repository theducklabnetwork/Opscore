"""Authenticated application-service boundary.

All UI-driven operations enter through AppService, which resolves the
caller from a session token and enforces authorization (authz) *before*
delegating to the existing read/write/snapshot services. Client-supplied
user IDs, roles, or store IDs are never trusted.

The underlying repositories remain user-unaware; kpi.py stays pure.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Optional

from ducklab import authz, store_ops_db
from ducklab.auth_service import AuthService, User
from ducklab.operations_repository import OperationsRepository
from ducklab.operations_service import MonthlyOperationsReport
from ducklab.operations_snapshots import SnapshotService
from ducklab.operations_write_service import OperationsWriteService


class AppService:
    def __init__(self, db_path: Path | str = store_ops_db.DB_PATH):
        self.auth = AuthService(db_path)
        self.read_repo = OperationsRepository(db_path)
        self.write = OperationsWriteService(db_path)
        self.snapshots = SnapshotService(db_path)

    # -- session helpers ---------------------------------------------------
    def authenticate(self, token: str) -> User:
        return self.auth.authenticate_token(token)

    # -- store listing ------------------------------------------------------
    def list_my_stores(self, user: User) -> list[dict]:
        """Stores the authenticated user may see (never trusts client input)."""
        authz.require_active(user)
        conn = store_ops_db.connect(self.read_repo._db_path)
        try:
            if user.is_super_admin:
                rows = conn.execute(
                    "SELECT id, code, name FROM stores ORDER BY code").fetchall()
            else:
                rows = conn.execute(
                    "SELECT s.id, s.code, s.name FROM stores s "
                    "JOIN auth_user_store_assignments a ON a.store_id = s.id "
                    "WHERE a.user_id = ? AND a.is_active = 1 ORDER BY s.code",
                    (user.id,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    # -- reads ---------------------------------------------------------------
    def preview_month(self, user: User, store_id: int, month: str,
                      as_of: Optional[date] = None) -> MonthlyOperationsReport:
        authz.require_store_access(user, store_id)
        return self.snapshots.preview_month(store_id, month, as_of=as_of)

    def get_finalized_report(self, user: User, store_id: int, month: str,
                             version: Optional[int] = None) -> dict:
        authz.require_store_access(user, store_id)
        return self.snapshots.get_finalized(store_id, month, version)

    def list_report_versions(self, user: User, store_id: int,
                             month: str) -> list[dict]:
        authz.require_store_access(user, store_id)
        return self.snapshots.list_versions(store_id, month)

    # -- snapshots (protected separately from ordinary viewing) ---------------
    def finalize_month(self, user: User, store_id: int, month: str,
                       as_of: Optional[date] = None) -> dict:
        authz.require_store_finalize(user, store_id)
        return self.snapshots.finalize_month(store_id, month, as_of=as_of)

    def correct_month(self, user: User, store_id: int, month: str,
                      as_of: Optional[date] = None) -> dict:
        authz.require_store_finalize(user, store_id)
        return self.snapshots.correct_month(store_id, month, as_of=as_of)

    # -- operational writes ---------------------------------------------------
    def list_stock_loss_codes(self, user: User) -> list[dict]:
        """Reference data: all 13 codes with contribution signs (read-only)."""
        authz.require_active(user)
        return [
            {"code": c.code, "name": c.name,
             "contribution_sign": c.contribution_sign}
            for c in self.read_repo.get_stock_loss_codes()
        ]

    def get_daily_records(self, user: User, store_id: int,
                          month: str) -> list:
        """Saved daily records for one store/month (read-only)."""
        authz.require_store_access(user, store_id)
        return self.read_repo.get_daily_records(store_id, month)

    def get_month_config(self, user: User, store_id: int, month: str):
        authz.require_store_access(user, store_id)
        return self.read_repo.get_month_config(store_id, month)

    def record_daily(self, user: User, store_id: int, day: date, **fields):
        authz.require_store_write(user, store_id)
        return self.write.record_daily(store_id, day, **fields)

    def correct_daily(self, user: User, store_id: int, day: date, **fields):
        authz.require_store_write(user, store_id)
        return self.write.correct_daily(store_id, day, **fields)

    def record_expense(self, user: User, store_id: int, **fields):
        authz.require_store_write(user, store_id)
        return self.write.record_expense(store_id, **fields)

    def update_expense(self, user: User, store_id: int, expense_id: int,
                       **fields):
        authz.require_store_write(user, store_id)
        self.write.update_expense(store_id, expense_id, **fields)

    def get_expense_transactions(self, user: User, store_id: int):
        authz.require_store_access(user, store_id)
        return self.read_repo.get_expense_transactions(store_id)

    def get_expense_categories(self, user: User, store_id: int):
        authz.require_store_access(user, store_id)
        return self.read_repo.get_expense_categories(store_id)

    def get_expense_budgets(self, user: User, store_id: int, month: str):
        authz.require_store_access(user, store_id)
        return self.read_repo.get_expense_budgets(store_id, month)

    def add_expense_category(self, user: User, store_id: int, name: str,
                             **fields) -> int:
        authz.require_config_manager(user)
        authz.require_store_access(user, store_id)
        return self.write.add_expense_category(store_id, name, **fields)

    def rename_expense_category(self, user: User, store_id: int,
                                category_id: int, name: str) -> None:
        authz.require_config_manager(user)
        authz.require_store_access(user, store_id)
        self.write.rename_expense_category(store_id, category_id, name)

    def set_expense_budget(self, user: User, store_id: int, month: str,
                           category_id: int, budget_cents) -> None:
        authz.require_config_manager(user)
        authz.require_store_access(user, store_id)
        self.write.set_expense_budget(store_id, month, category_id,
                                      budget_cents)

    def raise_action(self, user: User, store_id: int, raised_date: date,
                     pillar: str, description: str, priority: str,
                     **fields):
        authz.require_store_write(user, store_id)
        return self.write.raise_action(store_id, raised_date, pillar,
                                       description, priority,
                                       actor_id=user.id, **fields)

    def save_action(self, user: User, store_id: int, action_id,
                    today: date, **fields):
        """Create/update with workbook validation; store-scoped write."""
        authz.require_store_write(user, store_id)
        return self.write.save_action(store_id, user.id, action_id, today,
                                      **fields)

    def get_actions(self, user: User, store_id: int, month=None):
        authz.require_store_access(user, store_id)
        return self.read_repo.get_actions(store_id, month)

    # -- configuration ----------------------------------------------------------
    def configure_month(self, user: User, store_id: int, month: str, **fields):
        authz.require_config_manager(user)
        authz.require_store_access(user, store_id)
        return self.write.configure_month(store_id, month, **fields)

    def record_payroll(self, user: User, store_id: int, month: str,
                       salary_basic_allowance_cents, overtime_cents,
                       support_team_cost_cents=None) -> dict:
        """Month-end payroll entry. Restricted to configuration managers;
        None means awaiting payroll (explicit NULL, never coerced to 0)."""
        authz.require_config_manager(user)
        authz.require_store_access(user, store_id)
        return self.write.record_payroll(
            store_id, month, salary_basic_allowance_cents, overtime_cents,
            support_team_cost_cents)

    def register_store(self, user: User, code: str, name: str, **details):
        authz.require_config_manager(user)
        return self.write.register_store(code, name, **details)

    def update_store_details(self, user: User, store_id: int, **fields):
        """Edit store details: config managers with access to the store."""
        authz.require_config_manager(user)
        authz.require_store_access(user, store_id)
        self.write.update_store_details(store_id, **fields)

    def set_store_active(self, user: User, store_id: int, active: bool):
        """Activate/deactivate (never delete): config managers with access."""
        authz.require_config_manager(user)
        authz.require_store_access(user, store_id)
        if active:
            self.write.activate_store(store_id)
        else:
            self.write.deactivate_store(store_id)

    # -- user administration ------------------------------------------------------
    def create_user(self, actor: User, email: str, display_name: str,
                    password: str) -> User:
        authz.require_user_manager(actor)
        return self.auth.create_user(email, display_name, password, actor=actor)

    def list_users(self, actor: User) -> list[User]:
        authz.require_user_manager(actor)
        return self.auth.list_users()

    def set_user_active(self, actor: User, user_id: int, active: bool) -> User:
        authz.require_user_manager(actor)
        if actor.id == user_id and not active:
            from ducklab.auth_service import ValidationError
            raise ValidationError("Cannot disable your own account")
        return self.auth.set_active(actor, user_id, active)

    def assign_role(self, actor: User, user_id: int, role: str) -> User:
        authz.require_role_grantable(actor, role)
        return self.auth.assign_role(actor, user_id, role)

    def revoke_role(self, actor: User, user_id: int, role: str) -> User:
        authz.require_user_manager(actor)
        return self.auth.revoke_role(actor, user_id, role)

    def assign_store(self, actor: User, user_id: int, store_id: int) -> User:
        authz.require_store_grantable(actor, store_id)
        return self.auth.assign_store(actor, user_id, store_id)

    def revoke_store(self, actor: User, user_id: int, store_id: int) -> User:
        authz.require_store_grantable(actor, store_id)
        return self.auth.revoke_store(actor, user_id, store_id)

    def change_password(self, actor: User, user_id: int, new_password: str,
                        **kwargs) -> None:
        if actor.id == user_id:
            kwargs.setdefault("self_change", True)
        else:
            authz.require_user_manager(actor)
        return self.auth.change_password(actor, user_id, new_password, **kwargs)

    def revoke_sessions(self, actor: User, user_id: int) -> None:
        if actor.id != user_id:
            authz.require_user_manager(actor)
        return self.auth.revoke_user_sessions(actor, user_id)
