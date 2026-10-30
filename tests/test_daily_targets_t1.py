"""T1 tests: daily targets from the Daily Sales Contribution — store State /
Concept dropdowns, Monthly Setup basket targets + public holidays, the
schema v3/v4 migrations, the scripts/import_dsc.py import and the Sales
Performance "Daily Sales" table. Expected values are hand-computed
literals from a synthetic case (no company figures). Disposable temp DBs
and a synthetic workbook built in a temp dir only.

Run: python -m pytest tests/test_daily_targets_t1.py -v
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab import design as ds
from ducklab import kpi, store_ops_db
from ducklab.app_service import AppService
from ducklab.operations_write_repository import ValidationError

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "import_dsc.py"
from ducklab import dsc_import as import_dsc  # noqa: E402

D = Decimal
# Synthetic case: July 2026 (1 Jul = Wednesday), Level 1 300,000, no PH.
JULY = {"Mon": D("0.030"), "Tue": D("0.029"), "Wed": D("0.0295"),
        "Thu": D("0.026"), "Fri": D("0.031"), "Sat": D("0.039"),
        "Sun": D("0.0445")}                     # month total 1.0025
SALES_RM = [8800, 7400, 10000, 12000, 14000, 8700]   # 1-6 Jul
MONTH_NAMES = ["November", "December", "January", "February", "March",
               "April", "May", "June", "July", "August", "September",
               "October"]


def rm(v):
    return int(Decimal(str(v)) * 100)


def july_sales():
    return {date(2026, 7, n): rm(v) for n, v in enumerate(SALES_RM, 1)}


# 1 ---------------------------------------------------------------------------
class DailyTargetKpiTests(unittest.TestCase):
    def targets(self, sales=None, holidays=(), shares=JULY, level1=rm(300000)):
        return kpi.daily_targets("2026-07", level1, shares, set(holidays),
                                 july_sales() if sales is None else sales)

    def test_daily_targets_literal(self):
        rows = self.targets()
        self.assertEqual(len(rows), 31)
        self.assertEqual(sum(kpi.day_share(r.date, JULY, set())
                             for r in rows), D("1.0025"))
        self.assertEqual([r.target_cents for r in rows[:6]],
                         [882793, 778055, 927681, 1167082, 1331671, 897756])

    def test_adjusted_target_carries_shortfall_never_surplus(self):
        rows = self.targets()
        self.assertEqual([r.adjusted_cents for r in rows[:6]],
                         [882793, 780848, 968529, 1167082, 1331671, 897756])
        self.assertEqual([r.result for r in rows[:6]],
                         ["Not Achieved", "Not Achieved", "Achieved",
                          "Achieved", "Achieved", "Not Achieved"])

    def test_unreported_days_have_no_result(self):
        rows = self.targets()
        # 7 Jul is the next day to report: its Adjusted Target shows
        # (no shortfall left: cumulative sales are ahead)
        self.assertEqual(rows[6].adjusted_cents, rows[6].target_cents)
        self.assertIsNone(rows[6].result)
        self.assertTrue(all(r.adjusted_cents is None and r.result is None
                            for r in rows[7:]))

    def test_gap_day_left_out_of_carry_forward(self):
        # 2 Jul not reported: its target (7,780.55) is not carried and it
        # has no Adjusted Target or Result
        sales = july_sales()
        del sales[date(2026, 7, 2)]
        rows = self.targets(sales=sales)
        self.assertEqual([r.adjusted_cents for r in rows[:6]],
                         # 3 Jul: 9,276.81 + (8,827.93 - 8,800) = 9,304.74
                         # 4 Jul: (8,827.93 + 9,276.81) - 18,800 < 0
                         [882793, None, 930474, 1167082, 1331671, 897756])
        self.assertEqual([r.result for r in rows[:6]],
                         ["Not Achieved", None, "Achieved", "Achieved",
                          "Achieved", "Not Achieved"])
        self.assertEqual(rows[1].target_cents, 778055)
        # 1-3 Jul all unreported: 4 Jul carries nothing
        sales = {d: v for d, v in july_sales().items() if d.day > 3}
        rows = self.targets(sales=sales)
        self.assertEqual(rows[3].adjusted_cents, 1167082)
        self.assertEqual(rows[3].result, "Achieved")
        self.assertEqual([r.adjusted_cents for r in rows[:3]],
                         [None, None, None])

    def test_nothing_reported_first_day_is_next(self):
        rows = self.targets(sales={})
        self.assertEqual(rows[0].adjusted_cents, 882793)
        self.assertIsNone(rows[1].adjusted_cents)

    def test_sales_equal_to_adjusted_target_is_achieved(self):
        rows = self.targets(sales={date(2026, 7, 1): 882793})
        self.assertEqual(rows[0].result, kpi.RESULT_ACHIEVED)

    def test_public_holiday_uses_ph_share_when_present(self):
        shares = {**JULY, "PH": D("0.05")}
        ph = date(2026, 7, 6)                                 # a Monday
        self.assertEqual(kpi.day_share(ph, shares, {ph}), D("0.05"))
        self.assertEqual(kpi.day_share(ph, JULY, {ph}), D("0.030"))
        self.assertEqual(kpi.day_share(ph, shares, set()), D("0.030"))
        rows = self.targets(shares=shares, holidays={ph})
        self.assertTrue(rows[5].public_holiday)
        # total 1.0025 - 0.030 + 0.05 = 1.0225; 300,000 x 0.05 / 1.0225
        self.assertEqual(rows[5].target_cents, 1466993)

    def test_no_match_or_no_level1_gives_none(self):
        self.assertIsNone(self.targets(shares={}))
        partial = dict(JULY)
        del partial["Sun"]
        self.assertIsNone(self.targets(shares=partial))
        self.assertIsNone(self.targets(level1=None))

    def test_basket_percentages_literal(self):
        txn = kpi.transactions_pct(380, 12400, 31)
        spr = kpi.pct_vs_target(rm("23.00"), rm("25.00"))
        ipr = kpi.pct_vs_target(D("3.40"), D("3.75"))
        self.assertEqual(txn, D("-0.05"))
        self.assertEqual(spr, D("-0.08"))
        self.assertEqual([ds.fmt_variance_pct(v) for v in (txn, spr, ipr)],
                         ["−5.0%", "−8.0%", "−9.3%"])
        self.assertIsNone(kpi.transactions_pct(380, None, 31))
        self.assertIsNone(kpi.pct_vs_target(rm(23), 0))
        self.assertIsNone(kpi.pct_vs_target(None, rm(25)))

    def test_variance_pct_format(self):
        self.assertEqual(ds.fmt_variance_pct(D("0.021")), "+2.1%")
        self.assertEqual(ds.fmt_variance_pct(D("-0.0004")), "0.0%")
        self.assertEqual(ds.fmt_variance_pct(None), "—")
        self.assertEqual(ds.fmt_share(D("0.0295")), "2.95%")


# 2 ---------------------------------------------------------------------------
class MigrationTests(unittest.TestCase):
    def test_v2_database_migrates_additively(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "v2.db"
            conn = sqlite3.connect(db)
            conn.executescript(store_ops_db._SCHEMA_V1)
            conn.executescript(store_ops_db._SCHEMA_V2)
            conn.execute("PRAGMA user_version = 2")
            conn.execute("INSERT INTO stores (code, name, concept) "
                         "VALUES ('X1', 'Legacy', 'Kiosk')")
            conn.execute("INSERT INTO monthly_configs (store_id, month, "
                         "level1_base_cents) VALUES (1, '2026-07', 100)")
            conn.commit()
            conn.close()
            store_ops_db.init_db(db)
            store_ops_db.init_db(db)                         # idempotent
            conn = store_ops_db.connect(db)
            try:
                self.assertEqual(
                    conn.execute("PRAGMA user_version").fetchone()[0],
                    store_ops_db.SCHEMA_VERSION)
                row = conn.execute(
                    "SELECT concept, state FROM stores").fetchone()
                self.assertEqual(tuple(row), ("Kiosk", None))
                cfg = conn.execute(
                    "SELECT level1_base_cents, target_transactions, "
                    "target_spr_cents, target_ipr FROM monthly_configs"
                ).fetchone()
                self.assertEqual(tuple(cfg), (100, None, None, None))
                tables = {r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"public_holidays", "contribution_shares",
                                 "basket_benchmarks"} <= tables)
                cols = [r[1] for r in conn.execute(
                    "PRAGMA table_info(basket_benchmarks)")]
                self.assertEqual(cols, ["concept", "state", "month",
                                        "avg_spr", "avg_ipr"])
            finally:
                conn.close()

    def test_v3_benchmarks_replaced_by_monthly_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "v3.db"
            conn = sqlite3.connect(db)
            for script in (store_ops_db._SCHEMA_V1, store_ops_db._SCHEMA_V2,
                           store_ops_db._SCHEMA_V3):
                conn.executescript(script)
            conn.execute("PRAGMA user_version = 3")
            conn.execute("INSERT INTO basket_benchmarks VALUES "
                         "('Mall', 'Johor', 25.5, 3.2)")
            conn.execute("INSERT INTO contribution_shares VALUES "
                         "('Johor', 'Mall', 'Mon', 7, 0.03)")
            conn.commit()
            conn.close()
            store_ops_db.init_db(db)
            conn = store_ops_db.connect(db)
            try:
                self.assertEqual(
                    conn.execute("PRAGMA user_version").fetchone()[0],
                    store_ops_db.SCHEMA_VERSION)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM basket_benchmarks").fetchone()[0], 0)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM contribution_shares").fetchone()[0],
                    1)
                conn.execute("INSERT INTO basket_benchmarks VALUES "
                             "('Mall', 'Johor', 9, 25.5, 3.2)")
                with self.assertRaises(sqlite3.IntegrityError):
                    conn.execute("INSERT INTO basket_benchmarks VALUES "
                                 "('Mall', 'Johor', 13, 25.5, 3.2)")
            finally:
                conn.close()


# 3 ---------------------------------------------------------------------------
class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "t1.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha",
                                           state="Johor", concept="Mall")

    def tearDown(self):
        self._tmp.cleanup()

    def bound(self, module, cls, fn, month="2026-07"):
        sid, admin = self.sid, self.admin
        with mock.patch.object(module, "app_service", self.app), \
                mock.patch.object(cls, "_actor", lambda self_: admin), \
                mock.patch.object(cls, "_selected_store_id",
                                  lambda self_: sid), \
                mock.patch.object(cls, "_selected_month",
                                  lambda self_: month):
            return fn()

    def insert_shares(self, state="Johor", concept="Mall", month=7,
                      shares=JULY):
        conn = store_ops_db.connect(self.db)
        with conn:
            conn.executemany(
                "INSERT INTO contribution_shares VALUES (?,?,?,?,?)",
                [(state, concept, k, month, float(v))
                 for k, v in shares.items()])
        conn.close()


class StoreChoiceTests(Fixture):
    def test_state_and_concept_must_be_options(self):
        store = self.app.read_repo.get_store(self.sid)
        self.assertEqual((store.state, store.concept), ("Johor", "Mall"))
        for bad in ({"state": "Johore"}, {"concept": "Kiosk"},
                    {"concept": "mall"}):
            with self.assertRaises(ValidationError):
                self.app.update_store_details(self.admin, self.sid, **bad)
        with self.assertRaises(ValidationError):
            self.app.register_store(self.admin, "A02", "Beta",
                                    concept="Kiosk")

    def test_legacy_concept_kept_until_edited(self):
        conn = store_ops_db.connect(self.db)
        with conn:
            conn.execute("UPDATE stores SET concept = 'Kiosk'")
        conn.close()
        self.app.update_store_details(self.admin, self.sid, zone="East")
        self.assertEqual(self.app.read_repo.get_store(self.sid).concept,
                         "Kiosk")
        import ducklab.ops_stores_state as mod
        from ducklab.ops_stores_state import StoresState
        st = StoresState()
        self.bound(mod, StoresState, st.load_page_data)
        self.assertEqual(st.store_rows[0]["concept"], "Kiosk")
        self.assertEqual(st.store_rows[0]["state"], "Johor")
        st.open_store_form(str(self.sid))
        self.assertEqual(st.sf_concept_hint, "Current: Kiosk. Pick an option.")
        self.bound(mod, StoresState, lambda: list(st.save_store()))
        self.assertEqual(st.sf_error, "Concept: pick an option from the list")
        self.assertEqual(self.app.read_repo.get_store(self.sid).concept,
                         "Kiosk")
        st.set_sf_concept("Ringgit Mall")
        st.set_sf_state("WP")
        self.assertEqual(st.sf_concept_hint, "")
        self.bound(mod, StoresState, lambda: list(st.save_store()))
        self.assertEqual(st.sf_error, "")
        store = self.app.read_repo.get_store(self.sid)
        self.assertEqual((store.concept, store.state), ("Ringgit Mall", "WP"))


class MonthlySetupTests(Fixture):
    def setup_state(self):
        # the setup loader/handlers live in ducklab.ops_setup_state.
        import ducklab.ops_setup_state as mod
        from ducklab.ops_setup_state import SetupState
        st = SetupState()
        self.bound(mod, SetupState, st.load_setup_data)
        return mod, SetupState, st

    def test_targets_and_holidays_round_trip(self):
        mod, cls, st = self.setup_state()
        self.assertEqual((st.s_holiday_min, st.s_holiday_max),
                         ("2026-07-01", "2026-07-31"))
        st.s_target_txn = "12400"
        st.s_target_spr = "25.00"
        st.s_target_ipr = "3.75"
        self.bound(mod, cls, lambda: st.add_holiday("2026-07-20"))
        self.bound(mod, cls, lambda: st.add_holiday("2026-07-06"))
        self.bound(mod, cls, lambda: st.add_holiday("2026-07-06"))
        self.bound(mod, cls, lambda: st.add_holiday("2026-08-01"))
        self.assertEqual(st.setup_error,
                         "Public Holidays: pick a date in this month.")
        self.assertEqual(st.s_holidays, ["2026-07-06", "2026-07-20"])
        self.assertEqual([h["label"] for h in st.holiday_chips],
                         ["06 Jul", "20 Jul"])
        self.bound(mod, cls, lambda: list(st.save_setup()))
        self.assertEqual(st.setup_error, "")
        cfg = self.app.get_month_config(self.admin, self.sid, "2026-07")
        self.assertEqual((cfg.target_transactions, cfg.target_spr_cents,
                          cfg.target_ipr), (12400, 2500, D("3.75")))
        self.assertEqual(
            self.app.get_public_holidays(self.admin, self.sid, "2026-07"),
            [date(2026, 7, 6), date(2026, 7, 20)])
        # reload shows the saved values; removing one saves the new set
        mod, cls, st = self.setup_state()
        self.assertEqual((st.s_target_txn, st.s_target_spr, st.s_target_ipr),
                         ("12400", "25.00", "3.75"))
        st.remove_holiday("2026-07-20")
        self.bound(mod, cls, lambda: list(st.save_setup()))
        self.assertEqual(
            self.app.get_public_holidays(self.admin, self.sid, "2026-07"),
            [date(2026, 7, 6)])

    def test_unchanged_holidays_are_not_resent(self):
        mod, cls, st = self.setup_state()
        self.assertIsNone(st._parse_holidays())
        self.bound(mod, cls, lambda: list(st.save_setup()))
        self.assertEqual(st.setup_error, "Nothing to save — no changes made.")

    def test_bad_target_rejected(self):
        mod, cls, st = self.setup_state()
        st.s_target_ipr = "abc"
        self.bound(mod, cls, lambda: list(st.save_setup()))
        self.assertIn("Target IPR", st.setup_error)

    def test_repository_rejects_holiday_outside_month(self):
        with self.assertRaises(ValidationError):
            self.app.configure_month_with_budgets(
                self.admin, self.sid, "2026-07", {}, {},
                holidays=[date(2026, 8, 1)])


# 4 ---------------------------------------------------------------------------
class SalesTableTests(Fixture):
    def july(self, holidays=None):
        self.app.configure_month_with_budgets(
            self.admin, self.sid, "2026-07",
            {"level1_base_cents": rm(300000), "level2_pct": 0.1,
             "level3_pct": 0.2, "level4_pct": 0.3,
             "target_transactions": 12400, "target_spr_cents": rm(25),
             "target_ipr": 3.75}, {}, holidays)
        units = [None, None, None, None, None, 1292]
        txn = [None, None, None, None, None, 380]
        for n, v in enumerate(SALES_RM, 1):
            self.app.record_daily(self.admin, self.sid, date(2026, 7, n),
                                  sales_cents=rm(v), transactions=txn[n - 1],
                                  units=units[n - 1])

    def sales(self):
        import ducklab.ops_sales_state as mod
        from ducklab.ops_sales_state import SalesState
        st = SalesState()
        self.bound(mod, SalesState, st.load_sales_data)
        return st

    def test_daily_sales_rows_literal(self):
        self.insert_shares()
        self.july()
        st = self.sales()
        self.assertEqual(st.sp_error, "")
        rows = st.sp_rows
        self.assertEqual([r["target"] for r in rows[:6]],
                         ["RM 8,827.93", "RM 7,780.55", "RM 9,276.81",
                          "RM 11,670.82", "RM 13,316.71", "RM 8,977.56"])
        self.assertEqual([r["adjusted"] for r in rows[:6]],
                         ["RM 8,827.93", "RM 7,808.48", "RM 9,685.29",
                          "RM 11,670.82", "RM 13,316.71", "RM 8,977.56"])
        self.assertEqual([r["result"] for r in rows[:6]],
                         ["Not Achieved", "Not Achieved", "Achieved",
                          "Achieved", "Achieved", "Not Achieved"])
        self.assertEqual([r["result_color"] for r in rows[:2]],
                         ["red", "red"])
        self.assertEqual(rows[0]["contribution"], "2.95%")
        # 6 Jul: 8,700 / 380 = 22.89 SPR; 1,292 / 380 = 3.40 IPR
        r6 = rows[5]
        self.assertEqual((r6["transactions"], r6["txn_pct"],
                          r6["txn_pct_neg"]), ("380", "−5.0%", "1"))
        self.assertEqual((r6["spr"], r6["spr_pct"]), ("RM 22.89", "−8.4%"))
        self.assertEqual((r6["ipr"], r6["ipr_pct"]), ("3.40", "−9.3%"))
        # unreported days: no result, collapsed
        self.assertEqual(rows[6]["result"], "")
        self.assertEqual(rows[7]["adjusted"], "—")
        self.assertEqual(len(st.sp_rows_reported), 6)
        self.assertEqual(st.sp_hidden_label, "25 days not reported")

    def test_gap_day_in_table(self):
        self.insert_shares()
        self.july()
        conn = store_ops_db.connect(self.db)
        with conn:
            conn.execute("DELETE FROM daily_records WHERE date = '2026-07-02'")
        conn.close()
        rows = self.sales().sp_rows
        self.assertEqual([r["adjusted"] for r in rows[:3]],
                         ["RM 8,827.93", "—", "RM 9,304.74"])
        self.assertEqual([r["result"] for r in rows[:3]],
                         ["Not Achieved", "", "Achieved"])
        self.assertEqual(rows[1]["target"], "RM 7,780.55")

    def test_public_holiday_marked(self):
        self.insert_shares(shares={**JULY, "PH": D("0.05")})
        self.july(holidays=[date(2026, 7, 6)])
        rows = self.sales().sp_rows
        self.assertEqual(rows[5]["ph"], "PH")
        self.assertEqual(rows[5]["contribution"], "5.00%")
        self.assertEqual(rows[5]["target"], "RM 14,669.93")
        self.assertEqual(rows[4]["ph"], "")

    def test_no_state_concept_match_shows_dash(self):
        self.insert_shares(state="Kedah")
        self.july()
        rows = self.sales().sp_rows
        self.assertEqual((rows[0]["contribution"], rows[0]["target"],
                          rows[0]["adjusted"], rows[0]["result"]),
                         ("—", "—", "—", ""))
        self.assertEqual(rows[0]["sales"], "RM 8,800.00")

    def test_other_calendar_month_any_year(self):
        self.insert_shares(month=8)
        self.july()
        self.assertEqual(self.sales().sp_rows[0]["target"], "—")


# 5 ---------------------------------------------------------------------------
def build_workbook(path: Path, dsc_rows, spr_rows):
    """Synthetic DSC workbook in the real layout (positions per the import
    contract): DSC header row 4 with A4 left blank; SPR group labels in
    row 2 (C2, P2), header row 3 with a misspelled Concept header, SPR in
    C..N, blank O, IPR in P..AA."""
    import openpyxl
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "DSC"
    ws["A1"] = "Daily Sales Contribution"
    ws.append([])
    ws.append([])
    ws.append([None, "Concept", "Day", *MONTH_NAMES])        # row 4
    for row in dsc_rows:
        ws.append(row)
    sp = wb.create_sheet("SPR")
    sp.append(["SPR / IPR"])
    sp.append([None, None, "Average SPR", *[None] * 12,
               "Average IPR"])                               # row 2
    sp.append(["Conept", "State", *MONTH_NAMES, None,
               *MONTH_NAMES])                                # row 3
    for row in spr_rows:
        sp.append(row)
    wb.save(path)


def spr_row(concept, state, spr=None, ipr=None):
    """SPR sheet row; spr / ipr map month name -> value."""
    spr, ipr = spr or {}, ipr or {}
    return [concept, state, *[spr.get(m) for m in MONTH_NAMES], None,
            *[ipr.get(m) for m in MONTH_NAMES]]


def july_dsc_rows(state="Johor", concept="Mall", ph=None):
    rows = []
    for i, (day, share) in enumerate(JULY.items()):
        values = [None] * 12
        values[MONTH_NAMES.index("July")] = float(share)
        values[MONTH_NAMES.index("August")] = 0.03
        # merged group: State / Concept only on the group's first row
        rows.append([state if i == 0 else None,
                     concept if i == 0 else None, day, *values])
    if ph is not None:
        values = [None] * 12
        values[MONTH_NAMES.index("July")] = ph
        rows.append([None, None, "PH", *values])
    return rows


class ImportScriptTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.db = self.tmp / "ops.db"
        store_ops_db.init_db(self.db)
        self.xlsx = self.tmp / "dsc.xlsx"

    def tearDown(self):
        self._tmp.cleanup()

    def run_script(self, *args, env_db=True):
        env = {k: v for k, v in os.environ.items() if k != "DUCKLAB_DB_PATH"}
        if env_db:
            env["DUCKLAB_DB_PATH"] = str(self.db)
        return subprocess.run([sys.executable, str(SCRIPT), *args],
                              capture_output=True, text=True, env=env,
                              cwd=self.tmp)

    def rows(self, sql):
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_refuses_without_db_path(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        r = self.run_script(str(self.xlsx), env_db=False)
        self.assertEqual(r.returncode, 2)
        self.assertIn("DUCKLAB_DB_PATH is not set", r.stderr)

    def test_imports_shares_and_benchmarks(self):
        build_workbook(
            self.xlsx,
            july_dsc_rows(ph=0.05) + july_dsc_rows("Kedah", "Toys"),
            [spr_row("Mall", "Johor", {"July": 25.5, "August": 26},
                     {"July": 3.2}),
             spr_row(None, "Kedah", {"July": 24}),
             spr_row("Kiosk", "Johor", {"November": "26.10"},
                     {"November": 3.0})])
        r = self.run_script(str(self.xlsx))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Imported 29 day shares (2 State/Concept pairs) and 3 "
                      "SPR/IPR benchmarks (4 Concept/State months).",
                      r.stdout)
        self.assertIn("Warning: Concept 'Kiosk' is not a store Concept "
                      "option", r.stdout)
        repo = AppService(self.db).read_repo
        self.assertEqual(repo.get_contribution_shares("Johor", "Mall",
                                                      "2026-07"),
                         {**JULY, "PH": D("0.05")})
        self.assertEqual(repo.get_contribution_shares("Johor", "Mall",
                                                      "2031-07"),
                         {**JULY, "PH": D("0.05")})          # any year
        self.assertEqual(
            repo.get_contribution_shares("Kedah", "Toys", "2026-08")["Sun"],
            D("0.03"))
        self.assertEqual(repo.get_contribution_shares("Johor", "Mall",
                                                      "2026-09"), {})
        self.assertEqual(
            self.rows("SELECT concept, state, month, avg_spr, avg_ipr FROM "
                      "basket_benchmarks ORDER BY concept, state, month"),
            [("Kiosk", "Johor", 11, 26.1, 3.0),
             ("Mall", "Johor", 7, 25.5, 3.2),
             ("Mall", "Johor", 8, 26.0, None),
             ("Mall", "Kedah", 7, 24.0, None)])

    def test_real_layout_two_concepts_two_states(self):
        """2 concepts x 2 states, every month filled; blank A4 in DSC and a
        misspelled SPR Concept header must not matter."""
        dsc, spr = [], []
        for c_i, concept in enumerate(("Mall", "Toys")):
            for s_i, state in enumerate(("Johor", "Kedah")):
                base = 0.02 + c_i * 0.002 + s_i * 0.001
                for d_i, day in enumerate(kpi.DSC_WEEKDAYS):
                    shares = [round(base + d_i * 0.0001 + m * 0.00001, 6)
                              for m in range(12)]
                    dsc.append([state if d_i == 0 else None,
                                concept if d_i == 0 else None, day,
                                *shares])
                spr.append(spr_row(
                    concept if s_i == 0 else None, state,
                    {m: 20 + c_i * 2 + s_i + i / 10
                     for i, m in enumerate(MONTH_NAMES)},
                    {m: 3 + c_i * 0.2 + s_i * 0.1 + i / 100
                     for i, m in enumerate(MONTH_NAMES)}))
        build_workbook(self.xlsx, dsc, spr)
        r = self.run_script(str(self.xlsx))
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Imported 336 day shares (4 State/Concept pairs) and 4 "
                      "SPR/IPR benchmarks (48 Concept/State months).",
                      r.stdout)
        # Toys / Kedah, Wed, September (11th fiscal column, index 10):
        # 0.02 + 0.002 + 0.001 + 2 * 0.0001 + 10 * 0.00001 = 0.0233
        repo = AppService(self.db).read_repo
        self.assertEqual(
            repo.get_contribution_shares("Kedah", "Toys", "2026-09")["Wed"],
            D("0.0233"))
        # September SPR / IPR, Toys / Kedah: 20 + 2 + 1 + 1.0 = 24.0,
        # 3 + 0.2 + 0.1 + 0.10 = 3.4 (the Concept came from the row above)
        ((spr_v, ipr_v),) = self.rows(
            "SELECT avg_spr, avg_ipr FROM basket_benchmarks WHERE "
            "concept = 'Toys' AND state = 'Kedah' AND month = 9")
        self.assertAlmostEqual(spr_v, 24.0)
        self.assertAlmostEqual(ipr_v, 3.4)
        self.assertEqual(self.rows(
            "SELECT DISTINCT concept, state FROM basket_benchmarks "
            "ORDER BY 1, 2"),
            [("Mall", "Johor"), ("Mall", "Kedah"), ("Toys", "Johor"),
             ("Toys", "Kedah")])

    def test_shifted_spr_layout_refused(self):
        import openpyxl
        build_workbook(self.xlsx, july_dsc_rows(),
                       [spr_row("Mall", "Johor", {"July": 25})])
        wb = openpyxl.load_workbook(self.xlsx)
        wb["SPR"]["P2"] = None
        wb["SPR"]["Q3"] = "January"
        wb.save(self.xlsx)
        r = self.run_script(str(self.xlsx))
        self.assertEqual(r.returncode, 1)
        self.assertIn("P2 is '', expected Average IPR", r.stderr)
        wb["SPR"]["P2"] = "Average IPR"
        wb.save(self.xlsx)
        r = self.run_script(str(self.xlsx))
        self.assertIn("Q3 is 'January', expected December", r.stderr)
        self.assertEqual(self.rows("SELECT COUNT(*) FROM basket_benchmarks"),
                         [(0,)])

    def test_reimport_replaces(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        self.assertEqual(self.run_script(str(self.xlsx)).returncode, 0)
        build_workbook(self.xlsx, july_dsc_rows("Perak", "Outlet"), [])
        self.assertEqual(self.run_script(str(self.xlsx)).returncode, 0)
        self.assertEqual(self.rows("SELECT DISTINCT state, concept FROM "
                                   "contribution_shares"),
                         [("Perak", "Outlet")])

    def test_bad_workbook_changes_nothing(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        self.assertEqual(self.run_script(str(self.xlsx)).returncode, 0)
        before = self.rows("SELECT * FROM contribution_shares ORDER BY 1,2,3,4")
        bad = self.tmp / "bad.xlsx"
        rows = july_dsc_rows("Perak", "Outlet")
        rows[2][2] = "Holiday"
        build_workbook(bad, rows, [])
        r = self.run_script(str(bad))
        self.assertEqual(r.returncode, 1)
        self.assertIn("DSC row 7: Day 'Holiday' is not Mon–Sun or PH",
                      r.stderr)
        self.assertEqual(
            self.rows("SELECT * FROM contribution_shares ORDER BY 1,2,3,4"),
            before)

    def test_missing_month_header_and_sheet(self):
        import openpyxl
        build_workbook(self.xlsx, july_dsc_rows(), [])
        wb = openpyxl.load_workbook(self.xlsx)
        wb["DSC"]["N4"] = "Total"
        wb.save(self.xlsx)
        r = self.run_script(str(self.xlsx))
        self.assertEqual(r.returncode, 1)
        self.assertIn("N4 is 'Total', expected September", r.stderr)
        del wb["SPR"]
        wb["DSC"]["N4"] = "Sept"
        wb.save(self.xlsx)
        r = self.run_script(str(self.xlsx))
        self.assertIn("Sheet 'SPR' not found", r.stderr)

    def test_refuses_unmigrated_or_missing_database(self):
        build_workbook(self.xlsx, july_dsc_rows(), [])
        with self.assertRaises(import_dsc.DscImportError):
            import_dsc.import_workbook(self.xlsx, self.tmp / "none.db")
        old = self.tmp / "v3.db"
        conn = sqlite3.connect(old)
        conn.execute("PRAGMA user_version = 3")
        conn.close()
        with self.assertRaises(import_dsc.DscImportError) as ctx:
            import_dsc.import_workbook(self.xlsx, old)
        self.assertIn("schema is v3", str(ctx.exception))

    def test_day_names_and_percent_text(self):
        self.assertEqual([import_dsc._day_key(v) for v in
                          ("Mon", "tuesday", " THU ", "ph", "Sun")],
                         ["Mon", "Tue", "Thu", "PH", "Sun"])
        with self.assertRaises(ValueError):
            import_dsc._day_key("Mo")
        self.assertEqual(import_dsc._number("2.95%"), D("0.0295"))
        self.assertEqual(import_dsc._number(0.0295), D("0.0295"))
        self.assertEqual([import_dsc._month_number(v) for v in
                          ("November", "sep", "Sept", "Oc", None)],
                         [11, 9, 9, None, None])
        self.assertEqual(import_dsc._col_letters(27), "AA")


if __name__ == "__main__":
    unittest.main()
