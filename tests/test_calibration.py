"""Tests for the transponder-calibration path:

  1. studio.transponder — the defensive lap-timing-CSV parser (the ground-truth used to
     calibrate the GPS9 clock-rate factor). Synthetic CSV text, incl. the embedded-comma /
     stray-quote later columns and the 1- vs 2-digit-seconds lap-time format.
  2. studio.session.GPS9_RATE_FACTOR — the single clock-rate correction applied to the
     within-run GPS9 spacing: a lap fully inside one contiguous run scales by exactly the
     factor, the run stays anchored at its media-clock start (video sync preserved), and the
     axis stays monotonic. Plus an end-to-end check through the real pacer core that the
     scaled axis still gives sector splits that sum to the (scaled) lap time.

Run: python tests/test_calibration.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402
from studio import transponder  # noqa: E402
from studio.session import GPS9_RATE_FACTOR, _gps9_times  # noqa: E402


# ----------------------------------------------------------------- transponder CSV parse
def test_parse_lap_time_formats():
    """M:SS.mmm with 1- or 2-digit seconds → seconds."""
    assert abs(transponder.parse_lap_time("1:08.376") - 68.376) < 1e-9
    assert abs(transponder.parse_lap_time("1:9.030") - 69.030) < 1e-9
    assert abs(transponder.parse_lap_time("1:13.564") - 73.564) < 1e-9
    assert abs(transponder.parse_lap_time("3:45.985") - 225.985) < 1e-9
    assert abs(transponder.parse_lap_time('"1:08.376"') - 68.376) < 1e-9  # stray quotes
    print("test_parse_lap_time_formats OK")


def test_parse_csv_defensive(tmp_path=None):
    """Only field[0] (Lap) and field[2] (Lap Time) are trusted; the later columns embed
    commas/quotes (e.g. `2", laps`) and must NOT corrupt the parse. The header + any non-integer
    first field is skipped."""
    csv_text = (
        "Lap,Pos,Lap Time,Diff to Last Lap,Diff to Best Lap,Gap in Front,Diff to P1,Speed\n"
        "297,23,3:45.985,2:34.911,2:38.524,1\", lap,17\", laps,19.116 km/h\n"
        "298,23,1:13.055,-,5.594,1\", lap,17\", laps,59.134 km/h\n"
        "338,22,1:08.376,-,0.915,2\", laps,14\", laps,63.180 km/h\n"
        "342,21,1:9.178,-,1.717,2\", laps,14\", laps,62.448 km/h\n"
        "junk row that should be skipped\n"
    )
    # write to a temp file (the parser opens a path)
    path = (tmp_path or _here()) + "/_t_transponder.csv"
    with open(path, "w", encoding="utf-8") as f:
        f.write(csv_text)
    try:
        laps = transponder.parse_csv(path)
    finally:
        os.remove(path)
    assert set(laps) == {297, 298, 338, 342}, laps
    assert abs(laps[297] - 225.985) < 1e-9       # embedded-comma row, parsed Lap Time only
    assert abs(laps[338] - 68.376) < 1e-9        # the stint best
    assert abs(laps[342] - 69.178) < 1e-9        # 1-digit seconds
    # stint slice (inclusive range, in order)
    st = transponder.stint_times(laps, 298, 342)
    assert [i for i, _ in st] == [298, 338, 342]
    assert min(t for _, t in st) == 68.376
    print("test_parse_csv_defensive OK")


def _here():
    return os.path.dirname(os.path.abspath(__file__))


# ----------------------------------------------------------------- GPS9 rate factor
def _sample(ts_ms):
    return pacer.GPSSample(lat=0.0, lon=0.0, altitude=0.0, full_speed=20.0,
                           ground_speed=20.0, timestamp_ms=int(ts_ms))


def test_rate_factor_scales_within_run_lap_and_keeps_anchor():
    """A lap fully inside one contiguous run scales by EXACTLY the rate factor, and the run
    stays anchored at its naive (media-clock) start — so the calibration changes lap TIME but
    not the video-sync anchor."""
    n = 700  # one long run (no run break), spanning a whole lap
    naive = list(1000.0 + np.cumsum(np.full(n, 0.1001)))  # slightly-fast media clock
    samples = [_sample(500_000 + i * 100) for i in range(n)]  # exact 10.000 Hz
    spans = [(0.0, 0.0)] * n

    out_k = np.asarray(_gps9_times(samples, spans, naive))             # calibrated default
    out_1 = np.asarray(_gps9_times(samples, spans, naive, rate_factor=1.0))  # uncorrected

    # Lap = span between two interior samples; the calibrated span is k× the uncorrected.
    lap_1 = out_1[690] - out_1[10]
    lap_k = out_k[690] - out_k[10]
    assert abs(lap_k / lap_1 - GPS9_RATE_FACTOR) < 1e-6, (lap_k, lap_1)
    # Anchor preserved (video sync): the run starts at its naive time regardless of the factor.
    assert abs(out_k[0] - naive[0]) < 1e-9
    assert abs(out_1[0] - naive[0]) < 1e-9
    # Monotone increasing.
    assert np.all(np.diff(out_k) > 0)
    print("test_rate_factor_scales_within_run_lap_and_keeps_anchor OK")


def test_rate_factor_is_a_small_sub_permille_correction():
    """Guard the magnitude: the calibrated factor is a small (<0.2%) clock-rate trim, NOT a
    large per-lap fudge. (Derived from the transponder: the gps9 axis ran ~+0.057% long.)"""
    assert 0.998 < GPS9_RATE_FACTOR < 1.0, GPS9_RATE_FACTOR
    assert abs(GPS9_RATE_FACTOR - 1.0) < 0.002
    print("test_rate_factor_is_a_small_sub_permille_correction OK")


def test_scaled_axis_sector_splits_still_sum_to_lap_time():
    """End-to-end through the real pacer core: with the rate-scaled time axis, the per-sub-sector
    split times still sum to the lap time (the calibration scales the whole axis uniformly within
    a run, so the sector/lap-time consistency invariant is preserved)."""
    # A straight east-west run crossing two timing lines; one contiguous run.
    origin = pacer.GPSSample(lat=40.0, lon=-74.0, altitude=0.0)
    cs = pacer.CoordinateSystem(origin)
    n = 200
    samples = [_sample(500_000 + i * 100) for i in range(n)]
    naive = [0.0 + i * 0.1001 for i in range(n)]
    times = _gps9_times(samples, [(0.0, 0.0)] * n, naive)

    laps = pacer.Laps()
    # Place the run along local x from -100..+100 m so it crosses x=0 (start) twice over 2 "laps".
    for i in range(n):
        x = -100.0 + (200.0 * (i % 100) / 99.0)  # sawtooth → two passes of x=0
        g = cs.global_(pacer.Vec3f(x, 0.0, 0.0))
        laps.add_point(g, float(times[i]))
    laps.set_coordinate_system(cs)
    a, b = pacer.Point(), pacer.Point()
    a.x, a.y, b.x, b.y = 0.0, -50.0, 0.0, 50.0
    start = pacer.Segment()
    start.first, start.second = a, b
    # One sector line at x=+40.
    sa, sb = pacer.Point(), pacer.Point()
    sa.x, sa.y, sb.x, sb.y = 40.0, -50.0, 40.0, 50.0
    sec = pacer.Segment()
    sec.first, sec.second = sa, sb
    laps.sectors = pacer.Sectors(start_line=start, sector_lines=[sec])
    laps.update()
    assert laps.laps_count() >= 1
    # For each lap, the sector splits (projection-based, as session does) sum to the lap time.
    for lid in range(laps.laps_count()):
        lap = laps.get_lap(lid)
        m = min(lap.count(), len(lap.cum_distances))
        if m < 2:
            continue
        cum = np.asarray(lap.cum_distances[:m], float)
        t0 = lap.points[0].time
        elapsed = np.array([lap.points[i].time - t0 for i in range(m)])
        xy = np.array([(cs.local(lap.points[i].point)[0], cs.local(lap.points[i].point)[1])
                       for i in range(m)])
        j = int(np.argmin((xy[:, 0] - 40.0) ** 2 + (xy[:, 1]) ** 2))
        edges = [0.0, float(cum[j]), float(cum[-1])]
        t_at = np.interp(edges, cum, elapsed)
        splits = [t_at[k + 1] - t_at[k] for k in range(2)]
        assert abs(sum(splits) - laps.lap_time(lid)) < 1e-6, (sum(splits), laps.lap_time(lid))
    print("test_scaled_axis_sector_splits_still_sum_to_lap_time OK")


if __name__ == "__main__":
    test_parse_lap_time_formats()
    test_parse_csv_defensive()
    test_rate_factor_scales_within_run_lap_and_keeps_anchor()
    test_rate_factor_is_a_small_sub_permille_correction()
    test_scaled_axis_sector_splits_still_sum_to_lap_time()
    print("\nALL CALIBRATION TESTS PASSED")
