"""
WiFi Optimizer - Fast core checks (no admin, no network writes)
==============================================================

Run from the project root:

    python scripts/test_core.py

These tests cover the pure logic: unit conversions, band-aware channel
classification, scoring gates, profile definitions, the robustness controller's
hysteresis/cooldown, the benchmark dataclass, read-only system queries, and the
change ledger. They never write to the registry, the adapter, or the network.
"""

import os
import sys

# Windows consoles may default to cp1252; the test labels contain symbols.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(BASE_DIR, "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

import math  # noqa: E402

from core import adapter_manager  # noqa: E402
from core.benchmark import BenchmarkResult  # noqa: E402
from core.optimizer import (  # noqa: E402
    PROFILES, OptimizationResult, calculate_sig_score, parse_radio_rank,
    score_network,
)
from core.robustness import RobustnessController  # noqa: E402
from core.wifi_scanner import (  # noqa: E402
    WiFiNetwork, calculate_channel_overlap, calculate_effective_channel_congestion,
    channel_to_frequency, get_channel_congestion, percent_to_dbm,
)

PASSED = 0
FAILED = 0


def check(name, condition, detail=""):
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  PASS  {name}")
    else:
        FAILED += 1
        print(f"  FAIL  {name}  {detail}")


def close(a, b, tol=1e-6):
    return abs(float(a) - float(b)) <= tol


def test_conversions():
    print("\n[conversions]")
    check("percent_to_dbm(0) == -100", close(percent_to_dbm(0), -100.0))
    check("percent_to_dbm(50) == -75", close(percent_to_dbm(50), -75.0))
    check("percent_to_dbm(100) == -50", close(percent_to_dbm(100), -50.0))
    check("2.4 GHz ch 6 == 2437", channel_to_frequency(6, "2.4GHz") == (2437.0, "2.4GHz"))
    check("5 GHz ch 36 == 5180", channel_to_frequency(36, "5GHz") == (5180.0, "5GHz"))
    check("6 GHz ch 1 == 5955", channel_to_frequency(1, "6GHz") == (5955.0, "6GHz"))
    check("6 GHz ch 37 == 6135", channel_to_frequency(37, "6GHz") == (6135.0, "6GHz"))
    # Without a band, channels 1-14 are treated as 2.4 GHz (documented ambiguity).
    check("ambiguous ch 1 defaults to 2.4", channel_to_frequency(1)[1] == "2.4GHz")


def test_overlap():
    print("\n[channel overlap]")
    check("same channel == 1.0", calculate_channel_overlap(6, "2.4GHz", 6, "2.4GHz") == 1.0)
    check("adjacent 2.4 overlap", close(calculate_channel_overlap(1, "2.4GHz", 2, "2.4GHz"),
                                        1.0 - 5.0 / 22.0, 0.01))
    check("5 GHz adjacent is orthogonal",
          calculate_channel_overlap(36, "5GHz", 40, "5GHz") == 0.0)
    check("cross-band overlap is zero",
          calculate_channel_overlap(1, "2.4GHz", 1, "6GHz") == 0.0)


def test_congestion_keys():
    print("\n[congestion]")
    nets = [
        WiFiNetwork(ssid="a", bssid="00:00:00:00:00:01", band="2.4GHz", channel=1,
                    signal_dbm=-60),
        WiFiNetwork(ssid="b", bssid="00:00:00:00:00:02", band="6GHz", channel=1,
                    signal_dbm=-70),
        WiFiNetwork(ssid="c", bssid="00:00:00:00:00:03", band="2.4GHz", channel=1,
                    signal_dbm=-70),
    ]
    cong = get_channel_congestion(nets)
    check("2.4 ch1 counted separately from 6 GHz ch1", cong[("2.4GHz", 1)] == 2)
    check("6 GHz ch1 count == 1", cong[("6GHz", 1)] == 1)

    target = WiFiNetwork(ssid="a", bssid="00:00:00:00:00:01", band="2.4GHz",
                         channel=1, signal_dbm=-60)
    c_eff = calculate_effective_channel_congestion(target, nets)
    check("C_eff includes the other 2.4 GHz ch1 BSS", c_eff > 0)
    check("C_eff excludes the 6 GHz BSS", c_eff < 1.0)


def test_scoring():
    print("\n[scoring]")
    check("sigmoid midpoint -72 ≈ 15", close(calculate_sig_score(-72), 15.0, 0.01))
    check("sigscore monotonic", calculate_sig_score(-50) > calculate_sig_score(-80))
    check("PHY rank 802.11ax > 802.11n",
          parse_radio_rank("802.11ax") > parse_radio_rank("802.11n"))

    profile = PROFILES["balanced"]
    secure = WiFiNetwork(ssid="Secure", bssid="00:00:00:00:00:11",
                         signal_dbm=-60, band="5GHz", channel=36,
                         authentication="WPA2-Personal", encryption="CCMP",
                         radio_type="802.11ax")
    open_net = WiFiNetwork(ssid="Open", bssid="00:00:00:00:00:12",
                           signal_dbm=-40, band="2.4GHz", channel=6,
                           authentication="Open", encryption="None",
                           radio_type="802.11n")
    s1 = score_network(secure, {}, profile, all_networks=[secure, open_net])
    s2 = score_network(open_net, {}, profile, all_networks=[secure, open_net])
    check("secure network eligible", s1.is_eligible)
    check("open network gated out", not s2.is_eligible)
    check("open network reason mentions security", "security" in s2.ineligibility_reason.lower())
    check("score bounded 0..100", 0 <= s1.total_score <= 100)
    check("WPA3 > WPA2/TKIP security score",
          score_network(WiFiNetwork(ssid="x", authentication="WPA3-Personal",
                                    encryption="CCMP", signal_dbm=-60,
                                    band="5GHz", channel=36),
                        {}, profile).security_score >
          score_network(WiFiNetwork(ssid="y", authentication="WPA2-Personal",
                                    encryption="TKIP", signal_dbm=-60,
                                    band="5GHz", channel=36),
                        {}, profile).security_score)


def test_profiles():
    print("\n[profiles]")
    for key in ("far_range", "balanced", "max_speed", "power_save"):
        check(f"profile {key} exists", key in PROFILES)
    check("far_range forces 20 MHz on 2.4", PROFILES["far_range"].channel_width_24 == "20MHz")
    check("far_range prefers 2.4 GHz", PROFILES["far_range"].preferred_band == "2.4GHz")
    check("far_range roaming is minimum", PROFILES["far_range"].roaming_aggressiveness == 1)
    check("legacy 'selfish' alias maps to far_range", PROFILES["selfish"] is PROFILES["far_range"])
    check("max_speed prefers 5 GHz", PROFILES["max_speed"].preferred_band == "5GHz")


def test_controller():
    print("\n[robustness controller]")
    c = RobustnessController(cooldown_s=0, consecutive=2, window=2)
    # -78 dBm is degraded but not the "extreme" (-80 or worse) immediate path.
    d1 = c.observe(-78, efficiency=0.05, now=1)
    d2 = c.observe(-78, efficiency=0.05, now=2)
    check("first degraded sample does not switch", not d1.changed)
    check("second degraded sample switches to far_range",
          d2.changed and d2.mode == "far_range")
    d3 = c.observe(-78, efficiency=0.05, now=3)
    check("stays in far_range while degraded", not d3.changed and d3.mode == "far_range")

    c2 = RobustnessController(cooldown_s=0, consecutive=2, window=2)
    for i in range(3):
        c2.observe(-55, efficiency=0.40, now=10 + i)
    check("healthy link moves to max_speed", c2.mode == "max_speed")

    c3 = RobustnessController(cooldown_s=600, consecutive=1, window=2)
    c3.observe(-80, efficiency=0.05, now=1)
    c3._mode = "max_speed"
    d = c3.observe(-80, efficiency=0.05, now=2)
    check("cooldown blocks a second switch", not d.changed)

    # Background automation sets allow_speed=False: a healthy link may recover
    # far_range -> balanced, but must never auto-widen to max_speed.
    c4 = RobustnessController(cooldown_s=0, consecutive=2, window=2,
                              allow_speed=False)
    for i in range(4):
        c4.observe(-55, efficiency=0.40, now=100 + i)
    check("allow_speed=False holds balanced", c4.mode == "balanced")
    c4._mode = "far_range"
    c4.observe(-55, efficiency=0.40, now=200)
    d2 = c4.observe(-55, efficiency=0.40, now=201)
    check("allow_speed=False recovers far_range -> balanced",
          d2.changed and d2.mode == "balanced")

    c5 = RobustnessController(cooldown_s=0, consecutive=1, window=2)
    c5.set_mode("max_speed")
    check("set_mode syncs a manual ladder choice", c5.mode == "max_speed")
    c5.set_mode("power_save")
    check("set_mode ignores non-ladder profiles", c5.mode == "max_speed")

    c6 = RobustnessController(cooldown_s=0, consecutive=2, window=3)
    c6.observe(-80, efficiency=0.02, now=1)
    c6.observe(-55, now=2)
    d3 = c6.observe(-55, now=3)
    check("missing efficiency is unknown, not stale",
          d3.ewma_efficiency is None)


def test_power_plan_query():
    print("\n[wireless power plan]")
    wp = adapter_manager._query_wireless_power()
    check("powercfg query returns AC/DC",
          bool(wp and "ac" in wp and "dc" in wp), str(wp))
    check("power indices are 0..3",
          bool(wp and 0 <= wp["ac"] <= 3 and 0 <= wp["dc"] <= 3), str(wp))


def test_preview_restore_is_simulated():
    print("\n[preview restore safety]")
    adapter_manager.set_dry_run_mode(False)
    adapter_manager.set_preview_mode(True)
    adapter_manager._SESSION_SNAPSHOTS.clear()
    adapter_manager._SESSION_SNAPSHOTS.append({
        "subkey": "__nonexistent__",
        "property": "FakeSetting",
        "original_value": "1",
        "existed": True,
        "new_value": "2",
        "timestamp": 0.0,
    })
    try:
        _orig = adapter_manager._current_subkeys_or_none
        adapter_manager._current_subkeys_or_none = lambda: None
        try:
            ok, log = adapter_manager.rollback_settings("Z:\\definitely-missing.json")
        finally:
            adapter_manager._current_subkeys_or_none = _orig
    finally:
        adapter_manager.set_preview_mode(False)
        adapter_manager._SESSION_SNAPSHOTS.clear()
    check("preview rollback uses simulation", any("[PREVIEW]" in x for x in log))
    check("preview rollback did not attempt a write",
          not any("Failed" in x for x in log))
    check("preview rollback reports success", ok is True)


def test_ledger_and_result():
    print("\n[change ledger / result]")
    adapter_manager.set_dry_run_mode(True)
    adapter_manager.begin_run()
    adapter_manager.record_note("changed", intent="Preferred Band", new="2.4GHz")
    adapter_manager.record_note("noop", intent="Roaming", new="1")
    ledger = adapter_manager.get_run_changes()
    check("ledger records changed", len(ledger["changed"]) == 1)
    check("ledger records noop", len(ledger["noop"]) == 1)
    adapter_manager.begin_run()
    check("begin_run clears the ledger",
          all(len(v) == 0 for v in adapter_manager.get_run_changes().values()))

    r = OptimizationResult(
        profile_name="Far / Robust", networks_found=1, best_network=None,
        network_scores=[], settings_applied=["A"], settings_failed=[],
        estimated_improvement="Not measured", adapter_info=None,
        channel_congestion={}, recommendations=[])
    check("result has unchanged bucket", hasattr(r, "settings_unchanged"))
    check("result has skipped bucket", hasattr(r, "settings_skipped"))
    check("result measured defaults to None", r.measured is None)

    b = BenchmarkResult(throughput_mbps=42.0, latency_p95_ms=30.0,
                        jitter_ms=4.0, loss_pct=0.0, efficiency=0.5)
    check("benchmark summary includes throughput", "42.0 Mbps" in b.summary())


def test_native_enums():
    print("\n[native enums]")
    try:
        from core import wlan_native
    except Exception as e:
        check("wlan_native import", False, str(e))
        return
    check("auth 7 == WPA2-Personal", wlan_native._auth_enum(7) == "WPA2-Personal")
    check("auth 9 == WPA3-Personal", wlan_native._auth_enum(9) == "WPA3-Personal")
    check("cipher 4 == CCMP", wlan_native._cipher_enum(4) == "CCMP")
    check("cipher 9 == GCMP-256", wlan_native._cipher_enum(9) == "GCMP-256")


def main():
    print("WiFi Optimizer core checks")
    test_conversions()
    test_overlap()
    test_congestion_keys()
    test_scoring()
    test_profiles()
    test_controller()
    test_power_plan_query()
    test_preview_restore_is_simulated()
    test_ledger_and_result()
    test_native_enums()
    print(f"\n{PASSED} passed, {FAILED} failed")
    return 0 if FAILED == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
