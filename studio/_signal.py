"""Pure-numpy signal/clean helpers shared by the session pipeline and the g-meter.

PACER-FREE BY CONTRACT. The studio architecture rule is that only session.py, load.py,
tracks.py, and ingest.py touch the `pacer` bindings; this module is numpy-only so the boxcar
smoother, the gap/quality cleaners, the real-lap band filter, and the `fmt_time` lap-time
formatter can be shared without dragging a pacer (or Qt) import anywhere. Every function here
is a behaviour-identical extraction of code that previously lived inline in session.py /
gmeter.py — names and signatures match the originals so call sites are unchanged.
"""
from __future__ import annotations

import math

import numpy as np

# --- GPS denoising (originally derived from the upstream interpolation/noise notebooks) ---
SMOOTH_WINDOW = 13  # boxcar width in samples; 1 disables smoothing
SMOOTH_GAP_S = 1.0  # s — a jump larger than ~10x the 10 Hz period starts a new smoothing run

# --- GPS quality gating (uses the GPS9 DOP / fix fields exposed by the C++ core) ---
MIN_FIX = 3  # GPS9 fix: 0=none, 2=2D, 3=3D. Require a 3D lock when the field is present.
MAX_DOP = 10.0  # GPS9 DOP: dilution of precision; >~10 is a poor-geometry fix. Generous.

# --- "real lap" band filter ---
MIN_LAP_TIME = 5.0  # s — laps shorter than this are partial/phantom, not real laps
MIN_LAP_SAMPLES = 20  # a real lap has at least this many GPS samples
LAP_BAND_LO, LAP_BAND_HI = 0.5, 1.6  # "real lap" = lap_time within [lo, hi] x median lap time
# A second, tighter band on lap DISTANCE. On a fixed circuit every real lap covers a
# near-constant distance (it's the same loop), so lap distance clusters far tighter than lap
# TIME — a real lap varies only by racing-line / GPS noise (~a few %), while a mis-segmented
# short/long lap (an extra/missed start-line crossing) is off by 10%+. That phantom can defeat
# the time band (a 0.87x-median lap sits inside [0.5, 1.6]) yet be crowned 'best', which then
# poisons best_lap_id / theoretical_best / the Δ-to-best baseline / the coaching Opportunities /
# the current-lap map. ±10% of the median lap distance comfortably keeps every real lap while
# rejecting such a phantom. Only applied when real per-lap distances are available (see
# `_band_lap_ids`); the time band is unchanged.
LAP_DIST_BAND_LO, LAP_DIST_BAND_HI = 0.90, 1.10

# --- A LAP THAT STOPPED IS NOT A SLOW LAP -------------------------------------------------------
# The time band above is wide on purpose (it has to fit any track length from one median), and
# WIDE IS THE WHOLE PROBLEM at its top end: 1.6x a ~69 s kart lap is ~110 s, so a lap carrying a
# stop of up to **41.5 s** (measured: 41.53 s on the D24 0060 pair, 41.73 s on 0062) reads "valid
# and clean" and flows into the median, the best lap, the ideal lap, pace σ and every stint.
#
# TIGHTENING THE BAND IS NOT THE FIX, AND THE OWNER'S OWN RECORDINGS SAY SO. Sweeping LAP_BAND_HI
# down over both fixtures: 1.15 and above keeps all 38 / 65 laps, and **1.10 drops laps 20 and 37
# of 0060** — real laps at 1.110x and 1.114x the median, the ordinary slow laps of a session. (The
# two recordings disagree about how much room is needed, which is the point: 0062's 65 laps all sit
# under 1.047x and survive a bound of 1.05, so one fixture alone would have "proved" a tight band
# safe.) To bound a stop at even ~7 s you would need HI ~= 1.10, i.e. you would pay real laps for
# it. And the bound that costs nothing has no margin either: the slowest lap is 1.1144x, so 1.15
# clears it by 3.1 % — nothing, on a rule that has to hold for tracks nobody here has driven.
#
# So the stop is tested DIRECTLY, off the speed trace, because that is what it actually is: a
# stationary stretch, not a large total. A slow lap and a lap with a stop are different events and
# the band conflated them. The two numbers, both measured rather than chosen:
#
#   * STOPPED_KMH — the SLOWEST single GPS fix on any valid lap is 13.644 km/h (0060) and
#     25.344 km/h (0062); the median lap's own minimum is 27.1 and 31.5 km/h. 10 km/h sits 36 %
#     below the slowest real fix on either recording. It is deliberately NOT driving.MOVING_KMH
#     (14.4) — a real apex on 0060 dips under that for 0.101 s, so that constant means "not
#     accelerating meaningfully", not "stopped".
#   * MAX_STOPPED_S — the longest CONTIGUOUS stretch below 10 km/h anywhere in the 103 valid laps
#     of both recordings is **0.000 s**. Below 20 km/h it is 0.6 s, below 25 km/h 3.7 s. So 3 s
#     under 10 km/h cannot happen while racing (it is 8 m of travel), and on real data this rule
#     excludes NOTHING: the valid set, the golden fingerprint and every derived statistic are
#     unmoved. It is a guard against an absurdity, not a filter with an opinion.
#
# A lap it does reject is SUBSTANTIAL, so it surfaces in the ⊘ EXCLUDED strip via
# `_banded_out_lap_ids` — shown and explained, never silently dropped.
STOPPED_KMH = 10.0    # km/h; below this the kart is not running the lap, it is stopped/crawling
MAX_STOPPED_S = 3.0   # s; a contiguous stretch this long inside a lap means the lap contains a STOP

# --- longitudinal g from the speed trace (shared by driving channels + the g-meter dial) ---
G = 9.80665  # m/s^2 (standard gravity)
MAX_LONG_G = 2.0  # clip d|v|/dt spikes: a GPS glitch can't manufacture a real brake


# --- display conventions shared by the Qt views and the Qt-free export writers ---
#: The "no signal" value — an em-dash, never a fake 0. `widgets.DASH` re-exports it for the views;
#: it lives here so the exported report/clipboard summary print the SAME mark for the same absent
#: accessor. `fmt_time` below already returns it for a non-finite input.
DASH = "—"


def fmt_time(seconds: float) -> str:
    """`m:ss.mmm` lap/split-time formatting (em-dash for a non-finite input). Lives here —
    the pacer-free numpy-helpers module — so the views (plots_view/lap_table/
    compare_controller) can format times without importing session.py, which would drag the
    compiled `pacer` module in transitively. Moved verbatim from session.py (which keeps a
    compatibility re-export)."""
    if not math.isfinite(seconds):
        return "—"
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"


def fmt_hms(seconds: float) -> str:
    """A DURATION as `m:ss`, or `h:mm:ss` from an hour up — session totals span both.

    The lap-time twin of `fmt_time` (which is `m:ss.mmm`, right for a lap and wrong for a
    28-minute recording), and it lives here for the same reason: the Stats page and the exported
    session report both print the recording's "recorded"/"moving" totals off the SAME
    `SessionStats.totals()` floats, so they must round and punctuate them the same way or the
    exported document quietly disagrees with the screen it was exported from. Moved verbatim from
    stats_panel's private `_fmt_hms` when export_data (Qt-free by contract, so it cannot import
    stats_panel) became the second caller."""
    s = max(int(round(seconds)), 0)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}:{m:02d}:{sec:02d}" if h else f"{m}:{sec:02d}"


def plural(n: int, noun: str) -> str:
    """"1 corner" / "7 corners" — the one-line count helper the ideal-lap disclosure needs on
    three nouns whose smallest legal value is 1 (a layout where the detector finds ONE corner
    still builds a 3-segment partition and can still stitch a genuine ideal). Shared from here
    because that disclosure is now printed by a Qt view AND by a Qt-free export writer; the
    private copies in stats_panel / library_dialog delegate to it."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def lap_label(lap_id: int) -> str:
    """The 1-based lap NUMBER for display (`str(lap_id + 1)`). Lap ids are 0-based internally
    (indices, sort keys, sector-split column keys — all left untouched), but racers and every
    lap-timing tool count from 1, so every USER-FACING lap number is rendered through this one
    helper. Lives here — the pacer-free/Qt-free helpers module already imported by lap_table /
    plots_view / coaching_panel — so all three share one definition with no import cycle. Only a
    lap ID gets +1; COUNTS/QUANTITIES (e.g. "median of 13 clean laps") are not lap ids and must
    not pass through here."""
    return str(int(lap_id) + 1)


def _boxcar_core(a, w):
    """The edge-corrected boxcar moving average itself, given a float array `a` and a window
    `w` already known to be valid (2 <= w <= len(a)). Normalised at the ends so the first/last
    w//2 points aren't dragged toward zero by the convolution's implicit zero-padding (a raw
    `"same"` boxcar tapers the edges; here those points are averaged over only the samples that
    actually exist). The single shared implementation behind _signal._smooth and gmeter._boxcar."""
    kernel = np.ones(w)
    num = np.convolve(a, kernel, "same")          # windowed sum
    den = np.convolve(np.ones(len(a)), kernel, "same")  # count of real samples in each window
    return num / den


def _smooth(a, w: int = SMOOTH_WINDOW):
    """Edge-correct boxcar moving average — the upstream notebook's `np.convolve(a, ones(w)/w, "same")`
    in the interior, but normalized at the ends so the first/last w//2 points aren't dragged
    toward zero by the convolution's implicit zero-padding (a raw `"same"` boxcar tapers the
    edges; here those points are averaged over only the samples that actually exist).

    A no-op for w<2 or arrays shorter than the window. Applied to the GPS track coordinates
    (lat/lon/alt) once at load — never per frame.
    """
    a = np.asarray(a, float)
    if w < 2 or len(a) < w:
        return a
    return _boxcar_core(a, w)


def boxcar(a, w):
    """Edge-corrected boxcar moving average (shared by driving + gmeter). No-op for w<2 or
    arrays shorter than 2; clamps the window to the array length. See `_boxcar_core`."""
    a = np.asarray(a, float)
    if w < 2 or len(a) < 2:
        return a
    return _boxcar_core(a, min(w, len(a)))


def speed_long_g(speed_kmh, t) -> np.ndarray:
    """Longitudinal g from the speed trace: clip((d|v|/dt)/G) — positive accelerating, negative
    braking. The clean, GPS-validated brake signal (the IMU forward axis is vibration-dominated,
    ~1.5x inflated). Spikes are clipped to +/-MAX_LONG_G; a length mismatch between `speed_kmh`
    and `t` uses the common prefix; <3 samples -> zeros. Single source for studio.driving and
    studio.gmeter (the latter kept a private copy only to dodge a cross-import — this pacer-free
    numpy module is the home both already depend on).

    **UNSMOOTHED, AND THE GRID `t` IS PART OF THE ANSWER.** This returns the bare derivative; every
    window in the app is applied by a CALLER, and they do not agree — so the same function name
    reaches the user as three different series with three different peaks. `gmeter.compute` puts it
    on the 50 Hz output grid and boxcars it over LONG_SMOOTH_S (0.35 s) into `long_g_gps`, which is
    what the dial, the g-g cloud and the "peak braking g" tile read. `driving_channels` calls it
    twice more WITHOUT a window: once on that same 50 Hz grid for `derive_thresholds`, and once per
    lap on the lap's native ~10 Hz grid for the brake / coast / pedal-intensity detectors. Those two
    are not the same signal either — interpolating a 10 Hz speed to 50 Hz and THEN differentiating
    manufactures content the fixes cannot carry (measured on the D24 0060 pair: RMS 0.353 g vs
    0.302 g for differentiate-at-10-Hz-then-interpolate, and the +/-MAX_LONG_G clip fires 81 times
    against 1). The native 10 Hz derivative is the honest one: 99 % of its power sits below 3.7 Hz
    (4.0 on 0062), inside the 5 Hz Nyquist of the fixes it is made of. See the THREE SERIES block in
    studio/driving_channels.py for what each one is read as, and by whom."""
    v = np.asarray(speed_kmh, float) / 3.6
    t = np.asarray(t, float)
    n = min(len(v), len(t))
    if n < 3:
        return np.zeros(n)
    tt = t[:n]
    dt = np.diff(tt)
    if (dt <= 0).any():
        # A run/chapter seam clamps the time axis monotonic (load._gps9_times -> maximum.accumulate),
        # leaving a duplicated instant (dt == 0) that np.gradient would divide by -> NaN across the
        # longitudinal g, silently dropping brake/coast events near the seam (the sibling GPS path in
        # gmeter.py already guards this). Rebuild a strictly-increasing axis, each non-positive gap
        # replaced by the median positive gap. A no-op on clean strictly-increasing input.
        pos = dt[dt > 0]
        med = np.median(pos) if pos.size else 1.0
        dt = np.where(dt <= 0, med, dt)
        tt = np.concatenate([tt[:1], tt[:1] + np.cumsum(dt)])
    g = np.gradient(v[:n], tt) / G
    return np.clip(g, -MAX_LONG_G, MAX_LONG_G)


def _smooth_segments(a, seg_bounds, w: int = SMOOTH_WINDOW):
    """Apply `_smooth` independently within each contiguous run [lo, hi) so a boxcar never
    averages across a time discontinuity (chaptered files / GPS dropouts)."""
    a = np.asarray(a, float)
    if w < 2:
        return a
    out = a.copy()
    for lo, hi in seg_bounds:
        if hi - lo >= 2:
            out[lo:hi] = _smooth(a[lo:hi], w)
    return out


def _gap_segments(times, gap_s: float = SMOOTH_GAP_S):
    """Contiguous runs [lo, hi) of `times` with no inter-sample gap larger than `gap_s`. Used
    so the moving average never bridges a chapter break / GPS dropout."""
    t = np.asarray(times, float)
    n = len(t)
    if n == 0:
        return []
    breaks = np.where(np.diff(t) > gap_s)[0] + 1
    edges = [0, *breaks.tolist(), n]
    return [(edges[k], edges[k + 1]) for k in range(len(edges) - 1)]


def _quality_ok(s) -> bool:
    """True if a GPS sample's quality fields don't mark it as bad. Treats unknown/sentinel
    quality (fix<0, or a non-positive/non-finite DOP — e.g. the GPS5 stream, which carries
    neither) as "keep": we reject ONLY when the core actually reports a poor fix. `dop`/`fix`
    come from the GPS9 stream (C++ core); sentinels are fix=-1 and dop=-1.0."""
    # Reject non-finite position/speed (garbage / truncated GPMF) up front: a NaN/inf lat,
    # lon, or speed would otherwise poison the cleaner's percentile/distance math and flow
    # into the C++ geometry as NaN line / segmentation.
    if not (math.isfinite(s.lat) and math.isfinite(s.lon) and math.isfinite(s.full_speed)):
        return False
    fix = getattr(s, "fix", -1)
    dop = getattr(s, "dop", -1.0)
    if fix is not None and 0 <= fix < MIN_FIX:  # known, but no 3D lock
        return False
    # A known, positive, finite DOP above the threshold is poor geometry; anything else is kept.
    if isinstance(dop, (int, float)) and math.isfinite(dop) and dop > 0 and dop > MAX_DOP:
        return False
    return True


def _gate_quality(samples, spans, naive, moving_speed: float = 0.0):
    """Drop low-quality fixes (no 3D lock / high DOP) using the GPS9 quality fields. Conservative
    — sentinels (unknown quality) are kept. Returns
    ``(samples, spans, naive, dropped, moving_dropped_fraction)`` where:

      * ``dropped`` is the rejected-fix COUNT (still logged), and
      * ``moving_dropped_fraction`` is the fraction of the MOVING trace the gate rejected — the
        share of fixes with ``full_speed > moving_speed`` that were dropped, over the count of
        such moving fixes. This is what the data-quality signal reads: a recording's GPS quality
        is judged on the fixes it took WHILE DRIVING, not on the stationary GPS-acquisition
        lead-in (which the pipeline trims anyway). Low-quality fixes cluster in that warm-up, so
        dividing the fixed lead-in drop count by the RAW total flags a clean recording as
        "degraded" purely on how it was opened (see the D24 chapter-1-vs-all-3 artifact). A
        recording that genuinely drops many fixes DURING MOTION still flags. ``moving_speed=0``
        (the default) keeps the fraction over every fix — the historical behaviour."""
    keep = [i for i, s in enumerate(samples) if _quality_ok(s)]
    dropped = len(samples) - len(keep)
    if dropped:
        pct = 100.0 * dropped / max(len(samples), 1)
        print(f"studio: quality gate dropped {dropped}/{len(samples)} fixes ({pct:.1f}%) "
              f"(fix<{MIN_FIX} or dop>{MAX_DOP})", flush=True)
    # Judge GPS quality over the MOVING trace only (exclude the stationary lead-in from BOTH the
    # dropped numerator and the denominator). A dropped MOVING fix = rejected AND full_speed above
    # the threshold; the finite-position guard in _quality_ok means a NaN-speed fix can't count as
    # moving. moving_speed=0 reduces to dropped/len(samples).
    kept = set(keep)
    moving = [i for i, s in enumerate(samples)
              if math.isfinite(s.full_speed) and s.full_speed > moving_speed]
    n_moving = len(moving)
    moving_dropped = sum(1 for i in moving if i not in kept)
    moving_dropped_fraction = moving_dropped / n_moving if n_moving else 0.0
    return ([samples[i] for i in keep], [spans[i] for i in keep], [naive[i] for i in keep],
            dropped, moving_dropped_fraction)


def _longest_stopped_s(times, speed_mps, stopped_kmh: float = STOPPED_KMH) -> float:
    """The longest CONTIGUOUS stretch (seconds) a trace spends below `stopped_kmh`.

    CONTIGUOUS, not total: many brief dips are a tight circuit, one long dip is a stop, and only
    the second one makes a lap's time meaningless. Measured the way `driving.coasting_spans`
    measures a span — `times[end] - times[start]` over the run — so a stop that straddles a GPS
    dropout still reports the wall-clock time the car was slow, not the sample count. Aligned to
    the shorter of the two inputs; <2 samples, or nothing below the threshold, -> 0.0."""
    t = np.asarray(times, float)
    v = np.asarray(speed_mps, float) * 3.6
    n = min(len(t), len(v))
    if n < 2:
        return 0.0
    t, v = t[:n], v[:n]
    slow = v < stopped_kmh
    if not slow.any():
        return 0.0
    # Run boundaries from the edges of the boolean mask (no Python loop over the samples). The
    # zero-padding is what makes a run touching either end of the lap a run like any other.
    edges = np.diff(np.concatenate([[0], slow.astype(np.int8), [0]]))
    starts = np.flatnonzero(edges == 1)
    ends = np.flatnonzero(edges == -1) - 1  # inclusive last index of each run
    return float(np.max(t[ends] - t[starts]))


def _band_lap_ids(laps) -> list[int]:
    """The ids of laps that qualify as 'real laps': enough samples (>= MIN_LAP_SAMPLES) and a
    long-enough time (>= MIN_LAP_TIME), a lap time within [LAP_BAND_LO, LAP_BAND_HI] x the
    MEDIAN lap time, a lap distance within [LAP_DIST_BAND_LO, LAP_DIST_BAND_HI] x the
    median lap distance, AND no stationary stretch of MAX_STOPPED_S or more inside it. A fixed
    threshold is too crude (short double-crossings of the start line pass it and pollute the
    'best' lap); the time band adapts to any track length, the tighter distance band catches a
    mis-segmented short/long lap that defeats the time band — on a fixed circuit lap distance
    clusters far tighter than lap time — and the stop test catches the one thing NEITHER band can
    see: a lap driven normally that happens to contain a stop (see the band consts; a stop adds
    time without adding distance, so it passes the ±10 % distance band by construction).

    `laps` is the bound `pacer.Laps` object, but this function only calls its read accessors
    (laps_count / lap_time / sample_count / get_lap_distance / lap_columns) — it imports no pacer
    itself, so it stays pure. The distance and speed accessors are both guarded with getattr so
    the FAKE `laps` doubles in the tests (which expose only the time surface) fall back to the
    unchanged earlier result. The single source for Session.valid_lap_ids and
    session._band_lap_count."""
    basic = [(i, laps.lap_time(i)) for i in range(laps.laps_count())
             if laps.sample_count(i) >= MIN_LAP_SAMPLES and laps.lap_time(i) >= MIN_LAP_TIME]
    if not basic:
        return []
    med = float(np.median([t for _, t in basic]))
    lo, hi = LAP_BAND_LO * med, LAP_BAND_HI * med
    timed = [i for i, t in basic if lo <= t <= hi]

    # Distance band: reject a lap whose distance is off the median even though its TIME passed
    # (a mis-segmented short/long lap). Only when real per-lap distances exist — the fake test
    # doubles have no get_lap_distance, and a stream that reports no finite/positive distance
    # gives no median to band against, so both fall back to the time-only result unchanged.
    get_dist = getattr(laps, "get_lap_distance", None)
    if get_dist is None or not timed:
        banded = timed
    else:
        dists = {i: float(get_dist(i)) for i in timed}
        finite = [d for d in dists.values() if math.isfinite(d) and d > 0]
        if not finite:
            banded = timed
        else:
            med_d = float(np.median(finite))
            lo_d, hi_d = LAP_DIST_BAND_LO * med_d, LAP_DIST_BAND_HI * med_d
            banded = [i for i in timed
                      if math.isfinite(dists[i]) and dists[i] > 0 and lo_d <= dists[i] <= hi_d]

    # Stop test: a lap that STOOD STILL is not a slow lap (see MAX_STOPPED_S). Runs last, over the
    # already-banded laps only, so it costs one `lap_columns` crossing per surviving lap and never
    # touches a lap the bands have settled — 3.8 ms for all 66 laps of the 0062 recording, against
    # a load that takes seconds, and `load._fit_start_line` calls this filter up to five times per
    # load. Same getattr discipline as the distance band — a `laps` double without the speed
    # surface falls back to the banded result unchanged.
    get_cols = getattr(laps, "lap_columns", None)
    if get_cols is None or not banded:
        return banded
    kept = []
    for i in banded:
        cols = get_cols(i)
        if _longest_stopped_s(cols.times, cols.full_speed) < MAX_STOPPED_S:
            kept.append(i)
    return kept


def _banded_out_lap_ids(laps) -> list[int]:
    """The ids of SUBSTANTIAL laps the band filter REJECTED: laps that clear the coarse gate
    (>= MIN_LAP_SAMPLES samples and >= MIN_LAP_TIME seconds — so they look like a lap the driver
    actually ran, not a brief start/end sliver) but fell outside the median TIME or DISTANCE band
    in `_band_lap_ids`, or carried a stop of MAX_STOPPED_S or more — a mis-segmented short/long
    lap, an out-lap, an in-lap, or a lap the driver stopped on.

    Returned so the UI can SHOW that a real-looking lap was left out of the times / bests instead
    of silently dropping it (the `_band_lap_ids` filter removes such a lap so it can't be crowned
    'best' and poison the analysis — but the driver still ran it and may wonder where it went).
    Reuses `_band_lap_ids` unchanged (so `valid_lap_ids` stays byte-identical), touching only the
    same read accessors — it imports no pacer and stays pure. The single source for
    Session.excluded_lap_ids."""
    substantial = [i for i in range(laps.laps_count())
                   if laps.sample_count(i) >= MIN_LAP_SAMPLES
                   and laps.lap_time(i) >= MIN_LAP_TIME]
    valid = set(_band_lap_ids(laps))
    return [i for i in substantial if i not in valid]
