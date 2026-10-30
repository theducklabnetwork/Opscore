"""Store user-assignment state (Store Management workflow).

All operations go through AppService with the session token revalidated on
every event; user assignment is super_admin only. Identity never comes from
client input.
"""

from __future__ import annotations

import reflex as rx

from ducklab import authz
from ducklab.input_validation import parse_client_id
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.auth_pages import app_service
from ducklab.operations_write_repository import ValidationError
from ducklab.ops_stock_state import StockState


class AssignmentState(StockState):
    """Store Management shared page state + user-assignment workflow."""

    # --- stores table / shared page state -------------------------------
    store_rows: list[dict[str, str]] = []
    can_manage_stores: bool = False
    can_assign_users: bool = False
    stores_error: str = ""
    stores_notice: str = ""

    # --- user assignment (per store) ------------------------------------
    assign_store_id: str = ""
    assign_rows: list[dict[str, str]] = []   # assigned users for that store
    assign_options: list[str] = []           # "email — name (roles)" to add
    assign_selected: str = ""
    _assign_all: list[dict] = []             # cache: all active users

    # ------------------------------------------------------------------
    # user assignment
    # ------------------------------------------------------------------
    @rx.event
    def open_assign(self, store_id: str) -> None:
        try:
            sid = parse_client_id(store_id, "store")
        except ValidationError as exc:
            self.stores_error = str(exc)
            return
        self.assign_store_id = str(sid) if sid is not None else ""
        self.assign_selected = ""
        yield AssignmentState.load_assign_rows()

    @rx.event
    def load_assign_rows(self) -> None:
        self.assign_rows = []
        self.assign_options = []
        if not self.assign_store_id:
            return
        try:
            user = self._actor()
            sid = parse_client_id(self.assign_store_id, "store")
            authz.require_store_grantable(user, sid)
            users = app_service.list_users(user)
            self._assign_all = [
                {"id": u.id, "email": u.email, "name": u.display_name,
                 "roles": ", ".join(u.roles), "stores": u.store_ids,
                 "is_active": u.is_active}
                for u in users
            ]
            assigned = [u for u in self._assign_all if sid in u["stores"]]
            self.assign_rows = [
                {"id": str(u["id"]),
                 "label": f"{u['email']} — {u['name']} ({u['roles']})",
                 "active": "1" if u["is_active"] else "0"}
                for u in assigned
            ]
            self.assign_options = [
                f"{u['email']} — {u['name']} ({u['roles']})"
                for u in self._assign_all
                if u["is_active"] and sid not in u["stores"]
            ]
        except ValidationError as exc:
            self.stores_error = str(exc)
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."

    @rx.event
    def select_assign_user(self, label: str) -> None:
        self.assign_selected = label

    @rx.event
    def assign_user(self) -> None:
        try:
            user = self._actor()
            sid = parse_client_id(self.assign_store_id, "store",
                                  required=True)
            target = next((u for u in self._assign_all
                           if f"{u['email']} — {u['name']} ({u['roles']})"
                           == self.assign_selected), None)
            if target is None:
                self.stores_error = "Select an existing user to assign."
                return
            app_service.assign_store(user, target["id"], sid)
            self.stores_notice = f"Assigned {target['email']}."
            self.assign_selected = ""
        except ValidationError as exc:
            self.stores_error = str(exc)
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        yield AssignmentState.load_assign_rows()

    @rx.event
    def unassign_user(self, user_id: str) -> None:
        try:
            user = self._actor()
            sid = parse_client_id(self.assign_store_id, "store",
                                  required=True)
            uid = parse_client_id(user_id, "user", required=True)
            app_service.revoke_store(user, uid, sid)
            self.stores_notice = "User unassigned."
        except ValidationError as exc:
            self.stores_error = str(exc)
        except (AuthorizationError, AuthenticationError) as exc:
            self.stores_error = str(exc) if not isinstance(
                exc, AuthenticationError) else "Session expired — sign in again."
        yield AssignmentState.load_assign_rows()
