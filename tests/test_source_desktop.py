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


class TestWindowsBrowser(unittest.TestCase):
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
