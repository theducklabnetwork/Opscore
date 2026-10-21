"""Tests for ducklab/ops_charts.py: figure data equals table data, gap
semantics, Extra offsets, blank budgets, money hover formatting, and
dark/light theme parity (styling-only difference).
Run: python -m pytest tests/test_ops_charts.py -v
"""

import unittest

from ducklab import ops_charts


class TestOpsCharts(unittest.TestCase):
    # 1 ------------------------------------------------------------------
    def test_unreported_days_are_gaps_zero_is_a_point(self):
        labels = ["01 Oct", "02 Oct", "03 Oct", "04 Oct"]
        vals = [12.0, None, 0.0, None]
        fig = ops_charts.daily_line(labels, vals, dark=True, name="Total loss")
        trace = fig.to_plotly_json()["data"][0]
        self.assertEqual(list(trace["y"]), [12.0, None, 0.0, None])
        self.assertFalse(trace["connectgaps"])   # never interpolate
        # zero is a plotted point, None is a gap
        self.assertEqual(trace["y"][2], 0.0)
        self.assertIsNone(trace["y"][1])

    # 2 ------------------------------------------------------------------
    def test_pace_actual_stops_at_last_reported(self):
        days = list(range(1, 6))
        actual = [1000, 2000, None, None, None]  # day 2 last reported
        budget = [500 * d for d in days]
        fig = ops_charts.pace_chart(days, actual, budget, dark=False)
        traces = {t["name"]: t for t in fig.to_plotly_json()["data"]}
        self.assertEqual(list(traces["Actual"]["y"]),
                         [10.0, 20.0, None, None, None])
        self.assertEqual(list(traces["Level 1 budget pace"]["y"]),
                         [5.0, 10.0, 15.0, 20.0, 25.0])

    # 3 ------------------------------------------------------------------
    def test_extra_shown_as_offset_info_only_distinct(self):
        items = [
            {"code": "DMO", "name": "Damaged", "total_cents": 5000, "sign": 1},
            {"code": "EXTRA", "name": "Extra", "total_cents": -1200,
             "sign": -1},
            {"code": "DPI", "name": "B1F1 promo", "total_cents": 800,
             "sign": 0},
        ]
        fig = ops_charts.composition(items, dark=True)
        names = {t["name"] for t in fig.to_plotly_json()["data"]}
        self.assertIn("In Total Loss", names)
        self.assertIn("Offsets Total Loss", names)
        self.assertIn("Info only", names)
        by_name = {t["name"]: t for t in fig.to_plotly_json()["data"]}
        # offsets plotted from their absolute value, distinct trace
        self.assertEqual(list(by_name["Offsets Total Loss"]["x"]), ["EXTRA"])
        self.assertEqual(list(by_name["Offsets Total Loss"]["y"]), [12.0])
        # info-only visually distinct: lower opacity
        self.assertLess(by_name["Info only"]["opacity"],
                        by_name["In Total Loss"]["opacity"])
        self.assertIn("not in Total Loss",
                      by_name["Info only"]["hovertemplate"])

    # 4 ------------------------------------------------------------------
    def test_blank_budget_produces_no_bar(self):
        fig = ops_charts.budget_vs_actual(
            ["Water", "Electricity"], [None, 320000], [14500, 285000],
            dark=True)
        budget = next(t for t in fig.to_plotly_json()["data"] if t["name"] == "Budget")
        self.assertEqual(list(budget["y"]), [None, 3200.0])

    # 5 ------------------------------------------------------------------
    def test_hover_money_formatting(self):
        self.assertEqual(ops_charts.fmt_rm(123450), "RM 1,234.50")
        self.assertEqual(ops_charts.fmt_rm0(123450), "RM 1,234")
        self.assertEqual(ops_charts.fmt_pct(0.004547), "0.45%")
        self.assertEqual(ops_charts.fmt_rm(None), "—")
        fig = ops_charts.daily_line(["01 Oct"], [1234.50], dark=True)
        self.assertIn("RM 1,234.50", fig.to_plotly_json()["data"][0]["hovertext"][0])

    # 6 ------------------------------------------------------------------
    def test_dark_and_light_differ_only_in_styling(self):
        labels = ["01 Oct", "02 Oct"]
        vals = [10.0, None]
        d = ops_charts.daily_line(labels, vals, dark=True)
        l = ops_charts.daily_line(labels, vals, dark=False)
        # identical data
        dj, lj = d.to_plotly_json(), l.to_plotly_json()
        self.assertEqual(dj["data"][0]["y"], lj["data"][0]["y"])
        self.assertEqual(dj["data"][0]["x"], lj["data"][0]["x"])
        # different styling
        self.assertNotEqual(dj["data"][0]["line"]["color"],
                            lj["data"][0]["line"]["color"])
        self.assertNotEqual(dj["layout"]["font"]["color"],
                            lj["layout"]["font"]["color"])
        # transparent background in both
        self.assertEqual(dj["layout"]["paper_bgcolor"], "rgba(0,0,0,0)")
        self.assertEqual(lj["layout"]["paper_bgcolor"], "rgba(0,0,0,0)")

    # 7 ------------------------------------------------------------------
    def test_figure_matches_table_data_from_kpi(self):
        """Daily-line figure equals the day-by-day KPI loss the table shows."""
        from ducklab import kpi
        signs = {"DMO": 1, "MISSING": 1, "EXTRA": -1}
        # synthetic register: day 1 loss, day 2 unreported (gap),
        # day 3 explicit zero stock entries
        recs = [{"date": "01 Oct", "losses": {"DMO": 5000}},
                {"date": "02 Oct", "losses": None},
                {"date": "03 Oct", "losses": {"DMO": 0}}]
        labels, vals = [], []
        for r in recs:
            labels.append(r["date"])
            if r["losses"] is None:
                vals.append(None)
            else:
                cents = kpi.total_loss_cents(r["losses"], signs)
                vals.append(cents / 100)
        fig = ops_charts.daily_line(labels, vals, dark=True)
        self.assertEqual(list(fig.to_plotly_json()["data"][0]["y"]),
                         [50.0, None, 0.0])


if __name__ == "__main__":
    unittest.main()
