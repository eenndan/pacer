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
  * theoretical / rolling best (F1-roadmap): `session_best_splits` is the per-column min;
    `best_rolling_lap` finds a known faster straddling window on a two-lap session, excludes
    windows spanning a GPS-dropout lap, and degrades to the best complete lap.
  * THE IDEAL LAP (§6b): the corner/straight-partition composite that `theoretical_best` now IS —
    six properties, each written so the `ideal == best lap time` degeneracy that shipped FAILS
    them (it satisfied every one of the seven lower-envelope invariants that used to live there).
Run: python tests/test_session_pure.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import (  # noqa: E402
    bare_session,
    odometer,
    reset_corner_caches,
    seed_cols,
    seed_corner_basis,
    seed_lap,
)

import pacer  # noqa: E402
from studio import corner_model  # noqa: E402
from studio import corners as corners_mod  # noqa: E402
from studio._signal import (  # noqa: E402
    LAP_BAND_HI,
    LAP_BAND_LO,
    LAP_DIST_BAND_HI,
    LAP_DIST_BAND_LO,
    MIN_LAP_SAMPLES,
    MIN_LAP_TIME,
    _band_lap_ids,
    _banded_out_lap_ids,
)
from studio.corner_model import SegmentBests  # noqa: E402
from studio.load import (  # noqa: E402
    _HEURISTIC_HALF_M,
    MIN_START_SPEED,
    _clean,
    _heuristic_start_base,
    _sustained_moving,
)
from studio.session import Session  # noqa: E402

# ------------------------------------------------------------------ shared fakes / seeding

class _FakeBandLaps:
    """The pacer.Laps READ surface `_band_lap_ids` touches — laps_count / lap_time /
    sample_count, and OPTIONALLY get_lap_distance (the distance band).

    Distances are passed as ``dists``; when omitted the double does NOT expose
    get_lap_distance at all (via __getattr__ raising AttributeError), so `_band_lap_ids`'
    getattr guard falls back to the unchanged time-only path — the way the real fake test
    doubles (and any pre-distance-band caller) drive it."""

    def __init__(self, times, samples=None, dists=None):
        self._times = list(times)
        # Sample-rich by default so the time band is what's under test.
        self._samples = list(samples) if samples is not None else [1000] * len(self._times)
        self._dists = list(dists) if dists is not None else None

    def laps_count(self):
        return len(self._times)

    def lap_time(self, i):
        return self._times[i]

    def sample_count(self, i):
        return self._samples[i]

    def __getattr__(self, name):
        # Only surface get_lap_distance when distances were supplied — otherwise it must be
        # absent so the getattr(laps, "get_lap_distance", None) guard hits its fallback.
        if name == "get_lap_distance" and self.__dict__.get("_dists") is not None:
            return lambda i: self._dists[i]
        raise AttributeError(name)


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


def test_band_lap_ids_distance_band_drops_short_mis_segmented_lap():
    """THE real-recording bug (a fixed circuit, ~1060 m / ~1:08 laps): ONE lap was
    mis-segmented SHORT (921 m / 0:59) — 0.87x the median in BOTH time and distance, so it sat
    INSIDE the time band and was crowned 'best', poisoning best/baseline/coaching/map. The
    distance band (±10% of the median lap distance) catches it: its distance is out-of-band even
    though its TIME passed. Real laps (distance varies only a few %) all survive."""
    # Five laps: times all in-band (median 68 -> band [34, 108.8]); distances all ~1060 m
    # EXCEPT lap 3 at 921 m (the phantom). Median distance 1060 -> band [954, 1166].
    times = [68.0, 68.5, 67.5, 59.0, 68.2]
    dists = [1060.0, 1058.0, 1062.0, 921.0, 1059.0]
    med_t = float(np.median(times))
    med_d = float(np.median(dists))
    # The phantom passes the TIME band ...
    assert LAP_BAND_LO * med_t <= times[3] <= LAP_BAND_HI * med_t
    # ... but its distance is BELOW the distance band, while every real lap is inside it.
    assert dists[3] < LAP_DIST_BAND_LO * med_d
    for d in (dists[0], dists[1], dists[2], dists[4]):
        assert LAP_DIST_BAND_LO * med_d <= d <= LAP_DIST_BAND_HI * med_d
    assert _band_lap_ids(_FakeBandLaps(times, dists=dists)) == [0, 1, 2, 4]
    print("test_band_lap_ids_distance_band_drops_short_mis_segmented_lap OK")


def test_band_lap_ids_distance_band_noop_on_clean_recording():
    """No-op guarantee: a clean recording where every lap's distance clusters within a few % of
    the median (only racing-line / GPS variation) keeps EVERY lap — the distance band never
    excludes a real lap. Times vary a bit more (as on a real circuit); all stay in both bands."""
    times = [67.0, 69.0, 68.0, 70.0, 66.5]
    dists = [1055.0, 1062.0, 1058.0, 1049.0, 1067.0]  # spread ~1.7% — all within ±10%
    assert _band_lap_ids(_FakeBandLaps(times, dists=dists)) == [0, 1, 2, 3, 4]
    # A LONG mis-segmented lap (extra loop, ~1.5x distance) is also caught by the distance band.
    times2 = [68.0, 68.0, 68.0, 90.0]           # 90 s is still inside the time band [34, ...]
    dists2 = [1060.0, 1060.0, 1060.0, 1600.0]   # but 1600 m is way above the distance band
    assert _band_lap_ids(_FakeBandLaps(times2, dists=dists2)) == [0, 1, 2]
    print("test_band_lap_ids_distance_band_noop_on_clean_recording OK")


def test_band_lap_ids_falls_back_when_no_distance_accessor():
    """CRITICAL back-compat: a `laps` double with NO get_lap_distance (the pre-distance-band
    fake, and any caller that never exposed distances) falls back to the unchanged time-only
    result — the distance band only applies when real distances exist."""
    times = [60.0, 61.0, 62.0, 200.0, 25.0]     # same case as the time-band test
    # No dists -> _FakeBandLaps hides get_lap_distance -> getattr guard -> time-only result.
    assert not hasattr(_FakeBandLaps(times), "get_lap_distance")
    assert _band_lap_ids(_FakeBandLaps(times)) == [0, 1, 2]
    # A double that DOES expose get_lap_distance but reports only non-finite / non-positive
    # distances (no usable median to band against) ALSO falls back to the time-only result.
    bad = _FakeBandLaps([68.0, 68.0, 68.0], dists=[float("nan"), 0.0, -5.0])
    assert hasattr(bad, "get_lap_distance")
    assert _band_lap_ids(bad) == [0, 1, 2]
    print("test_band_lap_ids_falls_back_when_no_distance_accessor OK")


def test_banded_out_lap_ids_reports_the_short_mis_segmented_lap():
    """The complement of the valid set: the SAME short-lap scenario as the distance-band test —
    lap 3 (921 m / 0:59) is banded out, so it shows up in the EXCLUDED set (and, being invalid, in
    neither the valid set nor overlapping it). This is what the lap panel surfaces so a dropped
    real-looking lap isn't invisible."""
    times = [68.0, 68.5, 67.5, 59.0, 68.2]
    dists = [1060.0, 1058.0, 1062.0, 921.0, 1059.0]
    valid = _band_lap_ids(_FakeBandLaps(times, dists=dists))
    excluded = _banded_out_lap_ids(_FakeBandLaps(times, dists=dists))
    assert valid == [0, 1, 2, 4]
    assert excluded == [3]
    assert set(valid).isdisjoint(excluded)
    print("test_banded_out_lap_ids_reports_the_short_mis_segmented_lap OK")


def test_banded_out_lap_ids_empty_on_clean_recording():
    """No-op guarantee mirroring the band's: when every substantial lap is in-band nothing is
    excluded (so a clean recording shows no EXCLUDED strip)."""
    times = [67.0, 69.0, 68.0, 70.0, 66.5]
    dists = [1055.0, 1062.0, 1058.0, 1049.0, 1067.0]
    assert _banded_out_lap_ids(_FakeBandLaps(times, dists=dists)) == []
    print("test_banded_out_lap_ids_empty_on_clean_recording OK")


def test_banded_out_lap_ids_also_catches_a_time_band_reject():
    """A merged double-lap clears the coarse gate but is way over the TIME band (no distances
    supplied → time-only path); it is banded out of the valid set and reported as excluded."""
    times = [68.0, 68.5, 67.5, 140.0]  # lap 3 ~2x the median → outside [0.5, 1.6]x
    assert _band_lap_ids(_FakeBandLaps(times)) == [0, 1, 2]
    assert _banded_out_lap_ids(_FakeBandLaps(times)) == [3]
    print("test_banded_out_lap_ids_also_catches_a_time_band_reject OK")


def test_banded_out_lap_ids_ignores_sub_gate_fragments():
    """A brief start/end sliver (too few samples AND under MIN_LAP_TIME) is NOT a 'substantial
    excluded lap' — it fails the coarse gate, so it's in neither the valid nor the excluded set
    and never clutters the strip."""
    laps = _FakeBandLaps([68.0, 68.5, 2.0],
                         samples=[1000, 1000, MIN_LAP_SAMPLES - 1],
                         dists=[1060.0, 1058.0, 30.0])
    assert _band_lap_ids(laps) == [0, 1]
    assert _banded_out_lap_ids(laps) == []
    print("test_banded_out_lap_ids_ignores_sub_gate_fragments OK")


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
    # _gate_quality now returns a 5th value: the dropped fraction over the MOVING trace (the share of
    # fixes with full_speed > moving_speed that the gate rejected), which the load path threads into
    # the data-quality signal instead of dropped/raw (see the D24 stationary-lead-in artifact fix).
    s2, sp2, naive2, dropped, moving_frac = _gate_quality(samples, spans, list(range(n)))
    assert naive2 == [0, 2, 4, 6], naive2  # only the finite samples survive (naive kept in step)
    assert len(s2) == len(sp2) == 4
    assert dropped == 3, dropped          # the 3 non-finite fixes were rejected (raw count, logged)
    # With the default moving_speed=0.0, the "moving" set is every finite-positive-speed fix: the 4
    # good (10 m/s) + bad[0]/bad[1] (10 m/s but non-finite position) = 6; bad[2] has a NaN speed so it
    # can't count as moving. Of those 6 moving fixes 2 were dropped (bad[0], bad[1]) → 2/6.
    assert abs(moving_frac - 2 / 6) < 1e-12, moving_frac
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


def test_sector_splits_no_longer_define_the_theoretical_best():
    """The sum of the session-best SECTOR splits is still computable and still what the lap
    table's purple row shows — but it is NO LONGER `theoretical_best`.

    It could not be. Sector lines default to NONE, and with no line a lap is a single sub-sector
    whose split is its lap time, so the sum was identically the best lap time on every recording
    anyone owns (pinned below, on the same fixture that used to assert it AS the ideal). It also
    moved the wrong way when a line was added, because each lap projects the same midpoint onto
    its own odometer and the pieces tile nothing.

    THAT SECOND CLAIM USED TO CITE "on D24, 68.393 s → 68.651 s". Those numbers do not reproduce
    and cannot ever be checked: they were measured on ~/Desktop/D24/GX010060.MP4, which an agent
    overwrote with a 2.3 MB JSON dump. Re-derived on the D24 recording that survives (GX010062,
    `sum(session_best_splits())` at 0/1/2/3 evenly spaced lines — the retired formula, unchanged
    by the wave) the old target falls monotonically: 68.771 → 68.684 → 68.540 → 68.287. The
    non-monotonicity is real but it is NOT on D24 at one line — on Sandown chapter 1 the same
    sweep reads 48.983 → 48.506 → **48.635** → 48.329, so the second line put 0.129 s BACK on a
    target that is supposed to only improve with information. Cite that one; it is on a recording
    that still exists."""
    s, lap_a, lap_b = make_two_lap_sector_session()
    bests = s.session_best_splits()
    assert len(bests) == 3 and all(b > 0 for b in bests), bests
    # Still a real per-column minimum, and still ≤ the best lap time.
    best_laptime = float(min(sum(s.lap_sector_splits(lap_a)), sum(s.lap_sector_splits(lap_b))))
    assert float(sum(bests)) <= best_laptime + 1e-12
    # With NO sector line the "ideal by sectors" IS the best lap time — the degeneracy that
    # shipped as the app's largest readout.
    s.laps.sectors = SimpleNamespace(sector_lines=[])
    laptimes = [float(sum(s.lap_sector_splits(lid))) for lid in (lap_a, lap_b)]
    one_col = s.session_best_splits()
    assert len(one_col) == 1 and abs(one_col[0] - min(laptimes)) < 1e-9, (one_col, laptimes)
    # And `theoretical_best` no longer takes that value: this fixture has no corner partition,
    # so it reports None (hidden) rather than the best lap time dressed as a target.
    assert s.theoretical_best() is None
    print("test_sector_splits_no_longer_define_the_theoretical_best OK")


# --------------------------------------------- 6b) the IDEAL LAP (corner composite, D1)
#
# The ideal lap used to be the pointwise MINIMUM of the laps' cumulative-elapsed curves on a
# normalized-distance grid. Every lap's elapsed ends at its own lap time and every lap's
# normalized distance ends at 1, so that minimum at s=1 was — identically, on every recording
# anyone owns — THE BEST LAP TIME. `theoretical_best` (the sum of the session-best SECTOR splits)
# degenerated the same way, because sector lines default to none and a lap with no sector line is
# a single sub-sector whose split is its lap time. Both shipped, and the app's largest scalar
# readout was a structural zero in the state it is always in on arrival.
#
# None of the seven invariants that used to live here could catch that: they all asserted
# properties of a lower envelope (≤ every lap, non-decreasing, Δ ≥ 0) which `ideal == best` also
# satisfies. The tests below are written so `ideal == best` FAILS them.

_IDEAL_CORNERS = ((200.0, 300.0), (600.0, 750.0))


def make_ideal_session(spans=_IDEAL_CORNERS, sector_lines=()):
    """THREE clean laps with CROSSING pace over a seeded two-corner partition.

    Lap 0 is slow early / fast late, lap 1 is fast early / slow late, lap 2 is middling and is the
    seeded best. So lap 1 wins the early segments and lap 0 the late ones: the composite draws on
    more than one donor and is STRICTLY faster than any single lap — the whole point, and what the
    old lower envelope could not produce at s=1 no matter how the laps crossed. All three are
    valid, dropout-free (the 0.1 s sample step is well under gapfill's 0.35 s threshold) and
    seeded into both caches, so `consistency_lap_ids` returns all three.

    The lap ids are CONSECUTIVE and the lap clocks CONTIGUOUS, so `best_rolling_lap` has real
    straddling windows to find — lap 0's fast second half joins lap 1's fast first half, making
    the rolling best strictly faster than the best lap. Without that, "ideal ≤ best rolling"
    (property 4) is vacuous: the shipped `ideal == best lap time` satisfies it.

    All three laps are 1000 m, so `corners.project_boundaries` stays on its normalized branch and
    the partition edges are deterministic."""
    def slow_fast(u):
        return 0.8 + 1.4 * np.sin(u / 2)

    def fast_slow(u):
        return 2.2 - 1.4 * np.sin(u / 2)

    t0, d0 = odometer(122, 0.1, 100.0, 1000.0, slow_fast)
    t1, d1 = odometer(120, 0.1, float(t0[-1]), 1000.0, fast_slow)
    t2, d2 = odometer(118, 0.1, float(t1[-1]), 1000.0)
    s = bare_session({0: (t0, d0), 1: (t1, d1), 2: (t2, d2)}, best=2, valid=[0, 1, 2])
    for lid, (t, d) in ((0, (t0, d0)), (1, (t1, d1)), (2, (t2, d2))):
        seed_cols(s, lid, t, d)
    s.laps = SimpleNamespace(
        laps_count=lambda: 3,
        lap_time=lambda i: float(s._dist_cache[i][2][-1]),
        sectors=SimpleNamespace(sector_lines=list(sector_lines)),
    )
    seed_corner_basis(s, spans)
    return s, (0, 1, 2)


def _lap_times(s, ids):
    return {lid: float(s._lap_arrays(lid)[2][-1]) for lid in ids}


# --- property 1: the ideal is a lap you never drove -------------------------------------

def test_ideal_is_strictly_faster_than_the_best_lap():
    """PROPERTY 1 — the regression test for the defect that shipped. With more than one lap
    donating a segment, the ideal is STRICTLY faster than the best lap; `ideal == best_lap_time`
    fails here. It is also strictly faster than EVERY clean lap, and equals the sum of the
    per-segment minima exactly (one number, no second computation)."""
    s, ids = make_ideal_session()
    ideal = s.ideal_total()
    times = _lap_times(s, ids)
    sb = s.ideal_segment_bests()
    assert sb is not None and len(sb.donor_ids()) >= 2, "fixture must have >1 donor"
    assert ideal == float(sum(sb.bests)), (ideal, sb.bests)
    for lid, laptime in times.items():
        assert ideal < laptime - 1e-9, (lid, ideal, laptime)
    # And it is not a rounding-level difference: the crossing-pace fixture leaves real time on
    # the table, exactly as the real recordings do (0.22 s … 1.64 s there).
    assert min(times.values()) - ideal > 1e-3, (ideal, times)
    print("test_ideal_is_strictly_faster_than_the_best_lap OK")


# --- property 2: it draws on more than one lap --------------------------------------------

def test_ideal_composite_draws_on_more_than_one_donor():
    """PROPERTY 2 — the composite is stitched, not copied. More than one distinct lap wins a
    segment, `single_donor_id()` is therefore None, and every donor is a clean lap. The old
    envelope had exactly ONE donor at s=1 (the best lap) by construction."""
    s, ids = make_ideal_session()
    sb = s.ideal_segment_bests()
    donors = sb.donor_ids()
    assert len(donors) > 1, donors
    assert set(donors) <= set(ids), donors
    assert sb.single_donor_id() is None
    assert s.ideal_donor_lap_id() is None
    # Every non-point segment names a donor, and that donor's own time there IS the segment best.
    for j, best in enumerate(sb.bests):
        if best <= 0.0:
            assert sb.donors[j] is None, j
            continue
        row = sb.lap_ids.index(sb.donors[j])
        assert abs(float(sb.times[row, j]) - best) < 1e-12, (j, sb.donors[j])
    print("test_ideal_composite_draws_on_more_than_one_donor OK")


def test_segment_times_partition_each_lap_exactly():
    """The guarantee that makes a cross-lap composite legitimate: each lap's 2N+1 segment times
    SUM EXACTLY to that lap's own time (`corners.segment_times` asserts it; this pins that the
    composite is built on that guarantee and not on some other decomposition). Summing one lap's
    best corner with another's best straight therefore double-counts nothing and drops nothing."""
    s, ids = make_ideal_session()
    sb = s.ideal_segment_bests()
    times = _lap_times(s, ids)
    assert sb.times.shape == (len(ids), 2 * len(_IDEAL_CORNERS) + 1)
    for row, lid in enumerate(sb.lap_ids):
        assert abs(float(sb.times[row].sum()) - times[lid]) < 1e-9, lid
    # The partition edges span the whole lap, in order, on the reference odometer.
    assert sb.s_edges[0] == 0.0 and abs(sb.s_edges[-1] - 1.0) < 1e-12
    assert all(b >= a - 1e-12 for a, b in zip(sb.s_edges[:-1], sb.s_edges[1:], strict=True))
    print("test_segment_times_partition_each_lap_exactly OK")


# --- property 3: Δ-to-ideal is its own number ---------------------------------------------

def test_delta_to_ideal_at_flag_is_laptime_minus_ideal_and_not_delta_to_best():
    """PROPERTY 3 — at the flag Δ-to-ideal == lap_time − ideal_total, and it is NOT the same
    number as Δ-to-best. On the shipped code the two were identical for every lap, because the
    ideal WAS the best lap; here they differ by exactly (best_lap_time − ideal) for every lap."""
    s, ids = make_ideal_session()
    ideal = s.ideal_total()
    times = _lap_times(s, ids)
    best = s.best_lap_id()
    gap = times[best] - ideal
    assert gap > 1e-3, gap
    for lid in ids:
        for mode in ("distance", "time"):
            _x, dy = s.delta_to_ideal([lid], mode)[lid]
            assert abs(float(dy[-1]) - (times[lid] - ideal)) < 1e-9, (lid, mode)
        # Δ-to-BEST at the flag is lap_time − best_lap_time; the two differ by the ideal's gap.
        to_best = times[lid] - times[best]
        to_ideal = float(s.delta_to_ideal([lid], "distance")[lid][1][-1])
        assert abs((to_ideal - to_best) - gap) < 1e-9, (lid, to_ideal, to_best)
        assert abs(to_ideal - to_best) > 1e-3, lid
    # The BEST lap is the case that mattered: its Δ-to-best is 0 by definition, so a Δ-to-ideal
    # that also read 0 was the app's biggest readout showing a structural null.
    best_to_ideal = float(s.delta_to_ideal([best], "distance")[best][1][-1])
    assert best_to_ideal > 1e-3, best_to_ideal
    print("test_delta_to_ideal_at_flag_is_laptime_minus_ideal_and_not_delta_to_best OK")


# --- property 4: it beats the best rolling lap too ----------------------------------------

def test_ideal_le_best_rolling_and_best_lap():
    """PROPERTY 4 — the ideal is a target, so it must not be SLOWER than a lap the driver has
    already effectively done. On D24 the shipped theoretical best was 68.771 s against a best
    rolling of 68.635 s: 0.136 s slower than an already-beaten number."""
    s, ids = make_ideal_session()
    ideal, rolling = s.ideal_total(), s.best_rolling_lap()
    times = _lap_times(s, ids)
    assert rolling is not None
    # The constraint has to BITE: the fixture's contiguous, crossing-pace laps give a straddling
    # window strictly faster than the best lap, so `ideal <= rolling` is not satisfiable by the
    # shipped `ideal == best lap time`.
    assert rolling < min(times.values()) - 1e-6, (rolling, times)
    assert ideal <= rolling + 1e-9, (ideal, rolling)
    assert ideal <= min(times.values()) + 1e-9, (ideal, times)
    print("test_ideal_le_best_rolling_and_best_lap OK")


# --- property 5: the excluded stay excluded -----------------------------------------------

def test_ideal_excludes_dropout_and_band_excluded_laps():
    """PROPERTY 5 — only VALID, DROPOUT-FREE laps donate. A lap outside the valid band never
    appears; a lap with an interior GPS dropout is dropped too, because its distance is
    speed-integral reconstructed, which is exactly what makes its segment BOUNDARIES (and so its
    segment times) untrustworthy. Both are checked by making a fast lap ineligible and asserting
    the ideal does not improve."""
    ringer = 7  # a lap fast enough to win EVERY segment if it were ever admitted

    def with_ringer(*, valid, dropout):
        s, ids = make_ideal_session()
        tr, dr = odometer(120, 0.05, 900.0, 1000.0)   # half the sample step -> half the lap time
        seed_lap(s, ringer, tr, dr)
        seed_cols(s, ringer, tr, dr)
        if valid:
            s._valid_cache = [*ids, ringer]
        if dropout:
            # A >0.35 s interior gap in the ringer's point times is what lap_has_dropout reads.
            gappy = np.concatenate((tr[:60], tr[60:] + 5.0))

            def _point_times(lid, _g=gappy, _t=tr):
                return _g if lid == ringer else _t

            s._lap_point_times = _point_times
        seed_corner_basis(s, _IDEAL_CORNERS)
        return s, ids

    base_s, _ids = with_ringer(valid=False, dropout=False)
    base = base_s.ideal_total()
    base_donors = set(base_s.ideal_segment_bests().donor_ids())

    # NEGATIVE CONTROL — the ringer really is fast enough to change the answer. If it is admitted
    # (valid, no dropout) it wins every segment and the ideal collapses onto it. Without this the
    # two assertions below would pass on a ringer nobody would have picked anyway.
    admitted, _ = with_ringer(valid=True, dropout=False)
    assert admitted.ideal_total() < base - 1e-3, (admitted.ideal_total(), base)
    assert admitted.ideal_donor_lap_id() == ringer

    # BAND: not in valid_lap_ids -> never donates, ideal unchanged.
    banded, _ = with_ringer(valid=False, dropout=False)
    assert banded.ideal_total() == base, "a lap outside the valid band donated to the ideal"

    # DROPOUT: valid, but its distance is reconstructed -> excluded by the same rule.
    dropped, _ = with_ringer(valid=True, dropout=True)
    assert dropped.lap_has_dropout(ringer) is True, "fixture must actually flag the dropout"
    assert dropped.ideal_total() == base, "a GPS-dropout lap donated to the ideal"
    assert set(dropped.ideal_segment_bests().donor_ids()) == base_donors
    print("test_ideal_excludes_dropout_and_band_excluded_laps OK")


# --- property 6: more segmentation never makes the target worse ---------------------------

def test_more_segments_never_make_the_ideal_slower():
    """PROPERTY 6 — adding information must not make the target worse. This FAILED on record:
    adding a sector line could move the old theoretical best UP, because each lap projected the
    same midpoint onto its own odometer and the pieces tiled nothing. Measured on the real
    recordings with `sum(session_best_splits())` at 0/1/2/3 lines: Sandown chapter 1 reads
    48.983 → 48.506 → **48.635** → 48.329 — the second line gives 0.129 s back.

    (This docstring used to cite "68.393 s to 68.651 s on D24". Those numbers came from
    GX010060.MP4, the recording an agent destroyed, and do not reproduce on the D24 that survives:
    GX010062 goes 68.771 → 68.684 → 68.540 → 68.287, monotonically down. The Sandown figures above
    are re-derived on a file that still exists, which is the only kind of number worth pinning.)

    Two halves. (a) SECTOR LINES no longer touch the ideal at all — it is now invariant to them,
    which is stronger than monotone (verified byte-identical across 0/1/2/3 lines on the three
    real recordings too). (b) REFINING THE PARTITION — splitting a segment in two — can only
    lower the ideal, because a minimum over a refinement is taken over strictly more freedom."""
    coarse, ids = make_ideal_session(spans=((200.0, 300.0),))
    base = coarse.ideal_total()

    # (a) sector lines are a DISPLAY split now: same lap arrays, more columns, same ideal.
    for n in (1, 2, 3):
        lines = [_seg(x, -5.0, x, 5.0) for x in np.linspace(1000.0 / (n + 1), 1000.0, n,
                                                            endpoint=False)]
        withlines, _ = make_ideal_session(spans=((200.0, 300.0),), sector_lines=lines)
        assert withlines.session_best_splits() is not None
        assert len(withlines.session_best_splits()) == n + 1
        assert withlines.ideal_total() == base, (n, withlines.ideal_total(), base)
        assert withlines.theoretical_best() == base, n

    # (b) a finer corner partition is a refinement of the coarse one -> never slower.
    finer, _ = make_ideal_session(spans=((200.0, 300.0), (600.0, 750.0)))
    finest, _ = make_ideal_session(spans=((200.0, 300.0), (450.0, 520.0), (600.0, 750.0)))
    assert finer.ideal_total() <= base + 1e-9, (finer.ideal_total(), base)
    assert finest.ideal_total() <= finer.ideal_total() + 1e-9
    # and refinement genuinely buys something on a crossing-pace session (not a no-op assert)
    assert finest.ideal_total() < base - 1e-6, (finest.ideal_total(), base)
    print("test_more_segments_never_make_the_ideal_slower OK")


# --- the curve, the per-tick scalar, and the degenerate states ----------------------------

def test_ideal_curve_is_the_running_segment_total_at_the_partition_edges():
    """The ideal CURVE is the composite's running total, exact at every partition edge and
    following each segment's DONOR in between (the ideal lap is a real drive, so its shape
    through a segment is the shape its donor drove). It starts at 0, is non-decreasing — with no
    `np.maximum.accumulate` repair, which a running total of non-negative times does not need —
    and ends at `ideal_total`."""
    s, _ids = make_ideal_session()
    env = s.ideal_lap_elapsed()
    sb = s.ideal_segment_bests()
    assert env is not None and len(env) == Session._DELTA_GRID_N == 400
    assert abs(float(env[0])) < 1e-9
    assert np.all(np.diff(env) >= -1e-9), float(np.diff(env).min())
    assert abs(float(env[-1]) - s.ideal_total()) < 1e-9
    # Exact at the edges: resampling the 400-grid curve at each edge reproduces the running sum.
    s_grid = np.linspace(0.0, 1.0, Session._DELTA_GRID_N)
    cum = sb.cumulative()
    for j, s_edge in enumerate(sb.s_edges):
        got = float(np.interp(s_edge, s_grid, env))
        assert abs(got - float(cum[j])) < 5e-3, (j, s_edge, got, float(cum[j]))
    # The curve is NOT a straight line between edges — it carries the donor's pace.
    inside = (s_grid > sb.s_edges[1]) & (s_grid < sb.s_edges[2])
    if inside.sum() > 3:
        chord = np.interp(s_grid[inside], [sb.s_edges[1], sb.s_edges[2]],
                          [cum[1], cum[2]])
        assert np.abs(env[inside] - chord).max() > 1e-6, "donor shape collapsed to a chord"
    print("test_ideal_curve_is_the_running_segment_total_at_the_partition_edges OK")


def test_delta_to_ideal_is_non_negative_at_the_partition_edges():
    """Δ-to-ideal is NOT one-way, and that is correct. At every partition EDGE the ideal took the
    minimum over the clean laps, so every donor lap is at or behind it there. INSIDE a segment the
    ideal follows its donor's line, so a lap that brakes later can be transiently AHEAD — real
    information, and the reason nothing here clamps or asserts pointwise ≥ 0.

    Measured on the real recordings (372 k samples at 25 ms of media clock): the most negative
    value is −0.052 s and under 1 % of samples are negative, against end-of-lap values of
    +0.22 … +9.24 s. This pins the SHAPE of that claim — non-negative at the edges, bounded and
    small in between — so a future change that makes the interior wander gets caught."""
    s, ids = make_ideal_session()
    sb = s.ideal_segment_bests()
    s_grid = np.linspace(0.0, 1.0, Session._DELTA_GRID_N)
    for mode in ("distance", "time"):
        series = s.delta_to_ideal(list(ids), mode)
        assert series is not None
        for lid in ids:
            x, dy = series[lid]
            assert len(x) == len(dy) == 400
            for s_edge in sb.s_edges:
                at_edge = float(np.interp(s_edge, s_grid, dy))
                assert at_edge >= -5e-3, (mode, lid, s_edge, at_edge)
            # bounded in between — an excursion is a line difference, not a lap's worth of time
            assert float(dy.min()) > -0.25 * s.ideal_total(), (mode, lid, float(dy.min()))
    print("test_delta_to_ideal_is_non_negative_at_the_partition_edges OK")


def test_delta_to_ideal_at_matches_grid_and_is_cheap():
    """The per-tick scalar `delta_to_ideal_at(lap, t)` (the number the live readout leads with)
    AGREES with the grid-based `delta_to_ideal` curve resampled at the lap's own track fraction,
    and at the lap finish equals lap_time − ideal_total. The curve is MEMOIZED so the 30 Hz path
    stays cheap."""
    s, ids = make_ideal_session()
    ideal_total = s.ideal_total()
    s_grid = np.linspace(0.0, 1.0, Session._DELTA_GRID_N)
    for lid in ids:
        times, dist, _elapsed = s._dist_cache[lid]
        grid = s.delta_to_ideal([lid], "distance")[lid][1]  # dy on the 400 s-grid
        for frac in (0.0, 0.25, 0.6, 1.0):
            t = float(times[0] + frac * (times[-1] - times[0]))
            got = s.delta_to_ideal_at(lid, t)
            assert got is not None, (lid, frac)
            s_here = float(np.interp(t, times, dist)) / float(dist[-1])
            want = float(np.interp(s_here, s_grid, grid))
            # The scalar interps elapsed DIRECTLY at t; the grid curve resamples elapsed onto 400
            # points first, so `want` carries one extra grid-discretization step — agree to the
            # 400-grid resolution, not bit-for-bit (the scalar is the more accurate of the two).
            assert abs(got - want) < 1e-3, (lid, frac, got, want)
        finish = s.delta_to_ideal_at(lid, float(times[-1]))
        laptime = float(times[-1] - times[0])
        assert abs(finish - (laptime - ideal_total)) < 1e-9, (lid, finish)
    first = s._ideal_envelope()
    assert s._ideal_envelope() is first, "ideal curve must be memoized for the per-tick path"
    print("test_delta_to_ideal_at_matches_grid_and_is_cheap OK")


def test_delta_to_ideal_at_none_without_ideal_or_degenerate():
    """`delta_to_ideal_at` returns None (no crash) when there's no partition to build the ideal
    on, and the memoized curve drops on a fresh slot — the per-tick path degrades gracefully."""
    s = bare_session(valid=[], best=None)
    s._best_cache = None  # bare_session only seeds the memo for a non-None best; Session.__init__
    s.laps = SimpleNamespace(laps_count=lambda: 0)  # always sets it (session.py:171)
    assert s.delta_to_ideal_at(0, 0.0) is None
    # _drop_ideal_cache is a safe no-op before the slot exists, and clears it once set.
    s._drop_ideal_cache()
    s2, _ids = make_ideal_session()
    assert s2._ideal_envelope() is not None
    s2._drop_ideal_cache()
    assert not hasattr(s2, "_ideal_cache"), "drop must forget the memoized curve"
    print("test_delta_to_ideal_at_none_without_ideal_or_degenerate OK")


def test_ideal_delta_to_best_ends_below_zero_on_the_best_axis():
    """`ideal_delta_to_best` (the ideal drawn on delta()'s own Δ-to-best axis) ENDS STRICTLY BELOW
    zero — on the shipped code it ended at exactly 0, which is why the chart overlay could only
    ever draw a curve returning to the y=0 line. It is ≤ 0 at every partition edge; between edges
    the ideal follows its donor and the best lap its own line, so it can rise fractionally above
    (measured at most +0.019 s on the real recordings)."""
    s, _ids = make_ideal_session()
    best_time = float(s._lap_arrays(s.best_lap_id())[2][-1])
    ideal_total = s.ideal_total()
    sb = s.ideal_segment_bests()
    s_grid = np.linspace(0.0, 1.0, Session._DELTA_GRID_N)
    for mode in ("distance", "time"):
        x, dy = s.ideal_delta_to_best(mode)
        assert len(x) == len(dy) == 400
        assert float(dy[-1]) < -1e-3, (mode, float(dy[-1]))
        assert abs(float(dy[-1]) - (ideal_total - best_time)) < 1e-9, mode
        for s_edge in sb.s_edges:
            assert float(np.interp(s_edge, s_grid, dy)) <= 5e-3, (mode, s_edge)
    x_dist, _ = s.ideal_delta_to_best("distance")
    assert abs(float(x_dist[-1]) - s.active_baseline_total_distance()) < 1e-9
    x_time, _ = s.ideal_delta_to_best("time")
    assert abs(float(x_time[-1]) - best_time) < 1e-9
    print("test_ideal_delta_to_best_ends_below_zero_on_the_best_axis OK")


def test_ideal_single_donor_is_detectable_not_a_silent_duplicate():
    """DEGENERATE STATE, made visible. With ONE clean lap the ideal IS that lap — a correct
    answer, but printing it beside the best-lap readout is printing the same number twice. So
    `ideal_donor_lap_id()` names the lap, and a surface can say "your lap 4" instead. (This is the
    real state of a short recording: the Sandown single chapter has one valid lap.)"""
    lap = 4
    t, d = odometer(120, 0.1, 50.0, 900.0)
    s = bare_session({lap: (t, d)}, best=lap, valid=[lap])
    seed_cols(s, lap, t, d)
    s.laps = SimpleNamespace(laps_count=lambda: 5, lap_time=lambda i: float(t[-1] - t[0]),
                             sectors=SimpleNamespace(sector_lines=[]))
    seed_corner_basis(s, _IDEAL_CORNERS, total=900.0)
    elapsed = s._lap_arrays(lap)[2]
    assert abs(s.ideal_total() - float(elapsed[-1])) < 1e-9
    assert s.ideal_donor_lap_id() == lap, s.ideal_donor_lap_id()
    assert s.ideal_segment_bests().single_donor_id() == lap
    _x, dy = s.delta_to_ideal([lap], "distance")[lap]
    assert np.all(np.abs(dy) < 1e-6)
    print("test_ideal_single_donor_is_detectable_not_a_silent_duplicate OK")


def test_ideal_none_without_a_corner_partition():
    """No corner partition → every ideal accessor returns None, and `theoretical_best` with it.
    A lap with no detected corner is ONE segment, whose minimum over the laps is just the best lap
    time — the exact degeneracy this replaced. It is not returned dressed as an ideal; callers
    hide it, following stats_panel/export_data's precedent for a degenerate synthesized value."""
    s, _ids = make_ideal_session()
    reset_corner_caches(s, basis=None)
    assert s.ideal_segment_bests() is None
    assert s.ideal_lap_elapsed() is None
    assert s.ideal_total() is None
    assert s.ideal_donor_lap_id() is None
    assert s.theoretical_best() is None
    assert s.delta_to_ideal([2], "distance") is None
    assert s.ideal_delta_to_best("distance") is None
    # and with no laps at all
    s2 = bare_session(valid=[])
    s2._best_cache = None
    s2.laps = SimpleNamespace(laps_count=lambda: 0)
    assert s2.ideal_lap_elapsed() is None
    assert s2.ideal_total() is None
    print("test_ideal_none_without_a_corner_partition OK")


def test_theoretical_best_is_the_ideal_lap_one_definition():
    """`Bests.theoretical_best` IS `Session.ideal_total` — one definition of the ideal lap for
    every surface, by delegation rather than by a second computation that could drift. The
    session-best SECTOR splits are unchanged and still the per-column minimum; they just no longer
    define the ideal, so a 0-sector session no longer reports its own best lap as its target."""
    s, ids = make_ideal_session()
    assert s.theoretical_best() == s.ideal_total()
    assert s.theoretical_best() < min(_lap_times(s, ids).values()) - 1e-9
    # The purple cells still work, and with no sector line they are still [best lap time] — which
    # is exactly why they cannot be the ideal.
    splits = s.session_best_splits()
    assert len(splits) == 1
    assert abs(splits[0] - min(_lap_times(s, ids).values())) < 1e-9
    assert s.theoretical_best() < splits[0] - 1e-9
    print("test_theoretical_best_is_the_ideal_lap_one_definition OK")


def test_ideal_sample_counts_what_the_composite_was_minimised_over():
    """`SegmentBests.sample` / `Session.ideal_sample()` — the four counts every surface printing
    the ideal now prints with it, and the arithmetic that ties them to the partition.

    ONE accessor, because three surfaces print these numbers (the Stats block's tile caption and
    sample line, the hero's `vs ideal` chip, and — as `lap_count` — the Library's Laps column) and
    two of them used to derive nothing at all. `segments == 2 * corners + 1` is not decoration: it
    is the identity that makes "N corners and N+1 straights" a true sentence about a partition
    whose pieces tile the lap, and the sample line prints the straight count as
    `segments - corners` so a partition that ever stopped satisfying it would print the truth
    rather than a derived lie."""
    s, ids = make_ideal_session()
    sb = s.ideal_segment_bests()
    smp = s.ideal_sample()
    assert smp == sb.sample
    assert smp.donors == len(sb.donor_ids()) and smp.donors > 1   # genuinely stitched
    assert smp.laps == len(sb.lap_ids) == len(ids)
    assert smp.corners == len(sb.cids) == 2
    assert smp.segments == len(sb.bests) == 2 * smp.corners + 1
    assert smp.donors <= smp.laps
    # It takes the SAME gate as every other ideal accessor: no partition, no sample. A surface
    # printing "stitched from 0 of your 0 clean laps" under a dash is the failure mode.
    s2, _ = make_ideal_session()
    reset_corner_caches(s2, basis=None)
    assert s2.ideal_total() is None and s2.ideal_sample() is None
    print("test_ideal_sample_counts_what_the_composite_was_minimised_over OK")


def test_the_ideal_falls_as_laps_are_added_which_is_why_the_sample_is_on_screen():
    """THE PREMISE OF THE WHOLE DISCLOSURE, pinned: the composite is an ORDER STATISTIC over the
    session's clean laps, so dropping a lap can only make it slower or leave it alone — and adding
    one can only make it faster. A "theoretical best" is therefore partly a measure of how long
    the session was, and the number on its own is not comparable between sessions.

    Measured on the owner's five recordings over random subsets of the clean laps (200 draws per
    N), `ideal_total` falls 0.068 / 0.174 / 0.194 / 0.241 / 0.384 s per DOUBLING of lap count with
    no plateau — on D24's three chapters the same driving reads a 0.90 s gap over 5 laps and a
    1.64 s gap over 65. That table is in corner_model.IdealSample; this is the property it rests
    on, in a form that fails if the composite ever stops being a minimum.

    Every proper subset here is checked, not one sample: with three crossing-pace laps that is all
    six of them, and each must also report its own lap count through `sample`."""
    s, ids = make_ideal_session()
    full = s.ideal_total()
    assert full is not None
    seen = 0
    for keep in ([0, 1], [0, 2], [1, 2], [0], [1], [2]):
        sub, _ = make_ideal_session()
        # The service's INJECTED valid-lap accessor — the seam `_clean_lap_ids` reads, i.e.
        # the same one a shorter recording moves. Re-seed the hand-built basis afterwards:
        # `invalidate()` (which seed_corner_basis calls) drops it along with everything else, and
        # a synthetic straight-line lap has no real curvature to re-detect.
        sub.corners._valid_lap_ids = lambda _k=list(keep): list(_k)
        seed_corner_basis(sub, _IDEAL_CORNERS)
        got = sub.ideal_total()
        smp = sub.ideal_sample()
        # `_clean_lap_ids` appends the best lap when the subset excluded it, so the count the
        # surface prints is the set actually minimised over — never the set that was asked for.
        assert smp.laps == len(set(keep) | {s.best_lap_id()}), (keep, smp)
        assert got >= full - 1e-12, (
            f"the ideal over {keep} came out {got:.9f}, FASTER than the ideal over all "
            f"{len(ids)} laps ({full:.9f}) — a minimum over fewer laps cannot be smaller")
        seen += 1
    assert seen == 6
    # …and the sentence a reader is given: strictly slower on at least one subset, i.e. the
    # disclosure is about a real difference and not a theoretical one.
    two = make_ideal_session()[0]
    two.corners._valid_lap_ids = lambda: [0, 2]
    seed_corner_basis(two, _IDEAL_CORNERS)
    assert two.ideal_total() > full + 1e-9, (two.ideal_total(), full)
    print("test_the_ideal_falls_as_laps_are_added_which_is_why_the_sample_is_on_screen OK")


def _distinct_total_ideal_session():
    """The crossing-pace fixture with DISTINCT lap totals (1000 / 1002 / 1004 m — 0.2 % drift,
    inside corners.NORMALIZED_DRIFT_MAX = 0.5 %, so the projection stays on its deterministic
    normalized branch). The distinct totals are what let the collapse test below identify ONE
    lap inside `project_boundaries`, which only ever sees the totals."""
    t0, d0 = odometer(122, 0.1, 100.0, 1000.0, lambda u: 0.8 + 1.4 * np.sin(u / 2))
    t1, d1 = odometer(120, 0.1, float(t0[-1]), 1004.0, lambda u: 2.2 - 1.4 * np.sin(u / 2))
    t2, d2 = odometer(118, 0.1, float(t1[-1]), 1002.0)
    s = bare_session({0: (t0, d0), 1: (t1, d1), 2: (t2, d2)}, best=2, valid=[0, 1, 2])
    for lid, (t, d) in ((0, (t0, d0)), (1, (t1, d1)), (2, (t2, d2))):
        seed_cols(s, lid, t, d)
    s.laps = SimpleNamespace(laps_count=lambda: 3,
                             lap_time=lambda i: float(s._dist_cache[i][2][-1]),
                             sectors=SimpleNamespace(sector_lines=[]))
    seed_corner_basis(s, _IDEAL_CORNERS, total=1002.0)
    return s, (0, 1, 2), {0: 1000.0, 1: 1004.0, 2: 1002.0}


def test_ideal_donor_admission_refuses_a_collapsed_segment_AND_its_inflated_neighbour():
    """A lap may only donate a segment whose window is a COMPARABLE PIECE OF TRACK.

    A projected boundary that lands short does two things at once, and the second one is why the
    admission rule is symmetric (`corner_model.MAX_DONOR_SPAN_DEV`): the segment it closes collapses
    (free 0 s nobody drove) and the segment it opens INFLATES by exactly the same width (time from
    the neighbouring piece of track banked as if it belonged here). The old one-sided
    `MIN_DONOR_SPAN_FRAC` floor caught only the first; on the D24 0060 pair the inflated halves ran
    to 1.24× the expected span and polluted the neighbouring corner's Δ.

    Injected here at the projection's own seam: the same pure function both `segment_times` and the
    admission span read, so the collapsed lap's time really does move to its neighbour."""
    base_s, _ids, totals = _distinct_total_ideal_session()
    honest = base_s.ideal_segment_bests().total

    j = 2                       # the straight between C1 and C2 — real on every lap
    victim = 1                  # the fast-early / slow-late lap
    real_project = corners_mod.project_boundaries

    # **kw, not an explicit signature: this stands in for the REAL projection, whose keyword set
    # grows (traces → frame → alignment). Spelling them out here made the stub silently stop
    # matching its subject — the previous version raised TypeError the moment segment_bests began
    # passing the lap's pre-built warp. Forward whatever the caller sends, verbatim.
    def collapsing(d_ref, total_ref, total_lap, **kw):
        out = np.asarray(real_project(d_ref, total_ref, total_lap, **kw), float)
        # edges = [0, *out, total_lap], so segment j spans out[j-1]..out[j]. Pull the far edge
        # back onto the near one: span exactly 0, which is the clamp's signature.
        if abs(total_lap - totals[victim]) < 1e-6 and len(out) > j:
            out = out.copy()
            out[j] = out[j - 1]
        return out

    corners_mod.project_boundaries = collapsing
    try:
        s2, _ids2, _t = _distinct_total_ideal_session()
        got = s2.ideal_segment_bests()
        assert got is not None
        row = got.lap_ids.index(victim)
        assert abs(float(got.times[row, j])) < 1e-12, "fixture must actually collapse the segment"
        assert not got.admitted[row, j], "the collapsed cell must be refused"
        assert got.donors[j] != victim, "a collapsed cell must not win its segment"
        assert got.bests[j] > 0.0, "the segment best must come from a lap that drove it"
        # Refusing it keeps the total honest — it never claims MORE than the uncollapsed run.
        assert got.total >= honest - 1e-9, (got.total, honest)
        # Without the guard the free 0 would have been taken: the naive min IS strictly smaller.
        naive = float(got.times.min(axis=0).sum())
        assert naive < got.total - 1e-6, (naive, got.total)
        # …and the OTHER half of the same displacement: segment j+1 opened early, so it is wider
        # than this lap's own expected span and is refused too. (This is the assertion that moved
        # when the floor became symmetric — under MIN_DONOR_SPAN_FRAC the inflated cell donated.)
        assert not got.admitted[row, j + 1], "the inflated neighbour must be refused as well"
        assert got.donors[j + 1] != victim
        # The victim still donates every segment it did drive — the refusal is per CELL, not per
        # lap, so one bad projection does not throw away a whole lap's evidence.
        assert got.admitted[row].sum() == got.times.shape[1] - 2
        assert got.admitted[row].sum() >= got.times.shape[1] - 2 > 0
    finally:
        corners_mod.project_boundaries = real_project
    print("test_ideal_donor_admission_refuses_a_collapsed_segment_AND_its_inflated_neighbour OK")


# The admission band this file asserts against, as a LITERAL. Reading
# `corner_model.MAX_DONOR_SPAN_DEV` here would move the assertion with the very constant it exists
# to hold: the first version of the guard below did exactly that and passed with the constant at
# 0.50 AND at 0.0. Update this deliberately, and only with the measurement that justifies it.
_ADMISSION_BAND = 0.05


def test_a_shrunken_window_cannot_win_a_segment_however_fast_it_reads():
    """THE ADMISSION HALF'S GUARD, in the flagship number's own failure shape.

    The defect that shipped for a week was not a COLLAPSE — the test above covers that — but a
    SHRINK: on the D24 0060 pair the winner of `C5 → C6` had been measured over 31 m of a 42.8 m
    straight and won the segment on the missing 11 m. Neither the partition identity
    (`segment_times`' assertion) nor the decomposition sum could see it; both still held exactly,
    because the lost time had moved into the neighbouring segment. The only thing that can see it
    is a check on the WINDOW.

    Injected at the projection's seam: one lap's window for segment j is narrowed 20 %. The test
    FIRST asserts that this makes its time there the fastest in the session — so the guard can
    never be vacuously satisfied by a fixture where nothing is trying to win — and then asserts
    that it does not win, is not admitted, and that the segment's actual winner was measured on a
    window within `_ADMISSION_BAND` of ITS OWN expected span, read back from `donor_span` (the
    field the ideal CURVE is drawn from, not the bool the admission wrote).

    SCOPE: the admission half only. This test replaces the projection, so it cannot see a frame
    regression; that is
    tests/test_corners.py::test_projection_never_mixes_two_frames_within_one_lap's job. Between
    them they cover the two halves, and neither covers the other's."""
    shrink = 0.20
    j = 2                       # the straight between C1 and C2 — real on every lap
    victim = 1                  # the fast-early / slow-late lap
    _base_s, _ids, totals = _distinct_total_ideal_session()
    real_project = corners_mod.project_boundaries

    def shrinking(d_ref, total_ref, total_lap, **kw):
        out = np.asarray(real_project(d_ref, total_ref, total_lap, **kw), float)
        # edges = [0, *out, total_lap], so segment j spans out[j-1]..out[j]. Pull the far edge in
        # by 20 % of the segment: a real, non-degenerate window that is simply too short.
        if abs(total_lap - totals[victim]) < 1e-6 and len(out) > j:
            out = out.copy()
            out[j] -= shrink * (out[j] - out[j - 1])
        return out

    corners_mod.project_boundaries = shrinking
    try:
        s2, _ids2, _t = _distinct_total_ideal_session()
        got = s2.ideal_segment_bests()
        assert got is not None
        row = got.lap_ids.index(victim)
        col = got.times[:, j]
        # NON-VACUITY: the shrunken cell really is the fastest reading of that segment, so the
        # only thing standing between it and the composite is the admission rule.
        assert col[row] == col.min() and (col[row] < np.delete(col, row)).all(), (
            "fixture must make the shrunken cell the fastest — otherwise this proves nothing")
        assert not got.admitted[row, j], "a 20 %-short window must not be admitted"
        assert got.donors[j] != victim, "…and must not win the segment"

        total_ref = s2.corners.basis()[1]
        ref_span = np.diff(np.asarray(got.s_edges, float) * total_ref)
        worst, checked = 0.0, 0
        for k, donor in enumerate(got.donors):
            if donor is None or ref_span[k] <= corner_model.POINT_SPAN_M:
                continue                 # a POINT segment admits everyone by design
            lo, hi = got.donor_span[k]
            dist, _sp, _el = s2._lap_arrays(donor)
            expected = ref_span[k] * (float(dist[-1]) / total_ref)
            dev = abs((hi - lo) - expected) / expected
            worst = max(worst, dev)
            checked += 1
            assert dev <= _ADMISSION_BAND + 1e-9, (
                f"segment {k} ({got.display_label(k)}) was won on a {hi - lo:.2f} m window where "
                f"the donor's own expected span is {expected:.2f} m ({dev * 100:.1f} % off)")
        assert checked, "fixture must have real segments to check"
    finally:
        corners_mod.project_boundaries = real_project
    print(f"test_a_shrunken_window_cannot_win_a_segment_however_fast_it_reads OK "
          f"(shrunken cell {col[row]:.3f}s beat every other lap and was still refused; "
          f"{checked} winners within {_ADMISSION_BAND * 100:.0f} %, worst {worst * 100:.2f} %)")


# --- the DECOMPOSITION: from taunt into plan (N8) -----------------------------------------
#
# A lap time you never drove, with nothing attached, is a taunt. These pin the three things that
# turn it into a plan: the gains ADD UP to the headline, the achievability count means what it
# says, and the ranking puts the repeatable gain above the lucky one.


def test_decomposition_gains_sum_to_the_headline_gap():
    """The arithmetic the Stats page prints under the tile: summed over EVERY segment, the best
    lap's gains equal `best lap time − ideal_total` exactly. It holds because each lap's segment
    times sum exactly to its lap time, so `Σ(own − best) = own_total − Σbest`.

    This is what makes a top-N slice of the table safe to show: the remainder is computable, and
    the page states it. A decomposition that only approximately summed would make the table and
    the tile above it disagree — the defect class `_set_digest`'s rounding note records."""
    s, ids = make_ideal_session()
    sb = s.ideal_segment_bests()
    best = s.best_lap_id()
    rows = sb.decomposition(best)
    assert rows is not None and len(rows) == len(sb.bests), rows
    headline = _lap_times(s, ids)[best] - sb.total
    assert headline > 1e-3, headline
    assert abs(sum(r.gain for r in rows) - headline) < 1e-9, (sum(r.gain for r in rows), headline)
    # Every gain is a real deficit, never negative: the segment best is a minimum OVER a set the
    # subject belongs to.
    assert all(r.gain >= -1e-12 for r in rows), [r.gain for r in rows]
    # …and every OTHER lap's decomposition sums to its own gap, not just the best lap's.
    for lid in ids:
        other = sb.decomposition(lid)
        assert abs(sum(r.gain for r in other)
                   - (_lap_times(s, ids)[lid] - sb.total)) < 1e-9, lid
    assert sb.decomposition(999) is None, "a lap outside the composite has no decomposition"
    print("test_decomposition_gains_sum_to_the_headline_gap OK")


def test_beat_counts_are_not_a_fixed_tolerance_hit_rate():
    """ACHIEVABILITY, and why it is not `hit_counts(0.1)`.

    The count is 'how many clean laps drove this segment at least as fast as the subject did'.
    Ties count, so the subject always counts itself and the number is never 0; it is bounded by
    the laps ADMITTED on that segment; and — the property the fixed tolerance did not have — it
    is invariant under a change of the segment's DURATION.

    That last one is the whole reason `hit_counts(tol)` was replaced. A fixed 0.1 s window over
    segments running 0.16 s to 10.07 s on the real recordings covered 62 % of one and 1 % of
    another, and its 'achievability' tracked segment duration at r = -0.95 / -0.82 / -0.80 /
    -0.95 across the four. Here the same laps are re-timed with every segment scaled by 3x and
    the counts must not move."""
    s, ids = make_ideal_session()
    sb = s.ideal_segment_bests()
    best = s.best_lap_id()
    counts = sb.beat_counts(best)
    assert counts is not None and len(counts) == len(sb.bests)
    own = sb.times[sb.lap_ids.index(best)]
    for j, (hit, n) in enumerate(counts):
        assert 1 <= hit <= n <= len(sb.lap_ids), (j, hit, n)
        assert n == int(sb.admitted[:, j].sum()), j
        expect = int(((sb.times[:, j] <= own[j]) & sb.admitted[:, j]).sum())
        assert hit == expect, (j, hit, expect)
    # Scale-invariance: 3x every segment time (a slower track, identical relative driving) and
    # the counts are unchanged. `hit_counts(0.1)` could not survive this — a 0.1 s window over
    # 3x-longer segments admits a strictly different set.
    scaled = SegmentBests(labels=sb.labels, cids=sb.cids, lap_ids=sb.lap_ids,
                          times=sb.times * 3.0, admitted=sb.admitted,
                          bests=[b * 3.0 for b in sb.bests], donors=sb.donors,
                          s_edges=sb.s_edges, donor_span=sb.donor_span)
    assert scaled.beat_counts(best) == counts

    # NEGATIVE CONTROL, on a column built to make the difference unmistakable: three laps at
    # 1.00 / 1.05 / 1.20 s through one segment. A fixed 0.1 s window round the best admits two
    # of them; stretch the same driving 3x (3.00 / 3.15 / 3.60) and it admits ONE, because 0.1 s
    # is now a third of the window it was. `beat_counts` reports 3 both times — the subject is
    # the slowest, and all three are at least as fast as it, at either scale.
    col = np.array([[1.00], [1.05], [1.20]])
    one = SegmentBests(labels=["a"], cids=[], lap_ids=[0, 1, 2], times=col,
                       admitted=np.ones((3, 1), bool), bests=[1.00], donors=[0],
                       s_edges=[0.0, 1.0], donor_span=[(0.0, 0.0)])
    three = SegmentBests(labels=["a"], cids=[], lap_ids=[0, 1, 2], times=col * 3.0,
                         admitted=np.ones((3, 1), bool), bests=[3.00], donors=[0],
                         s_edges=[0.0, 1.0], donor_span=[(0.0, 0.0)])
    assert one.beat_counts(2) == three.beat_counts(2) == [(3, 3)]
    fixed_1x = int((col[:, 0] <= 1.00 + 0.1).sum())
    fixed_3x = int(((col[:, 0] * 3.0) <= 3.00 + 0.1).sum())
    assert (fixed_1x, fixed_3x) == (2, 1), (fixed_1x, fixed_3x)
    assert sb.beat_counts(999) is None
    print("test_beat_counts_are_not_a_fixed_tolerance_hit_rate OK")


def test_decomposition_ranks_a_repeatable_gain_above_a_lucky_one():
    """THE RANKING, and the reason it is not raw gain. Two segments, hand-built:

      * a BIG gain nobody repeats — 0.30 s, and exactly one lap of ten matched the subject.
      * a SMALLER gain the driver makes routinely — 0.20 s, matched on eight laps of ten.

    Ranked by gain the taunt wins. Ranked by gain × beat/n the plan does: 0.20 × 0.8 = 0.160
    beats 0.30 × 0.1 = 0.030. Both factors are columns on the Stats page, so the order is
    checkable by eye against the two numbers beside it.

    Measured on the owner's own recordings this is not a hypothetical: D24's largest single gain
    (C2, 0.213 s) and Sandown's (C5, 0.224 s) are both outranked, and on Sandown C5 falls to
    third behind two gains 0.05-0.09 s smaller that were matched on 22 of 59 laps against its 13.
    """
    n_laps, n_seg = 10, 2
    times = np.zeros((n_laps, n_seg))
    # segment 0: the subject (lap 0) is 0.30 s off the best, and only lap 1 also beat it.
    times[:, 0] = 10.0
    times[0, 0] = 10.0
    times[1, 0] = 9.70
    times[2:, 0] = 10.5
    # segment 1: the subject is 0.20 s off, and eight of the ten are at least as quick.
    times[:, 1] = 5.00
    times[0, 1] = 5.20
    times[1, 1] = 5.00
    times[2:9, 1] = 5.05
    times[9, 1] = 5.40
    sb = SegmentBests(labels=["a", "b"], cids=[1], lap_ids=list(range(n_laps)), times=times,
                      admitted=np.ones((n_laps, n_seg), bool),
                      bests=[float(times[:, 0].min()), float(times[:, 1].min())],
                      donors=[1, 1], s_edges=[0.0, 0.5, 1.0], donor_span=[(0.0, 0.0)] * n_seg)
    rows = sb.decomposition(0)
    big = next(r for r in rows if r.index == 0)
    repeatable = next(r for r in rows if r.index == 1)
    assert abs(big.gain - 0.30) < 1e-9 and (big.beat, big.n) == (2, 10), big
    assert abs(repeatable.gain - 0.20) < 1e-9 and (repeatable.beat, repeatable.n) == (9, 10)
    assert big.gain > repeatable.gain, "the fixture must make the taunt the BIGGER gain"
    assert rows[0] is repeatable, [(r.index, r.priority) for r in rows]
    assert repeatable.priority > big.priority
    # …and the retired ordering really would have got it wrong (negative control).
    assert sorted(rows, key=lambda r: -r.gain)[0] is big
    print("test_decomposition_ranks_a_repeatable_gain_above_a_lucky_one OK")


def test_decomposition_drops_a_segment_the_subject_never_drove():
    """A cell the SUBJECT is not admitted on carries a time it did not drive (the collapse
    `MIN_DONOR_SPAN_FRAC` exists for), so its gain is measured against a fiction and its
    neighbour's is inflated by the same amount. The pair still sums correctly — the headline is
    safe — but neither number is advice, so the row is dropped rather than shown."""
    times = np.array([[10.0, 0.0, 5.0], [9.0, 2.0, 5.5], [11.0, 1.8, 4.0]])
    admitted = np.ones((3, 3), bool)
    admitted[0, 1] = False                       # the subject's collapsed cell
    sb = SegmentBests(labels=["a", "b", "c"], cids=[1], lap_ids=[0, 1, 2], times=times,
                      admitted=admitted,
                      bests=[9.0, 1.8, 4.0], donors=[1, 2, 2],
                      s_edges=[0.0, 0.3, 0.6, 1.0], donor_span=[(0.0, 0.0)] * 3)
    rows = sb.decomposition(0)
    assert [r.index for r in sorted(rows, key=lambda r: r.index)] == [0, 2], rows
    # …and lap 1, which IS admitted everywhere, still gets all three.
    assert len(sb.decomposition(1)) == 3
    print("test_decomposition_drops_a_segment_the_subject_never_drove OK")


def test_display_labels_are_the_straights_tables_own_spelling():
    """The decomposition and the STRAIGHTS table list the SAME pieces of track a few hundred
    pixels apart on one page, so they must spell them the same way. `SegmentBests.display_label`
    is pinned against `stats.straights_report`'s labels rather than re-deriving the convention:
    corners are "C4", straights are "C3 → C4", and the timing line is "S/F" at both ends —
    including the WRAP, where the S/F straight is fed by the LAST corner (`ring_cid`)."""
    s, _ids = make_ideal_session()
    sb = s.ideal_segment_bests()
    report = s.straights_report()
    assert report, "fixture must produce straights to compare against"
    assert [sb.display_label(j) for j in range(0, len(sb.bests), 2)] == [r.label for r in report]
    assert [sb.ring_cid(j) for j in range(0, len(sb.bests), 2)] == [r.ring_cid for r in report]
    # The odd entries are the corners, named and pointing at themselves.
    for j in range(1, len(sb.bests), 2):
        cid = sb.cids[(j - 1) // 2]
        assert sb.display_label(j) == f"C{cid}" and sb.ring_cid(j) == cid, j
    print("test_display_labels_are_the_straights_tables_own_spelling OK")


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


def test_session_best_splits_not_poisoned_by_degenerate_lap():
    """D11 headline: a single lap with a degenerate (here near-zero) split must NOT drag the
    per-column session best toward 0.

    The session-best columns computed WITH a degenerate lap present equal those computed from the
    same valid laps with the degenerate lap excluded (the > 0 filter in session_best_splits
    ignores its poisoned column entry).

    `theoretical_best` no longer reads these columns at all — it is the corner-partition ideal
    (see test_theoretical_best_is_the_ideal_lap_one_definition), so a poisoned SPLIT can no longer
    reach the headline target by that route either. The sum is still checked here because it is
    what the lap table's purple row shows."""
    s, lap_a, lap_b = make_two_lap_sector_session()  # 2 lines -> 3 fully-filled columns
    clean_bests = s.session_best_splits()
    clean_theo = float(sum(clean_bests))
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
        poisoned_theo = float(sum(poisoned_bests))
    finally:
        s.lap_sector_splits = orig
        s._valid_cache = [lap_a, lap_b]

    # The degenerate lap's 0 s column is filtered, so the per-column bests + theoretical best are
    # IDENTICAL to the clean two-lap session — the poison never reaches the purple cells / footer.
    assert poisoned_bests == clean_bests, (poisoned_bests, clean_bests)
    assert poisoned_theo == clean_theo, (poisoned_theo, clean_theo)
    # And concretely: the middle column's best is a real positive split, not the injected 0.
    assert poisoned_bests[1] > 0.0, poisoned_bests
    print("test_session_best_splits_not_poisoned_by_degenerate_lap OK")


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
    # theoretical_best is the corner-partition ideal now, not this sum — and this fixture has no
    # corner basis, so it is None rather than a 30.000001 s "target" nobody could aim at.
    assert s.theoretical_best() is None
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
    # The ideal excludes the dropout lap by the SAME rule (CornerModel._clean_lap_ids); with
    # no corner basis on this fixture there is no partition, so it reports None.
    assert s.theoretical_best() is None

    # Fallback: when both laps are dropouts the set degrades to all valid, best = fastest.
    s2 = _two_lap_dropout_session()
    s2.lap_has_dropout = lambda lid: True
    assert s2._best_candidate_ids() == [0, 1]
    assert s2.best_lap_id() == 0
    print("test_best_excludes_dropout_lap_then_falls_back OK")


def test_heuristic_start_base_perpendicular_at_peak_speed():
    """The unknown-track heuristic places the start/finish line PERPENDICULAR to travel at the
    peak-speed point (the main straight). A straight along +x with the peak in the middle → a
    vertical (constant-x) line centred on the peak, spanning 2·_HEURISTIC_HALF_M."""
    n = 41
    xs = np.linspace(0.0, 100.0, n)
    ys = np.zeros(n)
    speeds = np.ones(n)
    speeds[20] = 50.0                       # peak at the middle sample (x = 50)
    seg = _heuristic_start_base(xs, ys, speeds)
    assert seg is not None
    # Centred on the peak point (50, 0)…
    assert abs((seg.first.x + seg.second.x) / 2 - 50.0) < 1e-6
    assert abs((seg.first.y + seg.second.y) / 2 - 0.0) < 1e-6
    # …perpendicular to the +x heading, so the line is vertical (both endpoints at x = 50)…
    assert abs(seg.first.x - 50.0) < 1e-6 and abs(seg.second.x - 50.0) < 1e-6
    # …and spans 2·half in y.
    assert abs(abs(seg.first.y - seg.second.y) - 2 * _HEURISTIC_HALF_M) < 1e-6
    print("test_heuristic_start_base_perpendicular_at_peak_speed OK")


def test_heuristic_start_base_degenerate_returns_none():
    """Too few samples, or no local heading (all points coincide), → None so the caller falls back
    to the old random pick (never a crash / zero-length line)."""
    assert _heuristic_start_base(np.zeros(3), np.zeros(3), np.ones(3)) is None  # too few samples
    n = 41
    assert _heuristic_start_base(np.zeros(n), np.zeros(n), np.ones(n)) is None  # no heading
    print("test_heuristic_start_base_degenerate_returns_none OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SESSION-PURE TESTS PASSED")
