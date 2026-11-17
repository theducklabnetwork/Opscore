"""O1 tests: Daily Operations + Daily Entry wording (presentation only).
DMQ reads "Damage in Quality" (also for databases seeded earlier); stock
codes in workbook order with "Missing" / "Extra" labels; the "×" beside
a saved field does what the "Clear saved value" checkbox did; plain
tile/field labels. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_daily_o1.py -v
"""

import sqlite3
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "o1.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sid, "2026-10", level1_base_cents=rm(250000),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            part_timer_factor=0.5)

    def tearDown(self):
        self._tmp.cleanup()

    def bound(self, fn):
        import ducklab.ops_daily_state as mod
        from ducklab.ops_daily_state import DailyState
        sid, admin = self.sid, self.admin
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(DailyState, "_actor", lambda s_: admin), \
                mock.patch.object(DailyState, "_selected_store_id",
                                  lambda s_: sid), \
                mock.patch.object(DailyState, "_selected_month",
                                  lambda s_: "2026-10"):
            return fn()

    def state(self):
        from ducklab.ops_daily_state import DailyState
        st = DailyState()
        st.selected_store_active = True
        self.bound(st.load_daily_data)
        return st


# 1 ---------------------------------------------------------------------------
class DmqWordingTests(Fixture):
    def test_new_and_existing_databases(self):
        names = {c["code"]: c["name"]
                 for c in self.app.list_stock_loss_codes(self.admin)}
        self.assertEqual(names["DMQ"], "Damage in Quality")
        # a database seeded before the change still has the old text
        conn = sqlite3.connect(self.db)
        conn.execute("UPDATE stock_loss_codes SET name = 'F&B quality "
                     "issue' WHERE code = 'DMQ'")
        conn.commit()
        conn.close()
        names = {c["code"]: c["name"]
                 for c in self.app.list_stock_loss_codes(self.admin)}
        self.assertEqual(names["DMQ"], "Damage in Quality")


# 6 ---------------------------------------------------------------------------
class StockCodeTests(Fixture):
    def test_order_labels_and_groups(self):
        st = self.state()
        self.assertEqual([c["label"] for c in st.contributing_codes],
                         ["DMO", "DMC", "DMP", "Missing", "Extra"])
        self.assertEqual([c["label"] for c in st.informational_codes],
                         ["2DMP", "DPI", "DDR", "DME", "DMN", "DMQ", "DMW",
                          "DST"])
        # stored codes unchanged; description kept for the tooltip
        dmq = next(c for c in st.stock_codes if c["code"] == "DMQ")
        self.assertEqual(dmq["name"], "Damage in Quality")
        self.assertEqual(
            next(c for c in st.stock_codes if c["label"] == "Missing")
            ["code"], "MISSING")


# 7 ---------------------------------------------------------------------------
class ClearXTests(Fixture):
    def test_x_clears_a_saved_field_and_stock_code(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(9000), units=300,
                              stock_losses={"DMO": rm(45)})
        st = self.state()
        self.bound(lambda: st.open_entry("2026-10-01"))
        # "×" only where a saved value exists
        self.assertTrue(st.can_clear["f_sales"])
        self.assertTrue(st.can_clear["f_units"])
        self.assertFalse(st.can_clear["f_transactions"])
        self.assertTrue(st.can_clear_stock["DMO"])
        self.assertFalse(st.can_clear_stock["DMC"])
        st.clear_saved_field("f_units")
        st.clear_saved_stock("DMO")
        self.assertEqual((st.f_units, st.stock_form["DMO"]), ("", ""))
        self.assertFalse(st.can_clear["f_units"])          # hidden once used
        self.bound(lambda: list(st.save_entry()))
        self.assertEqual(st.form_error, "")
        rec = self.app.get_daily_records(self.admin, self.sid, "2026-10")[0]
        self.assertEqual(rec.sales_cents, rm(9000))         # untouched
        self.assertIsNone(rec.units)                        # cleared to NULL
        self.assertEqual(rec.stock_losses, {})              # code removed

    def test_same_result_as_the_checkbox(self):
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(9000), units=300)
        st = self.state()
        self.bound(lambda: st.open_entry("2026-10-01"))
        st.f_units = ""
        st.set_clear_field("f_units", True)                 # old checkbox
        self.bound(lambda: list(st.save_entry()))
        rec = self.app.get_daily_records(self.admin, self.sid, "2026-10")[0]
        self.assertIsNone(rec.units)


# 2, 3 ------------------------------------------------------------------------
class TileTests(Fixture):
    def test_money_format_and_complete_flag(self):
        for d in range(1, 4):
            self.app.record_daily(self.admin, self.sid, date(2026, 10, d),
                                  sales_cents=rm(9000.50))
        st = self.state()
        k = st.day_kpis
        self.assertEqual(k["mtd"], "RM 27,001.50")
        self.assertEqual(k["avg_daily"], "RM 9,000.50")
        self.assertEqual(k["projected"], "RM 279,015.50")
        self.assertEqual(k["complete"], "")


# 2-7 -------------------------------------------------------------------------
class PageWordingTests(unittest.TestCase):
    def test_daily_upt_uses_daily_upt_values(self):
        from ducklab import ops_daily_pages as p
        self.assertIn(("BV", True), p._COLS)
        self.assertNotIn(("SPR", True), p._COLS)
        self.assertIn(("UPT", True), p._COLS)
        self.assertNotIn(("IPR", True), p._COLS)
        self.assertEqual(p._COLS[7], ("UPT", True))
        row = dict(date="1", weekday="", sales="s", transactions="t",
                   units="u", void="v", refund="r", spr="p", upt="UPT-VALUE", asp="a", staff="f",
                   sales_per_staff="x", loss_missing="0", loss="l",
                   expenses="e", iso="2026-10-01", status="Complete",
                   status_color="green")
        self.assertIn('"UPT-VALUE"', str(p._day_row(row)))

    def test_page_and_dialog(self):
        from ducklab import ops_daily_pages as p
        page = str(p.daily_page())
        for text in ("New Entry", "Sales", "Achievement", "Projected Sales",
                     "Projected Achievement", "Average Daily Sales",
                     "Transactions", "Units Sold", "Total Loss",
                     "Void", "Refund", "Total Loss", "SPS"):
            self.assertIn(f'"{text}"', page, text)
        dialog = str(p._entry_dialog())
        for text in ("Sales", "Manpower", "Stock Loss", "Sales (RM)",
                     "Units Sold", "Void (RM)", "Refund (RM)",
                     "Full-Time Staff", "Part-Time Staff",
                     "New Hires", "Resignations", "Annual Leave",
                     "Medical Leave", "Other Paid Leave (incl. RPL)",
                     "Emergency Leave", "Unpaid Leave",
                     "Total Loss", "Information Only"):
            self.assertIn(f'"{text}"', dialog, text)
        for gone in ("Record a day", "Sales MTD", "Month-end sales",
                     "Avg daily sales", "Record daily sales",
                     "Blank fields leave", "Counts toward total loss",
                     "Informational only", "Effective staff = FT",
                     "Daily sales (RM)", "Full-time staff working",
                     "Status reflects all entered fields",
                     '["reported_days"]', '["target_sub"]',
                     '["proj_achievement_sub"]', "RadixThemesCheckbox"):
            self.assertNotIn(gone, page + dialog, gone)
        # ids and handlers kept
        for fid in ("record-day", "f-sales", "f-leave-unpaid", "entry-save",
                    "f-sales-clear", "kpi-mtd"):
            self.assertIn(fid, page + dialog, fid)


if __name__ == "__main__":
    unittest.main()
