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
# D3: the synthetic brake/throttle band. A SECONDARY VISUALISATION of the SAME speed-derived
# longitudinal g the brake detector runs on (NOT a new detector) — it just maps that g to a
# bounded 0..1 pedal-style intensity. Braking is normalised so g == -theta_b reads ~full brake
# (the session's own brake-threshold = "on the brakes"); throttle is normalised to a fixed accel
# envelope (karts rarely sustain > ~0.5 g of forward accel). Smoothed with the detector's boxcar
# so the band tracks the same signal the brake glyphs sit on. ESTIMATED, never measured.
THROTTLE_ENV_G = 0.50     # g; positive long-g at/above this reads ~full throttle (clip)
BRAKE_INTENSITY_FLOOR = 0.18  # g; brake decel below this reads as 0 (engine braking / lift, not a brake)
# (longitudinal-g clip MAX_LONG_G and the speed_long_g helper live in studio._signal)
# Maneuver merge: the release hysteresis splits one braking-into-a-corner (threshold brake -> ease/
# trail -> re-brake) into several events. These fuse the fragments back into ONE brake point.
# Discriminator priority is DATA-DRIVEN (measured on real GPS-derived g, _diag_merge.py): adjacent
# same-corner fragments sit within ~17 m of coast (p90) while distinct corners cluster >30 m apart,
# so DISTANCE + the corner-window guard separate them cleanly; the throttle sign does NOT (same-corner
# g blips to +0.06..+0.4 between trail sub-phases on the noisy signal), so it is only a coarse safety.
MERGE_TROUGH_GAP_M = 25.0 # m; PRIMARY cut: fuse fragments within this much coast distance of each
#                           other (same-corner p90<=17 m; distinct corners >30 m). The corner guard
#                           handles the close distinct-corner cases distance alone would over-merge.
MERGE_ACCEL_G = 0.50      # g; SAFETY only: a clear, hard re-throttle (smoothed signed g above this)
#                           between two brakes keeps them separate even inside one corner window — the
#                           fallback for a corner model that wrongly merges two real corners. Set HIGH
#                           on purpose: a low value wrongly blocks valid same-corner merges.
MERGE_GATE_S = 0.30       # s; boxcar for the merge gate's signed g (wider than the detector's SMOOTH_S)
CORNER_LEAD_M = 40.0      # m; widen corner windows upstream (braking starts before the geometry)
GRIP_ENV_PCT = 98.0       # percentile of combined |g| over the session = the grip "limit" (robust max)
GRIP_ENV_FLOOR = 0.3      # g; a session that never loaded the tyres can't make a tiny divisor
# D4: braking-point optimizer. a_max (the deceleration the optimal-brake-distance physics divides by)
# is the session's DEMONSTRATED peak braking, NOT the detection threshold theta_b: a robust high
# percentile of the per-event peak decels (g), floored so a timid session can't yield an absurdly
# tiny a_max (-> absurdly long optimal braking distance). Converted g->m/s^2 via G at use.
AMAX_PCT = 90.0           # percentile of the per-event peak decels = the session's demonstrated brake
AMAX_FLOOR_G = 0.30       # g; a_max floor — a session that never braked hard still gets a sane decel
BRAKE_MATCH_LEAD_M = 30.0 # m; a corner's matched brake event onsets within [enter-this, exit] — the
#                           brake zone starts on the straight before turn-in (mirrors coaching's window)


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


@dataclass(frozen=True)
class BrakePoint:
    """D4: one corner's braking-point comparison — where the driver actually braked vs the
    apex-speed-matched latest sustainable brake point. ESTIMATED (the optimum assumes constant-decel
    braking at the session's demonstrated peak). All distances are this lap's odometer (m)."""

    cid: int                    # the Corner.cid this row belongs to
    actual_brake_dist: float    # this lap's odometer (m) at the matched brake event's onset
    optimal_brake_dist: float   # apex_dist - braking distance d (the latest sustainable brake point)
    metres_later: float         # optimal - actual; positive => you can brake LATER
    a_max_g: float              # the session's demonstrated peak braking used (g, positive)


def estimate_a_max(peak_decels, *, floor_g: float = AMAX_FLOOR_G) -> float:
    """The session's DEMONSTRATED peak braking deceleration (g, positive) for the brake-point
    optimizer: a robust high percentile (AMAX_PCT) of the per-event peak decels, floored to
    `floor_g`. NOT the detection threshold theta_b — that is a floor for what counts as braking,
    whereas a_max is what the kart can actually do, so the optimal-brake-distance physics matches
    the driver's own demonstrated capability. An empty list -> the floor (a timid/no-brake session
    can't manufacture an absurdly small a_max and thus an absurdly long braking distance)."""
    peaks = np.asarray(list(peak_decels), float)
    peaks = peaks[np.isfinite(peaks) & (peaks > 0.0)]
    if peaks.size == 0:
        return float(floor_g)
    return max(float(np.percentile(peaks, AMAX_PCT)), float(floor_g))


def optimal_brake_distance(v_entry: float, v_apex: float, a_max: float) -> float | None:
    """The constant-decel braking distance d (m) needed to slow from `v_entry` to `v_apex` at
    `a_max`: d = (v_entry^2 - v_apex^2) / (2 * a_max). ALL SI: speeds in m/s, a_max in m/s^2.

    Defensive: returns None (no answer) when v_apex >= v_entry (no braking needed -> d <= 0) or
    a_max <= 0 (no demonstrated braking -> the physics is undefined). Otherwise a positive d."""
    if not (a_max > 0.0) or v_apex >= v_entry:
        return None
    return (float(v_entry) ** 2 - float(v_apex) ** 2) / (2.0 * float(a_max))


def _win(t: np.ndarray, seconds: float) -> int:
    """Number of samples spanning `seconds` given the (roughly uniform) time step."""
    if len(t) < 3:
        return 1
    dt = float(np.median(np.diff(t)))
    return max(int(round(seconds / max(dt, 1e-9))), 1)


def _smooth_window(t: np.ndarray) -> int:
    """Samples spanning SMOOTH_S (the detector's pre-threshold boxcar)."""
    return _win(t, SMOOTH_S)


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


def brake_events(dist, elapsed, long_g, theta_b: float, *,
                 corner_windows=None) -> list[BrakeEvent]:
    """Detect braking zones on one lap (aligned dist/elapsed/long_g, same lap; gmeter sign:
    long_g<0 braking). Held open with Schmitt hysteresis (see RELEASE_RATIO). `long_g` is the
    clean speed-derived longitudinal. Adjacent fragments of ONE braking maneuver (the release
    hysteresis splits a threshold-brake -> trail -> re-brake) are then fused by
    merge_brake_maneuvers, unless the driver got back on the throttle between them (a chicane) —
    so a corner gets one brake point. `corner_windows` (optional lap-odometer (enter,exit) spans)
    is a block-only fail-safe that keeps two genuinely-distinct corners separate. Events in
    track order."""
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
    # Collect raw fragments (no MIN_BRAKE_S yet — a short pre-onset spike must be free to fold into
    # its parent maneuver before the duration test). Each: (i0, i1, onset_dist, onset_time, peak, end_dist).
    raw: list[tuple] = []
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
            raw.append((j0, j1, float(dist[j0]), float(elapsed[j0]),
                        float(-seg.min()), float(dist[j1])))
            i = j1 + 1
        else:
            i += 1
    if not raw:
        return []
    g_gate = boxcar(g, _win(elapsed, MERGE_GATE_S))
    return merge_brake_maneuvers(raw, elapsed, g_gate, corner_windows)


def merge_brake_maneuvers(raw, elapsed, g_gate, corner_windows=None) -> list[BrakeEvent]:
    """Fuse adjacent brake fragments that belong to ONE braking maneuver. `raw` is the per-fragment
    (i0, i1, onset_dist, onset_time, peak, end_dist) list in track order; `g_gate` the merge-gate
    smoothed signed g. Two fragments stay SEPARATE iff: more than MERGE_TROUGH_GAP_M of coast
    separates them (the primary distance cut), OR (block-only guard) they sit in two different corner
    windows, OR (coarse safety) a clear hard re-throttle (smoothed g above +MERGE_ACCEL_G) sits
    between them. A merged group keeps the EARLIEST onset verbatim (so recall + onset accuracy are
    untouched), peak = max, duration = the true onset->release span; MIN_BRAKE_S is applied to the
    merged span."""
    def corner_of(d):
        if corner_windows is None:
            return None
        for k, (a, b) in enumerate(corner_windows):
            if a <= d <= b:
                return k
        return None  # out of all windows -> the guard stays inert for this event

    groups = [list(raw[0])]
    for ev in raw[1:]:
        a = groups[-1]
        if ev[0] > a[1]:  # samples between a's release and ev's onset
            between = g_gate[a[1]:ev[0] + 1]
            got_throttle = between.size > 0 and float(between.max()) > MERGE_ACCEL_G
        else:
            got_throttle = False  # overlapping/abutting fragments -> certainly one maneuver
        trough_gap = ev[2] - a[5]  # onset_dist_b - end_dist_a (coast distance between)
        ca, cb = corner_of(a[2]), corner_of(ev[2])
        block = ca is not None and cb is not None and ca != cb
        if (not got_throttle) and (trough_gap <= MERGE_TROUGH_GAP_M) and (not block):
            a[1] = ev[1]                 # extend the group's release index
            a[4] = max(a[4], ev[4])      # peak = deepest sub-phase
            a[5] = ev[5]                 # extend the group's end distance
            # onset (a[2]/a[3]) kept verbatim -> the first hard decel = the brake point
        else:
            groups.append(list(ev))
    out: list[BrakeEvent] = []
    for grp in groups:
        span = float(elapsed[grp[1]] - elapsed[grp[0]])
        if span >= MIN_BRAKE_S:
            out.append(BrakeEvent(onset_dist=grp[2], onset_time=grp[3],
                                  peak_decel=grp[4], duration=span))
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


def brake_throttle_intensity(elapsed, long_g, theta_b: float) -> np.ndarray:
    """D3: per-sample ESTIMATED brake/throttle intensity in [-1, 1] from the SAME clean
    speed-derived longitudinal g the brake detector uses (NOT a new detector) — for the chart's
    pedal-style band under the speed curve.

      * braking  (g < 0): -min(decel / theta_b, 1), so g == -theta_b ≈ -1.0 (full brake, the
        session's own brake threshold), and a sub-threshold lift/engine-brake below
        BRAKE_INTENSITY_FLOOR reads 0 (it isn't a brake).
      * throttle (g > 0): +min(accel / THROTTLE_ENV_G, 1), proportional to forward accel and
        clipped to a sane envelope so a GPS-noise spike can't exceed full.
      * near-zero g (cruise / mild coast): ~0.

    Same SMOOTH_S boxcar as the detector so the band tracks the brake glyphs. Aligned to the
    shorter of `elapsed`/`long_g`; theta_b<=0 (no g signal) -> zeros."""
    elapsed = np.asarray(elapsed, float)
    g = np.asarray(long_g, float)
    n = min(len(elapsed), len(g))
    if n < 1 or theta_b <= 0:
        return np.zeros(n)
    elapsed, g = elapsed[:n], g[:n]
    g = boxcar(g, _smooth_window(elapsed))
    out = np.zeros(n)
    brake = g < 0.0
    decel = -g[brake]
    decel = np.where(decel >= BRAKE_INTENSITY_FLOOR, decel, 0.0)
    out[brake] = -np.minimum(decel / float(theta_b), 1.0)
    accel = g > 0.0
    out[accel] = np.minimum(g[accel] / THROTTLE_ENV_G, 1.0)
    return out


def grip_envelope(long_g, lat_g, speed_kmh) -> float:
    """The session's combined-g grip limit: the GRIP_ENV_PCT percentile of hypot(lat,long) over the
    MOVING samples (robust against a one-sample IMU spike), floored. corner_grip normalizes to this —
    the car/tyre's session-wide capability — so a slow lap reads genuinely lower, unlike normalizing
    to each lap's own peak (which renders every lap ~the same)."""
    long_g = np.asarray(long_g, float)
    lat_g = np.asarray(lat_g, float)
    speed_kmh = np.asarray(speed_kmh, float)
    n = min(len(long_g), len(lat_g), len(speed_kmh))
    if n == 0:
        return GRIP_ENV_FLOOR
    gmag = np.hypot(long_g[:n], lat_g[:n])
    moving = speed_kmh[:n] > MOVING_KMH
    if not np.any(moving):
        moving = np.ones(n, dtype=bool)
    return max(float(np.percentile(gmag[moving], GRIP_ENV_PCT)), GRIP_ENV_FLOOR)


GRIP_UTIL_CLIP = 1.2          # display clip for per-sample utilization (a little over the p98 limit)
CORNER_GRIP_CLIP = 1.1        # display clip for the per-corner median grip: a corner can genuinely
#                               sit AT (~1.0) or just past the session's robust-p98 envelope, so the
#                               cap is OVER 1.0 (was a dishonest 1.0 that could never read "at the
#                               limit"). Tighter than GRIP_UTIL_CLIP because a median is far less
#                               spiky than the per-sample series, so 1.1 is ample honest headroom.


def grip_utilization(lat_g, long_g, envelope: float, *,
                     clip_max: float = GRIP_UTIL_CLIP) -> np.ndarray:
    """D5: per-sample grip utilization = hypot(lat_g, long_g) / envelope, clipped to
    [0, clip_max], for the track-map "unused grip" colouring.

    `envelope` is the SESSION g-g limit (see grip_envelope) — the same divisor corner_grip uses, so
    map colour and the per-corner numbers tell one story; it is floored to GRIP_ENV_FLOOR here too so
    a low-load session can't make a tiny divisor inflate utilization. ~1.0 = on the session's grip
    limit, < 1.0 = grip left UNUSED, > 1.0 (up to clip_max) = a transient peak above the robust p98.

    Pure numpy. Aligned to the shorter of lat_g/long_g; ESTIMATED — lateral g is validated (r~0.9)
    but the combined magnitude mixes in the noisier longitudinal axis, so it is lateral-dominant by
    construction, never a precise friction-circle reading. NaN where either input is non-finite (the
    map skips those segments)."""
    lat = np.asarray(lat_g, float)
    lon = np.asarray(long_g, float)
    n = min(len(lat), len(lon))
    lat, lon = lat[:n], lon[:n]
    env = max(float(envelope), GRIP_ENV_FLOOR)
    util = np.hypot(lat, lon) / env
    util = np.clip(util, 0.0, float(clip_max))
    # carry non-finite inputs through as NaN so the map's bucketize skips those segments
    util[~(np.isfinite(lat) & np.isfinite(lon))] = np.nan
    return util


def corner_grip(dist, long_g, lat_g, windows, envelope: float, *,
                clip_max: float = CORNER_GRIP_CLIP) -> list[float]:
    """Per-corner grip utilization: median(hypot(lat,long)) inside each (enter,exit) odo window
    / the SESSION grip `envelope` (see grip_envelope), clamped to [0, clip_max]. Normalizing to the
    session envelope rather than this lap's own peak makes the values comparable across laps — a slow
    lap reads lower. The ceiling is OVER 1.0 (CORNER_GRIP_CLIP) so a corner genuinely AT or just past
    the session's robust-p98 envelope reads ~100%+ honestly, instead of being capped at a misleading
    1.0 that can never say "you're at the limit". One float per window (0.0 if empty).

    `long_g`/`lat_g` are the SAME validated axes the envelope is built from (clean speed-derived
    longitudinal + IMU lateral; see DrivingChannels._lap_g_arrays / _grip_envelope), so numerator
    and divisor share one friction circle and the value is unbiased."""
    dist = np.asarray(dist, float)
    lg = np.asarray(long_g, float)
    la = np.asarray(lat_g, float)
    n = min(len(dist), len(lg), len(la))
    dist, lg, la = dist[:n], lg[:n], la[:n]
    gmag = np.hypot(la, lg)
    if envelope <= 0 or n == 0:
        return [0.0 for _ in windows]
    out: list[float] = []
    for d0, d1 in windows:
        idx = np.flatnonzero((dist >= d0) & (dist <= d1))
        if len(idx):
            out.append(min(float(np.median(gmag[idx])) / envelope, float(clip_max)))
        else:
            out.append(0.0)
    return out
