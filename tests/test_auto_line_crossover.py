"""The unknown-track start/finish line is never left where ANOTHER stretch of track cuts it (NEW-3a).

`load._heuristic_start_base` puts the line square to travel where the speed stays highest (X1;
`studio/docs/refused-2026-09.md` §15 keeps it there). On a circuit that crosses itself (a figure-8
with a bridge), that place can be on the crossover, and then the OTHER pass cuts the line too.
Every lap becomes two pieces, one per loop of the 8, and neither guard behind the heuristic sees
it. Each piece starts and ends at the crossover, so its ends are metres apart, and it turns through
the angle between the two passes, which is under the closure test's 120 deg
(`_signal.MAX_LAP_TURN_DEG`). The pieces are also the majority, so the time and distance bands
centre on them. Measured on the fixture here before the fix (`_synthetic.figure8_trace`: 8 laps of
89.2 s, the passes 67.4 deg apart): 15 "laps" of 43.47 and 45.71 s, alternating loop by loop.

Pinned on a REAL `pacer.Laps` segmentation through the loader's own unknown-track branch
(`load._place_unknown_start_line`):
- at a crossover, the laps are whole and last exactly the drive's lap time;
- where nothing else cuts the held-peak line, the line stays where X1 put it.

Imports pacer (no Qt). Run:  PYTHONPATH=bindings/pacer python tests/test_auto_line_crossover.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402
from _synthetic import figure8_trace  # noqa: E402

import pacer  # noqa: E402
from studio import load  # noqa: E402
from studio._signal import _band_lap_ids  # noqa: E402

_CLAT, _CLON = 47.0, -32.0          # the open Atlantic, like the synthetic GoPro: no circuit here
_M_PER_DEG_LAT = 111_320.0


def _placed(trace):
    """Load `trace` the way `load.load_recording` hands a trace to the unknown-track branch, and run
    that branch: GPS fixes in a `pacer.Laps`, a coordinate system centred on the trace's box, the
    fixes back in local metres. Returns (laps, index of the fix the line passes through)."""
    laps = pacer.Laps()
    coslat = math.cos(math.radians(_CLAT))
    for x, y, v, t in zip(trace.x, trace.y, trace.v, trace.t, strict=True):
        laps.add_point(pacer.GPSSample(lat=_CLAT + y / _M_PER_DEG_LAT,
                                       lon=_CLON + x / (_M_PER_DEG_LAT * coslat), altitude=0.0,
                                       full_speed=float(v), ground_speed=float(v)), float(t))
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0.0))
    laps.set_coordinate_system(cs)
    local = [cs.local(laps.get_point(i).point) for i in range(laps.point_count())]
    xs = np.array([p[0] for p in local])
    ys = np.array([p[1] for p in local])
    load._place_unknown_start_line(laps, xs, ys, np.asarray(trace.v, float))
    line = laps.sectors.start_line
    mid_x, mid_y = (line.first.x + line.second.x) / 2, (line.first.y + line.second.y) / 2
    at = int(np.argmin(np.hypot(xs - mid_x, ys - mid_y)))
    assert math.hypot(xs[at] - mid_x, ys[at] - mid_y) < 1e-3, "the heuristic's line runs through a fix"
    return laps, at


def _lap_report(laps, ids):
    return ", ".join(f"{laps.lap_time(i):.2f} s/{laps.get_lap_distance(i):.0f} m" for i in ids)


def _metres_past(tr, at, s_ref):
    """How far the fix `at` is past arc length `s_ref` within the lap (negative: before it)."""
    return (tr.s[at] - s_ref + tr.lap_m / 2) % tr.lap_m - tr.lap_m / 2


def test_a_crossover_does_not_cut_every_lap_in_two():
    """The held peak is on the bridge, where the slow pass cuts its line on every lap. The line
    goes to the next-fastest place instead, which is on the same fast pass near the bridge and out
    of the slow pass's reach. There the 8 laps give 7 whole ones between their 8 crossings, each
    exactly the drive's lap time and length."""
    tr = figure8_trace()                          # b/a = 1.5: the passes' headings 67.4 deg apart
    laps, at = _placed(tr)
    valid = _band_lap_ids(laps)
    assert len(valid) == 7, (
        f"{len(valid)} laps counted where 8 were driven (7 whole between 8 crossings): "
        f"{_lap_report(laps, valid)}. The other pass cuts the line, and each piece is one loop of the 8")
    for i in valid:
        assert abs(laps.lap_time(i) - tr.lap_s) < 0.02, (i, laps.lap_time(i), tr.lap_s)
        assert abs(laps.get_lap_distance(i) - tr.lap_m) < 0.01 * tr.lap_m, (i, laps.get_lap_distance(i))
    # Still the held-peak rule's kind of place (the fast pass near the bridge), not a fallback.
    past = _metres_past(tr, at, tr.node_s)
    assert abs(past) < 60.0 and tr.v[at] >= 0.95 * tr.v.max(), (past, tr.v[at], tr.v.max())
    print(f"ok crossover: the line is {past:+.1f} m from the bridge on the fast pass; "
          f"{len(valid)} laps of {tr.lap_s:.3f} s")


def test_the_held_peak_stays_where_nothing_else_cuts_it():
    """With the fastest place 60 m before the bridge, no other stretch reaches the held-peak line,
    so it stays where X1 put it. That is within the window the speed is held over (5 fixes each side,
    about 12 m here) before the fastest place, on the same fix as before this check existed."""
    tr = figure8_trace(peak_after_m=-60.0)
    laps, at = _placed(tr)
    before_peak = -_metres_past(tr, at, tr.peak_s)
    assert 0.0 <= before_peak < 15.0 and tr.v[at] >= 0.99 * tr.v.max(), (before_peak, tr.v[at])
    valid = _band_lap_ids(laps)
    assert len(valid) == 7 and all(abs(laps.lap_time(i) - tr.lap_s) < 0.02 for i in valid), (
        _lap_report(laps, valid))
    print(f"ok nothing else in reach: the line is {before_peak:.1f} m before the fastest place")


if __name__ == "__main__":
    test_a_crossover_does_not_cut_every_lap_in_two()
    test_the_held_peak_stays_where_nothing_else_cuts_it()
    print("\n2 auto-line crossover tests passed")
