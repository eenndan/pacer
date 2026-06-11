"""Georeference the satellite/map image to lat/lon using the reference centerline ICP fit.

gmaps_sat.png and gmaps_pict.png share the SAME 1732x1488 viewport. mk_centerline.json was
traced from gmaps_pict.png in NORMALIZED image coords (x in [0,1] = px/1732, y in [0,1] =
py/1488, y-down). reference.centerline_local ICP-fits that normalized polyline onto the GPS
aggregate in LOCAL metres. We replicate that fit here to obtain the similarity transform
  T: (normalized image xy, y-down)  ->  local metres
and its inverse, then compose with the coordinate system to get image<->lat/lon.

We dump the transform params so the overlay renderer (and the S/F mapping) can use them.
"""
import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

import pacer
from studio import reference


def fit_transform(agg_xy):
    """Return (scale, R(2x2), t(2,)) mapping the *resampled normalized centerline* `ref` onto
    local metres, replicating reference.centerline_local's ICP exactly, PLUS the resampled ref
    polyline used (so we can map arbitrary normalized points through the same similarity)."""
    norm = reference._load_normalized()
    ref = reference._resample(norm, 600)
    agg = np.asarray(agg_xy, float)

    a_c, r_c = agg.mean(0), ref.mean(0)
    a_span = np.hypot(*(agg.max(0) - agg.min(0)))
    r_span = np.hypot(*(ref.max(0) - ref.min(0))) or 1.0
    s0 = a_span / r_span
    cur = (ref - r_c) * s0 + a_c

    scale = R = t = None
    for _ in range(40):
        d2 = ((cur[:, None, 0] - agg[None, :, 0]) ** 2
              + (cur[:, None, 1] - agg[None, :, 1]) ** 2)
        nn = agg[np.argmin(d2, axis=1)]
        scale, R, t = reference._similarity_fit(ref, nn, allow_reflection=True)
        new = (scale * (ref @ R.T)) + t
        if np.max(np.hypot(*(new - cur).T)) < 1e-3:
            cur = new
            break
        cur = new
    return scale, R, t, ref


def norm_to_local(pts_norm, scale, R, t):
    """Map normalized image points (N,2) through the fitted similarity to local metres."""
    pts = np.asarray(pts_norm, float)
    return (scale * (pts @ R.T)) + t


def main(trace_json, out):
    d = json.load(open(trace_json))
    clat, clon = d["cs_origin"]
    x = np.array(d["trace"]["x"])
    y = np.array(d["trace"]["y"])
    agg = np.column_stack([x, y])
    scale, R, t, ref = fit_transform(agg)

    cs = pacer.CoordinateSystem(pacer.GPSSample(lat=clat, lon=clon, altitude=0))

    # Map the fitted reference centerline to lat/lon (for the overlay + a residual check).
    ref_local = norm_to_local(ref, scale, R, t)
    ref_ll = []
    for px, py in ref_local:
        g = cs.global_(pacer.Vec3f(float(px), float(py), 0.0))
        ref_ll.append([g.lat, g.lon])

    # Fit residual: mean nearest-distance of fitted centerline to the trace (sanity of the georef)
    d2 = ((ref_local[:, None, 0] - x[None, :]) ** 2 + (ref_local[:, None, 1] - y[None, :]) ** 2)
    nn_d = np.sqrt(d2.min(axis=1))
    print(f"georef fit residual: mean={nn_d.mean():.2f}m median={np.median(nn_d):.2f}m "
          f"p90={np.percentile(nn_d,90):.2f}m max={nn_d.max():.2f}m", flush=True)

    out_d = {
        "trace_json": trace_json,
        "cs_origin": [clat, clon],
        "scale": float(scale),
        "R": R.tolist(),
        "t": t.tolist(),
        "ref_norm_resampled": ref.tolist(),
        "ref_centerline_latlon": ref_ll,
        "fit_residual_mean_m": float(nn_d.mean()),
    }
    with open(out, "w") as f:
        json.dump(out_d, f)
    print(f"wrote {out}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".startline_tmp/trace_0060.json",
         sys.argv[2] if len(sys.argv) > 2 else ".startline_tmp/georef_0060.json")
