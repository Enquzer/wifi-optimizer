import os
import sys
import shutil
import win32com.client

wscript = win32com.client.Dispatch("WScript.Shell")
desktop = wscript.SpecialFolders("Desktop")

# Project root folder (parent of scripts/)
workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
icon_path = os.path.join(workdir, "src", "assets", "app_icon.ico")
main_script = os.path.join(workdir, "src", "main.py")

# Resolve pythonw.exe (windowless Python launcher, signed by Microsoft/PSF - 0% false positives)
pythonw_path = shutil.which("pythonw.exe")
if not pythonw_path:
    candidate = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if os.path.exists(candidate):
        pythonw_path = candidate
    else:
        pythonw_path = "pythonw.exe"

# Prefer the packaged executable when it exists; --source forces the Python
# launcher (useful while developing). --exe is kept as a backwards-compatible
# no-op because the executable is now the default.
use_source = "--source" in sys.argv
exe_target = os.path.join(workdir, "app_bin", "WiFi Optimizer", "WiFi Optimizer.exe")
if not os.path.exists(exe_target):
    exe_target = os.path.join(workdir, "dist", "WiFi Optimizer", "WiFi Optimizer.exe")

if not use_source and os.path.exists(exe_target):
    target = exe_target
    args = ""
    exe_workdir = os.path.dirname(exe_target)
    launcher_type = f"Standalone Executable ({exe_target})"
else:
    target = pythonw_path
    args = f'"{main_script}"'
    exe_workdir = workdir
    launcher_type = f"Native Windowless Python ({pythonw_path})"

print(f"Creating shortcuts using: {launcher_type}")
print(f"Target: {target}")
print(f"Arguments: {args}")
print(f"Working Dir: {exe_workdir}")

# 1. Inside project root directory
root_lnk = os.path.join(workdir, "WiFi Optimizer.lnk")
link1 = wscript.CreateShortcut(root_lnk)
link1.TargetPath = target
link1.Arguments = args
link1.WorkingDirectory = exe_workdir
link1.IconLocation = f"{icon_path},0"
link1.Description = "WiFi Optimizer - Multi-Layer Wireless Network Tuning"
link1.Save()
print(f"  -> Created: {root_lnk}")

# 2. On User Desktop
desktop_lnk = os.path.join(desktop, "WiFi Optimizer.lnk")
link2 = wscript.CreateShortcut(desktop_lnk)
link2.TargetPath = target
link2.Arguments = args
link2.WorkingDirectory = exe_workdir
link2.IconLocation = f"{icon_path},0"
link2.Description = "WiFi Optimizer - Multi-Layer Wireless Network Tuning"
link2.Save()
print(f"  -> Created: {desktop_lnk}")

print("\nShortcuts created successfully!")
