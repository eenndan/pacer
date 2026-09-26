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
    COAST_LEAD_ALPHA,
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
    coast_report,
    coast_seconds_by_piece,
    corner_matrix,
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


def test_corner_report_counts_only_the_cells_matched_on_track():
    """C4: a lap's corner whose window was interpolated (not matched on track at both edges) counts
    towards nothing in that corner's row. The planted cell is the one a minimum SELECTS: the
    quickest time, the highest apex speed and a grip reading, all on the interpolated lap — the
    shape measured on D24 0060, where the CORNERS Best sat on such a cell in C2, C6 and C8."""
    rows_t = [[3.20, 5.0], [2.90, 5.4], [3.40, 5.8], [3.30, 5.2]]
    rows_a = [[58.0, 44.0], [66.0, 42.0], [56.0, 40.0], [57.0, 41.0]]
    rows_g = [[0.70, 0.8], [1.10, 0.7], [0.72, 0.6], [math.nan] * 2]   # lap 3: no g signal
    resolved = [[True, False], [False, False], [True, False], [True, False]]
    c1, c2 = corner_report([1, 2], [1, -1], rows_t, rows_a, rows_g, resolved)
    counted = [3.20, 3.40, 3.30]
    assert (c1.n, c1.n_laps) == (3, 4), (c1.n, c1.n_laps)
    assert c1.best_s == 3.20, ("an interpolated cell set the Best", c1.best_s)
    assert c1.median_s == 3.30, c1.median_s
    assert abs(c1.sigma_s - float(np.std(counted, ddof=1))) < 1e-12
    assert abs(c1.median_loss_s - 0.10) < 1e-9
    assert c1.apex_best_kmh == 58.0 and c1.apex_median_kmh == 57.0, (c1.apex_best_kmh,
                                                                     c1.apex_median_kmh)
    assert abs(c1.grip_median - 0.71) < 1e-12, c1.grip_median
    # A corner NO lap matched keeps its row, says how many laps it had, and publishes nothing.
    assert (c2.cid, c2.n, c2.n_laps) == (2, 0, 4), c2
    assert (c2.best_s, c2.median_s, c2.sigma_s, c2.median_loss_s, c2.apex_best_kmh,
            c2.apex_median_kmh, c2.grip_median) == (None,) * 7, c2
    assert c2.score == 0.0, "an untimed corner must never be ranked among the worst"
    # No mask (a pure caller with no warp) counts every cell, exactly as before.
    a1, _ = corner_report([1, 2], [1, -1], rows_t, rows_a, rows_g)
    assert (a1.n, a1.best_s, a1.apex_best_kmh) == (4, 2.90, 66.0), a1
    print("test_corner_report_counts_only_the_cells_matched_on_track OK")


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


def test_straights_report_counts_each_value_by_the_edges_it_reads():
    """C4, one level finer than a corner: a straight's TIME needs both its ends matched on track,
    its TRAP speed only its end (the next corner's entry), its EXIT Δ only the preceding corner's
    exit — on the best lap too. The timing line is always matched. Each planted interpolated edge
    carries the value a best/median would pick, so reading it would move the column."""
    times = [[5.0, 8.0, 4.0], [4.0, 7.0, 3.0], [5.1, 8.3, 4.05], [5.2, 8.6, 4.1]]
    traps = [[70.0, 90.0, 60.0], [99.0, 99.0, 60.5], [71.0, 89.0, 61.0], [69.0, 88.0, 59.0]]
    exits = [[50.0, 40.0], [60.0, 40.2], [49.0, 40.5], [48.0, 41.0]]
    best_exits = [51.0, 40.0]
    #          C1 enter, C1 exit, C2 enter, C2 exit
    edges = [[True, True, True, True],
             [False, False, False, True],     # lap 1: C1 both edges and C2's entry interpolated
             [True, True, True, True],
             [True, True, True, True]]
    s0, s1, s2 = straights_report([1, 2], times, traps, exits, best_exits, edges,
                                  [True, True, True, True])
    # S/F → C1 ends at C1's entry: lap 1's time AND trap are out; the start line is matched.
    assert (s0.n, s0.n_trap, s0.n_laps, s0.n_exit) == (3, 3, 4, None), s0
    assert s0.best_s == 5.0 and s0.trap_best_kmh == 71.0, s0
    # C1 → C2 runs C1 exit -> C2 entry (both out on lap 1); its exit Δ reads C1's exit (out).
    assert (s1.n, s1.n_trap, s1.n_exit) == (3, 3, 3), s1
    assert s1.best_s == 8.0 and s1.trap_best_kmh == 90.0, s1
    assert abs(s1.exit_delta_kmh - (49.0 - 51.0)) < 1e-12, s1.exit_delta_kmh
    # C2 → S/F runs C2 exit (matched on lap 1) -> the finish line: lap 1 counts in full here.
    assert (s2.n, s2.n_trap, s2.n_exit) == (4, 4, 4), s2
    assert s2.best_s == 3.0, s2
    # The best lap's own exit unmatched -> no Δ can be taken against it.
    _, b1, _ = straights_report([1, 2], times, traps, exits, best_exits, edges,
                                [True, False, True, True])
    assert b1.exit_delta_kmh is None and b1.leverage == 0.0, b1
    # No edges (a pure caller) -> every value counts, and nothing claims to have counted.
    a0, _, _ = straights_report([1, 2], times, traps, exits, best_exits)
    assert (a0.n, a0.best_s, a0.n_laps, a0.n_trap) == (4, 4.0, None, None), a0
    print("test_straights_report_counts_each_value_by_the_edges_it_reads OK")
# ------------------------------------------------------------------- coasting, by place (F5)
def _span(a, b, dur=0.0):
    return SimpleNamespace(start_dist=float(a), end_dist=float(b), duration=float(dur))


def test_coast_seconds_by_piece_splits_a_span_at_the_edges_on_the_laps_own_clock():
    """A coast that runs out of a corner into the next straight is SPLIT at the corner's edge, and
    each side is read off the lap's own clock — not apportioned by distance, and never counted in
    full on both sides (coaching's per-corner window counts an overlapping span whole, which is
    right for its question and wrong for a table whose rows must add up)."""
    dist = np.arange(0.0, 101.0)                            # 1 m samples
    # The lap slows through 40-50 m: 0.2 s per metre there, 0.1 s per metre elsewhere.
    dt = np.where((dist[1:] > 40.0) & (dist[1:] <= 50.0), 0.2, 0.1)
    elapsed = np.concatenate([[0.0], np.cumsum(dt)])
    spans = [_span(40.0, 60.0, float(elapsed[60] - elapsed[40])), _span(90.0, 95.0, 0.5)]
    got = coast_seconds_by_piece(spans, [0.0, 50.0, 100.0], dist, elapsed)
    assert np.allclose(got, [2.0, 1.0 + 0.5]), got          # 10 m slow (2.0 s) | 10 m + 5 m
    assert abs(got.sum() - sum(sp.duration for sp in spans)) < 1e-12
    # A span entirely outside every piece contributes nothing; no piece, no seconds.
    assert np.allclose(coast_seconds_by_piece([_span(-5.0, -1.0)], [0.0, 50.0, 100.0],
                                              dist, elapsed), [0.0, 0.0])
    assert coast_seconds_by_piece(spans, [0.0], dist, elapsed).size == 0
    print("test_coast_seconds_by_piece_splits_a_span_at_the_edges_on_the_laps_own_clock OK")


def test_coast_report_names_ranks_and_rings_like_the_straights_table():
    """Pieces run S/F → C1, C1, C1 → C2, C2, C2 → S/F; a straight is named and ringed exactly as the
    STRAIGHTS table names it (one helper), a place nobody coasted in is left out, and the share
    and laps columns are arithmetic on the rows."""
    rows = [[0.0, 1.0, 0.2, 0.0, 0.0],
            [0.0, 1.2, 0.0, 0.0, 0.0],
            [0.0, 0.8, 0.4, 0.0, 0.1]]
    rep = coast_report([1, 2], rows)
    assert rep.n_laps == 3 and abs(rep.per_lap_s - 3.7 / 3) < 1e-12
    assert [p.label for p in rep.places] == ["C1", "C1 → C2", "C2 → S/F"]   # C2, S/F → C1 omitted
    assert [p.ring_cid for p in rep.places] == [1, 1, 2]
    assert [p.index for p in rep.places] == [1, 2, 4]
    assert [p.laps for p in rep.places] == [3, 2, 1]
    assert abs(rep.places[0].s_per_lap - 1.0) < 1e-12
    assert abs(sum(p.share for p in rep.places) - 1.0) < 1e-12
    labels = [st.label for st in straights_report([1, 2], [[1, 1, 1]], [[1, 1, 1]], [[1, 1]],
                                                  [1, 1])]
    assert rep.places[1].label == labels[1] and rep.places[2].label == labels[2]
    # No lap at all -> no report; laps with no coasting anywhere -> a report with no places.
    assert coast_report([1, 2], []) is None
    empty = coast_report([1, 2], [[0.0] * 5] * 4)
    assert empty.places == [] and empty.lead_separable is False and empty.per_lap_s == 0.0
    print("test_coast_report_names_ranks_and_rings_like_the_straights_table OK")


def test_coast_report_does_not_crown_a_leader_the_laps_cannot_separate():
    """THE CLAIM THIS TABLE MAKES. A place leads only if the laps separate it from every other
    place; otherwise it is TIED with each place they cannot separate it from.

    Shaped on the owner's recordings: D24, where the busiest corners each coast on about half the
    laps and sit within a few hundredths of a second a lap of each other (the leader tied with 10
    places on 0060 and 7 on 0062), against Sandown, where C1 coasts on nearly every lap at roughly
    twice the runner-up."""
    n = 40
    lap = np.arange(n)
    # D24-shaped: two corners, each a 0.5 s coast on about half the laps — C1 on the 20 even laps,
    # C2 on 19 of the odd ones. C1 leads by 0.0125 s a lap, which no lap-to-lap order supports.
    a = np.where(lap % 2 == 0, 0.5, 0.0)
    b = np.where((lap % 2 == 1) & (lap != 39), 0.5, 0.0)
    d24 = coast_report([1, 2], np.column_stack([np.zeros(n), a, np.zeros(n), b, np.zeros(n)]))
    assert d24.places[0].label == "C1" and d24.places[0].s_per_lap > d24.places[1].s_per_lap
    assert not d24.lead_separable, "a leader the laps cannot separate was crowned"
    assert all(p.tied for p in d24.places)
    # Sandown-shaped: ~1.0 s on every lap against 0.6 s on 32 laps of 40.
    c1 = 1.0 + 0.25 * np.sin(lap)
    c4 = np.where(lap % 5 == 0, 0.0, 0.6 + 0.2 * np.cos(lap))
    sandown = coast_report([1, 4], np.column_stack([np.zeros(n), c1, np.zeros(n), c4,
                                                    np.zeros(n)]))
    assert sandown.lead_separable and sandown.places[0].label == "C1"
    assert [p.tied for p in sandown.places] == [True, False]
    assert 0.0 < COAST_LEAD_ALPHA < 0.5
    print("test_coast_report_does_not_crown_a_leader_the_laps_cannot_separate OK")


def test_session_coast_report_cuts_the_straights_tables_pieces_and_keeps_every_coast_second():
    """The real `Session` path, on the drift + noise fixture (the one where a lap drifts past its
    normalized projection, so the warp matters, and GPS noise reaches the coast detector).

    1. EVERY COAST SECOND LANDS IN EXACTLY ONE PLACE: each clean lap's row sums to that lap's
       `lap_coasting_spans`, so the table's s / lap column adds up to the mean of the Coast s the
       PER LAP grid prints for the same laps — two surfaces, one quantity, one total.
    2. THE PIECES ARE THE STRAIGHTS TABLE'S PIECES: a single span covering a whole lap splits into
       exactly the corner/straight times `corners.segment_times` gives that lap through the same
       memoized warp — not a second projection that agrees most of the time.
    3. No g signal, no instrument: None, not a report of zero coasting."""
    from _synthetic import drift_noise_session

    from studio import corners as corners_alg

    s = drift_noise_session()
    ids = s.consistency_lap_ids()
    rep = s.coast_report()
    cids, rows = s._coast_rows()
    assert rep is not None and rep.n_laps == len(ids) == len(rows) and rep.places
    for i, row in zip(ids, rows, strict=True):
        spans = s.driving.lap_coasting_spans(i)
        assert spans, f"the fixture's lap {i} has no coast to split — this check would be vacuous"
        assert abs(float(np.sum(row)) - sum(sp.duration for sp in spans)) < 1e-9, (i, row)
    coast_col = {r.idx: r.coast_s for r in s.stats.lap_stats()}
    assert abs(rep.per_lap_s - float(np.mean([coast_col[i] for i in ids]))) < 1e-9

    basis = s.corners.basis()
    corner_list = s.corners.corner_list()
    drifted = False
    for i in ids:
        dist, _v, elapsed = s._lap_arrays(i)
        whole = [SimpleNamespace(start_dist=float(dist[0]), end_dist=float(dist[-1]),
                                 duration=float(elapsed[-1] - elapsed[0]))]
        s.driving._coasting_spans_cache[i] = whole
        align = s.corners.lap_alignment(i, float(dist[-1]))
        drifted |= align is not None and not np.allclose(
            corners_alg.project_boundaries([c.enter for c in corner_list], float(basis[1]),
                                           float(dist[-1]), alignment=align),
            corners_alg.project_boundaries([c.enter for c in corner_list], float(basis[1]),
                                           float(dist[-1]), alignment=None), atol=0.5)
        expect = corners_alg.segment_times(corner_list, float(basis[1]), dist, elapsed, None,
                                           align)
        _cids, got = s._coast_rows()
        assert np.allclose(got[ids.index(i)], expect, atol=1e-9), (i, got[ids.index(i)], expect)
    assert drifted, "no lap's warp differs from the normalized projection — (2) would pass on either"

    s.driving._thresholds_cache = None
    assert s.coast_report() is None
    print("test_session_coast_report_cuts_the_straights_tables_pieces_and_keeps_every_coast_second OK")


def test_a_tie_is_decided_place_by_place_not_by_rank():
    """"Tied" is a question about ONE place against the leader, so the tied rows need not be the
    top of the order: a tie turns on the place's lap-to-lap spread, not on its rank. On D24 0060
    the rare-but-long coast on C9 → C10 (3 of 38 laps, 0.099 s a lap) is tied while C1 → C2 (5 of
    38, 0.097 s) is not. Here a place coasting on 4 laps of 40 stays tied BELOW a steadier place
    that separates; a rule that cut the order at the first separated row would untie it — which is
    why the vs top column is read per row, not as a cut."""
    n = 40
    lead = np.full(n, 0.6)                          # 0.6 s on every lap
    steady = np.full(n, 0.5)                        # 0.5 s on every lap: separates, 2nd by rank
    rare = np.zeros(n)
    rare[:4] = 4.5                                  # 0.45 s a lap, all of it on 4 laps: 3rd
    rep = coast_report([1, 2], np.column_stack([np.zeros(n), lead, rare, steady, np.zeros(n)]))
    assert [p.label for p in rep.places] == ["C1", "C2", "C1 → C2"]
    assert [p.tied for p in rep.places] == [True, False, True], [(p.label, p.tied)
                                                                 for p in rep.places]
    assert not rep.lead_separable
    print("test_a_tie_is_decided_place_by_place_not_by_rank OK")


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
             events=None, spans=None, brake_time=None):
    """A SessionStats over plain fake callables — the same DI seam Session wires."""
    gm = gm if gm is not None else _fake_gmeter([], [], [])
    lap_times = lap_times or {}
    arrays = arrays or {}
    windows = windows or {}
    events = events or {}
    spans = spans or {}
    brake_time = brake_time or {}
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
        brake_time=lambda i: brake_time.get(i, 0.0),
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
        # The channel's time ON THE BRAKES (driving.brake_time), deliberately NOT the events'
        # summed span (2.0 s on lap 0): an event is held open through its lift-off tail (LOOK-2).
        brake_time={0: 1.25, 1: 0.8},
        spans={0: [SimpleNamespace(duration=3.5)], 1: []},
    )
    rows = st.lap_stats()
    assert [r.idx for r in rows] == [0, 1]
    r0, r1 = rows
    assert r0.time == 70.0 and r0.vmax_kmh == 95.0
    assert abs(r0.avg_kmh - 1000.0 / 70.0 * 3.6) < 1e-9   # odometer / lap time
    assert abs(r0.peak_lat_g - 1.4) < 1e-12               # window [100,170) -> samples 0+1
    assert abs(r0.peak_brake_g - 1.1) < 1e-12             # from long_g_gps, NOT the junk IMU long
    assert r0.brake_s == 1.25 and r0.brake_n == 2         # brake time, not Σ event.duration
    assert r1.brake_s == 0.8 and r1.brake_n == 1
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
        brake_time=lambda i: 0.0,
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
        # (typical, laps, lowest, its lap): the median of 48.0 and 50.0, and lap 0's 48.0.
        slowest_corner=lambda: (49.0, 2, 48.0, 0),
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
                        admitted=np.ones(times.shape, bool),
                        resolved=np.ones(times.shape, bool), bests=bests, donors=donors,
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
    assert "49.0" in v.t_vmin.value.text()                   # the typical slowest corner
    assert v.t_peak_lat.value.text() == "1.60 g"             # max over the laps
    # v1.1 pace-quality tiles
    assert v.t_race_pace.value.text() == "1:08.900"
    assert v.t_trend.value.text() == "\u22120.05 s/lap"     # the true minus (LOOK-7)
    assert "improving" in v.t_trend.caption.text()
    # R11: PACE is six tiles. The four cut (median − best, σ/median, within 1 %, the coaching
    # digest) and why are written where the grid is built; the report export keeps its own rows.
    for gone in ("t_spread", "t_cov", "t_within", "t_digest"):
        assert not hasattr(v, gone), gone
    assert len(v._tile_grids[1][1]) == 6, [t.caption.text() for t in v._tile_grids[1][1]]
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
    assert not v.ideal.heading.isHidden() and not v.ideal.t_theoretical.isHidden()
    assert v.ideal.t_theoretical.value.text() == "0:20.900"
    assert v.ideal.t_gap.value.text() == "0.51 s"           # unsigned: time to find (LOOK-7)
    # Verified + high-quality timing: rendered as normal tiles, never the provisional muting.
    assert not v.t_rolling.value.font().italic()
    assert not v.ideal.t_theoretical.value.font().italic()
    assert not v.ideal.t_gap.value.font().italic()
    assert "not a lap you drove" in v.ideal.t_theoretical.toolTip()
    assert "not a lap you drove" in v.t_rolling.toolTip()
    # …and the retired copy is GONE. Every clause of it was false once the ideal stopped being a
    # sum of sector splits, and the tile is no longer gated on sector lines at all.
    for dead in ("best sector", "sector splits", "Shown only with sector lines"):
        assert dead not in v.ideal.t_theoretical.toolTip(), dead
    assert "agree" in v.trust.card.text()                   # the cross-check's first UI surface
    assert "GPS9 true clock" in v.trust.card.text()
    print("test_stats_view_renders_every_group OK")


def test_stats_view_hides_signal_absent_sections():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session(has_g=False, sectors=False))
    assert v._driving_section.isHidden() and v.gg.isHidden()     # no g -> no g sections
    # NO SECTOR LINES, NO SECTION (R11). The heading used to stay with an "Add sector" line,
    # because hiding it had made the capability invisible; but both of the owner's saved tracks
    # hold `sectors: []`, so it was a heading about a feature he does not use on every recording
    # he opens. The way in stays the map's own "Add sector" button.
    assert v.sector_table.isHidden() and v.splits_table.isHidden()
    assert v._splits_section.isHidden() and v._sector_section.isHidden()
    assert not hasattr(v, "sectors_empty")
    # THE THEORETICAL BEST NO LONGER HIDES WITH SECTORS, and this assertion is the gate fix.
    # It used to inherit this section's 0-sector hide, which was right while it was a sum of best
    # SECTOR splits (one sector = one lap = the best lap time). It is a corner/straight composite
    # now — sector lines do not touch it — and `sector_count()` is 0 on EVERY recording the owner
    # has, so the old gate hid the corrected number on all five of them. Same fake, sectors off,
    # ideal intact: the block stays.
    assert not v.ideal.t_theoretical.isHidden() and not v.ideal.heading.isHidden()
    assert v.ideal.table.rowCount() > 0
    assert not v.t_rolling.isHidden() and v.t_rolling.value.text() == "1:08.150"
    assert v.t_peak_lat.value.text() == "—"                      # None, never a fake 0
    assert v.lap_table.item(0, 5).text() == "—"                  # per-lap g cells dash too
    assert v.lap_table.item(0, 2).text() == "95.0"               # speed needs no g signal
    assert v.lap_table.item(0, 4).text() == "48.0"               # Min speed needs no g either
    print("test_stats_view_hides_signal_absent_sections OK")


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
    for term, value in v.trust.card._widgets:
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

    targets = lambda v: (v.ideal.t_theoretical, v.t_rolling)  # noqa: E731 — a local alias, not a def

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
    t = v.corners.table
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
    # R11: ONE tile — the shares on its face in track order, the seconds on its hover.
    assert not hasattr(v.corners, "t_phase_entry") and not hasattr(v.corners, "t_phase_exit")
    assert not v.corners.t_phase.isHidden()
    assert v.corners.t_phase.value.text() == "61 · 24 · 15 %", v.corners.t_phase.value.text()
    assert "entry · apex · exit" in v.corners.t_phase.caption.text()
    assert "Lost on entry 6.1 s" in v.corners.t_phase.toolTip() and "on exit 1.5 s" in v.corners.t_phase.toolTip()
    tip = v.corners.table.item(0, 4).toolTip()
    assert "entry +0.61" in tip and "exit +0.15" in tip
    # No phase data -> the tile hides, the table stands alone.
    sess.phase_report = lambda: None
    v.refresh()
    assert v.corners.t_phase.isHidden()
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
    t = v.braking.table
    assert not t.isHidden() and t.rowCount() == 2       # the unbraked corner (n=0) is omitted
    assert t.item(0, 0).text() == "C1" and t.item(0, 2).text() == "2.1"
    assert t.item(0, 4).text() == "88" and t.item(0, 5).text() == "3.5"
    assert t.item(1, 2).text() == "—"                   # single-lap σ: dash, never 0
    assert t.item(1, 5).text() == "\u22121.2"
    fired = []
    v.corner_clicked.connect(fired.append)
    t.selectRow(1)
    assert fired and fired[-1] == 3                     # the SAME signal the CORNERS table uses
    # No braking data at all -> section hidden.
    sess.brake_report = lambda: []
    v.refresh()
    assert v.braking.heading.isHidden() and v.braking.table.isHidden()
    print("test_stats_view_braking_table_filters_unbraked_and_emits_clicks OK")


def test_the_braking_bound_is_labelled_a_models_bound_not_room_to_gain():
    """L8. BRAKING's last column is the D4 model's BOUND: per lap, how far past the onset a stop
    held at the session's demonstrated PEAK deceleration would start, medianized. It read as room to
    gain — headed "m later", printed "+17.5", hovered "the ESTIMATED median metres you could brake
    later" — yet a kart never holds its peak, so the bound sits past the driver's braking by
    construction (positive at 33 of 33 working-set corners, refused-2026-09.md §16). Pinned on the
    real page: the header is the shared (est) label and never says "later"; a positive cell has no
    "+"; the hover says it is a model's bound, not room to gain; and it names the model's
    deceleration as the SAME demonstrated maximum Commit % divides by (one quantity, one source)."""
    _app()
    from studio import theme
    from studio.stats import BrakeConsistency
    from studio.stats_braking import BOUND_COLUMN, BRAKE_COLUMNS, BRAKING_TOOLTIP
    from studio.stats_panel import StatsView

    assert BRAKE_COLUMNS[-1] == BOUND_COLUMN == theme.estimated_label("Bound m"), BRAKE_COLUMNS
    assert not any("later" in h.lower() for h in BRAKE_COLUMNS), BRAKE_COLUMNS
    assert "brake later" not in BRAKING_TOOLTIP.lower(), BRAKING_TOOLTIP
    assert "NOT ROOM TO GAIN" in BRAKING_TOOLTIP and f"{BOUND_COLUMN} is, per lap," in BRAKING_TOOLTIP
    assert "demonstrated maximum deceleration — the one Commit % divides by" in BRAKING_TOOLTIP
    sess = _fake_view_session()
    sess.brake_report = lambda: [
        BrakeConsistency(cid=4, n=12, median_dist_m=250.0, sigma_m=3.0, span_m=9.0,
                         commit_pct=81.0, metres_later_med=17.5)]
    v = StatsView(sess)
    t = v.braking.table
    heads = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert heads == BRAKE_COLUMNS, heads
    assert t.item(0, 5).text() == "17.5", "a bound is a distance: no '+' to read as metres to gain"
    assert t.toolTip() == BRAKING_TOOLTIP
    v.hide()
    print(f"test_the_braking_bound_is_labelled_a_models_bound_not_room_to_gain OK ({heads[-1]!r})")


def test_stats_view_straights_table_and_exit_leverage_note():
    """The STRAIGHTS table, and the one line under it naming its top exit-leverage straight.

    PS-2: that line was a "fix first" TILE and, measured on the real window, it named a different
    corner from the Coaching tab's #1 on 3 of the 4 working-set recordings (SD_19_09 C2 vs C1). It
    now says what it measures and, where Coaching starts elsewhere, where and why — never a second
    instruction."""
    _app()
    from dataclasses import replace

    from PySide6.QtWidgets import QLabel

    from studio.stats import StraightStat
    from studio.stats_common import RING_ROLE
    from studio.stats_panel import StatsView
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
    t = v.straights.table
    assert not t.isHidden() and t.rowCount() == 2            # the ~0s stub is omitted (B8)
    assert t.item(0, 0).text() == "S/F → C1"
    assert t.item(0, 6).text() == "—"                      # k=0 exit delta: no double-count
    assert t.item(1, 6).text() == "\u22122.0"
    assert t.item(1, 0).data(RING_ROLE) == 1
    assert not hasattr(v, "t_fix_first"), "the imperative tile is gone"
    note = v.straights.note.text()
    assert not v.straights.note.isHidden() and note.startswith("Most exit leverage: C1 — "), note
    assert "2.0 km/h under your best lap's" in note and "C1 → C2" in note and "+0.30 s" in note
    assert "Coaching" not in note, "this stub has no corner ids, so no Coaching clause"
    labels = [lb.text().lower() for lb in v.findChildren(QLabel)]
    assert not any("fix first" in t for t in labels), [t for t in labels if "fix" in t]
    # Coaching starts at the same corner: the line says so …
    sess.coaching_opportunities = lambda: _digest_opportunities([0.4, 0.2], n_laps=12)
    v.refresh()
    note = v.straights.note.text()
    assert "The Coaching tab starts with C1" not in note and "C1 is also where" in note, note
    # … and when it starts elsewhere, the line names where and why the two differ.
    elsewhere = _digest_opportunities([0.4, 0.2], n_laps=12)
    elsewhere = replace(elsewhere, rows=[replace(r, cid=c)
                                         for r, c in zip(elsewhere.rows, (3, 1), strict=True)])
    sess.coaching_opportunities = lambda: elsewhere
    v.refresh()
    note = v.straights.note.text()
    assert "The Coaching tab starts with C3: it ranks the time lost inside the corners" in note, note
    # … and when Coaching's own theme cannot separate its top two ("Start with C3 or C1"), the line
    # names both, as that page does — measured on Sandown 3h, where this corner is the second.
    from studio import coaching
    spread = coaching.Evidence(n_laps=12, reach_laps=4, reach=coaching.REACH_REPEAT, iqr=0.2,
                               abstain=coaching.ABSTAIN_NONE)
    tied = replace(elsewhere, rows=[replace(r, time_lost=t, evidence=spread)
                                    for r, t in zip(elsewhere.rows, (0.40, 0.38), strict=True)])
    sess.coaching_opportunities = lambda: tied
    v.refresh()
    note = v.straights.note.text()
    assert note.endswith("The Coaching tab starts with C3 or C1 — this is one of them."), note
    # A tie wider than three is named as Coaching names it: three corners and a count.
    wide = replace(tied, rows=[replace(r, cid=c, time_lost=t) for r, c, t in zip(
        tied.rows * 2, (3, 1, 4, 5), (0.40, 0.39, 0.38, 0.37), strict=True)])
    sess.coaching_opportunities = lambda: wide
    v.refresh()
    note = v.straights.note.text()
    assert note.endswith("The Coaching tab starts with C3, C1, C4 or 1 more — this is one of "
                         "them."), note
    fired = []
    v.corner_clicked.connect(fired.append)
    t.selectRow(0)
    assert fired and fired[-1] == 2                        # the wrap straight rings C2
    # No straights data -> section + note hidden.
    sess.straights_report = lambda: []
    v.refresh()
    assert v.straights.heading.isHidden() and v.straights.note.isHidden()
    print("test_stats_view_straights_table_and_exit_leverage_note OK")


def test_stats_view_straights_say_how_many_laps_each_column_counted():
    """C4 on the STRAIGHTS table's face: a column that counted fewer laps than the table has says
    how many on hover, and a straight NO lap matched at both ends is a row of dashes that explains
    itself — not dropped as a ~0 s stub, which is a different fact."""
    _app()
    from studio.stats import StraightStat
    from studio.stats_panel import DASH, StatsView
    sess = _fake_view_session()
    sess.straights_report = lambda: [
        StraightStat(index=1, label="C1 → C2", ring_cid=1, n=38, best_s=8.0, median_s=8.3,
                     sigma_s=0.3, trap_best_kmh=90.0, trap_median_kmh=89.0,
                     exit_delta_kmh=-2.0, leverage=0.6, n_laps=38, n_trap=38, n_exit=38),
        StraightStat(index=8, label="C8 → C9", ring_cid=8, n=0, best_s=None, median_s=None,
                     sigma_s=None, trap_best_kmh=88.0, trap_median_kmh=87.0,
                     exit_delta_kmh=-1.0, leverage=0.0, n_laps=38, n_trap=10, n_exit=8),
    ]
    v = StatsView(sess)
    t = v.straights.table
    assert t.rowCount() == 2, "a straight with no matched time was dropped as a stub"
    rows = {t.item(r, 0).text(): r for r in range(t.rowCount())}
    full, part = rows["C1 → C2"], rows["C8 → C9"]
    assert all(t.item(full, c).toolTip() == "" for c in range(1, 7)), "an all-laps row disclosed"
    for c in (1, 2, 3):
        assert t.item(part, c).text() == DASH
        assert "No value" in t.item(part, c).toolTip() and "38" in t.item(part, c).toolTip()
    for c in (4, 5):
        assert "10 of 38" in t.item(part, c).toolTip(), t.item(part, c).toolTip()
    assert "8 of 38" in t.item(part, 6).toolTip() and "C8's exit" in t.item(part, 6).toolTip()
    v.hide()
    print("test_stats_view_straights_say_how_many_laps_each_column_counted OK")
def _coast_places(*rows):
    from studio.stats import CoastPlace
    return [CoastPlace(index=i, label=label, ring_cid=ring, s_per_lap=s, laps=laps, share=share,
                       tied=tied)
            for i, (label, ring, s, laps, share, tied) in enumerate(rows)]


def test_stats_view_coasting_table_ranks_marks_ties_and_rings_the_map():
    """The COASTING table on a stubbed report: ranked rows, the "vs top" word on every row, the
    unlisted count in the heading, the note that says what the order is worth, and a row click that
    rings the place on the map through the one corner_clicked pathway."""
    _app()
    from studio.stats import CoastReport
    from studio.stats_coasting import (
        COAST_LESS,
        COAST_LIST_MIN_S,
        COAST_TIED,
        COAST_TOP,
        COASTING_TOOLTIP,
    )
    from studio.stats_common import _DRIVING_COAST, RING_ROLE
    from studio.stats_panel import StatsView
    sess = _fake_view_session()
    # D24-shaped: no place separates from the leader except the last listed one.
    sess.coast_report = lambda: CoastReport(n_laps=38, per_lap_s=1.1, lead_separable=False,
                                            places=_coast_places(
        ("C1", 1, 0.311, 17, 0.28, True),
        ("C9 → C10", 9, 0.099, 3, 0.09, True),
        ("C1 → C2", 1, 0.097, 5, 0.09, False),
        ("C8", 8, 0.02, 2, 0.02, False)))
    v = StatsView(sess)
    t = v.coasting.table
    assert not t.isHidden() and not v.coasting.heading.isHidden()
    assert t.rowCount() == 3, "a place under COAST_LIST_MIN_S is counted, not listed"
    assert v.coasting.heading.text() == f"COASTING · 1 under {COAST_LIST_MIN_S:.2f} s a lap not listed"
    assert [t.item(r, 0).text() for r in range(3)] == ["C1", "C9 → C10", "C1 → C2"]
    assert [t.item(r, 1).text() for r in range(3)] == ["0.31", "0.10", "0.10"]
    assert t.item(0, 2).text() == "17/38" and t.item(0, 3).text() == "28"
    words = [t.item(r, 4).text() for r in range(3)]
    assert words == [COAST_TIED, COAST_TIED, COAST_LESS], (
        f"the vs top column crowns a leader the laps did not separate: {words}")
    note = v.coasting.note.text()
    assert note.startswith("No one place leads: C1 and C9 → C10 are tied"), note
    assert "38 clean laps cannot put them in order" in note, note
    assert _DRIVING_COAST in COASTING_TOOLTIP and t.toolTip() == COASTING_TOOLTIP
    fired = []
    v.corner_clicked.connect(fired.append)
    t.selectRow(1)
    assert fired[-1] == 9 and t.item(1, 0).data(RING_ROLE) == 9   # a straight rings its feeder
    t.clearSelection()
    assert fired[-1] is None

    # Sandown-shaped: a leader that separates reads "top", everything else "less".
    sess.coast_report = lambda: CoastReport(n_laps=59, per_lap_s=3.76, lead_separable=True,
                                            places=_coast_places(
        ("C1", 1, 1.016, 54, 0.27, True), ("C4", 4, 0.619, 47, 0.16, False)))
    v.refresh()
    assert v.coasting.heading.text() == "COASTING"
    assert [t.item(r, 4).text() for r in range(2)] == [COAST_TOP, COAST_LESS]
    assert v.coasting.note.text() == ("C1 holds the most coasting — 1.02 s a lap, more than C4 "
                                      "(0.62 s) or anywhere else by a margin these 59 clean laps "
                                      "can separate."), v.coasting.note.text()

    # A session that did not coast keeps the heading and says so; no instrument hides it all.
    sess.coast_report = lambda: CoastReport(n_laps=12, per_lap_s=0.0, places=[],
                                            lead_separable=False)
    v.refresh()
    assert t.isHidden() and not v.coasting.heading.isHidden()
    assert v.coasting.note.text() == "No coasting was detected on the 12 clean laps."
    sess.coast_report = lambda: None
    v.refresh()
    assert v.coasting.heading.isHidden() and t.isHidden() and v.coasting.note.isHidden()
    print("test_stats_view_coasting_table_ranks_marks_ties_and_rings_the_map OK")


def test_coast_note_names_at_most_six_tied_places():
    from studio.stats import CoastReport
    from studio.stats_coasting import coast_note
    places = _coast_places(*[(f"C{k}", k, 0.4 - 0.01 * k, 10, 0.1, True) for k in range(1, 12)])
    note = coast_note(CoastReport(n_laps=38, per_lap_s=4.0, places=places, lead_separable=False))
    assert note.startswith("No one place leads: C1, C2, C3, C4, C5, C6 and 5 more are tied — "
                           "between 0.29 and 0.39 s of coasting a lap"), note
    one = coast_note(CoastReport(n_laps=1, per_lap_s=0.5, lead_separable=True,
                                 places=_coast_places(("C3", 3, 0.5, 1, 1.0, True))))
    assert one == "All the coasting on the 1 clean lap is in C3: 0.50 s a lap.", one
    print("test_coast_note_names_at_most_six_tied_places OK")


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
    # The last PACE tile sits within the first two columns now (row-major re-place).
    g, tiles, _group = v._tile_grids[1]            # the PACE grid
    idx = tiles.index(v.t_trend)
    r, c = idx // 2, idx % 2
    assert g.itemAtPosition(r, c) is not None and g.itemAtPosition(r, c).widget() is v.t_trend
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
    and the three that did not are the ones this test was really for: the same PACE tiles take no
    more rows, reach strictly further right, and go back when the pane does."""
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
    # Measured on the real laid-out geometry, not on the column count: the same PACE tiles
    # occupy no more rows and reach further right, which is the whole point. (Strictly FEWER rows
    # while PACE had ten tiles, 4 -> 3; at six (R11) both panes need two, 3 + 3 and 4 + 2, and the
    # reach to the right is what still tells the two layouts apart.)
    wide_rows, wide_right = _pace_layout(v)
    assert wide_rows <= narrow_rows, (wide_rows, narrow_rows)
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
    # R11: the circle has no heading of its own now — it sits in the SPEED · G block whose peak-g
    # tiles are its extremes — so the unit convention is that block's heading.
    assert not hasattr(v, "_gg_section") and "G" in v._speed_section.text()
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
    text_good = v_good.trust.card.text()
    session.gmeter_cross = lambda: halved
    v_bad = StatsView(session)
    text_bad = v_bad.trust.card.text()
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
    assert v.corners.heading.isHidden() and v.corners.table.isHidden()
    print("test_stats_view_corners_table_hidden_without_corners OK")


def test_stats_view_unit_flip():
    _app()
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    # SHOWN, because since P1 this page renders only when it can be seen: `set_speed_unit` stores
    # the unit and defers the render on a page that is not on screen (it is paid by showEvent or by
    # CentralView's accessor). Showing it keeps this test asserting what it always asserted — that
    # the flip itself re-renders — rather than the deferral.
    v.show()
    v.set_speed_unit("mph")
    assert "60.6 mph" in v.t_vmax.value.text()                   # 97.5 km/h -> mph
    assert "speeds in mph" in v._laps_section.text()
    assert v.lap_table.item(0, 2).text() == "59.0"               # 95.0 km/h -> mph
    v.set_speed_unit("kmh")
    assert "97.5 km/h" in v.t_vmax.value.text()
    v.hide()
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
    text = v.trust.card.text().lower()
    assert "auto-fitted, not confirmed" in text
    assert "track: unknown" in text
    # Both counts stated, and the denominator is the laps FOUND (lap_count) — not valid+excluded,
    # which would be arithmetic invented to make the two numbers meet.
    assert "statistics use: 2 of the 5 laps found" in text and "3 ⊘ excluded" in text
    # ...and each of those is its OWN row, marked as a caveat — that is what the card being a fact
    # list rather than a paragraph buys, and it is what makes the alarms scannable. The caveats
    # lead: no provenance row may sort above one.
    caveats = [t for t, _v, c in v.trust.card.rows() if c]
    assert {"Start/finish line", "Track", "Statistics use"} <= set(caveats), caveats
    marks = [c for _t, _v, c in v.trust.card.rows()]
    assert marks == sorted(marks, reverse=True), f"caveats must lead the card: {marks}"

    clean = StatsView(_fake_view_session(excluded=()))  # verified, named track, nothing dropped
    text = clean.trust.card.text().lower()
    for phrase in ("auto-fitted", "track: unknown", "excluded"):
        assert phrase not in text, f"clean session must not claim {phrase!r}"
    # (the fixture does carry a ⚠ dropout lap, so "no caveats at all" is not the claim — the claim
    # is that the three the session does not have are absent, and that the caveats still lead)
    clean_marks = [c for _t, _v, c in clean.trust.card.rows()]
    assert clean_marks == sorted(clean_marks, reverse=True), clean_marks
    assert {"Start/finish line", "Track", "Statistics use"}.isdisjoint(
        {t for t, _v, c in clean.trust.card.rows() if c}), clean.trust.card.rows()
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
    assert "break in series" not in StatsView(plain).trust.card.text().lower()

    sess = _fake_view_session()
    sess.skipped_chapters = ["GX010060.MP4"]
    v = StatsView(sess)
    text = v.trust.card.text().lower()
    assert "break in series" in text, text
    assert "could not be read" in text and "closes over the gap" in text, text
    # It is a CAVEAT row, so it sorts with the other trust-breaking facts and above provenance.
    caveats = [t for t, _val, c in v.trust.card.rows() if c]
    assert "Break in series" in caveats, v.trust.card.rows()
    marks = [c for _t, _val, c in v.trust.card.rows()]
    assert marks == sorted(marks, reverse=True), f"caveats must lead the card: {marks}"

    desync = _fake_view_session()
    desync.chapters = _Map([("GX020060.MP4", 4.2)])
    assert "telemetry than video" in StatsView(desync).trust.card.text().lower()
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
    line = next(ln for ln in v.trust.card.text().split("\n") if ln.startswith("Timing:"))
    assert line == "Timing: GPS9 true clock · 0% of moving fixes rejected", line
    assert "WHILE MOVING" in v.trust.card.toolTip()
    # The measured value is the shipped one — the fix was the sentence, not the maths.
    assert sess.timing_quality.dropped_pct() == 0
    print("test_stats_view_trust_card_names_the_moving_fix_population OK")


def test_stats_view_states_the_missing_accelerometer():
    """With no IMU the trust card used to go silent about the g channel — exactly when the peak-g
    tiles, the per-lap g columns and the Grip column all render em-dashes."""
    _app()
    from studio.stats_panel import NO_GMETER_NOTE, StatsView

    v = StatsView(_fake_view_session(has_g=False))
    assert NO_GMETER_NOTE in v.trust.card.text()
    assert v.no_gmeter_note.isVisibleTo(v)                  # said again beside the dashes
    assert v.t_peak_lat.value.text() == "—"                 # the dash it explains
    v = StatsView(_fake_view_session(has_g=True))
    assert NO_GMETER_NOTE not in v.trust.card.text()
    assert not v.no_gmeter_note.isVisibleTo(v)
    assert "IMU lateral" in v.trust.card.text()
    print("test_stats_view_states_the_missing_accelerometer OK")


def test_stats_view_trust_card_states_why_the_imu_was_not_used():
    """A REFUSED accelerometer read, on this card, exactly like a camera that never had a usable one:
    "g-meter: GPS lateral · GPS-derived longitudinal", unmarked. Measured on the real StudioWindow
    over the bundled hero8.mp4 (GRAV all zeros) and over a D24 recording pushed through the real
    gate with its limit at 0: that row, no caveat, and no cross-check row either (a refused IMU is
    never cross-checked), so nothing on the page said the IMU had been set aside, let alone why.

    The row is where the card already states the g source, so the reason goes there, marked as a
    caveat exactly like the no-accelerometer and DISAGREE rows beside it."""
    _app()
    from studio.gmeter import AxisCheck
    from studio.stats_panel import NO_GMETER_NOTE, StatsView

    def _g_row(view):
        return next(r for r in view.trust.card.rows() if r[0] == "g-meter")

    tilted = AxisCheck(n=5000, tilt_deg=34.2, measurable=True, ok=False)
    blind = AxisCheck(n=0, tilt_deg=float("nan"), measurable=True, ok=False, has_direction=False)
    for axis in (tilted, blind):
        sess = _fake_view_session()
        sess.gmeter_source = lambda: "gps"
        sess.gmeter_cross = lambda: None
        sess.gmeter_axis = lambda axis=axis: axis
        v = StatsView(sess)
        _term, value, caveat = _g_row(v)
        assert caveat, f"a refused IMU must be a caveat row: {_g_row(v)}"
        assert value.startswith("GPS lateral · GPS-derived longitudinal"), value
        assert axis.refusal() in value, value
        assert axis.summary() in v.trust.card.toolTip(), v.trust.card.toolTip()

    # A refused IMU with no GPS trajectory to fall back on has no meter at all — and it is still not
    # "no accelerometer in this recording": the recording had one, and it was refused.
    sess = _fake_view_session(has_g=False)
    sess.gmeter_axis = lambda: blind
    text = StatsView(sess).trust.card.text()
    assert NO_GMETER_NOTE not in text and blind.refusal() in text, text

    # An ALIGNED check changes nothing on the card.
    sess = _fake_view_session()
    sess.gmeter_axis = lambda: AxisCheck(n=5000, tilt_deg=5.1, measurable=True, ok=True)
    v = StatsView(sess)
    assert _g_row(v) == ("g-meter", "IMU lateral · GPS-derived longitudinal", False), _g_row(v)
    print("test_stats_view_trust_card_states_why_the_imu_was_not_used OK")


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
    assert "below" not in v.trust.card.text().lower()
    # THE BLOCK IS TWO LABELS, so the copy is read as the block. `#ProvisionalBanner` is an 11 px
    # semibold amber call-to-action LINE and it was carrying all 308 characters; the statement
    # stays in the strip and the why/what-next moved into the app's prose step at the app's prose
    # measure (tests/test_measure_floors.py owns that geometry). Reading one label alone is how a
    # split like this silently loses half a sentence, so both are read here.
    note = f"{v.no_laps_note.text()} {v.no_laps_prose.text()}".lower()
    assert "no complete laps" in note and "start/finish line" in note   # reason + next action
    assert not v._pace_section.isVisibleTo(v) and not v._speed_section.isVisibleTo(v)
    tiles = ("t_best", "t_median", "t_race_pace", "t_rolling", "t_sigma", "t_trend",
             "t_vmax", "t_vmin", "t_peak_lat", "t_peak_brake")
    dashed = [n for n in tiles
              if getattr(v, n).isVisibleTo(v) and getattr(v, n).value.text() == "—"]
    assert dashed == [], f"dash-only tiles still visible: {dashed}"
    assert v.t_duration.isVisibleTo(v) and v.t_duration.value.text() == "1:01"  # real recording
    assert v.trust.card.text() != "—"                       # the diagnostic stays on the page
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
    lab = v.trust.card
    y = lab.mapTo(v._scroll.widget(), lab.rect().topLeft()).y()
    viewport = v._scroll.viewport().height()
    assert 0 < y < viewport, f"trust card at y={y} is outside the first {viewport}px viewport"
    assert y + lab.height() < viewport, "the card must fit whole in the first viewport"
    v.close()
    print("test_stats_view_trust_card_is_above_the_fold OK")


# ------------------------------------------------ real coaching rows for the notes' tests
def _digest_opportunities(losses, n_laps=65):
    """Real `coaching` dataclasses for `losses` (s, already ranked) — the shape the Coaching tab
    consumes and the Stats page's notes quote."""
    from studio import coaching
    rows = [coaching.Opportunity(
                cid=i + 1, direction=1 if i % 2 == 0 else -1, time_lost=t,
                entry_dist=100.0 * (i + 1),
                reason=coaching.Reason(kind=coaching.REASON_NONE, contribution=t,
                                       apex_speed_deficit=0.0, brake_extra_s=0.0,
                                       coast_extra_s=0.0, sigma=0.05))
            for i, t in enumerate(losses)]
    return coaching.Opportunities(enough=True, n_laps=n_laps, median_lap_id=3, rows=rows)


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
        for table in (v.lap_table, v.corners.table):
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
        card = v.trust.card
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
    from studio.stats_common import ReportTable
    out = []
    for t in v.findChildren(ReportTable):
        if t.isHidden() or not t._needs_bar():
            continue
        head = t.horizontalHeaderItem(0)
        columns_px = t.content_width() - 2 * t.frameWidth() - 2
        out.append(((head.text() if head else "?"), columns_px - t.viewport().width()))
    return out


def test_no_composed_column_hides_a_report_table_column():
    """THE ASSERTION WHOSE ABSENCE LET A P1 SHIP GREEN.

    Composing the page into columns narrower than its report tables does not wrap them — the
    tables are content-sized and scroll (see stats_common.ReportTable) — it HIDES their rightmost columns
    behind an inner scrollbar. Measured on D24 before the packer learned to ask: at the app's own
    default 1440x900 window, maximized, `Apex best · Apex med · Grip %` were gone from CORNERS,
    `Trap med · Exit Δ` from STRAIGHTS, `Brake s · Coast s` from PER LAP and `m later` from
    BRAKING — 266 / 159 / 90 / 81 px of hidden columns on a page whose single-column form showed
    all of them.

    The outer page-level check below is NOT this check, and believing it was is how the defect got
    through: the page fit its pane perfectly the whole time. The scroll had moved INSIDE the
    tables, which is the one place `ReportTable` is designed to put it and the one place nothing
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
    rows = [(t, val) for t, val in v.trust.card._widgets if val.isVisible()]
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
    values = [val for _term, val, _caveat in v.trust.card.rows()]
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

    from studio import gmeter
    from studio.stats_panel import GG_TOOLTIP, StatsView

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
    src = _stats_page_source()
    literal = f"{gmeter.LONG_SMOOTH_S:g} s"
    for line in src.splitlines():
        if literal in line and "LONG_SMOOTH_S" not in line:
            raise AssertionError(
                f"the Stats page (stats_*.py) types the smoothing window as a literal — it must read the "
                f"constant, or the copy rots the moment the signal changes: {line.strip()!r}")
    print("ok brake-g: both g surfaces state the smoothing window, read from the constant")


def _stats_page_source() -> str:
    """The Stats page's whole source — the shell and every section split out of it (ARCH-3,
    `studio/stats_*.py`). The copy guards below read the page's SOURCE for a typed literal, and one
    that read `stats_panel.py` alone would stop covering a section, green, the day it moved out."""
    import pathlib

    from studio.stats_panel import __file__ as sp_file
    files = sorted(pathlib.Path(sp_file).parent.glob("stats_*.py"))
    assert len(files) >= 6, files      # the shell, stats_common and four sections at least
    return "\n".join(f.read_text(encoding="utf-8") for f in files)


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
    note = view.corners.note.text()

    # BOTH baselines, in words, on the face — not only in a tooltip.
    assert "own Best" in note, note
    assert "against your best lap" in note, note
    # ...and the three totals, computed from the data in front of it.
    assert "1.00 s" in note, ("the Med loss column's own sum", note)
    assert "0.50 s" in note, ("the coaching total, the number the other tab prints", note)
    assert "its headline totals 0.50 s over 3 corners" in note, note
    # It must never read as three estimates of one quantity.
    assert "Different baselines" in note, note
    # An empty report hides the note rather than leaving a stale sentence under nothing.
    sess.corner_report = lambda: []
    view.refresh()
    assert view.corners.note.text() == "" and not view.corners.note.isVisible(), (
        view.corners.note.text())
    print("ok corners-note: both baselines named, three totals reconciled, hidden when empty")


def test_slowest_corner_is_the_typical_lap_not_one_lap_s_moment():
    """LOOK-12 (QA 2026-09-26). SPEED · G printed the session MINIMUM as "slowest point": 14.5 km/h
    on MK_18_09, lap 18's traffic moment, against a typical 30.1 km/h (the median of the per-lap
    minimums, the figure STINTS' Min takes per run); 31.4 against 39.3 on SD_19_09. The tile now
    shows the typical over the CLEAN laps, and its hover names the minimum with its lap."""
    _APP  # noqa: B018
    from studio.stats_panel import StatsView

    mins = {0: 30.0, 1: 31.0, 2: 29.0, 3: 14.5, 4: 32.0, 5: 9.0}   # lap 5: a GPS-dropout lap
    arrays = {i: (np.array([0.0, 1000.0]), np.array([v, 90.0]), np.array([0.0, 70.0]))
              for i, v in mins.items()}
    st = _service(valid=list(mins), cons=[0, 1, 2, 3, 4], lap_times={i: 70.0 for i in mins},
                  arrays=arrays, windows={i: (70.0 * i, 70.0 * i + 70.0) for i in mins})
    assert st.slowest_corner() == (30.0, 5, 14.5, 3), st.slowest_corner()

    sess = _fake_view_session()
    sess.stats.slowest_corner = st.slowest_corner
    v = StatsView(sess)
    assert v.t_vmin.caption.text() == "slowest corner · typical", v.t_vmin.caption.text()
    assert v.t_vmin.value.text() == "30.0 km/h", v.t_vmin.value.text()
    assert "14.5 km/h on lap 4" in v.t_vmin.toolTip(), v.t_vmin.toolTip()
    print("ok slowest-corner: the typical lap's, with the one slowest moment and its lap on hover")


def test_the_two_session_lengths_name_their_clocks():
    """LOOK-5 (QA 2026-09-26). "41:34 recorded" sat over "GPS quality over 2684 s of recording"
    on MK_18_09: the kept GPS trace's span and the footage's, both called the recording. The tile
    is the GPS trace; the quality line spans the footage and says so, in the tile's format."""
    _APP  # noqa: B018
    from studio import data_quality as dq
    from studio.stats_panel import StatsView

    v = StatsView(_fake_view_session())
    assert v.t_duration.caption.text().startswith("GPS trace"), v.t_duration.caption.text()
    cls = np.full(2684, dq.GOOD, np.int8)
    cls[:185] = dq.POOR
    tl = dq.QualityTimeline(cell_s=1.0, cls=cls, n=np.full(2684, 10, np.int32),
                            dropped=np.zeros(2684, np.int32), dop=np.ones(2684),
                            reports_quality=True)
    assert tl.summary().startswith("GPS quality over 44:44 of footage: 185 s poor"), tl.summary()
    print("ok clocks: GPS trace on the tile, footage on the quality line")


def test_every_signed_number_on_the_page_prints_one_minus():
    """LOOK-7 (QA 2026-09-26): the Stats tiles printed "-0.35 s/lap" with an ASCII hyphen, two
    tabs from Coaching's "−8.9 km/h". One formatter now; and U+2212 is the width of "+" in the
    tiles' tabular face, so a signed column stays decimal-aligned (the reason an old note gave
    for keeping the hyphen — a MONO tile — is gone: `mono_font` is Inter with tnum)."""
    _APP  # noqa: B018
    import re

    from PySide6.QtGui import QFontMetricsF
    from PySide6.QtWidgets import QTableWidget

    from studio import theme
    from studio._signal import MINUS, fmt_signed
    from studio.stats_panel import StatsView
    from studio.widgets import Tile

    assert fmt_signed(-0.354, 2, "s/lap") == f"{MINUS}0.35 s/lap"
    assert fmt_signed(0.2) == "+0.20" and fmt_signed(-0.004) == "0.00"
    fm = QFontMetricsF(theme.mono_font(theme.TABLE))
    assert abs(fm.horizontalAdvance(MINUS) - fm.horizontalAdvance("+")) < 0.01, (
        fm.horizontalAdvance(MINUS), fm.horizontalAdvance("+"))
    v = StatsView(_fake_view_session())
    faces = [t.value.text() for t in v.findChildren(Tile)]
    cells = [t.item(r, c).text() for t in v.findChildren(QTableWidget)
             for r in range(t.rowCount()) for c in range(t.columnCount()) if t.item(r, c)]
    hyphen = [x for x in faces + cells if re.search(r"(^|\s)-\d", x)]
    assert not hyphen, f"an ASCII-hyphen minus on the Stats page: {hyphen}"
    assert v.t_trend.value.text() == f"{MINUS}0.05 s/lap", v.t_trend.value.text()
    print("ok one-minus: every signed number on the Stats page prints U+2212")


def test_corners_note_sums_the_coaching_rows_one_way_and_counts_the_unranked():
    """LOOK-3 (QA 2026-09-26). On MK_18_09 the note read "totals 0.29 s, 0.28 s of it in its top
    3": the SAME three ranked corners summed raw (0.286) and rounded (0.13 + 0.09 + 0.06), which
    invents a 0.01 s remainder — while the Coaching tab measured 1.07 s across the 11 corners it
    lists, 8 of them not ranked, and the note never said they existed. The fixture is those
    numbers: three ranked rows whose raw and printed sums differ, and eight abstained rows."""
    _APP  # noqa: B018
    from types import SimpleNamespace

    from studio.stats import CornerReport
    from studio.stats_panel import StatsView

    def corner(cid):
        return CornerReport(cid=cid, direction=1, n=6, best_s=9.0, median_s=9.2, sigma_s=0.1,
                            median_loss_s=0.2, apex_best_kmh=60.0, apex_median_kmh=58.0,
                            grip_median=0.8, score=0.02)

    def row(cid, lost, ranked):
        return SimpleNamespace(cid=cid, time_lost=lost, evidence=SimpleNamespace(ranked=ranked))

    ranked = [row(5, 0.134, True), row(2, 0.088, True), row(8, 0.064, True)]    # raw 0.286
    unranked = [row(c, v, False) for c, v in ((7, 0.25), (11, 0.187), (6, 0.14), (9, 0.08),
                                              (4, 0.06), (10, 0.03), (12, 0.02), (1, 0.02))]
    sess = _fake_view_session()
    sess.corner_report = lambda: [corner(c) for c in range(1, 13)]
    sess.phase_report = lambda: None
    sess.coaching_opportunities = lambda: SimpleNamespace(enough=True, rows=ranked + unranked)
    view = StatsView(sess)
    note = view.corners.note.text()
    # ONE way: the rows print 0.13, 0.09 and 0.06, so the only total of them is 0.28 s.
    assert "0.29" not in note, ("the raw sum of the rounded rows is back", note)
    assert "its headline totals 0.28 s over 3 corners" in note, note
    # EVERY row the Coaching tab lists is accounted for, as not ranked, with its printed total.
    shown = sum(round(r.time_lost, 2) for r in unranked)
    assert f"8 more are listed there but not ranked ({shown:.2f} s)" in note, note
    print("ok corners-note: one way of summing, and the not-ranked rows counted")


def test_every_cross_lap_corner_surface_counts_the_same_cells():
    """C4 — ONE rule for which lap × corner cells count, end to end through the real Session on the
    seven-lap drift session: the CORNERS table (`corner_report`), the Corners page ★'s bests
    (`corner_session_bests`), the CORNERS BY LAP grid's typical and the phase split
    (`phase_report`) count exactly the corners `lap_corner_resolved` marks True; the STRAIGHTS
    table (`straights_report`) counts by the same rule per edge (`lap_edge_resolved`), of which a
    resolved corner is exactly two matched edges; and the real Corners page stars none of the
    others. (The Best cell's provenance needs raw fixes this fixture's laps do not carry;
    tests/test_provenance.py holds it to the same cells.)

    Three states are driven, because the fixture alone reaches only one of them. Its interpolated
    edge (lap 1, C1's exit) is on a SLOWER lap than the best, so it moves the Median and σ but no
    Best; the Best half is proved by planting what D24 0060 measured — the quickest cell of a corner
    interpolated — and the no-match half by planting a corner no lap matched. Each plant patches
    the EDGE resolution (and the corner resolution derived from it) on a FRESH session, so every
    cache is built under it."""
    from _synthetic import _drift_session, drift_band_laps, drift_noise_laps

    from studio.lap_table import BEST_SECTOR_MARK, CornerTable

    first = drift_noise_laps()
    laps = first + drift_band_laps(t0=float(first[-1]["cols"][0][-1]))

    def session(flip=()):
        """`flip` = {(lap, edge index)} planted as interpolated."""
        s = _drift_session(laps)
        if flip:
            real = s.corners.lap_edge_resolved

            def edges(lap):
                return [False if (lap, e) in flip else ok for e, ok in enumerate(real(lap))]

            s.corners.lap_edge_resolved = edges
            s.corners.lap_corner_resolved = lambda lap: [
                a and b for a, b in zip(edges(lap)[0::2], edges(lap)[1::2], strict=True)]
        return s

    def agree(s):
        ids = s.consistency_lap_ids()
        n = len(s.corners.corner_list())
        edges = {i: s.corners.lap_edge_resolved(i) for i in ids}
        cells = {i: ([st.time for st in s.corners.lap_corner_stats(i)],
                     s.corners.lap_corner_resolved(i)) for i in ids}
        for i in ids:
            assert cells[i][1] == [edges[i][2 * k] and edges[i][2 * k + 1] for k in range(n)], i
        report = s.corner_report()
        bests = s.corners.corner_session_bests()
        matrix = s.corner_matrix()
        phase = s.phase_report()
        assert matrix is not None and len(report) == len(bests) == len(matrix.cids) == n
        for k, row in enumerate(report):
            counted = {i: t[k] for i, (t, res) in cells.items() if res[k]}
            assert (row.n, row.n_laps) == (len(counted), len(ids)), (row.cid, row.n, row.n_laps)
            assert row.n == matrix.n_resolved[k], (row.cid, row.n, matrix.n_resolved[k])
            want = min(counted.values()) if counted else None
            assert row.best_s == want == bests[k], (row.cid, row.best_s, want, bests[k])
            if not counted:
                assert row.median_s is None and matrix.medians[k] is None, row.cid
                assert phase is None or phase.rows[k] is None, (row.cid, phase.rows[k])
                continue
            assert row.median_s == float(np.median(list(counted.values()))), row.cid
            if matrix.medians[k] is not None:
                assert row.median_s == matrix.medians[k], (row.cid, row.median_s, matrix.medians)
        # STRAIGHTS: straight j runs corner j's exit (edge 2j-1; the start line for j=0) to corner
        # j+1's entry (edge 2j; the finish line for j=n).
        for j, st in enumerate(s.straights_report()):
            start = [j == 0 or edges[i][2 * j - 1] for i in ids]
            end = [j == n or edges[i][2 * j] for i in ids]
            assert st.n_laps == len(ids), (j, st.n_laps)
            assert st.n == sum(a and b for a, b in zip(start, end, strict=True)), (j, st.n)
            assert st.n_trap == sum(end), (j, st.n_trap)
            assert st.n_exit == (None if j == 0 else sum(start)), (j, st.n_exit)
        return report

    s = session()
    report = agree(s)
    c1_all = float(np.median([s.corners.lap_corner_stats(i)[0].time
                              for i in s.consistency_lap_ids()]))
    assert report[0].n < report[0].n_laps, "the drift session no longer carries an interpolated cell"
    assert report[0].median_s != c1_all, "the interpolated cell no longer moves C1's Median"
    # ...and that edge is C1's EXIT, so the straight after C1 loses its time and exit Δ on that lap
    # but keeps its trap speed (read at C2's entry): the edge granularity, not only the corner's.
    after_c1 = s.straights_report()[1]
    n_ids = len(s.consistency_lap_ids())
    assert after_c1.n < n_ids and after_c1.n_exit < n_ids and after_c1.n_trap == n_ids, (
        "the fixture's interpolated edge is no longer C1's exit alone", after_c1)

    # THE BEST: C2's quickest cell, its exit planted as interpolated. It must leave the Best and the
    # ★ source together — and the Corners page must neither star it nor pass it off as measured,
    # while that lap's C2 ENTRY speed (a matched edge) stays a plain reading.
    ids = s.consistency_lap_ids()
    times = {i: s.corners.lap_corner_stats(i)[1].time for i in ids}
    # EVERY lap tied for the quickest, not just the first of them. This fixture stacks the
    # drift-noise laps on the drift-band ones and BOTH families start from the same undrifted
    # reference line at the same speeds, so two of its laps carry the identical C2 time
    # (7.793777 s, with or without the session geometry). Planting one of a tie leaves the Best
    # exactly where it was and the assertion below would be measuring nothing.
    quickest = min(times.values())
    tied = [i for i in ids if times[i] == quickest]
    quick = tied[0]
    planted = session(flip={(i, 3) for i in tied})
    report = agree(planted)
    assert report[1].best_s > s.corner_report()[1].best_s, "the planted cell did not set the Best"
    table = CornerTable(planted)
    table.set_lap(quick)
    time_cell, entry_cell, exit_cell = (table.table.item(1, c) for c in (1, 5, 6))
    assert not time_cell.text().endswith(BEST_SECTOR_MARK.strip()), (
        "the Corners page starred an interpolated corner", time_cell.text())
    assert time_cell.font().italic() and exit_cell.font().italic(), "an interpolated value is plain"
    assert not entry_cell.font().italic(), "a speed read at a MATCHED edge was muted"

    # NO MATCH: C1's entry interpolated on every lap. Its row stays, empty; nothing is starred there.
    untimed = session(flip={(i, 0) for i in ids})
    report = agree(untimed)
    assert report[0].n == 0 and report[0].score == 0.0, report[0]
    assert untimed.corners.corner_session_bests()[0] is None
    assert untimed.straights_report()[0].trap_best_kmh is None
    print(f"ok one cell set: CORNERS, ★ bests, the grid, the phase split and STRAIGHTS agree on the "
          f"drift session, with lap {quick}'s C2 exit planted as interpolated and with C1's entry "
          f"matched on no lap")


def test_corners_table_says_which_laps_count_and_dashes_a_corner_no_lap_matched():
    """C4, on the face of the CORNERS table. Its rows now count only corners matched on track, so
    a Best or Median over fewer laps than the page's other tables says so where the number is, a
    corner no lap matched reads as dashes that EXPLAIN themselves, and the caption states the count
    and that the Coaching total beside it counts the same cells (W1: it said "every lap" until
    #339 made that false). Nothing is said when every lap counted, so a clean recording's page
    is unchanged."""
    _APP  # noqa: B018
    from studio.stats import CornerReport
    from studio.stats_corners import WORST_LOSS_MARK
    from studio.stats_panel import DASH, StatsView

    full = CornerReport(cid=1, direction=1, n=38, best_s=2.48, median_s=2.69, sigma_s=0.4,
                        median_loss_s=0.21, apex_best_kmh=73.4, apex_median_kmh=67.4,
                        grip_median=0.72, score=0.08, n_laps=38)
    part = CornerReport(cid=8, direction=-1, n=7, best_s=2.09, median_s=2.11, sigma_s=0.09,
                        median_loss_s=0.02, apex_best_kmh=77.1, apex_median_kmh=74.2,
                        grip_median=0.68, score=0.002, n_laps=38)
    none = CornerReport(cid=9, direction=1, n=0, best_s=None, median_s=None, sigma_s=None,
                        median_loss_s=None, apex_best_kmh=None, apex_median_kmh=None,
                        grip_median=None, score=0.0, n_laps=38)
    sess = _fake_view_session()
    sess.corner_report = lambda: [full, part, none]
    sess.phase_report = lambda: None
    sess.coaching_opportunities = lambda: SimpleNamespace(
        enough=True, rows=[SimpleNamespace(cid=8, time_lost=0.05)])
    view = StatsView(sess)
    t = view.corners.table
    rows = {t.item(r, 0).text(): r for r in range(t.rowCount())}

    for col in (1, 2):
        assert t.item(rows["C1"], col).toolTip() == "", "a corner every lap counted says nothing"
        tip = t.item(rows["C8"], col).toolTip()
        assert "7 of 38" in tip and "interpolated" in tip, tip
        assert t.item(rows["C9"], col).text() == DASH
        tip = t.item(rows["C9"], col).toolTip()
        assert "No time for C9" in tip and "38" in tip, tip
    assert not t.item(rows["C9"], 4).text().startswith(WORST_LOSS_MARK), "untimed corner marked"

    note = view.corners.note.text()
    assert "Only corners matched on track count: 45 of 114" in note, note
    assert "the other 69 were interpolated" in note, note
    assert "No lap matched C9 on track" in note, note
    # W1: since #339 Coaching counts only the cells this table counts (a lap's corner, and the best
    # lap's, matched on track), so the caption says it counts the SAME cells. Until W1 it said
    # Coaching counted every clean lap, interpolated or not, which #339 had made false.
    assert "interpolated corners too" not in note, note
    assert ("The Coaching tab measures the SAME corners against your best lap, leaving out the "
            "same interpolated times: its headline totals" in note), note
    assert "Different baselines" in note, note

    # Every lap counted: the caption and the coaching sentence are exactly what they were.
    sess.corner_report = lambda: [full]
    view.refresh()
    note = view.corners.note.text()
    assert "matched on track" not in note and "interpolated" not in note, note
    assert "against your best lap: its headline totals" in note, note
    view.hide()
    print("ok CORNERS: partial counts disclosed on the cell, an unmatched corner dashed and named, "
          "and the coaching sentence says it leaves out the same cells")


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

    from studio import gmeter
    from studio.stats_panel import GG_TOOLTIP, StatsView

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
    src = _stats_page_source()
    for literal, const in ((lat_w, "LAT_SMOOTH_S"), (long_w, "LONG_SMOOTH_S")):
        for line in src.splitlines():
            if literal in line and const not in line:
                raise AssertionError(
                    f"the Stats page (stats_*.py) types the {const} window as a literal — it must read the "
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
    # mph flips the speed columns AND the heading, like every other speed on this page. Shown
    # first: since P1 a unit flip on a page that cannot be seen is deferred, not dropped (see
    # test_stats_view_unit_flip), and what is under test here is the re-render.
    v.show()
    v.set_speed_unit("mph")
    assert "mph" in v._stints_section.text()
    assert v.stints_table.item(0, 6).text() == "16.3"
    v.hide()
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
    assert "\u22120.05 s against your typical S1" in mid.toolTip(), mid.toolTip()
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


# ------------------------------------------------------------ F7: the laps × corners grid
def _corner_grid_fixture():
    """Eight laps × two corners, every cell's resolution chosen on purpose.

    C1: laps 0-5 resolved at 4.00-4.05 s; lap 6 RESOLVED and a whole second off (5.00); lap 7 the
        SAME 5.00 but UNRESOLVED — its window edge was interpolated, so it must be neither marked nor
        counted. Resolved median 4.03, scale 0.40 (the p90 of the resolved deviations).
    C2: laps 0-5 resolved at 3.00-3.25 in 0.05 steps; laps 6 and 7 UNRESOLVED and a whole second
        FAST (2.00) — counted, they would drag the typical from 3.125 to 3.075."""
    ids = list(range(8))
    c1 = [4.00, 4.01, 4.02, 4.03, 4.04, 4.05, 5.00, 5.00]
    c2 = [3.00, 3.05, 3.10, 3.15, 3.20, 3.25, 2.00, 2.00]
    times = [[a, b] for a, b in zip(c1, c2, strict=True)]
    resolved = [[True, True]] * 6 + [[True, False], [False, False]]
    return ids, times, resolved


def test_corner_matrix_never_marks_or_counts_a_cell_whose_window_edge_was_interpolated():
    """F7. Measured on the D24 0060 pair before #335 against an independent gate-crossing time, a
    corner cell with an interpolated window edge was off by a median 0.219 s (max 0.886 s) and a
    matched one by 0.004 s (max 0.024 s); 7 of the 10 marks the plain rule put on interpolated
    cells were not confirmed. So the grid shows those cells and refuses them twice: no ▼, and no
    vote in the typical the other laps are measured against (stats.CornerMatrix has the numbers)."""
    ids, times, resolved = _corner_grid_fixture()
    m = corner_matrix(ids, [1, 2], times, resolved)
    assert m is not None and m.cids == [1, 2]
    assert m.n_resolved == [7, 6], f"resolved cells miscounted: {m.n_resolved}"
    assert abs(m.medians[0] - 4.03) < 1e-9, m.medians
    assert abs(m.medians[1] - 3.125) < 1e-9, (
        f"two interpolated, fast cells moved C2's typical: {m.medians[1]}")
    assert abs(m.scales[0] - 0.40) < 1e-9, m.scales
    # The same +0.97 s: marked where the window was matched, not where it was interpolated.
    assert m.is_behind(6, 0), "a resolved cell a second off its typical carries no ▼"
    assert not m.is_behind(7, 0), "an interpolated cell was marked"
    assert m.cells[7][0] == 5.00 and m.resolved[7][0] is False  # shown, never hidden
    assert sum(m.is_behind(r, c) for r in range(8) for c in range(2)) == 1
    print("test_corner_matrix_never_marks_or_counts_a_cell_whose_window_edge_was_interpolated OK")


def test_corner_matrix_refuses_a_column_it_cannot_take_a_typical_of():
    """A percentile needs values to be a percentile OF (MATRIX_MIN_LAPS) — and in this grid the
    values are the RESOLVED cells, so a corner matched on too few laps gets no typical and no mark
    however far a lap is off. The whole grid still refuses under MATRIX_MIN_LAPS laps, like SPLITS,
    and a lap that projected no row is kept as a blank row rather than dropped."""
    ids = list(range(MATRIX_MIN_LAPS + 2))
    times = [[3.0 + 0.01 * i] for i in ids]
    times[0] = [9.0]
    few = [[i < MATRIX_MIN_LAPS - 1] for i in ids]
    m = corner_matrix(ids, [4], times, few)
    assert m.n_resolved == [MATRIX_MIN_LAPS - 1], f"resolved cells miscounted: {m.n_resolved}"
    assert m.medians == [None] and m.scales == [None], (m.medians, m.scales)
    assert not any(m.is_behind(r, 0) for r in range(len(ids)))
    # ...one more resolved lap and the same +6 s is marked.
    enough = [[i < MATRIX_MIN_LAPS] for i in ids]
    assert corner_matrix(ids, [4], times, enough).is_behind(0, 0)
    assert corner_matrix(ids[:MATRIX_MIN_LAPS - 1], [4], times[:MATRIX_MIN_LAPS - 1],
                         enough[:MATRIX_MIN_LAPS - 1]) is None
    assert corner_matrix(ids, [], [[] for _ in ids], [[] for _ in ids]) is None
    degenerate = corner_matrix(ids, [4, 5], [[3.0, 2.0]] * (len(ids) - 1) + [[]],
                               [[True, True]] * (len(ids) - 1) + [[]])
    assert degenerate.lap_ids == ids and degenerate.cells[-1] == [None, None]
    assert degenerate.resolved[-1] == [False, False]
    print("test_corner_matrix_refuses_a_column_it_cannot_take_a_typical_of OK")


def test_corner_and_split_grids_mark_by_one_rule():
    """A ▼ on the Stats page means one thing: ≥ the column's own p90-above-median, floored at
    MATRIX_SCALE_MIN_S, compared at print resolution. With every cell resolved the corner grid must
    decide exactly what the split grid decides on the same numbers."""
    rng = np.random.default_rng(2026)
    rows = (rng.normal(0.0, 0.18, size=(40, 5)) + np.array([2.7, 2.5, 4.6, 6.2, 6.9])).round(3)
    rows[3, 1] += 0.9
    rows[17, 4] += 0.45
    rows = [list(map(float, r)) for r in rows]
    ids = list(range(40))
    sm = split_matrix(ids, rows, columns=5)
    cm = corner_matrix(ids, [1, 2, 3, 4, 5], rows, [[True] * 5] * 40)
    assert cm.medians == sm.medians and cm.scales == sm.scales
    marks = [(r, c) for r in range(40) for c in range(5) if sm.is_behind(r, c)]
    assert marks and marks == [(r, c) for r in range(40) for c in range(5) if cm.is_behind(r, c)]
    print("test_corner_and_split_grids_mark_by_one_rule OK")


def test_stats_view_corner_grid_marks_mutes_and_states_what_it_left_out():
    """The page half of F7 on a stub session: the header is the session's own corners, a resolved
    cell past its scale wears the behind hue AND the ▼ that survives greyscale, an interpolated cell
    is muted italic (the trust tier's PROVISIONAL treatment) with no ▼ however far off it is, and
    the note says what the grid is over — including the ⊘ / ⚠ laps that are not rows at all."""
    _app()
    from PySide6.QtGui import QColor

    from studio import theme
    from studio.lap_table import BEST_SECTOR_MARK, DROPOUT_MARK, EXCLUDED_MARK, PROVISIONAL_COLOR
    from studio.stats_panel import StatsView
    ids, times, resolved = _corner_grid_fixture()
    sess = _fake_view_session()                    # excluded (5,), dropout {1}
    sess.corner_matrix = lambda: corner_matrix(ids, [3, 7], times, resolved)
    sess.lap_time = lambda i: 69.0 + i
    v = StatsView(sess)
    t = v.corner_grid_table
    assert not v._corner_grid_band.isHidden() and not t.isHidden()
    header = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    # The lap and its time lead (LOOK-8): see test_the_corner_grid_keeps_the_lap_and_its_time.
    assert header == ["Lap", "Lap time", "C3", "C7"], header
    assert t.rowCount() == 8 and t.item(0, 0).text() == "1"          # 1-based, app-wide
    assert t.item(6, 1).text() == "1:15.000", t.item(6, 1).text()
    slow = t.item(6, 2)
    assert slow.text() == f"{theme.DELTA_BEHIND_ARROW} 5.00", slow.text()
    assert slow.foreground().color() == QColor(theme.behind_colour())
    assert "+0.97 s against your typical C3 (4.03 s" in slow.toolTip(), slow.toolTip()
    assert "7 laps matched on track" in slow.toolTip(), slow.toolTip()
    same = t.item(7, 2)
    assert same.text() == "5.00", f"an interpolated cell carries a mark: {same.text()!r}"
    assert same.font().italic() and same.foreground().color() == PROVISIONAL_COLOR, (
        "an interpolated cell is not muted in the trust tier's provisional style")
    assert same.toolTip().startswith("Not marked: on lap 8, C3"), same.toolTip()
    plain = t.item(0, 2)
    assert not plain.font().italic() and not plain.text().startswith(theme.DELTA_BEHIND_ARROW)
    note = v.corner_grid_note.text()
    assert "8 clean laps × 2 corners." in note, note
    assert "▼ is 0.30–0.40 s or more slower" in note, note
    assert "3 of 16 cells are muted" in note, note
    assert (f"Not in the grid: 1 {EXCLUDED_MARK} excluded and 1 {DROPOUT_MARK} GPS-dropout laps "
            "(see the Laps tab).") in note, note
    # No ★ anywhere in it: "quickest" is the CORNERS table's question (stats.CornerMatrix).
    assert not any(BEST_SECTOR_MARK in t.item(r, c).text()
                   for r in range(t.rowCount()) for c in range(t.columnCount()))
    v.hide()
    # No corner matrix (no corners, or under the floor): the whole band hides.
    bare = StatsView(_fake_view_session())
    assert bare._corner_grid_band.isHidden() and bare.corner_grid_table.isHidden()
    assert bare.corner_grid_note.isHidden()
    bare.hide()
    print("test_stats_view_corner_grid_marks_mutes_and_states_what_it_left_out OK")


def test_the_corner_grid_takes_the_page_width_and_never_the_column_packing():
    """Measured on both D24 recordings through the real StatsView: the grid wants 913 / 924 px, and
    registered in a section column it became that column's minimum and dropped the page a whole
    composition at EVERY dashboard width (0062 at the default 1440x900 window: two columns → one).
    It lives full width under the columns instead, so it must never reach the packer."""
    _app()
    from studio.stats_panel import StatsView
    cids = list(range(1, 13))
    ids = list(range(10))
    times = [[2.5 + 0.1 * c + 0.01 * i for c in range(12)] for i in ids]
    sess = _fake_view_session()
    sess.corner_matrix = lambda: corner_matrix(ids, cids, times, [[True] * 12] * 10)
    wide = StatsView(sess)
    narrow = StatsView(_fake_view_session())
    for v in (wide, narrow):
        v.resize(1420, 900)
        v.show()
    _settle()
    assert wide.corner_grid_table.content_width() > 700, wide.corner_grid_table.content_width()
    got = [wide._group_min_width(g) for g in range(3)]
    want = [narrow._group_min_width(g) for g in range(3)]
    assert got == want, f"the corner grid widened a section column: {got} vs {want}"
    assert wide._layout == narrow._layout, (wide._layout, narrow._layout)
    assert not any(wide.corner_grid_table in tables for tables in wide._column_tables)
    for v in (wide, narrow):
        v.hide()
    print("test_the_corner_grid_takes_the_page_width_and_never_the_column_packing OK")


def test_the_corner_grid_keeps_the_lap_and_its_time_in_view_while_it_scrolls():
    """LOOK-8 (QA 2026-09-26). CORNERS BY LAP for twelve corners wants ~950 px, and the owner's
    Stats pane is 683 px (599 at 1280x800), so the grid scrolls there — and on main the lap time
    was its LAST column, off-screen together with C10-C12: no row could be read whole. The lap and
    its time now lead, frozen over the grid's left edge: scrolled all the way right, the two cells
    a row is read by are still painted where they were, on the same model."""
    _app()
    from studio.stats_panel import StatsView
    cids = list(range(1, 13))
    ids = list(range(10))
    times = [[2.5 + 0.1 * c + 0.01 * i for c in range(12)] for i in ids]
    sess = _fake_view_session()
    sess.corner_matrix = lambda: corner_matrix(ids, cids, times, [[True] * 12] * 10)
    sess.lap_time = lambda i: 69.0 + i
    v = StatsView(sess)
    v.resize(640, 900)
    v.show()
    _settle()
    t = v.corner_grid_table
    header = [t.horizontalHeaderItem(c).text() for c in range(t.columnCount())]
    assert header[:2] == ["Lap", "Lap time"], f"the lap time is not beside the lap: {header}"
    bar = t.horizontalScrollBar()
    assert bar.maximum() > 0, "the fixture is meant to scroll (12 corners in a 640 px page)"
    bar.setValue(bar.maximum())
    _settle()
    frozen = getattr(t, "_frozen", None)
    assert frozen is not None and frozen.isVisible(), "nothing keeps the lap in view"
    lead = t.columnWidth(0) + t.columnWidth(1)
    assert frozen.x() == t.frameWidth() and frozen.width() == lead, (frozen.geometry(), lead)
    assert frozen.model() is t.model() and not frozen.isColumnHidden(1), "not the grid's own cells"
    assert all(frozen.isColumnHidden(c) for c in range(2, t.columnCount()))
    assert frozen.height() >= t.horizontalHeader().height() + t.rowHeight(0) * t.rowCount()
    v.hide()
    print("test_the_corner_grid_keeps_the_lap_and_its_time_in_view_while_it_scrolls OK")


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

    from studio import driving, gmeter
    from studio.stats_braking import BRAKING_TOOLTIP
    from studio.stats_panel import DRIVING_TOOLTIP, LAP_TABLE_TOOLTIP, StatsView

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
    src = _stats_page_source()
    for literal, const in ((f"{driving.COAST_DRAG_MIN:g} g", "COAST_DRAG_MIN"),
                           (f"{driving.MIN_COAST_S:g} s", "MIN_COAST_S"),
                           (f"{driving.AMAX_PCT:g}th percentile", "AMAX_PCT")):
        for line in src.splitlines():
            if literal in line and const not in line:
                raise AssertionError(
                    f"the Stats page (stats_*.py) types {literal!r} as a literal — it must read driving.{const}, "
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
    import re

    from studio import driving
    from studio.stats_panel import DRIVING_TOOLTIP, LAP_TABLE_TOOLTIP

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
    src = _stats_page_source()
    for line in src.splitlines():
        if window in line and "COAST_SMOOTH_S" not in line and "LONG_SMOOTH_S" not in line:
            raise AssertionError(
                f"the Stats page (stats_*.py) types {window!r} as a literal — it must read "
                f"driving.COAST_SMOOTH_S, or the copy rots the moment the window moves: "
                f"{line.strip()!r}")
    print(f"ok coast disclosure: both surfaces state the {window} coast window, composed from "
          f"driving.COAST_SMOOTH_S, and neither denies it")


def test_the_stats_page_names_the_lateral_axis_this_recording_actually_has():
    """FOUR texts on this page named the accelerometer as the lateral channel, on every recording.

    #283 made the GPS-derived state reachable AND visible: `axis_check` refuses an accelerometer
    that does not read gravity when unloaded, and the g-meter toggle + the DATA TRUST card now say
    so. These four did not. Measured over the real `Session.load` on every bundled sample and both
    D24 recordings: SEVEN of the ten bundled clips run a GPS-derived lateral axis (hero5, hero6,
    hero6+ble, hero6a, hero7 and Fusion carry no GRAV/CORI to orient an IMU with; hero8 carries a
    GRAV stream of zeros and is REFUSED), while both D24 recordings measure ALIGNED at 6.1 and
    5.9 deg and keep the accelerometer. So the wording had to change on the first group and could
    not move on the second.

    The GPS-derived lateral is not the accelerometer and does not carry the accelerometer's filter
    chain either: `_resample_gps_only` takes speed x yaw-rate off the 10 Hz trace, and the
    LAT_SMOOTH_S boxcar / 200 Hz sensor rate the copy quoted never touch it. `long_g_gps` is built
    only on the IMU path (gmeter.py), so the LONG_SMOOTH_S window those same sentences claim for
    the braking axis is absent here too.
    """
    _app()
    from studio import gmeter
    from studio.gmeter import AxisCheck
    from studio.stats_panel import (
        BAND_NOTE_LAT,
        GG_TOOLTIP,
        LAP_TABLE_TOOLTIP,
        StatsView,
    )

    # The four surfaces, and the claim each one made on every recording.
    def _surfaces(view):
        return {
            "bands note": view.bands_note.text(),
            "friction circle": view.gg.toolTip(),
            "PER LAP": view.lap_table.toolTip(),
            "peak lateral g": view.t_peak_lat.toolTip(),
        }

    FALSE_ON_GPS = {
        "bands note": "Lateral g comes from the accelerometer",
        "friction circle": "Lateral is the accelerometer",
        "PER LAP": "Lat g is the accelerometer",
        "peak lateral g": "IMU lateral",
    }

    refused = AxisCheck(n=0, tilt_deg=float("nan"), measurable=True, ok=False,
                        has_direction=False)
    # Two ways to reach a GPS-derived lateral axis: an accelerometer REFUSED by the axis gate
    # (hero8), and a camera that never had one to refuse (hero5/6/7, Fusion — axis is None).
    for label, axis in (("refused IMU", refused), ("no IMU at all", None)):
        sess = _fake_view_session()
        sess.gmeter_source = lambda: "gps"
        sess.gmeter_long_source = lambda: "gps"
        sess.gmeter_cross = lambda: None
        sess.gmeter_axis = lambda axis=axis: axis
        v = StatsView(sess)
        for name, text in _surfaces(v).items():
            assert FALSE_ON_GPS[name] not in text, (
                f"[{label}] the {name} still tells the reader the lateral axis is the "
                f"accelerometer, on a recording whose g-meter is GPS-derived: {text!r}")
            assert "GPS" in text, (
                f"[{label}] the {name} never names the GPS trajectory the axis actually comes "
                f"from: {text!r}")
        # ...and it must not claim the ACCELEROMETER'S filter chain for a channel that never went
        # through it. The IMU meter's two windows are absent from the GPS-only path entirely.
        for name in ("bands note", "friction circle"):
            text = _surfaces(v)[name]
            assert f"{gmeter.LAT_SMOOTH_S:g} s" not in text, (
                f"[{label}] the {name} quotes the accelerometer's boxcar on a GPS-derived "
                f"lateral axis: {text!r}")
            assert "200 Hz" not in text, (
                f"[{label}] the {name} quotes the accelerometer's sample rate on a GPS-derived "
                f"lateral axis: {text!r}")
        # The REASON is the one #283 settled — the same clause the toggle and the trust card use,
        # never a second wording for one refusal.
        if axis is not None:
            joined = " ".join(_surfaces(v).values())
            assert axis.refusal() in joined, (
                f"[{label}] no surface gives the refusal in the words #283 settled: {joined!r}")
        v.hide()

    # THE CONTROL, and it is the one that must not move: both D24 recordings keep the
    # accelerometer, so an IMU-lateral session still reads exactly as it shipped.
    imu = StatsView(_fake_view_session())
    assert imu.gg.toolTip() == GG_TOOLTIP
    assert imu.lap_table.toolTip() == LAP_TABLE_TOOLTIP
    assert BAND_NOTE_LAT in imu.bands_note.text()
    assert "IMU lateral" in imu.t_peak_lat.toolTip()
    imu.hide()
    print("ok lateral provenance: GPS-derived recordings stop claiming an accelerometer, and an "
          "IMU-lateral session is untouched")


def test_the_stats_page_quotes_the_braking_window_only_where_that_window_exists():
    """#288 moved FOUR lateral-axis texts onto the recording's real g source and left three behind.

    All three are the same defect class, and all three are measured over the real `Session.load`
    (probe in the PR body; the same 10 bundled samples + both D24 recordings #288 used):

      1. THE NO-G-METER STATE. `gps_lateral_clause` returns None without a g-meter — correctly, its
         lateral g is not GPS-derived either — so karma.mp4 kept the IMU wording: the PER LAP grid
         said "Lat g is the accelerometer" and the peak-lateral tile said "IMU lateral" while the
         note directly above them said "no accelerometer in this recording".
      2. THE PEAK-BRAKING TILE stated the LONG_SMOOTH_S window unconditionally.
      3. `DRIVING_TOOLTIP` did the same, contrasting its own unwindowed detector with "the 0.35 s
         one the peak-braking tile and the friction circle above are drawn on".

    2 and 3 are false on a GPS-derived recording for one reason: `long_g_gps` — the series that
    window belongs to — is built ONLY on the IMU path (`gmeter.compute`), so `_resample_gps_only`
    leaves it None and `stats.lap_stats` falls back to the meter's own `long_g`. Measured on the
    four GPS-derived samples with real motion, the shipped peak |long_g| runs 1.18-1.36x what the
    same series would read had it carried that window (Fusion 1.364x, hero5 1.178x, hero6 1.178x,
    hero6a 1.186x): the window is genuinely absent, not merely unnamed.

    The wording is #283's and #288's, never a second vocabulary for one fact — that consistency is
    the whole point of the package."""
    _app()
    from studio import gmeter
    from studio.gmeter import AxisCheck
    from studio.stats_panel import (
        DRIVING_TOOLTIP,
        LAP_TABLE_TOOLTIP,
        NO_GMETER_CLAUSE,
        PEAK_BRAKE_TOOLTIP,
        PEAK_LAT_TOOLTIP,
        StatsView,
    )

    window = f"{gmeter.LONG_SMOOTH_S:g} s"
    refused = AxisCheck(n=0, tilt_deg=float("nan"), measurable=True, ok=False,
                        has_direction=False)

    # --- 2 and 3: a GPS-derived meter, reached both ways (hero8's REFUSED accelerometer, and the
    # six samples that never had one). The window may be named only to say it is ABSENT.
    for label, axis in (("refused IMU", refused), ("no IMU at all", None)):
        sess = _fake_view_session()
        sess.gmeter_source = lambda: "gps"
        sess.gmeter_long_source = lambda: "gps"
        sess.gmeter_cross = lambda: None
        sess.gmeter_axis = lambda axis=axis: axis
        v = StatsView(sess)
        brake_tip = v.t_peak_brake.toolTip()
        driving_tip = v._driving_section.toolTip()
        assert "Peak SUSTAINED deceleration" not in brake_tip, (
            f"[{label}] the peak-braking tile still calls a number that never met the {window} "
            f"window a SUSTAINED peak: {brake_tip!r}")
        assert "gps trajectory" in brake_tip.lower(), (
            f"[{label}] the peak-braking tile never says where this number comes from: "
            f"{brake_tip!r}")
        for name, tip in (("peak-braking tile", brake_tip), ("DRIVING tooltip", driving_tip)):
            assert "absent" in tip, (
                f"[{label}] the {name} does not say the {window} window is absent on this "
                f"recording — it is built only on the IMU path: {tip!r}")
            assert tip.count(window) == 1, (
                f"[{label}] the {name} quotes the {window} window {tip.count(window)}x on a "
                f"recording that never applied it: {tip!r}")
        assert "the opposite choice from the" not in driving_tip, (
            f"[{label}] DRIVING still contrasts its unwindowed detector with a window the tiles "
            f"above do not carry either: {driving_tip!r}")
        # Every DRIVING tile carries the SAME sentence as its heading — the #276 rule.
        for tile in (v.t_brake, v.t_brake_n, v.t_coast, v.t_longest_coast):
            assert tile.toolTip() == driving_tip, tile.caption.text()
        # ...and one recording gets ONE reason, on every surface that states one.
        axis_why = axis.refusal() if axis is not None else "no usable accelerometer"
        for name, tip in (("peak-braking tile", brake_tip), ("DRIVING tooltip", driving_tip)):
            assert axis_why in tip, (
                f"[{label}] the {name} gives a different reason than the trust card and the "
                f"toggle: {tip!r}")
        v.hide()

    # --- 1: no g-meter at all (karma.mp4). The three surfaces that stay VISIBLE here are the PER
    # LAP grid and the two peak tiles; DRIVING and the friction circle hide themselves.
    nog = StatsView(_fake_view_session(has_g=False, sectors=False))
    lap_tip = nog.lap_table.toolTip()
    lat_tip = nog.t_peak_lat.toolTip()
    brake_tip = nog.t_peak_brake.toolTip()
    assert "Lat g is the accelerometer" not in lap_tip, (
        f"the PER LAP grid still names an accelerometer beside a note saying there is none: "
        f"{lap_tip!r}")
    assert "IMU lateral" not in lat_tip, lat_tip
    for name, tip in (("PER LAP grid", lap_tip), ("peak lateral g tile", lat_tip),
                      ("peak braking g tile", brake_tip)):
        assert window not in tip, (
            f"the {name} quotes the {window} window on a recording with no g-meter at all: "
            f"{tip!r}")
        assert NO_GMETER_CLAUSE in tip, (
            f"the {name} explains its em-dashes in different words from the note above it "
            f"(NO_GMETER_NOTE): {tip!r}")
    nog.hide()

    # --- THE CONTROL: an accelerometer-derived recording (both D24 recordings, and the two max
    # samples) reads exactly as it shipped, byte for byte.
    imu = StatsView(_fake_view_session())
    assert imu.lap_table.toolTip() == LAP_TABLE_TOOLTIP
    assert imu.t_peak_lat.toolTip() == PEAK_LAT_TOOLTIP
    assert imu.t_peak_brake.toolTip() == PEAK_BRAKE_TOOLTIP
    assert imu._driving_section.toolTip() == DRIVING_TOOLTIP
    assert window in imu.t_peak_brake.toolTip() and "SUSTAINED" in imu.t_peak_brake.toolTip()
    imu.hide()
    print("ok braking window: quoted where it exists, said to be absent where it is not, and the "
          "no-g-meter page stops naming an accelerometer it just said it does not have")


def test_stats_view_defers_a_render_it_cannot_be_seen_making_and_pays_it_on_show():
    """P1, at the widget's own level: `refresh_when_shown` renders a VISIBLE page and marks a
    hidden one stale; `flush_if_stale` and `showEvent` pay the debt; `refresh` is unconditional.

    And the fact that makes the deferral safe for the View ▸ Units flip: the unit is STORED even
    when the render is deferred, so a page shown after a flip comes up in the new unit rather than
    in the one the user just left."""
    _app()
    from studio.lap_table import DROPOUT_MARK
    from studio.stats_panel import StatsView
    s = _fake_view_session()
    v = StatsView(s)                                 # __init__ renders once, unconditionally
    try:
        assert not v.isVisible() and not v._stale
        before = v.t_laps.value.text()
        assert f"3 {DROPOUT_MARK}" not in before, before

        s.dropout_lap_ids = lambda: {0, 1, 2}        # a session change the laps tile must show
        v.refresh_when_shown()                       # hidden -> deferred
        assert v._stale, "a hidden page rendered instead of deferring"
        assert v.t_laps.value.text() == before, (
            f"the deferred render happened anyway: {v.t_laps.value.text()!r}")

        v.flush_if_stale()                           # ...and the debt is payable on demand
        assert not v._stale
        assert f"3 {DROPOUT_MARK}" in v.t_laps.value.text(), v.t_laps.value.text()
        assert "97.5 km/h" in v.t_vmax.value.text()

        v.set_speed_unit("mph")                      # a View ▸ Units flip, also deferred
        assert v._stale, "set_speed_unit rendered a page that cannot be seen"
        assert "97.5 km/h" in v.t_vmax.value.text(), "the deferred flip rendered anyway"

        v.show()                                     # showEvent pays it — IN THE NEW UNIT
        assert not v._stale, "the page became visible still owing a render"
        assert "60.6 mph" in v.t_vmax.value.text(), (
            f"a page shown after a unit flip came up in the old unit: {v.t_vmax.value.text()!r}")

        # Visible from here on: the seam renders immediately, nothing is ever owed.
        v.refresh_when_shown()
        assert not v._stale
    finally:
        v.hide()
    print("test_stats_view_defers_a_render_it_cannot_be_seen_making_and_pays_it_on_show OK")


def test_the_raw_stats_page_attribute_is_only_touched_where_it_immediately_re_renders():
    """THE STRUCTURAL HALF of P1's no-stale-number guarantee, pinned by exact equality.

    The Stats page renders lazily, so a reader that reaches the widget without flushing would put a
    figure that predates the last edit on the app's honesty surface. `CentralView.stats_view` is a
    read-only property that flushes first, and it is the widget's ONLY name outside
    `stats_panel.py` — `studio/dev/media_capture.py`, the quality chip's `reveal_trust` and every
    test go through it. The raw `self._stats` exists so the four methods that re-render (or
    re-defer) the page on their very next statement do not pay for a render they are about to
    redo — and a fifth use of it would be a hole.

    EXACT EQUALITY, like `test_layering`'s allow-lists: a method that stops touching `_stats` fails
    this too, so the set cannot quietly stop being true. Source-level `ast`, no import."""
    import ast
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    src = open(os.path.join(repo, "studio", "central_view.py"), encoding="utf-8").read()
    tree = ast.parse(src)

    #  _construct_panels   builds it
    #  stats_view          the accessor itself (flushes, then hands it over)
    #  rebuild_derived_views / refresh_timing_trust / set_speed_unit / refresh_palette
    #                      each re-renders or re-defers the page on the next line
    PINNED = {"_construct_panels", "stats_view", "rebuild_derived_views",
              "refresh_timing_trust", "set_speed_unit", "refresh_palette"}

    touching = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if isinstance(node, ast.Attribute) and node.attr == "_stats" \
                    and isinstance(node.value, ast.Name) and node.value.id == "self":
                touching.add(fn.name)
            # `getattr(self, "_stats", ...)` reaches it by name, which a plain Attribute scan
            # would wave through — the exact shape that has hidden call sites in this repo before.
            if isinstance(node, ast.Constant) and node.value == "_stats":
                touching.add(fn.name)
    assert touching == PINNED, (
        f"studio/central_view.py reaches the raw Stats page from an unexpected set of methods.\n"
        f"  unexpected: {sorted(touching - PINNED)}\n"
        f"  no longer touching it: {sorted(PINNED - touching)}\n"
        f"Everything else must use the `stats_view` property, which flushes a deferred render "
        f"before handing the widget over.")

    # ...and the property must stay read-only: a setter would let a caller swap in a widget the
    # flush never runs on.
    (cls,) = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "CentralView"]
    props = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "stats_view"]
    assert len(props) == 1, f"expected exactly one `stats_view` definition, got {len(props)}"
    decorators = [ast.unparse(d) for d in props[0].decorator_list]
    assert decorators == ["property"], (
        f"CentralView.stats_view must be a plain read-only property, not {decorators}")
    print("test_the_raw_stats_page_attribute_is_only_touched_where_it_immediately_re_renders OK")


def _flippable_drift_session():
    """The C4 drift session with a `flip` hook: `session({(lap, edge index), …})` plants those
    corner EDGES as interpolated on a FRESH session, so every cache is built under the plant.

    Identical in shape to the helper inside
    `test_every_cross_lap_corner_surface_counts_the_same_cells`; shared here because C5's
    consumers need the same three states and a second copy would drift from the first."""
    from _synthetic import _drift_session, drift_band_laps, drift_noise_laps

    first = drift_noise_laps()
    laps = first + drift_band_laps(t0=float(first[-1]["cols"][0][-1]))

    def session(flip=()):
        s = _drift_session(laps)
        if flip:
            real = s.corners.lap_edge_resolved

            def edges(lap):
                return [False if (lap, e) in flip else ok for e, ok in enumerate(real(lap))]

            s.corners.lap_edge_resolved = edges
            s.corners.lap_corner_resolved = lambda lap: [
                a and b for a, b in zip(edges(lap)[0::2], edges(lap)[1::2], strict=True)]
        return s

    return session


def test_the_ideal_lap_composites_only_over_segments_the_donor_matched():
    """C5 — the ideal lap is a MINIMUM, so an interpolated cell wins it by being wrong.

    `CornerModel.segment_bests` now takes the per-segment minimum over the cells that are both
    admitted (MAX_DONOR_SPAN_DEV) and RESOLVED (`lap_segment_resolved`), and `SegmentBests`
    carries the second mask so a reader can see which. Driven through the real Session on the
    drift fixture: the mask is exactly the model's, the winner of a segment whose donor is planted
    as interpolated moves to the quickest lap that matched it, and the total rises with it.

    Measured on the owner's D24 0060 pair, where 34 of 456 cells are interpolated after #335: the
    winning donor sat on an unmatched boundary in 5 of the 25 segments and the composite read
    65.637 s against 65.864 s over matched cells only — 0.226 s, 0.133 s of it in C7→C8."""
    session = _flippable_drift_session()
    s = session()
    sb = s.corners.segment_bests()
    assert sb is not None and len(sb.bests) == 2 * len(sb.cids) + 1

    def piece_mask(lid) -> list[bool]:
        """Segment j runs between partition edges j−1 and j, the timing line standing in at both
        ends. Derived here from `lap_edge_resolved` ALONE, which predates C5, so check 0 below is
        a statement about the composite's VALUE on any tree rather than about a new accessor."""
        e = s.corners.lap_edge_resolved(lid)
        return [(j == 0 or e[j - 1]) and (j == len(e) or e[j]) for j in range(len(e) + 1)]

    # 0. THE PROPERTY ITSELF: no segment may be WON by a lap that did not match its two
    # boundaries. The fixture is chosen for it — lap 1's C1 exit is interpolated and its C1→C2 is
    # the quickest in the session, so a tree that counts every cell buys 0.167 s off a guess.
    for j in range(len(sb.bests)):
        if sb.bests[j] <= 0.0:
            continue
        for r, lid in enumerate(sb.lap_ids):
            if sb.times[r, j] == sb.bests[j]:
                assert piece_mask(lid)[j], (
                    f"segment {sb.labels[j]} is won by lap {lid} on a boundary it never matched "
                    f"({sb.bests[j]:.3f} s)")

    # 1. The mask IS the model's, row for row — not a second derivation that can drift from it.
    for r, lid in enumerate(sb.lap_ids):
        assert list(sb.resolved[r]) == s.corners.lap_segment_resolved(lid) == piece_mask(lid), lid
        # …and the partition's two end pieces ride on the timing line, which every lap matches.
        edges = s.corners.lap_edge_resolved(lid)
        assert sb.resolved[r][0] == edges[0] and sb.resolved[r][-1] == edges[-1], lid

    # The fixture's own interpolated edge (lap 1, C1's exit) takes C1 and C1→C2 off that lap.
    assert not all(sb.resolved.ravel()), "the drift session no longer carries an interpolated edge"

    # 2. Plant the WINNER of a real segment. Every lap tied for the quickest, or planting one of a
    # tie leaves the minimum exactly where it was and the assertion below measures nothing (the
    # lesson #335 paid for on this same fixture).
    counts = np.asarray(sb.admitted, bool) & np.asarray(sb.resolved, bool)

    def holders(j) -> list[int]:
        """The laps that hold segment j's best TODAY — counted cells only. The fixture's own
        interpolated cell is the column minimum on C1→C2 and is already excluded, so planting the
        raw argmin would plant a lap the composite had stopped reading (it did, and the assertion
        below then measured nothing)."""
        return [sb.lap_ids[r] for r in range(len(sb.lap_ids))
                if counts[r, j] and sb.times[r, j] == sb.bests[j]]

    def runner_up_gap(j) -> float:
        """How far segment j's best has to move once every lap holding it is planted — 0.0 where
        nothing is left to win it. Picking the segment that maximises this keeps the assertion
        below off a tie decided in the fourth decimal."""
        col = np.asarray(sb.times[:, j], float)
        rest = col[counts[:, j] & (col > sb.bests[j])]
        return float(rest.min() - sb.bests[j]) if rest.size else 0.0

    seg = max((j for j in range(len(sb.bests))
               if sb.donors[j] is not None and sb.bests[j] > 0.0), key=runner_up_gap)
    assert runner_up_gap(seg) > 1e-3, "no segment has a runner-up to fall back on"
    tied = holders(seg)
    assert tied, seg
    # Segment j sits between partition edges j-1 and j; edge j-1 is the timing line for j == 0.
    edge = seg if seg == 0 else seg - 1
    planted = session(flip={(lid, edge) for lid in tied})
    after = planted.corners.segment_bests()
    assert all(not after.resolved[after.lap_ids.index(lid)][seg] for lid in tied), tied
    assert after.bests[seg] - sb.bests[seg] > 1e-3, (
        f"segment {sb.labels[seg]}'s best did not move off the laps planted as interpolated: "
        f"{sb.bests[seg]} -> {after.bests[seg]}")
    assert after.donors[seg] not in tied, after.donors[seg]
    assert after.donors[seg] is not None and after.resolved[
        after.lap_ids.index(after.donors[seg])][seg], "the new donor is itself interpolated"
    assert planted.ideal_total() > s.ideal_total(), (planted.ideal_total(), s.ideal_total())

    # 3. …and `admitted` is NOT quietly merged with it: the plan's denominator is documented as a
    # function of the spans alone, and folding resolution in would move 22 of the 25 beat counts
    # on the D24 0060 pair. Same mask as before the plant.
    assert (after.admitted == sb.admitted).all(), "the plant moved the span-admission mask"

    # 4. A segment NO admitted lap matched keeps its column rather than collapsing to inf.
    every = session(flip={(lid, edge) for lid in sb.lap_ids})
    fallback = every.corners.segment_bests()
    assert not fallback.resolved[:, seg].any()
    # It falls back to the whole ADMITTED column — the pre-C5 answer — rather than to inf, which
    # is what a min over an empty set would be and what would poison the total.
    admitted_min = float(np.min(np.asarray(sb.times[:, seg], float)[
        np.asarray(sb.admitted[:, seg], bool)]))
    assert fallback.bests[seg] == admitted_min, (fallback.bests[seg], admitted_min)
    assert math.isfinite(every.ideal_total()) and every.ideal_total() > 0.0
    print(f"ok ideal lap: {sb.labels[seg]} re-won by a lap that matched it "
          f"({sb.bests[seg]:.3f} -> {after.bests[seg]:.3f} s), the mask is the model's, "
          f"admission untouched, and a segment nobody matched still has a number")


def test_coaching_the_brake_points_and_the_line_sigma_count_only_matched_cells():
    """C5 — the three remaining cross-lap corner statistics, through the real Session.

    * COACHING's loss, evidence and reach count a cell only where this lap AND the best lap
      matched the corner: `time_lost` is a difference between the two, so both halves must be
      measured. Measured on the D24 0060 pair the losses move up to 0.045 s and two abstained
      rows swap places; on 0062 (every cell matched) nothing moves.
    * THE LINE SIGMA coaching reads (`Session.corner_consistency`) is the CORNERS table's own σ.
      Counting every cell the two disagreed on 12 of 12 corners of the 0060 pair, by up to
      0.038 s — one quantity, one lap set, two printed numbers.
    * A BRAKE POINT is read inside the projected window (the last onset in [enter − lead, exit],
      and an optimum built from that window's apex), so an interpolated corner contributes no row
      to `_brake_rows` — and therefore none to the BRAKING table, the one surface that
      medianizes that list since L8 retired the coaching habit. At most 0.7 m on 0060, nothing on
      0062."""
    session = _flippable_drift_session()
    s = session()
    ids = s.consistency_lap_ids()
    best = s.best_lap_id()
    corner_list = s.corners.corner_list()

    # σ: the two surfaces agree corner for corner, on the untouched fixture and under a plant.
    for label, sess in (("as loaded", s), ("with C1 planted on lap 2", session(flip={(2, 0)}))):
        spread = {sp.cid: sp.sigma for sp in sess.corner_consistency()}
        table = {r.cid: r.sigma_s for r in sess.corner_report()}
        for c in corner_list:
            assert (c.cid in spread) == (table.get(c.cid) is not None), (label, c.cid)
            if c.cid in spread:
                assert abs(spread[c.cid] - table[c.cid]) < 1e-12, (label, c.cid, spread, table)

    # COACHING: each row's evidence counts exactly the cells both laps matched.
    rows = {r.cid: r for r in s.coaching_opportunities().rows}
    best_res = s.corners.lap_corner_resolved(best)
    for k, c in enumerate(corner_list):
        counted = [i for i in ids
                   if s.corners.lap_corner_resolved(i)[k] and best_res[k]
                   and len(s.corners.lap_corner_stats(i)) == len(corner_list)]
        if c.cid in rows:
            assert rows[c.cid].evidence.n_laps == len(counted), (c.cid, rows[c.cid].evidence)
    assert any(r.evidence.n_laps < len(ids) for r in rows.values()), (
        "no coaching row drops the fixture's interpolated cell", {c: r.evidence.n_laps for c, r in rows.items()})

    # …and a corner the BEST lap did not match has no counted cell anywhere, so it carries no row
    # at all: there is no cell to show and no target to jump to.
    blind = session(flip={(best, 0)})
    assert corner_list[0].cid not in {r.cid for r in blind.coaching_opportunities().rows}, (
        "a corner whose baseline is interpolated still ranks")

    # BRAKING: the corner's row leaves `_brake_rows` on the laps that did not match it.
    braked = {cid: [k for k, row in enumerate(s._brake_rows()) if cid in row]
              for cid in (c.cid for c in corner_list)}
    assert any(braked.values()), "the drift fixture no longer detects a brake point"
    target = next(c.cid for c in corner_list if braked[c.cid])
    k_target = next(k for k, c in enumerate(corner_list) if c.cid == target)
    n_before = next(b.n for b in s.brake_report() if b.cid == target)
    gone = session(flip={(i, 2 * k_target) for i in ids})
    assert all(target not in row for row in gone._brake_rows()), gone._brake_rows()
    assert next((b.n for b in gone.brake_report() if b.cid == target), 0) == 0, (
        f"C{target} kept {n_before} brake rows measured in a window nobody matched")
    print(f"ok coaching + braking: the LINE σ is the CORNERS table's, an unmatched baseline drops "
          f"the row, and C{target}'s {n_before} brake points go with its window")


def test_coaching_names_the_laps_a_corner_counted_when_it_is_not_all_of_them():
    """Lane A, between #339 and the copy it met. Since #339 a coaching row's `evidence.n_laps` is
    the laps whose time through THAT corner counts — matched on track at its entry and exit, on
    the lap and on the best lap — not the session's clean laps. The copy written before it still
    called that number "your clean laps":

      * the "Done it?" hover said "3 of your 16 clean laps" on MK_18_09_26's C2, under a headline
        reading "median of 19 clean laps" — the driver has 19; 3 were interpolated at C2. 7 of the
        11 rows shown there carry a count under 19;
      * the FEW_LAPS abstain said "only 2 clean laps through this corner". MIN_CORNER_LAPS equals
        coaching.MIN_LAPS, so since #339 that abstain fires ONLY because corners went unmatched —
        the sentence is false every time it is shown.

    Driven on the REAL Coaching page over the real Session: the drift fixture's own interpolated
    C1 exit (lap 1) is the MK shape in miniature."""
    from studio import coaching, coaching_panel

    session = _flippable_drift_session()
    s = session()
    total = len(s.consistency_lap_ids())
    opps = s.coaching_opportunities()
    short = {r.cid: r.evidence.n_laps for r in opps.rows if 0 < r.evidence.n_laps < total}
    full = {r.cid: r.evidence.n_laps for r in opps.rows if r.evidence.n_laps == total}
    assert short, ("the fixture no longer leaves a lap out of a coaching row",
                   {r.cid: r.evidence.n_laps for r in opps.rows}, total)

    panel = coaching_panel.OpportunitiesPanel(s)
    panel.resize(1400, 900)
    panel.show()
    _APP.processEvents()
    panel.refresh()
    _APP.processEvents()
    try:
        for where, table, col in (("page", panel.table, coaching_panel._PANEL_COL_REACH),):
            tips = {int(table.item(r, 0).text()[1:]): table.item(r, col).toolTip()
                    for r in range(table.rowCount())}
            for cid, n in short.items():
                tip = tips[cid]
                assert f"of your {n} clean laps" not in tip, (
                    f"{where}: C{cid}'s hover calls the {n} laps matched on track 'your clean laps' "
                    f"— the session has {total}: {tip!r}")
                assert f"{n} of your {total} clean laps" in tip, (where, cid, tip)
            for cid, n in full.items():
                if cid in tips:
                    assert f"of your {n} clean laps" in tips[cid], (where, cid, tips[cid])
    finally:
        panel.close()

    # FEW_LAPS: C2's entry planted as interpolated on every lap but the best and one other, so two
    # cells count and the corner abstains for want of laps it could MATCH, not laps it drove. That
    # is the ONLY way it fires while a summary needs as many clean laps as a corner does — the
    # premise the new sentence and its comment rest on, pinned here.
    assert coaching.MIN_CORNER_LAPS <= coaching.MIN_LAPS, (coaching.MIN_CORNER_LAPS,
                                                          coaching.MIN_LAPS)
    best = s.best_lap_id()
    keep = {best, next(i for i in s.consistency_lap_ids() if i != best)}
    few = session(flip={(i, 2) for i in s.consistency_lap_ids() if i not in keep})
    row = next(r for r in few.coaching_opportunities().rows if r.cid == 2)
    assert row.evidence.abstain == coaching.ABSTAIN_FEW_LAPS and row.evidence.n_laps == 2, row.evidence
    sentence = coaching.abstain_sentence(row)
    assert "only 2 clean laps through this corner" not in sentence, (
        f"C2 was driven on all {total} clean laps and matched on track on 2: {sentence!r}")
    assert "matched on track" in sentence, sentence
    print(f"ok coaching counts: {short} of {total} clean laps say so on the panel and the dialog; "
          f"FEW_LAPS reads {sentence!r}")


def test_braking_coasting_and_the_phase_tiles_say_which_laps_they_count():
    """Lane A. #331 and #339 put the phase split and BRAKING on the matched-only rule, and #339
    left COASTING counting every lap on purpose. The Stats page's copy said none of it:

      * the phase tiles' hover opened "Every clean lap's Δt-vs-best" — since #331 a lap's triple
        counts only where it AND the best lap matched the corner;
      * BRAKING's n dropped the braked laps whose corner was interpolated (MK_18_09_26 C1: n 12,
        three more clean laps braked there) and its hover still read "over the clean laps";
      * COASTING shares the STRAIGHTS table's pieces and says so, but keeps the laps STRAIGHTS
        leaves out — 10.3 % of MK_18_09_26's coasting sits in a piece bounded by an interpolated
        edge — and its hover never said which way it counts.

    Each sentence is checked against the behaviour it describes on the real Session, so the copy
    and the rule cannot drift apart again in either direction."""
    from studio.stats_panel import StatsView

    session = _flippable_drift_session()
    s = session()
    ids = s.consistency_lap_ids()

    # COASTING keeps the lap whose C1 exit is interpolated; STRAIGHTS leaves it out.
    assert s.coast_report().n_laps == len(ids), s.coast_report().n_laps
    assert any(st.n < len(ids) for st in s.straights_report()), [st.n for st in s.straights_report()]
    # BRAKING leaves out a braked lap planted as interpolated at that corner.
    corner_list = s.corners.corner_list()
    lap, k = next((i, k) for i in ids for k, c in enumerate(corner_list)
                  if any(bp.cid == c.cid for bp in s.driving.lap_brake_points(i)))
    cid = corner_list[k].cid
    before = next(b.n for b in s.brake_report() if b.cid == cid)
    planted = session(flip={(lap, 2 * k)})
    after = next((b.n for b in planted.brake_report() if b.cid == cid), 0)
    assert after == before - 1, (cid, before, after)

    # The three hovers are the page's own, fixed at construction — read off the real StatsView (the
    # duck-typed page session: the drift fixture carries no pacer lap/sector stand-in for the rest
    # of the page), against the behaviour measured on the real Session above.
    view = StatsView(_fake_view_session())
    try:
        coast = view.coasting.table.toolTip()
        assert "interpolated" in coast and "STRAIGHTS" in coast and "keeps every clean lap" in coast, (
            "COASTING counts the laps STRAIGHTS leaves out and does not say so", coast)
        braking = view.braking.table.toolTip()
        assert "matched to your best lap's line on track" in braking, (
            f"BRAKING's n left out lap {lap + 1}'s C{cid} brake point and its hover does not say "
            f"why: {braking}")
        phase = view.corners.t_phase.toolTip()
        assert not phase.startswith("Every clean lap's"), phase
        assert "matched on track" in phase, phase
    finally:
        view.hide()
    print(f"ok the Stats copy: COASTING keeps {len(ids)} laps where STRAIGHTS counts fewer, BRAKING "
          f"C{cid} {before} -> {after} under a plant and says why, the phase tiles name their rule")


def test_a_dropped_stats_page_is_freed_by_refcount():
    """A section must never hold its page (ARCH-3). The page holds each section, and each section's
    table signals hold the section — so a section that keeps a reference back to the page (a
    `lambda: self.session`, say) closes a cycle THROUGH Qt, which Python's collector cannot see:
    every StatsView ever built stays alive. Measured on the first CORNERS split, which did exactly
    that: `gc.collect()` did not free the page, and this file later segfaulted inside `_pump`, in
    a test that never touches CORNERS. Checked with the collector OFF, so the page has
    to go by refcount the moment its last reference does, as it did before the split."""
    import gc
    import weakref

    _app()
    from studio.stats_panel import StatsView
    was_enabled = gc.isenabled()
    gc.disable()
    try:
        v = StatsView(_fake_view_session())
        v.refresh()
        page = weakref.ref(v)
        del v
        held = page()
        if held is not None:
            why = [type(r).__name__ for r in gc.get_referrers(held)]
            del held
            raise AssertionError(f"a dropped StatsView is still alive, held by {why}")
    finally:
        if was_enabled:
            gc.enable()
    print("test_a_dropped_stats_page_is_freed_by_refcount OK")


def test_every_report_table_in_a_section_column_is_registered_with_the_packer():
    """A report table is the one thing on this page that cannot yield to a narrow column, so the
    packer asks the tables REGISTERED against a group what width they need (`_group_min_width`) —
    and a section hands the page its tables through `tables()` for exactly that. A table built but
    left out of `tables()` still shows, in a column composed as if it were not there: planting that
    on CORNERS (ARCH-3 slice 3) squeezed its 734 px table into 680 px at the 1420 px dashboard on
    MK_18_09, and no layout test here saw it, because each reads the same registry the plant broke.
    This compares the registry with what the columns actually hold."""
    _app()
    from studio.stats_common import ReportTable
    from studio.stats_panel import StatsView
    v = StatsView(_fake_view_session())
    v.refresh()
    for group, holder in enumerate(v._columns):
        held = {id(t): t for t in holder.findChildren(ReportTable)}
        registered = {id(t) for t in v._column_tables[group]}
        missing = [held[i].horizontalHeaderItem(0).text() for i in held.keys() - registered]
        assert not missing, f"group {group} shows tables the packer cannot see: {missing}"
        assert registered <= held.keys(), f"group {group} registers a table it does not hold"
    print(f"test_every_report_table_in_a_section_column_is_registered_with_the_packer OK — "
          f"{sum(len(g) for g in v._column_tables)} tables in {len(v._columns)} columns")


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
