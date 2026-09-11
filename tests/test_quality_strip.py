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
    """(times, rejected, dop) for a list of `(seconds, _Fix)` spans at `rate_hz`."""
    times, rejected, dops, t = [], [], [], 0.0
    for secs, proto in specs:
        for _ in range(int(round(secs * rate_hz))):
            s = _Fix(proto.fix, proto.dop, proto.full_speed)
            times.append(t)
            rejected.append(not _quality_ok(s))
            dops.append(s.dop)
            t += 1.0 / rate_hz
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

    def __init__(self, ok=True, gyro=0.983, path=1.0007, corner=0.946, n=346713, laps=38):
        self.ok, self.loop_ratio_gyro, self.loop_ratio_path = ok, gyro, path
        self.corner_corr, self.n, self.loop_n = corner, n, laps

    @property
    def loop_error_pct(self):
        return abs(self.loop_ratio_gyro - 1.0) * 100.0


def _trust_rows(cross, device="HERO13 Black"):
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
        timing_quality = data_quality.TimingQuality()
        has_gmeter = False

        def gmeter_cross(self):
            return None

        def rotation_cross(self):
            return cross

        def rotation_device(self):
            return device

    view = StatsView.__new__(StatsView)
    view.trust_card = QWidget()   # only set_rows / setToolTip are called on it
    captured = {}
    view.trust_card.set_rows = lambda rows: captured.setdefault("rows", list(rows))
    StatsView._refresh_trust(view, _S())
    return captured["rows"], view.trust_card.toolTip()


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
    test_the_rotation_row_reads_the_closed_lap_ratios_through_the_accessors()
    test_the_rotation_row_is_the_only_cross_check_with_an_exact_target_and_says_so()
    print("ALL OK")


if __name__ == "__main__":
    _main()
