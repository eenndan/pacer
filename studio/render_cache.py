"""LapRenderCache — the per-lap MAP-RENDERING cache cluster, extracted from Session.

What lives here (and ONLY here): the gap-aware draw segments the map renders for a lap
(`lap_trace_segments` — measured GPS runs + reconstructed fills, via studio/gapfill.py)
and the georeferenced reference-centerline fallback donor that reconstruction can borrow
from (via studio/reference.py). Nothing in this module alters any analysis quantity (lap
times, distances, deltas) — it only decides what the map DRAWS across GPS dropouts.

PACER-FREE by design (AGENTS.md: only ingest/load/session/tracks may import pacer): every
input arrives as a plain numpy array or a Session-bound callable over Session's own cached
per-lap arrays, so this module never touches the bound core.

Cache lifetime — the perf invariant (see Session.set_timing_lines): the draw segments are
computed once per lap on FIRST DRAW, never per frame, and become stale on re-segmentation
(lap ids, windows and donor traces all change when a timing line moves), so Session calls
`invalidate()` from set_timing_lines. The reference centerline is intentionally NOT
dropped there — same behaviour as before the extraction: it's only the last-resort fill
donor (the cross-lap borrow dominates), and the georeferenced track shape is good enough
for a dimmed dashed fill regardless of which lap it was fit against.
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np

from . import gapfill

# (xs, ys, times) for one lap: local metres + media-clock seconds (Session._lap_trace_xyt).
LapXYT = Callable[[int], tuple[np.ndarray, np.ndarray, np.ndarray]]


class LapRenderCache:
    """Per-lap draw-segment + reference-centerline cache over Session-provided arrays.

    `lap_xyt` / `valid_lap_ids` / `lap_has_dropout` / `lap_time` are Session-bound
    callables (Session owns the pacer side and its own per-lap caches); `trace_times`
    is the full-trace media-clock time array (Session.tt), used only to size gaps.
    """

    def __init__(self, *, lap_xyt: LapXYT,
                 valid_lap_ids: Callable[[], list[int]],
                 lap_has_dropout: Callable[[int], bool],
                 lap_time: Callable[[int], float],
                 trace_times: np.ndarray):
        self._lap_xyt = lap_xyt
        self._valid_lap_ids = valid_lap_ids
        self._lap_has_dropout = lap_has_dropout
        self._lap_time = lap_time
        self._trace_times = trace_times
        # Per-lap gap-filled draw segments (measured + inferred runs) — computed once per
        # lap on first draw, never per frame; cleared on re-segment via invalidate().
        self._segs: dict[int, list[gapfill.Segment]] = {}
        # Lazily-built georeferenced track centerline (fallback donor). NOT cleared on
        # re-segment (see the module docstring).
        self._reference_xy = None

    def invalidate(self) -> None:
        """Drop the per-lap draw segments — called from Session.set_timing_lines (the single
        re-segmentation point): lap ids/windows and donor traces are all stale after a
        timing-line change. The reference centerline is deliberately kept (module docstring)."""
        self._segs.clear()

    def lap_trace_segments(self, lap_id: int) -> list[gapfill.Segment]:
        """Ordered list of `gapfill.Segment` for drawing this lap: measured GPS runs and
        reconstructed (inferred) fills, tagged so the renderer can dash/dim the inferred ones.

        MAP RENDERING ONLY. Built from the lap's kept-point arrays (the same points
        `lap_trace_xy` returns); it does NOT alter any analysis quantity. Cached per lap."""
        cached = self._segs.get(lap_id)
        if cached is not None:
            return cached
        xs, ys, ts = self._lap_xyt(lap_id)
        donors = self.donors_for(lap_id)
        segs, _fills = gapfill.reconstruct_lap(xs, ys, ts, donors, med_dt=self.median_sample_dt())
        self._segs[lap_id] = segs
        return segs

    def median_sample_dt(self) -> float:
        """Median inter-sample interval over the whole trace (s) — used to size gaps."""
        if len(self._trace_times) < 2:
            return 0.1
        d = np.diff(self._trace_times)
        d = d[(d > 0) & (d < 1.0)]
        return float(np.median(d)) if len(d) else 0.1

    def donors_for(self, lap_id: int) -> list[dict]:
        """Ordered fill-source list for reconstructing `lap_id`'s gaps: every OTHER valid lap
        first (cross-lap borrow, the primary source), then the georeferenced reference
        centerline LAST (fallback). Each donor is {"xy", "name", "is_reference"}."""
        donors = []
        for other in self._valid_lap_ids():
            if other == lap_id:
                continue
            ox, oy, _ = self._lap_xyt(other)
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
        studio/reference.py for the trace+georeference of the Daytona MK centerline:
        the stored loop is fit against ONE clean lap's closed loop (cyclic arc-length
        correspondence), not the unordered all-laps point cloud."""
        if self._reference_xy is not None:
            return self._reference_xy if len(self._reference_xy) else None
        from . import reference  # local import: optional, only on the fallback path
        self._reference_xy = reference.centerline_local(self.reference_fit_loop())
        return self._reference_xy if len(self._reference_xy) else None

    def reference_fit_loop(self):
        """The lap loop the reference centerline is fit against: the fastest valid lap
        WITHOUT a GPS dropout (an ordered, closed, complete track footprint), else the
        fastest valid lap. None if there are no valid laps."""
        valid = self._valid_lap_ids()
        if not valid:
            return None
        by_time = sorted(valid, key=self._lap_time)
        pick = next((lap for lap in by_time if not self._lap_has_dropout(lap)), by_time[0])
        xs, ys, _ = self._lap_xyt(pick)
        if len(xs) < 10:
            return None
        return np.column_stack([xs, ys])
