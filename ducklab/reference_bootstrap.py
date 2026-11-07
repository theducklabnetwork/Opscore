"""Load the small bundled DSC workbook into a new reference-data store."""

from __future__ import annotations

import logging
from pathlib import Path

from ducklab import dsc_import, store_ops_db

log = logging.getLogger("ducklab.reference_bootstrap")

WORKBOOK = Path(__file__).parent / "data" / "DSCSPR_alias_2027.xlsx"


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
        dsc_import.import_workbook(Path(workbook), Path(db_path), bundled=True)
    except Exception:
        log.warning("Could not load bundled reference data; continuing without it",
                    exc_info=True)
        return False
    return True
