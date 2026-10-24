"""Verify resolve_intent_code against the REAL enum labels discovered on this PC."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from core.adapter_manager import resolve_intent_code

# Real Intel AX201 enums
intel = {
    "transmit_power": ("enum", [("0", "1. Lowest"), ("25", "2. Medium-low"), ("50", "3. Medium"),
                                 ("75", "4. Medium-High"), ("100", "5. Highest")]),
    "roaming_aggressiveness": ("enum", [("0", "1. Lowest"), ("1", "2. Medium-low"), ("2", "3. Medium"),
                                         ("3", "4. Medium-High"), ("4", "5. Highest")]),
    "preferred_band": ("enum", [("0", "1. No Preference"), ("1", "2. Prefer 2.4GHz band"),
                                ("2", "3. Prefer 5GHz band")]),
    "mimo_power_save": ("enum", [("0", "Auto SMPS"), ("1", "Static SMPS"), ("2", "Dynamic SMPS"),
                                 ("3", "No SMPS")]),
    "sleep_on_disconnect": ("enum", [("0", "Disabled"), ("1", "Enabled")]),
    "packet_coalescing": ("enum", [("0", "Disabled"), ("1", "Enabled")]),
}
# Real Realtek RTL8822BE enums (note inverted codes)
realtek = {
    "transmit_power": ("enum", [("0", "5. Highest"), ("1", "4. Medium-high"), ("2", "3. Medium"),
                                ("3", "2. Medium-low"), ("4", "1. Lowest")]),
    "roaming_aggressiveness": ("enum", [("65", "6. Highest"), ("70", "5. Medium-High"), ("75", "4. Medium"),
                                        ("80", "3. Medium-Low"), ("85", "2. Lowest"), ("127", "1. Disable")]),
    "preferred_band": ("enum", [("0", "1. No Preference"), ("1", "2. 2.4G first"), ("2", "3. 5G first")]),
    "sleep_on_disconnect": ("enum", [("1", "Disabled"), ("0", "Enabled")]),
}

def info(t):
    return {"type": t[0], "enums": [{"code": c, "label": l} for c, l in t[1]]}

print("=== INTEL AX201 ===")
for intent, sem, expect in [
    ("transmit_power", "max", "100"), ("transmit_power", "min", "0"),
    ("roaming_aggressiveness", "max", "4"), ("roaming_aggressiveness", "min", "0"),
    ("preferred_band", "5GHz", "2"), ("preferred_band", "2.4GHz", "1"), ("preferred_band", "auto", "0"),
    ("mimo_power_save", "disable", "3"),
    ("sleep_on_disconnect", "disable", "0"),
    ("packet_coalescing", "off", "0"), ("packet_coalescing", "on", "1"),
]:
    ok, code, reason = resolve_intent_code(intent, sem, info(intel[intent]))
    mark = "OK " if (ok and code == expect) else "FAIL"
    print(f"  [{mark}] {intent:24s} {sem:8s} -> code={code:>4s} (expected {expect})  {reason}")

print("=== REALTEK RTL8822BE ===")
for intent, sem, expect in [
    ("transmit_power", "max", "0"), ("transmit_power", "min", "4"),
    ("roaming_aggressiveness", "max", "65"), ("roaming_aggressiveness", "min", "85"),
    ("preferred_band", "5GHz", "2"), ("preferred_band", "2.4GHz", "1"), ("preferred_band", "auto", "0"),
    ("sleep_on_disconnect", "disable", "1"),
]:
    ok, code, reason = resolve_intent_code(intent, sem, info(realtek[intent]))
    mark = "OK " if (ok and code == expect) else "FAIL"
    print(f"  [{mark}] {intent:24s} {sem:8s} -> code={code:>4s} (expected {expect})  {reason}")
