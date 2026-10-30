"""Synthetic KPI rules. All inputs and expectations are invented round values."""

import unittest
from datetime import date
from decimal import Decimal

from ducklab import kpi
from ducklab.kpi import ActionInput, DailyInput, ExpenseInput, MonthConfig

SIGNS = {
    "DMO": 1, "DMC": 1, "DMP": 1, "2DMP": 0, "DPI": 0, "DMW": 0,
    "DDR": 0, "DMN": 0, "DME": 0, "DMQ": 0, "DST": 0,
    "MISSING": 1, "EXTRA": -1,
}


def rm(value) -> int:
    return int(Decimal(str(value)) * 100)


DAYS = [DailyInput(date(2026, 4, day), sales_cents=rm(value))
        for day, value in enumerate((100, 200, 300), 1)]
CFG = MonthConfig(
    month="2026-04", min_rtm_target_cents=rm(50000),
    level1_base_cents=rm(100000), level2_pct=Decimal("0.1"),
    level3_pct=Decimal("0.2"), level4_pct=Decimal("0.3"),
    sales_last_year_cents=rm(50000), sales_last_month_cents=rm(60000),
    salary_ratio_target=Decimal("0.15"), budget_headcount=5,
    turnover_threshold=Decimal("0.2"), stock_loss_target=Decimal("0.02"),
    opening_headcount_ft=5, part_timer_factor=Decimal("0.5"),
    salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000),
)


def expense(**changes):
    values = dict(
        incurred_date=date(2026, 4, 1), paid_date=date(2026, 4, 2),
        category_id=1, description="Synthetic line", amount_cents=rm(100),
        paid_by="Petty Cash", status="Paid", verification="Verified",
        recorded_by="Staff A", store_id=1,
    )
    values.update(changes)
    return ExpenseInput(**values)


class SyntheticSalesTests(unittest.TestCase):
    def test_days_mtd_and_complete_projection(self):
        self.assertEqual(kpi.days_with_sales_entered(DAYS), 3)
        self.assertEqual(kpi.sales_mtd_cents(DAYS), rm(600))
        self.assertEqual(kpi.month_end_sales_cents(DAYS, 3), Decimal(rm(600)))

    def test_partial_projection_and_empty_month(self):
        self.assertEqual(kpi.month_end_sales_cents(DAYS[:2], 4), Decimal(rm(600)))
        self.assertIsNone(kpi.month_end_sales_cents([], 30))

    def test_blank_is_not_entered_but_zero_is(self):
        days = [DailyInput(date(2026, 4, 1)),
                DailyInput(date(2026, 4, 2), sales_cents=0)]
        self.assertEqual(kpi.days_with_sales_entered(days), 1)
        self.assertEqual(kpi.sales_mtd_cents(days), 0)

    def test_target_levels_and_floor(self):
        targets = kpi.sales_target_levels_cents(CFG)
        self.assertEqual(targets["level1"], rm(100000))
        self.assertEqual(targets["level2"], Decimal(rm(110000)))
        self.assertEqual(targets["level4"], Decimal(rm(130000)))
        low = MonthConfig(month="2026-04", min_rtm_target_cents=rm(200000),
                          level1_base_cents=rm(100000), level4_pct=Decimal("0.1"))
        self.assertEqual(kpi.sales_target_levels_cents(low)["level4"],
                         Decimal(rm(200000)))

    def test_achievement_growth_and_basket_metrics(self):
        self.assertEqual(kpi.achievement(rm(600), rm(500)), Decimal("1.2"))
        self.assertEqual(kpi.growth(rm(600), rm(500)), Decimal("0.2"))
        self.assertEqual(kpi.spr_cents(rm(600), 3), Decimal(20000))
        self.assertEqual(kpi.upt(12, 3), Decimal(4))
        self.assertEqual(kpi.asp_cents(rm(600), 12), Decimal(5000))
        for value in (kpi.spr_cents(100, 0), kpi.upt(10, 0), kpi.asp_cents(100, 0)):
            self.assertIsNone(value)

    def test_run_rate_needed_and_status(self):
        self.assertEqual(kpi.required_daily_run_rate(rm(1000), 4), Decimal(25000))
        self.assertIsNone(kpi.sales_needed_per_remaining_day(rm(1000), rm(1000), 4, 4))
        self.assertEqual(kpi.sales_status(rm(600), rm(500)), "Achieved")
        self.assertEqual(kpi.sales_status(rm(400), rm(500)), "Below target")
        self.assertEqual(kpi.sales_status(None, rm(500)), "Awaiting data")
        self.assertEqual(kpi.sales_status(rm(400), None), "No target")


class SyntheticExpenseTests(unittest.TestCase):
    def test_paid_incurred_open_and_store_isolation(self):
        rows = [expense(), expense(amount_cents=rm(50), paid_date=None,
                                   status="Pending", paid_by=None,
                                   verification=None, recorded_by=None),
                expense(store_id=2, amount_cents=rm(900))]
        totals = kpi.expense_totals_cents(rows, "2026-04", store_id=1)
        self.assertEqual(totals, {"paid": rm(100), "incurred": rm(150), "open": rm(50)})

    def test_paid_uses_paid_month_and_incurred_uses_incurred_month(self):
        rows = [expense(incurred_date=date(2026, 3, 30), paid_date=date(2026, 4, 2))]
        self.assertEqual(kpi.expense_totals_cents(rows, "2026-04", 1)["paid"], rm(100))
        self.assertEqual(kpi.expense_totals_cents(rows, "2026-03", 1)["paid"], 0)
        self.assertEqual(kpi.expense_totals_cents(rows, "2026-03", 1)["incurred"], rm(100))

    def test_category_totals_cancelled_and_negative_amounts(self):
        rows = [expense(), expense(category_id=2, amount_cents=rm(200)),
                expense(amount_cents=rm(900), status="Cancelled")]
        self.assertEqual(kpi.category_paid_mtd_cents(rows, "2026-04", 1, 2), rm(200))
        self.assertEqual(kpi.expense_totals_cents(rows[2:], "2026-04", 1),
                         {"paid": 0, "incurred": 0, "open": 0})
        self.assertEqual(kpi.expense_totals_cents(
            [expense(amount_cents=-rm(20))], "2026-04", 1)["paid"], -rm(20))

    def test_validation_ladder(self):
        known = {1}
        cases = [
            (expense(), "OK"),
            (ExpenseInput(None, None, None, None, None, None, None, None, None), "Incomplete"),
            (expense(category_id=9), "Unknown category"),
            (expense(paid_date=None), "Missing paid date"),
            (expense(status="Pending"), "Check paid date"),
            (expense(paid_by=None), "Missing paid by"),
            (expense(recorded_by=None), "Missing recorder"),
            (expense(verification="Disputed"), "Disputed"),
            (expense(verification="Unverified"), "Unverified"),
            (ExpenseInput(None, None, 9, None, None, None, None, None, None), "Incomplete"),
        ]
        for row, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(kpi.expense_validation(row, known), expected)

    def test_pending_requires_recorder(self):
        row = expense(paid_date=None, paid_by=None, status="Pending",
                      verification=None, recorded_by=None)
        self.assertEqual(kpi.expense_validation(row, {1}), "Missing recorder")

    def test_budget_thresholds_and_expense_share(self):
        self.assertEqual(kpi.budget_status(0, None), "No budget")
        self.assertEqual(kpi.budget_status(rm(1001), rm(1000)), "OVER BUDGET")
        self.assertEqual(kpi.budget_status(rm(800), rm(1000)), "Watch")
        self.assertEqual(kpi.budget_status(rm(799), rm(1000)), "Within budget")
        self.assertEqual(kpi.expense_share_of_sales(rm(250), rm(1000)), Decimal("0.25"))


class SyntheticManpowerTests(unittest.TestCase):
    def test_headcount_rules(self):
        self.assertEqual(kpi.effective_staff(4, 2, Decimal("0.5")), Decimal(5))
        self.assertIsNone(kpi.effective_staff(None, None, Decimal("0.5")))
        self.assertEqual(kpi.effective_staff(0, None, Decimal("0.5")), Decimal(0))
        self.assertEqual(kpi.closing_headcount(5, 2, 1), 6)
        self.assertIsNone(kpi.closing_headcount(None, 2, 1))
        self.assertEqual(kpi.headcount_variance_status(5, 5), (0, "On budget"))
        self.assertEqual(kpi.headcount_variance_status(3, 5), (-2, "Vacancy"))
        self.assertEqual(kpi.headcount_variance_status(7, 5), (2, "Over budget"))
        self.assertEqual(kpi.headcount_variance_status(None, 5), (None, "Awaiting HC"))

    def test_salary_total_ratio_support_and_budget(self):
        self.assertEqual(kpi.total_salary_cents(CFG), rm(11000))
        self.assertEqual(kpi.salary_ratio(CFG, rm(100000)), Decimal("0.11"))
        supported = MonthConfig(**{**CFG.__dict__, "support_team_cost_cents": rm(1000)})
        self.assertEqual(kpi.salary_ratio(supported, rm(100000), include_support=True),
                         Decimal("0.12"))
        self.assertEqual(kpi.salary_ratio(CFG, rm(100000), include_support=True), Decimal("0.11"))
        self.assertEqual(kpi.salary_budget_cents(rm(100000), 0.15), Decimal(rm(15000)))

    def test_salary_statuses_and_turnover(self):
        awaiting = MonthConfig(month="2026-04", salary_ratio_target=Decimal("0.15"))
        self.assertIsNone(kpi.total_salary_cents(awaiting))
        self.assertEqual(kpi.salary_ratio_status(awaiting, rm(100000)), "Awaiting Payroll")
        self.assertEqual(kpi.salary_ratio_status(CFG, rm(100000)), "Within target")
        over = MonthConfig(**{**CFG.__dict__, "salary_ratio_target": Decimal("0.1")})
        self.assertEqual(kpi.salary_ratio_status(over, rm(100000)), "Failure: over target")
        self.assertEqual(kpi.turnover_pct(0, 5), 0)
        self.assertEqual(kpi.turnover_status(0, 5, Decimal("0.2")), "Normal")
        self.assertEqual(kpi.turnover_status(2, 5, Decimal("0.2")), "HIGH TURNOVER")
        self.assertEqual(kpi.turnover_status(0, None, Decimal("0.2")), "Awaiting HC")

    def test_average_staff_sales_per_staff_and_leave(self):
        days = [DailyInput(date(2026, 4, 1), sales_cents=rm(100), ft_working=4, pt_working=2),
                DailyInput(date(2026, 4, 2), sales_cents=rm(300), ft_working=6, pt_working=0),
                DailyInput(date(2026, 4, 3), sales_cents=rm(200), leave_annual=1,
                           leave_mc=2, leave_other_paid=1, leave_unpaid=8)]
        self.assertEqual(kpi.average_effective_staff(days, Decimal("0.5")), Decimal("5.5"))
        self.assertEqual(kpi.sales_per_staff_cents(days, Decimal("0.5")),
                         Decimal(rm(400)) / Decimal(11))
        self.assertEqual(kpi.leave_days(days), 12)


class SyntheticStockActionAndPrecisionTests(unittest.TestCase):
    def test_signed_losses_and_monthly_totals(self):
        self.assertEqual(kpi.total_loss_cents({"DMO": rm(100), "DPI": rm(900),
                                                "EXTRA": rm(20)}, SIGNS), rm(80))
        self.assertEqual(kpi.total_loss_cents({}, SIGNS), 0)
        days = [DailyInput(date(2026, 4, 1), stock_losses={"DMO": rm(30)}),
                DailyInput(date(2026, 4, 2), stock_losses={"DMO": rm(20), "DMC": rm(10)})]
        self.assertEqual(kpi.monthly_stock_totals(days), {"DMO": rm(50), "DMC": rm(10)})

    def test_loss_percentage_and_status(self):
        self.assertEqual(kpi.stock_loss_pct(rm(50), rm(1000)), Decimal("0.05"))
        self.assertEqual(kpi.stock_loss_status(rm(10), rm(1000), Decimal("0.02")), "Within target")
        self.assertEqual(kpi.stock_loss_status(rm(30), rm(1000), Decimal("0.02")), "Over target")
        self.assertEqual(kpi.stock_loss_status(rm(10), None, Decimal("0.02")), "")

    def test_action_counts_open_overdue_and_completion(self):
        actions = [ActionInput("Open", date(2026, 4, 1), "Sales Performance"),
                   ActionInput("Blocked", date(2026, 4, 30), "Stock Management"),
                   ActionInput("Done", date(2026, 4, 1), "General Operations"),
                   ActionInput("Cancelled", date(2026, 4, 1), "Expenses Management"),
                   ActionInput("Open", date(2026, 4, 1), None)]
        counts = kpi.action_counts(actions, as_of=date(2026, 4, 10))
        self.assertEqual((counts["total"], counts["open"], counts["done"],
                          counts["cancelled"], counts["overdue"]), (5, 2, 1, 1, 1))
        self.assertEqual(kpi.open_actions(counts), 3)
        self.assertEqual(kpi.completion_pct(counts), Decimal(1) / 5)
        self.assertIsNone(kpi.completion_pct(kpi.action_counts([], as_of=date(2026, 4, 10))))

    def test_overdue_boundary(self):
        action = [ActionInput("Open", date(2026, 4, 10), "Sales Performance")]
        self.assertEqual(kpi.action_counts(action, as_of=date(2026, 4, 10))["overdue"], 0)
        self.assertEqual(kpi.action_counts(action, as_of=date(2026, 4, 11))["overdue"], 1)

    def test_precision_money_and_month_helpers(self):
        self.assertEqual(kpi.to_decimal(0.06818), Decimal("0.06818"))
        self.assertEqual(kpi.to_decimal("0.5"), Decimal("0.5"))
        self.assertIsNone(kpi.to_decimal(None))
        self.assertEqual(kpi.cents_to_rm(1250), Decimal("12.50"))
        self.assertIsNone(kpi.cents_to_rm(None))
        self.assertEqual(kpi.month_bounds("2026-02"),
                         (date(2026, 2, 1), date(2026, 2, 28), 28))
        self.assertEqual(kpi.month_bounds("2024-02")[2], 29)
        cents = [DailyInput(date(2026, 4, 1), sales_cents=10),
                 DailyInput(date(2026, 4, 2), sales_cents=20)]
        self.assertEqual(kpi.sales_mtd_cents(cents), 30)


if __name__ == "__main__":
    unittest.main()
