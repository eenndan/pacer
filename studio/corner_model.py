"""CornerModel — the per-segmentation corner analysis extracted from Session: the detected
corner list + reference total, the per-lap projected corner stats (incl. the cross-recording
reference's under reference_id), and the per-corner session-best times. All derive from the
current segmentation, so Session composes this service + delegates.

PACER-FREE (numpy on Session's cached per-lap primitives). `invalidate()` (from
set_timing_lines) drops all three caches on re-segment; `invalidate_stats()` drops only the
per-lap stats when a cross-recording reference changes (the detection windows are unchanged).

DEPENDENCY INJECTION (like studio/render_cache.py): the constructor takes Session-bound
callables over Session's own primitives, so NO method here reaches a `_`-private attribute of
Session — Session owns the pacer side + wires its privates into the callables.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from . import corners

# "not yet computed" sentinel (None is a legal cached value); module-local to avoid importing
# Session.
_UNSET = object()


class CornerModel:
    """Corner detection + per-corner per-lap stats over Session-bound primitives.

    All inputs are Session-bound callables (Session owns the pacer side + its memoized lap sets /
    per-lap caches / active reference): `best_lap_id` / `valid_lap_ids` / `lap_has_dropout` are the
    memoized lap-set accessors; `lap_columns` / `lap_arrays` / `lap_time_dist` are the per-lap
    array fetches; `reference` returns the active cross-recording ReferenceLap (or None). The corner
    math is just numpy on those arrays. `reference_id` is Session.REFERENCE_ID, the sentinel the
    reference stats are parked under.
    """

    def __init__(self, *, reference_id: int,
                 best_lap_id: Callable[[], int | None],
                 valid_lap_ids: Callable[[], list[int]],
                 lap_has_dropout: Callable[[int], bool],
                 lap_columns: Callable[[int], tuple],
                 lap_arrays: Callable[[int], tuple],
                 lap_time_dist: Callable[[int], tuple | None],
                 reference: Callable[[], object | None]):
        self._reference_id = reference_id
        self._best_lap_id = best_lap_id
        self._valid_lap_ids = valid_lap_ids
        self._lap_has_dropout = lap_has_dropout
        self._lap_columns = lap_columns
        self._lap_arrays = lap_arrays
        self._lap_time_dist = lap_time_dist
        self._reference = reference
        self._basis_cache: object = _UNSET  # (corners, total_ref) or None
        self._stats_cache: dict[int, list[corners.CornerStat]] = {}  # per-lap stats + the reference's own under reference_id
        self._bests_cache: object = _UNSET  # per-corner session-best time

    def invalidate(self) -> None:
        """Drop EVERY corner cache — called from Session.set_timing_lines (the single
        re-segmentation point): the corner set is detected on + projected through the
        segmentation, so all three are stale after a timing-line change."""
        self._basis_cache = _UNSET
        self._stats_cache.clear()
        self._bests_cache = _UNSET

    def invalidate_stats(self) -> None:
        """Drop ONLY the per-lap stats (not the corner detection) — called from
        Session.set_reference_session / clear_reference: the per-corner Δ baseline switched
        (best lap <-> reference lap), so every cached per-lap stat delta is stale, but the
        corner windows themselves are unchanged. Recomputed lazily against the new baseline."""
        self._stats_cache.clear()

    # ----------------------------------------------------------- drift-gate spatial traces
    def _best_trace(self) -> tuple | None:
        """The best (reference) lap's local-frame trace (xs, ys, cum) — the spatial anchor side of
        the per-corner drift gate (corners.project_boundaries). None when there is no usable best
        lap. The reference-odometer corner windows are expressed in this lap's frame, so it is the
        fixed half of every (ref, comparison) trace pair."""
        best = self._best_lap_id()
        if best is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(best)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return xs, ys, cum

    def _lap_traces(self, lap_id: int, ref_trace: tuple | None) -> tuple | None:
        """The (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) trace pair the drift gate's
        spatial fallback needs to map this session's corner windows onto `lap_id`. None (→ the
        gate keeps the normalized projection) when either trace is degenerate. A reference-lap
        (cross-recording) projection has no local trace pair, so it stays normalized."""
        if ref_trace is None:
            return None
        _t, xs, ys, _v, cum = self._lap_columns(lap_id)
        if len(cum) < 2 or float(cum[-1]) <= 0:
            return None
        return (*ref_trace, xs, ys, cum)

    # ------------------------------------------------------------------ basis + corners
    def basis(self) -> tuple[list[corners.Corner], float] | None:
        """The cached (corner list, reference total distance) pair, or None when there is no
        usable best lap. The reference total is the best lap's odometer length — the basis
        the corner windows (and the delta plot's distance axis) are expressed in."""
        if self._basis_cache is not _UNSET:
            return self._basis_cache
        self._basis_cache = None
        best = self._best_lap_id()
        if best is not None:
            _t, _xs, _ys, _v, cum_best = self._lap_columns(best)
            if len(cum_best) >= 8 and float(cum_best[-1]) > 0:
                total_ref = float(cum_best[-1])
                # The median curvature profile pools the session's clean laps (valid, no GPS
                # dropout); the best lap is always included so a session where every lap is
                # dropout-flagged still detects on the best lap alone.
                ids = [i for i in self._valid_lap_ids() if not self._lap_has_dropout(i)]
                if best not in ids:
                    ids.append(best)
                traces = []
                for lid in ids:
                    _lt, xs, ys, _lv, cum = self._lap_columns(lid)
                    traces.append((xs, ys, cum))
                d_grid, kappa = corners.pooled_curvature(traces, total_ref)
                self._basis_cache = (corners.detect_corners(d_grid, kappa), total_ref)
        return self._basis_cache

    def corner_list(self) -> list[corners.Corner]:
        """The detected corners (C1… in track order) in best-lap odometer metres. [] when
        no best lap exists. Computed once per segmentation (see basis)."""
        basis = self.basis()
        return basis[0] if basis is not None else []

    # ------------------------------------------------------------------ per-lap stats
    def reference_corner_stats(self) -> list[corners.CornerStat] | None:
        """The cross-recording reference lap's per-corner stats projected onto THIS session's
        corner windows (the same normalized-distance projection any local lap uses), or None
        when no reference is loaded. Cached under the reference sentinel key; invalidated when
        the reference or the segmentation changes (invalidate_stats / invalidate)."""
        ref = self._reference()
        if ref is None:
            return None
        got = self._stats_cache.get(self._reference_id)
        if got is not None:
            return got
        basis = self.basis()
        if basis is None or not basis[0]:
            return None
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = ref.arrays()
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return None
        # ref=None: the reference IS the baseline (self-deltas 0).
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=None)
        self._stats_cache[self._reference_id] = stats
        return stats

    def lap_corner_stats(self, lap_id: int) -> list[corners.CornerStat]:
        """Per-corner metrics for one lap (time-in-corner, apex/entry/exit speeds, deltas vs
        the baseline's same corner). [] for a degenerate lap or when no corners were detected.
        Cached per lap; cleared on re-segment (and on a reference change via invalidate_stats).

        The Δ baseline is the local best lap normally, or the CROSS-RECORDING reference lap's
        projected corner stats when one is loaded (F7)."""
        got = self._stats_cache.get(lap_id)
        if got is not None:
            return got
        basis = self.basis()
        best = self._best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, total_ref = basis
        dist, speed_kmh, elapsed = self._lap_arrays(lap_id)
        if len(dist) < 2 or float(dist[-1]) <= 0:
            return []
        ref_stats = self.reference_corner_stats()
        if ref_stats is not None:
            ref = ref_stats
        else:
            ref = self.lap_corner_stats(best) if lap_id != best else None
        # Drift-gate spatial traces: the best lap (the corner-window reference frame) + this lap.
        # The best lap itself projects onto its OWN odometer (zero drift → identity), so its trace
        # pair is harmless; a degenerate trace → None → normalized projection (unchanged).
        traces = self._lap_traces(lap_id, self._best_trace())
        stats = corners.lap_corner_stats(corner_list, total_ref, dist, speed_kmh, elapsed,
                                         ref=ref or None, traces=traces)
        self._stats_cache[lap_id] = stats
        return stats

    def corner_session_bests(self) -> list[float]:
        """Per-corner session-best time-in-corner across all VALID laps (the purple-cell
        convention, matching the per-sector session bests). [] when no corners. Cached;
        cleared on re-segment."""
        if self._bests_cache is not _UNSET:
            return self._bests_cache
        per_lap = [self.lap_corner_stats(i) for i in self._valid_lap_ids()]
        per_lap = [st for st in per_lap if st]
        n = len(self.corner_list())
        self._bests_cache = [
            min(st[i].time for st in per_lap) for i in range(n)
        ] if per_lap and n else []
        return self._bests_cache

    # ------------------------------------------------------------------ map / seek glue
    def corner_map_markers(self) -> list[tuple[str, float, float, int]]:
        """(label, x, y, direction) per corner — the apex position in LOCAL metres on the
        best lap's trace, for the map's corner labels. [] when no corners/best lap."""
        basis = self.basis()
        best = self._best_lap_id()
        if basis is None or not basis[0] or best is None:
            return []
        corner_list, _total_ref = basis
        _t, xs, ys, _v, cum = self._lap_columns(best)
        apexes = np.asarray([c.apex for c in corner_list])
        mx = np.interp(apexes, cum, xs)
        my = np.interp(apexes, cum, ys)
        return [(c.label, float(mx[i]), float(my[i]), c.direction)
                for i, c in enumerate(corner_list)]

    def corner_entry_media_time(self, lap_id: int, cid: int) -> float | None:
        """Media-clock time (s) `lap_id` enters corner `cid` — the jump-to seek target. Projects
        the corner's enter point onto this lap's odometer and reads elapsed->media there. None if
        unknown/degenerate. Absolute (lap start + elapsed)."""
        basis = self.basis()
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
