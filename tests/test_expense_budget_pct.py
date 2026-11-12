"""Percentage-based expense budgets on the progressive sales-target ladder.

The stored budget is a fraction (budget_pct, e.g. 0.025 = 2.5%). The RM
budget = pct x the ACTIVE sales target, which tracks cumulative recorded
sales: Minimum RTM until Level 1 is reached, then the highest level reached
(up to Level 4). Falls back to Minimum RTM when Level 1 Base is unset, and
moves both up and down as sales are recorded or corrected.

Run: python -m pytest tests/test_expense_budget_pct.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import kpi
from ducklab.app_service import AppService
from ducklab.expense_category_defaults import (
    DEFAULT_BUDGET_PCT, DEFAULT_EXPENSE_CATEGORIES,
)
from ducklab.kpi import MonthConfig
from ducklab.reference_bootstrap import backfill_default_budget_pct


def rm(v):
    return int(Decimal(str(v)) * 100)


class LadderTests(unittest.TestCase):
    """active_sales_target_cents: the progressive ladder."""

    def cfg(self, **kw):
        base = dict(month="2026-10", min_rtm_target_cents=rm(200000),
                    level1_base_cents=rm(250000), level2_pct=Decimal("0.1"),
                    level3_pct=Decimal("0.2"), level4_pct=Decimal("0.3"))
        base.update(kw)
        return MonthConfig(**base)

    def test_no_sales_uses_minimum_rtm(self):
        label, cents = kpi.active_sales_target_cents(self.cfg(), 0)
        self.assertEqual((label, cents), ("Minimum RTM", rm(200000)))

    def test_below_floor_uses_minimum_rtm(self):
        label, cents = kpi.active_sales_target_cents(self.cfg(), rm(100000))
        self.assertEqual((label, cents), ("Minimum RTM", rm(200000)))

    def test_reaching_level1_switches_to_level1(self):
        # reach-or-exceed: exactly Level 1 base -> Level 1
        label, cents = kpi.active_sales_target_cents(self.cfg(), rm(250000))
        self.assertEqual((label, cents), ("Level 1", rm(250000)))

    def test_between_floor_and_level1_stays_at_floor(self):
        label, cents = kpi.active_sales_target_cents(self.cfg(), rm(240000))
        self.assertEqual((label, cents), ("Minimum RTM", rm(200000)))

    def test_progresses_through_levels(self):
        cfg = self.cfg()
        for sales, want_label, want_cents in (
                (rm(275000), "Level 2", rm(275000)),   # 250k x 1.1
                (rm(300000), "Level 3", rm(300000)),   # 250k x 1.2
                (rm(325000), "Level 4", rm(325000)),   # 250k x 1.3
                (rm(400000), "Level 4", rm(325000))):  # capped at L4
            label, cents = kpi.active_sales_target_cents(cfg, sales)
            self.assertEqual((label, cents), (want_label, want_cents))

    def test_downward_correction_steps_back_down(self):
        cfg = self.cfg()
        # reached Level 2, then sales corrected back below Level 1
        label, _ = kpi.active_sales_target_cents(cfg, rm(275000))
        self.assertEqual(label, "Level 2")
        label, cents = kpi.active_sales_target_cents(cfg, rm(210000))
        self.assertEqual((label, cents), ("Minimum RTM", rm(200000)))

    def test_missing_level1_base_falls_back_to_minimum_rtm(self):
        cfg = self.cfg(level1_base_cents=None)
        label, cents = kpi.active_sales_target_cents(cfg, rm(999999))
        self.assertEqual((label, cents), ("Minimum RTM", rm(200000)))

    def test_level4_uses_max_of_floor_and_uplift(self):
        # small Level 1 base: L4 raw < Minimum RTM -> floor is the L4 target
        cfg = self.cfg(level1_base_cents=rm(100000))
        targets = kpi.sales_target_levels_cents(cfg)
        self.assertEqual(int(targets["level4"]), rm(200000))  # MAX(200k, 130k)

    def test_nothing_configured_gives_none(self):
        cfg = MonthConfig(month="2026-10")
        label, cents = kpi.active_sales_target_cents(cfg, rm(5000))
        self.assertEqual((label, cents), ("Minimum RTM", None))

    def test_budget_from_pct_rounds_half_up(self):
        self.assertEqual(kpi.budget_from_pct(0.025, rm(200000)), rm(5000))
        self.assertEqual(kpi.budget_from_pct(0.0015, rm(200000)), rm(300))
        self.assertIsNone(kpi.budget_from_pct(None, rm(200000)))
        self.assertIsNone(kpi.budget_from_pct(0.025, None))
        self.assertEqual(kpi.budget_from_pct(0.0, rm(200000)), 0)


class BudgetPctServiceTests(unittest.TestCase):
    """End-to-end: pct budget -> computed RM against the active target."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "b.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "a@x.dev", "A", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sid, "2026-10",
            min_rtm_target_cents=rm(200000), level1_base_cents=rm(250000),
            level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5)
        self.cat = next(
            c.id for c in self.app.get_expense_categories(self.admin, self.sid)
            if c.name == "Electricity")

    def tearDown(self):
        self._tmp.cleanup()

    def budget_cents(self):
        rep = self.app.preview_month(self.admin, self.sid, "2026-10",
                                     as_of=date(2026, 10, 31))
        return next(c.budget_cents for c in rep.expenses.categories
                    if c.category_id == self.cat)

    def test_no_sales_budget_uses_minimum_rtm(self):
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat, 0.025)
        self.assertEqual(self.budget_cents(), rm(5000))  # 2.5% x 200,000

    def test_budget_moves_up_as_sales_cross_levels(self):
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat, 0.025)
        # record sales reaching Level 1 base (RM 250,000)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(250000))
        self.assertEqual(self.budget_cents(), rm(6250))  # 2.5% x 250,000
        # reach Level 2 (RM 275,000)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 2),
                              sales_cents=rm(25000))
        self.assertEqual(self.budget_cents(), rm(6875))  # 2.5% x 275,000

    def test_downward_sales_correction_lowers_budget(self):
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat, 0.025)
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(250000))
        self.assertEqual(self.budget_cents(), rm(6250))
        self.app.record_daily(self.admin, self.sid, date(2026, 10, 1),
                              sales_cents=rm(100000))
        self.assertEqual(self.budget_cents(), rm(5000))  # back to Minimum RTM


class DefaultBudgetSeedingTests(unittest.TestCase):
    """Bundled default % ship with the categories and backfill existing
    stores across all configured months, without overwriting user values."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "seed.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "a@x.dev", "A", "Sup3rSecure!x")
        self.sid = self.app.register_store(self.admin, "A01", "Alpha")

    def tearDown(self):
        self._tmp.cleanup()

    def test_defaults_carry_expected_percentages(self):
        self.assertEqual(
            dict(DEFAULT_EXPENSE_CATEGORIES),
            {"Electricity": 0.025, "Water": 0.003, "Consumables": 0.008,
             "Store Supplies": 0.006, "Summons & Penalties": 0.0,
             "Licenses & Permits Fees": 0.004, "Mall/Building Fees": 0.03,
             "Maintenance/Repairs/Renovation": 0.015,
             "Telecommunications": 0.005, "Cash Shortages": 0.0015})

    def test_backfill_seeds_all_configured_months(self):
        self.app.configure_month(self.admin, self.sid, "2026-09")
        self.app.configure_month(self.admin, self.sid, "2026-10")
        self.assertTrue(backfill_default_budget_pct(self.db))
        for month in ("2026-09", "2026-10"):
            budgets = self.app.get_expense_budgets(self.admin, self.sid, month)
            self.assertEqual(
                {c.name: budgets.get(c.id) for c in
                 self.app.get_expense_categories(self.admin, self.sid)},
                DEFAULT_BUDGET_PCT)

    def test_backfill_never_overwrites_user_values(self):
        self.app.configure_month(self.admin, self.sid, "2026-10")
        self.app.set_expense_budget(self.admin, self.sid, "2026-10",
                                    self.cat_id("Electricity"), 0.05)
        backfill_default_budget_pct(self.db)
        budgets = self.app.get_expense_budgets(self.admin, self.sid, "2026-10")
        self.assertEqual(budgets[self.cat_id("Electricity")], 0.05)
        # other categories still received their defaults
        self.assertEqual(budgets[self.cat_id("Water")], 0.003)

    def test_backfill_idempotent(self):
        self.app.configure_month(self.admin, self.sid, "2026-10")
        backfill_default_budget_pct(self.db)
        before = self.app.get_expense_budgets(self.admin, self.sid, "2026-10")
        backfill_default_budget_pct(self.db)
        self.assertEqual(
            self.app.get_expense_budgets(self.admin, self.sid, "2026-10"),
            before)

    def test_custom_category_gets_no_default(self):
        self.app.configure_month(self.admin, self.sid, "2026-10")
        custom = self.app.add_expense_category(self.admin, self.sid, "Custom",
                                               sort_order=99)
        backfill_default_budget_pct(self.db)
        budgets = self.app.get_expense_budgets(self.admin, self.sid, "2026-10")
        self.assertNotIn(custom, budgets)

    def cat_id(self, name):
        return next(c.id for c in self.app.get_expense_categories(
            self.admin, self.sid) if c.name == name)


if __name__ == "__main__":
    unittest.main()
