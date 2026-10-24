"""
WiFi Optimizer - Core Optimization Engine
=========================================

The engine is deliberately modest about what a client can change:

* It can pick the best visible BSS and configure the *client* adapter.
* It cannot raise the router's transmit power, change the router's antenna, or
  make a weak signal strong. Those are physics / AP-side decisions.
* Every reported "changed" setting is read back from the registry or system
  API; unchanged values are reported separately instead of being counted as
  wins. There is no fabricated percentage improvement.

The important new lever for far-from-router situations is the robustness
profile: force a 20 MHz channel, prefer 2.4 GHz, minimise roaming, disable
power save, and reload the miniport so the values actually take effect.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from core import adapter_manager
from core.wifi_scanner import (
    AdapterInfo, WiFiNetwork, get_channel_congestion, get_connected_network,
    get_saved_wifi_profiles, scan_networks,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Profiles
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class OptimizationProfile:
    """What the client side is allowed to do for a given goal."""
    name: str
    description: str
    preferred_band: str          # "5GHz", "2.4GHz", "Auto"
    roaming_aggressiveness: int  # 1-5 (5 = most aggressive)
    transmit_power: int          # 1-5 (5 = max)
    disable_power_save: bool
    background_scan: bool
    optimize_tcp: bool
    set_dns: bool
    dns_primary: str
    dns_secondary: str
    allow_insecure_networks: bool = False
    require_saved_profile: bool = False
    channel_width_24: str = "auto"   # "20MHz" | "auto"
    channel_width_52: str = "auto"
    latency_tweaks: bool = False     # U-APSD off, packet coalescing off, etc.
    ecn: bool = False                # opt-in TCP ECN
    restart_adapter: bool = True     # reload miniport so registry values apply


PROFILES: Dict[str, OptimizationProfile] = {
    "far_range": OptimizationProfile(
        name="Far / Robust",
        description=(
            "For weak/far links: 20 MHz width, 2.4 GHz, minimum roaming, no power "
            "save. Trades peak speed for a link that survives retries."
        ),
        preferred_band="2.4GHz",
        roaming_aggressiveness=1,
        transmit_power=5,
        disable_power_save=True,
        background_scan=False,
        optimize_tcp=False,
        set_dns=False,
        dns_primary="1.1.1.1",
        dns_secondary="1.0.0.1",
        channel_width_24="20MHz",
        channel_width_52="20MHz",
        latency_tweaks=False,
        ecn=False,
    ),
    "max_speed": OptimizationProfile(
        name="Max Speed",
        description="Highest throughput when the signal is already good (5/6 GHz).",
        preferred_band="5GHz",
        roaming_aggressiveness=3,
        transmit_power=5,
        disable_power_save=True,
        background_scan=False,
        optimize_tcp=True,
        set_dns=False,
        dns_primary="1.1.1.1",
        dns_secondary="1.0.0.1",
        channel_width_24="auto",
        channel_width_52="auto",
        latency_tweaks=True,
        ecn=False,
    ),
    "balanced": OptimizationProfile(
        name="Balanced",
        description="Prefer 5 GHz when it is usable, otherwise keep a stable 2.4 GHz link.",
        preferred_band="Auto",
        roaming_aggressiveness=2,
        transmit_power=5,
        disable_power_save=True,
        background_scan=False,
        optimize_tcp=True,
        set_dns=False,
        dns_primary="1.1.1.1",
        dns_secondary="1.0.0.1",
        channel_width_24="auto",
        channel_width_52="auto",
        latency_tweaks=False,
        ecn=False,
    ),
    "power_save": OptimizationProfile(
        name="Power Saver",
        description="Battery first. Accepts higher latency and lower throughput.",
        preferred_band="2.4GHz",
        roaming_aggressiveness=3,
        transmit_power=2,
        disable_power_save=False,
        background_scan=False,
        optimize_tcp=False,
        set_dns=False,
        dns_primary="1.1.1.1",
        dns_secondary="1.0.0.1",
        channel_width_24="auto",
        channel_width_52="auto",
        latency_tweaks=False,
        ecn=False,
    ),
}

# Backwards compatibility for scripts/UI state that still says "max_range" or
# "selfish". Both now mean the honest far/robust profile.
PROFILES["max_range"] = PROFILES["far_range"]
PROFILES["selfish"] = PROFILES["far_range"]


# ─────────────────────────────────────────────────────────────────────────────
# Scoring (kept simple and explainable)
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class NetworkScore:
    network: WiFiNetwork
    signal_score: float     # 0-30
    channel_score: float    # 0-20
    band_score: float       # 0-20
    security_score: float   # 0-10
    speed_score: float      # 0-20
    total_score: float      # 0-100
    recommendation: str
    is_eligible: bool = True
    ineligibility_reason: str = ""


@dataclass
class OptimizationResult:
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
    settings_unchanged: List[str] = field(default_factory=list)
    settings_skipped: List[str] = field(default_factory=list)
    restart_message: str = ""
    measured: Optional[dict] = None


def calculate_sig_score(dbm: float) -> float:
    """
    Logistic score for signal strength, 0-30 points, inflection at -72 dBm.
    This is a UI heuristic, not a calibrated throughput predictor.
    """
    try:
        return 30.0 / (1.0 + math.exp(-0.35 * (dbm + 72.0)))
    except OverflowError:
        return 0.0 if dbm < -72.0 else 30.0


def parse_radio_rank(radio_str: str) -> float:
    """Map a PHY name to a 0-20 capacity score."""
    r = (radio_str or "").lower()
    if "802.11be" in r or "wi-fi 7" in r or "wifi 7" in r:
        return 20.0
    if "802.11ax" in r or "wi-fi 6" in r or "wifi 6" in r or "wi-fi6" in r:
        return 18.0
    if "802.11ac" in r or "wi-fi 5" in r or "wifi 5" in r:
        return 14.0
    if "802.11n" in r or "wi-fi 4" in r or "wifi 4" in r:
        return 9.0
    if "802.11g" in r or "802.11a" in r:
        return 4.0
    if "802.11b" in r:
        return 1.0
    return 7.0


def score_network(network: WiFiNetwork, congestion: dict,
                  profile: OptimizationProfile,
                  all_networks: Optional[List[WiFiNetwork]] = None,
                  adapter_info: Optional[AdapterInfo] = None,
                  is_current_connected: bool = False,
                  saved_profiles: Optional[List[str]] = None) -> NetworkScore:
    """
    Score a BSS with an explicit, reproducible weighted sum:
        S = signal(30) + channel(20) + band(20) + PHY(20) + security(10)

    The old code called this "M-BFF" and multiplied by an unused BSS-load
    utilisation parameter that was always zero. That parameter is gone.
    """
    from core.wifi_scanner import calculate_effective_channel_congestion

    signal_score = max(0.0, min(30.0, calculate_sig_score(network.signal_dbm)))

    if all_networks:
        c_eff = calculate_effective_channel_congestion(network, all_networks)
    else:
        c_eff = max(0.0, float(congestion.get((network.band, network.channel), 1) - 1))
    channel_score = max(0.0, min(20.0, 20.0 - 5.0 * c_eff))

    if profile.preferred_band == "5GHz":
        g = 1.0 if network.band == "6GHz" else (0.95 if network.band == "5GHz" else 0.35)
    elif profile.preferred_band == "2.4GHz":
        g = 1.0 if network.band == "2.4GHz" else (0.6 if network.band == "5GHz" else 0.5)
    else:
        g = 1.0 if network.band == "6GHz" else (0.9 if network.band == "5GHz" else 0.5)

    # Link-margin taper: high bands need more signal to be usable.
    if network.band in ("5GHz", "6GHz"):
        h = max(0.1, min(1.0, (network.signal_dbm + 80.0) / 15.0))
    else:
        h = max(0.1, min(1.0, (network.signal_dbm + 95.0) / 20.0))
    band_score = max(0.0, min(20.0, 20.0 * g * h))

    auth = (network.authentication or "").lower()
    enc = (network.encryption or "").lower()
    is_open = network.authentication == "Open" or "open" in auth
    is_wep = "wep" in auth or "wep" in enc

    if "wpa3" in auth:
        security_score = 10.0
    elif "wpa2" in auth:
        security_score = 3.0 if "tkip" in enc else 8.0
    elif "wpa" in auth:
        security_score = 3.0
    elif is_wep or is_open:
        security_score = 0.0
    else:
        # Unknown (e.g. an unparsed enterprise IE): do not claim it is secure.
        security_score = 2.0

    is_eligible, ineligibility_reason = True, ""
    if (is_open or is_wep) and not profile.allow_insecure_networks:
        is_eligible = False
        ineligibility_reason = ("Excluded by security policy: open network"
                                if is_open else
                                "Excluded by security policy: WEP")
    if profile.require_saved_profile and saved_profiles is not None:
        if network.ssid not in saved_profiles:
            is_eligible = False
            ineligibility_reason = f"SSID '{network.ssid}' is not a saved profile"

    ap_phy = parse_radio_rank(network.radio_type)
    speed_score = min(ap_phy, parse_radio_rank(adapter_info.radio_type)) if (
        adapter_info and adapter_info.radio_type) else ap_phy
    speed_score = max(0.0, min(20.0, speed_score))

    total = max(0.0, min(100.0, signal_score + channel_score + band_score
                        + security_score + speed_score))

    if not is_eligible:
        recommendation = f"Disqualified — {ineligibility_reason}"
    elif total >= 80:
        recommendation = "Excellent — strong link margin"
    elif total >= 60:
        recommendation = "Good — stable connection"
    elif total >= 40:
        recommendation = "Fair — usable, but retries likely"
    elif total >= 20:
        recommendation = "Weak — expect retransmissions"
    else:
        recommendation = "Poor — near the sensitivity limit"

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
                         cooldown_sec: float = 30.0) -> Tuple[bool, str]:
    """Hysteresis + cooldown gate to prevent BSS ping-pong."""
    elapsed = current_timestamp - last_roam_timestamp
    if elapsed < cooldown_sec:
        return False, f"Cooldown active ({cooldown_sec - elapsed:.1f}s remaining)"
    delta = candidate_score - current_score
    if delta < hysteresis_threshold:
        return False, (f"Delta {delta:.1f} pts below hysteresis margin "
                       f"(+{hysteresis_threshold:.1f} pts required)")
    return True, f"Eligible: candidate exceeds current link by {delta:.1f} pts"


def auto_select_profile(adapter_info: Optional[AdapterInfo],
                        networks: List[WiFiNetwork]) -> str:
    """Pick a starting profile from signal and available bands."""
    if not adapter_info:
        return "balanced"
    # Use real dBm when available; the percentage is driver-defined.
    if adapter_info.signal_dbm <= -75 or adapter_info.signal_percent < 35:
        return "far_range"
    has_5ghz = any(n.band == "5GHz" for n in networks)
    if has_5ghz and adapter_info.signal_dbm >= -67:
        return "max_speed"
    return "balanced"


def build_recommendations(adapter_info: Optional[AdapterInfo],
                          networks: List[WiFiNetwork],
                          congestion: dict) -> List[str]:
    """Human-readable, honest advice."""
    recs: List[str] = []
    if not adapter_info:
        return ["⚠️ No active Wi-Fi connection detected."]

    dbm = adapter_info.signal_dbm
    if dbm <= -80:
        recs.append("📍 Very weak signal (≤ -80 dBm). Move closer, or add a mesh "
                    "node/AP on a wired backhaul. No client setting fixes this.")
    elif dbm <= -70:
        recs.append("📶 Weak signal. A 20 MHz channel and 2.4 GHz usually hold up "
                    "better than 80 MHz at this range.")
    elif dbm <= -60:
        recs.append("📶 Moderate signal. 5 GHz is usable; expect some retries.")
    else:
        recs.append("✅ Signal is good for this link.")

    current = congestion.get((adapter_info.band, adapter_info.channel), 0)
    if current > 3:
        recs.append(f"📡 {current - 1} other BSSes share {adapter_info.band} "
                    f"channel {adapter_info.channel}. Only the router admin can "
                    f"change the AP channel; the client can move to another band/AP.")

    if any(n.band == "6GHz" for n in networks):
        recs.append("🚀 6 GHz networks are visible. They only help at short range.")
    elif any(n.band == "5GHz" for n in networks):
        recs.append("⚡ 5 GHz is available. Prefer it only while its RSSI is "
                    "within ~10 dB of the 2.4 GHz AP.")

    if adapter_info.band == "2.4GHz" and dbm > -60:
        recs.append("ℹ️ You are on 2.4 GHz with a strong signal; switching to 5 GHz "
                    "would likely raise throughput.")

    if adapter_info.receive_rate_mbps > 0:
        recs.append(f"🔗 Negotiated link rate: {adapter_info.receive_rate_mbps:.0f} Mbps "
                    f"rx / {adapter_info.transmit_rate_mbps:.0f} Mbps tx. This is the "
                    f"PHY rate, not measured Internet throughput.")

    if adapter_info.radio_type and any(k in adapter_info.radio_type for k in ("802.11b", "802.11g")):
        recs.append("⚠️ Legacy PHY negotiated. Upgrading the adapter/AP would help more "
                    "than any registry tweak.")

    return recs


# ─────────────────────────────────────────────────────────────────────────────
# Apply
# ─────────────────────────────────────────────────────────────────────────────
def _ledger_labels(entries: List[dict]) -> List[str]:
    """De-duplicate change-ledger entries into human labels."""
    out: List[str] = []
    seen = set()
    for e in entries:
        intent = e.get("intent") or e.get("property") or "setting"
        new = e.get("new")
        old = e.get("old")
        if isinstance(new, (list, tuple)):
            new = " / ".join(str(x) for x in new)
        label = f"{intent} → {new}" if new is not None else intent
        if old is not None and str(old) == str(new):
            continue
        if label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def _ledger_failures(entries: List[dict]) -> List[str]:
    out, seen = [], set()
    for e in entries:
        intent = e.get("intent") or e.get("property") or "setting"
        reason = e.get("reason") or "failed"
        label = f"{intent}: {reason}"
        if label not in seen:
            seen.add(label)
            out.append(label)
    return out


def run_optimization(profile_key: str = "balanced",
                     adapter_name: str = "Wi-Fi",
                     progress_callback=None,
                     preview: bool = False,
                     measure: bool = False,
                     dns_primary: Optional[str] = None,
                     dns_secondary: Optional[str] = None,
                     apply_dns: Optional[bool] = None,
                     apply_ecn: Optional[bool] = None) -> OptimizationResult:
    """
    Scan, score, and apply a profile.

    preview=True records what *would* change and writes nothing.
    measure=True runs the benchmark before and after (adds ~40-90 s).
    dns_primary/dns_secondary override the profile defaults (from the Settings
    page) so those fields are not dead controls. apply_dns/apply_ecn override the
    profile opt-ins; both are off by default.
    """
    profile = PROFILES.get(profile_key, PROFILES["balanced"])
    preview_changes: List[dict] = []

    adapter_manager.set_preview_mode(bool(preview))
    adapter_manager.begin_run()

    def _progress(msg: str):
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    before_measure = None
    measured: Optional[dict] = None
    if measure and not preview:
        try:
            from core.benchmark import measure as _measure
            _progress("⏱️ Measuring the current link (baseline)...")
            before_measure = _measure().to_dict()
        except Exception as e:
            logger.warning("Baseline measurement failed: %s", e)
            _progress(f"⚠️ Baseline measurement failed: {e}")

    _progress("🔍 Scanning networks...")
    networks = scan_networks()
    _progress(f"📡 Found {len(networks)} BSS(es).")

    _progress("📊 Checking current connection...")
    adapter_info = get_connected_network()
    effective_adapter = (adapter_info.name if adapter_info and adapter_info.name
                         else adapter_name)

    congestion = get_channel_congestion(networks)
    _progress("📈 Analyzing channel congestion...")
    saved_profiles = get_saved_wifi_profiles()

    scored = [
        score_network(n, congestion, profile, all_networks=networks,
                      adapter_info=adapter_info, saved_profiles=saved_profiles)
        for n in networks
    ]
    scored.sort(key=lambda x: (x.is_eligible, x.total_score), reverse=True)
    eligible = [s for s in scored if s.is_eligible]
    best = eligible[0].network if eligible else None

    recommendations = build_recommendations(adapter_info, networks, congestion)

    is_admin = adapter_manager.is_admin()
    if not preview:
        # Snapshot even when not elevated: DNS/TCP changes can still be applied,
        # and Restore Defaults must be able to undo them.
        _progress("🛡️ Backing up current adapter + system settings...")
        try:
            adapter_manager.backup_adapter_settings(effective_adapter)
        except Exception as e:
            logger.warning("Backup failed (continuing): %s", e)

    if preview:
        _progress(f"👀 Preview: showing what '{profile.name}' would change...")
    else:
        _progress(f"⚙️ Applying profile: {profile.name}...")

    # Registry / driver settings require administrator. DNS and TCP calls are
    # attempted either way; failures are recorded honestly.
    if is_admin or preview:
        _progress(f"📶 Preferred band → {profile.preferred_band}")
        adapter_manager.set_preferred_band(effective_adapter, profile.preferred_band)

        _progress(f"🔄 Roaming aggressiveness → {profile.roaming_aggressiveness}/5")
        adapter_manager.set_roaming_aggressiveness(profile.roaming_aggressiveness)

        _progress(f"📡 Transmit power → {profile.transmit_power}/5")
        adapter_manager.set_transmit_power(profile.transmit_power)

        _progress("⚡ Sleep-on-disconnect / power mode...")
        adapter_manager.disable_sleep_on_disconnect(profile.disable_power_save)
        adapter_manager.set_power_saving(profile.disable_power_save)

        adapter_manager.set_background_scanning(profile.background_scan)

        _progress("📐 Channel width...")
        adapter_manager.set_channel_width("2.4GHz", profile.channel_width_24)
        adapter_manager.set_channel_width("5GHz", profile.channel_width_52)

        if profile.latency_tweaks:
            _progress("🛠️ Optional latency tweaks (opt-in)...")
            adapter_manager.apply_latency_tweaks(
                connection_phy=(adapter_info.radio_type if adapter_info else None))
    else:
        _progress("⚠️ Not elevated: driver/registry settings need Administrator.")
        adapter_manager.record_note("failed", adapter="system", property="driver settings",
                              intent="Driver settings",
                              reason="requires administrator privileges")

    # ECN is opt-in from Settings. When it is enabled, run the TCP pass even for
    # a profile that normally leaves TCP alone; autotuning is usually already
    # `normal`, so the extra write is a noop.
    do_ecn = profile.ecn if apply_ecn is None else bool(apply_ecn)
    if profile.optimize_tcp or do_ecn:
        _progress("🌐 TCP stack state...")
        adapter_manager.optimize_tcp_settings(effective_adapter, ecn=do_ecn)

    # DNS is opt-in: it does not improve RF, and changing it can break local
    # hostnames. `apply_dns=None` keeps the profile's flag (used by scripts).
    do_dns = profile.set_dns if apply_dns is None else bool(apply_dns)
    if do_dns:
        _progress("🌐 DNS resolvers...")
        adapter_manager.flush_dns()
        adapter_manager.set_dns_servers(
            dns_primary or profile.dns_primary,
            dns_secondary or profile.dns_secondary,
            effective_adapter)

    ledger = adapter_manager.get_run_changes()

    restart_message = ""
    # Only NDIS/registry changes require a miniport reload. DNS / power-plan /
    # TCP changes take effect immediately and should not drop the link.
    driver_changes = [e for e in ledger["changed"]
                      if e.get("subkey") not in (None, "", "-", "system")]
    if preview:
        preview_changes = adapter_manager.get_preview_changes()
        adapter_manager.set_preview_mode(False)
    else:
        if profile.restart_adapter and driver_changes:
            _progress("🔁 Reloading the Wi-Fi adapter so the new values take effect...")
            ok, restart_message = adapter_manager.restart_adapter(effective_adapter)
            if not ok:
                restart_message += (" Until the adapter is reloaded (or the PC "
                                    "reboots), NDIS registry values may not apply.")
            _progress(("✅ " if ok else "⚠️ ") + restart_message)
        elif not ledger["changed"]:
            if ledger["failed"]:
                restart_message = ("No changes were applied — driver settings require "
                                   "Administrator. Relaunch the app elevated.")
            else:
                restart_message = "No changes were needed; adapter reload skipped."
        elif not driver_changes:
            restart_message = ("Only system settings (DNS / power / TCP) changed; "
                               "they apply immediately, so the adapter was not reloaded.")
        else:
            restart_message = "Adapter reload skipped by profile."

        if measure:
            try:
                from core.benchmark import measure as _measure
                _progress("⏱️ Measuring the link after the change (settling 5 s)...")
                import time as _time
                _time.sleep(5)
                after = _measure().to_dict()
                measured = {"before": before_measure, "after": after}
            except Exception as e:
                logger.warning("Post-change measurement failed: %s", e)
                measured = {"before": before_measure, "after": None, "error": str(e)}
        else:
            measured = None

    settings_applied = _ledger_labels(ledger["changed"])
    settings_unchanged = _ledger_labels(ledger["noop"])
    settings_failed = _ledger_failures(ledger["failed"])
    settings_skipped = _ledger_failures(ledger["skipped"])

    if preview:
        estimated = "Not measured (preview) — no changes were written."
    elif measured and measured.get("before") and measured.get("after"):
        b, a = measured["before"], measured["after"]
        estimated = (f"Measured: {b.get('throughput_mbps', 0):.1f} → "
                     f"{a.get('throughput_mbps', 0):.1f} Mbps, "
                     f"latency p95 {b.get('latency_p95_ms', 0):.0f} → "
                     f"{a.get('latency_p95_ms', 0):.0f} ms.")
    else:
        estimated = ("Not measured. Use Measure before/after (or the benchmark "
                     "script) to compare real throughput and latency.")

    if preview:
        _progress(f"✅ Preview complete — {len(preview_changes)} proposed change(s).")
    else:
        _progress(f"✅ Done — {len(settings_applied)} changed, "
                  f"{len(settings_unchanged)} already at target, "
                  f"{len(settings_failed)} failed, {len(settings_skipped)} unsupported.")

    return OptimizationResult(
        profile_name=profile.name,
        networks_found=len(networks),
        best_network=best,
        network_scores=scored,
        settings_applied=settings_applied,
        settings_failed=settings_failed,
        estimated_improvement=estimated,
        adapter_info=adapter_info,
        channel_congestion=congestion,
        recommendations=recommendations,
        preview_changes=preview_changes,
        settings_unchanged=settings_unchanged,
        settings_skipped=settings_skipped,
        restart_message=restart_message,
        measured=measured,
    )
