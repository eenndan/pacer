"""Pure-fake session analysis tests — the PLAN §5 wishlist pack (no telemetry file, no Qt).

Pins five math-dense invariants that had no coverage, each driven on synthetic inputs:
  * `_band_lap_ids` (studio._signal): the 'real lap' gate+median+band filter behind
    Session.valid_lap_ids and load's _band_lap_count — in-band kept, out-of-band dropped,
    plus the empty / single-lap / all-out-of-band edges. Runs on a local fake exposing only
    the read surface the function touches (laps_count / lap_time / sample_count).
  * `_clean` (studio.load): synthetic pacer.GPSSample traces through the real cleaner —
    stationary lead-in/cool-down trim, lone-teleport spike removal, off-track-box removal,
    the <10-sample passthrough, and the degenerate lo/hi fallback (mostly-stationary clip
    keeps everything). Imports pacer only.
  * `lap_sector_splits`: a lap's sector splits are all positive and SUM exactly to the lap's
    elapsed[-1] (its lap time) — the distance-projection design's headline guarantee — on a
    synthetic straight-line odometer lap with SimpleNamespace sector lines (the code reads
    only .first/.second.x/.y).
  * `sector_plot_positions`: both x-modes (distance = boundary odometer metres on the best
    lap; time = elapsed-into-best-lap at each boundary), labels/order, and the documented
    []-returns (no sector lines / no valid best lap).
  * `delta()` endpoint: on the 400-point normalized-distance grid the delta curve's LAST
    value equals laptime_lap − laptime_best in BOTH x-modes (test_compare covers
    delta_between — a separate implementation; this pins delta() itself).
Run: python tests/test_session_pure.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import bare_session, odometer  # noqa: E402

import pacer  # noqa: E402
from studio._signal import (  # noqa: E402
    LAP_BAND_HI,
    LAP_BAND_LO,
    MIN_LAP_SAMPLES,
    MIN_LAP_TIME,
    _band_lap_ids,
)
from studio.load import MIN_START_SPEED, _clean  # noqa: E402
from studio.session import Session  # noqa: E402

# ------------------------------------------------------------------ shared fakes / seeding

class _FakeBandLaps:
    """The pacer.Laps READ surface `_band_lap_ids` touches — laps_count / lap_time /
    sample_count only (the existing _FakeLaps doubles elsewhere lack sample_count)."""

    def __init__(self, times, samples=None):
        self._times = list(times)
        # Sample-rich by default so the time band is what's under test.
        self._samples = list(samples) if samples is not None else [1000] * len(self._times)

    def laps_count(self):
        return len(self._times)

    def lap_time(self, i):
        return self._times[i]

    def sample_count(self, i):
        return self._samples[i]


def _seed_cols(session, lap_id, times, dists):
    """Seed one `_cols_cache` 5-tuple (times, xs, ys, full_speed m/s, cum_distances) for a
    STRAIGHT-LINE lap along the x-axis (xs = odometer, ys = 0), so the sector-midpoint→trace
    projection geometry is consistent with the odometer the splits interpolate on."""
    times = np.asarray(times, float)
    dists = np.asarray(dists, float)
    if not hasattr(session, "_cols_cache"):  # bare Session.__new__ — the slot needs creating
        session._cols_cache = {}
    session._cols_cache[lap_id] = (
        times, dists.copy(), np.zeros_like(dists), np.gradient(dists, times), dists.copy(),
    )


def _seg(x1, y1, x2, y2):
    """A SimpleNamespace timing line — the sector code reads only .first/.second.x/.y."""
    return SimpleNamespace(first=SimpleNamespace(x=x1, y=y1),
                           second=SimpleNamespace(x=x2, y=y2))


# ---------------------------------------------------------------- 1) _signal._band_lap_ids

def test_band_lap_ids_keeps_in_band_drops_out_of_band():
    """The headline filter: laps within [LO, HI] x the median lap time survive; the long
    out-lap and the short double-crossing (both passing the basic gate) are dropped."""
    times = [60.0, 61.0, 62.0, 200.0, 25.0]  # median 61 -> band [30.5, 97.6]
    med = float(np.median(times))
    assert not (LAP_BAND_LO * med <= times[3] <= LAP_BAND_HI * med)  # 200 is out-of-band
    assert not (LAP_BAND_LO * med <= times[4] <= LAP_BAND_HI * med)  # 25 is out-of-band
    assert _band_lap_ids(_FakeBandLaps(times)) == [0, 1, 2]
    print("test_band_lap_ids_keeps_in_band_drops_out_of_band OK")


def test_band_lap_ids_basic_gate():
    """The pre-band gate: too few samples (< MIN_LAP_SAMPLES) or too short a time
    (< MIN_LAP_TIME) excludes a lap BEFORE the median, so it can't skew the band either."""
    # Lap 1 is in-band by time but sample-starved; the median is taken over laps 0+2 only.
    laps = _FakeBandLaps([60.0, 61.0, 62.0], samples=[1000, MIN_LAP_SAMPLES - 1, 1000])
    assert _band_lap_ids(laps) == [0, 2]
    # Lap 0 is shorter than MIN_LAP_TIME: dropped at the gate, the real laps survive.
    assert _band_lap_ids(_FakeBandLaps([MIN_LAP_TIME - 0.1, 60.0, 62.0])) == [1, 2]
    print("test_band_lap_ids_basic_gate OK")


def test_band_lap_ids_edges():
    """Edges: no laps at all -> []; a single lap is its own median (always in-band) -> kept;
    two far-apart laps -> the even-count median sits between them and BOTH fall outside the
    band -> [] (all-out-of-band is reachable)."""
    assert _band_lap_ids(_FakeBandLaps([])) == []
    assert _band_lap_ids(_FakeBandLaps([62.0])) == [0]
    # median(10, 100) = 55 -> band [27.5, 88.0]: 10 below, 100 above -> nothing survives.
    assert _band_lap_ids(_FakeBandLaps([10.0, 100.0])) == []
    # All laps failing the basic gate is the other route to []: `basic` is empty.
    assert _band_lap_ids(_FakeBandLaps([2.0, 3.0, 4.0])) == []
    print("test_band_lap_ids_edges OK")


# ------------------------------------------------------------------------- 2) load._clean

_LAT0, _LON0 = 44.0, 7.0  # arbitrary mid-latitude origin for the synthetic traces
_M_PER_DEG_LAT = 111_320.0


def _gps(x_m, y_m, speed):
    """A pacer.GPSSample at local offset (x_m east, y_m north) metres from the origin."""
    lat = _LAT0 + y_m / _M_PER_DEG_LAT
    lon = _LON0 + x_m / (_M_PER_DEG_LAT * math.cos(math.radians(_LAT0)))
    return pacer.GPSSample(lat=lat, lon=lon, altitude=0.0,
                           full_speed=speed, ground_speed=speed)


def _run_clean(samples):
    """Drive the real `_clean` with index-tracking spans/naive, returning the KEPT original
    indices (the naive list is seeded with the indices themselves)."""
    n = len(samples)
    spans = [(float(i), float(i) + 0.1) for i in range(n)]
    _s, _sp, kept = _clean(samples, spans, list(range(n)))
    return kept


def test_clean_short_trace_passthrough():
    """< 10 samples: returned untouched — even an all-stationary scrap is kept verbatim."""
    samples = [_gps(0.0, 0.0, 0.0) for _ in range(9)]
    assert _run_clean(samples) == list(range(9))
    print("test_clean_short_trace_passthrough OK")


def test_clean_trims_stationary_lead_in_and_cool_down():
    """Stationary head (20) + moving run (40) + stationary tail (15): only the sustained-
    moving window survives (speeds above MIN_START_SPEED for 5+ consecutive samples)."""
    head = [_gps(0.0, 0.0, 0.0) for _ in range(20)]
    moving = [_gps(5.0 * k, 0.0, MIN_START_SPEED + 7.0) for k in range(40)]
    tail = [_gps(195.0, 0.0, 0.0) for _ in range(15)]
    assert _run_clean(head + moving + tail) == list(range(20, 60))
    print("test_clean_trims_stationary_lead_in_and_cool_down OK")


def test_clean_drops_lone_teleport_spike():
    """A lone fix ~111 m off the line while its neighbours sit 10 m apart is a teleport
    glitch: dropped. Every genuine point survives (no trim — all moving)."""
    samples = [_gps(5.0 * k, 0.0, 10.0) for k in range(40)]
    samples[15] = _gps(5.0 * 15, 111.0, 10.0)  # far from BOTH neighbours; they stay close
    assert _run_clean(samples) == [i for i in range(40) if i != 15]
    print("test_clean_drops_lone_teleport_spike OK")


def test_clean_drops_off_track_box_outliers():
    """TWO consecutive fixes 5 km off-track: invisible to the spike filter (each is close to
    its far twin) but far outside the 1-99-percentile inlier box + margin -> removed. They
    are <1% of the trace so they can't drag the percentile box out to themselves."""
    genuine = [_gps(5.0 * k, 0.0, 10.0) for k in range(200)]
    samples = genuine[:50] + [_gps(250.0, 5000.0, 10.0)] * 2 + genuine[50:]
    assert _run_clean(samples) == [i for i in range(202) if i not in (50, 51)]
    print("test_clean_drops_off_track_box_outliers OK")


def test_clean_degenerate_window_keeps_everything():
    """An (almost) all-stationary clip: the trim collapses to hi - lo < 10, and the
    degenerate fallback keeps the WHOLE trace instead of returning a 1-sample stub."""
    samples = [_gps(0.0, 0.0, 0.0) for _ in range(15)]
    assert _run_clean(samples) == list(range(15))
    print("test_clean_degenerate_window_keeps_everything OK")


# ------------------------------------------------- 3+4) sector splits / plot positions

def make_sector_session():
    """A bare Session with ONE straight-line lap (200 samples, 1000 m, slow-fast-slow) and
    TWO SimpleNamespace sector lines crossing the track exactly at trace points j1 < j2
    (vertical segments whose midpoints sit ON the line y=0 at x = dists[j]). The lines are
    listed out of track order on purpose — the boundaries must come back sorted."""
    lap = 4
    times, dists = odometer(200, 0.1, 50.0, 1000.0)
    s = bare_session({lap: (times, dists)}, best=lap, valid=[lap])
    _seed_cols(s, lap, times, dists)
    j1, j2 = 60, 140
    s.laps = SimpleNamespace(
        laps_count=lambda: 5,
        sectors=SimpleNamespace(sector_lines=[
            _seg(dists[j2], -5.0, dists[j2], 5.0),   # S-boundary 2 first: must get sorted
            _seg(dists[j1], -5.0, dists[j1], 5.0),
        ]),
    )
    return s, lap, times, dists, (j1, j2)


def test_lap_sector_splits_sum_to_laptime():
    """THE distance-projection guarantee: N sector lines -> N+1 positive splits that sum
    EXACTLY to the lap's elapsed[-1] (its lap time) — no blanks, none exceeding the lap."""
    s, lap, times, dists, (j1, j2) = make_sector_session()
    splits = s.lap_sector_splits(lap)
    laptime = float(times[-1] - times[0])
    assert len(splits) == 3, splits
    assert all(sp > 0 for sp in splits), splits
    assert abs(sum(splits) - laptime) < 1e-9, (sum(splits), laptime)
    # Each split individually is the elapsed-time difference between consecutive boundaries
    # (lap start, the two projected sector distances in sorted order, lap finish).
    elapsed = times - times[0]
    t_at = np.interp([0.0, dists[j1], dists[j2], dists[-1]], dists, elapsed)
    for k in range(3):
        assert abs(splits[k] - (t_at[k + 1] - t_at[k])) < 1e-9, (k, splits[k])
    print("test_lap_sector_splits_sum_to_laptime OK")


def test_sector_plot_positions_distance_mode():
    """Distance mode: S/F at x=0 plus one entry per sector line at the boundary's odometer
    distance on the BEST lap, in track order (sorted, even though the lines were not)."""
    s, _lap, _times, dists, (j1, j2) = make_sector_session()
    positions = s.sector_plot_positions("distance")
    assert [label for label, _ in positions] == ["S/F", "S1", "S2"]
    xs = [x for _, x in positions]
    assert xs[0] == 0.0
    assert abs(xs[1] - dists[j1]) < 1e-9, (xs[1], dists[j1])
    assert abs(xs[2] - dists[j2]) < 1e-9, (xs[2], dists[j2])
    print("test_sector_plot_positions_distance_mode OK")


def test_sector_plot_positions_time_mode():
    """Time mode: the same boundaries expressed as elapsed-into-the-best-lap seconds (the
    non-uniform odometer makes time != scaled distance, so this pins the interpolation)."""
    s, _lap, times, dists, (j1, j2) = make_sector_session()
    positions = s.sector_plot_positions("time")
    assert [label for label, _ in positions] == ["S/F", "S1", "S2"]
    xs = [x for _, x in positions]
    assert xs[0] == 0.0
    assert abs(xs[1] - (times[j1] - times[0])) < 1e-9, (xs[1], times[j1] - times[0])
    assert abs(xs[2] - (times[j2] - times[0])) < 1e-9, (xs[2], times[j2] - times[0])
    # Strictly increasing along the lap, and never exceeding the lap time.
    assert xs == sorted(xs) and xs[-1] < float(times[-1] - times[0])
    print("test_sector_plot_positions_time_mode OK")


def test_sector_plot_positions_empty_returns():
    """The two documented []-returns: no sector lines placed (reset sectors clears the
    guides), and no valid best lap (caller clears the lines)."""
    s, _lap, _times, _dists, _ = make_sector_session()
    s.laps.sectors = SimpleNamespace(sector_lines=[])
    assert s.sector_plot_positions("distance") == []
    assert s.sector_plot_positions("time") == []

    s2, _lap2, _t2, _d2, _ = make_sector_session()
    s2._best_cache = None  # the "no valid laps -> no best lap" memo state
    assert s2.sector_plot_positions("distance") == []
    assert s2.sector_plot_positions("time") == []
    print("test_sector_plot_positions_empty_returns OK")


# ------------------------------------------------------------- 5) delta() endpoint

def make_delta_session():
    """A bare Session with TWO odometer laps (different lengths/profiles, B faster = best),
    both caches seeded: `_dist_cache` 3-tuples via the factory and `_cols_cache` 5-tuples
    (delta()'s _lap_arrays path reads the bulk full_speed column even when the dist cache
    hits). laps_count is the only pacer surface delta() touches -> a SimpleNamespace."""
    lap_a, lap_b = 3, 7
    ta, da = odometer(120, 0.1, 100.0, 520.0)
    tb, db = odometer(110, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s = bare_session({lap_a: (ta, da), lap_b: (tb, db)}, best=lap_b)
    _seed_cols(s, lap_a, ta, da)
    _seed_cols(s, lap_b, tb, db)
    s.laps = SimpleNamespace(laps_count=lambda: 8)
    laptime_a = float(ta[-1] - ta[0])
    laptime_b = float(tb[-1] - tb[0])
    return s, lap_a, lap_b, laptime_a, laptime_b


def test_delta_endpoint_equals_laptime_diff_both_modes():
    """delta()'s documented s=1 identity: in BOTH x-modes the delta curve's last grid value
    is exactly laptime_lap - laptime_best (the lap table's diff), on the 400-point grid."""
    s, lap_a, lap_b, laptime_a, laptime_b = make_delta_session()
    for mode in ("distance", "time"):
        best, _speed, delta = s.delta([lap_a], mode)
        assert best == lap_b
        x, dy = delta[lap_a]
        assert len(x) == len(dy) == Session._DELTA_GRID_N == 400
        assert abs(float(dy[-1]) - (laptime_a - laptime_b)) < 1e-9, (mode, dy[-1])
        # The best lap against itself ends (and stays) at zero delta.
        assert abs(float(delta[lap_b][1][-1])) < 1e-9, mode
        assert np.all(np.abs(delta[lap_b][1]) < 1e-9), mode
    print("test_delta_endpoint_equals_laptime_diff_both_modes OK")


def test_delta_x_axis_endpoints_per_mode():
    """The mode-specific x basis at s=1: distance mode ends at the BEST lap's total odometer
    (one shared axis for every lap); time mode ends at each lap's OWN lap time."""
    s, lap_a, lap_b, laptime_a, laptime_b = make_delta_session()
    best_total = float(s._dist_cache[lap_b][1][-1])

    _best, _speed, delta = s.delta([lap_a], "distance")
    assert abs(float(delta[lap_a][0][-1]) - best_total) < 1e-9
    assert abs(float(delta[lap_b][0][-1]) - best_total) < 1e-9
    dy_dist = delta[lap_a][1]

    _best, _speed, delta = s.delta([lap_a], "time")
    assert abs(float(delta[lap_a][0][-1]) - laptime_a) < 1e-9
    assert abs(float(delta[lap_b][0][-1]) - laptime_b) < 1e-9
    # Only the x basis changes between modes — the delta y-values are identical.
    assert np.allclose(delta[lap_a][1], dy_dist, atol=1e-12)
    print("test_delta_x_axis_endpoints_per_mode OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SESSION-PURE TESTS PASSED")
