"""
WiFi Optimizer - Benchmark CLI
==============================

Measure the current Wi-Fi link honestly:

    python scripts/benchmark.py --label before --reps 3
    ... change settings / move the laptop ...
    python scripts/benchmark.py --label after --reps 3

Metrics: single-stream download goodput, TCP-connect RTT (median/p95), jitter,
loss, DNS time, RSSI, negotiated PHY rate, and goodput/PHY efficiency.

Each result is appended to benchmarks.jsonl in the project folder. Compare
medians, not single samples. Nothing is uploaded.
"""

import argparse
import json
import os
import sys

for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core import benchmark  # noqa: E402


def _fmt(value, unit="", digits=1):
    if value is None:
        return "—"
    try:
        return f"{float(value):.{digits}f}{unit}"
    except (TypeError, ValueError):
        return str(value)


def main():
    parser = argparse.ArgumentParser(description="Measure real Wi-Fi link quality")
    parser.add_argument("--label", default="", help="tag stored with the result")
    parser.add_argument("--reps", type=int, default=1,
                        help="repetitions; medians are reported (default 1)")
    parser.add_argument("--bytes", type=int, default=8_000_000,
                        help="download bytes per sample (default 8,000,000)")
    parser.add_argument("--samples", type=int, default=20,
                        help="TCP-connect latency samples (default 20)")
    parser.add_argument("--url", default=None,
                        help="override the download URL ({bytes} is substituted)")
    parser.add_argument("--json", action="store_true", help="print JSON only")
    parser.add_argument("--no-save", action="store_true", help="do not append to benchmarks.jsonl")
    args = parser.parse_args()

    if args.reps > 1:
        result = benchmark.measure_median(
            reps=args.reps, download_bytes=args.bytes,
            latency_samples=args.samples, url=args.url)
        record = dict(result)
        record["label"] = args.label
        summary = (f"{_fmt(record.get('throughput_mbps'), ' Mbps', 2)} down | "
                   f"RTT med {_fmt(record.get('latency_median_ms'), ' ms', 0)} / "
                   f"p95 {_fmt(record.get('latency_p95_ms'), ' ms', 0)} | "
                   f"jitter {_fmt(record.get('jitter_ms'), ' ms', 0)} | "
                   f"loss {_fmt(record.get('loss_pct'), '%', 1)} | "
                   f"RSSI {_fmt(record.get('rssi_dbm'), ' dBm', 0)}")
    else:
        res = benchmark.measure(
            download_bytes=args.bytes, latency_samples=args.samples, url=args.url)
        record = res.to_dict()
        record["label"] = args.label
        summary = res.summary()

    if args.json:
        print(json.dumps(record, indent=2, ensure_ascii=False))
    else:
        print(summary)
        eff = record.get("efficiency")
        if eff is not None:
            print(f"PHY efficiency : {eff * 100:.0f}% of the lower negotiated rate")
        if record.get("ssid"):
            print(f"Link           : {record.get('ssid')} / {record.get('bssid')} "
                  f"ch {record.get('channel')} ({record.get('band')})")
        if record.get("errors"):
            print(f"Errors         : {record['errors']}")

    if not args.no_save:
        try:
            with open(benchmark.RESULTS_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            if not args.json:
                print(f"Saved to      : {benchmark.RESULTS_FILE}")
        except OSError as e:
            print(f"Could not save: {e}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
