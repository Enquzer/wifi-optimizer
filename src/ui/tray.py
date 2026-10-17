"""
WiFi Optimizer - System Tray Module
Manages the pystray system tray icon with context menu.
"""

import threading
import logging
import os
from typing import Callable, Optional
from PIL import Image

logger = logging.getLogger(__name__)

_PYSTRAY_AVAILABLE = False
try:
    import pystray
    from pystray import MenuItem as item, Menu
    _PYSTRAY_AVAILABLE = True
except ImportError:
    logger.warning("pystray not available — system tray disabled.")


class TrayManager:
    """Manages the Windows system tray icon for WiFi Optimizer."""

    def __init__(self, assets_dir: str,
                 on_show: Optional[Callable] = None,
                 on_optimize: Optional[Callable] = None,
                 on_quit: Optional[Callable] = None):
        self.assets_dir = assets_dir
        self.on_show = on_show
        self.on_optimize = on_optimize
        self.on_quit = on_quit
        self._icon: Optional[object] = None
        self._thread: Optional[threading.Thread] = None
        self._signal_percent = 0
        self._ssid = "Not Connected"
        self._quality = "Unknown"

    def _load_image(self, name: str) -> Optional[Image.Image]:
        path = os.path.join(self.assets_dir, f"tray_{name}.png")
        if os.path.exists(path):
            return Image.open(path).convert("RGBA")
        # Fallback: create a simple colored image
        img = Image.new("RGBA", (64, 64), (0, 120, 255, 200))
        return img

    def _get_current_image(self) -> Image.Image:
        if self._signal_percent >= 80:
            return self._load_image("full")
        elif self._signal_percent >= 60:
            return self._load_image("high")
        elif self._signal_percent >= 40:
            return self._load_image("medium")
        elif self._signal_percent > 0:
            return self._load_image("low")
        return self._load_image("none")

    def _build_menu(self):
        if not _PYSTRAY_AVAILABLE:
            return None

        ssid_text = f"📶 {self._ssid}" if self._ssid else "Not connected"
        signal_text = f"Signal: {self._signal_percent}% — {self._quality}"

        return Menu(
            item(ssid_text, None, enabled=False),
            item(signal_text, None, enabled=False),
            Menu.SEPARATOR,
            item("🖥️ Open Dashboard", self._handle_show, default=True),
            item("⚡ Quick Optimize", self._handle_optimize),
            Menu.SEPARATOR,
            item("❌ Quit", self._handle_quit),
        )

    def _handle_show(self, icon=None, item=None):
        if self.on_show:
            self.on_show()

    def _handle_optimize(self, icon=None, item=None):
        if self.on_optimize:
            self.on_optimize()

    def _handle_quit(self, icon=None, item=None):
        if self._icon:
            self._icon.stop()
        if self.on_quit:
            self.on_quit()

    def start(self):
        """Start the system tray icon in a background thread."""
        if not _PYSTRAY_AVAILABLE:
            logger.warning("pystray not available — skipping tray.")
            return

        def _run():
            img = self._get_current_image()
            self._icon = pystray.Icon(
                name="wifi_optimizer",
                icon=img,
                title="WiFi Optimizer",
                menu=self._build_menu(),
            )
            self._icon.run()

        self._thread = threading.Thread(target=_run, daemon=True, name="TrayIcon")
        self._thread.start()
        logger.info("System tray started.")

    def update(self, signal_percent: int, ssid: str, quality: str):
        """Update the tray icon and tooltip based on current WiFi state."""
        self._signal_percent = signal_percent
        self._ssid = ssid
        self._quality = quality

        if not self._icon or not _PYSTRAY_AVAILABLE:
            return

        try:
            new_img = self._get_current_image()
            self._icon.icon = new_img
            self._icon.title = f"WiFi Optimizer — {ssid} | {signal_percent}% | {quality}"
            self._icon.menu = self._build_menu()
        except Exception as e:
            logger.error("Tray update error: %s", e)

    def set_optimizing(self):
        """Show the 'optimizing' icon animation."""
        if not self._icon or not _PYSTRAY_AVAILABLE:
            return
        try:
            img = self._load_image("optimizing")
            self._icon.icon = img
            self._icon.title = "WiFi Optimizer — Optimizing..."
        except Exception as e:
            logger.error("Tray optimizing icon error: %s", e)

    def stop(self):
        if self._icon:
            try:
                self._icon.stop()
            except Exception:
                pass
