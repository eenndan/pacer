"""load._fit_start_line widen-decision (test/fit-start-line-widen).

On a KNOWN track, load._fit_start_line chooses the start/finish line: it takes the registry line,
but if a wider line (scaled about the midpoint by 1.15 / 1.3 / 1.5) recovers band-laps a too-short
segment missed, it takes the smallest such factor. This directly sets how many laps segment and
where each boundary lands (best / baseline / coaching / map all follow), yet it ran in CI only via
the manual D24 dump — the synthetic golden gate never calls it. Pinned here on a synthetic circle
`pacer.Laps`, asserting BOTH branches: a sufficient line is kept un-widened, and an outside-the-trace
line is widened until it recovers the laps (with a non-vacuous guard that the widen path really
fires). Imports pacer (no Qt). Run:  python tests/test_fit_start_line.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pacer  # noqa: E402
from studio import load, tracks  # noqa: E402

_CLAT, _CLON = 52.0, -0.78
_M_PER_DEG_LAT = 111_320.0
_RADIUS = 100.0          # m
_PER_LAP = 314           # samples per loop (~one per metre)
_THETA = 2.0 * math.pi * (10.5 / _PER_LAP)   # a fixed start angle offset from a sample


def _gps(theta, radius=_RADIUS):
    lat = _CLAT + (radius * math.cos(theta)) / _M_PER_DEG_LAT
    lon = _CLON + (radius * math.sin(theta)) / (_M_PER_DEG_LAT * math.cos(math.radians(_CLAT)))
    return pacer.GPSSample(lat=lat, lon=lon, altitude=0.0, full_speed=20.0, ground_speed=20.0)


def _circle_laps(n_laps=4):
    """A real n-lap circular pacer.Laps (moving at 20 m/s) + its centred CoordinateSystem."""
    laps = pacer.Laps()
    for i in range(n_laps * _PER_LAP + 1):
        laps.add_point(_gps(2.0 * math.pi * (i / _PER_LAP)), i * 0.1)
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0.0))
    laps.set_coordinate_system(cs)
    return laps, cs


def _radial(cs, r_near, r_far):
    """A start-line Segment along the radius at _THETA, spanning [r_near, r_far] metres (local)."""
    a = cs.local(_gps(_THETA, r_near))
    b = cs.local(_gps(_THETA, r_far))
    return tracks.make_segment(a[0], a[1], b[0], b[1])


def _ends(seg):
    return (seg.first.x, seg.first.y, seg.second.x, seg.second.y)


def test_fit_start_line_keeps_a_sufficient_line_unwidened():
    """A line that already spans the trace segments every lap, so no widen factor improves on it —
    _fit_start_line must return the base line unchanged (not needlessly widen a good registry line)."""
    laps, cs = _circle_laps()
    base = _radial(cs, _RADIUS - 40.0, _RADIUS + 40.0)   # straddles the trace
    result = load._fit_start_line(laps, base)
    n = load._band_lap_count(laps)
    assert n >= 3, f"a full-span line should recover the laps: {n}"
    assert _ends(result) == _ends(base), "a sufficient line must not be widened"
    print(f"ok no-widen: sufficient line kept as-is ({n} band laps)")


def test_fit_start_line_widens_to_recover_missed_laps():
    """A line placed just OUTSIDE the trace crosses nothing (0 band laps); _fit_start_line must widen
    it (about its midpoint) until it reaches the trace and recovers the laps. Guarded non-vacuously:
    the base line must recover strictly fewer laps than the fitted line, else the widen path never
    fired and the test would be meaningless."""
    laps, cs = _circle_laps()
    base = _radial(cs, _RADIUS + 5.0, _RADIUS + 45.0)    # entirely outside; midpoint R+25, half 20
    laps.sectors = pacer.Sectors(start_line=base, sector_lines=[])
    laps.update()
    base_n = load._band_lap_count(laps)

    result = load._fit_start_line(laps, base)
    widened_n = load._band_lap_count(laps)

    assert base_n < widened_n, f"fixture never exercised the widen branch: base_n={base_n} widened_n={widened_n}"
    assert widened_n >= 3, f"the widened line should recover the laps: {widened_n}"
    assert _ends(result) != _ends(base), "the fitted line must be the widened one, not the base"
    print(f"ok widen: base recovered {base_n} laps, the widened line recovered {widened_n}")


if __name__ == "__main__":
    test_fit_start_line_keeps_a_sufficient_line_unwidened()
    test_fit_start_line_widens_to_recover_missed_laps()
    print("\n2 fit-start-line tests passed")
