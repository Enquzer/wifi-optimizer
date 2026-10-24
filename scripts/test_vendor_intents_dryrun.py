"""Dry-run test: verify vendor intents resolve and write for the real adapters."""
import os, sys, logging
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
logging.basicConfig(level=logging.INFO, format="%(message)s")

from core.adapter_manager import set_dry_run_mode, apply_intent, _MOCK_REGISTRY

set_dry_run_mode(True)

# Simulate the "Selfish Turbo" / Max Speed profile intents
intents = [
    ("preferred_band", "5GHz"),
    ("transmit_power", "max"),
    ("roaming_aggressiveness", "max"),
    ("sleep_on_disconnect", "disable"),
    ("mimo_power_save", "disable"),
    ("throughput_booster", "on"),
    ("packet_coalescing", "off"),
]

print("=" * 70)
for intent, sem in intents:
    ok = apply_intent(intent, sem)
    print(f"  {'APPLIED' if ok else 'skipped':8s}  {intent} = {sem}")

print("=" * 70)
print("MOCK REGISTRY (what would be written):")
for subkey, vals in _MOCK_REGISTRY.items():
    print(f"  [{subkey}] {vals}")
