"""Cross-recording reference lap (F7) — the data side of "race a friend's GoPro file".

A `ReferenceLap` is ONE lap, taken from a SEPARATE recording's `Session`, repackaged so the
primary session can use it wherever it would use its own best lap: the Δ-to-best charts, the
map best-lap overlay, the chart sector guide lines, and the lap-table per-corner Δ columns.

WHY this is a thin value object and NOT a second live Session wired into the views:
  * The delta machinery aligns laps by NORMALIZED distance fraction (s = cum_dist / total),
    so a reference lap only needs its arc-length curves — `(dist, speed_kmh, elapsed)` — in
    its OWN metres/seconds. The two recordings' lap lengths and start lines differ slightly;
    normalized-distance alignment handles that exactly (the same machinery `Session.delta`
    already uses), so no new alignment is invented here.
  * For the MAP overlay the reference racing line must be drawn in the PRIMARY session's
    LOCAL frame. The two recordings have independent coordinate systems (each centred on its
    own cleaned-trace bbox), so the reference loop is aligned onto the primary best lap's loop
    by the SAME closed-loop cyclic-arc-length similarity fit the centerline gap-fill uses
    (`studio.reference.fit_loop_to_loop`) — reused, not reinvented.

This module is PACER-FREE (numpy only): `Session.load_reference` does the one pacer-backed
step (loading the second Session via the normal pipeline) and hands the already-extracted
plain numpy arrays here. So `cross_reference` never imports `pacer`, matching the
views-stay-pacer-free / analysis-is-numpy boundary in PLAN.md.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import reference

# A reference loop must overlay the primary track closely to be trustworthy as a racing-line
# overlay. The closed-loop fit reports an RMS distance (metres) of the reference points to the
# primary best-lap polyline; above this the spatial alignment is judged unreliable and the map
# overlay is suppressed (the Δ charts/table, which are distance-NORMALIZED and frame-independent,
# are unaffected). Generous vs the ~8 m kart-track width + lap-to-lap line spread, tight vs a
# gross mis-fit. Same scale as reference.COVERAGE_TOL_M, which gates the centerline fit.
MAP_FIT_RMS_TOL_M = 12.0


@dataclass
class ReferenceLap:
    """One lap from another recording, as the primary session consumes it.

    `dist`/`speed_kmh`/`elapsed` are the reference lap's own arc-length curves (metres, km/h,
    seconds-from-its-own-start), index-aligned and monotonic in `dist` — identical in shape to
    what `Session._lap_arrays` returns for a local lap, so the normalized-distance alignment in
    `Session.delta` / `delta_at_lap` consumes them unchanged. `total_time` is the reference
    lap's full time (`elapsed[-1]`), the value the Δ endpoint must equal minus the primary lap.

    `overlay_xy` is the reference racing line already transformed into the PRIMARY session's
    LOCAL frame (an (M,2) closed ring), or None if the spatial fit was too poor to draw (the
    charts/table still work — only the map overlay is suppressed). `source_label` names the
    origin recording for the UI chip/statusbar.
    """

    dist: np.ndarray
    speed_kmh: np.ndarray
    elapsed: np.ndarray
    total_time: float
    source_label: str
    lap_id: int
    overlay_xy: np.ndarray | None
    map_fit_rms: float | None  # RMS (m) of the overlay fit, or None when no overlay

    def arrays(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """`(dist, speed_kmh, elapsed)` — the `_lap_arrays`-shaped triple `Session.delta`
        and `delta_at_lap` interpolate the reference curve from."""
        return self.dist, self.speed_kmh, self.elapsed

    def time_dist_elapsed(self) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """`(times, dists, elapsed)` for the per-tick delta path (`delta_at_lap` /
        `delta_at_time`). The reference lives on no shared media clock, so its `times` axis is
        just its own elapsed (starts at 0) — the delta math only ever uses `dists`/`elapsed`
        from the reference side (it inverts s -> reference distance -> reference elapsed), never
        the reference's absolute time, so a 0-anchored time axis is exactly right."""
        return self.elapsed, self.dist, self.elapsed


def build(
    *,
    dist: np.ndarray,
    speed_kmh: np.ndarray,
    elapsed: np.ndarray,
    loop_xy: np.ndarray,
    primary_loop_xy: np.ndarray | None,
    source_label: str,
    lap_id: int,
) -> ReferenceLap:
    """Assemble a `ReferenceLap` from the reference lap's extracted arrays.

    `dist`/`speed_kmh`/`elapsed` are the reference lap's own `_lap_arrays` triple. `loop_xy`
    is the reference lap's closed (xs, ys) loop in the REFERENCE recording's local metres;
    `primary_loop_xy` is the PRIMARY best lap's closed loop in the primary's local metres. The
    overlay is the reference loop fit onto the primary loop (so it draws in the primary frame);
    if the fit RMS exceeds MAP_FIT_RMS_TOL_M (or either loop is degenerate) the overlay is None
    and only the map racing-line is skipped — the distance-aligned charts/table are unaffected.
    """
    dist = np.asarray(dist, float)
    speed_kmh = np.asarray(speed_kmh, float)
    elapsed = np.asarray(elapsed, float)
    total_time = float(elapsed[-1]) if len(elapsed) else 0.0

    overlay_xy = None
    fit_rms = None
    if (primary_loop_xy is not None and len(primary_loop_xy) >= 10
            and loop_xy is not None and len(loop_xy) >= 10):
        # Reuse the centerline's closed-loop cyclic-arc-length similarity fit: align the
        # reference loop ONTO the primary best-lap loop, so it draws in the primary's frame.
        fitted, info = reference.fit_loop_to_loop(loop_xy, primary_loop_xy)
        fit_rms = float(info["rms"])
        if fit_rms <= MAP_FIT_RMS_TOL_M:
            overlay_xy = fitted

    return ReferenceLap(
        dist=dist, speed_kmh=speed_kmh, elapsed=elapsed, total_time=total_time,
        source_label=source_label, lap_id=lap_id,
        overlay_xy=overlay_xy, map_fit_rms=fit_rms,
    )
