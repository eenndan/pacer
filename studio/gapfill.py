"""Reconstruct missing track geometry across GPS dropouts — MAP RENDERING ONLY.

The studio map draws each lap as a polyline of its kept GPS points. Where a lap has an
interior DROPOUT (a run of samples removed by the quality gate, or a genuine GPS outage),
consecutive kept points are far apart in time, so the polyline draws a STRAIGHT CHORD
across the hole instead of following the track. The track is the SAME on every lap, so the
real corner shape is recoverable from the OTHER laps that drove that section cleanly.

This module is PURE PYTHON + numpy. It knows nothing about `pacer`: it operates on the
per-lap (xs, ys, times) arrays that `session.py` already caches (local metres + media-clock
seconds). It NEVER touches the analysis pipeline — `session.delta`, `lap_sector_splits`,
`cum_distances`, `valid_lap_ids` are all derived from the unchanged kept-point arrays. The
output here is used ONLY to draw measured-vs-inferred segments on the map.

Strategy (in priority order, per the task brief):
  1. PRIMARY — cross-lap borrow: find a donor lap that drove the gap section cleanly, take
     its sub-polyline between the points nearest the gap mouths, and apply a similarity
     transform (rotation + uniform scale, both endpoints pinned) so the borrowed corner
     connects continuously to the measured trace at both ends.
  2. FALLBACK — reference centerline: where NO lap covers the section, pin the relevant span
     of a georeferenced track centerline the same way. (Provided by the caller as another
     "donor" polyline; with ~18 laps this is rarely needed — measured + reported.)
  3. Very short gaps (a couple of samples) are bridged with a Catmull-Rom spline through the
     neighbouring measured points — no borrow needed.

Every reconstructed run is returned tagged so the renderer can draw it dashed/dimmed: the
user must always be able to tell measured GPS from inferred fill.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# A gap is an interior jump where the time delta between consecutive kept points exceeds a
# few sample intervals. At ~10 Hz a normal step is ~0.1 s; >0.35 s (~3+ missing samples) is a
# real dropout, not jitter. The lap's natural open start/finish ends are NOT gaps.
GAP_TIME_S = 0.35
# Below this many missing samples a gap is "short" → spline through neighbours, no borrow.
SHORT_GAP_MISSING = 3
# Donor endpoint match must be within this fraction of the gap's straight-chord length, else
# the donor doesn't really cover the section (reject; try the next donor).
DONOR_ENDPOINT_TOL_FRAC = 0.25
DONOR_ENDPOINT_TOL_MIN_M = 5.0  # …but always allow at least this absolute slack (metres)
# The donor sub-polyline's arc length must be within this ratio of the gap's expected span,
# so we don't grab a wrapped / much-longer donor section (e.g. a near-stationary hairpin).
DONOR_ARC_RATIO_LO, DONOR_ARC_RATIO_HI = 0.4, 3.0


@dataclass
class Segment:
    """An ordered run of points on a lap's drawn trace.

    `xs`/`ys` are local metres. `measured` is True for real kept GPS, False for a
    reconstructed fill. `source` describes an inferred fill ("borrow:lap K" / "reference" /
    "spline") and is "" for measured runs.
    """

    xs: np.ndarray
    ys: np.ndarray
    measured: bool
    source: str = ""


def _arc_length(xs, ys):
    if len(xs) < 2:
        return 0.0
    return float(np.sum(np.hypot(np.diff(xs), np.diff(ys))))


def find_gaps(times, gap_s: float = GAP_TIME_S, med_dt: float | None = None):
    """Interior gaps in a lap's kept-point time sequence.

    Returns a list of dicts: {i, j, dt, n_missing} where i,j are consecutive kept-point
    indices (j == i+1) straddling the dropout, dt is the time hole (s), and n_missing is the
    estimated number of dropped samples. `med_dt` (median sample interval) is used to estimate
    n_missing; if omitted it's taken from the lap's own deltas.
    """
    t = np.asarray(times, float)
    if len(t) < 3:
        return []
    dts = np.diff(t)
    if med_dt is None:
        pos = dts[(dts > 0) & (dts <= gap_s)]
        med_dt = float(np.median(pos)) if len(pos) else 0.1
    med_dt = max(med_dt, 1e-3)
    gaps = []
    for i in np.where(dts > gap_s)[0]:
        gaps.append({
            "i": int(i),
            "j": int(i) + 1,
            "dt": float(dts[i]),
            "n_missing": max(int(round(dts[i] / med_dt)) - 1, 1),
        })
    return gaps


def _similarity_map(src, dst_a, dst_b):
    """Map a source polyline so its endpoints land on dst_a / dst_b via a similarity
    transform (rotation + uniform scale + translation; pins both endpoints exactly).

    `src` is (N,2). Returns the transformed (N,2). If the source endpoints coincide it
    degenerates to a straight line between dst_a and dst_b.
    """
    src = np.asarray(src, float)
    a0, b0 = src[0], src[-1]
    d0 = b0 - a0
    d1 = np.asarray(dst_b, float) - np.asarray(dst_a, float)
    len0 = np.hypot(*d0)
    if len0 < 1e-9:
        # source has no extent — just draw the chord
        t = np.linspace(0.0, 1.0, len(src))[:, None]
        return np.asarray(dst_a, float) + t * d1
    scale = np.hypot(*d1) / len0
    ang = np.arctan2(d1[1], d1[0]) - np.arctan2(d0[1], d0[0])
    c, s = np.cos(ang) * scale, np.sin(ang) * scale
    rot = np.array([[c, -s], [s, c]])
    out = (src - a0) @ rot.T + np.asarray(dst_a, float)
    # Pin the far endpoint exactly (kill any residual float drift so the mouth closes).
    out[-1] = np.asarray(dst_b, float)
    out[0] = np.asarray(dst_a, float)
    return out


def _catmull_rom(p0, p1, p2, p3, n):
    """Centripetal Catmull-Rom points strictly BETWEEN p1 and p2 (n interior points)."""
    p0, p1, p2, p3 = (np.asarray(p, float) for p in (p0, p1, p2, p3))
    ts = np.linspace(0.0, 1.0, n + 2)[1:-1]  # exclude the endpoints (they're measured)
    out = []
    for t in ts:
        t2, t3 = t * t, t * t * t
        out.append(0.5 * ((2 * p1)
                          + (-p0 + p2) * t
                          + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t2
                          + (-p0 + 3 * p1 - 3 * p2 + p3) * t3))
    return np.array(out) if out else np.empty((0, 2))


def _donor_subpath(donor_xy, pa, pb):
    """In a donor polyline, find the sub-path that covers the gap (pa→pb), or None.

    Locates the donor index nearest pa and nearest pb, orients the slice between them to run
    pa→pb, and reports the endpoint match error + the donor arc length so the caller can
    reject donors whose endpoints are too far from the gap mouths or whose arc length is
    inconsistent with the gap span (a wrapped / wildly mismatched section). Returns
    (sub_xy, endpoint_err, donor_arc) or None.
    """
    d = np.asarray(donor_xy, float)
    if len(d) < 3:
        return None
    ia = int(np.argmin((d[:, 0] - pa[0]) ** 2 + (d[:, 1] - pa[1]) ** 2))
    ib = int(np.argmin((d[:, 0] - pb[0]) ** 2 + (d[:, 1] - pb[1]) ** 2))
    if ia == ib:
        return None
    lo, hi = (ia, ib) if ia < ib else (ib, ia)
    sub = d[lo:hi + 1]
    # Orient the sub-path so it runs pa → pb.
    if ia > ib:
        sub = sub[::-1]
    err_a = float(np.hypot(*(sub[0] - pa)))
    err_b = float(np.hypot(*(sub[-1] - pb)))
    endpoint_err = max(err_a, err_b)
    donor_arc = _arc_length(sub[:, 0], sub[:, 1])
    return sub, endpoint_err, donor_arc


def reconstruct_lap(xs, ys, times, donors,
                    gap_s: float = GAP_TIME_S, med_dt: float | None = None):
    """Build the drawable, gap-filled segment list for one lap.

    Parameters
    ----------
    xs, ys, times : arrays of the lap's KEPT points (local metres + media-clock seconds).
    donors : ordered list of candidate fill sources, each a dict
        {"xy": (M,2) array, "name": str, "is_reference": bool}. Cross-lap donors come first;
        the georeferenced reference centerline (if any) comes LAST so borrow is always
        preferred. The lap being reconstructed should NOT be in its own donor list.

    Returns
    -------
    (segments, fills) where `segments` is the ordered list of `Segment` to draw (measured runs
    interleaved with inferred fills) and `fills` is a per-gap report list of dicts for metrics.
    """
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    times = np.asarray(times, float)
    n = len(xs)
    if n < 2:
        return [Segment(xs, ys, True)], []

    gaps = find_gaps(times, gap_s, med_dt)
    if not gaps:
        return [Segment(xs, ys, True)], []

    segments: list[Segment] = []
    fills: list[dict] = []
    cursor = 0  # next un-emitted measured index
    for g in gaps:
        i, j = g["i"], g["j"]
        # Emit the measured run up to and including the gap's "before" point.
        seg_xs = xs[cursor:i + 1]
        seg_ys = ys[cursor:i + 1]
        if len(seg_xs) >= 1:
            segments.append(Segment(seg_xs, seg_ys, True))
        cursor = j  # the "after" point starts the next measured run

        pa = np.array([xs[i], ys[i]])
        pb = np.array([xs[j], ys[j]])
        chord = float(np.hypot(*(pb - pa)))
        report = {"i": i, "j": j, "dt": g["dt"], "n_missing": g["n_missing"],
                  "chord_m": chord, "source": "unfilled", "fill_m": 0.0,
                  "endpoint_err_m": 0.0}

        fill_xy = None
        # --- short gap: spline through neighbours (no borrow needed) ---
        if g["n_missing"] <= SHORT_GAP_MISSING:
            p0 = np.array([xs[max(i - 1, 0)], ys[max(i - 1, 0)]])
            p3 = np.array([xs[min(j + 1, n - 1)], ys[min(j + 1, n - 1)]])
            inner = _catmull_rom(p0, pa, pb, p3, g["n_missing"])
            fill_xy = np.vstack([pa, inner, pb]) if len(inner) else np.vstack([pa, pb])
            report["source"] = "spline"
            report["endpoint_err_m"] = 0.0
        else:
            # --- cross-lap borrow (PRIMARY), reference centerline (FALLBACK only) ---
            # Borrow is always preferred: pick the best REAL-LAP donor; only if none qualifies
            # do we consider the reference. So a reference fill happens iff no lap covers the
            # section — exactly the priority the task brief requires.
            tol = max(DONOR_ENDPOINT_TOL_FRAC * chord, DONOR_ENDPOINT_TOL_MIN_M)
            best_lap = None  # (endpoint_err, sub_xy, name)
            best_ref = None  # (endpoint_err, sub_xy, name)
            for dn in donors:
                got = _donor_subpath(dn["xy"], pa, pb)
                if got is None:
                    continue
                sub, endpoint_err, donor_arc = got
                if endpoint_err > tol:
                    continue
                # Arc-length sanity vs the chord (donor must span a comparable distance).
                ratio = donor_arc / max(chord, 1e-6)
                if not (DONOR_ARC_RATIO_LO <= ratio <= DONOR_ARC_RATIO_HI):
                    continue
                cand = (endpoint_err, sub, dn["name"])
                if dn.get("is_reference", False):
                    if best_ref is None or endpoint_err < best_ref[0] - 1e-9:
                        best_ref = cand
                else:
                    if best_lap is None or endpoint_err < best_lap[0] - 1e-9:
                        best_lap = cand
            best = best_lap if best_lap is not None else best_ref
            if best is not None:
                err, sub, name = best
                is_ref = best_lap is None
                mapped = _similarity_map(sub, pa, pb)
                fill_xy = mapped
                report["source"] = ("reference" if is_ref else f"borrow:{name}")
                report["endpoint_err_m"] = float(err)
                report["fill_m"] = _arc_length(mapped[:, 0], mapped[:, 1])
            else:
                # No donor covers this section. Don't draw a bare chord: bridge with a
                # Catmull-Rom spline through the neighbouring measured points — at least it
                # curves naturally out of/into the trace instead of cutting straight across.
                # Tagged "spline-fallback" (still inferred → dashed) and reported as
                # not-borrow-covered so the metrics show how often borrow actually missed.
                p0 = np.array([xs[max(i - 1, 0)], ys[max(i - 1, 0)]])
                p3 = np.array([xs[min(j + 1, n - 1)], ys[min(j + 1, n - 1)]])
                inner = _catmull_rom(p0, pa, pb, p3, max(g["n_missing"], 1))
                fill_xy = np.vstack([pa, inner, pb]) if len(inner) else np.vstack([pa, pb])
                report["source"] = "spline-fallback"

        if fill_xy is not None and len(fill_xy) >= 2:
            is_measured = False
            segments.append(Segment(fill_xy[:, 0], fill_xy[:, 1], is_measured,
                                    source=report["source"]))
            if report["fill_m"] == 0.0:
                report["fill_m"] = _arc_length(fill_xy[:, 0], fill_xy[:, 1])
        fills.append(report)

    # Trailing measured run after the last gap.
    if cursor < n:
        segments.append(Segment(xs[cursor:], ys[cursor:], True))
    return segments, fills
