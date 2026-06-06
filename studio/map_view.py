"""MapView: the track trace with draggable start/sector timing lines + a video marker.

Timing lines are drawn in LOCAL meters (same space as the trace). Each line is two
draggable pyqtgraph TargetItem handles joined by a segment; dragging either handle
re-segments the laps (via the app) live. The red marker tracks the video position and,
when dragged, seeks the video to the nearest telemetry sample.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .session import Seg

START_COLOR = "#ffd166"
SECTOR_COLOR = "#06d6a0"
BEST_COLOR = "#5a6068"  # faint reference line for the best lap
CURRENT_COLOR = "#39a0ed"  # highlighted current-lap trace
MARKER_COLOR = "#ff5252"


class _TimingLine:
    """Two draggable handles + a connecting segment, all in data (local-meter) coords."""

    def __init__(self, plot, seg: Seg, color, on_changed, session):
        self.plot = plot
        self.on_changed = on_changed
        self.session = session
        self._snapping = False  # guard so a snap's setPos doesn't recurse on Finished
        pen = pg.mkPen(color, width=2)
        self.line = pg.PlotDataItem([seg.x1, seg.x2], [seg.y1, seg.y2], pen=pen)
        self.h1 = pg.TargetItem((seg.x1, seg.y1), size=11, movable=True, pen=pen)
        self.h2 = pg.TargetItem((seg.x2, seg.y2), size=11, movable=True, pen=pen)
        plot.addItem(self.line)
        plot.addItem(self.h1)
        plot.addItem(self.h2)
        # Live: dragging either handle redraws the segment as it moves. On release each handle
        # snaps to its own nearest trace point (and re-segments the laps).
        self.h1.sigPositionChanged.connect(self._moved)
        self.h2.sigPositionChanged.connect(self._moved)
        self.h1.sigPositionChangeFinished.connect(lambda: self._snap(self.h1))
        self.h2.sigPositionChangeFinished.connect(lambda: self._snap(self.h2))

    def _moved(self, *_):
        p1, p2 = self.h1.pos(), self.h2.pos()
        self.line.setData([p1.x(), p2.x()], [p1.y(), p2.y()])
        self.on_changed()

    def _snap(self, handle):
        """On release, snap `handle` to its own nearest trace point, redraw, re-segment.

        The snap target must NOT land on the other handle's point: a zero-length segment
        crosses nothing (pacer Segment::Intersects returns false) and wipes out every lap.
        So exclude the other handle's current point and snap to the nearest DISTINCT sample;
        if none exists, skip the snap rather than collapse the line."""
        if self._snapping:
            return
        p = handle.pos()
        other = self.h2 if handle is self.h1 else self.h1
        op = other.pos()
        i = self.session.nearest_index_excluding(p.x(), p.y(), op.x(), op.y())
        if i is None:
            return
        self._snapping = True
        # setPos fires sigPositionChanged -> _moved (redraws the line + on_changed); that's
        # fine. The guard only prevents this snap from recursing via sigPositionChangeFinished.
        handle.setPos(pg.Point(float(self.session.tx[i]), float(self.session.ty[i])))
        self._snapping = False
        self._moved()

    def seg(self) -> Seg:
        p1, p2 = self.h1.pos(), self.h2.pos()
        return Seg(p1.x(), p1.y(), p2.x(), p2.y())

    def remove(self):
        for item in (self.line, self.h1, self.h2):
            self.plot.removeItem(item)


class MapView(QWidget):
    # (start: Seg, sectors: list[Seg]) whenever a handle moves or sectors change.
    timing_lines_changed = Signal(object, object)
    seek_requested = Signal(float)  # seconds

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._suppress_marker = False

        self.widget = pg.PlotWidget()
        self.plot = self.widget.getPlotItem()
        self.plot.setAspectLocked(True)  # equal aspect -> a true-shape track map
        self.plot.showGrid(x=True, y=True, alpha=0.2)
        self.plot.setLabel("bottom", "x (m)")
        self.plot.setLabel("left", "y (m)")
        # The full ~16k-point trace is no longer drawn (jagged + slow). Instead we draw at most
        # the best lap (faint reference) and the current lap (highlighted) — a few hundred points.
        self._best_overlay = None  # faint best-lap reference line
        self._best_lap_id: int | None = None
        self._current_overlay = None  # highlighted current-lap trace
        self._current_lap_id: int | None = None

        # Freeze the view to the track bbox so marker moves never trigger autorange / a full
        # re-render. The user's pan/zoom still works; the track stays fully visible on load.
        if len(session.tx) and len(session.ty):
            x_lo, x_hi = float(session.tx.min()), float(session.tx.max())
            y_lo, y_hi = float(session.ty.min()), float(session.ty.max())
            px = max(x_hi - x_lo, 1.0) * 0.05
            py = max(y_hi - y_lo, 1.0) * 0.05
            vb = self.plot.getViewBox()
            vb.setRange(xRange=(x_lo - px, x_hi + px), yRange=(y_lo - py, y_hi + py), padding=0)
            vb.disableAutoRange()

        self.marker = pg.TargetItem(
            (session.tx[0] if len(session.tx) else 0, session.ty[0] if len(session.ty) else 0),
            size=15, movable=True, pen=pg.mkPen(MARKER_COLOR, width=2),
            brush=pg.mkBrush(255, 82, 82, 110),
        )
        self.plot.addItem(self.marker)
        self.marker.setZValue(10)  # keep the marker above the lap overlays
        self.marker.sigPositionChanged.connect(self._marker_dragged)

        self._start: _TimingLine | None = None
        self._sectors: list[_TimingLine] = []
        self._rebuild(session.start_line, session.sector_lines)
        self._refresh_best()

        add_btn = QPushButton("Add sector")
        reset_btn = QPushButton("Reset sectors")
        add_btn.clicked.connect(self._add_sector)
        reset_btn.clicked.connect(self._reset_sectors)
        row = QHBoxLayout()
        row.addWidget(add_btn)
        row.addWidget(reset_btn)
        row.addStretch(1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget, 1)
        lay.addLayout(row)

    # ----------------------------------------------------------- timing lines
    def _rebuild(self, start: Seg, sectors: list[Seg]):
        for tl in [self._start, *self._sectors]:
            if tl:
                tl.remove()
        self._start = _TimingLine(self.plot, start, START_COLOR, self._emit, self.session)
        self._sectors = [_TimingLine(self.plot, s, SECTOR_COLOR, self._emit, self.session)
                         for s in sectors]

    def _current(self) -> tuple[Seg, list[Seg]]:
        return self._start.seg(), [s.seg() for s in self._sectors]

    def _emit(self):
        start, sectors = self._current()
        self.timing_lines_changed.emit(start, sectors)

    def _add_sector(self):
        start, sectors = self._current()
        sectors.append(self.session.suggest_sector())
        self._rebuild(start, sectors)
        self.timing_lines_changed.emit(start, sectors)

    def _reset_sectors(self):
        start, _ = self._current()
        self._rebuild(start, [])
        self.timing_lines_changed.emit(start, [])

    # --------------------------------------------------------------- video sync
    def _marker_dragged(self, *_):
        if self._suppress_marker:
            return
        p = self.marker.pos()
        i = self.session.nearest_index(p.x(), p.y())
        if i is not None:
            self.seek_requested.emit(float(self.session.tt[i]))

    def set_marker_time(self, t: float):
        i = self.session.index_at_time(t)
        if i is None:
            return
        self._suppress_marker = True
        self.marker.setPos(pg.Point(float(self.session.tx[i]), float(self.session.ty[i])))
        self._suppress_marker = False

    # --------------------------------------------------------------- lap overlays
    def _refresh_best(self):
        """Draw the best lap as a faint thin reference line. Re-draws only when the best
        lap id actually changes (e.g. after the timing lines move)."""
        best = self.session.best_lap_id()
        if best == self._best_lap_id and self._best_overlay is not None:
            return
        if self._best_overlay is not None:
            self.plot.removeItem(self._best_overlay)
            self._best_overlay = None
        self._best_lap_id = best
        if best is None:
            return
        xs, ys = self.session.lap_trace_xy(best)
        self._best_overlay = self.plot.plot(xs, ys, pen=pg.mkPen(BEST_COLOR, width=1))

    def set_current_lap(self, lap_id):
        """Highlight the lap the video is currently in. No-op if it hasn't changed; a None
        id clears the highlight so only the faint best-lap reference remains."""
        # The best lap can change when timing lines move; keep its reference line current.
        self._refresh_best()
        if lap_id == self._current_lap_id:
            return
        self._current_lap_id = lap_id
        if self._current_overlay is not None:
            self.plot.removeItem(self._current_overlay)
            self._current_overlay = None
        if lap_id is None:
            return
        xs, ys = self.session.lap_trace_xy(lap_id)
        self._current_overlay = self.plot.plot(xs, ys, pen=pg.mkPen(CURRENT_COLOR, width=3))
