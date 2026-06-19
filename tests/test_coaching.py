"""Synthetic unit tests for studio.coaching + the OpportunitiesDialog (F10).

The coaching summary must be DETERMINISTIC and EXPLAINABLE — numbers only, no ML/randomness.
The tests assert, on engineered inputs where the answer is known by construction:

  * the per-corner ranking is by MEDIAN time lost vs the best lap (biggest first);
  * the dominant-reason selection picks the right cause: a planted apex-speed deficit ⇒ the
    APEX reason; a planted late-throttle coast the best lap lacks ⇒ the COASTING reason; a
    planted earlier/longer brake ⇒ the BRAKING reason; pure cross-lap spread ⇒ the LINE reason;
  * DETERMINISM: summarize() called twice on the same inputs is byte-identical;
  * the <MIN_LAPS gate returns the friendly excluded state (enough=False, no rows, no crash).

The Session wiring runs on a bare Session (tests/_synthetic + test_corners' stadium idiom — no
pacer Laps, no telemetry file): coaching_opportunities() ranks the planted slow corner first and
corner_entry_media_time projects the corner entry onto the best lap exactly. The dialog runs
offscreen on the real dataclasses: populate, Go→jump_to(cid, entry_dist), the excluded state.
Run:  QT_QPA_PLATFORM=offscreen python tests/test_coaching.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from studio import coaching as K  # noqa: E402
from studio.corners import Corner  # noqa: E402


def _corners(n: int) -> list[Corner]:
    """n corners spaced 100 m apart, alternating direction (the cid/enter/exit/apex/direction
    the model reads — turn_deg is irrelevant to coaching)."""
    return [Corner(cid=i + 1, enter=100.0 * i + 50, exit=100.0 * i + 90,
                   apex=100.0 * i + 70, direction=(1 if i % 2 else -1), turn_deg=90.0)
            for i in range(n)]


def _brake(onset_dist, onset_time=0.0, peak=0.8, duration=0.5):
    return SimpleNamespace(onset_dist=float(onset_dist), onset_time=float(onset_time),
                           peak_decel=float(peak), duration=float(duration))


def _coast(start_dist, end_dist, duration=0.6):
    return SimpleNamespace(start_dist=float(start_dist), end_dist=float(end_dist),
                           duration=float(duration))


# ------------------------------------------------------------------ median-lap selection
def test_median_lap_id_is_deterministic_lower_of_two():
    # odd count: the true median-TIME lap. times {68,70,71} -> median 70 -> its id (3).
    assert K.median_lap_id([3, 7, 1], [70.0, 68.0, 71.0]) == 3
    # even count: the LOWER-middle (deterministic, no averaging). sorted times [68,69,70,71];
    # the lower of the two central (69,70) is 69 -> its id (1).
    assert K.median_lap_id([0, 1, 2, 3], [70.0, 69.0, 71.0, 68.0]) == 1
    assert K.median_lap_id([], []) is None
    print("ok median-lap: median time, lower-of-two for even n, None for empty")


# ----------------------------------------------------------------------- ranking
def test_ranking_is_by_median_time_lost_biggest_first():
    corners = _corners(4)
    best = [5.0, 6.0, 7.0, 4.0]
    # Per-lap per-corner times: C2 loses ~0.8 s, C0 ~0.3, C3 ~0.1, C1 ~0.0 (typical).
    rng = np.random.default_rng(0)
    losses_plan = [0.30, 0.00, 0.80, 0.10]
    times = []
    for _ in range(5):
        times.append([best[j] + losses_plan[j] + rng.normal(0, 0.01) for j in range(4)])
    lap_times = [sum(r) for r in times]
    opp = K.summarize(corners, [0, 1, 2, 3, 4], lap_times, times, best,
                      sigmas_by_cid={}, median_brake_events=[], best_brake_events=[],
                      median_coast_spans=[], best_coast_spans=[], median_apex_deltas=[0, 0, 0, 0])
    assert opp.enough and opp.median_lap_id is not None
    cids = [r.cid for r in opp.rows]
    # ranked by median loss: C3(cid 3) biggest, then C1(cid 1), then C4(cid 4); C2(cid 2) ~0 is
    # dropped (no positive loss).
    assert cids[0] == 3 and cids[1] == 1 and cids[2] == 4, cids
    assert 2 not in cids, "a corner with ~0 median loss is not an opportunity"
    # the losses are monotonic non-increasing
    losses = [r.time_lost for r in opp.rows]
    assert losses == sorted(losses, reverse=True), losses
    print(f"ok ranking: {[(r.cid, round(r.time_lost, 2)) for r in opp.rows]} biggest-first")


# --------------------------------------------------------------- reason selection
def _one_corner_lossy(loss=0.5):
    """A single-corner setup losing `loss` s on every candidate lap, with NO apex/brake/coast
    signal by default — the per-test planting flips exactly one signal on so it must dominate."""
    corners = _corners(1)
    best = [5.0]
    times = [[5.0 + loss] for _ in range(4)]
    lap_times = [r[0] for r in times]
    return corners, best, times, lap_times


def test_apex_deficit_picks_apex_reason():
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # the median lap is 5 km/h DOWN at the apex vs best — a clear apex-speed deficit
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, median_brake_events=[], best_brake_events=[],
                      median_coast_spans=[], best_coast_spans=[], median_apex_deltas=[-5.0])
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_APEX, r.reason
    assert abs(r.reason.apex_speed_deficit - 5.0) < 1e-9
    assert "apex speed" in K.reason_sentence(r) and "5.0 km/h" in K.reason_sentence(r)
    print(f"ok apex reason: {K.reason_sentence(r)} (contrib {r.reason.contribution:.2f}s)")


def test_coasting_picks_coasting_reason():
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # a coast INSIDE the corner window [50,90] the best lap does NOT have, and NO apex deficit
    med_coast = [_coast(60.0, 80.0, duration=0.6)]
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, median_brake_events=[], best_brake_events=[],
                      median_coast_spans=med_coast, best_coast_spans=[], median_apex_deltas=[0.0])
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_COASTING, r.reason
    assert abs(r.reason.coast_extra_s - 0.6) < 1e-9
    assert "throttle sooner" in K.reason_sentence(r)
    print(f"ok coasting reason: {K.reason_sentence(r)} (contrib {r.reason.contribution:.2f}s)")


def test_braking_picks_braking_reason():
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # median lap brakes LONGER in the corner's approach [50-30, 90] than best (0.9 s vs 0.3 s),
    # no apex deficit, no coast
    med_brakes = [_brake(onset_dist=30.0, duration=0.9)]   # within [20, 90]
    best_brakes = [_brake(onset_dist=40.0, duration=0.3)]
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, median_brake_events=med_brakes,
                      best_brake_events=best_brakes, median_coast_spans=[], best_coast_spans=[],
                      median_apex_deltas=[0.0])
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_BRAKING, r.reason
    assert abs(r.reason.brake_extra_s - 0.6) < 1e-9  # 0.9 - 0.3
    assert "brake later" in K.reason_sentence(r)
    print(f"ok braking reason: {K.reason_sentence(r)} (contrib {r.reason.contribution:.2f}s)")


def test_line_sigma_is_the_fallback_reason():
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # no apex/brake/coast signal at all, but real cross-lap spread -> LINE
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                      sigmas_by_cid={1: 0.20}, median_brake_events=[], best_brake_events=[],
                      median_coast_spans=[], best_coast_spans=[], median_apex_deltas=[0.0])
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_LINE, r.reason
    assert abs(r.reason.sigma - 0.20) < 1e-9
    assert "consistent" in K.reason_sentence(r)
    print(f"ok line reason (fallback): {K.reason_sentence(r)}")


def test_dominant_reason_is_the_largest_contribution():
    """With BOTH an apex deficit AND a coast present, the one with the larger seconds-of-loss
    contribution wins — here a big coast (0.6 s) beats a tiny apex deficit (0.3 km/h)."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    med_coast = [_coast(60.0, 80.0, duration=0.6)]
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, median_brake_events=[], best_brake_events=[],
                      median_coast_spans=med_coast, best_coast_spans=[], median_apex_deltas=[-0.3])
    assert opp.rows[0].reason.kind == K.REASON_COASTING, opp.rows[0].reason
    # and a big apex deficit beats a tiny coast
    med_coast_small = [_coast(60.0, 62.0, duration=0.05)]
    opp2 = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                       sigmas_by_cid={1: 0.03}, median_brake_events=[], best_brake_events=[],
                       median_coast_spans=med_coast_small, best_coast_spans=[],
                       median_apex_deltas=[-8.0])
    assert opp2.rows[0].reason.kind == K.REASON_APEX, opp2.rows[0].reason
    print("ok dominant: largest seconds-of-loss contribution wins (coast vs apex both ways)")


def test_brake_approach_window_and_coast_only_when_best_lacks_it():
    """A brake/coast that the BEST lap matches is NOT a loss (the difference is what counts);
    and a brake event OUTSIDE the corner approach window is ignored."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # identical brake on both laps -> brake contribution 0 (falls back to line)
    same_brake = [_brake(onset_dist=35.0, duration=0.7)]
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best, sigmas_by_cid={1: 0.10},
                      median_brake_events=same_brake, best_brake_events=same_brake,
                      median_coast_spans=[], best_coast_spans=[], median_apex_deltas=[0.0])
    assert opp.rows[0].reason.kind == K.REASON_LINE, opp.rows[0].reason
    # a brake far before the approach window (outside [enter-30, exit]) is ignored
    far_brake = [_brake(onset_dist=-100.0, duration=2.0)]
    opp2 = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best, sigmas_by_cid={1: 0.10},
                       median_brake_events=far_brake, best_brake_events=[],
                       median_coast_spans=[], best_coast_spans=[], median_apex_deltas=[0.0])
    assert opp2.rows[0].reason.kind == K.REASON_LINE, opp2.rows[0].reason
    print("ok windows: matched brake/coast not a loss; out-of-window brake ignored")


# ----------------------------------------------------------------------- determinism
def test_summarize_is_deterministic():
    corners = _corners(4)
    best = [5.0, 6.0, 7.0, 4.0]
    rng = np.random.default_rng(3)
    times = [[best[j] + (0.4 if j == 2 else 0.1) + rng.normal(0, 0.02) for j in range(4)]
             for _ in range(6)]
    lap_times = [sum(r) for r in times]
    kw = dict(sigmas_by_cid={1: 0.1, 2: 0.2, 3: 0.05, 4: 0.05},
              median_brake_events=[_brake(220.0, duration=0.7)], best_brake_events=[],
              median_coast_spans=[_coast(260.0, 280.0)], best_coast_spans=[],
              median_apex_deltas=[-1.0, 0.0, -3.0, 0.0])
    a = K.summarize(corners, [0, 1, 2, 3, 4, 5], lap_times, times, best, **kw)
    b = K.summarize(corners, [0, 1, 2, 3, 4, 5], lap_times, times, best, **kw)
    assert a == b, "summarize must be byte-identical across calls (determinism)"
    print("ok determinism: identical Opportunities across two calls")


# ----------------------------------------------------------------------- gates
def test_too_few_laps_is_friendly_excluded_state():
    corners = _corners(3)
    best = [5.0, 6.0, 7.0]
    times = [[5.1, 6.1, 7.1], [5.2, 6.0, 7.3]]  # only 2 laps < MIN_LAPS
    lap_times = [sum(r) for r in times]
    opp = K.summarize(corners, [0, 1], lap_times, times, best, sigmas_by_cid={},
                      median_brake_events=[], best_brake_events=[], median_coast_spans=[],
                      best_coast_spans=[], median_apex_deltas=[0, 0, 0])
    assert opp.enough is False and opp.rows == [] and opp.n_laps == 2
    print("ok gate: < MIN_LAPS -> enough=False, no rows, no crash")


def test_no_corners_or_no_loss_excluded():
    # no corners
    opp = K.summarize([], [0, 1, 2], [70, 71, 72], [[], [], []], [], {}, [], [], [], [], [])
    assert opp.enough is False and opp.rows == []
    # enough laps + corners but NO corner loses time -> enough=True but no rows (dialog shows the
    # "nice driving" empty state)
    corners = _corners(2)
    best = [5.0, 6.0]
    times = [[5.0, 6.0], [5.0, 6.0], [5.0, 6.0]]  # the typical lap matches best everywhere
    opp2 = K.summarize(corners, [0, 1, 2], [11.0, 11.0, 11.0], times, best, {}, [], [], [], [],
                       [0.0, 0.0])
    assert opp2.enough is True and opp2.rows == []
    print("ok gate: no corners -> excluded; no loss -> enough but empty rows")


# ---------------------------------------- D13: coaching row halves share ONE baseline (local best)
def test_brake_window_projected_onto_each_laps_own_odometer():
    """D13 (odometer-frame): a corner's [enter, exit] is in the BEST-lap (reference) odometer, but
    each lap's brake events live in its OWN odometer. summarize() must project the window onto each
    lap's own odometer before matching. Here the corner is [50, 90] in a 1000 m reference frame; the
    median lap is 1100 m long, so its window is [55, 99]. A median brake at onset 96 m (inside the
    PROJECTED [55-30, 99] window, but OUTSIDE the un-projected [50-30, 90]) must count as braking —
    proving the projection happened. Without the fix the brake would fall outside and pick LINE."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)  # one corner: enter 50, exit 90
    med_brakes = [_brake(onset_dist=96.0, duration=0.9)]   # in projected [25, 99], not raw [20, 90]
    best_brakes = [_brake(onset_dist=40.0, duration=0.3)]  # best frame == reference frame here
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, median_brake_events=med_brakes,
                      best_brake_events=best_brakes, median_coast_spans=[], best_coast_spans=[],
                      median_apex_deltas=[0.0],
                      corner_dist_total=1000.0, median_lap_total=1100.0, best_lap_total=1000.0)
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_BRAKING, r.reason
    assert abs(r.reason.brake_extra_s - 0.6) < 1e-9  # 0.9 - 0.3
    # control: the SAME inputs WITHOUT the totals (identity projection) leave the brake outside the
    # un-projected window -> it does NOT count -> the row falls back to LINE.
    opp0 = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                       sigmas_by_cid={1: 0.03}, median_brake_events=med_brakes,
                       best_brake_events=best_brakes, median_coast_spans=[], best_coast_spans=[],
                       median_apex_deltas=[0.0])
    assert opp0.rows[0].reason.kind == K.REASON_LINE, opp0.rows[0].reason
    print("ok D13 odometer-frame: corner window projected onto each lap's own odometer for braking")


def _stadium_reference(s, *, apex_scale):
    """Build a ReferenceLap for the stadium session whose speed profile is `apex_scale`× the best
    lap's — so its per-corner APEX speeds differ from the local best's. If the apex signal followed
    the reference (the D13 bug) loading this would CHANGE the reported apex deficit; the fix keeps
    it pinned to the local best, so the deficit is identical with and without the reference."""
    from studio import cross_reference
    t0, _xs, _ys, sp0, cum = s._cols_cache[0]  # the best lap (0)
    dist = np.asarray(cum, float)
    speed_kmh = np.asarray(sp0, float) * 3.6 * apex_scale
    elapsed = np.asarray(t0, float) - float(t0[0])
    return cross_reference.ReferenceLap(
        dist=dist, speed_kmh=speed_kmh, elapsed=elapsed, total_time=float(elapsed[-1]),
        source_label="ref", lap_id=0, overlay_xy=None, map_fit_rms=None,
    )


def test_apex_signal_and_loss_share_local_best_baseline_under_reference():
    """D13 (apex baseline): with a CROSS-RECORDING reference loaded, the per-corner Δ baseline for
    the lap table switches to the reference — but the coaching loss is still vs the LOCAL best, so
    the apex SIGNAL must stay vs the local best too (both halves of a row on ONE baseline). Assert
    the reported apex deficit is IDENTICAL with and without a reference whose apex speeds differ."""
    s = _stadium_session()
    base = s.coaching_opportunities()  # no reference: apex deficit measured vs local best
    base_apex = {r.cid: r.reason.apex_speed_deficit for r in base.rows}
    # Load a reference whose apex speeds are 10% lower than the local best's (so the OLD code, which
    # measured the median's apex vs the reference, would report a DIFFERENT — smaller — deficit).
    s._reference = _stadium_reference(s, apex_scale=0.90)
    s._corner_stats_cache.clear()  # drop the deltas computed against the now-different baseline
    with_ref = s.coaching_opportunities()
    with_ref_apex = {r.cid: r.reason.apex_speed_deficit for r in with_ref.rows}
    assert base_apex == with_ref_apex, (base_apex, with_ref_apex)
    # and the losses (the OTHER half of the row) are also unchanged — both halves on the local best.
    base_loss = {r.cid: round(r.time_lost, 9) for r in base.rows}
    ref_loss = {r.cid: round(r.time_lost, 9) for r in with_ref.rows}
    assert base_loss == ref_loss, (base_loss, ref_loss)
    print(f"ok D13 apex baseline: apex deficit + loss unchanged by a reference {base_apex}")


# ------------------------------------------------------------------- Session wiring
def _stadium_session():
    """Bare Session (test_corners stadium idiom): 4 clean laps that all lose time in the SAME
    corner vs the best lap, plus a 5th dropout lap that must be EXCLUDED. The best lap (0) is the
    fastest; laps 1-3 are slower THROUGH ONE CORNER by construction (a slower speed profile only
    on the second half of the lap, where corner 2 lives)."""
    from _synthetic import bare_session
    from test_corners import elapsed_for, speed_profile, stadium

    from studio.session import _UNSET
    s = bare_session(valid=[0, 1, 2, 3, 4], best=0)
    s._cols_cache = {}
    s._corner_cache = _UNSET
    s._corner_stats_cache = {}
    s._corner_bests = _UNSET
    s._driving_thresholds_cache = None      # no g signal -> brake/coast empty (apex/line drive)
    s._brake_events_cache = {}
    s._coasting_spans_cache = {}
    s._corner_grip_cache = {}
    s._gmeter = SimpleNamespace(has_data=False)

    xs, ys, cum = stadium()
    # best lap: fast everywhere
    sp0 = speed_profile(cum, 0.7)
    t0 = 100.0 + elapsed_for(cum, sp0)
    s._cols_cache[0] = (t0, xs, ys, sp0, cum)
    # laps 1-3: same line, but SLOWER in the second half (the second corner, ~[294,...]) — a
    # multiplicative slowdown on the far half drops the apex speed there and costs time.
    lap_times = {0: float(t0[-1] - t0[0])}
    for lid, base_t in ((1, 300.0), (2, 460.0), (3, 620.0)):
        sp = speed_profile(cum, 0.7).copy()
        sp[cum > 0.55 * cum[-1]] *= 0.80   # 20% slower on the far half -> loses time in corner 2
        t = base_t + elapsed_for(cum, sp)
        s._cols_cache[lid] = (t, xs, ys, sp, cum)
        lap_times[lid] = float(t[-1] - t[0])
    # lap 4: a DROPOUT lap (interior time gap > gapfill threshold) — must be excluded
    sp4 = speed_profile(cum, 2.1)
    t4 = 800.0 + elapsed_for(cum, sp4)
    t4[len(t4) // 2:] += 1.0
    s._cols_cache[4] = (t4, xs, ys, sp4, cum)
    lap_times[4] = float(t4[-1] - t4[0])

    s.laps = SimpleNamespace(lap_time=lambda i: lap_times[i],
                             sectors=SimpleNamespace(sector_lines=[]),
                             laps_count=lambda: 5)
    return s


def test_session_coaching_opportunities_ranks_the_slow_corner():
    s = _stadium_session()
    # the dropout lap (4) is excluded from the consistency set
    assert s.consistency_lap_ids() == [0, 1, 2, 3]
    opp = s.coaching_opportunities()
    assert opp.enough is True, opp
    assert opp.rows, "expected at least one opportunity"
    corner_list = s.corners()
    assert len(corner_list) == 2, corner_list
    # the second corner (the one the slow laps bleed time in) ranks first
    top = opp.rows[0]
    assert top.cid == 2, [(r.cid, round(r.time_lost, 3)) for r in opp.rows]
    # cross-check the time lost == direct median over laps 1-3 of (corner-2 time - best corner-2)
    best_stats = s.lap_corner_stats(0)
    best_c2 = best_stats[1].time
    losses = [s.lap_corner_stats(i)[1].time - best_c2 for i in (1, 2, 3)]
    assert abs(top.time_lost - float(np.median(losses))) < 1e-9, (top.time_lost, losses)
    # no g signal -> the reason falls back to apex (the slow half drops the apex speed) or line
    assert top.reason.kind in (K.REASON_APEX, K.REASON_LINE), top.reason
    print(f"ok session: C{top.cid} ranked first, lost {top.time_lost:.3f}s, "
          f"reason {top.reason.kind}")


def test_session_corner_entry_media_time_projects_onto_best():
    s = _stadium_session()
    corner_list = s.corners()
    c2 = corner_list[1]
    best = 0
    t0, _xs, _ys, _sp, cum = s._cols_cache[best]
    total_ref = float(cum[-1])  # best lap is the reference; total_lap == total_ref here
    # project the corner's enter odometer onto the best lap and read its media time
    expected = float(np.interp(c2.enter / total_ref * float(cum[-1]), cum, t0))
    got = s.corner_entry_media_time(best, c2.cid)
    assert got is not None and abs(got - expected) < 1e-6, (got, expected)
    # the entry time is INSIDE the corner window's start, before the apex time
    assert t0[0] <= got <= t0[-1]
    # an unknown cid -> None (no crash)
    assert s.corner_entry_media_time(best, 999) is None
    print(f"ok entry-time: C{c2.cid} entry on best lap = {got:.3f}s (== manual projection)")


def test_session_determinism_across_reloads():
    s = _stadium_session()
    a = s.coaching_opportunities()
    # clear the per-lap caches (simulate a recompute) and run again — must be identical
    s._corner_stats_cache.clear()
    from studio.session import _UNSET
    s._corner_cache = _UNSET
    s._corner_bests = _UNSET
    b = s.coaching_opportunities()
    assert a == b, "coaching_opportunities must be deterministic across recomputes"
    print("ok session determinism: identical Opportunities after a cache clear")


def test_session_gate_under_min_laps():
    """A session with only 2 clean laps yields the friendly excluded state."""
    from _synthetic import bare_session
    from test_corners import elapsed_for, speed_profile, stadium

    from studio.session import _UNSET
    s = bare_session(valid=[0, 1], best=0)
    s._cols_cache = {}
    s._corner_cache = _UNSET
    s._corner_stats_cache = {}
    s._corner_bests = _UNSET
    s._gmeter = SimpleNamespace(has_data=False)
    s._driving_thresholds_cache = None
    s._brake_events_cache, s._coasting_spans_cache, s._corner_grip_cache = {}, {}, {}
    xs, ys, cum = stadium()
    for lid, ph, base in ((0, 0.7, 100.0), (1, 2.1, 300.0)):
        sp = speed_profile(cum, ph)
        t = base + elapsed_for(cum, sp)
        s._cols_cache[lid] = (t, xs, ys, sp, cum)
    lt = {i: float(s._cols_cache[i][0][-1] - s._cols_cache[i][0][0]) for i in (0, 1)}
    s.laps = SimpleNamespace(lap_time=lambda i: lt[i],
                             sectors=SimpleNamespace(sector_lines=[]), laps_count=lambda: 2)
    opp = s.coaching_opportunities()
    assert opp.enough is False and opp.rows == [], opp
    print("ok session gate: 2 clean laps -> enough=False, no crash")


# ----------------------------------------------------------------------- UI (offscreen)
def _qapp():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _populated_opps():
    corners = _corners(4)
    best = [5.0, 6.0, 7.0, 4.0]
    times = [[best[j] + (0.6 if j == 0 else 0.05) for j in range(4)] for _ in range(4)]
    lap_times = [sum(r) for r in times]
    return K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                       sigmas_by_cid={1: 0.05, 2: 0.02, 3: 0.02, 4: 0.02},
                       median_brake_events=[], best_brake_events=[], median_coast_spans=[],
                       best_coast_spans=[], median_apex_deltas=[-5.0, 0.0, 0.0, 0.0])


def test_dialog_populates_and_go_calls_jump_to():
    _qapp()
    from studio.coaching_panel import OpportunitiesDialog
    opp = _populated_opps()
    calls = []
    dlg = OpportunitiesDialog(opp, jump_to=lambda c, d: calls.append((c, d)))
    assert dlg.table.rowCount() == len(opp.rows)
    # row 0: the biggest-loss corner (C1), with the apex sentence + the time-lost format
    assert dlg.table.item(0, 0).text().startswith(f"C{opp.rows[0].cid}")
    assert dlg.table.item(0, 1).text() == f"+{opp.rows[0].time_lost:.2f} s"
    assert "apex speed" in dlg.table.item(0, 2).text()
    # the Go button routes to jump_to(cid, entry_dist)
    dlg.table.cellWidget(0, 3).click()
    assert calls == [(opp.rows[0].cid, opp.rows[0].entry_dist)], calls
    print(f"ok dialog: {dlg.table.rowCount()} rows, Go -> jump_to{calls[0]}")


def test_dialog_excluded_state_has_no_table():
    _qapp()
    from studio.coaching_panel import OpportunitiesDialog
    excluded = K.Opportunities(enough=False, n_laps=2, median_lap_id=None, rows=[])
    dlg = OpportunitiesDialog(excluded, jump_to=None)
    assert not hasattr(dlg, "table"), "excluded state must not build the table (friendly message)"
    print("ok dialog: excluded state shows the friendly message, no table, no crash")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} COACHING TESTS PASSED")
