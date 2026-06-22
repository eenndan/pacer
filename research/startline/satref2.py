"""Register the centerline normalized [0,1] track bbox to the satellite track pixel bbox by a
grid search that maximizes overlap of the centerline OUTLINE (dilated) with the tarmac mask,
restricted to the track region of the image (exclude the right-side buildings/railway and the
far-left A5). Then dump the pixel<->local affine and the satellite track bbox.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui

from studio import reference

SAT = "~/Desktop/Tracks/MK/gmaps_sat.png"


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


def main():
    rgb, W, H = load_rgb(SAT)
    mask = tarmac_mask(rgb)
    # Restrict to the track region: from the mask preview the kart track sits left of the big
    # building roof / railway (right ~ x>0.78W) and right of the A5 (left strip x<0.04W). The
    # paddock tarmac (cadet track + parking, top-left) is above y<0.05H. Keep a generous window.
    region = np.zeros_like(mask)
    x0, x1 = int(0.03 * W), int(0.80 * W)
    y0, y1 = int(0.02 * H), int(0.99 * H)
    region[y0:y1, x0:x1] = True
    m = mask & region

    ref = reference._resample(reference._load_normalized(), 600)  # normalized [0,1], y-down
    # candidate pixel placement: pixel = origin + norm * size (size in px). Grid search.
    ys_m, xs_m = np.where(m)
    # seed from the masked bbox
    px_lo, px_hi = np.percentile(xs_m, [1, 99])
    py_lo, py_hi = np.percentile(ys_m, [1, 99])
    best = None
    for sx_f in np.linspace(0.80, 1.05, 9):
        for sy_f in np.linspace(0.80, 1.05, 9):
            for ox_f in np.linspace(-0.06, 0.06, 7):
                for oy_f in np.linspace(-0.06, 0.06, 7):
                    sx = (px_hi - px_lo) * sx_f
                    sy = (py_hi - py_lo) * sy_f
                    ox = px_lo + ox_f * (px_hi - px_lo)
                    oy = py_lo + oy_f * (py_hi - py_lo)
                    px = (ox + ref[:, 0] * sx).astype(int)
                    py = (oy + ref[:, 1] * sy).astype(int)
                    ok = (px >= 0) & (px < W) & (py >= 0) & (py < H)
                    if ok.sum() < len(ref) * 0.9:
                        continue
                    score = m[py[ok], px[ok]].mean()
                    if best is None or score > best[0]:
                        best = (score, ox, oy, sx, sy)
    score, ox, oy, sx, sy = best
    print(f"best outline-on-tarmac overlap = {score:.3f}", flush=True)
    print(f"satellite track pixel bbox: origin=({ox:.1f},{oy:.1f}) size=({sx:.1f},{sy:.1f}) "
          f"=> x[{ox:.0f},{ox+sx:.0f}] y[{oy:.0f},{oy+sy:.0f}]", flush=True)
    out = {"img": SAT, "W": W, "H": H, "track_px_origin": [ox, oy],
           "track_px_size": [sx, sy], "overlap_score": float(score)}
    with open(".startline_tmp/satref.json", "w") as f:
        json.dump(out, f)
    print("wrote .startline_tmp/satref.json", flush=True)


if __name__ == "__main__":
    main()
