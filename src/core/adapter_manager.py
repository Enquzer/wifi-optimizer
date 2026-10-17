"""
WiFi Optimizer - Adapter Manager Module
Applies validated, discovery-based settings to the Windows WiFi adapter.
Includes dry-run modes, atomic rollback snapshots, and tiered intervention safety.
"""

import subprocess
import logging
import winreg
import ctypes
import os
import json
import time
import re
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)

# Registry keys for WiFi adapter properties
NETWORK_ADAPTERS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"
SNAPSHOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter_rollback_snapshot.json")

# Common WiFi adapter property IDs (standard NDIS 6.x keywords)
PROP_ROAMING_AGGRESSIVENESS = "*RoamAggressiveness"
PROP_TRANSMIT_POWER = "*TransmitPower"
PROP_PREFERRED_BAND = "*PreferredBand"
PROP_SLEEP_ON_DISCONNECT = "*SleepOnDisconnect"
PROP_BGSCANNING = "*BgScanningEnabled"
PROP_MIMO = "*MIMOPowerSave"
PROP_BSS_TRANSITION = "*BSSTransition"
PROP_802_11_MODE = "*WirelessMode"

# Global dry-run flag
_DRY_RUN: bool = False
_MOCK_REGISTRY: Dict[str, Dict[str, Any]] = {}
_SESSION_SNAPSHOTS: List[Dict[str, Any]] = []


def set_dry_run_mode(enabled: bool):
    """Enable or disable dry-run mode (prevents actual system mutations)."""
    global _DRY_RUN
    _DRY_RUN = enabled
    logger.info("AdapterManager dry-run mode: %s", "ENABLED" if enabled else "DISABLED")


def is_dry_run_mode() -> bool:
    """Check if dry-run mode is currently enabled."""
    return _DRY_RUN


def is_admin() -> bool:
    """Check if the process is running as administrator."""
    if _DRY_RUN:
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def run_netsh(args: list, check: bool = False) -> Tuple[bool, str]:
    """Run a netsh command safely, returning (success, output)."""
    if _DRY_RUN:
        logger.info("[DRY-RUN] netsh %s", " ".join(args))
        return True, "[DRY-RUN] Success"

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


def find_active_wifi_adapters(target_guid: Optional[str] = None) -> List[Dict[str, str]]:
    """
    Locate active wireless network adapter keys in the registry.
    Matches via NetCfgInstanceId (GUID) or valid wireless DriverDesc.
    """
    if _DRY_RUN and _MOCK_REGISTRY:
        return [{"subkey": k, "desc": v.get("DriverDesc", "Mock WiFi"), "guid": v.get("NetCfgInstanceId", "")}
                for k, v in _MOCK_REGISTRY.items()]

    adapters = []
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETWORK_ADAPTERS_KEY, 0, winreg.KEY_READ) as base_key:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(base_key, i)
                    i += 1
                    if not subkey_name.isdigit():
                        continue
                    try:
                        with winreg.OpenKey(base_key, subkey_name, 0, winreg.KEY_READ) as subkey:
                            desc = ""
                            try:
                                desc = winreg.QueryValueEx(subkey, "DriverDesc")[0]
                            except FileNotFoundError:
                                continue

                            guid = ""
                            try:
                                guid = winreg.QueryValueEx(subkey, "NetCfgInstanceId")[0]
                            except FileNotFoundError:
                                pass

                            # Check for wireless signatures, excluding virtual adapters
                            is_wireless = any(k in desc.lower() for k in ["wireless", "wifi", "wi-fi", "802.11", "wlan"])
                            is_virtual = any(k in desc.lower() for k in ["virtual", "direct", "loopback", "tap", "vpn"])

                            if target_guid and guid.lower() == target_guid.lower():
                                adapters.append({"subkey": subkey_name, "desc": desc, "guid": guid})
                            elif is_wireless and not is_virtual:
                                adapters.append({"subkey": subkey_name, "desc": desc, "guid": guid})
                    except (PermissionError, FileNotFoundError):
                        continue
                except OSError:
                    break
    except Exception as e:
        logger.error("Error enumerating network adapters: %s", e)
    return adapters


def discover_supported_adapter_params(adapter_subkey_name: str) -> Dict[str, Dict[str, Any]]:
    """
    Inspect Ndi\\params for an adapter to discover which driver properties
    are officially exposed and supported by the OEM driver.
    """
    supported = {}
    if _DRY_RUN and _MOCK_REGISTRY:
        mock_data = _MOCK_REGISTRY.get(adapter_subkey_name, {})
        return mock_data.get("NdiParams", {})

    try:
        ndi_path = f"{NETWORK_ADAPTERS_KEY}\\{adapter_subkey_name}\\Ndi\\params"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, ndi_path, 0, winreg.KEY_READ) as params_key:
            i = 0
            while True:
                try:
                    param_name = winreg.EnumKey(params_key, i)
                    i += 1
                    param_info: Dict[str, Any] = {
                        "supported": True,
                        "desc": param_name,
                        "type": "string",
                        "enums": [],
                        "enum_codes": [],
                        "enum_labels": [],
                    }
                    try:
                        with winreg.OpenKey(params_key, param_name, 0, winreg.KEY_READ) as p_key:
                            try:
                                param_info["desc"] = winreg.QueryValueEx(p_key, "ParamDesc")[0]
                            except FileNotFoundError:
                                param_info["desc"] = param_name

                            try:
                                param_info["type"] = winreg.QueryValueEx(p_key, "type")[0]
                            except FileNotFoundError:
                                param_info["type"] = "string"

                            for num_attr in ["min", "max", "step", "default"]:
                                try:
                                    param_info[num_attr] = winreg.QueryValueEx(p_key, num_attr)[0]
                                except FileNotFoundError:
                                    pass

                            # Check for enum subkeys with code and human-readable label
                            try:
                                with winreg.OpenKey(p_key, "enum", 0, winreg.KEY_READ) as enum_key:
                                    j = 0
                                    while True:
                                        try:
                                            val_tuple = winreg.EnumValue(enum_key, j)
                                            e_code = str(val_tuple[0])
                                            e_label = str(val_tuple[1])
                                            param_info["enums"].append({"code": e_code, "label": e_label})
                                            param_info["enum_codes"].append(e_code)
                                            param_info["enum_labels"].append(e_label)
                                            j += 1
                                        except OSError:
                                            break
                            except FileNotFoundError:
                                pass
                    except (PermissionError, FileNotFoundError):
                        pass

                    supported[param_name] = param_info
                except OSError:
                    break
    except (FileNotFoundError, PermissionError) as e:
        logger.debug("Ndi\\params not accessible for %s: %s", adapter_subkey_name, e)
    return supported


def resolve_driver_param_value(prop_name: str, desired_value: str,
                               param_info: Dict[str, Any]) -> Tuple[bool, str, str]:
    """
    Validate and resolve driver parameter values against Ndi\\params metadata.
    Handles OEM enum code mappings (e.g. mapping MIMO Power Save disable to "No SMPS").
    Returns (is_valid, resolved_value, reason).
    """
    ptype = param_info.get("type", "string")

    if ptype == "enum":
        enums = param_info.get("enums", [])
        codes = param_info.get("enum_codes", [])

        # Special semantic mapping: MIMO Power Save
        if prop_name == "*MIMOPowerSave":
            # Search for "No SMPS" or "Disable" or "Off" in labels
            for item in enums:
                lbl = item["label"].lower()
                if "no smps" in lbl or "disable" in lbl or "off" in lbl:
                    return True, item["code"], f"Matched enum label '{item['label']}' -> code '{item['code']}'"

        # Check if desired_value is a valid code directly
        if desired_value in codes:
            return True, desired_value, f"Valid enum code '{desired_value}'"

        # If desired_value is a label match
        for item in enums:
            if desired_value.lower() in item["label"].lower():
                return True, item["code"], f"Matched enum label '{item['label']}' -> code '{item['code']}'"

        return False, desired_value, f"Desired value '{desired_value}' not found in supported driver enums: {codes}"

    elif ptype in ("int", "dword"):
        try:
            val_int = int(desired_value)
            p_min = int(param_info.get("min", val_int))
            p_max = int(param_info.get("max", val_int))
            clamped = max(p_min, min(p_max, val_int))
            return True, str(clamped), f"Clamped integer [{p_min}, {p_max}] -> {clamped}"
        except ValueError:
            return False, desired_value, "Non-integer value for integer driver property"

    return True, desired_value, "Standard string property"



def _record_snapshot_entry(subkey_name: str, prop_name: str, original_val: Optional[str], existed: bool, new_val: str):
    """Record a setting change in the in-memory rollback list and disk snapshot."""
    entry = {
        "subkey": subkey_name,
        "property": prop_name,
        "original_value": original_val,
        "existed": existed,
        "new_value": new_val,
        "timestamp": time.time()
    }
    _SESSION_SNAPSHOTS.append(entry)
    _save_snapshot_to_disk()


def _save_snapshot_to_disk():
    """Write current session snapshots to JSON file."""
    if _DRY_RUN:
        return
    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(_SESSION_SNAPSHOTS, f, indent=2)
    except Exception as e:
        logger.warning("Could not persist rollback snapshot: %s", e)


def rollback_settings(snapshot_path: Optional[str] = None) -> Tuple[bool, List[str]]:
    """
    Atomic rollback of all applied adapter modifications.
    Restores original registry values and deletes injected keys.
    """
    path = snapshot_path or SNAPSHOT_FILE
    snapshots_to_restore = list(_SESSION_SNAPSHOTS)

    if not snapshots_to_restore and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                snapshots_to_restore = json.load(f)
        except Exception as e:
            logger.error("Failed to read snapshot file for rollback: %s", e)
            return False, [f"Error reading snapshot: {e}"]

    if not snapshots_to_restore:
        return True, ["No previous settings to roll back."]

    restored_log = []
    # Process in reverse order of application
    for entry in reversed(snapshots_to_restore):
        subkey_name = entry["subkey"]
        prop = entry["property"]
        orig = entry.get("original_value")
        existed = entry.get("existed", False)

        if _DRY_RUN:
            action = f"Restored {prop} -> {orig}" if existed else f"Deleted injected {prop}"
            restored_log.append(f"[DRY-RUN] {subkey_name}: {action}")
            if subkey_name in _MOCK_REGISTRY:
                if existed:
                    _MOCK_REGISTRY[subkey_name][prop] = orig
                else:
                    _MOCK_REGISTRY[subkey_name].pop(prop, None)
            continue

        try:
            adapter_path = f"{NETWORK_ADAPTERS_KEY}\\{subkey_name}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, adapter_path, 0, winreg.KEY_READ | winreg.KEY_WRITE) as key:
                if existed and orig is not None:
                    winreg.SetValueEx(key, prop, 0, winreg.REG_SZ, str(orig))
                    restored_log.append(f"Restored {prop} to '{orig}' on {subkey_name}")
                else:
                    try:
                        winreg.DeleteValue(key, prop)
                        restored_log.append(f"Removed injected property {prop} on {subkey_name}")
                    except FileNotFoundError:
                        pass
        except Exception as e:
            logger.error("Error rolling back %s on %s: %s", prop, subkey_name, e)
            restored_log.append(f"Failed to roll back {prop}: {e}")

    # Clear snapshot after successful rollback
    _SESSION_SNAPSHOTS.clear()
    if not _DRY_RUN and os.path.exists(path):
        try:
            os.remove(path)
        except Exception:
            pass

    return True, restored_log


def _set_adapter_registry_value_safe(prop_name: str, value: str,
                                     target_guid: Optional[str] = None,
                                     verify_ndi_support: bool = True) -> bool:
    """
    Safely set a registry value on the active WiFi adapter.
    - Matches the specific adapter (not all blind keys).
    - Checks Ndi\\params for supported parameters if verify_ndi_support is True.
    - Records snapshot for atomic rollback.
    """
    adapters = find_active_wifi_adapters(target_guid)
    if not adapters:
        logger.warning("No active WiFi adapter located for setting %s", prop_name)
        return False

    success = False
    for ad in adapters:
        subkey_name = ad["subkey"]

        # Parameter validation and resolution via Ndi\params
        effective_value = str(value)
        if verify_ndi_support:
            supported_params = discover_supported_adapter_params(subkey_name)
            if supported_params:
                if prop_name not in supported_params:
                    logger.info("Property %s not supported in Ndi\\params for %s (%s). Skipping.",
                                prop_name, subkey_name, ad["desc"])
                    continue
                p_info = supported_params[prop_name]
                is_valid, resolved_val, reason = resolve_driver_param_value(prop_name, str(value), p_info)
                if not is_valid:
                    logger.warning("Value '%s' invalid for %s: %s. Skipping.", value, prop_name, reason)
                    continue
                effective_value = resolved_val

        # Dry run branch
        if _DRY_RUN:
            mock_adapter = _MOCK_REGISTRY.setdefault(subkey_name, {})
            existed = prop_name in mock_adapter
            orig_val = mock_adapter.get(prop_name)
            _record_snapshot_entry(subkey_name, prop_name, orig_val, existed, effective_value)
            mock_adapter[prop_name] = effective_value
            logger.info("[DRY-RUN] Set %s=%s for %s (%s)", prop_name, effective_value, subkey_name, ad["desc"])
            success = True
            continue

        # Live registry branch
        try:
            adapter_path = f"{NETWORK_ADAPTERS_KEY}\\{subkey_name}"
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, adapter_path, 0,
                                winreg.KEY_READ | winreg.KEY_WRITE) as key:
                existed = False
                orig_val = None
                try:
                    orig_val = winreg.QueryValueEx(key, prop_name)[0]
                    existed = True
                except FileNotFoundError:
                    existed = False

                winreg.SetValueEx(key, prop_name, 0, winreg.REG_SZ, str(effective_value))
                _record_snapshot_entry(subkey_name, prop_name, orig_val, existed, str(effective_value))
                logger.info("Successfully set %s=%s for %s (miniport link will reinitialize)",
                            prop_name, effective_value, ad["desc"])
                success = True
        except PermissionError:
            logger.warning("Access denied setting %s on %s. Run as Administrator.", prop_name, ad["desc"])
        except Exception as e:
            logger.error("Error setting %s on %s: %s", prop_name, ad["desc"], e)

    return success


# -------------------------------------------------------------
# Tier A: Safe / Standard Optimizations (Hardware Driver Level)
# -------------------------------------------------------------

def set_preferred_band(adapter_name: str, band: str = "5GHz", target_guid: Optional[str] = None) -> bool:
    """
    Tier A: Configure preferred band (2=5GHz, 1=2.4GHz, 0=Auto).
    Validated against adapter capabilities.
    """
    band_value = "2" if band == "5GHz" else ("1" if band == "2.4GHz" else "0")
    return _set_adapter_registry_value_safe(PROP_PREFERRED_BAND, band_value, target_guid=target_guid)


def set_roaming_aggressiveness(level: int = 3, target_guid: Optional[str] = None) -> bool:
    """
    Tier A: Configure roaming aggressiveness (1=Lowest to 5=Highest).
    Prevents sticky-client issues while maintaining stability.
    """
    val = str(min(5, max(1, level)))
    return _set_adapter_registry_value_safe(PROP_ROAMING_AGGRESSIVENESS, val, target_guid=target_guid)


def set_transmit_power(level: int = 5, target_guid: Optional[str] = None) -> bool:
    """
    Tier A: Set transmitter power (1=Lowest, 5=Highest/100%).
    """
    val = str(min(5, max(1, level)))
    return _set_adapter_registry_value_safe(PROP_TRANSMIT_POWER, val, target_guid=target_guid)


def disable_sleep_on_disconnect(disable: bool = True, target_guid: Optional[str] = None) -> bool:
    """Tier A: Keep transceiver energized when disconnected for fast reconnection."""
    val = "0" if disable else "1"
    return _set_adapter_registry_value_safe(PROP_SLEEP_ON_DISCONNECT, val, target_guid=target_guid)


def set_background_scanning(enabled: bool = True, target_guid: Optional[str] = None) -> bool:
    """Tier A: Configure periodic background BSSID scanning."""
    val = "1" if enabled else "0"
    return _set_adapter_registry_value_safe(PROP_BGSCANNING, val, target_guid=target_guid)


def set_power_saving(disable: bool = True, target_guid: Optional[str] = None) -> bool:
    """Tier A: Configure Windows high-performance wireless power plan and MIMO Power Save."""
    if _DRY_RUN:
        logger.info("[DRY-RUN] Power saving disabled: %s", disable)
        # Attempt mock MIMO power save resolve
        _set_adapter_registry_value_safe(PROP_MIMO, "disable", target_guid=target_guid, verify_ndi_support=True)
        return True
    try:
        run_netsh(["wlan", "set", "autoconfig", "enabled=yes", "interface=Wi-Fi"])
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
        # Resolve MIMO power save disable (e.g. mapping to "No SMPS" enum)
        _set_adapter_registry_value_safe(PROP_MIMO, "disable", target_guid=target_guid, verify_ndi_support=True)
        return True
    except Exception as e:
        logger.error("Failed to set power plan: %s", e)
        return False


def flush_dns() -> bool:
    """Tier A: Flush local DNS resolver cache."""
    if _DRY_RUN:
        logger.info("[DRY-RUN] DNS cache flushed")
        return True
    try:
        res = subprocess.run(
            ["ipconfig", "/flushdns"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return res.returncode == 0
    except Exception as e:
        logger.error("DNS flush error: %s", e)
        return False


def set_dns_servers(primary: str = "1.1.1.1", secondary: str = "8.8.8.8",
                    adapter_name: str = "Wi-Fi") -> bool:
    """Tier A: Set validated high-performance DNS resolvers."""
    if _DRY_RUN:
        logger.info("[DRY-RUN] DNS set to %s, %s", primary, secondary)
        return True
    try:
        ok1, _ = run_netsh(["interface", "ip", "set", "dns", f"name={adapter_name}", "static", primary, "primary"])
        ok2, _ = run_netsh(["interface", "ip", "add", "dns", f"name={adapter_name}", secondary, "index=2"])
        return ok1 or ok2
    except Exception as e:
        logger.error("Failed to set DNS: %s", e)
        return False


# -------------------------------------------------------------
# Tier B: Advanced Stack Tuning & Latency Optimizations (Opt-In)
# -------------------------------------------------------------
# NOTE: Client-side settings CANNOT override router-mandated EDCA parameters
# (CWmin/CWmax/TXOP limit) defined by IEEE 802.11 standards.
# Short Guard Interval applies only to 802.11n (400ns) and 802.11ac (400ns) links.
# 802.11ax/be links use standard 0.8us/1.6us/3.2us GI negotiated in the HE Operation element.

def is_rss_supported(adapter_name: str = "Wi-Fi") -> bool:
    """Check if the network interface hardware supports Receive Side Scaling (RSS)."""
    if _DRY_RUN:
        return True
    try:
        clean_name = re.sub(r"[^a-zA-Z0-9_\-\. ]", "", adapter_name)
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"(Get-NetAdapterRss -Name '{clean_name}' -ErrorAction SilentlyContinue).Enabled"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        out = res.stdout.strip().lower()
        return "true" in out or "false" in out
    except Exception:
        return False


def optimize_tcp_settings(adapter_name: str = "Wi-Fi") -> bool:
    """
    Tier B: Apply dynamic TCP stack optimizations.
    Enables auto-tuning (RFC 1323) and ECN (RFC 3168).
    Enables RSS only if hardware capability check passes.
    (Note: DCA is an Intel server I/OAT feature and is not applied on client Wi-Fi).
    """
    if _DRY_RUN:
        logger.info("[DRY-RUN] TCP stack optimized")
        return True

    cmds = [
        ["interface", "tcp", "set", "global", "autotuninglevel=normal"],
        ["interface", "tcp", "set", "global", "ecncapability=enabled"],
    ]

    if is_rss_supported(adapter_name):
        cmds.append(["interface", "tcp", "set", "global", "rss=enabled"])

    all_ok = True
    for cmd in cmds:
        ok, out = run_netsh(cmd)
        if not ok:
            logger.debug("TCP setting %s notice: %s", cmd, out)
            all_ok = False
    return all_ok


def apply_advanced_tier_b_tweaks(enable_packet_coalescing_disable: bool = False,
                                 target_guid: Optional[str] = None,
                                 connection_phy: Optional[str] = None) -> List[str]:
    """
    Tier B (Advanced / Opt-In):
    Applies optional driver-level latency tweaks with discovery validation.
    Short GI is gated on connection_phy: applied for 802.11n/ac, skipped for 802.11ax/be.
    """
    applied = []

    # Priority / VLAN Tagging (802.1p Layer-2 frame tagging)
    if _set_adapter_registry_value_safe("*PriorityVLANTag", "3", target_guid=target_guid, verify_ndi_support=True):
        applied.append("QoS / Priority Tagging (802.1p Layer-2) -> Enabled")

    # Short Guard Interval: evaluated against active connection PHY
    if connection_phy and any(k in connection_phy.lower() for k in ["ax", "be", "wi-fi 6", "wifi 6"]):
        logger.info("Active connection PHY is %s; skipping legacy SGI (802.11ax uses 0.8/1.6/3.2us GI).", connection_phy)
    else:
        for prop in ["*ShortGI20MHz", "*ShortGI40MHz", "*ShortGI80MHz"]:
            if _set_adapter_registry_value_safe(prop, "1", target_guid=target_guid, verify_ndi_support=True):
                applied.append(f"{prop.replace('*', '')} -> Enabled (HT/VHT SGI 400ns)")

    # Packet Coalescing (trade battery idle states for lower jitter)
    if enable_packet_coalescing_disable:
        if _set_adapter_registry_value_safe("*PacketCoalescing", "0", target_guid=target_guid, verify_ndi_support=True):
            applied.append("Packet Coalescing -> Disabled (minimum latency jitter)")

    # Vendor-specific throughput bursting (Intel / Realtek optional parameters)
    for booster_prop in ["*ThroughputBoosterEnabled", "*ThroughputBooster"]:
        if _set_adapter_registry_value_safe(booster_prop, "1", target_guid=target_guid, verify_ndi_support=True):
            applied.append(f"{booster_prop.replace('*', '')} -> Enabled (driver frame aggregation)")
            break

    return applied


def apply_selfish_airtime_boost(target_guid: Optional[str] = None,
                                connection_phy: Optional[str] = None) -> List[str]:
    """
    Legacy wrapper for UI compatibility:
    Applies Tier B advanced optimizations using discovery validation.
    """
    return apply_advanced_tier_b_tweaks(enable_packet_coalescing_disable=True,
                                        target_guid=target_guid,
                                        connection_phy=connection_phy)


def reset_wifi_adapter(adapter_name: str = "Wi-Fi") -> bool:
    """Safe adapter reset to clear hung link states (never run during active streaming)."""
    if _DRY_RUN:
        logger.info("[DRY-RUN] Adapter %s reset simulated", adapter_name)
        return True

    try:
        subprocess.run(["netsh", "wlan", "disconnect"], capture_output=True, timeout=5,
                       creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        run_netsh(["interface", "set", "interface", adapter_name, "disable"])
        time.sleep(1)
        run_netsh(["interface", "set", "interface", adapter_name, "enable"])
        return True
    except Exception as e:
        logger.error("Failed to reset adapter: %s", e)
        return False

