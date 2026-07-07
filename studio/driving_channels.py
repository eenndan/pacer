"""DrivingChannels (F5): brake events, coasting spans, per-corner grip + session thresholds
over Session primitives. numpy-only (no pacer core).

Thresholds are cached for the recording (the g series is constant); only the per-lap results
are dropped on re-segment, since they are projected through the segmentation.

DEPENDENCY INJECTION (like studio/render_cache.py): the constructor takes Session-bound
callables over Session's own primitives, so NO method here reaches a `_`-private attribute of
Session — Session owns the pacer side + the g-meter and wires its privates into the callables.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from . import corners, driving
from ._signal import G, speed_long_g

# "cache not yet computed" sentinel (None is a legal cached value); module-local to avoid
# importing Session.
_UNSET = object()


class DrivingChannels:
    """Brake / coast / grip channels + session-wide thresholds, computed over Session-bound
    primitives.

    All inputs are Session-bound callables (Session owns the pacer side + the g-meter):
    `gmeter` returns the live GMeter (built after construction, so it must be a callable);
    `trace_times` / `trace_speed_kmh` return the full-trace media clock + km/h speed (Session.tt /
    .tv); `lap_arrays` / `lap_time_dist` / `lap_time_dist_elapsed` / `lap_columns` are the per-lap
    fetches; `best_lap_id` / `valid_lap_ids` the memoized lap sets; `active_baseline_total_distance`
    the shared distance-axis basis; `corner_basis` / `lap_corner_stats` the composed CornerModel's
    detection basis + per-lap stats.
    """

    def __init__(self, *,
                 gmeter: Callable[[], object],
                 trace_times: Callable[[], np.ndarray],
                 trace_speed_kmh: Callable[[], np.ndarray],
                 lap_arrays: Callable[[int], tuple | None],
                 lap_time_dist: Callable[[int], tuple | None],
                 lap_time_dist_elapsed: Callable[[int], tuple | None],
                 lap_columns: Callable[[int], tuple],
                 best_lap_id: Callable[[], int | None],
                 valid_lap_ids: Callable[[], list[int]],
                 active_baseline_total_distance: Callable[[], float | None],
                 corner_basis: Callable[[], tuple | None],
                 lap_corner_stats: Callable[[int], list],
                 lap_elevation: Callable[[int], np.ndarray] | None = None):
        self._gmeter = gmeter
        # Per-sample altitude for the lap (for the OPT-IN hill-compensated braking, driving.py);
        # optional so a bare/old construction still works — a None just keeps braking flat-ground.
        self._lap_elevation = lap_elevation
        self._trace_times = trace_times
        self._trace_speed_kmh = trace_speed_kmh
        self._lap_arrays = lap_arrays
        self._lap_time_dist = lap_time_dist
        self._lap_time_dist_elapsed = lap_time_dist_elapsed
        self._lap_columns = lap_columns
        self._best_lap_id = best_lap_id
        self._valid_lap_ids = valid_lap_ids
        self._active_baseline_total_distance = active_baseline_total_distance
        self._corner_basis = corner_basis
        self._lap_corner_stats = lap_corner_stats
        # thresholds + grip envelope: _UNSET until computed (None = legal no-g); both derive from the
        # g series, which is constant -> kept across re-segments.
        self._thresholds_cache: object = _UNSET
        self._grip_env_cache: object = _UNSET
        # D4: the session's demonstrated peak braking decel (g), derived from EVERY valid lap's brake
        # events -> depends on the segmentation, so it is dropped on re-segment with the per-lap caches.
        self._a_max_cache: object = _UNSET
        # Per-lap channels, all projected through the segmentation -> cleared on re-segment.
        self._brake_events_cache: dict[int, list[driving.BrakeEvent]] = {}
        self._coasting_spans_cache: dict[int, list[driving.CoastSpan]] = {}
        self._corner_grip_cache: dict[int, list[float]] = {}
        # D5: per-lap per-sample grip utilization, aligned to the lap's map xy points.
        self._grip_util_cache: dict[int, object] = {}
        # D3: per-lap (dist, elapsed, intensity) for the synthetic brake/throttle band.
        self._brake_throttle_cache: dict[int, tuple] = {}
        # D4: per-lap per-corner braking-point comparisons.
        self._brake_points_cache: dict[int, list[driving.BrakePoint]] = {}

    def invalidate(self) -> None:
        """Drop the per-lap caches on re-segment (Session.set_timing_lines); thresholds are
        kept (the g series is unchanged)."""
        self._brake_events_cache.clear()
        self._coasting_spans_cache.clear()
        self._corner_grip_cache.clear()
        self._grip_util_cache.clear()
        self._brake_throttle_cache.clear()
        self._brake_points_cache.clear()
        self._a_max_cache = _UNSET  # depends on the per-lap brake events, which re-project

    # ----------------------------------------------------------- drift-gate spatial traces
    def _best_trace(self) -> tuple | None:
        """The best (reference) lap's local-frame trace (xs, ys, cum) — the spatial anchor side of
        the per-corner drift gate (corners.project_boundaries), mirroring CornerModel._best_trace.
        The reference-odometer corner windows are expressed in this lap's frame, so it is the fixed
        half of every (ref, comparison) trace pair. None when there is no usable best lap."""
        best = self._best_lap_id()
        if best is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(best)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return xs, ys, cum

    def _corner_traces(self, lap_id: int) -> tuple | None:
        """The (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) trace pair the drift gate's
        spatial fallback needs to map this session's corner windows onto `lap_id` (mirrors
        CornerModel._lap_traces). None (→ the gate keeps the normalized projection, byte-identical
        to the pre-gate output) when either trace is degenerate."""
        ref_trace = self._best_trace()
        if ref_trace is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(lap_id)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return (*ref_trace, xs, ys, cum)

    # ------------------------------------------------------------------ g + thresholds
    def _lap_g_arrays(self, lap_id: int):
        """(long_g, lat_g) for a lap, interpolated from the g meter onto the lap's media times
        (both share the media clock). (None, None) when there's no g signal or a degenerate lap.

        LONGITUDINAL prefers the GPS speed-derivative (gm.long_g_gps) when present — the IMU forward
        axis is vibration-inflated (see gmeter/driving), so the dial, the map grip colour, the
        per-corner grip AND the friction-circle envelope (_grip_envelope) all read this same
        validated longitudinal. Falls back to the IMU long_g for a GPS-only/synthetic meter. LATERAL
        is always the IMU lateral (which it gets right, r~0.9)."""
        gm = self._gmeter()
        if not gm.has_data:
            return None, None
        td = self._lap_time_dist_elapsed(lap_id)
        if td is None:
            return None, None
        times, _dists, _elapsed = td
        long_src = gm.long_g_gps if gm.long_g_gps is not None else gm.long_g
        long_g = np.interp(times, gm.times, long_src)
        lat_g = np.interp(times, gm.times, gm.lat_g)
        return long_g, lat_g

    def thresholds(self):
        """Session-wide brake threshold (None when no g signal); cached. Derived from the CLEAN
        speed-derived longitudinal g, not the vibration-dominated IMU forward axis (see driving)."""
        if self._thresholds_cache is not _UNSET:
            return self._thresholds_cache
        gm = self._gmeter()
        if not gm.has_data:
            self._thresholds_cache = None
            return None
        # Speed resampled to the g clock (trace + g series share the media clock), then the clean
        # longitudinal g = d|v|/dt — the validated brake signal.
        speed_kmh = np.interp(gm.times, self._trace_times(), self._trace_speed_kmh())
        long_clean = speed_long_g(speed_kmh, gm.times)
        self._thresholds_cache = driving.derive_thresholds(long_clean, speed_kmh)
        return self._thresholds_cache

    # ------------------------------------------------------------------ per-lap channels
    def lap_brake_events(self, lap_id: int) -> list[driving.BrakeEvent]:
        """Brake events on one lap (onset odometer/time, peak decel, duration), in track order.
        [] when no g signal or a degenerate lap."""
        got = self._brake_events_cache.get(lap_id)
        if got is not None:
            return got
        th = self.thresholds()
        arr = self._lap_arrays(lap_id)
        if th is None or arr is None:
            return []
        dists, speed_kmh, elapsed = arr
        if len(dists) < 2:
            return []
        # Brake detection on the CLEAN speed-derived longitudinal (d|v|/dt), not the IMU axis.
        # Corner windows are passed as a block-only guard so the maneuver merge keeps two genuinely-
        # distinct corners separate; None (no corner model) just lets the throttle gate decide.
        long_clean = speed_long_g(speed_kmh, elapsed)
        windows = self._corner_windows(lap_id, float(dists[-1]))
        events = driving.brake_events(dists, elapsed, long_clean, th.theta_b,
                                      corner_windows=windows)
        self._brake_events_cache[lap_id] = events
        return events

    def _corner_windows(self, lap_id: int, total_lap: float):
        """The detected corners projected onto this lap's odometer as (enter, exit) spans, widened
        CORNER_LEAD_M upstream (braking starts before the geometric entry). None when there's no
        corner model — the brake merge then runs purely on the throttle/distance gates.

        The enter/exit boundaries are mapped by the drift-gated alignment (corners.project_boundaries
        — normalized distance within NORMALIZED_DRIFT_MAX, the robust spatial nearest-point match
        above it; the same gate lap_corner_stats uses), byte-identical to the old normalized
        projection in the common well-matched case."""
        basis = self._corner_basis()
        if not basis or not basis[0] or total_lap <= 0:
            return None
        corner_list, total_ref = basis
        if total_ref <= 0:
            return None
        interior = [b for c in corner_list for b in (c.enter, c.exit)]
        proj = corners.project_boundaries(interior, total_ref, total_lap,
                                          traces=self._corner_traces(lap_id))
        return [(max(0.0, float(proj[2 * i]) - driving.CORNER_LEAD_M), float(proj[2 * i + 1]))
                for i in range(len(corner_list))]

    def lap_coasting_spans(self, lap_id: int) -> list[driving.CoastSpan]:
        """Coasting spans on one lap, in track order. [] when no g signal or a degenerate lap."""
        got = self._coasting_spans_cache.get(lap_id)
        if got is not None:
            return got
        th = self.thresholds()
        arr = self._lap_arrays(lap_id)
        if th is None or arr is None:
            return []
        dists, speed_kmh, elapsed = arr
        if len(dists) < 2:
            return []
        long_clean = speed_long_g(speed_kmh, elapsed)
        spans = driving.coasting_spans(dists, elapsed, speed_kmh, long_clean, th.theta_b)
        self._coasting_spans_cache[lap_id] = spans
        return spans

    def lap_brake_throttle(self, lap_id: int):
        """D3: (dist, elapsed, intensity) for the synthetic brake/throttle band on one lap.
        `intensity` is per-sample ESTIMATED pedal intensity in [-1, 1] (negative braking, positive
        throttle), derived from the SAME clean speed-derived longitudinal g + session brake
        threshold the brake detector uses (see driving.brake_throttle_intensity). (None, None,
        None) when there's no g signal or a degenerate lap. Cached per lap."""
        got = self._brake_throttle_cache.get(lap_id)
        if got is not None:
            return got
        th = self.thresholds()
        arr = self._lap_arrays(lap_id)
        if th is None or arr is None:
            return None, None, None
        dists, speed_kmh, elapsed = arr
        if len(dists) < 2:
            return None, None, None
        long_clean = speed_long_g(speed_kmh, elapsed)
        intensity = driving.brake_throttle_intensity(elapsed, long_clean, th.theta_b)
        result = (np.asarray(dists, float), np.asarray(elapsed, float), intensity)
        self._brake_throttle_cache[lap_id] = result
        return result

    def lap_corner_grip(self, lap_id: int) -> list[float]:
        """Per-corner grip utilization for one lap, one value per detected corner in track order.
        [] when no g signal, no corners, or a degenerate lap."""
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
        # Project each corner's reference-odometer window onto this lap by the drift-gated alignment
        # (corners.project_boundaries — the SAME gate lap_corner_stats uses: normalized within
        # NORMALIZED_DRIFT_MAX, the robust spatial nearest-point match above it), byte-identical to
        # the old normalized projection in the common well-matched case.
        interior = [b for c in corner_list for b in (c.enter, c.exit)]
        proj = corners.project_boundaries(interior, total_ref, total_lap,
                                          traces=self._corner_traces(lap_id))
        windows = [(float(proj[2 * i]), float(proj[2 * i + 1])) for i in range(len(corner_list))]
        grip = driving.corner_grip(dists, long_g, lat_g, windows, self._grip_envelope())
        self._corner_grip_cache[lap_id] = grip
        return grip

    def lap_grip_utilization(self, lap_id: int):
        """D5: per-sample grip utilization for one lap (hypot(lat,long) / session envelope, clipped),
        aligned 1:1 to the lap's MAP xy points (the lap_channels / _lap_columns sample grid) so the
        track map can colour the racing line by it. None when there's no g signal or a degenerate
        lap. ESTIMATED, lateral-dominant (see driving.grip_utilization). Cached per lap (dropped on
        re-segment with the other per-lap channels)."""
        got = self._grip_util_cache.get(lap_id)
        if got is not None:
            return got
        long_g, lat_g = self._lap_g_arrays(lap_id)
        if long_g is None or len(long_g) < 2:
            return None
        util = driving.grip_utilization(lat_g, long_g, self._grip_envelope())
        self._grip_util_cache[lap_id] = util
        return util

    def _grip_envelope(self) -> float:
        """The session-wide combined-g grip limit (cached; constant across re-segments). corner_grip
        normalizes to this so a slow lap reads lower, vs normalizing to each lap's own peak.

        AXIS: the friction circle is built from the SAME trusted axes the per-corner / per-sample
        grip numerator uses — the CLEAN speed-derived longitudinal g (long_g_gps when present; see
        _lap_g_arrays) and the IMU lateral. Using the raw IMU long_g here (vibration-inflated, ~2x
        RMS) would inflate the divisor and bias every grip reading systematically LOW."""
        if self._grip_env_cache is not _UNSET:
            return self._grip_env_cache
        gm = self._gmeter()
        speed_kmh = np.interp(gm.times, self._trace_times(), self._trace_speed_kmh())
        # Clean longitudinal on the g clock: prefer the GPS speed-derivative the dial/corner grip
        # use; for a GPS-only/synthetic meter (no long_g_gps) derive it from the resampled speed,
        # falling back to the IMU long_g only when there's no speed trace to differentiate.
        if gm.long_g_gps is not None:
            long_clean = gm.long_g_gps
        elif gm.times.size >= 2:
            long_clean = speed_long_g(speed_kmh, gm.times)
        else:
            long_clean = gm.long_g
        self._grip_env_cache = driving.grip_envelope(long_clean, gm.lat_g, speed_kmh)
        return self._grip_env_cache

    # ------------------------------------------------------------------ D4 braking-point optimizer
    def _a_max(self) -> float:
        """The session's DEMONSTRATED peak braking deceleration (g) for the brake-point optimizer:
        a robust high percentile of every valid lap's per-event peak decels, floored (see
        driving.estimate_a_max). NOT the detection threshold theta_b. Cached; dropped on re-segment.
        0.0 only when there's no g signal at all (the accessor then reports N/A everywhere)."""
        if self._a_max_cache is not _UNSET:
            return self._a_max_cache
        if self.thresholds() is None:  # no g signal -> no brake-point math
            self._a_max_cache = 0.0
            return 0.0
        peaks: list[float] = []
        for lap_id in self._valid_lap_ids():
            peaks.extend(e.peak_decel for e in self.lap_brake_events(lap_id))
        self._a_max_cache = driving.estimate_a_max(peaks)
        return self._a_max_cache

    def lap_brake_points(self, lap_id: int) -> list[driving.BrakePoint]:
        """D4: per-corner braking-point comparison for one lap (one BrakePoint per detected corner,
        track order) — where the driver actually braked vs the apex-speed-matched LATEST sustainable
        brake point (see driving.BrakePoint). ESTIMATED.

        Each corner is matched to the brake event whose onset falls in [enter - lead, exit] on THIS
        lap's odometer (the brake zone starts on the straight before turn-in); the LAST such onset is
        taken (the brake into the corner, not an earlier corner's release). entry speed = the speed at
        that onset; v_apex/apex_dist come from this lap's lap_corner_stats. A corner with no matched
        brake event, or where v_apex >= v_entry (no braking needed) / no a_max, is OMITTED (N/A).

        [] when there's no g signal, no corners, or the lap is degenerate."""
        got = self._brake_points_cache.get(lap_id)
        if got is not None:
            return got
        a_max_g = self._a_max()
        stats = self._lap_corner_stats(lap_id)
        arr = self._lap_arrays(lap_id)
        if a_max_g <= 0.0 or not stats or arr is None:
            return []
        dists, speed_kmh, _elapsed = arr
        if len(dists) < 2:
            return []
        basis = self._corner_basis()
        if basis is None or not basis[0]:
            return []
        corner_list, total_ref = basis
        if total_ref <= 0:
            return []
        total_lap = float(dists[-1])
        a_max_ms2 = a_max_g * G
        # Opt-in hill-compensated braking (driving.HILL_COMPENSATE_BRAKING, OFF by default): the
        # per-sample altitude for this lap, else None (flat-ground physics — the byte-identical default).
        elevation = (self._lap_elevation(lap_id)
                     if driving.HILL_COMPENSATE_BRAKING and self._lap_elevation is not None
                     else None)
        events = self.lap_brake_events(lap_id)
        # Project every corner's [enter, exit] onto THIS lap's odometer via the drift-gated alignment
        # (corners.project_boundaries — the SAME gate lap_corner_stats / grip use: normalized within
        # NORMALIZED_DRIFT_MAX, the robust spatial nearest-point match above it), byte-identical to
        # the old normalized projection in the common well-matched case.
        interior = [b for c in corner_list for b in (c.enter, c.exit)]
        proj = corners.project_boundaries(interior, total_ref, total_lap,
                                          traces=self._corner_traces(lap_id))
        out: list[driving.BrakePoint] = []
        for i, c in enumerate(corner_list):
            if i >= len(stats):
                break
            st = stats[i]
            # Take the LAST brake onset inside the corner's projected [enter - lead, exit] window as
            # the brake into this corner.
            lo = float(proj[2 * i]) - driving.BRAKE_MATCH_LEAD_M
            hi = float(proj[2 * i + 1])
            matched = [e for e in events if lo <= e.onset_dist <= hi]
            if not matched:
                continue  # no detected brake into this corner -> N/A
            onset = float(matched[-1].onset_dist)
            v_entry = float(np.interp(onset, dists, speed_kmh)) / 3.6  # km/h -> m/s
            v_apex = float(st.apex_speed) / 3.6
            gradient_rad = 0.0
            if elevation is not None and len(elevation) >= len(dists):
                # slope of the braking zone [onset → apex]: Δaltitude / Δodometer → incline angle.
                a_alt, apex_alt = np.interp(
                    [onset, float(st.apex_dist)], dists, elevation[:len(dists)])
                dx = float(st.apex_dist) - onset
                if dx > 0:
                    gradient_rad = float(np.arctan2(apex_alt - a_alt, dx))
            d = driving.optimal_brake_distance(v_entry, v_apex, a_max_ms2, gradient_rad)
            if d is None:  # v_apex >= v_entry (no braking needed) -> N/A for this corner
                continue
            optimal = float(st.apex_dist) - d
            out.append(driving.BrakePoint(
                cid=c.cid, actual_brake_dist=onset, optimal_brake_dist=optimal,
                metres_later=optimal - onset, a_max_g=a_max_g))
        self._brake_points_cache[lap_id] = out
        return out

    # ------------------------------------------------------------------ map / plot glue
    def lap_brake_map_markers(self, lap_id: int) -> list[tuple[float, float, float]]:
        """(x, y, peak_decel) per brake onset on one lap, in LOCAL metres on that lap's own
        trace — for the map's brake glyphs. [] when no brake events. The onset odometer is
        mapped to the lap's (x, y) via the lap's cached columns."""
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
        for `mode` ('distance' or 'time'). [] when no brake events / no best lap (distance mode).
          * 'distance': x = (onset_dist / lap_total) * baseline_distance
          * 'time':     x = onset_time (elapsed into the lap)"""
        events = self.lap_brake_events(lap_id)
        if not events:
            return []
        if mode == "time":
            return [(e.onset_time, e.peak_decel) for e in events]
        # 'distance' — normalize by this lap's total, scale to the active baseline's distance
        # (the reference total when one is loaded) so the glyphs sit on the curves/cursor.
        best = self._best_lap_id()
        td = self._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        best_total = self._active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(e.onset_dist / total_lap * best_total, e.peak_decel) for e in events]

    def lap_brake_throttle_plot(self, lap_id: int, mode: str):
        """D3: (plot_x, intensity) for the synthetic brake/throttle band on one lap, on the speed
        chart's SHARED axis for `mode`. Same x projection as the brake glyphs / coast bands so the
        band lines up under the speed curve. (None, None) when no g signal / no best lap (distance
        mode).
          * 'distance': x = (dist / lap_total) * active_baseline_total
          * 'time':     x = elapsed (into the lap)"""
        dists, elapsed, intensity = self.lap_brake_throttle(lap_id)
        if intensity is None:
            return None, None
        if mode == "time":
            return elapsed, intensity
        total_lap = float(dists[-1])
        best_total = self._active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return None, None
        return dists / total_lap * best_total, intensity

    def lap_coasting_plot_spans(self, lap_id: int, mode: str) -> list[tuple[float, float]]:
        """(plot-x0, plot-x1) per coasting span on one lap, on the speed chart's SHARED axis
        for `mode`. Same projection as lap_brake_plot_positions. [] when no spans / no best
        lap (distance mode)."""
        spans = self.lap_coasting_spans(lap_id)
        if not spans:
            return []
        if mode == "time":
            # Elapsed at each span edge: interp the span's odometer edges into the lap's elapsed.
            td = self._lap_time_dist_elapsed(lap_id)
            if td is None:
                return []
            _times, dists, elapsed = td
            return [(float(np.interp(sp.start_dist, dists, elapsed)),
                     float(np.interp(sp.end_dist, dists, elapsed))) for sp in spans]
        best = self._best_lap_id()
        td = self._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        # active baseline total (reference when loaded) — the shared axis delta() uses.
        best_total = self._active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(sp.start_dist / total_lap * best_total, sp.end_dist / total_lap * best_total)
                for sp in spans]
