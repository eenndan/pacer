"""Bounds-check surface of the Python-bound Laps accessors (P1.2), and what a field read hands back.

A bad index from Python used to index the underlying C++ vectors UNGUARDED (UB in a
Release build). The 8 scalar accessors (lap_time / start_timestamp / lap_entry_speed /
get_lap_distance / get_point / sector_time / sector_start_timestamp / sector_entry_speed)
now throw std::out_of_range, which nanobind translates to a Python IndexError. The
empty-return trio get_lap / sample_count / lap_columns keeps its documented contract.

A stored field read is the caller's own value (X5). nanobind's `def_rw` getter hands back a
REFERENCE into the C++ object by default, so `kept = laps.sectors.start_line` moved with every later
line edit, and every element of `laps.sectors.sector_lines` / `lap.points` pointed into the vector's
heap buffer: reassign the field and a kept element read freed memory. The three fields a caller
stores now return copies (bindings/pacer/generate-bindings.py, `_COPY_ON_READ`); `laps.sectors`
itself stays a live view, so `laps.sectors.start_line = seg` still writes into the laps.

Pure Python + the pacer bindings (no telemetry file, no Qt).
Run: python tests/test_laps_bindings.py
"""
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
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


def _ends(seg):
    return (seg.first.x, seg.first.y, seg.second.x, seg.second.y)


def test_a_stored_start_line_is_a_snapshot():
    """`kept = laps.sectors.start_line` keeps the line it read. It used to be a live view of the
    C++ member, so a later edit moved it too: an undo stack, a "before" snapshot or an auto-fit
    that compared the line it started from against the line it ended on compared a line with
    itself. The one-level write `laps.sectors.start_line = seg` must still reach the laps."""
    laps = _make_tiny_laps()
    kept = laps.sectors.start_line
    laps.sectors = pacer.Sectors(start_line=_vertical_line(1.0), sector_lines=[])
    assert _ends(kept) == (0.0, -10.0, 0.0, 10.0), \
        f"a stored start line moved with the laps' next edit: now {_ends(kept)}"
    laps.sectors.start_line = _vertical_line(-1.0)
    assert _ends(kept) == (0.0, -10.0, 0.0, 10.0), _ends(kept)
    assert _ends(laps.sectors.start_line) == (-1.0, -10.0, -1.0, 10.0), \
        "laps.sectors.start_line = seg no longer writes into the laps"
    print("test_a_stored_start_line_is_a_snapshot OK")


def test_a_stored_sector_line_survives_the_next_edit():
    """An element of `laps.sectors.sector_lines` pointed INTO the vector's buffer, and
    `Session.set_timing_lines` edits by assigning a whole new `pacer.Sectors`. At the same length
    the buffer is reused, so the kept element read the NEW line (200 of 200 runs); one line longer
    and the buffer is freed, so it read freed memory: (0.0, 2.8e-309) in 199 of 200 runs."""
    laps = _make_tiny_laps()
    kept = laps.sectors.sector_lines[0]
    laps.sectors = pacer.Sectors(start_line=_vertical_line(0.0),
                                 sector_lines=[_vertical_line(3.0)])
    assert _ends(kept) == (2.0, -10.0, 2.0, 10.0), \
        f"a stored sector line moved with a same-length edit: now {_ends(kept)}"
    kept = laps.sectors.sector_lines[0]
    laps.sectors = pacer.Sectors(start_line=_vertical_line(0.0),
                                 sector_lines=[_vertical_line(4.0), _vertical_line(5.0)])
    assert _ends(kept) == (3.0, -10.0, 3.0, 10.0), \
        f"a stored sector line read freed memory once the vector grew: {_ends(kept)}"
    laps.sectors.sector_lines = [_vertical_line(6.0)]
    assert [_ends(s) for s in laps.sectors.sector_lines] == [(6.0, -10.0, 6.0, 10.0)], \
        "laps.sectors.sector_lines = [...] no longer writes into the laps"
    print("test_a_stored_sector_line_survives_the_next_edit OK")


def test_a_stored_lap_point_survives_the_next_edit():
    """`lap.points` is the same shape (a vector of bound structs), reached through a setter that
    MOVES the new vector in: the first reassignment frees the kept point's buffer and the second
    is handed that block back, so an unfixed kept point read 200.0 in 200 of 200 runs."""
    fix = pacer.GPSSample(lat=40.0, lon=-74.0, altitude=0.0)

    def points(times):
        return [pacer.PointInTime_GPSSample(point=fix, time=float(t)) for t in times]

    lap = pacer.Lap(points=points(range(3)))
    kept = lap.points[0]
    lap.points = points(range(100, 103))
    lap.points = points(range(200, 203))
    assert kept.time == 0.0, f"a stored lap point changed with the next edits: 0.0 -> {kept.time}"
    assert [p.time for p in lap.points] == [200.0, 201.0, 202.0], "lap.points = [...] stopped writing"
    print("test_a_stored_lap_point_survives_the_next_edit OK")


def test_every_list_of_bound_structs_is_read_as_copies():
    """The guard for the next field: a field typed `List[<a bound class>]` in the stub is a
    `std::vector` of structs, and without `rv_policy::copy` its elements point into the vector's
    buffer (the dangling read above). Every such field must be in the generator's copy list."""
    stub = open(os.path.join(REPO, "bindings/pacer/pacer/__init__.pyi")).read()
    cpp = open(os.path.join(REPO, "bindings/pacer/nanobind_pacer.cpp")).read()
    bound = set(re.findall(r"^class (\w+)", stub, re.M))
    fields, cls = [], None
    for line in stub.splitlines():
        head = re.match(r"class (\w+)", line)
        if head:
            cls = head.group(1)
            continue
        m = re.match(r"    (\w+): List\[(\w+)(?:\[(\w+)\])?\]", line)
        if m and cls and "_".join(filter(None, m.group(2, 3))) in bound:
            fields.append((cls, m.group(1)))
    assert ("Sectors", "sector_lines") in fields and ("Lap", "points") in fields, fields
    for cls, field in fields:
        rw = re.search(rf'\.def_rw\("{field}", &pacer::[\w<>]+::{field},[^\n]*', cpp)
        assert rw and "rv_policy::copy" in rw.group(0), \
            f"{cls}.{field} hands back references into its vector: {rw and rw.group(0)}"
    print("test_every_list_of_bound_structs_is_read_as_copies OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok  {t.__name__}")
    print(f"\nALL {len(tests)} LAPS BINDING TESTS PASSED")
