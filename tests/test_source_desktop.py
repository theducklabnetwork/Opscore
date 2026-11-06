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

    def test_browser_launcher_stays_with_backend(self):
        desktop = load_desktop_module()
        backend = mock.Mock()
        backend.wait.return_value = 0
        desktop.BACKEND = backend
        with mock.patch.object(desktop.sys, "platform", "win32"), \
                mock.patch.object(desktop.platform_utils, "open_in_browser") as open_browser:
            self.assertEqual(desktop.open_window("http://127.0.0.1:47811/"), 0)
        open_browser.assert_called_once_with("http://127.0.0.1:47811/")
        backend.wait.assert_called_once_with()
        desktop.BACKEND = None


if __name__ == "__main__":
    unittest.main()
