"""Centralized, server-side role-based access control.

Deny by default. Every permission decision goes through this module;
roles or store IDs supplied by a client are never trusted — callers pass
an authenticated `User` obtained from `AuthService.authenticate_token`.

Role model
----------
super_admin    : everything, all stores (no store assignment needed)
administrator  : manage operational records/config for *assigned* stores,
                 view & finalize their reports. No user/role administration.
zone_manager   : assigned stores: read reports, write operational records
                 (daily/stock/expenses/actions). No config, no finalize.
branch_manager : assigned stores: write daily/stock records, expenses,
                 actions; read reports. No config, no finalize.
viewer         : assigned stores: read-only.

Restrictive defaults where business policy is undecided:
- Report finalization/correction: super_admin + administrator only.
- Month configuration & budgets: super_admin + administrator only.
- Store registration & user/role/store-access management: super_admin only.
- No zone-string matching ever grants access — explicit assignments only.
"""

from __future__ import annotations

from typing import Optional

from ducklab.auth_service import AuthorizationError, User

# Role name constants
SUPER_ADMIN = "super_admin"
ADMINISTRATOR = "administrator"
ZONE_MANAGER = "zone_manager"
BRANCH_MANAGER = "branch_manager"
VIEWER = "viewer"

ALL_ROLES = (SUPER_ADMIN, ADMINISTRATOR, ZONE_MANAGER, BRANCH_MANAGER, VIEWER)

# Roles allowed to manage other users / roles / store access.
USER_MANAGERS = frozenset({SUPER_ADMIN})
# Roles allowed to configure months, budgets, categories, register stores.
CONFIG_MANAGERS = frozenset({SUPER_ADMIN, ADMINISTRATOR})
# Roles allowed to finalize / correct monthly snapshots.
REPORT_FINALIZERS = frozenset({SUPER_ADMIN, ADMINISTRATOR})
# Roles allowed to write operational records (daily, stock, expense, action).
OPERATIONS_WRITERS = frozenset({SUPER_ADMIN, ADMINISTRATOR, ZONE_MANAGER,
                                BRANCH_MANAGER})
# Roles allowed to read operational reports at all.
READERS = frozenset({SUPER_ADMIN, ADMINISTRATOR, ZONE_MANAGER,
                     BRANCH_MANAGER, VIEWER})


def has_role(user: User, roles: frozenset) -> bool:
    return bool(set(user.roles) & roles) and user.is_active


def can_manage_users(user: User) -> bool:
    return has_role(user, USER_MANAGERS)


def can_manage_config(user: User) -> bool:
    return has_role(user, CONFIG_MANAGERS)


def can_finalize_reports(user: User) -> bool:
    return has_role(user, REPORT_FINALIZERS)


def can_write_operations(user: User) -> bool:
    return has_role(user, OPERATIONS_WRITERS)


def can_read(user: User) -> bool:
    return has_role(user, READERS)


def stores_visible_to(user: User) -> tuple[int, ...] | None:
    """Store IDs the user may access; None means 'all stores' (super admin)."""
    if not user.is_active or not can_read(user):
        return ()
    if user.is_super_admin:
        return None
    return user.store_ids


def can_access_store(user: User, store_id: int) -> bool:
    visible = stores_visible_to(user)
    if visible is None:
        return True
    return store_id in visible


# ---------------------------------------------------------------------------
# Enforcement helpers — raise AuthorizationError on failure
# ---------------------------------------------------------------------------

def require_active(user: User) -> None:
    if not user.is_active:
        raise AuthorizationError("Account is disabled")


def require_user_manager(user: User) -> None:
    require_active(user)
    if not can_manage_users(user):
        raise AuthorizationError("User management requires Super Admin")


def require_config_manager(user: User) -> None:
    require_active(user)
    if not can_manage_config(user):
        raise AuthorizationError("Configuration management not permitted")


def require_store_manager(user: User) -> None:
    """Store register/edit/activate-deactivate: Super Admin only, matching
    the authz docstring and the Store Management UI."""
    require_active(user)
    if not user.is_super_admin:
        raise AuthorizationError("Store management requires Super Admin")


def require_finalizer(user: User) -> None:
    require_active(user)
    if not can_finalize_reports(user):
        raise AuthorizationError("Report finalization not permitted")


def require_operations_writer(user: User) -> None:
    require_active(user)
    if not can_write_operations(user):
        raise AuthorizationError("Write access not permitted")


def require_store_access(user: User, store_id: int) -> None:
    require_active(user)
    if not can_read(user) or not can_access_store(user, store_id):
        raise AuthorizationError("No access to this store")


def require_store_write(user: User, store_id: int,
                        store_active: Optional[bool] = None) -> None:
    """Writes to a DEACTIVATED store are rejected server-side
    ("Store is deactivated: read-only"); reads stay allowed.

    ``store_active`` is supplied by the caller (AppService), which owns the
    store lookup — authz itself never queries the database."""
    require_operations_writer(user)
    if not can_access_store(user, store_id):
        raise AuthorizationError("No write access to this store")
    if store_active is False:
        raise AuthorizationError("Store is deactivated: read-only")


def require_store_finalize(user: User, store_id: int) -> None:
    require_finalizer(user)
    if not can_access_store(user, store_id):
        raise AuthorizationError("No access to this store")


def require_role_grantable(actor: User, role: str) -> None:
    """Only Super Admins manage roles at all; and non-super actors can
    never grant super_admin (defense in depth — currently only super admins
    reach this code, but the rule stays explicit)."""
    require_user_manager(actor)
    if role not in ALL_ROLES:
        raise AuthorizationError(f"Unknown role {role!r}")
    if role == SUPER_ADMIN and not actor.is_super_admin:
        raise AuthorizationError("Only a Super Admin can grant Super Admin")


def require_store_grantable(actor: User, store_id: int) -> None:
    """Store assignment changes are a Super Admin operation; scoped
    delegation to store-limited admins is a future, approved policy."""
    require_user_manager(actor)
