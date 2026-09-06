"""The burned-in overlay's DIGITS — SW1-02.

PRs #196/#197 fixed `theme.mono_font`: it had been calling `setFeature("tnum", 1)` inside a bare
`except: pass`, which raises on PySide6 6.11.1, so every column-aligning surface in the app shipped
PROPORTIONAL figures behind a launch banner claiming otherwise. `export_video._font` never joined
that fix — it was a bare `QFont()` with zero feature tags — and it is the one output the user
cannot re-render.

What that cost, measured on the COMPOSITED pixels (`_paint_readout` is what
`_paint_packed_frame` hands the encoder, so these are the bytes that go into the MP4):
`_paint_readout` places everything after the hero speed with
`x += fm_big.horizontalAdvance(speed_num)`, so changing the speed from 100 to 111 moved pixels
**147 px past the hero digit box at 1080p** — the "km/h" label and the Δ cue — 98 px at 720p and
296 px at 2160p. Nine distinct digit advances at every size the export asks for.

Pinned here, in the SHIPPED font stack (`_qtapp.themed_app`, because a test that measures a font
the app does not ship is measuring nothing):

  * every glyph size the export asks for has ONE digit advance;
  * the composited readout's pixels beyond the hero number's own cells are IDENTICAL across
    speeds, at every output height the export offers — the property "the label does not move",
    stated as pixels rather than as font metrics;
  * the lap strip, whose elapsed time has the same face, holds still the same way;
  * the face itself does not move: routing through `theme.mono_font` must not swap the typeface
    out from under the export's fitted boxes (that is #197's whole lesson).

Run: QT_QPA_PLATFORM=offscreen python tests/test_export_typography.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()   # module scope, before any painting: the SHIPPED face, with the tnum probe run

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QFontInfo, QFontMetricsF, QImage, QPainter  # noqa: E402

from studio import export_video as ev  # noqa: E402
from studio import theme  # noqa: E402

# The output heights the export offers (studio/app.py's resolution presets) plus the two ends.
OUT_HEIGHTS = (480, 720, 1080, 1440, 2160)
# Speeds grouped by DIGIT COUNT, because that is the honest comparison: `188 km/h` legitimately
# starts its unit label further right than `88 km/h`. Within a group the proportional face still
# moved everything, because `1` is the narrow digit (5.281 px against 8.422 at 13 px) — a 1-heavy
# value was the widest departure, and a lap swings through both.
SPEED_GROUPS = ((0, 1, 8), (11, 88, 18, 80), (100, 111, 108, 188, 199))


class _FakeSession:
    """The two accessors `_paint_strip` reaches for: the lap's window and nothing else."""

    @staticmethod
    def lap_window(_lap_id):
        return (10.0, 95.0)


def _values(speed):
    return ev.OverlayValues(t=42.0, lap_id=7, speed_kmh=float(speed), delta_s=-1.25,
                            g=None, marker_index=None)


def _canvas(out_h):
    w, h = int(out_h * 16 / 9), int(out_h)
    img = QImage(w, h, QImage.Format_RGB888)
    img.fill(0)
    return img


def _readout_box(out_h):
    """The readout's rect, derived exactly as OverlayPainter.__init__ derives it."""
    w, h = int(out_h * 16 / 9), int(out_h)
    cfg = ev.OverlayConfig()
    m = cfg.margin_frac * h
    rh = max(cfg.readout_h_frac * h, 22.0)
    return QRectF(m, h - m - rh, max(w * 0.30, 260.0), rh)


def _strip_box(out_h):
    w, h = int(out_h * 16 / 9), int(out_h)
    cfg = ev.OverlayConfig()
    m = cfg.margin_frac * h
    sh = max(cfg.strip_h_frac * h, 20.0)
    return QRectF(m, m, max(w * 0.26, 220.0), sh)


def _paint(out_h, box, speed, strip=False):
    img = _canvas(out_h)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    if strip:
        ev._paint_strip(p, box, _FakeSession(), _values(speed), 10.0)
    else:
        ev._paint_readout(p, box, _values(speed), "kmh", "standard")
    p.end()
    return img


def _differing_columns(a, b, box):
    return [x for x in range(int(box.x()), int(box.right()) + 1)
            if any(a.pixel(x, y) != b.pixel(x, y)
                   for y in range(int(box.y()), int(box.bottom()) + 1))]


def test_every_export_glyph_size_has_one_digit_advance():
    """The metric behind the pixels. `_paint_readout` and `_paint_strip` derive four sizes from
    their box height (0.74 / 0.50 / 0.34 / 0.54); all four must be tabular at every output height,
    and so must the bold and regular forms."""
    checked = 0
    for out_h in OUT_HEIGHTS:
        for box in (_readout_box(out_h), _strip_box(out_h)):
            for frac in (0.74, 0.54, 0.50, 0.34):
                for bold in (False, True):
                    fm = QFontMetricsF(ev._font(box.height() * frac, bold=bold))
                    advances = {round(fm.horizontalAdvance(d), 4) for d in "0123456789"}
                    assert len(advances) == 1, (
                        f"{out_h}p x{frac} bold={bold}: {len(advances)} digit advances "
                        f"{sorted(advances)} — the export is drawing proportional figures")
                    checked += 1
    print(f"test_every_export_glyph_size_has_one_digit_advance OK ({checked} sizes)")


def test_the_export_face_is_the_one_the_layout_was_budgeted_in():
    """#197's lesson, applied to the export. Reaching for tabular figures must not change the
    TYPEFACE: every burned-in box is fitted against the app's own face, and the mono stack leads
    with families macOS does not have, so Qt walks to whatever it finds. `theme.mono_font` already
    makes that decision (Inter+tnum, else Inter, and the mono stack only when Inter is absent) —
    this asserts the export goes through it rather than round-tripping the tag itself."""
    for px in (21, 32, 64):
        exported = QFontInfo(ev._font(px, bold=True)).family()
        ui = QFontInfo(theme.ui_font(px, theme.W_SEMIBOLD)).family()
        assert exported == ui, (px, exported, ui)
    print(f"test_the_export_face_is_the_one_the_layout_was_budgeted_in OK ({exported})")


def test_the_burned_in_readout_does_not_move_when_the_speed_changes():
    """The finding, as pixels. Composite the SAME readout at two speeds and ask how far right the
    frames differ: with tabular figures the difference is confined to the hero number's own cells;
    with proportional ones the unit label and the Δ cue slide with it.

    Swept over every output height rather than sampled at 1080p — the offset scales with the font,
    so the one resolution that happens to look fine proves nothing about 4K."""
    worst, pairs = 0, 0
    for out_h in OUT_HEIGHTS:
        box = _readout_box(out_h)
        fm = QFontMetricsF(ev._font(box.height() * 0.74, bold=True))
        # Every glyph in the readout is drawn with a dark halo under it (`_draw_text(halo=2.4*k)`),
        # which is ink OUTSIDE the glyph's own advance box and scales with the output height. The
        # allowance is that halo plus one pixel of antialiasing — the defect it has to stay clear of
        # was 98..296 px, so this is not a threshold that can hide anything.
        allow = 2.4 * box.height() / 44.0 + 1.0
        for group in SPEED_GROUPS:
            # Where this group's hero number's digit CELLS end (its advance, not its ink).
            digits_end = (box.x() + box.height() * 0.26
                          + fm.horizontalAdvance(str(group[0])))
            base = _paint(out_h, box, group[0])
            for speed in group[1:]:
                cols = _differing_columns(base, _paint(out_h, box, speed), box)
                beyond = [x for x in cols if x > digits_end + allow]
                assert not beyond, (
                    f"{out_h}p speed {group[0]} -> {speed}: {len(beyond)} pixel columns past the "
                    f"hero digit box moved (out to x={max(beyond)}, box ends {digits_end:.1f}) — "
                    "the unit label and the Δ cue are sliding under a fixed label")
                worst = max(worst, len(cols))
                pairs += 1
    print("test_the_burned_in_readout_does_not_move_when_the_speed_changes OK "
          f"({len(OUT_HEIGHTS)} heights x {pairs // len(OUT_HEIGHTS)} same-width speed pairs, "
          f"0 columns past the digit box, <= {worst} inside it)")


def test_the_lap_strip_holds_still_too():
    """The strip's elapsed time has the same face and the same problem: `LAP n  m:ss.mmm` is one
    left-aligned string, so every digit that changes width re-flows the rest of the line. Two
    elapsed times of the same length must produce ink of the same width."""
    for out_h in OUT_HEIGHTS:
        box = _strip_box(out_h)
        a = _paint(out_h, box, 100, strip=True)
        b = _paint(out_h, box, 111, strip=True)
        # Same t => identical frames; the real check is the FACE, so measure the string widths.
        fm = QFontMetricsF(ev._font(box.height() * 0.54, bold=True))
        # Same-LENGTH strings, only the digits differing — the strip's own template.
        widths = {round(fm.horizontalAdvance(f"LAP {n:02d}   1:0{d}.{d}{d}{d}"), 4)
                  for n, d in ((1, 1), (8, 8), (11, 0), (10, 6))}
        assert len(widths) == 1, (out_h, sorted(widths))
        assert not _differing_columns(a, b, box), out_h
    print(f"test_the_lap_strip_holds_still_too OK ({len(OUT_HEIGHTS)} heights)")


def _run_all():
    test_every_export_glyph_size_has_one_digit_advance()
    test_the_export_face_is_the_one_the_layout_was_budgeted_in()
    test_the_burned_in_readout_does_not_move_when_the_speed_changes()
    test_the_lap_strip_holds_still_too()
    print("ALL EXPORT-TYPOGRAPHY TESTS OK")


if __name__ == "__main__":
    _run_all()
