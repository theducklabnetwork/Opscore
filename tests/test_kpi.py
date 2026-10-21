"""Unit + Excel-parity tests for ducklab/kpi.py.

Parity expectations come from cached values in Store_Operations_Monitoring_v4.xlsx
(September 2026, store SLP/ZI68). Workbook money is whole RM; test data is
converted to cents at the boundary. Run: python -m unittest tests.test_kpi -v
"""

import unittest
from datetime import date
from decimal import Decimal

from ducklab import kpi
from ducklab.kpi import (
    ActionInput, DailyInput, ExpenseInput, MonthConfig,
)

SIGNS = {  # mirrors stock_loss_codes.contribution_sign
    "DMO": 1, "DMC": 1, "DMP": 1, "2DMP": 0, "DPI": 0, "DMW": 0, "DDR": 0,
    "DMN": 0, "DME": 0, "DMQ": 0, "DST": 0, "MISSING": 1, "EXTRA": -1,
}


def rm(v) -> int:
    """RM -> cents."""
    return int(Decimal(str(v)) * 100)


# September 2026 workbook daily sales (Daily!C7:C36, whole RM).
WB_SALES_RM = [9681, 10511, 11478, 13309, 15022, 13256, 10901, 10228, 10903,
               11480, 11513, 15236, 14712, 9256, 10467, 11018, 10396, 11298,
               14494, 12701, 10875, 9589, 9627, 10726, 11724, 15098, 14818,
               9856, 9792, 10854]
WB_DAYS = [DailyInput(date(2026, 9, i + 1), sales_cents=rm(v))
           for i, v in enumerate(WB_SALES_RM)]

# Workbook Expense Register rows 9-26 (incurred serials converted to dates).
def _d(serial: int) -> date:
    return date(1899, 12, 30).toordinal() and date.fromordinal(serial - 693594 + date(1900, 1, 1).toordinal() - 1)


# Build register rows directly with real dates (serials 46266 = 2026-09-01).
from datetime import timedelta
BASE = date(2026, 9, 1)
def sd(n):  # serial offset: serial 46266 -> 2026-09-01
    return BASE + timedelta(days=n - 46266)

CATS = {"Consumables": 1, "Store Supplies": 2, "Maintenance": 3,
        "Electricity": 4, "Water": 5, "Telecom": 6}
WB_EXPENSES = [
    # incurred, paid, category, desc, amount RM, paid_by, status, verification, recorded_by
    ExpenseInput(sd(46266), sd(46267), 1, "Cleaning materials", rm(85), "Petty Cash", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46267), sd(46267), 2, "Print paper and stationery", rm(65), "Petty Cash", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46268), sd(46270), 3, "Lighting replacement", rm(180), "HQ / Central", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46269), sd(46275), 4, "Monthly electricity bill", rm(2850), "HQ / Central", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46270), sd(46270), 1, "Garbage bags and tissue", rm(75), "Petty Cash", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46272), sd(46273), 2, "Price label rolls", rm(95), "Store Account", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46274), sd(46277), 5, "Monthly water bills", rm(145), "HQ / Central", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46275), sd(46275), 3, "Shutter door repair", rm(120), "Petty Cash", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46277), sd(46280), 6, "Internet and telephone", rm(189), "HQ / Central", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46279), sd(46279), 1, "Cleaning detergent", rm(65), "Petty Cash", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46281), sd(46283), 2, "Barcode label stickers", rm(110), "Store Account", "Paid", "Verified", "MP", store_id=1),
    ExpenseInput(sd(46283), sd(46285), 3, "Air-conditioner servicing", rm(350), "HQ / Central", "Paid", "Verified", "MP", store_id=1),
    ExpenseInput(sd(46285), sd(46287), 3, "Monthly pest treatment", rm(180), "HQ / Central", "Paid", "Verified", "MP", store_id=1),
    ExpenseInput(sd(46287), sd(46287), 1, "Toilet supplies", rm(90), "Petty Cash", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46289), sd(46290), 2, "Packaging materials", rm(125), "Store Account", "Paid", "Verified", "ABM", store_id=1),
    ExpenseInput(sd(46291), None, 3, "Electrical socket repair", rm(280), "HQ / Central", "Pending", None, None, store_id=1),
    ExpenseInput(sd(46293), sd(46294), 1, "Cleaning equipment", rm(95), "Petty Cash", "Paid", "Verified", "BM", store_id=1),
    ExpenseInput(sd(46295), None, 3, "Shelf bracket equipment", rm(160), "HQ / Central", "Pending", None, None, store_id=1),
]

# Workbook stock-loss monthly totals (Summary B58-B72, whole RM).
WB_STOCK_RM = {"DMO": 295, "DMC": 420, "DMP": 275, "2DMP": 295, "DPI": 355,
               "DMW": 600, "DDR": 685, "DMN": 255, "DME": 365, "DMQ": 230,
               "DST": 520, "MISSING": 705, "EXTRA": 100}

WB_CFG = MonthConfig(
    month="2026-09",
    min_rtm_target_cents=rm(200000),
    level1_base_cents=rm(269861),
    level2_pct=Decimal("0.1"), level3_pct=Decimal("0.2"), level4_pct=Decimal("0.3"),
    sales_last_year_cents=rm(271940), sales_last_month_cents=rm(197003),
    salary_ratio_target=Decimal("0.06818"),
    budget_headcount=9, turnover_threshold=Decimal("0.45"),
    stock_loss_target=Decimal("0.015"),
    opening_headcount_ft=9, part_timer_factor=Decimal("0.5"),
    salary_basic_allowance_cents=rm("13275.75"), overtime_cents=rm("4198.50"),
    support_team_cost_cents=None,
)


class TestSalesParity(unittest.TestCase):
    def test_days_entered(self):  # Summary!B14 = 30
        self.assertEqual(kpi.days_with_sales_entered(WB_DAYS), 30)

    def test_sales_mtd(self):  # Summary!B15 = 350819
        self.assertEqual(kpi.sales_mtd_cents(WB_DAYS), rm(350819))

    def test_month_complete_actual(self):  # Summary!B16 = 350819
        self.assertEqual(kpi.month_end_sales_cents(WB_DAYS, 30), rm(350819))

    def test_partial_month_projection(self):
        half = WB_DAYS[:15]
        expected = Decimal(kpi.sales_mtd_cents(half)) / 15 * 30
        self.assertEqual(kpi.month_end_sales_cents(half, 30), expected)
        # Complete month returns exact integer MTD.
        self.assertEqual(kpi.month_end_sales_cents(WB_DAYS, 30), Decimal(rm(350819)))

    def test_no_days_entered(self):
        self.assertIsNone(kpi.month_end_sales_cents([], 30))

    def test_blank_not_counted_but_zero_is(self):
        days = [DailyInput(date(2026, 9, 1), sales_cents=None),
                DailyInput(date(2026, 9, 2), sales_cents=0)]
        self.assertEqual(kpi.days_with_sales_entered(days), 1)
        self.assertEqual(kpi.sales_mtd_cents(days), 0)

    def test_target_levels(self):  # Setup!B21-B24
        lv = kpi.sales_target_levels_cents(WB_CFG)
        self.assertEqual(lv["level1"], rm(269861))
        self.assertEqual(lv["level2"], Decimal(rm(269861)) * Decimal("1.1"))
        self.assertEqual(lv["level4"], Decimal(rm(269861)) * Decimal("1.3"))
        # Floor rule: if base*1.3 < min RTM target, floor wins.
        cfg2 = MonthConfig(month="2026-09", min_rtm_target_cents=rm(200000),
                           level1_base_cents=rm(100000), level4_pct=Decimal("0.3"))
        self.assertEqual(kpi.sales_target_levels_cents(cfg2)["level4"], Decimal(rm(200000)))

    def test_achievement_l1(self):  # Summary!D17 = 1.29999888831658
        a = kpi.achievement(rm(350819), rm(269861))
        self.assertAlmostEqual(float(a), 1.29999888831658, places=10)

    def test_growth_last_year(self):  # Summary!D21 = 0.290060307420755
        g = kpi.growth(rm(350819), rm(271940))
        self.assertAlmostEqual(float(g), 0.290060307420755, places=10)

    def test_growth_last_month(self):  # Summary!D22 = 0.780779988122008
        g = kpi.growth(rm(350819), rm(197003))
        self.assertAlmostEqual(float(g), 0.780779988122008, places=10)

    def test_spr_upt_asp(self):  # Summary!B25-B27
        tx, units, sales = 11592, 45902, rm(350819)
        self.assertAlmostEqual(float(kpi.spr_cents(sales, tx)), 3026.388888888889, places=6)
        self.assertAlmostEqual(float(kpi.upt(units, tx)), 3.95979986197378, places=10)
        self.assertAlmostEqual(float(kpi.asp_cents(sales, units)), 764.2782449566467, places=6)

    def test_zero_denominators(self):
        self.assertIsNone(kpi.spr_cents(100, 0))
        self.assertIsNone(kpi.upt(10, 0))
        self.assertIsNone(kpi.asp_cents(100, 0))

    def test_run_rate(self):  # Daily Dashboard!E8 = 8995.366...
        rr = kpi.required_daily_run_rate(rm(269861), 30)
        self.assertAlmostEqual(float(rr) / 100, 8995.36666666667, places=6)

    def test_needed_per_remaining_day_complete_month(self):
        self.assertIsNone(kpi.sales_needed_per_remaining_day(rm(269861), rm(350819), 30, 30))

    def test_sales_status(self):
        self.assertEqual(kpi.sales_status(rm(350819), rm(269861)), "Achieved")
        self.assertEqual(kpi.sales_status(rm(100), rm(269861)), "Below target")
        self.assertEqual(kpi.sales_status(None, rm(269861)), "Awaiting data")
        self.assertEqual(kpi.sales_status(rm(100), None), "No target")


class TestExpensesParity(unittest.TestCase):
    def test_paid_incurred_open(self):
        # Expense Monitor!B8=4819, E8=5259, H8=440
        t = kpi.expense_totals_cents(WB_EXPENSES, "2026-09", store_id=1)
        self.assertEqual(t["paid"], rm(4819))
        self.assertEqual(t["incurred"], rm(5259))
        self.assertEqual(t["open"], rm(440))

    def test_store_isolation(self):
        other = [ExpenseInput(sd(46266), sd(46267), 1, "x", rm(999), "Petty Cash",
                              "Paid", "Verified", "ABM", store_id=2)]
        t = kpi.expense_totals_cents(WB_EXPENSES + other, "2026-09", store_id=1)
        self.assertEqual(t["paid"], rm(4819))

    def test_category_paid_mtd(self):
        # Expense Monitor!C15 Electricity = 2850; C22 Maintenance = 830
        self.assertEqual(kpi.category_paid_mtd_cents(WB_EXPENSES, "2026-09", 1, 4), rm(2850))
        self.assertEqual(kpi.category_paid_mtd_cents(WB_EXPENSES, "2026-09", 1, 3), rm(830))

    def test_cross_month_paid_date_basis(self):
        e = [ExpenseInput(date(2026, 8, 28), date(2026, 9, 3), 1, "x", rm(100),
                          "Petty Cash", "Paid", "Verified", "ABM", store_id=1)]
        self.assertEqual(kpi.expense_totals_cents(e, "2026-09", 1)["paid"], rm(100))
        self.assertEqual(kpi.expense_totals_cents(e, "2026-08", 1)["paid"], 0)
        self.assertEqual(kpi.expense_totals_cents(e, "2026-08", 1)["incurred"], rm(100))

    def test_cancelled_not_incurred(self):
        e = [ExpenseInput(date(2026, 9, 1), None, 1, "x", rm(50),
                          None, "Cancelled", None, "ABM", store_id=1)]
        t = kpi.expense_totals_cents(e, "2026-09", 1)
        self.assertEqual(t, {"paid": 0, "incurred": 0, "open": 0})

    def test_validation_ladder(self):
        known = {1}
        base = dict(incurred_date=date(2026, 9, 1), paid_date=date(2026, 9, 2),
                    category_id=1, description="x", amount_cents=100,
                    paid_by="Petty Cash", status="Paid", verification="Verified",
                    recorded_by="ABM")
        ok = ExpenseInput(**base)
        self.assertEqual(kpi.expense_validation(ok, known), "OK")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(None, None, None, None, None, None, None, None, None), known), "Incomplete")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(**{**base, "category_id": 99}), known), "Unknown category")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(**{**base, "paid_date": None}), known), "Missing paid date")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(**{**base, "status": "Pending"}), known), "Check paid date")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(**{**base, "paid_by": None}), known), "Missing paid by")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(**{**base, "recorded_by": None}), known), "Missing recorder")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(**{**base, "verification": "Disputed"}), known), "Disputed")
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(**{**base, "verification": "Unverified"}), known), "Unverified")
        # Precedence: incomplete beats unknown category.
        self.assertEqual(kpi.expense_validation(
            ExpenseInput(None, None, 99, None, None, None, None, None, None), known),
            "Incomplete")

    def test_pending_workbook_rows_flagged_missing_recorder(self):
        pendings = [e for e in WB_EXPENSES if e.status == "Pending"]
        self.assertEqual(len(pendings), 2)
        for e in pendings:
            self.assertEqual(kpi.expense_validation(e, set(CATS.values())),
                             "Missing recorder")

    def test_budget_status_thresholds(self):
        self.assertEqual(kpi.budget_status(0, None), "No budget")
        self.assertEqual(kpi.budget_status(rm(3201), rm(3200)), "OVER BUDGET")
        self.assertEqual(kpi.budget_status(rm(2560), rm(3200)), "Watch")   # exactly 80%
        self.assertEqual(kpi.budget_status(rm(2559), rm(3200)), "Within budget")
        # Workbook: Electricity 2850/3200 -> Watch
        self.assertEqual(kpi.budget_status(rm(2850), rm(3200)), "Watch")

    def test_expense_share_of_sales(self):  # Summary!D40 = 0.0137364281866148
        s = kpi.expense_share_of_sales(rm(4819), rm(350819))
        self.assertAlmostEqual(float(s), 0.0137364281866148, places=10)


class TestManpower(unittest.TestCase):
    def test_effective_staff(self):
        self.assertEqual(kpi.effective_staff(7, 1, Decimal("0.5")), Decimal("7.5"))
        self.assertIsNone(kpi.effective_staff(None, None, Decimal("0.5")))
        self.assertEqual(kpi.effective_staff(0, None, Decimal("0.5")), Decimal(0))

    def test_closing_headcount(self):
        self.assertEqual(kpi.closing_headcount(9, 2, 1), 10)
        self.assertIsNone(kpi.closing_headcount(None, 2, 1))

    def test_headcount_variance(self):
        self.assertEqual(kpi.headcount_variance_status(9, 9), (0, "On budget"))
        self.assertEqual(kpi.headcount_variance_status(7, 9), (-2, "Vacancy"))
        self.assertEqual(kpi.headcount_variance_status(11, 9), (2, "Over budget"))
        self.assertEqual(kpi.headcount_variance_status(None, 9), (None, "Awaiting HC"))

    def test_salary_total_and_ratio(self):
        # Summary!B43 = 17474.25; B44/B45 = 0.0498098734675146
        self.assertEqual(kpi.total_salary_cents(WB_CFG), rm("17474.25"))
        r = kpi.salary_ratio(WB_CFG, rm(350819))
        self.assertAlmostEqual(float(r), 0.0498098734675146, places=10)
        r2 = kpi.salary_ratio(WB_CFG, rm(350819), include_support=True)
        self.assertAlmostEqual(float(r2), 0.0498098734675146, places=10)  # support None -> same

    def test_awaiting_payroll(self):
        cfg = MonthConfig(month="2026-09", salary_ratio_target=Decimal("0.06818"))
        self.assertIsNone(kpi.total_salary_cents(cfg))
        self.assertEqual(kpi.salary_ratio_status(cfg, rm(350819)), "Awaiting payroll")
        self.assertEqual(kpi.salary_ratio_status(WB_CFG, rm(350819)), "Within target")
        over = MonthConfig(month="2026-09", salary_basic_allowance_cents=rm(50000),
                           salary_ratio_target=Decimal("0.06818"))
        self.assertEqual(kpi.salary_ratio_status(over, rm(350819)), "Failure: over target")

    def test_salary_budget(self):  # Setup!B43 = 23918.83942 RM
        b = kpi.salary_budget_cents(rm(350819), 0.06818)  # REAL float input
        self.assertEqual(b, Decimal("2391883.942"))

    def test_turnover(self):
        self.assertEqual(kpi.turnover_pct(0, 9), 0)
        self.assertEqual(kpi.turnover_status(0, 9, Decimal("0.45")), "Normal")
        self.assertEqual(kpi.turnover_status(5, 9, Decimal("0.45")), "HIGH TURNOVER")
        self.assertEqual(kpi.turnover_status(0, None, Decimal("0.45")), "Awaiting HC")

    def test_avg_staff_and_sales_per_staff(self):
        days = [DailyInput(date(2026, 9, 1), sales_cents=rm(100), ft_working=7, pt_working=1),
                DailyInput(date(2026, 9, 2), sales_cents=rm(300), ft_working=9, pt_working=0),
                DailyInput(date(2026, 9, 3), sales_cents=rm(200))]  # staffing missing
        avg = kpi.average_effective_staff(days, Decimal("0.5"))
        self.assertEqual(avg, (Decimal("7.5") + Decimal(9)) / 2)
        sps = kpi.sales_per_staff_cents(days, Decimal("0.5"))
        self.assertEqual(sps, Decimal(rm(400)) / Decimal("16.5"))

    def test_leave_days(self):
        days = [DailyInput(date(2026, 9, 1), leave_annual=1, leave_mc=2,
                           leave_other_paid=None, leave_unpaid=0)]
        self.assertEqual(kpi.leave_days(days), 3)


class TestStock(unittest.TestCase):
    def test_total_loss_workbook(self):
        # Summary!B63 = 1595
        losses = {k: rm(v) for k, v in WB_STOCK_RM.items()}
        self.assertEqual(kpi.total_loss_cents(losses, SIGNS), rm(1595))

    def test_missing_codes_mean_zero(self):
        self.assertEqual(kpi.total_loss_cents({}, SIGNS), 0)
        self.assertEqual(kpi.total_loss_cents({"DMO": rm(10)}, SIGNS), rm(10))

    def test_informational_excluded_and_extra_negative(self):
        losses = {"DPI": rm(1000), "EXTRA": rm(50), "DMP": rm(100)}
        self.assertEqual(kpi.total_loss_cents(losses, SIGNS), rm(50))

    def test_monthly_totals(self):
        days = [DailyInput(date(2026, 9, 1), stock_losses={"DMO": rm(70)}),
                DailyInput(date(2026, 9, 2), stock_losses={"DMO": rm(30), "DMC": rm(5)})]
        self.assertEqual(kpi.monthly_stock_totals(days), {"DMO": rm(100), "DMC": rm(5)})

    def test_loss_pct_and_status(self):
        # Summary!D63 = 0.00454650403769465, E63 = Within target
        p = kpi.stock_loss_pct(rm(1595), rm(350819))
        self.assertAlmostEqual(float(p), 0.00454650403769465, places=10)
        self.assertEqual(kpi.stock_loss_status(rm(1595), rm(350819), Decimal("0.015")),
                         "Within target")
        self.assertEqual(kpi.stock_loss_status(rm(6000), rm(350819), Decimal("0.015")),
                         "Over target")
        self.assertEqual(kpi.stock_loss_status(rm(1595), None, Decimal("0.015")), "")


class TestActions(unittest.TestCase):
    def setUp(self):
        self.actions = [
            ActionInput("Open", date(2026, 9, 1), "Sales Performance"),
            ActionInput("In progress", date(2026, 9, 10), "Stock Management"),
            ActionInput("Blocked", date(2026, 10, 1), "General Operations"),
            ActionInput("Done", date(2026, 8, 1), "Expenses Management"),
            ActionInput("Cancelled", date(2026, 8, 1), "Manpower Efficiency"),
            ActionInput("Open", None, None),  # no due date / no pillar
        ]

    def test_counts(self):
        c = kpi.action_counts(self.actions, as_of=date(2026, 9, 15))
        self.assertEqual(c["total"], 6)
        self.assertEqual(c["open"], 2)
        self.assertEqual(c["done"], 1)
        self.assertEqual(c["cancelled"], 1)
        # Overdue: row1 (due 9/1 < 9/15, open, has pillar) and row2 (9/10).
        # Blocked row due 10/1 not yet; Done/Cancelled excluded; no-pillar excluded.
        self.assertEqual(c["overdue"], 2)
        self.assertEqual(kpi.open_actions(c), 4)

    def test_overdue_boundary(self):
        a = [ActionInput("Open", date(2026, 9, 15), "Sales Performance")]
        self.assertEqual(kpi.action_counts(a, as_of=date(2026, 9, 15))["overdue"], 0)
        self.assertEqual(kpi.action_counts(a, as_of=date(2026, 9, 16))["overdue"], 1)

    def test_completion_pct(self):
        c = kpi.action_counts(self.actions, as_of=date(2026, 9, 15))
        self.assertEqual(kpi.completion_pct(c), Decimal(1) / 6)
        self.assertIsNone(kpi.completion_pct(kpi.action_counts([], as_of=date(2026, 9, 15))))


class TestPrecisionAndHelpers(unittest.TestCase):
    def test_to_decimal_float_boundary(self):
        self.assertEqual(kpi.to_decimal(0.06818), Decimal("0.06818"))
        self.assertIsNone(kpi.to_decimal(None))
        self.assertEqual(kpi.to_decimal("0.5"), Decimal("0.5"))

    def test_cents_to_rm(self):
        self.assertEqual(kpi.cents_to_rm(1250), Decimal("12.50"))
        self.assertIsNone(kpi.cents_to_rm(None))

    def test_no_float_currency_drift(self):
        # 0.1 + 0.2 style drift must not occur on money.
        days = [DailyInput(date(2026, 9, 1), sales_cents=10),
                DailyInput(date(2026, 9, 2), sales_cents=20)]
        self.assertEqual(kpi.sales_mtd_cents(days), 30)

    def test_negative_amounts_preserved(self):
        e = [ExpenseInput(date(2026, 9, 1), date(2026, 9, 1), 1, "cash over",
                          -rm(20), "Petty Cash", "Paid", "Verified", "ABM", store_id=1)]
        self.assertEqual(kpi.expense_totals_cents(e, "2026-09", 1)["paid"], -rm(20))

    def test_month_bounds(self):
        self.assertEqual(kpi.month_bounds("2026-02"), (date(2026, 2, 1), date(2026, 2, 28), 28))
        self.assertEqual(kpi.month_bounds("2024-02")[2], 29)
        self.assertEqual(kpi.month_bounds("2026-09")[2], 30)


if __name__ == "__main__":
    unittest.main()
