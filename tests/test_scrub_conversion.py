"""Round-trip tests for the cursor-scrub x<->media-time conversions in studio.session.

The draggable plot cursor seeks the video within the current lap; plots_view stays pacer-free,
so the per-axis-mode x<->time mapping lives in Session. These tests exercise that math DIRECTLY
on synthetic cached per-lap arrays (no pacer, no telemetry file, fast): they build a bare
Session via __new__, populate its _dist_cache, and check that
    media_time_at_plot_x(plot_x_at_media_time(t)) == t
for both axis modes ('time', and the shared-distance 'distance'/'delta'), that clamping holds at
the lap edges, and — crucially for the cursor-sync fix — that BOTH plots map a given media time
to the SAME plot-x (the two cursors coincide). The speed + delta plots share ONE x-axis: in
distance mode both use x = s × best_distance, so 'distance' and 'delta' are the same convention
(delta is a readable alias). Run: python tests/test_scrub_conversion.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from studio.session import Session  # noqa: E402


def make_session(lap_id=3, t0=100.0, dt=0.1, n=120, total_dist=520.0):
    """A bare Session carrying ONE lap's cached (times, dists). times start at t0; dists is a
    deliberately NON-uniform monotonic odometer (so distance<->time is a real, non-linear interp
    — a constant-speed lap would make distance mode trivially equal to a scaled time mode)."""
    s = Session.__new__(Session)
    s._dist_cache = {}
    times = t0 + np.arange(n) * dt
    # Non-uniform speed profile: slow-fast-slow, integrated to a monotonic odometer ending at
    # total_dist. (sin^2 keeps every step positive -> strictly increasing cum-distance.)
    speed = 1.0 + np.sin(np.linspace(0, np.pi, n)) ** 2
    cum = np.cumsum(speed)
    dists = (cum - cum[0]) / (cum[-1] - cum[0]) * total_dist
    s._dist_cache[lap_id] = (times, dists)
    return s, lap_id, times, dists


def test_time_mode_roundtrip():
    s, lid, times, dists = make_session()
    for t in np.linspace(times[0], times[-1], 37):
        x = s.plot_x_at_media_time(lid, t, "time")
        t2 = s.media_time_at_plot_x(lid, x, "time")
        assert abs(t2 - t) < 1e-9, (t, x, t2)
    # x is exactly time-into-lap.
    assert abs(s.plot_x_at_media_time(lid, times[0], "time") - 0.0) < 1e-12
    assert abs(s.plot_x_at_media_time(lid, times[-1], "time")
               - (times[-1] - times[0])) < 1e-9
    print("test_time_mode_roundtrip OK")


def test_distance_mode_roundtrip():
    s, lid, times, dists = make_session(total_dist=520.0)
    best_d = 600.0  # the shared distance axis is x = s * best_distance (NOT raw odometer)
    for t in np.linspace(times[0], times[-1], 37):
        x = s.plot_x_at_media_time(lid, t, "distance", best_distance=best_d)
        t2 = s.media_time_at_plot_x(lid, x, "distance", best_distance=best_d)
        # interp on a fine grid -> tolerance a small fraction of a sample step.
        assert abs(t2 - t) < 1e-6, (t, x, t2)
    # x is the normalized fraction * best_distance: at the finish (s=1) it equals best_distance.
    assert abs(s.plot_x_at_media_time(lid, times[-1], "distance", best_distance=best_d)
               - best_d) < 1e-9
    print("test_distance_mode_roundtrip OK")


def test_delta_mode_roundtrip():
    s, lid, times, dists = make_session(total_dist=520.0)
    best_d = 600.0  # best lap is a DIFFERENT length, so x = s*best_d != distance-into-this-lap
    for t in np.linspace(times[0], times[-1], 37):
        x = s.plot_x_at_media_time(lid, t, "delta", best_distance=best_d)
        t2 = s.media_time_at_plot_x(lid, x, "delta", best_distance=best_d)
        assert abs(t2 - t) < 1e-6, (t, x, t2)
    # At the finish (t1) the normalized fraction is 1 -> x == best_distance.
    assert abs(s.plot_x_at_media_time(lid, times[-1], "delta", best_distance=best_d)
               - best_d) < 1e-9
    print("test_delta_mode_roundtrip OK")


def test_all_modes_agree_on_one_time():
    """Both plots are two renderings of ONE truth (the media time). A given media time, mapped
    to each plot's x and back, must recover that same media time in every mode."""
    s, lid, times, dists = make_session()
    best_d = 600.0
    t = float(times[len(times) // 3])
    for mode in ("time", "distance", "delta"):
        x = s.plot_x_at_media_time(lid, t, mode, best_distance=best_d)
        t2 = s.media_time_at_plot_x(lid, x, mode, best_distance=best_d)
        assert abs(t2 - t) < 1e-6, (mode, t, x, t2)
    print("test_all_modes_agree_on_one_time OK")


def test_speed_and_delta_cursors_coincide():
    """THE cursor-sync fix: the speed plot (distance mode) and the delta plot now share ONE
    x-axis (x = s × best_distance), so the same media moment maps to the SAME plot-x on both —
    the two cursors coincide vertically. Asserted across the lap, for a NON-best-length lap
    (where the old raw-distance-vs-s×best_distance split made them diverge)."""
    s, lid, times, dists = make_session(total_dist=520.0)
    best_d = 600.0  # best lap is a different length than this lap (520 m) — the desync trigger
    for t in np.linspace(times[0], times[-1], 37):
        x_speed = s.plot_x_at_media_time(lid, t, "distance", best_distance=best_d)
        x_delta = s.plot_x_at_media_time(lid, t, "delta", best_distance=best_d)
        assert abs(x_speed - x_delta) < 1e-12, (t, x_speed, x_delta)
    print("test_speed_and_delta_cursors_coincide OK")


def test_clamp_to_lap_window():
    """Dragging past the lap boundary clamps to the lap's start/end media time."""
    s, lid, times, dists = make_session()
    best_d = 600.0
    t0, t1 = float(times[0]), float(times[-1])
    # time mode: x far past the lap end / before the start.
    span = t1 - t0
    assert abs(s.media_time_at_plot_x(lid, span + 10.0, "time") - t1) < 1e-9
    assert abs(s.media_time_at_plot_x(lid, -5.0, "time") - t0) < 1e-9
    # shared-distance mode: x = s*best_distance beyond best_distance (s>1) / negative (s<0).
    assert abs(s.media_time_at_plot_x(lid, best_d + 100.0, "distance", best_distance=best_d)
               - t1) < 1e-9
    assert abs(s.media_time_at_plot_x(lid, -50.0, "distance", best_distance=best_d) - t0) < 1e-9
    # delta mode (same axis): fraction >1 (x > best_distance) / <0.
    assert abs(s.media_time_at_plot_x(lid, best_d * 2, "delta", best_distance=best_d) - t1) < 1e-9
    assert abs(s.media_time_at_plot_x(lid, -10.0, "delta", best_distance=best_d) - t0) < 1e-9
    print("test_clamp_to_lap_window OK")


def test_distance_delta_no_best_distance_returns_none():
    """The shared-distance modes ('distance'/'delta') can't normalize without the best lap's
    total distance -> None (the app no-ops rather than crashing). The <2-point degenerate-lap
    None path is guarded in _lap_time_dist at cache-build time and exercised by the smoke."""
    s, lid, times, dists = make_session()
    for mode in ("distance", "delta"):
        assert s.media_time_at_plot_x(lid, 1.0, mode) is None
        assert s.media_time_at_plot_x(lid, 1.0, mode, best_distance=0.0) is None
        assert s.plot_x_at_media_time(lid, float(times[0]), mode) is None
    print("test_distance_delta_no_best_distance_returns_none OK")


if __name__ == "__main__":
    test_time_mode_roundtrip()
    test_distance_mode_roundtrip()
    test_delta_mode_roundtrip()
    test_all_modes_agree_on_one_time()
    test_speed_and_delta_cursors_coincide()
    test_clamp_to_lap_window()
    test_distance_delta_no_best_distance_returns_none()
    print("\nALL SCRUB CONVERSION TESTS PASSED")
