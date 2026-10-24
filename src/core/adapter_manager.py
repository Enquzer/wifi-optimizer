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
from datetime import datetime
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass, asdict

try:
    from core import wlan_native
except ImportError:  # direct import when src/core is on sys.path
    import wlan_native

logger = logging.getLogger(__name__)

# Registry keys for WiFi adapter properties
NETWORK_ADAPTERS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"
SNAPSHOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter_rollback_snapshot.json")
BACKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "adapter_settings_backup.json")
SYSTEM_SNAPSHOT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "system_rollback_snapshot.json")

# powercfg GUIDs for "Wireless Adapter Settings > Power Saving Mode"
WIRELESS_SUBGROUP = "19cbb8fa-5279-450e-9fac-8a3d5fedd0c1"
WIRELESS_SETTING = "12bbebe6-58d6-4636-95bb-3217ef867c1a"

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

# Per-run change ledger. Only entries in "changed" are real, applied changes;
# "noop" means the adapter was already at the target value. The UI/optimizer
# reports these separately instead of inflating the applied count.
_RUN: Dict[str, List[Dict[str, Any]]] = {
    "changed": [], "noop": [], "failed": [], "skipped": [],
}


def begin_run():
    """Reset the change ledger before an optimization/preview pass."""
    for bucket in _RUN.values():
        bucket.clear()


def _note(kind: str, **info):
    bucket = _RUN.get(kind)
    if bucket is not None:
        bucket.append(info)


def record_note(kind: str, **info):
    """Public wrapper around the change ledger (used outside this module).

    `kind` is one of changed / noop / failed / skipped. Unknown kinds are
    ignored, so a caller cannot corrupt the ledger.
    """
    _note(kind, **info)


def get_run_changes() -> Dict[str, List[Dict[str, Any]]]:
    """Return a copy of the current change ledger."""
    return {k: list(v) for k, v in _RUN.items()}


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
                    intent: Optional[str] = None,
                    compare_value: Optional[str] = None):
    """Record a single proposed change (real current value -> proposed value).

    `value` is what the user should see (e.g. "2.4GHz"/"20MHz"), while
    `compare_value` is the driver code actually compared with the registry.
    """
    existed, current = _read_current_value(subkey_name, prop_name)
    effective = str(value) if compare_value is None else str(compare_value)
    _PREVIEW_CHANGES.append({
        "subkey": subkey_name,
        "adapter": desc,
        "property": prop_name,
        "intent": intent or "",
        "current": current if existed else None,
        "proposed": str(value),
        "changed": (current != effective),
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


def run_powershell(script: str, timeout: int = 15) -> Tuple[bool, str]:
    """Run a PowerShell snippet and return (success, stdout).

    Callers that mutate system state must guard with `_suppress_mutations()`
    themselves; this helper is also used for read-only queries in preview mode.
    """
    try:
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0,
        )
        return res.returncode == 0, (res.stdout or "").strip()
    except Exception as e:
        logger.debug("PowerShell failed: %s", e)
        return False, ""


def _ps_quote(value: str) -> str:
    """Quote a value for a single-quoted PowerShell string literal."""
    return "'" + str(value).replace("'", "''") + "'"


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

    # Drop stale registry duplicates: keep only keys whose GUID is a WLAN
    # interface currently enumerated by the OS. This avoids writing to ghost
    # subkeys left behind by old drivers or renamed adapters.
    try:
        present = {i["guid"].strip("{}").lower() for i in wlan_native.list_interfaces()}
        if present:
            filtered = [a for a in adapters
                        if not a["guid"] or a["guid"].strip("{}").lower() in present]
            if filtered:
                adapters = filtered
    except Exception:
        pass

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
    # "enable" means "prefer power saving": Intel exposes Static/Dynamic SMPS.
    "enable":  ["auto smps", "static smps", "dynamic smps", "enabled",
                "maximum power saving", "medium power saving"],
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
                         intent: Optional[str] = None,
                         display_value: Optional[str] = None) -> bool:
    """Write a validated registry value on one adapter subkey, recording a snapshot.

    Returns True when the adapter is (now) at the requested value, False when
    the write failed or is unsupported. The run ledger distinguishes a real
    change from a no-op so callers never report "applied" for an unchanged value.
    """
    label = intent or prop_name
    # `shown` is the human-facing value (e.g. "2.4GHz"/"20MHz") while `value`
    # is the driver's numeric code. Reporting the code alone looked like a bug.
    shown = display_value if display_value is not None else str(value)
    if verify_ndi_support:
        supported = discover_supported_adapter_params(subkey_name)
        if supported and prop_name not in supported:
            logger.info("Property %s not supported on %s (%s). Skipping.",
                        prop_name, subkey_name, desc)
            _note("skipped", adapter=desc, subkey=subkey_name, property=prop_name,
                  intent=label, reason="driver does not expose this setting")
            return False

    if _PREVIEW_MODE:
        existed, current = _read_current_value(subkey_name, prop_name)
        _record_preview(subkey_name, prop_name, shown, desc, intent=label,
                        compare_value=str(value))
        _note("changed" if (not existed or current != str(value)) else "noop",
              adapter=desc, subkey=subkey_name, property=prop_name, intent=label,
              old=current if existed else None, new=shown)
        return True

    if _DRY_RUN:
        mock = _MOCK_REGISTRY.setdefault(subkey_name, {})
        existed = prop_name in mock
        orig = mock.get(prop_name)
        _record_snapshot_entry(subkey_name, prop_name, orig, existed, str(value))
        mock[prop_name] = str(value)
        _note("changed" if (not existed or orig != str(value)) else "noop",
              adapter=desc, subkey=subkey_name, property=prop_name, intent=label,
              old=orig, new=shown)
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

            # Already at the requested value: do not write, do not snapshot.
            if existed and str(orig_val) == str(value):
                logger.info("%s=%s already set on %s (%s). No change.",
                            prop_name, value, subkey_name, desc)
                _note("noop", adapter=desc, subkey=subkey_name, property=prop_name,
                      intent=label, old=orig_val, new=shown)
                return True

            winreg.SetValueEx(key, prop_name, 0, winreg.REG_SZ, str(value))
            _record_snapshot_entry(subkey_name, prop_name, orig_val, existed, str(value))
            # A registry write can be accepted and then rejected/normalized by the
            # driver. Read it back so "changed" means the value is actually there.
            read_back = winreg.QueryValueEx(key, prop_name)[0]
            if str(read_back) != str(value):
                logger.warning("Read-back mismatch for %s on %s: wrote %r, got %r",
                               prop_name, subkey_name, value, read_back)
                _note("failed", adapter=desc, subkey=subkey_name, property=prop_name,
                      intent=label, reason=f"read-back mismatch ({read_back!r})")
                return False
            _note("changed", adapter=desc, subkey=subkey_name, property=prop_name,
                  intent=label, old=orig_val, new=shown)
            logger.info("Set %s=%s on %s (%s) (miniport will reinitialize)",
                        prop_name, value, subkey_name, desc)
            return True
    except PermissionError:
        logger.warning("Access denied setting %s on %s. Run as Administrator.", prop_name, desc)
        _note("failed", adapter=desc, subkey=subkey_name, property=prop_name,
              intent=label, reason="access denied (run as administrator)")
    except Exception as e:
        logger.error("Error setting %s on %s: %s", prop_name, desc, e)
        _note("failed", adapter=desc, subkey=subkey_name, property=prop_name,
              intent=label, reason=str(e))
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
        _note("skipped", adapter="-", property=intent, intent=INTENT_LABELS.get(intent, intent),
              reason="no active Wi-Fi adapter")
        return False

    success = False
    for ad in adapters:
        subkey = ad["subkey"]
        params = discover_supported_adapter_params(subkey)
        if not params:
            continue
        keyword = _match_intent_keyword(intent, params)
        if not keyword:
            _note("skipped", adapter=ad["desc"], subkey=subkey, property=intent,
                  intent=INTENT_LABELS.get(intent, intent),
                  reason="driver does not expose this setting")
            continue
        ok, code, reason = resolve_intent_code(intent, semantic, params[keyword])
        if not ok:
            logger.warning("Intent %s=%s unresolvable on %s: %s", intent, semantic, subkey, reason)
            _note("skipped", adapter=ad["desc"], subkey=subkey, property=keyword,
                  intent=INTENT_LABELS.get(intent, intent), reason=reason)
            continue
        if _write_adapter_value(subkey, keyword, code, ad["desc"], verify_ndi_support=False,
                                intent=INTENT_LABELS.get(intent, intent),
                                display_value=semantic):
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
    # Preserve history across app restarts. Without this, the first write of a
    # new session overwrote the on-disk log and Restore Defaults could only undo
    # the newest session. Stale adapter subkeys are filtered at restore time.
    if not _SESSION_SNAPSHOTS and os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, list):
                _SESSION_SNAPSHOTS.extend(e for e in loaded if isinstance(e, dict))
        except Exception as e:
            logger.debug("Could not load existing rollback snapshot: %s", e)
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


def _current_subkeys_or_none() -> Optional[set]:
    """Subkeys of WLAN adapters that currently exist, or None if unknown.

    Registry-class keys left behind by an old/renamed driver (the app used to
    write 0002/0014/0017 as well as the live 0021) must never be restored or
    reported as current changes.
    """
    try:
        adapters = find_active_wifi_adapters()
        if adapters:
            return {a["subkey"] for a in adapters if a.get("subkey")}
    except Exception:
        pass
    return None


def _backup_files() -> List[str]:
    """Timestamped backups only.

    The legacy fixed-name `adapter_settings_backup.json` was overwritten on
    every run and already contained post-optimization values, so it is not a
    safe restore source and is deliberately excluded.
    """
    folder = os.path.dirname(BACKUP_FILE)
    legacy = os.path.basename(BACKUP_FILE)
    try:
        return [os.path.join(folder, f) for f in os.listdir(folder)
                if f.startswith("adapter_settings_backup_") and f.endswith(".json")
                and f != legacy]
    except Exception:
        return []


def _latest_backup_file() -> Optional[str]:
    """Newest timestamped adapter_settings_backup_*.json, if any."""
    files = _backup_files()
    return max(files, key=os.path.getmtime) if files else None


def _prune_backups(keep: int = 5):
    try:
        files = _backup_files()
        for path in sorted(files, key=os.path.getmtime, reverse=True)[keep:]:
            os.remove(path)
    except Exception:
        pass


def snapshot_system_settings(adapter_name: Optional[str] = None) -> Optional[str]:
    """
    Record every non-registry change the optimizer can make (DNS, wireless power
    plan index, TCP autotuning/ECN) so "Restore Defaults" can actually undo it.
    """
    if _suppress_mutations():
        return None
    name = adapter_name or detect_wifi_interface_name() or "Wi-Fi"
    # Keep the first pre-change snapshot. Overwriting it on a second optimize
    # would capture already-optimized values and break Restore Defaults.
    if os.path.exists(SYSTEM_SNAPSHOT_FILE):
        logger.info("Keeping existing pre-change system snapshot: %s",
                    SYSTEM_SNAPSHOT_FILE)
        return SYSTEM_SNAPSHOT_FILE
    data = {
        "created": time.time(),
        "adapter": name,
        "dns_v4": get_current_dns(name),
        "power_scheme": _active_power_scheme(),
        "wireless_power": _query_wireless_power(),
        "tcp": get_tcp_state(),
    }
    try:
        with open(SYSTEM_SNAPSHOT_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        logger.info("System settings snapshot written to %s", SYSTEM_SNAPSHOT_FILE)
        return SYSTEM_SNAPSHOT_FILE
    except Exception as e:
        logger.warning("Could not write system snapshot: %s", e)
        return None


def restore_system_settings(snapshot_path: Optional[str] = None) -> List[str]:
    """Undo DNS / wireless power plan / TCP changes captured by the snapshot."""
    path = snapshot_path or SYSTEM_SNAPSHOT_FILE
    log: List[str] = []
    if not os.path.exists(path):
        return ["No system snapshot to restore."]
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        return [f"Could not read system snapshot: {e}"]

    if _suppress_mutations():
        return ["[simulated] System settings restore"]

    name = data.get("adapter") or detect_wifi_interface_name() or "Wi-Fi"

    # DNS
    dns = data.get("dns_v4")
    if dns is not None:
        try:
            if not dns:
                ok, _ = run_netsh(["interface", "ipv4", "set", "dnsservers",
                                   f"name={name}", "dhcp"])
                log.append(f"DNS -> DHCP ({name}): {'ok' if ok else 'failed'}")
            else:
                ok, _ = run_netsh(["interface", "ipv4", "set", "dnsservers",
                                   f"name={name}", "static", dns[0], "primary"])
                for i, server in enumerate(dns[1:], start=2):
                    ok2, _ = run_netsh(["interface", "ipv4", "add", "dnsservers",
                                        f"name={name}", server, f"index={i}"])
                    ok = ok and ok2
                log.append(f"DNS -> {', '.join(dns)}: {'ok' if ok else 'failed'}")
            flush_dns()
        except Exception as e:
            log.append(f"DNS restore failed: {e}")

    # Wireless power plan
    scheme = data.get("power_scheme")
    wp = data.get("wireless_power") or {}
    if scheme and ("ac" in wp or "dc" in wp):
        try:
            rcs = []
            if "ac" in wp:
                rcs.append(subprocess.run(
                    ["powercfg", "/setacvalueindex", scheme, WIRELESS_SUBGROUP,
                     WIRELESS_SETTING, str(wp["ac"])],
                    capture_output=True, timeout=10).returncode)
            if "dc" in wp:
                rcs.append(subprocess.run(
                    ["powercfg", "/setdcvalueindex", scheme, WIRELESS_SUBGROUP,
                     WIRELESS_SETTING, str(wp["dc"])],
                    capture_output=True, timeout=10).returncode)
            rcs.append(subprocess.run(
                ["powercfg", "/setactive", scheme],
                capture_output=True, timeout=10).returncode)
            if any(rc != 0 for rc in rcs):
                log.append(f"Power plan restore failed (powercfg rc={rcs})")
            else:
                log.append(f"Wireless power plan restored (AC={wp.get('ac')}, DC={wp.get('dc')})")
        except Exception as e:
            log.append(f"Power plan restore failed: {e}")

    # TCP
    tcp = data.get("tcp") or {}
    try:
        if "AutoTuningLevelLocal" in tcp:
            level = _TCP_AUTOTUNING.get(int(tcp["AutoTuningLevelLocal"]))
            if level:
                ok, _ = run_netsh(["interface", "tcp", "set", "global",
                                   f"autotuninglevel={level}"])
                log.append(f"TCP autotuning -> {level}: {'ok' if ok else 'failed'}")
        if "EcnCapability" in tcp:
            mode = "enabled" if int(tcp["EcnCapability"]) == 1 else "disabled"
            ok, _ = run_netsh(["interface", "tcp", "set", "global",
                               f"ecncapability={mode}"])
            log.append(f"TCP ECN -> {mode}: {'ok' if ok else 'failed'}")
        if "Timestamps" in tcp:
            mode = "enabled" if int(tcp["Timestamps"]) == 1 else "disabled"
            ok, _ = run_netsh(["interface", "tcp", "set", "global",
                               f"timestamps={mode}"])
            log.append(f"TCP timestamps -> {mode}: {'ok' if ok else 'failed'}")
    except Exception as e:
        log.append(f"TCP restore failed: {e}")

    return log


def backup_adapter_settings(adapter_name: Optional[str] = None) -> Optional[str]:
    """
    Safety net: export the CURRENT value of every advanced property for every
    active WiFi adapter to a *timestamped* JSON file BEFORE any change, plus a
    non-registry system snapshot (DNS / power plan / TCP).

    The old implementation overwrote one fixed file, so a second "optimize"
    replaced the pre-optimization backup with already-optimized values. Fixing
    that makes Restore Defaults trustworthy.
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

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(os.path.dirname(BACKUP_FILE),
                        f"adapter_settings_backup_{stamp}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(backup, f, indent=2)
        _prune_backups(keep=5)
        logger.info("Adapter settings backup written to %s (%d adapters)",
                    path, len(backup["adapters"]))
    except Exception as e:
        logger.warning("Could not write adapter settings backup: %s", e)
        return None

    snapshot_system_settings(adapter_name)
    return path


def restore_from_backup(backup_path: Optional[str] = None) -> Tuple[bool, List[str]]:
    """
    Restore adapter advanced properties from a full backup file created by
    backup_adapter_settings(). Independent of the per-change snapshot.
    """
    path = backup_path or _latest_backup_file()
    if not path or not os.path.exists(path):
        return False, [
            "No timestamped adapter backup found. Use Restore Defaults (which "
            "replays the per-change snapshot) or create a new backup first."
        ]
    try:
        with open(path, "r", encoding="utf-8") as f:
            backup = json.load(f)
    except Exception as e:
        return False, [f"Could not read backup: {e}"]

    log: List[str] = []
    had_error = False
    active_subkeys = _current_subkeys_or_none()
    for subkey, info in backup.get("adapters", {}).items():
        if active_subkeys is not None and subkey not in active_subkeys:
            log.append(f"Skipped stale adapter subkey {subkey} (not present)")
            continue
        for prop, value in (info.get("values") or {}).items():
            if _suppress_mutations():
                label = "PREVIEW" if _PREVIEW_MODE else "DRY-RUN"
                log.append(f"[{label}] Restore {prop}={value} on {subkey}")
                continue
            try:
                adapter_path = f"{NETWORK_ADAPTERS_KEY}\\{subkey}"
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, adapter_path, 0,
                                    winreg.KEY_READ | winreg.KEY_WRITE) as key:
                    winreg.SetValueEx(key, prop, 0, winreg.REG_SZ, str(value))
                    log.append(f"Restored {prop}={value} on {subkey}")
            except Exception as e:
                had_error = True
                log.append(f"Failed to restore {prop} on {subkey}: {e}")
    return (not had_error), log


def rollback_settings(snapshot_path: Optional[str] = None) -> Tuple[bool, List[str]]:
    """
    Atomic rollback of all applied adapter modifications.
    Restores original registry values and deletes injected keys.

    Returns (ok, log). `ok` is False if any registry write failed; in that case
    the snapshot is kept so the user can retry after fixing permissions.
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

    # No registry changes, but the optimizer may still have changed DNS, the
    # wireless power plan, or TCP. Restore those too instead of returning early.
    if not snapshots_to_restore:
        if _suppress_mutations():
            return True, ["[simulated] No registry changes to roll back."]
        sys_log = restore_system_settings()
        if not sys_log or sys_log == ["No system snapshot to restore."]:
            return True, ["No previous settings to roll back."]
        sys_ok = not any("failed" in s.lower() for s in sys_log)
        if sys_ok:
            try:
                os.remove(SYSTEM_SNAPSHOT_FILE)
            except Exception:
                pass
        return sys_ok, sys_log

    restored_log: List[str] = []
    had_error = False
    active_subkeys = _current_subkeys_or_none()
    # Process in reverse order of application
    for entry in reversed(snapshots_to_restore):
        subkey_name = entry["subkey"]
        prop = entry["property"]
        orig = entry.get("original_value")
        existed = entry.get("existed", False)

        if active_subkeys is not None and subkey_name not in active_subkeys:
            restored_log.append(f"Skipped stale adapter subkey {subkey_name} ({prop})")
            continue

        if _suppress_mutations():
            label = "PREVIEW" if _PREVIEW_MODE else "DRY-RUN"
            action = f"Restored {prop} -> {orig}" if existed else f"Deleted injected {prop}"
            restored_log.append(f"[{label}] {subkey_name}: {action}")
            if _DRY_RUN and subkey_name in _MOCK_REGISTRY:
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
            had_error = True
            logger.error("Error rolling back %s on %s: %s", prop, subkey_name, e)
            restored_log.append(f"Failed to roll back {prop}: {e}")

    # Clear the snapshot only if every registry write succeeded. Otherwise keep
    # it so Restore Defaults can be retried (for example after elevation).
    if not had_error:
        _SESSION_SNAPSHOTS.clear()
        if not _suppress_mutations() and os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass

    # The old "Restore Defaults" only replayed the adapter registry snapshot and
    # left DNS / power-plan / TCP changes behind. Undo them too, then reload the
    # adapter so the restored registry values actually take effect.
    system_ok = True
    if not _suppress_mutations():
        sys_log = restore_system_settings()
        restored_log.extend(sys_log)
        system_ok = not any("failed" in s.lower() for s in sys_log)
        if system_ok:
            try:
                os.remove(SYSTEM_SNAPSHOT_FILE)
            except Exception:
                pass
        if not had_error:
            name = detect_wifi_interface_name()
            ok, msg = restart_adapter(name, wait_seconds=20)
            restored_log.append(msg)

    return (not had_error and system_ok), restored_log


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
            existed, current = _read_current_value(subkey_name, prop_name)
            _record_preview(subkey_name, prop_name, effective_value, ad["desc"])
            _note("changed" if (not existed or current != str(effective_value)) else "noop",
                  adapter=ad["desc"], subkey=subkey_name, property=prop_name,
                  intent=prop_name, old=current if existed else None, new=effective_value)
            success = True
            continue

        # Dry run branch
        if _DRY_RUN:
            mock_adapter = _MOCK_REGISTRY.setdefault(subkey_name, {})
            existed = prop_name in mock_adapter
            orig_val = mock_adapter.get(prop_name)
            _record_snapshot_entry(subkey_name, prop_name, orig_val, existed, effective_value)
            mock_adapter[prop_name] = effective_value
            _note("changed" if (not existed or orig_val != str(effective_value)) else "noop",
                  adapter=ad["desc"], subkey=subkey_name, property=prop_name,
                  intent=prop_name, old=orig_val, new=effective_value)
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

                if existed and str(orig_val) == str(effective_value):
                    logger.info("%s=%s already set for %s (%s). No change.",
                                prop_name, effective_value, subkey_name, ad["desc"])
                    _note("noop", adapter=ad["desc"], subkey=subkey_name,
                          property=prop_name, intent=prop_name,
                          old=orig_val, new=effective_value)
                    success = True
                    continue

                winreg.SetValueEx(key, prop_name, 0, winreg.REG_SZ, str(effective_value))
                _record_snapshot_entry(subkey_name, prop_name, orig_val, existed, str(effective_value))
                read_back = winreg.QueryValueEx(key, prop_name)[0]
                if str(read_back) != str(effective_value):
                    logger.warning("Read-back mismatch for %s on %s: wrote %r, got %r",
                                   prop_name, subkey_name, effective_value, read_back)
                    _note("failed", adapter=ad["desc"], subkey=subkey_name,
                          property=prop_name, intent=prop_name,
                          reason=f"read-back mismatch ({read_back!r})")
                    continue
                _note("changed", adapter=ad["desc"], subkey=subkey_name,
                      property=prop_name, intent=prop_name,
                      old=orig_val, new=effective_value)
                logger.info("Successfully set %s=%s for %s (miniport link will reinitialize)",
                            prop_name, effective_value, ad["desc"])
                success = True
        except PermissionError:
            logger.warning("Access denied setting %s on %s. Run as Administrator.", prop_name, ad["desc"])
            _note("failed", adapter=ad["desc"], subkey=subkey_name, property=prop_name,
                  intent=prop_name, reason="access denied (run as administrator)")
        except Exception as e:
            logger.error("Error setting %s on %s: %s", prop_name, ad["desc"], e)
            _note("failed", adapter=ad["desc"], subkey=subkey_name, property=prop_name,
                  intent=prop_name, reason=str(e))

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


def set_channel_width(band: str, width: str, target_guid: Optional[str] = None) -> bool:
    """
    Tier A: Force a narrower channel width.

    At long range a 20 MHz channel concentrates the same transmit power into
    half/quarter of the spectrum, which usually raises SNR per subcarrier and
    cuts retries. It cannot create signal, but it often turns a 2-bar link from
    unusable into usable. Width is applied only when the driver exposes the
    corresponding NDIS keyword.
    """
    intent = "channel_width_24" if str(band).startswith("2.4") else "channel_width_52"
    semantic = "20MHz" if str(width).lower().replace(" ", "") in ("20", "20mhz") else "auto"
    return apply_intent(intent, semantic, target_guid)


def _active_power_scheme() -> Optional[str]:
    if _suppress_mutations():
        return "SCHEME_CURRENT"
    try:
        res = subprocess.run(
            ["powercfg", "/getactivescheme"], capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        m = re.search(r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})",
                      res.stdout or "")
        if m:
            return m.group(1)
    except Exception as e:
        logger.debug("powercfg scheme query failed: %s", e)
    return None


def _query_wireless_power() -> Optional[Dict[str, int]]:
    """Read the current AC/DC wireless power-saving index (0=max perf .. 3=max saving)."""
    try:
        res = subprocess.run(
            ["powercfg", "/query", "SCHEME_CURRENT", WIRELESS_SUBGROUP, WIRELESS_SETTING],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        if res.returncode != 0:
            return None
        vals = re.findall(r"0x([0-9a-fA-F]+)", res.stdout or "")
        if len(vals) >= 2:
            return {"ac": int(vals[-2], 16), "dc": int(vals[-1], 16)}
    except Exception as e:
        logger.debug("powercfg query failed: %s", e)
    return None


def _set_wireless_power(index: int) -> bool:
    if _suppress_mutations():
        return True
    try:
        cmds = [
            ["powercfg", "/setacvalueindex", "SCHEME_CURRENT", WIRELESS_SUBGROUP, WIRELESS_SETTING, str(index)],
            ["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", WIRELESS_SUBGROUP, WIRELESS_SETTING, str(index)],
            ["powercfg", "/setactive", "SCHEME_CURRENT"],
        ]
        for cmd in cmds:
            res = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10,
                creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
            if res.returncode != 0:
                return False
        # Read the value back. A zero exit code is not proof that the active
        # scheme accepted the change.
        check = _query_wireless_power()
        if check and check.get("ac") == index and check.get("dc") == index:
            return True
        logger.warning("powercfg did not read back AC/DC=%s (got %s)", index, check)
        return False
    except Exception as e:
        logger.error("Failed to set wireless power plan: %s", e)
        return False


def set_power_saving(disable: bool = True, target_guid: Optional[str] = None) -> bool:
    """
    Tier A: Set the Windows wireless power plan (AC and DC) and the driver MIMO
    power-save mode.

    `disable=True` means "maximum performance" (index 0); `False` means the
    battery-first profile (index 3, maximum power saving). Unlike the old
    implementation this:
      * uses both AC and DC indices (laptops on battery were ignored),
      * detects the real interface name (the old code hardcoded "Wi-Fi"),
      * never runs a localized netsh command that silently did nothing,
      * reports whether the value actually changed.
    """
    target = 0 if disable else 3
    label = "Wireless Power Plan (max performance)" if disable else "Wireless Power Plan (max saving)"
    current = _query_wireless_power()
    if current and current.get("ac") == target and current.get("dc") == target:
        _note("noop", adapter="system", property="powercfg", intent=label,
              old=current, new=target)
        power_ok = True
        logger.info("%s already set (AC/DC=%s). No change.", label, target)
    else:
        if _PREVIEW_MODE:
            _PREVIEW_CHANGES.append({
                "subkey": "-",
                "adapter": "system",
                "property": "Wireless power plan",
                "intent": "Power plan",
                "current": (f"AC {current.get('ac')} / DC {current.get('dc')}"
                            if current else "unknown"),
                "proposed": f"AC {target} / DC {target}",
                "changed": True,
            })
        power_ok = _set_wireless_power(target)
        _note("changed" if power_ok else "failed", adapter="system", property="powercfg",
              intent=label, old=current, new=target,
              reason=None if power_ok else "powercfg returned an error (administrator required?)")

    mimo_ok = apply_intent("mimo_power_save", "disable" if disable else "enable", target_guid)
    return bool(power_ok or mimo_ok)


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


def get_current_dns(adapter_name: Optional[str] = None) -> Optional[List[str]]:
    """Return the interface's configured IPv4 DNS servers (locale-independent)."""
    name = adapter_name or detect_wifi_interface_name() or "Wi-Fi"
    ps = (f"(Get-DnsClientServerAddress -InterfaceAlias {_ps_quote(name)} "
          "-AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses")
    ok, out = run_powershell(ps)
    if not ok:
        return None
    return [line.strip() for line in out.replace(",", "\n").splitlines() if line.strip()]


def set_dns_servers(primary: str = "1.1.1.1", secondary: str = "8.8.8.8",
                    adapter_name: Optional[str] = None) -> bool:
    """Tier A: Set validated DNS resolvers on the real Wi-Fi interface."""
    name = adapter_name or detect_wifi_interface_name() or "Wi-Fi"
    desired = [primary] + ([secondary] if secondary else [])

    if _suppress_mutations():
        # Preview/dry-run: read the real current DNS (read-only) so the preview
        # does not claim a change that already exists.
        current = get_current_dns(name)
        changed = current is None or current[:len(desired)] != desired
        if _PREVIEW_MODE and changed:
            _PREVIEW_CHANGES.append({
                "subkey": "-", "adapter": name, "property": "DNS servers",
                "intent": "DNS",
                "current": " / ".join(current) if current else "(DHCP)",
                "proposed": " / ".join(desired),
                "changed": True,
            })
        _note("changed" if changed else "noop", adapter=name, property="DNS",
              intent="DNS servers", old=current, new=desired)
        logger.info("[%s] DNS %s to %s",
                    "PREVIEW" if _PREVIEW_MODE else "DRY-RUN",
                    "would change" if changed else "already", " / ".join(desired))
        return True

    current = get_current_dns(name)
    if current is not None and current[:len(desired)] == desired:
        _note("noop", adapter=name, property="DNS", intent="DNS servers",
              old=current, new=desired)
        logger.info("DNS already set to %s. No change.", " / ".join(desired))
        return True

    try:
        ok1, out1 = run_netsh(["interface", "ipv4", "set", "dnsservers",
                               f"name={name}", "static", primary, "primary"])
        ok2 = True
        if secondary:
            ok2, _ = run_netsh(["interface", "ipv4", "add", "dnsservers",
                                f"name={name}", secondary, "index=2"])
        if not (ok1 and ok2):
            _note("failed", adapter=name, property="DNS", intent="DNS servers",
                  old=current, new=desired,
                  reason=out1.strip()[:200] or "netsh failed")
            return False
        # Read back the configured resolvers; a successful netsh exit code is
        # not proof that the interface accepted them.
        time.sleep(0.3)
        verify = get_current_dns(name)
        if verify is None or verify[:len(desired)] != desired:
            _note("failed", adapter=name, property="DNS", intent="DNS servers",
                  old=current, new=desired,
                  reason=f"read-back mismatch ({verify})")
            return False
        _note("changed", adapter=name, property="DNS", intent="DNS servers",
              old=current, new=desired)
        return True
    except Exception as e:
        logger.error("Failed to set DNS: %s", e)
        _note("failed", adapter=name, property="DNS", intent="DNS servers", reason=str(e))
        return False


def reset_dns_to_dhcp(adapter_name: Optional[str] = None) -> bool:
    """Undo set_dns_servers() by returning the interface to DHCP-provided DNS."""
    name = adapter_name or detect_wifi_interface_name() or "Wi-Fi"
    if _suppress_mutations():
        return True
    ok, _ = run_netsh(["interface", "ipv4", "set", "dnsservers", f"name={name}", "dhcp"])
    if ok:
        flush_dns()
    return ok


# -------------------------------------------------------------
# Tier B: Advanced Stack Tuning & Latency Optimizations (Opt-In)
# -------------------------------------------------------------
# NOTE: Client-side settings CANNOT override router-mandated EDCA parameters
# (CWmin/CWmax/TXOP limit) defined by IEEE 802.11 standards.
# Short Guard Interval applies only to 802.11n (400ns) and 802.11ac (400ns) links.
# 802.11ax/be links use standard 0.8us/1.6us/3.2us GI negotiated in the HE Operation element.

_TCP_AUTOTUNING = {0: "disabled", 1: "highlyrestricted", 2: "restricted", 3: "normal", 4: "experimental"}


def get_tcp_state() -> Optional[Dict[str, int]]:
    """Read the active TCP template values as numeric enums (locale-independent)."""
    ps = ("Get-NetTCPSetting -SettingName Internet -ErrorAction SilentlyContinue | "
          "Select-Object -First 1 -Property AutoTuningLevelLocal,EcnCapability,Timestamps | "
          "ConvertTo-Json -Compress")
    ok, out = run_powershell(ps)
    if not ok or not out:
        return None
    try:
        data = json.loads(out)
        return {k: int(v) for k, v in data.items() if isinstance(v, (int, float))}
    except Exception:
        return None


def optimize_tcp_settings(adapter_name: Optional[str] = None, ecn: bool = False) -> bool:
    """
    Tier B: Bring a few TCP stack values to the intended state, skipping writes
    that would be no-ops.

    Honest scope:
      * autotuning=normal is the Windows default; it is applied only if a prior
        tool changed it.
      * ECN is opt-in. It can reduce loss recovery latency only when the path
        supports it; otherwise it is neutral. It is not a signal booster.
      * RSS is intentionally NOT touched: on a single Wi-Fi flow it does not
        change airtime or range.
    """
    state = get_tcp_state()

    if _suppress_mutations():
        at_now = (None if state is None
                  else _TCP_AUTOTUNING.get(state.get("AutoTuningLevelLocal", -1)))
        if at_now == "normal":
            _note("noop", adapter="system", property="TCP autotuning",
                  intent="TCP autotuning", old="normal", new="normal")
        else:
            _note("changed", adapter="system", property="TCP autotuning",
                  intent="TCP autotuning", old=at_now, new="normal")
            if _PREVIEW_MODE:
                _PREVIEW_CHANGES.append({
                    "subkey": "-", "adapter": "system", "property": "TCP autotuning",
                    "intent": "TCP", "current": str(at_now), "proposed": "normal",
                    "changed": True})
        if ecn:
            ecn_now = None if state is None else state.get("EcnCapability")
            if ecn_now == 1:
                _note("noop", adapter="system", property="TCP ECN",
                      intent="TCP ECN", old="enabled", new="enabled")
            else:
                _note("changed", adapter="system", property="TCP ECN",
                      intent="TCP ECN", old="disabled" if ecn_now == 0 else None,
                      new="enabled")
                if _PREVIEW_MODE:
                    _PREVIEW_CHANGES.append({
                        "subkey": "-", "adapter": "system", "property": "TCP ECN",
                        "intent": "TCP", "current": "disabled" if ecn_now == 0 else "unknown",
                        "proposed": "enabled", "changed": True})
        return True

    ok_all = True
    at_now = None if state is None else _TCP_AUTOTUNING.get(state.get("AutoTuningLevelLocal", -1))
    if at_now != "normal":
        ok, out = run_netsh(["interface", "tcp", "set", "global", "autotuninglevel=normal"])
        if ok:
            time.sleep(0.2)
            after = get_tcp_state()
            got = (None if after is None
                   else _TCP_AUTOTUNING.get(after.get("AutoTuningLevelLocal", -1)))
            if got != "normal":
                _note("failed", adapter="system", property="TCP autotuning",
                      intent="TCP autotuning", old=at_now, new="normal",
                      reason=f"read-back mismatch ({got})")
                ok = False
            else:
                _note("changed", adapter="system", property="TCP autotuning",
                      intent="TCP autotuning", old=at_now, new="normal")
        else:
            _note("failed", adapter="system", property="TCP autotuning",
                  intent="TCP autotuning", old=at_now, new="normal",
                  reason=out.strip()[:200])
        ok_all = ok_all and ok
    else:
        _note("noop", adapter="system", property="TCP autotuning",
              intent="TCP autotuning", old="normal", new="normal")

    if ecn:
        ecn_now = None if state is None else state.get("EcnCapability")
        if ecn_now == 1:
            _note("noop", adapter="system", property="TCP ECN", intent="TCP ECN",
                  old="enabled", new="enabled")
        else:
            ok, out = run_netsh(["interface", "tcp", "set", "global", "ecncapability=enabled"])
            if ok:
                time.sleep(0.2)
                after = get_tcp_state()
                got = None if after is None else after.get("EcnCapability")
                if got != 1:
                    _note("failed", adapter="system", property="TCP ECN",
                          intent="TCP ECN", old="disabled" if ecn_now == 0 else None,
                          new="enabled", reason=f"read-back mismatch ({got})")
                    ok = False
                else:
                    _note("changed", adapter="system", property="TCP ECN",
                          intent="TCP ECN", old="disabled" if ecn_now == 0 else None,
                          new="enabled")
            else:
                _note("failed", adapter="system", property="TCP ECN",
                      intent="TCP ECN", old="disabled" if ecn_now == 0 else None,
                      new="enabled", reason=out.strip()[:200])
            ok_all = ok_all and ok

    return ok_all


def apply_advanced_tier_b_tweaks(enable_packet_coalescing_disable: bool = False,
                                 target_guid: Optional[str] = None,
                                 connection_phy: Optional[str] = None,
                                 channel_width_24: Optional[str] = None,
                                 channel_width_52: Optional[str] = None,
                                 disable_uapsd: bool = True) -> List[str]:
    """
    Tier B (Advanced / Opt-In): optional driver-level latency/robustness tweaks.

    Every item is resolved against the driver's own Ndi\\params and silently
    skipped when unsupported, so this never guesses a vendor code. Short GI is
    gated on the active PHY (802.11ax/be negotiate GI in HE Operation and do not
    use the legacy keyword).
    """
    applied = []

    # Priority / VLAN Tagging (802.1p Layer-2 frame tagging)
    if apply_intent("priority_vlan_tag", "on", target_guid):
        applied.append("QoS / Priority Tagging (802.1p) -> Enabled")

    # Channel width is the one client-side lever that materially changes SNR at
    # long range: 20 MHz concentrates power instead of spreading it over 40/80.
    if channel_width_24:
        if set_channel_width("2.4GHz", channel_width_24, target_guid):
            applied.append(f"2.4 GHz width -> {channel_width_24}")
    if channel_width_52:
        if set_channel_width("5GHz", channel_width_52, target_guid):
            applied.append(f"5 GHz width -> {channel_width_52}")

    # Short Guard Interval: evaluated against active connection PHY
    if connection_phy and any(k in connection_phy.lower() for k in ["ax", "be", "wi-fi 6", "wifi 6"]):
        logger.info("Active connection PHY is %s; skipping legacy SGI (802.11ax uses 0.8/1.6/3.2us GI).", connection_phy)
    else:
        for prop in ["*ShortGI20MHz", "*ShortGI40MHz", "*ShortGI80MHz"]:
            if _set_adapter_registry_value_safe(prop, "1", target_guid=target_guid, verify_ndi_support=True):
                applied.append(f"{prop.replace('*', '')} -> Enabled (HT/VHT SGI 400ns)")

    # Packet Coalescing (trades battery idle states for lower jitter)
    if enable_packet_coalescing_disable:
        if apply_intent("packet_coalescing", "off", target_guid):
            applied.append("Packet Coalescing -> Disabled")

    # Vendor-specific throughput bursting (Intel Throughput Booster, etc.)
    if apply_intent("throughput_booster", "on", target_guid):
        applied.append("Throughput Booster -> Enabled")

    # U-APSD off reduces latency at the cost of battery. It does NOT give the
    # client priority over other stations (the old "selfish airtime" claim).
    if disable_uapsd and apply_intent("uapsd", "off", target_guid):
        applied.append("U-APSD -> Disabled (lower latency, higher battery use)")

    return applied


def apply_latency_tweaks(target_guid: Optional[str] = None,
                         connection_phy: Optional[str] = None) -> List[str]:
    """Honest alias for the opt-in Tier B bundle (formerly 'selfish airtime')."""
    return apply_advanced_tier_b_tweaks(enable_packet_coalescing_disable=True,
                                        target_guid=target_guid,
                                        connection_phy=connection_phy)


def restart_adapter(adapter_name: Optional[str] = None,
                    wait_seconds: int = 25) -> Tuple[bool, str]:
    """
    Disable/enable the Wi-Fi adapter and wait for reconnection.

    Registry values for NDIS keywords are read by the miniport when it
    initializes. Without this step most "applied" changes do nothing until the
    next reboot, which is why the old app could claim success while the link was
    unchanged.
    """
    name = adapter_name or detect_wifi_interface_name() or "Wi-Fi"
    if _suppress_mutations():
        logger.info("[%s] Adapter %s restart simulated",
                    "PREVIEW" if _PREVIEW_MODE else "DRY-RUN", name)
        return True, f"[simulated] Adapter '{name}' restart"

    # Ask the supplicant to drop the link first; otherwise some drivers keep the
    # old association alive across a disable/enable cycle.
    try:
        run_netsh(["wlan", "disconnect", f"interface={name}"])
    except Exception:
        pass

    ok_down, out_down = run_netsh(["interface", "set", "interface", name, "admin=disabled"])
    time.sleep(1.5)
    ok_up, out_up = run_netsh(["interface", "set", "interface", name, "admin=enabled"])

    if not (ok_down and ok_up):
        # netsh can fail on localized/mismatched names; try the cmdlet once.
        ps = f"Restart-NetAdapter -Name {_ps_quote(name)} -Confirm:$false -ErrorAction Stop"
        ok_ps, _ = run_powershell(ps, timeout=30)
        if not ok_ps:
            return False, (f"Could not restart '{name}'. Run as administrator, or toggle "
                           f"Wi-Fi off/on once. ({out_down.strip()[:120]})")

    deadline = time.time() + max(5, int(wait_seconds))
    while time.time() < deadline:
        conn = wlan_native.get_connection()
        if conn:
            return True, (f"Adapter '{name}' restarted and reconnected to "
                          f"'{conn.get('ssid', '?')}' "
                          f"({conn.get('rssi_dbm', 0):.0f} dBm, "
                          f"{conn.get('rx_mbps', 0):.0f} Mbps rx).")
        time.sleep(1.5)

    return False, (f"Adapter '{name}' restarted but did not reconnect within "
                   f"{wait_seconds}s. Windows will keep trying; no settings were lost.")


def reset_wifi_adapter(adapter_name: Optional[str] = None) -> bool:
    """Backward-compatible bool wrapper around restart_adapter()."""
    ok, _ = restart_adapter(adapter_name)
    return ok
