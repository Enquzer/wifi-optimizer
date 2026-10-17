# 📶 WiFi Optimizer

> **⚡ Powered by Fashion Flow 2026**  
> **Developed by Enquzer Getachew**

[![Windows](https://img.shields.io/badge/Platform-Windows%2010%20%2F%2011-blue.svg)](https://microsoft.com/windows)
[![Python](https://img.shields.io/badge/Python-3.10%2B-green.svg)](https://python.org)
[![NDIS](https://img.shields.io/badge/Driver-NDIS%206.x%2B-orange.svg)]()
[![Status](https://img.shields.io/badge/Status-Production--Grade-brightgreen.svg)]()

WiFi Optimizer is a custom-engineered, multi-layer Wi-Fi optimization and diagnostic platform for Windows 10 and 11. It bridges physical RF radio telemetry, access point contention, OEM driver NDIS registry parameters, and the Windows Kernel TCP/IP transport layer.

Unlike generic "cleaners" or passive scanners, WiFi Optimizer discovers hardware properties directly from your adapter's driver database (`Ndi\params`), resolves vendor-inverted enum codes (Intel vs. Realtek), enforces non-destructive closed-loop link stability, and optimizes the Windows kernel network pipe.

---

## ⚖️ Demystifying Scores: Signal vs. Quality vs. Optimization

A common source of confusion in Wi-Fi utilities is conflating antenna signal strength with network performance:

| Metric | UI Element | What It Measures | Why It Matters |
| :--- | :--- | :--- | :--- |
| **1. Signal Strength (Antenna Power)** | Circular Gauge (`Signal (RF)`) | Pure Layer-1 Radio Frequency (RF) power received by your Wi-Fi antenna ($P_{\text{rx}}$ in dBm or %). | Shows how "loud" the router's radio is received. **A loud router on a congested channel is still slow!** |
| **2. Link Quality (M-BFF Score)** | Live Status Chip (`🎯 Link Quality`) | Holistic multi-factor fitness score (0–100) combining Signal (30) + Clean Channel (20) + 5G/6G Bandwidth (20) + Generation (20) + Security (10). | **The true measure of actual network speed and stability.** 65% signal on clean 5GHz Wi-Fi 6 is 5x faster than 95% signal on noisy 2.4GHz Wi-Fi 4! |
| **3. Optimization Level (System Tuned)** | Result Badge (`100% Optimized`) | Percentage of client-side Windows adapter driver and kernel TCP/IP stack tweaks actively configured. | Confirms your PC's hardware and network pipe are operating without driver or OS throttling. |

---

## 🚀 Key Features

* **🔍 Vendor-Agnostic NDIS Parameter Discovery**: Queries `HKLM\SYSTEM\CurrentControlSet\Control\Class\{4d36e972-e325-11ce-bfc1-08002be10318}\<Index>\Ndi\params` to identify properties officially supported by OEM drivers. Unsupported properties are skipped, never guessed.
* **⚡ Solved Inverted Enum Traps**: Handles reversed vendor encodings automatically:
  * **Intel (AX/BE series)**: `IbssTxPower` (`100` = Highest), `RoamAggressiveness` (`4` = Highest), `MIMOPowerSaveMode` (`3` = "No SMPS" disables power saving; setting `0` enables Auto SMPS!).
  * **Realtek (RTL series)**: `TxPwrLevel` (`0` = Highest, `4` = Lowest!), `RegROAMSensitiveLevel` (`65` = Highest, `127` = Disabled!).
* **📡 Native Hardware RSSI Telemetry**: Reads physical dBm directly via Windows Native Wi-Fi API (`wlanapi.dll` $\to$ `WlanGetNetworkBssList` $\to$ `lRssi`) with calibrated $\pm 1.5\text{ dB}$ uncertainty, avoiding inaccurate synthetic netsh heuristics.
* **🛡️ Security Policy Gating**: Open (unencrypted plaintext) and WEP (broken RC4) networks are hard-disqualified (`is_eligible = False`). Client roaming is restricted to configured Windows profiles (`require_saved_profile=True`) to prevent rogue AP interception.
* **👀 Read-Only Preview (Dry-Run) Mode**: Inspect proposed changes (`Current Value -> Proposed Value`) across all adapters before applying any modification.
* **🔄 Closed-Loop Link Governor**: EWMA-smoothed telemetry ($\alpha = 0.3$) with dual-threshold hysteresis ($-75\text{ dBm}$ fall / $-70\text{ dBm}$ recovery), 30s roaming cooldown, and $15\text{ dB}$ gradient sudden drop rule over $\le 12\text{s}$.
* **🌐 Windows Kernel TCP/IP Tuning**: Enables dynamic TCP Receive Window auto-tuning based on Bandwidth-Delay Product (BDP), multi-core Receive Side Scaling (RSS), and RFC 3168 Explicit Congestion Notification (ECN).
* **🖥️ High-Readability Help Architecture**: In-app zoom controls (`➕ Zoom In`, `➖ Zoom Out`, `↺ 100%`) and `🗖 Fullscreen` toggle for effortless formula and diagram inspection.

---

## 📊 Mathematical Architecture & RF Physics

### 1. Multi-Objective BSSID Fitness Function (M-BFF)
Every detected access point is evaluated across five objective dimensions:

$$S(N) = f_{\text{sig}}(P_{\text{rx}}) + f_{\text{cong}}(C) + f_{\text{band}}(B, P_{\text{rx}}) + f_{\text{phy}}(R) + f_{\text{sec}}(A) \in [0, 100\text{ Pts}]$$

#### A. Calibrated Continuous Sigmoid Signal Function ($0 - 30\text{ Pts}$)
$$f_{\text{sig}}(P_{\text{rx}}) = \frac{30.0}{1 + e^{-0.35(P_{\text{rx}} + 72.0)}}$$
* $P_{\text{rx}} \ge -55\text{ dBm} \implies 29.9\text{ pts}$ (Near-field saturation / Peak SNR)
* $P_{\text{rx}} = -72\text{ dBm} \implies 15.0\text{ pts}$ (Calibrated inflection point)
* $P_{\text{rx}} \le -90\text{ dBm} \implies < 0.1\text{ pts}$ (Sensitivity floor)

#### B. 22 MHz Spectral Mask Overlap Integral ($0 - 20\text{ Pts}$)
$$w_{\text{ov}}(\Delta ch) = \max\left(0.0, \text{round}\left(1.0 - \frac{|\Delta ch| \cdot 5\text{ MHz}}{22\text{ MHz}}, 2\right)\right)$$
* $\Delta ch = 0 \implies 1.00$ (Co-channel contention)
* $\Delta ch = 1 \implies 0.77$ (Severe adjacent spillover)
* $\Delta ch = 2 \implies 0.55$ (High adjacent spillover)
* $\Delta ch = 3 \implies 0.32$ (Moderate channel bleed)
* $\Delta ch = 4 \implies 0.09$ (Marginal filter rolloff overlap)
* $\Delta ch \ge 5 \implies 0.00$ (Orthogonal channels: 1, 6, 11)

Effective congestion and score:
$$C_{\text{eff}} = \sum_{j \in \mathcal{N}} \left[ w_{\text{ov}}(\Delta ch_j) \cdot w_{\text{sig}}(P_{\text{rx}, j}) \right] + 5 \cdot u$$
$$f_{\text{cong}}(C) = \max(0.0, 20.0 - 5.0 \cdot C_{\text{eff}})$$

#### C. Band Capacity & Attenuation Gate ($0 - 20\text{ Pts}$)
$$f_{\text{band}}(B, P_{\text{rx}}) = B \cdot \left[ \frac{1}{1 + e^{-0.25(P_{\text{rx}} + 80.0)}} \right]$$
* 6 GHz ($B = 20\text{ pts}$), 5 GHz ($B = 18\text{ pts}$), 2.4 GHz ($B = 8\text{ pts}$).

#### D. PHY Modulation Standard ($0 - 20\text{ Pts}$)
* Wi-Fi 7 (802.11be, 4096-QAM, 320 MHz, MLO): **$20.0\text{ Pts}$**
* Wi-Fi 6/6E (802.11ax, 1024-QAM, OFDMA): **$18.0\text{ Pts}$**
* Wi-Fi 5 (802.11ac, 256-QAM, 80 MHz): **$14.0\text{ Pts}$**
* Wi-Fi 4 (802.11n, 64-QAM, MIMO): **$9.0\text{ Pts}$**
* Legacy (802.11a/b/g): **$3.0\text{ Pts}$**

#### E. Security Evaluation & Policy Gating ($0 - 10\text{ Pts}$)
* WPA3-SAE: **$10.0\text{ Pts}$**
* WPA2-AES (CCMP): **$8.0\text{ Pts}$**
* WPA2-TKIP: **$3.0\text{ Pts}$**
* Open / WEP: **Hard Disqualification** (`is_eligible = False`)

---

### 2. Environmental Propagation Range Intervals
Friis 1-meter reference path loss:
$$\text{FSPL}_{1\text{m}}(\text{dB}) = 20\log_{10}(f_{\text{MHz}}) - 27.55$$

Environmental range interval estimation:
$$d_n = 10^{\frac{P_{\text{tx}} - P_{\text{rx}} - \text{FSPL}_{1\text{m}}}{10 \cdot n}}$$
* $n = 2.0$: Free Space / Line-of-Sight (LOS)
* $n = 3.0$: Indoor Residential / Drywall Partitions
* $n = 4.0$: Dense Concrete & Brick Obstructions

---

## 🛠️ Installation & Running

### Requirements
* Windows 10 or 11 (64-bit)
* Python 3.10+ (for source execution)
* Administrator privileges (required to configure Windows NDIS adapter properties and TCP/IP stack)

### Option 1: Run from Source
```powershell
# Clone the repository
git clone https://github.com/Enquzer/wifi-optimizer.git
cd wifi-optimizer

# Install dependencies
pip install -r scripts/requirements.txt

# Run application
python src/main.py
```

### Option 2: Pre-Packaged Standalone Executable
Launch [`app_bin/WiFi Optimizer/WiFi Optimizer.exe`](file:///d:/my_codes/Wifi%20optimizer/app_bin/WiFi%20Optimizer/WiFi%20Optimizer.exe) directly or click `WiFi Optimizer.lnk`.

---

## 🔒 Safety & Reversibility

* **Zero Disruptions**: The background Link Governor never resets active adapters or interrupts open streams.
* **Automatic Snapshots**: Captures `adapter_rollback_snapshot.json` prior to any modification.
* **One-Click Restore**: "Restore Defaults" reverts all modified keys to original values and cleans up newly created parameters.

---

## 📄 License & Credits
* **⚡ Powered by Fashion Flow 2026**
* **Developed by Enquzer Getachew**
* Licensed under the MIT License.
