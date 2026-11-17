"""Bundled store-directory seeding: empty database seeds, stale bundled data
refreshes, user-uploaded directory is never replaced, failures are non-fatal.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path

import openpyxl

from ducklab import reference_bootstrap as rb
from ducklab import store_directory_import as sdi
from ducklab import store_ops_db


def workbook(path: Path, rows):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2025-2026"
    ws.append(["No.", "Store Code", "Store Name", "Display Area", "Concept",
               "State", "Zone", "BD Target",
               date(2025, 1, 1), date(2025, 2, 1)])
    for i, row in enumerate(rows, start=1):
        ws.append([i, *row])
    wb.save(path)
    return path


def rows(v1=100.00, v2=200.00, code="B001", name="CHP"):
    return [[code, name, 1000, "Shop Lot", "WP", "KV-6", "NIL", v1, v2]]


class BundledDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ops.db"
        store_ops_db.init_db(self.db)
        self.xlsx = self.tmp / "dir.xlsx"

    def counts(self):
        conn = sqlite3.connect(self.db)
        try:
            return (conn.execute("SELECT COUNT(*) FROM store_directory")
                    .fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM store_directory_history")
                    .fetchone()[0])
        finally:
            conn.close()

    def sales(self, code="B001", month="2025-01"):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT sales_cents FROM store_directory_history "
                "WHERE code = ? AND month = ?", (code, month)).fetchone()[0]
        finally:
            conn.close()

    def test_empty_directory_seeds_from_the_bundled_workbook(self):
        workbook(self.xlsx, rows())
        self.assertEqual(self.counts(), (0, 0))
        self.assertTrue(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        self.assertEqual(self.counts(), (1, 2))
        self.assertEqual(self.sales(), 10000)
        info = sdi.directory_info(self.db)
        self.assertTrue(info["bundled"])
        self.assertEqual(info["source_name"], "dir.xlsx")
        # No stores or daily records are ever created
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM stores")
                         .fetchone()[0], 0)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM daily_records")
                         .fetchone()[0], 0)
        conn.close()

    def test_unchanged_bundled_directory_is_not_reimported(self):
        workbook(self.xlsx, rows())
        rb.load_bundled_store_directory(self.db, workbook=self.xlsx)
        self.assertFalse(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))

    def test_stale_bundled_directory_is_refreshed(self):
        workbook(self.xlsx, rows(v1=100.00))
        rb.load_bundled_store_directory(self.db, workbook=self.xlsx)
        self.assertEqual(self.sales(), 10000)
        workbook(self.xlsx, rows(v1=150.00))     # a newer bundled workbook
        self.assertTrue(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        self.assertEqual(self.sales(), 15000)

    def test_new_store_in_the_bundled_workbook_triggers_a_refresh(self):
        workbook(self.xlsx, rows())
        rb.load_bundled_store_directory(self.db, workbook=self.xlsx)
        workbook(self.xlsx, rows()
                 + rows(code="B002", name="KLM", v1=50.00, v2=60.00))
        self.assertTrue(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        self.assertEqual(self.counts(), (2, 4))

    def test_removed_store_in_the_bundled_workbook_triggers_a_refresh(self):
        workbook(self.xlsx, rows()
                 + rows(code="B002", name="KLM", v1=50.00, v2=60.00))
        rb.load_bundled_store_directory(self.db, workbook=self.xlsx)
        self.assertEqual(self.counts(), (2, 4))
        workbook(self.xlsx, rows())      # B002 is gone from the workbook
        self.assertTrue(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        self.assertEqual(self.counts(), (1, 2))

    def test_changed_store_detail_triggers_a_refresh(self):
        workbook(self.xlsx, rows())
        rb.load_bundled_store_directory(self.db, workbook=self.xlsx)
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE store_directory SET zone = 'ZZ-9' "
                     "WHERE code = 'B001'")
        conn.commit()
        conn.close()
        self.assertTrue(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute(
            "SELECT zone FROM store_directory WHERE code = 'B001'"
        ).fetchone()[0], "KV-6")
        conn.close()

    def test_user_imported_directory_is_never_replaced(self):
        workbook(self.xlsx, rows(v1=100.00))
        # A user upload marks the directory as not bundled
        sdi.import_workbook(self.xlsx, self.db, bundled=False)
        self.assertFalse(sdi.directory_info(self.db)["bundled"])
        workbook(self.xlsx, rows(v1=999.00))
        self.assertFalse(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        self.assertEqual(self.sales(), 10000)    # untouched

    def test_missing_workbook_is_non_fatal(self):
        self.assertFalse(rb.load_bundled_store_directory(
            self.db, workbook=self.tmp / "absent.xlsx"))
        self.assertEqual(self.counts(), (0, 0))

    def test_corrupt_workbook_is_non_fatal(self):
        self.xlsx.write_bytes(b"not a zip file")
        self.assertFalse(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        self.assertEqual(self.counts(), (0, 0))

    def test_pre_v16_database_is_migrated_then_seeded(self):
        """init_db migrates before the version check, so an older database
        is upgraded and seeded in the same startup call."""
        conn = sqlite3.connect(self.db)
        conn.execute("PRAGMA user_version = 15")
        conn.execute("DELETE FROM schema_migrations WHERE version > 15")
        conn.commit()
        conn.close()
        workbook(self.xlsx, rows())
        self.assertTrue(rb.load_bundled_store_directory(
            self.db, workbook=self.xlsx))
        self.assertEqual(self.counts(), (1, 2))
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
        conn.close()

    def test_shipped_workbook_is_present_and_parseable(self):
        self.assertTrue(rb.DIRECTORY_WORKBOOK.is_file())
        self.assertTrue(rb.load_bundled_store_directory(self.db))
        info = sdi.directory_info(self.db)
        self.assertEqual(info["stores"], 1677)
        self.assertEqual(info["history"], 33601)
        self.assertTrue(info["bundled"])
        self.assertEqual(info["first_month"], "2025-01")
        self.assertEqual(info["last_month"], "2026-10")

    def test_provenance_column_added_by_v17(self):
        conn = sqlite3.connect(self.db)
        self.assertIn("bundled", [r[1] for r in conn.execute(
            "PRAGMA table_info(store_directory_info)")])
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], 19)
        conn.close()


if __name__ == "__main__":
    unittest.main()
