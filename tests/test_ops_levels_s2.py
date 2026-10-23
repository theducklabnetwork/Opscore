"""S2 tests: four levels required (write-service rule, Monthly Setup "Use
HQ defaults" button, warning chip for months saved without uplift), the
Sales Performance "Compare against" level selector, complete-month tiles
and the Level reached sub-line. Expected values are hand-computed
literals. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_levels_s2.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab.app_service import AppService
from ducklab.operations_repository import ConfigNotFoundError
from ducklab.operations_write_repository import ValidationError

TODAY = date(2026, 10, 21)
LEVELS_REQUIRED = ("Levels 2–4 uplift is required when a Level 1 target "
                   "is set")
WB_SALES_RM = [9681, 10511, 11478, 13309, 15022, 13256, 10901, 10228, 10903,
               11480, 11513, 15236, 14712, 9256, 10467, 11018, 10396, 11298,
               14494, 12701, 10875, 9589, 9627, 10726, 11724, 15098, 14818,
               9856, 9792, 10854]  # September 2026, total RM 350,819


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "s2.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")

    def tearDown(self):
        self._tmp.cleanup()

    def config(self, month="2026-10"):
        return self.app.get_month_config(self.admin, self.sid, month)

    def bound(self, module, cls, fn, month="2026-10"):
        sid, admin = self.sid, self.admin
        with mock.patch.object(module, "app_service", self.app), \
                mock.patch.object(cls, "_actor", lambda self_: admin), \
                mock.patch.object(cls, "_selected_store_id",
                                  lambda self_: sid), \
                mock.patch.object(cls, "_selected_month",
                                  lambda self_: month):
            return fn()

    def september(self):
        """Complete month: L1 269,861 / L2 296,847.10 / L3 323,833.20 /
        L4 350,819.30; actual RM 350,819."""
        self.app.configure_month(
            self.admin, self.sid, "2026-09",
            min_rtm_target_cents=rm(200000), level1_base_cents=rm(269861),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        for i, v in enumerate(WB_SALES_RM, 1):
            self.app.record_daily(self.admin, self.sid, date(2026, 9, i),
                                  sales_cents=rm(v))

    def october(self):
        """Incomplete: L1 200,000 / L2 210,000 / L3 220,000 / L4 230,000;
        10 x 6,900 -> MTD 69,000, projected 213,900."""
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=rm(200000),
            level2_pct=0.05, level3_pct=0.10, level4_pct=0.15)
        for d in range(1, 11):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(6900))

    def sales(self, month, compare="1"):
        import ducklab.ops_sales_state as mod
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        st.sp_compare = compare
        self.bound(mod, SalesState, st.load_sales_data, month=month)
        return st


# 1 ---------------------------------------------------------------------------
class FourLevelsRequiredTests(Fixture):
    def assert_rejected(self, **fields):
        with self.assertRaises(ValidationError) as ctx:
            self.app.configure_month(self.admin, self.sid, "2026-10",
                                     **fields)
        self.assertEqual(str(ctx.exception), LEVELS_REQUIRED)

    def test_level1_without_uplift_rejected_and_not_written(self):
        self.assert_rejected(level1_base_cents=rm(200000))
        self.assert_rejected(level1_base_cents=rm(200000), level2_pct=0.1,
                             level3_pct=0.2)                  # L4 blank
        self.assert_rejected(level1_base_cents=rm(200000), level2_pct=0.1,
                             level3_pct=0.2, level4_pct=None)
        with self.assertRaises(ConfigNotFoundError):
            self.config()                                     # nothing saved

    def test_no_level1_target_may_be_saved(self):
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 stock_loss_target=0.015)
        cfg = self.config()
        self.assertIsNone(cfg.level1_base_cents)
        self.assertEqual(cfg.stock_loss_target, Decimal("0.015"))
        # uplifts without a Level 1 target are fine too
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level2_pct=0.1)
        self.assertEqual(self.config().level2_pct, Decimal("0.1"))

    def test_four_levels_saved_and_read_back(self):
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level1_base_cents=rm(200000),
                                 level2_pct=0.1, level3_pct=0.2,
                                 level4_pct=0.3)
        cfg = self.config()
        self.assertEqual((cfg.level1_base_cents, cfg.level2_pct,
                          cfg.level3_pct, cfg.level4_pct),
                         (rm(200000), Decimal("0.1"), Decimal("0.2"),
                          Decimal("0.3")))
        # a later save that blanks one uplift is rejected; values kept
        self.assert_rejected(level3_pct=None)
        self.assertEqual(self.config().level3_pct, Decimal("0.2"))
        # 0% is a set uplift (not blank)
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level2_pct=0)
        self.assertEqual(self.config().level2_pct, Decimal("0"))

    def test_setup_save_with_budgets_same_rule_atomic(self):
        cat = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                            sort_order=1)
        with self.assertRaises(ValidationError) as ctx:
            self.app.configure_month_with_budgets(
                self.admin, self.sid, "2026-10",
                {"level1_base_cents": rm(200000)}, {cat: 50000})
        self.assertEqual(str(ctx.exception), LEVELS_REQUIRED)
        self.assertEqual(self.app.get_expense_budgets(
            self.admin, self.sid, "2026-10"), {})

    def test_existing_month_without_uplift_not_changed(self):
        # saved before the rule (written past the service)
        self.app.write.repo.save_month_config(
            self.sid, "2026-10", level1_base_cents=rm(200000))
        # saves that touch no level field still work (payroll, stock)
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 salary_basic_allowance_cents=rm(10000),
                                 stock_loss_target=0.015)
        cfg = self.config()
        self.assertEqual(cfg.salary_basic_allowance_cents, rm(10000))
        self.assertIsNone(cfg.level2_pct)                     # unchanged
        # touching the Level 1 target requires all three uplifts
        self.assert_rejected(level1_base_cents=rm(210000))
        self.assertEqual(self.config().level1_base_cents, rm(200000))
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 level2_pct=0.1, level3_pct=0.2,
                                 level4_pct=0.3)
        self.assertEqual(self.config().level4_pct, Decimal("0.3"))


class MonthlySetupTests(Fixture):
    def setup_state(self):
        from ducklab.ops_stores_state import StoresState
        st = StoresState()
        st.setup_original = {}
        return st

    def save(self, st):
        import ducklab.ops_stores_state as mod
        from ducklab.ops_stores_state import StoresState
        self.bound(mod, StoresState, lambda: list(st.save_setup()))

    def test_level1_only_rejected_with_message(self):
        st = self.setup_state()
        st.s_level1 = "200000"
        self.save(st)
        self.assertEqual(st.setup_error, LEVELS_REQUIRED)
        with self.assertRaises(ConfigNotFoundError):
            self.config()

    def test_hq_defaults_fill_visibly_then_save(self):
        st = self.setup_state()
        st.s_level1 = "200000"
        st.use_hq_defaults()
        # filled into the form fields the user sees, not applied silently
        self.assertEqual((st.s_level2, st.s_level3, st.s_level4),
                         ("10", "20", "30"))
        with self.assertRaises(ConfigNotFoundError):
            self.config()                                 # not saved yet
        self.save(st)
        self.assertEqual(st.setup_error, "")
        self.assertEqual(st.setup_notice, "Monthly setup saved.")
        cfg = self.config()
        self.assertEqual((cfg.level2_pct, cfg.level3_pct, cfg.level4_pct),
                         (Decimal("0.1"), Decimal("0.2"), Decimal("0.3")))

    def test_warning_chip_for_month_without_uplift(self):
        import ducklab.ops_stores_state as mod
        from ducklab.ops_stores_state import StoresState
        self.app.write.repo.save_month_config(
            self.sid, "2026-10", level1_base_cents=rm(200000))
        st = self.setup_state()
        self.bound(mod, StoresState, st.load_page_data)
        self.assertTrue(st.setup_levels_missing)
        self.assertEqual([lv["level"] for lv in st.setup_levels],
                         ["Level 1", "Level 2", "Level 3", "Level 4"])
        self.assertEqual(ds.LEVELS_MISSING,
                         "Levels 2–4 missing in Monthly Setup")

    def test_sales_page_flags_month_without_uplift(self):
        self.app.write.repo.save_month_config(
            self.sid, "2026-10", level1_base_cents=rm(200000))
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(1000))
        st = self.sales("2026-10")
        self.assertTrue(st.sp_levels_missing)
        self.assertEqual(st.sp_level, "Below Level 1")      # never "of 1"
        self.assertNotIn("of 1", st.sp_level)


# 2 ---------------------------------------------------------------------------
class CompareSelectorTests(Fixture):
    def test_default_level1(self):
        self.october()
        st = self.sales("2026-10")
        self.assertEqual(st.sp_compare, "1")
        # 213,900 / 200,000 = 106.95%; MTD 69,000 / 200,000 = 34.5%
        self.assertEqual(st.sp_l1_achievement_label,
                         "Projected achievement vs Level 1")
        self.assertEqual(st.sp_l1_achievement, "106.9%")
        self.assertEqual(st.sp_l1_achievement_sub,
                         "Projected gap RM 13,900.00")
        self.assertEqual(st.sp_mtd_achievement_label,
                         "Achievement to date vs Level 1")
        self.assertEqual(st.sp_mtd_achievement, "34.5%")
        self.assertEqual(st.sp_proj_status, "Projected: Achieved")
        # pace chart: Level 1 is the strong line
        l1, _, l2 = st.sp_fig_dark.data[:3]
        self.assertGreater(l1.line.width, l2.line.width)

    def test_level3_drives_tiles_gap_needed_and_chart(self):
        self.october()
        st = self.sales("2026-10", compare="3")
        # 213,900 / 220,000 = 97.23%; gap -6,100; MTD 69,000 / 220,000
        self.assertEqual(st.sp_l1_achievement_label,
                         "Projected achievement vs Level 3")
        self.assertEqual(st.sp_l1_achievement, "97.2%")
        self.assertEqual(st.sp_l1_achievement_sub,
                         "Projected gap −RM 6,100.00")
        self.assertEqual(st.sp_proj_status, "Projected: Below target")
        self.assertEqual(st.sp_mtd_achievement_label,
                         "Achievement to date vs Level 3")
        # 69,000 / 220,000 = 31.36% -> 31.3% (below target, rounded down)
        self.assertEqual(st.sp_mtd_achievement, "31.3%")
        self.assertEqual(st.sp_mtd_achievement_sub,
                         "Level 3 target RM 220,000")
        # (220,000 - 69,000) / 21 = 7,190.48; run-rate 220,000 / 31
        self.assertEqual(st.sp_needed_label,
                         "Needed per remaining day for Level 3")
        self.assertEqual(st.sp_needed, "RM 7,190")
        self.assertEqual(st.sp_needed_sub,
                         "Level 3 required run-rate: RM 7,097")
        # highlighted line = Level 3 (strong, in the legend)
        by_name = {t.name: t for t in st.sp_fig_dark.data}
        l1, l3 = by_name["Level 1"], by_name["Target"]   # P1 legend
        self.assertGreater(l3.line.width, l1.line.width)
        self.assertTrue(l3.showlegend)
        self.assertFalse(l1.showlegend)
        # the level reached tile and the ladder do not depend on it
        self.assertEqual(st.sp_level, "Level 2")
        self.assertEqual(len(st.sp_ladder), 4)
        # the daily vs-budget column stays on Level 1 (budget achievement)
        # 6,900 - 200,000 / 31 = 448.387 (truncated to the cent, as before)
        self.assertEqual(st.sp_rows[0]["vs_budget"], "RM 448.38")

    def test_select_compare_validates_and_remembers(self):
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        events = list(st.select_compare("4"))
        self.assertEqual(st.sp_compare, "4")
        self.assertEqual(len(events), 1)                     # reload
        list(st.select_compare("9"))
        self.assertEqual(st.sp_compare, "1")
        # remembered per viewer: browser localStorage, default Level 1
        import reflex as rx
        default = SalesState.get_fields()["sp_compare"].default
        self.assertIsInstance(default, rx.LocalStorage)
        self.assertEqual((str(default), default.name),
                         ("1", "ducklab_sales_compare"))

    def test_overview_stays_on_level1(self):
        import ducklab.ops_overview_state as ov_mod
        from ducklab.ops_overview_state import compute_overview
        self.october()
        with mock.patch.object(ov_mod, "app_service", self.app):
            ov = compute_overview(self.admin, self.sid, "2026-10", TODAY)
        self.assertTrue(ov["pill_sales"]["context"].endswith(
            "of Level 1 · Projected: Level 2"))
        l1, _, l2 = ov["pace_fig_dark"].data[:3]
        self.assertGreater(l1.line.width, l2.line.width)


# 3 ---------------------------------------------------------------------------
class CompleteMonthTests(Fixture):
    def test_one_achievement_tile_no_projection_no_dash(self):
        self.september()
        st = self.sales("2026-09")
        self.assertTrue(st.sp_complete)
        # 350,819 / 269,861 = 130.0%; gap +80,958
        self.assertEqual(st.sp_l1_achievement_label,
                         "Achievement vs Level 1")
        self.assertEqual(st.sp_l1_achievement, "130.0%")
        self.assertEqual(st.sp_l1_achievement_sub, "Gap RM 80,958.00")
        # S3: the engine status chip (actual: semantic colour)
        self.assertEqual((st.sp_proj_status, st.sp_proj_color),
                         ("Achieved", "green"))
        # S3: no "Needed per remaining day" tile for a complete month
        for value in (st.sp_sales_mtd, st.sp_month_end, st.sp_l1_achievement,
                      st.sp_level, st.sp_avg_day):
            self.assertNotEqual(value, "—")

    def test_level3_selected(self):
        self.september()
        st = self.sales("2026-09", compare="3")
        # 350,819 / 323,833.20 = 108.33%; gap 26,985.80
        self.assertEqual(st.sp_l1_achievement_label,
                         "Achievement vs Level 3")
        self.assertEqual(st.sp_l1_achievement, "108.3%")
        self.assertEqual(st.sp_l1_achievement_sub, "Gap RM 26,985.80")
        self.assertEqual(st.sp_proj_status, "Achieved")

    def test_incomplete_month_has_both_achievement_tiles(self):
        self.october()
        st = self.sales("2026-10", compare="2")
        self.assertFalse(st.sp_complete)
        self.assertEqual(st.sp_mtd_achievement_label,
                         "Achievement to date vs Level 2")
        self.assertEqual(st.sp_l1_achievement_label,
                         "Projected achievement vs Level 2")


# 4 ---------------------------------------------------------------------------
class LevelReachedSubLineTests(Fixture):
    def test_achievement_vs_next_level(self):
        self.september()
        st = self.sales("2026-09", compare="2")
        self.assertEqual(st.sp_level, "Level 3")      # unchanged
        self.assertEqual(st.sp_level_sub, "RM 0.30 short of Level 4")
        self.assertEqual(st.sp_level_sub2, "99.9% of Level 4")   # S3 floor

    def test_no_next_level_no_sub_line(self):
        self.september()
        self.app.configure_month(self.admin, self.sid, "2026-09",
                                 level4_pct=0.25)  # L4 337,326.25
        st = self.sales("2026-09")
        self.assertEqual(st.sp_level, "Level 4")
        self.assertEqual(st.sp_level_sub, "All levels reached")
        self.assertEqual(st.sp_level_sub2, "")


class PagesBuildTests(unittest.TestCase):
    def test_changed_pages_compile(self):
        # event-handler signatures are checked when a page is built
        # (e.g. on_change of the Compare against control)
        from ducklab.ops_pages import dashboard_page
        from ducklab.ops_sales_pages import sales_page
        from ducklab.ops_stores_pages import stores_page
        for page in (sales_page, stores_page, dashboard_page):
            self.assertIsNotNone(page())


if __name__ == "__main__":
    unittest.main()
