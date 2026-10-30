"""Synthetic sales-level, projection and comparison rules with literal values."""

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
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.operations_write_repository import ValidationError

TODAY = date(2026, 10, 21)
LEVELS_REQUIRED = ("Levels 2–4 uplift is required when a Level 1 target "
                   "is set")


def rm(value):
    return int(Decimal(str(value)) * 100)


TARGETS = {"level1": rm(100000), "level2": Decimal(rm(110000)),
           "level3": Decimal(rm(120000)), "level4": Decimal(rm(130000))}


class LevelRuleTests(unittest.TestCase):
    def test_highest_level_exact_below_top_and_missing_data(self):
        self.assertEqual(kpi.highest_level_achieved(rm(125000), TARGETS),
                         (3, 4, Decimal(rm(5000))))
        self.assertEqual(kpi.highest_level_achieved(Decimal(rm(130000)), TARGETS),
                         (4, None, None))
        self.assertEqual(kpi.highest_level_achieved(rm(90000), TARGETS),
                         (None, 1, Decimal(rm(10000))))
        self.assertEqual(kpi.highest_level_achieved(None, TARGETS), (None, 1, None))
        empty = kpi.sales_target_levels_cents(MonthConfig(month="2026-04"))
        self.assertEqual(kpi.highest_level_achieved(rm(1000), empty), (None, None, None))

    def test_level_one_subset_and_floor(self):
        shown = {"level1": rm(100000)}
        self.assertEqual(kpi.highest_level_achieved(rm(100000), shown), (1, None, None))
        self.assertEqual(kpi.highest_level_achieved(rm(99999), shown),
                         (None, 1, Decimal(rm(1))))
        cfg = MonthConfig(month="2026-04", min_rtm_target_cents=rm(200000),
                          level1_base_cents=rm(100000), level2_pct=Decimal("0.1"),
                          level3_pct=Decimal("0.2"), level4_pct=Decimal("0.3"))
        targets = kpi.sales_target_levels_cents(cfg)
        self.assertEqual(targets["level4"], Decimal(rm(200000)))
        self.assertEqual(kpi.highest_level_achieved(rm(150000), targets),
                         (3, 4, Decimal(rm(50000))))

    def test_levels_missing(self):
        cfg = MonthConfig(month="2026-04", min_rtm_target_cents=rm(300000),
                          level1_base_cents=rm(200000), level2_pct=Decimal("0.1"))
        self.assertTrue(kpi.levels_missing(cfg))
        complete = MonthConfig(month="2026-04", level1_base_cents=rm(100000),
                               level2_pct=Decimal("0.1"), level3_pct=Decimal("0.2"),
                               level4_pct=Decimal("0.3"))
        self.assertFalse(kpi.levels_missing(complete))
        self.assertFalse(kpi.levels_missing(MonthConfig(month="2026-04")))

    def test_needed_for_selected_level_and_edges(self):
        self.assertEqual(kpi.needed_per_remaining_day_for_level(
            TARGETS, 3, rm(69000), 10, 31), Decimal(rm(51000)) / 21)
        self.assertEqual(kpi.needed_per_remaining_day_for_level(
            TARGETS, 1, rm(69000), 10, 31),
            kpi.sales_needed_per_remaining_day(rm(100000), rm(69000), 10, 31))
        f = kpi.needed_per_remaining_day_for_level
        self.assertIsNone(f(TARGETS, None, rm(69000), 10, 31))
        self.assertIsNone(f(TARGETS, 2, rm(69000), 31, 31))
        self.assertIsNone(f(TARGETS, 2, 0, 0, 31))
        self.assertIsNone(f({"level1": rm(1)}, 2, 0, 1, 31))
        self.assertEqual(f(TARGETS, 2, rm(200000), 10, 31), Decimal(0))

    def test_distinct_pace_lines_and_chart_labels(self):
        cfg = MonthConfig(month="2026-04", level1_base_cents=rm(100000),
                          level3_pct=Decimal("0.1"))
        targets = kpi.sales_target_levels_cents(cfg)
        self.assertEqual(kpi.distinct_level_targets(targets),
                         [(1, Decimal(rm(100000))), (3, Decimal(rm(110000)))])
        lines = kpi.level_pace_lines(TARGETS, 30)
        self.assertEqual([number for number, _ in lines], [1, 2, 3, 4])
        self.assertEqual(lines[3][1][-1], 13000000)
        self.assertEqual(len(lines[0][1]), 30)
        self.assertEqual(lines[0][1], [p["budget"] for p in
                                       kpi.pace_series([], 30, rm(100000))])
        fig = ops_charts.pace_chart(list(range(1, 31)), [None] * 30,
                                    lines[0][1], dark=True, levels=lines)
        self.assertEqual([trace.name for trace in fig.data],
                         ["Actual", "Target", "Level 2", "Level 3", "Level 4"])
        self.assertEqual(fig.data[1].line.dash, "dash")
        self.assertEqual(fig.data[2].line.dash, "dash")
        self.assertGreater(fig.data[1].line.width, fig.data[2].line.width)
        self.assertLess(fig.data[2].opacity, fig.data[1].opacity)
        self.assertFalse(fig.data[2].showlegend)
        self.assertEqual([a.text for a in fig.layout.annotations],
                         ["Level 1", "Level 2", "Level 3", "Level 4"])
        self.assertEqual(fig.layout.annotations[3].y, 130000.0)
        self.assertEqual(fig.layout.annotations[3].x, 30)

    def test_level_one_chart_and_level_text(self):
        fig = ops_charts.pace_chart([1, 2], [100, None], [50, 100],
                                    dark=False, levels=[(1, [50, 100])])
        self.assertEqual(len(fig.data), 2)
        self.assertEqual(len(fig.layout.annotations), 0)
        self.assertEqual(ds.level_reached_text(3, "actual"), "Level 3 reached")
        self.assertEqual(ds.level_reached_text(2, "projected"), "Projected: Level 2")
        self.assertEqual(ds.level_reached_text(2, "early_estimate"), "Projected: Level 2")
        self.assertEqual(ds.level_reached_text(None, "actual"), "No level reached")
        self.assertEqual(ds.level_reached_text(None, "projected"), "Projected: below Level 1")

    def test_shortfall_rounding_only(self):
        ratio = Decimal(rm(120000)) / Decimal("12000030")
        self.assertEqual(ds.fmt_pct(ratio), "100.0%")
        self.assertEqual(ds.fmt_achievement(ratio, "Below target"), "99.9%")
        f = ds.fmt_achievement
        self.assertEqual(f(Decimal("0.99999"), "Below target"), "99.9%")
        self.assertEqual(f(Decimal("0.0999"), "Below target"), "9.9%")
        self.assertEqual(f(Decimal("0.5"), "Below target"), "50.0%")
        self.assertEqual(f(Decimal("1.29996"), "Achieved"), "130.0%")
        self.assertEqual(f(Decimal("1"), "Achieved"), "100.0%")
        self.assertEqual(f(Decimal("0.99999"), "Awaiting data"), "100.0%")
        self.assertEqual(f(None, "Below target"), "—")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "synthetic-levels.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.test", "Admin A", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "S01", "Store A")

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, month="2026-10"):
        return self.app.get_month_config(self.admin, self.sid, month)

    def bound(self, module, cls, fn, month="2026-10"):
        sid, admin = self.sid, self.admin
        with mock.patch.object(module, "app_service", self.app), \
                mock.patch.object(cls, "_actor", lambda self_: admin), \
                mock.patch.object(cls, "_selected_store_id", lambda self_: sid), \
                mock.patch.object(cls, "_selected_month", lambda self_: month):
            return fn()

    def complete(self, base=rm(100000)):
        self.app.configure_month(
            self.admin, self.sid, "2026-04", min_rtm_target_cents=rm(50000),
            level1_base_cents=base, level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        for day in range(1, 31):
            self.app.record_daily(self.admin, self.sid, date(2026, 4, day),
                                  sales_cents=rm(4000))

    def october(self, days=10, per_day=6900, **cfg):
        defaults = dict(level1_base_cents=rm(200000), level2_pct=0.05,
                        level3_pct=0.10, level4_pct=0.15)
        defaults.update(cfg)
        self.app.configure_month(self.admin, self.sid, "2026-10", **defaults)
        for day in range(1, days + 1):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, day),
                                  sales_cents=rm(per_day))

    def sales(self, month, compare="1"):
        import ducklab.ops_sales_state as mod
        from ducklab.ops_sales_state import SalesState
        state = SalesState()
        state.sp_compare = compare
        self.bound(mod, SalesState, state.load_sales_data, month=month)
        return state

    def overview(self, month):
        import ducklab.ops_overview_state as mod
        from ducklab.ops_overview_state import compute_overview
        with mock.patch.object(mod, "app_service", self.app):
            return compute_overview(self.admin, self.sid, month, TODAY)

    def daily_kpis(self, month):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        state = DailyState()
        days = self.app.get_daily_records(self.admin, self.sid, month)
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(DailyState, "_actor", lambda self_: self.admin), \
                mock.patch.object(DailyState, "_selected_store_id", lambda self_: self.sid), \
                mock.patch.object(DailyState, "_selected_month", lambda self_: month):
            return state._monthly_kpis(days)


class FourLevelsRequiredTests(Fixture):
    def assert_rejected(self, **fields):
        with self.assertRaises(ValidationError) as ctx:
            self.app.configure_month(self.admin, self.sid, "2026-10", **fields)
        self.assertEqual(str(ctx.exception), LEVELS_REQUIRED)

    def test_missing_uplifts_rejected_atomically(self):
        self.assert_rejected(level1_base_cents=rm(200000))
        self.assert_rejected(level1_base_cents=rm(200000), level2_pct=0.1,
                             level3_pct=0.2)
        self.assert_rejected(level1_base_cents=rm(200000), level2_pct=0.1,
                             level3_pct=0.2, level4_pct=None)
        with self.assertRaises(ConfigNotFoundError):
            self.config()
        category = self.app.add_expense_category(self.admin, self.sid, "Power", sort_order=1)
        with self.assertRaises(ValidationError):
            self.app.configure_month_with_budgets(
                self.admin, self.sid, "2026-10",
                {"level1_base_cents": rm(200000)}, {category: rm(500)})
        self.assertEqual(self.app.get_expense_budgets(self.admin, self.sid, "2026-10"), {})

    def test_no_target_and_complete_config_can_be_saved(self):
        self.app.configure_month(self.admin, self.sid, "2026-10", stock_loss_target=0.015)
        self.assertIsNone(self.config().level1_base_cents)
        self.app.configure_month(self.admin, self.sid, "2026-10", level2_pct=0.1)
        self.assertEqual(self.config().level2_pct, Decimal("0.1"))
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(200000), level2_pct=0.1,
                                 level3_pct=0.2, level4_pct=0.3)
        cfg = self.config()
        self.assertEqual((cfg.level1_base_cents, cfg.level2_pct, cfg.level3_pct,
                          cfg.level4_pct),
                         (rm(200000), Decimal("0.1"), Decimal("0.2"), Decimal("0.3")))
        self.assert_rejected(level3_pct=None)
        self.assertEqual(self.config().level3_pct, Decimal("0.2"))
        self.app.configure_month(self.admin, self.sid, "2026-10", level2_pct=0)
        self.assertEqual(self.config().level2_pct, Decimal(0))

    def test_legacy_missing_uplifts_only_checked_when_levels_change(self):
        self.app.write.repo.save_month_config(self.sid, "2026-10",
                                               level1_base_cents=rm(200000))
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 salary_basic_allowance_cents=rm(10000),
                                 stock_loss_target=0.015)
        self.assertIsNone(self.config().level2_pct)
        self.assert_rejected(level1_base_cents=rm(210000))
        self.app.configure_month(self.admin, self.sid, "2026-10", level2_pct=0.1,
                                 level3_pct=0.2, level4_pct=0.3)
        self.assertEqual(self.config().level4_pct, Decimal("0.3"))


class MonthlySetupTests(Fixture):
    def setup_state(self):
        from ducklab.ops_stores_state import StoresState
        state = StoresState()
        state.setup_original = {}
        return state

    def save(self, state):
        import ducklab.ops_setup_state as mod
        from ducklab.ops_stores_state import StoresState
        self.bound(mod, StoresState, lambda: list(state.save_setup()))

    def test_form_error_and_visible_hq_defaults(self):
        state = self.setup_state()
        state.s_level1 = "200000"
        self.save(state)
        self.assertEqual(state.setup_error, LEVELS_REQUIRED)
        state.use_hq_defaults()
        self.assertEqual((state.s_level2, state.s_level3, state.s_level4),
                         ("10", "20", "30"))
        with self.assertRaises(ConfigNotFoundError):
            self.config()
        self.save(state)
        self.assertEqual(state.setup_notice, "Monthly setup saved.")
        self.assertEqual((self.config().level2_pct, self.config().level3_pct,
                          self.config().level4_pct),
                         (Decimal("0.1"), Decimal("0.2"), Decimal("0.3")))

    def test_missing_uplift_warning_on_setup_and_sales(self):
        import ducklab.ops_stores_state as mod
        from ducklab.ops_stores_state import StoresState
        self.app.write.repo.save_month_config(self.sid, "2026-10",
                                               level1_base_cents=rm(200000))
        setup = self.setup_state()
        self.bound(mod, StoresState, setup.load_page_data)
        self.assertTrue(setup.setup_levels_missing)
        self.assertEqual([level["level"] for level in setup.setup_levels],
                         ["Level 1", "Level 2", "Level 3", "Level 4"])
        self.assertEqual(ds.LEVELS_MISSING, "Levels 2–4 missing in Monthly Setup")
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1), sales_cents=rm(1000))
        sales = self.sales("2026-10")
        self.assertTrue(sales.sp_levels_missing)
        self.assertEqual(sales.sp_level, "Below Level 1")


class SalesLevelAndComparisonTests(Fixture):
    def test_complete_month_tiles_ladder_and_no_needed_tile(self):
        self.complete()
        state = self.sales("2026-04")
        self.assertTrue(state.sp_complete)
        self.assertEqual(state.sp_level, "Level 3")
        self.assertEqual(state.sp_level_sub, "RM 10,000.00 short of Level 4")
        self.assertEqual(state.sp_level_sub2, "92.3% of Level 4")
        self.assertEqual(state.sp_level_chip, "")
        self.assertEqual(state.sp_l1_achievement_label, "Achievement vs Level 1")
        self.assertEqual(state.sp_l1_achievement, "120.0%")
        self.assertEqual(state.sp_l1_achievement_sub, "Gap RM 20,000.00")
        self.assertEqual((state.sp_proj_status, state.sp_proj_color), ("Achieved", "green"))
        self.assertEqual([row["achievement"] for row in state.sp_ladder],
                         ["120.0%", "109.1%", "100.0%", "92.3%"])
        for value in (state.sp_sales_mtd, state.sp_month_end, state.sp_l1_achievement,
                      state.sp_level, state.sp_avg_day):
            self.assertNotEqual(value, "—")

    def test_complete_selected_levels_control_status_and_subline(self):
        self.complete()
        state = self.sales("2026-04", "4")
        self.assertEqual(state.sp_l1_achievement_label, "Achievement vs Level 4")
        self.assertEqual(state.sp_l1_achievement, "92.3%")
        self.assertEqual((state.sp_proj_status, state.sp_proj_color), ("Below target", "red"))
        self.assertEqual(state.sp_l1_achievement_sub, "Gap −RM 10,000.00")
        self.assertEqual(state.sp_level_sub2, "")
        state = self.sales("2026-04", "3")
        self.assertEqual(state.sp_l1_achievement_label, "Achievement vs Level 3")
        self.assertEqual(state.sp_l1_achievement, "100.0%")
        self.assertEqual((state.sp_proj_status, state.sp_proj_color), ("Achieved", "green"))
        self.assertEqual(state.sp_level_sub2, "92.3% of Level 4")

    def test_top_level_has_no_next_level_subline(self):
        self.complete()
        self.app.configure_month(self.admin, self.sid, "2026-04", level4_pct=0.1)
        state = self.sales("2026-04")
        self.assertEqual(state.sp_level, "Level 4")
        self.assertEqual(state.sp_level_sub, "All levels reached")
        self.assertEqual(state.sp_level_sub2, "")

    def test_incomplete_projection_and_selected_level(self):
        self.october()
        state = self.sales("2026-10")
        self.assertFalse(state.sp_complete)
        self.assertEqual(state.sp_level, "Level 2")
        self.assertEqual(state.sp_level_chip, "Projected: Level 2")
        self.assertEqual(state.sp_level_chip_color, "amber")
        self.assertEqual(state.sp_level_sub, "RM 6,100.00 short of Level 3")
        self.assertEqual(state.sp_level_sub2, "97.2% of Level 3")
        self.assertEqual(state.sp_early_tag, "")
        self.assertEqual(state.sp_l1_achievement_label, "Projected achievement vs Level 1")
        self.assertEqual(state.sp_l1_achievement, "106.9%")
        self.assertEqual(state.sp_l1_achievement_sub, "Projected gap RM 13,900.00")
        self.assertEqual(state.sp_mtd_achievement_label, "Achievement to date vs Level 1")
        self.assertEqual(state.sp_mtd_achievement, "34.5%")
        self.assertEqual(state.sp_proj_status, "Projected: Achieved")
        self.assertEqual(state.sp_needed_label, "Needed per remaining day for Level 1")
        self.assertEqual(state.sp_needed, "RM 6,238")
        self.assertEqual(state.sp_needed_sub, "Level 1 required run-rate: RM 6,452")

    def test_compare_level_three_drives_tiles_needed_and_chart(self):
        self.october()
        state = self.sales("2026-10", "3")
        self.assertEqual(state.sp_l1_achievement_label, "Projected achievement vs Level 3")
        self.assertEqual(state.sp_l1_achievement, "97.2%")
        self.assertEqual(state.sp_l1_achievement_sub, "Projected gap −RM 6,100.00")
        self.assertEqual(state.sp_proj_status, "Projected: Below target")
        self.assertEqual(state.sp_mtd_achievement, "31.3%")
        self.assertEqual(state.sp_mtd_achievement_sub, "Level 3 target RM 220,000")
        self.assertEqual(state.sp_needed_label, "Needed per remaining day for Level 3")
        self.assertEqual(state.sp_needed, "RM 7,190")
        self.assertEqual(state.sp_needed_sub, "Level 3 required run-rate: RM 7,097")
        by_name = {trace.name: trace for trace in state.sp_fig_dark.data}
        self.assertGreater(by_name["Target"].line.width, by_name["Level 1"].line.width)
        self.assertTrue(by_name["Target"].showlegend)
        self.assertFalse(by_name["Level 1"].showlegend)
        self.assertEqual(state.sp_level, "Level 2")
        self.assertEqual(len(state.sp_ladder), 4)
        self.assertEqual(state.sp_rows[0]["target"], "—")

    def test_early_below_no_target_and_empty(self):
        self.october(days=3)
        state = self.sales("2026-10")
        self.assertEqual(state.sp_early_tag, "Early estimate")
        self.october(per_day=6000)
        state = self.sales("2026-10")
        self.assertEqual(state.sp_level, "Below Level 1")
        self.assertEqual(state.sp_level_chip, "Projected: below Level 1")
        self.assertEqual(state.sp_level_sub, "RM 14,000.00 short of Level 1")
        self.assertEqual(state.sp_level_sub2, "")
        self.assertEqual(state.sp_needed, "RM 6,667")
        self.october(level1_base_cents=None, min_rtm_target_cents=rm(100000))
        state = self.sales("2026-10")
        self.assertEqual(state.sp_level, "No target")
        self.assertEqual(state.sp_needed, "—")
        self.assertEqual(state.sp_needed_sub, "Level 1 required run-rate: No target")

    def test_nothing_entered(self):
        self.october(days=0)
        state = self.sales("2026-10")
        self.assertEqual(state.sp_level, "—")
        self.assertEqual(state.sp_level_chip, "")

    def test_legacy_month_still_shows_four_levels(self):
        self.app.write.repo.save_month_config(
            self.sid, "2026-10", level1_base_cents=rm(200000),
            min_rtm_target_cents=rm(300000))
        for day in range(1, 11):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, day),
                                  sales_cents=rm(7000))
        state = self.sales("2026-10")
        self.assertTrue(state.sp_levels_missing)
        self.assertEqual(state.sp_level, "Level 3")
        self.assertEqual(state.sp_level_sub, "RM 83,000.00 short of Level 4")
        self.assertEqual(len(state.sp_ladder), 4)
        self.assertEqual([trace.name for trace in state.sp_fig_dark.data],
                         ["Actual", "Target", "Level 4"])

    def test_selector_validation_and_browser_default(self):
        import reflex as rx
        from ducklab.ops_sales_state import SalesState
        state = SalesState()
        self.assertEqual(len(list(state.select_compare("4"))), 1)
        self.assertEqual(state.sp_compare, "4")
        list(state.select_compare("9"))
        self.assertEqual(state.sp_compare, "1")
        default = SalesState.get_fields()["sp_compare"].default
        self.assertIsInstance(default, rx.LocalStorage)
        self.assertEqual((str(default), default.name), ("1", "ducklab_sales_compare"))


class OverviewAndDailyTests(Fixture):
    def test_overview_uses_level_one_and_independent_projection(self):
        self.complete()
        overview = self.overview("2026-04")
        self.assertEqual(overview["pill_sales"]["context"],
                         "Projected RM 120.0k · 120.0% of Level 1 · Level 3 reached")
        self.assertEqual([target["pct"] for target in overview["targets"]],
                         ["120.0%", "109.1%", "100.0%", "92.3%"])
        self.october()
        overview = self.overview("2026-10")
        self.assertEqual(overview["pill_sales"]["context"],
                         "Projected RM 213.9k · 106.9% of Level 1 · Projected: Level 2")
        self.assertEqual(len(overview["pace_fig_dark"].layout.annotations), 4)
        line1, _, line2 = overview["pace_fig_dark"].data[:3]
        self.assertGreater(line1.line.width, line2.line.width)

    def test_overview_without_data_has_no_level_claim(self):
        self.october(days=0)
        context = self.overview("2026-10")["pill_sales"]["context"]
        self.assertNotIn("Level 2", context)
        self.assertTrue(context.endswith("of Level 1"))

    def test_overview_and_daily_shortfalls_round_down(self):
        self.complete(base=rm(120000.01))
        overview = self.overview("2026-04")
        self.assertEqual(overview["targets"][0]["pct"], "99.9%")
        self.assertEqual(overview["targets"][0]["status"], "Below target")
        self.assertEqual(overview["cc"]["proj_achievement"], "99.9%")
        daily = self.daily_kpis("2026-04")
        self.assertEqual(daily["achievement"], "99.9%")
        self.assertEqual(daily["proj_achievement"], "99.9%")
        self.assertEqual(daily["proj_status"], "Below target")

    def test_daily_achieved_and_incomplete_shortfall(self):
        self.complete()
        daily = self.daily_kpis("2026-04")
        self.assertEqual((daily["achievement"], daily["proj_achievement"]),
                         ("120.0%", "120.0%"))
        self.october()
        daily = self.daily_kpis("2026-10")
        self.assertEqual(daily["proj_achievement"], "106.9%")
        self.assertEqual(daily["proj_status"], "Projected: Achieved")
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(213900.01))
        daily = self.daily_kpis("2026-10")
        self.assertEqual(daily["proj_achievement"], "99.9%")
        self.assertEqual(daily["proj_status"], "Projected: Below target")
        self.assertEqual(daily["achievement"], "32.2%")


class PageBuildTests(unittest.TestCase):
    def test_selector_labels_and_complete_layout(self):
        from ducklab.ops_sales_pages import _compare_control, _kpi_tiles
        control = str(_compare_control())
        for number in (1, 2, 3, 4):
            self.assertIn(f'"Level {number}"', control)
            self.assertIn(f'"L{number}"', control)
        tiles = str(_kpi_tiles())
        self.assertEqual(tiles.count("sales-needed"), 0)
        self.assertEqual(tiles.count("sales-mtd-achievement"), 0)
        self.assertEqual(tiles.count('"Projected Sales"'), 1)
        self.assertEqual(tiles.count("sales-avg-day"), 2)

    def test_changed_pages_compile(self):
        from ducklab.ops_pages import dashboard_page
        from ducklab.ops_sales_pages import sales_page
        from ducklab.ops_stores_pages import stores_page
        for page in (sales_page, stores_page, dashboard_page):
            self.assertIsNotNone(page())


if __name__ == "__main__":
    unittest.main()
