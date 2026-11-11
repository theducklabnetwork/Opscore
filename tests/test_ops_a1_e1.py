"""A1 + E1 tests: Action Tracker wording (presentation only) and paid
expenses shown read-only on Daily Operations (by paid date, as workbook
Daily I-R). Disposable temp DBs only.

Run: python -m pytest tests/test_ops_a1_e1.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import kpi
from ducklab.app_service import AppService


def rm(v):
    return int(Decimal(str(v)) * 100)


# A1 --------------------------------------------------------------------------
class ActionWordingTests(unittest.TestCase):
    def test_page_wording(self):
        from ducklab import ops_actions_pages as p
        page = str(p.actions_page())
        for text in ("Open", "Overdue", "Completed", "Add Action",
                     "Overdue Only", "Raised", "Issue", "Priority", "Status",
                     "Owner", "Due", "Carried Over", "No Actions",
                     "Needs Attention"):
            self.assertIn(f'"{text}"', page, text)
        for gone in ("Follow-up actions per store", "Incl. In progress",
                     "Due before today", "Raised this month plus",
                     "Person in charge", "Carried over", "Overdue only",
                     "Add action", "No actions for this month",
                     "Pillar shows from"):
            self.assertNotIn(gone, page, gone)

    def test_filters(self):
        from ducklab.ops_actions_state import (
            ActionsState, FILTER_PILLARS, FILTER_PRIORITIES, FILTER_STATUSES,
        )
        self.assertEqual(FILTER_STATUSES[0], "All Statuses")
        self.assertEqual(FILTER_PILLARS[0], "All Pillars")
        self.assertEqual(FILTER_PRIORITIES[0], "All Priorities")
        st = ActionsState()
        self.assertEqual((st.f_status, st.f_pillar, st.f_priority),
                         ("All Statuses", "All Pillars", "All Priorities"))
        self.assertEqual(st.form_title, "Add Action")
        st.rows = [{"status": "Open", "pillar": "General Operations",
                    "priority": "High", "overdue": False}]
        self.assertEqual(len(st.filtered_rows), 1)    # "All ..." = no filter

    def test_dialog(self):
        from ducklab import ops_actions_pages as p
        dialog = str(p._dialog())
        for text in ("Details", "Issue", "Pillar", "Priority", "Status",
                     "Schedule",
                     "Owner", "Raised", "Due", "Completed", "Follow Up",
                     "Next Step", "Remarks", "Save Action",
                     "You have read-only access."):
            self.assertIn(f'"{text}"', dialog, text)
        for gone in ("Issue / observation", "Owner & dates",
                     "Person in charge", "Raised date", "Due date",
                     "Completion date", "Defaults to today",
                     "Corrective action", "are required",
                     "Contact an administrator"):
            self.assertNotIn(gone, dialog, gone)


# E1 --------------------------------------------------------------------------
class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "e1.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.other = self.app.register_store(self.admin, "B02", "Beta")
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=rm(250000),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            part_timer_factor=0.5)
        # Reuse the default "Electricity" categories seeded by create_store().
        self.cat = next(
            c.id for c in self.app.get_expense_categories(self.admin, self.sid)
            if c.name == "Electricity")
        self.cat_b = next(
            c.id for c in self.app.get_expense_categories(self.admin, self.other)
            if c.name == "Electricity")

    def tearDown(self):
        self._tmp.cleanup()

    def expense(self, amount, status="Paid", paid=None, incurred=None,
                store=None):
        store = store or self.sid
        fields = dict(incurred_date=incurred or date(2026, 10, 1),
                      category_id=self.cat if store == self.sid
                      else self.cat_b,
                      description="Bill", amount_cents=rm(amount),
                      status=status, recorded_by="Manager")
        if paid is not None:
            fields.update(paid_date=paid, paid_by="Petty Cash")
        return self.app.record_expense(self.admin, store, **fields)

    def seed(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 3),
                              sales_cents=rm(9000))
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 5),
                              sales_cents=rm(8000))
        # paid on the 5th: two lines; incurred in September counts by paid
        self.expense(125.50, paid=date(2026, 10, 5))
        self.expense(74.50, paid=date(2026, 10, 5),
                     incurred=date(2026, 9, 28))
        # paid on an unreported day (the 7th)
        self.expense(40, paid=date(2026, 10, 7))
        # never counted: pending, cancelled, paid next month, other store
        self.expense(999, status="Pending")
        self.expense(888, status="Cancelled")
        self.expense(777, paid=date(2026, 11, 2))
        self.expense(666, paid=date(2026, 10, 5), store=self.other)

    def bound(self, fn):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        sid, admin = self.sid, self.admin
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(DailyState, "_actor", lambda s_: admin), \
                mock.patch.object(DailyState, "_selected_store_id",
                                  lambda s_: sid), \
                mock.patch.object(DailyState, "_selected_month",
                                  lambda s_: "2026-10"):
            return fn()

    def state(self):
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        st.selected_store_active = True
        self.bound(st.load_daily_data)
        return st


class PaidByDayTests(Fixture):
    def test_engine(self):
        self.seed()
        recs = [r.input for r in self.app.get_expense_transactions(
            self.admin, self.sid)]
        self.assertEqual(kpi.paid_cents_by_day(recs, "2026-10", self.sid),
                         {date(2026, 10, 5): 20000, date(2026, 10, 7): 4000})
        # same total as the Expenses page "Paid" tile (engine rule)
        self.assertEqual(
            kpi.expense_totals_cents(recs, "2026-10", self.sid)["paid"],
            24000)

    def test_net_zero_day_still_shown(self):
        self.expense(50, paid=date(2026, 10, 9))
        self.expense(-50, paid=date(2026, 10, 9))
        recs = [r.input for r in self.app.get_expense_transactions(
            self.admin, self.sid)]
        self.assertEqual(kpi.paid_cents_by_day(recs, "2026-10", self.sid),
                         {date(2026, 10, 9): 0})


class DailyExpensesTests(Fixture):
    def test_column_tile_and_entry_line(self):
        self.seed()
        st = self.state()
        by_iso = {r["iso"]: r for r in st.day_rows}
        self.assertEqual(by_iso["2026-10-05"]["expenses"], "RM 200.00")
        self.assertEqual(by_iso["2026-10-03"]["expenses"], "—")
        # unreported day keeps its paid amount
        self.assertEqual(by_iso["2026-10-07"]["expenses"], "RM 40.00")
        self.assertEqual(by_iso["2026-10-07"]["status"], "Not Reported")
        self.assertEqual(st.day_kpis["expenses"], "RM 240.00")
        # Daily Entry: read-only line, no expense fields
        self.bound(lambda: st.open_entry("2026-10-05"))
        self.assertEqual(st.form_expenses, "RM 200.00")
        self.bound(lambda: st.open_entry("2026-10-03"))
        self.assertEqual(st.form_expenses, "—")

    def test_no_payments(self):
        self.expense(999, status="Pending")
        st = self.state()
        self.assertEqual(st.day_kpis["expenses"], "—")
        self.assertEqual({r["expenses"] for r in st.day_rows}, {"—"})

    def test_saving_an_entry_never_writes_expenses(self):
        self.seed()
        st = self.state()
        self.bound(lambda: st.open_entry("2026-10-05"))
        st.f_units = "12"
        self.bound(lambda: list(st.save_entry()))
        self.assertEqual(st.form_error, "")
        self.assertEqual(
            len(self.app.get_expense_transactions(self.admin, self.sid)), 6)
        self.assertEqual(st.day_kpis["expenses"], "RM 240.00")

    def test_page(self):
        from ducklab import ops_daily_pages as p
        page = str(p.daily_page())
        self.assertIn('"Expenses"', page)
        self.assertIn("kpi-expenses", page)
        self.assertIn("/ops/expenses?paid=", page)
        dialog = str(p._entry_dialog())
        self.assertIn('"Expenses"', dialog)
        self.assertIn("entry-expenses", dialog)
        # no expense input in the entry dialog
        for gone in ("e-amount", "e-paid", "e-category", "set_e_"):
            self.assertNotIn(gone, dialog, gone)


class ExpensesDeepLinkTests(Fixture):
    def test_paid_param(self):
        from ducklab.ops_expenses_state import _paid_param
        self.assertEqual(_paid_param("2026-10-05"), "2026-10-05")
        for bad in ("", "2026-13-01", "05/10/2026", "x"):
            self.assertEqual(_paid_param(bad), "", bad)

    def test_register_filtered_to_the_date(self):
        import ducklab.ops_expenses_state as mod
        from ducklab.ops_expenses_state import ExpensesState
        self.seed()
        st = ExpensesState()
        st.selected_store_active = True
        sid, admin = self.sid, self.admin
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(ExpensesState, "_actor",
                                  lambda s_: admin), \
                mock.patch.object(ExpensesState, "_selected_store_id",
                                  lambda s_: sid), \
                mock.patch.object(ExpensesState, "_selected_month",
                                  lambda s_: "2026-10"):
            st.load_expenses_data()
            st.filter_paid_date = "2026-10-05"
            rows = st.expense_rows
            self.assertEqual(sorted(r["amount"] for r in rows),
                             ["RM 125.50", "RM 74.50"])
            self.assertEqual(st.filter_paid_label, "Paid 05 Oct 2026")
            st.clear_filter_paid_date()
            self.assertEqual(st.filter_paid_label, "")
            # back to the default period (incurred this month)
            self.assertEqual(len(st.expense_rows), 5)
            st.filter_paid_date = "2026-10-07"
            st.set_filter_period_label("Paid this month")
            self.assertEqual(st.filter_paid_date, "")

    def test_filter_chip_on_page(self):
        from ducklab import ops_expenses_pages as p
        page = str(p._filters())
        self.assertIn("filter-paid", page)
        self.assertIn("filter-paid-clear", page)


# Follow-ups: report labels, validation labels, × wording -----------------
class ReportLabelTests(unittest.TestCase):
    def test_incomplete_month_labels(self):
        from ducklab import ops_pages as p
        sales, mp = str(p._sales_section()), str(p._manpower_section())
        self.assertIn('"Projected Achievement"', sales)
        self.assertIn('"Achievement"', sales)            # complete month
        for text in ("Salary Ratio (To Date)", "Total Salary Ratio (To Date)",
                     "Salary Ratio", "Total Salary Ratio"):
            self.assertIn(f'"{text}"', mp, text)


class ValidationLabelTests(Fixture):
    def test_daily_entry_messages(self):
        st = self.state()
        self.bound(lambda: st.open_entry("2026-10-04"))
        st.f_units = "1.5"
        self.bound(lambda: list(st.save_entry()))
        self.assertEqual(st.form_error, "Units Sold must be a whole number")
        st.f_units = ""
        st.f_leave_mc = "-1"
        self.bound(lambda: list(st.save_entry()))
        self.assertEqual(st.form_error, "Medical Leave cannot be negative")
        st.f_leave_mc = ""
        st.stock_form["MISSING"] = "abc"
        self.bound(lambda: list(st.save_entry()))
        self.assertEqual(st.form_error, "Missing: Invalid amount: 'abc'")

    def test_action_messages(self):
        from ducklab.operations_write_repository import ValidationError
        cases = [(dict(issue="", pic="A", due_date=date(2026, 10, 9)),
                  "Issue is required"),
                 (dict(issue="X", pic=" ", due_date=date(2026, 10, 9)),
                  "Owner is required"),
                 (dict(issue="X", pic="A", due_date=None),
                  "Due is required"),
                 (dict(issue="X", pic="A", due_date=date(2026, 10, 9),
                       status="Done", raised_date=date(2026, 10, 5),
                       completion_date=date(2026, 10, 4)),
                  "Completed cannot be earlier than Raised")]
        for over, msg in cases:
            fields = dict(raised_date=date(2026, 10, 1),
                          pillar="General Operations", priority="Medium",
                          status="Open", corrective_action=None,
                          completion_date=None, remarks=None)
            fields.update(over)
            with self.assertRaises(ValidationError) as ctx:
                self.app.save_action(self.admin, self.sid, None,
                                     date(2026, 10, 10), **fields)
            self.assertEqual(str(ctx.exception), msg)


class ClearXBudgetTests(unittest.TestCase):
    def test_monthly_setup_x(self):
        from ducklab.ops_setup_state import SetupState
        st = SetupState()
        st.budget_rows = [{"id": "3", "name": "Elec", "value": "500.00",
                           "original": "500.00", "clear": ""}]
        st.clear_budget("3")
        self.assertEqual((st.budget_rows[0]["value"],
                          st.budget_rows[0]["clear"]), ("", "1"))
        st.set_budget_value("3", "450")          # typing undoes the ×
        self.assertEqual((st.budget_rows[0]["value"],
                          st.budget_rows[0]["clear"]), ("450", ""))

    def test_expenses_budget_x(self):
        from ducklab.ops_expenses_state import ExpensesState
        st = ExpensesState()
        st.b_amount = "300"
        st.clear_budget()
        self.assertEqual((st.b_amount, st.b_clear), ("", True))
        st.set_b_amount("120")
        self.assertEqual((st.b_amount, st.b_clear), ("120", False))

    def test_no_clear_checkbox_left(self):
        from ducklab import ops_expenses_pages as e, ops_stores_pages as s
        page = str(e._budget_editor()) + str(s._budget_row(
            {"id": "3", "name": "Elec", "value": "", "original": "1.00",
             "clear": ""}))
        self.assertNotIn("RadixThemesCheckbox", page)
        self.assertIn("b-clear", page)
        self.assertIn("setup-budget-3-clear", page)


if __name__ == "__main__":
    unittest.main()
