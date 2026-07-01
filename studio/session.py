"""Session: UI-friendly accessors over a `pacer.Laps` + the Δ glue.

C++ analysis (segmentation, distances, lap timing, delta resample) comes from `pacer`;
the load pipeline is in studio/load.py, map-render caching in studio/render_cache.py
(Session keeps thin delegators).

Coordinate ordering: the trace and timing lines live in LOCAL meters (cs.local), so
`pick_random_start()`/`update()` must run AFTER `set_coordinate_system()`.
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
    bests as bests_service,
)
from . import (
    chapters,
    coaching,
    consistency,
    corner_model,
    cross_reference,
    data_quality,
    driving_channels,
    gapfill,
    gmeter,
    library,
    render_cache,
    timeline,
    tracks,
)
from ._signal import (
    SMOOTH_WINDOW,
    _band_lap_ids,
    fmt_time,  # noqa: F401  (re-export for call sites; lives in _signal now)
)
from .load import load_recording


def _default_sample_path() -> str:
    """The bundled sample clip path. In a normal checkout it's the repo-relative path (resolved
    against the cwd, which is the repo root for `python -m studio`). In a frozen PyInstaller .app
    the cwd is unpredictable (often "/"), so resolve it against sys._MEIPASS where pacer.spec adds
    the sample as a datas entry — otherwise the bundled "Open demo" sample would never be found."""
    import sys

    rel = "3rdparty/gpmf-parser/samples/hero6.mp4"
    base = getattr(sys, "_MEIPASS", None) if getattr(sys, "frozen", False) else None
    return os.path.join(base, rel) if base else rel


DEFAULT_SAMPLE = _default_sample_path()  # a clip with real motion

_UNSET = object()  # sentinel for "cache not yet computed" where None is a valid cached value

_EMPTY = np.empty(0)  # the `speed` slot for a LapCurve whose speed series isn't needed (Δ family)

# Sentinel "lap id" for the cross-recording reference lap (F7): negative, so it can never
# collide with a real lap id (>= 0). Exposed so plots_view can request/label the reference
# curve without importing the ReferenceLap type.
REFERENCE_ID = -1

# Per-lap columns `_lap_columns` caches: (times, xs, ys, full_speed m/s, cum_distances).
LapColumns = tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]
# One (x, y) plot series per lap id — the speed/delta payloads `delta()` returns.
LapSeries = dict[int, tuple[np.ndarray, np.ndarray]]


@dataclass(frozen=True)
class LapCurve:
    """One lap's arc-length-aligned curves — the Δ baseline value object (F2). `dist` = per-lap
    odometer (m, monotonic), `elapsed` = seconds-from-lap-start, `speed` km/h, all index-aligned;
    `times` = media-clock axis (for a cross-recording reference, which lives on no shared clock,
    `times` IS its 0-anchored `elapsed`). A baseline is just a `LapCurve`, so the local best lap
    and the cross-recording reference are the SAME type with no reference-vs-best branch. `total`
    = `float(dist[-1])`; callers guard `total <= 0` for a degenerate lap."""

    dist: np.ndarray
    elapsed: np.ndarray
    times: np.ndarray
    speed: np.ndarray

    @property
    def total(self) -> float:
        """Total odometer distance (m) = dist[-1]."""
        return float(self.dist[-1])

    def fraction_at_time(self, t: float) -> float:
        """Track fraction s in [0,1] at media time `t` (np.interp clamps `t` to the lap)."""
        return float(np.interp(t, self.times, self.dist)) / float(self.dist[-1])

    def elapsed_at_time(self, t: float) -> float:
        """Elapsed-into-lap (s) at media time `t`, clamped to the lap (= t − lap_start)."""
        return float(np.interp(t, self.times, self.elapsed))

    def elapsed_at_fraction(self, s: float) -> float:
        """This curve's elapsed time at track fraction `s` — invert s → distance (s × total) →
        elapsed via the monotonic odometer. The one "project a fraction onto a lap" primitive."""
        return float(np.interp(s * float(self.dist[-1]), self.dist, self.elapsed))


def project(s: float, baseline: LapCurve) -> float:
    """Baseline curve's elapsed time at source fraction `s` — the one alignment primitive.
    Δ = source_elapsed − project(s, baseline)."""
    return baseline.elapsed_at_fraction(s)


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
        # Ordered chapter list + cumulative offsets (global<->chapter time map); None only for an
        # empty session. The VIDEO layer uses it to switch sources / span the slider.
        self.chapters = chapter_map
        # Per-lap caches below are all fetched in ONE bulk pacer crossing per lap and cleared on
        # re-segment (set_timing_lines); the 30 Hz tick reads them many times per frame.
        self._cols_cache: dict[int, LapColumns] = {}  # per-lap (times, xs, ys, speed_mps, cum)
        # (times, dists, elapsed); elapsed precomputed once so per-tick delta math doesn't re-subtract.
        self._dist_cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}
        self._xyt_cache: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}  # (xs, ys, times) local m
        self._valid_cache: list[int] | None = None  # memoized "real lap" set
        self._best_cache: object = _UNSET   # sentinel: None is a legal "no best lap" result
        # Detected registry track name, or None for an unknown track (start line auto-fitted).
        # Persisted into the timing-line sidecar and used by the app's "unknown track" notice.
        self.track_name: str | None = None
        # Timing-trust marker (see timing_verified): True once the USER has explicitly placed/
        # confirmed the start/finish line (a drag, or a restored sidecar that recorded the
        # confirmation). A detected track is trusted on its own (track_name), so this only matters
        # on an unknown track whose start line was auto-fitted. set_timing_lines() flips it True
        # (an explicit edit IS the confirmation); the sidecar persists it across reloads.
        self._timing_user_confirmed = False
        # Data-quality signal (the timing-ACCURACY axis, orthogonal to the timing-TRUST surface
        # above): which per-sample time clock the load built (GPS9 true clock vs the ~0.1%-fast
        # media-clock fallback on an older GPS5 camera) + the gate's dropped-fix fraction. Set by
        # Session.load from the load pipeline; a from-scratch Session() defaults to high quality
        # (no degradation), so the no-__init__ test path reads clean. See studio/data_quality.py.
        self._timing_quality = data_quality.TimingQuality()
        # Vehicle-frame g from the GoPro ACCL+GRAV+CORI, cross-checked vs GPS-derived g. Built in
        # load(); empty until then, so a from-scratch Session() just has no g signal.
        self._gmeter: gmeter.GMeter = gmeter._empty()
        # Corner-model service (detection + per-lap stats + session bests, all derived from the
        # segmentation). invalidate() on re-segment; invalidate_stats() when only the Δ baseline moved.
        self._cornermodel = self._build_corner_model()
        # Driving-channel service (brake/coast/grip + thresholds). The thresholds depend only on
        # the constant g series so they survive a re-segment; the per-lap caches clear via invalidate().
        self._driving = self._build_driving_channels()
        # Session-summary bests service (headline best / session-best splits / theoretical / rolling).
        # Stateless apart from best_lap_id's memo, which stays in Session._best_cache (cleared in
        # set_timing_lines, seeded by tests) via injected cache accessors — nothing to invalidate here.
        self._bests = self._build_bests()

        # Cross-recording reference lap (F7): a lap from another recording that REPLACES the local
        # best as the Δ baseline everywhere a delta is drawn. None = DORMANT (every "vs best" path
        # falls back to the local best). NOT touched by set_timing_lines (the reference is frozen).
        self._reference: cross_reference.ReferenceLap | None = None
        # F7 Phase B: keep the live reference Session so pane B can play its footage with its own
        # telemetry (g / lap window / lap id). None = DORMANT.
        self._reference_session: Session | None = None

        # Full-trace local-metre xs/ys + media time + km/h speed, in one track_columns crossing
        # (bulk, like _lap_columns).
        cols = laps.track_columns()
        self.tx = np.asarray(cols.xs)
        self.ty = np.asarray(cols.ys)
        self.tt = np.asarray(cols.times)
        self.tv = np.asarray(cols.full_speed) * 3.6

        # Map-render cache (per-lap gap-filled draw segments); see studio/render_cache.py.
        # invalidate() on re-segment.
        self._render_cache = render_cache.LapRenderCache(
            lap_xyt=self._lap_trace_xyt,
            valid_lap_ids=self.valid_lap_ids,
            lap_has_dropout=self.lap_has_dropout,
            lap_time=self.lap_time,
            trace_times=self.tt,
        )

        # Cursor/plot/video-sync coordinate conversions (studio/timeline.py); owns the lap-window
        # table, invalidate() on re-segment. Session keeps thin delegators over it (lap_at_time …).
        self._timeline = self._build_timeline()

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, paths: list[str], smooth_window: int = SMOOTH_WINDOW) -> Session:
        """Public load entry point — delegates the pipeline to `load.load_recording` (see
        studio/load.py) and wraps the result in a Session.

        Per-sample time comes from the GPS9 fix timestamps, re-anchored per run to the media
        clock. The GPS track is quality-gated and boxcar-smoothed (window `smooth_window`)
        before the core sees it; `smooth_window=1` disables smoothing (raw trace, for baselines)."""
        (laps, cs, video_path, chapter_map, imu, track_name,
         timing_quality) = load_recording(paths, smooth_window)
        session = cls(laps, cs, video_path, chapter_map)
        session.track_name = track_name
        session._timing_quality = timing_quality
        if imu is not None:
            session._build_gmeter(*imu)
        return session

    def _build_gmeter(self, accl, grav, cori) -> None:
        """Precompute vehicle-frame g(t) from the already-read GoPro IMU (accl/grav/cori), aligned
        to the session's smoothed GPS trace + media clock. Reports the ACCL-vs-GPS cross-check +
        driving thresholds at load; degrades to an empty meter if the transform fails (additive)."""
        try:
            # Per-chapter alignment spans: CORI is referenced to each chapter's own capture start,
            # so the camera->ENU yaw must be fit independently per chapter.
            seg_bounds = None
            if self.chapters is not None and len(self.chapters.chapters) > 1:
                chs = self.chapters.chapters
                seg_bounds = [(c.offset, chs[i + 1].offset if i + 1 < len(chs) else c.offset + 1e9)
                              for i, c in enumerate(chs)]
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
        # Report the driving-channel thresholds derived from this session's g distribution.
        try:
            th = self.driving.thresholds()
            if th is not None:
                print(f"studio: {th.describe()}", flush=True)
        except Exception as e:  # noqa: BLE001 — additive diagnostics only
            print(f"studio: driving-channel thresholds unavailable ({e!r}).", flush=True)

    # ----------------------------------------- cross-recording reference lap (F7)
    # A lap from another recording that replaces the local best as the Δ baseline everywhere a
    # delta is drawn. baseline_curve() (per-tick Δ + sector guides) and _ref_arrays (delta()'s
    # grid) consult the seam: baseline_curve() hands back the reference AS A `LapCurve`, else the
    # local best's — the same type, so the delta math has no reference-vs-best branch.

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
        # F7 Phase B: keep the live reference Session so pane B can play its footage with its own
        # telemetry.
        self._reference_session = ref
        # The Δ baseline changed (best lap -> reference), so per-lap corner-stat deltas are stale;
        # invalidate_stats() drops only those (detection windows unchanged), recomputed lazily.
        self.corners.invalidate_stats()
        return None

    def clear_reference(self) -> None:
        """Drop the cross-recording reference — every "vs best" output reverts to the local
        best lap (the dormant state). No-op if none is loaded."""
        if self._ref is None:
            return
        self._reference = None
        # F7 Phase B: drop the retained live reference Session too (frees its decode/arrays).
        self._reference_session = None
        # Revert the Δ baseline to the local best; the per-lap deltas vs the cleared reference are
        # stale (detection windows unchanged), recomputed lazily.
        self.corners.invalidate_stats()

    @property
    def _ref(self) -> cross_reference.ReferenceLap | None:
        """The active reference (or None). getattr-guarded for the bare-Session (no-__init__)
        test path, where the slot is absent and so reads as dormant."""
        return getattr(self, "_reference", None)

    # The two composed services receive Session-bound CALLABLES over Session's own primitives
    # (the studio/render_cache.py dependency-injection pattern), so neither reaches a `_`-private
    # attribute of Session — Session owns the pacer side + the g-meter + the active reference and
    # wires its privates into the callables here. Built in __init__ and re-built lazily by _cm/_dc
    # for the bare-Session (no-__init__) test path.
    def _build_corner_model(self) -> corner_model.CornerModel:
        return corner_model.CornerModel(
            reference_id=REFERENCE_ID,
            best_lap_id=self.best_lap_id,
            valid_lap_ids=self.valid_lap_ids,
            lap_has_dropout=self.lap_has_dropout,
            lap_columns=self._lap_columns,
            lap_arrays=self._lap_arrays,
            lap_time_dist=self._lap_time_dist,
            reference=lambda: self._ref,
        )

    def _build_driving_channels(self) -> driving_channels.DrivingChannels:
        return driving_channels.DrivingChannels(
            gmeter=lambda: self._gmeter,
            trace_times=lambda: self.tt,
            trace_speed_kmh=lambda: self.tv,
            lap_arrays=self._lap_arrays,
            lap_time_dist=self._lap_time_dist,
            lap_time_dist_elapsed=self._lap_time_dist_elapsed,
            lap_columns=self._lap_columns,
            best_lap_id=self.best_lap_id,
            valid_lap_ids=self.valid_lap_ids,
            active_baseline_total_distance=self.active_baseline_total_distance,
            corner_basis=lambda: self.corners.basis(),
            lap_corner_stats=lambda lap_id: self.corners.lap_corner_stats(lap_id),
        )

    def _build_bests(self) -> bests_service.Bests:
        # `lap_time` reads Session's pacer `laps.lap_time` (NOT the `self.lap_time` accessor, which
        # some tests patch on the instance) so the moved bodies read exactly what the originals did.
        # best_lap_id's memo lives in Session._best_cache (cleared in set_timing_lines, seeded by the
        # pure-Python tests), reached via these get/set accessors + the shared _UNSET sentinel.
        return bests_service.Bests(
            valid_lap_ids=self.valid_lap_ids,
            lap_has_dropout=self.lap_has_dropout,
            lap_time=lambda i: self.laps.lap_time(i),
            lap_sector_splits=self.lap_sector_splits,
            sector_line_count=lambda: len(self.laps.sectors.sector_lines),
            lap_columns=self._lap_columns,
            best_cache_get=lambda: self._best_cache,
            best_cache_set=self._set_best_cache,
            unset=_UNSET,
        )

    def _set_best_cache(self, value: object) -> None:
        self._best_cache = value

    @property
    def bests(self) -> bests_service.Bests:
        """The session-summary bests service (headline best / session-best splits / theoretical /
        rolling, F1) — call session.bests.best_lap_id() / .session_best_splits() etc. directly (the
        public methods below are thin delegators over it). Lazily created; getattr-guarded for the
        bare-Session (no-__init__) test path — see _ref."""
        bs = getattr(self, "_bests", None)
        if bs is None:
            bs = self._build_bests()
            self._bests = bs
        return bs

    @property
    def corners(self) -> corner_model.CornerModel:
        """The corner-analysis service (corner detection + per-lap stats + session bests, on the
        best-lap odometer basis) — call session.corners.corner_list() / .lap_corner_stats(lap) /
        .basis() etc. directly. Lazily created; getattr-guarded for the bare-Session (no-__init__)
        test path — see _ref."""
        cm = getattr(self, "_cornermodel", None)
        if cm is None:
            cm = self._build_corner_model()
            self._cornermodel = cm
        return cm

    @property
    def driving(self) -> driving_channels.DrivingChannels:
        """The driving-channels service (brake/coast/grip + thresholds, F5/D3/D4/D5) — call
        session.driving.thresholds() / .lap_brake_events(lap) / .lap_grip_utilization(lap) etc.
        directly. Lazily created; getattr-guarded for the bare-Session (no-__init__) path — see _ref."""
        dc = getattr(self, "_driving", None)
        if dc is None:
            dc = self._build_driving_channels()
            self._driving = dc
        return dc

    def _build_timeline(self) -> timeline.Timeline:
        # All inputs are live-reading lambdas (not bound-method snapshots) so the extracted
        # conversions resolve Session's primitives exactly when the originals did — incl. tests
        # that monkey-patch s._lap_trace_xyt after the (lazily-built) timeline exists.
        return timeline.Timeline(
            lap_time_dist=lambda lid: self._lap_time_dist(lid),
            lap_trace_xyt=lambda lid: self._lap_trace_xyt(lid),
            valid_lap_ids=lambda: self.valid_lap_ids(),
            lap_window=lambda lid: self.lap_window(lid),
            trace_times=lambda: self.tt,
            trace_xs=lambda: self.tx,
            trace_ys=lambda: self.ty,
        )

    @property
    def timeline(self) -> timeline.Timeline:
        """The cursor/plot/video-sync coordinate-conversion service (studio/timeline.py). The
        public conversions below (lap_at_time / index_at_time / media_time_at_plot_x / nearest_*)
        are thin delegators over it. Lazily created; getattr-guarded for the bare-Session
        (no-__init__) test path — see _ref."""
        tl = getattr(self, "_timeline", None)
        if tl is None:
            tl = self._build_timeline()
            self._timeline = tl
        return tl

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
    # The retained live reference Session + the lookups pane B needs (ChapterMap video source,
    # locked lap id, overlay-line ghost position, reference-vs-primary Δ), so CompareController
    # can route pane B through the reference without importing pacer.
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
          * `s` ∈ [0, 1] — the reference lap's NORMALIZED track fraction, clamped to the window;
          * `elapsed_into_lap` — the reference's own time-into-lap (s), clamped to [0, total_time].
        None when there's no reference, no retained reference Session, or the lap is degenerate.

        WHY rebase: `t_ref` is the reference recording's GLOBAL clock but the reference's
        dist/elapsed arrays are from-0, so subtract the lap-window start (t_into = t_ref −
        window[0]) before interpolating, else a ~1000 s t_ref clamps a [0..60] axis to the finish."""
        ref = self._ref
        if ref is None:
            return None
        ref_sess = self.reference_session()
        if ref_sess is None:
            return None
        window = ref_sess.lap_window(ref.lap_id)
        if window is None:
            return None
        # The reference's OWN from-0 arc-length curves, so t_into and these arrays share one zero.
        _times, dists, elapsed = ref.time_dist_elapsed()
        total_dist = float(dists[-1]) if len(dists) else 0.0
        total_time = float(elapsed[-1]) if len(elapsed) else 0.0
        if total_dist <= 0 or total_time <= 0:
            return None
        t_into = t_ref - float(window[0])  # GLOBAL reference clock -> seconds-into-the-reference-lap
        # np.interp clamps t_into to [0, total_time], so s and elapsed below are window-clamped.
        s = float(np.interp(t_into, elapsed, dists)) / total_dist  # [0, 1]
        s = min(max(s, 0.0), 1.0)
        elapsed_into_lap = float(np.interp(t_into, elapsed, elapsed))
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
        """Δ (s) of the reference lap vs primary `lap_id` at the reference's position at GLOBAL
        ref clock `t_ref`; pane B's badge. None if degenerate.

        Mirror of pane A: source = reference (via `_reference_progress_at`), baseline = primary."""
        prog = self._reference_progress_at(t_ref)
        if prog is None:
            return None
        s, elapsed_ref = prog  # reference's track fraction + its own time-into-lap, both clamped
        primary = self._lap_curve(lap_id)  # the baseline here is the PRIMARY lap's curve
        if primary is None:
            return None
        if primary.total <= 0:
            return None
        return elapsed_ref - project(s, primary)

    # Delta seam: baseline_curve() (LapCurve) for the per-tick Δ; _ref_arrays (raw dist/speed/
    # elapsed triple) for delta()'s grid build.
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

    # ----------------------------------------------------------- timing trust
    @property
    def timing_verified(self) -> bool:
        """Whether this recording's lap timing references a TRUSTED start/finish line:

          * VERIFIED — the track was auto-detected from the track database (``track_name`` set),
            OR the user explicitly placed/confirmed the start line (a drag, or a restored sidecar
            that recorded the confirmation). Lap times / sector splits / theoretical-best are
            authoritative; the lap table paints purple session-bests, the map shows no warning.
          * PROVISIONAL (``not timing_verified``) — the start line was auto-fitted on an unknown
            track and never confirmed, so every lap time is referenced to an ARBITRARY point. The
            views de-emphasize the timing, drop the purple "validated best" cue, and show a
            persistent "drag to set start/finish" banner until this flips True.

        Trust lives here (on Session); the views just render it. getattr-guarded for the
        bare-Session (no-__init__) test path, where the slots are absent — see ``_ref``."""
        if getattr(self, "track_name", None) is not None:
            return True
        return bool(getattr(self, "_timing_user_confirmed", False))

    def confirm_timing(self) -> None:
        """Mark the start/finish line as user-confirmed (Provisional → Verified). Called when the
        user drags a timing line — an explicit edit IS the confirmation. Idempotent; persisted by
        the sidecar via ``timing_user_confirmed``."""
        self._timing_user_confirmed = True

    @property
    def timing_user_confirmed(self) -> bool:
        """The raw user-confirmation flag (for the sidecar to persist). Distinct from
        ``timing_verified``, which also counts a detected track as trusted."""
        return bool(getattr(self, "_timing_user_confirmed", False))

    # ----------------------------------------------------------- data quality (timing accuracy)
    @property
    def timing_quality(self) -> data_quality.TimingQuality:
        """The recording's data-quality verdict (the timing-ACCURACY axis — see
        studio/data_quality.py). Orthogonal to ``timing_verified`` (the start-line TRUST surface):
        a media-clock recording can be fully Verified yet still have degraded timing accuracy
        (~0.1% drift on an older GPS5 camera), and a low-DOP trace renders authoritative laps with
        no in-app cue today. ``timing_quality.degraded`` is what the data-quality banner + the
        lap-table de-emphasis read; a normal GPS9, clean-fix recording reports not-degraded so the
        UI is unchanged. getattr-guarded (defaults to high quality) for the bare-Session
        (no-__init__) test path — see ``_ref``."""
        return getattr(self, "_timing_quality", None) or data_quality.TimingQuality()

    # ----------------------------------------------------------- timing lines
    @property
    def start_line(self) -> Seg:
        return Seg.from_pacer(self.laps.sectors.start_line)

    @property
    def sector_lines(self) -> list[Seg]:
        return [Seg.from_pacer(s) for s in self.laps.sectors.sector_lines]

    def set_timing_lines(self, start: Seg, sectors: list[Seg],
                         user_confirm: bool = True) -> None:
        """Re-segment on new timing lines. `user_confirm=True` (a genuine user edit, the default)
        marks the timing user-confirmed → Verified; the sidecar-restore + revert paths pass
        `user_confirm=False` so they don't fabricate a confirmation the user never made (that's
        decided from the persisted flag in `apply_timing_lines_latlon`)."""
        if user_confirm:
            self._timing_user_confirmed = True
        self.laps.sectors = pacer.Sectors(
            start_line=start.to_pacer(),
            sector_lines=[s.to_pacer() for s in sectors],
        )
        self.laps.update()
        self._cols_cache.clear()
        self._dist_cache.clear()
        self._xyt_cache.clear()
        self._render_cache.invalidate()  # per-lap draw segments (MAP RENDERING ONLY)
        # The single re-segmentation point: every memoized "real lap" set + the timeline's lap-window
        # table is now stale (lap ids / times shifted), so drop them. They lazily recompute on next
        # access.
        self._valid_cache = None
        self._best_cache = _UNSET
        self._drop_ideal_cache()  # the ideal envelope is a min over the (now-shifted) lap arrays
        self.timeline.invalidate()
        # The corner model + driving channels are derived from / projected through the
        # segmentation — stale with the rest. Each service owns its caches and clears exactly
        # them via invalidate() (the corner basis + per-lap stats + session bests; the per-lap
        # brake/coast/grip — the driving thresholds depend only on the unchanged g series so
        # they survive), replacing the ~7 hand-cleared cache slots this block used to enumerate.
        self.corners.invalidate()
        self.driving.invalidate()

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

    def apply_timing_lines_latlon(self, start, sectors, confirmed: bool = True) -> bool:
        """Apply persisted absolute-lat/lon timing lines (the sidecar's form): convert each
        endpoint to local metres via ``cs.local`` and re-segment through set_timing_lines.

        `confirmed` (the sidecar's user-confirmation marker) is restored onto the session on
        success, so a recording whose start line the user once dragged stays VERIFIED across
        reloads (no purple-suppression / banner on a track they already fixed). A legacy sidecar
        with no marker is treated as confirmed (it could only have been written by a user edit).

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

        # user_confirm=False here: the confirmation is decided by the persisted `confirmed`
        # marker (below), not by the act of restoring — restoring isn't a fresh user edit.
        self.set_timing_lines(seg(start), [seg(s) for s in sectors], user_confirm=False)
        if self.valid_lap_ids():
            self._timing_user_confirmed = bool(confirmed)
            return True
        self.set_timing_lines(prev_start, prev_sectors, user_confirm=False)
        return False

    # ------------------------------------------ timing-line undo (edit history)
    # A bounded per-session stack of PRIOR timing-line states, captured just BEFORE each user
    # edit (the map drag re-segments AND overwrites the sidecar with no confirm; dragging the
    # start line slightly wrong would otherwise silently destroy the good timing + PB baseline).
    # Each state is a (start_latlon, sectors_latlon, confirmed) snapshot in the SAME load-invariant
    # (lat, lon) form the sidecar persists, so a restore replays through apply_timing_lines_latlon
    # — the same re-segment/apply path a fresh edit or a sidecar restore takes (no bespoke
    # re-segmentation, and the PB/session-best baseline recomputes for free from the restored lines).
    _UNDO_DEPTH = 32  # plenty for a hand-tuning session; bounds the stack so it can't grow forever

    def _timing_history(self) -> list:
        """The undo stack (lazily created — getattr-guarded for the bare-Session no-__init__ path)."""
        hist = getattr(self, "_timing_undo", None)
        if hist is None:
            hist = []
            self._timing_undo = hist
        return hist

    def push_timing_history(self) -> None:
        """Snapshot the CURRENT timing lines onto the undo stack — call BEFORE applying a user
        edit, so Undo can restore exactly what was on screen before the drag. Stored as the
        sidecar's load-invariant (start_latlon, sectors_latlon, confirmed) triple. Bounded to
        ``_UNDO_DEPTH`` (oldest dropped)."""
        start, sectors = self.timing_lines_latlon()
        hist = self._timing_history()
        hist.append((start, sectors, self.timing_user_confirmed))
        if len(hist) > self._UNDO_DEPTH:
            del hist[0]

    def can_undo_timing(self) -> bool:
        """True iff there's a prior timing-line state to undo (the Edit ▸ Undo enablement)."""
        return bool(self._timing_history())

    def undo_timing_lines(self) -> bool:
        """Restore the most recent snapshot pushed by ``push_timing_history`` — pop it and replay
        it through ``apply_timing_lines_latlon`` (the same re-segment/apply path a live edit takes,
        so the segmentation + PB/session-best baseline recompute identically, and a restored
        previously-confirmed state stays confirmed). Returns True if a state was restored, False
        when the stack is empty (Undo is then a no-op). The restore itself is NOT re-pushed, so
        repeated Undo walks back through the history one edit at a time."""
        hist = self._timing_history()
        if not hist:
            return False
        start, sectors, confirmed = hist.pop()
        self.apply_timing_lines_latlon(start, sectors, confirmed=confirmed)
        return True

    def track_location(self) -> tuple[tuple[float, float], tuple[float, float, float, float]]:
        """The recording's GPS detection anchor for the track database: ``(centroid, bbox)``
        where ``centroid`` is ``(lat, lon)`` and ``bbox`` is ``(min_lat, min_lon, max_lat,
        max_lon)``. The centroid is the trace bbox CENTRE — exactly the anchor ``load`` detects
        on (``min_max`` returns lon/lat Points: x=lon, y=lat) — so a track saved from one
        recording auto-detects on the next at the same circuit."""
        mn, mx = self.laps.min_max()
        centroid = ((mn.y + mx.y) / 2, (mn.x + mx.x) / 2)
        bbox = (float(mn.y), float(mn.x), float(mx.y), float(mx.x))
        return centroid, bbox

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
        a SINGLE pacer.Laps.lap_columns crossing: local metres + media-clock seconds + raw 3D
        speed (m/s) + the lap's gap-aware odometer, all index-aligned and the SAME length (the
        materialized lap: interpolated start crossing + interior points + interpolated finish).
        Cleared on re-segment."""
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

    def _best_candidate_ids(self) -> list[int]:
        """Delegates to the bests service (studio/bests.py) — kept as a thin Session forwarder
        for the ~call sites that read it through Session. See Bests.best_candidate_ids."""
        return self.bests.best_candidate_ids()

    def best_lap_id(self) -> int | None:
        """Delegates to the bests service (studio/bests.py). Kept as a thin Session forwarder so
        the ~41 call sites (and the tests that monkey-patch `s.best_lap_id`) keep working. The
        memo lives in Session._best_cache, which the service reads/writes through injected
        accessors. See Bests.best_lap_id."""
        return self.bests.best_lap_id()

    def best_lap_total_distance(self) -> float | None:
        """The LOCAL best lap's total odometer distance (metres), or None. The distance-mode chart
        x-axis is scaled by `active_baseline_total_distance()` (the reference total when loaded),
        not this; this stays the local-best value for callers that want the best lap's length."""
        best = self.best_lap_id()
        if best is None:
            return None
        td = self._lap_time_dist(best)
        if td is None:
            return None
        return float(td[1][-1])

    def active_baseline_total_distance(self) -> float | None:
        """Total odometer distance (m) of the ACTIVE Δ baseline: the cross-recording reference's
        total when loaded (F7), else the local best lap's. Single source for the distance-mode
        x-axis basis (x = s × total), shared by delta()'s x-grid AND the cursor mappers
        (media_time_at_plot_x / plot_x_at_media_time) so the scrub cursor stays on its curve when
        reference and local-best totals differ (D12). Dormant => `best_lap_total_distance()`."""
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
        """The media-clock times of a lap's KEPT GPS points, in order. Quality-gated / cleaned
        samples are already gone, so a large delta between consecutive entries is a real interior
        GPS dropout (not jitter)."""
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
        """Cached per-lap (xs, ys, times) — local metres + media-clock seconds, cleared on
        re-segment. The single source the map highlight, gap-fill draw, and marker-drag
        nearest-point lookup all slice from."""
        got = self._xyt_cache.get(lap_id)
        if got is None:
            times, xs, ys, _full_speed, _cum = self._lap_columns(lap_id)
            got = (xs, ys, times)
            self._xyt_cache[lap_id] = got
        return got

    # Gap-aware draw segments + the reference-centerline fallback donor live in
    # studio/render_cache.py (LapRenderCache); the delegators below keep callers on Session.

    def lap_trace_segments(self, lap_id: int) -> list[gapfill.Segment]:
        """Ordered list of `gapfill.Segment` for drawing this lap: measured GPS runs and
        reconstructed (inferred) fills, tagged so the renderer can dash/dim the inferred
        ones. MAP RENDERING ONLY — alters no analysis quantity. Cached per lap in the
        LapRenderCache (studio/render_cache.py); cleared on re-segment."""
        return self._render_cache.lap_trace_segments(lap_id)

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

    # Boundaries within 0.2% of the lap odometer are the same line (below the GPS step); fractional
    # so it scales across track lengths. Collapses duplicate/wrong-pass lines that would emit 0 s /
    # out-of-order splits and poison the theoretical best.
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
        # window is just the global point) on tiny synthetic laps. The arc constant lives with
        # best_rolling_lap in studio/bests.py — one source shared here.
        half = max(1, int(round(bests_service.ROLLING_SEARCH_FRAC * m)))
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
    # The "best" cluster (candidate set / headline best / session-best splits / theoretical /
    # rolling) is the `session.bests` service (studio/bests.py Bests); Session keeps thin
    # delegators below so the ~call sites + the tests that monkey-patch these on an instance keep
    # working. See Bests for the bodies.
    def session_best_splits(self) -> list[float | None]:
        """Delegates to the bests service (studio/bests.py). See Bests.session_best_splits."""
        return self.bests.session_best_splits()

    def theoretical_best(self) -> float | None:
        """Delegates to the bests service (studio/bests.py). See Bests.theoretical_best."""
        return self.bests.theoretical_best()

    def best_rolling_lap(self) -> float | None:
        """Delegates to the bests service (studio/bests.py). See Bests.best_rolling_lap."""
        return self.bests.best_rolling_lap()

    def sector_plot_positions(self, mode: str) -> list[tuple[str, float]]:
        """(label, plot-x) for the sector BOUNDARIES on the speed+delta charts' SHARED axis (F2).

        Boundary FRACTIONS are measured on the primary best lap (same midpoint→trace projection
        as the splits), then mapped onto the active Δ baseline's distance/time axis:
          * 'distance': x = (d_k / primary_lap_total) × baseline_distance
          * 'time':     x = baseline elapsed at that same track fraction
        Dormant => the baseline IS the primary best lap. Returns [] if there's no best lap."""
        # No sector lines → no guide lines (the chart x-origin already marks the lap start).
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
            # Dormant: baseline IS the best lap; raw d / interp(d,dists,times)−t0 kept exact (not
            # routed through project(), which would differ in the last ULP).
            if mode == "time":
                t0 = float(times[0])
                for label, d in zip(labels, edge_dists, strict=True):
                    t_at = float(np.interp(d, dists, times)) - t0  # elapsed into the best lap
                    positions.append((label, t_at))
            else:  # 'distance' — the shared s×best_distance axis (here best_distance == total)
                for label, d in zip(labels, edge_dists, strict=True):
                    positions.append((label, d))
            return positions
        # Reference active: map each boundary's primary-lap fraction onto the reference axis
        # (distance = frac × reference_total; time = project(frac, base)).
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

    # Corner analysis (detection + per-lap stats + session bests, all on the best-lap odometer
    # basis) is the `session.corners` service (studio/corner_model.py CornerModel); per-lap caches
    # clear on re-segment. Consumers call session.corners.corner_list() / .lap_corner_stats(lap)
    # etc. directly — no Session forwarders.

    # ------------------------------------------------------------- data export (F11)
    # The export writers (studio/export_data.py) are pacer-free, so the two accessors below own
    # the export's only pacer crossings (per-sample channel arrays + the GPS9 wall-clock date).

    def lap_channels(self, lap_id: int) -> dict[str, np.ndarray]:
        """Index-aligned per-sample channel arrays for ONE lap — the single pacer-free view over
        the cached `_lap_columns` fetch, shared by the channels-CSV export and the map rainbow
        (F3). Keys, in CSV column order: t_media_s, elapsed_s, lat_deg, lon_deg, x_m (cs.local),
        y_m, dist_m (the gap-aware odometer), speed_mps, speed_kmh; g_long / g_lat present only
        with a g signal (interpolated onto the lap's sample times; +long = accelerating, +lat =
        turning left). Read-only; nothing cached."""
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
    # Thin assemblers over the cached per-lap values; the math lives in studio/consistency.py.
    # Not cached (read on load / re-segment only, never per-tick).

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
        corner_list = self.corners.corner_list()
        if not corner_list or not ids:
            return []
        times_by_lap = []
        for i in ids:
            st = self.corners.lap_corner_stats(i)
            if len(st) == len(corner_list):  # degenerate laps project to [] — skip
                times_by_lap.append([s.time for s in st])
        return consistency.rank_corners(
            consistency.corner_spreads([c.cid for c in corner_list], times_by_lap))

    # ------------------------------------------------------ auto coaching summary (F10)
    # Composes the corner model, driving channels and consistency stats into the ranked
    # "opportunities". The math lives in studio/coaching.py; this accessor owns the pacer-side
    # extraction. Not cached (read on load / re-segment only).

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
        corner_list = self.corners.corner_list()
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
            st = self.corners.lap_corner_stats(i)
            if len(st) != n:
                continue
            cand_ids.append(i)
            lap_times.append(self.lap_time(i))
            corner_times_by_lap.append([s.time for s in st])

        # The best lap's own per-corner time-in-corner (its self-delta is 0; we need the raw
        # times as the per-corner baseline the median loss is measured against).
        best_stats = self.corners.lap_corner_stats(best)
        if len(best_stats) != n:
            return coaching.Opportunities(enough=False, n_laps=len(cand_ids),
                                          median_lap_id=None, rows=[])
        best_corner_times = [s.time for s in best_stats]

        # The representative ("median") lap — its time is the median of the candidate set.
        med_id = coaching.median_lap_id(cand_ids, lap_times)

        # Cross-lap σ of time-in-corner per cid (from the F6 ranking), as the LINE signal.
        sigmas_by_cid = {sp.cid: sp.sigma for sp in self.corner_consistency()}

        # Apex Δ vs the LOCAL best (median_apex − best_apex), NOT CornerStat.apex_speed_delta which
        # follows the reference baseline — keep the apex signal on the SAME baseline as the loss
        # (D13). [] if no median lap.
        if med_id is not None:
            med_stats = self.corners.lap_corner_stats(med_id)
            median_apex_deltas = (
                [med_stats[i].apex_speed - best_stats[i].apex_speed for i in range(n)]
                if len(med_stats) == n else [])
        else:
            median_apex_deltas = []

        # The driving channels (brake/coast) for the median + best laps — the BRAKING/COASTING
        # signals. [] when there's no g signal (the apex/line signals still drive the reasons).
        med_brakes = self.driving.lap_brake_events(med_id) if med_id is not None else []
        best_brakes = self.driving.lap_brake_events(best)
        med_coast = self.driving.lap_coasting_spans(med_id) if med_id is not None else []
        best_coast = self.driving.lap_coasting_spans(best)

        # Odometer totals so summarize can project each corner window onto each lap's OWN odometer
        # before matching that lap's brake/coast events (which live in its own odometer — D13). The
        # corner edges (c.enter/c.exit) are in the corner basis' reference total; the median/best
        # events are in their own laps' totals. Mirrors lap_corner_grip's projection.
        basis = self.corners.basis()
        corner_dist_total = float(basis[1]) if basis is not None else None
        best_lap_total = self.best_lap_total_distance()
        med_td = self._lap_time_dist(med_id) if med_id is not None else None
        median_lap_total = float(med_td[1][-1]) if med_td is not None else None

        # D2: the typical lap's + best lap's speed-vs-distance traces so summarize can decompose
        # each corner's Δt-vs-best into entry/apex/exit thirds (∫ds/v over each, the typical lap
        # vs best — the SAME comparison the loss/reasons use). Same projection as lap_corner_stats.
        med_dist, med_speed_kmh, _med_elapsed = (
            self._lap_arrays(med_id) if med_id is not None else (None, None, None))
        best_dist, best_speed_kmh, _best_elapsed = self._lap_arrays(best)

        # Drift-gate spatial traces for the phase decomposition (the same alignment lap_corner_stats
        # uses): the corner windows live in the BEST lap's frame, so its (xs, ys, cum) is the fixed
        # reference half of each (ref, comparison) pair. The best lap projects onto its own odometer
        # (zero drift → normalized identity); the typical lap gets the spatial fallback above the
        # bound. A degenerate trace → None → normalized (unchanged). Same xy basis as the map.
        _bt, best_xs, best_ys, _bv, best_cum = self._lap_columns(best)
        best_traces = (best_xs, best_ys, best_cum, best_xs, best_ys, best_cum)
        if med_id is not None:
            _mt, med_xs, med_ys, _mv, med_cum = self._lap_columns(med_id)
            median_traces = (best_xs, best_ys, best_cum, med_xs, med_ys, med_cum)
        else:
            median_traces = None

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
            median_dist=med_dist,
            median_speed_kmh=med_speed_kmh,
            best_dist=best_dist,
            best_speed_kmh=best_speed_kmh,
            median_traces=median_traces,
            best_traces=best_traces,
        )

    def coaching_brake_points(self) -> dict:
        """The best lap's per-corner braking-point comparison, keyed by cid → driving.BrakePoint,
        for the coaching surfaces' ESTIMATED "brake ~N m later" hint (D4). Empty {} when there's no
        best lap or no g signal. One source so the modal dialog AND the persistent panel append the
        SAME hint (the app/panel no longer hand-roll this from best_lap_id() + lap_brake_points())."""
        best = self.best_lap_id()
        if best is None:
            return {}
        return {bp.cid: bp for bp in self.driving.lap_brake_points(best)}


    # Driving channels (brake/coast/grip + thresholds, F5/D3/D4/D5) are the `session.driving`
    # service (studio/driving_channels.py); per-lap caches clear on re-segment, the thresholds
    # survive (they depend only on the constant g series). Consumers call session.driving.thresholds()
    # / .lap_brake_events(lap) / .lap_grip_utilization(lap) etc. directly — no Session forwarders.

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

    # ----------------------- cursor scrub / video sync / map nearest: the Timeline conversions
    # The cursor/plot-x <-> media-time, media-time -> trace-index/lap, and map (x,y) -> trace
    # conversions live in the `session.timeline` sub-object (studio/timeline.py); these stay as thin
    # delegators so the call sites + the tests that monkey-patch s.lap_at_time keep working. Pass
    # active_baseline_total_distance() as best_distance for the shared-distance axis.
    def media_time_at_plot_x(self, lap_id: int, x: float, mode: str,
                             best_distance: float | None = None) -> float | None:
        return self.timeline.media_time_at_plot_x(lap_id, x, mode, best_distance)

    def plot_x_at_media_time(self, lap_id: int, t: float, mode: str,
                             best_distance: float | None = None) -> float | None:
        return self.timeline.plot_x_at_media_time(lap_id, t, mode, best_distance)

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
        SHARE one x-linked axis. Always references the GLOBAL best lap (a single selected lap
        still shows a meaningful delta, not a flat zero).

        Laps are aligned by NORMALIZED distance, not raw odometer (each lap's total differs): on
        a shared s-grid s = cum_distance/total ∈ [0,1], delta_lap(s) = elapsed_lap(s) −
        elapsed_best(s), so s=1 is the laptime difference shown in the table.

        `x_mode` selects the shared x-axis:
          * 'distance' — x = s × best_total_distance (m), identical for every lap.
          * 'time' — x = elapsed_lap(s) (s into the lap), each lap's own x. Δ y-values are
            identical to distance mode.
        F7: a cross-recording reference replaces the local best as the baseline and is returned
        under the sentinel `REFERENCE_ID`. Dormant => byte-identical to before.
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

    # ----------------------------------------------- ideal-lap envelope (D1)
    def ideal_lap_elapsed(self) -> np.ndarray | None:
        """The IDEAL-LAP elapsed curve on the 400-point normalized-distance grid (s∈[0,1]): the
        continuous analogue of `theoretical_best` (the discrete 3-sector ideal).

        For each grid point it is the MINIMUM cumulative elapsed time across the dropout-free
        CLEAN laps (`consistency_lap_ids` — the same set every consistency stat runs on), each
        resampled onto the shared s-grid exactly as `delta()` does. The pointwise min of valid
        cumulative curves can momentarily dip below a later sample, so it is made monotonic
        non-decreasing (`np.maximum.accumulate`) to remain a valid elapsed curve — a small
        correction, since the laps already increase. Starts at 0 (every lap's elapsed[0] is 0).

        This is a SYNTHETIC curve — no single lap drove it — so callers must label it as such.
        Returns None when no clean lap has a usable (≥2 points, positive odometer) trace.
        Independent of any cross-recording reference: the ideal is always the DRIVER's own best
        achievable, so it ignores `_ref_arrays`.
        """
        s_grid = np.linspace(0.0, 1.0, self._DELTA_GRID_N)
        env: np.ndarray | None = None
        for lid in self.consistency_lap_ids():
            dist, _speed_kmh, elapsed = self._lap_arrays(lid)
            if len(dist) < 2 or dist[-1] <= 0:
                continue
            elapsed_on_grid = np.interp(s_grid, dist / dist[-1], elapsed)
            env = elapsed_on_grid if env is None else np.minimum(env, elapsed_on_grid)
        if env is None:
            return None
        # Enforce a valid (non-decreasing) cumulative-elapsed curve after the pointwise min.
        return np.maximum.accumulate(env)

    def ideal_total(self) -> float | None:
        """The IDEAL lap's total elapsed (s) — the continuous theoretical best, ≈ the sum of the
        clean laps' best per-distance segments. ≤ every clean lap's time (it's their lower
        envelope). None when no clean lap has a usable trace."""
        env = self.ideal_lap_elapsed()
        return None if env is None else float(env[-1])

    def ideal_delta_to_best(self, x_mode: str = "distance") -> tuple[np.ndarray, np.ndarray] | None:
        """The SYNTHETIC ideal lap drawn on `delta()`'s own Δ-to-best axis: `(x, dy)` where
        dy(s) = ideal_elapsed(s) − best_elapsed(s) ≤ 0 (the ideal is at least as fast as the best
        lap at every distance, since the best lap is one of the laps forming the envelope), ending
        at ideal_total − best_time ≤ 0.

        This shares the existing best-lap delta's reference frame (best lap = the y=0 line), so the
        ideal reads as a secondary curve dipping below zero — "you could be THIS much faster, and
        here is where" — without introducing a second y-origin. `x_mode` matches `delta()`
        ('distance' → s × active-baseline total; 'time' → the BEST lap's own elapsed-into-lap, so
        the ideal overlays the best-lap curves' x). Returns None when the ideal can't be built or
        there is no best/baseline lap to reference."""
        ideal = self.ideal_lap_elapsed()
        if ideal is None:
            return None
        # Reference frame = delta()'s baseline (cross-recording reference when loaded, else best).
        base = self.baseline_curve()
        if base is None:
            return None
        b_dist, b_elapsed = base.dist, base.elapsed
        if len(b_dist) < 2 or b_dist[-1] <= 0:
            return None
        s_grid = np.linspace(0.0, 1.0, self._DELTA_GRID_N)
        best_elapsed_on_grid = np.interp(s_grid, b_dist / b_dist[-1], b_elapsed)
        if x_mode == "time":
            x = best_elapsed_on_grid  # the best/baseline lap's own elapsed, matching delta()
        else:
            total = self.active_baseline_total_distance()
            if not total:
                return None
            x = s_grid * float(total)
        return x, ideal - best_elapsed_on_grid

    def delta_to_ideal(self, lap_ids, x_mode: str = "distance") -> LapSeries | None:
        """Δ-to-IDEAL for each requested lap over the 400-point grid, mirroring `delta()`'s
        return shape (a {lap_id: (x, dy)} map) but referenced to the synthetic ideal-lap envelope
        instead of the best lap. dy(s) = elapsed_lap(s) − ideal_elapsed(s) ≥ 0 (a lap can't beat
        the envelope it helped form), and dy at s=1 ≈ lap_time − ideal_total.

        `x_mode` matches `delta()`: 'distance' → x = s × active-baseline total (the SAME shared
        axis the best-lap Δ draws on, so the curves overlay); 'time' → x = this lap's own elapsed
        into the lap. Returns None when the ideal can't be built (no clean lap) or no requested
        lap is drawable."""
        ideal = self.ideal_lap_elapsed()
        if ideal is None:
            return None
        ids = [i for i in lap_ids if 0 <= i < self.laps.laps_count()]
        if not ids:
            return None
        s_grid = np.linspace(0.0, 1.0, self._DELTA_GRID_N)
        # Distance mode shares delta()'s x basis (active baseline total) so the two Δ curves line
        # up on one axis; fall back to None-safe 0 only when no baseline exists (no curve drawn).
        base_total = self.active_baseline_total_distance() if x_mode != "time" else None
        out: LapSeries = {}
        for lid in ids:
            dist, _speed_kmh, elapsed = self._lap_arrays(lid)
            if len(dist) < 2 or dist[-1] <= 0:
                continue
            s_lap = dist / dist[-1]
            elapsed_on_grid = np.interp(s_grid, s_lap, elapsed)
            if x_mode == "time":
                x = elapsed_on_grid
            elif base_total:
                x = s_grid * float(base_total)
            else:
                continue  # distance mode with no baseline total → nothing to draw against
            out[lid] = (x, elapsed_on_grid - ideal)
        return out or None

    # ------------------------------------------------------------ video sync
    def index_at_time(self, t: float) -> int | None:
        return self.timeline.index_at_time(t)

    def lap_at_time(self, t: float) -> int | None:
        """The valid lap whose [start, start+lap_time) window contains media-clock time `t`, else
        None — the readout + current-lap highlight. Delegates to session.timeline."""
        return self.timeline.lap_at_time(t)

    def g_at_time(self, t: float) -> tuple[float, float, float] | None:
        """Vehicle-frame g at media-clock time `t`: (lateral_g, longitudinal_g, total_g), or
        None if no g signal is available. Signs: +lateral = turning left, +longitudinal =
        accelerating (−longitudinal = braking). O(log n) lookup into the precomputed series —
        cheap enough for the 30 Hz overlay tick. LATERAL is from the GoPro accelerometer
        (ACCL+GRAV+CORI in the kart frame); LONGITUDINAL is the GPS speed derivative, because the
        IMU forward axis is vibration-inflated (see studio/gmeter.py)."""
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

    def _ideal_envelope(self) -> np.ndarray | None:
        """The ideal-lap elapsed curve (`ideal_lap_elapsed`) MEMOIZED on the `_DELTA_GRID_N` grid,
        so the per-tick `delta_to_ideal_at` re-uses one synthetic envelope instead of rebuilding it
        (a min over every clean lap, resampled) on every 30 Hz frame. The cache is dropped whenever
        the timing lines move (set_timing_lines → the lap arrays change), like the other per-lap
        memos — see `_drop_ideal_cache`."""
        cached = getattr(self, "_ideal_cache", _UNSET)
        if cached is _UNSET:
            cached = self.ideal_lap_elapsed()
            self._ideal_cache = cached
        return cached

    def _drop_ideal_cache(self):
        """Forget the memoized ideal envelope (re-segmentation / reference changes shift the lap
        arrays the envelope is built from). Defensive: no-op when the slot was never set."""
        if hasattr(self, "_ideal_cache"):
            del self._ideal_cache

    def delta_to_ideal_at(self, lap_id: int, t: float) -> float | None:
        """Δ-to-IDEAL (seconds) at media-clock time `t` for the already-resolved `lap_id` — the
        per-tick scalar analogue of `delta_to_ideal`'s 400-grid curve, and the moat number the
        live readout leads with ("you're 0.42 s off your achievable lap, here").

        dy = elapsed_lap(s) − ideal_elapsed(s) ≥ 0: the lap can't beat the synthetic envelope it
        helped form, so this is how much time the IDEAL lap has banked by this track position. At
        the finish (s=1) it ≈ lap_time − ideal_total.

        Cheap by construction: the ideal envelope is memoized (`_ideal_envelope`) and the source
        lap's curve is the same cached `LapCurve` `delta_at_lap` uses, so a tick is two O(log n)
        np.interps. None when the ideal can't be built (no clean lap), the lap is degenerate, or
        `t` isn't inside a valid lap."""
        ideal = self._ideal_envelope()
        if ideal is None:
            return None
        src = self._lap_curve(lap_id)
        if src is None or src.total <= 0:
            return None
        s = src.fraction_at_time(t)              # normalized fraction [0,1]
        elapsed_lap = src.elapsed_at_time(t)     # = t − lap_start, clamped to the lap
        # ideal_elapsed at the SAME track fraction s (the envelope is on the uniform s-grid).
        s_grid = np.linspace(0.0, 1.0, self._DELTA_GRID_N)
        ideal_here = float(np.interp(s, s_grid, ideal))
        return elapsed_lap - ideal_here

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

    # Map (x, y) -> trace nearest-point lookups (whole-trace + the lap-scoped variant for the
    # draggable marker) — delegated to session.timeline (studio/timeline.py).
    def nearest_index(self, x: float, y: float) -> int | None:
        return self.timeline.nearest_index(x, y)

    def nearest_index_in_lap(self, lap_id: int, x: float, y: float) -> int | None:
        """Index (into `lap_id`'s OWN point array) of the trace point nearest (x, y), searching ONLY
        within that lap (so the dragged map marker can't snap across spatial overlaps). Delegates to
        session.timeline."""
        return self.timeline.nearest_index_in_lap(lap_id, x, y)

    def nearest_time_in_lap(self, lap_id: int, x: float, y: float) -> float | None:
        """Media-clock time (s) of the point within `lap_id` nearest (x, y), clamped to the lap
        window — the map marker's drag-to-scrub target. Delegates to session.timeline."""
        return self.timeline.nearest_time_in_lap(lap_id, x, y)
