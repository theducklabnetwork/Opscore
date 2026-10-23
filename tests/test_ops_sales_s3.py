"""S3 tests: Sales tile fixes after S2 (display only). The achievement
tile carries the selected level's engine status chip; a Below-target
percentage is rounded DOWN to 1 decimal everywhere it is shown (Sales
tile, target ladder, Level reached sub-line, Overview); selector labels;
no "Needed per remaining day" tile for a complete month; the Level
reached sub-line is dropped when its level is the selected one.
Expected values are hand-computed literals; disposable temp DBs only.

Run: python -m pytest tests/test_ops_sales_s3.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab.app_service import AppService

TODAY = date(2026, 10, 21)
WB_SALES_RM = [9681, 10511, 11478, 13309, 15022, 13256, 10901, 10228, 10903,
               11480, 11513, 15236, 14712, 9256, 10467, 11018, 10396, 11298,
               14494, 12701, 10875, 9589, 9627, 10726, 11724, 15098, 14818,
               9856, 9792, 10854]  # September 2026, total RM 350,819


def rm(v):
    return int(Decimal(str(v)) * 100)


# 2 ---------------------------------------------------------------------------
class ShortfallNeverRoundsUpTests(unittest.TestCase):
    def test_september_case(self):
        # 350,819 / 350,819.30 = 0.99999914... -> "99.9%", not "100.0%"
        ratio = Decimal(rm(350819)) / Decimal("35081930.0")
        self.assertEqual(ds.fmt_pct(ratio), "100.0%")         # plain rule
        self.assertEqual(ds.fmt_achievement(ratio, "Below target"), "99.9%")

    def test_only_below_target_rounds_down(self):
        f = ds.fmt_achievement
        self.assertEqual(f(Decimal("0.99999"), "Below target"), "99.9%")
        self.assertEqual(f(Decimal("0.0999"), "Below target"), "9.9%")
        self.assertEqual(f(Decimal("0.5"), "Below target"), "50.0%")
        # achieved / other statuses keep the usual half-up display
        self.assertEqual(f(Decimal("1.29996"), "Achieved"), "130.0%")
        self.assertEqual(f(Decimal("1"), "Achieved"), "100.0%")
        self.assertEqual(f(Decimal("0.99999"), "Awaiting data"), "100.0%")
        self.assertEqual(f(None, "Below target"), "—")


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "s3.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")

    def tearDown(self):
        self._tmp.cleanup()

    def september(self, base=rm(269861)):
        """Complete: L1 269,861 / L2 296,847.10 / L3 323,833.20 /
        L4 350,819.30; actual RM 350,819."""
        self.app.configure_month(
            self.admin, self.sid, "2026-09",
            min_rtm_target_cents=rm(200000), level1_base_cents=base,
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3)
        for i, v in enumerate(WB_SALES_RM, 1):
            self.app.record_daily(self.admin, self.sid, date(2026, 9, i),
                                  sales_cents=rm(v))

    def sales(self, month, compare):
        import ducklab.ops_sales_state as mod
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        st.sp_compare = compare
        sid, admin = self.sid, self.admin
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(SalesState, "_actor", lambda s_: admin), \
                mock.patch.object(SalesState, "_selected_store_id",
                                  lambda s_: sid), \
                mock.patch.object(SalesState, "_selected_month",
                                  lambda s_: month):
            st.load_sales_data()
        return st

    def overview(self, month):
        import ducklab.ops_overview_state as ov_mod
        from ducklab.ops_overview_state import compute_overview
        with mock.patch.object(ov_mod, "app_service", self.app):
            return compute_overview(self.admin, self.sid, month, TODAY)


# 1, 2, 5 ---------------------------------------------------------------------
class AchievementTileTests(Fixture):
    def test_complete_level4_selected(self):
        self.september()
        st = self.sales("2026-09", "4")
        self.assertEqual(st.sp_l1_achievement_label, "Achievement vs Level 4")
        self.assertEqual(st.sp_l1_achievement, "99.9%")
        self.assertEqual((st.sp_proj_status, st.sp_proj_color),
                         ("Below target", "red"))
        self.assertEqual(st.sp_l1_achievement_sub, "Gap −RM 0.30")
        # Level reached unchanged; "x% of Level 4" dropped (selected)
        self.assertEqual(st.sp_level, "Level 3 of 4")
        self.assertEqual(st.sp_level_sub, "RM 0.30 short of Level 4")
        self.assertEqual(st.sp_level_sub2, "")
        # target ladder: same rule
        self.assertEqual([r["achievement"] for r in st.sp_ladder],
                         ["130.0%", "118.2%", "108.3%", "99.9%"])

    def test_complete_level3_selected(self):
        self.september()
        st = self.sales("2026-09", "3")
        self.assertEqual(st.sp_l1_achievement_label, "Achievement vs Level 3")
        self.assertEqual(st.sp_l1_achievement, "108.3%")
        self.assertEqual((st.sp_proj_status, st.sp_proj_color),
                         ("Achieved", "green"))
        self.assertEqual(st.sp_level_sub2, "99.9% of Level 4")

    def test_incomplete_projection_labels(self):
        # Oct: L1 200,000 .. L4 230,000; 10 x 6,900 -> projected 213,900
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=rm(200000),
            level2_pct=0.05, level3_pct=0.10, level4_pct=0.15)
        for d in range(1, 11):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(6900))
        st = self.sales("2026-10", "3")
        # 213,900 / 220,000 = 97.227% -> 97.2% (down); projected chip
        self.assertEqual(st.sp_l1_achievement, "97.2%")
        self.assertEqual((st.sp_proj_status, st.sp_proj_color),
                         ("Projected: Below target", "amber"))
        st = self.sales("2026-10", "1")
        self.assertEqual(st.sp_proj_status, "Projected: Achieved")
        self.assertFalse(st.sp_complete)


class OverviewRoundingTests(Fixture):
    def test_targets_panel_and_pillar(self):
        # Level 1 = 350,819.30 (base in cents) -> 350,819 is 0.30 short
        self.september(base=35081930)
        ov = self.overview("2026-09")
        self.assertEqual(ov["targets"][0]["pct"], "99.9%")
        self.assertEqual(ov["targets"][0]["status"], "Below target")
        self.assertEqual(ov["pill_sales"]["context"],
                         "Projected RM 350.8k · 99.9% of Level 1 · "
                         "No level reached")
        self.assertEqual(ov["cc"]["proj_achievement"], "99.9%")

    def test_achieved_levels_unchanged(self):
        self.september()
        ov = self.overview("2026-09")
        self.assertEqual([t["pct"] for t in ov["targets"]],
                         ["130.0%", "118.2%", "108.3%", "99.9%"])


# 3, 4 ------------------------------------------------------------------------
class PageLayoutTests(unittest.TestCase):
    def test_selector_labels(self):
        from ducklab.ops_sales_pages import _compare_control
        page = str(_compare_control())
        for n in (1, 2, 3, 4):
            self.assertIn(f'"Level {n}"', page)   # wide screens
            self.assertIn(f'"L{n}"', page)        # phones

    def test_complete_month_has_no_needed_tile(self):
        from ducklab.ops_sales_pages import _kpi_tiles
        tiles = str(_kpi_tiles())
        # rendered once, in the incomplete-month layout only
        self.assertEqual(tiles.count("sales-needed"), 1)
        self.assertEqual(tiles.count("sales-mtd-achievement"), 1)
        # the last tile spans 2 columns on phones in both layouts
        self.assertEqual(tiles.count("sales-avg-day"), 2)


if __name__ == "__main__":
    unittest.main()
