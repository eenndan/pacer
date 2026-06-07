"""Tests for the GPS9 true-clock time axis (studio.session._gps9_times).

The lap timer compares two start/finish-line crossing instants. The C++ core already
INTERPOLATES each crossing time along the chord (pacer::Split), so the accuracy of a lap time
is set by the per-sample TIME AXIS. The old `naive` axis spread each payload's media span over
i/n samples; the GoPro media clock for the GPS track runs ~0.1% fast, which systematically
compressed every lap. `_gps9_times` instead uses the GPS9 fix timestamps' true 10 Hz spacing,
re-anchored per contiguous run to the media (naive) clock — so the axis stays on the media clock
the video layer maps against, but inter-sample spacing is the real wall-clock spacing.

These tests run on synthetic samples (no telemetry file). Run: python tests/test_lap_timing.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pacer  # noqa: E402
from studio.session import _gps9_times  # noqa: E402


def _sample(ts_ms):
    return pacer.GPSSample(lat=0.0, lon=0.0, altitude=0.0, full_speed=20.0,
                           ground_speed=20.0, timestamp_ms=int(ts_ms))


def test_gps9_uses_true_spacing_reanchored_to_media():
    """The result must start at the media (naive) anchor but advance by the TRUE GPS9 spacing.
    The naive axis carries per-payload phase wobble (it spreads each media span over i/n); the
    GPS9 axis replaces that with the clean wall-clock spacing, which is what makes the two
    interpolated crossing instants — and hence the lap time — accurate vs the transponder."""
    n = 100
    # naive: a wobbly media axis (a slow drift + per-sample phase noise). gps9: clean 100 ms.
    rng = np.random.default_rng(0)
    naive = list(1000.0 + np.cumsum(0.1001 + rng.normal(0, 0.003, n)))
    samples = [_sample(500_000 + i * 100) for i in range(n)]  # exact 10.000 Hz wall clock
    spans = [(0.0, 0.0)] * n  # spans unused by _gps9_times

    # rate_factor=1.0 isolates the SPACING behaviour from the transponder clock-rate calibration
    # (the default factor, tested in tests/test_calibration.py, scales the spacing by ~0.9994).
    out = np.asarray(_gps9_times(samples, spans, naive, rate_factor=1.0))
    assert len(out) == n
    # Anchored at the first naive time (so the axis stays on the media clock for video sync).
    assert abs(out[0] - naive[0]) < 1e-9
    # Spacing is the clean GPS9 100 ms, NOT the wobbly naive spacing.
    d = np.diff(out)
    assert np.allclose(d, 0.100, atol=1e-6), d[:5]
    # And it's strictly monotone (no phase wobble that could misplace a crossing).
    assert np.all(d > 0)
    print("test_gps9_uses_true_spacing_reanchored_to_media OK")


def test_gps9_falls_back_to_naive_without_timestamps():
    """GPS5-only / sentinel samples (timestamp_ms == 0) keep their naive time exactly."""
    n = 50
    naive = [10.0 + i * 0.1 for i in range(n)]
    samples = [_sample(0) for _ in range(n)]  # no GPS9 timestamp
    out = _gps9_times(samples, [(0.0, 0.0)] * n, naive)
    assert np.allclose(out, naive, atol=1e-12)
    print("test_gps9_falls_back_to_naive_without_timestamps OK")


def test_gps9_reanchors_after_a_run_break():
    """A chapter break / long dropout (a GPS9 delta outside the sane single-step band) starts a
    new run anchored at ITS naive time — the GPS9 absolute epoch (which jumps at chapter/midnight
    boundaries) is never trusted, only its within-run spacing. The axis stays monotonic."""
    # Run A: 20 samples at 100 ms. Then a 5 s media gap. Run B: 20 samples at 100 ms, whose GPS9
    # epoch has JUMPED backwards (e.g. a new chapter's wall clock) — must not corrupt the axis.
    naiveA = [100.0 + i * 0.1 for i in range(20)]
    naiveB = [100.0 + 1.9 + 5.0 + i * 0.1 for i in range(20)]  # after a 5 s gap
    naive = naiveA + naiveB
    tsA = [800_000 + i * 100 for i in range(20)]
    tsB = [10_000 + i * 100 for i in range(20)]  # epoch jumped BACK by ~790 s
    samples = [_sample(t) for t in tsA + tsB]
    # rate_factor=1.0 to assert the raw 100 ms spacing (the calibration is tested separately).
    out = np.asarray(_gps9_times(samples, [(0.0, 0.0)] * 40, naive, rate_factor=1.0))
    # Monotonic non-decreasing across the seam (no backwards jump from the epoch reset).
    assert np.all(np.diff(out) >= -1e-9), np.diff(out).min()
    # Run B re-anchored near its own naive start (not dragged back to run A's epoch).
    assert abs(out[20] - naive[20]) < 0.2, (out[20], naive[20])
    # Within each run the spacing is the true 100 ms.
    assert np.allclose(np.diff(out[:20]), 0.1, atol=1e-6)
    assert np.allclose(np.diff(out[20:]), 0.1, atol=1e-6)
    print("test_gps9_reanchors_after_a_run_break OK")


def test_crossing_instant_is_interpolated_not_nearest():
    """Sanity on the core's crossing-instant interpolation that the lap timer depends on: a
    line crossed between two timed points yields a crossing TIME strictly between them, scaled
    by the geometric fraction — NOT snapped to the nearest sample. (This is what makes lap times
    sub-sample accurate; the time-axis fix above improves the per-sample times it interpolates.)"""
    origin = pacer.GPSSample(lat=40.0, lon=-74.0, altitude=0.0)
    cs = pacer.CoordinateSystem(origin)
    laps = pacer.Laps()
    # Straight run crossing local x == 0 between x=-3 (t=0.0) and x=+1 (t=1.0): fraction 3/4.
    for x, t in [(-7.0, -1.0), (-3.0, 0.0), (1.0, 1.0), (5.0, 2.0)]:
        g = cs.global_(pacer.Vec3f(x, 0.0, 0.0))
        laps.add_point(g, float(t))
    laps.set_coordinate_system(cs)
    a, b = pacer.Point(), pacer.Point()
    a.x, a.y, b.x, b.y = 0.0, -10.0, 0.0, 10.0
    seg = pacer.Segment()
    seg.first, seg.second = a, b
    laps.sectors = pacer.Sectors(start_line=seg, sector_lines=[])
    laps.update()
    assert laps.laps_count() >= 1
    lap = laps.get_lap(0)
    tstart = lap.points[0].time
    # crossing between t=0 and t=1 at fraction 3/4 -> t == 0.75 (interpolated, not 0 or 1).
    assert abs(tstart - 0.75) < 1e-6, tstart
    print("test_crossing_instant_is_interpolated_not_nearest OK")


if __name__ == "__main__":
    test_gps9_uses_true_spacing_reanchored_to_media()
    test_gps9_falls_back_to_naive_without_timestamps()
    test_gps9_reanchors_after_a_run_break()
    test_crossing_instant_is_interpolated_not_nearest()
    print("\nALL LAP-TIMING TESTS PASSED")
