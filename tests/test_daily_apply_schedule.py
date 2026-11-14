"""Apply Schedule preview/confirmation for the Daily Operations dialog."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.ops_daily_state import DailyState


class ApplyScheduleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self.tmp.name) / "apply-schedule.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.store = self.app.register_store(self.admin, "A01", "Alpha")
        self.other_store = self.app.register_store(self.admin, "B02", "Beta")
        self.day = date(2026, 10, 5)

    def tearDown(self):
        self.tmp.cleanup()

    def staff(self, code, employment_type):
        return self.app.save_staff(
            self.admin, self.store, None, name=code, staff_code=code,
            position="Cashier", employment_type=employment_type)

    def schedule(self, staff_id, code):
        self.app.set_schedule_entry(
            self.admin, self.store, staff_id, self.day, code)

    def preview(self, user=None, store=None):
        return self.app.get_daily_schedule_preview(
            user or self.admin, store or self.store, self.day)

    def state(self):
        import ducklab.ops_daily_state as module
        state = DailyState()
        state.selected_store_active = True
        state.can_write = True
        self.patch = mock.patch.multiple(
            module, app_service=self.app)
        self.patch.start()
        self.actor_patch = mock.patch.object(
            DailyState, "_actor", lambda _: self.admin)
        self.store_patch = mock.patch.object(
            DailyState, "_selected_store_id", lambda _: self.store)
        self.month_patch = mock.patch.object(
            DailyState, "_selected_month", lambda _: "2026-10")
        self.actor_patch.start()
        self.store_patch.start()
        self.month_patch.start()
        self.addCleanup(self.patch.stop)
        self.addCleanup(self.actor_patch.stop)
        self.addCleanup(self.store_patch.stop)
        self.addCleanup(self.month_patch.stop)
        return state

    def test_leave_mapping_ft_pt_and_nonworking_codes(self):
        assignments = [("F1", "Full-Time", "M900"),
                       ("F2", "Full-Time", "F900"),
                       ("P1", "Part-Time", "ODM"),
                       ("A1", "Full-Time", "AL"),
                       ("M1", "Part-Time", "MC"),
                       ("U1", "Full-Time", "UPL"),
                       ("P2", "Part-Time", "RPL"),
                       ("R1", "Full-Time", "ORD"),
                       ("O1", "Part-Time", "OOD"),
                       ("H1", "Full-Time", "OPH"),
                       ("E1", "Part-Time", "EL")]
        for code, kind, shift in assignments:
            self.schedule(self.staff(code, kind), shift)
        self.assertEqual(self.preview(), {
            "ft_working": 2, "pt_working": 1, "leave_annual": 1,
            "leave_other_paid": 1, "leave_unpaid": 1, "not_set": 0})

    def test_blank_day_and_custom_shifts(self):
        self.assertEqual(self.preview(), {
            "ft_working": 0, "pt_working": 0, "leave_annual": 0,
            "leave_other_paid": 0, "leave_unpaid": 0, "not_set": 0})
        for code, start, end in (("CUSTOM", "08:00", "17:00"),
                                 ("LEAVE", None, None)):
            self.app.save_shift_code(
                self.admin, self.store, None, code=code, label=code,
                start_time=start, end_time=end, color="#000000")
        self.schedule(self.staff("C1", "Full-Time"), "CUSTOM")
        self.schedule(self.staff("C2", "Part-Time"), "LEAVE")
        self.assertEqual(self.preview()["ft_working"], 1)
        self.assertEqual(self.preview()["pt_working"], 0)
        self.assertEqual(self.preview()["leave_annual"], 0)
        self.assertNotIn("leave_mc", self.preview())
        self.assertNotIn("leave_emergency", self.preview())
        self.assertEqual(self.preview()["leave_unpaid"], 0)

    def test_not_set_warning_and_exclusion(self):
        self.schedule(self.staff("N1", "Not Set"), "M900")
        self.schedule(self.staff("N2", "Not Set"), "AL")
        state = self.state()
        state.form_date = self.day.isoformat()
        state.preview_schedule()
        self.assertEqual(state.schedule_preview["not_set"], "1")
        self.assertEqual(state.schedule_preview["ft_working"], "0")
        self.assertIn("1 scheduled staff", state.schedule_preview_warning)

    def test_confirm_replaces_only_manpower_and_leave_and_can_reapply(self):
        self.app.record_daily(
            self.admin, self.store, self.day, sales_cents=12345,
            transactions=8, units=20, ft_working=9, pt_working=9,
            new_hires_ft=4, resigned_ft=3, leave_other_paid=2,
            stock_losses={"DMO": 500})
        self.schedule(self.staff("F1", "Full-Time"), "M900")
        self.schedule(self.staff("P1", "Part-Time"), "M900")
        self.schedule(self.staff("A1", "Full-Time"), "AL")
        state = self.state()
        state.open_entry(self.day.isoformat())
        state.preview_schedule()
        # Preview alone must not change any form values.
        self.assertEqual(state.f_ft_working, "9")
        state.apply_schedule_preview()
        self.assertEqual((state.f_ft_working, state.f_pt_working), ("1", "1"))
        self.assertEqual((state.f_leave_annual, state.f_leave_other,
                          state.f_leave_unpaid), ("1", "0", "0"))
        self.assertEqual((state.f_sales, state.f_transactions, state.f_units,
                          state.f_new_hires, state.f_resigned,
                          state.f_leave_emergency, state.stock_form["DMO"]),
                         ("123.45", "8", "20", "4", "3", "", "5.00"))
        self.assertFalse(state.schedule_preview_visible)
        # Still editable and the normal record_daily save path is unchanged.
        state.f_ft_working = "2"
        list(state.save_entry())
        rec = self.app.get_daily_records(
            self.admin, self.store, "2026-10")[0]
        self.assertEqual((rec.ft_working, rec.pt_working), (2, 1))
        self.assertEqual((rec.sales_cents, rec.transactions, rec.units,
                          rec.new_hires_ft, rec.resigned_ft,
                          rec.leave_other_paid, rec.stock_losses),
                         (12345, 8, 20, 4, 3, 0, {"DMO": 500}))
        # Reapplying updates planned values but never overwrites actual MC/EL.
        state.open_entry(self.day.isoformat())
        annual_staff = next(
            row for row in self.app.get_schedule(
                self.admin, self.store, self.day, self.day)["staff"]
            if row["staff_code"] == "A1")
        self.app.set_schedule_entry(
            self.admin, self.store, annual_staff["id"], self.day, "")
        self.schedule(annual_staff["id"], "RPL")
        state.f_leave_mc = "1"
        state.f_leave_emergency = "1"
        state.preview_schedule()
        state.apply_schedule_preview()
        self.assertEqual((state.f_leave_annual, state.f_leave_other), ("0", "1"))
        self.assertEqual((state.f_leave_mc, state.f_leave_emergency), ("1", "1"))

    def test_new_entry_auto_fills_once_and_saved_actuals_stay_unchanged(self):
        self.schedule(self.staff("F1", "Full-Time"), "M900")
        self.schedule(self.staff("P1", "Part-Time"), "F900")
        self.schedule(self.staff("A1", "Full-Time"), "AL")
        self.schedule(self.staff("R1", "Full-Time"), "RPL")
        self.schedule(self.staff("U1", "Full-Time"), "UPL")
        self.schedule(self.staff("M1", "Full-Time"), "MC")
        state = self.state()
        state.open_entry(self.day.isoformat())
        self.assertEqual((state.f_ft_working, state.f_pt_working), ("1", "1"))
        self.assertEqual((state.f_leave_annual, state.f_leave_other,
                          state.f_leave_unpaid), ("1", "1", "1"))
        self.assertEqual((state.f_leave_mc, state.f_leave_emergency), ("", ""))
        state.f_ft_working = "0"
        state.f_leave_mc = "1"
        state.f_leave_emergency = "1"
        list(state.save_entry())
        state.open_entry(self.day.isoformat())
        self.assertEqual((state.f_ft_working, state.f_pt_working), ("0", "1"))
        self.assertEqual((state.f_leave_mc, state.f_leave_emergency), ("1", "1"))
        rec = self.app.get_daily_records(
            self.admin, self.store, "2026-10")[0]
        self.assertEqual(rec.leave_emergency, 1)

    def test_preview_permissions_and_store_or_month_changes(self):
        viewer = self.app.create_user(
            self.admin, "viewer@ducklab.com", "Viewer", "Passw0rd!xyz")
        self.app.assign_role(self.admin, viewer.id, "viewer")
        self.app.assign_store(self.admin, viewer.id, self.store)
        viewer = self.app.auth.get_user(viewer.id)
        self.assertEqual(self.preview(viewer)["ft_working"], 0)
        with self.assertRaises(AuthorizationError):
            self.preview(viewer, self.other_store)

        state = self.state()
        state.form_date = self.day.isoformat()
        state.preview_schedule()
        self.assertTrue(state.schedule_preview_visible)
        state.selected_store_label = "Alpha"
        state.selected_month_label = "October 2026"
        list(state.select_store("Beta"))
        self.assertFalse(state.schedule_preview_visible)
        state.form_date = self.day.isoformat()
        state.schedule_preview = {"ft_working": "1"}
        state.schedule_preview_date = state.form_date
        list(state.select_month("September 2026"))
        self.assertFalse(state.schedule_preview_visible)


if __name__ == "__main__":
    unittest.main()
