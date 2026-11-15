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
from studio._signal import EXCLUDED_BAND  # noqa: E402

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
    assert best[0].height == ls.STUB and best[0].label == "21"
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


def _bar_pixel(img: QImage, d, lap_id) -> QColor:
    return img.pixelColor(_bar_x(d, lap_id), ls.PLOT_BASE - 6)


def _hue_close(c: QColor, ref: QColor) -> bool:
    return abs(c.hue() - ref.hue()) < 12 and c.saturation() > 60


def test_dropout_lap_is_hatched_and_legend_names_only_what_is_there():
    s = long_session()
    d = ls.lapstrip_data(s)
    drop = [b for b in d.bars if b.lap_id == 45][0]
    assert drop.kind == ls.KIND_DROPOUT and drop.gap_s is not None
    styles = [k for k, _ in ls.legend_entries(d)]
    assert ls.KIND_DROPOUT in styles and ls.KIND_EXCLUDED in styles and "clipped" in styles
    assert dict(ls.legend_entries(d))[ls.KIND_EXCLUDED] == "⊘ left out: 1 off the session median"
    # a clean short session: no dropout, no excluded, no clip, no level -> none of those named
    plain = ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.5, 2: 61.0}))
    assert [k for k, _ in ls.legend_entries(plain)] == [ls.TONE_BEST, ls.TONE_BEHIND]
    # the hatched bar really is hatched: its interior has canvas showing between the lines
    img = ls.render_lapstrip(d)
    x, canvas = _bar_x(d, 45), QColor(theme.C.canvas)
    col = [img.pixelColor(x, y) for y in range(ls.PLOT_BASE - 40, ls.PLOT_BASE - 4)]
    assert any(c == canvas for c in col), "a dropout bar must not be solid"
    solid = [img.pixelColor(_bar_x(d, 44), y) for y in range(ls.PLOT_BASE - 40, ls.PLOT_BASE - 4)]
    assert not any(c == canvas for c in solid)


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


def test_the_median_is_the_clean_laps_median_gap():
    times = {0: 60.0, 1: 60.2, 2: 60.4, 3: 60.6, 4: 60.8, 5: 70.0}
    d = ls.lapstrip_data(FakeSession(times, dropouts={5}))   # the dropout lap is not in it
    assert d.median_gap_s == 0.4
    assert ls.best_median_line(d.median_gap_s) == "median lap +0.40 s off the best"
    # under MEDIAN_MIN_LAPS clean laps the median is one of the bars: none is drawn
    assert ls.lapstrip_data(FakeSession({0: 60.0, 1: 60.4, 2: 60.8})).median_gap_s is None


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


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"{len(fns)} passed")
