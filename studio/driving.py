"""Driving channels (F5): brake events, coasting spans, per-corner grip utilization.

PACER-FREE (numpy only). Labels three things on a lap:

  * BRAKE EVENTS — contiguous deceleration below -theta_b, held open with Schmitt hysteresis
    (release above -theta_b*RELEASE_RATIO) so threshold ripple doesn't shatter one zone.
  * COASTING SPANS — off-power transitions: the car is DECELERATING from drag/engine braking
    (decel above COAST_DRAG_MIN) but NOT braking (below theta_b), while moving. This is the
    throttle-off-to-brake gap, which decelerates — unlike the old "speed stays flat" test that
    rejected real coasts.
  * PER-CORNER GRIP UTILIZATION — median(|g|)/envelope_max inside each corner window.

Brake and coast run on the LONGITUDINAL g derived from the GPS SPEED TRACE (d|v|/dt), not the
IMU longitudinal channel: on real recordings the IMU forward axis is vibration-dominated
(~2x RMS, r~0.4 vs the GPS-derived ground truth — see studio/docs/gmeter-validation.md), so it
mis-scales the threshold and misses ~a third of braking. The speed trace is the clean, validated
brake signal. Grip still uses the IMU lateral g (which correlates strongly).

theta_b is physical: floored to a real brake application and only gently adapted up to the
session's own braking distribution, so the same brake reads consistently across laps/drivers.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import boxcar

G = 9.80665  # m/s^2

# --- model constants -------------------------------------------------------------------
SMOOTH_S = 0.10           # boxcar on the longitudinal g before thresholding
BRAKE_G_FLOOR = 0.16      # g; a genuine brake application (above lift/engine-braking ~0.05-0.12 g)
BRAKE_G_CEIL = 0.30       # g; clamp the session-adaptive raise to a sane karting range
BRAKE_ADAPT_PCT = 25.0    # adapt theta_b up to this low percentile of the session's braking decel
BRAKE_SAMPLE_FLOOR = 0.05 # decel above this counts toward the braking-decel distribution
RELEASE_RATIO = 0.35      # Schmitt release at theta_b*this; low so one zone with a mid-dip or a
#                           trailing light decel stays a single event (less fragmentation)
MIN_BRAKE_S = 0.25        # drop brake runs shorter than the shortest real brake application
COAST_DRAG_MIN = 0.03     # g; below this |decel| is steady-state cruise, not coasting
MIN_COAST_S = 0.25        # drop coast blips between brake-release and throttle-pickup
MOVING_KMH = 14.4         # 4.0 m/s; below this a sample is "stopped"
MAX_LONG_G = 2.0          # clip speed-derivative spikes (a GPS glitch can't manufacture a brake)


def speed_long_g(speed_kmh, t) -> np.ndarray:
    """Longitudinal g from the speed trace: (d|v|/dt)/G — positive accelerating, negative
    braking. The clean, GPS-validated brake signal (the IMU forward axis is vibration-dominated).
    Spikes are clipped to a sane envelope."""
    v = np.asarray(speed_kmh, float) / 3.6
    t = np.asarray(t, float)
    n = min(len(v), len(t))
    if n < 3:
        return np.zeros(n)
    g = np.gradient(v[:n], t[:n]) / G
    return np.clip(g, -MAX_LONG_G, MAX_LONG_G)


@dataclass(frozen=True)
class Thresholds:
    """Brake threshold derived from one session's own (speed-derived) braking-decel distribution
    (g, positive magnitudes)."""

    theta_b: float       # brake decel threshold: a brake event is long_g < -theta_b
    n_moving: int        # moving samples the distribution was measured over
    # The measured braking-decel percentiles that motivate the value (for the load-time print).
    brake_p75: float
    brake_p90: float
    brake_max: float

    def describe(self) -> str:
        return (f"driving channels: brake threshold theta_b={self.theta_b:.3f} g "
                f"(speed-derived longitudinal); over {self.n_moving} moving samples the braking "
                f"decel ran p75={self.brake_p75:.3f}, p90={self.brake_p90:.3f}, "
                f"max={self.brake_max:.3f} g.")


@dataclass(frozen=True)
class BrakeEvent:
    """One detected braking zone within a lap, in that lap's own odometer/elapsed space."""

    onset_dist: float    # lap odometer (m) where the brake application begins
    onset_time: float    # elapsed (s, from the lap start) at the onset
    peak_decel: float    # peak braking decel over the event (g, positive magnitude)
    duration: float      # how long the event lasts (s)


@dataclass(frozen=True)
class CoastSpan:
    """One coasting span within a lap (off power, decelerating but not braking), in that lap's
    own odometer space."""

    start_dist: float    # lap odometer (m) where the coast begins
    end_dist: float      # lap odometer (m) where it ends
    duration: float      # how long it lasts (s)


def _smooth_window(t: np.ndarray) -> int:
    """Samples spanning SMOOTH_S given the (roughly uniform) time step."""
    if len(t) < 3:
        return 1
    dt = float(np.median(np.diff(t)))
    return max(int(round(SMOOTH_S / max(dt, 1e-9))), 1)


def derive_thresholds(long_g, speed_kmh) -> Thresholds:
    """Brake threshold over the session's MOVING samples. `long_g` is the CLEAN (speed-derived)
    longitudinal g (see speed_long_g); `speed_kmh` aligned. theta_b is floored to a physical brake
    application and only gently raised toward the session's own braking distribution, then clamped
    — so a no-braking session yields no false events and a hard session doesn't run away."""
    long_g = np.asarray(long_g, float)
    speed_kmh = np.asarray(speed_kmh, float)
    n = min(len(long_g), len(speed_kmh))
    long_g, speed_kmh = long_g[:n], speed_kmh[:n]
    moving = speed_kmh > MOVING_KMH
    if not np.any(moving):
        moving = np.ones(n, dtype=bool)  # degenerate: use all samples rather than divide by 0
    decel = np.maximum(-long_g[moving], 0.0)  # braking decel magnitude (0 when not braking)
    braking = decel[decel > BRAKE_SAMPLE_FLOOR]
    if braking.size:
        adapt = float(np.percentile(braking, BRAKE_ADAPT_PCT))
        theta_b = float(np.clip(adapt, BRAKE_G_FLOOR, BRAKE_G_CEIL))
    else:
        theta_b = BRAKE_G_FLOOR  # ~no braking in the session: floor it (no false events)
    return Thresholds(
        theta_b=theta_b, n_moving=int(np.sum(moving)),
        brake_p75=float(np.percentile(braking, 75.0)) if braking.size else 0.0,
        brake_p90=float(np.percentile(braking, 90.0)) if braking.size else 0.0,
        brake_max=float(decel.max()) if decel.size else 0.0,
    )


def brake_events(dist, elapsed, long_g, theta_b: float) -> list[BrakeEvent]:
    """Detect braking zones on one lap (aligned dist/elapsed/long_g, same lap; gmeter sign:
    long_g<0 braking). Held open with Schmitt hysteresis (see RELEASE_RATIO). `long_g` is the
    clean speed-derived longitudinal. Returns events in track order."""
    dist = np.asarray(dist, float)
    elapsed = np.asarray(elapsed, float)
    g = np.asarray(long_g, float)
    n = min(len(dist), len(elapsed), len(g))
    dist, elapsed, g = dist[:n], elapsed[:n], g[:n]
    if n < 2:
        return []
    g = boxcar(g, _smooth_window(elapsed))
    hi = float(theta_b)                  # ENTER braking below -hi
    lo = float(theta_b) * RELEASE_RATIO  # RELEASE only once decel recovers above -lo
    out: list[BrakeEvent] = []
    i = 0
    while i < n:
        if g[i] < -hi:
            j0 = i
            while i > 0 and g[i - 1] < -lo:  # extend backwards into the lo band (onset)
                j0 -= 1
                i -= 1
            j1 = j0
            while j1 + 1 < n and g[j1 + 1] < -lo:  # extend forwards until decel releases
                j1 += 1
            seg = g[j0:j1 + 1]
            duration = float(elapsed[j1] - elapsed[j0])
            if duration >= MIN_BRAKE_S:
                out.append(BrakeEvent(
                    onset_dist=float(dist[j0]),
                    onset_time=float(elapsed[j0]),
                    peak_decel=float(-seg.min()),  # deepest decel as a positive magnitude
                    duration=duration,
                ))
            i = j1 + 1
        else:
            i += 1
    return out


def coasting_spans(dist, elapsed, speed_kmh, long_g, theta_b: float) -> list[CoastSpan]:
    """Detect coasting spans on one lap: off power, decelerating from drag/engine braking
    (COAST_DRAG_MIN < decel < theta_b) while moving. `long_g` is the clean speed-derived
    longitudinal. Aligned arrays, same lap; spans in track order."""
    dist = np.asarray(dist, float)
    elapsed = np.asarray(elapsed, float)
    speed_kmh = np.asarray(speed_kmh, float)
    g = np.asarray(long_g, float)
    n = min(len(dist), len(elapsed), len(speed_kmh), len(g))
    if n < 2:
        return []
    dist, elapsed, speed_kmh, g = dist[:n], elapsed[:n], speed_kmh[:n], g[:n]
    g = boxcar(g, _smooth_window(elapsed))
    # Decelerating but below the brake threshold, and moving: the throttle-off-to-brake coast.
    coast = (g < -COAST_DRAG_MIN) & (g > -theta_b) & (speed_kmh > MOVING_KMH)
    out: list[CoastSpan] = []
    i = 0
    while i < n:
        if coast[i]:
            j0 = i
            while i + 1 < n and coast[i + 1]:
                i += 1
            j1 = i
            duration = float(elapsed[j1] - elapsed[j0])
            if duration >= MIN_COAST_S:
                out.append(CoastSpan(start_dist=float(dist[j0]), end_dist=float(dist[j1]),
                                     duration=duration))
        i += 1
    return out


def corner_grip(dist, long_g, lat_g, windows) -> list[float]:
    """Per-corner grip utilization: median(hypot(lat,long)) inside each (enter,exit) odo window
    / lap-envelope max, in (0,1]. One float per window (0.0 if empty)."""
    dist = np.asarray(dist, float)
    lg = np.asarray(long_g, float)
    la = np.asarray(lat_g, float)
    n = min(len(dist), len(lg), len(la))
    dist, lg, la = dist[:n], lg[:n], la[:n]
    gmag = np.hypot(la, lg)
    envelope_max = float(gmag.max()) if n else 0.0
    if envelope_max <= 0:
        return [0.0 for _ in windows]
    out: list[float] = []
    for d0, d1 in windows:
        idx = np.flatnonzero((dist >= d0) & (dist <= d1))
        if len(idx):
            out.append(float(np.median(gmag[idx])) / envelope_max)
        else:
            out.append(0.0)
    return out
