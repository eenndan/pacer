"""MapView: the track-shape trace with draggable start/sector timing lines, a draggable
video-position marker, and overlays (rainbow line, corner labels, brake glyphs, compare ghost).

All geometry is in local metres (same space as the trace). It holds no `pacer` types — the app
feeds it numpy arrays/markers. The compare ghost exists only during compare mode.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QEvent, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import gapfill, theme, units
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
# Whole-recording trace = the quietest layer of all (muted grey, hairline), drawn UNDER both laps:
# context and drag target, never competing with the two laps that carry the analysis.
TRACE_COLOR = C.text_muted
TRACE_WIDTH = 1.0
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
# centroid, then de-cluttered so labels don't stack on each other or on the start/finish crosshair
# where corners bunch up near the start (see set_corners).
CORNER_LABEL_COLOR = C.text                   # near-primary so the label reads over the surface
CORNER_LABEL_HALO = QColor(C.surface)         # dark translucent plate behind the glyphs
CORNER_LABEL_HALO.setAlpha(190)
CORNER_LABEL_OFFSET_PX = 14                    # px the label is nudged outward from the centroid
# Generous px box for the overlap test (no per-frame metrics; labels are static once built).
CORNER_LABEL_BOX_PX = (22.0, 16.0)
# Provisional start-line callout: anchored on the start segment's midpoint, text below it. The X
# anchor is a STARTING point, not a constant — _clamp_provisional_label slides it so the caption
# never paints off the canvas (MAP-05). The Y anchor is fixed (the caption hangs under the line).
PROVISIONAL_ANCHOR = (0.5, -0.25)
PROVISIONAL_EDGE_PAD_PX = 6.0   # px of air kept between the caption's box and the plot's edge
# On-canvas action notice (see MapView._post_notice): how long it stays up, and how wide it may
# grow. NOTICE_MS matches app.STATUS_MS so the map's confirmations and the window's read alike.
NOTICE_MS = 6000
NOTICE_MAX_W_PX = 340
# Extra outward push (px) applied to a label whose apex sits within CORNER_START_CLEAR_PX of the
# start/finish crosshair, so the amber crosshair and its clustered C-labels stop colliding.
CORNER_START_CLEAR_PX = 26.0
CORNER_START_NUDGE_PX = 12.0
# The video-position marker MOVES every video frame while the label layout above is computed ONCE
# per corner-set change, so a label the marker wanders onto is simply painted over (marker z=10 vs
# label z=6) and becomes unreadable. Per tick we run an O(n) axis-aligned box test (n = corners,
# ~10-20 floats) of the marker against each label's laid-out position and nudge ONLY the labels it
# actually covers, radially away from the marker; the full declutter layout stays off the tick.
CORNER_MARKER_CLEAR_PX = 12.0   # marker half-extent (a size-15 TargetItem) plus a little air
CORNER_MARKER_PAD_PX = 2.0      # extra px so a nudged label clears the marker rather than kissing it
CORNER_MARKER_EPS_PX = 0.5      # sub-px moves aren't worth a setPos/repaint
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

    def chrome_items(self):
        """The interaction chrome items (the segment line + both draggable handles) — the pieces a
        clean share grab must hide so the app's editing crosshairs never burn into the brag image.
        A single accessor so a future timing-line piece can't be missed by grab_clean (H2)."""
        return (self.line, self.h1, self.h2)

    def remove(self):
        for item in self.chrome_items():
            self.plot.removeItem(item)


def _segs_equal(a: list[Seg], b: list[Seg], tol: float = 1e-6) -> bool:
    """Two timing-line lists hold the same endpoints (within a micron). Used to tell an untouched
    set of suggested sector lines from one the user has dragged."""
    return len(a) == len(b) and all(
        abs(u - v) <= tol
        for s, t in zip(a, b, strict=True)
        for u, v in ((s.x1, t.x1), (s.y1, t.y1), (s.x2, t.x2), (s.y2, t.y2)))


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
_RAINBOW_ORDER = ("off", "speed", "delta", "grip", "elevation")
# Short, legible per-channel labels for the map-header dropdown (each channel visible + one click),
# replacing the old blind-cycle button captions (where Grip was an undiscoverable 4th step).
_RAINBOW_COMBO_LABELS = {"off": "Line: Off", "speed": "Line: Speed", "delta": "Line: Δ to best",
                         "grip": theme.estimated_label("Line: Grip"), "elevation": "Line: Elevation"}
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

    def recolor(self):
        """Re-pen each bucket item from the ACTIVE palette's rainbow (after a colour-blind-palette
        flip). No-op before the items exist; the caller re-fills their data via _apply_rainbow."""
        if self._items is None:
            return
        for it, color in zip(self._items, rainbow_colors(MAP_RAINBOW_N), strict=True):
            it.setPen(pg.mkPen(color, width=RAINBOW_WIDTH))


class _GradientStrip(QWidget):
    """The legend's colour bar: paints the EXACT bucket colours, low→high, edge to edge —
    legend == rendering, pen-for-pen."""

    def __init__(self, colors: list[QColor]):
        super().__init__()
        self._colors = colors
        self.setFixedHeight(8)

    def set_colors(self, colors: list[QColor]):
        """Swap the painted bucket colours (palette flip) and repaint."""
        self._colors = colors
        self.update()

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
        self._strip = _GradientStrip([QColor(c) for c in rainbow_colors(MAP_RAINBOW_N)])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(8)
        lay.addWidget(self.lo_label)
        lay.addWidget(self._strip, 1)
        lay.addWidget(self.hi_label)

    def set_labels(self, lo_text: str, hi_text: str):
        """Set the min/max end labels. A HINT (empty hi_text) reads as a single message (e.g. the
        best-lap "no delta" note): the gradient strip + max label are hidden so we don't imply a live
        colour gradient that isn't painted (L1)."""
        hint = hi_text == ""
        self.lo_label.setText(lo_text)
        self.hi_label.setText(hi_text)
        self.hi_label.setVisible(not hint)
        self._strip.setVisible(not hint)

    def recolor(self):
        """Repaint the legend strip in the ACTIVE palette's rainbow (after a palette flip)."""
        self._strip.set_colors([QColor(c) for c in rainbow_colors(MAP_RAINBOW_N)])


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

    def bounds(self):
        """NaN-safe (x_lo, x_hi, y_lo, y_hi) over the drawn items, or None if nothing is drawn."""
        return _items_bounds(self._items)


def _xy_bounds(xs, ys):
    """NaN-safe (x_lo, x_hi, y_lo, y_hi) over one x/y pair, or None if it holds no finite point.
    NaN-safe matters: inferred gap-fill runs are NaN-padded, so a plain min/max poisons the box with
    nan and every downstream comparison silently goes False."""
    if xs is None or ys is None:
        return None
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    if len(xs) == 0 or len(ys) != len(xs):
        return None
    ok = np.isfinite(xs) & np.isfinite(ys)
    if not ok.any():
        return None
    return (float(xs[ok].min()), float(xs[ok].max()), float(ys[ok].min()), float(ys[ok].max()))


def _union_bounds(boxes):
    """The bounding box of some (x_lo, x_hi, y_lo, y_hi) boxes; Nones are skipped, all-None → None."""
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), max(b[1] for b in boxes),
            min(b[2] for b in boxes), max(b[3] for b in boxes))


def _items_bounds(items):
    """NaN-safe bounds over a list of pyqtgraph data items, or None when none of them draw."""
    return _union_bounds(_xy_bounds(*it.getData()) for it in items)


def _trace_runs(session) -> list[tuple[np.ndarray, np.ndarray]]:
    """The whole recording's trace split into contiguous runs at GPS dropouts.

    A dropout is a hole in the KEPT-point clock (gapfill.find_gaps), so a missing corner draws as a
    break rather than a straight chord across it — the same honesty the per-lap overlay buys with
    its dashed inferred runs, minus the reconstruction (that stays a per-lap concern). Runs shorter
    than 2 points are dropped: a lone point draws nothing."""
    xs, ys = np.asarray(session.tx, float), np.asarray(session.ty, float)
    tt = np.asarray(getattr(session, "tt", []), float)
    if len(xs) < 2 or len(ys) != len(xs):
        return []
    cuts = [g["j"] for g in gapfill.find_gaps(tt)] if len(tt) == len(xs) else []
    return [(xs[a:b], ys[a:b])
            for a, b in zip([0, *cuts], [*cuts, len(xs)], strict=True) if b - a >= 2]


class _TraceOverlay:
    """The whole recording's driven trace, drawn once, faintly, under everything else.

    Two jobs. (1) It is the map's only ALWAYS-drawn track: the lap overlays hold at most two laps
    and are both empty when no lap is complete — which is exactly the state whose placeholder tells
    the user to "drag the start/finish line on the map", with nothing on the canvas to drag it onto.
    (2) It is the map's stable extent, so _fit_view frames the whole drive instead of whichever lap
    happens to be selected. Deliberately NOT part of the rainbow: one muted grey, never in the speed
    colour bar, so the legend keeps meaning exactly what it says.

    Built once — the trace is the recording; re-segmentation moves lap boundaries, never points.
    Undecimated: measured at +1.6 ms per FULL map repaint on the richest fixture (65 laps, 46 761
    points, offscreen dpr 1), and the 30 Hz marker tick only damages the marker's own rect."""

    def __init__(self, plot, session: Session):
        self.plot = plot
        self._items: list = []
        self._bounds = None  # memo: the items are built once and never move
        pen = pg.mkPen(TRACE_COLOR, width=TRACE_WIDTH)
        for xs, ys in _trace_runs(session):
            item = plot.plot(xs, ys, pen=pen)
            item.setZValue(-5)  # below the lap overlays/rainbow (see the marker's z-order note)
            self._items.append(item)

    def bounds(self):
        """NaN-safe (x_lo, x_hi, y_lo, y_hi) over the drawn trace, or None if nothing is drawn.
        Memoized: _fit_view runs on every resize, and this is the widest array of the three."""
        if self._bounds is None:
            self._bounds = _items_bounds(self._items)
        return self._bounds


def _point_seg_dist(p, a, b) -> float:
    """Euclidean distance from point ``p`` to the segment a→b (all 2-tuples). Used to test whether a
    corner label sits on the whole start-line segment, not just near an endpoint (M7 declutter)."""
    px, py = p
    ax, ay = a
    bx, by = b
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 0:  # degenerate segment → distance to the (coincident) endpoint
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / denom))
    qx, qy = ax + t * dx, ay + t * dy
    return ((px - qx) ** 2 + (py - qy) ** 2) ** 0.5


class _CornerMarkers:
    """Corner C# labels + direction-coloured apex dots (cyan=left, coral=right), rebuilt wholesale
    from (label,x,y,direction) tuples. The LAYOUT (the declutter pass) runs only on corner-set
    change; the per-tick path is `avoid_point`, an O(n) box test that nudges only the label the
    moving video marker is currently sitting on."""

    def __init__(self, plot):
        self.plot = plot
        self._items: list = []
        self._font = theme.mono_font(theme.CAPTION)
        # Click-to-locate highlight state: marker list (label->apex lookup), ring item, current label.
        self._markers: list = []
        self._highlight_item = None
        self.highlighted: str | None = None
        # Per-tick marker-avoidance state, parallel lists over the label TextItems (see avoid_point):
        # the item, its LAID-OUT home position (data units), the outward unit vector used to place it
        # (the degenerate-overlap fallback direction), and the position currently applied to the item.
        self._texts: list = []
        self._home: list[tuple[float, float]] = []
        self._outward: list[tuple[float, float]] = []
        self._applied: list[tuple[float, float]] = []

    def _px_per_data(self) -> tuple[float, float]:
        """(px-per-data-x, px-per-data-y) from the viewbox, to convert px offsets into data coords.
        Falls back to 1.0 before the widget has a size."""
        vb = self.plot.getViewBox()
        rect = vb.viewRect()          # data-space rect currently shown
        size = vb.boundingRect()      # px-space rect of the viewbox
        if rect.width() <= 0 or rect.height() <= 0 or size.width() <= 0 or size.height() <= 0:
            return 1.0, 1.0
        return size.width() / rect.width(), size.height() / rect.height()

    def set_corners(self, markers, start_anchors=None):
        """(Re)build labels + apex dots from (label,x,y,direction) markers ([] clears; also clears
        any highlight). Labels are nudged outward from the corner-cloud centroid, pushed clear of the
        start/finish cluster (``start_anchors`` = local-metre (x,y) points a label must clear — the
        start line's two endpoints + the video-position marker, or None/[]), and de-cluttered so
        overlapping labels are separated rather than dropped; dots are always drawn."""
        self.set_highlight(None)
        self._markers = list(markers)
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []
        self._texts, self._home, self._outward, self._applied = [], [], [], []
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
        for label, (lx, ly) in zip(
                [m[0] for m in markers], self._label_positions(markers, start_anchors), strict=True):
            # fill = a translucent dark plate behind the glyphs (the "halo"); border None keeps
            # it subtle. Anchor centred on the offset point so the nudge reads symmetrically.
            text = pg.TextItem(text=label, color=CORNER_LABEL_COLOR, anchor=(0.5, 0.5),
                               fill=pg.mkBrush(CORNER_LABEL_HALO))
            text.setFont(self._font)
            text.setPos(lx, ly)
            text.setZValue(6)
            self.plot.addItem(text)
            self._items.append(text)
            self._texts.append(text)
            self._home.append((lx, ly))
            self._applied.append((lx, ly))

    def _label_positions(self, markers, start_anchors):
        """Compute the (lx, ly) draw position for each corner label in data units, decluttered.

        Each label starts nudged outward from the apex-cloud centroid; a label whose apex sits near
        the start/finish cluster (within CORNER_START_CLEAR_PX px of EITHER start-line endpoint, the
        whole start-line segment, or the video-position marker — ``start_anchors``) gets an extra
        outward push so the amber crosshairs stay readable (M7: keying only on the line midpoint let
        C11 sit on the h2 endpoint handle); then any two labels whose px boxes overlap are separated
        (both slid apart along the line joining them) rather than one being dropped — every corner
        keeps a visible label. All the collision reasoning is in PX space; the returned positions are
        back in data units."""
        cx = float(np.mean([x for _l, x, _y, _d in markers]))
        cy = float(np.mean([y for _l, _x, y, _d in markers]))
        sx, sy = self._px_per_data()
        bw, bh = CORNER_LABEL_BOX_PX
        # Start/finish exclusion anchors in PX space: each anchor point, plus (when there are two
        # endpoints) the whole start-line SEGMENT between them, so a label mid-segment also clears.
        anchors_px = [(float(ax) * sx, float(ay) * sy) for ax, ay in (start_anchors or [])]
        seg_px = tuple(anchors_px[:2]) if len(anchors_px) >= 2 else None
        # Initial px placement: outward-normal offset from the centroid, plus a start-cluster push.
        px_pts: list[list[float]] = []
        self._outward = []
        for _label, x, y, _d in markers:
            apex_px = (float(x) * sx, float(y) * sy)
            dx, dy = float(x) - cx, float(y) - cy
            norm = (dx * dx + dy * dy) ** 0.5 or 1.0
            ux, uy = dx / norm, dy / norm  # outward unit vector (data-space direction)
            self._outward.append((ux, uy))  # reused by avoid_point as the degenerate-overlap direction
            off = CORNER_LABEL_OFFSET_PX
            # Start-cluster exclusion: a corner apex near ANY anchor point (or the start-line
            # segment) gets an extra outward nudge so its label clears the amber crosshairs +
            # video-position marker (the common cluster near the start).
            near = any((apex_px[0] - apx) ** 2 + (apex_px[1] - apy) ** 2 < CORNER_START_CLEAR_PX ** 2
                       for apx, apy in anchors_px)
            if not near and seg_px is not None:
                near = _point_seg_dist(apex_px, seg_px[0], seg_px[1]) < CORNER_START_CLEAR_PX
            if near:
                off += CORNER_START_NUDGE_PX
            px_pts.append([apex_px[0] + ux * off, apex_px[1] + uy * off])
        # Iteratively separate overlapping label boxes (a few passes settle the near-start cluster;
        # this is a tasteful nudge, not a physics sim). Two overlapping boxes are pushed apart along
        # the line joining them until their boxes just clear.
        for _ in range(6):
            moved = False
            for i in range(len(px_pts)):
                for j in range(i + 1, len(px_pts)):
                    ax, ay = px_pts[i]
                    bx, by = px_pts[j]
                    ddx, ddy = bx - ax, by - ay
                    ox, oy = bw - abs(ddx), bh - abs(ddy)
                    if ox <= 0 or oy <= 0:
                        continue  # boxes already clear on at least one axis
                    moved = True
                    # Push apart along whichever axis needs the smaller correction (least visual move).
                    if ox < oy:
                        sgn = 1.0 if ddx >= 0 else -1.0
                        shift = (ox / 2.0 + 0.5) * sgn
                        px_pts[i][0] -= shift
                        px_pts[j][0] += shift
                    else:
                        sgn = 1.0 if ddy >= 0 else -1.0
                        shift = (oy / 2.0 + 0.5) * sgn
                        px_pts[i][1] -= shift
                        px_pts[j][1] += shift
            if not moved:
                break
        return [(px / sx, py / sy) for px, py in px_pts]

    def avoid_point(self, mx: float, my: float) -> int:
        """Keep the corner labels out from under the moving video-position marker at data point
        (mx, my). Returns the number of labels currently nudged (for tests/telemetry).

        THE PER-TICK PATH — it must stay O(n) in the corner count and touch Qt only when something
        actually changed. The declutter LAYOUT (`_label_positions`) runs once per corner-set change
        and cannot see the marker move, so a label can end up buried under the marker (which paints
        over it: z=10 vs z=6). Here each label costs two absolute-difference compares against the
        marker's px box; the drawn position is a pure function of (laid-out home, marker) — clear of
        the marker means "sit at home", colliding means "sit pushed radially away from the marker,
        just past the box". The push tends to CORNER_MARKER_PAD_PX as the marker reaches the box
        edge, so the label slides out of the way and back continuously — no state machine, no
        flicker, and never a laid-out position lost. Steady state: n float compares, zero Qt calls."""
        if not self._texts:
            return 0
        sx, sy = self._px_per_data()
        if sx <= 0 or sy <= 0:
            return 0
        mpx, mpy = mx * sx, my * sy
        # Combined half-extents: half the label box plus the marker's own half-extent.
        half_w = CORNER_LABEL_BOX_PX[0] / 2.0 + CORNER_MARKER_CLEAR_PX
        half_h = CORNER_LABEL_BOX_PX[1] / 2.0 + CORNER_MARKER_CLEAR_PX
        nudged = 0
        for i, text in enumerate(self._texts):
            hx, hy = self._home[i]
            dx, dy = hx * sx - mpx, hy * sy - mpy
            adx, ady = abs(dx), abs(dy)
            if adx >= half_w or ady >= half_h:          # the fast path: no collision, sit at home
                tx_, ty_ = hx, hy
            else:
                nudged += 1
                norm = (dx * dx + dy * dy) ** 0.5
                if norm < 1e-9:  # label dead-centre on the marker — fall back to its outward normal
                    ux, uy = self._outward[i] if i < len(self._outward) else (1.0, 0.0)
                    n2 = (ux * ux + uy * uy) ** 0.5 or 1.0
                    ux, uy = ux / n2, uy / n2
                else:
                    ux, uy = dx / norm, dy / norm
                # u shares d's signs, so |d + t*u| = |d| + t*|u| and the clearing t per axis is exact.
                px_t = (half_w - adx) / abs(ux) if abs(ux) > 1e-9 else float("inf")
                py_t = (half_h - ady) / abs(uy) if abs(uy) > 1e-9 else float("inf")
                t = min(px_t, py_t) + CORNER_MARKER_PAD_PX
                tx_, ty_ = (hx * sx + ux * t) / sx, (hy * sy + uy * t) / sy
            ax, ay = self._applied[i]
            if abs(tx_ - ax) * sx > CORNER_MARKER_EPS_PX or abs(ty_ - ay) * sy > CORNER_MARKER_EPS_PX:
                text.setPos(tx_, ty_)
                self._applied[i] = (tx_, ty_)
        return nudged

    def reset_positions(self):
        """Put every label back at its laid-out home, undoing any marker nudge. For the share-card
        grab, where the marker is hidden: a label dodging a marker nobody can see just reads as a
        mis-placed label."""
        for i, text in enumerate(self._texts):
            if self._applied[i] != self._home[i]:
                text.setPos(*self._home[i])
                self._applied[i] = self._home[i]

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
        # Speed display unit (km/h default); app pushes the persisted choice via set_speed_unit.
        # Only the "speed" rainbow legend end-labels use it — the bucket COLOURS are km/h-invariant.
        self._speed_unit = units.DEFAULT_UNIT
        self._suppress_marker = False
        self._current_lap: int | None = None  # F3: scope the marker drag to this lap
        # Latest pending marker-drag seek time; the 30 Hz tick drains one per tick via
        # take_marker_seek(). None = none pending.
        self._marker_seek_target: float | None = None

        self.widget = pg.PlotWidget()
        self.plot = self.widget.getPlotItem()
        self.plot.setAspectLocked(True)  # equal aspect -> a true-shape track map
        # Drop pyqtgraph's developer chrome from this user-facing surface: the "A" auto-range button
        # that fades in over the bottom-left of the track on hover, and the raw right-click menu
        # (Transforms / Downsample / Average / Export…). Neither belongs on a track map. This touches
        # DISPLAY only — mouse interaction (pan/zoom drag, and the draggable timing-line + marker
        # handles) is deliberately left exactly as it was. Same treatment as the stats sparklines.
        self.plot.hideButtons()
        self.plot.setMenuEnabled(False)
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

        # The always-on faint layer under both laps: every point of the drive (see _TraceOverlay).
        self._trace_overlay = _TraceOverlay(self.plot, session)

        # Freeze the view to the drawn content so marker moves never autorange. _view_fitted tracks
        # whether the view still IS that fit: True until the user pans/zooms, so nothing we redraw
        # later ever yanks a view they moved deliberately.
        self._view_fitted = True
        self._fit_view()
        # pyqtgraph emits this from wheelEvent/mouseDragEvent only — i.e. exactly when the USER
        # moved the view, never from our own setRange. It is what raises the Fit affordance.
        self.plot.getViewBox().sigRangeChangedManually.connect(self._on_manual_range)
        # ANY range change (our fit, a wheel zoom, a pan) moves the start line relative to the
        # panel edges, so the provisional caption re-checks that it still fits (MAP-05).
        self.plot.getViewBox().sigRangeChanged.connect(lambda *_: self._clamp_provisional_label())

        self.marker = pg.TargetItem(
            (session.tx[0] if len(session.tx) else 0, session.ty[0] if len(session.ty) else 0),
            size=15, movable=True, pen=pg.mkPen(MARKER_COLOR, width=2),
            brush=pg.mkBrush(_MARKER_RGB.red(), _MARKER_RGB.green(), _MARKER_RGB.blue(), 110),
        )
        self.plot.addItem(self.marker)
        self.marker.setZValue(10)  # canonical z-order: lap overlays/rainbow ≤5, corner/brake 5-7, ghost 9, marker 10

        # Self-contained overlays; the app pushes corner/brake markers via set_corners /
        # set_brake_markers (both laps for brakes in compare mode).
        self._corner_markers = _CornerMarkers(self.plot)
        self._brake_markers = _BrakeMarkers(self.plot)
        # Wired AFTER the overlays exist: _marker_dragged also keeps the corner labels clear of the
        # marker, so _corner_markers must be constructed before the first emission can reach it.
        self.marker.sigPositionChanged.connect(self._marker_dragged)

        # Compare ghost (lap B's kart position); created lazily on first compare tick, removed on exit.
        # ghost_updates counts placements for the per-tick perf-invariant tests.
        self._ghost: pg.TargetItem | None = None
        self.ghost_updates = 0

        # E2: provisional start cue; declared before _rebuild (which may refresh it). None = track
        # known, no cue. See refresh_provisional_cue.
        self._provisional_line: pg.PlotDataItem | None = None
        self._provisional_label: pg.TextItem | None = None
        self._start: _TimingLine | None = None
        self._sectors: list[_TimingLine] = []
        # The (reference label, ring-missing) state the "no reference line" notice was last posted
        # for — declared before _rebuild/_refresh_best, which consult it. See _note_missing_reference.
        self._ref_note_key: tuple | None = None
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
        # Default the map's line to the SPEED gradient — the product's signature visual — so a
        # freshly-loaded recording (and the lap-card thumbnail it seeds) leads with the coloured
        # racing line instead of a flat single colour. The speed channel is cached/vectorised in
        # studio/map_render.py, so painting it on load costs no extra work. Off/Δ/grip stay one
        # combo pick (or one cycle) away.
        self._rainbow_mode = "speed"  # "off" | "speed" | "delta" | "grip" (see _RAINBOW_ORDER)
        self.rainbow_combo = QComboBox()
        for mode in _RAINBOW_ORDER:
            self.rainbow_combo.addItem(_RAINBOW_COMBO_LABELS[mode], userData=mode)
        self.rainbow_combo.setToolTip(
            "Colour the current lap's line by a channel: Speed (red = slow, green = fast), "
            "Δ to best (red = losing, green = gaining), Grip (ESTIMATED: red = on the session's "
            "grip limit, green = grip left unused), or Elevation (red = the lowest point of the "
            "lap, green = the highest). Elevation is RELATIVE within the lap: GPS altitude drifts "
            "by several metres between laps of the same track, so only the shape is meaningful — "
            "the legend reads 'lowest' → the rise above it, never an altitude above sea level. "
            "Off leaves the plain racing line. The faint best-lap reference is unchanged.")
        # Show the default channel in the combo BEFORE wiring the change signal, so the initial
        # selection reads "Line: Speed" without re-entering _on_rainbow_combo.
        self._sync_rainbow_combo(self._rainbow_mode)
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

        # The app's own way back from a pan/zoom. pyqtgraph's auto-range "A" button and its
        # right-click "View All" stay gone (dev chrome — see hideButtons/setMenuEnabled above);
        # this is an ordinary app button floated over the plot's top-right, and it appears ONLY
        # once the view has actually been moved off the fit, so a framed map carries no chrome.
        self.fit_btn = QPushButton("Fit", self.widget)
        self.fit_btn.setIcon(icon("ph.frame-corners"))
        self.fit_btn.setToolTip("Fit the whole track back into view (or double-click the map)")
        self.fit_btn.setCursor(Qt.PointingHandCursor)
        self.fit_btn.clicked.connect(self._fit_view)
        self.fit_btn.hide()
        self._sync_fit_btn()
        # Double-click anywhere on the canvas does the same thing — the gesture a user tries first,
        # which until now did nothing at all. Filtered on the viewport: no pyqtgraph chrome involved.
        self.widget.viewport().installEventFilter(self)

        # Transient confirmation for the map's own destructive/creative actions (MAP-07): the
        # sector buttons live in the map header and their effect is on this canvas, but the status
        # bar belongs to the window, which this view has no channel to. So the notice is posted
        # where the change happened — a small auto-dismissing plate over the plot's top-left, clear
        # of the Fit button (top-right), the map key (bottom-left) and the centred empty state.
        self._notice = QLabel("", self.widget)
        self._notice.setWordWrap(True)
        self._notice.setStyleSheet(
            f"background-color: {C.surface_active}; color: {C.text_dim}; "
            f"border: 1px solid {C.border}; border-radius: 6px; padding: 6px 10px; "
            f"font-size: {theme.CAPTION}px;")
        self._notice.hide()
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self._notice.hide)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget, 1)
        lay.addWidget(self._legend)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_key()
        self._reposition_empty_state()
        self._reposition_fit_btn()
        self._reposition_notice()
        self._clamp_provisional_label()  # a reshaped panel can push the cue over an edge (MAP-05)
        # A panel that changed shape re-frames the track (the map fills whatever room it is given),
        # but only while the view is still OUR fit — never on top of a view the user moved.
        if getattr(self, "_view_fitted", False):
            self._fit_view()

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.MouseButtonDblClick and obj is self.widget.viewport()
                and event.button() == Qt.LeftButton):
            self._fit_view()
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------ view fit
    def _content_bbox(self):
        """NaN-safe (x_lo, x_hi, y_lo, y_hi) over everything the map draws as track: the session
        trace UNION both lap overlays.

        Union, never either alone. The trace alone would clip an F7 cross-recording reference ring
        (another recording's polyline, drawn into _best_overlay by set_polyline). The overlays alone
        would drop the trace — and with it the draggable video marker, which rides session.tx/ty and
        would sit off-canvas for most of any recording whose complete laps cover a fraction of the
        drive (on the unknown-track fixture that is 77.7% of the points).

        The trace term falls back to the raw points when the overlay drew none of them (a trace too
        short to be a polyline is still somewhere the marker can sit)."""
        trace = self._trace_overlay.bounds() or _xy_bounds(self.session.tx, self.session.ty)
        return _union_bounds((trace, self._best_overlay.bounds(),
                              self._current_overlay.bounds()))

    def _fit_view(self):
        """Frame the map on its content and freeze it there (autorange off, so the video marker
        moving never drags the view around). The one place a range is set — called at build, from
        the Fit button / canvas double-click, and after a redraw or resize that left our own fit
        standing."""
        box = self._content_bbox()
        if box is None:
            return
        x_lo, x_hi, y_lo, y_hi = box
        # 2% pad so the aspect-locked track fills the panel without handles flush to the edge.
        px = max(x_hi - x_lo, 1.0) * 0.02
        py = max(y_hi - y_lo, 1.0) * 0.02
        vb = self.plot.getViewBox()
        vb.setRange(xRange=(x_lo - px, x_hi + px), yRange=(y_lo - py, y_hi + py), padding=0)
        vb.disableAutoRange()
        self._view_fitted = True
        self._sync_fit_btn()

    def _on_manual_range(self, *_):
        """The user panned or zoomed: stop re-fitting behind their back, and surface the way back."""
        self._view_fitted = False
        self._sync_fit_btn()

    def _sync_fit_btn(self):
        """Show the Fit button iff the view has been moved off the fitted frame."""
        btn = getattr(self, "fit_btn", None)
        if btn is None:  # called from _fit_view during __init__, before the button exists
            return
        btn.setVisible(not self._view_fitted)
        if not self._view_fitted:
            self._reposition_fit_btn()
            btn.raise_()

    def _reposition_fit_btn(self):
        """Keep the Fit button pinned to the plot's top-right, mirroring the map key's inset."""
        btn = getattr(self, "fit_btn", None)
        if btn is None:
            return
        m = 8  # px inset from the panel edges (same as _reposition_key)
        btn.adjustSize()
        btn.move(self.widget.width() - btn.width() - m, m)

    # ------------------------------------------------------------ on-canvas notice
    def _post_notice(self, text: str):
        """Show a transient confirmation over the plot for NOTICE_MS, replacing any previous one."""
        notice = getattr(self, "_notice", None)
        if notice is None:
            return
        notice.setText(text)
        notice.show()             # before the reposition: it skips a hidden plate (the resize path)
        self._reposition_notice()
        notice.raise_()
        self._notice_timer.start()

    def retract_notice(self, replacement: str) -> None:
        """Replace the plate's text with `replacement` — but ONLY while a plate is actually up.

        For the gestures that START on the map and COMPLETE elsewhere. "Reset sectors" posts "2
        sector lines cleared — Edit ▸ Undo timing-line edit (⌘Z) puts them back." on a fire-and-
        forget 6 s timer; the ⌘Z it asks for lands in 45 ms, and the plate then spent the remaining
        ~5.95 s instructing the user to press a key they had already pressed, directly above the
        restored lines (QA W3-04). Silent when no plate is showing, so an undo that nobody was
        told to make adds no new chatter to the canvas."""
        notice = getattr(self, "_notice", None)
        if notice is None or notice.isHidden():
            return
        self._post_notice(replacement)

    def _reposition_notice(self):
        """Pin the notice plate to the plot's top-left, wrapping within the panel's width."""
        notice = getattr(self, "_notice", None)
        if notice is None or notice.isHidden():
            return
        m = 8  # px inset from the panel edges (same as _reposition_key)
        host = self.widget
        w = max(min(host.width() - 2 * m, NOTICE_MAX_W_PX), 120)
        notice.setFixedWidth(w)
        # A word-wrapped QLabel's sizeHint is its ONE-LINE hint, so adjustSize() alone sizes the
        # plate for one line and paints the rest outside it — measured on the reference notice
        # below: 5 wrapped lines in a 70 px plate, top and bottom lines sliced through. Ask the
        # label what the text needs at the width just pinned; sizeHint stays the floor so the
        # existing one-line notices keep their exact geometry.
        notice.setFixedHeight(max(notice.heightForWidth(w), notice.sizeHint().height()))
        notice.move(m, m)

    def _reposition_key(self):
        """Keep the floating map key pinned to the plot's bottom-left, just inside the edge."""
        if getattr(self, "_map_key", None) is None:
            return
        m = 8  # px inset from the panel edges
        host = self.widget
        self._map_key.move(m, host.height() - self._map_key.height() - m)

    @contextmanager
    def grab_clean(self):
        """Temporarily hide the map's pure-INTERACTION chrome so a caller can ``grab()`` the plot as
        a clean image for a social share (the shareable lap card). Dev/interaction affordances have
        no place on a share image; the SPEED rainbow colouring, corner labels and track line stay.
        Restores each item's prior visibility on exit (even on error), so the live map is untouched.

        Hidden for the grab (H2 — otherwise the app's editing crosshairs burn into the brag image):
          * the "Map key" legend + the zero-lap empty-state placeholder + the Fit button + the
            transient action notice (Qt widgets — the Fit button and the notice are pure
            interaction chrome, and both are usually hidden anyway);
          * the coral video-position ``marker`` (the amber "+" crosshair on the track);
          * every timing line's segment + drag handles — the start line and each sector line — plus
            the compare ghost, all via each ``_TimingLine.chrome_items()`` so a future line type is
            hidden automatically.

        The provisional start-line cue is not hidden here: it only shows on unverified timing, which
        already blocks the card upstream, so it never reaches a real grab."""
        # Qt widget chrome: save the EXPLICIT hide flag (isHidden), not effective isVisible — a child
        # reads not-visible whenever its top-level window isn't shown yet, so restoring from isVisible
        # would wrongly leave the key hidden on an off-screen/grab-only map. isHidden is True only when
        # hide() was actually called.
        widgets = [w for w in (getattr(self, "_map_key", None), getattr(self, "_empty_state", None),
                               getattr(self, "fit_btn", None), getattr(self, "_notice", None))
                   if w is not None]
        widget_prev = [(w, w.isHidden()) for w in widgets]
        # pyqtgraph plot items (marker, timing-line segments/handles, compare ghost). These are not
        # top-level-gated Qt widget children, so isVisible() is the honest current flag for them.
        items = []
        if getattr(self, "marker", None) is not None:
            items.append(self.marker)
        timing_lines = [getattr(self, "_start", None), *(getattr(self, "_sectors", None) or [])]
        for tl in timing_lines:
            if tl is not None:
                items.extend(tl.chrome_items())
        if getattr(self, "_ghost", None) is not None:
            items.append(self._ghost)
        item_prev = [(it, it.isVisible()) for it in items]
        try:
            for w in widgets:
                w.hide()
            for it in items:
                it.setVisible(False)
            # The marker is hidden for the grab, so drop any marker-dodge nudge the labels are
            # holding (see _CornerMarkers.avoid_point): on a card with no marker, a nudged label
            # just looks mis-placed. Restored below from the marker's live position.
            self._corner_markers.reset_positions()
            yield self
        finally:
            for w, was_hidden in widget_prev:
                w.setVisible(not was_hidden)
            for it, was_visible in item_prev:
                it.setVisible(was_visible)
            if getattr(self, "marker", None) is not None:
                p = self.marker.pos()
                self._corner_markers.avoid_point(p.x(), p.y())

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
    def reload_timing_lines(self) -> None:
        """Re-draw the draggable start/sector line items from the session's CURRENT timing lines,
        WITHOUT emitting timing_lines_changed. Used after an Undo (the session lines were already
        restored through Session.undo_timing_lines): the map's handles must follow the session, but
        this is not a fresh user edit, so it must not re-fire the change signal / re-persist."""
        self._rebuild(self.session.start_line, self.session.sector_lines)
        self._refresh_best()

    def _rebuild(self, start: Seg, sectors: list[Seg]):
        for tl in [self._start, *self._sectors]:
            if tl:
                tl.remove()
        self._start = _TimingLine(self.plot, start, START_COLOR, self._emit, self._snap_to_trace)
        self._sectors = [_TimingLine(self.plot, s, SECTOR_COLOR, self._emit, self._snap_to_trace)
                         for s in sectors]
        # Re-pin the provisional cue (or remove it if the track is known).
        self.refresh_provisional_cue()

    def refresh_provisional_cue(self):
        """Overlay a dashed accent start line + "drag to set start/finish — lap timing provisional"
        callout while the session's timing is PROVISIONAL (start line auto-fitted, not user-
        confirmed — see Session.timing_verified); remove it when the timing is Verified (a detected
        track OR a user-confirmed start line). Re-run on build and on every start-line move (_emit),
        so dragging the line into place (which confirms the timing) clears the cue live.

        PUBLIC because the trust flag can flip without a re-segmentation: File ▸ Save as track…
        promotes these very lines into a named track, which makes the session Verified while every
        line stays exactly where it is. CentralView.refresh_timing_trust drives this method so that
        path clears the cue too — it used to leave the canvas shouting "lap timing provisional" in
        the same frame the trust strip above it had already cleared (QA W7-03)."""
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
                color=C.accent, anchor=PROVISIONAL_ANCHOR, fill=pg.mkBrush(halo))
            self._provisional_label.setFont(theme.mono_font(theme.CAPTION))
            self._provisional_label.setZValue(8)
            self.plot.addItem(self._provisional_label)
        else:
            self._provisional_line.setData([seg.x1, seg.x2], [seg.y1, seg.y2])
        self._provisional_label.setPos(mx, my)
        self._clamp_provisional_label()

    def _clamp_provisional_label(self):
        """Keep the provisional callout's box inside the plot, whatever the start line is near.

        MAP-05: a pg.TextItem draws its box around a data point at a FIXED anchor, so a centred
        (0.5) caption on a start line near an edge paints its outer half off-canvas — measured at
        x = −39.8 px in a 1272 px panel, i.e. 30 % of the caption gone and the cue reading
        "o set start/finish". The caption is chrome, not data, so slide the ANCHOR rather than the
        position: the box moves into the rect while the cue still points at the same line, and
        nothing about the string, the dashed line or the timing changes.

        Scene-x is derived from the ViewBox's own view→scene ratio rather than the item's
        sceneTransform, which pyqtgraph only refreshes lazily (at paint) after a setRange — so this
        is correct immediately after a fit, not one frame late."""
        label = getattr(self, "_provisional_label", None)
        if label is None:
            return
        vb = self.plot.getViewBox()
        view, scene = vb.viewRect(), vb.sceneBoundingRect()
        w = label.boundingRect().width()
        if w <= 0 or view.width() <= 0 or scene.width() <= 0:
            return
        # The anchored point, in scene px, and the anchor fractions that just fit each edge:
        # left  edge = x - ax*w >= L + pad  ->  ax <= (x - L - pad) / w
        # right edge = x + (1-ax)*w <= R - pad  ->  ax >= 1 - (R - pad - x) / w
        x = scene.left() + (label.pos().x() - view.left()) / view.width() * scene.width()
        pad = PROVISIONAL_EDGE_PAD_PX
        ax = PROVISIONAL_ANCHOR[0]
        ax = max(ax, 1.0 - (scene.right() - pad - x) / w)   # pull the right edge in…
        ax = min(ax, (x - scene.left() - pad) / w)          # …then the left, which wins if the
        ax = min(max(ax, 0.0), 1.0)                         # caption is wider than the panel
        if abs(ax - label.anchor.x()) > 1e-3:
            label.setAnchor((ax, PROVISIONAL_ANCHOR[1]))

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
        self.refresh_provisional_cue()  # keep the cue glued to the start handle while dragging
        self.timing_lines_changed.emit(start, sectors)

    def _add_sector(self):
        """Add one sector line — re-spacing the whole set while the user has not moved any of them.

        `suggest_sector(n)` can only APPEND: it bisects what is left after the lines already there,
        so three clicks used to give sub-sectors of 49.9 / 16.8 / 8.6 / 24.7 % of the lap (MAP-11) —
        the third click carving an 8.6 % sliver. `suggest_sectors(n)` places the whole set evenly,
        but replacing the set unconditionally would silently move lines the user dragged into
        place, which is the very thing MAP-07 is about. So: re-space only while the placed lines
        are still exactly what we suggested; the moment one is dragged, fall back to appending and
        leave the user's placements alone."""
        start, sectors = self._current()
        n = len(sectors)
        if _segs_equal(sectors, self.session.suggest_sectors(n)):
            sectors = self.session.suggest_sectors(n + 1)
            note = (f"Sector line {n + 1} added — the lap is now split into {n + 2} even sectors. "
                    "Drag any line to move it.")
        else:
            sectors.append(self.session.suggest_sector(n))
            note = (f"Sector line {n + 1} added — drag it into place. "
                    "(Your other sector lines were left where you put them.)")
        self._rebuild(start, sectors)
        self._emit()
        self._post_notice(note)

    def _reset_sectors(self):
        """Clear every sector line — and SAY so, naming the way back.

        MAP-07: this discarded three hand-placed lines in 59 ms with no dialog, no status line and
        nothing on the map that changed except the lines vanishing. It is fully reversible (the
        edit goes through the same undo stack as a line drag), so the fix is the missing feedback,
        not a confirmation dialog — the app's only two confirms wipe a whole library index, a
        different scale from clearing annotations on the session in front of you."""
        start, sectors = self._current()
        if not sectors:  # nothing to clear: say so rather than re-segmenting for no reason
            self._post_notice("No sector lines to clear.")
            return
        self._rebuild(start, [])
        self._emit()
        self._post_notice(
            f"{len(sectors)} sector line{'s' if len(sectors) != 1 else ''} cleared — "
            "Edit ▸ Undo timing-line edit (⌘Z) puts them back.")

    # --------------------------------------------------------------- video sync
    def _marker_dragged(self, *_):
        p = self.marker.pos()
        # The marker paints OVER the corner labels (z=10 vs z=6) and moves every video frame, while
        # the label layout is computed once per corner set — so nudge any label it has landed on out
        # from under it. Deliberately BEFORE the suppress guard: sigPositionChanged fires for the
        # per-tick programmatic setPos (set_marker_index) as well as for a user drag, so this one
        # call covers both paths. O(n corners) float compares; no re-layout. See avoid_point.
        self._corner_markers.avoid_point(p.x(), p.y())
        # Constrain the seek to the current lap (nearest_time_in_lap) so spatially-overlapping laps
        # don't snap; fall back to whole-trace nearest in the lead-in.
        if self._suppress_marker:
            return
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
    def set_speed_unit(self, unit: str):
        """Switch the speed display unit live: re-render so the SPEED rainbow legend re-labels in
        the new unit. No-op if unchanged; only the speed channel is affected (Δ/grip are unitless)."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        if self._rainbow_mode == "speed":
            self._apply_rainbow()

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
        bucket items; hides the normal overlay while painting and restores it otherwise. A "hint"
        status (Δ on the best lap — no delta to paint) shows the legend hint but keeps the plain
        overlay so the map still reads (L1)."""
        status = "none"
        if self._rainbow_mode != "off" and self._current_lap is not None:
            status = self._build_rainbow(self._current_lap, self._rainbow_mode)
        if status != "painted":
            self._rainbow.clear()
        # Legend visible while painting OR while showing a hint (e.g. "best lap — no delta").
        self._legend.setVisible(status in ("painted", "hint"))
        self._current_overlay.set_visible(status != "painted")

    def refresh_palette(self):
        """Re-render the rainbow + legend in the ACTIVE semantic palette (after a colour-blind-
        palette flip): re-pen the bucket items + legend strip, then re-apply so the fill + labels
        pick up the new ahead/behind endpoints. Cheap and safe when no rainbow is painted."""
        self._rainbow.recolor()
        self._legend.recolor()
        self._apply_rainbow()

    def _build_rainbow(self, lap_id: int, mode: str) -> str:
        """Fill the bucket items for `lap_id`'s channel (speed / Δ-vs-best / grip). Returns a status:
          * "painted" — the rainbow was filled + the legend set;
          * "hint"    — nothing painted, but the legend carries a hint (Δ on the best lap: no delta
                        to paint — L1); the caller keeps the plain overlay and shows the legend;
          * "none"    — can't be computed (degenerate lap, no best lap for Δ, no g signal for grip).

        The widget only fetches the lap's per-sample arrays from the session here; the per-channel
        value/bucket math (negation, grip fixed scale, GPS-dropout NaN-mask, the best-lap Δ hint) is
        the Qt-free map_render.rainbow_channel pure function."""
        ch = self.session.lap_channels(lap_id)
        times, xs, ys, speed_kmh, cum = (
            ch["t_media_s"], ch["x_m"], ch["y_m"], ch["speed_kmh"], ch["dist_m"])
        grip_util = self.session.driving.lap_grip_utilization(lap_id) if mode == "grip" else None
        elevation = self.session.lap_elevation_channel(lap_id) if mode == "elevation" else None
        # Δ-vs-best on the 400-grid (delta()'s y-series); None when no best lap / lap absent.
        delta_grid = None
        if mode == "delta":
            got = self.session.delta([lap_id])
            if got is not None and lap_id in got[2]:
                delta_grid = got[2][lap_id][1]
        result = rainbow_channel(mode, times, xs, ys, speed_kmh, cum, grip_util, delta_grid,
                                 self._speed_unit, elevation=elevation)
        if result is None:
            return "none"
        seg_buckets, lo_txt, hi_txt = result
        self._legend.set_labels(lo_txt, hi_txt)
        # A None seg_buckets with a legend = the best-lap Δ hint: don't paint, just show the hint.
        if seg_buckets is None:
            return "hint"
        self._rainbow.set_data(xs, ys, seg_buckets)
        return "painted"

    # --------------------------------------------------------------- lap overlays
    def _note_missing_reference(self, ref_xy):
        """Say WHY the map is not drawing a loaded reference's racing line, once per state change.

        A reference whose spatial fit is refused (too far off the primary loop, or the wrong SIZE
        — see cross_reference.fit_is_drawable) leaves `reference_overlay_xy()` None, and the map
        quietly falls back to the local best-lap ghost. That fallback is correct — a mis-fitted
        ring is worse than none, because it looks like data — but on its own it is indistinguishable
        from "the reference loaded fine", so the user is left to wonder which faint line they are
        looking at. The map owns the surface the line is missing from, so it explains it here, on
        the canvas where the change happened (the same reasoning as the sector notices).

        Keyed on (label, missing) so the 30 Hz `set_current_lap` tick and every re-segmentation
        re-run it for free; nothing is latched before the notice widget exists (early in __init__),
        so a reference already active at construction is still explained on the first real refresh."""
        if getattr(self, "_notice", None) is None:
            return
        # reference_label() is None exactly when no reference is active — the same identity the
        # drawn-ring branch below keys on. getattr-guarded like the view's other session reads, so
        # a minimal test double without the F7 seam behaves as "no reference" rather than raising.
        label = getattr(self.session, "reference_label", lambda: None)()
        key = (label, ref_xy is None)
        if key == self._ref_note_key:
            return
        self._ref_note_key = key
        if label is None or ref_xy is not None:
            return
        self._post_notice(
            f"Reference “{label}” loaded — but its racing line is not drawn: it doesn't match "
            "this lap's shape and size closely enough to place honestly. The faint line is your "
            "own best lap; the Δ charts and lap table still compare against the reference.")

    def _refresh_best(self):
        """Draw the faint reference (local best lap, or the F7 cross-recording reference ring when
        one is loaded); redraws only when the drawn identity changes."""
        ref_xy = self.session.reference_overlay_xy()
        self._note_missing_reference(ref_xy)
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
        self.refresh_provisional_cue()
        # Re-frame what is now drawn (a re-segmentation can swap in a different best lap, or an F7
        # reference ring that reaches outside the trace) — but only while our own fit still stands.
        if self._view_fitted:
            self._fit_view()

    # ------------------------------------------------------------- corner labels (F-corner)
    def set_corners(self, markers):
        """Show corner labels at the given (label, x, y, direction) apex markers ([] clears).
        Pushed by the app. The start/finish cluster's exclusion anchors (both start-line ENDPOINTS
        + the live video-position marker) are handed down so labels near that cluster are nudged
        clear of it (declutter) — not just the line midpoint (M7: C11 collided with the h2 endpoint,
        31 px from the midpoint but only 15 px from the handle it pierced).

        The layout's marker anchor is a SNAPSHOT (the marker moves every video frame), so the fresh
        labels are immediately re-checked against the marker's live position — the same O(n) test the
        30 Hz tick runs (_marker_dragged → _CornerMarkers.avoid_point)."""
        self._corner_markers.set_corners(markers, start_anchors=self._start_clear_anchors())
        marker = getattr(self, "marker", None)
        if marker is not None:
            p = marker.pos()
            self._corner_markers.avoid_point(p.x(), p.y())

    def _start_clear_anchors(self):
        """The local-metre points a corner label must clear near the start/finish: the start line's
        two ENDPOINT handle positions AND the current video-position marker (per QA item #21, so C1/
        C11 slide clear of the whole start/finish + marker cluster). Returns [] when nothing is
        available (bare-Session test paths). Each is an (x, y) tuple in local metres."""
        anchors: list[tuple[float, float]] = []
        seg = getattr(self.session, "start_line", None)
        if seg is not None:
            anchors.append((seg.x1, seg.y1))
            anchors.append((seg.x2, seg.y2))
        marker = getattr(self, "marker", None)
        if marker is not None:
            p = marker.pos()
            anchors.append((p.x(), p.y()))
        return anchors

    def highlight_corner(self, cid: int | None):
        """Ring-highlight one corner's apex marker by 1-based cid (None clears) — driven by
        the consistency panel's corner list (F6). Display-only: no selection, no seek."""
        self._corner_markers.set_highlight(None if cid is None else f"C{int(cid)}")

    # ------------------------------------------------------------- brake glyphs (F5)
    def set_brake_markers(self, lap_markers):
        """Show brake glyphs from `lap_markers` = [(markers, colour)], markers = [(x, y,
        peak_decel)] in local metres. Current lap normally; both laps in compare. [] clears."""
        self._brake_markers.set_markers(lap_markers)
