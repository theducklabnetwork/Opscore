"""Mocked tests for the desktop launcher's Linux and Windows adapters."""

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from ducklab import platform_utils


class TestDataDir(unittest.TestCase):
    def test_linux_path(self):
        with mock.patch.object(platform_utils.sys, "platform", "linux"), \
                mock.patch.object(platform_utils.Path, "home", return_value=Path("/home/test")):
            self.assertEqual(platform_utils.data_dir(), Path("/home/test/.local/share/opscore"))

    def test_windows_path(self):
        with mock.patch.object(platform_utils.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"LOCALAPPDATA": r"C:\Users\Test\AppData\Local"}):
            self.assertEqual(platform_utils.data_dir(), Path(r"C:\Users\Test\AppData\Local") / "Opscore")


class TestDocumentsDir(unittest.TestCase):
    def test_linux_path(self):
        with mock.patch.object(platform_utils.sys, "platform", "linux"), \
                mock.patch.object(platform_utils.Path, "home", return_value=Path("/home/test")):
            self.assertEqual(platform_utils.documents_dir(), Path("/home/test/Documents"))

    def test_windows_path(self):
        with mock.patch.object(platform_utils.sys, "platform", "win32"), \
                mock.patch.dict(os.environ, {"USERPROFILE": r"C:\Users\Test"}):
            self.assertEqual(platform_utils.documents_dir(), Path(r"C:\Users\Test") / "Documents")


class TestSingleInstance(unittest.TestCase):
    def test_linux_flock(self):
        with tempfile.TemporaryDirectory() as tmp:
            lock = platform_utils.acquire_single_instance(Path(tmp) / "launcher.lock")
            self.assertIsNotNone(lock)
            lock.close()

    def test_windows_mutex_available_and_existing(self):
        kernel32 = mock.Mock()
        kernel32.CreateMutexW.return_value = 42
        kernel32.GetLastError.return_value = 0
        with mock.patch.object(platform_utils.sys, "platform", "win32"), \
                mock.patch("ctypes.windll", create=True) as windll:
            windll.kernel32 = kernel32
            mutex = platform_utils.acquire_single_instance(Path("unused"))
            self.assertIsNotNone(mutex)
            mutex.close()
            kernel32.CloseHandle.assert_called_once_with(42)
            kernel32.CreateMutexW.reset_mock()
            kernel32.GetLastError.return_value = 183
            self.assertIsNone(platform_utils.acquire_single_instance(Path("unused")))


class TestBackendProcesses(unittest.TestCase):
    def test_linux_start_and_stop(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with mock.patch.object(platform_utils.sys, "platform", "linux"), \
                mock.patch.object(platform_utils.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(platform_utils.os, "killpg", create=True) as killpg:
            log = mock.Mock()
            result = platform_utils.start_backend(["backend"], cwd=Path("/app"), env={}, log=log)
            self.assertIs(result, process)
            self.assertTrue(popen.call_args.kwargs["start_new_session"])
            platform_utils.stop_process_tree(process)
            killpg.assert_called_once_with(123, signal.SIGTERM)

    def test_windows_start_and_stop(self):
        process = mock.Mock(pid=123)
        process.poll.return_value = None
        with mock.patch.object(platform_utils.sys, "platform", "win32"), \
                mock.patch.object(platform_utils.subprocess, "Popen", return_value=process) as popen, \
                mock.patch.object(platform_utils, "request_windows_shutdown") as request, \
                mock.patch.object(platform_utils.subprocess, "run") as run:
            result = platform_utils.start_backend(["backend"], cwd=Path("app"), env={}, log=mock.Mock())
            self.assertIs(result, process)
            self.assertEqual(
                popen.call_args.kwargs["creationflags"],
                0x00000200 | 0x08000000,
            )
            process.wait.side_effect = [subprocess.TimeoutExpired("backend", 20), None]
            platform_utils.stop_process_tree(process)
            request.assert_called_once_with(123)
            process.send_signal.assert_not_called()
            self.assertEqual(run.call_args.args[0], ["taskkill", "/PID", "123", "/T", "/F"])

    def test_windows_backend_wrapper_connects_shutdown_pipe(self):
        granian_server = mock.Mock()
        instances = []

        class Granian:
            def __init__(self):
                instances.append(self)

            def serve(self, *args, **kwargs):
                return "served"

        granian_server.Server = Granian
        with mock.patch.dict(sys.modules, {"granian.server": granian_server}), \
                mock.patch.object(platform_utils, "start_shutdown_pipe") as start_pipe:
            platform_utils.start_windows_shutdown(
                lambda: granian_server.Server().serve()
            )
        self.assertEqual(len(instances), 1)
        self.assertIsInstance(instances[0], Granian)
        start_pipe.assert_called_once_with(instances[0])
        self.assertIs(granian_server.Server, Granian)

    def test_windows_pipe_request_targets_backend_pid(self):
        connection = mock.MagicMock()
        client = mock.MagicMock(return_value=connection)
        connection_module = mock.Mock(Client=client)
        with mock.patch.dict(sys.modules, {"multiprocessing.connection": connection_module}):
            platform_utils.request_windows_shutdown(123)
        client.assert_called_once_with(r"\\.\pipe\opscore-backend-123", family="AF_PIPE")
        connection.__enter__.return_value.send_bytes.assert_called_once_with(b"stop")
        connection.__enter__.return_value.recv_bytes.assert_called_once_with()

    def test_windows_pipe_request_invokes_granian_interrupt_handler(self):
        listener = mock.MagicMock()
        connection = listener.accept.return_value.__enter__.return_value
        connection.recv_bytes.return_value = b"stop"
        connection_module = mock.Mock()
        connection_module.Listener.return_value = listener
        server = mock.Mock()
        thread = mock.Mock()
        with mock.patch.dict(sys.modules, {"multiprocessing.connection": connection_module}), \
                mock.patch.object(platform_utils.os, "getpid", return_value=123), \
                mock.patch("threading.Thread", return_value=thread) as thread_type:
            platform_utils.start_shutdown_pipe(server)
            connection_module.Listener.assert_called_once_with(
                r"\\.\pipe\opscore-backend-123", family="AF_PIPE", backlog=1
            )
            thread_type.assert_called_once_with(
                target=mock.ANY, name="opscore-shutdown", daemon=True
            )
            thread.start.assert_called_once_with()
            thread_type.call_args.kwargs["target"]()
        server.signal_handler_interrupt.assert_called_once_with()
        connection.send_bytes.assert_called_once_with(b"stopping")


class TestBrowser(unittest.TestCase):
    def test_linux_xdg_open(self):
        with mock.patch.object(platform_utils.sys, "platform", "linux"), \
                mock.patch.object(platform_utils.shutil, "which", return_value="/usr/bin/xdg-open"), \
                mock.patch.object(platform_utils.subprocess, "Popen") as popen:
            platform_utils.open_in_browser("http://127.0.0.1:47811/")
            self.assertEqual(popen.call_args.args[0], ["/usr/bin/xdg-open", "http://127.0.0.1:47811/"])

    def test_windows_startfile(self):
        with mock.patch.object(platform_utils.sys, "platform", "win32"), \
                mock.patch.object(platform_utils.os, "startfile", create=True) as startfile:
            self.assertIsNone(platform_utils.open_in_browser("http://127.0.0.1:47811/"))
            startfile.assert_called_once_with("http://127.0.0.1:47811/")


if __name__ == "__main__":
    unittest.main()
