"""
WiFi Optimizer - Setup / Install Script
Run this once to install all dependencies.
"""
import subprocess, sys

packages = [
    "customtkinter>=5.2.0",
    "pillow>=10.0.0",
    "pystray>=0.19.0",
    "psutil>=5.9.0",
    "winotify>=1.1.0",
    "pywin32>=306",
]

print("Installing WiFi Optimizer dependencies...")
for pkg in packages:
    print(f"  Installing {pkg}...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", pkg, "--quiet"])

print("\n✅ All dependencies installed! Run 'python main.py' or double-click launch.bat")
