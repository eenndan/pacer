"""PlotsView: speed-vs-distance (top) and lap-vs-best delta (bottom), x-linked.

Shows the laps selected in the lap table. A vertical cursor on both plots follows the
video position whenever the currently-playing lap is among those displayed.

The cursor is also a SCRUBBER: it is draggable on both plots, and dragging it seeks the
video within the current lap. This view stays pacer-free — it only emits the raw plot-x and
which axis/plot it came from (`scrubStarted` / `scrubMoved(x, mode)` / `scrubEnded`); app.py
owns session + video and does all conversion, throttled seeking, pause/resume and re-sync.
"""

from __future__ import annotations

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


class PlotsView(QWidget):
    # Cursor-scrub signals. plots_view stays pacer-free: it emits only the raw plot-x and which
    # axis/plot the drag came from; app.py converts to a media time, seeks, and re-syncs.
    scrubStarted = Signal()
    scrubMoved = Signal(float, str)  # (plot_x, mode) — mode in {'time','distance','delta'}
    scrubEnded = Signal()

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._lap_ids: list[int] = []
        self._curves: list[tuple[object, object]] = []
        self._time_mode = False  # speed-plot x-axis: distance (default) vs time-into-lap
        self._cursor_t: float | None = None  # last applied position; re-placed after refresh()
        self._user_dragging = False  # True between grab and release of either cursor
        self._suppress = False  # guard programmatic setValue from re-emitting a scrub

        # x-axis toggle (speed plot only — the delta plot is inherently distance-based).
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

    def _speed_mode(self) -> str:
        return "time" if self._time_mode else "distance"

    def _on_speed_dragged(self, *_):
        self._emit_scrub(self.cur_speed.value(), self._speed_mode())

    def _on_delta_dragged(self, *_):
        self._emit_scrub(self.cur_delta.value(), "delta")

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

        # The speed plot's x is distance or time-into-lap; the delta plot is always distance.
        # x-link only makes sense when both share an axis, so unlink in time mode, relink in
        # distance mode. Set the speed-plot x label to match the active mode.
        self.p_delta.setXLink(self.p_speed if not self._time_mode else None)
        self.p_speed.setLabel("bottom", "time (s)" if self._time_mode else "distance (m)")

        # Hide the cursors before fitting: cur_speed still holds the PREVIOUS mode's x (a
        # distance value when toggling to time mode), and a visible InfiniteLine contributes
        # that stale x to autoRange — stretching the frozen range ~8x. They're re-placed on
        # the new axis basis after the fit (below), so they never contaminate the range.
        self.cur_speed.setVisible(False)
        self.cur_delta.setVisible(False)

        # Re-enable autorange so the new selection's curves are fit before we freeze it again.
        self.p_speed.enableAutoRange()
        self.p_delta.enableAutoRange()

        result = self.session.delta(self._lap_ids)
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
            if self._time_mode:
                # Time mode: speed vs time-into-lap (monotonic x). The delta dict still keys
                # which laps have ≥2 points; reuse it to skip degenerate laps consistently.
                if lid in speed:
                    elapsed, spd = self.session.lap_speed_vs_time(lid)
                    if len(elapsed) >= 2:
                        c = self.p_speed.plot(elapsed, spd, pen=pen, name=name)
                        c.setDownsampling(auto=True)
                        c.setClipToView(True)
                        self._curves.append((self.p_speed, c))
            elif lid in speed:
                dist, spd = speed[lid]
                c = self.p_speed.plot(dist, spd, pen=pen, name=name)
                # Distance x-axis is monotonic, so downsampling + clip-to-view is valid and
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
        """Place each cursor on its OWN axis from the SAME media time t (the single source of
        truth). The speed plot is distance- or (time mode) time-into-lap; the delta plot's x is
        normalized-distance × best_distance, so use the session conversion (NOT raw distance) —
        when the current lap isn't the best lap their totals differ and raw distance would sit
        the cursor off the curve. Guarded by _suppress so a programmatic setValue can never
        masquerade as a user scrub. Caches t so refresh() can re-place after a mode/lap change."""
        self._cursor_t = t
        x_speed = None
        x_delta = None
        best_d = self.session.best_lap_total_distance()
        for lid in self._lap_ids:
            window = self.session.lap_window(lid)
            if window and window[0] <= t <= window[1]:
                x_speed = self.session.plot_x_at_media_time(lid, t, self._speed_mode())
                x_delta = self.session.plot_x_at_media_time(lid, t, "delta", best_distance=best_d)
                break
        self._suppress = True
        try:
            self.cur_speed.setVisible(x_speed is not None)
            self.cur_delta.setVisible(x_delta is not None)
            if x_speed is not None:
                self.cur_speed.setValue(x_speed)
            if x_delta is not None:
                self.cur_delta.setValue(x_delta)
        finally:
            self._suppress = False
