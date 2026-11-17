"""Tests for write operations, transactions, and read-after-write integration.

Temp DBs only. Run: python -m unittest tests.test_operations_write -v
"""

import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import store_ops_db
from ducklab.operations_repository import (
    InvalidMonthError, OperationsRepository, StoreNotFoundError,
)
from ducklab.operations_service import assemble_month
from ducklab.operations_write_repository import (
    DuplicateRecordError, OperationsWriteRepository, RecordNotFoundError,
    ValidationError, WriteError,
)
from ducklab.operations_write_service import OperationsWriteService


def rm(v) -> int:
    return int(Decimal(str(v)) * 100)


class WriteFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "write_test.db"
        store_ops_db.init_db(self.db_path)
        self.w = OperationsWriteRepository(self.db_path)
        self.svc = OperationsWriteService(self.db_path)
        self.r = OperationsRepository(self.db_path)

    def tearDown(self):
        self._tmp.cleanup()

    def seeded_category(self, store_id, name):
        """The default category seeded by create_store(), by name."""
        return next(c.id for c in self.r.get_expense_categories(store_id)
                    if c.name == name)


class TestStoreWrites(WriteFixture):
    def test_create_and_update(self):
        sid = self.svc.register_store("S01", "Store A", concept="Mall", zone="Z01")
        s = self.r.get_store(sid)
        self.assertEqual((s.code, s.name, s.active), ("S01", "Store A", True))
        self.svc.update_store_details(sid, zone="KV5", branch_manager="NEW BM")
        s = self.r.get_store(sid)
        self.assertEqual((s.zone, s.branch_manager, s.concept),
                         ("KV5", "NEW BM", "Mall"))  # partial update preserved concept

    def test_new_store_seeds_default_categories(self):
        from ducklab.expense_category_defaults import DEFAULT_EXPENSE_CATEGORIES
        sid = self.svc.register_store("S01", "Store A")
        cats = self.r.get_expense_categories(sid)
        self.assertEqual([c.name for c in cats],
                         [name for name, _pct in DEFAULT_EXPENSE_CATEGORIES])
        self.assertEqual([c.sort_order for c in cats], list(range(10)))

    def test_create_store_is_atomic(self):
        # If category seeding fails, the store row is rolled back too.
        # Patch the by-value import inside the repository module itself.
        from ducklab import operations_write_repository as repo_mod
        original = repo_mod.DEFAULT_EXPENSE_CATEGORIES
        try:
            repo_mod.DEFAULT_EXPENSE_CATEGORIES = (None,)  # invalid: name NOT NULL
            with self.assertRaises(Exception):
                self.svc.register_store("S01", "Store A")
        finally:
            repo_mod.DEFAULT_EXPENSE_CATEGORIES = original
        # No store, no categories.
        with self.assertRaises(StoreNotFoundError):
            self.r.get_store_by_code("S01")
        conn = store_ops_db.connect(self.db_path)
        n = conn.execute("SELECT COUNT(*) FROM expense_categories").fetchone()[0]
        conn.close()
        self.assertEqual(n, 0)

    def test_duplicate_code_rejected(self):
        self.svc.register_store("S01", "Store A")
        with self.assertRaises(DuplicateRecordError):
            self.svc.register_store("S01", "Clone")

    def test_required_fields(self):
        with self.assertRaises(ValidationError):
            self.svc.register_store("", "Name")
        with self.assertRaises(ValidationError):
            self.w.update_store(1, bogus_field="x")

    def test_deactivation_preserves_history(self):
        sid = self.svc.register_store("S01", "Store A")
        self.svc.configure_month(sid, "2026-09", opening_headcount_ft=9)
        self.svc.record_daily(sid, date(2026, 9, 1), sales_cents=rm(100))
        self.svc.deactivate_store(sid)
        self.assertFalse(self.r.get_store(sid).active)
        days = self.r.get_daily_records(sid, "2026-09")
        self.assertEqual(days[0].sales_cents, rm(100))  # history intact
        self.svc.activate_store(sid)
        self.assertTrue(self.r.get_store(sid).active)

    def test_update_unknown_store(self):
        with self.assertRaises(RecordNotFoundError):
            self.w.update_store(999, name="x")


class TestConfigWrites(WriteFixture):
    def setUp(self):
        super().setUp()
        self.sid = self.svc.register_store("S01", "Store A")

    def test_create_update_partial(self):
        self.svc.configure_month(self.sid, "2026-09", level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                                 salary_ratio_target=0.06818)
        # Partial update: only opening headcount changes.
        self.svc.configure_month(self.sid, "2026-09", opening_headcount_ft=9)
        cfg = self.r.get_month_config(self.sid, "2026-09")
        self.assertEqual(cfg.level1_base_cents, rm(200000))
        self.assertEqual(cfg.opening_headcount_ft, 9)

    def test_nullable_payroll_roundtrip(self):
        self.svc.record_payroll(self.sid, "2026-09", rm(12000.00), rm(1000.00), None)
        cfg = self.r.get_month_config(self.sid, "2026-09")
        self.assertEqual(cfg.salary_basic_allowance_cents, rm("12000.00"))
        self.assertIsNone(cfg.support_team_cost_cents)
        # Reset to awaiting payroll explicitly.
        self.svc.record_payroll(self.sid, "2026-09", None, None)
        self.assertIsNone(self.r.get_month_config(self.sid, "2026-09")
                          .salary_basic_allowance_cents)

    def test_invalid_month_and_pct_range(self):
        with self.assertRaises(InvalidMonthError):
            self.svc.configure_month(self.sid, "2026-13")
        with self.assertRaises(ValidationError):
            self.svc.configure_month(self.sid, "2026-09", part_timer_factor=11)

    def test_finalized_snapshot_risk_flag(self):
        self.svc.configure_month(self.sid, "2026-09")
        result = self.svc.configure_month(self.sid, "2026-09", budget_headcount=9)
        self.assertFalse(result["finalized_snapshot_exists"])
        conn = store_ops_db.connect(self.db_path)
        conn.execute(
            "INSERT INTO monthly_snapshots (store_id, month, version, kpi_json, "
            "finalized) VALUES (?,?,?,?,1)", (self.sid, "2026-09", 1, "{}"))
        conn.commit(); conn.close()
        result = self.svc.configure_month(self.sid, "2026-09", budget_headcount=8)
        self.assertTrue(result["finalized_snapshot_exists"])  # reported, not blocked
        snap = self.r.get_snapshot(self.sid, "2026-09")  # snapshot untouched
        self.assertEqual(snap["version"], 1)

    def test_unknown_store_config(self):
        with self.assertRaises(StoreNotFoundError):
            self.svc.configure_month(999, "2026-09")


class TestCategoryBudgetWrites(WriteFixture):
    def setUp(self):
        super().setUp()
        self.s1 = self.svc.register_store("A01", "A")
        self.s2 = self.svc.register_store("B02", "B")
        self.cat1 = self.seeded_category(self.s1, "Electricity")
        self.svc.configure_month(self.s1, "2026-09")

    def test_rename_keeps_id_and_transactions(self):
        eid = self.svc.record_expense(self.s1, incurred_date=date(2026, 9, 1),
                                      category_id=self.cat1, amount_cents=rm(50),
                                      status="Paid", paid_date=date(2026, 9, 2),
                                      description="x", paid_by="Petty Cash",
                                      verification="Verified", recorded_by="Staff A")
        self.svc.rename_expense_category(self.s1, self.cat1, "Power")
        cats = self.r.get_expense_categories(self.s1)
        self.assertEqual((cats[0].id, cats[0].name), (self.cat1, "Power"))
        # Transaction still references the same stable ID.
        tx = self.r.get_expense_transactions(self.s1)
        self.assertEqual(tx[0].input.category_id, self.cat1)

    def test_budget_upsert_and_isolation(self):
        self.svc.set_expense_budget(self.s1, "2026-09", self.cat1, 0.04)
        self.svc.set_expense_budget(self.s1, "2026-09", self.cat1, 0.031)
        budgets = self.r.get_expense_budgets(self.s1, "2026-09")
        self.assertEqual(budgets, {self.cat1: 0.031})  # updated, not duplicated
        # Cross-store category rejected.
        with self.assertRaises(RecordNotFoundError):
            self.svc.set_expense_budget(self.s2, "2026-09", self.cat1, 0.01)
        with self.assertRaises(RecordNotFoundError):
            self.svc.rename_expense_category(self.s2, self.cat1, "Hijack")

    def test_nullable_budget(self):
        self.svc.set_expense_budget(self.s1, "2026-09", self.cat1, None)
        self.assertEqual(self.r.get_expense_budgets(self.s1, "2026-09"),
                         {self.cat1: None})


class TestDailyWrites(WriteFixture):
    def setUp(self):
        super().setUp()
        self.s1 = self.svc.register_store("A01", "A")
        self.s2 = self.svc.register_store("B02", "B")

    def test_create_partial_update_null_zero(self):
        rid = self.svc.record_daily(self.s1, date(2026, 9, 1))  # unreported
        self.assertIsNone(self.r.get_daily_records(self.s1, "2026-09")[0].sales_cents)
        # Reported zero-sales day.
        self.svc.record_daily(self.s1, date(2026, 9, 2), sales_cents=0)
        # Partial update of day 1: sales only, staffing untouched.
        self.svc.record_daily(self.s1, date(2026, 9, 1), ft_working=7)
        self.svc.correct_daily(self.s1, date(2026, 9, 1), sales_cents=rm(10000))
        days = self.r.get_daily_records(self.s1, "2026-09")
        self.assertEqual(len(days), 2)
        self.assertEqual(days[0].sales_cents, rm(10000))
        self.assertEqual(days[0].ft_working, 7)
        self.assertEqual(days[1].sales_cents, 0)
        self.assertIsNone(days[1].ft_working)

    def test_duplicate_day_is_upsert_not_duplicate(self):
        self.svc.record_daily(self.s1, date(2026, 9, 1), sales_cents=rm(1))
        self.svc.record_daily(self.s1, date(2026, 9, 1), sales_cents=rm(2))
        self.assertEqual(len(self.r.get_daily_records(self.s1, "2026-09")), 1)

    def test_validation(self):
        with self.assertRaises(ValidationError):
            self.svc.record_daily(self.s1, date(2026, 9, 1), transactions=-1)
        for field in ("void_cents", "refund_cents"):
            with self.assertRaises(ValidationError):
                self.svc.record_daily(self.s1, date(2026, 9, 1), **{field: -1})
        with self.assertRaises(ValidationError):
            self.svc.record_daily(self.s1, date(2026, 9, 1), bogus=1)

    def test_informational_void_refund_do_not_change_sales_kpis(self):
        self.svc.configure_month(self.s1, "2026-09", level1_base_cents=rm(10000),
                                 level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        self.svc.record_daily(self.s1, date(2026, 9, 1), sales_cents=rm(1000),
                              transactions=10, units=20)
        before = assemble_month(self.r, self.s1, "2026-09").sales
        self.svc.record_daily(self.s1, date(2026, 9, 1), void_cents=rm(100),
                              refund_cents=rm(50))
        day = self.r.get_daily_records(self.s1, "2026-09")[0]
        self.assertEqual((day.void_cents, day.refund_cents), (rm(100), rm(50)))
        after = assemble_month(self.r, self.s1, "2026-09").sales
        self.assertEqual(after, before)

    def test_same_date_different_stores(self):
        self.svc.record_daily(self.s1, date(2026, 9, 1), sales_cents=rm(100))
        self.svc.record_daily(self.s2, date(2026, 9, 1), sales_cents=rm(999))
        self.assertEqual(self.r.get_daily_records(self.s1, "2026-09")[0].sales_cents,
                         rm(100))


class TestStockLossWrites(WriteFixture):
    def setUp(self):
        super().setUp()
        self.s1 = self.svc.register_store("A01", "A")
        self.s2 = self.svc.register_store("B02", "B")

    def test_atomic_daily_plus_stock(self):
        rid = self.svc.record_daily(
            self.s1, date(2026, 9, 1), sales_cents=rm(1000),
            stock_losses={"DMO": rm(70), "EXTRA": rm(5)})
        days = self.r.get_daily_records(self.s1, "2026-09")
        self.assertEqual(days[0].stock_losses, {"DMO": rm(70), "EXTRA": rm(5)})
        signs = self.r.get_contribution_signs()
        from ducklab import kpi
        self.assertEqual(kpi.total_loss_cents(days[0].stock_losses, signs), rm(65))

    def test_stock_upsert_and_zero(self):
        self.svc.record_daily(self.s1, date(2026, 9, 1),
                              stock_losses={"DMO": rm(10)})
        self.svc.record_daily(self.s1, date(2026, 9, 1),
                              stock_losses={"DMO": 0})
        days = self.r.get_daily_records(self.s1, "2026-09")
        self.assertEqual(days[0].stock_losses, {"DMO": 0})  # updated, zero kept

    def test_atomic_rollback_on_bad_code(self):
        with self.assertRaises(ValidationError):
            self.svc.record_daily(self.s1, date(2026, 9, 1), sales_cents=rm(100),
                                  stock_losses={"BOGUS": rm(1)})
        # No partial write: daily record must not exist.
        self.assertEqual(self.r.get_daily_records(self.s1, "2026-09"), [])

    def test_cross_store_stock_rejection(self):
        # Store 2 cannot write stock entries onto store 1's daily record:
        # record_daily is store-scoped; writing to s2's day never touches s1.
        self.svc.record_daily(self.s1, date(2026, 9, 1), sales_cents=rm(1),
                              stock_losses={"DMO": rm(10)})
        self.svc.record_daily(self.s2, date(2026, 9, 1),
                              stock_losses={"DMO": rm(999)})
        d1 = self.r.get_daily_records(self.s1, "2026-09")
        self.assertEqual(d1[0].stock_losses, {"DMO": rm(10)})


class TestExpenseWrites(WriteFixture):
    def setUp(self):
        super().setUp()
        self.s1 = self.svc.register_store("A01", "A")
        self.s2 = self.svc.register_store("B02", "B")
        self.cat1 = self.seeded_category(self.s1, "Electricity")
        self.cat2 = self.seeded_category(self.s2, "Electricity")

    def test_draft_allowed_then_lifecycle(self):
        eid = self.svc.record_expense(self.s1, incurred_date=date(2026, 9, 5),
                                      category_id=self.cat1, amount_cents=rm(280),
                                      status="Pending", description="socket")
        tx = self.r.get_expense_transactions(self.s1)[0]
        from ducklab import kpi
        self.assertEqual(kpi.expense_validation(tx.input, {self.cat1}),
                         "Missing recorder")
        self.svc.mark_expense_paid(self.s1, eid, date(2026, 9, 7), "HQ / Central")
        self.svc.verify_expense(self.s1, eid, "Verified", receipt_ref="INV1")
        tx = self.r.get_expense_transactions(self.s1)[0]
        self.assertEqual((tx.input.status, tx.input.paid_date, tx.receipt_ref),
                         ("Paid", date(2026, 9, 7), "INV1"))
        self.svc.cancel_expense(self.s1, eid)
        self.assertEqual(self.r.get_expense_transactions(self.s1)[0].input.status,
                         "Cancelled")
        # Still exactly one row — lifecycle updates never duplicate.
        self.assertEqual(len(self.r.get_expense_transactions(self.s1)), 1)

    def test_enum_validation(self):
        for bad in ({"status": "Bogus"}, {"paid_by": "Cash"}, {"verification": "OK"}):
            with self.assertRaises(ValidationError):
                self.svc.record_expense(self.s1, **bad)

    def test_cross_month_single_record(self):
        eid = self.svc.record_expense(self.s1, incurred_date=date(2026, 8, 28),
                                      category_id=self.cat1, amount_cents=rm(100),
                                      status="Pending", description="x",
                                      recorded_by="Staff A")
        # Paid in October — update, not new row.
        self.svc.mark_expense_paid(self.s1, eid, date(2026, 10, 3), "Petty Cash")
        rows = self.r.get_expense_transactions(self.s1)
        self.assertEqual(len(rows), 1)
        from ducklab import kpi
        inputs = [x.input for x in rows]
        self.assertEqual(kpi.expense_totals_cents(inputs, "2026-10", self.s1)["paid"],
                         rm(100))
        self.assertEqual(kpi.expense_totals_cents(inputs, "2026-09", self.s1)["paid"], 0)

    def test_cross_store_rejections(self):
        with self.assertRaises(RecordNotFoundError):
            self.svc.record_expense(self.s1, category_id=self.cat2)  # B's category
        eid = self.svc.record_expense(self.s1, amount_cents=rm(1), status="Pending")
        with self.assertRaises(RecordNotFoundError):
            self.svc.update_expense(self.s2, eid, status="Cancelled")  # B edits A
        # Unchanged.
        self.assertEqual(self.r.get_expense_transactions(self.s1)[0].input.status,
                         "Pending")

    def test_update_unknown_expense(self):
        with self.assertRaises(RecordNotFoundError):
            self.svc.update_expense(self.s1, 999, remarks="x")


class TestActionWrites(WriteFixture):
    def setUp(self):
        super().setUp()
        self.s1 = self.svc.register_store("A01", "A")
        self.s2 = self.svc.register_store("B02", "B")

    def test_lifecycle(self):
        aid = self.svc.raise_action(self.s1, date(2026, 9, 1), "Sales Performance",
                                    "Low pace", "High", pic="Staff A",
                                    due_date=date(2026, 9, 10))
        self.svc.update_action(self.s1, aid, corrective_action="Coach team")
        self.svc.update_action_status(self.s1, aid, "In progress")
        self.svc.update_action_status(self.s1, aid, "Done",
                                      completion_date=date(2026, 9, 9))
        a = self.r.get_actions(self.s1)[0]
        self.assertEqual((a.input.status, a.completion_date, a.corrective_action),
                         ("Done", date(2026, 9, 9), "Coach team"))

    def test_enum_validation(self):
        with self.assertRaises(ValidationError):
            self.svc.raise_action(self.s1, date(2026, 9, 1), "Bad Pillar", "x", "High")
        with self.assertRaises(ValidationError):
            self.svc.raise_action(self.s1, date(2026, 9, 1), "Sales Performance",
                                  "x", "Urgent")

    def test_cross_store_rejection(self):
        aid = self.svc.raise_action(self.s1, date(2026, 9, 1), "Sales Performance",
                                    "x", "Low")
        with self.assertRaises(RecordNotFoundError):
            self.svc.update_action_status(self.s2, aid, "Done")
        self.assertEqual(self.r.get_actions(self.s1)[0].input.status, "Open")

    def test_actions_survive_month_end(self):
        self.svc.raise_action(self.s1, date(2026, 9, 28), "Stock Management",
                              "x", "Low")
        self.assertEqual(len(self.r.get_actions(self.s1)), 1)
        self.assertEqual(len(self.r.get_actions(self.s1, "2026-10")), 0)


class TestReadAfterWriteIntegration(WriteFixture):
    def test_write_then_recalculate_then_update(self):
        sid = self.svc.register_store("S01", "Store A")
        self.svc.configure_month(
            sid, "2026-09", level1_base_cents=rm(20000), min_rtm_target_cents=rm(200000),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            salary_ratio_target=0.06818, opening_headcount_ft=9,
            turnover_threshold=0.45, stock_loss_target=0.015,
            salary_basic_allowance_cents=rm(1000), overtime_cents=rm(0),
            part_timer_factor=0.5)
        other = self.svc.register_store("B02", "B")
        self.svc.configure_month(other, "2026-09", level1_base_cents=rm(999999), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)

        self.svc.record_daily(sid, date(2026, 9, 1), sales_cents=rm(10000),
                              transactions=100, units=300, ft_working=7,
                              pt_working=2)
        r1 = assemble_month(self.r, sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(r1.sales.sales_mtd_cents, rm(10000))
        self.assertEqual(r1.manpower.average_effective_staff, Decimal(8))

        # Update the day; recalculation reflects the change.
        self.svc.correct_daily(sid, date(2026, 9, 1), sales_cents=rm(25000))
        r2 = assemble_month(self.r, sid, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(r2.sales.sales_mtd_cents, rm(25000))
        self.assertEqual(r2.sales.status_by_level["level1"], "Achieved")

        # Other store untouched.
        r_other = assemble_month(self.r, other, "2026-09", as_of=date(2026, 9, 30))
        self.assertEqual(r_other.sales.sales_mtd_cents, 0)


if __name__ == "__main__":
    unittest.main()
