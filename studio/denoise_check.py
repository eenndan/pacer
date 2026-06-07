"""GPS-denoise check + render harness — the visual & numeric feedback loop for the map trace.

Why this exists
---------------
The studio map trace was visibly NOISY (raw ~3 m GPS jitter), while the upstream Jupyter
notebooks (since removed) produced a SMOOTH,
track-matching map by moving-average-smoothing x/y BEFORE measuring distance. This tool lets
us SEE the map (render PNGs offscreen) and MEASURE both the jitter we want to kill and the
real lap-to-lap signal we must NOT erase.

Metrics (all on the real session's valid laps)
----------------------------------------------
* within-lap cross-track jitter std (m): perpendicular distance of each lap's raw/plotted
  points from their OWN heavily-smoothed centerline — the high-frequency wobble we want down.
* heading jitter (deg): std of the point-to-point heading second-difference — pure HF noise.
* lap-to-lap signal RMS (m): RMS spatial distance between two distinct laps' traces sampled
  at matched normalized distance — the genuine racing-line difference that must be PRESERVED.

Run (headless, offscreen):
    pixi run python -m studio.denoise_check -- /path/to/file.MP4 [--out DIR] [--tag NAME]

Without a file it uses session.DEFAULT_SAMPLE. PNGs + a metrics line are written to --out
(default $TMPDIR/denoise or ./denoise_out). Read the PNGs to judge smoothness/signal by eye.
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

import pacer  # noqa: F401 — ensure the module imports cleanly before Qt

from .session import DEFAULT_SAMPLE, Session, _smooth


# ----------------------------------------------------------------- metrics
def _resample_xy(xs, ys, n=400):
    """Resample a polyline to n points evenly spaced in normalized arc length [0,1]."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    if len(xs) < 2:
        return xs, ys
    d = np.concatenate([[0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(ys)))])
    if d[-1] <= 0:
        return xs, ys
    s = d / d[-1]
    g = np.linspace(0.0, 1.0, n)
    return np.interp(g, s, xs), np.interp(g, s, ys)


def cross_track_jitter(xs, ys, w_center=41):
    """High-frequency cross-track jitter (m): the std of the HIGH-PASS part of each point's
    perpendicular offset from a HEAVILY-smoothed (w=41) centerline of its own lap. Subtracting
    a w=9 smoothing of that offset removes the slow racing-line shape, leaving only the fast
    GPS wobble — so this measures the SAME noise on a raw vs a w=9 trace and falls when the
    trace is denoised (it does not just re-measure the residual to the trace's own smoothing)."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    if len(xs) < w_center + 4:
        return float("nan")
    cx = _smooth(xs, w_center)
    cy = _smooth(ys, w_center)
    tx = np.gradient(cx)
    ty = np.gradient(cy)
    tn = np.hypot(tx, ty)
    tn[tn == 0] = 1.0
    nx, ny = -ty / tn, tx / tn  # unit normal to the centerline tangent
    off = (xs - cx) * nx + (ys - cy) * ny  # signed cross-track offset
    hf = off - _smooth(off, 9)  # keep only frequencies above the w=9 cutoff (the noise band)
    k = w_center  # trim the smoothing edges
    hf = hf[k:-k] if len(hf) > 2 * k else hf
    return float(np.std(hf))


def heading_jitter_deg(xs, ys):
    """Std of the second difference of point-to-point heading (deg) — pure HF direction noise."""
    xs = np.asarray(xs, float)
    ys = np.asarray(ys, float)
    if len(xs) < 5:
        return float("nan")
    dx = np.diff(xs)
    dy = np.diff(ys)
    h = np.degrees(np.unwrap(np.arctan2(dy, dx)))
    return float(np.std(np.diff(h, 2)))


def laptolap_rms(lapA, lapB, n=400):
    """RMS spatial distance (m) between two laps sampled at matched normalized distance —
    the genuine lap-to-lap difference. Must stay healthy (we are NOT trying to drive this to 0)."""
    ax, ay = _resample_xy(*lapA, n=n)
    bx, by = _resample_xy(*lapB, n=n)
    if len(ax) < 2 or len(bx) < 2:
        return float("nan")
    return float(np.sqrt(np.mean((ax - bx) ** 2 + (ay - by) ** 2)))


def compute_metrics(session: Session):
    valid = session.valid_lap_ids()
    if not valid:
        return {"valid_laps": 0}
    traces = {i: session.lap_trace_xy(i) for i in valid}
    ct = [cross_track_jitter(*traces[i]) for i in valid]
    hd = [heading_jitter_deg(*traces[i]) for i in valid]
    # lap-to-lap signal: median RMS over consecutive valid-lap pairs (real line differences)
    pairs = [laptolap_rms(traces[valid[k]], traces[valid[k + 1]]) for k in range(len(valid) - 1)]
    return {
        "valid_laps": len(valid),
        "cross_track_jitter_m": float(np.nanmedian(ct)),
        "heading_jitter_deg": float(np.nanmedian(hd)),
        "laptolap_signal_rms_m": float(np.nanmedian(pairs)) if pairs else float("nan"),
        "best_lap": session.best_lap_id(),
    }


# ----------------------------------------------------------------- rendering
def _render(session: Session, out_dir: str, tag: str):
    import pyqtgraph as pg
    import pyqtgraph.exporters
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)
    os.makedirs(out_dir, exist_ok=True)
    valid = session.valid_lap_ids()
    best = session.best_lap_id()
    paths = []

    def new_plot(title):
        w = pg.PlotWidget()
        pi = w.getPlotItem()
        pi.setAspectLocked(True)
        pi.showGrid(x=True, y=True, alpha=0.2)
        pi.setTitle(title)
        w.resize(900, 900)
        return w, pi

    def save(w, name):
        # process events so the scene lays out before grabbing
        _app.processEvents()
        p = os.path.join(out_dir, name)
        ex = pg.exporters.ImageExporter(w.getPlotItem())
        ex.parameters()["width"] = 900
        ex.export(p)
        paths.append(p)
        return p

    # (a) best lap alone
    if best is not None:
        w, pi = new_plot(f"[{tag}] best lap {best}")
        xs, ys = session.lap_trace_xy(best)
        pi.plot(xs, ys, pen=pg.mkPen("#39a0ed", width=2))
        save(w, f"{tag}_best.png")

    # (b) a representative "selected" lap (median valid lap by id)
    if valid:
        sel = valid[len(valid) // 2]
        w, pi = new_plot(f"[{tag}] selected lap {sel}")
        xs, ys = session.lap_trace_xy(sel)
        pi.plot(xs, ys, pen=pg.mkPen("#ffd166", width=2))
        save(w, f"{tag}_selected.png")

    # (c) several laps overlaid (up to 6) — must stay DISTINCT after smoothing
    if valid:
        w, pi = new_plot(f"[{tag}] {min(6, len(valid))} laps overlaid")
        colors = ["#39a0ed", "#ff5252", "#06d6a0", "#ffd166", "#b388ff", "#ff9f1c"]
        for k, lid in enumerate(valid[: len(colors)]):
            xs, ys = session.lap_trace_xy(lid)
            pi.plot(xs, ys, pen=pg.mkPen(colors[k % len(colors)], width=1))
        save(w, f"{tag}_overlay.png")

    # (d) full trace (all plotted points) — global smoothness sanity
    w, pi = new_plot(f"[{tag}] full trace")
    pi.plot(session.tx, session.ty, pen=pg.mkPen("#888888", width=1))
    save(w, f"{tag}_full.png")

    return paths


def gap_metrics(session: Session):
    """Per-lap GPS-gap reconstruction stats (MAP-ONLY). Returns a summary dict + per-lap
    detail. Quantifies the chords that the gap-fill replaces and how each was filled
    (cross-lap borrow / spline / reference / unfilled)."""
    valid = session.valid_lap_ids()
    tot = {"laps": len(valid), "gaps": 0, "chord_m": 0.0, "borrow_m": 0.0, "spline_m": 0.0,
           "reference_m": 0.0, "unfilled_m": 0.0,
           "n_borrow": 0, "n_spline": 0, "n_reference": 0, "n_unfilled": 0}
    per_lap = {}
    for lid in valid:
        rep = session.lap_gap_report(lid)
        if not rep:
            continue
        per_lap[lid] = rep
        for f in rep:
            tot["gaps"] += 1
            tot["chord_m"] += f["chord_m"]
            src = f["source"]
            if src.startswith("borrow:"):
                tot["borrow_m"] += f["fill_m"]
                tot["n_borrow"] += 1
            elif src.startswith("spline"):  # spline + spline-fallback
                tot["spline_m"] += f["fill_m"]
                tot["n_spline"] += 1
            elif src == "reference":
                tot["reference_m"] += f["fill_m"]
                tot["n_reference"] += 1
            else:
                tot["unfilled_m"] += f["chord_m"]
                tot["n_unfilled"] += 1
    return tot, per_lap


def _render_gaps(session: Session, out_dir: str, tag: str):
    """Render the gap-filled map: measured runs SOLID, reconstructed fills DASHED/DIMMED —
    exactly the map_view styling. The best lap + the laps that actually have gaps."""
    import pyqtgraph as pg
    import pyqtgraph.exporters
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)
    os.makedirs(out_dir, exist_ok=True)
    valid = session.valid_lap_ids()
    best = session.best_lap_id()
    gappy = [lid for lid in valid if session.lap_gap_report(lid)]
    out = []

    def draw(lids, name, title):
        w = pg.PlotWidget()
        pi = w.getPlotItem()
        pi.setAspectLocked(True)
        pi.showGrid(x=True, y=True, alpha=0.2)
        pi.setTitle(title)
        w.resize(900, 900)
        colors = ["#39a0ed", "#ff5252", "#06d6a0", "#ffd166", "#b388ff", "#ff9f1c"]
        for k, lid in enumerate(lids):
            col = colors[k % len(colors)]
            for seg in session.lap_trace_segments(lid):
                if seg.measured:
                    pi.plot(seg.xs, seg.ys, pen=pg.mkPen(col, width=2))
                else:
                    pen = pg.mkPen("#ffffff", width=2)
                    pen.setStyle(Qt.DashLine)
                    pen.setDashPattern([4, 4])
                    pi.plot(seg.xs, seg.ys, pen=pen)
        _app.processEvents()
        p = os.path.join(out_dir, name)
        ex = pg.exporters.ImageExporter(pi)
        ex.parameters()["width"] = 900
        ex.export(p)
        out.append(p)

    if best is not None:
        draw([best], f"{tag}_gaps_best.png", f"[{tag}] best lap {best} (white dash = inferred)")
    if gappy:
        draw(gappy[:6], f"{tag}_gaps_gappy.png",
             f"[{tag}] {min(6, len(gappy))} laps with gaps (white dash = inferred)")
    return out


def _render_notebook_reference(paths_in, out_dir, tag="notebook"):
    """Render the upstream notebook's gold-standard map: _smooth the per-lap local x/y and plot —
    the SAME w=9 filter it applied before computing distance/delta. Parity eyeball target."""
    import pyqtgraph as pg
    import pyqtgraph.exporters
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication(sys.argv)
    os.makedirs(out_dir, exist_ok=True)
    # Reuse the Session (same cs/segmentation) but apply the w=9 _smooth to the RAW per-lap trace
    # independently, reproducing the upstream notebook's reference render.
    sess = Session.load(paths_in, smooth_window=1)  # raw trace, no studio smoothing
    valid = sess.valid_lap_ids()
    if not valid:
        return []
    w = pg.PlotWidget()
    pi = w.getPlotItem()
    pi.setAspectLocked(True)
    pi.showGrid(x=True, y=True, alpha=0.2)
    pi.setTitle(f"[{tag}] upstream-notebook _smooth(w=9) per-lap, {min(6, len(valid))} laps")
    w.resize(900, 900)
    colors = ["#39a0ed", "#ff5252", "#06d6a0", "#ffd166", "#b388ff", "#ff9f1c"]
    for k, lid in enumerate(valid[:6]):
        xs, ys = sess.lap_trace_xy(lid)
        pi.plot(_smooth(xs, 9), _smooth(ys, 9), pen=pg.mkPen(colors[k % len(colors)], width=1))
    _app.processEvents()
    p = os.path.join(out_dir, f"{tag}_overlay.png")
    ex = pg.exporters.ImageExporter(pi)
    ex.parameters()["width"] = 900
    ex.export(p)
    return [p]


_FLAGS_WITH_VALUE = {"--out", "--tag", "--window"}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    out_dir = os.environ.get("TMPDIR", "/tmp").rstrip("/") + "/denoise"
    tag = "trace"
    w = None
    if "--out" in argv:
        out_dir = argv[argv.index("--out") + 1]
    if "--tag" in argv:
        tag = argv[argv.index("--tag") + 1]
    if "--window" in argv:
        w = int(argv[argv.index("--window") + 1])

    # Positional args = files: skip flags AND the value that follows a value-taking flag.
    paths = []
    skip = False
    for a in argv:
        if skip:
            skip = False
            continue
        if a.startswith("-"):
            skip = a in _FLAGS_WITH_VALUE
            continue
        paths.append(a)
    paths = paths or [DEFAULT_SAMPLE]

    print(f"denoise_check: loading {paths} (smooth_window={'default' if w is None else w})")
    session = Session.load(paths) if w is None else Session.load(paths, smooth_window=w)
    m = compute_metrics(session)
    print("METRICS", {k: (round(v, 4) if isinstance(v, float) else v) for k, v in m.items()})
    out = _render(session, out_dir, tag)
    print("PNGs:")
    for p in out:
        print("  ", p)
    if "--notebook-ref" in argv:
        ref = _render_notebook_reference(paths, out_dir)
        for p in ref:
            print("   (ref)", p)
    if "--gaps" in argv:
        tot, per_lap = gap_metrics(session)
        print("GAP METRICS", {k: (round(v, 1) if isinstance(v, float) else v)
                              for k, v in tot.items()})
        for lid, rep in per_lap.items():
            print(f"  lap {lid}:", "; ".join(
                f"{f['source']}(chord {f['chord_m']:.1f}m fill {f['fill_m']:.1f}m "
                f"err {f['endpoint_err_m']:.2f}m)" for f in rep))
        gout = _render_gaps(session, out_dir, tag)
        for p in gout:
            print("   (gaps)", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
