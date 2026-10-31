"""Single-instance native window launcher for the packaged Opscore app."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

os.environ["OPSCORE_DESKTOP"] = "1"

from ducklab import platform_utils

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
DATA_DIR = platform_utils.data_dir()
DB_PATH = DATA_DIR / "store_ops.db"
LOCK_PATH = DATA_DIR / "launcher.lock"
URL_PATH = DATA_DIR / "current.json"
LOG_PATH = DATA_DIR / "opscore.log"
STARTUP_TIMEOUT = 90
APP_URL = "http://127.0.0.1:47811/"
HEALTH_URL = APP_URL + "api/health"
BACKEND: subprocess.Popen | None = None


def stop_backend() -> None:
    """Stop the complete backend process group when its window closes."""
    global BACKEND
    process = BACKEND
    BACKEND = None
    if process is None or process.poll() is not None:
        return
    platform_utils.stop_process_tree(process)


def acquire_lock():
    return platform_utils.acquire_single_instance(LOCK_PATH)


def active_app() -> tuple[str, str] | None:
    """Return the fixed app URL and optional focus endpoint for our instance."""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
            health = json.load(response)
        if response.status != 200 or health.get("status") != "ok" or not health.get("release"):
            return None
        try:
            state = json.loads(URL_PATH.read_text())
            focus_url = state.get("focus_url", "")
        except (OSError, ValueError, AttributeError):
            focus_url = ""
        return APP_URL, focus_url
    except (OSError, ValueError, AttributeError):
        return None


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


def show_error(message: str) -> None:
    """Show a desktop-visible fatal error even when no app window exists."""
    print(f"Unable to start Opscore: {message}", file=sys.stderr)
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        dialog = Gtk.MessageDialog(
            transient_for=None,
            flags=0,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            title="Opscore cannot start",
            text=message,
        )
        dialog.run()
        dialog.destroy()
    except Exception:
        if opener := shutil.which("zenity"):
            subprocess.run([opener, "--error", "--title=Opscore cannot start", f"--text={message}"])


def start_backend() -> tuple[subprocess.Popen, str]:
    url = APP_URL
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
    backend_command = (
        [str(Path(sys.executable).resolve()), "--backend"]
        if getattr(sys, "frozen", False)
        else [str(APP_ROOT / "packaging" / "opscore-backend")]
    )
    with LOG_PATH.open("ab") as log:
        process = platform_utils.start_backend(
            backend_command,
            cwd=APP_ROOT,
            env=environment,
            log=log,
        )
    global BACKEND
    BACKEND = process
    wait_until_ready(process, url)
    URL_PATH.write_text(json.dumps({"pid": os.getpid(), "url": url}) + "\n")
    return process, url


def focus_chromium() -> bool:
    """Raise the Chromium app window on a second desktop-entry activation."""
    if sys.platform == "win32" or not shutil.which("wmctrl"):
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
    if sys.platform != "win32" and os.geteuid() == 0:
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
    if sys.platform == "win32" and not force_chromium:
        platform_utils.open_in_browser(url)
        return 0
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
    process = platform_utils.open_in_browser(url)
    if process is None:
        raise RuntimeError(f"No GTK, Chromium/Chrome, or xdg-open available; see {LOG_PATH}")
    print(f"Opening Opscore with the system browser: {url}", file=sys.stderr)
    try:
        return process.wait()
    finally:
        stop_backend()


def focus_existing() -> None:
    active = active_app()
    if active:
        focus_native_window(active[1])
    focus_chromium()


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Opscore in a native desktop window")
    parser.add_argument("--backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backend-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--chromium", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.backend:
        import importlib.util

        backend_path = APP_ROOT / "packaging" / "opscore_backend.py"
        spec = importlib.util.spec_from_file_location("opscore_backend", backend_path)
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        backend.main()
        return 0

    DATA_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    if sys.platform != "win32":
        DATA_DIR.chmod(0o700)
    lock = acquire_lock()
    if lock is None:
        focus_existing()
        return 0

    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    signal.signal(signal.SIGINT, lambda *_: sys.exit(0))
    try:
        active = active_app()
        if active:
            focus_existing()
            return 0
        # A live service that does not answer our health API owns the fixed
        # port; never replace it or silently choose a different API endpoint.
        try:
            with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                response.read(1)
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                "Port 47811 is already used by another application. "
                "Close that application, then open Opscore again."
            ) from error
        except urllib.error.URLError as error:
            if getattr(error.reason, "errno", None) not in (111, 104, 101):
                raise RuntimeError(
                    "Port 47811 is already used by another application. "
                    "Close that application, then open Opscore again."
                ) from error
        except OSError as error:
            raise RuntimeError(
                "Port 47811 is already used by another application. "
                "Close that application, then open Opscore again."
            ) from error
        else:
            raise RuntimeError(
                "Port 47811 is already used by another application. "
                "Close that application, then open Opscore again."
            )
        process, url = start_backend()
        if args.backend_only:
            print(url, flush=True)
            try:
                return process.wait()
            finally:
                stop_backend()
        return open_window(url, force_chromium=args.chromium)
    except Exception as error:
        show_error(str(error))
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
