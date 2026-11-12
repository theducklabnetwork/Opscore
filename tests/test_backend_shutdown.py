"""Regression tests for the packaged backend's Windows shutdown bridge."""

import importlib.util
import os
from pathlib import Path
import sys
import types
import unittest
from unittest import mock

from ducklab import platform_utils


def module(name, **attributes):
    result = types.ModuleType(name)
    for key, value in attributes.items():
        setattr(result, key, value)
    return result


class TestWindowsBackendShutdown(unittest.TestCase):
    def test_granian_uses_its_normal_interrupt_handler(self):
        instances = []

        class Granian:
            def __init__(self, *args, **kwargs):
                instances.append(self)

            def serve(self, *args, **kwargs):
                return "served"

        constants = module("reflex_base.constants", NOCOMPILE_FILE=".nocompile")
        constants.__path__ = []
        base = module("reflex_base.constants.base", LogLevel=mock.Mock(INFO="info"))
        constants.base = base
        reflex_base = module("reflex_base", constants=constants)
        prerequisites = module(
            "reflex.utils.prerequisites", get_web_dir=lambda: Path("web")
        )
        granian_server = module("granian.server", Server=Granian)
        granian = module("granian", server=granian_server)
        reflex_exec = module(
            "reflex.utils.exec", get_frontend_mount=mock.Mock(), run_backend_prod=mock.Mock()
        )
        reflex_utils = module("reflex.utils", prerequisites=prerequisites, exec=reflex_exec)
        reflex = module("reflex", utils=reflex_utils)
        stubs = {
            "reflex_base": reflex_base,
            "reflex_base.constants": constants,
            "reflex_base.constants.base": base,
            "reflex": reflex,
            "reflex.utils": reflex_utils,
            "reflex.utils.prerequisites": prerequisites,
            "reflex.utils.exec": reflex_exec,
            "granian": granian,
            "granian.server": granian_server,
        }

        backend_path = Path(__file__).resolve().parent.parent / "packaging" / "opscore_backend.py"
        spec = importlib.util.spec_from_file_location("opscore_backend_test", backend_path)
        backend = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, stubs), \
                mock.patch.dict(os.environ, {"DUCKLAB_DB_PATH": ""}), \
                mock.patch.object(platform_utils, "start_windows_shutdown") as start_shutdown:
            spec.loader.exec_module(backend)
            self.assertIn(str(backend.APP_ROOT), sys.path)
            with mock.patch.object(backend.os, "name", "nt"), \
                    mock.patch.object(sys, "argv", ["opscore_backend.py"]):
                backend.main()
            start_shutdown.assert_called_once_with(mock.ANY)
            start_shutdown.call_args.args[0]()
            self.assertEqual(len(instances), 0)
        self.assertIs(granian_server.Server, Granian)
        backend.run_backend_prod.assert_called_once_with(
            "127.0.0.1", 47811, base.LogLevel.INFO, mount_frontend_compiled_app=True
        )


if __name__ == "__main__":
    unittest.main()
