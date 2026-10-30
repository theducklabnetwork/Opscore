"""Phase 3.6 regression: flat page-state inheritance.

Guards the two invariants the flat shape relies on:

1. Every /ops page state inherits SharedOpsState DIRECTLY (except
   OpsState, which defines it); no page state may inherit from another
   page's state — the DailyState->...->OverviewState linchpin chain must
   not grow back.

2. Sibling page states hold no data on each other: a value written on one
   sibling's instance never leaks into another sibling's instance, and a
   sibling cannot even address another page's fields (the Reflex
   dev-mode __setattr__ guard makes that an error, not a silent alias).

Run: python -m pytest tests/test_phase36_flat_state.py -q
"""

from __future__ import annotations

import unittest

import reflex as rx

from ducklab.ops_actions_state import ActionsState
from ducklab.ops_assignment_state import AssignmentState
from ducklab.ops_daily_state import DailyState
from ducklab.ops_expenses_state import ExpensesState
from ducklab.ops_manpower_state import ManpowerState
from ducklab.ops_overview_state import OverviewState
from ducklab.ops_sales_state import SalesState
from ducklab.ops_schedule_state import ScheduleState
from ducklab.ops_settings_pages import SettingsState
from ducklab.ops_setup_state import SetupState
from ducklab.ops_shared_state import SharedOpsState
from ducklab.ops_state import OpsState
from ducklab.ops_stock_state import StockState
from ducklab.ops_stores_state import StoresState

PAGE_STATES = (
    DailyState, ExpensesState, ScheduleState, ManpowerState, StockState,
    AssignmentState, SetupState, StoresState, SalesState, ActionsState,
    OverviewState, SettingsState,
)


class FlatShapeTests(unittest.TestCase):
    def test_every_page_state_inherits_shared_base_directly(self):
        for cls in PAGE_STATES:
            self.assertIs(cls.__mro__[1], SharedOpsState,
                          f"{cls.__name__} must inherit SharedOpsState, "
                          f"got {cls.__mro__[1].__name__}")

    def test_ops_state_is_the_only_shared_base_subclass_extra(self):
        # OpsState subclasses SharedOpsState for the report page; it is the
        # only class besides PAGE_STATES allowed to.
        self.assertIs(OpsState.__mro__[1], SharedOpsState)

    def test_no_page_state_inherits_from_another_page_state(self):
        for cls in PAGE_STATES:
            for other in PAGE_STATES:
                if cls is other:
                    continue
                self.assertFalse(
                    issubclass(cls, other),
                    f"{cls.__name__} must not subclass {other.__name__}")


class SiblingIsolationTests(unittest.TestCase):
    """Q4 acceptance: dropping SalesState._clear_all_modules is safe only
    because sibling states keep no data on each other."""

    def _fresh(self, cls):
        # A bare instance outside an app context is the cheapest stand-in
        # for "this page's state object" (bookkeeping included).
        return cls()

    def test_field_write_on_one_sibling_is_invisible_on_the_other(self):
        daily = self._fresh(DailyState)
        expenses = self._fresh(ExpensesState)
        daily.day_rows = [{"day": "x"}]
        expenses.exp_notice = "hello"
        # each sibling's fields exist only on its own class/instance
        self.assertEqual(daily.day_rows, [{"day": "x"}])
        self.assertEqual(expenses.exp_notice, "hello")
        with self.assertRaises(AttributeError):
            getattr(expenses, "day_rows")
        with self.assertRaises(AttributeError):
            getattr(daily, "exp_notice")

    def test_default_lists_are_not_shared_between_instances(self):
        a = self._fresh(DailyState)
        b = self._fresh(DailyState)
        a.day_rows.append({"day": "1"})
        self.assertEqual(b.day_rows, [])

    def test_sibling_fields_are_not_settable_across_classes(self):
        daily = self._fresh(DailyState)
        with self.assertRaises((AttributeError, rx.utils.exceptions
                                .SetUndefinedStateVarError)):
            daily.exp_notice = "leak"

    def test_shared_selector_fields_resolve_to_the_same_owner(self):
        # Shared members (selectors, status surface) stay single-source:
        # every sibling's field object is owned by SharedOpsState, so at
        # runtime all pages read the one shared instance's value.
        for cls in (DailyState, ExpensesState, StoresState, SalesState):
            field = cls.__fields__["selected_store_label"]
            self.assertIs(field._owner, SharedOpsState)


if __name__ == "__main__":
    unittest.main()
