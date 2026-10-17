"""
WiFi Optimizer - Independent Verification Script
================================================
Run this BEFORE and AFTER clicking OPTIMIZE in the GUI to objectively
compare your connection metrics. It reads the same Windows telemetry
(netsh + native RSSI) that the optimizer uses, so you can verify
real changes vs. faked numbers.

Usage:
    python scripts\verify_optimization.py before
    ...then run OPTIMIZE in the GUI...
    python scripts\verify_optimization.py after

It prints a side-by-side comparison and checks the optimizer log
and rollback snapshot to confirm which settings were actually applied.
"""

import sys
import os
import json
import subprocess
import ctypes
from datetime import datetime

# Ensure src is importable so we reuse the tool's own scanner logic
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

LOG_FILE = os.path.join(BASE_DIR, "wifi_optimizer.log")
SNAPSHOT_FILE = os.path.join(SRC_DIR, "core", "adapter_rollback_snapshot.json")


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def run(cmd):
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15,
                           creationflags=subprocess.CREATE_NO_WINDOW
                           if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        return r.stdout.strip(), r.returncode == 0
    except Exception as e:
        return str(e), False


def capture_netsh_interfaces():
    out, ok = run(["netsh", "wlan", "show", "interfaces"])
    return out, ok


def capture_netsh_networks():
    out, ok = run(["netsh", "wlan", "show", "networks", "mode=bssid"])
    return out, ok


def parse_interface_block(text):
    """Pull key fields out of 'netsh wlan show interfaces' output."""
    fields = {}
    for line in text.splitlines():
        ls = line.strip()
        if ":" not in ls:
            continue
        key, _, val = ls.partition(":")
        key = key.strip().lower()
        val = val.strip()
        mapping = {
            "name": "name",
            "description": "description",
            "ssid": "ssid",
            "bssid": "bssid",
            "network type": "network_type",
            "authentication": "authentication",
            "cipher": "cipher",
            "connection mode": "connection_mode",
            "channel": "channel",
            "receive rate": "receive_rate_mbps",
            "transmit rate": "transmit_rate_mbps",
            "signal": "signal_percent",
            "profile": "profile",
            "radio type": "radio_type",
        }
        if key in mapping:
            fields[mapping[key]] = val
    return fields


def read_tail_log(lines=30):
    if not os.path.exists(LOG_FILE):
        return None
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            all_lines = f.readlines()
        return "".join(all_lines[-lines:])
    except Exception as e:
        return f"(could not read log: {e})"


def read_rollback_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return None
    try:
        with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        return f"(could not read snapshot: {e})"


def print_section(title):
    print(f"\n{'='*70}\n{title}\n{'='*70}")


def mode_before():
    print_section("BEFORE OPTIMIZATION SNAPSHOT")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Running as Administrator: {is_admin()}")
    if not is_admin():
        print("  [!] WARNING: Not running as Admin. Registry/hardware settings will NOT apply.")
        print("      Only DNS flush / partial TCP will work. Right-click the app -> 'Run as Administrator'.")

    out, ok = capture_netsh_interfaces()
    print("\n--- netsh wlan show interfaces ---")
    print(out if out else "(no output)")

    fields = parse_interface_block(out)
    print("\n--- Parsed key metrics ---")
    for k in ["ssid", "channel", "receive_rate_mbps", "transmit_rate_mbps",
              "signal_percent", "authentication", "radio_type", "profile"]:
        print(f"  {k}: {fields.get(k, '—')}")

    out2, _ = capture_netsh_networks()
    net_count = out2.count("SSID")
    print(f"\nVisible networks (SSID count): {net_count}")

    # Save to file so 'after' mode can compare
    data = {
        "time": datetime.now().isoformat(),
        "admin": is_admin(),
        "interfaces": out,
        "networks_count": net_count,
        "parsed": fields,
    }
    with os.fdopen(os.open(os.path.join(BASE_DIR, "verify_before.json"),
                           os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644), "w") as f:
        json.dump(data, f, indent=2)
    print("\nSaved baseline to verify_before.json")


def mode_after():
    print_section("AFTER OPTIMIZATION SNAPSHOT")
    print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Running as Administrator: {is_admin()}")

    # Load before baseline
    before_path = os.path.join(BASE_DIR, "verify_before.json")
    before = None
    if os.path.exists(before_path):
        try:
            with open(before_path, "r", encoding="utf-8") as f:
                before = json.load(f)
        except Exception:
            pass

    out, ok = capture_netsh_interfaces()
    print("\n--- netsh wlan show interfaces ---")
    print(out if out else "(no output)")
    fields = parse_interface_block(out)

    print("\n--- Before vs After Comparison ---")
    if before:
        bf = before.get("parsed", {})
        keys = ["ssid", "channel", "receive_rate_mbps", "transmit_rate_mbps",
                "signal_percent", "authentication", "radio_type"]
        changed_any = False
        for k in keys:
            b = bf.get(k, "—")
            a = fields.get(k, "—")
            marker = "  <CHANGED>" if str(b) != str(a) else ""
            if marker:
                changed_any = True
            print(f"  {k:24s} before={str(b):18s} after={str(a):18s}{marker}")
        if not changed_any:
            print("  [No metric changed in netsh output.]")
    else:
        print("  (No baseline found. Run 'before' mode first.)")
        for k in ["ssid", "channel", "receive_rate_mbps", "transmit_rate_mbps",
                  "signal_percent", "authentication", "radio_type"]:
            print(f"  {k}: {fields.get(k, '—')}")

    # Check optimizer log
    print_section("OPTIMIZER LOG (tail)")
    log = read_tail_log(40)
    print(log if log else "(no log file found)")

    # Check rollback snapshot
    print_section("ROLLBACK SNAPSHOT (registry keys actually touched)")
    snap = read_rollback_snapshot()
    if snap is None:
        print("(no snapshot file — either no changes applied, dry-run, or not admin)")
    elif isinstance(snap, str):
        print(snap)
    elif not snap:
        print("(snapshot is empty — no registry keys were modified)")
    else:
        print(f"Total entries: {len(snap)}")
        for e in snap[-20:]:
            print(f"  [{e.get('subkey','?')}] {e.get('property','?')} = {e.get('new_value','?')} "
                  f"(existed_before={e.get('existed')})")

    out2, _ = capture_netsh_networks()
    net_count = out2.count("SSID")
    print(f"\nVisible networks (SSID count): {net_count}")


def main():
    if len(sys.argv) < 2 or sys.argv[1].lower() not in ("before", "after"):
        print("Usage: python scripts\\verify_optimization.py [before|after]")
        print("\nSteps:")
        print("  1. Run this script with 'before'")
        print("  2. Open WiFi Optimizer (as Administrator) and click OPTIMIZE NOW")
        print("  3. Run this script again with 'after' to compare")
        sys.exit(1)

    if sys.argv[1].lower() == "before":
        mode_before()
    else:
        mode_after()


if __name__ == "__main__":
    main()
