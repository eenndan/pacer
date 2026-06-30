"""Corner model: curvature-based corner detection + per-corner lap analysis.

PACER-FREE BY CONTRACT (numpy only); fed by Session's per-lap arrays, so neither this module
nor the views import the compiled `pacer` bindings. NOT MAP-MATCHING: everything runs on our own
smoothed GPS trace (curvature from its own heading, threshold from its own distribution) — no
external centerline.

Pipeline: per-lap curvature kappa(s) → median profile on the best-lap grid (averages out line
choice + GPS noise) → log-domain Otsu threshold (no magic constant) → hysteresis spans split at
sign changes, merged across jitter, filtered by arc length + turn angle → enter/exit/apex
(|kappa|-weighted centroid, stable on flat-topped sweepers) + direction, in best-lap odometer.

Projection (lap_corner_stats / segment_times): corner windows are fractions of the best lap's
odometer, projected onto every lap by normalized distance (same as lap_sector_splits). Corners +
straights partition each lap, so the telescoping sum of segment times equals the lap time exactly
(asserted).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import _smooth

# --- per-corner alignment drift gate ---------------------------------------------------
# The corner-window projection (lap_corner_stats / segment_times / coaching.corner_phase_losses)
# aligns a comparison lap to the reference (best) lap by NORMALIZED distance (d·total_lap/total_ref
# — same fraction = same track position). The codebase measures the laps' line-length drift at
# <0.5% (see session._ROLLING_SEARCH_FRAC's note + best_rolling_lap), so within that bound the
# normalized projection lands the boundaries within sub-sample tolerance and is kept verbatim — the
# common, well-matched case stays NUMERICALLY IDENTICAL to the pre-gate output. Only when the drift
# EXCEEDS this bound (a normalized fraction is then a materially different physical point, the same
# bias best_rolling_lap rejects) do we fall back to the robust heading-gated spatial nearest-point
# match (project_boundaries). 0.005 = the documented 0.5% line-length bound.
NORMALIZED_DRIFT_MAX = 0.005

# Spatial nearest-point gates for the drift fallback — the SAME constants best_rolling_lap trusts
# (session._ROLLING_*): search a ±SEARCH_FRAC arc of the comparison lap around the reference
# fraction, keep only same-direction samples (heading cos ≥ MIN_COS), accept the sub-sample-refined
# closest approach only when it is within MATCH_MAX_M of the reference point. A boundary whose match
# fails ANY gate falls back to the normalized projection for THAT boundary (never a NaN/None).
_SPATIAL_SEARCH_FRAC = 0.02      # ±2% of the comparison lap's samples (~21 m), floored at 5
_SPATIAL_HEADING_MIN_COS = 0.5   # same-direction within 60° (rejects the other leg of a corner)
_SPATIAL_MATCH_MAX_M = 3.0       # refined closest approach must be ≤ 3 m to count as the same point


def line_length_drift(total_lap: float, total_ref: float) -> float:
    """The cheap line-length drift between a comparison lap and the reference (best) lap:
    |total_lap − total_ref| / total_ref, where each total is that lap's full odometer (cum[-1]).
    0 (no drift) for a non-positive reference total. This is the single scalar the per-corner
    alignment gates on (≤ NORMALIZED_DRIFT_MAX → keep normalized; above → spatial fallback)."""
    total_ref = float(total_ref)
    if total_ref <= 0:
        return 0.0
    return abs(float(total_lap) - total_ref) / total_ref


def _unit_tangents(xs: np.ndarray, ys: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Per-sample unit direction-of-travel of a trace (central differences, normalized; a
    zero-length step keeps a zero vector, which the heading gate then rejects). Mirrors
    session._unit_tangents — the same same-direction filter best_rolling_lap uses. Needs len ≥ 2."""
    tx = np.gradient(np.asarray(xs, float))
    ty = np.gradient(np.asarray(ys, float))
    norm = np.hypot(tx, ty)
    norm[norm == 0] = 1.0
    return tx / norm, ty / norm


def _spatial_project_boundary(d_ref: float, total_ref: float,
                              ref_xs, ref_ys, ref_cum,
                              lap_xs, lap_ys, lap_cum) -> float | None:
    """Map ONE reference-odometer boundary `d_ref` onto a comparison lap's odometer by the robust
    heading-gated, sub-sample-refined nearest-point search (the best_rolling_lap machinery, on a
    single anchor instead of every sample). The reference (x,y) at `d_ref` is the anchor; the
    nearest same-direction comparison-lap sample within the ±_SPATIAL_SEARCH_FRAC arc is found, its
    two adjacent segments are projected onto, and the closer projection's chord parameter
    interpolates the comparison-lap odometer. Returns None when no candidate passes the heading or
    _SPATIAL_MATCH_MAX_M distance gate (caller falls back to the normalized projection)."""
    ref_xs = np.asarray(ref_xs, float)
    ref_ys = np.asarray(ref_ys, float)
    ref_cum = np.asarray(ref_cum, float)
    lap_xs = np.asarray(lap_xs, float)
    lap_ys = np.asarray(lap_ys, float)
    lap_cum = np.asarray(lap_cum, float)
    n_lap = len(lap_cum)
    if n_lap < 2 or len(ref_cum) < 2 or total_ref <= 0:
        return None
    # Anchor: the reference trace point + direction at the boundary odometer.
    ax = float(np.interp(d_ref, ref_cum, ref_xs))
    ay = float(np.interp(d_ref, ref_cum, ref_ys))
    rtx, rty = _unit_tangents(ref_xs, ref_ys)
    atx = float(np.interp(d_ref, ref_cum, rtx))
    aty = float(np.interp(d_ref, ref_cum, rty))
    # Search only a ±SEARCH_FRAC arc of the comparison lap around the same normalized fraction.
    total_lap = float(lap_cum[-1])
    frac = d_ref / total_ref
    center = int(np.clip(np.searchsorted(lap_cum, frac * total_lap), 0, n_lap - 1))
    k = max(5, int(_SPATIAL_SEARCH_FRAC * n_lap))
    lo = max(0, center - k)
    hi = min(n_lap, center + k + 1)
    win = np.arange(lo, hi)
    d2 = (lap_xs[win] - ax) ** 2 + (lap_ys[win] - ay) ** 2
    # Same-direction gate: reject the other leg of a corner / out-and-back.
    ltx, lty = _unit_tangents(lap_xs, lap_ys)
    heading_cos = atx * ltx[win] + aty * lty[win]
    d2 = np.where(heading_cos >= _SPATIAL_HEADING_MIN_COS, d2, np.inf)
    if not np.isfinite(d2).any():
        return None
    j = int(win[int(np.argmin(d2))])

    # Sub-sample refinement: project the anchor onto the two trace segments adjacent to its nearest
    # comparison-lap sample; the closer projection's chord parameter interpolates the odometer.
    def _project(j0: int, j1: int) -> tuple[float, float]:
        vx, vy = lap_xs[j1] - lap_xs[j0], lap_ys[j1] - lap_ys[j0]
        len2 = vx * vx + vy * vy
        if len2 <= 0:
            return ((lap_xs[j0] - ax) ** 2 + (lap_ys[j0] - ay) ** 2, float(lap_cum[j0]))
        u = float(np.clip(((ax - lap_xs[j0]) * vx + (ay - lap_ys[j0]) * vy) / len2, 0.0, 1.0))
        qx, qy = lap_xs[j0] + u * vx, lap_ys[j0] + u * vy
        return ((qx - ax) ** 2 + (qy - ay) ** 2,
                float(lap_cum[j0] + u * (lap_cum[j1] - lap_cum[j0])))

    d2_lo, dist_lo = _project(max(j - 1, 0), j)
    d2_hi, dist_hi = _project(j, min(j + 1, n_lap - 1))
    best_d2, best_dist = (d2_lo, dist_lo) if d2_lo <= d2_hi else (d2_hi, dist_hi)
    # Distance gate on the REFINED closest approach (same point only if within MATCH_MAX_M).
    if best_d2 > _SPATIAL_MATCH_MAX_M ** 2:
        return None
    return best_dist


def project_boundaries(d_ref, total_ref: float, total_lap: float, *,
                       traces: tuple | None = None) -> np.ndarray:
    """Project reference-odometer corner-window boundaries `d_ref` onto a comparison lap's odometer.

    The DRIFT-GATED alignment shared by lap_corner_stats / segment_times / coaching: when the laps'
    line-length drift is within NORMALIZED_DRIFT_MAX (the common, well-matched case) OR no spatial
    `traces` are supplied, this is exactly the legacy normalized projection
    `d_ref · total_lap / total_ref` — byte-identical to the pre-gate output. When the drift EXCEEDS
    the bound AND `traces` are supplied, each boundary is instead mapped by the robust heading-gated
    spatial nearest-point match (_spatial_project_boundary); any boundary whose match fails the gate
    falls back to its normalized value (never a NaN).

    `traces`, when given, is (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) — the reference
    (best) lap's and the comparison lap's local-frame trace + odometer (Session feeds these; the
    pure-numpy unit tests omit them and so always get the normalized projection)."""
    d_ref = np.asarray(d_ref, float)
    total_ref = float(total_ref)
    total_lap = float(total_lap)
    normalized = d_ref * (total_lap / total_ref) if total_ref > 0 else d_ref.copy()
    if traces is None or line_length_drift(total_lap, total_ref) <= NORMALIZED_DRIFT_MAX:
        return normalized
    ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum = traces
    out = normalized.copy()
    for i, d in enumerate(d_ref):
        spatial = _spatial_project_boundary(float(d), total_ref,
                                            ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum)
        if spatial is not None:
            out[i] = spatial
    # The boundaries arrive in track order (enter1, exit1, enter2, …). A spatial match could in
    # principle land marginally out of order vs its neighbours (sub-metre, near a hairpin's
    # closest-approach); clamp to [0, total_lap] and enforce the non-decreasing order the partition
    # (segment_times' telescoping sum) and the in-window apex search rely on. This only ever moves a
    # boundary that the spatial match nudged across a neighbour, never the normalized values.
    if total_lap > 0:
        out = np.clip(np.maximum.accumulate(np.clip(out, 0.0, total_lap)), 0.0, total_lap)
    return out


# --- model constants -------------------------------------------------------------------
# Constants tuned on the D24 recordings; the detected set is insensitive within the noted bands.
KAPPA_SMOOTH_M = 8.0      # m of arc; curvature boxcar (~5 samples), resolves the shortest corners
GRID_STEP_M = 0.75        # m; median-profile grid step, ~2x finer than GPS spacing
LOG_KAPPA_FLOOR = 1e-4    # |kappa| floor before log10; only guards log10(0), can't affect the split
HYSTERESIS_RATIO = 0.8    # span extends while |kappa| >= ratio×threshold; Schmitt trigger; band [0.6,1.0]
MERGE_GAP_M = 10.0        # m; re-merge adjacent same-direction jitter fragments; band [6,15]
MIN_SPAN_M = KAPPA_SMOOTH_M  # shorter than the smoothing support is unresolvable; band [5,12]
MIN_TURN_DEG = 30.0       # min integrated turn for a real corner (kinks <=17°, corners >=44°); band [20,40]


@dataclass(frozen=True)
class Corner:
    """One detected corner, in REFERENCE (best) lap odometer metres, track order."""

    cid: int          # 1-based id in track order (C1 is the first corner after the line)
    enter: float      # odometer (m) where sustained cornering starts
    exit: float       # odometer (m) where it ends
    apex: float       # odometer (m) of the |kappa|-weighted centroid (the geometric apex)
    direction: int    # +1 = left (kappa > 0), -1 = right
    turn_deg: float   # integrated heading change magnitude over the span (degrees)

    @property
    def label(self) -> str:
        return f"C{self.cid}"


@dataclass(frozen=True)
class CornerStat:
    """One lap x corner: the projected per-corner metrics (speeds in km/h, times in s)."""

    cid: int                  # Corner.cid this row belongs to
    time: float               # time-in-corner (s)
    delta: float              # time vs the reference lap's same corner (s; 0 for the ref)
    apex_speed: float         # MIN speed inside the window (km/h)
    apex_speed_delta: float   # vs the reference lap's apex speed (km/h; 0 for the ref)
    apex_dist: float          # THIS lap's odometer (m) at the min-speed sample
    entry_speed: float        # speed at the corner-enter boundary (km/h)
    exit_speed: float         # speed at the corner-exit boundary (km/h)


# ------------------------------------------------------------------- curvature profile
def lap_curvature(xs, ys, dists) -> np.ndarray:
    """Signed curvature kappa(s) (1/m, + = left) of one lap's local-frame trace: unwrapped
    heading differentiated vs arc length, boxcar-smoothed over KAPPA_SMOOTH_M of arc.
    `dists` must be strictly increasing (dedupe stationary samples first)."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    dists = np.asarray(dists, float)
    heading = np.unwrap(np.arctan2(np.gradient(ys, dists), np.gradient(xs, dists)))
    kappa = np.gradient(heading, dists)
    ds = float(np.median(np.diff(dists)))
    w = max(int(round(KAPPA_SMOOTH_M / max(ds, 1e-9))), 1)
    return _smooth(kappa, w)


def pooled_curvature(traces, total_ref: float):
    """The track's curvature profile on the reference lap's odometer grid: the MEDIAN of
    the per-lap kappa profiles, aligned by normalized distance (same fraction = same track
    position — the projection identity the whole feature rests on).

    `traces` is an iterable of (xs, ys, cum_dists) triples, one per clean lap (the caller
    passes the session's valid, dropout-free laps; a single trace degrades to that lap's own
    profile). Returns (grid_dists, kappa) with grid_dists spanning [0, total_ref]."""
    n = max(int(round(float(total_ref) / GRID_STEP_M)), 16)
    s_grid = np.linspace(0.0, 1.0, n)
    profiles = []
    for xs, ys, cum in traces:
        xs = np.asarray(xs, float)
        ys = np.asarray(ys, float)
        cum = np.asarray(cum, float)
        keep = np.concatenate(([True], np.diff(cum) > 1e-9))  # drop stationary duplicates
        xs, ys, cum = xs[keep], ys[keep], cum[keep]
        if len(cum) < 8 or cum[-1] <= 0:
            continue
        k = lap_curvature(xs, ys, cum)
        if not np.all(np.isfinite(k)):
            continue
        profiles.append(np.interp(s_grid, cum / cum[-1], k))
    if not profiles:
        return s_grid * float(total_ref), np.zeros(n)
    return s_grid * float(total_ref), np.median(np.vstack(profiles), axis=0)


def derive_threshold(kappa) -> float:
    """Corner/straight |kappa| split via Otsu (max between-class variance) on log10|kappa| — no
    magic constant. Log domain because |kappa| has two log-separated modes (straight noise floor
    vs corners); in linear space the corner mode's long tail destabilises Otsu inside the corner
    mode itself, while log space lands the split mid-valley and stable. Detection is insensitive to
    the exact value (corner set unchanged for a 0.8×..1.25× scaling)."""
    a = np.log10(np.maximum(np.abs(np.asarray(kappa, float)), LOG_KAPPA_FLOOR))
    hist, edges = np.histogram(a, bins=128)
    p = hist.astype(float) / max(hist.sum(), 1)
    centers = (edges[:-1] + edges[1:]) / 2.0
    w0 = np.cumsum(p)               # class-0 (straighter) weight
    w1 = 1.0 - w0                   # class-1 (cornering) weight
    mu = np.cumsum(p * centers)     # class-0 first moment
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * w0 - mu) ** 2 / (w0 * w1)  # between-class variance per split
    between[~np.isfinite(between)] = 0.0
    return float(10.0 ** centers[int(np.argmax(between))])


# ----------------------------------------------------------------------- detection
def detect_corners(dists, kappa, threshold: float | None = None) -> list[Corner]:
    """Corner list (track order, C1 first) from a curvature profile on an odometer grid.

    Pipeline: hysteresis spans on |kappa| (enter at `threshold`, extend while >= ratio x
    threshold), split at kappa sign changes so an S-complex yields one corner per direction,
    keep only parts that actually reach the threshold, re-merge adjacent same-direction
    parts within MERGE_GAP_M (jitter fragments, not real straights), then drop spans
    shorter than MIN_SPAN_M or turning less than MIN_TURN_DEG (sub-corner kinks).

    A lap is treated LINEARLY [0, total]: the timing line is conventionally on a straight,
    so a corner is not expected to straddle the start/finish seam; if the line does sit in
    an arc, the arc shows as a corner at each end of the lap (consistently across laps)."""
    dists = np.asarray(dists, float)
    kappa = np.asarray(kappa, float)
    if len(dists) < 3:
        return []
    hi = derive_threshold(kappa) if threshold is None else float(threshold)
    lo = hi * HYSTERESIS_RATIO
    a = np.abs(kappa)
    n = len(a)

    # 1. hysteresis spans (index-inclusive [j0, j1]) seeded wherever |kappa| >= hi.
    spans: list[tuple[int, int]] = []
    i = 0
    while i < n:
        if a[i] >= hi:
            j0, j1 = i, i
            while j0 > 0 and a[j0 - 1] >= lo:
                j0 -= 1
            while j1 + 1 < n and a[j1 + 1] >= lo:
                j1 += 1
            spans.append((j0, j1))
            i = j1 + 1
        else:
            i += 1

    # 2. split each span at kappa sign changes (S-complex -> one part per direction)…
    parts: list[tuple[int, int]] = []
    for j0, j1 in spans:
        sgn = np.sign(kappa[j0:j1 + 1])
        cuts = np.flatnonzero(np.diff(sgn) != 0)
        lo_i = j0
        for c in cuts:
            parts.append((lo_i, j0 + int(c)))
            lo_i = j0 + int(c) + 1
        parts.append((lo_i, j1))
    # …keeping only parts that genuinely reach the seed threshold (a sign-flip sliver that
    # only ever sat between lo and hi is jitter, not a corner of its own).
    parts = [(j0, j1) for j0, j1 in parts
             if j1 >= j0 and float(np.max(a[j0:j1 + 1])) >= hi]

    def _dir(j0: int, j1: int) -> int:
        seg = kappa[j0:j1 + 1]
        return 1 if seg[int(np.argmax(np.abs(seg)))] > 0 else -1

    # 3. re-merge ADJACENT same-direction parts separated by less than MERGE_GAP_M.
    merged: list[tuple[int, int]] = []
    for p in parts:
        if merged and _dir(*merged[-1]) == _dir(*p) and \
                dists[p[0]] - dists[merged[-1][1]] <= MERGE_GAP_M:
            merged[-1] = (merged[-1][0], p[1])
        else:
            merged.append(p)

    # 4. length + turn-angle filters, apex/direction extraction.
    out: list[Corner] = []
    for j0, j1 in merged:
        dd = dists[j0:j1 + 1]
        if dd[-1] - dd[0] < MIN_SPAN_M:
            continue
        seg = kappa[j0:j1 + 1]
        turn = float(np.degrees(abs(np.trapezoid(seg, dd))))  # integral of kappa ds = angle
        if turn < MIN_TURN_DEG:
            continue
        w = np.abs(seg)
        apex = float(np.sum(dd * w) / np.sum(w))  # |kappa|-weighted centroid (see module doc)
        out.append(Corner(cid=len(out) + 1, enter=float(dd[0]), exit=float(dd[-1]),
                          apex=apex, direction=_dir(j0, j1), turn_deg=turn))
    return out


# ---------------------------------------------------------------------- projection
def _window_edges(corner_list: list[Corner], total_ref: float, total_lap: float,
                  traces: tuple | None = None) -> np.ndarray:
    """All partition edges (lap odometer metres) for one lap: lap start, each corner's
    enter/exit projected onto the lap's odometer (the drift-gated alignment — normalized within
    NORMALIZED_DRIFT_MAX, robust spatial above; see project_boundaries), and the lap end. The lap
    start/end stay the literal 0 / total_lap (the timing line is the shared S/F point on both laps,
    so it never needs spatial re-matching). `traces` (Session-fed) enables the spatial fallback."""
    interior = []
    for c in corner_list:
        interior.extend((c.enter, c.exit))
    edges = [0.0]
    if interior:
        edges.extend(project_boundaries(interior, total_ref, total_lap, traces=traces).tolist())
    edges.append(float(total_lap))
    return np.asarray(edges, float)


def segment_times(corner_list: list[Corner], total_ref: float, dists, elapsed,
                  traces: tuple | None = None) -> np.ndarray:
    """Per-segment times of the corner/straight partition: 2N+1 entries [straight0, corner1, ...].
    One np.interp at the shared edges, so segments sum to the lap time exactly (asserted). `traces`
    (Session-fed) enables the drift-gated spatial alignment; omitted → the normalized projection."""
    dists = np.asarray(dists, float)
    elapsed = np.asarray(elapsed, float)
    edges = _window_edges(corner_list, total_ref, float(dists[-1]), traces)
    t_at = np.interp(edges, dists, elapsed)
    seg = np.diff(t_at)
    assert abs(float(seg.sum()) - float(elapsed[-1] - elapsed[0])) < 1e-9, \
        "corner/straight partition does not sum to the lap time"
    return seg


def lap_corner_stats(corner_list: list[Corner], total_ref: float, dists, speed_kmh,
                     elapsed, ref: list[CornerStat] | None = None,
                     traces: tuple | None = None) -> list[CornerStat]:
    """Project the corner windows onto ONE lap and measure each corner: time-in-corner
    (from the same edge interpolation as segment_times, so corner times + straight times
    partition the lap exactly), apex = MIN speed over the in-window samples (+ its lap
    odometer position), entry/exit speeds at the window edges, and deltas vs `ref` (the
    reference — best — lap's own stats; None for the reference lap itself -> deltas 0).

    The window-boundary projection is the drift-gated alignment (project_boundaries): normalized
    distance within NORMALIZED_DRIFT_MAX, the robust spatial nearest-point match above it. `traces`
    (Session-fed (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum)) enables the spatial fallback;
    omitted (pure-numpy callers) → normalized, byte-identical to the pre-gate output."""
    dists = np.asarray(dists, float)
    speed_kmh = np.asarray(speed_kmh, float)
    elapsed = np.asarray(elapsed, float)
    seg = segment_times(corner_list, total_ref, dists, elapsed, traces)
    total_lap = float(dists[-1])
    # The same gated enter/exit boundaries segment_times partitions on, so the in-window apex/edge
    # speeds read off the identical window (interleaved [enter1, exit1, enter2, exit2, …]).
    interior = []
    for c in corner_list:
        interior.extend((c.enter, c.exit))
    proj = (project_boundaries(interior, total_ref, total_lap, traces=traces)
            if interior else np.empty(0))
    out: list[CornerStat] = []
    for i, c in enumerate(corner_list):
        d0 = float(proj[2 * i])
        d1 = float(proj[2 * i + 1])
        t = float(seg[2 * i + 1])  # this corner's slice of the partition
        idx = np.flatnonzero((dists >= d0) & (dists <= d1))
        if len(idx):
            j = idx[int(np.argmin(speed_kmh[idx]))]
            apex_speed = float(speed_kmh[j])
            apex_dist = float(dists[j])
        else:  # window narrower than the sample spacing — fall back to the midpoint
            apex_dist = (d0 + d1) / 2.0
            apex_speed = float(np.interp(apex_dist, dists, speed_kmh))
        r = ref[i] if ref is not None and i < len(ref) else None
        out.append(CornerStat(
            cid=c.cid, time=t,
            delta=t - r.time if r is not None else 0.0,
            apex_speed=apex_speed,
            apex_speed_delta=apex_speed - r.apex_speed if r is not None else 0.0,
            apex_dist=apex_dist,
            entry_speed=float(np.interp(d0, dists, speed_kmh)),
            exit_speed=float(np.interp(d1, dists, speed_kmh)),
        ))
    return out
