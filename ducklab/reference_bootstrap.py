"""Seed bundled reference data without modifying existing records."""

from __future__ import annotations

import logging
from pathlib import Path

from ducklab import dsc_import, store_ops_db
from ducklab.expense_category_defaults import (
    DEFAULT_BUDGET_PCT, DEFAULT_EXPENSE_CATEGORIES,
)

log = logging.getLogger("ducklab.reference_bootstrap")

WORKBOOK = Path(__file__).parent / "data" / "DSCSPR_alias_2027.xlsx"

# Re-exported for callers that already import it from this module.
__all__ = ["DEFAULT_EXPENSE_CATEGORIES", "seed_default_expense_categories",
           "backfill_default_budget_pct", "load_bundled_reference_data"]


def _seed_store_categories(conn, store_id: int) -> None:
    """Insert the ten default categories and, for every month the store has
    configured, their bundled budget % (only where no budget is set yet)."""
    conn.executemany(
        "INSERT INTO expense_categories (store_id, name, sort_order) "
        "VALUES (?, ?, ?)",
        [(store_id, name, order)
         for order, (name, _pct) in enumerate(DEFAULT_EXPENSE_CATEGORIES)],
    )
    cat_ids = {row["name"]: row["id"] for row in conn.execute(
        "SELECT id, name FROM expense_categories WHERE store_id = ?",
        (store_id,))}
    configs = conn.execute(
        "SELECT id FROM monthly_configs WHERE store_id = ?", (store_id,)
    ).fetchall()
    for cfg in configs:
        for name, pct in DEFAULT_BUDGET_PCT.items():
            cat_id = cat_ids.get(name)
            if cat_id is None:
                continue
            conn.execute(
                "INSERT INTO monthly_expense_budgets "
                "(store_id, monthly_config_id, category_id, budget_pct) "
                "VALUES (?,?,?,?) "
                "ON CONFLICT (monthly_config_id, category_id) "
                "DO UPDATE SET budget_pct = excluded.budget_pct "
                "WHERE monthly_expense_budgets.budget_pct IS NULL",
                (store_id, cfg["id"], cat_id, pct),
            )


def seed_default_expense_categories(
        db_path: Path | str = store_ops_db.DB_PATH) -> bool:
    """Seed each category-free store once; never change existing categories."""
    store_ops_db.init_db(db_path)
    conn = store_ops_db.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id FROM stores AS s "
            "WHERE NOT EXISTS (SELECT 1 FROM expense_categories AS c "
            "                  WHERE c.store_id = s.id)").fetchall()
        for row in rows:
            _seed_store_categories(conn, row["id"])
        conn.commit()
        return bool(rows)
    finally:
        conn.close()


def backfill_default_budget_pct(
        db_path: Path | str = store_ops_db.DB_PATH) -> bool:
    """Apply the bundled default budget % to every store's configured
    months wherever a category has no budget set (NULL). User-entered
    percentages are never overwritten; custom categories get no default.

    Runs at startup after init_db so that stores upgraded to v11 (whose
    old fixed-RM budgets were cleared) receive the bundled defaults.
    Returns True when at least one budget was seeded."""
    store_ops_db.init_db(db_path)
    conn = store_ops_db.connect(db_path)
    try:
        seeded = False
        stores = conn.execute("SELECT id FROM stores").fetchall()
        for store in stores:
            sid = store["id"]
            cat_ids = {row["name"]: row["id"] for row in conn.execute(
                "SELECT id, name FROM expense_categories WHERE store_id = ?",
                (sid,))}
            configs = conn.execute(
                "SELECT id FROM monthly_configs WHERE store_id = ?", (sid,)
            ).fetchall()
            for cfg in configs:
                for name, pct in DEFAULT_BUDGET_PCT.items():
                    cat_id = cat_ids.get(name)
                    if cat_id is None:
                        continue
                    cur = conn.execute(
                        "INSERT INTO monthly_expense_budgets "
                        "(store_id, monthly_config_id, category_id, budget_pct) "
                        "VALUES (?,?,?,?) "
                        "ON CONFLICT (monthly_config_id, category_id) "
                        "DO UPDATE SET budget_pct = excluded.budget_pct "
                        "WHERE monthly_expense_budgets.budget_pct IS NULL",
                        (sid, cfg["id"], cat_id, pct),
                    )
                    # rowcount 1 = inserted or filled a NULL; 0 = kept an
                    # existing user-set percentage.
                    seeded = seeded or cur.rowcount > 0
        conn.commit()
        return seeded
    finally:
        conn.close()


def load_bundled_reference_data(
        db_path: Path | str = store_ops_db.DB_PATH,
        workbook: Path = WORKBOOK) -> bool:
    """Import defaults only when both reference tables are empty.

    Return True when the bundled workbook was imported. Existing data and
    import failures are deliberately non-fatal to application startup.
    """
    try:
        store_ops_db.init_db(db_path)
        conn = store_ops_db.connect(db_path)
        try:
            empty = all(conn.execute(f"SELECT NOT EXISTS (SELECT 1 FROM {table})")
                        .fetchone()[0]
                        for table in ("contribution_shares", "basket_benchmarks"))
        finally:
            conn.close()
        if not empty:
            return False
        if not Path(workbook).is_file():
            log.error("Bundled reference workbook is missing: %s", workbook)
            return False
        dsc_import.import_workbook(Path(workbook), Path(db_path), bundled=True)
    except Exception:
        log.warning("Could not load bundled reference data; continuing without it",
                    exc_info=True)
        return False
    return True
