"""Verify preview mode: reports real current -> proposed, and writes NOTHING."""
import os, sys, winreg
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from core import adapter_manager as am

NETKEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"


def read_val(subkey, prop):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{NETKEY}\\{subkey}", 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, prop)[0]
    except FileNotFoundError:
        return None


# Snapshot the exact keys we are about to "change"
WATCH = [(a["subkey"], "RoamingPreferredBandType", "IbssTxPower", "RoamAggressiveness")
         for a in am.find_active_wifi_adapters()]
before = {sk: {p: read_val(sk, p) for p in props} for sk, *props in WATCH}

print("=" * 72)
print("PREVIEW MODE TEST (must write nothing)")
print("=" * 72)

am.set_preview_mode(True)
applied = []
for intent, sem in [("preferred_band", "5GHz"), ("transmit_power", "max"),
                    ("roaming_aggressiveness", "max"), ("mimo_power_save", "disable"),
                    ("sleep_on_disconnect", "disable"), ("throughput_booster", "on"),
                    ("packet_coalescing", "off")]:
    applied.append((intent, sem, am.apply_intent(intent, sem)))

changes = am.get_preview_changes()
am.set_preview_mode(False)

print(f"\nRecorded {len(changes)} proposed change(s):")
for c in changes:
    print(f"  [{c['subkey']}] {c['intent']:24s} {c['property']:28s} "
          f"{str(c['current']):>8s} -> {c['proposed']:>8s}  changed={c['changed']}")

print("\nRegistry re-read AFTER preview (must be identical to before):")
ok = True
for sk, *props in WATCH:
    for p in props:
        after = read_val(sk, p)
        before_v = before[sk][p]
        same = (after == before_v)
        ok = ok and same
        if before_v is not None or after is not None:
            print(f"  [{sk}] {p:26s} before={str(before_v):>8s} after={str(after):>8s} "
                  f"{'OK' if same else 'CHANGED!!'}")

print("\nRESULT:", "PASS - nothing written" if ok else "FAIL - registry was modified!")
