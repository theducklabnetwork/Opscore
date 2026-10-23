"""S1 tests: level reached (kpi.highest_level_achieved), needed per
remaining day for any level, the pace chart's level lines, the Sales
Performance "Level reached" / next-level tiles and the Overview sales
pillar context. Expected values are hand-computed literals, including the
September 2026 workbook case (350,819 vs Level 4 350,819.30 -> Level 3,
RM 0.30 short of Level 4). Disposable temp DBs only.

Run: python -m pytest tests/test_ops_levels.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab import kpi
from ducklab import ops_charts
from ducklab.app_service import AppService
from ducklab.kpi import MonthConfig

TODAY = date(2026, 10, 21)


def rm(v):
    return int(Decimal(str(v)) * 100)


# September 2026 workbook (tests/test_kpi.py): 30 days, total RM 350,819.
WB_SALES_RM = [9681, 10511, 11478, 13309, 15022, 13256, 10901, 10228, 10903,
               11480, 11513, 15236, 14712, 9256, 10467, 11018, 10396, 11298,
               14494, 12701, 10875, 9589, 9627, 10726, 11724, 15098, 14818,
               9856, 9792, 10854]
WB_CFG = MonthConfig(month="2026-09", min_rtm_target_cents=rm(200000),
                     level1_base_cents=rm(269861), level2_pct=Decimal("0.1"),
                     level3_pct=Decimal("0.2"), level4_pct=Decimal("0.3"))
WB_TARGETS = kpi.sales_target_levels_cents(WB_CFG)


# 1 ---------------------------------------------------------------------------
class HighestLevelTests(unittest.TestCase):
    def test_september_level3_thirty_sen_short_of_level4(self):
        # L4 = 269,861 x 1.3 = 350,819.30 > 350,819 -> Level 3
        self.assertEqual(WB_TARGETS["level4"], Decimal("35081930.0"))
        self.assertEqual(
            kpi.highest_level_achieved(Decimal(rm(350819)), WB_TARGETS),
            (3, 4, Decimal("30")))

    def test_exactly_on_level4_is_top(self):
        self.assertEqual(
            kpi.highest_level_achieved(Decimal("35081930"), WB_TARGETS),
            (4, None, None))

    def test_below_level1(self):
        # L1 269,861 - 200,000 = 69,861 short
        self.assertEqual(
            kpi.highest_level_achieved(rm(200000), WB_TARGETS),
            (None, 1, Decimal(rm(69861))))

    def test_no_data_and_no_target(self):
        self.assertEqual(kpi.highest_level_achieved(None, WB_TARGETS),
                         (None, 1, None))
        none = kpi.sales_target_levels_cents(MonthConfig(month="2026-09"))
        self.assertEqual(kpi.highest_level_achieved(rm(1000), none),
                         (None, None, None))

    def test_level4_floor_rule_unchanged(self):
        # base 100,000; L2 110,000; L3 120,000; L4 raw 130,000 < floor
        # 200,000 -> L4 = 200,000. 150,000 -> Level 3, 50,000 short.
        cfg = MonthConfig(month="2026-09", min_rtm_target_cents=rm(200000),
                          level1_base_cents=rm(100000),
                          level2_pct=Decimal("0.1"),
                          level3_pct=Decimal("0.2"),
                          level4_pct=Decimal("0.3"))
        t = kpi.sales_target_levels_cents(cfg)
        self.assertEqual(kpi.highest_level_achieved(rm(150000), t),
                         (3, 4, Decimal(rm(50000))))

    def test_level1_only_subset(self):
        shown = {"level1": rm(200000)}
        self.assertEqual(kpi.highest_level_achieved(rm(200000), shown),
                         (1, None, None))
        self.assertEqual(kpi.highest_level_achieved(rm(199999), shown),
                         (None, 1, Decimal(rm(1))))

    def test_levels_missing_flags_months_without_uplift(self):
        # S2: four levels are shown everywhere; a month saved without a
        # Level 2-4 uplift is flagged instead of collapsed
        cfg = MonthConfig(month="2026-09", min_rtm_target_cents=rm(300000),
                          level1_base_cents=rm(200000),
                          level2_pct=Decimal("0.1"))
        self.assertTrue(kpi.levels_missing(cfg))
        self.assertFalse(kpi.levels_missing(WB_CFG))
        self.assertFalse(kpi.levels_missing(MonthConfig(month="2026-09")))


class NeededForLevelTests(unittest.TestCase):
    T = {"level1": rm(200000), "level2": Decimal(rm(210000)),
         "level3": Decimal(rm(220000)), "level4": Decimal(rm(230000))}

    def test_any_level_mirrors_level1_formula(self):
        # (220,000 - 69,000) / (31 - 10) = 7,190.476...
        self.assertEqual(
            kpi.needed_per_remaining_day_for_level(self.T, 3, rm(69000), 10,
                                                   31),
            Decimal(rm(151000)) / 21)
        self.assertEqual(
            kpi.needed_per_remaining_day_for_level(self.T, 1, rm(69000), 10,
                                                   31),
            kpi.sales_needed_per_remaining_day(rm(200000), rm(69000), 10,
                                               31))

    def test_none_cases(self):
        f = kpi.needed_per_remaining_day_for_level
        self.assertIsNone(f(self.T, None, rm(69000), 10, 31))   # top reached
        self.assertIsNone(f(self.T, 2, rm(69000), 31, 31))      # complete
        self.assertIsNone(f(self.T, 2, 0, 0, 31))               # no entries
        self.assertIsNone(f({"level1": rm(1)}, 2, 0, 1, 31))    # no level 2
        # already past the target -> 0, never negative
        self.assertEqual(f(self.T, 2, rm(250000), 10, 31), Decimal(0))


class LevelLinesTests(unittest.TestCase):
    def test_no_uplift_not_drawn_twice(self):
        cfg = MonthConfig(month="2026-10", level1_base_cents=rm(200000),
                          level3_pct=Decimal("0.1"))
        t = kpi.sales_target_levels_cents(cfg)
        # L2 and L4 equal L1 (no uplift) -> only Levels 1 and 3
        self.assertEqual(kpi.distinct_level_targets(t),
                         [(1, Decimal(rm(200000))),
                          (3, Decimal(rm(220000)))])

    def test_pace_lines_end_on_each_target(self):
        lines = kpi.level_pace_lines(WB_TARGETS, 30)
        self.assertEqual([n for n, _ in lines], [1, 2, 3, 4])
        # L4 350,819.30 / 30 x 30 -> 35,081,930 cents on day 30
        self.assertEqual(lines[3][1][-1], 35081930)
        self.assertEqual(len(lines[0][1]), 30)
        # Level 1 identical to pace_series' budget
        self.assertEqual(lines[0][1], [p["budget"] for p in
                                       kpi.pace_series([], 30, rm(269861))])

    def test_chart_draws_levels_light_and_labels_right_edge(self):
        lines = kpi.level_pace_lines(WB_TARGETS, 30)
        fig = ops_charts.pace_chart(list(range(1, 31)), [None] * 30,
                                    lines[0][1], dark=True, levels=lines)
        names = [t.name for t in fig.data]
        # Level 1 + Actual first (unchanged order), then Levels 2-4
        # P1 legend: the highlighted level reads "Target"
        self.assertEqual(names, ["Target", "Actual", "Level 2", "Level 3",
                                 "Level 4"])
        l1, l2 = fig.data[0], fig.data[2]
        self.assertEqual(l1.line.dash, "dash")
        self.assertEqual(l2.line.dash, "dash")
        self.assertGreater(l1.line.width, l2.line.width)
        self.assertLess(l2.opacity, l1.opacity)
        self.assertFalse(l2.showlegend)
        self.assertEqual([a.text for a in fig.layout.annotations],
                         ["Level 1", "Level 2", "Level 3", "Level 4"])
        self.assertEqual(fig.layout.annotations[3].y, 350819.30)
        self.assertEqual(fig.layout.annotations[3].x, 30)

    def test_chart_level1_only_has_no_labels(self):
        fig = ops_charts.pace_chart([1, 2], [100, None], [50, 100],
                                    dark=False, levels=[(1, [50, 100])])
        self.assertEqual(len(fig.data), 2)
        self.assertEqual(len(fig.layout.annotations), 0)


class LevelTextTests(unittest.TestCase):
    def test_level_reached_text(self):
        self.assertEqual(ds.level_reached_text(3, "actual"),
                         "Level 3 reached")
        self.assertEqual(ds.level_reached_text(2, "projected"),
                         "Projected: Level 2")
        self.assertEqual(ds.level_reached_text(2, "early_estimate"),
                         "Projected: Level 2")
        self.assertEqual(ds.level_reached_text(None, "actual"),
                         "No level reached")
        self.assertEqual(ds.level_reached_text(None, "projected"),
                         "Projected: below Level 1")


# 2 ---------------------------------------------------------------------------
class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "levels.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")

    def tearDown(self):
        self._tmp.cleanup()

    def september(self):
        """Workbook-shaped September: complete, RM 350,819."""
        self.app.configure_month(
            self.admin, self.sid, "2026-09",
            min_rtm_target_cents=rm(200000), level1_base_cents=rm(269861),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        for i, v in enumerate(WB_SALES_RM, 1):
            self.app.record_daily(self.admin, self.sid, date(2026, 9, i),
                                  sales_cents=rm(v))

    def october(self, days=10, per_day=6900, **cfg):
        """L1 200,000 / L2 210,000 / L3 220,000 / L4 230,000; ``days``
        entered at ``per_day`` (10 x 6,900 -> projected 213,900)."""
        cfg = cfg or dict(level1_base_cents=rm(200000), level2_pct=0.05,
                          level3_pct=0.10, level4_pct=0.15)
        self.app.configure_month(self.admin, self.sid, "2026-10", **cfg)
        for d in range(1, days + 1):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(per_day))

    def sales(self, month):
        import ducklab.ops_sales_state as mod
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        sid, admin = self.sid, self.admin
        patches = [
            mock.patch.object(mod, "app_service", self.app),
            mock.patch.object(SalesState, "_actor", lambda self: admin),
            mock.patch.object(SalesState, "_selected_store_id",
                              lambda self: sid),
            mock.patch.object(SalesState, "_selected_month",
                              lambda self: month),
        ]
        for p in patches:
            p.start()
        try:
            st.load_sales_data()
        finally:
            for p in reversed(patches):
                p.stop()
        return st

    def overview(self, month):
        import ducklab.ops_overview_state as ov_mod
        from ducklab.ops_overview_state import compute_overview
        with mock.patch.object(ov_mod, "app_service", self.app):
            return compute_overview(self.admin, self.sid, month, TODAY)


class SalesLevelTileTests(Fixture):
    def test_complete_month_september(self):
        self.september()
        st = self.sales("2026-09")
        self.assertEqual(st.sp_level, "Level 3")
        self.assertEqual(st.sp_level_sub, "RM 0.30 short of Level 4")
        # S3: a shortfall rounds down (99.99991% -> 99.9%)
        self.assertEqual(st.sp_level_sub2, "99.9% of Level 4")
        self.assertEqual(st.sp_level_chip, "")          # actual: no chip
        # complete: no "Needed per remaining day" tile (S3)
        self.assertTrue(st.sp_complete)
        # pace chart: Level 1 + Actual + Levels 2-4, labelled
        names = [t.name for t in st.sp_fig_dark.data]
        self.assertEqual(names[2:], ["Level 2", "Level 3", "Level 4"])
        self.assertEqual(len(st.sp_fig_light.layout.annotations), 4)

    def test_incomplete_month_projected_level2(self):
        self.october()
        st = self.sales("2026-10")
        # projected 69,000 / 10 x 31 = 213,900 >= L2 210,000 < L3 220,000
        self.assertEqual(st.sp_level, "Level 2")
        self.assertEqual(st.sp_level_chip, "Projected: Level 2")
        self.assertEqual(st.sp_level_chip_color, "amber")
        self.assertEqual(st.sp_level_sub, "RM 6,100.00 short of Level 3")
        self.assertEqual(st.sp_early_tag, "")           # 10 days: projected
        # 213,900 / 220,000 = 97.23% of the next level
        self.assertEqual(st.sp_level_sub2, "97.2% of Level 3")
        # S2: needed follows the selected level (default Level 1):
        # (200,000 - 69,000) / 21 = 6,238.10
        self.assertEqual(st.sp_needed_label,
                         "Needed per remaining day for Level 1")
        self.assertEqual(st.sp_needed, "RM 6,238")
        # L1 run-rate 200,000 / 31 = 6,451.61
        self.assertEqual(st.sp_needed_sub,
                         "Level 1 required run-rate: RM 6,452")

    def test_early_estimate_keeps_label(self):
        self.october(days=3)
        st = self.sales("2026-10")
        # 20,700 / 3 x 31 = 213,900 -> Projected: Level 2, early estimate
        self.assertEqual(st.sp_level_chip, "Projected: Level 2")
        self.assertEqual(st.sp_early_tag, "Early estimate")

    def test_projected_below_level1(self):
        self.october(per_day=6000)
        st = self.sales("2026-10")
        # 186,000 < 200,000 -> 14,000 short of Level 1;
        # needed (200,000 - 60,000) / 21 = 6,666.67
        self.assertEqual(st.sp_level, "Below Level 1")
        self.assertEqual(st.sp_level_chip, "Projected: below Level 1")
        self.assertEqual(st.sp_level_sub, "RM 14,000.00 short of Level 1")
        # S3: next level = the selected Level 1 -> shown in the
        # achievement tile, not repeated here
        self.assertEqual(st.sp_level_sub2, "")
        self.assertEqual(st.sp_needed_label,
                         "Needed per remaining day for Level 1")
        self.assertEqual(st.sp_needed, "RM 6,667")

    def test_no_target(self):
        self.october(min_rtm_target_cents=rm(100000))
        st = self.sales("2026-10")
        self.assertEqual(st.sp_level, "No target")
        self.assertEqual(st.sp_level_sub, "")
        self.assertEqual(st.sp_needed, "—")
        self.assertEqual(st.sp_needed_sub,
                         "Level 1 required run-rate: No target")

    def test_month_saved_without_uplift_shows_four_levels(self):
        # a month saved before four levels were required (written past
        # the service rule): L2/L3 = L1 by the workbook rule, L4 = floor
        # 300,000. Projected 70,000 / 10 x 31 = 217,000 -> Level 3 of 4.
        self.app.write.repo.save_month_config(
            self.sid, "2026-10", level1_base_cents=rm(200000),
            min_rtm_target_cents=rm(300000))
        for d in range(1, 11):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(7000))
        st = self.sales("2026-10")
        self.assertTrue(st.sp_levels_missing)
        self.assertEqual(st.sp_level, "Level 3")
        self.assertEqual(st.sp_level_sub, "RM 83,000.00 short of Level 4")
        self.assertEqual(len(st.sp_ladder), 4)
        # L2/L3 equal L1: drawn once
        self.assertEqual([t.name for t in st.sp_fig_dark.data],
                         ["Target", "Actual", "Level 4"])

    def test_nothing_entered(self):
        self.october(days=0)
        st = self.sales("2026-10")
        self.assertEqual(st.sp_level, "—")
        self.assertEqual(st.sp_level_chip, "")


class OverviewLevelContextTests(Fixture):
    def test_complete_month_level_reached(self):
        self.september()
        ctx = self.overview("2026-09")["pill_sales"]["context"]
        self.assertEqual(ctx, "Projected RM 350.8k · 130.0% of Level 1 · "
                              "Level 3 reached")

    def test_incomplete_month_projected_level(self):
        self.october()
        ov = self.overview("2026-10")
        self.assertEqual(ov["pill_sales"]["context"],
                         "Projected RM 213.9k · 106.9% of Level 1 · "
                         "Projected: Level 2")
        # Overview pace chart draws the same level lines
        self.assertEqual(len(ov["pace_fig_dark"].layout.annotations), 4)

    def test_no_level_text_without_data_or_target(self):
        self.october(days=0)
        self.assertNotIn("Level 2", self.overview("2026-10")[
            "pill_sales"]["context"])
        self.assertTrue(self.overview("2026-10")["pill_sales"]["context"]
                        .endswith("of Level 1"))


if __name__ == "__main__":
    unittest.main()
