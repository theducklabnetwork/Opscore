"""Source-checkout behavior of the cross-platform desktop launcher."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from tests.test_frozen_backend_env import load_desktop_module


class TestSourceEntrypoint(unittest.TestCase):
    def test_imports_app_package_from_source_checkout(self):
        root = Path(__file__).resolve().parent.parent
        result = subprocess.run(
            [sys.executable, "-c",
             "import runpy; module = runpy.run_path('packaging/opscore_desktop.py'); "
             "print(module['DATA_DIR'])"],
            cwd=root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.strip().endswith("opscore"))


class TestSourceBackend(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.app_root = Path(self.tmp.name)
        (self.app_root / "packaging").mkdir()
        self.backend = mock.Mock()
        self.backend.stdout = mock.MagicMock()
        self.backend.stdout.readline.side_effect = [b""]
        self.patches = [
            mock.patch.object(self.desktop, "APP_ROOT", self.app_root),
            mock.patch.object(self.desktop, "LOG_PATH", self.app_root / "opscore.log"),
            mock.patch.object(self.desktop, "URL_PATH", self.app_root / "current.json"),
            mock.patch.object(self.desktop, "wait_until_ready"),
            mock.patch.object(self.desktop.platform_utils, "start_backend", return_value=self.backend),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        self.desktop.BACKEND = None
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def test_linux_uses_existing_packaged_launcher(self):
        with mock.patch.object(self.desktop.sys, "platform", "linux"):
            self.desktop.start_backend()
        command = self.desktop.platform_utils.start_backend.call_args.args[0]
        self.assertEqual(command, [str(self.app_root / "packaging" / "opscore-backend")])

    def test_windows_runs_backend_with_current_python(self):
        with mock.patch.object(self.desktop.sys, "platform", "win32"):
            self.desktop.start_backend()
        call = self.desktop.platform_utils.start_backend.call_args
        self.assertEqual(
            call.args[0],
            [str(Path(self.desktop.sys.executable).resolve()),
             str(self.app_root / "packaging" / "opscore_backend.py")],
        )
        self.assertEqual(call.kwargs["env"]["DUCKLAB_RELEASE_ID"], "source")


class TestWebviewProfileUpgrade(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.app_root = self.data_dir / "bundle"
        self.app_root.mkdir()
        (self.app_root / "RELEASE").write_text("2.0\n")
        self.profile = self.data_dir / "webview"
        self.profile.mkdir()
        (self.profile / "browser-state").write_text("stale")
        self.protected = {
            name: self.data_dir / name
            for name in ("store_ops.db", "uploaded_files", "states", "reflex-web")
        }
        for name, path in self.protected.items():
            if name == "store_ops.db":
                path.write_text("database")
            else:
                path.mkdir()
                (path / "keep").write_text(name)
        self.patches = [
            mock.patch.object(self.desktop, "DATA_DIR", self.data_dir),
            mock.patch.object(self.desktop, "APP_ROOT", self.app_root),
            mock.patch.object(self.desktop, "VERSION_PATH", self.data_dir / "app_version.txt"),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def assert_protected_data_unchanged(self):
        for name, path in self.protected.items():
            if name == "store_ops.db":
                self.assertEqual(path.read_text(), "database")
            else:
                self.assertEqual((path / "keep").read_text(), name)

    def test_version_change_moves_webview_profile(self):
        (self.data_dir / "app_version.txt").write_text("1.0\n")
        with mock.patch.object(self.desktop.time, "strftime", return_value="20261109-120000"):
            self.desktop.prepare_webview_profile()
        moved = self.data_dir / "webview.old-20261109-120000"
        self.assertFalse(self.profile.exists())
        self.assertEqual((moved / "browser-state").read_text(), "stale")
        self.assertEqual((self.data_dir / "app_version.txt").read_text(), "2.0\n")
        self.assert_protected_data_unchanged()

    def test_missing_version_file_moves_webview_profile(self):
        with mock.patch.object(self.desktop.time, "strftime", return_value="20261109-120001"):
            self.desktop.prepare_webview_profile()
        self.assertFalse(self.profile.exists())
        self.assertTrue((self.data_dir / "webview.old-20261109-120001").is_dir())
        self.assertEqual((self.data_dir / "app_version.txt").read_text(), "2.0\n")
        self.assert_protected_data_unchanged()

    def test_same_version_leaves_webview_profile_alone(self):
        (self.data_dir / "app_version.txt").write_text("2.0\n")
        self.desktop.prepare_webview_profile()
        self.assertEqual((self.profile / "browser-state").read_text(), "stale")
        self.assertEqual(list(self.data_dir.glob("webview.old-*")), [])
        self.assert_protected_data_unchanged()

    def test_old_profiles_keep_newest_three_and_ignore_unrelated_paths(self):
        for timestamp in ("20261101-000000", "20261102-000000",
                          "20261103-000000", "20261104-000000"):
            path = self.data_dir / f"webview.old-{timestamp}"
            path.mkdir()
            (path / "marker").write_text(timestamp)
        outside = self.data_dir / "outside"
        outside.mkdir()
        (outside / "keep").write_text("yes")
        (self.data_dir / "webview.old-20200101-000000").symlink_to(
            outside, target_is_directory=True)
        unrelated = self.data_dir / "webview.old-not-a-timestamp"
        unrelated.mkdir()
        self.desktop.prune_webview_profiles()
        self.assertFalse((self.data_dir / "webview.old-20261101-000000").exists())
        self.assertTrue((self.data_dir / "webview.old-20261104-000000").exists())
        self.assertTrue((outside / "keep").exists())
        self.assertTrue(unrelated.exists())

    def test_windows_launch_prepares_profile_before_window(self):
        process = mock.Mock()
        with mock.patch.object(self.desktop.sys, "platform", "win32"), \
                mock.patch.object(self.desktop.sys, "argv", ["opscore_desktop.py"]), \
                mock.patch.object(self.desktop, "acquire_lock", return_value=mock.Mock()), \
                mock.patch.object(self.desktop, "active_app", return_value=None), \
                mock.patch.object(self.desktop, "windows_port_available", return_value=True), \
                mock.patch.object(self.desktop, "start_backend", return_value=(process, self.desktop.APP_URL)), \
                mock.patch.object(self.desktop, "open_window", side_effect=lambda *_, **__: (
                    self.assertFalse(self.profile.exists()), 0)[1]), \
                mock.patch.object(self.desktop, "stop_backend"):
            self.assertEqual(self.desktop.main(), 0)
        self.assertTrue(any(self.data_dir.glob("webview.old-*")))
        self.assertEqual((self.data_dir / "app_version.txt").read_text(), "2.0\n")


class TestDatabaseChoice(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self.tmp.name)
        self.db = self.data_dir / "store_ops.db"
        self.choice_path = self.data_dir / "db-choice.json"
        self.version_path = self.data_dir / "app_version.txt"
        self.patches = [
            mock.patch.object(self.desktop, "DATA_DIR", self.data_dir),
            mock.patch.object(self.desktop, "DB_PATH", self.db),
            mock.patch.object(self.desktop, "DB_CHOICE_PATH", self.choice_path),
            mock.patch.object(self.desktop, "VERSION_PATH", self.version_path),
            mock.patch.object(self.desktop, "installed_version", return_value="2.0"),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def test_backup_numbering_uses_lowest_free_number(self):
        self.assertEqual(self.desktop.next_backup_path(self.db).name, "store_ops.db.backup-1")
        (self.data_dir / "store_ops.db.backup-1").write_text("old")
        self.assertEqual(self.desktop.next_backup_path(self.db).name, "store_ops.db.backup-2")
        (self.data_dir / "store_ops.db.backup-3").write_text("old")
        self.assertEqual(self.desktop.next_backup_path(self.db).name, "store_ops.db.backup-2")

    def test_archive_moves_database_to_numbered_backup(self):
        self.db.write_text("database")
        backup = self.desktop.archive_existing_database()
        self.assertFalse(self.db.exists())
        self.assertEqual(backup.name, "store_ops.db.backup-1")
        self.assertEqual(backup.read_text(), "database")

    def test_archive_checkpoints_wal_into_self_contained_backup(self):
        import sqlite3
        from ducklab import store_ops_db
        store_ops_db.init_db(self.db)
        conn = store_ops_db.connect(self.db)
        conn.execute("INSERT INTO stores (code, name) VALUES ('WAL', 'Preserved')")
        conn.commit()
        conn.close()
        backup = self.desktop.archive_existing_database()
        copied = sqlite3.connect(f"{backup.resolve().as_uri()}?immutable=1", uri=True)
        self.assertEqual(copied.execute(
            "SELECT name FROM stores WHERE code = 'WAL'").fetchone()[0], "Preserved")
        copied.close()
        self.assertFalse(self.db.with_name(self.db.name + "-wal").exists())
        self.assertFalse(self.db.with_name(self.db.name + "-shm").exists())

    def test_fresh_start_backups_keep_ten_and_ignore_manual_files(self):
        for number in range(1, 13):
            (self.data_dir / f"store_ops.db.backup-{number}").write_text(str(number))
        unrelated = self.data_dir / "opscore-backup-2026-10-25-0915.db"
        unrelated.write_text("manual")
        external = self.data_dir / "external"
        external.mkdir()
        link = self.data_dir / "store_ops.db.backup-99"
        link.symlink_to(external / "outside")
        self.db.write_text("new")
        self.desktop.archive_existing_database()
        remaining = sorted(path.name for path in self.data_dir.glob("store_ops.db.backup-*")
                           if not path.is_symlink())
        self.assertEqual(len(remaining), 10)
        self.assertNotIn("store_ops.db.backup-1", remaining)
        self.assertNotIn("store_ops.db.backup-2", remaining)
        self.assertIn("store_ops.db.backup-12", remaining)
        self.assertEqual(unrelated.read_text(), "manual")
        self.assertTrue(link.is_symlink())

    def test_fresh_start_prune_failure_does_not_block_archive(self):
        self.db.write_text("new")
        (self.data_dir / "store_ops.db.backup-1").write_text("old")
        with mock.patch.object(self.desktop, "FRESH_START_BACKUP_RETENTION", 0), \
                mock.patch.object(self.desktop.Path, "unlink", side_effect=OSError("locked")):
            backup = self.desktop.archive_existing_database()
        self.assertEqual(backup.read_text(), "new")

    def test_no_database_never_prompts(self):
        with mock.patch.object(self.desktop, "ask_use_existing_database") as ask:
            self.desktop.prepare_database_choice()
        ask.assert_not_called()
        self.assertFalse(self.choice_path.exists())

    def test_same_version_launch_never_prompts(self):
        self.db.write_text("database")
        self.version_path.write_text("2.0\n")
        with mock.patch.object(self.desktop, "ask_use_existing_database") as ask:
            self.desktop.prepare_database_choice()
        ask.assert_not_called()
        self.assertEqual(self.db.read_text(), "database")

    def test_upgrade_launch_keeps_database_by_default(self):
        self.db.write_text("database")
        self.version_path.write_text("1.0\n")
        with mock.patch.object(self.desktop, "ask_use_existing_database", return_value=True) as ask:
            self.desktop.prepare_database_choice()
        ask.assert_called_once_with()
        self.assertEqual(self.db.read_text(), "database")
        import json
        record = json.loads(self.choice_path.read_text())
        self.assertEqual(record, {"version": "2.0", "choice": "keep"})

    def test_upgrade_launch_fresh_archives_database(self):
        self.db.write_text("database")
        with mock.patch.object(self.desktop, "ask_use_existing_database", return_value=False):
            self.desktop.prepare_database_choice()
        self.assertFalse(self.db.exists())
        self.assertEqual((self.data_dir / "store_ops.db.backup-1").read_text(), "database")
        import json
        record = json.loads(self.choice_path.read_text())
        self.assertEqual(record, {"version": "2.0", "choice": "fresh"})

    def test_installer_fresh_choice_skips_dialog(self):
        self.db.write_text("database")
        self.choice_path.write_text('{"version": "2.0", "choice": "fresh"}\n')
        with mock.patch.object(self.desktop, "ask_use_existing_database") as ask:
            self.desktop.prepare_database_choice()
        ask.assert_not_called()
        self.assertFalse(self.db.exists())
        self.assertTrue((self.data_dir / "store_ops.db.backup-1").exists())

    def test_installer_keep_choice_skips_dialog(self):
        self.db.write_text("database")
        self.choice_path.write_text('{"version": "2.0", "choice": "keep"}\n')
        with mock.patch.object(self.desktop, "ask_use_existing_database") as ask:
            self.desktop.prepare_database_choice()
        ask.assert_not_called()
        self.assertEqual(self.db.read_text(), "database")

    def test_stale_installer_choice_keeps_database(self):
        self.db.write_text("database")
        self.choice_path.write_text('{"version": "1.0", "choice": "fresh"}\n')
        with mock.patch.object(self.desktop, "ask_use_existing_database") as ask:
            self.desktop.prepare_database_choice()
        ask.assert_not_called()
        self.assertEqual(self.db.read_text(), "database")

    def test_malformed_installer_choice_is_discarded(self):
        self.choice_path.write_text("not json")
        self.assertIsNone(self.desktop.consume_installer_db_choice())
        self.assertFalse(self.choice_path.exists())


class TestPortCheck(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()

    def test_windows_bind_free_port(self):
        sock = mock.MagicMock()
        with mock.patch.object(self.desktop.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value = sock
            self.assertTrue(self.desktop.windows_port_available())
        sock.bind.assert_called_once_with(("127.0.0.1", 47811))
        socket_factory.return_value.__exit__.assert_called_once()

    def test_windows_bind_occupied_port(self):
        sock = mock.MagicMock()
        sock.bind.side_effect = OSError(10048, "Address already in use")
        with mock.patch.object(self.desktop.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value = sock
            self.assertFalse(self.desktop.windows_port_available())

    def test_windows_would_block_is_available(self):
        sock = mock.MagicMock()
        sock.bind.side_effect = OSError(10035, "Operation would block")
        with mock.patch.object(self.desktop.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value = sock
            self.assertTrue(self.desktop.windows_port_available())

    def test_windows_timeout_is_available(self):
        sock = mock.MagicMock()
        sock.bind.side_effect = TimeoutError()
        with mock.patch.object(self.desktop.socket, "socket") as socket_factory:
            socket_factory.return_value.__enter__.return_value = sock
            self.assertTrue(self.desktop.windows_port_available())

    def test_windows_available_port_starts_backend(self):
        process = mock.Mock()
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.desktop.sys, "platform", "win32"), \
                mock.patch.object(self.desktop.sys, "argv", ["opscore_desktop.py", "--backend-only"]), \
                mock.patch.object(self.desktop, "DATA_DIR", Path(tmp)), \
                mock.patch.object(self.desktop, "URL_PATH", Path(tmp) / "current.json"), \
                mock.patch.object(self.desktop, "acquire_lock", return_value=mock.Mock()), \
                mock.patch.object(self.desktop, "active_app", return_value=None), \
                mock.patch.object(self.desktop, "windows_port_available", return_value=True), \
                mock.patch.object(self.desktop, "start_backend", return_value=(process, self.desktop.APP_URL)) as start, \
                mock.patch.object(self.desktop, "stop_backend"):
            self.assertEqual(self.desktop.main(), 0)
        start.assert_called_once_with()

    def test_windows_bind_is_used_without_connect_probe(self):
        process = mock.Mock()
        process.wait.return_value = 0
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.desktop.sys, "platform", "win32"), \
                mock.patch.object(self.desktop.sys, "argv", ["opscore_desktop.py", "--backend-only"]), \
                mock.patch.object(self.desktop, "DATA_DIR", Path(tmp)), \
                mock.patch.object(self.desktop, "URL_PATH", Path(tmp) / "current.json"), \
                mock.patch.object(self.desktop, "acquire_lock", return_value=mock.Mock()), \
                mock.patch.object(self.desktop, "active_app", return_value=None), \
                mock.patch.object(self.desktop, "windows_port_available", return_value=True) as available, \
                mock.patch.object(self.desktop.urllib.request, "urlopen") as urlopen, \
                mock.patch.object(self.desktop, "start_backend", return_value=(process, self.desktop.APP_URL)), \
                mock.patch.object(self.desktop, "stop_backend"):
            self.assertEqual(self.desktop.main(), 0)
        available.assert_called_once_with()
        urlopen.assert_not_called()

    def test_windows_occupied_port_does_not_start_backend(self):
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(self.desktop.sys, "platform", "win32"), \
                mock.patch.object(self.desktop.sys, "argv", ["opscore_desktop.py"]), \
                mock.patch.object(self.desktop, "DATA_DIR", Path(tmp)), \
                mock.patch.object(self.desktop, "URL_PATH", Path(tmp) / "current.json"), \
                mock.patch.object(self.desktop, "acquire_lock", return_value=mock.Mock()), \
                mock.patch.object(self.desktop, "active_app", return_value=None), \
                mock.patch.object(self.desktop, "windows_port_available", return_value=False), \
                mock.patch.object(self.desktop, "start_backend") as start, \
                mock.patch.object(self.desktop, "show_error") as show_error, \
                mock.patch.object(self.desktop, "stop_backend"):
            self.assertEqual(self.desktop.main(), 1)
        start.assert_not_called()
        self.assertIn("already used", show_error.call_args.args[0])


class TestLogRotation(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "opscore.log"
        self.patcher = mock.patch.object(self.desktop, "LOG_PATH", self.log)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.tmp.cleanup()

    def test_small_log_is_not_rotated(self):
        self.log.write_bytes(b"keep")
        self.desktop.rotate_log_at_startup()
        self.assertEqual(self.log.read_bytes(), b"keep")
        self.assertFalse(self.log.with_name("opscore.log.1").exists())

    def test_large_log_keeps_three_archives(self):
        for number in range(1, 4):
            self.log.with_name(f"opscore.log.{number}").write_text(f"old-{number}")
        self.log.write_bytes(b"x" * self.desktop.LOG_MAX_BYTES)
        self.desktop.rotate_log_at_startup()
        self.assertEqual(self.log.read_bytes(), b"")
        self.assertEqual(self.log.with_name("opscore.log.1").stat().st_size,
                         self.desktop.LOG_MAX_BYTES)
        self.assertEqual(self.log.with_name("opscore.log.2").read_text(), "old-1")
        self.assertEqual(self.log.with_name("opscore.log.3").read_text(), "old-2")

    def test_rotation_failure_does_not_block_startup(self):
        with mock.patch.object(self.desktop.Path, "stat", side_effect=OSError("locked")):
            self.desktop.rotate_log_at_startup()


class TestLiveLogRotation(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()
        self.tmp = tempfile.TemporaryDirectory()
        self.log = Path(self.tmp.name) / "opscore.log"
        self.desktop.LOG_SINK = None
        self.desktop.DATA_DIR = Path(self.tmp.name)
        self.patches = [
            mock.patch.object(self.desktop, "LOG_PATH", self.log),
            mock.patch.object(self.desktop, "LOG_MAX_BYTES", 64),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        self.desktop.stop_backend()
        if self.desktop.LOG_SINK is not None:
            self.desktop.LOG_SINK.close()
        self.desktop.LOG_SINK = None
        self.desktop.LOG_READER = None
        self.desktop.LOG_READER_PROCESS = None
        for patcher in reversed(self.patches):
            patcher.stop()
        self.tmp.cleanup()

    def test_writes_rotate_while_sink_stays_open_and_keep_three_archives(self):
        sink = self.desktop._log_sink()
        for number in range(6):
            sink.write(f"launcher-{number}\n".ljust(24, "x"))
            sink.write(f"backend-{number}\n".ljust(24, "x"))
        sink.close()
        self.assertLessEqual(self.log.stat().st_size, 64)
        archives = [self.log.with_name(f"opscore.log.{n}") for n in range(1, 4)]
        self.assertTrue(all(path.exists() for path in archives))
        self.assertFalse(self.log.with_name("opscore.log.4").exists())
        content = b"".join(path.read_bytes() for path in [self.log, *archives])
        self.assertIn(b"launcher-5", content)
        self.assertIn(b"backend-5", content)
        self.assertIn(b"backend-2", archives[2].read_bytes())
        self.assertNotIn(b"launcher-0", content)

    def test_small_writes_do_not_rotate(self):
        sink = self.desktop._log_sink()
        sink.write("launcher\n")
        sink.write("backend\n")
        sink.close()
        self.assertEqual(self.log.read_text(), "launcher\nbackend\n")
        self.assertFalse(self.log.with_name("opscore.log.1").exists())

    def test_real_backend_sustained_output_is_drained_and_rotated(self):
        process = self.desktop.platform_utils.start_backend(
            [sys.executable, "-u", "-c",
             "import sys\n"
             "for i in range(500):\n"
             " print(f'backend-{i}-' + 'x' * 100, flush=True)\n"
             "print('worker-child-diagnostic', file=sys.stderr, flush=True)"],
            cwd=Path(self.tmp.name), env={}, log=subprocess.PIPE,
        )
        self.desktop.BACKEND = process
        self.desktop._start_log_reader(process)
        self.assertEqual(process.wait(timeout=15), 0)
        self.desktop.LOG_READER.join(timeout=5)
        self.desktop.LOG_SINK.close()
        self.assertFalse(self.desktop.LOG_READER.is_alive())
        self.assertEqual(process.returncode, 0)
        self.assertTrue(self.log.with_name("opscore.log.1").exists())
        paths = [self.log, *(self.log.with_name(f"opscore.log.{n}") for n in range(1, 4))]
        content = b"".join(path.read_bytes() for path in paths)
        self.assertIn(b"backend-499", content)
        self.assertIn(b"worker-child-diagnostic", content)

    def test_stdout_and_stderr_share_live_sink(self):
        self.desktop.redirect_output_to_log()
        old_stdout, old_stderr = sys.stdout, sys.stderr
        try:
            print("launcher-line", flush=True)
            print("diagnostic", file=sys.stderr, flush=True)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr
        self.desktop.LOG_SINK.close()
        self.assertEqual(self.log.read_text(), "launcher-line\ndiagnostic\n")


class TestZenityDatabaseChoice(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()

    def ask(self, returncode):
        import builtins
        with mock.patch.object(builtins, "__import__", side_effect=ImportError("no GTK")), \
                mock.patch.object(self.desktop.shutil, "which", return_value="/usr/bin/zenity") as which, \
                mock.patch.object(self.desktop.subprocess, "run", return_value=mock.Mock(returncode=returncode)) as run:
            response = self.desktop.ask_use_existing_database()
        which.assert_called_once_with("zenity")
        self.assertEqual(run.call_args.args[0][0], "/usr/bin/zenity")
        return response

    def test_zenity_ok_keeps_database(self):
        self.assertTrue(self.ask(0))

    def test_zenity_cancel_starts_fresh(self):
        self.assertFalse(self.ask(1))

    def test_no_gtk_or_zenity_keeps_database(self):
        import builtins
        with mock.patch.object(builtins, "__import__", side_effect=ImportError("no GTK")), \
                mock.patch.object(self.desktop.shutil, "which", return_value=None), \
                mock.patch.object(self.desktop.subprocess, "run") as run:
            self.assertTrue(self.desktop.ask_use_existing_database())
        run.assert_not_called()


class TestSchemaPreflight(unittest.TestCase):
    def test_newer_schema_produces_actionable_desktop_error(self):
        desktop = load_desktop_module()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(desktop, "DATA_DIR", Path(tmp)), \
                mock.patch.object(desktop, "DB_PATH", Path(tmp) / "store_ops.db"), \
                mock.patch.object(desktop, "URL_PATH", Path(tmp) / "current.json"), \
                mock.patch.object(desktop, "acquire_lock", return_value=mock.Mock()), \
                mock.patch.object(desktop, "active_app", return_value=None), \
                mock.patch.object(desktop, "windows_port_available", return_value=True), \
                mock.patch.object(desktop, "rotate_log_at_startup"), \
                mock.patch.object(desktop, "redirect_output_to_log"), \
                mock.patch.object(desktop, "show_error") as show_error, \
                mock.patch.object(desktop, "start_backend") as start, \
                mock.patch.object(desktop, "stop_backend"):
            db = Path(tmp) / "store_ops.db"
            import sqlite3
            conn = sqlite3.connect(db)
            conn.execute("PRAGMA user_version = 999")
            conn.close()
            with mock.patch.object(desktop.sys, "platform", "win32"), \
                    mock.patch.object(desktop.sys, "argv", ["opscore_desktop.py"]):
                self.assertEqual(desktop.main(), 1)
        start.assert_not_called()
        self.assertIn("newer than this app", show_error.call_args.args[0])
        self.assertIn("Upgrade Opscore", show_error.call_args.args[0])


class TestWindowsWindow(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()
        self.url = "http://127.0.0.1:47811/"
        self.desktop.BACKEND = mock.Mock()

    def tearDown(self):
        self.desktop.BACKEND = None

    def test_webview2_window_has_opscore_title_icon_and_size(self):
        webview = mock.Mock()
        with mock.patch.dict(sys.modules, {"webview": webview}):
            self.desktop.windows_native_window(self.url)
        webview.create_window.assert_called_once_with(
            "Opscore", self.url, width=1280, height=800, min_size=(800, 600),
            background_color="#111827", text_select=True,
        )
        webview.start.assert_called_once_with(
            gui="edgechromium", private_mode=False,
            storage_path=str(self.desktop.DATA_DIR / "webview"),
            icon=str(self.desktop.APP_ROOT / "assets" / "opscore.ico"),
        )

    def test_webview_unavailable_falls_back_to_edge_app_mode(self):
        with mock.patch.dict(sys.modules, {"webview": None}), \
                mock.patch.object(self.desktop, "edge_window", return_value=True) as edge, \
                mock.patch.object(self.desktop.platform_utils, "open_in_browser") as browser:
            self.desktop.windows_window(self.url)
        edge.assert_called_once_with(self.url)
        browser.assert_not_called()

    def test_webview2_failure_falls_back_to_edge(self):
        with mock.patch.object(self.desktop, "windows_native_window", side_effect=RuntimeError("missing")), \
                mock.patch.object(self.desktop, "edge_window", return_value=True) as edge, \
                mock.patch.object(self.desktop.platform_utils, "open_in_browser") as browser:
            self.desktop.windows_window(self.url)
        edge.assert_called_once_with(self.url)
        browser.assert_not_called()

    def test_edge_unavailable_falls_back_to_default_browser(self):
        with mock.patch.object(self.desktop, "windows_native_window", side_effect=ImportError), \
                mock.patch.object(self.desktop, "edge_window", return_value=False), \
                mock.patch.object(self.desktop.platform_utils, "open_in_browser") as browser:
            self.desktop.windows_window(self.url)
        browser.assert_called_once_with(self.url)
        self.desktop.BACKEND.wait.assert_called_once_with()

    def test_edge_failure_also_falls_back_without_raising(self):
        with mock.patch.object(self.desktop, "windows_native_window", side_effect=RuntimeError), \
                mock.patch.object(self.desktop, "edge_window", side_effect=OSError("missing")), \
                mock.patch.object(self.desktop.platform_utils, "open_in_browser") as browser:
            self.desktop.windows_window(self.url)
        browser.assert_called_once_with(self.url)

    def test_default_browser_failure_does_not_raise(self):
        with mock.patch.object(self.desktop, "windows_native_window", side_effect=ImportError), \
                mock.patch.object(self.desktop, "edge_window", return_value=False), \
                mock.patch.object(self.desktop.platform_utils, "open_in_browser", side_effect=OSError):
            self.desktop.windows_window(self.url)
        self.desktop.BACKEND.wait.assert_called_once_with()

    def test_tray_import_failure_does_not_change_window_fallback(self):
        with mock.patch.object(self.desktop.sys, "platform", "win32"), \
                mock.patch.object(self.desktop.importlib.util, "spec_from_file_location", side_effect=ImportError), \
                mock.patch.object(self.desktop, "windows_native_window", side_effect=RuntimeError), \
                mock.patch.object(self.desktop, "edge_window", return_value=True) as edge, \
                mock.patch.object(self.desktop.threading, "Thread", wraps=self.desktop.threading.Thread) as thread_class, \
                mock.patch.object(self.desktop.platform_utils, "open_in_browser") as browser:
            self.desktop.windows_window(self.url)
        edge.assert_called_once_with(self.url)
        browser.assert_not_called()
        thread_class.assert_called_once()

    def test_non_windows_direct_call_does_not_start_tray_thread(self):
        with mock.patch.object(self.desktop.sys, "platform", "linux"), \
                mock.patch.object(self.desktop, "windows_native_window", side_effect=RuntimeError), \
                mock.patch.object(self.desktop, "edge_window", return_value=True), \
                mock.patch.object(self.desktop.threading, "Thread") as thread_class:
            self.desktop.windows_window(self.url)
        thread_class.assert_not_called()

    def test_edge_app_mode_uses_opscore_profile_and_waits(self):
        process = mock.Mock()
        with mock.patch.object(self.desktop.Path, "is_file", return_value=True), \
                mock.patch.dict(self.desktop.os.environ, {"ProgramFiles(x86)": r"C:\Program Files (x86)"}), \
                mock.patch.object(self.desktop.subprocess, "Popen", return_value=process) as popen:
            self.assertTrue(self.desktop.edge_window(self.url))
        command = popen.call_args.args[0]
        self.assertTrue(command[0].endswith("msedge.exe"))
        self.assertIn(f"--app={self.url}", command)
        self.assertIn(f"--user-data-dir={self.desktop.DATA_DIR / 'edge'}", command)
        process.wait.assert_called_once_with()

    def test_missing_edge_returns_false(self):
        with mock.patch.object(self.desktop.Path, "is_file", return_value=False):
            self.assertFalse(self.desktop.edge_window(self.url))

    def test_linux_window_path_is_unchanged(self):
        with mock.patch.object(self.desktop.sys, "platform", "linux"), \
                mock.patch.object(self.desktop, "native_window", return_value=0) as native, \
                mock.patch.object(self.desktop, "windows_window") as windows:
            self.assertEqual(self.desktop.open_window(self.url), 0)
        native.assert_called_once_with(self.url)
        windows.assert_not_called()


class TestLauncherCleanup(unittest.TestCase):
    def test_window_close_stops_backend_and_releases_lock(self):
        desktop = load_desktop_module()
        process = mock.Mock()
        lock = mock.Mock()
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(desktop.sys, "platform", "win32"), \
                mock.patch.object(desktop.sys, "argv", ["opscore_desktop.py"]), \
                mock.patch.object(desktop, "DATA_DIR", Path(tmp)), \
                mock.patch.object(desktop, "URL_PATH", Path(tmp) / "current.json"), \
                mock.patch.object(desktop, "acquire_lock", return_value=lock), \
                mock.patch.object(desktop, "active_app", return_value=None), \
                mock.patch.object(desktop, "windows_port_available", return_value=True), \
                mock.patch.object(desktop, "start_backend", return_value=(process, desktop.APP_URL)), \
                mock.patch.object(desktop, "open_window", return_value=0), \
                mock.patch.object(desktop, "stop_backend") as stop:
            self.assertEqual(desktop.main(), 0)
        stop.assert_called_once_with()
        lock.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
