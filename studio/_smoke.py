"""Throwaway headless smoke test: build the full StudioWindow offscreen and exercise the
wiring (default selection, marker/cursor, timing-line re-segment). Run: python -m studio._smoke"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from studio.app import StudioWindow

app = QApplication(sys.argv)

w = StudioWindow(["3rdparty/gpmf-parser/samples/hero6.mp4"])
s = w.session
print("points:", s.laps.point_count(), "laps:", s.lap_count(),
      "valid:", len(s.valid_lap_ids()), "best:", s.best_lap_id())
assert s.laps.point_count() > 0

# default selection wired plots; map draws a faint best-lap reference line
print("plot curves:", len(w.plots._curves), "best overlay:", w.map._best_overlay is not None)

# video position -> marker + cursor + current-lap overlay (via the ~30 Hz tick)
if len(s.tt):
    w._on_position(float(s.tt[len(s.tt) // 2]))
    w._tick()
    print("marker/cursor OK, current lap:", w.map._current_lap_id)

# timing-line drag re-segments and re-selects without error
w._on_lines(s.start_line, s.sector_lines + [s.suggest_sector()])
print("after add-sector: laps", s.lap_count(), "valid", len(s.valid_lap_ids()))

print("SMOKE OK")
