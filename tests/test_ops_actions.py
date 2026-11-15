"""Tests for Action Tracker: overdue boundary, status semantics, required
fields, completion-date rules, carried-over visibility, multi-store
isolation, viewer RBAC, and the v2 audit-column migration.
Disposable temp DBs only.

Run: python -m pytest tests/test_ops_actions.py -v
"""

import shutil
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from ducklab.app_service import AppService
from ducklab.auth_service import AuthorizationError
from ducklab.operations_write_repository import ValidationError

TODAY = date(2026, 10, 21)


class ActionFixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.db = Path(self._tmp.name) / "act.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root", "Sup3rSecure!x")
        self.sa = self.app.register_store(self.admin, "A01", "Alpha")
        self.sb = self.app.register_store(self.admin, "B02", "Beta")
        self.viewer = self.app.create_user(
            self.admin, "viewer@ducklab.com", "V", "ViewerPass123!")
        self.app.assign_role(self.admin, self.viewer.id, "viewer")
        self.app.assign_store(self.admin, self.viewer.id, self.sa)

    def tearDown(self):
        self._tmp.cleanup()

    def save(self, store=None, **over):
        f = dict(raised_date=TODAY - timedelta(days=5),
                 pillar="Stock Management", issue="Fixture issue",
                 priority="Medium", status="Open", pic="DEV Owner",
                 due_date=TODAY + timedelta(days=3), corrective_action=None,
                 completion_date=None, remarks=None)
        f.update(over)
        return self.app.save_action(self.admin, store or self.sa, None,
                                    TODAY, **f)

    def counts(self, store=None, month=None):
        rows = self.app.get_actions(self.admin, store or self.sa)
        from ducklab import kpi
        c = kpi.action_counts([a.input for a in rows], as_of=TODAY)
        return c, kpi.open_actions(c)

    # 1 ------------------------------------------------------------------
    def test_overdue_boundary(self):
        self.save(due_date=TODAY)                    # due today: NOT overdue
        self.save(due_date=TODAY - timedelta(days=1))  # yesterday: overdue
        c, _ = self.counts()
        self.assertEqual(c["overdue"], 1)

    # 2 ------------------------------------------------------------------
    def test_done_and_cancelled_never_overdue_or_open(self):
        self.save(due_date=TODAY - timedelta(days=2), status="Done")
        self.save(due_date=TODAY - timedelta(days=2), status="Cancelled")
        c, open_n = self.counts()
        self.assertEqual(c["overdue"], 0)
        self.assertEqual(open_n, 0)
        self.assertEqual(c["done"], 1)
        self.assertEqual(c["cancelled"], 1)

    # 3 ------------------------------------------------------------------
    def test_open_count_includes_blocked_and_in_progress(self):
        self.save(status="Open")
        self.save(status="In progress")
        self.save(status="Blocked")
        c, open_n = self.counts()
        self.assertEqual(open_n, 3)

    # 4 ------------------------------------------------------------------
    def test_owner_and_due_date_required(self):
        with self.assertRaises(ValidationError):
            self.save(pic="   ")
        with self.assertRaises(ValidationError):
            self.save(due_date=None)
        with self.assertRaises(ValidationError):
            self.save(issue="")

    # 5 ------------------------------------------------------------------
    def test_done_requires_completion_non_done_clears_it(self):
        aid = self.save(status="Done", completion_date=None)
        row = next(a for a in self.app.get_actions(self.admin, self.sa)
                   if a.id == aid)
        self.assertEqual(row.completion_date, TODAY)      # default = today
        # switch back to Open: completion must be cleared
        self.app.save_action(self.admin, self.sa, aid, TODAY,
                             raised_date=row.raised_date,
                             pillar=row.input.pillar, issue=row.issue,
                             priority=row.priority, status="Open",
                             pic=row.pic, due_date=row.input.due_date,
                             corrective_action=None,
                             completion_date=row.completion_date,
                             remarks=None)
        row = next(a for a in self.app.get_actions(self.admin, self.sa)
                   if a.id == aid)
        self.assertIsNone(row.completion_date)

    # 6 ------------------------------------------------------------------
    def test_completion_before_raised_rejected(self):
        with self.assertRaises(ValidationError):
            self.save(status="Done",
                      raised_date=TODAY,
                      completion_date=TODAY - timedelta(days=1))

    # 7 ------------------------------------------------------------------
    def test_carried_over_visibility(self):
        old = self.save(raised_date=date(2026, 9, 10), status="Open")
        self.save(raised_date=date(2026, 10, 2), status="Done",
                  due_date=TODAY + timedelta(days=1))
        # state visibility rule: raised-in-month OR still open
        rows = self.app.get_actions(self.admin, self.sa)
        visible_oct = [a for a in rows
                       if (a.raised_date and a.raised_date.strftime("%Y-%m")
                           == "2026-10")
                       or (a.input.status or "Open") in
                       ("Open", "In progress", "Blocked")]
        ids = {a.id for a in visible_oct}
        self.assertIn(old, ids)       # carried over (still open)
        self.assertEqual(len(visible_oct), 2)

    # 8 ------------------------------------------------------------------
    def test_multi_store_isolation(self):
        self.save(store=self.sa)
        self.save(store=self.sb)
        self.save(store=self.sb)
        a_rows = self.app.get_actions(self.admin, self.sa)
        b_rows = self.app.get_actions(self.admin, self.sb)
        self.assertEqual(len(a_rows), 1)
        self.assertEqual(len(b_rows), 2)

    # 9 ------------------------------------------------------------------
    def test_viewer_write_rejected(self):
        with self.assertRaises(AuthorizationError):
            self.app.save_action(self.viewer, self.sa, None, TODAY,
                                 raised_date=TODAY, pillar="General Operations",
                                 issue="Nope", priority="Low", status="Open",
                                 pic="X", due_date=TODAY,
                                 corrective_action=None, completion_date=None,
                                 remarks=None)
        # viewer can still read an assigned store (re-fetch: assignments
        # are baked into the User object at read time)
        viewer = self.app.auth.get_user(self.viewer.id)
        self.assertEqual(self.app.get_actions(viewer, self.sa), [])
        with self.assertRaises(AuthorizationError):
            # viewer has no access to an unassigned store at all
            self.app.get_actions(viewer, self.sb)

    # 10 -----------------------------------------------------------------
    def test_migration_v2_preserves_data_and_audits(self):
        # build a v1 database by hand, then migrate
        import sqlite3
        from ducklab import store_ops_db
        v1db = Path(self._tmp.name) / "v1.db"
        conn = sqlite3.connect(v1db)
        store_ops_db._apply_v1(conn)
        conn.execute("PRAGMA user_version = 1")
        conn.execute(
            "INSERT INTO stores (code, name) VALUES ('X9', 'Legacy')")
        conn.execute(
            "INSERT INTO actions (store_id, raised_date, pillar, issue, "
            "priority, status, pic, due_date) VALUES (1, '2026-10-01', "
            "'General Operations', 'Legacy action', 'Low', 'Open', 'Old', "
            "'2026-10-31')")
        conn.commit()
        conn.close()
        store_ops_db.init_db(v1db)          # migrate 1 -> 2 -> latest
        conn = sqlite3.connect(v1db)
        ver = conn.execute("PRAGMA user_version").fetchone()[0]
        self.assertEqual(ver, store_ops_db.SCHEMA_VERSION)
        row = conn.execute("SELECT issue, created_by FROM actions").fetchone()
        self.assertEqual(row[0], "Legacy action")
        self.assertIsNone(row[1])           # old rows: NULL audit data
        # new writes stamp audit columns via the repository
        from ducklab.operations_write_repository import (
            OperationsWriteRepository)
        repo = OperationsWriteRepository(v1db)
        aid = repo.create_action(
            1, 42, raised_date=date(2026, 10, 2), pillar="Stock Management",
            issue="New", priority="High", status="Open", pic="N",
            due_date=date(2026, 10, 30))
        a = conn.execute(
            "SELECT created_by, created_at FROM actions WHERE id=?",
            (aid,)).fetchone()
        self.assertEqual(a[0], 42)
        self.assertIsNotNone(a[1])
        conn.close()


if __name__ == "__main__":
    unittest.main()
