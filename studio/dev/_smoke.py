"""Throwaway headless smoke test: build the full StudioWindow offscreen and exercise the
wiring (default selection, marker/cursor, timing-line re-segment).

Run: python -m studio.dev._smoke [--no-video]

--no-video (equivalently PACER_NO_MEDIA=1): build the window with PlayerPane's inert media
stand-ins instead of the real QMediaPlayer/QtMultimedia pipeline (see the seam in
studio/player_pane.py). For headless CI runners with no media/audio devices, where opening the
real ffmpeg/AVFoundation pipeline blocks indefinitely. Every check below — the full Session
load, the panel construction + wiring, the sidecar write/cleanup — runs identically in both
modes; only the decoder/audio stack is absent."""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if "--no-video" in sys.argv[1:]:
    # The seam is read when PlayerPane is CONSTRUCTED (inside StudioWindow below), so setting it
    # here — before any window exists — is early enough by construction.
    os.environ["PACER_NO_MEDIA"] = "1"

from PySide6.QtWidgets import QApplication, QMessageBox

from studio.app import StudioWindow

app = QApplication([a for a in sys.argv if a != "--no-video"])


# Headless gate: the app's load guard reports failures via a MODAL QMessageBox, which no one
# can dismiss here — on a CI runner it blocks until the step times out (observed: the run hung
# at exactly this dialog when `import pacer` resolved wrongly). Fail fast and loud instead;
# this also turns any future load regression into a red check with the message in the log.
def _fail_fast(_parent, title, text, *args, **kwargs):
    print(f"SMOKE FAILED — modal suppressed: [{title}] {text}", file=sys.stderr)
    sys.exit(1)


QMessageBox.critical = _fail_fast
QMessageBox.warning = _fail_fast

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

print("SMOKE OK" + (" (no-video)" if os.environ.get("PACER_NO_MEDIA") == "1" else ""))
