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
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QFontMetrics  # noqa: E402
from PySide6.QtWidgets import QApplication, QHeaderView  # noqa: E402

_APP = QApplication.instance() or QApplication([])

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

    def __init__(self, n=12):
        cl = [SimpleNamespace(label=f"C{i + 1}", direction=1 if i % 2 else -1, cid=i)
              for i in range(n)]
        st = [SimpleNamespace(time=2.75 + i * 0.1, delta=-0.12, apex_speed=44.9 + i,
                              apex_speed_delta=0.4, entry_speed=45.7 + i, exit_speed=47.9 + i)
              for i in range(n)]
        self.corners = SimpleNamespace(corner_list=lambda: cl,
                                       lap_corner_stats=lambda lap: st if lap == 0 else [],
                                       corner_session_bests=lambda: [s.time for s in st])
        self.driving = SimpleNamespace(lap_corner_grip=lambda lap: [0.77] * n)

    def lap_count(self):
        return 1


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
