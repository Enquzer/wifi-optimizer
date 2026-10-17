"""
WiFi Optimizer - Benchmark & Telemetry Verification Harness
Performs automated, reproducible measurements of:
1. Ping RTT Latency, Jitter (standard deviation), and Packet Loss percentage.
2. Signal Telemetry: Real hardware RSSI (wlanapi.dll) vs Synthetic netsh RSSI.
3. Link Speeds: Transmit / Receive Physical layer rates (Mbps).
4. Before / After optimization comparison and JSON report export.
"""

import subprocess
import time
import re
import os
import sys
import json
import statistics
from typing import Dict, Any, List, Optional

# Add src/ to sys.path
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.wifi_scanner import get_connected_network, scan_networks, query_native_wlan_bss_rssi


def measure_ping_latency(host: str = "1.1.1.1", count: int = 15, timeout_sec: int = 2) -> Dict[str, Any]:
    """
    Measure round-trip time (RTT), jitter (std dev), and packet loss to a target host.
    """
    try:
        cmd = ["ping", "-n", str(count), "-w", str(timeout_sec * 1000), host]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=count * timeout_sec + 5)
        out = res.stdout

        times = []
        for line in out.splitlines():
            m = re.search(r"time[<=](\d+)ms", line, re.IGNORECASE)
            if m:
                times.append(float(m.group(1)))

        lost_match = re.search(r"Lost = (\d+) \((\d+)% loss\)", out)
        loss_pct = float(lost_match.group(2)) if lost_match else (
            100.0 * (count - len(times)) / count if count > 0 else 0.0
        )

        min_rtt = min(times) if times else 0.0
        max_rtt = max(times) if times else 0.0
        avg_rtt = statistics.mean(times) if times else 0.0
        jitter = statistics.stdev(times) if len(times) > 1 else 0.0

        return {
            "target": host,
            "packets_sent": count,
            "packets_received": len(times),
            "packet_loss_pct": round(loss_pct, 1),
            "min_rtt_ms": round(min_rtt, 1),
            "avg_rtt_ms": round(avg_rtt, 1),
            "max_rtt_ms": round(max_rtt, 1),
            "jitter_ms": round(jitter, 2),
            "samples": times
        }
    except Exception as e:
        return {"error": str(e), "target": host}


def capture_telemetry_snapshot() -> Dict[str, Any]:
    """Capture comprehensive hardware and RF telemetry."""
    adapter = get_connected_network()
    native_map = query_native_wlan_bss_rssi()

    net_info = {}
    if adapter:
        net_info = {
            "name": adapter.name,
            "ssid": adapter.ssid,
            "bssid": adapter.bssid,
            "channel": adapter.channel,
            "signal_percent": adapter.signal_percent,
            "signal_dbm_reported": adapter.signal_dbm,
            "is_native_rssi": adapter.is_native_rssi,
            "rssi_uncertainty_db": adapter.rssi_uncertainty_db,
            "rx_rate_mbps": adapter.receive_rate_mbps,
            "tx_rate_mbps": adapter.transmit_rate_mbps,
            "radio_type": adapter.radio_type,
            "authentication": adapter.authentication,
        }
        if adapter.bssid and adapter.bssid.upper() in native_map:
            net_info["native_hardware_rssi_dbm"] = native_map[adapter.bssid.upper()]

    return {
        "timestamp": time.time(),
        "adapter": net_info,
        "visible_bssids_count": len(native_map),
    }


def run_full_benchmark(output_json: Optional[str] = None) -> Dict[str, Any]:
    """Execute complete benchmark run."""
    print("=" * 60)
    print("[*] WiFi Optimizer Benchmark & Telemetry Verification Harness")
    print("=" * 60)

    print("\n[1/3] Capturing RF and Adapter Telemetry...")
    telemetry = capture_telemetry_snapshot()
    if telemetry.get("adapter"):
        ad = telemetry["adapter"]
        print(f"  - Connected SSID: {ad.get('ssid')} ({ad.get('bssid')})")
        print(f"  - Channel: {ad.get('channel')} | Radio: {ad.get('radio_type')}")
        print(f"  - Link Speeds: RX {ad.get('rx_rate_mbps')} Mbps / TX {ad.get('tx_rate_mbps')} Mbps")
        print(f"  - Signal: {ad.get('signal_percent')}% | Synthesized dBm: {ad.get('signal_dbm_reported')} dBm")
        if "native_hardware_rssi_dbm" in ad:
            print(f"  - REAL Hardware Measured RSSI: {ad.get('native_hardware_rssi_dbm')} dBm (Native Wi-Fi API)")
        else:
            print(f"  - Hardware RSSI: Using synthetic netsh mapping (+/-{ad.get('rssi_uncertainty_db')} dB uncertainty)")
    else:
        print("  [!] No active Wi-Fi connection detected.")

    print("\n[2/3] Measuring Ping Latency & Jitter to Cloudflare (1.1.1.1)...")
    ping_cf = measure_ping_latency("1.1.1.1", count=15)
    print(f"  - RTT: Avg {ping_cf.get('avg_rtt_ms')} ms (Min: {ping_cf.get('min_rtt_ms')} ms, Max: {ping_cf.get('max_rtt_ms')} ms)")
    print(f"  - Jitter: +/-{ping_cf.get('jitter_ms')} ms | Packet Loss: {ping_cf.get('packet_loss_pct')}%")

    print("\n[3/3] Measuring Ping Latency & Jitter to Google (8.8.8.8)...")
    ping_google = measure_ping_latency("8.8.8.8", count=15)
    print(f"  - RTT: Avg {ping_google.get('avg_rtt_ms')} ms | Jitter: +/-{ping_google.get('jitter_ms')} ms | Loss: {ping_google.get('packet_loss_pct')}%")

    report = {
        "benchmark_timestamp": time.time(),
        "telemetry": telemetry,
        "latency_cloudflare": ping_cf,
        "latency_google": ping_google,
    }

    if output_json:
        try:
            with open(output_json, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=2)
            print(f"\n[+] Benchmark report saved to: {output_json}")
        except Exception as e:
            print(f"\n[!] Could not save benchmark report: {e}")

    print("\n" + "=" * 60)
    print("[+] Benchmark Completed.")
    print("=" * 60)
    return report


if __name__ == "__main__":
    report_file = sys.argv[1] if len(sys.argv) > 1 else None
    run_full_benchmark(report_file)
