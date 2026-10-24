"""Smoke test: build the full GUI, pump events, and destroy. Catches layout errors."""
import os, sys, traceback
BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "src"))

from ui.app import WiFiOptimizerApp

app = WiFiOptimizerApp(assets_dir=os.path.join(BASE, "src", "assets"))
app.withdraw()

# Navigate through every page to force construction/refresh
for key in ["dashboard", "networks", "settings", "hardware", "help", "about"]:
    app._nav(key)
    app.update()

# Exercise the hardware refresh (registry read + widget build)
app._refresh_hardware()
app.update()

# Verify sidebar profile radios all exist and set correctly
print("Profile radios:", sorted(app._profile_radios.keys()))
app._profile_var.set("power_save")
print("Selected profile key:", app._profile_var.get())
assert app._profile_var.get() == "power_save"

# Verify preview toggle + preview dialog construction
print("Preview toggle default:", app._preview_var.get())
app._preview_var.set(True)
app._on_preview_toggle()
app.update()
label = app._optimize_btn.cget("text")
print("Button label in preview mode contains PREVIEW:", "PREVIEW" in label)
assert "PREVIEW" in label

# Build a fake preview result and render the preview dialog
from core.optimizer import OptimizationResult
fake = OptimizationResult(
    profile_name="Far / Robust", networks_found=3,
    best_network=None, network_scores=[], settings_applied=[], settings_failed=[],
    estimated_improvement="Not measured", adapter_info=None, channel_congestion={},
    recommendations=[],
    preview_changes=[
        {"subkey": "0014", "adapter": "Intel(R) Wi-Fi 6 AX201 160MHz",
         "property": "RoamAggressiveness", "intent": "Roaming Aggressiveness",
         "current": "2", "proposed": "4", "changed": True},
        {"subkey": "0002", "adapter": "Realtek RTL8822BE", "property": "PreferBand",
         "intent": "Preferred Band", "current": "0", "proposed": "2", "changed": True},
    ])
app._show_preview(fake)
app.update()
print("Preview dialog built OK")

app.destroy()
print("SMOKE TEST PASSED")
