"""Task 21 tests: exact projection precision, salary-ratio gating in the
manpower config editor, store lookup without a stale active flag, finalized
projection labels, Daily MTD from report figures, malformed socket input,
Kuala Lumpur display times, clearing budgets back to "No budget", and the
dev cleanup script. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_task21.py -v
"""

import importlib.util
import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import design as ds
from ducklab import kpi
from ducklab.app_service import AppService
from ducklab.auth_service import AuthenticationError
from ducklab.operations_repository import StoreNotFoundError
from ducklab.operations_snapshots import SnapshotNotFoundError
from ducklab.operations_write_repository import ValidationError

TODAY = date(2026, 10, 21)
ROOT = Path(__file__).resolve().parent.parent


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t21.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")

    def tearDown(self):
        self._tmp.cleanup()

    def make_viewer(self, store_ids=()):
        v = self.app.create_user(self.admin, "v@ducklab.com", "V",
                                 "ViewerPass123!")
        self.app.assign_role(self.admin, v.id, "viewer")
        for sid in store_ids:
            self.app.assign_store(self.admin, v.id, sid)
        return self.app.auth.get_user(v.id)

    def complete_september(self):
        """30/30 days, MTD 350,819.00; Level 1 RM 100,000; Level 4 floor
        RM 350,819.30 (September parity case)."""
        self.app.configure_month(
            self.admin, self.sid, "2026-09", level1_base_cents=rm(100000),
            level4_pct=0.3, min_rtm_target_cents=rm(350819.30))
        for d in range(1, 30):
            self.app.record_daily(self.admin, self.sid, date(2026, 9, d),
                                  sales_cents=rm(10000))
        self.app.record_daily(self.admin, self.sid, date(2026, 9, 30),
                              sales_cents=rm(60819))  # 29x10,000 + 60,819


# 1 ---------------------------------------------------------------------------
class ProjectionPrecisionTests(Fixture):
    def test_fractional_projection_not_truncated(self):
        # Oct: 7 of 31 days, MTD 700,002 cents -> projection
        # 700002 / 7 x 31 = 3,100,008.857142... cents.
        # Level 2 = 2,480,007 x 1.25 = 3,100,008.75 cents.
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=2480007,
            level2_pct=0.25, sales_last_month_cents=3100008)
        for d in range(1, 7):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=100000)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 7),
                              sales_cents=100002)
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=TODAY)
        s = rep.sales
        self.assertEqual(s.sales_mtd_cents, 700002)
        self.assertEqual(s.month_end_sales_cents.quantize(Decimal("0.000001")),
                         Decimal("3100008.857143"))
        self.assertEqual(s.targets["level2"], Decimal("3100008.75"))
        # int() truncation (3,100,008) would have said "Below target"
        self.assertEqual(s.status_by_level["level2"], "Achieved")
        self.assertEqual(
            s.achievement_by_level["level2"].quantize(Decimal("0.0000000001")),
            Decimal("1.0000000346"))
        # growth vs 3,100,008: exact 6/21,700,056 (truncated would be 0)
        self.assertEqual(s.growth_last_month.quantize(Decimal("1e-15")),
                         Decimal("0.000000276496982"))
        self.assertEqual(s.growth_status_last_month, "Growth")

    def test_september_level4_stays_below_target(self):
        self.complete_september()
        rep = self.app.preview_month(self.admin, self.sid, "2026-09",
                                     as_of=date(2026, 9, 30))
        self.assertEqual(rep.sales.month_end_sales_cents, Decimal(35081900))
        self.assertEqual(rep.sales.targets["level4"], Decimal(35081930))
        self.assertEqual(rep.sales.status_by_level["level4"], "Below target")
        self.assertEqual(rep.sales.status_by_level["level1"], "Achieved")


# 2 ---------------------------------------------------------------------------
class ManpowerConfigGatingTests(Fixture):
    def test_ratio_target_only_for_config_managers(self):
        from ducklab.ops_manpower_state import _config_form_values
        self.app.configure_month(
            self.admin, self.sid, "2026-10", opening_headcount_ft=9,
            budget_headcount=10, part_timer_factor=0.5,
            turnover_threshold=0.05, salary_ratio_target=0.06818,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000),
            support_team_cost_cents=rm(2000))
        cfg = self.app.get_month_config(self.admin, self.sid, "2026-10")
        self.assertEqual(_config_form_values(cfg, can_payroll=False), {
            "c_opening": "9", "c_budget": "10", "c_pt_factor": "0.5",
            "c_turnover": "5", "c_ratio_target": "", "p_salary": "",
            "p_overtime": "", "p_support": ""})
        self.assertEqual(_config_form_values(cfg, can_payroll=True), {
            "c_opening": "9", "c_budget": "10", "c_pt_factor": "0.5",
            "c_turnover": "5", "c_ratio_target": "6.818",
            "p_salary": "10000.00", "p_overtime": "1000.00",
            "p_support": "2000.00"})


# 3 ---------------------------------------------------------------------------
class StoreLookupTests(Fixture):
    def test_app_service_get_store_missing(self):
        with self.assertRaises(StoreNotFoundError) as ctx:
            self.app.get_store(self.admin, 999)
        self.assertEqual(str(ctx.exception), "Unknown store id: 999")

    def test_failed_lookup_clears_active_flag(self):
        from ducklab.ops_state import _refresh_store_active
        stores = [{"id": 999, "label": "Z99 — Gone", "active": True}]
        active, err = _refresh_store_active(self.app, self.admin, 999,
                                            stores, "Z99 — Gone")
        self.assertFalse(active)
        self.assertEqual(err, "This store no longer exists.")
        self.assertFalse(stores[0]["active"])

    def test_unassigned_store_clears_active_flag(self):
        from ducklab.ops_state import _refresh_store_active
        other = self.app.register_store(self.admin, "B02", "Beta")
        viewer = self.make_viewer(store_ids=[self.sid])
        stores = [{"id": other, "label": "B02 — Beta", "active": True}]
        active, err = _refresh_store_active(self.app, viewer, other,
                                            stores, "B02 — Beta")
        self.assertFalse(active)
        self.assertEqual(err, "You do not have access to this store.")
        self.assertFalse(stores[0]["active"])

    def test_deactivated_store_read_back(self):
        from ducklab.ops_state import _refresh_store_active
        stores = [{"id": self.sid, "label": "A01 — Alpha", "active": True}]
        self.assertEqual(_refresh_store_active(
            self.app, self.admin, self.sid, stores, "A01 — Alpha"),
            (True, ""))
        self.app.set_store_active(self.admin, self.sid, False)
        self.assertEqual(_refresh_store_active(
            self.app, self.admin, self.sid, stores, "A01 — Alpha"),
            (False, ""))
        self.assertFalse(stores[0]["active"])

    def test_authentication_error_propagates(self):
        from ducklab.ops_state import _refresh_store_active

        class Expired:
            def get_store(self, user, store_id):
                raise AuthenticationError("Session expired")

        with self.assertRaises(AuthenticationError) as ctx:
            _refresh_store_active(Expired(), self.admin, self.sid, [], "")
        self.assertEqual(str(ctx.exception), "Session expired")


# 4 ---------------------------------------------------------------------------
class FinalizedLabelTests(Fixture):
    def test_complete_month_is_month_end_not_projected(self):
        from ducklab.ops_state import _serialize_snapshot
        self.complete_september()
        self.app.finalize_month(self.admin, self.sid, "2026-09",
                                as_of=date(2026, 9, 30))
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-09")
        sales = _serialize_snapshot(snap, user=self.admin)["sales"]
        self.assertEqual(sales["projection_state"], "actual")
        self.assertEqual(sales["status_l1"], "Achieved")
        self.assertEqual(sales["early_estimate_tag"], "")
        self.assertEqual(sales["growth_year_status"], "No reference")

    def test_partial_month_gets_projected_label(self):
        from ducklab.ops_state import _serialize_snapshot
        # June: 10 of 30 days x RM 10,000 -> projected RM 300,000 vs
        # Level 1 RM 400,000.
        self.app.configure_month(self.admin, self.sid, "2026-06",
                                 level1_base_cents=rm(400000))
        for d in range(1, 11):
            self.app.record_daily(self.admin, self.sid, date(2026, 6, d),
                                  sales_cents=rm(10000))
        self.app.finalize_month(self.admin, self.sid, "2026-06",
                                as_of=date(2026, 6, 30))
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-06")
        sales = _serialize_snapshot(snap, user=self.admin)["sales"]
        self.assertEqual(sales["projection_state"], "projected")
        self.assertEqual(sales["status_l1"], "Projected: Below target")
        self.assertEqual(sales["projected"], "RM 300,000")
        self.assertEqual(sales["completeness"], "10 of 30 days reported")


# 5 ---------------------------------------------------------------------------
class DailyMtdTests(Fixture):
    def _days(self):
        return [kpi.DailyInput(date(2026, 10, 1), sales_cents=10001),
                kpi.DailyInput(date(2026, 10, 2), sales_cents=10002),
                kpi.DailyInput(date(2026, 10, 3), sales_cents=10002),
                kpi.DailyInput(date(2026, 10, 4), ft_working=5)]

    def test_summary_without_report(self):
        from ducklab.ops_daily_state import _sales_summary
        self.assertEqual(_sales_summary(self._days()), {
            "mtd": "RM 300.05", "avg_daily": "RM 100",
            "reported_days": "3 sales days reported"})

    def test_summary_from_report(self):
        from ducklab.ops_daily_state import _sales_summary
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(1000))
        for d in self._days():
            self.app.record_daily(self.admin, self.sid, d.date,
                                  sales_cents=d.sales_cents,
                                  ft_working=d.ft_working)
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=TODAY)
        days = self.app.get_daily_records(self.admin, self.sid, "2026-10")
        self.assertEqual(_sales_summary(days, rep.sales), {
            "mtd": "RM 300.05", "avg_daily": "RM 100",
            "reported_days": "3 sales days reported"})

    def test_no_sales_days_shows_dash(self):
        from ducklab.ops_daily_state import _sales_summary
        days = [kpi.DailyInput(date(2026, 10, 4), ft_working=5)]
        self.assertEqual(_sales_summary(days), {
            "mtd": "—", "avg_daily": "—",
            "reported_days": "0 sales days reported"})


# 6 ---------------------------------------------------------------------------
class MalformedInputTests(unittest.TestCase):
    def test_bad_action_dates(self):
        from ducklab.ops_actions_state import _pdate
        for bad in ("2026-13-01", "abc", "21/10/2026"):
            with self.assertRaises(ValidationError) as ctx:
                _pdate(bad, "Due date")
            self.assertEqual(str(ctx.exception),
                             "Due date: enter a date as YYYY-MM-DD")
        self.assertIsNone(_pdate("  ", "Due date"))
        self.assertEqual(_pdate("2026-10-05", "Due date"), date(2026, 10, 5))

    def test_bad_editing_id(self):
        from ducklab.ops_actions_state import _editing_id
        with self.assertRaises(ValidationError) as ctx:
            _editing_id("abc")
        self.assertEqual(str(ctx.exception),
                         "Unknown action — reopen it from the list.")
        self.assertIsNone(_editing_id(""))
        self.assertEqual(_editing_id("12"), 12)

    def test_bad_version_label(self):
        from ducklab.ops_state import _version_for_label
        rows = [{"version": 1, "created_at": "2026-10-21 06:05:00"},
                {"version": 2, "created_at": "2026-10-21 17:30:00"}]
        self.assertEqual(
            _version_for_label("Version 2 — 22 Oct 2026, 01:30 MYT", rows), 2)
        for bad in ("Version x — y", "garbage",
                    "Version 3 — 21 Oct 2026, 14:05 MYT"):
            with self.assertRaises(SnapshotNotFoundError) as ctx:
                _version_for_label(bad, rows)
            self.assertEqual(
                str(ctx.exception),
                "Unknown report version — choose one from the list.")


# 7 ---------------------------------------------------------------------------
class KualaLumpurTimeTests(unittest.TestCase):
    def test_fmt_kl_datetime(self):
        self.assertEqual(ds.fmt_kl_datetime("2026-10-21 06:05:00"),
                         "21 Oct 2026, 14:05 MYT")
        self.assertEqual(ds.fmt_kl_datetime("2026-10-21 17:30:00"),
                         "22 Oct 2026, 01:30 MYT")   # crosses midnight
        self.assertEqual(ds.fmt_kl_datetime("2026-10-01 00:00:00"),
                         "1 Oct 2026, 08:00 MYT")
        self.assertEqual(ds.fmt_kl_datetime(None), "—")
        self.assertEqual(ds.fmt_kl_datetime("garbage"), "garbage")

    def test_version_label(self):
        from ducklab.ops_state import _version_label
        self.assertEqual(
            _version_label({"version": 3, "created_at": "2026-10-21 06:05:00"}),
            "Version 3 — 21 Oct 2026, 14:05 MYT")


# 8 ---------------------------------------------------------------------------
class ClearBudgetTests(Fixture):
    def setUp(self):
        super().setUp()
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(1000))
        self.cat = self.app.add_expense_category(self.admin, self.sid,
                                                 "Elec", sort_order=1)

    def _status(self):
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=TODAY)
        return (rep.expenses.categories[0].budget_status,
                rep.expenses.budget_control_status,
                rep.expenses.total_budget_cents)

    def test_parse_budget_rows(self):
        from ducklab.ops_stores_state import _parse_budget_rows
        rows = [
            {"id": "7", "name": "Elec", "value": "500.00",
             "original": "500.00", "clear": "1"},
            {"id": "8", "name": "Water", "value": "", "original": "",
             "clear": ""},
            {"id": "9", "name": "Gas", "value": "120.00",
             "original": "120.00", "clear": ""},
        ]
        self.assertEqual(_parse_budget_rows(rows), ({7: None}, []))
        # nothing ticked, nothing changed -> nothing sent
        rows[0]["clear"] = ""
        self.assertEqual(_parse_budget_rows(rows), ({}, []))
        # blanking a saved budget without ticking clear is rejected
        rows[0]["value"] = ""
        self.assertEqual(_parse_budget_rows(rows), ({}, [
            "Budget for Elec: enter a budget amount, or tick "
            "“Clear saved value” to remove it (No budget)."]))

    def test_budget_save_value(self):
        from ducklab.ops_expenses_state import _budget_save_value
        self.assertIsNone(_budget_save_value("", True))
        with self.assertRaises(ValueError) as clash:
            _budget_save_value("3200.00", True)
        self.assertEqual(str(clash.exception),
                         "Untick 'Clear saved value' to set an amount")
        self.assertEqual(_budget_save_value("3200.00", False), 320000)
        with self.assertRaises(ValueError) as ctx:
            _budget_save_value("  ", False)
        self.assertEqual(
            str(ctx.exception),
            "Enter a budget amount, or tick “Clear saved value” to remove "
            "it. A missing budget is distinct from RM 0.")

    def test_monthly_setup_clear_reads_back_none(self):
        from ducklab.ops_stores_state import _parse_budget_rows
        self.app.configure_month_with_budgets(
            self.admin, self.sid, "2026-10", {}, {self.cat: rm(50000)})
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, "2026-10"),
            {self.cat: 5000000})
        self.assertEqual(self._status(),
                         ("Within budget", "Within budget", 5000000))
        rows = [{"id": str(self.cat), "name": "Elec", "value": "50000.00",
                 "original": "50000.00", "clear": "1"}]
        updates, errors = _parse_budget_rows(rows)
        self.assertEqual((updates, errors), ({self.cat: None}, []))
        self.app.configure_month_with_budgets(
            self.admin, self.sid, "2026-10", {}, updates)
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, "2026-10"),
            {self.cat: None})
        self.assertEqual(self._status(), ("No budget", "No budget", None))

    def test_expenses_page_clear_reads_back_none(self):
        from ducklab.ops_expenses_state import _budget_save_value
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat, _budget_save_value("50000",
                                                                 False))
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, "2026-10"),
            {self.cat: 5000000})
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat, _budget_save_value("", True))
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, "2026-10"),
            {self.cat: None})
        self.assertEqual(self._status(), ("No budget", "No budget", None))


# dev cleanup -------------------------------------------------------------------
def _load_cleanup():
    spec = importlib.util.spec_from_file_location(
        "dev_cleanup", ROOT / "scripts" / "dev_cleanup.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class DevCleanupTests(Fixture):
    def setUp(self):
        super().setUp()
        self.mod = _load_cleanup()
        app, admin = self.app, self.admin
        self.qa = app.register_store(admin, "T7AB12", "Task Seven QA")
        self.dbg = app.register_store(admin, "D1CD34", "Debug Store")
        for sid in (self.sid, self.qa):
            app.configure_month(admin, sid, "2026-09",
                                level1_base_cents=rm(1000))
            app.record_daily(admin, sid, date(2026, 9, 1),
                             sales_cents=rm(100),
                             stock_losses={"DMO": rm(5)})
        cat = app.add_expense_category(admin, self.qa, "Elec", sort_order=1)
        app.set_expense_budget(admin, self.qa, "2026-09", cat, rm(10))
        app.record_expense(admin, self.qa, description="qa expense")
        app.raise_action(admin, self.qa, date(2026, 9, 2), "General Operations",
                         "qa issue", "High")
        app.finalize_month(admin, self.qa, "2026-09", as_of=date(2026, 9, 30))
        app.correct_month(admin, self.qa, "2026-09", as_of=date(2026, 9, 30))
        qa_user = app.create_user(admin, "smoke-a1b2c3@ducklab.dev", "Smoke",
                                  "SmokePass123!")
        app.assign_role(admin, qa_user.id, "viewer")
        app.assign_store(admin, qa_user.id, self.dbg)
        test_viewer = app.create_user(admin, "viewer@ducklab.com",
                                      "Test Viewer", "ViewerPass123!")
        app.assign_role(admin, test_viewer.id, "viewer")
        self.keeper = self.make_viewer(store_ids=[self.sid, self.qa])

    def _count(self, sql, *args):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql, args).fetchone()[0]
        finally:
            conn.close()

    def test_plan_lists_and_counts(self):
        conn = sqlite3.connect(self.db)
        try:
            plan = self.mod.plan_cleanup(conn)
        finally:
            conn.close()
        self.assertEqual([s["code"] for s in plan["remove_stores"]],
                         ["T7AB12", "D1CD34"])
        self.assertEqual([s["code"] for s in plan["keep_stores"]], ["A01"])
        self.assertEqual([u["email"] for u in plan["remove_users"]],
                         ["smoke-a1b2c3@ducklab.dev", "viewer@ducklab.com"])
        self.assertEqual([u["email"] for u in plan["keep_users"]],
                         ["admin@ducklab.com", "v@ducklab.com"])
        self.assertEqual(plan["store_counts"], {
            "daily_stock_losses": 1, "daily_records": 1,
            "monthly_expense_budgets": 1, "expense_transactions": 1,
            "expense_categories": 1, "monthly_configs": 1, "actions": 1,
            "monthly_snapshots": 2, "auth_user_store_assignments": 2})
        self.assertEqual(plan["user_counts"]["auth_user_roles"], 2)
        self.assertEqual(plan["user_counts"]["auth_user_store_assignments"], 1)
        # dry run (plan only) deletes nothing
        self.assertEqual(self._count("SELECT COUNT(*) FROM stores"), 3)
        self.assertEqual(self._count("SELECT COUNT(*) FROM auth_users"), 4)

    def test_apply_removes_exactly_the_plan(self):
        conn = sqlite3.connect(self.db)
        try:
            self.mod.apply_cleanup(conn, self.mod.plan_cleanup(conn))
        finally:
            conn.close()
        conn = sqlite3.connect(self.db)
        try:
            self.assertEqual(
                [r[0] for r in conn.execute("SELECT code FROM stores")],
                ["A01"])
            self.assertEqual(
                [r[0] for r in conn.execute(
                    "SELECT email FROM auth_users ORDER BY id")],
                ["admin@ducklab.com", "v@ducklab.com"])
            for table in ("daily_records", "monthly_configs"):
                self.assertEqual(conn.execute(
                    f"SELECT store_id FROM {table}").fetchall(),
                    [(self.sid,)])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM daily_stock_losses").fetchone()[0], 1)
            for table in ("expense_categories", "monthly_expense_budgets",
                          "expense_transactions", "actions",
                          "monthly_snapshots"):
                self.assertEqual(conn.execute(
                    f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            self.assertEqual(conn.execute(
                "SELECT store_id FROM auth_user_store_assignments "
                "WHERE user_id = ?", (self.keeper.id,)).fetchall(),
                [(self.sid,)])
            self.assertEqual(conn.execute(
                "PRAGMA foreign_key_check").fetchall(), [])
        finally:
            conn.close()

    def test_refuses_any_other_db(self):
        with self.assertRaises(ValueError) as ctx:
            self.mod.resolve_target(self.db)
        self.assertEqual(
            str(ctx.exception),
            f"Refusing {self.db.resolve()}: dev_cleanup only targets "
            f"{self.mod.DEV_DB}")
        self.assertEqual(self.mod.main(["--db", str(self.db), "--apply"]), 2)
        self.assertEqual(self._count("SELECT COUNT(*) FROM stores"), 3)


if __name__ == "__main__":
    unittest.main()
