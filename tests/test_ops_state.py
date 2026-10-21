"""Tests for ops_state: selector logic + report serialization.

Temp DBs only; no Reflex runtime involved. Run:
    python -m unittest tests.test_ops_state -v
"""

import tempfile
import unittest
from datetime import date
from pathlib import Path

from ducklab import store_ops_db
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.ops_state import (
    _serialize_live, _serialize_snapshot, month_label,
)


def rm(v):
    from decimal import Decimal
    return int(Decimal(str(v)) * 100)


class OpsFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "ops_state.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.store_a = self.app.register_store(self.admin, "A01", "Alpha")
        self.store_b = self.app.register_store(self.admin, "B02", "Beta")
        for sid in (self.store_a, self.store_b):
            self.app.configure_month(
                self.admin, sid, "2026-09", level1_base_cents=rm(200000),
                opening_headcount_ft=9, stock_loss_target=0.015,
                salary_ratio_target=0.06818,
                salary_basic_allowance_cents=rm(10000),
                overtime_cents=rm(1000))
        self.app.record_daily(self.admin, self.store_a, date(2026, 9, 1),
                              sales_cents=rm(9681), transactions=321,
                              units=1298, stock_losses={"DMO": rm(70)})
        self.app.record_daily(self.admin, self.store_b, date(2026, 9, 1),
                              sales_cents=rm(111))

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores):
        u = self.app.create_user(self.admin, email, "T", "Passw0rd!xyz")
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)


class TestSelectors(OpsFixture):
    def test_month_label(self):
        self.assertEqual(month_label("2026-09"), "September 2026")

    def test_authorized_store_listing(self):
        v = self.user("v@ducklab.com", ["viewer"], [self.store_a])
        stores = self.app.list_my_stores(v)
        self.assertEqual([s["id"] for s in stores], [self.store_a])

    def test_unauthorized_store_rejected(self):
        v = self.user("v@ducklab.com", ["viewer"], [self.store_a])
        with self.assertRaises(AuthorizationError):
            self.app.preview_month(v, self.store_b, "2026-09")

    def test_super_admin_sees_all_stores(self):
        stores = self.app.list_my_stores(self.admin)
        self.assertEqual({s["id"] for s in stores},
                         {self.store_a, self.store_b})

    def test_user_with_no_stores(self):
        v = self.user("none@ducklab.com", ["viewer"], [])
        self.assertEqual(self.app.list_my_stores(v), [])


class TestLiveSerialization(OpsFixture):
    def test_live_report_values(self):
        rep = self.app.preview_month(self.admin, self.store_a, "2026-09",
                                     as_of=date(2026, 9, 30))
        out = _serialize_live(rep, user=self.admin)
        self.assertEqual(out["mode"], "live")
        self.assertEqual(out["sales"]["mtd"], "RM 9,681.00")
        self.assertEqual(out["sales"]["target_l1"], "RM 200,000.00")
        self.assertEqual(out["sales"]["completeness"], "1 of 30 days reported")
        self.assertFalse(out["sales"]["complete"])
        self.assertEqual(out["manpower"]["salary"], "RM 11,000.00")
        self.assertEqual(out["stock"]["total_loss"], "RM 70.00")
        self.assertEqual(out["actions"]["total"], 0)
        self.assertEqual(out["store"], "A01 — Alpha")

    def test_null_vs_zero(self):
        rep = self.app.preview_month(self.admin, self.store_a, "2026-09",
                                     as_of=date(2026, 9, 30))
        out = _serialize_live(rep, user=self.admin)
        # No last-year data configured -> dash, not 0%.
        self.assertEqual(out["sales"]["growth_year"], "—")
        # Real zero stock loss for store B renders as RM 0.00, not a dash.
        rep_b = self.app.preview_month(self.admin, self.store_b, "2026-09",
                                       as_of=date(2026, 9, 30))
        self.assertEqual(_serialize_live(rep_b, user=self.admin)["stock"]["total_loss"],
                         "RM 0.00")

    def test_isolation_of_financials(self):
        a = _serialize_live(self.app.preview_month(
            self.admin, self.store_a, "2026-09", as_of=date(2026, 9, 30)),
            user=self.admin)
        b = _serialize_live(self.app.preview_month(
            self.admin, self.store_b, "2026-09", as_of=date(2026, 9, 30)),
            user=self.admin)
        self.assertNotEqual(a["sales"]["mtd"], b["sales"]["mtd"])
        self.assertEqual(b["sales"]["mtd"], "RM 111.00")


class TestFinalizedSerialization(OpsFixture):
    def test_snapshot_roundtrip_display(self):
        self.app.finalize_month(self.admin, self.store_a, "2026-09",
                                as_of=date(2026, 9, 30))
        snap = self.app.get_finalized_report(self.admin, self.store_a,
                                             "2026-09")
        out = _serialize_snapshot(snap, user=self.admin)
        self.assertEqual(out["mode"], "finalized")
        self.assertEqual(out["sales"]["mtd"], "RM 9,681.00")
        self.assertEqual(out["stock"]["total_loss"], "RM 70.00")
        self.assertEqual(snap["version"], 1)

    def test_version_listing_and_selection(self):
        self.app.finalize_month(self.admin, self.store_a, "2026-09",
                                as_of=date(2026, 9, 30))
        self.app.record_daily(self.admin, self.store_a, date(2026, 9, 1),
                              sales_cents=rm(500))
        self.app.correct_month(self.admin, self.store_a, "2026-09",
                               as_of=date(2026, 9, 30))
        versions = self.app.list_report_versions(self.admin, self.store_a,
                                                 "2026-09")
        self.assertEqual([v["version"] for v in versions], [1, 2])
        v1 = self.app.get_finalized_report(self.admin, self.store_a,
                                           "2026-09", version=1)
        self.assertEqual(
            _serialize_snapshot(v1, user=self.admin)["sales"]["mtd"], "RM 9,681.00")
        v2 = self.app.get_finalized_report(self.admin, self.store_a, "2026-09")
        self.assertEqual(_serialize_snapshot(v2, user=self.admin)["sales"]["mtd"], "RM 500.00")

    def test_viewer_cannot_finalize_but_can_read(self):
        self.app.finalize_month(self.admin, self.store_a, "2026-09",
                                as_of=date(2026, 9, 30))
        v = self.user("v@ducklab.com", ["viewer"], [self.store_a])
        snap = self.app.get_finalized_report(v, self.store_a, "2026-09")
        self.assertEqual(snap["version"], 1)
        with self.assertRaises(AuthorizationError):
            self.app.finalize_month(v, self.store_a, "2026-09")


class TestMissingConfig(OpsFixture):
    def test_unconfigured_month_raises(self):
        from ducklab.operations_repository import ConfigNotFoundError
        with self.assertRaises(ConfigNotFoundError):
            self.app.preview_month(self.admin, self.store_a, "2026-10")


if __name__ == "__main__":
    unittest.main()
