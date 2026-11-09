"""Single-instance native window launcher for the packaged Opscore app."""

from __future__ import annotations

import argparse
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

os.environ["OPSCORE_DESKTOP"] = "1"

APP_ROOT = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent.parent))
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))


from ducklab import platform_utils

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


def installed_version() -> str:
    release_file = APP_ROOT / "RELEASE"
    return release_file.read_text().strip() if release_file.exists() else "source"


def prepare_webview_profile() -> None:
    """Discard a browser profile from a different app version."""
    version = installed_version()
    version_path = DATA_DIR / "app_version.txt"
    try:
        previous_version = version_path.read_text().strip()
    except OSError:
        previous_version = None
    profile = DATA_DIR / "webview"
    if previous_version != version and profile.exists():
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        profile.rename(DATA_DIR / f"webview.old-{timestamp}")
    if previous_version != version:
        version_path.write_text(version + "\n")


def start_backend() -> tuple[subprocess.Popen, str]:
    url = APP_URL
    release = installed_version()
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
        if sys.platform != "win32"
        else [
            str(Path(sys.executable).resolve()),
            str(APP_ROOT / "packaging" / "opscore_backend.py"),
        ]
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


def windows_native_window(url: str) -> None:
    """Open Opscore in WebView2 and return after its window closes."""
    import webview

    webview.create_window(
        "Opscore",
        url,
        width=1280,
        height=800,
        min_size=(800, 600),
        background_color="#111827",
        text_select=True,
    )
    webview.start(
        gui="edgechromium",
        private_mode=False,
        storage_path=str(DATA_DIR / "webview"),
        icon=str(APP_ROOT / "assets" / "opscore.ico"),
    )


def edge_window(url: str) -> bool:
    """Open an Edge app window and wait for it, returning False if unavailable."""
    edge = next(
        (
            path
            for path in (
                Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
                Path(os.environ.get("ProgramFiles", "")) / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            )
            if path.is_file()
        ),
        None,
    )
    if edge is None:
        return False
    process = subprocess.Popen([
        str(edge),
        f"--app={url}",
        f"--user-data-dir={DATA_DIR / 'edge'}",
        "--no-first-run",
        "--no-default-browser-check",
    ])
    process.wait()
    return True


def windows_window(url: str) -> None:
    """Use WebView2, then Edge app mode, then the default browser."""
    try:
        windows_native_window(url)
        return
    except Exception as error:
        print(f"WebView2 window unavailable ({error}); trying Microsoft Edge.", file=sys.stderr)
    try:
        if edge_window(url):
            return
    except Exception as error:
        print(f"Edge app window unavailable ({error}); trying the default browser.", file=sys.stderr)
    try:
        platform_utils.open_in_browser(url)
    except Exception as error:
        print(f"Could not open a browser automatically ({error}); open {url}", file=sys.stderr)
    else:
        print(f"Opening Opscore in the default browser: {url}", file=sys.stderr)
    print("Leave this window open; close it or press Ctrl+C to stop Opscore.", file=sys.stderr)
    BACKEND.wait()


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
        windows_window(url)
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


def redirect_output_to_log() -> None:
    """Send stdout and stderr to opscore.log (append mode, line-buffered)."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    log_fd = os.open(LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    os.dup2(log_fd, 1)
    os.dup2(log_fd, 2)
    if log_fd > 2:
        os.close(log_fd)
    sys.stdout = io.open(
        1, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False
    )
    sys.stderr = io.open(
        2, "w", encoding="utf-8", errors="replace", buffering=1, closefd=False
    )


def run_worker_child() -> None:
    """Run a frozen multiprocessing worker child with diagnostics in the log."""
    import multiprocessing
    import traceback

    try:
        redirect_output_to_log()
        print(f"Opscore worker child started (pid {os.getpid()})", flush=True)
        # Hands the child to multiprocessing; never returns for a worker.
        multiprocessing.freeze_support()
    except SystemExit:
        raise
    except BaseException:
        print(
            f"Opscore worker child crashed in pid {os.getpid()}:",
            file=sys.stderr,
            flush=True,
        )
        traceback.print_exc()
        raise SystemExit(1)


def ensure_frontend_link(runtime_web: Path) -> None:
    """Point the runtime web dir's build at the read-only prebuilt frontend.

    Reflex serves <web dir>/build statically; the bundle's copy may live in
    read-only Program Files, so link it instead of duplicating it per user.
    A stale link (for example after the install moves) is recreated.
    """
    link = runtime_web / "build"
    target = APP_ROOT / ".web" / "build"
    if link.is_symlink() or link.is_junction():
        if link.resolve() == target.resolve():
            return
        # Remove only the link itself, never the bundle it points at.
        if sys.platform == "win32":
            os.rmdir(link)
        else:
            link.unlink()
    elif link.exists():
        # A plain directory could only be a stray copy; the frontend is
        # derived data, safe to replace with a link to the bundle.
        shutil.rmtree(link)
    if sys.platform == "win32":
        # Directory junctions need no admin rights, unlike os.symlink.
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
        if result.returncode != 0:
            raise RuntimeError(
                "Unable to link the prebuilt frontend into "
                f"{runtime_web}: {result.stderr.decode(errors='replace').strip()}"
            )
    else:
        link.symlink_to(target, target_is_directory=True)


def prepare_frozen_backend_env() -> None:
    """Keep the frozen backend out of the read-only install folder.

    Reflex workers on Windows are spawned: they re-import the app from
    scratch, so the runtime monkey patches packaging/opscore_backend.py
    installs (which Linux's forked workers inherit) never reach them. Only
    the process environment carries over, so the frozen backend uses Reflex's
    supported environment switches instead:

    - REFLEX_SKIP_COMPILE, set through Reflex's own env-var API (the same one
      run_uvicorn_backend_prod passes to prod backends), so no worker
      compiles the frontend.
    - REFLEX_WEB_WORKDIR, Reflex's supported .web relocation, so its runtime
      writes (stateful pages marker, upload marker) land in per-user data.
      Skipping the compile also stops cwd-relative build artifacts such as
      the reflex.lock directory from appearing in the install folder.
    """
    from reflex_base.environment import environment

    environment.REFLEX_SKIP_COMPILE.set(True)
    runtime_web = DATA_DIR / "reflex-web"
    runtime_web.mkdir(parents=True, exist_ok=True)
    os.environ["REFLEX_WEB_WORKDIR"] = str(runtime_web)
    ensure_frontend_link(runtime_web)


def run_frozen_backend() -> int:
    """Run the backend from the windowed executable with persistent diagnostics."""
    import traceback

    redirect_output_to_log()
    try:
        print("Starting Opscore backend", flush=True)
        prepare_frozen_backend_env()
        backend_path = APP_ROOT / "packaging" / "opscore_backend.py"
        spec = importlib.util.spec_from_file_location("opscore_backend", backend_path)
        backend = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(backend)
        original_argv = sys.argv
        sys.argv = [original_argv[0]]
        try:
            backend.main()
        finally:
            sys.argv = original_argv
        return 0
    except Exception:
        print(f"Opscore backend crashed in pid {os.getpid()}:", flush=True)
        traceback.print_exc()
        return 1


def windows_port_available() -> bool:
    """Check the fixed port without Windows' delayed localhost connect."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 47811))
        return True
    except OSError as error:
        return error.errno == 10035 or isinstance(error, TimeoutError)


def port_in_use_error() -> RuntimeError:
    return RuntimeError(
        "Port 47811 is already used by another application. "
        "Close that application, then open Opscore again."
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Open Opscore in a native desktop window")
    parser.add_argument("--backend", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--backend-only", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--chromium", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.backend:
        return run_frozen_backend()

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
        if sys.platform == "win32":
            if not windows_port_available():
                raise port_in_use_error()
        else:
            try:
                with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
                    response.read(1)
            except urllib.error.HTTPError as error:
                raise port_in_use_error() from error
            except urllib.error.URLError as error:
                if getattr(error.reason, "errno", None) not in (111, 104, 101):
                    raise port_in_use_error() from error
            except OSError as error:
                raise port_in_use_error() from error
            else:
                raise port_in_use_error()
        process, url = start_backend()
        prepare_webview_profile()
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
        lock.close()
        try:
            if json.loads(URL_PATH.read_text()).get("pid") == os.getpid():
                URL_PATH.unlink(missing_ok=True)
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    import multiprocessing

    # A frozen exe re-executed as a multiprocessing worker child (Granian
    # workers on Windows) must hand control to multiprocessing before any
    # launcher logic (arg parsing, imports, single-instance lock) can run.
    if "--multiprocessing-fork" in sys.argv:
        run_worker_child()
    # freeze_support() runs the child's real entry point and only returns in
    # genuine user invocations; without it a frozen worker re-runs main().
    multiprocessing.freeze_support()
    sys.exit(main())
