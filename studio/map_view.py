"""MapView: the track-shape trace with draggable start/sector timing lines, a draggable
video-position marker, and overlays (rainbow line, corner labels, brake glyphs, compare ghost).

All geometry is in local metres (same space as the trace). It holds no `pacer` types — the app
feeds it numpy arrays/markers. The compare ghost exists only during compare mode.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .map_render import (
    bucket_polylines,
    bucketize,  # noqa: F401  (re-exported for tests importing from map_view)
    rainbow_channel,
    resample_grid_to_points,  # noqa: F401  (re-exported for tests importing from map_view)
)
from .session import Seg
from .theme import CHART_SERIES, MAP_RAINBOW_N, C, icon, rainbow_colors

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# Track-map pens: best = quiet faint reference, current = bright amber accent.
START_COLOR = C.accent              # start/finish line — accent so it's the clear anchor
SECTOR_COLOR = C.text_dim           # sector lines — visible but quieter than the start line
# Best lap = quiet reference (secondary grey, width 1.5 so the track shape still reads); current
# lap at width 3 stays the emphasis.
BEST_COLOR = C.text_dim
BEST_WIDTH = 1.5
CURRENT_COLOR = C.accent            # highlighted current-lap trace (the racing line — pops)
MARKER_COLOR = C.behind             # video position marker — warm coral, reads on the trace
_MARKER_RGB = QColor(C.behind)      # for the translucent marker brush below
# Compare ghost = lap-B accent (cyan), the canonical "other lap" colour.
GHOST_COLOR = CHART_SERIES[1]
# Inferred gap-fill segments are drawn dashed + dimmed so they read as distinct from measured GPS.
INFERRED_DASH = [5, 5]  # on/off dash pattern (px)
INFERRED_ALPHA = 130    # 0-255; dimmer than the measured pen
INFERRED_DARKEN = 0.55  # blend the lap colour toward black for the fill pen
CORNER_LEFT_COLOR = theme.CHART_SERIES[1]    # cyan — left-handers
CORNER_RIGHT_COLOR = theme.CHART_SERIES[4]   # coral — right-handers
CORNER_DOT_ALPHA = 170                       # 0-255: subtle, under the text label
# Corner C# labels: near-primary text on a dark halo plate, nudged outward from the corner-cloud
# centroid, greedily px-box de-collided (see set_corners).
CORNER_LABEL_COLOR = C.text                   # near-primary so the label reads over the surface
CORNER_LABEL_HALO = QColor(C.surface)         # dark translucent plate behind the glyphs
CORNER_LABEL_HALO.setAlpha(190)
CORNER_LABEL_OFFSET_PX = 14                    # px the label is nudged outward from the centroid
# Generous px box for the greedy overlap test (no per-frame metrics; labels are static once built).
CORNER_LABEL_BOX_PX = (22.0, 16.0)
# Click-to-locate cue: a hollow accent ring slightly larger than the apex dot.
CORNER_HIGHLIGHT_PEN_W = 2
CORNER_HIGHLIGHT_SIZE = 18
# Brake glyphs (F5): a ▼ at each braking-zone onset; size ramps peak decel (g) via
# theme.brake_glyph_size (shared with the speed chart).


class _TimingLine:
    """Two draggable handles + a connecting segment, all in data (local-meter) coords."""

    def __init__(self, plot, seg: Seg, color, on_changed, snap):
        self.plot = plot
        self.on_changed = on_changed
        # snap(x,y)->(x,y)|None: opt-in snap hook; None (toggle off) = free placement. See _snap_to_trace.
        self.snap = snap
        pen = pg.mkPen(color, width=2)
        self.line = pg.PlotDataItem([seg.x1, seg.x2], [seg.y1, seg.y2], pen=pen)
        self.h1 = pg.TargetItem((seg.x1, seg.y1), size=11, movable=True, pen=pen)
        self.h2 = pg.TargetItem((seg.x2, seg.y2), size=11, movable=True, pen=pen)
        plot.addItem(self.line)
        plot.addItem(self.h1)
        plot.addItem(self.h2)
        # Drag redraws the segment live (_moved); release re-segments once (_released, which emits
        # the handle). TargetItem emits itself on release so _released knows which handle moved.
        self.h1.sigPositionChanged.connect(self._moved)
        self.h2.sigPositionChanged.connect(self._moved)
        self.h1.sigPositionChangeFinished.connect(self._released)
        self.h2.sigPositionChangeFinished.connect(self._released)

    def _released(self, handle):
        # Optionally snap the dragged handle (snap()=None when toggle off), then re-segment once.
        # setPos fires sigPositionChanged (cheap _moved redraw), NOT ...ChangeFinished — so no recursion.
        p = handle.pos()
        snapped = self.snap(p.x(), p.y())
        if snapped is not None:
            handle.setPos(pg.Point(snapped[0], snapped[1]))
        self.on_changed()

    def _moved(self, *_):
        # Live segment redraw during drag (cheap); re-segmentation is deferred to release.
        p1, p2 = self.h1.pos(), self.h2.pos()
        self.line.setData([p1.x(), p2.x()], [p1.y(), p2.y()])

    def seg(self) -> Seg:
        p1, p2 = self.h1.pos(), self.h2.pos()
        return Seg(p1.x(), p1.y(), p2.x(), p2.y())

    def remove(self):
        for item in (self.line, self.h1, self.h2):
            self.plot.removeItem(item)


def _inferred_pen(color, base_width):
    """Dashed/dimmed/thinner pen for inferred gap-fill segments (distinct from measured GPS)."""
    qc = pg.mkColor(color)
    qc = qc.darker(int(100 / INFERRED_DARKEN))  # toward black
    qc.setAlpha(INFERRED_ALPHA)
    pen = pg.mkPen(qc, width=max(base_width - 1, 1))
    pen.setStyle(Qt.DashLine)
    pen.setDashPattern(INFERRED_DASH)
    return pen


# --------------------------------------------------------------- rainbow map (F3)
# F3 rainbow: pyqtgraph has no per-vertex pen, so the channel (speed / Δ-vs-best) is quantized
# into MAP_RAINBOW_N buckets, one PlotCurveItem per bucket. Rebuilt only on lap/channel/segment change.
RAINBOW_WIDTH = 3  # same width as the current-lap overlay, so the painted line reads identically
# Cycle order for the channel control: off → speed → Δ → grip → off (kept for the cycle API the
# tests drive; the labelled combo lists the SAME modes, so no channel is hidden behind a blind cycle).
_RAINBOW_ORDER = ("off", "speed", "delta", "grip")
# Short, legible per-channel labels for the map-header dropdown (each channel visible + one click),
# replacing the old blind-cycle button captions (where Grip was an undiscoverable 4th step).
_RAINBOW_COMBO_LABELS = {"off": "Line: Off", "speed": "Line: Speed", "delta": "Line: Δ to best",
                         "grip": "Line: Grip (est)"}
# The per-channel rainbow value/bucket math (incl. the grip fixed scale + Δ/grip negation + the
# GPS-dropout NaN-mask) lives in the Qt-free studio/map_render.py (rainbow_channel + helpers).


class _RainbowOverlay:
    """Owns the ≤MAP_RAINBOW_N PlotCurveItems of the rainbow (one per bucket). Items are created
    lazily and re-filled in place afterwards; `rebuilds` counts every fill so tests can assert the
    30 Hz tick path never touches the bucket items."""

    def __init__(self, plot):
        self.plot = plot
        self._items: list | None = None  # created lazily on the first build (off by default)
        self.rebuilds = 0  # instrumentation for the perf-invariant tests (no rebuild per tick)

    def _ensure_items(self):
        if self._items is None:
            self._items = []
            for color in rainbow_colors(MAP_RAINBOW_N):
                it = pg.PlotCurveItem(pen=pg.mkPen(color, width=RAINBOW_WIDTH), connect="finite")
                it.setZValue(5)  # above lap overlays, below the marker (z=10)
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


# --------------------------------------------------------------- map key/legend (C3)
_LEGEND_ROW_H = 18        # px per key row
_LEGEND_GLYPH_W = 22      # px column reserved for the glyph
_LEGEND_PAD = 8           # px inner padding of the plate
_LEGEND_GAP = 6           # px between the glyph column and its label


class _MapLegend(QWidget):
    """A small collapsible key for the map's glyphs, anchored over the plot's bottom-left. Click
    the header to collapse to just the title. The glyph cells are painted to match the real
    markers; labels are plain language."""

    # Each row: (kind, label). `kind` selects the painter below.
    _ROWS = (
        ("marker", "Video position"),
        ("brake", "Brake point"),
        ("corner", "Corner apex (C#)"),
        ("start", "Drag = start / sector line"),
    )

    def __init__(self, on_resize=None):
        super().__init__()
        self._collapsed = False
        self._on_resize = on_resize  # MapView re-pins the key when collapse changes its height
        self._font = theme.ui_font(theme.CAPTION)
        self._title_font = theme.ui_font(theme.PANEL_HEADER, theme.W_SEMIBOLD)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self.setCursor(Qt.PointingHandCursor)
        self._relayout()

    def _relayout(self):
        rows = 0 if self._collapsed else len(self._ROWS)
        # Fixed width sized to the widest label + glyph column.
        self._w = 196
        self._h = _LEGEND_PAD * 2 + _LEGEND_ROW_H + rows * _LEGEND_ROW_H
        self.setFixedSize(self._w, self._h)

    def mousePressEvent(self, _event):
        # Click anywhere on the key toggles collapse — the whole plate is the affordance.
        self._collapsed = not self._collapsed
        self._relayout()
        if self._on_resize is not None:  # the plate changed height — re-pin it to the corner
            self._on_resize()
        self.update()

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        # The plate: dim translucent surface + a hairline border (theme tokens), rounded.
        plate = QColor(C.surface)
        plate.setAlpha(214)
        p.setBrush(QBrush(plate))
        p.setPen(QPen(QColor(C.border), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, self._w - 1, self._h - 1), 6, 6)
        p.setFont(self._title_font)
        p.setPen(QPen(QColor(C.text_dim)))
        caret = "▾" if not self._collapsed else "▸"
        p.drawText(QRectF(_LEGEND_PAD, _LEGEND_PAD, self._w - 2 * _LEGEND_PAD, _LEGEND_ROW_H),
                   int(Qt.AlignVCenter | Qt.AlignLeft), f"{caret}  Map key")
        if self._collapsed:
            p.end()
            return
        p.setFont(self._font)
        y = _LEGEND_PAD + _LEGEND_ROW_H
        for kind, label in self._ROWS:
            cell = QRectF(_LEGEND_PAD, y, _LEGEND_GLYPH_W, _LEGEND_ROW_H)
            self._paint_glyph(p, kind, cell)
            p.setPen(QPen(QColor(C.text_dim)))
            p.setFont(self._font)
            lx = _LEGEND_PAD + _LEGEND_GLYPH_W + _LEGEND_GAP
            p.drawText(QRectF(lx, y, self._w - lx - _LEGEND_PAD, _LEGEND_ROW_H),
                       int(Qt.AlignVCenter | Qt.AlignLeft), label)
            y += _LEGEND_ROW_H
        p.end()

    def _paint_glyph(self, p: QPainter, kind: str, cell: QRectF):
        """Draw one key glyph centred in `cell`, mirroring the on-map marker for that kind."""
        cx, cy = cell.center().x(), cell.center().y()
        if kind == "marker":  # filled coral ring — the video position marker
            mc = QColor(MARKER_COLOR)
            p.setPen(QPen(mc, 2))
            fill = QColor(MARKER_COLOR)
            fill.setAlpha(110)
            p.setBrush(QBrush(fill))
            p.drawEllipse(QPointF(cx, cy), 5, 5)
        elif kind == "brake":  # down-triangle (▼) — brake-point glyph
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(QColor(MARKER_COLOR)))
            tri = QPolygonF([QPointF(cx - 5, cy - 4), QPointF(cx + 5, cy - 4),
                             QPointF(cx, cy + 5)])
            p.drawPolygon(tri)
        elif kind == "corner":  # cyan apex dot (the left/right hues collapse to one in the key)
            qc = QColor(CORNER_LEFT_COLOR)
            qc.setAlpha(CORNER_DOT_ALPHA)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(qc))
            p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
        elif kind == "start":  # amber crosshair — the draggable start/sector handle
            p.setPen(QPen(QColor(START_COLOR), 1.5))
            p.drawLine(QPointF(cx - 5, cy), QPointF(cx + 5, cy))
            p.drawLine(QPointF(cx, cy - 5), QPointF(cx, cy + 5))


class _LapOverlay:
    """Draws one lap as solid measured + dashed inferred gap-fill items; tracks them for clear/redraw."""

    def __init__(self, plot, color, base_width):
        self.plot = plot
        self.color = color
        self.base_width = base_width
        self.lap_id = None
        self._items: list = []
        # Hidden in place (not rebuilt) while the rainbow paints the lap, so toggling it off restores
        # the same items/pens.
        self.visible = True

    def _clear(self):
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []

    def set_visible(self, on: bool):
        """Show/hide the existing items in place — no rebuild. Items created later inherit the
        state via set_lap."""
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

    def set_polyline(self, xs, ys, key):
        """(Re)draw one solid polyline (the F7 cross-recording reference ring). `key` gates redraws
        like set_lap's lap_id; honours the hidden-while-rainbow state."""
        if key == self.lap_id and self._items:
            return
        self._clear()
        self.lap_id = key
        if xs is None or len(xs) < 2:
            return
        item = self.plot.plot(np.asarray(xs), np.asarray(ys),
                              pen=pg.mkPen(self.color, width=self.base_width))
        if not self.visible:
            item.setVisible(False)
        self._items.append(item)

    def refresh(self, session: Session):
        """Force a redraw of the current lap (e.g. after re-segmentation invalidated caches)."""
        lap_id, self.lap_id = self.lap_id, None
        self.set_lap(session, lap_id)


class _CornerMarkers:
    """Corner C# labels + direction-coloured apex dots (cyan=left, coral=right), rebuilt wholesale
    from (label,x,y,direction) tuples. Rebuilt only on corner-set change; zero per-tick cost."""

    def __init__(self, plot):
        self.plot = plot
        self._items: list = []
        self._font = theme.mono_font(theme.CAPTION)
        # Click-to-locate highlight state: marker list (label->apex lookup), ring item, current label.
        self._markers: list = []
        self._highlight_item = None
        self.highlighted: str | None = None

    def _px_per_data(self) -> tuple[float, float]:
        """(px-per-data-x, px-per-data-y) from the viewbox, to convert px offsets into data coords.
        Falls back to 1.0 before the widget has a size."""
        vb = self.plot.getViewBox()
        rect = vb.viewRect()          # data-space rect currently shown
        size = vb.boundingRect()      # px-space rect of the viewbox
        if rect.width() <= 0 or rect.height() <= 0 or size.width() <= 0 or size.height() <= 0:
            return 1.0, 1.0
        return size.width() / rect.width(), size.height() / rect.height()

    def set_corners(self, markers):
        """(Re)build labels + apex dots from (label,x,y,direction) markers ([] clears; also clears
        any highlight). Labels are nudged outward from the centroid and greedily de-collided; dots
        are always drawn."""
        self.set_highlight(None)
        self._markers = list(markers)
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
            dots.setZValue(5)  # above lap traces, below the marker (z=10)
            self.plot.addItem(dots)
            self._items.append(dots)
        # Nudge each label outward from the apex-cloud centroid (px offset -> data units).
        cx = float(np.mean([x for _l, x, _y, _d in markers]))
        cy = float(np.mean([y for _l, _x, y, _d in markers]))
        sx, sy = self._px_per_data()
        bw, bh = CORNER_LABEL_BOX_PX
        placed_px: list[tuple[float, float]] = []  # (px_x, px_y) centres of kept labels
        for label, x, y, _d in markers:
            dx, dy = float(x) - cx, float(y) - cy
            norm = (dx * dx + dy * dy) ** 0.5 or 1.0
            # px offset converted back to data units along the outward unit vector.
            ox = (dx / norm) * CORNER_LABEL_OFFSET_PX / max(sx, 1e-6)
            oy = (dy / norm) * CORNER_LABEL_OFFSET_PX / max(sy, 1e-6)
            lx, ly = float(x) + ox, float(y) + oy
            # Greedy de-collision in PX space: drop this label if its box overlaps a kept one.
            px_x, px_y = lx * sx, ly * sy
            if any(abs(px_x - px) < bw and abs(px_y - py) < bh for px, py in placed_px):
                continue
            placed_px.append((px_x, px_y))
            # fill = a translucent dark plate behind the glyphs (the "halo"); border None keeps
            # it subtle. Anchor centred on the offset point so the nudge reads symmetrically.
            text = pg.TextItem(text=label, color=CORNER_LABEL_COLOR, anchor=(0.5, 0.5),
                               fill=pg.mkBrush(CORNER_LABEL_HALO))
            text.setFont(self._font)
            text.setPos(lx, ly)
            text.setZValue(6)
            self.plot.addItem(text)
            self._items.append(text)

    def set_highlight(self, label: str | None):
        """Ring-highlight one corner's apex by label (None / unknown clears). Display-only."""
        if self._highlight_item is not None:
            self.plot.removeItem(self._highlight_item)
            self._highlight_item = None
        self.highlighted = None
        if label is None:
            return
        for lbl, x, y, _d in self._markers:
            if lbl == label:
                ring = pg.ScatterPlotItem(
                    pos=[(float(x), float(y))], size=CORNER_HIGHLIGHT_SIZE,
                    brush=pg.mkBrush(None),
                    pen=pg.mkPen(C.accent, width=CORNER_HIGHLIGHT_PEN_W), pxMode=True)
                ring.setZValue(7)  # above corner dots/labels, below the marker
                self.plot.addItem(ring)
                self._highlight_item = ring
                self.highlighted = lbl
                return


class _BrakeMarkers:
    """Brake ▼ glyphs at braking-zone onsets, sized by peak decel; one ScatterPlotItem per lap
    (both laps in compare mode). Rebuilt wholesale on lap/compare change; zero per-tick cost."""

    def __init__(self, plot):
        self.plot = plot
        self._items: list = []

    def set_markers(self, lap_markers):
        """(Re)build the glyphs from `lap_markers` = [(markers, colour)], where markers is a list
        of (x, y, peak_decel) onsets in local metres. [] clears. One ScatterPlotItem per lap."""
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []
        if not lap_markers:
            return
        for markers, colour in lap_markers:
            if not markers:
                continue
            spots = [{"pos": (x, y), "size": theme.brake_glyph_size(d)} for x, y, d in markers]
            dots = pg.ScatterPlotItem(
                symbol="t", pen=None, brush=pg.mkBrush(colour), pxMode=True)
            dots.addPoints(spots)
            dots.setZValue(7)  # above corner dots, below the marker
            self.plot.addItem(dots)
            self._items.append(dots)


class MapView(QWidget):
    # (start: Seg, sectors: list[Seg]) whenever a handle moves or sectors change.
    timing_lines_changed = Signal(object, object)

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._suppress_marker = False
        self._current_lap: int | None = None  # F3: scope the marker drag to this lap
        # Latest pending marker-drag seek time; the 30 Hz tick drains one per tick via
        # take_marker_seek(). None = none pending.
        self._marker_seek_target: float | None = None

        self.widget = pg.PlotWidget()
        self.plot = self.widget.getPlotItem()
        self.plot.setAspectLocked(True)  # equal aspect -> a true-shape track map
        # Hide axes/grid: a track map is a shape, not a chart.
        self.plot.showGrid(x=False, y=False)
        for side in ("left", "bottom", "top", "right"):
            self.plot.hideAxis(side)
        # No axes -> drop margins so the track fills the panel.
        self.plot.layout.setContentsMargins(0, 0, 0, 0)
        self.plot.setContentsMargins(0, 0, 0, 0)
        # Draw only best (faint) + current (bright) laps, each split into measured (solid) /
        # inferred (dashed) segments so GPS dropouts don't show as straight chords across the hole.
        self._best_overlay = _LapOverlay(self.plot, BEST_COLOR, base_width=BEST_WIDTH)
        self._best_lap_id: int | None = None
        self._current_overlay = _LapOverlay(self.plot, CURRENT_COLOR, base_width=3)

        # Freeze the view to the track bbox so marker moves never autorange.
        if len(session.tx) and len(session.ty):
            x_lo, x_hi = float(session.tx.min()), float(session.tx.max())
            y_lo, y_hi = float(session.ty.min()), float(session.ty.max())
            # 2% pad so the aspect-locked track fills the panel without handles flush to the edge.
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
        self.marker.setZValue(10)  # canonical z-order: lap overlays/rainbow ≤5, corner/brake 5-7, ghost 9, marker 10
        self.marker.sigPositionChanged.connect(self._marker_dragged)

        # Self-contained overlays; the app pushes corner/brake markers via set_corners /
        # set_brake_markers (both laps for brakes in compare mode).
        self._corner_markers = _CornerMarkers(self.plot)
        self._brake_markers = _BrakeMarkers(self.plot)

        # Compare ghost (lap B's kart position); created lazily on first compare tick, removed on exit.
        # ghost_updates counts placements for the per-tick perf-invariant tests.
        self._ghost: pg.TargetItem | None = None
        self.ghost_updates = 0

        # E2: provisional start cue; declared before _rebuild (which may refresh it). None = track
        # known, no cue. See _refresh_provisional_cue.
        self._provisional_line: pg.PlotDataItem | None = None
        self._provisional_label: pg.TextItem | None = None
        self._start: _TimingLine | None = None
        self._sectors: list[_TimingLine] = []
        self._rebuild(session.start_line, session.sector_lines)
        self._refresh_best()

        # Sector controls are exposed (not placed here) so app.py mounts them in the map header.
        self.add_sector_btn = QPushButton("Add sector")
        self.reset_sectors_btn = QPushButton("Reset sectors")
        self.add_sector_btn.clicked.connect(self._add_sector)
        self.reset_sectors_btn.clicked.connect(self._reset_sectors)
        # Opt-in snap-to-track toggle (default off = free placement). When on, a released handle
        # snaps to the nearest trace point. See _snap_to_trace.
        self.snap_btn = QPushButton("Snap to track")
        self.snap_btn.setIcon(icon("ph.magnet"))
        self.snap_btn.setCheckable(True)
        self.snap_btn.setToolTip(
            "Snap to track: when on, a released timing-line handle jumps to the nearest point "
            "on the track trace. Off (default) = handles stay exactly where you drop them.")
        # Tint the icon accent while checked.
        self.snap_btn.toggled.connect(
            lambda on: self.snap_btn.setIcon(icon("ph.magnet", color=C.accent if on else C.text)))

        # F3 rainbow channel control: a LABELLED dropdown (Off · Speed · Δ · Grip), so every channel
        # — Grip especially, formerly an undiscoverable 4th blind-cycle step — is visible and one
        # click away. central_view mounts it in the map header. The cycle API (_cycle_rainbow /
        # _rainbow_mode / _RAINBOW_ORDER) is preserved underneath and stays in sync with the combo.
        self._rainbow = _RainbowOverlay(self.plot)
        self._rainbow_mode = "off"  # "off" | "speed" | "delta" | "grip" (see _RAINBOW_ORDER)
        self.rainbow_combo = QComboBox()
        for mode in _RAINBOW_ORDER:
            self.rainbow_combo.addItem(_RAINBOW_COMBO_LABELS[mode], userData=mode)
        self.rainbow_combo.setToolTip(
            "Colour the current lap's line by a channel: Speed (red = slow, green = fast), "
            "Δ to best (red = losing, green = gaining), or Grip (ESTIMATED: red = on the session's "
            "grip limit, green = grip left unused). Off leaves the plain racing line. The faint "
            "best-lap reference is unchanged.")
        self.rainbow_combo.currentIndexChanged.connect(self._on_rainbow_combo)
        self._legend = _RainbowLegend()
        self._legend.setVisible(False)

        # C3 map key: floats over the plot's bottom-left (parented to the PlotWidget, re-pinned by
        # _reposition_key, raised so it stays clickable).
        self._map_key = _MapLegend(on_resize=self._reposition_key)
        self._map_key.setParent(self.widget)
        self._map_key.raise_()
        self._map_key.show()

        # Zero-valid-lap empty state: a centred placeholder floated over the plot (the largest
        # quadrant), so a load with no complete laps reads as an explained state — with the recovery
        # action — rather than a black void. Parented to the PlotWidget, re-centred by
        # _reposition_empty_state, shown/hidden by _refresh_empty_state (called at build + reseg).
        self._empty_state = QLabel(
            "No complete laps found in this recording.\n\nIf this is the right track, drag the "
            "start/finish line on the map to set where a lap begins.", self.widget)
        self._empty_state.setProperty("role", "EmptyState")
        self._empty_state.setAlignment(Qt.AlignCenter)
        self._empty_state.setWordWrap(True)
        self._empty_state.hide()
        self._refresh_empty_state()

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget, 1)
        lay.addWidget(self._legend)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_key()
        self._reposition_empty_state()

    def _reposition_key(self):
        """Keep the floating map key pinned to the plot's bottom-left, just inside the edge."""
        if getattr(self, "_map_key", None) is None:
            return
        m = 8  # px inset from the panel edges
        host = self.widget
        self._map_key.move(m, host.height() - self._map_key.height() - m)

    def _reposition_empty_state(self):
        """Keep the zero-lap empty-state placeholder centred over the plot, spanning a comfortable
        width so the message wraps cleanly. No-op until it's built / while it's hidden. Uses
        isHidden() (the explicit hide() flag), not isVisible() — the latter is False whenever the
        top-level window isn't shown yet, which would skip the initial placement."""
        es = getattr(self, "_empty_state", None)
        if es is None or es.isHidden():
            return
        host = self.widget
        w = min(host.width() - 24, 420)
        es.setFixedWidth(max(w, 120))
        es.adjustSize()
        es.move((host.width() - es.width()) // 2, (host.height() - es.height()) // 2)

    def _refresh_empty_state(self):
        """Show the centred placeholder iff the session has zero valid (complete) laps, else hide it
        and let the track/marker show through. Called at build and after every re-segmentation (a
        dragged start line can flip a 0-lap recording into having laps, or back)."""
        es = getattr(self, "_empty_state", None)
        if es is None:
            return
        show = not self.session.valid_lap_ids()
        es.setVisible(show)
        if show:
            es.raise_()
            self._reposition_empty_state()

    # ----------------------------------------------------------- timing lines
    def _rebuild(self, start: Seg, sectors: list[Seg]):
        for tl in [self._start, *self._sectors]:
            if tl:
                tl.remove()
        self._start = _TimingLine(self.plot, start, START_COLOR, self._emit, self._snap_to_trace)
        self._sectors = [_TimingLine(self.plot, s, SECTOR_COLOR, self._emit, self._snap_to_trace)
                         for s in sectors]
        # Re-pin the provisional cue (or remove it if the track is known).
        self._refresh_provisional_cue()

    def _refresh_provisional_cue(self):
        """Overlay a dashed accent start line + "drag to set start/finish — lap timing provisional"
        callout while the session's timing is PROVISIONAL (start line auto-fitted, not user-
        confirmed — see Session.timing_verified); remove it when the timing is Verified (a detected
        track OR a user-confirmed start line). Re-run on build and on every start-line move (_emit),
        so dragging the line into place (which confirms the timing) clears the cue live."""
        provisional = (not getattr(self.session, "timing_verified", True)
                       and self._start is not None)
        if not provisional:
            for it in (self._provisional_line, self._provisional_label):
                if it is not None:
                    self.plot.removeItem(it)
            self._provisional_line = self._provisional_label = None
            return
        seg = self._start.seg()
        mx, my = (seg.x1 + seg.x2) / 2.0, (seg.y1 + seg.y2) / 2.0
        if self._provisional_line is None:
            # Dashed accent line over the start segment (z above the handles, below the marker).
            pen = pg.mkPen(C.accent, width=2)
            pen.setStyle(Qt.DashLine)
            pen.setDashPattern([4, 4])
            self._provisional_line = pg.PlotDataItem([seg.x1, seg.x2], [seg.y1, seg.y2], pen=pen)
            self._provisional_line.setZValue(4)
            self.plot.addItem(self._provisional_line)
            halo = QColor(C.surface)
            halo.setAlpha(200)  # a dark plate so the amber callout reads over the trace
            self._provisional_label = pg.TextItem(
                text="drag to set start/finish\nlap timing provisional",
                color=C.accent, anchor=(0.5, -0.25), fill=pg.mkBrush(halo))
            self._provisional_label.setFont(theme.mono_font(theme.CAPTION))
            self._provisional_label.setZValue(8)
            self.plot.addItem(self._provisional_label)
        else:
            self._provisional_line.setData([seg.x1, seg.x2], [seg.y1, seg.y2])
        self._provisional_label.setPos(mx, my)

    def _snap_to_trace(self, x: float, y: float) -> tuple[float, float] | None:
        """Snap hook for the timing lines: None when the toggle is off, else the nearest trace
        point (session.nearest_index)."""
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
        self._refresh_provisional_cue()  # keep the cue glued to the start handle while dragging
        self.timing_lines_changed.emit(start, sectors)

    def _add_sector(self):
        start, sectors = self._current()
        # Pass the existing sector count so each suggestion lands at a distinct track position
        # (evenly subdividing the lap); two identical lines would collapse a split.
        sectors.append(self.session.suggest_sector(len(sectors)))
        self._rebuild(start, sectors)
        self._emit()

    def _reset_sectors(self):
        start, _ = self._current()
        self._rebuild(start, [])
        self._emit()

    # --------------------------------------------------------------- video sync
    def _marker_dragged(self, *_):
        # Constrain the seek to the current lap (nearest_time_in_lap) so spatially-overlapping laps
        # don't snap; fall back to whole-trace nearest in the lead-in.
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
            # Coalesce: stash the time; the tick drains one seek (take_marker_seek).
            self._marker_seek_target = t

    def take_marker_seek(self) -> float | None:
        """Return + consume the latest pending marker-drag seek time (None if none); polled per
        tick so a drag fires at most one seek per tick."""
        t, self._marker_seek_target = self._marker_seek_target, None
        return t

    def set_marker_index(self, i: int | None):
        """Place the marker at trace index `i` (None = no-op). The app passes a pre-resolved index
        so the search isn't repeated per tick."""
        if i is None:
            return
        self._suppress_marker = True
        self.marker.setPos(pg.Point(float(self.session.tx[i]), float(self.session.ty[i])))
        self._suppress_marker = False

    def set_playhead_time(self, t: float):
        # Scrub path: resolves the index itself. Shared verb with PlotsView.set_playhead_time.
        self.set_marker_index(self.session.index_at_time(t))

    # --------------------------------------------------------------- compare ghost (F4)
    def set_ghost_index(self, i: int | None):
        """Place the compare ghost at trace index `i` (None = no-op) — lap B's kart at equal
        elapsed-into-lap."""
        if i is None:
            return
        self.set_ghost_pos(float(self.session.tx[i]), float(self.session.ty[i]))

    def set_ghost_pos(self, x: float, y: float):
        """Place the ghost at explicit local (x,y) — used by F7 cross-recording compare where lap B
        isn't a primary-trace index. Lazily creates the one hollow ghost item."""
        if self._ghost is None:
            # Hollow ring (no fill), not movable — the marker stays the only drag-to-seek surface.
            self._ghost = pg.TargetItem((0.0, 0.0), size=11, movable=False,
                                        pen=pg.mkPen(GHOST_COLOR, width=2),
                                        brush=pg.mkBrush(None))
            self._ghost.setZValue(9)  # below the marker (10)
            self.plot.addItem(self._ghost)
        self.ghost_updates += 1
        self._ghost.setPos(pg.Point(x, y))

    def clear_ghost(self):
        """Remove the ghost on compare exit (deleted, not hidden, so the non-compare item list
        stays clean)."""
        if self._ghost is not None:
            self.plot.removeItem(self._ghost)
            self._ghost = None

    # --------------------------------------------------------------- rainbow (F3)
    def set_rainbow_mode(self, mode: str):
        """Set the painted channel to `mode` (one of _RAINBOW_ORDER) and re-render. The single seam
        the labelled combo and the cycle API both route through, so the mode, the combo selection
        and the rendering never drift. Unknown modes are ignored."""
        if mode not in _RAINBOW_ORDER or mode == self._rainbow_mode:
            # Still keep the combo in sync (e.g. a no-op re-select) but skip a redundant rebuild.
            self._sync_rainbow_combo(mode if mode in _RAINBOW_ORDER else self._rainbow_mode)
            return
        self._rainbow_mode = mode
        self._sync_rainbow_combo(mode)
        self._apply_rainbow()

    def _sync_rainbow_combo(self, mode: str):
        """Reflect `mode` in the labelled combo without re-entering _on_rainbow_combo (the combo's
        change signal is the user-driven path; this is the programmatic mirror)."""
        combo = getattr(self, "rainbow_combo", None)
        if combo is None:
            return
        idx = _RAINBOW_ORDER.index(mode)
        if combo.currentIndex() != idx:
            combo.blockSignals(True)
            combo.setCurrentIndex(idx)
            combo.blockSignals(False)

    def _on_rainbow_combo(self, _index: int):
        """The labelled dropdown's selection changed → switch the painted channel to the chosen
        mode (Off · Speed · Δ · Grip), each one click, none hidden behind a blind cycle."""
        mode = self.rainbow_combo.currentData()
        if mode is not None:
            self.set_rainbow_mode(mode)

    def _cycle_rainbow(self):
        """Advance the channel cycle off → speed → Δ → grip → off and re-apply. Retained as the
        keyboard/programmatic cycle path (and the rainbow tests' driver); it routes through
        set_rainbow_mode so the labelled combo stays in sync."""
        order = _RAINBOW_ORDER
        nxt = order[(order.index(self._rainbow_mode) + 1) % len(order)]
        self.set_rainbow_mode(nxt)

    def _apply_rainbow(self):
        """(Re)build or clear the rainbow for the current lap+mode. The only path that fills the
        bucket items; hides the normal overlay while painting and restores it otherwise."""
        painted = False
        if self._rainbow_mode != "off" and self._current_lap is not None:
            painted = self._build_rainbow(self._current_lap, self._rainbow_mode)
        if not painted:
            self._rainbow.clear()
        self._legend.setVisible(painted)
        self._current_overlay.set_visible(not painted)

    def _build_rainbow(self, lap_id: int, mode: str) -> bool:
        """Fill the bucket items for `lap_id`'s channel (speed / Δ-vs-best / grip); returns False
        when it can't be computed (degenerate lap, no best lap for Δ, no g signal for grip).

        The widget only fetches the lap's per-sample arrays from the session here; the per-channel
        value/bucket math (negation, grip fixed scale, GPS-dropout NaN-mask) is the Qt-free
        map_render.rainbow_channel pure function."""
        ch = self.session.lap_channels(lap_id)
        times, xs, ys, speed_kmh, cum = (
            ch["t_media_s"], ch["x_m"], ch["y_m"], ch["speed_kmh"], ch["dist_m"])
        grip_util = self.session.driving.lap_grip_utilization(lap_id) if mode == "grip" else None
        # Δ-vs-best on the 400-grid (delta()'s y-series); None when no best lap / lap absent.
        delta_grid = None
        if mode == "delta":
            got = self.session.delta([lap_id])
            if got is not None and lap_id in got[2]:
                delta_grid = got[2][lap_id][1]
        result = rainbow_channel(mode, times, xs, ys, speed_kmh, cum, grip_util, delta_grid)
        if result is None:
            return False
        seg_buckets, lo_txt, hi_txt = result
        self._rainbow.set_data(xs, ys, seg_buckets)
        self._legend.set_labels(lo_txt, hi_txt)
        return True

    # --------------------------------------------------------------- lap overlays
    def _refresh_best(self):
        """Draw the faint reference (local best lap, or the F7 cross-recording reference ring when
        one is loaded); redraws only when the drawn identity changes."""
        ref_xy = self.session.reference_overlay_xy()
        if ref_xy is not None:
            # Key the reference distinctly from any lap id so switching always rebuilds.
            key = ("ref", self.session.reference_label())
            if self._best_lap_id == key and self._best_overlay.lap_id is not None:
                return
            self._best_lap_id = key
            self._best_overlay.set_polyline(ref_xy[:, 0], ref_xy[:, 1], key)
            return
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
        # A re-segmentation can flip the lap count to/from zero (e.g. dragging the start line onto
        # the track), so re-evaluate the zero-lap empty state.
        self._refresh_empty_state()
        # A user drag re-segments AND confirms the timing (Provisional → Verified), so re-evaluate
        # the on-canvas provisional cue here — by now session.timing_verified reflects the edit.
        self._refresh_provisional_cue()

    # ------------------------------------------------------------- corner labels (F-corner)
    def set_corners(self, markers):
        """Show corner labels at the given (label, x, y, direction) apex markers ([] clears).
        Pushed by the app."""
        self._corner_markers.set_corners(markers)

    def highlight_corner(self, cid: int | None):
        """Ring-highlight one corner's apex marker by 1-based cid (None clears) — driven by
        the consistency panel's corner list (F6). Display-only: no selection, no seek."""
        self._corner_markers.set_highlight(None if cid is None else f"C{int(cid)}")

    # ------------------------------------------------------------- brake glyphs (F5)
    def set_brake_markers(self, lap_markers):
        """Show brake glyphs from `lap_markers` = [(markers, colour)], markers = [(x, y,
        peak_decel)] in local metres. Current lap normally; both laps in compare. [] clears."""
        self._brake_markers.set_markers(lap_markers)
