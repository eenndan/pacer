"""LapTable / CornerTable COLUMN LAYOUT — the panel FIT (QA L2-06 · L3-03 · L3-04) and the
provisional/degraded column sets (QA L3-02).

P5 made the data columns content-tight with one blank trailing SPACER column absorbing the slack,
because the old stretched-LAST-section ballooned the Entry column past 300px with its values pinned
to the far right. That fixed the wide end by breaking both ends:

  * maximized, the four real columns totalled 382px of a 1432px viewport and the spacer parked the
    other 1050px — a 78.6% empty screen (L2-06);
  * at the DEFAULT 447px quadrant the Corners table wanted 501px, so "Grip (est)" started at x=422
    and none of its 12 cells rendered a readable value (L3-03), and the 609px the map's own
    "Add sector" button creates put S2 and S3 at ZERO visible pixels (L3-04).

So the columns are now FITTED to the panel: content-tight is the starting point, not the answer.
These pin that contract AND the wiring it has to leave alone:
  * spare width is shared across the data columns, each capped at MAX_DATA_COL_PX (< the 300px that
    was P5's own complaint), and the spacer still takes what is left over;
  * a short panel gives slack back down to each column's CELL width — headers elide (with their
    full text on a tooltip), values never do;
  * the Lap column's fixed width holds the widest decorated label ("▶ ★ 100 ⚠") uncut and is never
    squeezed;
  * the spacer stays LAST as the dynamic S-split columns come and go, never holds a cell, and
    never breaks the Entry-header unit flip;
  * a programmatic select() scrolls its row into view (IA-02);
  * PROVISIONAL timing demotes every start-line-derived column, a DEGRADED clock only the
    durations (L3-02);
  * sorting is untouched: both directions on the numeric keys, a click on the blank spacer header
    is refused, and an S-column sort that outlives its sector lines falls back to lap order.

Pure Qt on a fake session (no pacer, no telemetry file). Run: python tests/test_lap_table_columns.py
"""
import os
import sys
from types import SimpleNamespace

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _qtapp import themed_app  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QHeaderView  # noqa: E402

# 18 geometry assertions, every one a function of the FONT — including the Δbest/Δapex/Δref elide
# guard #163 and #171 rest on, which compares glyph advances 1-3 px apart. Unthemed this file
# measured Qt's default stack: column 3 came out 88 px where the shipped table gives 58 (-34 %),
# columns 4/5 62 px where the app gives 77/78. See tests/_qtapp.py; W10-05.
_APP = themed_app()

from studio import data_quality, units  # noqa: E402
from studio.lap_table import (  # noqa: E402
    _ENTRY_COL,
    BEST_LAP_MARK,
    COLUMNS,
    CURRENT_PREFIX,
    DROPOUT_SUFFIX,
    LAP_COL_PX,
    MAX_DATA_COL_PX,
    MIN_SECTION_PX,
    PROVISIONAL_TOOLTIP,
    LapTable,
    fit_columns,
)


class _FakeSession:
    """The read surface LapTable.refresh() touches: 3 laps (times 70.0 / 68.4 / 71.2, lap 1 the
    best) and 1 sector line -> 2 S-columns. The sector geometry is mutable so a test can add or
    remove sector lines and refresh(), the way a start/finish-line edit does."""

    timing_verified = True
    timing_quality = data_quality.TimingQuality()

    def __init__(self):
        self.splits = {0: [33.8, 36.2], 1: [34.0, 34.4], 2: [35.5, 35.7]}
        self.n_sectors = 1
        self.n_laps = 3   # raise it for the scroll test; laps past the 3rd repeat lap 2's numbers

    def set_sectors(self, n, splits):
        """Simulate a sector-line edit: n lines -> n+1 S-columns, with the given per-lap splits."""
        self.n_sectors = n
        self.splits = splits

    def lap_rows(self):
        base = [{"idx": 0, "time": 70.0, "dist": 1001.0, "entry": 51.0},
                {"idx": 1, "time": 68.4, "dist": 998.0, "entry": 52.5},
                {"idx": 2, "time": 71.2, "dist": 1003.0, "entry": 49.0}]
        return base + [{"idx": i, "time": 70.0 + i * 0.01, "dist": 1000.0, "entry": 50.0}
                       for i in range(3, self.n_laps)]

    def sector_count(self):
        return self.n_sectors

    def lap_sector_splits(self, lap_id):
        return self.splits.get(lap_id, self.splits[2])

    def session_best_splits(self):
        n = len(self.splits[0])
        return [min(sp[i] for sp in self.splits.values()) for i in range(n)]

    def theoretical_best(self):
        return 68.2

    def best_rolling_lap(self):
        return 68.3

    def best_lap_id(self):
        return 1

    def dropout_lap_ids(self):
        return set()


def _sized(table, w, h=240):
    """Show + resize the table, let Qt lay the header out, then read the column widths."""
    table.resize(w, h)
    table.show()
    _APP.processEvents()
    tb = table.table
    return [tb.columnWidth(c) for c in range(tb.columnCount())]


def test_fit_columns_grows_capped_and_shrinks_to_the_floors():
    """The fit itself, as arithmetic. Spare width is shared and capped; a short panel gives slack
    back down to the floors and no further (the scrollbar owns whatever is still missing); an exact
    fit is left alone."""
    nat, flo, cap = [50, 50, 50], [30, 30, 30], [80, 80, 80]
    assert fit_columns(nat, flo, cap, 150) == [50, 50, 50], "an exact fit must not move"
    assert sum(fit_columns(nat, flo, cap, 210)) == 210, "spare width is shared, not parked"
    # Capped: 300px of room, but 3x80 is all these columns may take.
    assert fit_columns(nat, flo, cap, 300) == [80, 80, 80]
    # Squeezed to exactly the panel, never past a floor.
    got = fit_columns(nat, flo, cap, 120)
    assert sum(got) == 120 and all(g >= f for g, f in zip(got, flo, strict=True)), got
    # Below the floors' total the fit stops AT the floors (the h-scrollbar covers the rest) —
    # it must never clip a value to make an impossible width work.
    assert fit_columns(nat, flo, cap, 40) == [30, 30, 30]
    # Slack-proportional: the column with the most padding gives the most back.
    got = fit_columns([100, 50], [20, 45], [200, 200], 130)
    assert got[0] < got[1] + 60 and got[0] >= 20 and got[1] >= 45 and sum(got) == 130, got
    print("test_fit_columns_grows_capped_and_shrinks_to_the_floors OK")


def test_columns_fill_a_wide_panel_instead_of_the_spacer():
    """L2-06: content-tight columns left a MAXIMIZED lap panel 78.6% empty — 382px of data and
    1050px parked in the unlabelled spacer. Widening the panel must now widen the DATA, capped so
    P5's own defect (one column past 300px, values pinned across a dead band) cannot come back, with
    the spacer taking only the leftover."""
    table = LapTable(_FakeSession())          # 1 sector line -> 2 S-columns
    tb, hdr = table.table, table.table.horizontalHeader()
    real = len(COLUMNS) + 2                   # the data columns (base + S1/S2)
    assert tb.columnCount() == real + 1, "one blank spacer column past the data columns"
    assert tb.horizontalHeaderItem(real).text() == "", "the spacer header must be blank"
    assert not hdr.stretchLastSection(), "the spacer, not a stretched data column, eats the slack"
    # Every section is Interactive: a ResizeToContents section refuses an explicit width, and a
    # Stretch spacer falls back to its own 40-50px size hint exactly when the data already overflows.
    for c in range(real + 1):
        assert hdr.sectionResizeMode(c) == QHeaderView.Interactive, c

    narrow = _sized(table, 560)
    wide = _sized(table, 900)      # measured LAST: the assertions below read the live header
    assert sum(wide[:real]) > sum(narrow[:real]) + 100, \
        f"the DATA must take the extra width, not the spacer: {wide} {narrow}"
    assert max(wide[:real]) <= MAX_DATA_COL_PX < 300, \
        f"no column may balloon back past P5's 300px: {wide}"
    # The data columns are ADJACENT + left-packed: they tile from x=0 with no gap, and the spacer
    # holds exactly what the caps left over.
    assert sum(wide[:real]) == hdr.sectionPosition(real), wide
    assert not tb.horizontalScrollBar().isVisible(), "a wide panel must not scroll horizontally"
    # A panel wide enough for every cap is filled to the pixel by columns + spacer.
    huge = _sized(table, 2400)
    assert all(w == MAX_DATA_COL_PX for w in huge[:real]), huge
    assert sum(huge) == tb.viewport().width(), (huge, tb.viewport().width())
    print("test_columns_fill_a_wide_panel_instead_of_the_spacer OK")


def test_a_short_panel_squeezes_headers_not_values():
    """L3-03/L3-04: a panel too short for the natural widths must give the slack back rather than
    push whole columns off the viewport. Values keep their full width; headers elide (ElideRight,
    never Qt's default centre-CLIP) and carry their full text as a tooltip."""
    table = LapTable(_FakeSession())
    tb, hdr = table.table, table.table.horizontalHeader()
    real = table._n_real_cols()
    assert hdr.textElideMode() == Qt.ElideRight, "a squeezed header must elide, not centre-clip"
    for c in range(real):
        assert tb.horizontalHeaderItem(c).toolTip(), f"column {c} header has no tooltip to fall back on"

    _sized(table, 900)
    natural, _floors, _caps = table._column_budget()       # header+values, before any fitting
    overhead = table.width() - tb.viewport().width()       # frame + any vertical scrollbar
    got = _sized(table, sum(natural) - 60 + MIN_SECTION_PX + overhead)[:real]
    assert sum(got) < sum(natural), (got, natural)
    # Every column still on screen, and every VALUE still fits its column.
    assert sum(got) <= tb.viewport().width(), (got, tb.viewport().width())
    for c in range(1, real):
        assert got[c] >= tb.sizeHintForColumn(c), f"column {c} squeezed below its own values: {got}"
    # The Lap column is the row identity — never squeezed.
    assert got[0] == LAP_COL_PX, got
    print("test_a_short_panel_squeezes_headers_not_values OK")


def test_lap_column_fits_its_markers():
    """The Lap column is Interactive (a content-sized one would jitter as the ▶ marker moves with
    playback), so its fixed width must hold the widest decorated label — '▶ ★ 100 ⚠' — uncut."""
    table = LapTable(_FakeSession())
    widest = f"{CURRENT_PREFIX}{BEST_LAP_MARK}100{DROPOUT_SUFFIX}"
    need = QFontMetrics(table.table.font()).horizontalAdvance(widest)
    assert LAP_COL_PX >= need + 10, f"Lap column {LAP_COL_PX}px clips {widest!r} ({need}px)"
    # Held at the fixed width on any panel narrow enough that there is nothing to share out.
    _sized(table, 420)
    assert table.table.columnWidth(0) == LAP_COL_PX
    print("test_lap_column_fits_its_markers OK")


def test_selecting_a_lap_scrolls_it_into_view():
    """IA-02: the app pre-selects the best lap at launch and draws four panels from it, but never
    scrolled the row into view — on a 21-lap session it sat 150px BELOW the viewport at every window
    size, scrollbar still at 0, so the panel that OWNS the selection painted no highlighted row.
    A programmatic select() must land its row inside the viewport at any height."""
    sess = _FakeSession()
    sess.n_laps = 40
    table = LapTable(sess)
    tb = table.table
    for h in (140, 220, 320):
        _sized(table, 560, h)
        table.select([37])                              # a lap far down the grid
        _APP.processEvents()
        row = sorted({i.row() for i in tb.selectionModel().selectedRows()})[0]
        rect = tb.visualRect(tb.model().index(row, 0))
        assert tb.viewport().rect().contains(rect), \
            f"selected row {row} is outside the {tb.viewport().height()}px viewport: {rect}"
    # ... and an already-visible row is left alone, so a re-fit never yanks a table the user has
    # scrolled deliberately.
    table.select([37])
    before = tb.verticalScrollBar().value()
    table.select([37])
    assert tb.verticalScrollBar().value() == before
    print("test_selecting_a_lap_scrolls_it_into_view OK")


def test_provisional_demotes_every_start_line_column():
    """L3-02: PROVISIONAL timing used to grey out the Time column ALONE, leaving Dist and Entry at
    full confidence — although both come from the same unverified start line (Dist is the distance
    BETWEEN crossings, Entry the speed AT one). A DEGRADED CLOCK is the other axis and must stay
    narrow: it demotes the durations only, never an odometer distance or a GPS speed sample."""
    sess = _FakeSession()
    table = LapTable(sess)                              # 1 sector line -> S1/S2
    tb = table.table
    real = table._n_real_cols()
    splits = {len(COLUMNS), len(COLUMNS) + 1}

    assert table._start_line_cols() == set(range(1, real)), table._start_line_cols()
    assert table._clock_cols() == {1, *splits}, table._clock_cols()

    sess.timing_verified = False
    table.refresh()
    for c in range(1, real):                            # Time, Dist, Entry, S1, S2 — all demoted
        it = tb.item(0, c)
        assert it.font().italic() and it.toolTip() == PROVISIONAL_TOOLTIP, \
            f"column {c} ({tb.horizontalHeaderItem(c).text()}) not demoted under provisional timing"
    assert not tb.item(0, 0).font().italic(), "the Lap NUMBER does not move with the start line"

    # The degraded-clock axis stays narrow: durations only.
    sess.timing_verified = True
    sess.timing_quality = data_quality.TimingQuality(clock=data_quality.MEDIA_CLOCK_FALLBACK)
    table.refresh()
    for c in (1, *splits):
        assert tb.item(0, c).font().italic(), f"duration column {c} not demoted by a degraded clock"
    for c in (2, _ENTRY_COL):
        assert not tb.item(0, c).font().italic(), \
            f"column {c} must not be demoted by a CLOCK concern — it is not a duration"
    print("test_provisional_demotes_every_start_line_column OK")


def test_spacer_stays_last_as_sector_columns_come_and_go():
    """The S-split columns are dynamic, so the spacer must stay the LAST column as they appear and
    vanish — and never hold a cell (the data lives only in the real columns; a shrinking column
    count must not strand an old S-value under the blank header). A unit flip re-labels Entry
    without disturbing the layout."""
    sess = _FakeSession()
    table = LapTable(sess)
    tb = table.table

    def _check(n_splits):
        real = len(COLUMNS) + n_splits
        assert table._n_real_cols() == real
        assert tb.columnCount() == real + 1, (n_splits, tb.columnCount())
        assert tb.horizontalHeaderItem(real).text() == ""
        # the spacer holds no cells, on any row ...
        assert all(tb.item(r, real) is None for r in range(tb.rowCount()))
        # ... while every data cell is present (the len(COLUMNS) offsets elsewhere still hold).
        assert all(tb.item(r, c) is not None
                   for r in range(tb.rowCount()) for c in range(real))

    _check(2)
    # Add a second sector line -> 3 S-columns; the spacer shifts right with them.
    sess.set_sectors(2, {0: [20.0, 25.0, 25.0], 1: [21.0, 24.0, 23.4], 2: [22.0, 25.5, 23.7]})
    table.refresh()
    _check(3)
    # Remove every sector line -> back to the 4 base columns, spacer still last.
    sess.set_sectors(0, {0: [], 1: [], 2: []})
    table.refresh()
    _check(0)
    # A unit flip re-labels the Entry header in place (it is no longer the last column).
    table.set_speed_unit(units.MPH)
    assert tb.horizontalHeaderItem(_ENTRY_COL).text() == "Entry (mph)"
    _check(0)
    table.set_speed_unit(units.KMH)
    assert tb.horizontalHeaderItem(_ENTRY_COL).text() == "Entry (km/h)"
    print("test_spacer_stays_last_as_sector_columns_come_and_go OK")


class _FakeCornerSession:
    """The read surface CornerTable.refresh() touches — 12 corners on one lap, the shape that put
    501px of columns in a 447px quadrant."""

    def __init__(self, n=12, reference=None):
        cl = [SimpleNamespace(label=f"C{i + 1}", direction=1 if i % 2 else -1, cid=i)
              for i in range(n)]
        st = [SimpleNamespace(time=2.75 + i * 0.1, delta=-0.12, apex_speed=44.9 + i,
                              apex_speed_delta=0.4, entry_speed=45.7 + i, exit_speed=47.9 + i)
              for i in range(n)]
        self.corners = SimpleNamespace(corner_list=lambda: cl,
                                       lap_corner_stats=lambda lap: st if lap == 0 else [],
                                       corner_session_bests=lambda: [s.time for s in st])
        self.driving = SimpleNamespace(lap_corner_grip=lambda lap: [0.77] * n)
        self._reference = reference   # the cross-recording reference's label, or None

    def lap_count(self):
        return 1

    def has_reference(self):
        return self._reference is not None

    def reference_label(self):
        return self._reference


def test_corner_columns_fit_the_default_quadrant():
    """L3-03: at the DEFAULT 447px quadrant the 8 corner columns wanted 501px, so "Grip (est)"
    started at x=422 — 25 of its 79px on screen and 0 of 12 grip cells readable, behind a
    horizontal scrollbar whose handle sits at 1.55:1 contrast. Every column must now fit."""
    from studio.lap_table import CORNER_COLUMNS, CornerTable
    table = CornerTable(_FakeCornerSession())
    table.set_lap(0)
    tb = table.table
    table.resize(600, 320)
    table.show()
    _APP.processEvents()
    natural, _floors, _caps = table._column_budget()
    overhead = table.width() - tb.viewport().width()
    # The DEFECT width (40px short of what the columns naturally want — the real shape was 501px of
    # columns in a 447px viewport), plus a comfortable one and a maximized one.
    for w in (sum(natural) - 40 + overhead, sum(natural) + 60 + overhead, 1100):
        table.resize(w, 320)
        _APP.processEvents()
        vp = tb.viewport().width()
        widths = [tb.columnWidth(c) for c in range(tb.columnCount())]
        assert len(widths) == len(CORNER_COLUMNS)
        assert sum(widths) <= vp, f"{sum(widths)}px of columns in a {vp}px viewport: {widths}"
        # The last column is the one that fell off — it must be wholly on screen, wide enough for
        # its own values (headers may elide to their tooltips; a number may not).
        last = len(widths) - 1
        assert tb.columnViewportPosition(last) + widths[last] <= vp, widths
        assert widths[last] >= tb.sizeHintForColumn(last), widths
    print("test_corner_columns_fit_the_default_quadrant OK")


def test_the_two_delta_headers_never_elide_to_the_same_string():
    """W6-01: this budget deliberately lets a squeezed header elide into its tooltip — sound for a
    label nothing can be confused with, which is why "Grip (est)" is the sacrifice it intends. It
    was NOT sound for "Δ best" and "Δ apex": Qt elides the TAIL, so the words that told them apart
    were the first thing destroyed. At 1280×800 — below the 1440×900 the budget was tuned at —
    both painted "Δ …": a column of SECONDS and a column of KM/H rendered as one string,
    separable only by a hover tooltip a keyboard user has no route to.

    The guard is on the labels across every width, not at one window size, because a size guard
    only covers the sizes someone thought to test — and 1280×800 is precisely the size nobody
    re-checked when the budget was tuned.

    Scope, stated honestly. Two limits are deliberate, not oversights:

      * Below ~19 px only ONE glyph fits, so "Δbest"/"Δapex" both paint "Δ…" — as would any pair
        sharing a first character. The check therefore applies wherever either header still keeps
        two characters, which is every width at which a header conveys anything at all.
      * "Entry" and "Exit" collide at 18 px, both painting "E…", and that is left alone: they
        carry the SAME quantity in the same unit (a speed), so confusing them costs a reader far
        less than confusing seconds with km/h.

    Neither is fixable by widening the budget, and neither needs to be. At 1280 the eight columns
    sum to the viewport exactly; rendering both Δ labels in FULL there would cost 26 px the table
    does not have, and every floor left to take it from is a CELL width, which this table never
    clips. The fix is not "never elide" — it is "never elide two different quantities into the
    same string", which is what this asserts.
    """
    from studio.lap_table import CORNER_COLUMNS, CORNER_DELTA_REF_HEADER, CornerTable
    table = CornerTable(_FakeCornerSession())          # keep the ref: Qt deletes a temporary
    fm = QFontMetrics(table.table.horizontalHeader().font())
    # BOTH spellings of column 2: "Δbest" normally, "Δref" while a cross-recording reference is the
    # Δ baseline (QA-W2R-03). The rename must not re-open what this guard closed.
    for a_label in (CORNER_COLUMNS[2], CORNER_DELTA_REF_HEADER):
        b_label = CORNER_COLUMNS[4]
        assert a_label != b_label, (a_label, b_label)
        for w in range(0, max(fm.horizontalAdvance(a_label), fm.horizontalAdvance(b_label)) + 8):
            a = fm.elidedText(a_label, Qt.ElideRight, w)
            b = fm.elidedText(b_label, Qt.ElideRight, w)
            if max(len(a.rstrip("…")), len(b.rstrip("…"))) < 2:
                continue    # a single glyph is all that fits at this width, for any label
            assert a != b, (f'at {w}px "{a_label}" and "{b_label}" both paint "{a}" — a seconds '
                            f"column and a km/h column rendered as one string")
    # ...and the renamed header never costs the seven columns beside it width they do not have.
    assert (fm.horizontalAdvance(CORNER_DELTA_REF_HEADER)
            <= fm.horizontalAdvance(CORNER_COLUMNS[2])), (
        f"{CORNER_DELTA_REF_HEADER!r} is wider than the {CORNER_COLUMNS[2]!r} it replaces "
        f"({fm.horizontalAdvance(CORNER_DELTA_REF_HEADER)}px vs "
        f"{fm.horizontalAdvance(CORNER_COLUMNS[2])}px) in a table that fits exactly at 1280")
    print("test_the_two_delta_headers_never_elide_to_the_same_string OK")


def _painted(table, fm, col):
    """What column `col`'s header ACTUALLY paints at its current section width — the label box the
    style hands the text, elided by the same metrics the header uses. Not the section width: the
    section also pays for the QSS padding and the sort indicator (see _header_pad_px)."""
    from PySide6.QtCore import QRect
    from PySide6.QtWidgets import QStyle, QStyleOptionHeader

    hdr = table.horizontalHeader()
    opt = QStyleOptionHeader()
    hdr.initStyleOption(opt)
    opt.section = col
    opt.rect = QRect(0, 0, hdr.sectionSize(col), max(hdr.height(), 1))
    box = hdr.style().subElementRect(QStyle.SE_HeaderLabel, opt, hdr).width()
    item = table.horizontalHeaderItem(col)
    return fm.elidedText(item.text() if item is not None else "", Qt.ElideRight, box)


def _stem_of(shown):
    """The header's OWN glyphs in what it painted — the ellipsis is not one of them."""
    return shown[:-1] if shown.endswith("…") else shown


def _keeps_stem(fm, text, px, chars=2):
    """Does `text` elided into a `px` label box still show `chars` of its own glyphs?

    Spelled out here rather than imported from the module under test, for the reason this file
    already spells HEADER_STEM_CHARS out: a guard that asks the code under test what the right
    answer is agrees with it by construction, and on main these assertions have to fail with a
    measurement, not with an ImportError."""
    return len(_stem_of(fm.elidedText(text, Qt.ElideRight, px))) >= min(chars, len(text))


def test_header_stem_px_is_the_narrowest_width_that_keeps_the_stem():
    """W14-02, the arithmetic. `header_stem_px` names a width and `header_floors` spends real
    pixels buying it, so the number has to be TRUE against the elide it predicts — and it was one
    pixel short of it.

    Its docstring claimed the width at which the k-th glyph survives "is exactly the advance of
    text[:k] + '…' — no search, and exact against the same metrics Qt will elide with". Qt's
    ElideRight keeps the last prefix whose width is strictly LESS than (box - ellipsis), so that
    advance is the widest box at which the k-th glyph is LOST. On main this test fails on the first
    real header it checks: header_stem_px(fm, 'Δbest') returns 28 and elidedText at 28 px is 'Δ…',
    one glyph, not two.

    Checked in BOTH directions and on every header the two tables ship, because a floor that is a
    pixel too generous is a pixel taken off a neighbouring column for nothing:

      * at the returned width the stem is there, and
      * at one pixel less it is not (unless the answer is already 1 px, i.e. the label is a single
        glyph the box can always hold).

    Then the whole neighbourhood is swept anyway — every width from 1 px to a few past the full
    advance — asserting the elide is MONOTONE and that the returned width is exactly the threshold
    it crosses. That is the property the search inside header_stem_px relies on, so it is checked
    rather than assumed."""
    from studio.lap_table import (
        CORNER_COLUMNS,
        CORNER_DELTA_REF_HEADER,
        HEADER_STEM_CHARS,
        CornerTable,
        _columns,
        header_stem_px,
    )
    table = CornerTable(_FakeCornerSession())          # keep the ref: Qt deletes a temporary
    fm = QFontMetrics(table.table.horizontalHeader().font())
    # Every header either table ships, in BOTH speed units — the Entry column renames itself.
    labels = [*CORNER_COLUMNS, CORNER_DELTA_REF_HEADER,
              *_columns("kmh"), *_columns("mph"), *(f"S{i}" for i in range(1, 5))]
    checked = 0
    for text in sorted({s for s in labels if s}):
        px = header_stem_px(fm, text)
        want = min(HEADER_STEM_CHARS, len(text))
        shown = fm.elidedText(text, Qt.ElideRight, px)
        assert len(_stem_of(shown)) >= want, (
            f"header_stem_px says {text!r} keeps {want} of its own glyphs in a {px}px label box, "
            f"but Qt elides it to {shown!r} — the width the column budget buys is short")
        if px > 1:
            below = fm.elidedText(text, Qt.ElideRight, px - 1)
            assert len(_stem_of(below)) < want, (
                f"header_stem_px({text!r}) = {px}px is not the NARROWEST such width: {px - 1}px "
                f"already paints {below!r} — every pixel over the threshold is taken from a "
                f"neighbouring column for nothing")
        # ...and the threshold is a threshold: monotone, with the answer exactly at the crossing.
        for w in range(0, fm.horizontalAdvance(text) + 6):
            assert _keeps_stem(fm, text, w, HEADER_STEM_CHARS) == (w >= px), (
                f"{text!r} at {w}px: elide is {fm.elidedText(text, Qt.ElideRight, w)!r} but the "
                f"stem width is {px}px — the keeps/loses boundary is not where it is claimed")
        checked += 1
    assert checked >= 10, f"only {checked} labels swept — the header sets moved"
    print(f"test_header_stem_px_is_the_narrowest_width_that_keeps_the_stem OK ({checked} labels)")


def test_the_corner_header_stems_survive_the_whole_width_band():
    """W14-02, the budget. A one-size check is what let a one-pixel error through, so this sweeps
    EVERY viewport width the corner table can be given across the app's whole window range — the
    real panel hands this table 392 px at the app's 973 px minimum and 499 px at 1440, and the
    sweep below covers 340-560 px, a superset of both ends.

    THREE THINGS ARE PINNED, and each of the first two failed on main:

      1. WHEN EVERY STEM FITS, EVERY HEADER KEEPS ITS STEM. The raised floors were asked for, the
         columns were granted exactly them, and "Δbest" still painted a bare "Δ…" beside a km/h
         column reading "Δa…" — a seconds column with no information in its header, over a 30 px
         band of window width that included 1200x800. That is header_stem_px's missing pixel
         reaching the pixels.
      2. WHEN THE COLUMNS DO NOT FIT AT ALL, EVERY HEADER STILL KEEPS ITS STEM. The yield was
         all-or-nothing: one column the viewport could not afford dropped the stems of all eight,
         which is how the app's own MINIMUM window size painted a seconds column and a km/h column
         as the same bare "…" — with a horizontal scrollbar already up, so the yield was buying
         nothing. It is graded now (header_floors case 2).
      3. AND THE BAND WHERE TWO HEADERS ARE ONE STRING IS SMALL. Between the two regimes there is
         genuinely not room for every stem, and header_floors says so out loud rather than
         pretending: the pixels go to the ambiguous columns first, and what is left over is a
         narrow band. Measured on the shipped corner table over the app's full window range it is
         22 px of 468, where it was 227 — so this asserts a tenth of the band, which passes with
         better than 2x of headroom and fails on main by a factor of five."""
    from studio.lap_table import CORNER_COLUMNS, CornerTable, _header_pad_px, header_stem_px

    table = CornerTable(_FakeCornerSession())
    table.set_lap(0)
    tb = table.table
    hdr = tb.horizontalHeader()
    fm = QFontMetrics(hdr.font())
    table.resize(600, 320)
    table.show()
    _APP.processEvents()
    overhead = table.width() - tb.viewport().width()
    pad = _header_pad_px(hdr)
    # What each header ASKS FOR — the number header_floors spends pixels on. Restated here from
    # the two published parts rather than read back off header_floors, because header_floors now
    # returns what it could AFFORD: asking it what it wanted would be asking the answer to grade
    # itself, and a test that does that agrees with the code by construction.
    asks = [header_stem_px(fm, L) + pad for L in CORNER_COLUMNS]

    short, twins, ambiguous, swept = [], [], 0, 0
    for vp_want in range(340, 561):
        table.resize(vp_want + overhead, 320)
        _APP.processEvents()
        vp = tb.viewport().width()
        natural, floors, _caps = table._column_budget()
        sections = [hdr.sectionSize(c) for c in range(tb.columnCount())]
        painted = [_painted(tb, fm, c) for c in range(tb.columnCount())]
        # Case 1 (every ask is affordable) or case 2 (the columns overflow the viewport, so the
        # scrollbar this budget yields to avoid is up whatever it does): every header is promised
        # its own glyphs. In between, header_floors buys what the slack covers and says so.
        wants = [min(max(floors[c], asks[c]), natural[c]) for c in range(len(floors))]
        guaranteed = sum(wants) <= vp or sum(sections) > vp
        for c, shown in enumerate(painted):
            label, stem = CORNER_COLUMNS[c], _stem_of(painted[c])
            if len(stem) >= min(2, len(label)):
                continue
            where = f"{vp}px viewport, col {c} {label!r} paints {shown!r} in a {sections[c]}px "
            # THE ARITHMETIC, per column and at every width: a section at least as wide as the
            # header asked for must show the stem it asked for. This is the one-pixel defect,
            # and it does not need a case analysis to catch.
            if sections[c] >= asks[c]:
                short.append(where + f"section — it asked for {asks[c]}px and got them")
            elif guaranteed:
                short.append(where + "section, at a width the budget guarantees")
        if painted[2] == painted[4]:
            ambiguous += 1
            if guaranteed:
                twins.append(f"{vp}px viewport: {CORNER_COLUMNS[2]!r} and {CORNER_COLUMNS[4]!r} "
                             f"both paint {painted[2]!r}")
        swept += 1

    assert not short, (
        "a corner header elided away the column it names:\n  "
        + "\n  ".join(short[:12]) + (f"\n  ...and {len(short) - 12} more" if len(short) > 12
                                     else ""))
    assert not twins, ("a seconds column and a km/h column painted one string:\n  "
                       + "\n  ".join(twins[:12]))
    assert ambiguous <= swept // 10, (
        f"the two Δ headers are the same string at {ambiguous} of {swept} swept viewport widths "
        f"— the graded yield is meant to hold this under {swept // 10}")
    print(f"test_the_corner_header_stems_survive_the_whole_width_band OK "
          f"({swept} viewport widths; {ambiguous} with the Δ pair still ambiguous)")


def test_the_corner_delta_column_names_the_baseline_it_measures():
    """QA-W2R-03. With a cross-recording reference loaded, EVERY Δ in this column is measured
    against another recording's lap — "Δbest" is then simply false, and it was the only baseline
    word on the Corners tab. The header must follow the live baseline in both directions, and
    because the header is an abbreviation either way, the recording it abbreviates has to be
    somewhere a reader can reach: the column's own tooltip."""
    from studio.lap_table import (
        CORNER_COLUMNS,
        CORNER_DELTA_COL,
        CORNER_DELTA_REF_HEADER,
        CornerTable,
    )

    sess = _FakeCornerSession(reference="recording 0059 · 3 chapters")
    table = CornerTable(sess)
    table.set_lap(0)
    hdr = table.table.horizontalHeaderItem
    assert hdr(CORNER_DELTA_COL).text() == CORNER_DELTA_REF_HEADER, hdr(CORNER_DELTA_COL).text()
    for col in (CORNER_DELTA_COL, 4):          # the Δ time column and the Δ apex-speed column
        assert "recording 0059 · 3 chapters" in hdr(col).toolTip(), (col, hdr(col).toolTip())
    # Clearing the reference puts the local wording back — the same table, no rebuild.
    sess._reference = None
    table.refresh()
    assert hdr(CORNER_DELTA_COL).text() == CORNER_COLUMNS[CORNER_DELTA_COL]
    assert "the best lap" in hdr(CORNER_DELTA_COL).toolTip(), hdr(CORNER_DELTA_COL).toolTip()
    print("test_the_corner_delta_column_names_the_baseline_it_measures OK")


def test_spacer_column_is_not_sortable():
    """Sorting is untouched by the spacer: the data columns still sort both ways on their numeric
    keys, a click on the BLANK spacer header can't order anything so it bounces the indicator back
    to the live sort column, and an S-column sort that outlives its sector lines falls back to lap
    order (never to the spacer)."""
    sess = _FakeSession()                # laps 0/1/2 with times 70.0 / 68.4 / 71.2
    table = LapTable(sess)
    tb, hdr = table.table, table.table.horizontalHeader()

    def _order():
        return [table._lap_id(r) for r in range(tb.rowCount())]

    tb.sortByColumn(1, Qt.AscendingOrder)
    assert _order() == [1, 0, 2], _order()
    tb.sortByColumn(1, Qt.DescendingOrder)
    assert _order() == [2, 0, 1], _order()
    assert (table._sort_col, table._sort_order) == (1, Qt.DescendingOrder)

    # A click on the blank spacer header (which is what setSortIndicator models) is refused: the
    # indicator returns to the live sort column and the row order is untouched.
    hdr.setSortIndicator(tb.columnCount() - 1, Qt.AscendingOrder)
    assert hdr.sortIndicatorSection() == 1, hdr.sortIndicatorSection()
    assert (table._sort_col, table._sort_order) == (1, Qt.DescendingOrder)
    assert _order() == [2, 0, 1], _order()

    # Sorting on an S-column whose sector line is then removed falls back to lap order — the
    # remembered column must never end up pointing at the (blank) spacer.
    tb.sortByColumn(len(COLUMNS) + 1, Qt.AscendingOrder)   # S2: 34.4 / 35.7 / 36.2 -> laps 1,2,0
    assert _order() == [1, 2, 0], _order()
    sess.set_sectors(0, {0: [], 1: [], 2: []})
    table.refresh()
    assert table._sort_col == 0 and table._sort_order == Qt.AscendingOrder
    assert _order() == [0, 1, 2], _order()
    print("test_spacer_column_is_not_sortable OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LAP-TABLE COLUMN TESTS PASSED", flush=True)
