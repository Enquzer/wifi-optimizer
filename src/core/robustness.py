"""
WiFi Optimizer - Closed-loop robustness controller
==================================================

What is actually novel (and honest) here is not a new radio waveform. It is a
*control loop* that changes the client's mode bundle based on measured link
behaviour rather than on a one-shot guess:

    observe(RSSI, goodput/PHY efficiency, p95 RTT, jitter, loss)
        -> decide(far / balanced / speed)
        -> actuate(channel width, band preference, roaming, power save)
        -> re-measure

Mode ladder
-----------
far_range : 20 MHz, 2.4 GHz preferred, roam=1, power save off
balanced  : auto width, auto band, roam=2, power save off
max_speed : auto width, 5/6 GHz preferred, roam=3, latency tweaks on

The controller uses hysteresis (must be sustained for 2 samples), an exponential
moving average, and a cooldown, so it cannot flap between modes. It only ever
changes *client* settings; it cannot raise the AP's power.

Why efficiency matters
----------------------
RSSI alone is a poor predictor: a -70 dBm link at 20 MHz can deliver more
goodput than a -62 dBm link at 80 MHz in a congested band. The ratio
`goodput / min(rx, tx PHY rate)` captures retries, airtime contention and
rate-adaptation collapse in one number. A low ratio with a decent RSSI usually
means the channel is wide/congested; a low ratio with a low RSSI means the link
is at the sensitivity edge. The controller treats those differently.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Deque, Dict, Optional
from collections import deque

logger = logging.getLogger(__name__)


MODE_LABELS = {
    "far_range": "Far / Robust (20 MHz, 2.4 GHz, low roaming)",
    "balanced": "Balanced (auto width/band)",
    "max_speed": "Max Speed (wide channel, 5/6 GHz)",
}


@dataclass
class Decision:
    mode: str
    changed: bool
    reason: str
    ewma_rssi_dbm: float = 0.0
    ewma_efficiency: Optional[float] = None
    confidence: float = 0.0


class RobustnessController:
    """
    Hysteretic mode selector.

    Parameters
    ----------
    degraded_rssi : float
        At or below this RSSI the link is treated as far-range.
    healthy_rssi : float
        At or above this RSSI (with good efficiency) speed mode is allowed.
    degraded_efficiency : float
        goodput/PHY below this is "collapsed", regardless of RSSI.
    healthy_efficiency : float
        goodput/PHY above this (plus healthy RSSI) allows speed mode.
    consecutive : int
        Samples a new mode must persist before switching.
    cooldown_s : float
        Minimum time between actuations.
    """

    def __init__(self,
                 degraded_rssi: float = -72.0,
                 healthy_rssi: float = -64.0,
                 degraded_efficiency: float = 0.10,
                 healthy_efficiency: float = 0.22,
                 max_jitter_ms: float = 60.0,
                 max_loss_pct: float = 3.0,
                 consecutive: int = 2,
                 cooldown_s: float = 600.0,
                 window: int = 6,
                 allow_speed: bool = True):
        self.degraded_rssi = degraded_rssi
        self.healthy_rssi = healthy_rssi
        self.degraded_efficiency = degraded_efficiency
        self.healthy_efficiency = healthy_efficiency
        self.max_jitter_ms = max_jitter_ms
        self.max_loss_pct = max_loss_pct
        self.consecutive = max(1, int(consecutive))
        self.cooldown_s = cooldown_s
        # When False the controller never proposes max_speed; a healthy link
        # recovers from far_range to balanced instead. Useful for background
        # automation, where silently widening a good link is surprising.
        self.allow_speed = allow_speed
        self._rssi: Deque[float] = deque(maxlen=max(2, window))
        self._eff: Deque[float] = deque(maxlen=max(2, window))
        self._mode = "balanced"
        self._candidate: Optional[str] = None
        self._candidate_count = 0
        self._last_switch = 0.0

    @property
    def mode(self) -> str:
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Synchronize the controller with a manual profile choice.

        This does not actuate anything and does not reset the cooldown; it only
        prevents the controller from thinking the link is in a profile the user
        has already overridden.
        """
        if mode in ("far_range", "balanced", "max_speed"):
            self._mode = mode
            self._candidate = None
            self._candidate_count = 0

    def _ewma(self, values: Deque[float]) -> float:
        if not values:
            return 0.0
        alpha = 0.4
        result = values[0]
        for v in list(values)[1:]:
            result = alpha * v + (1 - alpha) * result
        return result

    def observe(self,
                rssi_dbm: float,
                efficiency: Optional[float] = None,
                jitter_ms: Optional[float] = None,
                loss_pct: Optional[float] = None,
                now: Optional[float] = None) -> Decision:
        """Feed one observation; returns the (possibly unchanged) decision."""
        now = time.time() if now is None else now
        self._rssi.append(float(rssi_dbm))
        if efficiency is not None:
            self._eff.append(float(efficiency))

        ewma_rssi = self._ewma(self._rssi)
        # Efficiency is only meaningful while traffic is flowing. If this
        # observation has no sample (idle link), treat it as unknown instead of
        # reusing a stale low value that would keep the controller degraded.
        ewma_eff = self._ewma(self._eff) if (efficiency is not None and self._eff) else None

        degraded = (
            ewma_rssi <= self.degraded_rssi
            or (ewma_eff is not None and ewma_eff < self.degraded_efficiency)
            or (jitter_ms is not None and jitter_ms > self.max_jitter_ms)
            or (loss_pct is not None and loss_pct > self.max_loss_pct)
        )
        healthy = (
            ewma_rssi >= self.healthy_rssi
            and (ewma_eff is None or ewma_eff >= self.healthy_efficiency)
            and (jitter_ms is None or jitter_ms <= self.max_jitter_ms / 2)
            and (loss_pct is None or loss_pct <= 1.0)
        )

        if degraded:
            candidate = "far_range"
            reason = (f"RSSI {ewma_rssi:.0f} dBm"
                      + (f", efficiency {ewma_eff*100:.0f}%" if ewma_eff is not None else "")
                      + (f", jitter {jitter_ms:.0f} ms" if jitter_ms else "")
                      + (f", loss {loss_pct:.1f}%" if loss_pct else ""))
            confidence = 1.0 if ewma_rssi <= self.degraded_rssi - 8 else 0.7
        elif healthy and self.allow_speed and self._mode != "max_speed":
            candidate = "max_speed"
            reason = (f"RSSI {ewma_rssi:.0f} dBm and efficiency "
                      f"{(ewma_eff or 0)*100:.0f}% are healthy")
            confidence = 0.8
        elif healthy and not self.allow_speed and self._mode == "far_range":
            candidate = "balanced"
            reason = (f"RSSI {ewma_rssi:.0f} dBm is healthy; speed mode is "
                      "disabled for background automation")
            confidence = 0.6
        elif not degraded and not healthy and self._mode == "far_range":
            candidate = "balanced"
            reason = "Link left the far-range regime"
            confidence = 0.5
        else:
            self._candidate = None
            self._candidate_count = 0
            return Decision(self._mode, False,
                            f"Holding {self._mode} (RSSI {ewma_rssi:.0f} dBm"
                            + (f", eff {ewma_eff*100:.0f}%" if ewma_eff is not None else "")
                            + ")",
                            ewma_rssi, ewma_eff, 0.0)

        if candidate == self._mode:
            self._candidate = None
            self._candidate_count = 0
            return Decision(self._mode, False, f"Holding {self._mode}: {reason}",
                            ewma_rssi, ewma_eff, confidence)

        # Sustain the candidate for N samples; extreme degradation is immediate.
        if candidate == self._candidate:
            self._candidate_count += 1
        else:
            self._candidate = candidate
            self._candidate_count = 1

        required = 1 if (candidate == "far_range" and ewma_rssi <= self.degraded_rssi - 8) \
            else self.consecutive
        if self._candidate_count < required:
            return Decision(self._mode, False,
                            f"Candidate {candidate} not yet sustained "
                            f"({self._candidate_count}/{required}): {reason}",
                            ewma_rssi, ewma_eff, confidence * 0.5)

        if now - self._last_switch < self.cooldown_s:
            remaining = int(self.cooldown_s - (now - self._last_switch))
            return Decision(self._mode, False,
                            f"Would switch to {candidate}, but cooldown has {remaining}s left",
                            ewma_rssi, ewma_eff, confidence)

        old = self._mode
        self._mode = candidate
        self._last_switch = now
        self._candidate = None
        self._candidate_count = 0
        logger.info("Robustness mode %s -> %s (%s)", old, candidate, reason)
        return Decision(candidate, True, f"{old} → {candidate}: {reason}",
                        ewma_rssi, ewma_eff, confidence)


def actuate(decision: Decision,
            adapter_name: Optional[str] = None,
            progress_callback=None,
            measure: bool = False):
    """
    Apply a decision by running the corresponding client profile.

    Kept separate from observe() so callers can require explicit consent before
    the controller is allowed to touch the system. Imported lazily to avoid a
    circular import.
    """
    if not decision.changed:
        return None
    from core.optimizer import run_optimization
    return run_optimization(decision.mode, adapter_name or "Wi-Fi",
                            progress_callback=progress_callback,
                            measure=measure)
