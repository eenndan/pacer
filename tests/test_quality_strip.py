"""The two data-trust surfaces this PR ships: the per-second GPS quality STRIP under the scrub
bar, and the ROTATION cross-check row in the Stats page's DATA TRUST card.

What is actually asserted, and why each one is here rather than being obvious:

  1. THE CLASSIFIER IS THE GATE. `data_quality.build_quality_timeline` grades a second POOR when
     the loader would have thrown its fixes away and GOOD when it would have kept them — it takes
     the gate's own `_quality_ok` verdict as an input rather than re-deriving one, and this file
     pins that the two really do agree on a synthetic trace built to straddle every boundary.

  2. WORST-WINS SURVIVES DOWNSAMPLING. The strip's whole claim is that you can SEE where a
     recording went bad, and the way that claim dies is a 500 px bar averaging a one-second
     dropout into the five clean seconds beside it. A single POOR second inside an otherwise
     perfect 3,000-second recording must still paint a column.

  3. THE GPS5 STATE IS VISIBLY DIFFERENT, not a quieter green. A camera with no per-sample DOP
     grades UNREPORTED and gets a neutral, and NOTHING in such a recording is ever GOOD.

  4. THE COLOURS CLEAR WCAG AA AND THE DEUTERANOPIA RAMP, in BOTH palettes, measured with the
     same CIE76/Machado machinery as tests/test_contrast.py rather than asserted.

  5. THE ROTATION ROW READS THROUGH THE ACCESSORS. It prints `RotationCheck`'s own numbers — the
     closed-lap ratios, which are the only statistic on that card with an exact target — and it
     does not hard-code any of them, so a channel that changes moves the card with it.

Offscreen Qt (the strip is a real widget, and `runs()` is read off its real slider geometry); no
pacer, no telemetry file. Run: python tests/test_quality_strip.py
"""
import os
import sys

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)
sys.path.insert(0, os.path.join(_REPO, "tests"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

_APP = QApplication.instance() or QApplication([])

import test_contrast as cc  # noqa: E402  (the CIE76 / Machado deuteranopia maths, one copy)

from studio import data_quality, theme, video_view  # noqa: E402
from studio._signal import MAX_DOP, MIN_FIX, _quality_ok  # noqa: E402


class _Fix:
    """The three fields `_signal._quality_ok` actually reads off a GPSSample."""

    def __init__(self, fix=3, dop=1.5, speed=20.0):
        self.fix, self.dop, self.full_speed = fix, dop, speed
        self.lat, self.lon = 51.0, -1.0


def _trace(specs, rate_hz=10.0):
    """(times, rejected, dop) for a list of `(seconds, _Fix)` spans at `rate_hz`.

    The time of sample k is `k / rate_hz`, computed from the RUNNING INDEX rather than by adding
    a step each turn. Accumulating 1/10 a hundred times lands at 9.999999999999998, so the fix
    meant to open second 10 falls in cell 9 instead and the span boundaries in these fixtures
    land one sample early — which looks exactly like a classifier bug and is not one."""
    times, rejected, dops, k = [], [], [], 0
    for secs, proto in specs:
        for _ in range(int(round(secs * rate_hz))):
            s = _Fix(proto.fix, proto.dop, proto.full_speed)
            times.append(k / rate_hz)
            rejected.append(not _quality_ok(s))
            dops.append(s.dop)
            k += 1
    return times, rejected, dops


# =========================================================== 1. the classifier IS the gate
def test_a_cell_is_poor_exactly_when_the_loader_would_throw_its_fixes_away():
    """The strip's boundaries are `_signal.MIN_FIX` / `MAX_DOP`, read from the gate, not copied
    beside it. A trace that straddles every one of them must grade the way the gate decides:

      * no 3D lock  -> POOR  (the gate rejects it)
      * DOP above MAX_DOP -> POOR  (likewise)
      * DOP in the GNSS moderate band (5, MAX_DOP] -> MODERATE — KEPT by the gate, and still
        worth drawing differently, because it is the band a failing receiver passes through
      * DOP inside the good band -> GOOD

    The MODERATE case is the one a "just show what the gate rejected" strip would miss entirely,
    and it is the early warning: on the owner's 0060 recording it is the ONLY concern there is."""
    times, rejected, dop = _trace([
        (5, _Fix(fix=0, dop=99.0)),                 # acquiring: no lock at all
        (5, _Fix(fix=3, dop=MAX_DOP + 2.0)),        # locked, but geometry past the gate's bound
        (5, _Fix(fix=3, dop=data_quality.DOP_GOOD_MAX + 1.0)),   # kept, degrading
        (5, _Fix(fix=3, dop=1.2)),                  # clean
    ])
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=20.0)
    assert len(tl) == 20, len(tl)
    got = [int(c) for c in tl.cls]
    assert got[:5] == [data_quality.POOR] * 5, got[:5]
    assert got[5:10] == [data_quality.POOR] * 5, got[5:10]
    assert got[10:15] == [data_quality.MODERATE] * 5, got[10:15]
    assert got[15:] == [data_quality.GOOD] * 5, got[15:]
    # …and the classifier agrees with the gate fix-for-fix, not merely in aggregate.
    assert sum(rejected) == int(tl.dropped.sum()) == 100, tl.dropped.sum()
    assert tl.reports_quality
    # The sentinel bound is the gate's own, so a change there cannot leave the strip behind.
    assert _quality_ok(_Fix(fix=MIN_FIX, dop=MAX_DOP)) is True
    assert _quality_ok(_Fix(fix=MIN_FIX, dop=MAX_DOP + 0.1)) is False
    print("test_a_cell_is_poor_exactly_when_the_loader_would_throw_its_fixes_away OK "
          f"({tl.summary()})")


def test_a_second_with_some_fixes_rejected_is_not_reported_as_clean():
    """A cell is only GOOD when the gate kept EVERYTHING in it. Half a second of rejected fixes is
    a real hole in the trace, and a classifier keyed on "the surviving fixes look fine" would call
    it good — the survivors do look fine, by construction."""
    times, rejected, dop = [], [], []
    for i in range(10):                     # one second, alternating good / no-lock
        s = _Fix(fix=3 if i % 2 else 0, dop=1.2 if i % 2 else 60.0)
        times.append(i / 10.0)
        rejected.append(not _quality_ok(s))
        dop.append(s.dop)
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=1.0)
    assert int(tl.cls[0]) == data_quality.MODERATE, int(tl.cls[0])
    assert int(tl.dropped[0]) == 5 and int(tl.n[0]) == 10
    assert tl.dop[0] == 1.2, tl.dop[0]        # the worst DOP among the fixes the gate KEPT
    print("test_a_second_with_some_fixes_rejected_is_not_reported_as_clean OK")


def test_a_gap_in_the_stream_is_a_hole_in_the_bar_not_a_verdict():
    """Seconds with no fix at all are NO_FIX, and NO_FIX paints nothing — a gap reads as "nothing
    was recorded here" on sight, where a colour would compete with the two states that are
    verdicts. The span is the VIDEO's, so a receiver that stops four minutes early leaves four
    minutes of empty bar instead of the timeline quietly rescaling to its last fix."""
    times, rejected, dop = _trace([(5, _Fix())])
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=20.0)
    assert len(tl) == 20 and abs(tl.span_s - 20.0) < 1e-9
    assert [int(c) for c in tl.cls[5:]] == [data_quality.NO_FIX] * 15
    assert tl.worst == data_quality.NO_FIX
    assert video_view._QualityStrip.class_colour(data_quality.NO_FIX) is None
    print("test_a_gap_in_the_stream_is_a_hole_in_the_bar_not_a_verdict OK")


# =============================================== 2. worst-wins, all the way down to the pixel
def _strip(timeline, *, width=520, lo_ms=0, hi_ms=None):
    """A real `_QualityStrip` over a real `_LapRulerSlider`, laid out at `width`."""
    slider = video_view._LapRulerSlider(Qt.Horizontal)
    slider.setObjectName("ScrubBar")
    slider.setRange(lo_ms, int(timeline.span_s * 1000) if hi_ms is None else hi_ms)
    slider.resize(width, theme.HIT_MIN + theme.SPACE_XXS)
    strip = video_view._QualityStrip(slider)
    strip.resize(width, video_view._QualityStrip.INK_H)
    strip.set_timeline(timeline)
    return strip


def test_one_bad_second_in_fifty_minutes_still_paints_a_column():
    """THE CLAIM, AND THE WAY IT DIES. A 3,000-second recording over ~500 px is ~6 cells a pixel.
    Average them and a one-second dropout is 1/6th of a shade — invisible, and the strip is then
    a surface that VOUCHES for a recording with a hole in it. Folded worst-first it is a column.

    Asserted on the real widget's real `runs()`, off real slider geometry, not on the arrays."""
    times, rejected, dop = _trace([(1500, _Fix()), (1, _Fix(fix=0, dop=99.0)), (1499, _Fix())])
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=3000.0)
    assert tl.counts()[data_quality.POOR] == 1, tl.counts()
    strip = _strip(tl)
    runs = strip.runs()
    poor = [r for r in runs if r[2] == data_quality.POOR]
    assert poor, f"the one bad second vanished into {len(runs)} runs over 3000 s"
    x, w, _ = poor[0]
    assert w >= 1, poor
    # …and it is in the RIGHT PLACE: the middle of the bar, not merely somewhere on it.
    x0, span, _h = strip._slider._travel()
    frac = (x - x0) / span
    assert 0.45 < frac < 0.55, f"the bad second painted at {frac:.3f} of the bar, not near 0.5"
    # The hover over that column names the class and the numbers behind it.
    said = strip.describe_at(x)
    assert data_quality.QUALITY_LABEL[data_quality.POOR] in said, said
    assert "rejected" in said, said
    print(f"test_one_bad_second_in_fifty_minutes_still_paints_a_column OK "
          f"({len(runs)} runs, bad column {w}px at {frac:.3f})")


def test_the_strip_shares_the_sliders_travel_and_follows_its_range():
    """Two invariants that a strip measuring its OWN width would break.

    The cells must sit under the instants they grade, so every x comes from the slider's handle
    geometry — the bar starts at the handle's centre at minimum, not at pixel 0. And entering
    compare re-ranges the scrub bar from the whole session to ONE lap; the strip must re-scale
    with it rather than keep drawing the session."""
    times, rejected, dop = _trace([(60, _Fix()), (60, _Fix(fix=0, dop=99.0))])
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=120.0)
    strip = _strip(tl)
    x0, span, _handle = strip._slider._travel()
    runs = strip.runs()
    assert runs[0][0] == x0, f"the band must start where the handle does ({runs[0][0]} != {x0})"
    assert runs[-1][0] + runs[-1][1] <= x0 + span + 1, runs[-1]
    assert x0 > 0, "a slider handle has width; a strip starting at 0 is misaligned by half of it"
    assert {r[2] for r in runs} == {data_quality.GOOD, data_quality.POOR}, runs

    # Compare mode: the bar now spans the SECOND minute only, which is entirely POOR.
    strip._slider.setRange(60_000, 120_000)
    assert {r[2] for r in strip.runs()} == {data_quality.POOR}, strip.runs()
    # …and the first minute only, which is entirely GOOD.
    strip._slider.setRange(0, 60_000)
    assert {r[2] for r in strip.runs()} == {data_quality.GOOD}, strip.runs()
    print("test_the_strip_shares_the_sliders_travel_and_follows_its_range OK")


def test_a_lap_inherits_its_worst_cell():
    """`worst_between` is the one implementation of "a lap inherits its worst cell" — the same
    fold the pixel columns use, so a lap's badge and the bar under it can never disagree."""
    times, rejected, dop = _trace([(30, _Fix()), (1, _Fix(fix=0, dop=99.0)), (29, _Fix())])
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=60.0)
    assert tl.worst_between(0.0, 60.0) == data_quality.POOR
    assert tl.worst_between(0.0, 29.0) == data_quality.GOOD
    assert tl.worst_between(31.0, 60.0) == data_quality.GOOD
    # A zero-width query still answers — a hover is an instant, not a span.
    assert tl.worst_between(30.5, 30.5) == data_quality.POOR
    st = tl.stats_between(0.0, 60.0)
    assert st["n"] == 600 and st["dropped"] == 10, st
    print("test_a_lap_inherits_its_worst_cell OK")


# ================================================== 3. the GPS5 state is visibly a DIFFERENT one
def test_a_camera_that_reports_no_quality_is_never_painted_green():
    """The one failure mode this surface must not have. A GPS5-era stream carries no fix type and
    no DOP — the sentinels are -1 — so there is nothing to grade, and the honest answer is a
    neutral band that says so, not the confident colour of a recording that was actually checked.

    (The bundled hero6.mp4 clip is exactly such a recording; tests/test_load_pipeline.py asserts
    the same thing end to end through the real loader.)"""
    times, rejected, dop = _trace([(10, _Fix(fix=-1, dop=-1.0))])
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=10.0)
    assert not tl.reports_quality
    assert set(tl.counts()) == {data_quality.UNREPORTED}, tl.counts()
    assert data_quality.GOOD not in tl.counts()
    assert tl.concern_seconds() == 0.0, "not graded is not the same as a concern"
    strip = _strip(tl)
    neutral = video_view._QualityStrip.class_colour(data_quality.UNREPORTED)
    for verdict in (data_quality.GOOD, data_quality.MODERATE, data_quality.POOR):
        assert neutral != video_view._QualityStrip.class_colour(verdict), verdict
    said = strip.describe_at(strip._slider._travel()[0] + 10)
    assert "no per-sample GPS quality" in said, said
    print("test_a_camera_that_reports_no_quality_is_never_painted_green OK")


# ============================================== 4. the colours, measured rather than asserted
def test_every_strip_colour_clears_wcag_and_the_deuteranopia_ramp_in_both_palettes():
    """Three verdict colours on a 4 px band, against the bar they sit on — held to WCAG's 3:1 for
    a non-text UI component (1.4.11) and, between each other, to the CIE76 JND under a
    severity-1.0 deuteranopia simulation, in BOTH palettes.

    Not "they look different": the standard palette's own map ramp necks to 1.44 dE under exactly
    this simulation (tests/test_contrast.py), which is why that file exists and why a new
    three-colour semantic surface does not get to skip the measurement."""
    worst_pair, worst_bg = 1e9, 1e9
    try:
        for pal in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
            theme.set_palette(pal)
            cols = {c: video_view._QualityStrip.class_colour(c)
                    for c in (data_quality.GOOD, data_quality.MODERATE, data_quality.POOR,
                              data_quality.UNREPORTED)}
            assert None not in cols.values(), cols
            for c, hexv in cols.items():
                for bg in (theme.C.surface, theme.C.canvas, theme.C.border):
                    ratio = cc.contrast(hexv, bg)
                    worst_bg = min(worst_bg, ratio)
                    assert ratio >= 3.0, (
                        f"{pal}: {data_quality.QUALITY_LABEL[c]} {hexv} is {ratio:.2f}:1 on {bg} "
                        "— below WCAG 1.4.11's 3:1 for a non-text component")
            keys = list(cols)
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    d = cc._dE(cc._deut(cc._hx(cols[a])), cc._deut(cc._hx(cols[b])))
                    worst_pair = min(worst_pair, d)
                    assert d > cc.JND, (
                        f"{pal}: {data_quality.QUALITY_LABEL[a]} vs "
                        f"{data_quality.QUALITY_LABEL[b]} is {d:.2f} dE under deuteranopia "
                        f"(JND {cc.JND}) — two classes that read as one")
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print(f"test_every_strip_colour_clears_wcag_and_the_deuteranopia_ramp_in_both_palettes OK "
          f"(worst pair {worst_pair:.1f} dE, worst contrast {worst_bg:.2f}:1)")


def test_the_strip_follows_the_colourblind_palette():
    """Every hue is an ACCESSOR call resolved at paint, so flipping the palette moves the band —
    the U10-01 shape, applied to a surface that did not exist when that guard was written."""
    try:
        theme.set_palette(theme.PALETTE_STANDARD)
        std = [video_view._QualityStrip.class_colour(c)
               for c in (data_quality.GOOD, data_quality.MODERATE, data_quality.POOR)]
        theme.set_palette(theme.PALETTE_COLORBLIND)
        cb = [video_view._QualityStrip.class_colour(c)
              for c in (data_quality.GOOD, data_quality.MODERATE, data_quality.POOR)]
        assert all(a != b for a, b in zip(std, cb, strict=True)), (std, cb)
    finally:
        theme.set_palette(theme.PALETTE_STANDARD)
    print("test_the_strip_follows_the_colourblind_palette OK")


# ===================================================== 5. the DATA TRUST rotation row
class _Check:
    """A stand-in with `rotation.RotationCheck`'s read surface (the card only reads accessors)."""

    def __init__(self, ok=True, gyro=0.983, path=1.0007, corner=0.946, n=346713, laps=38,
                 lag_clause="", lag_corr=0.0, lag_corr_at_zero=0.0):
        self.ok, self.loop_ratio_gyro, self.loop_ratio_path = ok, gyro, path
        self.corner_corr, self.n, self.loop_n = corner, n, laps
        # The clock-offset sentence is EMPTY by default: a recording whose offset could not be
        # measured must produce a row that simply does not mention one (see the offset test).
        self.lag_clause, self.lag_corr = lag_clause, lag_corr
        self.lag_corr_at_zero = lag_corr_at_zero

    @property
    def loop_error_pct(self):
        return abs(self.loop_ratio_gyro - 1.0) * 100.0


def _trust_rows(cross, device="HERO13 Black", timeline=None, lap_cls=None, quality=None,
                applied_lag=None, clock=None):
    from studio.stats_panel import StatsView

    class _S:
        def valid_lap_ids(self):
            return [0, 1]

        def excluded_lap_ids(self):
            return []

        def dropout_lap_ids(self):
            return set()

        timing_verified = True
        track_name = "Test Circuit"
        timing_quality = quality if quality is not None else data_quality.TimingQuality()
        has_gmeter = False
        quality_timeline = timeline if timeline is not None else data_quality.empty_timeline()

        def lap_quality(self, lap_id):
            return None if lap_cls is None else lap_cls.get(lap_id)

        def gmeter_cross(self):
            return None

        def rotation_cross(self):
            return cross

        def rotation_device(self):
            return device

        # What the session did with the measured clock offset — None = nothing (no gyro, or a
        # value the clock refused). The card states the measurement and the ACTION separately.
        gps_lag_applied_s = applied_lag
        # …and the picture<->telemetry map itself, which is what the ACTION is a property of. A
        # stand-in that models no map at all gets no Video sync row (see that row's own test):
        # "this object knows nothing about a clock" must never render as "this recording's clock
        # could not be fitted".
        media_clock = clock

    view = StatsView.__new__(StatsView)
    view.trust_card = QWidget()   # only set_rows / setToolTip are called on it
    captured = {}
    view.trust_card.set_rows = lambda rows: captured.setdefault("rows", list(rows))
    StatsView._refresh_trust(view, _S())
    return captured["rows"], view.trust_card.toolTip()


def test_a_recording_with_no_gps_is_not_sold_as_the_true_clock():
    """The card's Timing row, on a recording that carries no GPS at all.

    Driven through the REAL `_refresh_trust`, because the defect was in the row it builds: the
    clock label was a two-way choice — media-clock fallback, else "GPS9 true clock" — so a verdict
    that was NEITHER fell through to the flattering branch. Measured end to end on the bundled
    `karma.mp4` (0 GPS fixes) before the fix, the card printed:

        Timing: GPS9 true clock · 0% of moving fixes rejected

    Both halves are wrong in the same direction, which is why this asserts the row is a CAVEAT as
    well as re-worded: it floats to the top of the card with the other trust-breaking facts."""
    no_gps = data_quality.TimingQuality(clock=data_quality.NO_GPS_TRACE)
    rows, _tip = _trust_rows(None, quality=no_gps)
    row = next((r for r in rows if r[0] == "Timing"), None)
    assert row is not None, [r[0] for r in rows]
    term, value, caveat = row
    assert "GPS9" not in value, f"no fix arrived; the true clock cannot be claimed: {value!r}"
    assert "no gps" in value.lower(), value
    assert caveat is True, "a recording that cannot be timed is a trust-breaking fact"
    # The rejected-fix percentage is meaningless with no fixes to reject — it must not be printed
    # as a reassuring 0 %.
    assert "0% of moving fixes rejected" not in value, value

    # THE TWO REAL CLOCKS ARE UNTOUCHED — this must not become a blanket re-word.
    good = next(r for r in _trust_rows(None)[0] if r[0] == "Timing")
    assert "GPS9 true clock" in good[1] and good[2] is False, good
    media = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
    est = next(r for r in _trust_rows(None, quality=media)[0] if r[0] == "Timing")
    assert "video clock (estimated)" in est[1], est
    print("test_a_recording_with_no_gps_is_not_sold_as_the_true_clock OK")


def test_the_rotation_row_reads_the_closed_lap_ratios_through_the_accessors():
    """The row is the card's THIRD cross-check and the only one with an exact target: a lap is a
    closed loop, so both channels' integrated yaw must be 1.000x2pi. It prints what
    `RotationCheck` reads and hard-codes nothing — feed it different numbers and the row moves."""
    rows, tip = _trust_rows(_Check(gyro=0.983, path=1.0007, corner=0.946, laps=38))
    row = next((r for r in rows if r[0] == "Rotation cross-check"), None)
    assert row is not None, [r[0] for r in rows]
    term, value, caveat = row
    assert caveat is False, "an agreeing cross-check is not a caveat"
    for token in ("0.983", "1.001", "38", "+0.95", "exact"):
        assert token in value, f"{token!r} missing from {value!r}"
    # Both channels against ONE stated target — no verdict about the gap between them.
    assert "1.000" in value, value
    assert "HERO13 Black" in tip and "2π" in tip, tip

    # A DIFFERENT measurement must produce a different row (nothing is baked in) …
    rows2, _ = _trust_rows(_Check(gyro=0.491, path=1.0007, ok=False))
    row2 = next(r for r in rows2 if r[0] == "Rotation cross-check")
    assert "0.491" in row2[1] and "DISAGREES" in row2[1], row2
    # … and a failing verdict is marked as a caveat, which is what puts it at the TOP of the card.
    assert row2[2] is True, row2

    # No channel at all: no row, and nothing else on the card changes.
    rows3, _ = _trust_rows(None)
    assert not [r for r in rows3 if r[0] == "Rotation cross-check"], rows3
    print("test_the_rotation_row_reads_the_closed_lap_ratios_through_the_accessors OK")


def test_the_rotation_row_is_the_only_cross_check_with_an_exact_target_and_says_so():
    """The reason this row exists beside the IMU↔GPS one, in the copy itself: that row's r and
    gain compare two ESTIMATES, so they describe agreement. This one names a target — and it must
    keep naming it, or it reads as a third correlation."""
    rows, tip = _trust_rows(_Check())
    value = next(r[1] for r in rows if r[0] == "Rotation cross-check")
    assert "exact" in value.lower(), value
    assert "closed lap" in value.lower(), value
    assert "ground truth" in tip.lower(), tip
    # It must not editorialise about the difference between the two channels: the card prints two
    # ratios against one target and leaves the comparison to the reader, because that difference
    # has already moved once (6.5-10 % of a lap -> ~0.1 %) and any sentence about it would have
    # gone stale with it.
    for banned in ("over-read", "over-reads", "inflated", "wrong", "v*kappa", "v·κ"):
        assert banned not in value.lower(), f"{banned!r} is a verdict about the OTHER channel"
    print("test_the_rotation_row_is_the_only_cross_check_with_an_exact_target_and_says_so OK")


def test_the_rotation_row_states_the_clock_offset_the_correlation_is_measured_with():
    """The two channels are timed on different clocks — the gyroscope on the camera's media clock,
    the GPS trace on its receiver's — and on the owner's recordings the GPS timestamps land ~0.46 s
    later. The row prints the correlation between the two channels WITH that offset left in, so it
    has to print the offset too, or the r reads as how well they agree.

    Built on a REAL `RotationCheck` rather than the stand-in above, because the sentence is that
    dataclass's own property: a test that retyped it here could not catch it going stale."""
    from dataclasses import replace

    from studio import rotation

    real = rotation.RotationCheck(
        n=26562, corr=0.87, gain=0.89, corner_n=9000, corner_corr=0.946, corner_gain=0.87,
        straight_n=8000, straight_rms_gyro=0.24, straight_rms_path=0.05, straight_mean_gyro=0.026,
        loop_n=38, loop_ratio_gyro=0.983, loop_ratio_path=1.001, ok=True,
        gps_lag_s=0.483, lag_corr=0.917, lag_corr_at_zero=0.854)
    rows, tip = _trust_rows(real, applied_lag=0.483)
    value = next(r[1] for r in rows if r[0] == "Rotation cross-check")
    assert "0.48 s" in value, value
    assert "behind" in value, value
    # It must say what the figures BESIDE it are measured with — an offset stated next to a
    # correlation that silently carries it reads as agreement the channels never had.
    assert "left in" in value, value
    # The tooltip carries what the row cannot: the correlation with and without the offset.
    assert "+0.92" in tip and "+0.85" in tip, tip
    assert "media clock" in tip and "lap times" in tip.lower(), tip

    # …and what the APP DID with the offset is a different fact, from a different place
    # (Session._install_gps_lag). It used to be a clause in THIS tooltip, which meant it was
    # stated only where there is a gyro to measure an offset with — never on the ten bundled
    # samples, and never on a recording whose measurement was refused, which is exactly the
    # recording whose overlay is uncorrected. It is now the Video sync ROW, and this tooltip
    # points at it rather than restating it (one fact, one wording).
    from studio.media_clock import MediaClock
    from studio.stats_panel import VIDEO_SYNC_TERM

    assert "Video sync row" in tip, tip
    assert "overlay IS corrected" not in tip, tip
    assert "0.48 s" in tip, tip                       # the MEASUREMENT stays here
    fit = MediaClock(rate=1.0 + 26.73e-6, offset=0.0256)
    rows_on, _ = _trust_rows(real, applied_lag=0.483, clock=fit.with_gps_lag(0.483))
    sync = next(r for r in rows_on if r[0] == VIDEO_SYNC_TERM)
    assert sync[1].startswith("corrected") and "0.48 s" in sync[1], sync
    assert sync[2] is False, sync
    # A recording whose offset was measured but NOT applied says so instead — the two must not be
    # collapsed into one sentence that assumes the correction landed.
    rows_off, tip3 = _trust_rows(real, applied_lag=None, clock=fit)
    sync_off = next(r for r in rows_off if r[0] == VIDEO_SYNC_TERM)
    assert sync_off[2] is True and "could not be measured" in sync_off[1], sync_off
    assert "corrected" not in sync_off[1], sync_off
    assert "overlay IS corrected" not in tip3, tip3

    # NOT MEASURED IS NOT ZERO. A recording whose offset could not be measured says nothing about
    # one — it must not print "0.00 s behind", which is a claim nobody made.
    rows2, tip2 = _trust_rows(replace(real, gps_lag_s=None))
    v2 = next(r[1] for r in rows2 if r[0] == "Rotation cross-check")
    assert "behind" not in v2 and "0.00 s" not in v2, v2
    assert "0.983" in v2, "the rest of the row is unchanged"
    assert "not on the same clock" not in tip2, tip2
    print("test_the_rotation_row_states_the_clock_offset_the_correlation_is_measured_with OK")


def test_the_card_and_the_bar_are_the_same_fact_and_the_card_says_which_laps():
    """The strip row exists because the percentage row above it comes out INVERTED on the owner's
    own two recordings, and both were measured through this code:

      * 0060 rejects NOT ONE fix — the percentage row reads 0 % — and yet 17 of its 38 clean laps
        contain a second whose DOP left the GNSS good band;
      * 0062 rejects 1 %, and every one of those rejections is inside the 48 seconds before the
        kart moves, so NOT ONE of its 65 laps inherits anything but good.

    So the row names the laps, and it is a CAVEAT only when a lap has a real hole in it (POOR or
    no fix at all). A merely degrading second is stated, not alarmed about — it is still a second
    the loader kept."""
    times, rejected, dop = _trace([(30, _Fix(dop=data_quality.DOP_GOOD_MAX + 1.0)),
                                   (30, _Fix())])
    tl = data_quality.build_quality_timeline(times, rejected, dop, span_s=60.0)

    # 0060's shape: a degraded second inside a lap, nothing rejected. Stated, not flagged.
    rows, tip = _trust_rows(None, timeline=tl,
                            lap_cls={0: data_quality.MODERATE, 1: data_quality.GOOD})
    row = next(r for r in rows if r[0] == "GPS quality over time")
    assert "30 s moderate" in row[1], row[1]
    assert "1 of 2 laps" in row[1], row[1]
    assert "scrubber" in row[1], row[1]
    assert row[2] is False, "a degrading second the loader KEPT is not a caveat"
    assert "worst second" in tip.lower(), tip

    # A real hole in a lap IS a caveat, which is what floats it to the top of the card.
    rows2, _ = _trust_rows(None, timeline=tl,
                           lap_cls={0: data_quality.POOR, 1: data_quality.GOOD})
    assert next(r for r in rows2 if r[0] == "GPS quality over time")[2] is True, rows2

    # 0062's shape: everything bad is outside every lap. Reported, and no lap clause at all.
    rows3, _ = _trust_rows(None, timeline=tl,
                           lap_cls={0: data_quality.GOOD, 1: data_quality.GOOD})
    row3 = next(r for r in rows3 if r[0] == "GPS quality over time")
    assert "laps contain" not in row3[1], row3[1]
    assert row3[2] is False, row3

    # No recording loaded: no row (an empty timeline is not a verdict of "clean").
    rows4, _ = _trust_rows(None)
    assert not [r for r in rows4 if r[0] == "GPS quality over time"], rows4
    print("test_the_card_and_the_bar_are_the_same_fact_and_the_card_says_which_laps OK")


# ===================================================== 6. the DATA TRUST video-sync row
def _sync_session(clock, quality, applied, total_duration=None):
    """The read surface `video_sync_row` touches, and nothing else — accessors only."""
    from types import SimpleNamespace
    sess = SimpleNamespace(media_clock=clock, timing_quality=quality, gps_lag_applied_s=applied)
    if total_duration is not None:
        sess.chapters = SimpleNamespace(total_duration=total_duration)
    return sess


def test_the_card_says_whether_what_is_drawn_over_a_frame_is_that_frames_own():
    """THE ONE SYNC FACT A USER CAN ACT ON, and the card could not state it.

    The app crosses ONE seam between the picture and the telemetry (`Session.media_time`), and two
    corrections ride on it: the two clocks' ~27 ppm rate difference (#266) and the GPS timestamps'
    own measured lag (#301). Whether the second one LANDED is a per-recording verdict —
    `Session.gps_lag_applied_s` is None for a camera with no gyro, for a gyro that never tracks
    the path, and for a measurement past `media_clock.MAX_GPS_LAG_S`.

    Measured before this row existed, on the REAL StudioWindow over `~/Desktop/D24/GX020060.MP4`
    + `GX030060.MP4` with `rotation.measure_lag` forced to its own refusing branch (the #283
    idiom: the real gate, driven to the branch the owner's files never reach):

        [session] quality=gps9_trueclock rate=+26.73 ppm gps_lag=+0.0000 applied=None
        Timing: GPS9 true clock · 0% of moving fixes rejected
        Rotation cross-check: agrees · … · r=+0.95 between them through the corners
        (tooltip: no clock-offset paragraph at all)

    Every GPS-derived overlay on that recording trails the picture by the receiver's fix latency —
    ~0.46 s, 14 frames at 30 fps — and the whole window said nothing: the Timing row reads as the
    app's best clock, and the rotation row simply drops its clause because `lag_clause` is empty
    when nothing was measured. The disclosure that DID exist lived in that same gyro-dependent
    tooltip, so it was absent on all ten bundled samples (none of which has a measurable gyro
    offset) and on exactly the recordings where the correction had failed.

    So the row states the ACTION, on the surface, for every recording that has a picture to be
    out of sync with — and it is a CAVEAT when the correction did not land."""
    from studio import data_quality as dq
    from studio.media_clock import MediaClock
    from studio.stats_panel import VIDEO_SYNC_TERM, video_sync_row

    gps9 = dq.TimingQuality()
    gps5 = dq.TimingQuality(clock=dq.MEDIA_CLOCK_FALLBACK)
    no_gps = dq.TimingQuality(clock=dq.NO_GPS_TRACE)
    # D24 0060's own numbers, measured through the real load path.
    fitted = MediaClock(rate=1.0 + 26.73e-6, offset=0.0256)
    corrected = fitted.with_gps_lag(0.47637)

    # 1. BOTH D24 RECORDINGS: the map is fitted and the measured lag is installed.
    term, value, caveat = video_sync_row(
        _sync_session(corrected, gps9, 0.47637, total_duration=2823.6))
    assert term == VIDEO_SYNC_TERM
    assert caveat is False, value
    assert "0.48 s" in value, value                      # the lag, as the rotation row prints it
    assert "26.7 ppm" in value, value                    # …and the rate difference beside it
    assert "0.08 s" in value, value                      # 26.73 ppm across 2823.6 s of recording
    assert "in the app and in an exported clip alike" in value, value

    # The seconds clause is the only part that needs a duration: a recording that cannot say how
    # long it is still gets the row, minus that figure — never a fabricated one.
    _t, short, _c = video_sync_row(_sync_session(corrected, gps9, 0.47637))
    assert "26.7 ppm" in short and "0.08 s" not in short, short

    # 2. THE MEASUREMENT REFUSED (the forced case above): fitted map, no lag installed.
    term, value, caveat = video_sync_row(_sync_session(fitted, gps9, None, total_duration=2823.6))
    assert caveat is True, value
    assert "could not be measured" in value, value
    assert "trail the picture" in value, value
    # It must not claim the correction the session did not make …
    assert "0.48 s" not in value and "corrected" not in value.split("—")[0], value
    # … and it must not frighten anyone about the lap times, which are differences on one clock.
    assert "Lap times" in value, value

    # 3. NO CONVERSION AT ALL — every bundled GPS5 sample (measured: 8 of the 10 load like this).
    term, value, caveat = video_sync_row(_sync_session(MediaClock(), gps5, None))
    assert caveat is False, value
    assert "one clock" in value, value
    assert "ppm" not in value, "there is no rate difference to state on a one-clock recording"

    # 4. A GPS9 RECORDING WHOSE MAP WAS REFUSED (too little trace, or a guard trip in
    #    `media_clock.fit`): identity, but NOT because the two clocks are the same one.
    term, value, caveat = video_sync_row(_sync_session(MediaClock(), gps9, None))
    assert caveat is True, value
    assert "could not be fitted" in value, value

    # 5. NO GPS AT ALL (the bundled `karma.mp4`): there is no trace to place on the picture, and
    #    the Timing row above already says nothing here can be lap-timed. No row.
    assert video_sync_row(_sync_session(MediaClock(), no_gps, None)) is None

    # 6. A STAND-IN THAT MODELS NO CLOCK is not a recording whose clock failed — it is a test
    #    double, and every other suite in this repo builds one. No row, and the real card agrees.
    from types import SimpleNamespace
    assert video_sync_row(SimpleNamespace()) is None
    assert video_sync_row(SimpleNamespace(media_clock=None, timing_quality=gps9)) is None
    rows, _tip = _trust_rows(None)
    assert not [r for r in rows if r[0] == VIDEO_SYNC_TERM], rows

    # …and it DOES reach the real card when the session carries a map (driven through the real
    # `_refresh_trust`, which is where the row has to appear).
    rows, tip = _trust_rows(None, quality=gps9, clock=corrected, applied_lag=0.47637)
    row = next((r for r in rows if r[0] == VIDEO_SYNC_TERM), None)
    assert row is not None, [r[0] for r in rows]
    assert "0.48 s" in row[1], row
    # The row sits with the clock fact it belongs beside, not at the foot of the card.
    terms = [r[0] for r in rows]
    assert terms.index(VIDEO_SYNC_TERM) == terms.index("Timing") + 1, terms
    # The floor that remains after both corrections is stated once, in the tooltip.
    assert "±0.05 s" in tip, tip
    print("test_the_card_says_whether_what_is_drawn_over_a_frame_is_that_frames_own OK")


def _main():
    test_a_cell_is_poor_exactly_when_the_loader_would_throw_its_fixes_away()
    test_a_second_with_some_fixes_rejected_is_not_reported_as_clean()
    test_a_gap_in_the_stream_is_a_hole_in_the_bar_not_a_verdict()
    test_one_bad_second_in_fifty_minutes_still_paints_a_column()
    test_the_strip_shares_the_sliders_travel_and_follows_its_range()
    test_a_lap_inherits_its_worst_cell()
    test_a_camera_that_reports_no_quality_is_never_painted_green()
    test_every_strip_colour_clears_wcag_and_the_deuteranopia_ramp_in_both_palettes()
    test_the_strip_follows_the_colourblind_palette()
    test_a_recording_with_no_gps_is_not_sold_as_the_true_clock()
    test_the_rotation_row_reads_the_closed_lap_ratios_through_the_accessors()
    test_the_rotation_row_is_the_only_cross_check_with_an_exact_target_and_says_so()
    test_the_rotation_row_states_the_clock_offset_the_correlation_is_measured_with()
    test_the_card_and_the_bar_are_the_same_fact_and_the_card_says_which_laps()
    test_the_card_says_whether_what_is_drawn_over_a_frame_is_that_frames_own()
    print("ALL OK")


if __name__ == "__main__":
    _main()
