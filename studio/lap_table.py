"""LapTable: lap times / distances / entry speed. Multi-select rows to compare laps.

Columns are click-to-sort by the UNDERLYING numeric value (F1): each cell carries its
numeric key in Qt.UserRole and a `_NumItem` sorts on that, so "1:08.408" sorts as 68.408 s
and "23.10" as 23.1 — not lexically. Clicking a header toggles asc/desc; the default order
(by lap number) holds until the user clicks.

Row/cell highlights are keyed by LAP ID, not row index, and re-applied after every sort so
they always follow the right lap: the ▶ playing marker + bold (current lap), the green best
lap (F-existing), the blue Qt selection, the PURPLE per-sector session-best cells (F5 —
the fastest split in each S-column across all valid laps, motorsport convention), and a
trailing ⚠ low-confidence marker (+ row tooltip) on laps with a GPS dropout. Base row text
is near-black for readability on the light table background.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .session import fmt_time

BASE_COLOR = QColor("#101010")          # near-black: default row text (the table is on a LIGHT bg)
BEST_COLOR = QColor("#06d6a0")          # green: the overall best lap (foreground on every cell)
BEST_SECTOR_COLOR = QColor("#b388eb")   # purple: per-column session-best split (F5)
CURRENT_PREFIX = "▶ "  # "▶ " marks the lap currently playing on the video
DROPOUT_SUFFIX = " ⚠"  # low-confidence: this lap has a GPS dropout (time/distance less reliable)
DROPOUT_TOOLTIP = "GPS dropout in this lap — its time, distance and map are less reliable."
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]
NUM_ROLE = Qt.UserRole  # the numeric sort key stored on every cell
LAP_ROLE = Qt.UserRole + 1  # the lap id (stable across sorts), stored on the Lap cell


def _best_split_per_sector_impl(splits_by_lap: dict[int, list[float]],
                                n_splits: int) -> list[float | None]:
    """F5: the fastest (minimum) split in EACH sector column across all valid laps, computed
    independently per column. Returns one value per S-column (None if a column has no data).
    Pure min over the per-lap splits — recomputed on refresh (i.e. after a sector edit changes
    the splits). Module-level so it's unit-testable without a Session/Qt table."""
    best: list[float | None] = []
    for i in range(n_splits):
        vals = [sp[i] for sp in splits_by_lap.values()
                if i < len(sp) and math.isfinite(sp[i])]
        best.append(min(vals) if vals else None)
    return best


class _NumItem(QTableWidgetItem):
    """A table cell that sorts by a numeric key (Qt.UserRole) rather than its display text, so
    e.g. lap times "1:08.408" sort as 68.408 s and blank cells sort to the end."""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401 (Qt sort hook)
        a = self.data(NUM_ROLE)
        b = other.data(NUM_ROLE)
        if a is None or (isinstance(a, float) and math.isnan(a)):
            return False  # blanks/NaN sort to the bottom regardless of direction
        if b is None or (isinstance(b, float) and math.isnan(b)):
            return True
        return float(a) < float(b)


class LapTable(QWidget):
    laps_selected = Signal(object)  # list[int]

    def __init__(self, session):
        super().__init__()
        self.session = session
        self._current_lap = None  # F3: the lap on the video (independent of selection)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
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
        lay.addWidget(self.table)
        self.refresh()

    # ------------------------------------------------------------------ build
    def refresh(self):
        rows = self.session.lap_rows()

        # N sector lines split each lap into N+1 sub-sectors; show one split column per
        # sub-sector (none by default = today's 4 columns). Column count depends on this,
        # so set the headers here — refresh() runs on selection and after sectors change.
        n_splits = self.session.laps.sector_count() + 1 if self.session.laps.sector_count() else 0
        headers = COLUMNS + [f"S{i + 1}" for i in range(n_splits)]

        # Per-lap splits (F5 input) and the per-column session-best split value (purple target).
        splits_by_lap = {row["idx"]: self.session.lap_sector_splits(row["idx"]) for row in rows}
        best_split = _best_split_per_sector_impl(splits_by_lap, n_splits)

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
                else:  # a partial lap may have fewer splits than columns — blank, sorts last
                    cells.append(("", float("nan")))
            for c, (text, key) in enumerate(cells):
                item = _NumItem(text)
                item.setData(NUM_ROLE, key)
                self.table.setItem(r, c, item)
            # Stash the lap id on the Lap cell so row<->lap stays correct across any sort.
            self.table.item(r, 0).setData(LAP_ROLE, lap_id)
        self.table.blockSignals(False)
        # Re-apply the user's chosen sort (lap-ascending by default) on the freshly-filled rows.
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._sort_col, self._sort_order)
        self._best_split = best_split  # cached so re-highlight after a sort needn't recompute
        # Laps with a GPS dropout (low-confidence). Keyed by lap id so the ⚠ flag + tooltip
        # follow the lap across any sort, exactly like the green/purple/▶ highlights.
        self._dropout_ids = self.session.dropout_lap_ids()
        self._apply_highlights()

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
        n_splits = self.session.laps.sector_count() + 1 if self.session.laps.sector_count() else 0
        best_split = getattr(self, "_best_split", [])

        dropout_ids = getattr(self, "_dropout_ids", set())
        self.table.blockSignals(True)
        for r in range(rows):
            lap_id = self._lap_id(r)
            is_best = lap_id == best_lap
            is_dropout = lap_id in dropout_ids
            for c in range(self.table.columnCount()):
                item = self.table.item(r, c)
                if item is None:
                    continue
                # Base text is near-black for readability on the light table background; the
                # green best-lap / purple best-sector foregrounds override it per cell below.
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

    def _apply_current_lap(self):
        """Compose the Lap-cell text: a '▶ ' prefix + bold for the current (playing) lap (F3),
        and a trailing ' ⚠' low-confidence marker for any lap with a GPS dropout. Lap-column
        only — no row background, so the BLUE Qt selection stays the sole row-background cue.
        Both cues are keyed by lap id, so they survive sorting and coexist with each other and
        with the green/purple highlights."""
        target = self._row_for_lap(self._current_lap)
        dropout_ids = getattr(self, "_dropout_ids", set())
        self.table.blockSignals(True)
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is None:
                continue
            on = r == target
            lap_id = self._lap_id(r)
            prefix = CURRENT_PREFIX if on else ""
            suffix = DROPOUT_SUFFIX if lap_id in dropout_ids else ""
            item.setText(f"{prefix}{lap_id}{suffix}")
            font = item.font()
            font.setBold(on)
            item.setFont(font)
        self.table.blockSignals(False)

    def _on_sorted(self, col, order):
        # A header click re-ordered the rows; remember the chosen column/direction so a later
        # refresh() (e.g. a sector edit) keeps the user's sort, and re-apply the highlights
        # keyed by lap id so they follow the laps to their new rows.
        self._sort_col = col
        self._sort_order = order
        self._apply_highlights()

    def set_current_lap(self, lap_id):
        """Mark the lap currently playing on the video; no effect on user selection."""
        if lap_id == self._current_lap:
            return
        self._current_lap = lap_id
        self._apply_current_lap()

    def select(self, idxs: list[int]):
        self.table.blockSignals(True)
        self.table.clearSelection()
        for r in range(self.table.rowCount()):
            if self._lap_id(r) in idxs:
                self.table.selectRow(r)
        self.table.blockSignals(False)

    def _on_selection(self):
        ids = sorted({self._lap_id(idx.row())
                      for idx in self.table.selectionModel().selectedRows()})
        self.laps_selected.emit(ids)
