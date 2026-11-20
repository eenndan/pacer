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
from PySide6.QtCore import QEvent, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QFontMetrics,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QTransform,
)
from PySide6.QtWidgets import (
    QComboBox,
    QGraphicsRectItem,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import data_quality, gapfill, map_render, motion, prefs, theme, units
from .map_render import (
    bucket_polylines,
    bucketize,  # noqa: F401  (re-exported for tests importing from map_view)
    rainbow_channel,
    resample_grid_to_points,  # noqa: F401  (re-exported for tests importing from map_view)
)
from .session import Seg
from .theme import CHART_SERIES, MAP_RAINBOW_N, C, icon, rainbow_colors
from .timeline import TRACE_TAIL_S, trace_point_at, trace_tail
from .widgets import EmptyState, ToggleButton

# The very QPainterPaths pyqtgraph fills for a ScatterPlotItem symbol, in a unit box centred on the
# origin. The map key paints its brake row from these, so the plate cannot drift from the canvas.
_SYMBOLS = pg.graphicsItems.ScatterPlotItem.Symbols

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# Track-map pens: best = quiet faint reference, current = bright amber accent.
#
# THE ACCENT IS SELECTION ON THIS CANVAS, AND ONLY SELECTION: the line the user drags, the lap the
# map is following, the primary lap's brake glyphs. The DATA drawn under them — the channel ramp —
# never carries it: its middle is theme.C.data_mid, not the accent (see the note there). Until it
# was, the start line sat on a ramp bucket 2.62 dE from its own colour on 17 of 37 laps at Sandown.
START_COLOR = C.accent              # start/finish line — accent so it's the clear anchor
SECTOR_COLOR = C.text_dim           # sector lines — visible but quieter than the start line
# Best lap = quiet reference (secondary grey, width 1.5 so the track shape still reads); current
# lap at width 3 stays the emphasis.
BEST_COLOR = C.text_dim
BEST_WIDTH = 1.5
CURRENT_COLOR = C.accent            # highlighted current-lap trace (the racing line — pops)
# THE HERO STROKE (cool/map-hero). The lap the map follows is the canvas's subject, and at 3 px it
# read as one more strand of the grey all-laps ribbon it sits on (the ribbon is ~20 laps of
# hairlines, several px wide at Sandown). It is now twice as wide, with round caps and joins (the
# rainbow's 16 bucket runs butt-joined with visible notches at every bucket change), over a dark
# CASING of the canvas colour 1.5 px wider on each side — so its colour reads against the grey
# whatever bucket it is in, the way a road map outlines its main roads. No glow: it would cost 16
# more wide translucent items and muddy the ribbon the casing already separates it from.
# All three are built with the line (lap / mode change), never on the tick.
HERO_WIDTH = 6
HERO_CASING_COLOR = C.canvas
HERO_CASING_WIDTH = HERO_WIDTH + 3
# Whole-recording trace = the quietest layer of all (muted grey, hairline), drawn UNDER both laps:
# context and drag target, never competing with the two laps that carry the analysis.
TRACE_COLOR = C.text_muted
TRACE_WIDTH = 1.0
# Lap Race layer (_RaceLayer): dot sizes (px), the grey of the laps outside the coloured five, the
# selected lap's ring, the trail (seconds of the lap's own path behind its dot) and the veil's
# alpha over the base map while a race runs.
RACE_DOT_PX = 9
RACE_GREY_PX = 6
RACE_GREY = QColor(C.text_muted).darker(135).name()   # under the coloured five, not beside them
RACE_RING_W = 1.5
RACE_RING_GAP_PX = 2
RACE_TRAIL_S = 1.5
RACE_TRAIL_W = 2.5
RACE_TRAIL_ALPHA = 150
RACE_VEIL_ALPHA = 195
# Lap Race ▸ Follow (MapView.set_race_follow, _FollowCam): the view's ground widths (m across the
# whole map), the share of the free area (the map right of the card) the field may fill from its
# median dot, the dead-zone box the median never leaves (a share of the free area each way), the
# easing time constants in RACE seconds (so 2× eases as far per metre as 1×) and the step of the
# race clock that counts as a jump (a scrub, a restart): recentre at once.
FOLLOW_WIDTHS_M = (30.0, 45.0, 60.0, 90.0, 120.0, 150.0)
FOLLOW_FIELD_Q = 0.8
FOLLOW_FILL = 0.6
FOLLOW_SHRINK = 0.8
FOLLOW_BOX = 0.15
FOLLOW_TAU_S = 0.2
FOLLOW_ZOOM_TAU_S = 0.3
FOLLOW_JUMP_S = 1.0
SCALE_BAR_MAX_PX = 96
MARKER_COLOR = C.position           # video position marker — the hue no data line mode paints
_MARKER_RGB = QColor(C.position)    # for the translucent marker brush below
# The playhead's fading tail (_PlayheadTail): `timeline.trace_tail`, the export inset's comet, in
# the marker's hue, cut into TAIL_STEPS equal lengths that grow brighter and wider toward the dot,
# each over a dark canvas-coloured halo (the export's answer: a bare magenta fade was measured LOST
# over the speed-coloured line). Widths are logical px, oldest → newest; alphas 0-255 likewise.
TAIL_STEPS = 6
TAIL_WIDTH = (2.0, 4.0)
TAIL_ALPHA = (90, 255)
TAIL_HALO_PX = 1.5          # halo px each side of the stroke (the hero casing's)
TAIL_HALO_ALPHA = (60, 220)
TAIL_Z = 8.5                # over the brake glyphs (7) and corner labels, under the ghost (9)
# Compare ghost = lap-B accent (cyan), the canonical "other lap" colour.
GHOST_COLOR = CHART_SERIES[1]
# Inferred gap-fill segments are drawn dashed + dimmed so they read as distinct from measured GPS.
INFERRED_DASH = [5, 5]  # on/off dash pattern (px)
INFERRED_ALPHA = 130    # 0-255; dimmer than the measured pen
INFERRED_DARKEN = 0.55  # blend the lap colour toward black for the fill pen
CORNER_LEFT_COLOR = theme.CHART_SERIES[1]    # cyan — left-handers
CORNER_RIGHT_COLOR = theme.CHART_SERIES[4]   # coral — right-handers
CORNER_DOT_ALPHA = 170                       # 0-255: subtle, under the text label
# The apex dots sit ON the painted line, and their two identity hues each land on it: the coral
# right-hand dot is 1.17 deuteranopic dE (3.67 normal) from the default ramp's bucket 2 once
# composited at CORNER_DOT_ALPHA, and the cyan left-hand one 2.82 deuteranopic from the
# colour-blind ramp's bucket 11. Measured where the apexes really are, 132 of 248 right-hand apex
# placements at Sandown (62 laps) and 27 of 74 on MK_18_09_26 (19 laps) sit under Line: Speed on a
# bucket within 10 dE (6.9 deuteranopic) of the dot's own hue — an apex is a lap's slow point,
# which is the ramp's red end. Recolouring would only
# move the collision to another bucket of one of the two ramps, so the dots take the brake glyphs'
# answer instead: the same dark canvas rim, which clears every bucket of both ramps by >= 67 dE.
CORNER_DOT_OUTLINE = C.canvas
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
# The video-position marker MOVES every video frame while the label layout above is computed ONCE
# per corner-set change, so a label the marker wanders onto is simply painted over (marker z=10 vs
# label z=6) and becomes unreadable. Per tick we run an O(n) axis-aligned box test (n = corners,
# ~10-20 floats) of the marker against each label's laid-out position and nudge ONLY the labels it
# actually covers, radially away from the marker; the full declutter layout stays off the tick.
# The marker's DRAWN half-extent plus 1 px of air: a size-15 TargetItem paints its crosshair ±15 px
# (a 30 x 30 px box, measured on the owner's map), not ±7.5 — this read 12 until VIEW-8, so a
# label could sit 3 px inside the crosshair and still count as clear.
CORNER_MARKER_CLEAR_PX = 16.0
# ...and the start line's handles, size-11 TargetItems: a 22 x 22 px box, plus 1 px of air.
START_HANDLE_CLEAR_PX = 12.0
CORNER_MARKER_PAD_PX = 2.0      # extra px so a nudged label clears the marker rather than kissing it
CORNER_MARKER_EPS_PX = 0.5      # sub-px moves aren't worth a setPos/repaint
# THE LAYOUT PLACES THE LABELS IT DRAWS (VIEW-8, QA 2026-09-25/26). It used to separate
# CORNER_LABEL_BOX_PX boxes, but a drawn label (the text plus its halo plate) measures 23 x 22 px
# ("C1") and 30 x 22 px ("C11") — so on MK_18_09 at the owner's layout C1 still sat on C11, under
# the start line and the playhead, and C3/C4/C8 under brake glyphs at 1280x800. Each label now
# tries CORNER_LABEL_DIRS directions around its apex (outward from the corner cloud first) at
# CORNER_LABEL_STEPS_PX extra distances, and takes the spot that overlaps least: other labels
# first, then the start line (handles and segment), the video marker, the brake glyphs and every
# apex dot, then the canvas edge. It re-runs when the view's scale, the brake glyphs or the start
# line change; never on the tick (the moving marker is still `avoid_point`'s).
CORNER_LABEL_DIRS = 16
CORNER_LABEL_STEPS_PX = (0.0, 5.0, 10.0, 16.0, 24.0)
CORNER_LABEL_AIR_PX = 3.0       # kept between a label's plate and the apex dot it names
CORNER_DOT_HALF_PX = 4.5        # an apex dot (size 7) plus its rim
# The map frames the CIRCUIT, not everything the receiver logged (VIEW-8): on MK_18_09 1.2 % of the
# kept fixes are acquisition junk off to one side, and fitting them made the 208 m circuit share
# its 205 px-tall canvas with 253 m of trace. The valid laps' extent is the frame when at least this
# share of the trace lies inside it (MK 98.9 %, the three Sandown recordings 95.8-99.7 %); a drive
# whose laps are a fraction of it — an unknown track, a lone short "lap" — keeps the whole trace,
# which is also where the video marker can be (MAP-04).
FIT_CIRCUIT_SHARE = 0.9
# ...and the frame keeps a label's room at its edge: a corner at the top or bottom of the circuit
# had nowhere to put its label but off the canvas or onto its own brake glyph (C12 on MK_18_09).
FIT_LABEL_ROOM_PX = 12.0
# Click-to-locate cue: a hollow ring slightly larger than the apex dot, drawn in the corner
# LABEL's own near-primary text colour — deliberately NOT the UI accent.
#
# THE ACCENT WAS ALREADY SPENT TWICE ON THIS CANVAS: START_COLOR (the start/finish crosshair) and
# CURRENT_COLOR (the current lap's own racing line) are both C.accent. A corner apex sits ON that
# line by construction, so with the rainbow off — a supported mode — the cue was #f5a623 drawn on
# #f5a623. Measured by sampling the ring's own stroke on the shipped canvas: the ring's ink and the
# line just outside it were the same hex, contrast 1.00:1. The one cue whose whole job is to say
# "this corner, here" was invisible against the one thing it points at.
#
# CORNER_LABEL_COLOR ties the ring to the C# label it belongs to, and collides with nothing else
# the map draws (apex dots cyan/coral, video marker magenta, best-lap reference grey). It is not
# one of the palette-swappable hues, so it cannot freeze a palette (tests/test_contrast.py).
#
# IT DOES COLLIDE WITH ONE THING: the colour-blind ramp's light middle. That ramp diverges through
# a near-white neutral, and its bucket 9 is 4.80 dE (4.86 deuteranopic) from this near-white ring
# — the Pedal line is sitting at exactly that neutral when a driver lifts for an apex. So the ring
# is drawn over a wider dark rim of the canvas colour (CORNER_HIGHLIGHT_HALO_W), as two spots of
# ONE item: the rim clears every bucket of both ramps, and the ring keeps its label's colour.
CORNER_HIGHLIGHT_COLOR = CORNER_LABEL_COLOR
CORNER_HIGHLIGHT_PEN_W = 2
CORNER_HIGHLIGHT_HALO_W = CORNER_HIGHLIGHT_PEN_W + 2   # 1 px of canvas either side of the ring
CORNER_HIGHLIGHT_SIZE = 18
# Hovering a corner's label plate, or within this many px of its apex dot, names it to the rest of
# the app (MapView.cornerHovered): the ring above is what the chart's hover draws here, too.
CORNER_HOVER_PX = 7.0
# The compare ghost is lap B's identity cyan (GHOST_COLOR), which is 3.73 deuteranopic dE from the
# colour-blind ramp's bucket 11 — and it rides lap A's painted line. It used to be a HOLLOW ring,
# so the line showed straight through it; the hole is now the map's dark label plate instead, so
# the ring reads as a ring on any bucket and still never as the filled video marker.
GHOST_PLATE = CORNER_LABEL_HALO
# Brake glyphs (F5): a ▼ at each braking-zone onset; size ramps peak decel (g) via
# theme.brake_glyph_size (shared with the speed chart). ONE hue of their own, theme.MAP_BRAKE_COLOR
# (see the note there): they wore their lap's colour, and the best lap's green is the Speed ramp's
# "fast" end. In compare the two laps keep their SERIES_SYMBOL shapes, and lap B's glyphs are
# HOLLOW — a violet rim on the dark label plate — the same filled-A / ringed-B language as the video
# marker and the compare ghost, so the pair stays apart even when both laps map to one shape.
BRAKE_COLOR = theme.MAP_BRAKE_COLOR
BRAKE_HOLLOW_PLATE = CORNER_LABEL_HALO
BRAKE_HOLLOW_PEN_W = 2
# THE START/FINISH MARK (cool/map-hero): a small chequered bar square across the track where the
# start line crosses it, and an arrow just past it pointing the way the laps run. It replaces the
# amber segment + two crosshair handles as the RESTING look; the handles (and the start segment)
# appear only while the pointer is near a timing line, while the timing is provisional or there are
# no laps (the states that ask the user to drag), and after "Add sector" until the pointer leaves.
# Their hit targets are untouched. One pxMode ScatterPlotItem of two spots at the crossing: a dark
# backing, then the light squares + arrow — sized in px so it reads the same at any zoom. Its
# geometry is map_render's (finish_mark_geometry), shared with the debrief hero and the share card
# (trace_paint), so the three surfaces draw one mark.
FINISH_LIGHT = C.text
FINISH_DARK = C.canvas
# Half of the px box a corner label must keep clear of around the mark (the arrow's reach).
FINISH_CLEAR_PX = 20.0
# How near (px) the pointer must come to a timing line's handle or segment to reveal the handles.
TIMING_REVEAL_PX = 28.0


class _TimingLine:
    """Two draggable handles + a connecting segment, all in data (local-meter) coords."""

    def __init__(self, plot, seg: Seg, color, on_changed, snap, segment_at_rest: bool = True):
        self.plot = plot
        self.on_changed = on_changed
        # snap(x,y)->(x,y)|None: opt-in snap hook; None (toggle off) = free placement. See _snap_to_trace.
        self.snap = snap
        # Whether the segment stays drawn while the handles rest hidden: a sector line has no
        # other mark, the start line has the chequered one (see set_handles_visible).
        self.segment_at_rest = segment_at_rest
        pen = pg.mkPen(color, width=theme.line_width(2))
        self.line = pg.PlotDataItem([seg.x1, seg.x2], [seg.y1, seg.y2], pen=pen)
        self.h1 = pg.TargetItem((seg.x1, seg.y1), size=11, movable=True, pen=pen)
        self.h2 = pg.TargetItem((seg.x2, seg.y2), size=11, movable=True, pen=pen)
        for h in (self.h1, self.h2):
            h.setCursor(Qt.OpenHandCursor)   # the grab affordance, now that the handles hide
        plot.addItem(self.line)
        plot.addItem(self.h1)
        plot.addItem(self.h2)
        self.handles_visible = True
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

    def set_handles_visible(self, on: bool):
        """Show or hide the drag handles (and, for the start line, its segment). Hidden is the
        resting look; the hit targets themselves never change size or place."""
        self.handles_visible = bool(on)
        self.h1.setVisible(self.handles_visible)
        self.h2.setVisible(self.handles_visible)
        self.line.setVisible(self.handles_visible or self.segment_at_rest)

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


def _hero_pen(color, width):
    """A round-capped, round-joined pen at `width` logical px — the hero stroke and its casing."""
    pen = pg.mkPen(color, width=theme.line_width(width))
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _painted_runs(xs, ys, seg_buckets, n_buckets: int):
    """The (xs, ys) runs of consecutive segments the rainbow paints (bucket in [0, n)): exactly the
    geometry bucket_polylines draws, as whole runs rather than per-bucket pieces."""
    seg = np.asarray(seg_buckets)
    idx = np.flatnonzero((seg >= 0) & (seg < n_buckets))
    if idx.size == 0:
        return []
    xs, ys = np.asarray(xs, float), np.asarray(ys, float)
    return [(xs[r[0]:r[-1] + 2], ys[r[0]:r[-1] + 2])
            for r in np.split(idx, np.flatnonzero(np.diff(idx) > 1) + 1)]


# The casing is CHUNKED, and that is a measured requirement, not tidiness. pyqtgraph strokes a
# curve item's WHOLE path whenever any part of its bounding rect is repainted, and the 30 Hz marker
# repaints a small rect that a whole-lap item always contains: one whole-lap casing item cost the
# playback tick +26 % CPU (1.05 -> 1.32 ms median, offscreen, 6 alternating runs on the demo).
# In runs of CASING_CHUNK_PTS points (sharing their end points, so round caps close every seam)
# each item's rect is a short stretch of track, and a tick re-strokes only the one or two under
# the marker.
CASING_CHUNK_PTS = 24


class _Casing:
    """The hero stroke's dark casing as a pool of short PlotCurveItems (see CASING_CHUNK_PTS),
    filled on a lap / mode change and never on the tick."""

    def __init__(self, plot, z: float):
        self.plot, self.z = plot, z
        self.items: list = []
        self.visible = True

    def set_runs(self, runs):
        pieces = []
        for xs, ys in runs:
            xs, ys = np.asarray(xs, float), np.asarray(ys, float)
            ok = np.isfinite(xs) & np.isfinite(ys)
            edges = np.flatnonzero(np.diff(np.r_[0, ok.astype(np.int8), 0]))
            for a, b in zip(edges[::2], edges[1::2], strict=True):   # finite stretches [a, b)
                for s in range(a, b - 1, CASING_CHUNK_PTS):
                    e = min(s + CASING_CHUNK_PTS + 1, b)
                    pieces.append((xs[s:e], ys[s:e]))
        while len(self.items) < len(pieces):
            it = pg.PlotCurveItem(pen=_hero_pen(HERO_CASING_COLOR, HERO_CASING_WIDTH))
            it.setZValue(self.z)
            it.setVisible(self.visible)
            self.plot.addItem(it)
            self.items.append(it)
        for k, it in enumerate(self.items):
            if k < len(pieces):
                it.setData(*pieces[k])
            else:
                it.setData(np.empty(0), np.empty(0))

    def clear(self):
        self.set_runs([])

    def set_visible(self, on: bool):
        self.visible = on
        for it in self.items:
            it.setVisible(on)

    def repen(self):
        for it in self.items:
            it.setPen(_hero_pen(HERO_CASING_COLOR, HERO_CASING_WIDTH))


def _inferred_pen(color, base_width):
    """Dashed/dimmed/thinner pen for inferred gap-fill segments (distinct from measured GPS)."""
    qc = pg.mkColor(color)
    qc = qc.darker(int(100 / INFERRED_DARKEN))  # toward black
    qc.setAlpha(INFERRED_ALPHA)
    pen = pg.mkPen(qc, width=theme.line_width(max(base_width - 1, 1)))
    pen.setStyle(Qt.DashLine)
    pen.setDashPattern(INFERRED_DASH)
    return pen


# --------------------------------------------------------------- rainbow map (F3)
# F3 rainbow: pyqtgraph has no per-vertex pen, so the channel (speed / Δ / Δ rate / grip /
# elevation) is quantized into MAP_RAINBOW_N buckets, one PlotCurveItem per bucket. Rebuilt only on
# lap/channel/segment change.
RAINBOW_WIDTH = HERO_WIDTH  # same as the current-lap overlay, so the painted line reads identically
# Cycle order for the channel control: off → speed → Δ → Δ rate → grip → off (kept for the cycle API
# the tests drive; the labelled combo lists the SAME modes, so no channel is hidden behind a blind
# cycle). The two Δ channels sit next to each other because they are the same measurement asked two
# different questions ("how far behind am I here" vs "am I losing it here"), and a reader who wants
# one usually wants to flick to the other.
#
# Brake/throttle sits beside Grip: the two ESTIMATED channels derived from the g signal, both
# `(est)` in the dropdown and on their legends.
_RAINBOW_ORDER = ("off", "speed", "delta", "delta_rate", "grip", "brake_throttle", "elevation")
# Short, legible per-channel labels for the map-header dropdown (each channel visible + one click),
# replacing the old blind-cycle button captions (where Grip was an undiscoverable 4th step).
#
# "Pedal", not the chart's "Brake/Throttle", AND THAT IS A MEASURED WIDTH. PanelToolbar pins the
# combo at its sizeHint, which is its widest entry, and the map toolbar's width is the right
# column's minimum, which must stay under the charts panel's
# (test_map_key.test_the_map_toolbars_glyphs_cost_the_window_no_minimum_width). Measured in the
# themed font: "Line: Brake/Throttle (est)" needs 152 px against the previous widest entry's 91
# ("Line: Grip (est)"), which would widen the combo by 62 px and the toolbar from 544 to ~606 px,
# past the charts panel's 553 — raising the window's own minimum width. "Line: Pedal (est)" needs
# 99 px: the toolbar goes 544 -> 552 px, ONE pixel under 553. The next entry wider than this one
# will trip that guard. The legend under the line and the dropdown's tooltip say "brake" /
# "throttle" and name the chart band, so the chart's two words are one glance away.
_RAINBOW_COMBO_LABELS = {"off": "Line: Off", "speed": "Line: Speed", "delta": "Line: Δ to best",
                         "delta_rate": "Line: Δ rate", "grip": theme.estimated_label("Line: Grip"),
                         "brake_throttle": theme.estimated_label("Line: Pedal"),
                         "elevation": "Line: Elevation"}
# The Elevation legend says WHAT it is and what is NOT known about it (cool/map-hero). "lowest" →
# "+5 m" read like a measured relief; it is the GPS receiver's altitude, whose noise Pacer has not
# measured on the recording in front of you. The only figures in the repo are two other recordings'
# within-lap wander (studio/docs/corner-match-0060-2026-09.md: 0.57 m and 2.40 m median), cited in
# the tooltip as exactly that.
ELEVATION_SOURCE = "GPS altitude"
ELEVATION_NOISE = "noise not measured"
# ...and on a narrow map (see LEGEND_STRIP_MIN_SHARE), "GPS: lowest" / "+5 m ±?" — the full wording
# moves to the tooltip, which always carries ELEVATION_LEGEND_TIP for this channel.
ELEVATION_SOURCE_SHORT = "GPS"
ELEVATION_NOISE_SHORT = "±?"
ELEVATION_LEGEND_TIP = (
    "Elevation is the GPS receiver's altitude, relative to this lap's lowest point. Its noise is "
    "not measured on this recording: on two other recordings its within-lap wander measured "
    "0.57 m and 2.40 m (median), so a rise of a few metres may be mostly noise.")
# The per-channel rainbow value/bucket math (incl. the grip fixed scale, the Δ-rate SYMMETRIC
# scale, the Δ/Δ-rate/grip negation and the GPS-dropout NaN-mask) lives in the pure-numpy
# studio/map_render.py (rainbow_channel + helpers).


class _RainbowOverlay:
    """Owns the ≤MAP_RAINBOW_N PlotCurveItems of the rainbow (one per bucket). Items are created
    lazily and re-filled in place afterwards; `rebuilds` counts every fill so tests can assert the
    30 Hz tick path never touches the bucket items."""

    def __init__(self, plot):
        self.plot = plot
        self._items: list | None = None  # created lazily on the first build (off by default)
        # The hero casing under the buckets: exactly the geometry they draw, chunked (_Casing).
        self._casing = _Casing(plot, z=4.5)   # under the buckets, above the lap overlays
        self.rebuilds = 0  # instrumentation for the perf-invariant tests (no rebuild per tick)

    def _ensure_items(self):
        if self._items is None:
            self._items = []
            for color in rainbow_colors(MAP_RAINBOW_N):
                it = pg.PlotCurveItem(pen=_hero_pen(color, RAINBOW_WIDTH), connect="finite")
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
        self._casing.set_runs(_painted_runs(xs, ys, seg_buckets, len(items)))

    def clear(self):
        if self._items is None:
            return
        for it in self._items:
            it.setData(np.empty(0), np.empty(0))
        self._casing.clear()

    def recolor(self):
        """Re-pen each bucket item from the ACTIVE palette's rainbow (after a colour-blind-palette
        flip). No-op before the items exist; the caller re-fills their data via _apply_rainbow."""
        if self._items is None:
            return
        for it, color in zip(self._items, rainbow_colors(MAP_RAINBOW_N), strict=True):
            it.setPen(_hero_pen(color, RAINBOW_WIDTH))
        self._casing.repen()


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


# The legend's colour strip keeps at least this share of its row. The Elevation labels ("GPS altitude:
# lowest", "+5 m · noise not measured") are 107 and 141 px; at a 380 px map they squeezed the strip
# to 100 px, so below the width the full labels need they fall back to a short pair (tooltip: full).
LEGEND_STRIP_MIN_SHARE = 0.45


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
        # The full labels, their short fallback and the tooltip, as last set (see set_labels/_fit).
        self._full: tuple[str, str] = ("", "")
        self._short: tuple[str, str] | None = None
        self._tip = ""
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 2, 8, 2)
        lay.setSpacing(8)
        lay.addWidget(self.lo_label)
        lay.addWidget(self._strip, 1)
        lay.addWidget(self.hi_label)

    def set_labels(self, lo_text: str, hi_text: str, tip: str = "", short=None):
        """Set the min/max end labels. A HINT (empty hi_text) reads as a single message (e.g. the
        best-lap "no delta" note): the gradient strip + max label are hidden so we don't imply a live
        colour gradient that isn't painted (L1).

        `short` is an optional (lo, hi) pair used when the full labels would leave the strip less
        than LEGEND_STRIP_MIN_SHARE of the row (see _fit); `tip` is the legend's tooltip."""
        hint = hi_text == ""
        self._full = (lo_text, hi_text)
        self._short = short
        self._tip = tip
        self.hi_label.setVisible(not hint)
        self._strip.setVisible(not hint)
        self._fit()

    def _fit(self):
        """Put the end labels on the row so the colour strip keeps at least LEGEND_STRIP_MIN_SHARE
        of it: the full labels if they fit, else the short pair, else the short pair elided. Every
        shortened legend carries its full wording in the tooltip. Runs on a label change or a
        resize — never on the tick."""
        lo, hi = self._full
        if hi == "":                                     # a hint: one message, no strip to protect
            self.lo_label.setText(lo)
            self.hi_label.setText("")
            self.setToolTip(self._tip)
            return
        m = self.layout().contentsMargins()
        row = self.width() - m.left() - m.right() - 2 * self.layout().spacing()
        budget = max(0.0, row * (1.0 - LEGEND_STRIP_MIN_SHARE) / 2.0)
        fm = QFontMetrics(self.lo_label.font())

        def fits(pair):
            return all(fm.horizontalAdvance(t) <= budget for t in pair)

        shown = (lo, hi)
        if not fits(shown) and self._short is not None:
            shown = self._short
        if not fits(shown):
            shown = tuple(fm.elidedText(t, Qt.ElideRight, int(budget)) for t in shown)
        self.lo_label.setText(shown[0])
        self.hi_label.setText(shown[1])
        shortened = shown != (lo, hi)
        self.setToolTip("\n".join(t for t in ((f"{lo} → {hi}" if shortened else ""), self._tip) if t))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit()

    def recolor(self):
        """Repaint the legend strip in the ACTIVE palette's rainbow (after a palette flip)."""
        self._strip.set_colors([QColor(c) for c in rainbow_colors(MAP_RAINBOW_N)])


# --------------------------------------------------------------- map key/legend (C3)
_LEGEND_ROW_H = 18        # px per key row
_LEGEND_GLYPH_W = 22      # px column reserved for the glyph
_LEGEND_PAD = 8           # px inner padding of the plate
_LEGEND_GAP = 6           # px between the glyph column and its label
# A floor for the plate, so a collapsed key (title + caret only) never shrinks to a stub
# that reads as a rendering fault rather than a control. The EXPANDED width is measured
# from the labels (see _MapLegend._relayout).
_LEGEND_MIN_W = 140       # px
# Inset of every plate floated over the plot (the map key, the Fit button, the action notice) from
# the canvas edges. One name, because _reposition_key's clamp and the other two have to agree — as
# a literal it was spelled three times, each with a comment pointing at the other two.
_CHROME_INSET = theme.SPACE_S


class _MapLegend(QWidget):
    """A small collapsible key for the map's glyphs, anchored over the plot's bottom-left. Click
    the plate to collapse to just the title. The glyph cells are painted to match the real
    markers; labels are plain language.

    TWO collapse inputs, one painted state. `_collapsed` is the USER's choice, and it is the one
    that persists (studio/prefs.py). `_fits` is whether the canvas the plate floats over is tall
    enough for the full plate at all: the plate is a FIXED height over a canvas whose height is
    whatever the splitter leaves, and one ordinary drag takes that canvas to 72 px. Below the
    threshold the plate paints itself collapsed regardless of the user's choice — and restores
    their choice when the room comes back — so the alternative (Qt clipping 42 px off the TOP of a
    106 px plate, taking the title row and the caret that are the only sign it collapses at all)
    cannot happen. See MapView._reposition_key."""

    # Each row: (kind, label). `kind` selects the painter below.
    _ROWS = (
        ("marker", "Video position"),
        ("brake", "Brake point"),
        ("corner", "Corner apex (C#)"),
        ("start", "Drag = start / sector line"),
    )

    # The plate is its own only control and a 5 px caret was the only thing saying so, so the
    # gesture is named in the tooltip — and when there is no room to expand, the plate says THAT
    # instead of offering a click that cannot do anything (the _set_control_enabled idiom the chart
    # controls use for a dead toggle).
    _TIP_EXPANDED = "Map key — what the marks on the track mean. Click it to collapse."
    _TIP_COLLAPSED = "Map key — what the marks on the track mean. Click it to expand."
    _TIP_NO_ROOM = ("Map key — collapsed because the map panel is too short to show it. Drag the "
                    "splitter under the map down (or maximize the panel) to open it.")
    _TIP_FOLDED = ("Map key — folded while a pass is zoomed, so it covers none of the corners "
                   "around it. Click it to open.")

    def __init__(self, on_resize=None, on_toggle=None, collapsed: bool = False):
        super().__init__()
        self._collapsed = bool(collapsed)   # the USER's choice — the value that persists
        self._fits = True                   # room on the canvas for the full plate (set by MapView)
        # A THIRD input, the pass zoom's (MapView.zoom_to_pass): folded to the title while a pass
        # is zoomed, so the plate covers none of its neighbours' labels. Like `_fits` it is a
        # display state: the user's own choice is never touched, and comes back with the zoom's end.
        self._folded = False
        # A still picture of the plate (the report's map, app._grab_report_map_png): no caret, as a
        # disclosure control in an image is one a click can never open. The title keeps its column.
        self._document = False
        self._on_resize = on_resize  # MapView re-pins the key when collapse changes its height
        self._on_toggle = on_toggle  # MapView persists the user's choice
        # The brake glyphs CURRENTLY on the plot, as [(pyqtgraph symbol, fill hex, edge hex)] —
        # pushed by MapView.set_brake_markers. Empty until a lap is drawn; see _paint_glyph.
        self.brake_glyphs: list[tuple[str, str, str]] = []
        self._font = theme.ui_font(theme.CAPTION)
        self._title_font = theme.ui_font(theme.PANEL_HEADER, theme.W_SEMIBOLD)
        # One-entry cache for the title caret (see _caret_pixmap): paintEvent runs whenever the
        # plot under this plate repaints, so building a qtawesome icon in it would be a per-frame
        # cost during playback.
        self._caret_cache: dict = {}
        self.setAttribute(Qt.WA_TransparentForMouseEvents, False)
        self._relayout()

    @classmethod
    def expanded_height(cls) -> int:
        """Height of the full plate — title row + one row per key row, regardless of state."""
        return _LEGEND_PAD * 2 + _LEGEND_ROW_H * (1 + len(cls._ROWS))

    @classmethod
    def collapsed_height(cls) -> int:
        """Height of the title-only plate, regardless of state."""
        return _LEGEND_PAD * 2 + _LEGEND_ROW_H

    def collapsed(self) -> bool:
        """The USER's collapse choice — what gets persisted. NOT what is painted: a canvas too
        short for the full plate paints it collapsed anyway (see set_available_height)."""
        return self._collapsed

    def painted_collapsed(self) -> bool:
        """What is actually on screen right now — the user's choice OR a canvas with no room."""
        return self._collapsed or not self._fits or self._folded

    def set_folded(self, on: bool) -> bool:
        """Fold the plate to its title for a pass zoom (True) or give back the user's own state
        (False). Returns True when that changed the plate's height (the caller re-pins it)."""
        was = self.painted_collapsed()
        self._folded = bool(on)
        self._relayout()
        self.update()
        return was != self.painted_collapsed()

    def set_available_height(self, avail: int) -> bool:
        """Tell the plate how much canvas height it may occupy (the canvas minus both insets), so
        it can fall back to its title row instead of being clipped. Returns True when that changed
        the plate's height (the caller then re-pins it)."""
        fits = int(avail) >= self.expanded_height()
        if fits == self._fits:
            return False
        self._fits = fits
        self._relayout()
        self.update()
        return True

    def _relayout(self):
        rows = 0 if self.painted_collapsed() else len(self._ROWS)
        # MEASURED width, not a typed one. This was `self._w = 196` under a comment claiming it was
        # "sized to the widest label + glyph column" — a claim nothing checked. It happens to hold
        # today (measured in the shipped face at 1x and 2x: the widest row, "Drag = start / sector
        # line", needs 126 px of the 152 the painter gives it, and the plate needs 170 of its 196),
        # so this is not a live clip — but a typed width over a label column is one copy edit away
        # from one, and the review filed it as already clipping on some build. Asking the font
        # settles it permanently: a longer label, a different face or a weight change now widens the
        # plate instead of running off it.
        title_fm = QFontMetrics(self._title_font)
        row_fm = QFontMetrics(self._font)
        label_col = _LEGEND_PAD + _LEGEND_GLYPH_W + _LEGEND_GAP
        widest = max([title_fm.horizontalAdvance("Map key")]
                     + [row_fm.horizontalAdvance(label) for _kind, label in self._ROWS])
        self._w = max(_LEGEND_MIN_W, label_col + widest + _LEGEND_PAD)
        self._h = _LEGEND_PAD * 2 + _LEGEND_ROW_H + rows * _LEGEND_ROW_H
        self.setFixedSize(self._w, self._h)
        # Cursor + tooltip are part of the layout state: whether the plate is a control at all
        # depends on whether it has room to open.
        self.setCursor(Qt.PointingHandCursor if self._fits else Qt.ArrowCursor)
        self.setToolTip(self._TIP_NO_ROOM if not self._fits
                        else self._TIP_COLLAPSED if self._collapsed
                        else self._TIP_FOLDED if self._folded else self._TIP_EXPANDED)

    def set_brake_glyphs(self, glyphs):
        """Tell the key which brake glyphs the map is drawing right now, [(symbol, colour)].

        The key used to paint one hard-coded ▼ in the VIDEO-MARKER's then-coral for "Brake point" — a
        picture of no lap on the canvas. Since the glyphs carry lap identity in shape as well as
        hue (theme.SERIES_SYMBOL), a fixed drawing cannot describe them: in compare mode there are
        two, in two different shapes, in neither of those colours. So the key is fed the real
        thing and mirrors it."""
        # (symbol, fill, edge) — a bare (symbol, colour) pair is a filled glyph with a canvas rim.
        glyphs = [(str(g[0]), QColor(g[1]).name().upper(),
                   QColor(g[2] if len(g) > 2 else C.canvas).name().upper()) for g in glyphs]
        if glyphs != self.brake_glyphs:
            self.brake_glyphs = glyphs
            self.update()

    def mousePressEvent(self, event):
        # Click anywhere on the key toggles collapse — the whole plate is the affordance.
        # Swallowed (not ignored) while there is no room to expand: propagating it would start a
        # pan of the plot underneath, and silently flipping the stored choice would mean the click
        # changed nothing on screen but something on disk. The tooltip says why instead.
        event.accept()
        if not self._fits:
            return
        if self._folded:
            self._folded = False            # the click opens what the zoom folded, for this zoom
            if not self._collapsed:         # ...and changes nothing he chose
                self._relayout()
                if self._on_resize is not None:
                    self._on_resize()
                self.update()
                return
        self._collapsed = not self._collapsed
        self._relayout()
        if self._on_resize is not None:  # the plate changed height — re-pin it to the corner
            self._on_resize()
        if self._on_toggle is not None:  # remember it across recordings and launches
            self._on_toggle(self._collapsed)
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
        collapsed = self.painted_collapsed()
        # The caret sits in the SAME glyph column the key rows use, so the title word starts on the
        # label column instead of 6 px short of it (it used to be a character inside the title).
        if not self._document:
            caret = self._caret_pixmap(collapsed)
            cw = caret.width() / caret.devicePixelRatio()
            ch = caret.height() / caret.devicePixelRatio()
            p.drawPixmap(QPointF(_LEGEND_PAD + (_LEGEND_GLYPH_W - cw) / 2.0,
                                 _LEGEND_PAD + (_LEGEND_ROW_H - ch) / 2.0), caret)
        tx = _LEGEND_PAD + _LEGEND_GLYPH_W + _LEGEND_GAP
        p.drawText(QRectF(tx, _LEGEND_PAD, self._w - tx - _LEGEND_PAD, _LEGEND_ROW_H),
                   int(Qt.AlignVCenter | Qt.AlignLeft), "Map key")
        if collapsed:
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

    def _caret_pixmap(self, collapsed: bool):
        """The title row's disclosure caret, as a Phosphor pixmap at this screen's pixel ratio.

        It used to be a literal "▾"/"▸" in the title string. Neither mark is in Inter, so Qt fell
        back to .AppleSystemUIFont and painted 3.2x4.5 px of ink at CAPTION — against 8.7x8.0 for
        the "M" beside it, and 40% of ph.caret-down's 10x6 — for the one mark that says this plate
        is a control at all. Cached one entry deep, keyed by state + ratio + tone, because this
        widget repaints with the plot under it."""
        key = (collapsed, round(self.devicePixelRatioF(), 3), C.text_dim)
        px = self._caret_cache.get(key)
        if px is None:
            px = icon("ph.caret-right" if collapsed else "ph.caret-down", color=C.text_dim).pixmap(
                QSize(theme.ICON_PX, theme.ICON_PX), self.devicePixelRatioF())
            self._caret_cache = {key: px}
        return px

    def _paint_glyph(self, p: QPainter, kind: str, cell: QRectF):
        """Draw one key glyph centred in `cell`, mirroring the on-map marker for that kind."""
        cx, cy = cell.center().x(), cell.center().y()
        if kind == "marker":  # filled MARKER_COLOR ring — the video position marker
            mc = QColor(MARKER_COLOR)
            p.setPen(QPen(mc, 2))
            fill = QColor(MARKER_COLOR)
            fill.setAlpha(110)
            p.setBrush(QBrush(fill))
            p.drawEllipse(QPointF(cx, cy), 5, 5)
        elif kind == "brake":  # the brake glyphs on the plot RIGHT NOW, in their own shapes/hues
            glyphs = self.brake_glyphs or [
                (theme.series_symbol(theme.CHART_SERIES[0]), BRAKE_COLOR, C.canvas)]
            # Side by side when compare draws two laps; the cell is _LEGEND_GLYPH_W wide, so the
            # glyph shrinks rather than the plate growing or the two overlapping.
            n = min(len(glyphs), 2)
            box = min(_LEGEND_GLYPH_W / n - 2.0, _LEGEND_ROW_H - 6.0)
            for i, (sym, fill, edge) in enumerate(glyphs[:n]):
                gx = cx + (i - (n - 1) / 2.0) * (box + 2.0)
                path = QTransform().translate(gx, cy).scale(box, box).map(
                    _SYMBOLS.get(sym, _SYMBOLS["o"]))
                # Same fill and rim the markers carry (_brake_style): lap B's is hollow.
                hollow = QColor(edge) != QColor(C.canvas)
                p.setPen(QPen(QColor(edge), 1.5 if hollow else 1))
                p.setBrush(QBrush(QColor(fill)))
                p.drawPath(path)
        elif kind == "corner":  # cyan apex dot (the left/right hues collapse to one in the key)
            qc = QColor(CORNER_LEFT_COLOR)
            qc.setAlpha(CORNER_DOT_ALPHA)
            p.setPen(QPen(QColor(CORNER_DOT_OUTLINE), 1))   # the rim the canvas dots carry
            p.setBrush(QBrush(qc))
            p.drawEllipse(QPointF(cx, cy), 3.5, 3.5)
        elif kind == "start":  # the draggable timing-line handles — BOTH of them
            # This row names two things ("Drag = start / sector line") and used to paint one: a
            # single START_COLOR crosshair, while the map draws start lines in START_COLOR (amber)
            # and every SECTOR line in SECTOR_COLOR (grey). Measured on the shipped plate, the grey
            # the map paints each sector line in was 125 RGB units from the nearest pixel this cell
            # contained — i.e. nowhere in it. That is W11-01's defect one row along (the brake row
            # promised a glyph in a colour no brake glyph is ever drawn in), so it takes W11-01's
            # answer: mirror what is on the canvas. Two crosshairs, side by side in the one cell,
            # the same way the brake row already draws compare's two lap glyphs.
            arm, gap = 3.5, 1.5
            for dx, colour in ((-(arm + gap), START_COLOR), (arm + gap, SECTOR_COLOR)):
                p.setPen(QPen(QColor(colour), 1.5))
                p.drawLine(QPointF(cx + dx - arm, cy), QPointF(cx + dx + arm, cy))
                p.drawLine(QPointF(cx + dx, cy - arm), QPointF(cx + dx, cy + arm))


class _LapOverlay:
    """Draws one lap as solid measured + dashed inferred gap-fill items; tracks them for clear/redraw."""

    def __init__(self, plot, color, base_width, hero: bool = False):
        self.plot = plot
        self.color = color
        self.base_width = base_width
        # hero: the followed lap — round-capped measured runs over a dark casing (see HERO_WIDTH).
        self.hero = hero
        self.lap_id = None
        self._items: list = []
        self._inferred: list[bool] = []   # per item: dashed gap-fill? (parallel to _items; see repen)
        # hero only: the dark casing under the measured runs, over the faint best lap (z=0) and
        # under the hero itself (z=1)
        self._casing = _Casing(plot, z=0.5) if hero else None
        # Hidden in place (not rebuilt) while the rainbow paints the lap, so toggling it off restores
        # the same items/pens.
        self.visible = True

    def _clear(self):
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []
        self._inferred = []
        if self._casing is not None:
            self._casing.clear()

    def _solid_pen(self):
        if self.hero:
            return _hero_pen(self.color, self.base_width)
        return pg.mkPen(self.color, width=theme.line_width(self.base_width))

    def repen(self):
        """Re-apply the pens to the already-drawn items, at the CURRENT device-pixel ratio.

        The geometry never moves — only the widths change — so this is a re-pen, not a rebuild.
        Which of the two pens (measured solid / inferred dashed) an item carries is remembered
        alongside it rather than read back off the item."""
        solid = self._solid_pen()
        dashed = _inferred_pen(self.color, self.base_width)
        for it, inferred in zip(self._items, self._inferred, strict=True):
            it.setPen(dashed if inferred else solid)
        if self._casing is not None:
            self._casing.repen()

    def set_visible(self, on: bool):
        """Show/hide the existing items in place — no rebuild. Items created later inherit the
        state via set_lap."""
        self.visible = on
        for it in self._items:
            it.setVisible(on)
        if self._casing is not None:
            self._casing.set_visible(on)

    def set_lap(self, session: Session, lap_id: int | None):
        """(Re)draw `lap_id` (or clear if None). No-op if unchanged."""
        if lap_id == self.lap_id and self._items:
            return
        self._clear()
        self.lap_id = lap_id
        if lap_id is None:
            return
        solid = self._solid_pen()
        dashed = _inferred_pen(self.color, self.base_width)
        segs = list(session.lap_trace_segments(lap_id))
        if self._casing is not None:
            self._casing.set_runs([(s.xs, s.ys) for s in segs if s.measured])
        for seg in segs:
            pen = solid if seg.measured else dashed
            item = self.plot.plot(seg.xs, seg.ys, pen=pen)
            if self.hero:
                item.setZValue(1)
            if not self.visible:
                item.setVisible(False)
            self._items.append(item)
            self._inferred.append(not seg.measured)

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
                              pen=pg.mkPen(self.color, width=theme.line_width(self.base_width)))
        if not self.visible:
            item.setVisible(False)
        self._items.append(item)
        self._inferred.append(False)

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
        for xs, ys in _trace_runs(session):
            item = plot.plot(xs, ys, pen=pg.mkPen(TRACE_COLOR, width=theme.line_width(TRACE_WIDTH)))
            item.setZValue(-5)  # below the lap overlays/rainbow (see the marker's z-order note)
            self._items.append(item)

    def repen(self):
        """Re-pen at the current device-pixel ratio (the points never move — see repen above)."""
        for it in self._items:
            it.setPen(pg.mkPen(TRACE_COLOR, width=theme.line_width(TRACE_WIDTH)))

    def bounds(self):
        """NaN-safe (x_lo, x_hi, y_lo, y_hi) over the drawn trace, or None if nothing is drawn.
        Memoized: _fit_view runs on every resize, and this is the widest array of the three."""
        if self._bounds is None:
            self._bounds = _items_bounds(self._items)
        return self._bounds


def _overlap(cand, hw: float, hh: float, boxes) -> np.ndarray:
    """Summed px² overlap of each candidate label box (centre rows of `cand`, half-extents hw x hh)
    with `boxes` ((cx, cy, half-w, half-h) rows)."""
    if len(boxes) == 0:
        return np.zeros(len(cand))
    ox = (np.minimum(cand[:, None, 0] + hw, boxes[None, :, 0] + boxes[None, :, 2])
          - np.maximum(cand[:, None, 0] - hw, boxes[None, :, 0] - boxes[None, :, 2]))
    oy = (np.minimum(cand[:, None, 1] + hh, boxes[None, :, 1] + boxes[None, :, 3])
          - np.maximum(cand[:, None, 1] - hh, boxes[None, :, 1] - boxes[None, :, 3]))
    return (np.clip(ox, 0.0, None) * np.clip(oy, 0.0, None)).sum(axis=1)


def _outside(cand, hw: float, hh: float, frame) -> np.ndarray:
    """px² of each candidate label box that falls outside `frame` (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = frame
    w = np.clip(np.minimum(cand[:, 0] + hw, x1) - np.maximum(cand[:, 0] - hw, x0), 0.0, None)
    h = np.clip(np.minimum(cand[:, 1] + hh, y1) - np.maximum(cand[:, 1] - hh, y0), 0.0, None)
    return 4.0 * hw * hh - w * h


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
        self.highlighted: str | None = None   # the ring shown: the hovered corner, else the pinned
        self.pinned: str | None = None
        self.hovered: str | None = None
        # Per-tick marker-avoidance state, parallel lists over the label TextItems (see avoid_point):
        # the item, its LAID-OUT home position (data units), the outward unit vector used to place it
        # (the degenerate-overlap fallback direction), and the position currently applied to the item.
        self._texts: list = []
        self._home: list[tuple[float, float]] = []
        self._outward: list[tuple[float, float]] = []
        self._applied: list[tuple[float, float]] = []
        # Each drawn label's half-extents (px), measured off its own plate; the placement's
        # extra obstacles ((x, y, half px) — the brake glyphs); the px-per-data scale it ran at.
        self._half: list[tuple[float, float]] = []
        self._obstacles: list[tuple[float, float, float]] = []
        self.layout_scale: tuple[float, float] = (1.0, 1.0)

    def _px_per_data(self) -> tuple[float, float]:
        """(px-per-data-x, px-per-data-y) from the viewbox, to convert px offsets into data coords.
        Falls back to 1.0 before the widget has a size."""
        vb = self.plot.getViewBox()
        rect = vb.viewRect()          # data-space rect currently shown
        size = vb.boundingRect()      # px-space rect of the viewbox
        if rect.width() <= 0 or rect.height() <= 0 or size.width() <= 0 or size.height() <= 0:
            return 1.0, 1.0
        return size.width() / rect.width(), size.height() / rect.height()

    def set_corners(self, markers, start_anchors=None, obstacles=None):
        """(Re)build labels + apex dots from (label,x,y,direction) markers ([] clears; also clears
        any highlight), then place the labels (`_label_positions`): clear of each other, of the
        start/finish cluster (``start_anchors`` = local-metre (x,y) points a label must clear — the
        start line's two endpoints + the video-position marker, or None/[]) and of ``obstacles``
        ((x, y, half-extent px) — the brake glyphs). Every label is drawn; dots always are."""
        self.hovered = None
        self.set_highlight(None)
        self._markers = list(markers)
        for it in self._items:
            self.plot.removeItem(it)
        self._items = []
        self._texts, self._home, self._outward, self._applied = [], [], [], []
        self._half = []
        self._obstacles = list(obstacles or [])
        if not markers:
            return
        for direction, colour in ((1, CORNER_LEFT_COLOR), (-1, CORNER_RIGHT_COLOR)):
            pts = [(x, y) for _label, x, y, d in markers if d == direction]
            if not pts:
                continue
            qc = pg.mkColor(colour)
            qc.setAlpha(CORNER_DOT_ALPHA)
            # The canvas rim is what separates a dot from the painted line under it — see
            # CORNER_DOT_OUTLINE. Its width is the brake glyphs' own, for the same reason.
            dots = pg.ScatterPlotItem(
                pos=pts, size=7, brush=pg.mkBrush(qc), pxMode=True,
                pen=pg.mkPen(CORNER_DOT_OUTLINE, width=theme.line_width(1)))
            # Above the rainbow (z=5): at the same z the lazily-created bucket items, added later,
            # painted over the dots — invisible under a 3 px line, a hole in a 6 px one.
            dots.setZValue(5.5)  # below the labels (6) and the marker (z=10)
            self.plot.addItem(dots)
            self._items.append(dots)
        for label, _x, _y, _d in markers:
            # fill = a translucent dark plate behind the glyphs (the "halo"); border None keeps
            # it subtle. Anchor centred on the offset point so the nudge reads symmetrically.
            text = pg.TextItem(text=label, color=CORNER_LABEL_COLOR, anchor=(0.5, 0.5),
                               fill=pg.mkBrush(CORNER_LABEL_HALO))
            text.setFont(self._font)
            text.setZValue(6)
            self.plot.addItem(text)
            self._items.append(text)
            self._texts.append(text)
            # The drawn plate, in px (a TextItem ignores the view's scale), floored at the old box.
            box = text.textItem.boundingRect()
            self._half.append((max(box.width(), CORNER_LABEL_BOX_PX[0]) / 2.0,
                               max(box.height(), CORNER_LABEL_BOX_PX[1]) / 2.0))
        self._place(self._label_positions(markers, start_anchors))

    def relayout(self, start_anchors=None, obstacles=None):
        """Re-place the existing labels — the same items, new homes — after the view's scale, the
        brake glyphs or the start line changed. Never called from the tick."""
        if not self._texts:
            return
        self._obstacles = list(obstacles or [])
        self._place(self._label_positions(self._markers, start_anchors))

    def _place(self, positions):
        self._home = [(float(x), float(y)) for x, y in positions]
        self._applied = list(self._home)
        for text, (lx, ly) in zip(self._texts, self._home, strict=True):
            text.setPos(lx, ly)
        self.layout_scale = self._px_per_data()

    def _label_positions(self, markers, start_anchors):
        """Each label's (lx, ly) draw position in data units, placed where it overlaps least.

        All the collision reasoning is in PX space (data scaled by `_px_per_data`; y stays up, which
        no overlap test cares about). Labels are placed one at a time, the most crowded apex first,
        each over CORNER_LABEL_DIRS x CORNER_LABEL_STEPS_PX candidate spots (see CORNER_LABEL_DIRS):
        a spot's cost is its overlap with the labels already placed (x10), with the start/finish
        cluster, the brake glyphs and every apex dot (x4) and with the area outside the canvas (x4),
        plus a little for distance and for turning away from the outward direction — so a label
        with room sits exactly where it always did, just outside its apex."""
        sx, sy = self._px_per_data()
        apex = np.array([[float(x) * sx, float(y) * sy] for _l, x, y, _d in markers], float)
        half = np.array(self._half if len(self._half) == len(markers)
                        else [(CORNER_LABEL_BOX_PX[0] / 2.0, CORNER_LABEL_BOX_PX[1] / 2.0)]
                        * len(markers), float)
        centre = apex.mean(axis=0)
        # Obstacles as (cx, cy, half-w, half-h) px boxes.
        obs = [(ax, ay, CORNER_DOT_HALF_PX, CORNER_DOT_HALF_PX) for ax, ay in apex]
        # An anchor is (x, y) or (x, y, half-extent px); a bare pair is marker-sized.
        anchors = [(float(a[0]) * sx, float(a[1]) * sy,
                    float(a[2]) if len(a) > 2 else CORNER_MARKER_CLEAR_PX)
                   for a in (start_anchors or [])]
        obs += [(ax, ay, h, h) for ax, ay, h in anchors]
        if len(anchors) >= 2:                       # the start line's own segment, every 3 px
            (ax, ay, _h), (bx, by, _h2) = anchors[0], anchors[1]
            n = max(2, int(np.hypot(bx - ax, by - ay) / 3.0))
            obs += [(ax + (bx - ax) * k / n, ay + (by - ay) * k / n, 2.5, 2.5) for k in range(n + 1)]
        boxes = np.array(obs, float).reshape(-1, 4)
        # The brake glyphs are SOFT: better a glyph's corner on a plate than a label so far from its
        # apex that it names the wrong corner.
        soft = np.array([(float(x) * sx, float(y) * sy, h, h) for x, y, h in self._obstacles],
                        float).reshape(-1, 4)
        view = self.plot.getViewBox().viewRect()
        frame = (view.left() * sx, view.top() * sy, view.right() * sx, view.bottom() * sy)
        frame = (min(frame[0], frame[2]), min(frame[1], frame[3]),
                 max(frame[0], frame[2]), max(frame[1], frame[3]))
        dist = np.hypot(*(apex[:, None, :] - apex[None, :, :]).transpose(2, 0, 1))
        order = np.argsort(-(dist < 60.0).sum(axis=1), kind="stable")
        steps = np.asarray(CORNER_LABEL_STEPS_PX)
        turn = np.array([0.0] + [s * k for k in range(1, CORNER_LABEL_DIRS // 2)
                                 for s in (1.0, -1.0)] + [CORNER_LABEL_DIRS / 2.0])
        penalty = 1.0 * np.tile(steps, len(turn)) + 3.0 * np.repeat(np.abs(turn), len(steps))
        self._outward = [(0.0, 0.0)] * len(markers)
        cands = []
        for i in range(len(markers)):
            hw, hh = half[i]
            ox, oy = apex[i] - centre
            norm = float(np.hypot(ox, oy)) or 1.0
            self._outward[i] = (ox / norm, oy / norm)
            theta = np.arctan2(oy, ox) + turn * 2.0 * np.pi / CORNER_LABEL_DIRS
            c, sn = np.cos(theta), np.sin(theta)
            with np.errstate(divide="ignore"):
                d0 = np.minimum((hw + CORNER_LABEL_AIR_PX + CORNER_DOT_HALF_PX) / np.abs(c),
                                (hh + CORNER_LABEL_AIR_PX + CORNER_DOT_HALF_PX) / np.abs(sn))
            d = (d0[:, None] + steps[None, :]).ravel()
            unit = np.stack([np.repeat(c, len(steps)), np.repeat(sn, len(steps))], 1)
            cand = apex[i] + unit * d[:, None]
            # What does not depend on the other labels is costed once.
            fixed = (4.0 * _overlap(cand, hw, hh, boxes) + 1.5 * _overlap(cand, hw, hh, soft)
                     + 4.0 * _outside(cand, hw, hh, frame) + penalty)
            cands.append((cand, fixed))
        out = np.zeros_like(apex)
        done: list[int] = []
        # Greedy, most crowded first; then ONE more pass that re-places each label against all the
        # others, which frees the spot an early label took from a later, more cornered one.
        for i in [*order, *order]:
            cand, fixed = cands[i]
            hw, hh = half[i]
            # (+1 px each: two plates kept a pixel apart, not kissing)
            others = np.array([[*out[j], half[j][0] + 1.0, half[j][1] + 1.0]
                               for j in done if j != i]).reshape(-1, 4)
            best = int(np.argmin(fixed + 10.0 * _overlap(cand, hw, hh, others)))
            out[i] = cand[best]
            if i not in done:
                done.append(i)
        return [(px / sx, py / sy) for px, py in out]

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
        nudged = 0
        for i, text in enumerate(self._texts):
            # Combined half-extents: half the label's own plate plus the marker's half-extent.
            lw, lh = (self._half[i] if i < len(self._half)
                      else (CORNER_LABEL_BOX_PX[0] / 2.0, CORNER_LABEL_BOX_PX[1] / 2.0))
            half_w = lw + CORNER_MARKER_CLEAR_PX
            half_h = lh + CORNER_MARKER_CLEAR_PX
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
        """Ring-highlight one corner's apex by label (None / unknown clears). Display-only. This
        is the PINNED ring (a Corners/Stats/Coaching row); a hover rings over it (set_hover)."""
        self.pinned = label
        self._ring(self.hovered or label)

    def set_hover(self, label: str | None):
        """Ring the hovered corner (None hands the ring back to the pinned one)."""
        if label != self.hovered:
            self.hovered = label
            self._ring(label or self.pinned)

    def hit(self, scene_pos) -> str | None:
        """The corner whose label plate or apex dot is under `scene_pos` (a scene point), or
        None. O(corners): one rect test per label, one mapped point per apex."""
        vb = self.plot.getViewBox()
        for text, (label, x, y, _d) in zip(self._texts, self._markers, strict=False):
            if text.sceneBoundingRect().contains(scene_pos):
                return label
            at = vb.mapViewToScene(QPointF(float(x), float(y)))
            if (abs(at.x() - scene_pos.x()) <= CORNER_HOVER_PX
                    and abs(at.y() - scene_pos.y()) <= CORNER_HOVER_PX):
                return label
        return None

    def _ring(self, label: str | None):
        if self._highlight_item is not None:
            self.plot.removeItem(self._highlight_item)
            self._highlight_item = None
        self.highlighted = None
        if label is None:
            return
        for lbl, x, y, _d in self._markers:
            if lbl == label:
                ring = pg.ScatterPlotItem(
                    size=CORNER_HIGHLIGHT_SIZE, brush=pg.mkBrush(None),
                    pen=pg.mkPen(CORNER_HIGHLIGHT_COLOR,
                                 width=theme.line_width(CORNER_HIGHLIGHT_PEN_W)),
                    pxMode=True)
                # Two spots, one place, painted in order: the wide canvas rim, then the ring on
                # it (see CORNER_HIGHLIGHT_HALO_W). One item, so the ring and its rim are moved,
                # hidden and removed as the one cue they are.
                at = (float(x), float(y))
                ring.addPoints([
                    {"pos": at, "pen": pg.mkPen(
                        C.canvas, width=theme.line_width(CORNER_HIGHLIGHT_HALO_W))},
                    {"pos": at}])
                ring.setZValue(7)  # above corner dots/labels, below the marker
                self.plot.addItem(ring)
                self._highlight_item = ring
                self.highlighted = lbl
                return


def _brake_style(lap_colour, k: int) -> tuple[str, QColor, QColor]:
    """(symbol, fill, edge) of the map's brake glyphs for the k-th lap pushed (0 = primary / lap A,
    1 = compare's lap B): the lap's SERIES_SYMBOL shape, the map's one BRAKE_COLOR — filled with a
    canvas rim for lap A, a BRAKE_COLOR rim on the dark plate for lap B."""
    symbol = theme.series_symbol(lap_colour)
    if k:
        return symbol, QColor(BRAKE_HOLLOW_PLATE), QColor(BRAKE_COLOR)
    return symbol, QColor(BRAKE_COLOR), QColor(C.canvas)


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
        for k, (markers, colour) in enumerate(lap_markers):
            if not markers:
                continue
            spots = [{"pos": (x, y), "size": theme.brake_glyph_size(d)} for x, y, d in markers]
            # The speed chart's identity contract, minus its hue: shape by CHART_SERIES slot, in
            # the map's own brake colour (BRAKE_COLOR); lap B hollow (see the note there). The
            # canvas outline keeps a filled glyph off the ribbon it sits on.
            symbol, fill, edge = _brake_style(colour, k)
            dots = pg.ScatterPlotItem(
                symbol=symbol, brush=pg.mkBrush(fill), pxMode=True,
                pen=pg.mkPen(edge, width=theme.line_width(BRAKE_HOLLOW_PEN_W if k else 1)))
            dots.addPoints(spots)
            dots.setZValue(7)  # above corner dots, below the marker
            self.plot.addItem(dots)
            self._items.append(dots)


def _finish_paths(ux: float, uy: float):
    """(backing, squares, arrow, size) — the chequered bar square across the direction of travel
    (ux, uy) and the arrow past it, as QPainterPaths in a pxMode symbol's unit box, plus the symbol
    size in px. The geometry is `map_render.finish_mark_geometry`'s (screen space, y down), the one
    the hero and the share card paint; the aspect lock keeps angles true, so the mark is
    pre-rotated once per start-line change."""
    backing_q, squares_q, arrow_q, reach = map_render.finish_mark_geometry(ux, uy)
    size = 2.0 * (reach + 1.5)

    def poly(pts):
        return QPolygonF([QPointF(x / size, y / size) for x, y in pts])

    backing = QPainterPath()
    backing.addPolygon(poly(backing_q))
    backing.closeSubpath()
    squares = QPainterPath()
    for q in squares_q:
        squares.addPolygon(poly(q))
        squares.closeSubpath()
    arrow = QPainterPath()
    arrow.addPolygon(poly(arrow_q))
    arrow.closeSubpath()
    return backing, squares, arrow, size


class _FinishMark:
    """The start/finish mark: one pxMode ScatterPlotItem of two spots at the start line's crossing
    of the trace (see FINISH_LIGHT). Rebuilt when the start line is set or released; never on the
    tick. `at` is (x, y, ux, uy) — the crossing and the unit direction of travel — or None."""

    def __init__(self, plot):
        # Item-level defaults the spots override; set so nothing reads pyqtgraph's stock blue disc.
        self.item = pg.ScatterPlotItem(pxMode=True, symbol="s", pen=pg.mkPen(None),
                                       brush=pg.mkBrush(FINISH_DARK))
        self.item.setZValue(7.5)   # over the brake glyphs (7), under the provisional label (8)
        plot.addItem(self.item)
        self.at: tuple[float, float, float, float] | None = None

    def update(self, tx, ty, seg: Seg | None):
        self.at = None if seg is None else map_render.start_crossing(tx, ty, seg)
        self.item.clear()
        if self.at is None:
            return
        x, y, ux, uy = self.at
        backing, squares, arrow, size = _finish_paths(ux, uy)
        # The squares sit on their own dark backing; the arrow sits on the line, so it carries a
        # dark rim (the brake glyphs' answer to a light ink over the colour-blind ramp's light mid).
        self.item.addPoints([
            {"pos": (x, y), "size": size, "symbol": backing, "pen": pg.mkPen(None),
             "brush": pg.mkBrush(FINISH_DARK)},
            {"pos": (x, y), "size": size, "symbol": squares, "pen": pg.mkPen(None),
             "brush": pg.mkBrush(FINISH_LIGHT)},
            {"pos": (x, y), "size": size, "symbol": arrow,
             "pen": pg.mkPen(FINISH_DARK, width=theme.line_width(1)),
             "brush": pg.mkBrush(FINISH_LIGHT)}])


class _RaceLayer:
    """The Lap Race on the map (studio/race_controller.py drives it): every raced lap as a dot on
    its own lap clock, the five fastest in their series colours with a short trail, the rest grey.

    BUILT ONCE AT ENTRY, MOVED PER TICK. Two ScatterPlotItems hold the dots — the grey field, then
    the coloured five — with sizes and brushes built once; one ring marks the selected lap; one
    PlotCurveItem per coloured lap is its trail; one translucent veil dims everything under it (the
    base lap line, brake glyphs, rainbow, corner labels) whatever those layers rebuild meanwhile,
    so the exit is the veil's removal. Every item goes in with `ignoreBounds=True`: an item that
    joins the bounds makes every update re-run pyqtgraph's auto-range bookkeeping (measured +9 ms
    per update on the 36-lap session). The view follows the field only under Follow (`_FollowCam`),
    off by default.

    LEGIBILITY IN A BUNCH (review of #553, looked at on the 36-lap session ~25 s in): the grey dots
    are small and darker so the coloured five read on top of them; the trails sit ABOVE the grey
    field and under the coloured dots; the coloured dots paint slowest-first so the fastest is on
    top; and the ring is thin and hugs its dot (2 px outside it), so the leader's ring does not
    swallow the pack right behind it. The start/finish mark (z 7.5) stays above the veil."""

    VEIL_Z = 7.25       # over the brake glyphs (7), under the start/finish mark (7.5)
    GREY_Z = 10.4
    TRAIL_Z = 10.5
    DOTS_Z = 11.0
    RING_Z = 11.5

    def __init__(self, plot, tracks, colours: dict[int, int], selected: int | None):
        self.plot = plot
        self.tracks = tracks
        n = len(tracks.ids)
        self.grey_k = np.array([k for k in range(n) if tracks.ids[k] not in colours], dtype=int)
        # Coloured slowest → fastest: the scatter paints in array order, the fastest on top.
        self.col_k = np.array(sorted((k for k in range(n) if tracks.ids[k] in colours),
                                     key=lambda k: -colours[tracks.ids[k]]), dtype=int)
        self.order = np.concatenate([self.grey_k, self.col_k])
        rim = pg.mkPen(C.canvas, width=theme.line_width(1))
        self.colours = colours
        self._col_brushes = [pg.mkBrush(CHART_SERIES[colours[tracks.ids[k]] % len(CHART_SERIES)])
                             for k in self.col_k]
        veil = QColor(C.canvas)
        veil.setAlpha(RACE_VEIL_ALPHA)
        self.veil = QGraphicsRectItem(QRectF(-1e6, -1e6, 2e6, 2e6))
        self.veil.setPen(QPen(Qt.NoPen))
        self.veil.setBrush(QBrush(veil))
        self.veil.setZValue(self.VEIL_Z)
        self.grey = pg.ScatterPlotItem(pxMode=True, symbol="o", size=RACE_GREY_PX,
                                       brush=pg.mkBrush(RACE_GREY), pen=rim)
        self.grey.setZValue(self.GREY_Z)
        self.dots = pg.ScatterPlotItem(pxMode=True, symbol="o", size=RACE_DOT_PX, pen=rim)
        self.dots.setZValue(self.DOTS_Z)
        self.ring = pg.ScatterPlotItem(pxMode=True, symbol="o", brush=pg.mkBrush(None),
                                       pen=pg.mkPen(C.text, width=theme.line_width(RACE_RING_W)))
        self.ring.setZValue(self.RING_Z)
        # Trails: the coloured laps only (36 trails cost +6.3 ms a tick; five, under a millisecond).
        self.trails: dict[int, pg.PlotCurveItem] = {}
        for lap_id, slot in colours.items():
            c = QColor(CHART_SERIES[slot % len(CHART_SERIES)])
            c.setAlpha(RACE_TRAIL_ALPHA)
            cur = pg.PlotCurveItem(pen=pg.mkPen(c, width=theme.line_width(RACE_TRAIL_W)))
            cur.setZValue(self.TRAIL_Z)
            self.trails[lap_id] = cur
        self._trail_k = {lap_id: tracks.index(lap_id) for lap_id in self.trails}
        self.items = [self.veil, self.grey, *self.trails.values(), self.dots, self.ring]
        for it in self.items:
            plot.addItem(it, ignoreBounds=True)
        self.selected = selected

    def positions(self) -> tuple[np.ndarray, np.ndarray]:
        """Every drawn dot's (x, y), in `order` (grey field, then the coloured five) — for tests."""
        gx, gy = self.grey.getData()
        cx, cy = self.dots.getData()
        return np.concatenate([gx, cx]), np.concatenate([gy, cy])

    def update(self, state):
        """Move every dot to `state` (a `lap_race.RaceState`) — two setData, no item added."""
        g, c = self.grey_k, self.col_k
        self.grey.setData(x=state.x[g], y=state.y[g])
        self.dots.setData(x=state.x[c], y=state.y[c], brush=self._col_brushes)
        if self.selected is not None and self.selected in state.ids:
            k = state.ids.index(self.selected)
            dot = RACE_DOT_PX if self.selected in self.colours else RACE_GREY_PX
            self.ring.setData(x=[float(state.x[k])], y=[float(state.y[k])],
                              size=dot + 2 * RACE_RING_GAP_PX + RACE_RING_W)
        else:
            self.ring.setData(x=[], y=[])
        t = state.t
        for lap_id, cur in self.trails.items():
            lap = self.tracks.laps[self._trail_k[lap_id]]
            now = min(max(t, 0.0), float(lap.elapsed[-1]))
            a = int(np.searchsorted(lap.elapsed, now - RACE_TRAIL_S))
            b = int(np.searchsorted(lap.elapsed, now))
            k = self._trail_k[lap_id]
            # The lap's own samples over the last RACE_TRAIL_S, ending exactly on the dot.
            cur.setData(np.append(lap.xs[a:b], state.x[k]), np.append(lap.ys[a:b], state.y[k]))

    def set_visible(self, on: bool):
        """Show / hide every race item (the clean grabs: a document never shows a race)."""
        for it in self.items:
            it.setVisible(on)

    def remove(self):
        for it in self.items:
            self.plot.removeItem(it)
        self.items = []


class _FollowCam:
    """Where the Lap Race's Follow view looks: the ground point (`cx`, `cy`) drawn at the centre of
    the free area (the map right of the race card) and the ground width `w` (m across the whole
    map). Qt-free; `MapView._follow_apply` turns it into one `setRange` a tick.

    THE REFERENCE is the median dot, x and y apart: the bulk of the field, which one straggler or
    one lap pulling away does not drag (the selected lap is the ring, wherever it is). THE WIDTH is
    the narrowest of FOLLOW_WIDTHS_M that keeps the MIDDLE FOLLOW_FIELD_Q of the dots (the nearest
    80 % to the median) and the selected lap's dot within FOLLOW_FILL of the free area from their
    median. Fitting every dot held the demo's view at the widest step 52 % of the race for two or
    three stragglers, at 14.8 px per 0.1 s of gap; this fit gives 22.2 px and keeps the selected
    lap in view 99.2 % of the race (97.6 % fitting every dot; review of #576). It is a ladder, not a continuous fit, and shrinks only
    once the field fits well inside the next step down (FOLLOW_SHRINK): a scale change re-lays the
    corner labels, and a view that zoomed every tick would swim. THE MOTION eases in race time
    (FOLLOW_TAU_S, FOLLOW_ZOOM_TAU_S), and the median is never let outside a dead-zone box of
    FOLLOW_BOX round the free centre; with motion off the view holds still until the median leaves
    that box, then recentres in one step. A step of the race clock backwards or past FOLLOW_JUMP_S
    (a scrub, a restart) recentres at once. WHEN RACE TIME IS HELD (a pause, the finish) an
    unfinished zoom completes at once: easing runs in race time, so a held clock would leave the
    view half-zoomed and the corner labels laid for another scale for as long as it is held."""

    def __init__(self):
        self.cx = self.cy = self.w = self.goal = self.t = None
        self.ref: tuple[float, float] | None = None     # the point the dead zone holds (tests)

    @staticmethod
    def reference(xs, ys) -> tuple[float, float]:
        return float(np.median(xs)), float(np.median(ys))

    @staticmethod
    def middle(xs, ys, px: float, py: float):
        """The FOLLOW_FIELD_Q of the dots nearest the median (all of them below five dots)."""
        if len(xs) < 5:
            return xs, ys
        d = np.hypot(xs - px, ys - py)
        keep = d <= np.quantile(d, FOLLOW_FIELD_Q)
        return xs[keep], ys[keep]

    def step(self, t: float, xs, ys, free_w: float, free_h: float, view_w: float,
             motion_on: bool, held: bool = False, keep=None) -> None:
        """`keep`: the selected lap's dot (x, y), fitted with the middle of the field wherever it
        is — on the demo that holds it in view 99.2 % of the race instead of 96.3 %, for 0.2 px
        per 0.1 s of gap."""
        xs, ys = np.asarray(xs, float), np.asarray(ys, float)
        fin = np.isfinite(xs) & np.isfinite(ys)
        if not fin.any():
            return
        xs, ys = self.middle(xs[fin], ys[fin], *self.reference(xs[fin], ys[fin]))
        if keep is not None and np.isfinite(keep[0]) and np.isfinite(keep[1]):
            xs, ys = np.append(xs, keep[0]), np.append(ys, keep[1])
        px, py = self.ref = self.reference(xs, ys)  # the centre of the middle of the field
        mpp = max(2 * float(np.max(np.abs(xs - px))) / (FOLLOW_FILL * free_w),
                  2 * float(np.max(np.abs(ys - py))) / (FOLLOW_FILL * free_h))
        need = mpp * view_w
        goal = next((w for w in FOLLOW_WIDTHS_M if w >= need), FOLLOW_WIDTHS_M[-1])
        jump = self.t is None or t < self.t or t - self.t > FOLLOW_JUMP_S
        dt = 0.0 if jump else t - self.t
        self.t = t
        if jump:
            self.cx, self.cy, self.w, self.goal = px, py, goal, goal
            return
        # Only as the race runs: a resize or a repaint while paused keeps the ground width.
        if dt > 0 and (goal > self.goal or (goal < self.goal and need < FOLLOW_SHRINK * goal)):
            self.goal = goal
        if motion_on and dt > 0 and not held:
            self.w += (self.goal - self.w) * (1.0 - np.exp(-dt / FOLLOW_ZOOM_TAU_S))
            if abs(self.w - self.goal) < 0.005 * self.goal:
                self.w = self.goal
        else:                   # motion off, or race time held: the zoom completes now
            self.w = self.goal
        mpp = self.w / view_w
        bx, by = FOLLOW_BOX * free_w * mpp, FOLLOW_BOX * free_h * mpp
        if motion_on:
            a = 1.0 - float(np.exp(-dt / FOLLOW_TAU_S))
            self.cx = min(max(self.cx + (px - self.cx) * a, px - bx), px + bx)
            self.cy = min(max(self.cy + (py - self.cy) * a, py - by), py + by)
        elif abs(px - self.cx) > bx or abs(py - self.cy) > by:
            self.cx, self.cy = px, py


def _nice_length(max_m: float) -> float:
    """The longest 1, 2 or 5 × 10^k metres not over `max_m`."""
    k = 10.0 ** np.floor(np.log10(max_m))
    return float(next(m * k for m in (5, 2, 1) if m * k <= max_m))


class _ScaleBar(QWidget):
    """The Follow view's distance key, bottom-right: a bar of a round length and its label ("10
    m"), so the zoom says what a gap between two dots is on the ground. Painted, like the card."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.metres = 0.0
        self.length_px = 0.0
        self._font = theme.ui_font(theme.CAPTION)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    @property
    def label(self) -> str:
        return f"{self.metres:g} m"

    def set_scale(self, m_per_px: float) -> None:
        metres = _nice_length(SCALE_BAR_MAX_PX * m_per_px)
        length = metres / m_per_px
        if (metres, round(length, 2)) == (self.metres, round(self.length_px, 2)):
            return
        self.metres, self.length_px = metres, length
        pad = theme.SPACE_S
        text_w = QFontMetrics(self._font).horizontalAdvance(self.label)
        self.setFixedSize(int(np.ceil(max(length, text_w))) + 2 * pad, 34)
        self.reposition()
        self.update()

    def reposition(self) -> None:
        host = self.parentWidget()
        self.move(host.width() - self.width() - _CHROME_INSET,
                  host.height() - self.height() - _CHROME_INSET)

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        plate = QColor(C.surface)
        plate.setAlpha(232)
        p.setBrush(QBrush(plate))
        p.setPen(QPen(QColor(C.border), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                          theme.RADIUS_M, theme.RADIUS_M)
        pad = theme.SPACE_S
        p.setFont(self._font)
        p.setPen(QPen(QColor(C.text_dim)))
        p.drawText(QRectF(pad, 3, self.width() - 2 * pad, 16),
                   int(Qt.AlignVCenter | Qt.AlignLeft), self.label)
        y = self.height() - 9.5
        p.setPen(QPen(QColor(C.text), 1.5))
        p.drawLine(QPointF(pad, y), QPointF(pad + self.length_px, y))
        for x in (pad, pad + self.length_px):
            p.drawLine(QPointF(x, y - 4), QPointF(x, y + 1))
        p.end()


def _ramp(lo_hi: tuple[float, float], k: int, n: int) -> float:
    """Step `k` of `n` along the (oldest, newest) pair."""
    lo, hi = lo_hi
    return lo + (hi - lo) * (k / (n - 1) if n > 1 else 1.0)


class _TailItem(pg.GraphicsObject):
    """The tail's ONE graphics item: TAIL_STEPS halo polylines, then TAIL_STEPS stroke polylines,
    painted in a single `paint` call, aliased like every other line on the map (pyqtgraph's
    `antialias=False`, plots_view; antialiasing the tail alone measured +0.2 ms a tick). One item,
    not 2 × TAIL_STEPS PlotCurveItems: the scene repaints each item under the dirty rect through
    its own Python `paint`, and twelve curves measured +1.6 ms a tick on the demo (+0.13 ms of it
    the geometry); one item costs one call."""

    def __init__(self):
        super().__init__()
        self.halo_pens: list[QPen] = []
        self.stroke_pens: list[QPen] = []
        self.polys: list[QPolygonF] = []
        self._data_rect = QRectF()
        self._rect = QRectF()

    def ink_colours(self) -> list[tuple[QColor, str]]:
        """Every colour this item paints (the map's marker-contrast guard reads it)."""
        return ([(QColor(p.color()), "halo") for p in self.halo_pens]
                + [(QColor(p.color()), "stroke") for p in self.stroke_pens])

    def set_polys(self, polys: list[QPolygonF]):
        self.prepareGeometryChange()
        self.polys = polys
        self._data_rect = QRectF()
        for poly in polys:
            self._data_rect = self._data_rect.united(poly.boundingRect())
        self._pad()
        self.update()

    def _pad(self):
        """The data rect grown by the widest pen — a cosmetic pen's width is in device px, so the
        pad in data units follows the view's zoom (re-run by `viewTransformChanged`)."""
        if self._data_rect.isNull():
            self._rect = QRectF()
            return
        widest = max((p.widthF() for p in self.halo_pens), default=0.0) + 2.0
        vx, vy = self.pixelVectors()
        per_px = max(np.hypot(vx.x(), vx.y()), np.hypot(vy.x(), vy.y())) if vx is not None else 0.0
        pad = widest * per_px if np.isfinite(per_px) and per_px > 0 else 1.0
        self._rect = self._data_rect.adjusted(-pad, -pad, pad, pad)

    def viewTransformChanged(self):
        self.prepareGeometryChange()
        self._pad()

    def boundingRect(self) -> QRectF:
        return self._rect

    def paint(self, p, *_):
        if not self.polys:
            return
        p.setBrush(Qt.BrushStyle.NoBrush)
        for pens in (self.halo_pens, self.stroke_pens):
            for pen, poly in zip(pens, self.polys, strict=False):
                p.setPen(pen)
                p.drawPolyline(poly)


class _PlayheadTail:
    """The playhead marker's fading tail: where the kart has just been, the comet the exported
    clip's map inset draws (`timeline.trace_tail` — one function, two callers).

    BUILT ONCE, MOVED PER TICK: one `_TailItem`, created with the map, added with
    `ignoreBounds=True` (an item in the bounds re-runs pyqtgraph's auto-range bookkeeping on every
    move, +9 ms an update measured on the race layer) and only ever handed new polylines. The path
    is cut by ARC LENGTH, not by sample, so the fade's steps slide with the kart instead of hopping
    at the GPS rate; all halos paint before all strokes, so a step's halo never notches the stroke
    before it. `updates` counts placements for the per-tick tests."""

    def __init__(self, plot):
        self.plot = plot
        self.item = _TailItem()
        self.item.setZValue(TAIL_Z)
        self.items = [self.item]
        self.repen()
        plot.addItem(self.item, ignoreBounds=True)
        self.updates = 0
        self.xy: tuple[np.ndarray, np.ndarray] = (np.empty(0), np.empty(0))

    def repen(self):
        """(Re)build the pens at the current device-pixel ratio (see theme.line_width)."""
        halos, strokes = [], []
        for k in range(TAIL_STEPS):
            w = _ramp(TAIL_WIDTH, k, TAIL_STEPS)
            halo = QColor(C.canvas)
            halo.setAlpha(int(round(_ramp(TAIL_HALO_ALPHA, k, TAIL_STEPS))))
            ink = QColor(MARKER_COLOR)
            ink.setAlpha(int(round(_ramp(TAIL_ALPHA, k, TAIL_STEPS))))
            for out, colour, width in ((halos, halo, w + 2 * TAIL_HALO_PX), (strokes, ink, w)):
                pen = pg.mkPen(colour, width=theme.line_width(width))
                # Flat caps: two translucent steps meet end to end. Round ones overlap there, and
                # the doubled alpha read as a row of beads along the tail.
                pen.setCapStyle(Qt.PenCapStyle.FlatCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                out.append(pen)
        self.item.halo_pens, self.item.stroke_pens = halos, strokes
        self.item.set_polys(self.item.polys)

    def set_path(self, xs, ys):
        """Show the tail along (`xs`, `ys`), oldest first, ending on the marker; fewer than two
        distinct points (paused at a seek, an empty trace) clears it."""
        self.updates += 1
        xs = np.asarray(xs, float)
        ys = np.asarray(ys, float)
        self.xy = (xs, ys)
        d = np.concatenate(([0.0], np.cumsum(np.hypot(np.diff(xs), np.diff(ys))))) if len(xs) else xs
        if len(xs) < 2 or not d[-1] > 0:
            self.item.set_polys([])
            return
        cuts = np.linspace(0.0, d[-1], TAIL_STEPS + 1)
        cx, cy = np.interp(cuts, d, xs), np.interp(cuts, d, ys)
        # Each step: its two cut points and the samples strictly between them.
        lo = np.searchsorted(d, cuts, side="right")
        hi = np.searchsorted(d, cuts, side="left")
        polys = []
        for k in range(TAIL_STEPS):
            inner = slice(int(lo[k]), int(hi[k + 1]))
            polys.append(pg.functions.arrayToQPolygonF(
                np.concatenate(([cx[k]], xs[inner], [cx[k + 1]])),
                np.concatenate(([cy[k]], ys[inner], [cy[k + 1]]))))
        self.item.set_polys(polys)

    def steps(self) -> list[tuple[np.ndarray, np.ndarray]]:
        """The drawn steps as (xs, ys) arrays, oldest first — for tests."""
        return [(np.array([q.x() for q in poly]), np.array([q.y() for q in poly]))
                for poly in self.item.polys]

    def clear(self):
        self.set_path((), ())

    def set_visible(self, on: bool):
        self.item.setVisible(on)


class MapView(QWidget):
    # (start: Seg, sectors: list[Seg]) whenever a handle moves or sectors change.
    timing_lines_changed = Signal(object, object)
    # The corner ("C3") whose label or apex dot is under the pointer, None when it leaves — on
    # change only, from mouse moves; silent while a Lap Race runs. CentralView owns the state.
    cornerHovered = Signal(object)
    # A played corner pass's zoom (zoom_to_pass) ended because the driver took the view over: a
    # pan, a wheel zoom, Fit or a double-click. CentralView puts the charts back to the lap.
    passZoomTaken = Signal()

    def __init__(self, session: Session):
        super().__init__()
        # Before ANY pen is built: pyqtgraph pen widths are in device pixels, so point theme at
        # this widget's device-pixel ratio first (see theme.line_width and `event` below).
        theme.set_pen_scale(self.devicePixelRatioF())
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
        self._current_overlay = _LapOverlay(self.plot, CURRENT_COLOR, base_width=HERO_WIDTH,
                                            hero=True)

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
        # ...and a range change that moved the SCALE re-places the corner labels, which are laid
        # out in px (a resize re-fits, a wheel zoom rescales; a pan moves nothing relative to them).
        self.plot.getViewBox().sigRangeChanged.connect(self._on_range_changed)
        # The fit's label room is in px, so it is re-derived when the VIEWBOX settles on its size —
        # which lags the widget's own resizeEvent — while our fit still stands.
        self.plot.getViewBox().sigResized.connect(lambda *_: self._refit_on_resize())

        self.marker = pg.TargetItem(
            (session.tx[0] if len(session.tx) else 0, session.ty[0] if len(session.ty) else 0),
            size=15, movable=True, pen=pg.mkPen(MARKER_COLOR, width=theme.line_width(2)),
            brush=pg.mkBrush(_MARKER_RGB.red(), _MARKER_RGB.green(), _MARKER_RGB.blue(), 110),
        )
        self.plot.addItem(self.marker)
        self.marker.setZValue(10)  # canonical z-order: lap overlays/rainbow ≤5, corner/brake 5-7, ghost 9, marker 10
        # The marker's fading tail (z 8.5), and the continuous stretch of playhead it may span: it
        # reaches back to the last JUMP (a seek, a lap pick, a backward move), never past it.
        self._tail = _PlayheadTail(self.plot)
        self._tail_from: float | None = None
        self._tail_last: float | None = None

        # Self-contained overlays; the app pushes corner/brake markers via set_corners /
        # set_brake_markers (both laps for brakes in compare mode).
        self._corner_markers = _CornerMarkers(self.plot)
        self._corner_hovered: str | None = None   # what cornerHovered last said
        self._brake_markers = _BrakeMarkers(self.plot)
        # Wired AFTER the overlays exist: _marker_dragged also keeps the corner labels clear of the
        # marker, so _corner_markers must be constructed before the first emission can reach it.
        self.marker.sigPositionChanged.connect(self._marker_dragged)

        # Compare ghost (lap B's kart position); created lazily on first compare tick, removed on exit.
        # ghost_updates counts placements for the per-tick perf-invariant tests.
        self._ghost: pg.TargetItem | None = None
        self.ghost_updates = 0
        # The Lap Race layer while a race runs (start_race / end_race), and what it hid.
        self._race: _RaceLayer | None = None
        self._race_restore = (True, False)
        self._race_card: QWidget | None = None   # the race card floated on this canvas, if any
        # Follow (set_race_follow): the camera while on, the view it put aside, the last race state.
        self._follow: _FollowCam | None = None
        self._follow_restore = None
        self._race_state = None
        self._scale_bar = _ScaleBar(self.widget)
        # A played corner pass (zoom_to_pass): the ground box it frames, and the view it put aside.
        self._pass_box: tuple[float, float, float, float] | None = None
        self._pass_restore = None

        # E2: provisional start cue; declared before _rebuild (which may refresh it). None = track
        # known, no cue. See refresh_provisional_cue.
        self._provisional_line: pg.PlotDataItem | None = None
        self._provisional_label: pg.TextItem | None = None
        self._start: _TimingLine | None = None
        self._sectors: list[_TimingLine] = []
        # The (reference label, ring-missing) state the "no reference line" notice was last posted
        # for — declared before _rebuild/_refresh_best, which consult it. See _note_missing_reference.
        self._ref_note_key: tuple | None = None
        # The start/finish mark, and the timing-line handles' reveal state (see FINISH_LIGHT):
        # the pointer is near a line / "Add sector" just ran (cleared by _pin_timer).
        self._finish_mark = _FinishMark(self.plot)
        self._timing_hover = False
        self._timing_pinned = False
        self._pin_timer = QTimer(self)
        self._pin_timer.setSingleShot(True)
        self._pin_timer.setInterval(NOTICE_MS)
        self._pin_timer.timeout.connect(self._unpin_timing_chrome)
        self._rebuild(session.start_line, session.sector_lines)
        self._refresh_best()
        # Pointer moves over the canvas (never the playback tick) reveal the handles near a line.
        self.widget.scene().sigMouseMoved.connect(self._on_mouse_moved)

        # Sector controls are exposed (not placed here) so app.py mounts them in the map header.
        # GLYPHED, because the map toolbar was the one place in the app where a labelled control on
        # panel chrome went bare: "Snap to track" sits beside them with ph.magnet, and every other
        # labelled control mounted on chrome carries a Phosphor glyph too (Compare, Brake/Throttle,
        # Ideal lap, the map's own Fit, coaching's Jump). Bare words are the DIALOG vocabulary
        # (Open / Close / Cancel / Clear library …), which is internally consistent and stays that
        # way. Cost measured: the map toolbar's fixed width goes 504 -> 544 px, which is also the
        # map panel's minimum width — still under the 557 px the charts panel already sets for that
        # column, so the window's own minimum stays 973x528.
        self.add_sector_btn = QPushButton(icon("ph.plus"), "Add sector")
        self.reset_sectors_btn = QPushButton(icon("ph.eraser"), "Reset sectors")
        for btn in (self.add_sector_btn, self.reset_sectors_btn):
            btn.setIconSize(QSize(theme.ICON_PX, theme.ICON_PX))
        self.add_sector_btn.clicked.connect(self._add_sector)
        self.reset_sectors_btn.clicked.connect(self._reset_sectors)
        # Opt-in snap-to-track toggle (default off = free placement). When on, a released handle
        # snaps to the nearest trace point. See _snap_to_trace.
        self.snap_btn = ToggleButton(
            "Snap to track", glyph="ph.magnet",
            tooltip="Snap to track: when on, a released timing-line handle jumps to the nearest "
                    "point on the track trace. Off (default) = handles stay exactly where you "
                    "drop them.")

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
            "Δ to best (red = losing, green = gaining), Δ rate (where you are losing it RIGHT "
            # Grip's gloss ends in the sentence the Corners and Stats grip columns carry
            # (theme.GRIP_COMPARE_NOTE): this line is the same reading per sample, and "green =
            # grip left unused" beside it read as a headroom claim between corners. Tooltip only —
            # the toolbar's width is its items' text, and none of them changed.
            "HERE), Grip (ESTIMATED: red = on the session's grip limit, green = well under it. "
            f"{theme.GRIP_COMPARE_NOTE}), or Elevation (red = the lowest point of the "
            "lap, green = the highest). Δ to best is CUMULATIVE — it shows how far behind you "
            "already were, so a corner you are gaining in still paints red while you carry a "
            "deficit into it. Δ rate is its slope: seconds lost per second of driving, smoothed "
            f"over {map_render.RATE_WINDOW_S:.1f} s of travel, on a scale centred on zero — the "
            "middle colour means matching the baseline, red is losing time here, green is taking "
            "it back. Pedal (ESTIMATED) is the charts' Brake/Throttle band painted on the line — "
            "the same values, not a second estimate: red where you are braking, green where you "
            "are accelerating, the middle colour for a lift or a steady speed. Pacer has no pedal "
            "sensors; it is the GPS speed's rate of change. "
            "Elevation is RELATIVE within the lap: GPS altitude drifts "
            "by several metres between laps of the same track, so only the shape is meaningful — "
            "the legend reads 'lowest' → the rise above it, never an altitude above sea level, "
            "and its noise is not measured. "
            "Off leaves the plain racing line. The faint best-lap reference is unchanged.")
        # Show the default channel in the combo BEFORE wiring the change signal, so the initial
        # selection reads "Line: Speed" without re-entering _on_rainbow_combo.
        self._sync_rainbow_combo(self._rainbow_mode)
        self.rainbow_combo.currentIndexChanged.connect(self._on_rainbow_combo)
        self._legend = _RainbowLegend()
        self._legend.setVisible(False)

        # C3 map key: floats over the plot's bottom-left (parented to the PlotWidget, re-pinned by
        # _reposition_key, raised so it stays clickable). Its collapse is a PREFERENCE, read here
        # and written back from the plate's own click: it used to be per-MapView state, so a user
        # who put a 196x106 plate away got it back on the next recording and on the next launch.
        # Read/written in the view rather than pushed down by app.py — the same call the Library
        # dialog makes for its own remembered size, and for the same reason: nothing outside this
        # widget has an opinion about it (studio/prefs.py).
        self._map_key = _MapLegend(on_resize=self._reposition_key,
                                   on_toggle=prefs.set_map_key_collapsed,
                                   collapsed=prefs.map_key_collapsed())
        self._map_key.setParent(self.widget)
        self._map_key.raise_()
        self._map_key.show()

        # Zero-valid-lap empty state: a centred placeholder floated over the plot (the largest
        # quadrant), so a load with no complete laps reads as an explained state — with the recovery
        # action — rather than a black void. Parented to the PlotWidget, re-centred by
        # _reposition_empty_state, shown/hidden by _refresh_empty_state (called at build + reseg).
        # The wording is data_quality's, not this file's: "No complete laps FOUND in this
        # recording." was the fourth phrasing of one fact in one frame, and the half of the next
        # action it stated was the half that contradicted the lap panel's (QA D2-01).
        self._empty_state = EmptyState(data_quality.NO_LAPS_HEADLINE,
                                       data_quality.no_laps_body(), parent=self.widget)
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
        self.fit_btn.clicked.connect(self._fit_by_user)
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
        # The card was the app's only whole-widget stylesheet built in a view file (its own 6 px
        # radius and 6px/10px padding, neither on any scale); it is the theme's QLabel#MapNotice now.
        self._notice.setObjectName("MapNotice")
        self._notice.hide()
        self._notice_timer = QTimer(self)
        self._notice_timer.setSingleShot(True)
        self._notice_timer.setInterval(NOTICE_MS)
        self._notice_timer.timeout.connect(self._notice.hide)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.widget, 1)
        lay.addWidget(self._legend)

    def event(self, ev):
        """Re-pen when the window moves to a screen with a different device-pixel ratio.

        The pens here are in DEVICE pixels (see theme.line_width), so the correct width depends on
        the screen the window is on right now: drag the window from the Retina panel to an external
        monitor and the rainbow ribbon and lap traces would otherwise keep the old screen's weight
        (double on the way out, half on the way back). The layers that are rebuilt on every lap
        change (brake/corner glyphs, timing-line handles) heal themselves on the next redraw; the
        ones that are built once and memoized are re-penned in place here."""
        if ev.type() == QEvent.Type.DevicePixelRatioChange:
            if theme.set_pen_scale(self.devicePixelRatioF()):
                self._trace_overlay.repen()
                self._best_overlay.repen()
                self._current_overlay.repen()
                self.marker.setPen(pg.mkPen(MARKER_COLOR, width=theme.line_width(2)))
                self._tail.repen()
                self.refresh_palette()      # re-pens the rainbow buckets + legend strip
        return super().event(ev)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._reposition_key()
        self._reposition_empty_state()
        self._reposition_fit_btn()
        self._reposition_notice()
        self._clamp_provisional_label()  # a reshaped panel can push the cue over an edge (MAP-05)
        # A panel that changed shape re-frames the track (the map fills whatever room it is given),
        # but only while the view is still OUR fit — never on top of a view the user moved.
        self._refit_on_resize()

    def _refit_on_resize(self):
        """Re-frame after a resize: under Follow the same ground width round the same point, else
        our fit while it still stands."""
        if getattr(self, "_follow", None) is not None:
            self._follow_apply()
        elif getattr(self, "_pass_box", None) is not None:
            self._pass_apply()
        elif getattr(self, "_view_fitted", False):
            self._fit_view()

    def eventFilter(self, obj, event):
        if (event.type() == QEvent.MouseButtonDblClick and obj is self.widget.viewport()
                and event.button() == Qt.LeftButton):
            self._fit_by_user()     # a no-op under Follow, which owns the view
            return True
        # Leaving the canvas sends no move, so the hover reveal is cleared here (pins and the
        # provisional / no-laps rules are untouched: _sync_timing_chrome still honours them).
        if (event.type() == QEvent.Leave and obj is self.widget.viewport()
                and getattr(self, "_timing_hover", False)):
            self._timing_hover = False
            self._sync_timing_chrome()
        # ...and so is the corner hover. The VIEWPORT's Leave, not MapView's: the map key, the
        # notice and the legend are children inside MapView, so a pointer moving onto them leaves
        # the canvas without ever leaving the view (it also fires when the view itself is left).
        if event.type() == QEvent.Leave and obj is self.widget.viewport():
            self._set_corner_hovered(None)
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
        return _union_bounds((self._circuit_bounds() or trace, self._best_overlay.bounds(),
                              self._current_overlay.bounds()))

    def _circuit_bounds(self):
        """The valid laps' extent, standing in for the trace's when the trace is (nearly) all
        circuit — see FIT_CIRCUIT_SHARE. None otherwise, or on a session without laps. Memoized per
        valid-lap set and start line: `_fit_view` runs on every resize."""
        s = self.session
        ids = tuple(s.valid_lap_ids()) if hasattr(s, "valid_lap_ids") else ()
        lap_xy = getattr(s, "lap_trace_xy", None)
        if not ids or lap_xy is None:
            return None
        start = getattr(s, "start_line", None)
        key = (ids, tuple(getattr(start, k, None) for k in ("x1", "y1", "x2", "y2")))
        if getattr(self, "_circuit_memo", (None,))[0] != key:
            try:
                box = _union_bounds(_xy_bounds(*lap_xy(lid)) for lid in ids)
            except (AttributeError, KeyError, IndexError, TypeError, ValueError):
                box = None          # a partial (bare/test) session: frame the trace, as before
            tx, ty = np.asarray(s.tx, float), np.asarray(s.ty, float)
            fin = np.isfinite(tx) & np.isfinite(ty)
            if box is not None and fin.any():
                x_lo, x_hi, y_lo, y_hi = box
                inside = fin & (tx >= x_lo) & (tx <= x_hi) & (ty >= y_lo) & (ty <= y_hi)
                if inside.sum() < FIT_CIRCUIT_SHARE * fin.sum():
                    box = None
            self._circuit_memo = (key, box)
        return self._circuit_memo[1]

    def _fit_view(self, force: bool = False):
        """Frame the map on its content and freeze it there (autorange off, so the video marker
        moving never drags the view around). The one place a range is set — called at build, from
        the Fit button / canvas double-click, and after a redraw or resize that left our own fit
        standing. Under Follow it does nothing: Follow owns the view, and puts back the fit (or the
        user's own view) when it ends."""
        if getattr(self, "_follow", None) is not None and not force:
            return
        box = self._content_bbox()
        if box is None:
            return
        x_lo, x_hi, y_lo, y_hi = box
        dx, dy = max(x_hi - x_lo, 1.0), max(y_hi - y_lo, 1.0)
        # 2% pad so the aspect-locked track fills the panel without handles flush to the edge, plus
        # FIT_LABEL_ROOM_PX on every side at the scale the aspect lock will settle on.
        vb = self.plot.getViewBox()
        size, room = vb.boundingRect(), FIT_LABEL_ROOM_PX
        extra = 0.0
        if size.width() > 4 * room and size.height() > 4 * room:
            scale = min((size.width() - 2 * room) / (dx * 1.04),
                        (size.height() - 2 * room) / (dy * 1.04))
            extra = room / scale
        px, py = dx * 0.02 + extra, dy * 0.02 + extra
        vb.setRange(xRange=(x_lo - px, x_hi + px), yRange=(y_lo - py, y_hi + py), padding=0)
        vb.disableAutoRange()
        self._view_fitted = True
        self._sync_fit_btn()

    def _on_manual_range(self, *_):
        """The user panned or zoomed: stop re-fitting behind their back, and surface the way back.
        Over a pass's zoom the view is theirs from here: it stays, and the pass zoom ends."""
        self._view_fitted = False
        self._sync_fit_btn()
        if self._drop_pass_zoom():
            self.passZoomTaken.emit()

    def _fit_by_user(self):
        """Fit / a double-click: the whole track, ending a pass's zoom if one is on."""
        taken = self._drop_pass_zoom()
        self._fit_view()
        if taken:
            self.passZoomTaken.emit()

    # ------------------------------------------------------------ a played corner pass
    # A pass played from Corners ▸ Quickest passes frames the map on that pass: the played lap's
    # line from the clip's lead-in to its end (CentralView passes the clip's telemetry window), with
    # a margin, at the aspect the lock gives. It borrows Follow's machinery — the view put aside and
    # put back exactly, the scale bar while zoomed — and draws nothing new: the lines on the canvas
    # are the ones it already drew. Never under a Lap race (Follow or the race's own fit owns it).
    PASS_MARGIN = 0.2         # of the pass's own larger side, each way: room for the C# label
    PASS_MIN_SIDE_M = 60.0    # a short pass still shows its surroundings (the demo's span 60-130 m)

    def pass_zoomed(self) -> bool:
        return self._pass_box is not None

    def zoom_to_pass(self, t0: float, t1: float) -> bool:
        """Frame the trace between telemetry times `t0` and `t1`. A second pass while zoomed moves
        the zoom and keeps the view the first one put aside. False when nothing could be framed."""
        if self._race is not None or self._follow is not None:
            return False
        tt = np.asarray(self.session.tt, float)
        lo, hi = np.searchsorted(tt, [t0, t1])
        xs = np.asarray(self.session.tx, float)[lo:hi + 1]
        ys = np.asarray(self.session.ty, float)[lo:hi + 1]
        fin = np.isfinite(xs) & np.isfinite(ys)
        if fin.sum() < 2:
            return False
        xs, ys = xs[fin], ys[fin]
        cx, cy = (xs.min() + xs.max()) / 2, (ys.min() + ys.max()) / 2
        side = max(xs.max() - xs.min(), ys.max() - ys.min(), self.PASS_MIN_SIDE_M)
        half = side * (0.5 + self.PASS_MARGIN)
        if self._pass_box is None:
            self._pass_restore = (self.plot.getViewBox().viewRange(), self._view_fitted)
        self._pass_box = (cx - half, cx + half, cy - half, cy + half)
        self._view_fitted = False
        self._fold_key(True)           # the key's plate off the neighbours' labels
        self._sync_fit_btn()           # Fit is the map's way back to the whole track
        self._pass_apply()
        return True

    def _pass_apply(self):
        box = self._pass_box
        if box is None:
            return
        vb = self.plot.getViewBox()
        r = vb.rect()      # the rect the aspect lock divides by, as _follow_apply
        w, h = r.width(), r.height()
        if w < 8 or h < 8:
            vb.setRange(xRange=box[:2], yRange=box[2:], padding=0)
            return
        # Centred in the FREE area, as Follow centres beside the race card: right of the map key
        # and left of the Fit button, so neither covers the pass or its corner labels. The map is
        # wide and short, so the columns they take cost the zoom nothing (its height decides it).
        x0, x1 = self._free_x_span(w)
        mpp = max((box[1] - box[0]) / (x1 - x0), (box[3] - box[2]) / h)
        cx, cy = (box[0] + box[1]) / 2, (box[2] + box[3]) / 2
        xl = cx - (x0 + x1) / 2 * mpp
        yl = cy - h / 2 * mpp
        vb.setRange(xRange=(xl, xl + w * mpp), yRange=(yl, yl + h * mpp), padding=0)
        self._scale_bar.set_scale(mpp)
        self._scale_bar.reposition()
        self._scale_bar.show()
        self._scale_bar.raise_()

    def _free_x_span(self, view_w: float) -> tuple[float, float]:
        """The pass zoom's free columns (px): right of the map key, left of the Fit button — each
        only while shown, and never narrower than half the map (a key wider than that is ignored)."""
        x0, x1 = 0.0, float(view_w)
        key = getattr(self, "_map_key", None)
        if key is not None and key.isVisibleTo(self.widget):
            x0 = float(key.x() + key.width() + _CHROME_INSET)
        btn = getattr(self, "fit_btn", None)
        if btn is not None and btn.isVisibleTo(self.widget):
            x1 = float(btn.x() - _CHROME_INSET)
        if x1 - x0 < 0.5 * view_w:
            return 0.0, float(view_w)
        return x0, x1

    def _drop_pass_zoom(self) -> bool:
        """Forget the pass zoom WITHOUT moving the view (the caller decides where it goes)."""
        if self._pass_box is None:
            return False
        self._pass_box = None
        self._pass_restore = None
        self._scale_bar.hide()
        self._fold_key(False)
        return True

    def _fold_key(self, on: bool) -> None:
        key = getattr(self, "_map_key", None)
        if key is not None and key.set_folded(on):
            self._reposition_key()

    def unzoom_pass(self) -> bool:
        """Put back the view the pass zoom put aside: our fit (re-run at today's size) or the
        driver's own range. False when no pass zoom was on."""
        restore = self._pass_restore
        if not self._drop_pass_zoom():
            return False
        (xr, yr), fitted = restore
        if fitted:
            self._fit_view()
        else:
            self.plot.getViewBox().setRange(xRange=xr, yRange=yr, padding=0)
            self._view_fitted = False
            self._sync_fit_btn()
        return True

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
        btn.adjustSize()
        btn.move(self.widget.width() - btn.width() - _CHROME_INSET, _CHROME_INSET)

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
        host = self.widget
        w = max(min(host.width() - 2 * _CHROME_INSET, NOTICE_MAX_W_PX), 120)
        notice.setFixedWidth(w)
        # A word-wrapped QLabel's sizeHint is its ONE-LINE hint, so adjustSize() alone sizes the
        # plate for one line and paints the rest outside it — measured on the reference notice
        # below: 5 wrapped lines in a 70 px plate, top and bottom lines sliced through. Ask the
        # label what the text needs at the width just pinned; sizeHint stays the floor so the
        # existing one-line notices keep their exact geometry.
        notice.setFixedHeight(max(notice.heightForWidth(w), notice.sizeHint().height()))
        notice.move(_CHROME_INSET, _CHROME_INSET)

    def _reposition_key(self):
        """Keep the floating map key pinned to the plot's bottom-left, just inside the edge — and
        never let a short canvas push it off the TOP.

        The plate is a fixed height (106 px expanded) over a canvas whose height is whatever the
        splitter leaves. Pinning it by `host.height() - key.height() - m` alone put y at −42 px on
        a 72 px canvas — one ordinary drag of the map/charts splitter, and also the window's own
        973x528 minimum — and Qt clips a child at its parent's edge, so 42 px of a 106 px plate
        went off the TOP: the "▾ Map key" title row and the "Video position" row, i.e. exactly the
        caret and title that are the only sign the plate collapses at all.

        Two lines fix it, in this order. Tell the plate how much room it has, so it falls back to
        its 34 px title-only form rather than being cut (and restores the user's own choice when
        the room returns); then clamp the corner. The clamp is what makes the failure GRACEFUL
        rather than absent: below ~50 px of canvas even the title bar cannot fit, and anchoring to
        the top-left means the title is the LAST thing lost instead of the first. (Measured: the
        map panel's own minimumSizeHint floors the canvas at 72 px, and the only way past that is
        maximizing the charts panel, which takes the whole map panel to 0 px — nothing is painted
        there at all.)"""
        key = getattr(self, "_map_key", None)
        if key is None:
            return
        host = self.widget
        key.set_available_height(host.height() - 2 * _CHROME_INSET)
        key.move(_CHROME_INSET,
                 max(_CHROME_INSET, host.height() - key.height() - _CHROME_INSET))

    def document_caption(self, lap_text: str) -> str:
        """What this map draws, for the line under a still of it (the HTML report's map): the lap
        whose line is coloured (`lap_text`, named by the caller, which owns lap numbering), the
        channel in the line combo's own word, and the colour bar's two ends as the legend under
        the map prints them, in its unit. Read from the drawn state, so it cannot name a channel
        or a range the picture does not show."""
        label = _RAINBOW_COMBO_LABELS.get(self._rainbow_mode, "").removeprefix("Line: ")
        lo, hi = getattr(self._legend, "_full", ("", ""))
        if self._rainbow_mode == "off" or not label:
            line = f"{lap_text}, its racing line"
        elif lo and hi and self._legend.isVisibleTo(self):
            line = (f"{lap_text}, its line coloured by {label.lower()}: the bar under the map runs "
                    f"from {lo} to {hi}")
        else:
            line = f"{lap_text}, its line coloured by {label.lower()}"
        return (f"{line}. Under it in faint grey, the whole recording's driven trace — every "
                f"lap, out- and in-laps too; the whole track framed, corners labelled.")

    @contextmanager
    def grab_clean(self):
        """Temporarily hide the map's pure-INTERACTION chrome so a caller can ``grab()`` the plot as
        a clean image for a social share (the shareable lap card). Dev/interaction affordances have
        no place on a share image; the SPEED rainbow colouring, corner labels and track line stay.
        Restores each item's prior visibility on exit (even on error), so the live map is untouched.

        Hidden for the grab (H2 — otherwise the app's editing crosshairs burn into the brag image),
        and, while a Lap Race runs, its card and every race item (a document shows the plain map):
          * the "Map key" legend + the zero-lap empty-state placeholder + the Fit button + the
            transient action notice (Qt widgets — the Fit button and the notice are pure
            interaction chrome, and both are usually hidden anyway);
          * the video-position ``marker`` (the MARKER_COLOR ring on the track);
          * every timing line's segment + drag handles — the start line and each sector line — plus
            the compare ghost, all via each ``_TimingLine.chrome_items()`` so a future line type is
            hidden automatically.

          * the provisional start-line cue (its dashed line and "drag to set start/finish" caption):
            the card refuses unverified timing upstream, but the exported REPORT does not — it
            grabbed the cue and printed a drag instruction in a document (QA w5). The report says
            "provisional" itself, in its Timing row and stamps."""
        # Qt widget chrome: save the EXPLICIT hide flag (isHidden), not effective isVisible — a child
        # reads not-visible whenever its top-level window isn't shown yet, so restoring from isVisible
        # would wrongly leave the key hidden on an off-screen/grab-only map. isHidden is True only when
        # hide() was actually called.
        widgets = [w for w in (getattr(self, "_map_key", None), getattr(self, "_empty_state", None),
                               getattr(self, "fit_btn", None), getattr(self, "_notice", None),
                               getattr(self, "_race_card", None), getattr(self, "_scale_bar", None))
                   if w is not None]
        # …and so is Follow's zoom: the grab shows the view Follow put aside, then Follow resumes.
        vb = self.plot.getViewBox()
        follow = getattr(self, "_follow", None) is not None
        follow_range = vb.viewRange() if follow else None
        # A Lap Race is a live view, never a document's map (review of #553: a report exported
        # mid-race carried the card, the veil and the dots): its items go with the chrome.
        race = getattr(self, "_race", None)
        widget_prev = [(w, w.isHidden()) for w in widgets]
        # pyqtgraph plot items (marker, timing-line segments/handles, compare ghost). These are not
        # top-level-gated Qt widget children, so isVisible() is the honest current flag for them.
        items = []
        if getattr(self, "marker", None) is not None:
            items.append(self.marker)
        if getattr(self, "_tail", None) is not None:
            items.extend(self._tail.items)      # the marker's tail goes with the marker
        timing_lines = [getattr(self, "_start", None), *(getattr(self, "_sectors", None) or [])]
        for tl in timing_lines:
            if tl is not None:
                items.extend(tl.chrome_items())
        if getattr(self, "_ghost", None) is not None:
            items.append(self._ghost)
        # The provisional cue is an INSTRUCTION ("drag to set start/finish") over a start line this
        # grab just hid. A document states the provisional timing in its own words (the report's
        # Timing row and stamps); a picture of a drag it cannot offer is not that.
        items.extend(it for it in (getattr(self, "_provisional_line", None),
                                   getattr(self, "_provisional_label", None)) if it is not None)
        item_prev = [(it, it.isVisible()) for it in items]
        try:
            for w in widgets:
                w.hide()
            for it in items:
                it.setVisible(False)
            if race is not None:
                race.set_visible(False)
            if follow:
                (xr, yr), fitted, _mouse = self._follow_restore
                if fitted:              # our fit, at the map's size NOW (it may have been resized)
                    self._fit_view(force=True)
                else:
                    vb.setRange(xRange=xr, yRange=yr, padding=0)
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
            if race is not None and race is getattr(self, "_race", None):
                race.set_visible(True)
            if follow_range is not None and getattr(self, "_follow", None) is not None:
                vb.setRange(xRange=follow_range[0], yRange=follow_range[1], padding=0)
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
        # The card is the app's one prose MEASURE plus the empty state's own SPACE_XL inset on both
        # sides — not a hand-picked 420. Shipped, this pane set the NARROWEST of the sixteen states
        # (372 px ≈ 57 characters) while the charts pane beside it set 869 px ≈ 134 (QA D2-12);
        # deriving both from theme.EMPTY_MEASURE_PX is what makes them the same object on screen.
        card = theme.EMPTY_MEASURE_PX + 2 * theme.SPACE_XL
        w = min(host.width() - 2 * theme.SPACE_XL, card)
        es.setFixedWidth(max(w, theme.SPACE_3XL * 2))
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
        self._sync_timing_chrome()        # no laps = the empty state asks for a drag: show handles

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
        self._start = _TimingLine(self.plot, start, START_COLOR, self._emit, self._snap_to_trace,
                                  segment_at_rest=False)
        self._sectors = [_TimingLine(self.plot, s, SECTOR_COLOR, self._emit, self._snap_to_trace)
                         for s in sectors]
        self._finish_mark.update(self.session.tx, self.session.ty, start)
        # Re-pin the provisional cue (or remove it if the track is known); it also settles whether
        # the fresh handles rest hidden (_sync_timing_chrome).
        self.refresh_provisional_cue()
        self._relayout_labels()           # a moved start line moves the cluster labels clear

    # ------------------------------------------------- timing-line chrome: hidden at rest
    def _timing_chrome_wanted(self) -> bool:
        """Show the handles? While the pointer is near a line or "Add sector" just ran — and always
        while the map is asking the user to drag (provisional timing, or no complete laps). Never
        under a Lap race: its veil dims the map, corner hover is off, and a drag would re-segment
        the laps the race is running (QA REG-4)."""
        if self._race is not None:
            return False
        if self._timing_hover or self._timing_pinned or self._provisional_line is not None:
            return True
        valid = getattr(self.session, "valid_lap_ids", None)
        return valid is not None and not valid()

    def _sync_timing_chrome(self):
        on = self._timing_chrome_wanted()
        for tl in [self._start, *self._sectors]:
            if tl is not None and tl.handles_visible != on:
                tl.set_handles_visible(on)

    def _pin_timing_chrome(self):
        self._timing_pinned = True
        self._pin_timer.start()
        self._sync_timing_chrome()

    def _unpin_timing_chrome(self):
        self._timing_pinned = False
        self._sync_timing_chrome()

    def _near_timing_line(self, scene_pos) -> bool:
        """Is the scene point within TIMING_REVEAL_PX of any timing line's handle or segment?"""
        vb = self.plot.getViewBox()
        px, py = scene_pos.x(), scene_pos.y()
        for tl in [self._start, *self._sectors]:
            if tl is None:
                continue
            s = tl.seg()
            a = vb.mapViewToScene(QPointF(s.x1, s.y1))
            b = vb.mapViewToScene(QPointF(s.x2, s.y2))
            ax, ay, bx, by = a.x(), a.y(), b.x(), b.y()
            L2 = (bx - ax) ** 2 + (by - ay) ** 2
            f = 0.0 if L2 <= 0 else min(max(((px - ax) * (bx - ax) + (py - ay) * (by - ay)) / L2,
                                            0.0), 1.0)
            if np.hypot(px - (ax + f * (bx - ax)), py - (ay + f * (by - ay))) <= TIMING_REVEAL_PX:
                return True
        return False

    def _on_mouse_moved(self, scene_pos):
        near = self._near_timing_line(scene_pos)
        if near != self._timing_hover:
            self._timing_hover = near
            self._sync_timing_chrome()
        self._set_corner_hovered(None if self._race is not None
                                 else self._corner_markers.hit(scene_pos))

    def _set_corner_hovered(self, label: str | None):
        if label != self._corner_hovered:
            self._corner_hovered = label
            self.cornerHovered.emit(label)

    def forget_corner_hover(self):
        """Forget what cornerHovered last said, without emitting (CentralView's state was just
        reset by a rebuild): the next move over a corner names it again."""
        self._corner_hovered = None

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
                       and self._start is not None
                       # a recording with no GPS trace has a placeholder line, not one to set
                       and not data_quality.no_start_line(self.session))
        if not provisional:
            for it in (self._provisional_line, self._provisional_label):
                if it is not None:
                    self.plot.removeItem(it)
            self._provisional_line = self._provisional_label = None
            self._sync_timing_chrome()
            return
        seg = self._start.seg()
        mx, my = (seg.x1 + seg.x2) / 2.0, (seg.y1 + seg.y2) / 2.0
        if self._provisional_line is None:
            # Dashed accent line over the start segment (z above the handles, below the marker).
            pen = pg.mkPen(C.accent, width=theme.line_width(2))
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
        self._sync_timing_chrome()

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
        self._finish_mark.update(self.session.tx, self.session.ty, start)
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
        self._pin_timing_chrome()         # the new line is there to be dragged: show its handles
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
        self._reset_tail()      # a hand-dragged marker is a jump: its old tail would trail nothing
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
        self._reset_tail()

    def set_playhead_time(self, t: float):
        """Place the marker where the kart is at TELEMETRY time `t` — the playback tick and the
        scrub both come here (shared verb with PlotsView.set_playhead_time).

        INTERPOLATED between the two samples that bracket `t`, and held across a dropout
        (`timeline.trace_point_at`, the rule the burned-in export's map dot uses). It used to land
        on the NEAREST sample, so on a 10 Hz trace the dot held still for three 30 Hz ticks (six
        59.94 fps frames) and then jumped: on MK_18_09_26's best lap (67.5 s) it moved 674 times in
        2,025 ticks, 1.5 m a step (2.4 m at most); now 2,024 times, 0.5 m (0.8 m) (HEALTH-2,
        2026-09-25)."""
        p = trace_point_at(self.session.tt, self.session.tx, self.session.ty, t)
        if p is None:
            return
        self._suppress_marker = True
        self.marker.setPos(pg.Point(p[0], p[1]))
        self._suppress_marker = False
        self._place_tail(t)

    def _place_tail(self, t: float):
        """Lay the marker's tail (`_PlayheadTail`) to end on it at TELEMETRY time `t`.

        THE EXPORT'S TAIL, ON THE STRETCH THE PLAYHEAD DROVE. In playback or a scrub it is
        `timeline.trace_tail(t)` exactly — the last TRACE_TAIL_S of the trace, reaching back into the
        lap before across a lap's start and cut at a dropout, as the clip draws it. After a JUMP — a
        seek, a lap picked in the table, a backward move, a step forward longer than the tail — it
        starts empty and grows from where the playhead landed: picking a lap parks the marker on its
        start line, and a tail there would draw a run-up the user never watched."""
        last = self._tail_last
        if self._tail_from is None or last is None or t < last or t - last > TRACE_TAIL_S:
            self._tail_from = t
        self._tail_last = t
        span = min(TRACE_TAIL_S, t - self._tail_from)
        if span <= 0.0:
            self._tail.clear()
            return
        self._tail.set_path(*trace_tail(self.session.tt, self.session.tx, self.session.ty, t, span))

    def _reset_tail(self):
        self._tail_from = self._tail_last = None
        self._tail.clear()

    def tail_items(self) -> list:
        """The playhead tail's plot items — tests count and inspect them."""
        return list(self._tail.items)

    # --------------------------------------------------------------- compare ghost (F4)
    def set_ghost_index(self, i: int | None):
        """Place the compare ghost at trace index `i` (None = no-op) — lap B's kart at equal
        elapsed-into-lap."""
        if i is None:
            return
        self.set_ghost_pos(float(self.session.tx[i]), float(self.session.ty[i]))

    def set_ghost_pos(self, x: float, y: float):
        """Place the ghost at explicit local (x,y) — used by F7 cross-recording compare where lap B
        isn't a primary-trace index. Lazily creates the one ghost item."""
        if self._ghost is None:
            # A ring on the dark label plate (GHOST_PLATE), never a hue fill, and not movable — the
            # marker stays the only filled, drag-to-seek surface.
            self._ghost = pg.TargetItem((0.0, 0.0), size=11, movable=False,
                                        pen=pg.mkPen(GHOST_COLOR, width=theme.line_width(2)),
                                        brush=pg.mkBrush(GHOST_PLATE))
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

    # --------------------------------------------------------------- Lap Race
    def start_race(self, tracks, colours: dict[int, int], selected: int | None):
        """Put the race layer on (`_RaceLayer`): hide the video-position marker (race time is not
        media time) and the map key (whose rows describe that marker and the glyphs the veil dims),
        remembering both so `end_race` restores them exactly."""
        if self._race is not None:
            self.end_race()
        # The key's own hidden flag, not isVisible(): that also reads False for an unshown parent,
        # and restoring it would then hide the key for good.
        self._race_restore = (self.marker.isVisible(), self._map_key.isHidden())
        self.marker.setVisible(False)
        self._tail.set_visible(False)       # the tail is the marker's: race time is not media time
        self._map_key.setVisible(False)
        self._corner_markers.set_hover(None)      # corner hover is inert under the race
        self._set_corner_hovered(None)            # …and the chart label it lit goes out (REG-3)
        self._race = _RaceLayer(self.plot, tracks, colours, selected)
        self._sync_timing_chrome()                # the drag handles rest hidden under it

    def update_race(self, state):
        """Move the field to `state` — the race tick's one map call (and, under Follow, one range)."""
        if self._race is not None:
            self._race.update(state)
            self._race_state = state
            if self._follow is not None:
                self._follow_apply()

    def set_race_selected(self, lap_id: int | None):
        """Ring another lap; drawn on the next `update_race`."""
        if self._race is not None:
            self._race.selected = lap_id

    def race_following(self) -> bool:
        return self._follow is not None

    def set_race_follow(self, on: bool) -> None:
        """Follow on: put the view aside (its range, whether it was our fit, the pan/zoom switch)
        and let `_FollowCam` drive it, with the scale bar shown and the user's pan/zoom off (the
        camera would undo either a tick later). Off: put all of it back EXACTLY — the user's own
        range, or our fit re-run at the map's size now. Only while a race is on."""
        if bool(on) == (self._follow is not None) or (on and self._race is None):
            return
        vb = self.plot.getViewBox()
        if on:
            self._follow_restore = (vb.viewRange(), self._view_fitted,
                                    tuple(vb.state["mouseEnabled"]))
            self._follow = _FollowCam()
            vb.setMouseEnabled(False, False)
            self.fit_btn.setVisible(False)
            self._follow_apply()        # shows the scale bar once it has a scale to show
            return
        self._follow = None
        (xr, yr), fitted, mouse = self._follow_restore
        self._follow_restore = None
        self._scale_bar.hide()
        vb.setMouseEnabled(*mouse)
        if fitted:
            self._fit_view()
        else:
            vb.setRange(xRange=xr, yRange=yr, padding=0)
            self._view_fitted = False
            self._sync_fit_btn()

    def _free_x0(self, view_w: float) -> float:
        """The free area's left edge (px): right of the race card, unless the card is so wide the
        rest would be a sliver — then the whole map."""
        card = self._race_card
        if card is None or not card.isVisible():
            return 0.0
        x0 = float(card.x() + card.width() + _CHROME_INSET)
        return x0 if x0 < 0.55 * view_w else 0.0

    def _follow_apply(self) -> None:
        """Step the camera to the last race state and set the one range it implies: the camera's
        point at the free area's centre, its ground width across the whole map."""
        cam, state = self._follow, self._race_state
        if cam is None or state is None:
            return
        vb = self.plot.getViewBox()
        r = vb.rect()      # the rect the aspect lock divides by (not boundingRect)
        w, h = r.width(), r.height()
        if w < 8 or h < 8:
            return
        x0 = self._free_x0(w)
        # At the finish race time stops: `held` completes any zoom (a pause does it through dt = 0).
        sel = self._race.selected if self._race is not None else None
        keep = None
        if sel is not None and sel in state.ids:
            k = state.ids.index(sel)
            keep = (float(state.x[k]), float(state.y[k]))
        cam.step(float(state.t), state.x, state.y, w - x0, h, w, motion.enabled(),
                 held=bool(getattr(state, "over", False)), keep=keep)
        if cam.w is None:
            return
        mpp = cam.w / w
        xl = cam.cx - (x0 + (w - x0) / 2) * mpp
        yl = cam.cy - h / 2 * mpp
        vb.setRange(xRange=(xl, xl + w * mpp), yRange=(yl, yl + h * mpp), padding=0)
        self._scale_bar.set_scale(mpp)
        self._scale_bar.reposition()
        if self._scale_bar.isHidden():
            self._scale_bar.show()
            self._scale_bar.raise_()

    def end_race(self):
        """Remove every race item and put the marker, the key and the view back as they were."""
        if self._race is None:
            return
        self.set_race_follow(False)
        self._race_state = None
        self._race.remove()
        self._race = None
        marker_on, key_hidden = self._race_restore
        self.marker.setVisible(marker_on)
        self._tail.set_visible(marker_on)
        self._map_key.setHidden(key_hidden)
        self._sync_timing_chrome()

    def attach_race_card(self, card: QWidget) -> None:
        """Name the race card floated on this canvas, so `grab_clean` keeps it out of a document
        exactly as it keeps the race layer out."""
        self._race_card = card

    def race_items(self) -> list:
        """The race layer's plot items (empty when no race is on) — tests count and inspect them."""
        return list(self._race.items) if self._race is not None else []

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
        """Fill the bucket items for `lap_id`'s channel (speed / Δ / Δ rate / grip). Returns a status:
          * "painted" — the rainbow was filled + the legend set;
          * "hint"    — nothing painted, but the legend carries a hint (Δ on the best lap: no delta
                        to paint — L1); the caller keeps the plain overlay and shows the legend;
          * "none"    — can't be computed (degenerate lap, no best lap for Δ, no g signal for grip).

        The widget only fetches the lap's per-sample arrays from the session here; the per-channel
        value/bucket math (negation, grip fixed scale, GPS-dropout NaN-mask, the best-lap Δ hint) is
        the pure-numpy map_render.rainbow_channel function (it paints nothing)."""
        ch = self.session.lap_channels(lap_id)
        times, xs, ys, speed_kmh, cum = (
            ch["t_telemetry_s"], ch["x_m"], ch["y_m"], ch["speed_kmh"], ch["dist_m"])
        grip_util = self.session.driving.lap_grip_utilization(lap_id) if mode == "grip" else None
        elevation = self.session.lap_elevation_channel(lap_id) if mode == "elevation" else None
        # Brake/throttle: the chart band's OWN array (DrivingChannels.lap_brake_throttle — the one
        # lap_brake_throttle_plot hands the speed chart), fetched and painted, never recomputed.
        pedal = (self.session.driving.lap_brake_throttle(lap_id)[2]
                 if mode == "brake_throttle" else None)
        # Δ-vs-best on the 400-grid (delta()'s y-series); None when no best lap / lap absent. BOTH
        # Δ channels are fed from this one call — the rate channel is that curve's slope, so it
        # must never be computed against a different baseline than the cumulative one shows.
        delta_grid = delta_frac = None
        if mode in ("delta", "delta_rate"):
            got = self.session.delta([lap_id])
            if got is not None and lap_id in got[2]:
                delta_grid = got[2][lap_id][1]
                # Each point's place on that grid: the baseline's odometer where the lap's warp
                # puts it (TRUTH-10), so a point is coloured with the Δ at ITS place on track.
                on_base = self.session.lap_distance_on_baseline(lap_id, cum)
                total = (self.session.active_baseline_total_distance() if on_base is not None
                         else None)
                if total:
                    delta_frac = np.asarray(on_base, float) / float(total)
        result = rainbow_channel(mode, times, xs, ys, speed_kmh, cum, grip_util, delta_grid,
                                 self._speed_unit, elevation=elevation, pedal=pedal,
                                 delta_frac=delta_frac)
        if result is None:
            return "none"
        seg_buckets, lo_txt, hi_txt = result
        tip, short = "", None
        if mode == "elevation" and seg_buckets is not None:
            # What the ramp is, and what nobody measured about it (map_render's labels say only
            # "lowest" → "+N m", which reads like a surveyed range).
            short = (f"{ELEVATION_SOURCE_SHORT}: {lo_txt}", f"{hi_txt} {ELEVATION_NOISE_SHORT}")
            lo_txt, hi_txt = f"{ELEVATION_SOURCE}: {lo_txt}", f"{hi_txt} · {ELEVATION_NOISE}"
            tip = ELEVATION_LEGEND_TIP
        self._legend.set_labels(lo_txt, hi_txt, tip=tip, short=short)
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
        self._corner_markers.set_corners(markers, start_anchors=self._start_clear_anchors(),
                                         obstacles=self._label_obstacles())
        marker = getattr(self, "marker", None)
        if marker is not None:
            p = marker.pos()
            self._corner_markers.avoid_point(p.x(), p.y())

    def _label_obstacles(self) -> list[tuple[float, float, float]]:
        """What a corner label must not cover besides the start/finish cluster: every brake glyph
        on the canvas, as (x, y, half-extent px)."""
        out = []
        brakes = getattr(self, "_brake_markers", None)
        # Read off the item's record array, NOT `item.points()`: that builds a SpotItem per glyph
        # and caches it IN the array (spot -> view of the array -> spot), a cycle the collector
        # cannot see through, so every relayout leaked one SpotItem per brake glyph (PR #591
        # review: 7 per report export). A size of -1 means "the item's default", as in SpotItem.
        for item in (brakes._items if brakes is not None else []):
            d = item.data
            for x, y, size in zip(d["x"], d["y"], d["size"], strict=True):
                size = item.opts["size"] if size < 0 else size
                out.append((float(x), float(y), float(size) / 2.0 + 1.0))
        return out

    def _relayout_labels(self) -> None:
        """Re-place the corner labels against the current scale, start line and brake glyphs —
        then let the live marker nudge them, exactly as a fresh `set_corners` does."""
        cm = getattr(self, "_corner_markers", None)
        if cm is None or not cm._texts:
            return
        cm.relayout(self._start_clear_anchors(), self._label_obstacles())
        marker = getattr(self, "marker", None)
        if marker is not None:
            p = marker.pos()
            cm.avoid_point(p.x(), p.y())

    def _on_range_changed(self, *_):
        cm = getattr(self, "_corner_markers", None)
        if cm is None or not cm._texts:
            return
        # Under Follow a zoom eases over many ticks: re-lay the labels once it settles, not on every
        # tick of it (measured: a re-layout a tick took the Follow p95 from 13 ms to 42 ms).
        cam = getattr(self, "_follow", None)
        if cam is not None and cam.w != cam.goal:
            return
        (sx, sy), (lx, ly) = cm._px_per_data(), cm.layout_scale
        if abs(sx - lx) > 1e-6 * max(lx, 1e-9) or abs(sy - ly) > 1e-6 * max(ly, 1e-9):
            self._relayout_labels()

    def _start_clear_anchors(self):
        """The local-metre points a corner label must clear near the start/finish: the start line's
        two ENDPOINT handle positions AND the current video-position marker (per QA item #21, so C1/
        C11 slide clear of the whole start/finish + marker cluster). Returns [] when nothing is
        available (bare-Session test paths). Each is (x, y, half-extent px): local metres, and the
        drawn box the label must clear around it (START_HANDLE_CLEAR_PX / CORNER_MARKER_CLEAR_PX)."""
        anchors: list[tuple[float, float, float]] = []
        seg = getattr(self.session, "start_line", None)
        if seg is not None:
            anchors.append((seg.x1, seg.y1, START_HANDLE_CLEAR_PX))
            anchors.append((seg.x2, seg.y2, START_HANDLE_CLEAR_PX))
        mark = getattr(self, "_finish_mark", None)
        if mark is not None and mark.at is not None:   # the chequered mark + its arrow
            anchors.append((mark.at[0], mark.at[1], FINISH_CLEAR_PX))
        marker = getattr(self, "marker", None)
        if marker is not None:
            p = marker.pos()
            anchors.append((p.x(), p.y(), CORNER_MARKER_CLEAR_PX))
        return anchors

    def highlight_corner(self, cid: int | None):
        """Ring-highlight one corner's apex marker by 1-based cid (None clears) — driven by
        the consistency panel's corner list (F6). Display-only: no selection, no seek."""
        self._corner_markers.set_highlight(None if cid is None else f"C{int(cid)}")

    def pinned_corner(self) -> str | None:
        """The corner label `highlight_corner` last rang ("C3"), or None."""
        return self._corner_markers.pinned

    def set_hovered_corner(self, label: str | None):
        """Ring the corner hovered here or on the chart ("C3"; None hands the ring back to the
        pinned corner). Pushed by CentralView; inert while a Lap Race owns the canvas."""
        self._corner_markers.set_hover(None if self._race is not None else label)

    # ------------------------------------------------------------- brake glyphs (F5)
    def set_brake_markers(self, lap_markers):
        """Show brake glyphs from `lap_markers` = [(markers, colour)], markers = [(x, y,
        peak_decel)] in local metres. Current lap normally; both laps in compare. [] clears."""
        self._brake_markers.set_markers(lap_markers)
        self._relayout_labels()           # the glyphs are obstacles the labels step around
        # The key describes what is on the canvas, so it is told at the same moment (and from the
        # same argument) rather than drawing a fixed glyph of its own — see _MapLegend.
        self._map_key.set_brake_glyphs([_brake_style(colour, k)
                                        for k, (markers, colour) in enumerate(lap_markers)
                                        if markers])
