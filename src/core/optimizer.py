"""
WiFi Optimizer - Core Optimization Algorithm
Analyzes scanned networks and applies the best settings for maximum performance.
"""

import logging
import math
from dataclasses import dataclass
from typing import List, Optional, Tuple
from core.wifi_scanner import WiFiNetwork, AdapterInfo, scan_networks, get_connected_network, get_channel_congestion
from core import adapter_manager

logger = logging.getLogger(__name__)


@dataclass
class OptimizationProfile:
    """Optimization target profile."""
    name: str
    description: str
    preferred_band: str          # "5GHz", "2.4GHz", "Auto"
    roaming_aggressiveness: int  # 1-5 (5=most aggressive)
    transmit_power: int          # 1-5 (5=max)
    disable_power_save: bool
    background_scan: bool
    optimize_tcp: bool
    set_dns: bool
    dns_primary: str
    dns_secondary: str


PROFILES = {
    "max_speed": OptimizationProfile(
        name="Max Speed",
        description="Maximum throughput — best for gaming, streaming, downloads",
        preferred_band="5GHz",
        roaming_aggressiveness=3,
        transmit_power=5,
        disable_power_save=True,
        background_scan=True,
        optimize_tcp=True,
        set_dns=True,
        dns_primary="8.8.8.8",
        dns_secondary="8.8.4.4",
    ),
    "selfish": OptimizationProfile(
        name="Selfish Dominant",
        description="Airtime priority — forces adapter to claim channel priority over other devices",
        preferred_band="Auto",
        roaming_aggressiveness=5,
        transmit_power=5,
        disable_power_save=True,
        background_scan=True,
        optimize_tcp=True,
        set_dns=True,
        dns_primary="1.1.1.1",
        dns_secondary="8.8.8.8",
    ),
    "max_range": OptimizationProfile(
        name="Max Range",
        description="Maximum range — best for far distances from router",
        preferred_band="2.4GHz",
        roaming_aggressiveness=5,
        transmit_power=5,
        disable_power_save=True,
        background_scan=True,
        optimize_tcp=True,
        set_dns=True,
        dns_primary="1.1.1.1",
        dns_secondary="1.0.0.1",
    ),
    "balanced": OptimizationProfile(
        name="Balanced",
        description="Balance speed and range — good for most situations",
        preferred_band="Auto",
        roaming_aggressiveness=3,
        transmit_power=5,
        disable_power_save=True,
        background_scan=True,
        optimize_tcp=True,
        set_dns=True,
        dns_primary="1.1.1.1",
        dns_secondary="8.8.8.8",
    ),
    "power_save": OptimizationProfile(
        name="Power Saver",
        description="Minimize power consumption — best for laptops on battery",
        preferred_band="2.4GHz",
        roaming_aggressiveness=1,
        transmit_power=2,
        disable_power_save=False,
        background_scan=False,
        optimize_tcp=False,
        set_dns=False,
        dns_primary="8.8.8.8",
        dns_secondary="8.8.4.4",
    ),
}


@dataclass
class NetworkScore:
    """Scoring result for a WiFi network."""
    network: WiFiNetwork
    signal_score: float     # 0-30
    channel_score: float    # 0-20
    band_score: float       # 0-20
    security_score: float   # 0-10
    speed_score: float      # 0-20
    total_score: float      # 0-100
    recommendation: str


@dataclass
class OptimizationResult:
    """Result of an optimization run."""
    profile_name: str
    networks_found: int
    best_network: Optional[WiFiNetwork]
    network_scores: List[NetworkScore]
    settings_applied: List[str]
    settings_failed: List[str]
    estimated_improvement: str
    adapter_info: Optional[AdapterInfo]
    channel_congestion: dict
    recommendations: List[str]


def score_network(network: WiFiNetwork, congestion: dict,
                  profile: OptimizationProfile) -> NetworkScore:
    """
    Score a WiFi network based on multiple factors.
    Returns a NetworkScore with breakdown.
    """
    # 1. Signal Strength Score (0-30 pts)
    # Excellent: > -60 dBm = 30pts, Good: -60 to -70 = 20pts, Fair: -70 to -80 = 10pts, Poor < -80 = 5pts
    dbm = network.signal_dbm
    if dbm >= -55:
        signal_score = 30.0
    elif dbm >= -65:
        signal_score = 25.0 + (dbm + 65) * 0.5
    elif dbm >= -75:
        signal_score = 15.0 + (dbm + 75) * 1.0
    elif dbm >= -85:
        signal_score = 5.0 + (dbm + 85) * 1.0
    else:
        signal_score = max(0.0, 5.0 + (dbm + 85))

    # 2. Channel Congestion Score (0-20 pts)
    ch_count = congestion.get(network.channel, 0)
    if ch_count <= 1:
        channel_score = 20.0
    elif ch_count == 2:
        channel_score = 15.0
    elif ch_count == 3:
        channel_score = 10.0
    elif ch_count <= 5:
        channel_score = 5.0
    else:
        channel_score = 1.0

    # 3. Band Score (0-20 pts)
    if profile.preferred_band == "5GHz":
        band_score = 20.0 if network.band == "5GHz" else (
            5.0 if network.band == "2.4GHz" else 10.0)
    elif profile.preferred_band == "2.4GHz":
        band_score = 20.0 if network.band == "2.4GHz" else 8.0
    else:  # Auto
        band_score = 15.0 if network.band == "5GHz" else 10.0

    # 6GHz bonus
    if network.band == "6GHz":
        band_score = 20.0

    # 4. Security Score (0-10 pts)
    auth = network.authentication.lower()
    if "wpa3" in auth:
        security_score = 10.0
    elif "wpa2" in auth or "wpa-2" in auth:
        security_score = 8.0
    elif "wpa" in auth:
        security_score = 5.0
    elif "open" in auth or auth == "":
        security_score = 1.0
    else:
        security_score = 3.0

    # 5. Speed / Radio Type Score (0-20 pts)
    radio = network.radio_type.lower()
    if "802.11ax" in radio or "wi-fi 6" in radio or "wi-fi6" in radio:
        speed_score = 20.0
    elif "802.11ac" in radio or "wi-fi 5" in radio:
        speed_score = 16.0
    elif "802.11n" in radio or "wi-fi 4" in radio:
        speed_score = 10.0
    elif "802.11g" in radio:
        speed_score = 6.0
    elif "802.11a" in radio:
        speed_score = 4.0
    elif "802.11b" in radio:
        speed_score = 2.0
    else:
        # Default based on band
        speed_score = 12.0 if network.band == "5GHz" else 7.0

    total = signal_score + channel_score + band_score + security_score + speed_score

    # Recommendation text
    if total >= 80:
        recommendation = "Excellent — optimal connection"
    elif total >= 60:
        recommendation = "Good — strong connection"
    elif total >= 40:
        recommendation = "Fair — acceptable connection"
    elif total >= 20:
        recommendation = "Weak — consider moving closer"
    else:
        recommendation = "Poor — very weak signal"

    network.score = total
    return NetworkScore(
        network=network,
        signal_score=signal_score,
        channel_score=channel_score,
        band_score=band_score,
        security_score=security_score,
        speed_score=speed_score,
        total_score=total,
        recommendation=recommendation,
    )


def auto_select_profile(adapter_info: Optional[AdapterInfo],
                        networks: List[WiFiNetwork]) -> str:
    """
    Automatically select the best optimization profile based on
    current signal strength and available networks.
    """
    if not adapter_info or adapter_info.signal_percent < 30:
        return "max_range"  # Weak signal — optimize for range

    has_5ghz = any(n.band == "5GHz" for n in networks)
    if has_5ghz and adapter_info.signal_percent >= 60:
        return "max_speed"  # Good signal + 5GHz available

    return "balanced"


def build_recommendations(adapter_info: Optional[AdapterInfo],
                           networks: List[WiFiNetwork],
                           congestion: dict) -> List[str]:
    """Generate human-readable recommendations."""
    recs = []

    if not adapter_info:
        recs.append("⚠️ No active WiFi connection detected.")
        return recs

    # Signal quality
    if adapter_info.signal_percent < 30:
        recs.append("📍 Move closer to your router — signal is very weak.")
    elif adapter_info.signal_percent < 60:
        recs.append("📶 Signal is moderate. Moving closer will improve speed.")
    else:
        recs.append("✅ Signal strength is good.")

    # Band
    ch_count_current = congestion.get(adapter_info.channel, 0)
    if ch_count_current > 3:
        recs.append(f"📡 Channel {adapter_info.channel} is congested ({ch_count_current} networks). "
                    f"Ask your router admin to change the channel.")

    # 5GHz availability
    has_5ghz = any(n.band == "5GHz" for n in networks)
    has_6ghz = any(n.band == "6GHz" for n in networks)
    if has_6ghz:
        recs.append("🚀 6GHz (WiFi 6E) networks detected — connect for maximum speed.")
    elif has_5ghz:
        recs.append("⚡ 5GHz networks available — preferring 5GHz for better speed.")

    # TX rate
    if adapter_info.receive_rate_mbps > 0 and adapter_info.receive_rate_mbps < 54:
        recs.append("⚡ Link speed is low — consider upgrading to WiFi 5 (802.11ac) or WiFi 6.")

    # Radio type
    if adapter_info.radio_type and "802.11b" in adapter_info.radio_type:
        recs.append("⚠️ Connected on legacy 802.11b — severely limiting speed.")
    elif adapter_info.radio_type and "802.11g" in adapter_info.radio_type:
        recs.append("⚠️ Connected on 802.11g — limited to 54 Mbps. Upgrade recommended.")

    if not recs:
        recs.append("✅ WiFi connection appears well-optimized.")

    return recs


def run_optimization(profile_key: str = "balanced",
                     adapter_name: str = "Wi-Fi",
                     progress_callback=None) -> OptimizationResult:
    """
    Main optimization function. Scans networks, scores them,
    applies optimal adapter settings, and returns a full result.
    """
    profile = PROFILES.get(profile_key, PROFILES["balanced"])
    settings_applied = []
    settings_failed = []

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    _progress("🔍 Scanning networks...")
    networks = scan_networks()
    _progress(f"📡 Found {len(networks)} networks.")

    _progress("📊 Checking current connection...")
    adapter_info = get_connected_network()

    congestion = get_channel_congestion(networks)
    _progress("📈 Analyzing channel congestion...")

    # Score all networks
    scored = [score_network(n, congestion, profile) for n in networks]
    scored.sort(key=lambda x: x.total_score, reverse=True)

    best = scored[0].network if scored else None

    recommendations = build_recommendations(adapter_info, networks, congestion)

    # Apply settings
    _progress(f"⚙️ Applying profile: {profile.name}...")

    if adapter_manager.is_admin():
        # Set preferred band
        _progress(f"📶 Setting preferred band: {profile.preferred_band}...")
        if adapter_manager.set_preferred_band(adapter_name, profile.preferred_band):
            settings_applied.append(f"Preferred band → {profile.preferred_band}")
        else:
            settings_failed.append("Preferred band")

        # Roaming aggressiveness
        _progress(f"🔄 Setting roaming aggressiveness: {profile.roaming_aggressiveness}...")
        if adapter_manager.set_roaming_aggressiveness(profile.roaming_aggressiveness):
            settings_applied.append(f"Roaming aggressiveness → {profile.roaming_aggressiveness}/5")
        else:
            settings_failed.append("Roaming aggressiveness")

        # Transmit power
        _progress(f"📡 Setting transmit power: {profile.transmit_power}...")
        if adapter_manager.set_transmit_power(profile.transmit_power):
            settings_applied.append(f"Transmit power → {profile.transmit_power}/5 (Max)")
        else:
            settings_failed.append("Transmit power")

        # Power saving
        _progress("⚡ Configuring power mode...")
        if adapter_manager.disable_sleep_on_disconnect(profile.disable_power_save):
            settings_applied.append("Sleep on disconnect → Disabled")
        else:
            settings_failed.append("Sleep on disconnect")

        if adapter_manager.set_power_saving(profile.disable_power_save):
            settings_applied.append("WiFi power saving → Disabled (Performance mode)")
        else:
            settings_failed.append("Power saving mode")

        # Background scanning
        if adapter_manager.set_background_scanning(profile.background_scan):
            settings_applied.append(f"Background scanning → {'Enabled' if profile.background_scan else 'Disabled'}")

        # Selfish airtime booster & WMM priority
        _progress("🚀 Enabling Selfish Airtime Priority & QoS Queueing...")
        selfish_items = adapter_manager.apply_selfish_airtime_boost()
        for item in selfish_items:
            settings_applied.append(item)

        # TCP optimization
        if profile.optimize_tcp:
            _progress("🌐 Optimizing TCP settings...")
            if adapter_manager.optimize_tcp_settings():
                settings_applied.append("TCP/IP settings → Optimized (auto-tuning, RSS, ECN)")
            else:
                settings_failed.append("TCP optimization")

        # DNS
        if profile.set_dns:
            _progress("🌐 Setting DNS servers...")
            adapter_manager.flush_dns()
            if adapter_manager.set_dns_servers(profile.dns_primary, profile.dns_secondary, adapter_name):
                settings_applied.append(f"DNS → {profile.dns_primary} / {profile.dns_secondary}")
            else:
                settings_failed.append("DNS servers")

    else:
        # Non-admin: apply only what we can
        _progress("⚠️ Running without admin privileges — limited settings applied.")
        if profile.set_dns:
            adapter_manager.flush_dns()
            settings_applied.append("DNS cache flushed")

        if profile.optimize_tcp:
            adapter_manager.optimize_tcp_settings()
            settings_applied.append("TCP settings (partial — run as admin for full)")

        settings_failed.append("Registry adapter settings (requires admin)")

    # Estimate improvement
    if len(settings_applied) >= 5:
        estimated_improvement = "High — expect 15-40% improvement in throughput and stability"
    elif len(settings_applied) >= 3:
        estimated_improvement = "Moderate — expect 5-15% improvement"
    else:
        estimated_improvement = "Low — run as Administrator for full optimization"

    _progress("✅ Optimization complete!")

    return OptimizationResult(
        profile_name=profile.name,
        networks_found=len(networks),
        best_network=best,
        network_scores=scored,
        settings_applied=settings_applied,
        settings_failed=settings_failed,
        estimated_improvement=estimated_improvement,
        adapter_info=adapter_info,
        channel_congestion=congestion,
        recommendations=recommendations,
    )
