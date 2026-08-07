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
  * the Session.stats property wiring on a bare Session (lazy build + degenerate trace);
  * the StatsView page (offscreen Qt on a stubbed session): tiles + per-lap table populated
    from real dataclasses, the None → em-dash rule, signal-absent sections hidden (no g →
    no DRIVING/FRICTION CIRCLE; no sectors → no SECTORS), and the km/h → mph unit flip.
Run: QT_QPA_PLATFORM=offscreen python tests/test_stats.py
"""
import datetime
import math
import os
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import bare_session, seed_cols  # noqa: E402

from studio.stats import (  # noqa: E402
    MOVING_MS,
    SessionStats,
    best_consecutive_mean,
    brake_consistency,
    clock_hhmm,
    corner_report,
    cov_pct,
    envelope_g,
    in_windows_mask,
    moving_time_s,
    pace_stats,
    path_distance_m,
    peak_g,
    phase_matrix,
    sector_medians,
    straights_report,
    theil_sen_slope,
    within_pct_of_best,
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


def test_corner_report_composes_columns():
    rows_t = [[3.0, 5.0], [3.2, 5.4], [3.4, 5.8]]
    rows_a = [[60.0, 44.0], [58.0, 42.0], [56.0, 40.0]]
    rows_g = [[0.9, 0.8], [1.0, 0.7]]                  # only 2 laps carried grip
    c1, c2 = corner_report([1, 2], [1, -1], rows_t, rows_a, rows_g)
    assert c1.n == 3 and c1.best_s == 3.0 and c1.median_s == 3.2
    assert abs(c1.median_loss_s - 0.2) < 1e-9
    assert abs(c1.sigma_s - float(np.std([3.0, 3.2, 3.4], ddof=1))) < 1e-12
    assert c1.apex_best_kmh == 60.0 and c1.apex_median_kmh == 58.0
    assert abs(c1.grip_median - 0.95) < 1e-12
    assert abs(c1.score - c1.sigma_s * c1.median_loss_s) < 1e-15
    assert c1.direction == 1 and c2.direction == -1
    # Ragged rows: a lap that never reached corner 2 + no grip anywhere.
    r1, r2 = corner_report([1, 2], [1, -1], [[3.0, 5.0], [3.2]], [[60.0, 44.0], [58.0]], [])
    assert r2.n == 1 and r2.sigma_s is None
    assert r2.score == 0.0                              # under-sampled never outranks measured
    assert r2.grip_median is None and r1.grip_median is None
    assert corner_report([], [], [], [], []) == []
    print("test_corner_report_composes_columns OK")


def test_brake_consistency_aggregates_matched_corners():
    rows = [
        {1: (100.0, 0.90, 2.0), 2: (300.0, 0.80, -1.0)},
        {1: (104.0, 0.95, 3.0)},                       # lap 2 never braked into corner 2
        {1: (102.0, None, None), 2: (306.0, 0.70, 5.0)},
    ]
    c1, c2, c3 = brake_consistency([1, 2, 3], rows)
    assert c1.n == 3 and c1.median_dist_m == 102.0
    assert abs(c1.sigma_m - float(np.std([100.0, 104.0, 102.0], ddof=1))) < 1e-12
    assert c1.span_m == 4.0
    assert abs(c1.commit_pct - 92.5) < 1e-9            # median of the two known commits
    assert c1.metres_later_med == 2.5                  # None entries lower n, never fake 0
    assert c2.n == 2 and c2.span_m == 6.0 and c2.metres_later_med == 2.0
    assert c3.n == 0 and c3.sigma_m is None and c3.commit_pct is None
    # single-lap corner: σ undefined, the rest still reported
    (only,) = brake_consistency([7], [{7: (50.0, 0.5, 1.0)}])
    assert only.n == 1 and only.sigma_m is None and only.span_m == 0.0
    print("test_brake_consistency_aggregates_matched_corners OK")


def test_straights_report_labels_deltas_and_leverage():
    # 2 corners -> 3 straights. Times (s) per lap down each straight; traps at each end;
    # exits per corner; the best lap exits fastest at corner 1 (the field is 2 km/h down).
    times = [[5.0, 8.0, 4.0], [5.2, 8.6, 4.1], [5.1, 8.3, 4.05]]
    traps = [[70.0, 90.0, 60.0], [69.0, 88.0, 59.0], [71.0, 89.0, 61.0]]
    exits = [[50.0, 40.0], [48.0, 41.0], [49.0, 40.5]]
    best_exits = [51.0, 40.0]
    s0, s1, s2 = straights_report([1, 2], times, traps, exits, best_exits)
    assert s0.label == "S/F → C1" and s0.ring_cid == 2       # the wrap: C2 feeds S/F
    assert s1.label == "C1 → C2" and s1.ring_cid == 1
    assert s2.label == "C2 → S/F" and s2.ring_cid == 2
    assert s0.exit_delta_kmh is None                          # no double-count with s2
    assert s1.best_s == 8.0 and s1.median_s == 8.3
    assert s1.trap_best_kmh == 90.0 and s1.trap_median_kmh == 89.0
    assert abs(s1.exit_delta_kmh - (49.0 - 51.0)) < 1e-12     # median exit − best exit
    assert abs(s1.leverage - 2.0 * 0.3) < 1e-9                # deficit 2 km/h × spread 0.3 s
    assert abs(s2.exit_delta_kmh - 0.5) < 1e-12               # field FASTER than best ->
    assert s2.leverage == 0.0                                 # no leverage claim
    print("test_straights_report_labels_deltas_and_leverage OK")


def test_phase_matrix_medians_and_positive_part_share():
    # Two corners × three laps; corner 2's exit phase is a median GAIN (negative) and must
    # NOT cancel losses elsewhere in the share (positive-part accounting).
    triples = [
        [(0.30, 0.10, 0.05), (0.20, 0.05, -0.10)],
        [(0.40, 0.20, 0.15), (0.10, 0.15, -0.20)],
        [(0.20, 0.30, 0.10), (0.30, 0.10, -0.30)],
    ]
    rep = phase_matrix([1, 2], triples)
    assert rep.rows[0] == (0.30, 0.20, 0.10)          # element-wise medians
    assert rep.rows[1] == (0.20, 0.10, -0.20)
    sh = rep.share
    assert abs(sh.entry_s - 0.50) < 1e-12             # 0.30 + 0.20
    assert abs(sh.apex_s - 0.30) < 1e-12
    assert abs(sh.exit_s - 0.10) < 1e-12              # 0.10 + max(0, -0.20)
    fr = sh.fracs()
    assert abs(sum(fr) - 1.0) < 1e-12 and abs(fr[0] - 0.50 / 0.90) < 1e-12
    # Ragged: a lap missing corner 2 → corner 2 medians over the remaining laps.
    rep2 = phase_matrix([1, 2], [[(0.1, 0.1, 0.1)], [(0.2, 0.2, 0.2), (0.3, 0.3, 0.3)]])
    assert rep2.rows[1] == (0.3, 0.3, 0.3)
    # All gains -> no share (nothing lost), rows still reported.
    rep3 = phase_matrix([1], [[(-0.1, -0.2, -0.1)]])
    assert rep3.share is None and rep3.rows[0] == (-0.1, -0.2, -0.1)
    # No data at all for a corner -> None row.
    assert phase_matrix([1], [[]]).rows == [None]
    print("test_phase_matrix_medians_and_positive_part_share OK")


def test_theil_sen_slope_exact_and_degenerate():
    assert abs(theil_sen_slope([70.0, 69.0, 68.0, 67.0]) - (-1.0)) < 1e-12  # every pair -1
    assert theil_sen_slope([70.0]) is None
    assert theil_sen_slope([]) is None
    print("test_theil_sen_slope_exact_and_degenerate OK")


def test_best_consecutive_mean_windows_and_nan_poisoning():
    assert abs(best_consecutive_mean([70.0, 68.0, 69.0, 72.0], 3) - 69.0) < 1e-12
    assert best_consecutive_mean([70.0, 68.0], 3) is None          # no full window
    # A NaN poisons ITS windows only; the clean trailing window still counts.
    v = best_consecutive_mean([70.0, math.nan, 68.0, 69.0, 70.0], 3)
    assert abs(v - 69.0) < 1e-12
    assert best_consecutive_mean([math.nan, math.nan, math.nan], 3) is None
    print("test_best_consecutive_mean_windows_and_nan_poisoning OK")


def test_within_pct_of_best_counts_the_best_itself():
    assert within_pct_of_best([68.0, 68.5, 68.68, 70.0], 1.0) == 3  # cutoff 68.68 inclusive
    assert within_pct_of_best([], 1.0) == 0
    assert within_pct_of_best([70.0], 1.0) == 1
    print("test_within_pct_of_best_counts_the_best_itself OK")


def test_cov_pct_is_sigma_over_median():
    v = cov_pct([68.0, 69.0, 70.0])  # sample sigma = 1.0, median = 69
    assert abs(v - 100.0 / 69.0) < 1e-9
    assert cov_pct([70.0]) is None
    print("test_cov_pct_is_sigma_over_median OK")


def test_envelope_g_percentile_of_combined():
    # Constant 3-4-5 samples: hypot == 1.0 everywhere -> any percentile is 1.0.
    assert abs(envelope_g([0.6] * 50, [0.8] * 50) - 1.0) < 1e-12
    assert envelope_g([], []) is None
    print("test_envelope_g_percentile_of_combined OK")


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


def test_pace_quality_service_methods():
    """pace_trend gating (None under TREND_MIN_LAPS), race pace over the consistency order,
    CoV and within-1% — all over the SAME clean-lap series as pace()."""
    times = {i: t for i, t in enumerate([70.0, 68.0, 69.0, 72.0])}
    st = _service(valid=list(times), cons=list(times), lap_times=times,
                  arrays={i: (np.array([0.0, 1000.0]), np.array([60.0, 90.0]),
                              np.array([0.0, 70.0])) for i in times})
    assert st.pace_trend() is None                       # 4 laps < TREND_MIN_LAPS(6): noise
    assert abs(st.race_pace() - 69.0) < 1e-12            # best 3-consecutive window
    assert st.laps_within_pct(1.0) == (1, 4)             # only 68.0 within 1% of 68.0
    assert st.pace_cov() is not None
    six = {i: 70.0 - i for i in range(6)}                # 70..65: exact -1 s/lap trend
    st6 = _service(valid=list(six), cons=list(six), lap_times=six)
    assert abs(st6.pace_trend() - (-1.0)) < 1e-12
    print("test_pace_quality_service_methods OK")


def test_longest_coast_and_gg_envelope():
    n = 200
    gm = _fake_gmeter(times=np.linspace(0.0, 100.0, n), lat_g=np.full(n, 0.6),
                      long_g=np.full(n, 9.9), long_g_gps=np.full(n, -0.8))
    st = _service(gm=gm, valid=[0], lap_times={0: 50.0},
                  arrays={0: (np.array([0.0, 1.0]), np.array([0.0, 1.0]),
                              np.array([0.0, 50.0]))},
                  windows={0: (0.0, 100.0)},
                  spans={0: [SimpleNamespace(duration=1.5), SimpleNamespace(duration=3.5)]})
    assert st.longest_coast_s() == 3.5
    assert abs(st.gg_envelope() - 1.0) < 1e-12           # hypot(0.6, -0.8) == 1.0 everywhere
    # no g signal -> None (unknown), never 0
    assert _service(valid=[0], lap_times={0: 50.0}).longest_coast_s() is None
    assert _service(valid=[0], lap_times={0: 50.0}).gg_envelope() is None
    print("test_longest_coast_and_gg_envelope OK")


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


# --------------------------------------------------------------- the StatsView page (Qt)
def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _fake_stats_service(*, has_g=True):
    from studio.stats import LapStat, PaceStats, SessionTotals
    rows = [
        LapStat(idx=0, time=70.0, vmax_kmh=95.0, avg_kmh=54.0, vmin_kmh=48.0,
                peak_lat_g=1.4 if has_g else None, peak_brake_g=1.1 if has_g else None,
                brake_s=28.0 if has_g else None, brake_n=12 if has_g else None,
                coast_s=0.5 if has_g else None, coast_frac=0.007 if has_g else None),
        LapStat(idx=1, time=68.2, vmax_kmh=97.5, avg_kmh=55.5, vmin_kmh=50.0,
                peak_lat_g=1.6 if has_g else None, peak_brake_g=0.9 if has_g else None,
                brake_s=26.0 if has_g else None, brake_n=11 if has_g else None,
                coast_s=0.0 if has_g else None, coast_frac=0.0 if has_g else None),
    ]
    gg = ((np.array([0.5, -0.8]), np.array([-0.4, 0.2])) if has_g else None)
    return SimpleNamespace(
        totals=lambda: SessionTotals(duration_s=4406.8, moving_s=4261.2, distance_m=65560.0,
                                     start_clock="19:16", end_clock="20:30"),
        pace=lambda: PaceStats(n=2, best=68.2, median=69.1, sigma=1.27, spread=0.9),
        lap_stats=lambda: rows,
        session_vmax=lambda: (97.5, 1),
        gg_cloud=lambda max_points=4000: gg,
        pace_trend=lambda: -0.05,
        race_pace=lambda: 68.9,
        pace_cov=lambda: 1.8,
        laps_within_pct=lambda pct=1.0: (2, 2),
        longest_coast_s=lambda: (1.4 if has_g else None),
        gg_envelope=lambda: (1.55 if has_g else None),
    )


def _fake_view_session(*, has_g=True, sectors=True):
    """The duck-typed read surface StatsView touches — a stub session, no Session machinery."""
    from studio.data_quality import TimingQuality
    from studio.gmeter import CrossCheck
    cross = CrossCheck(n=1000, lat_corr=0.9, long_corr=0.4, lat_rms_accl=0.5, lat_rms_gps=0.5,
                       long_rms_accl=0.3, long_rms_gps=0.3, align_yaw_deg=10.0,
                       align_reflect=False, ok=True)
    return SimpleNamespace(
        stats=_fake_stats_service(has_g=has_g),
        valid_lap_ids=lambda: [0, 1],
        # The two stitched TARGETS moved here from the Laps tab's SESSION-BESTS footer:
        # theoretical (sum of the session-best splits) renders inside SECTORS, rolling in PACE.
        theoretical_best=lambda: (68.0 if sectors else None),
        best_rolling_lap=lambda: 68.15,
        timing_verified=True,
        excluded_lap_ids=lambda: [5],
        dropout_lap_ids=lambda: {1},
        sector_sigmas=lambda: ([0.15, None] if sectors else []),
        session_best_splits=lambda: ([30.0, 38.0] if sectors else []),
        sector_medians=lambda: ([30.5, None] if sectors else []),
        timing_quality=TimingQuality(),
        has_gmeter=has_g,
        gmeter_source=lambda: "accl",
        gmeter_long_source=lambda: "gps",
        gmeter_cross=lambda: (cross if has_g else None),
        best_lap_id=lambda: 1,
        coaching_opportunities=lambda: SimpleNamespace(
            enough=True,
            rows=[SimpleNamespace(time_lost=0.4), SimpleNamespace(time_lost=0.3),
                  SimpleNamespace(time_lost=0.2), SimpleNamespace(time_lost=0.1)]),
    )


def test_stats_view_renders_every_group():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    assert v.t_laps.value.text() == "2 · 1⊘ · 1⚠"          # valid · excluded · dropout
    assert v.t_duration.value.text() == "1:13:27"
    assert v.t_distance.value.text() == "65.6 km"
    assert v.t_clock.value.text() == "19:16–20:30"
    assert v.t_best.value.text() == "1:08.200"
    assert "97.5 km/h" in v.t_vmax.value.text()
    assert "lap 2" in v.t_vmax.caption.text()                # 1-based, the app-wide rule
    assert "48.0" in v.t_vmin.value.text()                   # session slowest point
    assert v.t_peak_lat.value.text() == "1.60 g"             # max over the laps
    # v1.1 pace-quality tiles
    assert v.t_race_pace.value.text() == "1:08.900"
    assert v.t_cov.value.text() == "1.8 %"
    assert v.t_within.value.text() == "2 / 2"
    assert v.t_trend.value.text() == "-0.05 s/lap"
    assert "improving" in v.t_trend.caption.text()
    # The coaching digest: MEDIAN-anchored (69.1 - top-3 losses 0.9 = 68.2), honesty in tip.
    assert v.t_digest.value.text() == "1:08.200"
    assert "MEDIAN" in v.t_digest.toolTip()
    assert v.t_longest_coast.value.text() == "1.4 s"
    assert v.t_grip_ceiling.value.text() == "1.55 g"
    assert not v._driving_section.isHidden() and not v.gg.isHidden()
    assert v.lap_table.rowCount() == 2
    # Best lap starred, 1-based, AND carrying the ⚠ its page's DATA TRUST card promises
    # (the fake session flags lap id 1 as a dropout — C7).
    assert v.lap_table.item(1, 0).text() == "★ 2 ⚠"
    assert v.lap_table.item(0, 0).text() == "1"              # clean lap: no suffix
    assert v.sector_table.rowCount() == 2
    assert v.sector_table.item(1, 2).text() == "—"           # None median -> em-dash
    # The two stitched targets, each beside the data it comes from (they used to live in a
    # SESSION-BESTS footer on the Laps tab, which cost that grid two lap rows).
    assert v.t_rolling.value.text() == "1:08.150"            # PACE, next to best/median/race pace
    assert v.t_theoretical.value.text() == "1:08.000"        # SECTORS, above the per-sector table
    # Shown here (this fake HAS sector lines) — the counterpart of the 0-sector hide asserted in
    # test_stats_view_hides_signal_absent_sections, so neither direction is vacuous.
    assert not v.t_theoretical.isHidden() and not v._sector_section.isHidden()
    # Verified + high-quality timing: rendered as normal tiles, never the provisional muting.
    assert not v.t_rolling.value.font().italic()
    assert not v.t_theoretical.value.font().italic()
    assert "not a lap you drove" in v.t_theoretical.toolTip()
    assert "not a lap you drove" in v.t_rolling.toolTip()
    assert "agree" in v.trust_label.text()                   # the cross-check's first UI surface
    assert "GPS9 true clock" in v.trust_label.text()
    print("test_stats_view_renders_every_group OK")


def test_stats_view_hides_signal_absent_sections():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(has_g=False, sectors=False))
    assert v._driving_section.isHidden() and v.gg.isHidden()     # no g -> no g sections
    assert v._sector_section.isHidden() and v.sector_table.isHidden()
    # The theoretical best hides WITH its section: on a 0-sector track it degenerates to the best
    # lap time (a duplicate of the starred best that can even read slower than the rolling best),
    # so it carries no information — the rule the Laps footer used to hand-code. Rolling stays.
    assert v.t_theoretical.isHidden()
    assert not v.t_rolling.isHidden() and v.t_rolling.value.text() == "1:08.150"
    assert v.t_peak_lat.value.text() == "—"                      # None, never a fake 0
    assert v.lap_table.item(0, 5).text() == "—"                  # per-lap g cells dash too
    assert v.lap_table.item(0, 2).text() == "95.0"               # speed needs no g signal
    assert v.lap_table.item(0, 4).text() == "48.0"               # Min speed needs no g either
    print("test_stats_view_hides_signal_absent_sections OK")


def test_stats_view_target_tiles_mute_on_provisional_and_degraded_timing():
    """The theoretical / rolling tiles are stitched TARGETS, not laps anyone drove, so they share
    the lap timing's authority — the behaviour they carried in the Laps footer they moved from.

    PROVISIONAL timing (an arbitrary start line) or a DEGRADED clock (media-clock fallback /
    low-GPS estimate) renders them muted + italic with the explaining note prepended to the
    tooltip; Verified AND high-quality renders them as normal tiles. The measured PACE tiles
    beside them stay unmuted — those ARE laps you drove."""
    _app()
    from studio.data_quality import MEDIA_CLOCK_FALLBACK, TimingQuality
    from studio.lap_table import PROVISIONAL_TOOLTIP, estimated_timing_tooltip
    from studio.stats_panel import StatsView
    from studio.theme import PROVISIONAL_COLOR, C

    targets = lambda v: (v.t_theoretical, v.t_rolling)  # noqa: E731 — a local alias, not a def

    # Verified + clean clock: normal tiles.
    v = StatsView(_fake_view_session())
    for t in targets(v):
        assert not t.value.font().italic()
        assert C.text in t.value.styleSheet(), t.value.styleSheet()
    assert not v.t_best.value.font().italic(), "a measured lap time must never mute"

    # Provisional start line: muted + italic, tooltip led by the provisional note.
    sess = _fake_view_session()
    sess.timing_verified = False
    v = StatsView(sess)
    for t in targets(v):
        assert t.value.font().italic(), "provisional target tile must be italic"
        assert PROVISIONAL_COLOR in t.value.styleSheet(), t.value.styleSheet()
        assert t.toolTip().startswith(PROVISIONAL_TOOLTIP), t.toolTip()
    assert not v.t_best.value.font().italic(), "the measured best lap stays unmuted"

    # Verified but DEGRADED clock: the orthogonal axis — muted with the estimated note instead.
    sess = _fake_view_session()
    sess.timing_quality = TimingQuality(clock=MEDIA_CLOCK_FALLBACK)
    v = StatsView(sess)
    for t in targets(v):
        assert t.value.font().italic(), "degraded target tile must be italic"
        assert t.toolTip().startswith(estimated_timing_tooltip(sess.timing_quality)), t.toolTip()

    # And it RESTORES: flipping back to verified + clean and refreshing un-mutes in place.
    sess.timing_quality = TimingQuality()
    v.refresh()
    for t in targets(v):
        assert not t.value.font().italic(), "restored target tile must not stay italic"
        assert C.text in t.value.styleSheet(), t.value.styleSheet()
    print("test_stats_view_target_tiles_mute_on_provisional_and_degraded_timing OK")


def test_stats_view_corners_table_tint_sort_and_click():
    _app()
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor

    from studio import theme
    from studio.stats import CornerReport
    from studio.stats_panel import StatsView
    sess = _fake_view_session()
    sess.corner_report = lambda: [
        CornerReport(cid=1, direction=1, n=10, best_s=3.20, median_s=3.30, sigma_s=0.05,
                     median_loss_s=0.10, apex_best_kmh=62.0, apex_median_kmh=60.0,
                     grip_median=0.95, score=0.005),
        CornerReport(cid=2, direction=-1, n=10, best_s=5.10, median_s=5.50, sigma_s=0.20,
                     median_loss_s=0.40, apex_best_kmh=45.0, apex_median_kmh=43.0,
                     grip_median=None, score=0.08),
    ]
    v = StatsView(sess)
    t = v.corners_table
    assert not t.isHidden() and t.rowCount() == 2
    assert t.item(0, 0).text().startswith("C1")
    assert t.item(1, 7).text() == "—"                       # grip None -> dash, never 0
    # The worst corner (higher σ × loss) carries the behind hue on its loss cell; C1 doesn't.
    behind = QColor(theme.behind_colour())
    assert t.item(1, 4).foreground().color() == behind
    assert t.item(0, 4).foreground().color() != behind
    # Row click emits the cid read from the ROW'S OWN item (sort-stable); deselect -> None.
    fired = []
    v.corner_clicked.connect(fired.append)
    t.selectRow(1)
    assert fired and fired[-1] == 2
    t.clearSelection()
    assert fired[-1] is None
    # Numeric header sort: descending by Med loss puts C2 (0.40) first.
    t.sortItems(4, Qt.DescendingOrder)
    assert t.item(0, 0).text().startswith("C2")
    print("test_stats_view_corners_table_tint_sort_and_click OK")


def test_stats_view_phase_tiles_and_loss_tooltips():
    _app()
    from studio.stats import CornerReport, PhaseReport, PhaseShare
    from studio.stats_panel import StatsView
    sess = _fake_view_session()
    sess.corner_report = lambda: [
        CornerReport(cid=1, direction=1, n=10, best_s=3.20, median_s=3.30, sigma_s=0.05,
                     median_loss_s=0.10, apex_best_kmh=62.0, apex_median_kmh=60.0,
                     grip_median=0.95, score=0.005),
    ]
    sess.phase_report = lambda: PhaseReport(
        cids=[1], rows=[(0.61, 0.24, 0.15)], share=PhaseShare(6.1, 2.4, 1.5))
    v = StatsView(sess)
    assert not v.t_phase_entry.isHidden()
    assert v.t_phase_entry.value.text() == "61 %"
    assert "6.1 s" in v.t_phase_entry.caption.text()
    assert v.t_phase_exit.value.text() == "15 %"
    tip = v.corners_table.item(0, 4).toolTip()
    assert "entry +0.61" in tip and "exit +0.15" in tip
    # No phase data -> the tiles hide, the table stands alone.
    sess.phase_report = lambda: None
    v.refresh()
    assert v.t_phase_entry.isHidden() and v.t_phase_exit.isHidden()
    print("test_stats_view_phase_tiles_and_loss_tooltips OK")


def test_stats_view_braking_table_filters_unbraked_and_emits_clicks():
    _app()
    from studio.stats import BrakeConsistency
    from studio.stats_panel import StatsView
    sess = _fake_view_session()
    sess.brake_report = lambda: [
        BrakeConsistency(cid=1, n=10, median_dist_m=102.0, sigma_m=2.1, span_m=6.0,
                         commit_pct=88.0, metres_later_med=3.5),
        BrakeConsistency(cid=2, n=0, median_dist_m=None, sigma_m=None, span_m=None,
                         commit_pct=None, metres_later_med=None),
        BrakeConsistency(cid=3, n=1, median_dist_m=400.0, sigma_m=None, span_m=0.0,
                         commit_pct=None, metres_later_med=-1.2),
    ]
    v = StatsView(sess)
    t = v.braking_table
    assert not t.isHidden() and t.rowCount() == 2       # the unbraked corner (n=0) is omitted
    assert t.item(0, 0).text() == "C1" and t.item(0, 2).text() == "2.1"
    assert t.item(0, 4).text() == "88" and t.item(0, 5).text() == "+3.5"
    assert t.item(1, 2).text() == "—"                   # single-lap σ: dash, never 0
    assert t.item(1, 5).text() == "-1.2"
    fired = []
    v.corner_clicked.connect(fired.append)
    t.selectRow(1)
    assert fired and fired[-1] == 3                     # the SAME signal the CORNERS table uses
    # No braking data at all -> section hidden.
    sess.brake_report = lambda: []
    v.refresh()
    assert v._braking_section.isHidden() and v.braking_table.isHidden()
    print("test_stats_view_braking_table_filters_unbraked_and_emits_clicks OK")


def test_stats_view_straights_table_and_fix_first_tile():
    _app()
    from studio.stats import StraightStat
    from studio.stats_panel import RING_ROLE, StatsView
    sess = _fake_view_session()
    sess.straights_report = lambda: [
        StraightStat(index=0, label="S/F → C1", ring_cid=2, n=3, best_s=5.0, median_s=5.1,
                     sigma_s=0.1, trap_best_kmh=71.0, trap_median_kmh=70.0,
                     exit_delta_kmh=None, leverage=0.0),
        StraightStat(index=1, label="C1 → C2", ring_cid=1, n=3, best_s=8.0, median_s=8.3,
                     sigma_s=0.3, trap_best_kmh=90.0, trap_median_kmh=89.0,
                     exit_delta_kmh=-2.0, leverage=0.6),
        # B8: a start line inside a corner section yields a ~0-duration stub — must be omitted.
        StraightStat(index=2, label="C2 → S/F", ring_cid=2, n=3, best_s=0.0, median_s=0.01,
                     sigma_s=0.0, trap_best_kmh=71.0, trap_median_kmh=70.0,
                     exit_delta_kmh=0.5, leverage=0.0),
    ]
    v = StatsView(sess)
    t = v.straights_table
    assert not t.isHidden() and t.rowCount() == 2            # the ~0s stub is omitted (B8)
    assert t.item(0, 0).text() == "S/F → C1"
    assert t.item(0, 6).text() == "—"                      # k=0 exit delta: no double-count
    assert t.item(1, 6).text() == "-2.0"
    assert t.item(1, 0).data(RING_ROLE) == 1
    assert not v.t_fix_first.isHidden()
    assert v.t_fix_first.value.text() == "C1"              # the top-leverage corner
    assert "-2.0 km/h" in v.t_fix_first.caption.text()
    fired = []
    v.corner_clicked.connect(fired.append)
    t.selectRow(0)
    assert fired and fired[-1] == 2                        # the wrap straight rings C2
    # No straights data -> section + tile hidden.
    sess.straights_report = lambda: []
    v.refresh()
    assert v._straights_section.isHidden() and v.t_fix_first.isHidden()
    print("test_stats_view_straights_table_and_fix_first_tile OK")


def test_stats_view_trend_sparkline_shows_and_hides():
    _app()
    from studio.stats_panel import StatsView
    sess = _fake_view_session()
    sess.lap_time_trend = lambda: [(0, 70.0), (1, 68.2), (2, 69.0)]
    v = StatsView(sess)
    assert not v.spark.isHidden(), "the sparkline shows with >=2 clean laps"
    # x is the 1-BASED lap number (the app-wide display rule): first tick reads "1", last "3".
    ticks = v.spark.getPlotItem().getAxis("bottom")._tickLevels
    labels = [lab for _pos, lab in ticks[0]]
    assert labels == ["1", "3"], labels
    # A one-lap session hides the sparkline (a one-dot trend is noise).
    sess.lap_time_trend = lambda: [(0, 70.0)]
    v.refresh()
    assert v.spark.isHidden()
    print("test_stats_view_trend_sparkline_shows_and_hides OK")


def test_stats_view_tiles_reflow_with_pane_width():
    """C6: the tile grids reflow — 4 columns wide, down to 2 in a narrow quadrant — so the
    4th column (incl. the 'fix your top 3' digest) can never sit off-pane."""
    _app()
    from studio.stats_panel import TILES_PER_ROW, StatsView
    v = StatsView(_fake_view_session())
    v.show()
    v.resize(1000, 800)
    _pump()
    assert v._tile_cols == TILES_PER_ROW
    v.resize(420, 800)
    _pump()
    assert v._tile_cols == 2, v._tile_cols
    # The digest tile sits within the first two columns now (row-major re-place).
    g, tiles = v._tile_grids[1]                    # the PACE grid
    idx = tiles.index(v.t_digest)
    r, c = idx // 2, idx % 2
    assert g.itemAtPosition(r, c) is not None and g.itemAtPosition(r, c).widget() is v.t_digest
    v.hide()
    print("test_stats_view_tiles_reflow_with_pane_width OK")


def _pump(n=40):
    from PySide6.QtWidgets import QApplication
    for _ in range(n):
        QApplication.instance().processEvents()


def test_stats_view_corners_table_hidden_without_corners():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())      # no corner_report attr -> getattr default []
    assert v._corners_section.isHidden() and v.corners_table.isHidden()
    print("test_stats_view_corners_table_hidden_without_corners OK")


def test_stats_view_unit_flip():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    v.set_speed_unit("mph")
    assert "60.6 mph" in v.t_vmax.value.text()                   # 97.5 km/h -> mph
    assert "speeds in mph" in v._laps_section.text()
    assert v.lap_table.item(0, 2).text() == "59.0"               # 95.0 km/h -> mph
    v.set_speed_unit("kmh")
    assert "97.5 km/h" in v.t_vmax.value.text()
    print("test_stats_view_unit_flip OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STATS TESTS PASSED")
