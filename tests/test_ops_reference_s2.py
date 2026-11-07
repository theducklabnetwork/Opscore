"""S2 tests: Reference Data — shared DSC importer (ducklab/dsc_import.py)
and the Settings > Reference Data category actions.

The importer is the module behind both scripts/import_dsc.py and the
page: good file, bad headers and rollback on error, plus the v5
last-import timestamp and the "what's loaded" summary. Category actions
(Add / Rename / Move Up/Down; no delete) go through AppService and the
SettingsState handlers. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_reference_s2.py -v
"""

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.dsc_import import DscImportError, import_workbook, \
    reference_data_info
from ducklab.operations_write_repository import (
    DuplicateRecordError, RecordNotFoundError, ValidationError,
)

from test_daily_targets_t1 import (
    MONTH_NAMES, build_workbook, july_dsc_rows, spr_row,
)

PW = "Sup3rSecure!x"
PW2 = "An0therGood42"


# --- shared importer ---------------------------------------------------------
class ImportFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "ops.db"
        store_ops_db.init_db(self.db)
        self.xlsx = self.tmp / "dsc.xlsx"

    def tearDown(self):
        self._tmp.cleanup()

    def rows(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()


class SharedImporterTests(ImportFixture):
    def test_good_file_counts_and_timestamp(self):
        build_workbook(
            self.xlsx,
            july_dsc_rows(ph=0.05) + july_dsc_rows("Kedah", "Toys"),
            [spr_row("Mall", "Johor", {"July": 25.5}, {"July": 3.2})])
        result = import_workbook(self.xlsx, self.db)
        self.assertEqual(result["pairs"], 2)
        self.assertEqual(result["benchmarks"], 1)
        self.assertEqual(result["benchmark_months"], 1)  # only July rows
        # each dsc block: 7 days x (July + August); the first adds a PH
        # row (July only)
        self.assertEqual(result["shares"], 15 + 14)
        self.assertEqual(result["warnings"], [])
        # v5: the last-import timestamp is recorded in the same transaction
        imported_at = self.rows(
            "SELECT imported_at FROM reference_data_info WHERE id = 1")[0][0]
        self.assertTrue(imported_at)
        info = reference_data_info(self.db)
        self.assertEqual(info["pairs"], 2)
        self.assertEqual(info["benchmark_months"], 1)
        self.assertEqual(info["imported_at"], imported_at)

    def test_unknown_state_warns(self):
        build_workbook(self.xlsx, july_dsc_rows("Narnia"), [])
        result = import_workbook(self.xlsx, self.db)
        self.assertEqual(
            result["warnings"],
            ["State 'Narnia' is not a store State option"])

    def test_bad_headers_rejected_nothing_written(self):
        import openpyxl
        build_workbook(self.xlsx, july_dsc_rows(), [])
        wb = openpyxl.load_workbook(self.xlsx)
        wb["DSC"]["E4"] = "Totals"          # shifted month header
        wb.save(self.xlsx)
        with self.assertRaises(DscImportError) as ctx:
            import_workbook(self.xlsx, self.db)
        self.assertIn("E4 is 'Totals', expected December",
                      str(ctx.exception))
        self.assertEqual(
            self.rows("SELECT COUNT(*) FROM contribution_shares"), [(0,)])
        self.assertEqual(
            self.rows("SELECT COUNT(*) FROM reference_data_info"), [(0,)])

    def test_rollback_on_row_error_keeps_previous_import(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        import_workbook(self.xlsx, self.db)
        before = self.rows(
            "SELECT * FROM contribution_shares ORDER BY 1,2,3,4")
        stamp_before = self.rows(
            "SELECT imported_at FROM reference_data_info WHERE id = 1")
        bad = self.tmp / "bad.xlsx"
        rows = july_dsc_rows("Perak", "Outlet")
        rows[2][2] = "Holiday"
        build_workbook(bad, rows, [])
        with self.assertRaises(DscImportError) as ctx:
            import_workbook(bad, self.db)
        self.assertIn("DSC row 7: Day 'Holiday' is not Mon–Sun or PH",
                      str(ctx.exception))
        # all-or-nothing: data and timestamp untouched
        self.assertEqual(
            self.rows("SELECT * FROM contribution_shares ORDER BY 1,2,3,4"),
            before)
        self.assertEqual(
            self.rows("SELECT imported_at FROM reference_data_info "
                      "WHERE id = 1"), stamp_before)

    def test_reimport_replaces_and_refreshes_timestamp(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        import_workbook(self.xlsx, self.db)
        first = self.rows("SELECT imported_at FROM reference_data_info "
                          "WHERE id = 1")[0][0]
        build_workbook(self.xlsx, july_dsc_rows("Perak", "Outlet"), [])
        import_workbook(self.xlsx, self.db)
        self.assertEqual(self.rows(
            "SELECT DISTINCT state, concept FROM contribution_shares"),
            [("Perak", "Outlet")])
        last = self.rows("SELECT imported_at FROM reference_data_info "
                         "WHERE id = 1")[0][0]
        self.assertGreaterEqual(last, first)

    def test_missing_database_and_old_schema_refused(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        with self.assertRaises(DscImportError):
            import_workbook(self.xlsx, self.tmp / "none.db")
        old = self.tmp / "v4.db"
        conn = sqlite3.connect(old)
        conn.execute("PRAGMA user_version = 4")
        conn.close()
        with self.assertRaises(DscImportError) as ctx:
            import_workbook(self.xlsx, old)
        self.assertIn("schema is v4", str(ctx.exception))

    def test_info_empty_before_first_import(self):
        info = reference_data_info(self.db)
        self.assertEqual(
            info, {"pairs": 0, "benchmark_months": 0, "imported_at": None,
                   "bundled": False})


# --- category actions ----------------------------------------------------------
class CategoryFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ref.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", PW)
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.other = self.app.register_store(self.admin, "B02", "Beta")

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores=()):
        u = self.app.create_user(self.admin, email, "T User", PW2)
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)

    def names(self, store=None):
        return [c.name for c in self.app.get_expense_categories(
            self.admin, store or self.sid)]


class CategoryServiceTests(CategoryFixture):
    def test_add_rename_move_round_trip(self):
        a = self.app.add_expense_category(self.admin, self.sid, "Rent",
                                          sort_order=0)
        b = self.app.add_expense_category(self.admin, self.sid, "Power",
                                          sort_order=1)
        c = self.app.add_expense_category(self.admin, self.sid, "Water",
                                          sort_order=2)
        self.assertEqual(self.names(), ["Rent", "Power", "Water"])
        self.app.rename_expense_category(self.admin, self.sid, b, "Electric")
        self.assertEqual(self.names(), ["Rent", "Electric", "Water"])
        self.app.move_expense_category(self.admin, self.sid, c, -1)
        self.assertEqual(self.names(), ["Rent", "Water", "Electric"])
        self.app.move_expense_category(self.admin, self.sid, a, 1)
        self.assertEqual(self.names(), ["Water", "Rent", "Electric"])
        # at the edges: no-op
        self.app.move_expense_category(self.admin, self.sid, c, -1)
        self.app.move_expense_category(self.admin, self.sid, b, 1)
        self.assertEqual(self.names(), ["Water", "Rent", "Electric"])

    def test_move_normalizes_tied_sort_orders(self):
        # rows inserted with the default sort_order=0 (legacy data):
        # order came from the id tie-break; a move must still work
        a = self.app.add_expense_category(self.admin, self.sid, "A")
        b = self.app.add_expense_category(self.admin, self.sid, "B")
        self.app.move_expense_category(self.admin, self.sid, b, -1)
        self.assertEqual(self.names(), ["B", "A"])

    def test_move_is_per_store(self):
        a = self.app.add_expense_category(self.admin, self.sid, "A")
        self.app.add_expense_category(self.admin, self.other, "X")
        self.app.add_expense_category(self.admin, self.sid, "B")
        self.app.move_expense_category(self.admin, self.sid, a, 1)
        self.assertEqual(self.names(), ["B", "A"])
        self.assertEqual(self.names(self.other), ["X"])

    def test_validation_and_ownership(self):
        a = self.app.add_expense_category(self.admin, self.sid, "A")
        with self.assertRaises((ValidationError, DuplicateRecordError)):
            self.app.add_expense_category(self.admin, self.sid, "A")
        with self.assertRaises(RecordNotFoundError):
            self.app.move_expense_category(self.admin, self.other, a, 1)
        with self.assertRaises(RecordNotFoundError):
            self.app.rename_expense_category(self.admin, self.other, a, "H")

    def test_administrator_allowed_viewer_denied(self):
        admin = self.user("a@ducklab.com", ("administrator",), (self.sid,))
        viewer = self.user("v@ducklab.com", ("viewer",), (self.sid,))
        self.app.add_expense_category(admin, self.sid, "A")
        with self.assertRaises(AuthorizationError):
            self.app.add_expense_category(viewer, self.sid, "B")
        with self.assertRaises(AuthorizationError):
            self.app.move_expense_category(viewer, self.sid, 1, 1)
        with self.assertRaises(AuthorizationError):
            self.app.reference_data_info(viewer)
        with self.assertRaises(AuthorizationError):
            self.app.import_dsc_workbook(viewer, Path(self._tmp.name)
                                         / "nope.xlsx")


class CategoryStateTests(CategoryFixture):
    def bound(self, fn, actor=None, st=None):
        """Run ``fn(state)`` with the page's AppService/identity patched;
        pass ``st`` to keep form fields set on an earlier state."""
        from ducklab import ops_settings_pages as mod
        from ducklab.ops_settings_pages import SettingsState
        if st is None:
            st = SettingsState()
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(SettingsState, "_actor",
                                  lambda s_: actor or self.admin), \
                mock.patch.object(SettingsState, "_selected_store_id",
                                  lambda s_: self.sid):
            fn(st)
        return st

    def test_load_lists_categories_in_order(self):
        self.app.add_expense_category(self.admin, self.sid, "Rent",
                                      sort_order=0)
        self.app.add_expense_category(self.admin, self.sid, "Power",
                                      sort_order=1)
        st = self.bound(lambda s: s.load_settings())
        self.assertEqual([c["name"] for c in st.ref_categories],
                         ["Rent", "Power"])
        self.assertEqual((st.ref_categories[0]["first"],
                          st.ref_categories[-1]["last"]), ("1", "1"))
        self.assertEqual(st.ref_imported_at, "")
        self.assertFalse(st.ref_has_data)

    def test_add_via_state_appends(self):
        self.app.add_expense_category(self.admin, self.sid, "Rent",
                                      sort_order=0)
        st = self.bound(lambda s: s.load_settings())
        st.cat_new_name = "Power"
        self.bound(lambda s: s.add_category(), st=st)
        self.assertEqual([c["name"] for c in st.ref_categories],
                         ["Rent", "Power"])
        self.assertEqual(st.cat_new_name, "")
        self.assertEqual(st.ref_notice, "Category added.")

    def test_add_duplicate_shows_error(self):
        self.app.add_expense_category(self.admin, self.sid, "Rent")
        st = self.bound(lambda s: s.load_settings())
        st.cat_new_name = "Rent"
        self.bound(lambda s: s.add_category(), st=st)
        self.assertTrue(st.ref_error)

    def test_rename_and_move_via_state(self):
        a = self.app.add_expense_category(self.admin, self.sid, "A",
                                          sort_order=0)
        b = self.app.add_expense_category(self.admin, self.sid, "B",
                                          sort_order=1)
        st = self.bound(lambda s: s.load_settings())
        self.bound(lambda s: s.open_rename(str(a)), st=st)
        self.assertEqual(st.rename_name, "A")
        st.rename_name = "Alpha"
        self.bound(lambda s: s.save_rename(), st=st)
        self.assertEqual([c["name"] for c in st.ref_categories],
                         ["Alpha", "B"])
        self.assertFalse(st.rename_open)
        self.bound(lambda s: s.move_category(str(b), "up"), st=st)
        self.assertEqual([c["name"] for c in st.ref_categories],
                         ["B", "Alpha"])

    def test_reference_tab_hidden_for_viewer(self):
        viewer = self.user("v@ducklab.com", ("viewer",), (self.sid,))
        st = self.bound(lambda s: s.load_settings(), actor=viewer)
        self.assertNotIn("reference", st.visible_tabs)
        self.assertFalse(st.can_manage_reference)


class ImportStateTests(ImportFixture):
    """SettingsState DSC handlers: staging, confirm and result summary."""

    def setUp(self):
        super().setUp()
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", PW)

    def state(self):
        from ducklab import ops_settings_pages as mod
        from ducklab.ops_settings_pages import SettingsState
        st = SettingsState()
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(SettingsState, "_actor",
                                  lambda s_: self.admin):
            st.load_settings()
        return st

    def run_import(self, st):
        from ducklab import ops_settings_pages as mod
        from ducklab.ops_settings_pages import SettingsState
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(SettingsState, "_actor",
                                  lambda s_: self.admin):
            st.confirm_dsc_import()
        return st

    def test_first_import_needs_no_confirmation(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        st = self.state()
        self.assertFalse(st.ref_has_data)
        st._dsc_pending_path = str(self.xlsx)
        st.dsc_pending_name = "dsc.xlsx"
        # no existing data -> no confirmation dialog; import runs directly
        self.run_import(st)
        self.assertFalse(st.dsc_confirm_open)
        self.assertTrue(st.dsc_result[0].startswith("Imported"))
        self.assertTrue(st.ref_has_data)
        self.assertEqual(st.ref_pairs, 1)
        self.assertTrue(st.ref_imported_at)

    def test_staging_opens_confirmation_only_when_data_exists(self):
        import asyncio
        from ducklab import ops_settings_pages as mod
        from ducklab.ops_settings_pages import SettingsState
        build_workbook(self.xlsx, july_dsc_rows(), [])
        upload = mock.Mock()
        upload.filename = "dsc.xlsx"

        async def read():
            return self.xlsx.read_bytes()
        upload.read = read

        def stage(st):
            with mock.patch.object(mod, "app_service", self.app), \
                    mock.patch.object(SettingsState, "_actor",
                                      lambda s_: self.admin):
                coro = SettingsState.handle_dsc_upload.fn(st, [upload])
                follow = asyncio.run(coro) if coro is not None else None
                if follow is not None:
                    # no existing data: the handler returns confirm_dsc_import
                    follow.fn(st)

        # first upload on an empty database: staged and imported directly
        st = self.state()
        self.assertFalse(st.ref_has_data)
        stage(st)
        self.assertFalse(st.dsc_confirm_open)
        self.assertTrue(st.ref_has_data)
        self.assertTrue(st.dsc_result[0].startswith("Imported"))
        # second upload with data loaded: confirmation first, nothing
        # written until confirmed
        before = self.rows(
            "SELECT * FROM contribution_shares ORDER BY 1,2,3,4")
        st.dsc_result = []
        stage(st)
        self.assertTrue(st.dsc_confirm_open)
        self.assertEqual(st.dsc_pending_name, "dsc.xlsx")
        self.assertTrue(st._dsc_pending_path)
        self.assertEqual(
            self.rows("SELECT * FROM contribution_shares ORDER BY 1,2,3,4"),
            before)

    def test_replacement_asks_confirmation_and_summary(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        import_workbook(self.xlsx, self.db)
        st = self.state()
        self.assertTrue(st.ref_has_data)
        st._dsc_pending_path = str(self.xlsx)
        st.dsc_pending_name = "dsc.xlsx"
        st.dsc_confirm_open = True
        self.run_import(st)
        self.assertFalse(st.dsc_confirm_open)
        self.assertEqual(st.dsc_error, "")
        self.assertIn("Imported 14 day shares (1 State/Concept pairs)",
                      st.dsc_result[0])
        self.assertEqual(st._dsc_pending_path, "")

    def test_bad_workbook_error_and_unchanged(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        import_workbook(self.xlsx, self.db)
        rows = july_dsc_rows("Perak", "Outlet")
        rows[0][2] = "Funday"
        build_workbook(self.tmp / "bad.xlsx", rows, [])
        st = self.state()
        st._dsc_pending_path = str(self.tmp / "bad.xlsx")
        st.dsc_pending_name = "bad.xlsx"
        self.run_import(st)
        self.assertIn("Import failed; nothing changed.", st.dsc_error)
        self.assertIn("Day 'Funday' is not Mon–Sun or PH", st.dsc_error)
        self.assertEqual(self.rows(
            "SELECT DISTINCT state FROM contribution_shares"), [("Johor",)])

    def test_cancel_discards_staged_file(self):
        st = self.state()
        staged = self.tmp / "staged.xlsx"
        staged.write_bytes(b"x")
        st._dsc_pending_path = str(staged)
        st.dsc_pending_name = "staged.xlsx"
        st.dsc_confirm_open = True
        st.cancel_dsc_import()
        self.assertFalse(st.dsc_confirm_open)
        self.assertEqual(st._dsc_pending_path, "")
        self.assertFalse(staged.exists())


if __name__ == "__main__":
    unittest.main()
