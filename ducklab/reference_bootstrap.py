"""Seed bundled reference data without modifying existing records."""

from __future__ import annotations

import logging
from pathlib import Path

from ducklab import dsc_import, store_ops_db

log = logging.getLogger("ducklab.reference_bootstrap")

WORKBOOK = Path(__file__).parent / "data" / "DSCSPR_alias_2027.xlsx"

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
            conn.executemany(
                "INSERT INTO expense_categories (store_id, name, sort_order) "
                "VALUES (?, ?, ?)",
                [(row["id"], name, order) for order, name in enumerate(
                    DEFAULT_EXPENSE_CATEGORIES)],
            )
        conn.commit()
        return bool(rows)
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
