"""Shared interaction accessibility: presentation only, no database."""

from pathlib import Path
import unittest

import reflex as rx

from ducklab import ui


ROOT = Path(__file__).resolve().parent.parent


class GlobalInteractionTests(unittest.TestCase):
    def test_stylesheet_is_registered_and_covers_focus_motion_touch(self):
        from ducklab.ducklab import app

        self.assertIn("/accessibility.css", app.stylesheets)
        css = (ROOT / "assets/accessibility.css").read_text()
        self.assertIn(":focus-visible", css)
        self.assertIn("var(--blue-9)", css)
        self.assertIn("(pointer: coarse)", css)
        self.assertIn("min-width: 44px", css)
        self.assertIn("min-height: 44px", css)
        self.assertIn("prefers-reduced-motion: reduce", css)


class ExplanationTests(unittest.TestCase):
    def test_operational_acronyms_have_focusable_explanations(self):
        for label, phrase in (("BV", "sales per transaction"),
                              ("UPT", "units sold divided by transactions"),
                              ("ASP", "sales divided by units sold"),
                              ("SSSG", "Same-Store Sales Growth")):
            out = str(ui.metric_label(label))
            self.assertIn(phrase, out)
            self.assertIn("RadixThemesTooltip", out)
            self.assertIn("RadixThemesPopover.Trigger", out)
            self.assertIn('type:"button"', out)

    def test_unknown_metric_is_unchanged(self):
        out = str(ui.metric_label("Sales"))
        self.assertNotIn("Tooltip", out)

    def test_select_label_is_on_focusable_trigger(self):
        out = str(ui.labelled_select(
            ["A", "B"], value="A", aria_label="Store", id="store"))
        self.assertIn('RadixThemesSelect.Trigger,{"aria-label":"Store"', out)
        self.assertNotIn('RadixThemesSelect.Root,{"aria-label"', out)

    def test_compact_value_is_focusable_and_has_exact_name(self):
        out = str(ui.stat("Sales", "RM 12,345.67", compact="RM 12.3k"))
        self.assertIn('tabIndex:0', out)
        self.assertIn('aria-label', out)
        self.assertIn("RM 12,345.67", out)


class FeedbackTests(unittest.TestCase):
    def test_success_callout_is_politely_announced(self):
        out = str(ui.success_callout("Saved", id="saved"))
        self.assertIn('role:"status"', out)
        self.assertIn('"aria-live":"polite"', out)

    def test_empty_state_can_supply_action(self):
        out = str(ui.empty_state(
            "No Staff", "Add staff before planning.",
            action=rx.link("Add Staff", href="/ops/manpower")))
        self.assertIn('role:"status"', out)
        self.assertIn("Add Staff", out)

    def test_permission_dependent_empty_states_are_reactive(self):
        from ducklab import ops_actions_pages, ops_schedule_pages

        for out in (str(ops_actions_pages._table()),
                    str(ops_schedule_pages._staff_tab())):
            self.assertIn("can_write", out)
            self.assertIn("No Staff" if "schedule-staff" in out
                          else "No Actions", out)

    def test_chart_has_name_and_concise_summary(self):
        import plotly.graph_objects as go
        from ducklab import ops_charts

        chart = ops_charts.chart(go.Figure(),
                                 description="Cumulative sales vs target")
        self.assertIn('role:"img"', str(chart))
        self.assertIn("Cumulative sales vs target", str(chart))
        card = ui.chart_card("Sales Trend", chart, ready=True,
                             summary="Exact values are in the table below.")
        self.assertIn('role:"note"', str(card))
        self.assertIn("Exact values are in the table below.", str(card))

    def test_schedule_below_target_does_not_depend_on_colour(self):
        from ducklab import ops_schedule_pages

        self.assertIn("Below target", str(ops_schedule_pages._summary_row()))
        cell = {"code": "M900", "date": "2026-11-16", "color": "#fff"}
        self.assertIn("Shift for ", str(
            ops_schedule_pages._schedule_cell("1", cell)))


if __name__ == "__main__":
    unittest.main()
