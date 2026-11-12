"""Tests for Expenses Management: authorized writes, validation ladder,
budget rules, cross-month accounting, RBAC. Temp DBs only.

Run: python -m unittest tests.test_ops_expenses -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import kpi
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.operations_write_repository import (
    RecordNotFoundError, ValidationError,
)


def rm(v):
    return int(Decimal(str(v)) * 100)


class ExpenseFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "exp.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.sb = self.app.register_store(self.admin, "B02", "Beta")
        for sid in (self.sa, self.sb):
            self.app.configure_month(
                self.admin, sid, "2026-09", min_rtm_target_cents=rm(200000),
                level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                opening_headcount_ft=9, stock_loss_target=0.015,
                salary_ratio_target=0.06818, part_timer_factor=0.5,
                salary_basic_allowance_cents=rm(10000),
                overtime_cents=rm(1000))
            self.app.configure_month(
                self.admin, sid, "2026-10", min_rtm_target_cents=rm(200000),
                level1_base_cents=rm(210000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                opening_headcount_ft=10, stock_loss_target=0.015,
                salary_ratio_target=0.06818, part_timer_factor=0.5,
                salary_basic_allowance_cents=rm(10500),
                overtime_cents=rm(1000))
        # Reuse the default "Electricity" categories seeded by create_store().
        self.cat_a = next(
            c.id for c in self.app.get_expense_categories(self.admin, self.sa)
            if c.name == "Electricity")
        self.cat_b = next(
            c.id for c in self.app.get_expense_categories(self.admin, self.sb)
            if c.name == "Electricity")

    def tearDown(self):
        self._tmp.cleanup()

    def user(self, email, roles, stores):
        u = self.app.create_user(self.admin, email, "T", "Passw0rd!xyz")
        for r in roles:
            self.app.assign_role(self.admin, u.id, r)
        for s in stores:
            self.app.assign_store(self.admin, u.id, s)
        return self.app.auth.get_user(u.id)

    def get(self, store_id, expense_id):
        recs = self.app.get_expense_transactions(self.admin, store_id)
        return next(r for r in recs if r.id == expense_id)


class TestExpenseCRUD(ExpenseFixture):
    def test_create_and_read_after_write(self):
        eid = self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 2),
            category_id=self.cat_a, description="Utility bill",
            amount_cents=rm(125.50), status="Paid",
            paid_date=date(2026, 10, 3), paid_by="Store Account",
            receipt_ref="RCP-1", verification="Verified",
            recorded_by="Manager", remarks="September usage")
        rec = self.get(self.sa, eid)
        self.assertEqual(rec.input.amount_cents, rm(125.50))
        self.assertEqual(rec.receipt_ref, "RCP-1")
        self.assertEqual(rec.remarks, "September usage")

    def test_partial_update_preserves_untouched(self):
        eid = self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 2),
            category_id=self.cat_a, description="Utility bill",
            amount_cents=rm(100), status="Pending", recorded_by="Manager")
        self.app.update_expense(self.admin, self.sa, eid, remarks="note")
        rec = self.get(self.sa, eid)
        self.assertEqual(rec.input.amount_cents, rm(100))     # preserved
        self.assertEqual(rec.input.description, "Utility bill")   # preserved
        self.assertEqual(rec.remarks, "note")

    def test_pending_to_paid_workflow(self):
        eid = self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 1),
            category_id=self.cat_a, description="Water bill",
            amount_cents=rm(80), status="Pending",
            recorded_by="Manager")
        self.app.update_expense(self.admin, self.sa, eid,
                                status="Paid", paid_date=date(2026, 10, 5),
                                paid_by="Petty Cash")
        rec = self.get(self.sa, eid)
        self.assertEqual(rec.input.status, "Paid")
        self.assertEqual(rec.input.paid_date, date(2026, 10, 5))
        self.assertEqual(kpi.paid_value_cents(rec.input), rm(80))

    def test_cancel_preserves_history(self):
        eid = self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 1),
            category_id=self.cat_a, description="Mistake",
            amount_cents=rm(50), status="Paid",
            paid_date=date(2026, 10, 2), paid_by="Other",
            recorded_by="Manager")
        self.app.update_expense(self.admin, self.sa, eid,
                                status="Cancelled")
        rec = self.get(self.sa, eid)  # still exists, dates preserved
        self.assertEqual(rec.input.status, "Cancelled")
        self.assertEqual(rec.input.paid_date, date(2026, 10, 2))
        self.assertEqual(kpi.paid_value_cents(rec.input), 0)
        self.assertEqual(kpi.incurred_value_cents(rec.input), 0)

    def test_verification_independent_of_payment(self):
        eid = self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 1),
            category_id=self.cat_a, description="x", amount_cents=rm(10),
            status="Paid", paid_date=date(2026, 10, 2),
            paid_by="Other", recorded_by="Staff A", verification="Unverified")
        rec = self.get(self.sa, eid)
        self.assertEqual(rec.input.status, "Paid")
        self.assertEqual(rec.input.verification, "Unverified")
        # and validation reflects it
        self.assertEqual(
            kpi.expense_validation(rec.input, {self.cat_a}), "Unverified")

    def test_incomplete_draft_saved_but_flagged(self):
        eid = self.app.record_expense(self.admin, self.sa,
                                      description="half-entered")
        rec = self.get(self.sa, eid)
        self.assertIsNone(rec.input.amount_cents)
        self.assertEqual(
            kpi.expense_validation(rec.input, {self.cat_a}), "Incomplete")
        self.assertEqual(kpi.paid_value_cents(rec.input), 0)
        self.assertEqual(kpi.incurred_value_cents(rec.input), 0)

    def test_invalid_status_rejected(self):
        with self.assertRaises(ValidationError):
            self.app.record_expense(self.admin, self.sa, status="Settled")
        with self.assertRaises(ValidationError):
            self.app.record_expense(self.admin, self.sa, paid_by="Cashier")
        with self.assertRaises(ValidationError):
            self.app.record_expense(self.admin, self.sa, verification="OK")
        with self.assertRaises(ValidationError):
            self.app.record_expense(self.admin, self.sa,
                                    amount_cents=10.5)  # not integer cents

    def test_cross_store_category_rejected(self):
        with self.assertRaises(RecordNotFoundError):
            self.app.record_expense(self.admin, self.sa,
                                    category_id=self.cat_b)  # B's category
        eid = self.app.record_expense(self.admin, self.sa,
                                      description="x")
        with self.assertRaises(RecordNotFoundError):
            self.app.update_expense(self.admin, self.sa, eid,
                                    category_id=self.cat_b)

    def test_cross_store_expense_edit_rejected(self):
        eid = self.app.record_expense(self.admin, self.sa,
                                      description="x", amount_cents=rm(1))
        with self.assertRaises(RecordNotFoundError):
            self.app.update_expense(self.admin, self.sb, eid,
                                    description="hijack")


class TestValidationLadder(ExpenseFixture):
    def _input(self, **kw):
        base = dict(incurred_date=date(2026, 10, 1), category_id=self.cat_a,
                    description="d", amount_cents=rm(1), status="Pending",
                    verification="Verified", recorded_by="Staff A", paid_date=None,
                    paid_by=None, store_id=self.sa)
        base.update(kw)
        return kpi.ExpenseInput(**base)

    def test_precedence(self):
        k = {self.cat_a}
        v = kpi.expense_validation
        # Incomplete wins first
        self.assertEqual(v(self._input(description=None), k), "Incomplete")
        # then unknown category
        self.assertEqual(
            v(self._input(category_id=9999, status="Paid",
                          paid_date=date(2026, 10, 2), paid_by="Other"), k),
            "Unknown category")
        # then missing paid date
        self.assertEqual(
            v(self._input(status="Paid", paid_by="Other"), k),
            "Missing paid date")
        # then check paid date
        self.assertEqual(
            v(self._input(paid_date=date(2026, 10, 2)), k),
            "Check paid date")
        # then missing paid by
        self.assertEqual(
            v(self._input(status="Paid",
                          paid_date=date(2026, 10, 2)), k),
            "Missing paid by")
        # then missing recorder
        self.assertEqual(
            v(self._input(status="Paid", paid_date=date(2026, 10, 2),
                          paid_by="Other", recorded_by=None), k),
            "Missing recorder")
        # then disputed beats unverified
        self.assertEqual(
            v(self._input(status="Paid", paid_date=date(2026, 10, 2),
                          paid_by="Other", verification="Disputed"), k),
            "Disputed")
        self.assertEqual(
            v(self._input(status="Paid", paid_date=date(2026, 10, 2),
                          paid_by="Other", verification="Unverified"), k),
            "Unverified")
        self.assertEqual(
            v(self._input(status="Paid", paid_date=date(2026, 10, 2),
                          paid_by="Other"), k), "OK")


class TestBudgets(ExpenseFixture):
    def test_budget_create_update_and_status(self):
        self.app.set_expense_budget(self.admin, self.sa, "2026-10",
                                    self.cat_a, 0.02)
        budgets = self.app.get_expense_budgets(self.admin, self.sa, "2026-10")
        self.assertEqual(budgets[self.cat_a], 0.02)
        # update (idempotent, no duplicates)
        self.app.set_expense_budget(self.admin, self.sa, "2026-10",
                                    self.cat_a, 0.015)
        budgets = self.app.get_expense_budgets(self.admin, self.sa, "2026-10")
        self.assertEqual(budgets[self.cat_a], 0.015)

    def test_missing_budget_distinct_from_zero(self):
        budgets = self.app.get_expense_budgets(self.admin, self.sa, "2026-10")
        self.assertIsNone(budgets.get(self.cat_a))       # missing
        self.assertEqual(kpi.budget_status(0, None), "No budget")
        self.assertEqual(kpi.budget_status(1, None), "No budget")
        self.app.set_expense_budget(self.admin, self.sa, "2026-10",
                                    self.cat_a, 0)        # explicit 0%
        budgets = self.app.get_expense_budgets(self.admin, self.sa, "2026-10")
        self.assertEqual(budgets[self.cat_a], 0)
        self.assertEqual(kpi.budget_status(0, 0), "Within budget")
        self.assertEqual(kpi.budget_status(1, 0), "OVER BUDGET")

    def test_watch_threshold_80_percent(self):
        self.assertEqual(kpi.budget_status(rm(3200), rm(4000)), "Watch")
        self.assertEqual(kpi.budget_status(rm(3199.99), rm(4000)),
                         "Within budget")
        self.assertEqual(kpi.budget_status(rm(4000.01), rm(4000)),
                         "OVER BUDGET")

    def test_pct_budget_computes_rm_against_active_target(self):
        """2.5% of the Minimum RTM floor (RM 200,000) -> RM 5,000 budget."""
        self.app.set_expense_budget(self.admin, self.sa, "2026-10",
                                    self.cat_a, 0.025)
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=date(2026, 10, 31))
        self.assertEqual(rep.expenses.budget_basis_label, "Minimum RTM")
        self.assertEqual(rep.expenses.budget_basis_cents, rm(200000))
        self.assertEqual(rep.expenses.total_budget_cents, rm(5000))

    def test_cross_store_budget_rejected(self):
        with self.assertRaises(RecordNotFoundError):
            self.app.set_expense_budget(self.admin, self.sa, "2026-10",
                                        self.cat_b, 0.01)
        # zone manager (not config manager) rejected
        zm = self.user("z@ducklab.com", ["zone_manager"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.set_expense_budget(zm, self.sa, "2026-10",
                                        self.cat_a, 0.01)

    def test_unconfigured_month_budget_rejected(self):
        with self.assertRaises(RecordNotFoundError):
            self.app.set_expense_budget(self.admin, self.sa, "2027-01",
                                        self.cat_a, 0.01)


class TestCrossMonthAccounting(ExpenseFixture):
    def test_critical_cross_month_example(self):
        """Incurred Sep 28, paid Oct 3, RM100 -> Sep incurred + Oct paid,
        single transaction."""
        eid = self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 9, 28),
            paid_date=date(2026, 10, 3), category_id=self.cat_a,
            description="cross-month", amount_cents=rm(100),
            status="Paid", paid_by="Other", recorded_by="Staff A",
            verification="Verified")
        recs = self.app.get_expense_transactions(self.admin, self.sa)
        self.assertEqual(len([r for r in recs if r.id == eid]), 1)  # one row
        sep = self.app.preview_month(self.admin, self.sa, "2026-09",
                                     as_of=date(2026, 9, 30))
        oct_ = self.app.preview_month(self.admin, self.sa, "2026-10",
                                      as_of=date(2026, 10, 31))
        self.assertEqual(sep.expenses.incurred_cents, rm(100))
        self.assertEqual(sep.expenses.paid_cents, 0)
        self.assertEqual(oct_.expenses.paid_cents, rm(100))
        self.assertEqual(oct_.expenses.incurred_cents, 0)

    def test_open_pending_cut_off_at_month_end(self):
        # Pending RM 40 incurred 15 Sep: counts for Sep and later months
        # (still open), never for a month that ended before it was incurred.
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 9, 15),
            category_id=self.cat_a, description="pending", amount_cents=rm(40),
            status="Pending", recorded_by="Staff A")
        for month in ("2026-08", "2026-09", "2026-10"):
            self.app.configure_month(self.admin, self.sa, month)
        aug = self.app.preview_month(self.admin, self.sa, "2026-08",
                                     as_of=date(2026, 8, 31))
        sep = self.app.preview_month(self.admin, self.sa, "2026-09",
                                     as_of=date(2026, 9, 30))
        oct_ = self.app.preview_month(self.admin, self.sa, "2026-10",
                                      as_of=date(2026, 10, 31))
        self.assertEqual(aug.expenses.open_cents, 0)
        self.assertEqual(aug.expenses.pending_count, 0)
        self.assertEqual(sep.expenses.open_cents, 4000)
        self.assertEqual(sep.expenses.pending_count, 1)
        self.assertEqual(oct_.expenses.open_cents, 4000)
        self.assertEqual(oct_.expenses.pending_count, 1)

    def test_kpi_recalculation_after_expense_write(self):
        r1 = self.app.preview_month(self.admin, self.sa, "2026-10",
                                    as_of=date(2026, 10, 31))
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 10, 2),
            paid_date=date(2026, 10, 2), category_id=self.cat_a,
            description="paid now", amount_cents=rm(250), status="Paid",
            paid_by="Other", recorded_by="Staff A")
        r2 = self.app.preview_month(self.admin, self.sa, "2026-10",
                                    as_of=date(2026, 10, 31))
        self.assertEqual(r1.expenses.paid_cents, 0)
        self.assertEqual(r2.expenses.paid_cents, rm(250))
        # budget monitor row reflects it: 0.15% of Minimum RTM RM 200,000
        # = RM 300 budget; RM 250 paid -> 83% -> Watch
        self.app.set_expense_budget(self.admin, self.sa, "2026-10",
                                    self.cat_a, 0.0015)
        r3 = self.app.preview_month(self.admin, self.sa, "2026-10",
                                    as_of=date(2026, 10, 31))
        row = r3.expenses.categories[0]
        self.assertEqual(row.paid_mtd_cents, rm(250))
        self.assertEqual(row.budget_status, "Watch")  # 250/300 = 83%


class TestExpenseSecurity(ExpenseFixture):
    def test_viewer_cannot_write(self):
        v = self.user("v@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.record_expense(v, self.sa, description="x")
        eid = self.app.record_expense(self.admin, self.sa, description="x")
        with self.assertRaises(AuthorizationError):
            self.app.update_expense(v, self.sa, eid, description="y")
        # but can read
        self.assertEqual(
            len(self.app.get_expense_transactions(v, self.sa)), 1)

    def test_branch_manager_can_write(self):
        bm = self.user("bm@ducklab.com", ["branch_manager"], [self.sa])
        eid = bm and self.app.record_expense(
            bm, self.sa, incurred_date=date(2026, 10, 1),
            category_id=self.cat_a, description="ok", amount_cents=rm(5),
            status="Pending", recorded_by="Staff B")
        self.assertIsInstance(eid, int)
        # but cross-store write rejected
        with self.assertRaises(AuthorizationError):
            self.app.record_expense(bm, self.sb, description="nope")

    def test_disabled_user_cannot_write(self):
        bm = self.user("bm2@ducklab.com", ["branch_manager"], [self.sa])
        self.app.set_user_active(self.admin, bm.id, False)
        bm = self.app.auth.get_user(bm.id)
        with self.assertRaises(AuthorizationError):
            self.app.record_expense(bm, self.sa, description="x")

    def test_expired_session_rejected(self):
        from ducklab.auth_service import AuthenticationError
        with self.assertRaises(AuthenticationError):
            self.app.authenticate("bogus-token")

    def test_cross_store_read_rejected(self):
        v = self.user("v2@ducklab.com", ["viewer"], [self.sa])
        with self.assertRaises(AuthorizationError):
            self.app.get_expense_transactions(v, self.sb)

    def test_finalized_snapshot_untouched_by_expense_edits(self):
        self.app.finalize_month(self.admin, self.sa, "2026-09",
                                as_of=date(2026, 9, 30))
        self.app.record_expense(
            self.admin, self.sa, incurred_date=date(2026, 9, 10),
            paid_date=date(2026, 9, 10), category_id=self.cat_a,
            description="late entry", amount_cents=rm(999), status="Paid",
            paid_by="Other", recorded_by="Staff A")
        live = self.app.preview_month(self.admin, self.sa, "2026-09",
                                      as_of=date(2026, 9, 30))
        snap = self.app.get_finalized_report(self.admin, self.sa, "2026-09")
        self.assertEqual(live.expenses.paid_cents, rm(999))
        self.assertEqual(snap["report"]["expenses"]["paid_cents"], 0)

    def test_store_isolation_kpis(self):
        # A: 1.75% of RM 200,000 = RM 3,500; B: 0.75% = RM 1,500
        self.app.set_expense_budget(self.admin, self.sa, "2026-10",
                                    self.cat_a, 0.0175)
        self.app.set_expense_budget(self.admin, self.sb, "2026-10",
                                    self.cat_b, 0.0075)
        for sid, cat, amt in ((self.sa, self.cat_a, 3000),
                              (self.sb, self.cat_b, 500)):
            self.app.record_expense(
                self.admin, sid, incurred_date=date(2026, 10, 2),
                paid_date=date(2026, 10, 2), category_id=cat,
                description="bill", amount_cents=rm(amt), status="Paid",
                paid_by="Other", recorded_by="Staff A")
        ra = self.app.preview_month(self.admin, self.sa, "2026-10",
                                    as_of=date(2026, 10, 31))
        rb = self.app.preview_month(self.admin, self.sb, "2026-10",
                                    as_of=date(2026, 10, 31))
        self.assertEqual(ra.expenses.paid_cents, rm(3000))
        self.assertEqual(rb.expenses.paid_cents, rm(500))
        self.assertEqual(ra.expenses.categories[0].budget_status, "Watch")
        self.assertEqual(rb.expenses.categories[0].budget_status,
                         "Within budget")


if __name__ == "__main__":
    unittest.main()
