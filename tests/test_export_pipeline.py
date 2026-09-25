"""The PIPELINED export pump (E9) paints exactly what the serial pump paints, in order, and fails
like it: cancel, a failed write and a misaligned relay decoder all end cleanly.

Why this file exists. `Renderer` runs the decode read and the encode write on threads of their own
around the painting thread (`export_video._FramePool`), and on a hardware decode it relays the
decode across two ffmpeg processes (`_DecodeRelay`). Both are speed only: the bar is that every
painted frame is BYTE-IDENTICAL to the serial pump's. These tests read the painted frames exactly as
the render hands them to the encoder (an encoder stand-in that hashes each frame, before any
encoding) and compare the two pumps frame by frame — on the synthetic GoPro through the real
loader, and on a moving clip for the relay, whose seam check needs frames that differ. CI has no
VideoToolbox, so the relay is switched on by hand here: it is correct on any decoder, it only
PAYS on the hardware one (see `_RELAY_TURN`).
"""
import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import replace

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "bindings", "pacer"))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from studio import export_video as ev  # noqa: E402
from studio.timeline import nearest_sample  # noqa: E402


class _Session:
    """One lap over [0, dur) with a moving trace and a live g — just the accessors the painter
    reads (the same duck type test_export_video's StubSession is)."""

    def __init__(self, dur, n=400):
        self._dur = dur
        self.tt = np.linspace(0.0, dur, n)
        self.tv = 40.0 + 30.0 * np.sin(np.linspace(0.0, np.pi, n))
        self.tx = 50.0 * np.cos(np.linspace(0.0, 2 * np.pi, n))
        self.ty = 30.0 * np.sin(np.linspace(0.0, 2 * np.pi, n))
        self.has_gmeter = True

    def lap_at_time(self, t):
        return 1 if 0.0 <= t < self._dur else None

    def index_at_time(self, t):
        return nearest_sample(self.tt, t)

    def lap_window(self, lap_id):
        return (0.0, self._dur) if lap_id == 1 else None

    def delta_at_lap(self, lap_id, t):
        return 0.1 * np.sin(t) if lap_id == 1 else None

    def g_at_time(self, t):
        return (0.8 * np.sin(t), 0.6 * np.cos(1.3 * t), 1.0)

    def gmeter_source(self):
        return "accl"

    def lap_trace_xy(self, lap_id):
        return (self.tx, self.ty) if lap_id == 1 else None


class _HashingEncoder:
    """The encoder's stand-in: hashes every frame it is handed, exactly as painted. `fail_at`
    makes its write fail on that frame, as a dead encoder's pipe would."""

    def __init__(self, cmd, fail_at=None, delay=0.0):
        head = cmd[:cmd.index("pipe:0")]
        w, h = map(int, head[head.index("-s") + 1].split("x"))
        self.fb = w * h * (3 if head[head.index("-pix_fmt") + 1] == "rgb24" else 4)
        self.digests, self._buf = [], bytearray()
        self._fail_at, self._delay = fail_at, delay
        self.stdin, self.stderr, self.returncode, self.pid = self, None, None, -1

    def write(self, b):
        if self._fail_at is not None and len(self.digests) >= self._fail_at:
            self.returncode = 1
            raise BrokenPipeError("encoder gone")
        if self._delay:
            time.sleep(self._delay)
        self._buf += memoryview(b).cast("B")
        while len(self._buf) >= self.fb:
            self.digests.append(hashlib.sha1(self._buf[:self.fb]).hexdigest())
            del self._buf[:self.fb]
        return len(b)

    def flush(self):
        pass

    def close(self):
        pass

    def wait(self, timeout=None):
        self.returncode = self.returncode or 0
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


def _render(spec, session, **enc_kw):
    """Render `spec` with the real decoder and the hashing encoder; (digests, renderer, error)."""
    real_popen = subprocess.Popen
    encoders = []

    def popen(cmd, *a, **k):
        if "pipe:0" in cmd:
            encoders.append(_HashingEncoder(cmd, **enc_kw))
            return encoders[-1]
        return real_popen(cmd, *a, **k)

    r = ev.Renderer(session, spec)
    start = r._start

    def patched_start():
        ev.subprocess.Popen = popen
        try:
            start()
        finally:
            ev.subprocess.Popen = real_popen
    r._start = patched_start
    err = None
    try:
        r.run()
    except Exception as exc:  # noqa: BLE001 — the caller asserts on it
        err = exc
    return (encoders[0].digests if encoders else []), r, err


def _moving_clip(path, dur, fps=60):
    """A clip whose every frame differs (testsrc2 moves), so a seam check can prove alignment."""
    subprocess.run([ev.FFMPEG, "-nostdin", "-loglevel", "error", "-f", "lavfi", "-i",
                    f"testsrc2=size=640x360:rate={fps}:duration={dur}", "-c:v", "libx264",
                    "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-g", "30", path],
                   check=True, capture_output=True)


def _spec(src, dur, out="/nonexistent-e9/out.mp4", **cfg):
    """(The out path is never opened while the hashing encoder stands in for ffmpeg.)"""
    config = ev.OverlayConfig(out_height=360, encoder="libx264", hwaccel_decode=False, **cfg)
    return ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=dur, config=config)


def _export_threads():
    return [t.name for t in threading.enumerate()
            if t.is_alive() and t.name in ("export-read", "export-write")]


def test_pipelined_paints_the_serial_frames_on_the_synthetic_gopro():
    """The real loader on the synthetic GoPro: 6 s of its best lap, composite AND overlay-only,
    every painted frame hashed — the pipelined pump must equal the serial one frame for frame."""
    if not ev.ffmpeg_available():
        print("skip synthetic_gopro (no ffmpeg)")
        return
    from studio.chapters import discover_siblings
    from studio.dev import synth_gopro
    from studio.session import Session
    with tempfile.TemporaryDirectory(prefix="pacer-e9-synth-") as tmp:
        rec = synth_gopro.generate(os.path.join(tmp, "rec"))
        s = Session.load(discover_siblings(rec.paths[0]))
        lap = s.best_lap_id()
        for overlay_only in (False, True):
            cfg = ev.OverlayConfig(out_height=180, encoder="libx264", hwaccel_decode=False,
                                   overlay_only=overlay_only, alpha_codec=ev.ALPHA_PRORES)
            base = ev.build_lap_spec(s, os.path.join(tmp, "o.mov"), lap, config=cfg,
                                     lead_in=1.0, lead_out=1.0)
            spec = replace(base, t1=base.t0 + 6.0)
            got = {}
            for workers in (1, None):
                digests, r, err = _render(replace(spec, config=replace(cfg, workers=workers)), s)
                assert err is None, err
                got[workers] = digests
            spec.source.cleanup()
            assert len(got[1]) == r.total_frames > 100, (len(got[1]), r.total_frames)
            diff = [i for i, (a, b) in enumerate(zip(got[1], got[None], strict=True)) if a != b]
            assert not diff, f"overlay_only={overlay_only}: frames differ at {diff[:10]}"
    print("ok synthetic GoPro: pipelined == serial, composite and overlay-only")


def test_relay_hands_over_and_paints_the_serial_frames():
    """Three decoders in relay over 12 s at 30 fps: every seam checked and handed over, and the
    frames — in order — exactly the serial pump's."""
    if not ev.ffmpeg_available():
        print("skip relay (no ffmpeg)")
        return
    with tempfile.TemporaryDirectory(prefix="pacer-e9-relay-") as tmp:
        src = os.path.join(tmp, "src.mp4")
        _moving_clip(src, 12.5)
        s = _Session(12.0)
        serial, _, err = _render(_spec(src, 12.0, fps_cap=30.0, workers=1), s)
        assert err is None, err
        orig = ev.Renderer._relay_ok
        ev.Renderer._relay_ok = lambda self: True     # CI has no VideoToolbox; see module doc
        try:
            relayed, r, err = _render(_spec(src, 12.0, fps_cap=30.0), s)
        finally:
            ev.Renderer._relay_ok = orig
        assert err is None, err
        assert len(serial) == 360 and len(relayed) == 360, (len(serial), len(relayed))
        diff = [i for i, (a, b) in enumerate(zip(serial, relayed)) if a != b]
        assert not diff, f"the relay painted different frames at {diff[:10]}"
        assert not _export_threads(), _export_threads()
        print("ok relay: 360/360 frames identical to the serial pump")


def test_relay_refuses_a_misaligned_decoder_and_still_paints_the_serial_frames():
    """NEGATIVE CONTROL for the seam check: every relay decoder is started ONE FRAME late. The
    check must refuse the handover and the running decoder finish the clip — same frames."""
    if not ev.ffmpeg_available():
        print("skip relay_misaligned (no ffmpeg)")
        return
    with tempfile.TemporaryDirectory(prefix="pacer-e9-relay-") as tmp:
        src = os.path.join(tmp, "src.mp4")
        _moving_clip(src, 12.5)
        s = _Session(12.0)
        serial, _, _ = _render(_spec(src, 12.0, fps_cap=30.0, workers=1), s)
        orig_ok, orig_spawn = ev.Renderer._relay_ok, ev.Renderer._spawn_relay_leg
        relays = []
        orig_init = ev._DecodeRelay.__init__

        def init(self, *a, **k):
            relays.append(self)
            orig_init(self, *a, **k)
        ev.Renderer._relay_ok = lambda self: True
        ev.Renderer._spawn_relay_leg = lambda self, start: orig_spawn(self, start + 1)
        ev._DecodeRelay.__init__ = init
        try:
            relayed, r, err = _render(_spec(src, 12.0, fps_cap=30.0), s)
        finally:
            ev.Renderer._relay_ok, ev.Renderer._spawn_relay_leg = orig_ok, orig_spawn
            ev._DecodeRelay.__init__ = orig_init
        assert err is None, err
        assert relays and relays[0].gave_up and relays[0].handovers == 0, \
            "a decoder started off the plan's frames must fail the seam check"
        assert relayed == serial, "the refused relay must still paint the serial frames"
        print("ok relay: a misaligned decoder is refused and the clip is unchanged")


def test_cancel_mid_render_stops_every_thread_and_process():
    """Cancel partway through a pipelined relay render: CancelledError, promptly, with no export
    thread left running and every decoder reaped."""
    if not ev.ffmpeg_available():
        print("skip cancel (no ffmpeg)")
        return
    with tempfile.TemporaryDirectory(prefix="pacer-e9-cancel-") as tmp:
        src = os.path.join(tmp, "src.mp4")
        _moving_clip(src, 12.5)
        orig = ev.Renderer._relay_ok
        ev.Renderer._relay_ok = lambda self: True
        seen = {}
        try:
            r = ev.Renderer(_Session(12.0), _spec(src, 12.0, os.path.join(tmp, "out.mp4"),
                                                  fps_cap=30.0))
            procs = []
            t0 = time.monotonic()

            def cancel():
                if r._i >= 150 and not procs:
                    procs.extend(p for p in (r._dec, *r._extra_procs()) if p is not None)
                return bool(procs)
            try:
                r.run(cancel=cancel)
            except ev.CancelledError:
                seen["cancelled"] = time.monotonic() - t0
        finally:
            ev.Renderer._relay_ok = orig
        assert "cancelled" in seen, "a cancel mid-render must raise CancelledError"
        assert len(procs) >= 2, f"the relay should have had two decoders running: {procs}"
        assert all(p.poll() is not None for p in procs), "a decoder outlived the cancel"
        assert not _export_threads(), f"threads outlived the cancel: {_export_threads()}"
        assert r._enc.poll() is not None
    print(f"ok cancel: stopped in {seen['cancelled']:.1f}s, no thread or process left")


def test_a_failed_write_surfaces_as_an_encode_error_not_a_hang():
    """The writer thread's failed write reaches the painting thread as the serial pump's would:
    a clear encode error within seconds (libx264 has no retry), threads gone."""
    if not ev.ffmpeg_available():
        print("skip failed_write (no ffmpeg)")
        return
    with tempfile.TemporaryDirectory(prefix="pacer-e9-fail-") as tmp:
        src = os.path.join(tmp, "src.mp4")
        _moving_clip(src, 4.5)
        t0 = time.monotonic()
        digests, r, err = _render(_spec(src, 4.0, fps_cap=30.0), _Session(4.0), fail_at=40)
        dt = time.monotonic() - t0
        assert isinstance(err, RuntimeError) and "encode" in str(err), repr(err)
        assert len(digests) == 40 and dt < 20, (len(digests), dt)
        assert not _export_threads(), _export_threads()
    print(f"ok failed write: {type(err).__name__} after {dt:.1f}s")


def test_frame_order_survives_a_slow_encoder():
    """A slow encoder backs the pump up against its bounded pool; frames still leave in order and
    none is lost or repeated."""
    if not ev.ffmpeg_available():
        print("skip slow_encoder (no ffmpeg)")
        return
    with tempfile.TemporaryDirectory(prefix="pacer-e9-slow-") as tmp:
        src = os.path.join(tmp, "src.mp4")
        _moving_clip(src, 3.5)
        s = _Session(3.0)
        serial, _, _ = _render(_spec(src, 3.0, fps_cap=30.0, workers=1), s)
        slow, r, err = _render(_spec(src, 3.0, fps_cap=30.0), s, delay=0.01)
        assert err is None, err
        assert slow == serial and len(slow) == 90, (len(slow), len(serial))
    print("ok slow encoder: 90/90 frames in order")


def test_the_pool_bounds_memory():
    """The pump allocates its frames once: `_PIPE_FRAMES` at 4K (99.5 MB composite, 132.7 MB
    overlay-only), plus the relay's `_RELAY_AHEAD + 2` at 1080p (285 MB)."""
    mb = 1e6
    assert ev._PIPE_FRAMES * 3840 * 2160 * 3 / mb < 100
    assert ev._PIPE_FRAMES * 3840 * 2160 * 4 / mb < 133
    assert (ev._PIPE_FRAMES + ev._RELAY_AHEAD + 2) * 1920 * 1080 * 3 / mb < 290
    # the relay never runs on a frame its buffers were not sized for
    assert ev._RELAY_MAX_FRAME == 1920 * 1080 * 3
    pool = ev._FramePool(16, 3)
    got = [pool.take(lambda: True) for _ in range(4)]
    assert got[3] is None and all(isinstance(b, bytearray) for b in got[:3]), got
    pool.give(bytearray(16))            # a foreign buffer is never adopted
    assert pool.take(lambda: True) is None
    print("ok pool: bounded, never grows")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    if failed:
        print(f"\n{failed}/{len(tests)} export-pipeline tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} EXPORT-PIPELINE TESTS OK")
