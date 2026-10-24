"""
WiFi Optimizer - Independent Verification
=========================================

Measures the live link with real telemetry (Native Wi-Fi API) and a real
download/RTT benchmark, then compares a before/after pair.

    python scripts/verify_optimization.py before
    ... run the optimizer ...
    python scripts/verify_optimization.py after

The tool prints measured values and deltas. It does not invent an
"improvement percentage". A single sample is noisy: use --reps 3 for the
benchmark, and repeat the whole before/after pair if the deltas are small.

Options:
    --bytes N        download size per sample (default 8,000,000)
    --samples N      TCP-connect latency samples (default 20)
    --reps N         benchmark repetitions; medians are compared (default 1)
    --no-benchmark   telemetry only (fast)
"""

import argparse
import json
import os
import sys
from datetime import datetime

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core import adapter_manager, benchmark  # noqa: E402
from core.wifi_scanner import get_connected_network, scan_networks  # noqa: E402

STATE_FILE = os.path.join(BASE_DIR, "verify_before.json")
ROLLBACK_FILE = os.path.join(SRC_DIR, "core", "adapter_rollback_snapshot.json")
SYSTEM_SNAPSHOT = os.path.join(SRC_DIR, "core", "system_rollback_snapshot.json")


def _admin() -> bool:
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def capture(args, with_benchmark: bool) -> dict:
    adapter = get_connected_network()
    networks = scan_networks()
    data = {
        "time": datetime.now().isoformat(),
        "admin": _admin(),
        "adapter": adapter.__dict__ if adapter else None,
        "bss_count": len(networks),
        "bands": sorted({n.band for n in networks}),
        "benchmark": None,
    }
    if with_benchmark:
        if args.reps > 1:
            data["benchmark"] = benchmark.measure_median(
                reps=args.reps, download_bytes=args.bytes,
                latency_samples=args.samples)
        else:
            data["benchmark"] = benchmark.measure(
                download_bytes=args.bytes,
                latency_samples=args.samples).to_dict()
    return data


def _fmt(value, unit="", digits=1):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{unit}"
    except (TypeError, ValueError):
        return str(value)


def _delta(before, after, unit="", digits=1, lower_is_better=False):
    if before is None or after is None:
        return "—"
    try:
        diff = float(after) - float(before)
    except (TypeError, ValueError):
        return "—"
    if abs(diff) < 10 ** (-digits) / 2:
        return "unchanged"
    good = (diff < 0) if lower_is_better else (diff > 0)
    sign = "+" if diff > 0 else ""
    return f"{sign}{diff:.{digits}f}{unit} {'better' if good else 'worse'}"


def print_telemetry(data: dict):
    a = data.get("adapter") or {}
    print(f"  SSID / BSSID       : {a.get('ssid', '—')} / {a.get('bssid', '—')}")
    print(f"  RSSI / quality     : {_fmt(a.get('signal_dbm'), ' dBm', 0)} / {a.get('signal_percent', '—')}%")
    print(f"  Band / channel     : {a.get('band', '—')} / {a.get('channel', '—')}")
    print(f"  PHY (rx / tx)      : {_fmt(a.get('receive_rate_mbps'), ' Mbps', 0)} / "
          f"{_fmt(a.get('transmit_rate_mbps'), ' Mbps', 0)}")
    print(f"  Security           : {a.get('authentication', '—')} / {a.get('cipher', '—')}")
    print(f"  Driver             : {a.get('description', '—')}")
    print(f"  Visible BSSes      : {data.get('bss_count', 0)}  bands={data.get('bands', [])}")
    print(f"  Administrator      : {data.get('admin')}")


def print_benchmark(b: dict, indent: str = "  "):
    if not b:
        print(f"{indent}(benchmark skipped)")
        return
    print(f"{indent}download           : {_fmt(b.get('throughput_mbps'), ' Mbps', 2)}")
    print(f"{indent}RTT median / p95   : {_fmt(b.get('latency_median_ms'), ' ms', 0)} / "
          f"{_fmt(b.get('latency_p95_ms'), ' ms', 0)}")
    print(f"{indent}jitter / loss      : {_fmt(b.get('jitter_ms'), ' ms', 0)} / "
          f"{_fmt(b.get('loss_pct'), ' %', 1)}")
    print(f"{indent}DNS                : {_fmt(b.get('dns_ms'), ' ms', 1)}")
    eff = b.get("efficiency")
    print(f"{indent}efficiency         : {_fmt(eff * 100 if eff is not None else None, '% of PHY', 0)}")
    if b.get("reps"):
        print(f"{indent}repetitions        : {b['reps']} (median shown)")
    if b.get("errors"):
        print(f"{indent}errors             : {b['errors']}")


def mode_before(args):
    print("=" * 72)
    print("BEFORE — baseline snapshot")
    print("=" * 72)
    data = capture(args, not args.no_benchmark)
    print_telemetry(data)
    print("\nMeasured link:")
    print_benchmark(data.get("benchmark"))
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print(f"\nSaved baseline to {STATE_FILE}")
    if not data.get("admin"):
        print("[!] Not elevated: NDIS/registry settings cannot be applied. "
              "Run the app as Administrator for the full set.")


def mode_after(args):
    print("=" * 72)
    print("AFTER — measurement and comparison")
    print("=" * 72)
    before = _read_json(STATE_FILE)
    if not before:
        print(f"No baseline at {STATE_FILE}. Run 'before' first.")
        return 2

    data = capture(args, not args.no_benchmark)
    print("Current link:")
    print_telemetry(data)
    print("\nCurrent measurement:")
    print_benchmark(data.get("benchmark"))

    print("\n" + "-" * 72)
    print("BEFORE → AFTER")
    print("-" * 72)
    ba = before.get("adapter") or {}
    aa = data.get("adapter") or {}
    for key, unit, digits, lower in (
        ("signal_dbm", " dBm", 0, False),
        ("signal_percent", "%", 0, False),
        ("channel", "", 0, False),
        ("receive_rate_mbps", " Mbps", 0, False),
        ("transmit_rate_mbps", " Mbps", 0, False),
    ):
        b = ba.get(key)
        a = aa.get(key)
        print(f"  {key:20s} {_fmt(b, unit, digits):>14s} → {_fmt(a, unit, digits):<14s} "
              f"{_delta(b, a, unit, digits, lower)}")

    bb = before.get("benchmark") or {}
    ab = data.get("benchmark") or {}
    if bb and ab:
        print()
        for key, unit, digits, lower in (
            ("throughput_mbps", " Mbps", 2, False),
            ("latency_median_ms", " ms", 0, True),
            ("latency_p95_ms", " ms", 0, True),
            ("jitter_ms", " ms", 0, True),
            ("loss_pct", " %", 1, True),
        ):
            b = bb.get(key)
            a = ab.get(key)
            print(f"  {key:20s} {_fmt(b, unit, digits):>14s} → {_fmt(a, unit, digits):<14s} "
                  f"{_delta(b, a, unit, digits, lower)}")
        be, ae = bb.get("efficiency"), ab.get("efficiency")
        print(f"  {'efficiency':20s} {_fmt(be*100 if be is not None else None, '%', 0):>14s} → "
              f"{_fmt(ae*100 if ae is not None else None, '%', 0):<14s}")
        print("\nNote: throughput and RTT are noisy. Treat a change smaller than "
              "~15% (or any single run) as inconclusive; repeat with --reps 3.")
    else:
        print("\n(No benchmark data — rerun without --no-benchmark to compare "
              "throughput and latency.)")

    print("\n" + "-" * 72)
    print("REGISTRY CHANGES RECORDED SINCE THE BASELINE")
    print("-" * 72)
    snap = _read_json(ROLLBACK_FILE)
    if not snap:
        print("  No registry snapshot (no changes, preview mode, or not elevated).")
    else:
        # The snapshot file is historical. Only entries written after the
        # baseline, for adapters that still exist, are relevant to this pair.
        try:
            baseline_epoch = datetime.fromisoformat(before["time"]).timestamp()
        except Exception:
            baseline_epoch = 0.0
        try:
            active = {a["subkey"] for a in adapter_manager.find_active_wifi_adapters()}
        except Exception:
            active = set()
        fresh = [
            e for e in snap
            if (not active or e.get("subkey") in active)
            and float(e.get("timestamp", 0) or 0) >= baseline_epoch - 5
        ]
        stale = len(snap) - len(fresh)
        if not fresh:
            print("  No new registry changes since the baseline "
                  f"({stale} historical/stale entr{'y' if stale == 1 else 'ies'} ignored).")
        else:
            print(f"  {len(fresh)} change(s); {stale} historical/stale ignored:")
            for e in fresh[-25:]:
                print(f"    [{e.get('subkey', '?')}] {e.get('property', '?')} = "
                      f"{e.get('new_value', '?')} (existed={e.get('existed')})")

    sys_snap = _read_json(SYSTEM_SNAPSHOT)
    if sys_snap:
        print("  System snapshot:")
        print(f"    DNS={sys_snap.get('dns_v4')}  "
              f"power={sys_snap.get('wireless_power')}  "
              f"TCP={sys_snap.get('tcp')}")

    return 0


def main():
    parser = argparse.ArgumentParser(description="Measure before/after Wi-Fi changes")
    parser.add_argument("mode", choices=["before", "after"])
    parser.add_argument("--bytes", type=int, default=8_000_000)
    parser.add_argument("--samples", type=int, default=20)
    parser.add_argument("--reps", type=int, default=1)
    parser.add_argument("--no-benchmark", action="store_true")
    args = parser.parse_args()

    if args.mode == "before":
        mode_before(args)
        return 0
    return mode_after(args)


if __name__ == "__main__":
    sys.exit(main())
