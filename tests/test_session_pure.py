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
  * theoretical / rolling best (F1-roadmap): `session_best_splits` is the per-column min and
    `theoretical_best` its EXACT sum (with the documented no-sectors degenerate == best lap
    time); `best_rolling_lap` finds a known faster straddling window on a two-lap session,
    excludes windows spanning a GPS-dropout lap, and degrades to the best complete lap.
Run: python tests/test_session_pure.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import bare_session, odometer, seed_cols, seed_lap  # noqa: E402

import pacer  # noqa: E402
from studio._signal import (  # noqa: E402
    LAP_BAND_HI,
    LAP_BAND_LO,
    MIN_LAP_SAMPLES,
    MIN_LAP_TIME,
    _band_lap_ids,
)
from studio.load import MIN_START_SPEED, _clean, _sustained_moving  # noqa: E402
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


def test_gate_quality_drops_nonfinite_position_and_speed():
    """A2: garbage / truncated GPMF can yield NaN/inf lat/lon/speed; the quality gate drops
    those up front so they never reach the cleaner's percentile/distance math or the C++
    geometry. Finite sentinel-quality samples (no fix/dop fields) are still kept."""
    from studio._signal import _gate_quality
    good = [_gps(float(i), 0.0, 10.0) for i in range(4)]
    bad = [
        pacer.GPSSample(lat=float("nan"), lon=0.0, altitude=0.0,
                        full_speed=10.0, ground_speed=10.0),
        pacer.GPSSample(lat=0.0, lon=float("inf"), altitude=0.0,
                        full_speed=10.0, ground_speed=10.0),
        pacer.GPSSample(lat=0.0, lon=0.0, altitude=0.0,
                        full_speed=float("nan"), ground_speed=0.0),
    ]
    samples = [good[0], bad[0], good[1], bad[1], good[2], bad[2], good[3]]
    n = len(samples)
    spans = [(float(i), float(i) + 0.1) for i in range(n)]
    s2, sp2, kept = _gate_quality(samples, spans, list(range(n)))
    assert kept == [0, 2, 4, 6], kept     # only the finite samples survive
    assert len(s2) == len(sp2) == 4
    print("test_gate_quality_drops_nonfinite_position_and_speed OK")


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


def test_sustained_moving_finds_trailing_window():
    """Regression for the historical `hi - run` off-by-one: the run's window is
    samples[i .. i+run-1], so the LAST in-range candidate is i = hi - run — a run of exactly
    `run` moving samples ending flush at hi must be found, not fall through to `return lo`.
    Pinned directly on `_sustained_moving`: through `_clean` (the only caller) this trailing
    case is masked — lo = n - run leaves hi - lo = run < 10, so the degenerate fallback keeps
    everything either way (see test_clean_trailing_moving_run_keeps_everything)."""
    stationary = [_gps(0.0, 0.0, 0.0) for _ in range(7)]
    moving = [_gps(5.0 * k, 0.0, MIN_START_SPEED + 7.0) for k in range(5)]
    samples = stationary + moving  # the ONLY 5-run starts at index 7 == len - 5 == hi - run
    assert _sustained_moving(samples, 0, len(samples), run=5) == 7
    # An interior run is unaffected: the first qualifying start wins as before.
    samples2 = stationary + moving + stationary
    assert _sustained_moving(samples2, 0, len(samples2), run=5) == 7
    # No qualifying run anywhere still falls back to lo.
    assert _sustained_moving(stationary, 0, len(stationary), run=5) == 0
    print("test_sustained_moving_finds_trailing_window OK")


def test_clean_trailing_moving_run_keeps_everything():
    """The trailing-run trace through the full `_clean`: lo = n - 5 makes the kept window
    hi - lo = 5 < 10, so the degenerate fallback keeps the whole trace — the same kept range
    the pre-fix code produced (its fall-through lo = 0 kept [0, n) directly). Pins that the
    off-by-one fix cannot change `_clean`'s output."""
    stationary = [_gps(0.0, 0.0, 0.0) for _ in range(7)]
    moving = [_gps(5.0 * k, 0.0, MIN_START_SPEED + 7.0) for k in range(5)]
    assert _run_clean(stationary + moving) == list(range(12))
    print("test_clean_trailing_moving_run_keeps_everything OK")


# ------------------------------------------------- 3+4) sector splits / plot positions

def make_sector_session():
    """A bare Session with ONE straight-line lap (200 samples, 1000 m, slow-fast-slow) and
    TWO SimpleNamespace sector lines crossing the track exactly at trace points j1 < j2
    (vertical segments whose midpoints sit ON the line y=0 at x = dists[j]). The lines are
    listed out of track order on purpose — the boundaries must come back sorted."""
    lap = 4
    times, dists = odometer(200, 0.1, 50.0, 1000.0)
    s = bare_session({lap: (times, dists)}, best=lap, valid=[lap])
    seed_cols(s, lap, times, dists)
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
    seed_cols(s, lap_a, ta, da)
    seed_cols(s, lap_b, tb, db)
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


# ------------------------------------- 6) theoretical best + best rolling lap (F1-roadmap)

def make_two_lap_sector_session():
    """A bare Session with TWO straight-line laps (different totals/profiles, B faster = best)
    and TWO sector lines both laps cross — the real `lap_sector_splits` projection feeds
    `session_best_splits`, so the expected column minima come from the same per-lap splits."""
    lap_a, lap_b = 3, 7
    ta, da = odometer(120, 0.1, 100.0, 520.0)
    tb, db = odometer(110, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s = bare_session({lap_a: (ta, da), lap_b: (tb, db)}, best=lap_b, valid=[lap_a, lap_b])
    seed_cols(s, lap_a, ta, da)
    seed_cols(s, lap_b, tb, db)
    s.laps = SimpleNamespace(sectors=SimpleNamespace(sector_lines=[
        _seg(350.0, -5.0, 350.0, 5.0),   # out of track order on purpose (sorted downstream)
        _seg(150.0, -5.0, 150.0, 5.0),
    ]))
    return s, lap_a, lap_b


def test_session_best_splits_is_column_min_of_lap_splits():
    """`session_best_splits` == the per-column MINIMUM of the (already-pinned)
    `lap_sector_splits` across the valid laps — the same values the table paints purple."""
    s, lap_a, lap_b = make_two_lap_sector_session()
    sp_a, sp_b = s.lap_sector_splits(lap_a), s.lap_sector_splits(lap_b)
    assert len(sp_a) == len(sp_b) == 3
    expected = [min(a, b) for a, b in zip(sp_a, sp_b, strict=True)]
    assert s.session_best_splits() == expected, (s.session_best_splits(), expected)
    print("test_session_best_splits_is_column_min_of_lap_splits OK")


def test_theoretical_best_is_exact_sum_of_best_splits():
    """`theoretical_best` is the EXACT float sum of `session_best_splits` (the purple cells),
    and — both laps' splits being real sums to their lap times — it is ≤ the best lap time."""
    s, lap_a, lap_b = make_two_lap_sector_session()
    bests = s.session_best_splits()
    th = s.theoretical_best()
    assert th == float(sum(bests)), (th, bests)
    best_laptime = float(min(sum(s.lap_sector_splits(lap_a)), sum(s.lap_sector_splits(lap_b))))
    assert th <= best_laptime + 1e-12, (th, best_laptime)
    print("test_theoretical_best_is_exact_sum_of_best_splits OK")


def test_theoretical_best_no_sectors_degenerates_to_best_lap():
    """The documented no-sector-lines choice: one sub-sector per lap (its lap time), so
    `session_best_splits` is the one-column [best lap time] and theoretical == best lap time."""
    s, lap_a, lap_b = make_two_lap_sector_session()
    s.laps.sectors = SimpleNamespace(sector_lines=[])
    laptimes = [float(sum(s.lap_sector_splits(lid))) for lid in (lap_a, lap_b)]
    bests = s.session_best_splits()
    assert len(bests) == 1 and abs(bests[0] - min(laptimes)) < 1e-9, (bests, laptimes)
    assert abs(s.theoretical_best() - min(laptimes)) < 1e-9
    print("test_theoretical_best_no_sectors_degenerates_to_best_lap OK")


# --------------------------------------------- 6b) ideal-lap envelope / Δ-to-ideal (D1)

def make_ideal_session():
    """A bare Session of THREE clean laps with CROSSING pace so the per-distance lower envelope
    is genuinely synthetic (no single lap is fastest everywhere). All three are seeded into both
    caches and marked valid + clean, so `consistency_lap_ids` (valid ∧ no-dropout) returns them.

    Lap A is fast early / slow late, lap C is slow early / fast late, lap B is middling. The
    ideal envelope therefore takes A's early segment and C's late one — strictly below every
    lap's elapsed at the cross-over region. The 0.1 s sample step is well under gapfill's 0.35 s
    gap threshold, so `lap_has_dropout` is False for all three."""
    la, lb, lc = 2, 5, 9
    ta, da = odometer(120, 0.1, 100.0, 1000.0, lambda u: 2.2 - 1.4 * np.sin(u / 2))      # fast→slow
    tb, db = odometer(118, 0.1, 300.0, 1000.0)                                            # middling
    tc, dc = odometer(122, 0.1, 500.0, 1000.0, lambda u: 0.8 + 1.4 * np.sin(u / 2))      # slow→fast
    s = bare_session({la: (ta, da), lb: (tb, db), lc: (tc, dc)},
                     best=lb, valid=[la, lb, lc])
    for lid, (t, d) in ((la, (ta, da)), (lb, (tb, db)), (lc, (tc, dc))):
        seed_cols(s, lid, t, d)
    s.laps = SimpleNamespace(laps_count=lambda: 10)
    return s, (la, lb, lc)


def test_ideal_envelope_le_every_clean_lap_at_each_grid_point():
    """The ideal envelope ≤ every clean lap's elapsed at EVERY one of the 400 grid points — the
    defining lower-envelope property (the made-monotonic step only raises a momentary dip back to
    a value still ≤ the running pointwise min, so the bound holds), and it is non-decreasing,
    starting at 0."""
    s, ids = make_ideal_session()
    env = s.ideal_lap_elapsed()
    assert env is not None and len(env) == Session._DELTA_GRID_N == 400
    assert abs(float(env[0])) < 1e-9                              # starts at 0
    assert np.all(np.diff(env) >= -1e-12)                        # non-decreasing (valid elapsed)
    s_grid = np.linspace(0.0, 1.0, Session._DELTA_GRID_N)
    for lid in ids:
        dist, _spd, elapsed = s._lap_arrays(lid)
        lap_on_grid = np.interp(s_grid, dist / dist[-1], elapsed)
        assert np.all(env <= lap_on_grid + 1e-9), lid
    # Genuinely synthetic (crossing pace): the envelope matches no single lap everywhere — each
    # lap is strictly slower than the ideal somewhere — so the ideal isn't a copy of one lap.
    for lid in ids:
        dist, _spd, elapsed = s._lap_arrays(lid)
        lap_on_grid = np.interp(s_grid, dist / dist[-1], elapsed)
        assert np.any(env < lap_on_grid - 1e-6), lid
    print("test_ideal_envelope_le_every_clean_lap_at_each_grid_point OK")


def test_ideal_total_le_every_clean_lap_time_and_eq_envelope_end():
    """`ideal_total` == the envelope's last value and ≤ every clean lap's time (a lower envelope
    can't end above the fastest lap)."""
    s, ids = make_ideal_session()
    env = s.ideal_lap_elapsed()
    assert abs(s.ideal_total() - float(env[-1])) < 1e-12
    for lid in ids:
        laptime = float(s._lap_arrays(lid)[2][-1])
        assert s.ideal_total() <= laptime + 1e-9, lid
    print("test_ideal_total_le_every_clean_lap_time_and_eq_envelope_end OK")


def test_delta_to_ideal_nonneg_and_endpoint_is_laptime_minus_ideal():
    """`delta_to_ideal` ≥ 0 at every grid point (a lap can't beat the envelope it formed), in
    BOTH x-modes, and dy at s=1 ≈ lap_time − ideal_total. Even the fastest lap's Δ-to-ideal is
    ≥ 0 and its endpoint equals its (small, ≥0) margin over the synthetic ideal."""
    s, ids = make_ideal_session()
    ideal_total = s.ideal_total()
    for mode in ("distance", "time"):
        series = s.delta_to_ideal(list(ids), mode)
        assert series is not None
        for lid in ids:
            x, dy = series[lid]
            assert len(x) == len(dy) == 400
            assert np.all(dy >= -1e-9), (mode, lid, float(dy.min()))
            laptime = float(s._lap_arrays(lid)[2][-1])
            assert abs(float(dy[-1]) - (laptime - ideal_total)) < 1e-9, (mode, lid)
    # The fastest clean lap still has a ≥0 (and finite) margin over the ideal.
    fastest = min(ids, key=lambda i: float(s._lap_arrays(i)[2][-1]))
    end = float(s.delta_to_ideal([fastest], "distance")[fastest][1][-1])
    assert end >= -1e-9
    print("test_delta_to_ideal_nonneg_and_endpoint_is_laptime_minus_ideal OK")


def test_ideal_delta_to_best_nonpositive_shares_best_axis():
    """`ideal_delta_to_best` (the ideal drawn on delta()'s own Δ-to-best axis) is ≤ 0 everywhere
    (the ideal is at least as fast as the best lap it contains) and ends at ideal_total −
    best_time. Distance mode's x ends at the active baseline total; time mode's at the best lap's
    own time."""
    s, (la, lb, lc) = make_ideal_session()
    best_time = float(s._lap_arrays(lb)[2][-1])  # lb is the seeded best
    ideal_total = s.ideal_total()
    for mode in ("distance", "time"):
        x, dy = s.ideal_delta_to_best(mode)
        assert len(x) == len(dy) == 400
        assert np.all(dy <= 1e-9), (mode, float(dy.max()))
        assert abs(float(dy[-1]) - (ideal_total - best_time)) < 1e-9, mode
    x_dist, _ = s.ideal_delta_to_best("distance")
    assert abs(float(x_dist[-1]) - s.active_baseline_total_distance()) < 1e-9
    x_time, _ = s.ideal_delta_to_best("time")
    assert abs(float(x_time[-1]) - best_time) < 1e-9
    print("test_ideal_delta_to_best_nonpositive_shares_best_axis OK")


def test_ideal_single_clean_lap_equals_that_lap():
    """Edge case: ONE clean lap → the ideal envelope IS that lap (its elapsed resampled onto the
    grid), ideal_total == its lap time, and its own Δ-to-ideal is ~0 throughout."""
    lap = 4
    t, d = odometer(120, 0.1, 50.0, 900.0)
    s = bare_session({lap: (t, d)}, best=lap, valid=[lap])
    seed_cols(s, lap, t, d)
    s.laps = SimpleNamespace(laps_count=lambda: 5)
    s_grid = np.linspace(0.0, 1.0, Session._DELTA_GRID_N)
    dist, _spd, elapsed = s._lap_arrays(lap)
    lap_on_grid = np.interp(s_grid, dist / dist[-1], elapsed)
    env = s.ideal_lap_elapsed()
    assert np.allclose(env, lap_on_grid, atol=1e-9)
    assert abs(s.ideal_total() - float(elapsed[-1])) < 1e-9
    _x, dy = s.delta_to_ideal([lap], "distance")[lap]
    assert np.all(np.abs(dy) < 1e-9)
    print("test_ideal_single_clean_lap_equals_that_lap OK")


def test_ideal_none_without_clean_laps():
    """No clean lap (no valid ids) → every ideal accessor returns None (and the renderer no-ops),
    not a crash."""
    s = bare_session(valid=[])
    s.laps = SimpleNamespace(laps_count=lambda: 0)
    assert s.ideal_lap_elapsed() is None
    assert s.ideal_total() is None
    assert s.delta_to_ideal([0], "distance") is None
    assert s.ideal_delta_to_best("distance") is None
    print("test_ideal_none_without_clean_laps OK")


def make_rolling_session(n=401):
    """TWO CONTIGUOUS laps with mirrored pace: lap 0 runs its first half-track in 35 s and its
    second in 25 s; lap 1 the reverse (25 s then 35 s) — both 60 s laps. The loop from
    half-track in lap 0 to half-track in lap 1 stitches the two FAST halves: 25 + 25 = 50 s,
    the known best rolling window (the pair difference is V-shaped with its minimum exactly at
    φ = 0.5, a knot of both laps). `seed_cols` also feeds `lap_has_dropout` (steady-enough
    sample times, no interior gap)."""
    phi = np.linspace(0.0, 1.0, n)
    dists = phi * 1000.0
    times_a = np.interp(phi, [0.0, 0.5, 1.0], [0.0, 35.0, 60.0])
    times_b = 60.0 + np.interp(phi, [0.0, 0.5, 1.0], [0.0, 25.0, 60.0])
    s = bare_session({0: (times_a, dists), 1: (times_b, dists)}, best=0, valid=[0, 1])
    seed_cols(s, 0, times_a, dists)
    seed_cols(s, 1, times_b, dists)
    s.laps = SimpleNamespace(lap_time=lambda lid: 60.0,
                             sectors=SimpleNamespace(sector_lines=[]))
    return s


def test_best_rolling_finds_straddling_window():
    """The headline rolling-lap case: a start-anywhere loop straddling the S/F line beats both
    complete laps — 50 s vs the 60 s laps — and the φ-knot evaluation finds it EXACTLY."""
    s = make_rolling_session()
    rolling = s.best_rolling_lap()
    assert abs(rolling - 50.0) < 1e-9, rolling
    assert rolling < 60.0  # strictly faster than the best complete lap
    print("test_best_rolling_finds_straddling_window OK")


def test_best_rolling_excludes_dropout_straddles():
    """The ⚠ low-confidence rule: a straddling window touching a GPS-dropout lap is excluded,
    so the best rolling falls back to the best COMPLETE lap (which is always admitted —
    rolling ≤ best lap time stays guaranteed even when every straddle is excluded)."""
    s = make_rolling_session()
    s.lap_has_dropout = lambda lid: lid == 1  # lap 1 had a dropout
    assert abs(s.best_rolling_lap() - 60.0) < 1e-9
    # A single valid lap (no pair at all) likewise returns its own lap time; none → None.
    lone = make_rolling_session()
    lone._valid_cache = [0]
    assert abs(lone.best_rolling_lap() - 60.0) < 1e-9
    empty = bare_session(valid=[])
    assert empty.best_rolling_lap() is None
    print("test_best_rolling_excludes_dropout_straddles OK")


# ----------------------- 7) sector-segmentation robustness (D10 dedupe / D11 poison guard)

def make_dupe_sector_session():
    """TWO straight-line laps + THREE sector lines where TWO of them sit on the SAME track
    odometer (350 m, dropped twice). The raw global-argmin + sort would project them to the
    same cum_distance and emit a 0 s middle split; the dedupe in sector_boundary_distances
    must collapse the pair to a single boundary so every lap keeps positive, ordered splits."""
    lap_a, lap_b = 3, 7
    ta, da = odometer(120, 0.1, 100.0, 520.0)
    tb, db = odometer(110, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s = bare_session({lap_a: (ta, da), lap_b: (tb, db)}, best=lap_b, valid=[lap_a, lap_b])
    seed_cols(s, lap_a, ta, da)
    seed_cols(s, lap_b, tb, db)
    s.laps = SimpleNamespace(sectors=SimpleNamespace(sector_lines=[
        _seg(150.0, -5.0, 150.0, 5.0),
        _seg(350.0, -5.0, 350.0, 5.0),   # the duplicate pair: same midpoint x as the next line,
        _seg(350.0, -4.0, 350.0, 6.0),   # so both project to the SAME odometer -> must dedupe
    ]))
    return s, lap_a, lap_b


def test_sector_boundaries_dedupe_coincident_lines():
    """D10: two sector lines on the same odometer collapse to ONE ascending boundary (not a
    pair that sort() would leave adjacent and equal), so the boundary count is N_lines - 1 and
    the boundaries are strictly increasing — the guide lines can't sit on top of each other."""
    s, lap_a, lap_b = make_dupe_sector_session()
    for lid in (lap_a, lap_b):
        bounds = s.sector_boundary_distances(lid)
        assert len(bounds) == 2, (lid, bounds)            # 3 lines, the dupe pair fused to 1
        assert all(b2 - b1 > 0 for b1, b2 in zip(bounds, bounds[1:], strict=False)), \
            (lid, bounds)
    print("test_sector_boundaries_dedupe_coincident_lines OK")


def test_lap_sector_splits_no_zero_split_from_dupe_line():
    """D11 root: with the duplicate line collapsed, lap_sector_splits emits one split per
    DEDUPED sub-sector (boundaries+1 = 3, not the 4 the raw line count would give), all
    strictly positive, summing to the lap time — no 0 s / out-of-order split survives."""
    s, lap_a, lap_b = make_dupe_sector_session()
    for lid in (lap_a, lap_b):
        splits = s.lap_sector_splits(lid)
        assert len(splits) == 3, (lid, splits)            # boundaries (2) + 1, post-dedupe
        assert all(sp > 0 for sp in splits), (lid, splits)
        laptime = float(s._dist_cache[lid][0][-1] - s._dist_cache[lid][0][0])
        assert abs(sum(splits) - laptime) < 1e-9, (lid, sum(splits), laptime)
    print("test_lap_sector_splits_no_zero_split_from_dupe_line OK")


def test_theoretical_best_not_poisoned_by_degenerate_lap():
    """D11 headline: a single lap with a degenerate (here near-zero) split must NOT drag the
    per-column session best toward 0 / the theoretical best below a real lap's best stitch.

    The session-best columns and theoretical_best computed WITH a degenerate lap present equal
    those computed from the same valid laps with the degenerate lap excluded (the > 0 filter in
    session_best_splits ignores its poisoned column entry) — and the theoretical best is never
    faster than every real lap's worst column would allow."""
    s, lap_a, lap_b = make_two_lap_sector_session()  # 2 lines -> 3 fully-filled columns
    clean_bests = s.session_best_splits()
    clean_theo = s.theoretical_best()
    assert clean_bests and all(b is not None for b in clean_bests), clean_bests
    assert all(b > 0 for b in clean_bests), clean_bests

    # Inject a THIRD valid lap whose middle split is degenerate (0 s): seed its caches, then
    # monkeypatch lap_sector_splits so that lap returns a poisoned column directly (a 0 s split)
    # while the two real laps keep their real projections — the exact shape D11 warns about
    # (one degenerate lap among good ones feeding the per-column min).
    lap_c = 9
    tc, dc = odometer(115, 0.1, 200.0, 514.0)
    seed_lap(s, lap_c, tc, dc)
    seed_cols(s, lap_c, tc, dc)
    s._valid_cache = [lap_a, lap_b, lap_c]
    real_splits = {lid: s.lap_sector_splits(lid) for lid in (lap_a, lap_b)}
    poisoned = list(real_splits[lap_a])
    poisoned[1] = 0.0  # a degenerate middle split — the spurious 0 the guard must drop

    orig = s.lap_sector_splits

    def patched(lid):
        return poisoned if lid == lap_c else orig(lid)

    s.lap_sector_splits = patched
    try:
        poisoned_bests = s.session_best_splits()
        poisoned_theo = s.theoretical_best()
    finally:
        s.lap_sector_splits = orig
        s._valid_cache = [lap_a, lap_b]

    # The degenerate lap's 0 s column is filtered, so the per-column bests + theoretical best are
    # IDENTICAL to the clean two-lap session — the poison never reaches the purple cells / footer.
    assert poisoned_bests == clean_bests, (poisoned_bests, clean_bests)
    assert poisoned_theo == clean_theo, (poisoned_theo, clean_theo)
    # And concretely: the middle column's best is a real positive split, not the injected 0.
    assert poisoned_bests[1] > 0.0, poisoned_bests
    print("test_theoretical_best_not_poisoned_by_degenerate_lap OK")


def test_session_best_splits_filters_nonpositive_keeps_tiny_positive():
    """D11 defensive filter, pinned directly: session_best_splits takes the per-column min over
    FINITE and STRICTLY-POSITIVE splits only — a 0 / negative entry is ignored, but a legit
    tiny-but-positive split is still eligible to win its column."""
    s, lap_a, lap_b = make_two_lap_sector_session()  # 2 lines -> 3 columns
    s._valid_cache = [lap_a, lap_b]
    fake = {
        lap_a: [10.0, 0.0, 20.0],     # middle column degenerate (0) -> must be ignored
        lap_b: [10.0, 1e-6, 20.0],    # middle column tiny BUT positive -> eligible, wins
    }
    s.lap_sector_splits = lambda lid: fake[lid]
    bests = s.session_best_splits()
    assert bests == [10.0, 1e-6, 20.0], bests   # the 0 lost to the tiny-positive, not vice-versa
    assert s.theoretical_best() == float(sum([10.0, 1e-6, 20.0]))
    print("test_session_best_splits_filters_nonpositive_keeps_tiny_positive OK")


def _two_lap_dropout_session():
    """Two straight-line laps where the FASTER lap (0) has an interior GPS dropout and the
    slower lap (1) is clean. No sector lines, so each lap_sector_splits == its elapsed lap time.
    `_best_cache` is reset to the real sentinel so best_lap_id() runs its live path (bare
    __new__ would otherwise leave the slot unset)."""
    from studio.session import _UNSET
    t0 = np.arange(500) * 0.1            # 0.1 s steps (no gap) ...
    t0[250:] += 1.0                      # ... with ONE interior 1.1 s gap -> a GPS-dropout lap
    d0 = np.linspace(0.0, 500.0, 500)
    t1 = np.arange(550) * 0.1            # clean, steady, and SLOWER (54.9 s vs lap 0's ~50.9 s)
    d1 = np.linspace(0.0, 500.0, 550)
    s = bare_session({0: (t0, d0), 1: (t1, d1)}, valid=[0, 1])
    seed_cols(s, 0, t0, d0)
    seed_cols(s, 1, t1, d1)
    s.laps = SimpleNamespace(lap_time=lambda lid: {0: 50.0, 1: 55.0}[lid],
                             sectors=SimpleNamespace(sector_lines=[]))
    s._best_cache = _UNSET
    return s


def test_best_excludes_dropout_lap_then_falls_back():
    """A1 fix: a GPS-dropout lap (reconstructed distance, less-reliable timing) must never be the
    headline best / Δ-baseline / session-best split, even when it is the FASTEST lap — but if
    EVERY valid lap has a dropout, the candidate set falls back so a (⚠-flagged) best still
    exists. The lap table still SHOWS the dropout lap; only the 'best' selection excludes it."""
    s = _two_lap_dropout_session()
    assert s.lap_has_dropout(0) is True and s.lap_has_dropout(1) is False
    # The faster lap 0 is excluded; only the clean lap 1 is a best candidate.
    assert s._best_candidate_ids() == [1]
    assert s.best_lap_id() == 1, "the dropout lap must NOT win 'best' despite being faster"
    # No sectors => one column == the clean candidate's lap time, NOT the faster dropout lap's.
    split0, split1 = s.lap_sector_splits(0)[0], s.lap_sector_splits(1)[0]
    assert split0 < split1, (split0, split1)        # the dropout lap really is faster ...
    assert s.session_best_splits() == [split1]      # ... yet the clean (slower) lap owns purple
    assert abs(s.theoretical_best() - split1) < 1e-9

    # Fallback: when both laps are dropouts the set degrades to all valid, best = fastest.
    s2 = _two_lap_dropout_session()
    s2.lap_has_dropout = lambda lid: True
    assert s2._best_candidate_ids() == [0, 1]
    assert s2.best_lap_id() == 0
    print("test_best_excludes_dropout_lap_then_falls_back OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SESSION-PURE TESTS PASSED")
