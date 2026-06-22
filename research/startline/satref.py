"""Georeference gmaps_sat.png to local metres by registering the centerline's normalized
[0,1] track bbox to the satellite's visible-track pixel bbox.

The centerline (mk_centerline.json) was traced from gmaps_pict.png (same 1732x1488 viewport as
gmaps_sat.png) and renormalized to its OWN bbox [0,1]^2. So normalized (0,0)->(1,1) spans the
TRACK bbox in pixels, not the whole image. We recover that track pixel bbox from the satellite,
then: pixel = bbox_min + norm * bbox_size, and norm -> local via the stored ICP transform.

Recovering the track pixel bbox: the kart tarmac is dark grey on green grass. We threshold for
'tarmac-ish' pixels (low saturation, mid-low brightness, not the bright buildings/roads) within
the track region, take a robust bounding box (2..98 pct of tarmac pixel coords), and compare to
the centerline outline. We then refine by matching the centerline outline (mapped to candidate
pixels) against the tarmac mask via a small grid search on (scale, offset).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui
import pyqtgraph as pg  # noqa: F401

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
    sat = (mx - mn)
    bright = mx
    # tarmac: low saturation (grey), mid-dark brightness; exclude near-black and bright white
    return (sat < 32) & (bright > 45) & (bright < 150)


def main():
    rgb, W, H = load_rgb(SAT)
    mask = tarmac_mask(rgb)
    ys, xs = np.where(mask)
    print(f"image {W}x{H}; tarmac-ish pixels: {len(xs)}", flush=True)
    # The track occupies the center-left; the right side has a big building roof + railway that
    # can also be grey. Restrict to the dominant connected blob via a coarse 2-98 pct box first.
    x_lo, x_hi = np.percentile(xs, [2, 98])
    y_lo, y_hi = np.percentile(ys, [2, 98])
    print(f"raw tarmac bbox px: x[{x_lo:.0f},{x_hi:.0f}] y[{y_lo:.0f},{y_hi:.0f}]", flush=True)
    print("NOTE: manual visual refinement recommended; dumping mask preview.", flush=True)

    # Dump a downsampled mask preview as a PNG for inspection.
    prev = np.zeros((H, W, 3), np.uint8)
    prev[..., 1] = (rgb[..., 1] // 2)  # dim green channel of original for context
    prev[mask] = [255, 0, 0]
    out = QtGui.QImage(prev.data, W, H, 3 * W, QtGui.QImage.Format.Format_RGB888)
    out.save(".startline_tmp/tarmac_mask.png")
    print("wrote .startline_tmp/tarmac_mask.png", flush=True)


if __name__ == "__main__":
    main()
