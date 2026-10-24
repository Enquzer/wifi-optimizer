"""
WiFi Optimizer - Native Wi-Fi telemetry (wlanapi.dll)
=====================================================
Locale-independent replacement for parsing localized `netsh wlan` text.

All data here comes from the Windows Native Wi-Fi API, whose fields are
language-neutral:
  * WlanEnumInterfaces        -> interface GUIDs / descriptions
  * WlanQueryInterface        -> SSID, BSSID, RSSI, link quality, link rates,
                                 PHY type, auth/cipher of the live association
  * WlanGetNetworkBssList     -> every BSS (per-BSSID) with real dBm RSSI,
                                 center frequency, capability bits and the raw
                                 information elements (RSN/WPA => security)

Struct layouts are pinned to wlanapi.h (Windows 10/11). The BSS entry stride is
taken from WlanGetNetworkBssList's own dwTotalSize/dwNumberOfItems, so even a
future layout change cannot silently corrupt the parse.

If anything here is unavailable (non-Windows, service stopped, no adapter),
callers fall back to the legacy netsh parser.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────────────
WLAN_INTF_OPCODE_CURRENT_CONNECTION = 7
DOT11_BSS_TYPE_ANY = 3

DOT11_PHY_NAMES = {
    0: "", 1: "FHSS", 2: "DSSS", 3: "IR",
    4: "802.11a", 5: "802.11b", 6: "802.11g",
    7: "802.11n", 8: "802.11ac", 9: "802.11ad",
    10: "802.11ax", 11: "802.11be",
}

# AKM suite selector -> (auth label, enterprise?)
_AKM = {
    1: ("WPA2-Enterprise", True),
    2: ("WPA2-Personal", False),
    3: ("WPA2-Enterprise", True),
    4: ("WPA2-Personal", False),
    5: ("WPA2-Enterprise", True),
    6: ("WPA2-Personal", False),
    8: ("WPA3-Personal", False),
    9: ("WPA3-Personal", False),
    11: ("WPA3-Enterprise", True),
    18: ("OWE", False),
    24: ("WPA3-Personal", False),
}

# Group/pairwise cipher selector -> label
_CIPHER = {0: "None", 1: "WEP-40", 2: "TKIP", 4: "CCMP", 5: "WEP-104",
           8: "GCMP-128", 9: "GCMP-256"}

_RSN_OUI = b"\x00\x0f\xac"
_MS_OUI = b"\x00\x50\xf2"


# ── Structures ───────────────────────────────────────────────────────────────
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wt.DWORD), ("Data2", wt.WORD), ("Data3", wt.WORD),
                ("Data4", ctypes.c_ubyte * 8)]


class _DOT11_SSID(ctypes.Structure):
    _fields_ = [("uSSIDLength", wt.ULONG), ("ucSSID", ctypes.c_ubyte * 32)]


class _WLAN_RATE_SET(ctypes.Structure):
    _fields_ = [("uRateSetLength", wt.ULONG),
                ("usRateSet", wt.USHORT * 126)]


class _WLAN_BSS_ENTRY(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", _DOT11_SSID),
        ("uPhyId", wt.ULONG),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11BssType", ctypes.c_int),
        ("dot11BssPhyType", ctypes.c_int),
        ("lRssi", ctypes.c_long),
        ("uLinkQuality", wt.ULONG),
        ("bInRegDomain", ctypes.c_ubyte),
        ("usBeaconPeriod", wt.USHORT),
        ("ullTimestamp", ctypes.c_ulonglong),
        ("ullHostTimestamp", ctypes.c_ulonglong),
        ("usCapabilityInformation", wt.USHORT),
        ("ulChCenterFrequency", wt.ULONG),
        ("wlanRateSet", _WLAN_RATE_SET),
        ("ulIeOffset", wt.ULONG),
        ("ulIeSize", wt.ULONG),
    ]


class _WLAN_BSS_LIST(ctypes.Structure):
    _fields_ = [("dwTotalSize", wt.DWORD),
                ("dwNumberOfItems", wt.DWORD),
                ("wlanBssEntries", _WLAN_BSS_ENTRY * 1)]


class _WLAN_INTERFACE_INFO(ctypes.Structure):
    _fields_ = [("InterfaceGuid", _GUID),
                ("strInterfaceDescription", ctypes.c_wchar * 256),
                ("isState", ctypes.c_uint)]


class _WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
    _fields_ = [("dwNumberOfItems", ctypes.c_uint),
                ("dwIndex", ctypes.c_uint),
                ("InterfaceInfo", _WLAN_INTERFACE_INFO * 1)]


class _WLAN_ASSOCIATION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("dot11Ssid", _DOT11_SSID),
        ("dot11BssType", ctypes.c_int),
        ("dot11Bssid", ctypes.c_ubyte * 6),
        ("dot11PhyType", ctypes.c_int),
        ("uDot11PhyIndex", wt.ULONG),
        ("wlanSignalQuality", wt.ULONG),
        ("ulRxRate", wt.ULONG),
        ("ulTxRate", wt.ULONG),
    ]


class _WLAN_SECURITY_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("bSecurityEnabled", ctypes.c_int),
                ("bOneXEnabled", ctypes.c_int),
                ("dot11AuthAlgorithm", ctypes.c_int),
                ("dot11CipherAlgorithm", ctypes.c_int)]


class _WLAN_CONNECTION_ATTRIBUTES(ctypes.Structure):
    _fields_ = [("isState", ctypes.c_int),
                ("wlanConnectionMode", ctypes.c_int),
                ("strProfileName", ctypes.c_wchar * 256),
                ("wlanAssociationAttributes", _WLAN_ASSOCIATION_ATTRIBUTES),
                ("wlanSecurityAttributes", _WLAN_SECURITY_ATTRIBUTES),
                ("wlanReasonCode", wt.ULONG)]


# ── Small helpers ────────────────────────────────────────────────────────────
def _mac_str(raw: bytes) -> str:
    return ":".join(f"{b:02X}" for b in raw)


def _ssid_str(raw: bytes, length: int) -> str:
    try:
        return raw[:max(0, min(length, 32))].decode("utf-8", errors="replace")
    except Exception:
        return ""


def freq_to_channel(freq_khz: float) -> Tuple[int, str]:
    """Center frequency in kHz -> (channel number, band label)."""
    if not freq_khz:
        return 0, "2.4GHz"
    f = freq_khz / 1000.0  # MHz
    if 2412 <= f <= 2484:
        if f == 2484:
            return 14, "2.4GHz"
        return int(round((f - 2407) / 5)), "2.4GHz"
    if 4910 <= f <= 5895:
        return int(round((f - 5000) / 5)), "5GHz"
    if 5925 <= f <= 7125:
        return int(round((f - 5950) / 5)), "6GHz"
    if 57000 <= f <= 71000:
        return int(round((f - 56160) / 2160)), "60GHz"
    return 0, "2.4GHz"


def phy_name(phy_type: int) -> str:
    return DOT11_PHY_NAMES.get(int(phy_type), "")


def rssi_from_quality(quality: int) -> float:
    """Legacy linear mapping documented for WLAN_SIGNAL_QUALITY."""
    q = max(0, min(100, int(quality)))
    return -100.0 + q / 2.0


# ── IE parsing (security) ────────────────────────────────────────────────────
def _parse_rsn_like(body: bytes, offset: int, is_vendor_wpa: bool):
    """Return (akms, ciphers, group_cipher) from an RSN/WPA IE body."""
    akms, ciphers = [], []
    group = 0
    try:
        if is_vendor_wpa:
            # body: OUI(3) type(1) then same layout as RSN
            if len(body) < 8:
                return akms, ciphers, group
            p = 4
        else:
            p = offset
        if p + 2 > len(body):
            return akms, ciphers, group
        p += 2  # version
        if p + 4 > len(body):
            return akms, ciphers, group
        group = body[p + 3]
        p += 4
        if p + 2 > len(body):
            return akms, ciphers, group
        n = int.from_bytes(body[p:p + 2], "little")
        p += 2
        for _ in range(min(n, 8)):
            if p + 4 > len(body):
                break
            if body[p:p + 3] == _RSN_OUI:
                ciphers.append(body[p + 3])
            p += 4
        if p + 2 > len(body):
            return akms, ciphers, group
        n = int.from_bytes(body[p:p + 2], "little")
        p += 2
        for _ in range(min(n, 8)):
            if p + 4 > len(body):
                break
            if body[p:p + 3] == _RSN_OUI:
                akms.append(body[p + 3])
            p += 4
    except Exception:
        pass
    return akms, ciphers, group


def classify_security(privacy: bool, ie_blob: bytes) -> Tuple[str, str, bool]:
    """
    Derive (authentication, encryption, is_open) from the Privacy capability
    bit plus the raw RSN (48) / vendor WPA (221) information elements.
    Returns ("Unknown", "Unknown", False) when a secured network has no
    parseable IE, so an unknown AP is never treated as open.
    """
    if not privacy:
        return "Open", "None", True

    has_rsn = has_wpa = False
    akms: List[int] = []
    ciphers: List[int] = []
    group = 0

    i = 0
    n = len(ie_blob)
    while i + 2 <= n:
        eid = ie_blob[i]
        ln = ie_blob[i + 1]
        body = ie_blob[i + 2:i + 2 + ln]
        i += 2 + ln
        if eid == 48:  # RSN
            has_rsn = True
            a, c, g = _parse_rsn_like(body, 0, False)
            akms += a
            ciphers += c
            group = group or g
        elif eid == 221 and len(body) >= 4 and body[:3] == _MS_OUI and body[3] == 1:
            has_wpa = True
            a, c, g = _parse_rsn_like(body, 0, True)
            akms += a
            ciphers += c
            group = group or g

    cipher = _CIPHER.get(ciphers[0] if ciphers else group, "Unknown")

    if has_rsn:
        # Prefer the strongest AKM advertised.
        for want in (11, 8, 9, 24, 1, 3, 5, 2, 4, 6):
            if want in akms:
                return _AKM[want][0], cipher, False
        return "WPA2-Enterprise" if cipher != "CCMP" else "WPA2-Personal", cipher, False
    if has_wpa:
        ent = any(a in (1, 3, 5) for a in akms)
        return ("WPA-Enterprise" if ent else "WPA-Personal"), cipher, False
    # Privacy set but no RSN/WPA IE => legacy WEP (or unknown proprietary).
    return "WEP", "WEP", False


# ── API plumbing ─────────────────────────────────────────────────────────────
def _wlan():
    try:
        if not hasattr(ctypes, "windll"):
            return None
        return ctypes.windll.wlanapi
    except Exception:
        return None


def available() -> bool:
    return _wlan() is not None


class _Handle:
    def __init__(self):
        self.wlan = _wlan()
        self.handle = wt.HANDLE()
        self.negotiated = wt.DWORD()
        self.ok = False
        if not self.wlan:
            return
        try:
            if self.wlan.WlanOpenHandle(2, None, ctypes.byref(self.negotiated),
                                        ctypes.byref(self.handle)) == 0:
                self.ok = True
        except Exception as e:
            logger.debug("WlanOpenHandle failed: %s", e)

    def close(self):
        if self.ok:
            try:
                self.wlan.WlanCloseHandle(self.handle, None)
            except Exception:
                pass
            self.ok = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


def list_interfaces() -> List[dict]:
    """Return [{guid, description, state}] for every WLAN interface."""
    out: List[dict] = []
    with _Handle() as h:
        if not h.ok:
            return out
        p = ctypes.POINTER(_WLAN_INTERFACE_INFO_LIST)()
        if h.wlan.WlanEnumInterfaces(h.handle, None, ctypes.byref(p)) != 0 or not p:
            return out
        try:
            count = p.contents.dwNumberOfItems
            base = ctypes.addressof(p.contents) + 8
            stride = ctypes.sizeof(_WLAN_INTERFACE_INFO)
            for i in range(count):
                info = _WLAN_INTERFACE_INFO.from_address(base + i * stride)
                guid = info.InterfaceGuid
                out.append({
                    "guid": ("{%08X-%04X-%04X-%02X%02X-%02X%02X%02X%02X%02X%02X}" % (
                        guid.Data1, guid.Data2, guid.Data3,
                        guid.Data4[0], guid.Data4[1], guid.Data4[2], guid.Data4[3],
                        guid.Data4[4], guid.Data4[5], guid.Data4[6], guid.Data4[7])),
                    "description": info.strInterfaceDescription,
                    "state": int(info.isState),
                })
        finally:
            h.wlan.WlanFreeMemory(p)
    return out


def get_connection() -> Optional[dict]:
    """
    Locale-independent description of the live association, or None.

    Keys: guid, description, profile, ssid, bssid, rssi_dbm, link_quality,
          rx_mbps, tx_mbps, channel, band, frequency_mhz, phy, state
    """
    interfaces = list_interfaces()
    if not interfaces:
        return None

    with _Handle() as h:
        if not h.ok:
            return None
        for iface in interfaces:
            if iface["state"] != 1:  # wlan_interface_state_connected
                continue
            guid = _parse_guid(iface["guid"])
            if guid is None:
                continue
            size = wt.ULONG()
            data = ctypes.c_void_p()
            rc = h.wlan.WlanQueryInterface(
                h.handle, ctypes.byref(guid), WLAN_INTF_OPCODE_CURRENT_CONNECTION,
                None, ctypes.byref(size), ctypes.byref(data), None)
            if rc != 0 or not data.value:
                continue
            try:
                conn = ctypes.cast(
                    data, ctypes.POINTER(_WLAN_CONNECTION_ATTRIBUTES)).contents
                assoc = conn.wlanAssociationAttributes
                ssid = _ssid_str(bytes(assoc.dot11Ssid.ucSSID),
                                 assoc.dot11Ssid.uSSIDLength)
                bssid = _mac_str(bytes(assoc.dot11Bssid))
                freq_khz = 0.0
                rssi = 0.0
                privacy = 0
                ie_blob = b""
                for bss in scan_bss(fresh=False, handle=h.handle, guid=guid):
                    if bss["bssid"].upper() == bssid.upper():
                        freq_khz = bss["frequency_khz"]
                        rssi = bss["rssi_dbm"]
                        privacy = bss["privacy"]
                        ie_blob = bss["ie_blob"]
                        break
                channel, band = freq_to_channel(freq_khz)
                quality = int(assoc.wlanSignalQuality)
                if not (-110 <= rssi <= 0):
                    rssi = rssi_from_quality(quality)
                auth, cipher, _ = classify_security(bool(privacy), ie_blob)
                # For the *live* association the negotiated algorithms are the
                # truth; the BSS IE list can advertise a mixed cipher set.
                sec = conn.wlanSecurityAttributes
                if sec.bSecurityEnabled:
                    assoc_auth = _auth_enum(sec.dot11AuthAlgorithm)
                    assoc_cipher = _cipher_enum(sec.dot11CipherAlgorithm)
                    if assoc_auth != "Unknown":
                        auth = assoc_auth
                    if assoc_cipher != "Unknown":
                        cipher = assoc_cipher
                return {
                    "guid": iface["guid"],
                    "description": iface["description"],
                    "profile": conn.strProfileName,
                    "ssid": ssid,
                    "bssid": bssid,
                    "rssi_dbm": float(rssi),
                    "link_quality": quality,
                    "rx_mbps": float(assoc.ulRxRate) / 1000.0,
                    "tx_mbps": float(assoc.ulTxRate) / 1000.0,
                    "channel": channel,
                    "band": band,
                    "frequency_mhz": round(freq_khz / 1000.0, 1),
                    "phy": phy_name(assoc.dot11PhyType),
                    "authentication": auth,
                    "encryption": cipher,
                    "state": "connected",
                }
            finally:
                h.wlan.WlanFreeMemory(data)
    return None


def _parse_guid(text: str) -> Optional[_GUID]:
    try:
        t = text.strip("{}")
        parts = t.split("-")
        # Format is 8-4-4-4-12; Data4 is the last two groups.
        d4 = bytes.fromhex(parts[3] + parts[4])
        g = _GUID()
        g.Data1 = int(parts[0], 16)
        g.Data2 = int(parts[1], 16)
        g.Data3 = int(parts[2], 16)
        for i in range(8):
            g.Data4[i] = d4[i]
        return g
    except Exception:
        return None


# DOT11_AUTH_ALGORITHM / DOT11_CIPHER_ALGORITHM (numeric, locale-free)
_AUTH_ENUM = {
    1: "Open", 2: "WEP", 3: "WPA-Enterprise", 4: "WPA-Personal",
    5: "WPA-None", 6: "WPA2-Enterprise", 7: "WPA2-Personal",
    8: "WPA3-Enterprise", 9: "WPA3-Personal", 10: "OWE",
    11: "WPA3-Enterprise",
}
_CIPHER_ENUM = {
    0: "None", 1: "WEP-40", 2: "TKIP", 4: "CCMP", 5: "WEP-104",
    6: "BIP", 8: "GCMP-128", 9: "GCMP-256", 10: "CCMP-256",
    11: "BIP-GMAC-128", 12: "BIP-GMAC-256",
}


def _auth_enum(v: int) -> str:
    return _AUTH_ENUM.get(int(v), "Unknown")


def _cipher_enum(v: int) -> str:
    return _CIPHER_ENUM.get(int(v), "Unknown")


def scan_bss(fresh: bool = True, handle=None, guid=None) -> List[dict]:
    """
    Return every visible BSS with real per-BSSID RSSI, center frequency,
    privacy bit, IE blob and parsed security.

    Keys: ssid, bssid, rssi_dbm, link_quality, frequency_khz, channel, band,
          phy, privacy, ie_blob, authentication, encryption, is_open
    """
    owns_handle = handle is None
    h = _Handle() if owns_handle else None
    use_handle = h.handle if h and h.ok else handle
    if use_handle is None:
        return []

    try:
        targets = list_interfaces()
        if guid is None:
            guids = [i["guid"] for i in targets]
        else:
            guids = [None]  # caller passed a parsed GUID

        if fresh and h and h.ok:
            for iface in targets:
                g = _parse_guid(iface["guid"])
                if g is not None:
                    try:
                        h.wlan.WlanScan(h.handle, ctypes.byref(g), None, None, None)
                    except Exception:
                        pass
            time.sleep(1.8)

        results: List[dict] = []
        for iface in targets if guid is None else []:
            g = _parse_guid(iface["guid"])
            if g is None:
                continue
            results.extend(_read_bss_list(use_handle, g))
        if guid is not None:
            results.extend(_read_bss_list(use_handle, guid))

        if fresh and not results:
            time.sleep(1.7)
            if h and h.ok:
                for iface in targets:
                    g = _parse_guid(iface["guid"])
                    if g is not None:
                        results.extend(_read_bss_list(use_handle, g))
        return results
    finally:
        if h:
            h.close()


def _read_bss_list(handle, guid: _GUID) -> List[dict]:
    wlan = _wlan()
    out: List[dict] = []
    if not wlan:
        return out
    p = ctypes.c_void_p()
    # bSecurityEnabled=1 makes the driver include the security IEs in the blob.
    rc = wlan.WlanGetNetworkBssList(
        handle, ctypes.byref(guid), None, DOT11_BSS_TYPE_ANY, 1, None,
        ctypes.byref(p))
    if rc != 0 or not p.value:
        return out
    try:
        total = wt.DWORD.from_address(p.value).value
        count = wt.DWORD.from_address(p.value + 4).value
        if count <= 0:
            return out
        # WLAN_BSS_LIST is a fixed-size array of WLAN_BSS_ENTRY. The IE blobs
        # live in a packed tail region and are reached through each entry's
        # ulIeOffset (which can be larger than the struct size).
        stride = ctypes.sizeof(_WLAN_BSS_ENTRY)
        base = p.value + 8
        for i in range(count):
            addr = base + i * stride
            e = _WLAN_BSS_ENTRY.from_address(addr)
            ssid = _ssid_str(bytes(e.dot11Ssid.ucSSID), e.dot11Ssid.uSSIDLength)
            if not ssid:
                continue
            rssi = float(e.lRssi)
            if not (-110 <= rssi <= 0):
                rssi = rssi_from_quality(e.uLinkQuality)
            freq_khz = int(e.ulChCenterFrequency)
            channel, band = freq_to_channel(freq_khz)
            privacy = bool(e.usCapabilityInformation & 0x0010)
            ie_blob = b""
            try:
                off, size = int(e.ulIeOffset), int(e.ulIeSize)
                entry_rel = addr - (p.value + 8)
                abs_off = 8 + entry_rel + off
                if 0 < size <= 2400 and abs_off + size <= total:
                    ie_blob = ctypes.string_at(p.value + abs_off, size)
            except Exception:
                pass
            auth, cipher, is_open = classify_security(privacy, ie_blob)
            out.append({
                "ssid": ssid,
                "bssid": _mac_str(bytes(e.dot11Bssid)),
                "rssi_dbm": rssi,
                "link_quality": int(e.uLinkQuality),
                "frequency_khz": freq_khz,
                "channel": channel,
                "band": band,
                "phy": phy_name(e.dot11BssPhyType),
                "privacy": privacy,
                "ie_blob": ie_blob,
                "authentication": auth,
                "encryption": cipher,
                "is_open": is_open,
            })
    finally:
        try:
            wlan.WlanFreeMemory(p)
        except Exception:
            pass
    return out
