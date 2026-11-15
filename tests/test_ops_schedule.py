"""Manpower schedule persistence, calculations and store authorization."""

import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_write_repository import ValidationError
from ducklab.ops_schedule_state import ScheduleState


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
        self.assertEqual(store_ops_db.SCHEMA_VERSION, 15)
        data = self.app.get_schedule(self.admin, self.store,
                                     date(2026, 10, 1), date(2026, 10, 31))
        self.assertEqual(data["staff"], [])
        self.assertEqual(len(data["codes"]), 16)
        self.assertEqual(len({c["color"] for c in data["codes"]}), 11)
        by_code = {c["code"]: c for c in data["codes"]}
        self.assertEqual(by_code["F101"]["start_time"], "10:15")
        self.assertIsNone(by_code["OOD"]["start_time"])
        self.assertEqual(by_code["RPL"]["label"], "Replacement Leave")
        self.assertEqual(by_code["RPL"]["color"], "#f5d0fe")
        for code in ("M900", "F900", "M945", "F945", "F101", "A121"):
            self.assertEqual(by_code[code]["color"], "#FFFFFF")
        state = ScheduleState()
        state.schedule_codes = [{k: str(v or "") for k, v in row.items()}
                                for row in data["codes"]]
        self.assertEqual([c["code"] for c in state.schedule_config_codes], [
            "M900", "F900", "M945", "F945", "F101", "A121", "ODM",
            "ODA", "OOD", "ORD", "AL", "RPL", "UPL"])
        self.assertNotIn("EL", state.schedule_code_values)
        self.assertNotIn("MC", state.schedule_code_values)
        self.assertIn("OPH", state.schedule_code_values)

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
        state = ScheduleState()
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

    def test_shift_code_changes_require_config_manager(self):
        def user_with_role(role):
            user = self.app.create_user(
                self.admin, f"{role}@ducklab.com", role, "Passw0rd!xyz")
            self.app.assign_role(self.admin, user.id, role)
            self.app.assign_store(self.admin, user.id, self.store)
            return self.app.auth.get_user(user.id)

        fields = dict(code="M900", label="Morning Shift", start_time="09:00",
                      end_time="19:00", color="#3b82f6")
        for role in ("branch_manager", "zone_manager"):
            user = user_with_role(role)
            with self.subTest(role=role, operation="save"):
                with self.assertRaises(AuthorizationError):
                    self.app.save_shift_code(user, self.store, "M900", **fields)
            with self.subTest(role=role, operation="delete"):
                with self.assertRaises(AuthorizationError):
                    self.app.delete_shift_code(user, self.store, "M900")

        config_manager = user_with_role("administrator")
        self.app.save_shift_code(
            config_manager, self.store, "M900", **fields)
        self.app.delete_shift_code(config_manager, self.store, "M900")

    def test_viewer_can_read_but_cannot_write(self):
        data = self.app.get_schedule(self.viewer, self.store,
                                     date(2026, 10, 1), date(2026, 10, 2))
        self.assertEqual(len(data["codes"]), 16)
        with self.assertRaises(AuthorizationError):
            self.app.save_staff(self.viewer, self.store, None,
                name="Alice", staff_code="A001", position="Cashier")
        with self.assertRaises(AuthorizationError):
            self.app.set_schedule_target(self.viewer, self.store,
                                         date(2026, 10, 5), 2)

    def test_public_holidays_overwrite_only_abm_planner_cells(self):
        holiday = date(2026, 10, 5)
        self.app.configure_month_with_budgets(
            self.admin, self.store, "2026-10", {}, {}, [holiday])
        abm = self.app.save_staff(
            self.admin, self.store, None, name="Manager", staff_code="A001",
            position="ABM", employment_type="Full-Time")
        ra = self.app.save_staff(
            self.admin, self.store, None, name="Assistant", staff_code="A002",
            position="RA", employment_type="Full-Time")
        data = self.app.get_schedule(
            self.admin, self.store, holiday, date(2026, 10, 11))
        entries = {(r["staff_id"], r["work_date"]): r for r in data["entries"]}
        self.assertEqual(data["holidays"], [holiday])
        self.assertEqual(entries[(abm, holiday.isoformat())]["code"], "OPH")
        self.assertEqual(entries[(abm, holiday.isoformat())]["assignment_source"],
                         "auto_holiday")
        self.assertNotIn((ra, holiday.isoformat()), entries)
        state = ScheduleState()
        self.app.set_schedule_entry(
            self.admin, self.store, ra, holiday, "M900")
        data = self.app.get_schedule(
            self.admin, self.store, holiday, date(2026, 10, 11))
        entries = {(r["staff_id"], r["work_date"]): r for r in data["entries"]}
        self.assertEqual(entries[(ra, holiday.isoformat())]["code"], "M900")
        state.schedule_days = [{"iso": holiday.isoformat(), "label": "Mon 5"}]
        state.schedule_public_holidays = [holiday.isoformat()]
        self.assertEqual(state.schedule_day_headers[0]["label"], "Mon 5 · PH")

        # Manual ABM assignments become OPH while the holiday remains set.
        self.app.set_schedule_entry(
            self.admin, self.store, abm, holiday, "M900")
        data = self.app.get_schedule(
            self.admin, self.store, holiday, date(2026, 10, 11))
        abm_entry = next(r for r in data["entries"] if r["staff_id"] == abm)
        self.assertEqual(abm_entry["code"], "OPH")
        self.assertEqual(abm_entry["assignment_source"], "auto_holiday")
        self.app.configure_month_with_budgets(
            self.admin, self.store, "2026-10", {}, {}, [])
        remaining = self.app.get_schedule(
            self.admin, self.store, holiday, date(2026, 10, 11))["entries"]
        self.assertEqual([(r["staff_id"], r["code"]) for r in remaining],
                         [(ra, "M900")])

    def test_abm_holiday_planner_edit_immediately_displays_oph(self):
        import ducklab.ops_schedule_state as mod

        holiday = date(2026, 10, 5)
        self.app.configure_month_with_budgets(
            self.admin, self.store, "2026-10", {}, {}, [holiday])
        abm = self.app.save_staff(
            self.admin, self.store, None, name="Manager", staff_code="A001",
            position="ABM", employment_type="Full-Time")
        state = ScheduleState()
        state.can_write_schedule = True
        state.schedule_staff = [{"id": str(abm), "position": "ABM"}]
        state.schedule_public_holidays = [holiday.isoformat()]
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store):
            state.set_schedule_cell(str(abm), holiday.isoformat(), "M900")
        self.assertEqual(state.schedule_error, "")
        self.assertEqual(state.schedule_entries[f"{abm}:{holiday}"], "OPH")

    def test_holiday_removal_clears_only_automatic_oph(self):
        first, second = date(2026, 10, 5), date(2026, 10, 12)
        self.app.configure_month_with_budgets(
            self.admin, self.store, "2026-10", {}, {}, [first, second])
        abm = self.app.save_staff(
            self.admin, self.store, None, name="Manager", staff_code="A001",
            position="ABM", employment_type="Full-Time")
        self.app.configure_month_with_budgets(
            self.admin, self.store, "2026-10", {}, {}, [second])
        data = self.app.get_schedule(
            self.admin, self.store, first, date(2026, 10, 18))
        self.assertEqual([(r["work_date"], r["code"], r["assignment_source"])
                          for r in data["entries"]],
                         [(second.isoformat(), "OPH", "auto_holiday")])

    def test_generated_manpower_excludes_abm(self):
        state = ScheduleState()
        state.schedule_staff = [
            {"position": position} for position
            in ("ABM", "SV", "ASV", "RA", "RA")]
        state.schedule_target = "4"
        self.assertEqual(state.schedule_manpower_display, "4/4")
        state.schedule_target = "12"
        self.assertEqual(state.schedule_manpower_display, "4/12")
        state.schedule_target = ""
        self.assertEqual(state.schedule_manpower_display, "4/—")

    def test_generated_view_prioritizes_positions_and_preserves_builder(self):
        state = ScheduleState()
        state.schedule_staff = [
            {"id": "1", "name": "Retail One", "staff_code": "R1",
             "position": "RA"},
            {"id": "2", "name": "Assistant Supervisor", "staff_code": "A1",
             "position": "ASV"},
            {"id": "3", "name": "Supervisor One", "staff_code": "S1",
             "position": "SV"},
            {"id": "4", "name": "Supervisor Two", "staff_code": "S2",
             "position": "SV"},
            {"id": "5", "name": "Branch Manager", "staff_code": "B1",
             "position": "ABM"},
        ]
        state.schedule_days = [{"iso": "2026-10-26", "label": "Mon 26"}]
        self.assertEqual([row["position"] for row in state.schedule_grid_rows],
                         ["RA", "ASV", "SV", "SV", "ABM"])
        self.assertEqual([row["name"] for row in state.schedule_generated_rows], [
            "Branch Manager", "Supervisor One", "Supervisor Two",
            "Assistant Supervisor", "Retail One"])

        # Missing higher-priority positions do not leave gaps or placeholders.
        state.schedule_staff = state.schedule_staff[1:4]
        self.assertEqual([row["position"] for row in state.schedule_generated_rows],
                         ["SV", "SV", "ASV"])

    def test_generated_view_uses_builder_order_dates_and_legend(self):
        first = self.app.save_staff(self.admin, self.store, None,
            name="Zed", staff_code="Z001", position="Cashier")
        self.app.save_staff(self.admin, self.store, None,
            name="Amy", staff_code="A001", position="Supervisor")
        data = self.app.get_schedule(self.admin, self.store,
                                     date(2026, 10, 26), date(2026, 11, 1))
        state = ScheduleState()
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

    def test_view_legend_toggle_persists_and_preserves_planner_legend(self):
        import ducklab.ops_schedule_state as mod

        state = ScheduleState()
        state.can_write_schedule = True
        state.schedule_codes = [{
            "code": "M900", "label": "Morning", "start_time": "09:00",
            "end_time": "19:00", "color": "#FFFFFF", "show_in_legend": "1",
        }]
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store):
            state.set_shift_code_legend("M900", False)
        self.assertEqual(state.schedule_error, "")
        self.assertEqual(state.schedule_codes[0]["show_in_legend"], "0")
        self.assertEqual(state.schedule_generated_legend, [])
        self.assertEqual([row["code"] for row in state.schedule_legend], ["M900"])
        saved = next(row for row in self.app.get_schedule(
            self.admin, self.store, date(2026, 10, 26),
            date(2026, 11, 1))["codes"] if row["code"] == "M900")
        self.assertEqual(saved["show_in_legend"], 0)

    def test_view_legend_toggle_requires_config_permission(self):
        with self.assertRaises(AuthorizationError):
            self.app.set_shift_code_legend(
                self.viewer, self.store, "M900", False)
        with self.assertRaises(AuthorizationError):
            branch = self.app.create_user(
                self.admin, "branch@ducklab.com", "Branch", "Passw0rd!xyz")
            self.app.assign_role(self.admin, branch.id, "branch_manager")
            self.app.assign_store(self.admin, branch.id, self.store)
            branch = self.app.auth.get_user(branch.id)
            self.app.set_shift_code_legend(
                branch, self.store, "M900", False)

    def test_generated_layout_and_print_rules(self):
        from ducklab import ops_schedule_pages as pages

        row = {"number": "1", "name": "A very long staff name",
               "staff_code": "A001", "position": "ABM",
               "cells": [{"code": "M900", "color": "#FFFFFF"}]}
        self.assertIn('className:"generated-name"', str(pages._generated_row(row)))
        self.assertIn('className:"generated-position"', str(pages._generated_row(row)))
        self.assertIn("Manpower: ", str(pages._generated()))
        self.assertIn("schedule_generated_legend", str(pages._generated()))
        self.assertIn('"View"', str(pages._codes_tab()))
        css = (Path(__file__).parents[1] / "assets" / "schedule_print.css").read_text()
        self.assertNotIn("@media print", css)
        self.assertIn("-webkit-line-clamp: 2", css)
        self.assertIn("gap: 3px", css)
        self.assertIn('["whiteSpace"] : "nowrap"', str(pages._generated()))
        self.assertIn("Save PDF", str(pages._generated()))

    def test_save_pdf_writes_a4_landscape_file_without_print_dialog(self):
        import ducklab.ops_schedule_state as mod
        from ducklab import platform_utils

        staff_id = self.app.save_staff(
            self.admin, self.store, None,
            name="Alexandria Cassandra Montgomery-Richardson With A Very Long Name",
            staff_code="A001", position="ABM", employment_type="Full-Time")
        self.app.save_staff(
            self.admin, self.store, None, name="Zed Supervisor",
            staff_code="S001", position="SV", employment_type="Full-Time")
        monday = date(2026, 11, 2)
        self.app.set_schedule_entry(
            self.admin, self.store, staff_id, monday, "M900")
        self.app.set_shift_code_legend(
            self.admin, self.store, "M900", False)
        export_root = Path(self.tmp.name) / "Documents"
        state = ScheduleState()
        state.schedule_week = monday.isoformat()
        state.schedule_target = "3"
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store), \
                mock.patch.object(platform_utils, "documents_dir",
                                  lambda: export_root), \
                mock.patch("ducklab.schedule_pdf.write_schedule_pdf",
                           wraps=__import__("ducklab.schedule_pdf",
                                            fromlist=["write_schedule_pdf"]
                                            ).write_schedule_pdf) as writer:
            state.save_schedule_pdf()
        self.assertEqual([row["position"] for row in writer.call_args.kwargs["staff"]],
                         ["ABM", "SV"])
        self.assertEqual(writer.call_args.kwargs["target"], "1/3")
        self.assertEqual(state.schedule_error, "")
        path = export_root / "Opscore Schedules" / "A01" / "2026-11-02" / "Schedule_A01_2026-11-02.pdf"
        self.assertTrue(path.is_file())
        self.assertEqual(state.schedule_notice, f"PDF saved: {path.resolve()}")
        pdf = path.read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 1000)
        self.assertNotIn("window.print", str(state.save_schedule_pdf))

    def test_employment_type_migration_preserves_staff_as_not_set(self):
        path = Path(self.tmp.name) / "migration.db"
        conn = store_ops_db.connect(path)
        store_ops_db._apply_v1(conn)
        conn.execute("PRAGMA user_version = 1")
        for version, apply in ((2, store_ops_db._apply_v2),
                               (3, store_ops_db._apply_v3),
                               (4, store_ops_db._apply_v4),
                               (5, store_ops_db._apply_v5),
                               (6, store_ops_db._apply_v6),
                               (7, store_ops_db._apply_v7)):
            apply(conn)
            conn.execute(f"PRAGMA user_version = {version}")
        conn.execute("INSERT INTO stores (code, name) VALUES ('A01', 'Alpha')")
        conn.execute(
            "INSERT INTO staff (store_id, name, staff_code, position) "
            "VALUES (1, 'Alice', 'A001', 'RA')")
        conn.commit()
        conn.close()
        store_ops_db.init_db(path)
        conn = store_ops_db.connect(path)
        row = conn.execute("SELECT employment_type FROM staff").fetchone()
        self.assertEqual(row["employment_type"], "Not Set")
        self.assertEqual(conn.execute("PRAGMA user_version").fetchone()[0], store_ops_db.SCHEMA_VERSION)
        self.assertIn(8, [r["version"] for r in conn.execute(
            "SELECT version FROM schema_migrations")])
        conn.close()
        store_ops_db.init_db(path)  # idempotent

    def test_employment_type_save_edit_and_validation(self):
        staff_id = self.app.save_staff(
            self.admin, self.store, None, name="Alice", staff_code="A001",
            position="Cashier", employment_type="Full-Time")
        self.app.save_staff(
            self.admin, self.store, None, name="Bob", staff_code="A002",
            position="RA", employment_type="Not Set")
        self.app.save_staff(
            self.admin, self.store, staff_id, name="Alice", staff_code="A001",
            position="Cashier", employment_type="Part-Time")
        rows = self.app.get_schedule(self.admin, self.store,
            date(2026, 10, 1), date(2026, 10, 2))["staff"]
        self.assertEqual({r["name"]: r["employment_type"] for r in rows},
                         {"Alice": "Part-Time", "Bob": "Not Set"})
        with self.assertRaises(ValidationError):
            self.app.save_staff(
                self.admin, self.store, staff_id, name="Alice", staff_code="A001",
                position="Cashier", employment_type="Contractor")

    def test_employment_type_permission_and_store_isolation(self):
        with self.assertRaises(AuthorizationError):
            self.app.save_staff(
                self.viewer, self.store, None, name="Alice", staff_code="A001",
                position="Cashier", employment_type="Full-Time")
        staff_id = self.app.save_staff(
            self.admin, self.other_store, None, name="Bob", staff_code="B001",
            position="Cashier", employment_type="Part-Time")
        with self.assertRaises(Exception):
            self.app.save_staff(
                self.admin, self.store, staff_id, name="Bob", staff_code="B001",
                position="Cashier", employment_type="Full-Time")

    def test_no_cross_store_staff_assignment(self):
        staff_id = self.app.save_staff(self.admin, self.other_store, None,
            name="Bob", staff_code="B001", position="Cashier")
        with self.assertRaises(Exception):
            self.app.set_schedule_entry(self.admin, self.store, staff_id,
                                        date(2026, 10, 5), "M900")

    def test_csv_export_reports_malformed_week(self):
        import ducklab.ops_schedule_state as mod

        state = ScheduleState()
        state.can_write_schedule = True
        state.schedule_week = "not-a-date"
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store):
            state.export_schedule_csv()

        self.assertEqual(
            state.schedule_error,
            "Invalid schedule week. Select a valid week and try again.")
        self.assertEqual(state.schedule_notice, "")

    def test_csv_export_writes_per_staff_files(self):
        """One CSV per staff member who has shifts that week, under
        Documents/Opscore Schedules/<store-code>/<week-start>/, with
        date,shift_code rows; staff with no shifts are skipped."""
        import csv
        import ducklab.ops_schedule_state as mod
        from ducklab import platform_utils

        first = self.app.save_staff(self.admin, self.store, None,
            name="Zed One", staff_code="Z001", position="RA")
        second = self.app.save_staff(self.admin, self.store, None,
            name="Amy Two", staff_code="A002", position="SV")
        empty = self.app.save_staff(self.admin, self.store, None,
            name="No Shifts", staff_code="N001", position="RA")
        week = date(2026, 11, 2)
        self.app.set_schedule_entry(self.admin, self.store, first, week, "M900")
        self.app.set_schedule_entry(self.admin, self.store, first,
                                    date(2026, 11, 4), "AL")
        self.app.set_schedule_entry(self.admin, self.store, second,
                                    date(2026, 11, 8), "ORD")

        export_root = Path(self.tmp.name) / "Documents"
        state = ScheduleState()
        state.can_write_schedule = True
        state.schedule_week = "2026-11-02"
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store), \
                mock.patch.object(platform_utils, "documents_dir",
                                  lambda: export_root):
            state.export_schedule_csv()

        self.assertEqual(state.schedule_error, "")
        folder = export_root / "Opscore Schedules" / "A01" / "2026-11-02"
        self.assertEqual(
            state.schedule_notice, f"2 files saved: {folder.resolve()}")
        zed = folder / "Z001_2026-11-02.csv"
        amy = folder / "A002_2026-11-02.csv"
        self.assertTrue(zed.is_file())
        self.assertTrue(amy.is_file())
        # Staff with no shifts that week produce no file.
        self.assertFalse((folder / "N001_2026-11-02.csv").exists())
        with zed.open(newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.reader(handle)),
                             [["date", "shift_code"],
                              ["2026-11-02", "M900"],
                              ["2026-11-04", "AL"]])
        with amy.open(newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.reader(handle)),
                             [["date", "shift_code"],
                              ["2026-11-08", "ORD"]])

    def test_csv_export_read_only_user_denied(self):
        state = ScheduleState()
        state.can_write_schedule = False
        state.schedule_week = "2026-11-02"
        spec = state.export_schedule_csv()
        self.assertIsNone(spec)
        self.assertEqual(state.schedule_error, "You have read-only access.")

    def test_myess_batch_csv_combines_staff_in_staff_and_day_order(self):
        import csv
        import ducklab.ops_schedule_state as mod
        from ducklab import platform_utils

        zed = self.app.save_staff(self.admin, self.store, None,
            name="Zed One", staff_code="Z/001, West", position="RA")
        amy = self.app.save_staff(self.admin, self.store, None,
            name="Amy Two", staff_code="A002", position="SV")
        self.app.save_staff(self.admin, self.store, None,
            name="No Shifts", staff_code="N001", position="RA")
        week = date(2026, 11, 2)
        # Insert out of order to verify that export uses staff then day order.
        self.app.set_schedule_entry(self.admin, self.store, amy,
                                    date(2026, 11, 8), "ORD")
        self.app.set_schedule_entry(self.admin, self.store, zed,
                                    date(2026, 11, 4), "AL")
        self.app.set_schedule_entry(self.admin, self.store, zed, week, "M900")
        self.app.set_schedule_entry(self.admin, self.store, amy,
                                    date(2026, 11, 3), "F101")

        export_root = Path(self.tmp.name) / "Documents"
        state = ScheduleState()
        state.can_write_schedule = True
        state.schedule_week = "2026-11-02"
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store), \
                mock.patch.object(platform_utils, "documents_dir",
                                  lambda: export_root):
            state.export_myess_batch_csv()

        self.assertEqual(state.schedule_error, "")
        folder = export_root / "Opscore Schedules" / "A01" / "2026-11-02"
        path = folder / "MyESS_A01_2026-11-02.csv"
        self.assertEqual(
            state.schedule_notice, f"MyESS batch schedule saved: {path.resolve()}")
        self.assertEqual([p.name for p in folder.iterdir()], [path.name])
        with path.open(newline="", encoding="utf-8") as handle:
            self.assertEqual(list(csv.reader(handle)), [
                ["employee_no", "date", "shift_code"],
                ["A002", "2026-11-03", "F101"],
                ["A002", "2026-11-08", "ORD"],
                ["Z/001, West", "2026-11-02", "M900"],
                ["Z/001, West", "2026-11-04", "AL"],
            ])

    def test_myess_batch_csv_no_shifts_does_not_create_file(self):
        import ducklab.ops_schedule_state as mod
        from ducklab import platform_utils

        self.app.save_staff(self.admin, self.store, None,
            name="No Shifts", staff_code="N001", position="RA")
        export_root = Path(self.tmp.name) / "Documents"
        state = ScheduleState()
        state.can_write_schedule = True
        state.schedule_week = "2026-11-02"
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store), \
                mock.patch.object(platform_utils, "documents_dir",
                                  lambda: export_root):
            state.export_myess_batch_csv()

        self.assertEqual(state.schedule_error,
                         "No scheduled shifts to export for this week.")
        self.assertEqual(state.schedule_notice, "")
        self.assertFalse(export_root.exists())

    def test_myess_batch_csv_read_only_user_denied(self):
        state = ScheduleState()
        state.can_write_schedule = False
        state.schedule_week = "2026-11-02"
        spec = state.export_myess_batch_csv()
        self.assertIsNone(spec)
        self.assertEqual(state.schedule_error, "You have read-only access.")

    def test_myess_batch_csv_reports_malformed_week(self):
        import ducklab.ops_schedule_state as mod

        state = ScheduleState()
        state.can_write_schedule = True
        state.schedule_week = "not-a-date"
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ScheduleState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ScheduleState, "_selected_store_id",
                                  lambda _: self.store):
            state.export_myess_batch_csv()

        self.assertEqual(
            state.schedule_error,
            "Invalid schedule week. Select a valid week and try again.")
        self.assertEqual(state.schedule_notice, "")


if __name__ == "__main__":
    unittest.main()
