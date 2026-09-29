"""A clip cut on the finish line ENDS on it: one property over every export surface.

WHY IT EXISTS. Three escapes, one piece of arithmetic. A lap window is half-open, so a clip that
stops at its last frame before the line never shows the lap time: the unpadded lap clip read
1:07.465 against the table's 1:07.479 (EXP-2, #416); the compare stopped on 0:46.799 / 0:46.903
against 0:46.808 / 0:46.912 (JOURNEY-4, #439); and once the finish frame existed it was coded as a
starved P-frame whose "0:46.808" read "0:46.308" (#439). Each fix got ONE example, at one lap
length and one rate (test_export_polish's EXP-2, test_export_compare's JOURNEY-4), and none of the
2026-09-28 review's planted defects touched `frame_count`, `with_finish_frame` or the compare's
finish frame (TEETH-5). This file sweeps the arithmetic instead:

  * the unpadded lap clip, through `build_lap_spec` -> `with_finish_frame` -> `frame_times` and
    the decode/encode commands built from it, and the padded one (run-up and run-off of 0.5 s and
    5 s, padded at both ends as the picker pads them): 7 rates x 40 lap lengths spread across the
    fractional frame x 3 start phases x 3 paddings, on an identity clock and a measured-shape one
    (rate, offset and GPS lag, studio/media_clock.py);
  * the render's own plan: the real `Renderer`, composite and overlay-only (whose first frame snaps
    onto a source frame), on a sub-grid, so the checked pipeline is the one that renders;
  * the compare, through `build_compare_spec` + `CompareRenderer`, at the rates it renders (its
    30 fps cap leaves 24, 25, 29.97 and 30), lap B shorter and longer, same and cross recording;
  * the footage: a lap ending within a frame of a chapter seam still has its finish frame inside
    the source the render reads (`_FINISH_FRAME_REACH_S`).

THE EXPECTATIONS ARE FIRST PRINCIPLES, never the product's formula typed again: frame i of a clip
is the picture at t0 + i/fps, and which frames lie before the line is COUNTED on that grid; the
finish is the lap's end put on the picture's clock; the time a finish frame prints is the lap
table's, `fmt_time(session.lap_time(lap))`. A property that recomputed `frame_count` would carry
any defect in it.

ONE NOTE, MEASURED AND LEFT AS ONE. A finish frame prints the lap WINDOW's span, `(start + t) -
start`, which can sit an ulp off the table's t; the two print different milliseconds only when t is
within ~1e-13 s of a half millisecond. The grid's exact frame fractions land there (44 of its 840
identity-clock laps: 49.0125 s at 24 fps prints 0:49.013 against the table's 0:49.012); a million
arbitrary lap times never did. So every lap here is timed `OFF_TIE` off its exact fraction, which
keeps the ties out and moves no frame: 3 ps is inside the 1e-9 of a frame `frame_count` still
calls on the line (17 ps at 60 fps).

ITS OWN FILE, not a third example in test_export_polish or test_export_compare: it is one property
over both exporters, and it needs no ffmpeg, no window and no encode, where those two drive dialogs
and real clips. Every probe the renderers make is stubbed and put back (`_Restore`).

Run: python tests/test_export_ends_on_finish.py
"""
import inspect
import os
import sys
import time
import types
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from _qtapp import themed_app  # noqa: E402

_APP = themed_app()   # the painters measure their pills with Qt fonts

import numpy as np  # noqa: E402
from _synthetic import bare_session, odometer  # noqa: E402

from studio import chapters  # noqa: E402
from studio import export_compare as ec  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio import media_clock as mc  # noqa: E402
from studio._signal import fmt_time, lap_label  # noqa: E402
from studio.timeline import nearest_sample  # noqa: E402

RATES = (24.0, 25.0, 30000 / 1001, 30.0, 50.0, 60000 / 1001, 60.0)
LAP_PHASES = tuple(k / 40 for k in range(40))   # the lap's fractional frame, on the picture's clock
PADS = (0.0, 0.5, 5.0)
BASE = 1801.0      # s: where the owner's PB lap started, so the floats round as real ones do
TOL = 1e-6         # "on the line": far under a frame, far over the float error at 1800 s
OFF_TIE = 3e-12    # s added to every lap time: off the decimal half-ms ties (module docstring)
CLOCKS = {
    "identity": mc.IDENTITY,
    # The shape `media_clock.fit` produces and the GPS lag measured on D24 0062 (media_clock.py).
    "measured": mc.MediaClock(rate=1.0 + 27e-6, offset=0.31, gps_lag=0.4764),
}
CLOCK_B = mc.MediaClock(rate=1.0 - 19e-6, offset=-0.2, gps_lag=0.4589)   # a second recording's


def _start_phases(fps):
    return (0.0, 0.013, 0.5 / fps)


class _Restore:
    """Save/restore the module globals the renderer stubs clobber (no pytest here)."""

    _EV = ("probe_video_size", "probe_source_duration", "probe_source_clock", "resolve_encoder")

    def __enter__(self):
        self._saved = {n: getattr(ev, n) for n in self._EV}
        self._saved_ec = {"probe_video_size": ec.probe_video_size}
        return self

    def __exit__(self, *a):
        for n, v in self._saved.items():
            setattr(ev, n, v)
        for n, v in self._saved_ec.items():
            setattr(ec, n, v)


def monkeypatch_restore():
    return None


def _stub_probes(src_fps: float) -> None:
    """The renderers' only contact with the footage: its frame, its length, its clock. No ffmpeg."""
    ev.probe_video_size = ec.probe_video_size = lambda _p: (1920, 1080, src_fps)
    ev.probe_source_duration = lambda _s: 1.0e9
    ev.probe_source_clock = lambda _p: ev.SourceClock(
        rate=Fraction(src_fps).limit_denominator(1001), timecode="00:00:00:00")
    ev.resolve_encoder = lambda _c: ev.SW_H264


class _LapSession:
    """ONE lap, timed `lap_time` seconds from `start` on the telemetry clock and pictured through
    `clock`: the accessors the lap exporter reads. `lap_window` is the real Session's expression,
    `(start, start + lap_time)`, so the clip's clock and the table's time can part by an ulp."""

    LAP = 11            # printed as LAP 12

    def __init__(self, start, lap_time, clock, chapter_map=None):
        self._start, self._time, self._clock = float(start), float(lap_time), clock
        self.chapters = chapter_map
        self.video_path = "/footage/GX010064.MP4"      # never opened: every probe is stubbed
        # A trace for the map inset and the speed readout, 10 s either side of the lap.
        self.tt = np.linspace(self._start - 10.0, self._start + self._time + 10.0, 64)
        u = np.linspace(0.0, 2 * np.pi, 64)
        self.tx, self.ty, self.tv = 50.0 * np.cos(u), 30.0 * np.sin(u), 60.0 + 20.0 * np.sin(u)
        self.has_gmeter = False

    def lap_window(self, lap_id):
        return (self._start, self._start + self._time) if lap_id == self.LAP else None

    def lap_time(self, _lap_id):
        return self._time

    def media_time(self, t):
        return float(self._clock.to_media(float(t)))

    def telemetry_time(self, t):
        return float(self._clock.to_telemetry(float(t)))

    def lap_at_time(self, t):
        return self.LAP if self._start <= t < self._start + self._time else None

    def index_at_time(self, t):
        return nearest_sample(self.tt, t)

    def delta_at_lap(self, _lap_id, _t):
        return 0.0

    def lap_trace_xy(self, _lap_id):
        return self.tx, self.ty


def _timed(frames, fps, clock):
    """The telemetry lap time of a lap `frames` long ON THE PICTURE'S CLOCK, where the frames are
    (the clock stretches it by its rate), `OFF_TIE` off the exact fraction."""
    return frames / fps / clock.rate + OFF_TIE


def _grid_lap(fps, k, clock):
    """Grid lap k, 40 s to 69 s: a whole number of frames plus LAP_PHASES[k] of one."""
    return _timed(round(40.0 * fps) + k * round(0.75 * fps) + LAP_PHASES[k], fps, clock)


# ----------------------------------------------------------------------- the first principles
def _before_line(t0, fps, line) -> int:
    """How many frames of the clip t0 + i/fps start before `line`, COUNTED on that grid. It is
    also the index of the first frame at or past the line."""
    grid = t0 + np.arange(int((line - t0) * fps) + 3) / fps
    return int(np.count_nonzero(grid < line - TOL))


def _plan_problems(times, t0, fps, line, ends_here) -> list[str]:
    """What is wrong with the frame plan `times` of a clip starting at `t0` whose lap finishes at
    media time `line`; [] when nothing is. `ends_here`: the clip is cut on the line, so its last
    frame must be the finish frame; otherwise run-off follows it."""
    n = len(times)
    if n < 2:
        return [f"a {n}-frame plan"]
    out = []
    if not np.allclose(times, t0 + np.arange(n) / fps, rtol=0.0, atol=1e-9):
        out.append("frame i is not the picture at t0 + i/fps")
    i = _before_line(t0, fps, line)
    if i >= n:
        out.append(f"no frame at or past the finish: the last is {1e3 * (times[-1] - line):+.3f} ms "
                   f"from the line")
    elif ends_here and i != n - 1:
        out.append(f"{n - 1 - i} frame(s) after the finish frame of a clip cut on the line")
    elif not ends_here and i == n - 1:
        out.append("a padded clip with no run-off after its finish frame")
    elif not times[i] - line < 1.0 / fps:
        out.append(f"the finish frame is {(times[i] - line) * fps:.3f} frames past the line")
    return out


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


def _command_problems(spec, fps, n, finish_index) -> list[str]:
    """The decode and encode are asked for exactly the n planned frames (`-t` = n/fps, the
    decoder's `-frames:v` = n), and a clip cut on its line codes its finish frame as a keyframe."""
    out = []
    dec = ev.build_decode_cmd(spec, 640, 360, fps)
    enc = ev.build_encode_cmd(spec, 640, 360, fps, ev.SW_H264)
    want = f"{n / fps:.6f}"
    if (_arg(dec, "-t"), _arg(dec, "-frames:v"), _arg(enc, "-t")) != (want, str(n), want):
        out.append(f"asked for -t {_arg(dec, '-t')} / -frames:v {_arg(dec, '-frames:v')} / "
                   f"audio -t {_arg(enc, '-t')}, not {n} frames ({want} s)")
    key = _arg(enc, "-force_key_frames")
    if finish_index is not None and key != f"expr:gte(n,{finish_index})":
        out.append(f"the finish frame {finish_index} is not forced to a keyframe ({key})")
    return out


def _finish_problems(session, spec, t_finish, t_before) -> list[str]:
    """The finish frame reads finished and prints the lap table's time; the frame before runs."""
    out = []
    lap = session.LAP
    last = ev.overlay_values_at(session, float(t_finish), spec)
    before = ev.overlay_values_at(session, float(t_before), spec)
    runs = ev._strip_runs(session, last, spec.lap_t0, spec.is_best, None)
    label = runs[0] if runs else None
    want = f"LAP {lap_label(lap)}   {fmt_time(session.lap_time(lap))}"
    if not last.lap_finished:
        out.append("the finish frame is not marked finished")
    if label != want:
        out.append(f"the finish frame prints {label!r}, not the table's {want!r}")
    if before.lap_finished:
        out.append("the frame before the finish is already finished")
    return out


class _Tally:
    """Every case's problems, so a failure says how many cases broke and names the first few."""

    def __init__(self, what):
        self.what, self.cases, self.bad = what, 0, []

    def add(self, case, problems):
        self.cases += 1
        if problems:
            self.bad.append(f"  {case}: {'; '.join(problems)}")

    def check(self, at_least):
        assert self.cases >= at_least, f"{self.what}: only {self.cases} cases ran"
        assert not self.bad, (f"{self.what}: {len(self.bad)} of {self.cases} cases break the "
                              f"finish-frame property, e.g.\n" + "\n".join(self.bad[:6]))
        print(f"ok {self.what}: {self.cases} cases")


# ------------------------------------------------------------------ the lap clip, whole grid
def _lap_clip_cases(pads):
    for name, clock in CLOCKS.items():
        for fps in RATES:
            for k in range(len(LAP_PHASES)):
                for p in _start_phases(fps):
                    for pad in pads:
                        session = _LapSession(BASE + p, _grid_lap(fps, k, clock), clock)
                        case = (f"{fps:.3f} fps, lap {session.lap_time(0):.6f} s (phase "
                                f"{LAP_PHASES[k]:.3f}), start +{p:.4f} s, pad {pad} s, {name} clock")
                        yield fps, pad, session, case


def _lap_clip(session, fps, pad):
    """The spec a render of this lap runs and its frame plan, the way `Renderer` builds them."""
    spec = ev.build_lap_spec(session, "/out/lap.mp4", session.LAP, lead_in=pad, lead_out=pad)
    spec = ev.with_finish_frame(spec, fps)
    return spec, ev.frame_times(spec.t0, spec.t1, fps)


def test_an_unpadded_lap_clip_ends_on_its_finish_frame():
    """EXP-2's property at every rate and phase: exactly one frame at or past the line, less than
    a frame past it, marked finished and printing the table's lap time; the frame before it still
    running; decode and encode asked for exactly those frames; the last one forced to a keyframe."""
    tally = _Tally("unpadded lap clips")
    for fps, _pad, session, case in _lap_clip_cases((0.0,)):
        spec, times = _lap_clip(session, fps, 0.0)
        start, finish = (session.media_time(x) for x in session.lap_window(session.LAP))
        problems = _plan_problems(times, start, fps, finish, ends_here=True)
        if not problems:
            n = len(times)
            problems = (_finish_problems(session, spec, times[-1], times[-2])
                        + _command_problems(spec, fps, n, n - 1))
        tally.add(case, problems)
    tally.check(len(RATES) * len(LAP_PHASES) * 3 * len(CLOCKS))


def test_a_padded_clip_shows_the_finish_on_its_first_run_off_frame():
    """A padded clip always had its finish frame, its first run-off frame, and the unpadded one was
    given the same one. Same property, run-up and run-off of 0.5 s and 5 s."""
    tally = _Tally("padded lap clips")
    for fps, pad, session, case in _lap_clip_cases(PADS[1:]):
        spec, times = _lap_clip(session, fps, pad)
        start, finish = (session.media_time(x) for x in session.lap_window(session.LAP))
        problems = _plan_problems(times, start - pad, fps, finish, ends_here=False)
        if not problems:
            i = _before_line(start - pad, fps, finish)
            problems = (_finish_problems(session, spec, times[i], times[i - 1])
                        + _command_problems(spec, fps, len(times), None))
        tally.add(case, problems)
    tally.check(len(RATES) * len(LAP_PHASES) * 3 * len(CLOCKS) * 2)


# ------------------------------------------------------------------ the render's own plan
_SUB = ((0.0, 0), (0.025, 1), (0.5, 2), (0.975, 0))    # (lap phase, start-phase index)


def test_the_render_plans_the_finish_frame_it_was_checked_for(monkeypatch_restore):
    """The real `Renderer` (probes stubbed) builds its own spec and frame plan; hold THAT plan to
    the same property, composite and overlay-only, so the pipeline the grid checks is the one that
    renders. An overlay-only file snaps its first frame back onto a source frame
    (`plan_source_sync`), so its run-up is not a whole number of frames and its finish lands at
    another phase: it must still end on the finish frame."""
    tally = _Tally("rendered lap clips")
    clock = CLOCKS["measured"]
    for fps in RATES:
        _stub_probes(fps)
        for phase, j in _SUB:
            p = _start_phases(fps)[j]
            lap = _timed(round(4.0 * fps) + phase, fps, clock)
            session = _LapSession(BASE + p, lap, clock)
            start, finish = (session.media_time(x) for x in session.lap_window(session.LAP))
            for overlay_only in (False, True):
                cfg = ev.OverlayConfig(out_height=120, encoder="libx264", hwaccel_decode=False,
                                       fps_cap=30.0 if overlay_only else None,
                                       overlay_only=overlay_only)
                spec = ev.build_lap_spec(session, "/out/lap.mov", session.LAP, config=cfg)
                r = ev.Renderer(session, spec)
                rate = spec.output_frame(ev.probe_video_size)[2]
                times, t0 = r._times, float(r._times[0])
                case = (f"{'overlay-only' if overlay_only else 'composite'} over {fps:.3f} fps "
                        f"at {rate:.3f}, lap phase {phase}, start +{p:.4f} s")
                problems = _plan_problems(times, t0, rate, finish, ends_here=True)
                if overlay_only and not t0 <= start + TOL:
                    problems.append(f"the clip starts {t0 - start:+.4f} s after the start line")
                if not overlay_only and abs(t0 - start) > 1e-9:
                    problems.append(f"the clip starts {t0 - start:+.6f} s off the start line")
                if r.total_frames != len(times):
                    problems.append(f"total_frames {r.total_frames} != {len(times)} planned")
                if not problems:
                    problems = _finish_problems(session, r._spec, times[-1], times[-2])
                    if not overlay_only:     # ProRes and PNG code every frame whole anyway
                        problems += _command_problems(r._spec, rate, len(times), len(times) - 1)
                tally.add(case, problems)
    tally.check(len(RATES) * len(_SUB) * 2)


# ------------------------------------------------------------------------------ the compare
COMPARE_SOURCES = (24.0, 25.0, 30000 / 1001, 60000 / 1001)   # -> 24, 25, 29.97, 30 under the cap
LAP_A, LAP_B = 29, 22                                          # LAP 30 against LAP 23, as his PB


def _odometer_lap(start, lap_time, total):
    n = int(lap_time * 10.0) + 2
    return np.linspace(start, start + lap_time, n), odometer(n, 0.1, 0.0, total)[1]


def _time_laps(session, laps: dict):
    """The real Session's window and table time for each lap: `(start, start + t)` and `t`."""
    windows = {i: (s, s + t) for i, (s, t) in laps.items()}
    times = {i: t for i, (_s, t) in laps.items()}
    session.lap_window = windows.get
    session.lap_time = times.__getitem__


def _compare_pair(a_start, la, b_start, lb, cross):
    """Laps A and B as REAL (bare) Sessions — one recording, or two with different clocks."""
    ta, da = _odometer_lap(a_start, la, 1180.0)
    tb, db = _odometer_lap(b_start, lb, 1176.0)
    clock_a = CLOCKS["measured"]
    if not cross:
        s = bare_session({LAP_A: (ta, da), LAP_B: (tb, db)}, best=LAP_A, valid=[LAP_A, LAP_B])
        s.chapters = types.SimpleNamespace(media_clock=clock_a)
        _time_laps(s, {LAP_A: (a_start, la), LAP_B: (b_start, lb)})
        return s, s
    sa = bare_session({LAP_A: (ta, da)}, best=LAP_A, valid=[LAP_A])
    sb = bare_session({LAP_B: (tb, db)}, best=LAP_B, valid=[LAP_B])
    sa.chapters = types.SimpleNamespace(media_clock=clock_a)
    sb.chapters = types.SimpleNamespace(media_clock=CLOCK_B)
    _time_laps(sa, {LAP_A: (a_start, la)})
    _time_laps(sb, {LAP_B: (b_start, lb)})
    return sa, sb


def test_a_compare_ends_on_both_finishes(monkeypatch_restore):
    """JOURNEY-4's property at every rate a compare renders: the clip ends on lap A's finish frame;
    there both captions print the table's lap times, the track bar is full and the gap is the
    lap-time difference; the frame before still runs; the finish frame is a keyframe; and lap B's
    finish, which the lock puts on that same frame, is inside the frames B's decoder is asked
    for. Lap B shorter and longer than A, from the same recording and from another one."""
    tally = _Tally("compare clips")
    for src_fps in COMPARE_SOURCES:
        _stub_probes(src_fps)
        for k in range(8):
            for sign in (-1.0, 1.0):
                cross = k % 2 == 1
                cfg = ec.CompareConfig(out_height=120, encoder="libx264", hwaccel_decode=False)
                rate = ev.resolve_fps(cfg, src_fps)
                p = _start_phases(rate)[k % 3]
                clock_a = CLOCKS["measured"]
                la = _timed(round(6.0 * rate) + k / 8, rate, clock_a)
                lb = la + sign * (0.61 + 0.0371 * k)
                sa, sb = _compare_pair(BASE + p, la, 600.0 + p, lb, cross)
                spec = ec.build_compare_spec(sa, "/out/cmp.mp4", LAP_A, LAP_B, session_b=sb,
                                             config=cfg, src_path_a="/a.MP4", src_path_b="/b.MP4")
                r = ec.CompareRenderer(sa, spec, sb)
                case = (f"{rate:.3f} fps (source {src_fps:.3f}), lap A {la:.6f} s (phase {k / 8}), "
                        f"B {'shorter' if sign < 0 else 'longer'}, "
                        f"{'cross' if cross else 'same'} recording")
                start_a, finish_a = (sa.media_time(x) for x in sa.lap_window(LAP_A))
                problems = _plan_problems(r._times, start_a, rate, finish_a, ends_here=True)
                if r.total_frames != len(r._frames) or len(r._frames) != len(r._times):
                    problems.append(f"{r.total_frames} frames planned, {len(r._frames)} painted")
                if not problems:
                    problems = _compare_finish_problems(r, sa, sb, spec, rate)
                tally.add(case, problems)
    tally.check(len(COMPARE_SOURCES) * 8 * 2)


def _compare_finish_problems(r, sa, sb, spec, rate) -> list[str]:
    out = []
    last, before = r._frames[-1], r._frames[-2]
    want_a = f"{spec.label_a}   {fmt_time(sa.lap_time(LAP_A))}"
    want_b = f"{spec.label_b}   {fmt_time(sb.lap_time(LAP_B))}"
    if (last.caption_a, last.caption_b) != (want_a, want_b):
        out.append(f"the finish frame reads {last.caption_a!r} / {last.caption_b!r}, not the "
                   f"table's {want_a!r} / {want_b!r}")
    if last.fraction != 1.0:
        out.append(f"the finish frame's track bar is at {last.fraction!r}, not full")
    gap = sa.lap_time(LAP_A) - sb.lap_time(LAP_B)
    if abs(last.delta - gap) > 1e-9:
        out.append(f"the finish frame's gap is {last.delta:+.6f}, not the lap times' {gap:+.6f}")
    if not before.fraction < 1.0:
        out.append("the frame before the finish already shows the finish")
    n = len(r._frames)
    enc = ev.build_encode_cmd(r._spec, r.geometry.out_w, r.geometry.out_h, rate, ev.SW_H264)
    if _arg(enc, "-force_key_frames") != f"expr:gte(n,{n - 1})":
        out.append(f"the finish frame {n - 1} is not forced to a keyframe "
                   f"({_arg(enc, '-force_key_frames')})")
    # Lap B's finish on B's own picture clock, and the frame of B's stream nearest it.
    finish_b = sb.media_time(sb.lap_window(LAP_B)[1])
    if abs(float(r.lock.t_b_media[-1]) - finish_b) > 1e-6:
        out.append(f"pane B ends {1e3 * (r.lock.t_b_media[-1] - finish_b):+.3f} ms off its finish")
    j = int(round((finish_b - spec.t_b0) * rate))
    decoded = int(_arg(ev.build_decode_cmd(r._b_spec(), 64, 36, rate), "-frames:v"))
    if not j < decoded:
        out.append(f"pane B's finish is its frame {j}, past the {decoded} its decoder is asked for")
    return out


# --------------------------------------------------------------------------- the footage
def _chapter_holding(chapter_map, t) -> str:
    return os.path.basename(chapter_map.chapters[chapter_map.chapter_at(t)].path)


def _read_by(source) -> list[str]:
    return ev._source_names(source).split("+")


def test_the_finish_frame_is_inside_the_footage_at_a_chapter_seam():
    """A lap ending just before a chapter seam can have its finish frame in the NEXT chapter, up
    to a frame past the line: the source must read that chapter too. `_FINISH_FRAME_REACH_S` is
    how far past the line both exporters resolve it; the frame itself is found here by counting,
    and a seam 0.85 of a frame past the line with the finish frame 0.9 past it is exactly where a
    reach shorter than one 24 fps frame (41.7 ms) leaves the finish frame outside the source."""
    tally = _Tally("finish frames at a chapter seam")
    seam = 1500.0
    for fps in RATES:
        for phase in (0.0, 0.1, 0.5):
            for short_of_seam in (0.1, 0.6, 0.85):        # frames between the line and the seam
                clock = CLOCKS["measured"]
                cmap = chapters.ChapterMap(["/footage/GX010064.MP4", "/footage/GX020064.MP4"],
                                           [seam, 900.0], media_clock=clock)
                finish = seam - short_of_seam / fps
                length = (round(40.0 * fps) + phase) / fps
                start_t = clock.to_telemetry(finish - length)
                session = _LapSession(start_t, clock.to_telemetry(finish) - start_t, clock, cmap)
                case = f"{fps:.3f} fps, phase {phase}, line {short_of_seam} frame(s) before the seam"
                spec = ev.build_lap_spec(session, "/out/lap.mp4", session.LAP)
                try:
                    wide = ev.with_finish_frame(spec, fps)
                    times = ev.frame_times(wide.t0, wide.t1, fps)
                    line = session.media_time(session.lap_window(session.LAP)[1])
                    problems = _plan_problems(times, wide.t0, fps, line, ends_here=True)
                    need = _chapter_holding(cmap, float(times[-1]))
                    if need not in _read_by(spec.source):
                        problems.append(f"the finish frame is in {need}, the source reads "
                                        f"{'+'.join(_read_by(spec.source))} (reach "
                                        f"{ev._FINISH_FRAME_REACH_S} s < a frame?)")
                finally:
                    spec.source.cleanup()
                tally.add(case, problems)
    # The compare resolves both panes the same way, each against its own recording's seam.
    for rate in (24.0, 25.0, 30000 / 1001, 30.0):
        for phase, short_of_seam in ((0.0, 0.1), (0.1, 0.85), (0.5, 0.6)):
            clock = CLOCKS["measured"]
            finish = seam - short_of_seam / rate
            la = _timed(round(6.0 * rate) + phase, rate, clock)
            a_start = clock.to_telemetry(finish) - la
            sa, sb = _compare_pair(a_start, la, a_start, la + 0.37, cross=True)
            sa.chapters = chapters.ChapterMap(["/footage/GX010064.MP4", "/footage/GX020064.MP4"],
                                              [seam, 900.0], media_clock=clock)
            b_finish = sb.media_time(a_start + la + 0.37)
            sb.chapters = chapters.ChapterMap(
                ["/footage/GX010068.MP4", "/footage/GX020068.MP4"],
                [b_finish + 0.4 / rate, 900.0], media_clock=CLOCK_B)
            spec = ec.build_compare_spec(sa, "/out/cmp.mp4", LAP_A, LAP_B, session_b=sb)
            case = f"compare at {rate:.3f} fps, phase {phase}, both lines just before a seam"
            try:
                wide = ev.with_finish_frame(spec, rate)
                times = ev.frame_times(wide.t0, wide.t1, rate)
                a_line = sa.media_time(sa.lap_window(LAP_A)[1])
                problems = _plan_problems(times, wide.t0, rate, a_line, ends_here=True)
                j = int(round((b_finish - spec.t_b0) * rate))
                for label, cm, src, t in (("A", sa.chapters, spec.source, float(times[-1])),
                                          ("B", sb.chapters, spec.source_b,
                                           spec.t_b0 + j / rate)):
                    need = _chapter_holding(cm, t)
                    if need not in _read_by(src):
                        problems.append(f"pane {label}'s finish frame is in {need}, its source "
                                        f"reads {'+'.join(_read_by(src))}")
            finally:
                spec.cleanup()
            tally.add(case, problems)
    tally.check(len(RATES) * 3 * 3 + 4 * 3)


if __name__ == "__main__":
    tests = [
        test_an_unpadded_lap_clip_ends_on_its_finish_frame,
        test_a_padded_clip_shows_the_finish_on_its_first_run_off_frame,
        test_the_render_plans_the_finish_frame_it_was_checked_for,
        test_a_compare_ends_on_both_finishes,
        test_the_finish_frame_is_inside_the_footage_at_a_chapter_seam,
    ]
    failed, began = 0, time.perf_counter()
    for t in tests:
        try:
            if "monkeypatch_restore" in inspect.signature(t).parameters:
                with _Restore():
                    t(monkeypatch_restore)
            else:
                t()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    print(f"({time.perf_counter() - began:.2f} s)")
    if failed:
        print(f"\n{failed}/{len(tests)} ends-on-finish tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} ends-on-finish tests passed")
