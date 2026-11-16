"""The lap-strip card (studio/share_lapstrip.py): every lap of the session as a bar.

WHY THESE TESTS. The strip is a public image built from the Laps table's facts, and every way it can
lie is a way it could drift from that table or from the lap card it pairs with:
  * a bar count that is not the table's row count (a lap invented or silently dropped), or an
    excluded lap drawn as a slow lap instead of as what it is;
  * a "best" bar that is not the table's best lap;
  * a lap inside timing precision coloured as slower — the table's TONE_LEVEL rule, decided on the
    PRINTED milliseconds (printed_ms), so the edge is 0.069 s level / 0.070 s behind;
  * a legend naming a style the picture does not contain, or a cut-off outlier with no cap mark;
  * an honesty line that differs from the lap card's (media clock, GPS LOW, NO GPS, provisional);
  * an export that writes anywhere but the path it was given.

Runs offscreen; the Session is duck-typed exactly as far as `share_card.card_data` and
`lapstrip_data` reach, and the menu wiring runs on a real StudioWindow over a bare Session.
Run: python tests/test_share_lapstrip.py
"""
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QColor, QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import data_quality, share_card, theme  # noqa: E402
from studio import share_lapstrip as ls  # noqa: E402
from studio._signal import EXCLUDED_BAND, EXCLUDED_OPEN, EXCLUDED_STOPPED  # noqa: E402

theme.register_fonts()

CLEAN = data_quality.TimingQuality()
MEDIA = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
LOW = data_quality.TimingQuality(dropped_fraction=0.5)
NOGPS = data_quality.TimingQuality(clock=data_quality.NO_GPS_TRACE)


class FakeSession:
    """`times` {lap_id: s} are the table's rows; `excluded` {lap_id: reason} its ⊘ strip;
    `crossings` start/finish crossings too brief to be laps (in neither list)."""

    def __init__(self, times, *, dropouts=(), excluded=None, crossings=0, verified=True,
                 quality=CLEAN, track="Daytona MK", date="2026-06-29"):
        self.times = dict(times)
        self.dropouts = set(dropouts)
        self.excluded = dict(excluded or {})
        self.crossings = crossings
        self.timing_verified = verified
        self.timing_quality = quality
        self.track_name = track
        self._date = date

    def lap_rows(self):
        return [{"idx": i, "time": t, "dist": 1000.0, "entry": 50.0}
                for i, t in sorted(self.times.items())]

    def valid_lap_ids(self):
        return sorted(self.times)

    def best_lap_id(self):
        pool = {i: t for i, t in self.times.items() if i not in self.dropouts} or self.times
        return min(pool, key=pool.get) if pool else None

    def lap_time(self, i):
        return self.times[i] if i in self.times else 99.0

    def ideal_total(self):
        return None

    def ideal_donor_lap_id(self):
        return None

    def session_date(self):
        return self._date

    def coaching_opportunities(self):
        raise RuntimeError("not modelled")   # card_data degrades to "no opportunity"

    def dropout_lap_ids(self):
        return set(self.dropouts)

    def excluded_lap_ids(self):
        return sorted(self.excluded)

    def excluded_lap_reasons(self):
        return dict(self.excluded)

    def lap_count(self):
        return len(self.times) + len(self.excluded) + self.crossings


def long_session():
    """62 table rows round 60 s (lap ids 1..62), an outlier spin lap, a dropout lap, an out-lap
    excluded before them and a crossing too brief to count."""
    times = {i: 60.0 + 0.15 * ((i * 7) % 11) / 10 + 0.02 * (i % 3) for i in range(1, 63)}
    times[20] = 59.70      # the best
    times[33] = 71.50      # a spin: far past the fence
    return FakeSession(times, dropouts={45}, excluded={0: EXCLUDED_BAND}, crossings=1)


def _table_rows(session):
    """The Laps table's row count for this session — LapTable over the same duck-typed session."""
    return len(session.lap_rows())


# ------------------------------------------------------------------- the bars
def test_bar_count_is_the_lap_tables_row_count_and_excluded_laps_are_slots_not_bars():
    s = long_session()
    d = ls.lapstrip_data(s)
    timed = [b for b in d.bars if b.kind != ls.KIND_EXCLUDED]
    assert len(timed) == _table_rows(s) == 62
    ex = [b for b in d.bars if b.kind == ls.KIND_EXCLUDED]
    assert [b.lap_id for b in ex] == [0] and ex[0].height == 0.0 and ex[0].gap_s is None
    # driven order, nothing dropped: every lap id appears once, in order
    assert [b.lap_id for b in d.bars] == list(range(0, 63))
    # the too-brief crossing is no slot, and is said in the Laps tab's sentence
    assert d.too_brief == "1 other start/finish crossing was too brief to count as a lap."


def test_best_bar_is_the_tables_best_lap():
    s = long_session()
    d = ls.lapstrip_data(s)
    best = [b for b in d.bars if b.tone == ls.TONE_BEST]
    assert [b.lap_id for b in best] == [s.best_lap_id()] == [20]
    assert best[0].height == 0.0 and best[0].label == "21"
    # the table's best is dropout-aware: a quicker lap with a GPS dropout is not crowned
    s = FakeSession({0: 60.0, 1: 59.2, 2: 60.4}, dropouts={1})
    d = ls.lapstrip_data(s)
    assert [b.lap_id for b in d.bars if b.tone == ls.TONE_BEST] == [s.best_lap_id()] == [0]
    assert [b for b in d.bars if b.lap_id == 1][0].kind == ls.KIND_DROPOUT


def test_a_lap_inside_printed_precision_is_level_not_slower():
    # The table decides on the PRINTED ms: 60.0694 prints 1:00.069 (+0.069, level) and 60.0696
    # prints 1:00.070 (+0.070, behind) — the same edge `library.within_timing_precision` draws.
    s = FakeSession({0: 60.0, 1: 60.0694, 2: 60.0696, 3: 60.069, 4: 60.07, 5: 61.0})
    tones = {b.lap_id: b.tone for b in ls.lapstrip_data(s).bars}
    assert tones == {0: ls.TONE_BEST, 1: ls.TONE_LEVEL, 2: ls.TONE_BEHIND, 3: ls.TONE_LEVEL,
                     4: ls.TONE_BEHIND, 5: ls.TONE_BEHIND}
    # ...and in the picture the level bar wears the best lap's hue, not the neutral "slower" grey
    d = ls.lapstrip_data(s)
    img = ls.render_lapstrip(d)
    best_hue = QColor(theme.best_lap_colour())
    level, behind = _bar_pixel(img, d, 1), _bar_pixel(img, d, 2)
    assert _hue_close(level, best_hue), level.name()
    assert not _hue_close(behind, best_hue), behind.name()


def _bar_x(d, lap_id):
    n = len(d.bars)
    plot_l, plot_r = ls.PAD, ls.RIGHT - ls.SCALE_COL
    slot = (plot_r - plot_l) / n
    if slot * 0.72 > ls.MAX_BAR_W:
        slot = ls.MAX_BAR_W / 0.6
    x0 = plot_l + ((plot_r - plot_l) - slot * n) / 2
    i = [b.lap_id for b in d.bars].index(lap_id)
    return int(x0 + slot * (i + 0.5))


def _base(d) -> int:
    return int(ls.legend_plan(d).plot_base)


def _bar_pixel(img: QImage, d, lap_id) -> QColor:
    return img.pixelColor(_bar_x(d, lap_id), _base(d) - 6)


def test_heights_are_proportional_to_gaps_from_a_zero_base():
    # The base IS the zero gap: a +0.4 s lap is exactly twice a +0.2 s lap, and the best has none.
    d = ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.2, 2: 60.4, 3: 61.0}))
    h = {b.lap_id: b.height for b in d.bars}
    assert h[0] == 0.0 and abs(h[2] / h[1] - 2.0) < 1e-9
    # ...and in pixels: the bar tops measured off the render sit at gap/scale of the plot height
    img = ls.render_lapstrip(d)
    base, canvas = _base(d), QColor(theme.C.canvas)

    def top(lap_id):
        x = _bar_x(d, lap_id)
        y = base - 3
        while y > ls.PLOT_TOP and img.pixelColor(x, y - 1) != canvas \
                and img.pixelColor(x, y - 1).lightness() > 60:
            y -= 1
        return base - y

    assert abs(top(2) / top(1) - 2.0) < 0.05, (top(1), top(2))


def test_a_tie_has_one_best_and_the_other_is_level():
    d = ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.0, 2: 60.5}))
    tones = [b.tone for b in d.bars]
    assert tones.count(ls.TONE_BEST) == 1 and tones[1] == ls.TONE_LEVEL
    # the tied lap is still a visible (short) bar in the level tone, not invisible at the base
    img = ls.render_lapstrip(d)
    assert _hue_close(img.pixelColor(_bar_x(d, 1), _base(d) - 2), QColor(theme.best_lap_colour()))


def test_a_dropout_lap_quicker_than_the_best_is_marked_quicker_not_level():
    s = FakeSession({0: 60.0, 1: 60.3, 2: 59.4, 3: 60.5}, dropouts={2})
    d = ls.lapstrip_data(s)
    q = [b for b in d.bars if b.lap_id == 2][0]
    assert q.gap_s == -0.6 and q.tone == ls.TONE_QUICKER and q.height == 0.0
    assert dict(ls.legend_entries(d))[ls.TONE_QUICKER] == \
        "lap 3 −0.60 s: quicker, GPS dropout (not counted)"
    # drawn BELOW the zero line (where no other bar goes), not on top of it like a tie
    img = ls.render_lapstrip(d)
    x, base, canvas = _bar_x(d, 2), _base(d), QColor(theme.C.canvas)
    assert any(img.pixelColor(x, y) != canvas for y in range(base + 4, base + 12))
    assert all(img.pixelColor(x, y) == canvas for y in range(base - 30, base - 6))


def _hue_close(c: QColor, ref: QColor) -> bool:
    return abs(c.hue() - ref.hue()) < 12 and c.saturation() > 60


def test_dropout_lap_is_hatched_and_legend_names_only_what_is_there():
    s = long_session()
    d = ls.lapstrip_data(s)
    drop = [b for b in d.bars if b.lap_id == 45][0]
    assert drop.kind == ls.KIND_DROPOUT and drop.gap_s is not None
    styles = [k for k, _ in ls.legend_entries(d)]
    assert ls.KIND_DROPOUT in styles and ls.KIND_EXCLUDED in styles and "clipped" in styles
    assert dict(ls.legend_entries(d))[ls.KIND_EXCLUDED] == "left out: 1 off the session median"
    # a clean short session: no dropout, no excluded, no clip, no level -> none of those named
    plain = ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.5, 2: 61.0}))
    assert [k for k, _ in ls.legend_entries(plain)] == [ls.TONE_BEST, ls.TONE_BEHIND]
    # no grey bar -> no grey key: two laps, the second level with the best
    level = ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.03}))
    assert [k for k, _ in ls.legend_entries(level)] == [ls.TONE_BEST, ls.TONE_LEVEL]
    # the hatched bar really is hatched: a column through it is not one colour, a solid one is
    img = ls.render_lapstrip(d)
    base = _base(d)
    col = {img.pixelColor(_bar_x(d, 45), y).name() for y in range(base - 40, base - 4)}
    assert len(col) > 1, "a dropout bar must not be solid"
    solid = {img.pixelColor(_bar_x(d, 44), y).name() for y in range(base - 40, base - 4)}
    assert len(solid) == 1


def test_the_outlier_is_clipped_and_the_cap_is_marked():
    s = long_session()
    d = ls.lapstrip_data(s)
    spin = [b for b in d.bars if b.lap_id == 33][0]
    assert spin.clipped and spin.height == 1.0
    assert sum(b.clipped for b in d.bars) == 1
    assert d.scale_s is not None and d.scale_s < spin.gap_s
    cap = dict(ls.legend_entries(d))["clipped"]
    assert f"+{d.scale_s:.2f} s" in cap
    # with no outlier nothing is clipped and no cap is claimed
    assert not any(b.clipped for b in ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.5})).bars)


def test_four_or_more_cut_laps_are_counted_not_named():
    times = {i: 60.0 + 0.1 * (i % 5) for i in range(20)}
    for i in (3, 7, 11, 15):
        times[i] = 75.0 + i
    entry = dict(ls.legend_entries(ls.lapstrip_data(FakeSession(times))))["clipped"]
    assert entry.startswith("4 laps cut at the +") and "lap 4" not in entry, entry
    times[15] = 60.2   # three cut laps: named, each with its real gap
    entry = dict(ls.legend_entries(ls.lapstrip_data(FakeSession(times))))["clipped"]
    assert entry.startswith("lap 4 +18.00 s, lap 8 +22.00 s, lap 12 +26.00 s cut at"), entry


def test_the_median_is_the_clean_laps_median_gap():
    times = {0: 60.0, 1: 60.2, 2: 60.4, 3: 60.6, 4: 60.8, 5: 70.0}
    d = ls.lapstrip_data(FakeSession(times, dropouts={5}))   # the dropout lap is not in it
    assert d.median_gap_s == 0.4
    assert ls.best_median_line(d.median_gap_s, d.clean_laps) == \
        "median of 5 clean laps: +0.40 s off the best"
    # under MEDIAN_MIN_LAPS clean laps the median is one of the bars: none is drawn
    assert ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4, 2: 60.8})).median_gap_s is None
    # inside timing precision it is "≈ best", never a figure finer than the timing resolves
    flat = ls.lapstrip_data(FakeSession({i: 60.0 + 0.01 * i for i in range(6)}))
    assert ls.median_figure(flat.median_gap_s) == "≈ best"
    assert ls.best_median_line(flat.median_gap_s, 6) == "median of 6 clean laps: level with the best"


def worst_case():
    """The legend's worst case: 10 cut laps, 10 dropouts, a level lap, 12 excluded laps with all
    their reasons, 3 brief crossings, a very long title, mph."""
    times = {i: 60.0 + 0.05 * (i % 9) for i in range(12, 80)}
    for i in range(12, 22):
        times[i] = 80.0 + i       # cut
    times[30] = 59.0               # the best
    times[31] = 59.03              # level
    reasons = (EXCLUDED_BAND, EXCLUDED_OPEN, EXCLUDED_STOPPED, "unknown_reason")
    excluded = {i: reasons[i % 4] for i in range(12)}
    return FakeSession(times, dropouts=set(range(40, 50)), excluded=excluded, crossings=3,
                       track="Circuito Internacional de Velocidade e Karting de Lagoa Grande "
                             "Layout Norte Completo")


def test_the_worst_case_legend_stays_above_the_footer_and_inside_the_canvas():
    d = ls.lapstrip_data(worst_case(), unit="mph")
    plan = ls.legend_plan(d)
    assert plan.items, "the legend vanished"
    last = max(it.y for it in plan.items)
    assert last <= ls.LEGEND_LIMIT < ls.FOOTER_Y - 40, (last, ls.LEGEND_LIMIT)
    for it in plan.items:
        assert ls.PAD <= it.x and it.x + it.w <= ls.RIGHT + 0.5, it
    # the too-brief sentence is the last thing to go, and the cut laps are counted
    styles = [it.style for it in plan.items]
    assert "too_brief" in styles and "clipped" in styles
    cut = [it for it in plan.items if it.style == "clipped"][0]
    assert cut.text.startswith("10 laps cut"), cut.text
    assert plan.plot_base >= ls.PLOT_BASE_MIN    # the plot lifts, but is never squashed further
    # OVER-full: three named quicker laps and three named cut laps each take a row, so even with
    # the plot lifted something must go — the grey key first, the too-brief sentence kept
    times = {i: 60.0 + 0.05 * (i % 9) for i in range(40)}
    for i in (2, 4, 6):
        times[i] = 59.0 - 0.1 * i          # quicker, with a dropout
    for i in (10, 20, 30):
        times[i] = 90.0 + i                # cut
    times[12] = 59.5                       # the best clean lap
    times[13] = 59.53                      # level
    over = FakeSession(times, dropouts={2, 4, 6, 25}, excluded={40: EXCLUDED_BAND}, crossings=2,
                       track="Circuito Internacional de Velocidade e Karting de Lagoa Grande")
    plan = ls.legend_plan(ls.lapstrip_data(over))
    assert plan.dropped and plan.dropped[0] == ls.TONE_BEHIND, plan.dropped
    assert "too_brief" not in plan.dropped
    assert max(it.y for it in plan.items) <= ls.LEGEND_LIMIT
    assert plan.plot_base >= ls.PLOT_BASE_MIN
    # rendered: no ink in the outer 24 px border (nothing ran off the canvas)
    img = ls.render_lapstrip(d)
    canvas = QColor(theme.C.canvas)
    for x in list(range(0, 24)) + list(range(ls.CARD_W - 24, ls.CARD_W)):
        for y in range(0, ls.CARD_H, 3):
            assert img.pixelColor(x, y) == canvas, (x, y)


def test_a_tight_session_is_not_stretched_to_fill_the_plot():
    d = ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.1, 2: 60.19}))
    assert d.scale_s == ls.MIN_SPAN_S
    assert max(b.height for b in d.bars) < 0.5


# --------------------------------------------------------------- honesty
def test_the_honesty_line_is_the_lap_cards_own():
    for q in (CLEAN, MEDIA, LOW, NOGPS):
        s = FakeSession({0: 60.0, 1: 60.4, 2: 60.8}, quality=q)
        card, strip = share_card.card_data(s), ls.lapstrip_data(s)
        assert (strip.stamp, strip.blocked) == (card.stamp, card.blocked), q
        assert strip.provenance == card.provenance, q
        assert (strip.track, strip.date, strip.best_time) == (card.track, card.date, card.best_time)
    assert ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4}, quality=MEDIA)).stamp == \
        "estimated timing"
    # the clean footer is the report's own Timing words; a stamped card never also claims them
    assert ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4})).provenance == \
        "verified start line · GPS9 true clock"
    assert ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4}, quality=LOW)).provenance == ""
    # a provisional line: the lap card refuses, so does the strip
    prov = ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4}, verified=False))
    assert prov.blocked and not prov.offered


def test_offered_needs_two_clean_laps():
    assert not ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4}, dropouts={1})).offered
    assert ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4})).offered
    assert ls.clean_lap_count(FakeSession({0: 60.0, 1: 60.4, 2: 61.0}, dropouts={2})) == 2


# --------------------------------------------------------------- render + export
def test_renders_every_shape_at_the_lap_cards_size():
    for s in (long_session(), FakeSession({0: 60.0, 1: 60.4, 2: 61.0}),
              FakeSession({0: 60.0, 1: 60.4}, quality=MEDIA)):
        for pal in (theme.PALETTE_STANDARD, theme.PALETTE_COLORBLIND):
            img = ls.render_lapstrip(ls.lapstrip_data(s), palette=pal)
            assert (img.width(), img.height()) == (share_card.CARD_W, share_card.CARD_H)
            assert len(ls.lapstrip_to_png(img)) > 10_000


def test_save_writes_exactly_the_path_given():
    img = ls.render_lapstrip(ls.lapstrip_data(long_session()))
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "strip.png")
        ls.save_lapstrip_png(img, path)
        assert os.listdir(tmp) == ["strip.png"]
        assert QImage(path).size() == img.size()


def _bare_window(session):
    """test_share_card's fixture shape: a StudioWindow without its heavy __init__."""
    from PySide6.QtWidgets import QMainWindow

    from studio.app import STATUS_MS, StudioWindow
    from studio.export_controller import ExportController
    w = StudioWindow.__new__(StudioWindow)
    QMainWindow.__init__(w)
    w.session = session
    w._speed_unit = "kmh"
    w._paths = ["/nonexistent/rec/GX010067.MP4"]
    w.view = SimpleNamespace()
    w.exports = ExportController(w, STATUS_MS)
    return w


def test_menu_action_saves_to_the_dialog_path_named_after_the_best_lap():
    """File ▸ Export ▸ Lap-strip card…: saves exactly where the dialog says, with a default name
    that carries the best lap (the lap card's `_lap14_card.png` convention); cancel writes nothing."""
    from PySide6.QtWidgets import QFileDialog

    w = _bare_window(FakeSession({0: 60.0, 1: 59.5, 2: 60.4}))
    asked = {}
    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "chosen.png")

        def fake_save(parent, title, default, filt):
            asked["default"] = default
            return out, filt

        orig = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(fake_save)
        try:
            w.exports.export_lapstrip_card()
        finally:
            QFileDialog.getSaveFileName = orig
        assert os.path.basename(asked["default"]) == "GX010067_lapstrip_best_lap2.png"
        assert os.listdir(tmp) == ["chosen.png"]
        assert QImage(out).width() == share_card.CARD_W

        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: ("", ""))
        try:
            w.exports.export_lapstrip_card()
        finally:
            QFileDialog.getSaveFileName = orig
        assert os.listdir(tmp) == ["chosen.png"]


def test_menu_gate_greys_the_strip_under_two_clean_laps_and_says_why():
    from PySide6.QtGui import QAction

    for times, drop, ok in (({0: 60.0, 1: 60.4}, set(), True), ({0: 60.0, 1: 60.4}, {1}, False)):
        w = _bare_window(FakeSession(times, dropouts=drop))
        w._lapstrip_action = QAction("Lap-strip card…")
        w.exports.gate_lapstrip(has_laps=True)
        assert w._lapstrip_action.isEnabled() is ok
        if not ok:
            assert "two clean laps" in w._lapstrip_action.toolTip()
            # triggered anyway: the status bar says why, not "no verified laps"
            w.exports.export_lapstrip_card()
            assert "fewer than two clean laps" in w.statusBar().currentMessage()


def test_the_reports_strip_is_the_cards_png_under_the_cards_gate():
    """The HTML report embeds this card (`export_controller.report_lapstrip_png`): real PNG bytes
    on a session the card is offered for, and NO strip where its gate greys the menu action — a
    provisional start line, or fewer than two clean laps. Kept here, beside the card's own gate
    tests, because the strip's FakeSession is here."""
    from studio.export_controller import report_lapstrip_png
    png = report_lapstrip_png(FakeSession({0: 60.0, 1: 60.4, 2: 61.0}), "kmh")
    assert png is not None and png.startswith(b"\x89PNG\r\n\x1a\n") and len(png) > 1000, png[:8]
    img = QImage.fromData(png)
    assert (img.width(), img.height()) == (ls.CARD_W, ls.CARD_H), (img.width(), img.height())
    assert report_lapstrip_png(FakeSession({0: 60.0, 1: 60.4}, verified=False), "kmh") is None
    assert report_lapstrip_png(FakeSession({0: 60.0, 1: 60.4}, dropouts={1}), "kmh") is None


if __name__ == "__main__":
    fns =[v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
