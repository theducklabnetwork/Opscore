"""V4 tests: phone tables (pinned Edit / right-edge fade, no hidden phone
columns), chart legends above the plot + zero categories omitted, neutral
treatment chips, report chip colours, and the kit entry dialogs keeping
their controls. Presentation only; no DB needed.

Run: python -m pytest tests/test_ops_v4_design.py -v
"""

import unittest

from ducklab import design as ds
from ducklab import ops_charts
from ducklab import ui


class PhoneTableTests(unittest.TestCase):
    def test_action_column_is_pinned(self):
        """A last column labelled "" is the row action: the table carries
        has-act (sticky last cell) and no right-edge fade."""
        out = str(ui.data_table(["Date", ("Sales", True), ""]))
        self.assertIn('className:"has-act"', out)
        self.assertNotIn("linear-gradient", out)

    def test_table_without_action_gets_fade(self):
        out = str(ui.data_table(["Metric", ("Value", True)]))
        self.assertNotIn('className:"has-act"', out)
        self.assertIn("linear-gradient", out)

    def test_no_column_hides_on_phones(self):
        css = str(ui._TABLE_STYLE)
        self.assertNotIn("wide-only", css)
        # xl-only columns hide only between tablet and 1536px, never on
        # phones (< 768px), and come with a hint
        self.assertIn("(min-width: 768px) and (max-width: 1535px)", css)
        out = str(ui.data_table(["A", ("B", False, True), ""],
                                hidden_note="B is in the dialog."))
        self.assertIn("B is in the dialog.", out)

    def test_listed_tables_pin_edit(self):
        from ducklab import (ops_actions_pages, ops_daily_pages,
                             ops_expenses_pages, ops_sales_pages,
                             ops_stock_pages)
        for cols in (ops_daily_pages._COLS, ops_stock_pages._DAY_COLS,
                     ops_actions_pages._COLS, ops_expenses_pages._REG_COLS,
                     ops_sales_pages._DAILY_COLS):
            self.assertEqual(cols[-1], "", cols)


class ChartTests(unittest.TestCase):
    def test_legend_sits_above_the_plot(self):
        for fig in (ops_charts.budget_vs_actual(["Water"], [100], [50],
                                                dark=True),
                    ops_charts.pace_chart([1, 2], [100, None], [50, 100],
                                          dark=False)):
            legend = fig.layout.legend
            self.assertGreater(legend.y, 1)
            self.assertEqual(legend.yanchor, "bottom")

    def test_long_category_labels_wrap(self):
        self.assertEqual(ops_charts.wrap_label("Water"), "Water")
        self.assertEqual(ops_charts.wrap_label("Repairs & Maintenance"),
                         "Repairs &<br>Maintenance")
        # P1: the budget chart is horizontal, so the full name reads on
        # the category axis without wrapping or rotation
        fig = ops_charts.budget_vs_actual(["Repairs & Maintenance"], [None],
                                          [5000], dark=True)
        self.assertEqual(list(fig.data[1].y), ["Repairs & Maintenance"])
        self.assertEqual(fig.data[1].orientation, "h")

    def test_composition_omits_zero_categories(self):
        items = [
            {"code": "DMO", "name": "Damaged", "total_cents": 7500,
             "sign": 1},
            {"code": "DMC", "name": "Damaged C", "total_cents": 0,
             "sign": 1},
            {"code": "EXTRA", "name": "Extra", "total_cents": -1000,
             "sign": -1},
            {"code": "DPI", "name": "Info", "total_cents": 0, "sign": 0},
        ]
        fig = ops_charts.composition(items, dark=True)
        codes = [c for trace in fig.data for c in trace.x]
        self.assertEqual(codes, ["DMO", "EXTRA"])
        self.assertEqual([t.name for t in fig.data],
                         ["Total Loss", "Offset"])


class ChipTests(unittest.TestCase):
    def test_included_is_neutral(self):
        self.assertEqual(ds.status_color("Included"), "gray")

    def test_report_chip_colours(self):
        from ducklab.ops_state import OpsState
        st = OpsState()
        st.report = {"sales": {"status_l1": "Projected: Achieved"},
                     "manpower": {"ratio_status": "Failure: over target"},
                     "stock": {"status": "Within target"}}
        st.report_categories = [{"name": "Water", "status": "OVER BUDGET"}]
        self.assertEqual(st.report_colors, {"sales": "amber",
                                            "manpower": "red",
                                            "stock": "green",
                                            "expenses": "gray"})
        self.assertEqual(st.report_category_rows[0]["color"], "red")
        st.report = {"sales": {"status_l1": "Achieved"}}
        self.assertEqual(st.report_colors["sales"], "green")


class DialogTests(unittest.TestCase):
    """Every entry dialog is built on ui.form_dialog and keeps the ids the
    browser suites drive (behaviour and fields unchanged)."""

    def test_dialogs_keep_controls(self):
        from ducklab import (ops_actions_pages, ops_daily_pages,
                             ops_expenses_pages, ops_manpower_pages,
                             ops_stores_pages)
        cases = {
            ops_daily_pages._entry_dialog: (
                "entry-close", "entry-cancel", "entry-save", "f-sales",
                "f-leave-unpaid", "Clear saved value", "Unsaved changes"),
            ops_expenses_pages._expense_dialog: (
                "exp-close", "exp-cancel", "exp-save", "e-paid-by",
                "e-remarks"),
            ops_actions_pages._dialog: (
                "a-close", "a-cancel", "a-save", "a-issue", "a-completion"),
            ops_manpower_pages._config_dialog: (
                "cfg-close", "cfg-cancel", "cfg-save", "c-opening",
                "p-support"),
            ops_stores_pages._store_form_dialog: (
                "store-form-close", "store-form-save", "sf-code", "sf-bm"),
        }
        for build, ids in cases.items():
            out = str(build())
            self.assertIn("form-section", out, build.__qualname__)
            for needle in ids:
                self.assertIn(needle, out, f"{build.__qualname__}: {needle}")

    def test_setup_form_keeps_clear_and_fields(self):
        from ducklab import ops_stores_pages
        from ducklab.ops_setup_state import SETUP_ALL
        out = str(ops_stores_pages._setup_section())
        for _, field, _, _ in SETUP_ALL:
            self.assertIn("setup-" + field.replace("_", "-"), out)
        self.assertIn("Clear saved value", out)


if __name__ == "__main__":
    unittest.main()
