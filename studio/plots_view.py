"""PlotsView: speed (top) and lap-vs-best delta (bottom) on one shared, x-linked x-axis.

The best lap is always drawn green as the Δ baseline (added to a draw set at refresh time only;
the selection `self._lap_ids` is never mutated). Distance mode x = normalized-distance ×
best-lap distance; time mode x = time-into-lap. A draggable cursor on both plots scrubs the
video; the delta plot also shows a hover dot riding the delta curve. Stays pacer-free — emits
raw plot-x + axis mode; app.py owns session/video and all conversion.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QLabel,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import theme, units
from ._signal import fmt_time
from .session import REFERENCE_ID  # sentinel id of the cross-recording reference curve (F7)
from .theme import C, icon

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# Antialias off: the cursor re-renders every visible curve each ~30Hz tick.
pg.setConfigOptions(antialias=False)

# Lap-curve palette: best is recoloured green at draw time (matches the lap table); rest cycle.
PALETTE = theme.CHART_SERIES
# Scrub cursor: thin neutral dashed (quiet); brighter accent + thicker on hover so it reads as
# grabbable. Pens built once.
CURSOR_PEN = pg.mkPen(C.text_dim, width=1, style=Qt.DashLine)
CURSOR_HOVER_PEN = pg.mkPen(C.accent, width=2, style=Qt.DashLine)
# Hover dot rides the delta curve: accent fill with a dark canvas outline so it pops on any curve.
HOVER_DOT_BRUSH = pg.mkBrush(C.accent)
HOVER_DOT_PEN = pg.mkPen(C.canvas, width=1)
# Legend plate: near-opaque surface fill (alpha 230) + hairline border so it reads as a card on
# the chart.
_sr, _sg, _sb = theme._hex_rgb(C.surface)
LEGEND_BRUSH = pg.mkBrush(_sr, _sg, _sb, 230)
LEGEND_PEN = pg.mkPen(C.border, width=1)
# F2: sector boundary guide lines — neutral grey dashed (so they never clash with the amber
# current-lap curve), behind everything (zValue -5).
SECTOR_LINE_PEN = pg.mkPen(C.text_muted, width=1, style=Qt.DashLine)
SECTOR_LABEL_COLOR = C.text_dim
# The delta plot's y=0 reference line — a faint hairline, same weight as the gridlines.
ZERO_LINE_PEN = pg.mkPen(C.border, width=1)
# D1: the SYNTHETIC ideal-lap baseline (lower-envelope theoretical best). Best-SECTOR colour to
# echo the lap table's theoretical-best cells, dashed so it never reads as a real driven lap. Built
# at DRAW time (not frozen at import) so it follows the active palette's best-sector hue (purple →
# teal in the colour-blind palette), matching the lap-table cells.
def _ideal_line_pen():
    return pg.mkPen(theme.best_sector_colour(), width=1, style=Qt.DashLine)
# F5: brake glyphs (sized by peak decel) ride the speed curve; coast spans shade a neutral band.
COAST_FILL_ALPHA = 38                  # 0-255: a subtle shaded band, under the curves
COAST_PEN = pg.mkPen(None)
# D3: the SYNTHETIC brake/throttle band — a thin sub-track pinned to the bottom of the speed
# plot. Brake fills toward red (C.behind), throttle toward green (C.ahead); subtle alpha so it
# reads as a secondary backdrop, never competing with the speed curves. ESTIMATED (legend-labelled).
BT_FILL_ALPHA = 110                    # 0-255: the filled pedal band (more present than coast, still quiet)
BT_TRACK_FRAC = 0.16                   # the band occupies the bottom ~16% of the speed plot's y-range
# Brake fills toward the "behind" hue, throttle toward the "ahead" hue. Resolved at DRAW time via
# the palette accessors (not frozen at import) so the band follows the active palette — red/green by
# default, orange/blue in the colour-blind palette (matching the Δ readout + rainbow map).
BT_PEN = pg.mkPen(None)
BT_BASELINE_PEN = pg.mkPen(C.border, width=1, style=Qt.DotLine)  # the band's zero (lift/cruise) line


class PlotsView(QWidget):
    # Scrub signals (pacer-free: emit raw plot-x; app converts/seeks).
    scrubStarted = Signal()
    scrubMoved = Signal(float, str)  # (plot_x, mode) — mode in {'time','distance'} (shared axis)
    scrubEnded = Signal()
    # Fired when the shared x-axis mode flips; app re-pushes sector positions for the new mode (F2).
    modeChanged = Signal(str)  # the new mode: 'time' | 'distance'

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        # Speed-axis display unit (km/h default); the app pushes the persisted choice via
        # set_speed_unit. Speed VALUES stay km/h — the y-axis label is the only conversion here
        # (the plotted curves are the raw km/h arrays; only the axis LABEL names the unit).
        self._speed_unit = units.DEFAULT_UNIT
        self._lap_ids: list[int] = []
        self._curves: list[tuple[object, object]] = []
        self._delta_curves: list[tuple] = []  # [(lid, xs, ys)] cached for the hover-dot snap
        self._speed_curves: dict = {}  # {lid: (sx, spd)} cached so F5 brake glyphs ride the curve
        self._time_mode = False  # shared x-axis: distance (default) vs time-into-lap (both plots)
        self._cursor_t: float | None = None  # last applied position; re-placed after refresh()
        self._user_dragging = False  # True between grab and release of either cursor
        self._suppress = False  # guard programmatic setValue from re-emitting a scrub
        # F2: sector boundary guide lines on both plots; positions are (label, x) for the current
        # axis mode, pushed by app via set_sector_lines.
        self._sector_items: list = []
        self._sector_positions: list[tuple[str, float]] = []
        # F5 driving channels (brake glyphs + coast bands); data pushed by app, redrawn on fitted axes.
        self._brake_items: list = []
        self._coast_items: list = []
        self._brake_data: list = []  # [(positions=[(x,decel)], colour)]
        self._coast_data: list = []  # [(spans=[(x0,x1)], colour)]
        # D3 synthetic brake/throttle band: per-lap (plot_x, intensity[-1..1]); drawn as a sub-track
        # at the bottom of the speed plot when the toggle is on.
        self._brake_throttle_items: list = []
        self._brake_throttle_data: list = []  # [(xs, intensity)]

        # x-axis toggle (distance/time). Exposed but mounted by app.py in its consolidated bar.
        self.x_mode_combo = QComboBox()
        self.x_mode_combo.addItems(["x: distance", "x: time"])
        self.x_mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        # D1 opt-in: overlay the synthetic IDEAL-lap baseline (lower envelope of the clean laps)
        # on the Δ plot. Default off so the standard Δ-to-best view stays uncluttered. Exposed;
        # central_view mounts it in the charts header next to the x-mode toggle.
        self._show_ideal = False
        self.ideal_btn = QPushButton("Ideal lap")
        self.ideal_btn.setIcon(icon("ph.star-four"))
        self.ideal_btn.setCheckable(True)
        self.ideal_btn.setToolTip(
            "Ideal lap: overlay the SYNTHETIC theoretical-best Δ — the lower envelope of your "
            "clean laps' per-distance times (dashed purple, dips below the y=0 best-lap line). "
            "No single lap drove it; it shows where your achievable lap is faster than your best.")
        self.ideal_btn.toggled.connect(self._on_ideal_toggled)

        # D3 opt-in: a SYNTHETIC brake/throttle band under the speed curve. Default off so the
        # speed chart stays clean; mounted by central_view in the charts header next to the
        # ideal-lap toggle. ESTIMATED (we have no pedal sensors — it's the speed-derived g).
        self._show_brake_throttle = False
        self.brake_throttle_btn = QPushButton("Brake/Throttle")
        self.brake_throttle_btn.setIcon(icon("ph.gauge"))
        self.brake_throttle_btn.setCheckable(True)
        self.brake_throttle_btn.setToolTip(
            "Brake/Throttle band (ESTIMATED): a pedal-style trace under the speed curve, inferred "
            "from the GPS speed-derivative — pacer has no pedal sensors. Red fills below the line "
            "while braking, green above while on power. Derived from the same signal as the brake "
            "points; not measured.")
        self.brake_throttle_btn.toggled.connect(self._on_brake_throttle_toggled)

        self.glw = pg.GraphicsLayoutWidget()
        # Tight margins so the charts fill the panel.
        self.glw.ci.layout.setContentsMargins(2, 2, 2, 2)
        self.glw.ci.layout.setSpacing(4)
        self.p_speed = self.glw.addPlot(row=0, col=0)
        self.p_delta = self.glw.addPlot(row=1, col=0)
        # Speed 58 / delta 42 row stretch - delta legible, speed dominant.
        self.glw.ci.layout.setRowStretchFactor(0, 58)
        self.glw.ci.layout.setRowStretchFactor(1, 42)
        self._apply_speed_axis_label()
        # Hide the speed plot's bottom axis: the shared x ticks/label live on the Δ plot only.
        self.p_speed.hideAxis("bottom")
        # Faint gridlines (alpha 0.10) so they read as a quiet backdrop, not a foreground grid.
        self.p_speed.showGrid(x=True, y=True, alpha=0.10)
        leg = self.p_speed.addLegend(offset=(8, 8))
        # D1: a legend on the Δ plot too, used ONLY by the synthetic ideal-lap entry (lap Δ curves
        # are drawn unnamed there, so it stays a single quiet line item explaining the dashed line).
        self._delta_legend = self.p_delta.addLegend(offset=(8, 8))
        self.p_delta.setLabel("left", "Δ to best (s)")
        self.p_delta.setLabel("bottom", "distance (m)")
        # Sub-second deltas otherwise auto-scale to a "(x0.001)" SI prefix; keep plain seconds.
        self.p_delta.getAxis("left").enableAutoSIPrefix(False)
        self.p_delta.showGrid(x=True, y=True, alpha=0.10)
        # Permanently x-linked: same x basis in both modes, so cursors/pan/zoom track.
        self.p_delta.setXLink(self.p_speed)
        self.p_delta.addLine(y=0, pen=ZERO_LINE_PEN)

        # Axis styling, set once: dim tokens, tabular mono font, fewer ticks.
        for plot, sides in ((self.p_speed, ("left",)), (self.p_delta, ("left", "bottom"))):
            for side in sides:
                ax = plot.getAxis(side)
                ax.setPen(C.border)            # dim axis line + ticks
                ax.setTextPen(C.text_dim)      # tick labels + axis title
                ax.setTickFont(theme.mono_font(11))  # tabular figures so digits column-align
                ax.setStyle(maxTickLevel=1, hideOverlappingLabels=True)  # fewer, cleaner ticks
        # Legend: dimmed text on a surface plate (both the speed legend and the Δ ideal-lap one).
        for lg in (leg, self._delta_legend):
            if lg is not None:
                lg.setLabelTextColor(C.text_dim)
                lg.setBrush(LEGEND_BRUSH)
                lg.setPen(LEGEND_PEN)
        for plot in (self.p_speed, self.p_delta):
            plot.titleLabel.setAttr("color", C.text_dim)

        # Draggable scrub cursors; hoverPen makes the thin dashed line easy to grab.
        self.cur_speed = pg.InfiniteLine(angle=90, movable=True, pen=CURSOR_PEN,
                                         hoverPen=CURSOR_HOVER_PEN)
        self.cur_delta = pg.InfiniteLine(angle=90, movable=True, pen=CURSOR_PEN,
                                         hoverPen=CURSOR_HOVER_PEN)
        for ln in (self.cur_speed, self.cur_delta):
            ln.setVisible(False)
            ln.setCursor(Qt.SizeHorCursor)  # resize cursor on hover signals "drag me"
        self.p_speed.addItem(self.cur_speed)
        self.p_delta.addItem(self.cur_delta)

        # sigDragged->scrubMoved; sigPositionChangeFinished->scrubEnded. setValue doesn't emit sigDragged.
        self.cur_speed.sigDragged.connect(self._on_speed_dragged)
        self.cur_delta.sigDragged.connect(self._on_delta_dragged)
        self.cur_speed.sigPositionChangeFinished.connect(self._on_drag_finished)
        self.cur_delta.sigPositionChangeFinished.connect(self._on_drag_finished)

        # Hover dot: rides the delta curve under the mouse, showing the delta value (see _on_delta_hover).
        self.hover_dot = pg.ScatterPlotItem(size=9, brush=HOVER_DOT_BRUSH, pen=HOVER_DOT_PEN)
        self.hover_dot.setZValue(20)
        self.hover_dot.setVisible(False)
        self.hover_label = pg.TextItem(color=C.accent, anchor=(0, 1))
        self.hover_label.setZValue(21)
        self.hover_label.setVisible(False)
        self.p_delta.addItem(self.hover_dot)
        self.p_delta.addItem(self.hover_label)
        self.p_delta.scene().sigMouseMoved.connect(self._on_delta_hover)

        # E1: empty-state placeholder shown (via the stack) when there are no laps to plot.
        self._empty = QLabel(
            "No lap data to plot.\n\n"
            "This recording has no complete laps — the speed and Δ-to-best "
            "charts need at least one finished lap.")
        self._empty.setProperty("role", "EmptyState")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)

        # The view is now JUST the charts; the x-mode toggle lives in app.py's consolidated bar.
        self._stack = QStackedWidget()
        self._stack.addWidget(self.glw)     # index 0: the charts
        self._stack.addWidget(self._empty)  # index 1: the empty-state placeholder
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self._stack)

    def _on_mode_changed(self, index):
        self._time_mode = index == 1
        self.refresh()
        # Sector positions are mode-dependent; ask app to re-push them for the new mode (F2).
        self.modeChanged.emit(self._axis_mode())

    def _on_ideal_toggled(self, on: bool):
        """D1: toggle the synthetic ideal-lap baseline overlay on the Δ plot."""
        self._show_ideal = on
        self.ideal_btn.setIcon(icon("ph.star-four", color=C.best if on else C.text))
        self.refresh()

    def _on_brake_throttle_toggled(self, on: bool):
        """D3: toggle the synthetic brake/throttle band under the speed curve."""
        self._show_brake_throttle = on
        self.brake_throttle_btn.setIcon(icon("ph.gauge", color=C.accent if on else C.text))
        self._draw_brake_throttle()

    # ----------------------------------------------------------- cursor scrub
    def is_dragging(self) -> bool:
        """True while a cursor is being dragged (app suppresses the playback tick then)."""
        return self._user_dragging

    def _axis_mode(self) -> str:
        """The shared x-axis mode: 'time' or 'distance' (the s×best_distance axis)."""
        return "time" if self._time_mode else "distance"

    def _axis_unit(self) -> str:
        """The bare unit for the current x-axis mode ('s' for time, 'm' for distance)."""
        return "s" if self._time_mode else "m"

    def _axis_label(self) -> str:
        """The Δ-plot bottom-axis title for the current mode, e.g. 'time (s)' / 'distance (m)'."""
        return f"{self._axis_mode()} ({self._axis_unit()})"

    def axis_mode(self) -> str:
        """Public read of the current shared-axis mode ('time'|'distance')."""
        return self._axis_mode()

    def _on_speed_dragged(self, *_):
        self._emit_scrub(self.cur_speed.value(), self._axis_mode())

    def _on_delta_dragged(self, *_):
        # Shared axis with the speed plot, so the delta cursor's x converts with the same mode.
        self._emit_scrub(self.cur_delta.value(), self._axis_mode())

    def _emit_scrub(self, x: float, mode: str):
        # _suppress guards a programmatic re-place from looking like a drag.
        if self._suppress:
            return
        if not self._user_dragging:
            self._user_dragging = True
            self.scrubStarted.emit()
        self.scrubMoved.emit(float(x), mode)

    def _on_drag_finished(self, *_):
        if self._user_dragging:
            self._user_dragging = False
            self.scrubEnded.emit()

    def set_laps(self, lap_ids):
        self._lap_ids = list(lap_ids)
        self.refresh()

    def selected_lap_ids(self) -> list[int]:
        """Copy of the lap ids overlaid on the charts."""
        return list(self._lap_ids)

    # ----------------------------------------------------------- sector lines (F2)
    def set_sector_lines(self, positions):
        """Set sector guide lines on both charts. positions = [(label, plot-x)] in the current
        mode; [] clears."""
        self._sector_positions = list(positions or [])
        self._draw_sectors()

    def _clear_sectors(self):
        for plot, item in self._sector_items:
            plot.removeItem(item)
        self._sector_items = []

    def _draw_sectors(self):
        """(Re)draw cached sector lines: label on the speed plot top only, behind the cursor."""
        self._clear_sectors()
        if not self._sector_positions:
            return
        for label, x in self._sector_positions:
            for plot in (self.p_speed, self.p_delta):
                # Label on the speed plot only (delta panel is too small).
                text = label if plot is self.p_speed else None
                ln = pg.InfiniteLine(
                    pos=float(x), angle=90, pen=SECTOR_LINE_PEN, label=text,
                    labelOpts={"color": SECTOR_LABEL_COLOR, "position": 0.96, "movable": False},
                )
                ln.setZValue(-5)  # behind the curves + cursor; a subtle backdrop
                plot.addItem(ln)
                self._sector_items.append((plot, ln))

    # ----------------------------------------------------- driving channels (F5)
    def set_brake_markers(self, brake_data):
        """Set brake glyphs. brake_data = [(positions=[(x,decel)], colour)]; [] clears."""
        self._brake_data = list(brake_data or [])
        self._draw_driving()

    def set_coasting_spans(self, coast_data):
        """Set coast bands. coast_data = [(spans=[(x0,x1)], colour)]; [] clears."""
        self._coast_data = list(coast_data or [])
        self._draw_driving()

    def set_brake_throttle(self, bt_data):
        """D3: set the synthetic brake/throttle band data. bt_data = [(xs, intensity[-1..1])] per
        lap (xs on the shared axis); [] clears. Only rendered while the toggle is on."""
        self._brake_throttle_data = list(bt_data or [])
        self._draw_brake_throttle()

    def _clear_driving(self):
        for item in self._brake_items:
            self.p_speed.removeItem(item)
        for item in self._coast_items:
            self.p_speed.removeItem(item)
        self._brake_items = []
        self._coast_items = []

    def _draw_driving(self):
        """(Re)draw brake glyphs (riding the speed curve) + coast bands from cached data."""
        self._clear_driving()
        # Coast bands first so the brake glyphs draw above them.
        for spans, _colour in self._coast_data:
            fill = pg.mkColor(C.text_muted)
            fill.setAlpha(COAST_FILL_ALPHA)
            for x0, x1 in spans:
                region = pg.LinearRegionItem(
                    values=(float(x0), float(x1)), orientation="vertical",
                    brush=pg.mkBrush(fill), pen=COAST_PEN, movable=False)
                region.setZValue(-4)  # above sector lines, below the curves
                self.p_speed.addItem(region)
                self._coast_items.append(region)
        for positions, colour in self._brake_data:
            if not positions:
                continue
            spots = []
            for x, decel in positions:
                # Glyph y = speed at this x (riding the curve).
                y = self._speed_at_x(float(x))
                if y is None:
                    continue
                spots.append({"pos": (float(x), y), "size": theme.brake_glyph_size(decel)})
            if not spots:
                continue
            dots = pg.ScatterPlotItem(symbol="t", pen=None, brush=pg.mkBrush(colour), pxMode=True)
            dots.addPoints(spots)
            dots.setZValue(8)  # above curves + coast band, below the cursor
            self.p_speed.addItem(dots)
            self._brake_items.append(dots)

    def _clear_brake_throttle(self):
        for item in self._brake_throttle_items:
            self.p_speed.removeItem(item)
        self._brake_throttle_items = []

    def _draw_brake_throttle(self):
        """D3: (re)draw the synthetic brake/throttle band as a sub-track pinned to the BOTTOM of the
        speed plot's current y-range. Intensity in [-1,1] maps onto a thin strip (height BT_TRACK_FRAC
        of the y-span): brake fills DOWN from the strip's mid-line toward red, throttle fills UP
        toward green, so it reads like a pedal trace under the speed curve. No-op when the toggle is
        off or there's no data. Drawn after the autorange fit (called from refresh) so the strip is
        placed on the frozen axes; re-pinned each refresh."""
        self._clear_brake_throttle()
        if not self._show_brake_throttle or not self._brake_throttle_data:
            return
        (y0, y1) = self.p_speed.getViewBox().viewRange()[1]
        span = y1 - y0
        if span <= 0:
            return
        # The strip: a band of height BT_TRACK_FRAC*span sitting just above the x-axis, with its
        # zero (lift/cruise) line through the middle so brake fills below it and throttle above.
        half = 0.5 * BT_TRACK_FRAC * span
        mid = y0 + half
        # Resolve the fill hues from the ACTIVE palette at draw time so the band follows a
        # colour-blind flip (behind=red/orange, ahead=green/blue) — matching the Δ readout + rainbow.
        brake_fill = pg.mkColor(theme.behind_colour())
        brake_fill.setAlpha(BT_FILL_ALPHA)
        thr_fill = pg.mkColor(theme.ahead_colour())
        thr_fill.setAlpha(BT_FILL_ALPHA)
        for xs, intensity in self._brake_throttle_data:
            xs = np.asarray(xs, float)
            inten = np.asarray(intensity, float)
            n = min(len(xs), len(inten))
            if n < 2:
                continue
            xs, inten = xs[:n], inten[:n]
            ys = mid + np.clip(inten, -1.0, 1.0) * half  # -1 -> y0 (full brake), +1 -> mid+half (full throttle)
            base = pg.PlotDataItem(xs, np.full(n, mid), pen=BT_PEN)
            curve = pg.PlotDataItem(xs, ys, pen=BT_PEN)
            # Two fills off the same mid baseline: red below (braking), green above (throttle).
            for thresh, fill in ((np.minimum(ys, mid), brake_fill),
                                 (np.maximum(ys, mid), thr_fill)):
                edge = pg.PlotDataItem(xs, thresh, pen=BT_PEN)
                region = pg.FillBetweenItem(base, edge, brush=pg.mkBrush(fill))
                region.setZValue(-3)  # above coast bands, below the speed curves
                self.p_speed.addItem(region)
                self._brake_throttle_items.append(region)
            curve.setZValue(-2)
            self.p_speed.addItem(curve)
            self._brake_throttle_items.append(curve)
        # The band's zero/cruise reference line.
        zero = pg.InfiniteLine(pos=mid, angle=0, pen=BT_BASELINE_PEN)
        zero.setZValue(-3)
        self.p_speed.addItem(zero)
        self._brake_throttle_items.append(zero)

    def _speed_at_x(self, x: float):
        """Interpolated speed-curve y at plot-x x (nearest curve when several drawn); None if none."""
        best_y = None
        best_dx = None
        for sx, spd in self._speed_curves.values():
            if len(sx) < 2:
                continue
            # np.interp clamps to ends; pick the curve whose x-range is closest.
            dx = 0.0 if sx[0] <= x <= sx[-1] else min(abs(x - sx[0]), abs(x - sx[-1]))
            if best_dx is None or dx < best_dx:
                best_dx = dx
                best_y = float(np.interp(x, sx, spd))
        return best_y

    def _apply_speed_axis_label(self):
        """Name the speed y-axis in the current display unit ('speed (km/h)' / 'speed (mph)')."""
        self.p_speed.setLabel("left", f"speed ({units.speed_label(self._speed_unit)})")

    def set_speed_unit(self, unit: str):
        """Switch the speed display unit live: re-label the y-axis and re-plot so the curves carry
        the converted values. Called by the window's Units toggle. No-op if unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self._apply_speed_axis_label()
        self.refresh()

    def refresh_palette(self):
        """Re-render the charts after a colour-blind-palette flip so the SEMANTIC-hue surfaces follow
        it: the brake/throttle band (behind/ahead fills) and the synthetic ideal-lap line (best-sector
        hue) both read the palette accessors at draw time, so a plain refresh() re-pens them. The
        identity lap-curve palette (CHART_SERIES) is palette-independent, so this is just a redraw."""
        self.refresh()

    def refresh(self):
        for plot, curve in self._curves:
            plot.removeItem(curve)
        self._curves = []
        # D1: clear + hide the Δ ideal-lap legend; _draw_ideal re-adds + reveals it only when on.
        self._delta_legend.clear()
        self._delta_legend.setVisible(False)
        self._hide_hover()
        self._delta_curves = []  # [(lid, xs, ys)] for the hover-dot nearest-sample snap
        self._speed_curves = {}  # {lid: (sx, spd)} rebuilt below; F5 brake glyphs ride these
        # Clear sector lines + driving items up front: a stale item left in place would be caught
        # by the autoRange fit below (like the cursor) and stretch the frozen range; both are
        # redrawn at the end on the fitted axes.
        self._clear_sectors()
        self._clear_driving()
        self._clear_brake_throttle()

        x_mode = self._axis_mode()
        self.p_delta.setLabel("bottom", self._axis_label())  # shared x label lives on the Δ plot

        # Hide the cursors before fitting: a visible InfiniteLine still holding the previous mode's
        # x would contribute that stale value to autoRange. Re-placed after the fit.
        self.cur_speed.setVisible(False)
        self.cur_delta.setVisible(False)

        # Re-enable autorange so the new selection's curves are fit before we freeze it again.
        self.p_speed.enableAutoRange()
        self.p_delta.enableAutoRange()

        # Always draw the delta baseline (green) even if unselected, without mutating _lap_ids.
        # F7: baseline = the cross-recording REFERENCE lap when loaded, else the local best.
        if self.session.has_reference():
            baseline = REFERENCE_ID
        else:
            baseline = self.session.best_lap_id()
        draw_ids = list(self._lap_ids)
        best_always_on = baseline is not None and baseline not in draw_ids
        if best_always_on:
            draw_ids.append(baseline)

        # One delta() call yields both plots' series on the same x basis, so they stay x-linked.
        result = self.session.delta(draw_ids, x_mode=x_mode)
        if not result:
            self._stack.setCurrentIndex(1)  # E1: no laps -> empty-state placeholder
            return
        self._stack.setCurrentIndex(0)
        best, speed, delta = result
        for k, lid in enumerate(draw_ids):
            # Best lap green (matches lap table); others cycle CHART_SERIES. Always-on best drawn
            # thinner so a selected lap reads primary.
            is_best = lid == best
            color = theme.SERIES_BEST if is_best else PALETTE[k % len(PALETTE)]
            width = 1 if (is_best and best_always_on) else 2
            pen = pg.mkPen(color, width=width)
            # Legend label folds in the lap time (see _curve_label).
            name = self._curve_label(lid, is_best)
            if lid in speed:
                sx, spd = speed[lid]
                # Convert km/h → the display unit at the plot boundary (identity for km/h). The
                # CACHED curve is the displayed one so the brake/throttle band + hover-snap ride
                # the visible line; analysis math elsewhere keeps the raw km/h.
                spd = units.convert_speed(spd, self._speed_unit)
                c = self.p_speed.plot(sx, spd, pen=pen, name=name)
                # Monotonic x -> downsample + clip-to-view is valid; cuts per-tick re-render.
                c.setDownsampling(auto=True)
                c.setClipToView(True)
                self._curves.append((self.p_speed, c))
                self._speed_curves[lid] = (sx, spd)  # F5: brake glyphs ride this curve
            if lid in delta:
                dd, dl = delta[lid]
                c = self.p_delta.plot(dd, dl, pen=pen)
                c.setDownsampling(auto=True)
                c.setClipToView(True)
                self._curves.append((self.p_delta, c))
                self._delta_curves.append((lid, dd, dl))

        # D1: optional synthetic ideal-lap baseline, drawn on the SAME Δ-to-best axis (it dips
        # below the y=0 best-lap line). Drawn before the fit so its trough is in the y-range; not
        # added to _delta_curves so the scrub hover-dot stays on real laps.
        self._draw_ideal(x_mode)

        # Fit once, then freeze autorange so per-tick cursor moves don't recompute the range.
        self.glw.scene().update()
        self.p_speed.autoRange()
        self.p_delta.autoRange()
        self.p_speed.disableAutoRange()
        self.p_delta.disableAutoRange()

        # Re-place the cursors on the now-frozen axes (also covers the paused-toggle case).
        if self._cursor_t is not None:
            self.set_playhead_time(self._cursor_t)
        # Redraw the cached sector lines + driving items on the freshly-fit axes. The
        # brake/throttle band is pinned to the now-frozen y-range, so it draws last.
        self._draw_sectors()
        self._draw_driving()
        self._draw_brake_throttle()

    def _draw_ideal(self, x_mode: str):
        """D1: draw the synthetic ideal-lap baseline on the Δ plot when the toggle is on.

        `ideal_delta_to_best` returns the ideal envelope expressed on delta()'s own Δ-to-best
        axis (ideal − best ≤ 0), so it lays under the existing curves in the same reference frame
        and honors both x-modes. Dashed purple + a clearly-synthetic legend entry so it can't be
        mistaken for a real driven lap. No-op (and no legend entry) when the ideal can't be built
        (e.g. no clean lap)."""
        if not self._show_ideal:
            return
        series = self.session.ideal_delta_to_best(x_mode=x_mode)
        if series is None:
            return
        ix, iy = series
        c = self.p_delta.plot(ix, iy, pen=_ideal_line_pen(), name="ideal lap (synthetic)")
        c.setDownsampling(auto=True)
        c.setClipToView(True)
        self._curves.append((self.p_delta, c))
        self._delta_legend.setVisible(True)

    def _curve_label(self, lid: int, is_baseline: bool) -> str:
        """Legend label for a curve: 'lap N m:ss.mmm' (+ ' · best' on the baseline), or
        'ref <label> ...' for the F7 reference curve."""
        if lid == REFERENCE_ID:
            t = self.session.reference_lap_time() or 0.0
            tag = self.session.reference_label() or "reference"
            return f"ref {tag} {fmt_time(t)} · best"
        return (f"lap {lid} {fmt_time(self.session.lap_time(lid))}"
                + (" · best" if is_baseline else ""))

    def set_playhead_time(self, t: float, *, force: bool = False):
        """Place both cursors from media time t. No-op mid-drag unless force=True (used during a
        scrub to snap the dragged line to the clamped time)."""
        if self._user_dragging and not force:
            return
        self._place(t)

    def _place(self, t: float):
        """Place both cursors at media time t on the shared x-axis. Caches t for refresh()
        re-placement; _suppress prevents a re-emit."""
        self._cursor_t = t
        x = None
        mode = self._axis_mode()
        # Distance mode: scale by the active baseline total (same basis as delta()'s x-grid).
        # Time mode skips it.
        best_d = None if mode == "time" else self.session.active_baseline_total_distance()
        for lid in self._lap_ids:
            window = self.session.lap_window(lid)
            if window and window[0] <= t <= window[1]:
                x = self.session.plot_x_at_media_time(lid, t, mode, best_distance=best_d)
                break
        self._suppress = True
        try:
            # One x for both — the plots are x-linked, so the same value lines the cursors up.
            self.cur_speed.setVisible(x is not None)
            self.cur_delta.setVisible(x is not None)
            if x is not None:
                self.cur_speed.setValue(x)
                self.cur_delta.setValue(x)
        finally:
            self._suppress = False

    # --------------------------------------------------------------- hover dot
    def _hide_hover(self):
        self.hover_dot.setVisible(False)
        self.hover_label.setVisible(False)

    def _on_delta_hover(self, scene_pos):
        """Snap the hover dot to the nearest delta-curve sample at the hovered x and label its
        delta value."""
        vb = self.p_delta.getViewBox()
        if vb is None or not self._delta_curves:
            self._hide_hover()
            return
        # Only react when the cursor is actually inside the delta plot's scene rect.
        if not self.p_delta.sceneBoundingRect().contains(scene_pos):
            self._hide_hover()
            return
        mp = vb.mapSceneToView(scene_pos)
        mx, my = float(mp.x()), float(mp.y())
        # Find the curve + sample nearest the hovered x; if several laps are shown, prefer the
        # one whose y at that x is closest to the cursor (so hovering near a curve picks it).
        best = None  # (dx_to_y_dist, lid, xi, yi)
        for lid, xs, ys in self._delta_curves:
            if len(xs) == 0:
                continue
            j = int(np.argmin(np.abs(xs - mx)))
            xi, yi = float(xs[j]), float(ys[j])
            score = abs(yi - my)
            if best is None or score < best[0]:
                best = (score, lid, xi, yi)
        if best is None:
            self._hide_hover()
            return
        _, lid, xi, yi = best
        self.hover_dot.setData([xi], [yi])
        unit = self._axis_unit()
        self.hover_label.setText(f"lap {lid}  Δ {yi:+.3f} s\n@ {xi:.0f} {unit}")
        self.hover_label.setPos(xi, yi)
        self.hover_dot.setVisible(True)
        self.hover_label.setVisible(True)

    def leaveEvent(self, event):  # noqa: N802 (Qt override)
        # The widget lost the mouse — hide the hover dot (sigMouseMoved may not fire on exit).
        self._hide_hover()
        super().leaveEvent(event)
