"""Session-statistics tests (studio/stats.py) — pure reducers + the SessionStats service.

Pins the Stats-page math on synthetic inputs (no telemetry file, no Qt):
  * moving_time_s — leading-sample attribution, the >= threshold edge, and the
    MAX_SAMPLE_GAP_S dropout-skip (a gap while moving counts as NEITHER moving nor stopped);
  * path_distance_m — exact chord sum on a known polyline, empty/degenerate → 0;
  * clock_hhmm — the GPS5 zero-sentinel → None, and the local-clock rendering (pinned
    against datetime on the same epoch, the same LOCAL convention as session_date);
  * pace_stats — best/median/spread exactness + σ == np.std(ddof=1) (via consistency.sigma),
    NaN filtering, empty → None;
  * peak_g — |lateral| peak, braking = most NEGATIVE longitudinal reported positive,
    floored at 0 for an all-throttle span, empty → (None, None);
  * in_windows_mask — the half-open [t0, t1) lap-window convention + multi-window union;
  * sector_medians — ragged-row column convention shared with consistency.sector_sigmas;
  * SessionStats — the service over fake DI callables: totals (duration incl. gaps, moving
    excl. gaps, distance, wall clocks, caching), lap_stats (speed stats from the lap arrays,
    g peaks sliced by lap window, brake/coast reductions; None — not 0 — without a g signal),
    session_vmax, gg_cloud (valid-window restriction + the stride cap + no-g → None), and
    invalidate() dropping exactly the lap-level caches (totals survive, like the driving
    thresholds surviving a re-segment);
  * the Session.stats property wiring on a bare Session (lazy build + degenerate trace).
Run: python tests/test_stats.py
"""
import datetime
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import bare_session, seed_cols  # noqa: E402

from studio.stats import (  # noqa: E402
    MOVING_MS,
    SessionStats,
    clock_hhmm,
    in_windows_mask,
    moving_time_s,
    pace_stats,
    path_distance_m,
    peak_g,
    sector_medians,
)


# ------------------------------------------------------------------- pure reducers
def test_moving_time_attributes_leading_sample_and_skips_gaps():
    # 0.1 s cadence; the 3rd interval is a 5 s dropout gap while MOVING -> skipped entirely.
    times = np.array([0.0, 0.1, 0.2, 5.2, 5.3, 5.4])
    fast, slow = MOVING_MS + 1.0, MOVING_MS - 1.0
    speed = np.array([fast, slow, fast, fast, slow, fast])
    # kept intervals: [0,0.1) fast=0.1, [0.1,0.2) slow=0, gap skipped, [5.2,5.3) fast=0.1,
    # [5.3,5.4) slow=0 -> 0.2 s total.
    assert abs(moving_time_s(times, speed) - 0.2) < 1e-12
    # the threshold edge: exactly AT the threshold counts as moving (>=)
    assert abs(moving_time_s([0.0, 1.0], [MOVING_MS, 0.0]) - 1.0) < 1e-12
    # degenerate: <2 samples
    assert moving_time_s([0.0], [fast]) == 0.0
    print("test_moving_time_attributes_leading_sample_and_skips_gaps OK")


def test_path_distance_is_the_chord_sum():
    # A 3-4-5 right triangle traversed as two chords: 3 + 5 = 8 m.
    xs = [0.0, 3.0, 0.0]
    ys = [0.0, 0.0, 4.0]
    assert abs(path_distance_m(xs, ys) - 8.0) < 1e-12
    assert path_distance_m([], []) == 0.0
    assert path_distance_m([1.0], [1.0]) == 0.0
    print("test_path_distance_is_the_chord_sum OK")


def test_clock_hhmm_local_rendering_and_gps5_sentinel():
    assert clock_hhmm(0) is None      # GPS5 / empty stream sentinel — same rule as session_date
    assert clock_hhmm(-5) is None
    ms = 1_750_000_000_000  # a fixed epoch; rendering must match datetime's LOCAL clock
    assert clock_hhmm(ms) == datetime.datetime.fromtimestamp(ms / 1000.0).strftime("%H:%M")
    print("test_clock_hhmm_local_rendering_and_gps5_sentinel OK")


def test_pace_stats_exact_and_nan_filtered():
    p = pace_stats([68.0, 69.0, 70.0, math.nan])
    assert p is not None and p.n == 3
    assert p.best == 68.0 and p.median == 69.0 and abs(p.spread - 1.0) < 1e-12
    assert abs(p.sigma - float(np.std([68.0, 69.0, 70.0], ddof=1))) < 1e-12
    assert pace_stats([]) is None
    assert pace_stats([math.nan]) is None
    single = pace_stats([70.0])
    assert single is not None and single.sigma is None and single.spread == 0.0
    print("test_pace_stats_exact_and_nan_filtered OK")


def test_peak_g_conventions():
    lat_pk, brake_pk = peak_g([-1.2, 0.5, 0.9], [-0.9, 0.3, 0.1])
    assert abs(lat_pk - 1.2) < 1e-12          # |lateral| peak, sign-blind
    assert abs(brake_pk - 0.9) < 1e-12        # most negative longitudinal, reported positive
    # all-throttle: no negative longitudinal -> 0 braking, never negative
    _lat, brake = peak_g([0.1], [0.2, 0.4])
    assert brake == 0.0
    assert peak_g([], [1.0]) == (None, None)
    print("test_peak_g_conventions OK")


def test_in_windows_mask_half_open_union():
    t = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    m = in_windows_mask(t, [(1.0, 3.0), (4.0, 4.5)])
    # t0 inclusive, t1 EXCLUSIVE (a lap's end instant belongs to the next lap)
    assert list(m) == [False, True, True, False, True, False]
    assert not in_windows_mask(t, []).any()
    print("test_in_windows_mask_half_open_union OK")


def test_sector_medians_column_convention():
    rows = [[10.0, 20.0], [12.0, 22.0], [14.0]]  # ragged: lap 3 is partial
    med = sector_medians(rows)
    assert med == [12.0, 21.0]                    # col 0 over 3 laps, col 1 over 2
    assert sector_medians([]) == []
    assert sector_medians([[math.nan]]) == [None]  # a column with no finite split
    print("test_sector_medians_column_convention OK")


# --------------------------------------------------------------- the SessionStats service
def _fake_gmeter(times, lat_g, long_g, long_g_gps=None):
    return SimpleNamespace(has_data=len(times) > 0, times=np.asarray(times, float),
                           lat_g=np.asarray(lat_g, float), long_g=np.asarray(long_g, float),
                           long_g_gps=(None if long_g_gps is None
                                       else np.asarray(long_g_gps, float)))


def _service(*, gm=None, trace_t=(), trace_v_kmh=(), xs=(), ys=(), wall=(0, 0),
             valid=(), cons=None, lap_times=None, arrays=None, windows=None,
             events=None, spans=None):
    """A SessionStats over plain fake callables — the same DI seam Session wires."""
    gm = gm if gm is not None else _fake_gmeter([], [], [])
    lap_times = lap_times or {}
    arrays = arrays or {}
    windows = windows or {}
    events = events or {}
    spans = spans or {}
    return SessionStats(
        gmeter=lambda: gm,
        trace_times=lambda: np.asarray(trace_t, float),
        trace_speed_kmh=lambda: np.asarray(trace_v_kmh, float),
        trace_xy=lambda: (np.asarray(xs, float), np.asarray(ys, float)),
        wall_clock_ms=lambda: wall,
        valid_lap_ids=lambda: list(valid),
        consistency_lap_ids=lambda: list(cons if cons is not None else valid),
        lap_time=lambda i: lap_times[i],
        lap_arrays=lambda i: arrays[i],
        lap_window=lambda i: windows.get(i),
        brake_events=lambda i: events.get(i, []),
        coast_spans=lambda i: spans.get(i, []),
    )


def test_totals_duration_moving_distance_and_clocks():
    fast = (MOVING_MS + 2.0) * 3.6  # km/h, comfortably moving
    st = _service(
        trace_t=[0.0, 0.1, 0.2, 10.2],           # last step is a 10 s gap -> duration keeps it,
        trace_v_kmh=[fast, 0.0, 0.0, fast],      # moving time skips it (and the two slow leads)
        xs=[0.0, 3.0, 3.0, 3.0], ys=[0.0, 0.0, 4.0, 4.0],
        wall=(1_750_000_000_000, 1_750_000_600_000),
    )
    tot = st.totals()
    assert abs(tot.duration_s - 10.2) < 1e-9          # recorded span includes the gap
    assert abs(tot.moving_s - 0.1) < 1e-9             # only the first (moving) 0.1 s interval
    assert abs(tot.distance_m - 7.0) < 1e-9           # 3 + 4 + 0 chords
    assert tot.start_clock == clock_hhmm(1_750_000_000_000)
    assert tot.end_clock == clock_hhmm(1_750_000_600_000)
    assert st.totals() is tot                          # cached (the trace never changes)
    print("test_totals_duration_moving_distance_and_clocks OK")


def test_totals_degenerate_empty_trace():
    tot = _service().totals()
    assert tot.duration_s == 0.0 and tot.moving_s == 0.0 and tot.distance_m == 0.0
    assert tot.start_clock is None and tot.end_clock is None  # the GPS5/empty sentinel
    print("test_totals_degenerate_empty_trace OK")


def test_lap_stats_speed_g_and_brake_coast_reductions():
    # Lap 0 on media window [100, 170): g series peaks inside it; lap 1 [200, 268) quieter.
    gm = _fake_gmeter(times=[100.0, 150.0, 210.0, 250.0],
                      lat_g=[-1.4, 0.6, 0.8, -0.3],
                      long_g=[9.9, 9.9, 9.9, 9.9],          # IMU long: junk, must NOT be read
                      long_g_gps=[-1.1, 0.2, -0.5, 0.1])    # the validated signal
    arrays = {
        0: (np.array([0.0, 500.0, 1000.0]), np.array([60.0, 95.0, 70.0]),
            np.array([0.0, 30.0, 70.0])),
        1: (np.array([0.0, 500.0, 1020.0]), np.array([58.0, 90.0, 72.0]),
            np.array([0.0, 31.0, 68.0])),
    }
    st = _service(
        gm=gm, valid=[0, 1],
        lap_times={0: 70.0, 1: 68.0},
        arrays=arrays,
        windows={0: (100.0, 170.0), 1: (200.0, 268.0)},
        events={0: [SimpleNamespace(duration=1.5), SimpleNamespace(duration=0.5)],
                1: [SimpleNamespace(duration=2.0)]},
        spans={0: [SimpleNamespace(duration=3.5)], 1: []},
    )
    rows = st.lap_stats()
    assert [r.idx for r in rows] == [0, 1]
    r0, r1 = rows
    assert r0.time == 70.0 and r0.vmax_kmh == 95.0
    assert abs(r0.avg_kmh - 1000.0 / 70.0 * 3.6) < 1e-9   # odometer / lap time
    assert abs(r0.peak_lat_g - 1.4) < 1e-12               # window [100,170) -> samples 0+1
    assert abs(r0.peak_brake_g - 1.1) < 1e-12             # from long_g_gps, NOT the junk IMU long
    assert r0.brake_s == 2.0 and r0.brake_n == 2
    assert r0.coast_s == 3.5 and abs(r0.coast_frac - 3.5 / 70.0) < 1e-12
    assert abs(r1.peak_brake_g - 0.5) < 1e-12             # window [200,268) -> samples 2+3
    assert r1.coast_s == 0.0 and r1.coast_frac == 0.0     # a real zero WITH a g signal
    assert st.lap_stats() is rows                          # cached per segmentation
    print("test_lap_stats_speed_g_and_brake_coast_reductions OK")


def test_lap_stats_no_g_signal_reports_none_not_zero():
    st = _service(valid=[0], lap_times={0: 70.0},
                  arrays={0: (np.array([0.0, 1000.0]), np.array([60.0, 90.0]),
                              np.array([0.0, 70.0]))})
    (r,) = st.lap_stats()
    assert r.vmax_kmh == 90.0                       # speed stats need no g signal
    assert r.peak_lat_g is None and r.peak_brake_g is None
    assert r.brake_s is None and r.brake_n is None  # None (unknown), never a fake 0
    assert r.coast_s is None and r.coast_frac is None
    print("test_lap_stats_no_g_signal_reports_none_not_zero OK")


def test_session_vmax_picks_the_fastest_lap():
    st = _service(valid=[3, 7], lap_times={3: 70.0, 7: 68.0},
                  arrays={3: (np.array([0.0, 1000.0]), np.array([60.0, 97.5]),
                              np.array([0.0, 70.0])),
                          7: (np.array([0.0, 1000.0]), np.array([60.0, 91.0]),
                              np.array([0.0, 68.0]))})
    assert st.session_vmax() == (97.5, 3)
    assert _service().session_vmax() is None
    print("test_session_vmax_picks_the_fastest_lap OK")


def test_gg_cloud_window_restriction_and_stride():
    n = 1000
    times = np.linspace(0.0, 100.0, n)
    gm = _fake_gmeter(times=times, lat_g=np.ones(n), long_g=np.full(n, 9.9),
                      long_g_gps=np.full(n, -0.4))
    st = _service(gm=gm, valid=[0], lap_times={0: 50.0},
                  arrays={0: (np.array([0.0, 1.0]), np.array([0.0, 1.0]),
                              np.array([0.0, 50.0]))},
                  windows={0: (0.0, 50.0)})
    cloud = st.gg_cloud(max_points=100)
    assert cloud is not None
    lat, lon = cloud
    assert len(lat) <= 100 and len(lat) == len(lon)   # strided under the cap
    assert np.all(lon == -0.4)                        # the validated long axis, not the IMU junk
    # ~half the samples fall in the [0, 50) lap window; the stride ran over only those
    assert len(lat) >= 40
    # no g signal / no valid lap -> None
    assert _service(valid=[0]).gg_cloud() is None
    assert _service(gm=gm).gg_cloud() is None
    print("test_gg_cloud_window_restriction_and_stride OK")


def test_invalidate_drops_lap_level_keeps_totals():
    calls = {"valid": 0}

    def counting_valid():
        calls["valid"] += 1
        return [0]

    st = SessionStats(
        gmeter=lambda: _fake_gmeter([], [], []),
        trace_times=lambda: np.array([0.0, 1.0]),
        trace_speed_kmh=lambda: np.array([50.0, 50.0]),
        trace_xy=lambda: (np.array([0.0, 10.0]), np.array([0.0, 0.0])),
        wall_clock_ms=lambda: (0, 0),
        valid_lap_ids=counting_valid,
        consistency_lap_ids=lambda: [0],
        lap_time=lambda i: 70.0,
        lap_arrays=lambda i: (np.array([0.0, 1000.0]), np.array([60.0, 90.0]),
                              np.array([0.0, 70.0])),
        lap_window=lambda i: None,
        brake_events=lambda i: [],
        coast_spans=lambda i: [],
    )
    tot = st.totals()
    st.lap_stats()
    st.lap_stats()
    assert calls["valid"] == 1                # cached: one derivation
    st.invalidate()
    st.lap_stats()
    assert calls["valid"] == 2                # re-segment -> lap stats re-derive
    assert st.totals() is tot                 # …but the trace totals survive
    print("test_invalidate_drops_lap_level_keeps_totals OK")


# --------------------------------------------------------------- Session property wiring
def test_bare_session_stats_property_wires_the_service():
    """The Session.stats property lazily builds a real SessionStats over the session's own
    primitives — pinned on a bare Session with a degenerate trace (the seeding idiom)."""
    s = bare_session(valid=[0], best=0)
    s.tt = np.array([0.0, 1.0, 2.0])
    s.tv = np.array([80.0, 80.0, 80.0])       # km/h; comfortably moving
    s.tx = np.array([0.0, 20.0, 40.0])
    s.ty = np.zeros(3)
    s._gmeter = SimpleNamespace(has_data=False)
    s.laps = SimpleNamespace(point_count=lambda: 0, lap_time=lambda i: 70.0)
    seed_cols(s, 0, np.array([0.0, 35.0, 70.0]), np.array([0.0, 500.0, 1000.0]))
    st = s.stats
    assert s.stats is st                       # lazy-built once, then reused
    tot = st.totals()
    assert abs(tot.duration_s - 2.0) < 1e-12
    assert abs(tot.moving_s - 2.0) < 1e-12
    assert abs(tot.distance_m - 40.0) < 1e-12
    assert tot.start_clock is None             # point_count()==0 -> the empty sentinel
    (r,) = st.lap_stats()                      # real _lap_arrays over the seeded columns
    assert r.idx == 0 and r.time == 70.0
    assert r.peak_lat_g is None                # no g meter on the bare session
    print("test_bare_session_stats_property_wires_the_service OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STATS TESTS PASSED")
