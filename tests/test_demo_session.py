"""`pixi run studio -- --demo` with no footage of your own (board review UX-1, CEO-2, R8).

Until this file, an evaluator who cloned the repo could not see a single lap: the bundled clip has
none and `--demo` downloaded an asset that was never uploaded. The demo is now SYNTHETIC — generated,
not filmed (`studio/dev/make_demo.py`) — and it must open as the product at its best: laps, VERIFIED
timing, a ranked Coaching page. Network-free; everything the app resolves lands in this test's jail.

  1. THE CODE STILL MAKES THE PUBLISHED DEMO: the demo's true lap times are the ones `demo-data-v1`
     was generated with (`make_demo.PUBLISHED_LAP_MS`), timed at the built-in line.
  2. THE BUILT-IN TRACK IS THE DEMO'S CIRCUIT: `track_db`'s "Synthetic demo circuit" is exactly
     `make_demo.track_entry()`, and the test recording `synth_gopro` writes at its own ORIGIN stays
     an UNKNOWN track (it exists to exercise that path).
  3. `--demo` OPENS IT VERIFIED: the demo — its telemetry under the flat placeholder picture, since
     nothing here decodes a frame — is cached where `--demo` caches it; `resolve_demo_recording`
     finds it offline and the real `StudioWindow` opens it offscreen: >= 7 valid laps, timing
     verified on the built-in circuit, no provisional banner or "unknown track" notice, a ranked
     Coaching row, and a window title that says the recording is synthetic.
  4. THE PICTURE: two payloads render to exactly 60 frames at 29.97 fps with no B-frames (the frame
     contract `synth_gopro` builds the video trak on), and the label is on the frame in its amber.

Run: python tests/test_demo_session.py   (~10 s; the pixi env's ffmpeg writes the video traks)
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import numpy as np  # noqa: E402

from studio import app_support, demo, track_db  # noqa: E402
from studio.dev import make_demo as md  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402

MIN_LAPS = 7          # the board's success measure (R8): >= 7 valid laps
SEED_TOL_DEG = 2e-7   # the entry is rounded to 1e-7 deg (~1 cm); allow one ulp of that each way


def _ffmpeg(tool: str = "ffmpeg") -> str:
    """The pixi env's ffmpeg/ffprobe, found the way synth_gopro finds ffmpeg."""
    found = shutil.which(tool) or shutil.which(tool, path=os.path.join(sys.prefix, "bin"))
    assert found, f"{tool} not found (the pixi env provides it)"
    return found


def _seed_entry() -> dict:
    hit = [e for e in track_db.SEED if e["name"] == md.DEMO_TRACK_NAME]
    assert len(hit) == 1, f"{md.DEMO_TRACK_NAME!r} is not ONE built-in track: {hit}"
    return hit[0]


def test_the_code_still_makes_the_published_demo():
    truth = md.simulate()
    got = tuple(int(round(t * 1000)) for t in truth.lap_times(_seed_entry()["start"]))
    assert got == md.PUBLISHED_LAP_MS, (
        "the generator no longer makes the demo that was published (demo-data-v1): re-publish it "
        f"under a new tag and re-pin studio/demo.py, or keep the change off the demo.\n  now "
        f"{got}\n  published {md.PUBLISHED_LAP_MS}")
    assert len(got) >= MIN_LAPS


def test_the_built_in_track_is_the_demo_circuit():
    seed, want = _seed_entry(), md.track_entry()
    for key in ("centroid", "bbox", "start"):
        a, b = np.ravel(seed[key]), np.ravel(want[key])
        assert a.shape == b.shape and np.abs(a - b).max() <= SEED_TOL_DEG, (key, seed[key], want[key])
    assert seed["sectors"] == [] and track_db.is_builtin(md.DEMO_TRACK_NAME)
    with tempfile.TemporaryDirectory() as d:
        db = os.path.join(d, "tracks.json")                       # never written: a first-ever run
        hit = track_db.detect(*seed["centroid"], db)
        assert hit is not None and hit["name"] == md.DEMO_TRACK_NAME, hit
        # The B1a test recording sits at synth_gopro.ORIGIN and must stay an unknown circuit.
        c = sg.build_circuit()
        lat, lon = sg.to_latlon(c.x, c.y, sg.ORIGIN)
        anchor = ((lat.min() + lat.max()) / 2, (lon.min() + lon.max()) / 2)
        assert track_db.detect(*anchor, db) is None, "the demo circuit swallowed synth_gopro.ORIGIN"
        assert not os.path.exists(db)


class _Window:
    """The real StudioWindow on `paths`, offscreen, with any modal failing the test instead of
    blocking it forever (a test that can only fail by hanging is a weak one)."""

    def __init__(self, paths):
        from PySide6.QtWidgets import QApplication, QMessageBox

        from studio.app import StudioWindow

        self.app = QApplication.instance() or QApplication([sys.argv[0]])

        def fail(*args, **kwargs):
            raise AssertionError(f"a modal dialog opened on the demo: {args[1:3]}")

        QMessageBox.critical = QMessageBox.warning = QMessageBox.information = fail
        QMessageBox.exec = lambda box, *a: fail(None, box.windowTitle(), box.text())
        self.win = StudioWindow(paths)
        deadline = time.time() + 60.0
        while self.win.view is None and time.time() < deadline:
            self.app.processEvents()
            time.sleep(0.01)
        assert self.win.view is not None, "the demo did not finish loading within 60 s"
        self.app.processEvents()

    def close(self):
        self.win.close()
        self.app.processEvents()


def test_demo_opens_verified_in_the_real_window():
    cache = demo.demo_cache_path()
    assert not os.path.realpath(cache).startswith(os.path.realpath(app_support.real_dir())), \
        f"the demo cache resolved into the owner's app-support: {cache}"
    os.environ.pop("PACER_DEMO_MP4", None)
    assert demo.resolve_demo_recording(allow_download=False) is None, "a demo was already cached"
    truth = md.make(cache, render=False)
    path = demo.resolve_demo_recording(allow_download=False)
    assert path == cache, (path, cache)
    assert demo.demo_available(), "a cached demo must offer the welcome screen's Open demo button"

    w = _Window([path])
    try:
        s = w.win.session
        valid = s.valid_lap_ids()
        print(f"  {len(valid)} valid laps on {s.track_name!r}, verified={s.timing_verified}, "
              f"clock {s.timing_quality.clock}")
        assert s.timing_verified, f"the demo opened with PROVISIONAL timing (track {s.track_name!r})"
        assert s.track_name == md.DEMO_TRACK_NAME, f"the demo circuit was not detected: {s.track_name}"
        assert len(valid) >= MIN_LAPS, f"{len(valid)} valid laps, the board asked for >= {MIN_LAPS}"
        assert len(valid) == truth.laps, (len(valid), truth.laps)
        assert w.win.view.provisional_banner.isHidden(), "the provisional banner is on screen"
        notice = w.win._session_notice() or ""
        assert "unknown track" not in notice, notice
        ranked = s.coaching_opportunities().ranked_rows()
        print(f"  coaching: {[(r.cid, round(r.time_lost, 3)) for r in ranked]}")
        assert ranked, "the demo's Coaching page ranks nothing"
        assert "synthetic" in w.win.windowTitle(), w.win.windowTitle()
    finally:
        w.close()


def test_the_picture_keeps_the_frame_contract_and_says_synthetic():
    truth = md.simulate()
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "clip.mp4")
        md.render_video(clip, truth, 100, 2, _ffmpeg())
        probe = json.loads(subprocess.run(
            [_ffmpeg("ffprobe"), "-v", "error", "-count_frames", "-select_streams", "v:0",
             "-show_entries", "stream=nb_read_frames,r_frame_rate,has_b_frames,width,height",
             "-of", "json", clip], capture_output=True, text=True, check=True).stdout)["streams"][0]
        assert (int(probe["nb_read_frames"]), probe["r_frame_rate"]) == \
            (2 * sg.FRAMES_PER_PAYLOAD, "30000/1001"), probe
        assert int(probe["has_b_frames"]) == 0, probe
        raw = subprocess.run([_ffmpeg(), "-v", "error", "-i", clip, "-frames:v", "1", "-f", "rawvideo",
                              "-pix_fmt", "rgb24", "-"], capture_output=True, check=True).stdout
    frame = np.frombuffer(raw, np.uint8).reshape(md.H, md.W, 3).astype(int)
    title = frame[40:84, 40:520]                                    # "SYNTHETIC DEMO SESSION"
    amber = (title[..., 0] > 200) & (title[..., 1] > 130) & (title[..., 2] < 90)
    assert amber.sum() > 2000, f"the SYNTHETIC label is not on the frame ({amber.sum()} amber px)"


def _run_all():
    for fn in (test_the_code_still_makes_the_published_demo,
               test_the_built_in_track_is_the_demo_circuit,
               test_demo_opens_verified_in_the_real_window,
               test_the_picture_keeps_the_frame_contract_and_says_synthetic):
        t0 = time.time()
        fn()
        print(f"ok {fn.__name__} ({time.time() - t0:.1f} s)")


if __name__ == "__main__":
    _run_all()
