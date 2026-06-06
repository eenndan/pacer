"""PlotsView: speed (top) and lap-vs-best delta (bottom) on ONE shared, x-linked x-axis.

Shows the laps selected in the lap table. A vertical cursor on both plots follows the
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

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from .session import fmt_time

# Antialiased path rendering is a major per-repaint cost; the cursor's InfiniteLine.setValue
# re-renders every visible curve each ~30 Hz tick, so keep it OFF for smooth playback.
pg.setConfigOptions(antialias=False)

PALETTE = ["#39a0ed", "#ef476f", "#ffd166", "#06d6a0", "#b388eb", "#ff924c", "#118ab2"]
CURSOR_PEN = pg.mkPen("#ffffff", width=1, style=Qt.DashLine)
# Brighter/thicker pen while hovering so the user can tell the cursor is grabbable.
CURSOR_HOVER_PEN = pg.mkPen("#ffd166", width=2, style=Qt.DashLine)
HOVER_DOT_BRUSH = pg.mkBrush("#ffd166")
HOVER_DOT_PEN = pg.mkPen("#000000", width=1)


class PlotsView(QWidget):
    # Cursor-scrub signals. plots_view stays pacer-free: it emits only the raw plot-x and which
    # axis/plot the drag came from; app.py converts to a media time, seeks, and re-syncs.
    scrubStarted = Signal()
    scrubMoved = Signal(float, str)  # (plot_x, mode) — mode in {'time','distance'} (shared axis)
    scrubEnded = Signal()

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._lap_ids: list[int] = []
        self._curves: list[tuple[object, object]] = []
        self._delta_curves: list[tuple] = []  # [(lid, xs, ys)] cached for the hover-dot snap
        self._time_mode = False  # shared x-axis: distance (default) vs time-into-lap (both plots)
        self._cursor_t: float | None = None  # last applied position; re-placed after refresh()
        self._user_dragging = False  # True between grab and release of either cursor
        self._suppress = False  # guard programmatic setValue from re-emitting a scrub

        # x-axis toggle — drives BOTH plots together (distance ⇄ time-into-lap). The plots share
        # one x-axis and stay x-linked, so the speed + delta cursors always align in either mode.
        self.x_mode = QComboBox()
        self.x_mode.addItems(["x: distance", "x: time"])
        self.x_mode.currentIndexChanged.connect(self._on_mode_changed)
        bar = QHBoxLayout()
        bar.setContentsMargins(2, 2, 2, 0)
        bar.addWidget(self.x_mode)
        bar.addStretch(1)

        self.glw = pg.GraphicsLayoutWidget()
        self.p_speed = self.glw.addPlot(row=0, col=0)
        self.p_delta = self.glw.addPlot(row=1, col=0)
        self.p_speed.setLabel("left", "speed (km/h)")
        self.p_speed.setLabel("bottom", "distance (m)")
        self.p_speed.showGrid(x=True, y=True, alpha=0.2)
        self.p_speed.addLegend(offset=(8, 8))
        self.p_delta.setLabel("left", "Δ to best (s)")
        self.p_delta.setLabel("bottom", "distance (m)")
        # Sub-second deltas otherwise auto-scale to a "(x0.001)" SI prefix on the axis; keep
        # the left axis in plain seconds so it reads e.g. 0.228 directly.
        self.p_delta.getAxis("left").enableAutoSIPrefix(False)
        self.p_delta.showGrid(x=True, y=True, alpha=0.2)
        # Both plots now share ONE x basis in BOTH modes (distance = s×best_dist; time =
        # time-into-lap), so keep them permanently x-linked — same moment = same x on each, and
        # pan/zoom on one follows the other. (Previously unlinked in time mode.)
        self.p_delta.setXLink(self.p_speed)
        self.p_delta.addLine(y=0, pen=pg.mkPen("#555", width=1))

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
        # plot. The handler does a cheap nearest-index lookup on the cached curve (no re-plot).
        self._hover_xs = None   # x samples of the curve the hover dot snaps to (delta plot)
        self._hover_ys = None   # matching Δ values
        self.hover_dot = pg.ScatterPlotItem(size=9, brush=HOVER_DOT_BRUSH, pen=HOVER_DOT_PEN)
        self.hover_dot.setZValue(20)
        self.hover_dot.setVisible(False)
        self.hover_label = pg.TextItem(color="#ffd166", anchor=(0, 1))
        self.hover_label.setZValue(21)
        self.hover_label.setVisible(False)
        self.p_delta.addItem(self.hover_dot)
        self.p_delta.addItem(self.hover_label)
        self.p_delta.scene().sigMouseMoved.connect(self._on_delta_hover)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.glw)

    def _on_mode_changed(self, index):
        self._time_mode = index == 1
        self.refresh()

    # ----------------------------------------------------------- cursor scrub
    def is_dragging(self) -> bool:
        """True while the user is actively dragging either cursor — app.py uses this to stop the
        playback tick from fighting the drag (it ignores position-driven cursor updates then)."""
        return self._user_dragging

    def _axis_mode(self) -> str:
        """The ONE shared x-axis mode driving both plots: 'time' or 'distance' (the latter is
        the s×best_distance axis the conversion helpers treat identically to 'delta')."""
        return "time" if self._time_mode else "distance"

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

    def place_cursors_at_time(self, t: float):
        """Place BOTH cursors from a media time, even mid-drag (suppressed so it can't re-emit a
        scrub). app calls this during a scrub with the CLAMPED/converted time so the dragged line
        snaps to the lap boundary and the other plot's cursor stays in sync — 'two lines, one
        truth'. Outside a drag, set_cursor_time is the normal entry point."""
        self._place(t)

    def set_laps(self, lap_ids):
        self._lap_ids = list(lap_ids)
        self.refresh()

    def refresh(self):
        for plot, curve in self._curves:
            plot.removeItem(curve)
        self._curves = []
        self._hide_hover()
        self._delta_curves = []  # [(lid, xs, ys)] for the hover-dot nearest-sample snap

        # Both plots share ONE x-axis (distance = s×best_dist, or time-into-lap), kept x-linked
        # in BOTH modes so the two cursors always align. Just relabel the (shared) bottom axis.
        x_mode = "time" if self._time_mode else "distance"
        label = "time (s)" if self._time_mode else "distance (m)"
        self.p_speed.setLabel("bottom", label)
        self.p_delta.setLabel("bottom", label)

        # Hide the cursors before fitting: cur_speed still holds the PREVIOUS mode's x (a
        # distance value when toggling to time mode), and a visible InfiniteLine contributes
        # that stale x to autoRange — stretching the frozen range ~8x. They're re-placed on
        # the new axis basis after the fit (below), so they never contaminate the range.
        self.cur_speed.setVisible(False)
        self.cur_delta.setVisible(False)

        # Re-enable autorange so the new selection's curves are fit before we freeze it again.
        self.p_speed.enableAutoRange()
        self.p_delta.enableAutoRange()

        # One delta() call yields BOTH plots' series on the SAME x basis for `x_mode`, so the
        # speed and delta curves (and the cursors) share one axis and stay x-linked → aligned.
        result = self.session.delta(self._lap_ids, x_mode=x_mode)
        if not result:
            self.p_speed.setTitle(None)
            return
        best, speed, delta = result
        labels = [f"lap {lid} {fmt_time(self.session.laps.lap_time(lid))}"
                  + (" ★best" if lid == best else "") for lid in self._lap_ids]
        self.p_speed.setTitle("   ".join(labels) or None)
        for k, lid in enumerate(self._lap_ids):
            color = PALETTE[k % len(PALETTE)]
            pen = pg.mkPen(color, width=2)
            name = f"lap {lid}" + (" (best)" if lid == best else "")
            if lid in speed:
                sx, spd = speed[lid]
                c = self.p_speed.plot(sx, spd, pen=pen, name=name)
                # x is monotonic (distance or time), so downsampling + clip-to-view is valid and
                # cuts the segments re-rendered on every cursor tick to roughly the visible set.
                c.setDownsampling(auto=True)
                c.setClipToView(True)
                self._curves.append((self.p_speed, c))
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
            self.set_cursor_time(self._cursor_t)

    def set_cursor_time(self, t: float):
        # While the user is dragging, the source of truth is the drag, not playback — ignore
        # position-driven re-placement so the tick can't fight the drag (app also pauses, but
        # any in-flight positionChanged from the seek must not bounce the cursor either).
        if self._user_dragging:
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
        best_d = self.session.best_lap_total_distance()
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
        unit = "s" if self._time_mode else "m"
        self.hover_label.setText(f"lap {lid}  Δ {yi:+.3f} s\n@ {xi:.0f} {unit}")
        self.hover_label.setPos(xi, yi)
        self.hover_dot.setVisible(True)
        self.hover_label.setVisible(True)

    def leaveEvent(self, event):  # noqa: N802 (Qt override)
        # The widget lost the mouse — hide the hover dot (sigMouseMoved may not fire on exit).
        self._hide_hover()
        super().leaveEvent(event)
