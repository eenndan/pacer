"""Annotated satellite: trace (speed) + current S/F (red) + direction arrows + lap-fraction
labels, to map the plan's S/F (main straight between corner 11 and corner 1, by the Start Board)
onto the real track. Also draws the GPS lap's start heading.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui, QtCore
import pyqtgraph as pg

import pacer
from studio import chapters
from studio.session import Session


def main(recording, out_png):
    pg.mkQApp()
    sr = json.load(open(".startline_tmp/satref_direct.json"))
    M = np.array(sr["M"])
    c = np.array(sr["c"])

    def ll_to_px(la, lo):
        v = M @ np.array([lo, la]) + c
        return float(v[0]), float(v[1])

    paths = chapters.discover_siblings(recording)
    sess = Session.load(paths)
    cs = sess.cs
    best = sess.best_lap_id()
    lap = sess._get_lap(best)
    blat = np.array([p.point.lat for p in lap.points])
    blon = np.array([p.point.lon for p in lap.points])
    bspd = np.array([p.point.full_speed * 3.6 for p in lap.points])
    n = len(blat)

    img = QtGui.QImage(sr["img"]).convertToFormat(QtGui.QImage.Format.Format_RGB888)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    # best-lap trace, speed-colored, as a polyline
    cmap = pg.colormap.get("viridis")
    vmin, vmax = 30.0, 90.0
    for i in range(n - 1):
        a = ll_to_px(blat[i], blon[i])
        b = ll_to_px(blat[i + 1], blon[i + 1])
        nv = np.clip((bspd[i] - vmin) / (vmax - vmin), 0, 1)
        cc = cmap.map(nv, mode="byte")
        p.setPen(QtGui.QPen(QtGui.QColor(int(cc[0]), int(cc[1]), int(cc[2]), 230), 4))
        p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))

    # direction arrows every ~12% of the lap
    p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 2))
    for f in np.linspace(0.04, 0.96, 9):
        i = int(f * (n - 2))
        a = np.array(ll_to_px(blat[i], blon[i]))
        b = np.array(ll_to_px(blat[i + 1], blon[i + 1]))
        dirv = b - a
        nrm = np.hypot(*dirv) or 1
        dirv = dirv / nrm
        tip = a + dirv * 14
        left = tip - dirv * 8 + np.array([-dirv[1], dirv[0]]) * 5
        right = tip - dirv * 8 - np.array([-dirv[1], dirv[0]]) * 5
        p.drawLine(QtCore.QPointF(*tip), QtCore.QPointF(*left))
        p.drawLine(QtCore.QPointF(*tip), QtCore.QPointF(*right))

    # lap-fraction text labels
    p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 0), 1))
    f = p.font(); f.setPointSize(11); f.setBold(True); p.setFont(f)
    for frac in (0.0, 0.25, 0.5, 0.75):
        i = int(frac * (n - 1))
        px, py = ll_to_px(blat[i], blon[i])
        p.drawText(QtCore.QPointF(px + 5, py - 5), f"{frac:.2f}")

    # current S/F line (red, thick)
    a = ll_to_px(52.04031, -0.78487)
    b = ll_to_px(52.04020, -0.78460)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 30, 30), 6))
    p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))
    p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1))
    p.drawText(QtCore.QPointF((a[0] + b[0]) / 2 - 60, (a[1] + b[1]) / 2 + 4), "CURRENT S/F")

    p.end()
    img.save(out_png)
    print(f"best lap {best} n={n}; wrote {out_png}", flush=True)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "~/Desktop/D24/GX010060.MP4",
         sys.argv[2] if len(sys.argv) > 2 else ".startline_tmp/sat_annotated.png")
