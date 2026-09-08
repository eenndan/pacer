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
odometer, projected onto every lap by normalized distance (same as lap_sector_splits) — or, on a
lap whose line length drifts past NORMALIZED_DRIFT_MAX, by ONE monotone spatial warp for that whole
lap (project_boundaries). Never a mix of the two within a lap: that is what shrank segments by up
to 27 % and made the ideal lap's headline 44 % artifact. Corners + straights partition each lap, so
the telescoping sum of segment times equals the lap time exactly (asserted).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from ._signal import _smooth

# --- per-corner alignment drift gate ---------------------------------------------------
# The corner-window projection (lap_corner_stats / segment_times / coaching.corner_phase_losses)
# aligns a comparison lap to the reference (best) lap by NORMALIZED distance (d·total_lap/total_ref
# — same fraction = same track position). Within this bound the normalized projection is kept
# verbatim, so the common, well-matched case stays NUMERICALLY IDENTICAL to the pre-gate output.
# Above it the alignment switches to the heading-gated spatial nearest-point match, assembled into
# ONE warp for the whole lap (project_boundaries).
#
# WHAT THIS GATE IS AND IS NOT, measured on the D24 0060 pair (38 clean laps, 24 boundaries each,
# error = the LONGITUDINAL component of |P_ref(d) − P_lap(proj(d))|, so a wider racing line does not
# count as misalignment):
#
#   normalized projection, laps BELOW the gate: median 1.96 m, p90 6.46 m, max 14.66 m
#   normalized projection, laps ABOVE the gate: median 2.93 m, p90 7.35 m, max 14.18 m
#   r(line-length drift, per-lap median error) = +0.38
#
# So line-length drift is only a WEAK predictor of odometer misalignment, and the previous note
# here ("within that bound the normalized projection lands the boundaries within sub-sample
# tolerance") is true of the median and false of the tail — a lap at 0.41 % drift carries 14.7 m of
# misalignment while one at 1.55 % carries 7.6 m. The gate is kept at 0.005 because it is what
# preserves byte-identity for the well-matched case (and the golden fingerprint with it), NOT
# because it separates the aligned laps from the misaligned ones. Lowering it is a live follow-up:
# the spatial alignment cuts the median error to 0.10 m on the below-gate laps too.
NORMALIZED_DRIFT_MAX = 0.005

# Spatial nearest-point gates for the drift fallback — the SAME constants best_rolling_lap trusts
# (session._ROLLING_*): search a ±SEARCH_FRAC arc of the comparison lap around the reference
# fraction, keep only same-direction samples (heading cos ≥ MIN_COS), accept the sub-sample-refined
# closest approach only when it is within MATCH_MAX_M of the reference point. A boundary whose match
# fails ANY gate contributes no knot to the lap's warp (it is INTERPOLATED, never mixed frames).
_SPATIAL_SEARCH_FRAC = 0.02      # ±2% of the comparison lap's samples (~21 m), floored at 5
_SPATIAL_HEADING_MIN_COS = 0.5   # same-direction within 60° (rejects the other leg of a corner)
# PUBLIC because it is the projection's stated per-boundary resolution, and
# corner_model.UNMEASURABLE_SPAN_M is defined against it — a segment shorter than this cannot be
# measured by a projection built out of matches only accurate to it.
SPATIAL_MATCH_MAX_M = 3.0        # refined closest approach must be ≤ 3 m to count as the same point


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


def _spatial_matches(d_ref, total_ref: float,
                     ref_xs, ref_ys, ref_cum,
                     lap_xs, lap_ys, lap_cum) -> np.ndarray:
    """Map reference-odometer boundaries `d_ref` onto a comparison lap's odometer by the robust
    heading-gated, sub-sample-refined nearest-point search (the best_rolling_lap machinery, on
    anchors instead of every sample). Per boundary: the reference (x,y) at `d_ref` is the anchor;
    the nearest same-direction comparison-lap sample within the ±_SPATIAL_SEARCH_FRAC arc is found,
    its two adjacent segments are projected onto, and the closer projection's chord parameter
    interpolates the comparison-lap odometer. NaN where no candidate passes the heading or
    SPATIAL_MATCH_MAX_M distance gate.

    Takes the WHOLE boundary set in one call because the per-lap unit-tangent fields are O(n) and
    identical for every anchor — computing them once per lap instead of once per boundary is what
    lets the alignment be built from the full partition without paying for it (the previous
    one-anchor-per-call shape recomputed both tangent fields 24 times a lap)."""
    d_ref = np.asarray(d_ref, float)
    ref_xs = np.asarray(ref_xs, float)
    ref_ys = np.asarray(ref_ys, float)
    ref_cum = np.asarray(ref_cum, float)
    lap_xs = np.asarray(lap_xs, float)
    lap_ys = np.asarray(lap_ys, float)
    lap_cum = np.asarray(lap_cum, float)
    out = np.full(len(d_ref), np.nan)
    n_lap = len(lap_cum)
    if n_lap < 2 or len(ref_cum) < 2 or total_ref <= 0 or not len(d_ref):
        return out
    # Anchors: the reference trace point + direction at each boundary odometer.
    ax = np.interp(d_ref, ref_cum, ref_xs)
    ay = np.interp(d_ref, ref_cum, ref_ys)
    rtx, rty = _unit_tangents(ref_xs, ref_ys)
    atx = np.interp(d_ref, ref_cum, rtx)
    aty = np.interp(d_ref, ref_cum, rty)
    ltx, lty = _unit_tangents(lap_xs, lap_ys)
    # Search only a ±SEARCH_FRAC arc of the comparison lap around the same normalized fraction.
    total_lap = float(lap_cum[-1])
    k = max(5, int(_SPATIAL_SEARCH_FRAC * n_lap))
    centers = np.clip(np.searchsorted(lap_cum, (d_ref / total_ref) * total_lap), 0, n_lap - 1)
    for i in range(len(d_ref)):
        lo = max(0, int(centers[i]) - k)
        hi = min(n_lap, int(centers[i]) + k + 1)
        dx = lap_xs[lo:hi] - ax[i]
        dy = lap_ys[lo:hi] - ay[i]
        d2 = dx * dx + dy * dy
        # Same-direction gate: reject the other leg of a corner / out-and-back.
        d2 = np.where(atx[i] * ltx[lo:hi] + aty[i] * lty[lo:hi] >= _SPATIAL_HEADING_MIN_COS,
                      d2, np.inf)
        if not np.isfinite(d2).any():
            continue
        j = lo + int(np.argmin(d2))
        # Sub-sample refinement: project the anchor onto the two trace segments adjacent to its
        # nearest sample; the closer projection's chord parameter interpolates the odometer.
        best_d2, best_dist = np.inf, 0.0
        for j0, j1 in ((max(j - 1, 0), j), (j, min(j + 1, n_lap - 1))):
            vx, vy = lap_xs[j1] - lap_xs[j0], lap_ys[j1] - lap_ys[j0]
            len2 = vx * vx + vy * vy
            if len2 <= 0:
                cand_d2 = (lap_xs[j0] - ax[i]) ** 2 + (lap_ys[j0] - ay[i]) ** 2
                cand_dist = float(lap_cum[j0])
            else:
                u = float(np.clip(((ax[i] - lap_xs[j0]) * vx
                                   + (ay[i] - lap_ys[j0]) * vy) / len2, 0.0, 1.0))
                qx, qy = lap_xs[j0] + u * vx, lap_ys[j0] + u * vy
                cand_d2 = (qx - ax[i]) ** 2 + (qy - ay[i]) ** 2
                cand_dist = float(lap_cum[j0] + u * (lap_cum[j1] - lap_cum[j0]))
            if cand_d2 < best_d2:
                best_d2, best_dist = cand_d2, cand_dist
        # Distance gate on the REFINED closest approach (same point only if within MATCH_MAX_M).
        if best_d2 <= SPATIAL_MATCH_MAX_M ** 2:
            out[i] = best_dist
    return out


# "no alignment supplied — derive it here". A distinct sentinel because None is the LEGAL value
# meaning "this lap keeps the normalized projection", which a caller must be able to pass through.
DERIVE_ALIGNMENT = object()


def lap_alignment(frame, total_ref: float, total_lap: float, *,
                  traces: tuple | None = None) -> tuple | None:
    """ONE comparison lap's odometer alignment to the reference lap, as the (knot_ref, knot_lap)
    pair of a monotone piecewise-linear warp — or None when the normalized projection applies
    verbatim (no traces, drift inside NORMALIZED_DRIFT_MAX, or no spatial match survived).

    `frame` is the reference-odometer boundary set the warp is fitted to; the two timing-line
    anchors are added here. See project_boundaries for what the warp is and why it exists.

    BUILD IT ONCE PER LAP when you are projecting many windows of the same lap.
    `coaching.corner_phase_losses` is called per (lap, corner) — deriving the lap's warp inside
    each of those calls cost 106 ms of a 168 ms `Session.phase_report` on the 38-lap D24 0060 pair.
    Hoisting it to once per lap took that to 43.9 ms."""
    total_ref = float(total_ref)
    total_lap = float(total_lap)
    if traces is None or line_length_drift(total_lap, total_ref) <= NORMALIZED_DRIFT_MAX:
        return None
    ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum = traces
    knots = np.asarray(frame, float)
    matched = _spatial_matches(knots, total_ref, ref_xs, ref_ys, ref_cum,
                               lap_xs, lap_ys, lap_cum)
    # The two exact timing-line anchors plus every surviving match, in track order, kept strictly
    # increasing on both axes (a match that crosses its accepted neighbour is a mis-match, and
    # dropping it costs only interpolation).
    knot_ref = [0.0]
    knot_lap = [0.0]
    for i in np.argsort(knots, kind="stable"):
        d, m = float(knots[i]), float(matched[i])
        if not np.isfinite(m) or not (knot_ref[-1] < d < total_ref) \
                or not (knot_lap[-1] < m < total_lap):
            continue
        knot_ref.append(d)
        knot_lap.append(m)
    if len(knot_ref) == 1:
        # Nothing matched anywhere: the two anchors alone ARE the normalized map, so say so and let
        # the caller return it verbatim rather than re-deriving it through np.interp.
        return None
    knot_ref.append(total_ref)
    knot_lap.append(total_lap)
    return np.asarray(knot_ref, float), np.asarray(knot_lap, float)


def project_boundaries(d_ref, total_ref: float, total_lap: float, *,
                       traces: tuple | None = None, frame=None,
                       alignment=DERIVE_ALIGNMENT) -> np.ndarray:
    """Project reference-odometer corner-window boundaries `d_ref` onto a comparison lap's odometer.

    The DRIFT-GATED alignment shared by lap_corner_stats / segment_times / driving / coaching: when
    the laps' line-length drift is within NORMALIZED_DRIFT_MAX (the common, well-matched case) OR no
    spatial `traces` are supplied, this is exactly the legacy normalized projection
    `d_ref · total_lap / total_ref` — byte-identical to the pre-gate output.

    Above the bound the lap is aligned by ONE WARP, not boundary by boundary. The spatial matches
    that pass the gates become the interior KNOTS of a monotone piecewise-linear map, anchored at
    the timing line at both ends (0 → 0 and total_ref → total_lap are the same physical point on
    both laps by definition), and every boundary is read off that map. A boundary whose own match
    fails is therefore INTERPOLATED between its neighbours' matches — it is never left in the
    normalized frame while its neighbour sits in the spatial one.

    WHY (the P0 this replaces): the previous version fell back to the normalized value for THAT
    boundary alone, so a segment with one spatial edge and one fallback edge shrank or stretched by
    the whole local odometer offset — up to 11.4 m on a 42.8 m straight (27 %) on the D24 0060 pair.
    Per-lap tiling still summed exactly (the time moved BETWEEN segments), so nothing downstream
    could see it, and `CornerModel.segment_bests`' per-segment minimum then harvested the shrunken
    windows: the pair's ideal read 62.869 s where the order statistic extrapolated from the 22
    undistorted laps of the same recording predicts 65.226 s — 2.36 s of the 5.36 s headline was
    measurement artifact, not driving. With one frame per lap and the symmetric span admission in
    `corner_model.MAX_DONOR_SPAN_DEV` it reads 65.149 s.

    The warp is strictly increasing by construction (a match that would cross its neighbour is
    dropped, not clamped), so the partition can no longer fold — which is why the old
    `np.maximum.accumulate` repair is gone.

    `traces`, when given, is (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum) — the reference
    (best) lap's and the comparison lap's local-frame trace + odometer (Session feeds these; the
    pure-numpy unit tests omit them and so always get the normalized projection).

    `frame`, when given, is the FULL reference-odometer boundary set the lap's warp is built from,
    so a caller asking about ONE corner (coaching.corner_phase_losses) gets the SAME alignment as a
    caller asking about the whole partition (lap_corner_stats / segment_bests / driving_channels).
    Defaults to `d_ref` itself — which is already the full set at every whole-partition call site.

    `alignment` is that warp, already built (`lap_alignment`) — pass it when projecting many
    windows of the SAME lap so the spatial match runs once for the lap instead of once per window.
    None is a legal value meaning "this lap keeps the normalized projection"."""
    d_ref = np.asarray(d_ref, float)
    total_ref = float(total_ref)
    total_lap = float(total_lap)
    normalized = d_ref * (total_lap / total_ref) if total_ref > 0 else d_ref.copy()
    if alignment is DERIVE_ALIGNMENT:
        alignment = lap_alignment(d_ref if frame is None else frame, total_ref, total_lap,
                                  traces=traces)
    elif alignment is not None and abs(float(alignment[1][-1]) - total_lap) > 1e-6:
        # A warp built for a DIFFERENT lap would otherwise project silently and wrongly — its last
        # anchor is that lap's total. Cheap and exact: lap_alignment ends knot_lap at total_lap.
        raise ValueError(
            f"alignment was built for a lap of total {float(alignment[1][-1])} m, not {total_lap}")
    if alignment is None:
        return normalized
    return np.interp(d_ref, alignment[0], alignment[1])


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
    NORMALIZED_DRIFT_MAX, one monotone spatial warp above; see project_boundaries), and the lap
    end. The lap start/end stay the literal 0 / total_lap — the timing line is the shared S/F point
    on both laps, which is exactly why it is also the warp's two anchors. `traces` (Session-fed)
    enables the spatial alignment."""
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
    (Session-fed) enables the drift-gated spatial alignment; omitted → the normalized projection.

    The sum holding does NOT mean the pieces are comparable across laps: it held throughout the
    projection defect project_boundaries documents, because a shrunken segment pushes its time into
    its neighbour. `corner_model.MAX_DONOR_SPAN_DEV` is where per-piece comparability is checked."""
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
    distance within NORMALIZED_DRIFT_MAX, one monotone spatial warp for the whole lap above it.
    `traces` (Session-fed (ref_xs, ref_ys, ref_cum, lap_xs, lap_ys, lap_cum)) enables the spatial
    alignment; omitted (pure-numpy callers) → normalized, byte-identical to the pre-gate output."""
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
