"""Tests for Store Management + Monthly Setup: CRUD, uniqueness,
deactivation, user assignment, blank-vs-zero, validation, stock-target
wiring to /ops/stock status, RBAC, finalized-snapshot immutability, and
schema migration. Temp DBs only. Run: python -m pytest tests/test_ops_stores.py -v
"""

import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import (
    AuthenticationError, AuthorizationError,
)
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.operations_write_repository import (
    DuplicateRecordError, ValidationError,
)


def rm(v):
    return int(Decimal(str(v)) * 100)


class StoresFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "stores.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores):
        u = self.app.create_user(self.admin, email, "T", "Passw0rd!xyz")
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)


class TestStoreCrud(StoresFixture):
    def test_create_and_edit_store(self):
        sid = self.app.register_store(
            self.admin, "C03", "Gamma", concept="Retail", zone="East",
            zone_pic="Amin", branch_manager="Siti")
        st = self.app.read_repo.get_store(sid)
        self.assertEqual((st.code, st.concept, st.zone, st.zone_pic,
                          st.branch_manager),
                         ("C03", "Retail", "East", "Amin", "Siti"))
        self.app.write.update_store_details(
            sid, name="Gamma Two", zone="West")
        st = self.app.read_repo.get_store(sid)
        self.assertEqual((st.name, st.zone, st.code), ("Gamma Two", "West", "C03"))

    def test_duplicate_code_rejected(self):
        self.app.register_store(self.admin, "D04", "Delta")
        with self.assertRaises(DuplicateRecordError):
            self.app.register_store(self.admin, "D04", "Delta Again")

    def test_required_fields_validated(self):
        with self.assertRaises(ValidationError):
            self.app.register_store(self.admin, "", "NoCode")
        with self.assertRaises(ValidationError):
            self.app.register_store(self.admin, "E05", "")

    def test_deactivate_and_reactivate_preserves_data(self):
        sid = self.app.register_store(self.admin, "F06", "Foxtrot")
        self.app.configure_month(self.admin, sid, "2026-10",
                                 level1_base_cents=rm(100000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        self.app.write.deactivate_store(sid)
        st = self.app.read_repo.get_store(sid)
        self.assertFalse(st.active)
        cfg = self.app.get_month_config(self.admin, sid, "2026-10")
        self.assertEqual(cfg.level1_base_cents, rm(100000))
        self.app.write.activate_store(sid)
        self.assertTrue(self.app.read_repo.get_store(sid).active)


class TestAssignment(StoresFixture):
    def test_assign_and_unassign(self):
        sid = self.app.register_store(self.admin, "G07", "Golf")
        u = self.app.create_user(self.admin, "ops@x.dev", "Ops", "Passw0rd!xyz")
        self.app.assign_role(self.admin, u.id, "administrator")
        self.app.assign_store(self.admin, u.id, sid)
        self.assertIn(sid, self.app.auth.get_user(u.id).store_ids)
        self.app.revoke_store(self.admin, u.id, sid)
        self.assertNotIn(sid, self.app.auth.get_user(u.id).store_ids)

    def test_assignment_requires_super_admin(self):
        sid = self.app.register_store(self.admin, "H08", "Hotel")
        mgr = self.user("m@x.dev", ["administrator"], [sid])
        target = self.app.create_user(self.admin, "v@x.dev", "V", "Passw0rd!xyz")
        with self.assertRaises(AuthorizationError):
            self.app.assign_store(mgr, target.id, sid)

    def test_store_scoped_config_edit_blocked_for_other_store(self):
        sa = self.app.register_store(self.admin, "I09", "India")
        sb = self.app.register_store(self.admin, "J10", "Juliet")
        mgr = self.user("m2@x.dev", ["administrator"], [sa])
        self.app.configure_month(mgr, sa, "2026-10", stock_loss_target=0.01)
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(mgr, sb, "2026-10", stock_loss_target=0.01)


class TestSetup(StoresFixture):
    def setUp(self):
        super().setUp()
        self.sid = self.app.register_store(self.admin, "K11", "Kilo")

    def test_save_and_reload_setup(self):
        self.app.configure_month(
            self.admin, self.sid, "2026-10",
            min_rtm_target_cents=rm(180000), level1_base_cents=rm(200000),
            level2_pct=0.05, level3_pct=0.1, level4_pct=0.15,
            sales_last_year_cents=rm(190000),
            sales_last_month_cents=rm(195000),
            salary_ratio_target=0.18, budget_headcount=8,
            turnover_threshold=0.1, opening_headcount_ft=5,
            part_timer_factor=0.5, stock_loss_target=0.015,
            salary_basic_allowance_cents=rm(25000), overtime_cents=rm(1200),
            support_team_cost_cents=rm(3000))
        cfg = self.app.get_month_config(self.admin, self.sid, "2026-10")
        self.assertEqual(cfg.min_rtm_target_cents, rm(180000))
        self.assertEqual(cfg.budget_headcount, 8)
        self.assertEqual(cfg.support_team_cost_cents, rm(3000))

    def test_blank_vs_explicit_zero(self):
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(100000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 overtime_cents=0)
        cfg = self.app.get_month_config(self.admin, self.sid, "2026-10")
        self.assertEqual(cfg.overtime_cents, 0)            # explicit zero
        self.assertIsNone(cfg.min_rtm_target_cents)        # untouched blank

    def test_negative_and_out_of_range_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.configure_month(self.admin, self.sid, "2026-10",
                                     stock_loss_target=-0.01)
        with self.assertRaises(ValidationError):
            self.app.configure_month(self.admin, self.sid, "2026-10",
                                     stock_loss_target=11.0)
        with self.assertRaises(ValidationError):
            self.app.configure_month(self.admin, self.sid, "2026-10",
                                     unknown_field=1)

    def test_stock_target_flows_to_stock_status(self):
        from ducklab import kpi
        self.app.configure_month(
            self.admin, self.sid, "2026-10",
            level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3, stock_loss_target=0.015,
            part_timer_factor=0.5)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              stock_losses={"DMO": rm(50)},
                              sales_cents=rm(10000))
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 31))
        self.assertEqual(rep.stock.status, "Within target")
        # without target -> status carries no target verdict
        sid2 = self.app.register_store(self.admin, "L12", "Lima")
        self.app.configure_month(self.admin, sid2, "2026-10",
                                 level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        self.app.record_daily(self.admin, sid2, date(2026, 10, 1),
                              sales_cents=rm(10000))
        rep2 = self.app.preview_month(self.admin, sid2, "2026-10",
                                      as_of=date(2026, 10, 31))
        self.assertNotEqual(rep2.stock.status, "Within target")
        self.assertEqual(kpi.stock_loss_status(0, rm(10000), None), "")

    def test_multi_store_isolation(self):
        sb = self.app.register_store(self.admin, "M13", "Mike")
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 stock_loss_target=0.01)
        with self.assertRaises(ConfigNotFoundError):
            self.app.get_month_config(self.admin, sb, "2026-10")

    def test_viewer_write_rejected(self):
        viewer = self.user("vw@x.dev", ["viewer"], [self.sid])
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(viewer, self.sid, "2026-10",
                                     stock_loss_target=0.01)

    def test_disabled_user_and_expired_session_rejected(self):
        # disabled user: login itself fails; ops via stale identity fail authz
        u = self.user("d@x.dev", ["administrator"], [self.sid])
        self.app.set_user_active(self.admin, u.id, False)
        with self.assertRaises(AuthenticationError):
            self.app.auth.login("d@x.dev", "Passw0rd!xyz")
        disabled = self.app.auth.get_user(u.id)
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(disabled, self.sid, "2026-10",
                                     budget_headcount=3)
        # expired session
        self.user("e@x.dev", ["administrator"], [self.sid])
        token, _ = self.app.auth.login("e@x.dev", "Passw0rd!xyz")
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE auth_sessions SET expires_at = '2000-01-01 00:00:00' "
                     "WHERE token_hash = (SELECT token_hash FROM auth_sessions "
                     "ORDER BY id DESC LIMIT 1)")
        conn.commit(); conn.close()
        with self.assertRaises(AuthenticationError):
            self.app.authenticate(token)

    def test_finalized_snapshot_unchanged_after_setup_edit(self):
        self.app.configure_month(
            self.admin, self.sid, "2026-10",
            level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3, stock_loss_target=0.015)
        self.app.finalize_month(self.admin, self.sid, "2026-10",
                                as_of=date(2026, 10, 31))
        before = self.app.get_finalized_report(
            self.admin, self.sid, "2026-10")["kpi_json"]
        result = self.app.configure_month(
            self.admin, self.sid, "2026-10", stock_loss_target=0.02)
        self.assertTrue(result["finalized_snapshot_exists"])
        again = self.app.get_finalized_report(self.admin, self.sid, "2026-10")
        self.assertEqual(again["kpi_json"], before)  # snapshot immutable


class TestMigration(unittest.TestCase):
    """Schema migration: Task 7 adds no new columns — every Setup field
    already exists in schema v1. Verify a v1 database opens and preserves
    data through the current migration runner."""

    def test_existing_schema_and_data_preserved(self):
        tmp = tempfile.TemporaryDirectory()
        db = Path(tmp.name) / "mig.db"
        store_ops_db.init_db(db)  # creates at current version
        app = AppService(db)
        admin = app.auth.bootstrap_first_admin("a@x.dev", "A", "Sup3rSecure!x")
        sid = app.register_store(admin, "N14", "November")
        app.configure_month(admin, sid, "2026-10",
                            stock_loss_target=0.012, budget_headcount=4)
        # re-run init (migration path) — data must survive
        store_ops_db.init_db(db)
        cfg = app.get_month_config(admin, sid, "2026-10")
        self.assertEqual(float(cfg.stock_loss_target), 0.012)
        self.assertEqual(cfg.budget_headcount, 4)
        tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
