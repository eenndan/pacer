"""map_render — the pure-numpy core of the track map (extracted from studio/map_view.py).

What lives here (and ONLY here): the array math the rainbow line and the bucketed polyline
rendering need — value→bucket quantization (`bucketize`), per-bucket draw-array grouping
(`bucket_polylines`), grid→points Δ resampling (`resample_grid_to_points`), the local gain/loss
rate (`delta_rate`), and the per-channel rainbow computation (`rainbow_channel`: the channel→value
mapping, the Δ/Δ-rate/grip NEGATION, the fixed grip scale, the Δ-rate SYMMETRIC scale, and the
GPS-dropout NaN-masking of cross-gap segments). Each function takes plain numpy arrays and returns
plain numpy arrays / scalars, and no function here touches Qt or pacer.

The MODULE, however, is not Qt-free at import: `from .theme import MAP_RAINBOW_N` takes one int
from the palette module, and `theme` imports PySide6 — so `import studio.map_render` really does
load Qt (measured; `tests/test_layering.py` pins it in `QT_REACHING` rather than `ALLOWED_QT`).
Nothing depends on that being true; taking the constant from a Qt-free home would make the module
genuinely headless and the guard would then require shrinking `QT_REACHING` in the same PR.

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

# Δ-to-best channel: below this |Δ| span (s) across the whole lap the delta is informationless — the
# selected lap IS (essentially) the best lap, so every segment lands in the middle bucket and both
# legend ends would read "+0.00 s". At/under this the channel reports the DELTA_BEST_LAP hint
# instead of painting a flat mid-colour line with a duplicate-zero legend (L1). 5 ms = the display
# resolution ("+0.00 s" already rounds anything smaller to zero).
DELTA_FLAT_EPS_S = 0.005
# Sentinel legend the widget maps to a "best lap — no delta" hint (lo text) + a blank hi text.
DELTA_BEST_LAP_HINT = "this is your best lap — no delta"

# ---- Δ RATE channel (the Δ channel's derivative) ----
# The cumulative Δ answers "how far behind am I by this point"; on a map that is dominated by where
# you ALREADY were behind — a corner you are actively gaining in still paints red while you carry a
# 1.2 s deficit into it. The rate channel paints dΔ/dt instead: seconds gained or lost PER SECOND
# of driving, right here. It is a DIVERGING quantity (gaining / neutral / losing), so unlike every
# other channel its colour scale is SYMMETRIC about zero — see RATE_SCALE_PCTL.
#
# The smoothing window, in seconds of travel: a centred difference over `RATE_WINDOW_S`, which is
# the Δ curve's derivative convolved with a boxcar of that width.
#
# THE EXPECTED PROBLEM — that differentiating a 10 Hz GPS-derived curve gives static that has to be
# filtered back out — DID NOT SHOW UP. Measured over every non-best lap of both dev recordings (37
# on 060, 65 on 062) at w ∈ {0.1 … 2.0} s, on the PAINTED buckets rather than on the raw signal:
#   * a raw per-sample slope already paints runs of 4.3-4.9 samples (~0.45 s) of constant colour,
#     changing colour 2.2-2.5x per second — a corner-scale signal, not per-sample static;
#   * widening the window to 0.4 s moves that by ~7% (flicker 0.27 → 0.25 bucket steps per segment
#     on 060, colour changes 2.47/s → 2.34/s) and to 2.0 s by ~40%. There is no knee anywhere in
#     the range: the window trades flicker against end-fidelity monotonically and gently.
#   * the split-half spatial correlation (mean rate(s) over the odd laps vs the even laps) is
#     +0.93 on 060 and +0.95 on 062 and moves by 0.001 across 0.1 → 0.5 s. What this channel paints
#     is repeatable track-position structure, and the window is not what makes it so.
# The Δ curve is a CUMULATIVE quantity (an elapsed-time integral) resampled through the 400-point
# distance grid, whose cells are 0.17 s on a 69 s lap — coarser than the 0.1 s samples. Both steps
# are low-passes, so the derivative arrives already smoothed.
#
# 0.4 s is therefore a judgement inside a measured-flat band, not a threshold:
#   * anything at or under 0.2 s is a literal NO-OP at 10 Hz — a ±0.1 s centred difference IS the
#     per-sample slope, and reproduces it to three decimals;
#   * ∫rate·dt has to come back to the lap's total Δ, and does — 0.2 ms of error at 0.4 s, rising
#     to 1.8 ms at 1.2 s and 3.6 ms at 2.0 s as the boxcar smears the lap's two ends;
#   * stating a window in SECONDS OF TRAVEL (rather than "one sample") is what keeps the channel
#     honest if the sample rate ever changes: at 20 Hz the raw slope would be static, and this
#     definition would not move.
RATE_WINDOW_S = 0.4
# Below this |rate| everywhere, the channel has nothing to paint: 0.005 s/s is the display floor of
# the "0.01 s/s" the legend can print, the same reasoning DELTA_FLAT_EPS_S applies to the Δ itself.
RATE_FLAT_EPS_SS = 0.005
# The symmetric colour scale is the 98th percentile of |rate|, NOT the lap's max, and that is the
# one choice here that measurably changes what the map says.
#
# On the 060 recording every one of the 37 non-best laps has its single largest |rate| in the SAME
# 20 km/h hairpin, at s = 0.869-0.873 — a spread of four thousandths of a lap. That is not driving,
# it is the normalized-distance alignment the Δ curve is built on: at the slowest corner on the
# track a couple of metres of line difference is a few tenths of a second, and it comes straight
# back out. Across that corner the fleet-mean NET Δ change is -0.005 s against a mean SWING of
# 0.457 s — a phase dipole, not time won. (The 062 recording has no sub-30 km/h corner, its |rate|
# extremes land at six different places on the track, and its max/p98 is 1.19.)
#
# Scaling to the max therefore lets one corner of one recording own the whole ramp. Measured, as
# the median non-best lap's painted bucket histogram:
#          scale     in the 2 middle buckets     clipped     buckets used     entropy
#   060    max               67.1%                 0.6%          6 / 16         0.554
#   060    p98               35.8%                 3.6%         13 / 16         0.818
#   062    max               41.3%                 1.6%         12 / 16         0.752
#   062    p98               33.8%                 4.0%         13 / 16         0.827
# p98 lands the two recordings on nearly the same visual density (36% vs 34% neutral, 13 buckets
# each) where `max` differs by 26 points — a robust scale costs almost nothing where there is no
# outlier and saves the map where there is one. p95 was also tried: it is visually
# indistinguishable at 8% clipped instead of 4%, so the more conservative of the two wins.
RATE_SCALE_PCTL = 98.0
# Sentinel legend for a lap whose Δ moves but whose RATE never clears the display floor. DERIVED
# from the floor, not typed beside it: the smallest non-zero a 2-decimal legend can print is twice
# the rounding floor, so the two move together or not at all.
RATE_FLAT_HINT_VALUE = f"under {2 * RATE_FLAT_EPS_SS:.2f} s/s"

# Elevation legend: the low end is labelled RELATIVELY ("lowest"), the high end as the RISE above
# it ("+5 m"), never as two absolute altitudes. GPS altitude carries a slowly-drifting bias of
# several metres, so the same physical corner reads 79.9 m on one lap and 83.0 m on another
# (measured across 21 laps of one recording) — a 3.1 m disagreement quoted to 1 m, against a lap
# profile only 4.5 m tall. What the colours actually encode is the WITHIN-LAP shape (the channel is
# min/max normalised per lap), and that is exactly what these two labels now claim. Elevation
# analytics stay out of scope; this is what the existing control says about itself (MAP-09).
ELEVATION_LO_LABEL = "lowest"


def _flat_hint(channel: str, value: str) -> str:
    """The single-label legend for a channel with no gradient to paint (MAP-10).

    Returned as the `lo` text with an EMPTY `hi` text — the same hint shape the Δ channel uses — so
    the widget hides the colour ramp instead of painting a full red→green strip under two labels
    that read the same. The measured case: a re-segmentation left a 2-sample segment whose speed
    was 43.24 km/h at both ends, and `bucketize`'s degenerate (hi<=lo) branch dropped every segment
    in the middle bucket while the legend still promised a gradient from `43` to `43 km/h`."""
    return f"{channel} is {value} for this whole lap — no gradient"


def _fmt_delta(x: float) -> str:
    """Format a signed Δ in seconds, normalizing a tiny negative to +0.00 (never "-0.00 s") (P2).
    Anything with |x| under the 5 ms display floor renders as the unsigned "0.00 s"."""
    if abs(x) < DELTA_FLAT_EPS_S:
        return "0.00 s"
    return f"{x:+.2f} s"


def _fmt_rate(x: float) -> str:
    """Format a gain/loss RATE as a MAGNITUDE in s/s — `_fmt_delta`'s treatment for the derived
    channel. Unsigned on purpose: the rate legend's two ends are ±the same number, so the direction
    is carried by the words ("losing" / "gaining") rather than by a sign the eye has to hunt for —
    the same non-hue cue the grip channel's endpoints use."""
    return f"{abs(x):.2f} s/s"


def delta_rate(times, delta_points, window_s: float = RATE_WINDOW_S):
    """The LOCAL gain/loss rate dΔ/dt (seconds per second) of a per-point Δ-vs-best curve.

    A centred difference over `window_s` seconds of TRAVEL — mathematically the Δ curve's
    derivative convolved with a boxcar of that width, which is what makes the window a plain
    statement of scale ("smoothed over 0.4 s") rather than a filter coefficient.

    At the two ends the window is CLAMPED to the lap and the difference is divided by the span
    actually used, not by `window_s`. Dividing by the nominal width instead would halve the first
    and last samples' rate — a fake fade to neutral at the start/finish line, which is exactly
    where a driver looks first. Pure numpy; `times` must be non-decreasing (a lap's are)."""
    t = np.asarray(times, dtype=float)
    d = np.asarray(delta_points, dtype=float)
    half = 0.5 * float(window_s)
    lo = np.clip(t - half, t[0], t[-1])
    hi = np.clip(t + half, t[0], t[-1])
    span = hi - lo
    out = np.zeros_like(d)
    ok = span > 0
    out[ok] = (np.interp(hi[ok], t, d) - np.interp(lo[ok], t, d)) / span[ok]
    return out


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


def _delta_rate_channel(times, d_pts):
    """The Δ-RATE channel: per-segment buckets + legend for dΔ/dt, on a scale SYMMETRIC about zero.

    Called with the same per-point Δ curve the cumulative Δ channel paints, already gated on
    "there is a best lap and this is not it" — so the only degenerate state left to it is a Δ that
    moves, but so slowly that no point of the lap clears the printable 0.01 s/s.

    THE SYMMETRY IS THE WHOLE POINT, and it is what makes this the app's first DIVERGING map
    channel. Every other channel min/max-normalizes (or, for grip, clips to a physical range) and
    reads left-to-right as "less → more"; here the meaningful landmark is in the MIDDLE, and the
    reader has to be able to see zero. `rainbow_colors` is already a three-anchor
    behind → mid → ahead ramp, so no new colours are needed — bucketing over [-scale, +scale] puts
    a rate of exactly zero on the boundary between buckets 7 and 8 of 16, which is precisely where
    `rainbow_colors` places its middle anchor (t = i/(n-1)*2 == 1 at i = 7.5). Neutral driving
    therefore paints the ramp's own neutral colour, in either palette, for free.

    Values beyond ±scale CLAMP into the end buckets (bucketize's documented behaviour), which is
    what lets the scale be robust rather than max-driven — see RATE_SCALE_PCTL."""
    rate = delta_rate(times, d_pts)
    peak = float(np.max(np.abs(rate)))
    if peak < RATE_FLAT_EPS_SS:
        return None, _flat_hint("gain/loss rate", RATE_FLAT_HINT_VALUE), ""
    # Never claim a finer scale than the legend can print: a "0.00 s/s" endpoint labels nothing.
    scale = max(float(np.percentile(np.abs(rate), RATE_SCALE_PCTL)), RATE_FLAT_EPS_SS)
    # Negated like the Δ channel, so GAINING (rate < 0) lands in the high (green) buckets.
    seg_buckets = _seg_buckets(times, -rate, lo=-scale, hi=scale)
    # The ends are ±the same number, so the words — not a sign — carry the direction, and the
    # symmetry itself says the middle of the strip is "matching the baseline". The unit is on both
    # ends because either end may be the one a reader looks at.
    return seg_buckets, f"losing {_fmt_rate(scale)}", f"gaining {_fmt_rate(scale)}"


def rainbow_channel(mode, times, xs, ys, speed_kmh, cum, grip_util, delta_grid,
                    speed_unit=None, elevation=None):
    """Compute the per-segment bucket ids + legend texts for one rainbow channel. Pure numpy.

    Inputs are the lap's already-fetched per-sample arrays (the map fetches them from Session):
      * `times`, `xs`, `ys`, `speed_kmh`, `cum` — the lap_channels arrays (media s / local m / km/h /
        gap-aware odometer), all index-aligned;
      * `grip_util` — the per-sample grip utilization (lap_grip_channel), or None (no g signal);
      * `delta_grid` — the lap's Δ-vs-best curve ON THE 400-POINT GRID (delta()'s y-series), or
        None (no best lap for Δ).

    Returns `(seg_buckets, lo_text, hi_text)` where seg_buckets has len(xs)-1 entries (bucket per
    segment, -1 = skip), or None when the channel can't be computed (degenerate lap, missing g for
    grip, missing best lap / zero odometer for Δ). A `(None, hint_text, "")` triple is the
    NO-GRADIENT case — the Δ best-lap hint, or a measured channel whose two ends label the same
    (`_flat_hint`) — and the widget then shows that one label with no colour ramp.
    The Δ, Δ-RATE and grip channels are NEGATED so AHEAD / GAINING /
    UNUSED grip land in the HIGH (green) buckets; grip uses a FIXED [0, GRIP_UTIL_DISPLAY_MAX] scale
    and Δ-rate a SYMMETRIC one about zero (see `_delta_rate_channel`).
    """
    if len(xs) < 2:
        return None
    if mode == "speed":
        vals = np.asarray(speed_kmh, float)
        if not np.isfinite(vals).any():
            return None
        # Bucketing is scale-invariant (min/max normalized), so the COLOURS ride the raw km/h; only
        # the legend end-labels convert to the display unit (identity for km/h).
        lo_txt = f"{units.convert_speed(float(np.nanmin(vals)), speed_unit):.0f}"
        hi_num = f"{units.convert_speed(float(np.nanmax(vals)), speed_unit):.0f}"
        unit = units.speed_label(speed_unit)
        if lo_txt == hi_num:  # both ends round to the same number: no gradient to label (MAP-10)
            return None, _flat_hint("speed", f"{hi_num} {unit}"), ""
        return _seg_buckets(times, vals), lo_txt, f"{hi_num} {unit}"
    if mode == "grip":
        # D5: per-sample grip utilization (|g| / session envelope), ESTIMATED + lateral-dominant.
        # NEGATED + a FIXED [0, GRIP_UTIL_DISPLAY_MAX] scale so on-the-limit (high util) lands in
        # the LOW (red) buckets and unused grip in the HIGH (green) ones, on the physical 0..limit
        # range rather than this lap's own max.
        if grip_util is None or len(grip_util) < len(xs):
            return None
        vals = -np.asarray(grip_util[:len(xs)], float)
        seg_buckets = _seg_buckets(times, vals, lo=-GRIP_UTIL_DISPLAY_MAX, hi=0.0)
        # Legend reads "committed" (red, lo) → "unused" (green, hi). The words are the non-hue cue:
        # the grip map is a red→green gradient with no shape or sign of its own, so the labelled
        # endpoints have to carry "using the tyre vs grip left" without the hue (colour blindness /
        # greyscale) — which they do, and did before, as words.
        #
        # THE WORD USED TO BE "⚠ on limit", AND THE ACCENT WAS BACKWARDS. ⚠ is this app's distrust
        # glyph (a GPS-dropout lap, a caveated trust term): on the one channel where the low end is
        # the driver doing it RIGHT — at the limit, using the grip he has — it read as a warning
        # about his best cornering. "committed" says the same thing in the coach's voice, keeps the
        # endpoint labelled, and leaves ⚠ meaning exactly one thing app-wide.
        return seg_buckets, "committed", "unused (est.)"
    if mode == "elevation":
        # Altitude along the lap (metres, boxcar-smoothed at load). Informational — no good/bad
        # direction — so it rides this lap's own min→max range: low = low (red) bucket, high = green.
        # The legend states that range RELATIVELY (lowest → +N m), never as two absolute GPS
        # altitudes — see ELEVATION_LO_LABEL for the measured reason.
        if elevation is None or len(elevation) < len(xs):
            return None
        vals = np.asarray(elevation[:len(xs)], float)
        if not np.isfinite(vals).any():
            return None
        rise = float(np.nanmax(vals)) - float(np.nanmin(vals))
        hi_txt = f"+{rise:.0f} m"
        if hi_txt == "+0 m":  # a rise under half a metre is GPS noise, not relief (MAP-10)
            return None, _flat_hint("elevation", "flat"), ""
        return _seg_buckets(times, vals), ELEVATION_LO_LABEL, hi_txt
    # Δ-vs-best, resampled from the 400-grid delta() onto this lap's point distances. BOTH Δ
    # channels start here — the cumulative one below and the RATE one, which differentiates it —
    # so they can never disagree about the baseline, the alignment or the best-lap gate.
    if delta_grid is None or float(cum[-1]) <= 0:
        return None
    d_pts = resample_grid_to_points(cum, delta_grid)
    # Negated so ahead (negative Δ) lands in the high (green) buckets.
    vals = -d_pts
    d_min, d_max = -float(np.max(vals)), -float(np.min(vals))  # signed Δ extremes (min ≤ max)
    # L1: when the Δ span across the whole lap is ~0 the selected lap IS the best lap — painting a
    # flat mid-bucket line with a duplicate "+0.00 s → +0.00 s" legend says nothing. Report the
    # best-lap hint instead (the widget greys the strip / shows "no delta"), skipping the flat paint.
    #
    # The RATE channel takes the same gate FIRST and for a stronger reason: on the baseline lap the
    # Δ curve is identically zero, so its derivative is identically zero too — and on a lap whose Δ
    # merely wobbles below this floor, the derivative is that wobble divided by RATE_WINDOW_S, i.e.
    # pure amplified noise dressed as a full-contrast rainbow. A flat Δ has no rate worth painting.
    if d_max - d_min < DELTA_FLAT_EPS_S:
        return None, DELTA_BEST_LAP_HINT, ""
    if mode == "delta_rate":
        return _delta_rate_channel(times, d_pts)
    # Legend shows the signed Δ at each end (red = most-behind, green = most-ahead); -0.00 normalized.
    lo_txt = _fmt_delta(d_max)   # low (red) bucket = most-behind = the max signed Δ
    hi_txt = _fmt_delta(d_min)   # high (green) bucket = most-ahead = the min signed Δ
    return _seg_buckets(times, vals), lo_txt, hi_txt
