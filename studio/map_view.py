"""MapView: the track trace with draggable start/sector timing lines + a video marker.

Timing lines are drawn in LOCAL meters (same space as the trace). Each line is two
draggable pyqtgraph TargetItem handles joined by a segment; handles are placed FREELY
(no snap to trace) and stay exactly where the user drops them — releasing a handle
re-segments the laps once. The red marker tracks the video position and, when dragged,
seeks the video to the nearest telemetry sample.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QPushButton, QVBoxLayout, QWidget

from .session import Seg
from .theme import C

# Tokenized track-map pens (Phase 2). The best lap is a quiet faint reference; the current lap
# is the bright amber accent so the racing line pops. Timing lines + marker use distinct tokens.
START_COLOR = C.accent              # start/finish line — accent so it's the clear anchor
SECTOR_COLOR = C.text_dim           # sector lines — visible but quieter than the start line
BEST_COLOR = C.text_muted           # quiet reference line for the best lap (legible, not loud)
CURRENT_COLOR = C.accent            # highlighted current-lap trace (the racing line — pops)
MARKER_COLOR = C.behind             # video position marker — warm coral, reads on the trace
_MARKER_RGB = QColor(C.behind)      # for the translucent marker brush below
# Reconstructed (inferred) gap-fill segments are drawn DASHED + DIMMED so they read as
# clearly distinct from measured GPS — the user must always be able to tell them apart.
INFERRED_DASH = [5, 5]  # on/off dash pattern (px)
INFERRED_ALPHA = 130    # 0-255; dimmer than the measured pen
INFERRED_DARKEN = 0.55  # blend the lap colour toward black for the fill pen


class _TimingLine:
    """Two draggable handles + a connecting segment, all in data (local-meter) coords."""

    def __init__(self, plot, seg: Seg, color, on_changed, session):
        self.plot = plot
        self.on_changed = on_changed
        self.session = session
        pen = pg.mkPen(color, width=2)
        self.line = pg.PlotDataItem([seg.x1, seg.x2], [seg.y1, seg.y2], pen=pen)
        self.h1 = pg.TargetItem((seg.x1, seg.y1), size=11, movable=True, pen=pen)
        self.h2 = pg.TargetItem((seg.x2, seg.y2), size=11, movable=True, pen=pen)
        plot.addItem(self.line)
        plot.addItem(self.h1)
        plot.addItem(self.h2)
        # Free placement: dragging either handle redraws the segment as it moves; on release
        # the laps are re-segmented ONCE. Handles are NOT snapped to a trace point — they stay
        # exactly where the user drops them.
        self.h1.sigPositionChanged.connect(self._moved)
        self.h2.sigPositionChanged.connect(self._moved)
        self.h1.sigPositionChangeFinished.connect(self._released)
        self.h2.sigPositionChangeFinished.connect(self._released)

    def _released(self, *_):
        # On release: re-segment the laps ONCE, leaving the handle exactly where dropped.
        self.on_changed()

    def _moved(self, *_):
        # Fires continuously while a handle is dragged — only redraw the segment (cheap).
        # Lap re-segmentation (laps.update over ~16k points) is deferred to release, so the
        # drag stays smooth instead of re-segmenting on every mouse-move tick.
        p1, p2 = self.h1.pos(), self.h2.pos()
        self.line.setData([p1.x(), p2.x()], [p1.y(), p2.y()])

    def seg(self) -> Seg:
        p1, p2 = self.h1.pos(), self.h2.pos()
        return Seg(p1.x(), p1.y(), p2.x(), p2.y())

    def remove(self):
        for item in (self.line, self.h1, self.h2):
            self.plot.removeItem(item)


def _inferred_pen(color, base_width):
    """A dashed, dimmed, thinner pen for reconstructed (inferred) gap-fill segments — visibly
    distinct from the solid measured pen so real GPS and reconstruction are never confused."""
    qc = pg.mkColor(color)
    qc = qc.darker(int(100 / INFERRED_DARKEN))  # toward black
    qc.setAlpha(INFERRED_ALPHA)
    pen = pg.mkPen(qc, width=max(base_width - 1, 1))
    pen.setStyle(Qt.DashLine)
    pen.setDashPattern(INFERRED_DASH)
    return pen


class _LapOverlay:
    """Draws ONE lap as a group of plot items: solid measured runs + dashed/dimmed inferred
    gap-fills (`session.lap_trace_segments`). Tracks its items so it can clear/redraw without
    disturbing the rest of the scene. Holds NO `pacer` types — just numpy arrays from session."""

    def __init__(self, plot, color, base_width):
        self.plot = plot
        self.color = color
        self.base_width = base_width
        self.lap_id = None
        self._items: list = []

    def _clear(self):
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []

    def set_lap(self, session, lap_id):
        """(Re)draw `lap_id` (or clear if None). No-op if unchanged."""
        if lap_id == self.lap_id and self._items:
            return
        self._clear()
        self.lap_id = lap_id
        if lap_id is None:
            return
        solid = pg.mkPen(self.color, width=self.base_width)
        dashed = _inferred_pen(self.color, self.base_width)
        for seg in session.lap_trace_segments(lap_id):
            pen = solid if seg.measured else dashed
            self._items.append(self.plot.plot(seg.xs, seg.ys, pen=pen))

    def refresh(self, session):
        """Force a redraw of the current lap (e.g. after re-segmentation invalidated caches)."""
        lap_id, self.lap_id = self.lap_id, None
        self.set_lap(session, lap_id)


class MapView(QWidget):
    # (start: Seg, sectors: list[Seg]) whenever a handle moves or sectors change.
    timing_lines_changed = Signal(object, object)
    seek_requested = Signal(float)  # seconds

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._suppress_marker = False
        self._current_lap: int | None = None  # F3: scope the marker drag to this lap

        self.widget = pg.PlotWidget()
        self.plot = self.widget.getPlotItem()
        self.plot.setAspectLocked(True)  # equal aspect -> a true-shape track map
        # A track map is a SHAPE, not a chart — the meter ticks/labels/grid add noise and carry no
        # useful info for reading a racing line. Hide the axes entirely for the cleanest read; the
        # trace + start/sector lines + marker are all that should show on the C.surface background.
        self.plot.showGrid(x=False, y=False)
        for side in ("left", "bottom", "top", "right"):
            self.plot.hideAxis(side)
        # With the axes hidden, drop the PlotItem's default content margins so the (aspect-locked)
        # track fills the panel edge-to-edge — no wasted chrome gutter around a shape-only map.
        self.plot.layout.setContentsMargins(0, 0, 0, 0)
        self.plot.setContentsMargins(0, 0, 0, 0)
        # The full ~16k-point trace is no longer drawn (jagged + slow). Instead we draw at most
        # the best lap (faint reference) and the current lap (highlighted) — a few hundred points.
        # Each lap is drawn as measured (solid) + reconstructed (dashed/dimmed) segments, so GPS
        # dropouts no longer show as straight chords across the hole.
        self._best_overlay = _LapOverlay(self.plot, BEST_COLOR, base_width=1)
        self._best_lap_id: int | None = None
        self._current_overlay = _LapOverlay(self.plot, CURRENT_COLOR, base_width=3)

        # Freeze the view to the track bbox so marker moves never trigger autorange / a full
        # re-render. The user's pan/zoom still works; the track stays fully visible on load.
        if len(session.tx) and len(session.ty):
            x_lo, x_hi = float(session.tx.min()), float(session.tx.max())
            y_lo, y_hi = float(session.ty.min()), float(session.ty.max())
            # Tight padding (2%) so the aspect-locked track is drawn as LARGE as possible in the
            # now-shorter map panel (the right column rebalanced to give the charts the majority).
            # Aspect lock still letterboxes to the track's true shape; a small pad just keeps the
            # start/sector handles from sitting flush against the panel edge.
            px = max(x_hi - x_lo, 1.0) * 0.02
            py = max(y_hi - y_lo, 1.0) * 0.02
            vb = self.plot.getViewBox()
            vb.setRange(xRange=(x_lo - px, x_hi + px), yRange=(y_lo - py, y_hi + py), padding=0)
            vb.disableAutoRange()

        self.marker = pg.TargetItem(
            (session.tx[0] if len(session.tx) else 0, session.ty[0] if len(session.ty) else 0),
            size=15, movable=True, pen=pg.mkPen(MARKER_COLOR, width=2),
            brush=pg.mkBrush(_MARKER_RGB.red(), _MARKER_RGB.green(), _MARKER_RGB.blue(), 110),
        )
        self.plot.addItem(self.marker)
        self.marker.setZValue(10)  # keep the marker above the lap overlays
        self.marker.sigPositionChanged.connect(self._marker_dragged)

        self._start: _TimingLine | None = None
        self._sectors: list[_TimingLine] = []
        self._rebuild(session.start_line, session.sector_lines)
        self._refresh_best()

        # The sector controls are EXPOSED (not placed here) so app.py can mount them compactly,
        # right-aligned, in the MAP panel's header row — reclaiming the full-width button row that
        # used to sit between the map and the charts. Their handlers/signal wiring are unchanged.
        self.add_sector_btn = QPushButton("Add sector")
        self.reset_sectors_btn = QPushButton("Reset sectors")
        self.add_sector_btn.clicked.connect(self._add_sector)
        self.reset_sectors_btn.clicked.connect(self._reset_sectors)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget, 1)

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
        # Pass the count of existing sectors so each suggestion lands at a DISTINCT track
        # position (evenly subdividing the lap); two identical lines would collapse a split.
        sectors.append(self.session.suggest_sector(len(sectors)))
        self._rebuild(start, sectors)
        self.timing_lines_changed.emit(start, sectors)

    def _reset_sectors(self):
        start, _ = self._current()
        self._rebuild(start, [])
        self.timing_lines_changed.emit(start, [])

    # --------------------------------------------------------------- video sync
    def _marker_dragged(self, *_):
        # F3: constrain the drag to the CURRENT lap's trace so the marker can't snap to another
        # lap where laps overlap spatially. The seek (and thus the marker's re-placement via
        # set_marker_time) is clamped to that lap's time window, so the drag scrubs smoothly
        # within the one lap and never jumps. Outside any valid lap (lead-in) there's no current
        # lap — fall back to the whole-trace nearest so the marker is still draggable there.
        if self._suppress_marker:
            return
        p = self.marker.pos()
        t = None
        if self._current_lap is not None:
            t = self.session.nearest_time_in_lap(self._current_lap, p.x(), p.y())
        if t is None:
            i = self.session.nearest_index(p.x(), p.y())
            t = float(self.session.tt[i]) if i is not None else None
        if t is not None:
            self.seek_requested.emit(t)

    def set_marker_time(self, t: float):
        i = self.session.index_at_time(t)
        if i is None:
            return
        self._suppress_marker = True
        self.marker.setPos(pg.Point(float(self.session.tx[i]), float(self.session.ty[i])))
        self._suppress_marker = False

    # --------------------------------------------------------------- lap overlays
    def _refresh_best(self):
        """Draw the best lap as a faint thin reference line (measured solid + inferred dashed).
        Re-draws only when the best lap id actually changes (e.g. after the timing lines move).
        When the best lap changes, its per-lap segment cache may also be stale, so force a
        redraw of the current overlay too."""
        best = self.session.best_lap_id()
        if best == self._best_lap_id and self._best_overlay.lap_id is not None:
            return
        self._best_lap_id = best
        self._best_overlay.set_lap(self.session, best)

    def set_current_lap(self, lap_id):
        """Highlight the lap the video is currently in (measured solid + inferred dashed/dimmed).
        No-op if it hasn't changed; a None id clears the highlight so only the faint best-lap
        reference remains."""
        # The best lap can change when timing lines move; keep its reference line current.
        self._refresh_best()
        self._current_lap = lap_id  # F3: the lap the marker drag is constrained to
        self._current_overlay.set_lap(self.session, lap_id)

    def refresh_overlays(self):
        """Force both lap overlays to redraw from the session — call after the timing lines
        move (re-segmentation shifts lap ids and clears the session's per-lap segment cache,
        so the cached drawings are stale even when the lap id is nominally unchanged)."""
        self._best_lap_id = None
        self._refresh_best()
        self._current_overlay.refresh(self.session)
