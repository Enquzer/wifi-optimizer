"""Simulate an UNKNOWN vendor chipset (e.g. Qualcomm/MediaTek/Broadcom) whose
registry keyword names differ from Intel/Realtek, but whose driver labels are
recognisable. Verifies the ParamDesc fallback resolves them safely."""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from core.adapter_manager import (_match_intent_keyword, resolve_intent_code,
                                  INTENT_LABELS)

def enum(*pairs):
    return [{"code": str(c), "label": l} for c, l in pairs]


# Hypothetical foreign vendor: alien keyword names, normal human labels
foreign = {
    "QcaBandPref":       {"type": "enum", "desc": "Preferred Band",
                          "enums": enum(("0", "No Preference"), ("1", "2.4 GHz"), ("2", "5 GHz"))},
    "QcaRoamLevel":      {"type": "enum", "desc": "Roaming Aggressiveness",
                          "enums": enum(("0", "Low"), ("1", "Medium"), ("2", "High"))},
    "QcaTxPowerPct":     {"type": "enum", "desc": "Transmit Power",
                          "enums": enum(("10", "10%"), ("50", "50%"), ("100", "100%"))},
    "QcaMimoPs":         {"type": "enum", "desc": "MIMO Power Save",
                          "enums": enum(("0", "Auto SMPS"), ("1", "Dynamic SMPS"), ("2", "No SMPS"))},
    "QcaSleepDisc":      {"type": "enum", "desc": "Sleep on Disconnect",
                          "enums": enum(("0", "Disabled"), ("1", "Enabled"))},
    "QcaBurst":          {"type": "enum", "desc": "Frame Bursting",
                          "enums": enum(("0", "Disabled"), ("1", "Enabled"))},
    "QcaBgScan":         {"type": "enum", "desc": "Background Scanning",
                          "enums": enum(("0", "Disabled"), ("1", "Enabled"))},
}

CASES = [
    ("preferred_band", "5GHz", "2"),
    ("preferred_band", "2.4GHz", "1"),
    ("roaming_aggressiveness", "max", "2"),
    ("roaming_aggressiveness", "min", "0"),
    ("transmit_power", "max", "100"),
    ("mimo_power_save", "disable", "2"),
    ("sleep_on_disconnect", "disable", "0"),
    ("throughput_booster", "on", "1"),
    ("background_scanning", "on", "1"),
]

print("=" * 74)
print("UNKNOWN-VENDOR FALLBACK (match by driver label, not keyword name)")
print("=" * 74)
for intent, sem, expect in CASES:
    kw = _match_intent_keyword(intent, foreign)
    if not kw:
        print(f"  [SKIP] {intent:24s} {sem:8s}  no label match")
        continue
    ok, code, reason = resolve_intent_code(intent, sem, foreign[kw])
    mark = "OK  " if (ok and code == expect) else "FAIL"
    print(f"  [{mark}] {INTENT_LABELS[intent]:24s} {sem:8s} -> {kw:16s} code={code:>4s} "
          f"(exp {expect:>4s})  {reason}")

# Safety: a vendor whose labels DON'T match must be skipped, never guessed
print("\nUnknown labels must be skipped (safe):")
weird = {"Xyzzy": {"type": "enum", "desc": "Frobnicator Level",
                   "enums": enum(("0", "Off"), ("1", "On"))}}
for intent in ["transmit_power", "roaming_aggressiveness", "preferred_band"]:
    kw = _match_intent_keyword(intent, weird)
    print(f"  {intent:24s} -> {'UNEXPECTED ' + str(kw) if kw else 'skipped (None) OK'}")
