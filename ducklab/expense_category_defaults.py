"""Default expense categories shared by store creation and startup seeding.

Single source of truth: both ``reference_bootstrap.seed_default_expense_categories``
and ``OperationsWriteRepository.create_store`` import this constant so that
category names are never duplicated or hardcoded in multiple places.
"""

DEFAULT_EXPENSE_CATEGORIES = (
    "Electricity",
    "Water",
    "Consumables",
    "Store Supplies",
    "Summons & Penalties",
    "Licenses & Permits Fees",
    "Mall/Building Fees",
    "Maintenance/Repairs/Renovation",
    "Telecommunications",
    "Cash Shortages",
)
