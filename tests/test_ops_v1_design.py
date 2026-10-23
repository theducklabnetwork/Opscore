"""V1 design kit tests: Daily achievement labels (MTD vs projected), the
unified day statuses, collapsed unreported days, the chart "not enough
data" threshold and compact money. Presentation only — every figure comes
from the engine. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_v1_design.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab import kpi
from ducklab.app_service import AppService


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "v1.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(210000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)

    def tearDown(self):
        self._tmp.cleanup()

    def record(self, day, **fields):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, day),
                              **fields)

    def bound(self, module, state_cls, fn):
        sid, admin = self.sid, self.admin
        patches = [
            mock.patch.object(module, "app_service", self.app),
            mock.patch.object(state_cls, "_actor", lambda self: admin),
            mock.patch.object(state_cls, "_selected_store_id",
                              lambda self: sid),
            mock.patch.object(state_cls, "_selected_month",
                              lambda self: "2026-10"),
        ]
        for p in patches:
            p.start()
        try:
            return fn()
        finally:
            for p in reversed(patches):
                p.stop()

    def daily_kpis(self):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        days = self.app.get_daily_records(self.admin, self.sid, "2026-10")
        return self.bound(mod, DailyState, lambda: st._monthly_kpis(days))

    def sales_state(self):
        import ducklab.ops_sales_state as mod
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        self.bound(mod, SalesState, st.load_sales_data)
        return st


class DailyAchievementLabelTests(Fixture):
    def test_achievement_is_mtd_over_level1(self):
        """MTD 23,500 / Level 1 210,000 = 11.19% -> '11.1%' (below
        target: rounded down, S3)."""
        self.record(1, sales_cents=rm(12000))
        self.record(2, sales_cents=rm(11500))
        k = self.daily_kpis()
        self.assertEqual(k["mtd"], "RM 23,500.00")
        self.assertEqual(k["achievement"], "11.1%")
        # projected achievement is a separate figure: 23,500 / 2 * 31 =
        # 364,250 / 210,000 = 173.5%
        self.assertEqual(k["proj_achievement"], "173.5%")
        self.assertEqual(k["projected"], "RM 364,250.00")   # O1 format

    def test_projection_label_and_early_tag_like_overview(self):
        self.record(1, sales_cents=rm(12000))
        self.record(2, sales_cents=rm(11500))
        k = self.daily_kpis()
        rep = self.app.preview_month(self.admin, self.sid, "2026-10")
        label, color, tag = ds.projection_style(
            rep.sales.projection_state, rep.sales.status_by_level["level1"])
        self.assertEqual((k["proj_status"], k["proj_color"], k["early_tag"]),
                         (label, color, tag))
        self.assertEqual(k["proj_status"], "Projected: Achieved")
        self.assertEqual(k["early_tag"], "Early estimate")   # 2 of 31 days
        self.assertIn("2 of 31", k["early_tip"])

    def test_no_early_tag_from_seven_days(self):
        for d in range(1, 8):
            self.record(d, sales_cents=rm(1000))
        k = self.daily_kpis()
        self.assertTrue(k["proj_status"].startswith("Projected: "))
        self.assertEqual(k["early_tag"], "")
        self.assertEqual(k["early_tip"], "")

    def test_no_sales_days_shows_dash_not_zero(self):
        self.record(1, ft_working=5)
        k = self.daily_kpis()
        self.assertEqual(k["achievement"], "—")


class DayStatusTests(unittest.TestCase):
    def test_four_statuses(self):
        d = date(2026, 10, 1)
        self.assertEqual(ds.day_status(None), "Not reported")
        self.assertEqual(ds.day_status(kpi.DailyInput(d)), "Not reported")
        self.assertEqual(ds.day_status(kpi.DailyInput(d, sales_cents=0)),
                         "Zero sales")
        self.assertEqual(ds.day_status(kpi.DailyInput(d, sales_cents=100)),
                         "Partial")
        self.assertEqual(ds.day_status(kpi.DailyInput(
            d, sales_cents=100, transactions=2, units=3)), "Complete")
        self.assertEqual(ds.day_status(kpi.DailyInput(
            d, sales_cents=100, stock_losses={"DMO": 5, "DMC": 1})),
            "Complete")

    def test_same_chip_colours_everywhere(self):
        colors = [ds.status_color(s) for s in ds.DAY_STATUSES]
        self.assertEqual(colors, ["green", "amber", "gray", "blue"])

    def test_hidden_days_label(self):
        self.assertEqual(ds.days_not_reported_label(29),
                         "29 days not reported")
        self.assertEqual(ds.days_not_reported_label(1), "1 day not reported")


class DailyRowsTests(Fixture):
    def test_rows_use_unified_statuses(self):
        from ducklab.ops_daily_state import DailyState
        self.record(1, sales_cents=rm(100), transactions=1, units=1)
        self.record(2, sales_cents=0)
        self.record(3, ft_working=4)
        days = {r.date.day: r for r in self.app.get_daily_records(
            self.admin, self.sid, "2026-10")}
        st = DailyState()
        status = {n: st._day_row(date(2026, 10, n), days.get(n), {}, 0.5)
                  ["status"] for n in (1, 2, 3, 4)}
        self.assertEqual(status, {1: "Complete", 2: "Zero sales",
                                  3: "Partial", 4: "Not reported"})


class SalesPageTests(Fixture):
    def test_collapsed_days_and_statuses(self):
        self.record(1, sales_cents=rm(12000), transactions=10, units=20)
        self.record(2, sales_cents=0)
        st = self.sales_state()
        self.assertEqual(len(st.sp_rows), 31)
        self.assertEqual([r["status"] for r in st.sp_rows_reported],
                         ["Complete", "Zero sales"])
        self.assertEqual(st.sp_hidden_count, 29)
        self.assertEqual(st.sp_hidden_label, "29 days not reported")
        self.assertFalse(st.sp_show_hidden)              # default collapsed

    def test_chart_needs_three_reported_days(self):
        self.record(1, sales_cents=rm(1000))
        self.record(2, sales_cents=rm(1000))
        self.assertFalse(self.sales_state().sp_chart_ready)
        self.record(3, sales_cents=rm(1000))
        self.assertTrue(self.sales_state().sp_chart_ready)

    def test_mtd_and_projected_achievement_with_chip(self):
        self.record(1, sales_cents=rm(12000))
        self.record(2, sales_cents=rm(11500))
        st = self.sales_state()
        self.assertEqual(st.sp_mtd_achievement, "11.1%")   # same as Daily
        self.assertEqual(st.sp_l1_achievement, "173.5%")
        self.assertEqual(st.sp_proj_status, "Projected: Achieved")
        self.assertEqual(st.sp_early_tag, "Early estimate")
        self.assertEqual(st.sp_sales_mtd, "RM 23,500")
        self.assertEqual(st.sp_sales_mtd_compact, "RM 23.5k")
        self.assertEqual(st.sp_sales_mtd_exact, "RM 23,500.00")


class CompactMoneyTests(unittest.TestCase):
    def test_compact(self):
        self.assertEqual(ds.fmt_rm_compact(2350000), "RM 23.5k")
        self.assertEqual(ds.fmt_rm_compact(150000000), "RM 1.5M")
        self.assertEqual(ds.fmt_rm_compact(706666), "RM 7,066.66")
        self.assertEqual(ds.fmt_rm_compact(706666, whole_below=True),
                         "RM 7,067")
        self.assertEqual(ds.fmt_rm_compact(None), "—")


if __name__ == "__main__":
    unittest.main()
