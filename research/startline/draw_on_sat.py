"""Draw the GPS trace (speed-colored) + current S/F line + candidate lines on the satellite,
using the DIRECT lon/lat->pixel affine (satref_direct.json). Save PNG. Candidates as argv json.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui, QtCore
import pyqtgraph as pg


def main(trace_json, out_png, candidates=None, crop=None):
    sr = json.load(open(".startline_tmp/satref_direct.json"))
    M = np.array(sr["M"])
    c = np.array(sr["c"])

    def ll_to_px(la, lo):
        v = M @ np.array([lo, la]) + c
        return float(v[0]), float(v[1])

    d = json.load(open(trace_json))
    lat = np.array(d["trace"]["lat"])
    lon = np.array(d["trace"]["lon"])
    v = np.array(d["trace"]["v"])

    img = QtGui.QImage(sr["img"]).convertToFormat(QtGui.QImage.Format.Format_RGB888)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    # trace, speed-colored
    cmap = pg.colormap.get("viridis")
    vmin, vmax = np.percentile(v, 5), np.percentile(v, 95)
    nv = np.clip((v - vmin) / max(vmax - vmin, 1e-6), 0, 1)
    cols = cmap.map(nv, mode="byte")
    dot = float(os.environ.get("DOT", "2.0"))
    for i in range(0, len(lat), 1):
        px, py = ll_to_px(lat[i], lon[i])
        cc = cols[i]
        p.setPen(QtCore.Qt.PenStyle.NoPen)
        p.setBrush(QtGui.QColor(int(cc[0]), int(cc[1]), int(cc[2]), 200))
        p.drawEllipse(QtCore.QPointF(px, py), dot, dot)

    # current S/F (red, thick) + endpoints
    a = ll_to_px(52.04031, -0.78487)
    b = ll_to_px(52.04020, -0.78460)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 30, 30), 6))
    p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))
    p.setBrush(QtGui.QColor(255, 30, 30))
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    p.drawEllipse(QtCore.QPointF(*a), 6, 6)
    p.drawEllipse(QtCore.QPointF(*b), 6, 6)

    # candidates (lime)
    for (la1, lo1), (la2, lo2) in (candidates or []):
        c1 = ll_to_px(la1, lo1)
        c2 = ll_to_px(la2, lo2)
        p.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0), 6))
        p.drawLine(QtCore.QPointF(*c1), QtCore.QPointF(*c2))
    p.end()

    if crop:  # crop=[x0,y0,x1,y1]
        img = img.copy(crop[0], crop[1], crop[2] - crop[0], crop[3] - crop[1])
    img.save(out_png)
    print(f"current S/F px A={a} B={b}", flush=True)
    print(f"wrote {out_png}", flush=True)


if __name__ == "__main__":
    cand = json.loads(sys.argv[3]) if len(sys.argv) > 3 else None
    crop = json.loads(sys.argv[4]) if len(sys.argv) > 4 else None
    main(sys.argv[1], sys.argv[2], cand, crop)
