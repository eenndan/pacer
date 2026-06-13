"""PlotsView: speed (top) and lap-vs-best delta (bottom) on ONE shared, x-linked x-axis.

Shows the laps selected in the lap table PLUS the best lap as an always-on green reference
(`SERIES_BEST`) — the panel is "Δ TO BEST", so the best lap is the baseline and is drawn even
when the user selects other laps. The best is added to a DRAW set at refresh time only; the
selection (`self._lap_ids`) is never mutated, so the cursor/scrub/hover-dot/auto-follow keep
keying off the user's current lap, not the best. A vertical cursor on both plots follows the
video position whenever the currently-playing lap is among those displayed.

Both plots share a single x-axis driven by the dist/time toggle and kept x-linked, so the same
media moment maps to the same x on both → the two cursors ALWAYS line up vertically (and pan/zoom
on one follows the other). In distance mode x = normalized-distance × best-lap distance (metres,
the axis `session.delta` draws the curves on); in time mode x = time-into-lap (seconds).

The cursor is also a SCRUBBER: it is draggable on both plots, and dragging it seeks the video
within the current lap. The delta plot additionally shows a HOVER DOT that rides the delta curve
under the mouse with its Δ value — independent of the playback cursor, to inspect any point.

This view stays pacer-free — it only emits the raw plot-x and which axis/plot a scrub came from
(`scrubStarted` / `scrubMoved(x, mode)` / `scrubEnded`); app.py owns session + video and does all
conversion, throttled seeking, pause/resume and re-sync. The always-on Δ/speed readout box lives
in app.py too (values from session); the hover dot reads only the curve already drawn here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QVBoxLayout, QWidget

from . import theme
from ._signal import fmt_time
from .session import REFERENCE_ID  # sentinel id of the cross-recording reference curve (F7)
from .theme import C

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

# Antialiased path rendering is a major per-repaint cost; the cursor's InfiniteLine.setValue
# re-renders every visible curve each ~30 Hz tick, so keep it OFF for smooth playback.
pg.setConfigOptions(antialias=False)

# Lap-curve palette (Phase 2): tokenized categorical series. The BEST lap is recoloured to
# C.ahead (green) at draw time so it matches the lap table; the rest cycle through CHART_SERIES.
PALETTE = theme.CHART_SERIES
# Scrub cursor: a thin neutral dashed line (quiet by default); brighter accent + thicker on hover
# so the user can tell it's grabbable. Pens are built ONCE here, never per-tick.
CURSOR_PEN = pg.mkPen(C.text_dim, width=1, style=Qt.DashLine)
CURSOR_HOVER_PEN = pg.mkPen(C.accent, width=2, style=Qt.DashLine)
# Hover dot rides the delta curve: accent fill with a dark canvas outline so it pops on any curve.
HOVER_DOT_BRUSH = pg.mkBrush(C.accent)
HOVER_DOT_PEN = pg.mkPen(C.canvas, width=1)
# F2: sector boundary guide lines — clearly-legible NEUTRAL grey dashed vertical lines on BOTH
# charts. C.text_muted (#6B7280) reads cleanly against the surface and dashed reads better than
# dotted at this scale; they stay NEUTRAL (not amber) so they never clash with the amber
# current-lap curve, and behind everything (setZValue(-5)) so they remain a subordinate backdrop.
SECTOR_LINE_PEN = pg.mkPen(C.text_muted, width=1, style=Qt.DashLine)
SECTOR_LABEL_COLOR = C.text_dim
# The delta plot's y=0 reference line — a faint hairline, same weight as the gridlines.
ZERO_LINE_PEN = pg.mkPen(C.border, width=1)
# F5 driving channels on the speed chart: brake-point glyphs (▼) at each braking-zone onset,
# riding the speed curve, sized by peak decel (same ramp as the map glyphs); and shaded
# coasting spans (a translucent vertical band over each coast region). The brake glyph uses
# the lap's own series colour (app passes it) so compare mode reads lap A vs lap B; the coast
# band is a quiet neutral fill that never competes with the curves.
BRAKE_MARKER_MIN_PX = 9
BRAKE_MARKER_MAX_PX = 17
BRAKE_DECEL_LO = 0.10   # g
BRAKE_DECEL_HI = 0.45   # g
COAST_FILL = pg.mkBrush(C.text_muted)  # alpha applied per-region below
COAST_FILL_ALPHA = 38                  # 0-255: a subtle shaded band, under the curves
COAST_PEN = pg.mkPen(None)


def _brake_glyph_size(peak_decel: float) -> float:
    """Brake event peak decel (g) -> speed-chart glyph size (px), clamped to the ramp ends."""
    frac = (float(peak_decel) - BRAKE_DECEL_LO) / max(BRAKE_DECEL_HI - BRAKE_DECEL_LO, 1e-6)
    frac = min(max(frac, 0.0), 1.0)
    return BRAKE_MARKER_MIN_PX + frac * (BRAKE_MARKER_MAX_PX - BRAKE_MARKER_MIN_PX)


class PlotsView(QWidget):
    # Cursor-scrub signals. plots_view stays pacer-free: it emits only the raw plot-x and which
    # axis/plot the drag came from; app.py converts to a media time, seeks, and re-syncs.
    scrubStarted = Signal()
    scrubMoved = Signal(float, str)  # (plot_x, mode) — mode in {'time','distance'} (shared axis)
    scrubEnded = Signal()
    # Emitted whenever the shared x-axis mode flips (distance ⇄ time). app re-pushes the sector
    # boundary positions for the new mode (F2) so the vertical guide lines reposition correctly.
    modeChanged = Signal(str)  # the new mode: 'time' | 'distance'

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._lap_ids: list[int] = []
        self._curves: list[tuple[object, object]] = []
        self._delta_curves: list[tuple] = []  # [(lid, xs, ys)] cached for the hover-dot snap
        self._speed_curves: dict = {}  # {lid: (sx, spd)} cached so F5 brake glyphs ride the curve
        self._time_mode = False  # shared x-axis: distance (default) vs time-into-lap (both plots)
        self._cursor_t: float | None = None  # last applied position; re-placed after refresh()
        self._user_dragging = False  # True between grab and release of either cursor
        self._suppress = False  # guard programmatic setValue from re-emitting a scrub
        # F2: sector boundary guide lines (start/finish + each sector) on BOTH plots. Items are
        # tracked so they can be cleared/redrawn live as sectors are edited; positions are
        # (label, x) for the CURRENT axis mode, pushed by app via set_sector_lines.
        self._sector_items: list = []
        self._sector_positions: list[tuple[str, float]] = []
        # F5 driving channels on the speed chart: brake glyphs + shaded coasting bands. The
        # DATA is pushed by app (so this view stays pacer-free) as per-lap lists keyed by the
        # SAME draw set the curves use; the items are tracked so refresh() clears/redraws them
        # on the freshly-fit axes (positions are in the current mode's units, like the sectors).
        #   _brake_data: list of (positions, colour) — positions = [(plot-x, peak_decel)]
        #   _coast_data: list of (spans, colour)     — spans = [(plot-x0, plot-x1)]
        self._brake_items: list = []
        self._coast_items: list = []
        self._brake_data: list = []
        self._coast_data: list = []

        # x-axis toggle — drives BOTH plots together (distance ⇄ time-into-lap). The plots share
        # one x-axis and stay x-linked, so the speed + delta cursors always align in either mode.
        # The combo is EXPOSED but NOT placed here: app.py mounts it (right-aligned) in the single
        # consolidated bar above the charts — section label · Δ/speed readout · this toggle — so
        # the toggle no longer eats its own full-width row. Its modeChanged wiring is unchanged.
        self.x_mode_combo = QComboBox()
        self.x_mode_combo.addItems(["x: distance", "x: time"])
        self.x_mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.glw = pg.GraphicsLayoutWidget()
        # Tight outer margins + inter-plot spacing so the plot area fills the panel — the charts
        # are the analytical core and need every pixel. Set on the central GraphicsLayout.
        self.glw.ci.layout.setContentsMargins(2, 2, 2, 2)
        self.glw.ci.layout.setSpacing(4)
        self.p_speed = self.glw.addPlot(row=0, col=0)
        self.p_delta = self.glw.addPlot(row=1, col=0)
        # Give the Δ plot more room — it was a cramped sliver. Row stretch ≈ speed 58 / Δ 42 so
        # the delta curve (the priority readout) is comfortably legible while speed stays dominant.
        self.glw.ci.layout.setRowStretchFactor(0, 58)
        self.glw.ci.layout.setRowStretchFactor(1, 42)
        self.p_speed.setLabel("left", "speed (km/h)")
        # The two plots are x-linked and share ONE x-axis, so the speed plot's bottom axis would
        # just duplicate the Δ plot's "distance (m)"/"time (s)" labels+ticks — wasted vertical
        # space. Hide the speed plot's bottom axis entirely; the SHARED x ticks/label live on the
        # BOTTOM (Δ) plot only. The gridlines below still draw the vertical guides on the speed
        # plot, so it reads cleanly against the same x.
        self.p_speed.hideAxis("bottom")
        # Faint gridlines (alpha 0.10) so they read as a quiet backdrop, not a foreground grid.
        self.p_speed.showGrid(x=True, y=True, alpha=0.10)
        leg = self.p_speed.addLegend(offset=(8, 8))
        self.p_delta.setLabel("left", "Δ to best (s)")
        self.p_delta.setLabel("bottom", "distance (m)")
        # Sub-second deltas otherwise auto-scale to a "(x0.001)" SI prefix on the axis; keep
        # the left axis in plain seconds so it reads e.g. 0.228 directly.
        self.p_delta.getAxis("left").enableAutoSIPrefix(False)
        self.p_delta.showGrid(x=True, y=True, alpha=0.10)
        # Both plots now share ONE x basis in BOTH modes (distance = s×best_dist; time =
        # time-into-lap), so keep them permanently x-linked — same moment = same x on each, and
        # pan/zoom on one follows the other. (Previously unlinked in time mode.)
        self.p_delta.setXLink(self.p_speed)
        self.p_delta.addLine(y=0, pen=ZERO_LINE_PEN)

        # Premium axis styling (Phase 2): dim axis lines + tick text to tokens, tabular numeric
        # tick font, and reduced tick clutter. Set ONCE here — never on the 30 Hz tick. The speed
        # plot's bottom axis is hidden (shared x lives on the Δ plot), so only style its LEFT axis.
        for plot, sides in ((self.p_speed, ("left",)), (self.p_delta, ("left", "bottom"))):
            for side in sides:
                ax = plot.getAxis(side)
                ax.setPen(C.border)            # dim axis line + ticks
                ax.setTextPen(C.text_dim)      # tick labels + axis title
                ax.setTickFont(theme.mono_font(11))  # tabular figures so digits column-align
                ax.setStyle(maxTickLevel=1, hideOverlappingLabels=True)  # fewer, cleaner ticks
        # Legend + per-plot title read dimmed (the title is rebuilt in refresh()).
        if leg is not None:
            leg.setLabelTextColor(C.text_dim)
        for plot in (self.p_speed, self.p_delta):
            plot.titleLabel.setAttr("color", C.text_dim)

        # Draggable scrub cursors. A generous hover region (hoverPen + a wider hover-detection
        # span) makes the thin dashed line easy to grab. movable=True; their drag signals are
        # wired to the scrub handlers below.
        self.cur_speed = pg.InfiniteLine(angle=90, movable=True, pen=CURSOR_PEN,
                                         hoverPen=CURSOR_HOVER_PEN)
        self.cur_delta = pg.InfiniteLine(angle=90, movable=True, pen=CURSOR_PEN,
                                         hoverPen=CURSOR_HOVER_PEN)
        for ln in (self.cur_speed, self.cur_delta):
            ln.setVisible(False)
            ln.setCursor(Qt.SizeHorCursor)  # resize cursor on hover signals "drag me"
        self.p_speed.addItem(self.cur_speed)
        self.p_delta.addItem(self.cur_delta)

        # Continuous drag (sigDragged) → scrubMoved; release (sigPositionChangeFinished) →
        # scrubEnded. scrubStarted is emitted on the first drag tick of a grab (tracked by
        # _user_dragging). Programmatic setValue (the playback tick) does NOT emit sigDragged,
        # and _suppress guards the rest, so the tick can never masquerade as a user scrub.
        self.cur_speed.sigDragged.connect(self._on_speed_dragged)
        self.cur_delta.sigDragged.connect(self._on_delta_dragged)
        self.cur_speed.sigPositionChangeFinished.connect(self._on_drag_finished)
        self.cur_delta.sigPositionChangeFinished.connect(self._on_drag_finished)

        # Hover dot on the delta plot: a marker that rides the delta curve under the mouse, with
        # a small label showing the Δ value (and the distance/time there). Independent of the
        # playback cursor — lets the user inspect ANY point. Hidden until the mouse is over the
        # plot. The handler does a cheap nearest-index lookup on `_delta_curves` (no re-plot).
        self.hover_dot = pg.ScatterPlotItem(size=9, brush=HOVER_DOT_BRUSH, pen=HOVER_DOT_PEN)
        self.hover_dot.setZValue(20)
        self.hover_dot.setVisible(False)
        self.hover_label = pg.TextItem(color=C.accent, anchor=(0, 1))
        self.hover_label.setZValue(21)
        self.hover_label.setVisible(False)
        self.p_delta.addItem(self.hover_dot)
        self.p_delta.addItem(self.hover_label)
        self.p_delta.scene().sigMouseMoved.connect(self._on_delta_hover)

        # The view is now JUST the charts; the x-mode toggle lives in app.py's consolidated bar.
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.glw)

    def _on_mode_changed(self, index):
        self._time_mode = index == 1
        self.refresh()
        # The sector guide-line x-positions are mode-dependent; ask app to re-push them for the
        # new mode (F2). app recomputes via session and calls set_sector_lines.
        self.modeChanged.emit(self._axis_mode())

    # ----------------------------------------------------------- cursor scrub
    def is_dragging(self) -> bool:
        """True while the user is actively dragging either cursor — app.py uses this to stop the
        playback tick from fighting the drag (it ignores position-driven cursor updates then)."""
        return self._user_dragging

    def _axis_mode(self) -> str:
        """The ONE shared x-axis mode driving both plots: 'time' or 'distance' (the latter is
        the s×best_distance axis the conversion helpers treat identically to 'delta')."""
        return "time" if self._time_mode else "distance"

    def _axis_unit(self) -> str:
        """The bare unit for the current x-axis mode ('s' for time, 'm' for distance) — the single
        source for both the axis title (_axis_label) and the hover readout's "@ x <unit>"."""
        return "s" if self._time_mode else "m"

    def _axis_label(self) -> str:
        """The Δ-plot bottom-axis title for the current mode, e.g. 'time (s)' / 'distance (m)'.
        Built from _axis_mode + _axis_unit so the mode→label/unit mapping lives in one place."""
        return f"{self._axis_mode()} ({self._axis_unit()})"

    def axis_mode(self) -> str:
        """Public read of the current shared-axis mode ('time'|'distance'), so app can compute
        the sector boundary positions (F2) in the right units without poking internals."""
        return self._axis_mode()

    def _on_speed_dragged(self, *_):
        self._emit_scrub(self.cur_speed.value(), self._axis_mode())

    def _on_delta_dragged(self, *_):
        # Same shared axis as the speed plot now (distance mode = s×best_dist; time = into-lap),
        # so the delta cursor's x converts with the same mode — no longer a separate 'delta' axis.
        self._emit_scrub(self.cur_delta.value(), self._axis_mode())

    def _emit_scrub(self, x: float, mode: str):
        # Programmatic setValue doesn't emit sigDragged, but guard anyway: never let a re-placed
        # cursor masquerade as a user drag (belt-and-braces against the feedback loop).
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
        """The lap ids currently overlaid on the charts (read-only copy). Mirrors
        LapTable.selected_lap_ids so callers needn't reach into the private `_lap_ids`."""
        return list(self._lap_ids)

    # ----------------------------------------------------------- sector lines (F2)
    def set_sector_lines(self, positions):
        """Draw the sector BOUNDARIES as subtle vertical guide lines on BOTH charts. `positions`
        is a list of (label, plot-x) on the CURRENT shared-axis mode (app computes them via
        session, so this view stays pacer-free). Called live as sectors are added/moved/reset and
        whenever the dist/time mode flips. An empty list clears the lines."""
        self._sector_positions = list(positions or [])
        self._draw_sectors()

    def _clear_sectors(self):
        for plot, item in self._sector_items:
            plot.removeItem(item)
        self._sector_items = []

    def _draw_sectors(self):
        """(Re)draw the cached sector guide lines on both plots. A small label (S/F, S1, S2…)
        sits near the TOP of the speed plot so it doesn't collide with the delta curve. The lines
        sit BELOW the scrub cursor (lower zValue) and use a neutral grey dashed pen so they read
        clearly without obscuring the curves or the cursor."""
        self._clear_sectors()
        if not self._sector_positions:
            return
        for label, x in self._sector_positions:
            for plot in (self.p_speed, self.p_delta):
                # Only the speed plot carries the text label (top); the delta plot just gets the
                # line so the boundary reads across both without crowding the small delta panel.
                # InfiniteLine wants the label as a format string and styling in labelOpts.
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
        """Show brake-point glyphs on the speed chart. `brake_data` is a list of
        (positions, colour) where positions = [(plot-x, peak_decel)] on the CURRENT shared-axis
        mode (app computes them via session, so this view stays pacer-free) and colour is that
        lap's series colour. One entry for the current lap; BOTH laps in compare. [] clears."""
        self._brake_data = list(brake_data or [])
        self._draw_driving()

    def set_coasting_spans(self, coast_data):
        """Shade coasting spans on the speed chart. `coast_data` is a list of (spans, colour)
        where spans = [(plot-x0, plot-x1)] on the CURRENT shared-axis mode. One entry for the
        current lap; BOTH laps in compare. [] clears."""
        self._coast_data = list(coast_data or [])
        self._draw_driving()

    def _clear_driving(self):
        for item in self._brake_items:
            self.p_speed.removeItem(item)
        for item in self._coast_items:
            self.p_speed.removeItem(item)
        self._brake_items = []
        self._coast_items = []

    def _draw_driving(self):
        """(Re)draw the brake glyphs + coast bands on the speed plot from the cached per-lap
        data. Brake glyphs RIDE the lap's speed curve (their y = the speed at the onset x, from
        the cached curve); the coast band is a translucent vertical LinearRegionItem. Both are
        rebuilt wholesale (cheap; only on a selection/mode/compare change, never per tick)."""
        self._clear_driving()
        # Coast bands first so the brake glyphs draw above them.
        for spans, _colour in self._coast_data:
            fill = pg.mkColor(C.text_muted)
            fill.setAlpha(COAST_FILL_ALPHA)
            for x0, x1 in spans:
                region = pg.LinearRegionItem(
                    values=(float(x0), float(x1)), orientation="vertical",
                    brush=pg.mkBrush(fill), pen=COAST_PEN, movable=False)
                region.setZValue(-4)  # above the sector lines (-5), below the curves
                self.p_speed.addItem(region)
                self._coast_items.append(region)
        for positions, colour in self._brake_data:
            if not positions:
                continue
            spots = []
            for x, decel in positions:
                # Ride the speed curve: the glyph y is the speed at this x. Fall back to the
                # nearest drawn-lap curve if this exact lap's curve isn't cached.
                y = self._speed_at_x(float(x))
                if y is None:
                    continue
                spots.append({"pos": (float(x), y), "size": _brake_glyph_size(decel)})
            if not spots:
                continue
            dots = pg.ScatterPlotItem(symbol="t", pen=None, brush=pg.mkBrush(colour), pxMode=True)
            dots.addPoints(spots)
            dots.setZValue(8)  # above the curves + coast band, below the scrub cursor
            self.p_speed.addItem(dots)
            self._brake_items.append(dots)

    def _speed_at_x(self, x: float):
        """The speed-curve y at plot-x `x`, interpolated from the cached speed curves. When
        several laps are drawn (compare), the nearest sample across all curves is used so a
        glyph still lands on a sensible point. None if no speed curve is drawn."""
        best_y = None
        best_dx = None
        for sx, spd in self._speed_curves.values():
            if len(sx) < 2:
                continue
            # x is monotonic; np.interp clamps to the ends (a glyph just past the last sample
            # rides the endpoint). Track the curve whose x-range is closest to x.
            dx = 0.0 if sx[0] <= x <= sx[-1] else min(abs(x - sx[0]), abs(x - sx[-1]))
            if best_dx is None or dx < best_dx:
                best_dx = dx
                best_y = float(np.interp(x, sx, spd))
        return best_y

    def refresh(self):
        for plot, curve in self._curves:
            plot.removeItem(curve)
        self._curves = []
        self._hide_hover()
        self._delta_curves = []  # [(lid, xs, ys)] for the hover-dot nearest-sample snap
        self._speed_curves = {}  # {lid: (sx, spd)} rebuilt below; F5 brake glyphs ride these
        # Clear the sector guide lines too: a stale vertical InfiniteLine would otherwise be
        # caught by the autoRange fit below (like the cursor) and stretch the frozen range.
        # They're redrawn at the end on the freshly-fit axes (and re-pushed by app on a mode flip).
        self._clear_sectors()
        # F5 brake glyphs / coast bands: clear them up front for the same reason (a stale
        # scatter/region would skew the autoRange fit); redrawn at the end on the fitted axes.
        self._clear_driving()

        # Both plots share ONE x-axis (distance = s×best_dist, or time-into-lap), kept x-linked
        # in BOTH modes so the two cursors always align. The shared x label/ticks live ONLY on the
        # bottom (Δ) plot — the speed plot's bottom axis is hidden — so relabel just the Δ axis.
        x_mode = self._axis_mode()
        self.p_delta.setLabel("bottom", self._axis_label())

        # Hide the cursors before fitting: cur_speed still holds the PREVIOUS mode's x (a
        # distance value when toggling to time mode), and a visible InfiniteLine contributes
        # that stale x to autoRange — stretching the frozen range ~8x. They're re-placed on
        # the new axis basis after the fit (below), so they never contaminate the range.
        self.cur_speed.setVisible(False)
        self.cur_delta.setVisible(False)

        # Re-enable autorange so the new selection's curves are fit before we freeze it again.
        self.p_speed.enableAutoRange()
        self.p_delta.enableAutoRange()

        # The whole panel is "Δ TO BEST" — the baseline lap IS the reference curve, so it must
        # ALWAYS be drawn (green) regardless of the user's selection, even when they picked other
        # laps. Build a DRAW set = the selection plus the baseline (appended once if not already
        # chosen), WITHOUT mutating self._lap_ids: the cursor/scrub/hover-dot/auto-follow all key
        # off _lap_ids and the current lap, so the always-on baseline must not change which lap is
        # "current". (session.delta also fetches the baseline's arrays internally, but we pass it
        # explicitly so its speed/delta series come back in `speed`/`delta` to be drawn.)
        #
        # CROSS-RECORDING REFERENCE (F7): when one is loaded, the always-on baseline is the
        # REFERENCE lap (from another recording, id = REFERENCE_ID), not the local best — append
        # that sentinel so its curve is drawn green. DORMANT: with no reference, baseline = the
        # local best lap, byte-identical to before.
        if self.session.has_reference():
            baseline = REFERENCE_ID
        else:
            baseline = self.session.best_lap_id()
        draw_ids = list(self._lap_ids)
        best_always_on = baseline is not None and baseline not in draw_ids
        if best_always_on:
            draw_ids.append(baseline)

        # One delta() call yields BOTH plots' series on the SAME x basis for `x_mode`, so the
        # speed and delta curves (and the cursors) share one axis and stay x-linked → aligned.
        result = self.session.delta(draw_ids, x_mode=x_mode)
        if not result:
            self.p_speed.setTitle(None)
            return
        best, speed, delta = result
        labels = [self._curve_label(lid, lid == best) for lid in draw_ids]
        self.p_speed.setTitle("   ".join(labels) or None)
        for k, lid in enumerate(draw_ids):
            # Semantic colouring (Phase 2): the BEST lap is green (C.ahead) to match the lap
            # table; every other lap cycles through the categorical CHART_SERIES (amber accent
            # first → the primary/first-selected lap pops). width=2 solid keeps the fast path.
            # The ALWAYS-ON best reference (drawn because it's the Δ baseline, not because the
            # user selected it) is rendered slightly thinner so an explicitly-selected lap reads
            # as primary — but it stays clearly green so "Δ to best" always has its green baseline.
            is_best = lid == best
            color = theme.SERIES_BEST if is_best else PALETTE[k % len(PALETTE)]
            width = 1 if (is_best and best_always_on) else 2
            pen = pg.mkPen(color, width=width)
            if lid == REFERENCE_ID:
                name = "reference (best)"
            else:
                name = f"lap {lid}" + (" (best)" if is_best else "")
            if lid in speed:
                sx, spd = speed[lid]
                c = self.p_speed.plot(sx, spd, pen=pen, name=name)
                # x is monotonic (distance or time), so downsampling + clip-to-view is valid and
                # cuts the segments re-rendered on every cursor tick to roughly the visible set.
                c.setDownsampling(auto=True)
                c.setClipToView(True)
                self._curves.append((self.p_speed, c))
                self._speed_curves[lid] = (sx, spd)  # F5: so brake glyphs can ride this curve
            if lid in delta:
                dd, dl = delta[lid]
                c = self.p_delta.plot(dd, dl, pen=pen)
                c.setDownsampling(auto=True)
                c.setClipToView(True)
                self._curves.append((self.p_delta, c))
                self._delta_curves.append((lid, dd, dl))

        # Fit each plot to its data once, then freeze autorange: cursor moves (InfiniteLine
        # setValue every tick) must not trigger a range recompute. x is linked, so fitting both
        # axes here covers the shared x range and each plot's own y range. Pan/zoom still works.
        self.glw.scene().update()
        self.p_speed.autoRange()
        self.p_delta.autoRange()
        self.p_speed.disableAutoRange()
        self.p_delta.disableAutoRange()

        # Re-place the cursors on the now-frozen axes (in the new basis) so they're correct
        # immediately — including when paused, where no position tick follows the toggle.
        if self._cursor_t is not None:
            self.set_playhead_time(self._cursor_t)
        # Redraw the sector guide lines on the freshly-fit axes (positions are in the current
        # mode's units; app re-pushes them when the mode flips, but a selection-only refresh
        # keeps the same positions, so just redraw the cached ones here).
        self._draw_sectors()
        # F5: redraw the brake glyphs / coast bands on the fitted axes (cached per-lap data in
        # the current mode's units; app re-pushes on a mode flip / selection change).
        self._draw_driving()

    def _curve_label(self, lid: int, is_baseline: bool) -> str:
        """The chart-title label for one drawn curve. A local lap reads "lap N m:ss.mmm" (with a
        trailing " ★best" when it's the baseline). The cross-recording REFERENCE curve (id
        REFERENCE_ID, F7) reads "ref <label> m:ss.mmm ★" instead — it has no local lap id, so its
        time comes from the session's reference accessor. DORMANT: never reaches the reference
        branch, so local-lap labels are byte-identical to before."""
        if lid == REFERENCE_ID:
            t = self.session.reference_lap_time() or 0.0
            tag = self.session.reference_label() or "reference"
            return f"ref {tag} {fmt_time(t)} ★"
        return (f"lap {lid} {fmt_time(self.session.lap_time(lid))}"
                + (" ★best" if is_baseline else ""))

    def set_playhead_time(self, t: float, *, force: bool = False):
        """Place BOTH cursors from a single media time t (the shared playhead). Shared setter verb
        with MapView.set_playhead_time.

        Normally (force=False) this is a no-op while the user is dragging a cursor: the source of
        truth is then the drag, not playback, so the tick can't fight the drag (app also pauses,
        but any in-flight positionChanged from the seek must not bounce the cursor either).

        force=True skips that mid-drag guard: app calls it during a scrub with the CLAMPED/converted
        time so the dragged line snaps to the lap boundary and the other plot's cursor stays in
        sync — 'two lines, one truth'."""
        if self._user_dragging and not force:
            return
        self._place(t)

    def _place(self, t: float):
        """Place BOTH cursors from the SAME media time t (the single source of truth) on the
        SHARED x-axis, so they coincide. Both plots use one basis for the active mode: time-mode
        x = time-into-lap; distance-mode x = normalized-distance × best_distance (the axis the
        curves are drawn on — NOT raw odometer, which for a non-best lap would sit the cursor off
        the curve and diverge from the other plot). Guarded by _suppress so a programmatic
        setValue can never masquerade as a user scrub. Caches t so refresh() can re-place after a
        mode/lap change."""
        self._cursor_t = t
        x = None
        mode = self._axis_mode()  # the one shared axis mode (both plots)
        # best_lap_total_distance is only used by the distance/delta axis (time mode ignores it),
        # so skip the lookup entirely in time mode.
        best_d = None if mode == "time" else self.session.best_lap_total_distance()
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
        """Mouse over the delta plot → snap a dot to the nearest delta-curve sample at the
        hovered x and label its Δ value (+ the distance/time there). Independent of the playback
        cursor: lets the user read Δ at ANY point by mouse-over. Cheap — a nearest-index lookup on
        the cached curve arrays, no re-plot. Hidden when the mouse leaves the plot."""
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
