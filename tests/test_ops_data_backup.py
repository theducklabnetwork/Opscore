"""S3 tests: Settings > Data — backup & restore of the ops database.

Backup round trip through the SQLite backup API, every restore rejection
case (not SQLite, corrupt, missing tables, newer schema), automatic copies,
and newest-10 retention across migration and restore backups. Disposable temp
DBs only.

Run: python -m pytest tests/test_ops_data_backup.py -v
"""

import shutil
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from ducklab import backup, store_ops_db
from ducklab.app_service import AppService
from ducklab import ops_settings_pages as mod
from ducklab.ops_settings_pages import SettingsState

PW = "Sup3rSecure!x"


def make_db(path: Path) -> None:
    """A fully-initialized app database with a marker row."""
    store_ops_db.init_db(path)
    app = AppService(path)
    app.auth.bootstrap_first_admin("admin@ducklab.com", "Root Admin", PW)


class BackupModuleTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "store_ops.db"
        make_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_backup_round_trip(self):
        """Backup then restore into a fresh file: identical content."""
        copy = self.dir / "copy.db"
        backup.create_backup(self.db, copy)
        # round trip: restore the copy into a third file and compare data
        restored = self.dir / "restored.db"
        restored.touch()
        version = backup.restore(restored, copy)
        self.assertEqual(version, store_ops_db.SCHEMA_VERSION)
        src, dst = sqlite3.connect(self.db), sqlite3.connect(restored)
        for table in ("stores", "auth_users", "schema_migrations"):
            self.assertEqual(
                src.execute(f"SELECT * FROM {table}").fetchall(),
                dst.execute(f"SELECT * FROM {table}").fetchall())
        src.close()
        dst.close()

    def test_download_name_format(self):
        name = backup.download_name(datetime(2026, 10, 25, 14, 30))
        self.assertEqual(name, "opscore-backup-2026-10-25-1430.db")

    def test_reject_not_sqlite(self):
        bad = self.dir / "bad.db"
        bad.write_bytes(b"this is not sqlite at all, plain text content")
        with self.assertRaises(backup.BackupError) as ctx:
            backup.validate_restore(bad)
        self.assertIn("Not a SQLite database", str(ctx.exception))

    def test_reject_corrupt(self):
        bad = self.dir / "corrupt.db"
        # Valid SQLite header, garbage body: opens but fails integrity_check.
        bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
        with self.assertRaises(backup.BackupError) as ctx:
            backup.validate_restore(bad)
        self.assertIn("ntegrity", str(ctx.exception))

    def test_reject_missing_tables(self):
        other = self.dir / "other.db"
        conn = sqlite3.connect(other)
        conn.execute("CREATE TABLE something (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()
        with self.assertRaises(backup.BackupError) as ctx:
            backup.validate_restore(other)
        self.assertIn("Missing expected tables", str(ctx.exception))

    def test_reject_foreign_key_violations(self):
        invalid = self.dir / "invalid-foreign-keys.db"
        shutil.copy(self.db, invalid)
        conn = sqlite3.connect(invalid)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute(
            "INSERT INTO expense_categories (store_id, name) VALUES (?, ?)",
            (999999, "Orphan category"))
        conn.commit()
        conn.close()
        with self.assertRaises(backup.BackupError) as ctx:
            backup.validate_restore(invalid)
        self.assertIn("Foreign-key check failed", str(ctx.exception))

    def test_reject_missing_columns(self):
        invalid = self.dir / "invalid-columns.db"
        shutil.copy(self.db, invalid)
        conn = sqlite3.connect(invalid)
        conn.execute("PRAGMA foreign_keys = OFF")
        conn.execute("DROP TABLE stock_loss_codes")
        conn.execute("CREATE TABLE stock_loss_codes (code TEXT PRIMARY KEY)")
        conn.commit()
        conn.close()
        with self.assertRaises(backup.BackupError) as ctx:
            backup.validate_restore(invalid)
        self.assertIn("Missing expected columns", str(ctx.exception))
        self.assertIn("stock_loss_codes.name", str(ctx.exception))

    def test_reject_newer_schema(self):
        newer = self.dir / "newer.db"
        shutil.copy(self.db, newer)
        conn = sqlite3.connect(newer)
        conn.execute(
            f"PRAGMA user_version = {store_ops_db.SCHEMA_VERSION + 1}")
        conn.commit()
        conn.close()
        with self.assertRaises(backup.BackupError) as ctx:
            backup.validate_restore(newer)
        self.assertIn("newer than this app", str(ctx.exception))

    def test_older_schema_accepted(self):
        older = self.dir / "older.db"
        shutil.copy(self.db, older)
        conn = sqlite3.connect(older)
        conn.execute("DELETE FROM schema_migrations WHERE version > 3")
        conn.execute("PRAGMA user_version = 3")
        conn.commit()
        conn.close()
        self.assertEqual(backup.validate_restore(older), 3)

    def test_restore_v4_backup_migrates_to_current_schema(self):
        """A v4 backup is restored and migrated immediately — no restart."""
        v4 = self.dir / "v4.db"
        shutil.copy(self.db, v4)
        conn = sqlite3.connect(v4)
        conn.execute("ALTER TABLE daily_records DROP COLUMN leave_emergency")
        conn.execute("DROP TABLE reference_data_info")   # v5's table
        for table in ("schedule_entries", "schedule_targets", "staff", "shift_codes"):
            conn.execute(f"DROP TABLE {table}")          # v6's tables
        conn.execute("DELETE FROM schema_migrations WHERE version >= 5")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()
        self.assertEqual(backup.validate_restore(v4), 4)
        pre_version = backup.restore(self.db, v4)
        self.assertEqual(pre_version, 4)
        conn = sqlite3.connect(self.db)
        try:
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(version, store_ops_db.SCHEMA_VERSION)
        self.assertIn("reference_data_info", tables)
        self.assertEqual(ok, "ok")

    def test_corrupt_restore_leaves_live_db_unchanged(self):
        corrupt = self.dir / "corrupt-restore.db"
        corrupt.write_bytes(b"SQLite format 3\x00" + b"\xff" * 4096)
        before = self.db.read_bytes()
        with self.assertRaises(backup.BackupError):
            backup.restore(self.db, corrupt)
        self.assertEqual(self.db.read_bytes(), before)

    def test_migration_failure_leaves_live_db_unchanged(self):
        older = self.dir / "migration-failure.db"
        shutil.copy(self.db, older)
        conn = sqlite3.connect(older)
        conn.execute("DELETE FROM schema_migrations WHERE version > 4")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()
        before = self.db.read_bytes()
        with mock.patch.object(
                store_ops_db, "init_db",
                side_effect=sqlite3.OperationalError("forced migration error")):
            with self.assertRaises(backup.BackupError) as ctx:
                backup.restore(self.db, older)
        self.assertIn("Database migration failed", str(ctx.exception))
        self.assertEqual(self.db.read_bytes(), before)

    def test_restore_rejects_inconsistent_audit_without_touching_live_db(self):
        incoming = self.dir / "inconsistent.db"
        backup.create_backup(self.db, incoming)
        conn = sqlite3.connect(incoming)
        conn.execute("DELETE FROM schema_migrations WHERE version = 7")
        conn.commit()
        conn.close()
        before = self.db.read_bytes()
        with self.assertRaisesRegex(backup.BackupError, "migration audit"):
            backup.restore(self.db, incoming)
        self.assertEqual(self.db.read_bytes(), before)
        self.assertFalse(list(self.dir.glob(".store_ops.db.restore-*")))

    def test_backup_captures_committed_wal_content(self):
        conn = store_ops_db.connect(self.db)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("INSERT INTO stores (code, name) VALUES ('WAL', 'WAL committed')")
        conn.commit()
        self.assertGreater((self.dir / "store_ops.db-wal").stat().st_size, 0)
        copy = self.dir / "wal-copy.db"
        backup.create_backup(self.db, copy)
        copied = sqlite3.connect(copy)
        self.assertEqual(copied.execute(
            "SELECT name FROM stores WHERE code = 'WAL'").fetchone()[0], "WAL committed")
        copied.close()
        conn.close()

    def test_restore_wal_database_is_consistent_and_leaves_no_stale_sidecars(self):
        conn = store_ops_db.connect(self.db)
        conn.execute("INSERT INTO stores (code, name) VALUES ('LIVE', 'Live WAL')")
        conn.commit()
        self.assertTrue((self.dir / "store_ops.db-wal").exists())
        incoming = self.dir / "incoming-wal.db"
        backup.create_backup(self.db, incoming)
        source = store_ops_db.connect(incoming)
        source.execute("UPDATE stores SET name = 'Restored WAL' WHERE code = 'LIVE'")
        source.commit()
        # Simulate receiving the main database plus stale/mismatched sidecars.
        incoming_main = self.dir / "received.db"
        shutil.copyfile(incoming, incoming_main)
        source.close()
        incoming.unlink()
        incoming_main.rename(incoming)
        # A stale uploaded sidecar must not be applied to the main file.
        (self.dir / "incoming-wal.db-wal").write_bytes(b"stale")
        (self.dir / "incoming-wal.db-shm").write_bytes(b"stale")
        conn.close()
        backup.restore(self.db, incoming)
        restored = store_ops_db.connect(self.db)
        # The main file was copied before the WAL commit; stale sidecars must
        # not be replayed, so the self-contained main file's value is restored.
        self.assertEqual(restored.execute(
            "SELECT name FROM stores WHERE code = 'LIVE'").fetchone()[0], "Live WAL")
        self.assertEqual(restored.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(restored.execute("PRAGMA foreign_key_check").fetchall(), [])
        restored.close()
        self.assertFalse(list(self.dir.glob(".store_ops.db.restore-*")))

    def test_restore_refuses_open_wal_connections_without_replacing(self):
        incoming = self.dir / "locked-restore.db"
        backup.create_backup(self.db, incoming)
        conn = store_ops_db.connect(self.db)
        conn.execute("INSERT INTO stores (code, name) VALUES ('KEEP', 'Current')")
        conn.commit()
        with self.assertRaisesRegex(backup.BackupError, "replace the database safely"):
            backup.restore(self.db, incoming)
        check = sqlite3.connect(self.db)
        self.assertEqual(check.execute(
            "SELECT name FROM stores WHERE code = 'KEEP'").fetchone()[0], "Current")
        check.close()
        conn.close()
        self.assertFalse(list(self.dir.glob(".store_ops.db.restore-*")))

    def test_pre_restore_copy_created(self):
        saved = backup.save_pre_restore_backup(
            self.db, now=datetime(2026, 10, 25, 9, 15, 0))
        self.assertTrue(saved.exists())
        self.assertEqual(saved.parent, self.dir / "backups")
        self.assertEqual(saved.name, "pre-restore-20261025-091500.db")
        # and it is a valid copy of the current database
        self.assertEqual(
            backup.validate_restore(saved), store_ops_db.SCHEMA_VERSION)

    def test_automatic_backups_keep_ten_newest_across_both_kinds(self):
        base = datetime(2026, 10, 20, 8, 0, 0)
        for i in range(7):
            backup.save_pre_restore_backup(self.db,
                                           now=base + timedelta(hours=i))
            backup.save_pre_migration_backup(
                self.db, 10, 11, now=base + timedelta(hours=i, minutes=30))
        items = backup.list_automatic_backups(self.db)
        self.assertEqual(len(items), 10)
        self.assertTrue(items[0].name > items[-1].name)
        self.assertTrue(all(b.size_bytes > 0 for b in items))
        self.assertEqual(len(list((self.dir / "backups").glob("*.db"))), 10)

    def test_automatic_backup_names_never_overwrite_same_second_copy(self):
        now = datetime(2026, 10, 25, 9, 15, 0)
        first = backup.save_pre_restore_backup(self.db, now=now)
        second = backup.save_pre_restore_backup(self.db, now=now)
        self.assertNotEqual(first, second)
        self.assertEqual(second.name, "pre-restore-20261025-091500-1.db")
        self.assertEqual(backup.validate_restore(first),
                         store_ops_db.SCHEMA_VERSION)

    def test_pruning_preserves_manual_and_unrecognized_files(self):
        preserved = [
            self.dir / "opscore-backup-2026-10-25-0915.db",
            self.dir / "store_ops.db.backup-1",
            self.dir / "store_ops.db.bak-20261025-091500",
            self.dir / "backups" / "manual.db",
        ]
        for path in preserved:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"keep")
        base = datetime(2026, 10, 20, 8, 0, 0)
        for i in range(11):
            backup.save_pre_restore_backup(self.db,
                                           now=base + timedelta(hours=i))
        self.assertEqual(len(backup.list_automatic_backups(self.db)), 10)
        for path in preserved:
            self.assertEqual(path.read_bytes(), b"keep")

    def test_prune_failure_does_not_invalidate_new_backup(self):
        saved = backup.save_pre_restore_backup(self.db)
        doomed = self.dir / "backups" / "pre-restore-20200101-000000.db"
        doomed.write_bytes(b"old")
        with mock.patch.object(backup.Path, "unlink",
                               side_effect=OSError("locked")):
            backup.prune_automatic_backups(self.db, keep=1)
        self.assertTrue(saved.is_file())
        self.assertTrue(doomed.is_file())

    def test_symlinked_backup_directory_is_not_enumerated(self):
        external = self.dir / "external"
        external.mkdir()
        (external / "pre-restore-20200101-000000.db").write_bytes(b"keep")
        backup_dir = self.dir / "backups"
        backup_dir.symlink_to(external, target_is_directory=True)
        self.assertEqual(backup.list_automatic_backups(self.db), [])
        self.assertTrue((external / "pre-restore-20200101-000000.db").is_file())

    def test_restore_migration_does_not_make_extra_migration_backup(self):
        older = self.dir / "older.db"
        shutil.copy(self.db, older)
        conn = sqlite3.connect(older)
        conn.execute("ALTER TABLE daily_records DROP COLUMN leave_emergency")
        conn.execute("DROP TABLE reference_data_info")
        for table in ("schedule_entries", "schedule_targets", "staff", "shift_codes"):
            conn.execute(f"DROP TABLE {table}")
        conn.execute("DELETE FROM schema_migrations WHERE version >= 5")
        conn.execute("PRAGMA user_version = 4")
        conn.commit()
        conn.close()
        with mock.patch("ducklab.backup.save_pre_migration_backup") as save, \
                mock.patch("ducklab.store_ops_db.init_db", wraps=store_ops_db.init_db) as init:
            backup.restore(self.db, older)
        self.assertFalse(init.call_args.kwargs["backup_before_migration"])
        save.assert_not_called()


class SettingsDataTabTest(unittest.TestCase):
    """State-level wiring: tab visibility, backup download event, restore
    confirm signing out, and access control."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        self.db = self.dir / "store_ops.db"
        self.app = AppService(self.db)
        self.admin = self.app.auth.bootstrap_first_admin(
            "admin@ducklab.com", "Root Admin", PW)
        store_ops_db.init_db(self.db)

    def tearDown(self):
        self._tmp.cleanup()

    def state(self, actor) -> SettingsState:
        st = SettingsState()
        with mock.patch.object(mod, "app_service", self.app), \
                mock.patch.object(mod, "DB_PATH", self.db), \
                mock.patch.object(SettingsState, "_actor", lambda s_: actor):
            st.load_settings()
        return st

    def test_data_tab_super_admin_only(self):
        st = self.state(self.admin)
        self.assertIn("data", st.visible_tabs)
        viewer = self.app.create_user(self.admin, "v@ducklab.com", "V", PW)
        self.app.assign_role(self.admin, viewer.id, "viewer")
        st2 = self.state(self.app.auth.get_user(viewer.id))
        self.assertNotIn("data", st2.visible_tabs)

    def test_load_lists_backups(self):
        backup.save_pre_restore_backup(self.db)
        st = self.state(self.admin)
        self.assertEqual(len(st.data_backups), 1)
        self.assertIn("pre-restore-", st.data_backups[0]["name"])

    def test_restore_replaces_and_saves_pre_copy(self):
        """End-to-end through backup.py: pre-copy exists, content swapped."""
        other = self.dir / "incoming.db"
        make_db(other)
        conn = sqlite3.connect(other)
        conn.execute("UPDATE auth_users SET display_name = 'Restored Admin'")
        conn.commit()
        conn.close()
        pre = backup.save_pre_restore_backup(self.db)
        backup.restore(self.db, other)
        conn = sqlite3.connect(self.db)
        name = conn.execute(
            "SELECT display_name FROM auth_users").fetchone()[0]
        conn.close()
        self.assertEqual(name, "Restored Admin")
        self.assertTrue(pre.exists())


if __name__ == "__main__":
    unittest.main()
