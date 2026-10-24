"""
WiFi Optimizer - Core WiFi Scanner
==================================

Telemetry is language-independent: on Windows everything comes from the Native
Wi-Fi API (``wlanapi.dll``) via :mod:`core.wlan_native`. The old netsh-text
parser is kept only as a fallback for non-Windows / restricted systems.

What changed vs. the previous version:
  * every BSSID is reported (the old parser silently kept only the first BSS of
    each SSID and overwrote the rest),
  * real per-BSSID RSSI (dBm) and center frequency are used, so 6 GHz channels
    are classified correctly,
  * security is derived from the RSN/WPA information elements instead of
    localized "Authentication:" labels,
  * the connected interface reports link quality, negotiated rates, band and
    the live PHY, not an inferred percentage.
"""

from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

try:
    from core import wlan_native
except ImportError:  # direct import when src/core is on sys.path
    try:
        import wlan_native  # type: ignore
    except ImportError:  # pragma: no cover - non-Windows fallback
        wlan_native = None


@dataclass
class WiFiNetwork:
    """Represents one BSS (one BSSID), not merely one SSID."""
    ssid: str = ""
    bssid: str = ""
    signal_percent: int = 0
    signal_dbm: float = -100.0
    is_native_rssi: bool = False
    rssi_uncertainty_db: float = 6.0
    channel: int = 0
    band: str = "2.4GHz"
    frequency_mhz: float = 2400.0
    authentication: str = ""
    encryption: str = ""
    network_type: str = "Infrastructure"
    radio_type: str = ""
    connected: bool = False
    score: float = 0.0
    link_quality: int = 0

    def to_dict(self) -> dict:
        return {
            "ssid": self.ssid,
            "bssid": self.bssid,
            "signal_percent": self.signal_percent,
            "signal_dbm": self.signal_dbm,
            "is_native_rssi": self.is_native_rssi,
            "rssi_uncertainty_db": self.rssi_uncertainty_db,
            "channel": self.channel,
            "band": self.band,
            "frequency_mhz": self.frequency_mhz,
            "authentication": self.authentication,
            "encryption": self.encryption,
            "radio_type": self.radio_type,
            "connected": self.connected,
            "score": round(self.score, 2),
        }


@dataclass
class AdapterInfo:
    """Information about the current WiFi adapter / live association."""
    name: str = ""
    description: str = ""
    guid: str = ""
    physical_address: str = ""
    state: str = ""
    ssid: str = ""
    bssid: str = ""
    network_type: str = ""
    authentication: str = ""
    cipher: str = ""
    connection_mode: str = ""
    channel: int = 0
    band: str = ""
    frequency_mhz: float = 0.0
    receive_rate_mbps: float = 0.0
    transmit_rate_mbps: float = 0.0
    signal_percent: int = 0
    signal_dbm: float = -100.0
    is_native_rssi: bool = False
    rssi_uncertainty_db: float = 6.0
    profile: str = ""
    radio_type: str = ""


@dataclass
class PropagationEstimate:
    """RF Propagation distance bounds with environmental uncertainty."""
    d_dense_m: float
    d_indoor_m: float
    d_free_space_m: float
    uncertainty_db: float
    range_summary: str


# ── Interface identity (used by netsh-based DNS/power commands) ──────────────
_IFACE_CACHE: Dict[str, Tuple[float, str, str]] = {}


def get_wifi_interface_identity() -> Tuple[str, str]:
    """
    Return (friendly_name, mac_address) of the physical Wi-Fi adapter.

    PowerShell object properties are locale-independent, unlike the localized
    netsh text this project used to assume ("Wi-Fi").
    """
    cached = _IFACE_CACHE.get("wifi")
    if cached and time.time() - cached[0] < 60:
        return cached[1], cached[2]
    name, mac = "", ""
    try:
        ps = ("$w = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | "
              "Where-Object { $_.NdisPhysicalMedium -eq 9 -or $_.PhysicalMediaType -eq 'Native 802.11' } | "
              "Select-Object -First 1; "
              "if ($w) { $w.Name + '|' + $w.MacAddress }")
        res = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True, timeout=12,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        out = (res.stdout or "").strip()
        if "|" in out:
            name, mac = [p.strip() for p in out.split("|", 1)]
        elif out:
            name = out
    except Exception as e:
        logger.debug("Could not detect Wi-Fi interface name: %s", e)
    if not name:
        name = "Wi-Fi"
    _IFACE_CACHE["wifi"] = (time.time(), name, mac)
    return name, mac


def get_wifi_interface_name() -> str:
    return get_wifi_interface_identity()[0]


# ── Conversions ──────────────────────────────────────────────────────────────
def percent_to_dbm(percent: int) -> float:
    """
    Convert the legacy Windows signal percentage (0-100) to an *estimate* in
    dBm (P/2 - 100). Only used when the driver does not expose real RSSI.
    """
    if percent <= 0:
        return -100.0
    if percent >= 100:
        return -50.0
    return (percent / 2.0) - 100.0


def channel_to_frequency(channel: int, band: Optional[str] = None) -> Tuple[float, str]:
    """
    Convert a channel number to (MHz, band).

    Channel numbers are reused across bands (channel 1 exists in 2.4 GHz and in
    6 GHz). When `band` is supplied it is authoritative. Without it, channels
    1-14 are assumed to be 2.4 GHz, 32-177 5 GHz and 181-233 6 GHz. This is why
    the scanner now prefers the native center frequency.
    """
    if band:
        b = band.lower()
        if b.startswith("6"):
            return float(5950 + 5 * channel), "6GHz"
        if b.startswith("5"):
            return float(5000 + 5 * channel), "5GHz"
        if b.startswith("2.4") or b.startswith("2"):
            return (2484.0 if channel == 14 else float(2407 + 5 * channel)), "2.4GHz"

    if 1 <= channel <= 14:
        return (2484.0 if channel == 14 else float(2407 + 5 * channel)), "2.4GHz"
    if 32 <= channel <= 177:
        return float(5000 + 5 * channel), "5GHz"
    if 181 <= channel <= 233:
        return float(5950 + 5 * channel), "6GHz"
    return 2400.0, "2.4GHz"


def run_netsh(args: list) -> str:
    """Run a netsh command and return stdout (fallback path only)."""
    try:
        result = subprocess.run(
            ["netsh"] + args,
            capture_output=True, text=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.stdout or ""
    except subprocess.TimeoutExpired:
        logger.warning("netsh command timed out: %s", args)
        return ""
    except Exception as e:
        logger.error("netsh error: %s", e)
        return ""


# ── Native scan ──────────────────────────────────────────────────────────────
def _network_from_bss(bss: dict) -> WiFiNetwork:
    quality = int(bss.get("link_quality") or 0)
    return WiFiNetwork(
        ssid=bss.get("ssid", ""),
        bssid=bss.get("bssid", ""),
        signal_percent=quality,
        signal_dbm=float(bss.get("rssi_dbm", percent_to_dbm(quality))),
        is_native_rssi=True,
        rssi_uncertainty_db=1.5,
        channel=int(bss.get("channel") or 0),
        band=bss.get("band") or "2.4GHz",
        frequency_mhz=round(float(bss.get("frequency_khz") or 0) / 1000.0, 1),
        authentication=bss.get("authentication") or "Unknown",
        encryption=bss.get("encryption") or "Unknown",
        network_type="Infrastructure",
        radio_type=bss.get("phy") or "802.11",
        link_quality=quality,
    )


def query_native_wlan_bss_rssi() -> Dict[str, int]:
    """Backward-compatible {BSSID: dBm} map built from the native BSS list."""
    if not wlan_native:
        return {}
    try:
        return {b["bssid"].upper(): int(round(b["rssi_dbm"]))
                for b in wlan_native.scan_bss(fresh=False)}
    except Exception as e:
        logger.debug("Native RSSI query failed: %s", e)
        return {}


def scan_networks(prefer_native: bool = True) -> List[WiFiNetwork]:
    """
    Scan all visible BSSes.

    Returns one WiFiNetwork per BSSID, so mesh nodes / dual-band APs with the
    same SSID are no longer collapsed into a single (often worst) entry.
    """
    if prefer_native and wlan_native and wlan_native.available():
        try:
            bss_list = wlan_native.scan_bss(fresh=True)
            if bss_list:
                networks = [_network_from_bss(b) for b in bss_list]
                # Deduplicate identical BSSIDs (multiple WLAN interfaces can
                # return the same physical BSS).
                seen, unique = set(), []
                for n in networks:
                    key = (n.ssid, n.bssid.upper())
                    if key in seen:
                        continue
                    seen.add(key)
                    unique.append(n)
                conn = wlan_native.get_connection()
                if conn:
                    for n in unique:
                        n.connected = n.bssid.upper() == (conn.get("bssid") or "").upper()
                return unique
        except Exception as e:
            logger.warning("Native scan failed, falling back to netsh: %s", e)
    return _scan_with_netsh()


def _scan_with_netsh() -> List[WiFiNetwork]:
    """Localized-text fallback. Kept only for systems without wlanapi."""
    output = run_netsh(["wlan", "show", "networks", "mode=bssid"])
    if not output:
        return []

    networks: List[WiFiNetwork] = []
    current_ssid = ""
    base_auth = ""
    base_enc = ""
    base_type = "Infrastructure"
    current: Optional[WiFiNetwork] = None

    def flush():
        nonlocal current
        if current and current.ssid:
            networks.append(current)
        current = None

    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue

        # "SSID 1 : name" — value after the first colon.
        if re.match(r"^SSID\s+\d+\s*:", line, re.IGNORECASE):
            flush()
            current_ssid = line.split(":", 1)[1].strip()
            base_auth, base_enc, base_type = "", "", "Infrastructure"
            continue

        if re.match(r"^BSSID\s+\d+\s*:", line, re.IGNORECASE):
            flush()
            mac = line.split(":", 1)[1].strip()
            # Re-join remaining colon-separated octets (split only once removed
            # the label colon, so the MAC is intact).
            current = WiFiNetwork(
                ssid=current_ssid, bssid=mac, authentication=base_auth,
                encryption=base_enc, network_type=base_type,
            )
            continue

        if ":" not in line:
            continue
        value = line.split(":", 1)[1].strip()

        # Security / network-type values are usually untranslated proper nouns.
        if re.search(r"\b(WPA3|WPA2|WPA|WEP|OWE|Open)\b", value, re.IGNORECASE):
            if current is None:
                base_auth = value
            else:
                current.authentication = value
        if re.search(r"\b(CCMP|TKIP|GCMP|WEP|None)\b", value, re.IGNORECASE):
            if current is None:
                base_enc = value
            else:
                current.encryption = value
        if "Infrastructure" in value or "Ad-hoc" in value or "Ad-Hoc" in value:
            if current is None:
                base_type = value
            else:
                current.network_type = value
        if "802.11" in value:
            if current is not None:
                current.radio_type = value

        m_band = re.search(r"\b(2\.4|5|6)\s*GHz", value, re.IGNORECASE)
        if m_band and current is not None:
            current.band = m_band.group(1) + "GHz"

        m_pct = re.search(r"(\d+)\s*%", value)
        if m_pct and current is not None:
            current.signal_percent = int(m_pct.group(1))
            current.signal_dbm = percent_to_dbm(current.signal_percent)
            current.link_quality = current.signal_percent

        # A bare integer value is the channel in every localized netsh output.
        if re.fullmatch(r"\d{1,3}", value) and current is not None:
            current.channel = int(value)
            current.frequency_mhz, derived_band = channel_to_frequency(current.channel)
            if not current.band:
                current.band = derived_band

    flush()

    # Use native RSSI if available (the fallback parser still gives us BSSIDs).
    native = query_native_wlan_bss_rssi()
    for n in networks:
        if n.bssid.upper() in native:
            n.signal_dbm = float(native[n.bssid.upper()])
            n.is_native_rssi = True
            n.rssi_uncertainty_db = 1.5
        else:
            n.is_native_rssi = False
            n.rssi_uncertainty_db = 6.0
    return networks


# ── Connected link ───────────────────────────────────────────────────────────
def get_connected_network() -> Optional[AdapterInfo]:
    """Describe the live Wi-Fi association (native first, netsh fallback)."""
    if wlan_native and wlan_native.available():
        try:
            conn = wlan_native.get_connection()
            if conn:
                name, mac = get_wifi_interface_identity()
                return AdapterInfo(
                    name=name,
                    description=conn.get("description", ""),
                    guid=conn.get("guid", ""),
                    physical_address=mac,
                    state="connected",
                    ssid=conn.get("ssid", ""),
                    bssid=conn.get("bssid", ""),
                    network_type="Infrastructure",
                    authentication=conn.get("authentication", ""),
                    cipher=conn.get("encryption", ""),
                    connection_mode="Auto",
                    channel=int(conn.get("channel") or 0),
                    band=conn.get("band", ""),
                    frequency_mhz=float(conn.get("frequency_mhz") or 0.0),
                    receive_rate_mbps=float(conn.get("rx_mbps") or 0.0),
                    transmit_rate_mbps=float(conn.get("tx_mbps") or 0.0),
                    signal_percent=int(conn.get("link_quality") or 0),
                    signal_dbm=float(conn.get("rssi_dbm") or -100.0),
                    is_native_rssi=True,
                    rssi_uncertainty_db=1.5,
                    profile=conn.get("profile", ""),
                    radio_type=conn.get("phy", ""),
                )
        except Exception as e:
            logger.warning("Native connection query failed: %s", e)

    return _get_connected_with_netsh()


def _get_connected_with_netsh() -> Optional[AdapterInfo]:
    output = run_netsh(["wlan", "show", "interfaces"])
    if not output:
        return None

    info = AdapterInfo()
    for line in output.splitlines():
        line = line.strip()
        if ":" not in line:
            continue
        label, value = [p.strip() for p in line.split(":", 1)]
        low = label.lower()

        if low == "name" or (low.startswith("name") and "network" not in low):
            info.name = value
        elif low.startswith("description"):
            info.description = value
        elif low.startswith("guid"):
            info.guid = value
        elif low.startswith("physical address"):
            info.physical_address = value
        elif low.startswith("state"):
            info.state = value
        elif low.startswith("ssid") and not low.startswith("bssid"):
            info.ssid = value
        elif low.startswith("bssid"):
            info.bssid = value
        elif low.startswith("network type"):
            info.network_type = value
        elif low.startswith("authentication"):
            info.authentication = value
        elif low.startswith("cipher"):
            info.cipher = value
        elif low.startswith("connection mode"):
            info.connection_mode = value
        elif low.startswith("channel"):
            m = re.search(r"\d+", value)
            if m:
                info.channel = int(m.group())
                info.frequency_mhz, info.band = channel_to_frequency(info.channel)
        elif low.startswith("receive rate"):
            m = re.search(r"[\d.]+", value)
            if m:
                info.receive_rate_mbps = float(m.group())
        elif low.startswith("transmit rate"):
            m = re.search(r"[\d.]+", value)
            if m:
                info.transmit_rate_mbps = float(m.group())
        elif low.startswith("signal"):
            m = re.search(r"(\d+)", value)
            if m:
                info.signal_percent = int(m.group(1))
                info.signal_dbm = percent_to_dbm(info.signal_percent)
        elif low.startswith("profile"):
            info.profile = value
        elif low.startswith("radio type"):
            info.radio_type = value

    if not info.name:
        return None

    native = query_native_wlan_bss_rssi()
    if info.bssid and info.bssid.upper() in native:
        info.signal_dbm = float(native[info.bssid.upper()])
        info.is_native_rssi = True
        info.rssi_uncertainty_db = 1.5
    else:
        info.is_native_rssi = False
        info.rssi_uncertainty_db = 6.0
    return info


def get_saved_wifi_profiles() -> List[str]:
    """
    Return saved profile names without relying on the localized label.

    Each profile line has a non-empty value after ':' while the section headers
    end with ':' and therefore yield an empty value.
    """
    output = run_netsh(["wlan", "show", "profiles"])
    profiles: List[str] = []
    for line in output.splitlines():
        if ":" not in line:
            continue
        value = line.split(":", 1)[1].strip()
        if value:
            profiles.append(value)
    # Preserve order, drop duplicates.
    return list(dict.fromkeys(profiles))


# ── Congestion / propagation ─────────────────────────────────────────────────
def get_channel_congestion(networks: List[WiFiNetwork]) -> Dict[Tuple[str, int], int]:
    """Count BSSes per (band, channel) so 2.4/5/6 GHz channel 1 do not collide."""
    congestion: Dict[Tuple[str, int], int] = {}
    for net in networks:
        key = (net.band, net.channel)
        congestion[key] = congestion.get(key, 0) + 1
    return congestion


def get_best_channel(networks: List[WiFiNetwork], band: str = "5GHz") -> int:
    """Find the least congested *orthogonal* channel for a band."""
    congestion = get_channel_congestion(networks)
    if band == "5GHz":
        candidates = list(range(36, 65, 4)) + list(range(100, 145, 4)) + list(range(149, 178, 4))
    elif band == "6GHz":
        candidates = list(range(1, 234, 16))
    else:
        candidates = [1, 6, 11]  # Non-overlapping 2.4 GHz channels
    return min(candidates, key=lambda ch: congestion.get((band, ch), 0))


def calculate_channel_overlap(ch1: int, band1: str, ch2: int, band2: str) -> float:
    """
    Spectral overlap coefficient in [0, 1].

    2.4 GHz uses a triangular mask (22 MHz wide, 5 MHz spacing); 5/6 GHz are
    treated as orthogonal unless co-channel. This is an approximation: it does
    not model 40/80 MHz bonding because the client cannot observe the AP's
    configured width reliably.
    """
    if band1 != band2:
        return 0.0
    if band1 == "2.4GHz":
        delta = abs(ch1 - ch2)
        if delta == 0:
            return 1.0
        if delta <= 4:
            return round(1.0 - (delta * 5.0 / 22.0), 2)
        return 0.0
    return 1.0 if ch1 == ch2 else 0.0


def calculate_effective_channel_congestion(target: WiFiNetwork,
                                           networks: List[WiFiNetwork]) -> float:
    """C_eff = Σ w_ov(ch) * w_sig(P) over all other BSSes."""
    c_eff = 0.0
    for net in networks:
        if net is target or (target.bssid and net.bssid and net.bssid == target.bssid):
            continue
        w_ov = calculate_channel_overlap(target.channel, target.band, net.channel, net.band)
        if w_ov > 0:
            w_sig = max(0.0, min(1.0, (net.signal_dbm + 90.0) / 40.0))
            c_eff += w_ov * w_sig
    return c_eff


def estimate_distance_meters(signal_dbm: float, frequency_mhz: float = 2400.0,
                             path_loss_exponent: float = 3.0,
                             tx_power_dbm: float = 20.0) -> float:
    """Log-distance path-loss distance estimate (uncalibrated)."""
    import math
    if path_loss_exponent <= 0:
        path_loss_exponent = 3.0
    path_loss = tx_power_dbm - signal_dbm
    try:
        pl_1m = 20.0 * math.log10(frequency_mhz) - 27.55
        exponent = (path_loss - pl_1m) / (10.0 * path_loss_exponent)
        return round(max(0.1, min(10.0 ** exponent, 500.0)), 2)
    except Exception:
        return 0.0


def estimate_propagation_range(signal_dbm: float, frequency_mhz: float = 2400.0,
                               tx_power_dbm: float = 20.0,
                               uncertainty_db: float = 6.0) -> PropagationEstimate:
    """Distance bounds for dense concrete / typical indoor / free space."""
    d_dense = estimate_distance_meters(signal_dbm, frequency_mhz, 4.0, tx_power_dbm)
    d_indoor = estimate_distance_meters(signal_dbm, frequency_mhz, 3.0, tx_power_dbm)
    d_free = estimate_distance_meters(signal_dbm, frequency_mhz, 2.0, tx_power_dbm)
    summary = (f"{d_dense:.1f} m - {d_indoor:.1f} m (indoor obstacle range) "
               f"[uncalibrated ±{uncertainty_db:.0f} dB]")
    return PropagationEstimate(d_dense, d_indoor, d_free, uncertainty_db, summary)
