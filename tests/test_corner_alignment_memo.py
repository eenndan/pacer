"""The per-lap corner-warp MEMO (corner_model.CornerModel.lap_alignment) and — the point of this
file — its INVALIDATION.

`lap_alignment` caches the monotone warp that maps the corner partition onto one lap's odometer.
Every corner-window projection in the app is a read of that warp, and nine independent call paths
used to derive it for themselves (measured: `corners._spatial_matches` ran 144x in ONE
`stats_view.refresh` on the D24 0060 pair — 9x per lap that needs it). Sharing one is worth
~150 ms a refresh; sharing a STALE one after a start-line drag would be a correctness bug far worse
than the latency it saves, because it would silently re-frame every corner Δ, the ideal-lap
composite and the coaching plan against the previous segmentation.

So the tests here are all the same shape, and it is a shape with TEETH:
  1. populate the memo,
  2. MOVE the thing the warp depends on,
  3. fire the invalidation the production seam fires,
  4. assert the memoized answer now equals a from-scratch recompute against the NEW state,
  5. and assert (NEGATIVE CONTROL) that the pre-move answer was actually DIFFERENT — otherwise
     step 4 passes on a fixture where nothing moved and proves nothing.

The fixture is a bare Session (tests/_synthetic idiom) whose comparison lap is 1.2 % longer than
the reference — past `corners.NORMALIZED_DRIFT_MAX` — so the spatial path genuinely runs. That is
asserted explicitly (`test_fixture_actually_crosses_the_drift_gate`): a fixture that quietly stayed
below the gate would make every warp None and every assertion below vacuous.

Run:  python tests/test_corner_alignment_memo.py
"""
import os
import sys

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from _synthetic import bare_session, seed_corner_basis  # noqa: E402

from studio import corners as C  # noqa: E402

RADIUS = 40.0
STRAIGHT = 160.0
ARC = np.pi * RADIUS
TOTAL = 2 * STRAIGHT + 2 * ARC


def stadium(scale: float = 1.0, ds: float = 1.0):
    """(times, xs, ys, speed_mps, cum) of a stadium loop scaled by `scale` about the origin.

    Scaling the whole loop is what makes a lap "drift": its odometer grows by `scale` while every
    point stays within a few metres of the reference (the widest offset here is ~1.2 m at scale
    1.012), so the heading gate and the 3 m distance gate both pass and the spatial match produces
    real interior knots rather than degenerating to the two timing-line anchors."""
    s = np.arange(0.0, TOTAL, ds)
    xs = np.empty_like(s)
    ys = np.empty_like(s)
    for i, si in enumerate(s):
        if si < STRAIGHT:
            xs[i], ys[i] = si, 0.0
        elif si < STRAIGHT + ARC:
            th = (si - STRAIGHT) / RADIUS
            xs[i] = STRAIGHT + RADIUS * np.sin(th)
            ys[i] = RADIUS - RADIUS * np.cos(th)
        elif si < 2 * STRAIGHT + ARC:
            xs[i] = STRAIGHT - (si - STRAIGHT - ARC)
            ys[i] = 2 * RADIUS
        else:
            th = (si - 2 * STRAIGHT - ARC) / RADIUS
            xs[i] = -RADIUS * np.sin(th)
            ys[i] = RADIUS + RADIUS * np.cos(th)
    xs, ys = xs * scale, ys * scale
    cum = s * scale
    speed_mps = 18.0 + 4.0 * np.sin(np.linspace(0.0, 2 * np.pi, len(s)))
    # Elapsed from the speed profile, then a media clock offset per lap so laps are distinguishable.
    dt = np.diff(cum) / ((speed_mps[:-1] + speed_mps[1:]) / 2.0)
    times = np.concatenate(([0.0], np.cumsum(dt)))
    return times, xs, ys, speed_mps, cum


# The reference lap is lap 0 (scale 1.0); laps 1..3 drift progressively.
_SCALES = {0: 1.0, 1: 1.012, 2: 1.009, 3: 1.0006}
_SPANS = ((150.0, 260.0), (480.0, 600.0))


def fixture(spans=_SPANS):
    """A bare Session with four real 2-D laps + a seeded corner partition, best lap = 0."""
    s = bare_session(best=0, valid=list(_SCALES))
    s._cols_cache = {}
    for lap_id, scale in _SCALES.items():
        times, xs, ys, speed, cum = stadium(scale)
        s._cols_cache[lap_id] = (times, xs, ys, speed, cum)
        s._dist_cache[lap_id] = (times, cum, times - times[0])
    seed_corner_basis(s, spans=spans, total=float(TOTAL))
    return s


def frame_of(session):
    corner_list, _total = session.corners.basis()
    return [b for c in corner_list for b in (float(c.enter), float(c.exit))]


def fresh_alignment(session, lap_id):
    """The warp computed from scratch against the session's CURRENT state — the oracle every
    assertion below compares the memo against. Deliberately re-derives everything (basis, traces)
    rather than reusing anything the service may be holding."""
    corner_list, total_ref = session.corners.basis()
    frame = [b for c in corner_list for b in (float(c.enter), float(c.exit))]
    _t, ref_xs, ref_ys, _v, ref_cum = session._lap_columns(session.best_lap_id())
    _t2, xs, ys, _v2, cum = session._lap_columns(lap_id)
    return C.lap_alignment(frame, total_ref, float(cum[-1]),
                           traces=(ref_xs, ref_ys, ref_cum, xs, ys, cum))


def total_of(session, lap_id):
    return float(session._lap_columns(lap_id)[4][-1])


def same(a, b) -> bool:
    """Warp equality; None (the legal "keep the normalized projection") compares by identity."""
    if a is None or b is None:
        return a is None and b is None
    return (len(a[0]) == len(b[0]) and np.array_equal(a[0], b[0])
            and np.array_equal(a[1], b[1]))


def test_fixture_actually_crosses_the_drift_gate():
    """TEETH FOR EVERY OTHER TEST HERE. If the drifted laps sat inside NORMALIZED_DRIFT_MAX their
    warp would be None, `same(None, None)` would hold trivially after any mutation, and this file
    would assert nothing. Lap 3 is the control: it is deliberately BELOW the gate."""
    s = fixture()
    total_ref = s.corners.basis()[1]
    assert C.line_length_drift(total_of(s, 1), total_ref) > C.NORMALIZED_DRIFT_MAX
    assert C.line_length_drift(total_of(s, 2), total_ref) > C.NORMALIZED_DRIFT_MAX
    assert C.line_length_drift(total_of(s, 3), total_ref) <= C.NORMALIZED_DRIFT_MAX
    for lap in (1, 2):
        al = s.corners.lap_alignment(lap, total_of(s, lap))
        assert al is not None, f"lap {lap} produced no spatial warp — fixture is degenerate"
        # Real interior knots, not just the two timing-line anchors (which would BE the
        # normalized map and make the spatial path a no-op).
        assert len(al[0]) > 2, f"lap {lap}: no boundary survived the gates ({len(al[0])} knots)"
    assert s.corners.lap_alignment(3, total_of(s, 3)) is None, \
        "a lap inside the drift gate must keep the normalized projection (None)"
    print("ok fixture crosses the drift gate (laps 1,2 spatial; lap 3 normalized)")


def test_memo_returns_the_same_answer_as_a_fresh_derivation():
    """The memo is not allowed to be a different number from the thing it replaces."""
    s = fixture()
    for lap in _SCALES:
        assert same(s.corners.lap_alignment(lap, total_of(s, lap)), fresh_alignment(s, lap))
        # …and again, now served from the cache.
        assert same(s.corners.lap_alignment(lap, total_of(s, lap)), fresh_alignment(s, lap))
    print("ok memoized warp == fresh derivation, cold and warm")


def test_memo_is_actually_hit():
    """A memo that never hits would be correct and pointless — the whole PR is the hit rate.
    Counts the spatial matcher instead of the timer (deterministic, load-independent)."""
    s = fixture()
    calls = {"n": 0}
    real = C._spatial_matches

    def counting(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    C._spatial_matches = counting
    try:
        for _ in range(5):
            for lap in _SCALES:
                s.corners.lap_alignment(lap, total_of(s, lap))
    finally:
        C._spatial_matches = real
    assert calls["n"] == 2, \
        f"expected ONE spatial match for each of the 2 drifted laps, got {calls['n']}"
    print("ok 20 alignment reads over 4 laps cost 2 spatial matches")


def test_key_separates_laps_and_totals():
    """The key must carry everything per-lap the warp depends on. Two laps must not share an
    entry, and neither must two different `total_lap` readings of one lap — a caller measuring
    the lap through another accessor has to get a warp built for ITS total, never a silently
    mismatched one."""
    s = fixture()
    a1 = s.corners.lap_alignment(1, total_of(s, 1))
    a2 = s.corners.lap_alignment(2, total_of(s, 2))
    assert not same(a1, a2), "two different laps returned the same warp"
    shifted = total_of(s, 1) + 5.0
    a1b = s.corners.lap_alignment(1, shifted)
    assert not same(a1, a1b), "a different total_lap reused the other total's warp"
    assert abs(float(a1b[1][-1]) - shifted) < 1e-9, "the warp must end at the total it was asked for"
    # And the mismatch guard project_boundaries added in #228 still fires for anyone who
    # hand-builds a warp for the wrong lap.
    try:
        C.project_boundaries([100.0], s.corners.basis()[1], total_of(s, 2), alignment=a1)
    except ValueError:
        pass
    else:
        raise AssertionError("project_boundaries accepted a warp built for another lap")
    print("ok memo key separates (lap, total_lap); cross-lap warp still rejected")


def test_invalidate_drops_the_memo_after_a_resegmentation():
    """`Session.set_timing_lines` → `corners.invalidate()`. A start-line drag re-partitions the
    lap, so a warp fitted to the OLD partition must not survive it."""
    s = fixture()
    before = s.corners.lap_alignment(1, total_of(s, 1))
    # Re-segment: a different corner partition (what a start-line drag produces downstream).
    moved_spans = ((190.0, 300.0), (520.0, 640.0))
    s.corners.invalidate()
    seed_corner_basis(s, spans=moved_spans, total=float(TOTAL))
    after = s.corners.lap_alignment(1, total_of(s, 1))
    assert same(after, fresh_alignment(s, 1)), "post-invalidate warp is not the fresh one"
    # NEGATIVE CONTROL: the two states really are distinguishable, so the assertion above had
    # something to catch. Without the invalidate, `after` would have been `before`.
    assert not same(before, after), \
        "the moved partition produced an identical warp — this test cannot detect staleness"
    print("ok invalidate() (re-segmentation) drops the warp memo")


def test_invalidate_stats_drops_the_memo_on_a_reference_change():
    """`Session.set_reference_session` / `clear_reference` → `corners.invalidate_stats()`.

    The warp does not actually depend on the Δ baseline, so this is a belt-and-braces clear — but
    it is the property `CornerModel.invalidate_stats` documents (the memo's lifetime is a strict
    subset of `_stats_cache`'s), and documented behaviour that nothing checks is how the next
    refactor loses it."""
    s = fixture()
    s.corners.lap_alignment(1, total_of(s, 1))
    assert s.corners._align_cache, "nothing was memoized — fixture broken"
    s.corners.invalidate_stats()
    assert not s.corners._align_cache, \
        "invalidate_stats left the warp memo populated; its lifetime must not outlive _stats_cache"
    assert same(s.corners.lap_alignment(1, total_of(s, 1)), fresh_alignment(s, 1))
    print("ok invalidate_stats() (reference change) drops the warp memo")


def test_memo_follows_the_best_lap():
    """The warp's reference half is the BEST lap's trace. `Session._best_cache` has exactly one
    writer — `set_timing_lines`, which also calls `corners.invalidate()` — so the memo cannot go
    stale behind a best-lap change. Pin that: move the best lap, invalidate, demand the fresh
    answer, and prove the move was observable."""
    s = fixture()
    before = s.corners.lap_alignment(2, total_of(s, 2))
    s._best_cache = 1              # what set_timing_lines' recompute would land on
    s.corners.invalidate()
    after = s.corners.lap_alignment(2, total_of(s, 2))
    assert same(after, fresh_alignment(s, 2)), "warp did not follow the new best lap"
    assert not same(before, after), \
        "changing the reference lap produced an identical warp — no staleness could be detected"
    print("ok the warp memo follows the best (reference) lap through invalidate()")


def test_downstream_reads_are_unchanged_by_the_memo():
    """EQUIVALENCE. Everything the memo feeds must be bit-identical to deriving the warp inline:
    the projected boundaries, the corner/straight partition times, and the per-corner stats."""
    s = fixture()
    corner_list, total_ref = s.corners.basis()
    frame = frame_of(s)
    for lap in _SCALES:
        total = total_of(s, lap)
        _t, ref_xs, ref_ys, _v, ref_cum = s._lap_columns(s.best_lap_id())
        _t2, xs, ys, _v2, cum = s._lap_columns(lap)
        traces = (ref_xs, ref_ys, ref_cum, xs, ys, cum)
        dist, speed_kmh, elapsed = s._lap_arrays(lap)
        al = s.corners.lap_alignment(lap, total)

        assert np.array_equal(
            C.project_boundaries(frame, total_ref, total, traces=traces),
            C.project_boundaries(frame, total_ref, total, alignment=al))
        assert np.array_equal(
            C.segment_times(corner_list, total_ref, dist, elapsed, traces),
            C.segment_times(corner_list, total_ref, dist, elapsed, traces, al))
        a = C.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed, traces=traces)
        b = C.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed, traces=traces,
                               alignment=al)
        assert a == b, f"lap {lap}: per-corner stats moved when the warp was passed in"
    print("ok projected boundaries / segment times / corner stats identical with the shared warp")


def test_stats_surfaces_match_a_from_scratch_recompute_after_each_invalidation():
    """The end-to-end shape of the failure mode, on the sequence the mandate names: start-line
    drag → sector edit → undo → reference load.

    LEFT-HAND SIDE is one long-lived session carrying a WARM memo through every mutation — the
    thing that goes stale. RIGHT-HAND SIDE is a session freshly built at that same state, which
    by construction has never seen an earlier one. Every corner surface a Stats refresh reads
    must agree; a memo that survived an invalidation is exactly a left-hand divergence."""
    def surfaces(sess):
        out = []
        for lap in sorted(_SCALES):
            out.append([(st.cid, st.time, st.delta, st.apex_speed, st.entry_speed, st.exit_speed)
                        for st in sess.corners.lap_corner_stats(lap)])
        out.append(sess.corners.corner_session_bests())
        sb = sess.corners.segment_bests()
        out.append(None if sb is None else (list(sb.bests), list(sb.donors)))
        out.append([sess.corners.corner_entry_media_time(lap, 1) for lap in sorted(_SCALES)])
        return out

    def at(sess, spans, best, *, reference_change=False):
        """Put a session into (spans, best) through the PRODUCTION seam and read its surfaces."""
        sess._best_cache = best
        if reference_change:
            sess.corners.invalidate_stats()   # set_reference_session / clear_reference
        else:
            sess.corners.invalidate()         # set_timing_lines (start-line / sector edit)
        seed_corner_basis(sess, spans=spans, total=float(TOTAL))
        return surfaces(sess)

    edited = ((190.0, 300.0), (520.0, 640.0))
    steps = [(_SPANS, 0, False),        # initial segmentation
             (edited, 0, False),        # start-line drag / sector edit
             (_SPANS, 0, False),        # undo
             (_SPANS, 1, True)]         # reference load (Δ baseline moves)

    warm = fixture()
    seen = []
    for spans, best, is_ref in steps:
        got = at(warm, spans, best, reference_change=is_ref)
        oracle = at(fixture(spans=spans), spans, best, reference_change=is_ref)
        assert got == oracle, \
            f"spans={spans} best={best} ref={is_ref}: the warm session disagrees with a fresh one"
        seen.append(got)
    # TEETH: the four states must be mutually distinguishable, or "warm == fresh" is vacuous.
    assert seen[0] != seen[1], "the sector edit did not move any surface"
    assert seen[0] == seen[2], "undo did not return to the pre-edit state"
    assert seen[2] != seen[3], "the reference/baseline change did not move any surface"
    print("ok corner surfaces survive drag → edit → undo → reference load (warm == fresh)")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} CORNER-ALIGNMENT-MEMO TESTS PASSED")
