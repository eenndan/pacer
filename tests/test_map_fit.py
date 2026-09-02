"""Track-map view fit + the always-on session trace (QA MAP-01 / MAP-03 / MAP-04), offscreen.

Three defects that had to be fixed together, because a fit computed over the wrong content
recreates the others:

  * MAP-01 — a scroll-wheel zoom or a drag left the map at an arbitrary pan/zoom that NOTHING in
    the app could undo (#126 removed pyqtgraph's "A" auto-range button and its right-click
    "View All" — the only two reset paths — while pan/zoom stayed live against a range set once in
    __init__). The fix is an APP-OWNED way back: a Fit button that appears over the plot the moment
    the view is moved, plus double-click-to-fit on the canvas. The pyqtgraph chrome stays gone, and
    the last test here is the guard that keeps it gone.
  * MAP-03 — the map drew only the best + current lap, so a recording with no complete laps drew
    NOTHING under a placeholder telling the user to "drag the start/finish line on the map".
  * MAP-04 — the frozen range came from session.tx/ty rather than from what is drawn. The fit must
    be the UNION of the trace and the lap overlays: laps alone would push the draggable video
    marker (which rides session.tx/ty) off-canvas on any recording whose complete laps cover a
    fraction of the drive.

Run: python tests/test_map_fit.py
"""
import math
import os
import sys
from types import SimpleNamespace

import numpy as np
import pyqtgraph as pg

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent, QWheelEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from _synthetic import bare_session  # noqa: E402

from studio.map_view import MapView, _trace_runs  # noqa: E402


def _session(n=240, lap_span=None, valid=(0,), best=0, gap_at=None):
    """A bare Session with the read surface MapView touches.

    The trace is a loop of `n` points; the drawn lap covers only `lap_span` of it (default: all of
    it), which is how a real unknown-track recording looks — a long drive with one short segment
    the auto-fitted start line happened to close into a "lap". `gap_at` punches a time hole at that
    index (a GPS dropout) without moving any point."""
    t = np.arange(n) * 0.1
    if gap_at is not None:
        t[gap_at:] += 5.0                      # a 5 s hole in the kept-point clock
    xs = np.cos(np.linspace(0, 2 * math.pi, n)) * 200.0
    ys = np.sin(np.linspace(0, 2 * math.pi, n)) * 120.0
    speed = np.linspace(20.0, 60.0, n)
    s = bare_session(best=best, valid=list(valid))
    s._best_cache = best        # seed the memo even for None (= no complete lap), which bare_session skips
    s.tx, s.ty, s.tt, s.tv = xs, ys, t, speed
    line = SimpleNamespace(first=SimpleNamespace(x=-210.0, y=0.0),
                           second=SimpleNamespace(x=-190.0, y=0.0))
    s.laps = SimpleNamespace(sectors=SimpleNamespace(start_line=line, sector_lines=[]))
    a, b = lap_span or (0, n)
    s.lap_trace_segments = lambda lid: [SimpleNamespace(xs=xs[a:b], ys=ys[a:b], measured=True)]
    s.lap_channels = lambda lid: {"t_media_s": t[a:b], "x_m": xs[a:b], "y_m": ys[a:b],
                                  "speed_kmh": speed[a:b],
                                  "dist_m": np.linspace(0.0, 500.0, b - a)}
    s.delta = lambda ids, x_mode="distance": (0, {}, {})
    return s


def _sized_map(session, w=900, h=240):
    """A MapView with a REAL px size — the default quadrant's letterbox shape, so the aspect-locked
    view rect is the one a user actually gets."""
    mv = MapView(session)
    mv.resize(w, h)
    mv.show()
    _APP.processEvents()
    _APP.processEvents()
    return mv


def _rect(mv):
    r = mv.plot.getViewBox().viewRect()
    return (r.left(), r.top(), r.width(), r.height())


def _same(a, b, tol=0.01):
    return all(abs(x - y) <= tol * max(abs(y), 1.0) for x, y in zip(a, b, strict=True))


def _drawn_points(mv):
    """Every finite (x, y) the map paints as track, over all three overlays."""
    xs, ys = [], []
    for ov in (mv._trace_overlay, mv._best_overlay, mv._current_overlay):
        for it in ov._items:
            x, y = it.getData()
            if x is None or len(x) == 0:
                continue
            x, y = np.asarray(x, float), np.asarray(y, float)
            ok = np.isfinite(x) & np.isfinite(y)
            xs.append(x[ok])
            ys.append(y[ok])
    return (np.concatenate(xs), np.concatenate(ys)) if xs else (np.empty(0), np.empty(0))


def _gesture(mv, notches=3, drag=(-300, -200)):
    """Three real wheel notches + a real left-drag on the plot viewport — the production event
    path, not a setRange() shortcut."""
    vp = mv.widget.viewport()
    c = QPointF(vp.width() / 2, vp.height() / 2)

    def g(p):
        return QPointF(vp.mapToGlobal(QPoint(int(p.x()), int(p.y()))))

    for _ in range(notches):
        _APP.sendEvent(vp, QWheelEvent(c, g(c), QPoint(0, 0), QPoint(0, 120), Qt.NoButton,
                                       Qt.NoModifier, Qt.NoScrollPhase, False))
        _APP.processEvents()
    p1 = QPointF(c.x() + drag[0], c.y() + drag[1])
    _APP.sendEvent(vp, QMouseEvent(QEvent.MouseButtonPress, c, g(c), Qt.LeftButton,
                                   Qt.LeftButton, Qt.NoModifier))
    for k in range(1, 6):
        pk = QPointF(c.x() + (p1.x() - c.x()) * k / 5, c.y() + (p1.y() - c.y()) * k / 5)
        _APP.sendEvent(vp, QMouseEvent(QEvent.MouseMove, pk, g(pk), Qt.NoButton,
                                       Qt.LeftButton, Qt.NoModifier))
        _APP.processEvents()
    _APP.sendEvent(vp, QMouseEvent(QEvent.MouseButtonRelease, p1, g(p1), Qt.LeftButton,
                                   Qt.NoButton, Qt.NoModifier))
    _APP.processEvents()


# --------------------------------------------------------------- MAP-01: a way back
def test_wheel_and_drag_are_recoverable_via_the_fit_button():
    """MAP-01. Two ordinary mouse gestures must not be a one-way door: the Fit button appears the
    moment the view leaves the fit, and clicking it restores the fitted rect (±1%)."""
    mv = _sized_map(_session())
    fitted = _rect(mv)
    assert not mv.fit_btn.isVisible(), "a framed map must carry no Fit chrome"
    _gesture(mv)
    moved = _rect(mv)
    assert not _same(moved, fitted), "the gesture did not move the view — the test proves nothing"
    assert mv.fit_btn.isVisible(), "no way back is offered after a real wheel-zoom + drag"
    mv.fit_btn.click()
    _APP.processEvents()
    assert _same(_rect(mv), fitted), f"Fit did not restore the view: {_rect(mv)} vs {fitted}"
    assert not mv.fit_btn.isVisible(), "the Fit button must retire once the map is framed again"
    print("test_wheel_and_drag_are_recoverable_via_the_fit_button OK")


def test_canvas_double_click_fits():
    """MAP-01. The gesture a user tries first (and which did nothing at all before) also fits."""
    mv = _sized_map(_session())
    fitted = _rect(mv)
    _gesture(mv)
    assert not _same(_rect(mv), fitted)
    vp = mv.widget.viewport()
    p = QPointF(vp.width() / 2, vp.height() / 2)
    gp = QPointF(vp.mapToGlobal(QPoint(int(p.x()), int(p.y()))))
    _APP.sendEvent(vp, QMouseEvent(QEvent.MouseButtonDblClick, p, gp, Qt.LeftButton,
                                   Qt.LeftButton, Qt.NoModifier))
    _APP.processEvents()
    assert _same(_rect(mv), fitted), f"double-click did not fit: {_rect(mv)} vs {fitted}"
    print("test_canvas_double_click_fits OK")


def test_a_redraw_never_yanks_back_a_view_the_user_moved():
    """The other half of MAP-01's fix: re-fitting on refresh/resize must respect a deliberate
    zoom. A user studying one corner keeps it across a re-segmentation and a panel resize."""
    mv = _sized_map(_session())
    _gesture(mv)
    zoomed = _rect(mv)
    mv.refresh_overlays()
    _APP.processEvents()
    assert _same(_rect(mv), zoomed), "refresh_overlays stole the user's zoom"
    mv.resize(700, 400)
    _APP.processEvents()
    assert mv.fit_btn.isVisible(), "the way back must survive a resize"
    # …while a map still on OUR fit does follow the panel: refit, resize, stay framed.
    mv.fit_btn.click()
    mv.resize(1000, 300)
    _APP.processEvents()
    xs, ys = _drawn_points(mv)
    r = mv.plot.getViewBox().viewRect()
    assert xs.min() >= r.left() and xs.max() <= r.right(), "a fitted map lost its track on resize"
    print("test_a_redraw_never_yanks_back_a_view_the_user_moved OK")


# --------------------------------------------------------------- MAP-03: something to drag onto
def test_zero_lap_map_still_draws_the_trace_it_tells_you_to_drag_onto():
    """MAP-03. With no complete lap both lap overlays are empty by design — that is exactly the
    state whose placeholder says "drag the start/finish line on the map", so the trace must be
    there to drag it onto, and inside the view."""
    mv = _sized_map(_session(valid=(), best=None))
    assert mv._empty_state.isVisibleTo(mv.widget), "this is meant to be the zero-valid-lap state"
    assert mv._best_overlay._items == [] and mv._current_overlay._items == [], \
        "the lap overlays are expected to be empty here — that is the premise"
    xs, ys = _drawn_points(mv)
    assert len(xs) > 0, "the map draws nothing to drag the start/finish line onto"
    r = mv.plot.getViewBox().viewRect()
    assert xs.min() >= r.left() and xs.max() <= r.right(), "drawn track falls outside the view"
    assert ys.min() >= r.top() and ys.max() <= r.top() + r.height(), "drawn track above/below view"
    print("test_zero_lap_map_still_draws_the_trace_it_tells_you_to_drag_onto OK")


def test_trace_breaks_at_gps_dropouts_instead_of_chording_across_them():
    """The faint trace is split at holes in the kept-point clock, so a dropout reads as a break —
    not as a straight line through a corner that was never driven that way."""
    n = 240
    runs = _trace_runs(_session(n=n))
    assert len(runs) == 1 and len(runs[0][0]) == n, "a clean trace is one unbroken run"
    runs = _trace_runs(_session(n=n, gap_at=100))
    assert len(runs) == 2, f"a 5 s dropout must split the trace, got {len(runs)} run(s)"
    assert len(runs[0][0]) == 100 and len(runs[1][0]) == n - 100
    print("test_trace_breaks_at_gps_dropouts_instead_of_chording_across_them OK")


# --------------------------------------------------------------- MAP-04: fit over the union
def test_fit_frames_the_trace_and_the_laps_together():
    """MAP-04 + MAP-03's interaction. The lap here covers a tenth of the drive (the unknown-track
    shape). Fitting to the drawn LAP would frame that tenth and throw the rest of the trace — and
    the video marker, which rides session.tx/ty — off the canvas."""
    n = 240
    mv = _sized_map(_session(n=n, lap_span=(0, n // 10)))
    r = mv.plot.getViewBox().viewRect()
    s = mv.session
    assert (s.tx.min() >= r.left() and s.tx.max() <= r.right()
            and s.ty.min() >= r.top() and s.ty.max() <= r.top() + r.height()), \
        f"the fit dropped part of the trace: view {r} vs trace x[{s.tx.min()},{s.tx.max()}]"
    for i in (0, n // 2, n - 1):                    # the marker anywhere in the recording
        mv.set_marker_index(i)
        p = mv.marker.pos()
        assert r.contains(QPointF(p.x(), p.y())), f"the video marker left the canvas at index {i}"
    # …and the drawn lap is inside it too (the union, not one or the other).
    xs, ys = _drawn_points(mv)
    assert xs.min() >= r.left() and xs.max() <= r.right()
    print("test_fit_frames_the_trace_and_the_laps_together OK")


def test_fit_covers_a_cross_recording_reference_beyond_the_trace():
    """The union's other direction: an F7 cross-recording reference ring is drawn into the best-lap
    overlay from ANOTHER recording, so it can reach outside this session's trace. Fitting to the
    trace alone would clip it."""
    mv = _sized_map(_session())
    far_x = np.array([400.0, 500.0, 500.0, 400.0, 400.0])
    far_y = np.array([200.0, 200.0, 300.0, 300.0, 200.0])
    mv._best_overlay.set_polyline(far_x, far_y, ("ref", "other recording"))
    mv._fit_view()
    _APP.processEvents()
    r = mv.plot.getViewBox().viewRect()
    assert far_x.max() <= r.right() and far_y.max() <= r.top() + r.height(), \
        f"the reference ring is outside the fitted view {r}"
    assert mv.session.tx.min() >= r.left(), "…and the session trace was dropped to fit it"
    print("test_fit_covers_a_cross_recording_reference_beyond_the_trace OK")


# --------------------------------------------------------------- the A16 / #126 guard
def test_fit_affordance_does_not_resurrect_the_pyqtgraph_chrome():
    """A16/#126 must survive this fix. The way back is the app's own button + a canvas gesture —
    NOT pyqtgraph's "A" auto-range button or its right-click developer menu, both of which stay
    off. Mouse interaction (pan/zoom, draggable handles) stays exactly as it was."""
    mv = _sized_map(_session())
    mv._fit_view()
    _gesture(mv)
    mv.fit_btn.click()
    _APP.processEvents()
    plot, vb = mv.plot, mv.plot.getViewBox()
    assert plot.buttonsHidden is True, "the 'A' auto-range button came back"
    assert not plot.autoBtn.isVisible(), "the auto-range button is showing"
    assert plot.menuEnabled() is False and vb.menuEnabled() is False, \
        "the pyqtgraph right-click developer menu came back"
    assert list(vb.state["mouseEnabled"]) == [True, True], "pan/zoom was disabled"
    assert vb.state["mouseMode"] == pg.ViewBox.PanMode
    assert vb.state["autoRange"] == [False, False], "the frozen range was left on autorange"
    assert mv.marker.movable is True and mv._start.h1.movable is True
    print("test_fit_affordance_does_not_resurrect_the_pyqtgraph_chrome OK")


def test_share_grab_hides_the_fit_button():
    """The Fit button is interaction chrome: it has no place on a shareable lap card."""
    mv = _sized_map(_session())
    _gesture(mv)
    assert mv.fit_btn.isVisible()
    with mv.grab_clean():
        assert not mv.fit_btn.isVisible(), "the Fit button burned into the share image"
    assert mv.fit_btn.isVisible(), "grab_clean must restore the button it hid"
    print("test_share_grab_hides_the_fit_button OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} MAP FIT/TRACE TESTS PASSED")
