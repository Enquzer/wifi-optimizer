"""
WiFi Optimizer - Adapter Manager Module
Applies optimized settings to the Windows WiFi adapter via netsh and registry.
"""

import subprocess
import logging
import winreg
import ctypes
import sys
from typing import Optional

logger = logging.getLogger(__name__)

# Registry keys for WiFi adapter properties
NETWORK_ADAPTERS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"

# Common WiFi adapter property IDs (vendor-neutral)
PROP_ROAMING_AGGRESSIVENESS = "*RoamAggressiveness"
PROP_TRANSMIT_POWER = "*TransmitPower"
PROP_PREFERRED_BAND = "*PreferredBand"
PROP_SLEEP_ON_DISCONNECT = "*SleepOnDisconnect"
PROP_BGSCANNING = "*BgScanningEnabled"
PROP_MIMO = "*MIMOPowerSave"
PROP_BSS_TRANSITION = "*BSSTransition"
PROP_802_11_MODE = "*WirelessMode"


def is_admin() -> bool:
    """Check if the process is running as administrator."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_netsh(args: list, check: bool = False) -> tuple[bool, str]:
    """Run a netsh command, return (success, output)."""
    try:
        result = subprocess.run(
            ["netsh"] + args,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        success = result.returncode == 0
        output = result.stdout + result.stderr
        return success, output
    except Exception as e:
        logger.error("netsh error: %s", e)
        return False, str(e)


def set_preferred_band(adapter_name: str, band: str = "5GHz") -> bool:
    """
    Set the preferred WiFi band (5GHz for speed, 2.4GHz for range).
    Uses netsh wlan set profileparameter (per-profile) or registry.
    """
    # Map band to netsh values
    band_map = {
        "5GHz": "5GHz",
        "2.4GHz": "2.4GHz",
        "Auto": "Auto"
    }
    target = band_map.get(band, "Auto")
    logger.info("Setting preferred band to %s", target)

    # Use registry approach for adapter-level setting
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETWORK_ADAPTERS_KEY, 0,
                            winreg.KEY_READ) as base_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(base_key, i)
                    if subkey_name.startswith("0"):
                        with winreg.OpenKey(base_key, subkey_name, 0,
                                            winreg.KEY_READ | winreg.KEY_WRITE) as subkey:
                            try:
                                desc = winreg.QueryValueEx(subkey, "DriverDesc")[0]
                                if "wireless" in desc.lower() or "wifi" in desc.lower() or "wi-fi" in desc.lower() or "802.11" in desc.lower() or "wlan" in desc.lower():
                                    # Try to set preferred band
                                    band_value = "2" if band == "5GHz" else "1" if band == "2.4GHz" else "0"
                                    try:
                                        winreg.SetValueEx(subkey, "*PreferredBand", 0, winreg.REG_SZ, band_value)
                                        logger.info("Set preferred band for adapter: %s", desc)
                                    except PermissionError:
                                        logger.warning("No permission to set band for %s", desc)
                            except FileNotFoundError:
                                pass
                    i += 1
                except OSError:
                    break
        return True
    except Exception as e:
        logger.error("Failed to set preferred band: %s", e)
        return False


def set_roaming_aggressiveness(level: int = 3) -> bool:
    """
    Set roaming aggressiveness.
    Level: 1=Lowest, 2=Medium-Low, 3=Medium, 4=Medium-High, 5=Highest
    Higher = roam more aggressively (good for movement / weak signal)
    Lower = stay connected longer (good for stationary use / stability)
    """
    logger.info("Setting roaming aggressiveness to level %d", level)
    value = str(min(5, max(1, level)))
    return _set_adapter_registry_value("*RoamAggressiveness", value)


def set_transmit_power(level: int = 5) -> bool:
    """
    Set transmit power level.
    Level: 1=Lowest, 2=Low, 3=Medium, 4=High, 5=Highest
    """
    logger.info("Setting transmit power to level %d", level)
    value = str(min(5, max(1, level)))
    return _set_adapter_registry_value("*TransmitPower", value)


def disable_sleep_on_disconnect(disable: bool = True) -> bool:
    """Disable sleep on disconnect for maximum connectivity."""
    value = "0" if disable else "1"
    return _set_adapter_registry_value("*SleepOnDisconnect", value)


def set_background_scanning(enabled: bool = True) -> bool:
    """Enable/disable background scanning for roaming decisions."""
    value = "1" if enabled else "0"
    return _set_adapter_registry_value("*BgScanningEnabled", value)


def set_power_saving(disable: bool = True) -> bool:
    """
    Disable power saving mode for maximum performance.
    Uses netsh wlan set autoconfig and power management.
    """
    try:
        # Set wireless power plan to max performance
        success, _ = run_netsh(["wlan", "set", "autoconfig", "enabled=yes", "interface=Wi-Fi"])
        # Also set via powercfg
        subprocess.run(
            ["powercfg", "/setacvalueindex", "SCHEME_CURRENT", "19caa947-ffe4-4a1e-a9a6-5d6ecab12b5a",
             "12bbebe6-58d6-4636-95bb-3217ef867c1a", "000"],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        subprocess.run(
            ["powercfg", "/setactive", "SCHEME_CURRENT"],
            capture_output=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return True
    except Exception as e:
        logger.error("Failed to set power mode: %s", e)
        return False


def optimize_tcp_settings() -> bool:
    """Apply optimal TCP/IP settings for WiFi throughput."""
    try:
        cmds = [
            ["interface", "tcp", "set", "global", "autotuninglevel=normal"],
            ["interface", "tcp", "set", "global", "rss=enabled"],
            ["interface", "tcp", "set", "global", "chimney=disabled"],
            ["interface", "tcp", "set", "global", "ecncapability=enabled"],
            ["interface", "tcp", "set", "global", "timestamps=disabled"],
            ["interface", "tcp", "set", "global", "dca=enabled"],
            ["interface", "tcp", "set", "global", "netdma=disabled"],
        ]
        all_ok = True
        for cmd in cmds:
            ok, out = run_netsh(cmd)
            if not ok:
                logger.warning("TCP setting failed: %s — %s", cmd, out)
                all_ok = False
        return all_ok
    except Exception as e:
        logger.error("Failed to optimize TCP: %s", e)
        return False


def flush_dns() -> bool:
    """Flush DNS cache for fresh resolution."""
    try:
        result = subprocess.run(
            ["ipconfig", "/flushdns"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return result.returncode == 0
    except Exception as e:
        logger.error("DNS flush failed: %s", e)
        return False


def set_dns_servers(primary: str = "8.8.8.8", secondary: str = "8.8.4.4",
                    adapter_name: str = "Wi-Fi") -> bool:
    """Set DNS servers to fast public DNS."""
    try:
        ok1, _ = run_netsh(["interface", "ip", "set", "dns", f"name={adapter_name}",
                             "static", primary, "primary"])
        ok2, _ = run_netsh(["interface", "ip", "add", "dns", f"name={adapter_name}",
                             secondary, "index=2"])
        return ok1 or ok2
    except Exception as e:
        logger.error("Failed to set DNS: %s", e)
        return False


def reset_wifi_adapter(adapter_name: str = "Wi-Fi") -> bool:
    """Reset WiFi adapter to clear stuck states."""
    try:
        subprocess.run(["netsh", "wlan", "disconnect"], capture_output=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        run_netsh(["interface", "set", "interface", adapter_name, "disable"])
        import time; time.sleep(1)
        run_netsh(["interface", "set", "interface", adapter_name, "enable"])
        return True
    except Exception as e:
        logger.error("Failed to reset adapter: %s", e)
        return False


def get_adapter_properties() -> dict:
    """Read current adapter registry properties."""
    props = {}
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETWORK_ADAPTERS_KEY, 0,
                            winreg.KEY_READ) as base_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(base_key, i)
                    if subkey_name.startswith("0"):
                        try:
                            with winreg.OpenKey(base_key, subkey_name, 0,
                                                winreg.KEY_READ) as subkey:
                                try:
                                    desc = winreg.QueryValueEx(subkey, "DriverDesc")[0]
                                    if any(k in desc.lower() for k in
                                           ["wireless", "wifi", "wi-fi", "802.11", "wlan"]):
                                        prop_names = [
                                            "*RoamAggressiveness", "*TransmitPower",
                                            "*PreferredBand", "*SleepOnDisconnect",
                                            "*BgScanningEnabled", "*MIMOPowerSave",
                                            "*WirelessMode"
                                        ]
                                        adapter_props = {"name": desc}
                                        for prop in prop_names:
                                            try:
                                                val = winreg.QueryValueEx(subkey, prop)[0]
                                                adapter_props[prop] = val
                                            except FileNotFoundError:
                                                pass
                                        props[desc] = adapter_props
                                except FileNotFoundError:
                                    pass
                        except PermissionError:
                            pass
                    i += 1
                except OSError:
                    break
    except Exception as e:
        logger.error("Failed to read adapter properties: %s", e)
def apply_selfish_airtime_boost() -> list:
    """
    Applies aggressive client-side priority optimizations to force the adapter to claim maximum
    channel airtime, disable power constraints, enable short guard intervals, and prioritize traffic.
    """
    applied = []
    # 1. Enable Short Guard Interval (cuts inter-symbol idle gap in half -> +11% raw PHY throughput)
    for prop in ["*ShortGI20MHz", "*ShortGI40MHz", "*ShortGI80MHz"]:
        if _set_adapter_registry_value(prop, "1"):
            applied.append(f"{prop.replace('*', '')} -> Enabled (reduced inter-symbol dead time)")

    # 2. Enable 802.11e WMM / QoS packet priority tagging
    if _set_adapter_registry_value("*PriorityVLANTag", "3"):
        applied.append("802.11e QoS / Priority -> Enabled (cuts in front of other devices)")

    # 3. Enable Throughput Booster / Packet Bursting
    for prop in ["*ThroughputBoosterEnabled", "*ThroughputBooster"]:
        if _set_adapter_registry_value(prop, "1"):
            applied.append("Throughput Booster -> Enabled (continuous airtime bursting)")

    # 4. Disable Packet Coalescing (Eliminate artificial latency buffers)
    if _set_adapter_registry_value("*PacketCoalescing", "0"):
        applied.append("Packet Coalescing -> Disabled (instant frame dispatch)")

    # 5. Enable TCP & IP Task Offloading
    run_netsh(["interface", "ip", "set", "global", "taskoffload=enabled"])
    run_netsh(["interface", "ip", "set", "global", "neighborcachelimit=4096"])
    applied.append("Kernel Task Offload -> Enabled (hardware-accelerated packet pipeline)")

    return applied


def _set_adapter_registry_value(prop_name: str, value: str) -> bool:
    """Set a registry value on all wireless adapters found in registry."""
    success_count = 0
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETWORK_ADAPTERS_KEY, 0,
                            winreg.KEY_READ) as base_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(base_key, i)
                    if subkey_name.startswith("0"):
                        try:
                            with winreg.OpenKey(base_key, subkey_name, 0,
                                                winreg.KEY_READ | winreg.KEY_WRITE) as subkey:
                                try:
                                    desc = winreg.QueryValueEx(subkey, "DriverDesc")[0]
                                    if any(k in desc.lower() for k in
                                           ["wireless", "wifi", "wi-fi", "802.11", "wlan"]):
                                        winreg.SetValueEx(subkey, prop_name, 0, winreg.REG_SZ, value)
                                        logger.info("Set %s=%s for %s", prop_name, value, desc)
                                        success_count += 1
                                except (FileNotFoundError, PermissionError) as e:
                                    logger.warning("Could not set %s: %s", prop_name, e)
                        except PermissionError:
                            pass
                    i += 1
                except OSError:
                    break
        return success_count > 0
    except Exception as e:
        logger.error("Registry error setting %s: %s", prop_name, e)
        return False
