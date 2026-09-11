"""map_render — pure-numpy track-map math unit tests (synthetic data).

studio.map_render is the extracted pure core of the track map — every function is numpy in, numpy
out, and pacer-free; the module itself is NOT Qt-free at import (it takes `MAP_RAINBOW_N` from
`theme`), which is why this test runs offscreen:
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
    range (low→red, high→green). The legend states that range RELATIVELY — "lowest" → the rise
    above it — because absolute GPS altitude drifts several metres between laps of the same track
    (MAP-09); a missing/too-short elevation yields no channel (degrades, doesn't crash)."""
    t, xs, ys, speed, cum = _lap_arrays()
    elev = np.linspace(10.0, 40.0, len(xs))          # a rising 10 m → 40 m slope
    seg, lo, hi = rainbow_channel("elevation", t, xs, ys, speed, cum, None, None, elevation=elev)
    assert len(seg) == len(xs) - 1
    assert (np.diff(seg) >= 0).all(), "rising altitude must paint monotonically greener"
    assert seg[0] == 0 and seg[-1] == MAP_RAINBOW_N - 1
    assert lo == "lowest" and hi == "+30 m", (lo, hi)
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


def test_rainbow_channel_delta_best_lap_hint_and_no_negative_zero():
    """L1/P2: selecting the best lap gives Δ≈0 everywhere. Instead of painting a flat mid-colour
    line with a duplicate "+0.00 s → +0.00 s" legend, the delta channel returns (None seg_buckets,
    best-lap HINT, "") — the widget then shows the hint and keeps the plain overlay. And no Δ legend
    ever renders a negative zero ("-0.00 s"); a tiny negative normalizes to "0.00 s"."""
    from studio.map_render import DELTA_BEST_LAP_HINT, DELTA_FLAT_EPS_S, _fmt_delta
    t, xs, ys, speed, cum = _lap_arrays()
    # A dead-flat (all-zero) Δ grid = the best lap vs itself.
    flat = np.zeros(400)
    seg, lo, hi = rainbow_channel("delta", t, xs, ys, speed, cum, None, flat)
    assert seg is None, "the best lap must not paint a flat mid-colour delta line"
    assert lo == DELTA_BEST_LAP_HINT and hi == "", (lo, hi)
    # A sub-5ms wobble still counts as "no delta" (under the display floor).
    tiny = np.full(400, -0.001)
    seg2, lo2, hi2 = rainbow_channel("delta", t, xs, ys, speed, cum, None, tiny)
    assert seg2 is None and lo2 == DELTA_BEST_LAP_HINT and hi2 == ""
    # _fmt_delta never emits "-0.00 s": a tiny negative → the unsigned "0.00 s".
    assert _fmt_delta(-0.0) == "0.00 s"
    assert _fmt_delta(-0.001) == "0.00 s"
    assert _fmt_delta(DELTA_FLAT_EPS_S / 2) == "0.00 s"
    assert _fmt_delta(0.31) == "+0.31 s" and _fmt_delta(-0.31) == "-0.31 s"
    print("test_rainbow_channel_delta_best_lap_hint_and_no_negative_zero OK")


# --------------------------------------------------------- Δ RATE (the Δ channel's derivative)
def _rate_lap(n=600, dt=0.1):
    """A synthetic lap at 10 Hz with a uniform odometer, so a Δ curve on the 400-grid resamples
    onto the points linearly and a known grid slope is a known per-point rate."""
    t = np.arange(n) * dt
    xs = np.cos(np.linspace(0, 2 * math.pi, n)) * 50.0
    ys = np.sin(np.linspace(0, 2 * math.pi, n)) * 30.0
    speed = np.full(n, 45.0)
    cum = np.linspace(0.0, 600.0, n)   # uniform: distance fraction == time fraction
    return t, xs, ys, speed, cum


def test_delta_rate_is_the_delta_curves_slope_in_seconds_per_second():
    """The rate is dΔ/dt in s/s, nothing more: on a Δ curve that climbs a known number of seconds
    over a known number of seconds, every interior sample reports exactly that slope."""
    from studio.map_render import RATE_WINDOW_S, delta_rate
    t = np.arange(600) * 0.1                     # 60 s
    d = 0.25 * t                                 # losing a quarter-second per second, all lap
    got = delta_rate(t, d, RATE_WINDOW_S)
    assert np.allclose(got, 0.25), (got.min(), got.max())
    # Sign convention: a FALLING Δ (taking time back) is a negative rate.
    assert np.allclose(delta_rate(t, -0.25 * t, RATE_WINDOW_S), -0.25)
    # And it integrates back to the Δ it came from — the property that makes it the same
    # measurement as the cumulative channel rather than a different one.
    wander = np.cumsum(np.sin(t) * 0.01)
    r = delta_rate(t, wander, RATE_WINDOW_S)
    assert abs(np.trapezoid(r, t) - (wander[-1] - wander[0])) < 5e-3
    print("test_delta_rate_is_the_delta_curves_slope_in_seconds_per_second OK")


def test_delta_rate_ends_are_divided_by_the_span_actually_used():
    """At the two ends the centred window is clamped to the lap, so the difference must be divided
    by the span ACTUALLY used, not by the nominal width. Dividing by the width instead halves the
    first and last samples — a fake fade to neutral at the start/finish line, which is the first
    place a driver looks. On a constant-slope Δ the rate is constant edge to edge."""
    from studio.map_render import delta_rate
    t = np.arange(200) * 0.1
    got = delta_rate(t, 0.3 * t, 0.4)
    assert abs(got[0] - 0.3) < 1e-12 and abs(got[-1] - 0.3) < 1e-12, (got[0], got[-1])
    # A window wider than the whole lap degrades to the lap's average slope, not to zero.
    assert abs(delta_rate(t, 0.3 * t, 99.0)[0] - 0.3) < 1e-12
    print("test_delta_rate_ends_are_divided_by_the_span_actually_used OK")


def test_rainbow_channel_delta_rate_paints_losing_red_and_gaining_green():
    """The channel's whole point: where the driver is LOSING time right now the line is red, where
    he is TAKING IT BACK it is green — regardless of how far behind he already is. The Δ curve here
    climbs steeply through the first third (losing), falls through the middle (gaining) and is flat
    at the end, all while staying far above zero: the CUMULATIVE channel would paint the whole lap
    red-to-amber on the way up, and it is the RATE that separates the three stretches."""
    t, xs, ys, speed, cum = _rate_lap()
    g = np.linspace(0.0, 1.0, 400)
    grid = np.piecewise(g, [g < 1 / 3, (g >= 1 / 3) & (g < 2 / 3), g >= 2 / 3],
                        [lambda u: 2.0 + 3.0 * u,          # climbing: losing
                         lambda u: 3.0 - 3.0 * (u - 1 / 3),  # falling: gaining
                         lambda u: 2.0])                     # flat: matching
    seg, lo, hi = rainbow_channel("delta_rate", t, xs, ys, speed, cum, None, grid)
    assert len(seg) == len(xs) - 1
    n = len(seg)
    losing, gaining, flat = seg[n // 12:n // 4], seg[5 * n // 12:7 * n // 12], seg[9 * n // 12:]
    assert losing.max() < 4, f"a climbing Δ must paint in the red buckets, got {losing.tolist()}"
    assert gaining.min() > 11, f"a falling Δ must paint green, got {gaining.tolist()}"
    assert set(flat.tolist()) <= {7, 8}, f"a flat Δ must paint the ramp's neutral, got {flat[:8]}"
    # The legend states the unit and carries the direction in WORDS (the non-hue cue), and the two
    # ends are ±the same number because the scale is symmetric about zero.
    assert lo.startswith("losing ") and hi.startswith("gaining "), (lo, hi)
    assert lo.endswith(" s/s") and hi.endswith(" s/s"), (lo, hi)
    assert lo.split()[1] == hi.split()[1], f"a diverging scale must be symmetric: {lo} / {hi}"
    print("test_rainbow_channel_delta_rate_paints_losing_red_and_gaining_green OK")


def test_delta_rate_zero_lands_on_the_ramps_middle_anchor():
    """The one structural difference from every other channel: this scale is SYMMETRIC, so that a
    rate of exactly zero paints the ramp's own neutral colour. With MAP_RAINBOW_N=16 that means the
    boundary between buckets 7 and 8, which is where `rainbow_colors` puts its middle anchor
    (t = i/(n-1)*2 == 1 at i = 7.5) — the mid colour therefore means "matching the baseline" in
    BOTH palettes, with no new colours invented for it."""
    from studio.map_render import RATE_SCALE_PCTL
    assert bucketize([0.0], MAP_RAINBOW_N, lo=-1.0, hi=1.0)[0] == MAP_RAINBOW_N // 2
    assert bucketize([-1e-9], MAP_RAINBOW_N, lo=-1.0, hi=1.0)[0] == MAP_RAINBOW_N // 2 - 1
    # An ASYMMETRIC lap (it loses far harder than it gains) must still be centred on zero, not
    # stretched over its own min..max the way the speed/elevation channels are: the stretch it
    # merely matches the baseline in has to keep reading neutral.
    t, xs, ys, speed, cum = _rate_lap()
    g = np.linspace(0.0, 1.0, 400)
    grid = np.where(g < 0.25, 4.0 * g, np.where(g < 0.5, 1.0 - 0.4 * (g - 0.25), 0.9))
    seg, lo, hi = rainbow_channel("delta_rate", t, xs, ys, speed, cum, None, grid)
    assert set(seg[int(0.6 * len(seg)):].tolist()) <= {7, 8}, "the matched stretch must read neutral"
    assert seg[:int(0.2 * len(seg))].max() <= 1, "the hard-losing stretch must saturate red"
    assert lo.split()[1] == hi.split()[1]
    assert RATE_SCALE_PCTL == 98.0
    print("test_delta_rate_zero_lands_on_the_ramps_middle_anchor OK")


def test_delta_rate_one_outlier_corner_does_not_own_the_colour_scale():
    """MEASURED on the dev recordings: every non-best lap of one of them peaks in the same 20 km/h
    hairpin, where the Δ curve's normalized-distance alignment turns a couple of metres of line
    difference into a few tenths that come straight back out. Scaling the ramp to the lap's MAX
    hands that one corner the whole gradient — 67% of the lap collapsed into the two middle buckets
    and only 6 of 16 colours used. The robust (p98) scale keeps the rest of the lap legible; the
    spike still saturates the end bucket, which is honest."""
    t, xs, ys, speed, cum = _rate_lap()
    g = np.linspace(0.0, 1.0, 400)
    gentle = 0.10 * np.sin(2 * math.pi * 6 * g)   # ±0.06 s/s of ordinary corner-scale structure
    spike = -0.5 * np.exp(-((g - 0.8) ** 2) / (2 * 0.0015**2))  # one violent, LOCAL excursion
    seg, _lo, _hi = rainbow_channel("delta_rate", t, xs, ys, speed, cum, None, gentle + spike)
    painted = seg[seg >= 0]
    assert len(set(painted.tolist())) >= 10, (
        f"the ordinary structure must still use most of the ramp, got "
        f"{sorted(set(painted.tolist()))}")
    mid = np.count_nonzero((painted == 7) | (painted == 8)) / len(painted)
    assert mid < 0.5, f"{mid:.0%} of the lap collapsed to neutral — the outlier owns the scale"
    assert painted.min() == 0 and painted.max() == MAP_RAINBOW_N - 1, "both ends must be reached"
    print("test_delta_rate_one_outlier_corner_does_not_own_the_colour_scale OK")


def test_rainbow_channel_delta_rate_degenerate_states():
    """The two informationless states, handled explicitly rather than painted as a rainbow:

      * the BASELINE lap — its Δ is identically zero, so its rate is identically zero. Same gate,
        same hint as the cumulative channel (there is no delta, so there is no slope either).
      * a Δ that WOBBLES below the display floor — the rate is that wobble divided by the window,
        i.e. amplified noise, and it would otherwise paint at full contrast because the scale is
        per-lap. The Δ-span gate catches it before the derivative is ever taken.
      * a Δ that moves REAL seconds but so slowly no point clears the printable 0.01 s/s.
    And the shared gates: no best lap, a zero-length odometer, a degenerate lap."""
    from studio.map_render import (
        DELTA_BEST_LAP_HINT,
        DELTA_FLAT_EPS_S,
        RATE_FLAT_EPS_SS,
        RATE_FLAT_HINT_VALUE,
    )
    t, xs, ys, speed, cum = _rate_lap()
    args = (t, xs, ys, speed, cum, None)
    seg, lo, hi = rainbow_channel("delta_rate", *args, np.zeros(400))
    assert seg is None and lo == DELTA_BEST_LAP_HINT and hi == ""
    # A sub-5 ms wobble: a rate of ±0.006 s/s if differentiated, painted at FULL contrast by a
    # per-lap scale. Gated on the Δ span, before the derivative.
    wobble = 0.002 * np.sin(np.linspace(0, 40 * math.pi, 400))
    seg, lo, hi = rainbow_channel("delta_rate", *args, wobble)
    assert seg is None and lo == DELTA_BEST_LAP_HINT and hi == "", (lo, hi)
    # A real, monotone 0.06 s taken over a 60 s lap: the Δ span clears its gate, but the rate is
    # 0.001 s/s and every legend end would read "0.00 s/s".
    creep = np.linspace(0.0, 0.06, 400)
    seg, lo, hi = rainbow_channel("delta_rate", *args, creep)
    assert seg is None and hi == "", (lo, hi)
    assert RATE_FLAT_HINT_VALUE in lo and "gain/loss rate" in lo, lo
    assert 0.06 / 60.0 < RATE_FLAT_EPS_SS, "this fixture is meant to sit under the display floor"
    assert DELTA_FLAT_EPS_S < 0.06, "…while clearing the Δ-span gate"
    # Shared gates: no baseline at all, a zero-length odometer, and a lap with no segment.
    assert rainbow_channel("delta_rate", *args, None) is None
    assert rainbow_channel("delta_rate", t, xs, ys, speed, np.zeros_like(cum), None,
                           np.linspace(0, 1, 400)) is None
    one = np.array([0.0])
    assert rainbow_channel("delta_rate", one, one, one, one, one, None,
                           np.linspace(0, 1, 400)) is None
    print("test_rainbow_channel_delta_rate_degenerate_states OK")


def test_delta_rate_never_draws_across_a_gps_dropout():
    """The rate channel goes through the same per-segment NaN-mask as every other channel, so a
    segment spanning a dropout is skipped rather than painted from an interpolation across a hole."""
    t, xs, ys, speed, cum = _rate_lap(n=40)
    t = t.copy()
    t[20:] += 6.0
    seg, _lo, _hi = rainbow_channel("delta_rate", t, xs, ys, speed, cum, None,
                                    np.linspace(0.0, 1.5, 400))
    assert seg[19] == -1, "the cross-dropout segment must be skipped"
    assert (seg[np.arange(len(seg)) != 19] >= 0).all()
    print("test_delta_rate_never_draws_across_a_gps_dropout OK")


def test_rainbow_channel_grip_fixed_scale_and_negation():
    """Grip is NEGATED on a FIXED [0, GRIP_UTIL_DISPLAY_MAX] scale (not the lap's own max): a
    rising util ramp paints monotonically redder (more grip used = redder), the unused end is
    greener than the on-limit end, and the legend is the fixed 'committed'/'unused (est.)' pair
    (the ⚠ is the non-hue at-limit cue for colour-blind readers)."""
    t, xs, ys, speed, cum = _lap_arrays()
    util = np.linspace(0.1, 1.1, len(xs))         # unused → over the limit
    seg, lo, hi = rainbow_channel("grip", t, xs, ys, speed, cum, util, None)
    assert (np.diff(seg) <= 0).all(), "rising util must paint monotonically redder"
    assert seg[0] > seg[-1], (seg[0], seg[-1])    # unused greener than on-limit
    # "committed", not "⚠ on limit": ⚠ is the app's DISTRUST glyph, and this end of the grip
    # channel is the driver using the tyre he has — the good end. See map_render for the accent.
    assert lo == "committed" and hi == "unused (est.)"
    assert "⚠" not in lo, "the grip legend must not wear the distrust glyph"
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
    """The grip display ceiling lives in map_render (the pure layer) — the widget's single source."""
    assert map_render.GRIP_UTIL_DISPLAY_MAX == 1.2
    print("test_grip_display_max_is_the_module_constant OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} MAP RENDER TESTS PASSED")
