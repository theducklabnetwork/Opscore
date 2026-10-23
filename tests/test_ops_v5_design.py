"""V5 tests: phone column sets, treatment labels (EXTRA is an Offset on
every page), the report's expenses empty states / salary "Month to date"
wording / single early-estimate chip, plain-language copy, "Level 1"
labels, Overview sparkline gating and compact money. Disposable temp DBs
only.

Run: python -m pytest tests/test_ops_v5_design.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab import ops_charts
from ducklab import ui
from ducklab.app_service import AppService
from ducklab.ops_state import _serialize_live, _serialize_snapshot

TODAY = date(2026, 10, 21)


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "v5.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        for month in ("2026-09", "2026-10"):
            self.app.configure_month(
                self.admin, self.sid, month, level1_base_cents=rm(210000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
                opening_headcount_ft=10, salary_ratio_target=0.06818,
                part_timer_factor=0.5, stock_loss_target=0.015,
                salary_basic_allowance_cents=rm(10500))

    def tearDown(self):
        self._tmp.cleanup()

    def day(self, d, month=10, **kw):
        self.app.record_daily(self.admin, self.sid, date(2026, month, d),
                              **kw)

    def live(self, month="2026-10", user=None):
        rep = self.app.preview_month(self.admin, self.sid, month,
                                     as_of=TODAY)
        return _serialize_live(rep, user=user or self.admin)

    def patched(self, *mods):
        """Point the given state modules (and auth_pages) at this DB."""
        import importlib
        from ducklab import auth_pages
        targets = [auth_pages] + [importlib.import_module(f"ducklab.{m}")
                                  for m in mods]
        return [mock.patch.object(t, "app_service", self.app)
                for t in targets]

    def bound(self, modname, clsname, fn_name, month="2026-10"):
        import importlib
        mod = importlib.import_module(f"ducklab.{modname}")
        cls = getattr(mod, clsname)
        sid, admin = self.sid, self.admin
        patches = self.patched(modname) + [
            mock.patch.object(cls, "_actor", lambda self: admin),
            mock.patch.object(cls, "_selected_store_id", lambda self: sid),
            mock.patch.object(cls, "_selected_month", lambda self: month),
        ]
        st = cls()
        st.has_stores = True
        for p in patches:
            p.start()
        try:
            out = getattr(st, fn_name)()
            if out is not None and hasattr(out, "__next__"):
                for _ in out:
                    pass
        finally:
            for p in reversed(patches):
                p.stop()
        return st


# ---------------------------------------------------------------------------
# 1. phone tables
# ---------------------------------------------------------------------------
PHONE_SETS = {
    # page module, table builder -> labels that must show at 390px
    ("ops_actions_pages", "_table"): ("Issue", "Priority", "Status", ""),
    ("ops_stock_pages", "_category_section"): ("Code", "Total",
                                               "Treatment"),
    ("ops_stock_pages", "_daily_section"): ("Date", "Total Loss", "Status",
                                            ""),
    ("ops_expenses_pages", "_register"): ("Incurred", "Description",
                                          "Amount", "Status", ""),
    ("ops_expenses_pages", "_budget_monitor"): ("Category", "Paid",
                                                "Status"),
    ("ops_sales_pages", "_daily_table"): ("Date", "Sales", "Status", ""),
    ("ops_daily_pages", "_records_table"): ("Date", "Sales", "Status", ""),
    ("ops_manpower_pages", "_staffing_table"): ("Date", "Effective",
                                                "Status", ""),
}


class PhoneTableTests(unittest.TestCase):
    def test_phone_style_hides_only_non_phone_columns(self):
        style = ui._phone_style(["A", ("B", True), "C", ""], ("A", ""))
        hidden = [k for k in style[ui.PHONE] if "nth-child" in k]
        self.assertEqual(sorted(hidden), [
            "& tr:not(.collapsed-row) > :nth-child(2)",
            "& tr:not(.collapsed-row) > :nth-child(3)"])
        # the shared phone rules (ph-only / ph-hide) are kept
        self.assertIn("& .ph-only", style[ui.PHONE])
        with self.assertRaises(ValueError):
            ui._phone_style(["A"], ("Nope",))

    def test_every_listed_table_has_its_phone_set(self):
        import importlib
        for (modname, fn), phone in PHONE_SETS.items():
            mod = importlib.import_module(f"ducklab.{modname}")
            seen = {}

            def spy(columns, *body, _orig=ui.data_table, **kw):
                seen["cols"], seen["phone"] = columns, kw.get("phone")
                return _orig(columns, *body, **kw)

            with mock.patch.object(mod.ui, "data_table", spy):
                getattr(mod, fn)()
            self.assertEqual(tuple(seen["phone"]), phone, fn)
            # 3-5 columns on a phone, Edit last when the table has one
            self.assertLessEqual(len(phone), 5, fn)
            if seen["cols"][-1] == "":
                self.assertEqual(phone[-1], "", fn)

    def test_hidden_column_data_moves_into_kept_cells(self):
        from ducklab import ops_actions_pages, ops_expenses_pages
        reg = str(ops_expenses_pages._register())
        self.assertIn("ph-only", reg)          # category under description
        acts = str(ops_actions_pages._table())
        self.assertIn("ph-only", acts)         # Overdue badge under issue
        self.assertIn("More columns on a wider screen", acts)


# ---------------------------------------------------------------------------
# 2. labels
# ---------------------------------------------------------------------------
class TreatmentTests(Fixture):
    def test_extra_is_offset_on_report_live_and_finalized(self):
        self.day(1, month=9, sales_cents=rm(9000),
                 stock_losses={"DMO": rm(75), "EXTRA": rm(10),
                               "DPI": rm(5)})
        live = {c["code"]: c for c in self.live("2026-09")["stock"]["codes"]}
        self.app.finalize_month(self.admin, self.sid, "2026-09")
        snap = self.app.get_finalized_report(self.admin, self.sid, "2026-09")
        final = {c["code"]: c for c in
                 _serialize_snapshot(snap, user=self.admin)["stock"]["codes"]}
        for codes in (live, final):
            self.assertEqual(codes["EXTRA"]["treatment"], "Offset")
            self.assertEqual(codes["EXTRA"]["treatment_color"], "green")
            self.assertEqual(codes["DMO"]["treatment"], "Included")
            self.assertEqual(codes["DMO"]["treatment_color"], "gray")
            self.assertEqual(codes["DPI"]["treatment"], "Info Only")
            self.assertEqual(codes["DPI"]["treatment_color"], "gray")

    def test_extra_is_offset_on_stock_page(self):
        self.day(1, sales_cents=rm(9000),
                 stock_losses={"DMO": rm(75), "EXTRA": rm(10)})
        st = self.bound("ops_stock_state", "StockState", "load_stock_data")
        rows = {r["code"]: r for r in st.cat_rows}
        self.assertEqual(rows["EXTRA"]["treatment"], "Offset")
        self.assertEqual(rows["EXTRA"]["treatment_color"], "green")
        self.assertEqual(rows["DMO"]["treatment"], "Included")

    def test_chip_colours(self):
        self.assertEqual({t: ds.status_color(t) for t in ds.TREATMENT.values()},
                         {"Included": "gray", "Offset": "green",
                          "Info Only": "gray"})


class ReportTests(Fixture):
    def test_expenses_empty_states(self):
        out = self.live()["expenses"]
        self.assertEqual((out["paid"], out["paid_note"]),
                         ("—", "No payments yet"))
        self.assertEqual((out["incurred"], out["incurred_note"]),
                         ("—", "Not reported yet"))
        self.assertEqual(out["pending"], "RM 0.00")     # genuine zero
        self.assertEqual(out["share"], "—")
        self.assertEqual(out["hq_status"], "No payments yet")

    def test_expenses_amounts_once_paid(self):
        cat = self.app.add_expense_category(self.admin, self.sid, "Elec",
                                            sort_order=1)
        self.app.record_expense(
            self.admin, self.sid, incurred_date=date(2026, 10, 2),
            paid_date=date(2026, 10, 3), category_id=cat, description="x",
            amount_cents=rm(120), paid_by="Petty Cash", status="Paid",
            verification="Verified", recorded_by="QA", receipt_ref="R1")
        out = self.live()["expenses"]
        self.assertEqual((out["paid"], out["paid_note"]), ("RM 120.00", ""))
        self.assertEqual((out["incurred"], out["incurred_note"]),
                         ("RM 120.00", ""))

    def test_salary_status_month_to_date(self):
        self.day(1, sales_cents=rm(10000), ft_working=8)
        mp = self.live()["manpower"]
        self.assertTrue(mp["ratio_status"].startswith("Month to date: "),
                        mp["ratio_status"])
        self.assertEqual(mp["ratio_mtd"], "1")

    def test_salary_status_restricted_for_viewer(self):
        v = self.app.create_user(self.admin, "v@ducklab.com", "V",
                                 "ViewerPass123")
        self.app.assign_role(self.admin, v.id, "viewer")
        self.app.assign_store(self.admin, v.id, self.sid)
        v = self.app.auth.get_user(v.id)     # roles + stores reloaded
        rep = self.app.preview_month(v, self.sid, "2026-10", as_of=TODAY)
        mp = _serialize_live(rep, user=v)["manpower"]
        self.assertEqual(mp["ratio_status"], "Restricted")
        self.assertEqual(mp["salary"], "Restricted")

    def test_sales_section_plain_labels(self):
        # R1: no section-header chips and no "(projected)" / "(early
        # estimate)" labels; "Projected Sales" only in the incomplete
        # layout
        from ducklab import ops_pages
        out = str(ops_pages._sales_section())
        self.assertNotIn("(early estimate)", out)
        self.assertNotIn("(projected)", out)
        self.assertNotIn('"Early estimate"', out)
        self.assertNotIn('["status_l1"]', out)
        self.assertEqual(out.count('"Projected Sales"'), 1)

    def test_awaiting_payroll_subline_only_when_missing(self):
        from ducklab import ops_pages
        out = str(ops_pages._manpower_section())
        self.assertNotIn("— = awaiting payroll", out)
        self.assertIn("Awaiting Payroll", out)


# ---------------------------------------------------------------------------
# 3. plain language
# ---------------------------------------------------------------------------
class PlainLanguageTests(Fixture):
    def test_no_developer_wording_in_pages(self):
        from ducklab import (ops_daily_pages, ops_expenses_pages,
                             ops_manpower_pages, ops_pages,
                             ops_stores_pages)
        for page in (ops_pages.report_page, ops_stores_pages.stores_page,
                     ops_manpower_pages.manpower_page,
                     ops_expenses_pages.expenses_page,
                     ops_daily_pages.daily_page):
            out = str(page())
            for word in ("NULL", "¢", "12550", "Level1"):
                self.assertNotIn(word, out, f"{page.__name__}: {word}")

    def test_setup_levels_read_level_1(self):
        st = self.bound("ops_stores_state", "StoresState", "load_page_data")
        self.assertEqual([lv["level"] for lv in st.setup_levels],
                         ["Level 1", "Level 2", "Level 3", "Level 4"])


# ---------------------------------------------------------------------------
# 4. overview
# ---------------------------------------------------------------------------
class OverviewTests(Fixture):
    def overview(self):
        import ducklab.ops_overview_state as mod
        with self.patched("ops_overview_state")[0], \
                mock.patch.object(mod, "app_service", self.app):
            return mod.compute_overview(self.admin, self.sid, "2026-10",
                                        TODAY)

    def test_sparklines_need_three_days(self):
        for d in (1, 2):
            self.day(d, sales_cents=rm(10000), stock_losses={"DMO": rm(5)})
        ov = self.overview()
        self.assertFalse(ov["spark_sales_ready"])
        self.assertFalse(ov["spark_stock_ready"])
        self.day(3, sales_cents=rm(10000), stock_losses={"DMO": rm(5)})
        ov = self.overview()
        self.assertTrue(ov["spark_sales_ready"])
        self.assertTrue(ov["spark_stock_ready"])

    def test_panel_money_is_compact_like_the_tiles(self):
        self.day(1, sales_cents=rm(10000))
        ov = self.overview()
        for row in ov["targets_extra"]:
            self.assertNotRegex(row["value"], r"\.\d\d$", row)
        # Summary: full values, not the short form
        glance = {r["label"]: r["value"] for r in ov["glance"]}
        self.assertEqual(glance["Daily Sales (Avg.)"], "RM 10,000.00")
        self.assertEqual(ops_charts.fmt_money_compact(rm(6774.19)),
                         "RM 6,774")
        self.assertEqual(ops_charts.fmt_money_compact(rm(23500)),
                         "RM 23.5k")


if __name__ == "__main__":
    unittest.main()
