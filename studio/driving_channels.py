"""DrivingChannels (F5): brake events, coasting spans, per-corner grip + session thresholds
over Session primitives. numpy-only (no pacer core).

Thresholds are cached for the recording (the g series is constant); only the per-lap results
are dropped on re-segment, since they are projected through the segmentation.
"""

from __future__ import annotations

import numpy as np

from . import driving

# "cache not yet computed" sentinel (None is a legal cached value); module-local to avoid
# importing Session.
_UNSET = object()


class DrivingChannels:
    """Brake / coast / grip channels + session-wide thresholds, computed over the owning
    Session's primitives.
    """

    def __init__(self, session):
        self._s = session
        # thresholds: _UNSET until computed (None = legal no-g result); kept across re-segments.
        self._thresholds_cache: object = _UNSET
        # Per-lap channels, all projected through the segmentation -> cleared on re-segment.
        self._brake_events_cache: dict[int, list[driving.BrakeEvent]] = {}
        self._coasting_spans_cache: dict[int, list[driving.CoastSpan]] = {}
        self._corner_grip_cache: dict[int, list[float]] = {}

    def invalidate(self) -> None:
        """Drop the per-lap caches on re-segment (Session.set_timing_lines); thresholds are
        kept (the g series is unchanged)."""
        self._brake_events_cache.clear()
        self._coasting_spans_cache.clear()
        self._corner_grip_cache.clear()

    # ------------------------------------------------------------------ g + thresholds
    def _lap_g_arrays(self, lap_id: int):
        """(long_g, lat_g) for a lap, interpolated from the g meter onto the lap's media times
        (both share the media clock). (None, None) when there's no g signal or a degenerate lap."""
        s = self._s
        gm = s._gmeter
        if not gm.has_data:
            return None, None
        td = s._lap_time_dist_elapsed(lap_id)
        if td is None:
            return None, None
        times, _dists, _elapsed = td
        long_g = np.interp(times, gm.times, gm.long_g)
        lat_g = np.interp(times, gm.times, gm.lat_g)
        return long_g, lat_g

    def thresholds(self):
        """Session-wide brake threshold (None when no g signal); cached. Derived from the CLEAN
        speed-derived longitudinal g, not the vibration-dominated IMU forward axis (see driving)."""
        if self._thresholds_cache is not _UNSET:
            return self._thresholds_cache
        s = self._s
        gm = s._gmeter
        if not gm.has_data:
            self._thresholds_cache = None
            return None
        # Speed resampled to the g clock (trace + g series share the media clock), then the clean
        # longitudinal g = d|v|/dt — the validated brake signal.
        speed_kmh = np.interp(gm.times, s.tt, s.tv)
        long_clean = driving.speed_long_g(speed_kmh, gm.times)
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
        arr = self._s._lap_arrays(lap_id)
        if th is None or arr is None:
            return []
        dists, speed_kmh, elapsed = arr
        if len(dists) < 2:
            return []
        # Brake detection on the CLEAN speed-derived longitudinal (d|v|/dt), not the IMU axis.
        # Corner windows are passed as a block-only guard so the maneuver merge keeps two genuinely-
        # distinct corners separate; None (no corner model) just lets the throttle gate decide.
        long_clean = driving.speed_long_g(speed_kmh, elapsed)
        windows = self._corner_windows(float(dists[-1]))
        events = driving.brake_events(dists, elapsed, long_clean, th.theta_b,
                                      corner_windows=windows)
        self._brake_events_cache[lap_id] = events
        return events

    def _corner_windows(self, total_lap: float):
        """The detected corners projected onto this lap's odometer as (enter, exit) spans, widened
        CORNER_LEAD_M upstream (braking starts before the geometric entry). None when there's no
        corner model — the brake merge then runs purely on the throttle/distance gates."""
        basis = self._s._corner_basis()
        if not basis or not basis[0] or total_lap <= 0:
            return None
        corner_list, total_ref = basis
        if total_ref <= 0:
            return None
        return [(max(0.0, c.enter / total_ref * total_lap - driving.CORNER_LEAD_M),
                 c.exit / total_ref * total_lap) for c in corner_list]

    def lap_coasting_spans(self, lap_id: int) -> list[driving.CoastSpan]:
        """Coasting spans on one lap, in track order. [] when no g signal or a degenerate lap."""
        got = self._coasting_spans_cache.get(lap_id)
        if got is not None:
            return got
        s = self._s
        th = self.thresholds()
        arr = s._lap_arrays(lap_id)
        if th is None or arr is None:
            return []
        dists, speed_kmh, elapsed = arr
        if len(dists) < 2:
            return []
        long_clean = driving.speed_long_g(speed_kmh, elapsed)
        spans = driving.coasting_spans(dists, elapsed, speed_kmh, long_clean, th.theta_b)
        self._coasting_spans_cache[lap_id] = spans
        return spans

    def lap_corner_grip(self, lap_id: int) -> list[float]:
        """Per-corner grip utilization for one lap, one value per detected corner in track order.
        [] when no g signal, no corners, or a degenerate lap."""
        got = self._corner_grip_cache.get(lap_id)
        if got is not None:
            return got
        s = self._s
        long_g, lat_g = self._lap_g_arrays(lap_id)
        basis = s._corner_basis()
        if long_g is None or basis is None or not basis[0]:
            return []
        corner_list, total_ref = basis
        td = s._lap_time_dist_elapsed(lap_id)
        if td is None:
            return []
        _times, dists, _elapsed = td
        total_lap = float(dists[-1])
        if total_lap <= 0:
            return []
        # Project each corner's reference-odometer window onto this lap by normalized distance
        # (same projection lap_corner_stats uses).
        windows = [(c.enter / total_ref * total_lap, c.exit / total_ref * total_lap)
                   for c in corner_list]
        grip = driving.corner_grip(dists, long_g, lat_g, windows)
        self._corner_grip_cache[lap_id] = grip
        return grip

    # ------------------------------------------------------------------ map / plot glue
    def lap_brake_map_markers(self, lap_id: int) -> list[tuple[float, float, float]]:
        """(x, y, peak_decel) per brake onset on one lap, in LOCAL metres on that lap's own
        trace — for the map's brake glyphs. [] when no brake events. The onset odometer is
        mapped to the lap's (x, y) via the lap's cached columns."""
        s = self._s
        events = self.lap_brake_events(lap_id)
        if not events:
            return []
        _t, xs, ys, _v, cum = s._lap_columns(lap_id)
        onsets = np.asarray([e.onset_dist for e in events])
        mx = np.interp(onsets, cum, xs)
        my = np.interp(onsets, cum, ys)
        return [(float(mx[i]), float(my[i]), e.peak_decel) for i, e in enumerate(events)]

    def lap_brake_plot_positions(self, lap_id: int, mode: str) -> list[tuple[float, float]]:
        """(plot-x, peak_decel) per brake onset on one lap, on the speed chart's SHARED axis
        for `mode` ('distance' or 'time'). [] when no brake events / no best lap (distance mode).
          * 'distance': x = (onset_dist / lap_total) * baseline_distance
          * 'time':     x = onset_time (elapsed into the lap)"""
        s = self._s
        events = self.lap_brake_events(lap_id)
        if not events:
            return []
        if mode == "time":
            return [(e.onset_time, e.peak_decel) for e in events]
        # 'distance' — normalize by this lap's total, scale to the active baseline's distance
        # (the reference total when one is loaded) so the glyphs sit on the curves/cursor.
        best = s.best_lap_id()
        td = s._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        best_total = s.active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(e.onset_dist / total_lap * best_total, e.peak_decel) for e in events]

    def lap_coasting_plot_spans(self, lap_id: int, mode: str) -> list[tuple[float, float]]:
        """(plot-x0, plot-x1) per coasting span on one lap, on the speed chart's SHARED axis
        for `mode`. Same projection as lap_brake_plot_positions. [] when no spans / no best
        lap (distance mode)."""
        s = self._s
        spans = self.lap_coasting_spans(lap_id)
        if not spans:
            return []
        if mode == "time":
            # Elapsed at each span edge: interp the span's odometer edges into the lap's elapsed.
            td = s._lap_time_dist_elapsed(lap_id)
            if td is None:
                return []
            _times, dists, elapsed = td
            return [(float(np.interp(sp.start_dist, dists, elapsed)),
                     float(np.interp(sp.end_dist, dists, elapsed))) for sp in spans]
        best = s.best_lap_id()
        td = s._lap_time_dist(lap_id)
        if best is None or td is None:
            return []
        _times, dists = td
        total_lap = float(dists[-1])
        # active baseline total (reference when loaded) — the shared axis delta() uses.
        best_total = s.active_baseline_total_distance()
        if total_lap <= 0 or not best_total:
            return []
        return [(sp.start_dist / total_lap * best_total, sp.end_dist / total_lap * best_total)
                for sp in spans]
