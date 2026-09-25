"""The overlay export the owner gets, end to end, through the REAL dialog (E8).

WHY IT EXISTS. On 2026-09-25 three export failures reached the owner, and none of the 154 tests
then in the suite saw any of them:
  * a remembered "Overlay only — ProRes 4444 (alpha)" made EVERY export a transparent track with no
    footage and no sound, timed on the camera clock (#410: Contents is no longer remembered);
  * the map's comet tail sat at a SESSION fraction of the lap, a median 241 px from its marker at
    4K, and the marker held three frames and jumped at the GPS rate (#409);
  * a slow 4K ProRes path (#408).
Every unit test held its own piece. Nothing drove the export the way the owner does — his saved
prefs, the dialog accepted unchanged, the worker thread, the progress modal, the finished box — and
then LOOKED at the file that came out. The first defect was caught only by doing exactly that by
hand. This file does it on every run.

HOW. The source is `studio/dev/synth_gopro.py`'s recording (a fictional circuit, known truth, no
personal data) with its opt-in HERO13 tracks: a 59.94 fps picture, a tone as the sound, and a
GoPro-style NDF `tmcd` timecode. The picture is flat grey except for a texture scrolling in the
middle, SCROLL px per source frame, so the corners where the overlay lives carry nothing but the
overlay and the middle says which source frame it is. App-support is jailed and its prefs are
seeded with the owner's stale values (Contents = overlay-only, Resolution = Source, run-up = 5 s);
a real `StudioWindow` holds a real `Session` of the recording, and
`ExportController.export_overlay_video()` runs for real — only the native save panel is patched,
and the dialogs are answered from a timer the way the owner answers them.

WHAT IS CHECKED, on the files themselves:
  * the default export, the dialog accepted unchanged: it opened on "Footage with the overlay burned
    in" (on Source and 5 s, so the stale prefs were in force); the file has a video and a
    non-silent audio stream, both starting at 0, at 30 fps with no timecode, exactly the planned
    clip long, and the clip starts 5 s before the kart truly crossed the line; its middle is the
    SOURCE frame at each frame's media time (normalised correlation with the known texture), the
    lap strip is painted, and the map marker moves on every frame with the amber tail touching it;
  * overlay-only, chosen explicitly: `_overlay_alpha.mov`, ProRes 4444 at 29.97 off the 59.94
    source, a straight (not premultiplied) alpha, no audio, the source's timecode plus the clip's
    start frame embedded, the same marker and tail, and a finished box that says why the picture
    is black and what clock it runs on; and the next dialog opens on the burned-in footage again.

THE MARGINS, measured 2026-09-25 on both encoder paths — VideoToolbox here, and libx264 + prores_ks
with VideoToolbox hidden, as CI has it: the planned source frame correlates 0.99 and every other
one within 7 frames under 0.02, and it is the best within +0.9 of the plan (limit 1.5: the seek
keeps the first frame at or after t0, the fps filter then rounds); the clip starts 6 ms from truth
(limit 25); the marker's neighbourhood changes by at least 0.25 of its p90 on every frame pair but
one (libx264 skipped the last sub-pixel step; H.264 may skip 1 %, ProRes none); the tail starts
more than 16k px out on 1.9 % of frames at worst (limit 5 %); 1,101 alpha pixels are brighter than
their alpha, which premultiplied colour never is (limit 50).

Each check was watched failing on a planted defect: the Contents pref read again (the dialog opens
on overlay-only), the old session-fraction tail (away from its marker on 92 % of burned-in
frames), the marker without interpolation (still across 58 % of burned-in and 67 % of ProRes frame
pairs), an overlay-only render without its timecode.

Run: python tests/test_export_e2e.py   (~23 s on a quiet dev Mac, ~35 s at load 5: two renders of
one 55 s clip at 640x360)
"""
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from fractions import Fraction

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

import numpy as np  # noqa: E402

# Jailed BEFORE any store is read: the prefs are seeded below and the window reads them.
from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("pacer-test-export-e2e-")
from studio import app_support, chapters, prefs  # noqa: E402

assert os.path.realpath(prefs._app_support_dir()) != os.path.realpath(app_support.real_dir()), \
    "the prefs about to be seeded are the owner's own"

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
)

_APP = QApplication.instance() or QApplication([])

from studio import export_video as ev  # noqa: E402
from studio.app import StudioWindow  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.session import Session  # noqa: E402

# ------------------------------------------------------------------------------ the fixture
W, H = 640, 360                 # the synthetic camera's frame: small, so two renders stay cheap
CW, CH = W // 2, 144            # the textured window in the middle of it
TEX_W = 4096                    # the texture strip the window scrolls across
SCROLL = 3                      # texture px per SOURCE frame (59.94 fps)
BG = 0x38                       # the flat grey everywhere else: the overlay's corners
SOURCE_FPS = Fraction(60000, 1001)
SOURCE_TC = "10:51:06:25"       # MK_18_09_26 chapter 1's own timecode, 60 to the TC second
STALE_PREFS = {"export_content_idx": 1, "export_res_idx": 3, "export_lead_idx": 1}
LEAD_S = 5.0                    # what export_lead_idx 1 means
BURNED = "Footage with the overlay burned in"
PRORES = "Overlay only — ProRes 4444 (alpha)"
DEADLINE_MS = 240_000           # a dialog nothing answers fails the test instead of hanging it

_FIX: dict = {}


def _texture() -> np.ndarray:
    """The scrolling strip: 2 px blocks of seeded grey noise, so one source frame of scroll (3 px)
    already decorrelates it — a picture a frame early or late cannot pass for the right one."""
    rng = np.random.default_rng(20260925)
    blocks = rng.integers(40, 216, (CH // 2, TEX_W // 2), dtype=np.uint8)
    return np.kron(blocks, np.ones((2, 2), np.uint8))


def _texture_at(frame: int) -> np.ndarray:
    """The middle of the picture at GLOBAL source frame `frame`, as `_picture` crops it."""
    x = (SCROLL * frame) % (TEX_W - CW)
    return _FIX["tex"][:, x:x + CW]


def _picture(path, truth, first_payload, n_payloads, ffmpeg):
    """`synth_gopro.generate(video=)`: one chapter of the 59.94 fps picture. The crop's `n` counts
    this chapter's frames, so the chapter's first payload puts it back on the global frame count."""
    g0 = first_payload * 60
    vf = (f"crop={CW}:{CH}:x='mod({SCROLL}*(n+{g0}),{TEX_W - CW})':y=0,format=yuv420p,"
          f"pad={W}:{H}:{(W - CW) // 2}:{(H - CH) // 2}:color=0x{BG:02x}{BG:02x}{BG:02x}")
    subprocess.run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1",
                    "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{TEX_W}x{CH}",
                    "-framerate", "60000/1001", "-i", _FIX["tex_path"], "-vf", vf,
                    "-frames:v", str(n_payloads * 60), "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-x264-params", "keyint=120:min-keyint=120:scenecut=0",
                    "-video_track_timescale", "60000", "-an", "-n", path],
                   check=True, capture_output=True)


def _fixture() -> dict:
    """The recording, its Session, and a real window holding it — built once per process."""
    if _FIX:
        return _FIX
    tmp = tempfile.TemporaryDirectory(prefix="pacer-export-e2e-")
    _FIX["tmp"] = tmp
    _FIX["tex"] = _texture()
    _FIX["tex_path"] = os.path.join(tmp.name, "texture.gray")
    _FIX["tex"].tofile(_FIX["tex_path"])
    rec = sg.generate(os.path.join(tmp.name, "rec"), laps=3, chapters=2, video=_picture,
                      frames_per_payload=60, audio=True, timecode=SOURCE_TC)
    paths = chapters.discover_siblings(rec.paths[0])
    assert paths == rec.paths, f"sibling discovery found {paths}, generated {rec.paths}"
    session = Session.load(paths)
    # The owner's MK sessions time on a built-in track; this circuit is unknown, so its start line
    # is the app's own auto-fit — confirmed here the way the map's banner asks, or every export
    # would stop on the provisional-timing question first.
    start, sectors = session.timing_lines_latlon()
    assert session.apply_timing_lines_latlon(start, sectors, confirmed=True), "timing not confirmed"
    for key, value in STALE_PREFS.items():
        prefs.set(key, value)
    win = StudioWindow([])
    win.session = session
    win._paths = list(paths)
    assert not win._share_card_blocked(), "the synthetic session's timing is still provisional"
    out = os.path.join(tmp.name, "out")
    os.makedirs(out)
    _FIX.update(rec=rec, paths=paths, session=session, win=win, out=out)
    return _FIX


# ------------------------------------------------------------------------------ driving the dialog
class _Owner:
    """Answers the export's modal dialogs from a timer, as the owner does: reads the options dialog
    as it OPENED, optionally picks a Contents row, presses Export; lets the progress modal run;
    reads the finished box and presses Done. Anything else modal is recorded and escaped."""

    def __init__(self, contents: str | None = None, press: str = "Export"):
        self.contents = contents
        self.press = press
        self.opened: dict | None = None
        self.hint = ""
        self.boxes: list[str] = []
        self.saves: list[tuple] = []
        self.barked = False
        self._timer = QTimer()
        self._timer.setInterval(20)
        self._timer.timeout.connect(self._tick)

    def _tick(self):
        w = QApplication.activeModalWidget()
        if w is None or not w.isVisible() or isinstance(w, QProgressDialog):
            return
        if isinstance(w, QMessageBox):
            self.boxes.append(w.text())
            done = next((b for b in w.buttons() if b.text() == "Done"), None)
            (done or w.escapeButton() or w.buttons()[0]).click()
            return
        if isinstance(w, QDialog) and self.opened is None:
            form = w.findChild(QFormLayout)
            rows = {}
            for r in range(form.rowCount()):
                label = form.itemAt(r, QFormLayout.ItemRole.LabelRole)
                field = form.itemAt(r, QFormLayout.ItemRole.FieldRole)
                if label is not None and field is not None:
                    rows[label.widget().text()] = field.widget()
            self.opened = {name: combo.currentText() for name, combo in rows.items()}
            if self.contents is not None:
                combo = rows["Contents"]
                combo.setCurrentIndex(combo.findText(self.contents))
            self.hint = " ".join(lab.text() for lab in w.findChildren(QLabel)
                                 if lab.property("role") == "Hint")
            buttons = w.findChild(QDialogButtonBox)
            which = (QDialogButtonBox.StandardButton.Ok if self.press == "Export"
                     else QDialogButtonBox.StandardButton.Cancel)
            buttons.button(which).click()

    def _bark(self):
        self.barked = True
        w = QApplication.activeModalWidget()
        if isinstance(w, QProgressDialog):
            w.cancel()
        elif isinstance(w, QDialog):
            w.reject()
        if QApplication.activeModalWidget() is not None:
            QTimer.singleShot(100, self._bark)

    def export(self, win, out_dir: str) -> str | None:
        """Run File ▸ Export overlay video… to its end; the path written, or None."""
        real_save = QFileDialog.getSaveFileName

        def save_panel(_parent, title, default, filt, *_a, **_k):
            self.saves.append((title, default, filt))
            return os.path.join(out_dir, os.path.basename(default)), filt

        QFileDialog.getSaveFileName = staticmethod(save_panel)
        deadline = QTimer()
        deadline.setSingleShot(True)
        deadline.timeout.connect(self._bark)
        deadline.start(DEADLINE_MS)
        self._timer.start()
        try:
            win.exports.export_overlay_video()
        finally:
            self._timer.stop()
            deadline.stop()
            QFileDialog.getSaveFileName = real_save
        assert not self.barked, (f"the export was still waiting on a dialog after "
                                 f"{DEADLINE_MS // 1000} s; boxes seen: {self.boxes}")
        if not self.saves:
            return None
        return os.path.join(out_dir, os.path.basename(self.saves[-1][1]))


# ------------------------------------------------------------------------------ reading the file
def _probe(path: str) -> dict:
    out = subprocess.run([ev.FFPROBE, "-v", "error", "-show_entries",
                          "format=duration,start_time:stream=index,codec_type,codec_name,pix_fmt,"
                          "width,height,r_frame_rate,start_time,duration,nb_frames:stream_tags=timecode",
                          "-of", "json", path], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _decode(path: str, crop: tuple[int, int, int, int], pix_fmt: str, select: str | None = None):
    """Frames of `path` cropped to (x, y, w, h), as uint8 arrays (n, h, w, channels). Even numbers
    only: ffmpeg rounds a crop of 4:2:0 video to whole chroma samples."""
    x, y, w, h = crop
    assert not (x % 2 or y % 2 or w % 2 or h % 2), crop
    vf = f"crop={w}:{h}:{x}:{y}"
    if select:
        vf = f"select='{select}',{vf}"
    raw = subprocess.run([ev.FFMPEG, "-nostdin", "-v", "error", "-i", path, "-vf", vf,
                          "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", pix_fmt,
                          "pipe:1"], capture_output=True, check=True).stdout
    ch = {"gray": 1, "rgb24": 3, "rgba": 4}[pix_fmt]
    return np.frombuffer(raw, np.uint8).reshape(-1, h, w, ch)


def _tc_frames(tc: str, count: int) -> int:
    h, m, s, f = (int(v) for v in tc.split(":"))
    return ((h * 60 + m) * 60 + s) * count + f


def _map_track(frames: np.ndarray, k: float):
    """Per frame: the marker's centre and the gap from it to its amber tail. After
    `marker_track.py`, the orchestrator's tracker, with two changes this frame size needs:
      * the centre is a coral-WEIGHTED centroid (the core is #FF5A36): the marker is drawn at
        sub-pixel positions and moves a fraction of a pixel per frame here, which a thresholded
        blob cannot see;
      * the tail (#FFD34D) is looked for BEYOND the marker's own disc (7k: core + ring), because
        the glow under the core is amber too, and a 1.6 px amber line through 4:2:0 H.264 needs a
        looser hue test than a ProRes frame does."""
    f = frames[..., :3].astype(np.int32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    coral = np.clip(r - g - 60, 0, None) * (r > 150)
    amber = (r > 170) & (g > 120) & (g < 235) & (b < 150) & (r - b > 90)
    if frames.shape[-1] == 4:
        amber &= frames[..., 3] > 150
    yy, xx = np.mgrid[0:f.shape[1], 0:f.shape[2]]
    weight = coral.sum(axis=(1, 2))
    mx = (coral * xx).sum(axis=(1, 2)) / np.maximum(weight, 1)
    my = (coral * yy).sum(axis=(1, 2)) / np.maximum(weight, 1)
    gap = np.full(len(f), np.inf)
    for i in range(len(f)):
        ty, tx = np.nonzero(amber[i])
        d = np.hypot(tx - mx[i], ty - my[i])
        d = d[d > 7.0 * k]
        if len(d) and weight[i]:
            gap[i] = float(d.min())
    return weight, mx, my, gap


def _still_pairs(frames: np.ndarray, mx, my, half: int = 8) -> tuple[np.ndarray, float]:
    """How much the marker's neighbourhood changes between each pair of consecutive frames (sum of
    |Δ| over a (2*half+1)^2 window at the earlier frame's marker), and the 90th percentile of that
    change — the scale "still" is judged against. NOT the median: a marker held on its GPS samples
    is still on more than half the pairs, which takes the median to zero with it."""
    f = frames[..., :3].astype(np.int32)
    moved = np.empty(len(f) - 1)
    for k in range(len(f) - 1):
        cx, cy = int(round(mx[k])), int(round(my[k]))
        a, b = max(0, cy - half), max(0, cx - half)
        moved[k] = np.abs(f[k + 1, a:cy + half + 1, b:cx + half + 1]
                          - f[k, a:cy + half + 1, b:cx + half + 1]).sum()
    return moved, float(np.percentile(moved, 90))


def _corner() -> tuple[int, int, int, int]:
    """The bottom-right corner clear of the textured middle: the map inset's, and nothing else's."""
    x0, y0 = (W + CW) // 2 + 4, (H + CH) // 2 + 4
    return x0, y0, W - x0, H - y0


def _assert_map_marker(frames: np.ndarray, what: str, still_allowed: float = 0.0) -> None:
    """The marker moves on every frame and the amber tail touches it on (nearly) every frame.

    `still_allowed` is the share of frame pairs the FILE may show still although the painter moved
    the marker: an inter-coded H.264 file can skip a sub-pixel step (libx264 skipped 1 of 1658
    pairs here, the last), while ProRes codes every frame whole and may skip none. A marker held on
    a 10 Hz GPS sample at 30 fps is still on two pairs in three, so the defect is far above either."""
    k = max(0.5, min(W, H) / 1080.0)            # the overlay's size scale (OverlayPainter._k)
    weight, mx, my, gap = _map_track(frames, k)
    assert weight.min() > 0, f"{what}: no map marker in frame {int(np.argmin(weight))}"
    moved, scale = _still_pairs(frames, mx, my)
    still = np.flatnonzero(moved < 0.1 * scale)
    assert len(still) <= still_allowed * len(moved), (
        f"{what}: the map marker stood still across {len(still)} of {len(moved)} frame pairs "
        f"(first at frames {still[:6].tolist()}; change {moved[still[:6]].round().tolist()} vs a "
        f"p90 of {scale:.0f}) — held on a GPS sample instead of interpolated between them")
    # 16k: a pixel or so past the glow. Measured on this clip, frames whose tail starts further
    # out: 1.9 % on libx264, 0.8 % on VideoToolbox; with the old session-fraction tail, 90 %.
    touch = 16.0 * k
    apart = np.flatnonzero(gap > touch)
    assert len(apart) <= 0.05 * len(gap), (
        f"{what}: the amber tail is away from the marker on {len(apart)} of {len(gap)} frames "
        f"(median gap {np.median(gap[apart]):.0f} px, allowed {touch:.1f}; first at "
        f"{apart[:6].tolist()}) — a tail that is not read off the marker's own trace")


# ------------------------------------------------------------------------------ the tests
def test_the_default_export_is_the_footage_with_the_overlay_burned_in():
    fx = _fixture()
    session, win = fx["session"], fx["win"]
    owner = _Owner()
    t = time.perf_counter()
    out = owner.export(win, fx["out"])
    took = time.perf_counter() - t
    assert owner.opened is not None, f"the options dialog never opened; boxes: {owner.boxes}"
    assert owner.opened["Contents"] == BURNED, (
        f"the dialog opened on {owner.opened['Contents']!r} with the owner's stale prefs — a "
        f"remembered overlay-only choice is back (#410); opened on {owner.opened}")
    # ...and the prefs that ARE remembered were in force, or this proved nothing about them.
    assert owner.opened["Resolution"] == "Source (no downscale)", owner.opened
    assert owner.opened["Run-up / run-off"].startswith("5 s"), owner.opened
    assert out and out.endswith("_overlay.mp4"), (out, owner.saves)
    assert os.path.isfile(out), f"no file at {out}; boxes: {owner.boxes}"
    assert len(owner.boxes) == 1 and "exported" in owner.boxes[0], owner.boxes
    assert "black" not in owner.boxes[0], f"a burned-in export explained a black picture: {owner.boxes}"

    info = _probe(out)
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    audio = [s for s in info["streams"] if s["codec_type"] == "audio"]
    assert len(video) == 1 and len(audio) == 1, f"streams: {info['streams']}"
    assert len(info["streams"]) == 2, f"a stream beyond picture and sound: {info['streams']}"
    v, a = video[0], audio[0]
    assert (v["codec_name"], v["width"], v["height"]) == ("h264", W, H), v
    assert v["r_frame_rate"] == "30/1", f"burned-in at {v['r_frame_rate']} off 59.94 footage: {v}"
    for s in info["streams"]:
        assert float(s["start_time"]) == 0.0, f"{s['codec_type']} starts at {s['start_time']}"
        assert "timecode" not in s.get("tags", {}), f"a burned-in file carries a timecode: {s}"

    lap = session.best_lap_id()
    t0, t1 = ev.lap_window_for_export(session, lap, LEAD_S, LEAD_S)
    n = ev.frame_count(t0, t1, 30.0)
    # The window against TRUTH: the true lap (on the app's own start line) plus both leads.
    truth = fx["rec"].truth
    crossed = truth.crossings(session.timing_lines_latlon()[0])
    p = list(session.valid_lap_ids()).index(lap)
    true_lap = float(crossed[p + 1] - crossed[p])
    assert abs(n / 30.0 - (true_lap + 2 * LEAD_S)) < 2 / 30.0, (n / 30.0, true_lap)
    # ...and it starts 5 s before the kart truly crossed the line, on the PICTURE's clock (media
    # time runs MEDIA_PPM fast of true time; the GPS lag is the telemetry's, not the picture's).
    start = crossed[p] * (1.0 + truth.media_ppm * 1e-6) - LEAD_S
    assert abs(t0 - start) < 0.025, (
        f"the clip starts at {t0:.3f} s of footage, {(t0 - start) * 1000:+.0f} ms from 5 s before "
        f"the kart crossed the line ({start:.3f} s)")
    assert int(v["nb_frames"]) == n, f"{v['nb_frames']} frames, the clip is {n}"
    assert abs(float(info["format"]["duration"]) - n / 30.0) < 0.002, (info["format"], n)
    assert abs(float(a["duration"]) - n / 30.0) < 0.03, f"audio {a['duration']} s, clip {n / 30:.3f}"
    pcm = subprocess.run([ev.FFMPEG, "-nostdin", "-v", "error", "-i", out, "-map", "0:a:0", "-ac",
                          "1", "-ar", "48000", "-f", "s16le", "pipe:1"],
                         capture_output=True, check=True).stdout
    pcm = np.frombuffer(pcm, "<i2").astype(float) / 32768.0
    blocks = pcm[: len(pcm) // 48000 * 48000].reshape(-1, 48000)
    rms = np.sqrt((blocks ** 2).mean(axis=1))
    assert len(rms) >= int(n / 30.0) and rms.min() > 0.02, (
        f"the sound is silent in second {int(np.argmin(rms))} (rms {rms.min():.4f}); the source "
        f"tone is ~0.09")

    # THE FOOTAGE: the middle of output frame i is source frame (t0 + i/30) * 59.94.
    x0, y0 = (W - CW) // 2, (H - CH) // 2
    picks = [0, n // 4, n // 2, 3 * n // 4, n - 1]
    mids = _decode(out, (x0 + 8, y0 + 8, CW - 16, CH - 16), "gray",
                   select="+".join(f"eq(n,{i})" for i in picks))
    assert len(mids) == len(picks), f"decoded {len(mids)} of the frames {picks}"
    for i, mid in zip(picks, mids, strict=True):
        want = (t0 + i / 30.0) * float(SOURCE_FPS)
        got = mid[..., 0].astype(float)
        got = (got - got.mean()) / got.std()
        scores = {}
        for f in range(int(want) - 6, int(want) + 8):
            ref = _texture_at(f)[8:-8, 8:-8].astype(float)
            ref = (ref - ref.mean()) / ref.std()
            scores[f] = float((got * ref).mean())
        best = max(scores, key=scores.get)
        assert scores[best] > 0.6, (
            f"frame {i}: the middle of the picture matches no source frame near {want:.1f} (best "
            f"correlation {scores[best]:.2f}) — the footage is not in the file")
        # 1.5: the accurate seek keeps the first frame AT OR AFTER t0 (up to one late), and the
        # fps filter then takes the nearest 59.94 frame to each 30 fps tick (half a frame either way).
        assert abs(best - want) <= 1.5, (
            f"frame {i} shows source frame {best}, planned {want:.1f}: the footage is "
            f"{(best - want) / float(SOURCE_FPS) * 1000:+.0f} ms off its overlay")

    # THE OVERLAY: the lap strip — its pill and its white type — over the flat top-left corner.
    top_left = _decode(out, (0, 0, W // 2, (H - CH) // 2 - 4), "rgb24", select="eq(n,0)")[0]
    pill = int((np.abs(top_left.astype(int) - BG).max(axis=-1) > 24).sum())
    white = int((top_left.min(axis=-1) > 200).sum())
    assert pill > 400 and white > 20, (
        f"no lap strip in the top-left corner ({pill} px off the grey, {white} white)")
    _assert_map_marker(_decode(out, _corner(), "rgb24"), "burned-in", still_allowed=0.01)
    fx["burned"] = {"path": out, "t0": t0, "frames": n, "took": took}


def test_overlay_only_chosen_in_the_dialog_is_a_track_an_editor_can_line_up():
    fx = _fixture()
    win = fx["win"]
    owner = _Owner(contents=PRORES)
    t = time.perf_counter()
    out = owner.export(win, fx["out"])
    took = time.perf_counter() - t
    assert owner.opened is not None and owner.opened["Contents"] == BURNED, (
        f"the second export's dialog opened on {owner.opened} — Contents came back remembered")
    assert "black" in owner.hint and "camera" in owner.hint, f"the hint: {owner.hint!r}"
    assert out and out.endswith("_overlay_alpha.mov"), (out, owner.saves)
    assert os.path.isfile(out), f"no file at {out}; boxes: {owner.boxes}"

    info = _probe(out)
    video = [s for s in info["streams"] if s["codec_type"] == "video"]
    assert len(video) == 1, info["streams"]
    assert not [s for s in info["streams"] if s["codec_type"] == "audio"], (
        f"an overlay-only track carries sound: {info['streams']}")
    v = video[0]
    assert v["codec_name"] == "prores" and v["pix_fmt"].startswith("yuva"), v
    assert (v["width"], v["height"]) == (W, H), v
    assert v["r_frame_rate"] == "30000/1001", (
        f"overlay-only at {v['r_frame_rate']} over 60000/1001 footage drifts a frame every 33 s")

    # THE TIMECODE: the source chapter's own, plus the source frame the overlay starts on.
    tc = v.get("tags", {}).get("timecode")
    assert tc, f"no timecode in the overlay-only file: {info['streams']}"
    session = fx["session"]
    t0 = fx.get("burned", {}).get("t0")
    if t0 is None:
        t0 = ev.lap_window_for_export(session, session.best_lap_id(), LEAD_S, LEAD_S)[0]
    chapter = session.chapters.chapters[session.chapters.chapter_at(t0)]
    src_tc = _probe(chapter.path)["streams"][0]["tags"]["timecode"]
    j0 = math.floor((t0 - chapter.offset) * SOURCE_FPS + 1e-6)   # the source frame at t0
    j = 2 * _tc_frames(tc, 30) - _tc_frames(src_tc, 60)        # ...and the one the tc names
    assert j in (j0, j0 - 1), (
        f"the overlay's timecode {tc} names source frame {j} of {os.path.basename(chapter.path)} "
        f"(which starts at {src_tc}); the clip starts at source frame {j0}")
    # THE ALPHA IS STRAIGHT: premultiplied colour can never exceed its alpha, straight colour can.
    rgba = _decode(out, (0, 0, W, H), "rgba", select="eq(n,0)+eq(n,300)")
    alpha = rgba[..., 3].astype(int)
    over = (alpha > 16) & (alpha < 200) & (rgba[..., :3].max(axis=-1).astype(int) > alpha + 40)
    assert (alpha < 8).mean() > 0.5 and (alpha > 247).any(), "not a transparent overlay track"
    assert over.sum() > 50, f"only {int(over.sum())} px brighter than their alpha: premultiplied"
    _assert_map_marker(_decode(out, _corner(), "rgba"), "overlay-only")

    # THE FINISHED BOX: what the file is, where it starts, and the row for a video to watch.
    assert len(owner.boxes) == 1, owner.boxes
    box = owner.boxes[0]
    assert "black" in box and "timecode" in box and BURNED in box, box
    assert tc in box and os.path.basename(chapter.path) in box, (tc, box)
    # ...and the next export opens on the footage again, the stale pref neither read nor rewritten.
    again = _Owner(press="Cancel")
    assert again.export(win, fx["out"]) is None
    assert again.opened is not None and again.opened["Contents"] == BURNED, again.opened
    assert prefs.get("export_content_idx") == STALE_PREFS["export_content_idx"]
    fx["alpha"] = {"path": out, "took": took}


def _run_all():
    t_all = time.time()
    for fn in (test_the_default_export_is_the_footage_with_the_overlay_burned_in,
               test_overlay_only_chosen_in_the_dialog_is_a_track_an_editor_can_line_up):
        t = time.time()
        fn()
        print(f"ok {fn.__name__} ({time.time() - t:.1f} s)")
    print(f"test_export_e2e: all ok ({time.time() - t_all:.1f} s)")


if __name__ == "__main__":
    _run_all()
