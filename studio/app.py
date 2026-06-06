"""StudioWindow: assembles the panels and wires the cross-panel sync.

Layout (resizable splitters):
    ┌──────────────┬───────────────────────────┐
    │  VideoView   │   MapView (track + lines) │
    ├──────────────┼───────────────────────────┤
    │  LapTable    │   PlotsView (speed/delta) │
    └──────────────┴───────────────────────────┘
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QLabel,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .lap_table import LapTable
from .map_view import MapView
from .plots_view import PlotsView
from .session import DEFAULT_SAMPLE, Session, fmt_time
from .video_view import VideoView


class StudioWindow(QMainWindow):
    def __init__(self, paths: list[str], interpolate: bool = False):
        super().__init__()
        self.setWindowTitle("pacer studio")
        self.resize(1340, 840)

        print("studio: loading telemetry…", flush=True)
        self.session = Session.load(paths, interpolate=interpolate)
        print(f"studio: {self.session.laps.point_count()} points, "
              f"{self.session.lap_count()} laps.", flush=True)

        self.video = VideoView(self.session.video_path)
        self.map = MapView(self.session)
        self.plots = PlotsView(self.session)
        self.table = LapTable(self.session)

        # Always-on Δ/speed readout box for the CURRENT playback/scrub moment (Δ-to-best is the
        # priority). Owned here (values come from session); placed ABOVE the plots so it never
        # overlaps the curves. plots_view stays pacer-free — it knows nothing about this box.
        self.diff_box = QLabel("Δ —    — km/h")
        self.diff_box.setAlignment(Qt.AlignCenter)
        self.diff_box.setStyleSheet(
            "QLabel { background:#1b1b1b; color:#e6e6e6; font-size:18px; font-weight:600;"
            " padding:6px; border-bottom:1px solid #333; }"
        )

        left = QSplitter(Qt.Vertical)
        left.addWidget(self.video)
        left.addWidget(self.table)
        left.setSizes([460, 360])

        plots_panel = QWidget()
        plots_lay = QVBoxLayout(plots_panel)
        plots_lay.setContentsMargins(0, 0, 0, 0)
        plots_lay.setSpacing(0)
        plots_lay.addWidget(self.diff_box)
        plots_lay.addWidget(self.plots, 1)

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.map)
        right.addWidget(plots_panel)
        right.setSizes([460, 380])

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setSizes([520, 820])
        self.setCentralWidget(main)

        # --- cross-panel wiring ---
        # positionChanged fires in the video decode/present path; it must do almost nothing
        # (just record the latest time). A steady ~30 Hz timer applies the map/plot/readout
        # update off that path, so heavy repaints never starve frame presentation.
        self._latest_t = 0.0
        self._applied_t: float | None = None
        self.video.positionChanged.connect(self._on_position)
        self._tick_timer = QTimer(self)
        self._tick_timer.setInterval(33)  # ~30 Hz
        self._tick_timer.timeout.connect(self._tick)
        self._tick_timer.start()
        self.map.seek_requested.connect(self.video.seek)
        self.map.timing_lines_changed.connect(self._on_lines)
        self.table.laps_selected.connect(self._on_user_select)

        # --- plot cursor scrub (a fine, lap-scoped scrubber; the full-video slider stays) ---
        # Dragging either plot cursor seeks the video WITHIN the current lap. plots_view emits
        # the raw plot-x + which axis it came from; we convert (session, pacer-side) to a media
        # time, clamp it to the lap, throttle the seek to ≤1 per tick, pause while dragging and
        # resume iff it was playing. See _on_scrub_*.
        self._scrub_lap: int | None = None      # the lap captured at grab; the drag is scoped to it
        self._scrub_target: float | None = None  # latest requested media time (coalesced)
        self._scrub_pending = False              # a new target awaits the next tick's seek
        self._scrub_was_playing = False          # restore playback on release iff it was playing
        self.plots.scrubStarted.connect(self._on_scrub_started)
        self.plots.scrubMoved.connect(self._on_scrub_moved)
        self.plots.scrubEnded.connect(self._on_scrub_ended)
        # Auto-follow: the charts always show whichever lap the playhead is in (current vs best).
        # When the playhead crosses into a NEW lap (playing OR scrubbing), the speed + delta
        # charts switch to that lap, keeping the best lap as the reference overlay. We key off
        # the playhead's lap, so a single O(1) edge check per tick (in _apply_readout) drives it;
        # we only re-select on the actual lap CHANGE so it never thrashes. _followed_lap is the
        # lap the charts are currently following; seeded from _select_default below.
        self._followed_lap: int | None = None
        # F2: keep the sector boundary guide lines on the charts in sync. plots_view stays
        # pacer-free, so app computes the boundary x-positions via session for the current
        # axis mode and pushes them; recompute when the mode flips (the positions' units change).
        self.plots.modeChanged.connect(self._refresh_sector_lines)

        self._select_default()
        self._refresh_sector_lines()  # draw any sectors present on launch (none by default)

    def _select_default(self):
        """Pre-select the two fastest laps so speed + a real delta-to-best show on launch.

        Also clears the auto-follow state: on launch nothing is "current" yet, and after a
        re-segmentation (_on_lines) the lap ids have shifted, so the next playhead movement must
        be free to re-establish the follow on the now-current lap (a stale id would suppress the
        edge). This multi-lap default overlay is simply replaced once the playhead enters a lap."""
        self._followed_lap = None
        rows = sorted(self.session.lap_rows(), key=lambda r: r["time"])
        ids = [r["idx"] for r in rows[:2]]
        self.table.select(ids)
        self._on_laps_selected(ids)

    def _on_user_select(self, ids):
        # A genuine user click in the lap table also jumps the video to that lap (F1).
        self._on_laps_selected(ids, seek=True)

    def _on_laps_selected(self, ids, seek=False):
        # The table multi-selection drives the PLOTS only; the map's current-lap overlay
        # follows the video position (and thus selection, since F1 seeks into the lap).
        self.plots.set_laps(ids)
        # F1 seeks ONLY on user selection — not on programmatic re-select from
        # _select_default()/_on_lines(), or dragging a timing line would yank the video.
        if seek and ids:
            target = self.session.laps.start_timestamp(min(ids))
            self.video.seek(target)
            # Don't let the auto-follow collapse a just-made (possibly multi-lap) comparison the
            # instant the seek's positionChanged lands: seed _followed_lap to the lap the seek
            # resolves into, so the immediate post-seek tick is NOT an edge. The user keeps the
            # selected overlay while paused; once PLAYBACK MOVES ON into a different lap, the edge
            # fires and the charts collapse to [current, best] (the locked behaviour).
            self._followed_lap = self.session.lap_at_time(target)

    def _on_position(self, t: float):
        # Runs in the video event path — keep it trivial so frame presentation isn't starved.
        self._latest_t = t

    def _tick(self):
        # Steady ~30 Hz. While the user is scrubbing a plot cursor, the source of truth is the
        # drag, not playback: issue at most ONE coalesced seek per tick to the latest dragged
        # target, and DON'T apply the (stale / seek-driven) playback position — that gating is
        # what prevents the drag↔positionChanged feedback loop from oscillating.
        if self._scrub_target is not None:
            if self._scrub_pending:
                self._scrub_pending = False
                self.video.seek(self._scrub_target)
            return
        # Normal playback: apply an update only when the position actually advanced.
        if self._latest_t != self._applied_t:
            self._applied_t = self._latest_t
            self._apply_position(self._applied_t)

    def _apply_position(self, t: float):
        self.map.set_marker_time(t)
        self.plots.set_cursor_time(t)
        self._apply_readout(t)

    def _apply_readout(self, t: float):
        lap_id = self.session.lap_at_time(t)  # F3: which lap is on the video
        self._follow_current_lap(lap_id, t)  # charts auto-follow the playhead's lap (vs best)
        self.table.set_current_lap(lap_id)
        self.map.set_current_lap(lap_id)  # highlight the current lap's trace on the map
        sp = self.session.speed_at_time(t)  # F2: time / speed / lap readout
        speed = f"{sp:.1f}" if sp is not None else "-"
        lap = lap_id if lap_id is not None else "-"
        self.video.set_readout(f"t = {fmt_time(t)}   speed = {speed} km/h   lap {lap}")
        self._update_diff_box(t, sp)

    def _follow_current_lap(self, lap_id: int | None, t: float):
        """Auto-follow the playhead's lap on the speed + delta charts (current lap vs best).

        On a lap-change EDGE — `lap_id` is a valid lap that differs from the one the charts are
        currently following — switch the charts to show [current lap, best] and update the lap
        table highlight/selection to match, so the table ▶/selection, the map overlay and the
        plots all agree on the current lap. Only acts on the actual edge (O(1) check per tick, a
        refresh only on the change) so it never thrashes, and it works while PLAYING or SCRUBBING
        (we key off the playhead's lap, not the play state).

        Graceful edges:
          * `lap_id is None` (lead-in / between laps / cool-down) → HOLD the last followed lap;
            never blank the charts.
          * The table selection is updated via the programmatic `select()` path (signals blocked),
            so it does NOT emit `laps_selected` and therefore cannot trigger a user-seek that would
            fight playback. (Select-lap→seek is gated to genuine user clicks via _on_user_select.)
        """
        if lap_id is None or lap_id == self._followed_lap:
            return  # hold on no-lap regions; only act on a genuine change to a new valid lap
        self._followed_lap = lap_id
        # Keep the best lap as the reference overlay; current lap first so it's the primary curve.
        best = self.session.best_lap_id()
        ids = [lap_id] if best is None or best == lap_id else [lap_id, best]
        self.table.select(ids)   # programmatic (signals blocked) → no seek, won't fight playback
        self.plots.set_laps(ids)
        # During a scrub-across-boundary, set_laps→refresh re-places the cursor via set_cursor_time
        # which is a no-op mid-drag; re-place it from the dragged time so the cursor stays put in
        # the now-current lap (resolving the old "scrub dead off the displayed lap" caveat too).
        if self.plots.is_dragging():
            self.plots.place_cursors_at_time(t)

    def _update_diff_box(self, t: float, sp: float | None):
        """Refresh the always-on Δ/speed box for the current moment (priority: Δ-to-best in
        seconds). Δ comes from session.delta_at_time (same normalized-distance alignment as the
        delta plot, so the box and the cursor on the curve agree). Outside a valid lap Δ is —."""
        d = self.session.delta_at_time(t)
        if d is None:
            delta_txt = "Δ —"
        else:
            # +behind / −ahead vs best, at the same track position.
            delta_txt = f"Δ {d:+.2f} s"
        speed_txt = f"{sp:.0f} km/h" if sp is not None else "— km/h"
        # Colour cue: green when up on best (ahead), red when down (behind).
        colour = "#e6e6e6" if d is None else ("#06d6a0" if d <= 0 else "#ef476f")
        self.diff_box.setText(f"{delta_txt}     {speed_txt}")
        self.diff_box.setStyleSheet(
            f"QLabel {{ background:#1b1b1b; color:{colour}; font-size:18px; font-weight:600;"
            " padding:6px; border-bottom:1px solid #333; }"
        )

    # ------------------------------------------------------------- plot scrub
    def _on_scrub_started(self):
        """Grab: scope the scrub to the lap the playhead is currently in; pause playback,
        remembering whether it was playing so we can resume on release."""
        self._scrub_lap = self.session.lap_at_time(self._applied_t or 0.0)
        self._scrub_was_playing = self.video.is_playing()
        if self._scrub_was_playing:
            self.video.pause()
        self._scrub_target = None
        self._scrub_pending = False

    def _on_scrub_moved(self, x: float, mode: str):
        """Drag: convert the raw plot-x (in `mode`'s axis) to a media time within the captured
        current lap, clamped to that lap. Store it as the latest target (the tick coalesces the
        actual seek to ≤1/tick) and immediately re-place BOTH cursors + the map marker + the
        readout from that single clamped time, so the line snaps to the lap boundary and every
        view stays in sync without waiting on the (throttled, async) seek."""
        lap = self._scrub_lap
        if lap is None:  # not inside a valid lap (lead-in / between laps) — no-op
            return
        best_d = self.session.best_lap_total_distance()
        t = self.session.media_time_at_plot_x(lap, x, mode, best_distance=best_d)
        if t is None:
            return
        self._scrub_target = t
        self._scrub_pending = True
        # Drive every view from the one clamped truth (plots ignore the playback tick mid-drag).
        self.plots.place_cursors_at_time(t)
        self.map.set_marker_time(t)
        self._apply_readout(t)

    def _on_scrub_ended(self):
        """Release: issue a final seek to the last target (so the frame matches the cursor even
        if the last move coalesced out), resume playback iff it was playing at grab, then clear
        the scrub state so the normal playback→cursor sync resumes."""
        target = self._scrub_target
        self._scrub_target = None
        self._scrub_pending = False
        if target is not None:
            self.video.seek(target)
            self._applied_t = target  # keep current-lap/readout consistent until the seek lands
        if self._scrub_was_playing:
            self.video.play()
        self._scrub_was_playing = False
        self._scrub_lap = None

    def _refresh_sector_lines(self, mode: str | None = None):
        """F2: push the sector boundary positions (start/finish + each sector line) to the charts
        for the current axis mode. Computed via session (the s×best_distance / time-into-lap
        axis), so plots_view stays pacer-free. Called on launch, after a sector edit, and when
        the dist/time mode flips (positions' units change)."""
        mode = mode or self.plots.axis_mode()
        self.plots.set_sector_lines(self.session.sector_plot_positions(mode))

    def _on_lines(self, start, sectors):
        self.session.set_timing_lines(start, sectors)
        self.table.refresh()
        # Re-segmentation shifted lap ids + cleared the per-lap gap-fill cache; redraw the map
        # overlays so their measured/inferred segments match the new segmentation.
        self.map.refresh_overlays()
        self._select_default()
        # F2: the sector lines changed — update the chart guide lines live.
        self._refresh_sector_lines()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    interpolate = "--interp" in argv  # off by default; the C++ fit diverges on long sessions
    paths = [a for a in argv if not a.startswith("-")] or [DEFAULT_SAMPLE]
    app = QApplication(sys.argv)
    window = StudioWindow(paths, interpolate=interpolate)
    window.show()
    return app.exec()
