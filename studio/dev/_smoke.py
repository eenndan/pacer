"""Throwaway headless smoke test: build the full StudioWindow offscreen and exercise the
wiring (default selection, marker/cursor, timing-line re-segment). Run: python -m studio.dev._smoke"""
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

# default selection wired plots; map draws a faint best-lap reference line (measured +
# inferred gap-fill segments) — exercise the gap-fill segment build for the best lap.
print("plot curves:", len(w.plots._curves), "best overlay lap:", w.map._best_overlay.lap_id)
if s.best_lap_id() is not None:
    segs = s.lap_trace_segments(s.best_lap_id())
    n_inf = sum(1 for sg in segs if not sg.measured)
    print(f"best-lap segments: {len(segs)} ({n_inf} inferred)")

# video position -> marker + cursor + current-lap overlay (via the ~30 Hz tick)
if len(s.tt):
    w._on_position(float(s.tt[len(s.tt) // 2]))
    w._tick()
    print("marker/cursor OK, current lap:", w.map._current_overlay.lap_id)

# timing-line drag re-segments and re-selects without error; the user edit also writes the
# timing-line sidecar next to the sample clip — assert it appeared, then remove it so the
# submodule working tree stays clean (the smoke run must leave no artifacts).
w._on_lines(s.start_line, s.sector_lines + [s.suggest_sector()])
print("after add-sector: laps", s.lap_count(), "valid", len(s.valid_lap_ids()))
assert w._sidecar_path and os.path.exists(w._sidecar_path), "sidecar not written on user edit"
os.remove(w._sidecar_path)

print("SMOKE OK")
