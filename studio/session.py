"""Session: the loaded telemetry session — UI-friendly accessors over a `pacer.Laps`.

All the C++ analysis (lap/sector segmentation, distances, crossing-instant lap timing,
lap-vs-best delta resampling) is reused via the bound `pacer` module. This file only adds
the thin Python glue around it: building plot series, the delta computation, and writing
dragged timing lines back to the core. The LOAD pipeline itself (GPS9 true-clock time axis,
trace cleaning/smoothing, start-line placement) lives in studio/load.py; `Session.load`
below stays the public entry point and delegates to it.

Coordinate note (verified against pacer/laps/laps.cpp): the track trace and the timing
lines both live in LOCAL meters (cs.local). `pick_random_start()`/`update()` must run
AFTER `set_coordinate_system()`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import TypedDict

import numpy as np

import pacer

from . import chapters, gapfill, gmeter, tracks

# Pacer-free helpers from studio/_signal.py (numpy-only, shared with gmeter): the band filter
# behind valid_lap_ids and the default smoothing window `Session.load` forwards to the pipeline.
# `fmt_time` moved to _signal.py (pacer-free home); re-exported here for compatibility.
from ._signal import (
    SMOOTH_WINDOW,
    _band_lap_ids,
    fmt_time,  # noqa: F401  (re-export for call sites; lives in _signal now)
)

# The load pipeline (quality gate -> clean -> GPS9 time axis -> smooth -> segment + start-line
# fit) lives in studio/load.py; `Session.load` wraps its result in a Session.
from .load import load_recording

DEFAULT_SAMPLE = "3rdparty/gpmf-parser/samples/hero6.mp4"  # a clip with real motion

_UNSET = object()  # sentinel for "cache not yet computed" where None is a valid cached value

# The five index-aligned per-lap columns `_lap_columns` caches: (times, xs, ys,
# full_speed m/s, cum_distances), one bulk pacer.Laps.lap_columns crossing per lap.
LapColumns = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
# One (x, y) plot series per lap id — the speed/delta payloads `delta()` returns.
LapSeries = dict[int, tuple[np.ndarray, np.ndarray]]


class LapRow(TypedDict):
    """One `lap_rows()` row — the lap id + the lap-level metrics the lap table shows."""

    idx: int      # lap id
    time: float   # lap time (s)
    dist: float   # lap distance (m)
    entry: float  # entry speed (km/h)


@dataclass
class Seg:
    """A timing line in LOCAL meters: two endpoints (x1,y1)-(x2,y2)."""

    x1: float
    y1: float
    x2: float
    y2: float

    @classmethod
    def from_pacer(cls, s) -> Seg:
        return cls(s.first.x, s.first.y, s.second.x, s.second.y)

    def to_pacer(self):
        return tracks.make_segment(self.x1, self.y1, self.x2, self.y2)


class Session:
    def __init__(self, laps: pacer.Laps, cs, video_path: str | None,
                 chapter_map: chapters.ChapterMap | None = None):
        self.laps = laps
        self.cs = cs
        self.video_path = video_path
        # The ordered chapter list + cumulative offsets (global<->chapter time mapping). Always
        # present for a real load (single chapter => a one-entry map). `None` only for an empty
        # session. The telemetry trace already lives on the map's continuous global clock; this
        # is what the VIDEO layer uses to switch sources / span the slider across chapters.
        self.chapters = chapter_map
        # Per-lap bulk columns (times, xs, ys, full_speed m/s, cum_distances) fetched in ONE
        # pacer.Laps.lap_columns crossing instead of a per-point cs.local/full_speed/time loop.
        # The single source _lap_trace_xyt / _lap_time_dist_elapsed / _lap_arrays /
        # sector_boundary_distances all slice from; cleared on re-segment. Built once per lap.
        self._cols_cache: dict[int, LapColumns] = {}
        # Per-lap (times, dists, elapsed) arrays for _lap_time_dist (the cursor/video x<->time
        # conversions) — rebuilding these from the bound lap object every ~30 Hz cursor tick is
        # wasteful; cache and clear on re-segment. `elapsed` (= times - times[0]) is precomputed
        # once so the per-tick delta math doesn't re-subtract every call. (Unit tests may seed
        # a legacy (times, dists) 2-tuple; _lap_time_dist_elapsed upgrades it in place.)
        self._dist_cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        # Per-lap (xs, ys, times) local-metre arrays (map highlight + marker-drag nearest). Built
        # once per lap (an O(n_lap) cs.local pass); cleared on re-segment. Killing the double
        # rebuild the marker drag used to do per mouse-move.
        self._xyt_cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        # Per-lap gap-filled draw segments (measured + inferred runs). MAP RENDERING ONLY —
        # computed once per lap on first draw, never per frame; cleared on re-segment.
        self._seg_cache: dict[int, list[gapfill.Segment]] = {}
        # Memoized "real lap" sets — the 30 Hz tick resolves valid_lap_ids()/best_lap_id() many
        # times each frame (lap_at_time, delta_at_time, the readout, the map/table highlights).
        # Computed once and reused; cleared on re-segment (the only point they can change).
        self._valid_cache: list[int] | None = None
        self._best_cache: object = _UNSET   # sentinel: None is a legal "no best lap" result
        # Cached lap [start, end) windows on the GLOBAL clock for the O(log n) lap_at_time binary
        # search (parallel arrays over valid laps, in start order). Cleared on re-segment.
        self._lap_windows: tuple[np.ndarray, np.ndarray, list[int]] | None = None
        self._reference_xy = None  # lazily-built georeferenced track centerline (fallback donor)
        # The detected registry track's name (tracks.detect_track), or None for an unknown
        # track (where the start line was auto-fitted via pick_random_start). Set by load();
        # a from-scratch Session() has no detection, so it stays None. Persisted into the
        # timing-line sidecar and used by the app's "unknown track" notice.
        self.track_name: str | None = None
        # Vehicle-frame g (lateral/longitudinal in g) precomputed from the GoPro ACCL+GRAV+CORI,
        # cross-checked against GPS-derived g. Built in load() (needs the trace arrays below);
        # an empty meter until then, so a from-scratch Session() (no IMU) just has no g signal.
        self._gmeter: gmeter.GMeter = gmeter._empty()

        # Full-trace arrays in local meters + the video-clock time + speed (km/h), from ONE
        # pacer.Laps.track_columns crossing (the same bulk idiom as _lap_columns; was a
        # per-point get_point/cs.local loop — one binding crossing per point, ~16k+ per load).
        # Byte-identical: cs.Local in C++ is the laps' own coordinate system, which is `cs`
        # (== self.cs) on the load path, and full_speed is widened float->double identically
        # in both paths before the km/h scale.
        cols = laps.track_columns()
        self.tx = np.asarray(cols.xs)
        self.ty = np.asarray(cols.ys)
        self.tt = np.asarray(cols.times)
        self.tv = np.asarray(cols.full_speed) * 3.6

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, paths: list[str], smooth_window: int = SMOOTH_WINDOW) -> Session:
        """The public load entry point — delegates the pipeline to `load.load_recording`
        (single-pass ingest → quality gate → clean → GPS9 true-clock time axis → boxcar
        smoothing → segmentation + start-line fit; see studio/load.py for the WHYs), then
        wraps the result in a Session.

        True-clock timing — the per-sample time comes from the GPS9 fix timestamps' real
        10 Hz spacing, re-anchored per run to the media clock (see `load._gps9_times`).
        The GPS track is quality-gated and boxcar-smoothed (window `smooth_window`, default
        SMOOTH_WINDOW) BEFORE the points are handed to the core; `smooth_window=1` disables
        smoothing (raw trace, for baselines)."""
        laps, cs, video_path, chapter_map, imu, track_name = load_recording(paths, smooth_window)
        session = cls(laps, cs, video_path, chapter_map)
        # The registry track the pipeline detected (or None for an unknown track) — persisted
        # into the timing-line sidecar and shown by the app's "unknown track" notice.
        session.track_name = track_name
        if imu is not None:
            # Vehicle-frame g from the real GoPro accelerometer, cross-checked vs GPS-derived
            # g. The ACCL/GRAV/CORI were read off load_recording's single-pass chain (no second
            # MP4 open). Built here — _build_gmeter reads the instance trace arrays — and cached.
            session._build_gmeter(*imu)
        return session

    def _build_gmeter(self, accl, grav, cori) -> None:
        """Precompute the vehicle-frame g(t) series from the ALREADY-READ GoPro IMU streams
        (accl/grav/cori, read once off the single-pass ingest chain in `load`), aligned to the
        SAME smoothed GPS trace + media clock the rest of the session uses (so it syncs to the
        video and respects chapter offsets). Reports the ACCL-vs-GPS cross-check once at load.
        Degrades silently to an empty meter if the transform fails — the overlay just shows no
        data, and nothing else in the session is affected (the IMU is additive)."""
        try:
            # Per-chapter alignment spans: CORI is referenced to each chapter's own capture start,
            # so the camera->ENU yaw must be fit independently per chapter. Build (start, end)
            # global spans from the chapter offset table (single chapter => one full span).
            seg_bounds = None
            if self.chapters is not None and len(self.chapters.chapters) > 1:
                chs = self.chapters.chapters
                seg_bounds = [(c.offset, chs[i + 1].offset if i + 1 < len(chs) else c.offset + 1e9)
                              for i, c in enumerate(chs)]
            # The GPS trajectory for the cross-check + heading is the session's own smoothed
            # trace: local-metre east/north (tx,ty), media time (tt), speed in m/s (tv is km/h).
            self._gmeter = gmeter.compute(
                accl, grav, cori,
                gps_t=self.tt, gps_x=self.tx, gps_y=self.ty, gps_speed=self.tv / 3.6,
                segment_bounds=seg_bounds)
        except Exception as e:  # noqa: BLE001 — IMU is additive; never break a load over it
            print(f"studio: g-meter build failed ({e!r}); g-meter disabled.", flush=True)
            return
        gm = self._gmeter
        if gm.cross is not None:
            print(f"studio: {gm.cross.summary()}", flush=True)
            if gm.source == "gps":
                print("studio: ACCL g looked unreliable — using GPS-derived g for the meter.",
                      flush=True)
        elif gm.has_data:
            print(f"studio: g-meter using {gm.source}-derived g "
                  f"({len(gm)} samples, no cross-check).", flush=True)

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
        self._cols_cache.clear()
        self._dist_cache.clear()
        self._xyt_cache.clear()
        self._seg_cache.clear()
        # The single re-segmentation point: every memoized "real lap" set + window table is now
        # stale (lap ids / times shifted), so drop them. They lazily recompute on next access.
        self._valid_cache = None
        self._best_cache = _UNSET
        self._lap_windows = None

    # ------------------------------------------ timing-line persistence (sidecar glue)
    # The sidecar (studio/sidecar.py, pacer-free) stores the user's timing lines as ABSOLUTE
    # (lat, lon) endpoints, because the LOCAL frame's origin is the cleaned-trace bbox centre
    # (see load()) and shifts between loads — persisted local metres would drift. These two
    # helpers own the lat/lon <-> local conversion via the bound CoordinateSystem, so the
    # sidecar module (and app.py) never touch pacer.

    def timing_lines_latlon(self) -> tuple[list, list]:
        """The current start + sector lines as absolute (lat, lon) endpoint pairs — the
        sidecar's persisted form: ``(start, sectors)`` where each line is
        ``[[lat, lon], [lat, lon]]``. Endpoints map through ``cs.global_`` at z=0 (the
        timing lines are 2D in the local plane; altitude is irrelevant to a crossing)."""
        def line(seg: Seg) -> list:
            out = []
            for x, y in ((seg.x1, seg.y1), (seg.x2, seg.y2)):
                g = self.cs.global_(pacer.Vec3f(float(x), float(y), 0.0))
                out.append([float(g.lat), float(g.lon)])
            return out
        return line(self.start_line), [line(s) for s in self.sector_lines]

    def apply_timing_lines_latlon(self, start, sectors) -> bool:
        """Apply persisted absolute-lat/lon timing lines (the sidecar's form): convert each
        endpoint to local metres via ``cs.local`` and re-segment through set_timing_lines.

        REVERT GUARD: if the new segmentation yields NO valid laps — a corrupt sidecar, or
        one written for a different recording/track whose lines never cross this trace —
        the previous (auto-fitted) lines are restored and False is returned, so a bad
        sidecar can never silently destroy the session's lap segmentation."""
        prev_start, prev_sectors = self.start_line, self.sector_lines

        def seg(pair) -> Seg:
            (a_lat, a_lon), (b_lat, b_lon) = pair
            a = self.cs.local(pacer.GPSSample(lat=float(a_lat), lon=float(a_lon), altitude=0))
            b = self.cs.local(pacer.GPSSample(lat=float(b_lat), lon=float(b_lon), altitude=0))
            return Seg(float(a[0]), float(a[1]), float(b[0]), float(b[1]))

        self.set_timing_lines(seg(start), [seg(s) for s in sectors])
        if self.valid_lap_ids():
            return True
        self.set_timing_lines(prev_start, prev_sectors)
        return False

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
    def _lap_columns(self, lap_id: int) -> LapColumns:
        """Cached per-lap (times, xs, ys, full_speed_mps, cum_distances) numpy arrays, fetched in
        a SINGLE pacer.Laps.lap_columns crossing (was a per-point cs.local/full_speed/time loop).
        local metres + media-clock seconds + raw 3D speed (m/s) + the lap's gap-aware odometer,
        all index-aligned and the SAME length (the materialized lap: interpolated start crossing +
        interior track points + interpolated finish crossing). Cleared on re-segment.

        Byte-identical to the former per-element builders: cs (the laps' coordinate system) is
        `self.cs` on the load path, so cs.Local in C++ matches the Python cs.local(p.point), and
        cum_distances is moved straight off GetLap()'s — see Laps::LapColumns."""
        cols = self._cols_cache.get(lap_id)
        if cols is None:
            c = self.laps.lap_columns(lap_id)
            cols = (np.asarray(c.times), np.asarray(c.xs), np.asarray(c.ys),
                    np.asarray(c.full_speed), np.asarray(c.cum_distances))
            self._cols_cache[lap_id] = cols
        return cols

    def lap_count(self) -> int:
        return self.laps.laps_count()

    def lap_time(self, lap_id: int) -> float:
        """Lap time (seconds) for a lap id — thin pacer-free accessor so view modules
        (lap_table, app) read lap times through Session, not the pacer binding directly."""
        return self.laps.lap_time(lap_id)

    def sector_count(self) -> int:
        """Number of sector lines on the laps (0 by default). Thin pacer-free accessor so
        view modules read the sector count through Session, not the pacer binding."""
        return self.laps.sector_count()

    def point_count(self) -> int:
        """Total GPS point count across the recording. Thin pacer-free accessor used by the
        app's startup log so it needn't reach into the pacer binding."""
        return self.laps.point_count()

    def valid_lap_ids(self) -> list[int]:
        """Real laps only. A fixed threshold is too crude (short double-crossings of the
        start line pass it and pollute the 'best' lap), so accept laps whose time is within
        a band around the MEDIAN lap time — this adapts to any track length.

        Memoized — the 30 Hz tick (lap_at_time, the highlights, delta) hits this many times per
        frame and the result only changes on re-segmentation (cleared in set_timing_lines). The
        gate+median+band filter itself is single-sourced in `_signal._band_lap_ids` (shared with
        load.py's load-time `_band_lap_count`)."""
        if self._valid_cache is not None:
            return self._valid_cache
        self._valid_cache = _band_lap_ids(self.laps)
        return self._valid_cache

    def best_lap_id(self) -> int | None:
        """The valid lap with the fastest time, else None. Memoized (same lifetime as
        valid_lap_ids; cleared on re-segment) — resolved several times per tick."""
        if self._best_cache is not _UNSET:
            return self._best_cache
        valid = self.valid_lap_ids()
        self._best_cache = min(valid, key=self.laps.lap_time) if valid else None
        return self._best_cache

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

    def lap_rows(self) -> list[LapRow]:
        return [
            {
                "idx": i,
                "time": self.laps.lap_time(i),
                "dist": self.laps.get_lap_distance(i),
                "entry": self.laps.lap_entry_speed(i) * 3.6,
            }
            for i in self.valid_lap_ids()
        ]

    def _lap_point_times(self, lap_id: int) -> np.ndarray:
        """The media-clock times of a lap's KEPT GPS points, in order. Quality-gated /
        cleaned samples have already been removed at load, so a large delta between two
        consecutive entries here is a real interior GPS dropout (not jitter).

        The times column of the cached bulk `_lap_columns` fetch — index-identical to the
        former per-point `lap.points` materialization (Laps::LapColumns reuses GetLap, see
        laps.cpp), so no separate get_lap crossing/cache is needed."""
        return self._lap_columns(lap_id)[0]

    def lap_has_dropout(self, lap_id: int) -> bool:
        """True if a lap's kept-point times contain an INTERIOR gap — a delta between two
        consecutive samples larger than the gap threshold (gapfill.GAP_TIME_S = 0.35 s, the
        same threshold the gap-aware draw logic uses). Such a lap had a GPS dropout, so its
        time / distance / map are less reliable. Read-only; alters no analysis value."""
        return bool(gapfill.find_gaps(self._lap_point_times(lap_id)))

    def dropout_lap_ids(self) -> set[int]:
        """The set of VALID lap ids whose trace has an interior GPS dropout (see
        `lap_has_dropout`). The lap table flags these as low-confidence so the user knows the
        timing / distance / map for that lap is less reliable. Pure read-only helper —
        the views stay pacer-free, so they consume this flag via the app."""
        return {lap_id for lap_id in self.valid_lap_ids() if self.lap_has_dropout(lap_id)}

    def lap_trace_xy(self, lap_id: int):
        """Local-meter (xs, ys) of a single lap's trace, for highlighting on the map."""
        xs, ys, _ = self._lap_trace_xyt(lap_id)
        return xs, ys

    # ------------------------------------------------- map gap-fill (rendering only)
    def _lap_trace_xyt(self, lap_id: int):
        """Cached per-lap (xs, ys, times) as numpy arrays — local metres + media-clock seconds,
        built once with a single cs.local pass over the lap's kept points (cleared on re-segment).
        The single source the map highlight, the gap-fill draw, and the marker-drag nearest-point
        lookup all slice from, so a marker drag no longer rebuilds these on every mouse-move."""
        got = self._xyt_cache.get(lap_id)
        if got is None:
            # One bulk crossing instead of a per-point cs.local loop: the local-metre xs/ys and the
            # media-clock times come straight off pacer.Laps.lap_columns (cs.Local in C++ == the
            # former self.cs.local(p.point); see _lap_columns).
            times, xs, ys, _full_speed, _cum = self._lap_columns(lap_id)
            got = (xs, ys, times)
            self._xyt_cache[lap_id] = got
        return got

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
        self._reference_xy = reference.centerline_local(agg)
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
        segs, _fills = gapfill.reconstruct_lap(xs, ys, ts, donors, med_dt=self._median_sample_dt())
        self._seg_cache[lap_id] = segs
        return segs

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
        # times + cum_distances from the one bulk lap_columns crossing (both length lap.count(),
        # so the former m = min(len(points), len(cum_distances)) is just that length).
        times, _xs, _ys, _full_speed, cum = self._lap_columns(lap_id)
        m = min(len(times), len(cum))
        if m < 2:
            return []
        cum_distance = cum[:m]
        elapsed = times[:m] - times[0]

        # Each sector line's lap distance = cum_distance of the lap point nearest its midpoint —
        # single-sourced (and already sorted ascending) via sector_boundary_distances, so the
        # boundary guide lines (F2) sit exactly where these splits are measured.
        bounds = self.sector_boundary_distances(lap_id)

        total = float(cum_distance[-1])
        # Boundaries plus lap start/finish: N+1 sub-sectors. interp elapsed at each.
        edges = [0.0, *bounds, total]
        t_at = np.interp(edges, cum_distance, elapsed)
        splits = [float(t_at[k + 1] - t_at[k]) for k in range(n_splits)]
        return splits

    def sector_boundary_distances(self, lap_id: int) -> list[float]:
        """Per-lap odometer distance (metres) of each sector line, found the SAME way
        `lap_sector_splits` measures the splits: project each sector line's midpoint onto this
        lap's trace and take the nearest point's cum_distance. Sorted ascending. So the boundary
        guide lines on the charts (F2) land exactly where the split times are measured."""
        lines = self.laps.sectors.sector_lines
        if not lines:
            return []
        # Local-metre xs/ys + cum_distances from the one bulk lap_columns crossing (replacing the
        # per-point self.cs.local loop); all length lap.count(), so m is just that length.
        _times, xs, ys, _full_speed, cum = self._lap_columns(lap_id)
        m = min(len(xs), len(cum))
        if m < 2:
            return []
        xs, ys, cum = xs[:m], ys[:m], cum[:m]
        bounds = []
        for seg in lines:
            mx = (seg.first.x + seg.second.x) / 2.0
            my = (seg.first.y + seg.second.y) / 2.0
            j = int(np.argmin((xs - mx) ** 2 + (ys - my) ** 2))
            bounds.append(float(cum[j]))
        return sorted(bounds)

    def sector_plot_positions(self, mode: str) -> list[tuple[str, float]]:
        """(label, plot-x) for the sector BOUNDARIES on the speed+delta charts' SHARED axis (F2).

        Includes the start/finish ("S/F", x=0) plus one line per sector. Positions are taken on
        the GLOBAL best lap — the reference the distance axis is scaled to — using the same
        midpoint→trace projection as the split times, so the guide lines sit exactly where the
        splits are measured and align with the curves. Respects the dist/time toggle:
          * 'distance': x = (d_k / lap_total) × best_distance  (the s×best_distance axis)
          * 'time':     x = elapsed-into-best-lap at d_k        (seconds)
        Returns [] if there's no best lap (so the caller clears the lines)."""
        # No sector lines placed → no guide lines (the chart x-origin already marks the lap
        # start; a lone S/F line would be redundant). "Reset sectors" therefore clears them.
        if not self.laps.sectors.sector_lines:
            return []
        best = self.best_lap_id()
        if best is None:
            return []
        td = self._lap_time_dist(best)
        if td is None:
            return []
        times, dists = td
        total = float(dists[-1])
        if total <= 0:
            return []
        bounds = self.sector_boundary_distances(best)
        # Start/finish first, then the sector lines in track order.
        positions: list[tuple[str, float]] = []
        labels = ["S/F"] + [f"S{i + 1}" for i in range(len(bounds))]
        edge_dists = [0.0, *bounds]
        if mode == "time":
            t0 = float(times[0])
            for label, d in zip(labels, edge_dists, strict=True):
                t_at = float(np.interp(d, dists, times)) - t0  # elapsed into the best lap
                positions.append((label, t_at))
        else:  # 'distance' — the shared s×best_distance axis (here best_distance == total)
            for label, d in zip(labels, edge_dists, strict=True):
                positions.append((label, d))
        return positions

    def _lap_time_dist(self, lap_id: int):
        """Cached (times, dists) for a lap: media-clock seconds + per-lap odometer (metres),
        both monotonic and aligned. The single source the cursor↔video conversions interpolate
        on — built once per lap, cleared on re-segment. Returns None if the lap is degenerate."""
        td = self._lap_time_dist_elapsed(lap_id)
        if td is None:
            return None
        times, dists, _elapsed = td
        return times, dists

    def _lap_time_dist_elapsed(self, lap_id: int):
        """Cached (times, dists, elapsed) for a lap. `elapsed` (= times - times[0]) is computed
        ONCE here so the per-tick delta math (delta_at_time / delta_between, each ~30 Hz) reads it
        instead of re-subtracting times[0] every call. Cleared on re-segment. None if degenerate."""
        td = self._dist_cache.get(lap_id)
        if td is None:
            # times + cum_distances come from the one bulk lap_columns crossing (both have length
            # lap.count(), so the former m = min(count, len(cum_distances)) is just that length).
            # Slice defensively to the shorter of the two to keep the exact <2-degenerate guard.
            all_times, _xs, _ys, _full_speed, cum = self._lap_columns(lap_id)
            m = min(len(all_times), len(cum))
            if m < 2:
                return None
            times = all_times[:m].copy()
            dists = cum[:m].copy()
            elapsed = times - times[0]
            td = (times, dists, elapsed)
            self._dist_cache[lap_id] = td
        return td

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

        `dist`/`elapsed` are reused from the cached `_lap_time_dist_elapsed` (the same per-lap
        odometer + seconds-from-start the cursor↔video conversions interpolate on), so they're
        built once per lap. `speed_kmh` is the bulk `full_speed` column (m/s) scaled to km/h — the
        whole row set comes from the one `lap_columns` crossing (`_lap_columns`), replacing the
        former per-point `pts[i].point.full_speed` loop. A degenerate lap (<2 points, where the
        shared cache returns None) falls back to the same short arrays as before — `delta()`, the
        sole caller, filters those out with its `len(dist) >= 2` check."""
        all_times, _xs, _ys, full_speed, cum = self._lap_columns(lap_id)
        td = self._lap_time_dist_elapsed(lap_id)
        if td is None:  # <2 points: reproduce the original short-array output exactly
            m = min(len(cum), len(all_times))
            dist = cum[:m].copy()
            speed_kmh = full_speed[:m] * 3.6
            t0 = all_times[0] if m else 0.0
            elapsed = all_times[:m] - t0
            return dist, speed_kmh, elapsed
        _times, dist, elapsed = td
        m = len(dist)
        speed_kmh = full_speed[:m] * 3.6
        return dist, speed_kmh, elapsed

    _DELTA_GRID_N = 400  # samples on the normalized-distance grid (smooth + cheap to render)

    def delta(self, lap_ids, x_mode: str = "distance") -> tuple[int, LapSeries, LapSeries] | None:
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

    def _lap_window_table(self):
        """Cached parallel arrays (starts, ends, lap_ids) over the VALID laps, sorted by start
        time, for the O(log n) `lap_at_time` binary search. Built once per (re)segment and cleared
        in set_timing_lines. `starts`/`ends` are the same [start_timestamp, start+lap_time) windows
        the old linear scan tested, so the resolved lap id is identical."""
        # getattr so a bare Session built via __new__ (unit tests) still resolves — it just
        # recomputes each call instead of caching (no __init__ ran to create the slot).
        if getattr(self, "_lap_windows", None) is None:
            valid = self.valid_lap_ids()
            # The [start, start+lap_time) window definition is single-sourced in lap_window (each
            # valid id is in range, so it never returns None). lap_at_time's binary search runs on
            # this cached table, so the window it tests against is exactly lap_window's.
            rows = [(*self.lap_window(i), i) for i in valid]
            rows.sort(key=lambda r: r[0])  # by start time (valid is already id-ascending => time-ascending)
            starts = np.array([r[0] for r in rows], dtype=float)
            ends = np.array([r[1] for r in rows], dtype=float)
            ids = [r[2] for r in rows]
            self._lap_windows = (starts, ends, ids)
        return self._lap_windows

    def lap_at_time(self, t: float) -> int | None:
        """The valid lap whose [start_timestamp, start_timestamp+lap_time) window contains
        `t` (media-clock seconds), else None — for the readout + current-lap highlight.

        The upper bound is HALF-OPEN (`t < end`) on purpose: consecutive laps are contiguous
        (lap N's finish timestamp == lap N+1's start), so an inclusive upper bound made a `t`
        exactly on a lap's START resolve to the PREVIOUS lap (whose window also ends there).
        That is precisely the time produced by selecting a lap — `start_timestamp(lap)` — so the
        select→seek→auto-follow chain would jump the highlight/charts back one lap. Half-open ties
        the shared boundary to the lap that STARTS at `t` (the one the user actually picked). The
        sole side-effect — the exact finish instant of the LAST lap resolving to None — is a
        harmless between-laps moment that auto-follow simply HOLDS through.

        O(log n) binary search on the cached, start-sorted window table (was a linear scan every
        tick). `searchsorted(starts, t, "right") - 1` is the candidate window whose start is the
        greatest start <= t; it contains `t` iff `t < end`. Identical result to the linear scan
        (the windows are the same; half-open `t < end` keeps the on-a-start tie with the lap that
        STARTS at t)."""
        starts, ends, ids = self._lap_window_table()
        if len(starts) == 0:
            return None
        k = int(np.searchsorted(starts, t, side="right")) - 1
        if k < 0:
            return None
        if starts[k] <= t < ends[k]:
            return ids[k]
        return None

    def speed_at_time(self, t: float) -> float | None:
        """Speed (km/h) at media-clock time `t`, from the nearest trace sample, else None."""
        i = self.index_at_time(t)
        if i is None:
            return None
        return float(self.tv[i])

    def g_at_time(self, t: float) -> tuple[float, float, float] | None:
        """Vehicle-frame g at media-clock time `t`: (lateral_g, longitudinal_g, total_g), or
        None if no g signal is available. Signs: +lateral = turning left, +longitudinal =
        accelerating (−longitudinal = braking). O(log n) lookup into the precomputed series —
        cheap enough for the 30 Hz overlay tick. The g comes from the GoPro accelerometer
        (ACCL+GRAV+CORI), transformed into the kart frame (see studio/gmeter.py)."""
        return self._gmeter.at_time(t)

    @property
    def has_gmeter(self) -> bool:
        """True if a vehicle-frame g signal was computed (IMU present and usable)."""
        return self._gmeter.has_data

    def gmeter_source(self) -> str:
        """Which sensor drives the live g signal: "accl" (the GoPro accelerometer, the default)
        or "gps" (the GPS-derived fallback, used if the IMU is absent or proved unreliable)."""
        return self._gmeter.source

    def delta_at_time(self, t: float) -> float | None:
        """Δ-to-best (seconds) at media-clock time `t`: how far ahead (−) / behind (+) the lap
        being driven at `t` is versus the GLOBAL best lap, AT THE SAME TRACK POSITION. None if
        `t` isn't inside a valid lap (lead-in / between laps) or there's no best lap.

        Consistent with the delta plot's curve (same normalized-distance alignment): find the
        lap containing `t`, take its distance fraction s = dist_in_lap(t)/lap_total, then
        Δ = elapsed_lap(s) − elapsed_best(s). At the lap finish (s=1) this equals the laptime
        difference. Drives the always-on readout box, which reflects the current playback/scrub
        moment — so the cursor on the delta curve and the boxed number always agree.

        Single-sourced through `delta_between`: vs-best is just vs an arbitrary lap where the
        arbitrary lap is the GLOBAL best, so resolve the lap containing `t` and the best lap, then
        delegate to the shared normalized-distance alignment (cross-checked equal in test_compare)."""
        lap_id = self.lap_at_time(t)
        best = self.best_lap_id()
        if lap_id is None or best is None:
            return None
        return self.delta_between(lap_id, best, t)

    def delta_at_lap(self, lap_id: int, t: float) -> float | None:
        """Δ-to-best (seconds) at media-clock time `t`, given the already-resolved `lap_id`
        containing `t`. Splits the lap resolution out of `delta_at_time` so the tick can resolve
        `lap_at_time(t)` ONCE per frame and reuse it for both the readout and the delta (the lap
        lookup is no longer done twice). Same math/result as `delta_at_time`."""
        best = self.best_lap_id()
        if best is None:
            return None
        td = self._lap_time_dist_elapsed(lap_id)
        best_td = self._lap_time_dist_elapsed(best)
        if td is None or best_td is None:
            return None
        times, dists, elapsed = td
        if float(dists[-1]) <= 0:
            return None
        s = float(np.interp(t, times, dists)) / float(dists[-1])  # normalized fraction [0,1]
        elapsed_lap = float(np.interp(t, times, elapsed))  # = t − lap_start, clamped
        best_times, best_dists, best_elapsed = best_td
        best_total = float(best_dists[-1])
        if best_total <= 0:
            return None
        # Best lap's elapsed time at the SAME track fraction s (invert s→best distance→time).
        best_elapsed_at_s = float(np.interp(s * best_total, best_dists, best_elapsed))
        return elapsed_lap - best_elapsed_at_s

    def delta_between(self, lap_a: int, lap_b: int, t_in_a: float) -> float | None:
        """Δ (seconds) of lap_a vs lap_b at the SAME track position lap_a is at time `t_in_a`:
        how far ahead (−) / behind (+) lap_a is relative to lap_b at that normalized distance.
        None if either lap is degenerate or `t_in_a` falls outside lap_a's window.

        The compare-mode "Δ vs other" badge. Mirrors `delta_at_time`'s normalized-distance
        alignment (s = distance_in_lap / lap_total_distance), but compares against an ARBITRARY
        `lap_b` instead of the hardcoded GLOBAL best: take lap_a's distance fraction s at
        `t_in_a`, then interpolate lap_b's elapsed-into-lap at the SAME fraction s and subtract.
        For `lap_b == best_lap_id()` this equals `delta_at_time(t_in_a)` (cross-checked in the
        unit test). At the finish (s=1) it is exactly lap_a's time minus lap_b's time.

        O(1) on the cached per-lap (times, dists) arrays — cheap enough for the 30 Hz tick."""
        td_a = self._lap_time_dist_elapsed(lap_a)
        td_b = self._lap_time_dist_elapsed(lap_b)
        if td_a is None or td_b is None:
            return None
        times_a, dists_a, elapsed_arr_a = td_a
        total_a = float(dists_a[-1])
        if total_a <= 0:
            return None
        # lap_a's normalized track fraction s and its own elapsed time at t_in_a (clamped to lap).
        s = float(np.interp(t_in_a, times_a, dists_a)) / total_a  # [0,1]
        elapsed_a = float(np.interp(t_in_a, times_a, elapsed_arr_a))  # = t_in_a − start
        times_b, dists_b, elapsed_arr_b = td_b
        total_b = float(dists_b[-1])
        if total_b <= 0:
            return None
        # lap_b's elapsed time at the SAME track fraction s (invert s → b's distance → time).
        elapsed_b_at_s = float(np.interp(s * total_b, dists_b, elapsed_arr_b))
        return elapsed_a - elapsed_b_at_s

    def nearest_index(self, x: float, y: float) -> int | None:
        if len(self.tx) == 0:
            return None
        return int(np.argmin((self.tx - x) ** 2 + (self.ty - y) ** 2))

    # ----------------------------------------------- map marker: lap-scoped nearest (F3)
    # The red map marker is draggable; dragging seeks the video. Searching the WHOLE trace for
    # the nearest point makes the marker JUMP to another lap wherever the laps overlap
    # spatially. So constrain the search to the CURRENT lap's own trace — the same lap-scoped
    # behaviour as the scrub cursor. Pure numpy on the lap's local-metre points; no pacer.
    def _lap_xy_t(self, lap_id: int):
        """(xs, ys, times) for one lap in local metres + media-clock seconds. Reads the shared
        per-lap cache (built once, cleared on re-segment), so a marker drag's nearest-point lookup
        no longer rebuilds the arrays on every mouse-move. Returns None if the lap is degenerate."""
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
