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


# ─── a wider line that reaches a SECOND stretch of track ────────────────────────────────────────
# A hairpin circuit in local metres (x east, y north): the start straight runs east along y = 0,
# and the return straight runs west along y = 21 — so a line across the start straight at x = 0
# meets the return straight too once it is longer than 42 m. That is the geometry of the owner's
# SD_30_08 recording (T13): the loader's 30 m line cut 23 real 47.6 s laps, its ×1.5 widening (45 m)
# reached the second stretch and cut each lap into a 13.3 s and a 34 s piece, and because the short
# pieces outnumbered the laps by two, "more band laps" took it.
_HAIRPIN_GAP = 21.0      # m between the two straights — reached by ×1.5 (±22.5 m), not by ×1.3
_EAST_RUN = 250.0        # m of straight east of the line: the long piece is 2 × 250 + π·10.5 ≈ 533 m
_WEST_RUN = 50.0         # m of straight west of it: the short piece is 2 × 50 + π·10.5 ≈ 133 m
_SPEED = 20.0            # m/s, sampled at 10 Hz — 2 m per sample


def _hairpin_xy(s):
    """The point `s` metres round the hairpin loop, from (0, 0) eastbound. Two straights joined by
    two semicircles of radius _HAIRPIN_GAP / 2; the loop crosses x = 0 exactly twice per lap, at
    y = 0 (s ≡ 0) and at y = _HAIRPIN_GAP (s ≡ the long piece's length)."""
    r = _HAIRPIN_GAP / 2
    arc = math.pi * r
    legs = ((_EAST_RUN, "e"), (arc, "turn_e"), (_EAST_RUN + _WEST_RUN, "w"), (arc, "turn_w"),
            (_WEST_RUN, "back"))
    lap = sum(n for n, _ in legs)
    s %= lap
    leg = legs[-1][1]
    for n, name in legs:
        if s <= n:
            leg = name
            break
        s -= n
    if leg == "e":
        return s, 0.0
    if leg == "turn_e":
        a = s / r
        return _EAST_RUN + r * math.sin(a), r - r * math.cos(a)
    if leg == "w":
        return _EAST_RUN - s, _HAIRPIN_GAP
    if leg == "turn_w":
        a = s / r
        return -_WEST_RUN - r * math.sin(a), r + r * math.cos(a)
    return -_WEST_RUN + s, 0.0


def _local_gps(x, y):
    lat = _CLAT + y / _M_PER_DEG_LAT
    lon = _CLON + x / (_M_PER_DEG_LAT * math.cos(math.radians(_CLAT)))
    return pacer.GPSSample(lat=lat, lon=lon, altitude=0.0, full_speed=_SPEED, ground_speed=_SPEED)


def _hairpin_laps(s_from, s_to):
    laps = pacer.Laps()
    n = int((s_to - s_from) / (_SPEED * 0.1))
    for i in range(n + 1):
        laps.add_point(_local_gps(*_hairpin_xy(s_from + i * _SPEED * 0.1)), i * 0.1)
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0.0))
    laps.set_coordinate_system(cs)
    return laps, cs


def _band_laps(laps):
    from studio._signal import _band_lap_ids
    ids = _band_lap_ids(laps)
    return ids, [float(laps.get_lap_distance(i)) for i in ids], sum(laps.lap_time(i) for i in ids)


def test_fit_start_line_refuses_a_wider_line_that_cuts_every_lap_into_pieces():
    """T13. A wider line that recovers a pass the short one stepped over turns one excluded
    double-length piece into two counted laps: more laps, and more of the driving counted. A wider
    line that reaches a SECOND stretch of track cuts every lap in two, and the band — which is
    centred on the median piece — follows whichever piece is more numerous. That is more "laps"
    too, so a rule that asks only for more band laps takes it, and every lap the app then shows is
    a piece of one.

    The trace starts 80 m before the return straight's crossing and ends 80 m past the start line,
    so neither end is a substantial piece and the short pieces outnumber the long ones 6 to 5 —
    the arrangement SD_30_08 happened to have (25 short pieces, 24 long, 23 laps).

    Since L3 the band itself refuses these pieces (none of them ends where it started), which
    would leave this rule untested: ×1.5 would count nothing and lose on count alone. So the rule
    is pinned with the closure test switched OFF, as the one line of defence it was — and the
    closure test is then checked to be a second one, on its own."""
    long_piece = 2 * _EAST_RUN + math.pi * _HAIRPIN_GAP / 2
    lap_m = long_piece + 2 * _WEST_RUN + math.pi * _HAIRPIN_GAP / 2
    laps, cs = _hairpin_laps(long_piece - 80.0, 6 * lap_m + 80.0)
    a, b = cs.local(_local_gps(0.0, -15.0)), cs.local(_local_gps(0.0, 15.0))
    base = tracks.make_segment(a[0], a[1], b[0], b[1])

    from studio import _signal
    closure_test = _signal._is_open_lap
    _signal._is_open_lap = lambda cols: False
    try:
        # Non-vacuous: on the base line the laps are laps, and the ×1.5 line really does win on count.
        laps.sectors = pacer.Sectors(start_line=base, sector_lines=[])
        laps.update()
        base_ids, base_d, base_s = _band_laps(laps)
        laps.sectors = pacer.Sectors(start_line=load._widen(base, 1.5), sector_lines=[])
        laps.update()
        wide_ids, wide_d, wide_s = _band_laps(laps)
        assert len(base_ids) == 5 and all(abs(d - lap_m) < 5 for d in base_d), (base_ids, base_d)
        assert len(wide_ids) > len(base_ids), (
            f"the fixture no longer reproduces the defect: ×1.5 counts {len(wide_ids)} pieces "
            f"against {len(base_ids)} laps")
        assert max(wide_d) < 0.5 * lap_m, f"×1.5 should count pieces of a lap, got {wide_d}"

        result = load._fit_start_line(laps, base)
        ids, dists, secs = _band_laps(laps)
    finally:
        _signal._is_open_lap = closure_test

    # The second line of defence, live: on the ×1.5 line not one of those pieces is counted.
    laps.sectors = pacer.Sectors(start_line=load._widen(base, 1.5), sector_lines=[])
    laps.update()
    assert _band_laps(laps)[0] == [], "the closure test should refuse every ×1.5 piece on its own"
    laps.sectors = pacer.Sectors(start_line=result, sector_lines=[])
    laps.update()
    assert _ends(result) == _ends(base), (
        f"_fit_start_line took a widened line that cuts every {lap_m:.0f} m lap into pieces: it now "
        f"counts {len(ids)} 'laps' of {sorted(round(d) for d in dists)} m ({secs:.1f} s of driving) "
        f"where the base line counted {len(base_ids)} laps of {lap_m:.0f} m ({base_s:.1f} s)")
    assert len(ids) == 5 and all(abs(d - lap_m) < 5 for d in dists), (ids, dists)
    print(f"ok pieces refused: the base line counts {len(base_ids)} laps of {lap_m:.0f} m "
          f"({base_s:.1f} s); ×1.5 would count {len(wide_ids)} pieces of ~{wide_d[0]:.0f} m "
          f"({wide_s:.1f} s), and the base line is kept")


def test_fit_start_line_still_widens_when_the_base_line_counted_laps_and_missed_one_pass():
    """The other half of T13's rule. The widen test above starts from a line that counts NOTHING,
    where any driving beats zero; this one starts from a line that counts laps and steps over one
    pass. The 30 m line sits off-centre, reaching only 5 m outside the racing line, and the kart
    runs 8 m wide through it on its fourth lap, so the line misses that crossing and the two laps
    either side become one excluded piece twice as long. The ×1.3 line (reaching 9.5 m out)
    catches it: two more laps, and their driving is counted, so the widening has to be taken
    exactly as before.

    Off-centre since L3, and not for convenience: the original pass ran 18 m wide of a CENTRED
    line, and a lap whose two crossings are 18 m apart is one the closure test calls open
    (`_signal.MAX_LAP_GAP_M`) — on any line, widened or not. No counted real lap on the owner's
    footage crosses more than 8.45 m from where it started; a line that misses the kart by less
    than that is an off-centre line, which is the case widening exists for."""
    wide_lap = 3

    def gps(i):
        theta = 2.0 * math.pi * (i / _PER_LAP)
        lap_pos = theta / (2.0 * math.pi)
        # A smooth radial excursion centred on the start line's angle during `wide_lap` only.
        centre = wide_lap + _THETA / (2.0 * math.pi)
        bump = math.exp(-((lap_pos - centre) / 0.03) ** 2)
        return _gps(theta, _RADIUS + 8.0 * bump)

    laps = pacer.Laps()
    for i in range(7 * _PER_LAP + 1):
        laps.add_point(gps(i), i * 0.1)
    mn, mx = laps.min_max()
    cs = pacer.CoordinateSystem(
        pacer.GPSSample(lat=(mn.y + mx.y) / 2, lon=(mn.x + mx.x) / 2, altitude=0.0))
    laps.set_coordinate_system(cs)
    base = _radial(cs, _RADIUS - 25.0, _RADIUS + 5.0)
    laps.sectors = pacer.Sectors(start_line=base, sector_lines=[])
    laps.update()
    base_ids, _d, base_s = _band_laps(laps)

    result = load._fit_start_line(laps, base)
    ids, dists, secs = _band_laps(laps)
    assert len(base_ids) >= 3, f"the base line should still count the other laps: {base_ids}"
    assert len(ids) > len(base_ids) and secs > base_s, (
        f"the missed pass was not recovered: {len(base_ids)} laps ({base_s:.1f} s) -> {len(ids)} "
        f"({secs:.1f} s)")
    assert _ends(result) == _ends(load._widen(base, 1.3)), "expected the smallest widening that catches it"
    print(f"ok recovery kept: the base line counts {len(base_ids)} laps ({base_s:.1f} s), the ×1.3 "
          f"line {len(ids)} ({secs:.1f} s)")


if __name__ == "__main__":
    test_fit_start_line_keeps_a_sufficient_line_unwidened()
    test_fit_start_line_widens_to_recover_missed_laps()
    test_fit_start_line_refuses_a_wider_line_that_cuts_every_lap_into_pieces()
    test_fit_start_line_still_widens_when_the_base_line_counted_laps_and_missed_one_pass()
    print("\n4 fit-start-line tests passed")
