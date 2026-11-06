"""Small platform adapters used by the Opscore desktop launcher."""

from __future__ import annotations

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


def stop_process_tree(process: subprocess.Popen | None) -> None:
    """Gracefully stop a backend and its children, then force termination."""
    if process is None or process.poll() is not None:
        return
    if sys.platform == "win32":
        try:
            process.send_signal(getattr(signal, "CTRL_BREAK_EVENT", 1))
        except ProcessLookupError:
            return
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
