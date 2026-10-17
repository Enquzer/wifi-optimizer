"""
WiFi Optimizer - Comprehensive Audit & Mathematical Verification Unit Tests
Tests:
1. RF physics & Friis path loss / distance equations.
2. Signal-to-dBm mapping and boundary constraints.
3. Channel overlap matrix and effective interference calculation.
4. Sigmoid signal curve calibration and M-BFF score bounds [0, 100].
5. Security scoring and gating.
6. Hysteresis threshold and cooldown logic.
7. EWMA-smoothed Link Governor state machine simulation.
8. Adapter Manager parameter discovery, dry-run safety, and atomic rollback snapshot.
"""

import unittest
import math
import time
import os
import sys

# Ensure src/ is on sys.path
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from core.wifi_scanner import (
    WiFiNetwork, AdapterInfo,
    percent_to_dbm, estimate_distance_meters,
    calculate_channel_overlap, calculate_effective_channel_congestion
)
from core.optimizer import (
    score_network, calculate_sig_score, parse_radio_rank,
    should_roam_decision, PROFILES
)
from core.monitor import WiFiMonitor, GovernorState
from core import adapter_manager


class TestRFPhysicsAndMath(unittest.TestCase):
    """Verify RF and physical propagation formulas."""

    def test_rssi_to_dbm_mapping(self):
        """Verify Windows signal percentage to dBm conversion and bounds."""
        self.assertEqual(percent_to_dbm(100), -50.0)
        self.assertEqual(percent_to_dbm(40), -80.0)
        self.assertEqual(percent_to_dbm(0), -100.0)
        self.assertEqual(percent_to_dbm(50), -75.0)
        # Out-of-bound clamps
        self.assertEqual(percent_to_dbm(-10), -100.0)
        self.assertEqual(percent_to_dbm(120), -50.0)

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

    def test_channel_spectral_overlap_weights(self):
        """Verify spectral overlap weights w_ov for co-channel and adjacent interference."""
        # 2.4 GHz channel spacing (22MHz channel width, 5MHz channel separation)
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 6, "2.4GHz"), 1.0)
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 7, "2.4GHz"), 0.75)
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 8, "2.4GHz"), 0.50)
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 9, "2.4GHz"), 0.25)
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 10, "2.4GHz"), 0.0)
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 1, "2.4GHz"), 0.0)

        # Cross band must be zero
        self.assertEqual(calculate_channel_overlap(6, "2.4GHz", 36, "5GHz"), 0.0)

        # 5 GHz orthogonal channels
        self.assertEqual(calculate_channel_overlap(36, "5GHz", 36, "5GHz"), 1.0)
        self.assertEqual(calculate_channel_overlap(36, "5GHz", 40, "5GHz"), 0.0)

    def test_effective_channel_congestion_calculation(self):
        """Verify C_eff summation with signal weighting."""
        target = WiFiNetwork(ssid="TargetNet", bssid="AA:BB:CC:00:00:01", channel=6, band="2.4GHz", signal_dbm=-60.0)
        # Neighbor 1: Co-channel (Ch 6) at -50 dBm -> w_ov=1.0, w_sig=1.0 -> contrib = 1.0
        n1 = WiFiNetwork(ssid="Net1", bssid="AA:BB:CC:00:00:02", channel=6, band="2.4GHz", signal_dbm=-50.0)
        # Neighbor 2: Adjacent (Ch 7, delta=1) at -70 dBm -> w_ov=0.75, w_sig=(-70+90)/40=0.5 -> contrib = 0.375
        n2 = WiFiNetwork(ssid="Net2", bssid="AA:BB:CC:00:00:03", channel=7, band="2.4GHz", signal_dbm=-70.0)
        # Neighbor 3: Non-overlapping (Ch 11) at -40 dBm -> w_ov=0.0 -> contrib = 0.0
        n3 = WiFiNetwork(ssid="Net3", bssid="AA:BB:CC:00:00:04", channel=11, band="2.4GHz", signal_dbm=-40.0)

        c_eff = calculate_effective_channel_congestion(target, [target, n1, n2, n3])
        self.assertAlmostEqual(c_eff, 1.375, places=3)


class TestMBFFScoringAlgorithm(unittest.TestCase):
    """Verify Multi-Objective BSSID Fitness Function (M-BFF)."""

    def test_sigmoid_signal_curve(self):
        """Verify calibrated Sigmoid f_sig(P_rx) = 30 / (1 + exp(-0.35*(P_rx + 72)))."""
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

        # Super strong WiFi 6E network
        best_net = WiFiNetwork(
            ssid="BestWiFi", bssid="00:11:22:33:44:55",
            signal_percent=100, signal_dbm=-50.0, channel=36,
            band="6GHz", authentication="WPA3-Personal",
            radio_type="802.11ax"
        )
        res_best = score_network(best_net, {}, profile)
        self.assertLessEqual(res_best.total_score, 100.0)
        self.assertGreaterEqual(res_best.total_score, 85.0)

        # Very weak, insecure network
        worst_net = WiFiNetwork(
            ssid="WorstWiFi", bssid="00:11:22:33:44:66",
            signal_percent=0, signal_dbm=-100.0, channel=6,
            band="2.4GHz", authentication="Open",
            radio_type="802.11b"
        )
        res_worst = score_network(worst_net, {6: 10}, profile)
        self.assertGreaterEqual(res_worst.total_score, 0.0)
        self.assertLessEqual(res_worst.total_score, 10.0)

    def test_security_gating(self):
        """Verify that Open/WEP networks receive 0 security score and are flagged."""
        profile = PROFILES["balanced"]
        net_open = WiFiNetwork(
            ssid="CoffeeShop_Open", bssid="00:11:22:33:44:77",
            signal_percent=90, signal_dbm=-55.0, channel=36,
            band="5GHz", authentication="Open", radio_type="802.11ac"
        )
        res = score_network(net_open, {}, profile)
        self.assertEqual(res.security_score, 0.0)
        self.assertIn("Insecure", res.recommendation)

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
    """Verify closed-loop Link Governor state machine and EWMA smoothing."""

    def test_governor_anti_flapping_and_smoothing(self):
        """Simulate telemetry traces to verify Governor does not flap on transient noise."""
        monitor = WiFiMonitor(alpha=0.3, cooldown_sec=30.0)
        now = 1000.0

        # Initial steady state at -60 dBm
        for _ in range(5):
            now += 3.0
            alert = monitor.update_governor(-60.0, now)
            self.assertIsNone(alert)
            self.assertEqual(monitor.governor_state, GovernorState.HEALTHY)

        self.assertAlmostEqual(monitor.smoothed_dbm, -60.0, delta=0.5)

        # Single transient dip to -85 dBm (e.g. microwave or momentary obstruction)
        now += 3.0
        alert = monitor.update_governor(-85.0, now)
        # EWMA: 0.3 * (-85) + 0.7 * (-60) = -25.5 - 42.0 = -67.5 dBm
        # -67.5 dBm > -75 dBm -> does not even enter degraded counter
        self.assertEqual(monitor.governor_state, GovernorState.HEALTHY)
        self.assertIsNone(alert)

        # Sustained severe degradation below -75 dBm for multiple consecutive ticks
        now += 3.0
        monitor.update_governor(-85.0, now)  # 2nd sample: ~ -72.75 dBm
        now += 3.0
        monitor.update_governor(-85.0, now)  # 3rd sample: ~ -76.4 dBm (< -75) - count 1
        now += 3.0
        monitor.update_governor(-85.0, now)  # count 2
        now += 3.0
        alert = monitor.update_governor(-85.0, now)  # count 3 -> triggers DEGRADED state!

        self.assertEqual(monitor.governor_state, GovernorState.DEGRADED)
        self.assertIsNotNone(alert)
        self.assertIn("Degraded", alert[0])

        # Attempt immediate recovery without satisfying 30s cooldown
        now += 3.0  # elapsed 3s since state change < 30s cooldown
        for _ in range(5):
            now += 3.0
            alert_rec = monitor.update_governor(-55.0, now)
            # Cannot restore yet due to cooldown
            self.assertEqual(monitor.governor_state, GovernorState.DEGRADED)

        # Advance past cooldown (now > last_state_change + 30s) with sustained good signal
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


class TestAdapterManagerSafetyAndRollback(unittest.TestCase):
    """Verify Adapter Manager dry-run safety and atomic snapshot rollback."""

    def setUp(self):
        adapter_manager.set_dry_run_mode(True)
        adapter_manager._MOCK_REGISTRY.clear()
        adapter_manager._SESSION_SNAPSHOTS.clear()

        # Seed mock adapter with supported Ndi\params
        adapter_manager._MOCK_REGISTRY["0001"] = {
            "DriverDesc": "Intel(R) Wi-Fi 6 AX200 160MHz",
            "NetCfgInstanceId": "{12345678-ABCD-1234-ABCD-123456789ABC}",
            "*TransmitPower": "3",
            "NdiParams": {
                "*TransmitPower": {"desc": "Transmit Power", "enums": ["1", "2", "3", "4", "5"]},
                "*RoamAggressiveness": {"desc": "Roaming Aggressiveness", "enums": ["1", "2", "3", "4", "5"]},
                "*PreferredBand": {"desc": "Preferred Band", "enums": ["0", "1", "2"]},
            }
        }

    def tearDown(self):
        adapter_manager.set_dry_run_mode(False)

    def test_dry_run_does_not_mutate_system(self):
        """Ensure dry-run mode returns success without writing to real registry or netsh."""
        self.assertTrue(adapter_manager.is_dry_run_mode())
        ok = adapter_manager.set_transmit_power(5)
        self.assertTrue(ok)
        # Verified mock registry updated
        self.assertEqual(adapter_manager._MOCK_REGISTRY["0001"]["*TransmitPower"], "5")

    def test_unsupported_param_is_skipped_via_ndi_discovery(self):
        """Parameters absent from Ndi\\params must be skipped to protect the driver."""
        # *ShortGI20MHz is not in AX200 NdiParams mock -> should not be blindly written
        success = adapter_manager._set_adapter_registry_value_safe("*ShortGI20MHz", "1", verify_ndi_support=True)
        self.assertFalse(success)
        self.assertNotIn("*ShortGI20MHz", adapter_manager._MOCK_REGISTRY["0001"])

    def test_atomic_snapshot_and_rollback(self):
        """Verify pre-change values are recorded and fully restored upon rollback."""
        orig_power = adapter_manager._MOCK_REGISTRY["0001"]["*TransmitPower"]
        self.assertEqual(orig_power, "3")

        # 1. Mutate existing property (*TransmitPower)
        adapter_manager.set_transmit_power(5)
        self.assertEqual(adapter_manager._MOCK_REGISTRY["0001"]["*TransmitPower"], "5")

        # 2. Inject a previously non-existent property (*RoamAggressiveness)
        self.assertNotIn("*RoamAggressiveness", adapter_manager._MOCK_REGISTRY["0001"])
        adapter_manager.set_roaming_aggressiveness(4)
        self.assertEqual(adapter_manager._MOCK_REGISTRY["0001"]["*RoamAggressiveness"], "4")

        # 3. Perform rollback
        ok, log = adapter_manager.rollback_settings()
        self.assertTrue(ok)
        self.assertGreater(len(log), 0)

        # 4. Verify previous state restored
        # Original property restored to "3"
        self.assertEqual(adapter_manager._MOCK_REGISTRY["0001"]["*TransmitPower"], "3")
        # Injected property removed
        self.assertNotIn("*RoamAggressiveness", adapter_manager._MOCK_REGISTRY["0001"])


if __name__ == "__main__":
    unittest.main()
