"""Render the GPS trace + start lines over the satellite image -> PNG (offscreen pyqtgraph).

Uses the georef transform (norm image coords -> local metres) to place the satellite image
behind the trace in LOCAL-METRE space: each image corner maps norm (0,0),(1,0),(1,1),(0,1)
through the transform, giving the image's placement. pyqtgraph ImageItem takes an affine
QTransform, so we build it from the (scale,R,t) mapping pixel->local.

Outputs a PNG with:
  - satellite image (faded) as background, georeferenced
  - the GPS trace (thin) colored by speed
  - the CURRENT start line A/B (red)
  - any CANDIDATE start line(s) passed in (lime)
  - markers at the line midpoints
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg
from pyqtgraph.Qt import QtCore, QtGui  # noqa: F401
import pacer

SAT = "~/Desktop/Tracks/MK/gmaps_sat.png"
IMG_W, IMG_H = 1732, 1488


def latlon_to_local(cs, lat, lon):
    v = cs.local(pacer.GPSSample(lat=float(lat), lon=float(lon), altitude=0))
    return float(v[0]), float(v[1])


def main(trace_json, georef_json, out_png, candidates=None):
    d = json.load(open(trace_json))
    gd = json.load(open(georef_json))
    clat, clon = d["cs_origin"]
    cs = pacer.CoordinateSystem(pacer.GPSSample(lat=clat, lon=clon, altitude=0))

    x = np.array(d["trace"]["x"])
    y = np.array(d["trace"]["y"])
    v = np.array(d["trace"]["v"])

    scale = gd["scale"]
    R = np.array(gd["R"])
    t = np.array(gd["t"])
    # pixel(px,py) -> norm(px/W, py/H) -> local = scale*R@norm + t
    # local = scale*R@( [px/W, py/H] ) + t = M @ [px,py] + t, with M = scale*R@diag(1/W,1/H)
    M = scale * R @ np.diag([1.0 / IMG_W, 1.0 / IMG_H])
    # full affine pixel->local: [X;Y] = M[[a,b],[c,d]] [px;py] + [t]

    app = pg.mkQApp()
    pg.setConfigOptions(imageAxisOrder="row-major", antialias=True, background="w",
                        foreground="k")
    glw = pg.GraphicsLayoutWidget(size=(1400, 1300))
    glw.setBackground("w")
    plot = glw.addPlot()
    plot.setAspectLocked(True)
    plot.invertY(False)

    # Background satellite
    img = QtGui.QImage(SAT)
    img = img.convertToFormat(QtGui.QImage.Format.Format_RGBA8888)
    ptr = img.constBits()
    arr = np.frombuffer(ptr, np.uint8).reshape((img.height(), img.width(), 4)).copy()
    item = pg.ImageItem(arr)
    # QTransform maps item (col=px, row=py) coords -> scene (local). row-major ImageItem uses
    # (x=col, y=row). Build affine: X = a*px + c*py + tx ; Y = b*px + d*py + ty
    a, c = M[0, 0], M[0, 1]
    b, dd = M[1, 0], M[1, 1]
    tr = QtGui.QTransform(a, b, c, dd, t[0], t[1])
    item.setTransform(tr)
    item.setOpacity(1.0)
    plot.addItem(item)

    # Trace colored by speed (semi-transparent so the tarmac underneath shows for alignment)
    vmin, vmax = float(np.percentile(v, 5)), float(np.percentile(v, 95))
    cmap = pg.colormap.get("viridis")
    norm_v = np.clip((v - vmin) / max(vmax - vmin, 1e-6), 0, 1)
    cols = cmap.map(norm_v, mode="qcolor")
    for cc in cols:
        cc.setAlpha(170)
    sp = pg.ScatterPlotItem(x=x, y=y, size=2, pen=None,
                            brush=[pg.mkBrush(cc) for cc in cols])
    plot.addItem(sp)

    # CURRENT line A/B (from tracks.py coords)
    cur_a = latlon_to_local(cs, 52.04031, -0.78487)
    cur_b = latlon_to_local(cs, 52.04020, -0.78460)
    plot.addItem(pg.PlotDataItem(x=[cur_a[0], cur_b[0]], y=[cur_a[1], cur_b[1]],
                                 pen=pg.mkPen("red", width=4)))
    plot.addItem(pg.ScatterPlotItem(x=[(cur_a[0] + cur_b[0]) / 2], y=[(cur_a[1] + cur_b[1]) / 2],
                                    size=12, brush=pg.mkBrush("red"), pen=None))

    # Candidate lines (lat/lon pairs) in lime
    for cand in (candidates or []):
        (la1, lo1), (la2, lo2) = cand
        p1 = latlon_to_local(cs, la1, lo1)
        p2 = latlon_to_local(cs, la2, lo2)
        plot.addItem(pg.PlotDataItem(x=[p1[0], p2[0]], y=[p1[1], p2[1]],
                                     pen=pg.mkPen((0, 255, 0), width=4)))
        plot.addItem(pg.ScatterPlotItem(x=[(p1[0] + p2[0]) / 2], y=[(p1[1] + p2[1]) / 2],
                                        size=12, brush=pg.mkBrush((0, 255, 0)), pen=None))

    plot.setXRange(x.min() - 15, x.max() + 15)
    plot.setYRange(y.min() - 15, y.max() + 15)

    exporter = pg.exporters.ImageExporter(plot)
    exporter.parameters()["width"] = 1600
    exporter.export(out_png)
    print(f"wrote {out_png}", flush=True)


if __name__ == "__main__":
    import pyqtgraph.exporters  # noqa: F401
    cand = None
    if len(sys.argv) > 4:
        cand = json.loads(sys.argv[4])
    main(sys.argv[1], sys.argv[2], sys.argv[3], cand)
