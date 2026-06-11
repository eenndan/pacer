"""Final visual proof: a 2-panel PNG.
 LEFT  : satellite (georeferenced) + full GPS trace (speed-colored) + CURRENT start line (red),
         with a zoom inset of the S/F region by the pit building.
 RIGHT : the official plan crop showing the labelled 'Start Finish' on the main straight by the
         Start Board / pit building, for visual correspondence.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui, QtCore
import pyqtgraph as pg


def draw_sat(trace_json):
    sr = json.load(open(".startline_tmp/satref_direct.json"))
    M = np.array(sr["M"]); c = np.array(sr["c"])

    def llpx(la, lo):
        v = M @ np.array([lo, la]) + c
        return float(v[0]), float(v[1])

    d = json.load(open(trace_json))
    lat = np.array(d["trace"]["lat"]); lon = np.array(d["trace"]["lon"]); v = np.array(d["trace"]["v"])
    img = QtGui.QImage(sr["img"]).convertToFormat(QtGui.QImage.Format.Format_RGB888)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)
    cmap = pg.colormap.get("viridis")
    cols = cmap.map(np.clip((v - 30) / 60, 0, 1), mode="byte")
    p.setPen(QtCore.Qt.PenStyle.NoPen)
    for i in range(len(lat)):
        cc = cols[i]
        p.setBrush(QtGui.QColor(int(cc[0]), int(cc[1]), int(cc[2]), 190))
        px, py = llpx(lat[i], lon[i]); p.drawEllipse(QtCore.QPointF(px, py), 1.8, 1.8)
    a = llpx(52.04031, -0.78487); b = llpx(52.04020, -0.78460)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 20, 20), 6)); p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))
    pg.mkQApp()
    f = p.font(); f.setPointSize(16); f.setBold(True); p.setFont(f)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 255, 255), 1))
    p.drawText(QtCore.QPointF(a[0] - 150, a[1] - 8), "CURRENT S/F (on the pit straight)")
    p.end()
    return img


def main():
    sat = draw_sat(".startline_tmp/trace_0060.json")
    plan = QtGui.QImage(".startline_tmp/plan_pitstraight_2x.png").convertToFormat(
        QtGui.QImage.Format.Format_RGB888)
    # compose side by side, scaled to same height
    Hh = 1300
    sw = int(sat.width() * Hh / sat.height())
    pw = int(plan.width() * Hh / plan.height())
    sat = sat.scaled(sw, Hh, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                     QtCore.Qt.TransformationMode.SmoothTransformation)
    plan = plan.scaled(pw, Hh, QtCore.Qt.AspectRatioMode.KeepAspectRatio,
                       QtCore.Qt.TransformationMode.SmoothTransformation)
    out = QtGui.QImage(sw + pw + 20, Hh, QtGui.QImage.Format.Format_RGB888)
    out.fill(QtGui.QColor(255, 255, 255))
    pp = QtGui.QPainter(out)
    pp.drawImage(0, 0, sat)
    pp.drawImage(sw + 20, 0, plan)
    pg.mkQApp()
    f = pp.font(); f.setPointSize(20); f.setBold(True); pp.setFont(f)
    pp.setPen(QtGui.QColor(0, 0, 0))
    pp.drawText(sw + 30, 40, "Official plan: 'Start Finish' on the main straight by the pits")
    pp.end()
    out.save(".startline_tmp/start_line_proof.png")
    print("wrote .startline_tmp/start_line_proof.png", flush=True)


if __name__ == "__main__":
    main()
