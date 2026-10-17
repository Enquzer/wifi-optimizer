"""
WiFi Optimizer - Core WiFi Scanner Module
Scans and analyzes available WiFi networks using Windows netsh commands.
"""

import subprocess
import re
import json
from dataclasses import dataclass, field
from typing import List, Optional
import logging

logger = logging.getLogger(__name__)


@dataclass
class WiFiNetwork:
    """Represents a scanned WiFi network."""
    ssid: str = ""
    bssid: str = ""
    signal_percent: int = 0
    signal_dbm: float = -100.0
    is_native_rssi: bool = False
    rssi_uncertainty_db: float = 6.0  # ±6.0 dB for synthetic P/2-100; ±1.5 dB for native hardware measurement
    channel: int = 0
    band: str = "2.4GHz"
    frequency_mhz: float = 2400.0
    authentication: str = ""
    encryption: str = ""
    network_type: str = "Infrastructure"
    radio_type: str = ""
    connected: bool = False
    score: float = 0.0

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
    """Information about the current WiFi adapter."""
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
    d_dense_m: float           # n = 4.0 (dense concrete walls / heavy obstruction lower bound)
    d_indoor_m: float          # n = 3.0 (typical residential / drywall indoor)
    d_free_space_m: float      # n = 2.0 (ideal line-of-sight free space upper bound)
    uncertainty_db: float      # RF measurement uncertainty in dB
    range_summary: str         # Human-readable summary e.g. "9.1 m - 18.9 m (Indoor Obstacle Range)"



def percent_to_dbm(percent: int) -> float:
    """
    Convert Windows signal percentage (0-100) to an estimated relative power indicator in dBm
    using the netsh heuristic formula P/2 - 100.
    Note: This is an assumed driver-synthesized mapping (with typical uncertainty of ±6 dB),
    NOT a calibrated laboratory instrument measurement.
    """
    if percent <= 0:
        return -100.0
    if percent >= 100:
        return -50.0
    return (percent / 2.0) - 100.0


def query_native_wlan_bss_rssi() -> dict[str, int]:
    """
    Query real physical RSSI values (in real dBm) directly from Windows Native Wi-Fi API (wlanapi.dll).
    Returns a mapping of {bssid_uppercase: rssi_dbm}.
    Returns empty dict if Native Wi-Fi API is unavailable or unassociated.
    """
    results = {}
    try:
        import ctypes
        import ctypes.wintypes
        if not hasattr(ctypes, "windll") or not hasattr(ctypes.windll, "wlanapi"):
            return results

        wlan = ctypes.windll.wlanapi
        handle = ctypes.wintypes.HANDLE()
        neg = ctypes.wintypes.DWORD()
        if wlan.WlanOpenHandle(2, None, ctypes.byref(neg), ctypes.byref(handle)) != 0:
            return results

        try:
            class GUID(ctypes.Structure):
                _fields_ = [('Data1', ctypes.c_ulong), ('Data2', ctypes.c_ushort), ('Data3', ctypes.c_ushort), ('Data4', ctypes.c_ubyte * 8)]
            class WLAN_INTERFACE_INFO(ctypes.Structure):
                _fields_ = [('InterfaceGuid', GUID), ('strInterfaceDescription', ctypes.c_wchar * 256), ('isState', ctypes.c_uint)]
            class WLAN_INTERFACE_INFO_LIST(ctypes.Structure):
                _fields_ = [('dwNumberOfItems', ctypes.c_uint), ('dwIndex', ctypes.c_uint), ('InterfaceInfo', WLAN_INTERFACE_INFO * 1)]

            p_list = ctypes.POINTER(WLAN_INTERFACE_INFO_LIST)()
            if wlan.WlanEnumInterfaces(handle, None, ctypes.byref(p_list)) == 0:
                try:
                    num_interfaces = p_list.contents.dwNumberOfItems
                    for idx in range(num_interfaces):
                        guid = p_list.contents.InterfaceInfo[idx].InterfaceGuid
                        p_bss = ctypes.c_void_p()
                        ret = wlan.WlanGetNetworkBssList(handle, ctypes.byref(guid), None, 3, 0, None, ctypes.byref(p_bss))
                        if ret == 0 and p_bss.value:
                            try:
                                count = ctypes.c_ulong.from_address(p_bss.value + 4).value
                                entry_ptr = p_bss.value + 8
                                if count > 0:
                                    bssid_bytes = (ctypes.c_ubyte * 6).from_address(entry_ptr + 40)
                                    bssid_str = ':'.join(f'{b:02X}' for b in bssid_bytes)
                                    lRssi = ctypes.c_long.from_address(entry_ptr + 56).value
                                    if -110 <= lRssi <= 0:
                                        results[bssid_str] = lRssi
                            finally:
                                wlan.WlanFreeMemory(p_bss)
                finally:
                    wlan.WlanFreeMemory(p_list)
        finally:
            wlan.WlanCloseHandle(handle, None)
    except Exception as e:
        logger.debug("Native Wi-Fi RSSI query notice: %s", e)
    return results


def get_saved_wifi_profiles() -> List[str]:
    """Return a list of SSIDs that have saved network profiles in Windows."""
    output = run_netsh(["wlan", "show", "profiles"])
    profiles = []
    for line in output.splitlines():
        if "All User Profile" in line or "User Profile" in line:
            parts = line.split(":")
            if len(parts) >= 2:
                profiles.append(parts[1].strip())
    return profiles



def channel_to_frequency(channel: int) -> tuple[float, str]:
    """Convert WiFi channel number to frequency (MHz) and band."""
    if 1 <= channel <= 14:
        freq = 2407 + channel * 5 if channel != 14 else 2484
        return float(freq), "2.4GHz"
    elif 36 <= channel <= 64:
        freq = 5180 + (channel - 36) * 5
        return float(freq), "5GHz"
    elif 100 <= channel <= 144:
        freq = 5500 + (channel - 100) * 5
        return float(freq), "5GHz"
    elif 149 <= channel <= 177:
        freq = 5745 + (channel - 149) * 5
        return float(freq), "5GHz"
    elif channel >= 1 and channel <= 233:
        return 6000.0 + channel * 5, "6GHz"
    return 2400.0, "2.4GHz"


def run_netsh(args: list) -> str:
    """Run a netsh command and return stdout."""
    try:
        result = subprocess.run(
            ["netsh"] + args,
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0
        )
        return result.stdout
    except subprocess.TimeoutExpired:
        logger.warning("netsh command timed out: %s", args)
        return ""
    except Exception as e:
        logger.error("netsh error: %s", e)
        return ""


def scan_networks() -> List[WiFiNetwork]:
    """Scan all visible WiFi networks using netsh wlan show networks."""
    output = run_netsh(["wlan", "show", "networks", "mode=bssid"])
    networks = []
    native_rssi_map = query_native_wlan_bss_rssi()

    # Split by SSID block
    blocks = re.split(r'\nSSID\s+\d+\s*:', output)
    for block in blocks[1:]:  # skip first empty
        net = WiFiNetwork()
        lines = block.strip().splitlines()
        # SSID is first line
        net.ssid = lines[0].strip() if lines else ""

        for line in lines:
            line_stripped = line.strip()
            if line_stripped.startswith("BSSID"):
                m = re.search(r':\s*(.+)', line_stripped)
                if m:
                    net.bssid = m.group(1).strip()
            elif line_stripped.startswith("Signal"):
                m = re.search(r':\s*(\d+)%', line_stripped)
                if m:
                    net.signal_percent = int(m.group(1))
                    net.signal_dbm = percent_to_dbm(net.signal_percent)
            elif line_stripped.startswith("Channel"):
                m = re.search(r':\s*(\d+)', line_stripped)
                if m:
                    net.channel = int(m.group(1))
                    net.frequency_mhz, net.band = channel_to_frequency(net.channel)
            elif line_stripped.startswith("Authentication"):
                m = re.search(r':\s*(.+)', line_stripped)
                if m:
                    net.authentication = m.group(1).strip()
            elif line_stripped.startswith("Encryption"):
                m = re.search(r':\s*(.+)', line_stripped)
                if m:
                    net.encryption = m.group(1).strip()
            elif line_stripped.startswith("Network type"):
                m = re.search(r':\s*(.+)', line_stripped)
                if m:
                    net.network_type = m.group(1).strip()
            elif line_stripped.startswith("Radio type"):
                m = re.search(r':\s*(.+)', line_stripped)
                if m:
                    net.radio_type = m.group(1).strip()

        if net.ssid:
            bssid_upper = net.bssid.upper()
            if bssid_upper in native_rssi_map:
                net.signal_dbm = float(native_rssi_map[bssid_upper])
                net.is_native_rssi = True
                net.rssi_uncertainty_db = 1.5
            else:
                net.is_native_rssi = False
                net.rssi_uncertainty_db = 6.0
            networks.append(net)

    return networks


def get_connected_network() -> Optional[AdapterInfo]:
    """Get information about the currently connected WiFi network."""
    output = run_netsh(["wlan", "show", "interfaces"])
    if not output:
        return None

    info = AdapterInfo()
    for line in output.splitlines():
        line_stripped = line.strip()
        if line_stripped.startswith("Name") and not line_stripped.startswith("Network"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.name = m.group(1).strip()
        elif line_stripped.startswith("Description"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.description = m.group(1).strip()
        elif line_stripped.startswith("GUID"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.guid = m.group(1).strip()
        elif line_stripped.startswith("Physical address"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.physical_address = m.group(1).strip()
        elif line_stripped.startswith("State"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.state = m.group(1).strip()
        elif line_stripped.startswith("SSID") and not line_stripped.startswith("BSSID"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.ssid = m.group(1).strip()
        elif line_stripped.startswith("BSSID"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.bssid = m.group(1).strip()
        elif line_stripped.startswith("Network type"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.network_type = m.group(1).strip()
        elif line_stripped.startswith("Authentication"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.authentication = m.group(1).strip()
        elif line_stripped.startswith("Cipher"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.cipher = m.group(1).strip()
        elif line_stripped.startswith("Connection mode"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.connection_mode = m.group(1).strip()
        elif line_stripped.startswith("Channel"):
            m = re.search(r':\s*(\d+)', line_stripped)
            if m:
                info.channel = int(m.group(1))
        elif line_stripped.startswith("Receive rate"):
            m = re.search(r':\s*([\d.]+)', line_stripped)
            if m:
                info.receive_rate_mbps = float(m.group(1))
        elif line_stripped.startswith("Transmit rate"):
            m = re.search(r':\s*([\d.]+)', line_stripped)
            if m:
                info.transmit_rate_mbps = float(m.group(1))
        elif line_stripped.startswith("Signal"):
            m = re.search(r':\s*(\d+)%', line_stripped)
            if m:
                info.signal_percent = int(m.group(1))
                info.signal_dbm = percent_to_dbm(info.signal_percent)
        elif line_stripped.startswith("Profile"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.profile = m.group(1).strip()
        elif line_stripped.startswith("Radio type"):
            m = re.search(r':\s*(.+)', line_stripped)
            if m:
                info.radio_type = m.group(1).strip()

    if info.name:
        native_map = query_native_wlan_bss_rssi()
        if info.bssid and info.bssid.upper() in native_map:
            info.signal_dbm = float(native_map[info.bssid.upper()])
            info.is_native_rssi = True
            info.rssi_uncertainty_db = 1.5
        else:
            info.is_native_rssi = False
            info.rssi_uncertainty_db = 6.0

    return info if info.name else None


def get_channel_congestion(networks: List[WiFiNetwork]) -> dict:
    """Analyze channel congestion — returns {channel: count}."""
    congestion = {}
    for net in networks:
        ch = net.channel
        congestion[ch] = congestion.get(ch, 0) + 1
    return congestion


def get_best_channel(networks: List[WiFiNetwork], band: str = "5GHz") -> int:
    """Find the least congested channel for a given band."""
    congestion = get_channel_congestion(networks)
    if band == "5GHz":
        candidates = list(range(36, 65, 4)) + list(range(100, 145, 4)) + list(range(149, 178, 4))
    else:
        candidates = [1, 6, 11]  # Non-overlapping 2.4GHz channels

    best_channel = candidates[0]
    best_count = float('inf')
    for ch in candidates:
        count = congestion.get(ch, 0)
        if count < best_count:
            best_count = count
            best_channel = ch

    return best_channel


def calculate_channel_overlap(ch1: int, band1: str, ch2: int, band2: str) -> float:
    """
    Calculate spectral overlap coefficient w_ov in [0.0, 1.0] between two channels.
    - For 2.4 GHz (22 MHz channel width, 5 MHz channel spacing):
      Uses the standard spectral mask overlap integral:
      w_ov = max(0.0, round(1.0 - (delta * 5.0 / 22.0), 2))
      Delta = 0 -> 1.0 (co-channel)
      Delta = 1 -> 0.77 (~0.8)
      Delta = 2 -> 0.55 (~0.6)
      Delta = 3 -> 0.32 (~0.3)
      Delta = 4 -> 0.09 (~0.1)
      Delta >= 5 -> 0.0 (orthogonal)
    - For 5 GHz / 6 GHz:
      Orthogonal channels unless on identical center channel (1.0 vs 0.0).
    - Cross-band -> 0.0.
    """
    if band1 != band2:
        return 0.0

    if band1 == "2.4GHz":
        delta = abs(ch1 - ch2)
        if delta == 0:
            return 1.0
        elif delta <= 4:
            return round(1.0 - (delta * 5.0 / 22.0), 2)
        else:
            return 0.0
    else:
        # 5GHz / 6GHz
        return 1.0 if ch1 == ch2 else 0.0


def calculate_effective_channel_congestion(target: WiFiNetwork, networks: List[WiFiNetwork]) -> float:
    """
    Compute effective channel interference C_eff:
    C_eff = sum_{j != target} w_ov(ch_target, ch_j) * w_sig(P_j)
    where w_sig(P_j) = clamp((P_j + 90) / 40, 0.0, 1.0)
    """
    c_eff = 0.0
    for net in networks:
        # Skip the target itself (matching by BSSID or same instance)
        if net is target or (target.bssid and net.bssid and net.bssid == target.bssid):
            continue
        w_ov = calculate_channel_overlap(target.channel, target.band, net.channel, net.band)
        if w_ov > 0:
            # Signal weighting: links at -90 dBm contribute ~0; links at >= -50 dBm contribute 1.0
            w_sig = max(0.0, min(1.0, (net.signal_dbm + 90.0) / 40.0))
            c_eff += w_ov * w_sig
    return c_eff


def estimate_distance_meters(signal_dbm: float, frequency_mhz: float = 2400.0,
                             path_loss_exponent: float = 3.0, tx_power_dbm: float = 20.0) -> float:
    """
    Estimate distance using the Log-Distance Path Loss Model:
    PL(d) = PL(1m) + 10 * n * log10(d)
    where PL(1m) = 20*log10(f_MHz) - 27.55 (Free-space reference at 1m)
    Total PL = tx_power_dbm - signal_dbm

    Solving for d:
    10 * n * log10(d) = (tx_power_dbm - signal_dbm) - 20*log10(f_MHz) + 27.55
    d = 10 ^ (((tx_power_dbm - signal_dbm) - 20*log10(f_MHz) + 27.55) / (10 * n))
    """
    import math
    if path_loss_exponent <= 0:
        path_loss_exponent = 3.0

    path_loss = tx_power_dbm - signal_dbm
    try:
        pl_1m = 20.0 * math.log10(frequency_mhz) - 27.55
        exponent = (path_loss - pl_1m) / (10.0 * path_loss_exponent)
        distance = 10.0 ** exponent
        return round(max(0.1, min(distance, 500.0)), 2)
    except Exception:
        return 0.0


def estimate_propagation_range(signal_dbm: float, frequency_mhz: float = 2400.0,
                               tx_power_dbm: float = 20.0,
                               uncertainty_db: float = 6.0) -> PropagationEstimate:
    """
    Estimate physical propagation range with uncertainty intervals:
    - Dense Concrete / heavy walls (n = 4.0)
    - Typical Residential Drywall / Office (n = 3.0)
    - Free Space Line-of-Sight (n = 2.0)
    Note: Without per-room calibration, actual wall materials, reflections,
    and router antenna gain introduce substantial uncertainty (typically ±6 to ±10 dB).
    """
    d_dense = estimate_distance_meters(signal_dbm, frequency_mhz, path_loss_exponent=4.0, tx_power_dbm=tx_power_dbm)
    d_indoor = estimate_distance_meters(signal_dbm, frequency_mhz, path_loss_exponent=3.0, tx_power_dbm=tx_power_dbm)
    d_free = estimate_distance_meters(signal_dbm, frequency_mhz, path_loss_exponent=2.0, tx_power_dbm=tx_power_dbm)

    summary = f"{d_dense:.1f} m - {d_indoor:.1f} m (Indoor Obstacle Range) [Uncalibrated ±{uncertainty_db:.0f}dB]"
    return PropagationEstimate(
        d_dense_m=d_dense,
        d_indoor_m=d_indoor,
        d_free_space_m=d_free,
        uncertainty_db=uncertainty_db,
        range_summary=summary
    )


