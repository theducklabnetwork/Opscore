"""First-run bundled reference data: load once, never replace, never crash."""

import sqlite3
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


if __name__ == "__main__":
    unittest.main()
