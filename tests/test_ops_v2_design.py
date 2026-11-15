"""V2 tests: V1 fixes (target ladder collapse, "No reference" without a
projection prefix, one projection chip, "Record a day" target), Stock and
Manpower on the kit (collapsed days, chart threshold, numeric day axis,
salary ratio "Month to date", no duplicated groups). Presentation only.
Disposable temp DBs only.

Run: python -m pytest tests/test_ops_v2_design.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab import ops_charts
from ducklab.app_service import AppService


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    config = dict(level1_base_cents=rm(210000), level2_pct=0.1,
                  level3_pct=0.2, level4_pct=0.3, opening_headcount_ft=10,
                  stock_loss_target=0.015, salary_ratio_target=0.06818,
                  part_timer_factor=0.5,
                  salary_basic_allowance_cents=rm(10500))

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "v2.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(self.admin, self.sid, "2026-10",
                                 **self.config)

    def tearDown(self):
        self._tmp.cleanup()

    def record(self, day, **fields):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, day),
                              **fields)

    def load(self, modname, clsname, loader):
        import importlib
        mod = importlib.import_module(f"ducklab.{modname}")
        cls = getattr(mod, clsname)
        sid, admin = self.sid, self.admin
        patches = [
            mock.patch.object(mod, "app_service", self.app),
            mock.patch.object(cls, "_actor", lambda self: admin),
            mock.patch.object(cls, "_selected_store_id", lambda self: sid),
            mock.patch.object(cls, "_selected_month",
                              lambda self: "2026-10"),
        ]
        st = cls()
        st.has_stores = True
        for p in patches:
            p.start()
        try:
            gen = getattr(st, loader)()
            if gen is not None and hasattr(gen, "__next__"):
                for _ in gen:
                    pass
        finally:
            for p in reversed(patches):
                p.stop()
        return st


class ProjectionRuleTests(unittest.TestCase):
    def test_no_reference_has_no_prefix_or_tag(self):
        for state in ("projected", "early_estimate", "actual"):
            self.assertEqual(ds.projection_style(state, "No reference"),
                             ("No reference", "gray", ""))
        # other statuses keep the projection label
        self.assertEqual(ds.projection_style("early_estimate", "Growth"),
                         ("Projected: Growth", "amber", "Early estimate"))


class SalesLadderTests(Fixture):
    def test_month_without_uplift_keeps_four_levels_with_warning(self):
        # S2: no collapse — a month saved before four levels were required
        # shows all four levels (workbook rule) and the warning chip
        self.app.write.repo.save_month_config(
            self.sid, "2026-10", level2_pct=None, level3_pct=None,
            level4_pct=None)
        self.record(1, sales_cents=rm(12000))
        st = self.load("ops_sales_state", "SalesState", "load_sales_data")
        self.assertTrue(st.sp_levels_missing)
        self.assertEqual([r["level"] for r in st.sp_ladder],
                         ["Level 1", "Level 2", "Level 3", "Level 4"])
        # missing references: plain "No reference", no tag
        self.assertEqual([(r["status"], r["early_tag"])
                          for r in st.sp_comparisons],
                         [("No reference", ""), ("No reference", "")])
        # month-end tile: plain sub-label; chip lives on projected achv.
        self.assertEqual(st.sp_month_end_sub, "Projected")
        self.assertEqual(st.sp_proj_status, "Projected: Achieved")

    def test_all_levels_with_uplift(self):
        self.record(1, sales_cents=rm(12000))
        st = self.load("ops_sales_state", "SalesState", "load_sales_data")
        self.assertFalse(st.sp_levels_missing)
        self.assertEqual(len(st.sp_ladder), 4)


class RecordDayTests(Fixture):
    def _st(self, active=True):
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        st.selected_store_active = active
        return st

    def _rows(self, reported):
        return [{"iso": f"2026-10-{d:02d}",
                 "status": "Complete" if d in reported else "Not Reported"}
                for d in range(1, 32)]

    def test_today_when_in_month_and_open(self):
        st = self._st()
        with mock.patch("ducklab.kpi.kl_today",
                        return_value=date(2026, 10, 22)):
            st._set_record_day(self._rows({1, 2}), True)
        self.assertEqual(st.record_day_iso, "2026-10-22")
        self.assertTrue(st.record_day_enabled)
        self.assertIn("22 October", st.record_day_tip)

    def test_first_unreported_when_today_entered_or_outside(self):
        st = self._st()
        with mock.patch("ducklab.kpi.kl_today",
                        return_value=date(2026, 10, 2)):
            st._set_record_day(self._rows({1, 2}), True)
        self.assertEqual(st.record_day_iso, "2026-10-03")
        with mock.patch("ducklab.kpi.kl_today",
                        return_value=date(2026, 11, 5)):
            st._set_record_day(self._rows({1}), True)
        self.assertEqual(st.record_day_iso, "2026-10-02")

    def test_disabled_for_read_only_and_deactivated(self):
        st = self._st()
        st._set_record_day(self._rows(set()), False)
        self.assertFalse(st.record_day_enabled)
        self.assertIn("read-only", st.record_day_tip)
        st = self._st(active=False)
        st._set_record_day(self._rows(set()), True)
        self.assertFalse(st.record_day_enabled)
        self.assertIn("deactivated", st.record_day_tip)

    def test_disabled_when_every_day_entered(self):
        st = self._st()
        st._set_record_day(self._rows(set(range(1, 32))), True)
        self.assertFalse(st.record_day_enabled)
        self.assertEqual(st.record_day_iso, "")


class StockPageTests(Fixture):
    def test_collapsed_days_and_chart_threshold(self):
        self.record(1, sales_cents=rm(1000), stock_losses={"DMO": rm(5)})
        self.record(2, sales_cents=rm(1000), stock_losses={"DMO": rm(5)})
        st = self.load("ops_stock_state", "StockState", "load_stock_data")
        self.assertEqual(len(st.day_rows), 31)
        self.assertEqual(len(st.st_rows_reported), 2)
        self.assertEqual(st.st_hidden_label, "29 days not reported")
        self.assertFalse(st.st_chart_ready)
        self.record(3, sales_cents=rm(1000), stock_losses={"DMC": rm(1)})
        st = self.load("ops_stock_state", "StockState", "load_stock_data")
        self.assertTrue(st.st_chart_ready)

    def test_trend_uses_numeric_day_axis(self):
        fig = ops_charts.daily_line(["01 Oct", "02 Oct"], [1.0, None],
                                    dark=True, day_numbers=[1, 2])
        self.assertEqual(list(fig.data[0].x), [1, 2])
        self.assertEqual(fig.layout.xaxis.title.text, "Day")
        self.assertEqual(list(fig.layout.xaxis.range), [0.5, 2.5])


class ManpowerPageTests(Fixture):
    def test_month_to_date_ratio_label(self):
        self.record(1, sales_cents=rm(12000), ft_working=8, pt_working=1)
        st = self.load("ops_manpower_state", "ManpowerState",
                       "load_manpower_data")
        self.assertTrue(st.pay_kpis["status"].startswith("Month to date: "))
        self.assertEqual(st.pay_kpis["mtd"], "1")
        # A partial month shows the numeric ratio without a red/green verdict.
        self.assertEqual(st.pay_kpis["status_color"], "gray")
        self.assertEqual(st.pay_kpis["bar_color"], "gray")

    def test_each_figure_once_and_collapsed_days(self):
        self.record(1, sales_cents=rm(12000), ft_working=8, pt_working=1,
                    leave_annual=1)
        self.record(2, ft_working=8)
        st = self.load("ops_manpower_state", "ManpowerState",
                       "load_manpower_data")
        self.assertFalse(hasattr(st, "prod_kpis"))
        self.assertFalse(hasattr(st, "move_kpis"))
        self.assertIn("turnover", st.hc_kpis)
        self.assertIn("sales_mtd", st.eff_kpis)
        self.assertEqual([r["status"] for r in st.staff_rows_reported],
                         ["Complete", "Partial"])
        self.assertEqual(st.staff_hidden_count, 29)


if __name__ == "__main__":
    unittest.main()
