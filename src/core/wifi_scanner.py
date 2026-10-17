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
    profile: str = ""
    radio_type: str = ""


def percent_to_dbm(percent: int) -> float:
    """Convert Windows signal percentage to approximate dBm value."""
    # Windows uses RSSI = percent/2 - 100 mapping
    if percent <= 0:
        return -100.0
    if percent >= 100:
        return -50.0
    return (percent / 2.0) - 100.0


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


def estimate_distance_meters(signal_dbm: float, frequency_mhz: float = 2400.0) -> float:
    """
    Estimate distance using Free Space Path Loss (Friis equation).
    FSPL(dB) = 20*log10(d) + 20*log10(f) + 20*log10(4π/c)
    Solving for d: d = 10^((FSPL - 20*log10(f) - 27.55) / 20)
    where 27.55 = 20*log10(4π/c) adjusted constant for MHz/meters
    """
    import math
    # Approximate TX power for typical router = 20 dBm
    tx_power_dbm = 20.0
    path_loss = tx_power_dbm - signal_dbm
    try:
        distance = 10 ** ((path_loss - 20 * math.log10(frequency_mhz) - 27.55) / 20)
        return round(max(0.1, distance), 2)
    except Exception:
        return 0.0
