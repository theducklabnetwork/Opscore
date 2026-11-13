"""Windows-only status tray icon for the Opscore desktop launcher.

The tray icon is optional and status-only: it mirrors the fixed backend health
endpoint and offers Open/Quit actions, but closing the application window still
stops Opscore exactly as before. All optional dependencies are imported lazily
so source runs on Linux, minimal Windows environments, and the frozen backend
workers never fail merely because pystray or Pillow is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path
import threading
import urllib.request

HEALTH_URL = "http://127.0.0.1:47811/api/health"
ICON_TITLE_RUNNING = "Opscore — running"
ICON_TITLE_NOT_RESPONDING = "Opscore — not responding"
POLL_INTERVAL_SECONDS = 2.0
_TRAY_SIZE = 64
_MASK_SCALE = 4


def backend_is_healthy(url: str = HEALTH_URL, timeout: float = 2.0) -> bool:
    """Return True only when the running backend answers the Opscore health API."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except OSError:
        return False


def _circle_mask(size: int):
    """Render an anti-aliased circular alpha mask for a square tray image."""
    from PIL import Image, ImageDraw

    mask = Image.new("L", (size * _MASK_SCALE, size * _MASK_SCALE), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse(
        (0, 0, size * _MASK_SCALE - 1, size * _MASK_SCALE - 1),
        fill=255,
    )
    return mask.resize((size, size), Image.Resampling.LANCZOS)


def _circular_image(source_path: Path, size: int = _TRAY_SIZE):
    """Load the source artwork and force a smooth circular alpha silhouette."""
    from PIL import Image

    with Image.open(source_path) as source:
        image = source.convert("RGBA")

    # Remove transparent padding first so the artwork always fills the tray
    # circle instead of rendering as a smaller square-with-margins.
    bounds = image.getbbox()
    if bounds is not None:
        image = image.crop(bounds)

    side = max(image.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(
        image,
        ((side - image.width) // 2, (side - image.height) // 2),
        image,
    )
    square = square.resize((size, size), Image.Resampling.LANCZOS)
    square.putalpha(_circle_mask(size))
    return square


def _muted_variant(image):
    """Create the not-responding image without changing the circular shape."""
    from PIL import Image, ImageOps

    muted = image.copy()
    red, green, blue, alpha = muted.split()
    gray = ImageOps.grayscale(Image.merge("RGB", (red, green, blue)))
    gray = _darken_gray(gray)
    muted = Image.merge("RGBA", (gray, gray, gray, alpha.point(lambda value: int(value * 0.82))))
    return muted


def _darken_gray(gray_image):
    """Keep the muted icon legible on both light and dark Windows taskbars."""
    return gray_image.point(lambda value: int(value * 0.72))


def build_status_images(source_path: Path, size: int = _TRAY_SIZE):
    """Return (running, not-responding) circular tray images."""
    running = _circular_image(source_path, size)
    return running, _muted_variant(running)


def tray_is_supported() -> bool:
    """The tray is intentionally limited to the Windows desktop build."""
    return os.name == "nt"


class TrayHandle:
    """Best-effort wrapper around a running pystray icon."""

    def __init__(self, icon, thread, running_image, muted_image):
        self._icon = icon
        self._thread = thread
        self._running_image = running_image
        self._muted_image = muted_image
        self._healthy = None
        self._closed = threading.Event()

    def update_health(self, healthy: bool) -> None:
        """Swap the icon/title only when the backend state actually changes."""
        if self._closed.is_set() or healthy == self._healthy:
            return
        self._healthy = healthy
        self._icon.icon = self._running_image if healthy else self._muted_image
        self._icon.title = (
            ICON_TITLE_RUNNING if healthy else ICON_TITLE_NOT_RESPONDING
        )

    def stop(self) -> None:
        """Remove the tray icon; safe to call repeatedly during shutdown."""
        if self._closed.is_set():
            return
        self._closed.set()
        try:
            self._icon.stop()
        except Exception:
            pass
        if self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)


def _show_existing_window() -> None:
    """Raise the existing native Opscore window when one is present."""
    try:
        import webview

        window = webview.windows[0] if webview.windows else None
    except Exception:
        window = None
    if window is not None:
        try:
            window.restore()
            window.show()
            return
        except Exception:
            pass

    if not tray_is_supported():
        return
    try:
        import ctypes

        hwnd = ctypes.windll.user32.FindWindowW(None, "Opscore")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
    except Exception:
        pass


def start_tray(icon_path: Path, on_open=None, on_quit=None) -> TrayHandle | None:
    """Start the optional Windows tray icon, returning None when unavailable.

    Any import, image, or notification-area failure is deliberately swallowed:
    the application window and backend must start normally even on systems
    where the tray cannot be created.
    """
    if not tray_is_supported():
        return None

    try:
        import pystray
    except Exception:
        return None

    try:
        running_image, muted_image = build_status_images(icon_path)
    except Exception:
        return None

    open_action = on_open or _show_existing_window
    quit_action = on_quit or (lambda: None)

    def activate_open(icon=None, item=None):
        try:
            open_action()
        except Exception:
            pass

    def activate_quit(icon=None, item=None):
        try:
            quit_action()
        finally:
            try:
                icon.stop()
            except Exception:
                pass

    menu = pystray.Menu(
        pystray.MenuItem("Open Opscore", activate_open, default=True),
        pystray.MenuItem("Quit", activate_quit),
    )
    icon = pystray.Icon(
        "Opscore",
        muted_image,
        ICON_TITLE_NOT_RESPONDING,
        menu,
    )
    handle = TrayHandle(icon, None, running_image, muted_image)

    def run_icon():
        try:
            icon.run()
        except Exception:
            handle._closed.set()

    thread = threading.Thread(
        target=run_icon,
        name="opscore-tray",
        daemon=True,
    )
    handle._thread = thread
    thread.start()
    handle.update_health(backend_is_healthy())
    return handle
