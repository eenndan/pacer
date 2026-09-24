"""What the Stats page's SECTIONS share — the page shell is `stats_panel.StatsView`.

ARCH-3 (board review 2026-09-23) splits the page one section per module: `stats_trust`,
`stats_braking` and `stats_straights` so far, the rest still inside `stats_panel`. What more than
one section needs lives here, so a section never imports the page it sits on: the report table and
its row height, the section heading, the sortable numeric cell and its blanks-last sort hook, and
the two values two sections must spell the same way (`RING_ROLE`, `NO_GMETER_NOTE`).

A SECTION is a plain object that BUILDS its widgets, REFRESHES them from a session and owns its
copy. It is not a container widget: `widgets()` hands the page its widgets in reading order and the
page adds them straight to its column, so the page's widget tree — and every layout measurement
`stats_panel` carries — is exactly what it was before the split. `tables()` names the report
tables the page's column packer has to be able to ask for a width (`StatsView._group_min_width`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLabel,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
)

from . import theme
from .lap_table import NUM_ROLE, NUMERIC_COL_START, _NumItem, align_headers_over_their_columns

# Every report table's row height. It was a bare 22, documented here as "the consistency-table
# convention" — a convention inherited from the ConsistencyPanel, which PR #111 DELETED, so the
# number outlived its only argument. Three of the five tables below are genuine row click targets
# (SelectRows + SingleSelection + ClickFocus → corner_clicked → the map's apex ring), and 35 of
# their rows therefore shipped two pixels under the pointer-target floor theme.py declares. This is
# that floor, spelled as the token: a report grid may be denser than a control, never denser than
# the floor. See theme.GRID_ROW_DENSE_H for why this is not a new density scale.
ROW_HEIGHT = theme.GRID_ROW_DENSE_H
RING_ROLE = NUM_ROLE + 1   # the map-ring corner cid stored on a straight row's label item
# The absent-accelerometer sentence — used BOTH in the DATA TRUST card and under the SPEED · G
# tiles, so the dashes and the trust card explain themselves in the same words.
NO_GMETER_NOTE = ("g-meter: no accelerometer in this recording — lateral g, braking g and grip "
                  "are unavailable.")


def section_heading(title: str) -> QLabel:
    """A section's heading: the page's one `BarLabel` style, by role."""
    lab = QLabel(title)
    lab.setProperty("role", "BarLabel")
    return lab


def num_item(text: str) -> QTableWidgetItem:
    """A right-aligned numeric cell in the tabular stack (not sortable — see `_NumItem`)."""
    item = QTableWidgetItem(text)
    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
    item.setFont(theme.mono_font(theme.TABLE))
    return item


def keep_blanks_last(_index, order):
    """Keep _NumItem's blanks-last convention through descending sorts (the lap-table
    idiom: the class flag flips before Qt reverses the order). Connected to a sortable report
    table's `sortIndicatorChanged` BEFORE its first `setSortingEnabled(True)`, so it runs first."""
    _NumItem._descending = order == Qt.DescendingOrder


class ReportTable(QTableWidget):
    """A content-sized statistics table that SCROLLS ITSELF when the pane is too narrow for it.

    THE PAGE'S HORIZONTAL SCROLLBAR WAS THIS WIDGET. Each report table pinned itself to the exact
    width of its own columns (`_fit_table`), and the widest of them — PER LAP, nine columns — asks
    for 730 px. In the 503 px quadrant that is the app's default the table's fixed width became the
    scroll body's minimum, so the WHOLE page was laid out 742 px wide and then scrolled sideways
    inside a 503 px viewport: every section heading, every tile row and the DATA TRUST card were
    being wrapped at a width 239 px larger than anything the reader could see. The one widget that
    genuinely did not fit made the eight that did fit stop fitting.

    The honesty rule that put the scrollbar there in the first place still holds — a statistics
    table must never silently clip its rightmost column — so the scrolling is not removed, it is
    MOVED to the widget that actually overflows. The table takes `min(pane, its content)`: at
    dashboard width it is exactly as wide as its columns and reads left-packed as before; in a
    quadrant it takes the pane and grows its own horizontal scrollbar. The page never scrolls
    sideways again, and no column is ever hidden without a bar saying so.

    The HEIGHT has to follow, which is why this is a class and not two more lines in `_fit_table`:
    the outer column owns vertical scrolling, so each table is pinned to its content height — and
    the moment an in-table scrollbar appears it would eat the last row out of that pinned height.
    `_apply_height` re-pays for the bar when it is showing and takes the pixels back when it is
    not, on every resize."""

    def __init__(self, columns: list[str], row_height: int):
        super().__init__(0, len(columns))
        self._row_height = row_height
        self._content_w = 0
        self.setHorizontalHeaderLabels(columns)
        # ...and then give every header the SIDE of the column it labels. Qt's
        # QHeaderView.defaultAlignment is AlignCenter, these five tables never overrode it, and
        # every cell from NUMERIC_COL_START on is AlignRight — so each label floated over the
        # middle of a column whose digits sit at its right edge, by up to 34 px of ink-centre drift
        # on the widest column (BRAKING "Commit %", 108 px, measured on the window composite at
        # 1440x900). The rule is the app's, already written down for the lap / corner / coaching
        # grids; these tables were simply never brought to it, and the guard that exists for
        # exactly this defect (tests/test_design_system.py::test_no_table_header_floats_off_its_data)
        # enumerated four tables and not these five.
        #
        # Applied HERE, in the shared table, rather than at the five call sites, because unlike the
        # lap and corner grids all five build their headers the same way — through this one
        # constructor — so a SIXTH report table cannot arrive without it. The boundary is the same
        # NUMERIC_COL_START the cells use (column 0 is the row's identity: "C7" / "S2" / a lap
        # number, left; everything after it is a number, right), which is what stops a new column
        # arriving with its header and its values disagreeing.
        align_headers_over_their_columns(self, NUMERIC_COL_START)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(row_height)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setAlternatingRowColors(True)
        self.setFocusPolicy(Qt.NoFocus)
        # Vertical scrolling belongs to the outer page (each table is pinned to its content
        # height); horizontal scrolling belongs HERE, and only when the pane is too narrow.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        # Preferred (not Fixed) horizontally: the table may shrink to the pane. Its MAXIMUM is its
        # content width, so a wide pane never stretches it — the left-packed reading is unchanged.
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        # ...and its layout MINIMUM must not be its content: a QTableWidget's minimumSizeHint is
        # generous enough to re-create the very overflow this class exists to remove.
        self.setMinimumWidth(0)

    def fit(self) -> None:
        """Re-measure after a refill: columns to their content, width capped there, height pinned."""
        self.resizeColumnsToContents()
        self._content_w = (sum(self.columnWidth(c) for c in range(self.columnCount()))
                           + 2 * self.frameWidth() + 2)
        self.setMaximumWidth(self._content_w)
        self._apply_height()

    def set_columns(self, columns: list[str]) -> None:
        """Re-label the header for a table whose COLUMN COUNT is a property of the session.

        Only the SPLITS grid needs it (one column per sub-sector, and the user adds and removes
        sector lines live). A no-op when the labels already match, so the ordinary refresh of a
        fixed-column table costs nothing; `align_headers_over_their_columns` is re-applied because
        Qt builds fresh header items and they arrive centred."""
        current = [self.horizontalHeaderItem(c).text() if self.horizontalHeaderItem(c) else ""
                   for c in range(self.columnCount())]
        if current == columns:
            return
        self.setRowCount(0)
        self.setColumnCount(len(columns))
        self.setHorizontalHeaderLabels(columns)
        align_headers_over_their_columns(self, NUMERIC_COL_START)

    def content_width(self) -> int:
        """The width at which this table shows every column — what it would LIKE to be.

        Its layout minimum is deliberately 0 (below) and its maximum is this, so between the two it
        takes whatever the pane gives and scrolls the difference. That is right for a quadrant and
        wrong for a page CHOOSING its own columns: the chooser has to know the number before it
        commits, or it composes a column that hides three of CORNERS' eight columns. `fit()` has
        always computed it; this is the read the page's packer needs (see _group_min_width)."""
        return self._content_w

    def minimumSizeHint(self):
        """Zero-width, full-height. Qt's own hint for a scroll area is wide enough to reserve room
        for content that this table is explicitly willing to scroll instead."""
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def _needs_bar(self) -> bool:
        return self.viewport().width() < self._content_w - 2 * self.frameWidth() - 2

    def _apply_height(self) -> None:
        h = (self.horizontalHeader().height() + self._row_height * self.rowCount()
             + 2 * self.frameWidth())
        if self._needs_bar():
            h += self.horizontalScrollBar().sizeHint().height()
        if h != self.height() or self.minimumHeight() != h:
            self.setFixedHeight(h)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._apply_height()
