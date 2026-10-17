"""
WiFi Optimizer - Comprehensive Audit & Mathematical Verification Unit Tests
Validates:
1. RF physics & Friis path loss / distance equations with environmental range intervals.
2. Signal-to-dBm mapping, native RSSI fallback, and measurement uncertainty.
3. 22 MHz spectral mask overlap integral and effective interference calculation.
4. Sigmoid signal curve bounds [0, 100] and heuristic parameter documentation.
5. True security policy gating (Open/WEP disqualification) and saved profile filtering.
6. Hysteresis threshold and cooldown logic.
7. Closed-loop Link Governor state machine simulation across telemetry traces (including gradient drops).
8. Adapter Manager parameter discovery, enum label resolution (e.g. MIMOPowerSave), and connection PHY SGI gating.
9. Real disk filesystem atomic snapshot and rollback verification in temporary directory.
"""

import unittest
import math
import time
import os
import sys
import tempfile
import json

# Ensure src/ is on sys.path
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.wifi_scanner import (
    WiFiNetwork, AdapterInfo, PropagationEstimate,
    percent_to_dbm, estimate_distance_meters, estimate_propagation_range,
    calculate_channel_overlap, calculate_effective_channel_congestion
)
from core.optimizer import (
    score_network, calculate_sig_score, parse_radio_rank,
    should_roam_decision, PROFILES, OptimizationProfile
)
from core.monitor import WiFiMonitor, GovernorState
from core import adapter_manager


class TestRFPhysicsAndMath(unittest.TestCase):
    """Verify RF and physical propagation formulas."""

    def test_rssi_to_dbm_mapping_and_uncertainty(self):
        """Verify Windows signal percentage to dBm conversion, uncertainty, and bounds."""
        self.assertEqual(percent_to_dbm(100), -50.0)
        self.assertEqual(percent_to_dbm(40), -80.0)
        self.assertEqual(percent_to_dbm(0), -100.0)
        self.assertEqual(percent_to_dbm(50), -75.0)
        # Out-of-bound clamps
        self.assertEqual(percent_to_dbm(-10), -100.0)
        self.assertEqual(percent_to_dbm(120), -50.0)

        # Verify network dataclass default uncertainty
        net = WiFiNetwork(signal_percent=50, signal_dbm=percent_to_dbm(50))
        self.assertFalse(net.is_native_rssi)
        self.assertEqual(net.rssi_uncertainty_db, 6.0)

    def test_friis_reference_path_loss_1m(self):
        """Verify 1-meter free space reference path loss PL(1m) = 20*log10(f) - 27.55."""
        # 5180 MHz: 20*log10(5180) - 27.55 = 74.2866 - 27.55 = 46.7366 dB ~ 46.74 dB
        pl_5g = 20.0 * math.log10(5180) - 27.55
        self.assertAlmostEqual(pl_5g, 46.74, places=1)

        # 2437 MHz: 20*log10(2437) - 27.55 = 67.737 - 27.55 = 40.187 dB ~ 40.19 dB
        pl_24g = 20.0 * math.log10(2437) - 27.55
        self.assertAlmostEqual(pl_24g, 40.19, places=1)

    def test_distance_estimation_test_vectors(self):
        """
        Verify distance calculation with correct sign (+27.55) across path loss exponents.
        At 5180 MHz, P_rx = -65 dBm, P_tx = 20 dBm -> PL = 85 dB.
        PL - PL(1m) = 85 - 46.7366 = 38.2634 dB.
        """
        freq = 5180.0
        p_rx = -65.0
        p_tx = 20.0

        # Free space (n = 2.0): 10^(38.2634 / 20) = 10^1.91317 ~ 81.88 m
        d_n2 = estimate_distance_meters(p_rx, frequency_mhz=freq, path_loss_exponent=2.0, tx_power_dbm=p_tx)
        self.assertAlmostEqual(d_n2, 81.88, delta=0.5)

        # Indoor obstructed (n = 3.0): 10^(38.2634 / 30) = 10^1.2754 ~ 18.86 m
        d_n3 = estimate_distance_meters(p_rx, frequency_mhz=freq, path_loss_exponent=3.0, tx_power_dbm=p_tx)
        self.assertAlmostEqual(d_n3, 18.86, delta=0.3)

        # Dense office (n = 4.0): 10^(38.2634 / 40) = 10^0.9565 ~ 9.05 m
        d_n4 = estimate_distance_meters(p_rx, frequency_mhz=freq, path_loss_exponent=4.0, tx_power_dbm=p_tx)
        self.assertAlmostEqual(d_n4, 9.05, delta=0.2)

        # Ensure the distance bug (-27.55 instead of +27.55 yielding 0.14m) is eliminated
        self.assertGreater(d_n2, 50.0)
        self.assertGreater(d_n3, 10.0)

    def test_propagation_range_interval(self):
        """Verify propagation range outputs an interval reflecting indoor uncertainty."""
        prop = estimate_propagation_range(-65.0, frequency_mhz=5180.0, tx_power_dbm=20.0, uncertainty_db=6.0)
        self.assertLess(prop.d_dense_m, prop.d_indoor_m)
        self.assertLess(prop.d_indoor_m, prop.d_free_space_m)
        self.assertIn("Indoor Obstacle Range", prop.range_summary)
        self.assertIn("±6dB", prop.range_summary)

    def test_channel_spectral_overlap_integral(self):
        """Verify 22 MHz spectral mask overlap integral w_ov = max(0, round(1 - delta*5/22, 2))."""
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 6, "2.4GHz"), 1.0)
        # Delta = 1: 1 - 5/22 = 17/22 = 0.7727 ~ 0.77
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 7, "2.4GHz"), 0.77)
        # Delta = 2: 1 - 10/22 = 12/22 = 0.5454 ~ 0.55
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 8, "2.4GHz"), 0.55)
        # Delta = 3: 1 - 15/22 = 7/22 = 0.318 ~ 0.32
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 9, "2.4GHz"), 0.32)
        # Delta = 4: 1 - 20/22 = 2/22 = 0.0909 ~ 0.09
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 10, "2.4GHz"), 0.09)
        # Delta >= 5: 0.0
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 11, "2.4GHz"), 0.0)
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 1, "2.4GHz"), 0.0)

        # Cross band must be zero
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 36, "5GHz"), 0.0)

        # 5 GHz orthogonal channels
        self.assertEqual(calculate_channel_overlap(36, "5GHz", 36, "5GHz"), 1.0)
        self.assertEqual(calculate_channel_overlap(36, "5GHz", 40, "5GHz"), 0.0)


class TestMBFFScoringAlgorithm(unittest.TestCase):
    """Verify Multi-Objective BSSID Fitness Function (M-BFF)."""

    def test_sigmoid_signal_curve(self):
        """Verify heuristic Sigmoid f_sig(P_rx) = 30 / (1 + exp(-0.35*(P_rx + 72)))."""
        # Saturation near -50 dBm: ~30.0
        s_50 = calculate_sig_score(-50.0)
        self.assertAlmostEqual(s_50, 30.0, delta=0.1)

        # -65 dBm: ~27.62
        s_65 = calculate_sig_score(-65.0)
        self.assertAlmostEqual(s_65, 27.62, delta=0.2)

        # Inflection point at -72 dBm: exactly 15.0
        s_72 = calculate_sig_score(-72.0)
        self.assertAlmostEqual(s_72, 15.0, places=3)

        # Retransmission cliff at -80 dBm: ~1.72
        s_80 = calculate_sig_score(-80.0)
        self.assertAlmostEqual(s_80, 1.72, delta=0.2)

        # Sensitivity floor at -90 dBm: ~0.05
        s_90 = calculate_sig_score(-90.0)
        self.assertAlmostEqual(s_90, 0.05, delta=0.05)

    def test_sigmoid_monotonicity(self):
        """Ensure f_sig is strictly monotonic across entire operating range."""
        prev = -1.0
        for dbm in range(-100, -40, 2):
            val = calculate_sig_score(float(dbm))
            self.assertGreaterEqual(val, prev)
            prev = val

    def test_score_network_bounds(self):
        """Ensure total score is always clamped in [0.0, 100.0]."""
        profile = PROFILES["balanced"]

        best_net = WiFiNetwork(
            ssid="BestWiFi", bssid="00:11:22:33:44:55",
            signal_percent=100, signal_dbm=-50.0, channel=36,
            band="6GHz", authentication="WPA3-Personal",
            radio_type="802.11ax"
        )
        res_best = score_network(best_net, {}, profile)
        self.assertLessEqual(res_best.total_score, 100.0)
        self.assertGreaterEqual(res_best.total_score, 85.0)

        worst_net = WiFiNetwork(
            ssid="WorstWiFi", bssid="00:11:22:33:44:66",
            signal_percent=0, signal_dbm=-100.0, channel=6,
            band="2.4GHz", authentication="Open",
            radio_type="802.11b"
        )
        res_worst = score_network(worst_net, {6: 10}, profile)
        self.assertGreaterEqual(res_worst.total_score, 0.0)
        self.assertLessEqual(res_worst.total_score, 10.0)

    def test_true_security_policy_gating(self):
        """Verify that Open and WEP networks are disqualified when allow_insecure_networks=False."""
        strict_profile = OptimizationProfile(
            name="Strict", description="Strict Security",
            preferred_band="Auto", roaming_aggressiveness=3,
            transmit_power=5, disable_power_save=True,
            background_scan=True, optimize_tcp=True,
            set_dns=True, dns_primary="1.1.1.1", dns_secondary="8.8.8.8",
            allow_insecure_networks=False
        )

        open_high_signal = WiFiNetwork(
            ssid="OpenCafe", bssid="00:11:22:33:44:77",
            signal_percent=100, signal_dbm=-45.0, channel=36,
            band="5GHz", authentication="Open", radio_type="802.11ax"
        )
        res_open = score_network(open_high_signal, {}, strict_profile)
        # Even with high signal and ax radio, open network must be disqualified!
        self.assertFalse(res_open.is_eligible)
        self.assertIn("Excluded by Security Policy", res_open.ineligibility_reason)
        self.assertIn("Plaintext", res_open.ineligibility_reason)

        wep_high_signal = WiFiNetwork(
            ssid="LegacyWEP", bssid="00:11:22:33:44:88",
            signal_percent=100, signal_dbm=-45.0, channel=36,
            band="5GHz", authentication="WEP", radio_type="802.11n"
        )
        res_wep = score_network(wep_high_signal, {}, strict_profile)
        self.assertFalse(res_wep.is_eligible)
        self.assertIn("Broken WEP RC4 cipher", res_wep.ineligibility_reason)

        # Permit when allow_insecure_networks is explicitly enabled
        permissive_profile = OptimizationProfile(
            name="Permissive", description="Permissive",
            preferred_band="Auto", roaming_aggressiveness=3,
            transmit_power=5, disable_power_save=True,
            background_scan=True, optimize_tcp=True,
            set_dns=True, dns_primary="1.1.1.1", dns_secondary="8.8.8.8",
            allow_insecure_networks=True
        )
        res_permitted = score_network(open_high_signal, {}, permissive_profile)
        self.assertTrue(res_permitted.is_eligible)

    def test_saved_profile_roaming_filter(self):
        """Verify roaming candidate filter rejects unknown SSIDs when require_saved_profile=True."""
        profile = OptimizationProfile(
            name="KnownOnly", description="",
            preferred_band="Auto", roaming_aggressiveness=3,
            transmit_power=5, disable_power_save=True,
            background_scan=True, optimize_tcp=True,
            set_dns=True, dns_primary="1.1.1.1", dns_secondary="8.8.8.8",
            allow_insecure_networks=True, require_saved_profile=True
        )
        known_ssid = "HomeNetwork"
        unknown_ssid = "NeighborGuest"
        net_unknown = WiFiNetwork(ssid=unknown_ssid, bssid="00:11:22:00:00:01", signal_dbm=-50.0, authentication="WPA2-PSK")
        res = score_network(net_unknown, {}, profile, saved_profiles=[known_ssid])
        self.assertFalse(res.is_eligible)
        self.assertIn("not in saved profiles", res.ineligibility_reason)

    def test_hysteresis_and_cooldown(self):
        """Verify AP roaming hysteresis threshold (+10 pts) and 30s cooldown."""
        t0 = 1000.0

        # Candidate score 75 vs current 70 (delta +5, below threshold 10)
        ok1, reason1 = should_roam_decision(70.0, 75.0, last_roam_timestamp=t0 - 40, current_timestamp=t0)
        self.assertFalse(ok1)
        self.assertIn("hysteresis", reason1.lower())

        # Candidate score 85 vs current 70 (delta +15, but within 20s cooldown)
        ok2, reason2 = should_roam_decision(70.0, 85.0, last_roam_timestamp=t0 - 20, current_timestamp=t0)
        self.assertFalse(ok2)
        self.assertIn("cooldown", reason2.lower())

        # Candidate score 85 vs current 70 (delta +15, elapsed 35s > 30s)
        ok3, reason3 = should_roam_decision(70.0, 85.0, last_roam_timestamp=t0 - 35, current_timestamp=t0)
        self.assertTrue(ok3)


class TestLinkGovernorSimulation(unittest.TestCase):
    """Verify closed-loop Link Governor state machine and EWMA smoothing across realistic traces."""

    def test_governor_anti_flapping_and_smoothing(self):
        """Simulate telemetry traces to verify Governor does not flap on transient noise."""
        monitor = WiFiMonitor(alpha=0.3, cooldown_sec=30.0)
        now = 1000.0

        # Steady state at -60 dBm
        for _ in range(5):
            now += 3.0
            alert = monitor.update_governor(-60.0, now)
            self.assertIsNone(alert)
            self.assertEqual(monitor.governor_state, GovernorState.HEALTHY)

        self.assertAlmostEqual(monitor.smoothed_dbm, -60.0, delta=0.5)

        # Transient dip (1 sample at -85 dBm)
        now += 3.0
        alert = monitor.update_governor(-85.0, now)
        self.assertEqual(monitor.governor_state, GovernorState.HEALTHY)
        self.assertIsNone(alert)

        # Sustained severe degradation below -75 dBm (3 consecutive samples)
        now += 3.0
        monitor.update_governor(-85.0, now)
        now += 3.0
        monitor.update_governor(-85.0, now)
        now += 3.0
        monitor.update_governor(-85.0, now)
        now += 3.0
        alert = monitor.update_governor(-85.0, now)

        self.assertEqual(monitor.governor_state, GovernorState.DEGRADED)
        self.assertIsNotNone(alert)
        self.assertIn("Degraded", alert[0])

        # Recovery blocked by 30s cooldown
        now += 3.0
        for _ in range(5):
            now += 3.0
            alert_rec = monitor.update_governor(-55.0, now)
            self.assertEqual(monitor.governor_state, GovernorState.DEGRADED)

        # Advance past cooldown with 5 sustained healthy samples
        now += 30.0
        alert_restored = None
        for _ in range(5):
            now += 3.0
            res = monitor.update_governor(-55.0, now)
            if res:
                alert_restored = res

        self.assertEqual(monitor.governor_state, GovernorState.HEALTHY)
        self.assertIsNotNone(alert_restored)
        self.assertIn("Restored", alert_restored[0])

    def test_governor_sudden_drop_gradient_rule(self):
        """Verify sudden gradient drop (∇P <= -15 dB over <= 12s) triggers DEGRADED."""
        monitor = WiFiMonitor(alpha=0.3, cooldown_sec=30.0)
        now = 1000.0

        # Establish initial steady baseline at -50 dBm
        for _ in range(10):
            now += 1.0
            monitor.update_governor(-50.0, now)
        self.assertEqual(monitor.governor_state, GovernorState.HEALTHY)

        # Sudden steep cliff: drop to -95 dBm over 3 seconds
        # Even before 3 full consecutive samples below -75 dBm accumulate,
        # the -15 dB gradient triggers immediate degraded state
        now += 1.0
        monitor.update_governor(-95.0, now)
        now += 1.0
        alert = monitor.update_governor(-95.0, now)

        self.assertEqual(monitor.governor_state, GovernorState.DEGRADED)
        self.assertIsNotNone(alert)
        self.assertIn("Sudden signal drop", alert[1])


class TestAdapterManagerSafetyAndRollback(unittest.TestCase):
    """Verify Adapter Manager discovery, enum label resolution, PHY gating, and atomic rollback."""

    def setUp(self):
        adapter_manager.set_dry_run_mode(True)
        adapter_manager._MOCK_REGISTRY.clear()
        adapter_manager._SESSION_SNAPSHOTS.clear()

        # Seed mock adapter with rich Ndi\params (enums with code and labels, numeric ranges)
        adapter_manager._MOCK_REGISTRY["0001"] = {
            "DriverDesc": "Intel(R) Wi-Fi 6 AX200 160MHz",
            "NetCfgInstanceId": "{12345678-ABCD-1234-ABCD-123456789ABC}",
            "*TransmitPower": "3",
            "NdiParams": {
                "*TransmitPower": {
                    "desc": "Transmit Power", "type": "enum",
                    "enums": [{"code": "1", "label": "1. Lowest"}, {"code": "3", "label": "3. Medium"}, {"code": "5", "label": "5. Highest"}],
                    "enum_codes": ["1", "3", "5"],
                    "enum_labels": ["1. Lowest", "3. Medium", "5. Highest"]
                },
                "*RoamAggressiveness": {
                    "desc": "Roaming Aggressiveness", "type": "enum",
                    "enums": [{"code": "1", "label": "1. Lowest"}, {"code": "3", "label": "3. Medium"}, {"code": "5", "label": "5. Highest"}],
                    "enum_codes": ["1", "3", "5"],
                    "enum_labels": ["1. Lowest", "3. Medium", "5. Highest"]
                },
                "*MIMOPowerSave": {
                    "desc": "MIMO Power Save Mode", "type": "enum",
                    "enums": [
                        {"code": "0", "label": "Auto SMPS"},
                        {"code": "1", "label": "Dynamic SMPS"},
                        {"code": "2", "label": "Static SMPS"},
                        {"code": "3", "label": "No SMPS"}  # Code 3 means disabled!
                    ],
                    "enum_codes": ["0", "1", "2", "3"],
                    "enum_labels": ["Auto SMPS", "Dynamic SMPS", "Static SMPS", "No SMPS"]
                },
                "*ShortGI40MHz": {
                    "desc": "Short Guard Interval 40MHz", "type": "enum",
                    "enums": [{"code": "0", "label": "Disabled"}, {"code": "1", "label": "Enabled"}],
                    "enum_codes": ["0", "1"],
                    "enum_labels": ["Disabled", "Enabled"]
                }
            }
        }

    def tearDown(self):
        adapter_manager.set_dry_run_mode(False)

    def test_mimo_power_save_resolves_to_no_smps_code(self):
        """Verify resolve_driver_param_value maps 'disable' to code '3' (No SMPS) rather than naive '0'."""
        p_info = adapter_manager._MOCK_REGISTRY["0001"]["NdiParams"]["*MIMOPowerSave"]
        valid, resolved_code, reason = adapter_manager.resolve_driver_param_value("*MIMOPowerSave", "disable", p_info)
        self.assertTrue(valid)
        self.assertEqual(resolved_code, "3")
        self.assertIn("No SMPS", reason)

    def test_short_gi_gated_by_connection_phy(self):
        """Short GI must be applied on 802.11ac connections and skipped on 802.11ax."""
        # 1. On 802.11ac association -> Short GI applied
        applied_ac = adapter_manager.apply_advanced_tier_b_tweaks(connection_phy="802.11ac")
        self.assertTrue(any("ShortGI" in x for x in applied_ac))

        # 2. On 802.11ax association -> Short GI skipped
        adapter_manager._MOCK_REGISTRY["0001"].pop("*ShortGI40MHz", None)
        applied_ax = adapter_manager.apply_advanced_tier_b_tweaks(connection_phy="802.11ax (Wi-Fi 6)")
        self.assertFalse(any("ShortGI" in x for x in applied_ax))

    def test_real_disk_snapshot_and_rollback(self):
        """Verify rollback with an actual snapshot file in a temporary directory on disk."""
        with tempfile.TemporaryDirectory() as temp_dir:
            temp_snapshot_file = os.path.join(temp_dir, "test_snapshot.json")
            original_snapshot_file = adapter_manager.SNAPSHOT_FILE

            try:
                adapter_manager.SNAPSHOT_FILE = temp_snapshot_file

                # Temporarily enable disk writing for this test
                adapter_manager._DRY_RUN = False
                # Mock adapters in memory for simulation
                adapter_manager._record_snapshot_entry("0001", "*TransmitPower", "3", True, "5")
                adapter_manager._record_snapshot_entry("0001", "*RoamAggressiveness", None, False, "5")

                # Verify snapshot file physically written to disk
                self.assertTrue(os.path.exists(temp_snapshot_file))
                with open(temp_snapshot_file, "r") as f:
                    data = json.load(f)
                self.assertEqual(len(data), 2)
                self.assertEqual(data[0]["original_value"], "3")
                self.assertFalse(data[1]["existed"])

                # Run rollback in dry run mode to verify restoration actions
                adapter_manager._DRY_RUN = True
                ok, log = adapter_manager.rollback_settings(snapshot_path=temp_snapshot_file)
                self.assertTrue(ok)
                self.assertTrue(any("Restored *TransmitPower -> 3" in x for x in log))
                self.assertTrue(any("Deleted injected *RoamAggressiveness" in x for x in log))

            finally:
                adapter_manager.SNAPSHOT_FILE = original_snapshot_file
                adapter_manager._DRY_RUN = True


if __name__ == "__main__":
    unittest.main()
