"""Session: the loaded telemetry session — UI-friendly accessors over a `pacer.Laps`.

All the C++ analysis (lap/sector segmentation, distances, crossing-instant lap timing,
lap-vs-best delta resampling) is reused via the bound `pacer` module. This file only adds
the thin Python glue around it: building plot series, the delta computation, and writing
dragged timing lines back to the core. The LOAD pipeline itself (GPS9 true-clock time axis,
trace cleaning/smoothing, start-line placement) lives in studio/load.py; `Session.load`
below stays the public entry point and delegates to it. The MAP-RENDERING cache cluster
(gap-filled draw segments + the reference-centerline fallback donor) lives in
studio/render_cache.py; Session keeps thin delegators so callers stay unchanged.

Coordinate note (verified against pacer/laps/laps.cpp): the track trace and the timing
lines both live in LOCAL meters (cs.local). `pick_random_start()`/`update()` must run
AFTER `set_coordinate_system()`.
"""

from __future__ import annotations

import datetime
import math
from dataclasses import dataclass
from typing import TypedDict

import numpy as np

import pacer

from . import chapters, corners, gapfill, gmeter, render_cache, tracks

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


def _unit_tangents(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-sample unit direction-of-travel of a trace (central differences, normalized;
    a zero-length step keeps a zero vector, which any heading gate then rejects). Used by
    `best_rolling_lap`'s same-direction match filter. Needs len ≥ 2 (guarded by callers)."""
    tx = np.gradient(xs)
    ty = np.gradient(ys)
    norm = np.hypot(tx, ty)
    norm[norm == 0] = 1.0
    return tx / norm, ty / norm


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
        # Memoized "real lap" sets — the 30 Hz tick resolves valid_lap_ids()/best_lap_id() many
        # times each frame (lap_at_time, delta_at_time, the readout, the map/table highlights).
        # Computed once and reused; cleared on re-segment (the only point they can change).
        self._valid_cache: list[int] | None = None
        self._best_cache: object = _UNSET   # sentinel: None is a legal "no best lap" result
        # Cached lap [start, end) windows on the GLOBAL clock for the O(log n) lap_at_time binary
        # search (parallel arrays over valid laps, in start order). Cleared on re-segment.
        self._lap_windows: tuple[np.ndarray, np.ndarray, list[int]] | None = None
        # The detected registry track's name (tracks.detect_track), or None for an unknown
        # track (where the start line was auto-fitted via pick_random_start). Set by load();
        # a from-scratch Session() has no detection, so it stays None. Persisted into the
        # timing-line sidecar and used by the app's "unknown track" notice.
        self.track_name: str | None = None
        # Vehicle-frame g (lateral/longitudinal in g) precomputed from the GoPro ACCL+GRAV+CORI,
        # cross-checked against GPS-derived g. Built in load() (needs the trace arrays below);
        # an empty meter until then, so a from-scratch Session() (no IMU) just has no g signal.
        self._gmeter: gmeter.GMeter = gmeter._empty()
        # Corner-model caches (studio/corners.py, pacer-free): the detected corner list +
        # its reference total ((corners, total_ref) tuple), the per-lap projected stats, and
        # the per-corner session-best times. All derived from the segmentation, so all three
        # are computed once and cleared together with the other per-lap caches on a
        # timing-line change (set_timing_lines — the single re-segmentation point).
        self._corner_cache: object = _UNSET            # (list[Corner], total_ref) | None
        self._corner_stats_cache: dict[int, list[corners.CornerStat]] = {}
        self._corner_bests: object = _UNSET            # list[float] (min time per corner)

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

        # The MAP-RENDERING cache cluster (per-lap gap-filled draw segments + the
        # reference-centerline fallback donor) — extracted to studio/render_cache.py
        # (pacer-free), wired with Session-bound callables over the shared per-lap caches
        # above. set_timing_lines calls invalidate() on re-segment (the perf invariant).
        self._render_cache = render_cache.LapRenderCache(
            lap_xyt=self._lap_trace_xyt,
            valid_lap_ids=self.valid_lap_ids,
            lap_has_dropout=self.lap_has_dropout,
            lap_time=self.lap_time,
            trace_times=self.tt,
        )

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
        self._render_cache.invalidate()  # per-lap draw segments (MAP RENDERING ONLY)
        # The single re-segmentation point: every memoized "real lap" set + window table is now
        # stale (lap ids / times shifted), so drop them. They lazily recompute on next access.
        self._valid_cache = None
        self._best_cache = _UNSET
        self._lap_windows = None
        # The corner model is derived from the segmentation (detected on the best lap's
        # grid, projected per lap) — stale with the rest; recomputed lazily on next access.
        self._corner_cache = _UNSET
        self._corner_stats_cache.clear()
        self._corner_bests = _UNSET

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

    # The gap-aware draw-segment computation + cache (and the reference-centerline fallback
    # donor it borrows from) live in studio/render_cache.py (LapRenderCache, pacer-free) —
    # wired in __init__ with callables over this session's cached per-lap arrays. The thin
    # delegators below keep every caller (map_view, the dev tooling) on Session;
    # set_timing_lines invalidates the segment cache on re-segment.

    def lap_trace_segments(self, lap_id: int) -> list[gapfill.Segment]:
        """Ordered list of `gapfill.Segment` for drawing this lap: measured GPS runs and
        reconstructed (inferred) fills, tagged so the renderer can dash/dim the inferred
        ones. MAP RENDERING ONLY — alters no analysis quantity. Cached per lap in the
        LapRenderCache (studio/render_cache.py); cleared on re-segment."""
        return self._render_cache.lap_trace_segments(lap_id)

    def reference_centerline_xy(self):
        """The georeferenced track centerline in LOCAL metres (an (M,2) array), or None —
        the gap-fill's last-resort donor (see LapRenderCache.reference_centerline_xy)."""
        return self._render_cache.reference_centerline_xy()

    # Private delegators kept for the dev tooling (denoise_check, build_reference).
    def _median_sample_dt(self) -> float:
        return self._render_cache.median_sample_dt()

    def _donors_for(self, lap_id: int):
        return self._render_cache.donors_for(lap_id)

    def _reference_fit_loop(self):
        return self._render_cache.reference_fit_loop()

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

    # ------------------------------------- session summaries: theoretical + rolling best (F1)
    def session_best_splits(self) -> list[float | None]:
        """The session-best (minimum) split per sub-sector COLUMN, computed independently per
        column across all VALID laps — exactly the values the lap table paints purple (F5).
        N sector lines → N+1 columns; a column with no finite data → None. Hoisted here from
        lap_table so the table's purple cells and the theoretical-best footer read ONE
        computation and can never disagree. With NO sector lines a lap is a single sub-sector
        whose split is its lap time, so the one column's best is the best lap time.

        Recomputed per call (refresh-time only, never per-tick): the inputs are the cached
        per-lap `lap_sector_splits`, so memoizing here would only add another slot to clear
        on re-segment."""
        n_splits = len(self.laps.sectors.sector_lines) + 1
        all_splits = [self.lap_sector_splits(lap_id) for lap_id in self.valid_lap_ids()]
        best: list[float | None] = []
        for i in range(n_splits):
            vals = [sp[i] for sp in all_splits if i < len(sp) and math.isfinite(sp[i])]
            best.append(min(vals) if vals else None)
        return best

    def theoretical_best(self) -> float | None:
        """The THEORETICAL BEST lap time (seconds): the sum of the session-best sector splits
        (`session_best_splits` — the purple cells), i.e. the lap you'd drive by stitching every
        best sector together. Always exactly the sum of the purple cells, because both read the
        same accessor. With no sector lines a lap is one sub-sector, so this DEGENERATES to the
        best lap time by definition (documented choice: the footer row stays meaningful before
        any sectors are placed instead of reading '—'). None when no valid laps exist or some
        column has no finite split (every lap partial there)."""
        bests = self.session_best_splits()
        if not bests or any(b is None for b in bests):
            return None
        return float(sum(bests))

    # Best-rolling nearest-point search half-width, as a fraction of the next lap's samples.
    # WHY 0.02: the anchor's matching point on lap k+1 sits near the SAME normalized fraction —
    # the two parameterizations drift apart only by the laps' line-length difference, measured
    # ≤ 4.5 m (0.4% of the ~1.06 km lap) across the real D24 sessions — so a ±2% (~21 m) search
    # arc is an order of magnitude of headroom. Floor of 5 samples keeps short synthetic laps
    # searchable.
    _ROLLING_SEARCH_FRAC = 0.02
    # Heading gate for the nearest-point match: the match must be travelled in (roughly) the
    # SAME direction as the anchor — cos(heading difference) ≥ 0.5 (within 60°). WHY: inside
    # the search arc a tight corner's legs can sit closer ACROSS the track than the two laps'
    # line offset along it; on real 0060 the unfiltered winner snapped the anchor (pair (9,10),
    # s_a 0.871) to a point 4 m away spatially but ~12 m of track EARLIER (s_b 0.860, heading
    # cos ≈ +0.24), short-changing the loop by 1.2 s. Genuine same-point matches measure
    # cos > 0.9 even with metres of lateral line offset; 0.5 splits the two decisively.
    _ROLLING_HEADING_MIN_COS = 0.5
    # Distance gate: the refined closest approach must be ≤ 3 m for the match to count as
    # "passing the same point". WHY: across both real D24 sessions every genuine winner
    # measures ≤ 1.6 m (two laps' racing lines through the same point), while the residual
    # false matches — heading-compatible points on a NEARBY piece of track (e.g. a wide-radius
    # leg the heading gate can't separate) — measure ≥ 4 m and short-change the loop by ~1 s.
    # A rejected anchor only DROPS a candidate window (conservative: rolling can only get
    # slower, never optimistic), so the gate cannot bias the minimum downwards.
    _ROLLING_MATCH_MAX_M = 3.0

    def best_rolling_lap(self) -> float | None:
        """The BEST ROLLING lap time (seconds): the fastest single COMPLETE loop of the track
        regardless of where it starts — the minimum, over every track position P, of the time
        from passing P to passing P again one lap later (the MoTeC i2 "rolling lap" / AiM RS3
        "best rolling": a contiguous lap not aligned to start/finish). None if no valid laps.

        IMPLEMENTATION CHOICE — per-pair windows anchored to the same SPATIAL point: for each
        consecutive valid-lap pair (k, k+1), every lap-k sample is an anchor P; the window ends
        when lap k+1 passes CLOSEST to P (nearest SAME-DIRECTION sample within a narrow
        normalized-distance arc — see the two gating constants above — refined by projecting P
        onto the adjacent trace segments for a sub-sample crossing time). Two simpler variants
        were measured and REJECTED on the real D24 data, because a min over thousands of
        windows seeks out exactly their alignment error:
          * pure normalized-distance phases (t_{k+1}(φ) − t_k(φ)): the laps' line lengths
            differ (e.g. 1061.7 vs 1066.2 m on the 0062 winner pair), so equal φ is a different
            physical point — the winning "window" measured 146 ms FASTER than the true spatial
            loop (68.077 vs 68.223 s). Optimistically biased ⇒ rejected.
          * a fixed odometer window over the full trace (searchsorted(cum, cum[i] + L)):
            the same defect, worse — a ±4.5 m line-length spread is ±~0.3 s at lap speed,
            and L itself depends on which lap it is taken from. Rejected.
        Anchor resolution: one GPS sample (~1.5 m at speed). Between adjacent anchors the
        window time changes by the PACE DIFFERENCE of the two laps over that metre-scale span
        — far below the GPS noise floor — so a continuum search would add nothing.

        Window admission (consistent with the ⚠ low-confidence rule): straddling windows are
        taken only across pairs of CONSECUTIVE valid laps where NEITHER lap has a GPS dropout
        (a straddling window would inherit the unreliable timing). Every complete valid lap is
        always admitted as the S/F-aligned degenerate window — those are exactly the lap times
        the table already shows (⚠-flagged where applicable) — which also guarantees
        best_rolling ≤ best lap time."""
        valid = self.valid_lap_ids()
        if not valid:
            return None
        # Complete valid laps are themselves (S/F-aligned) rolling windows: rolling ≤ best.
        best = min(self.laps.lap_time(i) for i in valid)
        valid_set = set(valid)
        for a in valid:
            b = a + 1
            if b not in valid_set or self.lap_has_dropout(a) or self.lap_has_dropout(b):
                continue
            times_a, xs_a, ys_a, _spd_a, cum_a = self._lap_columns(a)
            times_b, xs_b, ys_b, _spd_b, cum_b = self._lap_columns(b)
            n_a, n_b = len(times_a), len(times_b)
            if n_a < 2 or n_b < 2:
                continue
            total_a, total_b = float(cum_a[-1]), float(cum_b[-1])
            if total_a <= 0 or total_b <= 0:
                continue
            # Consecutive valid laps are time-contiguous by construction (lap a's interpolated
            # finish crossing IS lap b's start crossing — one crossing instant computed once in
            # the segmentation). Defensive: skip a pair with a real hole between the laps (only
            # reachable on hand-seeded sessions), where the windows would not be one loop.
            if abs(float(times_b[0]) - float(times_a[-1])) > 1e-3:
                continue
            # Nearest lap-b sample per anchor, searched only inside the ±_ROLLING_SEARCH_FRAC
            # arc around the anchor's normalized fraction, and only among samples travelled in
            # the same direction (the heading gate — see both constants' WHY above).
            s_a = cum_a / total_a
            s_b = cum_b / total_b
            k = max(5, int(self._ROLLING_SEARCH_FRAC * n_b))
            centers = np.clip(np.searchsorted(s_b, s_a), 0, n_b - 1)
            idx = np.clip(centers[:, None] + np.arange(-k, k + 1)[None, :], 0, n_b - 1)
            d2 = (xs_b[idx] - xs_a[:, None]) ** 2 + (ys_b[idx] - ys_a[:, None]) ** 2
            tax, tay = _unit_tangents(xs_a, ys_a)
            tbx, tby = _unit_tangents(xs_b, ys_b)
            heading_cos = tax[:, None] * tbx[idx] + tay[:, None] * tby[idx]
            d2 = np.where(heading_cos >= self._ROLLING_HEADING_MIN_COS, d2, np.inf)
            rowmin = np.argmin(d2, axis=1)
            anchors = np.arange(n_a)
            j = idx[anchors, rowmin]
            # An anchor whose whole search arc fails the heading gate (degenerate geometry)
            # simply contributes no window.
            usable = np.isfinite(d2[anchors, rowmin])

            # Sub-sample refinement: project each anchor onto the two trace segments adjacent
            # to its nearest sample; the closer projection's chord parameter interpolates the
            # crossing time (the same chord idiom the C++ start-line crossing uses).
            def _project(j0, j1, xs_b=xs_b, ys_b=ys_b, times_b=times_b,
                         xs_a=xs_a, ys_a=ys_a):
                vx, vy = xs_b[j1] - xs_b[j0], ys_b[j1] - ys_b[j0]
                len2 = vx * vx + vy * vy
                safe = np.where(len2 > 0, len2, 1.0)
                u = ((xs_a - xs_b[j0]) * vx + (ys_a - ys_b[j0]) * vy) / safe
                u = np.clip(np.where(len2 > 0, u, 0.0), 0.0, 1.0)
                qx, qy = xs_b[j0] + u * vx, ys_b[j0] + u * vy
                dist2 = (qx - xs_a) ** 2 + (qy - ys_a) ** 2
                return dist2, times_b[j0] + u * (times_b[j1] - times_b[j0])

            d2_lo, t_lo = _project(np.maximum(j - 1, 0), j)
            d2_hi, t_hi = _project(j, np.minimum(j + 1, n_b - 1))
            t_cross = np.where(d2_lo <= d2_hi, t_lo, t_hi)
            # Distance gate on the REFINED closest approach (see _ROLLING_MATCH_MAX_M's WHY).
            usable &= np.minimum(d2_lo, d2_hi) <= self._ROLLING_MATCH_MAX_M ** 2
            w = np.where(usable, t_cross - times_a, np.inf)
            if np.isfinite(w).any():
                best = min(best, float(np.min(w)))
        return float(best)

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

    # -------------------------------------------------------------- corner model (F-corner)
    # The corner model (studio/corners.py, pacer-free) detected once per segmentation on the
    # MEDIAN curvature profile of the session's clean laps, expressed in the best lap's
    # odometer space, and projected onto each lap by normalized distance — the same
    # projection lap_sector_splits uses for sector boundaries. All cached; cleared with the
    # per-lap caches in set_timing_lines (the corner set depends on the segmentation).

    def _corner_basis(self) -> tuple[list[corners.Corner], float] | None:
        """The cached (corner list, reference total distance) pair, or None when there is no
        usable best lap. The reference total is the best lap's odometer length — the basis
        the corner windows (and the delta plot's distance axis) are expressed in."""
        if self._corner_cache is not _UNSET:
            return self._corner_cache
        self._corner_cache = None
        best = self.best_lap_id()
        if best is not None:
            _t, _xs, _ys, _v, cum_best = self._lap_columns(best)
            if len(cum_best) >= 8 and float(cum_best[-1]) > 0:
                total_ref = float(cum_best[-1])
                # The median curvature profile pools the session's clean laps (valid, no GPS
                # dropout); the best lap is always included so a session where every lap is
                # dropout-flagged still detects on the best lap alone.
                ids = [i for i in self.valid_lap_ids() if not self.lap_has_dropout(i)]
                if best not in ids:
                    ids.append(best)
                traces = []
                for lid in ids:
                    _lt, xs, ys, _lv, cum = self._lap_columns(lid)
                    traces.append((xs, ys, cum))
                d_grid, kappa = corners.pooled_curvature(traces, total_ref)
                self._corner_cache = (corners.detect_corners(d_grid, kappa), total_ref)
        return self._corner_cache

    def corners(self) -> list[corners.Corner]:
        """The detected corners (C1… in track order) in best-lap odometer metres. [] when
        no best lap exists. Computed once per segmentation (see _corner_basis)."""
        basis = self._corner_basis()
        return basis[0] if basis is not None else []

    def lap_corner_stats(self, lap_id: int) -> list[corners.CornerStat]:
        """Per-corner metrics for one lap (time-in-corner, apex/entry/exit speeds, deltas vs
        the best lap's same corner — see corners.CornerStat). [] for a degenerate lap or
        when no corners were detected. Cached per lap; cleared on re-segment."""
        got = self._corner_stats_cache.get(lap_id)
        if got is not None:
            return got
        basis = self._corner_basis()
        best = self.best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = self._lap_arrays(lap_id)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return []
        # The reference stats are the best lap's own (deltas 0); computed first, then every
        # other lap's deltas are measured against them.
        ref = self.lap_corner_stats(best) if lap_id != best else None
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=ref or None)
        self._corner_stats_cache[lap_id] = stats
        return stats

    def corner_session_bests(self) -> list[float]:
        """Per-corner session-best time-in-corner across all VALID laps (the purple-cell
        convention, matching the per-sector session bests). [] when no corners. Cached;
        cleared on re-segment."""
        if self._corner_bests is not _UNSET:
            return self._corner_bests
        per_lap = [self.lap_corner_stats(i) for i in self.valid_lap_ids()]
        per_lap = [s for s in per_lap if s]
        n = len(self.corners())
        self._corner_bests = [
            min(s[i].time for s in per_lap) for i in range(n)
        ] if per_lap and n else []
        return self._corner_bests

    def corner_map_markers(self) -> list[tuple[str, float, float, int]]:
        """(label, x, y, direction) per corner — the apex position in LOCAL metres on the
        best lap's trace, for the map's corner labels. [] when no corners/best lap."""
        basis = self._corner_basis()
        best = self.best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, _total_ref = basis
        _t, xs, ys, _v, cum = self._lap_columns(best)
        apexes = np.asarray([c.apex for c in corner_list])
        mx = np.interp(apexes, cum, xs)
        my = np.interp(apexes, cum, ys)
        return [(c.label, float(mx[i]), float(my[i]), c.direction)
                for i, c in enumerate(corner_list)]

    # ------------------------------------------------------------- data export (F11)
    # The export writers (studio/export_data.py) are pacer-free by contract, so the two
    # accessors below own the export's only pacer crossings: the per-sample channel arrays
    # for the channels CSV, and the GPS9 wall-clock date for the report header.

    def lap_channels(self, lap_id: int) -> dict[str, np.ndarray]:
        """Index-aligned per-sample channel arrays for ONE lap — the SINGLE pacer-free view
        over the cached bulk `_lap_columns` fetch (no new pacer crossing) shared by BOTH
        consumers: the channels-CSV export (all keys) and the map's rainbow channel painting
        (F3 — `map_view._build_rainbow`, which reads t_media_s / x_m / y_m / speed_kmh /
        dist_m). Keys, in CSV column order:

          t_media_s            media-clock time of each kept sample (the video-sync clock;
                               the rainbow's gap detection basis)
          elapsed_s            t − lap start (s)
          lat_deg / lon_deg    GPS position in degrees — the SAME smoothed samples every
                               analysis uses, read off the lap's materialized points
          x_m / y_m            LOCAL metres (the map/timing frame, cs.local — the rainbow
                               polyline)
          dist_m               the lap's gap-aware odometer (m — the rainbow Δ-grid resample
                               basis)
          speed_mps / speed_kmh   raw 3D GPS speed (km/h is the same basis the speed chart
                               and the rainbow speed mode plot)
          g_long / g_lat       kart-frame longitudinal/lateral acceleration (g), present
                               only when the session has a g signal. The g-meter series
                               (gmeter.GMeter: long_g/lat_g at its own 50 Hz `times`) is
                               interpolated onto the lap's sample times; signs follow
                               gmeter.py (+long = accelerating, +lat = turning left).

        Arrays span the materialized lap (interpolated start crossing + interior points +
        interpolated finish crossing), sliced defensively to the common length like the
        other per-lap accessors. Read-only; nothing is cached (exports are one-shot, and the
        rainbow is rebuilt only on a lap change / re-segment, not per tick)."""
        times, xs, ys, speed_mps, cum = self._lap_columns(lap_id)
        pts = self.laps.get_lap(lap_id).points
        lat = np.asarray([p.point.lat for p in pts], dtype=float)
        lon = np.asarray([p.point.lon for p in pts], dtype=float)
        m = min(len(times), len(lat))
        times, xs, ys, speed_mps, cum = (a[:m] for a in (times, xs, ys, speed_mps, cum))
        out: dict[str, np.ndarray] = {
            "t_media_s": times,
            "elapsed_s": times - times[0] if m else times.copy(),
            "lat_deg": lat[:m],
            "lon_deg": lon[:m],
            "x_m": xs,
            "y_m": ys,
            "dist_m": cum,
            "speed_mps": speed_mps,
            "speed_kmh": speed_mps * 3.6,
        }
        if self._gmeter.has_data:
            out["g_long"] = np.interp(times, self._gmeter.times, self._gmeter.long_g)
            out["g_lat"] = np.interp(times, self._gmeter.times, self._gmeter.lat_g)
        return out

    def session_date(self) -> str | None:
        """The recording's UTC date ("YYYY-MM-DD") from the first kept GPS fix's GPS9
        wall-clock timestamp (epoch ms — see pacer's ParseGPS9; preserved verbatim through
        the clean/smooth pipeline). None when the stream carries no per-fix timestamp
        (a GPS5-only camera) or the session is empty — the report shows a dash."""
        if self.laps.point_count() == 0:
            return None
        ts = int(self.laps.get_point(0).point.timestamp_ms)
        if ts <= 0:  # GPS5 / sentinel samples report 0 — no wall clock to read
            return None
        dt = datetime.datetime.fromtimestamp(ts / 1000.0, tz=datetime.UTC)
        return dt.strftime("%Y-%m-%d")

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
        if dists[-1] <= 0:  # zero-length odometer (≥2 stationary points): degenerate → no x
            return None     # (same `<= 0` convention as delta() / sector_plot_positions)
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
