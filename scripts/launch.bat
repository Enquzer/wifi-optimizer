@echo off
title WiFi Optimizer
echo Starting WiFi Optimizer...
cd /d "%~dp0\.."
if exist "app_bin\WiFi Optimizer\WiFi Optimizer.exe" (
    start "" "app_bin\WiFi Optimizer\WiFi Optimizer.exe"
) else (
    python src\main.py
)
if errorlevel 1 (
    echo.
    echo ERROR: Failed to start. Make sure dependencies are installed.
    echo Run: pip install -r scripts\requirements.txt
    pause
)
