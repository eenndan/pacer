"""Draw the centerline (mapped through satref pixel placement) + the current/candidate S/F lines
ON the satellite image, in PIXEL space, and save a PNG to verify the satellite georeferencing
and locate the real S/F. Also maps the current start line lat/lon -> satellite pixels.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from pyqtgraph.Qt import QtGui, QtCore

import pacer
from studio import reference


def main():
    sat = "~/Desktop/Tracks/MK/gmaps_sat.png"
    sr = json.load(open(".startline_tmp/satref.json"))
    gd = json.load(open(".startline_tmp/georef_0060.json"))
    ox, oy = sr["track_px_origin"]
    sx, sy = sr["track_px_size"]
    W, H = sr["W"], sr["H"]
    clat, clon = gd["cs_origin"]
    cs = pacer.CoordinateSystem(pacer.GPSSample(lat=clat, lon=clon, altitude=0))

    # local-metre -> satellite pixel: invert norm->local (scale,R,t) then norm->pixel.
    scale = gd["scale"]
    R = np.array(gd["R"])
    t = np.array(gd["t"])
    Rinv = np.linalg.inv(R)

    def local_to_px(lx, ly):
        norm = (Rinv @ (np.array([lx, ly]) - t)) / scale  # -> normalized [0,1] (y-down)
        return ox + norm[0] * sx, oy + norm[1] * sy

    def latlon_to_px(la, lo):
        v = cs.local(pacer.GPSSample(lat=la, lon=lo, altitude=0))
        return local_to_px(float(v[0]), float(v[1]))

    img = QtGui.QImage(sat).convertToFormat(QtGui.QImage.Format.Format_RGB888)
    p = QtGui.QPainter(img)
    p.setRenderHint(QtGui.QPainter.RenderHint.Antialiasing, True)

    # centerline (cyan)
    ref = reference._resample(reference._load_normalized(), 600)
    pen = QtGui.QPen(QtGui.QColor(0, 220, 255), 3)
    p.setPen(pen)
    pts = [QtCore.QPointF(ox + rx * sx, oy + ry * sy) for rx, ry in ref]
    for i in range(len(pts) - 1):
        p.drawLine(pts[i], pts[i + 1])

    # current S/F line (red) from tracks.py coords
    a = latlon_to_px(52.04031, -0.78487)
    b = latlon_to_px(52.04020, -0.78460)
    p.setPen(QtGui.QPen(QtGui.QColor(255, 40, 40), 6))
    p.drawLine(QtCore.QPointF(*a), QtCore.QPointF(*b))
    # extend a label dot
    p.setBrush(QtGui.QBrush(QtGui.QColor(255, 40, 40)))
    p.drawEllipse(QtCore.QPointF((a[0] + b[0]) / 2, (a[1] + b[1]) / 2), 8, 8)

    # candidate lines passed as argv json: list of [[la1,lo1],[la2,lo2]]
    if len(sys.argv) > 1:
        cands = json.loads(sys.argv[1])
        p.setBrush(QtCore.Qt.BrushStyle.NoBrush)
        for (la1, lo1), (la2, lo2) in cands:
            c1 = latlon_to_px(la1, lo1)
            c2 = latlon_to_px(la2, lo2)
            p.setPen(QtGui.QPen(QtGui.QColor(0, 255, 0), 6))
            p.drawLine(QtCore.QPointF(*c1), QtCore.QPointF(*c2))
    p.end()
    img.save(".startline_tmp/satref_check.png")
    print(f"current S/F px: A={a} B={b}", flush=True)
    print("wrote .startline_tmp/satref_check.png", flush=True)


if __name__ == "__main__":
    main()
