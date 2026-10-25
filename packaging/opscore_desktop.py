"""Single-instance native window launcher for the packaged Opscore app."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.request

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path.home() / ".local" / "share" / "opscore"
DB_PATH = DATA_DIR / "store_ops.db"
LOCK_PATH = DATA_DIR / "launcher.lock"
URL_PATH = DATA_DIR / "current.json"
LOG_PATH = DATA_DIR / "opscore.log"
STARTUP_TIMEOUT = 90
BACKEND: subprocess.Popen | None = None


def stop_backend() -> None:
    """Stop the complete backend process group when its window closes."""
    global BACKEND
    process = BACKEND
    BACKEND = None
    if process is None or process.poll() is not None:
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


def acquire_lock():
    lock_file = LOCK_PATH.open("a+")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock_file.close()
        return None
    return lock_file


def read_url() -> str | None:
    try:
        state = json.loads(URL_PATH.read_text())
        url = state["url"]
        focus_url = state.get("focus_url", "")
        if url.startswith("http://127.0.0.1:"):
            with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                if response.status == 200:
                    return url, focus_url
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_until_ready(process: subprocess.Popen, url: str) -> None:
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError(
                f"Opscore backend exited with status {process.returncode}; see {LOG_PATH}"
            )
        try:
            with urllib.request.urlopen(url + "api/health", timeout=2) as response:
                if response.status == 200:
                    return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError(f"Opscore did not become ready; see {LOG_PATH}")


def start_backend() -> tuple[subprocess.Popen, str]:
    port = free_port()
    url = f"http://127.0.0.1:{port}/"
    release = (APP_ROOT / "RELEASE").read_text().strip()
    environment = {
        **os.environ,
        "DUCKLAB_DB_PATH": str(DB_PATH),
        "DUCKLAB_RELEASE_ID": release,
        "DUCKLAB_SECURE_COOKIES": "0",
        "REFLEX_CHECK_LATEST_VERSION": "false",
        "REFLEX_SKIP_COMPILE": "true",
        "REFLEX_ENV_MODE": "prod",
        "REFLEX_STATES_WORKDIR": str(DATA_DIR / "states"),
        "REFLEX_UPLOADED_FILES_DIR": str(DATA_DIR / "uploaded_files"),
        "REFLEX_TELEMETRY_DISABLED": "1",
        "NO_COLOR": "1",
    }
    with LOG_PATH.open("ab") as log:
        process = subprocess.Popen(
            [str(APP_ROOT / "packaging" / "opscore-backend"), "--port", str(port)],
            cwd=APP_ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    global BACKEND
    BACKEND = process
    wait_until_ready(process, url)
    URL_PATH.write_text(json.dumps({"pid": os.getpid(), "url": url}) + "\n")
    return process, url


def focus_chromium() -> bool:
    """Raise the Chromium app window on a second desktop-entry activation."""
    if not shutil.which("wmctrl"):
        return False
    return subprocess.run(
        ["wmctrl", "-a", "Opscore"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ).returncode == 0


def focus_native_window(url: str) -> bool:
    """Ask the running native-window process to raise its window."""
    if not url:
        return False
    import http.client

    port = int(url.rsplit(":", 1)[1].rstrip("/"))
    try:
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        connection.request("POST", "/_opscore/focus")
        response = connection.getresponse()
        response.read()
        return response.status == 204
    except OSError:
        return False
    finally:
        connection.close()


def chromium_window(url: str) -> int:
    browser = next(
        (
            shutil.which(name)
            for name in ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable")
            if shutil.which(name)
        ),
        None,
    )
    if browser is None:
        return None
    flags = [
        f"--app={url}",
        "--class=opscore",
        f"--user-data-dir={DATA_DIR / 'chromium'}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-session-crashed-bubble",
        "--disable-features=Translate",
    ]
    # Root containers have no sandbox helper; normal desktop launches keep Chromium's sandbox.
    if os.geteuid() == 0:
        flags.append("--no-sandbox")
    process = subprocess.Popen([browser, *flags])
    try:
        return process.wait()
    finally:
        stop_backend()


def native_window(url: str) -> int:
    import threading
    from wsgiref.simple_server import WSGIRequestHandler, make_server

    import webview

    class QuietHandler(WSGIRequestHandler):
        def log_message(self, *args):
            pass

    def focus_app(environ, start_response):
        window.restore()
        window.show()
        start_response("204 No Content", [])
        return [b""]

    with make_server("127.0.0.1", 0, focus_app, handler_class=QuietHandler) as server:
        focus_url = f"http://127.0.0.1:{server.server_port}/"
        URL_PATH.write_text(json.dumps({"pid": os.getpid(), "url": url, "focus_url": focus_url}) + "\n")
        threading.Thread(target=server.serve_forever, daemon=True).start()
        window = webview.create_window(
            "Opscore",
            url,
            width=1280,
            height=800,
            min_size=(800, 600),
            background_color="#111827",
            text_select=True,
        )
        webview.start(
            private_mode=False,
            storage_path=str(DATA_DIR / "webview"),
            icon=str(APP_ROOT / "assets" / "icon-512.png"),
        )
        server.shutdown()
    # GTK returns here after the native window closes.
    stop_backend()
    return 0


def open_window(url: str, force_chromium: bool = False) -> int:
    if force_chromium:
        return chromium_window(url) or 0
    try:
        # Explicit GTK selection avoids pywebview trying unavailable Qt bindings.
        os.environ["PYWEBVIEW_GUI"] = "gtk"
        return native_window(url)
    except Exception as error:
        print(f"GTK window unavailable ({error}); trying Chromium/Chrome.", file=sys.stderr)
        result = chromium_window(url)
        if result is not None:
            return result
    opener = shutil.which("xdg-open")
    if opener is None:
        raise RuntimeError(f"No GTK, Chromium/Chrome, or xdg-open available; see {LOG_PATH}")
    print(f"Opening Opscore with xdg-open: {url}", file=sys.stderr)
    process = subprocess.Popen([opener, url])
    try:
        return process.wait()
    finally:
        stop_backend()


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Opscore in a native desktop window")
    parser.add_argument("--backend-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--chromium", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()

    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    DATA_DIR.chmod(0o700)
    lock = acquire_lock()
    if lock is not None and args.port is not None:
        if not 0 < args.port < 65536:
            parser.error("--port must be between 1 and 65535")
        print(f"http://127.0.0.1:{args.port}/", flush=True)
        return 0
    if lock is None:
        active = read_url()
        if active and focus_native_window(active[1]):
            return 0
        if focus_chromium():
            return 0
        # Never open a second backend or window when the app is already active.
        return 0

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    try:
        active = read_url()
        url = active[0] if active else None
        if url is None:
            process, url = start_backend()
            if args.backend_only:
                print(url, flush=True)
                try:
                    return process.wait()
                finally:
                    stop_backend()
        return open_window(url, force_chromium=args.chromium)
    except Exception as error:
        print(f"Unable to start Opscore: {error}", file=sys.stderr)
        return 1
    finally:
        stop_backend()
        try:
            if json.loads(URL_PATH.read_text()).get("pid") == os.getpid():
                URL_PATH.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    sys.exit(main())
