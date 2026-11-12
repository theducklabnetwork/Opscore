"""Default expense categories shared by store creation and startup seeding.

Single source of truth: both ``reference_bootstrap.seed_default_expense_categories``
and ``OperationsWriteRepository.create_store`` import this constant so that
category names are never duplicated or hardcoded in multiple places.

Each default carries its bundled budget percentage — the share of the
store's active sales target the category is budgeted at (0.025 = 2.5%).
The percentage is editable afterward in the budget editors (Expenses →
Set Budget and Monthly Setup → Expense Budgets); it is never re-forced.
"""

# (name, budget_pct as a fraction of the active sales target)
DEFAULT_EXPENSE_CATEGORIES = (
    ("Electricity", 0.025),
    ("Water", 0.003),
    ("Consumables", 0.008),
    ("Store Supplies", 0.006),
    ("Summons & Penalties", 0.0),
    ("Licenses & Permits Fees", 0.004),
    ("Mall/Building Fees", 0.03),
    ("Maintenance/Repairs/Renovation", 0.015),
    ("Telecommunications", 0.005),
    ("Cash Shortages", 0.0015),
)

# category name -> budget_pct fraction, for name-based seeding/backfill
DEFAULT_BUDGET_PCT = {name: pct for name, pct in DEFAULT_EXPENSE_CATEGORIES}
