"""Synthetic unit tests for studio.gapfill — the MAP-ONLY GPS-gap reconstruction.

Pure Python + numpy: no `pacer`, no telemetry file, fast. A regression guard for the gap
detection, cross-lap borrow (curved arc, pinned endpoints), short-gap spline, reference
fallback ordering, and segment continuity. Run:  python tests/test_gapfill.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio import gapfill as gf  # noqa: E402


def make_circle_lap(n=200, r=50.0, gap=None, dt=0.1, rot=0.0, scale=1.0):
    """A clean circular lap; if gap=(lo,hi) drop those indices (interior dropout)."""
    th = np.linspace(0, 2 * np.pi, n, endpoint=False)
    xs = scale * r * np.cos(th + rot)
    ys = scale * r * np.sin(th + rot)
    ts = np.arange(n) * dt
    keep = np.ones(n, bool)
    if gap is not None:
        keep[gap[0]:gap[1]] = False
    return xs[keep], ys[keep], ts[keep]


def test_find_gaps():
    xs, ys, ts = make_circle_lap(gap=(80, 120))  # drop 40 samples
    gaps = gf.find_gaps(ts, med_dt=0.1)
    assert len(gaps) == 1, gaps
    g = gaps[0]
    assert g["dt"] > 4.0, g
    assert 38 <= g["n_missing"] <= 42, g
    print("test_find_gaps OK:", g)


def test_no_gap_when_clean():
    xs, ys, ts = make_circle_lap()
    assert gf.find_gaps(ts, med_dt=0.1) == []
    segs, fills = gf.reconstruct_lap(xs, ys, ts, donors=[])
    assert len(segs) == 1 and segs[0].measured and fills == []
    print("test_no_gap_when_clean OK")


def test_cross_lap_borrow_fills_arc():
    # Lap A has a big gap; donor B is a clean circle (same track). Borrow should reconstruct
    # a curved arc (NOT a straight chord) connecting the mouths.
    ax, ay, at = make_circle_lap(gap=(80, 120))
    bx, by, bt = make_circle_lap(rot=0.003, scale=1.001)  # near-identical "other lap"
    donors = [{"xy": np.column_stack([bx, by]), "name": "1", "is_reference": False}]
    segs, fills = gf.reconstruct_lap(ax, ay, at, donors=donors)
    assert len(fills) == 1
    f = fills[0]
    assert f["source"].startswith("borrow:"), f
    # the inferred segment must follow the circle: its max deviation from radius r=50 is small
    inferred = [s for s in segs if not s.measured]
    assert len(inferred) == 1
    s = inferred[0]
    rad = np.hypot(s.xs, s.ys)
    assert np.all(np.abs(rad - 50.0) < 2.0), (rad.min(), rad.max())
    # endpoints pinned to the mouths
    assert np.hypot(s.xs[0] - ax[79], s.ys[0] - ay[79]) < 1e-6
    assert np.hypot(s.xs[-1] - ax[80], s.ys[-1] - ay[80]) < 1e-6  # ax[80] is the after-point
    # the fill arc length must be longer than the straight chord (it curves, not a shortcut)
    chord = np.hypot(ax[80] - ax[79], ay[80] - ay[79])
    assert f["fill_m"] > chord * 1.03, (f["fill_m"], chord)
    assert f["endpoint_err_m"] < 1.0, f
    print("test_cross_lap_borrow_fills_arc OK:", f)


def test_short_gap_uses_spline():
    xs, ys, ts = make_circle_lap(gap=(80, 83))  # drop 3 samples -> 0.4s hole, n_missing=3
    segs, fills = gf.reconstruct_lap(xs, ys, ts, donors=[])
    assert len(fills) == 1 and fills[0]["source"] == "spline", fills
    inferred = [s for s in segs if not s.measured]
    assert len(inferred) == 1
    # spline should still hug the circle reasonably
    s = inferred[0]
    rad = np.hypot(s.xs, s.ys)
    assert np.all(np.abs(rad - 50.0) < 3.0), (rad.min(), rad.max())
    print("test_short_gap_uses_spline OK:", fills[0])


def test_reference_fallback_when_no_donor():
    # No clean donor covers the gap → reference (a circle) used as last resort.
    ax, ay, at = make_circle_lap(gap=(80, 120))
    ref = make_circle_lap()  # acts as reference centerline
    donors = [{"xy": np.column_stack([ref[0], ref[1]]), "name": "MK", "is_reference": True}]
    segs, fills = gf.reconstruct_lap(ax, ay, at, donors=donors)
    assert fills[0]["source"] == "reference", fills
    print("test_reference_fallback_when_no_donor OK:", fills[0])


def test_borrow_preferred_over_reference():
    ax, ay, at = make_circle_lap(gap=(80, 120))
    donor = make_circle_lap(rot=0.002)
    ref = make_circle_lap()
    donors = [
        {"xy": np.column_stack([donor[0], donor[1]]), "name": "5", "is_reference": False},
        {"xy": np.column_stack([ref[0], ref[1]]), "name": "MK", "is_reference": True},
    ]
    segs, fills = gf.reconstruct_lap(ax, ay, at, donors=donors)
    assert fills[0]["source"].startswith("borrow:"), fills
    print("test_borrow_preferred_over_reference OK:", fills[0])


def test_endpoints_continuous():
    # The drawn segments must chain end-to-end with no jumps (continuity).
    ax, ay, at = make_circle_lap(gap=(80, 120))
    bx, by, bt = make_circle_lap(rot=0.003)
    donors = [{"xy": np.column_stack([bx, by]), "name": "1", "is_reference": False}]
    segs, _ = gf.reconstruct_lap(ax, ay, at, donors=donors)
    for k in range(len(segs) - 1):
        end = np.array([segs[k].xs[-1], segs[k].ys[-1]])
        nxt = np.array([segs[k + 1].xs[0], segs[k + 1].ys[0]])
        assert np.hypot(*(end - nxt)) < 1e-6, (k, end, nxt)
    print("test_endpoints_continuous OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} GAPFILL TESTS PASSED")
