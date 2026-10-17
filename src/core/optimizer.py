"""
WiFi Optimizer - Core Optimization Algorithm
Analyzes scanned networks and applies the best settings for maximum performance.
"""

import logging
import math
from dataclasses import dataclass, field
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
    allow_insecure_networks: bool = False  # Hard security gate: disallow Open and WEP
    require_saved_profile: bool = False    # Roaming filter: only recommend networks already configured


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
        allow_insecure_networks=False,
        require_saved_profile=False,
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
        allow_insecure_networks=False,
        require_saved_profile=False,
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
        allow_insecure_networks=False,
        require_saved_profile=False,
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
        allow_insecure_networks=False,
        require_saved_profile=False,
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
        allow_insecure_networks=False,
        require_saved_profile=False,
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
    is_eligible: bool = True               # True security gate: False excludes AP from recommendation
    ineligibility_reason: str = ""         # Reason if disqualified by policy



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
    preview_changes: List[dict] = field(default_factory=list)


def calculate_sig_score(dbm: float) -> float:
    """
    Sigmoid curve for signal strength score (0-30 pts):
    f_sig(P_rx) = 30 / (1 + exp(-0.35 * (P_rx + 72)))
    Inflection point at -72 dBm (15 pts).
    Near saturation at >= -55 dBm (~30 pts).
    Steep cliff below -75 dBm down to ~0.05 pts at -90 dBm.
    """
    try:
        exp_val = math.exp(-0.35 * (dbm + 72.0))
        return 30.0 / (1.0 + exp_val)
    except OverflowError:
        return 0.0 if dbm < -72.0 else 30.0


def parse_radio_rank(radio_str: str) -> float:
    """Map radio type string to PHY score (0-20 pts)."""
    r = radio_str.lower()
    if "802.11be" in r or "wi-fi 7" in r or "wifi 7" in r:
        return 20.0
    if "802.11ax" in r or "wi-fi 6" in r or "wifi 6" in r or "wi-fi6" in r:
        return 18.0
    if "802.11ac" in r or "wi-fi 5" in r or "wifi 5" in r:
        return 14.0
    if "802.11n" in r or "wi-fi 4" in r or "wifi 4" in r:
        return 9.0
    if "802.11g" in r:
        return 4.0
    if "802.11a" in r:
        return 4.0
    if "802.11b" in r:
        return 1.0
    return 7.0


def score_network(network: WiFiNetwork, congestion: dict,
                  profile: OptimizationProfile,
                  all_networks: Optional[List[WiFiNetwork]] = None,
                  adapter_info: Optional[AdapterInfo] = None,
                  is_current_connected: bool = False,
                  saved_profiles: Optional[List[str]] = None,
                  bss_load_utilization: float = 0.0) -> NetworkScore:
    """
    Score a WiFi network using the Multi-Objective BSSID Fitness Function (M-BFF).
    S(N) = f_sig(P_rx) + f_cong(C) + f_band(B, P_rx) + f_phy(R) + f_sec(A)
    Includes hard security policy gating and saved profile filtering.
    """
    from core.wifi_scanner import calculate_effective_channel_congestion

    # 1. Signal Strength Score (0-30 pts) - Continuous Sigmoid
    signal_score = calculate_sig_score(network.signal_dbm)
    signal_score = max(0.0, min(30.0, signal_score))

    # 2. Channel Congestion Score (0-20 pts)
    # Penalizes co-channel and adjacent-channel interference scaled by BSS Load (u)
    if all_networks:
        c_eff = calculate_effective_channel_congestion(network, all_networks)
    else:
        # Fallback to simple channel count if full list not provided
        c_eff = max(0.0, float(congestion.get(network.channel, 1) - 1))

    u = max(0.0, min(1.0, bss_load_utilization))
    channel_score = max(0.0, 20.0 - 5.0 * c_eff) * (1.0 - u)
    channel_score = max(0.0, min(20.0, channel_score))

    # 3. Band Matching Score (0-20 pts)
    if profile.preferred_band == "5GHz" or profile.name == "Max Speed":
        g = 1.0 if network.band == "6GHz" else (0.95 if network.band == "5GHz" else 0.35)
    elif profile.preferred_band == "2.4GHz" or profile.name == "Max Range":
        g = 1.0 if network.band == "2.4GHz" else (0.6 if network.band == "5GHz" else 0.5)
    else:  # Balanced / Selfish / Auto
        g = 1.0 if network.band == "6GHz" else (0.9 if network.band == "5GHz" else 0.5)

    # Link margin distance attenuation factor h(P_rx)
    if network.band in ("5GHz", "6GHz"):
        h = max(0.1, min(1.0, (network.signal_dbm + 80.0) / 15.0))
    else:
        h = max(0.1, min(1.0, (network.signal_dbm + 95.0) / 20.0))

    band_score = 20.0 * g * h
    band_score = max(0.0, min(20.0, band_score))

    # 4. Security Score (0-10 pts) and Security Policy Gating
    auth = network.authentication.lower()
    enc = network.encryption.lower()
    is_open = ("open" in auth or auth == "")
    is_wep = ("wep" in auth)

    if "wpa3" in auth:
        security_score = 10.0
    elif "wpa2" in auth or "wpa-2" in auth:
        if "tkip" in enc:
            security_score = 3.0
        else:
            security_score = 8.0
    elif "wpa" in auth:
        security_score = 3.0
    elif is_wep or is_open:
        security_score = 0.0
    else:
        security_score = 2.0

    # Policy Gating
    is_eligible = True
    ineligibility_reason = ""

    if (is_open or is_wep) and not profile.allow_insecure_networks:
        is_eligible = False
        ineligibility_reason = (
            "Excluded by Security Policy: Plaintext Open network" if is_open
            else "Excluded by Security Policy: Broken WEP RC4 cipher"
        )

    if profile.require_saved_profile and saved_profiles is not None:
        if network.ssid not in saved_profiles:
            is_eligible = False
            ineligibility_reason = f"Excluded by Policy: SSID '{network.ssid}' is not in saved profiles."

    # 5. PHY / Radio Tech Score (0-20 pts)
    ap_phy = parse_radio_rank(network.radio_type)
    if adapter_info and adapter_info.radio_type:
        adapter_phy = parse_radio_rank(adapter_info.radio_type)
        speed_score = min(ap_phy, adapter_phy)
    else:
        speed_score = ap_phy
    speed_score = max(0.0, min(20.0, speed_score))

    total = signal_score + channel_score + band_score + security_score + speed_score
    total = max(0.0, min(100.0, total))

    # Recommendation text
    if not is_eligible:
        recommendation = f"Disqualified — {ineligibility_reason}"
    elif total >= 80:
        recommendation = "Excellent — optimal link margin and capacity"
    elif total >= 60:
        recommendation = "Good — stable connection"
    elif total >= 40:
        recommendation = "Fair — acceptable link margin"
    elif total >= 20:
        recommendation = "Weak — high retransmission probability"
    else:
        recommendation = "Poor — marginal link near sensitivity threshold"

    network.score = round(total, 2)
    return NetworkScore(
        network=network,
        signal_score=round(signal_score, 2),
        channel_score=round(channel_score, 2),
        band_score=round(band_score, 2),
        security_score=round(security_score, 2),
        speed_score=round(speed_score, 2),
        total_score=round(total, 2),
        recommendation=recommendation,
        is_eligible=is_eligible,
        ineligibility_reason=ineligibility_reason,
    )


def should_roam_decision(current_score: float, candidate_score: float,
                         last_roam_timestamp: float, current_timestamp: float,
                         hysteresis_threshold: float = 10.0,
                         cooldown_sec: float = 30.0) -> tuple[bool, str]:
    """
    Hysteresis and cooldown gate for AP selection:
    Prevents ping-pong / flapping across adjacent BSSIDs.
    - Requires candidate score > current score + hysteresis_threshold
    - Requires elapsed time >= cooldown_sec
    """
    elapsed = current_timestamp - last_roam_timestamp
    if elapsed < cooldown_sec:
        return False, f"Cooldown active ({cooldown_sec - elapsed:.1f}s remaining)"

    delta = candidate_score - current_score
    if delta < hysteresis_threshold:
        return False, f"Delta {delta:.1f} pts below hysteresis margin (+{hysteresis_threshold:.1f} pts required)"

    return True, f"Eligible: Candidate exceeds current link by {delta:.1f} pts"



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
                     progress_callback=None,
                     preview: bool = False) -> OptimizationResult:
    """
    Main optimization function. Scans networks, scores them,
    applies optimal adapter settings, and returns a full result.

    When preview=True, nothing is written to the system: every proposed change
    is recorded (real current value -> proposed value) and returned in
    OptimizationResult.preview_changes so the user can review before applying.
    """
    profile = PROFILES.get(profile_key, PROFILES["balanced"])
    settings_applied = []
    settings_failed = []
    preview_changes: List[dict] = []

    if preview:
        adapter_manager.set_preview_mode(True)
    else:
        # Defensive: ensure a real run is never silently suppressed by a
        # preview flag left on from an interrupted preview.
        adapter_manager.set_preview_mode(False)

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    _progress("🔍 Scanning networks...")
    networks = scan_networks()
    _progress(f"📡 Found {len(networks)} networks.")

    _progress("📊 Checking current connection...")
    adapter_info = get_connected_network()

    # Prefer the LIVE connected interface name for netsh commands. The name in
    # Settings may be stale or generic ("Wi-Fi") while the real interface on
    # this machine is e.g. "Wi-Fi 4" — using the wrong name silently fails.
    effective_adapter = adapter_info.name if (adapter_info and adapter_info.name) else adapter_name

    congestion = get_channel_congestion(networks)
    _progress("📈 Analyzing channel congestion...")

    from core.wifi_scanner import get_saved_wifi_profiles
    saved_profiles = get_saved_wifi_profiles()

    # Score all networks with corrected M-BFF and Security Gating
    scored = [score_network(n, congestion, profile, all_networks=networks, adapter_info=adapter_info, saved_profiles=saved_profiles) for n in networks]
    # Rank eligible networks first, then by total score
    scored.sort(key=lambda x: (x.is_eligible, x.total_score), reverse=True)

    eligible_candidates = [s for s in scored if s.is_eligible]
    best = eligible_candidates[0].network if eligible_candidates else None

    recommendations = build_recommendations(adapter_info, networks, congestion)

    # ── Safety: full independent backup of current adapter settings ──────────
    if adapter_manager.is_admin() and not preview:
        _progress("🛡️ Backing up current adapter settings...")
        try:
            adapter_manager.backup_adapter_settings()
        except Exception as e:
            logger.warning("Adapter backup failed (continuing): %s", e)

    # Apply settings
    if preview:
        _progress(f"👀 Preview mode — showing what '{profile.name}' would change...")
    else:
        _progress(f"⚙️ Applying profile: {profile.name}...")

    if adapter_manager.is_admin() or preview:
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
            if adapter_manager.optimize_tcp_settings(effective_adapter):
                settings_applied.append("TCP/IP settings → Optimized (auto-tuning, RSS, ECN)")
            else:
                settings_failed.append("TCP optimization")

        # DNS
        if profile.set_dns:
            _progress("🌐 Setting DNS servers...")
            adapter_manager.flush_dns()
            if adapter_manager.set_dns_servers(profile.dns_primary, profile.dns_secondary,
                                               effective_adapter):
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
            adapter_manager.optimize_tcp_settings(effective_adapter)
            settings_applied.append("TCP settings (partial — run as admin for full)")

        settings_failed.append("Registry adapter settings (requires admin)")

    # Estimate improvement
    if len(settings_applied) >= 5:
        estimated_improvement = "High — expect 15-40% improvement in throughput and stability"
    elif len(settings_applied) >= 3:
        estimated_improvement = "Moderate — expect 5-15% improvement"
    else:
        estimated_improvement = "Low — run as Administrator for full optimization"

    if preview:
        preview_changes = adapter_manager.get_preview_changes()
        adapter_manager.set_preview_mode(False)
        _progress(f"✅ Preview complete — {len(preview_changes)} change(s) would be made.")
    else:
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
        preview_changes=preview_changes,
    )
