"""Pure-numpy signal/clean helpers shared by the session pipeline and the g-meter.

PACER-FREE BY CONTRACT. The studio architecture rule is that only session.py and
tracks.py touch the `pacer` bindings; this module is numpy-only so the boxcar smoother,
the gap/quality cleaners, and the real-lap band filter can be shared without dragging a
pacer (or Qt) import anywhere. Every function here is a behaviour-identical extraction of
code that previously lived inline in session.py / gmeter.py — names and signatures match
the originals so call sites are unchanged.
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


def _boxcar_core(a, w):
    """The edge-corrected boxcar moving average itself, given a float array `a` and a window
    `w` already known to be valid (2 <= w <= len(a)). Normalised at the ends so the first/last
    w//2 points aren't dragged toward zero by the convolution's implicit zero-padding (a raw
    `"same"` boxcar tapers the edges; here those points are averaged over only the samples that
    actually exist). The single shared implementation behind session._smooth and gmeter._boxcar."""
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
    fix = getattr(s, "fix", -1)
    dop = getattr(s, "dop", -1.0)
    if fix is not None and 0 <= fix < MIN_FIX:  # known, but no 3D lock
        return False
    # A known, positive, finite DOP above the threshold is poor geometry; anything else is kept.
    if isinstance(dop, (int, float)) and math.isfinite(dop) and dop > 0 and dop > MAX_DOP:
        return False
    return True


def _gate_quality(samples, spans, naive):
    """Drop low-quality fixes (no 3D lock / high DOP) using the GPS9 quality fields. Reports
    the count dropped. Conservative — sentinels (unknown quality) are kept."""
    keep = [i for i, s in enumerate(samples) if _quality_ok(s)]
    dropped = len(samples) - len(keep)
    if dropped:
        pct = 100.0 * dropped / max(len(samples), 1)
        print(f"studio: quality gate dropped {dropped}/{len(samples)} fixes ({pct:.1f}%) "
              f"(fix<{MIN_FIX} or dop>{MAX_DOP})", flush=True)
    return [samples[i] for i in keep], [spans[i] for i in keep], [naive[i] for i in keep]


def _band_lap_ids(laps) -> list[int]:
    """The ids of laps that qualify as 'real laps': enough samples (>= MIN_LAP_SAMPLES) and a
    long-enough time (>= MIN_LAP_TIME), AND a lap time within [LAP_BAND_LO, LAP_BAND_HI] x the
    MEDIAN lap time. A fixed threshold is too crude (short double-crossings of the start line
    pass it and pollute the 'best' lap); the band adapts to any track length.

    `laps` is the bound `pacer.Laps` object, but this function only calls its read accessors
    (laps_count / lap_time / sample_count) — it imports no pacer itself, so it stays pure.
    The single source for Session.valid_lap_ids and session._band_lap_count."""
    basic = [(i, laps.lap_time(i)) for i in range(laps.laps_count())
             if laps.sample_count(i) >= MIN_LAP_SAMPLES and laps.lap_time(i) >= MIN_LAP_TIME]
    if not basic:
        return []
    med = float(np.median([t for _, t in basic]))
    lo, hi = LAP_BAND_LO * med, LAP_BAND_HI * med
    return [i for i, t in basic if lo <= t <= hi]
