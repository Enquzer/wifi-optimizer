"""
WiFi Optimizer - Measurement harness
====================================

The project used to *claim* improvements (and even invent "15-40%") without
measuring anything. This module measures the things a client can actually
change:

  * download goodput (HTTP, single stream)
  * TCP connect RTT: median, p95, jitter, loss
  * DNS resolution time
  * negotiated PHY rate and RSSI at the same moment
  * efficiency = goodput / min(rx, tx PHY rate)

Run it several times and compare medians. A single sample is not evidence.

Default endpoint: Cloudflare's public speed-test download (no API key). You can
override with --url. Nothing is uploaded.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import statistics
import time
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

DEFAULT_URL = "https://speed.cloudflare.com/__down?bytes={bytes}"
DEFAULT_PING_HOSTS = [("1.1.1.1", 443), ("8.8.8.8", 53)]
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
RESULTS_FILE = os.path.join(PROJECT_ROOT, "benchmarks.jsonl")


@dataclass
class BenchmarkResult:
    timestamp: str = ""
    ssid: str = ""
    bssid: str = ""
    rssi_dbm: float = 0.0
    link_quality: int = 0
    phy_rx_mbps: float = 0.0
    phy_tx_mbps: float = 0.0
    channel: int = 0
    band: str = ""
    throughput_mbps: float = 0.0
    bytes_downloaded: int = 0
    duration_s: float = 0.0
    latency_median_ms: float = 0.0
    latency_p95_ms: float = 0.0
    jitter_ms: float = 0.0
    loss_pct: float = 0.0
    dns_ms: float = 0.0
    efficiency: Optional[float] = None
    url: str = ""
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def summary(self) -> str:
        eff = f"{self.efficiency * 100:.0f}% of PHY" if self.efficiency else "PHY rate unknown"
        return (f"{self.throughput_mbps:.1f} Mbps down | "
                f"RTT med {self.latency_median_ms:.0f} ms / p95 {self.latency_p95_ms:.0f} ms | "
                f"jitter {self.jitter_ms:.0f} ms | loss {self.loss_pct:.1f}% | "
                f"{self.link_quality}% @ {self.rssi_dbm:.0f} dBm | {eff}")


def _link_snapshot() -> Dict[str, object]:
    try:
        from core.wifi_scanner import get_connected_network
        a = get_connected_network()
        if not a:
            return {}
        return {
            "ssid": a.ssid, "bssid": a.bssid, "rssi_dbm": a.signal_dbm,
            "link_quality": a.signal_percent, "phy_rx_mbps": a.receive_rate_mbps,
            "phy_tx_mbps": a.transmit_rate_mbps, "channel": a.channel,
            "band": a.band,
        }
    except Exception:
        return {}


def _tcp_rtt(host: str, port: int, timeout: float = 3.0) -> Optional[float]:
    """Return TCP connect time in ms (a real path RTT sample), or None."""
    start = time.perf_counter()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return (time.perf_counter() - start) * 1000.0
    except OSError:
        return None


def measure_latency(samples: int = 20, timeout: float = 3.0) -> Dict[str, float]:
    """TCP-connect RTT statistics. Works without ICMP/raw sockets."""
    rtts: List[float] = []
    attempts = 0
    for i in range(max(1, samples)):
        host, port = DEFAULT_PING_HOSTS[i % len(DEFAULT_PING_HOSTS)]
        attempts += 1
        rtt = _tcp_rtt(host, port, timeout)
        if rtt is not None:
            rtts.append(rtt)
    if not rtts:
        return {"median": 0.0, "p95": 0.0, "jitter": 0.0, "loss": 100.0}

    rtts_sorted = sorted(rtts)
    p95 = rtts_sorted[min(len(rtts_sorted) - 1, int(round(0.95 * (len(rtts_sorted) - 1))))]
    jitter = 0.0
    if len(rtts) > 1:
        jitter = statistics.mean(abs(b - a) for a, b in zip(rtts, rtts[1:]))
    return {
        "median": float(statistics.median(rtts)),
        "p95": float(p95),
        "jitter": float(jitter),
        "loss": float(100.0 * (attempts - len(rtts)) / attempts),
    }


def measure_dns(host: str = "www.cloudflare.com", samples: int = 3) -> float:
    """Median getaddrinfo time in ms. May hit the OS cache; still useful as a
    resolver-path indicator when repeated with a fresh hostname."""
    times = []
    for _ in range(max(1, samples)):
        start = time.perf_counter()
        try:
            socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
            times.append((time.perf_counter() - start) * 1000.0)
        except OSError:
            pass
    return float(statistics.median(times)) if times else 0.0


def measure_throughput(download_bytes: int = 8_000_000,
                       url: Optional[str] = None,
                       timeout: float = 45.0) -> Dict[str, object]:
    """Single-stream HTTP download; returns goodput and byte count."""
    byte_count = max(1_000_000, min(int(download_bytes), 50_000_000))
    target = (url or DEFAULT_URL).format(bytes=byte_count)
    req = urllib.request.Request(
        target,
        headers={"User-Agent": "WiFiOptimizer/2.0 (measurement)",
                 "Accept-Encoding": "identity"},
    )
    start = time.perf_counter()
    received = 0
    first_byte_at = None
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                if first_byte_at is None:
                    first_byte_at = time.perf_counter()
                received += len(chunk)
    except Exception as e:
        return {"throughput_mbps": 0.0, "bytes": received,
                "duration_s": time.perf_counter() - start,
                "url": target, "error": str(e)}

    end = time.perf_counter()
    elapsed = max(1e-6, end - (first_byte_at or start))
    return {
        "throughput_mbps": (received * 8.0) / elapsed / 1_000_000.0,
        "bytes": received,
        "duration_s": elapsed,
        "url": target,
    }


def measure(download_bytes: int = 8_000_000,
            latency_samples: int = 20,
            url: Optional[str] = None) -> BenchmarkResult:
    """One complete measurement run."""
    snapshot = _link_snapshot()
    latency = measure_latency(latency_samples)
    dns_ms = measure_dns()
    tput = measure_throughput(download_bytes, url)

    phy_rx = float(snapshot.get("phy_rx_mbps") or 0.0)
    phy_tx = float(snapshot.get("phy_tx_mbps") or 0.0)
    phy_floor = min(v for v in (phy_rx, phy_tx) if v > 0) if (phy_rx > 0 or phy_tx > 0) else 0.0
    efficiency = (tput["throughput_mbps"] / phy_floor) if phy_floor > 0 else None

    errors = []
    if tput.get("error"):
        errors.append(f"download: {tput['error']}")

    return BenchmarkResult(
        timestamp=datetime.now(timezone.utc).isoformat(),
        ssid=str(snapshot.get("ssid", "")),
        bssid=str(snapshot.get("bssid", "")),
        rssi_dbm=float(snapshot.get("rssi_dbm", 0.0) or 0.0),
        link_quality=int(snapshot.get("link_quality", 0) or 0),
        phy_rx_mbps=phy_rx,
        phy_tx_mbps=phy_tx,
        channel=int(snapshot.get("channel", 0) or 0),
        band=str(snapshot.get("band", "")),
        throughput_mbps=float(tput["throughput_mbps"]),
        bytes_downloaded=int(tput["bytes"]),
        duration_s=float(tput["duration_s"]),
        latency_median_ms=float(latency["median"]),
        latency_p95_ms=float(latency["p95"]),
        jitter_ms=float(latency["jitter"]),
        loss_pct=float(latency["loss"]),
        dns_ms=float(dns_ms),
        efficiency=efficiency,
        url=str(tput.get("url", "")),
        errors=errors,
    )


def measure_median(reps: int = 3, **kwargs) -> Dict[str, object]:
    """Run several samples and return the median of each metric (noise control)."""
    runs = [measure(**kwargs).to_dict() for _ in range(max(1, int(reps)))]
    keys = ("throughput_mbps", "latency_median_ms", "latency_p95_ms",
            "jitter_ms", "loss_pct", "dns_ms", "rssi_dbm", "phy_rx_mbps",
            "phy_tx_mbps", "efficiency")
    med = {}
    for k in keys:
        vals = [r[k] for r in runs if isinstance(r.get(k), (int, float))]
        if vals:
            med[k] = float(statistics.median(vals))
        else:
            med[k] = None if k == "efficiency" else 0.0
    med["reps"] = len(runs)
    med["runs"] = runs
    return med


def save_result(result: BenchmarkResult, label: str = "") -> str:
    """Append a JSON line to benchmarks.jsonl next to the project."""
    record = result.to_dict()
    record["label"] = label
    try:
        with open(RESULTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("Could not save benchmark: %s", e)
    return RESULTS_FILE
