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

from . import tracks

DEFAULT_SAMPLE = "3rdparty/gpmf-parser/samples/hero6.mp4"  # a clip with real motion

# Data-cleaning thresholds (see studio/diagnose.py; validated on real sessions).
MIN_START_SPEED = 3.0  # m/s — below this the car is stationary / GPS not yet locked
SPIKE_STEP = 50.0  # m — a lone fix farther than this from BOTH neighbours is a glitch
OFF_TRACK_MARGIN = 0.5  # drop points outside the inlier bbox (1-99 pct) expanded by this fraction
START_WIDEN = 3.0  # widen the auto start line so every lap pass crosses it
MIN_LAP_TIME = 5.0  # s — laps shorter than this are partial/phantom, not real laps
MIN_LAP_SAMPLES = 20  # a real lap has at least this many GPS samples
LAP_BAND_LO, LAP_BAND_HI = 0.5, 1.6  # "real lap" = lap_time within [lo, hi] x median lap time


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


class Session:
    def __init__(self, laps: pacer.Laps, cs, video_path: str | None):
        self.laps = laps
        self.cs = cs
        self.video_path = video_path
        self._lap_cache: dict[int, object] = {}
        # Per-lap (times, dists) arrays for distance_in_lap_at_time — rebuilding these from the
        # bound lap object every ~30 Hz cursor tick is wasteful; cache and clear on re-segment.
        self._dist_cache: dict[int, tuple] = {}

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
    def load(cls, paths: list[str], interpolate: bool = False) -> "Session":
        """Naive timing by default — the C++ interpolation diverges on long/noisy sessions
        (see studio/diagnose.py); `interpolate=True` enables it but the result is validated
        and falls back to naive if it's non-monotonic or runs past the video duration."""
        laps = pacer.Laps()
        empty = pacer.CoordinateSystem(pacer.GPSSample())
        video_path = paths[0] if paths else None
        if not paths:
            return cls(laps, empty, None)

        samples, spans, naive = _read_gpmf(paths)
        samples, spans, naive = _clean(samples, spans, naive)
        if not samples:
            return cls(laps, empty, video_path)

        times = list(naive)
        if interpolate and len(samples) >= 8:
            times = cls._interpolated_or_naive(samples, spans, naive)

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

    def suggest_sector(self) -> Seg:
        """A short line perpendicular to the trace at ~1/4 of the way round."""
        n = len(self.tx)
        if n < 4:
            return Seg(0, 0, 0, 0)
        i = n // 4
        j = min(i + 5, n - 1)
        dx, dy = self.tx[j] - self.tx[i], self.ty[j] - self.ty[i]
        length = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / length, dx / length
        cx, cy = self.tx[i], self.ty[i]
        return Seg(cx - nx * 5, cy - ny * 5, cx + nx * 5, cy + ny * 5)

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

    def lap_window(self, lap_id: int) -> tuple[float, float] | None:
        if not (0 <= lap_id < self.laps.laps_count()):
            return None
        t0 = self.laps.start_timestamp(lap_id)
        return (t0, t0 + self.laps.lap_time(lap_id))

    def lap_sector_splits(self, lap_id: int) -> list[float]:
        """Per-sub-sector split times (seconds) for a lap, in order. With N sector lines a
        lap has N+1 sub-sectors and these sum to the lap time. Mapped by TIMESTAMP, not the
        C++ flat sector index — the partial out-lap makes that index off-by-one."""
        t0 = self.laps.start_timestamp(lap_id)
        t1 = t0 + self.laps.lap_time(lap_id)
        return [self.laps.sector_time(x) for x in range(self.laps.recorded_sectors())
                if t0 - 1e-6 <= self.laps.sector_start_timestamp(x) < t1 - 1e-6]

    def distance_in_lap_at_time(self, lap_id: int, t: float) -> float | None:
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
        times, dists = td
        return float(np.interp(t, times, dists))

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

    def delta(self, lap_ids):
        """Returns (best_lap_id, speed_series, delta_series).

        Always references the GLOBAL best lap, so a single selected lap still shows a
        meaningful delta-to-best (not a trivial flat zero). Uses ARC-LENGTH interpolation:
        each lap is sampled onto a shared cumulative-distance grid via `np.interp`, so
        every selected lap spans the FULL distance range (no dependence on the C++
        `resample`, which truncated non-best laps at the first missed timing line).

        The grid is uniform 0..max over the selected laps (NOT the best lap's odometer):
        the best lap can be the SHORTEST by distance. Each lap is then masked to its OWN
        distance so it never flat-clamps past its end; delta spans the overlap with best.
        """
        ids = [i for i in lap_ids if 0 <= i < self.laps.laps_count()]
        best = self.best_lap_id()
        if not ids or best is None:
            return None

        arrays = {}
        for lid in set(ids) | {best}:
            dist, speed_kmh, elapsed = self._lap_arrays(lid)
            if len(dist) >= 2:
                arrays[lid] = (dist, speed_kmh, elapsed)
        if best not in arrays:
            return None

        # Shared grid up to the longest selected lap's distance so each lap spans its full
        # own distance (best lap may be the shortest; np.interp clamps past the right edge).
        max_dist = max(dist[-1] for dist, _, _ in arrays.values())
        grid = np.linspace(0.0, float(max_dist), num=max(len(arrays[best][0]), 2))

        best_dist, _, best_elapsed = arrays[best]

        speed, delta = {}, {}
        for lid, (dist, speed_kmh, elapsed) in arrays.items():
            # Mask each lap to its OWN distance so a shorter lap doesn't flat-clamp across the
            # grid (np.interp holds the last value past the right edge): speed spans the lap's
            # own range; delta spans the overlap with the best lap (where both have data).
            own = grid <= dist[-1]
            speed[lid] = (grid[own], np.interp(grid[own], dist, speed_kmh))
            both = grid <= min(dist[-1], best_dist[-1])
            delta[lid] = (grid[both],
                          np.interp(grid[both], dist, elapsed)
                          - np.interp(grid[both], best_dist, best_elapsed))
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

    def nearest_index(self, x: float, y: float) -> int | None:
        if len(self.tx) == 0:
            return None
        return int(np.argmin((self.tx - x) ** 2 + (self.ty - y) ** 2))
