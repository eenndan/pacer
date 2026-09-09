"""Tests for the overlay export's LEAD-IN / LEAD-OUT (0 / 5 / 10 s of run-up and run-off).

The feature is one number in a dialog and four ways to ship a wrong clip. Each of the four has a
section here:

  1. THE WINDOW, and its clamps. Padding is applied in `lap_window_for_export`, the one funnel both
     callers go through, and BEFORE `resolve_video_source` — which is what picks the chapter file
     and the concat span. A lead-in that reaches back across a chapter seam has to widen the window
     first or it resolves against the wrong file. A negative t0 is the dangerous case: it asks for
     footage from before the recording and stamps every frame |t0| seconds early.
     `guard_validate_window` refuses it on the GLOBAL t0, which is the check that survives whatever
     a source's `time_offset` happens to be. And nothing bounded t1 at all, so an over-long window
     produced a short clip and a progress bar that stopped short of 100 %.
  2. THE LAP, through the lead-in. Laps are contiguous: `lap_at_time` answers lap N−1 for every
     frame of lap N's run-up. The overlay keys off the EXPORTED lap instead, marked pending.
  3. THE LIVE VALUES. Speed and g are time-indexed and valid everywhere; gating them on lap
     membership would burn "— km/h" over footage where the kart is plainly doing 88.
  4. THE G-ENVELOPE. It is the exported lap's: empty before the line, live between the lines,
     frozen after — so the peaks a clip burns are the lap's, whatever padding it carries.

Headless offscreen Qt through the SHIPPED font stack (the strip is measured in pixels); no ffmpeg
except the one real-clip duration test, which gates on ffmpeg like its neighbours in
test_export_video.py.

Run: python tests/test_export_padding.py
"""
import math
import os
import subprocess
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402

from studio import chapters, theme  # noqa: E402
from studio import export_video as ev  # noqa: E402


# --------------------------------------------------------------------------- a padded session
class PadStub:
    """A duck-typed Session with THREE CONTIGUOUS LAPS — which is the whole point of it.

    `test_export_video.StubSession` has one lap and answers None outside it, so it cannot express
    the failure this feature exists to avoid: real laps abut, so the frames of lap N's lead-in are
    inside lap N−1 and `lap_at_time` says so. The g here VARIES with time and is read by a clamped
    lookup (like `gmeter.at_time`, never `None` outside a lap) so the run-up's g is real enough to
    inflate an envelope that is not reset."""

    def __init__(self, lap0=100.0, lap_s=60.0, n_laps=3, rate=50.0, total=None):
        self._lap0, self._lap_s, self._n = lap0, lap_s, n_laps
        end = lap0 + lap_s * n_laps
        self.tt = np.arange(0.0, end + 30.0, 1.0 / rate)     # trace spans the whole recording
        self.tv = 40.0 + 50.0 * np.abs(np.sin(self.tt / 7.0))
        self.tx = np.linspace(0.0, 100.0, len(self.tt))
        self.ty = np.zeros(len(self.tt))
        self.has_gmeter = True
        self.chapters = None if total is None else chapters.ChapterMap(
            ["/v/A.MP4", "/v/B.MP4"], [total / 2.0, total / 2.0])

    # --- lap geometry
    def lap_window(self, lap_id):
        if not (0 <= lap_id < self._n):
            return None
        t0 = self._lap0 + lap_id * self._lap_s
        return (t0, t0 + self._lap_s)

    def lap_at_time(self, t):
        """Half-open, contiguous — exactly `timeline.lap_at_time`. THIS is the trap: a lead-in
        into lap N lands squarely inside lap N−1."""
        for i in range(self._n):
            a, b = self.lap_window(i)
            if a <= t < b:
                return i
        return None

    def best_lap_id(self):
        return 1

    # --- time-indexed lookups: clamped, never None (like the real Session's)
    def index_at_time(self, t):
        i = int(np.searchsorted(self.tt, t))
        return min(max(i, 0), len(self.tt) - 1)

    def g_at_time(self, t):
        """Big in the RUN-UP, small inside lap 1, big again in the RUN-OFF — so an envelope that
        is not reset at the line, or not frozen after it, is visible as a number."""
        lat = 1.6 if t < self._lap0 + self._lap_s else (1.4 if t >= self._lap0 + 2 * self._lap_s
                                                       else 0.4)
        return (lat, -lat, float(np.hypot(lat, lat)))

    def delta_at_lap(self, lap_id, t):
        win = self.lap_window(lap_id)
        if win is None:
            return None
        return round((t - win[0]) / self._lap_s, 4) - 0.5    # -0.5 .. +0.5 across the lap

    def gmeter_source(self):
        return "accl"

    def lap_trace_xy(self, lap_id):
        return self.tx, self.ty


def _spec(s, lap=1, pad=0.0, out="/out/clip.mp4", **cfg):
    return ev.build_lap_spec(s, out, lap, config=ev.OverlayConfig(**cfg),
                             src_path="/in/src.MP4", lead_in=pad, lead_out=pad)


# ==================================================================== 1. the window + its clamps
def test_the_funnel_widens_the_window_without_moving_the_lap():
    """0 / 5 / 10 s, mid-recording, where the footage can honour all of it: the RENDERED window
    grows by the padding at both ends, the LAP window inside it never moves, and an unpadded call
    is byte-identical to `Session.lap_window` (this funnel's contract before the feature)."""
    s = PadStub()
    lap_win = s.lap_window(1)
    assert ev.lap_window_for_export(s, 1) == lap_win, "an unpadded window must not change"
    for pad in (0.0, 5.0, 10.0):
        t0, t1 = ev.lap_window_for_export(s, 1, pad, pad)
        assert (t0, t1) == (lap_win[0] - pad, lap_win[1] + pad), pad
        spec = _spec(s, 1, pad)
        assert (spec.lead_in, spec.lead_out) == (pad, pad), spec
        assert (spec.lap_t0, spec.lap_t1) == lap_win, (pad, spec.lap_t0, spec.lap_t1)
        assert math.isclose(spec.duration, s._lap_s + 2 * pad), spec.duration
        assert math.isclose(spec.lap_duration, s._lap_s), spec.lap_duration
    print("test_the_funnel_widens_the_window_without_moving_the_lap OK (0/5/10 s)")


def test_a_lead_in_that_runs_off_the_front_is_clamped_to_the_recording():
    """THE FIRST LAP. A lap starting 3 s in cannot have 10 s of run-up; the window is clamped to 0
    and the spec reports the 3 s it really got — never a negative t0, and never a shorter lap."""
    s = PadStub(lap0=3.0)
    t0, t1 = ev.lap_window_for_export(s, 0, 10.0, 0.0)
    assert t0 == 0.0, t0
    spec = _spec(s, 0, 10.0)
    assert spec.t0 == 0.0 and spec.lead_in == 3.0, (spec.t0, spec.lead_in)
    assert spec.lap_t0 == 3.0 and spec.lap_duration == s._lap_s, spec
    ev.guard_validate_window(ev.ExportSpec(out_path="/o.mp4", lap_id=0, t0=spec.t0, t1=spec.t1,
                                           src_path="/in.MP4"))          # must not raise
    print(f"test_a_lead_in_that_runs_off_the_front_is_clamped_to_the_recording OK "
          f"(asked 10.0 s, footage had {spec.lead_in} s)")


def test_a_lead_out_past_the_end_is_clamped_to_the_chapter_table():
    """THE LAST LAP, and the missing upper clamp. There was none: a t1 past the footage sizes the
    render for frames that do not exist, the decoder's short read is taken as a clean finish, and
    the export lands short with its bar stuck below 100 %. The bound is the CHAPTER TABLE's
    cumulative duration."""
    s = PadStub(total=285.0)                      # laps run 100..280; the footage ends at 285
    assert ev.footage_duration(s) == 285.0
    _t0, t1 = ev.lap_window_for_export(s, 2, 10.0, 10.0)
    assert t1 == 285.0, t1                        # asked for 290; only 5 s of run-off existed
    spec = _spec(s, 2, 10.0)
    assert spec.lead_out == 5.0 and spec.lap_t1 == 280.0, spec
    assert spec.lap_duration == 60.0, "the clamp took the padding back, not part of the lap"
    # the promise the progress bar makes: every frame it counts is a frame the footage can supply
    assert len(ev.frame_times(spec.t0, spec.t1, 30.0)) == math.ceil(spec.duration * 30.0)
    assert spec.t1 <= ev.footage_duration(s)
    print("test_a_lead_out_past_the_end_is_clamped_to_the_chapter_table OK "
          "(asked t1 290.0, footage ends 285.0)")


def test_the_footage_bound_is_the_chapter_table_and_not_the_telemetry():
    """`load._clean` trims the stationary lead-in and cool-down out of the TELEMETRY, so the trace
    spans less than the video. Bounding the padding by `session.tt` would refuse run-up footage
    that plainly exists — the bound is the chapter table, and a session without one has no bound
    to state (None) rather than a made-up one."""
    s = PadStub(total=400.0)
    s.tt = s.tt[(s.tt >= 90.0) & (s.tt <= 290.0)]         # a trimmed trace, 90..290
    s.tv = s.tv[: len(s.tt)]
    assert ev.footage_duration(s) == 400.0, "the footage bound followed the trimmed telemetry"
    assert float(s.tt[-1]) < 300.0 < ev.footage_duration(s)
    _t0, t1 = ev.lap_window_for_export(s, 2, 0.0, 10.0)
    assert t1 == 290.0, t1                                 # granted in full: the footage has it
    assert ev.footage_duration(PadStub()) is None          # no ChapterMap -> no bound to state
    print("test_the_footage_bound_is_the_chapter_table_and_not_the_telemetry OK")


def test_a_negative_t0_is_refused_on_the_global_clock_whatever_the_source_says():
    """The trap, stated as a measurement rather than as prose.

    A negative t0 asks for footage from before the recording; whatever ffmpeg then decodes, every
    frame is stamped |t0| seconds early. The GLOBAL test is the one that always sees it, because
    `local_t0` is only as honest as the source's `time_offset`: a source whose offset happens to
    equal t0 reports `local_t0 == 0.0` and the local test passes (asserted below on a hand-built
    source — it is exactly what the concat branch used to do for EVERY t0, which is why the global
    test exists).

    On today's concat branch `time_offset` is the first spanned chapter's offset, so `local_t0` is a
    real seek into the span and the LOCAL test sees a negative t0 too — also asserted, because that
    is the property the seam fix bought and a regression would be silent."""
    cm = chapters.ChapterMap(["/v/A.MP4", "/v/B.MP4"], [100.0, 100.0])
    src = ev.resolve_video_source(cm, -4.0, 120.0, tmp_dir=os.environ.get("TMPDIR", "/tmp"))
    try:
        assert src.concat_list_path is not None, "expected the seam-spanning concat branch"
        assert src.time_offset == 0.0, "the span's clock starts at the first spanned chapter"
        bad = ev.ExportSpec(out_path="/o.mp4", lap_id=1, t0=-4.0, t1=120.0, source=src)
        assert bad.local_t0 == -4.0, (
            f"local_t0 {bad.local_t0} — the concat branch must carry a REAL offset into the span, "
            "so a window starting before the recording is visible to the local test too")
        try:
            ev.guard_validate_window(bad)
            raise AssertionError("a negative global t0 was accepted")
        except ValueError as exc:
            assert "before the recording" in str(exc), exc
    finally:
        src.cleanup()
    # A source whose time_offset absorbs t0 hides it from the local test — the global test does not
    # depend on the source at all, and still refuses.
    blind = ev.ExportSpec(out_path="/o.mp4", lap_id=1, t0=-4.0, t1=120.0,
                          source=ev.VideoSource(probe_path="/v/A.MP4", time_offset=-4.0))
    assert blind.local_t0 == 0.0, "premise: this source's local seek looks clean"
    try:
        ev.guard_validate_window(blind)
        raise AssertionError("a negative global t0 was accepted behind a shifted time_offset")
    except ValueError as exc:
        assert "before the recording" in str(exc), exc
    # and the funnel cannot produce one: 10 s of run-up on a lap that starts 3 s in gives t0 = 0.
    s = PadStub(lap0=3.0)
    assert ev.lap_window_for_export(s, 0, 10.0, 10.0)[0] == 0.0
    print("test_a_negative_t0_is_refused_on_the_global_clock_whatever_the_source_says OK "
          "(concat local_t0 == -4.0; a shifted-offset source reports 0.0 and is still refused)")


def test_the_lead_in_is_part_of_the_window_the_source_is_resolved_from():
    """ORDERING, which is the whole feature: the padded window has to exist BEFORE the source is
    resolved. A lap wholly inside chapter 2 resolves to that single file; the same lap with a
    lead-in reaching back over the seam has to resolve to a CONCAT over both. Resolving first and
    padding after would have picked one file and then seeked outside it."""
    s = PadStub(lap0=104.0, lap_s=60.0, n_laps=1)
    s.chapters = chapters.ChapterMap(["/v/A.MP4", "/v/B.MP4"], [100.0, 200.0])
    tight = ev.build_lap_spec(s, "/o.mp4", 0, lead_in=0.0, lead_out=0.0)
    wide = ev.build_lap_spec(s, "/o.mp4", 0, lead_in=10.0, lead_out=10.0)
    try:
        assert tight.source.concat_list_path is None, "the unpadded lap is inside one chapter"
        assert os.path.basename(tight.source.probe_path) == "B.MP4"
        assert tight.source.time_offset == 100.0 and tight.local_t0 == 4.0
        assert wide.source.concat_list_path is not None, (
            "a lead-in across the seam must resolve to a concat span, not to chapter B alone")
        # the span's clock starts at chapter A, so the seek is the run-up's own position in A
        assert wide.source.time_offset == 0.0
        assert wide.local_t0 == 94.0 and wide.t0 == 94.0
    finally:
        tight.source.cleanup()
        wide.source.cleanup()
    print("test_the_lead_in_is_part_of_the_window_the_source_is_resolved_from OK "
          "(single chapter -> concat once the run-up crosses the seam)")


# ==================================================================== 2. the lap, through the pad
def test_the_lead_in_reports_the_exported_lap_pending_not_the_previous_one():
    """Laps are contiguous, so `lap_at_time` answers lap N−1 for the whole run-up — measured here,
    not assumed. With the spec, every frame of the clip reports the EXPORTED lap: pending before
    the line, running between the lines, finished after."""
    s = PadStub()
    spec = _spec(s, 1, 10.0)
    lead_t = spec.lap_t0 - 5.0
    assert s.lap_at_time(lead_t) == 0, "the premise: the lead-in sits inside the previous lap"
    v = ev.overlay_values_at(s, lead_t, spec)
    assert v.lap_id == 1 and not v.lap_started and not v.lap_finished, v
    assert v.delta_s is None, "a Δ before the line is the wrong lap's baseline"
    mid = ev.overlay_values_at(s, spec.lap_t0 + 30.0, spec)
    assert mid.lap_id == 1 and mid.lap_started and not mid.lap_finished
    assert mid.delta_s == s.delta_at_lap(1, spec.lap_t0 + 30.0)
    out = ev.overlay_values_at(s, spec.lap_t1 + 5.0, spec)
    assert out.lap_id == 1 and out.lap_started and out.lap_finished, out
    assert s.lap_at_time(spec.lap_t1 + 5.0) == 2, "the premise: the run-off is inside the NEXT lap"
    # the run-off freezes the Δ at the value the lap FINISHED on rather than extrapolating
    assert out.delta_s == s.delta_at_lap(1, spec.lap_t1 - ev._LAP_CLOCK_EPS), out.delta_s
    # and without a spec the accessor is exactly what it always was
    assert ev.overlay_values_at(s, lead_t).lap_id == 0
    print("test_the_lead_in_reports_the_exported_lap_pending_not_the_previous_one OK")


def _strip_box(out_h=1080):
    cfg = ev.OverlayConfig()
    m = cfg.margin_frac * out_h
    sh = max(cfg.strip_h_frac * out_h, 20.0)
    return QRectF(m, m, 600.0, sh)


def _paint(fn, box, out_h=1080):
    img = QImage(int(out_h * 16 / 9), int(out_h), QImage.Format_RGB888)
    img.fill(0)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    fn(p)
    p.end()
    return img


def _ink_columns(a, b, box):
    return [x for x in range(int(box.x()), int(box.right()) + 1)
            if any(a.pixel(x, y) != b.pixel(x, y)
                   for y in range(int(box.y()), int(box.bottom()) + 1))]


class _NoWindow:
    """A session whose lap has no window — the idiom `test_export_typography` uses to KILL the
    amber progress fill, so the only thing left in the strip is glyph ink and a column comparison
    measures text rather than a bar."""

    @staticmethod
    def lap_window(_lap_id):
        return None


def test_the_strip_paints_nothing_past_its_pending_clock_before_the_line():
    """The Δ and the `★ BEST` mark are claims about a lap that has not been driven yet, so before
    the line the strip carries its lap number, a pending clock, and NOTHING to the right of it.

    Measured against the bare pill (a frame whose `lap_id` is None paints the pill and returns), so
    the columns compared are ink and not background, and the fill is taken out of it with the
    no-window session. The bound is the pending clock's own advance — not a guessed x."""
    from PySide6.QtGui import QFontMetricsF

    s = PadStub()
    spec = _spec(s, 1, 10.0)
    box = _strip_box()
    fm = QFontMetricsF(ev._font(box.height() * 0.54, bold=True))
    inner_x = box.x() + box.height() * ev._STRIP_PAD_L_FRAC
    clock_end = inner_x + fm.horizontalAdvance(f"LAP 2   {ev._PENDING_TIME}")
    halo = 2.2 * box.height() / 44.0 + 1.0

    no_window = _NoWindow()

    def strip(t, session=no_window, is_best=False, lap_id=...):
        vals = ev.overlay_values_at(s, t, spec)
        if lap_id is not ...:
            vals.lap_id = lap_id
        return _paint(
            lambda p: ev._paint_strip(p, box, session, vals, spec.lap_t0, "standard", is_best), box)

    pill = strip(spec.lap_t0 - 5.0, lap_id=None)          # the pill alone: no label, no tail
    pending, running = strip(spec.lap_t0 - 5.0), strip(spec.lap_t0 + 30.0)
    pending_ink = _ink_columns(pill, pending, box)
    running_ink = _ink_columns(pill, running, box)
    assert pending_ink, "the pending strip painted no label at all"
    assert max(pending_ink) <= clock_end + halo, (
        f"the pending strip painted ink out to x={max(pending_ink)}, past its own clock "
        f"(ends {clock_end:.1f}) — a Δ or a ★ BEST mark before the start line")
    assert [x for x in running_ink if x > clock_end + halo], (
        "the running strip painted nothing past the clock either — the guard is vacuous")
    # the ★ BEST verdict is gated identically: is_best must change nothing before the line
    assert not _ink_columns(pending, strip(spec.lap_t0 - 5.0, is_best=True), box), \
        "the best-lap mark was painted before the start line"
    # and the progress fill is empty: having a lap window changes nothing before the line
    assert not _ink_columns(pending, strip(spec.lap_t0 - 5.0, session=s), box), \
        "the amber progress fill painted before the start line"
    print(f"test_the_strip_paints_nothing_past_its_pending_clock_before_the_line OK "
          f"(pending ink ends x={max(pending_ink)}, clock ends {clock_end:.1f})")


def test_the_strips_clock_freezes_at_the_finish_instead_of_running_on():
    """The lead-out holds the lap's finishing time. Unclamped, `t - lap_start` keeps counting, so a
    60 s lap with 10 s of run-off would end its clip reading 1:10.000 for a lap that took
    1:00.000 — a wrong number burned into the file, on the frames a viewer sees last.

    The reference is the frame at the finish line itself (elapsed == the lap time, fill full);
    every run-off frame must be pixel-identical to it."""
    from studio._signal import fmt_time

    s = PadStub()
    spec = _spec(s, 1, 10.0)
    box = _strip_box()

    def strip(t):
        vals = ev.overlay_values_at(s, t, spec)
        return _paint(lambda p: ev._paint_strip(p, box, s, vals, spec.lap_t0, "standard"), box)

    at_line = strip(spec.lap_t1)
    for dt in (0.1, 2.0, 5.0, 9.9):
        assert not _ink_columns(at_line, strip(spec.lap_t1 + dt), box), (
            f"the strip changed {dt}s into the run-off — the clock, the Δ or the fill is still "
            "moving after the flag")
    # what it is frozen AT: the lap's own time, not the clip's length
    assert fmt_time(spec.lap_duration) == "1:00.000" != fmt_time(spec.duration), spec
    print(f"test_the_strips_clock_freezes_at_the_finish_instead_of_running_on OK "
          f"(frozen at {fmt_time(spec.lap_duration)} over a {fmt_time(spec.duration)} clip)")


# ==================================================================== 3. the live values
def test_speed_reads_the_real_speed_through_the_padding():
    """`theme.speed_number` returns an em dash when the lap is None — so keying the readout off
    `lap_at_time` would have burned 10 s of "— km/h" over footage where the kart is doing 88.
    Measured as pixels: the lead-in readout must equal the one for the same speed inside a lap, and
    must NOT equal the dash."""
    s = PadStub()
    spec = _spec(s, 1, 10.0)
    t = spec.lap_t0 - 5.0
    v = ev.overlay_values_at(s, t, spec)
    assert v.speed_kmh is not None and v.speed_kmh > 1.0, v
    assert theme.speed_number(v.speed_kmh, v.lap_id) != "—", "the lead-in readout is a dash"
    box = QRectF(40, 900, 320, 44)

    def readout(vals):
        return _paint(lambda p: ev._paint_readout(p, box, vals, "kmh"), box)

    same = ev.OverlayValues(t=t, lap_id=1, speed_kmh=v.speed_kmh, delta_s=-0.3, g=None,
                            marker_index=v.marker_index)
    dashed = ev.OverlayValues(t=t, lap_id=None, speed_kmh=v.speed_kmh, delta_s=None, g=None,
                              marker_index=v.marker_index)
    assert not _ink_columns(readout(v), readout(same), box), \
        "the lead-in readout differs from the same speed inside the lap"
    assert _ink_columns(readout(v), readout(dashed), box), \
        "the lead-in readout is identical to the no-lap dash"
    print(f"test_speed_reads_the_real_speed_through_the_padding OK "
          f"({theme.speed_number(v.speed_kmh, v.lap_id)} km/h, not an em dash)")


# ==================================================================== 4. the g-envelope
def _drive(s, pad, fps=10.0):
    """Run a padded export's per-frame loop WITHOUT ffmpeg, returning the dial state per frame."""
    spec = _spec(s, 1, pad, out_height=360)
    painter = ev.OverlayPainter(s, spec, 640, 360, fps)
    out = []
    for t in ev.frame_times(spec.t0, spec.t1, fps):
        vals = ev.overlay_values_at(s, float(t), spec)
        out.append((float(t), vals, painter.advance_and_snapshot(vals)))
    return spec, out


def _peaks(st):
    return (round(st.peak_fwd, 6), round(st.peak_back, 6),
            round(st.peak_left, 6), round(st.peak_right, 6))


def test_the_dial_peaks_are_the_same_with_ten_seconds_of_padding_as_with_none():
    """The proof obligation, frame for frame.

    `DialFilter.set_lap` resets the envelope only when it is ALREADY holding a lap, which in a
    padded export it never is: the lead-in is fed `set_g` but no `set_lap`, so the run-up's g grows
    the hull, and the one `set_lap` at the line finds `lap is None` and skips the reset. The
    run-up would be baked into the peaks the clip states for the lap. So the envelope is reset at
    the line and frozen at the finish, and the peaks of every LAP frame must match a 0 s export's
    exactly — including the last frame of the clip, which is in the run-off."""
    s = PadStub()
    _, none = _drive(s, 0.0)
    spec10, ten = _drive(s, 10.0)
    off = int(round(spec10.lead_in * 10.0))            # frames of lead-in at 10 fps
    assert off == 100, off
    assert len(ten) == len(none) + off + int(round(spec10.lead_out * 10.0))
    # the run-up really is fiercer than the lap (else the guard proves nothing)
    assert abs(s.g_at_time(spec10.t0)[0]) > abs(s.g_at_time(spec10.lap_t0 + 1.0)[0]) * 2
    for i, (_t, _v, st) in enumerate(none):
        assert _peaks(ten[i + off][2]) == _peaks(st), (
            f"frame {i} of the lap: peaks {_peaks(ten[i + off][2])} with 10 s of padding vs "
            f"{_peaks(st)} with none — the run-up is in the lap's numbers")
    assert _peaks(ten[-1][2]) == _peaks(none[-1][2]), (
        f"the LAST frame of the padded clip states {_peaks(ten[-1][2])} against the lap's "
        f"{_peaks(none[-1][2])} — the run-off grew the envelope")
    print(f"test_the_dial_peaks_are_the_same_with_ten_seconds_of_padding_as_with_none OK "
          f"({len(none)} lap frames identical; lap peaks {_peaks(none[-1][2])})")


def test_the_dial_shows_no_envelope_at_all_before_the_start_line():
    """The strip says the lap has not started; the dial has to agree. A hull and four peak numbers
    accumulated from the run-up would be a lap-scoped claim about a lap that has not begun."""
    s = PadStub()
    spec, frames = _drive(s, 10.0)
    lead = [f for f in frames if f[0] < spec.lap_t0]
    assert len(lead) == 100, len(lead)
    for t, _v, st in lead:
        assert _peaks(st) == (0.0, 0.0, 0.0, 0.0) and not st.hull_pts, (t, _peaks(st))
    assert lead[-1][2].have, "the live dot was blanked through the run-up too"
    # the dot is LIVE: it moves with the footage the run-up shows
    assert abs(lead[-1][2].fx) > 0.1, lead[-1][2].fx
    first_in_lap = next(f for f in frames if f[0] >= spec.lap_t0)
    assert _peaks(first_in_lap[2]) != (0.0, 0.0, 0.0, 0.0) or not first_in_lap[2].hull_pts
    print("test_the_dial_shows_no_envelope_at_all_before_the_start_line OK (100 lead-in frames)")


# ==================================================================== the render, end to end
def test_a_padded_render_asks_for_every_frame_the_clip_promises(monkeypatch_restore):
    """The frame count follows the PADDED window, and the render reaches it — a progress bar that
    stops short of 100 % is how the missing upper clamp used to present."""
    s = PadStub(lap0=10.0, lap_s=2.0, n_laps=3)
    cfg = dict(out_height=120, fps_cap=None, encoder="libx264", workers=1)
    spec = _spec(s, 1, 1.0, **cfg)
    out_w, out_h = ev.output_size(3840, 2160, spec.config)
    state = _patch_pipeline(out_w * out_h * 3, nframes=240)
    r = ev.Renderer(s, spec)
    assert r.total_frames == math.ceil((2.0 + 2 * 1.0) * 60.0) == 240, r.total_frames
    seen = []
    res = r.run(progress=lambda d, t: seen.append((d, t)))
    assert res.frames == 240 and seen[-1] == (240, 240), (res.frames, seen[-1])
    assert len(state["encoder"].written) == out_w * out_h * 3 * 240
    print("test_a_padded_render_asks_for_every_frame_the_clip_promises OK (240/240)")


def test_a_real_padded_render_is_as_long_as_it_asked_for():
    """A REAL ffmpeg round trip on a synthetic clip: a 0.6 s lap with 0.3 s of run-up and run-off
    must produce a 1.2 s file. This is the end the mocks cannot cover — the decoder's own trim, its
    EOF, and the muxed duration."""
    if not ev.ffmpeg_available():
        assert ".pixi/envs" not in os.environ.get("CONDA_PREFIX", ""), \
            "ffmpeg is a locked pixi dep: this must run in CI, not skip"
        print("skip real_padded_render (no ffmpeg; not in the pixi env)")
        return
    tmp = os.environ.get("TMPDIR", "/tmp")
    src, out = os.path.join(tmp, "pad_src.mp4"), os.path.join(tmp, "pad_out.mp4")
    subprocess.run(
        [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-y",
         "-f", "lavfi", "-i", "testsrc=size=320x180:rate=30:duration=2.0",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=2.0",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", src],
        check=True, capture_output=True)
    s = PadStub(lap0=0.7, lap_s=0.6, n_laps=1)
    try:
        res = ev.render_lap(s, src, out, 0,
                            config=ev.OverlayConfig(out_height=180, fps_cap=30.0,
                                                    encoder="libx264", hwaccel_decode=False),
                            lead_in=0.3, lead_out=0.3)
        assert math.isclose(res.duration, 1.2, abs_tol=1e-6), res.duration
        probed = float(subprocess.run(
            [ev.FFPROBE, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", out],
            check=True, capture_output=True, text=True).stdout.strip())
        assert abs(probed - 1.2) < 0.12, f"asked for 1.2 s, the file is {probed:.3f} s"
        assert res.frames >= 34, res.frames
    finally:
        for p in (src, out):
            if os.path.exists(p):
                os.remove(p)
    print(f"test_a_real_padded_render_is_as_long_as_it_asked_for OK "
          f"(requested 1.200 s, file {probed:.3f} s, {res.frames} frames)")


# --------------------------------------------------------------------------- mocked pipeline
class _FakeProc:
    """The decode/encode stand-ins (same shape as test_export_video's)."""

    def __init__(self, frame_bytes=0, nframes=0, is_decoder=False):
        self.returncode = 0
        self.stdout = self.stdin = None
        if is_decoder:
            self._left, self._fb = nframes, frame_bytes
            self.stdout = type("S", (), {"read": lambda _s, n: self._read(n),
                                         "close": lambda _s: None})()
        else:
            self.written = bytearray()
            self.stdin = type("S", (), {"write": lambda _s, b: self.written.extend(b),
                                        "close": lambda _s: None, "flush": lambda _s: None})()

    def _read(self, _n):
        if self._left <= 0:
            return b""
        self._left -= 1
        return bytes(self._fb)

    def communicate(self, *a, **k):
        return (b"", b"")

    def wait(self, *a, **k):
        return 0

    def kill(self):
        pass


def _patch_pipeline(frame_bytes, nframes):
    state = {}

    def fake_popen(cmd, **kw):
        is_decoder = cmd[-1] == "pipe:1"
        proc = _FakeProc(frame_bytes=frame_bytes, nframes=nframes, is_decoder=is_decoder)
        state["decoder" if is_decoder else "encoder"] = proc
        return proc

    ev.subprocess.Popen = fake_popen                      # type: ignore[assignment]
    ev.probe_video_size = lambda _p: (3840, 2160, 60.0)   # type: ignore[assignment]
    ev.probe_source_duration = lambda _s: 1.0e9           # type: ignore[assignment]
    ev.resolve_encoder = lambda _choice: ev.SW_H264       # type: ignore[assignment]
    return state


class _Restore:
    """Save/restore the module globals the pipeline mocks clobber (no pytest here)."""

    _SAVED = ("probe_video_size", "probe_source_duration", "resolve_encoder")

    def __enter__(self):
        self._popen = ev.subprocess.Popen
        self._saved = {n: getattr(ev, n) for n in self._SAVED}
        return self

    def __exit__(self, *a):
        ev.subprocess.Popen = self._popen
        for name, val in self._saved.items():
            setattr(ev, name, val)


def monkeypatch_restore():
    return None


if __name__ == "__main__":
    import inspect
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
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    if failed:
        print(f"\n{failed}/{len(tests)} export-padding tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} EXPORT-PADDING TESTS OK")
