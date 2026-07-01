"""map_render — the Qt-free pure-numpy core of the track map (extracted from studio/map_view.py).

What lives here (and ONLY here): the array math the rainbow line and the bucketed polyline
rendering need — value→bucket quantization (`bucketize`), per-bucket draw-array grouping
(`bucket_polylines`), grid→points Δ resampling (`resample_grid_to_points`), and the per-channel
rainbow computation (`rainbow_channel`: the channel→value mapping, the Δ/grip NEGATION, the fixed
grip scale, and the GPS-dropout NaN-masking of cross-gap segments). Each function takes plain numpy
arrays and returns plain numpy arrays / scalars; nothing here imports Qt or pacer.

The widget (MapView._build_rainbow) fetches the per-lap channel arrays from the Session and then
calls `rainbow_channel`, turning the returned (seg_buckets, legend texts) into Qt curve items —
so the colour/bucket math is now a tested pure function rather than widget-private code.
"""

from __future__ import annotations

import numpy as np

from . import units
from .gapfill import GAP_TIME_S
from .theme import MAP_RAINBOW_N

# D5 grip utilization clips to [0, GRIP_UTIL_DISPLAY_MAX] for bucketing so the colour scale is the
# physical 0..limit range, not stretched to a lap's own max (a low-load lap then reads honestly low).
GRIP_UTIL_DISPLAY_MAX = 1.2


def bucketize(values, n_buckets: int, lo: float | None = None, hi: float | None = None):
    """Quantize values into bucket ids 0..n_buckets-1 over [lo,hi] (default: finite min/max).
    0=low (red), n-1=high (green). Non-finite -> -1 (skipped). Degenerate range (hi<=lo) ->
    middle bucket. Pure numpy."""
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, -1, dtype=np.int64)
    finite = np.isfinite(v)
    if not finite.any():
        return out
    lo = float(np.min(v[finite])) if lo is None else float(lo)
    hi = float(np.max(v[finite])) if hi is None else float(hi)
    if hi <= lo:
        out[finite] = (n_buckets - 1) // 2
        return out
    idx = np.floor((v[finite] - lo) / (hi - lo) * n_buckets).astype(np.int64)
    out[finite] = np.clip(idx, 0, n_buckets - 1)  # v == hi lands exactly on n_buckets → clamp
    return out


def bucket_polylines(xs, ys, seg_buckets, n_buckets: int):
    """Group a polyline's segments by bucket id into per-bucket draw arrays. Pure numpy.

    seg_buckets has len(xs)-1 entries (bucket of segment i->i+1; -1 = skip). Disjoint runs in a
    bucket are joined by a single NaN so one PlotCurveItem(connect='finite') draws them all."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    seg = np.asarray(seg_buckets)
    out = []
    for b in range(n_buckets):
        idx = np.flatnonzero(seg == b)
        if idx.size == 0:
            out.append((np.empty(0), np.empty(0)))
            continue
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        bx: list = []
        by: list = []
        for r in runs:
            bx.extend((xs[r[0]:r[-1] + 2], [np.nan]))  # segments i..j -> points i..j+1
            by.extend((ys[r[0]:r[-1] + 2], [np.nan]))
        out.append((np.concatenate(bx[:-1]), np.concatenate(by[:-1])))  # drop the trailing NaN
    return out


def resample_grid_to_points(cum_dist, grid_values):
    """Resample a value-on-uniform-[0,1]-grid curve onto a lap's normalized odometer distances
    (cum/cum[-1]) via np.interp. Caller guarantees cum_dist[-1] > 0."""
    cum = np.asarray(cum_dist, dtype=float)
    g = np.asarray(grid_values, dtype=float)
    return np.interp(cum / cum[-1], np.linspace(0.0, 1.0, len(g)), g)


def _seg_buckets(times, vals, lo=None, hi=None):
    """Per-segment bucket ids for a per-point value channel: endpoint-mean value, with segments
    spanning a GPS dropout (Δt > GAP_TIME_S) set to NaN (-> bucket -1, not painted) so the rainbow
    never draws a chord across a hole. `lo`/`hi` pass straight through to bucketize."""
    seg_vals = 0.5 * (vals[:-1] + vals[1:])
    seg_vals = np.where(np.diff(times) > GAP_TIME_S, np.nan, seg_vals)
    return bucketize(seg_vals, MAP_RAINBOW_N, lo=lo, hi=hi)


def rainbow_channel(mode, times, xs, ys, speed_kmh, cum, grip_util, delta_grid,
                    speed_unit=None):
    """Compute the per-segment bucket ids + legend texts for one rainbow channel. Pure numpy.

    Inputs are the lap's already-fetched per-sample arrays (the map fetches them from Session):
      * `times`, `xs`, `ys`, `speed_kmh`, `cum` — the lap_channels arrays (media s / local m / km/h /
        gap-aware odometer), all index-aligned;
      * `grip_util` — the per-sample grip utilization (lap_grip_channel), or None (no g signal);
      * `delta_grid` — the lap's Δ-vs-best curve ON THE 400-POINT GRID (delta()'s y-series), or
        None (no best lap for Δ).

    Returns `(seg_buckets, lo_text, hi_text)` where seg_buckets has len(xs)-1 entries (bucket per
    segment, -1 = skip), or None when the channel can't be computed (degenerate lap, missing g for
    grip, missing best lap / zero odometer for Δ). The Δ and grip channels are NEGATED so AHEAD /
    UNUSED grip land in the HIGH (green) buckets; grip uses a FIXED [0, GRIP_UTIL_DISPLAY_MAX] scale.
    """
    if len(xs) < 2:
        return None
    if mode == "speed":
        vals = speed_kmh
        # Bucketing is scale-invariant (min/max normalized), so the COLOURS ride the raw km/h; only
        # the legend end-labels convert to the display unit (identity for km/h).
        lo_txt = f"{units.convert_speed(float(np.min(vals)), speed_unit):.0f}"
        hi_txt = f"{units.convert_speed(float(np.max(vals)), speed_unit):.0f} {units.speed_label(speed_unit)}"
        return _seg_buckets(times, vals), lo_txt, hi_txt
    if mode == "grip":
        # D5: per-sample grip utilization (|g| / session envelope), ESTIMATED + lateral-dominant.
        # NEGATED + a FIXED [0, GRIP_UTIL_DISPLAY_MAX] scale so on-the-limit (high util) lands in
        # the LOW (red) buckets and unused grip in the HIGH (green) ones, on the physical 0..limit
        # range rather than this lap's own max.
        if grip_util is None or len(grip_util) < len(xs):
            return None
        vals = -np.asarray(grip_util[:len(xs)], float)
        seg_buckets = _seg_buckets(times, vals, lo=-GRIP_UTIL_DISPLAY_MAX, hi=0.0)
        # legend reads "on limit" (red, lo) → "unused" (green, hi)
        return seg_buckets, "on limit", "unused (est.)"
    # Δ-vs-best, resampled from the 400-grid delta() onto this lap's point distances
    if delta_grid is None or float(cum[-1]) <= 0:
        return None
    d_pts = resample_grid_to_points(cum, delta_grid)
    # Negated so ahead (negative Δ) lands in the high (green) buckets.
    vals = -d_pts
    # Legend shows the signed Δ at each end (red = most-behind, green = most-ahead).
    lo_txt = f"{-float(np.min(vals)):+.2f} s"
    hi_txt = f"{-float(np.max(vals)):+.2f} s"
    return _seg_buckets(times, vals), lo_txt, hi_txt
