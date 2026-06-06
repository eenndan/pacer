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
from PySide6.QtWidgets import QApplication, QMainWindow, QSplitter

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

        left = QSplitter(Qt.Vertical)
        left.addWidget(self.video)
        left.addWidget(self.table)
        left.setSizes([460, 360])

        right = QSplitter(Qt.Vertical)
        right.addWidget(self.map)
        right.addWidget(self.plots)
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

        self._select_default()

    def _select_default(self):
        """Pre-select the two fastest laps so speed + a real delta-to-best show on launch."""
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
            self.video.seek(self.session.laps.start_timestamp(min(ids)))

    def _on_position(self, t: float):
        # Runs in the video event path — keep it trivial so frame presentation isn't starved.
        self._latest_t = t

    def _tick(self):
        # Steady ~30 Hz: apply an update only when the position actually advanced.
        if self._latest_t != self._applied_t:
            self._applied_t = self._latest_t
            self._apply_position(self._applied_t)

    def _apply_position(self, t: float):
        self.map.set_marker_time(t)
        self.plots.set_cursor_time(t)

        lap_id = self.session.lap_at_time(t)  # F3: which lap is on the video
        self.table.set_current_lap(lap_id)
        self.map.set_current_lap(lap_id)  # highlight the current lap's trace on the map
        sp = self.session.speed_at_time(t)  # F2: time / speed / lap readout
        speed = f"{sp:.1f}" if sp is not None else "-"
        lap = lap_id if lap_id is not None else "-"
        self.video.set_readout(f"t = {fmt_time(t)}   speed = {speed} km/h   lap {lap}")

    def _on_lines(self, start, sectors):
        self.session.set_timing_lines(start, sectors)
        self.table.refresh()
        self._select_default()


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    interpolate = "--interp" in argv  # off by default; the C++ fit diverges on long sessions
    paths = [a for a in argv if not a.startswith("-")] or [DEFAULT_SAMPLE]
    app = QApplication(sys.argv)
    window = StudioWindow(paths, interpolate=interpolate)
    window.show()
    return app.exec()
