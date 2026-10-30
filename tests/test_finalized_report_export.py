"""Finalized Monthly Report Excel export uses only the selected snapshot."""

import tempfile
import unittest
from datetime import date
from io import BytesIO
from pathlib import Path
from unittest import mock

from openpyxl import load_workbook

from ducklab import design as ds
from ducklab import ops_finalize
from ducklab import ops_state
from ducklab import report_export
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_snapshots import snapshot_to_report
from ducklab.ops_state import OpsState
from ducklab.report_presenter import _version_label


class FinalizedReportExportTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self.tmp.name) / "finalized_export.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "S01", "Store A")
        self.app.configure_month(
            self.admin, self.sid, "2026-09", level1_base_cents=20000000,
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=5, salary_basic_allowance_cents=1000000)
        self.app.record_daily(
            self.admin, self.sid, date(2026, 9, 1), sales_cents=100000)
        self.app.finalize_month(self.admin, self.sid, "2026-09")

    def tearDown(self):
        self.tmp.cleanup()

    def user(self, email, role, store=True):
        user = self.app.create_user(self.admin, email, "User", "Passw0rd!xyz")
        self.app.assign_role(self.admin, user.id, role)
        if store:
            self.app.assign_store(self.admin, user.id, self.sid)
        return self.app.auth.get_user(user.id)

    def export(self, actor=None, version=None):
        actor = actor or self.admin
        state = OpsState()
        state.stores = [{"id": self.sid, "label": "S01 — Store A"}]
        state.selected_store_label = "S01 — Store A"
        state.report_mode = "finalized"
        if version is not None:
            row = next(row for row in self.app.list_report_versions(
                self.admin, self.sid, "2026-09") if row["version"] == version)
            state.selected_version_label = _version_label(row)
        with mock.patch.object(ops_state, "app_service", self.app), \
                mock.patch.object(ops_finalize, "app_service", self.app), \
                mock.patch.object(OpsState, "_actor", lambda _: actor), \
                mock.patch.object(OpsState, "_selected_month",
                                  lambda _: "2026-09"), \
                mock.patch.object(ops_finalize.rx, "download",
                                  side_effect=lambda **kw: kw):
            result = state.export_report()
        self.assertIsNotNone(result, state.error_message)
        return result, load_workbook(BytesIO(result["data"]))

    @staticmethod
    def summary(book):
        return {row[0]: row[1] for row in book["Summary"].iter_rows(
            min_row=2, max_col=2, values_only=True)}

    def test_snapshot_kpis_survive_live_edits(self):
        original = self.app.get_finalized_report(self.admin, self.sid, "2026-09")
        self.app.record_daily(
            self.admin, self.sid, date(2026, 9, 2), sales_cents=50000)
        result, book = self.export()
        self.assertEqual(self.summary(book)["Sales MTD"], 1000)
        self.assertEqual(
            original["report"]["sales"]["sales_mtd_cents"],
            snapshot_to_report(original).sales.sales_mtd_cents)
        self.assertEqual(result["filename"],
                         "opscore-S01-2026-09-finalized-v1.xlsx")

    def test_older_version_and_metadata(self):
        self.app.record_daily(
            self.admin, self.sid, date(2026, 9, 2), sales_cents=50000)
        self.app.correct_month(self.admin, self.sid, "2026-09")
        snap = self.app.get_finalized_report(
            self.admin, self.sid, "2026-09", version=1)
        result, book = self.export(version=1)
        self.assertEqual(result["filename"],
                         "opscore-S01-2026-09-finalized-v1.xlsx")
        self.assertEqual(book.sheetnames, ["Summary", "Manpower", "Stock Loss"])
        summary = self.summary(book)
        self.assertEqual(summary["Sales MTD"], 1000)
        self.assertEqual(summary["Store"], "S01 — Store A")
        self.assertEqual(summary["Month"], "2026-09")
        self.assertEqual(summary["Mode"], "Finalized")
        self.assertEqual(summary["Version"], 1)
        self.assertEqual(summary["Finalized Date and Time"],
                         ds.fmt_kl_datetime(snap["created_at"]))
        self.assertEqual(summary["Status"], "Superseded")
        self.assertNotIn("Actions Total", summary)
        _, current = self.export()
        self.assertEqual(self.summary(current)["Status"], "Current")

    def test_payroll_redaction_matches_live(self):
        viewer = self.user("viewer@ducklab.com", "viewer")
        _, book = self.export(viewer)
        summary = self.summary(book)
        self.assertIsNone(summary["Salary Cost"])
        self.assertIsNone(summary["Salary Ratio"])
        manpower = {row[0]: row[1] for row in book["Manpower"].iter_rows(
            min_row=2, max_col=2, values_only=True)}
        self.assertIsNone(manpower["Salary Cost"])
        self.assertEqual(manpower["Salary Status"], "Restricted")
        administrator = self.user("administrator@ducklab.com", "administrator")
        _, visible = self.export(administrator)
        self.assertEqual(self.summary(visible)["Salary Cost"], 10000)

    def test_unauthorized_store_access_is_rejected(self):
        outsider = self.user("outsider@ducklab.com", "viewer", store=False)
        with self.assertRaises(AuthorizationError):
            self.app.get_finalized_report(outsider, self.sid, "2026-09")

    def test_failure_preserves_selected_report_and_version(self):
        state = OpsState()
        state.stores = [{"id": self.sid, "label": "S01 — Store A"}]
        state.selected_store_label = "S01 — Store A"
        state.report_mode = "finalized"
        row = self.app.list_report_versions(self.admin, self.sid, "2026-09")[0]
        state.selected_version_label = _version_label(row)
        state.report = {"sales": {"mtd": "original"}}
        with mock.patch.object(ops_state, "app_service", self.app), \
                mock.patch.object(ops_finalize, "app_service", self.app), \
                mock.patch.object(OpsState, "_actor", lambda _: self.admin), \
                mock.patch.object(OpsState, "_selected_month",
                                  lambda _: "2026-09"), \
                mock.patch.object(report_export, "export_bytes",
                                  side_effect=ValueError("workbook failed")):
            self.assertIsNone(state.export_report())
        self.assertEqual(state.error_message, "workbook failed")
        self.assertEqual(state.selected_version_label, _version_label(row))
        self.assertEqual(state.report, {"sales": {"mtd": "original"}})

    def test_live_export_unchanged(self):
        report = self.app.preview_month(self.admin, self.sid, "2026-09")
        book = load_workbook(BytesIO(report_export.export_bytes(
            report, self.app.read_repo)))
        self.assertEqual(book.sheetnames, [
            "Summary", "Daily Sales", "Expenses", "Manpower", "Stock Loss", "Actions",
        ])
        self.assertEqual(report_export.export_filename(report),
                         "opscore-S01-2026-09.xlsx")
        self.assertNotIn("Mode", self.summary(book))


if __name__ == "__main__":
    unittest.main()
