"""LapTable COLUMN LAYOUT (P5: the dead width band).

The lap panel used to stretch its LAST column, so on a wide panel the Entry column ballooned past
300px with its values pinned to the far right and a meaningless void in the middle of the table.
The columns are now content-tight and a single blank trailing SPACER column absorbs the slack.

These pin the layout AND the wiring it has to leave alone:
  * content-tight data columns + a stretching blank spacer (widening the panel moves ONLY the
    spacer; every data column stays put and stays wide enough for its own header),
  * the Lap column's fixed width holds the widest decorated label ("▶ ★ 100 ⚠") uncut,
  * the spacer stays LAST as the dynamic S-split columns come and go, never holds a cell, and
    never breaks the Entry-header unit flip,
  * sorting is untouched: both directions on the numeric keys, a click on the blank spacer header
    is refused, and an S-column sort that outlives its sector lines falls back to lap order.

Pure Qt on a fake session (no pacer, no telemetry file). Run: python tests/test_lap_table_columns.py
"""
import os
import sys

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
    MIN_SECTION_PX,
    LapTable,
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

    def set_sectors(self, n, splits):
        """Simulate a sector-line edit: n lines -> n+1 S-columns, with the given per-lap splits."""
        self.n_sectors = n
        self.splits = splits

    def lap_rows(self):
        return [{"idx": 0, "time": 70.0, "dist": 1001.0, "entry": 51.0},
                {"idx": 1, "time": 68.4, "dist": 998.0, "entry": 52.5},
                {"idx": 2, "time": 71.2, "dist": 1003.0, "entry": 49.0}]

    def sector_count(self):
        return self.n_sectors

    def lap_sector_splits(self, lap_id):
        return self.splits[lap_id]

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


def test_columns_are_content_tight_with_a_stretching_spacer():
    """Widening the panel must change ONLY the spacer's width — the data columns keep their content
    size (no 300px Entry column, no dead band), stay adjacent/left-packed, and each is wide enough
    for its own header, so no header is elided or stranded off its column."""
    table = LapTable(_FakeSession())          # 1 sector line -> 2 S-columns
    tb, hdr = table.table, table.table.horizontalHeader()
    real = len(COLUMNS) + 2                   # the data columns (base + S1/S2)
    assert tb.columnCount() == real + 1, "one blank spacer column past the data columns"
    assert tb.horizontalHeaderItem(real).text() == "", "the spacer header must be blank"
    assert not hdr.stretchLastSection(), "the spacer, not a stretched data column, eats the slack"
    assert hdr.sectionResizeMode(0) == QHeaderView.Interactive       # Lap: fixed, marker-proof
    for c in range(1, real):
        assert hdr.sectionResizeMode(c) == QHeaderView.ResizeToContents, c
    assert hdr.sectionResizeMode(real) == QHeaderView.Stretch

    wide = _sized(table, 900)
    narrow = _sized(table, 560)
    assert wide[:real] == narrow[:real], f"data columns must not track panel width: {wide} {narrow}"
    assert wide[real] - narrow[real] > 250, f"the spacer must absorb the extra width: {wide} {narrow}"
    # No dead band: the Entry column stays its own content size instead of ballooning.
    assert wide[_ENTRY_COL] < 160, wide
    # Every data column fits its own header text (nothing elided into "Entry (km…").
    fm = QFontMetrics(hdr.font())
    for c in range(real):
        assert wide[c] >= fm.horizontalAdvance(tb.horizontalHeaderItem(c).text()), \
            f"column {c} narrower than its header"
    # And the data columns are ADJACENT + left-packed: they tile from x=0 with no gap.
    assert sum(wide[:real]) == hdr.sectionPosition(real), wide
    assert not tb.horizontalScrollBar().isVisible(), "a wide panel must not scroll horizontally"
    # The spacer must never be the thing that summons a horizontal scrollbar. Squeeze the panel to
    # 8px more than the data columns need — less than Qt's stock ~17px minimum section size, which
    # would have overflowed the table — and it collapses into the gap instead.
    assert hdr.minimumSectionSize() <= MIN_SECTION_PX
    overhead = table.width() - tb.viewport().width()      # frame + any vertical scrollbar
    tight = _sized(table, sum(narrow[:real]) + overhead + 8)
    assert tight[:real] == narrow[:real], tight
    assert tight[real] <= 8, tight
    assert not tb.horizontalScrollBar().isVisible(), \
        "the collapsing spacer must not force a horizontal scrollbar"
    print("test_columns_are_content_tight_with_a_stretching_spacer OK")


def test_lap_column_fits_its_markers():
    """The Lap column is Interactive (a content-sized one would jitter as the ▶ marker moves with
    playback), so its fixed width must hold the widest decorated label — '▶ ★ 100 ⚠' — uncut."""
    table = LapTable(_FakeSession())
    widest = f"{CURRENT_PREFIX}{BEST_LAP_MARK}100{DROPOUT_SUFFIX}"
    need = QFontMetrics(table.table.font()).horizontalAdvance(widest)
    assert LAP_COL_PX >= need + 10, f"Lap column {LAP_COL_PX}px clips {widest!r} ({need}px)"
    assert table.table.columnWidth(0) == LAP_COL_PX
    print("test_lap_column_fits_its_markers OK")


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
