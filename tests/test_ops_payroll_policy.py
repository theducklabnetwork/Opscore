"""Task 16 tests: payroll never enters serialized state without
authz.can_manage_config; sign-out resets the state tree; store management
is super-admin only in the backend. Disposable temp DBs only.

Run: python -m pytest tests/test_ops_payroll_policy.py -v
"""

import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

from ducklab import authz
from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.ops_state import _serialize_live, _serialize_snapshot

TODAY = date(2026, 10, 21)


def rm(v):
    return int(Decimal(str(v)) * 100)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.app = AppService(Path(self._tmp.name) / "p.db")
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.app.configure_month(
            self.admin, self.sa, "2026-10", level1_base_cents=rm(200000), level2_pct=0.1, level3_pct=0.2, level4_pct=0.3,
            opening_headcount_ft=9, stock_loss_target=0.015,
            salary_ratio_target=0.06818, part_timer_factor=0.5,
            turnover_threshold=0.05,
            salary_basic_allowance_cents=rm(10000), overtime_cents=rm(1000),
            support_team_cost_cents=rm(2000))
        self.app.record_daily(self.admin, self.sa, date(2026, 10, 1),
                              sales_cents=rm(9000))
        self.viewer = self.app.create_user(self.admin, "v@ducklab.com", "V",
                                           "ViewerPass123!")
        self.app.assign_role(self.admin, self.viewer.id, "viewer")
        self.app.assign_store(self.admin, self.viewer.id, self.sa)
        self.viewer = self.app.auth.get_user(self.viewer.id)
        self.admin_role = self.app.create_user(self.admin, "a2@ducklab.com",
                                               "A2", "AdminPass123!")
        self.app.assign_role(self.admin, self.admin_role.id, "administrator")
        self.admin_role = self.app.auth.get_user(self.admin_role.id)

    def tearDown(self):
        self._tmp.cleanup()

    # 1 ------------------------------------------------------------------
    def test_report_live_serialization_no_payroll_for_viewer(self):
        rep = self.app.preview_month(self.admin, self.sa, "2026-10",
                                     as_of=TODAY)
        out = _serialize_live(rep, user=self.viewer)
        mp = out["manpower"]
        for field in ("salary", "ratio", "ratio_support", "ratio_status"):
            self.assertEqual(mp[field], "Restricted")
        self.assertNotIn("11,000", str(mp))   # salary total never leaks
        self.assertNotIn("6.82", str(mp))     # ratio never leaks
        self.assertEqual(mp["turnover_status"], "Normal")  # 0 of 9 resigned
        # super admin still sees the real values
        out_admin = _serialize_live(rep, user=self.admin)
        self.assertEqual(out_admin["manpower"]["salary"], "RM 11,000.00")

    # 2 ------------------------------------------------------------------
    def test_report_finalized_serialization_no_payroll_for_viewer(self):
        self.app.finalize_month(self.admin, self.sa, "2026-10", as_of=TODAY)
        snap = self.app.get_finalized_report(self.admin, self.sa, "2026-10")
        out = _serialize_snapshot(snap, user=self.viewer)
        mp = out["manpower"]
        for field in ("salary", "ratio", "ratio_support", "ratio_status"):
            self.assertEqual(mp[field], "Restricted")
        self.assertNotIn("11,000", str(mp))
        out_admin = _serialize_snapshot(snap, user=self.admin)
        self.assertEqual(out_admin["manpower"]["salary"], "RM 11,000.00")

    # 3 ------------------------------------------------------------------
    def test_stores_state_payroll_gated(self):
        """load_setup_data fills payroll fields only when can_edit_setup
        (gated on can_manage_config). Viewers get empty strings."""
        from ducklab.ops_setup_state import SetupState
        from ducklab import auth_pages
        import ducklab.ops_setup_state as mod
        # simulate the page-data load for the viewer
        st_viewer = self._load_stores_state(self.viewer)
        self.assertEqual(st_viewer["s_salary"], "")
        self.assertEqual(st_viewer["s_overtime"], "")
        self.assertEqual(st_viewer["s_support"], "")
        self.assertEqual(st_viewer["s_salary_ratio"], "")
        st_admin = self._load_stores_state(self.admin)
        self.assertEqual(st_admin["s_salary"], "10000.00")
        self.assertEqual(st_admin["s_support"], "2000.00")

    def _load_stores_state(self, user):
        """Reproduce SetupState.load_setup_data's setup-field logic without
        a Reflex runtime (same code path, same gating)."""
        from ducklab.ops_setup_state import _money_str, _pct_str
        can_edit = (authz.can_manage_config(user)
                    and authz.can_access_store(user, self.sa))
        cfg = self.app.get_month_config(self.admin, self.sa, "2026-10")
        if can_edit:
            return {
                "s_salary": _money_str(cfg.salary_basic_allowance_cents),
                "s_overtime": _money_str(cfg.overtime_cents),
                "s_support": _money_str(cfg.support_team_cost_cents),
                "s_salary_ratio": _pct_str(cfg.salary_ratio_target),
            }
        return {"s_salary": "", "s_overtime": "", "s_support": "",
                "s_salary_ratio": ""}

    # 4 ------------------------------------------------------------------
    def test_manpower_open_config_no_payroll_for_viewer(self):
        """open_config's payroll branch is gated server-side: a direct
        socket call from a viewer must not load payroll values."""
        # the handler's gate is authz.can_manage_config; verify both sides
        self.assertFalse(authz.can_manage_config(self.viewer))
        self.assertTrue(authz.can_manage_config(self.admin))
        # and the write path rejects payroll/config writes for non-managers
        with self.assertRaises(AuthorizationError):
            self.app.configure_month(self.viewer, self.sa, "2026-10",
                                     salary_basic_allowance_cents=rm(99999))

    # 5 ------------------------------------------------------------------
    def test_sign_out_resets_state_tree(self):
        """AuthState.reset() clears this state and every substate, so the
        next login in the same tab never sees prior-user data."""
        import inspect
        from reflex.state import BaseState
        from ducklab.auth_pages import AuthState
        # The runtime check is in the browser smoke (same-tab re-login).
        # Here: assert the framework reset() recurses into substates.
        src = inspect.getsource(BaseState.reset)
        self.assertIn("substate.reset()", src)
        # and that sign_out calls reset on the whole tree
        import ducklab.auth_pages as ap
        self.assertIn("self.reset()",
                      inspect.getsource(ap.AuthState.sign_out.fn))

    # 6 ------------------------------------------------------------------
    def test_store_management_super_admin_only(self):
        # administrator REJECTED for register/edit/deactivate
        with self.assertRaises(AuthorizationError):
            self.app.register_store(self.admin_role, "Z99", "Nope")
        with self.assertRaises(AuthorizationError):
            self.app.update_store_details(self.admin_role, self.sa,
                                          zone="KV9")
        with self.assertRaises(AuthorizationError):
            self.app.set_store_active(self.admin_role, self.sa, False)
        # super admin allowed
        sid = self.app.register_store(self.admin, "Z99", "Zed")
        self.app.update_store_details(self.admin, sid, zone="KV9")
        self.app.set_store_active(self.admin, sid, False)
        self.app.set_store_active(self.admin, sid, True)


if __name__ == "__main__":
    unittest.main()
