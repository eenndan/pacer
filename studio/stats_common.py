"""What the Stats page's SECTIONS share — the page shell is `stats_panel.StatsView`.

ARCH-3 (board review 2026-09-23) splits the page one section per module: `stats_trust`,
`stats_braking`, `stats_straights`, `stats_ideal`, `stats_corners` and `stats_coasting` so far, the
rest still inside `stats_panel`.
What more than one section needs lives here, so a section never imports the page it sits on: the
report table and its row height, the section heading, the sortable numeric cell and its blanks-last
sort hook, the stitched-target tile's timing mute (`set_target_tile`), and the values two
sections must spell the same way (`RING_ROLE`, `NO_GMETER_NOTE`, `_DRIVING_COAST`).

A SECTION is a plain object that BUILDS its widgets, REFRESHES them from a session and owns its
copy. It is not a container widget: `widgets()` hands the page its widgets in reading order and the
page adds them straight to its column, so the page's widget tree — and every layout measurement
`stats_panel` carries — is exactly what it was before the split. A TUPLE in that order is a row of
tiles, which the page places on its own reflowing tile grid (`StatsView._mount`): tile layout is
the page's. `tables()` names the report tables the page's column packer has to be able to ask for
a width (`StatsView._group_min_width`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QFrame,
    QHeaderView,
    QLabel,
    QSizePolicy,
    QTableView,
    QTableWidget,
    QTableWidgetItem,
)

from . import driving, theme
from ._signal import fmt_time
from .lap_table import (
    NUM_ROLE,
    NUMERIC_COL_START,
    PROVISIONAL_TOOLTIP,
    _NumItem,
    align_headers_over_their_columns,
    estimated_timing_tooltip,
)
from .theme import C
from .widgets import Tile

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

# The COAST paragraph of the DRIVING tooltip (`stats_panel`'s ONE AXIS, THREE FILTERS block composes
# it with the brake pieces) — and, word for word, the close of the COASTING section's tooltip: what
# a coast IS, stated once for both.
_DRIVING_COAST = (
    "A COAST is the narrower test — off-power deceleration inside a band from "
    f"{driving.COAST_DRAG_MIN:g} g up to that same threshold, held for at least "
    f"{driving.MIN_COAST_S:g} s. Sustained membership of a band is the opposite shape from an "
    "onset, and that band is narrower than the bare derivative's own noise, so this one figure is "
    f"measured on a {driving.COAST_SMOOTH_S:g} s window. The band, the minimum duration and that "
    "window are the whole instrument — this is time that passed all three tests, not every moment "
    "the driver was off the throttle.")


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


def set_target_tile(tile: Tile, value, tip: str, session, text: str | None = None,
                    caption: str | None = None):
    """Render a stitched TARGET tile (theoretical best / best rolling / the ideal's gap).

    These are not laps anyone drove — they are composed from the session's best splits and
    loops — so they share the lap timing's authority: while the timing is PROVISIONAL (an
    arbitrary start line) OR the clock is DEGRADED (media-clock / low-GPS estimate) the value
    is muted + italic and carries the explaining note, restored to the normal tile once
    Verified AND high-quality. Kept byte-for-byte in spirit with the Laps footer this moved
    from; the measured PACE tiles beside it are unmuted because they ARE laps you drove.

    `text` overrides the m:ss.mmm formatting for a target that is a DIFFERENCE rather than a
    lap time ("-1.49 s"). It is still a synthesized number and still takes the mute — the rule
    is about where the number came from, not about how it is printed.

    `caption` re-labels the tile per refresh, for a target whose SAMPLE belongs on it — the
    ideal's "theoretical best · 65 laps", the same shape the measured `median · 65 clean laps`
    beside it already uses. It goes through this function rather than a bare `tile.set()` after
    it, because a second `set()` re-runs `_claim_ink_height` on a value this function has just
    styled, and the colour-then-font ordering below exists precisely because that path is
    order-sensitive.

    `session` is the page's session: its timing authority (`timing_verified`,
    `timing_quality`) is what mutes the tile. A function here rather than a page method
    because the page's rolling best and the IDEAL LAP section's two tiles are all stitched
    targets (see stats_ideal)."""
    tile.set((text if text is not None else fmt_time(value)) if value is not None else None,
             caption)
    provisional = not getattr(session, "timing_verified", True)
    quality = getattr(session, "timing_quality", None)
    muted = provisional or bool(quality is not None and quality.degraded)
    # COLOUR FIRST, THEN FONT — not cosmetic ordering. setStyleSheet on a NEW string repolishes
    # the label, and the repolish re-resolves its font, dropping the italic bit a setFont set a
    # moment earlier. The other way round the first refresh of a fresh view painted the muted
    # target tile upright, and only a SECOND refresh() made it italic: the app happened to get
    # that second call from CentralView after a load, so the cue shipped — but nothing on the
    # single-refresh paths (a tab switch, a unit flip, a bare StatsView) did. Setting the
    # stylesheet first means the repolish is already spent when the font lands.
    tile.value.setStyleSheet(
        f"color: {theme.PROVISIONAL_COLOR if muted else C.text};")
    font = theme.mono_font(theme.EMPHASIS, theme.W_SEMIBOLD)
    font.setItalic(muted)
    tile.value.setFont(font)
    if not muted:
        tile.setToolTip(tip)
        return
    note = PROVISIONAL_TOOLTIP if provisional else estimated_timing_tooltip(quality)
    tile.setToolTip(f"{note}\n\n{tip}")


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

    def __init__(self, columns: list[str], row_height: int, frozen: int = 0):
        super().__init__(0, len(columns))
        self._row_height = row_height
        self._content_w = 0
        self._frozen: _FrozenLead | None = None
        self.setHorizontalHeaderLabels(columns)
        # A sortable table re-fits when its sort column moves: the column that now carries the
        # arrow is the one that pays for it (see `fit`).
        self.horizontalHeader().sortIndicatorChanged.connect(lambda *_: self.fit())
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
        if frozen > 0:
            self._frozen = _FrozenLead(self, frozen)

    def fit(self) -> None:
        """Re-measure after a refill: columns to their content, width capped there, height pinned.

        ONLY THE SORTED COLUMN PAYS FOR THE SORT ARROW (LOOK-8, QA 2026-09-26). Qt sizes every
        section of a header that shows a sort indicator as if the arrow were in it — 28 px of the
        44 px of chrome each CORNERS header carried, measured on the shipped theme. "Apex best" was
        106 px for a 4-character number, and the eight columns asked 728 px of the owner's 683 px
        pane (599 px at 1280x800), so "Grip (est)" showed as a lone "G" behind a scrollbar. One
        column shows the arrow at a time, so the others are sized without it and the sorted one
        re-fits when the sort moves (the connection in __init__): 565 px, whole at both sizes."""
        hdr = self.horizontalHeader()
        if hdr.isSortIndicatorShown():
            hdr.setSortIndicatorShown(False)
            self.resizeColumnsToContents()
            hdr.setSortIndicatorShown(True)
            if 0 <= hdr.sortIndicatorSection() < self.columnCount():
                self.resizeColumnToContents(hdr.sortIndicatorSection())
        else:
            self.resizeColumnsToContents()
        self._content_w = (sum(self.columnWidth(c) for c in range(self.columnCount()))
                           + 2 * self.frameWidth() + 2)
        self.setMaximumWidth(self._content_w)
        self._apply_height()
        if self._frozen is not None:
            self._frozen.sync()

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
        if self._frozen is not None:
            self._frozen.sync()


class _FrozenLead(QTableView):
    """The first `n` columns of a `ReportTable`, drawn over it so they stay put while it scrolls
    sideways — Qt's frozen-column pattern: a second view on the SAME model, so every cell, font,
    colour and tooltip is the table's own and nothing is filled twice.

    WHY (LOOK-8, QA 2026-09-26). CORNERS BY LAP is a lap, a lap time and one column per corner:
    952 px on MK_18_09 against the owner's 683 px pane, and never fewer than ~850 px for twelve
    corners even with every cell padding cut, so it has to scroll there. Scrolled, C10-C12 and the
    lap time were off-screen together and a row could not be read whole. With the lap id and the
    lap time frozen, any corner column scrolled into view sits beside the lap it belongs to."""

    def __init__(self, table: ReportTable, n: int):
        super().__init__(table)
        self._table, self._n = table, n
        self.setModel(table.model())
        self.setFocusPolicy(Qt.NoFocus)
        self.setSelectionMode(QAbstractItemView.NoSelection)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setFrameShape(QFrame.NoFrame)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(table.verticalHeader().defaultSectionSize())
        self.horizontalHeader().setSectionResizeMode(QHeaderView.Fixed)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.viewport().stackUnder(self)
        table.horizontalHeader().sectionResized.connect(lambda *_: self.sync())

    def sync(self) -> None:
        """Match the table's columns, header height, tooltip and frame, over its left edge."""
        t = self._table
        for c in range(t.columnCount()):
            self.setColumnHidden(c, c >= self._n)
            if c < self._n:
                self.setColumnWidth(c, t.columnWidth(c))
        self.horizontalHeader().setFixedHeight(t.horizontalHeader().height())
        self.setToolTip(t.toolTip())
        f = t.frameWidth()
        width = sum(t.columnWidth(c) for c in range(min(self._n, t.columnCount())))
        self.setGeometry(f, f, width, t.horizontalHeader().height() + t.viewport().height())
        self.raise_()
