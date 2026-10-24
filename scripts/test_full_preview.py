"""End-to-end: run the FULL optimizer in preview mode and confirm nothing is written."""
import os, sys, winreg
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from core.optimizer import run_optimization
from core import adapter_manager as am

NETKEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"


def read_val(subkey, prop):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{NETKEY}\\{subkey}", 0, winreg.KEY_READ) as k:
                return winreg.QueryValueEx(k, prop)[0]
    except FileNotFoundError:
        return None


WATCH_PROPS = ["RoamingPreferredBandType", "IbssTxPower", "RoamAggressiveness",
               "MIMOPowerSaveMode", "ThroughputBoosterEnabled", "*PacketCoalescing",
               "PreferBand", "TxPwrLevel", "RegROAMSensitiveLevel", "WakeOnDisconnect"]
before = {}
for ad in am.find_active_wifi_adapters():
    before[ad["subkey"]] = {p: read_val(ad["subkey"], p) for p in WATCH_PROPS}

print("=" * 72)
print("FULL OPTIMIZER PREVIEW RUN (profile=far_range)")
print("=" * 72)
result = run_optimization(profile_key="far_range", adapter_name="Wi-Fi", preview=True)

print(f"\nPreview changes: {len(result.preview_changes)}")
for c in result.preview_changes:
    flag = "CHANGE" if c["changed"] else "same  "
    print(f"  [{flag}] {c['adapter'][:34]:34s} {c['intent']:22s} {c['property']:26s} "
          f"{str(c['current']):>8s} -> {c['proposed']}")

print("\nPost-preview registry check:")
ok = True
for sk, props in before.items():
    for p, bv in props.items():
        av = read_val(sk, p)
        if av != bv:
            ok = False
            print(f"  [{sk}] {p}: {bv} -> {av}  CHANGED!!")
print("  all unchanged" if ok else "  REGISTRY MODIFIED")
print("\nRESULT:", "PASS" if ok else "FAIL")
