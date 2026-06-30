"""Unit tests for studio.timeline.Timeline (E2): the cursor/plot/video-sync coordinate
conversions extracted off the Session facade. Timeline is Qt-FREE and pacer-FREE — it runs on
injected callables over a synthetic 2-lap layout, so these pin the conversions in isolation
(the Session delegators that forward to them are exercised end-to-end elsewhere: test_scrub_
conversion, test_controllers, test_studio_features, and the whole-API golden).

Run:  python tests/test_timeline.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio.timeline import Timeline  # noqa: E402


def _timeline():
    """A Timeline over two contiguous laps on a shared media clock:
      lap 0: media [100, 110], odometer [0, 200]
      lap 1: media [110, 120], odometer [0, 220]  (contiguous: 110 is lap 1's START)
    The full trace is the two laps concatenated; xy is a simple rising line so argmin is obvious."""
    t0 = np.linspace(100.0, 110.0, 11)
    d0 = np.linspace(0.0, 200.0, 11)
    t1 = np.linspace(110.0, 120.0, 11)
    d1 = np.linspace(0.0, 220.0, 11)
    td = {0: (t0, d0), 1: (t1, d1)}
    tt = np.concatenate([t0, t1])
    tx = np.concatenate([np.linspace(0.0, 100.0, 11), np.linspace(100.0, 200.0, 11)])
    ty = np.zeros(22)
    xyt = {0: (np.linspace(0.0, 100.0, 11), np.zeros(11), t0),
           1: (np.linspace(100.0, 200.0, 11), np.zeros(11), t1)}
    windows = {0: (100.0, 110.0), 1: (110.0, 120.0)}
    return Timeline(
        lap_time_dist=lambda lid: td.get(lid),
        lap_trace_xyt=lambda lid: xyt[lid],
        valid_lap_ids=lambda: [0, 1],
        lap_window=lambda lid: windows[lid],
        trace_times=lambda: tt,
        trace_xs=lambda: tx,
        trace_ys=lambda: ty,
    )


def test_lap_at_time_half_open_windows():
    """lap_at_time resolves the [start, start+lap_time) window; the upper bound is HALF-OPEN so a
    `t` exactly on lap 1's start (== lap 0's end) is lap 1, not lap 0 (the seek-to-start case)."""
    tl = _timeline()
    assert tl.lap_at_time(105.0) == 0
    assert tl.lap_at_time(110.0) == 1, "the contiguous boundary belongs to the LATER lap"
    assert tl.lap_at_time(119.9) == 1
    assert tl.lap_at_time(120.0) is None, "the last lap's exact finish is a between-laps None"
    assert tl.lap_at_time(99.0) is None, "before the first lap"
    print("test_lap_at_time_half_open_windows OK")


def test_invalidate_rebuilds_window_table():
    """invalidate() drops the cached window table so a later valid-set change is reflected."""
    tl = _timeline()
    assert tl.lap_at_time(105.0) == 0
    # Shrink the valid set behind the cache: without invalidate the stale table still answers.
    tl._valid_lap_ids = lambda: [1]
    assert tl.lap_at_time(105.0) == 0, "stale cached table still resolves lap 0"
    tl.invalidate()
    assert tl.lap_at_time(105.0) is None, "after invalidate, lap 0 is gone from the table"
    assert tl.lap_at_time(115.0) == 1
    print("test_invalidate_rebuilds_window_table OK")


def test_index_at_time_clamps():
    """index_at_time is a clamped searchsorted into the full media-time trace (22 samples)."""
    tl = _timeline()
    assert tl.index_at_time(50.0) == 0           # before the trace -> first
    assert tl.index_at_time(1e6) == 21           # after -> last index
    i = tl.index_at_time(110.0)
    assert 0 <= i <= 21 and abs(tl._trace_times()[i] - 110.0) < 1.0
    print("test_index_at_time_clamps OK")


def test_plot_x_media_time_roundtrip_both_modes():
    """media_time_at_plot_x and plot_x_at_media_time invert each other inside a lap, in TIME mode
    (x = t - lap_start) and the shared-DISTANCE mode (x = s * best_distance)."""
    tl = _timeline()
    t = 104.0  # inside lap 0 (media [100, 110])
    # TIME mode: x is seconds-into-lap; round-trips exactly.
    x_t = tl.plot_x_at_media_time(0, t, "time")
    assert abs(x_t - 4.0) < 1e-9, x_t
    assert abs(tl.media_time_at_plot_x(0, x_t, "time") - t) < 1e-9
    # DISTANCE mode: x = s * best_distance; round-trips through the lap odometer.
    best = 500.0
    x_d = tl.plot_x_at_media_time(0, t, "distance", best_distance=best)
    assert abs(tl.media_time_at_plot_x(0, x_d, "distance", best_distance=best) - t) < 1e-9
    # Distance mode with no best_distance -> None (caller no-ops); a degenerate lap -> None.
    assert tl.plot_x_at_media_time(0, t, "distance") is None
    assert tl.media_time_at_plot_x(99, 0.0, "time") is None  # unknown lap is degenerate
    print("test_plot_x_media_time_roundtrip_both_modes OK")


def test_media_time_at_plot_x_clamps_to_lap():
    """The result is CLAMPED to the lap's [start, end] media window so a drag can't leave it."""
    tl = _timeline()
    assert tl.media_time_at_plot_x(0, 1e6, "time") == 110.0   # past the end -> lap end
    assert tl.media_time_at_plot_x(0, -1e6, "time") == 100.0  # before -> lap start
    print("test_media_time_at_plot_x_clamps_to_lap OK")


def test_nearest_index_whole_trace_vs_lap_scoped():
    """nearest_index searches the WHOLE trace; nearest_index_in_lap stays inside the given lap
    (so a draggable marker can't snap across spatially-overlapping laps); nearest_time clamps."""
    tl = _timeline()
    # Whole-trace nearest to x=200 is the very last trace point.
    assert tl.nearest_index(200.0, 0.0) == 21
    # Lap-scoped: within lap 0 (x in [0,100]) the nearest to x=1000 is lap 0's last point (idx 10).
    assert tl.nearest_index_in_lap(0, 1000.0, 0.0) == 10
    assert tl.nearest_index_in_lap(0, -1000.0, 0.0) == 0
    # nearest_time_in_lap clamps to the lap's media window [100, 110].
    assert abs(tl.nearest_time_in_lap(0, 1e6, 0.0) - 110.0) < 1e-9
    assert abs(tl.nearest_time_in_lap(0, -1e6, 0.0) - 100.0) < 1e-9
    print("test_nearest_index_whole_trace_vs_lap_scoped OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} TIMELINE TESTS PASSED")
