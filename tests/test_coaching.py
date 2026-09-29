"""Synthetic unit tests for studio.coaching + the Coaching page (F10).

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
import math
import os
import re
import sys
from types import SimpleNamespace

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

from studio import coaching as K  # noqa: E402
from studio import corners as corners_mod  # noqa: E402
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


def _levers(times, best, *, brake=None, coast=None, apex=None,
            best_brake=None, best_coast=None, best_apex=None, exit=None, best_exit=None) -> dict:
    """summarize's per-lap lever inputs (ADV-1) for a fixture shaped like `_plan_times`: every lap
    SLOWER than the best through a corner carries that corner's planted cell (`brake` / `coast`
    seconds, `apex` km/h), and every lap ON the baseline carries the best lap's own (`best_*`;
    0 s and 100 km/h by default). The laps' habit — the median over the cells of lap − best — is
    then the planted difference wherever the slow laps are the majority. A scalar applies to every
    corner; None means "the same as the best lap". `exit` / `best_exit` (km/h, the line signature's
    second half, COACHING-5) are passed only when either is given."""
    n = len(best)

    def row(v, default):
        if v is None:
            return list(default)
        return [float(v)] * n if np.isscalar(v) else [float(x) for x in v]

    bb, bc = row(best_brake, [0.0] * n), row(best_coast, [0.0] * n)
    ba = row(best_apex, [100.0] * n)
    lb, lc, la = row(brake, bb), row(coast, bc), row(apex, ba)

    def per_lap(slow, base):
        return [[slow[j] if t[j] > best[j] else base[j] for j in range(n)] for t in times]

    out = dict(brake_time_by_lap=per_lap(lb, bb), coast_time_by_lap=per_lap(lc, bc),
               apex_by_lap=per_lap(la, ba), best_brake_time=bb, best_coast_time=bc,
               best_apex=ba)
    if exit is not None or best_exit is not None:
        be = row(best_exit, [60.0] * n)
        out.update(exit_by_lap=per_lap(row(exit, be), be), best_exit=be)
    return out


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
    opp = K.summarize(corners, [0, 1, 2, 3, 4], lap_times, times, best, sigmas_by_cid={})
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
def _plan_times(best, plan, at_best=2, slow=3):
    """Per-lap per-corner times whose MEDIAN loses `plan[j]` s at corner j, shaped the way a real
    session is: `at_best` laps sitting on the baseline (the best lap is always one of the
    consistency laps) and `slow` laps `plan` slower. Returns (times, lap_times).

    Fixtures that make EVERY lap slower than the baseline describe a session that cannot exist, and
    the per-corner evidence gate reads exactly the relation they get wrong — it abstains on a corner
    no second lap ever reached, so such a fixture silently turns any test into an abstain test."""
    slow_row = [best[j] + plan[j] for j in range(len(best))]
    times = [list(best) for _ in range(at_best)] + [list(slow_row) for _ in range(slow)]
    return times, [sum(r) for r in times]


def _one_corner_lossy(loss=0.5):
    """A single-corner setup whose TYPICAL lap loses `loss` s, with NO apex/brake/coast signal by
    default — the per-test planting flips exactly one signal on so it must dominate.

    THE BEST LAP IS IN THE CANDIDATE SET, and twice over. That is not decoration: in production the
    best lap is always one of the consistency laps, so `min(corner times) <= best_corner_time` holds
    by construction, and the per-corner evidence gate reads exactly that relation. The fixture used
    to be four laps ALL slower than the baseline — a distribution no real session can produce — and
    under the gate every one of those corners correctly abstains as "no second lap ever matched
    your best here", which would have turned every reason test into an abstain test. Five laps: two
    on the baseline, three `loss` slower, so the median loses `loss` and the corner reads as
    reachable (2 of 5) with an interquartile spread of `loss` that the claim clears."""
    corners = _corners(1)
    best = [5.0]
    times = [[5.0], [5.0]] + [[5.0 + loss] for _ in range(3)]
    lap_times = [r[0] for r in times]
    return corners, best, times, lap_times


def test_apex_deficit_picks_apex_reason():
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # the slow laps are 5 km/h DOWN at the apex vs best — a clear apex-speed deficit habit
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, **_levers(times, best, apex=95.0))
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_APEX, r.reason
    assert abs(r.reason.apex_speed_deficit - 5.0) < 1e-9
    assert "apex speed" in K.reason_sentence(r) and "5.0 km/h" in K.reason_sentence(r)
    print(f"ok apex reason: {K.reason_sentence(r)} (contrib {r.reason.contribution:.2f}s)")


def test_coasting_picks_coasting_reason():
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # a coast INSIDE the corner window [50,90] the best lap does NOT have, and NO apex deficit
    med_coast = [_coast(60.0, 80.0, duration=0.6)]
    _brake_s, coast_s = K.lap_window_inputs(corners, [], med_coast)
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, **_levers(times, best, coast=coast_s))
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_COASTING, r.reason
    assert abs(r.reason.coast_extra_s - 0.6) < 1e-9
    assert "throttle sooner" in K.reason_sentence(r)
    # M6 (same as braking): the raw coast_extra_s is a cause ("~N s longer coasting"), not a "+N s"
    # recoverable gain.
    sent = K.reason_sentence(r)
    assert "longer coasting" in sent and "+" not in sent, sent
    print(f"ok coasting reason: {sent} (contrib {r.reason.contribution:.2f}s)")


def test_braking_picks_braking_reason():
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # median lap brakes LONGER in the corner's approach [50-30, 90] than best (0.9 s vs 0.3 s),
    # no apex deficit, no coast
    med_brakes = [_brake(onset_dist=30.0, duration=0.9)]   # within [20, 90]
    best_brakes = [_brake(onset_dist=40.0, duration=0.3)]
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.03},
                      **_levers(times, best, brake=K.lap_window_inputs(corners, med_brakes, [])[0],
                                best_brake=K.lap_window_inputs(corners, best_brakes, [])[0]))
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_BRAKING, r.reason
    assert abs(r.reason.brake_extra_s - 0.6) < 1e-9  # 0.9 - 0.3
    assert "brake later" in K.reason_sentence(r)
    # M6: the raw brake_extra_s is a CAUSE (extra seconds on the brakes), not recoverable time — the
    # sentence now phrases it "~N s longer on the brakes" so it can't read as a recoverable gain
    # larger than the corner's whole loss (the old "+N s on the brakes" advertised a gain).
    sent = K.reason_sentence(r)
    assert "longer on the brakes" in sent and "+" not in sent, sent
    print(f"ok braking reason: {sent} (contrib {r.reason.contribution:.2f}s)")


def _flat_lap(total=300.0, v_mps=20.0):
    """A constant-20 m/s lap as the (dist, elapsed) pair the overlap integral runs on: 1 m
    samples, so 20 m of odometer is exactly 1.0 s and every expected value below is exact."""
    dist = np.linspace(0.0, total, int(total) + 1)
    return dist, dist / v_mps


def test_brake_event_is_counted_by_overlap_not_by_its_onset():
    """L5-01. Corner 1's window is [50, 90] m, so the approach cut is at 50 − BRAKE_APPROACH_M
    = 20 m. A 2.0 s application that STARTS at 10 m (upstream of the cut) and releases at 50 m
    spends 30 m == 1.5 s of itself inside the window; the onset rule scored the whole event
    0.00 s. This is the D24 shape: the best lap's 2.2 s brake into C3 began 14.5 m upstream."""
    dist, elapsed = _flat_lap()
    straddling = [_brake(onset_dist=10.0, onset_time=0.5, duration=2.0)]  # releases at 50 m
    assert K._window_brake_time(straddling, 50.0, 90.0) == 0.0, "the onset rule drops it whole"
    got = K._window_brake_time(straddling, 50.0, 90.0, dist, elapsed)
    assert abs(got - 1.5) < 1e-6, got
    # A lap that braked EARLIER and LONGER than another can therefore never score 0.00 against it.
    assert K._window_brake_time(straddling, 50.0, 90.0, dist, elapsed) > K._window_brake_time(
        [_brake(onset_dist=30.0, onset_time=1.5, duration=0.5)], 50.0, 90.0, dist, elapsed)
    # An event that releases before the cut still scores 0 (no overlap) …
    early = [_brake(onset_dist=0.0, onset_time=0.0, duration=0.5)]      # releases at 10 m
    assert K._window_brake_time(early, 50.0, 90.0, dist, elapsed) == 0.0
    # … and one running past the exit is clipped there (80→90 m = 0.5 s of a 2.0 s application).
    late = [_brake(onset_dist=80.0, onset_time=4.0, duration=2.0)]      # releases at 120 m
    assert abs(K._window_brake_time(late, 50.0, 90.0, dist, elapsed) - 0.5) < 1e-6
    print("ok brake overlap: straddling 2.0 s application scores its in-window 1.5 s, not 0.00")


def test_braking_extra_measures_only_the_in_window_part():
    """L5-01 end to end: the best lap's straddling application is no longer invisible, so the
    printed cause shrinks from the whole-event difference to the in-window one."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    dist, elapsed = _flat_lap()
    med_brakes = [_brake(onset_dist=50.0, onset_time=2.5, duration=1.4)]   # wholly inside [20,90]
    best_brakes = [_brake(onset_dist=10.0, onset_time=0.5, duration=1.5)]  # onset upstream of 20
    assert K._window_brake_time(best_brakes, 50.0, 90.0) == 0.0, "the pre-fix rule saw nothing"
    lap_brake = K.lap_window_inputs(corners, med_brakes, [], dist, elapsed)[0]
    best_brake = K.lap_window_inputs(corners, best_brakes, [], dist, elapsed)[0]
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.03},
                      **_levers(times, best, brake=lap_brake, best_brake=best_brake))
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_BRAKING, r.reason
    assert abs(r.reason.brake_extra_s - 0.4) < 1e-6, r.reason  # 1.4 − 1.0, not 1.4 − 0.0
    print(f"ok in-window braking: {K.reason_sentence(r)} (was ~1.40 s under the onset rule)")


def test_every_ranked_row_is_analysed_not_only_the_first_three():
    """L5-04: a row below the old top-3 cut carried REASON_NONE because it was never analysed,
    so it printed "find time here" under a "How to find it" header while its own ±σ column held
    a usable signal. Every ranked row now gets the same measured pick; `top_n` still caps it."""
    corners = _corners(5)
    best = [5.0] * 5
    times = [[best[j] + (0.50 - 0.10 * j) for j in range(5)] for _ in range(4)]
    lap_times = [sum(r) for r in times]
    kw = dict(sigmas_by_cid={c.cid: 0.20 for c in corners})
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best, **kw)
    assert len(opp.rows) == 5
    kinds = [r.reason.kind for r in opp.rows]
    assert kinds == [K.REASON_CONSISTENCY] * 5, kinds
    assert all("find time here" not in K.reason_sentence(r) for r in opp.rows)
    capped = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best, top_n=3, **kw)
    assert [r.reason.kind for r in capped.rows] == [K.REASON_CONSISTENCY] * 3 + [K.REASON_NONE] * 2
    print(f"ok all rows analysed: {kinds} (top_n=3 still caps at 3)")


def test_the_sigma_fallback_is_named_consistency_not_line():
    """DOMAIN-5 (COACHING-5). The fallback fires on the corner's lap-to-lap spread alone — nothing
    positional enters it — and it was stored and printed as "line" ("repeat your best line"), the
    reason on 14 of the 15 ranked working-set rows. It is named by its trigger now; the stored id
    and the words both say consistency, and neither says line."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # no apex/brake/coast/line signal at all, but real cross-lap spread -> the spread fallback
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.20})
    r = opp.rows[0]
    assert r.reason.kind == "consistency", r.reason
    assert abs(r.reason.sigma - 0.20) < 1e-9
    sent = K.reason_sentence(r)
    assert sent.startswith("consistency: middle half of laps within 0.50 s"), sent
    assert "line" not in sent, f"a spread nothing positional measured reads as a line: {sent!r}"
    print(f"ok consistency reason (fallback): {sent}")


def _line_fixture(*, apex_gain: float, exit_gain: float, sigma: float = 0.03):
    """`_one_corner_lossy`'s five laps with the best lap's apex and exit planted `apex_gain` /
    `exit_gain` km/h against the three slower laps (best − lap; the two baseline laps carry the
    best lap's own speeds), so the habit medians are exactly the planted numbers."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    lev = _levers(times, best, apex=100.0 - apex_gain, best_apex=100.0,
                  exit=60.0 - exit_gain, best_exit=60.0)
    return K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                       sigmas_by_cid={1: sigma}, **lev)


def test_line_needs_a_measured_slower_apex_and_faster_exit():
    """COACHING-5: "line" is said only where the laps' habit shows a line-shaped difference — the
    best lap SLOWER at the apex by LINE_APEX_DELTA_KMH or more AND faster out by LINE_EXIT_DELTA_KMH
    or more. Each half alone is not a line, a faster apex is the apex lever (exclusive by sign), and
    the row's numbers are the habit's, signed."""
    da, de = K.LINE_APEX_DELTA_KMH, K.LINE_EXIT_DELTA_KMH
    r = _line_fixture(apex_gain=-3.0, exit_gain=2.0).rows[0].reason
    assert r.kind == K.REASON_LINE == "line", r
    assert (r.apex_speed_gain, r.exit_speed_gain, r.apex_speed_deficit) == (-3.0, 2.0, 0.0), r
    assert abs(r.contribution - 0.5 * K._saturate(2.0, de)) < 1e-12, r
    opp = _line_fixture(apex_gain=-3.0, exit_gain=2.0).rows[0]
    kmh = K.reason_sentence(opp, reach=False)
    assert kmh == "take your best lap's line (its apex −3.0, exit +2.0 km/h)", kmh
    mph = K.reason_sentence(opp, "mph", reach=False)
    assert mph == "take your best lap's line (its apex −1.9, exit +1.2 mph)", mph
    # Exactly at both thresholds it fires; a hair under either, it does not.
    assert _line_fixture(apex_gain=-da, exit_gain=de).rows[0].reason.kind == K.REASON_LINE
    for a, e in ((-da + 0.01, 5.0), (-5.0, de - 0.01), (-5.0, 0.0), (0.0, 5.0), (-5.0, -2.0)):
        got = _line_fixture(apex_gain=a, exit_gain=e).rows[0].reason
        assert got.kind == K.REASON_CONSISTENCY, (a, e, got)
        assert abs(got.apex_speed_gain - a) < 1e-9 and abs(got.exit_speed_gain - e) < 1e-9, got
    # A FASTER apex with the same exit is the apex lever, never a line.
    fast = _line_fixture(apex_gain=4.0, exit_gain=0.0).rows[0].reason
    assert fast.kind == K.REASON_APEX and fast.apex_speed_deficit == 4.0, fast
    # It competes like every reason: a spread far wider than the exit gain still wins the row.
    wide = _line_fixture(apex_gain=-3.0, exit_gain=1.2, sigma=1.0).rows[0].reason
    assert wide.kind == K.REASON_CONSISTENCY and abs(wide.exit_speed_gain - 1.2) < 1e-9, wide
    print(f"ok line signature: {kmh!r} / {mph!r}")


def test_no_row_says_line_without_its_measured_signature():
    """COACHING-5 pass criterion 1, as a property: across a seeded sweep of habit inputs (apex and
    exit gains either side of the thresholds, every lever and spread) and the three drift phases of
    the golden gate, a row whose kind is "line" always carries the signature — apex gain ≤
    −LINE_APEX_DELTA_KMH and exit gain ≥ LINE_EXIT_DELTA_KMH — and nothing else ever reads "line"."""
    from _synthetic import drift_band_session, drift_median_session, drift_noise_session

    da, de = K.LINE_APEX_DELTA_KMH, K.LINE_EXIT_DELTA_KMH
    rng = np.random.default_rng(5)
    seen = {}
    for _ in range(400):
        n_laps, n_c = int(rng.integers(3, 9)), int(rng.integers(1, 5))
        corners = _corners(n_c)
        best = [5.0] * n_c
        times = [[5.0 + max(0.0, rng.normal(0.2, 0.2)) for _j in range(n_c)] for _i in range(n_laps)]
        times[0] = list(best)                                  # the best lap is a candidate
        lap_times = [sum(t) for t in times]

        def cells(scale, base, n_c=n_c, n_laps=n_laps):
            return [[base + rng.normal(0.0, scale) for _j in range(n_c)] for _i in range(n_laps)]
        opp = K.summarize(
            corners, list(range(n_laps)), lap_times, times, best,
            {c.cid: float(abs(rng.normal(0.0, 0.3))) for c in corners},
            apex_by_lap=cells(3.0, 60.0), best_apex=list(60.0 + rng.normal(0, 3.0, n_c)),
            exit_by_lap=cells(3.0, 80.0), best_exit=list(80.0 + rng.normal(0, 3.0, n_c)),
            brake_time_by_lap=cells(0.2, 1.0), best_brake_time=[1.0] * n_c)
        for row in opp.rows:
            r = row.reason
            seen[r.kind] = seen.get(r.kind, 0) + 1
            shaped = r.apex_speed_gain <= -da and r.exit_speed_gain >= de
            assert r.kind != K.REASON_LINE or shaped, r
    for make in (drift_band_session, drift_median_session, drift_noise_session):
        for row in make().coaching_opportunities().rows:
            r = row.reason
            seen[r.kind] = seen.get(r.kind, 0) + 1
            assert r.kind != K.REASON_LINE or (r.apex_speed_gain <= -da
                                               and r.exit_speed_gain >= de), (make.__name__, r)
    # The sweep must reach the branch it guards, and the other kinds beside it.
    assert seen.get(K.REASON_LINE, 0) >= 10 and seen.get(K.REASON_CONSISTENCY, 0) >= 10, seen
    print(f"ok no line without its signature: {seen}")


def test_dominant_reason_is_the_largest_contribution():
    """With BOTH an apex deficit AND a coast present, the one with the larger seconds-of-loss
    contribution wins — here a big coast (0.6 s) beats a tiny apex deficit (0.3 km/h)."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    med_coast = [_coast(60.0, 80.0, duration=0.6)]
    coast_s = K.lap_window_inputs(corners, [], med_coast)[1]
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, **_levers(times, best, coast=coast_s, apex=99.7))
    assert opp.rows[0].reason.kind == K.REASON_COASTING, opp.rows[0].reason
    # and a big apex deficit beats a tiny coast
    med_coast_small = [_coast(60.0, 62.0, duration=0.05)]
    coast_small = K.lap_window_inputs(corners, [], med_coast_small)[1]
    opp2 = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                       sigmas_by_cid={1: 0.03}, **_levers(times, best, coast=coast_small, apex=92.0))
    assert opp2.rows[0].reason.kind == K.REASON_APEX, opp2.rows[0].reason
    print("ok dominant: largest seconds-of-loss contribution wins (coast vs apex both ways)")


# ------------------------------------------------ ADV-1: the levers are the habit, not one lap
def _six_laps():
    """One corner, six laps: two on the best lap's time (lap 0 IS the best) and four slower, so
    the median-TIME lap (`median_lap_id`, the lower middle) is lap 2 and the corner is reachable."""
    corners = _corners(1)
    best = [5.0]
    times = [[5.0], [5.0], [5.3], [5.4], [5.5], [5.6]]
    return corners, best, times, [r[0] for r in times]


def test_the_lever_is_the_habit_not_one_lap():
    """ADV-1 (QA r4, 2026-09-28). The reason used to difference the median-TIME lap alone against
    the best lap and print that one lap's anecdote as the instruction: SD_19_09 C2 read "back to
    throttle sooner (~1.50 s longer coasting)" while the laps' median difference was 0.00 s. Each
    lever is now the median, over the corner's counted laps, of each lap's difference from best.

    Both directions: the median-time lap ALONE coasts 1.5 s longer and every other lap matches the
    best lap → no coasting lever (0.00 s, LINE on the corner's spread); four of six laps spend
    0.30 s longer on the brakes while the median-time lap does not → BRAKING at 0.30 s."""
    corners, best, times, lap_times = _six_laps()
    assert K.median_lap_id(list(range(6)), lap_times) == 2
    coast = [[0.0], [0.0], [1.5], [0.0], [0.0], [0.0]]            # only the median-time lap
    opp = K.summarize(corners, list(range(6)), lap_times, times, best, sigmas_by_cid={1: 0.20},
                      coast_time_by_lap=coast, best_coast_time=[0.0])
    r = opp.rows[0]
    assert r.reason.coast_extra_s == 0.0, r.reason
    assert r.reason.kind == K.REASON_CONSISTENCY, (
        f"one lap's 1.5 s coast became the instruction: {K.reason_sentence(r)}", r.reason)

    brake = [[0.0], [0.30], [0.0], [0.30], [0.30], [0.30]]         # 4 of 6, not the median lap
    opp = K.summarize(corners, list(range(6)), lap_times, times, best, sigmas_by_cid={1: 0.03},
                      brake_time_by_lap=brake, best_brake_time=[0.0])
    r = opp.rows[0]
    assert abs(r.reason.brake_extra_s - 0.30) < 1e-12 and r.reason.kind == K.REASON_BRAKING, r.reason
    # The apex lever is the same statistic, signed the other way (best − lap).
    apex = [[100.0], [100.0], [90.0], [100.0], [100.0], [100.0]]
    opp = K.summarize(corners, list(range(6)), lap_times, times, best, sigmas_by_cid={1: 0.03},
                      apex_by_lap=apex, best_apex=[100.0])
    assert opp.rows[0].reason.apex_speed_deficit == 0.0, opp.rows[0].reason
    print(f"ok ADV-1 habit: a lone 1.5 s coast on the median-time lap -> {K.REASON_CONSISTENCY}; "
          f"4 of 6 laps +0.30 s on the brakes -> {K.REASON_BRAKING} {r.reason.brake_extra_s:.2f} s")


def test_a_session_reads_the_laps_coasting_habit_not_the_median_laps_coast():
    """ADV-1 through the real Session: on the golden gate's drift-band session the median-time lap
    coasts 0.60 s longer than the best lap through C2 and the other laps do not, so the row said
    "coasting" off one lap. Its lever is now the laps' median difference — none."""
    from _synthetic import drift_band_session

    s = drift_band_session()
    opp = s.coaching_opportunities()
    c2 = next(r for r in opp.rows if r.cid == 2)
    assert c2.reason.coast_extra_s == 0.0 and c2.reason.kind != K.REASON_COASTING, (
        f"C2's coasting lever is {c2.reason.coast_extra_s:.3f} s ({c2.reason.kind}) — one lap's "
        "anecdote, not the laps' habit", c2.reason)
    # ...and the anecdote is really there on the median-time lap, so the assertion above has teeth.
    cells = s._coaching_lap_inputs(s.consistency_lap_ids())
    k = [c.cid for c in s.corners.corner_list()].index(2)
    best = s.best_lap_id()
    lone = cells[opp.median_lap_id][1][k] - cells[best][1][k]
    assert lone > 0.5, lone
    others = [cells[i][1][k] - cells[best][1][k] for i in cells if i not in (opp.median_lap_id, best)]
    assert max(others) < 0.05, others
    print(f"ok ADV-1 session: C2's median-time lap coasts +{lone:.2f} s alone; the habit reads "
          f"{c2.reason.coast_extra_s:.2f} s ({c2.reason.kind})")


def test_the_levers_take_the_median_of_the_signed_differences_then_the_floor():
    """ADV-1's order of operations, pinned: the median of the SIGNED lap − best differences, THEN
    floored at 0. For an even count the two orders differ — cells {−0.4, +0.2} read 0 this way and
    0.1 the other — and for a lever the laps do not share (half faster than best, half slower) 0 is
    the honest answer."""
    corners = _corners(1)
    best = [5.0]
    times = [[5.0], [5.0], [5.5], [5.5]]
    lap_times = [r[0] for r in times]
    brake = [[0.6], [0.6], [1.2], [1.2]]           # best lap on 1.0 s: cells −0.4, −0.4, +0.2, +0.2
    opp = K.summarize(corners, list(range(4)), lap_times, times, best, sigmas_by_cid={1: 0.03},
                      brake_time_by_lap=brake, best_brake_time=[1.0])
    assert opp.rows[0].reason.brake_extra_s == 0.0, opp.rows[0].reason
    print("ok ADV-1 order: median of signed differences, then the floor")


def test_an_unresolved_cell_counts_in_none_of_the_lever_medians():
    """The levers read EXACTLY the cells the row's `time_lost` is a median of: a lap whose corner
    was not matched on track at both edges (C5, `resolved_by_lap`) is out of the loss, and must be
    out of the brake, coast and apex medians too — here its wild cells would move all three."""
    corners = _corners(1)
    best = [5.0]
    times = [[5.0], [5.0], [5.5], [5.5], [5.5], [5.5]]
    lap_times = [r[0] for r in times]
    kw = dict(brake_time_by_lap=[[0.0], [0.0], [0.0], [0.2], [0.2], [9.0]], best_brake_time=[0.0],
              coast_time_by_lap=[[0.0], [0.0], [0.0], [0.2], [0.2], [9.0]], best_coast_time=[0.0],
              apex_by_lap=[[100.0], [100.0], [100.0], [98.0], [98.0], [50.0]], best_apex=[100.0],
              sigmas_by_cid={1: 0.03})
    masked = K.summarize(corners, list(range(6)), lap_times, times, best,
                         resolved_by_lap=[[True]] * 5 + [[False]], best_resolved=[True], **kw)
    r = masked.rows[0].reason
    assert (r.brake_extra_s, r.coast_extra_s, r.apex_speed_deficit) == (0.0, 0.0, 0.0), r
    counted = K.summarize(corners, list(range(6)), lap_times, times, best,
                          resolved_by_lap=[[True]] * 6, best_resolved=[True], **kw)
    c = counted.rows[0].reason
    assert (c.brake_extra_s, c.coast_extra_s, c.apex_speed_deficit) == (0.1, 0.1, 1.0), c
    print("ok ADV-1 mask: an unmatched lap's cells stay out of all three lever medians")


def test_session_levers_equal_an_independent_median_over_the_laps():
    """The Session wiring, recomputed independently: on the braking stadium (a real g signal, a
    coasting tail on the three slow laps) every row's apex / brake / coast lever equals, bit for
    bit, the median over the counted laps of `lap_window_inputs` (each lap's memoized warp) and
    `lap_corner_stats`' apex speed, minus the best lap's, floored at 0."""
    s = _braking_stadium_session()
    cl = s.corners.corner_list()
    n = len(cl)
    best = s.best_lap_id()
    total_ref = float(s.corners.basis()[1])
    ids = [i for i in s.consistency_lap_ids() if len(s.corners.lap_corner_stats(i)) == n]

    def cells(i):
        tot = (s.best_lap_total_distance() if i == best
               else float(s._lap_time_dist(i)[1][-1]))
        dist, _v, el = s._lap_arrays(i)
        b, c = K.lap_window_inputs(cl, s.driving.lap_brake_events(i), s.driving.lap_coasting_spans(i),
                                   dist, el, s.driving.lap_brake_on(i), corner_dist_total=total_ref,
                                   lap_total=tot, align=s.corners.lap_alignment(i, tot))
        return b, c, [st.apex_speed for st in s.corners.lap_corner_stats(i)]

    B = cells(best)
    L = {i: cells(i) for i in ids}
    rows = {r.cid: r.reason for r in s.coaching_opportunities().rows}
    assert rows, "the braking stadium ranks nothing"
    best_ok = s.corners.lap_corner_resolved(best)
    for j, c in enumerate(cl):
        if c.cid not in rows:
            continue
        ok = [i for i in ids if s.corners.lap_corner_resolved(i)[j] and best_ok[j]]
        want = [max(float(np.median([sign * (L[i][k][j] - B[k][j]) for i in ok])), 0.0)
                for k, sign in ((2, -1.0), (0, 1.0), (1, 1.0))]
        got = [rows[c.cid].apex_speed_deficit, rows[c.cid].brake_extra_s, rows[c.cid].coast_extra_s]
        assert got == want, (c.cid, got, want)
    assert rows[2].coast_extra_s > 1.0, rows[2]   # the tail the three slow laps share
    print(f"ok session levers == independent median over {len(ids)} laps on {len(rows)} rows")


def test_the_coaching_cells_and_thirds_are_derived_once_and_follow_every_upstream_change():
    """COACHING-2: `coaching_opportunities` runs five times a refresh, and it now reads the phase
    report too, so its per-lap lever cells (`_coaching_lap_inputs`) and the report's per-lap thirds
    are MEMOIZED — a warm call runs neither `lap_window_inputs` nor `corner_phase_losses`. The memo
    is keyed on its whole input set instead of being invalidated, so the teeth are the other way
    round: the invalidations `set_timing_lines` fires (the corner and driving services') make the
    next call derive everything again, and after the best lap moves the warm session answers
    exactly what a fresh one does."""
    s = _braking_stadium_session()
    calls = {"cells": 0, "thirds": 0}
    real_cells, real_thirds = K.lap_window_inputs, K.corner_phase_losses

    def cells(*a, **k):
        calls["cells"] += 1
        return real_cells(*a, **k)

    def thirds(*a, **k):
        calls["thirds"] += 1
        return real_thirds(*a, **k)

    K.lap_window_inputs, K.corner_phase_losses = cells, thirds
    try:
        first = repr(s.coaching_opportunities())
        cold = dict(calls)
        assert cold["cells"] > 0 and cold["thirds"] > 0, cold
        for _ in range(4):
            assert repr(s.coaching_opportunities()) == first
        assert calls == cold, f"a warm call derived its inputs again: {calls} after {cold}"
        s.corners.invalidate()
        s.driving.invalidate()
        assert repr(s.coaching_opportunities()) == first
        assert calls == {k: 2 * v for k, v in cold.items()}, (
            f"after the re-segment's invalidations the memo still answered: {calls}, cold {cold}")
    finally:
        K.lap_window_inputs, K.corner_phase_losses = real_cells, real_thirds

    fresh = _braking_stadium_session()
    for sess in (s, fresh):          # the best lap moves, as a test seeds it
        sess._best_cache = 1
        sess.corners.invalidate()
        sess.driving.invalidate()
    moved = repr(s.coaching_opportunities())
    assert moved != first, "moving the best lap changed nothing — the comparison below is vacuous"
    assert moved == repr(fresh.coaching_opportunities()), "the warm session kept a stale answer"
    print(f"ok coaching memo: cold {cold}, warm 0, re-derived after invalidation, follows the best")


def test_the_consistency_row_states_the_iqr_its_gate_reads_never_sigma():
    """ADV-4 (QA r4). "laps vary ±1.28 s" printed σ on SD3h C1 while the row's own evidence gate
    reads the interquartile range (0.31 s there): 90 % of the laps sat inside the printed band. The
    spread sentence ("line" until COACHING-5) states the middle half; a row with no measured
    evidence states no spread."""
    import dataclasses

    ev = K.Evidence(n_laps=62, reach_laps=4, reach=K.REACH_RARE, iqr=0.309, abstain=K.ABSTAIN_NONE)
    opp = dataclasses.replace(_opp_with(K.REASON_CONSISTENCY, K._NO_PHASES, sigma=1.278),
                              evidence=ev)
    sent = K.reason_sentence(opp, reach=False)
    assert sent == "consistency: middle half of laps within 0.31 s", sent
    assert "1.28" not in sent and "±" not in sent, sent
    bare = _opp_with(K.REASON_CONSISTENCY, K._NO_PHASES, sigma=1.278)  # _NO_EVIDENCE, n_laps 0
    assert K.reason_sentence(bare) == "consistency", K.reason_sentence(bare)
    print(f"ok ADV-4 consistency copy: {sent!r}; unmeasured: {K.reason_sentence(bare)!r}")


# ----------------------------------------------- D2: entry/apex/exit Δt-vs-best decomposition
def _flat_trace(d0: float, d1: float, v_kmh: float, n: int = 200):
    """A constant-speed lap as the decomposition now reads it: (dist, ELAPSED). Constant v keeps
    the clock analytic — the time over a span L (m) at v (m/s) is exactly L/v — so the thirds are
    exact. (These fixtures used to hand back speed, because the thirds were ∫ds/v; they are now
    read off the lap's own clock, the same quantity `segment_times` measures.)"""
    dist = np.linspace(d0, d1, n)
    return dist, (dist - d0) / (float(v_kmh) / 3.6)


def _clock_from_speed(dist: np.ndarray, v_kmh: np.ndarray) -> np.ndarray:
    """The elapsed array a piecewise speed profile implies: cumulative ∫ds/v. For a fixture whose
    speed changes mid-window, this is the exact clock that speed would have produced."""
    v_mps = np.maximum(np.asarray(v_kmh, float), 1e-6) / 3.6
    dt = np.diff(dist) / ((v_mps[:-1] + v_mps[1:]) / 2.0)
    return np.concatenate([[0.0], np.cumsum(dt)])


def test_phase_losses_sum_to_total_and_signs():
    """The three thirds telescope to the corner's total Δt-vs-best, and the sign is right:
    a lap slower than best ⇒ positive total; faster ⇒ negative."""
    enter, exit_ = 100.0, 220.0  # a 120 m corner window
    best_dist, best_t = _flat_trace(0.0, 400.0, 80.0)   # best is fast everywhere
    # SLOWER lap: 72 km/h through the window -> positive Δt over every third.
    slow_dist, slow_t = _flat_trace(0.0, 400.0, 72.0)
    pl = K.corner_phase_losses(slow_dist, slow_t, best_dist, best_t, enter, exit_)
    # each third is 40 m: 40/(72/3.6) - 40/(80/3.6) = 40/20 - 40/22.222 = 2.0 - 1.8 = 0.2 s
    for v in pl.as_tuple():
        assert v > 0, ("slower than best must be a positive loss per third", pl)
    assert abs(pl.total - sum(pl.as_tuple())) < 1e-9, "total must equal the sum of the thirds"
    expected_total = (exit_ - enter) / (72.0 / 3.6) - (exit_ - enter) / (80.0 / 3.6)
    assert abs(pl.total - expected_total) < 1e-3, (pl.total, expected_total)
    # FASTER lap ⇒ negative total (each third negative).
    fast_dist, fast_t = _flat_trace(0.0, 400.0, 88.0)
    pf = K.corner_phase_losses(fast_dist, fast_t, best_dist, best_t, enter, exit_)
    assert pf.total < 0 and all(v < 0 for v in pf.as_tuple()), pf
    print(f"ok phases: thirds sum to total; slow⇒+{pl.total:.3f}s, fast⇒{pf.total:.3f}s")


def test_phase_losses_all_on_entry_attributes_to_entry():
    """A lap that loses ALL its time in the entry third (slow there, on-best elsewhere) attributes
    the loss to entry — entry positive, apex/exit ~0, dominant == PHASE_ENTRY."""
    enter, exit_ = 100.0, 220.0  # thirds: [100,140] entry, [140,180] apex, [180,220] exit
    best_dist, best_t = _flat_trace(0.0, 400.0, 80.0)
    # Lap is 60 km/h in the entry third only, matches best (80) elsewhere. Build piecewise.
    n = 600
    lap_dist = np.linspace(0.0, 400.0, n)
    lap_v = np.full(n, 80.0)
    lap_v[(lap_dist >= 100.0) & (lap_dist < 140.0)] = 60.0
    lap_t = _clock_from_speed(lap_dist, lap_v)
    pl = K.corner_phase_losses(lap_dist, lap_t, best_dist, best_t, enter, exit_)
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


def test_phase_thirds_telescope_to_the_corners_own_time():
    """THE INVARIANT THE ∫ds/v DECOMPOSITION DID NOT HOLD: the three thirds are the corner's own
    time, split three ways — so they must sum to exactly what the Corners table reports for that
    corner (`corners.lap_corner_stats(...).time`), and the Δt triple must sum to exactly that
    row's `delta` vs best.

    It used to be an ∫ds/v approximation of the smoothed speed channel while the table read the
    lap's clock, and the two disagreed on EVERY corner. Measured on the best lap of the D24 0060
    pair, where there is no drift and nothing to compare against:

        C7   clock 4.484 s  vs  ∫ds/v 4.870 s   (+0.385, +8.6%)   apex 31.1 km/h
        C11  clock 6.384 s  vs  ∫ds/v 6.877 s   (+0.493, +7.7%)   apex 19.2 km/h

    r = -0.46 between apex speed and the signed error — 1/v amplifies a speed error exactly where
    the kart is slowest, which is where a corner's time is largest. On the coaching panel that
    error sat beside a true-clock "time lost" and, on 4 of 11 D24 rows, netted the opposite sign.
    This test fails if anyone reintroduces an estimator here."""
    corner_list = _corners(3)
    total = 400.0
    # A lap whose speed varies over the whole trace, so an integral and a clock cannot agree by
    # accident: the clock is built from the speed profile, then only the clock is handed over.
    dist = np.linspace(0.0, total, 800)
    v_kmh = 60.0 + 25.0 * np.sin(dist / 40.0)          # 35-85 km/h, slowest inside the corners
    elapsed = _clock_from_speed(dist, v_kmh)
    stats = corners_mod.lap_corner_stats(corner_list, total, dist, v_kmh, elapsed)
    for c, st in zip(corner_list, stats, strict=True):
        thirds = K.corner_best_thirds(dist, elapsed, c.enter, c.exit,
                                      corner_dist_total=total, best_total=total)
        assert abs(sum(thirds) - st.time) < 1e-9, (
            f"C{c.cid}: thirds sum to {sum(thirds):.6f} s but the Corners table says "
            f"{st.time:.6f} s — the decomposition is measuring a different quantity again")

    # ...and the Δt triple telescopes to the same row's delta vs best.
    slow_v = v_kmh * 0.92
    slow_elapsed = _clock_from_speed(dist, slow_v)
    ref = corners_mod.lap_corner_stats(corner_list, total, dist, v_kmh, elapsed)
    slow_stats = corners_mod.lap_corner_stats(corner_list, total, dist, slow_v, slow_elapsed,
                                              ref=ref)
    for c, st in zip(corner_list, slow_stats, strict=True):
        pl = K.corner_phase_losses(dist, slow_elapsed, dist, elapsed, c.enter, c.exit,
                                   corner_dist_total=total, lap_total=total, best_total=total)
        assert abs(pl.total - st.delta) < 1e-9, (
            f"C{c.cid}: thirds net {pl.total:+.6f} s but the row's delta is {st.delta:+.6f} s")
        assert pl.total > 0, ("a slower lap must decompose to a positive net", c.cid, pl)
    print("ok phase-thirds: sum == the Corners table's own corner time, and the triple == its "
          "delta, on every corner")


def test_phase_losses_are_deterministic_and_degenerate_is_zero():
    best_dist, best_t = _flat_trace(0.0, 400.0, 80.0)
    slow_dist, slow_t = _flat_trace(0.0, 400.0, 72.0)
    a = K.corner_phase_losses(slow_dist, slow_t, best_dist, best_t, 100.0, 220.0)
    b = K.corner_phase_losses(slow_dist, slow_t, best_dist, best_t, 100.0, 220.0)
    assert a == b, "corner_phase_losses must be deterministic"
    # a degenerate (exit <= enter) window, and a too-short trace, both ⇒ zero phases
    deg = K.corner_phase_losses(slow_dist, slow_t, best_dist, best_t, 220.0, 220.0)
    assert deg.as_tuple() == (0.0, 0.0, 0.0), deg
    short = K.corner_phase_losses(np.array([1.0]), np.array([0.1]), best_dist, best_t,
                                  100.0, 220.0)
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
    best_dist, best_t = _flat_trace(0.0, 400.0, 80.0)          # best lap == corner basis frame
    # 1.05× longer odometer AND 1.05× faster ⇒ same time over the same track fraction.
    s = 1.05
    lap_total = corner_total * s
    lap_dist, lap_t = _flat_trace(0.0, lap_total, 80.0 * s)
    pl = K.corner_phase_losses(lap_dist, lap_t, best_dist, best_t, enter, exit_,
                               corner_dist_total=corner_total, lap_total=lap_total,
                               best_total=corner_total)
    assert all(abs(v) < 1e-6 for v in pl.as_tuple()), ("projected boundaries ⇒ ~0 loss", pl)
    # Without projection (literal window on the longer-odometer lap) the same trace WOULD register
    # a loss — the window covers a 1.05× longer slice of track. Confirms projection is doing work.
    pl_literal = K.corner_phase_losses(lap_dist, lap_t, best_dist, best_t, enter, exit_)
    assert pl_literal.total < -1e-3, ("literal (unprojected) window differs", pl_literal)
    print(f"ok phases-proj: projected per-lap ⇒ {pl.as_tuple()}; literal ⇒ {pl_literal.total:.3f}s")


def test_summarize_attaches_phase_decomposition():
    """summarize takes each row's PhaseLoss from `phases_by_cid` exactly as handed in (the phase
    report's median triple — COACHING-2); a corner absent from it, or None there, gets zero phases
    and so no "most of it on" clause."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    args = (corners, list(range(len(lap_times))), lap_times, times, best)
    triple = (0.021, 0.094, -0.013)
    opp = K.summarize(*args, sigmas_by_cid={1: 0.03}, **_levers(times, best, apex=97.0),
                      phases_by_cid={1: triple})
    pl = opp.rows[0].phases
    assert pl.as_tuple() == triple, pl
    assert "most of it on the apex" in K.reason_sentence(opp.rows[0]), K.reason_sentence(opp.rows[0])
    for absent in (None, {}, {1: None}):
        opp0 = K.summarize(*args, sigmas_by_cid={1: 0.03}, **_levers(times, best, apex=97.0),
                           phases_by_cid=absent)
        assert opp0.rows[0].phases.as_tuple() == (0.0, 0.0, 0.0), absent
        assert "most of it on" not in K.reason_sentence(opp0.rows[0])
    print(f"ok summarize-phases: row thirds {pl.as_tuple()} as handed in; absent ⇒ zero")


def _opp_with(reason_kind: str, phases: "K.PhaseLoss", *, time_lost: float = 0.30,
              **reason_kw) -> "K.Opportunity":
    """A hand-built Opportunity with a chosen reason kind + phase decomposition, for the copy
    (M4/M5/M6) guards that must be deterministic regardless of the summarize plumbing."""
    reason = K.Reason(kind=reason_kind, contribution=reason_kw.pop("contribution", 0.1),
                      apex_speed_deficit=reason_kw.pop("apex_speed_deficit", 0.0),
                      brake_extra_s=reason_kw.pop("brake_extra_s", 0.0),
                      coast_extra_s=reason_kw.pop("coast_extra_s", 0.0),
                      sigma=reason_kw.pop("sigma", 0.05),
                      apex_speed_gain=reason_kw.pop("apex_speed_gain", 0.0),
                      exit_speed_gain=reason_kw.pop("exit_speed_gain", 0.0))
    return K.Opportunity(cid=1, direction=-1, time_lost=time_lost, entry_dist=50.0,
                         reason=reason, phases=phases)


def test_m5_phase_clause_is_reason_aware():
    """M5: the "most of it on <phase>" clause reads as a FIX LOCATION, so it must only be appended
    when the dominant third matches the reason's natural lever phase. A BRAKING (entry/approach)
    reason whose EXIT third dominates must NOT read "brake … — most of it on exit" (nonsense — an
    entry fix pointed at the exit); it must read as a consequence instead ("… and it carries to
    exit"). The matching case (braking + entry-dominant) keeps the fix-location clause."""
    # Exit dominates (>= half of the positive total) but the reason is a braking/entry lever.
    exit_dom = K.PhaseLoss(entry=0.02, apex=0.02, exit=0.20)
    brake_opp = _opp_with(K.REASON_BRAKING, exit_dom, brake_extra_s=0.5)
    sent = K.reason_sentence(brake_opp)
    assert "most of it on exit" not in sent, ("an entry fix must not point at the exit", sent)
    assert "carries to exit" in sent, ("the incompatible phase reads as a consequence", sent)
    assert sent.startswith("brake later"), sent
    # The compatible case: braking reason with the ENTRY third dominant -> the fix-location clause.
    entry_dom = K.PhaseLoss(entry=0.20, apex=0.02, exit=0.02)
    brake_entry = _opp_with(K.REASON_BRAKING, entry_dom, brake_extra_s=0.5)
    assert "most of it on entry" in K.reason_sentence(brake_entry), K.reason_sentence(brake_entry)
    # An APEX reason with the apex third dominant -> "most of it on the apex" (compatible).
    apex_dom = K.PhaseLoss(entry=0.02, apex=0.20, exit=0.02)
    apex_opp = _opp_with(K.REASON_APEX, apex_dom, apex_speed_deficit=5.0)
    assert "most of it on the apex" in K.reason_sentence(apex_opp), K.reason_sentence(apex_opp)
    # The CONSISTENCY (spread) reason is phase-agnostic -> the plain fix-location clause on any
    # dominant third.
    spread_opp = _opp_with(K.REASON_CONSISTENCY, exit_dom, sigma=0.2)
    assert "most of it on exit" in K.reason_sentence(spread_opp), K.reason_sentence(spread_opp)
    spread_entry = _opp_with(K.REASON_CONSISTENCY, entry_dom, sigma=0.2)
    assert "most of it on entry" in K.reason_sentence(spread_entry)
    # A measured LINE trades apex speed for the exit (COACHING-5): apex/exit are its phases, and an
    # entry-dominant loss reads as where it shows, not where the line is fixed.
    line_exit = _opp_with(K.REASON_LINE, exit_dom, apex_speed_gain=-2.0, exit_speed_gain=1.5)
    assert K.reason_sentence(line_exit).endswith("km/h) — most of it on exit"), \
        K.reason_sentence(line_exit)
    line_entry = _opp_with(K.REASON_LINE, entry_dom, apex_speed_gain=-2.0, exit_speed_gain=1.5)
    assert K.reason_sentence(line_entry).endswith("km/h), and it carries to entry"), \
        K.reason_sentence(line_entry)
    print("ok M5 reason-aware clause: entry fix never 'most of it on exit'; compatible phases keep it")


def test_m6_raw_channel_number_is_a_cause_not_a_gain():
    """M6: brake_extra_s / coast_extra_s are raw driving-channel CAUSES that can exceed the corner's
    whole time_lost — the sentence must phrase them as a cause ("~N s longer on the brakes"), never
    as a "+N s" recoverable gain larger than the corner's own measured loss."""
    # brake_extra_s (1.40 s) is 8.5x the corner's whole loss (0.16 s) — the M6 fixture shape.
    opp = _opp_with(K.REASON_BRAKING, K._NO_PHASES, time_lost=0.16, brake_extra_s=1.40)
    sent = K.reason_sentence(opp)
    assert "1.40 s longer on the brakes" in sent, sent
    assert "+1.40" not in sent and "+" not in sent, ("the raw cause must not read as a +gain", sent)
    # coasting mirrors it.
    opp_c = _opp_with(K.REASON_COASTING, K._NO_PHASES, time_lost=0.16, coast_extra_s=1.40)
    assert "1.40 s longer coasting" in K.reason_sentence(opp_c), K.reason_sentence(opp_c)
    print("ok M6: raw brake/coast seconds read as a cause, never a +gain above the corner's loss")


def test_brake_approach_window_and_coast_only_when_best_lacks_it():
    """A brake/coast that the BEST lap matches is NOT a loss (the difference is what counts);
    and a brake event OUTSIDE the corner approach window is ignored."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)
    # identical brake on both laps -> brake contribution 0 (falls back to consistency)
    same_brake = [_brake(onset_dist=35.0, duration=0.7)]
    same_s = K.lap_window_inputs(corners, same_brake, [])[0]
    assert same_s == [0.7], same_s   # the application IS counted — on both laps alike
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best, sigmas_by_cid={1: 0.10},
                      **_levers(times, best, brake=same_s, best_brake=same_s))
    assert opp.rows[0].reason.kind == K.REASON_CONSISTENCY, opp.rows[0].reason
    # a brake far before the approach window (outside [enter-30, exit]) is ignored
    far_brake = [_brake(onset_dist=-100.0, duration=2.0)]
    far_s = K.lap_window_inputs(corners, far_brake, [])[0]
    assert far_s == [0.0], far_s
    opp2 = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best, sigmas_by_cid={1: 0.10},
                       **_levers(times, best, brake=far_s))
    assert opp2.rows[0].reason.kind == K.REASON_CONSISTENCY, opp2.rows[0].reason
    print("ok windows: matched brake/coast not a loss; out-of-window brake ignored")


# ----------------------------------------------------------------------- determinism
def test_summarize_is_deterministic():
    corners = _corners(4)
    best = [5.0, 6.0, 7.0, 4.0]
    rng = np.random.default_rng(3)
    times = [[best[j] + (0.4 if j == 2 else 0.1) + rng.normal(0, 0.02) for j in range(4)]
             for _ in range(6)]
    lap_times = [sum(r) for r in times]
    brake_s, coast_s = K.lap_window_inputs(corners, [_brake(220.0, duration=0.7)],
                                           [_coast(260.0, 280.0)])
    kw = dict(sigmas_by_cid={1: 0.1, 2: 0.2, 3: 0.05, 4: 0.05},
              **_levers(times, best, brake=brake_s, coast=coast_s, apex=[99.0, 100.0, 97.0, 100.0]))
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
    opp = K.summarize(corners, [0, 1], lap_times, times, best, sigmas_by_cid={})
    assert opp.enough is False and opp.rows == [] and opp.n_laps == 2
    print("ok gate: < MIN_LAPS -> enough=False, no rows, no crash")


def test_no_corners_or_no_loss_excluded():
    # no corners
    opp = K.summarize([], [0, 1, 2], [70, 71, 72], [[], [], []], [], {})
    assert opp.enough is False and opp.rows == []
    # enough laps + corners but NO corner loses time -> enough=True but no rows (dialog shows the
    # "nice driving" empty state)
    corners = _corners(2)
    best = [5.0, 6.0]
    times = [[5.0, 6.0], [5.0, 6.0], [5.0, 6.0]]  # the typical lap matches best everywhere
    opp2 = K.summarize(corners, [0, 1, 2], [11.0, 11.0, 11.0], times, best, {})
    assert opp2.enough is True and opp2.rows == []
    print("ok gate: no corners -> excluded; no loss -> enough but empty rows")


# ---------------------------------------- D13: coaching row halves share ONE baseline (local best)
def test_brake_window_projected_onto_each_laps_own_odometer():
    """D13 (odometer-frame): a corner's [enter, exit] is in the BEST-lap (reference) odometer, but
    each lap's brake events live in its OWN odometer. `lap_window_inputs` (the per-lap cells the
    braking reason takes its median over) must project the window onto the lap's own odometer
    before matching. Here the corner is [50, 90] in a 1000 m reference frame; the lap is 1100 m
    long, so its window is [55, 99]. A brake at onset 96 m (inside the PROJECTED [55-30, 99]
    window, but OUTSIDE the un-projected [50-30, 90]) must count — proving the projection
    happened; end to end, the slow laps' habit then reads BRAKING."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)  # one corner: enter 50, exit 90
    med_brakes = [_brake(onset_dist=96.0, duration=0.9)]   # in projected [25, 99], not raw [20, 90]
    best_brakes = [_brake(onset_dist=40.0, duration=0.3)]  # best frame == reference frame here
    lap_s = K.lap_window_inputs(corners, med_brakes, [], corner_dist_total=1000.0,
                                lap_total=1100.0)[0]
    best_s = K.lap_window_inputs(corners, best_brakes, [], corner_dist_total=1000.0,
                                 lap_total=1000.0)[0]
    assert lap_s == [0.9] and best_s == [0.3], (lap_s, best_s)
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, **_levers(times, best, brake=lap_s, best_brake=best_s))
    r = opp.rows[0]
    assert r.reason.kind == K.REASON_BRAKING, r.reason
    assert abs(r.reason.brake_extra_s - 0.6) < 1e-9  # 0.9 - 0.3
    # control: the SAME events WITHOUT the totals (identity projection) leave the brake outside the
    # un-projected window -> it does NOT count -> the row falls back to CONSISTENCY.
    raw_s = K.lap_window_inputs(corners, med_brakes, [])[0]
    assert raw_s == [0.0], raw_s
    opp0 = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                       sigmas_by_cid={1: 0.03}, **_levers(times, best, brake=raw_s, best_brake=best_s))
    assert opp0.rows[0].reason.kind == K.REASON_CONSISTENCY, opp0.rows[0].reason
    print("ok D13 odometer-frame: corner window projected onto each lap's own odometer for braking")


def test_reason_window_is_the_same_drift_gated_window_the_phases_use():
    """C1: a corner's [enter, exit] lives in the REFERENCE odometer, and on a drifted lap the
    window the BRAKE/COAST evidence is matched in must be the spatially aligned one — the single
    warp `lap_corner_stats`, `segment_times` and the phase triple already read — not the bare
    normalized scale `d·lap_total/ref_total`. Since ADV-1 that window is `lap_window_inputs`'.

    Fixture (both laps down the same straight line, so the spatial match is exact and the two
    projections are analytic): reference 1000 m, typical lap 1050 m — 5 % line-length drift, ten
    times the gate. The spatial match therefore maps reference metre d onto lap metre d, while the
    normalized projection maps it onto 1.05·d. The one corner is [50, 90] reference, so the gated
    window is [50, 90] and the normalized one [52.5, 94.5] — 4.5 m of daylight at the exit.

    The lap brakes at 92.0 m, which on the track is 2 m PAST the corner exit. Under the warped
    window that application is outside the corner and contributes nothing (⇒ LINE, on the
    corner's own spread). Under the normalized window it lands inside and manufactures 0.125 s of
    "extra braking in the corner" that never happened there (⇒ BRAKING) — a row pairing a
    warp-derived phase triple with a normalized-frame reason, which is the defect."""
    corners, best, times, lap_times = _one_corner_lossy(0.5)  # one corner: enter 50, exit 90
    ref_total, lap_total = 1000.0, 1050.0
    assert corners_mod.line_length_drift(lap_total, ref_total) > 0.005

    # Straight-line traces: (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum), xs == odometer.
    ref_x = np.linspace(0.0, ref_total, 1001)
    lap_x = np.linspace(0.0, lap_total, 1051)
    med_traces = (ref_x, np.zeros_like(ref_x), ref_x, lap_x, np.zeros_like(lap_x), lap_x)
    frame = [50.0, 90.0]

    # FIXTURE GUARD, asked of the real projection (the #286 lesson: a golden/fixture assertion must
    # ask the matcher, not the thing built from it): the two frames really do disagree here.
    align = corners_mod.lap_alignment(frame, ref_total, lap_total, traces=med_traces)
    gated = corners_mod.project_boundaries(frame, ref_total, lap_total, alignment=align)
    normalized = np.array(frame) * (lap_total / ref_total)
    assert abs(float(gated[1]) - 90.0) < 1e-6, gated
    assert float(normalized[1]) - float(gated[1]) > 2.0, (gated, normalized)

    # Constant 20 m/s, so the overlap integral is analytic.
    med_dist, med_elapsed = _flat_trace(0.0, lap_total, 72.0, n=1051)
    med_brakes = [_brake(onset_dist=92.0, onset_time=92.0 / 20.0, duration=0.5)]
    kw = dict(corner_dist_total=ref_total, lap_total=lap_total, frame=frame)
    warped = K.lap_window_inputs(corners, med_brakes, [], med_dist, med_elapsed, align=align, **kw)
    assert warped[0] == [0.0], (
        "the brake application is 2 m past the corner exit on the track; the warped window must "
        f"not count it, got {warped[0]}")
    # The SAME window the phase thirds read: `_project_window` through the same warp.
    assert K._project_window(50.0, 90.0, ref_total, lap_total, frame=frame,
                             alignment=align) == (float(gated[0]), float(gated[1]))
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.03}, **_levers(times, best, brake=warped[0]))
    row = opp.rows[0]
    assert row.reason.brake_extra_s == 0.0 and row.reason.kind == K.REASON_CONSISTENCY, row.reason

    # CONTROL, the normalized projection (align=None): the out-of-corner brake counts.
    normal = K.lap_window_inputs(corners, med_brakes, [], med_dist, med_elapsed, align=None, **kw)
    assert abs(normal[0][0] - 0.125) < 1e-9, normal
    ug = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                     sigmas_by_cid={1: 0.03}, **_levers(times, best, brake=normal[0])).rows[0]
    assert ug.reason.kind == K.REASON_BRAKING, ug.reason
    print(f"ok C1 gated reason window: warped ⇒ {row.reason.kind} "
          f"(brake {row.reason.brake_extra_s:.3f} s), normalized ⇒ {ug.reason.kind} "
          f"(brake {ug.reason.brake_extra_s:.3f} s)")


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
    # no g signal -> the reason falls back to apex (the slow half drops the apex speed) or consistency
    assert top.reason.kind in (K.REASON_APEX, K.REASON_CONSISTENCY), top.reason
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


def _braking_stadium_session():
    """Bare Session on the stadium, WITH a g signal, whose laps brake into corner 2 (the arc
    [494.25, 588.5]) with ONE identical application: 0.5 g from 20 to 12 m/s, onset at 470 m.
    Laps 1-3 then hold a 0.10 g lift-off for 20 m — between the release (theta_b * RELEASE_RATIO)
    and theta_b, so the event stays open through it — and carry the lower speed into the corner;
    the best lap (0) holds 12 m/s. Known by construction: every lap is on the brakes for the same
    application, and the tail is coasting."""
    from _synthetic import bare_session, reset_corner_caches, reset_driving_caches
    from test_corners import elapsed_for, stadium

    from studio import gmeter
    from studio._signal import G

    s = bare_session(valid=[0, 1, 2, 3], best=0)
    s._cols_cache = {}
    xs, ys, cum = stadium()
    app_m = (20.0 ** 2 - 12.0 ** 2) / (2 * 0.5 * G)

    def profile(tail_m):
        v = np.full_like(cum, 20.0)
        app = (cum >= 470.0) & (cum < 470.0 + app_m)
        v[app] = np.sqrt(20.0 ** 2 - 2 * 0.5 * G * (cum[app] - 470.0))
        s1 = 470.0 + app_m
        v[cum >= s1] = 12.0
        tail = (cum >= s1) & (cum < s1 + tail_m)
        v[tail] = np.sqrt(12.0 ** 2 - 2 * 0.10 * G * (cum[tail] - s1))
        v[cum >= s1 + tail_m] = np.sqrt(12.0 ** 2 - 2 * 0.10 * G * tail_m)
        return v

    lap_times, tt, tv = {}, [], []
    for lid, base, tail_m in ((0, 100.0, 0.0), (1, 300.0, 20.0), (2, 460.0, 20.0), (3, 620.0, 20.0)):
        sp = profile(tail_m)
        t = base + elapsed_for(cum, sp)
        s._cols_cache[lid] = (t, xs, ys, sp, cum)
        lap_times[lid] = float(t[-1] - t[0])
        tt.append(t)
        tv.append(sp * 3.6)
    s.tt, s.tv = np.concatenate(tt), np.concatenate(tv)
    s._gmeter = gmeter.GMeter(times=s.tt.copy(), lat_g=np.zeros(len(s.tt)),
                              long_g=np.zeros(len(s.tt)), cross=None, source="accl")
    s.laps = SimpleNamespace(lap_time=lambda i: lap_times[i],
                             start_timestamp=lambda i: float(s._cols_cache[i][0][0]),
                             sectors=SimpleNamespace(sector_lines=[]), laps_count=lambda: 4)
    reset_corner_caches(s)
    reset_driving_caches(s)
    return s


def test_the_brake_reason_counts_time_on_the_brakes_not_the_event_span():
    """K, through the REAL wiring (Session -> DrivingChannels -> coaching.summarize): the coaching
    row's "~X s longer on the brakes" is time ON THE BRAKES — `driving.brake_on`, the quantity the
    Stats page's braking figure counts since #421 — not the brake events' spans.

    Every lap brakes into corner 2 with the same application; the slower laps then lift off at
    0.10 g, which the release hysteresis holds the event open through. The row used to integrate
    the spans and blame the brakes for the lift-off tail ("~1.8 s longer on the brakes", with the
    same tail counted again as coasting). Measured on the owner's recordings the span read 3x the
    braking (0064 chapter 3's C1: 1.70 s for 0.60 s) and moved the reason on three ranked rows."""
    from studio import driving as D

    s = _braking_stadium_session()
    assert abs(s.driving.thresholds().theta_b - D.BRAKE_G_FLOOR) < 1e-9, s.driving.thresholds()
    med = s.coaching_opportunities().median_lap_id
    (ev_med,), (ev_best,) = s.driving.lap_brake_events(med), s.driving.lap_brake_events(0)
    assert ev_med.duration > ev_best.duration + 1.0, (ev_med, ev_best)   # the tail holds it open
    row = next(r for r in s.coaching_opportunities().rows if r.cid == 2)
    assert row.time_lost > 0.5, row
    # Not 0: the coast window smears the application's trailing edge into the tail, and this
    # lap is sampled every 1.5 m, so its 7-sample window spans ~1 s in the slow corner (0.32 s
    # here). The spans read the whole tail.
    assert row.reason.brake_extra_s < 0.5, (
        f"C2's row says ~{row.reason.brake_extra_s:.2f} s longer on the brakes, but every lap is on "
        f"the brakes for the same application: the typical lap's event spans {ev_med.duration:.2f} s "
        f"through its lift-off tail against the best lap's {ev_best.duration:.2f} s")
    assert row.reason.kind == K.REASON_COASTING and row.reason.coast_extra_s > 1.0, row.reason
    print(f"ok brake reason: {row.reason.brake_extra_s:.2f} s longer on the brakes (spans "
          f"{ev_med.duration:.2f} vs {ev_best.duration:.2f} s), reason {row.reason.kind} "
          f"({row.reason.coast_extra_s:.2f} s coasting)")


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
# The dialog half of this file makes 12 geometry assertions (column widths, row heights), all of
# them functions of the FONT — so it runs in the app's real regime, not Qt's default stack. See
# tests/_qtapp.py; W10-03.
_APP = themed_app()


def _qapp():
    return _APP


def _settle(n=6):
    for _ in range(n):
        _APP.processEvents()


def _populated_opps():
    corners = _corners(4)
    best = [5.0, 6.0, 7.0, 4.0]
    # Two laps ON the baseline + three slower — the shape a real session has (the best lap is one
    # of the candidate laps), so the corners are REACHED and survive the per-corner evidence gate.
    # C1 loses 0.6 s, the rest 0.05 s.
    slow = [best[j] + (0.6 if j == 0 else 0.05) for j in range(4)]
    times = [list(best), list(best)] + [list(slow) for _ in range(3)]
    lap_times = [sum(r) for r in times]
    return K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                       sigmas_by_cid={1: 0.05, 2: 0.02, 3: 0.02, 4: 0.02},
                       **_levers(times, best, apex=[95.0, 100.0, 100.0, 100.0]))


_FULL_WINDOW = (1432, 808)   # the lap panel maximized on the 1440x900 default window
_PAGES: list = []            # built pages stay alive for the whole run (a dropped one is deleted)


def _full_window_page(opp, directions=None, size=_FULL_WINDOW):
    """The Coaching page over `opp`, shown and settled at `size` (default: full-window, where the
    width budget gives every optional column its room). The stand-in session offers exactly what a
    real one does for the page: its opportunities and its braking directions (L8 retired the
    braking habit, `coaching_brake_points`, that no Coaching row had printed since L7)."""
    from studio.coaching_panel import OpportunitiesPanel

    class _S:
        def coaching_opportunities(self):
            return opp

        def coaching_brake_direction(self):
            return directions or {}

    page = OpportunitiesPanel(_S())
    _PAGES.append(page)
    page.resize(*size)
    page.show()
    _settle()
    return page


def test_the_full_window_page_has_the_modals_columns_and_jump_emits():
    """R11: the Coaching page replaced the modal that rendered its ranking a second time. Full-window
    it carries that modal's two extra columns — the D2 Entry·Apex·Exit bar and a Jump per row — and
    a Jump emits (cid, entry_dist) for the app to act on. The header's hover says what the modal's
    title used to — since COACHING-2 that the reasons AND the bars are medians over the clean laps,
    so it names no lap (it named the typical lap, as the 1-based lap NUMBER, M9)."""
    _qapp()
    from studio.coaching_panel import (
        _PANEL_COL_GO,
        _PANEL_COL_PHASES,
        _PANEL_COL_REACH,
        _PANEL_COL_REASON,
        PhaseBar,
    )
    opp = _populated_opps()
    page = _full_window_page(opp)
    t = page.table
    assert t.rowCount() == len(opp.rows)
    assert not any(t.isColumnHidden(c) for c in (_PANEL_COL_REACH, _PANEL_COL_PHASES,
                                                  _PANEL_COL_GO)), "full-window shows every column"
    assert t.item(0, 0).text().startswith(f"C{opp.rows[0].cid}")
    assert t.item(0, 1).text() == f"+{opp.rows[0].time_lost:.2f} s"
    ev = opp.rows[0].evidence
    assert t.item(0, _PANEL_COL_REACH).text() == f"{ev.reach_laps} of {ev.n_laps} laps", \
        t.item(0, _PANEL_COL_REACH).text()
    assert isinstance(t.cellWidget(0, _PANEL_COL_PHASES), PhaseBar)
    assert "apex speed" in t.item(0, _PANEL_COL_REASON).text()
    tip = page.summary_label.toolTip()
    assert "the Entry·Apex·Exit bars are medians over your clean laps." in tip, tip
    assert not re.search(r"\blap \d", tip), f"the hover names a lap: {tip}"
    calls = []
    page.jump_requested.connect(lambda c, d: calls.append((c, d)))
    go = t.cellWidget(0, _PANEL_COL_GO)
    assert not go.autoDefault(), "B10: focused-default styling repainted the arrow amber-on-amber"
    go.click()
    assert calls == [(opp.rows[0].cid, opp.rows[0].entry_dist)], calls
    print(f"ok page: {t.rowCount()} rows full-window, bars + Jump shown, Jump -> {calls[0]}")


def _direction(cid, rho, p=0.01, n_laps=20, family=1, p_holm=None):
    """A coaching.BrakeDirection; a family of one by default, where Holm's p IS the raw p."""
    return K.BrakeDirection(cid=cid, n_laps=n_laps, rho=rho, p=p, family=family,
                            p_holm=p if p_holm is None else p_holm)


def test_the_braking_direction_is_a_rank_test_over_the_laps():
    """L7: `brake_direction` is Spearman ρ of the onset against the time through the corner, with
    a seeded two-sided permutation p. Pinned on laps whose answer is known by construction."""
    rng = np.random.default_rng(7)
    n = 24
    onsets = np.sort(rng.uniform(80.0, 110.0, n))
    # Later onset -> quicker pass, monotone: ρ is exactly −1 and no shuffle of 24 laps reaches it.
    quick = K.brake_direction(3, onsets, 6.0 - 0.01 * onsets)
    assert quick.rho == -1.0 and quick.n_laps == n, quick
    assert quick.p == 1.0 / (K.BRAKE_DIRECTION_DRAWS + 1.0), quick.p
    assert quick.verdict == K.BRAKE_LATER
    # The mirror image says "earlier": the line can point either way.
    slow = K.brake_direction(3, onsets, 5.0 + 0.01 * onsets)
    assert slow.rho == 1.0 and slow.verdict == K.BRAKE_EARLIER, slow
    # Unrelated times: no verdict, and the same call renders the same p (seeded per corner).
    noise = rng.permutation(np.linspace(5.0, 6.0, n))
    a, b = K.brake_direction(3, onsets, noise), K.brake_direction(3, onsets, noise)
    assert a == b and a.p > K.BRAKE_DIRECTION_ALPHA and a.verdict is None, a
    # Ties take their mean rank (Spearman's convention), and a constant side reads ρ 0, p 1.
    tied = K.brake_direction(3, [1.0, 1.0, 2.0, 3.0], [4.0, 3.0, 2.0, 1.0])
    assert abs(tied.rho - float(np.corrcoef([0.5, 0.5, 2.0, 3.0], [3.0, 2.0, 1.0, 0.0])[0, 1])) < 1e-12
    assert K.brake_direction(3, onsets, np.full(n, 5.5)) == K.BrakeDirection(3, n, 0.0, 1.0, 1, 1.0)
    # The braking habit's floor: a corner with fewer pairs is absent, not tested — and not counted
    # in the family either, so the one corner left is a family of one.
    pairs = {1: list(zip(onsets[:K.MIN_BRAKE_LAPS - 1], noise[:K.MIN_BRAKE_LAPS - 1], strict=True)),
             2: list(zip(onsets, 6.0 - 0.01 * onsets, strict=True))}
    got = K.brake_directions(pairs)
    assert set(got) == {2} and got[2].verdict == K.BRAKE_LATER, got
    assert (got[2].family, got[2].p_holm) == (1, got[2].p), got[2]
    print(f"ok L7 rank test: ρ −1 -> later (p {quick.p:.5f}), ρ +1 -> earlier, noise p {a.p:.3f}")


def test_the_line_is_corrected_for_every_corner_the_recording_tested():
    """L7: choosing WHERE to speak is a search over the recording's corners, so a line needs its
    corner to survive Holm's step-down over every corner tested there, at BRAKE_DIRECTION_ALPHA.
    (refused-2026-09.md §16 lists the working-set corners that fire uncorrected; coaching.py's table
    has the ones that survive.)"""
    # Holm on a textbook family: all four raw p are under 0.05; two survive the correction.
    got = K.holm_adjust([0.01, 0.04, 0.03, 0.005])
    assert all(abs(g - w) < 1e-12 for g, w in zip(got, [0.03, 0.06, 0.06, 0.02], strict=True)), got
    assert K.holm_adjust([0.6, 0.7]) == [1.0, 1.0] and K.holm_adjust([]) == []
    # A corner whose laps separate a direction when tested ALONE ...
    n = 20
    on = np.arange(n, dtype=float)
    moderate = list(zip(on, -on + np.random.default_rng(4).normal(0.0, 9.0, n), strict=True))
    alone = K.brake_directions({2: moderate})[2]
    assert alone.p < K.BRAKE_DIRECTION_ALPHA and alone.verdict == K.BRAKE_LATER, alone
    # ... prints nothing among the seven a recording tested, while a decisive one still does.
    family = {1: list(zip(on, -on, strict=True)), 2: moderate}
    for c in range(3, 8):
        family[c] = list(zip(on, np.random.default_rng(100 + c).permutation(n).astype(float),
                             strict=True))
    got = K.brake_directions(family)
    assert all(d.family == 7 for d in got.values()), got
    assert got[2].p == alone.p and got[2].p_holm >= K.BRAKE_DIRECTION_ALPHA, got[2]
    assert got[2].verdict is None and K.brake_direction_line(got[2]) is None, got[2]
    assert got[1].verdict == K.BRAKE_LATER and got[1].p_holm < K.BRAKE_DIRECTION_ALPHA, got[1]
    assert [c for c, d in got.items() if d.verdict] == [1], got
    # The verdict reads the CORRECTED p: an uncorrected pass alone prints nothing.
    assert _direction(3, -0.40, p=0.016, family=7, p_holm=0.1113).verdict is None
    print(f"ok L7 Holm: C2 alone p {alone.p:.4f} -> later; among 7, p Holm {got[2].p_holm:.4f} -> "
          f"nothing; the decisive C1 still fires (p Holm {got[1].p_holm:.4f})")


def test_the_braking_line_is_a_direction_and_a_count_never_metres():
    """L7: the row's braking line is words and a lap count, only where the laps separate a
    direction at BRAKE_DIRECTION_ALPHA once corrected for the recording's corners — and carries no
    metres and no (est) mark: it is measured."""
    from studio import theme
    assert (K.brake_direction_line(_direction(3, -0.40, n_laps=36))
            == "Braking later went with quicker passes here (36 laps)")
    assert (K.brake_direction_line(_direction(3, 0.40, n_laps=17))
            == "Braking earlier went with quicker passes here (17 laps)")
    # Not separated from chance, or no association at all: no line, rather than a default.
    assert K.brake_direction_line(_direction(3, -0.40, p=K.BRAKE_DIRECTION_ALPHA)) is None
    assert K.brake_direction_line(_direction(3, -0.40, p=0.016, family=7, p_holm=0.11)) is None
    assert K.brake_direction_line(_direction(3, 0.0, p=0.0)) is None
    assert K.brake_direction_line(None) is None
    line = K.brake_direction_line(_direction(3, -0.9))
    assert line is not None and theme.ESTIMATED_MARK not in line and " m " not in line, line
    print("ok L7 line: 'Braking later/earlier went with quicker passes here (N laps)', else None")


def test_the_page_prints_no_braking_metres_only_the_direction_where_it_holds():
    """L7: no Coaching row prints the ESTIMATED "Brake ~N m later" any more — it said "later" at 33
    of 33 working-set corners by construction (and since L8 the session no longer computes the
    habit it read). A row whose laps separate a direction says so with its count; a row whose laps
    do not says nothing; and the first-open debrief carries neither line."""
    _qapp()
    from studio.coaching_panel import _PANEL_COL_REASON
    opp = _populated_opps()
    assert opp.rows[0].evidence.ranked and len(opp.rows) > 1, opp.rows
    top, other = opp.rows[0].cid, opp.rows[1].cid
    # The top row has 0064 C6's numbers (it survives Holm among 7 corners), the other 0068 C3's
    # (p 0.016 uncorrected, 0.111 once corrected) — which is exactly the line that must NOT print.
    directions = {top: _direction(top, -0.419, p=0.0019, n_laps=57, family=7, p_holm=0.0133),
                  other: _direction(other, -0.399, p=0.0159, n_laps=36, family=7, p_holm=0.1113)}
    page = _full_window_page(opp, directions)
    cells = [page.table.item(r, _PANEL_COL_REASON) for r in range(page.table.rowCount())]
    for cell in cells:
        text = cell.text()
        assert "Brake ~" not in text and " m later" not in text and "(est)" not in text, text
    assert cells[0].text().endswith("\nBraking later went with quicker passes here (57 laps)"), \
        cells[0].text()
    tip = cells[0].toolTip()
    assert "over the 57 clean laps" in tip and "ρ −0.42" in tip and "p = 0.002" in tip, tip
    assert "still p = 0.013 once corrected for all 7 corners tested on this recording" in tip, tip
    assert "Braking" not in cells[1].text(), "an uncorrected pass the family does not keep prints nothing"
    page.set_debrief(True, None, [])
    _settle()
    assert not any("Braking" in page.table.item(r, _PANEL_COL_REASON).text()
                   for r in range(page.table.rowCount())), "the debrief carries no braking line"
    print("ok L7 page: no metres on any row; the direction line where the laps hold it, with n")


def test_full_window_reason_column_outweighs_the_bars_and_jump():
    """Full-window the page carries the retired modal's two extra columns (the fixed 150-px
    Entry·Apex·Exit bar + the per-row Jump), and the stretch reason column must still be the widest
    and hold real room — asserted as its SHARE of the viewport, which no font change can move (the
    W10-03 lesson from the modal's own version of this test: measure a SHOWN, laid-out table)."""
    _qapp()
    from studio.coaching_panel import _PANEL_COL_GO, _PANEL_COL_PHASES, _PANEL_COL_REASON
    page = _full_window_page(_populated_opps())
    t = page.table
    assert not t.isColumnHidden(_PANEL_COL_PHASES) and not t.isColumnHidden(_PANEL_COL_GO)
    widths = [0 if t.isColumnHidden(c) else t.columnWidth(c) for c in range(t.columnCount())]
    reason_px, viewport_px = widths[_PANEL_COL_REASON], t.viewport().width()
    assert reason_px == max(widths), f"the prose column must be the widest one: {widths}"
    assert reason_px >= 0.4 * viewport_px, (
        f"the reason column holds only {reason_px}px of a {viewport_px}px viewport")
    assert not t.horizontalScrollBar().isVisible(), "the full-window columns must fit the viewport"
    print(f"ok page: reason column {reason_px}px of a {viewport_px}px viewport beside the bars + "
          "Jump")


def test_m4_phasebar_tooltip_does_not_claim_time_lost_and_guards_sign_flip():
    """M4: the Entry·Apex·Exit PhaseBar is a WHERE-in-the-corner Δt PROFILE, a DIFFERENT statistic
    from the row's "Time lost". Since COACHING-2 each third is its own median over the laps, so the
    bar states NO sum at all — three medians add up to no lap's net and not to Time lost — and
    never says "net faster" (the claim nothing measured). Its tooltip says what the three are and
    still names the slowest third; the face and the tooltip name no lap."""
    _qapp()
    from PySide6.QtWidgets import QLabel

    from studio.coaching_panel import PhaseBar
    # The C12-shaped case: the entry third's median faster, the other two slower.
    flip = K.PhaseLoss(entry=-0.30, apex=0.05, exit=0.04)   # sum = -0.21
    pos = K.PhaseLoss(entry=0.05, apex=0.20, exit=0.03)     # sum +0.28, apex slowest
    for ph, sum_text in ((flip, "0.21"), (pos, "0.28")):
        bar = PhaseBar(ph)
        tip = bar.toolTip()
        text = " ".join([tip] + [lb.text() for lb in bar.findChildren(QLabel)])
        assert "need not add up to the row's Time lost" in tip, tip
        assert sum_text not in text, f"the bar states the thirds' sum: {text}"
        assert "net" not in text.lower(), f"the bar claims a net: {text}"
        assert not re.search(r"\blap \d", text), f"the bar names a lap: {text}"
        assert "Time lost vs your best lap, split" not in tip, ("old 'time lost' label", tip)
    assert "Slowest third: apex." in PhaseBar(pos).toolTip()
    assert "Slowest third: apex." in PhaseBar(flip).toolTip()   # the apex median is the largest
    print("ok M4: phase-bar states three medians, no sum, no net, no lap")


def test_l2_zero_rounding_rows_are_not_shown_opportunities():
    """L2: summarize keeps the internal 1e-9 ranking (golden fingerprint + share card), but the
    DISPLAYED opportunity lists (dialog + panel) drop rows whose time_lost rounds to "+0.00 s" at
    the shown 2-dp resolution (< 0.005 s) — no informationless row with a live Jump button."""
    _qapp()
    from studio.coaching_panel import (
        DISPLAY_MIN_LOST_S,
        OpportunitiesPanel,
        _shown_rows,
    )
    corners = _corners(4)
    best = [5.0, 6.0, 7.0, 4.0]
    # Two REAL losers (C1 ~0.30 s, C3 ~0.10 s) and two sub-resolution "losers" (C2 0.0043 s,
    # C4 0.0026 s) that summarize still ranks (> 1e-9) but that round to +0.00 s on screen.
    plan = [0.30, 0.0043, 0.10, 0.0026]
    times, lap_times = _plan_times(best, plan)
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.05, 2: 0.02, 3: 0.02, 4: 0.02},
                      **_levers(times, best, apex=[95.0, 100.0, 100.0, 100.0]))
    # summarize still RANKS all four (the internal 1e-9 threshold is unchanged).
    assert [r.cid for r in opp.rows] == [1, 3, 2, 4], [r.cid for r in opp.rows]
    # but the shown rows drop the two that round to +0.00 s.
    shown = _shown_rows(opp)
    assert [r.cid for r in shown] == [1, 3], [r.cid for r in shown]
    assert all(r.time_lost >= DISPLAY_MIN_LOST_S for r in shown)
    # PANEL: same filter — the "+0.00 s" corners never appear, and the summary counts only shown rows.
    class _S:
        def coaching_opportunities(self):
            return opp

        def coaching_brake_direction(self):
            return {}

    panel = OpportunitiesPanel(_S())
    assert panel.body.currentIndex() == 0
    assert panel.table.rowCount() == 2, panel.table.rowCount()
    panel_lost = [panel.table.item(r, 1).text() for r in range(panel.table.rowCount())]
    assert all(t != "+0.00 s" for t in panel_lost), panel_lost
    print("ok L2: sub-0.005 s rows ranked internally but dropped from the shown dialog + panel")


def test_p1_summary_grammar_by_count():
    """P1: the panel's headline summary phrases by COUNT — one shown corner reads "in your worst
    corner" (never the ungrammatical "across the top 1"); several read "across your top N corners".

    Declutter PR: the panel now ships COLLAPSED by default, so its summary label leads with a
    "Coaching · " prefix (the thin header is a self-labelling re-open affordance). The COUNT phrasing
    is unchanged and is what we assert as a substring."""
    _qapp()
    from studio.coaching_panel import OpportunitiesPanel
    corners = _corners(3)
    best = [5.0, 6.0, 7.0]

    def _panel_for(plan):
        times, lap_times = _plan_times(best, plan)
        o = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                        sigmas_by_cid={1: 0.05, 2: 0.02, 3: 0.02})

        class _S:
            def coaching_opportunities(self):
                return o

            def coaching_brake_direction(self):
                return {}

        return OpportunitiesPanel(_S())

    # exactly ONE shown corner -> "in your worst corner", NOT "across the top 1".
    p1 = _panel_for([0.30, 0.0, 0.0])   # keep a ref so Qt doesn't GC the C++ label
    one = p1.summary_label.text()
    assert "in your worst corner" in one and "across the top 1" not in one, one
    # several shown corners -> "across your top N corners".
    p3 = _panel_for([0.30, 0.20, 0.10])
    many = p3.summary_label.text()
    assert "across your top 3 corners" in many, many
    print(f"ok P1: 1-corner => '{one}'; many => '{many}'")


# ------------------------------------------- the PERSISTENT top-3 panel (the coaching front-door)
def test_panel_renders_top3_off_a_session():
    """The always-on OpportunitiesPanel reads the session's coaching_opportunities() directly and
    renders the ranked rows (corner · time lost · done-it? · reason), the SAME data the modal dialog
    shows. On the stadium session (4 clean laps, the far corner losing time) it shows the ranked
    rows, leads with the slow corner, and the body is on the table page (not the excluded state).

    L5-08: PANEL_TOP_N is the FLOOR, not a ceiling — the page shows at least the shortlist and then
    as much of the ranking as its viewport holds, so assert the band, not a literal 3."""
    _qapp()
    from studio.coaching_panel import PANEL_TOP_N, OpportunitiesPanel
    s = _stadium_session()
    opp = s.coaching_opportunities()
    assert opp.enough and opp.rows, "fixture must produce real opportunities"
    panel = OpportunitiesPanel(s)
    assert panel.body.currentIndex() == 0, "enough laps -> the table page, not the excluded state"
    n = min(len(opp.rows), PANEL_TOP_N)
    assert n <= panel.table.rowCount() <= len(opp.rows), (panel.table.rowCount(), n, len(opp.rows))
    # Row 0 is the top-ranked corner (the far corner, cid 2), with the +time-lost format.
    assert panel.table.item(0, 0).text().startswith(f"C{opp.rows[0].cid}")
    assert panel.table.item(0, 1).text() == f"+{opp.rows[0].time_lost:.2f} s"
    # col 2: "have you already done this?" as the count over its denominator, no verdict word
    # (LOOK-9) — the same cell the dialog builds, from the same shared builder.
    ev = opp.rows[0].evidence
    assert panel.table.item(0, 2).text() == f"{ev.reach_laps} of {ev.n_laps} laps", \
        panel.table.item(0, 2).text()
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

    from studio.coaching_panel import _PANEL_COL_REASON, OpportunitiesPanel, _fit_reason_rows
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
                   "best through the slowest point.\n"
                   "Braking later went with quicker passes here (36 laps)")
    panel.table.item(0, 3).setText(long_reason)
    # L5-03: row heights come from the panel's own fit pass now (it measures the width the delegate
    # PAINTS into, which a bare resizeRowsToContents does not) — drive the same call the panel does.
    _fit_reason_rows(panel.table, _PANEL_COL_REASON)
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
    msg = panel.empty_state.text().lower()
    assert "clean" in msg and "lap" in msg, msg
    assert str(K.MIN_LAPS) in panel.empty_state.text(), "the friendly state names the lap minimum"
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


def test_panel_is_a_full_uncapped_page_with_headline():
    """Tabbed-panel PR: the coaching panel is a FULL tab page now — no collapse machinery, no
    height cap (the old strip's body was clamped to 132 px; its entire splitter drag range was
    68 px). The body is always visible, takes the page's height, and the headline strip reads
    the plain summary (no "Coaching · " re-open prefix — the tab bar names the page)."""
    _qapp()
    from studio.coaching_panel import OpportunitiesPanel
    s = _stadium_session()
    panel = OpportunitiesPanel(s)
    assert panel.body.isVisibleTo(panel), "the body is always visible (no collapse state)"
    assert panel._header.isVisibleTo(panel), "the headline strip frames the page"
    assert panel.body.maximumHeight() > 10_000, "the old 132 px cap must be gone"
    assert panel.body.minimumHeight() == 0, "…and the old 64 px floor with it"
    assert not hasattr(panel, "collapse_btn"), "no chevron — a tab you leave costs nothing"
    txt = panel.summary_label.text()
    assert txt and not txt.startswith("Coaching · "), txt
    assert "corner" in txt, "the headline reads the summary sentence"
    print("ok panel: full uncapped page, plain headline, no collapse machinery")


def test_ia01_panel_headline_names_its_session_scope():
    """IA-01: the Coaching page is SESSION-scoped — `coaching_opportunities()` takes no lap, so it
    cannot re-scope to your selection the way the sibling Corners tab does (which renames itself
    "Corners · L6"). Its headline therefore has to SAY so, or the number reads as the selected lap's
    and understates it (on D24 lap 6: "0.21 s" beside the lap's own +2.08 s). Pin that the headline
    leads with the scope AND carries the sample it is a median over, and that the tooltip says
    plainly that these rows do not follow the selection."""
    _qapp()
    from studio.coaching_panel import _SCOPE_PREFIX, OpportunitiesPanel
    s = _stadium_session()
    opp = s.coaching_opportunities()
    assert opp.enough and opp.rows, "fixture must produce real opportunities"
    panel = OpportunitiesPanel(s)
    txt = panel.summary_label.text()
    assert txt.startswith(_SCOPE_PREFIX), f"the headline must LEAD with the scope: {txt!r}"
    assert f"median of {opp.n_laps} clean lap" in txt, f"…and carry its own denominator: {txt!r}"
    tip = panel.summary_label.toolTip().lower()
    assert "whole session" in tip and "do not follow the lap you select" in tip, tip
    # The rows are a pure function of the session: nothing about a lap selection is an input, so a
    # panel built twice off the same session is identical — the honest label is the whole fix.
    again = OpportunitiesPanel(s)
    assert again.summary_label.text() == txt, "session-scoped => stable headline"
    print(f"ok IA-01: headline scoped => {txt!r}")


def test_l5_03_reason_row_is_tall_enough_for_the_painted_wrap():
    """L5-03: the row height must cover the wrap the delegate PAINTS, not the wider one it measures.
    QTableWidget sizes rows from the delegate's sizeHint, which the stylesheet style computes at the
    full section width and then ADDS `QTableWidget::item {padding: 4px 8px}` to, while the paint pass
    DEDUCTS that padding — so a sentence that fits the section but not the text rect is measured as
    one line and painted as two, silently dropping the row's "(est)" brake line.

    Asserted on rowHeight vs the painter's own wrapped boundingRect — NEVER on an elide probe, which
    reports this exact cell as *not* elided (a false negative: the text is dropped, not clipped)."""
    _qapp()
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    from studio.coaching_panel import _PANEL_COL_REASON, OpportunitiesPanel, _fit_reason_rows
    panel = OpportunitiesPanel(_stadium_session())
    # The cause is the app's own cell padding (theme.py's `QTableView::item, QTableWidget::item
    # {padding: 4px 8px}`), which the sizeHint adds and the painter deducts. Apply that one rule to
    # THIS widget only — the suite shares a QApplication, so setting the app stylesheet here would
    # silently change every later test's metrics.
    panel.setStyleSheet("QTableWidget::item { padding: 4px 8px; }")
    panel.resize(515, 320)          # the app's own default left-column width
    panel.layout().activate()
    t, col = panel.table, _PANEL_COL_REASON

    # The width the delegate really paints into (QSS padding included), from the style itself.
    opt = QStyleOptionViewItem()
    opt.initFrom(t)
    opt.features = QStyleOptionViewItem.HasDisplay
    opt.rect = QRect(0, 0, t.columnWidth(col), 100)
    paint_w = t.style().subElementRect(QStyle.SE_ItemViewItemText, opt, t).width()
    assert 0 < paint_w < t.columnWidth(col), (paint_w, t.columnWidth(col))
    fm = t.fontMetrics()

    # Craft the defect: a first sentence that FITS the width Qt MEASURES but not the one it PAINTS,
    # plus the braking line that used to disappear. Search for that band rather than assume its
    # width — Qt's own measuring inset is a style detail, the 2-16 px gap is the point.
    head = "brake later / shorter"
    while fm.horizontalAdvance(head) <= paint_w:
        head += " and a little more"
    naive = need = 0
    while len(head) > 24:
        head = head[:-1]
        text = f"{head}\nBraking later went with quicker passes here (17 laps)"
        t.item(0, col).setText(text)
        t.item(0, col).setData(Qt.SizeHintRole, None)   # Qt's own answer, unpinned
        t.resizeRowsToContents()
        naive = t.rowHeight(0)
        need = fm.boundingRect(QRect(0, 0, paint_w, 0), Qt.TextWordWrap, text).height()
        if naive < need:
            break
    assert naive < need, "the defect band must exist between the measured and painted widths"

    _fit_reason_rows(t, col)
    assert t.rowHeight(0) >= need, (
        f"the reason row must fit its PAINTED wrap: {t.rowHeight(0)}px < {need}px")
    assert t.rowHeight(0) > naive, "…and that must be more than Qt's own short answer"
    # Every other row stays fitted too (and is not gratuitously grown).
    for r in range(t.rowCount()):
        own = fm.boundingRect(QRect(0, 0, paint_w, 0), Qt.TextWordWrap,
                              t.item(r, col).text()).height()
        assert t.rowHeight(r) >= own, (r, t.rowHeight(r), own)
    print(f"ok L5-03: row0 {naive}px -> {t.rowHeight(0)}px for a {need}px painted wrap")


def test_l5_05_all_faster_phase_bar_is_visible_not_a_border_sliver():
    """L5-05: a corner whose typical lap is FASTER than best through all three thirds used to paint
    three `C.border` slivers — 1.19:1 against the row — so a row headlined "+0.08 s" (a cross-lap
    median, a different statistic) looked self-contradictory with nothing on screen to reconcile it
    but a tooltip. The thirds now take the palette's ahead colour, are sized by |Δt|, and the row
    face says so in the ahead hue. Since COACHING-2 the thirds are medians over the laps and the
    face names that statistic, never a lap or a net (a sum of three medians is neither), while the
    tooltip reconciles it with Time lost in words."""
    _qapp()
    from PySide6.QtWidgets import QLabel

    from studio import theme
    from studio.coaching_panel import PhaseBar
    from studio.theme import C
    bar = PhaseBar(K.PhaseLoss(entry=-0.0283, apex=-0.0441, exit=-0.0207))   # the measured C12 row
    segs = bar.layout().itemAt(0).layout()
    widths, colours = [], []
    for i in range(segs.count()):
        colours.append(segs.itemAt(i).widget().styleSheet())
        widths.append(segs.stretch(i))   # the proportional-bar stretch == the third's |Δt| share
    ahead = theme.ahead_colour()
    assert all(ahead in c for c in colours), f"faster thirds must be the ahead hue, got {colours}"
    assert all(C.border not in c for c in colours), f"never the 1.19:1 border grey: {colours}"
    # sized by |Δt|: the apex (biggest |Δ|) takes the widest stretch, the exit the narrowest.
    assert widths[1] > widths[0] > widths[2], widths
    # …and the all-quicker state is on the row FACE, not only in the tooltip. VIEW-9/JOURNEY-8:
    # the face names WHOSE numbers the bars are — the laps' medians, not a lap's, with no number of
    # its own to be read against the row's Time lost.
    face = [lb for lb in bar.findChildren(QLabel) if lb.text() == PhaseBar.FACE]
    assert len(face) == 1 and ahead in face[0].styleSheet(), [lb.text() for lb in face]
    assert "Every third's median is quicker than your best lap's" in bar.toolTip(), bar.toolTip()
    assert "net" not in bar.toolTip().lower(), bar.toolTip()
    # A row with a slower third keeps the face muted: the ahead hue means "every third quicker".
    mixed_bar = PhaseBar(K.PhaseLoss(-0.03, 0.02, -0.01))
    mixed = [lb for lb in mixed_bar.findChildren(QLabel) if lb.text() == PhaseBar.FACE]
    assert mixed and ahead not in mixed[0].styleSheet(), mixed[0].styleSheet()
    face = [face[0].text()]
    print(f"ok L5-05: faster thirds {ahead} sized {widths}, row face says {face[0]!r}")


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


# =============================================== spread, reach and the per-corner evidence gate
# The two questions `time_lost` alone cannot answer: is this corner something the driver has ALREADY
# DONE (execution) or has never done (pace), and does the row carry a claim at all.

def _evidence_for(times, target, loss=None):
    """corner_evidence over one corner's per-lap times (loss defaults to median − target)."""
    med = float(np.median(times))
    return K.corner_evidence(times, target, med - target if loss is None else loss)


def test_reach_tells_a_repeated_target_from_a_rare_one():
    """The FEATURE, in one assertion: two corners with the SAME time_lost and the SAME dominant
    reason get DIFFERENT instructions, because one is a pace the driver produces routinely and the
    other is one they have hit twice in twenty.

    A ranking built on median-minus-best cannot distinguish them — that is the whole complaint the
    reviewer of a rival product was making — so the distinction has to come from the distribution
    the median was taken over, which is the only place it exists."""
    target, loss, n = 5.0, 0.20, 25
    # REPEAT: 6 of 25 laps at the target, the rest 0.20 s slower.
    repeat = [target] * 6 + [target + loss] * (n - 6)
    # RARE: 2 of 25 at the target — the same median, the same loss, under 1 lap in 10.
    rare = [target] * 2 + [target + loss] * (n - 2)
    ev_r, ev_n = _evidence_for(repeat, target, loss), _evidence_for(rare, target, loss)
    assert ev_r.reach == K.REACH_REPEAT and ev_r.reach_laps == 6, ev_r
    assert ev_n.reach == K.REACH_RARE and ev_n.reach_laps == 2, ev_n
    # ...and the SENTENCE changes with it, which is the point — same lever, opposite instruction.
    def _sentence(ev):
        return K.reason_sentence(K.Opportunity(
            cid=1, direction=1, time_lost=loss, entry_dist=0.0,
            reason=K.Reason(kind=K.REASON_APEX, contribution=0.1, apex_speed_deficit=3.0,
                            brake_extra_s=0.0, coast_extra_s=0.0, sigma=0.1), evidence=ev))
    s_r, s_n = _sentence(ev_r), _sentence(ev_n)
    # LOOK-9 (QA 2026-09-26): the COUNT carries it, in one form either side of REACH_REPEAT_FRAC;
    # the "already"/"rarely" verdict flipped at one lap (2/19 vs 1/19 on MK_18_09).
    assert "6 of 25 laps matched your best lap here" in s_r, s_r
    assert "2 of 25 laps matched your best lap here" in s_n, s_n
    assert s_r != s_n
    print(f"ok reach: repeat => {s_r!r}\n           rare   => {s_n!r}")


def test_a_claim_inside_the_corners_own_spread_abstains():
    """The gate that fires on real data. A corner whose middle half of laps spans 0.40 s cannot be
    aimed at with a 0.05 s median claim, however real that 0.05 s is — and the row says so instead
    of printing a lever.

    Measured on the two working-set recordings (coaching.py's evidence table, T16b): sigma >=
    time_lost on 11 of the 12 shown rows (worst 81.5x), and this test's shape is the smallest
    reproduction of it."""
    target = 5.0
    times = [target, target, target, target + 0.4, target + 0.4, target + 0.4]
    ev = _evidence_for(times, target, 0.05)
    assert ev.abstain == K.ABSTAIN_SPREAD, ev
    assert not ev.ranked and ev.iqr > 0.2, ev
    opp = K.Opportunity(cid=4, direction=1, time_lost=0.05, entry_dist=0.0,
                        reason=K.Reason(kind=K.REASON_BRAKING, contribution=0.05,
                                        apex_speed_deficit=0.0, brake_extra_s=0.4,
                                        coast_extra_s=0.0, sigma=0.2), evidence=ev)
    sentence = K.reason_sentence(opp)
    assert sentence.startswith("Not ranked:"), sentence
    assert "brake" not in sentence.lower(), ("an abstained row must not print a lever — the "
                                             "collapse-to-a-default is what the gate prevents",
                                             sentence)
    assert "0.05 s" in sentence and "spread" in sentence, sentence
    # ...and a claim that DOES clear the spread keeps its lever.
    ok = K.corner_evidence(times, target, 0.30)
    assert ok.ranked and ok.abstain == K.ABSTAIN_NONE, ok
    print(f"ok abstain-spread: {sentence!r}; a 0.30 s claim on the same corner stays ranked")


def test_an_unreplicated_target_and_a_thin_corner_both_abstain():
    """The other two evidence tests, and the honest note about one of them.

    ONE_OFF — nothing but the baseline itself ever reached the target — is the test the brief was
    built around, and it fired on 0 of the 12 rows across both working-set recordings: the ranking's
    baseline is the BEST LAP's time through the corner, and on both recordings at least one OTHER
    lap beats it on every shown row — at least two OTHER laps on every one of them (2..25 of
    them). It does fire on five of the five single chapters, and a short session can trivially
    produce it. FEW_LAPS guards the ragged case the session-level MIN_LAPS gate cannot see (a corner
    only some laps project onto)."""
    one_off = K.corner_evidence([5.0, 5.4, 5.5, 5.6, 5.7], 5.0, 0.5)
    assert one_off.abstain == K.ABSTAIN_ONE_OFF and one_off.reach_laps == 1, one_off
    assert "no second lap" in K.abstain_sentence(
        K.Opportunity(cid=1, direction=1, time_lost=0.5, entry_dist=0.0,
                      reason=K.Reason(K.REASON_APEX, 0.1, 3.0, 0.0, 0.0, 0.1),
                      evidence=one_off))
    thin = K.corner_evidence([5.0, 5.4], 5.0, 0.2)
    assert thin.abstain == K.ABSTAIN_FEW_LAPS, thin
    empty = K.corner_evidence([], 5.0, 0.2)
    assert empty.abstain == K.ABSTAIN_FEW_LAPS and empty.n_laps == 0, empty
    # An Opportunity built with no per-lap times at all is UNMEASURED, not gated out — every
    # pre-existing caller and fixture must behave exactly as it did before the gate existed.
    bare = K.Opportunity(cid=1, direction=1, time_lost=0.5, entry_dist=0.0,
                         reason=K.Reason(K.REASON_APEX, 0.1, 3.0, 0.0, 0.0, 0.1))
    assert bare.evidence.ranked and bare.evidence.reach == K.REACH_UNKNOWN
    assert K.reach_clause(bare) == "" and "apex speed" in K.reason_sentence(bare)
    print("ok abstain: one-off / too-few-laps gate; an unmeasured row is untouched")


def test_a_ranked_row_has_always_been_reached_so_never_is_only_ever_an_abstain():
    """The invariant the ranked sentence is written against. "Reached" means a clean lap matched
    the target, ranking needs MIN_REACH_LAPS of them, so a ranked row is REPEAT, RARE or (unmeasured)
    UNKNOWN — never NEVER. `reach_clause` and `theme_actions` only ever see ranked rows, which is why
    neither carries a "no lap has matched this" wording: that sentence belongs to the abstain.
    Swept over random corners, including targets no lap reaches and corners with too few laps."""
    rng = np.random.default_rng(20260915)
    nevers = 0
    for _ in range(4000):
        n = int(rng.integers(0, 12))
        times = rng.normal(5.0, 0.3, n).tolist()
        target = float(rng.normal(5.0, 0.6))
        ev = K.corner_evidence(times, target, float(abs(rng.normal(0.2, 0.3))))
        if ev.ranked:
            assert ev.reach_laps >= K.MIN_REACH_LAPS and ev.reach != K.REACH_NEVER, ev
        if ev.reach == K.REACH_NEVER:
            nevers += 1
            assert not ev.ranked and ev.abstain in (K.ABSTAIN_FEW_LAPS, K.ABSTAIN_ONE_OFF), ev
    assert nevers > 100, nevers
    print(f"ok invariant: {nevers} NEVER corners, every one abstained")


def test_abstained_rows_sink_below_the_ranked_ones_and_are_never_summed():
    """Order + arithmetic. An abstained corner is SHOWN (never silently dropped — a row that says
    why it is not ranked is worth more than a missing row), but it sits below every row that
    carries a claim, and nothing sums it into a "time available" figure."""
    _qapp()
    from studio.coaching_panel import OpportunitiesPanel, _ranked_shown, _shown_rows
    corners = _corners(2)
    best = [5.0, 6.0]
    # C1: a 0.50 s claim over a 0.375 s interquartile spread — aimable. C2: a 0.025 s claim over a
    # 0.31 s spread — real, and inside the driver's own scatter, so there is nothing to aim at.
    times = [[5.0, 6.0], [5.0, 6.0], [5.5, 6.0], [5.5, 6.05], [5.5, 6.4], [5.5, 6.4]]
    lap_times = [sum(r) for r in times]
    opp = K.summarize(corners, list(range(len(lap_times))), lap_times, times, best,
                      sigmas_by_cid={1: 0.25, 2: 0.20},
                      **_levers(times, best, apex=[96.0, 100.0]))
    kinds = {r.cid: r.evidence.abstain for r in opp.rows}
    assert kinds.get(1) == K.ABSTAIN_NONE and kinds.get(2) == K.ABSTAIN_SPREAD, kinds
    assert [r.cid for r in opp.rows] == [1, 2], "the ranked row leads, the abstained one follows"
    assert [r.cid for r in opp.ranked_rows()] == [1]
    assert [r.cid for r in _ranked_shown(opp)] == [1]
    assert [r.cid for r in _shown_rows(opp)] == [1, 2], "the abstained row is still SHOWN"

    class _S:
        def coaching_opportunities(self):
            return opp

        def coaching_brake_direction(self):
            return {2: _direction(2, -0.9, p=0.001, n_laps=6)}

    panel = OpportunitiesPanel(_S())
    assert panel.table.rowCount() == 2
    # the headline totals ONLY the ranked row
    assert f"{opp.rows[0].time_lost:.2f} s" in panel.summary_label.text(), \
        panel.summary_label.text()
    assert f"{opp.rows[1].time_lost:.2f}" not in panel.summary_label.text()
    # the abstained row: muted number, "Not ranked" sentence, and no braking line of either kind
    from PySide6.QtGui import QColor

    from studio.theme import C
    assert (panel.table.item(1, 1).foreground().color().name().upper()
            == QColor(C.text_dim).name().upper()), "an abstained loss must not read as a claim"
    reason = panel.table.item(1, 3).text()
    assert reason.startswith("Not ranked:") and "Brake ~" not in reason, reason
    assert "Braking" not in reason, "an abstained row grows no lever under it"
    print(f"ok abstain rows: shown but demoted; headline totals {panel.summary_label.text()!r}")


def _themed(plan_reach, kinds=None, losses=None):
    """Opportunities whose rows carry the given reach states (and optional reasons/losses)."""
    kinds = kinds or [K.REASON_BRAKING] * len(plan_reach)
    losses = losses or [1.0] * len(plan_reach)
    rows = [K.Opportunity(
        cid=i + 1, direction=1, time_lost=losses[i], entry_dist=0.0,
        reason=K.Reason(kinds[i], 0.1, 3.0, 0.3, 0.0, 0.1),
        evidence=K.Evidence(n_laps=20, reach_laps=(6 if r == K.REACH_REPEAT else 2),
                            reach=r, iqr=0.01, abstain=K.ABSTAIN_NONE))
        for i, r in enumerate(plan_reach)]
    return rows


def test_the_session_theme_is_one_line_and_refuses_to_invent_one():
    """Part 3: cluster to ONE theme, on SHARE OF RANKED TIME (the ranking's own unit), and say
    "no single theme" rather than crowning a plurality.

    Measured on the real recordings the two working-set recordings come out OPPOSITE — 0068 is 78 %
    execution and 0064 is 100 % pace — which is what makes the axis worth stating at all."""
    execution = K.session_theme(_themed([K.REACH_REPEAT] * 3 + [K.REACH_RARE]))
    assert execution.kind == K.THEME_EXECUTION and execution.share == 0.75, execution
    assert "execution, not pace" in K.theme_sentence(execution)
    pace = K.session_theme(_themed([K.REACH_RARE] * 3 + [K.REACH_REPEAT]))
    assert pace.kind == K.THEME_PACE, pace
    assert "pace, not execution" in K.theme_sentence(pace)
    # 50/50 clears neither side's THEME_SHARE -> say so, with both halves.
    split = K.session_theme(_themed([K.REACH_REPEAT, K.REACH_RARE]))
    assert split.kind == K.THEME_SPLIT, split
    assert "No single theme" in K.theme_sentence(split), K.theme_sentence(split)
    # ...and it is SHARE OF TIME, not a row count: three trivial corners must not outvote the one
    # that holds the seconds.
    by_time = K.session_theme(_themed([K.REACH_REPEAT] * 3 + [K.REACH_RARE],
                                      losses=[0.02, 0.02, 0.02, 1.00]))
    assert by_time.kind == K.THEME_PACE, by_time
    # Nothing ranked -> no theme, no sentence (the empty states own that case).
    none = K.session_theme([])
    assert none.kind == K.THEME_NONE and K.theme_sentence(none) == ""
    assert K.theme_actions(none, []) == []
    print(f"ok theme: {K.theme_sentence(execution)!r} / split => {K.theme_sentence(split)!r}")


def test_the_theme_names_at_most_two_actions_and_no_cause_it_cannot_measure():
    """Compression is the point: one theme, then AT MOST two actions — and when no cause holds a
    majority the action says exactly that instead of naming one.

    Measured, the cause axis does not generalize to every lap set: consistency holds 78 % of
    0068's ranked time and 100 % of 0064's (a theme on each), but one of the five single chapters
    names no single cause, so the "no single cause" branch is a real case on real recordings and is
    asserted here as a first-class output, not as a fallback."""
    # DISTINCT losses on purpose: four IDENTICAL ones are a tie by construction, and a tied lead is
    # now named as one (see test_t4_a_lead_corner_inside_the_pairs_own_spread_is_not_crowned_alone).
    # This test is about the CAUSE axis and the two-action cap, so it keeps a clear lead.
    spread = [1.0, 0.5, 0.4, 0.3]
    one_cause = K.session_theme(_themed([K.REACH_REPEAT] * 4, losses=spread))
    acts = K.theme_actions(one_cause, _themed([K.REACH_REPEAT] * 4, losses=spread))
    assert len(acts) == 2, acts
    assert acts[0].startswith("Braking is the common thread"), acts[0]
    assert acts[1].startswith("Start with C1:"), acts[1]
    mixed_rows = _themed([K.REACH_REPEAT] * 4,
                         kinds=[K.REASON_BRAKING, K.REASON_APEX, K.REASON_CONSISTENCY,
                                K.REASON_COASTING])
    mixed = K.session_theme(mixed_rows)
    mixed_acts = K.theme_actions(mixed, mixed_rows)
    assert mixed.cause == K.REASON_NONE and mixed.cause_cids == ()
    assert len(mixed_acts) == 2 and mixed_acts[0].startswith("No single cause dominates"), \
        mixed_acts
    print(f"ok theme actions: {acts}")


def _ranked_row(cid: int, loss: float, iqr: float, reach=K.REACH_REPEAT,
                kind=K.REASON_BRAKING, n_laps: int = 65, reach_laps: int = 12) -> K.Opportunity:
    """One RANKED row with a chosen loss, lap-to-lap spread and lap count (the numbers the tie
    test reads), everything else fixed."""
    return K.Opportunity(
        cid=cid, direction=1, time_lost=loss, entry_dist=0.0,
        reason=K.Reason(kind, 0.1, 3.0, 0.3, 0.0, 0.1),
        evidence=K.Evidence(n_laps=n_laps, reach_laps=reach_laps, reach=reach, iqr=iqr,
                            abstain=K.ABSTAIN_NONE))


def test_t4_a_lead_corner_inside_the_pairs_own_spread_is_not_crowned_alone():
    """T4: "Start with C<n>" must not name ONE corner when the corner behind it is the same
    number.

    MEASURED on both real recordings (current `main`, after #289 and #300 moved these numbers),
    with a paired within-lap permutation test on the corner labels, 20,000 permutations:

        0060  top C12 +0.330 s vs C4 +0.244 s — gap 0.086 s, p = 0.125  -> a TIE
        0062  top C3  +0.148 s vs C12 +0.143 s — gap 0.005 s, p = 0.837 -> a TIE

    and a paired lap bootstrap crowns a DIFFERENT corner in 13.8 % (0060) / 46.0 % (0062) of
    20,000 resamples. Split the same session in half (odd vs even laps) and the crown changes on
    BOTH recordings. Yet 17 of the 30 ranked pairs ARE separable, so the ranking itself stands —
    it is only the top-of-the-list crown that the data does not support.

    The numbers here are 0062's real ones. C1 is the third row, which the same permutation test
    DOES separate from the lead (p = 0.0042), so it must not be dragged into the sentence."""
    rows = [_ranked_row(3, 0.148, 0.158), _ranked_row(12, 0.143, 0.206),
            _ranked_row(1, 0.085, 0.109)]
    acts = K.theme_actions(K.session_theme(rows), rows)
    start = acts[-1]
    assert start.split(":")[0] == "Start with C3 or C12", start
    assert "+0.15 s and +0.14 s" in start, start
    assert "lap-to-lap spread" in start, start
    print(f"ok T4 tie: {start!r}")


def test_t4_a_lead_that_clears_the_spread_still_reads_exactly_as_before():
    """The other half of the same rule: a lead the measurement DOES separate keeps the sentence it
    has always had, word for word. 0060's C12 (+0.330 s, IQR 0.331) against its C2 (+0.204 s, IQR
    0.220) — the permutation test separates them at p = 0.0046 and the rule agrees."""
    rows = [_ranked_row(12, 0.330, 0.331), _ranked_row(2, 0.204, 0.220),
            _ranked_row(9, 0.153, 0.193)]
    acts = K.theme_actions(K.session_theme(rows), rows)
    assert acts[-1] == ("Start with C12: +0.33 s, and you have matched it on 12 of 65 laps."), \
        acts[-1]
    print(f"ok T4 clear lead: {acts[-1]!r}")


# The working set's RANKED rows, biggest loss first, as the QA r4 ADVICE lane extracted them through
# the real (jailed) loader on 13099c4 (qa-2026-09-28-r4/ADVICE/adv_<KEY>.pkl):
# (cid, time lost s, IQR s, counted laps, laps at the target, reach).
_R, _P = K.REACH_REPEAT, K.REACH_RARE
_WORKING_SET_RANKED = {
    "MK_18_09_26": [(5, 0.134780, 0.230944, 19, 6, _R), (2, 0.087096, 0.106218, 16, 3, _R),
                    (8, 0.064168, 0.107409, 17, 4, _R)],
    "SD_19_09_26": [(1, 0.146633, 0.200070, 36, 4, _R), (5, 0.093451, 0.064636, 35, 3, _P),
                    (7, 0.077588, 0.101853, 36, 4, _R), (2, 0.059539, 0.096228, 35, 5, _R),
                    (3, 0.049277, 0.090632, 36, 9, _R)],
    "SD_30_08_26": [(7, 0.099327, 0.095077, 37, 5, _R), (5, 0.082542, 0.113700, 37, 6, _R),
                    (3, 0.046750, 0.078567, 37, 9, _R)],
    "Sandown 3h 2026": [(1, 0.229400, 0.309380, 61, 3, _P), (4, 0.193470, 0.230531, 61, 5, _P),
                        (7, 0.177929, 0.304993, 62, 4, _P), (6, 0.167461, 0.187463, 62, 4, _P)],
}


def _working_set_rows(name: str, n_laps: int | None = None) -> list[K.Opportunity]:
    return [_ranked_row(cid, loss, iqr, reach=reach, n_laps=n if n_laps is None else n_laps,
                        reach_laps=hit)
            for cid, loss, iqr, n, hit, reach in _WORKING_SET_RANKED[name]]


def test_lead_ties_widen_on_a_short_session():
    """ADV-2 (QA r4): the tie margin had no lap-count term, so the fewer the laps the more often
    "Start with" crowned a corner the laps cannot separate. MK_18_09_26 (19 laps): C5 +0.135 s vs
    C8 +0.064 s — gap 0.071 s, above half the smaller spread (0.054 s), so C8 was left out, while
    the lane's paired permutation test (20,000) calls the pair a tie at p = 0.190.

    The margin now also takes 1.5 standard errors of the pair's difference (each median's SE from
    its own IQR and lap count), which is 0.082 s here. The other three recordings' sentences are
    byte-identical to 13099c4's: SD_19_09_26's C1 stays alone (the permutation separates it from
    C5 at p = 0.001), SD_30_08_26's C3 stays out (p = 0.010), and the 62-lap Sandown 3h keeps its
    four-way tie."""
    spread = "sit closer together than your own lap-to-lap spread"
    expect = {
        "MK_18_09_26": f"Start with C5, C2 or C8: +0.13 s down to +0.06 s {spread}, so this "
                       "cannot rank them.",
        "SD_19_09_26": "Start with C1: +0.15 s, and you have matched it on 4 of 36 laps.",
        "SD_30_08_26": f"Start with C7 or C5: +0.10 s and +0.08 s {spread}, so either is the same "
                       "call.",
        "Sandown 3h 2026": f"Start with C1, C4, C7 or C6: +0.23 s down to +0.17 s {spread}, "
                           "so this cannot rank them.",
    }
    for name, sentence in expect.items():
        rows = _working_set_rows(name)
        acts = K.theme_actions(K.session_theme(rows), rows)
        assert acts[-1] == sentence, (name, acts[-1])
    # The floor shrinks with √n: MK's same three losses and spreads over 60 laps a corner separate
    # C8 again (C2 stays, inside half the smaller spread on its own).
    long = _working_set_rows("MK_18_09_26", n_laps=60)
    assert [r.cid for r in K.lead_ties(long, 5)] == [5, 2], K.lead_ties(long, 5)
    print(f"ok ADV-2 short-session ties: {expect['MK_18_09_26']!r}")


def test_a_pace_theme_is_not_argued_with_by_a_consistency_cause_and_every_start_corner_is_named():
    """QA1-THEME-CLASH (REG-2, W1 close-out QA). Sandown 3h's debrief read "All of the time on offer
    is in corners you have rarely been quick through: it needs new speed, not repetition." directly
    above "Consistency is the common thread — 100% of that time is in C1, C4, C7, C6." — and then
    "Start with C1, C4, C7 or 1 more", a fourth corner the debrief's three-row grid does not show.

    The consistency cause is the spread fallback: no lever fired, so it names no input to change,
    and under a pace theme it only argues. The reword the QA offered ("the gap is the rare best lap,
    not the spread") does not survive the numbers: on Sandown 3h's four rows 51-56 % of each
    corner's time on offer lies beyond the laps' fast quartile and the rest INSIDE their spread
    (coaching.py, above `theme_actions`). So the line is dropped for that pairing and nothing else.

    The rows are the working set's real ones (`_WORKING_SET_RANKED`) with their real reasons, as the
    loader gives them on 95694b9: consistency everywhere but SD_19_09_26's C5 (braking). The other
    three recordings' actions are exactly what they printed before."""
    import dataclasses
    real_kinds = {"SD_19_09_26": {5: K.REASON_BRAKING}}

    def rows_of(name):
        kinds = real_kinds.get(name, {})
        return [dataclasses.replace(r, reason=dataclasses.replace(
            r.reason, kind=kinds.get(r.cid, K.REASON_CONSISTENCY)))
            for r in _working_set_rows(name)]

    spread = "sit closer together than your own lap-to-lap spread"
    expect = {
        "MK_18_09_26": (K.THEME_EXECUTION, [
            "Consistency is the common thread — 100% of that time is in C5, C2, C8.",
            f"Start with C5, C2 or C8: +0.13 s down to +0.06 s {spread}, so this cannot rank "
            "them."]),
        "SD_19_09_26": (K.THEME_EXECUTION, [
            "Consistency is the common thread — 78% of that time is in C1, C7, C2, C3.",
            "Start with C1: +0.15 s, and you have matched it on 4 of 36 laps."]),
        "SD_30_08_26": (K.THEME_EXECUTION, [
            "Consistency is the common thread — 100% of that time is in C7, C5, C3.",
            f"Start with C7 or C5: +0.10 s and +0.08 s {spread}, so either is the same call."]),
        "Sandown 3h 2026": (K.THEME_PACE, [
            f"Start with C1, C4, C7 or C6: +0.23 s down to +0.17 s {spread}, so this cannot rank "
            "them."]),
    }
    for name, (kind, lines) in expect.items():
        rows = rows_of(name)
        theme = K.session_theme(rows)
        acts = K.theme_actions(theme, rows)
        assert theme.kind == kind and theme.cause == K.REASON_CONSISTENCY, (name, theme)
        assert acts == lines, (name, acts)
        page = " ".join([K.theme_sentence(theme), *acts])
        assert not ("not repetition" in page and "Consistency" in page), (name, page)
        # Every corner "Start with" names is named, never counted: the debrief shows three rows.
        start = acts[-1].split(":")[0]
        named = [int(c) for c in re.findall(r"C(\d+)", start)]
        tied = [r.cid for r in K.lead_ties(rows, theme.lead_cid)]
        assert named == tied and "more" not in start, (name, start, tied)
    # The rule is the PAIRING, not either half: a pace theme keeps a concrete cause, a consistency
    # cause keeps its line under an execution or split theme, and "most" pace drops it like "all".
    pace = [_ranked_row(c, loss, 0.01, reach=K.REACH_RARE, n_laps=40, reach_laps=2,
                        kind=K.REASON_BRAKING) for c, loss in ((1, 0.5), (2, 0.3), (3, 0.2))]
    assert K.theme_actions(K.session_theme(pace), pace)[0].startswith("Braking is the common")
    cons = [dataclasses.replace(r, reason=dataclasses.replace(r.reason, kind=K.REASON_CONSISTENCY))
            for r in pace]

    def repeat(rows, cid):
        return [dataclasses.replace(r, evidence=dataclasses.replace(
            r.evidence, reach=K.REACH_REPEAT, reach_laps=12)) if r.cid == cid else r for r in rows]

    most = repeat(cons, 3)                                  # 0.8 s of 1.0 s rarely reached
    most_theme = K.session_theme(most)
    assert most_theme.kind == K.THEME_PACE and most_theme.share < 1.0, most_theme
    assert [a.split(":")[0] for a in K.theme_actions(most_theme, most)] == ["Start with C1"]
    split = repeat(cons, 1)                                 # 0.5 s against 0.5 s
    split_theme = K.session_theme(split)
    assert split_theme.kind == K.THEME_SPLIT, split_theme
    assert K.theme_actions(split_theme, split)[0].startswith("Consistency is the common thread")
    # A wide tie names all of its corners (the page and the Stats note name the same ones).
    wide = [_ranked_row(c, 0.40 - 0.005 * i, 0.3) for i, c in enumerate((3, 1, 4, 5, 9))]
    assert K.corner_names([r.cid for r in K.lead_ties(wide, 3)]) == "C3, C1, C4, C5 or C9"
    assert K.theme_actions(K.session_theme(wide), wide)[-1].startswith(
        "Start with C3, C1, C4, C5 or C9: +0.40 s down to +0.38 s")
    assert [K.corner_names(c) for c in ([7], [7, 5], [])] == ["C7", "C7 or C5", ""]
    print(f"ok QA1-THEME-CLASH: Sandown 3h => {expect['Sandown 3h 2026'][1]!r}")


def test_a_tie_decided_by_the_standard_error_never_outruns_the_spread_it_prints():
    """The tied sentence says the corners "sit closer together than your own lap-to-lap spread".
    At n = 3 (MIN_CORNER_LAPS, a 3-lap session) 1.5 standard errors of a difference are 1.14 x a
    shared IQR, so the SE term is capped at the wider of the two spreads: a gap past it separates
    even when the uncapped term would have tied it, and every tied gap stays inside a spread."""
    se3 = 1.5 * math.hypot(*[1.2533 / 1.349 * 0.10 / math.sqrt(3)] * 2)
    assert se3 > 0.11, se3                     # the uncapped term: 0.114 s on a 0.10 s spread
    inside = [_ranked_row(1, 0.30, 0.10, n_laps=3), _ranked_row(2, 0.21, 0.10, n_laps=3)]
    past = [_ranked_row(1, 0.30, 0.10, n_laps=3), _ranked_row(2, 0.195, 0.10, n_laps=3)]
    assert [r.cid for r in K.lead_ties(inside, 1)] == [1, 2]   # 0.09 s: half a spread is 0.05
    assert [r.cid for r in K.lead_ties(past, 1)] == [1]        # 0.105 s: past the 0.10 s spread
    # Asymmetric spreads at n = 3: every tied gap is inside the wider spread of its pair.
    rows = [_ranked_row(1, 0.40, 0.30, n_laps=3), _ranked_row(2, 0.25, 0.02, n_laps=3),
            _ranked_row(3, 0.05, 0.02, n_laps=3)]
    tied = K.lead_ties(rows, 1)
    assert [r.cid for r in tied] == [1, 2], tied
    for r in tied[1:]:
        assert tied[0].time_lost - r.time_lost < max(tied[0].evidence.iqr, r.evidence.iqr)
    # A row with no counted laps (_NO_EVIDENCE: iqr 0, n 0) still never ties.
    bare = K.Opportunity(cid=9, direction=1, time_lost=0.30, entry_dist=0.0,
                         reason=K.Reason(K.REASON_BRAKING, 0.1, 3.0, 0.3, 0.0, 0.1))
    assert [r.cid for r in K.lead_ties([inside[0], bare], 1)] == [1]
    # The constants the arithmetic above spells out (SE_MEDIAN_K is public for the focus verdict).
    assert (K.TIE_SE_K, K.SE_MEDIAN_K) == (1.5, 1.2533 / 1.349)
    print("ok ADV-2 SE tie capped at the printed spread")


def test_the_page_leads_with_the_theme():
    """The theme block is the first thing on the page — one story, stated once, in one place in
    the model (the modal that restated it was retired in R11).

    It also has to DISAPPEAR when there is nothing to state, or the "not enough clean laps" empty
    state would sit under a headline claiming a theme."""
    _qapp()
    from studio.coaching_panel import OpportunitiesPanel
    rows = _themed([K.REACH_RARE] * 3 + [K.REACH_REPEAT])
    opp = K.Opportunities(enough=True, n_laps=20, median_lap_id=4, rows=rows,
                          theme=K.session_theme(rows))
    sentence = K.theme_sentence(opp.theme)
    assert sentence and "pace, not execution" in sentence

    class _S:
        def coaching_opportunities(self):
            return opp

        def coaching_brake_direction(self):
            return {}

    panel = OpportunitiesPanel(_S())
    assert panel.theme_block.headline.text() == sentence, panel.theme_block.headline.text()
    shown = [lb.text() for lb in panel.theme_block.actions if lb.text()]
    assert 1 <= len(shown) <= 2, shown
    # nothing ranked -> no theme block at all (the empty state owns the page)
    empty = K.Opportunities(enough=False, n_laps=1, median_lap_id=None, rows=[])

    class _E:
        def coaching_opportunities(self):
            return empty

        def coaching_brake_direction(self):
            return {}

    p2 = OpportunitiesPanel(_E())
    assert p2.theme_block.headline.text() == ""
    assert p2.body.currentIndex() == 1, "the friendly excluded state still owns the body"
    print(f"ok theme block: the page leads with {sentence!r}")


def test_the_share_card_never_publishes_an_abstained_opportunity():
    """A card is the one coaching surface that LEAVES the app. If every corner failed the evidence
    gate it shows no opportunity at all, rather than publishing the biggest un-aimable number."""
    from studio import share_card
    ev = K.Evidence(n_laps=20, reach_laps=8, reach=K.REACH_REPEAT, iqr=0.40,
                    abstain=K.ABSTAIN_SPREAD)
    row = K.Opportunity(cid=3, direction=1, time_lost=0.05, entry_dist=10.0,
                        reason=K.Reason(K.REASON_APEX, 0.02, 2.0, 0.0, 0.0, 0.2), evidence=ev)
    opps = K.Opportunities(enough=True, n_laps=20, median_lap_id=4, rows=[row])
    sess = SimpleNamespace(coaching_opportunities=lambda: opps)
    assert share_card._top_opportunity(sess, "km/h") is None
    # the same row, ranked, IS published
    ranked = K.Opportunity(**{**row.__dict__,
                              "evidence": K.Evidence(n_laps=20, reach_laps=8,
                                                     reach=K.REACH_REPEAT, iqr=0.02,
                                                     abstain=K.ABSTAIN_NONE)})
    ok = SimpleNamespace(coaching_opportunities=lambda: K.Opportunities(
        enough=True, n_laps=20, median_lap_id=4, rows=[ranked]))
    top = share_card._top_opportunity(ok, "km/h")
    assert top is not None and top.corner_label.startswith("C3"), top
    print("ok share card: abstained top row publishes nothing; a ranked one publishes")


# ------------------------------------ U2: the empty state names WHAT removed the laps (D2-08)
class _LapAccountSession:
    """A coaching read surface whose summary is the excluded state, with the lap account the real
    panel reads set independently: the VALID lap ids the lap table lists and the ⚠ DROPOUT subset.
    `n_clean` is the summary's own denominator (valid minus dropouts, as Session computes it)."""

    def __init__(self, n_clean, valid, dropout):
        self._opps = K.Opportunities(enough=False, n_laps=n_clean, median_lap_id=None, rows=[])
        self._valid = list(valid)
        self._dropout = set(dropout)

    def coaching_opportunities(self):
        return self._opps

    def coaching_brake_direction(self):
        return {}

    def valid_lap_ids(self):
        return list(self._valid)

    def dropout_lap_ids(self):
        return set(self._dropout)


_U2_ALIVE: list = []


def _panel_state_text(s) -> str:
    from studio.coaching_panel import OpportunitiesPanel
    panel = OpportunitiesPanel(s)
    _U2_ALIVE.append(panel)
    assert panel.body.currentIndex() == 1, "fixture must reach the excluded page"
    return panel.empty_state.text()


def test_u2_zero_lap_coaching_states_the_same_fact_as_every_other_panel():
    """hero6.mp4 / hero8.mp4, measured in the real StudioWindow: the Laps page, the map, the charts
    and the status bar all said "No complete laps in this recording." with the GPS-lock / drag-the-
    line body, while the Coaching page on the same frame said "Not enough clean laps yet. … this
    session has 0. Drive a few more laps and reload." — the driver blamed for a recording that has
    no lap at all, and a next action no other panel gave. Zero valid laps is data_quality's state."""
    _qapp()
    from studio import data_quality
    s = _LapAccountSession(n_clean=0, valid=[], dropout=[])
    want = f"{data_quality.NO_LAPS_HEADLINE}\n\n{data_quality.no_laps_body()}"
    got = _panel_state_text(s)
    assert got == want, got
    print("ok U2: zero-lap coaching copy is the app's one no-laps sentence (the page)")


def test_u2_dropout_decided_coaching_copy_does_not_tell_the_driver_to_drive_more():
    """Five valid laps, three with a GPS dropout: two clean, under MIN_LAPS. The driver drove
    enough laps — the GPS removed them — so "Drive a few more laps" is the wrong reason. The copy
    names the dropout count against the lap table's own total, and both surfaces say it."""
    _qapp()
    s = _LapAccountSession(n_clean=2, valid=range(5), dropout=[0, 1, 2])
    got = _panel_state_text(s)
    assert "Drive a few more laps" not in got, got
    assert "3 of its 5 laps had a GPS dropout" in got, got
    assert "this session has 2" in got, got           # the clean denominator is still stated
    print("ok U2: dropout-decided copy names the GPS, not the driver")


def test_u2_count_decided_coaching_copy_keeps_its_next_action_and_reconciles_totals():
    """Under MIN_LAPS valid laps the count IS the reason, so the next action stays. A dropout among
    them is named so "this session has 1" reconciles with the 2 rows the lap table lists; a
    session with none reads exactly as before."""
    _qapp()
    got = _panel_state_text(_LapAccountSession(n_clean=1, valid=[0, 1], dropout=[1]))
    assert "Drive a few more laps" in got, got
    assert "1 of its 2 laps had a GPS dropout" in got, got
    plain = _panel_state_text(_LapAccountSession(n_clean=2, valid=[0, 1], dropout=[]))
    assert plain == ("Not enough clean laps yet.\n\nCoaching needs 3 clean (valid, GPS-dropout-"
                     "free) laps; this session has 2. Drive a few more laps and reload."), plain
    print("ok U2: count-decided copy keeps 'drive more' and reconciles a dropout")


def test_look_9_the_page_states_counts_not_verdicts_and_says_all_at_100_percent():
    """LOOK-9 (QA 2026-09-26). On MK_18_09 C11 read "Yes · 2/19" and "You have already done this"
    beside C7's "Rarely · 1/19" / "You have rarely done this": a verdict that flipped at one lap
    (REACH_REPEAT_FRAC). And the theme said "Most of the time on offer is execution … — 100% of
    it is …". The reason sentence now states the count in one form whatever side of the threshold
    it falls, and a 100 % share says "All"."""
    import dataclasses

    def row(k, n, reach):
        ev = K.Evidence(n_laps=n, reach_laps=k, reach=reach, iqr=0.02,
                               abstain=K.ABSTAIN_NONE)
        reason = K.Reason(kind=K.REASON_CONSISTENCY, contribution=0.05,
                                 apex_speed_deficit=0.0, brake_extra_s=0.0, coast_extra_s=0.0,
                                 sigma=0.3)
        return K.Opportunity(cid=1, direction=1, time_lost=0.1, entry_dist=0.0,
                                    reason=reason, evidence=ev)
    two, one = (K.reach_clause(row(2, 19, K.REACH_REPEAT)),
                K.reach_clause(row(1, 19, K.REACH_RARE)))
    assert two == " 2 of 19 laps matched your best lap here.", two
    assert one == " 1 of 19 laps matched your best lap here.", one
    whole = K.Theme(kind=K.THEME_EXECUTION, share=1.0, execution_s=0.28,
                           pace_s=0.0, n_ranked=3, n_abstained=8, cause=K.REASON_CONSISTENCY,
                           cause_share=0.78)
    sentence = K.theme_sentence(whole)
    assert sentence.startswith("All of the time on offer") and "Most" not in sentence, sentence
    most = dataclasses.replace(whole, share=0.78, pace_s=0.08)
    assert K.theme_sentence(most).startswith("Most of the time on offer"), most
    print(f"ok LOOK-9: {two.strip()!r} / {one.strip()!r}; {sentence!r}")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} COACHING TESTS PASSED")
