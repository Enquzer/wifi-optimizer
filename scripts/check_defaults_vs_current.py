"""Compare current registry values against the driver-declared DEFAULTS in Ndi\\params.
Tells us whether 'changed=False' means 'already optimized' or 'default already matches'."""
import os, sys, winreg
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from core import adapter_manager as am

NETKEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"


def current(subkey, prop):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, f"{NETKEY}\\{subkey}", 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, prop)[0]
    except FileNotFoundError:
        return None


def default_of(subkey, prop):
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                            f"{NETKEY}\\{subkey}\\Ndi\\params\\{prop}", 0, winreg.KEY_READ) as k:
            return winreg.QueryValueEx(k, "default")[0]
    except FileNotFoundError:
        return None


PROPS = ["PreferBand", "RoamingPreferredBandType", "TxPwrLevel", "IbssTxPower",
         "RegROAMSensitiveLevel", "RoamAggressiveness", "WakeOnDisconnect",
         "*DeviceSleepOnDisconnect", "MIMOPowerSaveMode", "ThroughputBoosterEnabled",
         "*PacketCoalescing"]

print(f"{'subkey':6s} {'property':26s} {'current':>10s} {'default':>10s}  source")
print("-" * 70)
for ad in am.find_active_wifi_adapters():
    sk = ad["subkey"]
    for p in PROPS:
        c = current(sk, p)
        if c is None:
            continue
        d = default_of(sk, p)
        src = "DRIVER DEFAULT" if str(c) == str(d) else ("optimizer-applied" if d is not None else "?")
        print(f"{sk:6s} {p:26s} {str(c):>10s} {str(d):>10s}  {src}")
