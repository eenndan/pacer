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
import os
from dataclasses import dataclass
from typing import TypedDict

import numpy as np

import pacer

from . import (
    chapters,
    coaching,
    consistency,
    corners,
    cross_reference,
    driving,
    gapfill,
    gmeter,
    library,
    render_cache,
    tracks,
)

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

_EMPTY = np.empty(0)  # the `speed` slot for a LapCurve whose speed series isn't needed (Δ family)

# Sentinel "lap id" for the cross-recording reference lap (F7) in `delta()`'s returned series
# and as its baseline id: it has no id among THIS session's laps (those are >= 0), so a negative
# sentinel can never collide. Views detect it via Session.has_reference()/reference_label(); the
# value is exposed so plots_view can request + label the reference curve without importing the
# ReferenceLap type.
REFERENCE_ID = -1

# The five index-aligned per-lap columns `_lap_columns` caches: (times, xs, ys,
# full_speed m/s, cum_distances), one bulk pacer.Laps.lap_columns crossing per lap.
LapColumns = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
# One (x, y) plot series per lap id — the speed/delta payloads `delta()` returns.
LapSeries = dict[int, tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class LapCurve:
    """One lap's arc-length-aligned curves — the single value object the whole Δ family aligns
    on (F2). `dist` is the per-lap odometer (metres, monotonic), `elapsed` the seconds-from-the-
    lap's-start, `speed` km/h, all index-aligned; `times` is the media-clock axis the source side
    inverts a scrub/playback time onto (for a cross-recording reference, which lives on no shared
    clock, `times` IS its 0-anchored `elapsed`, exactly what `ReferenceLap.time_dist_elapsed`
    hands over). Built from the EXISTING per-lap cached arrays (`_lap_time_dist_elapsed` /
    `_lap_arrays`) — no new heavy cache.

    The KEY unification: a Δ "baseline" is just a `LapCurve`, so the LOCAL best lap and the
    cross-recording REFERENCE lap are the SAME type. The old `if ref is None … else _ref_*`
    branches that injected the reference into every delta path collapse into picking which
    `LapCurve` is the baseline — `baseline_curve()` does exactly that.

    The three primitives below reproduce the open-coded alignment arithmetic VERBATIM (same
    `float(np.interp(...))` casts, same `/ float(total)` then `s * float(total)` order), so
    re-expressing the delta family through them is byte-for-byte identical, not a behaviour
    change. `total` is `float(dist[-1])`; callers guard `total <= 0` for the degenerate lap."""

    dist: np.ndarray
    elapsed: np.ndarray
    times: np.ndarray
    speed: np.ndarray

    @property
    def total(self) -> float:
        """The lap's total odometer distance (metres) — `float(dist[-1])`. This is exactly the
        value `active_baseline_total_distance()` exposes when this curve is the active baseline."""
        return float(self.dist[-1])

    def fraction_at_time(self, t: float) -> float:
        """Normalized track fraction s ∈ [0,1] at media-clock time `t`: distance-into-the-lap at
        `t` divided by the lap total. `np.interp` clamps `t` to the lap window, so s is clamped to
        [0,1]. Same arithmetic as the open-coded `interp(t, times, dists) / dists[-1]`."""
        return float(np.interp(t, self.times, self.dist)) / float(self.dist[-1])

    def elapsed_at_time(self, t: float) -> float:
        """The lap's own elapsed time (seconds-into-the-lap) at media-clock time `t`, clamped to
        the lap window. Same as the open-coded `interp(t, times, elapsed)` (= t − lap_start)."""
        return float(np.interp(t, self.times, self.elapsed))

    def elapsed_at_fraction(self, s: float) -> float:
        """This curve's elapsed time at normalized track fraction `s` — invert s → this lap's
        distance (s × total) → its elapsed via the monotonic odometer. The canonical "project a
        fraction onto a lap's elapsed axis" step (`interp(s × total, dist, elapsed)`) every Δ and
        every alignment-derived position shares."""
        return float(np.interp(s * float(self.dist[-1]), self.dist, self.elapsed))


def project(s: float, baseline: LapCurve) -> float:
    """The one canonical alignment primitive: the `baseline` curve's elapsed time at the source's
    normalized track fraction `s`. Δ(source vs baseline) at a track position is then simply
    `source_elapsed − project(s, baseline)`. This is the single place the "scale a fraction onto
    the active baseline's elapsed axis" pattern lives — the local best lap and the cross-recording
    reference are both just a `LapCurve`, so there is no separate reference branch."""
    return baseline.elapsed_at_fraction(s)


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
        # Driving-channel caches (studio/driving.py, pacer-free): the brake/coast thresholds
        # derived ONCE from this session's own g distribution, plus the per-lap brake events,
        # coasting spans, and per-corner grip utilization. The thresholds depend only on the g
        # series (constant for the recording), but the per-lap/per-corner results are projected
        # through the segmentation, so all the per-lap caches clear together on a timing-line
        # change (set_timing_lines), exactly like the corner caches above.
        self._driving_thresholds_cache: object = _UNSET   # driving.Thresholds | None
        self._brake_events_cache: dict[int, list[driving.BrakeEvent]] = {}
        self._coasting_spans_cache: dict[int, list[driving.CoastSpan]] = {}
        self._corner_grip_cache: dict[int, list[float]] = {}

        # Cross-recording reference lap (F7): a lap loaded from ANOTHER recording that REPLACES
        # the local best lap as the reference for the Δ charts, the map overlay, the chart
        # sector guide lines and the lap-table per-corner Δ columns. None = DORMANT: every
        # "vs best" path below falls back to the local best lap, byte-identical to a session
        # that never grew the feature. Set by load_reference(); cleared by clear_reference().
        # NOT touched by set_timing_lines — re-segmenting the PRIMARY laps doesn't change the
        # (frozen, externally-loaded) reference curve.
        self._reference: cross_reference.ReferenceLap | None = None
        # F7 Phase B: the LIVE reference Session, kept alive so the cross-recording VIDEO compare
        # can play the reference recording's footage + feed pane B its OWN telemetry (g / lap
        # window / lap id) from the reference's time axis + ChapterMap. Phase A discards this right
        # after extracting the lightweight `_reference` arrays; Phase B retains it. None = DORMANT
        # (no reference, or a data-only reference loaded by a test that never set it). Set by
        # set_reference_session; cleared by clear_reference. One extra Session of memory — it was
        # already loaded transiently, so this is acceptable (see the F7 Phase B brief).
        self._reference_session: Session | None = None

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
        # Report the F5 driving-channel thresholds derived from THIS session's g distribution
        # (the measured numbers that set the brake/coast detection — like the g cross-check, so
        # they're always visible at load). Best-effort; never breaks a load.
        try:
            th = self.driving_thresholds()
            if th is not None:
                print(f"studio: {th.describe()}", flush=True)
        except Exception as e:  # noqa: BLE001 — additive diagnostics only
            print(f"studio: driving-channel thresholds unavailable ({e!r}).", flush=True)

    # ----------------------------------------- cross-recording reference lap (F7)
    # The reference is a SINGLE lap from a SEPARATE recording that replaces the local best lap
    # as the "vs best" reference everywhere a delta is drawn (the Δ charts, the map overlay,
    # the chart sector guide lines, the lap-table per-corner Δ columns). It is loaded once,
    # frozen into a pacer-free `cross_reference.ReferenceLap` (arc-length curves + a racing
    # line pre-aligned into THIS session's local frame), and kept here. The seam below
    # (`baseline_curve()` for the per-tick Δ family + sector guides, `_ref_arrays` for `delta()`'s
    # grid build) is what the delta paths consult: `baseline_curve()` hands back the reference lap
    # AS A `LapCurve` when one is loaded, else the local best lap's `LapCurve` — the SAME type, so
    # the delta math has no reference-vs-best branch (the DORMANT case is byte-identical to before).

    def load_reference(self, paths: list[str]) -> str | None:
        """Load another recording and adopt ITS best lap as the reference for all the "vs best"
        outputs. Returns None on success, else a human-readable reason the reference was REFUSED
        (the local best lap is left untouched in every failure case — the feature is additive).

        Guards (all non-fatal — a refusal just keeps the current behaviour):
          * the reference must be the SAME detected track as this session (`track_name`); a
            different track would make the normalized-distance overlay meaningless;
          * the reference must have a valid best lap with a real arc-length curve.

        Alignment is by NORMALIZED distance (reusing `delta`'s machinery, not a new scheme):
        only the reference lap's `(dist, speed, elapsed)` curve is needed for the charts/table.
        For the map the reference racing line is fit into THIS session's local frame (see
        cross_reference.build). Loading goes through the normal headless `Session.load`
        pipeline (no video pane needed for the data path)."""
        try:
            ref = Session.load(paths)
        except Exception as exc:  # noqa: BLE001 — a bad reference must never break the session
            return f"could not load the reference recording ({type(exc).__name__}: {exc})"
        return self.set_reference_session(ref, source_label=chapters.recording_label(paths))

    def set_reference_session(self, ref: Session, source_label: str = "") -> str | None:
        """Adopt an already-loaded `Session` as the reference (the guard + extraction half of
        `load_reference`, split out so tests can pass a synthetic reference Session without a
        telemetry file). Returns None on success or the refusal reason."""
        # Track guard: both sides must be the SAME detected track. Compared on the registry
        # name; if EITHER side is an unknown track (name None) we can't prove they match, so
        # refuse rather than overlay two possibly-different tracks. (Same-track-or-bust.)
        if self.track_name is None or ref.track_name is None or self.track_name != ref.track_name:
            mine = self.track_name or "unknown"
            theirs = ref.track_name or "unknown"
            return (f"reference is a different track ({theirs}) than this session ({mine}); "
                    "keeping the local best lap")
        ref_best = ref.best_lap_id()
        if ref_best is None:
            return "reference recording has no valid laps; keeping the local best lap"
        dist, speed_kmh, elapsed = ref._lap_arrays(ref_best)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return "reference best lap is degenerate; keeping the local best lap"
        # The reference lap's closed (xs, ys) loop in the REFERENCE's local metres, and THIS
        # session's best-lap loop in OUR local metres — the two loops the racing-line overlay
        # is aligned between (see cross_reference.build).
        rx, ry = ref.lap_trace_xy(ref_best)
        ref_loop = np.column_stack([rx, ry]) if len(rx) >= 10 else None
        primary_loop = self._reference_fit_loop()  # our fastest clean lap's closed loop
        self._reference = cross_reference.build(
            dist=dist, speed_kmh=speed_kmh, elapsed=elapsed,
            loop_xy=ref_loop, primary_loop_xy=primary_loop,
            source_label=source_label or "reference", lap_id=ref_best,
        )
        # F7 Phase B: KEEP the live reference Session (its time axis + ChapterMap + g) so the
        # cross-recording VIDEO compare can play its footage in pane B with the right telemetry.
        # getattr-guarded set so a bare Session (test path, no __init__) gains the slot lazily.
        self._reference_session = ref
        # The per-corner Δ baseline just changed (best lap -> reference lap), so every cached
        # per-lap corner-stat delta is stale. Drop them; they recompute lazily against the new
        # reference. (The corner DETECTION — windows — is unchanged, so _corner_cache stays.)
        # getattr so a bare Session (Session.__new__, no __init__) used in unit tests works.
        if getattr(self, "_corner_stats_cache", None) is not None:
            self._corner_stats_cache.clear()
        return None

    def clear_reference(self) -> None:
        """Drop the cross-recording reference — every "vs best" output reverts to the local
        best lap (the dormant state). No-op if none is loaded."""
        if self._ref is None:
            return
        self._reference = None
        # F7 Phase B: drop the retained live reference Session too (frees its decode/arrays) — the
        # cross-recording video compare can no longer be entered once the reference is cleared.
        self._reference_session = None
        # Revert the per-corner Δ baseline to the local best lap: the cached deltas (measured
        # against the now-cleared reference) are stale. Drop them; they recompute lazily.
        # getattr-guarded for the bare-Session test path (no __init__ ran to create the slot).
        if getattr(self, "_corner_stats_cache", None) is not None:
            self._corner_stats_cache.clear()

    @property
    def _ref(self) -> cross_reference.ReferenceLap | None:
        """The active reference (or None), read defensively so a BARE Session built via
        `Session.__new__` for tests (no `__init__`, so no `_reference` slot) reads as dormant —
        the same getattr idiom the other cache slots use for the test-seeded path."""
        return getattr(self, "_reference", None)

    def has_reference(self) -> bool:
        """True iff a cross-recording reference lap is currently active."""
        return self._ref is not None

    def reference_label(self) -> str | None:
        """The source-recording label of the active reference (for the UI chip/statusbar), or
        None when dormant."""
        ref = self._ref
        return ref.source_label if ref is not None else None

    def reference_lap_time(self) -> float | None:
        """The active reference lap's total time (seconds), or None when dormant."""
        ref = self._ref
        return ref.total_time if ref is not None else None

    def reference_overlay_xy(self):
        """The reference racing line as an (M,2) ring in THIS session's local frame (for the
        map best-lap overlay), or None when there's no reference or its spatial fit was too
        poor to draw. The charts/table reference is unaffected by a None here (they align by
        distance, which is frame-independent)."""
        ref = self._ref
        return ref.overlay_xy if ref is not None else None

    # ----------------------------------------- cross-recording VIDEO compare (F7 Phase B)
    # The retained live reference Session + the few derived lookups the cross-recording compare
    # needs: its ChapterMap (pane B video source), its best lap id (pane B's locked lap), the
    # reference lap's position on the FITTED overlay line (the map ghost), and the reference-vs-
    # primary Δ at a reference-clock time (pane B's badge). These let CompareController route
    # pane B through the reference WITHOUT importing pacer (the views-stay-pacer-free boundary).
    def reference_session(self) -> Session | None:
        """The live reference Session retained for the cross-recording video compare, or None when
        no reference is loaded (or a data-only reference was set without a live Session). Lets the
        app/controller route pane B's video source + g + lap window through the reference."""
        return getattr(self, "_reference_session", None)

    def reference_lap_id(self) -> int | None:
        """The reference lap id (the reference recording's best lap) that pane B is locked to, or
        None when dormant. v1 locks pane B to this lap (no pane-B picker)."""
        ref = self._ref
        return ref.lap_id if ref is not None else None

    def _reference_progress_at(self, t_ref: float) -> tuple[float, float] | None:
        """The reference lap's progress at the reference recording's GLOBAL media-clock time
        `t_ref`, returned as `(s, elapsed_into_lap)`:
          * `s` ∈ [0, 1] — the reference lap's NORMALIZED track fraction (cum_dist / total_dist),
            clamped to the lap window;
          * `elapsed_into_lap` — the reference's own time-into-lap (seconds-from-its-start),
            clamped to [0, total_time].
        None when there's no reference, no retained reference Session, or the lap is degenerate.

        WHY the conversion: the cross-recording compare hands pane B's position as the REFERENCE
        recording's GLOBAL media clock (the reference lap sits at `lap_window[0]` ≈ wherever it is
        in that file, NOT 0). But `ReferenceLap.time_dist_elapsed()` carries an elapsed-FROM-0 axis
        (the reference lives on no shared media clock). So `t_ref` MUST be rebased to seconds-into-
        the-reference-lap — `t_into = t_ref − ref_window_start` — before it is interpolated against
        the from-0 `dist`/`elapsed` arrays. Without this the interp of a ~1000 s `t_ref` against a
        [0..60] axis clamps to the finish, freezing the map ghost at S/F and the badge at the
        finish delta. Both `reference_delta_vs_lap` and `reference_overlay_index_at_progress` build
        on this single helper so they convert the clock identically (and stay fake-satisfiable in
        tests — only `lap_window` is needed off the reference Session, not its per-lap arrays)."""
        ref = self._ref
        if ref is None:
            return None
        ref_sess = self.reference_session()
        if ref_sess is None:
            return None
        window = ref_sess.lap_window(ref.lap_id)
        if window is None:
            return None
        # The reference lap's OWN from-0 arc-length curves (the same source the Δ charts/table use),
        # so the rebased t_into and these arrays share one zero — no clamp-to-finish mismatch.
        _times, dists, elapsed = ref.time_dist_elapsed()
        total_dist = float(dists[-1]) if len(dists) else 0.0
        total_time = float(elapsed[-1]) if len(elapsed) else 0.0
        if total_dist <= 0 or total_time <= 0:
            return None
        t_into = t_ref - float(window[0])  # GLOBAL reference clock -> seconds-into-the-reference-lap
        # np.interp clamps t_into to [elapsed[0]=0, elapsed[-1]=total_time], so s and the elapsed
        # below are already window-clamped (start before the lap -> s=0, after the finish -> s=1).
        s = float(np.interp(t_into, elapsed, dists)) / total_dist  # [0, 1]
        s = min(max(s, 0.0), 1.0)
        elapsed_into_lap = float(np.interp(t_into, elapsed, elapsed))  # clamp(t_into, 0, total_time)
        return s, elapsed_into_lap

    def reference_overlay_index_at_progress(self, t_ref: float) -> int | None:
        """The index into `reference_overlay_xy()` of the reference kart's position at the reference
        recording's GLOBAL media-clock time `t_ref` — for the F4 map ghost in cross-recording
        compare. The overlay ring is the reference racing line ALREADY fit into THIS session's local
        frame (cross_reference), sampled along its own arc length; the reference lap's normalized
        progress at `t_ref` (distance-fraction, via `_reference_progress_at`) maps onto it directly.
        None when there's no overlay (no reference or a poor spatial fit) — the ghost is then
        suppressed, the charts/table are unaffected.

        Distinct from `index_at_time` (which indexes the PRIMARY trace): the cross-recording ghost
        must sit on the reference overlay line, not the primary trace."""
        ref = self._ref
        if ref is None or ref.overlay_xy is None:
            return None
        prog = self._reference_progress_at(t_ref)
        if prog is None:
            return None
        s, _elapsed_into_lap = prog
        m = len(ref.overlay_xy)
        if m == 0:
            return None
        return min(int(round(s * (m - 1))), m - 1)

    def reference_delta_vs_lap(self, lap_id: int, t_ref: float) -> float | None:
        """Δ (seconds) of the REFERENCE lap vs the primary `lap_id` at the reference's track
        position at the reference recording's GLOBAL media-clock time `t_ref`: how far ahead (−) /
        behind (+) the reference is relative to the primary lap at that normalized distance. None if
        either lap is degenerate.

        Pane B's "Δ vs other" badge in cross-recording compare — the mirror of pane A's
        `delta_at_lap(lap_a, t_a)` (primary vs reference). Reuses the SAME normalized-distance
        alignment Phase A's delta machinery uses (via `_reference_progress_at`, which rebases the
        GLOBAL reference clock to seconds-into-the-reference-lap first), so the two are consistent
        and the finish-line (s=1) Δ is exactly `reference_total_time − primary_lap_time`, the
        cross-recording laptime difference (the negative of pane A's endpoint).

        F2: the SAME `project(s, baseline)` step Phase A's `delta_at_lap` uses, only here the
        SOURCE is the reference (its `s`/`elapsed` come from `_reference_progress_at`, since the
        reference lives on its own clock) and the BASELINE is the primary `lap_id`'s curve — the
        mirror image of pane A, expressed through the one alignment primitive."""
        prog = self._reference_progress_at(t_ref)
        if prog is None:
            return None
        s, elapsed_ref = prog  # reference's track fraction + its own time-into-lap, both clamped
        primary = self._lap_curve(lap_id)  # the baseline here is the PRIMARY lap's curve
        if primary is None:
            return None
        if primary.total <= 0:
            return None
        # The primary lap's elapsed at the SAME track fraction s (invert s → primary distance → time).
        return elapsed_ref - project(s, primary)

    # The delta seam (F2). The per-tick Δ family + the alignment-derived positions all align on a
    # `LapCurve`; a "baseline" is just a `LapCurve`, so the LOCAL best lap and the cross-recording
    # REFERENCE lap are the SAME type and the old `if ref is None … else _ref_*` branches collapse
    # into `baseline_curve()` picking which one. `_ref_arrays` stays for `delta()`, whose grid
    # builder still consumes the raw `(dist, speed_kmh, elapsed)` triple shape.
    def _ref_arrays(self):
        """`(dist, speed_kmh, elapsed)` for the reference lap when one is loaded, else None.
        Drop-in for `_lap_arrays(best)` in `delta()`."""
        ref = self._ref
        return ref.arrays() if ref is not None else None

    def _lap_curve(self, lap_id: int) -> LapCurve | None:
        """The `LapCurve` for `lap_id` (None if degenerate, <2 points). Built from the cached
        `_lap_time_dist_elapsed` triple — `times` is the media-clock axis, `dist`/`elapsed` the
        per-lap odometer + seconds-from-start. `speed` is left empty: the per-tick Δ family aligns
        only on dist/elapsed/times (the speed series is `delta()`'s own grid build)."""
        td = self._lap_time_dist_elapsed(lap_id)
        if td is None:
            return None
        times, dist, elapsed = td
        return LapCurve(dist=dist, elapsed=elapsed, times=times, speed=_EMPTY)

    def baseline_curve(self) -> LapCurve | None:
        """The ACTIVE Δ baseline as a `LapCurve`: the cross-recording REFERENCE lap when one is
        loaded (F7), else the LOCAL best lap. None when neither exists/is degenerate. This is the
        single seam the per-tick Δ family inverts a source fraction onto — picking the baseline
        here is what makes the reference vs local-best branch disappear from every delta path.
        Its `.total` is exactly `active_baseline_total_distance()`."""
        ref = self._ref
        if ref is not None:
            r_times, r_dist, r_elapsed = ref.time_dist_elapsed()
            return LapCurve(dist=r_dist, elapsed=r_elapsed, times=r_times, speed=ref.speed_kmh)
        best = self.best_lap_id()
        if best is None:
            return None
        return self._lap_curve(best)

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
        # The per-lap driving channels (brake events / coasting spans / per-corner grip) are
        # projected through the same segmentation — stale with the corner model. The derived
        # thresholds depend only on the (unchanged) g series, so they're kept; only the per-lap
        # results clear (recomputed lazily on next access).
        self._brake_events_cache.clear()
        self._coasting_spans_cache.clear()
        self._corner_grip_cache.clear()

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
        """The LOCAL best lap's total odometer distance (metres). None if there's no valid best
        lap. NOTE: the distance-mode chart x-axis is scaled by `active_baseline_total_distance()`
        (the REFERENCE total when one is loaded), not this — use that for cursor↔x mapping so the
        cursor mappers agree with `delta()`'s x-grid. This stays the local-best value for the
        callers that genuinely want the local best lap's length."""
        best = self.best_lap_id()
        if best is None:
            return None
        td = self._lap_time_dist(best)
        if td is None:
            return None
        return float(td[1][-1])

    def active_baseline_total_distance(self) -> float | None:
        """The total odometer distance (metres) of the ACTIVE Δ baseline — the CROSS-RECORDING
        reference lap's total when one is loaded (F7), else the local best lap's total. This is
        the single source of truth for the basis the distance-mode chart x-axis is scaled in
        (x = s × baseline_total), used by BOTH `delta()`'s x-grid AND the distance-mode cursor
        mappers (`media_time_at_plot_x` / `plot_x_at_media_time`). Single-sourcing it keeps the
        scrub cursor sitting on its curve when the reference and local-best totals DIFFER — the
        D12 bug was the x-grid using the reference total while the mappers used the local best.
        DORMANT: with no reference this is byte-identical to `best_lap_total_distance()`.

        F2: this is just the active `baseline_curve().total` — the single seam the per-tick Δ
        family inverts a fraction onto — so the x-grid and the Δ math read ONE baseline total.
        The reference's `len(dist) >= 1`-vs-empty guard is preserved verbatim (a 1-point reference
        still yields its total; `baseline_curve()` skips the local-best `<2`-point degenerate via
        `_lap_curve`, exactly as `best_lap_total_distance()` did)."""
        ref = self._ref
        if ref is not None:
            dist = ref.dist
            return float(dist[-1]) if len(dist) >= 1 else None
        baseline = self.baseline_curve()  # the local best lap's curve (ref handled above)
        return baseline.total if baseline is not None else None

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
        finish (d=total) the boundaries give one more split than there are boundaries, all
        positive and SUMMING to the lap time for every lap (no blanks, none exceeding it)."""
        # times + cum_distances from the one bulk lap_columns crossing (both length lap.count(),
        # so the former m = min(len(points), len(cum_distances)) is just that length).
        times, _xs, _ys, _full_speed, cum = self._lap_columns(lap_id)
        m = min(len(times), len(cum))
        if m < 2:
            return []
        cum_distance = cum[:m]
        elapsed = times[:m] - times[0]

        # Each sector line's lap distance = cum_distance of the lap point nearest its midpoint —
        # single-sourced (ascending, windowed + DEDUPED) via sector_boundary_distances, so the
        # boundary guide lines (F2) sit exactly where these splits are measured. The split count
        # follows the DEDUPED boundary count (not len(sector_lines)): a duplicate / wrong-pass
        # line was already collapsed there, so it can't inject a 0 s split here.
        bounds = self.sector_boundary_distances(lap_id)
        n_splits = len(bounds) + 1

        total = float(cum_distance[-1])
        # Boundaries plus lap start/finish: N+1 sub-sectors. interp elapsed at each.
        edges = [0.0, *bounds, total]
        t_at = np.interp(edges, cum_distance, elapsed)
        splits = [float(t_at[k + 1] - t_at[k]) for k in range(n_splits)]
        return splits

    # Sector boundaries closer than this fraction of the lap odometer are treated as the SAME
    # line and collapsed (D10/D11). WHY a fraction, not a fixed metre: it scales from go-kart
    # laps (~1 km) up; 0.2% of a ~1.06 km D24 lap is ~2 m — well under the GPS step (~1.4 m at
    # 50 Hz / 70 km/h) so two distinct lines a real sub-sector apart are never fused, but two
    # lines the user dropped on top of each other (or a wrong-pass mis-snap that lands on a
    # neighbour) can't survive as a 0 s / out-of-order split that would poison the splits and
    # the theoretical best.
    _SECTOR_DEDUPE_FRAC = 0.002

    def sector_boundary_distances(self, lap_id: int) -> list[float]:
        """Per-lap odometer distance (metres) of each sector line, found the SAME way
        `lap_sector_splits` measures the splits: project each sector line's midpoint onto this
        lap's trace and take the nearest point's cum_distance, then return them ASCENDING. So the
        boundary guide lines on the charts (F2) land exactly where the split times are measured.

        Two robustness guards live here (the single source of every consumer's boundaries):
          * WINDOWED projection — a plain global argmin over the whole lap snaps a line to its
            globally-nearest trace point, which on an out-and-back / hairpin (the line's midpoint
            sits near two passes) or two lines placed close together can pick the WRONG pass and
            put the boundary at a bogus odometer. So each line is first projected globally to find
            its lap fraction, then RE-projected within a window around that fraction, breaking
            wrong-pass ties toward the expected location while leaving a normal, well-separated
            line on a single-pass section byte-identical to the old global argmin.
          * DEDUPE — after sorting, boundaries within `_SECTOR_DEDUPE_FRAC` of the previous one
            are dropped, so a duplicate / mis-snapped line can never yield a zero-length (or, once
            the sort masks it, out-of-order) split. Returning fewer boundaries than lines is fine:
            lap_sector_splits derives its split count from THIS list, and the table simply shows a
            blank in any trailing S-column a deduped lap no longer fills (its highlight/best-split
            paths already tolerate a short per-lap split list)."""
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
        total = float(cum[-1])
        # Window half-width (in samples) for the wrong-pass guard: a fraction of the lap's
        # samples, reusing the same ±2% arc the rolling-best nearest-point search trusts (a real
        # sub-sector is far wider than that, so the window keeps the matching pass while excluding
        # the OTHER pass of an out-and-back). Floored at 1 so it stays a no-op refinement (the
        # window is just the global point) on tiny synthetic laps.
        half = max(1, int(round(self._ROLLING_SEARCH_FRAC * m)))
        bounds = []
        for seg in lines:
            mx = (seg.first.x + seg.second.x) / 2.0
            my = (seg.first.y + seg.second.y) / 2.0
            d2 = (xs - mx) ** 2 + (ys - my) ** 2
            j_global = int(np.argmin(d2))
            # Re-pick the nearest point WITHIN a window around the global hit. On a single-pass
            # section the window minimum IS the global hit, so this is a no-op; on an out-and-back
            # it keeps the match on the pass the global argmin already chose rather than letting a
            # marginally-closer point on the OTHER pass win.
            lo = max(0, j_global - half)
            hi = min(m, j_global + half + 1)
            j = lo + int(np.argmin(d2[lo:hi]))
            bounds.append(float(cum[j]))
        bounds.sort()
        # Collapse boundaries that land on (nearly) the same odometer: keep the first, drop any
        # follower within the dedupe tolerance of the last KEPT boundary. This is what stops a
        # degenerate (duplicate / wrong-pass) line from producing a 0 s / out-of-order split that
        # `lap_sector_splits` would emit and `session_best_splits` could take as a spurious min.
        tol = self._SECTOR_DEDUPE_FRAC * total if total > 0 else 0.0
        deduped: list[float] = []
        for b in bounds:
            if not deduped or b - deduped[-1] > tol:
                deduped.append(b)
        return deduped

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
        # Column count tracks len(sector_lines)+1 — the SAME count the lap table headers use
        # (sector_count()+1), so the purple cells and the footer stay column-aligned with the
        # table. A lap that deduped to fewer boundaries (a duplicate / wrong-pass line collapsed
        # in sector_boundary_distances) just contributes nothing to its missing trailing column
        # via the i<len(sp) guard below; if that column is empty on EVERY lap it reads None,
        # exactly the documented "some column has no finite split" → theoretical_best None case.
        n_splits = len(self.laps.sectors.sector_lines) + 1
        all_splits = [self.lap_sector_splits(lap_id) for lap_id in self.valid_lap_ids()]
        best: list[float | None] = []
        for i in range(n_splits):
            # Defensive against a degenerate split surviving into a column: take the per-column
            # min only over FINITE and STRICTLY-POSITIVE splits (sp[i] > 0). sector_boundary_-
            # distances already dedupes coincident boundaries (the root), but were one lap's
            # geometry to still emit a 0 s / negative split (e.g. a non-monotonic projection),
            # the > 0 guard keeps that one lap from poisoning theoretical_best with a spurious
            # near-zero minimum. A legitimately tiny-but-positive split (a very short sub-sector)
            # still passes — only non-positive values are filtered.
            vals = [sp[i] for sp in all_splits
                    if i < len(sp) and math.isfinite(sp[i]) and sp[i] > 0]
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

        Includes the start/finish ("S/F", x=0) plus one line per sector. The sector positions
        on the TRACK come from the user's own (primary) best lap — the same midpoint→trace
        projection as the split times — but they're expressed on the chart's SHARED axis, which
        is scaled to the active Δ BASELINE (the local best lap normally; the cross-recording
        reference lap when one is loaded, F7). So the boundary FRACTIONS are measured on the
        primary best lap, then mapped onto the baseline's distance/time axis by the same
        normalized-distance alignment the curves use. Respects the dist/time toggle:
          * 'distance': x = (d_k / primary_lap_total) × baseline_distance
          * 'time':     x = baseline elapsed at that same track fraction
        DORMANT: with no reference the baseline IS the primary best lap, so this reduces to the
        previous behaviour exactly. Returns [] if there's no best lap (caller clears the lines)."""
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
        if self._ref is None:
            # DORMANT path — the baseline IS the primary best lap; keep the previous arithmetic
            # verbatim so the positions stay byte-identical to before the feature existed. (NOT
            # routed through project(): the dormant axis uses the raw boundary distance `d` and
            # `interp(d, dists, times) − t0`, which differ in the last ULP from project()'s
            # `interp((d/total)×total, dists, elapsed)` — deliberately kept exact, not unified.)
            if mode == "time":
                t0 = float(times[0])
                for label, d in zip(labels, edge_dists, strict=True):
                    t_at = float(np.interp(d, dists, times)) - t0  # elapsed into the best lap
                    positions.append((label, t_at))
            else:  # 'distance' — the shared s×best_distance axis (here best_distance == total)
                for label, d in zip(labels, edge_dists, strict=True):
                    positions.append((label, d))
            return positions
        # REFERENCE active: map each boundary's PRIMARY-lap fraction onto the reference axis
        # (distance = frac × reference_total; time = reference elapsed at that frac), so the
        # guide lines stay aligned with the curves, which are now scaled to the reference lap.
        # The baseline is just the active `LapCurve`; time mode is exactly `project(frac, base)`.
        base = self.baseline_curve()  # the reference lap's curve (ref is non-None here)
        if base is None or base.total <= 0:
            return []
        for label, d in zip(labels, edge_dists, strict=True):
            frac = d / total
            if mode == "time":
                positions.append((label, project(frac, base)))
            else:
                positions.append((label, frac * base.total))
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

    def _reference_corner_stats(self) -> list[corners.CornerStat] | None:
        """The cross-recording reference lap's per-corner stats projected onto THIS session's
        corner windows (the same normalized-distance projection any local lap uses), or None
        when no reference is loaded. Cached on the ReferenceLap so it isn't recomputed per lap;
        invalidated when the reference or the segmentation changes (clear/load_reference and
        set_timing_lines drop _corner_stats_cache, where this is parked under REFERENCE_ID)."""
        ref = self._ref
        if ref is None:
            return None
        got = self._corner_stats_cache.get(REFERENCE_ID)
        if got is not None:
            return got
        basis = self._corner_basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = ref.arrays()
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return None
        # ref=None on the reference itself -> its own deltas are 0 (it IS the baseline).
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=None)
        self._corner_stats_cache[REFERENCE_ID] = stats
        return stats

    def lap_corner_stats(self, lap_id: int) -> list[corners.CornerStat]:
        """Per-corner metrics for one lap (time-in-corner, apex/entry/exit speeds, deltas vs
        the baseline's same corner — see corners.CornerStat). [] for a degenerate lap or when
        no corners were detected. Cached per lap; cleared on re-segment.

        The Δ baseline is the local best lap normally, or the CROSS-RECORDING reference lap's
        projected corner stats when one is loaded (F7) — the deltas then read against the other
        recording's corners. DORMANT: with no reference this is byte-identical to before."""
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
        # The reference stats are the baseline lap's own (deltas measured against them): the
        # cross-recording reference when loaded, else the local best lap (whose self-deltas are
        # 0). Computed first, then this lap's deltas are measured against them.
        ref_stats = self._reference_corner_stats()
        if ref_stats is not None:
            ref = ref_stats
        else:
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

    # ------------------------------------------------------------ consistency stats (F6)
    # Thin assemblers over the cached per-lap values; the math (σ / median loss / ranking)
    # lives pacer-free in studio/consistency.py. Not cached: the panel reads these only on
    # load and after a re-segmentation (never on the 30 Hz tick), and the per-lap inputs
    # (lap_corner_stats, _lap_columns) are already memoized above.

    def consistency_lap_ids(self) -> list[int]:
        """The lap set every consistency statistic runs over: VALID laps with no GPS
        dropout, in session order — the ⚠ rule (a dropout lap's time/splits are
        low-confidence, so it is excluded from σ/medians exactly as it is excluded from
        the corner-detection profile in _corner_basis)."""
        return [i for i in self.valid_lap_ids() if not self.lap_has_dropout(i)]

    def lap_time_trend(self) -> list[tuple[int, float]]:
        """(lap_id, lap_time s) per consistency lap, in session order — the panel's trend
        sparkline series. The times are the same laps.lap_time values the lap table rows
        show, so the trend and the table can never disagree."""
        return [(i, self.lap_time(i)) for i in self.consistency_lap_ids()]

    def sector_sigmas(self) -> list[float | None]:
        """Per-sub-sector sample σ (s) of the split times over the consistency laps, one
        per S-column (None where a column has <2 finite splits). [] when no sector lines
        are placed (no split columns exist then — the lap table convention)."""
        if not self.laps.sectors.sector_lines:
            return []
        return consistency.sector_sigmas(
            [self.lap_sector_splits(i) for i in self.consistency_lap_ids()])

    def corner_consistency(self) -> list[consistency.CornerSpread]:
        """The "most inconsistent corners" ranking over the consistency laps: per corner,
        the sample σ of time-in-corner and the median time lost vs the per-corner best,
        ranked by σ × median_loss (corners that are BOTH inconsistent and slow first —
        see studio/consistency.py for the weighting rationale). [] without corners."""
        ids = self.consistency_lap_ids()
        corner_list = self.corners()
        if not corner_list or not ids:
            return []
        times_by_lap = []
        for i in ids:
            st = self.lap_corner_stats(i)
            if len(st) == len(corner_list):  # degenerate laps project to [] — skip
                times_by_lap.append([s.time for s in st])
        return consistency.rank_corners(
            consistency.corner_spreads([c.cid for c in corner_list], times_by_lap))

    # ------------------------------------------------------ auto coaching summary (F10)
    # The capstone: composes the corner model (F2), driving channels (F5) and consistency
    # stats (F6) into the ranked "opportunities" — where to find time vs your own best lap,
    # with the dominant MEASURED reason per corner. The math lives pacer-free in
    # studio/coaching.py; this accessor owns the only pacer-side extraction (the per-lap
    # corner stats / brake events / coasting spans) and hands plain dataclasses + arrays to
    # the model. Not cached: the panel reads it on load and after a re-segmentation only
    # (never on the 30 Hz tick), and every per-lap input it reads is already memoized above.

    def coaching_opportunities(self) -> coaching.Opportunities:
        """The ranked coaching opportunities (F10): per corner, the MEDIAN time lost vs the
        best lap over the consistency laps (biggest first), with the dominant measured reason
        attached to the top-N. Deterministic and explainable (see studio/coaching.py).

        Returns an Opportunities with `enough=False` (empty rows) when there are fewer than
        coaching.MIN_LAPS valid, dropout-free laps — the friendly "need more laps" state, no
        crash. Composes only existing accessors: corners(), lap_corner_stats(),
        consistency_lap_ids(), corner_consistency(), lap_brake_events(), lap_coasting_spans(),
        best_lap_id(), lap_time()."""
        ids = self.consistency_lap_ids()
        corner_list = self.corners()
        best = self.best_lap_id()
        if not corner_list or best is None:
            return coaching.Opportunities(enough=False, n_laps=len(ids),
                                          median_lap_id=None, rows=[])
        n = len(corner_list)
        # Per candidate lap: lap time + the per-corner time-in-corner aligned to corner_list.
        # A degenerate lap projects to [] (len != n) — drop it from BOTH the id list and the
        # matrix so every row of corner_times_by_lap is aligned to candidate_lap_ids.
        cand_ids: list[int] = []
        lap_times: list[float] = []
        corner_times_by_lap: list[list[float]] = []
        for i in ids:
            st = self.lap_corner_stats(i)
            if len(st) != n:
                continue
            cand_ids.append(i)
            lap_times.append(self.lap_time(i))
            corner_times_by_lap.append([s.time for s in st])

        # The best lap's own per-corner time-in-corner (its self-delta is 0; we need the raw
        # times as the per-corner baseline the median loss is measured against).
        best_stats = self.lap_corner_stats(best)
        if len(best_stats) != n:
            return coaching.Opportunities(enough=False, n_laps=len(cand_ids),
                                          median_lap_id=None, rows=[])
        best_corner_times = [s.time for s in best_stats]

        # The representative ("median") lap — its time is the median of the candidate set.
        med_id = coaching.median_lap_id(cand_ids, lap_times)

        # Cross-lap σ of time-in-corner per cid (from the F6 ranking), as the LINE signal.
        sigmas_by_cid = {sp.cid: sp.sigma for sp in self.corner_consistency()}

        # The median lap's per-corner apex-speed delta vs the LOCAL best lap (km/h) — the APEX
        # signal. Computed DIRECTLY as median_apex − best_apex (raw apex_speed from each lap's
        # stats), NOT via CornerStat.apex_speed_delta: that field follows the active Δ baseline,
        # which becomes the CROSS-RECORDING reference when one is loaded (F7), whereas the coaching
        # losses are measured vs the LOCAL best lap (best_corner_times). Mixing baselines made the
        # two halves of a coaching row disagree (D13); pinning the apex signal to the local best,
        # the same baseline as the loss, keeps the row self-consistent. [] if no median lap.
        if med_id is not None:
            med_stats = self.lap_corner_stats(med_id)
            median_apex_deltas = (
                [med_stats[i].apex_speed - best_stats[i].apex_speed for i in range(n)]
                if len(med_stats) == n else [])
        else:
            median_apex_deltas = []

        # The driving channels (brake/coast) for the median + best laps — the BRAKING/COASTING
        # signals. [] when there's no g signal (the apex/line signals still drive the reasons).
        med_brakes = self.lap_brake_events(med_id) if med_id is not None else []
        best_brakes = self.lap_brake_events(best)
        med_coast = self.lap_coasting_spans(med_id) if med_id is not None else []
        best_coast = self.lap_coasting_spans(best)

        # Odometer totals so summarize can project each corner window onto each lap's OWN odometer
        # before matching that lap's brake/coast events (which live in its own odometer — D13). The
        # corner edges (c.enter/c.exit) are in the corner basis' reference total; the median/best
        # events are in their own laps' totals. Mirrors lap_corner_grip's projection.
        basis = self._corner_basis()
        corner_dist_total = float(basis[1]) if basis is not None else None
        best_lap_total = self.best_lap_total_distance()
        med_td = self._lap_time_dist(med_id) if med_id is not None else None
        median_lap_total = float(med_td[1][-1]) if med_td is not None else None

        return coaching.summarize(
            corners=corner_list,
            candidate_lap_ids=cand_ids,
            lap_times=lap_times,
            corner_times_by_lap=corner_times_by_lap,
            best_corner_times=best_corner_times,
            sigmas_by_cid=sigmas_by_cid,
            median_brake_events=med_brakes,
            best_brake_events=best_brakes,
            median_coast_spans=med_coast,
            best_coast_spans=best_coast,
            median_apex_deltas=median_apex_deltas,
            corner_dist_total=corner_dist_total,
            median_lap_total=median_lap_total,
            best_lap_total=best_lap_total,
        )

    def corner_entry_media_time(self, lap_id: int, cid: int) -> float | None:
        """The media-clock time (s) at which `lap_id` ENTERS corner `cid` — the jump-to seek
        target for an opportunity (seek the best lap to the corner's entry). The corner's
        reference-odometer enter point is projected onto this lap by normalized distance (the
        SAME projection lap_corner_stats / lap_corner_grip use), then the lap's elapsed→media
        time is read at that distance. None when the corner/lap is unknown or degenerate.

        Returned as an ABSOLUTE media time (lap start + elapsed at entry) so the caller can
        hand it straight to video.seek, like the lap-select seek does."""
        basis = self._corner_basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        corner = next((c for c in corner_list if c.cid == cid), None)
        if corner is None:
            return None
        td = self._lap_time_dist(lap_id)
        if td is None:
            return None
        times, dists = td
        total_lap = float(dists[-1])
        if total_lap <= 0:
            return None
        d_enter = corner.enter / total_ref * total_lap  # project onto THIS lap's odometer
        return float(np.interp(d_enter, dists, times))

    # ----------------------------------------------------- driving channels (F5)
    # Brake events / coasting spans / per-corner grip, derived in studio/driving.py (pacer-free)
    # off the VALIDATED vehicle-frame g (the gmeter ACCL->kart series, GPS-cross-checked at
    # load). The brake/coast thresholds come from this session's OWN g distribution (no magic
    # constants — see driving.derive_thresholds); all per-lap results cache per segmentation and
    # clear with the corner caches in set_timing_lines.

    def _lap_g_arrays(self, lap_id: int):
        """(long_g, lat_g) for a lap, aligned 1:1 to the lap's cached (dist, elapsed) arrays.

        The g series (self._gmeter) lives on the MEDIA clock — the SAME clock the lap's own
        per-point `times` come from (both flow from self.tt) — so the per-lap g is just the
        meter's long/lat g interpolated at the lap's media times. Returns (None, None) when
        there's no g signal (no IMU and no GPS-fallback meter) or the lap is degenerate, so the
        callers degrade to empty channels."""
        gm = self._gmeter
        if not gm.has_data:
            return None, None
        td = self._lap_time_dist_elapsed(lap_id)
        if td is None:
            return None, None
        times, _dists, _elapsed = td
        long_g = np.interp(times, gm.times, gm.long_g)
        lat_g = np.interp(times, gm.times, gm.lat_g)
        return long_g, lat_g

    def driving_thresholds(self):
        """The brake/coast thresholds (driving.Thresholds) derived ONCE from this session's
        own full-session g distribution over its moving samples — no magic constants. None when
        there's no g signal. Cached for the recording (the g series is constant); reported at
        load via .describe(). Built from the WHOLE g series + the matching speed (resampled to
        the g clock), so the distribution reflects the entire session, not one lap."""
        if self._driving_thresholds_cache is not _UNSET:
            return self._driving_thresholds_cache
        gm = self._gmeter
        if not gm.has_data:
            self._driving_thresholds_cache = None
            return None
        # Speed (km/h) on the g clock: the full-trace speed tv (km/h) interpolated onto gm.times
        # (the trace + the g series share the media clock).
        speed_kmh = np.interp(gm.times, self.tt, self.tv)
        self._driving_thresholds_cache = driving.derive_thresholds(
            gm.long_g, gm.lat_g, speed_kmh)
        return self._driving_thresholds_cache

    def lap_brake_events(self, lap_id: int) -> list[driving.BrakeEvent]:
        """Braking zones detected on one lap's longitudinal-g series (onset odometer/time,
        peak decel, duration — see driving.BrakeEvent), in track order. [] when there's no g
        signal or the lap is degenerate. Cached per lap; cleared on re-segment."""
        got = self._brake_events_cache.get(lap_id)
        if got is not None:
            return got
        th = self.driving_thresholds()
        long_g, _lat_g = self._lap_g_arrays(lap_id)
        td = self._lap_time_dist_elapsed(lap_id)
        if th is None or long_g is None or td is None:
            return []
        _times, dists, elapsed = td
        events = driving.brake_events(dists, elapsed, long_g, th.theta_b)
        self._brake_events_cache[lap_id] = events
        return events

    def lap_coasting_spans(self, lap_id: int) -> list[driving.CoastSpan]:
        """Coasting spans (neither braking/accelerating nor cornering) on one lap, in track
        order — see driving.CoastSpan. [] when there's no g signal or the lap is degenerate.
        Cached per lap; cleared on re-segment."""
        got = self._coasting_spans_cache.get(lap_id)
        if got is not None:
            return got
        th = self.driving_thresholds()
        long_g, lat_g = self._lap_g_arrays(lap_id)
        td = self._lap_time_dist_elapsed(lap_id)
        if th is None or long_g is None or td is None:
            return []
        _times, dists, _elapsed = td
        _d, speed_kmh, elapsed = self._lap_arrays(lap_id)
        spans = driving.coasting_spans(dists, elapsed, speed_kmh[:len(dists)], long_g, lat_g,
                                       th.theta_c, th.theta_lat)
        self._coasting_spans_cache[lap_id] = spans
        return spans

    def lap_corner_grip(self, lap_id: int) -> list[float]:
        """Per-corner friction-circle grip utilization for one lap (median |g| / lap-envelope
        max inside each corner window, in (0,1]; higher in the hard corners — see
        driving.corner_grip), one value per detected corner in track order. [] when there's no
        g signal, no corners, or the lap is degenerate. Cached per lap; cleared on re-segment."""
        got = self._corner_grip_cache.get(lap_id)
        if got is not None:
            return got
        long_g, lat_g = self._lap_g_arrays(lap_id)
        basis = self._corner_basis()
        if long_g is None or basis is None or not basis[0]:
            return []
        corner_list, total_ref = basis
        td = self._lap_time_dist_elapsed(lap_id)
        if td is None:
            return []
        _times, dists, _elapsed = td
        total_lap = float(dists[-1])
        if total_lap <= 0:
            return []
        # Project each corner's reference-odometer window onto this lap by normalized distance
        # (d_lap = d_ref / total_ref * total_lap) — the SAME projection lap_corner_stats uses.
        windows = [(c.enter / total_ref * total_lap, c.exit / total_ref * total_lap)
                   for c in corner_list]
        grip = driving.corner_grip(dists, long_g, lat_g, windows)
        self._corner_grip_cache[lap_id] = grip
        return grip

    def lap_brake_map_markers(self, lap_id: int) -> list[tuple[float, float, float]]:
        """(x, y, peak_decel) per brake onset on one lap, in LOCAL metres on that lap's own
        trace — for the map's brake glyphs. peak_decel (g) drives the glyph size. [] when no
        brake events. The onset odometer is mapped to the lap's (x, y) via the lap's cached
        columns (the same cum->xy interpolation corner_map_markers uses)."""
        events = self.lap_brake_events(lap_id)
        if not events:
            return []
        _t, xs, ys, _v, cum = self._lap_columns(lap_id)
        onsets = np.asarray([e.onset_dist for e in events])
        mx = np.interp(onsets, cum, xs)
        my = np.interp(onsets, cum, ys)
        return [(float(mx[i]), float(my[i]), e.peak_decel) for i, e in enumerate(events)]

    def lap_brake_plot_positions(self, lap_id: int, mode: str) -> list[tuple[float, float]]:
        """(plot-x, peak_decel) per brake onset on one lap, on the speed chart's SHARED axis
        for `mode` ('distance' or 'time') — the SAME axis the curves/sector lines use, so the
        glyphs sit on the speed trace. [] when no brake events / no best lap (distance mode).
          * 'distance': x = (onset_dist / lap_total) * best_distance  (the s*best_distance axis)
          * 'time':     x = onset_time (elapsed into the lap)
        peak_decel (g) is returned alongside so the caller can size the glyph."""
        events = self.lap_brake_events(lap_id)
        if not events:
            return []
        if mode == "time":
            return [(e.onset_time, e.peak_decel) for e in events]
        # 'distance' — normalize by this lap's total, scale to the ACTIVE baseline's distance
        # (the reference total when one is loaded) so the glyphs sit on the curves/cursor, which
        # delta() scales the same way. Same single-source as the cursor mappers (D12).
        best = self.best_lap_id()
        td = self._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        best_total = self.active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(e.onset_dist / total_lap * best_total, e.peak_decel) for e in events]

    def lap_coasting_plot_spans(self, lap_id: int, mode: str) -> list[tuple[float, float]]:
        """(plot-x0, plot-x1) per coasting span on one lap, on the speed chart's SHARED axis
        for `mode` — for the shaded coast regions on the speed chart. Same projection as
        lap_brake_plot_positions. [] when no spans / no best lap (distance mode)."""
        spans = self.lap_coasting_spans(lap_id)
        if not spans:
            return []
        if mode == "time":
            # Elapsed at each span edge: interp the span's odometer edges into the lap's elapsed.
            td = self._lap_time_dist_elapsed(lap_id)
            if td is None:
                return []
            _times, dists, elapsed = td
            return [(float(np.interp(s.start_dist, dists, elapsed)),
                     float(np.interp(s.end_dist, dists, elapsed))) for s in spans]
        best = self.best_lap_id()
        td = self._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        # ACTIVE baseline total (reference when loaded) — the shared axis delta() uses (D12).
        best_total = self.active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(s.start_dist / total_lap * best_total, s.end_dist / total_lap * best_total)
                for s in spans]

    def library_entry(self, paths: list[str]) -> dict:
        """Build this recording's session-library entry (F8) — a plain dict fed to the
        pacer-free ``studio.library`` index. PACER stays on THIS side of the seam (the values
        come from Session accessors); library.py never imports pacer.

        Identity: the recording's CHAPTER-INVARIANT fingerprint, derived from the FIRST chapter's
        stem (via ``chapters.discover_siblings`` — the same recording-not-file rule the timing-line
        sidecar uses) by ``library.fingerprint``, which strips the chapter index so a single-chapter
        open and a full chaptered open of the same recording share ONE entry (the duration, which
        differs between those two opens, is deliberately NOT in the key). `paths` are the file
        path(s) as opened, stored ABSOLUTE so the dialog's file-exists check is cwd-independent."""
        first = chapters.discover_siblings(paths[0])[0] if paths else ""
        stem = os.path.splitext(os.path.basename(first))[0] if first else ""
        best_id = self.best_lap_id()
        best = self.lap_time(best_id) if best_id is not None else None
        return {
            "fingerprint": library.fingerprint(stem),
            "stem": stem,
            "track": self.track_name,
            "date": self.session_date(),
            "lap_count": len(self.valid_lap_ids()),
            "best": best,
            "theoretical": self.theoretical_best(),
            "paths": [os.path.abspath(p) for p in paths],
        }

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
    #   * DISTANCE mode (both plots): x = s × baseline_total_dist, where s = dist_in_lap(t)/lap_total
    #     is the NORMALIZED distance fraction. This is the SAME axis the curves are drawn on
    #     (session.delta maps every lap's s∈[0,1] through the ACTIVE baseline's distance — the
    #     cross-recording reference's total when one is loaded, else the local best), so a cursor
    #     placed here sits exactly on its curve AND coincides with the other plot's cursor. Pass
    #     `active_baseline_total_distance()` as `best_distance` so both halves use the SAME total.
    #     Inverse: s = x / baseline_total; dist_in_lap = s × this lap's total; then interp→time.
    # 'distance' and 'delta' are the SAME shared-distance mode (delta is kept as a readable alias
    # for the signal the delta-plot cursor emits). All clamp to the lap window so a drag can't
    # escape the current lap.

    def media_time_at_plot_x(self, lap_id: int, x: float, mode: str,
                             best_distance: float | None = None) -> float | None:
        """Absolute media-clock time (s) for a plot x-value within `lap_id`.

        `mode` is 'time' (time-into-lap x, seconds) or 'distance'/'delta' (the SHARED distance
        axis, x = s × best_distance metres — both plots use it, so the cursors coincide). For
        the distance/delta modes pass the ACTIVE baseline's total distance as `best_distance`
        (`active_baseline_total_distance()` — the reference total when one is loaded, else the
        local best) so this inverts delta()'s x-grid exactly. The result is CLAMPED to `lap_id`'s
        [start, end] media window so a drag can't leave the current lap. Returns None if the lap
        is degenerate (so the caller can no-op)."""
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

        CROSS-RECORDING REFERENCE (F7): when one is loaded, the Δ baseline is the REFERENCE
        lap's curve (from another recording) instead of the local best lap, and the returned
        baseline id is the sentinel `REFERENCE_ID` (< 0, no local lap). The reference curve is
        itself emitted under `REFERENCE_ID` in `speed`/`delta` (its self-Δ is the flat 0
        baseline, exactly as the best lap's is today), so the chart draws it as the green
        reference. The alignment is unchanged — still normalized distance — so the Δ endpoint
        is `lap_total_time − reference_total_time`, the cross-recording laptime difference.
        DORMANT: with no reference, `ref_arrays` is None and this is byte-identical to before.
        """
        ids = [i for i in lap_ids if 0 <= i < self.laps.laps_count()]
        ref_arrays = self._ref_arrays()  # None unless a cross-recording reference is loaded
        if ref_arrays is None:
            best = self.best_lap_id()
            if not ids or best is None:
                return None
        else:
            # The reference replaces the local best as the baseline; it has no local id, so use
            # the sentinel and never require a local best to exist.
            best = REFERENCE_ID
            if not ids:
                return None

        arrays = {}
        local_ids = set(ids) if ref_arrays is not None else set(ids) | {best}
        for lid in local_ids:
            dist, speed_kmh, elapsed = self._lap_arrays(lid)
            if len(dist) >= 2 and dist[-1] > 0:
                arrays[lid] = (dist, speed_kmh, elapsed)
        if ref_arrays is not None:
            r_dist, r_speed, r_elapsed = ref_arrays
            if len(r_dist) >= 2 and r_dist[-1] > 0:
                arrays[REFERENCE_ID] = (r_dist, r_speed, r_elapsed)
        if best not in arrays:
            return None

        # Common grid in normalized distance fraction [0,1]; the same fraction is the same
        # track position on every lap, so the last point (s=1) is the finish line for all.
        s_grid = np.linspace(0.0, 1.0, self._DELTA_GRID_N)
        best_dist, _, best_elapsed = arrays[best]
        # Distance mode keeps the x-axis in metres via the baseline lap's distance (one shared
        # x): the local best normally, or the cross-recording reference's distance when active.
        # `best_dist[-1]` here is exactly `active_baseline_total_distance()` (the same baseline
        # array) — the distance-mode cursor mappers read that accessor so they agree with this
        # grid even when the reference and local-best totals differ (the D12 single-source).
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

        Single-sourced through `delta_at_lap`: resolve the lap containing `t`, then delegate to
        the shared normalized-distance alignment against the active baseline (the local best, or
        the cross-recording reference when one is loaded). For the dormant case this equals the
        old delta_between(lap, best, t) (cross-checked equal in test_compare)."""
        lap_id = self.lap_at_time(t)
        if lap_id is None:
            return None
        return self.delta_at_lap(lap_id, t)

    def delta_at_lap(self, lap_id: int, t: float) -> float | None:
        """Δ-to-baseline (seconds) at media-clock time `t`, given the already-resolved `lap_id`
        containing `t`. Splits the lap resolution out of `delta_at_time` so the tick can resolve
        `lap_at_time(t)` ONCE per frame and reuse it for both the readout and the delta (the lap
        lookup is no longer done twice). Same math/result as `delta_at_time`.

        The baseline is the local best lap normally, or the CROSS-RECORDING reference lap when
        one is loaded (F7) — both are just a `LapCurve` (via `baseline_curve()`), consumed the
        SAME way, so the only change with a reference active is which curve `s` is inverted onto.
        DORMANT: with no reference, the baseline is the best lap's curve, byte-identical to before.

        F2: open-coded `s = interp(t, times, dists)/dists[-1]` then `interp(s × baseline_total,
        baseline_dists, baseline_elapsed)` is now `src.fraction_at_time` + `project(s, baseline)`
        — same arithmetic, no behaviour change."""
        baseline = self.baseline_curve()  # reference lap's curve when loaded, else local best's
        src = self._lap_curve(lap_id)
        if src is None or baseline is None:
            return None
        if src.total <= 0:
            return None
        s = src.fraction_at_time(t)  # normalized fraction [0,1]
        elapsed_lap = src.elapsed_at_time(t)  # = t − lap_start, clamped
        if baseline.total <= 0:
            return None
        # Baseline's elapsed time at the SAME track fraction s (invert s→baseline distance→time).
        return elapsed_lap - project(s, baseline)

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

        O(1) on the cached per-lap arrays — cheap enough for the 30 Hz tick. F2: the same
        `LapCurve` source-fraction → `project()` onto baseline `b` the whole Δ family shares; here
        the baseline is just an ARBITRARY lap rather than the active baseline."""
        curve_a = self._lap_curve(lap_a)
        curve_b = self._lap_curve(lap_b)
        if curve_a is None or curve_b is None:
            return None
        if curve_a.total <= 0:
            return None
        # lap_a's normalized track fraction s and its own elapsed time at t_in_a (clamped to lap).
        s = curve_a.fraction_at_time(t_in_a)  # [0,1]
        elapsed_a = curve_a.elapsed_at_time(t_in_a)  # = t_in_a − start
        if curve_b.total <= 0:
            return None
        # lap_b's elapsed time at the SAME track fraction s (invert s → b's distance → time).
        return elapsed_a - project(s, curve_b)

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
