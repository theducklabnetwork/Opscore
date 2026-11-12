"""Small platform adapters used by the Opscore desktop launcher."""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys


def data_dir() -> Path:
    """Return the per-user Opscore data directory."""
    if sys.platform == "win32":
        return Path(os.environ["LOCALAPPDATA"]) / "Opscore"
    return Path.home() / ".local" / "share" / "opscore"


def documents_dir() -> Path:
    """Return the per-user directory used for exported documents."""
    if sys.platform == "win32":
        return Path(os.environ["USERPROFILE"]) / "Documents"
    return Path.home() / "Documents"


RECOVERY_KIT_NAME = "opscore-recovery-key.txt"


def write_recovery_kit(key: str, account_email: str,
                       directory: Path | None = None) -> Path:
    """Write the recovery kit file once into the per-user data directory.

    The file contains the raw recovery key exactly once at issue; the
    database stores only its SHA-256 hash. On POSIX the directory is
    created 0700 and the file 0600 (owner only). On Windows the file
    inherits the per-user profile ACL.

    Raises OSError on failure; the caller must surface the error without
    logging the key.
    """
    folder = Path(directory) if directory is not None else data_dir()
    folder.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(folder, 0o700)
    path = folder / RECOVERY_KIT_NAME
    body = (
        "Opscore account recovery kit\n"
        "============================\n\n"
        f"Account: {account_email}\n"
        f"Recovery key: {key}\n\n"
        "Use this key on the sign-in page under 'Forgot password?' to set a\n"
        "new password. The key is single-use: after a successful recovery a\n"
        "replacement key is issued. Keep this file private; do not upload,\n"
        "email, or sync it to cloud storage.\n"
    )
    if os.name == "posix":
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(body)
        os.chmod(path, 0o600)
    else:
        path.write_text(body, encoding="utf-8")
    return path


def acquire_single_instance(lock_path: Path):
    """Acquire the non-blocking per-user instance lock, or return None."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        handle = kernel32.CreateMutexW(None, False, r"Local\Opscore")
        if not handle:
            return None
        if kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return None

        class _Mutex:
            def close(self):
                kernel32.CloseHandle(handle)

        return _Mutex()

    import fcntl

    lock_file = lock_path.open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def start_backend(command: list[str], *, cwd: Path, env: dict[str, str], log) -> subprocess.Popen:
    """Start the backend in its own process group with output redirected."""
    options = {}
    if sys.platform == "win32":
        options["creationflags"] = 0x00000200 | 0x08000000  # new group, no console
    else:
        options["start_new_session"] = True
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        **options,
    )


def start_windows_shutdown(run_backend) -> None:
    """Run a Windows backend with a local request for its normal shutdown."""
    granian_server = importlib.import_module("granian.server")

    original_server = granian_server.Server

    class DesktopGranian(original_server):
        def serve(self, *args, **kwargs):
            start_shutdown_pipe(self)
            return super().serve(*args, **kwargs)

    granian_server.Server = DesktopGranian
    try:
        run_backend()
    finally:
        granian_server.Server = original_server


def start_shutdown_pipe(server) -> None:
    """Give the Windows launcher a way to request Granian's normal shutdown."""
    from multiprocessing.connection import Listener
    import threading

    listener = Listener(
        rf"\\.\pipe\opscore-backend-{os.getpid()}", family="AF_PIPE", backlog=1
    )

    def listen() -> None:
        try:
            with listener:
                with listener.accept() as connection:
                    if connection.recv_bytes() == b"stop":
                        server.signal_handler_interrupt()
                        connection.send_bytes(b"stopping")
        except OSError:
            # Process teardown also closes the listener and accepted pipe.
            pass

    threading.Thread(target=listen, name="opscore-shutdown", daemon=True).start()


def request_windows_shutdown(pid: int) -> None:
    """Ask the backend identified by pid to stop through its private pipe."""
    from multiprocessing.connection import Client

    with Client(rf"\\.\pipe\opscore-backend-{pid}", family="AF_PIPE") as connection:
        connection.send_bytes(b"stop")
        connection.recv_bytes()


def stop_process_tree(process: subprocess.Popen | None) -> None:
    """Gracefully stop a backend and its children, then force termination."""
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            request_windows_shutdown(process.pid)
        except OSError:
            # The backend may have exited between poll() and the pipe request.
            if process.poll() is not None:
                return
            raise
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            process.wait(timeout=5)
        return

    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=20)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def open_in_browser(url: str):
    """Open a URL using the platform's normal browser opener."""
    if sys.platform == "win32":
        os.startfile(url)
        return None
    opener = shutil.which("xdg-open")
    if opener is None:
        return None
    return subprocess.Popen([opener, url])
