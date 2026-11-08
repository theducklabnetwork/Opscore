"""Regressions for overview deep links and Actions store-active refreshes."""

import inspect
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest import mock

from ducklab.app_service import AppService


def rm(value):
    return int(Decimal(str(value)) * 100)


class StoreSelectionFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "store-selection.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.store_a = self.app.register_store(self.admin, "X01", "Alpha")
        self.store_b = self.app.register_store(self.admin, "Y02", "Beta")
        for store_id in (self.store_a, self.store_b):
            self.app.configure_month(
                self.admin, store_id, "2026-10",
                level1_base_cents=rm(100000), level2_pct=0.1,
                level3_pct=0.2, level4_pct=0.3)
        self.label_a = "X01 — Alpha"
        self.label_b = "Y02 — Beta"

    def tearDown(self):
        self._tmp.cleanup()

    def run_events(self, state, events):
        """Run an event generator, including its yielded follow-up events."""
        for event in events:
            if event is None:  # Reflex's intermediate state-flush yield
                continue
            result = event.handler.fn(state)
            if inspect.isgenerator(result):
                self.run_events(state, result)


class OverviewStoreParamTests(StoreSelectionFixture):
    def test_store_query_param_is_not_reapplied_on_later_loads(self):
        import ducklab.ops_overview_state as module
        from ducklab.ops_overview_state import OverviewState

        state = OverviewState()
        state.stores = [
            {"id": self.store_a, "label": self.label_a, "active": True},
            {"id": self.store_b, "label": self.label_b, "active": True},
        ]
        state.selected_store_label = self.label_a
        state.selected_month_label = "October 2026"
        state.router.page.params["store"] = str(self.store_b)
        with mock.patch.object(module, "app_service", self.app), \
                mock.patch.object(OverviewState, "require_auth",
                                  lambda _: None), \
                mock.patch.object(module.SharedOpsState, "_populate_selectors",
                                  lambda _, user: None), \
                mock.patch.object(OverviewState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(OverviewState, "_selected_month",
                                  lambda _: "2026-10"):
            self.run_events(state, state.load_overview())
            self.assertEqual(state.selected_store_label, self.label_b)

            # A later selector-driven load must honor the new selection even
            # though the original URL still contains ?store=<store_b>.
            self.run_events(state, state.select_store(self.label_a))
            self.assertEqual(state.selected_store_label, self.label_a)


class ActionsStoreActiveTests(StoreSelectionFixture):
    def test_store_change_refreshes_active_and_write_status(self):
        import ducklab.ops_actions_state as module
        import ducklab.ops_shared_state as shared_module
        from ducklab.ops_actions_state import ActionsState

        self.app.set_store_active(self.admin, self.store_b, False)
        state = ActionsState()
        state.stores = [
            {"id": self.store_a, "label": self.label_a, "active": True},
            {"id": self.store_b, "label": self.label_b, "active": True},
        ]
        state.selected_store_label = self.label_a
        with mock.patch.object(module, "app_service", self.app), \
                mock.patch.object(shared_module, "app_service", self.app), \
                mock.patch.object(ActionsState, "_actor",
                                  lambda _: self.admin), \
                mock.patch.object(ActionsState, "_selected_month",
                                  lambda _: "2026-10"):
            self.run_events(state, state.select_store(self.label_b))
            self.assertFalse(state.selected_store_active)
            self.assertFalse(state.can_write_actions)

            # Reactivation is picked up too, rather than retaining read-only.
            self.app.set_store_active(self.admin, self.store_b, True)
            self.run_events(state, state.select_store(self.label_a))
            self.run_events(state, state.select_store(self.label_b))
            self.assertTrue(state.selected_store_active)
            self.assertTrue(state.can_write_actions)


if __name__ == "__main__":
    unittest.main()
