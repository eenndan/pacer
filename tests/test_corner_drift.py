"""The session GEOMETRY model — `corners.session_geometry` and what `CornerModel.lap_alignment`
does with it — and, above everything else here, THE ONE CONTROL THAT DECIDES WHETHER IT MAY SHIP.

The corner-window match gates on a 3 m distance between the reference lap's trace and the
comparison lap's. A consumer GNSS receiver's position error is nearly constant over one 90-second
lap, so each lap's whole trace sits displaced by ONE 2D vector, and on the owner's D24 0060
recording that alone failed 38 % of the interior boundaries (M6,
`studio/docs/corner-match-0060-2026-09.md`). Removing it before the gate recovers them.

THE RISK IS THAT IT REMOVES A RACING LINE TOO, and that is what most of this file is about. The
separation is geometric, not statistical: around a CLOSED circuit the heading sweeps a full turn,
so a fixed vector T appears as a perpendicular offset n̂(s)·T that changes SIGN twice a lap, while
driving wider is an offset along the local normal with a consistent sign and is not in the span of
{n̂·T}. Both are PLANTED on the same synthetic lap here and the fit must recover one and refuse the
other — a fitter that absorbed the line would return |T| ≈ the line offset instead of ≈ 0.

The other teeth:
  * a lap displaced 4 m — past the gate — resolves every corner again (it FAILS on the tree
    before this change: with no de-drift its warp is None and every cell reads unresolved);
  * a lap that genuinely drove 4 m wide is NOT rescued, because it really was somewhere else;
  * THE REFERENCE LAP IS EXEMPT. Its odometer IS the frame the corner windows are expressed in, so
    its warp must stay the exact identity even when it is the lap furthest from the consensus —
    which is exactly D24 0060's case (its fastest lap ranks 34/38 from the consensus line).
    Applying the anchor correction to it would push its OWN boundaries past the 3 m gate and make
    `lap_corner_resolved(best)` read False;
  * a session with no drift at all is BIT-IDENTICAL to the uncorrected match, so the correction
    cannot creep into a recording that does not need it (and the golden fixture stays put).

Run:  python tests/test_corner_drift.py
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
# Both spans sit INSIDE the loop's two arcs (160.0-285.7 and 445.7-571.3 m of arc), strictly
# between the timing-line anchors — a boundary at or past the lap total is never a warp knot, so a
# span that ran off the end would make every assertion here read False for the wrong reason.
SPANS = ((170.0, 275.0), (455.0, 560.0))
PLANTED_M = 4.0          # past corners.SPATIAL_MATCH_MAX_M, so the shipped gate refuses it


def stadium(ds: float = 1.0):
    """(xs, ys) of one closed stadium loop sampled every `ds` metres of arc — a circuit whose
    heading sweeps a full turn, which is what makes a rigid translation identifiable at all."""
    s = np.arange(0.0, TOTAL, ds)
    xs, ys = np.empty_like(s), np.empty_like(s)
    for i, si in enumerate(s):
        if si < STRAIGHT:
            xs[i], ys[i] = si, 0.0
        elif si < STRAIGHT + ARC:
            th = (si - STRAIGHT) / RADIUS
            xs[i] = STRAIGHT + RADIUS * np.sin(th)
            ys[i] = RADIUS - RADIUS * np.cos(th)
        elif si < 2 * STRAIGHT + ARC:
            xs[i], ys[i] = STRAIGHT - (si - STRAIGHT - ARC), 2 * RADIUS
        else:
            th = (si - 2 * STRAIGHT - ARC) / RADIUS
            xs[i] = -RADIUS * np.sin(th)
            ys[i] = RADIUS + RADIUS * np.cos(th)
    return xs, ys


def wider(xs, ys, offset: float):
    """The same loop driven `offset` metres to the LEFT of it all the way round — a parallel
    curve, i.e. what a driver does. Not a translation: the displacement follows the lap's own
    normal, so it turns with the car."""
    tx, ty = C._unit_tangents(xs, ys)
    return xs - offset * ty, ys + offset * tx


def lap_columns(xs, ys):
    """(times, xs, ys, speed_mps, cum) for a trace, with the odometer measured from the trace
    itself so a wider line really is longer."""
    step = np.hypot(np.diff(xs), np.diff(ys))
    cum = np.concatenate(([0.0], np.cumsum(step)))
    speed = 18.0 + 4.0 * np.sin(np.linspace(0.0, 2 * np.pi, len(cum)))
    dt = np.diff(cum) / ((speed[:-1] + speed[1:]) / 2.0)
    return np.concatenate(([0.0], np.cumsum(dt))), xs, ys, speed, cum


def session(traces: dict, best: int = 0):
    """A bare Session whose laps carry the given (xs, ys) traces, with a seeded partition."""
    s = bare_session(best=best, valid=sorted(traces))
    s._cols_cache = {}
    for lap_id, (xs, ys) in traces.items():
        cols = lap_columns(xs, ys)
        s._cols_cache[lap_id] = cols
        s._dist_cache[lap_id] = (cols[0], cols[4], cols[0] - cols[0][0])
    seed_corner_basis(s, spans=SPANS, total=float(s._cols_cache[best][4][-1]))
    return s


def planted_session(best: int = 0):
    """Five laps on one line, except lap 1 which the RECEIVER moved (a rigid translation) and
    lap 2 which the DRIVER moved (4 m wide all the way round). Laps 0, 3 and 4 are the consensus."""
    xs, ys = stadium()
    return session({0: (xs, ys),
                    1: (xs + PLANTED_M * 0.6, ys + PLANTED_M * 0.8),
                    2: wider(xs, ys, PLANTED_M),
                    3: (xs, ys),
                    4: (xs, ys)}, best=best)


def total_of(s, lap_id):
    return float(s._lap_columns(lap_id)[4][-1])


def without_geometry(s):
    """The same session with the geometry model switched off — i.e. the tree before this change.
    Used as the NEGATIVE CONTROL inside the tests that assert a lap now resolves: without it, a
    fixture whose laps matched anyway would make those assertions pass for no reason."""
    s.corners.geometry = lambda: None
    s.corners.invalidate_stats()
    return s


def uncorrected_warp(s, lap_id):
    """The warp `corners` would build with no session geometry at all, from scratch."""
    corner_list, total_ref = s.corners.basis()
    frame = [b for c in corner_list for b in (float(c.enter), float(c.exit))]
    _t, rx, ry, _v, rc = s._lap_columns(s.best_lap_id())
    _t2, lx, ly, _v2, lc = s._lap_columns(lap_id)
    return C.lap_alignment(frame, total_ref, float(lc[-1]), traces=(rx, ry, rc, lx, ly, lc))


def same_warp(a, b) -> bool:
    if a is None or b is None:
        return a is None and b is None
    return np.array_equal(a[0], b[0]) and np.array_equal(a[1], b[1])


# ------------------------------------------------------------------- the separation control
def test_a_planted_translation_is_recovered_and_a_planted_wide_line_is_not():
    """THE CONTROL THIS WHOLE CHANGE RESTS ON. One lap is displaced by a fixed vector (what a
    receiver does) and another by the same distance along its own normal (what a driver does).
    The fit must recover the first and leave the second alone — and it must say so in its
    residual, which is the quantity that reports "this lap was somewhere else"."""
    s = planted_session()
    g = s.corners.geometry()
    assert g is not None, "no geometry fitted on a five-lap fixture"

    t_receiver = g.shift[1]
    want = np.array([PLANTED_M * 0.6, PLANTED_M * 0.8])
    err = float(np.hypot(*(t_receiver - want)))
    assert err <= 0.25, f"planted translation {want} came back {t_receiver} (error {err:.3f} m)"
    assert g.residual_rms[1] <= 0.25, (
        f"a pure translation should leave nothing behind, left {g.residual_rms[1]:.3f} m")

    t_driver = float(np.hypot(*g.shift[2]))
    assert t_driver <= 0.25, (
        f"THE WIDE LINE WAS ABSORBED: a {PLANTED_M:g} m parallel curve was read as a "
        f"{t_driver:.2f} m receiver shift. A rigid fit that does this moves real driving.")
    assert g.residual_rms[2] >= 0.5 * PLANTED_M, (
        f"the wide line should survive the fit as residual, got {g.residual_rms[2]:.2f} m")
    print(f"ok translation recovered to {err:.3f} m; a {PLANTED_M:g} m wide line read as "
          f"{t_driver:.2f} m of shift ({100 * t_driver / PLANTED_M:.0f} % absorbed), residual "
          f"{g.residual_rms[2]:.2f} m")


def test_a_lap_the_receiver_moved_resolves_its_corners_again():
    """FAILS ON THE TREE BEFORE THIS CHANGE. Lap 1 sits 4 m away — past
    `corners.SPATIAL_MATCH_MAX_M` — so without de-drifting, every one of its boundaries fails the
    distance gate, its warp is None and `lap_corner_resolved` reads all-False. The driving is
    identical to the consensus laps', so every corner must come back."""
    before = without_geometry(planted_session())
    assert not any(before.corners.lap_corner_resolved(1)), (
        f"TOOTHLESS FIXTURE: lap 1 already resolved without de-drifting "
        f"({before.corners.lap_corner_resolved(1)}) — the assertions below prove nothing")

    s = planted_session()
    assert s.corners.lap_alignment(1, total_of(s, 1)) is not None, (
        "the de-drifted lap produced no warp at all")
    assert all(s.corners.lap_corner_resolved(1)), (
        f"the displaced lap still does not resolve: {s.corners.lap_corner_resolved(1)}")
    assert all(s.corners.lap_edge_resolved(1)), "an edge of the displaced lap is still interpolated"
    for lap in (3, 4):
        assert all(s.corners.lap_corner_resolved(lap)), f"lap {lap} (on the line) regressed"
    print("ok a lap the receiver moved 4 m resolves every corner and every edge again "
          "(and resolved none of them without the fix)")


def test_a_lap_the_driver_moved_is_not_rescued():
    """THE OTHER HALF OF THE CONTROL, at the surface the user reads. Lap 2 really did drive 4 m
    wide, so its corner edges really are further from the reference line than the gate allows and
    they must stay interpolated. If this ever passes, the fix is moving driving."""
    s = planted_session()
    assert not any(s.corners.lap_corner_resolved(2)), (
        f"a genuinely wide lap was rescued: {s.corners.lap_corner_resolved(2)}")
    print("ok a lap that drove 4 m wide is still reported as interpolated, as it should be")


def test_the_reference_lap_keeps_the_exact_identity_warp():
    """THE REFERENCE LAP IS THE FRAME. Its odometer is what every corner boundary is expressed in,
    so its own warp has to map each boundary to itself — even when IT is the displaced lap, which
    is D24 0060's case exactly (its fastest lap ranks 34/38 from the consensus). Applying the
    anchor correction to it would push its own boundaries a metre or more sideways, past the same
    3 m gate, and `lap_corner_resolved(best)` would start reading False."""
    s = planted_session(best=1)          # the DISPLACED lap is the reference here
    g = s.corners.geometry()
    assert float(np.hypot(*g.shift[1])) >= 2.0, (
        "fixture is toothless: the reference lap is not displaced from the consensus")
    frame = np.asarray([b for c in s.corners.corner_list()
                        for b in (float(c.enter), float(c.exit))], float)
    warp = s.corners.lap_alignment(1, total_of(s, 1))
    assert warp is not None, "the reference lap produced no warp"
    moved = np.max(np.abs(C.project_boundaries(frame, total_of(s, 1), total_of(s, 1),
                                               alignment=warp) - frame))
    assert moved <= 1e-6, f"the reference lap's own boundaries moved {moved:g} m"
    assert all(s.corners.lap_corner_resolved(1)), "the reference lap stopped resolving its corners"
    print(f"ok the reference lap keeps the identity warp (max move {moved:g} m) even when it is "
          f"the lap furthest from the consensus")


def test_a_session_with_no_drift_matches_bit_for_bit():
    """A recording whose laps sit on one line must be projected EXACTLY as before: the consensus
    is zero, every shift is zero, and `x - 0.0` / `x + 0.0` are the same double. This is what
    keeps the change out of recordings that do not need it — and what keeps the synthetic golden
    fixture from moving for no reason."""
    xs, ys = stadium()
    s = session({0: (xs, ys), 1: (xs, ys), 2: (xs, ys)})
    g = s.corners.geometry()
    assert g is not None, "no geometry fitted"
    assert np.allclose(g.consensus, 0.0, atol=1e-9), "a session on one line has a non-zero consensus"
    for lap in (0, 1, 2):
        assert s.corners.lap_shift(lap) == (0.0, 0.0), f"lap {lap} got a shift out of nowhere"
    for lap in (1, 2):
        assert same_warp(s.corners.lap_alignment(lap, total_of(s, lap)),
                         uncorrected_warp(s, lap)), (
            f"lap {lap}'s warp is not bit-identical to the uncorrected one")
    print("ok a drift-free session is matched bit for bit as before")


def test_a_short_session_fits_no_geometry_and_changes_nothing():
    """Below `corners.DRIFT_MIN_LAPS` a median over laps is not a consensus, so no geometry is
    fitted and every lap keeps the uncorrected match. Fail-closed: the correction only exists
    where there is enough evidence to fit it."""
    xs, ys = stadium()
    s = session({0: (xs, ys), 1: (xs + PLANTED_M * 0.6, ys + PLANTED_M * 0.8)})
    assert len(s.corners._clean_lap_ids()) < C.DRIFT_MIN_LAPS
    assert s.corners.geometry() is None, "a two-lap session fitted a consensus anyway"
    assert s.corners.lap_shift(1) == (0.0, 0.0)
    assert same_warp(s.corners.lap_alignment(1, total_of(s, 1)), uncorrected_warp(s, 1)), (
        "a session below the minimum lap count was still corrected")
    print("ok a session with too few laps fits nothing and is matched exactly as before")


def test_a_lap_outside_the_clean_set_is_still_measured_against_the_consensus():
    """A lap the consensus was not fitted FROM — a GPS-dropout lap is excluded from every "best"
    in the app but its corner windows are still drawn — must be measured against that consensus
    rather than left on the uncorrected match, or one session carries two frames."""
    s = planted_session()
    g = s.corners.geometry()
    xs, ys = stadium()
    moved_x, moved_y = xs - 3.0, ys + 1.0
    t, residual = g.fit(moved_x, moved_y, lap_columns(moved_x, moved_y)[4])
    assert float(np.hypot(*(t - np.array([-3.0, 1.0])))) <= 0.25, f"late lap fitted {t}"
    assert residual <= 0.25, f"a pure translation left {residual:.3f} m behind"
    print(f"ok a lap outside the consensus set is fitted on demand ({t[0]:+.2f}, {t[1]:+.2f})")


TESTS = [
    test_a_planted_translation_is_recovered_and_a_planted_wide_line_is_not,
    test_a_lap_the_receiver_moved_resolves_its_corners_again,
    test_a_lap_the_driver_moved_is_not_rescued,
    test_the_reference_lap_keeps_the_exact_identity_warp,
    test_a_session_with_no_drift_matches_bit_for_bit,
    test_a_short_session_fits_no_geometry_and_changes_nothing,
    test_a_lap_outside_the_clean_set_is_still_measured_against_the_consensus,
]

if __name__ == "__main__":
    for t in TESTS:
        t()
    print(f"\nall {len(TESTS)} corner-drift tests passed")
