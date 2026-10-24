"""Tests for monthly snapshots: preview, finalization, versioning, isolation.

Temp DBs only. Run: python -m unittest tests.test_operations_snapshots -v
"""

import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import store_ops_db
from ducklab.operations_repository import OperationsRepository
from ducklab.operations_snapshots import (
    PAYLOAD_FORMAT, SnapshotError, SnapshotNotFoundError, SnapshotService,
    deserialize_report, serialize_report,
)
from ducklab.operations_write_service import OperationsWriteService


def rm(v) -> int:
    return int(Decimal(str(v)) * 100)


class SnapFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "snap_test.db"
        store_ops_db.init_db(self.db_path)
        self.snaps = SnapshotService(self.db_path)
        self.svc = OperationsWriteService(self.db_path)
        self.repo = OperationsRepository(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def seed_store(self, code="A01", base=rm(200000)):
        sid = self.svc.register_store(code, f"Store {code}")
        self.svc.configure_month(
            sid, "2026-09", level1_base_cents=base, min_rtm_target_cents=rm(200000),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            salary_ratio_target=0.06818, opening_headcount_ft=9,
            turnover_threshold=0.45, stock_loss_target=0.015,
            part_timer_factor=0.5,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000))
        cat = self.svc.add_expense_category(sid, "Electricity")
        return sid, cat

    def count_snapshots(self):
        conn = store_ops_db.connect(self.db_path)
        n = conn.execute("SELECT COUNT(*) FROM monthly_snapshots").fetchone()[0]
        conn.close()
        return n


class TestPreview(SnapFixture):
    def test_preview_writes_nothing(self):
        sid, _ = self.seed_store()
        self.svc.record_daily(sid, date(2026, 9, 1), sales_cents=rm(10000))
        report = self.snaps.preview_month(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(report.sales.sales_mtd_cents, rm(10000))
        self.assertFalse(report.is_snapshot)
        self.assertEqual(self.count_snapshots(), 0)  # no auto-finalization

    def test_preview_errors(self):
        from ducklab.operations_repository import ConfigNotFoundError, StoreNotFoundError
        sid, _ = self.seed_store()
        with self.assertRaises(StoreNotFoundError):
            self.snaps.preview_month(999, "2026-09")
        with self.assertRaises(ConfigNotFoundError):
            self.snaps.preview_month(sid, "2026-10")


class TestFinalization(SnapFixture):
    def test_finalize_v1_roundtrip(self):
        sid, cat = self.seed_store()
        self.svc.set_expense_budget(sid, "2026-09", cat, rm(4000))
        self.svc.record_daily(sid, date(2026, 9, 1), sales_cents=rm(10000),
                              transactions=321, units=1298, ft_working=7,
                              pt_working=1,
                              stock_losses={"DMO": rm(70), "EXTRA": rm(5)})
        eid = self.svc.record_expense(sid, incurred_date=date(2026, 9, 1),
                                      paid_date=date(2026, 9, 2), category_id=cat,
                                      description="bill", amount_cents=rm(3000),
                                      paid_by="HQ / Central", status="Paid",
                                      verification="Verified", recorded_by="Staff A")
        self.svc.raise_action(sid, date(2026, 9, 1), "Sales Performance",
                              "pace", "High", due_date=date(2026, 9, 5))
        result = self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(result["version"], 1)
        self.assertEqual(result["superseded_id"], None)
        # Completeness warnings surfaced (1 of 30 days) but not blocking.
        self.assertTrue(any("1 of 30" in w for w in result["warnings"]))

        snap = self.snaps.get_finalized(sid, "2026-09")
        rep = snap["report"]
        self.assertTrue(snap["finalized"])
        # Exact cents round-trip — no float drift.
        self.assertEqual(rep["sales"]["sales_mtd_cents"], rm(10000))
        self.assertEqual(rep["expenses"]["paid_cents"], rm(3000))
        self.assertEqual(rep["stock"]["total_loss_cents"], rm(65))
        self.assertEqual(rep["manpower"]["total_salary_cents"], rm(11000))
        self.assertEqual(rep["actions"]["open"], 1)
        self.assertEqual(rep["actions"]["counts"]["overdue"], 1)
        # Decimal round-trip.
        spr = rep["sales"]["spr"]
        self.assertIsInstance(spr, Decimal)
        self.assertEqual(spr, Decimal(rm(10000)) / 321)
        # NULL preserved (no last-year sales configured -> growth is null).
        self.assertIsNone(rep["sales"]["growth_last_year"])

    def test_serialization_no_float_money(self):
        sid, _ = self.seed_store()
        self.svc.record_daily(sid, date(2026, 9, 1), sales_cents=rm(100000))
        self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        raw = self.repo.get_snapshot(sid, "2026-09")["kpi_json"]
        self.assertIn('"format": "ducklab.snapshot.v1"', raw)
        self.assertIn("10000000", raw)  # integer cents, not 100000.00000001

    def test_finalize_missing_config_raises(self):
        sid = self.svc.register_store("X01", "X")
        from ducklab.operations_repository import ConfigNotFoundError
        with self.assertRaises(ConfigNotFoundError):
            self.snaps.finalize_month(sid, "2026-09")
        self.assertEqual(self.count_snapshots(), 0)

    def test_repeated_finalize_creates_new_versions(self):
        sid, _ = self.seed_store()
        r1 = self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        r2 = self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual((r1["version"], r2["version"]), (1, 2))
        self.assertEqual(r2["superseded_id"], r1["snapshot_id"])


class TestVersioning(SnapFixture):
    def test_correction_chain_v1_v2_v3(self):
        sid, _ = self.seed_store()
        self.svc.record_daily(sid, date(2026, 9, 1), sales_cents=rm(100))
        v1 = self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))

        # Operational correction AFTER finalization.
        self.svc.correct_daily(sid, date(2026, 9, 1), sales_cents=rm(200))

        # Live reflects correction; v1 does not.
        snap1 = self.snaps.get_finalized(sid, "2026-09", version=1)
        self.assertEqual(snap1["report"]["sales"]["sales_mtd_cents"], rm(100))
        live = self.snaps.preview_month(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(live.sales.sales_mtd_cents, rm(200))

        v2 = self.snaps.correct_month(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(v2["version"], 2)
        snap2 = self.snaps.get_finalized(sid, "2026-09")  # latest
        self.assertEqual(snap2["version"], 2)
        self.assertEqual(snap2["report"]["sales"]["sales_mtd_cents"], rm(200))

        self.svc.correct_daily(sid, date(2026, 9, 1), sales_cents=rm(300))
        v3 = self.snaps.correct_month(sid, "2026-09", as_of=date(2026, 9, 30))

        versions = self.snaps.list_versions(sid, "2026-09")
        self.assertEqual([v["version"] for v in versions], [1, 2, 3])
        # Chain: v1 -> v2, v2 -> v3, v3 head.
        self.assertEqual(versions[0]["superseded_by"], v2["snapshot_id"])
        self.assertEqual(versions[1]["superseded_by"], v3["snapshot_id"])
        self.assertIsNone(versions[2]["superseded_by"])
        # All versions remain accessible with original values.
        self.assertEqual(self.snaps.get_finalized(sid, "2026-09", 1)
                         ["report"]["sales"]["sales_mtd_cents"], rm(100))
        self.assertEqual(self.snaps.get_finalized(sid, "2026-09", 2)
                         ["report"]["sales"]["sales_mtd_cents"], rm(200))
        self.assertEqual(self.snaps.get_finalized(sid, "2026-09", 3)
                         ["report"]["sales"]["sales_mtd_cents"], rm(300))

    def test_correct_without_existing_fails(self):
        sid, _ = self.seed_store()
        with self.assertRaises(SnapshotNotFoundError):
            self.snaps.correct_month(sid, "2026-09")

    def test_get_missing_is_not_found_no_live_fallback(self):
        sid, _ = self.seed_store()
        with self.assertRaises(SnapshotNotFoundError):
            self.snaps.get_finalized(sid, "2026-09")
        with self.assertRaises(SnapshotNotFoundError):
            self.snaps.get_finalized(sid, "2026-09", version=7)

    def test_diff_with_live(self):
        sid, _ = self.seed_store()
        self.svc.record_daily(sid, date(2026, 9, 1), sales_cents=rm(100))
        self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        d0 = self.snaps.diff_with_live(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertFalse(d0["differs"])
        self.svc.correct_daily(sid, date(2026, 9, 1), sales_cents=rm(500))
        d1 = self.snaps.diff_with_live(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertTrue(d1["differs"])
        fields = {(d["section"], d["field"]) for d in d1["differences"]}
        self.assertIn(("sales", "sales_mtd_cents"), fields)


class TestMultiStoreIsolation(SnapFixture):
    def test_independent_version_sequences(self):
        s1, _ = self.seed_store("A01", rm(200000))
        s2, _ = self.seed_store("B02", rm(150000))
        self.svc.record_daily(s1, date(2026, 9, 1), sales_cents=rm(100))
        self.svc.record_daily(s2, date(2026, 9, 1), sales_cents=rm(999))
        v1a = self.snaps.finalize_month(s1, "2026-09", as_of=date(2026, 9, 30))
        v1b = self.snaps.finalize_month(s2, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual((v1a["version"], v1b["version"]), (1, 1))
        # A corrects; B's report and version chain unchanged.
        self.svc.correct_daily(s1, date(2026, 9, 1), sales_cents=rm(200))
        v2a = self.snaps.correct_month(s1, "2026-09", as_of=date(2026, 9, 30))
        b = self.snaps.get_finalized(s2, "2026-09")
        self.assertEqual(b["version"], 1)
        self.assertEqual(b["report"]["sales"]["sales_mtd_cents"], rm(999))
        self.assertEqual(len(self.snaps.list_versions(s2, "2026-09")), 1)
        self.assertEqual(len(self.snaps.list_versions(s1, "2026-09")), 2)
        # Store A cannot reference store B's snapshot IDs.
        a_versions = self.snaps.list_versions(s1, "2026-09")
        b_ids = {v["id"] for v in self.snaps.list_versions(s2, "2026-09")}
        self.assertTrue(all(v["superseded_by"] not in b_ids
                            for v in a_versions if v["superseded_by"]))

    def test_cross_month_not_confused(self):
        sid, _ = self.seed_store()
        self.svc.configure_month(sid, "2026-10", level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                                 opening_headcount_ft=9)
        self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        self.snaps.finalize_month(sid, "2026-10", as_of=date(2026, 10, 31))
        self.assertEqual(self.snaps.get_finalized(sid, "2026-10")["version"], 1)


class TestTransactionalIntegrity(SnapFixture):
    def test_rollback_on_chain_validation_failure(self):
        sid, _ = self.seed_store()
        self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        other, _ = self.seed_store("B02")
        self.snaps.finalize_month(other, "2026-09", as_of=date(2026, 9, 30))
        # Corrupt B's chain out-of-band: B's head points at A's snapshot
        # (dangling within B's scope). Finalizing B must fail and roll back.
        conn = store_ops_db.connect(self.db_path)
        a_head = conn.execute(
            "SELECT id FROM monthly_snapshots WHERE store_id = ?", (sid,)
        ).fetchone()[0]
        b_head = conn.execute(
            "SELECT id FROM monthly_snapshots WHERE store_id = ?", (other,)
        ).fetchone()[0]
        conn.execute("UPDATE monthly_snapshots SET superseded_by = ? WHERE id = ?",
                     (a_head, b_head))
        conn.commit(); conn.close()
        before = self.count_snapshots()
        with self.assertRaises(SnapshotError):
            self.snaps.finalize_month(other, "2026-09", as_of=date(2026, 9, 30))
        # No partial write: B got no new version, chain left as it was.
        self.assertEqual(self.count_snapshots(), before)
        self.assertEqual(len(self.snaps.list_versions(other, "2026-09")), 1)
        # A's chain unaffected.
        self.assertIsNone(self.snaps.list_versions(sid, "2026-09")[0]["superseded_by"])

    def test_no_connection_leak_on_error(self):
        sid, _ = self.seed_store()
        from ducklab.operations_repository import ConfigNotFoundError
        with self.assertRaises(ConfigNotFoundError):
            self.snaps.finalize_month(sid, "2026-10")
        # Subsequent operations still work.
        self.snaps.finalize_month(sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(self.count_snapshots(), 1)


class TestSerializationRoundTrip(unittest.TestCase):
    def test_edge_values(self):
        payload = serialize_report_minimal()
        out = deserialize_report(payload)
        self.assertEqual(out["exact_cents"], 10000000)
        self.assertEqual(out["ratio"], Decimal("0.06818"))
        self.assertIsNone(out["missing"])
        self.assertEqual(out["negative_cents"], -2000)
        self.assertEqual(out["zero"], 0)


def serialize_report_minimal() -> str:
    """Direct encoder/decoder round-trip without building a full report."""
    from ducklab.operations_snapshots import _encode
    import json
    return json.dumps({"format": PAYLOAD_FORMAT, "report": _encode({
        "exact_cents": 10000000, "ratio": Decimal("0.06818"),
        "missing": None, "negative_cents": -2000, "zero": 0})}, sort_keys=True)


if __name__ == "__main__":
    unittest.main()
