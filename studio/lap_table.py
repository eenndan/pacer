"""LapTable: lap times / distances / entry speed. Multi-select rows to compare laps."""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .session import fmt_time

BEST_COLOR = QColor("#06d6a0")
CURRENT_PREFIX = "▶ "  # "▶ " marks the lap currently playing on the video
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]


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
        self.table.itemSelectionChanged.connect(self._on_selection)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        self.refresh()

    def refresh(self):
        rows = self.session.lap_rows()
        best = min(range(len(rows)), key=lambda i: rows[i]["time"]) if rows else -1

        # N sector lines split each lap into N+1 sub-sectors; show one split column per
        # sub-sector (none by default = today's 4 columns). Column count depends on this,
        # so set the headers here — refresh() runs on selection and after sectors change.
        n_splits = self.session.laps.sector_count() + 1 if self.session.laps.sector_count() else 0
        headers = COLUMNS + [f"S{i + 1}" for i in range(n_splits)]

        self.table.blockSignals(True)
        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)
        self.table.setRowCount(len(rows))
        for r, row in enumerate(rows):
            cells = [
                str(row["idx"]),
                fmt_time(row["time"]),
                f"{row['dist']:.0f}",
                f"{row['entry']:.1f}",
            ]
            if n_splits:
                splits = self.session.lap_sector_splits(row["idx"])
                # A partial lap may have fewer splits than columns — leave those cells blank.
                cells += [f"{splits[i]:.2f}" if i < len(splits) else "" for i in range(n_splits)]
            for c, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if r == best:
                    item.setForeground(BEST_COLOR)
                self.table.setItem(r, c, item)
        self.table.blockSignals(False)
        self._apply_current_lap()

    def _lap_id(self, r: int) -> int:
        # The Lap cell may carry the "▶ " current-lap prefix; strip it before parsing.
        return int(self.table.item(r, 0).text().removeprefix(CURRENT_PREFIX))

    def _row_for_lap(self, lap_id) -> int:
        if lap_id is None:
            return -1
        for r in range(self.table.rowCount()):
            if self._lap_id(r) == lap_id:
                return r
        return -1

    def _apply_current_lap(self):
        """Mark the current lap (F3) with a '▶ ' prefix + bold in the Lap column only — no
        row background, so the BLUE Qt selection stays the sole row-background cue."""
        target = self._row_for_lap(self._current_lap)
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item is None:
                continue
            on = r == target
            lap_id = item.text().removeprefix(CURRENT_PREFIX)
            item.setText(f"{CURRENT_PREFIX}{lap_id}" if on else lap_id)
            font = item.font()
            font.setBold(on)
            item.setFont(font)

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
