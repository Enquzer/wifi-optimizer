import os
import win32com.client

wscript = win32com.client.Dispatch("WScript.Shell")
desktop = wscript.SpecialFolders("Desktop")

# Project root folder (parent of scripts/)
workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
icon_path = os.path.join(workdir, "src", "assets", "app_icon.ico")
exe_target = os.path.join(workdir, "app_bin", "WiFi Optimizer", "WiFi Optimizer.exe")

if os.path.exists(exe_target):
    target = exe_target
    args = ""
    exe_workdir = os.path.dirname(exe_target)
else:
    target = "pythonw.exe"
    args = f'"{os.path.join(workdir, "src", "main.py")}"'
    exe_workdir = os.path.join(workdir, "src")

# 1. Inside project root directory
link1 = wscript.CreateShortcut(os.path.join(workdir, "WiFi Optimizer.lnk"))
link1.TargetPath = target
link1.Arguments = args
link1.WorkingDirectory = exe_workdir
link1.IconLocation = f"{icon_path},0"
link1.Description = "WiFi Optimizer"
link1.Save()

# 2. On User Desktop
link2 = wscript.CreateShortcut(os.path.join(desktop, "WiFi Optimizer.lnk"))
link2.TargetPath = target
link2.Arguments = args
link2.WorkingDirectory = exe_workdir
link2.IconLocation = f"{icon_path},0"
link2.Description = "WiFi Optimizer"
link2.Save()

print("Shortcuts updated successfully pointing to app_bin/WiFi Optimizer/WiFi Optimizer.exe!")
