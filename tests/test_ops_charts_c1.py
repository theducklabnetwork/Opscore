"""C1 tests: Daily Sales vs Target, Sales by Day, Sales Calendar, Expense
Mix and the Basket State Average. Expected values are hand-computed
literals from the synthetic T1 July case (1 Jul 2026 = Wednesday) — no
company figures. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_charts_c1.py -v
"""

import unittest
from datetime import date
from decimal import Decimal
from unittest import mock

from ducklab import kpi, ops_charts, store_ops_db
from ducklab.kpi import DailyInput
from tests.test_daily_targets_t1 import SALES_RM, Fixture, rm

D = Decimal


# 1 ---------------------------------------------------------------------------
class KpiTests(unittest.TestCase):
    def test_average_sales_by_weekday(self):
        days = [
            DailyInput(date(2026, 7, 1), sales_cents=rm(8800)),   # Wed
            DailyInput(date(2026, 7, 8), sales_cents=0),           # Wed, zero
            DailyInput(date(2026, 7, 15)),                         # unreported
            DailyInput(date(2026, 7, 6), sales_cents=rm(8700)),   # Mon, PH
            DailyInput(date(2026, 7, 13), sales_cents=rm(9000)),  # Mon
        ]
        self.assertEqual(
            kpi.average_sales_by_weekday(days, {date(2026, 7, 6)}),
            [D(900000), None, D(440000), None, None, None, None])

    def test_average_sales_by_weekday_none_reported(self):
        self.assertEqual(kpi.average_sales_by_weekday(
            [DailyInput(date(2026, 7, 1))], set()), [None] * 7)

    def test_month_weeks(self):
        weeks = kpi.month_weeks("2026-07")       # Wednesday start, 31 days
        self.assertEqual(len(weeks), 5)
        self.assertEqual(weeks[0], [None, None] + [date(2026, 7, n)
                                                   for n in range(1, 6)])
        self.assertEqual(weeks[4], [date(2026, 7, n)
                                    for n in range(27, 32)] + [None, None])
        feb = kpi.month_weeks("2021-02")         # Monday start, 28 days
        self.assertEqual(len(feb), 4)
        self.assertEqual(feb[0][0], date(2021, 2, 1))
        self.assertEqual(feb[3][6], date(2021, 2, 28))

    def test_expense_mix(self):
        self.assertEqual(
            kpi.expense_mix([("Water", 0), ("Rent", rm(3000)),
                             ("Electricity", rm(450)), ("Misc", rm(450))]),
            [("Rent", 300000), ("Electricity", 45000), ("Misc", 45000)])
        self.assertEqual(kpi.expense_mix([("Water", 0)]), [])
        items = [(f"C{n}", rm(n)) for n in range(1, 9)]   # 8 categories
        self.assertEqual(
            kpi.expense_mix(items),
            [("C8", 800), ("C7", 700), ("C6", 600), ("C5", 500),
             ("C4", 400), ("C3", 300), ("Other", 300)])

    def test_benchmark_spr_cents(self):
        self.assertEqual(kpi.benchmark_spr_cents(21.5), D(2150))
        self.assertIsNone(kpi.benchmark_spr_cents(None))
        # difference vs the State Average: 22.89 / 25.00 - 1
        self.assertEqual(kpi.pct_vs_target(D(2289), kpi.benchmark_spr_cents(
            25.0)), D("-0.0844"))


# 2 ---------------------------------------------------------------------------
class ChartBuilderTests(unittest.TestCase):
    def test_daily_vs_target_bars_and_gaps(self):
        fig = ops_charts.daily_vs_target(
            [1, 2, 3], [rm(100), None, rm(50)], [rm(90), None, rm(60)],
            ["Achieved", None, "Not Achieved"], dark=True)
        t = {x["name"]: x for x in fig.to_plotly_json()["data"]}
        self.assertEqual(list(t["Achieved"]["y"]), [100.0, None, None])
        self.assertEqual(list(t["Not Achieved"]["y"]), [None, None, 50.0])
        self.assertEqual(t["Achieved"]["marker"]["color"], "#3DD68C")
        self.assertEqual(t["Not Achieved"]["marker"]["color"], "#E5484D")
        line = t["Adjusted Target"]
        self.assertEqual(list(line["y"]), [90.0, None, 60.0])
        self.assertFalse(line["connectgaps"])

    def test_daily_vs_target_skips_empty_result(self):
        fig = ops_charts.daily_vs_target([1], [rm(100)], [rm(90)],
                                         ["Achieved"], dark=False)
        names = [x["name"] for x in fig.to_plotly_json()["data"]]
        self.assertEqual(names, ["Achieved", "Adjusted Target"])

    def test_weekday_bars(self):
        fig = ops_charts.weekday_bars(kpi.DSC_WEEKDAYS,
                                      [D(900000), None] + [None] * 5,
                                      dark=True)
        bar = fig.to_plotly_json()["data"][0]
        self.assertEqual(list(bar["x"]), list(kpi.DSC_WEEKDAYS))
        self.assertEqual(list(bar["y"])[:2], [9000.0, None])

    def test_sales_calendar_blank_unreported(self):
        weeks = [[None, (1, "Wed 01 Jul", rm(80)), (2, "Thu 02 Jul", None),
                  (3, "Fri 03 Jul", 0), None, None, None]]
        fig = ops_charts.sales_calendar(weeks, dark=True).to_plotly_json()
        hm = fig["data"][0]
        self.assertEqual(list(hm["z"][0]),
                         [None, 80.0, None, 0.0, None, None, None])
        self.assertFalse(hm["hoverongaps"])
        self.assertEqual(hm["customdata"][0][1],
                         "Wed 01 Jul<br>Sales: RM 80.00")
        self.assertEqual(hm["customdata"][0][2], "")       # unreported
        # a day number on every day of the month (reported or not)
        self.assertEqual([a["text"] for a in fig["layout"]["annotations"]],
                         ["1", "2", "3"])

    def test_expense_donut_other_is_gray(self):
        fig = ops_charts.expense_donut(
            [("Rent", rm(300)), ("Other", rm(20))], dark=False)
        pie = fig.to_plotly_json()["data"][0]
        self.assertEqual(list(pie["values"]), [300.0, 20.0])
        self.assertEqual(list(pie["marker"]["colors"]),
                         ["#2A78D6", "#8B8D98"])
        self.assertEqual(list(pie["customdata"]), ["RM 300.00", "RM 20.00"])

    def test_dark_and_light_same_data(self):
        args = ([1, 2], [rm(10), rm(20)], [rm(15), rm(15)],
                ["Not Achieved", "Achieved"])
        for build in (
                lambda dark: ops_charts.daily_vs_target(*args, dark=dark),
                lambda dark: ops_charts.weekday_bars(
                    kpi.DSC_WEEKDAYS, [D(100)] * 7, dark=dark),
                lambda dark: ops_charts.sales_calendar(
                    [[(1, "x", 100)] + [None] * 6], dark=dark),
                lambda dark: ops_charts.expense_donut([("A", 100)],
                                                      dark=dark)):
            d, l = (build(True).to_plotly_json(),
                    build(False).to_plotly_json())
            for td, tl in zip(d["data"], l["data"]):
                for key in ("x", "y", "z", "values"):
                    self.assertEqual(td.get(key), tl.get(key))
            self.assertNotEqual(d["layout"]["font"]["color"],
                                l["layout"]["font"]["color"])


# 3 ---------------------------------------------------------------------------
class SalesChartsStateTests(Fixture):
    def configure(self):
        self.app.configure_month_with_budgets(
            self.admin, self.sid, "2026-07",
            {"level1_base_cents": rm(300000), "level2_pct": 0.1,
             "level3_pct": 0.2, "level4_pct": 0.3}, {}, None)

    def july(self, benchmark=True, shares=True):
        if shares:
            self.insert_shares()
        if benchmark:
            conn = store_ops_db.connect(self.db)
            with conn:
                conn.execute("INSERT INTO basket_benchmarks VALUES "
                             "('Mall', 'Johor', 7, 25.0, 3.2)")
                # another month / state: never picked up
                conn.execute("INSERT INTO basket_benchmarks VALUES "
                             "('Mall', 'Johor', 8, 99.0, 9.0)")
            conn.close()
        self.configure()
        for n, v in enumerate(SALES_RM, 1):
            self.app.record_daily(
                self.admin, self.sid, date(2026, 7, n), sales_cents=rm(v),
                transactions=400, units=1200)

    def sales(self):
        import ducklab.ops_sales_state as mod
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        self.bound(mod, SalesState, st.load_sales_data)
        self.assertEqual(st.sp_error, "")
        return st

    def test_state_average_and_difference(self):
        self.july()
        st = self.sales()
        # SPR 60,900 / 2,400 = RM 25.375 vs RM 25.00 -> +1.5%;
        # IPR 7,200 / 2,400 = 3.00 vs 3.20 -> -6.25% -> -6.3%
        self.assertEqual((st.sp_spr_state, st.sp_spr_state_pct,
                          st.sp_spr_state_neg),
                         ("RM 25.00", "+1.5%", False))
        self.assertEqual((st.sp_upt, st.sp_ipr_state, st.sp_ipr_state_pct,
                          st.sp_ipr_state_neg),
                         ("3.00", "3.20", "−6.3%", True))

    def test_state_average_dash_without_benchmark(self):
        self.july(benchmark=False)
        st = self.sales()
        self.assertEqual((st.sp_spr_state, st.sp_spr_state_pct,
                          st.sp_ipr_state, st.sp_ipr_state_pct),
                         ("—", "—", "—", "—"))

    def test_daily_vs_target_figure(self):
        self.july()
        st = self.sales()
        self.assertTrue(st.sp_dvt_ready)
        t = {x["name"]: x for x in st.sp_dvt_dark.to_plotly_json()["data"]}
        self.assertEqual(list(t["Achieved"]["y"])[:7],
                         [None, None, 10000.0, 12000.0, 14000.0, None, None])
        self.assertEqual(list(t["Not Achieved"]["y"])[:7],
                         [8800.0, 7400.0, None, None, None, 8700.0, None])
        # Adjusted Target on reported days only (the table's figures)
        adj = list(t["Adjusted Target"]["y"])
        self.assertEqual(adj[:6], [8827.93, 7808.48, 9685.29, 11670.82,
                                   13316.71, 8977.56])
        self.assertTrue(all(v is None for v in adj[6:]))
        self.assertEqual(len(adj), 31)

    def test_daily_vs_target_hidden_without_targets(self):
        self.july(shares=False)
        st = self.sales()
        self.assertFalse(st.sp_dvt_ready)
        self.assertTrue(st.sp_weekday_ready and st.sp_cal_ready)

    def test_sales_by_day_and_calendar(self):
        self.july()
        st = self.sales()
        bar = st.sp_weekday_light.to_plotly_json()["data"][0]
        # Mon 6, Wed 1, Thu 2, Fri 3, Sat 4, Sun 5 Jul; no Tuesday
        self.assertEqual(list(bar["y"]), [8700.0, None, 8800.0, 7400.0,
                                          10000.0, 12000.0, 14000.0])
        hm = st.sp_cal_dark.to_plotly_json()["data"][0]
        self.assertEqual(list(hm["z"][0]), [None, None, 8800.0, 7400.0,
                                            10000.0, 12000.0, 14000.0])
        self.assertEqual(list(hm["z"][1]), [8700.0] + [None] * 6)
        self.assertEqual(hm["customdata"][1][0],
                         "Mon 06 Jul<br>Sales: RM 8,700.00")

    def test_ph_left_out_of_sales_by_day(self):
        self.july()
        self.app.configure_month_with_budgets(
            self.admin, self.sid, "2026-07", {}, {}, [date(2026, 7, 6)])
        bar = self.sales().sp_weekday_dark.to_plotly_json()["data"][0]
        self.assertIsNone(list(bar["y"])[0])          # 6 Jul (Mon) is PH

    def test_no_sales_no_charts(self):
        self.insert_shares()
        self.configure()
        st = self.sales()
        self.assertFalse(st.sp_dvt_ready or st.sp_weekday_ready
                         or st.sp_cal_ready)

    def test_page_sections(self):
        from ducklab import ops_sales_pages as p
        page = str(p.sales_page())
        for text in ("Daily Sales vs Target", "Sales by Day",
                     "Sales Calendar", "State Average", "dvt-section",
                     "weekday-section", "calendar-section",
                     "basket-spr-state", "basket-ipr-state"):
            self.assertIn(text, page)


# 4 ---------------------------------------------------------------------------
class ExpenseMixStateTests(Fixture):
    def expenses(self, month="2026-10"):
        import ducklab.ops_expenses_state as mod
        from ducklab.ops_expenses_state import ExpensesState
        st = ExpensesState()
        st.selected_store_active = True
        self.bound(mod, ExpensesState, st.load_expenses_data, month=month)
        return st

    def setUp(self):
        super().setUp()
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(200000), level2_pct=0.1,
                                 level3_pct=0.2, level4_pct=0.3)
        self.rent = self.app.add_expense_category(
            self.admin, self.sid, "Rent", sort_order=1)
        self.elec = self.app.add_expense_category(
            self.admin, self.sid, "Electricity", sort_order=2)

    def pay(self, cat, amount, status="Paid", paid=date(2026, 10, 3)):
        self.app.record_expense(
            self.admin, self.sid, incurred_date=date(2026, 10, 2),
            category_id=cat, description="x", amount_cents=rm(amount),
            status=status, paid_date=paid if status == "Paid" else None,
            paid_by="Store Account", recorded_by="Manager")

    def test_mix_by_category(self):
        self.pay(self.rent, 300)
        self.pay(self.elec, 450)
        self.pay(self.rent, 999, status="Pending")    # not paid: left out
        st = self.expenses()
        self.assertTrue(st.mix_ready)
        pie = st.mix_fig_dark.to_plotly_json()["data"][0]
        self.assertEqual(list(pie["labels"]), ["Electricity", "Rent"])
        self.assertEqual(list(pie["values"]), [450.0, 300.0])

    def test_hidden_when_nothing_paid(self):
        self.pay(self.rent, 999, status="Pending")
        self.assertFalse(self.expenses().mix_ready)

    def test_page_section(self):
        from ducklab import ops_expenses_pages as p
        self.assertIn("Expense Mix", str(p.expenses_page()))


if __name__ == "__main__":
    unittest.main()
