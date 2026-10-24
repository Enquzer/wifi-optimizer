# 📶 WiFi Optimizer

> **Powered by Fashion Flow 2026**
>
> **Developed by Enquzer Getachew**

Windows 10/11 client-side Wi-Fi diagnostics and link-robustness tool.

This is **not** a signal booster. A program running on your laptop cannot raise the
router's transmit power, move the router, or remove walls. What it can do is read
the real link, choose sensible client settings, apply them correctly, verify that
they were applied, and measure whether the link actually improved.

---

## What it does

| Area | Reality |
|---|---|
| **Telemetry** | Reads RSSI (dBm), link quality, center frequency, channel/band, negotiated PHY rates and security from the Windows Native Wi-Fi API (`wlanapi.dll`). No localized `netsh` text parsing on the primary path. |
| **Scanning** | Returns **one entry per BSSID**, not one per SSID. Mesh nodes and dual-band APs are no longer collapsed into a single (often worst) row. |
| **Scoring** | An explicit weighted sum: signal (30) + channel (20) + band (20) + PHY (20) + security (10). The weights are a UX heuristic, not a calibrated throughput model. |
| **Applying** | Discovery-validated NDIS registry writes (only keywords the driver's `Ndi\params` exposes), power-plan changes, optional DNS/TCP changes, and a miniport reload when an NDIS value changed so it actually takes effect. |
| **Verification** | Every write is read back and classified as **changed**, **already at target**, **failed**, or **unsupported**. There is no fabricated “15–40%” claim. |
| **Measurement** | Single-stream HTTPS download, TCP-connect RTT (median/p95), jitter, loss, DNS time, and goodput ÷ PHY efficiency, saved to `benchmarks.jsonl`. |

## What it cannot do

- Increase the router's EIRP or antenna gain.
- Make a −85 dBm link fast.
- Override the AP's channel, width, or EDCA parameters.
- Guarantee a throughput improvement. The ISP, WAN link, other clients and
  environmental noise all matter. The app reports measured deltas and lets you
  decide whether they are real.

---

## Profiles

| Profile | Width | Band | Roaming | Power save | TCP | Intended use |
|---|---|---|---|---|---|---|
| **Far / Robust** | 20 MHz | 2.4 GHz preferred | 1/5 (minimum) | off | no | Weak/far links where retries dominate |
| **Balanced** | auto | auto | 2/5 | off | yes | General use |
| **Max Speed** | auto | 5/6 GHz preferred | 3/5 | off | yes | Strong signal, short range |
| **Power Saver** | auto | 2.4 GHz | 3/5 | on | no | Battery first; higher latency |

`max_range` and the legacy `selfish` key are aliases for **Far / Robust**.

DNS is **never** changed unless you enable *Apply these DNS servers when
optimizing* on the Settings page. DNS does not affect RF or throughput. ECN is
also opt-in and off by default; it is enabled only when the Settings switch asks
for it (or a script passes `apply_ecn=True`).

Why 20 MHz at range: the same transmit power concentrated into a narrower channel
usually raises SNR per subcarrier and reduces retries. It lowers the peak PHY rate,
but a link that survives retries is often faster in practice than a wide link that
keeps collapsing. This is a trade-off, not a magic gain.

---

## Measurement

```powershell
python scripts/benchmark.py --reps 3 --label before
# change a profile / move the laptop
python scripts/benchmark.py --reps 3 --label after
```

Or use the dashboard's **Measure before/after** switch. Results are appended to
`benchmarks.jsonl`; compare medians, not single samples.

```powershell
python scripts/verify_optimization.py before
# run the optimizer
python scripts/verify_optimization.py after
```

`verify_optimization.py` prints before → after RSSI, band/channel, PHY rate,
throughput, RTT, jitter, loss, and the exact registry entries the optimizer
touched.

### Metric definitions

- **RSSI (dBm)** — received RF power. Native resolution is about 1 dB; treat it as
  ±1–2 dB, not a calibrated instrument.
- **Link quality (%)** — driver-reported 0–100 indicator. It is not linear in dBm.
- **PHY rate (Mbps)** — negotiated modulation/coding rate, not Internet throughput.
- **Efficiency** — measured download goodput ÷ `min(rx, tx)` PHY rate. Low with a
  decent RSSI usually means a wide/congested channel; low with a low RSSI means
  you are at the sensitivity edge.
- **p95 RTT** — 95th percentile TCP-connect time. More useful than an average when
  the link is retrying.

---

## Safety and reversibility

- **Preview mode** shows current → proposed values and writes nothing.
- **Timestamped backups** (`adapter_settings_backup_YYYYMMDD_HHMMSS.json`, five
  newest kept) are written before changes. The old fixed-name backup was
  overwritten after optimization and is deliberately ignored.
- **System snapshot** (`system_rollback_snapshot.json`) records DNS, the wireless
  power-plan AC/DC indices, and TCP autotuning/ECN/timestamps.
- **Restore Defaults** replays the registry change log **and** the system snapshot,
  skips stale adapter subkeys from renamed/ghost drivers, then reloads the adapter
  if registry values were restored. Failed restores keep the snapshot so they can
  be retried after elevation.
- **Driver discovery** means unsupported properties are skipped, never guessed.

Registry and NDIS changes require **Administrator**. Without elevation the app
reports them as failed instead of pretending they worked. DNS/TCP changes may
still apply, and they are snapshotted so they can be undone.

---

## Auto-optimize (opt-in)

The background monitor can apply a profile when the link is degraded or recovers.
It is:

- **off by default**,
- rate-limited to once per 15 minutes,
- hysteretic (the controller requires a sustained condition),
- disruptive for a few seconds because the adapter reloads.

It uses `RobustnessController` from `src/core/robustness.py`: EWMA smoothing,
two-sample persistence, and a cooldown. Background automation is created with
`allow_speed=False`, so it only moves **Far / Robust ↔ Balanced**; it never
auto-widens a good link to `max_speed` and never restarts the adapter just
because the signal is strong.

---

## Installation

### Requirements

- Windows 10 or 11 (64-bit)
- Python 3.10+ for source execution
- Administrator privileges for NDIS/registry changes

### Run from source

```powershell
pip install -r scripts/requirements.txt
python src/main.py
```

### Packaged build

Launch `app_bin/WiFi Optimizer/WiFi Optimizer.exe` or the provided shortcut.

---

## Project layout

```
src/core/wlan_native.py     Native Wi-Fi API telemetry (locale-independent)
src/core/wifi_scanner.py    BSS/connection model + netsh fallback
src/core/adapter_manager.py Discovery, writes, change ledger, backup/rollback
src/core/optimizer.py       Profiles, scoring, actuation
src/core/benchmark.py       Real download/RTT/jitter/DNS measurement
src/core/robustness.py      Hysteretic far/balanced/speed controller
src/core/monitor.py         Background sampler + governor
src/ui/app.py               GUI
scripts/benchmark.py        CLI measurement
scripts/verify_optimization.py  Before/after comparison
scripts/test_core.py        Fast logic checks (no admin/network writes)
```

## Limitations

- Throughput samples are noisy; background traffic and other clients affect them.
- A driver that does not expose a width/band keyword cannot be narrowed by software.
- 2.4/5/6 GHz channel numbers overlap; band classification uses the native center
  frequency, and the netsh fallback is explicitly ambiguous.
- The scoring weights and controller thresholds are starting points, not universal
  constants. Tune them in `optimizer.py` / `robustness.py`.

---

## License & Credits

- **Powered by Fashion Flow 2026**
- **Developed by Enquzer Getachew**
- MIT License
