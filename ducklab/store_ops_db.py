"""Store Operations Monitoring database layer.

Isolated from the Placement Tracker prototype (ducklab/db.py). Uses its own
SQLite file and never touches placement_tracker.db.

Monetary storage: all RM amounts are stored as INTEGER cents (e.g. RM 12.50 =
1250). Conversion to float happens only at the presentation boundary; no
floating-point currency arithmetic is performed in this layer.

NULL semantics: NULL means "not reported" and is never coerced to zero. An
explicitly reported zero-sales day is stored as 0 (cents), distinct from NULL.

Migrations: schema_version table + PRAGMA user_version. Initialization is
idempotent and non-destructive; new versions append migration steps.
"""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "store_ops.db"

SCHEMA_VERSION = 14


class SchemaVersionError(RuntimeError):
    """The database belongs to a newer, incompatible application version."""


def require_compatible_schema(current: int) -> None:
    if current > SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema v{current} is newer than this app "
            f"(v{SCHEMA_VERSION}). Upgrade Opscore or restore a compatible "
            "backup; no changes were made.")

# Reference data defined by the workbook (Store_Operations_Monitoring_v4.xlsx,
# Daily sheet columns T-AF and Guide definitions). contribution_sign:
#   +1  adds to Total Loss, -1 reduces Total Loss, 0 informational only.
STOCK_LOSS_CODES = [
    ("DMO", "Damaged in outlet", 1),
    ("DMC", "Returned by customer", 1),
    ("DMP", "Expired", 1),
    ("2DMP", "Expired Buy 1 Free 1 (DPI) stock", 0),
    ("DPI", "Buy 1 Free 1 promotion", 0),
    ("DMW", "Damaged under warranty", 0),
    ("DDR", "Damaged during receiving", 0),
    ("DMN", "Natural disaster", 0),
    ("DME", "E-commerce return", 0),
    ("DMQ", "Damage in Quality", 0),
    ("DST", "Damaged in stock transfer", 0),
    ("MISSING", "Missing at stock balance checks", 1),
    ("EXTRA", "Extra stock found at stock balance checks", -1),
]

_SCHEMA_V1 = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE stores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    concept TEXT,
    zone TEXT,
    zone_pic TEXT,
    branch_manager TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1))
);

CREATE TABLE expense_categories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    name TEXT NOT NULL,
    sort_order INTEGER NOT NULL DEFAULT 0,
    UNIQUE (store_id, name),
    UNIQUE (store_id, id)
);

CREATE TABLE stock_loss_codes (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    contribution_sign INTEGER NOT NULL CHECK (contribution_sign IN (-1, 0, 1))
);

CREATE TABLE monthly_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    month TEXT NOT NULL,  -- 'YYYY-MM'
    min_rtm_target_cents INTEGER,
    level1_base_cents INTEGER,
    level2_pct REAL,
    level3_pct REAL,
    level4_pct REAL,
    sales_last_year_cents INTEGER,
    sales_last_month_cents INTEGER,
    salary_ratio_target REAL,
    budget_headcount INTEGER,
    turnover_threshold REAL,
    stock_loss_target REAL,
    opening_headcount_ft INTEGER,
    part_timer_factor REAL,
    -- Month-end inputs; NULL = awaiting payroll (not zero).
    salary_basic_allowance_cents INTEGER,
    overtime_cents INTEGER,
    support_team_cost_cents INTEGER,
    UNIQUE (store_id, month),
    UNIQUE (store_id, id)
);

CREATE TABLE monthly_expense_budgets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    monthly_config_id INTEGER NOT NULL,
    category_id INTEGER NOT NULL,
    -- Budget as a FRACTION of the store's active sales target
    -- (0.025 = 2.5%); NULL = no budget entered.
    budget_pct REAL,
    UNIQUE (monthly_config_id, category_id),
    FOREIGN KEY (store_id, monthly_config_id)
        REFERENCES monthly_configs (store_id, id),
    FOREIGN KEY (store_id, category_id)
        REFERENCES expense_categories (store_id, id)
);

CREATE TABLE daily_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    date TEXT NOT NULL,  -- 'YYYY-MM-DD'
    sales_cents INTEGER,        -- NULL = not reported; 0 = reported zero-sales day
    transactions INTEGER,
    units INTEGER,
    resigned_ft INTEGER,
    new_hires_ft INTEGER,
    ft_working INTEGER,
    pt_working INTEGER,
    leave_annual INTEGER,
    leave_mc INTEGER,
    leave_other_paid INTEGER,
    leave_unpaid INTEGER,
    UNIQUE (store_id, date)
);

CREATE TABLE daily_stock_losses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    daily_record_id INTEGER NOT NULL REFERENCES daily_records(id) ON DELETE CASCADE,
    code TEXT NOT NULL REFERENCES stock_loss_codes(code),
    amount_cents INTEGER NOT NULL,
    UNIQUE (daily_record_id, code)
);

CREATE TABLE expense_transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    incurred_date TEXT,
    paid_date TEXT,
    category_id INTEGER,
    description TEXT,
    amount_cents INTEGER,  -- signed: shortage positive, overage negative
    paid_by TEXT CHECK (paid_by IN ('Petty Cash', 'HQ / Central', 'Store Account', 'Other')),
    status TEXT CHECK (status IN ('Pending', 'Paid', 'Cancelled')),
    receipt_ref TEXT,
    verification TEXT CHECK (verification IN ('Unverified', 'Verified', 'Disputed')),
    recorded_by TEXT,
    remarks TEXT,
    FOREIGN KEY (store_id, category_id)
        REFERENCES expense_categories (store_id, id)
    -- Deliberately no NOT NULL on business fields and no paid_date/status
    -- CHECK: draft rows must be storable so the validation ladder
    -- (Incomplete / Missing paid date / ...) can flag them.
);

CREATE TABLE actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    raised_date TEXT,
    pillar TEXT CHECK (pillar IN ('Sales Performance', 'Expenses Management',
        'Manpower Efficiency', 'Stock Management', 'General Operations')),
    issue TEXT,
    priority TEXT CHECK (priority IN ('Low', 'Medium', 'High', 'Critical')),
    status TEXT CHECK (status IN ('Open', 'In progress', 'Blocked', 'Done', 'Cancelled')),
    pic TEXT,
    due_date TEXT,
    corrective_action TEXT,
    completion_date TEXT,
    remarks TEXT
);

CREATE TABLE monthly_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    month TEXT NOT NULL,  -- 'YYYY-MM'
    version INTEGER NOT NULL DEFAULT 1,
    kpi_json TEXT NOT NULL,
    finalized INTEGER NOT NULL DEFAULT 0 CHECK (finalized IN (0, 1)),
    superseded_by INTEGER REFERENCES monthly_snapshots(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (store_id, month, version)
    -- Corrections never overwrite: a correction inserts version+1 and sets
    -- the old row's superseded_by to the new row's id.
);
"""


_TRANSACTION_STATES = {}


class _TransactionState:
    def __init__(self):
        self.failed = False


class _SharedConnection:
    """Non-owning connection used inside an AppService transaction.

    Repository commits, rollbacks, closes and nested BEGINs are left to the
    transaction owner. Statement errors still propagate to that owner.
    """

    def __init__(self, conn: sqlite3.Connection, state=None):
        self._conn = conn
        self._state = _TransactionState() if state is None else state

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def execute(self, sql: str, *args):
        statement = sql.strip().upper()
        if statement == "ROLLBACK" or statement.startswith("ROLLBACK;"):
            self.rollback()
            return None
        if statement.startswith(("BEGIN", "COMMIT")):
            return None
        return self._conn.execute(sql, *args)

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        self._state.failed = True

    def close(self) -> None:
        pass


def connect(db_path: Path | str | sqlite3.Connection = DB_PATH) -> sqlite3.Connection:
    """Open a foreign-key-enabled connection, or share a transaction's one."""
    if isinstance(db_path, sqlite3.Connection):
        return _SharedConnection(db_path, _TRANSACTION_STATES.get(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _execute_schema(conn: sqlite3.Connection, schema: str) -> None:
    statement = ""
    for line in schema.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            conn.execute(statement)
            statement = ""


def _apply_v1(conn: sqlite3.Connection) -> None:
    _execute_schema(conn, _SCHEMA_V1)
    conn.executemany(
        "INSERT OR IGNORE INTO stock_loss_codes (code, name, contribution_sign) "
        "VALUES (?, ?, ?)",
        STOCK_LOSS_CODES,
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (1,)
    )


def init_db(db_path: Path | str = DB_PATH, *,
            backup_before_migration: bool = True) -> None:
    """Create/migrate the database, preserving an existing pre-migration copy."""
    existed = Path(db_path).exists()
    conn = connect(db_path)
    try:
        current = conn.execute("PRAGMA user_version").fetchone()[0]
        require_compatible_schema(current)
        if existed and current < SCHEMA_VERSION and backup_before_migration:
            # Local import avoids backup.py's module-level store_ops_db import.
            from ducklab import backup
            backup.save_pre_migration_backup(
                db_path, current, SCHEMA_VERSION)
        migrations = (
            (1, _apply_v1),
            (2, _apply_v2),
            (3, _apply_v3),
            (4, _apply_v4),
            (5, _apply_v5),
            (6, _apply_v6),
            (7, _apply_v7),
            (8, _apply_v8),
            (9, _apply_v9),
            (10, _apply_v10),
            (11, _apply_v11),
            (12, _apply_v12),
            (13, _apply_v13),
            (14, _apply_v14),
        )
        for version, apply_migration in migrations:
            if current >= version:
                continue
            try:
                conn.execute("BEGIN")
                apply_migration(conn)
                conn.execute(f"PRAGMA user_version = {version}")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
            current = version
    finally:
        conn.close()


_SCHEMA_V2 = """
-- v2: audit columns on actions (created/updated by/at). Additive ALTERs;
-- existing rows keep NULL audit data. No KPI-relevant columns change.
ALTER TABLE actions ADD COLUMN created_by INTEGER;
ALTER TABLE actions ADD COLUMN created_at TEXT;
ALTER TABLE actions ADD COLUMN updated_by INTEGER;
ALTER TABLE actions ADD COLUMN updated_at TEXT;
"""


def _apply_v2(conn: sqlite3.Connection) -> None:
    _execute_schema(conn, _SCHEMA_V2)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (2,)
    )


_SCHEMA_V3 = """
-- v3: daily targets from the Daily Sales Contribution (DSC). Additive:
-- store state, three monthly basket targets, per-store public holidays,
-- the imported contribution shares and the SPR/IPR benchmarks. Existing
-- rows keep NULL (no state = no daily targets; no target = no %).
ALTER TABLE stores ADD COLUMN state TEXT;
ALTER TABLE monthly_configs ADD COLUMN target_transactions INTEGER;
ALTER TABLE monthly_configs ADD COLUMN target_spr_cents INTEGER;
ALTER TABLE monthly_configs ADD COLUMN target_ipr REAL;

CREATE TABLE public_holidays (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    date TEXT NOT NULL,  -- 'YYYY-MM-DD'
    UNIQUE (store_id, date)
);

-- One day's share of the month by State + Concept (exact text) + weekday
-- (Mon..Sun) or PH, per calendar month (1-12, any year).
CREATE TABLE contribution_shares (
    state TEXT NOT NULL,
    concept TEXT NOT NULL,
    day TEXT NOT NULL CHECK (day IN ('Mon', 'Tue', 'Wed', 'Thu', 'Fri',
                                     'Sat', 'Sun', 'PH')),
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    share REAL NOT NULL,
    PRIMARY KEY (state, concept, day, month)
);

-- 12-month average SPR (RM) / IPR per Concept + State (later benchmarks).
CREATE TABLE basket_benchmarks (
    concept TEXT NOT NULL,
    state TEXT NOT NULL,
    avg_spr REAL,
    avg_ipr REAL,
    PRIMARY KEY (concept, state)
);
"""


def _apply_v3(conn: sqlite3.Connection) -> None:
    _execute_schema(conn, _SCHEMA_V3)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (3,)
    )


_SCHEMA_V4 = """
-- v4: SPR / IPR benchmarks are monthly in the DSC workbook. The v3
-- single-value table was never read by the app (reference data only,
-- refilled by scripts/import_dsc.py), so it is replaced, not altered.
DROP TABLE IF EXISTS basket_benchmarks;

-- Average SPR (RM) / IPR per Concept + State + calendar month (1-12).
CREATE TABLE basket_benchmarks (
    concept TEXT NOT NULL,
    state TEXT NOT NULL,
    month INTEGER NOT NULL CHECK (month BETWEEN 1 AND 12),
    avg_spr REAL,
    avg_ipr REAL,
    PRIMARY KEY (concept, state, month)
);
"""


def _apply_v4(conn: sqlite3.Connection) -> None:
    _execute_schema(conn, _SCHEMA_V4)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (4,)
    )


_SCHEMA_V5 = """
-- v5: DSC reference-data import bookkeeping (last import timestamp).
-- Additive, one singleton row; written by ducklab/dsc_import inside the
-- same transaction that replaces the reference tables.
CREATE TABLE reference_data_info (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    imported_at TEXT NOT NULL
);
"""


def _apply_v5(conn: sqlite3.Connection) -> None:
    _execute_schema(conn, _SCHEMA_V5)
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (5,)
    )


_SCHEMA_V6 = """
-- v6: store-scoped staff roster and weekly manpower schedules.
CREATE TABLE staff (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    name TEXT NOT NULL,
    staff_code TEXT NOT NULL,
    position TEXT NOT NULL,
    UNIQUE (store_id, staff_code),
    UNIQUE (store_id, id)
);

CREATE TABLE shift_codes (
    code TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    start_time TEXT,
    end_time TEXT,
    color TEXT NOT NULL
);

CREATE TABLE schedule_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    store_id INTEGER NOT NULL REFERENCES stores(id),
    staff_id INTEGER NOT NULL REFERENCES staff(id) ON DELETE CASCADE,
    work_date TEXT NOT NULL,
    code TEXT NOT NULL REFERENCES shift_codes(code),
    UNIQUE (staff_id, work_date),
    FOREIGN KEY (store_id, staff_id) REFERENCES staff (store_id, id)
);

CREATE TABLE schedule_targets (
    store_id INTEGER NOT NULL REFERENCES stores(id),
    week_start TEXT NOT NULL,
    target_manpower INTEGER NOT NULL CHECK (target_manpower >= 0),
    PRIMARY KEY (store_id, week_start)
);
"""

SHIFT_CODES = [
    ("M900", "Morning", "09:00", "19:00", "#FFFFFF"),
    ("F900", "Full Day", "09:00", "21:00", "#FFFFFF"),
    ("M945", "Morning", "09:45", "19:45", "#FFFFFF"),
    ("F945", "Full Day", "09:45", "21:45", "#FFFFFF"),
    ("F101", "Full Day", "10:15", "22:15", "#FFFFFF"),
    ("A121", "Afternoon", "12:15", "22:15", "#FFFFFF"),
    ("ODM", "Off Day Morning", "09:00", "19:00", "#bae6fd"),
    ("ODA", "Off Day Afternoon", "12:15", "22:15", "#93c5fd"),
    ("OOD", "Off Day", None, None, "#fef08a"),
    ("ORD", "Rest Day", None, None, "#86efac"),
    ("AL", "Annual Leave", None, None, "#e9d5ff"),
    ("RPL", "Replacement Leave", None, None, "#f5d0fe"),
    ("UPL", "Unpaid Leave", None, None, "#e5e7eb"),
    ("EL", "Emergency Leave", None, None, "#fecaca"),
    ("MC", "Sick Leave", None, None, "#fbcfe8"),
    ("OPH", "Public Holiday", None, None, "#ddd6fe"),
]


def _apply_v6(conn: sqlite3.Connection) -> None:
    _execute_schema(conn, _SCHEMA_V6)
    conn.executemany(
        "INSERT OR IGNORE INTO shift_codes "
        "(code, label, start_time, end_time, color) VALUES (?, ?, ?, ?, ?)",
        SHIFT_CODES,
    )
    conn.execute(
        "INSERT OR IGNORE INTO schema_migrations (version) VALUES (?)", (6,)
    )


def _apply_v7(conn: sqlite3.Connection) -> None:
    v7_colors = {
        "M900": "#bbf7d0", "F900": "#fed7aa", "M945": "#dcfce7",
        "F945": "#ffedd5", "F101": "#fdba74", "A121": "#fde68a",
        "ODM": "#bae6fd", "ODA": "#93c5fd", "OOD": "#fef08a",
        "ORD": "#86efac", "AL": "#e9d5ff", "EL": "#fecaca",
        "UPL": "#e5e7eb", "MC": "#fbcfe8", "OPH": "#ddd6fe",
    }
    old_colors = ("#3b82f6", "#06b6d4", "#8b5cf6", "#a855f7", "#d946ef",
                  "#f59e0b", "#14b8a6", "#10b981", "#64748b", "#78716c",
                  "#22c55e", "#ef4444", "#f97316", "#eab308", "#ec4899")
    for (code, color), old_color in zip(v7_colors.items(), old_colors):
        conn.execute("UPDATE shift_codes SET color = ? WHERE code = ? AND color = ?",
                     (color, code, old_color))
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (7)")


def _apply_v8(conn: sqlite3.Connection) -> None:
    conn.execute(
        "ALTER TABLE staff ADD COLUMN employment_type TEXT NOT NULL "
        "DEFAULT 'Not Set' CHECK (employment_type IN "
        "('Full-Time', 'Part-Time', 'Not Set'))"
    )
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (8)")


def _apply_v9(conn: sqlite3.Connection) -> None:
    # Half-open month ranges can use these directly. Daily records and
    # holidays already have equivalent UNIQUE (store_id, date) indexes.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_actions_store_raised_date "
        "ON actions (store_id, raised_date)"
    )
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (9)")


def _apply_v10(conn: sqlite3.Connection) -> None:
    # Track whether the current reference tables came from the bundled
    # workbook; an explicit upload clears this flag in the same transaction.
    conn.execute(
        "ALTER TABLE reference_data_info ADD COLUMN bundled "
        "INTEGER NOT NULL DEFAULT 0 CHECK (bundled IN (0, 1))"
    )
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (10)")


def _apply_v11(conn: sqlite3.Connection) -> None:
    """Expense budgets become a percentage of the active sales target.

    ``budget_cents`` (a fixed RM amount) is replaced by ``budget_pct``
    (a fraction, e.g. 0.025 = 2.5%). Fixed-RM budgets are cleared; the
    bundled default percentages are seeded afterward by the startup
    lifespan task (reference_bootstrap). Finalized monthly snapshots keep
    their historical RM figures — only the live budget input changes.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'monthly_expense_budgets'").fetchone()
    if not exists:
        # Minimal/legacy test fixtures may not carry the table.
        conn.execute(
            "CREATE TABLE IF NOT EXISTS monthly_expense_budgets ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "store_id INTEGER NOT NULL REFERENCES stores(id),"
            "monthly_config_id INTEGER NOT NULL,"
            "category_id INTEGER NOT NULL,"
            "budget_pct REAL,"
            "UNIQUE (monthly_config_id, category_id))"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations (version) VALUES (11)")
        return
    conn.execute("ALTER TABLE monthly_expense_budgets RENAME TO "
                 "monthly_expense_budgets_v10")
    conn.execute(
        "CREATE TABLE monthly_expense_budgets ("
        "id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "store_id INTEGER NOT NULL REFERENCES stores(id),"
        "monthly_config_id INTEGER NOT NULL,"
        "category_id INTEGER NOT NULL,"
        "budget_pct REAL,"
        "UNIQUE (monthly_config_id, category_id),"
        "FOREIGN KEY (store_id, monthly_config_id)"
        "    REFERENCES monthly_configs (store_id, id),"
        "FOREIGN KEY (store_id, category_id)"
        "    REFERENCES expense_categories (store_id, id))"
    )
    # Carry rows across as "no budget set"; the old RM value is dropped.
    conn.execute(
        "INSERT INTO monthly_expense_budgets "
        "(id, store_id, monthly_config_id, category_id, budget_pct) "
        "SELECT id, store_id, monthly_config_id, category_id, NULL "
        "FROM monthly_expense_budgets_v10"
    )
    conn.execute("DROP TABLE monthly_expense_budgets_v10")
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (11)")


def _apply_v12(conn: sqlite3.Connection) -> None:
    """Add actual emergency leave and holiday-assignment provenance."""
    tables = {row[0] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    if "daily_records" in tables:
        conn.execute(
            "ALTER TABLE daily_records ADD COLUMN leave_emergency INTEGER"
        )
    if "schedule_entries" not in tables:
        conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (12)")
        return
    conn.execute(
        "ALTER TABLE schedule_entries ADD COLUMN assignment_source "
        "TEXT NOT NULL DEFAULT 'manual' CHECK(assignment_source IN "
        "('manual', 'auto_holiday'))"
    )
    conn.execute(
        "INSERT OR IGNORE INTO shift_codes "
        "(code, label, start_time, end_time, color) VALUES (?, ?, ?, ?, ?)",
        ("RPL", "Replacement Leave", None, None, "#f5d0fe"),
    )
    v7_colors = {
        "M900": "#bbf7d0", "F900": "#fed7aa", "M945": "#dcfce7",
        "F945": "#ffedd5", "F101": "#fdba74", "A121": "#fde68a",
    }
    for code, old_color in v7_colors.items():
        conn.execute(
            "UPDATE shift_codes SET color = '#FFFFFF' "
            "WHERE code = ? AND color = ?",
            (code, old_color),
        )
    # Existing ABM rosters and configured holidays are linked during upgrade.
    conn.execute(
        "INSERT INTO schedule_entries "
        "(store_id, staff_id, work_date, code, assignment_source) "
        "SELECT s.store_id, s.id, h.date, 'OPH', 'auto_holiday' "
        "FROM staff s JOIN public_holidays h ON h.store_id = s.store_id "
        "WHERE s.position = 'ABM' AND NOT EXISTS "
        "(SELECT 1 FROM schedule_entries e "
        "WHERE e.staff_id = s.id AND e.work_date = h.date)"
    )
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (12)")


def _apply_v13(conn: sqlite3.Connection) -> None:
    """Allow each configurable shift code to appear in the View legend."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'shift_codes'").fetchone()
    if exists:
        conn.execute(
            "ALTER TABLE shift_codes ADD COLUMN show_in_legend "
            "INTEGER NOT NULL DEFAULT 1 CHECK (show_in_legend IN (0, 1))"
        )
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (13)")


def _apply_v14(conn: sqlite3.Connection) -> None:
    """Public holidays replace every ABM assignment with automatic OPH."""
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' "
        "AND name = 'schedule_entries'").fetchone()
    if not exists:
        conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (14)")
        return
    conn.execute(
        "INSERT INTO schedule_entries "
        "(store_id, staff_id, work_date, code, assignment_source) "
        "SELECT s.store_id, s.id, h.date, 'OPH', 'auto_holiday' "
        "FROM staff s JOIN public_holidays h ON h.store_id = s.store_id "
        "WHERE s.position = 'ABM' "
        "ON CONFLICT(staff_id, work_date) DO UPDATE SET "
        "code = 'OPH', assignment_source = 'auto_holiday'"
    )
    conn.execute("INSERT OR IGNORE INTO schema_migrations (version) VALUES (14)")
