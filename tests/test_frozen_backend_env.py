"""Tests for the frozen backend's environment setup.

The frozen backend's spawned workers re-import the app, so compile skipping
and .web relocation must travel through the process environment, unlike the
Linux .deb where forked workers inherit packaging/opscore_backend.py's monkey
patches.
"""

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_desktop_module():
    """Import packaging/opscore_desktop.py the way the frozen exe does."""
    spec = importlib.util.spec_from_file_location(
        "opscore_desktop", PROJECT_ROOT / "packaging" / "opscore_desktop.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FrozenDesktopFixture(unittest.TestCase):
    def setUp(self):
        self.desktop = load_desktop_module()
        self._tmp = tempfile.TemporaryDirectory()
        tmp = Path(self._tmp.name)
        self.app_root = tmp / "bundle"
        (self.app_root / ".web" / "build" / "client").mkdir(parents=True)
        self.data_dir = tmp / "data"
        self.data_dir.mkdir()
        self.patches = [
            mock.patch.object(self.desktop, "APP_ROOT", self.app_root),
            mock.patch.object(self.desktop, "DATA_DIR", self.data_dir),
            mock.patch.dict(os.environ),
        ]
        for patcher in self.patches:
            patcher.start()

    def tearDown(self):
        for patcher in reversed(self.patches):
            patcher.stop()
        self._tmp.cleanup()


class TestEnsureFrontendLink(FrozenDesktopFixture):
    def runtime_web(self):
        runtime_web = self.data_dir / "reflex-web"
        runtime_web.mkdir()
        return runtime_web

    def test_creates_link_to_bundled_build_once(self):
        runtime_web = self.runtime_web()
        self.desktop.ensure_frontend_link(runtime_web)
        link = runtime_web / "build"
        self.assertTrue(link.is_symlink() or link.is_junction())
        self.assertEqual(link.resolve(), (self.app_root / ".web" / "build").resolve())
        # Replacing the link target must not happen while it is correct.
        self.desktop.ensure_frontend_link(runtime_web)
        self.assertTrue(link.is_symlink() or link.is_junction())

    def test_recreates_stale_link_without_touching_targets(self):
        runtime_web = self.runtime_web()
        other = self.app_root / "other"
        other.mkdir()
        (other / "keep.txt").write_text("keep")
        link = runtime_web / "build"
        link.symlink_to(other, target_is_directory=True)
        self.desktop.ensure_frontend_link(runtime_web)
        self.assertEqual(link.resolve(), (self.app_root / ".web" / "build").resolve())
        self.assertTrue((other / "keep.txt").exists())

    def test_replaces_stray_plain_directory(self):
        runtime_web = self.runtime_web()
        (runtime_web / "build").mkdir()
        self.desktop.ensure_frontend_link(runtime_web)
        link = runtime_web / "build"
        self.assertTrue(link.is_symlink() or link.is_junction())

    def test_windows_uses_mklink_junction_without_console(self):
        runtime_web = self.runtime_web()
        result = mock.Mock(returncode=0)
        with mock.patch.object(self.desktop.sys, "platform", "win32"), \
                mock.patch.object(self.desktop.subprocess, "run", return_value=result) as run:
            self.desktop.ensure_frontend_link(runtime_web)
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["cmd", "/c", "mklink", "/J"])
        self.assertEqual(Path(command[4]), runtime_web / "build")
        self.assertEqual(Path(command[5]), self.app_root / ".web" / "build")
        self.assertEqual(run.call_args.kwargs["creationflags"], 0x08000000)

    def test_windows_mklink_failure_raises(self):
        result = mock.Mock(returncode=1, stderr=b"denied")
        with mock.patch.object(self.desktop.sys, "platform", "win32"), \
                mock.patch.object(self.desktop.subprocess, "run", return_value=result):
            with self.assertRaises(RuntimeError):
                self.desktop.ensure_frontend_link(self.runtime_web())


class TestPrepareFrozenBackendEnv(FrozenDesktopFixture):
    def test_skip_compile_and_web_relocation_travel_through_environ(self):
        self.desktop.prepare_frozen_backend_env()
        self.assertTrue(
            os.environ.get("__REFLEX_SKIP_COMPILE", "").lower()
            in {"true", "1", "yes", "y"}
        )
        self.assertEqual(
            os.environ.get("REFLEX_WEB_WORKDIR"), str(self.data_dir / "reflex-web")
        )
        self.assertTrue((self.data_dir / "reflex-web" / "build").is_symlink())

    def test_skip_compile_reaches_reflexs_own_gate(self):
        from reflex_base.environment import environment

        # Do not instantiate rx.App here: App binds the process-global
        # RegistrationContext, which would break later imports of
        # ducklab.ducklab in the same pytest process. App._should_compile's
        # first check is exactly this env var, so asserting it covers the gate.
        self.assertFalse(environment.REFLEX_SKIP_COMPILE.get())
        self.desktop.prepare_frozen_backend_env()
        self.assertTrue(environment.REFLEX_SKIP_COMPILE.get())


if __name__ == "__main__":
    unittest.main()
