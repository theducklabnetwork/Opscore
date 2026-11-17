"""Store directory import (monthly-sales workbook), auto-fill lookup,
Monthly Setup history button, BD Target field and sales-history charts.
"""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path

import openpyxl

from ducklab import ops_charts, store_ops_db
from ducklab import store_directory_import as sdi
from ducklab.app_service import AppService
from ducklab.auth_service import AuthService
from ducklab.operations_repository import OperationsRepository
from ducklab.operations_write_service import OperationsWriteService

PASSWORD = "test-password-123"


def write_workbook(path: Path, rows, months=(date(2025, 1, 1),
                                            date(2025, 2, 1),
                                            date(2026, 1, 1))):
    """Header row + one row per store; months start at column I."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "2025-2026"
    ws.append(["No.", "Store Code", "Store Name", "Display Area", "Concept",
               "State", "Zone", "BD Target", *months])
    for i, row in enumerate(rows, start=1):
        ws.append([i, *row])
    wb.save(path)
    return path


class DirectoryBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.db = self.tmp / "ops.db"
        store_ops_db.init_db(self.db)
        self.xlsx = self.tmp / "monthly.xlsx"

    def rows(self):
        return [
            # code, name, area, concept, state, zone, bd, Jan25, Feb25, Jan26
            ["B001", "CHP", 10388, "Shop Lot", "WP", "KV-6", "NIL",
             437363.42, 407251.66, 460638.95],
            ["B002", "KLM", 7273, "Shop Lot", "WP", "KV-4", 148000,
             425862.52, 0, 452915.77],
            ["Z012", "EVTM", 0, 0, 0, 0, 0, 100.00, 200.00, 300.00],
        ]


class ParseTests(DirectoryBase):
    def test_stores_history_and_warnings(self):
        write_workbook(self.xlsx, self.rows())
        stores, history, warnings = sdi.parse_workbook(
            sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))
        self.assertEqual([s["code"] for s in stores],
                         ["B001", "B002", "Z012"])
        self.assertEqual(stores[0], {"code": "B001", "name": "CHP",
                                     "state": "WP", "zone": "KV-6",
                                     "display_area": 10388})
        # Concept and BD Target are never imported
        self.assertNotIn("concept", stores[0])
        self.assertNotIn("bd_target", stores[0])
        # A numeric 0 in State/Zone is blank, not the text "0.0"
        self.assertIsNone(stores[2]["state"])
        self.assertIsNone(stores[2]["zone"])
        self.assertIsNone(stores[2]["display_area"])
        self.assertTrue(any("Z012" in w for w in warnings))

    def test_zero_and_blank_months_are_skipped_not_zero(self):
        write_workbook(self.xlsx, self.rows())
        _, history, _ = sdi.parse_workbook(
            sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))
        keys = {(h["code"], h["month"]) for h in history}
        self.assertIn(("B001", "2025-01"), keys)
        self.assertNotIn(("B002", "2025-02"), keys)   # typed 0 -> no row
        self.assertEqual(len([h for h in history if h["code"] == "B002"]), 2)

    def test_amounts_are_integer_cents_rounded_half_up(self):
        write_workbook(self.xlsx, self.rows())
        _, history, _ = sdi.parse_workbook(
            sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))
        first = next(h for h in history if h["code"] == "B001")
        self.assertEqual(first["sales_cents"], 43736342)
        self.assertIsInstance(first["sales_cents"], int)

    def test_month_headers_come_from_excel_serial_dates(self):
        self.assertEqual(sdi._month_key(45658.0), "2025-01")
        self.assertEqual(sdi._month_key(datetime(2026, 10, 1)), "2026-10")
        self.assertEqual(sdi._month_key(date(2026, 10, 1)), "2026-10")
        self.assertEqual(sdi._month_key("2026-10-01"), "2026-10")
        self.assertIsNone(sdi._month_key("Total"))
        self.assertIsNone(sdi._month_key(None))
        self.assertIsNone(sdi._month_key(12.0))       # not a date serial

    def test_mid_month_header_is_used_but_warned(self):
        write_workbook(self.xlsx, self.rows(),
                       months=(date(2025, 1, 1), date(2026, 12, 13)))
        _, history, warnings = sdi.parse_workbook(
            sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))
        self.assertIn("2026-12", {h["month"] for h in history})
        self.assertTrue(any("mid-month" in w for w in warnings))

    def test_missing_month_columns_rejected(self):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "2025-2026"
        ws.append(["No.", "Store Code", "Store Name"])
        ws.append([1, "B001", "CHP"])
        wb.save(self.xlsx)
        with self.assertRaises(sdi.DirectoryImportError):
            sdi.parse_workbook(sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))

    def test_duplicate_code_and_non_numeric_sales_rejected(self):
        rows = self.rows()
        rows.append(["B001", "CHP2", 1, "Mall", "WP", "KV-1", None,
                     1, 2, 3])
        write_workbook(self.xlsx, rows)
        with self.assertRaises(sdi.DirectoryImportError) as ctx:
            sdi.parse_workbook(sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))
        self.assertIn("appears twice", str(ctx.exception))

        write_workbook(self.xlsx, [["B001", "CHP", 1, "Mall", "WP", "KV-1",
                                    None, "abc", 2, 3]])
        with self.assertRaises(sdi.DirectoryImportError):
            sdi.parse_workbook(sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))

    def test_negative_sales_rejected(self):
        write_workbook(self.xlsx, [["B001", "CHP", 1, "Mall", "WP", "KV-1",
                                    None, -5, 2, 3]])
        with self.assertRaises(sdi.DirectoryImportError):
            sdi.parse_workbook(sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))

    def test_missing_store_code_rejected(self):
        write_workbook(self.xlsx, [[None, "CHP", 1, "Mall", "WP", "KV-1",
                                    None, 1, 2, 3]])
        with self.assertRaises(sdi.DirectoryImportError):
            sdi.parse_workbook(sdi.dsc_import.read_sheet(self.xlsx, "2025-2026"))


class ImportTests(DirectoryBase):
    def test_import_replaces_directory_and_touches_no_stores(self):
        write_workbook(self.xlsx, self.rows())
        result = sdi.import_workbook(self.xlsx, self.db)
        self.assertEqual(result["stores"], 3)
        self.assertEqual(result["history"], 8)
        self.assertEqual(result["months"], 3)
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM stores").fetchone()[0], 0)
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM daily_records").fetchone()[0], 0)
        finally:
            conn.close()
        info = sdi.directory_info(self.db)
        self.assertEqual(info["stores"], 3)
        self.assertEqual(info["first_month"], "2025-01")
        self.assertEqual(info["source_name"], "monthly.xlsx")

        # Re-running replaces rather than duplicating
        result = sdi.import_workbook(self.xlsx, self.db)
        self.assertEqual(sdi.directory_info(self.db)["history"], 8)

    def test_failed_import_changes_nothing(self):
        write_workbook(self.xlsx, self.rows())
        sdi.import_workbook(self.xlsx, self.db)
        write_workbook(self.xlsx, [["B900", "BAD", 1, "Mall", "WP", "KV-1",
                                    None, "nope", 2, 3]])
        with self.assertRaises(sdi.DirectoryImportError):
            sdi.import_workbook(self.xlsx, self.db)
        self.assertEqual(sdi.directory_info(self.db)["stores"], 3)

    def test_missing_database_and_old_schema_rejected(self):
        write_workbook(self.xlsx, self.rows())
        with self.assertRaises(sdi.DirectoryImportError):
            sdi.import_workbook(self.xlsx, self.tmp / "none.db")
        old = self.tmp / "old.db"
        store_ops_db.init_db(old)
        conn = sqlite3.connect(old)
        conn.execute("PRAGMA user_version = 15")
        conn.commit()
        conn.close()
        with self.assertRaises(sdi.DirectoryImportError) as ctx:
            sdi.import_workbook(self.xlsx, old)
        self.assertIn("v15", str(ctx.exception))


class LookupTests(DirectoryBase):
    def setUp(self):
        super().setUp()
        write_workbook(self.xlsx, self.rows())
        sdi.import_workbook(self.xlsx, self.db)

    def test_exact_code_or_name_case_insensitive(self):
        self.assertEqual(sdi.lookup(self.db, code="b001")["name"], "CHP")
        self.assertEqual(sdi.lookup(self.db, name="chp")["code"], "B001")
        self.assertEqual(sdi.lookup(self.db, code="B002")["zone"], "KV-4")

    def test_partial_input_never_matches(self):
        self.assertIsNone(sdi.lookup(self.db, code="B00"))
        self.assertIsNone(sdi.lookup(self.db, name="CH"))
        self.assertIsNone(sdi.lookup(self.db, code="", name=""))
        self.assertIsNone(sdi.lookup(self.db, code="NOPE"))

    def test_blank_directory_fields_come_back_empty(self):
        match = sdi.lookup(self.db, code="Z012")
        self.assertEqual(match["state"], "")
        self.assertEqual(match["zone"], "")

    def test_comparison_figures(self):
        # Feb 2025: last month = Jan 2025; no Jan 2024 in the workbook
        self.assertEqual(
            sdi.comparison_figures(self.db, "B001", "2025-02"),
            {"sales_last_month_cents": 43736342,
             "sales_last_year_cents": None})
        # Jan 2026: both available
        self.assertEqual(
            sdi.comparison_figures(self.db, "B001", "2026-01"),
            {"sales_last_month_cents": None,      # Dec 2025 not a column
             "sales_last_year_cents": 46063895 - 46063895 + 43736342})
        # Feb 2025 for B002 is a typed 0 -> never RM 0
        self.assertIsNone(sdi.comparison_figures(
            self.db, "B002", "2025-03")["sales_last_month_cents"])

    def test_history_for_is_ordered_months(self):
        history = sdi.history_for(self.db, "B001")
        self.assertEqual(list(history), ["2025-01", "2025-02", "2026-01"])


class AppServiceTests(DirectoryBase):
    """Authorization + the store-scoped directory reads."""

    def setUp(self):
        super().setUp()
        write_workbook(self.xlsx, self.rows())
        sdi.import_workbook(self.xlsx, self.db)
        self.app = AppService(self.db)
        self.admin = AuthService(self.db).bootstrap_first_admin(
            "admin@example.com", "Admin", PASSWORD)
        self.store_id = OperationsWriteService(self.db).register_store(
            "B001", "CHP", concept="Outlet", state="WP", zone="KV-6")

    def test_lookup_requires_a_store_manager(self):
        self.assertEqual(
            self.app.lookup_store_directory(self.admin, code="B001")["name"],
            "CHP")
        viewer = self.app.create_user(self.admin, "v@example.com", "V", PASSWORD)
        from ducklab.auth_service import AuthorizationError
        with self.assertRaises(AuthorizationError):
            self.app.lookup_store_directory(viewer, code="B001")

    def test_history_and_comparison_are_store_scoped(self):
        history = self.app.store_directory_history(self.admin, self.store_id)
        self.assertEqual(history["2025-01"], 43736342)
        figures = self.app.store_directory_comparison(
            self.admin, self.store_id, "2026-01")
        self.assertEqual(figures["sales_last_year_cents"], 43736342)

    def test_import_via_service_replaces_directory(self):
        result = self.app.import_store_directory(self.admin, self.xlsx)
        self.assertEqual(result["stores"], 3)
        self.assertEqual(self.app.store_directory_info(self.admin)["stores"], 3)


class BdTargetTests(DirectoryBase):
    def test_bd_target_round_trip_and_clear(self):
        service = OperationsWriteService(self.db)
        store_id = service.register_store("B001", "CHP")
        service.configure_month(store_id, "2026-10",
                                bd_target_cents=14800000)
        cfg = OperationsRepository(self.db).get_month_config(
            store_id, "2026-10")
        self.assertEqual(cfg.bd_target_cents, 14800000)
        # Informational only: no target levels are derived from it
        from ducklab import kpi
        self.assertEqual(kpi.sales_target_levels_cents(cfg)["level1"], None)
        service.configure_month(store_id, "2026-10", bd_target_cents=None)
        self.assertIsNone(OperationsRepository(self.db).get_month_config(
            store_id, "2026-10").bd_target_cents)

    def test_negative_bd_target_rejected(self):
        from ducklab.operations_write_repository import ValidationError
        service = OperationsWriteService(self.db)
        store_id = service.register_store("B001", "CHP")
        with self.assertRaises(ValidationError):
            service.configure_month(store_id, "2026-10", bd_target_cents=-1)

    def test_backup_expects_the_new_columns_and_tables(self):
        from ducklab import backup
        self.assertIn("bd_target_cents",
                      backup.EXPECTED_COLUMNS["monthly_configs"])
        self.assertIn("store_directory", backup.EXPECTED_COLUMNS)
        self.assertIn("store_directory_history", backup.EXPECTED_COLUMNS)


class HistoryChartTests(unittest.TestCase):
    def test_timeline_gaps_and_labels(self):
        fig = ops_charts.history_timeline(
            ["2025-01", "2025-02", "2025-03"], [100000, None, 300000],
            dark=False)
        trace = fig.data[0]
        self.assertEqual(list(trace.x), ["Jan 25", "Feb 25", "Mar 25"])
        self.assertEqual(list(trace.y), [1000.0, None, 3000.0])
        self.assertFalse(trace.connectgaps)

    def test_year_compare_one_line_per_year_with_gaps(self):
        fig = ops_charts.history_year_compare(
            [2025, 2026], [[100000] * 12,
                           [None, 200000] + [None] * 10], dark=True)
        self.assertEqual(len(fig.data), 2)
        self.assertEqual(fig.data[0].name, "2025")
        self.assertEqual(list(fig.data[1].x)[:2], ["Jan", "Feb"])
        self.assertIsNone(fig.data[1].y[0])
        self.assertEqual(fig.data[1].y[1], 2000.0)

    def test_year_compare_source_shaping(self):
        from ducklab.ops_sales_state import SalesState
        years, series = SalesState._year_compare(
            (["2025-01", "2026-02"], [111, 222]))
        self.assertEqual(years, [2025, 2026])
        self.assertEqual(series[0][0], 111)
        self.assertIsNone(series[0][1])
        self.assertEqual(series[1][1], 222)


class MigrationTests(unittest.TestCase):
    def test_v15_database_upgrades_and_keeps_data(self):
        tmp = Path(tempfile.mkdtemp())
        db = tmp / "v15.db"
        store_ops_db.init_db(db)
        conn = sqlite3.connect(db)
        conn.execute("INSERT INTO stores (code, name) VALUES ('AB01', 'SLP')")
        conn.execute("INSERT INTO monthly_configs (store_id, month, "
                     "sales_last_year_cents) VALUES (1, '2026-10', 12345)")
        conn.execute("PRAGMA user_version = 15")
        conn.execute("DELETE FROM schema_migrations WHERE version > 15")
        conn.commit()
        conn.close()
        store_ops_db.init_db(db)
        conn = sqlite3.connect(db)
        try:
            self.assertEqual(conn.execute(
                "PRAGMA user_version").fetchone()[0], 17)
            self.assertEqual(conn.execute(
                "SELECT sales_last_year_cents FROM monthly_configs"
            ).fetchone()[0], 12345)
            self.assertIsNone(conn.execute(
                "SELECT bd_target_cents FROM monthly_configs").fetchone()[0])
            for table in ("store_directory", "store_directory_history",
                          "store_directory_info"):
                self.assertEqual(conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        finally:
            conn.close()
        # Pre-migration backup was taken
        self.assertTrue(list((tmp / "backups").glob(
            "pre-migration-v15-to-v17-*.db")))


if __name__ == "__main__":
    unittest.main()
