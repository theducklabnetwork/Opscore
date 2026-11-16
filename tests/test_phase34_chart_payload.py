"""Phase 3.4: active-theme chart payloads and per-session source reuse."""

import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest import mock

from reflex.utils import serializers

from ducklab import ops_charts
from ducklab.auth_pages import AuthState
from ducklab.ops_overview_state import OverviewState, compute_overview
from ducklab.ops_sales_state import SalesState
from tests.test_daily_targets_t1 import Fixture, rm
import ducklab.ops_overview_state as overview_module
import ducklab.ops_sales_state as sales_module


def _data(fig):
    """Chart values/labels, excluding theme-only colors and fills."""
    ignored = {"fillcolor", "line", "marker", "marker_color", "colorscale"}
    return [{k: v for k, v in trace.items() if k not in ignored}
            for trace in fig.to_plotly_json()["data"]]


def _serialized_size(state):
    return len(json.dumps(state.dict(), default=serializers.serialize).encode())


class ChartPayloadTests(Fixture):
    def configure(self, month="2026-07", payroll=False):
        self.app.configure_month(
            self.admin, self.sid, month, level1_base_cents=rm(300000),
            min_rtm_target_cents=rm(1000), level2_pct=.1, level3_pct=.2,
            level4_pct=.3, salary_basic_allowance_cents=rm(5000) if payroll else None)

    def sales(self, month="2026-07", user=None):
        state = SalesState()
        state.is_dark = True  # these fixtures exercise the dark payload
        user = user or self.admin
        with mock.patch.object(sales_module, "app_service", self.app), \
             mock.patch.object(SalesState, "_actor", lambda _: user), \
             mock.patch.object(SalesState, "_selected_store_id", lambda _: self.sid), \
             mock.patch.object(SalesState, "_selected_month", lambda _: month):
            state.load_sales_data()
        self.assertEqual(state.sp_error, "")
        return state

    def overview(self, month="2026-07", dark=True):
        with mock.patch.object(overview_module, "app_service", self.app):
            return compute_overview(self.admin, self.sid, month,
                                    date(2026, 7, 31), dark=dark)

    def test_sales_reuses_private_chart_data_and_only_active_theme(self):
        self.configure()
        self.insert_shares()
        for day in range(1, 5):
            self.app.record_daily(self.admin, self.sid, date(2026, 7, day),
                                  sales_cents=rm(day * 1000))
        state = self.sales()
        serialized = json.dumps(state.dict(), default=serializers.serialize)
        self.assertNotIn("_chart_inputs", state.base_vars)
        self.assertNotIn("sp_chart", state.base_vars)
        self.assertNotIn("sp_chart_rx_state_", serialized)
        for name in ("sp_fig", "sp_dvt", "sp_weekday", "sp_cal"):
            self.assertTrue(getattr(state, name + "_dark").data)
            self.assertIsNone(getattr(state, name + "_light"))
        with mock.patch.object(ops_charts, "pace_chart",
                               wraps=ops_charts.pace_chart) as pace, \
             mock.patch.object(self.app, "preview_month",
                               side_effect=AssertionError("theme must not reload data")):
            state.restyle_charts(False)
        pace.assert_called_once()
        state.restyle_charts(True)
        dark_data = _data(state.sp_fig_dark)
        state.restyle_charts(False)
        self.assertIsNone(state.sp_fig_dark)
        self.assertEqual(_data(state.sp_fig_light), dark_data)
        self.assertTrue(state.sp_dvt_light.data)
        self.assertIsNone(state.sp_dvt_dark)
        state.restyle_charts(True)
        self.assertTrue(state.sp_fig_dark.data)

    def test_dashboard_active_theme_has_same_values_and_no_source_in_state(self):
        self.configure(payroll=True)
        for day in range(1, 5):
            self.app.record_daily(self.admin, self.sid, date(2026, 7, day),
                                  sales_cents=rm(day * 1000))
        dark = self.overview()
        light = self.overview(dark=False)
        self.assertNotIn("_chart_inputs", OverviewState.base_vars)
        for name in ("pace_fig", "spark_sales", "spark_stock"):
            self.assertTrue(dark[name + "_dark"].data)
            self.assertIsNone(dark[name + "_light"])
            self.assertEqual(_data(dark[name + "_dark"]),
                             _data(light[name + "_light"]))
        self.assertEqual(dark["pill_manpower"]["value"], "50.0%")

    def test_dashboard_theme_restyle_does_not_reload_records(self):
        self.configure()
        for day in range(1, 4):
            self.app.record_daily(self.admin, self.sid, date(2026, 7, day),
                                  sales_cents=rm(1000))
        state = OverviewState()
        with mock.patch.object(overview_module, "app_service", self.app), \
             mock.patch.object(OverviewState, "_actor", lambda _: self.admin), \
             mock.patch.object(OverviewState, "_selected_store_id", lambda _: self.sid), \
             mock.patch.object(OverviewState, "_selected_month", lambda _: "2026-07"):
            state.load_overview_data()
            with mock.patch.object(self.app, "preview_month",
                                   side_effect=AssertionError("theme must not reload data")):
                state.restyle_charts(False)
        self.assertIsNone(state.pace_fig_dark)
        self.assertTrue(state.pace_fig_light.data)
        self.assertNotIn("_chart_inputs", state.base_vars)

    def test_empty_incomplete_and_missing_payroll_keep_chart_and_kpi_rules(self):
        self.configure(payroll=False)
        state = self.sales()
        self.assertFalse(state.sp_chart_ready or state.sp_dvt_ready
                         or state.sp_weekday_ready or state.sp_cal_ready)
        self.assertTrue(state.sp_fig_dark.data)  # budget pace, no actual points
        self.assertTrue(all(v is None for v in state.sp_fig_dark.data[0].y))
        self.app.record_daily(self.admin, self.sid, date(2026, 7, 1),
                              sales_cents=None)
        self.app.record_daily(self.admin, self.sid, date(2026, 7, 2),
                              sales_cents=0)
        state = self.sales()
        self.assertFalse(state.sp_chart_ready)
        self.assertEqual(list(state.sp_fig_dark.data[0].y), [0, 0] + [None] * 29)
        overview = self.overview()
        self.assertEqual(overview["pill_manpower"]["value"], "—")
        self.assertIsNone(overview["_chart_inputs"]["days"][0].sales_cents)
        self.assertEqual(overview["_chart_inputs"]["days"][1].sales_cents, 0)

    def test_operational_record_changes_reload_and_theme_does_not_reuse_them(self):
        self.configure()
        self.app.record_daily(self.admin, self.sid, date(2026, 7, 1),
                              sales_cents=rm(1000))
        state = self.sales()
        self.assertEqual(state.sp_fig_dark.data[0].y[0], 1000)
        self.app.record_daily(self.admin, self.sid, date(2026, 7, 1),
                              sales_cents=rm(2000))
        state.restyle_charts(False)
        self.assertEqual(state.sp_fig_light.data[0].y[0], 1000)
        state = self.sales()
        self.assertEqual(state.sp_fig_dark.data[0].y[0], 2000)

    def test_store_month_and_user_changes_do_not_share_private_sources(self):
        self.configure()
        self.app.record_daily(self.admin, self.sid, date(2026, 7, 1),
                              sales_cents=rm(1000))
        first = self.sales()
        second = self.sales()
        self.assertIsNot(first._chart_source(), second._chart_source())
        self.assertEqual(second.sp_fig_dark.data[0].y[0], 1000)

        other_store = self.app.register_store(self.admin, "B01", "Beta")
        self.app.configure_month(
            self.admin, other_store, "2026-07", level1_base_cents=rm(300000),
            min_rtm_target_cents=rm(1000), level2_pct=.1, level3_pct=.2,
            level4_pct=.3)
        self.app.record_daily(self.admin, other_store, date(2026, 7, 1),
                              sales_cents=rm(9000))
        selected = {"store": self.sid, "month": "2026-07", "user": self.admin}
        state = SalesState()
        state.is_dark = True
        with mock.patch.object(sales_module, "app_service", self.app), \
             mock.patch.object(SalesState, "_actor", lambda _: selected["user"]), \
             mock.patch.object(SalesState, "_selected_store_id",
                               lambda _: selected["store"]), \
             mock.patch.object(SalesState, "_selected_month",
                               lambda _: selected["month"]):
            state.load_sales_data()
            selected["store"] = other_store
            state.load_sales_data()
            self.assertEqual(state.sp_fig_dark.data[0].y[0], 9000)
            self.configure("2026-08")
            self.app.record_daily(self.admin, self.sid, date(2026, 8, 1),
                                  sales_cents=rm(5000))
            selected.update(store=self.sid, month="2026-08")
            state.load_sales_data()
            self.assertEqual(state.sp_fig_dark.data[0].y[0], 5000)
            self.app.record_daily(self.admin, self.sid, date(2026, 7, 1),
                                  sales_cents=rm(7000))
            selected["month"] = "2026-07"
            state.load_sales_data()
            self.assertEqual(state.sp_fig_dark.data[0].y[0], 7000)
            other_admin = self.app.create_user(
                self.admin, "other@example.com", "Other", "Sup3rSecure!x")
            self.app.assign_role(self.admin, other_admin.id, "super_admin")
            for store in (self.sid, other_store):
                self.app.assign_store(self.admin, other_admin.id, store)
            selected["user"] = other_admin
            state.load_sales_data()
            self.assertIsNone(state.sp_fig_dark)
            self.assertNotEqual(state.sp_error, "")
        state._clear_sales()
        self.assertEqual(state._chart_source(), {})


class ThemeToggleTests(unittest.IsolatedAsyncioTestCase):
    async def test_resolved_browser_theme_syncs_charts_both_directions(self):
        children = {
            OverviewState: SimpleNamespace(calls=[], _apply_chart_theme=None),
            SalesState: SimpleNamespace(calls=[], _apply_chart_theme=None),
        }
        for child in children.values():
            child._apply_chart_theme = child.calls.append

        async def get_state(_self, cls):
            return children[cls]

        with mock.patch.object(AuthState, "get_state", get_state), \
             mock.patch.object(AuthState, "_mark_dirty"):
            auth = AuthState()
            await auth.sync_is_dark("dark")
            await auth.sync_is_dark("dark")  # unchanged: no redundant rebuild
            await auth.sync_is_dark("light")
        self.assertFalse(auth.is_dark)
        for child in children.values():
            self.assertEqual(child.calls, [True, False])


if __name__ == "__main__":
    unittest.main()
