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
    no DRIVING/FRICTION CIRCLE; no sectors → no SECTORS), and the km/h → mph unit flip;
  * the coaching DIGEST tile — its total is the Coaching panel's OWN arithmetic (same rows, same
    2-dp rounding: the two surfaces may never state different totals for the same corners), its
    caption names the median anchor, and it paints no arrow it cannot honour.
Run: QT_QPA_PLATFORM=offscreen python tests/test_stats.py
"""
import datetime
import math
import os
import re
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _qtapp import themed_app  # noqa: E402
from _synthetic import bare_session, seed_cols  # noqa: E402

# The page's typography and its mute cue are BOTH font-resolution behaviour, so the whole Qt half
# of this file runs in the app's real regime. Unthemed, `tile.value.font()` echoed back whatever
# the constructor asked for and every assertion below about a size or an italic was a statement
# about Qt's default stack — the tiles asked for 15/12, painted 13/13 in the app, and this file
# saw 15/12 and passed. See tests/_qtapp.py.
_APP = themed_app()

from studio.stats import (  # noqa: E402
    MIN_KEPT_FRAC,
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
    path_distance,
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


def test_path_distance_gates_a_teleport_fix():
    """L4-02. A GPS fix that jumps further than the trace's OWN speed channel allows over the same
    interval is a dropped fix, not distance driven — un-gated it dominated the session total (a
    real 9.6 s clip rendered 2.3 km against a 72 m speed ceiling, 31x). The gate must reject it
    and NOT touch the honest chords beside it."""
    # 10 Hz at a steady 20 m/s: nine 2.0 m chords, plus one injected 200 m teleport.
    n = 11
    times = [i * 0.1 for i in range(n)]
    speed = [20.0] * n
    xs = [i * 2.0 for i in range(n)]
    ys = [0.0] * n
    clean = path_distance(xs, ys, times, speed)
    assert abs(clean.metres - 20.0) < 1e-9 and clean.rejected_n == 0
    assert clean.kept_frac == 1.0                       # an honest trace loses nothing

    xs[5] += 200.0                                      # one teleport out and back
    glitched = path_distance(xs, ys, times, speed)
    assert abs(path_distance_m(xs, ys) - 416.0) < 1e-9  # un-gated: the glitch IS the number
    assert glitched.rejected_n == 2                     # the jump out and the jump back
    assert abs(glitched.metres - 16.0) < 1e-9           # the eight real chords survive intact
    # …and the total can never exceed what the speed channel allows over the recorded span.
    ceiling = max(speed) * (times[-1] - times[0])
    assert glitched.metres <= ceiling < path_distance_m(xs, ys)
    assert glitched.kept_frac < MIN_KEPT_FRAC           # -> the view renders a dash
    print("test_path_distance_gates_a_teleport_fix OK")


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
    # L4-06: at one lap the median IS the best, so `spread` carries σ's minimum-sample gate.
    # It used to report 0.0, which the tile printed as a measured "+0.00 s" beside σ's dash.
    single = pace_stats([70.0])
    assert single is not None and single.sigma is None and single.spread is None
    assert single.n == 1 and single.best == 70.0 and single.median == 70.0
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
    # L4-06: one lap is trivially within 1% of itself — "1 / 1" measures nothing, so the
    # reducer reports None below MIN_DIST_LAPS and the tile dashes like σ's does.
    assert within_pct_of_best([70.0], 1.0) is None
    assert within_pct_of_best([70.0, 71.0], 1.0) == 1               # two laps: a real count
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
    fast = (MOVING_MS + 2.0) * 3.6  # km/h == 6 m/s, comfortably moving
    st = _service(
        trace_t=[0.0, 0.1, 0.2, 10.2],           # last step is a 10 s gap -> duration keeps it,
        trace_v_kmh=[fast, 0.0, 0.0, fast],      # moving time skips it (and the two slow leads)
        # Positions consistent with those speeds: 0.6 m while moving at 6 m/s, then stationary,
        # then the 10 s gap closed by a 0.5 m chord (the trace does drift while parked).
        xs=[0.0, 0.6, 0.6, 1.1], ys=[0.0, 0.0, 0.0, 0.0],
        wall=(1_750_000_000_000, 1_750_000_600_000),
    )
    tot = st.totals()
    assert abs(tot.duration_s - 10.2) < 1e-9          # recorded span includes the gap
    assert abs(tot.moving_s - 0.1) < 1e-9             # only the first (moving) 0.1 s interval
    assert abs(tot.distance_m - 1.1) < 1e-9           # 0.6 + 0 + 0.5 chords, all plausible
    assert tot.distance_kept_frac == 1.0              # …so the speed gate rejected nothing
    assert tot.start_clock == clock_hhmm(1_750_000_000_000)
    assert tot.end_clock == clock_hhmm(1_750_000_600_000)
    assert st.totals() is tot                          # cached (the trace never changes)
    print("test_totals_duration_moving_distance_and_clocks OK")


def test_totals_distance_is_none_when_the_trace_is_mostly_glitch():
    """L4-02, end to end through the service: a trace whose chords are overwhelmingly impossible
    at its own speed reports distance_m=None (the view dashes) rather than a number 30x the
    physical ceiling — while the duration and moving time, which are real, still stand."""
    n = 11
    xs = [i * 2.0 for i in range(n)]
    xs[5] += 400.0                                    # the teleport dwarfs the honest chords
    st = _service(trace_t=[i * 0.1 for i in range(n)],
                  trace_v_kmh=[72.0] * n,             # 20 m/s
                  xs=xs, ys=[0.0] * n)
    tot = st.totals()
    assert tot.distance_m is None
    assert tot.distance_kept_frac < MIN_KEPT_FRAC
    assert abs(tot.duration_s - 1.0) < 1e-9           # the honest totals are untouched
    assert tot.moving_s > 0.0
    print("test_totals_distance_is_none_when_the_trace_is_mostly_glitch OK")


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
    return _APP


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _fake_stats_service(*, has_g=True, laps=True):
    from studio.stats import LapStat, PaceStats, SessionTotals
    if not laps:
        # The 0-lap recording: the trace (and so the SESSION totals) is real, every lap-derived
        # reduction is None/empty — exactly what SessionStats returns with no valid lap window.
        return SimpleNamespace(
            totals=lambda: SessionTotals(duration_s=61.0, moving_s=12.0, distance_m=180.0,
                                         distance_kept_frac=1.0,
                                         start_clock="19:16", end_clock="19:17"),
            pace=lambda: None, lap_stats=list, session_vmax=lambda: None,
            gg_cloud=lambda max_points=4000: None, pace_trend=lambda: None,
            race_pace=lambda: None, pace_cov=lambda: None,
            laps_within_pct=lambda pct=1.0: (0, 0),
            longest_coast_s=lambda: None, gg_envelope=lambda: None)
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
                                     distance_kept_frac=1.0,
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


def _fake_view_session(*, has_g=True, sectors=True, laps=True, track_name="Test Circuit",
                       excluded=(5,)):
    """The duck-typed read surface StatsView touches — a stub session, no Session machinery.

    `laps=False` is the 0-lap recording, `track_name=None` the unregistered track, `excluded=()`
    a session where the median band dropped nothing — the three trust states the DATA TRUST card
    has to tell apart."""
    from studio.data_quality import TimingQuality
    from studio.gmeter import CrossCheck
    cross = CrossCheck(n=1000, lat_corr=0.9, long_corr=0.4, lat_rms_accl=0.5, lat_rms_gps=0.5,
                       long_rms_accl=0.3, long_rms_gps=0.3, align_yaw_deg=10.0,
                       align_reflect=False, ok=True)
    return SimpleNamespace(
        stats=_fake_stats_service(has_g=has_g, laps=laps),
        valid_lap_ids=lambda: ([0, 1] if laps else []),
        track_name=track_name,
        lap_count=lambda: (2 + len(excluded) if laps else 1),
        # The two stitched TARGETS moved here from the Laps tab's SESSION-BESTS footer:
        # theoretical (sum of the session-best splits) renders inside SECTORS, rolling in PACE.
        theoretical_best=lambda: (68.0 if sectors and laps else None),
        best_rolling_lap=lambda: (68.15 if laps else None),
        timing_verified=True,
        excluded_lap_ids=lambda: list(excluded),
        dropout_lap_ids=lambda: ({1} if laps else set()),
        sector_sigmas=lambda: ([0.15, None] if sectors and laps else []),
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


def test_stats_tiles_paint_a_value_over_a_smaller_caption():
    """W10-01: the page's whole type hierarchy, measured as PAINTED — the tile value at
    TILE_VALUE_PT semibold over a CAPTION-sized caption, and the three page-level captions
    (DATA TRUST, the no-g note, the g-g key) at CAPTION too.

    Shipped, every one of the 29 tiles painted 13 px over 13 px: the theme's base QSS rule carried
    `font-size`, which outranks a setFont, so "1:08.771" and "best lap" had the same cap height and
    only colour separated them. This file could not see it — it had no theme, so `setFont` survived
    and it measured a page the app never rendered. Hence fontInfo(), not font(), and hence
    tests/_qtapp.py at the top of the file."""
    _app()
    from studio import theme
    from studio.stats_panel import TILE_VALUE_PT, StatsView, _Tile

    v = StatsView(_fake_view_session())
    v.resize(900, 900)
    v.show()
    _settle()
    tiles = v.findChildren(_Tile)
    assert len(tiles) >= 20, len(tiles)                    # the whole page, not one group
    painted = {(t.value.fontInfo().pixelSize(), t.caption.fontInfo().pixelSize()) for t in tiles}
    assert painted == {(TILE_VALUE_PT, theme.CAPTION)}, painted
    assert TILE_VALUE_PT > theme.CAPTION, "the value must outrank its own caption"
    # The emphasis half of the hierarchy: a semibold value over a regular caption.
    assert all(int(t.value.fontInfo().weight()) >= int(theme.W_SEMIBOLD) for t in tiles)
    assert all(int(t.caption.fontInfo().weight()) < int(theme.W_SEMIBOLD) for t in tiles)
    # The page's three prose captions share the caption size (they are notes, not body copy).
    for lab in (v.trust_label, v.no_gmeter_note, v.gg_key):
        assert lab.fontInfo().pixelSize() == theme.CAPTION, lab.fontInfo().pixelSize()
    v.hide()
    print(f"test_stats_tiles_paint_a_value_over_a_smaller_caption OK ({len(tiles)} tiles, "
          f"{painted.pop()} px)")


def test_stats_view_target_tiles_mute_on_provisional_and_degraded_timing():
    """The theoretical / rolling tiles are stitched TARGETS, not laps anyone drove, so they share
    the lap timing's authority — the behaviour they carried in the Laps footer they moved from.

    PROVISIONAL timing (an arbitrary start line) or a DEGRADED clock (media-clock fallback /
    low-GPS estimate) renders them muted + italic with the explaining note prepended to the
    tooltip; Verified AND high-quality renders them as normal tiles. The measured PACE tiles
    beside them stay unmuted — those ARE laps you drove.

    W10-02 — every italic assertion here reads fontInfo(), the font Qt PAINTS, not font(), the one
    the widget was asked for, and every one is made on a view fresh out of the constructor with no
    extra refresh(). Under the theme those two distinctions were the whole test: this guard used to
    pass only because the file was unthemed, and the app only painted the cue because CentralView
    refreshes a second time after a load."""
    _app()
    from studio import theme
    from studio.data_quality import MEDIA_CLOCK_FALLBACK, TimingQuality
    from studio.lap_table import PROVISIONAL_TOOLTIP, estimated_timing_tooltip
    from studio.stats_panel import TILE_VALUE_PT, StatsView
    from studio.theme import PROVISIONAL_COLOR, C

    targets = lambda v: (v.t_theoretical, v.t_rolling)  # noqa: E731 — a local alias, not a def

    # Verified + clean clock: normal tiles.
    v = StatsView(_fake_view_session())
    for t in targets(v):
        assert not t.value.fontInfo().italic()
        assert C.text in t.value.styleSheet(), t.value.styleSheet()
    assert not v.t_best.value.fontInfo().italic(), "a measured lap time must never mute"

    # Provisional start line: muted + italic, tooltip led by the provisional note. Asserted on the
    # tile as the CONSTRUCTOR leaves it — a second refresh() must not be what makes the cue appear.
    sess = _fake_view_session()
    sess.timing_verified = False
    v = StatsView(sess)
    for t in targets(v):
        assert t.value.fontInfo().italic(), "provisional target tile must PAINT italic"
        assert PROVISIONAL_COLOR in t.value.styleSheet(), t.value.styleSheet()
        assert t.toolTip().startswith(PROVISIONAL_TOOLTIP), t.toolTip()
    assert not v.t_best.value.fontInfo().italic(), "the measured best lap stays unmuted"

    # Verified but DEGRADED clock: the orthogonal axis — muted with the estimated note instead.
    sess = _fake_view_session()
    sess.timing_quality = TimingQuality(clock=MEDIA_CLOCK_FALLBACK)
    v = StatsView(sess)
    for t in targets(v):
        assert t.value.fontInfo().italic(), "degraded target tile must PAINT italic"
        assert t.toolTip().startswith(estimated_timing_tooltip(sess.timing_quality)), t.toolTip()

    # And it RESTORES: flipping back to verified + clean and refreshing un-mutes in place.
    sess.timing_quality = TimingQuality()
    v.refresh()
    for t in targets(v):
        assert not t.value.fontInfo().italic(), "restored target tile must not stay italic"
        assert C.text in t.value.styleSheet(), t.value.styleSheet()
    # ...and muting never costs the tile its type: the cue is the slant and the colour, not a
    # size change (the value stays TILE_VALUE_PT over a CAPTION caption in both states).
    sess.timing_verified = False
    v.refresh()
    for t in targets(v):
        assert t.value.fontInfo().pixelSize() == TILE_VALUE_PT, t.value.fontInfo().pixelSize()
        assert t.caption.fontInfo().pixelSize() == theme.CAPTION
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


def test_tile_reflow_takes_each_tile_out_of_the_grid_before_re_adding_it():
    """QA W8-01 — the MECHANISM behind a crash, pinned deterministically because the crash itself
    is a SIGSEGV that no in-process assertion can catch.

    The reflow re-places the same tiles into the same QGridLayout at a new column count, and
    QGridLayout.addWidget is NOT an idempotent move for a widget the layout already holds: Qt's
    QLayout::addChildWidget reacts by deleting that widget's existing layout item from INSIDE the
    addWidget call (removeWidgetRecursively -> `delete lay->takeAt(i)`), re-entrantly mutating the
    layout it is midway through inserting into. One such pass over the page's ~30 tiles — which is
    all it takes, since the page's own scrollbar appearing on first show flips the column count
    once — left the process in a state where the next burst of Qt-object destruction segfaulted
    inside Shiboken::Object::destroy: a View ▸ Units or View ▸ Colour-blind-safe cues toggle killed
    the app after nothing but ordinary lap-panel tab use.

    So the contract is on the CALL ORDER: on a re-place, every tile is removed from the grid before
    it is added back. Measured on the reporter's sequence: 8/8 clean runs with the removeWidget,
    5 deaths in 11 without it."""
    _app()
    from PySide6.QtWidgets import QGridLayout, QLabel

    from studio.stats_panel import StatsView

    class _RecordingGrid(QGridLayout):
        """A QGridLayout that records the ORDER of the layout calls made against it."""

        def __init__(self):
            super().__init__()
            self.calls = []

        def addWidget(self, w, *a, **k):
            self.calls.append(("add", w))
            super().addWidget(w, *a, **k)

        def removeWidget(self, w):
            self.calls.append(("remove", w))
            super().removeWidget(w)

    g = _RecordingGrid()
    tiles = [QLabel(f"t{i}") for i in range(6)]
    StatsView._place_tiles(g, tiles, 3)          # first placement: nothing to remove yet
    g.calls.clear()

    StatsView._place_tiles(g, tiles, 2)          # THE REFLOW: same tiles, new column count
    for w in tiles:
        seq = [kind for kind, obj in g.calls if obj is w]
        assert seq, f"{w.text()} was not re-placed at all"
        assert seq[0] == "remove", (
            f"{w.text()} was handed straight back to addWidget while the grid still held it — "
            "Qt then deletes its layout item from inside addWidget, which is what arms the "
            f"toggle crash (calls for this tile: {seq})")
    # …and it is still laid out where the new column count says it belongs.
    for i, w in enumerate(tiles):
        row, col = i // 2, i % 2
        item = g.itemAtPosition(row, col)
        assert item is not None and item.widget() is w, (
            f"{w.text()} should sit at ({row}, {col}) after a 2-column reflow")
    print("test_tile_reflow_takes_each_tile_out_of_the_grid_before_re_adding_it OK")


def test_stats_view_wide_pane_raises_the_tile_ceiling():
    """L4-05: ⌘⇧S maximizes this page into the whole window, where a hard 4-column cap left every
    tile row ending ~1000 px short of the right edge. Above WIDE_PANE_PX the reflow ceiling rises
    and the friction circle grows with it — the quadrant behaviour (2..4) is untouched."""
    _app()
    from studio.stats_panel import (
        GG_HEIGHT,
        GG_HEIGHT_WIDE,
        TILES_PER_ROW,
        TILES_PER_ROW_WIDE,
        WIDE_PANE_PX,
        StatsView,
    )
    v = StatsView(_fake_view_session())
    v.show()
    v.resize(1000, 900)                       # a normal pane: the old cap still applies
    _pump()
    assert v._tile_cols == TILES_PER_ROW
    assert v.gg.height() == GG_HEIGHT
    narrow_rows, narrow_right = _pace_layout(v)

    v.resize(WIDE_PANE_PX + 400, 900)         # a dashboard-width pane
    _pump()
    assert v._tile_cols == TILES_PER_ROW_WIDE, v._tile_cols
    assert v.gg.height() == GG_HEIGHT_WIDE
    # Measured on the real laid-out geometry, not on the column count: the same ten PACE tiles
    # occupy strictly fewer rows and reach further right, which is the whole point — the page
    # used to be a tall column down the left edge of a 1700 px pane.
    wide_rows, wide_right = _pace_layout(v)
    assert wide_rows < narrow_rows, (wide_rows, narrow_rows)
    assert wide_right > narrow_right, (wide_right, narrow_right)

    v.resize(1000, 900)                        # …and it is reversible
    _pump()
    assert v._tile_cols == TILES_PER_ROW and v.gg.height() == GG_HEIGHT
    assert _pace_layout(v)[0] == narrow_rows
    v.hide()
    print("test_stats_view_wide_pane_raises_the_tile_ceiling OK")


def _pace_layout(v):
    """(rows, right edge) of the PACE tile grid, from the widgets' actual laid-out geometry."""
    _g, tiles = v._tile_grids[1]
    vis = [t for t in tiles if t.isVisible()]
    return len({t.y() for t in vis}), max(t.x() + t.width() for t in vis)


def test_friction_circle_names_its_axes_and_keys_its_rings():
    """L4-09: the friction circle used to ship no unit and no axis name — both labelText's were
    '' and its ticks read '-2.0 / +0.0 / +2.0' — while CORNERS and PER LAP name theirs. It also
    draws two kinds of ring (a fixed 0.5 g rule, a MEASURED p98 envelope) with nothing saying
    which is which."""
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    plot = v.gg.getPlotItem()
    x_label = plot.getAxis("bottom").labelText
    y_label = plot.getAxis("left").labelText
    assert x_label and y_label, (x_label, y_label)
    assert "lateral" in x_label and "g" in x_label
    assert "longitudinal" in y_label
    assert "braking" in y_label and "accelerating" in y_label   # the sign IS the direction
    assert "g" in v._gg_section.text()                          # the peers' header convention
    # The key names the dashed ring AND carries the envelope's own value (1.55 g in the fake).
    key = v.gg_key.text()
    assert "dashed" in key and "1.55 g" in key, key
    assert "0.5 g" in key                                       # …and the solid rule beside it
    assert not v.gg_key.isHidden()
    # The origin tick is unsigned: "+0.0" reads as a signed measurement of nothing.
    ticks = [lab for _pos, lab in plot.getAxis("bottom")._tickLevels[0]]
    assert "+0.0" not in ticks and "0" in ticks, ticks
    print("test_friction_circle_names_its_axes_and_keys_its_rings OK")


def test_friction_circle_size_is_device_pixel_ratio_independent():
    """U8-01: pyqtgraph's sizeHint moves with the devicePixelRatio, so the plot laid out 440x220
    at DPR 1 and 300x220 at DPR 2 in the IDENTICAL logical window. Both axes are now pinned, so
    the laid-out size is a property of the layout, not of the screen."""
    _app()
    from studio.stats_panel import GG_ASPECT, GG_HEIGHT, StatsView
    v = StatsView(_fake_view_session())
    v.show()
    v.resize(1000, 900)
    _pump()
    expected = (int(GG_HEIGHT * GG_ASPECT), GG_HEIGHT)
    assert (v.gg.width(), v.gg.height()) == expected, (v.gg.width(), v.gg.height())
    # Pinned in both directions — a MAXIMUM width (what it used to carry) still lets the
    # DPR-dependent sizeHint choose the actual number below the cap.
    assert v.gg.minimumWidth() == v.gg.maximumWidth() == expected[0]
    assert v.gg.minimumHeight() == v.gg.maximumHeight() == expected[1]
    v.hide()
    print("test_friction_circle_size_is_device_pixel_ratio_independent OK")


def _stats_at_dpr(v, dpr):
    """Pretend the window moved to a screen at `dpr` and deliver Qt's OWN notification — the same
    path a user dragging the window between displays takes."""
    from PySide6.QtCore import QEvent
    v.devicePixelRatioF = lambda: dpr
    v.event(QEvent(QEvent.Type.DevicePixelRatioChange))
    _pump()


def _stats_pens(v):
    """Every cosmetic pyqtgraph pen on the page, as {name: (width, style, colour)}. A cosmetic
    pen's width IS device px, which is the quantity under test; the style and colour ride along so
    a re-pen that silently turned a dashed ring solid, or recoloured it, fails here too."""
    spark, gg = v.spark.getPlotItem(), v.gg.getPlotItem()
    pens = {
        "spark left axis": spark.getAxis("left").pen(),
        "spark bottom axis": spark.getAxis("bottom").pen(),
        "spark curve": v._spark_curve.opts["pen"],
        "spark baseline": v._spark_baseline.pen,
        "spark PB dot outline": v._spark_pb_dots.opts["pen"],
        "gg left axis": gg.getAxis("left").pen(),
        "gg bottom axis": gg.getAxis("bottom").pen(),
    }
    for i, ring in enumerate(v._gg_rings):
        pens[f"gg ring/axis/envelope {i}"] = ring.opts["pen"] if hasattr(ring, "opts") else ring.pen
    return {k: (p.widthF(), p.style(), p.color().name()) for k, p in pens.items()}


def test_stats_page_line_weights_are_logical_pixels_not_device_pixels():
    """QA W11-03. #175 made every chart/map pen a LOGICAL weight (theme.line_width) — and its AST
    guard walked a two-name list, `("plots_view.py", "map_view.py")`. The Stats page has its own
    pyqtgraph charts, so it was outside the fix AND outside the guard: measured live on one
    1440x900 window, the charts' axis pen went 1 -> 2 device px across a DPR 1 -> 2 move while
    every pen on this page — spark axes, spark curve, baseline, PB-dot outline, the friction
    circle's axes, its 0.5 g rings and its measured grip envelope — stayed at 1, i.e. HALF a
    logical pixel on the Retina panel the owner uses. The guard reported green throughout.

    Asserted in BOTH directions: the ratio is a property of the screen the window is on, so
    dragging back to an external monitor has to thin the pens again."""
    _app()
    from studio import theme
    from studio.stats_panel import StatsView
    try:
        v = StatsView(_fake_view_session())
        v.show()
        _pump()
        assert v._gg_rings, "the fixture must draw the friction circle for this to mean anything"
        at_1 = _stats_pens(v)
        assert {w for w, _s, _c in at_1.values()} == {1.0}, at_1
        # the friction circle draws a dashed MEASURED envelope beside its solid 0.5 g rules — the
        # style/colour half of the snapshot is only worth asserting if both kinds are present
        assert len({s for _w, s, _c in at_1.values()}) > 1, at_1

        _stats_at_dpr(v, 2.0)                        # dragged onto the Retina panel
        at_2 = _stats_pens(v)
        assert at_2.keys() == at_1.keys()
        assert {w for w, _s, _c in at_2.values()} == {2.0}, (
            f"on a DPR-2 screen (theme.pen_scale()={theme.pen_scale()}) the Stats page still "
            f"draws {at_2} device px — half the logical weight the charts beside it draw")
        assert theme.pen_scale() == 2.0
        # Dash patterns ride the width (Qt specifies them in pen-width units), so re-penning must
        # keep every style and colour exactly — a solid grip envelope would be a different chart.
        assert ({(s, c) for _w, s, c in at_2.values()}
                == {(s, c) for _w, s, c in at_1.values()}), (at_1, at_2)
        # …and the pxMode glyph SIZE is already device-independent: scaling it too would draw
        # double-size PB dots on a Retina panel.
        assert v._spark_pb_dots.opts["size"] == 7

        _stats_at_dpr(v, 1.0)                        # …and back to the external monitor
        back = _stats_pens(v)
        assert back == at_1, (at_1, back)
        v.hide()
        v.deleteLater()
    finally:
        theme.set_pen_scale(1.0)
    print("test_stats_page_line_weights_are_logical_pixels_not_device_pixels OK")


def test_single_lap_dashes_every_distribution_tile():
    """L4-06: with one clean lap the honesty gate was applied by 4 tiles and skipped by 3 — σ,
    CoV, trend and race pace dashed while 'median − best' printed '+0.00 s', 'within 1% of best'
    printed '1 / 1' and the median caption read '1 clean laps'. All of them describe a
    DISTRIBUTION, so they now dash together (gated in stats.py, not here)."""
    _app()
    from studio.stats import PaceStats
    from studio.stats_panel import DASH, StatsView
    session = _fake_view_session()
    one = pace_stats([70.0])
    assert isinstance(one, PaceStats)
    session.stats.pace = lambda: one
    session.stats.pace_cov = lambda: None
    session.stats.pace_trend = lambda: None
    session.stats.race_pace = lambda: None
    session.stats.laps_within_pct = lambda pct=1.0: (within_pct_of_best([70.0], pct), 1)
    v = StatsView(session)
    assert v.t_sigma.value.text() == DASH
    assert v.t_spread.value.text() == DASH, v.t_spread.value.text()
    assert v.t_within.value.text() == DASH, v.t_within.value.text()
    assert v.t_median.caption.text() == "median · 1 clean lap"   # singular, and it is one lap
    print("test_single_lap_dashes_every_distribution_tile OK")


def test_stats_view_distance_dashes_when_the_trace_is_mostly_glitch():
    """L4-02 at the tile: an implausible trace shows a dash with the reason in its tooltip, never
    the 2.3 km a 9.6 s clip used to render. A clean trace still prints its number."""
    _app()
    from studio.stats import SessionTotals
    from studio.stats_panel import DASH, StatsView
    session = _fake_view_session()
    session.stats.totals = lambda: SessionTotals(
        duration_s=9.6, moving_s=8.0, distance_m=None, distance_kept_frac=0.0035,
        start_clock=None, end_clock=None)
    v = StatsView(session)
    assert v.t_distance.value.text() == DASH
    tip = v.t_distance.toolTip()
    assert "0%" in tip and "possible" in tip, tip
    # A partially-gated but still plausible trace keeps its number and says what was dropped.
    session.stats.totals = lambda: SessionTotals(
        duration_s=1549.0, moving_s=1400.0, distance_m=18445.0, distance_kept_frac=0.94,
        start_clock=None, end_clock=None)
    v2 = StatsView(session)
    assert v2.t_distance.value.text() == "18.4 km"
    assert "6% of the raw steps were rejected" in v2.t_distance.toolTip()
    # …but a trace that lost 0.02% (a real 26-minute recording) gets no arithmetic-noise caveat.
    session.stats.totals = lambda: SessionTotals(
        duration_s=1549.0, moving_s=1400.0, distance_m=18445.0, distance_kept_frac=0.9998,
        start_clock=None, end_clock=None)
    v3 = StatsView(session)
    assert "rejected" not in v3.t_distance.toolTip()
    print("test_stats_view_distance_dashes_when_the_trace_is_mostly_glitch OK")


def test_trust_card_states_the_lateral_gain():
    """L9-01 at the card: Pearson r is scale-invariant, so halving the g channel left this card
    BYTE-IDENTICAL while every displayed g halved. The card must therefore state the one number
    that does move — the lateral MAGNITUDE ratio — and change when it changes."""
    _app()
    from studio.gmeter import CrossCheck
    from studio.stats_panel import StatsView
    good = CrossCheck(n=1000, lat_corr=0.96, long_corr=0.8, lat_rms_accl=0.747,
                      lat_rms_gps=0.670, long_rms_accl=0.25, long_rms_gps=0.21,
                      align_yaw_deg=47.0, align_reflect=False, ok=True)
    halved = CrossCheck(**{**vars(good), "lat_rms_accl": 0.747 / 2, "ok": False})
    assert halved.lat_corr == good.lat_corr            # r cannot see the scale error at all

    session = _fake_view_session()
    session.gmeter_cross = lambda: good
    v_good = StatsView(session)
    text_good = v_good.trust_label.text()
    session.gmeter_cross = lambda: halved
    v_bad = StatsView(session)
    text_bad = v_bad.trust_label.text()
    assert "gain ×1.11" in text_good, text_good
    assert "gain ×0.56" in text_bad, text_bad
    assert "agree" in text_good and "DISAGREE" in text_bad
    assert text_good != text_bad                       # the card is no longer scale-blind
    print("test_trust_card_states_the_lateral_gain OK")


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


def test_stats_view_states_provisional_timing_on_the_page():
    """The page must SAY the timing is unverified, and demote the PER LAP Time column.

    View ▸ Session statistics maximizes the lap panel, which hides the map — and with it the
    app's only prominent "Lap timing is unverified" banner. So the statement has to live on the
    page. The muting is the Time column ONLY: the speed/g cells and the measured PACE tiles are
    true whatever the start line is, and muting everything would say nothing about WHICH numbers
    the unverified line moves."""
    _app()
    from studio.lap_table import PROVISIONAL_TOOLTIP
    from studio.stats_panel import StatsView

    v = StatsView(_fake_view_session())                       # verified: no banner, no muting
    assert not v.provisional_banner.isVisibleTo(v)
    assert not v.lap_table.item(0, 1).font().italic()
    assert v.lap_table.item(1, 0).text() == "★ 2 ⚠"           # the ★ stands on verified timing

    sess = _fake_view_session()
    sess.timing_verified = False
    v = StatsView(sess)
    assert v.provisional_banner.isVisibleTo(v)
    banner = v.provisional_banner.text().lower()
    assert "unverified" in banner and "start/finish line" in banner
    for r in range(v.lap_table.rowCount()):                   # every Time cell, like the Laps tab
        item = v.lap_table.item(r, 1)
        assert item.font().italic(), f"row {r} Time cell must be italic while provisional"
        assert item.toolTip() == PROVISIONAL_TOOLTIP
    # ...and ONLY the Time column: the measured channels keep full authority.
    assert not v.lap_table.item(0, 2).font().italic(), "Vmax is measured, not start-line derived"
    assert not v.t_best.value.font().italic(), "the measured PACE tiles must not all mute"
    assert not v.t_vmax.value.font().italic()
    # No ★ against an arbitrary start line — the Laps tab suppresses the best there too, and a
    # starred "best" over a muted Time column would contradict the banner above it.
    assert not any(v.lap_table.item(r, 0).text().startswith("★")
                   for r in range(v.lap_table.rowCount()))
    print("test_stats_view_states_provisional_timing_on_the_page OK")


def test_stats_view_trust_card_names_the_sessions_own_problems():
    """The DATA TRUST card has to VARY with the session's trust state. It used to print
    provenance only, so a recording with an unconfirmed start line, an unknown track and half
    its laps dropped rendered the same three lines as a clean one."""
    _app()
    from studio.stats_panel import StatsView

    sess = _fake_view_session(excluded=(5, 6, 7))   # 2 valid + 3 dropped = 5 laps found
    sess.timing_verified = False
    sess.track_name = None
    v = StatsView(sess)   # bound, not inlined: a dropped StatsView deletes its own QLabels
    text = v.trust_label.text().lower()
    assert "auto-fitted, not confirmed" in text
    assert "track: unknown" in text
    # Both counts stated, and the denominator is the laps FOUND (lap_count) — not valid+excluded,
    # which would be arithmetic invented to make the two numbers meet.
    assert "statistics use 2 of the 5 laps found" in text and "3 ⊘ excluded" in text

    clean = StatsView(_fake_view_session(excluded=()))  # verified, named track, nothing dropped
    text = clean.trust_label.text().lower()
    for phrase in ("auto-fitted", "track: unknown", "excluded"):
        assert phrase not in text, f"clean session must not claim {phrase!r}"
    print("test_stats_view_trust_card_names_the_sessions_own_problems OK")


def test_stats_view_trust_card_names_the_moving_fix_population():
    """"0% of fixes rejected" claimed something the number never measured: the fraction is
    judged over the RETAINED MOVING trace (load.py:266-272 — deliberate, it is what stopped a
    clean recording reading degraded on its trimmed stationary lead-in). The label names the
    population; dropped_fraction itself is untouched."""
    _app()
    from studio.data_quality import TimingQuality
    from studio.stats_panel import StatsView

    sess = _fake_view_session()
    sess.timing_quality = TimingQuality(dropped_fraction=0.0)   # the raw gate DID drop fixes
    v = StatsView(sess)
    line = next(ln for ln in v.trust_label.text().split("\n") if ln.startswith("Timing:"))
    assert line == "Timing: GPS9 true clock · 0% of moving fixes rejected", line
    assert "WHILE MOVING" in v.trust_label.toolTip()
    # The measured value is the shipped one — the fix was the sentence, not the maths.
    assert sess.timing_quality.dropped_pct() == 0
    print("test_stats_view_trust_card_names_the_moving_fix_population OK")


def test_stats_view_states_the_missing_accelerometer():
    """With no IMU the trust card used to go silent about the g channel — exactly when the peak-g
    tiles, the per-lap g columns and the Grip column all render em-dashes."""
    _app()
    from studio.stats_panel import NO_GMETER_NOTE, StatsView

    v = StatsView(_fake_view_session(has_g=False))
    assert NO_GMETER_NOTE in v.trust_label.text()
    assert v.no_gmeter_note.isVisibleTo(v)                  # said again beside the dashes
    assert v.t_peak_lat.value.text() == "—"                 # the dash it explains
    v = StatsView(_fake_view_session(has_g=True))
    assert NO_GMETER_NOTE not in v.trust_label.text()
    assert not v.no_gmeter_note.isVisibleTo(v)
    assert "IMU lateral" in v.trust_label.text()
    print("test_stats_view_states_the_missing_accelerometer OK")


def test_stats_view_zero_lap_page_explains_itself():
    """The 0-lap page was 15 em-dashes across 19 tiles whose only explanation sat in the status
    bar, outside the maximized panel. The dash-only groups now hide behind one block carrying
    that copy plus the next action; SESSION (a real recorded trace) and DATA TRUST stay."""
    _app()
    from studio.stats_panel import StatsView

    sess = _fake_view_session(laps=False, has_g=False)
    sess.timing_verified = False
    v = StatsView(sess)
    assert v.no_laps_note.isVisibleTo(v)
    # No provisional banner and no "every lap time BELOW" line when there is nothing below —
    # the empty-state block already names placing the start line as the next action.
    assert not v.provisional_banner.isVisibleTo(v)
    assert "below" not in v.trust_label.text().lower()
    note = v.no_laps_note.text().lower()
    assert "no complete laps" in note and "start/finish line" in note   # reason + next action
    assert not v._pace_section.isVisibleTo(v) and not v._speed_section.isVisibleTo(v)
    tiles = ("t_best", "t_median", "t_race_pace", "t_rolling", "t_digest", "t_sigma", "t_spread",
             "t_cov", "t_within", "t_trend", "t_vmax", "t_vmin", "t_peak_lat", "t_peak_brake")
    dashed = [n for n in tiles
              if getattr(v, n).isVisibleTo(v) and getattr(v, n).value.text() == "—"]
    assert dashed == [], f"dash-only tiles still visible: {dashed}"
    assert v.t_duration.isVisibleTo(v) and v.t_duration.value.text() == "1:01"  # real recording
    assert v.trust_label.text() != "—"                       # the diagnostic stays on the page
    # Reversible: a re-segmentation that finds laps restores every group.
    v.session = _fake_view_session()
    v.refresh()
    assert not v.no_laps_note.isVisibleTo(v)
    assert v._pace_section.isVisibleTo(v) and v.t_best.isVisibleTo(v)
    assert v.t_best.value.text() == "1:08.200"
    print("test_stats_view_zero_lap_page_explains_itself OK")


def test_stats_view_trust_card_is_above_the_fold():
    """The card is only worth its new lines if they are read. At the foot of the page it sat
    ~1200 px down — below the fold of even a 1728x1117 maximized dashboard, so the caveats
    saying what every number is worth were reachable only by scrolling past all of them."""
    app = _app()
    from studio.stats_panel import StatsView

    sess = _fake_view_session()
    sess.timing_verified = False
    sess.track_name = None
    v = StatsView(sess)                       # the worst case: every trust line present
    v.resize(1728, 1025)
    v.show()
    app.processEvents()
    lab = v.trust_label
    y = lab.mapTo(v._scroll.widget(), lab.rect().topLeft()).y()
    viewport = v._scroll.viewport().height()
    assert 0 < y < viewport, f"trust card at y={y} is outside the first {viewport}px viewport"
    assert y + lab.height() < viewport, "the card must fit whole in the first viewport"
    v.close()
    print("test_stats_view_trust_card_is_above_the_fold OK")


# ------------------------------------------------------- the coaching digest tile (L5-02/IA-04/L4-08)
# The three corners the 3-chapter D24 fixture ranks first (cids 5, 3, 12). Their 2-dp cells read
# +0.13 +0.11 +0.08 = 0.32 s on the Coaching page; the raw floats sum to 0.3134 -> "0.31 s". The
# rounding penny between the two surfaces IS the defect these tests pin.
_D24_TOP3 = [0.12596491489577843, 0.10903147805383018, 0.07835681696116126]


def _digest_opportunities(losses, n_laps=65):
    """Real `coaching` dataclasses for `losses` (s, already ranked) — the exact shape BOTH the
    Stats digest tile and the Coaching panel consume, so the two can be compared side by side."""
    from studio import coaching
    rows = [coaching.Opportunity(
                cid=i + 1, direction=1 if i % 2 == 0 else -1, time_lost=t,
                entry_dist=100.0 * (i + 1),
                reason=coaching.Reason(kind=coaching.REASON_NONE, contribution=t,
                                       apex_speed_deficit=0.0, brake_extra_s=0.0,
                                       coast_extra_s=0.0, sigma=0.05))
            for i, t in enumerate(losses)]
    return coaching.Opportunities(enough=True, n_laps=n_laps, median_lap_id=3, rows=rows)


class _CoachSession:
    """The two calls OpportunitiesPanel makes on a session — nothing else."""

    def __init__(self, opp):
        self._opp = opp

    def coaching_opportunities(self):
        return self._opp

    def coaching_brake_points(self):
        return {}


def _digest_views(opp):
    """The same opportunities rendered by both surfaces: (StatsView, OpportunitiesPanel)."""
    from studio.coaching_panel import OpportunitiesPanel
    from studio.stats_panel import StatsView
    sess = _fake_view_session()
    sess.coaching_opportunities = lambda: opp
    return StatsView(sess), OpportunitiesPanel(_CoachSession(opp))


def test_stats_digest_total_equals_the_coaching_headline():
    """L5-02: the two surfaces must state the SAME total for the same corners.

    Stats summed the raw floats (0.3134 -> "0.31 s") while the Coaching headline sums the 2-dp
    cells the user can add up by eye (0.13+0.11+0.08 -> "0.32 s"), and the tile then subtracted
    0.3134 while printing 0.31 — disagreeing with the coaching page AND with its own tooltip.
    The digest now runs the panel's own arithmetic: its rows (`_shown_rows`), its count
    (`PANEL_TOP_N`) and its rounding."""
    _app()
    from studio._signal import fmt_time

    opp = _digest_opportunities(_D24_TOP3)
    v, panel = _digest_views(opp)
    tip, headline = v.t_digest.toolTip(), panel.summary_label.text()
    stats_total = re.search(r"\(([0-9]+\.[0-9]{2}) s", tip)
    coach_total = re.search(r"([0-9]+\.[0-9]{2}) s (?:across|in)", headline)
    assert stats_total and coach_total, (tip, headline)
    assert stats_total.group(1) == coach_total.group(1) == "0.32", (tip, headline)
    # ...and the tile's OWN number is that same total: printed == subtracted, no 3 ms slip.
    median = v.session.stats.pace().median
    assert v.t_digest.value.text() == fmt_time(median - 0.32) == "1:08.780", \
        v.t_digest.value.text()

    # The latent second bug: sub-resolution rows (< 0.005 s, rendered "+0.00 s") are ranked by
    # summarize but never SHOWN, so they must not be spent either. Here only one corner is real.
    opp = _digest_opportunities([0.30, 0.003, 0.002])
    v, panel = _digest_views(opp)
    assert "in your worst corner" in panel.summary_label.text(), panel.summary_label.text()
    assert v.t_digest.value.text() == fmt_time(69.1 - 0.30) == "1:08.800", v.t_digest.value.text()
    assert "top-1 corner losses" in v.t_digest.toolTip(), v.t_digest.toolTip()
    assert "top 1 fixed" in v.t_digest.caption.text(), v.t_digest.caption.text()
    print("test_stats_digest_total_equals_the_coaching_headline OK")


def test_stats_digest_tile_captions_its_base_and_paints_no_dead_link():
    """IA-04 + L4-08, one tile.

    IA-04: the digest is the MEDIAN lap rebased, so it routinely reads slower than the "best lap"
    tile a row away — the caption has to say which lap it started from, or a target you have
    already beaten looks like a contradiction. The anchor itself is deliberate and stays: best −
    losses would overclaim (the best lap already banks some of those corners).

    L4-08: the caption used to paint a "→" on a tile with no click handler, no PointingHandCursor
    and no focus — a navigation affordance that navigates nowhere. Either it is clickable or it
    does not paint the arrow."""
    _app()
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    from studio._signal import fmt_time

    v, _panel = _digest_views(_digest_opportunities(_D24_TOP3))
    tile = v.t_digest
    cap = tile.caption.text()

    # IA-04 — the base is named on the tile face, not just in the tooltip.
    assert "median" in cap.lower(), cap
    # ...and the anchor is UNCHANGED: still median − losses, never best − losses.
    pace = v.session.stats.pace()
    assert tile.value.text() == fmt_time(pace.median - 0.32) != fmt_time(pace.best - 0.32)
    assert "MEDIAN" in tile.toolTip()
    assert "slower" in tile.toolTip().lower(), \
        "the tooltip must say why a target can read slower than your best lap"

    # L4-08 — no arrow unless the tile can actually be pressed.
    clickable = (type(tile).mousePressEvent is not QWidget.mousePressEvent
                 or tile.cursor().shape() == Qt.PointingHandCursor
                 or tile.focusPolicy() != Qt.NoFocus)
    assert "→" not in cap and not clickable, \
        f"inert tile still paints a navigation arrow: {cap!r}"
    # It points at the Coaching tab in WORDS instead.
    assert "Coaching" in tile.toolTip(), tile.toolTip()
    print("test_stats_digest_tile_captions_its_base_and_paints_no_dead_link OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STATS TESTS PASSED")
