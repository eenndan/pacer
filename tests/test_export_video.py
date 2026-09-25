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

Two GATING tiers:
  * REAL-MEDIA tests (FOOTAGE_CHECKS) are NOT part of this file's ordinary run. Each is its own
    CTest registration, `footage.<check>`, on the recording `PACER_GOLDEN_MP4` names — and without
    it CTest reports that check as SKIPPED, by name, rather than this file counting it as passed
    (tests/_footage.py). The VideoToolbox-hardware checks (VIDEOTOOLBOX_CHECKS) are registrations
    of their own too, `videotoolbox.<check>`, SKIPPED by name where no VT session opens
    (tests/_videotoolbox.py); a check with a software half keeps that half here.
  * NO-MEDIA ffmpeg tests (synthetic-clip render, watchdog, cancel, GUI worker, fallback,
    determinism) gate through `_require_ffmpeg`: ffmpeg is a LOCKED pixi dependency (pyproject.toml),
    so inside the pixi env (CI's `pixi run test`) a missing ffmpeg FAILS LOUDLY rather than silently
    skipping — the safety net can't quietly no-op. Outside the pixi env (bare-python local run with
    the env bin off PATH) they still skip.

Headless offscreen Qt (the painter builds a QImage + a headless g-meter dial); fast; no network.

Run: python tests/test_export_video.py
"""
import os
import subprocess
import sys
import time
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from _qtapp import themed_app  # noqa: E402

# The SHIPPED font stack, not a bare QApplication. This file now measures WIDTHS — both HUD pills
# are fitted to their own text — and a width measured in a face the app does not ship is measuring
# nothing (see tests/_qtapp). Before this, the bare app had no bundled fonts registered, so
# `theme.mono_font` fell through Inter to the mono stack and Qt substituted whatever it could find.
_APP = themed_app()

import _footage  # noqa: E402
import _videotoolbox  # noqa: E402

from studio import chapters  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.timeline import nearest_sample  # noqa: E402


def _real_media(label: str) -> str:
    """The recording `PACER_GOLDEN_MP4` names (default: D24's intact chapter 2), present AND
    PARSEABLE, for one of the real-render checks — or `FootageMissing`, which CTest reports as that
    check SKIPPED. It used to answer False, and each check printed a skip line and RETURNED, so the
    runner below counted three renders nobody had done as three passes; D24's own chapter 1 — the
    2.4 MB JSON a dev tool wrote over the footage — once held all three there permanently.

    THE TWO WAYS THE PROBE FAILS ARE REPORTED SEPARATELY, and the split is not cosmetic. A bare
    `import pacer` from the repo root resolves to the C++ `pacer/` source directory — a PEP 420
    namespace portion with no `GPMFSource` — so a run without the bindings on PYTHONPATH raised
    AttributeError here and printed it as "<the owner's own footage> is present but unreadable":
    an import problem wearing the clothes of data loss, on a machine where a dev tool really did
    destroy 11.9 GB of footage. CTest injects the bindings, so it is the standalone run that hits
    it. Neither branch guesses at a CAUSE for the file; the parser's own words are enough."""
    path = _footage.recording()
    try:
        import pacer
        open_gpmf = pacer.GPMFSource
    except (ImportError, AttributeError) as exc:
        raise _footage.FootageMissing(
            f"the pacer bindings are not importable in this run ({exc}) — this says nothing about "
            f"{path}. Run under CTest, or with PYTHONPATH=bindings/pacer") from None
    try:
        open_gpmf(path)
    except Exception as exc:  # noqa: BLE001 — any parser failure = "not usable"
        raise _footage.FootageMissing(f"{path} did not parse as GPMF ({exc})") from None
    if not _require_ffmpeg(label):
        raise _footage.FootageMissing("no ffmpeg on PATH, outside the pixi env")
    return path


def _in_pixi_env() -> bool:
    """True when we're running inside the project's pixi env, where `ffmpeg = ">=7.1,<8"` is a
    LOCKED dependency (pyproject.toml [tool.pixi.dependencies]) — so ffmpeg/ffprobe are guaranteed
    present. CI runs `pixi run test`, which sets CONDA_PREFIX to .pixi/envs/<name>."""
    prefix = os.environ.get("CONDA_PREFIX", "")
    return os.path.join(".pixi", "envs") in prefix


def _require_ffmpeg(label: str) -> bool:
    """Gate the NO-MEDIA ffmpeg tests so the safety net can't silently no-op in CI.

    Returns True when ffmpeg/ffprobe are available (run the test). When they're absent it FAILS
    LOUDLY if we're inside the pixi env (ffmpeg is a locked dep there, so absence = a broken
    env/PATH that must not hide these regression tests); otherwise — e.g. a bare-python local run
    with the pixi bin off PATH — it returns False so the caller skips. This only guards tests that
    need ffmpeg ALONE (a synthetic clip / mocked media); the real-media checks go through `_real_media`."""
    if ev.ffmpeg_available():
        return True
    assert not _in_pixi_env(), (
        f"{label}: ffmpeg/ffprobe not found on PATH inside the pixi env, where they are a LOCKED "
        "dependency (pyproject.toml) — this no-media regression test must run in CI, not skip. "
        "A broken env/PATH must fail loudly, never silently disable the safety net.")
    print(f"skip {label} (no ffmpeg; not in the pixi env)")
    return False


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
        return nearest_sample(self.tt, t)   # the real Session's rule, not a copy of it

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

    def lap_trace_xy(self, lap_id):
        if lap_id != self._lap:
            return None
        return self.tx, self.ty


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
    """Decode argv: pre-input -ss t0, -i src, -t (the PLAN's length), fps filter + scale=WxH, rgb24
    rawvideo to pipe:1, audio/subs/data dropped. The fps filter comes FIRST, so the scale only sees
    the frames the render keeps (byte-identical output — see `build_decode_cmd`).

    THE `-t` IS THE PLAN, NOT THE WINDOW, and that is the "one frame short" fix: ffmpeg trims the
    output in the frame's own timebase and ROUNDS, so a window of 70.000000 s at 59.94 fps (4195.8
    frames) delivered 4195 against a plan of 4196 and the render hit a short read. Asked for
    4196/59.94 s it lands on the frame boundary. Asserted as the RELATIONSHIP — the ask covers
    exactly the planned frames and `-frames:v` pins the same count — rather than as a literal,
    which would just be this arithmetic typed twice."""
    cmd = ev.build_decode_cmd(_spec(), 1920, 1080, 59.94)
    assert cmd[0] == ev.FFMPEG
    # pre-input seek (fast) is BEFORE -i
    assert cmd.index("-ss") < cmd.index("-i")
    assert "/in/src.MP4" in cmd
    assert any(a == "fps=59.940000,scale=1920:1080" for a in cmd)
    planned = len(ev.frame_times(100.0, 170.0, 59.94))
    asked = float(cmd[cmd.index("-t") + 1])
    assert abs(asked * 59.94 - planned) < 1e-3, (
        f"the decode asks for {asked * 59.94:.3f} frames against a plan of {planned}")
    assert cmd[cmd.index("-frames:v") + 1] == str(planned)
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
    (same t0, same length), so audio and the composited video cover the identical lap span.

    And the audio's `-t` is an INPUT option — it must sit before the `-i` it belongs to. After the
    `-i` it is an output cap, which is what let `-shortest` drop the last video frame of every
    fractional window (see build_encode_cmd); the two placements are one argument apart and mean
    entirely different things, so the POSITION is pinned here and not just the value."""
    spec = _spec(t0=12.5, t1=80.0)
    dec = ev.build_decode_cmd(spec, 640, 360, 30.0)
    enc = ev.build_encode_cmd(spec, 640, 360, 30.0)
    # the LAST -ss in each is the source seek; both -t cover the same planned clip
    assert dec[dec.index("-ss") + 1] == f"{12.5:.6f}"
    assert enc[enc.index("-ss") + 1] == f"{12.5:.6f}"
    assert dec[dec.index("-t") + 1] == enc[enc.index("-t") + 1]
    assert float(enc[enc.index("-t") + 1]) >= 67.5, "the clip may be rounded UP to a frame, never down"
    t_at = enc.index("-t")
    assert enc[t_at - 2] == "-ss", "the audio -t belongs with the seek, in front of its input"
    assert "-i" in enc[t_at:], "the audio -t must precede the -i it bounds (an input option)"
    assert "-t" not in enc[enc.index("/in/src.MP4"):], (
        "a -t after the input is an OUTPUT cap — that is the frame-dropping bug, not the fix")


# --------------------------------------------------------- chaptered source resolution (the F9 bug)
# A chaptered recording lays N files on ONE global media clock — chapter i covers
# [offset_i, offset_i+dur_i). A lap's window is a GLOBAL window, but ffmpeg `-ss` seeks into a
# SINGLE file's LOCAL clock. The bug these tests pin: the export used to seek a GLOBAL t0 into the
# FIRST chapter file, which lands PAST that file's end for any lap outside chapter 1 -> zero frames
# -> an empty progress bar. The fix resolves the global window to the correct chapter file + local
# offset (or a concat over a seam), reusing the ChapterMap the video player seeks with.
def _chapter_map_3x(d=1000.0):
    """A synthetic 3-chapter map: ch0 [0,d), ch1 [d,2d), ch2 [2d,3d) — the D24 shape in miniature."""
    return chapters.ChapterMap(["/v/GX010001.MP4", "/v/GX020001.MP4", "/v/GX030001.MP4"],
                               [d, d, d])


def test_resolve_source_non_first_chapter_picks_right_file_and_offset():
    """THE REGRESSION (the user's lap 36): a window wholly inside the SECOND chapter must resolve to
    the SECOND chapter file with time_offset = that chapter's global offset, so the file-LOCAL seek
    is `global - offset` (NOT the global t0 into chapter 1, which decoded zero frames)."""
    cm = _chapter_map_3x(1000.0)
    # a window at global 1500..1560 -> chapter 1 (the 2nd file), local 500..560
    src = ev.resolve_video_source(cm, 1500.0, 1560.0)
    assert src.probe_path == "/v/GX020001.MP4", "must point at the chapter the window falls in"
    assert src.time_offset == 1000.0, "offset must be the chapter's global start"
    assert src.concat_list_path is None, "a single-chapter window is NOT a concat source"
    assert src.input_args() == ["-i", "/v/GX020001.MP4"]
    # and a window deep in the THIRD chapter:
    src3 = ev.resolve_video_source(cm, 2500.0, 2560.0)
    assert src3.probe_path == "/v/GX030001.MP4" and src3.time_offset == 2000.0


def test_spec_local_t0_is_global_minus_offset():
    """ExportSpec.local_t0 (what ffmpeg `-ss` gets) is the global t0 shifted into the resolved
    source's own clock — the crux of the fix. A global window of 1500..1560 in chapter 1 (offset
    1000) seeks the file at LOCAL 500, and the decode/encode argv carry that local seek (not 1500)."""
    cm = _chapter_map_3x(1000.0)
    src = ev.resolve_video_source(cm, 1500.0, 1560.0)
    spec = ev.ExportSpec(out_path="/out.mp4", lap_id=7, t0=1500.0, t1=1560.0, source=src)
    assert spec.local_t0 == 500.0
    assert spec.duration == 60.0
    dec = ev.build_decode_cmd(spec, 640, 360, 30.0)
    enc = ev.build_encode_cmd(spec, 640, 360, 30.0)
    # the seek is the LOCAL time, and BOTH commands read the SECOND chapter file
    assert dec[dec.index("-ss") + 1] == f"{500.0:.6f}"
    assert enc[enc.index("-ss") + 1] == f"{500.0:.6f}"
    assert "/v/GX020001.MP4" in dec and "/v/GX020001.MP4" in enc
    # and crucially NOT the first chapter file with the global t0 (the old bug)
    assert "/v/GX010001.MP4" not in dec
    assert f"{1500.0:.6f}" not in dec


def test_resolve_source_seam_uses_concat_over_spanned_chapters():
    """A lap that crosses a chapter SEAM resolves to a CONCAT demuxer over exactly the spanned
    chapters, with time_offset = the FIRST spanned chapter's offset — the SAME shift the
    single-chapter branch uses, so `local = global - offset` addresses the same instant either way
    and one accurate `-ss` serves both. Every entry declares its `duration`: that is what makes the
    span seekable at all (without it ffmpeg cannot seek a concat input and decodes the span from its
    start — 663 s against 1.2 s for half a second of D24 picture), and it pins the concat clock to
    the numbers ChapterMap's offsets are built from.

    NO `inpoint`. Trimming the first chapter to the lap made the stream begin at the lap and both
    commands seek `-ss 0` — but `inpoint` is keyframe-granular, so the picture began up to a GOP
    BEFORE t0 while `frame_times` stamped every overlay frame from t0. Measured on D24 lap 22:
    -0.956 s of picture, and -0.977 s of audio with it."""
    cm = _chapter_map_3x(1000.0)
    # window 980..1040 spans ch0 (ends at 1000) into ch1 -> concat [ch0, ch1], stream clock starting
    # at ch0's global offset (0) => local seek 980, the same number a single-chapter ch0 window gets.
    src = ev.resolve_video_source(cm, 980.0, 1040.0)
    try:
        assert src.concat_list_path is not None, "a seam-crossing window must be a concat source"
        assert src.time_offset == 0.0, "the span's clock starts at the FIRST spanned chapter"
        ia = src.input_args()
        assert ia[:5] == ["-f", "concat", "-safe", "0", "-i"] and ia[5] == src.concat_list_path
        listing = open(src.concat_list_path).read()
        assert "GX010001.MP4" in listing and "GX020001.MP4" in listing, "lists the 2 spanned chapters"
        assert "GX030001.MP4" not in listing, "does NOT list the un-spanned 3rd chapter"
        assert "inpoint" not in listing, "a keyframe-granular inpoint desyncs the overlay"
        assert listing.count("duration 1000.000000") == 2, (
            f"every spanned file must declare its duration or the span is unseekable:\n{listing}")
        # the seek is the real offset into the span, and BOTH commands carry it
        spec = ev.ExportSpec(out_path="/out.mp4", lap_id=9, t0=980.0, t1=1040.0, source=src)
        assert spec.local_t0 == 980.0
        dec = ev.build_decode_cmd(spec, 640, 360, 30.0)
        enc = ev.build_encode_cmd(spec, 640, 360, 30.0)
        assert dec[dec.index("-ss") + 1] == f"{980.0:.6f}"
        assert enc[enc.index("-ss") + 1] == f"{980.0:.6f}"
    finally:
        src.cleanup()


def test_resolve_source_seam_omits_durations_when_a_chapter_has_none():
    """A ChapterMap built WITHOUT media durations (0.0) must not have `duration 0.000000` written
    into the list — that would declare a zero-length file and mis-time the whole span. The span
    falls back to no directives: unseekable, so ffmpeg decodes its way down to the seek, which is
    slow and lands on the right frame anyway (measured exact-pixel on a synthetic span). Slow and
    right beats fast and wrong. Such a map's global offsets are already degenerate."""
    cm = chapters.ChapterMap(["/v/GX010001.MP4", "/v/GX020001.MP4"], [0.0, 0.0])
    src = ev.resolve_video_source(cm, 0.0, 10.0)
    try:
        # both chapters are zero-length, so chapter_at(9.999999) is the LAST one -> a real span
        assert src.concat_list_path is not None
        listing = open(src.concat_list_path).read()
        assert "duration" not in listing, listing
        assert listing.count("file '") == 2, listing
    finally:
        src.cleanup()


def test_resolve_source_window_ending_exactly_on_seam_stays_one_chapter():
    """A window that ENDS exactly on a chapter boundary (half-open) must NOT pull in the next
    chapter: the end is nudged back so a lap ending at offset_{k+1} resolves to a single file."""
    cm = _chapter_map_3x(1000.0)
    src = ev.resolve_video_source(cm, 940.0, 1000.0)  # ends exactly at the ch0/ch1 seam
    try:
        assert src.concat_list_path is None, "ending on the seam should stay within chapter 0"
        assert src.probe_path == "/v/GX010001.MP4" and src.time_offset == 0.0
    finally:
        src.cleanup()


def test_build_lap_spec_resolves_chaptered_source_from_session():
    """app.py builds the export spec via build_lap_spec, which reads `session.chapters` (a
    ChapterMap) to resolve the source. A StubSession carrying a 3-chapter map + a non-first-chapter
    lap window must yield a spec whose source is the right chapter file + local offset — the exact
    path the GUI takes for the user's lap 36."""
    s = StubSession(lap_id=42, t0=1500.0, dur=60.0, n=120)
    s.chapters = _chapter_map_3x(1000.0)           # lap 42 window 1500..1560 -> chapter 1
    spec = ev.build_lap_spec(s, "/out.mp4", 42, config=ev.OverlayConfig(out_height=360))
    try:
        assert spec.source.probe_path == "/v/GX020001.MP4"
        assert spec.source.time_offset == 1000.0
        assert spec.local_t0 == 500.0
        assert spec.t0 == 1500.0 and spec.t1 == 1560.0   # the spec keeps the GLOBAL window
    finally:
        spec.source.cleanup()


def test_build_lap_spec_single_file_session_unchanged():
    """A plain single-file session (no ChapterMap) still resolves to that one file at offset 0, so
    the legacy single-file export is byte-for-byte unchanged (global == local)."""
    s = StubSession(lap_id=2, t0=100.0, dur=60.0, n=120)
    s.video_path = "/v/hero6.mp4"                   # no .chapters attr set -> single-file path
    spec = ev.build_lap_spec(s, "/out.mp4", 2)
    assert spec.source.probe_path == "/v/hero6.mp4"
    assert spec.source.time_offset == 0.0 and spec.local_t0 == 100.0
    assert spec.source.concat_list_path is None


def test_guard_refuses_window_past_source_end(monkeypatch_restore):
    """The up-front window guard REFUSES a window whose source-local seek lands at/after the source
    duration (a window that would decode zero frames) with a clear ValueError — instead of launching
    a doomed ffmpeg that sits on an empty bar (the exact 2-minute-hang failure mode). Stubs the
    duration probe so no real ffprobe runs."""
    ev.probe_source_duration = lambda _s: 1000.0          # type: ignore[assignment]
    src = ev.single_file_source("/v/GX010001.MP4")
    spec = ev.ExportSpec(out_path="/out.mp4", lap_id=1, t0=1500.0, t1=1560.0, source=src)
    try:
        ev.guard_validate_window(spec)
        raise AssertionError("expected ValueError for a past-end window")
    except ValueError as e:
        assert "past the end" in str(e).lower() or "duration" in str(e).lower()
    # a window comfortably inside the duration passes
    ok = ev.ExportSpec(out_path="/out.mp4", lap_id=1, t0=100.0, t1=160.0, source=src)
    ev.guard_validate_window(ok)   # must not raise


def test_guard_refuses_empty_window():
    """A degenerate (empty) window is refused before any ffmpeg — duration <= 0 means nothing to
    render."""
    src = ev.single_file_source("/v/x.MP4")
    spec = ev.ExportSpec(out_path="/out.mp4", lap_id=1, t0=50.0, t1=50.0, source=src)
    try:
        ev.guard_validate_window(spec)
        raise AssertionError("expected ValueError for an empty window")
    except ValueError as e:
        assert "empty" in str(e).lower()


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

    def __init__(self, frame_bytes=0, nframes=0, is_decoder=False,
                 frame_delay=0.0, slow_at=None, slow_delay=0.0):
        self.returncode = 0
        self.stdout = None
        self.stdin = None
        self.killed = False
        self._is_decoder = is_decoder
        # A decoder that takes REAL TIME to hand over a frame, so a test can exercise the stall
        # watchdog against a render that is slow rather than wedged. `slow_at` makes exactly one
        # frame take `slow_delay` — the shape of a real hiccup (the worst single gap measured on
        # D24 was 0.783 s against a 0.018-0.064 s median).
        self._nframes = nframes
        self._frame_delay = frame_delay
        self._slow_at = slow_at
        self._slow_delay = slow_delay
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
        # A KILLED DECODER STOPS PRODUCING, and without this a mocked render cannot observe an
        # abort at all: the supervisor's `kill()` only set a flag here, the fake went on serving
        # frames, the loop never took the short-read branch, and `_raise_if_aborted` — the one
        # place a stall becomes a typed error — was never reached. The render simply finished.
        # Real ffmpeg dies and its pipe reads EOF, which is what this reproduces.
        if self.killed or so._left <= 0:
            return b""
        index = self._nframes - so._left
        so._left -= 1
        delay = self._slow_delay if index == self._slow_at else self._frame_delay
        if delay:
            time.sleep(delay)
        return bytes(so._fb)

    def communicate(self, *a, **k):
        return (b"", b"")

    def wait(self, *a, **k):
        return 0

    def kill(self):
        self.killed = True


def _patch_pipeline(monkeypatch_targets, frame_bytes, nframes,
                    frame_delay=0.0, slow_at=None, slow_delay=0.0):
    """Install fake decode/encode Popen + a fake probe so a Renderer runs with no real ffmpeg.
    Returns the dict so the caller can inspect the encoder's captured bytes.

    `frame_delay` / `slow_at` / `slow_delay` make the fake DECODER take real time per frame (see
    `_FakeProc`), which is what lets the stall-watchdog tests drive a slow-but-healthy render."""
    state = {}

    def fake_popen(cmd, **kw):
        # the decode cmd ends with pipe:1, the encode cmd starts reading pipe:0
        is_decoder = cmd[-1] == "pipe:1"
        proc = _FakeProc(frame_bytes=frame_bytes, nframes=nframes, is_decoder=is_decoder,
                         frame_delay=frame_delay if is_decoder else 0.0,
                         slow_at=slow_at if is_decoder else None,
                         slow_delay=slow_delay)
        state["decoder" if is_decoder else "encoder"] = proc
        return proc

    ev.subprocess.Popen = fake_popen                      # type: ignore[assignment]
    ev.probe_video_size = lambda _p: (3840, 2160, 60.0)   # type: ignore[assignment]
    # The up-front window guard ffprobes the source DURATION; stub it (a generous duration) so the
    # mocked render never shells out to real ffprobe and the guard passes for these in-window specs.
    ev.probe_source_duration = lambda _s: 1.0e9           # type: ignore[assignment]
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
    """End-to-end on real media (a FOOTAGE_CHECK: reported SKIPPED without ffmpeg + a recording).
    Renders a SHORT 2 s window of the best lap at 360p and asserts the output is a non-empty valid
    file with a couple of frames."""
    import tempfile
    real = _real_media("real_render_smoke")
    from studio.session import Session
    s = Session.load([real])
    best = s.best_lap_id()
    t0, _ = s.lap_window(best)
    with tempfile.TemporaryDirectory(prefix="pacer-f9-smoke-") as tmp:
        out = os.path.join(tmp, "smoke.mp4")
        spec = ev.ExportSpec(src_path=real, out_path=out, lap_id=best, t0=t0, t1=t0 + 2.0,
                             config=ev.OverlayConfig(out_height=360))
        res = ev.Renderer(s, spec).run()
        assert res.frames > 30                                   # ~120 at 60 fps
        assert os.path.getsize(out) > 0
        w, h, _ = ev.probe_video_size(out)
        assert h == 360
    print(f"real_render_smoke OK ({res.frames} frames of lap {best} of {os.path.basename(real)})")


def test_real_chaptered_non_first_chapter_render_if_media():
    """THE BUG, end-to-end — a FOOTAGE_CHECK on a CHAPTERED recording (reported SKIPPED without
    ffmpeg + one). Loads the FULL chaptered recording (the named file + its siblings), finds a lap
    whose GLOBAL window falls OUTSIDE the first chapter, and renders a SHORT window of it.

    This is the exact case the old code broke: it seeked the global t0 into the FIRST chapter file,
    which is past that file's end for a non-first-chapter lap -> zero frames -> an empty progress
    bar. The fix resolves the window to the right chapter file at the file-LOCAL offset, so this
    must produce REAL frames. We ALSO assert the resolved source is NOT the first chapter file +
    that its local seek is the global-minus-offset (the precise gap)."""
    import tempfile
    real = _real_media("real_chaptered_non_first_chapter_render")
    sibs = chapters.discover_siblings(real)
    if len(sibs) < 2:
        raise _footage.FootageMissing(f"{real} is a single-chapter recording; this check needs a "
                                      "chaptered one")
    from studio.session import Session
    s = Session.load(sibs)
    cm = s.chapters
    assert cm is not None and cm.is_multi
    first_ch_end = cm.chapters[0].offset + cm.chapters[0].duration
    # pick a valid lap whose window lies wholly inside a LATER chapter (not the first, not a seam)
    target = None
    for lap in s.valid_lap_ids():
        win = s.lap_window(lap)
        if win is None:
            continue
        t0, t1 = win
        i0 = cm.chapter_at(t0)
        i1 = cm.chapter_at(max(t0, t1 - 1e-6))
        if i0 == i1 and i0 >= 1 and t0 > first_ch_end:
            target = (lap, t0, t1, i0)
            break
    assert target is not None, "expected at least one lap inside a non-first chapter"
    lap, t0, t1, ci = target
    # resolve the source and assert the precise gap: right chapter file + file-local seek offset
    src = ev.resolve_video_source(cm, t0, t1)
    assert src.probe_path == cm.chapters[ci].path, "must point at the chapter the lap falls in"
    assert src.probe_path != cm.chapters[0].path, "must NOT be the first chapter file (the old bug)"
    assert abs(src.time_offset - cm.chapters[ci].offset) < 1e-6
    tmp = tempfile.TemporaryDirectory(prefix="pacer-f9-chaptered-")
    out = os.path.join(tmp.name, "chaptered.mp4")
    spec = ev.ExportSpec(out_path=out, lap_id=lap, t0=t0, t1=min(t1, t0 + 2.0),
                         source=src, config=ev.OverlayConfig(out_height=360))
    # the file-local seek lands inside the chapter (well before its end), not past EOF
    assert spec.local_t0 < cm.chapters[ci].duration
    try:
        res = ev.Renderer(s, spec).run()
        assert res.frames > 30, f"a non-first-chapter lap must render REAL frames, got {res.frames}"
        assert os.path.getsize(out) > 0
        w, h, _ = ev.probe_video_size(out)
        assert h == 360
    finally:
        src.cleanup()
        tmp.cleanup()
    print(f"real_chaptered_non_first_chapter_render OK (lap {lap} in chapter {ci}, {res.frames} frames)")


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
    if not _require_ffmpeg("real_synthetic_pipe_render"):
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
    h264_videotoolbox (proof the Apple media engine, not libx264, produced it). Its own
    registration, `videotoolbox.<name>`: SKIPPED by name where VT isn't usable (CI), never a pass."""
    _videotoolbox.need(ev.ffmpeg_available() and ev.videotoolbox_usable(),
                       "ffmpeg with an H.264 VideoToolbox session")
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
    if not _require_ffmpeg("real_fallback_to_libx264"):
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    src, out = os.path.join(tmp, "f9_fb_src.mp4"), os.path.join(tmp, "f9_fb_out.mp4")
    _make_syn_clip(src)
    # Force VT for the first attempt but honour the retry's libx264 choice; keep decode in software.
    ev.resolve_encoder = lambda c: (                        # type: ignore[assignment]
        ev.VT_H264 if str(c).lower() in ("videotoolbox", "auto") else ev.SW_H264)
    ev.videotoolbox_decode_available = lambda: False        # type: ignore[assignment]
    _orig_codec = ev._video_codec_args

    def broken_vt(encoder, w, h, fps, quality=ev._DEFAULT_QUALITY):
        if encoder == ev.VT_H264:
            return ["-c:v", ev.VT_H264, "-b:v", "-5"]       # invalid bitrate -> VT exits non-zero
        return _orig_codec(encoder, w, h, fps, quality)
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


def test_composite_is_deterministic_across_workers_if_ffmpeg(monkeypatch_restore):
    """DETERMINISM: `workers=1` is the serial pump and anything else the pipelined one (the read and
    the write on threads of their own; the paint stays on one). Rendering the same clip both ways
    must produce BYTE-IDENTICAL frames. Gated on ffmpeg; no media file. (Guards the composite stays
    stable + frame-exact whichever pump runs it.)"""
    if not _require_ffmpeg("composite_is_deterministic_across_workers"):
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
        assert h1 == h4, f"composite differs across the workers knob: {h1} != {h4}"
    finally:
        for p in (src, o1, o4):
            if os.path.exists(p):
                os.remove(p)
    print("composite_is_deterministic_across_workers OK")


# ----------------------------------- GUI-worker path + watchdog/cancel regression (the missed gap)
def test_gui_worker_drives_render_to_completion_if_ffmpeg():
    """REGRESSION — the test gap that let the GUI hang slip past three fixes: drive the ACTUAL GUI
    export worker (studio.workers.VideoExportWorker, a QThread) end-to-end on a tiny real clip and
    assert it COMPLETES (the dialog would reach 100%) without hanging. The worker calls
    Renderer.run(progress=…, cancel=…) — the exact path File ▸ 'Export overlay video…' triggers —
    which the mocked suite and the headless `run()` benchmarks never exercised to completion. A
    deadlock/wedge HANGS here and the runner's outer time budget catches it. Gated on ffmpeg; no
    media file."""
    if not _require_ffmpeg("gui_worker_drives_render_to_completion"):
        return
    from PySide6.QtCore import QEventLoop, QTimer

    from studio.workers import VideoExportWorker

    tmp = os.environ.get("TMPDIR", "/tmp")
    src, out = os.path.join(tmp, "f9_gui_src.mp4"), os.path.join(tmp, "f9_gui_out.mp4")
    _make_syn_clip(src, dur=1.5)
    if os.path.exists(out):
        os.remove(out)
    s = StubSession(lap_id=1, t0=0.0, dur=1.5, n=90)
    # Force libx264 so this regression is DETERMINISTIC (no media-engine session churn / contention
    # flakiness across the suite's many real renders). The VideoToolbox path is covered separately
    # by test_real_videotoolbox_render; what THIS test guards is the GUI worker → Renderer.run →
    # run_chunk → watchdog/supervisor wiring driving an export to completion without hanging.
    spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=1.5,
                         config=ev.OverlayConfig(out_height=360, encoder="libx264",
                                                 hwaccel_decode=False))
    worker = VideoExportWorker(s, spec)
    result = {"ok": None, "msg": None, "last": (0, 0)}
    loop = QEventLoop()
    worker.progress.connect(lambda d, t: result.update(last=(d, t)))

    def done(ok, msg):
        result.update(ok=ok, msg=msg)
        loop.quit()
    worker.finished_export.connect(done)
    # Hard safety net: if the worker HANGS, quit the loop after 90 s so the test FAILS (not hangs).
    QTimer.singleShot(90_000, loop.quit)
    worker.start()
    loop.exec()
    worker.wait(5000)
    assert result["ok"] is True, f"GUI worker did not finish OK: ok={result['ok']} msg={result['msg']}"
    d, t = result["last"]
    assert t > 0 and d == t, f"progress did not reach 100%: {d}/{t}"
    assert os.path.getsize(out) > 0
    w, h, _ = ev.probe_video_size(out)
    assert h == 360 and w == 640
    for p in (src, out):
        if os.path.exists(p):
            os.remove(p)
    print(f"gui_worker_drives_render_to_completion OK ({d}/{t} frames)")


def test_watchdog_aborts_a_wedged_encoder_if_ffmpeg(monkeypatch_restore):
    """REGRESSION — the no-progress WATCHDOG makes an infinite hang impossible: a render whose
    encoder WEDGES (stops draining our pipe but never exits, like a stuck VideoToolbox session)
    must be ABORTED within the watchdog window, not hang forever. We force libx264 (so there is no
    VT retry to a *second* wedge) with a SHORT watchdog, then swap the real encoder for a process
    that never reads stdin; the writer blocks, the supervisor kills it, and run() raises a clear
    RuntimeError (wrapping RenderTimeoutError) well inside the test budget. Gated on ffmpeg."""
    if not _require_ffmpeg("watchdog_aborts_a_wedged_encoder"):
        return
    import time as _time
    tmp = os.environ.get("TMPDIR", "/tmp")
    src, out = os.path.join(tmp, "f9_wd_src.mp4"), os.path.join(tmp, "f9_wd_out.mp4")
    _make_syn_clip(src, dur=2.0)
    s = StubSession(lap_id=1, t0=0.0, dur=2.0, n=120)
    # libx264 + a 3 s watchdog; the wedge has to trip the stall guard, not a fallback.
    spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=2.0,
                         config=ev.OverlayConfig(out_height=240, encoder="libx264",
                                                 hwaccel_decode=False, fps_cap=None,
                                                 watchdog_timeout=3.0))
    r = ev.Renderer(s, spec)
    real_start = r._start

    def wedge_start():
        real_start()
        # Replace the encoder with a process that NEVER reads its stdin -> the pipe fills and the
        # writer blocks forever (a stand-in for a wedged encode session).
        try:
            r._enc.terminate()
            r._enc.wait(timeout=2)
        except Exception:
            pass
        r._enc = ev.subprocess.Popen(["sleep", "120"], stdin=ev.subprocess.PIPE,
                                     stderr=ev.subprocess.DEVNULL)
    r._start = wedge_start
    t0 = _time.monotonic()
    raised = None
    try:
        r.run(progress=lambda d, t: None)
    except BaseException as e:  # noqa: BLE001
        raised = e
    dt = _time.monotonic() - t0
    try:
        if r._enc is not None:
            r._enc.kill()
    except Exception:
        pass
    if os.path.exists(src):
        os.remove(src)
    if os.path.exists(out):
        os.remove(out)
    assert raised is not None, "a wedged encoder must NOT hang — run() should have raised"
    assert isinstance(raised, RuntimeError), f"expected RuntimeError, got {type(raised).__name__}"
    assert "stall" in str(raised).lower() or "no frame" in str(raised).lower(), \
        f"error should explain the stall, got: {raised}"
    assert dt < 30, f"watchdog took too long to fire ({dt:.1f}s > 30s)"
    print(f"watchdog_aborts_a_wedged_encoder OK (aborted in {dt:.1f}s)")


def test_cancel_mid_write_does_not_hang_if_ffmpeg(monkeypatch_restore):
    """REGRESSION — cancel must work even when the loop is blocked on a pipe write: a cancel() that
    flips True while the writer is stuck on stdin.write (encoder not draining) is detected by the
    supervisor, which kills the processes so the blocked write returns and CancelledError is raised
    promptly. (The old code only polled cancel BETWEEN frames, so a cancel during a wedged write
    could not stop it.) Gated on ffmpeg."""
    if not _require_ffmpeg("cancel_mid_write_does_not_hang"):
        return
    import time as _time
    tmp = os.environ.get("TMPDIR", "/tmp")
    src, out = os.path.join(tmp, "f9_cm_src.mp4"), os.path.join(tmp, "f9_cm_out.mp4")
    _make_syn_clip(src, dur=2.0)
    s = StubSession(lap_id=1, t0=0.0, dur=2.0, n=120)
    # Long watchdog so the CANCEL (not the stall) is what stops it; libx264 to avoid VT retry.
    spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=2.0,
                         config=ev.OverlayConfig(out_height=240, encoder="libx264",
                                                 hwaccel_decode=False, fps_cap=None,
                                                 watchdog_timeout=60.0))
    r = ev.Renderer(s, spec)
    real_start = r._start

    def wedge_start():
        real_start()
        try:
            r._enc.terminate()
            r._enc.wait(timeout=2)
        except Exception:
            pass
        r._enc = ev.subprocess.Popen(["sleep", "120"], stdin=ev.subprocess.PIPE,
                                     stderr=ev.subprocess.DEVNULL)
    r._start = wedge_start
    flag = {"cancel": False}
    # flip cancel True shortly after the render starts (it will already be blocked on the write)
    import threading as _th
    _th.Timer(2.0, lambda: flag.update(cancel=True)).start()
    t0 = _time.monotonic()
    raised = None
    try:
        r.run(cancel=lambda: flag["cancel"])
    except BaseException as e:  # noqa: BLE001
        raised = e
    dt = _time.monotonic() - t0
    try:
        if r._enc is not None:
            r._enc.kill()
    except Exception:
        pass
    if os.path.exists(src):
        os.remove(src)
    if os.path.exists(out):
        os.remove(out)
    assert isinstance(raised, ev.CancelledError), \
        f"a mid-write cancel must raise CancelledError, got {type(raised).__name__}: {raised}"
    assert dt < 20, f"cancel took too long to take effect ({dt:.1f}s)"
    print(f"cancel_mid_write_does_not_hang OK (cancelled in {dt:.1f}s)")


# --------------------------------------------------------------------------- export restyle: palette
def test_export_palette_is_vivid_and_opaque():
    """The EXPORT palette (used to burn overlays over BRIGHT footage) is distinct from the dim live
    theme: pure-white text, fully-opaque vivid colours. A regression guard so the export doesn't
    silently revert to the washed-out theme tokens."""
    from studio.theme import C
    assert ev.EXPORT.text == "#FFFFFF"                       # pure white, not the dim theme off-white
    assert ev.EXPORT.text != C.text
    # the export ahead/behind are punchier than the theme's softer pair
    assert ev.EXPORT.ahead != C.ahead and ev.EXPORT.behind != C.behind


def test_export_delta_colour_three_way():
    """export_delta_colour follows the SAME ahead/behind/neutral rule as theme.delta_colour (shared
    dead band) but always returns a concrete EXPORT colour (neutral white for no/even Δ), since the
    burned text is never a widget's neutral foreground."""
    from studio.theme import DELTA_EVEN_EPS_S
    assert ev.export_delta_colour(-0.20) == ev.EXPORT.ahead   # ahead -> vivid green
    assert ev.export_delta_colour(+0.20) == ev.EXPORT.behind  # behind -> vivid red
    assert ev.export_delta_colour(None) == ev.EXPORT.neutral  # no Δ -> neutral white
    assert ev.export_delta_colour(DELTA_EVEN_EPS_S / 2) == ev.EXPORT.neutral  # dead-even -> neutral


# --------------------------------------------------------------------------- shared-source dedup (F6)
def _old_export_delta_colour(d):
    """The PRE-refactor inlined export Δ-colour rule (reimplemented dead band + palette map) — the
    reference the now-delegating export_delta_colour must reproduce EXACTLY across a Δ sweep."""
    from studio.theme import DELTA_EVEN_EPS_S
    if d is None or abs(d) <= DELTA_EVEN_EPS_S:
        return ev.EXPORT.neutral
    return ev.EXPORT.ahead if d < 0 else ev.EXPORT.behind


def test_export_delta_colour_equivalent_to_old_inlined_rule():
    """REGRESSION GUARD for the F6 dedup: the export Δ colour now DELEGATES the ahead/behind/even
    decision to theme.delta_colour (the single rule source) and only re-tones it to the vivid EXPORT
    palette. It must return what the old inlined reimplementation did across the full Δ sweep — incl.
    exactly 0, ±eps either side of the dead band, and large +/- — so the burned cue is unchanged."""
    from studio.theme import DELTA_EVEN_EPS_S as eps
    sweep = [None, -5.0, -0.20, -eps * 1.01, -eps, -eps * 0.99, 0.0,
             eps * 0.99, eps, eps * 1.01, 0.20, 5.0]
    for d in sweep:
        assert ev.export_delta_colour(d) == _old_export_delta_colour(d), f"Δ={d}"


def _old_diff_box_text_and_colour(d, sp, lap_id):
    """The live #DiffBox formatter as a standalone reference (originally a verbatim copy of the old
    app._update_diff_box) — the byte-for-byte contract theme.format_delta_speed must reproduce.
    Updated for the accessibility ▲/▼ direction arrow now paired with the signed Δ (the NON-COLOUR
    redundancy so ahead/behind survives greyscale); the even dead-band still emits NO arrow, so the
    neutral readout is unchanged."""
    from studio import theme
    if d is None:
        delta_txt = "Δ —"
    else:
        arrow = theme.delta_arrow(d)
        delta_txt = f"Δ {d:+.2f} s" + (f" {arrow}" if arrow else "")
    speed_txt = f"{sp:.0f} km/h" if (sp is not None and lap_id is not None) else "— km/h"
    colour = theme.delta_colour(d) or theme.C.text
    return f"{delta_txt}     {speed_txt}", colour


def test_format_delta_speed_reproduces_old_live_diff_box():
    """LIVE BYTE-IDENTITY: theme.format_delta_speed (the shared source the live #DiffBox + the export
    now both read) reproduces the EXACT text + colour the old inlined app._update_diff_box produced,
    across a sweep of (d, speed, lap) — incl. the no-lap "— km/h" honesty rule, an exact-0 / dead-even
    Δ (neutral text colour), and ahead/behind colours. If this drifts the live hero readout changed,
    which the refactor must not do."""
    from studio import theme
    cases = [
        (None, None, None),        # no lap at all -> "Δ —     — km/h"
        (None, 73.4, None),        # speed known but NO lap -> honest "— km/h"
        (None, 73.4, 2),           # lap but no Δ baseline
        (0.0, 73.4, 2),            # dead-even -> neutral text colour, "Δ +0.00 s"
        (0.004, 73.4, 2),          # within dead band -> neutral
        (-0.31, 88.0, 2),          # ahead -> green
        (0.62, 64.0, 2),           # behind -> red
        (-2.5, 100.0, 5),          # large ahead
    ]
    for d, sp, lap in cases:
        text, sem = theme.format_delta_speed(d, sp, lap)
        colour = sem or theme.C.text                       # the live call-site resolves None -> text
        old_text, old_colour = _old_diff_box_text_and_colour(d, sp, lap)
        assert text == old_text, f"text drift for {(d, sp, lap)}: {text!r} != {old_text!r}"
        assert colour == old_colour, f"colour drift for {(d, sp, lap)}"


def test_format_delta_speed_exact_strings_and_spacing():
    """Pin the exact live readout strings (incl. the FIVE-space gap between the Δ run and the speed
    run, the "Δ +0.00 s" units form, and the em-dash no-lap forms) so a stray space/format change is
    caught even if the inlined reference above were also edited."""
    from studio import theme
    assert theme.format_delta_speed(None, None, None)[0] == "Δ —     — km/h"
    # Even Δ (dead-band): no direction arrow, so the neutral readout is unchanged.
    assert theme.format_delta_speed(0.0, 73.4, 2)[0] == "Δ +0.00 s     73 km/h"
    # Ahead/behind carry the accessibility ▲/▼ arrow (non-colour redundancy) after the signed value.
    assert theme.format_delta_speed(-0.31, 88.0, 2)[0] == "Δ -0.31 s ▲     88 km/h"
    assert theme.format_delta_speed(0.62, 64.0, 2)[0] == "Δ +0.62 s ▼     64 km/h"
    # export-side fragments: tight Δ run (no " s", no arrow — the burned overlay passes arrow=False),
    # bare speed number under the SAME no-lap gate.
    assert theme.format_delta_run(-0.31, units=False, arrow=False) == "Δ -0.31"
    assert theme.format_delta_run(None, units=False, arrow=False) == "Δ —"
    assert theme.speed_number(73.4, 2) == "73"
    assert theme.speed_number(73.4, None) == "—"        # no lap -> em dash (the honesty rule)
    assert theme.speed_number(None, 2) == "—"


def test_paint_strip_pulls_shared_semantics():
    """The export's Δ sources its colour from the shared theme.delta_colour decision (via
    export_delta_colour) — a light spy guard so the painter can't silently fork the Δ rule again.

    The Δ moved out of `_paint_readout` and into `_paint_strip`, after the elapsed time: a Δ is a
    TIME measurement and sat in the SPEED box, where it could only read as a qualifier on the
    speed. So the spy follows it — and the readout is asserted to have stopped calling the rule at
    all, which is what proves the run really left rather than being drawn twice."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    class _Win:
        @staticmethod
        def lap_window(_lap):
            return (100.0, 160.0)

    seen = []
    orig = ev.export_delta_colour
    ev.export_delta_colour = lambda d, palette=None: seen.append(d) or orig(d, palette)
    try:
        img = QImage(400, 200, QImage.Format_RGB888)
        img.fill(0)
        p = QPainter(img)
        vals = ev.OverlayValues(t=130.0, lap_id=2, speed_kmh=88.0, delta_s=-0.31,
                                g=None, marker_index=10)
        ev._paint_readout(p, QRectF(10, 150, 260, 44), vals)
        assert seen == [], "the readout must not draw the Δ any more; it moved to the lap strip"
        ev._paint_strip(p, QRectF(10, 10, 260, 44), _Win(), vals, 100.0)
        p.end()
    finally:
        ev.export_delta_colour = orig
    assert seen == [-0.31]                                # the painter routed Δ through the shared rule


def test_the_best_lap_strip_drops_the_delta_and_says_why():
    """The export defaults to the best lap and `delta_at_lap` compares a lap against its own curve,
    so the shared MP4 burned `Δ +0.00` from the first frame to the last with nothing saying why.

    `strip_tail` is the single decision: on a best-lap export it returns the `★ BEST` mark and the
    Δ is not composed AT ALL (asserted through the same spy — a Δ that is computed and then not
    drawn would still be a Δ the code believes in). Every other lap is unchanged, formatter and
    colour rule included."""
    from studio import theme
    seen = []
    orig = ev.export_delta_colour
    ev.export_delta_colour = lambda d, palette=None: seen.append(d) or orig(d, palette)
    try:
        best_text, best_colour = ev.strip_tail(0.0, is_best=True)
        assert seen == [], "a best-lap export must not even ASK for a Δ colour"
        assert best_text == ev._BEST_MARK == "★ BEST"
        assert best_colour == ev.EXPORT.accent
        # the ordinary lap: the shared tight run, the shared colour rule, arrow=False kept
        text, colour = ev.strip_tail(-0.31, is_best=False)
        assert text == theme.format_delta_run(-0.31, units=False, arrow=False) == "Δ -0.31"
        assert colour == ev.export_delta_colour(-0.31)
        # and theme's dead band still reaches the file (a -0.00 must never be burned in)
        assert ev.strip_tail(-1e-15)[0] == "Δ +0.00"
    finally:
        ev.export_delta_colour = orig


def test_build_lap_spec_resolves_the_best_lap_verdict():
    """`build_lap_spec` is where a lap becomes an export, so it is where the best-lap verdict is
    resolved — the exporter had never called `session.best_lap_id()` at all (grep: zero references)
    while the app's video export defaults to that very lap."""
    class _Best(StubSession):
        def __init__(self, best, **kw):
            super().__init__(**kw)
            self._best = best
            self.video_path = "/in.MP4"

        def best_lap_id(self):
            return self._best

    assert ev.build_lap_spec(_Best(2, lap_id=2), "/o.mp4", 2).is_best is True
    assert ev.build_lap_spec(_Best(5, lap_id=2), "/o.mp4", 2).is_best is False
    assert ev.build_lap_spec(_Best(None, lap_id=2), "/o.mp4", 2).is_best is False
    # a duck-typed session with no accessor at all keeps the old behaviour (a Δ, no mark)
    s = StubSession(lap_id=2)
    s.video_path = "/in.MP4"
    assert not hasattr(s, "best_lap_id")
    assert ev.build_lap_spec(s, "/o.mp4", 2).is_best is False
    assert ev.ExportSpec(src_path="/in.MP4", out_path="/o.mp4", lap_id=1,
                         t0=0.0, t1=1.0).is_best is False       # default off


def test_both_hud_pills_are_fitted_to_their_own_ink():
    """The pills were `max(out_w * 0.30, 260)` and `max(out_w * 0.26, 220)` — a fraction of the
    FRAME, with no relation to their contents. Measured on the shipped face that left the readout's
    ink filling ~35 % of its pill and the strip's ~46 % at every output height.

    Now both measure their own text. The check is the RATIO, swept over the heights the export
    offers: content (the runs the painter actually places, plus the stated padding) must fill the
    pill, and the pill must still hold its widest string with the padding intact.

    The content is measured from `_burned_runs` — the painter's own enumeration of what this export
    will draw — which is the same list the pill was fitted to, so this is a check that
    `readout_pill_width`/`strip_pill_width` spend the whole box on ink and padding and nothing
    else. That the enumeration is CORRECT is `test_export_pill_budget.py`'s job."""
    from PySide6.QtGui import QFontMetricsF
    s = StubSession(lap_id=2, t0=0.0, dur=64.238, n=600)
    worst = 1.0
    for out_h in (480, 720, 1080, 1440, 2160):
        out_w = int(out_h * 16 / 9)
        spec = ev.ExportSpec(src_path="/x.MP4", out_path="/o.MP4", lap_id=2, t0=0.0, t1=64.238,
                             config=ev.OverlayConfig(out_height=out_h))
        painter = ev.OverlayPainter(s, spec, out_w, out_h, 30.0)
        speeds, labels, tails = ev._burned_runs(s, spec, 30.0)
        # --- readout: pad + widest hero + gap + unit + pad, measured the painter's own way
        rh = painter._readout_rect.height()
        k = rh / 44.0
        fm_big = QFontMetricsF(ev._font(rh * 0.74, bold=True))
        fm_u = QFontMetricsF(ev._font(rh * 0.34, bold=True))
        hero = max(fm_big.horizontalAdvance(t) for t in speeds)
        content = (2 * rh * ev._READOUT_PAD_FRAC + hero + ev._READOUT_GAP_K * k
                   + fm_u.horizontalAdvance("km/h"))
        ratio = content / painter._readout_rect.width()
        assert abs(ratio - 1.0) < 1e-6, f"{out_h}p readout fills {ratio:.3f} of its pill"
        # --- strip: the same, with the tail run
        sh = painter._strip_rect.height()
        sk = sh / 44.0
        fm_s = QFontMetricsF(ev._font(sh * 0.54, bold=True))
        label = max(fm_s.horizontalAdvance(t) for t in labels)
        tail = max(fm_s.horizontalAdvance(t) for t in tails if t)
        s_content = (sh * ev._STRIP_PAD_L_FRAC + label + ev._RUN_GAP_K * sk + tail
                     + sh * ev._STRIP_PAD_R_FRAC)
        s_ratio = s_content / painter._strip_rect.width()
        assert abs(s_ratio - 1.0) < 1e-6, f"{out_h}p strip fills {s_ratio:.3f} of its pill"
        # and both are strictly narrower than the frame-fraction boxes they replaced
        assert painter._readout_rect.width() < max(out_w * 0.30, 260.0), out_h
        assert painter._strip_rect.width() < max(out_w * 0.26, 220.0), out_h
        worst = min(worst, ratio, s_ratio)
    print(f"test_both_hud_pills_are_fitted_to_their_own_ink OK (worst fill {worst:.3f})")


def test_the_readout_pill_holds_every_speed_this_export_can_burn():
    """The pill is fitted ONCE per export, so it has to be fitted to the widest string, not to the
    first frame's. Two traps, both pinned: the hero number is ROUNDED, so a session peaking at
    99.6 km/h burns "100" — three digits from a two-digit maximum; and a session with no usable
    speed track burns `theme.speed_number`'s EM DASH on every frame, which the pill must hold.

    Both come out of `_burned_runs`, which enumerates the strings rather than estimating them —
    so the dash is budgeted when and only when a frame draws one (it used to be added to every
    pill unconditionally, as insurance against an estimate)."""
    from PySide6.QtGui import QFontMetricsF
    s = StubSession(lap_id=2, t0=0.0, dur=60.0, n=600)
    s.tv = np.full(600, 99.6)                             # peaks at 99.6 -> "100", three digits
    spec = ev.ExportSpec(src_path="/x.MP4", out_path="/o.MP4", lap_id=2, t0=0.0, t1=60.0)
    speeds, _labels, _tails = ev._burned_runs(s, spec, 30.0)
    assert set(speeds) == {"100"}, sorted(set(speeds))
    rh = 44.0
    w = ev.readout_pill_width(rh, speeds, "km/h")
    fm = QFontMetricsF(ev._font(rh * 0.74, bold=True))
    fm_u = QFontMetricsF(ev._font(rh * 0.34, bold=True))
    pad = 2 * rh * ev._READOUT_PAD_FRAC
    for text in ("100", "0"):
        need = pad + fm.horizontalAdvance(text) + ev._READOUT_GAP_K + fm_u.horizontalAdvance("km/h")
        assert need <= w + 1e-6, f"{text!r} needs {need:.1f} px in a {w:.1f} px pill"
    # no usable track at all -> every frame burns the em dash, and the pill is fitted to it
    nt = StubSession(lap_id=2, t0=0.0, dur=60.0, n=4)
    nt.tv = np.asarray([])
    dash_speeds, _l, _t = ev._burned_runs(nt, spec, 30.0)
    assert set(dash_speeds) == {"—"}, sorted(set(dash_speeds))
    w_dash = ev.readout_pill_width(rh, dash_speeds, "km/h")
    need = pad + fm.horizontalAdvance("—") + ev._READOUT_GAP_K + fm_u.horizontalAdvance("km/h")
    assert need <= w_dash + 1e-6, f"the dash needs {need:.1f} px in a {w_dash:.1f} px pill"
    print("test_the_readout_pill_holds_every_speed_this_export_can_burn OK")


def test_the_strip_pill_is_budgeted_for_this_laps_real_delta_range():
    """`Δ -0.31` and `Δ -12.40` are not the same width, and the strip is sized once. The budget is
    the session's OWN Δ curve read through the per-frame lookup the render uses, so a lap that
    swings to double digits gets a pill that holds it."""
    from PySide6.QtGui import QFontMetricsF

    class _BigDelta(StubSession):
        def delta_at_lap(self, lap_id, t):
            return -12.4 if lap_id == self._lap else None

    small = StubSession(lap_id=2, t0=0.0, dur=60.0, n=600)
    big = _BigDelta(lap_id=2, t0=0.0, dur=60.0, n=600)
    spec = ev.ExportSpec(src_path="/x.MP4", out_path="/o.MP4", lap_id=2, t0=0.0, t1=60.0)
    s_speeds, s_labels, s_tails = ev._burned_runs(small, spec, 30.0)
    b_speeds, b_labels, b_tails = ev._burned_runs(big, spec, 30.0)
    assert set(s_tails) == {"Δ +0.00"}, sorted(set(s_tails))
    assert set(b_tails) == {"Δ -12.40"}, sorted(set(b_tails))
    fm = QFontMetricsF(ev._font(44.0 * 0.54, bold=True))
    w_small = ev.strip_pill_width(44.0, s_labels, s_tails)
    w_big = ev.strip_pill_width(44.0, b_labels, b_tails)
    assert w_big > w_small, (w_small, w_big)
    # "Δ +0.00" -> "Δ -12.40" is exactly ONE extra digit cell (plus the +/- advance difference),
    # and with tabular figures a digit cell is a fixed width — so this is an equality in disguise.
    assert w_big - w_small >= fm.horizontalAdvance("0") * 0.9, (w_small, w_big)
    # a best-lap export is sized for the mark instead
    best = ev.ExportSpec(src_path="/x.MP4", out_path="/o.MP4", lap_id=2, t0=0.0, t1=60.0,
                         is_best=True)
    _sp, _lb, best_tails = ev._burned_runs(small, best, 30.0)
    assert set(best_tails) == {ev._BEST_MARK}, sorted(set(best_tails))
    assert s_speeds and b_speeds                          # both sessions do burn a speed
    print("test_the_strip_pill_is_budgeted_for_this_laps_real_delta_range OK")


# --------------------------------------------------------------------------- export restyle: quality
def test_quality_params_high_vs_standard():
    """quality_params maps the picker level to (bpp, crf): high keeps the original 0.10 bpp / CRF 20
    (visually-lossless), standard is leaner (lower bpp, higher CRF); unknown -> the high default."""
    hi_bpp, hi_crf = ev.quality_params("high")
    st_bpp, st_crf = ev.quality_params("standard")
    assert hi_bpp == ev._BITS_PER_PIXEL and hi_crf == 20
    assert st_bpp < hi_bpp                                    # standard = smaller files
    assert st_crf > hi_crf                                    # higher CRF = lower quality
    assert ev.quality_params("nonsense") == ev.quality_params("high")  # default
    assert ev.quality_params(None) == ev.quality_params("high")


def test_vt_target_bitrate_honours_quality_bpp():
    """vt_target_bitrate scales with the quality level's bits-per-pixel, so standard < high at the
    same size/fps (and the floor still applies for tiny sizes)."""
    hi = ev.vt_target_bitrate(1920, 1080, 30.0, ev.quality_params("high")[0])
    st = ev.vt_target_bitrate(1920, 1080, 30.0, ev.quality_params("standard")[0])
    assert hi > st
    assert ev.vt_target_bitrate(64, 64, 2.0, 0.06) == ev._MIN_VT_BITRATE   # floored


def test_encode_cmd_reflects_quality_level():
    """build_encode_cmd carries the chosen quality through to BOTH encoders: a different VideoToolbox
    -b:v bitrate, and a different libx264 -crf, for standard vs high. The default config stays at the
    high (original) numbers so existing exports are unchanged."""
    def spec(q):
        return ev.ExportSpec(src_path="/in/src.MP4", out_path="/out/clip.mp4", lap_id=1,
                             t0=0.0, t1=10.0, config=ev.OverlayConfig(quality=q))
    vt_hi = ev.build_encode_cmd(spec("high"), 1920, 1080, 30.0, encoder=ev.VT_H264)
    vt_st = ev.build_encode_cmd(spec("standard"), 1920, 1080, 30.0, encoder=ev.VT_H264)
    assert vt_hi[vt_hi.index("-b:v") + 1] != vt_st[vt_st.index("-b:v") + 1]
    sw_hi = ev.build_encode_cmd(spec("high"), 1920, 1080, 30.0, encoder=ev.SW_H264)
    sw_st = ev.build_encode_cmd(spec("standard"), 1920, 1080, 30.0, encoder=ev.SW_H264)
    assert sw_hi[sw_hi.index("-crf") + 1] == "20"
    assert sw_st[sw_st.index("-crf") + 1] == "23"
    # default OverlayConfig -> high (no silent regression)
    assert ev.OverlayConfig().quality == "high"
    vt_def = ev.build_encode_cmd(_spec(), 1920, 1080, 30.0, encoder=ev.VT_H264)
    assert vt_def[vt_def.index("-b:v") + 1] == vt_hi[vt_hi.index("-b:v") + 1]


# --------------------------------------------------------------------------- export restyle: map inset
def test_map_inset_draws_only_lap_line_no_box():
    """The export map inset draws ONLY the selected lap's racing line with NO backdrop box and NO
    full-session trace. Asserted on the baked layer: the four CORNERS of the inset box (where a box
    backdrop would paint) stay fully transparent, while the lap line leaves bright pixels inside."""
    from PySide6.QtCore import QRectF
    s = StubSession(lap_id=2, t0=0.0, dur=60.0, n=600)
    # give the stub a curved lap trace so the lap line isn't degenerate
    import numpy as _np
    th = _np.linspace(0, 2 * _np.pi, 600)
    s.tx = 50 + 40 * _np.cos(th)
    s.ty = 50 + 30 * _np.sin(th)
    box = QRectF(100, 100, 200, 160)
    mi = ev._MapInset(s, box, 2, scale_k=1.0)
    assert mi._ok
    layer = mi._layer
    import numpy as np
    arr = np.frombuffer(layer.constBits(), np.uint8,
                        count=layer.bytesPerLine() * layer.height()).reshape(layer.height(), -1)
    alpha_plane = arr[:, 3::4]   # ARGB32 premultiplied: alpha is every 4th byte

    # the box corners must be transparent — proof there's no rounded-rect backdrop drawn.
    for cx, cy in [(box.x() + 3, box.y() + 3), (box.right() - 4, box.y() + 3),
                   (box.x() + 3, box.bottom() - 4), (box.right() - 4, box.bottom() - 4)]:
        assert alpha_plane[int(cy), int(cx)] == 0, \
            f"box corner ({cx:.0f},{cy:.0f}) must be transparent (no box backdrop)"
    # but SOME pixels inside the box are painted (the lap line)
    assert int((alpha_plane > 0).sum()) > 50, "the lap line must paint some pixels"


def test_map_inset_degenerate_lap_falls_back_gracefully():
    """If the selected lap's own trace is unusable, the inset falls back to the full-session trace
    line (still no box) rather than being empty or raising. The marker and its comet tail never
    needed the lap line — both are read off the session trace at the frame's time (E6) — so a
    degenerate lap still gets both."""
    from PySide6.QtCore import QRectF

    class NoLapTrace(StubSession):
        def lap_trace_xy(self, lap_id):
            return None                                      # degenerate: no lap line
    s = NoLapTrace(lap_id=2, t0=0.0, dur=60.0, n=400)
    mi = ev._MapInset(s, QRectF(0, 0, 200, 160), 2, scale_k=1.0)
    assert mi._ok                                            # built from the full trace fallback
    marker, tail = mi.marker_and_tail(float(s.tt[200]))
    assert marker is not None and tail.size() >= 2           # still a marker, and its tail
    assert tail.at(tail.size() - 1) == marker                # ...ending on it


def test_overlay_painter_size_scale_tracks_height():
    """OverlayPainter computes a size scale `_k` from the output height (1.0 at 1080p) so the
    overlays scale with resolution — 720p < 1080p < 1440p."""
    s = StubSession()
    spec = ev.ExportSpec(src_path="/x.MP4", out_path="/o.MP4", lap_id=2, t0=100.0, t1=160.0)
    p720 = ev.OverlayPainter(s, spec, 1280, 720, 30.0)
    p1080 = ev.OverlayPainter(s, spec, 1920, 1080, 30.0)
    p1440 = ev.OverlayPainter(s, spec, 2560, 1440, 30.0)
    assert p720._k < p1080._k < p1440._k
    assert abs(p1080._k - 1.0) < 1e-9                        # 1.0 at 1080p


def test_real_render_quality_levels_if_media():
    """The quality picker end-to-end — a FOOTAGE_CHECK (reported SKIPPED without ffmpeg + media).
    Renders the SAME short window at 720p-standard and 1080p-high and asserts both are valid files
    whose resolution differs and whose bitrate differs (the picker actually changes the encode).

    Both files must also land at or above the free-space guard's floor for them (E1): these are
    the two real renders every footage run already pays for, so the floor is re-checked against
    real footage on every run rather than only against the table it was chosen from."""
    import tempfile
    real = _real_media("real_render_quality_levels")
    from studio.session import Session
    s = Session.load(chapters.discover_siblings(real))
    best = s.best_lap_id()
    t0, _ = s.lap_window(best)
    tmp = tempfile.TemporaryDirectory(prefix="pacer-f9-quality-")
    out_lo = os.path.join(tmp.name, "q_720_std.mp4")
    out_hi = os.path.join(tmp.name, "q_1080_high.mp4")
    for out, cfg in [(out_lo, ev.OverlayConfig(out_height=720, quality="standard")),
                     (out_hi, ev.OverlayConfig(out_height=1080, quality="high"))]:
        spec = ev.build_lap_spec(s, out, best, config=cfg, src_path=real)
        est, codec = ev.estimate_spec_bytes(spec), ev.output_codec(cfg)
        try:
            ev.Renderer(s, spec).run()
        finally:
            spec.source.cleanup()
        size = os.path.getsize(out)
        assert size > 0
        # E1: the free-space guard refuses below FREE_SPACE_FLOOR_FRACTION of this estimate, so a
        # real file landing UNDER that floor is exactly an export it would have wrongly refused.
        floor = ev.FREE_SPACE_FLOOR_FRACTION[codec] * est
        assert size >= floor, (
            f"{os.path.basename(out)} ({codec}) came out at {size:,} B, under the guard's floor "
            f"{floor:,.0f} B ({size / est:.3f} of the estimate) — the guard would have refused it")
        print(f"  {os.path.basename(out)} {codec}: {size / est:.3f} of the estimate "
              f"(floor {ev.FREE_SPACE_FLOOR_FRACTION[codec]})")

    def probe_bitrate(path):
        import subprocess as sp
        r = sp.run([ev.FFPROBE, "-v", "error", "-select_streams", "v:0",
                    "-show_entries", "stream=bit_rate", "-of",
                    "default=noprint_wrappers=1:nokey=1", path], capture_output=True, text=True)
        return int(r.stdout.strip() or 0)

    w_lo, h_lo, _ = ev.probe_video_size(out_lo)
    w_hi, h_hi, _ = ev.probe_video_size(out_hi)
    assert h_lo == 720 and h_hi == 1080                      # resolution picker took effect
    br_lo, br_hi = probe_bitrate(out_lo), probe_bitrate(out_hi)
    assert br_hi > br_lo > 0, f"high bitrate {br_hi} must exceed standard {br_lo}"
    tmp.cleanup()
    print(f"real_render_quality_levels OK (720/std {br_lo} < 1080/high {br_hi} bps)")


def test_resolve_binary_env_override_then_path():
    """_resolve_binary (the bundled-.app ffmpeg lookup): an explicit PACER_FFMPEG/PACER_FFPROBE env
    var wins (the runtime hook's mechanism + a hand override); with no override and not frozen, it
    falls back to the bare PATH name (the dev/pixi path, unchanged)."""
    saved = {k: os.environ.get(k) for k in ("PACER_FFMPEG", "PACER_FFPROBE")}
    try:
        os.environ["PACER_FFMPEG"] = "/opt/pacer/ffmpeg"
        os.environ["PACER_FFPROBE"] = "/opt/pacer/ffprobe"
        assert ev._resolve_binary("ffmpeg", "PACER_FFMPEG") == "/opt/pacer/ffmpeg"
        assert ev._resolve_binary("ffprobe", "PACER_FFPROBE") == "/opt/pacer/ffprobe"
        # no override + a normal (non-frozen) interpreter -> the bare name resolved on PATH later.
        os.environ.pop("PACER_FFMPEG", None)
        assert not getattr(sys, "frozen", False), "test interpreter unexpectedly frozen"
        assert ev._resolve_binary("ffmpeg", "PACER_FFMPEG") == "ffmpeg"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("resolve_binary OK (env override wins, PATH fallback otherwise)")


# --------------------------------------------------------------------------- restore fixture
class _Restore:
    """Save/restore the module globals the pipeline mocks clobber, so tests don't bleed into each
    other (no pytest here — a tiny manual fixture run around each mocked test)."""

    _SAVED = ("subprocess", "probe_video_size", "probe_source_duration", "resolve_encoder",
              "videotoolbox_encoder_available", "videotoolbox_usable", "videotoolbox_decode_available",
              "prores_videotoolbox_usable", "alpha_codec_args", "free_bytes", "_PRORES_REC709")

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


class _EnospcEncoder:
    """A fake ffmpeg encode that takes `fail_after` frames and then dies the way ffmpeg 7.1 did on a
    slow VideoToolbox render with 94.5 GB free: exit status 228 (its -28) and this stderr tail."""

    TAIL = (b"[af#0:1 @ 0x10de0a7d0] Error sending frames to consumers: No space left on device\n"
            b"[af#0:1 @ 0x10de0a7d0] Task finished with error code: -28 (No space left on "
            b"device)\n")

    def __init__(self, fail_after):
        import io
        self._left = fail_after
        self.returncode = None
        self.stdout = None
        self.stderr = io.BytesIO(self.TAIL)
        self.stdin = types.SimpleNamespace(write=self._write, flush=lambda: None,
                                           close=lambda: None)

    def _write(self, _frame):
        if self._left <= 0:
            self.returncode = 228
            raise BrokenPipeError(32, "Broken pipe")
        self._left -= 1

    def wait(self, *_a, **_k):
        return self.returncode

    def kill(self):
        if self.returncode is None:
            self.returncode = -9


def test_a_no_space_failure_asks_the_disk_before_it_skips_the_retry(monkeypatch_restore):
    """ffmpeg's "No space left on device" is a CLAIM: ffmpeg 7.1 printed it when its own `-shortest`
    sync queue overflowed (the mux before E4), and a slow VideoToolbox render hit that with 94.5 GB
    free. So the words
    only make the renderer ASK THE DISK (free blocks, not purgeable space). Only the disk's answer
    decides whether a render is a doomed retry and whether the user hears "no room".

    The REAL Renderer, with its real painter, on a stub session; ffmpeg is faked to fail the way it
    failed. Pinned to VideoToolbox (the only encoder with a retry to take or skip):
      * full (1 MB free): DiskFullError, whose sentence carries what the disk said; no 2nd encoder;
      * room (90 GB), or a volume that cannot be asked: the libx264 retry runs and the render ends.
    On main the first had no figures and the other two never retried."""
    s = StubSession(lap_id=2, t0=0.0, dur=2.0, n=400)
    # 1080p60 over 2 s: the least VideoToolbox can make of it is ~1.9 MB, so 1 MB is a full disk.
    cfg = ev.OverlayConfig(out_height=1080, fps_cap=None, encoder="auto", workers=1)
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/exports/lap.mp4", lap_id=2, t0=0.0,
                         t1=2.0, config=cfg)
    out_w, out_h = ev.output_size(3840, 2160, cfg)
    real_free = ev.free_bytes
    for free, retried in ((90_000_000_000, True), (None, True), (1_000_000, False)):
        encoders, asked = [], []

        def fake_popen(cmd, _encoders=encoders, **_kw):
            if cmd[-1] == "pipe:1":
                return _FakeProc(frame_bytes=out_w * out_h * 3, nframes=10_000, is_decoder=True)
            encoder = cmd[cmd.index("-c:v") + 1]
            _encoders.append(encoder)
            return _EnospcEncoder(fail_after=5) if encoder == ev.VT_H264 else _FakeProc()

        ev.subprocess.Popen = fake_popen                      # type: ignore[assignment]
        ev.probe_video_size = lambda _p: (3840, 2160, 60.0)   # type: ignore[assignment]
        ev.probe_source_duration = lambda _s: 1.0e9           # type: ignore[assignment]
        ev.videotoolbox_decode_available = lambda: False      # type: ignore[assignment]
        ev.resolve_encoder = (lambda c: ev.SW_H264 if c == "libx264"  # type: ignore[assignment]
                              else ev.VT_H264)
        ev.free_bytes = (lambda p, purgeable=True, _f=free, _asked=asked:
                         (_asked.append((p, purgeable)), _f)[1])
        try:
            r = ev.Renderer(s, spec)
            assert r.encoder == ev.VT_H264, r.encoder
            try:
                res = r.run()
                raised = None
            except Exception as exc:  # noqa: BLE001 — the TYPE is what is under test
                raised = exc
        finally:
            ev.free_bytes = real_free
        if retried:
            assert encoders == [ev.VT_H264, ev.SW_H264], (
                f"free={free}: a no-space CLAIM with room to spare skipped the libx264 retry: "
                f"{encoders} ({raised!r})")
            assert raised is None and res.frames == 120, (free, raised)
        else:
            assert encoders == [ev.VT_H264], (
                f"a second encoder was started for a disk with 1 MB left: {encoders}")
            assert isinstance(raised, ev.DiskFullError), (
                f"a full disk must surface as DiskFullError (got {type(raised).__name__}: {raised})")
            text = str(raised)
            assert ev.is_out_of_space(text), text
            est = ev.estimate_output_bytes(out_w, out_h, 60.0, ev.clip_seconds(0.0, 2.0, 60.0),
                                           "high", ev.VT_H264)
            least = ev.fmt_bytes(ev.floor_bytes(est, ev.VT_H264))
            sentence = ev.disk_full_sentence(text)
            assert sentence == (
                f"There's no room left on the disk holding /exports: it had 1 MB free when the "
                f"export stopped, and this export needs at least {least}."), sentence
            assert "No space left on device" in text and "No space" not in sentence, text
        assert asked == [("/exports", False)], (
            f"free={free}: the disk was not asked, free blocks only, about the output's folder: "
            f"{asked}")

    # The words are a claim; only the renderer's sentence is a verdict.
    assert ev.reports_no_space("av_interleaved_write_frame(): No space left on device")
    assert ev.reports_no_space("ENOSPC") and ev.reports_no_space("Disk full")
    assert not ev.reports_no_space("Error encoding frame: -12905")
    assert not ev.reports_no_space("") and not ev.reports_no_space(None)
    assert not ev.is_out_of_space("av_interleaved_write_frame(): No space left on device")
    assert not ev.is_out_of_space(_EnospcEncoder.TAIL.decode())
    assert not ev.is_out_of_space(None)
    print("ok enospc: the disk decides — full skips the retry with a true sentence, room retries")


def test_the_size_model_is_the_stated_rate_times_the_clip():
    """`estimate_output_bytes` is the ONE model behind both the picker's "About N MB" and the
    free-space guard's requirement. VideoToolbox is bitrate-targeted, so it is the stated target;
    libx264 is CRF-driven, so it is the measured bits-per-pixel table; the two alpha outputs are
    their own measured rates. Nothing to render costs nothing."""
    from studio import export_video as EV

    sec = 60.0
    vt = EV.estimate_output_bytes(1920, 1080, 30.0, sec, "high", EV.VT_H264)
    assert vt == int(EV.vt_target_bitrate(1920, 1080, 30.0, 0.10) * sec / 8), vt
    std = EV.estimate_output_bytes(1920, 1080, 30.0, sec, "standard", EV.VT_H264)
    assert std == int(EV.vt_target_bitrate(1920, 1080, 30.0, 0.06) * sec / 8), std
    x264 = EV.estimate_output_bytes(1920, 1080, 30.0, sec, "high", EV.SW_H264)
    assert x264 == int(1920 * 1080 * 30.0 * EV.X264_BPP[20] * sec / 8), x264
    prores = EV.estimate_output_bytes(1920, 1080, 30.0, sec, "high", EV.ALPHA_PRORES)
    png = EV.estimate_output_bytes(1920, 1080, 30.0, sec, "high", EV.ALPHA_PNG)
    assert prores == int(1920 * 1080 * 30.0 * EV.PRORES_4444_BPP * sec / 8), prores
    assert png == int(1920 * 1080 * 30.0 * EV.PNG_SEQUENCE_BPP * sec / 8), png
    for degenerate in ((0, 1080, 30.0, sec), (1920, 1080, 30.0, 0.0),
                       (1920, 1080, 30.0, float("nan"))):
        assert EV.estimate_output_bytes(*degenerate, "high", EV.VT_H264) == 0, degenerate
    # E5: each ProRes encoder has its own measured figure (prores_ks is ALPHA_PRORES's), and the
    # alpha outputs cost less per pixel on a bigger frame: x (1080 / short side) ** 0.5.
    assert EV.estimate_output_bytes(1920, 1080, 30.0, sec, "high", EV.SW_PRORES) == prores
    vt = EV.estimate_output_bytes(1920, 1080, 30.0, sec, "high", EV.VT_PRORES)
    assert vt == int(1920 * 1080 * 30.0 * EV.PRORES_VT_4444_BPP * sec / 8), vt
    uhd = EV.estimate_output_bytes(3840, 2160, 30.0, sec, "high", EV.VT_PRORES)
    assert uhd == int(3840 * 2160 * 30.0 * EV.PRORES_VT_4444_BPP * 0.5 ** 0.5 * sec / 8), uhd
    # the owner's export as measured: MK best lap +-5 s, 2325 frames of 4K on VideoToolbox, 1.522 GB
    owner = EV.estimate_output_bytes(3840, 2160, 30.0, 2325 / 30.0, "high", EV.VT_PRORES)
    assert 0.9 < 1.522e9 / owner < 1.1, owner
    print("ok size model: stated rate x clip, per codec; nothing to render costs nothing")


def test_the_time_model_reproduces_the_rates_it_was_measured_at():
    """`estimate_render_seconds` is the picker's "about M:SS to render": frames over the measured
    rate of the path at 1080p and 2160p, linear in pixels between them. It says nothing for a path
    it has no measurement of, and nothing about nothing."""
    for codec, (at_1080, at_2160) in ev.RENDER_FPS.items():
        assert abs(ev.estimate_render_seconds(1920, 1080, 600, codec) - 600 / at_1080) < 1e-9
        assert abs(ev.estimate_render_seconds(3840, 2160, 600, codec) - 600 / at_2160) < 1e-9
        mid = ev.estimate_render_seconds(2560, 1440, 600, codec)
        assert 600 / at_1080 < mid < 600 / at_2160, (codec, mid)
        assert ev.estimate_render_seconds(640, 360, 600, codec) > 0
    assert ev.estimate_render_seconds(1920, 1080, 600, "hevc_mystery") is None
    assert ev.estimate_render_seconds(1920, 1080, 0, ev.VT_H264) is None
    # The owner's export, measured end to end: 35.2 s on VideoToolbox since #412 (65.2 s before it —
    # the table had kept the old rate, so the picker quoted 1:06 for a 35 s render), 145.3 s on
    # prores_ks, which #412 barely moved.
    assert abs(ev.estimate_render_seconds(3840, 2160, 2325, ev.VT_PRORES) - 35.2) < 3.0
    assert abs(ev.estimate_render_seconds(3840, 2160, 2325, ev.SW_PRORES) - 145.3) < 7.0
    print("ok time model: the measured rates, linear in pixels between them")


def test_free_space_is_asked_of_the_nearest_existing_folder():
    """The output does not exist yet, and neither may its folder — so the volume is asked about
    the nearest folder that does. The answer is the larger of `statvfs` and macOS's own capacity
    for important usage (which adds back purgeable space `statvfs` cannot see), and None — never
    zero — when neither can be asked."""
    import shutil as _shutil
    import tempfile as _tempfile

    from studio import export_video as EV

    asked, real_usage = [], _shutil.disk_usage
    real_capacity = EV._volume_capacity_for_important_usage
    with _tempfile.TemporaryDirectory() as td:
        deep = os.path.join(td, "not", "made", "yet", "lap.mp4")
        try:
            _shutil.disk_usage = lambda p: (asked.append(("statvfs", p)),
                                            types.SimpleNamespace(free=100))[1]
            EV._volume_capacity_for_important_usage = lambda p: (asked.append(("important", p)),
                                                                 250)[1]
            assert EV.free_bytes(deep) == 250
            assert asked == [("statvfs", td), ("important", td)], asked
            # After a write has FAILED, purgeable space is not room: that write is the proof it
            # had not been freed for the writer. `purgeable=False` asks statvfs alone, and does not
            # even ask CoreFoundation (E3).
            asked.clear()
            assert EV.free_bytes(deep, purgeable=False) == 100
            assert asked == [("statvfs", td)], asked
            EV._volume_capacity_for_important_usage = lambda p: None
            assert EV.free_bytes(deep) == 100            # no purgeable figure: statvfs alone

            def _raise(_p):
                raise OSError("volume does not answer")
            _shutil.disk_usage = _raise
            assert EV.free_bytes(deep) is None, "an unanswerable volume must read as UNKNOWN"
            assert EV.free_bytes(deep, purgeable=False) is None, "…with free blocks alone too"
        finally:
            _shutil.disk_usage = real_usage
            EV._volume_capacity_for_important_usage = real_capacity
        if sys.platform == "darwin":
            # The real CoreFoundation query, on this machine: a real number, in bytes.
            important = EV._volume_capacity_for_important_usage(td)
            assert isinstance(important, int) and important > 0, important
            assert EV.free_bytes(deep) >= _shutil.disk_usage(td).free - (1 << 30)
    print("ok free space: nearest existing folder, the larger figure, None when unanswerable")


def _space_spec(out_path, t1=60.0, **cfg):
    from studio import export_video as EV
    return EV.ExportSpec(out_path=out_path, lap_id=0, t0=0.0, t1=t1,
                         src_path="/nonexistent/GX010099.MP4", config=EV.OverlayConfig(**cfg))


class _Encoder:
    """Pin the H.264 encoder `resolve_encoder` answers with, for the length of a `with` block.

    THE ENCODER IS A PROPERTY OF THE MACHINE, so any expected size that depends on it has to name
    it. This Mac opens a VideoToolbox session; the GitHub macos-14 runner does not, and resolves
    "auto" to libx264 — where an unpinned estimate of the same 60 s lap is 317,260,800 B against
    VideoToolbox's 46,656,000 B. That is exactly how the first version of the test below passed
    here and failed in CI."""

    def __init__(self, codec):
        self.codec = codec

    def __enter__(self):
        from studio import export_video as EV
        self._real = EV.resolve_encoder
        EV.resolve_encoder = lambda _choice: self.codec
        return self

    def __exit__(self, *_exc):
        from studio import export_video as EV
        EV.resolve_encoder = self._real


def _guard(specs, free, asked=None):
    """guard_free_space over `specs` with a 4K 59.94 source and `free` bytes, on whichever encoder
    the caller has pinned with `_Encoder`."""
    from studio import export_video as EV
    real_free = EV.free_bytes
    EV.free_bytes = lambda p: (asked.append(p) if asked is not None else None, free)[1]
    try:
        EV.guard_free_space(specs, probe=lambda _p: (3840, 2160, 60000 / 1001))
    finally:
        EV.free_bytes = real_free


def test_the_guard_refuses_below_its_floor_and_nothing_at_or_above_it():
    """The threshold, to the byte, on both sides, on BOTH H.264 encoders: the guard requires
    FREE_SPACE_FLOOR_FRACTION[codec] of the central estimate, less what the render reclaims by
    overwriting a previous one. One byte short is refused with the three numbers; exactly enough,
    or a volume that cannot be asked, refuses nothing.

    Each encoder is PINNED (`_Encoder`) — the resolved one is a property of the machine — so this
    asserts the VideoToolbox arithmetic and the libx264 arithmetic on every machine, including one
    where only one of them could ever be resolved."""
    import tempfile as _tempfile

    from studio import export_video as EV

    probe = lambda _p: (3840, 2160, 60000 / 1001)  # noqa: E731
    expected = {   # what the module states for a 60 s, 1080p30, "high" clip, per encoder
        EV.VT_H264: int(EV.vt_target_bitrate(1920, 1080, 30.0, 0.10) * 60.0 / 8),
        EV.SW_H264: int(1920 * 1080 * 30.0 * EV.X264_BPP[20] * 60.0 / 8),
    }
    assert expected[EV.VT_H264] != expected[EV.SW_H264], expected
    for codec, want in expected.items():
        with _Encoder(codec), _tempfile.TemporaryDirectory() as td:
            spec = _space_spec(os.path.join(td, "lap.mp4"))
            est = EV.estimate_spec_bytes(spec, probe)
            assert est == want, (codec, est, want)
            need = int(est * EV.FREE_SPACE_FLOOR_FRACTION[codec])
            asked = []
            _guard([spec], need, asked)                         # exactly enough: runs
            assert asked == [td], (codec, asked)
            _guard([spec], None)                                # unknown: runs
            try:
                _guard([spec], need - 1)
            except EV.InsufficientSpaceError as exc:
                text = str(exc)
            else:
                raise AssertionError(f"{codec}: one byte below the floor was not refused")
            assert EV.is_refused_for_space(text) and not EV.is_out_of_space(text), text
            assert f"about {EV.fmt_bytes(est)}" in text and td in text, text
            assert f"({EV.fmt_bytes(need)})" in text or f"{need / 1e6:,.0f} MB" in text, text

            # Re-exporting over a previous export: ffmpeg's -y truncates it, so it only has to
            # find the DIFFERENCE. Without this the re-export a user tries first on a full disk
            # is refused.
            with open(spec.out_path, "wb") as f:
                f.write(b"\0" * 5_000_000)
            _guard([spec], need - 5_000_000)
            try:
                _guard([spec], need - 5_000_001)
                raise AssertionError(f"{codec}: the reclaim was counted twice")
            except EV.InsufficientSpaceError:
                pass

    # A PNG sequence reclaims the frames of an earlier sequence it will overwrite (its own
    # numbering, overlay_000001.png..N) and nothing else in that folder.
    with _tempfile.TemporaryDirectory() as td:
        seq = _space_spec(td, t1=1.0, overlay_only=True, alpha_codec=EV.ALPHA_PNG)
        n = EV.frame_count(0.0, 1.0, 30.0)
        for i in (1, n, n + 1):
            with open(os.path.join(td, f"overlay_{i:06d}.png"), "wb") as f:
                f.write(b"\0" * 1000)
        with open(os.path.join(td, "notes.png"), "wb") as f:
            f.write(b"\0" * 1000)
        assert EV._reclaimable_bytes(seq, n) == 2000, EV._reclaimable_bytes(seq, n)
    print("ok guard: refuses one byte under its floor, runs at it, reclaims only what it replaces")


def test_the_export_failure_dialog_speaks_english_not_ffmpeg():
    """The failure body used to be the raw stderr tail: "[h264_videotoolbox @ 0x…] Error encoding
    frame: -12905" as the explanation of what to do next. Plain language first, the encoder's own
    words behind Details — the shape the load-failure table and the crash report already use."""
    from studio.export_controller import ExportController

    # A full disk is a sentence the RENDERER writes after asking the disk (`DiskFullError`), and
    # it goes in front with its figures; the encoder's tail after it stays behind Details.
    full = ExportController._export_failure_message(
        "There's no room left on the disk holding /Users/x/Movies: it had 1 MB free when the "
        "export stopped, and this export needs at least 11 MB.\n\nffmpeg encode failed "
        "(h264_videotoolbox, rc=228): … No space left on device", "/Users/x/Movies/lap.mp4")
    assert full == ("There's no room left on the disk holding /Users/x/Movies: it had 1 MB free "
                    "when the export stopped, and this export needs at least 11 MB. Free some "
                    "space, or choose somewhere else, and export again."), full
    # ffmpeg's own "No space left on device" is only a CLAIM — ffmpeg 7.1 also prints it when a
    # queue inside ffmpeg overflows with the disk nearly empty — so on its own it gets the honest
    # generic, never "no room left" (E3).
    claim = ExportController._export_failure_message(
        "av_interleaved_write_frame(): No space left on device", "/Users/x/Movies/lap.mp4")
    assert "no room" not in claim.lower() and "encoder stopped partway" in claim, claim
    denied = ExportController._export_failure_message("Permission denied", "/x/y.mp4")
    assert "isn't allowed to write" in denied, denied
    gone = ExportController._export_failure_message("No such file or directory", "/x/y.mp4")
    assert "isn't there any more" in gone, gone
    generic = ExportController._export_failure_message("Error encoding frame: -12905", "/x/y.mp4")
    assert "encoder stopped partway" in generic and "-12905" not in generic, generic
    # The up-front refusal is NOT the mid-render "no room left": it kept its numbers, and says
    # nothing was written rather than that the disk ran out while writing.
    from studio import export_video as EV
    refusal = ("This export would take about 3.4 GB, and the disk holding /Users/x/Movies has "
               "900 MB free — not enough for even the smallest the export could come out at (2.0 GB).")
    assert EV.is_refused_for_space(refusal) and not EV.is_out_of_space(refusal)
    said = ExportController._export_failure_message(refusal, "/Users/x/Movies/lap.mp4")
    assert said.startswith(refusal) and "Nothing was written" in said, said
    assert "no room left" not in said.lower(), said
    print("ok export-copy: every case names an action, and none of them is an ffmpeg tail")


def test_the_map_inset_is_as_wide_as_the_track_not_as_wide_as_the_frame():
    """§6.6d: the inset box was `map_w_frac * out_w` by `map_h_frac * out_h` — 16:9 by construction,
    because the fractions are equal and the frame is not square — while a kart circuit is roughly
    square.

    Measured on D24 (track bbox 209 x 197 m, aspect 1.06): the box came out 422x238 at 1080p and the
    fitted track drew at 252x238, so 40% of the inset was empty at EVERY resolution — 170 px of
    reserved frame at 1080p, burned over the footage for nothing.

    The height fraction still sets the size; the width is what that height needs at the track's own
    aspect, capped by the old width so a genuinely wide circuit cannot grow the inset past what the
    composition was designed for."""
    from studio.export_video import _inset_width

    class _Sess:
        def __init__(self, w, h):
            self._w, self._h = w, h
        def lap_trace_xy(self, _lap):
            return (np.array([0.0, self._w]), np.array([0.0, self._h]))

    # a square-ish track uses the height it is given and no more width than it needs
    assert abs(_inset_width(_Sess(209.0, 197.0), 0, 238.0, 422.0) - 238.0 * (209.0 / 197.0)) < 0.5
    # a WIDE track is capped at the old box rather than growing the inset
    assert _inset_width(_Sess(1000.0, 100.0), 0, 238.0, 422.0) == 422.0
    # a TALL track keeps a floor, so the plate never becomes a sliver
    assert _inset_width(_Sess(50.0, 1000.0), 0, 238.0, 422.0) == 238.0 * 0.5
    # degenerate / missing traces fall back to the cap — an overlay must never fail an export
    class _Broken:
        def lap_trace_xy(self, _lap):
            raise RuntimeError("no trace")
    assert _inset_width(_Broken(), 0, 238.0, 422.0) == 422.0
    assert _inset_width(_Sess(0.0, 0.0), 0, 238.0, 422.0) == 422.0
    print("ok inset-width: the box follows the track, is capped, floored, and fails soft")


# ===================================================================== output SHAPE (aspect + fit)
def test_frame_geometry_leaves_the_source_aspect_path_exactly_where_it_was():
    """The historic path is byte-for-byte: height controls, width follows the source aspect, both
    even, never upscaled. `output_size` is now a thin wrapper over `frame_geometry`, so this is
    also what says the wrapper did not change its answer."""
    cfg = ev.OverlayConfig(out_height=1080)
    geo = ev.frame_geometry(3840, 2160, cfg)
    assert (geo.out_w, geo.out_h) == (1920, 1080)
    assert geo.scale_filter == "scale=1920:1080", geo.scale_filter
    assert ev.output_size(3840, 2160, cfg) == (1920, 1080)
    # never upscales, and an odd source still lands on even output
    assert ev.output_size(1280, 720, cfg) == (1280, 720)
    w, h = ev.output_size(1921, 1081, ev.OverlayConfig(out_height=540))
    assert w % 2 == 0 and h % 2 == 0
    print("ok frame_geometry: the source-aspect path is unchanged")


def test_a_vertical_or_square_output_is_sized_from_the_pixels_it_can_actually_use():
    """`out_height` is the target SHORT side, and the never-upscale bound is the source pixels the
    chosen mode can reach — which is a different question per mode.

    CROP takes the largest aspect-shaped rectangle out of the source, so a 9:16 crop of 1080p
    footage has only 608x1080 real pixels and 1080p-vertical is honestly refused. The same request
    against 4K crops 1215x2160 and lands on a clean 1080x1920."""
    vertical = ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_9_16, frame_fit=ev.FIT_CROP)
    assert ev.output_size(3840, 2160, vertical) == (1080, 1920), "4K has the pixels for 1080x1920"
    assert ev.output_size(1920, 1080, vertical) == (608, 1080), \
        "a 9:16 crop of 1080p footage is 608x1080 of real pixels — it must not be upscaled"
    square = ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_1_1)
    assert ev.output_size(3840, 2160, square) == (1080, 1080)
    assert ev.output_size(1920, 1080, square) == (1080, 1080), "a 1:1 crop is bounded by the height"
    # FIT keeps the whole picture, so the bound is the axis the picture spans (here the width).
    fit = ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_9_16, frame_fit=ev.FIT_FIT)
    assert ev.output_size(1920, 1080, fit) == (1080, 1920), \
        "fitting 1080p into 9:16 scales the picture DOWN into a 1080-wide frame — no upscale"
    for cfg in (vertical, square, fit):
        w, h = ev.output_size(3840, 2160, cfg)
        assert w % 2 == 0 and h % 2 == 0, (cfg.aspect, cfg.frame_fit, w, h)
    print("ok frame_geometry: vertical/square sized from the pixels each mode can reach")


def test_crop_cuts_before_it_scales_and_fit_pads_without_a_rounding_edge():
    """The two filter chains, and what each is careful about.

    CROP cuts in SOURCE pixels and scales afterwards — the cheap order. FIT uses
    `force_divisible_by=2` under `decrease`, so the contained picture can never come out a pixel
    LARGER than the frame it is padded into (a `pad` smaller than its input is a hard ffmpeg
    error, not a crop)."""
    crop = ev.frame_geometry(3840, 2160,
                             ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_9_16,
                                              frame_fit=ev.FIT_CROP)).scale_filter
    # CROP FIRST, in SOURCE pixels, THEN scale — never ffmpeg's cover-then-crop idiom, which on a
    # source larger than the output scales the whole picture UP before discarding most of it
    # (measured: 57.3 s against 12.1 s for the same window).
    assert crop == "crop=1214:2160,scale=1080:1920", crop
    assert crop.index("crop=") < crop.index("scale="), f"the crop must come first: {crop}"
    fit = ev.frame_geometry(3840, 2160,
                            ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_9_16,
                                             frame_fit=ev.FIT_FIT)).scale_filter
    assert "force_original_aspect_ratio=decrease" in fit and "pad=1080:1920" in fit, fit
    assert "force_divisible_by=2" in fit and "color=black" in fit, fit
    # and the decode argv carries whichever chain it was handed, with the fps pinned BEFORE it
    # (the chain then only ever sees the frames the render keeps)
    spec = ev.ExportSpec(src_path="/x.MP4", out_path="/o.mp4", lap_id=1, t0=10.0, t1=20.0)
    argv = ev.build_decode_cmd(spec, 1080, 1920, 30.0, scale_filter=crop)
    assert f"fps=30.000000,{crop}" in argv, argv
    print("ok frame_geometry: crop cuts before it scales; fit pads")


# ===================================================== the overlay's unit is the frame's SHORT side
def _painter(w, h, session=None, spec=None):
    s = session or StubSession()
    sp = spec or ev.ExportSpec(src_path="/x.MP4", out_path="/o.MP4", lap_id=2, t0=100.0, t1=160.0)
    return ev.OverlayPainter(s, sp, w, h, 30.0)


def test_the_overlay_scales_off_the_short_side_so_16_9_does_not_move():
    """Every overlay dimension is a fraction of `overlay_unit` — the frame's SHORT side.

    On a landscape export that IS the height, so the shipped composition is reproduced exactly:
    the four rects at 720p / 1080p / 1440p are identical to what the height-fractioned code
    produced, which is checked here by deriving them from the config the same way it did."""
    cfg = ev.OverlayConfig()
    for w, h in ((1280, 720), (1920, 1080), (2560, 1440)):
        p = _painter(w, h)
        assert ev.overlay_unit(w, h) == h, "on a landscape frame the short side is the height"
        m = cfg.margin_frac * h
        gside = cfg.gmeter_frac * h
        assert abs(p._g_rect.width() - gside) < 1e-9 and abs(p._g_rect.x() - (w - m - gside)) < 1e-9
        assert abs(p._strip_rect.x() - m) < 1e-9 and abs(p._strip_rect.y() - m) < 1e-9
        assert abs(p._strip_rect.height() - max(cfg.strip_h_frac * h, 20.0)) < 1e-9
        assert abs(p._readout_rect.height() - max(cfg.readout_h_frac * h, 22.0)) < 1e-9
        assert abs(p._k - h / 1080.0) < 1e-9
    print("ok overlay unit: the 16:9 composition is unchanged at 720/1080/1440")


def test_a_vertical_frame_does_not_get_a_dial_sized_off_its_long_side():
    """THE REASON the unit changed. `gmeter_frac * out_h` on a 1080x1920 frame is 499 px across a
    picture 1080 px wide — 46 % of it. Off the short side it is the same 281 px dial it is at
    1080p landscape, i.e. 26 % of the narrow axis either way, and the map inset stays the shape a
    roughly-square circuit needs instead of a 238x422 portrait slot."""
    tall = _painter(1080, 1920)
    wide = _painter(1920, 1080)
    assert ev.overlay_unit(1080, 1920) == 1080
    assert abs(tall._g_rect.width() - wide._g_rect.width()) < 1e-9, \
        "the dial is the same size on both frames — it is a fraction of the short side"
    assert tall._g_rect.width() / 1080 < 0.30, "the dial must not eat a third of a vertical frame"
    old_height_fractioned = ev.OverlayConfig().gmeter_frac * 1920
    assert tall._g_rect.width() < 0.60 * old_height_fractioned, (
        f"a height-fractioned dial would be {old_height_fractioned:.0f} px wide on a 1080-wide "
        f"frame; this one is {tall._g_rect.width():.0f}")
    # the inset is as wide as the TRACK needs, capped at its own aspect — never the frame's
    cap = ev.OverlayConfig().map_h_frac * 1080 * ev.OverlayConfig().map_max_aspect
    assert tall._map._box.width() <= cap + 1e-9 and tall._map._box.height() <= 1080 * 0.22 + 1e-9
    # every element still lands inside the frame
    for rect in (tall._g_rect, tall._strip_rect, tall._readout_rect, tall._map._box):
        assert rect.left() >= 0 and rect.top() >= 0, rect
        assert rect.right() <= 1080 + 1e-6 and rect.bottom() <= 1920 + 1e-6, rect
    print("ok overlay unit: a 9:16 frame reflows instead of being swamped")


def test_a_row_too_narrow_for_both_its_elements_stacks_them():
    """The corner layout is a composition only while the two elements sharing a row still fit
    beside each other, and three of the four widths are MEASUREMENTS (pills fitted to their text,
    the inset fitted to the track). So the overlap is tested, not assumed — and when it fails the
    right-hand element moves off the row rather than overlapping."""
    from PySide6.QtCore import QRectF
    left = QRectF(10, 10, 100, 20)
    right = QRectF(150, 10, 60, 60)
    assert ev._unstack_row(left, right, 220, 10, downward=True) == right, \
        "elements that already fit are left exactly where they are"
    tight = QRectF(100, 10, 60, 60)            # starts inside left.right() + gap
    moved = ev._unstack_row(left, tight, 170, 10, downward=True)
    assert moved.y() > tight.y() and moved.x() == tight.x(), moved
    assert moved.top() >= left.bottom(), "the moved element clears the one it collided with"
    up = ev._unstack_row(left, tight, 170, 10, downward=False)
    assert up.y() < tight.y(), "the bottom row escapes upward"
    print("ok layout: a row that cannot hold both elements stacks them")


# ============================================================== overlay-only output (ProRes / PNG)
def test_no_encoder_argv_ever_carries_the_qp_that_videotoolbox_ignores():
    """MEASURED on this machine (ffmpeg 7.1.1, sandbox off so a real VT session opens): the same
    2 s clip encoded with `-qp 12` and `-qp 32` produced files of 136,189 bytes EACH. The option
    is accepted, nothing is logged, and it changes nothing. (Two runs at the same `-qp` differ in
    stream hash — VT is not bit-deterministic — so equal SIZE is what shows it.) The working knob
    is `-q:v` on an inverse 1-100 scale: 20 / 50 / 80 gave 42,768 / 75,774 / 187,394 bytes.

    This module is bitrate-targeted and so was never exposed to it. This is the guard that keeps
    it that way, over every encoder path including the two alpha ones."""
    for args in (ev._video_codec_args(ev.VT_H264, 1920, 1080, 30.0),
                 ev._video_codec_args(ev.SW_H264, 1920, 1080, 30.0),
                 ev.alpha_codec_args(ev.ALPHA_PRORES),
                 ev.alpha_codec_args(ev.ALPHA_PNG)):
        assert "-qp" not in args, f"-qp is silently ignored by h264_videotoolbox: {args}"
    vt = ev._video_codec_args(ev.VT_H264, 1920, 1080, 30.0)
    assert "-b:v" in vt and "-maxrate" in vt, f"the VT path must stay bitrate-driven: {vt}"
    print("ok encoders: no path passes the -qp VideoToolbox swallows")


def test_alpha_codec_args_ask_for_a_real_alpha_plane():
    """ProRes 4444 through `prores_ks` (the encoder that offers yuva444p10le; VideoToolbox's
    ProRes does 4444 but only in bgra/ayuv64le), and RGBA PNG for the sequence."""
    pro = ev.alpha_codec_args(ev.ALPHA_PRORES)
    assert pro[:2] == ["-c:v", "prores_ks"], pro
    assert "-profile:v" in pro and pro[pro.index("-profile:v") + 1] == "4444", pro
    assert pro[pro.index("-pix_fmt") + 1] == "yuva444p10le", pro
    png = ev.alpha_codec_args(ev.ALPHA_PNG)
    assert png == ["-c:v", "png", "-pix_fmt", "rgba"], png
    print("ok alpha: both formats ask for a real alpha plane")


def test_an_overlay_only_encode_has_no_source_input_and_no_audio():
    """The overlay-only argv is ONE input — our RGBA pipe. No `-i <source>`, no `-map 1:a`, no
    `-c:a`: the artifact is a track to lay over the footage the editor already has, and a PNG
    sequence could not carry audio anyway. It is also why this path does no source decode."""
    cfg = ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PRORES)
    spec = ev.ExportSpec(src_path="/v/GX010001.MP4", out_path="/out/overlay.mov", lap_id=1,
                         t0=10.0, t1=20.0, config=cfg)
    argv = ev.build_encode_cmd(spec, 1280, 720, 30.0, ev.VT_H264)
    assert argv.count("-i") == 1 and argv[argv.index("-i") + 1] == "pipe:0", argv
    assert "/v/GX010001.MP4" not in argv, f"the source must not be opened at all: {argv}"
    assert "-c:a" not in argv and not any(a.startswith("1:a") for a in argv), argv
    assert argv[argv.index("-pix_fmt") + 1] == "rgba", "the pipe carries straight RGBA"
    assert argv[-1] == "/out/overlay.mov"
    # a PNG sequence writes a numbered pattern INSIDE the chosen directory
    png_cfg = ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PNG)
    png_spec = ev.ExportSpec(src_path="/v/GX010001.MP4", out_path="/out/frames", lap_id=1,
                             t0=10.0, t1=20.0, config=png_cfg)
    assert png_spec.is_png_sequence and not spec.is_png_sequence
    assert ev.build_encode_cmd(png_spec, 1280, 720, 30.0)[-1] == "/out/frames/overlay_%06d.png"
    print("ok overlay-only: one input, no audio, no source decode")


# ================================================ E5 — ProRes 4444 on VideoToolbox, with a fallback
# The owner's "export has become extremely slow" was a remembered overlay-only ProRes at source
# resolution: MK's best lap took 145 s on prores_ks (15.7 fps) and takes 65 s on VideoToolbox
# (35.4 fps), measured through the real renderer. These pin the policy, the argv, the retry and the
# alpha; the VideoToolbox-only assertions skip where no VideoToolbox ProRes session opens (CI).
def test_the_prores_encoder_is_videotoolbox_only_where_its_probe_passes(monkeypatch_restore):
    """`resolve_alpha_encoder` reads the SAME `OverlayConfig.encoder` words as the H.264 path, but
    even a forced "videotoolbox" has to pass the probe: an alpha that comes back premultiplied is a
    wrong file, not a slower one. The pipe's pixel format follows the encoder it feeds."""
    cfg = ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PRORES)
    ev.prores_videotoolbox_usable = lambda: True            # type: ignore[assignment]
    assert ev.resolve_alpha_encoder("auto") == ev.VT_PRORES
    assert ev.resolve_alpha_encoder("videotoolbox") == ev.VT_PRORES
    assert ev.output_codec(cfg) == ev.VT_PRORES
    for word in ("software", "libx264", "sw", "cpu"):
        assert ev.resolve_alpha_encoder(word) == ev.SW_PRORES, word
    ev.prores_videotoolbox_usable = lambda: False           # type: ignore[assignment]
    assert ev.resolve_alpha_encoder("auto") == ev.SW_PRORES
    assert ev.resolve_alpha_encoder("videotoolbox") == ev.SW_PRORES, "a forced VT must pass the probe"
    assert ev.output_codec(cfg) == ev.SW_PRORES
    assert ev.output_codec(ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PNG)) == ev.ALPHA_PNG

    vt = ev.alpha_codec_args(ev.ALPHA_PRORES, ev.VT_PRORES)
    ks = ev.alpha_codec_args(ev.ALPHA_PRORES, ev.SW_PRORES)
    assert vt[:2] == ["-c:v", "prores_videotoolbox"] and ks[:2] == ["-c:v", "prores_ks"], (vt, ks)
    for args in (vt, ks):
        assert args[args.index("-profile:v") + 1] == "4444", args
        assert "colorspace=bt709" in args[args.index("-vf") + 1], "both label (and convert) 709"
    assert vt[vt.index("-pix_fmt") + 1] == "bgra" and vt[vt.index("-allow_sw") + 1] == "1", vt
    assert ks[ks.index("-pix_fmt") + 1] == "yuva444p10le" and "apl0" in ks, ks
    assert ev.alpha_codec_args(ev.ALPHA_PRORES) == ks, "the default stays the portable encoder"

    spec = ev.ExportSpec(src_path="/v/GX010001.MP4", out_path="/out/overlay.mov", lap_id=1,
                         t0=10.0, t1=20.0, config=cfg)

    def pipe(encoder):
        argv = ev.build_encode_cmd(spec, 1280, 720, 30.0, encoder)
        return argv[argv.index("-pix_fmt") + 1], argv[argv.index("-c:v") + 1]
    assert pipe(ev.VT_PRORES) == ("bgra", ev.VT_PRORES), "VideoToolbox is fed its own bgra"
    assert pipe(ev.SW_PRORES) == ("rgba", ev.SW_PRORES)
    assert pipe(ev.VT_H264) == ("rgba", ev.SW_PRORES), "an H.264 name never reaches the alpha argv"

    # The RENDERER asks the same question, and a sequence is not a ProRes at all.
    ev.probe_video_size = lambda _p: (1280, 720, 30.0)      # type: ignore[assignment]
    ev.probe_source_duration = lambda _s: 1.0e9             # type: ignore[assignment]
    s = StubSession(lap_id=1, t0=10.0, dur=10.0, n=200)
    ev.prores_videotoolbox_usable = lambda: True            # type: ignore[assignment]
    assert ev.Renderer(s, spec).encoder == ev.VT_PRORES
    png = ev.ExportSpec(src_path="/v/GX010001.MP4", out_path="/out/frames", lap_id=1, t0=10.0,
                        t1=20.0, config=ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PNG))
    assert ev.Renderer(s, png).encoder == "png"
    print("ok prores: VideoToolbox only where the probe passes; the pipe follows the encoder")


class _VtProresFails(_EnospcEncoder):
    """A VideoToolbox ProRes encode that dies after a few frames the way a session that will not
    keep up does — no claim about the disk in its tail."""

    TAIL = b"[prores_videotoolbox @ 0x1] Error encoding frame: -12905\n"


def test_a_failed_videotoolbox_prores_retries_once_on_prores_ks_but_not_on_a_full_disk(
        monkeypatch_restore):
    """The H.264 path's contract, now on the alpha path: a hardware ProRes encode that fails retries
    ONCE on prores_ks with an identical spec — still ProRes, still alpha, fed rgba — and a disk that
    is really full surfaces as DiskFullError with no second encoder started."""
    s = StubSession(lap_id=2, t0=0.0, dur=2.0, n=400)
    cfg = ev.OverlayConfig(out_height=720, fps_cap=None, overlay_only=True,
                           alpha_codec=ev.ALPHA_PRORES)
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/exports/lap.mov", lap_id=2, t0=0.0,
                         t1=2.0, config=cfg)
    ev.probe_video_size = lambda _p: (1280, 720, 60.0)      # type: ignore[assignment]
    ev.probe_source_duration = lambda _s: 1.0e9             # type: ignore[assignment]
    ev.prores_videotoolbox_usable = lambda: True            # type: ignore[assignment]
    for failing, free, retried in ((_VtProresFails, 90_000_000_000, True),
                                   (_EnospcEncoder, 90_000_000_000, True),
                                   (_EnospcEncoder, 1_000_000, False)):
        started = []

        def fake_popen(cmd, _started=started, _failing=failing, **_kw):
            encoder = cmd[cmd.index("-c:v") + 1]
            _started.append((encoder, cmd[cmd.index("-pix_fmt") + 1]))
            return _failing(fail_after=5) if encoder == ev.VT_PRORES else _FakeProc()

        ev.subprocess.Popen = fake_popen                    # type: ignore[assignment]
        ev.free_bytes = lambda _p, purgeable=True, _f=free: _f   # type: ignore[assignment]
        r = ev.Renderer(s, spec)
        assert r.encoder == ev.VT_PRORES, r.encoder
        try:
            res, raised = r.run(), None
        except Exception as exc:  # noqa: BLE001 — the TYPE is what is under test
            res, raised = None, exc
        if retried:
            assert started == [(ev.VT_PRORES, "bgra"), (ev.SW_PRORES, "rgba")], (
                f"{failing.__name__}: a failed VideoToolbox ProRes did not retry once on "
                f"prores_ks: {started} ({raised!r})")
            assert raised is None and res is not None and res.frames == 120, (raised, res)
        else:
            assert started == [(ev.VT_PRORES, "bgra")], f"a full disk started a retry: {started}"
            assert isinstance(raised, ev.DiskFullError), f"{type(raised).__name__}: {raised}"
            est = ev.estimate_output_bytes(1280, 720, 60.0, ev.clip_seconds(0.0, 2.0, 60.0),
                                           "high", ev.VT_PRORES)
            assert ev.fmt_bytes(ev.floor_bytes(est, ev.VT_PRORES)) in str(raised), str(raised)
    print("ok prores: a failed VideoToolbox encode retries once on prores_ks; a full disk does not")


def _decode_rgba(path, index, w, h):
    """Frame `index` of `path` as an (h, w, 4) uint8 array, decoded by ffmpeg."""
    raw = subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-i", path,
         "-vf", f"select=eq(n\\,{index})", "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba",
         "pipe:1"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4)


def _prores_stream(path):
    return subprocess.run(
        [ev.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
         "stream=codec_name,profile,pix_fmt,color_space:stream_tags=encoder",
         "-of", "default=noprint_wrappers=1", path],
        capture_output=True, text=True, check=True).stdout


def test_the_alpha_round_trip_is_straight_and_the_probe_can_say_no(monkeypatch_restore):
    """The probe that admits VideoToolbox is only worth its cost if it can FAIL: a premultiplying
    path hands an NLE a grey halo round every half-transparent edge. prores_ks — the fallback, and
    CI's encoder — must pass it; the same encoder with a premultiply spliced in front must not.
    VideoToolbox must pass it too: `videotoolbox.test_the_videotoolbox_prores_alpha_round_trip_is_straight`."""
    if not _require_ffmpeg("alpha_round_trip"):
        return
    assert ev.prores_alpha_round_trip(ev.SW_PRORES), "prores_ks lost the straight alpha"
    straight = ev._PRORES_REC709
    ev._PRORES_REC709 = "premultiply=inplace=1," + straight
    try:
        assert not ev.prores_alpha_round_trip(ev.SW_PRORES), (
            "the probe passed a PREMULTIPLIED encode — it cannot tell a grey halo from a white one")
    finally:
        ev._PRORES_REC709 = straight
    print("ok prores: straight alpha on prores_ks; a premultiply fails")


def test_the_videotoolbox_prores_alpha_round_trip_is_straight():
    """The round trip above through prores_videotoolbox, the encoder `resolve_alpha_encoder` picks
    wherever its probe passes: its own registration, SKIPPED by name where no ProRes VideoToolbox
    session opens (tests/_videotoolbox.py)."""
    _videotoolbox.need(ev.ffmpeg_available() and ev.prores_videotoolbox_usable(),
                       "ffmpeg with a ProRes VideoToolbox session")
    assert ev.prores_alpha_round_trip(ev.VT_PRORES), "VideoToolbox ProRes lost the straight alpha"
    print("ok prores: straight alpha on VideoToolbox")


def test_real_prores_overlay_falls_back_to_prores_ks_and_matches_videotoolbox(monkeypatch_restore):
    """A REAL overlay-only render whose VideoToolbox encode is made to fail must come out of the
    prores_ks retry as a real ProRes 4444 with an alpha plane, labelled 709. Where VideoToolbox
    ProRes works, the same frames rendered through it must decode to the SAME picture: identical
    coverage, and the colour under it within ProRes's own rounding — a fallback that changed the
    overlay's colours would not be a fallback."""
    if not _require_ffmpeg("real_prores_fallback"):
        return
    _prores_fallback_and_match(on_videotoolbox=False)


def test_real_prores_overlay_on_videotoolbox_matches_the_prores_ks_fallback(monkeypatch_restore):
    """The comparison half of the check above — the same frames through a WORKING VideoToolbox
    decode to the fallback's picture — which CI cannot run: its own registration, SKIPPED by name
    where no ProRes VideoToolbox session opens (tests/_videotoolbox.py)."""
    _videotoolbox.need(ev.ffmpeg_available() and ev.prores_videotoolbox_usable(),
                       "ffmpeg with a ProRes VideoToolbox session")
    _prores_fallback_and_match(on_videotoolbox=True)


def _prores_fallback_and_match(on_videotoolbox):
    """The two checks above: the prores_ks fallback, then (`on_videotoolbox`) the comparison."""
    import tempfile
    real_probe, vt_here = ev.probe_video_size, on_videotoolbox
    ev.probe_video_size = lambda _p: (640, 360, 30.0)       # type: ignore[assignment]
    ev.probe_source_duration = lambda _s: 1.0e9             # type: ignore[assignment]
    s = StubSession(lap_id=1, t0=0.0, dur=1.0, n=60)
    real_args = ev.alpha_codec_args

    def render(out, encoder_word):
        spec = ev.ExportSpec(src_path="/v/GX010001.MP4", out_path=out, lap_id=1, t0=0.0, t1=1.0,
                             config=ev.OverlayConfig(out_height=360, overlay_only=True,
                                                     alpha_codec=ev.ALPHA_PRORES,
                                                     encoder=encoder_word))
        r = ev.Renderer(s, spec)
        return r.encoder, r.run()

    with tempfile.TemporaryDirectory(prefix="e5-prores-") as td:
        # 1. VideoToolbox forced in and broken: the retry must deliver prores_ks.
        ev.prores_videotoolbox_usable = lambda: True        # type: ignore[assignment]

        def broken(alpha_codec, encoder=ev.SW_PRORES):
            if encoder == ev.VT_PRORES:
                return ["-c:v", ev.VT_PRORES, "-profile:v", "no-such-profile"]
            return real_args(alpha_codec, encoder)
        ev.alpha_codec_args = broken                        # type: ignore[assignment]
        fell_back = os.path.join(td, "fell_back.mov")
        first, res = render(fell_back, "auto")
        assert first == ev.VT_PRORES and res.frames == 30, (first, res)
        info = _prores_stream(fell_back)
        for want in ("codec_name=prores", "profile=4444", "pix_fmt=yuva444p12le",
                     "color_space=bt709", "prores_ks"):
            assert want in info, f"{want!r} missing from the fallback's stream:\n{info}"
        assert real_probe(fell_back)[:2] == (640, 360)
        ks = _decode_rgba(fell_back, 15, 640, 360)
        alpha = ks[..., 3]
        assert (alpha == 0).mean() > 0.5 and (alpha == 255).any() and ((alpha > 0) & (alpha < 255)).any(), (
            "the fallback's alpha is not a real plane (transparent, opaque AND edge pixels)")
        with open(fell_back, "rb") as fh:
            assert b"apl0" in fh.read(), "prores_ks must keep the apl0 vendor tag"
        ev.alpha_codec_args = real_args                     # type: ignore[assignment]
        if not vt_here:
            print("ok prores: a broken VideoToolbox encode fell back to a real prores_ks 4444")
            return
        # 2. The same frames through a working VideoToolbox: the same picture.
        ev.prores_videotoolbox_usable = lambda: True        # type: ignore[assignment]
        on_vt = os.path.join(td, "videotoolbox.mov")
        used, _ = render(on_vt, "auto")
        assert used == ev.VT_PRORES
        info = _prores_stream(on_vt)
        for want in ("profile=4444", "pix_fmt=yuva444p12le", "color_space=bt709"):
            assert want in info, f"{want!r} missing from VideoToolbox's stream:\n{info}"
        with open(on_vt, "rb") as fh:
            assert b"apl0" in fh.read(), "VideoToolbox's frames carry no apl0 vendor tag"
        vt = _decode_rgba(on_vt, 15, 640, 360)
        d_alpha = np.abs(vt[..., 3].astype(int) - alpha.astype(int))
        covered = (alpha == 255) & (vt[..., 3] == 255)
        d_rgb = np.abs(vt[..., :3].astype(int) - ks[..., :3].astype(int))[covered]
        print(f"   VT vs prores_ks, frame 15: alpha max|d| {d_alpha.max()}, opaque rgb mean|d| "
              f"{d_rgb.mean():.2f} max|d| {d_rgb.max()}")
        assert d_alpha.max() <= 2, f"the two encoders disagree on coverage by {d_alpha.max()}"
        assert d_rgb.mean() < 1.0 and d_rgb.max() <= 8, (
            f"the two encoders' colours differ: mean {d_rgb.mean():.2f}, max {d_rgb.max()}")
    print("ok prores: the fallback is a real 4444 with alpha; VideoToolbox decodes to the same picture")


# ======================================================================== export SCOPE
class _ScopeSession(StubSession):
    """StubSession with the accessors the SCOPE machinery needs: several laps on one clock, a
    chapter table so a full-session window has an end, and a best lap."""

    def __init__(self, n_laps=4, lap_dur=30.0, total=200.0):
        super().__init__(lap_id=0, t0=0.0, dur=total, n=400)
        self._n, self._dur = n_laps, lap_dur
        self.chapters = chapters.ChapterMap(["/v/GX010001.MP4"], [total])

    def lap_count(self):
        return self._n

    def valid_lap_ids(self):
        return list(range(self._n))

    def best_lap_id(self):
        return 2

    def lap_window(self, lap_id):
        if lap_id is None or not (0 <= lap_id < self._n):
            return None
        return (10.0 + lap_id * self._dur, 10.0 + (lap_id + 1) * self._dur)

    def lap_at_time(self, t):
        i = int((t - 10.0) // self._dur)
        return i if 0 <= i < self._n and t >= 10.0 else None

    def delta_at_lap(self, lap_id, t):
        return 0.25 if lap_id is not None else None

    def lap_trace_xy(self, lap_id):
        return (self.tx, self.ty) if lap_id is not None and 0 <= lap_id < self._n else None


def test_each_scope_resolves_to_the_laps_it_names():
    s = _ScopeSession()
    assert ev.scope_lap_ids(s, ev.SCOPE_THIS_LAP, 1) == [1]
    assert ev.scope_lap_ids(s, ev.SCOPE_BEST_LAP, 1) == [2], "best lap ignores the selected one"
    assert ev.scope_lap_ids(s, ev.SCOPE_ALL_LAPS, 1) == [0, 1, 2, 3]
    assert ev.scope_lap_ids(s, ev.SCOPE_SESSION, 1) == [], "the session scope renders no one lap"
    # All laps goes through valid_lap_ids where it exists — the SAME set the lap table shows, so a
    # batch cannot include the mis-segmented short lap the rest of the app excludes.
    s.valid_lap_ids = lambda: [0, 3]
    assert ev.scope_lap_ids(s, ev.SCOPE_ALL_LAPS, 1) == [0, 3]
    print("ok scope: each scope names its laps")


def test_an_all_laps_batch_writes_one_file_per_lap():
    """One save prompt, N files, named for the lap the lap table calls it. A single-lap scope keeps
    the exact path the user chose — no suffix appears where there is nothing to disambiguate."""
    assert ev.lap_output_path("/out/ride.mp4", 6) == "/out/ride_lap7.mp4"
    assert ev.lap_output_path("/out/frames", 0) == "/out/frames_lap1.mp4".replace(".mp4", "")
    s = _ScopeSession()
    specs = ev.build_scope_specs(s, "/out/ride.mp4", ev.SCOPE_ALL_LAPS, lap_id=1, lead=0.0)
    assert [sp.lap_id for sp in specs] == [0, 1, 2, 3]
    assert [os.path.basename(sp.out_path) for sp in specs] == [
        "ride_lap1.mp4", "ride_lap2.mp4", "ride_lap3.mp4", "ride_lap4.mp4"]
    assert [sp.is_best for sp in specs] == [False, False, True, False], \
        "each file knows whether IT is the best lap — the ★ BEST mark is per file"
    one = ev.build_scope_specs(s, "/out/ride.mp4", ev.SCOPE_THIS_LAP, lap_id=1, lead=0.0)
    assert len(one) == 1 and one[0].out_path == "/out/ride.mp4"
    print("ok scope: an all-laps batch writes one named file per lap")


def test_padding_puts_the_neighbouring_laps_in_the_file_and_the_spec_says_so():
    """THE ARTEFACT A COMPETITOR SHIPS SILENTLY: a padded per-lap export holds the tail of the
    previous lap and the head of the next. We do not forbid it — run-up is the point — but the
    window is widened by exactly the amount asked for and the spec reports what was APPLIED, which
    is what lets the picker say it in words and the overlay mark the run-up as pending."""
    s = _ScopeSession()
    specs = ev.build_scope_specs(s, "/out/ride.mp4", ev.SCOPE_ALL_LAPS, lap_id=1, lead=5.0)
    lap1 = specs[1]
    assert lap1.lead_in == 5.0 and lap1.lead_out == 5.0
    assert (lap1.t0, lap1.t1) == (35.0, 75.0) and (lap1.lap_t0, lap1.lap_t1) == (40.0, 70.0)
    # the head of the file really is the PREVIOUS lap's footage...
    assert s.lap_at_time(lap1.t0) == 0, "the premise: the run-up is inside lap 0"
    assert s.lap_at_time(lap1.t1 - 1e-6) == 2, "the premise: the run-off is inside lap 2"
    # ...and the overlay still names the EXPORTED lap through all of it, marked not-started
    head = ev.overlay_values_at(s, lap1.t0, lap1)
    assert head.lap_id == 1 and not head.lap_started, head
    print("ok scope: padding is applied exactly, and the overlay still names the exported lap")


def test_a_full_session_export_spans_the_footage_and_follows_the_laps():
    """The whole recording, as one file whose overlay follows the laps.

    The window comes from the FOOTAGE (the chapter table), not `tt[-1]`: `load._clean` trims the
    stationary lead-in and the cool-down out of the telemetry, so a trace-bounded window would
    silently drop real footage off both ends of a clip whose whole promise is that it is
    everything."""
    s = _ScopeSession(total=200.0)
    spec = ev.build_session_spec(s, "/out/whole.mp4")
    assert (spec.t0, spec.t1) == (0.0, 200.0) and spec.follow_laps
    assert spec.best_lap_id == 2
    # every frame names the lap IT is in — not one pinned lap
    assert ev.overlay_values_at(s, 15.0, spec).lap_id == 0
    assert ev.overlay_values_at(s, 45.0, spec).lap_id == 1
    assert ev.overlay_values_at(s, 75.0, spec).lap_id == 2
    assert ev.overlay_values_at(s, 5.0, spec).lap_id is None, "before the first line: no lap"
    # ...and the ★ BEST mark follows with it, instead of being one verdict for the file
    assert not spec.is_best_at(1) and spec.is_best_at(2)
    lap_spec = ev.build_scope_specs(s, "/out/x.mp4", ev.SCOPE_BEST_LAP, lap_id=1)[0]
    assert lap_spec.is_best and lap_spec.is_best_at(0) and lap_spec.is_best_at(2), \
        "a single-lap export's verdict is about the FILE and never varies frame to frame"
    # a session that cannot state its own length is refused rather than guessed at
    bare = StubSession()
    try:
        ev.build_session_spec(bare, "/out/whole.mp4", src_path="/v/a.MP4")
    except ValueError as exc:
        assert "full length" in str(exc) or "end" in str(exc), exc
    else:
        raise AssertionError("a session with no chapter table has no end to render to")
    print("ok scope: a full-session export spans the footage and follows the laps")


def test_the_full_session_inset_draws_the_whole_trace_not_one_lap():
    """The map inset is built for the lap the clip is OF. A full-session clip is of no one lap, so
    it gets the session's own trace — `lap_id=None` through the same two functions."""
    s = _ScopeSession()
    cap = 200.0
    assert ev._inset_width(s, None, 100.0, cap) > 0, "a None lap measures the session trace"
    spec = ev.build_session_spec(s, "/out/whole.mp4")
    p = ev.OverlayPainter(s, spec, 640, 360, 30.0)
    assert p._map._ok, "the full-session inset still draws a line"
    print("ok scope: the full-session inset draws the whole trace")


def test_a_full_range_source_can_never_reach_the_encoder():
    """The other verified trap: hardware encoders REFUSE full-range `yuvj420p`, and the D24 fixture
    is exactly that (`pix_fmt=yuvj420p, color_range=pc`, by ffprobe). It cannot bite here because
    the pipeline is split at an rgb24 pipe — the decode converts on ITS output, and the encode
    declares its own. This pins both ends of that boundary, which is the thing that makes the trap
    unreachable; a future single-process `-i src -c:v <hw>` chain would reintroduce it."""
    spec = ev.ExportSpec(src_path="/v/GX010001.MP4", out_path="/o.mp4", lap_id=1, t0=1.0, t1=4.0)
    dec = ev.build_decode_cmd(spec, 1920, 1080, 30.0)
    assert dec[dec.index("-pix_fmt") + 1] == "rgb24", dec
    assert dec[-1] == "pipe:1" and "-f" in dec and dec[dec.index("-f") + 1] == "rawvideo", dec
    enc = ev.build_encode_cmd(spec, 1920, 1080, 30.0, ev.VT_H264)
    assert enc[enc.index("-pix_fmt") + 1] == "rgb24", "input 0 is our rgb24 pipe"
    assert "yuv420p" in enc, "the encoder names its own OUTPUT format, never the source's"
    assert enc[enc.index("-color_range") + 1] == "tv", "and pins limited range on the hw encoder"
    # Both output dimensions are even on every shape, which is the other half of the same rule.
    for cfg in (ev.OverlayConfig(out_height=1080),
                ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_9_16),
                ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_9_16, frame_fit=ev.FIT_FIT),
                ev.OverlayConfig(out_height=1080, aspect=ev.ASPECT_1_1)):
        for src in ((3840, 2160), (1920, 1080), (2704, 1520), (1921, 1081)):
            w, h = ev.output_size(*src, cfg)
            assert w % 2 == 0 and h % 2 == 0, (cfg.aspect, cfg.frame_fit, src, w, h)
    print("ok pipeline: a full-range source converts at the rgb24 boundary, never at the encoder")


def test_a_fractional_window_muxes_every_frame_it_planned_if_ffmpeg(monkeypatch_restore):
    """X2(a), END TO END ON REAL FFMPEG: the file must hold the frames the export planned.

    A lap window is a real-valued span, so `duration*fps` is almost never whole — and the two ends
    of the pipeline rounded the leftover differently, in opposite directions:

      * BELOW half a frame the DECODER came up short. Its `-t` was the raw window, trimmed in the
        output frame's own timebase, which ROUNDS: the render hit a short read, `produced > 0`
        counted as a clean finish, and the bar stopped one below its own total. Measured on main
        across 96 D24 windows (both recordings, interior and seam-crossing, 25/30/59.94 fps): 18
        came up one frame short, every one of them with a fractional frame under ~0.5.
      * ABOVE it the MUXER dropped the last frame instead, because that frame ENDED past a
        duration-capped audio track: 0060 lap 17 planned 2047, wrote 2047, filed 2046 (video
        68.200 s vs audio 68.229 s) — the case review §4.5 read as a decoder short read. Six of six
        real D24 exports lost exactly one frame this way, on VideoToolbox AND libx264, at 30 AND
        59.94 fps, interior AND across a chapter seam.

    Both windows below are checked THROUGH THE FILE (`ffprobe -count_frames`), not through the
    renderer's own count, because the renderer's count was RIGHT in the mux case and the file was
    still short. Synthetic clip, so this runs wherever ffmpeg does — no 11 GB media."""
    if not _require_ffmpeg("a_fractional_window_muxes_every_frame_it_planned"):
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    src = os.path.join(tmp, "f9_frac_src.mp4")
    _make_syn_clip(src, dur=3.0)
    s = StubSession(lap_id=1, t0=0.0, dur=3.0, n=180)
    try:
        # 45.25 frames at 30 fps (the decoder's rounding case) and 45.75 (the muxer's).
        for label, t1 in (("rounds down", 45.25 / 30.0), ("rounds up", 45.75 / 30.0)):
            out = os.path.join(tmp, "f9_frac_out.mp4")
            if os.path.exists(out):
                os.remove(out)
            spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=0.0, t1=t1,
                                 config=ev.OverlayConfig(out_height=360, fps_cap=30.0,
                                                         encoder="libx264",
                                                         hwaccel_decode=False))
            planned = len(ev.frame_times(0.0, t1, 30.0))
            assert planned == 46, f"{label}: the fixture must be a fractional window, got {planned}"
            res = ev.Renderer(s, spec).run()
            assert res.frames == planned, (
                f"{label}: the decoder ran dry at {res.frames} of {planned} planned frames")
            muxed = int(subprocess.run(
                [ev.FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_frames",
                 "-show_entries", "stream=nb_read_frames", "-of",
                 "default=noprint_wrappers=1:nokey=1", out],
                check=True, capture_output=True, text=True).stdout.strip())
            assert muxed == planned, (
                f"{label}: rendered {res.frames} frames and the file holds {muxed} of {planned}")
            os.remove(out)
    finally:
        if os.path.exists(src):
            os.remove(src)
    print("ok fractional window: every planned frame reaches the file")


def _make_av_clip(path: str, video_s: float, audio_s: float) -> None:
    """A synthetic source whose AUDIO track may be shorter than its VIDEO, the shape the last
    chapter of a GoPro recording has (MK_18_09_26 chapter 2: video 697.346650 s, audio
    697.344000 s; D24 chapter 3 was 10.4 ms short). `+faststart`, like a camera file."""
    subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=size=320x180:rate=30:duration={video_s:g}",
         "-f", "lavfi", "-i", f"sine=frequency=440:sample_rate=48000:duration={audio_s:g}",
         "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", path],
        check=True, capture_output=True)
    assert os.path.getsize(path) > 0


def _stream_seconds(path: str, sel: str) -> float:
    return float(subprocess.run(
        [ev.FFPROBE, "-v", "error", "-select_streams", sel, "-show_entries", "stream=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        check=True, capture_output=True, text=True).stdout.strip())


def _muxed_frames(path: str) -> int:
    return int(subprocess.run(
        [ev.FFPROBE, "-v", "error", "-select_streams", "v:0", "-count_frames",
         "-show_entries", "stream=nb_read_frames", "-of", "default=noprint_wrappers=1:nokey=1",
         path], check=True, capture_output=True, text=True).stdout.strip())


def test_the_last_frame_and_the_audio_both_reach_the_end_of_a_recording_if_ffmpeg(
        monkeypatch_restore):
    """F2 (#205), END TO END: a window that ends where the RECORDING ends keeps its last frame,
    and the file's audio runs to that frame rather than stopping short of it.

    The last chapter's audio is a few ms shorter than its video, so a run-off clamped to the end
    of the footage asks for audio that is not there. Under `-shortest` that let the AUDIO end the
    clip and the muxer dropped the last composited frame (480 -> 479 on D24); `-af apad` fixed it
    by padding the audio with silence without end. E4 took `-shortest` out of the mux (see
    `build_encode_cmd`), so what now has to hold is: nothing cuts the video, and the audio is
    padded to EXACTLY the plan's own length (`apad=whole_dur`) rather than stopping where the
    source's track did. Measured on MK_18_09_26's last chapter before E4: the file's audio ended
    at 16.320 s against a 16.333 s picture, cut at an AAC frame boundary.

    Pinned to libx264, the encoder CI has; the VideoToolbox run is its own registration,
    `videotoolbox.test_the_last_frame_and_the_audio_reach_the_end_on_videotoolbox`."""
    if not _require_ffmpeg("the_last_frame_and_the_audio_both_reach_the_end_of_a_recording"):
        return
    _end_of_recording(["libx264"])


def test_the_last_frame_and_the_audio_reach_the_end_on_videotoolbox(monkeypatch_restore):
    """The check above on h264_videotoolbox, which CI cannot run: its own registration, SKIPPED by
    name where no H.264 VideoToolbox session opens (tests/_videotoolbox.py)."""
    _videotoolbox.need(ev.ffmpeg_available() and ev.videotoolbox_usable(),
                       "ffmpeg with an H.264 VideoToolbox session")
    _end_of_recording(["videotoolbox"])


def _end_of_recording(encoders):
    """The two checks above, on each of `encoders`."""
    import tempfile
    with tempfile.TemporaryDirectory(prefix="pacer-e4-end-") as tmp:
        src = os.path.join(tmp, "last_chapter.mp4")
        _make_av_clip(src, video_s=3.0, audio_s=2.98)
        a_src, v_src = _stream_seconds(src, "a:0"), _stream_seconds(src, "v:0")
        assert a_src < v_src - 0.005, f"the fixture's audio must run out first: {a_src} vs {v_src}"
        s = StubSession(lap_id=1, t0=0.0, dur=3.0, n=180)
        # A run-off clamped to the end of the footage, 55 frames long: 88,000 samples of audio,
        # 85.94 AAC frames, so a mux that ends the audio on a whole AAC frame stops 20 ms short.
        t1 = v_src
        t0 = t1 - 55 / 30.0
        planned = len(ev.frame_times(t0, t1, 30.0))
        assert planned == 55, planned
        clip = ev.clip_seconds(t0, t1, 30.0)
        for enc in encoders:
            out = os.path.join(tmp, f"end_{enc}.mp4")
            spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=t0, t1=t1,
                                 config=ev.OverlayConfig(out_height=180, fps_cap=30.0,
                                                         encoder=enc, hwaccel_decode=False))
            res = ev.Renderer(s, spec).run()
            assert res.frames == planned, f"{enc}: the decoder ran dry at {res.frames}/{planned}"
            muxed = _muxed_frames(out)
            assert muxed == planned, (
                f"{enc}: rendered {res.frames} frames and the file holds {muxed} of {planned} — "
                "the mux dropped the recording's last frame")
            audio = _stream_seconds(out, "a:0")
            assert abs(audio - clip) < 0.001, (
                f"{enc}: the file's audio is {audio:.6f} s against a {clip:.6f} s clip — it stops "
                f"{(clip - audio) * 1000:+.1f} ms short of the last frame")
            os.remove(out)
    print(f"ok end of recording: {planned}/{planned} frames and the audio to the last frame "
          f"({', '.join(encoders)})")


def test_the_muxed_audio_stays_on_the_picture_across_a_seam_if_ffmpeg(monkeypatch_restore):
    """A/V sync THROUGH THE FILE, across a chapter seam: the exported clip's audio must be the
    audio the mux was fed, at the same instant, before the seam and after it.

    `test_export_seam` checks the audio the mux is FED; this checks what the mux WROTE, which is
    where E4 changed the command (no `-shortest`, a bounded pad). Two synthetic chapters with
    deterministic white-noise audio (a sharp cross-correlation peak), a 2 s window centred on the
    seam, rendered by the real renderer on libx264. The file's audio is decoded and located inside
    the audio its mux input decodes to (the same `-ss` and concat list): 0.3 s from the start and
    0.3 s from 1.2 s in (after the seam) must each sit where they sit in the file, to 1 ms.

    What the concat input ITSELF does at the seam is a separate question and E4 does not touch it:
    on these ffmpeg-made chapters the fed audio starts 21.3 ms (one AAC packet) before t0, as
    `test_export_seam` allows, and runs 32.0 ms early after the seam — identically under the old
    `-shortest` mux. On real GoPro chapters it is exact: an E4 export of MK_18_09_26 lap 13, located
    in each chapter's own audio, was +0.00 ms off at the start, on both sides of the seam, and
    -0.02 ms at the end."""
    if not _require_ffmpeg("the_muxed_audio_stays_on_the_picture_across_a_seam"):
        return
    import tempfile

    import test_export_seam as tes
    with tempfile.TemporaryDirectory(prefix="pacer-e4-seam-") as tmp:
        ch_a, ch_b = os.path.join(tmp, "GX010001.MP4"), os.path.join(tmp, "GX020001.MP4")
        tes._make_chapter(ch_a, 4.0, seed=7, hue=0)
        tes._make_chapter(ch_b, 4.0, seed=8, hue=120)
        cm = chapters.ChapterMap([ch_a, ch_b], [tes._probe_duration(ch_a),
                                                tes._probe_duration(ch_b)])
        seam = cm.chapters[1].offset
        t0, t1 = seam - 1.0, seam + 1.0
        src = ev.resolve_video_source(cm, t0, t1, tmp_dir=tmp)
        out = os.path.join(tmp, "seam.mp4")
        try:
            assert src.concat_list_path is not None, "the window must span the seam"
            spec = ev.ExportSpec(out_path=out, lap_id=1, t0=t0, t1=t1, source=src,
                                 config=ev.OverlayConfig(out_height=180, fps_cap=30.0,
                                                         encoder="libx264", hwaccel_decode=False))
            s = StubSession(lap_id=1, t0=t0, dur=t1 - t0, n=40)
            assert ev.Renderer(s, spec).run().frames == 60
            fed = tes._audio_at(spec.local_t0, src.input_args(), 2.0)
        finally:
            src.cleanup()
        muxed = tes._audio_at(0.0, ["-i", out], 2.0)
        sr = tes.SR
        for label, at in (("before the seam", 0.0), ("after the seam", 1.2)):
            piece = muxed[int(at * sr):int((at + 0.3) * sr)]
            lag, sharp = tes._lag_samples(piece, fed)
            assert sharp > 5.0, f"{label}: the correlation peak is not distinctive ({sharp:.1f}x)"
            off = lag / sr - at
            assert abs(off) <= 0.001, (
                f"{label}: the file's audio at {at:.1f} s is its input's at {lag / sr:.5f} s — "
                f"{off * 1000:+.2f} ms off the picture")
            print(f"    {label}: {off * 1000:+.3f} ms (peak {sharp:.0f}x)")
    print("ok seam sync: the muxed audio sits where its input put it, on both sides of the seam")


def test_a_slow_start_behind_videotoolbox_keeps_the_hardware_encode_if_available(
        monkeypatch_restore):
    """E4: a VideoToolbox export whose opening frames arrive slowly must finish ON VideoToolbox.

    With `-shortest` in the mux, ffmpeg held each stream in a sync queue until the others caught
    up. Behind a VideoToolbox encode whose first second arrived below ~10-12 fps, the audio side
    was let loose into that queue and never released: the endless `apad` silence (or, bounded,
    a long export's whole soundtrack) piled up until the queue's 131,072-frame FIFO refused a
    write, and ffmpeg died with "No space left on device" (exit 228) with the disk nearly empty.
    #365 made that cost one libx264 re-render instead of a false "disk full"; this pins that the
    re-render is no longer needed at all.

    Reproduced through the REAL renderer on this synthetic clip — the only stand-in is the pace
    of the first 36 frames (6 fps, the rate two 4K exports at once opened at on this Mac, where
    two of fourteen such exports failed un-throttled). The window SEEKS into the source as every
    real lap does: from t0 = 0 the same feed never overflowed. Measured before E4: 4/4 renders
    at 6 fps and 4/4 at 10 fps failed at frame 30-35; at 15 fps and above none did.

    Its own registration, `videotoolbox.<name>`: SKIPPED by name where no VideoToolbox session
    opens — CI's runner has none, and libx264 never overflowed this queue even fed at 0.5 fps."""
    _videotoolbox.need(ev.ffmpeg_available() and ev.videotoolbox_usable(),
                       "ffmpeg with an H.264 VideoToolbox session")
    import tempfile
    first_failure = []

    class _SlowStart(ev.Renderer):
        """The production renderer, with its first 36 frames paced at 6 fps."""

        def _compose_frame(self, raw):
            frame = super()._compose_frame(raw)
            if self._i < 36:
                time.sleep(1.0 / 6.0)
            return frame

        def _raise_if_disk_full(self, exc):
            first_failure.append(str(exc))
            return super()._raise_if_disk_full(exc)

    with tempfile.TemporaryDirectory(prefix="pacer-e4-slow-") as tmp:
        src = os.path.join(tmp, "src.mp4")
        _make_av_clip(src, video_s=12.0, audio_s=12.0)
        out = os.path.join(tmp, "slow.mp4")
        t0, t1 = 5.3, 9.3
        s = StubSession(lap_id=1, t0=t0, dur=t1 - t0, n=80)
        spec = ev.ExportSpec(src_path=src, out_path=out, lap_id=1, t0=t0, t1=t1,
                             config=ev.OverlayConfig(out_height=360, fps_cap=30.0,
                                                     encoder="videotoolbox",
                                                     hwaccel_decode=False))
        r = _SlowStart(s, spec)
        assert r.encoder == ev.VT_H264
        res = r.run()
        tag = _stream_encoder_tag(out)
        assert "videotoolbox" in tag.lower(), (
            f"the VideoToolbox render failed and was re-rendered on {tag!r}; it failed with: "
            f"{first_failure[0] if first_failure else '(no failure recorded)'}")
        planned = len(ev.frame_times(t0, t1, 30.0))
        assert res.frames == planned and _muxed_frames(out) == planned, (res.frames, planned)
    print(f"ok slow start: VideoToolbox kept, {planned}/{planned} frames")


def test_footage_that_runs_out_mid_clip_is_refused_rather_than_called_finished(monkeypatch_restore):
    """X2(a), the other half: a decode that runs dry with frames still to render is a FAILURE.

    `produced > 0` used to be the whole test, and ffmpeg gives nothing better to go on: a source
    truncated to half its bytes decodes what it has, prints `partial file` on stderr and STILL
    EXITS 0 (measured: 117 of 240 frames, rc=0). So on main this exported half a lap, said "export
    finished", and the only sign was a bar stopped at 117 of 240 — a clip the user would publish
    believing it whole. Now it raises, names how far it got, and the worker drops the partial file.

    The comparison is against the SAME window on the intact clip, so the fixture proves the
    refusal is about the truncation and not about the window."""
    if not _require_ffmpeg("footage_that_runs_out_mid_clip_is_refused"):
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    src, cut = os.path.join(tmp, "f9_trunc_src.mp4"), os.path.join(tmp, "f9_trunc_cut.mp4")
    out = os.path.join(tmp, "f9_trunc_out.mp4")
    # `+faststart` (the moov atom UP FRONT) is what makes this fixture the failure it is meant to
    # be, and it is what production writes. Without it the index sits at the END of the file, so a
    # truncated copy is not half a recording at all — it is an unreadable one, ffprobe refuses it
    # before the render starts, and the test would be exercising a broken FILE instead of footage
    # that runs out. A part-copied recording off a camera has its moov and its first frames.
    subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=8.0",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8.0",
         "-c:v", "libx264", "-g", "30", "-pix_fmt", "yuv420p", "-c:a", "aac",
         "-movflags", "+faststart", "-shortest", src],
        check=True, capture_output=True)
    assert os.path.getsize(src) > 0
    with open(src, "rb") as f:
        whole = f.read()
    with open(cut, "wb") as f:                      # half the bytes; the moov is up front already
        f.write(whole[: len(whole) // 2])
    s = StubSession(lap_id=1, t0=0.0, dur=8.0, n=480)

    def _spec_for(path):
        return ev.ExportSpec(src_path=path, out_path=out, lap_id=1, t0=0.0, t1=7.0,
                             config=ev.OverlayConfig(out_height=240, fps_cap=30.0,
                                                     encoder="libx264", hwaccel_decode=False))
    try:
        planned = len(ev.frame_times(0.0, 7.0, 30.0))
        # INVERSE CONTROL FIRST: the intact clip renders the whole window and is not refused.
        assert ev.Renderer(s, _spec_for(src)).run().frames == planned
        try:
            res = ev.Renderer(s, _spec_for(cut)).run()
            raise AssertionError(
                f"a truncated recording exported as a finished clip: {res.frames} of {planned} "
                "frames, reported as success")
        except ev.TruncatedRenderError as exc:
            assert ev.is_truncated_footage(str(exc)), str(exc)
            assert f"of {planned} frames" in str(exc), str(exc)
    finally:
        for p in (src, cut, out):
            if os.path.exists(p):
                os.remove(p)
    print("ok truncated footage: refused, not reported as finished")


def test_a_decoder_a_single_frame_short_finishes_the_bar_at_what_it_wrote(monkeypatch_restore):
    """The one-frame boundary slack, and what the progress bar says about it (mocked, no ffmpeg).

    A frame is the resolution of the plan's own arithmetic, so a render that ends one frame early
    is finished, not failed — but it must not leave the bar parked at 59 of 60 with nothing more
    coming. The FINAL progress report counts what was written; the mid-render reports still carry
    the plan, because that is what a bar is for."""
    s = StubSession(lap_id=2, t0=0.0, dur=1.0, n=200)
    cfg = ev.OverlayConfig(out_height=120, fps_cap=None, encoder="libx264", workers=1)
    out_w, out_h = ev.output_size(3840, 2160, cfg)
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/out.mp4", lap_id=2, t0=0.0, t1=1.0,
                         config=cfg)
    _patch_pipeline(None, out_w * out_h * 3, nframes=59)      # one short of the 60 planned
    r = ev.Renderer(s, spec)
    assert r.total_frames == 60
    seen = []
    res = r.run(progress=lambda d, t: seen.append((d, t)), chunk=8)
    assert res.frames == 59
    assert seen[-1] == (59, 59), f"the bar must finish at the count it wrote: {seen[-1]}"
    assert any(t == 60 for _d, t in seen[:-1]), (
        f"mid-render the denominator is the PLAN, not a moving target: {seen}")
    print("ok tail slack: one frame short still finishes the bar")


def test_a_decode_that_stops_well_short_raises_instead_of_returning(monkeypatch_restore):
    """The same path as the truncated-file test, without ffmpeg: 40 of 60 frames is not a rounding
    tail, it is a clip that ends 0.33 s early, and it must not return a RenderResult."""
    s = StubSession(lap_id=2, t0=0.0, dur=1.0, n=200)
    cfg = ev.OverlayConfig(out_height=120, fps_cap=None, encoder="libx264", workers=1)
    out_w, out_h = ev.output_size(3840, 2160, cfg)
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/out.mp4", lap_id=2, t0=0.0, t1=1.0,
                         config=cfg)
    _patch_pipeline(None, out_w * out_h * 3, nframes=40)
    try:
        res = ev.Renderer(s, spec).run(chunk=8)
        raise AssertionError(f"a 40-of-60-frame decode returned a result: {res}")
    except ev.TruncatedRenderError as exc:
        assert "40 of 60 frames" in str(exc), str(exc)
    print("ok short decode: refused with the counts named")


# ----------------------------------- X1: what the bar means, and a watchdog that scales
def _mini_spec(**cfg_kw):
    """The spec every X1 mocked render below uses: a 1.0 s window which, at the fake probe's 60 fps
    source with `fps_cap` off, plans exactly 60 frames.

    The frame COUNT is deliberately not a parameter. A spec that plans more frames than the fake
    decoder serves raises `TruncatedRenderError` — correctly, because that IS a clip that ends
    early — and that is a different failure from the one each test below is about."""
    s = StubSession(lap_id=2, t0=0.0, dur=1.0, n=200)
    cfg = ev.OverlayConfig(out_height=120, fps_cap=None, encoder="libx264", workers=1, **cfg_kw)
    out_w, out_h = ev.output_size(3840, 2160, cfg)
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/out.mp4", lap_id=2, t0=0.0, t1=1.0,
                         config=cfg)
    return s, spec, out_w * out_h * 3


def test_the_stall_limit_is_a_floor_that_scales_with_the_renders_own_pace(monkeypatch_restore):
    """The watchdog's limit is `max(floor, multiple x this render's own mean cost per frame)`.

    A CONSTANT IS WRONG AT BOTH ENDS, which is the whole point: large enough never to kill a
    legitimately slow export, it is far too slack to catch a wedge in a fast one. Measured over 11
    real D24 exports (1080p/2160p x VideoToolbox/libx264 x interior/seam laps) the worst gap
    between consecutive frames was 0.783 s and the per-frame median ran 0.018-0.064 s — so the
    shipped 30 s was ~38x the worst real stall, and at every one of those configurations the 10 s
    floor governs while the multiple only takes over past ~0.167 s per frame.

    Asserted on the pure rule, at the boundaries a real render cannot be steered to."""
    s, spec, fb = _mini_spec(watchdog_timeout=10.0, watchdog_frame_multiple=60.0)
    _patch_pipeline(None, fb, nframes=60)
    r = ev.Renderer(s, spec)

    # Before the first frame there is no rate to measure, so the floor stands — and it is the floor
    # that has to cover the ffmpeg spawn and the seek (worst measured: 0.674 s).
    r._render_t0, r._last_progress_t, r._i = 100.0, 100.0, 0
    assert r._stall_limit() == 10.0, r._stall_limit()

    # A fast render (0.02 s/frame, the 1080p VideoToolbox median): 60 x 0.02 = 1.2 s < the floor.
    r._render_t0, r._last_progress_t, r._i = 100.0, 102.0, 100
    assert r._stall_limit() == 10.0, r._stall_limit()

    # A slow one (0.5 s/frame — 7.8x the slowest configuration measured): the render's own pace
    # raises its own limit, which is what stops a long export being killed for being long.
    r._render_t0, r._last_progress_t, r._i = 100.0, 150.0, 100
    assert abs(r._stall_limit() - 30.0) < 1e-9, r._stall_limit()

    # Either knob at zero is the documented off switch (cancel still works).
    r._watchdog_multiple = 0.0
    assert r._stall_limit() == 10.0, r._stall_limit()
    r._watchdog_timeout = 0.0
    assert r._stall_limit() == 0.0, r._stall_limit()
    print("ok stall limit: a floor that scales with the render's own pace")


def test_a_slow_but_healthy_render_survives_a_stall_guard_a_constant_would_trip(monkeypatch_restore):
    """REGRESSION — a render that is merely SLOW must not be aborted, and the INVERSE CONTROL runs
    first so the guard cannot be vacuous.

    The fake decoder hands over a frame every 0.05 s and takes 1.5 s for one of them — a hiccup of
    the shape measured on real media (worst single gap on D24: 0.783 s against a 0.018 s median),
    enlarged so the control is DETERMINISTIC: the supervisor polls every 0.5 s, so a stall only
    reliably trips a limit L when it lasts longer than the poll interval plus L. At 0.6 s and
    L=0.25 s the poll could straddle the hiccup and miss it, which it did.

    With a CONSTANT 0.25 s limit the hiccup is indistinguishable from a wedge and the export dies;
    with the limit scaled to the render's own pace (60 x ~0.05 s = ~3.0 s, twice the hiccup) it is
    plainly just a slow frame. Neither run can false-trip on the ordinary frames, which are only
    0.05 s apart.

    It asserts the failure DIRECTLY in both directions rather than relying on a timeout, so a
    broken subject fails fast and says what broke instead of hanging."""
    slow = dict(nframes=60, frame_delay=0.05, slow_at=40, slow_delay=1.5)

    # --- INVERSE CONTROL: the old fixed-limit behaviour, reproduced by pinning the limit ---
    s, spec, fb = _mini_spec(watchdog_timeout=0.25, watchdog_frame_multiple=60.0)
    _patch_pipeline(None, fb, **slow)
    fixed = ev.Renderer(s, spec)
    fixed._stall_limit = lambda: 0.25         # what a constant watchdog does
    raised = None
    try:
        fixed.run(chunk=1)
    except BaseException as exc:  # noqa: BLE001
        raised = exc
    assert raised is not None, (
        "the inverse control did not trip: a 1.5 s hiccup under a constant 0.25 s limit must be "
        "killed, or this test proves nothing about the scaled limit below")
    assert "stalled" in str(raised).lower(), str(raised)

    # --- the shipped behaviour: the same render, the same hiccup, the same floor ---
    s2, spec2, fb2 = _mini_spec(watchdog_timeout=0.25, watchdog_frame_multiple=60.0)
    _patch_pipeline(None, fb2, **slow)
    res = ev.Renderer(s2, spec2).run(chunk=1)
    assert res.frames == 60, f"a slow but healthy render was cut short at {res.frames} of 60"
    print("ok slow render: survives a hiccup a constant limit would have called a wedge")


def test_the_bar_reaches_its_total_before_the_encoder_is_finalized(monkeypatch_restore):
    """REGRESSION — every frame is written, and the file is not finished. The renderer must report
    the completed count BEFORE it closes the encoder's stdin and waits for the trailer.

    Measured on D24, that wait is 1.6-5.7 s — 12.1 % of one 2160p export's whole wall clock — and
    nothing was reported across it. Worse, the last report before it was whatever the final CHUNK
    boundary happened to be: 60 frames pumped 48 at a time reports 48 and then goes quiet, so a
    real 2047-frame lap froze at 2016 (98.5 %) for the entire mux while the caption claimed about a
    second was left. The bar must reach its own maximum when the last frame is written."""
    s, spec, fb = _mini_spec()
    _patch_pipeline(None, fb, nframes=60)
    r = ev.Renderer(s, spec)
    assert r.total_frames == 60
    assert 60 % 48, "the point of this test is a total that is NOT a whole number of chunks"

    seen = []
    real_finish = r._finish
    marks = {}

    def spy_finish():
        marks.setdefault("reports_before_finish", len(seen))
        return real_finish()
    r._finish = spy_finish
    r.run(progress=lambda d, t: seen.append((d, t)), chunk=48)

    before = seen[: marks["reports_before_finish"]]
    assert before, "nothing at all was reported before the encoder was finalized"
    assert (60, 60) in before, (
        f"the bar never reached its total before the mux began: reports before finalize were "
        f"{before} — a dialog frozen short of the end for the whole write")
    assert before[-1] == (60, 60), (
        f"the last thing said before the mux was {before[-1]}, not the completed count")
    print(f"ok finalize: bar reaches 60 of 60 before the mux (reports before finalize: {before})")


def test_the_single_lap_progress_fill_is_a_time_fraction(monkeypatch_restore):
    """The burned single-lap fill means ELAPSED TIME, and this pins it so it cannot be swapped.

    It is deliberately NOT the compare export's bar, which is a normalized DISTANCE (see
    `export_compare.ComparePainter._paint_progress`): this fill sits under the lap CLOCK in the
    same pill, so it has to agree with the number printed on top of it, while a compare frame has
    two clocks and only the track position is shared. Over all 103 valid laps of both D24
    recordings the two fractions differ by a median 3.47 % / 3.13 % of the bar and by up to 8.93 %
    (0060 lap 20), and NEITHER ever steps backwards — two honest answers to two questions.

    The lap below is built so the distinction is unmissable: the kart covers a quarter of the lap
    in the first half of the LAP TIME and three quarters in the second, so at half time the time
    fraction is 0.50 and the distance fraction is 0.25."""
    class _Lap:
        """Only what `_strip_runs` reads: the lap's window on the telemetry clock."""

        @staticmethod
        def lap_window(_lap_id):
            return (100.0, 200.0)

    def frac_at(t, started=True):
        vals = ev.OverlayValues(t=t, lap_id=1, speed_kmh=None, delta_s=None, g=None,
                                marker_index=None, lap_started=started, lap_finished=False)
        runs = ev._strip_runs(_Lap(), vals, 100.0, False, "standard")
        assert runs is not None
        return runs[3]

    assert frac_at(100.0) == 0.0
    assert abs(frac_at(150.0) - 0.50) < 1e-9, frac_at(150.0)
    assert abs(frac_at(175.0) - 0.75) < 1e-9, frac_at(175.0)
    assert frac_at(200.0) == 1.0, "the fill must be full at the flag"

    # Clamped at BOTH ends: a lead-in cannot drive it negative and a lead-out cannot overrun it.
    assert frac_at(80.0, started=False) == 0.0, "the fill ran before the start line"
    assert frac_at(260.0) == 1.0, "the fill overran the flag through the lead-out"

    # Monotonic, which is the honesty claim that survives whatever it MEANS.
    steps = [frac_at(t) for t in [100.0 + i * (100.0 / 120.0) for i in range(121)]]
    assert all(b >= a for a, b in zip(steps, steps[1:], strict=False)), "the fill moved backwards"

    # ...and it is emphatically not the distance fraction on this lap: a quarter of the distance is
    # covered in the first half of the time, so the two differ by 25 points of the bar at half time.
    assert abs(frac_at(150.0) - 0.25) > 0.2, (
        "the fill tracks distance, not the clock printed on top of it")
    print("ok single-lap fill: a clamped, monotonic TIME fraction")


# Each is its own CTest registration, `footage.<name>` (tests/_footage.py), so a machine without a
# recording reports them SKIPPED by name — they are not in this file's ordinary run or its count.
FOOTAGE_CHECKS = (test_real_render_smoke_if_ffmpeg_and_media,
                  test_real_chaptered_non_first_chapter_render_if_media,
                  test_real_render_quality_levels_if_media)
# Each is its own CTest registration, `videotoolbox.<name>` (tests/_videotoolbox.py): SKIPPED by name
# where VideoToolbox is missing (CI) — not in this file's ordinary run or its count.
VIDEOTOOLBOX_CHECKS = (test_real_videotoolbox_render_if_available,
                       test_a_slow_start_behind_videotoolbox_keeps_the_hardware_encode_if_available,
                       test_the_last_frame_and_the_audio_reach_the_end_on_videotoolbox,
                       test_the_videotoolbox_prores_alpha_round_trip_is_straight,
                       test_real_prores_overlay_on_videotoolbox_matches_the_prores_ks_fallback)


def _call_restored(fn):
    """Run `fn` as the runner below does: inside `_Restore()` when it names `monkeypatch_restore`."""
    import inspect
    if "monkeypatch_restore" in inspect.signature(fn).parameters:
        with _Restore():
            return fn(monkeypatch_restore)
    return fn()


if __name__ == "__main__":
    if _footage.requested():
        sys.exit(_footage.run(FOOTAGE_CHECKS))
    if _videotoolbox.requested():
        sys.exit(_videotoolbox.run(VIDEOTOOLBOX_CHECKS, call=_call_restored))
    import inspect
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and v not in FOOTAGE_CHECKS + VIDEOTOOLBOX_CHECKS]
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
