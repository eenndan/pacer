"""Session: load GoPro/GPMF telemetry into a `pacer.Laps` and expose UI-friendly data.

All the C++ analysis (lap/sector segmentation, distances, lap-vs-best delta resampling,
timestamp interpolation) is reused via the bound `pacer` module. This file only adds the
thin glue the old ImGui app kept inline in laps-display.cpp: building plot series, the
delta computation, and writing dragged timing lines back to the core.

Coordinate note (verified against pacer/laps/laps.cpp): the track trace and the timing
lines both live in LOCAL meters (cs.local). `pick_random_start()`/`update()` must run
AFTER `set_coordinate_system()`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

import pacer

from . import gapfill, tracks

DEFAULT_SAMPLE = "3rdparty/gpmf-parser/samples/hero6.mp4"  # a clip with real motion

# Data-cleaning thresholds (see studio/diagnose.py; validated on real sessions).
MIN_START_SPEED = 3.0  # m/s — below this the car is stationary / GPS not yet locked
SPIKE_STEP = 50.0  # m — a lone fix farther than this from BOTH neighbours is a glitch
OFF_TRACK_MARGIN = 0.5  # drop points outside the inlier bbox (1-99 pct) expanded by this fraction
START_WIDEN = 3.0  # widen the auto start line so every lap pass crosses it
MIN_LAP_TIME = 5.0  # s — laps shorter than this are partial/phantom, not real laps
MIN_LAP_SAMPLES = 20  # a real lap has at least this many GPS samples
LAP_BAND_LO, LAP_BAND_HI = 0.5, 1.6  # "real lap" = lap_time within [lo, hi] x median lap time

# --- GPS denoising (see notebooks/interpolation.ipynb + noise-investigation.ipynb) ---
# Position smoothing window (samples). The notebook's gold-standard map smooths x/y/lat/lon
# with a w=9 boxcar (~0.9 s @ 10 Hz) BEFORE measuring arc-length distance/delta; that cut the
# delta jitter ~14% without erasing the real ~0.5 s lap-to-lap signal. We smooth the GPS
# track ONCE at load (here), so every downstream quantity the C++ core derives — cum_distances,
# lap segmentation, the delta resample, sector splits — uses the SAME smoothed coordinates.
# w=13 (~1.3 s @ 10 Hz): tuned up from the notebook's w=9 baseline because the studio map
# feeds the SMOOTHED track straight back into segmentation/distance, so a touch more smoothing
# buys a much cleaner trace. Verified on the real session (studio/denoise_check.py): w=13 cuts
# the high-frequency cross-track jitter ~39% and the point-to-point heading jitter ~91% while
# the lap-to-lap racing-line signal is preserved and the corner APEXES are not clipped (a
# close-up hairpin render shows w<=15 tracking the raw apex; w>=21 visibly cuts the corner).
SMOOTH_WINDOW = 13  # boxcar width in samples; 1 disables smoothing
# Don't smooth across large sample-time gaps (chaptered files / dropouts): a moving average
# spanning a gap would drag points across a discontinuity. Split the trace at gaps > this.
SMOOTH_GAP_S = 1.0  # s — a jump larger than ~10x the 10 Hz period starts a new smoothing run

# --- GPS quality gating (uses the GPS9 DOP / fix fields exposed by the C++ core) ---
# Reject obviously-bad fixes before smoothing/segmentation. Conservative defaults: drop only
# samples that report NO 3D lock or an implausibly high dilution-of-precision. Sentinels
# (fix<0 / non-finite dop, e.g. from the GPS5 stream which carries no DOP) mean "unknown" and
# are KEPT — we never reject for missing quality info.
MIN_FIX = 3  # GPS9 fix: 0=none, 2=2D, 3=3D. Require a 3D lock when the field is present.
MAX_DOP = 10.0  # GPS9 DOP: dilution of precision; >~10 is a poor-geometry fix. Generous.


def _smooth(a, w: int = SMOOTH_WINDOW):
    """Edge-correct boxcar moving average — the notebook's `np.convolve(a, ones(w)/w, "same")`
    in the interior, but normalized at the ends so the first/last w//2 points aren't dragged
    toward zero by the convolution's implicit zero-padding (a raw `"same"` boxcar tapers the
    edges; here those points are averaged over only the samples that actually exist).

    A no-op for w<2 or arrays shorter than the window. Applied to the GPS track coordinates
    (lat/lon/alt) once at load — never per frame.
    """
    a = np.asarray(a, float)
    if w < 2 or len(a) < w:
        return a
    kernel = np.ones(w)
    num = np.convolve(a, kernel, "same")          # windowed sum
    den = np.convolve(np.ones(len(a)), kernel, "same")  # count of real samples in each window
    return num / den


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


@dataclass
class Seg:
    """A timing line in LOCAL meters: two endpoints (x1,y1)-(x2,y2)."""

    x1: float
    y1: float
    x2: float
    y2: float

    @classmethod
    def from_pacer(cls, s) -> "Seg":
        return cls(s.first.x, s.first.y, s.second.x, s.second.y)

    def to_pacer(self):
        seg = pacer.Segment()
        p1, p2 = pacer.Point(), pacer.Point()
        p1.x, p1.y = float(self.x1), float(self.y1)
        p2.x, p2.y = float(self.x2), float(self.y2)
        seg.first, seg.second = p1, p2
        return seg


def fmt_time(seconds: float) -> str:
    if not math.isfinite(seconds):
        return "—"
    m, s = divmod(seconds, 60)
    return f"{int(m)}:{s:06.3f}"


def _read_gpmf(paths):
    """Iterate one or more GoPro files, returning (samples, spans, naive_times)."""
    owners = [pacer.GPMFSource(paths[0])]
    head = owners[0]
    for p in paths[1:]:
        nxt = pacer.GPMFSource(p)
        owners.append(nxt)
        head = pacer.SequentialGPSSource(head, nxt)
        owners.append(head)  # keep the chain alive while we iterate

    samples, spans, naive = [], [], []
    head.seek(0)
    while not head.is_end():
        a, b = head.current_time_span()
        chunk = []
        head.read_samples(lambda s, i, n: chunk.append((s, i, n)))
        for s, i, n in chunk:
            samples.append(s)
            spans.append((a, b))
            naive.append(a + (b - a) * (i / n if n else 0.0))
        head.next()
    return samples, spans, naive


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
    out = pacer.Segment()
    a, b = pacer.Point(), pacer.Point()
    a.x, a.y = mx + (seg.first.x - mx) * factor, my + (seg.first.y - my) * factor
    b.x, b.y = mx + (seg.second.x - mx) * factor, my + (seg.second.y - my) * factor
    out.first, out.second = a, b
    return out


def _band_lap_count(laps) -> int:
    """How many laps land in a band around the median lap time — the same 'real lap' notion
    as Session.valid_lap_ids, but a free function usable during load (no Session yet)."""
    ts = [laps.lap_time(i) for i in range(laps.laps_count())
          if laps.sample_count(i) >= MIN_LAP_SAMPLES and laps.lap_time(i) >= MIN_LAP_TIME]
    if not ts:
        return 0
    med = float(np.median(ts))
    return sum(1 for t in ts if LAP_BAND_LO * med <= t <= LAP_BAND_HI * med)


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


def _clean(samples, spans, naive):
    """Trim the stationary lead-in/cool-down (where GPS spikes cluster), then drop lone
    teleport glitches (a fix far from BOTH neighbours while they stay close to each other).
    Returns cleaned (samples, spans, naive). See studio/diagnose.py for the evidence."""
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


def _smooth_track(samples, times, w: int = SMOOTH_WINDOW):
    """Return NEW GPSSamples with lat/lon/altitude boxcar-smoothed in place, matching the
    notebook's gold-standard map. Smoothing the SOURCE coordinates (not a render-time copy)
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


class Session:
    def __init__(self, laps: pacer.Laps, cs, video_path: str | None):
        self.laps = laps
        self.cs = cs
        self.video_path = video_path
        self._lap_cache: dict[int, object] = {}
        # Per-lap (times, dists) arrays for distance_in_lap_at_time — rebuilding these from the
        # bound lap object every ~30 Hz cursor tick is wasteful; cache and clear on re-segment.
        self._dist_cache: dict[int, tuple] = {}
        # Per-lap gap-filled draw segments (measured + inferred runs). MAP RENDERING ONLY —
        # computed once per lap on first draw, never per frame; cleared on re-segment.
        self._seg_cache: dict[int, list] = {}
        self._fills_cache: dict[int, list] = {}
        self._reference_xy = None  # lazily-built georeferenced track centerline (fallback donor)

        # Full-trace arrays in local meters + the video-clock time + speed (km/h).
        n = laps.point_count()
        self.tx = np.empty(n)
        self.ty = np.empty(n)
        self.tt = np.empty(n)
        self.tv = np.empty(n)
        for i in range(n):
            pit = laps.get_point(i)
            loc = cs.local(pit.point)
            self.tx[i], self.ty[i] = loc[0], loc[1]
            self.tt[i] = pit.time
            self.tv[i] = pit.point.full_speed * 3.6

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, paths: list[str], interpolate: bool = False,
             smooth_window: int = SMOOTH_WINDOW) -> "Session":
        """Naive timing by default — the C++ interpolation diverges on long/noisy sessions
        (see studio/diagnose.py); `interpolate=True` enables it but the result is validated
        and falls back to naive if it's non-monotonic or runs past the video duration.

        The GPS track is quality-gated (drop no-3D-lock / high-DOP fixes) and boxcar-smoothed
        (window `smooth_window`, default SMOOTH_WINDOW) BEFORE the points are handed to the
        core, so the map trace and every distance/delta/sector derived from it match the smooth
        notebook reference. `smooth_window=1` disables smoothing (raw trace, for baselines)."""
        laps = pacer.Laps()
        empty = pacer.CoordinateSystem(pacer.GPSSample())
        video_path = paths[0] if paths else None
        if not paths:
            return cls(laps, empty, None)

        samples, spans, naive = _read_gpmf(paths)
        samples, spans, naive = _gate_quality(samples, spans, naive)
        samples, spans, naive = _clean(samples, spans, naive)
        if not samples:
            return cls(laps, empty, video_path)

        times = list(naive)
        if interpolate and len(samples) >= 8:
            times = cls._interpolated_or_naive(samples, spans, naive)

        # Smooth the GPS positions once, here — over the cleaned, time-ordered trace, guarded
        # against averaging across chapter/dropout gaps. All downstream geometry follows.
        samples = _smooth_track(samples, times, smooth_window)

        for s, t in zip(samples, times):
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
        return cls(laps, cs, video_path)

    @staticmethod
    def _interpolated_or_naive(samples, spans, naive) -> list[float]:
        duration = max(b for _, b in spans)
        try:
            res = pacer.interpolate_timestamps(samples, spans, pacer.CoordinateSystem(samples[0]))
            ts = np.array(res.timestamps)
        except Exception as e:  # noqa: BLE001 — interpolation is best-effort
            print(f"studio: interpolation failed ({e!r}); naive timing.")
            return list(naive)
        ok = (len(ts) == len(samples)
              and bool(np.all(np.diff(ts) >= -1e-6))
              and ts.min() >= -1.0
              and ts.max() <= duration * 1.05)
        if not ok:
            print(f"studio: interpolation rejected (range {ts.min():.1f}..{ts.max():.1f}s "
                  f"vs duration {duration:.1f}s); using naive timing.")
            return list(naive)
        return list(ts)

    # ----------------------------------------------------------- timing lines
    @property
    def start_line(self) -> Seg:
        return Seg.from_pacer(self.laps.sectors.start_line)

    @property
    def sector_lines(self) -> list[Seg]:
        return [Seg.from_pacer(s) for s in self.laps.sectors.sector_lines]

    def set_timing_lines(self, start: Seg, sectors: list[Seg]) -> None:
        self.laps.sectors = pacer.Sectors(
            start_line=start.to_pacer(),
            sector_lines=[s.to_pacer() for s in sectors],
        )
        self.laps.update()
        self._lap_cache.clear()
        self._dist_cache.clear()
        self._seg_cache.clear()
        self._fills_cache.clear()

    def suggest_sector(self, existing: int = 0) -> Seg:
        """A line perpendicular to the track at a DISTINCT fraction of the way round, so each
        added sector lands on a different track position. With `existing` sector lines already
        placed, the new one is the (existing+1)-th of (existing+2) sub-sectors, so put it at
        fraction (existing+1)/(existing+2) — 1/2, then 2/3, 3/4, … — evenly subdividing the
        lap and never colliding with an earlier suggestion (which would collapse a split to 0).

        The fraction is taken along a single representative lap's trace (the best lap), not the
        full multi-lap trace: a fraction of the full trace lands on an arbitrary lap, so two
        suggestions could still map to the same per-lap distance. ±15 m (not ±5) so the line
        reliably registers a crossing every lap — a too-short line gets stepped over, fusing
        sub-sectors and making split times exceed the lap time. Draggable to adjust."""
        frac = (existing + 1) / (existing + 2)
        best = self.best_lap_id()
        xy = None
        if best is not None:
            xs, ys = self.lap_trace_xy(best)
            if len(xs) >= 4:
                xy = (np.asarray(xs), np.asarray(ys))
        if xy is None:  # no valid lap yet — fall back to the full trace
            if len(self.tx) < 4:
                return Seg(0, 0, 0, 0)
            xy = (self.tx, self.ty)
        xs, ys = xy
        n = len(xs)
        i = min(int(n * frac), n - 2)
        j = min(i + 5, n - 1)
        dx, dy = xs[j] - xs[i], ys[j] - ys[i]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        cx, cy = xs[i], ys[i]
        return Seg(cx - nx * 15, cy - ny * 15, cx + nx * 15, cy + ny * 15)

    # ------------------------------------------------------------- lap access
    def _get_lap(self, lap_id: int):
        lap = self._lap_cache.get(lap_id)
        if lap is None:
            lap = self.laps.get_lap(lap_id)
            self._lap_cache[lap_id] = lap
        return lap

    def lap_count(self) -> int:
        return self.laps.laps_count()

    def valid_lap_ids(self) -> list[int]:
        """Real laps only. A fixed threshold is too crude (short double-crossings of the
        start line pass it and pollute the 'best' lap), so accept laps whose time is within
        a band around the MEDIAN lap time — this adapts to any track length."""
        basic = [(i, self.laps.lap_time(i)) for i in range(self.laps.laps_count())
                 if self.laps.sample_count(i) >= MIN_LAP_SAMPLES and self.laps.lap_time(i) >= MIN_LAP_TIME]
        if not basic:
            return []
        med = float(np.median([t for _, t in basic]))
        lo, hi = LAP_BAND_LO * med, LAP_BAND_HI * med
        return [i for i, t in basic if lo <= t <= hi]

    def best_lap_id(self) -> int | None:
        valid = self.valid_lap_ids()
        return min(valid, key=self.laps.lap_time) if valid else None

    def best_lap_total_distance(self) -> float | None:
        """The best lap's total odometer distance (metres) — the basis the delta plot's x-axis is
        scaled in (x = s × best_distance). Used to map the delta cursor's x to/from a media time.
        Matches the `best_dist[-1]` used in `delta()`. None if there's no valid best lap."""
        best = self.best_lap_id()
        if best is None:
            return None
        td = self._lap_time_dist(best)
        if td is None:
            return None
        return float(td[1][-1])

    def lap_rows(self) -> list[dict]:
        return [
            {
                "idx": i,
                "time": self.laps.lap_time(i),
                "dist": self.laps.get_lap_distance(i, self.cs),
                "entry": self.laps.lap_entry_speed(i) * 3.6,
            }
            for i in self.valid_lap_ids()
        ]

    def lap_trace_xy(self, lap_id: int):
        """Local-meter (xs, ys) of a single lap's trace, for highlighting on the map."""
        lap = self._get_lap(lap_id)
        xs, ys = [], []
        for p in lap.points:
            v = self.cs.local(p.point)
            xs.append(v[0])
            ys.append(v[1])
        return xs, ys

    # ------------------------------------------------- map gap-fill (rendering only)
    def _lap_trace_xyt(self, lap_id: int):
        """Per-lap (xs, ys, times): local metres + media-clock seconds. Used only to build
        the gap-filled DRAW segments — never feeds the analysis pipeline."""
        lap = self._get_lap(lap_id)
        xs, ys, ts = [], [], []
        for p in lap.points:
            v = self.cs.local(p.point)
            xs.append(v[0])
            ys.append(v[1])
            ts.append(p.time)
        return np.asarray(xs), np.asarray(ys), np.asarray(ts)

    def _median_sample_dt(self) -> float:
        """Median inter-sample interval over the whole trace (s) — used to size gaps."""
        if len(self.tt) < 2:
            return 0.1
        d = np.diff(self.tt)
        d = d[(d > 0) & (d < 1.0)]
        return float(np.median(d)) if len(d) else 0.1

    def _donors_for(self, lap_id: int):
        """Ordered fill-source list for reconstructing `lap_id`'s gaps: every OTHER valid lap
        first (cross-lap borrow, the primary source), then the georeferenced reference
        centerline LAST (fallback). Each donor is {"xy", "name", "is_reference"}."""
        donors = []
        for other in self.valid_lap_ids():
            if other == lap_id:
                continue
            ox, oy, _ = self._lap_trace_xyt(other)
            if len(ox) >= 3:
                donors.append({"xy": np.column_stack([ox, oy]),
                               "name": str(other), "is_reference": False})
        ref = self.reference_centerline_xy()
        if ref is not None and len(ref) >= 3:
            donors.append({"xy": ref, "name": "MK-ref", "is_reference": True})
        return donors

    def reference_centerline_xy(self):
        """The georeferenced track centerline in LOCAL metres (an (M,2) array), or None.

        Built once and cached. Only the known-track fallback uses it; with ~18 laps the
        cross-lap borrow covers virtually every gap, so this is rarely needed. See
        studio/reference.py for the trace+georeference of the Daytona MK centerline."""
        if self._reference_xy is not None:
            return self._reference_xy if len(self._reference_xy) else None
        from . import reference  # local import: optional, only on the fallback path
        agg = np.column_stack([self.tx, self.ty]) if len(self.tx) else None
        self._reference_xy = reference.centerline_local(self.cs, agg)
        return self._reference_xy if len(self._reference_xy) else None

    def lap_trace_segments(self, lap_id: int):
        """Ordered list of `gapfill.Segment` for drawing this lap: measured GPS runs and
        reconstructed (inferred) fills, tagged so the renderer can dash/dim the inferred ones.

        MAP RENDERING ONLY. Built from the lap's kept-point arrays (the same points
        `lap_trace_xy` returns); it does NOT alter any analysis quantity. Cached per lap."""
        cached = self._seg_cache.get(lap_id)
        if cached is not None:
            return cached
        xs, ys, ts = self._lap_trace_xyt(lap_id)
        donors = self._donors_for(lap_id)
        segs, fills = gapfill.reconstruct_lap(xs, ys, ts, donors, med_dt=self._median_sample_dt())
        self._seg_cache[lap_id] = segs
        self._fills_cache[lap_id] = fills
        return segs

    def lap_gap_report(self, lap_id: int) -> list[dict]:
        """Per-gap fill report for a lap (for metrics/diagnostics): each dict has the gap's
        chord length, dt, n_missing, fill source, filled length and endpoint error. Computed
        as a side effect of `lap_trace_segments` (cached)."""
        if lap_id not in self._fills_cache:
            self.lap_trace_segments(lap_id)
        return self._fills_cache.get(lap_id, [])

    def lap_window(self, lap_id: int) -> tuple[float, float] | None:
        if not (0 <= lap_id < self.laps.laps_count()):
            return None
        t0 = self.laps.start_timestamp(lap_id)
        return (t0, t0 + self.laps.lap_time(lap_id))

    def lap_sector_splits(self, lap_id: int) -> list[float]:
        """Per-sub-sector split times (seconds) for a lap, in order. With N sector lines a
        lap has N+1 sub-sectors and these sum to the lap time.

        Mapped by DISTANCE PROJECTION, not pacer's geometric crossing list: the short sector
        lines miss a pass on many laps (the GPS step over the line lands just past an endpoint),
        leaving blank columns and fusing sub-sectors into splits that exceed the lap time. So
        instead project each sector line's MIDPOINT onto this lap's trace — the cum_distance of
        the nearest trace point is that boundary's lap distance d_k — then read elapsed time at
        each boundary by interpolating on (cum_distance, elapsed). With the lap start (d=0) and
        finish (d=total) the N boundaries give N+1 splits that are all positive and SUM to the
        lap time for every lap (no blanks, none exceeding the lap time)."""
        lines = self.laps.sectors.sector_lines
        n_splits = len(lines) + 1
        lap = self._get_lap(lap_id)
        pts = lap.points
        cds = lap.cum_distances
        m = min(len(pts), len(cds))
        if m < 2:
            return []
        locs = [self.cs.local(pts[i].point) for i in range(m)]
        xy = np.array([(v[0], v[1]) for v in locs])
        cum_distance = np.asarray(cds[:m], dtype=float)
        t0 = pts[0].time
        elapsed = np.array([pts[i].time - t0 for i in range(m)])

        # Each sector line's lap distance = cum_distance of the lap point nearest its midpoint.
        bounds = []
        for seg in lines:
            mx = (seg.first.x + seg.second.x) / 2.0
            my = (seg.first.y + seg.second.y) / 2.0
            j = int(np.argmin((xy[:, 0] - mx) ** 2 + (xy[:, 1] - my) ** 2))
            bounds.append(float(cum_distance[j]))

        total = float(cum_distance[-1])
        # Boundaries plus lap start/finish, sorted: N+1 sub-sectors. interp elapsed at each.
        edges = [0.0] + sorted(bounds) + [total]
        t_at = np.interp(edges, cum_distance, elapsed)
        splits = [float(t_at[k + 1] - t_at[k]) for k in range(n_splits)]
        return splits

    def _lap_time_dist(self, lap_id: int):
        """Cached (times, dists) for a lap: media-clock seconds + per-lap odometer (metres),
        both monotonic and aligned. The single source the cursor↔video conversions interpolate
        on — built once per lap, cleared on re-segment. Returns None if the lap is degenerate."""
        td = self._dist_cache.get(lap_id)
        if td is None:
            lap = self._get_lap(lap_id)
            n = lap.count()
            cds = lap.cum_distances
            m = min(n, len(cds))
            if m < 2:
                return None
            times = np.array([lap.points[i].time for i in range(m)])
            dists = np.array([cds[i] for i in range(m)])
            td = (times, dists)
            self._dist_cache[lap_id] = td
        return td

    def distance_in_lap_at_time(self, lap_id: int, t: float) -> float | None:
        td = self._lap_time_dist(lap_id)
        if td is None:
            return None
        times, dists = td
        return float(np.interp(t, times, dists))

    # ------------------------------------------------ cursor scrub: x <-> media time
    # The plot cursors are DRAGGABLE; dragging seeks the video within the *current* lap.
    # plots_view stays pacer-free, so the x<->time mapping for each plot/axis-mode lives here
    # (pure numpy on the cached per-lap arrays). The speed + delta plots SHARE one x-axis (the
    # dist/time toggle drives both, and they're x-linked), so the same media moment lands at the
    # same x on BOTH plots — the two cursors always coincide. Two plots, one truth = the media
    # time:
    #   * TIME mode (both plots):     x = t - lap_start            (t = lap_start + x)
    #   * DISTANCE mode (both plots): x = s × best_total_dist, where s = dist_in_lap(t)/lap_total
    #     is the NORMALIZED distance fraction. This is the SAME axis the curves are drawn on
    #     (session.delta maps every lap's s∈[0,1] through the best lap's distance), so a cursor
    #     placed here sits exactly on its curve AND coincides with the other plot's cursor.
    #     Inverse: s = x / best_total_dist; dist_in_lap = s × this lap's total; then interp→time.
    # 'distance' and 'delta' are the SAME shared-distance mode (delta is kept as a readable alias
    # for the signal the delta-plot cursor emits). All clamp to the lap window so a drag can't
    # escape the current lap.

    def media_time_at_plot_x(self, lap_id: int, x: float, mode: str,
                             best_distance: float | None = None) -> float | None:
        """Absolute media-clock time (s) for a plot x-value within `lap_id`.

        `mode` is 'time' (time-into-lap x, seconds) or 'distance'/'delta' (the SHARED distance
        axis, x = s × best_distance metres — both plots use it, so the cursors coincide). For
        the distance/delta modes pass the best lap's total distance as `best_distance`. The
        result is CLAMPED to `lap_id`'s [start, end] media window so a drag can't leave the
        current lap. Returns None if the lap is degenerate (so the caller can no-op)."""
        td = self._lap_time_dist(lap_id)
        if td is None:
            return None
        times, dists = td
        t0, t1 = float(times[0]), float(times[-1])
        if mode == "time":
            t = t0 + float(x)
        else:  # 'distance' / 'delta' — the shared normalized-distance × best_distance axis
            if not best_distance:
                return None
            s = float(x) / float(best_distance)            # normalized fraction [0,1]
            d = s * float(dists[-1])                        # → this lap's odometer (m)
            # Invert distance→time within the lap on the monotonic odometer.
            t = float(np.interp(d, dists, times))
        return min(max(t, t0), t1)

    def plot_x_at_media_time(self, lap_id: int, t: float, mode: str,
                             best_distance: float | None = None) -> float | None:
        """Inverse of `media_time_at_plot_x`: the plot x-value for media-clock time `t` within
        `lap_id`, in the given `mode` ('time', or the shared-distance 'distance'/'delta'). Used
        to re-place a cursor from the shared media time. Returns None if the lap is degenerate
        (or distance/delta with no best distance)."""
        td = self._lap_time_dist(lap_id)
        if td is None:
            return None
        times, dists = td
        if mode == "time":
            return float(t) - float(times[0])
        # 'distance' / 'delta' — the shared normalized-distance × best_distance axis.
        if not best_distance:
            return None
        d = float(np.interp(t, times, dists))  # distance-into-lap at t
        s = d / float(dists[-1])               # normalized fraction [0,1]
        return s * float(best_distance)

    # -------------------------------------------------------- plot series glue
    def _lap_arrays(self, lap_id):
        """(dist, speed_kmh, elapsed) numpy arrays for a lap, aligned to the min length.

        Arc-length basis: cum_distances is the per-lap odometer (monotonic), full_speed is
        m/s (→ km/h), elapsed is seconds from the lap's first point. Aligning to the shortest
        of the three guards against the C++ arrays disagreeing in length by one.
        """
        lap = self._get_lap(lap_id)
        pts = lap.points
        dist = np.array(lap.cum_distances)
        m = min(len(dist), len(pts))
        dist = dist[:m]
        speed_kmh = np.array([pts[i].point.full_speed * 3.6 for i in range(m)])
        t0 = pts[0].time if m else 0.0
        elapsed = np.array([pts[i].time - t0 for i in range(m)])
        return dist, speed_kmh, elapsed

    _DELTA_GRID_N = 400  # samples on the normalized-distance grid (smooth + cheap to render)

    def delta(self, lap_ids, x_mode: str = "distance"):
        """Returns (best_lap_id, speed_series, delta_series) for the speed + delta plots, which
        SHARE one x-axis (the dist/time toggle drives both, and they're x-linked).

        Always references the GLOBAL best lap, so a single selected lap still shows a
        meaningful delta-to-best (not a trivial flat zero).

        Laps are aligned by NORMALIZED distance fraction, not raw odometer: each lap's
        total distance differs (slightly different racing lines), so equal raw distance is
        a different point on the track. For each lap s = cum_distance/total_distance spans
        [0,1] exactly; on a shared s-grid (np.linspace(0,1,N)) elapsed-time and speed are
        interpolated for every selected lap AND the best lap. Then

            delta_lap(s) = elapsed_lap(s) - elapsed_best(s)

        and at s=1 (the finish line) this is exactly lap_total_time - best_total_time — the
        laptime difference shown in the table.

        `x_mode` selects the SHARED x-axis used by BOTH plots:
          * 'distance' — x = s × best_total_distance (metres). Identical for every lap, so the
            curves and the (distance-mode) scrub cursors all live on one axis → x-link aligns
            them. This is the axis `plot_x_at_media_time(..., 'distance'/'delta')` maps to.
          * 'time' — x = elapsed_lap(s) (seconds into the lap). Each lap gets its OWN x (its
            own time-into-lap), but all start at 0 on the same scale; delta-vs-time is the Δ at
            each time-into-lap. Matches `plot_x_at_media_time(..., 'time')` (x = t − lap_start).
        The delta y-values (and the laptime-diff endpoint) are identical in both modes — only
        the x basis changes — so the delta endpoint still equals the laptime difference.
        """
        ids = [i for i in lap_ids if 0 <= i < self.laps.laps_count()]
        best = self.best_lap_id()
        if not ids or best is None:
            return None

        arrays = {}
        for lid in set(ids) | {best}:
            dist, speed_kmh, elapsed = self._lap_arrays(lid)
            if len(dist) >= 2 and dist[-1] > 0:
                arrays[lid] = (dist, speed_kmh, elapsed)
        if best not in arrays:
            return None

        # Common grid in normalized distance fraction [0,1]; the same fraction is the same
        # track position on every lap, so the last point (s=1) is the finish line for all.
        s_grid = np.linspace(0.0, 1.0, self._DELTA_GRID_N)
        best_dist, _, best_elapsed = arrays[best]
        # Distance mode keeps the x-axis in metres via the best lap's distance (one shared x).
        x_dist = s_grid * float(best_dist[-1])
        best_elapsed_on_grid = np.interp(s_grid, best_dist / best_dist[-1], best_elapsed)

        speed, delta = {}, {}
        for lid, (dist, speed_kmh, elapsed) in arrays.items():
            s_lap = dist / dist[-1]  # this lap's own distance fraction, spans [0,1]
            spd_on_grid = np.interp(s_grid, s_lap, speed_kmh)
            elapsed_on_grid = np.interp(s_grid, s_lap, elapsed)
            # Time mode: each lap's own elapsed time at each s (time-into-lap, starts at 0).
            # Distance mode: the shared s × best_distance metres.
            x = elapsed_on_grid if x_mode == "time" else x_dist
            speed[lid] = (x, spd_on_grid)
            # delta at s=1 == this lap's elapsed(1) - best elapsed(1) == laptime difference.
            delta[lid] = (x, elapsed_on_grid - best_elapsed_on_grid)
        return best, speed, delta

    # ------------------------------------------------------------ video sync
    def index_at_time(self, t: float) -> int | None:
        n = len(self.tt)
        if n == 0:
            return None
        i = int(np.searchsorted(self.tt, t))
        return min(max(i, 0), n - 1)

    def lap_at_time(self, t: float) -> int | None:
        """The valid lap whose [start_timestamp, start_timestamp+lap_time] window contains
        `t` (media-clock seconds), else None — for the readout + current-lap highlight."""
        for lap_id in self.valid_lap_ids():
            t0 = self.laps.start_timestamp(lap_id)
            if t0 <= t <= t0 + self.laps.lap_time(lap_id):
                return lap_id
        return None

    def speed_at_time(self, t: float) -> float | None:
        """Speed (km/h) at media-clock time `t`, from the nearest trace sample, else None."""
        i = self.index_at_time(t)
        if i is None:
            return None
        return float(self.tv[i])

    def delta_at_time(self, t: float) -> float | None:
        """Δ-to-best (seconds) at media-clock time `t`: how far ahead (−) / behind (+) the lap
        being driven at `t` is versus the GLOBAL best lap, AT THE SAME TRACK POSITION. None if
        `t` isn't inside a valid lap (lead-in / between laps) or there's no best lap.

        Consistent with the delta plot's curve (same normalized-distance alignment): find the
        lap containing `t`, take its distance fraction s = dist_in_lap(t)/lap_total, then
        Δ = elapsed_lap(s) − elapsed_best(s). At the lap finish (s=1) this equals the laptime
        difference. Drives the always-on readout box, which reflects the current playback/scrub
        moment — so the cursor on the delta curve and the boxed number always agree."""
        lap_id = self.lap_at_time(t)
        if lap_id is None:
            return None
        best = self.best_lap_id()
        if best is None:
            return None
        td = self._lap_time_dist(lap_id)
        best_td = self._lap_time_dist(best)
        if td is None or best_td is None:
            return None
        times, dists = td
        if float(dists[-1]) <= 0:
            return None
        s = float(np.interp(t, times, dists)) / float(dists[-1])  # normalized fraction [0,1]
        elapsed_lap = float(np.interp(t, times, times - times[0]))  # = t − lap_start, clamped
        best_times, best_dists = best_td
        best_total = float(best_dists[-1])
        if best_total <= 0:
            return None
        # Best lap's elapsed time at the SAME track fraction s (invert s→best distance→time).
        best_elapsed_at_s = float(
            np.interp(s * best_total, best_dists, best_times - best_times[0])
        )
        return elapsed_lap - best_elapsed_at_s

    def nearest_index(self, x: float, y: float) -> int | None:
        if len(self.tx) == 0:
            return None
        return int(np.argmin((self.tx - x) ** 2 + (self.ty - y) ** 2))

    # ----------------------------------------------- map marker: lap-scoped nearest (F3)
    # The red map marker is draggable; dragging seeks the video. Searching the WHOLE trace for
    # the nearest point makes the marker JUMP to another lap wherever the laps overlap
    # spatially. So constrain the search to the CURRENT lap's own trace — the same lap-scoped
    # behaviour as the scrub cursor. Pure numpy on the lap's cached local-metre points; no pacer.
    def _lap_xy_t(self, lap_id: int):
        """Cached (xs, ys, times) for one lap in local metres + media-clock seconds. Reuses the
        per-lap cache so the marker drag is a cheap O(n_lap) nearest-point lookup, not a rebuild."""
        td = self._lap_time_dist(lap_id)  # ensures the lap is segmented/usable
        if td is None:
            return None
        xs, ys, ts = self._lap_trace_xyt(lap_id)
        if len(xs) < 1:
            return None
        return xs, ys, ts

    def nearest_index_in_lap(self, lap_id: int, x: float, y: float) -> int | None:
        """Index (into `lap_id`'s OWN point array) of the trace point nearest (x, y), searching
        ONLY within that lap. Returns None if the lap is degenerate. Pure numpy — used to keep
        the dragged map marker on the current lap instead of snapping across spatial overlaps."""
        got = self._lap_xy_t(lap_id)
        if got is None:
            return None
        xs, ys, _ = got
        return int(np.argmin((xs - x) ** 2 + (ys - y) ** 2))

    def nearest_time_in_lap(self, lap_id: int, x: float, y: float) -> float | None:
        """Media-clock time (s) of the point within `lap_id` nearest (x, y), CLAMPED to the lap's
        [start, end] window. The map marker uses this so a drag scrubs smoothly inside the one
        lap and never jumps to another lap. None if the lap is degenerate."""
        i = self.nearest_index_in_lap(lap_id, x, y)
        if i is None:
            return None
        _, _, ts = self._lap_xy_t(lap_id)
        return float(min(max(ts[i], ts[0]), ts[-1]))
