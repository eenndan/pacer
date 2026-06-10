"""Georeferenced track centerline — the FALLBACK gap-fill source.

Used only where NO measured lap covers a gap section (rare with ~18 laps). The Daytona
Milton Keynes reference centerline is traced once from a track image (no embedded geo) and
best-fit aligned to the aggregate GPS point cloud via a similarity transform. See
`build_reference.py` for how the stored polyline was produced.

This module is PURE PYTHON + numpy + a stored polyline; it has no `pacer` dependency for the
fill itself. `centerline_local` takes the GPS aggregate (local-metre points) and returns the
centerline in LOCAL metres, aligned to the data, as an (M,2) array (or empty).
"""

from __future__ import annotations

import json
import os

import numpy as np

_HERE = os.path.dirname(__file__)
_DATA = os.path.join(_HERE, "mk_centerline.json")


def _load_normalized():
    """The stored centerline as a normalized (M,2) polyline, or None if no data file."""
    if not os.path.exists(_DATA):
        return None
    with open(_DATA) as fh:
        d = json.load(fh)
    pts = np.asarray(d.get("points_norm", []), float)
    if pts.ndim != 2 or pts.shape[0] < 3 or pts.shape[1] != 2:
        return None
    return pts


def _similarity_fit(src, dst):
    """Best-fit similarity (rotation+uniform scale+translation, reflection allowed) mapping
    `src` onto `dst` (both (K,2), point-correspondence assumed). Umeyama closed form."""
    src = np.asarray(src, float)
    dst = np.asarray(dst, float)
    mu_s, mu_d = src.mean(0), dst.mean(0)
    xs, xd = src - mu_s, dst - mu_d
    cov = xd.T @ xs / len(src)
    u, s, vt = np.linalg.svd(cov)
    S = np.eye(2)
    R = u @ S @ vt
    var_s = (xs ** 2).sum() / len(src)
    scale = (s * np.diag(S)).sum() / var_s if var_s > 0 else 1.0
    t = mu_d - scale * R @ mu_s
    return scale, R, t


def _resample(xy, n=400):
    xy = np.asarray(xy, float)
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xy[:, 0]), np.diff(xy[:, 1])))])
    if d[-1] <= 0:
        return xy
    s = d / d[-1]
    g = np.linspace(0, 1, n)
    return np.column_stack([np.interp(g, s, xy[:, 0]), np.interp(g, s, xy[:, 1])])


def centerline_local(aggregate_xy):
    """Return the reference centerline in LOCAL metres, best-fit aligned to `aggregate_xy`
    (the union of all laps' local-metre points), as an (M,2) array — empty if unavailable.

    The stored polyline is normalized/arbitrary-scaled; we align it to the GPS aggregate by an
    ICP-style similarity fit. A coarse rough alignment (centroid + bbox scale) seeds a few
    nearest-point similarity refinement iterations (allowing reflection — image axes may be
    flipped vs the local-metre frame).
    """
    norm = _load_normalized()
    if norm is None or aggregate_xy is None or len(aggregate_xy) < 10:
        return np.empty((0, 2))
    agg = np.asarray(aggregate_xy, float)
    ref = _resample(norm, 600)

    # Rough seed: match centroids and bbox scale (the cloud is roughly the track footprint).
    a_c, r_c = agg.mean(0), ref.mean(0)
    a_span = np.hypot(*(agg.max(0) - agg.min(0)))
    r_span = np.hypot(*(ref.max(0) - ref.min(0))) or 1.0
    s0 = a_span / r_span
    cur = (ref - r_c) * s0 + a_c

    # ICP: assign each centerline point its nearest aggregate point, refit similarity, iterate.
    for _ in range(40):
        # nearest aggregate point for each centerline point
        d2 = ((cur[:, None, 0] - agg[None, :, 0]) ** 2
              + (cur[:, None, 1] - agg[None, :, 1]) ** 2)
        nn = agg[np.argmin(d2, axis=1)]
        scale, R, t = _similarity_fit(ref, nn)
        new = (scale * (ref @ R.T)) + t
        if np.max(np.hypot(*(new - cur).T)) < 1e-3:
            cur = new
            break
        cur = new
    return cur
