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
the reference, and whose laps carry real xy traces — so the spatial path genuinely runs and
produces real interior knots. That is asserted explicitly
(`test_fixture_actually_crosses_the_drift_gate`): a fixture whose laps produced no warp would make
every warp None and every assertion below vacuous.

Run:  python tests/test_corner_alignment_memo.py
"""
import ast
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
    """TEETH FOR EVERY OTHER TEST HERE. If a lap produced no warp, `same(None, None)` would hold
    trivially after any mutation and this file would assert nothing. EVERY lap must therefore carry
    a real warp with real interior knots.

    Lap 3 used to be the control for the opposite property: it sits under 0.5 % drift, and the old
    `corners.NORMALIZED_DRIFT_MAX` gate kept such a lap on the normalized projection (warp None).
    That gate is gone — every lap is spatially aligned now — so lap 3 is held to the same standard
    as the others, and the spread of drifts below is kept only because it is what makes the laps
    distinct from one another."""
    s = fixture()
    total_ref = s.corners.basis()[1]
    assert C.line_length_drift(total_of(s, 1), total_ref) > 0.005
    assert C.line_length_drift(total_of(s, 2), total_ref) > 0.005
    assert C.line_length_drift(total_of(s, 3), total_ref) <= 0.005
    for lap in (1, 2, 3):
        al = s.corners.lap_alignment(lap, total_of(s, lap))
        assert al is not None, f"lap {lap} produced no spatial warp — fixture is degenerate"
        # Real interior knots, not just the two timing-line anchors (which would BE the
        # normalized map and make the spatial path a no-op).
        assert len(al[0]) > 2, f"lap {lap}: no boundary survived the gates ({len(al[0])} knots)"
    print("ok fixture laps 1-3 all carry a real warp (drifts spanning the old 0.5 % gate)")


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
    assert calls["n"] == len(_SCALES), \
        f"expected ONE spatial match for each of the {len(_SCALES)} laps, got {calls['n']}"
    print(f"ok 20 alignment reads over {len(_SCALES)} laps cost {len(_SCALES)} spatial matches")


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


# ----------------------------------------------- what a None alignment MEANS, and what it is called
# `lap_alignment` returns None for three reasons, and "this lap drifted too little to be worth
# warping" is no longer one of them: no corner basis, no usable trace pair, or no spatial match
# surviving anywhere on the lap. Its docstring said None meant "below the drift gate" until #300
# deleted `corners.NORMALIZED_DRIFT_MAX`; every lap with a trace pair has been warped since, and on
# the owner's recordings NOT ONE lap reaches None at all — 0 of 38 (D24 0060 pair) and 0 of 65
# (0062), with 4-22 and 20-22 of the 24 corner boundaries carrying a matched interior knot.
#
# Both halves are guarded, because either alone is weak: the behaviour can go wrong with every
# comment still reading true, and a comment can go false with no behaviour changing at all — which
# is exactly what happened here.
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Narrow on purpose. "gate" is a real word all over this repo (the GPS quality gate, the heading and
# 3 m match gates this very warp still uses); only the phrasings that name the REMOVED per-lap drift
# gate are banned.
BANNED_GATE_WORDING = ("drift gate", "drift-gate", "drift gated", "drift-gated")

# Docstrings that describe the alignment, or a surface built out of it. Checked in BOTH directions,
# like tests/test_layering.py: a target that no longer exists fails too, so this list cannot rot
# into a silent no-op.
ALIGNMENT_DOCS = [
    ("studio/corner_model.py", "lap_alignment"),
    ("studio/corner_model.py", "_best_trace"),
    ("studio/corner_model.py", "_lap_traces"),
    ("studio/corner_model.py", "corner_entry_media_time"),
    ("studio/corners.py", "lap_alignment"),
    ("studio/corners.py", "project_boundaries"),
    ("studio/corners.py", "segment_times"),
    ("studio/corners.py", "lap_corner_stats"),
    ("studio/driving_channels.py", "_best_trace"),
    ("studio/driving_channels.py", "_corner_traces"),
    ("studio/driving_channels.py", "_corner_align"),
    ("studio/session.py", "phase_report"),
    ("studio/session.py", "straights_report"),
    ("studio/coaching.py", "_project_window"),
    ("studio/coaching.py", "corner_phase_losses"),
    ("studio/coaching.py", "summarize"),
    ("studio/stats.py", "phase_matrix"),
]

# Files whose comments (and, for provenance, an on-screen note) carried the wording where no
# docstring walk can see it. The gate is gone from every one of them.
#
# `AGENTS.md` is deliberately NOT here even though it names the gate: it spells two UNRELATED
# gates the same way (the bindings regen-drift gate, the clang-format CI gate), so a text ban on
# that file would be noise rather than a guard. Its one stale sentence is corrected in the same
# commit as these.
ALIGNMENT_TEXT = [
    "studio/session.py",
    "studio/coaching.py",
    "studio/stats.py",
    "studio/stats_panel.py",
    "studio/provenance.py",
    "studio/driving_channels.py",
    "tests/test_studio_features.py",
    "studio/docs/friction-circle-release-investigation.md",
]

# Files that OWN the removed gate's history: the measurement blocks that justify removing it, the
# unit tests named after it, and the golden fixtures whose drift bands are defined AGAINST it
# (`tests/_synthetic.py` grew a sub-gate band phase alongside this work). They may name it — but
# only as history, so every occurrence has to sit beside a past-tense marker.
GATE_HISTORY = ["studio/corners.py", "studio/corner_model.py", "tests/test_corners.py",
                "tests/_synthetic.py", "tests/test_golden_synthetic.py"]
PAST_MARKERS = ("old", "used to", "was", "were", "gone", "removed", "no longer", "then-current",
                "pre-gate", "until it", "there was")


def _read(path):
    with open(os.path.join(_REPO, path), encoding="utf-8") as f:
        return f.read()


def _docstrings(path):
    """{function name: docstring} for every def in a module, nested ones included."""
    out = {}
    for node in ast.walk(ast.parse(_read(path))):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, ast.get_docstring(node) or "")
    return out


def test_a_none_alignment_means_no_warp_could_be_built():
    """THE FACT. None is "nothing to build a warp out of", and it has exactly three causes. The
    negative control for the claim this package corrects is the FIRST one: a lap far inside the old
    0.5 % gate is warped like any other, so re-introducing a drift gate turns this red."""
    s = fixture()
    total_ref = s.corners.basis()[1]

    # 1. Small drift is NOT a cause. Lap 3 sits at 0.06 %, an order of magnitude inside the gate.
    drift = C.line_length_drift(total_of(s, 3), total_ref)
    assert drift < 0.005, f"fixture lap 3 must sit inside the old 0.5 % gate, got {drift:.4%}"
    al = s.corners.lap_alignment(3, total_of(s, 3))
    assert al is not None and len(al[0]) > 2, (
        f"a lap at {drift:.4%} drift kept the normalized projection — a drift gate is back")

    # 2. No corner basis (no corners detected / no usable best lap).
    no_basis = fixture()
    seed_corner_basis(no_basis, spans=(), total=float(TOTAL))
    assert no_basis.corners.basis()[0] == []
    assert no_basis.corners.lap_alignment(1, total_of(no_basis, 1)) is None

    # 3. No usable trace pair: this lap's own trace is degenerate (a cross-recording reference lap
    #    has no local pair at all, which is the same branch).
    degenerate = fixture()
    total_1 = total_of(degenerate, 1)
    degenerate._cols_cache[1] = tuple(col[:1] for col in degenerate._cols_cache[1])
    seed_corner_basis(degenerate, spans=_SPANS, total=float(TOTAL))
    assert degenerate.corners.lap_alignment(1, total_1) is None

    # 4. No spatial match survives anywhere: the same lap driven 10 m off the reference line, past
    #    corners.SPATIAL_MATCH_MAX_M (3 m) at every boundary.
    off_line = fixture()
    times, xs, ys, speed, cum = off_line._cols_cache[2]
    off_line._cols_cache[2] = (times, xs, ys + 10.0, speed, cum)
    seed_corner_basis(off_line, spans=_SPANS, total=float(TOTAL))
    assert off_line.corners.lap_alignment(2, total_of(off_line, 2)) is None
    # …and the control: the same lap ON the line has a warp, so (4) is the displacement talking.
    assert s.corners.lap_alignment(2, total_of(s, 2)) is not None
    print("ok None = no basis / no trace pair / no surviving match — never a small drift "
          f"(lap 3 at {drift:.4%} is warped)")


def test_a_corner_is_resolved_only_where_both_its_edges_are_knots_of_the_lap_warp():
    """F7's per-cell trust flag (`CornerModel.lap_corner_resolved`) is a READ of this memo: a corner
    is resolved on a lap iff its enter AND exit are knots of that lap's warp, i.e. were matched on
    track rather than interpolated. Checked against a from-scratch warp on the drift fixture whose
    run-wide lap has one boundary its spatial match cannot find — and that fixture property is
    asserted too, or every corner would be resolved and this would prove nothing."""
    from _synthetic import drift_noise_session

    s = drift_noise_session()
    corner_list, total_ref = s.corners.basis()
    frame = [b for c in corner_list for b in (float(c.enter), float(c.exit))]
    ref_trace = s.corners._best_trace()
    unresolved = 0
    for lap in s.valid_lap_ids():
        got = s.corners.lap_corner_resolved(lap)
        total = total_of(s, lap)
        knots = C.lap_alignment(frame, total_ref, total,
                                traces=s.corners._lap_traces(lap, ref_trace))[0]
        want = [bool(np.isin(c.enter, knots) and np.isin(c.exit, knots)) for c in corner_list]
        assert got == want, (lap, got, want)
        assert len(got) == len(s.corners.lap_corner_stats(lap))
        unresolved += got.count(False)
    assert all(s.corners.lap_corner_resolved(s.best_lap_id())), "the best lap matches itself"
    assert unresolved >= 1, "the drift fixture no longer has an interpolated corner edge"

    # No warp at all (every edge is the normalized fraction) -> nothing is resolved; no corner
    # basis -> [] exactly where lap_corner_stats is [].
    off_line = fixture()
    times, xs, ys, speed, cum = off_line._cols_cache[2]
    off_line._cols_cache[2] = (times, xs, ys + 10.0, speed, cum)
    seed_corner_basis(off_line, spans=_SPANS, total=float(TOTAL))
    assert off_line.corners.lap_alignment(2, total_of(off_line, 2)) is None
    assert off_line.corners.lap_corner_resolved(2) == [False] * len(_SPANS)
    no_basis = fixture()
    seed_corner_basis(no_basis, spans=(), total=float(TOTAL))
    assert no_basis.corners.lap_corner_resolved(1) == [] == no_basis.corners.lap_corner_stats(1)
    print(f"ok resolved = both edges are warp knots ({unresolved} interpolated corner(s) on the "
          "drift fixture), all False without a warp, [] without a basis")


def test_session_corner_matrix_is_the_corner_stats_and_their_resolution_unrolled():
    """End to end through the real Session on a seven-lap drift session (the noise laps followed by
    the band ladder — enough laps for the grid's floor): rows are the consistency laps, cells are
    `lap_corner_stats` times verbatim, the resolution is `lap_corner_resolved` verbatim, and no
    interpolated cell carries a mark."""
    from _synthetic import _drift_session, drift_band_laps, drift_noise_laps

    first = drift_noise_laps()
    s = _drift_session(first + drift_band_laps(t0=float(first[-1]["cols"][0][-1])))
    m = s.corner_matrix()
    assert m is not None and m.lap_ids == s.consistency_lap_ids()
    assert m.cids == [c.cid for c in s.corners.corner_list()]
    for r, lap in enumerate(m.lap_ids):
        assert m.cells[r] == [st.time for st in s.corners.lap_corner_stats(lap)]
        assert m.resolved[r] == s.corners.lap_corner_resolved(lap)
    flat = [x for row in m.resolved for x in row]
    assert not all(flat), "the session no longer carries an interpolated corner edge"
    assert not any(m.is_behind(r, c) for r, row in enumerate(m.resolved)
                   for c, ok in enumerate(row) if not ok)
    assert m.n_resolved == [sum(row[c] for row in m.resolved) for c in range(len(m.cids))]
    print(f"ok Session.corner_matrix: {len(m.lap_ids)} laps x {len(m.cids)} corners, "
          f"{flat.count(False)} interpolated cell(s), none marked")


def test_no_comment_says_a_none_alignment_is_a_lap_below_the_drift_gate():
    """THE CLAIM — the half a fact test cannot cover, because a comment goes false on its own. #300
    removed the gate and left the wording behind in twenty-odd places, one of them a note printed in
    the provenance panel."""
    missing, banned, silent = [], [], []
    for path, name in ALIGNMENT_DOCS:
        docs = _docstrings(path)
        if name not in docs:
            missing.append(f"{path}::{name}")          # the both-directions half
            continue
        text = docs[name].lower()
        banned += [f"{path}::{name} says {p!r}" for p in BANNED_GATE_WORDING if p in text]
        if not any(word in text for word in ("spatial", "warp", "align")):
            silent.append(f"{path}::{name}")
    assert not missing, f"guarded symbols that no longer exist (stale targets): {missing}"
    assert not banned, f"the removed drift gate is described as live: {banned}"
    assert not silent, f"these carry the alignment but name nothing about it: {silent}"

    for path in ALIGNMENT_TEXT:
        text = _read(path).lower()
        for phrase in BANNED_GATE_WORDING:
            assert phrase not in text, f"{path} says {phrase!r} — the gate is gone"

    for path in GATE_HISTORY:
        text = _read(path).lower()
        for phrase in BANNED_GATE_WORDING:
            at = text.find(phrase)
            while at >= 0:
                context = text[max(0, at - 200):at + 200]
                assert any(m in context for m in PAST_MARKERS), (
                    f"{path}: {phrase!r} at offset {at} reads as a live gate, not as history")
                at = text.find(phrase, at + 1)

    # …and the memo's own docstring must say what None IS, not merely stop saying what it was.
    memo = _docstrings("studio/corner_model.py")["lap_alignment"].lower()
    for token in ("basis", "trace", "match"):
        assert token in memo, f"CornerModel.lap_alignment does not name the {token} cause of None"
    print(f"ok claim: {len(ALIGNMENT_DOCS)} docstrings + {len(ALIGNMENT_TEXT)} files clean, "
          f"{len(GATE_HISTORY)} history files checked")


def test_the_provenance_note_names_the_projection_it_used():
    """The one user-facing surface in this family: the CORNERS table's provenance note explains the
    window a corner time was measured over, and it named the gate that no longer exists."""
    src = _read("studio/provenance.py")
    assert "one monotone spatial warp" in src, (
        "the corner provenance note no longer says what the window was projected through")
    for phrase in BANNED_GATE_WORDING:
        assert phrase not in src.lower()


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} CORNER-ALIGNMENT-MEMO TESTS PASSED")
