"""
WiFi Optimizer - Main Entry Point
Bootstraps the application: generates icons, starts monitor, tray, and GUI.
"""

import sys
import os
import logging
import ctypes

# ── Ensure project root is on path ───────────────────────────────────────────
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# ── Logging setup ─────────────────────────────────────────────────────────────
LOG_FILE = os.path.join(BASE_DIR, "wifi_optimizer.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("main")


def request_admin():
    """Re-launch as administrator if not already elevated."""
    try:
        is_admin = ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        is_admin = False

    if not is_admin:
        logger.info("Requesting administrator privileges...")
        try:
            ret = ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable,
                " ".join([f'"{a}"' for a in sys.argv]),
                None, 1
            )
            # ShellExecute returns > 32 on success. If <= 32, user cancelled UAC.
            if int(ret) > 32:
                sys.exit(0)
            else:
                logger.info("UAC declined or skipped. Running with standard privileges.")
        except Exception as e:
            logger.warning("Could not elevate: %s — running with limited features.", e)


def main():
    # ── Generate icons ────────────────────────────────────────────────────────
    logger.info("Initializing WiFi Optimizer...")
    os.makedirs(ASSETS_DIR, exist_ok=True)

    try:
        from assets.icon_generator import ensure_assets, create_tray_icons
        ico_path = ensure_assets(ASSETS_DIR)
        create_tray_icons(ASSETS_DIR)
        logger.info("Icons ready at %s", ASSETS_DIR)
    except Exception as e:
        logger.warning("Icon generation error: %s", e)

    # ── Start background monitor ──────────────────────────────────────────────
    try:
        from core.monitor import WiFiMonitor
        monitor = WiFiMonitor(interval_sec=4.0, history_size=120)
    except Exception as e:
        logger.error("Monitor init failed: %s", e)
        monitor = None

    # ── Build GUI ─────────────────────────────────────────────────────────────
    try:
        from ui.app import WiFiOptimizerApp
        app = WiFiOptimizerApp(assets_dir=ASSETS_DIR)
    except Exception as e:
        logger.critical("GUI init failed: %s", e)
        raise

    # ── Build system tray ─────────────────────────────────────────────────────
    tray = None
    try:
        from ui.tray import TrayManager
        tray = TrayManager(
            assets_dir=ASSETS_DIR,
            on_show=app.show,
            on_optimize=app._do_optimize,
            on_quit=lambda: (app.destroy(), sys.exit(0)),
        )
        tray.start()
        app.attach_tray(tray)
        logger.info("System tray started.")
    except Exception as e:
        logger.warning("Tray init failed: %s", e)

    # ── Attach monitor callbacks ──────────────────────────────────────────────
    if monitor:
        try:
            from ui.notifications import notify_signal_weak, notify_signal_restored

            def _alert(title: str, msg: str):
                try:
                    from ui.notifications import notify
                    notify(title, msg, duration="long")
                    if "Weak" in title and app:
                        # Auto-optimize on weak signal if enabled
                        try:
                            if hasattr(app, '_auto_opt_var') and app._auto_opt_var.get():
                                app.after(0, app._do_optimize)
                        except Exception:
                            pass
                except Exception:
                    pass

            monitor.register_alert_callback(_alert)
            app.attach_monitor(monitor)
            monitor.start()
            logger.info("Background monitor started.")
        except Exception as e:
            logger.warning("Monitor attach failed: %s", e)

    # ── Startup notification ───────────────────────────────────────────────────
    try:
        from ui.notifications import notify
        notify("📶 WiFi Optimizer Running",
               "Dashboard open. System tray icon active.", duration="short")
    except Exception:
        pass

    # ── Run main loop ─────────────────────────────────────────────────────────
    logger.info("Starting GUI main loop.")
    try:
        app.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        if monitor:
            monitor.stop()
        if tray:
            tray.stop()
        logger.info("WiFi Optimizer exited.")


if __name__ == "__main__":
    # Ask for admin on startup for full registry access
    request_admin()
    main()
