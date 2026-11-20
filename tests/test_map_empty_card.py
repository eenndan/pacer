"""The map's zero-lap card stays clear of the trace and the start/finish line it asks you to drag.

On a recording with no complete lap every empty state says "drag the start/finish line on the map
to set where a lap begins" — and the map's own card used to sit CENTRED over the map, i.e. over the
middle of that trace. Measured in the real window on the zero-lap synthetic (DECISIONS §20 open
list): at 1280x800 the docked canvas is 815x216 and the card covered 57 % of the trace and one of
the line's two drag handles; at 1440x900 (917x254) 48 %. The fix pins the card in a strip along the
canvas's right edge or top and frames the track in the rest (MapView._empty_card_slot).

The canvas sizes below are the ones measured in that window, docked and with the map maximised.

Run: python tests/test_map_empty_card.py
"""
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtCore import QPointF, QRectF  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

import pyqtgraph as pg  # noqa: E402
from test_map_fit import _drawn_points, _session  # noqa: E402

from studio import data_quality, theme  # noqa: E402
from studio.map_view import MapView  # noqa: E402

theme.apply_theme(_APP)

# (canvas w, canvas h): docked and maximised, in the real window at 1280x800 and 1440x900.
CANVASES = {"1280x800 docked": (815, 216), "1440x900 docked": (917, 254),
            "1280x800 maximised": (1272, 678), "1440x900 maximised": (1432, 778)}
# The most of the trace the card may hide: a stated small fraction, not "none by luck".
MAX_TRACE_UNDER_CARD = 0.02
HANDLE_BOX_PX = 24          # a size-11 TargetItem's 22 px box plus a pixel of air each side


def _zero_lap_map(canvas):
    """A zero-lap MapView whose plot canvas is exactly `canvas` px."""
    mv = MapView(_session(valid=(), best=None))
    w, h = canvas
    mv.resize(w, h)
    mv.show()
    _APP.processEvents()
    mv.resize(w, h + (mv.height() - mv.widget.height()))   # the legend strip sits below the canvas
    _APP.processEvents()
    _APP.processEvents()
    assert (mv.widget.width(), mv.widget.height()) == canvas, (mv.widget.size(), canvas)
    assert mv._empty_state.isVisibleTo(mv.widget), "this is meant to be the zero-lap state"
    return mv


def _to_canvas(mv, x, y):
    sp = mv.plot.getViewBox().mapViewToScene(QPointF(float(x), float(y)))
    return QPointF(mv.widget.viewport().mapTo(mv.widget, mv.widget.mapFromScene(sp)))


def _card(mv):
    return QRectF(mv._empty_state.geometry())


def _handle_boxes(mv):
    out = []
    for h in (mv._start.h1, mv._start.h2):
        p = _to_canvas(mv, h.pos().x(), h.pos().y())
        half = HANDLE_BOX_PX / 2
        out.append(QRectF(p.x() - half, p.y() - half, HANDLE_BOX_PX, HANDLE_BOX_PX))
    return out


def _trace_share_under(mv, rect):
    xs, ys = _drawn_points(mv)
    pts = [_to_canvas(mv, x, y) for x, y in zip(xs, ys, strict=True)]
    return sum(rect.contains(p) for p in pts) / max(len(pts), 1)


def test_card_leaves_the_trace_and_the_line_handles_free():
    for name, canvas in CANVASES.items():
        mv = _zero_lap_map(canvas)
        card = _card(mv)
        share = _trace_share_under(mv, card)
        assert share <= MAX_TRACE_UNDER_CARD, \
            f"{name}: the card hides {share:.0%} of the trace it asks you to drag the line onto"
        for box in _handle_boxes(mv):
            assert not card.intersects(box), f"{name}: the card covers a start/finish handle"
            assert QRectF(mv.widget.rect()).contains(box), f"{name}: a handle is off the canvas"
        # …and the card itself is whole: inside the canvas, tall enough for its wrapped text.
        es = mv._empty_state
        assert QRectF(mv.widget.rect()).contains(card), f"{name}: the card runs off the canvas"
        assert es.height() >= es.heightForWidth(es.width()), f"{name}: the card clips its text"
        mv.close()
    print("test_card_leaves_the_trace_and_the_line_handles_free OK")


def test_card_says_the_shared_body():
    mv = _zero_lap_map(CANVASES["1280x800 docked"])
    es = mv._empty_state
    assert es.title.text() == data_quality.NO_LAPS_HEADLINE
    assert es.body.text() == data_quality.no_laps_body_for(mv.session), es.body.text()
    mv.close()
    print("test_card_says_the_shared_body OK")


def test_card_stays_put_while_the_line_is_dragged():
    """A drag that still leaves no lap moves neither the card nor its strip."""
    for name, canvas in CANVASES.items():
        mv = _zero_lap_map(canvas)
        before = _card(mv)
        st = mv._start
        st.h1.setPos(pg.Point(150.0, -40.0))
        st.h2.setPos(pg.Point(150.0, 40.0))
        st._released(st.h2)
        mv.refresh_overlays()          # what the window does after the re-segmentation
        _APP.processEvents()
        assert mv._empty_state.isVisibleTo(mv.widget)
        assert _card(mv) == before, f"{name}: the card moved under a drag ({before} -> {_card(mv)})"
        for box in _handle_boxes(mv):
            assert not _card(mv).intersects(box), f"{name}: the dragged line went under the card"
        mv.close()
    print("test_card_stays_put_while_the_line_is_dragged OK")


def test_card_goes_and_the_track_takes_the_canvas_back_once_a_lap_exists():
    for name, canvas in CANVASES.items():
        mv = _zero_lap_map(canvas)
        mv.session._valid_cache = [0]
        mv.session._best_cache = 0
        mv.refresh_overlays()
        _APP.processEvents()
        assert not mv._empty_state.isVisibleTo(mv.widget), f"{name}: a lap exists, the card must go"
        # The strip goes with the card: the track is centred on the whole canvas again.
        xs, ys = _drawn_points(mv)
        lo = _to_canvas(mv, np.nanmin(xs), np.nanmax(ys))
        hi = _to_canvas(mv, np.nanmax(xs), np.nanmin(ys))
        cx, cy = (lo.x() + hi.x()) / 2, (lo.y() + hi.y()) / 2
        assert abs(cx - canvas[0] / 2) <= 2 and abs(cy - canvas[1] / 2) <= 2, \
            f"{name}: the track still sits off-centre ({cx:.0f}, {cy:.0f}) after the card went"
        mv.close()
    print("test_card_goes_and_the_track_takes_the_canvas_back_once_a_lap_exists OK")


if __name__ == "__main__":
    test_card_leaves_the_trace_and_the_line_handles_free()
    test_card_says_the_shared_body()
    test_card_stays_put_while_the_line_is_dragged()
    test_card_goes_and_the_track_takes_the_canvas_back_once_a_lap_exists()
    print("ALL OK")
