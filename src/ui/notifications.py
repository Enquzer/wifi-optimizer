"""
WiFi Optimizer - Windows Notifications Module
Sends Windows toast notifications using winotify with rich content.
"""

import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

_NOTIF_AVAILABLE = False
try:
    from winotify import Notification, audio
    _NOTIF_AVAILABLE = True
except ImportError:
    logger.warning("winotify not available — notifications disabled.")


def _get_app_icon() -> str:
    """Get the app icon path."""
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", "app_icon.ico")


def _send(title: str, message: str, icon: Optional[str] = None,
          duration: str = "short", sound: bool = True):
    """Internal: send a toast notification."""
    if not _NOTIF_AVAILABLE:
        logger.info("[NOTIFICATION] %s: %s", title, message)
        return

    try:
        ico = icon or _get_app_icon()
        toast = Notification(
            app_id="WiFi Optimizer",
            title=title,
            msg=message,
            duration=duration,
            icon=ico if os.path.exists(ico) else "",
        )
        if sound:
            toast.set_audio(audio.Default, loop=False)
        toast.show()
    except Exception as e:
        logger.error("Notification error: %s", e)


def notify(title: str, message: str, icon: Optional[str] = None,
           duration: str = "short", async_send: bool = True):
    """Send a Windows toast notification (async by default)."""
    if async_send:
        threading.Thread(target=_send, args=(title, message, icon, duration, True),
                         daemon=True).start()
    else:
        _send(title, message, icon, duration, True)


def notify_optimization_start(profile_name: str):
    notify(
        "🔧 Optimizing WiFi...",
        f"Applying {profile_name} profile settings. This will take a moment.",
        duration="short"
    )


def notify_optimization_complete(settings_count: int, improvement: str):
    notify(
        "✅ WiFi Optimized!",
        f"{settings_count} settings applied. Estimated improvement: {improvement}",
        duration="long"
    )


def notify_signal_weak(signal_percent: int):
    notify(
        "⚠️ Weak WiFi Signal",
        f"Signal dropped to {signal_percent}%. Move closer to router or run optimization.",
        duration="long"
    )


def notify_signal_restored(signal_percent: int, quality: str):
    notify(
        "📶 Signal Restored",
        f"WiFi signal back to {signal_percent}% — {quality}",
        duration="short"
    )


def notify_connected(ssid: str, signal_percent: int):
    notify(
        "🔗 WiFi Connected",
        f"Connected to '{ssid}' — Signal: {signal_percent}%",
        duration="short"
    )


def notify_error(message: str):
    notify(
        "❌ WiFi Optimizer Error",
        message,
        duration="long"
    )
