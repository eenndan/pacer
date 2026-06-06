"""PlotsView: speed-vs-distance (top) and lap-vs-best delta (bottom), x-linked.

Shows the laps selected in the lap table. A vertical cursor on both plots follows the
video position whenever the currently-playing lap is among those displayed.
"""

from __future__ import annotations

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QVBoxLayout, QWidget

from .session import fmt_time

# Antialiased path rendering is a major per-repaint cost; the cursor's InfiniteLine.setValue
# re-renders every visible curve each ~30 Hz tick, so keep it OFF for smooth playback.
pg.setConfigOptions(antialias=False)

PALETTE = ["#39a0ed", "#ef476f", "#ffd166", "#06d6a0", "#b388eb", "#ff924c", "#118ab2"]
CURSOR_PEN = pg.mkPen("#ffffff", width=1, style=Qt.DashLine)


class PlotsView(QWidget):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self._lap_ids: list[int] = []
        self._curves: list[tuple[object, object]] = []
        self._time_mode = False  # speed-plot x-axis: distance (default) vs time-into-lap
        self._cursor_t: float | None = None  # last applied position; re-placed after refresh()

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

        self.cur_speed = pg.InfiniteLine(angle=90, movable=False, pen=CURSOR_PEN)
        self.cur_delta = pg.InfiniteLine(angle=90, movable=False, pen=CURSOR_PEN)
        self.cur_speed.setVisible(False)
        self.cur_delta.setVisible(False)
        self.p_speed.addItem(self.cur_speed)
        self.p_delta.addItem(self.cur_delta)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.glw)

    def _on_mode_changed(self, index):
        self._time_mode = index == 1
        self.refresh()

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
        self._cursor_t = t
        # The delta plot is always distance-based; the speed plot is distance OR, in time mode,
        # time-into-lap (= t - the lap's start timestamp). Find the playing lap once, then place
        # each cursor on its own axis.
        dist = None
        elapsed = None
        for lid in self._lap_ids:
            window = self.session.lap_window(lid)
            if window and window[0] <= t <= window[1]:
                dist = self.session.distance_in_lap_at_time(lid, t)
                elapsed = t - window[0]  # window[0] is the lap's start_timestamp
                break
        x_speed = elapsed if self._time_mode else dist
        self.cur_speed.setVisible(x_speed is not None)
        self.cur_delta.setVisible(dist is not None)
        if x_speed is not None:
            self.cur_speed.setValue(x_speed)
        if dist is not None:
            self.cur_delta.setValue(dist)
