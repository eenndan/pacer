"""The crossing test, as Python sees it (H4).

`Segment::Intersects` answers through a `double *ratio` OUT-PARAMETER. litgen bound that as
a by-value `float`, so the Python call type-checked, returned the right bool, wrote the
crossing fraction into a temporary and dropped it:

    seg.intersects(a, b, 0.0)   ->  True, and the caller's 0.0 was still 0.0 (answer: 0.25)
    seg.intersects(a, b, None)  ->  TypeError

A binding that looks like it works and silently loses its output is worse than none. Python
now gets `Segment.intersection_ratio(fst, snd) -> float | None` instead, over the same
arithmetic (`Intersects` is an adapter over `IntersectionRatio`, not a second copy of it).

Two things are pinned here:

  1. THE SHAPE OF THE BINDING, because it is produced by a GENERATOR. The exclusion of
     `intersects` and the exposure of `intersection_ratio` live in
     `bindings/pacer/generate-bindings.py`; a future regen that lost either would restore the
     lossy call silently. This file fails instead, naming which half moved.

  2. THE PROVENANCE TRANSCRIPTION, value for value. `studio/provenance.py` re-derives the
     crossing in pure Python — deliberately, because the layering contract
     (tests/test_layering.py) forbids `provenance` from importing the pacer core, and the
     panel is supposed to re-derive rather than quote. Its own docstring calls it "a
     transcription, not a second implementation". Until the core's ratio was reachable from
     Python that claim could only be checked INDIRECTLY, through lap-boundary times on a real
     session (tests/test_provenance.py). Now it can be checked directly, on the number itself,
     over chords chosen to hit both branches and both strict-sign edges. A test file is free to
     import both sides; only `studio/` is constrained.

Pure Python + the pacer bindings and studio.provenance (no Qt, no telemetry file).
Run: python tests/test_geometry_bindings.py
"""
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402
from studio import provenance  # noqa: E402


def _point(x, y):
    p = pacer.Point()
    p.x, p.y = float(x), float(y)
    return p


def _segment(first, second):
    s = pacer.Segment()
    s.first, s.second = _point(*first), _point(*second)
    return s


# The vertical timing line at x == 0 that every hand-written case below crosses.
_LINE = ((0.0, -1.0), (0.0, 1.0))


def test_python_reads_the_crossing_fraction_the_out_parameter_used_to_swallow():
    """The exact case the old binding got wrong: a chord from x=-1 to x=3 crosses x=0 one
    quarter of the way along, and `intersects(a, b, 0.0)` used to return True while leaving
    the caller's 0.0 alone. The fraction now comes back as the return value."""
    line = _segment(*_LINE)
    ratio = line.intersection_ratio(_point(-1, 0), _point(3, 0))
    assert ratio == 0.25, ratio
    # ...and it means what the docstring says it means: fst*(1-r) + snd*r is ON the line.
    fst, snd = (-1.0, 0.0), (3.0, 0.0)
    x = fst[0] * (1 - ratio) + snd[0] * ratio
    assert x == 0.0, x
    print(f"test_python_reads_the_crossing_fraction_the_out_parameter_used_to_swallow OK — {ratio}")


def test_the_out_parameter_spelling_is_not_on_the_python_surface():
    """`intersects` must stay excluded in `bindings/pacer/generate-bindings.py`. It is bound
    from a `double *` out-parameter, which litgen renders as a by-value `float`: the call
    succeeds, the answer goes into a temporary and is lost. If a regeneration ever puts it
    back, that silent form returns with it — so the absence is the thing to assert, not a
    comment to trust."""
    assert not hasattr(pacer.Segment, "intersects"), (
        "pacer.Segment.intersects is bound again — its `double *ratio` out-parameter cannot be "
        "read from Python. Restore the `^Intersects$` exclusion in "
        "bindings/pacer/generate-bindings.py and regenerate.")
    assert hasattr(pacer.Segment, "intersection_ratio"), (
        "pacer.Segment.intersection_ratio is gone — the crossing test is unreachable from "
        "Python again.")
    print("test_the_out_parameter_spelling_is_not_on_the_python_surface OK")


def test_a_miss_and_a_touch_are_both_no_crossing():
    """The strict-sign rule, read through the binding: a chord that stops short of the line
    does not cross it, and neither does one whose endpoint sits exactly ON it. That second
    one is what stops a single pass being counted twice."""
    line = _segment(*_LINE)
    assert line.intersection_ratio(_point(-2, 0), _point(-1, 0)) is None, "a miss is not a cross"
    assert line.intersection_ratio(_point(0, 0), _point(1, 0)) is None, "a touch is not a cross"
    assert line.intersection_ratio(_point(-1, 0), _point(0, 0)) is None, "a touch is not a cross"
    # Past the line's own ENDPOINT is a miss too: the test is segment-vs-segment, not ray-vs-line.
    assert line.intersection_ratio(_point(-1, 5), _point(1, 5)) is None, "beyond the line's end"
    # The OTHER strict-sign edge, mirroring the tangential-touch case in tests/test_geometry.cpp:
    # a chord passing exactly through an ENDPOINT of the timing line grazes it, from either end.
    for graze in (_segment((0, 0), (0, 1)), _segment((0, -1), (0, 0))):
        assert graze.intersection_ratio(_point(-1, 0), _point(1, 0)) is None, "a graze is no cross"
    print("test_a_miss_and_a_touch_are_both_no_crossing OK")


def _sweep_chords(n, rng):
    """`n` chords around a degrees-scale timing line, deliberately mixing crossings, misses and
    exact-on-the-line endpoints (the strict-sign edge) so both branches are exercised. Degrees
    around a real track, so the arithmetic runs at the magnitudes the core actually sees."""
    lat0, lon0 = 51.3, -0.28
    half = 6e-5                        # ~7 m of timing line, half-length
    line = ((lon0, lat0 - half), (lon0, lat0 + half))
    for _ in range(n):
        y = lat0 + rng.uniform(-1.5 * half, 1.5 * half)
        y2 = y + rng.uniform(-half, half)
        kind = rng.randrange(8)
        if kind == 0:                  # endpoint EXACTLY on the line's supporting line
            first, second = (lon0, y), (lon0 + rng.uniform(1e-6, 2e-4), y2)
        elif kind == 1:
            first, second = (lon0 - rng.uniform(1e-6, 2e-4), y), (lon0, y2)
        elif kind == 2:
            # THE OTHER strict-sign edge, and the one a sweep is most likely to miss: a chord
            # whose own supporting line passes EXACTLY through an ENDPOINT of the timing line
            # (a tangential graze — tests/test_geometry.cpp pins it as no crossing). This is
            # straddle test ONE hitting exactly zero, which the endpoint-on-x==lon0 cases above
            # never reach; without it, relaxing that test's `>= 0` to `> 0` goes unnoticed.
            end_y = lat0 - half if rng.random() < 0.5 else lat0 + half
            dx = rng.uniform(1e-6, 2e-4)
            first, second = (lon0 - dx, end_y), (lon0 + dx, end_y)
        elif kind < 6:                 # straddles x == lon0; crosses iff it does so within the line
            first = (lon0 - rng.uniform(1e-6, 2e-4), y)
            second = (lon0 + rng.uniform(1e-6, 2e-4), y2)
        else:                          # free chord: mostly misses, occasionally clips the line
            first = (lon0 + rng.uniform(-3e-4, 3e-4), y)
            second = (first[0] + rng.uniform(-3e-4, 3e-4),
                      y + rng.uniform(-2 * half, 2 * half))
        yield line, first, second


def test_the_provenance_transcription_equals_the_core_value_for_value():
    """`provenance.crossing_fraction` claims to be `Segment::Intersects` transcribed operation
    for operation. With the core's ratio finally reachable from Python, that is checkable on
    the number itself rather than through a lap time: same crossings, same misses, and the same
    double — not within a tolerance, EXACTLY."""
    rng = random.Random(20260917)      # fixed seed: this sweep is reproducible, not sampled anew
    crossings = misses = 0
    worst = None
    for line_pts, first, second in _sweep_chords(4000, rng):
        core = _segment(*line_pts).intersection_ratio(_point(*first), _point(*second))
        mine = provenance.crossing_fraction(line_pts, first, second)
        if core is None or mine is None:
            assert core is None and mine is None, (line_pts, first, second, core, mine)
            misses += 1
            continue
        crossings += 1
        if core != mine:
            worst = (line_pts, first, second, core, mine)
            break
        assert 0.0 <= core <= 1.0, core
    assert worst is None, (
        "the Python transcription in studio/provenance.py no longer agrees with the core's "
        f"crossing test: line={worst[0]} chord={worst[1]}->{worst[2]} "
        f"core={worst[3]!r} transcription={worst[4]!r}")
    # A sweep that hit only one branch would prove nothing, so pin that both ran.
    assert crossings > 300, crossings
    assert misses > 300, misses
    print(f"test_the_provenance_transcription_equals_the_core_value_for_value OK — "
          f"{crossings} crossings + {misses} non-crossings, all exact")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} GEOMETRY BINDING TESTS PASSED")
