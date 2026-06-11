"""Bounds-check surface of the Python-bound Laps accessors (P1.2).

A bad index from Python used to index the underlying C++ vectors UNGUARDED (UB in a
Release build). The 8 scalar accessors (lap_time / start_timestamp / lap_entry_speed /
get_lap_distance / get_point / sector_time / sector_start_timestamp / sector_entry_speed)
now throw std::out_of_range, which nanobind translates to a Python IndexError. The
empty-return trio get_lap / sample_count / lap_columns keeps its documented contract.

Pure Python + the pacer bindings (no telemetry file, no Qt).
Run: python tests/test_laps_bindings.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402


def _vertical_line(x):
    """A vertical timing line at local x, spanning y in [-10, 10]."""
    a, b = pacer.Point(), pacer.Point()
    a.x, a.y, b.x, b.y = float(x), -10.0, float(x), 10.0
    seg = pacer.Segment()
    seg.first, seg.second = a, b
    return seg


def _make_tiny_laps():
    """A straight run along local x with a start line at x == 0 and one sector line at
    x == 2: one lap chunk (the single start crossing) and two recorded sector chunks
    (the rotating boundary crosses the start line, then the sector line)."""
    origin = pacer.GPSSample(lat=40.0, lon=-74.0, altitude=0.0)
    cs = pacer.CoordinateSystem(origin)
    laps = pacer.Laps()
    for x, t in [(-7.0, 0.0), (-3.0, 1.0), (1.0, 2.0), (5.0, 3.0)]:
        laps.add_point(cs.global_(pacer.Vec3f(x, 0.0, 0.0)), t)
    laps.set_coordinate_system(cs)
    laps.sectors = pacer.Sectors(start_line=_vertical_line(0.0),
                                 sector_lines=[_vertical_line(2.0)])
    laps.update()
    assert laps.laps_count() == 1, laps.laps_count()
    assert laps.recorded_sectors() == 2, laps.recorded_sectors()
    assert laps.point_count() == 4, laps.point_count()
    return laps


def _assert_index_error(fn, idx):
    name = getattr(fn, "__name__", repr(fn))
    try:
        fn(idx)
    except IndexError:
        return
    raise AssertionError(f"{name}({idx}) did not raise IndexError")


def test_lap_accessors_raise_index_error_out_of_range():
    """Each bound per-lap scalar accessor raises IndexError (translated from C++
    std::out_of_range) for lap == laps_count() and a huge lap — and still answers
    in-range calls."""
    laps = _make_tiny_laps()
    count = laps.laps_count()
    for fn in (laps.lap_time, laps.start_timestamp, laps.lap_entry_speed,
               laps.get_lap_distance):
        fn(count - 1)  # in-range: must not raise
        for bad in (count, 9999):
            _assert_index_error(fn, bad)
    print("test_lap_accessors_raise_index_error_out_of_range OK")


def test_sector_and_point_accessors_raise_index_error_out_of_range():
    """Same IndexError contract for the per-sector accessors (bounded by
    recorded_sectors()) and get_point (bounded by point_count())."""
    laps = _make_tiny_laps()
    recorded = laps.recorded_sectors()
    for fn in (laps.sector_time, laps.sector_start_timestamp,
               laps.sector_entry_speed):
        fn(recorded - 1)  # in-range: must not raise
        for bad in (recorded, 9999):
            _assert_index_error(fn, bad)

    points = laps.point_count()
    assert laps.get_point(points - 1).time == 3.0
    for bad in (points, 9999):
        _assert_index_error(laps.get_point, bad)
    print("test_sector_and_point_accessors_raise_index_error_out_of_range OK")


def test_empty_return_trio_contract_unchanged():
    """get_lap / sample_count / lap_columns keep their documented EMPTY/0 returns for an
    out-of-range lap (they must NOT have grown the throwing behavior)."""
    laps = _make_tiny_laps()
    for bad in (laps.laps_count(), 9999):
        assert laps.sample_count(bad) == 0
        assert laps.get_lap(bad).count() == 0
        cols = laps.lap_columns(bad)
        assert len(cols.times) == 0 and len(cols.cum_distances) == 0
    print("test_empty_return_trio_contract_unchanged OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} LAPS BINDING TESTS PASSED")
