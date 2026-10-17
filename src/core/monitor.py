"""
WiFi Optimizer - Background Monitor & Link Governor Module
Continuously monitors signal strength, speed, and connection quality.
Implements an EWMA-smoothed closed-loop Link Governor with hysteresis and cooldown.
"""

import threading
import time
import psutil
import logging
from dataclasses import dataclass
from typing import Callable, List, Optional, Tuple
from core.wifi_scanner import get_connected_network, AdapterInfo, percent_to_dbm

logger = logging.getLogger(__name__)


class GovernorState:
    HEALTHY = "Healthy"
    DEGRADED = "Degraded"


@dataclass
class MonitorSnapshot:
    """A single monitoring snapshot."""
    timestamp: float
    signal_percent: int
    signal_dbm: float
    smoothed_signal_dbm: float
    receive_rate_mbps: float
    transmit_rate_mbps: float
    bytes_sent: int
    bytes_recv: int
    throughput_down_mbps: float
    throughput_up_mbps: float
    ssid: str
    channel: int
    quality: str  # Excellent / Good / Fair / Poor / Very Poor
    governor_state: str = GovernorState.HEALTHY

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "signal_percent": self.signal_percent,
            "signal_dbm": round(self.signal_dbm, 1),
            "smoothed_signal_dbm": round(self.smoothed_signal_dbm, 1),
            "receive_rate_mbps": self.receive_rate_mbps,
            "transmit_rate_mbps": self.transmit_rate_mbps,
            "throughput_down_mbps": round(self.throughput_down_mbps, 2),
            "throughput_up_mbps": round(self.throughput_up_mbps, 2),
            "ssid": self.ssid,
            "channel": self.channel,
            "quality": self.quality,
            "governor_state": self.governor_state,
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
    Features EWMA smoothing and an anti-flapping Link Governor state machine.
    """

    def __init__(self, interval_sec: float = 3.0, history_size: int = 60,
                 alpha: float = 0.3, cooldown_sec: float = 30.0):
        self.interval_sec = interval_sec
        self.history_size = history_size
        self.alpha = alpha  # EWMA smoothing factor
        self.cooldown_sec = cooldown_sec

        self._callbacks: List[Callable[[MonitorSnapshot], None]] = []
        self._alert_callbacks: List[Callable[[str, str], None]] = []
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._history: List[MonitorSnapshot] = []
        self._lock = threading.Lock()

        # Telemetry state
        self._prev_bytes_sent = 0
        self._prev_bytes_recv = 0
        self._prev_time = 0.0
        self._running = False

        # Link Governor state machine
        self.governor_state: str = GovernorState.HEALTHY
        self.smoothed_dbm: Optional[float] = None
        self._degraded_counter: int = 0
        self._healthy_counter: int = 0
        self._last_state_change: float = 0.0

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
        logger.info("WiFi monitor started (interval=%.1fs, governor=EWMA-smoothed)", self.interval_sec)

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

    def update_governor(self, raw_dbm: float, now: float) -> Optional[Tuple[str, str]]:
        """
        Advance the Governor state machine with a new RSSI reading.
        Applies EWMA smoothing:
          P_tilde(t) = alpha * P(t) + (1 - alpha) * P_tilde(t-1)
        Transitions:
          HEALTHY -> DEGRADED: 3 consecutive samples with P_tilde < -75 dBm
          DEGRADED -> HEALTHY: 5 consecutive samples with P_tilde >= -70 dBm
          Guarded by 30s cooldown.
        """
        if self.smoothed_dbm is None:
            self.smoothed_dbm = raw_dbm
        else:
            self.smoothed_dbm = self.alpha * raw_dbm + (1.0 - self.alpha) * self.smoothed_dbm

        # Track smoothed history for gradient drop evaluation (last 15s window)
        if not hasattr(self, "_recent_smoothed"):
            self._recent_smoothed = []
        self._recent_smoothed.append((now, self.smoothed_dbm))
        self._recent_smoothed = [(t, val) for t, val in self._recent_smoothed if now - t <= 15.0]

        # Evaluate sudden gradient drop: delta <= -15.0 dB over <= 12 seconds
        sudden_drop = False
        if len(self._recent_smoothed) >= 2:
            earliest_t, earliest_val = self._recent_smoothed[0]
            if (now - earliest_t) <= 12.0 and (self.smoothed_dbm - earliest_val) <= -15.0:
                sudden_drop = True

        alert = None

        if self.governor_state == GovernorState.HEALTHY:
            if self.smoothed_dbm < -75.0 or sudden_drop:
                self._degraded_counter += 1
                self._healthy_counter = 0
                if self._degraded_counter >= 3 or sudden_drop:
                    if (now - self._last_state_change) >= self.cooldown_sec:
                        self.governor_state = GovernorState.DEGRADED
                        self._last_state_change = now
                        self._degraded_counter = 0
                        reason = "Sudden signal drop (∇P <= -15 dB)" if sudden_drop else f"Smoothed signal degraded to {self.smoothed_dbm:.1f} dBm"
                        alert = (
                            "⚠️ WiFi Link Degraded",
                            f"{reason}. Link margin is low."
                        )
            else:
                self._degraded_counter = 0

        elif self.governor_state == GovernorState.DEGRADED:
            if self.smoothed_dbm >= -70.0:
                self._healthy_counter += 1
                self._degraded_counter = 0
                if self._healthy_counter >= 5:
                    if (now - self._last_state_change) >= self.cooldown_sec:
                        self.governor_state = GovernorState.HEALTHY
                        self._last_state_change = now
                        self._healthy_counter = 0
                        alert = (
                            "✅ WiFi Link Restored",
                            f"Smoothed signal recovered to {self.smoothed_dbm:.1f} dBm."
                        )
            else:
                self._healthy_counter = 0

        return alert

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

            except Exception as e:
                logger.error("Monitor loop error: %s", e)

            self._stop_event.wait(self.interval_sec)

    def _take_snapshot(self) -> Optional[MonitorSnapshot]:
        """Take a single monitoring snapshot and update governor state."""
        adapter = get_connected_network()
        now = time.time()

        # Network throughput
        try:
            counters = psutil.net_io_counters()
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

        # Update Link Governor
        alert = self.update_governor(signal_dbm, now)
        if alert and self._alert_callbacks:
            for cb in self._alert_callbacks:
                try:
                    cb(alert[0], alert[1])
                except Exception as e:
                    logger.error("Governor alert callback error: %s", e)

        snap = MonitorSnapshot(
            timestamp=now,
            signal_percent=signal_pct,
            signal_dbm=signal_dbm,
            smoothed_signal_dbm=self.smoothed_dbm if self.smoothed_dbm is not None else signal_dbm,
            receive_rate_mbps=adapter.receive_rate_mbps if adapter else 0.0,
            transmit_rate_mbps=adapter.transmit_rate_mbps if adapter else 0.0,
            bytes_sent=counters.bytes_sent if counters else 0,
            bytes_recv=counters.bytes_recv if counters else 0,
            throughput_down_mbps=max(0.0, throughput_down),
            throughput_up_mbps=max(0.0, throughput_up),
            ssid=adapter.ssid if adapter else "",
            channel=adapter.channel if adapter else 0,
            quality=signal_to_quality(signal_pct),
            governor_state=self.governor_state,
        )
        return snap
