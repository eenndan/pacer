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
import shutil
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if "--no-video" in sys.argv[1:]:
    # The seam is read when PlayerPane is CONSTRUCTED (inside StudioWindow below), so setting it
    # here — before any window exists — is early enough by construction.
    os.environ["PACER_NO_MEDIA"] = "1"

from PySide6.QtWidgets import QApplication, QMessageBox

from studio import library
from studio.app import StudioWindow

# F8: the load now upserts the recording into the session-library index (~/Library/Application
# Support/pacer/library.json). The smoke run "must leave no artifacts" and must never touch the
# user's real library, so divert the index to a throwaway temp dir BEFORE any window is built
# (the upsert reads this seam at load time). We assert the entry appeared there, then drop it.
_LIB_DIR = tempfile.mkdtemp(prefix="pacer-smoke-lib-")
library._app_support_dir = lambda: _LIB_DIR

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
print("plot curves:", len(w.view.plots._curves), "best overlay lap:", w.view.map._best_overlay.lap_id)
if s.best_lap_id() is not None:
    segs = s.lap_trace_segments(s.best_lap_id())
    n_inf = sum(1 for sg in segs if not sg.measured)
    print(f"best-lap segments: {len(segs)} ({n_inf} inferred)")

# video position -> marker + cursor + current-lap overlay (via the ~30 Hz tick)
if len(s.tt):
    w.view._on_position(float(s.tt[len(s.tt) // 2]))
    w._tick()
    print("marker/cursor OK, current lap:", w.view.map._current_overlay.lap_id)

# timing-line drag re-segments and re-selects without error; the user edit also writes the
# timing-line sidecar next to the sample clip — assert it appeared, then remove it so the
# submodule working tree stays clean (the smoke run must leave no artifacts).
w.view._on_lines(s.start_line, s.sector_lines + [s.suggest_sector()])
print("after add-sector: laps", s.lap_count(), "valid", len(s.valid_lap_ids()))
assert w._sidecar_path and os.path.exists(w._sidecar_path), "sidecar not written on user edit"
os.remove(w._sidecar_path)

# F8: _update_library deliberately does NOT index the bundled DEFAULT_SAMPLE, nor any open with
# no valid laps — that guard is what stops a junk row (0 laps, null track) from being persisted
# and then surfaced by the library dialog forever. This smoke run opens exactly that sample (0
# valid laps), so assert the (temp-diverted) index stayed empty, then drop the temp dir so the
# run leaves no artifacts.
_lib = library.load()
assert len(s.valid_lap_ids()) == 0, "smoke fixture changed: DEFAULT_SAMPLE now has valid laps"
assert len(_lib["entries"]) == 0, f"library: sample/0-lap open must be skipped, got {len(_lib['entries'])}"
print("library entries:", len(_lib["entries"]), "(sample correctly not indexed)")
shutil.rmtree(_LIB_DIR, ignore_errors=True)

print("SMOKE OK" + (" (no-video)" if os.environ.get("PACER_NO_MEDIA") == "1" else ""))
