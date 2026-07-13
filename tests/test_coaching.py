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


# ----------------------------------------------- D2: entry/apex/exit Δt-vs-best decomposition
def _flat_trace(d0: float, d1: float, v_kmh: float, n: int = 200):
    """A constant-speed lap trace over [d0, d1]: (dist, speed_kmh). Constant v makes the time
    integral analytic — time over a span L (m) at v (m/s) is L/v — so the thirds are exact."""
    dist = np.linspace(d0, d1, n)
    return dist, np.full(n, float(v_kmh))


def test_phase_losses_sum_to_total_and_signs():
    """The three thirds telescope to the corner's total Δt-vs-best, and the sign is right:
    a lap slower than best ⇒ positive total; faster ⇒ negative."""
    enter, exit_ = 100.0, 220.0  # a 120 m corner window
    best_dist, best_v = _flat_trace(0.0, 400.0, 80.0)   # best is fast everywhere
    # SLOWER lap: 72 km/h through the window -> positive Δt over every third.
    slow_dist, slow_v = _flat_trace(0.0, 400.0, 72.0)
    pl = K.corner_phase_losses(slow_dist, slow_v, best_dist, best_v, enter, exit_)
    # each third is 40 m: 40/(72/3.6) - 40/(80/3.6) = 40/20 - 40/22.222 = 2.0 - 1.8 = 0.2 s
    for v in pl.as_tuple():
        assert v > 0, ("slower than best must be a positive loss per third", pl)
    assert abs(pl.total - sum(pl.as_tuple())) < 1e-9, "total must equal the sum of the thirds"
    expected_total = (exit_ - enter) / (72.0 / 3.6) - (exit_ - enter) / (80.0 / 3.6)
    assert abs(pl.total - expected_total) < 1e-3, (pl.total, expected_total)
    # FASTER lap ⇒ negative total (each third negative).
    fast_dist, fast_v = _flat_trace(0.0, 400.0, 88.0)
    pf = K.corner_phase_losses(fast_dist, fast_v, best_dist, best_v, enter, exit_)
    assert pf.total < 0 and all(v < 0 for v in pf.as_tuple()), pf
    print(f"ok phases: thirds sum to total; slow⇒+{pl.total:.3f}s, fast⇒{pf.total:.3f}s")


def test_phase_losses_all_on_entry_attributes_to_entry():
    """A lap that loses ALL its time in the entry third (slow there, on-best elsewhere) attributes
    the loss to entry — entry positive, apex/exit ~0, dominant == PHASE_ENTRY."""
    enter, exit_ = 100.0, 220.0  # thirds: [100,140] entry, [140,180] apex, [180,220] exit
    best_dist, best_v = _flat_trace(0.0, 400.0, 80.0)
    # Lap is 60 km/h in the entry third only, matches best (80) elsewhere. Build piecewise.
    n = 600
    lap_dist = np.linspace(0.0, 400.0, n)
    lap_v = np.full(n, 80.0)
    lap_v[(lap_dist >= 100.0) & (lap_dist < 140.0)] = 60.0
    pl = K.corner_phase_losses(lap_dist, lap_v, best_dist, best_v, enter, exit_)
    assert pl.dominant == K.PHASE_ENTRY, pl
    assert pl.entry > 0.0, pl
    # apex/exit are on-best ⇒ ~0 (a tiny residual is just boundary interpolation smear at the
    # speed step, << the entry loss).
    assert abs(pl.apex) < 0.01 and abs(pl.exit) < 0.01, ("apex/exit on-best ~0", pl)
    # entry ≈ the whole loss; the thirds still sum to the total
    assert abs(pl.total - sum(pl.as_tuple())) < 1e-9
    assert pl.entry > 0.9 * pl.total, ("entry holds the loss", pl)
    print(f"ok phases-entry: dominant=entry, entry={pl.entry:.3f}s apex={pl.apex:.3f} "
          f"exit={pl.exit:.3f}")


def test_phase_losses_are_deterministic_and_degenerate_is_zero():
    best_dist, best_v = _flat_trace(0.0, 400.0, 80.0)
    slow_dist, slow_v = _flat_trace(0.0, 400.0, 72.0)
    a = K.corner_phase_losses(slow_dist, slow_v, best_dist, best_v, 100.0, 220.0)
    b = K.corner_phase_losses(slow_dist, slow_v, best_dist, best_v, 100.0, 220.0)
    assert a == b, "corner_phase_losses must be deterministic"
    # a degenerate (exit <= enter) window, and a too-short trace, both ⇒ zero phases
    deg = K.corner_phase_losses(slow_dist, slow_v, best_dist, best_v, 220.0, 220.0)
    assert deg.as_tuple() == (0.0, 0.0, 0.0), deg
    short = K.corner_phase_losses(np.array([1.0]), np.array([10.0]), best_dist, best_v, 100.0, 220.0)
    assert short.as_tuple() == (0.0, 0.0, 0.0), short
    print("ok phases-det: identical across calls; degenerate window/short trace ⇒ zero")


def test_phase_losses_projected_onto_each_laps_own_odometer():
    """The corner window is in the reference (best) odometer; for a lap whose own odometer is
    scaled vs the basis, the window projects onto that lap's odometer (d·lap_total/basis_total) so
    the third boundaries land on the SAME track fraction on both laps. A lap that is 1.05× longer
    in odometer but takes the SAME time per track-fraction (speed scaled 1.05× too) integrates to
    ~0 loss — proving the boundaries are projected, not taken literally."""
    enter, exit_ = 100.0, 220.0
    corner_total = 400.0
    best_dist, best_v = _flat_trace(0.0, 400.0, 80.0)          # best lap == corner basis frame
    # 1.05× longer odometer AND 1.05× faster ⇒ same time over the same track fraction.
    s = 1.05
    lap_total = corner_total * s
    lap_dist, lap_v = _flat_trace(0.0, lap_total, 80.0 * s)
    pl = K.corner_phase_losses(lap_dist, lap_v, best_dist, best_v, enter, exit_,
                               corner_dist_total=corner_total, lap_total=lap_total,
                               best_total=corner_total)
    assert all(abs(v) < 1e-6 for v in pl.as_tuple()), ("projected boundaries ⇒ ~0 loss", pl)
    # Without projection (literal window on the longer-odometer lap) the same trace WOULD register
    # a loss — the window covers a 1.05× longer slice of track. Confirms projection is doing work.
    pl_literal = K.corner_phase_losses(lap_dist, lap_v, best_dist, best_v, enter, exit_)
    assert pl_literal.total < -1e-3, ("literal (unprojected) window differs", pl_literal)
    print(f"ok phases-proj: projected per-lap ⇒ {pl.as_tuple()}; literal ⇒ {pl_literal.total:.3f}s")


def test_summarize_attaches_phase_decomposition():
    """summarize wires the typical-lap vs best speed traces into each row's PhaseLoss, and the
    decomposition is consistent with the row's measured loss sign (slow corner ⇒ positive sum)."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # one corner enter=50, exit=90 (see _corners). Best fast, typical slower over the window.
    best_dist, best_v = _flat_trace(0.0, 200.0, 80.0)
    med_dist, med_v = _flat_trace(0.0, 200.0, 70.0)
    opp = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, median_brake_events=[], best_brake_events=[],
                      median_coast_spans=[], best_coast_spans=[], median_apex_deltas=[-3.0],
                      median_dist=med_dist, median_speed_kmh=med_v,
                      best_dist=best_dist, best_speed_kmh=best_v)
    row = opp.rows[0]
    pl = row.phases
    assert abs(pl.total - sum(pl.as_tuple())) < 1e-9
    assert pl.total > 0, ("typical lap slower than best ⇒ positive Δt", pl)
    # absent traces ⇒ zero phases (back-compat path)
    opp0 = K.summarize(corners, [0, 1, 2, 3], lap_times, times, best,
                       sigmas_by_cid={1: 0.03}, median_brake_events=[], best_brake_events=[],
                       median_coast_spans=[], best_coast_spans=[], median_apex_deltas=[-3.0])
    assert opp0.rows[0].phases.as_tuple() == (0.0, 0.0, 0.0)
    # the dominant-phase clause shows up in the sentence when one third dominates
    flat_sentence = K.reason_sentence(opp0.rows[0])  # no phases -> no clause
    assert "most of it on" not in flat_sentence
    print(f"ok summarize-phases: row Δt={pl.total:.3f}s (thirds {pl.as_tuple()}); absent⇒zero")


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
    s.corners.invalidate_stats()  # drop the deltas computed against the now-different baseline (F1)
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
    from _synthetic import bare_session, reset_corner_caches, reset_driving_caches
    from test_corners import elapsed_for, speed_profile, stadium

    s = bare_session(valid=[0, 1, 2, 3, 4], best=0)
    s._cols_cache = {}
    s._gmeter = SimpleNamespace(has_data=False)  # no g signal -> brake/coast empty
    # F1: corner + driving caches live in the CornerModel / DrivingChannels services now; reset
    # through the service-aware helpers (REAL corner detection; thresholds re-derive None for
    # the no-g meter, so the apex/line signals drive the coaching reasons).
    reset_corner_caches(s)
    reset_driving_caches(s)

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
    corner_list = s.corners.corner_list()
    assert len(corner_list) == 2, corner_list
    # the second corner (the one the slow laps bleed time in) ranks first
    top = opp.rows[0]
    assert top.cid == 2, [(r.cid, round(r.time_lost, 3)) for r in opp.rows]
    # cross-check the time lost == direct median over laps 1-3 of (corner-2 time - best corner-2)
    best_stats = s.corners.lap_corner_stats(0)
    best_c2 = best_stats[1].time
    losses = [s.corners.lap_corner_stats(i)[1].time - best_c2 for i in (1, 2, 3)]
    assert abs(top.time_lost - float(np.median(losses))) < 1e-9, (top.time_lost, losses)
    # no g signal -> the reason falls back to apex (the slow half drops the apex speed) or line
    assert top.reason.kind in (K.REASON_APEX, K.REASON_LINE), top.reason
    print(f"ok session: C{top.cid} ranked first, lost {top.time_lost:.3f}s, "
          f"reason {top.reason.kind}")


def test_session_corner_entry_media_time_projects_onto_best():
    s = _stadium_session()
    corner_list = s.corners.corner_list()
    c2 = corner_list[1]
    best = 0
    t0, _xs, _ys, _sp, cum = s._cols_cache[best]
    total_ref = float(cum[-1])  # best lap is the reference; total_lap == total_ref here
    # project the corner's enter odometer onto the best lap and read its media time
    expected = float(np.interp(c2.enter / total_ref * float(cum[-1]), cum, t0))
    got = s.corners.corner_entry_media_time(best, c2.cid)
    assert got is not None and abs(got - expected) < 1e-6, (got, expected)
    # the entry time is INSIDE the corner window's start, before the apex time
    assert t0[0] <= got <= t0[-1]
    # an unknown cid -> None (no crash)
    assert s.corners.corner_entry_media_time(best, 999) is None
    print(f"ok entry-time: C{c2.cid} entry on best lap = {got:.3f}s (== manual projection)")


def test_session_determinism_across_reloads():
    s = _stadium_session()
    a = s.coaching_opportunities()
    # clear the corner caches (simulate a recompute) and run again — must be identical. F1: the
    # corner caches live in the CornerModel service; invalidate() drops all three (basis, per-lap
    # stats, session bests) so the second call genuinely recomputes from scratch.
    from _synthetic import reset_corner_caches
    reset_corner_caches(s)
    b = s.coaching_opportunities()
    assert a == b, "coaching_opportunities must be deterministic across recomputes"
    print("ok session determinism: identical Opportunities after a cache clear")


def test_session_gate_under_min_laps():
    """A session with only 2 clean laps yields the friendly excluded state."""
    from _synthetic import bare_session, reset_corner_caches, reset_driving_caches
    from test_corners import elapsed_for, speed_profile, stadium

    s = bare_session(valid=[0, 1], best=0)
    s._cols_cache = {}
    s._gmeter = SimpleNamespace(has_data=False)
    reset_corner_caches(s)  # F1: corner + driving caches live in the services now
    reset_driving_caches(s)
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
    # M9: the header's "typical lap N" is the 1-based lap NUMBER (median_lap_id + 1), while the
    # "median of N clean laps" count stays the raw n_laps (a quantity, NOT a lap id). Find the
    # PanelHeader title label and check both.
    from PySide6.QtWidgets import QLabel
    title = next(w for w in dlg.findChildren(QLabel) if w.property("role") == "PanelHeader")
    assert opp.median_lap_id is not None
    assert f"typical lap {opp.median_lap_id + 1}" in title.text(), title.text()
    assert f"median of {opp.n_laps} clean laps" in title.text(), title.text()
    # columns: 0 Corner, 1 Time-lost, 2 ±σ, 3 Phases (D2 cell widget), 4 Reason, 5 Go.
    # row 0: the biggest-loss corner (C1), with the apex sentence + the time-lost format
    assert dlg.table.item(0, 0).text().startswith(f"C{opp.rows[0].cid}")
    assert dlg.table.item(0, 1).text() == f"+{opp.rows[0].time_lost:.2f} s"
    # column 2 is the lap-to-lap consistency σ folded onto the canonical row (Consistency signal)
    assert dlg.table.item(0, 2).text() == f"±{opp.rows[0].reason.sigma:.2f}", dlg.table.item(0, 2).text()
    # column 3 is the D2 entry/apex/exit breakdown widget (no text item there)
    from studio.coaching_panel import PhaseBar
    assert isinstance(dlg.table.cellWidget(0, 3), PhaseBar)
    assert "apex speed" in dlg.table.item(0, 4).text()
    # the Go button routes to jump_to(cid, entry_dist)
    dlg.table.cellWidget(0, 5).click()
    assert calls == [(opp.rows[0].cid, opp.rows[0].entry_dist)], calls
    print(f"ok dialog: {dlg.table.rowCount()} rows, Go -> jump_to{calls[0]}")


def test_brake_point_hint_text():
    """D4: the brake-point hint helper reads metres_later -> a labelled ESTIMATED line; a negligible
    delta (< BRAKE_HINT_MIN_M) -> None (within the estimate's noise)."""
    from studio import theme
    from studio.coaching_panel import BRAKE_HINT_MIN_M, _brake_point_hint
    # The hint carries the shared canonical "(est)" marker (theme.ESTIMATED_MARK) — was a stray
    # "(EST)"; the whole app now spells "estimated" one way for inline chips.
    assert theme.ESTIMATED_MARK == "(est)"
    later = SimpleNamespace(cid=3, metres_later=6.4, actual_brake_dist=78.0,
                            optimal_brake_dist=84.4, a_max_g=0.9)
    assert _brake_point_hint(later) == "Brake ~6 m later into C3 (est)"
    earlier = SimpleNamespace(cid=2, metres_later=-5.0, actual_brake_dist=90.0,
                              optimal_brake_dist=85.0, a_max_g=0.9)
    assert _brake_point_hint(earlier) == "Brake ~5 m earlier into C2 (est)"
    tiny = SimpleNamespace(cid=1, metres_later=0.5, actual_brake_dist=80.0,
                           optimal_brake_dist=80.5, a_max_g=0.9)
    assert abs(0.5) < BRAKE_HINT_MIN_M and _brake_point_hint(tiny) is None
    print("ok D4 hint: 'brake ~N m later/earlier (est)'; negligible -> None")


def test_dialog_shows_brake_point_hint():
    """D4: the OpportunitiesDialog appends the ESTIMATED brake-point line to a row's reason when a
    BrakePoint is supplied for that corner, and leaves rows without one untouched."""
    _qapp()
    from studio.coaching_panel import OpportunitiesDialog
    opp = _populated_opps()
    top_cid = opp.rows[0].cid
    brake_points = {top_cid: SimpleNamespace(cid=top_cid, metres_later=6.0, actual_brake_dist=78.0,
                                             optimal_brake_dist=84.0, a_max_g=0.9)}
    dlg = OpportunitiesDialog(opp, jump_to=None, brake_points=brake_points)
    reason_text = dlg.table.item(0, 4).text()
    assert "Brake ~6 m later" in reason_text and "(est)" in reason_text, reason_text
    # a row WITHOUT a brake point keeps just the reason sentence (no hint appended).
    if dlg.table.rowCount() > 1:
        assert "Brake ~" not in dlg.table.item(1, 4).text()
    print("ok D4 dialog: brake-point hint appended to the matched corner's reason")


def test_dialog_reason_cell_is_not_truncated():
    """The MODAL OpportunitiesDialog's "How to find it" reason cell must show its FULL text (no
    ellipsis clip) at the dialog's default size — the modal carries two extra columns the panel
    lacks (the fixed ~150-px Entry·Apex·Exit PhaseBar + the per-row Jump button) which squeeze the
    stretch reason column. Mirror the panel's #66 test: word-wrap on + the vertical header sizes
    rows to their content, and the dialog default width leaves the reason column real room (a
    genuinely long, 2-line reason grows the row past the old fixed 40-px section)."""
    _qapp()
    from PySide6.QtWidgets import QHeaderView

    from studio.coaching_panel import _COL_REASON, OpportunitiesDialog
    opp = _populated_opps()
    dlg = OpportunitiesDialog(opp, jump_to=None)
    assert dlg.table.wordWrap() is True, "the reason cell must word-wrap, not elide"
    assert (dlg.table.verticalHeader().sectionResizeMode(0)
            == QHeaderView.ResizeToContents), "rows must auto-fit their wrapped content, not clip"
    # At the dialog's default size the stretch reason column must have real room — not a sliver
    # squeezed by the phase bar + Jump button. A sane floor well above the ~40-px truncating width.
    dlg.resize(920, 380)
    dlg.table.resizeColumnsToContents()  # settle the content columns; reason keeps the slack
    assert dlg.table.columnWidth(_COL_REASON) > 200, (
        f"the reason column must have real width, got {dlg.table.columnWidth(_COL_REASON)}px")
    # A genuinely long, two-line reason MUST grow the row past the old fixed 40-px section (which
    # clipped the 2nd line). Set the text directly so the assertion is deterministic.
    long_reason = ("Carry more apex speed here — your typical lap is ~5 km/h slower than your "
                   "best through the slowest point.\nBrake ~4 m later into C2 (est)")
    dlg.table.item(0, _COL_REASON).setText(long_reason)
    dlg.table.resizeRowsToContents()
    assert dlg.table.rowHeight(0) > 40, (
        f"a wrapped 2-line reason must grow the row, not clip: {dlg.table.rowHeight(0)}px")
    print(f"ok dialog: reason cell not truncated (reason col w={dlg.table.columnWidth(_COL_REASON)}px, "
          f"row0 h={dlg.table.rowHeight(0)}px, wrap+auto-height)")


def test_dialog_excluded_state_has_no_table():
    _qapp()
    from studio.coaching_panel import OpportunitiesDialog
    excluded = K.Opportunities(enough=False, n_laps=2, median_lap_id=None, rows=[])
    dlg = OpportunitiesDialog(excluded, jump_to=None)
    assert not hasattr(dlg, "table"), "excluded state must not build the table (friendly message)"
    print("ok dialog: excluded state shows the friendly message, no table, no crash")


# ------------------------------------------- the PERSISTENT top-3 panel (the coaching front-door)
def test_panel_renders_top3_off_a_session():
    """The always-on OpportunitiesPanel reads the session's coaching_opportunities() directly and
    renders the TOP-3 rows (corner · time lost · reason), the SAME data the modal dialog shows. On
    the stadium session (4 clean laps, the far corner losing time) it shows the ranked rows, leads
    with the slow corner, and the body is on the table page (not the excluded state)."""
    _qapp()
    from studio.coaching_panel import PANEL_TOP_N, OpportunitiesPanel
    s = _stadium_session()
    opp = s.coaching_opportunities()
    assert opp.enough and opp.rows, "fixture must produce real opportunities"
    panel = OpportunitiesPanel(s)
    assert panel.body.currentIndex() == 0, "enough laps -> the table page, not the excluded state"
    n = min(len(opp.rows), PANEL_TOP_N)
    assert panel.table.rowCount() == n, (panel.table.rowCount(), n)
    # Row 0 is the top-ranked corner (the far corner, cid 2), with the +time-lost format.
    assert panel.table.item(0, 0).text().startswith(f"C{opp.rows[0].cid}")
    assert panel.table.item(0, 1).text() == f"+{opp.rows[0].time_lost:.2f} s"
    # col 2: the lap-to-lap consistency σ folded onto the canonical row (the same as the dialog).
    assert panel.table.item(0, 2).text() == f"±{opp.rows[0].reason.sigma:.2f}", panel.table.item(0, 2).text()
    assert coaching_module_reason(panel, opp), "the reason cell must carry the coaching sentence"
    # A row click emits the corner cid (the map-ring consumer); selecting row 0 -> rows[0].cid.
    got = []
    panel.corner_clicked.connect(lambda c: got.append(c))
    panel.table.selectRow(0)
    assert got and got[-1] == opp.rows[0].cid, got
    print(f"ok panel: top-{n} rows, C{opp.rows[0].cid} first, row-click emits cid")


def test_panel_reason_cell_is_not_truncated():
    """The persistent panel's "How to find it" reason cell must show its FULL text (no ellipsis
    clip) even at a narrow panel width: word-wrap is on AND the vertical header sizes rows to their
    content, so a wrapped 2nd line grows the row instead of being cut off (the truncation bug). We
    assert the layout invariants (word-wrap + ResizeToContents rows) and that a squeezed panel still
    gives the reason row height for its multi-line wrapped sentence."""
    _qapp()
    from PySide6.QtWidgets import QHeaderView

    from studio.coaching_panel import OpportunitiesPanel
    s = _stadium_session()
    panel = OpportunitiesPanel(s)
    assert panel.table.wordWrap() is True, "the reason cell must word-wrap, not elide"
    assert (panel.table.verticalHeader().sectionResizeMode(0)
            == QHeaderView.ResizeToContents), "rows must auto-fit their wrapped content, not clip"

    # A genuinely long, two-line "How to find it" reason at a narrow (1512-px-laptop) panel width:
    # the stretch column squeezes, the sentence wraps, and the row MUST grow past the old fixed
    # 34-px section (which clipped the 2nd line). Set the long text directly so the assertion is
    # deterministic regardless of the fixture's exact reason wording.
    panel.resize(360, panel.height())
    long_reason = ("Carry more apex speed here — your typical lap is ~5 km/h slower than your "
                   "best through the slowest point.\nBrake ~4 m later into C2 (est)")
    panel.table.item(0, 3).setText(long_reason)
    panel.table.resizeRowsToContents()
    assert panel.table.rowHeight(0) > 34, (
        f"a wrapped 2-line reason must grow the row, not clip: {panel.table.rowHeight(0)}px")
    print(f"ok panel: reason cell not truncated (row0 h={panel.table.rowHeight(0)}px, "
          f"wrap+auto-height)")


def coaching_module_reason(panel, opp) -> bool:
    """The panel's reason cell (col 3, after σ was folded in at col 2) shows the same coaching
    sentence the dialog renders."""
    return K.reason_sentence(opp.rows[0])[:12] in panel.table.item(0, 3).text()


def test_panel_shows_need_more_laps_state_not_empty_box():
    """Under MIN_LAPS clean laps the panel shows the FRIENDLY 'drive more laps' message (the
    excluded page), NOT an empty table — the same no-table state the dialog uses."""
    _qapp()
    from studio.coaching_panel import OpportunitiesPanel
    # The <MIN_LAPS fixture (2 clean laps) -> coaching_opportunities().enough is False.
    s = _gate_session()
    assert s.coaching_opportunities().enough is False
    panel = OpportunitiesPanel(s)
    assert panel.body.currentIndex() == 1, "too few laps -> the excluded (friendly) page"
    assert panel.table.rowCount() == 0, "the excluded state must not fill the table"
    msg = panel.empty_label.text().lower()
    assert "clean" in msg and "lap" in msg, msg
    assert str(K.MIN_LAPS) in panel.empty_label.text(), "the friendly state names the lap minimum"
    print("ok panel: <MIN_LAPS -> friendly need-more-laps state, no empty box")


def test_panel_refresh_swaps_between_states():
    """refresh() recomputes from the session: a panel built on a too-few-laps session shows the
    excluded page, then re-pointing it at a full session + refresh() swaps to the top-3 table (the
    re-segmentation path the central view drives)."""
    _qapp()
    from studio.coaching_panel import OpportunitiesPanel
    panel = OpportunitiesPanel(_gate_session())
    assert panel.body.currentIndex() == 1
    panel.session = _stadium_session()
    panel.refresh()
    assert panel.body.currentIndex() == 0, "refresh must surface the rows once the laps qualify"
    assert panel.table.rowCount() >= 1
    print("ok panel: refresh swaps excluded <-> populated off the live session")


def _gate_session():
    """The <MIN_LAPS (2 clean laps) bare stadium session — coaching_opportunities() is excluded."""
    from _synthetic import bare_session, reset_corner_caches, reset_driving_caches
    from test_corners import elapsed_for, speed_profile, stadium

    s = bare_session(valid=[0, 1], best=0)
    s._cols_cache = {}
    s._gmeter = SimpleNamespace(has_data=False)
    reset_corner_caches(s)
    reset_driving_caches(s)
    xs, ys, cum = stadium()
    for lid, ph, base in ((0, 0.7, 100.0), (1, 2.1, 300.0)):
        sp = speed_profile(cum, ph)
        t = base + elapsed_for(cum, sp)
        s._cols_cache[lid] = (t, xs, ys, sp, cum)
    lt = {i: float(s._cols_cache[i][0][-1] - s._cols_cache[i][0][0]) for i in (0, 1)}
    s.laps = SimpleNamespace(lap_time=lambda i: lt[i],
                             sectors=SimpleNamespace(sector_lines=[]), laps_count=lambda: 2)
    return s


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} COACHING TESTS PASSED")
