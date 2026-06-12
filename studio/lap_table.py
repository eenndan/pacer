"""LapTable: lap times / distances / entry speed. Multi-select rows to compare laps.

Columns are click-to-sort by the UNDERLYING numeric value (F1): each cell carries its
numeric key in Qt.UserRole and a `_NumItem` sorts on that, so "1:08.408" sorts as 68.408 s
and "23.10" as 23.1 — not lexically. Clicking a header toggles asc/desc; the default order
(by lap number) holds until the user clicks.

Row/cell highlights are keyed by LAP ID, not row index, and re-applied after every sort so
they always follow the right lap: the ▶ playing marker + bold (current lap), the green best
lap (F-existing), the blue Qt selection, the PURPLE per-sector session-best cells (F5 —
the fastest split in each S-column across all valid laps, motorsport convention), and a
trailing ⚠ low-confidence marker (+ row tooltip) on laps with a GPS dropout. Colours,
alignment and the tabular numeric font come from the design tokens in `theme`; base row text
is the primary off-white on the dark table surface.

Under the table sit two summary FOOTER rows (F1-roadmap) — "Theoretical best" (sum of the
purple session-best splits) and "Best rolling" (fastest start-anywhere full loop) — plain
labels OUTSIDE the QTableWidget, so they can never participate in sorting/selection and
survive any sort by construction; refresh() rewrites their values (session is the single
source: `theoretical_best` / `best_rolling_lap`).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QGridLayout,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ._signal import fmt_time

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

BASE_COLOR = QColor(theme.C.text)             # primary off-white: default row text (dark surface)
BEST_COLOR = QColor(theme.C.ahead)            # green: the overall best lap (foreground every cell)
BEST_SECTOR_COLOR = QColor(theme.C.best)      # purple: per-column session-best split (F5)
CURRENT_PREFIX = "▶ "  # "▶ " marks the lap currently playing on the video
DROPOUT_SUFFIX = " ⚠"  # low-confidence: this lap has a GPS dropout (time/distance less reliable)
DROPOUT_TOOLTIP = "GPS dropout in this lap — its time, distance and map are less reliable."
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]
# Columns 1.. (everything but the Lap column) hold numerics: right-align + tabular font so the
# digits column-align. The Lap column stays left/default.
NUMERIC_COL_START = 1
NUM_ROLE = Qt.UserRole  # the numeric sort key stored on every cell
LAP_ROLE = Qt.UserRole + 1  # the lap id (stable across sorts), stored on the Lap cell

# The summary footer rows under the table: (title, session accessor name, defining tooltip).
# Both are styled like the purple per-sector session-best (they're composed FROM those bests /
# from start-anywhere windows) and read from Session so the table cells and the footer can
# never disagree. (The per-column session-best itself is `Session.session_best_splits` —
# hoisted there from this module so both consumers share one computation.)
FOOTER_ROWS = (
    ("Theoretical best", "theoretical_best",
     "Sum of the session-best sector splits (the purple cells): the lap you'd drive by "
     "stitching every best sector together. With no sector lines this equals the best lap "
     "time."),
    ("Best rolling", "best_rolling_lap",
     "The fastest single complete loop regardless of where it starts: the minimum time from "
     "passing any track position to passing it again one lap later (windows spanning a "
     "GPS-dropout ⚠ lap are excluded)."),
)


def _is_blank(v) -> bool:
    """A cell key is "blank" when it's absent or NaN (a partial lap with fewer splits)."""
    return v is None or (isinstance(v, float) and math.isnan(v))


class _NumItem(QTableWidgetItem):
    """A table cell that sorts by a numeric key (Qt.UserRole) rather than its display text, so
    e.g. lap times "1:08.408" sort as 68.408 s, and blank cells (partial laps) sort LAST in BOTH
    directions — never above a real value.

    Qt's view sort reverses the `<` result for a descending column, so a fixed `__lt__` that puts
    blanks last ascending would float them to the TOP descending. To keep blanks last either way,
    the blank ordering is flipped to match the active direction: LapTable sets `_descending` to the
    column's sort order right before each sort (header click or programmatic), so `__lt__` makes
    blanks compare as the extreme that lands them at the bottom after Qt's reversal."""

    _descending = False  # active sort direction, set by LapTable before each sort

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401 (Qt sort hook)
        a = self.data(NUM_ROLE)
        b = other.data(NUM_ROLE)
        a_blank = _is_blank(a)
        b_blank = _is_blank(b)
        if a_blank or b_blank:
            if a_blank and b_blank:
                return False  # two blanks: equal, stable order
            # Exactly one is blank. Blanks must end up LAST after Qt's optional descending reversal:
            #   ascending  -> blank is "greatest" (blank < x is False, x < blank is True);
            #   descending -> Qt reverses the result, so blank must be "smallest" here (blank < x is
            #                 True, x < blank is False) to STILL land at the bottom after reversal.
            if a_blank:        # self is the blank
                return self._descending
            return not self._descending  # other is the blank, self is real
        return float(a) < float(b)


class LapTable(QWidget):
    laps_selected = Signal(object)  # list[int]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._current_lap = None  # F3: the lap on the video (independent of selection)
        # Highlight caches, populated by refresh(): the per-column session-best split values
        # (F5 purple target) and the set of lap ids with a GPS dropout (⚠). Initialised here so
        # _apply_highlights()/_lap_cell_text() can read them directly (no getattr defaults).
        self._best_split: list = []
        self._dropout_ids: set = set()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        # Dark theme: zebra striping (alternate row colour comes from the global QSS) + a
        # comfortable row height so the table reads as a clean dark surface, not a cramped grid.
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        # Tabular/mono numeric face shared by every numeric cell (digits column-align).
        self._num_font = theme.mono_font(theme.TABLE)
        # F1: click any header to sort by the column's numeric key (asc/desc toggles). The
        # highlights are re-applied after each sort (sortIndicatorChanged) so they follow laps.
        # Default = lap number ascending (column 0) until the user clicks a different header;
        # the chosen sort is remembered and re-applied across refreshes (sector edits etc.).
        self._sort_col = 0
        self._sort_order = Qt.AscendingOrder
        self.table.setSortingEnabled(True)
        hdr = self.table.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(self._sort_col, self._sort_order)
        hdr.sortIndicatorChanged.connect(self._on_sorted)
        self.table.itemSelectionChanged.connect(self._on_selection)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.table)
        lay.addWidget(self._build_footer())
        self.refresh()

    # ------------------------------------------------------------------ build
    def _build_footer(self) -> QWidget:
        """The two session-summary footer rows ("Theoretical best" / "Best rolling").
        Deliberately a separate widget BELOW the QTableWidget, not table rows: footer rows must
        never participate in sorting or selection, so plain labels make that structural (they
        survive any sort/refresh by construction). Values are styled like the purple per-sector
        session-best cells (same colour + bold tabular font); each row carries its defining
        tooltip. `_refresh_footer` rewrites the values on every refresh()."""
        footer = QWidget()
        footer.setObjectName("LapTableFooter")
        # A hairline above the rows separates them from the table body (theme border token).
        footer.setStyleSheet(
            f"QWidget#LapTableFooter {{ border-top: 1px solid {theme.C.border}; }}")
        grid = QGridLayout(footer)
        grid.setContentsMargins(8, 4, 8, 4)
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(2)
        bold_num = theme.mono_font(theme.TABLE, theme.W_SEMIBOLD)
        self._footer_values: list[QLabel] = []
        for r, (title, _accessor, tip) in enumerate(FOOTER_ROWS):
            name = QLabel(title)
            name.setStyleSheet(f"color: {theme.C.text_dim};")
            name.setToolTip(tip)
            value = QLabel(fmt_time(float("nan")))  # "—" until refresh() fills it
            value.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            value.setFont(bold_num)
            value.setStyleSheet(f"color: {theme.C.best};")  # the purple session-best style
            value.setToolTip(tip)
            grid.addWidget(name, r, 0)
            grid.addWidget(value, r, 1)
            self._footer_values.append(value)
        grid.setColumnStretch(1, 1)
        return footer

    def _refresh_footer(self):
        """Rewrite the footer values from Session (the accessor named per row in FOOTER_ROWS).
        None (no valid laps / a sector column with no data) renders as the em-dash."""
        for (_title, accessor, _tip), label in zip(FOOTER_ROWS, self._footer_values,
                                                   strict=True):
            v = getattr(self.session, accessor)()
            label.setText(fmt_time(v if v is not None else float("nan")))

    def _n_split_cols(self) -> int:
        """How many S-split columns to show: N sector lines split a lap into N+1 sub-sectors,
        so N+1 columns when there are any sector lines, else 0 (no default split columns).
        Single-sourced (used by refresh() for the headers and _apply_highlights() for the
        purple per-column best span)."""
        n = self.session.sector_count()
        return n + 1 if n else 0

    def refresh(self):
        rows = self.session.lap_rows()

        # N sector lines split each lap into N+1 sub-sectors; show one split column per
        # sub-sector (none by default = today's 4 columns). Column count depends on this,
        # so set the headers here — refresh() runs on selection and after sectors change.
        n_splits = self._n_split_cols()
        headers = COLUMNS + [f"S{i + 1}" for i in range(n_splits)]

        # Per-lap splits (F5 input) and the per-column session-best split value (purple target).
        # The session-best is computed in Session (session_best_splits) — the same accessor the
        # theoretical-best footer sums — so the purple cells and the footer can never disagree.
        # (It always returns sector_count()+1 entries; with no sectors the table shows 0 split
        # columns, so the lone entry is simply unused here.)
        splits_by_lap = {row["idx"]: self.session.lap_sector_splits(row["idx"]) for row in rows}
        best_split = self.session.session_best_splits()

        # Sorting must be OFF while we populate (else rows reorder mid-fill and setItem(r,…)
        # lands on the wrong row); re-enabled after, preserving the user's chosen sort.
        self.table.setSortingEnabled(False)
        self.table.blockSignals(True)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            lap_id = row["idx"]
            splits = splits_by_lap[lap_id]
            # (text, numeric-sort-key) per column.
            cells: list[tuple[str, float]] = [
                (str(lap_id), float(lap_id)),
                (fmt_time(row["time"]), float(row["time"])),
                (f"{row['dist']:.0f}", float(row["dist"])),
                (f"{row['entry']:.1f}", float(row["entry"])),
            ]
            for i in range(n_splits):
                if i < len(splits):
                    cells.append((f"{splits[i]:.2f}", float(splits[i])))
                else:  # a partial lap may have fewer splits than columns — blank (NaN key),
                    cells.append(("", float("nan")))  # sorts LAST in both directions (_NumItem)
            for c, (text, key) in enumerate(cells):
                item = _NumItem(text)
                item.setData(NUM_ROLE, key)
                # Numeric columns (everything but Lap): right-align + tabular/mono font so the
                # digits line up; the Lap column keeps the default left alignment.
                if c >= NUMERIC_COL_START:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setFont(self._num_font)
                self.table.setItem(r, c, item)
            # Stash the lap id on the Lap cell so row<->lap stays correct across any sort.
            self.table.item(r, 0).setData(LAP_ROLE, lap_id)
        self.table.blockSignals(False)
        # Re-apply the user's chosen sort (lap-ascending by default) on the freshly-filled rows.
        # Tell _NumItem the direction first so blanks land LAST after any descending reversal.
        _NumItem._descending = self._sort_order == Qt.DescendingOrder
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._sort_col, self._sort_order)
        self._best_split = best_split  # cached so re-highlight after a sort needn't recompute
        # Laps with a GPS dropout (low-confidence). Keyed by lap id so the ⚠ flag + tooltip
        # follow the lap across any sort, exactly like the green/purple/▶ highlights.
        self._dropout_ids = self.session.dropout_lap_ids()
        self._apply_highlights()
        # The summary footer (theoretical best / best rolling) follows every refresh — i.e.
        # also after a timing-line edit re-segments the laps.
        self._refresh_footer()

    # ------------------------------------------------------------- highlights
    def _lap_id(self, r: int) -> int:
        # The lap id is stored in LAP_ROLE on the Lap cell — stable across sorts and the
        # "▶ " current-lap prefix.
        return int(self.table.item(r, 0).data(LAP_ROLE))

    def _row_for_lap(self, lap_id) -> int:
        if lap_id is None:
            return -1
        for r in range(self.table.rowCount()):
            if self._lap_id(r) == lap_id:
                return r
        return -1

    def _apply_highlights(self):
        """Re-apply ALL row/cell highlights keyed by lap id, so they survive any sort:
          * green foreground on every cell of the overall best lap,
          * purple foreground+bold on each per-column session-best split cell (F5),
          * the ▶ prefix + bold Lap cell for the current (playing) lap.
        The blue selection is Qt's own row background and is left to the selection model."""
        rows = self.table.rowCount()
        if not rows:
            return
        # Overall best lap = the valid lap with the min time (foreground green on all cells).
        best_lap = self.session.best_lap_id()
        n_splits = self._n_split_cols()
        best_split = self._best_split

        dropout_ids = self._dropout_ids
        self.table.blockSignals(True)
        for r in range(rows):
            lap_id = self._lap_id(r)
            is_best = lap_id == best_lap
            is_dropout = lap_id in dropout_ids
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item is None:
                    continue
                # Base text is the theme's primary off-white (theme.C.text, dark table surface);
                # the green best-lap / purple best-sector foregrounds override it per cell below.
                item.setForeground(BEST_COLOR if is_best else BASE_COLOR)
                # Low-confidence GPS-dropout laps carry a row-wide tooltip explaining the flag
                # (the visible ⚠ marker on the Lap cell is set in _apply_current_lap).
                item.setToolTip(DROPOUT_TOOLTIP if is_dropout else "")
            # Purple per-sector session-best: a sector cell whose value equals that column's min
            # reads purple+bold, overriding the green-best-lap foreground for THAT cell (F5 must
            # coexist with green/blue/▶).
            for i in range(n_splits):
                c = len(COLUMNS) + i
                item = self.table.item(r, c)
                if item is None:
                    continue
                key = item.data(NUM_ROLE)
                target = best_split[i] if i < len(best_split) else None
                font = item.font()
                if (target is not None and key is not None
                        and math.isfinite(float(key))
                        and abs(float(key) - target) < 1e-9):
                    item.setForeground(BEST_SECTOR_COLOR)
                    font.setBold(True)
                else:
                    font.setBold(False)
                item.setFont(font)
        self.table.blockSignals(False)
        self._apply_current_lap()

    def _lap_cell_text(self, lap_id, on: bool) -> str:
        """The Lap-cell text for `lap_id`: a '▶ ' prefix when it's the current (playing) lap and a
        trailing ' ⚠' low-confidence marker when it has a GPS dropout."""
        prefix = CURRENT_PREFIX if on else ""
        suffix = DROPOUT_SUFFIX if lap_id in self._dropout_ids else ""
        return f"{prefix}{lap_id}{suffix}"

    def _set_row_current(self, r: int, on: bool):
        """Apply/clear the ▶ prefix + bold on ONE row's Lap cell (the only per-lap-change cue)."""
        if r < 0:
            return
        item = self.table.item(r, 0)
        if item is None:
            return
        item.setText(self._lap_cell_text(self._lap_id(r), on))
        font = item.font()
        font.setBold(on)
        item.setFont(font)

    def _apply_current_lap(self):
        """Compose every Lap-cell's text/bold: a '▶ ' prefix + bold for the current (playing) lap
        (F3), and a trailing ' ⚠' low-confidence marker for any lap with a GPS dropout. Lap-column
        only — no row background, so the BLUE Qt selection stays the sole row-background cue. Both
        cues are keyed by lap id, so they survive sorting and coexist with the green/purple
        highlights. FULL-REBUILD path: used after refresh()/sort (every row's identity may have
        changed). The per-tick lap CHANGE uses set_current_lap's fast two-row path instead."""
        target = self._row_for_lap(self._current_lap)
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            self._set_row_current(r, r == target)
        self.table.blockSignals(False)

    def _on_sorted(self, col, order):
        # A header click re-ordered the rows; remember the chosen column/direction so a later
        # refresh() (e.g. a sector edit) keeps the user's sort, and re-apply the highlights
        # keyed by lap id so they follow the laps to their new rows.
        self._sort_col = col
        self._sort_order = order
        # Qt's header-click sort ran with the PREVIOUS direction flag, which can mis-place blank
        # cells (they must stay LAST in both directions). Set the flag to the new direction and
        # re-sort so blanks land at the bottom whichever way the column is now ordered. Guarded so
        # the re-sort's own sortIndicatorChanged (same col/order) doesn't recurse.
        descending = order == Qt.DescendingOrder
        if _NumItem._descending != descending:
            _NumItem._descending = descending
            self.table.sortByColumn(col, order)
        self._apply_highlights()

    def set_current_lap(self, lap_id):
        """Mark the lap currently playing on the video; no effect on user selection.

        Per-tick fast path: only the OLD current-lap row (clear ▶/unbold) and the NEW one (add
        ▶/bold) are touched — not every row's text+font rewritten each lap change. The full-row
        path (_apply_current_lap) is reserved for the refresh()/sort case where row identities
        shift. Identical on-screen result; far less per-change work."""
        if lap_id == self._current_lap:
            return
        old_row = self._row_for_lap(self._current_lap)
        self._current_lap = lap_id
        new_row = self._row_for_lap(lap_id)
        self.table.blockSignals(True)
        if old_row != new_row:
            self._set_row_current(old_row, False)  # clear the prefix/bold off the previous lap row
        self._set_row_current(new_row, True)       # mark the new current lap row
        self.table.blockSignals(False)

    def select(self, idxs: list[int]):
        self.table.blockSignals(True)
        self.table.clearSelection()
        for r in range(self.table.rowCount()):
            if self._lap_id(r) in idxs:
                self.table.selectRow(r)
        self.table.blockSignals(False)

    def selected_lap_ids(self) -> list[int]:
        """The lap ids of the currently-selected rows (sorted). Read-only — used to restore the
        chart overlay to the table's selection when compare mode is turned off."""
        return sorted({self._lap_id(idx.row())
                       for idx in self.table.selectionModel().selectedRows()})

    def _on_selection(self):
        self.laps_selected.emit(self.selected_lap_ids())
