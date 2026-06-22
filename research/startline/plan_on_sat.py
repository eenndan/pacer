"""Map the plan's track outline (via its ICP affine plan_px->lonlat) onto the satellite (via
satref_direct lonlat->sat_px) to confirm the plan<->real-track correspondence and where the
plan's main straight (hence S/F) lands relative to the CURRENT line.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui, QtCore

PLAN = "~/Documents/Github/pacer/.startline_tmp/Link-Cliff-Plan.pdf.png"


def main():
    # plan affine px->lonlat
    pf = json.load(open(".startline_tmp/plan_sf.json"))
    sol = np.array(pf["affine_px_to_lonlat"])  # (3,2): [lon,lat] = [px,py,1]@sol
    cfg = pf["cfg"]  # (flipx,flipy,swap)
    # satellite affine lonlat->sat_px
    sr = json.load(open(".startline_tmp/satref_direct.json"))
    M = np.array(sr["M"])
    c = np.array(sr["c"])

    # plan track pixels
    img = QtGui.QImage(PLAN).convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    W, H = img.width(), img.height()
    arr = np.frombuffer(img.constBits(), np.uint8).reshape((H, W, 4)).copy()
    r = arr[..., 0].astype(int); g = arr[..., 1].astype(int); b = arr[..., 2].astype(int)
    mx = np.maximum(np.maximum(r, g), b); mn = np.minimum(np.minimum(r, g), b)
    track = (mx < 70) & (mn < 60)
    track[: int(0.16 * H)] = False
    track[int(0.93 * H):] = False
    ys, xs = np.where(track)
    step = max(1, len(xs) // 6000)
    px = np.column_stack([xs[::step], ys[::step]]).astype(float)
    if cfg[2]:
        px = px[:, ::-1]
    A = np.column_stack([px, np.ones(len(px))])
    lonlat = A @ sol  # (N,2) [lon,lat]

    # map to sat px
    satpx = (M @ lonlat.T).T + c

    sat = QtGui.QImage(sr["img"]).convertToFormat(QtGui.QImage.Format.Format_RGB888)
    p = QtGui.QPainter(sat)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    # plan track (magenta dots)
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.setBrush(QtGui.QColor(255, 0, 255, 110))
    for x, y in satpx:
        p.drawEllipse(QtCore.QPointF(x, y), 2.0, 2.0)
    # current line (red)
    def ll_px(la, lo):
        v = M @ np.array([lo, la]) + c
        return float(v[0]), float(v[1])
    a = ll_px(52.04031, -0.78487); bb = ll_px(52.04020, -0.78460)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 30, 30), 6))
    p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*bb))
    # plan S/F marker (from plan_sf.json) in yellow
    sflat, sflon = pf["plan_sf_latlon"]
    sfp = ll_px(sflat, sflon)
    p.setBrush(QtGui.QColor(255, 255, 0))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.drawEllipse(QtCore.QPointF(*sfp), 9, 9)
    p.end()
    sat.save(".startline_tmp/plan_on_sat.png")
    print("wrote .startline_tmp/plan_on_sat.png; plan S/F sat_px=", sfp, flush=True)


if __name__ == "__main__":
    main()
