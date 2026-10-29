"""Manpower schedule persistence, calculations and store authorization."""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_write_repository import ValidationError
from ducklab.ops_manpower_state import ManpowerState


class ScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self.tmp.name) / "schedule.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.store = self.app.register_store(self.admin, "A01", "Alpha")
        self.other_store = self.app.register_store(self.admin, "B02", "Beta")
        self.viewer = self.app.create_user(
            self.admin, "viewer@ducklab.com", "Viewer", "Passw0rd!xyz")
        self.app.assign_role(self.admin, self.viewer.id, "viewer")
        self.app.assign_store(self.admin, self.viewer.id, self.store)
        self.viewer = self.app.auth.get_user(self.viewer.id)

    def tearDown(self):
        self.tmp.cleanup()

    def test_schema_seeds_codes_but_no_staff(self):
        self.assertEqual(store_ops_db.SCHEMA_VERSION, 7)
        data = self.app.get_schedule(self.admin, self.store,
                                     date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(data["staff"], [])
        self.assertEqual(len(data["codes"]), 15)
        self.assertEqual(len({c["color"] for c in data["codes"]}), 15)
        by_code = {c["code"]: c for c in data["codes"]}
        self.assertEqual(by_code["F101"]["start_time"], "10:15")
        self.assertIsNone(by_code["OOD"]["start_time"])

    def test_staff_crud_and_store_isolation(self):
        staff_id = self.app.save_staff(self.admin, self.store, None,
            name="Alice", staff_code="A001", position="Cashier")
        self.app.save_staff(self.admin, self.other_store, None,
            name="Bob", staff_code="A001", position="Supervisor")
        self.app.save_staff(self.admin, self.store, staff_id,
            name="Alice A", staff_code="A001", position="Senior Cashier")
        rows = self.app.get_schedule(self.admin, self.store,
            date(2026, 10, 1), date(2026, 10, 31))["staff"]
        self.assertEqual([(r["name"], r["position"]) for r in rows],
                         [("Alice A", "Senior Cashier")])
        self.app.delete_staff(self.admin, self.store, staff_id)
        self.assertEqual(self.app.get_schedule(self.admin, self.store,
            date(2026, 10, 1), date(2026, 10, 31))["staff"], [])

    def test_entries_targets_and_headcount(self):
        first = self.app.save_staff(self.admin, self.store, None,
            name="Alice", staff_code="A001", position="Cashier")
        second = self.app.save_staff(self.admin, self.store, None,
            name="Bob", staff_code="A002", position="Cashier")
        monday = date(2026, 10, 5)
        self.app.set_schedule_entry(self.admin, self.store, first, monday, "ODM")
        self.app.set_schedule_entry(self.admin, self.store, second, monday, "AL")
        self.app.set_schedule_target(self.admin, self.store, monday, 2)
        data = self.app.get_schedule(self.admin, self.store, monday,
                                     date(2026, 10, 11))
        self.assertEqual(len(data["entries"]), 2)
        self.assertEqual(self.app.get_schedule_target(self.admin, self.store, monday), 2)
        state = ManpowerState()
        state.schedule_staff = [{k: str(v) for k, v in row.items()}
                                for row in data["staff"]]
        state.schedule_codes = [{k: str(v or "") for k, v in row.items()}
                                for row in data["codes"]]
        state.schedule_entries = {f"{r['staff_id']}:{r['work_date']}": r["code"]
                                  for r in data["entries"]}
        state.schedule_days = [{"iso": (monday.fromordinal(monday.toordinal() + i)).isoformat(),
                                "label": "Day"} for i in range(7)]
        state.schedule_target = "2"
        self.assertEqual(state.schedule_headcounts, [1, 0, 0, 0, 0, 0, 0])
        self.assertEqual(state.schedule_day_summaries[0]["below_target"], "1")
        self.app.set_schedule_entry(self.admin, self.store, first, monday, "")
        self.assertEqual(self.app.get_schedule(self.admin, self.store, monday,
                         date(2026, 10, 11))["entries"][0]["code"], "AL")

    def test_code_edit_delete_and_validation(self):
        self.app.save_shift_code(self.admin, self.store, "M900", code="M900",
            label="Morning Shift", start_time="09:00", end_time="19:00", color="#3b82f6")
        code = next(c for c in self.app.get_schedule(self.admin, self.store,
            date(2026, 10, 1), date(2026, 10, 2))["codes"] if c["code"] == "M900")
        self.assertEqual(code["label"], "Morning Shift")
        with self.assertRaises(ValidationError):
            self.app.save_shift_code(self.admin, self.store, None, code="BAD",
                label="Bad", start_time="09:00", end_time=None, color="#000000")
        self.app.delete_shift_code(self.admin, self.store, "M900")
        self.assertNotIn("M900", [c["code"] for c in self.app.get_schedule(
            self.admin, self.store, date(2026, 10, 1), date(2026, 10, 2))["codes"]])

    def test_viewer_can_read_but_cannot_write(self):
        data = self.app.get_schedule(self.viewer, self.store,
                                     date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual(len(data["codes"]), 15)
        with self.assertRaises(AuthorizationError):
            self.app.save_staff(self.viewer, self.store, None,
                name="Alice", staff_code="A001", position="Cashier")
        with self.assertRaises(AuthorizationError):
            self.app.set_schedule_target(self.viewer, self.store,
                                         date(2026, 10, 5), 2)

    def test_generated_view_uses_builder_order_dates_and_legend(self):
        first = self.app.save_staff(self.admin, self.store, None,
            name="Zed", staff_code="Z001", position="Cashier")
        self.app.save_staff(self.admin, self.store, None,
            name="Amy", staff_code="A001", position="Supervisor")
        data = self.app.get_schedule(self.admin, self.store,
                                     date(2026, 10, 26), date(2026, 11, 1))
        state = ManpowerState()
        state.schedule_staff = [{k: str(v) for k, v in row.items()}
                                for row in data["staff"]]
        state.schedule_codes = [{k: str(v or "") for k, v in row.items()}
                                for row in data["codes"]]
        state.schedule_days = [{"iso": "2026-10-26", "label": "Mon 26"}]
        state.schedule_entries = {f"{first}:2026-10-26": "M900"}
        state.selected_store_label = "A01 — Alpha"
        state.selected_month_label = "October 2026"
        self.assertEqual(state.schedule_generated_days[0]["short_date"], "26/Oct")
        self.assertEqual([r["number"] for r in state.schedule_generated_rows], ["1", "2"])
        self.assertEqual(state.schedule_generated_rows[0]["name"], "Amy")
        legend = {row["code"]: row["detail"] for row in state.schedule_legend}
        self.assertEqual(legend["M900"], "9:00 AM - 7:00 PM")
        self.assertEqual(legend["OOD"], "Off Day")

    def test_no_cross_store_staff_assignment(self):
        staff_id = self.app.save_staff(self.admin, self.other_store, None,
            name="Bob", staff_code="B001", position="Cashier")
        with self.assertRaises(Exception):
            self.app.set_schedule_entry(self.admin, self.store, staff_id,
                                        date(2026, 10, 5), "M900")


if __name__ == "__main__":
    unittest.main()
