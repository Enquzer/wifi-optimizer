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
BACKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter_settings_backup.json")

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

# Preview mode: read-only simulation that reports the REAL current value and the
# proposed value for every change, without touching the system. Unlike dry-run
# (which uses an in-memory mock), preview reads actual registry state so users
# can see exactly what WOULD change before anything is written.
_PREVIEW_MODE: bool = False
_PREVIEW_CHANGES: List[Dict[str, Any]] = []


def set_dry_run_mode(enabled: bool):
    """Enable or disable dry-run mode (prevents actual system mutations)."""
    global _DRY_RUN
    _DRY_RUN = enabled
    logger.info("AdapterManager dry-run mode: %s", "ENABLED" if enabled else "DISABLED")


def is_dry_run_mode() -> bool:
    """Check if dry-run mode is currently enabled."""
    return _DRY_RUN


def set_preview_mode(enabled: bool):
    """Enable or disable preview mode (read-only; reports real current -> proposed)."""
    global _PREVIEW_MODE
    _PREVIEW_MODE = enabled
    if enabled:
        _PREVIEW_CHANGES.clear()
    logger.info("AdapterManager preview mode: %s", "ENABLED" if enabled else "DISABLED")


def is_preview_mode() -> bool:
    """Check if preview mode is currently enabled."""
    return _PREVIEW_MODE


def get_preview_changes() -> List[Dict[str, Any]]:
    """Return the list of changes recorded during preview mode."""
    return list(_PREVIEW_CHANGES)


def clear_preview_changes():
    """Clear recorded preview changes."""
    _PREVIEW_CHANGES.clear()


def _suppress_mutations() -> bool:
    """True when neither dry-run nor preview should touch the real system."""
    return _DRY_RUN or _PREVIEW_MODE


def _read_current_value(subkey_name: str, prop_name: str) -> Tuple[bool, Optional[str]]:
    """Read the current registry value (read-only). Returns (existed, value_string)."""
    try:
        adapter_path = f"{NETWORK_ADAPTERS_KEY}\\{subkey_name}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, adapter_path, 0, winreg.KEY_READ) as key:
            try:
                return True, str(winreg.QueryValueEx(key, prop_name)[0])
            except FileNotFoundError:
                return False, None
    except Exception:
        return False, None


def _record_preview(subkey_name: str, prop_name: str, value: str, desc: str,
                    intent: Optional[str] = None):
    """Record a single proposed change (real current value -> proposed value)."""
    existed, current = _read_current_value(subkey_name, prop_name)
    _PREVIEW_CHANGES.append({
        "subkey": subkey_name,
        "adapter": desc,
        "property": prop_name,
        "intent": intent or "",
        "current": current if existed else None,
        "proposed": str(value),
        "changed": (current != str(value)),
    })
    logger.info("[PREVIEW] %s (%s): %s  %s -> %s", desc, subkey_name, prop_name,
                current if existed else "(not set)", value)


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
    if _suppress_mutations():
        logger.info("[%s] netsh %s", "PREVIEW" if _PREVIEW_MODE else "DRY-RUN", " ".join(args))
        return True, "[simulated] Success"

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
    In dry-run mode the real adapters are still enumerated read-only, and any
    mock-only adapters are merged in (so simulation stays realistic).
    """
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

    # Merge any mock-only adapters during dry-run
    if _DRY_RUN:
        known = {a["subkey"] for a in adapters}
        for k, v in _MOCK_REGISTRY.items():
            if k not in known:
                adapters.append({"subkey": k,
                                 "desc": v.get("DriverDesc", "Mock WiFi"),
                                 "guid": v.get("NetCfgInstanceId", "")})
    return adapters


def detect_wifi_interface_name() -> Optional[str]:
    """
    Best-effort detection of the wireless interface's friendly name, used by the
    netsh-based features (DNS, TCP, interface reset). On a different laptop this
    name may not be "Wi-Fi", so we detect it instead of assuming.

    Uses PowerShell Get-NetAdapter, whose object properties are language-
    independent (unlike localized netsh text output).
    """
    if _suppress_mutations():
        return "Wi-Fi"
    try:
        ps = ("$w = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | "
              "Where-Object { $_.PhysicalMediaType -eq 'Native 802.11' -or "
              "$_.NdisPhysicalMedium -eq 9 }; "
              "$up = $w | Where-Object { $_.Status -eq 'Up' } | "
              "Select-Object -First 1 -ExpandProperty Name; "
              "if ($up) { $up } else { $w | Select-Object -First 1 -ExpandProperty Name }")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        lines = [l.strip() for l in (res.stdout or "").splitlines() if l.strip()]
        if lines:
            return lines[0]
    except Exception as e:
        logger.debug("Interface auto-detect failed: %s", e)
    return None


def discover_supported_adapter_params(adapter_subkey_name: str) -> Dict[str, Dict[str, Any]]:
    """
    Inspect Ndi\\params for an adapter to discover which driver properties
    are officially exposed and supported by the OEM driver.
    """
    supported = {}
    if _DRY_RUN:
        mock_data = _MOCK_REGISTRY.get(adapter_subkey_name, {})
        if "NdiParams" in mock_data:
            return mock_data["NdiParams"]
        # otherwise fall through and read the real (read-only) driver params

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


# ─────────────────────────────────────────────────────────────────────────────
# Vendor-agnostic intent resolution
# ─────────────────────────────────────────────────────────────────────────────
# Each optimizer "intent" maps to candidate NDIS registry keywords used by
# different OEM drivers (Intel, Realtek, and the generic Microsoft names).
# The first keyword the driver actually exposes in Ndi\params is used, and the
# value is resolved by matching enum labels — so vendor-specific encodings
# (e.g. Realtek TxPwrLevel where 0 = Highest, or WakeOnDisconnect where
# Disabled = 1) are handled correctly instead of writing a guessed integer.
INTENT_KEYWORDS = {
    "preferred_band":         ["RoamingPreferredBandType", "*PreferredBand", "PreferBand"],
    "roaming_aggressiveness": ["RoamAggressiveness", "*RoamAggressiveness", "RegROAMSensitiveLevel"],
    "transmit_power":         ["IbssTxPower", "*TransmitPower", "TxPwrLevel"],
    "sleep_on_disconnect":    ["*DeviceSleepOnDisconnect", "*SleepOnDisconnect", "WakeOnDisconnect"],
    "mimo_power_save":        ["MIMOPowerSaveMode", "*MIMOPowerSave"],
    "throughput_booster":     ["ThroughputBoosterEnabled", "*ThroughputBoosterEnabled", "*ThroughputBooster"],
    "packet_coalescing":      ["*PacketCoalescing"],
    "uapsd":                  ["uAPSDSupport"],
    "channel_width_24":       ["ChannelWidth24", "BW40MHzFor2G"],
    "channel_width_52":       ["ChannelWidth52", "BW40MHzFor5G"],
    "background_scanning":    ["BgScanningEnabled", "*BgScanningEnabled"],
    "priority_vlan_tag":      ["*PriorityVLANTag", "*PriorityVlanTag"],
}

INTENT_LABELS = {
    "preferred_band":         "Preferred Band",
    "roaming_aggressiveness": "Roaming Aggressiveness",
    "transmit_power":         "Transmit Power",
    "sleep_on_disconnect":    "Sleep on Disconnect",
    "mimo_power_save":        "MIMO Power Save",
    "throughput_booster":     "Throughput Booster",
    "packet_coalescing":      "Packet Coalescing",
    "uapsd":                  "U-APSD Support",
    "channel_width_24":       "Channel Width 2.4GHz",
    "channel_width_52":       "Channel Width 5GHz",
    "background_scanning":    "Background Scanning",
    "priority_vlan_tag":      "Priority / VLAN Tag",
}

# Fallback: match an intent to a driver property by its human-readable ParamDesc.
# This lets the tool work on vendors whose registry keyword names are unknown
# (Qualcomm/Atheros, Broadcom, MediaTek, Killer, Marvell, ...) as long as the
# driver exposes a recognisable label. Longer patterns win (more specific).
INTENT_DESC_PATTERNS = {
    "preferred_band":         ["preferred band", "band preference", "prefer band", "band select"],
    "roaming_aggressiveness": ["roaming aggressiveness", "roaming sensitivity",
                               "roaming policy", "roaming level"],
    "transmit_power":         ["transmit power", "tx power", "transmit power level",
                               "power output"],
    "sleep_on_disconnect":    ["sleep on disconnect"],
    "mimo_power_save":        ["mimo power save", "mimo power saving"],
    "throughput_booster":     ["throughput booster", "throughput enhancement",
                               "frame bursting", "packet bursting"],
    "packet_coalescing":      ["packet coalescing"],
    "uapsd":                  ["uapsd", "u-apds", "unscheduled automatic power save"],
    "channel_width_24":       ["2.4ghz channel width", "2.4 ghz channel width",
                               "channel width 2.4"],
    "channel_width_52":       ["5ghz channel width", "5 ghz channel width",
                               "channel width 5"],
    "background_scanning":    ["background scanning", "roaming scanning",
                               "background scan"],
    "priority_vlan_tag":      ["priority & vlan", "priority and vlan", "vlan tag",
                               "802.1p"],
}

# Semantic value -> label substrings (longer = more specific; matched greedily).
# Broadened to cover common third-party vendor label styles, e.g. Intel's
# "5. Highest", Realtek's "6. Highest", and generic "High"/"Low"/"100%".
AGGRESSION_SYNONYMS = {
    "max":    ["highest", "maximum", "100%"],
    "high":   ["medium-high", "medium high", "high", "75%"],
    "medium": ["medium", "50%"],
    "low":    ["medium-low", "medium low", "low", "25%"],
    "min":    ["lowest", "minimum", "10%", "0%", "disable"],
}
BAND_SYNONYMS = {
    "5GHz":   ["5ghz", "5 ghz", "5g first", "prefer 5", "5g"],
    "2.4GHz": ["2.4ghz", "2.4 ghz", "2.4g first", "prefer 2.4", "2.4g"],
    "auto":   ["no preference", "auto", "automatic"],
}
POWER_SAVE_SYNONYMS = {
    "disable": ["no smps", "disabled", "off"],
    "enable":  ["auto smps", "enabled"],
}
ON_OFF_SYNONYMS = {
    "on":  ["enabled"],
    "off": ["disabled"],
}
WIDTH_SYNONYMS = {
    "auto":  ["auto"],
    "20MHz": ["20 mhz", "20mhz"],
    "40MHz": ["40 mhz", "40mhz"],
    "80MHz": ["80 mhz", "80mhz"],
}

INTENT_SYNONYMS = {
    "transmit_power":         AGGRESSION_SYNONYMS,
    "roaming_aggressiveness": AGGRESSION_SYNONYMS,
    "preferred_band":         BAND_SYNONYMS,
    "mimo_power_save":        POWER_SAVE_SYNONYMS,
    "sleep_on_disconnect":    POWER_SAVE_SYNONYMS,
    "throughput_booster":     ON_OFF_SYNONYMS,
    "packet_coalescing":      ON_OFF_SYNONYMS,
    "uapsd":                  ON_OFF_SYNONYMS,
    "background_scanning":    ON_OFF_SYNONYMS,
    "priority_vlan_tag":      ON_OFF_SYNONYMS,
    "channel_width_24":       WIDTH_SYNONYMS,
    "channel_width_52":       WIDTH_SYNONYMS,
}

_BAND_SEMANTIC = {"5GHz": "5GHz", "2.4GHz": "2.4GHz", "Auto": "auto", "auto": "auto"}
_LEVEL_SEMANTIC = {1: "min", 2: "low", 3: "medium", 4: "high", 5: "max"}
_LEVEL_RANK = {"min": 0.0, "low": 0.25, "medium": 0.5, "high": 0.75, "max": 1.0}


def _match_intent_keyword(intent: str, params: Dict[str, Dict[str, Any]]) -> Optional[str]:
    """
    Return the registry keyword to use for an intent.

    Two-stage lookup so the tool is portable across Wi-Fi vendors:
      1) Exact registry-keyword match (known Intel/Realtek/generic names) — fast path.
      2) Fallback: match the driver's human-readable ParamDesc (e.g. "Transmit Power")
         so unknown chipsets (Qualcomm/Atheros, Broadcom, MediaTek, Killer,
         Marvell, etc.) still resolve, provided their driver exposes a
         recognisable label. If nothing matches, returns None and the intent is
         safely skipped (never guessed).
    """
    # Stage 1: known registry keywords
    for candidate in INTENT_KEYWORDS.get(intent, []):
        if candidate in params:
            return candidate

    # Stage 2: match by human-readable driver label (ParamDesc)
    patterns = INTENT_DESC_PATTERNS.get(intent, [])
    if not patterns:
        return None
    best_kw = None
    best_len = 0
    for keyword, info in params.items():
        desc = str(info.get("desc", "")).lower()
        for p in patterns:
            if p in desc and len(p) > best_len:
                best_kw, best_len = keyword, len(p)
    return best_kw


def resolve_intent_code(intent: str, semantic: str,
                        param_info: Dict[str, Any]) -> Tuple[bool, str, str]:
    """
    Resolve a semantic value (e.g. 'max', '5GHz', 'disable') to the exact driver
    enum code by assigning every enum entry to its most specific semantic.
    Handles inverted vendor encodings correctly.
    Returns (is_valid, code, reason).
    """
    ptype = param_info.get("type", "string")
    enums = param_info.get("enums", []) or []

    if ptype == "enum" and enums:
        synonyms = INTENT_SYNONYMS.get(intent, {})
        if semantic not in synonyms:
            return False, "", f"No synonym for {intent}={semantic}"

        # Assign each enum entry to its most specific semantic (greedy longest match)
        assigned: Dict[str, Dict[str, str]] = {}
        for item in enums:
            label_l = str(item.get("label", "")).lower()
            best_sem, best_len = None, 0
            for sem, syns in synonyms.items():
                for syn in syns:
                    if syn in label_l and len(syn) > best_len:
                        best_sem, best_len = sem, len(syn)
            if best_sem and best_sem not in assigned:
                assigned[best_sem] = item

        hit = assigned.get(semantic)
        if hit:
            return True, str(hit["code"]), f"enum '{hit['label']}' -> code '{hit['code']}'"

        # Rank-proximity fallback: some drivers expose a shorter scale (e.g. a
        # 3-level "Low/Medium/High"), so "max" has no "Highest" to match. Pick
        # the assigned semantic closest in rank to what was requested.
        rank = _LEVEL_RANK.get(semantic)
        if rank is not None and assigned:
            sem_name, item = min(
                assigned.items(),
                key=lambda kv: abs(_LEVEL_RANK.get(kv[0], 0.5) - rank))
            return True, str(item["code"]), \
                (f"rank-proximity fallback ({semantic}~{sem_name}) -> "
                 f"'{item['label']}' code '{item['code']}'")

        # Numeric-rank fallback: for labels like "10%","50%","100%" or plain
        # numeric enums ("0","1","2"), order entries by an extracted number and
        # pick by semantic rank (min..max). Keeps unknown vendors working.
        if rank is not None:
            numbered = []
            for item in enums:
                m = re.search(r"(-?\d+(?:\.\d+)?)", str(item.get("label", "")))
                if m:
                    numbered.append((float(m.group(1)), item))
            if len(numbered) >= 2:
                numbered.sort(key=lambda t: t[0])
                idx = int(round(rank * (len(numbered) - 1)))
                chosen = numbered[idx][1]
                return True, str(chosen["code"]), \
                    f"numeric-rank fallback -> '{chosen['label']}' code '{chosen['code']}'"

        return False, "", f"No enum label matched semantic '{semantic}'"

    if ptype in ("int", "dword"):
        try:
            p_min = int(param_info.get("min", 0))
            p_max = int(param_info.get("max", 100))
        except (TypeError, ValueError):
            p_min, p_max = 0, 100
        rank = _LEVEL_RANK.get(semantic)
        if rank is None:
            return False, "", f"Cannot map '{semantic}' onto integer range"
        val = int(round(p_min + rank * (p_max - p_min)))
        return True, str(val), f"mapped to {val} within [{p_min},{p_max}]"

    return True, str(semantic), "string passthrough"


def _write_adapter_value(subkey_name: str, prop_name: str, value: str, desc: str,
                         verify_ndi_support: bool = True,
                         intent: Optional[str] = None) -> bool:
    """Write a validated registry value on one adapter subkey, recording a snapshot."""
    if verify_ndi_support:
        supported = discover_supported_adapter_params(subkey_name)
        if supported and prop_name not in supported:
            logger.info("Property %s not supported on %s (%s). Skipping.",
                        prop_name, subkey_name, desc)
            return False

    if _PREVIEW_MODE:
        _record_preview(subkey_name, prop_name, str(value), desc, intent=intent)
        return True

    if _DRY_RUN:
        mock = _MOCK_REGISTRY.setdefault(subkey_name, {})
        existed = prop_name in mock
        orig = mock.get(prop_name)
        _record_snapshot_entry(subkey_name, prop_name, orig, existed, str(value))
        mock[prop_name] = str(value)
        logger.info("[DRY-RUN] Set %s=%s on %s (%s)", prop_name, value, subkey_name, desc)
        return True

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
            winreg.SetValueEx(key, prop_name, 0, winreg.REG_SZ, str(value))
            _record_snapshot_entry(subkey_name, prop_name, orig_val, existed, str(value))
            logger.info("Set %s=%s on %s (%s) (miniport will reinitialize)",
                        prop_name, value, subkey_name, desc)
            return True
    except PermissionError:
        logger.warning("Access denied setting %s on %s. Run as Administrator.", prop_name, desc)
    except Exception as e:
        logger.error("Error setting %s on %s: %s", prop_name, desc, e)
    return False


def apply_intent(intent: str, semantic: str,
                 target_guid: Optional[str] = None) -> bool:
    """
    Apply a vendor-agnostic optimizer intent to every matching WiFi adapter.
    Resolves the real registry keyword + enum code via Ndi\\params discovery.
    """
    adapters = find_active_wifi_adapters(target_guid)
    if not adapters:
        logger.warning("No active WiFi adapter located for intent '%s'", intent)
        return False

    success = False
    for ad in adapters:
        subkey = ad["subkey"]
        params = discover_supported_adapter_params(subkey)
        if not params:
            continue
        keyword = _match_intent_keyword(intent, params)
        if not keyword:
            continue
        ok, code, reason = resolve_intent_code(intent, semantic, params[keyword])
        if not ok:
            logger.warning("Intent %s=%s unresolvable on %s: %s", intent, semantic, subkey, reason)
            continue
        if _write_adapter_value(subkey, keyword, code, ad["desc"], verify_ndi_support=False,
                                intent=INTENT_LABELS.get(intent, intent)):
            logger.info("Intent %s=%s -> %s=%s on %s (%s)",
                        intent, semantic, keyword, code, ad["desc"], reason)
            success = True
    return success


def discover_optimizer_capabilities(target_guid: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Report which optimizer intents each active WiFi driver actually exposes.
    Powers the 'Hardware' page so users can see real vs. unavailable tuning.
    """
    report: List[Dict[str, Any]] = []
    for ad in find_active_wifi_adapters(target_guid):
        subkey = ad["subkey"]
        params = discover_supported_adapter_params(subkey)
        caps: Dict[str, Dict[str, Any]] = {}
        for intent, label in INTENT_LABELS.items():
            keyword = _match_intent_keyword(intent, params) if params else None
            entry: Dict[str, Any] = {"available": keyword is not None, "keyword": keyword, "options": []}
            if keyword:
                info = params[keyword]
                entry["type"] = info.get("type", "?")
                entry["options"] = [f"{e['label']} ({e['code']})"
                                    for e in (info.get("enums") or [])]
            caps[label] = entry
        report.append({
            "subkey": subkey,
            "desc": ad["desc"],
            "guid": ad["guid"],
            "param_count": len(params),
            "capabilities": caps,
        })
    return report



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
    if _suppress_mutations():
        return
    try:
        with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(_SESSION_SNAPSHOTS, f, indent=2)
    except Exception as e:
        logger.warning("Could not persist rollback snapshot: %s", e)


def backup_adapter_settings() -> Optional[str]:
    """
    Safety net: export the CURRENT value of every advanced property for every
    active WiFi adapter to a timestamped-agnostic JSON file BEFORE any change.
    Read-only against the registry. This is a full, independent backup you can
    inspect or restore from even if the per-change snapshot is lost.
    Returns the backup path (or None on dry-run).
    """
    backup = {"created": time.time(), "adapters": {}}
    for ad in find_active_wifi_adapters():
        subkey = ad["subkey"]
        params = discover_supported_adapter_params(subkey)
        values: Dict[str, str] = {}
        adapter_path = f"{NETWORK_ADAPTERS_KEY}\\{subkey}"
        for kw in params:
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, adapter_path, 0, winreg.KEY_READ) as key:
                    try:
                        values[kw] = str(winreg.QueryValueEx(key, kw)[0])
                    except FileNotFoundError:
                        pass
            except Exception:
                pass
        backup["adapters"][subkey] = {"desc": ad["desc"], "guid": ad["guid"], "values": values}

    if _DRY_RUN:
        logger.info("[DRY-RUN] Adapter settings backup skipped")
        return None
    if _PREVIEW_MODE:
        logger.info("[PREVIEW] Adapter settings backup skipped (read-only)")
        return None
    try:
        with open(BACKUP_FILE, "w", encoding="utf-8") as f:
            json.dump(backup, f, indent=2)
        logger.info("Adapter settings backup written to %s (%d adapters)",
                    BACKUP_FILE, len(backup["adapters"]))
        return BACKUP_FILE
    except Exception as e:
        logger.warning("Could not write adapter settings backup: %s", e)
        return None


def restore_from_backup(backup_path: Optional[str] = None) -> Tuple[bool, List[str]]:
    """
    Restore adapter advanced properties from a full backup file created by
    backup_adapter_settings(). Independent of the per-change snapshot.
    """
    path = backup_path or BACKUP_FILE
    if not os.path.exists(path):
        return False, [f"No backup file found at {path}"]
    try:
        with open(path, "r", encoding="utf-8") as f:
            backup = json.load(f)
    except Exception as e:
        return False, [f"Could not read backup: {e}"]

    log: List[str] = []
    for subkey, info in backup.get("adapters", {}).items():
        for prop, value in (info.get("values") or {}).items():
            if _DRY_RUN:
                log.append(f"[DRY-RUN] Restore {prop}={value} on {subkey}")
                continue
            try:
                adapter_path = f"{NETWORK_ADAPTERS_KEY}\\{subkey}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, adapter_path, 0,
                                    winreg.KEY_READ | winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, prop, 0, winreg.REG_SZ, str(value))
                    log.append(f"Restored {prop}={value} on {subkey}")
            except Exception as e:
                log.append(f"Failed to restore {prop} on {subkey}: {e}")
    return True, log


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

        # Preview branch: report real current -> proposed without writing
        if _PREVIEW_MODE:
            _record_preview(subkey_name, prop_name, effective_value, ad["desc"])
            success = True
            continue

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
    Tier A: Configure preferred band.
    Vendor-mapped: Intel 'RoamingPreferredBandType', Realtek 'PreferBand',
    or generic '*PreferredBand' — resolved via Ndi\\params discovery.
    """
    return apply_intent("preferred_band", _BAND_SEMANTIC.get(band, "auto"), target_guid)


def set_roaming_aggressiveness(level: int = 3, target_guid: Optional[str] = None) -> bool:
    """
    Tier A: Configure roaming aggressiveness (1=Lowest to 5=Highest).
    Vendor-mapped by enum label so inverted encodings resolve correctly.
    """
    lvl = min(5, max(1, int(level)))
    return apply_intent("roaming_aggressiveness", _LEVEL_SEMANTIC[lvl], target_guid)


def set_transmit_power(level: int = 5, target_guid: Optional[str] = None) -> bool:
    """
    Tier A: Set transmitter power (1=Lowest, 5=Highest/100%).
    Vendor-mapped: Intel 'IbssTxPower', Realtek 'TxPwrLevel'.
    """
    lvl = min(5, max(1, int(level)))
    return apply_intent("transmit_power", _LEVEL_SEMANTIC[lvl], target_guid)


def disable_sleep_on_disconnect(disable: bool = True, target_guid: Optional[str] = None) -> bool:
    """Tier A: Keep transceiver energized when disconnected for fast reconnection."""
    return apply_intent("sleep_on_disconnect", "disable" if disable else "enable", target_guid)


def set_background_scanning(enabled: bool = True, target_guid: Optional[str] = None) -> bool:
    """Tier A: Configure periodic background BSSID scanning (if driver exposes it)."""
    return apply_intent("background_scanning", "on" if enabled else "off", target_guid)


def set_power_saving(disable: bool = True, target_guid: Optional[str] = None) -> bool:
    """Tier A: Configure Windows high-performance wireless power plan and MIMO Power Save."""
    if _suppress_mutations():
        logger.info("[%s] Power saving disabled: %s",
                    "PREVIEW" if _PREVIEW_MODE else "DRY-RUN", disable)
        apply_intent("mimo_power_save", "disable" if disable else "enable", target_guid)
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
        # Resolve MIMO power save disable (Intel: MIMOPowerSaveMode -> "No SMPS")
        apply_intent("mimo_power_save", "disable" if disable else "enable", target_guid)
        return True
    except Exception as e:
        logger.error("Failed to set power plan: %s", e)
        return False


def flush_dns() -> bool:
    """Tier A: Flush local DNS resolver cache."""
    if _suppress_mutations():
        logger.info("[%s] DNS cache flush simulated",
                    "PREVIEW" if _PREVIEW_MODE else "DRY-RUN")
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
    if _suppress_mutations():
        if _PREVIEW_MODE:
            _PREVIEW_CHANGES.append({
                "subkey": "-", "adapter": adapter_name, "property": "DNS servers",
                "intent": "DNS", "current": "(current DNS)", "proposed": f"{primary} / {secondary}",
                "changed": True,
            })
        logger.info("[%s] DNS set to %s, %s",
                    "PREVIEW" if _PREVIEW_MODE else "DRY-RUN", primary, secondary)
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
    if _suppress_mutations():
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
    if _suppress_mutations():
        if _PREVIEW_MODE:
            _PREVIEW_CHANGES.append({
                "subkey": "-", "adapter": adapter_name, "property": "TCP/IP stack",
                "intent": "TCP", "current": "(current TCP config)",
                "proposed": "autotuning=normal, ECN=enabled, RSS=enabled", "changed": True,
            })
        logger.info("[%s] TCP stack optimized",
                    "PREVIEW" if _PREVIEW_MODE else "DRY-RUN")
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
    Applies optional driver-level latency tweaks via vendor-agnostic intents.
    Short GI is gated on connection_phy: applied for 802.11n/ac, skipped for 802.11ax/be.
    """
    applied = []

    # Priority / VLAN Tagging (802.1p Layer-2 frame tagging)
    if apply_intent("priority_vlan_tag", "on", target_guid):
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
        if apply_intent("packet_coalescing", "off", target_guid):
            applied.append("Packet Coalescing -> Disabled (minimum latency jitter)")

    # Vendor-specific throughput bursting (Intel Throughput Booster, etc.)
    if apply_intent("throughput_booster", "on", target_guid):
        applied.append("Throughput Booster -> Enabled (driver frame aggregation)")

    # U-APSD off reduces latency at the cost of battery
    if apply_intent("uapsd", "off", target_guid):
        applied.append("U-APSD -> Disabled (lower latency)")

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
    if _suppress_mutations():
        logger.info("[%s] Adapter %s reset simulated",
                    "PREVIEW" if _PREVIEW_MODE else "DRY-RUN", adapter_name)
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

