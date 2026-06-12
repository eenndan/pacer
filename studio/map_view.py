"""MapView: the track trace with draggable start/sector timing lines + a video marker.

Timing lines are drawn in LOCAL meters (same space as the trace). Each line is two
draggable pyqtgraph TargetItem handles joined by a segment; by DEFAULT handles are placed
FREELY (no snap to trace) and stay exactly where the user drops them — releasing a handle
re-segments the laps once. An OPT-IN "Snap" toggle in the map header (default off) snaps
just the RELEASED handle to the nearest trace point before that one re-segmentation; the
other endpoint never moves (the user may deliberately anchor it off-track). The red marker
tracks the video position and, when dragged, seeks the video to the nearest telemetry sample.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from . import theme
from .gapfill import GAP_TIME_S
from .session import Seg
from .theme import MAP_RAINBOW_N, C, icon, rainbow_colors

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

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
# Corner labels (F-corner): a subtle direction-coloured apex dot under each C-label. The two
# hues come from the theme's categorical chart spread (used here purely as hues — cyan for a
# left-hander, coral for a right-hander), dimmed by alpha so the labels never compete with
# the lap traces; the label text is the secondary text grey.
CORNER_LEFT_COLOR = theme.CHART_SERIES[1]    # cyan — left-handers
CORNER_RIGHT_COLOR = theme.CHART_SERIES[4]   # coral — right-handers
CORNER_DOT_ALPHA = 170                       # 0-255: subtle, under the text label
CORNER_LABEL_COLOR = C.text_dim


class _TimingLine:
    """Two draggable handles + a connecting segment, all in data (local-meter) coords."""

    def __init__(self, plot, seg: Seg, color, on_changed, snap):
        self.plot = plot
        self.on_changed = on_changed
        # `snap(x, y) -> (x, y) | None`: MapView's opt-in snap hook. None (the default —
        # toggle off) means free placement; a point means "move the released handle here".
        self.snap = snap
        pen = pg.mkPen(color, width=2)
        self.line = pg.PlotDataItem([seg.x1, seg.x2], [seg.y1, seg.y2], pen=pen)
        self.h1 = pg.TargetItem((seg.x1, seg.y1), size=11, movable=True, pen=pen)
        self.h2 = pg.TargetItem((seg.x2, seg.y2), size=11, movable=True, pen=pen)
        plot.addItem(self.line)
        plot.addItem(self.h1)
        plot.addItem(self.h2)
        # Free placement: dragging either handle redraws the segment as it moves; on release
        # the laps are re-segmented ONCE. Handles are NOT snapped to a trace point unless the
        # Snap toggle is on — by default they stay exactly where the user drops them.
        self.h1.sigPositionChanged.connect(self._moved)
        self.h2.sigPositionChanged.connect(self._moved)
        # TargetItem emits ITSELF on release, so _released knows which handle was dragged.
        self.h1.sigPositionChangeFinished.connect(self._released)
        self.h2.sigPositionChangeFinished.connect(self._released)

    def _released(self, handle):
        # On release: optionally snap the DRAGGED handle to the nearest trace point (opt-in,
        # default off — snap() returns None when the toggle is off), then re-segment the laps
        # ONCE. Only the released handle moves; the other endpoint stays where the user
        # anchored it. TargetItem.setPos fires sigPositionChanged only (a cheap segment
        # redraw via _moved) — never sigPositionChangeFinished, so no recursion here.
        p = handle.pos()
        snapped = self.snap(p.x(), p.y())
        if snapped is not None:
            handle.setPos(pg.Point(snapped[0], snapped[1]))
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


# --------------------------------------------------------------- rainbow map (F3)
# The current lap's line painted as a channel colour gradient (speed / Δ-vs-best). pyqtgraph has
# no per-vertex pen on a single curve, so the channel is QUANTIZED into MAP_RAINBOW_N (16) levels
# and each level's segments are drawn by ONE PlotCurveItem with that bucket's pen — NaN breaks +
# connect='finite' let a single item hold all of its bucket's disjoint runs. ≤16 items total,
# rebuilt ONLY on lap change / channel change / re-segment (never on the 30 Hz marker tick).
RAINBOW_WIDTH = 3  # same width as the current-lap overlay, so the painted line reads identically
# Header-button captions for the channel cycle (OFF → Speed → Δ-vs-best → OFF …).
_RAINBOW_LABELS = {"off": "Color: off", "speed": "Color: speed", "delta": "Color: Δ"}


def bucketize(values, n_buckets: int, lo: float | None = None, hi: float | None = None):
    """Quantize `values` into integer bucket ids 0..n_buckets-1 over [lo, hi] (default: the
    finite min/max of the values). Pure numpy. Index 0 is the LOW end (slow / losing → red),
    n_buckets-1 the HIGH end (fast / gaining → green) — matching theme.rainbow_colors order.

    Non-finite values map to -1 ("no bucket" — the renderer skips those segments: the NaN-break
    mechanism for GPS dropout gaps). A degenerate range (hi <= lo, e.g. a perfectly flat channel)
    puts every finite value in the MIDDLE bucket — when the channel carries no contrast, neither
    the red nor the green extreme tells a true story."""
    v = np.asarray(values, dtype=float)
    out = np.full(v.shape, -1, dtype=np.int64)
    finite = np.isfinite(v)
    if not finite.any():
        return out
    lo = float(np.min(v[finite])) if lo is None else float(lo)
    hi = float(np.max(v[finite])) if hi is None else float(hi)
    if hi <= lo:
        out[finite] = (n_buckets - 1) // 2
        return out
    idx = np.floor((v[finite] - lo) / (hi - lo) * n_buckets).astype(np.int64)
    out[finite] = np.clip(idx, 0, n_buckets - 1)  # v == hi lands exactly on n_buckets → clamp
    return out


def bucket_polylines(xs, ys, seg_buckets, n_buckets: int):
    """Group a polyline's SEGMENTS by bucket id into per-bucket draw arrays. Pure numpy.

    `seg_buckets[i]` is the bucket of the segment joining point i to point i+1 (so it has
    len(xs)-1 entries; -1 = skip that segment). Within a bucket, consecutive same-bucket
    segments share their joint point, and non-adjacent runs are separated by a single NaN so
    ONE PlotCurveItem(connect='finite') draws all of a bucket's disjoint runs without spurious
    connecting chords. Returns n_buckets (xs, ys) pairs (empty arrays for unused buckets)."""
    xs = np.asarray(xs, dtype=float)
    ys = np.asarray(ys, dtype=float)
    seg = np.asarray(seg_buckets)
    out = []
    for b in range(n_buckets):
        idx = np.flatnonzero(seg == b)
        if idx.size == 0:
            out.append((np.empty(0), np.empty(0)))
            continue
        runs = np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)
        bx: list = []
        by: list = []
        for r in runs:
            bx.extend((xs[r[0]:r[-1] + 2], [np.nan]))  # segments i..j -> points i..j+1
            by.extend((ys[r[0]:r[-1] + 2], [np.nan]))
        out.append((np.concatenate(bx[:-1]), np.concatenate(by[:-1])))  # drop the trailing NaN
    return out


def resample_grid_to_points(cum_dist, grid_values):
    """Resample a curve sampled on the UNIFORM normalized-distance grid [0, 1] (session.delta()'s
    400-point grid) onto a lap's per-point odometer distances: s_i = cum_i / cum_total, then one
    np.interp against the grid. Pure numpy — REUSES the already-computed grid values (the Δ is
    never recomputed here). Caller guarantees cum_dist[-1] > 0."""
    cum = np.asarray(cum_dist, dtype=float)
    g = np.asarray(grid_values, dtype=float)
    return np.interp(cum / cum[-1], np.linspace(0.0, 1.0, len(g)), g)


class _RainbowOverlay:
    """Owns the ≤MAP_RAINBOW_N PlotCurveItems of the rainbow (one per bucket, per-bucket pens
    from theme.rainbow_colors). Items are created lazily on first use and re-FILLED in place
    afterwards; `rebuilds` counts every fill so tests can assert the 30 Hz tick path never
    touches the bucket items. Holds no `pacer` types — fed plain numpy arrays."""

    def __init__(self, plot):
        self.plot = plot
        self._items: list | None = None  # created lazily on the first build (off by default)
        self.rebuilds = 0  # instrumentation for the perf-invariant tests (no rebuild per tick)

    def _ensure_items(self):
        if self._items is None:
            self._items = []
            for color in rainbow_colors(MAP_RAINBOW_N):
                it = pg.PlotCurveItem(pen=pg.mkPen(color, width=RAINBOW_WIDTH), connect="finite")
                it.setZValue(5)  # above the lap overlays, below the video marker (z=10)
                self.plot.addItem(it)
                self._items.append(it)
        return self._items

    def set_data(self, xs, ys, seg_buckets):
        """Fill every bucket item from the polyline + per-segment bucket ids (one rebuild)."""
        items = self._ensure_items()
        self.rebuilds += 1
        polylines = bucket_polylines(xs, ys, seg_buckets, len(items))
        for it, (bx, by) in zip(items, polylines, strict=True):
            it.setData(bx, by)

    def clear(self):
        if self._items is None:
            return
        for it in self._items:
            it.setData(np.empty(0), np.empty(0))


class _GradientStrip(QWidget):
    """The legend's colour bar: paints the EXACT bucket colours, low→high, edge to edge —
    legend == rendering, pen-for-pen."""

    def __init__(self, colors: list[QColor]):
        super().__init__()
        self._colors = colors
        self.setFixedHeight(8)

    def paintEvent(self, _event):
        p = QPainter(self)
        w = self.width() / len(self._colors)
        for i, c in enumerate(self._colors):
            p.fillRect(QRectF(i * w, 0.0, w + 1.0, float(self.height())), c)
        p.end()


class _RainbowLegend(QWidget):
    """Slim legend shown ONLY while a rainbow is painted: min label · bucket-colour strip ·
    max label (the channel's red/'slow-losing' and green/'fast-gaining' extremes)."""

    def __init__(self):
        super().__init__()
        self.lo_label = QLabel("")
        self.hi_label = QLabel("")
        for lab in (self.lo_label, self.hi_label):
            lab.setProperty("role", "BarLabel")  # the dimmed small header type from the QSS
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(8)
        lay.addWidget(self.lo_label)
        lay.addWidget(_GradientStrip([QColor(c) for c in rainbow_colors(MAP_RAINBOW_N)]), 1)
        lay.addWidget(self.hi_label)

    def set_labels(self, lo_text: str, hi_text: str):
        self.lo_label.setText(lo_text)
        self.hi_label.setText(hi_text)


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
        # F3: while the rainbow paints the lap, this overlay is HIDDEN in place (never rebuilt),
        # so toggling the rainbow off restores the exact same items/pens, byte-identical.
        self.visible = True

    def _clear(self):
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []

    def set_visible(self, on: bool):
        """Show/hide the existing items IN PLACE — no rebuild, no pen change. Items created
        later (a lap change while hidden) inherit the state via set_lap."""
        self.visible = on
        for it in self._items:
            it.setVisible(on)

    def set_lap(self, session: Session, lap_id: int | None):
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
            item = self.plot.plot(seg.xs, seg.ys, pen=pen)
            if not self.visible:
                item.setVisible(False)
            self._items.append(item)

    def refresh(self, session: Session):
        """Force a redraw of the current lap (e.g. after re-segmentation invalidated caches)."""
        lap_id, self.lap_id = self.lap_id, None
        self.set_lap(session, lap_id)


class _CornerMarkers:
    """Corner labels (F-corner): "C1…Cn" at each detected corner's apex position with a
    subtle direction-coloured dot (cyan = left, coral = right). SELF-CONTAINED overlay —
    it owns its plot items and rebuilds them wholesale from the (label, x, y, direction)
    tuples Session.corner_map_markers provides, touching nothing else in the scene. Pure
    display: rebuilt only when the corner set changes (load / re-segmentation), zero
    per-tick cost."""

    def __init__(self, plot):
        self.plot = plot
        self._items: list = []
        self._font = theme.mono_font(theme.CAPTION)

    def set_corners(self, markers):
        """(Re)build the labels from `markers`: a list of (label, x, y, direction) with the
        apex position in local metres and direction +1 = left / -1 = right. [] clears."""
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []
        if not markers:
            return
        for direction, colour in ((1, CORNER_LEFT_COLOR), (-1, CORNER_RIGHT_COLOR)):
            pts = [(x, y) for _label, x, y, d in markers if d == direction]
            if not pts:
                continue
            qc = pg.mkColor(colour)
            qc.setAlpha(CORNER_DOT_ALPHA)
            dots = pg.ScatterPlotItem(
                pos=pts, size=7, pen=None, brush=pg.mkBrush(qc), pxMode=True)
            dots.setZValue(5)  # above the lap traces, below the red video marker (z=10)
            self.plot.addItem(dots)
            self._items.append(dots)
        for label, x, y, _d in markers:
            text = pg.TextItem(text=label, color=CORNER_LABEL_COLOR, anchor=(0.5, 1.25))
            text.setFont(self._font)
            text.setPos(float(x), float(y))
            text.setZValue(6)
            self.plot.addItem(text)
            self._items.append(text)


class MapView(QWidget):
    # (start: Seg, sectors: list[Seg]) whenever a handle moves or sectors change.
    timing_lines_changed = Signal(object, object)

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._suppress_marker = False
        self._current_lap: int | None = None  # F3: scope the marker drag to this lap
        # Marker-drag seek coalescing: a drag fires sigPositionChanged on every mouse-move, each of
        # which used to emit a (costly) video seek. Instead we stash the latest dragged time here and
        # let the app's 30 Hz tick drain ONE seek per tick via take_marker_seek() — mirroring the
        # plot-scrub coalescing. None = nothing pending.
        self._marker_seek_target: float | None = None

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

        # Corner labels (F-corner): a self-contained overlay; the app pushes the apex
        # markers via set_corners (on load and after a re-segmentation recomputes them).
        self._corner_markers = _CornerMarkers(self.plot)

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
        # OPT-IN snap-to-track toggle (default OFF = today's free placement, byte-identical).
        # When checked, releasing a dragged timing-line handle snaps THAT handle to the nearest
        # point on the track trace before the one re-segmentation. Exposed like the sector
        # buttons so app.py mounts it in the map header. (The early snap-as-DEFAULT experiment
        # was rejected — free placement stays the default; this is the PLAN-sanctioned toggle.)
        self.snap_btn = QPushButton("Snap")
        self.snap_btn.setIcon(icon("ph.magnet"))
        self.snap_btn.setCheckable(True)
        self.snap_btn.setToolTip(
            "Snap to track: when on, a released timing-line handle jumps to the nearest point "
            "on the track trace. Off (default) = handles stay exactly where you drop them.")
        # Recolour the glyph to the accent while active (the QSS already tints the button
        # background on :checked) — same idiom as the g-meter toggle.
        self.snap_btn.toggled.connect(
            lambda on: self.snap_btn.setIcon(icon("ph.magnet", color=C.accent if on else C.text)))

        # F3 rainbow track map: ONE header button cycling OFF → Speed → Δ-vs-best. Exposed like
        # the sector buttons so app.py mounts it in the map header. While ON, the current lap's
        # line is painted as a channel colour gradient (_RainbowOverlay) and the normal overlay is
        # HIDDEN in place; OFF restores the exact pre-toggle items/pens (byte-identical — nothing
        # was rebuilt). The bucket items rebuild only on lap/channel change or re-segment.
        self._rainbow = _RainbowOverlay(self.plot)
        self._rainbow_mode = "off"  # "off" | "speed" | "delta" (the cycle order below)
        self.rainbow_btn = QPushButton(_RAINBOW_LABELS["off"])
        self.rainbow_btn.setIcon(icon("ph.palette"))
        self.rainbow_btn.setToolTip(
            "Paint the current lap's line by a channel: off → speed (red = slow, green = fast) "
            "→ Δ vs best (red = losing, green = gaining). The faint best-lap reference is "
            "unchanged.")
        self.rainbow_btn.clicked.connect(self._cycle_rainbow)
        self._legend = _RainbowLegend()
        self._legend.setVisible(False)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget, 1)
        lay.addWidget(self._legend)

    # ----------------------------------------------------------- timing lines
    def _rebuild(self, start: Seg, sectors: list[Seg]):
        for tl in [self._start, *self._sectors]:
            if tl:
                tl.remove()
        self._start = _TimingLine(self.plot, start, START_COLOR, self._emit, self._snap_to_trace)
        self._sectors = [_TimingLine(self.plot, s, SECTOR_COLOR, self._emit, self._snap_to_trace)
                         for s in sectors]

    def _snap_to_trace(self, x: float, y: float) -> tuple[float, float] | None:
        """The snap hook handed to every _TimingLine. Toggle OFF (default): return None so the
        released handle stays exactly where it was dropped. Toggle ON: the nearest point ON the
        track trace (local meters) via session.nearest_index — pure numpy in session, so this
        module stays pacer-free."""
        if not self.snap_btn.isChecked():
            return None
        i = self.session.nearest_index(x, y)
        if i is None:
            return None
        return float(self.session.tx[i]), float(self.session.ty[i])

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
        # Single-source the emit through _emit(), which re-reads the just-rebuilt timing
        # lines as the authoritative source (identical to the start/sectors we rebuilt from).
        self._emit()

    def _reset_sectors(self):
        start, _ = self._current()
        self._rebuild(start, [])
        self._emit()

    # --------------------------------------------------------------- video sync
    def _marker_dragged(self, *_):
        # F3: constrain the drag to the CURRENT lap's trace so the marker can't snap to another
        # lap where laps overlap spatially. The seek (and thus the marker's re-placement via
        # set_playhead_time) is clamped to that lap's time window, so the drag scrubs smoothly
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
            # Coalesce: stash the latest dragged time; the app's tick drains ONE seek per tick. (A
            # fast drag fired many seeks/sec before — now at most one per ~33 ms tick.)
            self._marker_seek_target = t

    def take_marker_seek(self) -> float | None:
        """Return + consume the latest pending marker-drag seek time (None if none). The app polls
        this each tick so a marker drag fires at most ONE video seek per tick, not per mouse-move."""
        t, self._marker_seek_target = self._marker_seek_target, None
        return t

    def set_marker_index(self, i: int | None):
        """Place the marker at trace index `i` (None = no-op). The app resolves index_at_time(t)
        ONCE per tick and passes the index here, so the marker placement reuses the same search the
        speed readout uses instead of re-running index_at_time inside set_playhead_time."""
        if i is None:
            return
        self._suppress_marker = True
        self.marker.setPos(pg.Point(float(self.session.tx[i]), float(self.session.ty[i])))
        self._suppress_marker = False

    def set_playhead_time(self, t: float):
        # Used by the scrub path (single drag-driven time); resolves the index itself.
        # Shared playhead-setter verb with PlotsView.set_playhead_time.
        self.set_marker_index(self.session.index_at_time(t))

    # --------------------------------------------------------------- rainbow (F3)
    def _cycle_rainbow(self):
        """Header-button click: advance the channel cycle and re-apply the rendering."""
        order = ("off", "speed", "delta")
        self._rainbow_mode = order[(order.index(self._rainbow_mode) + 1) % len(order)]
        self.rainbow_btn.setText(_RAINBOW_LABELS[self._rainbow_mode])
        self._apply_rainbow()

    def _apply_rainbow(self):
        """(Re)build or clear the rainbow for the current lap + mode — the ONLY path that touches
        the bucket items. Called on toggle, on a current-lap CHANGE, and after a re-segment; the
        per-tick marker path never reaches it (set_current_lap gates on an actual lap change).
        When nothing is painted (mode off / no current lap / channel unavailable) the normal
        overlay is shown — its items were only hidden, so they return byte-identical."""
        painted = False
        if self._rainbow_mode != "off" and self._current_lap is not None:
            painted = self._build_rainbow(self._current_lap, self._rainbow_mode)
        if not painted:
            self._rainbow.clear()
        self._legend.setVisible(painted)
        self._current_overlay.set_visible(not painted)

    def _build_rainbow(self, lap_id: int, mode: str) -> bool:
        """Fill the bucket items for `lap_id`'s channel. Returns False (nothing painted) when the
        channel can't be computed (degenerate lap, no best lap for Δ). Data comes from the cached
        bulk lap-columns fetch + the EXISTING 400-grid delta() — nothing is recomputed."""
        times, xs, ys, speed_kmh, cum = self.session.lap_channels(lap_id)
        if len(xs) < 2:
            return False
        if mode == "speed":
            vals = speed_kmh
            lo_txt = f"{float(np.min(vals)):.0f}"
            hi_txt = f"{float(np.max(vals)):.0f} km/h"
        else:  # Δ-vs-best, resampled from the 400-grid delta() onto this lap's point distances
            got = self.session.delta([lap_id])
            if got is None or lap_id not in got[2] or float(cum[-1]) <= 0:
                return False
            d_pts = resample_grid_to_points(cum, got[2][lap_id][1])
            # NEGATED: ahead = negative Δ must land in the HIGH (green / 'gaining') buckets.
            vals = -d_pts
            # Legend shows the actual Δ at each end: red end = the most-behind Δ, green end =
            # the most-ahead Δ (signed seconds, matching the Δ readout convention).
            lo_txt = f"{-float(np.min(vals)):+.2f} s"
            hi_txt = f"{-float(np.max(vals)):+.2f} s"
        # Per-SEGMENT value = mean of its endpoints; segments spanning an interior GPS dropout
        # (sample step > the gap threshold) get NaN → bucket -1 → not painted: the rainbow must
        # not draw a corner-cutting chord across a hole as if it were measured.
        seg_vals = 0.5 * (vals[:-1] + vals[1:])
        seg_vals = np.where(np.diff(times) > GAP_TIME_S, np.nan, seg_vals)
        self._rainbow.set_data(xs, ys, bucketize(seg_vals, MAP_RAINBOW_N))
        self._legend.set_labels(lo_txt, hi_txt)
        return True

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
        changed = lap_id != self._current_lap
        self._current_lap = lap_id  # F3: the lap the marker drag is constrained to
        self._current_overlay.set_lap(self.session, lap_id)
        # Rainbow: rebuild the bucket items ONLY on an actual lap change. This method runs every
        # 30 Hz tick with an unchanged lap — that path must not touch the rainbow.
        if changed and self._rainbow_mode != "off":
            self._apply_rainbow()

    def refresh_overlays(self):
        """Force both lap overlays to redraw from the session — call after the timing lines
        move (re-segmentation shifts lap ids and clears the session's per-lap segment cache,
        so the cached drawings are stale even when the lap id is nominally unchanged)."""
        self._best_lap_id = None
        self._refresh_best()
        self._current_overlay.refresh(self.session)
        # Re-segmentation invalidated the channel arrays too — rebuild the painted rainbow.
        if self._rainbow_mode != "off":
            self._apply_rainbow()

    # ------------------------------------------------------------- corner labels (F-corner)
    def set_corners(self, markers):
        """Show corner labels at the given (label, x, y, direction) apex markers (from
        Session.corner_map_markers; [] clears). Pushed by the app so this view stays a pure
        consumer — on load and again after a timing-line edit recomputes the corner set."""
        self._corner_markers.set_corners(markers)
