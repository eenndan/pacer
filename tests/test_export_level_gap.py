"""A burned-in gap the timing cannot tell from zero is drawn level (owner-approved design 3A + Q3a).

WHY. The Classic overlay painted "Δ +0.03" bright red while Broadcast, the corner cards, the Laps
table and the live compare seam all show a gap under the timing step (`library.PB_PRECISION_S`,
0.07 s, decided on the PRINTED value) in neutral white: red there said "you are behind" where the
honest answer is "no measurable difference". The owner chose (2026-11-18) Broadcast's rule,
unchanged and with no hold, for the Classic lap strip's Δ AND the side-by-side compare export's Δ
pill. The number still prints with its sign (and the compare pill's arrow); only its colour is level.

WHAT THIS HOLDS.
  * The step: printed ±0.06 is white, ±0.07 and ±0.08 keep their colour, in both palettes, in the
    Classic strip and in the compare pill — painted, not only decided.
  * ONE rule: Classic and the compare pill call Broadcast's own `overlay.broadcast.is_level`, so
    giving Classic a threshold of its own, or a copy of the rule, fails here.
  * Nothing else moved: on the demo's telemetry, painted against main's rule (`export_delta_colour`
    alone), Broadcast, the Extras (ghost, corner cards) and the best-lap Classic export are
    byte-identical, and a Classic frame changes only where its printed gap is level — only inside
    the Δ's box, never its shape (the overlay-only alpha is identical).
  * An estimated (media-clock) recording keeps Broadcast's step: the burned overlays have one rule,
    and the Classic strip's colour is the Broadcast tower's on every frame. (The Lap race and the
    story's 0.15 s tie on that clock are not an overlay rule; widening it would move Broadcast and
    the corner cards too, which the approved design leaves as they are.)

Run: python tests/test_export_level_gap.py
"""
import os
import sys
import tempfile
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
sys.path.insert(0, HERE)

import numpy as np  # noqa: E402

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("pacer-test-export-level-gap-")

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402

from studio import chapters, data_quality, library, theme  # noqa: E402
from studio import export_compare as ec  # noqa: E402
from studio import export_video as ev  # noqa: E402
from studio.dev import make_demo as md  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402
from studio.export_palette import EXPORT  # noqa: E402
from studio.overlay import broadcast  # noqa: E402
from studio.session import Session  # noqa: E402

PALETTES = (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND)
LEVEL = (0.06, -0.06, 0.0649, -0.0649, 0.004, -0.004)        # print ±0.06 / ±0.00
COLOURED = (0.07, -0.07, 0.0651, -0.0651, 0.08, -0.08, 0.35, -0.35)
_FIX: dict = {}


def _fixture() -> dict:
    """The demo's telemetry (seed, laps, origin, habit) under the flat placeholder picture."""
    if _FIX:
        return _FIX
    tmp = tempfile.TemporaryDirectory(prefix="pacer-export-level-gap-")
    rec = sg.generate(os.path.join(tmp.name, "rec"), md.DEMO_SEED, md.DEMO_LAPS, chapters=1,
                      origin=md.DEMO_ORIGIN, habit=md.DEMO_HABIT)
    s = Session.load(chapters.discover_siblings(rec.paths[0]))
    best = s.best_lap_id()
    others = [i for i in s.valid_lap_ids() if i != best]
    _FIX.update(tmp=tmp, s=s, best=best, others=others, out=os.path.join(tmp.name, "never.mp4"))
    return _FIX


def _expected(d: float, palette: str) -> str:
    ahead, behind = ev.export_semantic_pair(palette)
    return ahead if d < 0 else behind


# ------------------------------------------------------------------------------ the step
def test_the_classic_strip_colours_a_gap_from_the_printed_step():
    for pal in PALETTES:
        for d in LEVEL:
            text, colour = ev.strip_tail(d, False, pal)
            assert colour == EXPORT.neutral, (pal, d, text, colour)
        for d in COLOURED:
            text, colour = ev.strip_tail(d, False, pal)
            assert colour == _expected(d, pal), (pal, d, text, colour)
    # The number still prints, with its sign; only the colour is level.
    assert ev.strip_tail(0.06)[0] == "Δ +0.06" and ev.strip_tail(-0.06)[0] == "Δ −0.06"
    # The best lap is untouched: the ★ BEST mark in the accent, never a Δ.
    assert ev.strip_tail(0.03, is_best=True) == ("★ BEST", EXPORT.accent)
    print("ok Classic: printed ±0.06 white, ±0.07 and over coloured, both palettes")


def _paint_compare(deltas, palette, layout=ec.LAYOUT_SIDE):
    """Paint one compare frame per Δ at 720p on a grey picture; return the Δ pill's pixels."""
    cfg = ec.CompareConfig(layout=layout, out_height=720, palette=palette)
    geo = ec.compare_geometry((1920, 1080), (1920, 1080), cfg)
    frames = [ec.CompareFrame(fraction=0.5, delta=d, caption_a="LAP 7   0:20.000",
                              caption_b="LAP 13   0:20.000", speed_a_kmh=60.0, speed_b_kmh=60.0,
                              speed_a="60", speed_b="60",
                              delta_run=theme.format_delta_run(d, units=False, arrow=True))
              for d in deltas]
    painter = ec.ComparePainter(geo, cfg, frames, "LAP 7 vs LAP 13")
    box = painter.delta_box()
    out = []
    for f in frames:
        img = QImage(geo.out_w, geo.out_h, QImage.Format_RGB888)
        img.fill(0x5A6070)
        painter.paint(img, f)
        a = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * img.height())
        a = a.reshape(img.height(), img.bytesPerLine())[:, : geo.out_w * 3]
        a = a.reshape(geo.out_h, geo.out_w, 3).astype(int)
        out.append(a[int(box.top()):int(box.bottom()), int(box.left()):int(box.right())])
    return out


def _hue_ink(px: np.ndarray, colour: str) -> int:
    """Pixels within a few levels of `colour` (#RRGGBB): the glyph's solid fill."""
    rgb = np.array([int(colour[i:i + 2], 16) for i in (1, 3, 5)])
    return int((np.abs(px - rgb).max(axis=-1) <= 6).sum())


def test_the_compare_pill_colours_a_gap_from_the_same_step():
    for pal in PALETTES:
        ahead, behind = ev.export_semantic_pair(pal)
        for layout in (ec.LAYOUT_SIDE, ec.LAYOUT_STACK):
            pills = _paint_compare(LEVEL + COLOURED, pal, layout)
            for d, px in zip(LEVEL + COLOURED, pills, strict=True):
                n_ahead, n_behind = _hue_ink(px, ahead), _hue_ink(px, behind)
                if d in LEVEL:
                    assert n_ahead == n_behind == 0, (pal, layout, d, n_ahead, n_behind)
                    assert _hue_ink(px, EXPORT.neutral) > 50, (pal, layout, d)
                else:
                    want, other = (n_ahead, n_behind) if d < 0 else (n_behind, n_ahead)
                    assert want > 50 and other == 0, (pal, layout, d, n_ahead, n_behind)
    print("ok compare: the Δ pill is white at printed ±0.06, coloured from ±0.07, both layouts")


def test_the_rule_is_broadcasts_own_object():
    """Classic and the compare pill CALL `broadcast.is_level`: swap it and both follow; widen the
    one precision constant and all three follow. A Classic threshold of its own fails here."""
    real = broadcast.is_level
    try:
        broadcast.is_level = lambda d: True
        assert ev.strip_tail(0.5)[1] == EXPORT.neutral
        assert _hue_ink(_paint_compare((0.5,), theme.PALETTE_STANDARD)[0], EXPORT.behind) == 0
        broadcast.is_level = lambda d: False
        assert ev.strip_tail(0.03)[1] == EXPORT.behind
        assert _hue_ink(_paint_compare((0.03,), theme.PALETTE_STANDARD)[0], EXPORT.behind) > 50
    finally:
        broadcast.is_level = real
    step = library.PB_PRECISION_S
    try:
        library.PB_PRECISION_S = 0.10
        assert broadcast.is_level(0.09) and ev.strip_tail(0.09)[1] == EXPORT.neutral
        assert _hue_ink(_paint_compare((0.09,), theme.PALETTE_STANDARD)[0], EXPORT.behind) == 0
    finally:
        library.PB_PRECISION_S = step
    assert ev.strip_tail(0.09)[1] == EXPORT.behind
    print("ok one rule: Classic and the compare pill follow broadcast.is_level and its constant")


# ------------------------------------------------------------------------------ nothing else moved
def _frames(spec, s, out_h, stride, overlay_only=True):
    """{frame index: (rgba bytes, alpha bytes)} every `stride`-th frame, the dial advanced on every
    frame as the render does."""
    geo = ev.frame_geometry(1920, 1080, spec.config)
    painter = ev.OverlayPainter(s, spec, geo.out_w, geo.out_h, 30000 / 1001)
    out = {}
    for i, t in enumerate(ev.frame_times(spec.t0, spec.t1, 30000 / 1001)):
        vals = ev.overlay_values_at(s, float(t), spec)
        dial = painter.advance_and_snapshot(vals)
        if i % stride:
            continue
        fmt = QImage.Format_RGBA8888 if overlay_only else QImage.Format_RGB888
        img = QImage(geo.out_w, geo.out_h, fmt)
        img.fill(Qt.transparent if overlay_only else 0x5A6070)
        painter.paint_frame_with_state(img, vals, dial)
        a = np.frombuffer(img.constBits(), np.uint8, count=img.bytesPerLine() * img.height())
        a = a.reshape(img.height(), img.bytesPerLine()).copy()
        out[i] = (a, vals, painter)
    return out


class _MainRule:
    """main's colour rule for the duration of a `with` block: the gap coloured even inside the step."""

    def __enter__(self):
        self._ev, self._ec = ev.burned_delta_colour, ec.burned_delta_colour
        ev.burned_delta_colour = ec.burned_delta_colour = ev.export_delta_colour

    def __exit__(self, *_a):
        ev.burned_delta_colour, ec.burned_delta_colour = self._ev, self._ec


def _spec(s, lap, style="classic", extras=(), **kw):
    cfg = broadcast.styled(ev.OverlayConfig(elements=extras, **kw), style)
    return ev.build_lap_spec(s, _fixture()["out"], lap, config=cfg)


def test_classic_changes_only_where_the_printed_gap_is_level():
    f = _fixture()
    s = f["s"]
    lap = _most_level_lap(s, f["others"])
    spec = _spec(s, lap, out_height=360)
    with _MainRule():
        before = _frames(spec, s, 360, 4)
    after = _frames(spec, s, 360, 4)
    n_level = n_same = 0
    for i, (a, vals, painter) in after.items():
        b = before[i][0]
        level = vals.lap_started and vals.delta_s is not None and broadcast.is_level(vals.delta_s) \
            and ev.strip_tail(vals.delta_s)[0] != "Δ 0.00"
        if not level:
            assert np.array_equal(a, b), f"frame {i}: moved with a gap of {vals.delta_s}"
            n_same += 1
            continue
        n_level += 1
        # Only colour: the alpha (the shape) is identical, and every changed pixel is in the
        # strip's Δ run — right of the clock, inside the strip pill grown by its halo.
        w = painter._w
        aa, bb = a[:, : w * 4].reshape(-1, w, 4), b[:, : w * 4].reshape(-1, w, 4)
        assert np.array_equal(aa[..., 3], bb[..., 3]), f"frame {i}: the Δ's shape moved"
        ys, xs = np.nonzero((aa != bb).any(-1))
        assert len(ys), f"frame {i}: a level gap ({vals.delta_s}) kept its colour"
        r = painter._strip_rect
        k = r.height() / 44.0
        assert ys.min() >= r.top() - 6 * k and ys.max() <= r.bottom() + 6 * k, (i, ys.min(), ys.max())
        assert xs.max() <= r.right() + 6 * k and xs.min() > r.left() + r.width() / 2, (i, xs.min())
    assert n_level > 10 and n_same > 10, (n_level, n_same)
    print(f"ok Classic lap {lap}: {n_level} level frames changed only in the Δ's colour, "
          f"{n_same} others byte-identical")


def _without_delta_run(a: np.ndarray, painter) -> np.ndarray:
    """`a` (rows of RGBA) with the strip's right half — where the Δ run sits — grown by its halo,
    zeroed."""
    r = painter._strip_rect
    g = 6 * r.height() / 44.0
    out = a.copy()
    out[max(0, int(r.top() - g)):int(r.bottom() + g) + 1,
        int(r.left() + r.width() / 2) * 4:(int(r.right() + g) + 1) * 4] = 0
    return out


def _most_level_lap(s, laps):
    """The lap whose Classic export has the most frames with a level gap (lap 7 on the demo)."""
    def share(lap):
        spec = _spec(s, lap)
        return sum(broadcast.is_level(ev.overlay_values_at(s, float(t), spec).delta_s or 0.0)
                   for t in ev.frame_times(spec.t0, spec.t1, 30000 / 1001)[::10])
    return max(laps, key=share)


def test_broadcast_extras_and_the_best_lap_are_byte_identical():
    f = _fixture()
    s, best = f["s"], f["best"]
    lap = f["others"][0]
    cases = [(best, "classic", ()), (best, "classic", ("ghost", "corner_cards")),
             (lap, "classic", ("ghost", "corner_cards")),        # the Extras' own pixels
             (lap, "broadcast", ("ghost", "corner_cards")), (best, "broadcast", ()),
             (lap, "broadcast", ())]
    n = 0
    for lp, style, extras in cases:
        for pal in PALETTES:
            spec = _spec(s, lp, style, extras, out_height=360, palette=pal)
            with _MainRule():
                before = _frames(spec, s, 360, 9)
            after = _frames(spec, s, 360, 9)
            for i, (a, _vals, painter) in after.items():
                b = before[i][0]
                if lp != best and style == "classic":
                    # Only the strip's Δ run may move here; the Extras' pixels must not.
                    a, b = _without_delta_run(a, painter), _without_delta_run(b, painter)
                assert np.array_equal(a, b), f"{style} lap {lp} {extras} {pal}: frame {i} moved"
                n += 1
    print(f"ok Broadcast, the Extras and the best lap: {n} frames byte-identical to main's rule")


def test_an_estimated_clock_keeps_broadcasts_step():
    f = _fixture()
    s = f["s"]
    real = s._timing_quality
    s._timing_quality = replace(real, clock=data_quality.MEDIA_CLOCK_FALLBACK)
    try:
        assert s.timing_quality.media_clock
        assert data_quality.STAMP_ESTIMATED in data_quality.burned_timing_stamp(s)
        lap = f["others"][0]
        spec = _spec(s, lap, "broadcast", ())
        geo = ev.frame_geometry(1920, 1080, spec.config)
        tower = next(e for e in ev.OverlayPainter(s, spec, geo.out_w, geo.out_h,
                                                  30000 / 1001).elements if e.name == "broadcast")
        seen = {"level": 0, "coloured": 0}
        for t in ev.frame_times(spec.t0, spec.t1, 30000 / 1001):
            vals = ev.overlay_values_at(s, float(t), spec)
            runs = ev._strip_runs(s, vals, spec.lap_t0, False, None)
            if runs is None or not runs[1].startswith("Δ "):
                continue
            colour = tower.state(vals)[3]
            assert runs[2] == colour, (vals.delta_s, runs[2], colour)
            seen["level" if colour == EXPORT.neutral else "coloured"] += 1
        assert seen["level"] > 10 and seen["coloured"] > 10, seen
        # The step on this clock is the overlays' one step, not the Lap race's 0.15 s tie.
        assert ev.strip_tail(0.14)[1] == EXPORT.behind and ev.strip_tail(0.06)[1] == EXPORT.neutral
    finally:
        s._timing_quality = real
    print(f"ok estimated clock: Classic's Δ colour is the Broadcast tower's on every frame {seen}")


def _run_all():
    for fn in (test_the_classic_strip_colours_a_gap_from_the_printed_step,
               test_the_compare_pill_colours_a_gap_from_the_same_step,
               test_the_rule_is_broadcasts_own_object,
               test_classic_changes_only_where_the_printed_gap_is_level,
               test_broadcast_extras_and_the_best_lap_are_byte_identical,
               test_an_estimated_clock_keeps_broadcasts_step):
        fn()
    print("test_export_level_gap: all ok")


if __name__ == "__main__":
    _run_all()
