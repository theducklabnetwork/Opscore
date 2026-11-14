"""P1 tests: Sales, Manpower, Expenses, Stock — plain wording (presentation
only). One money format, "Level 3" (not "of 4"), SSSG rows, chart legend
"Actual" / "Target" and axis "Day", headcount variance "0", "(To Date)"
salary ratios, "Needs Review: N" filtering the register to exactly those
lines, the budget "×" (same as the old checkbox), "Missing" / "Extra"
labels, and the page wording. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_pages_p1.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    month = "2026-10"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "p1.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sid, "2026-10", min_rtm_target_cents=rm(200000),
            level1_base_cents=rm(250000),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=9, budget_headcount=9,
            part_timer_factor=0.5, salary_ratio_target=0.06818,
            stock_loss_target=0.015,
            salary_basic_allowance_cents=rm(12000), overtime_cents=rm(500))
        for d in range(1, 11):
            self.app.record_daily(
                self.admin, self.sid, date(2026, 10, d),
                sales_cents=rm(9000), ft_working=7, pt_working=2,
                stock_losses={"DMO": rm(20), "MISSING": rm(5),
                              "EXTRA": rm(2)})

    def tearDown(self):
        self._tmp.cleanup()

    def load(self, modname, clsname, loader, user=None):
        import importlib
        mod = importlib.import_module(f"ducklab.{modname}")
        cls = getattr(mod, clsname)
        sid, actor, month = self.sid, user or self.admin, self.month
        st = cls()
        st.selected_store_active = True
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(cls, "_actor", lambda s_: actor), \
                mock.patch.object(cls, "_selected_store_id",
                                  lambda s_: sid), \
                mock.patch.object(cls, "_selected_month",
                                  lambda s_: month):
            getattr(st, loader)()
        return st, mod, cls

    def bound(self, mod, cls, fn):
        sid, admin, month = self.sid, self.admin, self.month
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(cls, "_actor", lambda s_: admin), \
                mock.patch.object(cls, "_selected_store_id",
                                  lambda s_: sid), \
                mock.patch.object(cls, "_selected_month",
                                  lambda s_: month):
            return fn()


# Sales ------------------------------------------------------------------------
class SalesTests(Fixture):
    def test_values_rows_and_chart(self):
        st, _, _ = self.load("ops_sales_state", "SalesState",
                             "load_sales_data")
        self.assertEqual(st.sp_sales_mtd_exact, "RM 90,000.00")
        # 90,000 / 10 x 31 = 279,000 -> Level 2 (275,000), value "Level 2"
        self.assertEqual(st.sp_month_end_exact, "RM 279,000.00")
        self.assertEqual(st.sp_level, "Level 2")
        self.assertEqual([r["label"] for r in st.sp_comparisons],
                         ["SSSG/Year", "SSSG/Month"])
        names = [t.name for t in st.sp_fig_dark.data]
        self.assertEqual(names[:2], ["Actual", "Target"])
        self.assertEqual(st.sp_fig_dark.layout.xaxis.title.text, "Day")


# Manpower ---------------------------------------------------------------------
class ManpowerTests(Fixture):
    def test_variance_money_and_to_date(self):
        st, _, _ = self.load("ops_manpower_state", "ManpowerState",
                             "load_manpower_data")
        self.assertEqual(st.hc_kpis["variance"], "0")          # not "+0"
        self.assertEqual(st.pay_kpis["total"], "RM 12,500.00")
        # salary budget = sales 90,000 x 6.818% = RM 6,136.20
        self.assertEqual(st.pay_kpis["budget"], "RM 6,136.20")
        self.assertEqual(st.pay_kpis["variance"], "−RM 6,363.80")
        self.assertEqual(st.pay_kpis["mtd"], "1")       # "(To Date)" labels

    def test_viewer_payroll_still_restricted(self):
        viewer = self.app.create_user(self.admin, "v@ducklab.com", "V",
                                      "ViewerPass123!")
        self.app.assign_role(self.admin, viewer.id, "viewer")
        self.app.assign_store(self.admin, viewer.id, self.sid)
        viewer = self.app.auth.get_user(viewer.id)
        st, _, _ = self.load("ops_manpower_state", "ManpowerState",
                             "load_manpower_data", user=viewer)
        self.assertEqual(st.pay_kpis, {"status": "Restricted",
                                       "status_color": "gray"})


# Expenses ---------------------------------------------------------------------
class ExpensesTests(Fixture):
    def setUp(self):
        super().setUp()
        self.cat = self.app.add_expense_category(self.admin, self.sid,
                                                 "Repairs & Maintenance",
                                                 sort_order=1)
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat, 0.005)  # 0.5% x RM 200,000 = RM 1,000
        common = dict(category_id=self.cat, recorded_by="Staff A")
        # OK, Unverified (needs review), Missing paid date (needs review)
        self.app.record_expense(
            self.admin, self.sid, incurred_date=date(2026, 10, 2),
            paid_date=date(2026, 10, 3), description="OK line",
            amount_cents=rm(100), paid_by="Petty Cash", status="Paid",
            verification="Verified", **common)
        self.app.record_expense(
            self.admin, self.sid, incurred_date=date(2026, 10, 4),
            paid_date=date(2026, 10, 5), description="Unverified line",
            amount_cents=rm(200), paid_by="Petty Cash", status="Paid",
            verification="Unverified", **common)
        self.app.record_expense(
            self.admin, self.sid, incurred_date=date(2026, 10, 6),
            paid_date=None, description="Pending unverified",
            amount_cents=rm(300), status="Pending", **common)
        # previous month, needs review but out of October's scope
        self.app.record_expense(
            self.admin, self.sid, incurred_date=date(2026, 9, 6),
            paid_date=date(2026, 9, 7), description="September line",
            amount_cents=rm(50), paid_by="Petty Cash", status="Paid",
            verification="Unverified", **common)

    def test_needs_review_filters_exactly_those_lines(self):
        st, mod, cls = self.load("ops_expenses_state", "ExpensesState",
                                 "load_expenses_data")
        self.assertEqual(st.needs_review_count, 2)
        self.bound(mod, cls, st.show_needs_review)
        rows = self.bound(mod, cls, lambda: st.expense_rows)
        self.assertEqual(sorted(r["description"] for r in rows),
                         ["Pending unverified", "Unverified line"])
        st.clear_needs_review()
        self.assertFalse(st.filter_review)

    def test_budget_x_is_the_old_checkbox(self):
        st, mod, cls = self.load("ops_expenses_state", "ExpensesState",
                                 "load_expenses_data")
        st.b_category = "Repairs & Maintenance"
        self.assertTrue(st.b_has_saved)
        st.b_amount = "1000"
        st.clear_budget_x()
        self.assertEqual((st.b_amount, st.b_clear), ("", True))
        self.bound(mod, cls, lambda: list(st.save_budget()))
        self.assertEqual(st.b_error, "")
        self.assertEqual(self.app.get_expense_budgets(
            self.admin, self.sid, "2026-10"), {self.cat: None})

    def test_filters_title_case(self):
        st, _, _ = self.load("ops_expenses_state", "ExpensesState",
                             "load_expenses_data")
        self.assertEqual(st.period_options,
                         ["Incurred This Month", "Paid This Month",
                          "Pending", "All"])
        st.set_sort_key("Amount")
        self.assertEqual((st.sort_key, st.sort_label), ("amount", "Amount"))
        st.set_filter_status("All Statuses")
        self.assertEqual(st.filter_status, "")

    def test_horizontal_budget_chart(self):
        st, _, _ = self.load("ops_expenses_state", "ExpensesState",
                             "load_expenses_data")
        budget, paid = st.budget_fig_dark.data
        self.assertEqual((budget.name, paid.name), ("Budget", "Paid"))
        self.assertEqual(paid.orientation, "h")
        # The chart lists every category: the ten seeded defaults plus this
        # test's own "Repairs & Maintenance".
        from ducklab.expense_category_defaults import DEFAULT_EXPENSE_CATEGORIES
        self.assertEqual(
            sorted(paid.y),
            sorted([name for name, _pct in DEFAULT_EXPENSE_CATEGORIES]
                   + ["Repairs & Maintenance"]))


# Stock ------------------------------------------------------------------------
class StockTests(Fixture):
    def test_labels_and_legend(self):
        st, _, _ = self.load("ops_stock_state", "StockState",
                             "load_stock_data")
        labels = [r["label"] for r in st.cat_rows]
        self.assertEqual(labels[:5], ["DMO", "DMC", "DMP", "Missing",
                                      "Extra"])
        dmq = next(r for r in st.cat_rows if r["code"] == "DMQ")
        self.assertEqual(dmq["name"], "Damage in Quality")
        self.assertEqual({t.name for t in st.comp_fig_dark.data},
                         {"Total Loss", "Offset"})
        self.assertIn("Missing", list(st.comp_fig_dark.data[0].x))


# Page wording -----------------------------------------------------------------
class PageWordingTests(unittest.TestCase):
    def check(self, page, present, gone):
        for text in present:
            self.assertIn(f'"{text}"', page, text)
        for text in gone:
            self.assertNotIn(text, page, text)

    def test_sales(self):
        from ducklab.ops_sales_pages import sales_page
        self.check(str(sales_page()),
                   ("Target:", "Sales", "Projected Sales", "Achievement",
                    "Level Reached", "Average Daily Sales", "Basket", "BV",
                    "Sales Trend", "Targets", "Gap", "Transactions",
                    "Daily Sales", "PH", "Contribution", "Adjusted Target",
                    "UPT", "Result"),
                   ("Compare against", "Sales MTD", "Month-end sales",
                    "Basket Metrics", "Pace vs Level Targets",
                    "Target Ladder", "vs Budget", "Sales per Receipt",
                    "Sales against the monthly", "Achievement to date",
                    "Needed per remaining day"))

    def test_sales_ipr_uses_sales_ipr_values(self):
        from ducklab import ops_sales_pages as p
        self.assertIn(("UPT", True), p._DAILY_COLS)
        self.assertNotIn(("IPR", True), p._DAILY_COLS)
        self.assertEqual(p._DAILY_COLS[8], ("UPT", True))
        row = dict(date="1", weekday="", ph="", contribution="", target="",
                   adjusted="", sales="", transactions="", txn_pct="",
                   txn_pct_neg="", spr="", spr_pct="", spr_pct_neg="",
                   ipr="IPR-VALUE", ipr_pct="", ipr_pct_neg="", result="",
                   result_color="gray", iso="2026-10-01")
        self.assertIn('"IPR-VALUE"', str(p._daily_row(row)))
        basket = str(p._basket())
        self.assertIn('"UPT"', basket)
        self.assertIn('.sp_upt_rx_state_', basket)
        self.assertIn('id:"basket-ipr-state"', basket)

    def test_manpower(self):
        from ducklab.ops_manpower_pages import manpower_page
        self.check(str(manpower_page()),
                   ("Opening Headcount", "New Hires", "Resignations",
                    "Closing Headcount", "Budget Headcount", "Variance",
                    "Turnover", "Productivity", "Average Effective Staff",
                    "Sales per Staff", "Salary", "Target", "Salary Cost",
                    "Basic + Allowance", "Overtime", "Support Cost",
                    "Salary Budget", "Edit Payroll", "Annual Leave",
                    "Medical Leave", "Other Paid Leave (incl. MC/RPL)",
                    "Emergency Leave", "Unpaid Leave",
                    "Total Leave", "Daily Staffing", "FT", "PT",
                    "Effective", "Hires", "Leave"),
                   ("Headcount variance", "Closing − budget",
                    "Staffing efficiency", "Ratio incl. support vs target",
                    "Month to date: the ratio", "Edit configuration",
                    "Store-level daily leave", "Sales / staff",
                    "Edit staffing opens", "hc-status", "ratio-status",
                    "Leave blank to keep", "e.g. 0.5", "Headcount, staffing"))

    def test_expenses(self):
        from ducklab.ops_expenses_pages import expenses_page
        self.check(str(expenses_page()),
                   ("Paid", "Incurred", "Pending", "Budget", "Utilization",
                    "Variance", "Budget vs Paid", "Remaining", "Set Budget",
                    "Category", "Budget (%)", "Save", "Transactions",
                    "Add Expense", "Paid By", "Receipt Ref",
                    "All Statuses", "All Verifications"),
                   ("Pending / outstanding", "Total budget",
                    "Budget utilization", "Paid-date basis",
                    "Update monthly budget",
                    "Save budget", "Transaction register", "Add expense",
                    "Data check", "Category budget monitor",
                    "Budgets belong to", "Record expenses, track",
                    "RadixThemesCheckbox", "Budget (RM)"))

    def test_stock(self):
        from ducklab.ops_stock_pages import stock_page
        self.check(str(stock_page()),
                   ("Total Loss", "Loss Ratio", "Missing", "Extra",
                    "Daily Loss", "Loss by Category", "Categories", "Code",
                    "Description", "Total", "Share", "Days", "Treatment",
                    "Daily Stock Loss", "Ratio", "Comparison",
                    "No Previous Month", "This Month"),
                   ("Total loss = DMO", "Daily total loss",
                    "Loss composition", "Category breakdown",
                    "Monthly total", "Share of loss", "Month comparison",
                    "No previous-month", "Incomplete month: ",
                    "Stock losses by category", "Missing at stock balance",
                    "Offsets total loss"))
        self.assertIn("stock-status", str(stock_page()))   # id kept


if __name__ == "__main__":
    unittest.main()
