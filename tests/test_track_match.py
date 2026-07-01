"""Geometry track-match (studio.track_match) — pure-Python unit tests.

The admission gate for the cross-recording reference ("race a friend's GoPro") falls back to GPS
GEOMETRY when either recording has no detected track NAME. This module tests that pure decision
directly on lat/lon centroids + bboxes — NO telemetry file, NO pacer build, NO Qt (track_match is
numpy/math only). The Session-level integration (the gate wiring + the UNVERIFIED flag) is covered
in test_cross_reference.py; here we pin the geometry primitives and the two-check same/not-same
verdict from first principles.

Run:  python tests/test_track_match.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import track_match as tm  # noqa: E402


# ---------------------------------------------------------------- the haversine primitive
def test_haversine_zero_and_symmetry():
    assert tm.haversine_m(52.0, -0.78, 52.0, -0.78) == 0.0
    d1 = tm.haversine_m(52.0, -0.78, 52.01, -0.77)
    d2 = tm.haversine_m(52.01, -0.77, 52.0, -0.78)
    assert abs(d1 - d2) < 1e-9, (d1, d2)  # symmetric
    print("test_haversine_zero_and_symmetry OK")


def test_haversine_one_degree_latitude():
    # One degree of latitude is ~111 km everywhere; check we're within 1 % of that.
    d = tm.haversine_m(52.0, -0.78, 53.0, -0.78)
    assert abs(d - 111_195.0) / 111_195.0 < 0.01, d
    print(f"test_haversine_one_degree_latitude OK ({d:.0f} m)")


def test_bbox_diagonal():
    # A 0.0009°-half-extent box at lat 52° spans ~200 m in latitude but only ~123 m in longitude
    # (cos 52° ≈ 0.616 compresses lon degrees), so its diagonal ≈ hypot(200, 123) ≈ 235 m.
    diag = tm.bbox_diagonal_m((52.0403 - 0.0009, -0.7847 - 0.0009,
                               52.0403 + 0.0009, -0.7847 + 0.0009))
    assert 210.0 < diag < 260.0, diag
    print(f"test_bbox_diagonal OK ({diag:.0f} m)")


# ---------------------------------------------------------------- the two-check verdict
def _bbox(clat, clon, half=0.0009):
    return (clat - half, clon - half, clat + half, clon + half)


def test_same_place_same_size_matches():
    # Centroids ~6 m apart, identical ~100 m footprints -> same circuit.
    a_c, a_b = (52.0403, -0.7847), _bbox(52.0403, -0.7847)
    b_c, b_b = (52.04035, -0.78475), _bbox(52.04035, -0.78475)
    v = tm.match(a_c, a_b, b_c, b_b)
    assert v.same_track and v.reason == "", v
    assert v.distance_m < 20.0 and v.size_ratio < 1.1, v
    print(f"test_same_place_same_size_matches OK (d={v.distance_m:.1f} m, ratio={v.size_ratio:.2f})")


def test_far_apart_refuses():
    # ~29 km apart (0.26° latitude) -> different circuits.
    a_c, a_b = (52.0403, -0.7847), _bbox(52.0403, -0.7847)
    b_c, b_b = (52.30, -0.7847), _bbox(52.30, -0.7847)
    v = tm.match(a_c, a_b, b_c, b_b)
    assert not v.same_track and "apart" in v.reason, v
    assert v.distance_m > tm.MATCH_RADIUS_M
    print(f"test_far_apart_refuses OK (d={v.distance_m:.0f} m, {v.reason!r})")


def test_same_centroid_different_size_refuses():
    # Coincident centroids but ~10× different footprint -> different tracks (the size guard).
    a_c, a_b = (52.0403, -0.7847), _bbox(52.0403, -0.7847, half=0.0009)   # ~100 m
    b_c, b_b = (52.0403, -0.7847), _bbox(52.0403, -0.7847, half=0.009)     # ~1 km
    v = tm.match(a_c, a_b, b_c, b_b)
    assert not v.same_track and "size" in v.reason, v
    assert v.distance_m < 1.0 and v.size_ratio > tm.SIZE_RATIO_MAX, v
    print(f"test_same_centroid_different_size_refuses OK (ratio={v.size_ratio:.1f}×)")


def test_just_inside_and_just_outside_radius():
    # A match just under the radius passes; a hair over it fails (the boundary is honoured).
    a_c, a_b = (52.0403, -0.7847), _bbox(52.0403, -0.7847)
    # Move due north by ~ (radius - 50) m and by ~ (radius + 50) m.
    deg_per_m = 1.0 / 111_195.0
    near = 52.0403 + (tm.MATCH_RADIUS_M - 50.0) * deg_per_m
    far = 52.0403 + (tm.MATCH_RADIUS_M + 50.0) * deg_per_m
    assert tm.match(a_c, a_b, (near, -0.7847), _bbox(near, -0.7847)).same_track
    assert not tm.match(a_c, a_b, (far, -0.7847), _bbox(far, -0.7847)).same_track
    print("test_just_inside_and_just_outside_radius OK")


def test_degenerate_zero_size_footprint_refuses():
    # A zero-extent (single-point) footprint has no measurable size -> never matches.
    a_c, a_b = (52.0403, -0.7847), _bbox(52.0403, -0.7847)
    zero_b = (52.0403, -0.7847, 52.0403, -0.7847)
    v = tm.match(a_c, a_b, (52.0403, -0.7847), zero_b)
    assert not v.same_track and not math.isfinite(v.size_ratio), v
    print("test_degenerate_zero_size_footprint_refuses OK")


if __name__ == "__main__":
    for fn in list(globals().values()):
        if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
            fn()
    print("\nALL TRACK-MATCH TESTS PASSED")
