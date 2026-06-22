"""Georeference the plan PDF render to lat/lon by ICP-fitting the plan's TRACK pixels to the
GPS trace (lat/lon), then map the plan's labelled 'Start Finish' RED marker to lat/lon.

Steps:
  1. Detect the plan's track tarmac (dark near-black band) -> a point cloud of track pixels.
  2. Detect the small RED 'Start Finish' marker (saturated red, isolated, near the main straight)
     -> its pixel.  (The plan also has red flag markers + the title arrow; we restrict the search
     region to the main-straight area we can see and pick the marker nearest the 'Start Finish'
     text location.)
  3. ICP-fit an affine  latlon -> plan_pixel  using the trace (mapped through a seed) against the
     plan track pixels. Equivalent: fit plan_pixel -> latlon. We fit affine T: plan_px -> [lon,lat]
     by ICP between plan track pixels (subsampled) and trace points... but correspondence is
     shape-only, so we ICP plan_px (transformed) onto the trace point cloud.
  4. Apply T to the red marker -> its lat/lon. Report it + distance to the current line.

The plan is a DIFFERENT projection (stylized, possibly rotated/sheared vs north-up), so we allow
a full affine (6 dof) in the ICP refit.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui

PLAN = "/Users/daniil/Documents/Github/pacer/.startline_tmp/Link-Cliff-Plan.pdf.png"


def load_rgb(path):
    img = QtGui.QImage(path).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    h, w = img.height(), img.width()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape((h, w, 4)).copy()
    return arr[:, :, :3], w, h


def main(trace_json):
    rgb, W, H = load_rgb(PLAN)
    r = rgb[..., 0].astype(int)
    g = rgb[..., 1].astype(int)
    b = rgb[..., 2].astype(int)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)

    # Track tarmac: near-black band (the asphalt), excluding the dark title bar at the very top.
    track = (mx < 70) & (mn < 60)
    track[: int(0.16 * H)] = False  # drop the black title banner
    track[int(0.93 * H):] = False
    ys, xs = np.where(track)
    print(f"plan track pixels: {len(xs)} (img {W}x{H})", flush=True)

    # Red 'Start Finish' marker: saturated red. Restrict to the main-straight region (center-left,
    # below the grid). From the read crops the marker is ~ x in [0.40,0.52]W, y in [0.42,0.55]H.
    red = (r > 150) & (g < 90) & (b < 90)
    rr = np.zeros_like(red)
    rr[int(0.40 * H):int(0.58 * H), int(0.38 * W):int(0.55 * W)] = True
    red_m = red & rr
    rys, rxs = np.where(red_m)
    if len(rxs) == 0:
        print("no red S/F marker found in expected region; widening...", flush=True)
        rr[:] = False
        rr[int(0.35 * H):int(0.62 * H), int(0.30 * W):int(0.60 * W)] = True
        red_m = red & rr
        rys, rxs = np.where(red_m)
    sf_px = np.array([rxs.mean(), rys.mean()])
    print(f"plan S/F red marker px = ({sf_px[0]:.1f},{sf_px[1]:.1f}) "
          f"(from {len(rxs)} red pixels)", flush=True)

    # trace lat/lon point cloud
    d = json.load(open(trace_json))
    lat = np.array(d["trace"]["lat"])
    lon = np.array(d["trace"]["lon"])
    step = max(1, len(lat) // 1500)
    T = np.column_stack([lon[::step], lat[::step]])  # (N,2) [lon,lat]

    # plan track point cloud, subsampled
    pstep = max(1, len(xs) // 2500)
    Pp = np.column_stack([xs[::pstep], ys[::pstep]]).astype(float)  # (Kp,2) plan px

    # seed affine plan_px -> [lon,lat] by bbox match. Plan orientation vs north is unknown; we try
    # a few rotations/reflections and keep the ICP with lowest residual.
    def bbox_seed(flip_x, flip_y, swap):
        px = Pp.copy()
        if swap:
            px = px[:, ::-1]
        plo, phi = px.min(0), px.max(0)
        psz = (phi - plo)
        u = (px - plo) / psz  # [0,1]^2
        if flip_x:
            u[:, 0] = 1 - u[:, 0]
        if flip_y:
            u[:, 1] = 1 - u[:, 1]
        tlo, thi = T.min(0), T.max(0)
        seeded = tlo + u * (thi - tlo)
        return seeded, px

    best = None
    for flip_x in (False, True):
        for flip_y in (False, True):
            for swap in (False, True):
                seeded, px = bbox_seed(flip_x, flip_y, swap)
                A = np.column_stack([px, np.ones(len(px))])
                # init affine px->lonlat by lstsq seeded mapping
                sol, *_ = np.linalg.lstsq(A, seeded, rcond=None)
                for _ in range(15):
                    proj = A @ sol
                    # nearest trace point for each plan point
                    d2 = ((proj[:, None, 0] - T[None, :, 0]) ** 2
                          + (proj[:, None, 1] - T[None, :, 1]) ** 2)
                    nn = T[np.argmin(d2, axis=1)]
                    sol, *_ = np.linalg.lstsq(A, nn, rcond=None)
                proj = A @ sol
                d2 = ((proj[:, None, 0] - T[None, :, 0]) ** 2
                      + (proj[:, None, 1] - T[None, :, 1]) ** 2)
                resid = np.sqrt(d2.min(1)).mean()
                if best is None or resid < best[0]:
                    best = (resid, sol, (flip_x, flip_y, swap))
    resid, sol, cfg = best
    print(f"best plan->latlon ICP residual (deg) = {resid:.6e}  cfg(flipx,flipy,swap)={cfg}",
          flush=True)
    # convert residual to metres approx
    import math
    mdeg_lat = 111320.0
    mdeg_lon = 111320.0 * math.cos(math.radians(lat.mean()))
    resid_m = resid * math.hypot(mdeg_lat, mdeg_lon) / math.sqrt(2)
    print(f"  ~ {resid_m:.1f} m mean residual", flush=True)

    # apply to S/F marker (account for swap)
    sf = sf_px[::-1] if cfg[2] else sf_px
    sf_lonlat = np.array([sf[0], sf[1], 1.0]) @ sol
    sf_lon, sf_lat = float(sf_lonlat[0]), float(sf_lonlat[1])
    print(f"\nPLAN S/F -> lat/lon = ({sf_lat:.6f}, {sf_lon:.6f})", flush=True)

    # distance to current line midpoint
    cur_mid = ((52.04031 + 52.04020) / 2, (-0.78487 - 0.78460) / 2)
    dlat = (sf_lat - cur_mid[0]) * mdeg_lat
    dlon = (sf_lon - cur_mid[1]) * mdeg_lon
    print(f"current line midpoint = ({cur_mid[0]:.6f},{cur_mid[1]:.6f})", flush=True)
    print(f"PLAN S/F is {math.hypot(dlat, dlon):.1f} m from the current line midpoint "
          f"(dN={dlat:+.1f}m dE={dlon:+.1f}m)", flush=True)

    out = {"plan_sf_latlon": [sf_lat, sf_lon], "plan_sf_px": sf_px.tolist(),
           "icp_residual_m": resid_m, "affine_px_to_lonlat": sol.tolist(), "cfg": cfg}
    with open(".startline_tmp/plan_sf.json", "w") as f:
        json.dump(out, f)
    print("wrote .startline_tmp/plan_sf.json", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".startline_tmp/trace_0060.json")
