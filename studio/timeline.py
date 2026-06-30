"""Timeline: the per-Session cursor / plot / video-sync coordinate conversions, grouped off the
Session facade (E2). Pure numpy over Session-bound CALLABLES (the studio/render_cache.py +
corner_model/driving_channels dependency-injection pattern), so it owns no pacer and reaches no
Session `_`-private directly — Session wires its primitives (per-lap time/dist + trace arrays +
the valid-lap set + lap windows) into the callables here. Session keeps thin delegators
(session.lap_at_time -> self.timeline.lap_at_time, …) so the existing call sites and the tests
that monkey-patch `s.lap_at_time` keep working unchanged.

Coordinate spaces:
  * plot-x <-> media time (lap-scoped, mode-aware: 'time' = t-into-lap seconds; 'distance'/'delta'
    = the shared normalized-distance × baseline_total axis delta() draws on, so the cursors
    coincide). best_distance is caller-supplied (the active baseline total stays on Session).
  * media time -> trace index / lap (full-trace searchsorted + the O(log n) lap-window search).
  * map (x, y) -> trace (whole-trace argmin + the lap-scoped variant for the draggable marker).
"""
from __future__ import annotations

import numpy as np


class Timeline:
    def __init__(self, *, lap_time_dist, lap_trace_xyt, valid_lap_ids, lap_window,
                 trace_times, trace_xs, trace_ys):
        self._lap_time_dist = lap_time_dist      # (lap_id) -> (times, dists) | None
        self._lap_trace_xyt = lap_trace_xyt      # (lap_id) -> (xs, ys, times) local metres
        self._valid_lap_ids = valid_lap_ids      # () -> list[int] (memoized on Session)
        self._lap_window = lap_window            # (lap_id) -> (start_ts, start_ts + lap_time)
        self._trace_times = trace_times          # () -> tt (full-trace media-clock times)
        self._trace_xs = trace_xs                # () -> tx (full-trace local-metre xs)
        self._trace_ys = trace_ys                # () -> ty
        # [start, end) windows on the GLOBAL clock for the O(log n) lap_at_time binary search;
        # cleared on re-segment via invalidate() (driven by Session.set_timing_lines).
        self._lap_windows: tuple[np.ndarray, np.ndarray, list[int]] | None = None

    def invalidate(self) -> None:
        """Drop the cached lap-window table (a re-segment shifted the lap ids/times)."""
        self._lap_windows = None

    # --------------------------------------------- cursor scrub: plot-x <-> media time
    # Speed + delta share one x-linked axis:
    #   * TIME mode:     x = t − lap_start
    #   * DISTANCE mode: x = s × baseline_total, s = dist_in_lap(t)/lap_total — the same axis
    #     delta() draws on, so the cursor sits on its curve. The caller passes
    #     active_baseline_total_distance() as best_distance so both halves use the SAME total.
    # 'distance' and 'delta' are the same shared-distance mode; all clamp to the lap window.
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

    # ------------------------------------------------------ media time -> trace index / lap
    def index_at_time(self, t: float) -> int | None:
        tt = self._trace_times()
        n = len(tt)
        if n == 0:
            return None
        i = int(np.searchsorted(tt, t))
        return min(max(i, 0), n - 1)

    def _lap_window_table(self):
        """Cached parallel arrays (starts, ends, lap_ids) over the VALID laps, sorted by start
        time, for the O(log n) `lap_at_time` binary search. Cleared on re-segment. The windows
        are [start_timestamp, start+lap_time), single-sourced via lap_window."""
        if self._lap_windows is None:
            valid = self._valid_lap_ids()
            rows = [(*self._lap_window(i), i) for i in valid]
            rows.sort(key=lambda r: r[0])  # by start time (valid is id-ascending => time-ascending)
            starts = np.array([r[0] for r in rows], dtype=float)
            ends = np.array([r[1] for r in rows], dtype=float)
            ids = [r[2] for r in rows]
            self._lap_windows = (starts, ends, ids)
        return self._lap_windows

    def lap_at_time(self, t: float) -> int | None:
        """The valid lap whose [start_timestamp, start+lap_time) window contains `t` (media-clock
        seconds), else None — for the readout + current-lap highlight.

        The upper bound is HALF-OPEN (`t < end`) on purpose: consecutive laps are contiguous, so
        an inclusive bound would resolve a `t` exactly on a lap's START — the time select→seek
        produces — to the PREVIOUS lap, jumping the highlight back one lap. The sole side-effect
        (the exact finish instant of the LAST lap resolving to None) is a harmless between-laps
        moment auto-follow holds through.

        O(log n) binary search on the cached, start-sorted window table."""
        starts, ends, ids = self._lap_window_table()
        if len(starts) == 0:
            return None
        k = int(np.searchsorted(starts, t, side="right")) - 1
        if k < 0:
            return None
        if starts[k] <= t < ends[k]:
            return ids[k]
        return None

    # ------------------------------------------------------------- map (x, y) -> trace
    def nearest_index(self, x: float, y: float) -> int | None:
        tx = self._trace_xs()
        if len(tx) == 0:
            return None
        return int(np.argmin((tx - x) ** 2 + (self._trace_ys() - y) ** 2))

    # The red map marker is draggable; dragging seeks the video. Searching the WHOLE trace for the
    # nearest point makes the marker JUMP to another lap wherever the laps overlap spatially. So
    # constrain the search to the CURRENT lap's own trace — the same lap-scoped behaviour as the
    # scrub cursor. Pure numpy on the lap's local-metre points; no pacer.
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
