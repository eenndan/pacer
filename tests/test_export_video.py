"""Tests for studio.export_video (F9 offline video-overlay export) — the PURE-LOGIC parts that
need neither ffmpeg nor a media file:

  * the lap-window TRIM math (lap_window_for_export, frame_times, output_size);
  * the per-frame OVERLAY-VALUE lookup (overlay_values_at) against a synthetic Session — it must
    read the SAME accessors the live readout does (index_at_time->tv, lap_at_time+delta_at_lap,
    g_at_time), so a burned-in frame matches the app;
  * the ffmpeg COMMAND construction (build_decode_cmd / build_encode_cmd) — argv shape, the
    seek/trim window, scale+fps, the rawvideo-in / h264+aac-out mux mapping;
  * the Renderer drive loop + teardown with the subprocess + ffprobe MOCKED (no real ffmpeg):
    the decode->paint->encode pump, the progress callback, and cooperative cancellation.

A single real-render smoke test is GATED behind `export_video.ffmpeg_available()` AND the presence
of the D24 media file, so CI without ffmpeg/the file still passes (the test is skipped, not failed).

Headless offscreen Qt (the painter builds a QImage + a headless g-meter dial); fast; no network.

Run: python tests/test_export_video.py
"""
import os
import subprocess
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import export_video as ev  # noqa: E402

REAL_MP4 = "/Users/daniil/Desktop/D24/GX010060.MP4"


# --------------------------------------------------------------------------- a synthetic Session
class StubSession:
    """A minimal duck-typed Session for the per-frame value lookup + trim math: ONE lap whose
    window is [t0, t0+dur), a per-sample (tt, tv) speed track, a straight trace (tx, ty), and a
    constant g. Implements exactly the accessors export_video reads — no pacer, no Qt-heavy
    Session machinery — so overlay_values_at can be checked deterministically."""

    def __init__(self, lap_id=2, t0=100.0, dur=60.0, n=600, g=(0.3, -0.4, 0.5),
                 has_g=True):
        self._lap = lap_id
        self._t0 = t0
        self._t1 = t0 + dur
        self.tt = t0 + np.linspace(0.0, dur, n)
        self.tv = 40.0 + 30.0 * np.sin(np.linspace(0.0, np.pi, n))   # km/h, 40..70..40
        self.tx = np.linspace(0.0, 100.0, n)                          # straight trace in metres
        self.ty = np.zeros(n)
        self._g = g
        self.has_gmeter = has_g
        # the per-fraction Δ baseline: this lap vs itself is 0 everywhere; expose a couple of
        # canned deltas keyed by an exact t for the lookup test.
        self._delta = {}

    # accessors overlay_values_at / the strip / the map use
    def lap_at_time(self, t):
        return self._lap if self._t0 <= t < self._t1 else None

    def index_at_time(self, t):
        if len(self.tt) == 0:
            return None
        i = int(np.searchsorted(self.tt, t))
        return min(max(i, 0), len(self.tt) - 1)

    def lap_window(self, lap_id):
        return (self._t0, self._t1) if lap_id == self._lap else None

    def delta_at_lap(self, lap_id, t):
        if lap_id != self._lap:
            return None
        return self._delta.get(round(t, 6), 0.0)

    def g_at_time(self, t):
        return self._g if (self.has_gmeter and self._t0 <= t < self._t1) else None

    def gmeter_source(self):
        return "accl"

    def _lap_trace_xyt(self, lap_id):
        if lap_id != self._lap:
            return None
        return self.tx, self.ty, self.tt


# --------------------------------------------------------------------------- trim / frame math
def test_lap_window_for_export_matches_lap_window():
    """The export window is exactly Session.lap_window (the same half-open [t0, t1) lap_at_time
    resolves), and None / a degenerate window are rejected."""
    s = StubSession(lap_id=2, t0=100.0, dur=60.0)
    assert ev.lap_window_for_export(s, 2) == (100.0, 160.0)
    assert ev.lap_window_for_export(s, 7) is None        # no such lap

    class Degenerate:
        def lap_window(self, _):
            return (5.0, 5.0)                            # zero-length
    assert ev.lap_window_for_export(Degenerate(), 0) is None


def test_frame_times_count_and_spacing():
    """ffmpeg emits ceil(dur*fps) rawvideo frames from t0 spaced 1/fps; frame_times mirrors that
    so the i-th composited frame is stamped with the time it was decoded from."""
    ft = ev.frame_times(10.0, 11.0, 60.0)
    assert len(ft) == 60
    assert ft[0] == 10.0
    assert np.isclose(ft[1] - ft[0], 1 / 60.0)
    # a non-integer number of frames rounds UP (the tail partial frame exists)
    ft2 = ev.frame_times(0.0, 1.005, 60.0)
    assert len(ft2) == 61                                 # ceil(60.3) = 61
    # fps must be positive
    try:
        ev.frame_times(0.0, 1.0, 0.0)
        raise AssertionError("expected ValueError for fps<=0")
    except ValueError:
        pass


def test_output_size_aspect_and_even():
    """Height is the controlling dim; width follows the source aspect; both are forced even
    (yuv420p), and the output never upscales past the source height."""
    assert ev.output_size(3840, 2160, ev.OverlayConfig(out_height=1080)) == (1920, 1080)
    assert ev.output_size(3840, 2160, ev.OverlayConfig(out_height=720)) == (1280, 720)
    # odd-aspect source -> width rounded to even
    w, h = ev.output_size(1921, 1081, ev.OverlayConfig(out_height=540))
    assert w % 2 == 0 and h % 2 == 0
    # never upscales: a 720p source asked for 1080 stays 720
    assert ev.output_size(1280, 720, ev.OverlayConfig(out_height=1080)) == (1280, 720)


# --------------------------------------------------------------------------- per-frame values
def test_overlay_values_match_accessors():
    """overlay_values_at must read the SAME values the live readout shows: index_at_time->tv for
    speed, lap_at_time for the lap, delta_at_lap for Δ, g_at_time for the dot."""
    s = StubSession(lap_id=2, t0=100.0, dur=60.0)
    t = 130.0
    v = ev.overlay_values_at(s, t)
    i = s.index_at_time(t)
    assert v.lap_id == 2
    assert v.marker_index == i
    assert v.speed_kmh == float(s.tv[i])
    assert v.delta_s == 0.0
    assert v.g == s._g


def test_overlay_values_outside_lap_blank():
    """Outside the lap window: lap None, Δ None, g None (lead-in / between laps), but the marker
    index + speed still resolve to the nearest clamped sample (matches the live behaviour)."""
    s = StubSession(lap_id=2, t0=100.0, dur=60.0)
    v = ev.overlay_values_at(s, 99.0)                    # before the lap
    assert v.lap_id is None
    assert v.delta_s is None
    assert v.g is None
    assert v.marker_index is not None                    # clamped to sample 0
    assert v.speed_kmh is not None


def test_overlay_values_no_gmeter_session():
    """A session without a g signal (has_gmeter False) yields g=None — overlay_values_at must not
    call g_at_time when there's no meter (mirrors the app's gate)."""
    s = StubSession(has_g=False)
    v = ev.overlay_values_at(s, s._t0 + 5.0)
    assert v.g is None


def test_overlay_values_uses_delta_at_lap():
    """The Δ shown is delta_at_lap(lap, t) — seed a non-zero Δ and confirm it propagates."""
    s = StubSession(lap_id=2, t0=100.0, dur=60.0)
    s._delta[round(120.0, 6)] = -0.37                    # 0.37 s ahead of best at that instant
    v = ev.overlay_values_at(s, 120.0)
    assert v.delta_s == -0.37


# --------------------------------------------------------------------------- ffmpeg commands
def _spec(**kw):
    d = dict(src_path="/in/src.MP4", out_path="/out/clip.mp4", lap_id=3, t0=100.0, t1=170.0)
    d.update(kw)
    return ev.ExportSpec(**d)


def test_decode_cmd_shape():
    """Decode argv: pre-input -ss t0, -i src, -t duration, scale=WxH + fps filter, rgb24 rawvideo
    to pipe:1, audio/subs/data dropped."""
    cmd = ev.build_decode_cmd(_spec(), 1920, 1080, 59.94)
    assert cmd[0] == ev.FFMPEG
    # pre-input seek (fast) is BEFORE -i
    assert cmd.index("-ss") < cmd.index("-i")
    assert "/in/src.MP4" in cmd
    assert any(a == "scale=1920:1080,fps=59.940000" for a in cmd)
    assert cmd[cmd.index("-t") + 1] == "70.000000"       # duration = t1 - t0
    assert "-an" in cmd and "rawvideo" in cmd and "rgb24" in cmd
    assert cmd[-1] == "pipe:1"


def test_encode_cmd_shape_and_mux():
    """Encode argv: input 0 is rgb24 rawvideo on pipe:0 (declared size+rate); input 1 is the
    source seek-trimmed for audio; map our video + the source audio; h264 + aac out."""
    cmd = ev.build_encode_cmd(_spec(), 1920, 1080, 59.94)
    assert cmd[0] == ev.FFMPEG
    assert "pipe:0" in cmd
    assert cmd[cmd.index("-s") + 1] == "1920x1080"
    # two inputs: the pipe and the source
    assert cmd.count("-i") == 2
    assert "/in/src.MP4" in cmd
    assert "-map" in cmd and "0:v:0" in cmd and "1:a:0?" in cmd
    assert "libx264" in cmd and "aac" in cmd
    assert "yuv420p" in cmd and "+faststart" in cmd
    assert cmd[-1] == "/out/clip.mp4"


def test_encode_window_matches_decode_window():
    """A/V sync hinge: the encode's source-audio -ss/-t window equals the decode's video window
    (same t0 + duration), so audio and the composited video cover the identical lap span."""
    spec = _spec(t0=12.5, t1=80.0)
    dec = ev.build_decode_cmd(spec, 640, 360, 30.0)
    enc = ev.build_encode_cmd(spec, 640, 360, 30.0)
    # the LAST -ss in each is the source seek; both -t are the duration
    assert dec[dec.index("-ss") + 1] == f"{12.5:.6f}"
    assert enc[enc.index("-ss") + 1] == f"{12.5:.6f}"
    assert dec[dec.index("-t") + 1] == f"{67.5:.6f}"
    assert enc[enc.index("-t") + 1] == f"{67.5:.6f}"


# ----------------------------------------------------------- encoder selection / GPU offload (F9)
def test_encode_cmd_videotoolbox_uses_hw_codec_and_bitrate():
    """With the VideoToolbox encoder the encode argv carries `h264_videotoolbox` + a bitrate
    target (it's bitrate-driven, no CRF) + yuv420p/+faststart; libx264 (the fallback) stays
    CRF-driven."""
    vt = ev.build_encode_cmd(_spec(), 1920, 1080, 30.0, encoder=ev.VT_H264)
    assert "h264_videotoolbox" in vt and "libx264" not in vt
    assert "-b:v" in vt                                   # bitrate target, not -crf
    assert "-crf" not in vt
    assert "yuv420p" in vt and "+faststart" in vt
    assert vt[-1] == "/out/clip.mp4"
    sw = ev.build_encode_cmd(_spec(), 1920, 1080, 30.0, encoder=ev.SW_H264)
    assert "libx264" in sw and "-crf" in sw and "h264_videotoolbox" not in sw


def test_vt_target_bitrate_scales_and_floors():
    """The VideoToolbox target bitrate scales with pixels*fps (bits-per-pixel) and never drops
    below the floor (so a tiny test size still encodes cleanly)."""
    big = ev.vt_target_bitrate(1920, 1080, 60.0)
    small = ev.vt_target_bitrate(1920, 1080, 30.0)
    assert big > small                                    # more fps -> more bitrate
    assert ev.vt_target_bitrate(64, 64, 2.0) == ev._MIN_VT_BITRATE   # floored


def test_decode_cmd_hwaccel_placement():
    """`-hwaccel videotoolbox` (hardware decode) is inserted BEFORE the input so ffmpeg decodes the
    source on the media engine; without it the decode argv is unchanged."""
    hw = ev.build_decode_cmd(_spec(), 1920, 1080, 30.0, hwaccel=True)
    assert "-hwaccel" in hw and hw[hw.index("-hwaccel") + 1] == "videotoolbox"
    assert hw.index("-hwaccel") < hw.index("-i")          # before the input
    sw = ev.build_decode_cmd(_spec(), 1920, 1080, 30.0, hwaccel=False)
    assert "-hwaccel" not in sw


def test_resolve_encoder_choices(monkeypatch_restore):
    """resolve_encoder maps the choice to a concrete -c:v: explicit libx264 always SW; an explicit
    GPU/vt request uses VT when COMPILED IN; auto uses VT only when a real session opens."""
    ev.videotoolbox_encoder_available = lambda: True      # type: ignore[assignment]
    ev.videotoolbox_usable = lambda: True                 # type: ignore[assignment]
    assert ev.resolve_encoder("libx264") == ev.SW_H264
    assert ev.resolve_encoder("cpu") == ev.SW_H264
    assert ev.resolve_encoder("videotoolbox") == ev.VT_H264
    assert ev.resolve_encoder("gpu") == ev.VT_H264
    assert ev.resolve_encoder("auto") == ev.VT_H264
    # auto falls back to libx264 when no real VT session opens, even if the encoder is compiled in
    ev.videotoolbox_usable = lambda: False                # type: ignore[assignment]
    assert ev.resolve_encoder("auto") == ev.SW_H264
    # an explicit gpu request with NO VT compiled in also degrades to libx264 (never errors)
    ev.videotoolbox_encoder_available = lambda: False     # type: ignore[assignment]
    assert ev.resolve_encoder("gpu") == ev.SW_H264


def test_resolve_hwaccel_decode_auto_pairs_with_vt_encoder(monkeypatch_restore):
    """hwaccel-decode "auto" turns ON only when the VT encoder is used AND the hwaccel is available
    (so decode+encode both run on the media engine); explicit True/False force it."""
    ev.videotoolbox_decode_available = lambda: True       # type: ignore[assignment]
    assert ev.resolve_hwaccel_decode("auto", ev.VT_H264) is True
    assert ev.resolve_hwaccel_decode("auto", ev.SW_H264) is False   # SW encoder -> no auto hwdec
    assert ev.resolve_hwaccel_decode(True, ev.SW_H264) is True      # forced on
    assert ev.resolve_hwaccel_decode(False, ev.VT_H264) is False    # forced off
    # forcing on when the hwaccel isn't available degrades to software decode (never errors)
    ev.videotoolbox_decode_available = lambda: False      # type: ignore[assignment]
    assert ev.resolve_hwaccel_decode(True, ev.VT_H264) is False
    assert ev.resolve_hwaccel_decode("auto", ev.VT_H264) is False


def test_resolve_fps_cap_and_explicit():
    """resolve_fps: an explicit fps wins; otherwise the source rate capped by fps_cap (so 59.94 ->
    30 by default); never exceeds the source; a non-positive source falls back to the cap."""
    Cfg = ev.OverlayConfig
    assert ev.resolve_fps(Cfg(fps=24.0, fps_cap=30.0), 59.94) == 24.0       # explicit wins
    assert ev.resolve_fps(Cfg(fps=None, fps_cap=30.0), 59.94) == 30.0       # capped
    assert ev.resolve_fps(Cfg(fps=None, fps_cap=None), 59.94) == 59.94      # uncapped -> source
    assert ev.resolve_fps(Cfg(fps=None, fps_cap=30.0), 24.0) == 24.0        # source below cap kept
    assert ev.resolve_fps(Cfg(fps=None, fps_cap=30.0), 0.0) == 30.0         # bad source -> cap


def test_default_config_offloads_to_gpu_and_caps_fps():
    """The DEFAULT export config opts into the GPU offload + the 30 fps cap (the new fast defaults
    the brief asked for) — a regression guard so the defaults don't silently revert."""
    cfg = ev.OverlayConfig()
    assert cfg.encoder == "auto"
    assert cfg.hwaccel_decode == "auto"
    assert cfg.fps_cap == 30.0


# --------------------------------------------------------------------------- mocked render loop
class _FakeProc:
    """A stand-in subprocess.Popen: the decoder serves `nframes` of zeroed rgb24 bytes then EOF;
    the encoder swallows everything written to its stdin. communicate() returns ("", "")."""

    def __init__(self, frame_bytes=0, nframes=0, is_decoder=False):
        self.returncode = 0
        self.stdout = None
        self.stdin = None
        self.killed = False
        self._is_decoder = is_decoder
        if is_decoder:
            self.stdout = types.SimpleNamespace(
                _left=nframes, _fb=frame_bytes,
                read=self._read, close=lambda: None)
        else:
            self.written = bytearray()
            self.stdin = types.SimpleNamespace(
                write=lambda b: self.written.extend(b),
                close=lambda: None, flush=lambda: None)

    def _read(self, n):
        so = self.stdout
        if so._left <= 0:
            return b""
        so._left -= 1
        return bytes(so._fb)

    def communicate(self, *a, **k):
        return (b"", b"")

    def wait(self, *a, **k):
        return 0

    def kill(self):
        self.killed = True


def _patch_pipeline(monkeypatch_targets, frame_bytes, nframes):
    """Install fake decode/encode Popen + a fake probe so a Renderer runs with no real ffmpeg.
    Returns the dict so the caller can inspect the encoder's captured bytes."""
    state = {}

    def fake_popen(cmd, **kw):
        # the decode cmd ends with pipe:1, the encode cmd starts reading pipe:0
        is_decoder = cmd[-1] == "pipe:1"
        proc = _FakeProc(frame_bytes=frame_bytes, nframes=nframes, is_decoder=is_decoder)
        state["decoder" if is_decoder else "encoder"] = proc
        return proc

    ev.subprocess.Popen = fake_popen                      # type: ignore[assignment]
    ev.probe_video_size = lambda _p: (3840, 2160, 60.0)   # type: ignore[assignment]
    # Never shell out to real ffmpeg for the encoder probe in a mocked render — keep these tests
    # ffmpeg-free + deterministic regardless of which encoder the spec asked for.
    ev.resolve_encoder = lambda _choice: ev.SW_H264       # type: ignore[assignment]
    return state


def test_renderer_pumps_frames_and_reports_progress(monkeypatch_restore):
    """The Renderer reads one frame's bytes per loop, paints it, writes it to the encoder, and
    reports progress — all with ffmpeg mocked. The encoder must receive exactly
    nframes * frame_bytes bytes. Pinned to the single-threaded pump (workers=1) + libx264 +
    fps_cap off so the count is deterministic and no real ffmpeg is touched."""
    s = StubSession(lap_id=2, t0=0.0, dur=1.0, n=200)
    cfg = ev.OverlayConfig(out_height=120, fps_cap=None, encoder="libx264", workers=1)
    out_w, out_h = ev.output_size(3840, 2160, cfg)
    fb = out_w * out_h * 3
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/out.mp4", lap_id=2, t0=0.0, t1=1.0,
                         config=cfg)
    # at 60 fps over 1.0 s -> 60 frames expected; serve exactly that many
    state = _patch_pipeline(None, fb, nframes=60)
    r = ev.Renderer(s, spec)
    assert r.total_frames == 60
    seen = []
    res = r.run(progress=lambda d, t: seen.append((d, t)))
    assert res.frames == 60
    # exactly nframes * frame_bytes written (packed; no stride padding leaked) ...
    assert len(state["encoder"].written) == fb * 60
    # ... and the frames are not all-zero: the overlays WERE painted onto the (zeroed) input.
    assert any(state["encoder"].written), "overlay pixels should be painted onto the frame"
    assert seen and seen[-1][0] == 60                       # final progress hit the total


def test_renderer_cancel_raises_and_kills(monkeypatch_restore):
    """A cancel() that returns True mid-render raises CancelledError; both fake procs are killed
    (cooperative teardown)."""
    s = StubSession(lap_id=2, t0=0.0, dur=2.0, n=200)
    cfg = ev.OverlayConfig(out_height=120, fps_cap=None, encoder="libx264", workers=1)
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/out.mp4", lap_id=2, t0=0.0, t1=2.0,
                         config=cfg)
    out_w, out_h = ev.output_size(3840, 2160, cfg)
    fb = out_w * out_h * 3
    state = _patch_pipeline(None, fb, nframes=200)
    r = ev.Renderer(s, spec)
    calls = {"n": 0}

    def cancel():
        calls["n"] += 1
        return calls["n"] >= 1                              # cancel after the first chunk
    try:
        r.run(cancel=cancel, chunk=4)
        raise AssertionError("expected CancelledError")
    except ev.CancelledError:
        pass
    assert r._done is True
    # cooperative teardown killed both ffmpeg processes (the fakes record .kill()).
    assert state["decoder"].killed and state["encoder"].killed


def test_render_lap_rejects_unusable_lap(monkeypatch_restore):
    """render_lap raises ValueError when the lap has no usable window (before touching ffmpeg)."""
    s = StubSession(lap_id=2, t0=0.0, dur=1.0)
    try:
        ev.render_lap(s, "/in.MP4", "/out.mp4", lap_id=99)
        raise AssertionError("expected ValueError")
    except ValueError:
        pass


# --------------------------------------------------------------------------- gated real render
def test_real_render_smoke_if_ffmpeg_and_media():
    """End-to-end on the real D24 media — GATED: skipped (not failed) unless ffmpeg/ffprobe AND
    the media file are present, so CI without them still passes. Renders a SHORT 2 s window of the
    best lap at 360p and asserts the output is a non-empty valid file with a couple of frames."""
    if not ev.ffmpeg_available() or not os.path.exists(REAL_MP4):
        print("skip real_render_smoke (no ffmpeg or media)")
        return
    from studio.session import Session
    s = Session.load([REAL_MP4])
    best = s.best_lap_id()
    t0, _ = s.lap_window(best)
    out = os.path.join(os.environ.get("TMPDIR", "/tmp"), "f9_unit_smoke.mp4")
    spec = ev.ExportSpec(src_path=REAL_MP4, out_path=out, lap_id=best, t0=t0, t1=t0 + 2.0,
                         config=ev.OverlayConfig(out_height=360))
    res = ev.Renderer(s, spec).run()
    assert res.frames > 30                                   # ~120 at 60 fps
    assert os.path.getsize(out) > 0
    w, h, _ = ev.probe_video_size(out)
    assert h == 360
    os.remove(out)
    print("real_render_smoke OK")


# ------------------------------------------------ stderr-drain unit (no ffmpeg, runs everywhere)
def test_stderr_drainer_drains_large_output_and_keeps_tail():
    """REGRESSION (deadlock guard): the _StderrDrainer must keep reading a stderr stream no matter
    how much it emits — far past an OS pipe's ~64 KB — so an ffmpeg that gets chatty can never block
    on write(stderr) while the render loop is busy on the stdout/stdin pipes. Feeds a stream that
    serves WAY more than a pipe buffer and asserts (a) it all drained without blocking and (b) only
    a bounded TAIL is retained (for error reporting). Uses a real OS pipe, no ffmpeg."""
    import threading as _th
    r_fd, w_fd = os.pipe()
    total = 512 * 1024  # 512 KB — 8x a typical 64 KB pipe buffer; would deadlock a non-draining read
    payload = (b"ffmpeg noise line %05d\n" % 0).ljust(64) * (total // 64)

    drainer = ev._StderrDrainer(os.fdopen(r_fd, "rb"), tail_bytes=4096)

    def feed():
        with os.fdopen(w_fd, "wb") as w:
            w.write(payload)            # blocks unless the drainer is actively reading -> proves it
    t = _th.Thread(target=feed)
    t.start()
    t.join(timeout=10)
    assert not t.is_alive(), "writer blocked -> stderr was NOT being drained (deadlock!)"
    drainer.join(timeout=5)
    tail = drainer.tail()
    assert 0 < len(tail) <= 4096, f"tail must be bounded, got {len(tail)} bytes"
    assert tail == payload[-len(tail):], "tail must be the END of the stream (last bytes kept)"


# --------------------------------- real tiny synthetic render (no media file; gated on ffmpeg only)
def test_real_synthetic_pipe_render_if_ffmpeg(monkeypatch_restore):
    """REGRESSION (real pipe path, NO mocks): build a tiny 1.5 s synthetic clip with ffmpeg, then
    run the REAL Renderer over it — real decode pipe → real QPainter composite → real encode pipe →
    real stderr drain. This is the test the mocked suite can't be: a pipe/stderr/threading deadlock
    or a short-read bug HANGS here (the runner's outer time budget catches it) instead of passing.
    Gated on ffmpeg_available() so CI without ffmpeg skips it; needs NO 11 GB media file.

    It also guards the PERF root cause indirectly: the map inset's static art is baked once and
    blitted, so even this little render returns promptly rather than re-rasterizing the trace per
    frame."""
    if not ev.ffmpeg_available():
        print("skip real_synthetic_pipe_render (no ffmpeg)")
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    src = os.path.join(tmp, "f9_syn_src.mp4")
    out = os.path.join(tmp, "f9_syn_out.mp4")
    for p in (src, out):
        if os.path.exists(p):
            os.remove(p)
    # a 1.5 s, 640x360, 30 fps test pattern WITH an audio tone (so the 1:a:0? audio map exercises
    # too); -loglevel error keeps it quiet, matching production.
    subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=1.5",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=1.5",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src],
        check=True, capture_output=True)
    assert os.path.getsize(src) > 0

    # A synthetic Session whose lap window spans the whole clip [0, 1.5).
    s = StubSession(lap_id=1, t0=0.0, dur=1.5, n=90)
    spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=1.5,
                         config=ev.OverlayConfig(out_height=360))
    res = ev.Renderer(s, spec).run()
    # the real pipeline completed (didn't deadlock) and wrote real frames
    assert res.frames >= 40, f"expected ~45 frames, got {res.frames}"
    assert os.path.getsize(out) > 0
    w, h, _ = ev.probe_video_size(out)
    assert h == 360 and w == 640
    # the output is a real, decodable H.264 stream: ffprobe reports its codec + frame count.
    info = subprocess.run(
        [ev.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=codec_name,nb_read_frames", "-count_frames",
         "-of", "default=noprint_wrappers=1:nokey=1", out],
        check=True, capture_output=True, text=True).stdout.split()
    assert info and info[0] == "h264", f"expected h264, got {info}"
    for p in (src, out):
        os.remove(p)
    print("real_synthetic_pipe_render OK")


def _make_syn_clip(path, dur=1.5):
    """Build a tiny test clip (video + audio tone) at `path`. Shared by the GPU/fallback tests."""
    if os.path.exists(path):
        os.remove(path)
    subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=640x360:rate=30:duration={dur}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", path],
        check=True, capture_output=True)
    assert os.path.getsize(path) > 0


def _stream_encoder_tag(path):
    """The VIDEO stream's `encoder` tag (e.g. 'Lavc61.19.101 h264_videotoolbox' / '... libx264') —
    this is where the concrete encoder name lands (the format/muxer tag is just libavformat)."""
    return subprocess.run(
        [ev.FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream_tags=encoder",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True).stdout.strip()


def test_real_videotoolbox_render_if_available(monkeypatch_restore):
    """END-TO-END GPU offload: if a VideoToolbox H.264 session actually opens on this machine, a
    render forced to the VT encoder must produce a valid H.264 file whose stream encoder tag names
    h264_videotoolbox (proof the Apple media engine, not libx264, produced it). Skipped (not
    failed) where VT isn't usable or ffmpeg is absent — so CI without the hardware still passes."""
    if not ev.ffmpeg_available() or not ev.videotoolbox_usable():
        print("skip real_videotoolbox_render (no ffmpeg or no VT session)")
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    src, out = os.path.join(tmp, "f9_vt_src.mp4"), os.path.join(tmp, "f9_vt_out.mp4")
    _make_syn_clip(src)
    s = StubSession(lap_id=1, t0=0.0, dur=1.5, n=90)
    # Force the VT encoder + software decode (keep the probe surface small); fps uncapped so the
    # frame count is the full clip.
    spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=1.5,
                         config=ev.OverlayConfig(out_height=360, encoder="videotoolbox",
                                                 hwaccel_decode=False, fps_cap=None))
    r = ev.Renderer(s, spec)
    assert r.encoder == ev.VT_H264                          # selected the GPU encoder
    res = r.run()
    assert res.frames >= 40 and os.path.getsize(out) > 0
    tag = _stream_encoder_tag(out)
    assert "videotoolbox" in tag.lower(), f"expected a VideoToolbox stream encoder tag, got {tag!r}"
    for p in (src, out):
        os.remove(p)
    print(f"real_videotoolbox_render OK (encoder tag: {tag})")


def test_real_fallback_to_libx264_if_ffmpeg(monkeypatch_restore):
    """ROBUSTNESS: a VideoToolbox encode that fails at runtime must transparently fall back to
    libx264 so the export never breaks. We FORCE VT selection, then break its codec args so the VT
    encode exits non-zero; the render must retry on libx264 and still produce a valid H.264 file
    (encoder tag = libx264). Gated on ffmpeg; needs no hardware (VT is made to fail on purpose)."""
    if not ev.ffmpeg_available():
        print("skip real_fallback_to_libx264 (no ffmpeg)")
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    src, out = os.path.join(tmp, "f9_fb_src.mp4"), os.path.join(tmp, "f9_fb_out.mp4")
    _make_syn_clip(src)
    # Force VT for the first attempt but honour the retry's libx264 choice; keep decode in software.
    ev.resolve_encoder = lambda c: (                        # type: ignore[assignment]
        ev.VT_H264 if str(c).lower() in ("videotoolbox", "auto") else ev.SW_H264)
    ev.videotoolbox_decode_available = lambda: False        # type: ignore[assignment]
    _orig_codec = ev._video_codec_args

    def broken_vt(encoder, w, h, fps):
        if encoder == ev.VT_H264:
            return ["-c:v", ev.VT_H264, "-b:v", "-5"]       # invalid bitrate -> VT exits non-zero
        return _orig_codec(encoder, w, h, fps)
    ev._video_codec_args = broken_vt                        # type: ignore[assignment]
    try:
        s = StubSession(lap_id=1, t0=0.0, dur=1.5, n=90)
        spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=1.5,
                             config=ev.OverlayConfig(out_height=360, encoder="videotoolbox",
                                                     hwaccel_decode=False, fps_cap=None))
        r = ev.Renderer(s, spec)
        assert r.encoder == ev.VT_H264                       # the (doomed) first attempt is VT
        res = r.run()                                        # must NOT raise — falls back to libx264
        assert res.frames >= 40 and os.path.getsize(out) > 0
        info = subprocess.run(
            [ev.FFPROBE, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=codec_name", "-of", "default=noprint_wrappers=1:nokey=1", out],
            check=True, capture_output=True, text=True).stdout.strip()
        assert info == "h264", f"fallback output must be h264, got {info!r}"
        tag = _stream_encoder_tag(out)
        assert "libx264" in tag.lower(), f"fallback must be libx264, got tag {tag!r}"
    finally:
        ev._video_codec_args = _orig_codec
        for p in (src, out):
            if os.path.exists(p):
                os.remove(p)
    print("real_fallback_to_libx264 OK")


def test_parallel_composite_is_deterministic_if_ffmpeg(monkeypatch_restore):
    """PARALLEL CORRECTNESS: the multi-worker paint pool must produce BYTE-IDENTICAL frames to the
    single-threaded pump — the g-meter dial's order-dependent state is advanced sequentially up
    front, so only the (independent) drawing is parallelized. Render the same synthetic clip with
    workers=1 and workers=4 (libx264 so the encoder itself is deterministic) and assert the decoded
    RGB frames hash equal. Gated on ffmpeg; no media file. (Guards against a future change that
    parallelizes something stateful and silently desyncs the dial/envelope between frames.)"""
    if not ev.ffmpeg_available():
        print("skip parallel_composite_is_deterministic (no ffmpeg)")
        return
    import hashlib
    tmp = os.environ.get("TMPDIR", "/tmp")
    src = os.path.join(tmp, "f9_det_src.mp4")
    o1 = os.path.join(tmp, "f9_det_w1.mp4")
    o4 = os.path.join(tmp, "f9_det_w4.mp4")
    _make_syn_clip(src, dur=2.0)

    def render(out, workers):
        s = StubSession(lap_id=1, t0=0.0, dur=2.0, n=120)
        spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=2.0,
                             config=ev.OverlayConfig(out_height=360, encoder="libx264",
                                                     hwaccel_decode=False, fps_cap=None,
                                                     workers=workers))
        ev.Renderer(s, spec).run()

    def frames_hash(path):
        raw = subprocess.run(
            [ev.FFMPEG, "-hide_banner", "-loglevel", "error", "-i", path,
             "-map", "0:v", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
            check=True, capture_output=True).stdout
        return hashlib.md5(raw).hexdigest()

    try:
        render(o1, 1)
        render(o4, 4)
        h1, h4 = frames_hash(o1), frames_hash(o4)
        assert h1 == h4, f"parallel composite differs from single-thread: {h1} != {h4}"
    finally:
        for p in (src, o1, o4):
            if os.path.exists(p):
                os.remove(p)
    print("parallel_composite_is_deterministic OK")


# --------------------------------------------------------------------------- restore fixture
class _Restore:
    """Save/restore the module globals the pipeline mocks clobber, so tests don't bleed into each
    other (no pytest here — a tiny manual fixture run around each mocked test)."""

    _SAVED = ("subprocess", "probe_video_size", "resolve_encoder", "videotoolbox_encoder_available",
              "videotoolbox_usable", "videotoolbox_decode_available")

    def __enter__(self):
        # snapshot subprocess.Popen separately (it lives on the subprocess module, not ev)
        self._popen = ev.subprocess.Popen
        self._saved = {name: getattr(ev, name) for name in self._SAVED if name != "subprocess"}
        return self

    def __exit__(self, *a):
        ev.subprocess.Popen = self._popen
        for name, val in self._saved.items():
            setattr(ev, name, val)


# the mocked tests take a `monkeypatch_restore` arg purely as a marker; the runner wraps them.
def monkeypatch_restore():
    return None


if __name__ == "__main__":
    import inspect
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        needs_restore = "monkeypatch_restore" in inspect.signature(t).parameters
        try:
            if needs_restore:
                with _Restore():
                    t(monkeypatch_restore)
            else:
                t()
            print(f"ok  {t.__name__}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    if failed:
        print(f"\n{failed}/{len(tests)} export-video tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} export-video tests passed")
