"""Driving channels (F5): brake events, coasting spans, per-corner grip utilization.

PACER-FREE BY CONTRACT (numpy only). Fed by Session's per-lap arrays (the kart-frame g
series from gmeter.py + the lap's odometer / elapsed / speed) — Session owns the
caching/invalidations and the views consume the results through it, so neither this module
nor the views ever import the compiled `pacer` bindings (the studio architecture rule).

WHAT THIS DERIVES, and from WHICH validated signal
--------------------------------------------------
The only new physics here is reading the ALREADY-VALIDATED vehicle-frame g (studio/gmeter.py:
ACCL/GRAV/CORI -> kart frame, GPS-cross-checked at load) and labelling three things on it:

  * BRAKE EVENTS — the kart is decelerating hard. gmeter's sign convention is
    `long_g > 0` = accelerating, `long_g < 0` = braking, so a brake event is a contiguous
    run of `long_g < -theta_b` (a real, sustained decel — not throttle-lift coast). Onset =
    where it first crosses below `-theta_b`; the run is held open with HYSTERESIS until the
    decel recovers above `-theta_b * RELEASE_RATIO` (a Schmitt trigger so brake-pressure
    ripple right at the threshold doesn't shatter one zone into many). Each event records its
    onset (lap odometer + elapsed time), peak decel (g, a positive magnitude), and duration.

  * COASTING SPANS — neither braking nor accelerating: |long_g| < theta_c AND |lat_g| <
    theta_lat (not loaded up in a corner either) AND only a mild speed change across the span
    (guards against a long, gentle, steady-throttle pull reading as "coast"). This is the
    Circuit Tools "coasting" maths channel, but off the REAL IMU instead of a speed
    derivative.

  * PER-CORNER GRIP UTILIZATION — inside each corner window (from the F2 corner model), how
    much of the available grip the driver used: median(|g|) / envelope_max, where |g| is the
    friction-circle magnitude hypot(lat, long) and envelope_max is the lap's own peak |g|
    (its measured traction limit — the same per-lap max-G envelope the g-meter dial draws).
    A fraction in (0, 1]; higher in the hard corners. Median (not mean) so a one-sample IMU
    spike at turn-in can't inflate it, and it reflects the sustained load through the corner.

THRESHOLDS ARE DERIVED FROM THE SESSION'S OWN G DISTRIBUTION (no magic constants)
--------------------------------------------------------------------------------
Like the corner model derived its curvature threshold from the track's own |kappa|
distribution, the brake/coast thresholds come from THIS recording's measured longitudinal-g
distribution, so they self-scale to the kart, the grip level, and the IMU's noise floor:

  theta_b (brake decel threshold) = the MEDIAN of the BRAKING-ONLY decel distribution: "a real
    brake event is at least the typical brake application." Real kart braking is HARD and
    FREQUENT — the long-g signal is NOT a near-zero spike with a thin tail. MEASURED on the two
    D24 validation recordings over the moving samples (GX010060 / GX010062):
      - signed long_g: p50 +0.06 g (a slight accel bias), p25 -0.36 g, p10 -0.72 g, p1 -1.16 g;
        51% of moving samples are accelerating (>0.05 g), 40% are BRAKING (decel >0.05 g).
      - BRAKING-only decel: p10 0.12 g, p25 0.25 g, p50 ~0.46 g, p75 ~0.71 g, p90 ~0.95 g,
        max ~1.5 g.
    So theta_b = median(braking decel) lands at 0.463 g (0060) / 0.452 g (0062) — within 2.4%
    across recordings, because the median of the BRAKING side is DUTY-CYCLE INDEPENDENT (a
    percentile of ALL moving samples shifts with how much of the lap is braking; the
    braking-only median does not). At 0.46 g it marks the DECISIVE braking zones (~10 onsets
    per lap on the ~12-corner validation track), comfortably above any throttle-lift coast and
    below the deepest threshold-braking. Floored at THETA_B_FLOOR so a session with almost no
    braking cannot drive it into the noise. VALIDATED: against the independent GPS
    speed-derivative brake-onset method, the matched onsets correlate r≈1.00 in track position
    with a ~5-6 m median offset, and there are ZERO brake onsets on the full-throttle straight.

  theta_c (coast |long_g| band) / theta_lat (coast |lat_g| band): the level BELOW which the
    kart is neither braking/accelerating nor loaded in a corner. Taken as a low percentile
    (COAST_PERCENTILE) of |long_g| / |lat_g| over the moving samples, then floored. MEASURED:
    |long_g| p40 ~0.22 g, |lat_g| p40 ~0.35 g on the validation recordings — and coasting
    requires BOTH bands at once (an AND), so a sample only counts as coasting when its
    longitudinal AND lateral g are both in their low-percentile band AND the speed is barely
    changing. On the real laps this lands 1-6 short coasting spans per lap (0.3-2.9 s total) in
    the slow/transition zones, not on the hard corners or the straights.

All of these are reported by `derive_thresholds(...).describe()` (printed once at load next to
the g cross-check) so the numbers that set the thresholds on THIS session are always visible.

SMOOTHING
---------
The g series is already low-passed in gmeter (0.15 s boxcar). We add a short additional
SMOOTH_S boxcar before thresholding so a single high-frequency IMU sample can't open/close a
brake event; the corner-grip median is robust without it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import boxcar

# --- model constants -------------------------------------------------------------------
# Additional boxcar applied to long_g before brake/coast thresholding, in seconds. gmeter
# already low-passes at 0.15 s; this just guarantees a lone sample can't toggle an event.
# 0.10 s ~ a couple of samples at the 50 Hz g-series rate — below the duration of any real
# brake application, so it never smears a brake onset.
SMOOTH_S = 0.10
# theta_b is the MEDIAN of the session's BRAKING-decel distribution (the decel of just the
# samples that are actually braking, decel > BRAKE_SAMPLE_FLOOR) — "a firm brake application
# is at least the typical brake decel". The median of the BRAKING-only side is DUTY-CYCLE
# INDEPENDENT (it does not depend on what fraction of the lap is spent braking), which a
# percentile of ALL moving samples is not — that is why it is the principled choice and why it
# is stable across recordings (see derive_threshold's docstring for the measured numbers).
BRAKE_MEDIAN_PCT = 50.0
# A sample counts as "braking" (for the theta_b distribution) when its decel exceeds this small
# floor — above the straight-line long-g noise floor (~0.05 g measured), so light throttle
# modulation doesn't pollute the braking-decel median with near-zero values.
BRAKE_SAMPLE_FLOOR = 0.05
# Hard floor for theta_b (g). The braking decel threshold can never fall below this even on a
# session with almost no braking (e.g. a single gentle out-lap), so the noise floor alone can't
# manufacture brake events. ~2x the measured straight-line long-g noise floor (~0.05 g).
THETA_B_FLOOR = 0.10
# A brake event is HELD OPEN (hysteresis) until the decel recovers above
# theta_b * RELEASE_RATIO — a Schmitt trigger so brake-pressure ripple right at threshold
# doesn't fragment one braking zone into several. 0.6 matches the corner model's hysteresis.
RELEASE_RATIO = 0.6
# A detected brake run shorter than this (s) is dropped as noise — below the shortest real
# brake application; a true brake zone lasts several tenths of a second at least.
MIN_BRAKE_S = 0.20
# Percentile of |long_g| / |lat_g| (moving samples) that sets the coast bands theta_c /
# theta_lat — the noise floor the kart sits at when neither braking/accelerating nor turning.
COAST_PERCENTILE = 40.0
# Floors for the coast bands (g): a coasting band can't be tighter than the IMU floor.
THETA_C_FLOOR = 0.04
THETA_LAT_FLOOR = 0.05
# Max |speed change| across a coasting span, as a fraction of the entry speed. A span where
# speed barely moves is a true coast; a steady hard pull (constant mild accel) would otherwise
# masquerade as one, so cap the allowed drift. 0.06 = up to ~6% speed change end-to-end.
COAST_MAX_SPEED_FRAC = 0.06
# A coasting span shorter than this (s) is dropped — a blip between brake-release and
# throttle-pickup, not a real coast worth shading.
MIN_COAST_S = 0.30
# Speed (km/h) below which a sample is treated as "stopped" and excluded from the threshold
# distributions + the coast/brake search (g is dominated by manoeuvring noise at a crawl).
MOVING_KMH = 14.4  # = 4.0 m/s, matching gmeter._MOVING_MS


@dataclass(frozen=True)
class Thresholds:
    """The brake/coast thresholds derived from one session's own longitudinal/lateral-g
    distribution (all in g; magnitudes, positive). Reported once at load so the numbers that
    set the channels on THIS recording are always visible."""

    theta_b: float       # brake decel threshold: a brake event is long_g < -theta_b
    theta_c: float       # coast longitudinal band: |long_g| < theta_c
    theta_lat: float     # coast lateral band: |lat_g| < theta_lat
    n_moving: int        # moving samples the distribution was measured over
    # The measured distribution percentiles that motivate the values (for the load-time print).
    brake_p75: float
    brake_p90: float
    brake_max: float

    def describe(self) -> str:
        return (f"driving channels: theta_b={self.theta_b:.3f} g (brake), "
                f"theta_c={self.theta_c:.3f} g / theta_lat={self.theta_lat:.3f} g (coast); "
                f"over {self.n_moving} moving samples the braking decel ran "
                f"p75={self.brake_p75:.3f}, p90={self.brake_p90:.3f}, "
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
    """One coasting span within a lap (neither braking nor accelerating nor cornering),
    in that lap's own odometer space."""

    start_dist: float    # lap odometer (m) where the coast begins
    end_dist: float      # lap odometer (m) where it ends
    duration: float      # how long it lasts (s)


def _smooth_window(t: np.ndarray) -> int:
    """Samples spanning SMOOTH_S given the (roughly uniform) g-series time step."""
    if len(t) < 3:
        return 1
    dt = float(np.median(np.diff(t)))
    return max(int(round(SMOOTH_S / max(dt, 1e-9))), 1)


def derive_thresholds(long_g, lat_g, speed_kmh) -> Thresholds:
    """Derive (theta_b, theta_c, theta_lat) from a session's own g distribution over its
    MOVING samples — no magic constants (see the module doc for the measured distributions
    and the WHY of each percentile). `long_g`/`lat_g` are the kart-frame g series (gmeter
    convention: long_g>0 accel, <0 brake); `speed_kmh` is the aligned speed.

    Robust to a session with no real braking: every threshold is floored, so the noise floor
    alone can never manufacture a brake event or a degenerate (empty) coast band."""
    long_g = np.asarray(long_g, float)
    lat_g = np.asarray(lat_g, float)
    speed_kmh = np.asarray(speed_kmh, float)
    n = min(len(long_g), len(lat_g), len(speed_kmh))
    long_g, lat_g, speed_kmh = long_g[:n], lat_g[:n], speed_kmh[:n]
    moving = speed_kmh > MOVING_KMH
    if not np.any(moving):
        moving = np.ones(n, dtype=bool)  # degenerate: use all samples rather than divide by 0
    lg = long_g[moving]
    la = lat_g[moving]
    decel = np.maximum(-lg, 0.0)  # braking decel magnitude (0 when accelerating/coasting)
    # theta_b = the MEDIAN of the BRAKING-ONLY decel (duty-cycle independent, see the constant
    # + module docstring). The braking samples are those past the small BRAKE_SAMPLE_FLOOR so
    # light throttle modulation near zero doesn't drag the median down.
    braking = decel[decel > BRAKE_SAMPLE_FLOOR]
    if braking.size:
        theta_b = max(float(np.percentile(braking, BRAKE_MEDIAN_PCT)), THETA_B_FLOOR)
    else:
        theta_b = THETA_B_FLOOR  # ~no braking in the session: floor it (no false events)
    theta_c = max(float(np.percentile(np.abs(lg), COAST_PERCENTILE)), THETA_C_FLOOR)
    theta_lat = max(float(np.percentile(np.abs(la), COAST_PERCENTILE)), THETA_LAT_FLOOR)
    return Thresholds(
        theta_b=theta_b, theta_c=theta_c, theta_lat=theta_lat, n_moving=int(np.sum(moving)),
        brake_p75=float(np.percentile(braking, 75.0)) if braking.size else 0.0,
        brake_p90=float(np.percentile(braking, 90.0)) if braking.size else 0.0,
        brake_max=float(decel.max()) if decel.size else 0.0,
    )


def brake_events(dist, elapsed, long_g, theta_b: float) -> list[BrakeEvent]:
    """Detect braking zones on one lap's longitudinal-g series.

    A brake event is a contiguous run where the (smoothed) long_g drops below -theta_b,
    held open with HYSTERESIS until it recovers above -theta_b*RELEASE_RATIO (so pressure
    ripple right at threshold doesn't fragment one zone). Runs shorter than MIN_BRAKE_S are
    dropped. `dist`/`elapsed`/`long_g` are all aligned, on the SAME lap (odometer m / elapsed
    s / g, gmeter sign convention). Returns events in track order."""
    dist = np.asarray(dist, float)
    elapsed = np.asarray(elapsed, float)
    g = np.asarray(long_g, float)
    n = min(len(dist), len(elapsed), len(g))
    dist, elapsed, g = dist[:n], elapsed[:n], g[:n]
    if n < 2:
        return []
    g = boxcar(g, _smooth_window(elapsed))
    hi = float(theta_b)               # ENTER braking below -hi
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


def coasting_spans(dist, elapsed, speed_kmh, long_g, lat_g,
                   theta_c: float, theta_lat: float) -> list[CoastSpan]:
    """Detect coasting spans on one lap: contiguous runs where |long_g| < theta_c AND
    |lat_g| < theta_lat (neither braking/accelerating nor loaded in a corner), keeping only
    runs that last >= MIN_COAST_S AND over which the speed changes by less than
    COAST_MAX_SPEED_FRAC of the entry speed (a true coast, not a steady mild pull). All
    arrays are aligned, on the SAME lap. Returns spans in track order."""
    dist = np.asarray(dist, float)
    elapsed = np.asarray(elapsed, float)
    speed_kmh = np.asarray(speed_kmh, float)
    lg = np.asarray(long_g, float)
    la = np.asarray(lat_g, float)
    n = min(len(dist), len(elapsed), len(speed_kmh), len(lg), len(la))
    if n < 2:
        return []
    dist, elapsed, speed_kmh = dist[:n], elapsed[:n], speed_kmh[:n]
    lg, la = lg[:n], la[:n]
    w = _smooth_window(elapsed)
    lg = boxcar(lg, w)
    la = boxcar(la, w)
    coast = (np.abs(lg) < theta_c) & (np.abs(la) < theta_lat) & (speed_kmh > MOVING_KMH)
    out: list[CoastSpan] = []
    i = 0
    while i < n:
        if coast[i]:
            j0 = i
            while i + 1 < n and coast[i + 1]:
                i += 1
            j1 = i
            duration = float(elapsed[j1] - elapsed[j0])
            v0 = float(speed_kmh[j0])
            dv_frac = abs(float(speed_kmh[j1]) - v0) / max(v0, 1e-6)
            if duration >= MIN_COAST_S and dv_frac < COAST_MAX_SPEED_FRAC:
                out.append(CoastSpan(start_dist=float(dist[j0]), end_dist=float(dist[j1]),
                                     duration=duration))
        i += 1
    return out


def corner_grip(dist, long_g, lat_g, windows) -> list[float]:
    """Per-corner friction-circle grip utilization for one lap.

    For each (enter, exit) window (lap odometer metres), the fraction of the lap's available
    grip used in that corner: median(|g|) over the in-window samples / envelope_max, where
    |g| = hypot(lat, long) and envelope_max is the lap's own peak |g| (its measured traction
    limit). A value in (0, 1]; higher in the hard corners. Median is robust to a one-sample
    turn-in IMU spike. Returns one float per window (0.0 for a window with no samples)."""
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
