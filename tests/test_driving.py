"""Tests for studio.driving (F5): brake events, coasting spans, per-corner grip utilization,
and the distribution-derived thresholds — all on SYNTHETIC g traces (no media file, fast,
deterministic), plus the Session wiring + the offscreen UI overlays.

Why synthetic: the real cross-check (the ACCL-derived brake onsets vs the independent GPS
speed-derivative method, on the recording) is validated by the orchestrator on the D24 files
and reported in the PR; these unit tests instead pin the pure detection math — a known brake
pulse is found at the right place, a flat-throttle stretch yields NONE, coasting is classified
where g is at the floor, the grip fraction is in (0,1] and ranks with corner load, and the
thresholds track the synthetic distribution — so a regression is caught without a 12 GB file.

Run: python tests/test_driving.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import driving as D  # noqa: E402
from studio._signal import G  # noqa: E402


# --------------------------------------------------------------- synthetic g traces
def _lap_trace(n=1000, dur=20.0, total_dist=400.0):
    """A uniform (dist, elapsed) lap: elapsed 0..dur, odometer 0..total_dist, both monotonic.
    Constant speed keeps dist linear in time so a brake placed at a sample index lands at a
    known distance/time — the detection assertions read those back."""
    elapsed = np.linspace(0.0, dur, n)
    dist = np.linspace(0.0, total_dist, n)
    return dist, elapsed


# Detection tests pass an EXPLICIT theta_b (0.20 g) so the brake-DETECTION logic is isolated
# from the threshold-DERIVATION logic (tested separately below) — the way the real pipeline
# splits the two (Session derives once, brake_events takes the threshold).
THETA_B = 0.20


def test_known_brake_pulse_is_detected():
    """A single rectangular brake pulse (long_g = -0.4 g over a known index window) is detected
    as exactly one event at the right onset distance/time, with the right peak decel + duration,
    and NO event on the same trace with the brake removed (flat throttle)."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[400:520] = -0.4  # a brake from index 400..519 (~0.4 g, above the 0.20 g threshold)
    events = D.brake_events(dist, elapsed, g, THETA_B)
    assert len(events) == 1, events
    e = events[0]
    # onset distance/time: index 400 of 1000 over 400 m / 20 s -> ~160 m, ~8 s (within smoothing).
    assert abs(e.onset_dist - 160.0) < 5.0, e.onset_dist
    assert abs(e.onset_time - 8.0) < 0.2, e.onset_time
    assert abs(e.peak_decel - 0.4) < 0.02, e.peak_decel
    # duration ~ 120 samples over 20 s / 1000 = ~2.4 s.
    assert abs(e.duration - 2.4) < 0.2, e.duration

    # Flat throttle (no brake): zero false positives.
    flat = np.full(n, 0.05)  # gentle steady accel, never below -theta_b
    assert D.brake_events(dist, elapsed, flat, THETA_B) == []
    print(f"ok brake: 1 event @ {events[0].onset_dist:.0f} m, "
          f"peak {events[0].peak_decel:.2f} g; flat throttle -> 0")


def test_brake_hysteresis_merges_one_zone():
    """A brake with a brief mid-zone ripple back toward zero (but staying in the lo band) stays
    ONE event (Schmitt-trigger hysteresis), not two — the headline reason for the lo/hi split.
    The ripple (-0.13 g) sits between theta_b*RELEASE_RATIO (0.07) and theta_b (0.20), so it
    must NOT release the event."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[400:520] = -0.4
    g[455:465] = -0.13  # toward release but still inside the lo (release) band -> one event
    events = D.brake_events(dist, elapsed, g, THETA_B)
    assert len(events) == 1, [(e.onset_dist, e.duration) for e in events]
    print(f"ok hysteresis: ripple kept one zone (dur {events[0].duration:.2f} s)")


def test_short_blip_is_rejected():
    """A brake shorter than MIN_BRAKE_S is dropped as noise (a 2-sample spike)."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[500:502] = -0.5  # ~0.04 s at 50 Hz-ish — below MIN_BRAKE_S
    assert D.brake_events(dist, elapsed, g, THETA_B) == []
    print("ok short blip rejected")


def test_trail_brake_merges_to_one_point():
    """One braking maneuver split by the hysteresis (hard brake -> ease above the release band, no
    throttle -> re-brake, close together) fuses to ONE event at the FIRST onset (the brake point)."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[200:260] = -0.45             # threshold brake
    g[260:280] = -0.03             # ease (above release band -theta_b*0.35 -> splits the raw event)
    g[280:340] = -0.30             # re-brake, ~8 m later (well within MERGE_TROUGH_GAP_M)
    events = D.brake_events(dist, elapsed, g, THETA_B)
    assert len(events) == 1, [(e.onset_dist, e.peak_decel) for e in events]
    assert abs(events[0].onset_dist - dist[200]) < 5.0, events[0].onset_dist  # onset = first hard decel
    assert abs(events[0].peak_decel - 0.45) < 0.02, events[0].peak_decel       # peak = deepest sub-phase
    print(f"ok merge: trail-brake fused to 1 @ {events[0].onset_dist:.0f} m, peak {events[0].peak_decel:.2f} g")


def test_chicane_throttle_squirt_stays_two():
    """Two genuine brake points with a clear hard re-throttle between them (a chicane) stay TWO —
    the throttle-sign safety (smoothed g above +MERGE_ACCEL_G) blocks the merge."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[200:260] = -0.40
    g[260:300] = 0.60              # a clear throttle squirt (> MERGE_ACCEL_G=0.50)
    g[300:360] = -0.40
    events = D.brake_events(dist, elapsed, g, THETA_B)
    assert len(events) == 2, [(e.onset_dist) for e in events]
    print("ok merge: chicane with a throttle squirt stayed 2 points")


def test_corner_guard_blocks_back_to_back_corners():
    """Two brakes close together with NO throttle between (distance gate alone would merge) stay
    TWO when they sit in DIFFERENT corner windows — the block-only corner guard."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[200:240] = -0.40            # onset ~80 m
    g[250:290] = -0.40            # onset ~100 m, ~8 m of coast between, no throttle
    windows = [(70.0, 95.0), (96.0, 120.0)]  # the two onsets fall in different corners
    merged = D.brake_events(dist, elapsed, g, THETA_B)                       # no guard -> merges
    guarded = D.brake_events(dist, elapsed, g, THETA_B, corner_windows=windows)
    assert len(merged) == 1, "without the guard the close no-throttle pair should merge"
    assert len(guarded) == 2, "the corner guard must keep two distinct corners separate"
    print("ok merge: corner guard kept back-to-back corners separate (1 -> 2 with windows)")


def test_corner_guard_inert_outside_windows():
    """Two close no-throttle brakes OUTSIDE all corner windows still merge — the guard never wrongly
    blocks (the out-of-window fail-safe; regression for the guard-hole)."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[200:240] = -0.40
    g[250:290] = -0.40
    far = [(1000.0, 1100.0)]  # both onsets are outside this window -> guard inert
    events = D.brake_events(dist, elapsed, g, THETA_B, corner_windows=far)
    assert len(events) == 1, "out-of-window events must be decided by the gates, not blocked"
    print("ok merge: guard inert outside windows (close pair still merged)")


def test_distance_keeps_far_brakes_separate():
    """Two brakes far apart (more than MERGE_TROUGH_GAP_M of coast) stay TWO on distance alone."""
    dist, elapsed = _lap_trace()
    n = len(dist)
    g = np.zeros(n)
    g[100:160] = -0.40
    g[400:460] = -0.40   # ~96 m later, far beyond MERGE_TROUGH_GAP_M
    events = D.brake_events(dist, elapsed, g, THETA_B)
    assert len(events) == 2, [(e.onset_dist) for e in events]
    print("ok merge: far-apart brakes stayed separate on the distance cut")


def test_coasting_classification():
    """Coasting is the off-power band: DECELERATING from drag (COAST_DRAG_MIN < decel < theta_b)
    while moving — NOT where the kart is braking or accelerating."""
    dist, elapsed = _lap_trace(n=1000, dur=20.0)
    n = len(dist)
    long_g = np.zeros(n)
    speed = np.full(n, 60.0)
    # Segment A [100:300): genuine coast (gentle ~0.06 g decel, below the brake threshold).
    long_g[100:300] = -0.06
    # Segment B [400:600): braking (decel 0.4 g, above theta_b) -> not coast.
    long_g[400:600] = -0.4
    # Segment C [700:900): accelerating (long_g +0.2) -> not coast (not decelerating).
    long_g[700:900] = 0.2
    th = D.derive_thresholds(long_g, speed)
    spans = D.coasting_spans(dist, elapsed, speed, long_g, th.theta_b)
    covered = np.zeros(n, dtype=bool)
    for s in spans:
        covered |= (dist >= s.start_dist) & (dist <= s.end_dist)
    a_mid, b_mid, c_mid = int(0.5 * (100 + 300)), int(0.5 * (400 + 600)), int(0.5 * (700 + 900))
    assert covered[a_mid], "coast segment A not detected"
    assert not covered[b_mid], "braking misclassified as coast"
    assert not covered[c_mid], "acceleration misclassified as coast"
    print(f"ok coasting: {len(spans)} span(s); A coast, B brake, C accel correctly separated")


def test_coast_rejects_steady_pull():
    """A steady mild ACCEL (speed climbing) is not coasting — coast requires the car to be
    decelerating off-power, not on the throttle."""
    dist, elapsed = _lap_trace(n=1000, dur=20.0)
    n = len(dist)
    long_g = np.full(n, 0.05)   # mild but real acceleration (positive longitudinal g)
    speed = np.linspace(40.0, 80.0, n)
    th = D.derive_thresholds(long_g, speed)
    spans = D.coasting_spans(dist, elapsed, speed, long_g, th.theta_b)
    assert spans == [], [(s.start_dist, s.duration) for s in spans]
    print("ok coast: a steady mild pull (accelerating) is not coasting")


def test_brake_throttle_intensity_band():
    """D3: the synthetic brake/throttle band maps the SAME speed-derived long-g to a bounded
    [-1,1] pedal intensity — hard brake -> strongly negative, on-power -> positive, cruise/
    sub-floor lift -> ~0, clipped to the envelope, length == the lap."""
    dist, elapsed = _lap_trace(n=1000, dur=20.0)
    n = len(dist)
    g = np.zeros(n)
    g[300:420] = -0.40           # hard brake (above theta_b) -> strong negative
    g[600:720] = 0.30            # clear acceleration -> positive throttle
    g[800:880] = -0.10           # sub-floor lift/engine braking (< BRAKE_INTENSITY_FLOOR) -> ~0
    inten = D.brake_throttle_intensity(elapsed, g, THETA_B)
    assert len(inten) == n, len(inten)
    assert inten.min() >= -1.0 and inten.max() <= 1.0, (inten.min(), inten.max())
    assert inten[360] < -0.8, inten[360]                      # mid brake reads ~full brake
    assert inten[660] > 0.4, inten[660]                       # mid throttle reads positive
    assert abs(inten[0]) < 1e-9                               # cruise is zero
    assert abs(inten[840]) < 1e-9, inten[840]                 # a lift below the floor is not a brake
    # A brake DEEPER than theta_b still clips to -1 (the envelope), never beyond.
    deep = np.zeros(n)
    deep[300:420] = -1.5
    assert D.brake_throttle_intensity(elapsed, deep, THETA_B).min() == -1.0
    # No g signal (theta_b <= 0) -> all zeros.
    assert np.all(D.brake_throttle_intensity(elapsed, g, 0.0) == 0.0)
    print("ok brake/throttle band: hard brake strong-, on-power +, lift/cruise 0, clipped to [-1,1]")


def test_corner_grip_math():
    """Grip utilization = median(|g|) in each window / the SESSION envelope, clamped to
    [0, CORNER_GRIP_CLIP]; higher in the harder corner; an empty window -> 0; a corner AT the
    envelope reads ~1.0 (the honest "at the limit"); a corner well past it clamps at the ceiling."""
    n = 600
    dist = np.linspace(0.0, 600.0, n)
    long_g = np.zeros(n)
    lat_g = np.zeros(n)
    # Corner 1 [100:200): |g| ~ 0.3 ; Corner 2 [300:400): |g| ~ 0.6.
    lat_g[100:200] = 0.3
    lat_g[300:400] = 0.6
    windows = [(dist[100], dist[199]), (dist[300], dist[399]), (590.0, 600.0)]
    env = 0.6  # the session grip envelope (passed in, not this lap's own peak)
    grip = D.corner_grip(dist, long_g, lat_g, windows, env)
    assert len(grip) == 3
    assert abs(grip[0] - 0.5) < 1e-6, grip[0]   # 0.3 / 0.6
    assert abs(grip[1] - 1.0) < 1e-6, grip[1]   # 0.6 / 0.6 (AT the envelope -> honest ~100%)
    assert grip[2] == 0.0                         # empty window
    # HONESTY: a corner just at/over the robust-p98 envelope is NOT capped at a misleading 1.0 — the
    # ceiling is CORNER_GRIP_CLIP (>1) so the value can read "at the limit" (~100%) and a hair over.
    over = D.corner_grip(dist, long_g, lat_g, [(dist[300], dist[399])], 0.3)  # 0.6/0.3 = 2.0
    assert over == [D.CORNER_GRIP_CLIP] and D.CORNER_GRIP_CLIP > 1.0, over
    # A corner driven exactly AT the envelope reads ~1.0 (no longer pre-capped below it).
    at_limit = D.corner_grip(dist, long_g, lat_g, [(dist[300], dist[399])], 0.6)
    assert abs(at_limit[0] - 1.0) < 1e-9, at_limit
    # The cross-lap win: the SAME corner driven slower (less g) reads LOWER against the SAME
    # session envelope — lap-self-normalization (the old metric) could not show this.
    slow = lat_g.copy()
    slow[300:400] = 0.4
    g_fast = D.corner_grip(dist, long_g, lat_g, [(dist[300], dist[399])], env)[0]
    g_slow = D.corner_grip(dist, long_g, slow, [(dist[300], dist[399])], env)[0]
    assert g_slow < g_fast, (g_slow, g_fast)
    print(f"ok grip: 0.3->{grip[0]:.2f}, 0.6->{grip[1]:.2f} (clamped), empty 0, slower corner lower")


def test_grip_envelope_is_session_robust_and_floored():
    """grip_envelope = p98 of combined g over MOVING samples, floored — robust to a lone IMU spike
    and to a session that never loaded the tyres."""
    n = 2000
    rng = np.random.default_rng(1)
    long_g = rng.normal(0.0, 0.2, n)
    lat_g = rng.normal(0.0, 0.5, n)
    speed = np.full(n, 60.0)
    env = D.grip_envelope(long_g, lat_g, speed)
    assert abs(env - np.percentile(np.hypot(long_g, lat_g), D.GRIP_ENV_PCT)) < 1e-9
    long_g[0] = 50.0  # a single huge spike must NOT blow up the envelope (p98, not max)
    assert D.grip_envelope(long_g, lat_g, speed) < 5.0
    calm = np.full(n, 0.01)  # a calm session floors out
    assert D.grip_envelope(calm, calm, speed) == D.GRIP_ENV_FLOOR
    print(f"ok grip envelope: p98 robust max = {env:.2f} g, spike-proof, floored when calm")


def test_grip_utilization_per_sample():
    """D5: per-sample utilization = hypot(lat,long)/envelope, clipped to [0, GRIP_UTIL_CLIP].
    A sample AT the envelope reads ~1.0; below reads <1; way above clips; non-finite -> NaN."""
    env = 1.0
    # lat-only samples (long = 0) at fractions/above the envelope.
    lat = np.array([0.0, 0.5, 1.0, 1.5, 10.0])
    lon = np.zeros_like(lat)
    util = D.grip_utilization(lat, lon, env)
    assert util[0] == 0.0
    assert abs(util[1] - 0.5) < 1e-9          # 0.5 / 1.0 -> grip left UNUSED
    assert abs(util[2] - 1.0) < 1e-9          # AT the envelope -> ~1.0 (on the limit)
    assert abs(util[3] - 1.2) < 1e-9          # 1.5 clipped to GRIP_UTIL_CLIP
    assert util[4] == D.GRIP_UTIL_CLIP        # a huge transient clamps, never blows up the scale
    # combined magnitude: a (lat, long) at the envelope on the circle reads ~1.0.
    on = D.grip_utilization([0.6], [0.8], 1.0)  # hypot = 1.0
    assert abs(on[0] - 1.0) < 1e-9
    print(f"ok grip util: 0.5->{util[1]:.2f} (unused), 1.0->{util[2]:.2f} (limit), "
          f"clipped at {D.GRIP_UTIL_CLIP}")


def test_grip_utilization_floored_divisor_and_nan():
    """A tiny/zero envelope is FLOORED to GRIP_ENV_FLOOR (mirror corner_grip) so a low-load session
    can't make a divisor that inflates utilization to infinity; non-finite inputs pass through NaN
    so the map skips those segments."""
    lat = np.array([0.3, 0.3])
    lon = np.array([0.0, 0.0])
    # envelope below the floor must be replaced by the floor (0.3) -> util == 1.0, not blown up.
    util = D.grip_utilization(lat, lon, 1e-6)
    assert abs(util[0] - (0.3 / D.GRIP_ENV_FLOOR)) < 1e-9 and util[0] <= D.GRIP_UTIL_CLIP
    # NaN / inf in either axis -> NaN (skipped by the map's bucketize).
    nan_util = D.grip_utilization([0.5, np.nan, 0.5], [0.0, 0.0, np.inf], 1.0)
    assert not np.isfinite(nan_util[1]) and not np.isfinite(nan_util[2])
    assert np.isfinite(nan_util[0])
    # mismatched lengths align to the shorter input.
    assert len(D.grip_utilization([0.1, 0.2, 0.3], [0.0, 0.0], 1.0)) == 2
    print(f"ok grip util: floored divisor (env<{D.GRIP_ENV_FLOOR} -> floor), NaN passthrough")


def test_thresholds_physical_and_floored():
    """theta_b is PHYSICAL: a low percentile of the session's braking-only decel, clamped to
    [BRAKE_G_FLOOR, BRAKE_G_CEIL] so the same brake reads consistently and a no-braking session
    can't manufacture events. The distribution mimics the measured D24 shape (braking 0.2..0.8 g)."""
    n = 4000
    rng = np.random.default_rng(0)
    long_g = np.abs(rng.normal(0.0, 0.15, n))        # accel/coast bulk (positive-ish, near 0)
    brake_idx = rng.choice(n, size=int(0.35 * n), replace=False)
    long_g[brake_idx] = -rng.uniform(0.2, 0.8, len(brake_idx))
    speed = np.full(n, 60.0)
    th = D.derive_thresholds(long_g, speed)
    decel = np.maximum(-long_g, 0.0)
    braking = decel[decel > D.BRAKE_SAMPLE_FLOOR]
    expected = float(np.clip(np.percentile(braking, D.BRAKE_ADAPT_PCT),
                             D.BRAKE_G_FLOOR, D.BRAKE_G_CEIL))
    assert abs(th.theta_b - expected) < 1e-9, (th.theta_b, expected)
    assert D.BRAKE_G_FLOOR <= th.theta_b <= D.BRAKE_G_CEIL, th.theta_b
    # A session with NO braking: theta_b floors out, so brake detection finds nothing.
    calm = np.abs(rng.normal(0.0, 0.02, n))  # all (mild) accel, never braking
    th_calm = D.derive_thresholds(calm, speed)
    assert th_calm.theta_b == D.BRAKE_G_FLOOR, th_calm.theta_b
    dist, elapsed = _lap_trace(n=n, dur=40.0)
    assert D.brake_events(dist, elapsed, -calm, th_calm.theta_b) == []
    print(f"ok thresholds: theta_b={th.theta_b:.3f} g (physical, clamped); "
          f"calm session floors to {D.BRAKE_G_FLOOR} -> 0 events")


# ------------------------------------------------------- D4 braking-point optimizer (physics)
def test_optimal_brake_distance_exact_known_case():
    """d = (v_entry^2 - v_apex^2) / (2*a_max), SI units. A known case: 30 -> 20 m/s at 5 m/s^2
    needs (900 - 400) / 10 = 50 m."""
    d = D.optimal_brake_distance(30.0, 20.0, 5.0)
    assert d is not None and abs(d - 50.0) < 1e-9, d
    # symmetric textbook check: 40 -> 0 m/s at 8 m/s^2 -> 1600/16 = 100 m
    assert abs(D.optimal_brake_distance(40.0, 0.0, 8.0) - 100.0) < 1e-9
    print(f"ok D4 physics: 30->20 m/s @ 5 m/s^2 = {d:.1f} m (exact)")


def test_optimal_brake_distance_guards():
    """v_apex >= v_entry (no braking needed) -> None; a_max <= 0 (no demonstrated braking) -> None;
    equal speeds -> None (d would be 0)."""
    assert D.optimal_brake_distance(20.0, 25.0, 5.0) is None  # apex faster than entry
    assert D.optimal_brake_distance(20.0, 20.0, 5.0) is None  # equal -> no braking
    assert D.optimal_brake_distance(30.0, 20.0, 0.0) is None  # no a_max
    assert D.optimal_brake_distance(30.0, 20.0, -1.0) is None  # negative a_max
    print("ok D4 physics: guards (apex>=entry, a_max<=0) -> None")


def test_estimate_a_max_percentile_and_floor():
    """a_max = the AMAX_PCT percentile of the per-event peak decels (g), floored — the session's
    DEMONSTRATED peak braking, not the detection threshold."""
    peaks = [0.40, 0.45, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.90]
    expected = max(float(np.percentile(peaks, D.AMAX_PCT)), D.AMAX_FLOOR_G)
    assert abs(D.estimate_a_max(peaks) - expected) < 1e-9, D.estimate_a_max(peaks)
    # a TIMID session (all tiny decels) floors out so the braking distance can't blow up.
    assert D.estimate_a_max([0.05, 0.08, 0.10]) == D.AMAX_FLOOR_G
    # empty / no braking -> the floor (no division by ~0).
    assert D.estimate_a_max([]) == D.AMAX_FLOOR_G
    # non-finite / non-positive peaks are ignored, not poisoning the percentile.
    assert abs(D.estimate_a_max([np.nan, 0.0, -0.3, 0.6, 0.7]) - max(
        float(np.percentile([0.6, 0.7], D.AMAX_PCT)), D.AMAX_FLOOR_G)) < 1e-9
    print(f"ok D4 a_max: p{D.AMAX_PCT:.0f} of peaks (floored {D.AMAX_FLOOR_G}); timid floors out")


def test_brake_point_metres_later_sign():
    """metres_later = optimal - actual, positive when the driver can brake LATER. With a_max derived
    from the demonstrated peaks, an optimum AHEAD of the actual onset is a positive metres-later."""
    # demonstrated a_max from a hard session: p90 of these is well above the floor.
    a_max_g = D.estimate_a_max([0.8, 0.85, 0.9, 0.95, 1.0])
    a_max_ms2 = a_max_g * G
    v_entry, v_apex = 30.0, 18.0  # m/s
    d = D.optimal_brake_distance(v_entry, v_apex, a_max_ms2)
    apex_dist = 200.0
    optimal = apex_dist - d
    # driver braked 10 m BEFORE the optimum -> can brake ~10 m later (positive).
    actual_early = optimal - 10.0
    assert (optimal - actual_early) > 0
    # driver braked 10 m AFTER the optimum -> should brake earlier (negative).
    actual_late = optimal + 10.0
    assert (optimal - actual_late) < 0
    print(f"ok D4 sign: a_max {a_max_g:.2f} g, d={d:.1f} m, early=+later late=-later")


# ------------------------------------------------------------------- Session wiring
def _bare_driving_session():
    """A bare Session (tests/_synthetic idiom) with one straight lap + a seeded g-meter that
    brakes mid-lap, and the driving-channel + corner caches reset through the F1 services."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import bare_session, reset_corner_caches, reset_driving_caches

    from studio import gmeter

    s = bare_session(valid=[0], best=0)
    n = 600
    times = 100.0 + np.linspace(0.0, 12.0, n)
    dists = np.linspace(0.0, 300.0, n)
    # Brake/coast now read the SPEED-derived longitudinal (not the IMU), so the synthetic must
    # move the speed: a hard brake mid-lap, then a brief gentle off-power coast.
    speed = np.full(n, 16.0)                       # m/s
    speed[250:330] = np.linspace(16.0, 10.0, 80)   # hard brake (~0.38 g) -> onset at ~125 m
    speed[330:430] = np.linspace(10.0, 9.0, 100)   # gentle off-power coast (~0.05 g)
    speed[430:] = 9.0
    s._dist_cache[0] = (times, dists, times - times[0])
    s._cols_cache = {0: (times, dists.copy(), np.zeros(n), speed.copy(), dists.copy())}
    s.tt = times.copy()
    s.tv = speed * 3.6  # km/h
    # A g meter is still required (the channels gate on a g signal), but its long_g is no longer
    # used for brake/coast; lat_g feeds grip only.
    s._gmeter = gmeter.GMeter(times=times.copy(), lat_g=np.zeros(n), long_g=np.zeros(n),
                              cross=None, source="accl")
    # F1: the driving + corner caches live in the DrivingChannels / CornerModel services now;
    # reset them through the service-aware helpers (the raw slots moved off Session).
    reset_driving_caches(s)
    reset_corner_caches(s)
    return s


def test_session_driving_accessors_and_caching():
    s = _bare_driving_session()
    th = s.driving.thresholds()
    assert th is not None and th is s.driving.thresholds(), "thresholds must cache"
    events = s.driving.lap_brake_events(0)
    assert len(events) == 1, events
    assert s.driving.lap_brake_events(0) is events, "brake events must cache per lap"
    # onset at index 250/600 over 300 m -> ~125 m.
    assert abs(events[0].onset_dist - 125.0) < 5.0, events[0].onset_dist
    # map markers map the onset to the lap's (x,y) (straight lap: x == odometer).
    markers = s.driving.lap_brake_map_markers(0)
    assert len(markers) == 1
    assert abs(markers[0][0] - events[0].onset_dist) < 1e-6 and markers[0][2] == events[0].peak_decel
    # plot positions: distance mode scales by best distance (== this lap's, so identity here).
    pos_d = s.driving.lap_brake_plot_positions(0, "distance")
    assert len(pos_d) == 1 and abs(pos_d[0][0] - events[0].onset_dist) < 1e-6
    pos_t = s.driving.lap_brake_plot_positions(0, "time")
    assert abs(pos_t[0][0] - events[0].onset_time) < 1e-9
    # coasting spans exist (the flat-throttle stretches before/after the brake).
    spans = s.driving.lap_coasting_spans(0)
    assert len(spans) >= 1 and s.driving.lap_coasting_spans(0) is spans
    # D3: the synthetic brake/throttle band — per-sample [-1,1], caches, strong brake at the onset.
    dists_bt, _elapsed_bt, inten = s.driving.lap_brake_throttle(0)
    assert s.driving.lap_brake_throttle(0)[2] is inten, "brake/throttle must cache per lap"
    assert len(inten) == len(dists_bt) and inten.min() >= -1.0 and inten.max() <= 1.0
    onset_idx = int(np.argmin(np.abs(dists_bt - events[0].onset_dist)))
    assert inten[onset_idx + 10] < -0.5, inten[onset_idx + 10]  # braking reads strongly negative
    # plot projection: distance mode scales by best (==self here); time mode is elapsed.
    px_d, iy_d = s.driving.lap_brake_throttle_plot(0, "distance")
    assert px_d is not None and len(px_d) == len(iy_d)
    px_t, _iy_t = s.driving.lap_brake_throttle_plot(0, "time")
    assert px_t is not None and abs(px_t[0]) < 1e-6  # elapsed starts at 0
    print(f"ok session: brake @ {events[0].onset_dist:.0f} m, "
          f"{len(spans)} coast span(s), brake/throttle band [-1,1], markers+positions consistent")


def test_session_grip_channel_aligned_and_cached():
    """D5 wiring: lap_grip_channel returns one per-sample utilization value per MAP xy point
    (lap_channels length), caches per lap, and degrades to None with no g signal."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import reset_driving_caches

    from studio import gmeter

    s = _bare_driving_session()
    # Seed a real lateral-g load on the same media clock the lap g-arrays interpolate onto, so the
    # utilization is non-trivial; long stays 0 (lateral-dominant, the validated channel).
    n = len(s._gmeter.times)
    lat = np.zeros(n)
    lat[250:330] = 0.5  # a loaded corner mid-lap
    s._gmeter = gmeter.GMeter(times=s._gmeter.times.copy(), lat_g=lat, long_g=np.zeros(n),
                              cross=None, source="accl")
    reset_driving_caches(s)

    util = s.driving.lap_grip_utilization(0)
    assert util is not None
    # aligned 1:1 to the lap's map xy points (the _lap_columns grid lap_channels' x_m comes from).
    _t, xs, _ys, _v, _cum = s._lap_columns(0)
    assert len(util) == len(xs), (len(util), len(xs))
    assert s.driving.lap_grip_utilization(0) is util, "grip channel must cache per lap"
    # the loaded stretch reads higher utilization than the unloaded straight.
    assert float(np.nanmax(util)) > float(util[0]) + 1e-3
    assert float(np.nanmax(util)) <= D.GRIP_UTIL_CLIP

    # no g signal -> None (graceful degrade), like the map's no-g path.
    s2 = _bare_driving_session()
    s2._gmeter = gmeter.GMeter(times=np.empty(0), lat_g=np.empty(0), long_g=np.empty(0),
                               cross=None, source="accl")
    reset_driving_caches(s2)
    assert s2.driving.lap_grip_utilization(0) is None
    print(f"ok D5 session: grip channel len {len(util)} aligned to xy, cached, "
          f"max {float(np.nanmax(util)):.2f}; no-g -> None")


def test_grip_envelope_uses_clean_not_raw_longitudinal():
    """HONESTY FIX: the friction-circle envelope (the grip DIVISOR) must be built from the CLEAN
    GPS-derived longitudinal (gm.long_g_gps), NOT the vibration-inflated raw IMU gm.long_g. A raw
    long_g blown far past the clean one used to inflate the divisor and bias EVERY grip reading
    systematically low. Here raw and clean differ hugely; the envelope must follow the clean axis."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import reset_driving_caches

    from studio import gmeter

    s = _bare_driving_session()
    n = len(s._gmeter.times)
    times = s._gmeter.times.copy()
    lat = np.zeros(n)
    lat[250:330] = 0.5  # a real lateral load (the validated axis)
    raw_long = np.zeros(n)
    raw_long[100:500] = 1.8  # the IMU forward axis grossly vibration-inflated (~1.8 g of "noise")
    clean_long = np.zeros(n)  # the CLEAN GPS-derived longitudinal: essentially no forward g here
    s._gmeter = gmeter.GMeter(times=times, lat_g=lat, long_g=raw_long, cross=None,
                              source="accl", long_g_gps=clean_long)
    reset_driving_caches(s)

    env = s.driving._grip_envelope()
    # The clean friction circle is just the lateral 0.5 g (long ~0) -> p98 ~0.5 g, floored at 0.3.
    # If the RAW long (1.8 g) had leaked into the divisor, hypot(0.5,1.8) ~1.87 -> env ~1.8.
    assert env < 0.7, f"envelope {env:.3f} g — raw inflated long_g leaked into the grip divisor"
    # And it equals the envelope computed directly from the CLEAN axes (numerator==divisor axes).
    speed_kmh = np.interp(times, s.tt, s.tv)
    expect = D.grip_envelope(clean_long, lat, speed_kmh)
    assert abs(env - expect) < 1e-9, (env, expect)
    print(f"ok grip envelope: clean-axis divisor = {env:.2f} g (NOT the raw 1.8 g IMU long)")


def test_session_brake_points_accessor_and_caching():
    """D4 wiring: lap_brake_points matches the lap's brake event to a seeded corner and reports the
    apex-speed-matched optimum vs the actual onset, derived from the session's DEMONSTRATED a_max
    (not theta_b). The numbers are pinned by construction; the accessor caches per lap."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import reset_corner_caches

    from studio.corners import Corner

    s = _bare_driving_session()
    # Seed ONE corner whose [enter, exit] window contains both the brake onset (~125 m) and the
    # apex (the slowest sample, ~9 m/s near the end). The basis frame == this lap's odometer.
    corner = Corner(cid=1, enter=120.0, exit=260.0, apex=190.0, direction=1, turn_deg=90.0)
    reset_corner_caches(s, basis=([corner], 300.0))

    events = s.driving.lap_brake_events(0)
    assert len(events) == 1, events
    bps = s.driving.lap_brake_points(0)
    assert len(bps) == 1, bps
    bp = bps[0]
    assert bp.cid == 1
    assert s.driving.lap_brake_points(0) is bps, "brake points must cache per lap"

    # a_max is the DEMONSTRATED peak (single event -> its own peak decel), NOT the threshold theta_b.
    th = s.driving.thresholds()
    assert bp.a_max_g > th.theta_b, (bp.a_max_g, th.theta_b)
    assert abs(bp.a_max_g - events[0].peak_decel) < 1e-6, (bp.a_max_g, events[0].peak_decel)

    # The actual onset matches the detected brake event's onset.
    assert abs(bp.actual_brake_dist - events[0].onset_dist) < 1e-6

    # The optimum reproduces the physics exactly: apex_dist - (v_entry^2 - v_apex^2)/(2*a_max).
    st = s.corners.lap_corner_stats(0)[0]
    dists, speed_kmh, _e = s._lap_arrays(0)
    v_entry = float(np.interp(bp.actual_brake_dist, dists, speed_kmh)) / 3.6
    v_apex = float(st.apex_speed) / 3.6
    d = D.optimal_brake_distance(v_entry, v_apex, bp.a_max_g * G)
    assert d is not None
    expected_optimal = float(st.apex_dist) - d
    assert abs(bp.optimal_brake_dist - expected_optimal) < 1e-6, (bp.optimal_brake_dist,
                                                                  expected_optimal)
    assert abs(bp.metres_later - (bp.optimal_brake_dist - bp.actual_brake_dist)) < 1e-9
    print(f"ok D4 session: C1 actual {bp.actual_brake_dist:.0f} m, optimal "
          f"{bp.optimal_brake_dist:.0f} m, metres_later {bp.metres_later:+.1f} (a_max "
          f"{bp.a_max_g:.2f} g > theta_b {th.theta_b:.2f})")


def test_session_brake_points_na_when_no_brake_in_corner():
    """A corner with NO brake event in its [enter-lead, exit] window is omitted (N/A) — the brake is
    far from this corner."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import reset_corner_caches

    from studio.corners import Corner

    s = _bare_driving_session()
    # corner near the START (before the brake onset ~125 m, even past the lead) -> no matched brake.
    corner = Corner(cid=1, enter=10.0, exit=40.0, apex=25.0, direction=1, turn_deg=90.0)
    reset_corner_caches(s, basis=([corner], 300.0))
    assert s.driving.lap_brake_points(0) == [], s.driving.lap_brake_points(0)
    print("ok D4 session: corner with no braking -> N/A (omitted)")


def test_session_no_gmeter_degrades_to_empty():
    """With no g signal (empty meter), every driving accessor returns [] and the thresholds are
    None — the channels degrade gracefully (a recording with no IMU + no GPS fallback)."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import bare_session, reset_corner_caches, reset_driving_caches

    from studio import gmeter
    s = bare_session(valid=[0], best=0)
    n = 100
    times = np.linspace(0.0, 5.0, n)
    s._dist_cache[0] = (times, np.linspace(0, 100, n), times - times[0])
    s._cols_cache = {0: (times, np.linspace(0, 100, n), np.zeros(n), np.full(n, 16.0),
                         np.linspace(0, 100, n))}
    s.tt = times.copy()
    s.tv = np.full(n, 57.6)
    s._gmeter = gmeter._empty()
    reset_driving_caches(s)
    reset_corner_caches(s)
    assert s.driving.thresholds() is None
    assert s.driving.lap_brake_events(0) == []
    assert s.driving.lap_coasting_spans(0) == []
    assert s.driving.lap_corner_grip(0) == []
    assert s.driving.lap_brake_map_markers(0) == []
    assert s.driving.lap_brake_throttle(0) == (None, None, None)
    assert s.driving.lap_brake_throttle_plot(0, "distance") == (None, None)
    assert s.driving.lap_brake_points(0) == []  # D4: no g -> no a_max -> no brake points
    print("ok no-g: all driving channels empty, thresholds None")


# ----------------------------------------------------------------------- UI (offscreen)
def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_map_brake_markers_overlay():
    _qapp()
    import pyqtgraph as pg

    from studio.map_view import _BrakeMarkers
    from studio.theme import brake_glyph_size
    widget = pg.PlotWidget()
    bm = _BrakeMarkers(widget.getPlotItem())
    # two laps' brake sets (compare mode) -> one scatter item per non-empty lap.
    bm.set_markers([([(0.0, 0.0, 0.2), (10.0, 5.0, 0.45)], "#F5A623"),
                    ([(3.0, 3.0, 0.15)], "#7FB3D5")])
    assert len(bm._items) == 2, bm._items
    bm.set_markers([])  # clears
    assert bm._items == []
    # harder braking -> bigger glyph
    assert brake_glyph_size(0.45) > brake_glyph_size(0.10)
    print("ok map overlay: one scatter per lap, sizes ramp with decel, clears cleanly")


def test_plots_brake_and_coast_overlays():
    _qapp()
    from studio.plots_view import PlotsView

    class FakeSession:
        def best_lap_id(self):
            return 0

        def has_reference(self):  # F7: no cross-recording reference here — dormant baseline
            return False

        def lap_time(self, i):
            return 70.0

        def delta(self, ids, x_mode="distance"):
            sx = np.linspace(0.0, 200.0, 100)
            return 0, {0: (sx, np.full(100, 60.0))}, {0: (sx, np.zeros(100))}

        def sector_plot_positions(self, m):
            return []

    pv = PlotsView(FakeSession())
    pv.set_laps([0])
    pv.set_brake_markers([([(50.0, 0.4)], "#F5A623")])
    pv.set_coasting_spans([([(100.0, 140.0)], "#F5A623")])
    assert len(pv._brake_items) == 1, pv._brake_items
    assert len(pv._coast_items) == 1, pv._coast_items
    # the glyph rides the speed curve: y == the (flat 60) speed at x=50.
    spot = pv._brake_items[0].points()[0]
    assert abs(spot.pos().y() - 60.0) < 1e-6, spot.pos().y()
    # a selection refresh re-pushes from cached data without leaking items.
    pv.refresh()
    assert len(pv._brake_items) == 1 and len(pv._coast_items) == 1
    pv.set_brake_markers([])
    pv.set_coasting_spans([])
    assert pv._brake_items == [] and pv._coast_items == []
    print("ok plots overlay: brake glyph rides the curve, coast band drawn, survives refresh")


def test_plots_brake_throttle_band_toggle():
    """D3: the brake/throttle band is OFF by default (no items) even when data is pushed; turning
    the toggle on draws the sub-track and turning it off clears it. Survives a selection refresh."""
    _qapp()
    from studio.plots_view import PlotsView

    class FakeSession:
        def best_lap_id(self):
            return 0

        def has_reference(self):
            return False

        def lap_time(self, i):
            return 70.0

        def delta(self, ids, x_mode="distance"):
            sx = np.linspace(0.0, 200.0, 100)
            return 0, {0: (sx, np.full(100, 60.0))}, {0: (sx, np.zeros(100))}

        def sector_plot_positions(self, m):
            return []

    pv = PlotsView(FakeSession())
    pv.set_laps([0])
    xs = np.linspace(0.0, 200.0, 100)
    inten = np.zeros(100)
    inten[20:40] = -0.9   # braking
    inten[60:80] = 0.5    # throttle
    pv.set_brake_throttle([(xs, inten)])
    assert pv._brake_throttle_items == [], "band must stay off until toggled on"
    pv.brake_throttle_btn.setChecked(True)  # toggled -> _draw_brake_throttle
    assert pv._show_brake_throttle and len(pv._brake_throttle_items) > 0
    pv.refresh()  # a selection refresh re-pins the band on the fitted axes
    assert len(pv._brake_throttle_items) > 0
    pv.brake_throttle_btn.setChecked(False)
    assert not pv._show_brake_throttle and pv._brake_throttle_items == []
    print("ok plots overlay: brake/throttle band off by default, toggles on/off, survives refresh")


def test_corner_table_has_grip_column():
    _qapp()
    from studio import corners as C
    from studio import theme
    from studio.lap_table import CORNER_COLUMNS, CornerTable
    # The grip column carries the shared ESTIMATED marker (honest: it's an inferred friction-circle
    # estimate, not a measured percentage) — "Grip (est)", units/meaning in the header tooltip.
    assert CORNER_COLUMNS[-1] == theme.estimated_label("Grip") == "Grip (est)", CORNER_COLUMNS

    stub = SimpleNamespace(
        lap_count=lambda: 4,
        corners=SimpleNamespace(
            corner_list=lambda: [C.Corner(cid=1, enter=0, exit=10, apex=5, direction=1, turn_deg=90)],
            lap_corner_stats=lambda i: [C.CornerStat(cid=1, time=2.0, delta=0.0, apex_speed=40.0,
                                                     apex_speed_delta=0.0, apex_dist=5.0,
                                                     entry_speed=60.0, exit_speed=55.0)],
            corner_session_bests=lambda: [2.0],
        ),
        driving=SimpleNamespace(lap_corner_grip=lambda i: [0.73]),
    )
    t = CornerTable(stub)
    t.set_lap(1)
    assert t.table.columnCount() == len(CORNER_COLUMNS)
    assert t.table.item(0, len(CORNER_COLUMNS) - 1).text() == "73"
    print("ok corner table: Grip % column populated (0.73 -> '73')")


def test_corner_table_grip_dash_without_g():
    """When there's no g signal, lap_corner_grip returns [] and the Grip cell shows a dash —
    the Corners view still works on a session without IMU/GPS-fallback g."""
    _qapp()
    from studio import corners as C
    from studio.lap_table import CORNER_COLUMNS, CornerTable

    stub = SimpleNamespace(
        lap_count=lambda: 4,
        corners=SimpleNamespace(
            corner_list=lambda: [C.Corner(cid=1, enter=0, exit=10, apex=5, direction=1, turn_deg=90)],
            lap_corner_stats=lambda i: [C.CornerStat(cid=1, time=2.0, delta=0.0, apex_speed=40.0,
                                                     apex_speed_delta=0.0, apex_dist=5.0,
                                                     entry_speed=60.0, exit_speed=55.0)],
            corner_session_bests=lambda: [2.0],
        ),
        driving=SimpleNamespace(lap_corner_grip=lambda i: []),
    )
    t = CornerTable(stub)
    t.set_lap(1)
    assert t.table.item(0, len(CORNER_COLUMNS) - 1).text() == "–"
    print("ok corner table: Grip cell dashes when no g signal")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
