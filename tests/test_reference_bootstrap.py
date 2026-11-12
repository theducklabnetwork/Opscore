"""First-run bundled reference data: load once, never replace, never crash."""

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

from ducklab import reference_bootstrap, store_ops_db
from ducklab.app_service import AppService
from ducklab.dsc_import import reference_data_info

from test_daily_targets_t1 import build_workbook, july_dsc_rows, spr_row


class BundledReferenceTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "ops.db"
        self.xlsx = self.tmp / "dsc.xlsx"
        build_workbook(
            self.xlsx, july_dsc_rows(),
            [spr_row("Mall", "Johor", {"July": 25.5}, {"July": 3.2})])

    def tearDown(self):
        self._tmp.cleanup()

    def rows(self, table):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(f"SELECT * FROM {table}").fetchall()
        finally:
            conn.close()

    def test_empty_tables_load_bundled_workbook(self):
        self.assertTrue(reference_bootstrap.load_bundled_reference_data(
            self.db, self.xlsx))
        self.assertTrue(self.rows("contribution_shares"))
        self.assertTrue(self.rows("basket_benchmarks"))
        self.assertTrue(reference_data_info(self.db)["bundled"])
        # A later startup leaves the imported data in place.
        before = self.rows("contribution_shares")
        self.assertFalse(reference_bootstrap.load_bundled_reference_data(
            self.db, self.xlsx))
        self.assertEqual(self.rows("contribution_shares"), before)

    def test_startup_populates_reference_tables_from_real_workbook(self):
        self.assertTrue(reference_bootstrap.load_bundled_reference_data(self.db))
        self.assertTrue(self.rows("contribution_shares"))
        self.assertTrue(self.rows("basket_benchmarks"))
        self.assertTrue(reference_data_info(self.db)["bundled"])

    def test_missing_workbook_logs_error_and_does_not_raise(self):
        missing = self.tmp / "missing.xlsx"
        with mock.patch.object(
                reference_bootstrap.dsc_import, "import_workbook"
        ) as importer, self.assertLogs(
                "ducklab.reference_bootstrap", "ERROR") as logs:
            self.assertFalse(reference_bootstrap.load_bundled_reference_data(
                self.db, missing))
        self.assertIn("Bundled reference workbook is missing", logs.output[0])
        self.assertIn(str(missing), logs.output[0])
        importer.assert_not_called()

    def test_either_nonempty_table_prevents_import(self):
        for populated in ("contribution_shares", "basket_benchmarks"):
            with self.subTest(populated=populated):
                with tempfile.TemporaryDirectory() as other:
                    db = Path(other) / "ops.db"
                    store_ops_db.init_db(db)
                    conn = sqlite3.connect(db)
                    if populated == "contribution_shares":
                        conn.execute(
                            "INSERT INTO contribution_shares VALUES "
                            "('Johor', 'Mall', 'Mon', 1, 0.1)")
                    else:
                        conn.execute(
                            "INSERT INTO basket_benchmarks VALUES "
                            "('Mall', 'Johor', 1, 25.5, 3.2)")
                    conn.commit()
                    conn.close()
                    with mock.patch.object(
                            reference_bootstrap.dsc_import, "import_workbook"
                    ) as importer:
                        self.assertFalse(
                            reference_bootstrap.load_bundled_reference_data(
                                db, self.xlsx))
                    importer.assert_not_called()

    def test_failed_import_logs_warning_and_does_not_raise(self):
        with mock.patch.object(
                reference_bootstrap.dsc_import, "import_workbook",
                side_effect=RuntimeError("broken workbook")), \
                self.assertLogs("ducklab.reference_bootstrap", "WARNING") as logs:
            self.assertFalse(reference_bootstrap.load_bundled_reference_data(
                self.db, self.xlsx))
        self.assertIn("continuing without it", logs.output[0])
        self.assertEqual(self.rows("contribution_shares"), [])
        self.assertEqual(self.rows("basket_benchmarks"), [])

    def test_settings_notice_only_for_bundled_data(self):
        app = AppService(self.db)
        admin = app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        from ducklab import ops_settings_pages as mod
        from ducklab.ops_settings_pages import SettingsState

        def load():
            state = SettingsState()
            with mock.patch.object(mod, "app_service", app), \
                    mock.patch.object(SettingsState, "_actor",
                                      lambda _: admin):
                state.load_settings()
            return state

        self.assertFalse(load().ref_bundled_data)
        reference_bootstrap.load_bundled_reference_data(self.db, self.xlsx)
        self.assertTrue(load().ref_bundled_data)
        app.import_dsc_workbook(admin, self.xlsx)  # explicit user upload
        self.assertFalse(load().ref_bundled_data)


class BundledArtifactTests(unittest.TestCase):
    def test_workbook_is_present_and_copied_into_deb(self):
        root = Path(__file__).resolve().parent.parent
        workbook = root / "ducklab" / "data" / "DSCSPR_alias_2027.xlsx"
        self.assertTrue(workbook.is_file())
        self.assertLess(workbook.stat().st_size, 200_000)
        build = (root / "scripts" / "build_deb.sh").read_text()
        self.assertIn('"$APP/ducklab/data"', build)
        self.assertIn('"$ROOT/ducklab/data/DSCSPR_alias_2027.xlsx"', build)

    def test_workbook_is_unignored_and_tracked(self):
        root = Path(__file__).resolve().parent.parent
        if not (root / ".git").exists():
            self.skipTest("Git metadata is not shipped in release copies")
        workbook = "ducklab/data/DSCSPR_alias_2027.xlsx"
        check_ignore = subprocess.run(
            ["git", "check-ignore", "--no-index", workbook], cwd=root,
            capture_output=True, text=True, check=False)
        self.assertEqual(check_ignore.returncode, 1, check_ignore.stderr)
        tracked = subprocess.run(
            ["git", "ls-files", "--error-unmatch", workbook], cwd=root,
            capture_output=True, text=True, check=False)
        self.assertEqual(tracked.returncode, 0, tracked.stderr)
        self.assertEqual(tracked.stdout.strip(), workbook)

    def test_windows_builds_include_workbook(self):
        root = Path(__file__).resolve().parent.parent
        spec = (root / "packaging" / "windows" / "opscore.spec").read_text()
        self.assertIn('ROOT / "ducklab" / "data" / "DSCSPR_alias_2027.xlsx"',
                      spec)
        self.assertIn('"ducklab/data"', spec)
        build = (root / "scripts" / "build_windows.ps1").read_text()
        self.assertIn('Join-Path $Root "ducklab\\data\\DSCSPR_alias_2027.xlsx"',
                      build)
        self.assertIn('Join-Path $App "ducklab\\data"', build)


class DefaultExpenseCategoryTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ops.db"
        store_ops_db.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def add_store(self, code="A01"):
        conn = sqlite3.connect(self.db)
        try:
            store_id = conn.execute(
                "INSERT INTO stores (code, name) VALUES (?, ?)",
                (code, code)).lastrowid
            conn.commit()
            return store_id
        finally:
            conn.close()

    def category_rows(self, store_id):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT id, name, sort_order FROM expense_categories "
                "WHERE store_id = ? ORDER BY sort_order, id",
                (store_id,)).fetchall()
        finally:
            conn.close()

    def test_empty_store_gets_all_ten_in_order(self):
        store_id = self.add_store()
        self.assertTrue(reference_bootstrap.seed_default_expense_categories(
            self.db))
        rows = self.category_rows(store_id)
        self.assertEqual(
            [row[1] for row in rows],
            [name for name, _pct
             in reference_bootstrap.DEFAULT_EXPENSE_CATEGORIES])
        self.assertEqual([row[2] for row in rows], list(range(10)))

    def test_existing_categories_are_untouched(self):
        store_id = self.add_store()
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO expense_categories (store_id, name, sort_order) "
                "VALUES (?, 'Custom', 42)", (store_id,))
            conn.commit()
        finally:
            conn.close()
        before = self.category_rows(store_id)
        self.assertFalse(reference_bootstrap.seed_default_expense_categories(
            self.db))
        self.assertEqual(self.category_rows(store_id), before)

    def test_seeding_runs_once_per_store(self):
        first = self.add_store()
        reference_bootstrap.seed_default_expense_categories(self.db)
        before = self.category_rows(first)
        second = self.add_store("B02")
        reference_bootstrap.seed_default_expense_categories(self.db)
        self.assertEqual(self.category_rows(first), before)
        self.assertEqual(len(self.category_rows(second)), 10)

    def test_startup_repairs_legacy_store_with_no_categories(self):
        """A store created before the create_store() seeding fix has zero
        categories; the startup lifespan task repairs it on next launch."""
        legacy = self.add_store("LEGACY")
        self.assertEqual(self.category_rows(legacy), [])
        self.assertTrue(reference_bootstrap.seed_default_expense_categories(
            self.db))
        self.assertEqual(len(self.category_rows(legacy)), 10)

    def test_startup_does_not_duplicate_already_seeded_store(self):
        """A store created by create_store() already has 10 categories; the
        startup task must be a no-op for it (no duplicates, no re-order)."""
        from ducklab.operations_write_service import OperationsWriteService
        store_id = OperationsWriteService(self.db).register_store(
            "NEW", "New Store")
        before = self.category_rows(store_id)
        self.assertEqual(len(before), 10)
        # Startup seeding: nothing to do.
        self.assertFalse(reference_bootstrap.seed_default_expense_categories(
            self.db))
        self.assertEqual(self.category_rows(store_id), before)

    def test_customised_categories_survive_startup(self):
        """A store that has customised (renamed / added) categories is not
        touched by startup seeding — even if some defaults were renamed."""
        from ducklab.operations_write_service import OperationsWriteService
        svc = OperationsWriteService(self.db)
        store_id = svc.register_store("CUST", "Customised")
        cats = self.category_rows(store_id)
        svc.rename_expense_category(store_id, cats[0][0], "My Power")
        svc.add_expense_category(store_id, "Custom Extra", sort_order=99)
        before = self.category_rows(store_id)
        self.assertFalse(reference_bootstrap.seed_default_expense_categories(
            self.db))
        self.assertEqual(self.category_rows(store_id), before)


if __name__ == "__main__":
    unittest.main()
