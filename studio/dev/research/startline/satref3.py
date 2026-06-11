"""Directly georeference gmaps_sat.png to lat/lon using the GPS TRACE (which is already correct
in lat/lon) as the shape template — NOT the broken stored centerline.

Method: take the GPS trace in lat/lon, build an affine  pixel = M @ [lon,lat] + c  by an
ICP-style fit between the trace points (mapped to candidate pixels) and the tarmac-mask pixels
of the satellite, seeded from a bbox match (track region only). Lon/lat -> pixel is locally
affine over this ~300 m extent. We solve the 6-param affine by alternating:
  1. for the current affine, assign each trace point its nearest tarmac pixel
  2. least-squares refit the affine [lon,lat,1] -> [px,py]
Seed: map trace lon/lat bbox onto the track tarmac bbox (orientation from the known layout:
North=up=smaller py; East=right=larger px). Output the affine + its inverse.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui

SAT = "/Users/daniil/Desktop/Tracks/MK/gmaps_sat.png"


def load_rgb(path):
    img = QtGui.QImage(path).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    h, w = img.height(), img.width()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape((h, w, 4)).copy()
    return arr[:, :, :3], w, h


def tarmac_mask(rgb):
    r, g, b = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
    mx = np.maximum(np.maximum(r, g), b)
    mn = np.minimum(np.minimum(r, g), b)
    return (mx - mn < 32) & (mx > 45) & (mx < 150)


def main(trace_json):
    rgb, W, H = load_rgb(SAT)
    mask = tarmac_mask(rgb)
    region = np.zeros_like(mask)
    region[int(0.02 * H):int(0.99 * H), int(0.03 * W):int(0.80 * W)] = True
    m = mask & region
    ys, xs = np.where(m)
    tar = np.column_stack([xs, ys]).astype(float)  # (K,2) px

    d = json.load(open(trace_json))
    lat = np.array(d["trace"]["lat"])
    lon = np.array(d["trace"]["lon"])
    # subsample the trace for speed
    step = max(1, len(lat) // 1500)
    lat, lon = lat[::step], lon[::step]
    P = np.column_stack([lon, lat])  # (N,2)

    # seed affine: lon-> px (East=+x), lat-> py (North=-y). Use track tarmac bbox.
    px_lo, px_hi = np.percentile(xs, [1, 99])
    py_lo, py_hi = np.percentile(ys, [1, 99])
    lon_lo, lon_hi = lon.min(), lon.max()
    lat_lo, lat_hi = lat.min(), lat.max()
    a = (px_hi - px_lo) / (lon_hi - lon_lo)        # px per deg lon
    e = -(py_hi - py_lo) / (lat_hi - lat_lo)       # py per deg lat (north up => negative)
    M = np.array([[a, 0.0], [0.0, e]])
    c = np.array([px_lo - a * lon_lo, py_hi - e * lat_lo])

    # tarmac KD-ish nearest via chunked argmin (K up to ~1e6, N ~1500 -> fine)
    def nearest(pts):
        out = np.empty_like(pts)
        for i in range(0, len(pts), 200):
            chunk = pts[i:i + 200]
            d2 = ((chunk[:, None, 0] - tar[None, :, 0]) ** 2
                  + (chunk[:, None, 1] - tar[None, :, 1]) ** 2)
            out[i:i + 200] = tar[np.argmin(d2, axis=1)]
        return out

    A = np.column_stack([P, np.ones(len(P))])  # (N,3) [lon,lat,1]
    for it in range(12):
        proj = (M @ P.T).T + c
        nn = nearest(proj)
        # least squares: A @ [Mx;cx] = nn  -> solve 3x2
        sol, *_ = np.linalg.lstsq(A, nn, rcond=None)  # (3,2)
        M = sol[:2].T
        c = sol[2]
        err = np.sqrt(((proj - nn) ** 2).sum(1))
        if it % 3 == 0 or it == 11:
            print(f"iter {it}: mean px err to tarmac = {err.mean():.1f}px "
                  f"median={np.median(err):.1f}px", flush=True)

    # full affine lonlat->px: px = M@[lon,lat]+c ; store M (2x2), c (2)
    out = {"img": SAT, "W": W, "H": H, "M": M.tolist(), "c": c.tolist(),
           "trace_json": trace_json}
    with open(".startline_tmp/satref_direct.json", "w") as f:
        json.dump(out, f)
    print(f"M={M.tolist()} c={c.tolist()}", flush=True)
    print("wrote .startline_tmp/satref_direct.json", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".startline_tmp/trace_0060.json")
