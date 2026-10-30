"""Regression tests for the shared live/finalized report presentation mapper."""

import copy
import tempfile
import unittest
from datetime import date
from pathlib import Path

from ducklab.app_service import AppService
from ducklab.ops_state import _serialize_live, _serialize_snapshot


class PresentationParityTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "presenter.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.viewer = self.app.create_user(
            self.admin, "viewer@ducklab.com", "Viewer", "ViewerPass123!")
        self.app.assign_role(self.admin, self.viewer.id, "viewer")
        self.app.assign_store(self.admin, self.viewer.id, self.sid)
        self.viewer = self.app.auth.get_user(self.viewer.id)

    def tearDown(self):
        self._tmp.cleanup()

    def configure(self, days=(), payroll=True, expenses=False, actions=False):
        config = dict(
            level1_base_cents=10_000_000, level2_pct=0.1, level3_pct=0.2,
            level4_pct=0.3, opening_headcount_ft=8, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            overtime_cents=50_000, support_team_cost_cents=20_000,
        )
        if payroll:
            config["salary_basic_allowance_cents"] = 1_000_000
        self.app.configure_month(self.admin, self.sid, "2026-10", **config)
        for day in days:
            self.app.record_daily(
                self.admin, self.sid, date(2026, 10, day),
                sales_cents=1_000_000, transactions=100, units=250,
                ft_working=7, pt_working=2,
                stock_losses={"DMO": 5_000, "EXTRA": 1_000, "DPI": 500})
        if expenses:
            category = self.app.add_expense_category(
                self.admin, self.sid, "Utilities", sort_order=1)
            self.app.set_expense_budget(
                self.admin, self.sid, "2026-10", category, 200_000)
            self.app.record_expense(
                self.admin, self.sid, incurred_date=date(2026, 10, 2),
                category_id=category, description="Electricity",
                amount_cents=100_000, status="Paid",
                paid_date=date(2026, 10, 3), paid_by="Store Account",
                receipt_ref="R-1", verification="Verified",
                recorded_by="Manager")
            self.app.record_expense(
                self.admin, self.sid, incurred_date=date(2026, 10, 4),
                category_id=category, description="Water", amount_cents=25_000,
                status="Pending", recorded_by="Manager")
        if actions:
            for day, status, due in (
                    (5, "Open", date(2026, 10, 30)),
                    (6, "Done", date(2026, 10, 15)),
                    (7, "In progress", date(2026, 10, 10))):
                self.app.save_action(
                    self.admin, self.sid, None, date(2026, 10, 21),
                    raised_date=date(2026, 10, day),
                    pillar="General Operations", issue=f"Action {day}",
                    priority="High", status=status, pic="DEV", due_date=due,
                    corrective_action=None, completion_date=None, remarks=None)

    def assert_parity(self, user=None):
        user = user or self.admin
        live = self.app.preview_month(
            self.admin, self.sid, "2026-10", as_of=date(2026, 10, 21))
        expected = _serialize_live(live, user=user)
        self.app.finalize_month(
            self.admin, self.sid, "2026-10", as_of=date(2026, 10, 21))
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-10")
        actual = _serialize_snapshot(snap, user=user)
        expected_finalized = copy.deepcopy(expected)
        expected_finalized["mode"] = "finalized"
        # This live-only legacy field remains absent from finalized payloads.
        del expected_finalized["expenses"]["budget_control_status"]
        self.assertEqual(actual, expected_finalized)
        return expected, snap

    def test_complete_month(self):
        self.configure(days=range(1, 32), payroll=False)
        live, _ = self.assert_parity()
        self.assertEqual(live["sales"]["projection_state"], "actual")
        self.assertEqual(live["sales"]["status_l1"], "Achieved")

    def test_incomplete_month_with_all_sections(self):
        self.configure(days=range(1, 11), expenses=True, actions=True)
        live, _ = self.assert_parity()
        self.assertEqual(live["sales"]["projection_state"], "projected")
        self.assertEqual(live["actions"], {
            "total": 3, "open": 2, "in_progress": 1, "blocked": 0,
            "done": 1, "overdue": 1, "completion": "33.3%"})
        self.assertEqual(
            {row["treatment"] for row in live["stock"]["codes"]},
            {"Included", "Offset", "Info Only"})
        self.assertEqual(live["expenses"]["paid"], "RM 1,000.00")

    def test_early_projection(self):
        self.configure(days=range(1, 7))
        live, _ = self.assert_parity()
        self.assertEqual(live["sales"]["projection_state"], "early_estimate")
        self.assertEqual(
            live["sales"]["early_estimate_tag"], "Early estimate: 6 of 31 days")
        self.assertTrue(live["sales"]["achievement_label"].endswith(
            "(early estimate)"))

    def test_missing_payroll_and_payroll_gating(self):
        self.configure(days=range(1, 11), payroll=False)
        live, _ = self.assert_parity()
        self.assertEqual(live["manpower"]["salary"], "—")
        live, _ = self.assert_parity(user=self.viewer)
        self.assertEqual(live["manpower"]["salary"], "Restricted")
        self.assertEqual(live["manpower"]["ratio_status"], "Restricted")

    def test_empty_expenses(self):
        self.configure(days=range(1, 11))
        live, _ = self.assert_parity()
        self.assertEqual(live["expenses"]["paid"], "—")
        self.assertEqual(live["expenses"]["paid_note"], "No payments yet")
        self.assertEqual(live["expenses"]["incurred"], "—")
        self.assertEqual(live["expenses"]["incurred_note"], "Not reported yet")
        self.assertEqual(live["expenses"]["pending"], "RM 0.00")

    def test_superseded_snapshot_keeps_its_original_presentation(self):
        self.configure(days=range(1, 11), expenses=True, actions=True)
        _, first = self.assert_parity()
        self.app.record_daily(
            self.admin, self.sid, date(2026, 10, 1), sales_cents=500_000)
        self.app.correct_month(
            self.admin, self.sid, "2026-10", as_of=date(2026, 10, 21))
        superseded = self.app.get_finalized_report(
            self.admin, self.sid, "2026-10", version=1)
        self.assertEqual(superseded["report"], first["report"])
        self.assertIsNotNone(superseded["superseded_by"])
        current = self.app.get_finalized_report(self.admin, self.sid, "2026-10")
        self.assertNotEqual(
            _serialize_snapshot(superseded, user=self.admin)["sales"]["mtd"],
            _serialize_snapshot(current, user=self.admin)["sales"]["mtd"])

    def test_legacy_snapshot_defaults_are_preserved(self):
        self.configure(days=range(1, 11))
        expected, snap = self.assert_parity()
        legacy = copy.deepcopy(snap)
        for section in ("paid_count", "incurred_count"):
            del legacy["report"]["expenses"][section]
        del legacy["report"]["sales"]["growth_status_last_year"]
        del legacy["report"]["sales"]["growth_status_last_month"]
        expected_finalized = copy.deepcopy(expected)
        expected_finalized["mode"] = "finalized"
        del expected_finalized["expenses"]["budget_control_status"]
        expected_finalized["expenses"].update({
            "paid": "RM 0.00", "paid_note": "",
            "incurred": "RM 0.00", "incurred_note": "", "share": "0.0%"})
        self.assertEqual(
            _serialize_snapshot(legacy, user=self.admin), expected_finalized)


if __name__ == "__main__":
    unittest.main()
