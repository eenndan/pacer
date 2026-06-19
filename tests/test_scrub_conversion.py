"""Round-trip tests for the cursor-scrub x<->media-time conversions in studio.session.

The draggable plot cursor seeks the video within the current lap; plots_view stays pacer-free,
so the per-axis-mode x<->time mapping lives in Session. These tests exercise that math DIRECTLY
on synthetic cached per-lap arrays (no pacer, no telemetry file, fast): they build a bare
Session via the shared tests/_synthetic factory and check that
    media_time_at_plot_x(plot_x_at_media_time(t)) == t
for both axis modes ('time', and the shared-distance 'distance'/'delta'), that clamping holds at
the lap edges, and — crucially for the cursor-sync fix — that BOTH plots map a given media time
to the SAME plot-x (the two cursors coincide). The speed + delta plots share ONE x-axis: in
distance mode both use x = s × best_distance, so 'distance' and 'delta' are the same convention
(delta is a readable alias). Run: python tests/test_scrub_conversion.py
"""
import os
import sys
from types import SimpleNamespace

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from _synthetic import bare_session, odometer, seed_cols  # noqa: E402

from studio import cross_reference  # noqa: E402


def make_session(lap_id=3, t0=100.0, dt=0.1, n=120, total_dist=520.0):
    """A bare Session carrying ONE lap's cached (times, dists). times start at t0; dists is the
    factory's deliberately NON-uniform monotonic odometer (so distance<->time is a real,
    non-linear interp — a constant-speed lap would make distance mode trivially equal to a
    scaled time mode)."""
    times, dists = odometer(n, dt, t0, total_dist)
    s = bare_session({lap_id: (times, dists)})
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


def test_plot_x_zero_total_distance_returns_none():
    """A lap with >= 2 points but a ZERO-length odometer (stationary fixes) passes the
    <2-point cache guard, so plot_x_at_media_time's distance normalization must guard
    dists[-1] <= 0 itself (it used to divide by it -> ZeroDivisionError). None, like the
    other degenerate paths; time mode needs no division and still works."""
    lid = 5
    times = np.array([100.0, 100.1, 100.2])
    s = bare_session({lid: (times, np.zeros(3))})
    for mode in ("distance", "delta"):
        assert s.plot_x_at_media_time(lid, 100.1, mode, best_distance=600.0) is None
    assert abs(s.plot_x_at_media_time(lid, 100.1, "time") - 0.1) < 1e-9
    print("test_plot_x_zero_total_distance_returns_none OK")


# ----------------------------- D12: active-baseline single-source (cross-recording reference)
# When a cross-recording reference (F7) is loaded, the distance-mode chart x-axis is scaled by
# the REFERENCE total (delta() scales its x-grid with the baseline lap's distance). The cursor
# mappers must use the SAME total — active_baseline_total_distance() — or a dragged cursor seeks
# the wrong track position and no longer sits on its curve. These tests pin both to ONE total.

def _ref_lap(total_dist):
    """A minimal ReferenceLap whose odometer total is `total_dist` (the only field these tests
    read via active_baseline_total_distance / delta). speed/elapsed are plausible monotonic
    curves so delta()'s normalized-distance interp has real arrays to consume."""
    n = 60
    dist = np.linspace(0.0, float(total_dist), n)
    elapsed = np.linspace(0.0, 30.0, n)
    speed = np.full(n, 80.0)
    return cross_reference.ReferenceLap(
        dist=dist, speed_kmh=speed, elapsed=elapsed, total_time=float(elapsed[-1]),
        source_label="ref", lap_id=0, overlay_xy=None, map_fit_rms=None,
    )


def test_active_baseline_total_is_local_best_when_no_reference():
    """DORMANT path: with no reference, active_baseline_total_distance() == the local best lap's
    total (byte-identical to best_lap_total_distance()) — the no-behaviour-change invariant."""
    s, lid, times, dists = make_session(total_dist=520.0)
    s._best_cache = lid  # the seeded lap is the local best
    assert s._ref is None
    assert abs(s.active_baseline_total_distance() - s.best_lap_total_distance()) < 1e-12
    assert abs(s.active_baseline_total_distance() - float(dists[-1])) < 1e-9
    print("test_active_baseline_total_is_local_best_when_no_reference OK")


def test_active_baseline_total_follows_reference_when_loaded():
    """With a reference loaded, active_baseline_total_distance() returns the REFERENCE total, NOT
    the local best's — even though best_lap_total_distance() still reports the local best."""
    s, lid, times, dists = make_session(total_dist=520.0)
    s._best_cache = lid
    ref_total = 640.0  # deliberately DIFFERENT from the local best (520 m) — the desync trigger
    s._reference = _ref_lap(ref_total)
    assert abs(s.best_lap_total_distance() - 520.0) < 1e-9       # local best unchanged
    assert abs(s.active_baseline_total_distance() - ref_total) < 1e-9  # baseline follows the ref
    print("test_active_baseline_total_follows_reference_when_loaded OK")


def test_cursor_mappers_use_reference_total_and_roundtrip():
    """The cursor mappers fed active_baseline_total_distance() (the reference total) round-trip
    exactly AND map the lap finish to x == reference_total — so the cursor sits on its curve when
    the reference and local-best totals differ (the D12 bug was the mappers using the local best
    total while the x-grid used the reference total)."""
    s, lid, times, dists = make_session(total_dist=520.0)
    s._best_cache = lid
    s._reference = _ref_lap(640.0)
    base_d = s.active_baseline_total_distance()
    assert abs(base_d - 640.0) < 1e-9
    for t in np.linspace(times[0], times[-1], 37):
        x = s.plot_x_at_media_time(lid, t, "distance", best_distance=base_d)
        t2 = s.media_time_at_plot_x(lid, x, "distance", best_distance=base_d)
        assert abs(t2 - t) < 1e-6, (t, x, t2)
    # the lap finish (s=1) maps to x == the BASELINE total, i.e. the reference total
    assert abs(s.plot_x_at_media_time(lid, times[-1], "distance", best_distance=base_d)
               - 640.0) < 1e-9
    print("test_cursor_mappers_use_reference_total_and_roundtrip OK")


def test_delta_x_max_and_cursor_share_the_baseline_total():
    """THE D12 consistency check: delta()'s distance x-extent and the cursor mappers use the SAME
    total. With a reference loaded, the distance-mode x grid's max equals the reference total, and
    that equals active_baseline_total_distance() (what the mappers are fed) — so the cursor lands
    exactly at the right end of the curve. Verified for a reference total != the local best."""
    s, lid, times, dists = make_session(total_dist=520.0)
    seed_cols(s, lid, times, dists)        # delta() reads _lap_arrays off the columns cache
    s._best_cache = lid
    s._valid_cache = [lid]
    s.laps = SimpleNamespace(laps_count=lambda: lid + 1)  # delta()'s id-range filter
    ref_total = 640.0
    s._reference = _ref_lap(ref_total)
    base_d = s.active_baseline_total_distance()
    out = s.delta([lid], x_mode="distance")
    assert out is not None
    _best_id, speed, _delta = out
    # every series shares the one distance x-grid; its max is the baseline (reference) total
    x_axis = speed[lid][0]
    assert abs(float(x_axis[-1]) - ref_total) < 1e-6, float(x_axis[-1])
    assert abs(float(x_axis[-1]) - base_d) < 1e-9    # == what the cursor mappers are fed
    # and the cursor's finish-x matches that same axis max (cursor sits on the curve end)
    assert abs(s.plot_x_at_media_time(lid, times[-1], "distance", best_distance=base_d)
               - float(x_axis[-1])) < 1e-6
    print("test_delta_x_max_and_cursor_share_the_baseline_total OK")


def test_delta_x_max_unchanged_with_no_reference():
    """No-reference invariant: delta()'s distance x-max is the LOCAL best total and equals what
    the cursor mappers are fed — unchanged by this fix."""
    s, lid, times, dists = make_session(total_dist=520.0)
    seed_cols(s, lid, times, dists)
    s._best_cache = lid
    s._valid_cache = [lid]
    s.laps = SimpleNamespace(laps_count=lambda: lid + 1)
    assert s._ref is None
    base_d = s.active_baseline_total_distance()
    out = s.delta([lid], x_mode="distance")
    assert out is not None
    _best_id, speed, _delta = out
    x_axis = speed[lid][0]
    assert abs(float(x_axis[-1]) - float(dists[-1])) < 1e-6   # local best total
    assert abs(float(x_axis[-1]) - base_d) < 1e-9
    print("test_delta_x_max_unchanged_with_no_reference OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SCRUB CONVERSION TESTS PASSED")
