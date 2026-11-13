"""Windows status-only tray behavior and circular icon preparation.

The production tray imports pystray/Pillow lazily; these tests exercise the
pure health/state logic and image generation when Pillow is available without
creating a real notification-area icon.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
TRAY_PATH = ROOT / "packaging" / "opscore_tray.py"


def load_tray_module():
    """Import the tray helper without conflicting with PyPI's packaging module."""
    spec = importlib.util.spec_from_file_location("opscore_tray_under_test", TRAY_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


opscore_tray = load_tray_module()


class TestBackendHealth(unittest.TestCase):
    def test_health_requires_http_ok(self):
        response = mock.Mock(status=200)
        response.__enter__ = mock.Mock(return_value=response)
        response.__exit__ = mock.Mock(return_value=None)
        with mock.patch.object(
            opscore_tray.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            self.assertTrue(opscore_tray.backend_is_healthy())
        urlopen.assert_called_once_with(opscore_tray.HEALTH_URL, timeout=2.0)

    def test_http_error_or_socket_error_is_not_healthy(self):
        with mock.patch.object(
            opscore_tray.urllib.request, "urlopen", side_effect=OSError
        ):
            self.assertFalse(opscore_tray.backend_is_healthy())


@unittest.skipUnless(
    importlib.util.find_spec("PIL"),
    "Pillow is not installed",
)
class TestTrayImages(unittest.TestCase):
    def _source_png(self) -> Path:
        path = Path(self._tmp.name) / "source.png"
        with open(path, "wb") as handle:
            handle.write(self._png_bytes())
        return path

    @staticmethod
    def _png_bytes() -> bytes:
        from PIL import Image, ImageDraw

        image = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.ellipse((4, 4, 27, 27), fill=(255, 128, 0, 255))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self._tmp.cleanup()

    def test_build_status_images_are_circular_with_transparent_corners(self):
        running, muted = opscore_tray.build_status_images(self._source_png())
        self.assertEqual(running.mode, "RGBA")
        self.assertEqual(muted.mode, "RGBA")
        self.assertEqual(running.size, muted.size)
        width, _ = running.size
        for image in (running, muted):
            alpha = image.getchannel("A")
            self.assertEqual(alpha.getpixel((0, 0)), 0)
            self.assertEqual(alpha.getpixel((width - 1, 0)), 0)
            self.assertEqual(alpha.getpixel((0, width - 1)), 0)
            self.assertEqual(alpha.getpixel((width - 1, width - 1)), 0)
            self.assertGreater(alpha.getpixel((width // 2, width // 2)), 0)
        self.assertEqual(alpha.getpixel((0, 0)), muted.getchannel("A").getpixel((0, 0)))
        self.assertLess(
            muted.getchannel("A").getpixel((width // 2, width // 2)),
            alpha.getpixel((width // 2, width // 2)),
        )

    def test_real_opscore_asset_produces_centered_circular_icon(self):
        running, _ = opscore_tray.build_status_images(ROOT / "assets" / "icon-512.png")
        alpha = running.getchannel("A")
        width, _ = running.size
        self.assertEqual(alpha.getpixel((0, 0)), 0)
        self.assertGreater(alpha.getpixel((width // 2, width // 2)), 0)


class TestTrayHandle(unittest.TestCase):
    def test_update_health_swaps_icon_and_title_once_per_state(self):
        icon = mock.Mock()
        thread = mock.Mock(is_alive=mock.Mock(return_value=False))
        handle = opscore_tray.TrayHandle(icon, thread, "running", "muted")
        handle.update_health(True)
        handle.update_health(True)
        self.assertEqual(icon.icon, "running")
        self.assertEqual(icon.title, opscore_tray.ICON_TITLE_RUNNING)
        handle.update_health(False)
        self.assertEqual(icon.icon, "muted")
        self.assertEqual(icon.title, opscore_tray.ICON_TITLE_NOT_RESPONDING)

    def test_stop_is_idempotent(self):
        icon = mock.Mock()
        thread = mock.Mock(is_alive=mock.Mock(return_value=False))
        handle = opscore_tray.TrayHandle(icon, thread, "running", "muted")
        handle.stop()
        handle.stop()
        icon.stop.assert_called_once_with()


class TestStartTray(unittest.TestCase):
    def test_non_windows_never_starts_tray(self):
        with mock.patch.object(opscore_tray.os, "name", "posix"):
            self.assertIsNone(opscore_tray.start_tray(Path("icon.png")))

    def test_missing_pystray_is_ignored(self):
        with mock.patch.object(opscore_tray.os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"pystray": None}):
            self.assertIsNone(opscore_tray.start_tray(Path("icon.png")))

    def test_menu_has_open_default_and_quit(self):
        pystray = mock.Mock()
        menu_items = []

        class FakeMenu:
            def __init__(self, *items):
                menu_items.extend(items)

        pystray.Menu = FakeMenu
        pystray.MenuItem = lambda text, action, **kwargs: (text, action, kwargs)
        pystray.Icon = mock.Mock(return_value=mock.Mock())
        with mock.patch.object(opscore_tray.os, "name", "nt"), \
                mock.patch.dict(sys.modules, {"pystray": pystray}), \
                mock.patch.object(
                    opscore_tray, "build_status_images", return_value=("running", "muted")
                ), \
                mock.patch.object(opscore_tray.threading, "Thread") as thread_class, \
                mock.patch.object(opscore_tray, "backend_is_healthy", return_value=True):
            thread = mock.Mock()
            thread_class.return_value = thread
            handle = opscore_tray.start_tray(Path("icon.png"))
        self.assertIsNotNone(handle)
        self.assertEqual(menu_items[0][0], "Open Opscore")
        self.assertEqual(menu_items[0][2], {"default": True})
        self.assertEqual(menu_items[1][0], "Quit")
        thread.start.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
