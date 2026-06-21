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

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import driving as D  # noqa: E402


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


def test_corner_grip_math():
    """Grip utilization = median(|g|) in each window / the SESSION envelope, clamped to [0,1];
    higher in the harder corner; an empty window -> 0; a window at/above the envelope clamps to 1."""
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
    assert abs(grip[1] - 1.0) < 1e-6, grip[1]   # 0.6 / 0.6 (at the envelope)
    assert grip[2] == 0.0                         # empty window
    # A corner that loads BEYOND the envelope clamps to 1.0 (never > 1).
    assert D.corner_grip(dist, long_g, lat_g, [(dist[300], dist[399])], 0.3) == [1.0]
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
    th = s.driving_thresholds()
    assert th is not None and th is s.driving_thresholds(), "thresholds must cache"
    events = s.lap_brake_events(0)
    assert len(events) == 1, events
    assert s.lap_brake_events(0) is events, "brake events must cache per lap"
    # onset at index 250/600 over 300 m -> ~125 m.
    assert abs(events[0].onset_dist - 125.0) < 5.0, events[0].onset_dist
    # map markers map the onset to the lap's (x,y) (straight lap: x == odometer).
    markers = s.lap_brake_map_markers(0)
    assert len(markers) == 1
    assert abs(markers[0][0] - events[0].onset_dist) < 1e-6 and markers[0][2] == events[0].peak_decel
    # plot positions: distance mode scales by best distance (== this lap's, so identity here).
    pos_d = s.lap_brake_plot_positions(0, "distance")
    assert len(pos_d) == 1 and abs(pos_d[0][0] - events[0].onset_dist) < 1e-6
    pos_t = s.lap_brake_plot_positions(0, "time")
    assert abs(pos_t[0][0] - events[0].onset_time) < 1e-9
    # coasting spans exist (the flat-throttle stretches before/after the brake).
    spans = s.lap_coasting_spans(0)
    assert len(spans) >= 1 and s.lap_coasting_spans(0) is spans
    print(f"ok session: brake @ {events[0].onset_dist:.0f} m, "
          f"{len(spans)} coast span(s), markers+positions consistent")


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
    assert s.driving_thresholds() is None
    assert s.lap_brake_events(0) == []
    assert s.lap_coasting_spans(0) == []
    assert s.lap_corner_grip(0) == []
    assert s.lap_brake_map_markers(0) == []
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


def test_corner_table_has_grip_column():
    _qapp()
    from studio import corners as C
    from studio.lap_table import CORNER_COLUMNS, CornerTable
    assert CORNER_COLUMNS[-1] == "Grip", CORNER_COLUMNS  # header abbreviated; units now in tooltip

    class Stub:
        def lap_count(self):
            return 4

        def corners(self):
            return [C.Corner(cid=1, enter=0, exit=10, apex=5, direction=1, turn_deg=90)]

        def lap_corner_stats(self, i):
            return [C.CornerStat(cid=1, time=2.0, delta=0.0, apex_speed=40.0,
                                 apex_speed_delta=0.0, apex_dist=5.0, entry_speed=60.0,
                                 exit_speed=55.0)]

        def corner_session_bests(self):
            return [2.0]

        def lap_corner_grip(self, i):
            return [0.73]

    t = CornerTable(Stub())
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

    class Stub:
        def lap_count(self):
            return 4

        def corners(self):
            return [C.Corner(cid=1, enter=0, exit=10, apex=5, direction=1, turn_deg=90)]

        def lap_corner_stats(self, i):
            return [C.CornerStat(cid=1, time=2.0, delta=0.0, apex_speed=40.0,
                                 apex_speed_delta=0.0, apex_dist=5.0, entry_speed=60.0,
                                 exit_speed=55.0)]

        def corner_session_bests(self):
            return [2.0]

        def lap_corner_grip(self, i):
            return []

    t = CornerTable(Stub())
    t.set_lap(1)
    assert t.table.item(0, len(CORNER_COLUMNS) - 1).text() == "–"
    print("ok corner table: Grip cell dashes when no g signal")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
