"""
WiFi Optimizer - Adapter Property Discovery (diagnostic)
Dumps the ACTUAL NDIS advanced properties your WiFi drivers expose,
so we can map optimizer intents to the correct registry keywords.

Read-only. Does not need admin for the registry portion, but
Get-NetAdapterAdvancedProperty may show more as admin.

Usage:
    python scripts/discover_adapter_properties.py
"""

import os
import sys
import subprocess
import winreg

NETWORK_ADAPTERS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}"

WIRELESS_HINTS = ["wireless", "wifi", "wi-fi", "802.11", "wlan", "intel", "realtek"]
VIRTUAL_HINTS = ["virtual", "direct", "loopback", "tap", "vpn", "bluetooth", "wan"]


def is_wireless(desc: str) -> bool:
    d = desc.lower()
    return any(k in d for k in WIRELESS_HINTS) and not any(k in d for k in VIRTUAL_HINTS)


def enum_adapters():
    adapters = []
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, NETWORK_ADAPTERS_KEY, 0, winreg.KEY_READ) as base:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(base, i)
                i += 1
            except OSError:
                break
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(base, sub, 0, winreg.KEY_READ) as k:
                    try:
                        desc = winreg.QueryValueEx(k, "DriverDesc")[0]
                    except FileNotFoundError:
                        continue
                    if not is_wireless(desc):
                        continue
                    try:
                        guid = winreg.QueryValueEx(k, "NetCfgInstanceId")[0]
                    except FileNotFoundError:
                        guid = ""
                    adapters.append((sub, desc, guid))
            except (PermissionError, FileNotFoundError):
                continue
    return adapters


def dump_ndi_params(subkey):
    """Return dict of param_name -> {desc, type, enums}."""
    result = {}
    path = f"{NETWORK_ADAPTERS_KEY}\\{subkey}\\Ndi\\params"
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path, 0, winreg.KEY_READ) as params:
            i = 0
            while True:
                try:
                    pname = winreg.EnumKey(params, i)
                    i += 1
                except OSError:
                    break
                info = {"desc": pname, "type": "?", "enums": {}}
                try:
                    with winreg.OpenKey(params, pname, 0, winreg.KEY_READ) as p:
                        try:
                            info["desc"] = winreg.QueryValueEx(p, "ParamDesc")[0]
                        except FileNotFoundError:
                            pass
                        try:
                            info["type"] = winreg.QueryValueEx(p, "type")[0]
                        except FileNotFoundError:
                            pass
                        for attr in ("min", "max", "default"):
                            try:
                                info[attr] = winreg.QueryValueEx(p, attr)[0]
                            except FileNotFoundError:
                                pass
                        try:
                            with winreg.OpenKey(p, "enum", 0, winreg.KEY_READ) as ek:
                                j = 0
                                while True:
                                    try:
                                        name, data, _vtype = winreg.EnumValue(ek, j)
                                        info["enums"][str(data)] = str(name)
                                        j += 1
                                    except OSError:
                                        break
                        except FileNotFoundError:
                            pass
                except (PermissionError, FileNotFoundError):
                    pass
                result[pname] = info
    except (FileNotFoundError, PermissionError) as e:
        result["__error__"] = {"desc": f"Ndi\\params not accessible: {e}", "type": "?", "enums": {}}
    return result


def get_netadapter_props():
    """PowerShell Get-NetAdapterAdvancedProperty for all wireless adapters."""
    try:
        ps = (
            "Get-NetAdapterAdvancedProperty -ErrorAction SilentlyContinue | "
            "Where-Object { $_.DisplayName -or $_.RegistryKeyword } | "
            "Select-Object ifAlias,DisplayName,RegistryKeyword,RegistryValue,"
            "@{n='Valid';e={$_.ValidRegistryValues -join '|'}} | "
            "Format-Table -AutoSize | Out-String -Width 300"
        )
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=40,
                           creationflags=subprocess.CREATE_NO_WINDOW
                           if hasattr(subprocess, 'CREATE_NO_WINDOW') else 0)
        return r.stdout.strip()
    except Exception as e:
        return f"(PowerShell failed: {e})"


def main():
    print("=" * 78)
    print("WiFi ADAPTER ADVANCED PROPERTY DISCOVERY")
    print("=" * 78)

    adapters = enum_adapters()
    if not adapters:
        print("No wireless adapters found via registry.")
    for sub, desc, guid in adapters:
        print(f"\n### Adapter subkey {sub}: {desc}")
        print(f"    GUID: {guid}")
        params = dump_ndi_params(sub)
        if not params:
            print("    (no Ndi\\params entries)")
            continue
        for pname, info in params.items():
            line = f"    - {pname:32s} type={info.get('type','?'):8s} desc={info.get('desc','')}"
            if "min" in info or "max" in info:
                line += f"  range=[{info.get('min','?')},{info.get('max','?')}]"
            print(line)
            enums = info.get("enums") or {}
            if enums:
                for code, label in list(enums.items())[:12]:
                    print(f"        code {code:>4} = {label}")

    print("\n" + "=" * 78)
    print("Get-NetAdapterAdvancedProperty (PowerShell) — friendly view")
    print("=" * 78)
    print(get_netadapter_props())


if __name__ == "__main__":
    main()
