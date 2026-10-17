"""
WiFi Optimizer - Background Monitor Module
Continuously monitors signal strength, speed, and connection quality.
"""

import threading
import time
import psutil
import logging
from dataclasses import dataclass, field
from typing import Callable, List, Optional
from core.wifi_scanner import get_connected_network, AdapterInfo, percent_to_dbm

logger = logging.getLogger(__name__)


@dataclass
class MonitorSnapshot:
    """A single monitoring snapshot."""
    timestamp: float
    signal_percent: int
    signal_dbm: float
    receive_rate_mbps: float
    transmit_rate_mbps: float
    bytes_sent: int
    bytes_recv: int
    throughput_down_mbps: float
    throughput_up_mbps: float
    ssid: str
    channel: int
    quality: str  # Excellent / Good / Fair / Poor

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "signal_percent": self.signal_percent,
            "signal_dbm": round(self.signal_dbm, 1),
            "receive_rate_mbps": self.receive_rate_mbps,
            "transmit_rate_mbps": self.transmit_rate_mbps,
            "throughput_down_mbps": round(self.throughput_down_mbps, 2),
            "throughput_up_mbps": round(self.throughput_up_mbps, 2),
            "ssid": self.ssid,
            "channel": self.channel,
            "quality": self.quality,
        }


def signal_to_quality(percent: int) -> str:
    if percent >= 80:
        return "Excellent"
    elif percent >= 60:
        return "Good"
    elif percent >= 40:
        return "Fair"
    elif percent >= 20:
        return "Poor"
    return "Very Poor"


class WiFiMonitor:
    """
    Background thread that periodically samples WiFi metrics.
    Calls registered callbacks with each new snapshot.
    """

    def __init__(self, interval_sec: float = 3.0, history_size: int = 60):
        self.interval_sec = interval_sec
        self.history_size = history_size
        self._callbacks: List[Callable[[MonitorSnapshot], None]] = []
        self._alert_callbacks: List[Callable[[str, str], None]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._history: List[MonitorSnapshot] = []
        self._lock = threading.Lock()
        self._prev_bytes_sent = 0
        self._prev_bytes_recv = 0
        self._prev_time = 0.0
        self._last_signal = -1
        self._running = False

    def register_callback(self, fn: Callable[[MonitorSnapshot], None]):
        """Register a function to call on each new snapshot."""
        self._callbacks.append(fn)

    def register_alert_callback(self, fn: Callable[[str, str], None]):
        """Register a function to call when alert conditions are met: fn(title, message)."""
        self._alert_callbacks.append(fn)

    def start(self):
        if self._running:
            return
        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="WiFiMonitor")
        self._thread.start()
        logger.info("WiFi monitor started (interval=%.1fs)", self.interval_sec)

    def stop(self):
        self._stop_event.set()
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
        logger.info("WiFi monitor stopped.")

    @property
    def history(self) -> List[MonitorSnapshot]:
        with self._lock:
            return list(self._history)

    @property
    def latest(self) -> Optional[MonitorSnapshot]:
        with self._lock:
            return self._history[-1] if self._history else None

    def _run_loop(self):
        # Initialize previous network stats
        try:
            counters = psutil.net_io_counters()
            self._prev_bytes_sent = counters.bytes_sent
            self._prev_bytes_recv = counters.bytes_recv
            self._prev_time = time.time()
        except Exception:
            pass

        while not self._stop_event.is_set():
            try:
                snapshot = self._take_snapshot()
                if snapshot:
                    with self._lock:
                        self._history.append(snapshot)
                        if len(self._history) > self.history_size:
                            self._history.pop(0)

                    for cb in self._callbacks:
                        try:
                            cb(snapshot)
                        except Exception as e:
                            logger.error("Monitor callback error: %s", e)

                    # Check alert conditions
                    self._check_alerts(snapshot)

            except Exception as e:
                logger.error("Monitor loop error: %s", e)

            self._stop_event.wait(self.interval_sec)

    def _take_snapshot(self) -> Optional[MonitorSnapshot]:
        """Take a single monitoring snapshot."""
        adapter = get_connected_network()

        # Network throughput
        try:
            counters = psutil.net_io_counters()
            now = time.time()
            dt = now - self._prev_time if self._prev_time else 1.0
            if dt <= 0:
                dt = 1.0

            bytes_sent_delta = counters.bytes_sent - self._prev_bytes_sent
            bytes_recv_delta = counters.bytes_recv - self._prev_bytes_recv

            throughput_down = (bytes_recv_delta / dt) / 1_000_000 * 8  # Mbps
            throughput_up = (bytes_sent_delta / dt) / 1_000_000 * 8    # Mbps

            self._prev_bytes_sent = counters.bytes_sent
            self._prev_bytes_recv = counters.bytes_recv
            self._prev_time = now
        except Exception:
            counters = None
            throughput_down = 0.0
            throughput_up = 0.0

        signal_pct = adapter.signal_percent if adapter else 0
        signal_dbm = adapter.signal_dbm if adapter else -100.0

        snap = MonitorSnapshot(
            timestamp=time.time(),
            signal_percent=signal_pct,
            signal_dbm=signal_dbm,
            receive_rate_mbps=adapter.receive_rate_mbps if adapter else 0.0,
            transmit_rate_mbps=adapter.transmit_rate_mbps if adapter else 0.0,
            bytes_sent=counters.bytes_sent if counters else 0,
            bytes_recv=counters.bytes_recv if counters else 0,
            throughput_down_mbps=max(0.0, throughput_down),
            throughput_up_mbps=max(0.0, throughput_up),
            ssid=adapter.ssid if adapter else "",
            channel=adapter.channel if adapter else 0,
            quality=signal_to_quality(signal_pct),
        )
        return snap

    def _check_alerts(self, snap: MonitorSnapshot):
        """Fire alert callbacks on significant signal changes."""
        if not self._alert_callbacks:
            return

        # Alert if signal drops below 20%
        if self._last_signal >= 30 and snap.signal_percent < 20:
            for cb in self._alert_callbacks:
                cb("⚠️ WiFi Signal Dropped",
                   f"Signal is very weak ({snap.signal_percent}%). "
                   f"Move closer to your router or run optimization.")

        # Alert if signal recovers
        if self._last_signal < 20 and snap.signal_percent >= 40:
            for cb in self._alert_callbacks:
                cb("✅ WiFi Signal Improved",
                   f"Signal restored to {snap.signal_percent}% — {snap.quality}")

        self._last_signal = snap.signal_percent
