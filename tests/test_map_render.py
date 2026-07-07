"""map_render — Qt-free pure-numpy track-map math unit tests (synthetic data).

studio.map_render is the extracted pure core of the track map (no Qt, no pacer):
  * `bucketize` — values → bucket ids over [lo, hi]: known mappings, hi lands in the TOP bucket
    (clamped), NaN → -1, degenerate (flat) range → the middle bucket, explicit lo/hi override.
  * `bucket_polylines` — per-bucket draw arrays: consecutive same-bucket segments share their
    joint point; NON-adjacent runs are separated by exactly one NaN (the connect='finite' break);
    -1 segments are skipped; unused buckets come back empty.
  * `resample_grid_to_points` — the 400-grid Δ resampled onto a lap's odometer == a direct
    np.interp on normalized distance (REUSE, never recompute), endpoint preserved.
  * `rainbow_channel` — the per-channel value/bucket math the widget used to inline: the speed /
    Δ / grip channels, the Δ + grip NEGATION (ahead / unused-grip → high green buckets), the grip
    FIXED [0, GRIP_UTIL_DISPLAY_MAX] scale, the GPS-dropout NaN-mask, the legend texts, and the
    degenerate / missing-input → None gates.

This file needs NO Qt — it imports the pure module directly.
Run: python tests/test_map_render.py
"""
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from studio import map_render  # noqa: E402
from studio.map_render import (  # noqa: E402
    GRIP_UTIL_DISPLAY_MAX,
    bucket_polylines,
    bucketize,
    rainbow_channel,
    resample_grid_to_points,
)
from studio.theme import MAP_RAINBOW_N  # noqa: E402


# ------------------------------------------------------------------ bucketize
def test_bucketize_known_values():
    """Values spread over [0, 16) with 16 buckets land in floor(v) buckets; the exact max
    CLAMPS into the top bucket (never an out-of-range id)."""
    v = [0.0, 0.5, 1.0, 7.99, 15.0, 16.0]
    got = bucketize(v, 16, lo=0.0, hi=16.0)
    assert got.tolist() == [0, 0, 1, 7, 15, 15], got
    # Default lo/hi = the data min/max: min → bucket 0, max → top bucket.
    got = bucketize([10.0, 12.0, 20.0], 4)
    assert got[0] == 0 and got[-1] == 3
    # Below-lo / above-hi inputs clamp to the extreme buckets (no -1, no overflow).
    got = bucketize([-5.0, 99.0], 8, lo=0.0, hi=10.0)
    assert got.tolist() == [0, 7], got
    print("test_bucketize_known_values OK")


def test_bucketize_nan_and_flat():
    """NaN/inf → -1 (the 'skip this segment' marker); a FLAT channel (hi <= lo) puts every
    finite value in the MIDDLE bucket — no fake red/green story without contrast."""
    got = bucketize([1.0, float("nan"), 2.0, float("inf")], 16)
    assert got[1] == -1 and got[3] == -1 and got[0] == 0 and got[2] == 15
    flat = bucketize([5.0, 5.0, float("nan")], 16)
    assert flat.tolist() == [7, 7, -1], flat  # (16-1)//2 == 7
    assert bucketize([float("nan")] * 3, 16).tolist() == [-1, -1, -1]
    print("test_bucketize_nan_and_flat OK")


def test_bucketize_monotonic_in_value():
    """Bucket id is non-decreasing in the channel value — the gradient can never invert."""
    v = np.linspace(-3.0, 11.0, 257)
    ids = bucketize(v, 16)
    assert (np.diff(ids) >= 0).all()
    assert ids[0] == 0 and ids[-1] == 15
    print("test_bucketize_monotonic_in_value OK")


# ------------------------------------------------------------- bucket_polylines
def test_bucket_polylines_runs_and_nan_breaks():
    """6 points / 5 segments with seg buckets [0, 0, 1, -1, 1]:
      * bucket 0: one run, segments 0-1 → points 0..2 inclusive, NO NaN;
      * bucket 1: two NON-adjacent runs (segments 2 and 4) → points 2..3, ONE NaN, points 4..5;
      * the -1 segment (3) is painted by nobody; every other bucket is empty."""
    xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0, 5.0])
    ys = xs * 10.0
    out = bucket_polylines(xs, ys, [0, 0, 1, -1, 1], n_buckets=4)
    assert len(out) == 4
    b0x, b0y = out[0]
    assert b0x.tolist() == [0.0, 1.0, 2.0] and b0y.tolist() == [0.0, 10.0, 20.0]
    assert np.isfinite(b0x).all(), "single run must carry no NaN break"
    b1x, b1y = out[1]
    # [x2, x3, NaN, x4, x5] — exactly one NaN, exactly between the two runs.
    assert len(b1x) == 5 and math.isnan(b1x[2]) and math.isnan(b1y[2])
    assert b1x[[0, 1, 3, 4]].tolist() == [2.0, 3.0, 4.0, 5.0]
    # connect='finite' semantics: the finite mask has exactly two runs of 2 points.
    finite_runs = np.flatnonzero(np.diff(np.isfinite(b1x).astype(int)) != 0)
    assert len(finite_runs) == 2, "exactly one break"
    for b in (2, 3):
        assert out[b][0].size == 0 and out[b][1].size == 0
    print("test_bucket_polylines_runs_and_nan_breaks OK")


def test_bucket_polylines_shared_joint_points():
    """Adjacent segments of DIFFERENT buckets both include their shared joint point, so the
    painted line is continuous (no 1-segment hole at every colour change)."""
    xs = np.arange(4.0)
    ys = np.zeros(4)
    out = bucket_polylines(xs, ys, [0, 1, 1], n_buckets=2)
    assert out[0][0].tolist() == [0.0, 1.0]            # segment 0 → points 0..1
    assert out[1][0].tolist() == [1.0, 2.0, 3.0]       # segments 1-2 → points 1..3
    # Point 1 appears in BOTH buckets — the joint is shared, the line unbroken.
    assert 1.0 in out[0][0] and 1.0 in out[1][0]
    print("test_bucket_polylines_shared_joint_points OK")


# ------------------------------------------------------- Δ resampling (grid → points)
def test_resample_grid_to_points_matches_direct_interp():
    """The helper must equal a DIRECT np.interp of the 400-grid onto normalized distances —
    nothing recomputed, endpoints preserved (Δ at the finish == the laptime difference)."""
    rng = np.random.default_rng(42)
    # A realistic non-uniform odometer (speeds vary) ending at ~830 m.
    steps = rng.uniform(0.4, 1.6, 900)
    cum = np.concatenate([[0.0], np.cumsum(steps)])
    grid_vals = np.cumsum(rng.normal(0.0, 0.01, 400))  # a wandering Δ curve on the 400-grid
    got = resample_grid_to_points(cum, grid_vals)
    want = np.interp(cum / cum[-1], np.linspace(0.0, 1.0, 400), grid_vals)
    assert np.array_equal(got, want)
    assert got[0] == grid_vals[0] and got[-1] == grid_vals[-1]  # endpoints exact
    assert len(got) == len(cum)
    print("test_resample_grid_to_points_matches_direct_interp OK")


# ------------------------------------------------------------- rainbow_channel
def _lap_arrays(n=60):
    """A synthetic lap: rising speed ramp, no GPS dropouts, monotonic odometer."""
    t = np.arange(n) * 0.1
    xs = np.cos(np.linspace(0, 2 * math.pi, n)) * 50.0
    ys = np.sin(np.linspace(0, 2 * math.pi, n)) * 30.0
    speed = np.linspace(20.0, 60.0, n)            # km/h, strictly rising
    cum = np.linspace(0.0, 500.0, n)
    return t, xs, ys, speed, cum


def test_rainbow_channel_speed_extremes_and_legend():
    """The speed channel buckets the per-segment endpoint-mean speed (default min/max scale): on a
    strictly-rising ramp the segment ids rise monotonically (slow→red, fast→green), and the legend
    reads the raw min/max with the km/h unit on the high end."""
    t, xs, ys, speed, cum = _lap_arrays()
    seg, lo, hi = rainbow_channel("speed", t, xs, ys, speed, cum, None, None)
    assert len(seg) == len(xs) - 1
    assert (np.diff(seg) >= 0).all(), "rising speed must paint monotonically greener"
    assert seg[0] == 0 and seg[-1] == MAP_RAINBOW_N - 1
    assert lo == f"{float(speed.min()):.0f}" and hi == f"{float(speed.max()):.0f} km/h"
    print("test_rainbow_channel_speed_extremes_and_legend OK")


def test_rainbow_channel_elevation_bucketizes_altitude_with_metre_legend():
    """The Elevation channel (F3) colours the line by per-sample altitude on this lap's own min→max
    range (low→red, high→green), legend in metres; a missing/too-short elevation yields no channel
    (degrades, doesn't crash)."""
    t, xs, ys, speed, cum = _lap_arrays()
    elev = np.linspace(10.0, 40.0, len(xs))          # a rising 10 m → 40 m slope
    seg, lo, hi = rainbow_channel("elevation", t, xs, ys, speed, cum, None, None, elevation=elev)
    assert len(seg) == len(xs) - 1
    assert (np.diff(seg) >= 0).all(), "rising altitude must paint monotonically greener"
    assert seg[0] == 0 and seg[-1] == MAP_RAINBOW_N - 1
    assert lo == "10 m" and hi == "40 m", (lo, hi)
    assert rainbow_channel("elevation", t, xs, ys, speed, cum, None, None, elevation=None) is None
    assert rainbow_channel("elevation", t, xs, ys, speed, cum, None, None, elevation=elev[:3]) is None
    print("test_rainbow_channel_elevation_bucketizes_altitude_with_metre_legend OK")


def test_rainbow_channel_delta_negated_and_gated():
    """Δ is resampled from the 400-grid then NEGATED before bucketing: ahead (Δ<0) → high (green)
    buckets, behind (Δ>0) → low (red). The legend prints the SIGNED Δ at each end. A None grid or a
    zero-length odometer → None (the widget then falls back to the plain overlay)."""
    t, xs, ys, speed, cum = _lap_arrays()
    grid = np.linspace(-0.8, 1.2, 400)            # ahead early, behind late
    seg, lo, hi = rainbow_channel("delta", t, xs, ys, speed, cum, None, grid)
    # The lap odometer is uniform here, so the resampled Δ rises with distance → negated falls →
    # the bucket ids fall (early ahead = greener, late behind = redder).
    assert (np.diff(seg) <= 0).all(), "ahead→behind must paint monotonically redder"
    assert seg[0] > seg[-1]
    # Legend reads the signed Δ: most-behind on the low end, most-ahead on the high end.
    vals = -resample_grid_to_points(cum, grid)
    assert lo == f"{-float(np.min(vals)):+.2f} s" and hi == f"{-float(np.max(vals)):+.2f} s"
    # Gates: no grid (no best lap) and a zero-length odometer both → None.
    assert rainbow_channel("delta", t, xs, ys, speed, cum, None, None) is None
    assert rainbow_channel("delta", t, xs, ys, speed, np.zeros_like(cum), None, grid) is None
    print("test_rainbow_channel_delta_negated_and_gated OK")


def test_rainbow_channel_grip_fixed_scale_and_negation():
    """Grip is NEGATED on a FIXED [0, GRIP_UTIL_DISPLAY_MAX] scale (not the lap's own max): a
    rising util ramp paints monotonically redder (more grip used = redder), the unused end is
    greener than the on-limit end, and the legend is the fixed '⚠ on limit'/'unused (est.)' pair
    (the ⚠ is the non-hue at-limit cue for colour-blind readers)."""
    t, xs, ys, speed, cum = _lap_arrays()
    util = np.linspace(0.1, 1.1, len(xs))         # unused → over the limit
    seg, lo, hi = rainbow_channel("grip", t, xs, ys, speed, cum, util, None)
    assert (np.diff(seg) <= 0).all(), "rising util must paint monotonically redder"
    assert seg[0] > seg[-1], (seg[0], seg[-1])    # unused greener than on-limit
    assert lo == "⚠ on limit" and hi == "unused (est.)"
    # The fixed scale is the contract: equal to bucketize(neg-util-seg, lo=-MAX, hi=0).
    vals = -util
    seg_vals = 0.5 * (vals[:-1] + vals[1:])
    want = bucketize(seg_vals, MAP_RAINBOW_N, lo=-GRIP_UTIL_DISPLAY_MAX, hi=0.0)
    assert seg.tolist() == want.tolist()
    # No g signal / too-short util → None (graceful degrade).
    assert rainbow_channel("grip", t, xs, ys, speed, cum, None, None) is None
    assert rainbow_channel("grip", t, xs, ys, speed, cum, util[:3], None) is None
    print("test_rainbow_channel_grip_fixed_scale_and_negation OK")


def test_rainbow_channel_gps_dropout_masks_segment():
    """A segment that spans a GPS dropout (Δt > GAP_TIME_S) is set to NaN → bucket -1, so the
    rainbow never draws a chord across the hole. Only that one segment is dropped."""
    t, xs, ys, speed, cum = _lap_arrays(n=10)
    t = t.copy()
    t[5:] += 5.0  # a 5 s hole between samples 4 and 5 → segment 4 spans a dropout
    seg, _lo, _hi = rainbow_channel("speed", t, xs, ys, speed, cum, None, None)
    assert seg[4] == -1, "the cross-dropout segment must be skipped"
    assert (seg[np.arange(len(seg)) != 4] >= 0).all(), "only the gap segment is dropped"
    print("test_rainbow_channel_gps_dropout_masks_segment OK")


def test_rainbow_channel_degenerate_lap_is_none():
    """A degenerate lap (<2 points) yields None for every channel — there is no segment to paint."""
    t, xs, ys, speed, cum = (np.array([0.0]),) * 5
    for mode in ("speed", "delta", "grip"):
        assert rainbow_channel(mode, t, xs, ys, speed, cum,
                               np.array([0.0]), np.linspace(0, 1, 400)) is None, mode
    print("test_rainbow_channel_degenerate_lap_is_none OK")


def test_grip_display_max_is_the_module_constant():
    """The grip display ceiling lives in map_render (Qt-free) — the single source the widget reads."""
    assert map_render.GRIP_UTIL_DISPLAY_MAX == 1.2
    print("test_grip_display_max_is_the_module_constant OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} MAP RENDER TESTS PASSED")
