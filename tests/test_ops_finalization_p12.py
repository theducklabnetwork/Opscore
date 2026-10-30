"""Phase 1.2: monthly report finalization/correction workflow.

All tests use disposable databases; no operational record is locked and no
snapshot is edited by these workflows.
"""

import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

import ducklab.ops_state as ops_state
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.operations_snapshots import finalization_warnings
from ducklab.ops_state import OpsState, _version_label


class FinalizationFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "finalize.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sid, "2026-09", level1_base_cents=1000000,
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=5)
        self.app.record_daily(
            self.admin, self.sid, date(2026, 9, 1), sales_cents=100000)

    def tearDown(self):
        self.tmp.cleanup()

    def user(self, email, role):
        user = self.app.create_user(self.admin, email, "User", "Passw0rd!xyz")
        self.app.assign_role(self.admin, user.id, role)
        self.app.assign_store(self.admin, user.id, self.sid)
        return self.app.auth.get_user(user.id)

    def state(self):
        # These tests call state handlers directly, outside the runtime's
        # computed-var dependency graph.
        self.enterContext(mock.patch.object(OpsState, "_mark_dirty", lambda *args: None))
        state = OpsState()
        state.has_stores = True
        state.stores = [{"id": self.sid, "label": "A01 — Alpha", "active": True}]
        state.selected_store_label = "A01 — Alpha"
        state.selected_month_label = "September 2026"
        return state

    def bound(self, fn):
        with mock.patch.object(ops_state, "app_service", self.app), \
                mock.patch.object(OpsState, "_actor", lambda _: self.admin):
            return fn()


class ServiceFinalizationTests(FinalizationFixture):
    def test_permissions_and_deactivated_store(self):
        viewer = self.user("viewer@ducklab.com", "viewer")
        administrator = self.user("administrator@ducklab.com", "administrator")
        with self.assertRaises(AuthorizationError):
            self.app.finalize_month(viewer, self.sid, "2026-09")
        self.assertEqual(
            self.app.finalize_month(administrator, self.sid, "2026-09")["version"], 1)
        with self.assertRaises(AuthorizationError):
            self.app.correct_month(viewer, self.sid, "2026-09")
        self.app.set_store_active(self.admin, self.sid, False)
        for operation in (self.app.finalize_month, self.app.correct_month):
            with self.assertRaises(AuthorizationError):
                operation(self.admin, self.sid, "2026-09")

    def test_warnings_versioning_and_snapshot_immutability(self):
        report = self.app.preview_month(self.admin, self.sid, "2026-09")
        self.assertIn("Only 1 of 30 days have sales entered",
                      finalization_warnings(report))
        first = self.app.finalize_month(self.admin, self.sid, "2026-09")
        old = copy.deepcopy(self.app.get_finalized_report(
            self.admin, self.sid, "2026-09", version=1)["kpi_json"])
        self.app.record_daily(
            self.admin, self.sid, date(2026, 9, 2), sales_cents=50000)
        second = self.app.correct_month(self.admin, self.sid, "2026-09")
        self.assertEqual((first["version"], second["version"]), (1, 2))
        self.assertEqual(self.app.get_finalized_report(
            self.admin, self.sid, "2026-09", version=1)["kpi_json"], old)
        current = self.app.get_finalized_report(self.admin, self.sid, "2026-09")
        self.assertEqual(current["version"], 2)
        self.assertEqual(current["report"]["sales"]["sales_mtd_cents"], 150000)


class StateFinalizationTests(FinalizationFixture):
    def test_dialog_preflight_warnings_and_success_selects_new_version(self):
        state = self.state()
        self.bound(lambda: list(state.open_finalize()))
        self.assertTrue(state.finalize_dialog_open)
        self.assertEqual(state.finalize_next_version, 1)
        self.assertIn("Only 1 of 30 days have sales entered",
                      state.finalize_warnings)
        self.assertEqual(state.finalize_error, "")
        self.bound(lambda: list(state.save_finalization()))
        self.assertFalse(state.finalize_dialog_open)
        self.assertFalse(state.finalize_saving)
        self.assertEqual(state.report_mode, "finalized")
        self.assertIn("Version 1", state.selected_version_label)
        self.assertIn("Current", state.selected_version_label)
        self.assertEqual(state.report["mode"], {"label": "finalized"})

    def test_correction_uses_live_even_with_old_version_selected(self):
        self.app.finalize_month(self.admin, self.sid, "2026-09")
        self.app.record_daily(
            self.admin, self.sid, date(2026, 9, 2), sales_cents=50000)
        state = self.state()
        state.report_mode = "finalized"
        state.selected_version_label = _version_label(
            self.app.list_report_versions(self.admin, self.sid, "2026-09")[0])
        self.bound(lambda: list(state.refresh_report()))
        self.assertEqual(state.report["sales"]["mtd"], "RM 1,000.00")
        self.bound(lambda: list(state.open_correct()))
        self.assertEqual(state.finalize_next_version, 2)
        self.bound(lambda: list(state.save_finalization()))
        self.assertEqual(state.report["sales"]["mtd"], "RM 1,500.00")
        self.assertIn("Version 2", state.selected_version_label)
        versions = self.app.list_report_versions(self.admin, self.sid, "2026-09")
        self.assertIsNotNone(versions[0]["superseded_by"])
        self.assertIsNone(versions[1]["superseded_by"])

    def test_failed_save_keeps_dialog_report_and_resets_saving(self):
        self.app.finalize_month(self.admin, self.sid, "2026-09")
        state = self.state()
        state.report_mode = "finalized"
        self.bound(lambda: list(state.refresh_report()))
        selected = state.selected_version_label
        report = state.report
        self.bound(lambda: list(state.open_correct()))
        with mock.patch.object(self.app, "correct_month",
                               side_effect=AuthorizationError("denied")):
            self.bound(lambda: list(state.save_finalization()))
        self.assertTrue(state.finalize_dialog_open)
        self.assertEqual(state.finalize_error, "You do not have access to this store.")
        self.assertEqual(state.report, report)
        self.assertEqual(state.selected_version_label, selected)
        self.assertFalse(state.finalize_saving)

    def test_next_version_ignores_selection_and_uses_maximum(self):
        self.app.finalize_month(self.admin, self.sid, "2026-09")
        self.app.correct_month(self.admin, self.sid, "2026-09")
        self.app.correct_month(self.admin, self.sid, "2026-09")
        state = self.state()
        state.report_mode = "finalized"
        self.bound(lambda: list(state.refresh_report()))
        rows = self.app.list_report_versions(self.admin, self.sid, "2026-09")
        state.selected_version_label = _version_label(rows[0])
        self.bound(lambda: list(state.open_correct()))
        self.assertEqual(state.finalize_next_version, 4)
        self.bound(lambda: list(state.save_finalization()))
        self.assertIn("Version 4", state.selected_version_label)

    def test_failed_preflight_keeps_dialog_open_with_error(self):
        state = self.state()
        with mock.patch.object(self.app, "preview_month",
                               side_effect=ConfigNotFoundError("not configured")):
            self.bound(lambda: list(state.open_finalize()))
        self.assertTrue(state.finalize_dialog_open)
        self.assertIn("not configured", state.finalize_error)
        self.assertFalse(state.finalize_saving)

    def test_finalized_report_does_not_supply_live_preflight(self):
        self.app.finalize_month(self.admin, self.sid, "2026-09")
        state = self.state()
        state.report_mode = "finalized"
        with mock.patch.object(self.app, "preview_month",
                               side_effect=ConfigNotFoundError("not configured")):
            self.bound(lambda: list(state.refresh_report()))
        self.assertEqual(state.report_status, "ok")
        self.assertFalse(state.live_preflight_ok)
        self.assertEqual(state.report["mode"], {"label": "finalized"})

    def test_version_label_uses_active_head_not_highest_number(self):
        old = {"version": 9, "created_at": "2026-09-30T12:00:00+00:00",
               "superseded_by": 1}
        current = {"version": 2, "created_at": "2026-10-01T12:00:00+00:00",
                   "superseded_by": None}
        self.assertIn("Superseded", _version_label(old))
        self.assertIn("Current", _version_label(current))

    def test_deactivated_store_blocks_state_preflight_save(self):
        self.app.finalize_month(self.admin, self.sid, "2026-09")
        self.app.set_store_active(self.admin, self.sid, False)
        state = self.state()
        state.report_mode = "finalized"
        self.bound(lambda: list(state.refresh_report()))
        self.assertFalse(state.selected_store_active)
        self.bound(lambda: list(state.open_correct()))
        self.bound(lambda: list(state.save_finalization()))
        self.assertTrue(state.finalize_dialog_open)
        self.assertEqual(state.finalize_error, "You do not have access to this store.")
        self.assertEqual(len(self.app.list_report_versions(
            self.admin, self.sid, "2026-09")), 1)


if __name__ == "__main__":
    unittest.main()
