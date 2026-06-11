"""The Session.load pipeline: raw GPMF streams -> a segmented `pacer.Laps` + its frame.

Extracted verbatim from session.py (a structural move, not a rewrite): the GPS9 true-clock
time-axis builder (`_gps9_times`), the data cleaners (`_clean`, the quality gate is in
`_signal`), the load-time GPS smoothing (`_smooth_track`), the start-line fit for known
tracks (`_fit_start_line`), and `load_recording` — the body of `Session.load`.
`Session.load` (session.py) stays the public entry point and delegates here, then wraps the
result in a `Session` and builds the g-meter (which reads Session instance arrays).

One of the FOUR studio modules allowed to touch the `pacer` bindings (with session.py,
tracks.py, and ingest.py — see studio/PLAN.md "Architecture an agent MUST respect"):
ingest.py reads the raw streams, this module turns them into a timed/cleaned/segmented
`pacer.Laps`, session.py exposes the loaded session to the (pacer-free) views.
"""

from __future__ import annotations

import math

import numpy as np

import pacer

from . import chapters, tracks

# Pacer-free signal/clean helpers live in studio/_signal.py (numpy-only, shared with gmeter):
# the "real lap" band thresholds (MIN_LAP_TIME / MIN_LAP_SAMPLES / LAP_BAND_*), the GPS denoising
# window (SMOOTH_WINDOW / SMOOTH_GAP_S), the quality-gate thresholds (MIN_FIX / MAX_DOP), and the
# helpers themselves (_smooth, _smooth_segments, _gap_segments, _quality_ok, _gate_quality,
# _band_lap_ids).
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
# GPS denoising rationale: the upstream notebook smoothed x/y/lat/lon with a boxcar BEFORE
# measuring arc-length distance/delta, cutting the delta jitter without erasing the real lap-to-lap
# signal. We smooth the GPS track ONCE at load (in _smooth_track), so every downstream quantity the
# C++ core derives — cum_distances, lap segmentation, the delta resample, sector splits — uses the
# SAME smoothed coordinates. SMOOTH_WINDOW=13 (~1.3 s @ 10 Hz) was verified on the real session
# (studio/dev/denoise_check.py) to cut the high-frequency jitter while preserving the racing-line
# signal and the corner apexes; smoothing never bridges a >SMOOTH_GAP_S time gap (chaptered files /
# dropouts). The quality gate drops only NO-3D-lock / high-DOP fixes; sentinels are kept.


# --- True-clock timing from the GPS9 per-sample timestamps -------------------------------
# WHY: the `naive` axis spreads each payload's MEDIA span [a,b] across i/n. The GoPro's
# media clock for the GPS track runs ~0.1% fast (measured: 9.990 Hz over the real 0060
# session) whereas the GPS9 stream carries the true GPS fix time (`timestamp_ms`), which is
# real WALL-CLOCK at a clean 10.000 Hz — the same clock a lap-timing transponder uses. Lap
# time is a difference of two crossing instants, so the ~0.1% media-clock fast-rate
# systematically COMPRESSES every lap (measured ~30 ms on the best lap). Timing off the GPS9
# fix times removes that bias and is the physically-correct reference. (It supersedes an
# earlier global Adam timestamp-fit experiment, since removed, that diverged on long sessions.)
#
# We don't trust the GPS9 ABSOLUTE epoch (it jumps at chapter boundaries / midnight and is UTC,
# not the media clock the video layer maps against). We use only its per-sample SPACING, and
# RE-ANCHOR each contiguous run to that run's media (naive) start time — so the axis stays on
# the global media clock end-to-end (video sync, chapter offsets, durations all unchanged) but
# the inter-sample spacing within each run is the true 10 Hz GPS spacing.
GPS9_MIN_DT_S = 0.02    # an inter-sample GPS9 delta below this is a duplicate/garbage fix
GPS9_MAX_DT_S = 0.40    # …above this, the run is broken (chapter break / dropout / rollover)

# NOTE: an earlier transponder-fit GPS9 clock-rate multiplier was REMOVED. The out-of-sample
# validation (recording 0062, see studio/dev/_validate_wallclock.py) proved it a 0060-specific
# OVERFIT to GPS-dropout-tail skew, NOT a real clock rate: both recordings' true rate is ≈1.0
# (−22 / −46 ppm, not the fitted −486 ppm), and applying the factor WORSENS the clean-lap RMS on
# both. The plain GPS9 true-clock spacing (rate = 1.0) is already unbiased out of sample (0062
# clean-lap mean +0.0015 s, ±0.053 s), so the timing below uses the GPS9 spacing verbatim.


def _gps9_times(samples, naive, rate_factor: float = 1.0):
    """Per-sample times built from the GPS9 fix timestamps' true spacing, re-anchored per
    contiguous run to the media (naive) clock. Falls back to the naive time for any sample
    whose GPS9 timestamp is absent/sentinel or sits across a run break, so a GPS5-only stream
    (no per-sample timestamp) or a dropout degrades gracefully to the old behaviour.

    Returns a list aligned to `samples`. Guaranteed monotonic non-decreasing: each run is
    anchored at its naive start (naive is already sorted) and advanced by the GPS9 deltas; a
    run that would overtake the next run's anchor is clamped (can't happen for sane 10 Hz data
    but keeps the axis safe for video sync).

    `rate_factor` (default 1.0 = no correction) optionally scales the WITHIN-RUN GPS9 spacing.
    It multiplies only the spacing, so each run stays anchored at its media-clock start (video
    sync unchanged). It exists ONLY so the out-of-sample validator can probe an explicit `--rate`;
    the load path leaves it at 1.0 because the GPS9 wall-clock spacing is already unbiased (the
    former transponder-fit factor was an overfit — see the module note above)."""
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
    # Enforce monotonicity defensively (re-anchoring keeps runs ordered; this guards the seams).
    return list(np.maximum.accumulate(out))


def _sustained_moving(samples, lo, hi, run=5):
    """First index in [lo,hi) where the car is moving for `run` consecutive samples."""
    for i in range(lo, hi - run):
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
    """How many laps land in a band around the median lap time — the same 'real lap' notion
    as Session.valid_lap_ids, but a free function usable during load (no Session yet).
    Single-sourced via `_signal._band_lap_ids` (the exact same gate+median+band filter)."""
    return len(_band_lap_ids(laps))


def _fit_start_line(laps, base):
    """Choose the start/finish line for a known track. Prefer the EXACT track line (`base`)
    — the goal is the line at the given coords, and `_widen` scales about the MIDPOINT so the
    line stays centred on those coords regardless of factor. The short exact segment can miss
    a pass (the GPS step over the line lands just past an endpoint), fusing a real flying lap
    into the long out-lap; that under-segmentation is exactly when the PLAN says to widen.

    So: probe modest factors from smallest up and adopt the SMALLEST one that recovers more
    valid laps than the exact line — keeping the endpoints as close to A/B as possible (the
    midpoint is unchanged) while still catching missed passes. Cap the factor well below the
    point where the longer line double-crosses each flying lap and over-segments (~2.0+).
    Returns the chosen Segment; leaves `laps.sectors` on it."""
    laps.sectors = pacer.Sectors(start_line=base, sector_lines=[])
    laps.update()
    base_n = _band_lap_count(laps)
    best_seg = base
    # Smallest-first: take the first factor that recovers even one real (band) lap the exact
    # short segment missed. Modest cap — wider drifts off the straight and over-segments.
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

    # Drop points clearly outside the track. The 1st–99th percentile box ignores the far GPS
    # excursions (km off-track) when sizing, then a generous margin keeps the whole real track;
    # anything beyond that box is an off-track fix and is removed.
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
    """Return NEW GPSSamples with lat/lon/altitude boxcar-smoothed in place, matching the
    upstream notebook's gold-standard map. Smoothing the SOURCE coordinates (not a render-time copy)
    means every downstream quantity the C++ core derives — arc-length cum_distances, lap
    segmentation, the lap-vs-best delta, sector splits — is computed from the SAME smoothed
    track, so the trace and all metrics stay consistent. Speed fields are left untouched.

    Smoothed independently within each gap-free run so the average never bridges a time
    discontinuity. O(n) and run once at load — never per frame."""
    if w < 2 or len(samples) < w:
        return samples
    segs = _gap_segments(times)
    lat = _smooth_segments([s.lat for s in samples], segs, w)
    lon = _smooth_segments([s.lon for s in samples], segs, w)
    alt = _smooth_segments([s.altitude for s in samples], segs, w)
    out = []
    for i, s in enumerate(samples):
        # Preserve every other field (speeds, timestamp) — only the position is smoothed.
        out.append(pacer.GPSSample(
            lat=float(lat[i]), lon=float(lon[i]), altitude=float(alt[i]),
            full_speed=s.full_speed, ground_speed=s.ground_speed, timestamp_ms=s.timestamp_ms,
        ))
    return out


def load_recording(paths: list[str], smooth_window: int = SMOOTH_WINDOW):
    """The body of `Session.load` (the public entry point in session.py delegates here):
    single-pass-read the chapters, quality-gate + clean the trace, build the GPS9 true-clock
    time axis, smooth the GPS positions, hand the points to `pacer.Laps`, centre a coordinate
    system on the clean track, and place/fit the start line (known track ⇒ its fixed line,
    else pick_random_start + widen).

    Returns `(laps, cs, video_path, chapter_map, imu, track_name)`:
      * `laps`/`cs` — the segmented `pacer.Laps` + its `pacer.CoordinateSystem` (an EMPTY laps
        and a default cs if `paths` is empty or no samples survive cleaning);
      * `video_path` — the first chapter path (None if no paths);
      * `chapter_map` — the `chapters.ChapterMap` offset table (None if no paths);
      * `imu` — the `(accl, grav, cori)` streams off the same single-pass chain, for
        `Session._build_gmeter` (which reads Session instance arrays, so it stays a Session
        method); None when the trace is empty (the g-meter is skipped exactly as before);
      * `track_name` — the detected registry track's name (`tracks.detect_track` on the clean
        trace's centroid), or None for an unknown track (where the start line above is the
        pick_random_start auto-fit). Stored on the Session for the timing-line sidecar's
        `track` field and the app's "unknown track" notice.
    """
    laps = pacer.Laps()
    empty = pacer.CoordinateSystem(pacer.GPSSample())
    video_path = paths[0] if paths else None
    if not paths:
        return laps, empty, None, None, None, None

    # Single-pass ingest: build the SequentialGPSSource chain ONCE and read BOTH the GPS
    # trace and the IMU (accl/grav/cori) streams off the same opened containers, so each
    # chapter MP4 is opened / GMPF-parsed once on load instead of twice. The IMU arrays are
    # handed straight to Session._build_gmeter (no second open). Byte-identical to the former
    # read_gpmf(paths) + read_imu(paths) two-chain path (see ingest.read_recording).
    samples, spans, naive, durations, accl, grav, cori = read_recording(paths)
    # The offset table for the video layer: each chapter's media duration on one global axis.
    chapter_map = chapters.ChapterMap(list(paths), durations)
    samples, spans, naive = _gate_quality(samples, spans, naive)
    samples, spans, naive = _clean(samples, spans, naive)
    if not samples:
        return laps, empty, video_path, chapter_map, None, None

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
        # Known track: use its FIXED start/finish line (a track property) instead of
        # pick_random_start/_widen, which mis-placed the line after _clean shifted the
        # median point. Keep the exact coords; widen modestly about the midpoint only
        # if some passes miss the short segment.
        base = tracks.start_line_segment(track, cs)
        _fit_start_line(laps, base)  # sets laps.sectors + update() on the chosen line
    else:
        laps.sectors = pacer.Sectors(
            start_line=_widen(laps.pick_random_start(), START_WIDEN), sector_lines=[]
        )
        laps.update()
    return laps, cs, video_path, chapter_map, (accl, grav, cori), (
        track.name if track is not None else None)
