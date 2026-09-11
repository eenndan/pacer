"""Chart statistics: the datum-cursor interval maths, the visible-window per-channel readout,
and the steepest-delta-slope search that walks a lap's losses.

Qt-free and pacer-free numpy on the arrays `plots_view` already caches for its curves
(`_speed_curves` / `_delta_curves`), so the whole instrument layer is testable headlessly and the
view stays a view. Every x array here is the shared plot axis (metres in distance mode, seconds
into the lap in time mode) and is MONOTONIC in both — `session.delta` builds them off one
increasing s-grid — which is what lets every lookup here be a `searchsorted`, not a scan.
"""

from __future__ import annotations

from typing import NamedTuple

import numpy as np

# ---------------------------------------------------------------- the window-statistic vocabulary
# The six statistics the visible-range readout can show, in the order the selector lists them.
# `current` is the value under the playhead; the other five are reductions over whatever x-range
# the charts are currently showing (Racelogic Circuit Tools / MoTeC i2 / WinDarab all ship this
# same set, and `range`/`delta` are the two that are NOT interchangeable: range is max−min, delta
# is last−first, and on a speed trace through one corner they differ by the whole entry speed).
STAT_CURRENT = "current"
STAT_MIN = "min"
STAT_MAX = "max"
STAT_MEAN = "mean"
STAT_RANGE = "range"
STAT_DELTA = "delta"
WINDOW_STATS = (STAT_CURRENT, STAT_MIN, STAT_MAX, STAT_MEAN, STAT_RANGE, STAT_DELTA)
# Two names per statistic, because the two places they appear have different budgets. SHORT is what
# the selector and the readout print; LONG is what the selector's hover explains.
#
# "value", not "current", and that is a width measurement rather than a preference: under the
# shipped theme a `stat: current` selector measures 118 px, which takes the charts TOOLBAR to 563 px
# — 6 px past the charts HEADER's 557 px need, and the header is what
# tests/test_charts_header_budget pins as the column's floor. `stat: value` measures 108 px and
# leaves the floor exactly where it was. It is also i2's own word for the reading at the cursor.
STAT_SHORT = {
    STAT_CURRENT: "value",
    STAT_MIN: "min",
    STAT_MAX: "max",
    STAT_MEAN: "mean",
    STAT_RANGE: "range",
    STAT_DELTA: "delta",
}
STAT_LABELS = {
    STAT_CURRENT: "the value under the playhead",
    STAT_MIN: "minimum over the visible range",
    STAT_MAX: "maximum over the visible range",
    STAT_MEAN: "mean over the visible range",
    STAT_RANGE: "range (max − min) over the visible range",
    STAT_DELTA: "delta (last − first) over the visible range",
}

# ------------------------------------------------------------------------- the SLOPE FLOOR
# THE NUMBER THIS FILE EXISTS TO REFUSE TO PRINT.
#
# The GPS is 10 Hz — measured, not assumed: on ~/Desktop/D24 the raw per-sample spacing is a median
# of 100.0 ms over 690-700 samples per 69 s lap, and the speed channel's high-frequency residual
# against a 0.5 s moving average is sigma = 0.62 km/h (median over 24 laps; 0.54-0.73). A slope is
# that noise divided by the interval, so it grows as 1/interval as the cursors close up.
#
# MEASURED BY INJECTION, which is the only way to separate the noise from the driving: add
# synthetic noise of exactly that measured sigma to a real lap, recompute the slope, and take the
# RMS displacement as a fraction of the RMS true slope. On the 400-point grid the cursor actually
# reads (GX020060 / GX010062, 8 laps each):
#
#     interval    two-point        least-squares      grid samples in the fit
#       0.1 s    52.0% / 56.6%    52.0% / 56.6%           2   <- one grid step: half of it is noise
#       0.2 s    52.4% / 57.7%    52.4% / 57.7%           2
#       0.3 s    28.1% / 31.6%    28.1% / 31.6%           3
#       0.5 s    20.3% / 22.7%    19.1% / 21.4%           4
#      0.75 s    14.0% / 15.4%    12.5% / 13.6%           6
#       1.0 s    10.6% / 11.6%     8.7% /  9.5%           7
#       1.5 s     7.9% /  8.2%     5.6% /  5.8%          10
#       2.0 s     6.2% /  6.5%     3.9% /  4.2%          13
#
# (On the raw 10 Hz arrays, which have more samples to fit, 1.0 s reads 10.2%/11.3% two-point and
# 7.0%/7.8% least-squares — the same picture.) There is no knee; it is a 1/L decay. So the floor is
# a STATED threshold, not a discovered one: the noise must be under a tenth of the number printed.
# The least-squares fit crosses that at 1.0 s and the two-point difference the obvious
# implementation would use does not — which is why `interval_stats` fits rather than subtracts.
#
# A second, model-free reading agrees. Take the slope, slide the whole interval by ONE raw GPS
# sample, take it again: the two describe the same piece of track, so the gap between them is
# instability (noise plus whatever the track really did in 100 ms). RMS over ~16 500 anchors on 24
# laps, as a fraction of a typical slope: 105.3% at 0.1 s, 32.0% at 0.5 s, 19.4% at 1.0 s, 12.2%
# at 2.0 s. At one GPS sample apart the answer is, literally, 100% noise.
#
# TIME, not x-span: in distance mode the interval is metres, and 30 m is 2.4 s in a hairpin and
# 0.9 s on the straight. The floor is applied to the interval's own elapsed time, which the view
# gets from the same `session.media_time_at_plot_x` conversion the scrub cursor uses — the true
# GPS clock, never a ∫ds/v reconstruction of it.
SLOPE_MIN_INTERVAL_S = 1.0
# ...and the sentence the UI says instead of a number, so the reason travels with the refusal.
SLOPE_FLOOR_NOTE = (
    f"slope needs ≥ {SLOPE_MIN_INTERVAL_S:.1f} s between the cursors. The GPS is 10 Hz and its "
    "speed noise is ±0.62 km/h, so a shorter interval reports mostly that noise — 10% of the "
    "value at 1.0 s, 20% at 0.5 s, and over half of it one sample apart.")


class Interval(NamedTuple):
    """Everything the datum readout says about the stretch between the two cursors.

    `slope` is None exactly when `dt` is under SLOPE_MIN_INTERVAL_S (or unknown) — the readout
    prints SLOPE_FLOOR_NOTE there rather than a confident number."""

    n: int              # samples of the channel inside the closed interval
    x0: float           # the earlier cursor's x, on the shared plot axis
    x1: float           # the later cursor's x
    dt: float | None    # true elapsed seconds between them (None when the view can't convert)
    dd: float | None    # metres travelled between them (None when the view can't convert)
    y0: float           # channel value at x0 (interpolated onto the cursor, not snapped)
    y1: float           # channel value at x1
    diff: float         # y1 − y0
    mean: float
    minimum: float
    maximum: float
    slope: float | None  # channel units PER SECOND, least-squares over the interval; see above


def _bracket(xs: np.ndarray, x0: float, x1: float) -> tuple[int, int]:
    """Index half-open [i0, i1) of the samples strictly inside [x0, x1] — EMPTY when there are none.

    A deep zoom can legitimately put both edges between two samples, and the honest answer there is
    "no samples inside", not the nearest one. This function first clamped to one sample so a caller
    could not divide by zero, which is a hazard neither caller has (both interpolate the two edges
    in and reduce over those at minimum) — and it was a wrong answer, not a safe one: over
    x in [5, 6] on a trace peaking at 100 at x = 10, the clamp dragged that peak inside and reported
    the window's maximum as 100 where the trace only reaches 60."""
    i0 = int(np.searchsorted(xs, x0, side="left"))
    i1 = int(np.searchsorted(xs, x1, side="right"))
    return (i0, i1) if i1 > i0 else (i0, i0)


def interval_stats(xs, ys, xa: float, xb: float, *, dt: float | None = None,
                   dd: float | None = None) -> Interval | None:
    """The datum-cursor readout for channel `ys` over the interval between `xa` and `xb`.

    The cursors are wherever the user dropped them, so the endpoint VALUES are interpolated onto
    them (`np.interp`) and then folded into the min/max/mean alongside the real samples between —
    a max that ignored the endpoints would report a corner's entry speed as lower than the number
    printed one column to its left.

    `dt`/`dd` are the interval's true elapsed time and distance, which only the view can supply
    (they come from the session's own plot-x↔media-time conversions — the same ones the scrub
    cursor uses, i.e. the true GPS clock, never ∫ds/v). `dt` also gates the slope.

    Returns None for an empty channel or a zero-width interval."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    n_all = min(len(xs), len(ys))
    if n_all < 2:
        return None
    xs, ys = xs[:n_all], ys[:n_all]
    x0, x1 = (float(xa), float(xb)) if xa <= xb else (float(xb), float(xa))
    if x1 <= x0:
        return None
    i0, i1 = _bracket(xs, x0, x1)
    y0 = float(np.interp(x0, xs, ys))
    y1 = float(np.interp(x1, xs, ys))
    inner_y = ys[i0:i1]
    ext_y = np.concatenate(([y0], inner_y, [y1]))
    ext_x = np.concatenate(([x0], xs[i0:i1], [x1]))
    # Mean over the interval, not over the samples: a trapezoidal average, so it does not shift
    # when the grid happens to place more samples in one half (and so it matches what the eye
    # reads off the curve). Falls back to the plain mean if the x's collapse.
    width = ext_x[-1] - ext_x[0]
    mean = (float(np.trapezoid(ext_y, ext_x) / width) if width > 0
            else float(np.mean(ext_y)))
    slope = None
    if dt is not None and dt >= SLOPE_MIN_INTERVAL_S:
        # Least squares over every sample in the interval, on a TIME abscissa built by mapping the
        # x's linearly onto [0, dt]. Linear is exact in time mode (x IS time) and is the honest
        # reading in distance mode: it is the interval's mean rate, which is the number the fit
        # would give anyway over a stretch this short. The fit rather than (y1−y0)/dt because the
        # two endpoints alone are twice as noisy — see the sweep at the top of this file.
        tt = (ext_x - ext_x[0]) * (dt / width) if width > 0 else None
        if tt is not None and len(tt) >= 2 and float(np.ptp(tt)) > 0:
            slope = float(np.polyfit(tt, ext_y, 1)[0])
    return Interval(n=int(len(inner_y)), x0=x0, x1=x1, dt=dt, dd=dd, y0=y0, y1=y1,
                    diff=y1 - y0, mean=mean, minimum=float(ext_y.min()),
                    maximum=float(ext_y.max()), slope=slope)


def window_stat(xs, ys, x0: float, x1: float, stat: str,
                at: float | None = None) -> float | None:
    """One statistic of channel `ys` over the VISIBLE x-range [x0, x1].

    `current` needs the playhead's x (`at`) and is the only one that is not a reduction; it is
    here rather than in the caller so the six selector entries all take one road. The window's
    edges are interpolated in, for the same reason `interval_stats` interpolates its cursors: the
    chart's own left edge is a real position on the trace, not the nearest sample to it.

    None when the channel is empty, when the range is degenerate, or when `current` is asked for
    with no playhead."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    n_all = min(len(xs), len(ys))
    if n_all == 0:
        return None
    xs, ys = xs[:n_all], ys[:n_all]
    if stat == STAT_CURRENT:
        if at is None:
            return None
        return float(np.interp(at, xs, ys))
    if n_all < 2 or not (x1 > x0):
        return None
    lo = max(float(x0), float(xs[0]))
    hi = min(float(x1), float(xs[-1]))
    if hi <= lo:
        return None  # the view is panned entirely off this curve
    i0, i1 = _bracket(xs, lo, hi)
    ext_x = np.concatenate(([lo], xs[i0:i1], [hi]))
    ext_y = np.concatenate(([float(np.interp(lo, xs, ys))], ys[i0:i1],
                            [float(np.interp(hi, xs, ys))]))
    if stat == STAT_MIN:
        return float(ext_y.min())
    if stat == STAT_MAX:
        return float(ext_y.max())
    if stat == STAT_RANGE:
        return float(ext_y.max() - ext_y.min())
    if stat == STAT_DELTA:
        return float(ext_y[-1] - ext_y[0])
    if stat == STAT_MEAN:
        width = ext_x[-1] - ext_x[0]
        return (float(np.trapezoid(ext_y, ext_x) / width) if width > 0
                else float(np.mean(ext_y)))
    return None


# ------------------------------------------------------------- steepest delta slope (the tour)
# HOW WIDE IS "LOCAL". MoTeC's own training material says to look for the steepest slope or biggest
# jump in the variance line and zoom in there; it does not say over what window, and the answer is
# not free — the delta is a noisy cumulative signal, so a narrow window finds the noise and a wide
# one finds the whole lap.
#
# TWO CANDIDATE CRITERIA WERE MEASURED AND BOTH FAILED TO DECIDE IT.
#   * STABILITY. The obvious test — does the pick move when the window changes — says nothing:
#     matching the top-5 SET between adjacent widths (a pick counts as matched when the other width
#     put one within a window's width of it) scores 90-96% at EVERY width from 0.5% to 12% of the
#     lap. Tracking only the TOP-1 centre looks unstable at narrow widths (p90 of 250-380 m), but
#     that is near-equal losses swapping rank, not the search finding a different thing; the set is
#     the same set. The window cannot be chosen by stability.
#   * CORNER ALIGNMENT. Checking the picks against the app's own corner partition scores 100% at
#     0.5-3%, 99.1% at 5% and 91.3% at 8% — but the detected corners cover 66.5% of this kart lap
#     and 100.5% of it with a 15 m grace band, so a RANDOM pick would score about the same. The only
#     signal in it is the drop at 8%, where the window is wide enough to straddle a corner and the
#     straight beside it.
#
# What DOES decide it is the delta's own noise floor and the length of a corner. The delta curve's
# high-frequency residual against a 13-sample moving average is sigma = 25.4 ms on GX020060 and
# 9.1 ms on GX010062, so a window's two endpoints alone contribute sqrt(2)*sigma = 36 / 13 ms of
# pure noise to whatever "loss" it reports. Against the median top-1 loss actually found:
#
#     window     metres   seconds    top-1 loss (D24 / 62)     signal:noise
#      0.5%        5.3 m   0.35 s      107 ms /  55 ms          3.0x /  4.3x
#      1%         10.6 m   0.69 s      200 ms / 104 ms          5.6x /  8.1x
#      2%         21.2 m   1.39 s      310 ms / 181 ms          8.6x / 14.1x
#      3%         31.8 m   2.08 s      372 ms / 222 ms         10.3x / 17.3x
#      5%         53.0 m   3.47 s      453 ms / 284 ms         12.6x / 22.1x
#      8%         84.7 m   5.55 s      573 ms / 361 ms         15.9x / 28.2x
#
# 3% is the narrowest window that clears 10x the delta's own noise on the noisier fixture, its
# 2.08 s of driving is twice the 1.0 s slope floor above (the same 10 Hz limit governs both), and
# 31.8 m is just under the SHORTEST corner on the track (35.3 m; the median is 53.3 m = 5.0% of the
# lap) — so the span lands inside one corner instead of spanning two. 5% is a whole median corner
# and 8% measurably starts straddling. A FRACTION rather than a fixed distance so it travels to a
# 3 km circuit, where 3% is 90 m and still ~2.7 s at racing speed.
DEFAULT_LOSS_WINDOW_FRAC = 0.03
# HOW MANY LOSSES ARE WORTH WALKING. Each pick's share of the lap's GROSS loss (the sum of the delta
# curve's positive increments — the time actually given away; the net laptime difference is that
# minus what was won back, which is why the first cut of this measurement produced cumulative
# "shares" over 100%), averaged over the 23 non-best laps at the 3% window:
#     #1 17.2%   #2 12.2%   #3 10.2%   #4 8.5%   #5 7.0%   #6 5.7%   #7 4.4%   #8 3.4%
# cumulatively 17 / 29 / 40 / 48 / 55%. The fifth still carries a seventh of the lap's loss; past
# it each one adds under 6% and the tour turns into a scroll. Five — and the walk WRAPS, so a
# driver can keep pressing rather than hunting for the way back to the worst one.
DEFAULT_LOSS_COUNT = 5
# Two picks must be at least this far apart (as a fraction of the lap) to count as distinct losses.
# Measured at the 3% window over the same 23 laps, counting adjacent picks landing within 40 m of
# each other — i.e. twice inside one braking zone: a 3% separation gave 10 such pairs out of 92,
# and 4% and 5% gave none. 5% is the first round number clear of it.
DEFAULT_LOSS_SEPARATION_FRAC = 0.05


class LossSpan(NamedTuple):
    """One stretch where the lap gave time away against the chart's baseline."""

    lap_id: int
    x0: float        # span start on the shared plot axis
    x1: float        # span end
    centre: float    # where the cursor is parked (and the zoom is centred)
    loss: float      # seconds lost across the span (always > 0)


def steepest_losses(curves, *, window_frac: float = DEFAULT_LOSS_WINDOW_FRAC,
                    count: int = DEFAULT_LOSS_COUNT,
                    separation_frac: float = DEFAULT_LOSS_SEPARATION_FRAC) -> list[LossSpan]:
    """The `count` biggest local time losses across `curves`, worst first.

    `curves` is [(lap_id, xs, ys)] — the delta curves as drawn, in whatever the chart's current
    baseline is. A lap whose delta is IDENTICALLY FLAT contributes nothing: that is the baseline
    lap against itself, and "the biggest place you lost time to yourself" is not a question. (When
    the best lap is the only one drawn the chart is already Δ-to-IDEAL, which is not flat, so the
    gesture keeps working there — it walks the gap to the ideal instead.)

    Greedy: take the highest-rising window, then exclude everything within `separation_frac` of
    its centre ON THE SAME LAP and take the next. Across laps the spans may coincide — two laps
    losing time at the same corner are two findings, not one — but the RANKING is global, so a
    six-lap overlay tours the six worst moments in the frame rather than lap 0's six worst."""
    picked: list[LossSpan] = []
    pool: list[tuple[float, int, float, float, float]] = []  # (loss, lap, x0, x1, centre)
    for lap_id, xs, ys in curves:
        xs = np.asarray(xs, float)
        ys = np.asarray(ys, float)
        n = min(len(xs), len(ys))
        if n < 3:
            continue
        xs, ys = xs[:n], ys[:n]
        if float(np.ptp(ys)) <= 0.0:
            continue  # the baseline against itself: a flat zero line has no steepest slope
        w = max(2, int(round(window_frac * n)))
        if w >= n:
            continue
        i = np.arange(0, n - w)
        gain = ys[i + w] - ys[i]
        sep_x = separation_frac * float(xs[-1] - xs[0])
        chosen: list[float] = []
        for j in i[np.argsort(-gain)]:
            g = float(gain[j])
            if g <= 0.0:
                break
            centre = 0.5 * float(xs[j] + xs[j + w])
            if any(abs(centre - c) < sep_x for c in chosen):
                continue
            chosen.append(centre)
            pool.append((g, int(lap_id), float(xs[j]), float(xs[j + w]), centre))
            if len(chosen) >= count:
                break
    # Global ranking, then the per-lap order is whatever falls out — the worst moment on the chart
    # is the one the first press must land on, whichever lap it belongs to.
    pool.sort(key=lambda e: -e[0])
    for loss, lap_id, x0, x1, centre in pool[:count]:
        picked.append(LossSpan(lap_id=lap_id, x0=x0, x1=x1, centre=centre, loss=loss))
    return picked
