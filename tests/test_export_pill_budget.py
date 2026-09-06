"""The burned-in overlay's PILL BUDGET, and the clip's promised LENGTH — SW-EXPORT F2/F3/F4.

#202/#203 fitted both HUD pills to their own ink, and the regression sweep measured the fit as
EXACT: worst `need - pill` = +0.00 px across every frame of all 21 valid D24 laps x 3 heights x
2 units x 0/5/10 s of padding. Zero slack is the intended design — the pills were 35-46 % full
before — but it makes the WIDTH BUDGET load-bearing, and the budget was an estimate re-derived
from the same series the painter reads, under different conventions:

  * F4, `_speed_text_candidates` — masked `tt < spec.t1` while the per-frame lookup is
    `Session.index_at_time`, i.e. `np.searchsorted`, a CEILING. Every frame past the last
    in-window sample reads the first sample AT OR AFTER `t1`, which the mask excluded. At 10 Hz
    GPS / 30 fps that is the last 2-3 frames of every clip.
  * F3, `_peak_abs_delta` — sampled `np.linspace(lap_t0, lap_t1, 128, endpoint=False)`, so it
    never asked about `lap_t1 - _LAP_CLOCK_EPS`: the exact instant a lead-out FREEZES the clock
    and the Δ on, and holds for the whole run-off.

Both are now gone: `_burned_runs` walks the render's OWN frame times through the render's OWN
per-frame lookup and the painters' OWN run builders (`_readout_runs` / `_strip_runs`), so the set
of strings the pill is measured against IS the set the compositor draws. These tests pin that
end-to-end, on COMPOSITED pixels — a width computed from the same helper the fix uses would agree
with itself no matter what.

And F2, `build_encode_cmd` — a run-off clamped to the end of the footage delivered a clip ONE
FRAME short while every indicator said otherwise. Not the decoder: measured on real D24, the loop
requested 480 frames, WROTE 480, and the file held 479. `-shortest` ends the clip with the
shortest stream, and a GoPro chapter's audio can be shorter than its video (D24 chapter 3: video
1590.005083 s vs audio 1589.994667 s), so the muxer dropped the last video frame.

Run: QT_QPA_PLATFORM=offscreen python tests/test_export_pill_budget.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()   # module scope, before any painting: the SHIPPED face

import numpy as np  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from studio import export_video as ev  # noqa: E402
from studio import theme  # noqa: E402

FPS = 30.0
# A flat frame colour nothing in the overlay uses, so "differs from the background" == "ink".
BG = (17, 34, 51)
EDGES = ("left", "right", "top", "bottom")


class Stub:
    """A session built to order: a per-sample (tt, tv) km/h track, one lap window, a Δ curve.

    Exactly the accessors `overlay_values_at` / `_paint_strip` / `_MapInset` reach for — the same
    shape as tests/test_export_video.py's StubSession, with the trace and the Δ curve injectable
    so a worst case can be CONSTRUCTED (neither F3 nor F4 fires on the D24 fixture)."""

    def __init__(self, tt, tv, lap_t0, lap_t1, delta_fn, lap_id=7, has_g=False):
        self.tt = np.asarray(tt, dtype=float)
        self.tv = np.asarray(tv, dtype=float)
        self.tx = np.linspace(0.0, 100.0, len(self.tt))
        self.ty = np.zeros(len(self.tt))
        self._lap = lap_id
        self._w = (float(lap_t0), float(lap_t1))
        self._delta = delta_fn
        self.has_gmeter = has_g

    def lap_window(self, lap_id):
        return self._w if lap_id == self._lap else None

    def lap_at_time(self, t):
        return self._lap if self._w[0] <= t < self._w[1] else None

    def index_at_time(self, t):
        if not len(self.tt):
            return None
        return int(min(max(np.searchsorted(self.tt, t), 0), len(self.tt) - 1))

    def delta_at_lap(self, lap_id, t):
        return self._delta(float(t)) if lap_id == self._lap else None

    def g_at_time(self, t):
        return (0.3, -0.4, 0.5) if self.has_gmeter else None

    def gmeter_source(self):
        return "accl"

    def _lap_trace_xyt(self, lap_id):
        return (self.tx, self.ty, self.tt) if lap_id == self._lap else None


def _spec(session, out_h, *, lead_in=0.0, lead_out=0.0, is_best=False,
          unit="kmh", palette=None):
    t0, t1 = ev.lap_window_for_export(session, session._lap, lead_in, lead_out)
    lap_t0, lap_t1 = session.lap_window(session._lap)
    return ev.ExportSpec(
        src_path="/x.MP4", out_path="/o.MP4", lap_id=session._lap, t0=t0, t1=t1,
        config=ev.OverlayConfig(out_height=out_h, speed_unit=unit, palette=palette),
        is_best=is_best, lead_in=lap_t0 - t0, lead_out=t1 - lap_t1)


def _composite(painter, out_w, out_h, vals):
    """One frame, painted onto a flat background exactly as `_paint_packed_frame` paints it.

    Read from the COMPOSITE, never from a per-element grab: the pill, the halo, the drop shadow
    and the glyph fill are four separate passes and only the composite knows where the ink ended
    up.

    The `.copy()` is load-bearing: `np.frombuffer(img.constBits())` is a VIEW into the QImage's
    buffer, and `img` dies with this frame. Returned uncopied, the array aliases freed memory —
    which at 480p..1080p still read back the right pixels and at 2160p came back as near-black,
    i.e. a harness bug that reports a 47.52 px overflow that is not there."""
    img = QImage(out_w, out_h, QImage.Format_RGB888)
    img.fill(BG[0] << 16 | BG[1] << 8 | BG[2])
    painter.paint_frame_with_state(img, vals, painter.advance_and_snapshot(vals))
    bpl = img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * out_h).reshape(out_h, bpl)
    return arr[:, : 3 * out_w].reshape(out_h, out_w, 3).copy()


def _chrome_only(painter, out_w, out_h, vals):
    """The same frame with the two painters' RUNS emptied — the pill, its stroke and its progress
    fill, and nothing else.

    THIS IS THE CONTROL, and it is what makes the sweep below need no fudge factor. A pill draws a
    `1.0 * k` px rounded-rect stroke that Qt centres on the rect and antialiases, so some of the
    pill's OWN chrome always lands outside its rect — 1.37 px at a 22 px pill, 1.52 px at the
    86.4 px one 2160p asks for. Subtracting a measured control states the real question ("does the
    TEXT reach outside the pill?") instead of guessing a tolerance that is wrong at one end of the
    resolution range or the other.

    `_paint_readout` / `_paint_strip` look their runs up by module name, so swapping them here is
    the same call the painters make. `_strip_runs` keeps its progress FRACTION, so the amber fill
    still paints (it is clipped to the pill, so it cannot add ink outside either way)."""
    real_r, real_s = ev._readout_runs, ev._strip_runs

    def blank_strip(*a):
        runs = real_s(*a)
        return None if runs is None else ("", "", runs[2], runs[3])

    ev._readout_runs = lambda _v, _u: ("", "")
    ev._strip_runs = blank_strip
    try:
        return _composite(painter, out_w, out_h, vals)
    finally:
        ev._readout_runs, ev._strip_runs = real_r, real_s


def _outside(arr, rect):
    """Ink outside `rect` on each of the FOUR edges, in px (positive == outside), measured in a
    band around the pill so the dial / map inset / the other pill cannot be mistaken for it."""
    x0, y0, x1, y1 = rect.x(), rect.y(), rect.right(), rect.bottom()
    pad = 240
    ys0, xs0 = max(0, int(y0) - pad), max(0, int(x0) - pad)
    # crop BEFORE the comparison: a 2160p frame is 24 MB and this runs four times per frame
    crop = arr[ys0:int(y1) + pad, xs0:int(x1) + pad]
    band = np.any(crop != np.array(BG, dtype=np.uint8), axis=2)
    ys, xs = np.nonzero(band)
    if not len(xs):
        return {"left": 0.0, "right": 0.0, "top": 0.0, "bottom": 0.0}
    xs, ys = xs + xs0, ys + ys0
    return {"left": float(x0 - xs.min()), "right": float(xs.max() + 1 - x1),
            "top": float(y0 - ys.min()), "bottom": float(ys.max() + 1 - y1)}


def _frames(session, spec):
    """(first, a mid-lap frame, the last frame) — the last one is the frozen run-off when the
    export carries a lead-out, which is the frame F3 is about."""
    times = ev.frame_times(spec.t0, spec.t1, FPS)
    picks = (times[0], times[len(times) // 2], times[-1])
    return [ev.overlay_values_at(session, float(t), spec) for t in picks]


def _frame_pair(painter, out_w, out_h, vals):
    """(real composite, chrome-only composite) for one frame — the two renders every measurement
    below is a difference of. Taken as a PAIR so a 2160p frame is composited twice, not eight
    times: both pills are read out of the same image."""
    return (_composite(painter, out_w, out_h, vals),
            _chrome_only(painter, out_w, out_h, vals))


def _text_beyond_chrome(pair, rect):
    """How much further outside `rect` the TEXT reaches than the pill's own chrome does, on each
    of the four edges. <= 0 everywhere == every run is inside the dark backing that the pill is."""
    real, chrome = _outside(pair[0], rect), _outside(pair[1], rect)
    return {e: real[e] - chrome[e] for e in EDGES}


def _worst_over(session, spec, out_w, out_h, painter):
    """The worst text-past-chrome overflow of EITHER pill over the sampled frames of this export."""
    worst = dict.fromkeys(EDGES, -1e9)
    where = {}
    for vals in _frames(session, spec):
        pair = _frame_pair(painter, out_w, out_h, vals)
        for name, rect in (("readout", painter._readout_rect), ("strip", painter._strip_rect)):
            for edge, v in _text_beyond_chrome(pair, rect).items():
                if v > worst[edge]:
                    worst[edge] = v
                    where[edge] = (name, round(float(vals.t), 4))
    return worst, where


# ------------------------------------------------------------------- F4: the readout's speed set
def _f4_session():
    """99.4 km/h ("99") everywhere inside [0, 10), 142 km/h ("142") from the sample AT t = 10.0 —
    the first sample the CEILING lookup hands the closing frames and the `tt < t1` mask dropped."""
    tt = np.round(np.arange(0.0, 20.001, 0.1), 6)
    tv = np.where(tt < 10.0 - 1e-9, 99.4, 142.0)
    return Stub(tt, tv, 0.0, 10.0, lambda t: 0.0)


def test_the_readout_budget_reads_the_sample_the_render_reads():
    """`_burned_runs` (and so `readout_pill_width`) must budget for the sample the CLOSING FRAMES
    resolve to, not for the last sample strictly inside the window.

    The old `_speed_text_candidates` masked `tt >= t0 & tt < t1`; `Session.index_at_time` is
    `np.searchsorted`, a ceiling. Constructed above, at 1080p/30 fps: the mask said `('99',)`,
    2 frames burned `142`, and the composite carried +9.57 px of ink right of the readout pill's
    right edge — one full digit cell at a 44 px pill (measured +21.34 px of ADVANCE in the sweep's
    own construction)."""
    s = _f4_session()
    out_h, out_w = 1080, 1920
    spec = _spec(s, out_h)
    speeds, _labels, _tails = ev._burned_runs(s, spec, FPS)
    burned = sorted(set(speeds))
    assert burned == ["142", "99"], burned
    n142 = speeds.count("142")
    assert n142 == 2, n142                       # the last 2 frames of the clip, at 30 fps / 10 Hz
    painter = ev.OverlayPainter(s, spec, out_w, out_h, FPS)
    times = ev.frame_times(spec.t0, spec.t1, FPS)
    worst_t = max(times, key=lambda t: ev.overlay_values_at(s, float(t), spec).speed_kmh or 0.0)
    vals = ev.overlay_values_at(s, float(worst_t), spec)
    assert theme.speed_number(vals.speed_kmh, vals.lap_id, None) == "142"
    over = _text_beyond_chrome(_frame_pair(painter, out_w, out_h, vals),
                               painter._readout_rect)
    assert over["right"] <= 0.0, over
    print(f"test_the_readout_budget_reads_the_sample_the_render_reads OK "
          f"(pill {painter._readout_rect.width():.2f} px holds {burned}; "
          f"{n142} frames burn '142'; text reaches {over['right']:+.2f} px past the pill's "
          f"own chrome, was +9.57)")


# ---------------------------------------------------------- F3: the Δ the lead-out freezes on
def _f3_session():
    """A Δ curve reaching 9.9995 s exactly at the flag. `linspace(..., 128, endpoint=False)`'s
    last sample lands at 9.92138 -> `Δ +9.92`; the frozen run burns `Δ +10.00`, one digit wider."""
    tt = np.round(np.arange(0.0, 80.001, 0.1), 6)
    tv = np.full(len(tt), 88.0)
    return Stub(tt, tv, 0.0, 60.0, lambda t: 9.9995 * (max(0.0, min(t, 60.0)) / 60.0))


def test_the_strip_budget_asks_about_the_instant_the_lead_out_freezes_on():
    """`_burned_runs` (and so `strip_pill_width`) must budget for the Δ the RUN-OFF holds.

    `overlay_values_at` clamps the lap clock to `lap_t1 - _LAP_CLOCK_EPS` for every frame past the
    flag, and the old `_peak_abs_delta` sampled `[lap_t0, lap_t1)` with `endpoint=False`, so that
    instant was provably never asked about. Constructed above, 1080p/30 fps with a 10 s run-off:
    the budget fitted `Δ ±9.92`, the clip burned `Δ +10.00` for 300 frames, and the composite
    carried +6.59 px of the final `0` outside the strip pill's dark backing — held there for the
    full 10.00 s, because `_text_at`/`_draw_text` draw an UNCLIPPED QPainterPath.

    The padding is what makes it ten seconds long, not one frame: at `lead_out=0` the same lap
    misses the boundary by a hair."""
    s = _f3_session()
    out_h, out_w = 1080, 1920
    spec = _spec(s, out_h, lead_out=10.0)
    assert spec.lead_out == 10.0, spec.lead_out
    _speeds, _labels, tails = ev._burned_runs(s, spec, FPS)
    frozen = ev.strip_tail(s.delta_at_lap(s._lap, spec.lap_t1 - ev._LAP_CLOCK_EPS), False, None)[0]
    assert frozen == "Δ +10.00", frozen
    assert frozen in tails, sorted(set(tails))[-4:]
    held = tails.count(frozen)
    assert held == 300, held                      # 10.00 s of run-off at 30 fps
    painter = ev.OverlayPainter(s, spec, out_w, out_h, FPS)
    times = ev.frame_times(spec.t0, spec.t1, FPS)
    vals = ev.overlay_values_at(s, float(times[-1]), spec)
    assert vals.lap_finished
    over = _text_beyond_chrome(_frame_pair(painter, out_w, out_h, vals),
                               painter._strip_rect)
    assert over["right"] <= 0.0, over
    print(f"test_the_strip_budget_asks_about_the_instant_the_lead_out_freezes_on OK "
          f"(pill {painter._strip_rect.width():.2f} px holds {frozen!r} for {held / FPS:.2f} s; "
          f"text reaches {over['right']:+.2f} px past the pill's own chrome, was +6.59)")


# ------------------------------------------------------- the sweep: nothing paints outside a pill
def _cases():
    """The content the sweep's own no-overflow check ran, rebuilt as constructible sessions.

    Each is a (name, session, spec kwargs) the two pills have to hold: a 3-digit speed, a
    double-digit Δ in both signs, the `★ BEST` mark, the all-ones values (`1` is the narrow digit,
    so a 1-heavy string is the widest departure a proportional face would make), a small negative
    Δ, and a 10-minute lap whose clock gains a digit."""
    tt = np.round(np.arange(0.0, 200.001, 0.1), 6)
    n = len(tt)
    return [
        ("speed_3_digit", Stub(tt, np.full(n, 199.4), 0.0, 64.238, lambda t: 0.0), {}),
        ("delta_double", Stub(tt, np.full(n, 88.0), 0.0, 64.238, lambda t: -12.34),
         {"lead_in": 5.0, "lead_out": 10.0}),
        ("best_mark", Stub(tt, np.full(n, 88.0), 0.0, 64.238, lambda t: 0.0),
         {"is_best": True, "lead_out": 10.0}),
        ("all_ones", Stub(tt, np.full(n, 111.4), 0.0, 71.111, lambda t: 1.11), {}),
        ("delta_small_neg", Stub(tt, np.full(n, 88.0), 0.0, 64.238, lambda t: -0.31), {}),
        ("ten_minute_lap", Stub(tt, np.full(n, 88.0), 0.0, 190.0, lambda t: 0.0),
         {"lead_out": 5.0}),
    ]


def test_no_run_paints_outside_its_pill():
    """The sweep's own assertion, re-run against the new budget: nothing either painter draws may
    land outside the pill it was fitted to, on ANY of the four edges.

    720 composited samples — 5 output heights x 2 units x 2 palettes x 6 content cases x 3 frames
    x 2 pills. The VERTICAL axis is in here because nothing in the export wave had ever measured
    it: the pills are fitted horizontally, and their height comes from a frame fraction, so a
    descender or a halo escaping the box is a separate failure mode from the one F3/F4 describe.

    Every sample is measured against a CHROME-ONLY control render of the same frame
    (`_chrome_only`), so the pill's own antialiased stroke — which legitimately lands 1.37-1.52 px
    outside its rect, depending on size — is subtracted rather than guessed at. The margins
    printed are px of TEXT past the furthest the pill's own chrome reaches."""
    total, worst = 0, dict.fromkeys(EDGES, -1e9)
    where = {}
    for out_h in (480, 720, 1080, 1440, 2160):
        out_w = int(out_h * 16 / 9)
        for unit in ("kmh", "mph"):
            for palette in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
                for name, s, kw in _cases():
                    spec = _spec(s, out_h, unit=unit, palette=palette, **kw)
                    painter = ev.OverlayPainter(s, spec, out_w, out_h, FPS)
                    w, wh = _worst_over(s, spec, out_w, out_h, painter)
                    total += 3 * 2
                    for edge, v in w.items():
                        if v > worst[edge]:
                            worst[edge] = v
                            where[edge] = (out_h, unit, palette, name, *wh[edge])
    for edge, v in worst.items():
        assert v <= 0.0, f"{edge}: {v:+.2f} px of text past the pill's own chrome at {where[edge]}"
    print(f"test_no_run_paints_outside_its_pill OK ({total} composited samples; worst margins "
          + ", ".join(f"{e} {v:+.2f}" for e, v in sorted(worst.items())) + " px)")


# ---------------------------------------------------------------- F2: the clip's promised length
def test_the_mux_never_lets_the_audio_decide_the_clip_length():
    """`build_encode_cmd` must pad the audio so the VIDEO is the shortest stream.

    `-shortest` ends the output with whichever input runs out first, and at the end of a recording
    that is the audio: D24 chapter 3's video is 1590.005083 s and its audio 1589.994667 s, so a
    run-off clamped to `chapters.total_duration` asks for 10.4 ms of a track that does not exist.
    Measured, real ffmpeg at 480p, before: requested 480 frames, WROTE 480, the file held 479
    (15.989 s against a promised 16.000 s) — and the progress bar still reached 100 %, so nothing
    said so. Reproduced at 483->482, 510->509 and 2100->2099. After: 480/480, 16.000000 s.

    The two flags are a pair — `apad` alone never ends, `-shortest` alone truncates to whatever ran
    out — so this pins BOTH, and pins that they are output options (after the last `-i`)."""
    spec = ev.ExportSpec(src_path="/in.MP4", out_path="/out.mp4", lap_id=1, t0=10.0, t1=26.0)
    cmd = ev.build_encode_cmd(spec, 854, 480, 30.0)
    assert "-shortest" in cmd, cmd
    assert "-af" in cmd and cmd[cmd.index("-af") + 1] == "apad", cmd
    # both must be OUTPUT options: after the last input, before the output path
    last_i = max(i for i, a in enumerate(cmd) if a == "-i")
    assert last_i < cmd.index("-af") < len(cmd) - 1, cmd
    assert last_i < cmd.index("-shortest") < len(cmd) - 1, cmd
    assert cmd[-1] == spec.out_path
    # and it is on BOTH encoder paths (the audio filter is independent of -c:v)
    vt = ev.build_encode_cmd(spec, 854, 480, 30.0, ev.VT_H264)
    assert "apad" in vt and "-shortest" in vt, vt
    print("test_the_mux_never_lets_the_audio_decide_the_clip_length OK "
          "(-af apad -shortest, both encoders)")


if __name__ == "__main__":
    test_the_readout_budget_reads_the_sample_the_render_reads()
    test_the_strip_budget_asks_about_the_instant_the_lead_out_freezes_on()
    test_no_run_paints_outside_its_pill()
    test_the_mux_never_lets_the_audio_decide_the_clip_length()
    print("ALL EXPORT PILL-BUDGET TESTS OK")
