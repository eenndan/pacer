"""Tests for studio.export_compare — the DISTANCE-LOCKED two-lap compare export.

The claim this file has to make good on is narrow and checkable: at every output frame the two
panes are at the same TRACK POSITION, not merely started together. Everything here exists to pin
one half of that.

  * THE LOCK'S MATH, against the app's own number. `lock_to_track`'s per-frame gap must equal
    `Session.delta_between(lap_a, lap_b, t)` — the number the live compare badge shows — on a REAL
    Session (tests/_synthetic's bare factory) with two laps of different durations AND different
    odometer lengths. If the export's alignment ever parts company with the delta engine's, this
    fails.
  * THAT IT IS A RESAMPLE, NOT AN OFFSET. The pane-B time minus the pane-A time must VARY across
    the lap by far more than a frame. A constant difference is exactly what "press play on both"
    produces, and it is the thing this feature exists to not be.
  * THE CLOCKS. Pane B's media time must come from pane B's OWN telemetry->media clock (#266). The
    cross-recording test gives the two sessions different clocks and checks B's is the one used.
  * THE PICTURE, end to end, through REAL ffmpeg on tiny synthetic clips. Each clip's frames carry
    a white bar whose COLUMN encodes the frame index, so the exported file can be decoded and each
    pane's frame index READ BACK — which turns "is pane B at the locked position" into an exact
    integer comparison instead of a claim. No 11 GB media file needed.
  * THE PROCESS COUNT. A mocked render must launch exactly three processes (two decoders, one
    encoder) for a 60-frame clip — the structural statement that the export seeks twice in total
    rather than once per frame, which is what makes the ~765 ms dual-seek finding inapplicable.

Headless offscreen Qt (the painter builds QImages). Run: python tests/test_export_compare.py
"""
import inspect
import os
import subprocess
import sys
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from _synthetic import bare_session, odometer  # noqa: E402
from PySide6.QtGui import QFontMetricsF  # noqa: E402

from studio import export_compare as ec  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio import media_clock as mc  # noqa: E402

TMP = os.environ.get("TMPDIR", "/tmp")


def _in_pixi_env() -> bool:
    return os.path.join(".pixi", "envs") in os.environ.get("CONDA_PREFIX", "")


def _require_ffmpeg(label: str) -> bool:
    """ffmpeg is a LOCKED pixi dependency, so inside the pixi env (CI) its absence must FAIL rather
    than silently disable a regression test; outside it, skip."""
    if ev.ffmpeg_available():
        return True
    assert not _in_pixi_env(), (
        f"{label}: ffmpeg/ffprobe not found inside the pixi env, where they are a locked "
        "dependency — this test must run in CI, not skip.")
    print(f"skip {label} (no ffmpeg; not in the pixi env)")
    return False


# --------------------------------------------------------------------------- sessions
def _two_lap_session():
    """A REAL Session with two laps that differ in BOTH ways real laps differ: lap A is slower
    (11.9 s vs 10.9 s) and longer round (520 m vs 508 m). The odometers are non-uniform, so
    distance<->time is a genuine non-linear inversion rather than a scaled clock."""
    lap_a, lap_b = 3, 7
    ta, da = odometer(120, 0.1, 100.0, 520.0)
    tb, db = odometer(110, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    s = bare_session({lap_a: (ta, da), lap_b: (tb, db)}, best=lap_b, valid=[lap_a, lap_b])
    s.chapters = None  # no chapter table -> the identity media clock, and no footage bound
    s.lap_window = lambda i, _c={lap_a: (ta[0], ta[-1]), lap_b: (tb[0], tb[-1])}: _c.get(i)
    return s, lap_a, lap_b


def _single_lap_session(times, dists, lap_id=0, clock=None):
    """A REAL Session carrying ONE lap, optionally with a non-identity telemetry->media clock."""
    s = bare_session({lap_id: (times, dists)}, best=lap_id, valid=[lap_id])
    s.chapters = types.SimpleNamespace(media_clock=clock) if clock is not None else None
    s.lap_window = lambda i, _w=(float(times[0]), float(times[-1])): _w if i == lap_id else None
    return s


# --------------------------------------------------------------------------- geometry
def test_compare_geometry_is_two_panes_of_the_source_shape():
    """The pane keeps pane A's aspect, the frame is two of them, and every dimension is EVEN (no
    encoder this module can reach accepts an odd dimension in a subsampled pixel format)."""
    for src in ((3840, 2160), (1920, 1080), (2704, 1520), (1921, 1081)):
        for layout in ec.LAYOUT_CHOICES:
            cfg = ec.CompareConfig(out_height=360, layout=layout)
            g = ec.compare_geometry(src, src, cfg)
            assert g.pane_h == 360, (src, layout, g)
            assert abs(g.pane_w / g.pane_h - src[0] / src[1]) < 0.01, (src, g)
            for v in (g.out_w, g.out_h, g.pane_w, g.pane_h):
                assert v % 2 == 0, (src, layout, g)
            if layout == ec.LAYOUT_SIDE:
                assert (g.out_w, g.out_h) == (2 * g.pane_w, g.pane_h)
                assert g.pane_origin(1) == (g.pane_w, 0)
            else:
                assert (g.out_w, g.out_h) == (g.pane_w, 2 * g.pane_h)
                assert g.pane_origin(1) == (0, g.pane_h)
    print("ok geometry: two panes of the source shape, all even")


def test_geometry_never_upscales_past_pane_as_footage():
    """A 1080p request on 720p footage lands at 720p — the same "never upscale" rule the single-lap
    export applies to its frame, applied here to the PANE, because here the frame is two panes."""
    g = ec.compare_geometry((1280, 720), (1280, 720), ec.CompareConfig(out_height=1080))
    assert (g.pane_w, g.pane_h) == (1280, 720), g


def test_pane_filter_pads_a_mismatched_source_and_crops_on_request():
    """Cross-recording panes can disagree about frame shape. FIT pads to the exact pane; CROP takes
    the pane-shaped rectangle out of the SOURCE first (crop-then-scale, never scale-then-crop)."""
    fit = ec.pane_scale_filter(1440, 1080, 640, 360, ec.FIT_FIT)
    assert fit.startswith("scale=640:360:force_original_aspect_ratio=decrease"), fit
    assert "pad=640:360" in fit, fit
    crop = ec.pane_scale_filter(1440, 1080, 640, 360, ec.FIT_CROP)
    assert crop.startswith("crop="), "the crop must come FIRST — scaling 4K up to cover and then "\
                                     "discarding it is three times the render"
    assert crop.endswith("scale=640:360"), crop
    # the cropped rectangle is the widest 16:9 inside a 4:3 source, and even on both axes
    cw, ch = (int(v) for v in crop.split(",")[0][len("crop="):].split(":"))
    assert (cw, ch) == (1440, 810), (cw, ch)


# --------------------------------------------------------------------------- the lock
def _lock_for(s, lap_a, lap_b, fps=30.0):
    win = s.lap_window(lap_a)
    times = ev.frame_times(win[0], win[1], fps)
    return times, ec.lock_to_track(s, lap_a, s, lap_b, times)


def test_lock_gap_is_the_delta_engines_own_number():
    """THE tie-in. The per-frame gap the compare export burns must be the number the app's own
    compare badge shows — `Session.delta_between(a, b, t)` — at every frame, not just at the flag.
    They are the same normalized-distance projection; this pins that they stay the same."""
    s, a, b = _two_lap_session()
    times, lock = _lock_for(s, a, b)
    assert len(times) > 300
    worst = 0.0
    for i, t in enumerate(times):
        expect = s.delta_between(a, b, float(t))
        worst = max(worst, abs(float(lock.delta[i]) - expect))
    assert worst < 1e-9, f"the export's gap drifted from delta_between by {worst:.3e} s"
    print(f"ok lock == delta_between (max |Δ| {worst:.2e} s over {len(times)} frames)")


def test_lock_spans_the_whole_lap_and_stays_inside_lap_b():
    """Both panes traverse s in [0, 1] exactly once. The fractions are monotone, start at 0, and
    pane B's time never leaves lap B's own window — which is why "the shorter lap runs out" is not
    a case: the mapping is onto B's window, not onto A's clock."""
    s, a, b = _two_lap_session()
    _times, lock = _lock_for(s, a, b)
    assert lock.fraction[0] == 0.0
    assert np.all(np.diff(lock.fraction) >= -1e-12), "track position must never go backwards"
    assert lock.fraction[-1] > 0.99, lock.fraction[-1]
    b0, b1 = s.lap_window(b)
    assert lock.t_b_media.min() >= b0 - 1e-9 and lock.t_b_media.max() <= b1 + 1e-9
    # ... and at the same fraction, both elapsed clocks run from 0 to their own lap time.
    assert lock.elapsed_a[0] == 0.0 and lock.elapsed_b[0] == 0.0
    assert lock.elapsed_b[-1] > 0.0


def test_lock_is_a_resample_not_an_offset():
    """The heart of it. If pane B could be produced by SHIFTING pane A's clock, this feature would
    be "press play on both" with extra steps. Measured: the A->B time offset varies across the lap
    by far more than a frame, so no single shift reproduces it."""
    s, a, b = _two_lap_session()
    times, lock = _lock_for(s, a, b)
    b0 = s.lap_window(b)[0]
    a0 = s.lap_window(a)[0]
    offset = (lock.t_b_media - b0) - (times - a0)   # how far B's clock sits from A's, per frame
    spread = float(offset.max() - offset.min())
    assert spread > 0.25, (
        f"the pane-B mapping varies by only {spread:.3f} s across the lap — that is close enough "
        "to a constant offset that the lock would not be doing anything")
    # and the gap really does move: it is not a lap compared with itself
    assert float(lock.delta.max() - lock.delta.min()) > 0.25
    print(f"ok resample: the A->B offset spans {spread:.3f} s across one lap")


def test_self_compare_is_dead_even_everywhere():
    """A lap against itself locks to itself: zero gap at every frame, and pane B's time is pane A's
    telemetry time. The degenerate case has to be boring, not subtly wrong."""
    s, a, _b = _two_lap_session()
    times, lock = _lock_for(s, a, a)
    assert float(np.abs(lock.delta).max()) < 1e-9
    assert float(np.abs(lock.t_b_media - times).max()) < 1e-9


def test_cross_recording_uses_pane_bs_own_media_clock():
    """#266: the two recordings' media clocks run at different rates against their GPS clocks. Pane
    B's media time must be produced by SESSION B's clock; using pane A's would put B's picture off
    its own overlay by up to a fifth of a second at the end of a long recording."""
    ta, da = odometer(120, 0.1, 100.0, 520.0)
    tb, db = odometer(110, 0.1, 300.0, 508.0, lambda u: 1.3 + 0.7 * np.sin(u) ** 2)
    # Deliberately large and DIFFERENT rates, so "which clock" is visible in the numbers.
    sa = _single_lap_session(ta, da, lap_id=0, clock=mc.MediaClock(rate=1.0 + 2e-4, offset=0.5))
    sb = _single_lap_session(tb, db, lap_id=0, clock=mc.MediaClock(rate=1.0 - 3e-4, offset=-0.25))
    times = ev.frame_times(sa.media_time(ta[0]), sa.media_time(ta[-1]), 30.0)
    lock = ec.lock_to_track(sa, 0, sb, 0, times)
    # Recover the telemetry time pane B was locked to, through B's OWN inverse, and check it is a
    # real position inside lap B. Through A's inverse it would be a different (wrong) instant.
    tel_b = np.array([sb.telemetry_time(float(t)) for t in lock.t_b_media])
    assert tel_b.min() >= tb[0] - 1e-9 and tel_b.max() <= tb[-1] + 1e-9
    wrong = np.array([sa.telemetry_time(float(t)) for t in lock.t_b_media])
    assert float(np.abs(wrong - tel_b).max()) > 0.5, (
        "the two clocks must differ enough here that using the wrong one is detectable")
    # the gap is still the distance-aligned one, computed on each lap's own telemetry clock
    assert abs(float(lock.delta[-1]) - ((ta[-1] - ta[0]) - (tb[-1] - tb[0]))) < 5e-3
    print("ok cross-recording: pane B converts with pane B's clock")


def test_a_degenerate_lap_is_refused_before_any_ffmpeg():
    """A lap with no distance axis has no track position to lock to. Say so, rather than exporting
    a pane that never moves."""
    ta, da = odometer(120, 0.1, 100.0, 520.0)
    s = bare_session({0: (ta, da), 1: (np.array([5.0, 6.0]), np.array([0.0, 0.0]))},
                     best=0, valid=[0, 1])
    s.chapters = None
    s.lap_window = lambda i: (ta[0], ta[-1]) if i == 0 else (5.0, 6.0)
    try:
        ec.lock_to_track(s, 0, s, 1, ev.frame_times(ta[0], ta[-1], 5.0))
    except ValueError as exc:
        assert "track position" in str(exc), exc
    else:
        raise AssertionError("a zero-length odometer must be refused")


# --------------------------------------------------------------------------- the spec
def _spec_pair(out="/out.mp4", **kw):
    s, a, b = _two_lap_session()
    s.video_path = "/a.MP4"
    return s, ec.build_compare_spec(s, out, a, b, src_path_a="/a.MP4", src_path_b="/b.MP4", **kw)


def test_a_compare_spec_frees_both_panes_temp_files():
    """A seam-crossing lap resolves to a concat-demuxer LIST FILE, and a compare export can have
    two of them. The batch dialog's teardown now asks the spec to clean itself up for exactly this
    reason — reaching for `spec.source` alone leaked pane B's list on every compare."""
    made = []
    for name in ("a", "b"):
        path = os.path.join(TMP, f"pacer_cmp_list_{name}.txt")
        with open(path, "w") as fh:
            fh.write("file '/x.MP4'\n")
        made.append(ev.VideoSource(probe_path=f"/{name}.MP4", time_offset=0.0,
                                   concat_list_path=path))
    s, a, b = _two_lap_session()
    spec = ec.CompareSpec(out_path="/out.mp4", lap_id=a, t0=0.0, t1=1.0, source=made[0],
                          lap_b=b, source_b=made[1], t_b0=0.0, t_b1=1.0)
    assert all(os.path.exists(v.concat_list_path) for v in made)
    spec.cleanup()
    assert not any(os.path.exists(v.concat_list_path) for v in made), "both panes must be freed"
    spec.cleanup()  # idempotent


def test_the_progress_dialog_names_both_laps():
    """One modal serves both exports, so its label has to be able to describe a PAIR. A compare
    render announced as "lap 4" would name half of what it is doing."""
    from studio.export_controller import ExportController
    _s, spec = _spec_pair()
    text = ExportController._describe_spec(spec)
    assert text == "lap 4 against lap 8", text
    # ... and the single-lap spec is described exactly as it was
    single = ev.ExportSpec(out_path="/o.mp4", lap_id=3, t0=0.0, t1=1.0, src_path="/a.MP4")
    assert ExportController._describe_spec(single) == "lap 4"


def test_the_worker_drives_the_renderer_it_was_handed():
    """The compare export reaches the app's one export modal through a renderer FACTORY on the
    worker. Injected rather than branched on the spec's type, because a compare needs a second
    SESSION and that is not on the spec."""
    from studio.workers import VideoExportWorker
    seen = {}

    class _Fake:
        def __init__(self, session, spec):
            seen["built"] = (session, spec)

        def run(self, progress=None, cancel=None):
            seen["ran"] = True

    _s, spec = _spec_pair()
    worker = VideoExportWorker("SESSION", spec, lambda se, sp: _Fake(se, sp))
    worker.run()
    assert seen.get("ran") and seen["built"] == ("SESSION", spec)
    # the default is still the single-lap renderer, untouched
    assert VideoExportWorker("S", spec)._make_renderer is ev.Renderer


def test_audio_is_pane_as_alone():
    """One audio stream, from PANE A's source, over pane A's window. Pane B is time-warped by the
    lock, so its audio would have to be resampled by a varying factor (pitch-shifting the engine
    note); two engine tracks a second apart is mush. The single-lap `-af apad -shortest` pairing
    comes with it, so the clip's length is still the VIDEO's."""
    _s, spec = _spec_pair()
    enc = ev.build_encode_cmd(spec, 640, 720, 30.0, ev.SW_H264)
    assert enc.count("-map") == 2 and "0:v:0" in enc and "1:a:0?" in enc, enc
    assert "/a.MP4" in enc and "/b.MP4" not in enc, "pane B's audio must not be in the mux"
    assert enc[enc.index("-af") + 1] == "apad" and "-shortest" in enc
    print("ok audio: pane A's, once, with the length still decided by the video")


def test_both_panes_seek_their_own_window_in_their_own_file():
    """Pane A's decode is the inherited one; pane B's is built by the SAME `build_decode_cmd` from
    its own window and its own file — and each carries exactly ONE `-ss`."""
    _s, spec = _spec_pair()
    dec_a = ev.build_decode_cmd(spec, 640, 360, 30.0, False, "scale=640:360")
    dec_b = ev.build_decode_cmd(spec.spec_b(), 640, 360, 30.0, False, "scale=640:360")
    assert dec_a.count("-ss") == 1 and dec_b.count("-ss") == 1
    assert "/a.MP4" in dec_a and "/b.MP4" in dec_b
    assert abs(float(dec_b[dec_b.index("-ss") + 1]) - spec.t_b0) < 1e-6


def test_no_overlay_only_compare():
    """An overlay track has no second picture to lock to, so a "distance-locked alpha export" would
    be an ordinary overlay with a misleading name. Refuse it in words."""
    try:
        _spec_pair(config=ec.CompareConfig(overlay_only=True))
    except ValueError as exc:
        assert "overlay-only" in str(exc), exc
    else:
        raise AssertionError("overlay-only must be refused for a compare export")


def test_compare_config_survives_the_software_retry():
    """The inherited VideoToolbox->libx264 retry does `dataclasses.replace(config, encoder=...)`. A
    plain OverlayConfig coming back would silently drop the layout, and the retry would render a
    DIFFERENT clip from the one that failed."""
    from dataclasses import replace
    cfg = ec.CompareConfig(layout=ec.LAYOUT_SIDE, pane_fit=ec.FIT_CROP)
    again = replace(cfg, encoder="libx264")
    assert isinstance(again, ec.CompareConfig)
    assert again.layout == ec.LAYOUT_SIDE and again.pane_fit == ec.FIT_CROP


# --------------------------------------------------------------------------- the overlay budget
def test_every_pill_holds_the_ink_it_will_draw():
    """The pills are measured over the render's OWN strings, so the budget cannot disagree with the
    ink (the single-lap export's budget did, twice, by re-deriving the strings). Checked the way
    the painter draws them: advance + both paddings against the pill width."""
    s, a, b = _two_lap_session()
    _times, lock = _lock_for(s, a, b)
    spec = ec.build_compare_spec(s, "/out.mp4", a, b, src_path_a="/a.MP4", src_path_b="/b.MP4")
    frames = ec.build_frames(spec, lock)
    geo = ec.compare_geometry((1920, 1080), (1920, 1080), spec.config)
    p = ec.ComparePainter(geo, spec.config, frames, f"{spec.label_a} vs {spec.label_b}")
    pad = p._caption_h * ec._PILL_PAD_FRAC
    fm = QFontMetricsF(p._caption_font)
    for f in frames:
        for text in (f.caption_a, f.caption_b):
            need = fm.horizontalAdvance(text) + 2 * pad
            assert need <= p._caption_w + 1e-6, (text, need, p._caption_w)
    fm_big = QFontMetricsF(p._delta_font)
    fm_small = QFontMetricsF(p._prefix_font)
    k = p._delta_h / 44.0
    base = (2 * p._delta_h * ec._PILL_PAD_FRAC
            + fm_small.horizontalAdvance(p._prefix) + ec._DELTA_GAP_K * k)
    for f in frames:
        need = base + fm_big.horizontalAdvance(f.delta_run)
        assert need <= p._delta_w + 1e-6, (f.delta_run, need, p._delta_w)
    # the gap pill is centred and clear of both panes' corner elements
    box = p.delta_box()
    assert box.x() > p._margin + max(p._caption_w, p._readout_w), "the gap pill overlaps a corner"
    print(f"ok pill budget: {len(frames)} frames, no run exceeds its pill")


# --------------------------------------------------------------------------- the drive loop
class _FakeProc:
    """A stand-in Popen: a decoder serves `nframes` of zeroed rgb24 then EOF; the encoder swallows
    its stdin."""

    def __init__(self, frame_bytes=0, nframes=0, is_decoder=False):
        self.returncode = 0
        self.stdout = None
        self.stdin = None
        self.stderr = None
        self.killed = False
        if is_decoder:
            self.stdout = types.SimpleNamespace(_left=nframes, _fb=frame_bytes,
                                                read=self._read, close=lambda: None)
        else:
            self.written = bytearray()
            self.stdin = types.SimpleNamespace(write=lambda b: self.written.extend(b),
                                               close=lambda: None, flush=lambda: None)

    def _read(self, n):
        so = self.stdout
        if so._left <= 0:
            return b""
        so._left -= 1
        return bytes(so._fb)

    def wait(self, *a, **k):
        return 0

    def kill(self):
        self.killed = True


class _Restore:
    """Save/restore the module globals these mocks clobber (no pytest here)."""

    def __enter__(self):
        self._popen = ev.subprocess.Popen
        self._saved = {n: getattr(ev, n) for n in
                       ("probe_video_size", "probe_source_duration", "resolve_encoder")}
        self._saved_ec = {n: getattr(ec, n) for n in ("probe_video_size", "guard_validate_window")}
        return self

    def __exit__(self, *a):
        ev.subprocess.Popen = self._popen
        for n, v in self._saved.items():
            setattr(ev, n, v)
        for n, v in self._saved_ec.items():
            setattr(ec, n, v)


def monkeypatch_restore():
    return None


def test_the_whole_render_launches_three_processes(monkeypatch_restore):
    """THE 765 ms FINDING, ANSWERED STRUCTURALLY. Live distance-lock needed a corrective seek per
    frame with two decoders alive (~765 ms each), which is why it was abandoned on screen. An
    export streams: two decoders and one encoder are opened ONCE, each decoder carrying exactly one
    `-ss`, and every one of the 60 frames comes out of a forward read. Count the processes."""
    s, a, b = _two_lap_session()
    cfg = ec.CompareConfig(out_height=120, fps_cap=None, fps=30.0, encoder="libx264")
    spec = ec.build_compare_spec(s, "/out.mp4", a, b, config=cfg,
                                 src_path_a="/a.MP4", src_path_b="/b.MP4")
    geo = ec.compare_geometry((640, 360), (640, 360), cfg)
    pane_bytes = geo.pane_w * geo.pane_h * 3
    launched = []

    def fake_popen(cmd, **kw):
        launched.append(cmd)
        proc = _FakeProc(frame_bytes=pane_bytes, nframes=10_000, is_decoder=cmd[-1] == "pipe:1")
        return proc

    ev.subprocess.Popen = fake_popen
    ev.probe_video_size = ec.probe_video_size = lambda _p: (640, 360, 30.0)
    ev.probe_source_duration = lambda _s: 1.0e9
    ev.resolve_encoder = lambda _c: ev.SW_H264
    r = ec.CompareRenderer(s, spec, s)
    total = r.total_frames
    res = r.run()
    assert res.frames == total > 100, (res.frames, total)
    assert len(launched) == 3, f"expected 2 decoders + 1 encoder, got {len(launched)}"
    decoders = [c for c in launched if c[-1] == "pipe:1"]
    assert len(decoders) == 2
    for cmd in decoders:
        assert cmd.count("-ss") == 1, "one accurate seek per pane, for the whole render"
    assert res.out_w == geo.out_w and res.out_h == geo.out_h
    print(f"ok {total} frames from 3 processes and 2 seeks total")


def test_pane_b_running_out_holds_its_last_frame(monkeypatch_restore):
    """A lap at the very end of a recording can leave pane B a frame or two short. Hold the last
    picture and COUNT it rather than throwing away an otherwise-correct clip — but a pane B that
    decoded NOTHING is a broken export and must say so."""
    s, a, b = _two_lap_session()
    cfg = ec.CompareConfig(out_height=120, fps_cap=None, fps=30.0, encoder="libx264")
    spec = ec.build_compare_spec(s, "/out.mp4", a, b, config=cfg,
                                 src_path_a="/a.MP4", src_path_b="/b.MP4")
    geo = ec.compare_geometry((640, 360), (640, 360), cfg)
    pane_bytes = geo.pane_w * geo.pane_h * 3
    starved = {"n": 0}

    def fake_popen(cmd, **kw):
        if cmd[-1] != "pipe:1":
            return _FakeProc(is_decoder=False)
        starved["n"] += 1
        # decoder 1 = pane A (plenty), decoder 2 = pane B (cut short by 5 frames)
        n = 10_000 if starved["n"] == 1 else 5
        return _FakeProc(frame_bytes=pane_bytes, nframes=n, is_decoder=True)

    ev.subprocess.Popen = fake_popen
    ev.probe_video_size = ec.probe_video_size = lambda _p: (640, 360, 30.0)
    ev.probe_source_duration = lambda _s: 1.0e9
    ev.resolve_encoder = lambda _c: ev.SW_H264
    r = ec.CompareRenderer(s, spec, s)
    res = r.run()
    assert res.frames == r.total_frames, "a short pane B must not shorten the clip"
    assert r.held_frames > 0, "the held frames must be counted, not hidden"
    print(f"ok pane B ran out: {r.held_frames} frames held, clip still {res.frames} long")


# --------------------------------------------------------------------------- end to end
_BAR_W = 6
_CLIP_W, _CLIP_H = 640, 360


def _bar_clip(path: str, n: int, fps: float, step: int) -> None:
    """A tiny clip whose frame INDEX is readable off the picture: frame j is black with a white bar
    at x = 4 + j*step. That is what lets the exported file be decoded and each pane's source frame
    identified exactly, instead of the lock being checked only against itself."""
    frames = []
    for j in range(n):
        f = np.zeros((_CLIP_H, _CLIP_W, 3), dtype=np.uint8)
        x = 4 + j * step
        assert x + _BAR_W <= _CLIP_W, (n, step)
        f[:, x:x + _BAR_W] = 255
        frames.append(f.tobytes())
    subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{_CLIP_W}x{_CLIP_H}", "-r", f"{fps:g}",
         "-i", "pipe:0", "-c:v", "libx264", "-preset", "ultrafast", "-crf", "10",
         "-pix_fmt", "yuv420p", path],
        input=b"".join(frames), check=True, capture_output=True)


def _bar_index(rows: np.ndarray, step: int) -> float:
    """Recover the source frame index from a band of pane pixels: the intensity-weighted centroid
    of the bright columns, inverted through the bar's own placement rule."""
    profile = rows.mean(axis=(0, 2)).astype(float)
    hot = profile >= 0.5 * profile.max()
    xs = np.arange(len(profile))[hot]
    centre = float((profile[hot] * xs).sum() / profile[hot].sum())
    return (centre - 4.0 - (_BAR_W - 1) / 2.0) / step


def test_real_render_puts_pane_b_at_the_locked_frame():
    """THE END-TO-END LOCK CHECK, on real ffmpeg and no big media file.

    Two synthetic clips whose frames carry their own index as a bar position, two synthetic laps of
    different duration AND different length, one real compare render — then every output frame is
    decoded and BOTH panes' source frame indices are read back off the picture and compared with an
    independently recomputed distance lock. Pane A must be frame i; pane B must be the frame at the
    same TRACK POSITION, which is a different, non-linearly varying number."""
    if not _require_ffmpeg("real_render_puts_pane_b_at_the_locked_frame"):
        return
    fps = 30.0
    clip_a = os.path.join(TMP, "pacer_cmp_a.mp4")
    clip_b = os.path.join(TMP, "pacer_cmp_b.mp4")
    out = os.path.join(TMP, "pacer_cmp_out.mp4")
    # A: 2.0 s of lap on a 2.1 s clip. B: 1.4 s of lap on a 1.5 s clip.
    _bar_clip(clip_a, 63, fps, step=10)
    _bar_clip(clip_b, 45, fps, step=13)
    # Non-uniform, DIFFERENT odometers: the lock is a real inversion, not a scaled clock.
    ta, da = odometer(101, 0.02, 0.0, 500.0)
    tb, db = odometer(71, 0.02, 0.0, 480.0, lambda u: 1.4 + 0.9 * np.sin(u) ** 2)
    sa = _single_lap_session(ta, da, lap_id=0)
    sb = _single_lap_session(tb, db, lap_id=0)
    cfg = ec.CompareConfig(out_height=_CLIP_H, fps=fps, encoder="libx264", quality="high")
    try:
        res = ec.render_compare(sa, out, 0, 0, session_b=sb, config=cfg,
                                src_path_a=clip_a, src_path_b=clip_b)
        assert res.frames == 60, res.frames
        # --- independently recompute the lock from the odometers alone ---
        times = ev.frame_times(0.0, float(ta[-1]), fps)
        s_of = np.interp(times, ta, da) / da[-1]
        tb_of = np.interp(s_of * db[-1], db, tb)
        want_b = np.round(tb_of * fps).astype(int)
        # --- decode the render and read both panes' source frames off the picture ---
        raw = subprocess.run(
            [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-i", out,
             "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"],
            check=True, capture_output=True).stdout
        w, h, _ = ev.probe_video_size(out)
        assert (w, h) == (_CLIP_W, 2 * _CLIP_H), (w, h)
        frame_bytes = w * h * 3
        n = len(raw) // frame_bytes
        assert n == 60, n
        arr = np.frombuffer(raw, np.uint8).reshape(n, h, w, 3)
        # A clean band in the middle of each pane: clear of the caption, the speed pill, the gap
        # pill on the seam and the progress bar.
        band_a = slice(int(_CLIP_H * 0.35), int(_CLIP_H * 0.55))
        band_b = slice(_CLIP_H + int(_CLIP_H * 0.35), _CLIP_H + int(_CLIP_H * 0.55))
        err_a, err_b = [], []
        for i in range(n):
            err_a.append(_bar_index(arr[i, band_a], 10) - i)
            err_b.append(_bar_index(arr[i, band_b], 13) - want_b[i])
        err_a = np.abs(np.array(err_a))
        err_b = np.abs(np.array(err_b))
        assert err_a.max() < 0.5, f"pane A drifted from its own frame grid: {err_a.max():.2f} frames"
        assert err_b.max() < 0.6, f"pane B is off the lock by {err_b.max():.2f} frames"
        # ...and the lock is doing something: B's frame index is NOT i, and not i scaled either.
        drift = np.abs(want_b - np.arange(n))
        assert drift.max() > 10, drift.max()
        print(f"ok end-to-end lock: pane A max {err_a.max():.2f} frames, "
              f"pane B max {err_b.max():.2f} frames, against a lock that moves "
              f"{drift.max()} frames away from a clock lock")
    finally:
        for p in (clip_a, clip_b, out):
            if os.path.exists(p):
                os.remove(p)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        needs = "monkeypatch_restore" in inspect.signature(t).parameters
        try:
            if needs:
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
        print(f"\n{failed}/{len(tests)} compare-export tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} compare-export tests passed")
