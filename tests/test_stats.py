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
    LAT_G_BAND,
    MATRIX_MIN_LAPS,
    MATRIX_SCALE_MIN_S,
    MIN_KEPT_FRAC,
    MIN_SPLIT_LAPS,
    MOVING_MS,
    SPEED_BAND,
    STINT_GAP_LAPS,
    STINT_GAP_MIN_S,
    TREND_MIN_LAPS,
    VMIN_STEADY_BAND,
    BandReport,
    Bands,
    SessionStats,
    Stint,
    band_edges,
    band_seconds,
    best_consecutive_mean,
    brake_consistency,
    clock_hhmm,
    corner_report,
    cov_pct,
    envelope_g,
    in_windows_mask,
    moving_time_s,
    pace_split,
    pace_stats,
    path_distance,
    path_distance_m,
    peak_g,
    phase_matrix,
    sample_durations,
    sector_medians,
    split_matrix,
    split_stints,
    stint_gap_s,
    stint_rows,
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


def _fake_stint(index, n, best, median, *, sigma=0.4, trend=-0.03, vmin=48.0, vmin_trend=0.02,
                start=0.0, gap_before=None):
    return Stint(index=index, lap_ids=list(range(n)), start_s=start, end_s=start + n * median,
                 gap_before_s=gap_before, best=best, median=median, sigma=sigma, trend=trend,
                 vmin_median=vmin, vmin_trend=vmin_trend)


def _fake_stats_service(*, has_g=True, laps=True, stints=None):
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
            longest_coast_s=lambda: None, gg_envelope=lambda: None,
            # Nothing to distribute with no lap: the DISTRIBUTIONS group hides itself on the
            # same None the friction circle hides on.
            speed_bands=lambda scale=1.0, width=SPEED_BAND: None,
            lateral_g_bands=lambda width=LAT_G_BAND: None,
            # …and no lap means no RUN. The tile dashes rather than printing a 0 a reader would
            # take for "the camera never left the pits".
            stints=list, stint_count=lambda: 0, stint_break_s=lambda: STINT_GAP_MIN_S)
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
        # The DISTRIBUTIONS charts. `split` on, so the view has outlines to draw; the lateral
        # report is None without an accelerometer, which is what hides that chart alone.
        speed_bands=lambda scale=1.0, width=SPEED_BAND, split=True: _fake_bands(
            np.arange(40.0, 65.1, width * scale), split=split),
        lateral_g_bands=lambda width=LAT_G_BAND: (
            _fake_bands(np.arange(-1.1, 1.11, width), split=True) if has_g else None),
        # ONE run by default — what both of the owner's recordings measure at every threshold
        # from 15 s to 600 s, and the state the STINTS table stays hidden on.
        stints=lambda: (stints if stints is not None else [_fake_stint(1, 2, 68.2, 69.1)]),
        stint_count=lambda: len(stints if stints is not None else [1]),
        stint_break_s=lambda: 207.6,
    )


def _fake_bands(edges, *, split=True, laps=9):
    """A stats.BandReport over made-up edges — a triangular profile, and a fast group that
    spends more of the lap at the top end (the shape the real fastest quartile has)."""
    n = len(edges) - 1
    base = np.linspace(1.0, 3.0, n)

    def band(seconds, laps_n, median):
        return Bands(edges=np.asarray(edges, float), seconds=np.asarray(seconds, float),
                     laps=laps_n, lap_ids=tuple(range(laps_n)), median_lap_s=median)

    if not split:
        return BandReport(all=band(base, laps, 69.1), fast=None, slow=None)
    return BandReport(all=band(base, laps, 69.1),
                      fast=band(base * np.linspace(0.8, 1.2, n), 3, 68.2),
                      slow=band(base * np.linspace(1.2, 0.8, n), 3, 70.4))


def _fake_segment_bests(single_donor=False):
    """A three-lap, five-segment composite for the IDEAL LAP block — hand-built so every number
    on the page is arithmetic a reader of this file can check.

    Lap 1 is the session best (the stub's `best_lap_id`). Its gains against the per-segment
    minima are, in segment order: 0.000 (S/F → C1), 0.284 (C1), 0.024 (C1 → C2), 0.204 (C2),
    0.000 (C2 → S/F) — 0.512 s in total, of which the two above the 0.05 s display floor print as
    0.28 and 0.20. C1 is the BIGGER gain and only one other lap matched it (beat 2/3); C2 is
    smaller and every lap matched it (beat 3/3), so the ranking has to put C2 first — 0.204 × 3/3
    = 0.204 over 0.284 × 2/3 = 0.189 — while a raw-gain order would lead with C1.

    (A positive gain always has beat ≥ 2: the donor is by definition at least as fast as the
    subject, so it counts itself and the subject. The fixture spends its whole discriminating
    range on that floor rather than pretending a 1/3 is reachable.)

    THE THIRD DECIMAL IS LOAD-BEARING, and it is why this matrix changed. It used to hold gains of
    exactly 0.28 and 0.20 against a 0.50 s gap — numbers that round cleanly, so summing the raw
    floats and summing the printed cells gave the SAME answer and the note's pinned assertion
    passed either way. On all four of the owner's real recordings they do not agree (D24 3 chapters
    printed cells adding to 1.40 under a note saying 1.39; Sandown chapter 1 printed 0.92 under a
    note saying 0.94), so the guard was blind to the defect it exists to catch. With these gains
    the two spellings differ: the printed cells sum to 0.48, the raw floats to 0.488 → "0.49", and
    the gap rounds to 0.51 — so a note built the wrong way states a total the column above it does
    not reach AND misses the tile by a penny in the other direction.

    `single_donor=True` collapses it to the state where one lap wins everything and the page must
    hide the block instead of printing a duplicate of the best lap."""
    from studio.corner_model import SegmentBests
    times = np.array([
        [1.00, 9.900, 3.000, 5.100, 2.00],   # lap 0 — owns C1 and the C1 → C2 straight
        [1.00, 10.184, 3.024, 5.204, 2.00],  # lap 1 — the best lap (the subject), 21.412
        [1.00, 10.600, 3.100, 5.000, 2.00],  # lap 2 — owns C2
    ])
    if single_donor:
        times = np.array([[1.0, 9.9, 3.0, 5.0, 2.0],
                          [1.1, 10.0, 3.1, 5.1, 2.1],
                          [1.2, 10.1, 3.2, 5.2, 2.2]])
    bests = [float(c.min()) for c in times.T]
    donors = [int(times[:, j].argmin()) for j in range(times.shape[1])]
    return SegmentBests(labels=["start", "C1", "C1-C2", "C2", "C2-finish"], cids=[1, 2],
                        lap_ids=[0, 1, 2], times=times,
                        admitted=np.ones(times.shape, bool), bests=bests, donors=donors,
                        s_edges=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],
                        donor_span=[(0.0, 0.0)] * times.shape[1])


def _fake_view_session(*, has_g=True, sectors=True, laps=True, track_name="Test Circuit",
                       excluded=(5,), ideal=True, single_donor=False, verified=True,
                       stints=None, splits=None):
    """The duck-typed read surface StatsView touches — a stub session, no Session machinery.

    `laps=False` is the 0-lap recording, `track_name=None` the unregistered track, `excluded=()`
    a session where the median band dropped nothing — the three trust states the DATA TRUST card
    has to tell apart. `ideal=False` is a recording with no corner partition and `single_donor`
    the one where a single lap wins every segment: the two states the IDEAL LAP block hides on.
    `verified=False` is the provisional start line that mutes the synthesized targets."""
    from studio.data_quality import TimingQuality
    from studio.gmeter import CrossCheck
    sb = _fake_segment_bests(single_donor) if (ideal and laps) else None
    cross = CrossCheck(n=1000, lat_corr=0.9, long_corr=0.4, lat_rms_accl=0.5, lat_rms_gps=0.5,
                       long_rms_accl=0.3, long_rms_gps=0.3, align_yaw_deg=10.0,
                       align_reflect=False, ok=True)
    # The SPLITS grid's own read surface — Session's, not SessionStats' (the splits are a
    # projection the segmentation owns). `splits` is {lap_id: [split, …]}; absent means a session
    # with no sector lines, which is every recording the owner has.
    split_rows = splits or {}
    return SimpleNamespace(
        stats=_fake_stats_service(has_g=has_g, laps=laps, stints=stints),
        valid_lap_ids=lambda: ([0, 1] if laps else []),
        consistency_lap_ids=lambda: sorted(split_rows),
        lap_sector_splits=lambda i: list(split_rows.get(i, [])),
        effective_sector_count=lambda: (
            max((len(r) for r in split_rows.values()), default=1) - 1),
        lap_time=lambda i: float(sum(split_rows.get(i, [0.0]))),
        track_name=track_name,
        lap_count=lambda: (2 + len(excluded) if laps else 1),
        # The stitched TARGETS. `best_rolling` renders in PACE; the theoretical best and its
        # decomposition are the IDEAL LAP block, read off the segment composite (NOT off
        # `theoretical_best()`, which is the same number by a longer route — one object, one
        # definition, so the tile and the table beneath it cannot disagree).
        theoretical_best=lambda: (None if sb is None else sb.total),
        ideal_segment_bests=lambda: sb,
        ideal_total=lambda: (None if sb is None else sb.total),
        ideal_donor_lap_id=lambda: (None if sb is None else sb.single_donor_id()),
        best_rolling_lap=lambda: (68.15 if laps else None),
        timing_verified=verified,
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
    # valid · excluded · dropout. The count and its mark are SEPARATED (D1-04): unspaced, the ⊘ and
    # the digit before it merged into one 40x19 ink run on the composite, and the tile's own legend
    # and the DATA TRUST row below both already spaced it.
    assert v.t_laps.value.text() == "2 · 1 ⊘ · 1 ⚠"
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
    # The stitched targets, each beside the data it comes from (they used to live in a
    # SESSION-BESTS footer on the Laps tab, which cost that grid two lap rows).
    assert v.t_rolling.value.text() == "1:08.150"            # PACE, next to best/median/race pace
    # The IDEAL LAP block: 1.00 + 9.90 + 3.00 + 5.00 + 2.00 = 20.90 s of per-segment minima,
    # against the best lap's own 21.412 — see _fake_segment_bests for the matrix.
    assert not v._ideal_section.isHidden() and not v.t_theoretical.isHidden()
    assert v.t_theoretical.value.text() == "0:20.900"
    assert v.t_ideal_gap.value.text() == "-0.51 s"
    # Verified + high-quality timing: rendered as normal tiles, never the provisional muting.
    assert not v.t_rolling.value.font().italic()
    assert not v.t_theoretical.value.font().italic()
    assert not v.t_ideal_gap.value.font().italic()
    assert "not a lap you drove" in v.t_theoretical.toolTip()
    assert "not a lap you drove" in v.t_rolling.toolTip()
    # …and the retired copy is GONE. Every clause of it was false once the ideal stopped being a
    # sum of sector splits, and the tile is no longer gated on sector lines at all.
    for dead in ("best sector", "sector splits", "Shown only with sector lines"):
        assert dead not in v.t_theoretical.toolTip(), dead
    assert "agree" in v.trust_card.text()                   # the cross-check's first UI surface
    assert "GPS9 true clock" in v.trust_card.text()
    print("test_stats_view_renders_every_group OK")


def test_stats_view_hides_signal_absent_sections():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(has_g=False, sectors=False))
    assert v._driving_section.isHidden() and v.gg.isHidden()     # no g -> no g sections
    # NO SECTOR LINES: the TABLES go and the HEADING stays, carrying the one line that says what
    # is missing and which control supplies it. This assertion used to read
    # `v._sector_section.isHidden()`, and that was the defect rather than the contract:
    # `sector_count()` is 0 on all five of the owner's recordings, so hiding the group outright
    # meant the per-sector table and the split grid existed and were never once seen or named.
    assert v.sector_table.isHidden() and v.splits_table.isHidden()
    assert v._splits_section.isHidden()
    assert not v._sector_section.isHidden() and not v.sectors_empty.isHidden()
    assert "Add sector" in v.sectors_empty.text()
    # THE THEORETICAL BEST NO LONGER HIDES WITH SECTORS, and this assertion is the gate fix.
    # It used to inherit this section's 0-sector hide, which was right while it was a sum of best
    # SECTOR splits (one sector = one lap = the best lap time). It is a corner/straight composite
    # now — sector lines do not touch it — and `sector_count()` is 0 on EVERY recording the owner
    # has, so the old gate hid the corrected number on all five of them. Same fake, sectors off,
    # ideal intact: the block stays.
    assert not v.t_theoretical.isHidden() and not v._ideal_section.isHidden()
    assert v.ideal_table.rowCount() > 0
    assert not v.t_rolling.isHidden() and v.t_rolling.value.text() == "1:08.150"
    assert v.t_peak_lat.value.text() == "—"                      # None, never a fake 0
    assert v.lap_table.item(0, 5).text() == "—"                  # per-lap g cells dash too
    assert v.lap_table.item(0, 2).text() == "95.0"               # speed needs no g signal
    assert v.lap_table.item(0, 4).text() == "48.0"               # Min speed needs no g either
    print("test_stats_view_hides_signal_absent_sections OK")


_IDEAL_NOTE_RE = re.compile(
    r"These (\d+) segments hold (-?\d+\.\d\d) s of the (-?\d+\.\d\d) s; "
    r"the other (\d+) hold (-?\d+\.\d\d) s between them")


def _ideal_page_numbers(v):
    """Every number the IDEAL LAP block prints, read off the RENDERED widgets: the `Gain (s)`
    cells, the note's three figures, and the gap tile. Strings only — no session accessor — so
    this measures what a reader can add up, not what the model believes."""
    t = v.ideal_table
    cells = [float(t.item(r, 1).text()) for r in range(t.rowCount())]
    m = _IDEAL_NOTE_RE.search(v.ideal_note.text())
    assert m, f"the note's shape changed; this guard cannot read it: {v.ideal_note.text()!r}"
    tile = v.t_ideal_gap.value.text()
    assert tile.endswith(" s") and tile[0] == "-", tile
    return SimpleNamespace(cells=cells, n_shown=int(m[1]), shown=float(m[2]), gap=float(m[3]),
                           n_rest=int(m[4]), rest=float(m[5]), tile=abs(float(tile[:-2])))


def _assert_ideal_page_adds_up(v):
    """F1 — THE PAGE ADDS UP IN THE READER'S OWN NUMBERS, at 2 dp, on the surfaces themselves.

    Three claims, none of which the model can satisfy on its own behalf:
      * the note's "these N segments hold X s" IS the sum of the printed cells;
      * X + the stated remainder IS the note's own total;
      * that total IS the number on the gap tile.

    Written structurally rather than as a pinned string because the pinned string is exactly how
    this shipped broken: `tests/test_stats.py` asserted "These 2 segments hold 0.48 s of the
    0.50 s" over a fixture whose gains were 0.28 and 0.20 — numbers that round cleanly, so
    summing the raw floats and summing the printed cells agreed and the guard could not tell the
    two spellings apart. On all four of the owner's real recordings they disagree."""
    n = _ideal_page_numbers(v)
    assert n.n_shown == len(n.cells), (n.n_shown, n.cells)
    assert round(sum(n.cells), 2) == n.shown, (
        f"the note says {n.n_shown} segments hold {n.shown:.2f} s, but the cells above it print "
        f"{n.cells} = {round(sum(n.cells), 2):.2f}")
    assert round(n.shown + n.rest, 2) == n.gap, (n.shown, n.rest, n.gap)
    assert n.gap == n.tile, (
        f"the note's total {n.gap:.2f} s is not the tile's {n.tile:.2f} s")
    return n


def test_the_ideal_note_adds_up_in_the_numbers_on_screen():
    """F1, swept rather than sampled: 240 composites whose gains carry a THIRD decimal, which is
    where the two spellings of "the total" come apart.

    The shipped note summed the raw floats. Measured on the real recordings that put a 1.40 s
    column under a 1.39 s sentence (D24, 3 chapters) and a 0.92 s column under a 0.94 s one
    (Sandown chapter 1) — a reader adding the cells and the stated remainder landed a penny off
    the tile in either direction. One fixture cannot show that (the previous one could not), so
    this sweeps the rounding space and carries its own NEGATIVE CONTROL: the retired spelling is
    re-derived here from the same rows and asserted to fail on a real share of the draws.

    Every number is read back off the rendered widgets by `_assert_ideal_page_adds_up`."""
    _app()
    from studio.corner_model import SegmentBests
    from studio.stats_panel import IDEAL_GAIN_FLOOR, StatsView
    rng = np.random.default_rng(20260906)
    n_seg = 7                      # 3 corners -> 2N+1 segments, the smallest realistic plan
    idx = np.arange(n_seg)
    old_spelling_failures = 0
    checked = 0
    for _ in range(240):
        gains = np.round(rng.uniform(0.0, 0.30, n_seg), 3)
        base = np.full(n_seg, 3.0)
        # Two donors, so the block is never the single-donor state: lap 0 owns the even segments,
        # lap 2 the odd ones, and lap 1 (the subject / best lap) is `gains` slower everywhere.
        times = np.stack([base + np.where(idx % 2 == 0, 0.0, 0.5),
                          base + gains,
                          base + np.where(idx % 2 == 1, 0.0, 0.5)])
        sb = SegmentBests(
            labels=[f"s{j}" for j in range(n_seg)], cids=[1, 2, 3], lap_ids=[0, 1, 2],
            times=times, admitted=np.ones(times.shape, bool),
            bests=[float(c.min()) for c in times.T],
            donors=[int(times[:, j].argmin()) for j in range(n_seg)],
            s_edges=list(np.linspace(0.0, 1.0, n_seg + 1)),
            donor_span=[(0.0, 0.0)] * n_seg)
        if sb.single_donor_id() is not None:
            continue
        s = _fake_view_session()
        s.ideal_segment_bests = lambda sb=sb: sb
        s.ideal_total = lambda sb=sb: sb.total
        s.theoretical_best = lambda sb=sb: sb.total
        s.ideal_donor_lap_id = lambda sb=sb: sb.single_donor_id()
        v = StatsView(s)
        if v.ideal_table.rowCount() == 0:
            continue               # every gain fell under the display floor: no plan to check
        checked += 1
        _assert_ideal_page_adds_up(v)
        # NEGATIVE CONTROL — the retired spelling, on these very rows.
        shown = [g for g in gains if g >= IDEAL_GAIN_FLOOR]
        if f"{sum(shown):.2f}" != f"{round(sum(round(float(g), 2) for g in shown), 2):.2f}":
            old_spelling_failures += 1
        v.deleteLater()
    assert checked >= 200, checked
    assert old_spelling_failures >= 20, (
        f"only {old_spelling_failures} of {checked} draws separate the two spellings — this sweep "
        f"is not exercising the rounding it exists to pin")
    print(f"test_the_ideal_note_adds_up_in_the_numbers_on_screen OK ({checked} composites; the "
          f"retired raw-float spelling misses the printed column on {old_spelling_failures})")


def test_ideal_decomposition_table_is_a_plan_not_a_taunt():
    """The IDEAL LAP table (N8): where the gap lives, on which lap, and how repeatable it is.

    Pinned on the hand-built composite in `_fake_segment_bests`, whose numbers are:

        row  segment      gain   beat   donor   priority
        1    C2          0.204    3/3   lap 3   0.204   <- smaller, every lap matched it
        2    C1          0.284    2/3   lap 1   0.189   <- BIGGER, matched once
        -    C1 → C2     0.024    2/3   lap 1   under the 0.05 s floor
        -    S/F → C1    0.000    3/3   lap 1   under the floor
        -    C2 → S/F    0.000    3/3   lap 1   under the floor

    So the table leads with the smaller gain, and a raw-gain order would lead with the bigger
    one. That inversion is the whole feature: measured on the owner's recordings D24's largest
    single gain (C2, 0.213 s, matched on 42 of 65 laps) and Sandown's (C5, 0.224 s, 13 of 59)
    rank differently for exactly this reason, and Sandown's biggest falls to third."""
    _app()
    from studio.stats_panel import IDEAL_GAIN_FLOOR, RING_ROLE, StatsView
    v = StatsView(_fake_view_session())
    t = v.ideal_table
    assert t.rowCount() == 2, [t.item(r, 0).text() for r in range(t.rowCount())]
    labels = [t.item(r, 0).text() for r in range(t.rowCount())]
    assert set(labels) == {"C1", "C2"}, labels
    assert labels == ["C2", "C1"], labels          # the ranking, not the gain order
    gains = {t.item(r, 0).text(): float(t.item(r, 1).text()) for r in range(t.rowCount())}
    assert gains == {"C1": 0.28, "C2": 0.20}, gains
    beats = {t.item(r, 0).text(): t.item(r, 2).text() for r in range(t.rowCount())}
    assert beats == {"C1": "2 / 3", "C2": "3 / 3"}, beats
    # The donor lap is 1-BASED on screen (the app-wide rule) — lap id 0 prints as "1".
    donors = {t.item(r, 0).text(): t.item(r, 3).text() for r in range(t.rowCount())}
    assert donors == {"C1": "1", "C2": "3"}, donors
    # Rows point the map at the corner they name (a straight would point at the corner feeding
    # it); the rows below the floor are not on screen at all.
    assert [t.item(r, 0).data(RING_ROLE) for r in range(t.rowCount())] == [
        int(lbl[1:]) for lbl in labels]
    assert IDEAL_GAIN_FLOOR == 0.05
    # THE NOTE CLOSES THE ARITHMETIC — IN THE READER'S OWN NUMBERS. The tile says -0.51 s; the two
    # printed cells add to 0.48 and the note accounts for the remaining three segments (0.03 s),
    # so 0.48 + 0.03 = 0.51 = the tile. A top-N list under a total that does not add up is the
    # defect this line exists to prevent, and summing the RAW gains (0.488 -> "0.49") is how it
    # shipped anyway on all four real recordings — see `_fake_segment_bests` for why the third
    # decimal is in this fixture.
    note = v.ideal_note.text()
    assert "These 2 segments hold 0.48 s of the 0.51 s" in note, note
    assert "the other 3 hold 0.03 s between them" in note, note
    assert "Ranked by gain" in note, note
    # THE SAMPLE moved OFF this note and onto its own line between the tiles and the table, so it
    # sits with the numbers it qualifies instead of under ten rows of decomposition. It is still
    # printed exactly once on the block: the note must not have kept a copy.
    assert "Stitched from 2 of your 3 clean laps" in v.ideal_sample.text(), v.ideal_sample.text()
    assert "Stitched from" not in note, note
    # ...and the same three numbers read STRUCTURALLY off the rendered surfaces, so this guard
    # keeps working when the fixture changes and cannot go blind again on a fixture whose gains
    # happen to round cleanly.
    _assert_ideal_page_adds_up(v)
    # Every cell of a row carries that row's own arithmetic, because NEITHER visible column is
    # sorted — the order is their product, and the CORNERS table's marked-loss column is on
    # record in this app as unreadable for exactly that reason.
    tip = t.item(0, 0).toolTip()
    assert "Ranked 1 of 2 by" in tip, tip
    assert t.item(0, 1).toolTip() == tip and t.item(0, 3).toolTip() == tip
    # NEGATIVE CONTROL: the retired ordering really would have got it wrong here.
    assert sorted(gains, key=lambda k: -gains[k]) == ["C1", "C2"], gains
    assert labels[0] == "C2", "a gain-ordered table leads with C1; this one must not"
    print("test_ideal_decomposition_table_is_a_plan_not_a_taunt OK")


def test_the_ideal_says_what_it_was_minimised_over_where_a_reader_sees_it():
    """THE IDEAL IS AN ORDER STATISTIC, AND THE PAGE NOW SAYS SO ON THE SURFACE, not only on hover.

    `SegmentBests.total` is a sum of per-segment minima, so it falls as a session accumulates laps
    and moves again when the corner partition is re-cut. Measured over random subsets of the
    owner's five recordings it falls 0.074–0.334 s per DOUBLING of lap count with no plateau, and
    on D24's three chapters the gap tile reads `-0.84 s` over 5 clean laps and `-1.42 s` over 65 —
    the same driving, the same recording. Before this, the tile,
    the caption and the hero all printed the number with nothing beside it and the counts lived
    only in the last sentence of a note under a ten-row table.

    Three placements, and each is load-bearing for a different reader:
      * the TILE CAPTION carries the lap count, so the number and its sample cannot be read apart
        — the same shape the measured `median · N clean laps` tile beside it already uses;
      * the SAMPLE LINE between the tiles and the table carries the donor count and the PARTITION
        (N corners + N+1 straights), which is what visibly changes when a start/finish-line drag
        re-detects the corners;
      * the TOOLTIPS carry the mechanism and the measured rate, on BOTH tiles, because it is one
        fact about both numbers.

    And the sample appears exactly ONCE on the block: the remainder note used to open with it and
    must not have kept a copy (two surfaces stating one fact is how they drift)."""
    _app()
    from studio.stats_panel import IDEAL_SAMPLE_TOOLTIP, StatsView
    v = StatsView(_fake_view_session())
    sb = v.session.ideal_segment_bests()
    smp = sb.sample
    assert (smp.donors, smp.laps, smp.corners, smp.segments) == (2, 3, 2, 5), smp

    assert v.t_theoretical.caption.text() == "theoretical best · 3 laps", \
        v.t_theoretical.caption.text()
    line = v.ideal_sample.text()
    assert "Stitched from 2 of your 3 clean laps" in line, line
    # N corners and N+1 straights, printed as `segments - corners` so the sentence stays true if
    # the partition ever stops being 2N+1 rather than printing a derived lie.
    assert "2 corners and 3 straights" in line, line
    assert line.count("Stitched from") == 1, line
    assert "Stitched from" not in v.ideal_note.text(), v.ideal_note.text()

    for tile in (v.t_theoretical, v.t_ideal_gap):
        assert IDEAL_SAMPLE_TOOLTIP.strip() in tile.toolTip(), tile.toolTip()
        assert "per doubling of lap count" in tile.toolTip(), tile.toolTip()
    # …and the retired copy is still gone from the theoretical tile (the #211 guard, re-checked
    # here because this test rewrote that constant).
    for dead in ("best sector", "sector splits"):
        assert dead not in v.t_theoretical.toolTip(), dead

    # SINGULARS. `corners` and `straights` and `laps` all have 1 as a legal value, and this page
    # has shipped "median · 1 clean laps" once already.
    # The pluralizer moved to _signal (the exported report prints the same sentence and cannot
    # import a Qt view); IdealSample.sentence() is now the ONE place that composes it.
    from studio._signal import plural
    assert plural(1, "corner") == "1 corner"
    assert plural(2, "corner") == "2 corners"

    # THE LINE FOLLOWS THE NUMBER. Restrict the composite to two laps — what a shorter recording
    # does — and the caption, the line and the tile all move together, in the same frame.
    from studio.corner_model import SegmentBests
    # Laps 1 and 2 — it must keep the BEST lap (the subject the decomposition is measured against,
    # which `_clean_lap_ids` guarantees by appending it) and it must keep two distinct donors,
    # or the block hides instead of re-rendering and this guard would pass on a stale caption.
    keep, times = [1, 2], sb.times[1:]
    donors = [keep[int(times[:, j].argmin())] for j in range(times.shape[1])]
    two = SegmentBests(labels=sb.labels, cids=sb.cids, lap_ids=keep,
                       times=times, admitted=sb.admitted[1:],
                       bests=[float(c.min()) for c in times.T], donors=donors,
                       s_edges=sb.s_edges, donor_span=sb.donor_span)
    assert two.single_donor_id() is None and len(two.donor_ids()) == 2, two.donor_ids()
    was_gap = v.t_ideal_gap.value.text()
    v.session.ideal_segment_bests = lambda: two
    v.session.ideal_total = lambda: two.total
    v.session.ideal_donor_lap_id = lambda: two.single_donor_id()
    v.refresh()
    assert v.t_theoretical.caption.text() == "theoretical best · 2 laps", \
        v.t_theoretical.caption.text()
    assert "Stitched from 2 of your 2 clean laps" in v.ideal_sample.text(), v.ideal_sample.text()
    # Fewer laps, a SLOWER ideal and therefore a smaller gap — the property the disclosure exists
    # for, on the rendered strings rather than on the model.
    assert two.total >= sb.total, (two.total, sb.total)
    assert abs(float(v.t_ideal_gap.value.text()[:-2])) < abs(float(was_gap[:-2])), (
        was_gap, v.t_ideal_gap.value.text())
    print("test_the_ideal_says_what_it_was_minimised_over_where_a_reader_sees_it OK")


def test_ideal_block_hides_when_it_would_duplicate_a_lap_you_drove():
    """The two states the block must NOT print, and they are the only two.

      * ONE lap won every segment — the "ideal" IS that lap, so a tile would be a byte-identical
        duplicate of the ★ best. (`ideal_donor_lap_id()` names it; the house precedent is to hide
        a degenerate synthesized target, not to print it.)
      * no corner partition at all — the "partition" is the whole lap and its minimum is the best
        lap time again, the original defect.

    Sector lines are absent in BOTH fakes and present in neither hide reason, which is the point:
    the gate moved off `sector_count()` entirely."""
    _app()
    from studio.stats_panel import StatsView
    one = StatsView(_fake_view_session(single_donor=True))
    assert one.session.ideal_donor_lap_id() is not None
    assert one._ideal_section.isHidden() and one.t_theoretical.isHidden()
    assert one.t_ideal_gap.isHidden() and one.ideal_table.isHidden()
    assert one.ideal_table.rowCount() == 0 and one.ideal_note.text() == ""
    assert one.ideal_note.isHidden()
    # …and the SAMPLE line with them. A sentence counting the laps of a composite that is not on
    # screen is the same "tile left behind under a hidden heading" defect, one widget over.
    assert one.ideal_sample.isHidden() and one.ideal_sample.text() == ""

    none = StatsView(_fake_view_session(ideal=False))
    assert none.session.ideal_segment_bests() is None
    assert none._ideal_section.isHidden() and none.ideal_table.isHidden()

    # …and the zero-lap recording, where there is no best lap to decompose against.
    empty = StatsView(_fake_view_session(laps=False))
    assert empty._ideal_section.isHidden() and empty.t_theoretical.isHidden()
    print("test_ideal_block_hides_when_it_would_duplicate_a_lap_you_drove OK")


def test_ideal_targets_mute_with_the_timing_they_borrow_authority_from():
    """Both IDEAL LAP tiles are SYNTHESIZED, so both take `_set_target_tile`'s provisional
    treatment — muted, italic, and carrying the start-line note above their own tooltip — while
    the measured PACE tiles beside them stay upright, because those ARE laps you drove.

    The TABLE is deliberately not muted: its cells are measured segment times and differences
    between them, the same standing the CORNERS and STRAIGHTS reports have, and the page's own
    amber banner already qualifies the page (ledger A26/A7 — a page that hides the map carries
    its own trust chrome)."""
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(verified=False))
    assert v.provisional_banner.isVisible() or not v.isVisible()   # the page's own chrome
    assert v.t_theoretical.value.font().italic()
    assert v.t_ideal_gap.value.font().italic()
    assert v.t_rolling.value.font().italic()
    assert not v.t_best.value.font().italic(), "a lap you DROVE must not be muted"
    assert not v.t_median.value.font().italic()
    for tile in (v.t_theoretical, v.t_ideal_gap, v.t_rolling):
        assert "start/finish line" in tile.toolTip(), tile.toolTip()
    assert "not a lap you drove" in v.t_theoretical.toolTip()
    # …and it is reversible: the same fake with a verified line renders upright.
    ok = StatsView(_fake_view_session())
    assert not ok.t_theoretical.value.font().italic()
    assert not ok.t_ideal_gap.value.font().italic()
    print("test_ideal_targets_mute_with_the_timing_they_borrow_authority_from OK")


def test_the_two_synthesized_targets_do_not_contradict_each_other():
    """The page now carries TWO synthesized lap times, and `_set_digest`'s own comment records
    that a rounding-level disagreement between two such surfaces was already treated as a defect.

    They are reconcilable because they are anchored differently, and each tooltip now says so:
    the digest is a TYPICAL lap with its top-3 corners fixed, the ideal is your quickest time
    through every segment stitched together. Measured on the owner's five recordings the ideal is
    the faster of the two by 0.33 to 2.67 s and the order ideal < best < projected never breaks —
    so neither tile can be read as a target the other has already beaten."""
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    digest_tip, ideal_tip = v.t_digest.toolTip(), v.t_theoretical.toolTip()
    # Each names its own ANCHOR (IA-04: the caption names the base, the tooltip names the maths).
    assert "MEDIAN" in digest_tip and "median lap" in v.t_digest.caption.text()
    assert "each corner and each straight" in ideal_tip, ideal_tip
    # …and the digest points at the other one rather than leaving a reader to guess why two
    # synthesized targets on one page disagree.
    assert "IDEAL LAP" in digest_tip, digest_tip
    assert "Different anchors" in digest_tip, digest_tip
    print("test_the_two_synthesized_targets_do_not_contradict_each_other OK")


def test_stats_tiles_paint_a_value_over_a_smaller_caption():
    """W10-01: the page's whole type hierarchy, measured as PAINTED — the tile value at
    theme.EMPHASIS semibold over a CAPTION-sized caption, and the page-level captions
    (DATA TRUST, the no-g note, the g-g key) at CAPTION too.

    Shipped, every one of the 29 tiles painted 13 px over 13 px: the theme's base QSS rule carried
    `font-size`, which outranks a setFont, so "1:08.771" and "best lap" had the same cap height and
    only colour separated them. This file could not see it — it had no theme, so `setFont` survived
    and it measured a page the app never rendered. Hence fontInfo(), not font(), and hence
    tests/_qtapp.py at the top of the file."""
    _app()
    from studio import stats_panel, theme
    from studio.stats_panel import StatsView
    from studio.widgets import Tile

    # The alias is GONE, not merely equal: the tile and its type step are app vocabulary now
    # (widgets.Tile / theme.EMPHASIS), and a page-local name for either is what this phase removed.
    assert not hasattr(stats_panel, "TILE_VALUE_PT"), "the local alias for theme.EMPHASIS is gone"
    assert not hasattr(stats_panel, "_Tile"), "the page-private tile is gone; widgets.Tile is it"

    v = StatsView(_fake_view_session())
    v.resize(900, 900)
    v.show()
    _settle()
    tiles = v.findChildren(Tile)
    assert len(tiles) >= 20, len(tiles)                    # the whole page, not one group
    painted = {(t.value.fontInfo().pixelSize(), t.caption.fontInfo().pixelSize()) for t in tiles}
    assert painted == {(theme.EMPHASIS, theme.CAPTION)}, painted
    assert theme.EMPHASIS > theme.CAPTION, "the value must outrank its own caption"
    # The emphasis half of the hierarchy: a semibold value over a regular caption.
    assert all(int(t.value.fontInfo().weight()) >= int(theme.W_SEMIBOLD) for t in tiles)
    assert all(int(t.caption.fontInfo().weight()) < int(theme.W_SEMIBOLD) for t in tiles)
    # The page's three prose captions share the caption size (they are notes, not body copy).
    for lab in (v.no_gmeter_note, v.gg_key):
        assert lab.fontInfo().pixelSize() == theme.CAPTION, lab.fontInfo().pixelSize()
    # ...and so does every half of every DATA TRUST row (the card is a definition list now, so
    # there are two labels per fact rather than one paragraph).
    for term, value in v.trust_card._widgets:
        for lab in (term, value):
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
    from studio.stats_panel import StatsView
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
    # size change (the value stays theme.EMPHASIS over a CAPTION caption in both states).
    sess.timing_verified = False
    v.refresh()
    for t in targets(v):
        assert t.value.fontInfo().pixelSize() == theme.EMPHASIS, t.value.fontInfo().pixelSize()
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
    4th column (incl. the 'fix your top 3' digest) can never sit off-pane.

    700 px OF PANE AND NOT 1000, for the reason the friction-circle check above states: a tile
    grid is measured against its own SECTION COLUMN now, and 1000 px is two columns of 476 — which
    packs three tiles, exactly as the 445 px quadrant does. 700 px is a single column, so it is
    still the pane class this test was written for. The composed case is asserted after it."""
    _app()
    from studio.stats_panel import TILES_PER_ROW, StatsView
    v = StatsView(_fake_view_session())
    v.show()
    v.resize(700, 800)
    _pump()
    assert v._tile_cols == TILES_PER_ROW
    # ...and a COMPOSED page reflows per column too: the tile rows used to run to
    # TILES_PER_ROW_WIDE across the whole 1900 px pane, which is what made the maximized page one
    # wide strip. Six tiles in a 609 px column would be 101 px each.
    v.resize(1900, 800)
    _pump()
    assert v._tile_cols == TILES_PER_ROW, v._tile_cols
    v.resize(420, 800)
    _pump()
    assert v._tile_cols == 2, v._tile_cols
    # The digest tile sits within the first two columns now (row-major re-place).
    g, tiles, _group = v._tile_grids[1]            # the PACE grid
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
    """L4-05's OUTCOME, kept; L4-05's mechanism, superseded.

    ⌘⇧S maximizes this page into the whole window, where a hard 4-column tile cap left every tile
    row ending ~1000 px short of the right edge. L4-05 answered that by raising the CAP above
    WIDE_PANE_PX — which widened the rows and could not do anything about the other ~1200 px of
    empty canvas beside a page that was still one column. The page composes into section columns
    now, so the width is spent on the PAGE and the tile grid keeps the 2..4 shape it has in a
    quadrant; the ceiling still exists, for a single column that is dashboard-width on its own.

    So the two assertions that moved are the tile cap and the circle's ceiling at a 1600 px pane,
    and the three that did not are the ones this test was really for: the same ten PACE tiles take
    strictly fewer rows, reach strictly further right, and go back when the pane does."""
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
    # 503 and not 445, the OTHER shipped quadrant width, because 445 sits within 11 px of the
    # 3-tile threshold and the answer there depends on whether the vertical scrollbar was showing
    # at the instant the last resize arrived. That hysteresis is older than this test and is not
    # what it is measuring; 503 is the same pane class with 58 px of margin.
    v.resize(503, 900)                        # the quadrant this page is a quadrant in
    _pump()
    assert v._column_count() == 1
    assert v._tile_cols < TILES_PER_ROW       # a quadrant's body: three tiles
    assert v.gg.height() <= GG_HEIGHT         # ...and a circle no bigger than the normal ceiling
    narrow_rows, narrow_right = _pace_layout(v)

    v.resize(1900, 900)                       # the ⌘⇧S dashboard
    _pump()
    assert v._column_count() == 3
    assert v._tile_cols == TILES_PER_ROW, v._tile_cols
    assert v.gg.height() == GG_HEIGHT_WIDE
    # Measured on the real laid-out geometry, not on the column count: the same ten PACE tiles
    # occupy strictly fewer rows and reach further right, which is the whole point.
    wide_rows, wide_right = _pace_layout(v)
    assert wide_rows < narrow_rows, (wide_rows, narrow_rows)
    assert wide_right > narrow_right, (wide_right, narrow_right)

    # The tile ceiling is still REACHABLE — on a single column that is dashboard-width by itself,
    # which is what WIDE_PANE_PX has always meant. Three of those is a 4K panel.
    v.resize(3 * WIDE_PANE_PX + 200, 900)
    _pump()
    assert v._column_width() >= WIDE_PANE_PX, v._column_width()
    assert v._tile_cols == TILES_PER_ROW_WIDE, v._tile_cols

    v.resize(503, 900)                        # …and it is reversible
    _pump()
    assert v._tile_cols < TILES_PER_ROW and v.gg.height() <= GG_HEIGHT
    assert _pace_layout(v) == (narrow_rows, narrow_right)
    v.hide()
    print("test_stats_view_wide_pane_raises_the_tile_ceiling OK")


def _pace_layout(v):
    """(rows, right edge) of the PACE tile grid, from the widgets' actual laid-out geometry.

    Mapped to the PAGE, not read off the tiles' own x: they sit inside a section column now, so a
    local coordinate would measure the column and call a page twice as wide the same width."""
    _g, tiles, _group = v._tile_grids[1]
    vis = [t for t in tiles if t.isVisible()]
    return (len({t.mapTo(v, t.rect().topLeft()).y() for t in vis}),
            max(t.mapTo(v, t.rect().topLeft()).x() + t.width() for t in vis))


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
    the laid-out size is a property of the layout, not of the screen.

    THE PANE IS 900 px AND NOT 1000, and the 100 px is the whole of what the column reflow moved.
    The circle is sized from its own SECTION COLUMN now, not from the page, and 1000 px of pane is
    two columns of 476 — so the ceiling that applies there is GG_HEIGHT_WIDE and the column
    decides, which is a different assertion from this one. 900 px is a single column (the page
    composes from two PAGE_COL_MIN_PX columns up), so it is still the pane class this test was
    written for: wide enough that the CEILING decides and nothing about DPR can move it. The
    dashboard's own sizing is asserted below instead of overwritten here."""
    _app()
    from studio.stats_panel import GG_ASPECT, GG_HEIGHT, StatsView
    v = StatsView(_fake_view_session())
    v.show()
    v.resize(900, 900)
    _pump()
    assert v._column_count() == 1, "setup: this check is about a single-column pane"
    expected = (int(GG_HEIGHT * GG_ASPECT), GG_HEIGHT)
    assert (v.gg.width(), v.gg.height()) == expected, (v.gg.width(), v.gg.height())
    # Pinned in both directions — a MAXIMUM width (what it used to carry) still lets the
    # DPR-dependent sizeHint choose the actual number below the cap.
    assert v.gg.minimumWidth() == v.gg.maximumWidth() == expected[0]
    assert v.gg.minimumHeight() == v.gg.maximumHeight() == expected[1]
    v.hide()
    # ...and the same pinning on a COMPOSED page, where the circle grows to its column instead:
    # still exactly 2:1, still fixed in both axes, and never wider than the column carrying it.
    v = StatsView(_fake_view_session())
    v.show()
    v.resize(1900, 900)
    _pump()
    assert v._column_count() >= 2, "setup: 1900px of pane must compose"
    assert v.gg.width() == int(v.gg.height() * GG_ASPECT), (v.gg.width(), v.gg.height())
    assert v.gg.minimumWidth() == v.gg.maximumWidth() == v.gg.width()
    assert v.gg.minimumHeight() == v.gg.maximumHeight() == v.gg.height()
    assert v.gg.width() <= v._column_width(), (v.gg.width(), v._column_width())
    assert v.gg.height() > GG_HEIGHT, "a dashboard column has room for the bigger circle"
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
    text_good = v_good.trust_card.text()
    session.gmeter_cross = lambda: halved
    v_bad = StatsView(session)
    text_bad = v_bad.trust_card.text()
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
    text = v.trust_card.text().lower()
    assert "auto-fitted, not confirmed" in text
    assert "track: unknown" in text
    # Both counts stated, and the denominator is the laps FOUND (lap_count) — not valid+excluded,
    # which would be arithmetic invented to make the two numbers meet.
    assert "statistics use: 2 of the 5 laps found" in text and "3 ⊘ excluded" in text
    # ...and each of those is its OWN row, marked as a caveat — that is what the card being a fact
    # list rather than a paragraph buys, and it is what makes the alarms scannable. The caveats
    # lead: no provenance row may sort above one.
    caveats = [t for t, _v, c in v.trust_card.rows() if c]
    assert {"Start/finish line", "Track", "Statistics use"} <= set(caveats), caveats
    marks = [c for _t, _v, c in v.trust_card.rows()]
    assert marks == sorted(marks, reverse=True), f"caveats must lead the card: {marks}"

    clean = StatsView(_fake_view_session(excluded=()))  # verified, named track, nothing dropped
    text = clean.trust_card.text().lower()
    for phrase in ("auto-fitted", "track: unknown", "excluded"):
        assert phrase not in text, f"clean session must not claim {phrase!r}"
    # (the fixture does carry a ⚠ dropout lap, so "no caveats at all" is not the claim — the claim
    # is that the three the session does not have are absent, and that the caveats still lead)
    clean_marks = [c for _t, _v, c in clean.trust_card.rows()]
    assert clean_marks == sorted(clean_marks, reverse=True), clean_marks
    assert {"Start/finish line", "Track", "Statistics use"}.isdisjoint(
        {t for t, _v, c in clean.trust_card.rows() if c}), clean.trust_card.rows()
    print("test_stats_view_trust_card_names_the_sessions_own_problems OK")


def test_stats_view_trust_card_names_a_break_in_series():
    """The card's fourth trust-breaking fact, and the one it had no name for.

    A skipped chapter, or a chapter whose telemetry stops covering its video, means the recording
    closes over a gap and times on the two sides are not on the same footing. Both were already
    DETECTED — they were only ever mentioned in the transient load notice, which is long gone by
    the time anyone opens this page.

    The NEGATIVE is asserted with it: a plain multi-chapter recording is not a break. load.py's own
    measurement says a seam does not break a GPS9 run and steps the axis by 0.000127 s, so a rule
    that fired on every chaptered session would fire on both reference recordings and mean nothing.

    Stated in the card's own prose, NOT as the exported `[b]` code — a code needs a key and this
    card is a list of sentences (studio/data_quality.py's vocabulary note says why)."""
    _app()
    from studio.stats_panel import StatsView

    class _Map:
        def __init__(self, desynced=()):
            self._d = list(desynced)

        def desynced_chapters(self):
            return list(self._d)

    plain = _fake_view_session()
    plain.chapters = _Map()                       # chaptered, in sync
    assert "break in series" not in StatsView(plain).trust_card.text().lower()

    sess = _fake_view_session()
    sess.skipped_chapters = ["GX010060.MP4"]
    v = StatsView(sess)
    text = v.trust_card.text().lower()
    assert "break in series" in text, text
    assert "could not be read" in text and "closes over the gap" in text, text
    # It is a CAVEAT row, so it sorts with the other trust-breaking facts and above provenance.
    caveats = [t for t, _val, c in v.trust_card.rows() if c]
    assert "Break in series" in caveats, v.trust_card.rows()
    marks = [c for _t, _val, c in v.trust_card.rows()]
    assert marks == sorted(marks, reverse=True), f"caveats must lead the card: {marks}"

    desync = _fake_view_session()
    desync.chapters = _Map([("GX020060.MP4", 4.2)])
    assert "telemetry than video" in StatsView(desync).trust_card.text().lower()
    print("test_stats_view_trust_card_names_a_break_in_series OK")


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
    line = next(ln for ln in v.trust_card.text().split("\n") if ln.startswith("Timing:"))
    assert line == "Timing: GPS9 true clock · 0% of moving fixes rejected", line
    assert "WHILE MOVING" in v.trust_card.toolTip()
    # The measured value is the shipped one — the fix was the sentence, not the maths.
    assert sess.timing_quality.dropped_pct() == 0
    print("test_stats_view_trust_card_names_the_moving_fix_population OK")


def test_stats_view_states_the_missing_accelerometer():
    """With no IMU the trust card used to go silent about the g channel — exactly when the peak-g
    tiles, the per-lap g columns and the Grip column all render em-dashes."""
    _app()
    from studio.stats_panel import NO_GMETER_NOTE, StatsView

    v = StatsView(_fake_view_session(has_g=False))
    assert NO_GMETER_NOTE in v.trust_card.text()
    assert v.no_gmeter_note.isVisibleTo(v)                  # said again beside the dashes
    assert v.t_peak_lat.value.text() == "—"                 # the dash it explains
    v = StatsView(_fake_view_session(has_g=True))
    assert NO_GMETER_NOTE not in v.trust_card.text()
    assert not v.no_gmeter_note.isVisibleTo(v)
    assert "IMU lateral" in v.trust_card.text()
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
    assert v.no_laps_note.isVisibleTo(v) and v.no_laps_prose.isVisibleTo(v)
    # No provisional banner and no "every lap time BELOW" line when there is nothing below —
    # the empty-state block already names placing the start line as the next action.
    assert not v.provisional_banner.isVisibleTo(v)
    assert "below" not in v.trust_card.text().lower()
    # THE BLOCK IS TWO LABELS, so the copy is read as the block. `#ProvisionalBanner` is an 11 px
    # semibold amber call-to-action LINE and it was carrying all 308 characters; the statement
    # stays in the strip and the why/what-next moved into the app's prose step at the app's prose
    # measure (tests/test_measure_floors.py owns that geometry). Reading one label alone is how a
    # split like this silently loses half a sentence, so both are read here.
    note = f"{v.no_laps_note.text()} {v.no_laps_prose.text()}".lower()
    assert "no complete laps" in note and "start/finish line" in note   # reason + next action
    assert not v._pace_section.isVisibleTo(v) and not v._speed_section.isVisibleTo(v)
    tiles = ("t_best", "t_median", "t_race_pace", "t_rolling", "t_digest", "t_sigma", "t_spread",
             "t_cov", "t_within", "t_trend", "t_vmax", "t_vmin", "t_peak_lat", "t_peak_brake")
    dashed = [n for n in tiles
              if getattr(v, n).isVisibleTo(v) and getattr(v, n).value.text() == "—"]
    assert dashed == [], f"dash-only tiles still visible: {dashed}"
    assert v.t_duration.isVisibleTo(v) and v.t_duration.value.text() == "1:01"  # real recording
    assert v.trust_card.text() != "—"                       # the diagnostic stays on the page
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
    lab = v.trust_card
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


# ------------------------------------------------------------- Phase 4: the page fits its pane
#: The two widths this page's own quadrant has at the app's shipped window sizes, measured on the
#: real CentralView (1440x900 -> 503 px of viewport, 1280x800 -> 445). The page is a QUADRANT
#: first and a ⌘⇧S dashboard second, and every legibility claim below is made at these two.
QUADRANT_WIDTHS = (503, 445)


def _laid_out(width, height=760, **kw):
    """A real, shown, settled StatsView at a given pane size."""
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(**kw))
    v.resize(width, height)
    v.show()
    _settle(8)
    return v


def test_the_stats_page_never_scrolls_sideways_in_its_own_quadrant():
    """The page laid itself out WIDER than the pane it lives in, and then scrolled to it.

    Each report table pinned itself to the exact width of its own columns and the friction circle
    to 2:1 around a 220 px height — 730 px and 440 px against a 503 px quadrant. The larger of
    those became the scroll body's minimum, so every section heading, every tile row and the whole
    DATA TRUST card was wrapped at a width the reader could not see and then had to be dragged
    into view. Measured on the shipped default: body 742 px inside a 503 px viewport.

    The contract is the body's width, not the scrollbar's visibility, because that is the thing
    that decides where every other widget on the page wraps."""
    for width in QUADRANT_WIDTHS:
        v = _laid_out(width)
        viewport = v._scroll.viewport()
        body = v._scroll.widget()
        assert body.width() <= viewport.width(), (
            f"the Stats page lays out {body.width()}px of content in a {viewport.width()}px "
            f"pane, so everything on it wraps to a width the reader cannot see")
        assert not v._scroll.horizontalScrollBar().isVisible(), (
            f"horizontal scrollbar on the Stats page at a {width}px quadrant")
        # ...and the honesty rule the page-level scroll used to buy is KEPT, moved to the widget
        # that actually overflows: a report table narrower than its columns grows its OWN bar
        # rather than clipping a column in silence.
        for table in (v.lap_table, v.corners_table):
            if not table.isVisible():
                continue
            assert table.width() <= viewport.width(), (table.width(), viewport.width())
            content = sum(table.columnWidth(c) for c in range(table.columnCount()))
            if content > table.viewport().width():
                assert table.horizontalScrollBar().isVisible(), (
                    f"{table.objectName() or table} hides {content - table.viewport().width()}px "
                    f"of columns with no scrollbar to say so")
        v.hide()
    print(f"test_the_stats_page_never_scrolls_sideways_in_its_own_quadrant OK ({QUADRANT_WIDTHS})")


def test_the_data_trust_card_fits_the_pane_it_is_given():
    """DATA TRUST was clipped mid-word, and the fix is structural rather than a smaller font.

    Shipped, the card was one word-wrapping QLabel holding up to seven `·`-separated sentences.
    It wrapped at the scroll BODY's width — which the over-wide report tables had pushed to 742 px
    inside a 503 px quadrant — so its longest line ran 61 px past the right edge of the viewport
    and simply stopped ("…longitudinal r=+0.82 · 3468"). At 1280x800 it was 119 px.

    Measured the way the defect was: the width the text PAINTS at, against the width the pane can
    show. Asserted at both quadrant widths AND at dashboard width, because the card has to survive
    a ~500 px column and the maximized ⌘⇧S page with the same treatment."""
    from PySide6.QtCore import QRect, Qt
    worst = []
    for width in (*QUADRANT_WIDTHS, 1420):
        # The worst case: every caveat present as well as every provenance fact.
        v = _laid_out(width, excluded=(5, 6, 7))
        v.session.timing_verified = False
        v.session.track_name = None
        v.refresh()
        _settle(8)
        card = getattr(v, "trust_card", None) or v.trust_label
        viewport = v._scroll.viewport()
        # Every text-bearing half of the card, painted at the width the layout gave it.
        labels = ([w for pair in card._widgets for w in pair if w.isVisible()]
                  if hasattr(card, "_widgets") else [card])
        assert labels
        for label in labels:
            painted = label.fontMetrics().boundingRect(
                QRect(0, 0, max(label.width(), 1), 10000), Qt.TextWordWrap, label.text())
            right = label.mapTo(v._scroll.widget(), label.rect().topLeft()).x() + painted.width()
            worst.append(right - viewport.width())
            assert right <= viewport.width(), (
                f"at a {width}px pane the DATA TRUST line {label.text()[:40]!r} paints to "
                f"{right}px in a {viewport.width()}px viewport — {right - viewport.width()}px of "
                f"it is off-screen")
        v.hide()
    print(f"test_the_data_trust_card_fits_the_pane_it_is_given OK "
          f"(worst margin {-max(worst)}px to spare)")


def test_the_friction_circles_axis_titles_fit_inside_the_chart():
    """The one chart on this page painted 88 px of its y-axis title outside itself.

    pyqtgraph rotates a left-axis title, so its LENGTH is spent vertically: "longitudinal g
    (− braking · + accelerating)" is a 304 px box, and the axis is 173 px tall in the quadrant.
    Both ends were cut, including the word "accelerating" — the half of the label that says which
    way is braking. The x title lost 7.4 px through its descenders to a separate defect: pyqtgraph
    reserves 0.8 of a title's bounding height and then nudges the title 5 px further out (see
    widgets.budget_plot_gutters).

    Measured in SCENE coordinates against what the viewport can show, because that is the
    rectangle that decides which pixels exist."""
    for width in (*QUADRANT_WIDTHS, 1420):
        v = _laid_out(width)
        plot = v.gg.getPlotItem()
        viewport = v.gg.viewport()
        seen = 0
        for side in ("left", "bottom"):
            axis = plot.getAxis(side)
            label = axis.label
            assert axis.labelText, f"the {side} axis lost its title"
            r = label.mapRectToScene(label.boundingRect())
            seen += 1
            over = {"left": -r.left(), "top": -r.top(),
                    "right": r.right() - viewport.width(),
                    "bottom": r.bottom() - viewport.height()}
            bad = {k: round(px, 1) for k, px in over.items() if px > 0.5}
            assert not bad, (
                f"at a {width}px pane the friction circle's {side} title "
                f"{axis.labelText!r} paints outside the chart: {bad}")
        assert seen == 2
        v.hide()
    print("test_the_friction_circles_axis_titles_fit_inside_the_chart OK")


# --------------------------------------------------- the ⌘⇧S dashboard composes at width
#: The MAXIMIZED pane widths the app's shipped window sizes give this page, measured on the real
#: CentralView (1280x800 -> 1260 px, 1440x900 -> 1420, 1920x1200 -> 1900). The quadrant widths
#: above are the page's first duty; these are the surface ⌘⇧S opens onto.
#:
#: 1420 IS THE ONE THAT WAS MISSING, and its absence is what let a P1 ship green. It is the app's
#: OWN DEFAULT window maximized, and it sat between the other two in exactly the band where a
#: width-only packer produced its narrowest columns — three of 449 px, against report tables that
#: want up to 718. A guard list that samples the extremes of a range is not sampling the range.
DASHBOARD_WIDTHS = (1260, 1420, 1900)


def test_the_dashboard_composes_into_columns_and_the_quadrant_does_not():
    """The page is ONE column in a quadrant and 2-3 columns on the ⌘⇧S dashboard.

    Measured before this guard: at 1920x1200 the maximized page laid 1900 px of pane out as a
    single 700 px column of content — every table capped at its own width, ~70 % of the canvas
    empty dark — and the scroll body stood 3135 px tall. The quadrant is NOT a small dashboard and
    must not acquire a second column: 675 px (the 1920x1200 quadrant, the widest either shipped
    window gives) is the negative control here, not an omission."""
    for width in QUADRANT_WIDTHS + (675,):
        v = _laid_out(width)
        assert v._column_count() == 1, (
            f"a {width}px QUADRANT composed into {v._column_count()} columns — the single-column "
            f"scroll is what every quadrant renders")
        v.hide()
    for width in DASHBOARD_WIDTHS:
        v = _laid_out(width)
        cols = v._column_count()
        assert 2 <= cols <= 3, f"a {width}px maximized page is still {cols} column(s)"
        # ...and every column got AT LEAST the width it declared it needs. Not "the columns are
        # equal", which is what this asserted first and is exactly the assumption the P1 was made
        # of: equal thirds of 1420 px are 449 px each, and three of the five report tables want
        # more than that. A column is as wide as its widest non-reflowing member, and the packer
        # only composes an arrangement it can pay for.
        planned = v._planned_widths(v._layout)
        for gcol, groups in enumerate(v._layout):
            need = v._grid_column_min(groups)
            assert planned[gcol] >= need, (
                f"at {width}px grid column {gcol} is planned at {planned[gcol]}px for content "
                f"that needs {need}px")
            for group in groups:
                got = v._columns[group].width()
                assert got >= need, (
                    f"at {width}px the column holding group {group} was laid out at {got}px "
                    f"against a {need}px minimum")
        # The content has to actually REACH across the canvas: the old page's rightmost ink
        # stopped at ~700 px whatever the pane.
        right = max(c.x() + c.width() for c in v._columns)
        assert right >= 0.9 * v._scroll.viewport().width(), (
            f"at {width}px the composed columns still end at {right}px of "
            f"{v._scroll.viewport().width()}px of canvas")
        v.hide()
    print(f"test_the_dashboard_composes_into_columns_and_the_quadrant_does_not OK "
          f"({QUADRANT_WIDTHS + (675,)} -> 1 col, {DASHBOARD_WIDTHS} -> 2-3)")


def test_the_composed_page_keeps_the_shipped_section_order():
    """Stacked, the three columns concatenate back into the page that shipped.

    The single-column page is not a fallback — it is what every quadrant renders — so the split
    has to be into CONTIGUOUS groups. This reads the section headings in visual order (top to
    bottom, then left to right) at one column and asserts the dashboard is a re-DEALING of that
    order rather than a re-ordering of it: each column's own sections stay in sequence, and the
    columns concatenate to the quadrant's sequence."""
    from PySide6.QtWidgets import QLabel

    def headings(view):
        out = []
        for holder in view._columns:
            names = []
            for lab in holder.findChildren(QLabel):
                if lab.property("role") == "BarLabel" and lab.isVisible():
                    names.append((lab.mapTo(holder, lab.rect().topLeft()).y(), lab.text()))
            out.append([t for _y, t in sorted(names)])
        return out

    quadrant = _laid_out(445)
    order = headings(quadrant)
    quadrant.hide()
    flat = [t for column in order for t in column]
    assert flat, "no section headings found — the walk is broken, not the page"
    for width in DASHBOARD_WIDTHS:
        v = _laid_out(width)
        assert headings(v) == order, (
            f"at {width}px the dashboard re-ordered the page instead of re-dealing it:\n"
            f"  {headings(v)}\n  != {order}")
        v.hide()
    print(f"test_the_composed_page_keeps_the_shipped_section_order OK ({len(flat)} sections)")


def _hidden_columns(v):
    """Every visible report table that is hiding a column behind its OWN horizontal scrollbar,
    as [(first header, px of column hidden)].

    Asked of the WIDGET (`_needs_bar`), not of a subtraction: `content_width()` includes the frame
    and two spare pixels, so a raw content-minus-viewport reads +4 px on a table that fits
    perfectly, and a guard written that way is either always red or tuned to a constant nobody
    can explain."""
    from studio.stats_panel import _ReportTable
    out = []
    for t in v.findChildren(_ReportTable):
        if t.isHidden() or not t._needs_bar():
            continue
        head = t.horizontalHeaderItem(0)
        columns_px = t.content_width() - 2 * t.frameWidth() - 2
        out.append(((head.text() if head else "?"), columns_px - t.viewport().width()))
    return out


def test_no_composed_column_hides_a_report_table_column():
    """THE ASSERTION WHOSE ABSENCE LET A P1 SHIP GREEN.

    Composing the page into columns narrower than its report tables does not wrap them — the
    tables are content-sized and scroll (see _ReportTable) — it HIDES their rightmost columns
    behind an inner scrollbar. Measured on D24 before the packer learned to ask: at the app's own
    default 1440x900 window, maximized, `Apex best · Apex med · Grip %` were gone from CORNERS,
    `Trap med · Exit Δ` from STRAIGHTS, `Brake s · Coast s` from PER LAP and `m later` from
    BRAKING — 266 / 159 / 90 / 81 px of hidden columns on a page whose single-column form showed
    all of them.

    The outer page-level check below is NOT this check, and believing it was is how the defect got
    through: the page fit its pane perfectly the whole time. The scroll had moved INSIDE the
    tables, which is the one place `_ReportTable` is designed to put it and the one place nothing
    was looking."""
    for width in DASHBOARD_WIDTHS:
        v = _laid_out(width)
        hidden = _hidden_columns(v)
        assert not hidden, (
            f"at a {width}px composed page these tables hide columns: "
            + ", ".join(f"{name} by {px}px" for name, px in hidden))
        v.hide()
    print(f"test_no_composed_column_hides_a_report_table_column OK ({DASHBOARD_WIDTHS})")


def test_composing_never_hides_a_column_the_single_column_page_showed():
    """The comparison that names the regression: composing may not LOSE a reader anything.

    A quadrant is allowed to scroll a table — that is the shipped contract, and at 445 px the
    widest table is 273 px too wide for the pane whatever anyone does. What is not allowed is for
    the page to hide a column at a width where it did not have to, so this pins the composed page
    against the SAME page at the same pane width forced to a single column: whatever the one-column
    layout can show, the composed one shows too."""
    for width in DASHBOARD_WIDTHS:
        v = _laid_out(width)
        composed = dict(_hidden_columns(v))
        # ...the same page, same width, dealt into one column (what main renders here).
        from studio.stats_panel import PAGE_LAYOUTS
        v._layout = PAGE_LAYOUTS[-1]
        v._place_columns(PAGE_LAYOUTS[-1])
        _settle(8)
        single = dict(_hidden_columns(v))
        lost = {k: composed[k] for k in composed if k not in single}
        assert not lost, (
            f"at {width}px composing hid columns the single-column page showed: {lost}")
        v.hide()
    print("test_composing_never_hides_a_column_the_single_column_page_showed OK")


#: The two recordings this page's composition was measured on, as the numbers the packer actually
#: reads: (group minimum widths, group content heights). The shared stub cannot produce either —
#: it has two laps and no corners, so its CORNERS and STRAIGHTS tables are hidden and its columns
#: sit at the prose floor — and a composition guard that only ever runs on it is asserting an
#: invariant against numbers no user has. These are measured on the real app (see the PR body):
#: D24 = 38 laps, 0062 = 65 laps, both on this machine's fixture disk.
REAL_PAGES = {
    #                   group min WIDTHS      group content HEIGHTS
    "D24 (38 laps)":  ((440, 718, 610), (983, 907, 1338)),
    "0062 (65 laps)": ((440, 718, 610), (929, 921, 2322)),
}


def test_the_packer_picks_the_documented_form_on_both_real_recordings():
    """The composition table in stats_panel's PAGE_LAYOUTS prose, as an assertion.

    `_choose_layout` is a pure decision over three inputs — the pane, each group's minimum WIDTH
    and each group's content HEIGHT — so it can be asked the real recordings' numbers without
    loading 32 GB of video. That is the point: the guards below run on the shared stub, whose
    tables are narrower and shorter than any real session's, and the stub composes ((0,1),(2,))
    at 1260 where the real recording composes ((0,),(1,2)). Both are right for their content;
    only one of them is what a user sees.

    The 65-lap row is the regression this pins. Group 2 holds PER LAP, one uncapped row per clean
    lap, so it grows with the recording while groups 0 and 1 do not: at 38 laps it is 1338 px
    against a 1894 px stack, at 65 laps it is 2322 px against the same stack — and the balanced
    form then bands the column beside it by 230 px at the app's own default window."""
    from studio.stats_panel import PAGE_LAYOUTS

    three, balanced, narrow_left, single = PAGE_LAYOUTS
    expected = {
        "D24 (38 laps)":  {1260: narrow_left, 1420: balanced, 1900: three},
        "0062 (65 laps)": {1260: narrow_left, 1420: narrow_left, 1900: three},
    }
    v = _laid_out(1900)
    for name, (mins, heights) in REAL_PAGES.items():
        v._group_min_width = lambda g, _m=mins: _m[g]
        v._group_height = lambda g, _h=heights: _h[g]
        for pane, want in expected[name].items():
            v._pane_width = lambda _p=pane: _p
            got = v._choose_layout()
            assert got == want, f"{name} at {pane}px chose {got}, expected {want}"
            # ...and what it chose is a form the SPAN check accepts, which is the half no width
            # arithmetic can see.
            assert v._span_fits(want), f"{name} at {pane}px: {want} bands"
    assert single == PAGE_LAYOUTS[-1]
    v.hide()
    print(f"test_the_packer_picks_the_documented_form_on_both_real_recordings OK "
          f"({len(REAL_PAGES)} recordings x 3 widths)")


def test_a_long_session_never_bands_a_stacked_column():
    """A COLUMN THAT SPANS THE BAND MAY NOT BE TALLER THAN THE ONE STACKING BESIDE IT.

    `_place_columns` spans a lone grid column across both rows so every band holds exactly one
    item and no gap can open between two sections. That shape has one failure mode, and the
    docstring used to argue it away in prose from ONE recording's heights: if the spanning group
    is taller than the two stacked beside it, Qt grows both of those rows to fit it and each
    stacked holder pays the difference out as empty space under its last section — an empty band
    in the middle of a column, next to a full one.

    It is reachable from the fixture disk. PER LAP takes one row per clean lap and is uncapped
    while groups 0 and 1 gain nothing per lap, so ~50 laps flips the inequality; the owner's own
    0062 recording has 65 and opened a 230 px band in the LEFT column of the default 1440x900
    window under ⌘⇧S. Reproduced here by inflating the lap table the way a session would —
    `setRowCount` + `_fit_table`, which is what `_refresh_lap_table` does — at the three widths
    the band covered.

    THE BAND IS MEASURED INSIDE THE HOLDER, not between two of them: each column layout ends in
    addStretch(1), so a holder grown past its content keeps its sections packed at the top and
    puts the slack underneath. Reading the gap between two holders' geometries finds 4 px (the
    grid's own spacing) and misses the defect entirely — which is how the first version of this
    check passed against a page that was visibly banded."""
    from studio import theme

    for rows in (38, 65):
        v = _laid_out(1420)
        for width in (1420, 1600, 1800):
            v.resize(width, 900)
            _settle(4)
            v.lap_table.setRowCount(rows)
            v._fit_table(v.lap_table)
            v._reflow_tiles()
            _settle(8)
            for groups in v._layout:
                for group in groups[:-1]:     # the last one's slack is the page's own bottom
                    holder = v._columns[group]
                    band = holder.height() - holder.layout().sizeHint().height()
                    assert band <= theme.SPACE_XS, (
                        f"{rows} laps at {width}px: group {group} is laid out {band}px taller "
                        f"than its content inside a stacked column — an empty band beside a full "
                        f"column, because a spanning group made Qt grow the rows")
        v.hide()
    print("test_a_long_session_never_bands_a_stacked_column OK (38 and 65 laps x 3 widths)")


def test_the_dashboard_never_scrolls_sideways_either():
    """The quadrant's own rule (above), held at the widths ⌘⇧S opens onto.

    A column reflow is only a fix if the content FITS the columns it is dealt into; a page that
    composes and then scrolls sideways has moved the defect, not removed it."""
    for width in DASHBOARD_WIDTHS:
        v = _laid_out(width)
        body, viewport = v._scroll.widget(), v._scroll.viewport()
        assert body.width() <= viewport.width(), (
            f"at {width}px the composed page lays out {body.width()}px in {viewport.width()}px")
        assert not v._scroll.horizontalScrollBar().isVisible(), \
            f"horizontal scrollbar on the composed page at {width}px"
        v.hide()
    print(f"test_the_dashboard_never_scrolls_sideways_either OK ({DASHBOARD_WIDTHS})")


def test_the_trend_sparkline_is_capped_by_its_sample_count():
    """The one widget on the page with no width of its own.

    Every table caps itself at its content and the friction circle is pinned in both axes, so on
    the maximized dashboard the sparkline was the only thing left to absorb the slack: 24 laps
    stretched across 1850 px, a slope drawn at 77 px per lap. Its ceiling is now its samples'
    (SPARK_AXIS_W + n x SPARK_PX_PER_LAP), and being a MAXIMUM it still yields to a narrow pane."""
    from studio.stats_panel import SPARK_AXIS_W, SPARK_PX_PER_LAP, StatsView

    # D24's own shape: 24 clean laps in a ~1.3 s band. `lap_time_trend` lives on the SESSION and
    # the shared stub does not carry it (the sparkline hides with none), so it is added here.
    times = [68.2 + 0.06 * i for i in range(24)]
    sess = _fake_view_session()
    sess.lap_time_trend = lambda: list(enumerate(times))
    v = StatsView(sess)
    v.resize(1900, 900)
    v.show()
    _settle(8)
    n = len(times)
    assert not v.spark.isHidden(), "setup: the stub must draw a sparkline at all"
    cap = v.spark.maximumWidth()
    assert cap <= SPARK_AXIS_W + n * SPARK_PX_PER_LAP, (cap, n)
    assert v.spark.width() <= cap, (v.spark.width(), cap)
    assert v.spark.width() < 0.6 * v._scroll.viewport().width(), (
        f"{n} samples still stretched across {v.spark.width()}px of a "
        f"{v._scroll.viewport().width()}px page")
    v.hide()
    print(f"test_the_trend_sparkline_is_capped_by_its_sample_count OK ({n} samples, cap {cap}px)")


def test_the_sparkline_frame_survives_one_traffic_lap():
    """A robust y ceiling, and BOTH y labels still real lap times.

    On D24 one 1:17.136 lap against 23 in 1:08.2-1:09.5 spent 78 % of the band on the gap to a
    single lap, leaving the session's whole story in a floor-hugging line. The frame is fenced at
    Tukey's "far out" (q3 + 3 x IQR) and the ceiling is the slowest lap AT OR UNDER the fence — a
    lap time, never the fence itself, because a y axis printing a percentile as if it were a lap
    is a worse defect than a flat line. Self-limiting: a session with no outlier is unfenced."""
    from studio.stats_panel import StatsView

    clean = [68.2, 68.4, 68.6, 68.7, 68.9, 69.0, 69.2, 69.4, 69.6, 69.9]
    hi, over = StatsView._spark_frame(clean)
    assert (hi, over) == (69.9, []), "a session with no outlier must keep every lap in frame"
    traffic = clean + [77.1]
    hi, over = StatsView._spark_frame(traffic)
    assert over == [77.1] and hi == 69.9, (hi, over)
    assert hi in traffic, "the ceiling has to be a lap someone drove"
    # A merely SLOW lap is not an outlier — it stays in the frame.
    hi, over = StatsView._spark_frame(clean + [71.0])
    assert over == [] and hi == 71.0, (hi, over)
    # Too few laps for quartiles to describe anything, and a degenerate spread: no fence at all.
    assert StatsView._spark_frame([68.2, 77.1]) == (77.1, [])
    assert StatsView._spark_frame([70.0] * 9 + [99.0]) == (99.0, [])
    print("test_the_sparkline_frame_survives_one_traffic_lap OK")


def test_every_data_trust_row_fits_on_one_line_once_it_can():
    """DATA TRUST row 3 wrapped at EVERY width, and the width was never the reason.

    Its value is 421 px of ink. In the 445 px quadrant it genuinely needs two lines and does —
    but it kept both of them on a 1900 px dashboard, painted vertically centred in a box twice its
    text's height, because widgets.WrapLabel measured itself against its OWN previous minimum
    (QLabel.heightForWidth is clamped by minimumSize, so the answer could only ratchet upwards).
    Asserted here on the SHIPPED card at the shipped widths, not on a synthetic label."""
    from PySide6.QtGui import QFontMetrics

    v = _laid_out(1900)
    rows = [(t, val) for t, val in v.trust_card._widgets if val.isVisible()]
    assert len(rows) >= 3, "setup: the stub must produce a multi-row trust card"
    for term, value in rows:
        fm = QFontMetrics(value.font())
        ink = fm.horizontalAdvance(value.text())
        if ink > value.width():
            continue                     # genuinely too long for its column: wrapping is correct
        assert value.height() <= fm.height() + 1, (
            f"DATA TRUST {term.text()!r}: {ink}px of ink in a {value.width()}px column, laid out "
            f"{value.height()}px tall for a {fm.height()}px line")
    v.hide()
    print(f"test_every_data_trust_row_fits_on_one_line_once_it_can OK ({len(rows)} rows)")


def test_the_cross_check_sample_count_is_grouped():
    """"346713 samples" is read digit by digit; "346,713" is read at a glance."""
    v = _laid_out(1900)
    values = [val for _term, val, _caveat in v.trust_card.rows()]
    line = next(t for t in values if "samples" in t)
    assert "1,000 samples" in line, line
    v.hide()
    print("test_the_cross_check_sample_count_is_grouped OK")


def test_the_peak_braking_tile_says_it_is_a_smoothed_peak():
    """§4.3: the tile said "smoothed GPS speed derivative" and never said the WINDOW — and a window
    is the whole story for a MAXIMUM.

    Measured on the D24 0060 pair (38 valid laps): the per-lap peak runs a median 0.862 g as the
    service reports it against 1.081 g on the same signal unsmoothed, and the session max the tile
    prints reads 1.27 g where the instantaneous peak was 1.94 g. (Those unsmoothed figures are
    taken on the 10 Hz GPS grid; on the 50 Hz grid the g-meter actually serves, the same
    unsmoothed per-lap peak is 1.58 g with the 2.0 g MAX_LONG_G clip firing — so the window
    removes MORE than this pairing suggests, not less.) The smoothing is the right choice
    — a raw d|v|/dt peak is GPS quantization noise, and this repo's rule is percentiles over raw
    maxima — but a number 20-35% under the instantaneous one has to say which it is.

    The window is READ from `gmeter.LONG_SMOOTH_S`, never retyped, so the copy cannot drift from the
    signal it describes (the §5.5 lesson: a constant typed into honesty copy rots)."""
    _APP  # noqa: B018
    import pathlib

    from studio import gmeter
    from studio.stats_panel import GG_TOOLTIP, StatsView
    from studio.stats_panel import __file__ as SP_FILE

    view = StatsView(_fake_view_session())
    tip = view.t_peak_brake.toolTip()
    window = f"{gmeter.LONG_SMOOTH_S:g} s"
    assert window in tip, (f"the tile never states the {window} window", tip)
    assert "SUSTAINED" in tip, ("the tile must say WHICH peak this is", tip)
    # ...and the friction circle, whose axis is the same signal.
    assert window in GG_TOOLTIP and "SUSTAINED" in GG_TOOLTIP, GG_TOOLTIP
    # INTERPOLATED, NOT TYPED — checked on the source rather than by reloading the module: a
    # reload rebinds StatsView, and every later test in this process holding the old class would
    # then be comparing two different types. The window may appear in this file only through the
    # constant.
    src = pathlib.Path(SP_FILE).read_text(encoding="utf-8")
    literal = f"{gmeter.LONG_SMOOTH_S:g} s"
    for line in src.splitlines():
        if literal in line and "LONG_SMOOTH_S" not in line:
            raise AssertionError(
                f"stats_panel types the smoothing window as a literal — it must read the "
                f"constant, or the copy rots the moment the signal changes: {line.strip()!r}")
    print("ok brake-g: both g surfaces state the smoothing window, read from the constant")


def test_race_pace_and_trend_do_not_treat_a_gap_as_consecutive():
    """§4.2: both statistics ran over the clean-lap times with the lap IDS THROWN AWAY, so
    adjacency in that filtered list stood in for adjacency on track.

    A session whose clean laps are 1,2,3,10,11 — laps 4-9 gone to a pit stop, a spin, or a GPS
    dropout — reported a three-lap "sustained run" spanning 3→10, and a trend in seconds "per lap"
    that had counted six laps as three steps. Latent on both fixtures (their clean sequences are
    gap-free), which is exactly why it needed a fixture with a hole in it."""
    _APP  # noqa: B018
    from studio.stats import best_consecutive_mean, consecutive_runs, theil_sen_slope

    assert consecutive_runs([1, 2, 3, 10, 11]) == [[1, 2, 3], [10, 11]]
    assert consecutive_runs([]) == []
    assert consecutive_runs([7]) == [[7]]

    # laps 1,2,3 are slow; 10,11 are fast. The only 3-lap RUN is 1-3, so race pace is its mean —
    # NOT the mean of {3, 10, 11}, which is what an index window would find.
    ids = [1, 2, 3, 10, 11]
    times = [70.0, 70.0, 70.0, 60.0, 60.0]
    across_the_gap = (70.0 + 60.0 + 60.0) / 3.0
    assert abs(best_consecutive_mean(times, n=3) - across_the_gap) < 1e-9, (
        "fixture must be one where the index window WOULD span the gap",
        best_consecutive_mean(times, n=3), across_the_gap)
    assert abs(best_consecutive_mean(times, n=3, ids=ids) - 70.0) < 1e-9, (
        "race pace crossed a 6-lap gap and called it a sustained run",
        best_consecutive_mean(times, n=3, ids=ids))

    # ...and a run shorter than the window contributes nothing rather than being padded.
    assert best_consecutive_mean([60.0, 61.0, 62.0], n=3, ids=[1, 2, 9]) is None

    # The trend is per LAP, so the hole has to count: the same five times fitted against the ids
    # give a shallower slope than the same times fitted against their positions.
    by_index = theil_sen_slope(times)
    by_lap = theil_sen_slope(times, ids)
    assert by_index is not None and by_lap is not None
    assert abs(by_lap) < abs(by_index), (
        "a gap in the lap numbers must flatten a per-lap slope, not be invisible to it",
        by_index, by_lap)
    print(f"ok pace-gaps: race pace stays inside a run; trend {by_index:+.3f} s/step -> "
          f"{by_lap:+.3f} s/lap once the gap counts")


def test_corners_note_names_both_baselines_and_reconciles_them():
    """Stats ▸ CORNERS: two columns one tab apart are both called a loss and are measured against
    DIFFERENT things — this page's Med loss against each corner's own Best (the quickest anyone
    went through it), the Coaching tab's Time lost against your best lap's same corner.

    On the D24 0060 pair they sum to 3.93 s and 1.02 s: a 3.8x gap, running 1.3x to 2450x corner
    by corner. Neither surface said which baseline it used, so the honest reading of the two
    screens was that one of them was broken. The header has no room to say it (measured: the
    section is 100 px, "Med loss vs best corner" needs 148 px and elides), so the caption under
    the table says it — and reconciles the page's three answers while it is there, with every
    number READ rather than baked."""
    _APP  # noqa: B018
    from types import SimpleNamespace

    from studio.stats import CornerReport
    from studio.stats_panel import StatsView

    def corner(cid, loss):
        return CornerReport(cid=cid, direction=1, n=6, best_s=9.0, median_s=9.0 + loss,
                            sigma_s=0.1, median_loss_s=loss, apex_best_kmh=60.0,
                            apex_median_kmh=58.0, grip_median=0.8, score=0.1 * loss)

    report = [corner(1, 0.20), corner(2, 0.30), corner(3, 0.50)]          # sums to 1.00 s
    opp_rows = [SimpleNamespace(cid=3, time_lost=0.25), SimpleNamespace(cid=2, time_lost=0.15),
                SimpleNamespace(cid=1, time_lost=0.10)]                    # sums to 0.50 s
    sess = _fake_view_session()
    sess.corner_report = lambda: report
    sess.phase_report = lambda: None
    sess.coaching_opportunities = lambda: SimpleNamespace(enough=True, rows=opp_rows)
    view = StatsView(sess)
    note = view.corners_note.text()

    # BOTH baselines, in words, on the face — not only in a tooltip.
    assert "own Best" in note, note
    assert "against your best lap" in note, note
    # ...and the three totals, computed from the data in front of it.
    assert "1.00 s" in note, ("the Med loss column's own sum", note)
    assert "0.50 s" in note, ("the coaching total, the number the other tab prints", note)
    assert "0.25 s of it in its top 3" in note or "0.50 s of it in its top 3" in note, note
    # It must never read as three estimates of one quantity.
    assert "Different baselines" in note, note
    # An empty report hides the note rather than leaving a stale sentence under nothing.
    sess.corner_report = lambda: []
    view.refresh()
    assert view.corners_note.text() == "" and not view.corners_note.isVisible(), (
        view.corners_note.text())
    print("ok corners-note: both baselines named, three totals reconciled, hidden when empty")


def test_digest_tooltip_reads_the_ideal_delta_instead_of_a_baked_range():
    """The digest tile's tooltip used to promise "measured on the owner's recordings the ideal is
    0.33 to 2.67 s the faster of the two" — an empirical range typed into shipping copy. On the
    reviewed screen the two tiles were 5.0 s apart, i.e. the sentence was already false on the
    owner's own data. It now reads the two numbers it is comparing."""
    _APP  # noqa: B018
    from studio.stats_panel import StatsView

    view = StatsView(_fake_view_session())
    tip = view.t_digest.toolTip()
    assert tip, "the digest tile must still explain itself"
    assert "0.33" not in tip and "2.67" not in tip, ("a baked empirical range came back", tip)
    assert "here the ideal is" in tip, tip
    print("ok digest-tooltip: the ideal delta is read, not baked")


# ------------------------------------------------------- the band distributions (histograms)
def test_sample_durations_reconcile_with_the_span_and_skip_gaps():
    """The weighting contract: a lap's per-sample durations must SUM to that lap's measured time,
    less any dropout gap. That is why this is trapezoidal and moving_time_s is not — leading
    attribution drops the last interval on the floor, which a threshold does not care about and a
    distribution does (its bands would sum to 0.1 s under the lap on every recording)."""
    t = np.array([0.0, 0.1, 0.2, 0.3])
    w = sample_durations(t)
    assert abs(w.sum() - 0.3) < 1e-12, w                 # the span, exactly
    assert abs(w[0] - 0.05) < 1e-12 and abs(w[-1] - 0.05) < 1e-12   # ends take half a step
    assert abs(w[1] - 0.1) < 1e-12                                  # interior takes a whole one
    # A dropout gap belongs to NEITHER endpoint: nothing was measured across it.
    gap = np.array([0.0, 0.1, 5.1, 5.2])
    wg = sample_durations(gap)
    assert abs(wg.sum() - 0.2) < 1e-12, wg               # 0.1 + 0.1, the 5 s step contributing 0
    assert abs(wg[1] - 0.05) < 1e-12 and abs(wg[2] - 0.05) < 1e-12
    # Degenerate inputs are shaped, not special-cased by callers.
    assert list(sample_durations([1.0])) == [0.0]
    assert len(sample_durations([])) == 0
    print("ok sample_durations: sums to the span, gaps contribute to neither side")


def test_band_edges_align_to_the_width_and_can_centre_on_zero():
    e = band_edges([13.6, 90.4], 5.0)
    assert e[0] == 10.0 and e[-1] >= 90.4                # aligned to the width, covering the data
    assert all(abs(v % 5.0) < 1e-9 for v in e), e
    # SIGNED: zero must be a band CENTRE, not a boundary — otherwise the fifth of the lap spent
    # near zero splits into a fake "slightly left / slightly right" pair.
    z = band_edges([-1.7, 1.86], 0.2, centre_on_zero=True)
    centres = 0.5 * (z[:-1] + z[1:])
    assert min(abs(centres)) < 1e-9, centres             # a band sits ON zero
    assert abs(z[0] + z[-1]) < 1e-9, z                   # ...and the axis is symmetric about it
    assert z[0] <= -1.7 and z[-1] >= 1.86
    # An empty / all-NaN channel still returns a usable shape.
    assert len(band_edges([], 5.0)) == 2
    assert len(band_edges([np.nan, np.nan], 0.2, centre_on_zero=True)) == 2
    print("ok band_edges: aligned, covering, and zero-centred when signed")


def test_band_seconds_weighs_time_and_not_sample_count():
    """THE discriminating test for the whole feature. Two speeds, one sample each — but one of
    them stands for ten times as long. A sample-count histogram calls them equal; the honest
    answer is 10:1, and it is the answer a driver is asking for."""
    edges = np.array([0.0, 10.0, 20.0])
    got = band_seconds([5.0, 15.0], [1.0, 10.0], edges)
    assert list(got) == [1.0, 10.0], got
    # Zero-weight and non-finite samples are dropped rather than counted as a band's worth of 0.
    assert list(band_seconds([5.0, 15.0], [1.0, 0.0], edges)) == [1.0, 0.0]
    assert list(band_seconds([np.nan, 15.0], [1.0, 2.0], edges)) == [0.0, 2.0]
    assert list(band_seconds([], [], edges)) == [0.0, 0.0]
    print("ok band_seconds: time-weighted, never a count")


def test_pace_split_takes_quartiles_and_refuses_a_session_too_short_to_have_them():
    ids = list(range(20))
    times = [70.0 - 0.1 * i for i in ids]         # lap 19 fastest, lap 0 slowest
    fast, slow = pace_split(ids, times)
    assert len(fast) == len(slow) == 5, (fast, slow)          # 20 // 4
    assert set(fast) == {15, 16, 17, 18, 19} and set(slow) == {0, 1, 2, 3, 4}
    # The floor: never fewer than three a side, however few laps there are…
    f8, s8 = pace_split(list(range(8)), [70.0 - i for i in range(8)])
    assert len(f8) == len(s8) == 3
    # …and below MIN_SPLIT_LAPS there is no comparison at all, rather than a two-lap "group"
    # which is the single-pair defect the whole split exists to avoid.
    assert pace_split(list(range(MIN_SPLIT_LAPS - 1)),
                      [70.0 - i for i in range(MIN_SPLIT_LAPS - 1)]) is None
    # Non-finite lap times are out of the count entirely, not sorted to one end.
    assert pace_split(list(range(9)), [70.0] * 8 + [float("nan")]) is not None
    assert pace_split(list(range(8)), [70.0] * 7 + [float("nan")]) is None
    # Deterministic under ties: identical times break by lap id, so a refresh cannot swap sides.
    tied = pace_split(list(range(12)), [70.0] * 12)
    assert tied[0] == [0, 1, 2] and tied[1] == [9, 10, 11]
    print("ok pace_split: quartiles, a three-lap floor, and a hard gate under it")


def _band_service(*, laps=12, with_g=True):
    """A SessionStats whose laps are synthetic but whose SHAPE is the real one: a 10 Hz speed
    trace per lap and a 50 Hz g series on the media clock, faster laps spending more of the lap at
    the top of both channels."""
    n = 100                                   # ~10 Hz over ~10 s
    lap_times, arrays, windows = {}, {}, {}
    g_t, g_lat = [], []
    for i in range(laps):
        # lap 0 is the quickest and holds the highest speeds; each later lap is 0.1 s slower.
        fast = 1.0 - i * 0.02
        speed = 40.0 + 40.0 * fast * np.abs(np.sin(np.linspace(0, np.pi, n)))
        lap_times[i] = 10.0 + 0.1 * i
        # `elapsed` spans the LAP TIME, as a real lap's does (the materialized lap runs from the
        # interpolated start crossing to the interpolated finish) — which is what makes the
        # reconciliation assertion below a statement about the reducer and not about the fixture.
        elapsed = np.linspace(0.0, lap_times[i], n)
        arrays[i] = (np.cumsum(speed) / 36.0, speed, elapsed)
        windows[i] = (i * 20.0, i * 20.0 + lap_times[i])
        gt = np.arange(windows[i][0], windows[i][1], 0.02)
        g_t.append(gt)
        g_lat.append(1.2 * fast * np.sin(np.linspace(0, 4 * np.pi, len(gt))))
    times = np.concatenate(g_t)
    gm = (_fake_gmeter(times, np.concatenate(g_lat), np.zeros(len(times)))
          if with_g else _fake_gmeter([], [], []))
    return _service(gm=gm, valid=list(range(laps)), lap_times=lap_times,
                    arrays=arrays, windows=windows)


def test_speed_bands_are_seconds_per_lap_and_reconcile_with_the_lap():
    st = _band_service()
    rep = st.speed_bands()
    assert rep is not None and rep.has_split
    # THE RECONCILIATION: the bands of the average clean lap sum to the average clean LAP TIME,
    # not to a sample count and not to a window.
    mean_lap = float(np.mean([10.0 + 0.1 * i for i in range(12)]))
    assert abs(rep.all.total_s - mean_lap) < 1e-9, (rep.all.total_s, mean_lap)
    assert rep.all.laps == 12 and rep.fast.laps == 3 and rep.slow.laps == 3
    assert rep.fast.median_lap_s < rep.slow.median_lap_s
    # The fast group really is the fast one: it spends MORE of the lap in the top band.
    assert rep.fast.seconds[-1] > rep.slow.seconds[-1]
    # One set of edges across all three, or the outlines would not be over the bars.
    assert rep.edges is rep.all.edges
    assert np.array_equal(rep.fast.edges, rep.all.edges)
    # The display unit is the binning unit: an mph page bins in mph, it does not relabel km/h.
    mph = st.speed_bands(scale=0.621371, width=2.5)
    assert mph.all.edges[-1] < rep.all.edges[-1]
    assert abs(mph.all.total_s - rep.all.total_s) < 1e-9   # same driving, same seconds
    assert rep.all.carrying() >= 5
    print("ok speed_bands: s per lap, reconciled, split, and binned in the displayed unit")


def test_lateral_g_bands_read_the_trusted_axis_and_vanish_without_one():
    st = _band_service()
    rep = st.lateral_g_bands()
    assert rep is not None
    centres = rep.all.centres
    assert min(abs(centres)) < 1e-9, centres          # zero-centred (see band_edges)
    # Reconciles with the lap to within ONE g sample (the 50 Hz grid does not land exactly on the
    # lap's two ends), which is the same contract the speed chart holds to exactly.
    mean_lap = float(np.mean([10.0 + 0.1 * i for i in range(12)]))
    assert abs(rep.all.total_s - mean_lap) < 0.02, (rep.all.total_s, mean_lap)
    # The g grid is uniform, so the bands still have to be TIME and not a count — same numbers
    # here, but the type says which, and a resampled grid is exactly where the two diverge.
    assert rep.all.seconds.sum() > 0
    # No accelerometer -> no chart. Not a row of zeroes, not an empty report: None.
    assert _band_service(with_g=False).lateral_g_bands() is None
    print("ok lateral_g_bands: signed, zero-centred, None without an accelerometer")


def test_band_reports_cache_per_argument_and_drop_on_re_segment():
    st = _band_service()
    a = st.speed_bands()
    assert st.speed_bands() is a                       # cached
    assert st.speed_bands(width=10.0) is not a         # …per argument, not per session
    lat = st.lateral_g_bands()
    st.invalidate()
    assert st.speed_bands() is not a and st.lateral_g_bands() is not lat
    print("ok band caches: keyed by argument, dropped by invalidate()")


def test_a_band_chart_never_labels_a_tick_with_a_number_it_does_not_stand_on():
    """The mph regression, pinned. The mph page bands at 2.5, so a three-band stride is a step of
    7.5 — a step >= 1, which the first rule formatted with no decimals: the axis labelled the
    22.5 mph gridline "22" and the 37.5 one "38"."""
    _APP  # noqa: B018
    from studio.stats_panel import _band_tick_step, _band_ticks

    edges = np.arange(15.0, 55.1, 2.5)
    ticks = _band_ticks(edges, _band_tick_step(edges))
    for value, label in ticks:
        assert abs(float(label) - value) < 1e-9, (value, label)
    # …and the signed axis labels ZERO, which the edge-stride rule never did: the lateral bands'
    # edges are at ±0.1, ±0.3, ±0.5 …, so no boundary of them is a round number or a zero.
    lat = band_edges([-1.7, 1.86], LAT_G_BAND, centre_on_zero=True)
    labels = [lab for _v, lab in _band_ticks(lat, _band_tick_step(lat, 0.5))]
    assert "0" in labels, labels
    assert "+1.0" in labels and "-1.0" in labels, labels
    assert all(lab == "0" or lab[0] in "+-" for lab in labels), labels
    print("ok band ticks: honest labels, and zero named on the signed axis")


def test_stats_view_distribution_charts_render_and_disclose_their_channels():
    _APP  # noqa: B018
    from studio.stats_panel import StatsView

    v = StatsView(_fake_view_session())
    assert not v._bands_section.isHidden()
    assert not v.speed_bands.isHidden() and not v.lat_bands.isHidden()
    for chart in (v.speed_bands, v.lat_bands):
        assert len(chart._bars.opts["height"]) > 0
        assert chart._fast.opts["pen"] is not None and chart._slow.opts["pen"] is not None
        # The outline is an OUTLINE of the histogram: pyqtgraph's centred step mode wants the
        # band edges against the per-band values, n+1 against n.
        assert len(chart._fast.xData) == len(chart._fast.yData) + 1
    # The weighting is in the heading, where a reader takes the numbers off.
    assert "s per lap" in v._bands_section.text()
    note = v.bands_note.text()
    assert "TIME" in note and "sample count" in note                    # the weighting, in words
    assert "10 Hz" in note                                              # the speed channel's rate
    assert "0.15 s" in note and "50 Hz" in note                         # …and the g channel's
    assert "200 Hz" in note, "the sensor rate has to be named to be disowned"
    assert "fastest 3" in note and "slowest 3" in note                  # the sample, named
    assert "1:08.200" in note and "1:10.400" in note                    # …with its lap times
    # The refutation belongs on the surface a reader would otherwise ask best-vs-median of.
    assert "best lap" in v._bands_section.toolTip()
    assert "median lap" in v._bands_section.toolTip()
    print("ok distributions: bars + outlines drawn, weighting/rate/window/sample all stated")


def test_stats_view_distributions_degrade_one_rung_at_a_time():
    _APP  # noqa: B018
    from studio.stats_panel import StatsView

    # 1. No accelerometer: the lateral chart goes, the speed chart stays, and the note stops
    #    describing a filter belonging to a chart that is not on screen.
    no_g = StatsView(_fake_view_session(has_g=False))
    assert not no_g._bands_section.isHidden() and not no_g.speed_bands.isHidden()
    assert no_g.lat_bands.isHidden()
    assert "10 Hz" in no_g.bands_note.text()
    assert "accelerometer" not in no_g.bands_note.text(), no_g.bands_note.text()

    # 2. Too few clean laps for a quartile split: the bars stay, both outlines are CLEARED (a
    #    stale outline over a session that has no comparison is worse than none), and the note
    #    says how many laps a comparison needs.
    sess = _fake_view_session()
    sess.stats.speed_bands = lambda scale=1.0, width=SPEED_BAND: _fake_bands(
        np.arange(40.0, 65.1, 5.0), split=False, laps=5)
    sess.stats.lateral_g_bands = lambda width=LAT_G_BAND: _fake_bands(
        np.arange(-1.1, 1.11, LAT_G_BAND), split=False, laps=5)
    short = StatsView(sess)
    assert not short.speed_bands.isHidden()
    for chart in (short.speed_bands, short.lat_bands):
        assert chart._fast.opts["pen"] is None
        assert chart._fast.xData is None or chart._fast.xData.size == 0
    assert "5 clean laps" in short.bands_note.text()
    assert f"{MIN_SPLIT_LAPS} laps" in short.bands_note.text(), short.bands_note.text()

    # 3. No lap at all: the whole group goes, heading and note with it — nothing here is a
    #    whole-recording total, so there is nothing left to draw.
    empty = StatsView(_fake_view_session(laps=False))
    assert empty._bands_section.isHidden()
    assert empty.speed_bands.isHidden() and empty.lat_bands.isHidden()
    assert not empty.bands_note.isVisible() and empty.bands_note.text() == ""
    print("ok distributions degrade: no g -> one chart, few laps -> no outlines, no laps -> none")


def test_stats_view_distribution_charts_fit_their_column_at_every_pane():
    """Same contract as the friction circle's: a chart that pins itself wider than its column is
    the one thing on this page a horizontal scroll cannot help you read."""
    _APP  # noqa: B018
    from studio.stats_panel import StatsView

    v = StatsView(_fake_view_session())
    for w, h in ((503, 700), (845, 414), (1440, 900), (1900, 1200)):
        v.resize(w, h)
        v.show()
        _APP.processEvents()
        for chart in (v.speed_bands, v.lat_bands):
            assert chart.width() <= v.width(), (w, chart.width(), v.width())
            assert chart.width() > 0
    print("ok distributions: both charts inside the pane from 845x414 up")


def test_the_friction_circle_states_that_its_two_axes_are_not_on_one_window():
    """The g-g cloud is the one surface where the g-meter's TWO smoothing windows meet inside a
    single quantity, so it is the one that owes the reader both.

    `gmeter.LAT_SMOOTH_S` (0.15 s) sets the lateral bandwidth and `LONG_SMOOTH_S` (0.35 s) the
    GPS-derived longitudinal, so hypot(lat, long) — the cloud, its dashed p98 envelope ring, the
    "grip ceiling" tile — has no single window. The tooltip named ONLY the longitudinal one, which
    reads as though the whole picture were on 0.35 s.

    Measured on both D24 pairs (38 and 65 valid laps): the asymmetry is worth ~1 % on the numbers
    (matching both axes at 0.15 s moves the p98 envelope 1.425 -> 1.447 g and 1.368 -> 1.378 g, and
    the CORNERS Grip % column by at most one point) but 9-26 % on the SHAPE — the p98 braking extent
    grows 9-13 % and the acceleration extent 22-26 % while the width does not move at all. A reader
    measuring the circle's aspect ratio is partly measuring the filter chain, so the picture has to
    say so. Both windows are COMPOSED from the constants for the same reason the peak-braking tile's
    is: a window typed into honesty copy rots the moment the signal changes (§5.5)."""
    _APP  # noqa: B018
    import pathlib

    from studio import gmeter
    from studio.stats_panel import GG_TOOLTIP, StatsView
    from studio.stats_panel import __file__ as SP_FILE

    lat_w = f"{gmeter.LAT_SMOOTH_S:g} s"
    long_w = f"{gmeter.LONG_SMOOTH_S:g} s"
    assert lat_w in GG_TOOLTIP, ("the friction circle never states its LATERAL window", GG_TOOLTIP)
    assert long_w in GG_TOOLTIP, GG_TOOLTIP
    assert "not on one window" in GG_TOOLTIP, (
        "naming two windows is not the same as saying they differ", GG_TOOLTIP)
    # ...and it must be the tooltip the reader actually gets, not a constant nothing hangs on.
    view = StatsView(_fake_view_session())
    assert view.gg.toolTip() == GG_TOOLTIP

    # INTERPOLATED, NOT TYPED — on the source, for the reason the peak-braking test gives (a module
    # reload would rebind StatsView under every later test in this process). NEITHER window may
    # appear in this file except through its constant.
    src = pathlib.Path(SP_FILE).read_text(encoding="utf-8")
    for literal, const in ((lat_w, "LAT_SMOOTH_S"), (long_w, "LONG_SMOOTH_S")):
        for line in src.splitlines():
            if literal in line and const not in line:
                raise AssertionError(
                    f"stats_panel types the {const} window as a literal — it must read the "
                    f"constant, or the copy rots the moment the signal changes: {line.strip()!r}")
    print("ok friction circle: both smoothing windows stated, composed from the constants")


# ------------------------------------------------------------------- stints
def test_stint_break_threshold_is_in_the_tracks_own_units():
    """An absolute "120 s" is five missed laps at a 25 s kart circuit and half a missed lap at a
    4-minute one, so the threshold is STINT_GAP_LAPS median laps — floored, because three laps of
    a very short circuit is a spin-and-recover rather than a run change."""
    assert abs(stint_gap_s([69.2] * 9) - STINT_GAP_LAPS * 69.2) < 1e-9   # D24: ~208 s
    assert stint_gap_s([20.0] * 9) == STINT_GAP_MIN_S                    # floored
    assert stint_gap_s([]) == STINT_GAP_MIN_S                            # no laps -> the floor
    assert stint_gap_s([math.nan, 0.0, 70.0, 70.0]) == STINT_GAP_LAPS * 70.0
    print("test_stint_break_threshold_is_in_the_tracks_own_units OK")


def test_split_stints_breaks_only_where_time_went_unanalysed():
    """A break is measured in SECONDS OF RECORDING, not in missing lap numbers — which is the
    one thing a lap-id gap cannot tell you. One dropped lap and six dropped laps are the same
    "gap in the ids" and completely different events."""
    # Adjacent laps: pacer cuts at crossings, so lap k+1 starts where lap k ends — gap 0.0.
    ids, starts, ends = [0, 1, 2], [0.0, 70.0, 140.0], [70.0, 140.0, 210.0]
    assert split_stints(ids, starts, ends, 200.0) == [[0, 1, 2]]
    # One lap dropped (70 s hole) — an id gap, but nowhere near a run change.
    ids, starts, ends = [0, 1, 3], [0.0, 70.0, 210.0], [70.0, 140.0, 280.0]
    assert split_stints(ids, starts, ends, 200.0) == [[0, 1, 3]]
    # Five dropped (350 s) — the pit stop.
    ids, starts, ends = [0, 1, 7], [0.0, 70.0, 490.0], [70.0, 140.0, 560.0]
    assert split_stints(ids, starts, ends, 200.0) == [[0, 1], [7]]
    assert split_stints([], [], [], 200.0) == []
    print("test_split_stints_breaks_only_where_time_went_unanalysed OK")


def test_stint_rows_carry_both_trends_and_suppress_them_when_short():
    """Per run: lap count, best, median, σ and TWO trends — the lap time and the slowest-corner
    speed, the pair that separates grip going off from the driver going off. Every one of them
    keeps the gate its session-wide twin carries."""
    n = TREND_MIN_LAPS + 2
    ids = list(range(n))
    times = [70.0 - 0.1 * k for k in range(n)]                      # improving
    starts = [70.0 * k for k in range(n)]
    ends = [s + 70.0 for s in starts]
    vmins = [48.0 - 0.5 * k for k in range(n)]                      # …and grip going with it
    rows = stint_rows(ids, times, starts, ends, vmins=vmins)
    assert len(rows) == 1 and rows[0].index == 1 and rows[0].n == n
    assert rows[0].lap_ids == ids and rows[0].gap_before_s is None
    assert abs(rows[0].best - min(times)) < 1e-12
    assert rows[0].trend is not None and rows[0].trend < 0            # lap time falling
    assert rows[0].vmin_trend is not None and rows[0].vmin_trend < -VMIN_STEADY_BAND
    assert abs(rows[0].duration_s - (ends[-1] - starts[0])) < 1e-12

    # A two-lap out/in run reports its two times and refuses to describe their "trend".
    short = stint_rows([0, 1], [70.0, 71.0], [0.0, 70.0], [70.0, 140.0], vmins=[48.0, 47.0])
    assert len(short) == 1 and short[0].n == 2
    assert short[0].trend is None and short[0].vmin_trend is None
    assert short[0].sigma is not None                                 # σ's own floor is 2 laps
    assert stint_rows([0], [70.0], [0.0], [70.0])[0].sigma is None

    # No speed channel -> both minimum-speed fields are None, never a 0.
    no_v = stint_rows(ids, times, starts, ends)[0]
    assert no_v.vmin_median is None and no_v.vmin_trend is None

    # Two runs: the second carries the unanalysed span ahead of it, and both are numbered.
    ids2 = [0, 1, 8, 9]
    two = stint_rows(ids2, [70.0] * 4, [0.0, 70.0, 560.0, 630.0], [70.0, 140.0, 630.0, 700.0])
    assert [s.index for s in two] == [1, 2]
    assert two[0].gap_before_s is None and abs(two[1].gap_before_s - 420.0) < 1e-9
    print("test_stint_rows_carry_both_trends_and_suppress_them_when_short OK")


def test_stint_boundaries_are_a_subset_of_the_lap_id_gaps_race_pace_already_breaks_on():
    """THE ONE THING THE STINT VIEW MUST NOT DO: replace the race-pace window.

    §4.2 was fixed by windowing race pace inside runs of consecutive lap IDS, and a stint
    partition looks like the stronger rule. It is the weaker one. Laps tile the trace, so a gap
    in the CLOCK can only appear where laps are missing — every stint boundary is an id gap, and
    an id gap that is merely a dropped lap is not a stint boundary. Windowing on stints would
    therefore let a 3-lap "sustained run" bridge a lap nobody timed.

    Measured on the real recordings, both ways round: on D24 0060/0062 every adjacency between
    analysed laps is 0.000 s, so both partitions are one run and race pace is identical
    (68.6758 / 68.7920 s); with five laps punched out it stays identical again, because the id
    partition had already broken there."""
    from studio.stats import consecutive_runs

    ids = [0, 1, 2, 4, 5, 6, 7, 8, 9, 10]          # lap 3 dropped: one 70 s hole
    starts = [70.0 * i for i in ids]
    ends = [s + 70.0 for s in starts]
    stints = split_stints(ids, starts, ends, stint_gap_s([70.0] * len(ids)))
    runs = consecutive_runs(ids)
    assert stints == [ids], stints                  # one run: 70 s is not a pit stop
    assert runs == [[0, 1, 2], [4, 5, 6, 7, 8, 9, 10]], runs
    # Every stint boundary is also an id boundary — never the other way round.
    stint_edges = {run[0] for run in stints[1:]}
    id_edges = {run[0] for run in runs[1:]}
    assert stint_edges <= id_edges, (stint_edges, id_edges)
    # …and the window that matters refuses to bridge the hole the stint partition ignores.
    times = [70.0, 70.0, 70.0, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0, 60.0]
    assert abs(best_consecutive_mean(times, n=3, ids=ids) - 60.0) < 1e-9
    print("test_stint_boundaries_are_a_subset_of_the_lap_id_gaps_race_pace_already_breaks_on OK")


def test_session_stats_stints_run_over_the_clean_laps_and_clear_on_resegment():
    arrays = {i: (np.array([0.0, 1000.0]), np.array([40.0, 90.0]), np.array([0.0, 70.0]))
              for i in range(12)}
    windows = {i: (70.0 * i, 70.0 * i + 70.0) for i in range(12)}
    st = _service(valid=list(range(12)), cons=[0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11],
                  lap_times={i: 70.0 for i in range(12)}, arrays=arrays, windows=windows)
    assert st.stint_count() == 1
    first = st.stints()
    assert st.stints() is first                        # cached per segmentation
    assert first[0].n == 12 and first[0].vmin_median == 40.0   # the lap arrays' own minimum
    st.invalidate()
    assert st.stints() is not first                    # …and dropped by a re-segment
    assert abs(st.stint_break_s() - STINT_GAP_LAPS * 70.0) < 1e-9

    # A pit stop: laps 5-8 never analysed, so 280 s of the recording sits between two runs.
    holed = _service(valid=list(range(12)), cons=[0, 1, 2, 3, 4, 9, 10, 11],
                     lap_times={i: 70.0 for i in range(12)}, arrays=arrays, windows=windows)
    assert holed.stint_count() == 2
    assert [s.n for s in holed.stints()] == [5, 3]
    assert abs(holed.stints()[1].gap_before_s - 280.0) < 1e-9
    print("test_session_stats_stints_run_over_the_clean_laps_and_clear_on_resegment OK")


# ------------------------------------------------------------------- the split matrix
def test_split_matrix_refuses_to_decorate_a_thin_session():
    rows = [[20.0, 25.0, 24.0]] * MATRIX_MIN_LAPS
    assert split_matrix(list(range(MATRIX_MIN_LAPS - 1)), rows[:-1], columns=3) is None
    assert split_matrix(list(range(MATRIX_MIN_LAPS)), rows, columns=3) is not None
    # One column is the lap time, which this page already prints in four other places.
    assert split_matrix(list(range(MATRIX_MIN_LAPS)), [[69.0]] * MATRIX_MIN_LAPS,
                        columns=1) is None
    print("test_split_matrix_refuses_to_decorate_a_thin_session OK")


def test_split_matrix_columns_bests_and_the_robust_tint_scale():
    ids = [0, 1, 2, 3, 4, 5]
    rows = [[20.0, 25.0], [20.5, 24.5], [21.0, 24.0], [20.2, 26.0], [23.0, 25.5], [20.1, 24.2]]
    m = split_matrix(ids, rows, columns=2)
    assert m.columns == 2 and m.lap_ids == ids
    assert m.bests == [20.0, 24.0]
    assert m.best_lap == [0, 2]                         # the lap that OWNS each column's best
    assert abs(m.medians[0] - 20.35) < 1e-9
    # A p90 of each column's OWN deviations ABOVE ITS MEDIAN, floored — so a lone 3.0 s outlier in
    # column 0 does not set the threshold column 1 is read against, and vice versa.
    assert len(m.scales) == 2 and all(s >= MATRIX_SCALE_MIN_S for s in m.scales)
    assert m.scales[0] < 3.0 and m.scales[1] < 2.0, m.scales
    # A hyper-consistent session cannot drive the scale below the measurement's own resolution —
    # this is what stops a percentile from being a rank (10 % of anything is always the worst
    # 10 %), so a session where nothing is off gets nothing marked.
    flat = split_matrix(ids, [[20.0, 24.0]] * 6, columns=2)
    assert flat.scales == [MATRIX_SCALE_MIN_S, MATRIX_SCALE_MIN_S]

    # THE DEFECT THE MEDIAN ANCHOR EXISTS FOR, as the shape that produced it: a TIGHT column
    # behind a freak best (one lap 1.5 s clear of a column that is otherwise flat), beside a
    # column with a genuinely wide spread. Anchored on the best, every cell of the tight column is
    # the same distance behind, so the column's own p90 IS that distance and the whole column
    # marks. Anchored on the median the freak is one cell below it and moves nothing.
    n = 12
    wide = [17.0 + 0.35 * (k % 6) for k in range(n)]            # spread ~1.8 s
    freak = [16.7] * (n - 1) + [15.2]                            # one lap 1.5 s clear
    poisoned = split_matrix(list(range(n)), [list(p) for p in zip(wide, freak, strict=True)],
                            columns=2)
    over = [sum(1 for r in range(n)
                if poisoned.cells[r][c] - poisoned.medians[c] >= poisoned.scales[c])
            for c in range(2)]
    assert over[1] == 0, ("a freak best must not paint its own flat column as behind",
                          over, poisoned.scales)
    assert over[0] <= max(2, n // 5), ("and the wide column still marks only its tail",
                                       over, poisoned.scales)
    # …which the best anchor could not do: it puts the whole flat column at the same distance.
    by_best = sum(1 for r in range(n)
                  if poisoned.cells[r][1] - poisoned.bests[1] >= 1.5)
    assert by_best == n - 1, by_best

    # A lap whose projection produced a different column count is BLANKED, never dropped: the
    # grid is indexed by lap and a missing row would silently renumber every row under it.
    ragged = split_matrix(ids, rows[:-1] + [[20.0]], columns=2)
    assert ragged.lap_ids == ids and ragged.cells[-1] == [None, None]
    print("test_split_matrix_columns_bests_and_the_robust_tint_scale OK")


def test_split_matrix_marks_never_split_a_tie_the_display_cannot_show():
    """Both defects this rule exists for, as the exact shapes that produced them on D24.

    Interior splits are differences of two GPS sample times, so they sit on a ~0.0998 s grid —
    which is where BOTH marks went wrong. A threshold derived from those values lands on one of
    its own steps (0060, three lines: S2's came out at 17.1000 with cells at 17.099 and 17.100, so
    two cells printing the identical `17.10` came out one marked and one plain), and a column
    minimum is routinely tied at print (0062, five lines: SIX cells read 11.30 in S3, one of them
    0.001 s quicker than the rest)."""
    quantum = 0.0998
    # Seven laps around a median, two of which straddle the threshold by a thousandth.
    col = [16.40, 16.50, 16.70, 16.70, 16.80, 17.099, 17.100]
    m = split_matrix(list(range(len(col))), [[v, 20.0] for v in col], columns=2)
    printed = [round(v, 2) for v in col]
    assert printed[-1] == printed[-2], printed          # they print identically…
    assert m.is_behind(len(col) - 1, 0) == m.is_behind(len(col) - 2, 0), (
        "two cells printing the same value came out one marked and one plain",
        m.medians[0], m.scales[0])

    # …and every cell level with the best at print carries the ★, not just the float minimum.
    tied = [11.30, 11.30, 11.299, 11.30, 11.40, 11.50 + quantum]
    t = split_matrix(list(range(len(tied))), [[v, 20.0] for v in tied], columns=2)
    assert t.bests[0] == 11.299 and t.best_lap[0] == 2   # the minimum, for the tooltip to name
    starred = [r for r in range(len(tied)) if t.is_best(r, 0)]
    assert starred == [0, 1, 2, 3], starred              # every 11.30, not only the 11.299
    print("test_split_matrix_marks_never_split_a_tie_the_display_cannot_show OK")


# ------------------------------------------------------------------- the two new page surfaces
def _splits_fixture(n=8):
    """n laps × 3 sub-sectors. Lap 0 owns S1, lap 1 owns S2 and S3; lap 2 is 1.5 s off in S1 —
    comfortably past the tint scale — and every other lap sits in between."""
    rows = {i: [20.0 + 0.1 * i, 25.0 + 0.1 * i, 24.0 + 0.1 * i] for i in range(n)}
    rows[1] = [20.4, 24.5, 23.5]
    rows[2] = [21.5, 25.4, 24.4]
    return rows


def test_stats_view_runs_tile_and_the_stint_table_that_stays_hidden_on_one_run():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    # ONE run is both of the owner's recordings at every threshold from 15 s to 600 s. The tile
    # carries it; the table would be the PACE tiles above it repeated inside a grid, so it hides.
    assert v.t_runs.value.text() == "1"
    assert "one continuous" in v.t_runs.caption.text()
    assert v._stints_section.isHidden() and v.stints_table.isHidden()
    assert v.stints_note.isHidden()
    # No laps at all -> a dash, never the 0 that reads as "the camera never left the pits".
    lapless = StatsView(_fake_view_session(laps=False))   # held: an inline view is collected
    assert lapless.t_runs.value.text() == "—"             # under it and Qt deletes the label
    print("test_stats_view_runs_tile_and_the_stint_table_that_stays_hidden_on_one_run OK")


def test_stats_view_stint_table_appears_with_two_runs_and_states_its_sample():
    _app()
    from studio.stats_panel import StatsView
    two = [_fake_stint(1, 20, 68.2, 69.3, sigma=0.81, trend=0.024, vmin=26.3, vmin_trend=0.045),
           _fake_stint(2, 13, 68.4, 68.9, sigma=2.22, trend=None, vmin=30.4, vmin_trend=0.31,
                       start=1800.0, gap_before=354.6)]
    v = StatsView(_fake_view_session(stints=two))
    assert v.t_runs.value.text() == "2" and "one continuous" not in v.t_runs.caption.text()
    assert not v._stints_section.isHidden() and v.stints_table.rowCount() == 2
    assert "km/h" in v._stints_section.text()
    assert v.stints_table.item(0, 0).text() == "R1"
    assert v.stints_table.item(0, 1).text() == "20"
    assert v.stints_table.item(0, 2).text() == "1:08.200"
    assert v.stints_table.item(0, 4).text() == "0.81"
    assert v.stints_table.item(0, 5).text() == "+0.02 s/lap"
    assert v.stints_table.item(0, 6).text() == "26.3"
    # Inside the shuffled-label band -> prints flat, unsigned. Past it -> signed.
    assert v.stints_table.item(0, 7).text() == "0.00"
    assert v.stints_table.item(1, 7).text() == "+0.31"
    assert v.stints_table.item(1, 5).text() == "—"          # trend suppressed -> em-dash
    # The break that produced the second row is on the row, and the threshold under the table.
    assert "not analysed" in v.stints_table.item(1, 0).toolTip() or \
           "no lap was analysed" in v.stints_table.item(1, 0).toolTip()
    assert "R1 20 laps" in v.stints_note.text() and "R2 13 laps" in v.stints_note.text()
    assert "3:28" in v.stints_note.text(), v.stints_note.text()   # 207.6 s, the D24 threshold
    # mph flips the speed columns AND the heading, like every other speed on this page.
    v.set_speed_unit("mph")
    assert "mph" in v._stints_section.text()
    assert v.stints_table.item(0, 6).text() == "16.3"
    print("test_stats_view_stint_table_appears_with_two_runs_and_states_its_sample OK")


def test_stats_view_split_matrix_marks_the_best_and_the_behind_cells():
    _app()
    from PySide6.QtGui import QColor

    from studio import theme
    from studio.lap_table import BEST_SECTOR_MARK
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(splits=_splits_fixture()))
    assert not v._splits_section.isHidden() and v.splits_table.rowCount() == 8
    header = [v.splits_table.horizontalHeaderItem(c).text()
              for c in range(v.splits_table.columnCount())]
    assert header == ["Lap", "S1", "S2", "S3", "Lap time"], header
    # Lap numbers are 1-based on screen, app-wide.
    assert v.splits_table.item(0, 1).text().startswith("20.00")
    assert v.splits_table.item(0, 0).text() == "1"
    # THE BEST CELL IN EACH COLUMN carries the ★ and the purple — a tint picking ONE cell out of
    # a column of comparable ones, which is exactly where this app's rule says the mark belongs
    # (the SECTORS table above deliberately has none: its whole column is bests).
    assert v.splits_table.item(0, 1).text().endswith(BEST_SECTOR_MARK)
    assert v.splits_table.item(0, 1).foreground().color() == QColor(theme.best_sector_colour())
    assert v.splits_table.item(1, 2).text().endswith(BEST_SECTOR_MARK)   # lap 1 owns S2
    # …and a cell past its sector's own scale carries the behind hue AND the ▼ that survives
    # greyscale, with BOTH anchors on hover — the typical lap the mark is measured against and
    # the best the ★ is.
    slow = v.splits_table.item(2, 1)
    assert slow.text().startswith(theme.DELTA_BEHIND_ARROW), slow.text()
    assert slow.foreground().color() == QColor(theme.behind_colour())
    assert "+1.05 s against your typical S1 (20.45 s)" in slow.toolTip(), slow.toolTip()
    assert "+1.50 s against the sector best (20.00 s, lap 1)" in slow.toolTip(), slow.toolTip()
    # A middling cell is plain, and still says both numbers — including the laps that are QUICKER
    # than typical and still 0.40 s off the best, which is the pair no single anchor can give.
    mid = v.splits_table.item(1, 1)
    assert "-0.05 s against your typical S1" in mid.toolTip(), mid.toolTip()
    assert "+0.40 s against the sector best" in mid.toolTip(), mid.toolTip()
    # The note states the SAMPLE and the two things a reader cannot see: what the ▼ is measured
    # against, and the 10 Hz floor the interior columns are quantized to.
    assert "8 clean laps × 3 sectors" in v.splits_note.text()
    assert "typical lap" in v.splits_note.text()
    assert "10 Hz" in v.splits_note.text()
    print("test_stats_view_split_matrix_marks_the_best_and_the_behind_cells OK")


def test_stats_view_split_matrix_hides_under_the_decorative_floor():
    """"A heat grid over three laps is decoration" — the page must hold that line, not just the
    reducer. The SECTORS summary above it still stands: three laps have a best and a median."""
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(splits=_splits_fixture(MATRIX_MIN_LAPS - 1)))
    assert v._splits_section.isHidden() and v.splits_table.isHidden()
    assert v.splits_note.isHidden()
    assert not v._sector_section.isHidden()
    print("test_stats_view_split_matrix_hides_under_the_decorative_floor OK")


def test_every_longitudinal_surface_names_which_filter_it_read():
    """#271 found a THIRD longitudinal series and disclosed it at source; this is the half that
    reaches a user.

    THE CONTRADICTION, re-measured here rather than inherited. On the D24 0060 pair (38 valid
    laps) a brake event's own peak deceleration EXCEEDS the "peak braking g" printed on the same
    lap row on 37 of 38 laps, median ratio 1.258 (0.862 -> 1.081 g); on 0062 (65 laps) it is 65 of
    65 at 1.244 (0.652 -> 0.811 g). The session maxima the tile prints are 1.266 g and 1.076 g
    against largest event peaks of 1.943 g and 1.535 g. Both numbers are right for what they are —
    a window can only lower a peak — and a page printing both while naming neither is the same
    self-contradiction #237 fixed by splitting the priority glyph from the trust glyph.

    So: the four DRIVING tiles (which shipped with NO tooltip at all, the only untooltipped tiles
    on the page and the only ones built from the DETECTION series), their section heading, the
    PER LAP grid where the two filters sit in adjacent columns, the peak-braking tile and the
    BRAKING table's commit % each say which series they read.

    AND THE INSTRUMENT IS COMPOSED, NEVER TYPED. The coast band, the minimum duration, the coast
    WINDOW and the commit denominator's percentile come from `driving`'s own constants, so the
    copy follows the detector rather than describing a past version of it — the §5.5 lesson. The
    text still characterises no coast MAGNITUDE, which is a different guarantee from naming the
    instrument and the only one that survived #275: that PR gave the coast its own window one
    merge after this copy was written, and the "no smoothing window" claim went stale on a green
    suite. `test_the_coast_copy_states_the_window_the_coast_was_measured_on` is the guard."""
    _app()
    import pathlib

    from studio import driving, gmeter
    from studio.stats_panel import BRAKING_TOOLTIP, DRIVING_TOOLTIP, LAP_TABLE_TOOLTIP, StatsView
    from studio.stats_panel import __file__ as SP_FILE

    v = StatsView(_fake_view_session())
    # The peak-braking tile points AT the other channel rather than only describing its own.
    tip = v.t_peak_brake.toolTip()
    assert "no window at all" in tip and "ABOVE this figure" in tip, tip
    # The four event tiles + the heading carry the disclosure. `grip envelope · p98` keeps its own
    # (it is the combined-g percentile, not an event count).
    for tile in (v.t_brake, v.t_brake_n, v.t_coast, v.t_longest_coast):
        assert tile.toolTip() == DRIVING_TOOLTIP, tile.caption.text()
    assert v._driving_section.toolTip() == DRIVING_TOOLTIP
    grip_tip = v.t_grip_ceiling.toolTip()
    assert "98th percentile" in grip_tip and grip_tip != DRIVING_TOOLTIP, grip_tip
    # The PER LAP grid is the one surface that prints both filters in adjacent columns.
    assert "TWO COLUMNS HERE READ ONE AXIS THROUGH TWO FILTERS" in LAP_TABLE_TOOLTIP
    assert f"{gmeter.LONG_SMOOTH_S:g} s" in LAP_TABLE_TOOLTIP
    assert "no window at all" in LAP_TABLE_TOOLTIP
    # Commit % is a ratio inside ONE channel, and says so against the tile it could be read as.
    assert f"{driving.AMAX_PCT:g}th percentile" in BRAKING_TOOLTIP
    assert "peak braking g" in BRAKING_TOOLTIP
    # The coast instrument: the band and the minimum duration, from the constants.
    assert f"{driving.COAST_DRAG_MIN:g} g" in DRIVING_TOOLTIP
    assert f"{driving.MIN_COAST_S:g} s" in DRIVING_TOOLTIP
    # ...and NOT typed. Checked on the source (a reload would rebind StatsView for every later
    # test in this process), the way the LONG_SMOOTH_S check above it already is.
    src = pathlib.Path(SP_FILE).read_text(encoding="utf-8")
    for literal, const in ((f"{driving.COAST_DRAG_MIN:g} g", "COAST_DRAG_MIN"),
                           (f"{driving.MIN_COAST_S:g} s", "MIN_COAST_S"),
                           (f"{driving.AMAX_PCT:g}th percentile", "AMAX_PCT")):
        for line in src.splitlines():
            if literal in line and const not in line:
                raise AssertionError(
                    f"stats_panel types {literal!r} as a literal — it must read driving.{const}, "
                    f"or the copy rots the moment the detector changes: {line.strip()!r}")
    v.hide()
    print("ok longitudinal disclosure: peak tile, DRIVING tiles, PER LAP and commit % each name "
          "their series; the coast band + duration are read from driving's constants")


def test_the_coast_copy_states_the_window_the_coast_was_measured_on():
    """A coast is no longer detected on the bare derivative, and the copy beside it has to say so.

    #275 gave the coast band its own pre-threshold boxcar (`driving.COAST_SMOOTH_S`) because the
    band is narrower than the raw 10 Hz derivative's own noise — the reported coasting was 5.9 %
    and 4.5 % of the real band time on the two reference recordings, and the fix moved the Stats
    page's `coasting / lap · median` tile from 0.4 s to 2.6 s on D24 0060.

    It landed ONE PR AFTER the tooltip that explains that tile (#276), which had been written on
    the then-true claim that brake and coast both run on the derivative "with NO smoothing
    window". That sentence is now false for the coast, and it is false in the direction that
    matters: the window it denies (0.50 s) is WIDER than the 0.35 s one the same sentence
    contrasts itself against. Measured on ~/Desktop/D24 GX020060+GX030060 — the tile reads
    "2.6 s", its tooltip says the number carries no window, and the exported DRIVING note beside
    it (`driving.coast_instrument`, which #275 did update) says it carries a 0.50 s one. Two
    sentences about one number, in one app.

    So both surfaces that print a coast number state the coast's own window, COMPOSED from the
    constant like the band and the minimum duration already are, and no sentence that mentions a
    coast may claim it is unwindowed."""
    _app()
    import pathlib
    import re

    from studio import driving
    from studio.stats_panel import DRIVING_TOOLTIP, LAP_TABLE_TOOLTIP
    from studio.stats_panel import __file__ as SP_FILE

    window = f"{driving.COAST_SMOOTH_S:g} s"
    surfaces = (("DRIVING_TOOLTIP", DRIVING_TOOLTIP), ("LAP_TABLE_TOOLTIP", LAP_TABLE_TOOLTIP))
    for name, tip in surfaces:
        assert window in tip, (
            f"{name} prints a coasting number but never says it was measured over {window} "
            f"(driving.COAST_SMOOTH_S) — the instrument the exported DRIVING note already states")
        # ...and no sentence that names a coast may also deny it a window. Split on a period
        # FOLLOWED BY SPACE so the constants ("0.5 s", "0.03 g") stay intact.
        for sentence in re.split(r"(?<=\.)\s+", tip):
            if "coast" not in sentence.lower():
                continue
            for denial in ("no window", "no smoothing window"):
                assert denial not in sentence.lower(), (
                    f"{name} tells the reader a coast carries {denial!r}, but "
                    f"driving.coasting_spans boxcars over COAST_SMOOTH_S={window} before the "
                    f"band test: {sentence.strip()!r}")

    # The window is READ from the constant, never typed — the same rot guard the band and the
    # minimum duration already carry, checked on the source for the same reason.
    src = pathlib.Path(SP_FILE).read_text(encoding="utf-8")
    for line in src.splitlines():
        if window in line and "COAST_SMOOTH_S" not in line and "LONG_SMOOTH_S" not in line:
            raise AssertionError(
                f"stats_panel types {window!r} as a literal — it must read "
                f"driving.COAST_SMOOTH_S, or the copy rots the moment the window moves: "
                f"{line.strip()!r}")
    print(f"ok coast disclosure: both surfaces state the {window} coast window, composed from "
          f"driving.COAST_SMOOTH_S, and neither denies it")


if __name__ == "__main__":
    # AT THE FOOT OF THE FILE, and that is a fix rather than a move. This block used to sit ~120
    # lines above the end, so the three "Phase 4: the page fits its pane" tests written after it
    # were DEFINED and never called: `python tests/test_stats.py` printed "ALL 62 STATS TESTS
    # PASSED" and exited before reaching them, and ctest runs exactly that command. They had never
    # run once. Anything appended from here on runs by construction.
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} STATS TESTS PASSED")
