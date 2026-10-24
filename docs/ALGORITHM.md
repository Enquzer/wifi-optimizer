# 📡 WiFi Optimizer — Algorithm & Implementation Notes

This document describes what the code actually does. It is not a claim of a new
radio technology. Every formula below is a standard heuristic, and the weights
are stated explicitly so they can be checked or changed.

---

## 1. Scope

The tool is a **client-side link-robustness controller**. It can:

- measure the current association (RSSI, band, channel, PHY rate, security),
- rank visible BSSIDs,
- change NDIS/driver settings the adapter exposes,
- change the Windows wireless power plan,
- optionally change DNS and two TCP settings,
- reload the miniport so NDIS values take effect,
- measure the result and report the delta.

It cannot raise the access point's transmit power, change the AP's channel
width, or remove path loss. There is no “signal boost” path in the code.

---

## 2. Telemetry

### 2.1 Native path (`wlan_native.py`)

Primary telemetry uses `wlanapi.dll`:

- `WlanOpenHandle` / `WlanEnumInterfaces` — interfaces and state.
- `WlanQueryInterface` with `wlan_intf_opcode_current_connection` — SSID, BSSID,
  `lRssi`, link quality, negotiated rx/tx rates, and the live security algorithms.
- `WlanGetNetworkBssList` — per-BSS entries: BSSID, `lRssi`, link quality,
  center frequency, capability, and security IEs.

The BSS entry layout is parsed with `ctypes.sizeof(_WLAN_BSS_ENTRY)` (360 bytes
on 64-bit Windows). Fixed offsets used:

| Field | Offset |
|---|---|
| BSSID | +40 |
| `lRssi` | +56 |
| `uLinkQuality` | +60 |
| capability | +88 |
| center frequency (kHz) | +92 |
| IE offset (from entry base) | +352 |
| IE size | +356 |

RSSI is accepted only when it is in `[-110, 0]`; otherwise the driver's link
quality is mapped through `rssi_from_quality()`.

### 2.2 Band classification

The center frequency is authoritative (`freq_to_channel()` in `wlan_native.py`):

```
2412 .. 2484 MHz    -> 2.4 GHz  (2484 MHz is channel 14)
4910 .. 5895 MHz    -> 5 GHz
5925 .. 7125 MHz    -> 6 GHz
57000 .. 71000 MHz  -> 60 GHz (labelled, not scored specially)
anything else       -> 2.4 GHz fallback
```

This avoids the ambiguity that channel numbers 1–14 can be 2.4 GHz or 6 GHz.
The `netsh` fallback cannot distinguish them reliably; it is used only if the
native API is unavailable, and it is documented as ambiguous.

### 2.3 Conversion helper

```
percent_to_dbm(p) = -100 + p / 2          # p in 0..100
```

This is a display fallback, not a calibrated mapping.

---

## 3. BSSID scoring (weighted sum)

`score_network()` returns a 0–100 score plus component scores. It is an explicit
weighted sum:

```
total = signal_score        (max 30)
      + channel_score       (max 20)
      + band_score          (max 20)
      + speed_score         (max 20)
      + security_score      (max 10)
```

`total` is clamped to `[0, 100]`.

### 3.1 Signal

```
signal_score = 30 / (1 + exp(-0.35 * (rssi_dbm + 72)))
```

A logistic curve with its midpoint near −72 dBm. It saturates: below about
−90 dBm the score is near 0, above about −55 dBm it is near 30. RSSI resolution
is about 1 dB; treat differences under 2 dB as noise.

### 3.2 Channel congestion

For a candidate BSS, congestion is the sum of overlap from every **other** BSS:

```
C_eff = Σ ( overlap(candidate, other)
            * clamp((other.rssi_dbm + 90) / 40, 0, 1) )
```

The weight means a −90 dBm neighbour contributes 0 and a −50 dBm neighbour
contributes 1. The candidate itself is excluded by BSSID (or object identity).

Overlap:

```
same band, 2.4 GHz:
    delta = |ch1 - ch2|
    delta == 0          -> 1.0
    delta <= 4          -> max(0, 1 - delta * 5 / 22)
    otherwise           -> 0.0

same band, 5 GHz or 6 GHz:
    ch1 == ch2          -> 1.0
    otherwise           -> 0.0

different bands         -> 0.0
```

The 2.4 GHz mask is a triangular approximation of a 22 MHz channel. 5/6 GHz
channels are treated as orthogonal; this ignores adjacent-channel leakage and
is a simplification, not a measurement.

```
channel_score = clamp(20 - 5 * C_eff, 0, 20)
```

BSS Load (`u` / station count) is **not** used. The old documentation claimed a
`(1 - u)` factor; the implementation never had it.

### 3.3 Band preference

`preferred_band` comes from the profile. The score is

```
band_score = clamp(20 * g * h, 0, 20)
```

with `g`:

| preferred | 6 GHz | 5 GHz | 2.4 GHz |
|---|---|---|---|
| `5GHz` | 1.00 | 0.95 | 0.35 |
| `2.4GHz` | 0.50 | 0.60 | 1.00 |
| `auto` | 1.00 | 0.90 | 0.50 |

and a link-margin taper `h`:

```
5/6 GHz: h = clamp((rssi_dbm + 80) / 15, 0.1, 1.0)
2.4 GHz: h = clamp((rssi_dbm + 95) / 20, 0.1, 1.0)
```

The taper encodes the practical fact that a weak 5 GHz link is worse than a
strong 2.4 GHz link, while a strong 5/6 GHz link is preferable. The constants
are heuristics.

### 3.4 PHY generation

`parse_radio_rank()` in `optimizer.py` maps the driver's PHY name to a 0–20 score:

| PHY | Score |
|---|---|
| 802.11be | 20 |
| 802.11ax | 18 |
| 802.11ac | 14 |
| 802.11n | 9 |
| 802.11g / 802.11a | 4 |
| 802.11b | 1 |
| unknown | 7 |

If the adapter's own radio type is known, `speed_score` is the minimum of the
AP's rank and the adapter's rank, so advertising a standard the client cannot
use does not inflate the score.

### 3.5 Security

| Security | Score |
|---|---|
| WPA3-Personal / Enterprise | 10 |
| WPA2 with CCMP/GCMP | 8 |
| WPA2 with TKIP | 3 |
| WPA (any) | 3 |
| Open / WEP | 0 |
| Unknown | 2 |

Open and WEP networks are marked ineligible when
`profile.allow_insecure_networks` is false (the default). WPA3-SAE is recognised
by the AKM suite, not by a string match.

---

## 4. Profiles

`PROFILES` in `optimizer.py`:

| Key | Width 2.4 / 5 | Preferred band | Roaming | Power save | TCP | Notes |
|---|---|---|---|---|---|---|
| `far_range` | 20 MHz / 20 MHz | 2.4 GHz | 1/5 | off | no | far/weak links |
| `balanced` | auto / auto | auto | 2/5 | off | yes | default |
| `max_speed` | auto / auto | 5 GHz | 3/5 | off | yes | short range; latency tweaks |
| `power_save` | auto / auto | 2.4 GHz | 3/5 | **on** | no | battery; transmit power 2/5 |

DNS is a separate opt-in (`apply_dns=True`), not a profile default. It does not
change the RF link and can break local hostnames, so the GUI leaves it off unless
the Settings switch is enabled.

`max_range` and `selfish` are aliases for `far_range`. The old “selfish airtime”
bundle is now the opt-in `apply_advanced_tier_b_tweaks()`: it enables priority
tagging and the vendor throughput booster, optionally narrows the channel width,
enables legacy Short GI only on non-HE PHYs, disables U-APSD by default, and
disables packet coalescing only when the caller explicitly asks. It does not
claim airtime priority over other stations.

Narrowing to 20 MHz at range is a real trade-off: it lowers the peak PHY rate
but usually improves SNR per subcarrier and reduces retries. On a strong link it
can reduce throughput. The GUI starts on `far_range`; background automation can
move to `balanced` (or back) based on the controller's sustained decisions.

---

## 5. Actuation

`adapter_manager.py`:

1. Find present WLAN adapters by GUID. Stale class subkeys from renamed or
   removed drivers are excluded (the old code wrote `0002`, `0014`, `0017` as
   well as the live `0021`).
2. Read the driver's `Ndi\params` database. A property that is not declared
   there is **skipped**, never guessed.
3. Resolve the semantic intent (`max`, `2.4GHz`, `disable`, …) to the driver's
   enum code by matching the enum label, with a rank-proximity fallback for
   vendor-specific labels.
4. Write the value, then read it back.
5. Record the outcome in a per-run ledger:
   - `changed` — the value was different and was read back at the new value,
   - `noop` — it was already at the target,
   - `failed` — the write or read-back failed,
   - `skipped` — the driver does not expose the property.
6. If at least one **NDIS** value changed, disable/enable the adapter (or
   `Restart-NetAdapter`) so the miniport re-reads it. DNS, power-plan and TCP
   changes do not trigger a reload.

Preview mode performs steps 1–3, reads the current values, records the proposed
values, and writes nothing. Dry-run keeps an in-memory mock registry.

### 5.1 Power plan

The wireless power-saving index is read and written for **both AC and DC**:

```
powercfg /setacvalueindex SCHEME_CURRENT 19cbb8fa-5279-450e-9fac-8a3d5fedd0c1 12bbebe6-58d6-4636-95bb-3217ef867c1a <0..3>
powercfg /setdcvalueindex ...
powercfg /setactive SCHEME_CURRENT
```

The old code changed only AC. `19cbb8fa-…` is the standard Windows Wireless
Adapter Settings subgroup and `12bbebe6-…` is its Power Saving Mode setting;
`SCHEME_CURRENT` selects the active scheme. The interface name is not involved.
The AC/DC values are read back after writing.

### 5.2 DNS / TCP

- DNS is set with `netsh interface ipv4 set dnsservers ... static` and
  `netsh interface ipv4 add dnsservers ... index=2`; the prior servers are read
  first and stored in `system_rollback_snapshot.json`. The configured servers are
  read back and compared with the requested list.
- TCP changes are limited to autotuning (`normal`) and, when the profile or the
  Settings opt-in asks for it, ECN (`enabled`). Both are read back with
  `Get-NetTCPSetting`.
  On a typical Windows 11 machine autotuning is already
  `normal`, so the honest outcome is often `noop`.
- RSS, window scaling and other global tweaks were removed. They were either
  already enabled or unrelated to Wi-Fi latency.

---

## 6. Robustness controller

`robustness.py` implements a mode ladder:

```
far_range  ->  balanced  ->  max_speed
```

It is a control loop, not a new waveform:

```
observe(RSSI, efficiency, jitter, loss)
  -> decide(mode)
  -> actuate(profile)
  -> re-measure
```

### 6.1 Smoothing

RSSI and efficiency are kept in a deque of the last 6 samples and smoothed with
an EWMA, `alpha = 0.4`:

```
ewma[0] = x[0]
ewma[i] = 0.4 * x[i] + 0.6 * ewma[i-1]
```

### 6.2 Decision rules

With defaults:

| Condition | Result |
|---|---|
| `ewma_rssi <= -72` | degraded |
| `ewma_eff < 0.10` | degraded |
| `jitter > 60 ms` | degraded |
| `loss > 3 %` | degraded |
| `ewma_rssi >= -64` **and** `ewma_eff >= 0.22` (when supplied) **and** `jitter <= 30 ms` (when supplied) **and** `loss <= 1 %` (when supplied) | healthy |
| degraded | candidate `far_range` |
| healthy and not already `max_speed` | candidate `max_speed` |
| healthy, `allow_speed=False`, currently `far_range` | candidate `balanced` |
| neither, and currently `far_range` | candidate `balanced` |

`allow_speed=False` is used by the GUI's background automation: a healthy link
may recover from `far_range` to `balanced`, but the app never auto-widens a link
to `max_speed`. The default controller (`allow_speed=True`) exposes the full
ladder for callers that want it.

A candidate must persist for **2 samples**. The exception is extreme
degradation: `ewma_rssi <= degraded_rssi - 8` (≤ −80 dBm) switches immediately.
After a switch the controller waits **600 s** before another actuation (the GUI
passes `cooldown_s=900`, matching its 15-minute automation limit).

These thresholds are defaults, not universal constants. They are constructor
arguments and are easy to tune.

### 6.3 Background governor

`monitor.py` has a separate, simpler governor used for desktop notifications.
It does not write settings:

- EWMA `alpha = 0.3`.
- `HEALTHY -> DEGRADED` after 3 samples below −75 dBm, or immediately on a
  ≥15 dB drop within 12 s.
- `DEGRADED -> HEALTHY` after 5 samples at or above −70 dBm.
- 30 s cooldown.

The GUI receives every `MonitorSnapshot` and feeds the raw RSSI plus (only while
traffic is flowing) efficiency into `RobustnessController`, which smooths them.
Mode changes are applied by `app._auto_apply_mode()` on the UI thread; `main.py`
only forwards the monitor's alert text to the toast notifier.

### 6.4 Efficiency

```
efficiency = measured_goodput_mbps / min(rx_phy_mbps, tx_phy_mbps)
```

- Low efficiency + decent RSSI → wide/congested channel, retries, or airtime
  contention. `far_range` (narrower channel, lower band) is a reasonable probe.
- Low efficiency + low RSSI → sensitivity edge. Narrowing may help; moving
  closer or adding an AP is the real fix.

The ratio is only meaningful while traffic is flowing. The controller therefore
treats a missing efficiency sample as “unknown”, not as zero.

---

## 7. Measurement

`benchmark.py` measures:

- **Throughput**: one HTTPS GET of N bytes from a public endpoint, wall-clock
  time, converted to Mbps for the payload. This is single-stream goodput, not
  PHY rate and not a speedtest.
- **Latency**: repeated TCP connects to a target; median and p95.
- **Jitter**: mean absolute difference between consecutive RTT samples.
- **Loss**: failed connection attempts ÷ attempts.
- **DNS**: time to resolve a hostname.
- **Efficiency**: throughput ÷ `min(rx, tx)` PHY rate, read immediately after
  the transfer.

`measure_median(reps=N)` runs the whole measurement N times and reports the
median of each metric. Single samples are noisy; the CLI prints this caveat.

Results are appended to `benchmarks.jsonl`. Nothing is uploaded.

---

## 8. Safety and rollback

- `begin_run()` starts a fresh in-memory change ledger.
- `backup_adapter_settings()` writes a timestamped
  `adapter_settings_backup_YYYYMMDD_HHMMSS.json` (five newest kept) with all
  discovered adapter values, plus `system_rollback_snapshot.json` for DNS,
  wireless power AC/DC, and TCP state. The system snapshot is **not overwritten**
  by later runs, so it keeps the first pre-change state; a successful Restore
  removes it.
- `rollback_settings()` replays the per-change log in reverse, then restores the
  system snapshot. It reloads the adapter only if registry values were restored.
  Entries whose adapter subkey no longer exists are skipped and logged, so a
  renamed driver cannot be written. If any write fails, the snapshot is kept so
  the restore can be retried after elevation.
- The legacy fixed-name `adapter_settings_backup.json` is excluded from the
  default restore path and from pruning because it was overwritten after
  optimization and is not a valid pre-change state.

The snapshot files are per-machine artifacts. They are not required for normal
operation and can be deleted; the app will simply have nothing to restore.

---

## 9. Known simplifications

1. The 2.4 GHz overlap mask is triangular and ignores capture effects.
2. 5/6 GHz adjacent channels are treated as orthogonal.
3. PHY generation is a static rank, not a rate-vs-SNR model.
4. Security scoring is coarse; it does not evaluate 802.11w/PMF or enterprise
   configuration quality.
5. The controller thresholds are heuristic starting points.
6. Throughput depends on the ISP and WAN; the benchmark measures one path at one
   moment.
7. `netsh` fallback cannot disambiguate 2.4 GHz and 6 GHz channel numbers.

These are deliberate scope limits. The tool reports what it changed and what it
measured, and does not convert those into an invented percentage.
