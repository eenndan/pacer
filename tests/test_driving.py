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
    The ripple (-0.13 g) sits between theta_b*RELEASE_RATIO (0.12) and theta_b (0.20), so it
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


def test_coasting_classification():
    """Coasting is classified where both g components sit at the floor AND speed barely changes,
    and NOT where the kart is braking, accelerating, or cornering."""
    dist, elapsed = _lap_trace(n=1000, dur=20.0)
    n = len(dist)
    long_g = np.zeros(n)
    lat_g = np.zeros(n)
    speed = np.full(n, 60.0)
    # Segment A [100:300): genuine coast (g ~ 0, speed flat).
    # Segment B [400:600): braking (long_g -0.4) -> not coast.
    long_g[400:600] = -0.4
    speed[400:600] = np.linspace(60.0, 45.0, 200)
    # Segment C [700:900): hard cornering (lat_g 0.5) -> not coast.
    lat_g[700:900] = 0.5
    th = D.derive_thresholds(long_g, lat_g, speed)
    spans = D.coasting_spans(dist, elapsed, speed, long_g, lat_g, th.theta_c, th.theta_lat)
    # The big coast span A must be present; the braking + cornering windows must NOT be coast.
    covered = np.zeros(n, dtype=bool)
    for s in spans:
        covered |= (dist >= s.start_dist) & (dist <= s.end_dist)
    a_mid = int(0.5 * (100 + 300))
    b_mid = int(0.5 * (400 + 600))
    c_mid = int(0.5 * (700 + 900))
    assert covered[a_mid], "coast segment A not detected"
    assert not covered[b_mid], "braking misclassified as coast"
    assert not covered[c_mid], "cornering misclassified as coast"
    print(f"ok coasting: {len(spans)} span(s); A coast, B brake, C corner correctly separated")


def test_coast_rejects_steady_pull():
    """A long, gentle, STEADY mild accel (g within the coast band but speed climbing steadily)
    is NOT a coast — the speed-change guard rejects it."""
    dist, elapsed = _lap_trace(n=1000, dur=20.0)
    n = len(dist)
    long_g = np.full(n, 0.02)  # within theta_c, but a real (mild) accel
    lat_g = np.zeros(n)
    speed = np.linspace(40.0, 80.0, n)  # +100% over the lap — far past COAST_MAX_SPEED_FRAC
    th = D.derive_thresholds(long_g, lat_g, speed)
    spans = D.coasting_spans(dist, elapsed, speed, long_g, lat_g, th.theta_c, th.theta_lat)
    assert spans == [], [(s.start_dist, s.duration) for s in spans]
    print("ok coast: steady mild pull rejected by the speed-change guard")


def test_corner_grip_math():
    """Grip utilization = median(|g|)/envelope_max in each window, in (0,1], higher in the
    harder corner; an empty window yields 0; a window AT the lap's peak |g| yields ~1."""
    n = 600
    dist = np.linspace(0.0, 600.0, n)
    long_g = np.zeros(n)
    lat_g = np.zeros(n)
    # Corner 1 [100:200): |g| ~ 0.3 ; Corner 2 [300:400): |g| ~ 0.6 (the hard one, = envelope).
    lat_g[100:200] = 0.3
    lat_g[300:400] = 0.6
    windows = [(dist[100], dist[199]), (dist[300], dist[399]), (590.0, 600.0)]
    grip = D.corner_grip(dist, long_g, lat_g, windows)
    assert len(grip) == 3
    assert 0.0 < grip[0] <= 1.0 and 0.0 < grip[1] <= 1.0
    assert grip[1] > grip[0], grip  # the harder corner uses more grip
    assert abs(grip[1] - 1.0) < 1e-6, grip[1]  # corner 2 IS the lap's envelope max
    assert abs(grip[0] - 0.5) < 1e-6, grip[0]  # 0.3 / 0.6
    # An empty window (past the data) -> 0.
    grip_empty = D.corner_grip(dist, long_g, lat_g, [(700.0, 800.0)])
    assert grip_empty == [0.0]
    print(f"ok grip: corner1 {grip[0]:.2f}, corner2 {grip[1]:.2f} (=1.0 envelope), empty 0")


def test_thresholds_track_distribution_and_are_floored():
    """theta_b == the MEDIAN of the BRAKING-ONLY decel of the distribution (duty-cycle
    independent), and every threshold is floored so a no-braking session can't manufacture
    events. The synthetic distribution mimics the measured D24 shape: an accel/coast bulk near
    zero plus a braking population spread roughly 0.2..0.8 g (median ~0.45 g)."""
    n = 4000
    rng = np.random.default_rng(0)
    long_g = np.abs(rng.normal(0.0, 0.15, n))        # accel/coast bulk (positive-ish, near 0)
    # ~35% of samples are braking, decel spread 0.2..0.8 g (median ~0.45) — the D24 shape.
    brake_idx = rng.choice(n, size=int(0.35 * n), replace=False)
    long_g[brake_idx] = -rng.uniform(0.2, 0.8, len(brake_idx))
    lat_g = rng.normal(0.0, 0.3, n)
    speed = np.full(n, 60.0)
    th = D.derive_thresholds(long_g, lat_g, speed)
    decel = np.maximum(-long_g, 0.0)
    braking_median = float(np.median(decel[decel > D.BRAKE_SAMPLE_FLOOR]))
    assert abs(th.theta_b - braking_median) < 1e-9, (th.theta_b, braking_median)
    assert 0.4 < th.theta_b < 0.55, th.theta_b  # ~the median of a 0.2..0.8 uniform brake spread
    assert th.theta_c >= D.THETA_C_FLOOR and th.theta_lat >= D.THETA_LAT_FLOOR
    # A session with NO braking: theta_b floors out, so brake detection finds nothing.
    calm = np.abs(rng.normal(0.0, 0.02, n))  # all (mild) accel, never braking
    th_calm = D.derive_thresholds(calm, lat_g, speed)
    assert th_calm.theta_b == D.THETA_B_FLOOR, th_calm.theta_b
    dist, elapsed = _lap_trace(n=n, dur=40.0)
    assert D.brake_events(dist, elapsed, -calm, th_calm.theta_b) == []
    print(f"ok thresholds: theta_b={th.theta_b:.3f} == median(braking decel) "
          f"{braking_median:.3f}; calm session floors out -> 0 events")


# ------------------------------------------------------------------- Session wiring
def _bare_driving_session():
    """A bare Session (tests/_synthetic idiom) with one straight lap + a seeded g-meter that
    brakes mid-lap, and the driving-channel + corner cache slots Session.__init__ creates."""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from _synthetic import bare_session

    from studio import gmeter
    from studio.session import _UNSET

    s = bare_session(valid=[0], best=0)
    n = 600
    times = 100.0 + np.linspace(0.0, 12.0, n)
    dists = np.linspace(0.0, 300.0, n)
    s._dist_cache[0] = (times, dists, times - times[0])
    s._cols_cache = {0: (times, dists.copy(), np.zeros(n), np.full(n, 16.0), dists.copy())}
    s.tt = times.copy()
    s.tv = np.full(n, 57.6)  # km/h (= 16 m/s)
    long_g = np.zeros(n)
    # One brake zone with a VARIED depth (ramps 0.2 -> 0.5 -> 0.2 g) so the braking-only median
    # (~0.35 g) sits clearly below the peak (0.5 g) — the realistic shape (a single flat-depth
    # brake would make theta_b == the peak and the strict-below test trivial/edge).
    long_g[250:330] = -np.concatenate([np.linspace(0.2, 0.5, 40), np.linspace(0.5, 0.2, 40)])
    lat_g = np.zeros(n)
    s._gmeter = gmeter.GMeter(times=times.copy(), lat_g=lat_g, long_g=long_g,
                              cross=None, source="accl")
    s._driving_thresholds_cache = _UNSET
    s._brake_events_cache = {}
    s._coasting_spans_cache = {}
    s._corner_grip_cache = {}
    s._corner_cache = _UNSET
    s._corner_stats_cache = {}
    s._corner_bests = _UNSET
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
    from _synthetic import bare_session

    from studio import gmeter
    from studio.session import _UNSET
    s = bare_session(valid=[0], best=0)
    n = 100
    times = np.linspace(0.0, 5.0, n)
    s._dist_cache[0] = (times, np.linspace(0, 100, n), times - times[0])
    s._cols_cache = {0: (times, np.linspace(0, 100, n), np.zeros(n), np.full(n, 16.0),
                         np.linspace(0, 100, n))}
    s.tt = times.copy()
    s.tv = np.full(n, 57.6)
    s._gmeter = gmeter._empty()
    s._driving_thresholds_cache = _UNSET
    s._brake_events_cache = {}
    s._coasting_spans_cache = {}
    s._corner_grip_cache = {}
    s._corner_cache = _UNSET
    s._corner_stats_cache = {}
    s._corner_bests = _UNSET
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

    from studio.map_view import _brake_glyph_size, _BrakeMarkers
    widget = pg.PlotWidget()
    bm = _BrakeMarkers(widget.getPlotItem())
    # two laps' brake sets (compare mode) -> one scatter item per non-empty lap.
    bm.set_markers([([(0.0, 0.0, 0.2), (10.0, 5.0, 0.45)], "#F5A623"),
                    ([(3.0, 3.0, 0.15)], "#7FB3D5")])
    assert len(bm._items) == 2, bm._items
    bm.set_markers([])  # clears
    assert bm._items == []
    # harder braking -> bigger glyph
    assert _brake_glyph_size(0.45) > _brake_glyph_size(0.10)
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
