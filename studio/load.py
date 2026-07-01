"""The Session.load pipeline: raw GPMF streams -> a cleaned, GPS9-true-clock-timed, smoothed,
segmented `pacer.Laps` + its coordinate frame.

One of the few pacer-touching modules (see AGENTS.md); numpy signal helpers live in
studio/_signal.py.
"""

from __future__ import annotations

import math

import numpy as np

import pacer

from . import chapters, data_quality, tracks

# numpy-only signal/clean helpers live in studio/_signal.py (shared with gmeter).
from ._signal import (
    SMOOTH_WINDOW,
    _band_lap_ids,
    _gap_segments,
    _gate_quality,
    _smooth_segments,
)
from .ingest import read_recording  # the single-pass GPS+IMU load reader (pacer IO layer)

# Data-cleaning thresholds (see studio/dev/diagnose.py; validated on real sessions).
MIN_START_SPEED = 3.0  # m/s — below this the car is stationary / GPS not yet locked
SPIKE_STEP = 50.0  # m — a lone fix farther than this from BOTH neighbours is a glitch
OFF_TRACK_MARGIN = 0.5  # drop points outside the inlier bbox (1-99 pct) expanded by this fraction
START_WIDEN = 3.0  # widen the auto start line so every lap pass crosses it
#
# Smooth the GPS track ONCE at load (see _smooth_track) so every C++-derived quantity uses the
# same coords. SMOOTH_WINDOW=13 (~1.3 s @ 10 Hz) verified in studio/dev/denoise_check.py.


# --- True-clock timing from the GPS9 per-sample timestamps -------------------------------
# The media clock runs ~0.1% fast, which compresses every lap; the GPS9 stream carries the true
# GPS fix time at a clean 10 Hz. We don't trust the GPS9 absolute epoch (it jumps at chapter
# boundaries / is UTC), so we use only its per-sample SPACING, re-anchored per contiguous run to
# that run's media (naive) start — the axis stays on the global media clock (video sync unchanged)
# but the within-run spacing is the true 10 Hz GPS spacing.
GPS9_MIN_DT_S = 0.02    # an inter-sample GPS9 delta below this is a duplicate/garbage fix
GPS9_MAX_DT_S = 0.40    # …above this, the run is broken (chapter break / dropout / rollover)


def _gps9_times(samples, naive, rate_factor: float = 1.0):
    """Per-sample times = GPS9 spacing re-anchored per contiguous run to the naive media clock.
    Falls back to the naive time for a sentinel (ts==0) or run-break sample, so a GPS5-only
    stream degrades gracefully. Returns a list aligned to `samples`, monotonic non-decreasing.

    `rate_factor` (default 1.0) scales only the within-run spacing (each run stays anchored at
    its media start); the load path leaves it at 1.0 — it exists only for the validator."""
    n = len(samples)
    if n == 0:
        return []
    ts = np.array([getattr(s, "timestamp_ms", 0) for s in samples], dtype=np.float64)
    naive = np.asarray(naive, float)
    out = naive.copy()
    have = ts > 0  # GPS5 / sentinel samples report 0 — keep their naive time
    i = 0
    while i < n:
        if not have[i]:
            i += 1
            continue
        # Extend a contiguous run while the GPS9 delta is a sane single-sample step.
        j = i
        while (j + 1 < n and have[j + 1]
               and GPS9_MIN_DT_S <= (ts[j + 1] - ts[j]) / 1000.0 <= GPS9_MAX_DT_S):
            j += 1
        if j > i:  # a real run [i, j]: anchor at its naive start, add GPS9 spacing (rate=1.0)
            base_naive = naive[i]
            base_ts = ts[i]
            out[i:j + 1] = base_naive + rate_factor * (ts[i:j + 1] - base_ts) / 1000.0
        i = j + 1
    # defensive monotonicity guard at run seams
    return list(np.maximum.accumulate(out))


def _used_gps9_trueclock(samples) -> bool:
    """Did the GPS9 true-clock axis actually get built, or did the load fall back to the media
    clock? True iff at least one contiguous GPS9 run exists — the SAME run rule `_gps9_times`
    uses to re-anchor spacing (two consecutive non-sentinel fixes whose delta is a sane single
    GPS9 step). A GPS5-only camera reports ts==0 on every sample, so no run is found → False →
    every time stayed on the naive media clock. The recording's timing-quality clock provenance
    (data_quality.TimingQuality.clock) is read off this."""
    ts = np.array([getattr(s, "timestamp_ms", 0) for s in samples], dtype=np.float64)
    have = ts > 0
    for i in range(len(ts) - 1):
        if (have[i] and have[i + 1]
                and GPS9_MIN_DT_S <= (ts[i + 1] - ts[i]) / 1000.0 <= GPS9_MAX_DT_S):
            return True
    return False


def _sustained_moving(samples, lo, hi, run=5):
    """First index in [lo,hi) where the car is moving for `run` consecutive samples.

    Last candidate is hi - run (the window samples[i..i+run-1] fits iff i <= hi - run), hence the
    range bound hi - run + 1 — a plain `hi - run` skips that trailing window."""
    for i in range(lo, hi - run + 1):
        if all(samples[i + k].full_speed > MIN_START_SPEED for k in range(run)):
            return i
    return lo


def _widen(seg, factor):
    """Scale a pacer.Segment about its midpoint (a longer timing line)."""
    mx = (seg.first.x + seg.second.x) / 2
    my = (seg.first.y + seg.second.y) / 2
    return tracks.make_segment(
        mx + (seg.first.x - mx) * factor, my + (seg.first.y - my) * factor,
        mx + (seg.second.x - mx) * factor, my + (seg.second.y - my) * factor,
    )


def _band_lap_count(laps) -> int:
    """Count band-around-median 'real' laps (same gate as Session.valid_lap_ids), usable
    pre-Session."""
    return len(_band_lap_ids(laps))


def _fit_start_line(laps, base):
    """Choose the start/finish line for a known track: prefer the exact track line; if a wider
    line (`_widen` scales about the midpoint) recovers more band-laps a short segment missed,
    take the smallest such factor. Capped below where the longer line over-segments. Sets
    `laps.sectors`; returns the chosen Segment."""
    laps.sectors = pacer.Sectors(start_line=base, sector_lines=[])
    laps.update()
    base_n = _band_lap_count(laps)
    best_seg = base
    # Smallest-first: take the first factor that recovers a band lap the short segment missed.
    for factor in (1.15, 1.3, 1.5):
        seg = _widen(base, factor)
        laps.sectors = pacer.Sectors(start_line=seg, sector_lines=[])
        laps.update()
        if _band_lap_count(laps) > base_n:
            best_seg = seg
            break
    laps.sectors = pacer.Sectors(start_line=best_seg, sector_lines=[])
    laps.update()
    return best_seg


# Half-length (m each side of the racing line) of the heuristic unknown-track start line, and the
# number of samples each side used to estimate the local heading at the peak. _fit_start_line widens
# the line further if a pass misses it; ~a kart straight's width so the perpendicular line crosses
# the racing line once per lap.
_HEURISTIC_HALF_M = 15.0
_HEURISTIC_HEADING_SAMPLES = 5


def _heuristic_start_base(xs, ys, speeds):
    """A SENSIBLE unknown-track start/finish line (vs an arbitrary point): a `pacer.Segment`
    perpendicular to the direction of travel at the PEAK-SPEED point — the main straight, which the
    car crosses once per lap and where a timing line reads cleanly (no mid-corner ambiguity). All
    coordinates are LOCAL metres (match `tracks.make_segment` / `cs.local`). Returns None when the
    geometry is degenerate (too few samples / no local heading) so the caller falls back to
    `laps.pick_random_start()`. NOTE: the timing stays PROVISIONAL until the user confirms the line
    (see Session.timing_verified) — this is a better DEFAULT placement, not a trusted fix."""
    n = len(speeds)
    if n < 2 * _HEURISTIC_HEADING_SAMPLES + 1:
        return None
    i = int(np.argmax(speeds))
    k = _HEURISTIC_HEADING_SAMPLES
    a, b = max(0, i - k), min(n - 1, i + k)
    dx, dy = float(xs[b] - xs[a]), float(ys[b] - ys[a])
    heading = math.hypot(dx, dy)
    if heading < 1e-6:
        return None
    ux, uy = dx / heading, dy / heading   # unit direction of travel at the peak
    px, py = -uy, ux                      # the perpendicular — the timing line's direction
    cx, cy = float(xs[i]), float(ys[i])
    return tracks.make_segment(cx - px * _HEURISTIC_HALF_M, cy - py * _HEURISTIC_HALF_M,
                               cx + px * _HEURISTIC_HALF_M, cy + py * _HEURISTIC_HALF_M)


def _clean(samples, spans, naive):
    """Trim the stationary lead-in/cool-down (where GPS spikes cluster), then drop lone
    teleport glitches (a fix far from BOTH neighbours while they stay close to each other).
    Returns cleaned (samples, spans, naive). See studio/dev/diagnose.py for the evidence."""
    n = len(samples)
    if n < 10:
        return samples, spans, naive
    lo = _sustained_moving(samples, 0, n)
    hi = n
    while hi > lo + 1 and samples[hi - 1].full_speed <= MIN_START_SPEED:
        hi -= 1
    if hi - lo < 10:  # degenerate (mostly stationary clip) — keep everything
        lo, hi = 0, n

    s, sp, t = samples[lo:hi], spans[lo:hi], naive[lo:hi]
    cs = pacer.CoordinateSystem(s[len(s) // 2])
    xy = []
    for x in s:
        v = cs.local(x)
        xy.append((v[0], v[1]))
    keep = [True] * len(s)
    for i in range(1, len(s) - 1):
        if (math.dist(xy[i], xy[i - 1]) > SPIKE_STEP
                and math.dist(xy[i], xy[i + 1]) > SPIKE_STEP
                and math.dist(xy[i - 1], xy[i + 1]) < SPIKE_STEP):
            keep[i] = False

    # Drop off-track fixes: keep the 1-99 pct bbox widened by OFF_TRACK_MARGIN.
    xs = np.array([p[0] for p in xy])
    ys = np.array([p[1] for p in xy])
    x_lo, x_hi = np.percentile(xs, [1, 99])
    y_lo, y_hi = np.percentile(ys, [1, 99])
    margin = max(x_hi - x_lo, y_hi - y_lo, 1.0) * OFF_TRACK_MARGIN
    in_box = ((xs >= x_lo - margin) & (xs <= x_hi + margin)
              & (ys >= y_lo - margin) & (ys <= y_hi + margin))

    idx = [i for i in range(len(s)) if keep[i] and bool(in_box[i])]
    return [s[i] for i in idx], [sp[i] for i in idx], [t[i] for i in idx]


def _smooth_track(samples, times, w: int = SMOOTH_WINDOW):
    """Return NEW GPSSamples with lat/lon/altitude boxcar-smoothed (speeds untouched) so all
    C++ geometry uses the same track. Smoothed per gap-free run (never bridges a time gap).
    O(n), run once at load."""
    if w < 2 or len(samples) < w:
        return samples
    segs = _gap_segments(times)
    lat = _smooth_segments([s.lat for s in samples], segs, w)
    lon = _smooth_segments([s.lon for s in samples], segs, w)
    alt = _smooth_segments([s.altitude for s in samples], segs, w)
    out = []
    for i, s in enumerate(samples):
        out.append(pacer.GPSSample(
            lat=float(lat[i]), lon=float(lon[i]), altitude=float(alt[i]),
            full_speed=s.full_speed, ground_speed=s.ground_speed, timestamp_ms=s.timestamp_ms,
        ))
    return out


def load_recording(paths: list[str], smooth_window: int = SMOOTH_WINDOW):
    """The body of `Session.load` (session.py delegates here): single-pass-read the chapters,
    quality-gate + clean the trace, build the GPS9 true-clock axis, smooth the positions, feed
    `pacer.Laps`, centre a coordinate system on the clean track, then place/fit the start line.

    Returns `(laps, cs, video_path, chapter_map, imu, track_name, timing_quality)`:
      * `laps`/`cs` — segmented `pacer.Laps` + its `CoordinateSystem` (EMPTY/default if no paths
        or no samples survive cleaning);
      * `video_path` — first chapter path (None if no paths);
      * `chapter_map` — `chapters.ChapterMap` offset table (None if no paths);
      * `imu` — `(accl, grav, cori)` for `Session._build_gmeter` (None when the trace is empty);
      * `track_name` — detected registry track name (None for an unknown track);
      * `timing_quality` — `data_quality.TimingQuality`: the per-sample timing-clock provenance
        (GPS9 true clock vs media-clock fallback) + the gate's dropped-fix fraction. The data
        axis ORTHOGONAL to the timing-trust (start-line) surface; the views demote the lap times
        only when it reports degraded.
    """
    laps = pacer.Laps()
    empty = pacer.CoordinateSystem(pacer.GPSSample())
    video_path = paths[0] if paths else None
    quality = data_quality.TimingQuality()  # high-quality default (no paths / empty trace)
    if not paths:
        return laps, empty, None, None, None, None, quality

    # Single-pass: one chain read for both GPS and IMU (see ingest.read_recording).
    samples, spans, naive, durations, accl, grav, cori = read_recording(paths)
    n_raw = len(samples)
    # The offset table for the video layer: each chapter's media duration on one global axis.
    chapter_map = chapters.ChapterMap(list(paths), durations)
    samples, spans, naive, dropped = _gate_quality(samples, spans, naive)
    samples, spans, naive = _clean(samples, spans, naive)
    # Dropped-fix fraction is over the RAW fix count (pre-gate) — the share the quality gate
    # rejected, independent of how many later survived the geometric cleaner.
    dropped_fraction = dropped / n_raw if n_raw else 0.0
    if not samples:
        return laps, empty, video_path, chapter_map, None, None, quality

    # Per-sample timing clock: GPS9 true-clock spacing when the stream carries it, else the
    # (~0.1%-fast) media clock — the recording's headline timing-accuracy provenance.
    clock = (data_quality.GPS9_TRUECLOCK if _used_gps9_trueclock(samples)
             else data_quality.MEDIA_CLOCK_FALLBACK)
    quality = data_quality.TimingQuality(clock=clock, dropped_fraction=dropped_fraction)

    # GPS9 true-clock spacing (re-anchored to the media clock); naive otherwise.
    times = _gps9_times(samples, naive)

    # Smooth the GPS positions once, here — over the cleaned, time-ordered trace, guarded
    # against averaging across chapter/dropout gaps. All downstream geometry follows.
    samples = _smooth_track(samples, times, smooth_window)

    for s, t in zip(samples, times, strict=True):
        laps.add_point(s, float(t))

    # Coordinate system centred on the (now clean) track, then segment into laps.
    mn, mx = laps.min_max()
    clat, clon = (mn.y + mx.y) / 2, (mn.x + mx.x) / 2
    cs = pacer.CoordinateSystem(pacer.GPSSample(lat=clat, lon=clon, altitude=0))
    laps.set_coordinate_system(cs)

    track = tracks.detect_track(clat, clon)
    if track is not None:
        # Known track: fixed line via _fit_start_line (widens only if passes miss the short segment).
        base = tracks.start_line_segment(track, cs)
        _fit_start_line(laps, base)  # sets laps.sectors + update() on the chosen line
        if track.sectors:
            # A track that defines sector lines (a user-saved track): place them on the fitted
            # start line. MK defines none, so this is a no-op there — its timing is unchanged.
            laps.sectors = pacer.Sectors(
                start_line=laps.sectors.start_line,
                sector_lines=tracks.sector_line_segments(track, cs),
            )
            laps.update()
    else:
        # Unknown track: place a SENSIBLE default line perpendicular to travel at the peak-speed
        # point (the main straight) instead of an arbitrary point, then validate/widen it. Fall back
        # to the old random pick if the heuristic is degenerate OR its line finds no laps. Either way
        # the timing is PROVISIONAL (muted + the "drag the start/finish line" banner) until confirmed.
        xs = np.fromiter((cs.local(s)[0] for s in samples), float, len(samples))
        ys = np.fromiter((cs.local(s)[1] for s in samples), float, len(samples))
        speeds = np.fromiter((s.full_speed for s in samples), float, len(samples))
        base = _heuristic_start_base(xs, ys, speeds)
        if base is not None:
            _fit_start_line(laps, base)
        if base is None or _band_lap_count(laps) == 0:
            laps.sectors = pacer.Sectors(
                start_line=_widen(laps.pick_random_start(), START_WIDEN), sector_lines=[]
            )
            laps.update()
    return laps, cs, video_path, chapter_map, (accl, grav, cori), (
        track.name if track is not None else None), quality
