"""The overlay export's MAP MARKER + TAIL, and an overlay-only file an editor can line up (E6).

The owner's report was "understand the issues with exported video and fix". His export — MK_18_09_26
lap 14 with 5 s either side, ProRes 4444 alpha at 3840x2160 — had four measured defects, and each
has a section here:

  1. THE TAIL WAS DRAWN IN THE WRONG PLACE. `_MapInset.paint` turned the FULL-SESSION marker index
     into a session fraction and applied it to the LAP line, so for lap 14 of 22 the tail sat near
     60 % of the lap all clip long: a median 241 px from the marker at 4K (121 px at 1080p).
  2. THE MARKER STEPPED AT THE GPS RATE: the nearest 10 Hz sample, drawn at 30 fps, held three
     frames and jumped. It is now interpolated at the frame's telemetry time — and HELD across a
     dropout, since the straight line between the two sides of a gap runs across the infield.
  3. THE TAIL WAS `24 * k` POINTS, so a 4K export (k = 2) showed twice the time a 1080p one did.
     It is a fixed 2.4 s now (24 samples of the measured 10.000 Hz GPS).
  4. THE OVERLAY-ONLY FILE COULD NOT BE LINED UP IN AN EDITOR: 30/1 against 60000/1001 footage, no
     timecode, and no word of where it began. It now renders at 30000/1001, starts on a source frame
     its own timecode can name, carries the footage's NDF timecode, and the finished box says where.

The painted checks drive the real `OverlayPainter` over a duck-typed 22-lap session on a circle and
read the marker and tail back out of the pixels, so they measure what the file would show rather
than what a helper returns. The timecode checks use MK_18_09_26's own numbers. One test encodes a
real (tiny) ProRes against a synthetic source with a 59.94 tmcd and reads it back with ffprobe; it
gates on ffmpeg like its neighbours in test_export_video.py.

Run: python tests/test_export_map_sync.py
"""
import json
import math
import os
import subprocess
import sys
import tempfile
from fractions import Fraction

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from PySide6.QtCore import QThread, QTimer, Signal  # noqa: E402
from PySide6.QtWidgets import QMainWindow, QMessageBox  # noqa: E402

from studio import export_controller  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.timeline import nearest_sample  # noqa: E402

_TMP = tempfile.TemporaryDirectory(prefix="pacer-test-map-sync-")
TMP = _TMP.name

NTSC30 = 30000 / 1001
R_M, LAP_S, N_LAPS, HZ = 50.0, 20.0, 22, 10.0


def circle_xy(t):
    """The kart's TRUE position at time t: anticlockwise round a 50 m circle, one lap per 20 s."""
    a = 2 * np.pi * (np.asarray(t, float) % LAP_S) / LAP_S
    return R_M * np.cos(a), R_M * np.sin(a)


class TrackStub:
    """A duck-typed Session: 22 contiguous 20 s laps round one circle, sampled at 10 Hz — the GPS
    rate measured on MK_18_09_26 and SD_19_09_26 (median interval 100.00 ms). Every lap's trace is
    the same circle, which is what makes a session fraction applied to a lap line visible: it
    lands on the right curve at the wrong place. `drop` removes the samples strictly inside a
    (t_a, t_b) window — a planted GPS dropout."""

    def __init__(self, drop=None):
        tt = np.arange(0.0, N_LAPS * LAP_S + 1e-9, 1.0 / HZ)
        if drop is not None:
            tt = tt[(tt <= drop[0] + 1e-9) | (tt >= drop[1] - 1e-9)]
        self.tt = tt
        self.tx, self.ty = circle_xy(tt)
        self.tv = np.full(len(tt), 56.5)                 # km/h: 314 m per 20 s
        self.has_gmeter = False

    def lap_window(self, lap_id):
        return (lap_id * LAP_S, (lap_id + 1) * LAP_S) if 0 <= lap_id < N_LAPS else None

    def lap_at_time(self, t):
        i = int(t // LAP_S)
        return i if 0 <= i < N_LAPS else None

    def index_at_time(self, t):
        return nearest_sample(self.tt, t)

    def delta_at_lap(self, lap_id, t):
        return 0.0

    def g_at_time(self, t):
        return None

    def gmeter_source(self):
        return "accl"

    def lap_trace_xy(self, lap_id):
        w = self.lap_window(lap_id)
        if w is None:
            return None
        m = (self.tt >= w[0]) & (self.tt < w[1])
        return self.tx[m], self.ty[m]


LAP14 = 13                                               # 0-based id of "lap 14" of 22


def _spec(lap=LAP14, lead=5.0, **cfg):
    w = (lap * LAP_S, (lap + 1) * LAP_S)
    return ev.ExportSpec(out_path=os.path.join(TMP, "unused.mov"), lap_id=lap, t0=w[0] - lead,
                         t1=w[1] + lead, src_path=os.path.join(TMP, "none.MP4"),
                         config=ev.OverlayConfig(**cfg), lead_in=lead, lead_out=lead)


def _frame(painter, session, spec, t, w, h):
    """One overlay-only frame at media time t, painted by the REAL painter, as an RGBA array."""
    vals = ev.overlay_values_at(session, t, spec)
    dial = painter.advance_and_snapshot(vals)
    raw = ev._paint_alpha_frame(painter, w, h, vals, dial)
    return np.frombuffer(raw, np.uint8).reshape(h, w, 4).astype(int)


def _read_map(img, box):
    """(marker centre, tail pixel coords) read out of the map inset's box: the marker's hot-coral
    core and the amber comet, by colour — the same masks the evidence tracker used on the
    owner's file."""
    x0, y0 = int(box.x()), int(box.y())
    sub = img[y0:int(math.ceil(box.bottom())), x0:int(math.ceil(box.right()))]
    r, g, b, a = sub[..., 0], sub[..., 1], sub[..., 2], sub[..., 3]
    core = (a > 240) & (r > 200) & (g < 110) & (b < 110)
    tail = (a > 200) & (r > 200) & (g > 140) & (g < 215) & (b < 90)
    ys, xs = np.nonzero(core)
    assert len(xs), "no marker drawn in the map inset"
    tys, txs = np.nonzero(tail)
    return (xs.mean() + x0, ys.mean() + y0), np.column_stack([txs + x0, tys + y0]).astype(float)


def _proj_px(painter, x_m, y_m):
    p = painter._map._proj(float(x_m), float(y_m))
    return np.array([p.x(), p.y()])


# ============================================================ 1 + 3 — where the tail is, and how long
def test_the_tail_ends_on_the_marker_and_reaches_back_2_4_s_in_the_lap_and_its_padding():
    """The tail must touch the marker and trail it by exactly the last 2.4 s of driving — in the
    run-up (inside lap 13), mid-lap and in the run-off (inside lap 15), where there is no lap
    fraction to map through at all.

    On the old code this read a nearest tail pixel ~70-150 px from the marker at every one of these
    frames: the tail sat at 13/21.95 of lap 14's line whatever the frame time."""
    s = TrackStub()
    spec = _spec()
    painter = ev.OverlayPainter(s, spec, 1920, 1080, NTSC30)
    for t in (spec.t0 + 1.0, spec.lap_t0 + 7.3, spec.lap_t0 + 14.9, spec.t1 - 1.0):
        img = _frame(painter, s, spec, t, 1920, 1080)
        (mx, my), tail = _read_map(img, painter._map._box)
        head = _proj_px(painter, *circle_xy(t))
        back = _proj_px(painter, *circle_xy(t - 2.4))
        assert math.hypot(mx - head[0], my - head[1]) < 1.0, (t, (mx, my), head)
        assert len(tail) > 20, f"t={t}: no tail drawn ({len(tail)} px)"
        d = np.hypot(tail[:, 0] - mx, tail[:, 1] - my)
        # The marker's core + rim + halo ring cover ~6 px of radius; the tail starts right there.
        assert d.min() < 9.0, f"t={t}: the tail's nearest pixel is {d.min():.1f} px off the marker"
        chord = float(np.hypot(*(back - head)))
        assert abs(d.max() - chord) < 4.0, (
            f"t={t}: the tail reaches {d.max():.1f} px back, the last 2.4 s span {chord:.1f} px")
        at_back = np.hypot(tail[:, 0] - back[0], tail[:, 1] - back[1]).min()
        assert at_back < 3.0, f"t={t}: nothing of the tail where the kart was 2.4 s ago ({at_back:.1f})"
    print("ok 1: the tail ends on the marker and reaches back 2.4 s, padding included")


def test_the_full_session_scope_draws_the_same_tail():
    """The full-session scope has no lap at all (`lap_id=None` for the inset); the tail still
    ends on the marker. There used to be no tail here: no lap line, no `_lap_pts`."""
    s = TrackStub()
    spec = ev.ExportSpec(out_path=os.path.join(TMP, "unused.mov"), lap_id=-1, t0=0.0,
                         t1=N_LAPS * LAP_S, src_path=os.path.join(TMP, "none.MP4"),
                         follow_laps=True)
    painter = ev.OverlayPainter(s, spec, 1920, 1080, NTSC30)
    t = 301.3
    (mx, my), tail = _read_map(_frame(painter, s, spec, t, 1920, 1080), painter._map._box)
    head = _proj_px(painter, *circle_xy(t))
    assert math.hypot(mx - head[0], my - head[1]) < 1.0
    assert len(tail) > 20 and np.hypot(tail[:, 0] - mx, tail[:, 1] - my).min() < 9.0
    print("ok 1b: the full-session scope's tail ends on its marker")


def test_the_tail_spans_the_same_time_at_4k_as_at_1080p():
    """`24 * k` points was 2.4 s at 1080p and 4.8 s at 4K. The stroke scales with k; the time span
    must not — so the tail's reach, divided by k, is the same at both sizes, and is the last
    2.4 s of the circle at either."""
    s = TrackStub()
    spec = _spec()
    reach = {}
    for w, h in ((1920, 1080), (3840, 2160)):
        painter = ev.OverlayPainter(s, spec, w, h, NTSC30)
        k = painter._k
        t = spec.lap_t0 + 9.05
        (mx, my), tail = _read_map(_frame(painter, s, spec, t, w, h), painter._map._box)
        chord = float(np.hypot(*(_proj_px(painter, *circle_xy(t - 2.4))
                                 - _proj_px(painter, *circle_xy(t)))))
        reach[h] = float(np.hypot(tail[:, 0] - mx, tail[:, 1] - my).max()) / k
        assert abs(reach[h] * k - chord) < 4.0 * k, (h, reach[h] * k, chord)
    assert abs(reach[1080] - reach[2160]) < 2.0, f"tail reach per k: {reach}"
    print(f"ok 3: the tail reaches {reach[1080]:.1f} / {reach[2160]:.1f} px per k at 1080p / 4K")


# ============================================================ 2 — the marker between samples, and a gap
def test_the_marker_moves_every_frame_on_the_trace():
    """At 29.97 fps off a 10 Hz trace the old marker held the nearest sample for three frames and
    jumped (measured on the owner's file: still on 240 of 359 frame steps). Interpolated, it moves
    on every frame and sits on the kart's position at that frame's time."""
    s = TrackStub()
    spec = _spec()
    painter = ev.OverlayPainter(s, spec, 1920, 1080, NTSC30)
    t0 = spec.lap_t0 + 3.0
    prev, steps = None, []
    for n in range(12):
        t = t0 + n / NTSC30
        (mx, my), _tail = _read_map(_frame(painter, s, spec, t, 1920, 1080), painter._map._box)
        head = _proj_px(painter, *circle_xy(t))
        assert math.hypot(mx - head[0], my - head[1]) < 1.0, (n, (mx, my), head)
        if prev is not None:
            steps.append(math.hypot(mx - prev[0], my - prev[1]))
        prev = (mx, my)
    assert min(steps) > 0.3, f"the marker stood still between frames: {np.round(steps, 2)}"
    print(f"ok 2: the marker moves every frame ({min(steps):.2f}..{max(steps):.2f} px)")


def test_across_a_dropout_the_marker_holds_and_the_tail_does_not_cut_the_infield():
    """A 5 s dropout is a quarter of the circle, and the straight line across it runs 11-15 m
    INSIDE the track. Interpolating through it (the planted defect: drop the gap check) puts the
    marker 16 px into the infield 1 s into the gap, and the tail's back end on that chord."""
    t_a, t_b = LAP14 * LAP_S + 6.0, LAP14 * LAP_S + 11.0
    s = TrackStub(drop=(t_a, t_b))
    spec = _spec()
    painter = ev.OverlayPainter(s, spec, 1920, 1080, NTSC30)
    centre = _proj_px(painter, 0.0, 0.0)
    ring_px = float(np.hypot(*(_proj_px(painter, R_M, 0.0) - centre)))
    # Inside the gap, nearer its start: held on the last fix before it.
    (mx, my), _ = _read_map(_frame(painter, s, spec, t_a + 1.0, 1920, 1080), painter._map._box)
    held = _proj_px(painter, *circle_xy(t_a))
    assert math.hypot(mx - held[0], my - held[1]) < 1.0, ((mx, my), held)
    # ...nearer its end: the first fix after it.
    (mx, my), _ = _read_map(_frame(painter, s, spec, t_b - 1.0, 1920, 1080), painter._map._box)
    after = _proj_px(painter, *circle_xy(t_b))
    assert math.hypot(mx - after[0], my - after[1]) < 1.0, ((mx, my), after)
    # 1 s past the gap the 2.4 s tail spans it: every tail pixel must lie ON the ring.
    (mx, my), tail = _read_map(_frame(painter, s, spec, t_b + 1.0, 1920, 1080), painter._map._box)
    off_ring = np.abs(np.hypot(tail[:, 0] - centre[0], tail[:, 1] - centre[1]) - ring_px)
    assert len(tail) > 10 and off_ring.max() < 4.0, (
        f"the tail left the track by {off_ring.max():.1f} px: it bridged the dropout")
    # The same rule, stated on the helpers the inset draws with.
    x, y, stamp = ev.trace_point_at(s.tt, s.tx, s.ty, t_a + 1.0)
    assert (x, y, stamp) == (float(s.tx[s.tt <= t_a][-1]), float(s.ty[s.tt <= t_a][-1]), t_a)
    tx, ty = ev.trace_tail(s.tt, s.tx, s.ty, t_b + 1.0)
    assert np.allclose(np.hypot(tx, ty), R_M, atol=0.05), np.hypot(tx, ty)
    print("ok 2b: across a dropout the marker holds and the tail stays on the track")


# ============================================================ 4 — rate, timecode, and the message
MK_RATE = Fraction(60000, 1001)
MK_TC1, MK_TC2 = "10:51:06:25", "11:24:10:25"      # chapters 1 and 2


def _clock(tc):
    return ev.SourceClock(MK_RATE, tc)


MK_CH1_S = 1985.984                                      # chapter 1's video duration
OWNER_T0 = 1977.4146978899043                            # lap 14's window, 5 s of run-up


def _mk_spec(t0, offset=0.0, name="GX010067.MP4"):
    src = ev.VideoSource(probe_path=f"/footage/{name}", time_offset=offset)
    return ev.ExportSpec(out_path="/x.mov", lap_id=LAP14, t0=t0, t1=t0 + 77.5, source=src,
                         config=ev.OverlayConfig(overlay_only=True))


def test_an_overlay_only_render_divides_the_source_rate_and_a_composite_keeps_30():
    Cfg = ev.OverlayConfig
    ovl = Cfg(overlay_only=True)
    assert ev.resolve_fps(ovl, float(MK_RATE)) == float(Fraction(30000, 1001))
    assert ev.resolve_fps(ovl, 50.0) == 25.0
    assert ev.resolve_fps(ovl, 120000 / 1001) == float(Fraction(30000, 1001))
    assert ev.resolve_fps(ovl, 60.0) == 30.0
    assert ev.resolve_fps(ovl, float(Fraction(30000, 1001))) == float(Fraction(30000, 1001))
    assert ev.resolve_fps(ovl, 24.0) == 24.0
    assert ev.resolve_fps(Cfg(overlay_only=True, fps=30.0), float(MK_RATE)) == 30.0  # explicit
    assert ev.resolve_fps(Cfg(), float(MK_RATE)) == 30.0     # the composite is left as it was
    assert ev.rate_arg(float(Fraction(30000, 1001))) == "30000/1001"
    assert ev.rate_arg(30.0) == "30/1"
    spec = _mk_spec(OWNER_T0)
    cmd = ev.build_encode_cmd(spec, 3840, 2160, NTSC30, ev.VT_PRORES, timecode="11:24:01:25")
    assert cmd[cmd.index("-r") + 1] == "30000/1001", cmd
    assert cmd[cmd.index("-metadata") + 1] == "timecode=11:24:01:25", cmd
    png = ev.ExportSpec(out_path=os.path.join(TMP, "seq"), lap_id=1, t0=0.0, t1=1.0,
                        src_path="/x.MP4",
                        config=ev.OverlayConfig(overlay_only=True, alpha_codec=ev.ALPHA_PNG))
    assert "-metadata" not in ev.build_encode_cmd(png, 64, 36, NTSC30, "png", timecode="01:00:00:00")
    comp = ev.build_encode_cmd(ev.ExportSpec(out_path="/o.mp4", lap_id=1, t0=0.0, t1=1.0,
                                             src_path="/x.MP4"), 1920, 1080, 30.0, ev.SW_H264)
    assert comp[comp.index("-r") + 1] == "30.000000" and "-metadata" not in comp, comp
    print("ok 4a: overlay-only renders at 30000/1001 off 59.94; the composite keeps 30.000")


def test_the_timecode_arithmetic_on_mk_18_09_26():
    """MK_18_09_26: chapter 1 starts at 10:51:06:25, chapter 2 at 11:24:10:25, NDF at 60 frames to
    the TC-second, and chapter 1 is 1985.984 s = 119,040 frames — exactly the gap. The owner's lap
    14 with 5 s of run-up starts 1977.4147 s into chapter 1 and crosses into chapter 2, so it is
    chapter 1's clock it counts on."""
    ch1 = ev.timecode_frames(MK_TC1, 60)
    assert ch1 == ((10 * 60 + 51) * 60 + 6) * 60 + 25
    assert round(MK_CH1_S * MK_RATE) == 119040
    assert ev.format_timecode(ch1 + 119040, 60) == MK_TC2
    assert ev.timecode_frames("10:51:06;25", 60) is None, "drop-frame is not NDF arithmetic"
    assert ev.timecode_frames("10:51:06:60", 60) is None
    assert ev.format_timecode(24 * 3600 * 30 + 5, 30) == "00:00:00:05"

    sync = ev.plan_source_sync(_mk_spec(OWNER_T0), _clock(MK_TC1), NTSC30)
    # Source frame 118526 holds t0; its 60-count number is odd, so it has no 30-count name and
    # frame 0 moves back one more, to 118525: 11:24:01:50 in the footage, 11:24:01:25 at 29.97.
    assert sync.source_frame == 118525, sync
    assert sync.t0 == float(Fraction(118525 * 1001, 60000)) and sync.t0 <= OWNER_T0
    assert OWNER_T0 - sync.t0 < 2 * 1001 / 60000
    assert (sync.source_timecode, sync.timecode) == ("11:24:01:50", "11:24:01:25"), sync
    assert sync.rate == Fraction(30000, 1001) and sync.source_name == "GX010067.MP4"
    # Idempotent: a spec already on that frame (the software retry's) keeps it.
    again = ev.plan_source_sync(_mk_spec(sync.t0), _clock(MK_TC1), NTSC30)
    assert again == sync, again

    # A window that starts in chapter 2 counts on chapter 2's own clock.
    two = ev.plan_source_sync(_mk_spec(1990.0, MK_CH1_S, "GX020067.MP4"), _clock(MK_TC2), NTSC30)
    assert (two.source_frame, two.source_timecode, two.timecode) == (
        239, "11:24:14:24", "11:24:14:12"), two
    assert abs(two.t0 - (MK_CH1_S + 239 * 1001 / 60000)) < 1e-9
    # At the very start of the footage there is nothing earlier to snap to: it moves forward.
    first = ev.plan_source_sync(_mk_spec(0.0), _clock(MK_TC1), NTSC30)
    assert first.source_frame == 1 and first.source_timecode == "10:51:06:26", first
    # No timecode: any source frame will do, and there is nothing to embed.
    bare = ev.plan_source_sync(_mk_spec(OWNER_T0), ev.SourceClock(MK_RATE, None), NTSC30)
    assert bare.source_frame == 118526 and bare.timecode is None, bare
    # A rate that is not a whole division of the source's cannot sit on its frames.
    assert ev.plan_source_sync(_mk_spec(OWNER_T0), _clock(MK_TC1), 30.0) is None
    assert ev.plan_source_sync(_mk_spec(OWNER_T0), None, NTSC30) is None
    print("ok 4b: the MK timecode arithmetic: 11:24:01:50 in GX010067.MP4, 11:24:01:25 at 29.97")


def test_the_finished_box_says_where_an_overlay_only_file_starts():
    """The export-finished box — the REAL `_run_video_export`, with the render faked — states the
    start the worker's `RenderResult.sync` carries: the footage timecode and file, and the timecode
    the .mov carries. A PNG sequence cannot carry one and says so; a composite says nothing new."""
    owner = ev.plan_source_sync(_mk_spec(OWNER_T0), _clock(MK_TC1), NTSC30)
    bodies = []

    def run(sync, out_path, png=False):
        class _Worker(QThread):
            progress = Signal(int, int)
            finished_export = Signal(bool, str)

            def __init__(self, _session, spec, _mk=None):
                super().__init__()
                self.result = ev.RenderResult(spec.out_path, 2, 64, 36, NTSC30, 1.0, sync)

            def cancel(self):
                pass

            def start(self):
                QTimer.singleShot(0, lambda: self.finished_export.emit(True, ""))

            def wait(self, *_a, **_k):
                return True

        class _Spec:
            class _Src:
                def cleanup(self):
                    pass

            def __init__(self):
                self.out_path, self.lap_id, self.source = out_path, LAP14, self._Src()
                self.is_png_sequence = png

        win = QMainWindow()
        win.session, win._load_workers, win._video_worker = None, set(), None
        ctl = export_controller.ExportController(win, 1000)
        orig_worker, orig_exec = export_controller.VideoExportWorker, QMessageBox.exec
        export_controller.VideoExportWorker = _Worker
        QMessageBox.exec = lambda box, *_a, **_k: bodies.append(box.text()) or 0
        try:
            ctl._run_video_export(_Spec(), LAP14)
        finally:
            export_controller.VideoExportWorker = orig_worker
            QMessageBox.exec = orig_exec
        return bodies[-1]

    body = run(owner, "/renders/GX010067_overlay.mov")
    assert ("Starts at 11:24:01:50 in GX010067.MP4 — timecode embedded (11:24:01:25 at the "
            "overlay's 29.97 fps).") in body, body
    body = run(owner, "/renders/GX010067_seq", png=True)
    assert "Starts at 11:24:01:50 in GX010067.MP4. A PNG sequence cannot carry timecode" in body
    bare = ev.plan_source_sync(_mk_spec(OWNER_T0), ev.SourceClock(MK_RATE, None), NTSC30)
    body = run(bare, "/renders/GX010067_overlay.mov")
    assert "Starts 32:57.409 into GX010067.MP4 (source frame 118526)" in body, body
    body = run(None, "/renders/GX010067_overlay.mp4")
    assert "Starts" not in body and "timecode" not in body, body
    print("ok 4c: the finished box says where the file starts, and whether it carries it")


def _ffprobe_json(path, *args):
    return json.loads(subprocess.run([ev.FFPROBE, "-v", "error", *args, "-of", "json", path],
                                     capture_output=True, text=True, check=True).stdout)


def test_a_real_overlay_only_render_carries_the_source_timecode_if_ffmpeg():
    """End to end through the REAL renderer and ffmpeg: a 59.94 source stamped 10:51:06:25 (the
    MK chapter-1 start), a lap window from 1.0 s, rendered overlay-only on prores_ks (pinned: CI has
    no VideoToolbox). Read back with ffprobe, the .mov carries a tmcd equal to the source's own TC
    plus the frames to its first frame, counted at 30000/1001, and frame n sits at n*1001/30000."""
    if not ev.ffmpeg_available():
        print("skip: no ffmpeg")
        return
    src = os.path.join(TMP, "src_5994.mov")
    subprocess.run([ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y", "-f", "lavfi",
                    "-i", "color=c=black:s=64x36:r=60000/1001:d=3", "-c:v", "mpeg4",
                    "-metadata", "timecode=10:51:06:25", src], check=True)

    class Stub(TrackStub):
        video_path = src

        def lap_window(self, lap_id):
            return (1.0, 2.0) if lap_id == 0 else None

        def lap_at_time(self, t):
            return 0 if 1.0 <= t < 2.0 else None

    out = os.path.join(TMP, "ovl.mov")
    cfg = ev.OverlayConfig(out_height=36, overlay_only=True, alpha_codec=ev.ALPHA_PRORES,
                           encoder="software")
    spec = ev.build_lap_spec(Stub(), out, 0, config=cfg)
    try:
        res = ev.Renderer(Stub(), spec).run()
    finally:
        spec.source.cleanup()
    # By hand: 1.0 s is source frame 59 (59.94 frames), 10:51:06:25 + 59 = 60-count 2,344,044,
    # even, so it has a 30-count name: 1,172,022 = 10:51:07:12.
    start60 = ((10 * 60 + 51) * 60 + 6) * 60 + 25 + 59
    assert start60 % 2 == 0
    sec, ff = divmod(start60 // 2, 30)
    expected = f"{sec // 3600:02d}:{sec // 60 % 60:02d}:{sec % 60:02d}:{ff:02d}"
    assert expected == "10:51:07:12"
    assert res.sync is not None and res.sync.timecode == expected, res.sync
    streams = _ffprobe_json(out, "-show_entries",
                            "stream=codec_type,codec_tag_string,r_frame_rate,time_base"
                            ":stream_tags=timecode")["streams"]
    video = [st for st in streams if st["codec_type"] == "video"][0]
    assert any(st.get("codec_tag_string") == "tmcd" for st in streams), streams
    assert {st.get("tags", {}).get("timecode") for st in streams} - {None} == {expected}, streams
    assert video["r_frame_rate"] == "30000/1001", video
    pts = [int(p["pts"]) for p in _ffprobe_json(out, "-select_streams", "v:0", "-show_entries",
                                                "packet=pts")["packets"]]
    tb = Fraction(video["time_base"])
    assert len(pts) == res.frames and res.frames >= 29, (len(pts), res.frames)
    assert all(Fraction(p) * tb == Fraction(n * 1001, 30000) for n, p in enumerate(pts)), pts[:5]
    assert abs(res.sync.t0 - 59 * 1001 / 60000) < 1e-12
    print(f"ok 4d: a real overlay-only .mov carries {expected} at 30000/1001, {len(pts)} frames")


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
        print(f"\n{failed}/{len(tests)} export map/sync tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} EXPORT MAP/SYNC TESTS OK")
