"""The session-library dialog (F8): browse analyzed recordings + per-track PB progression.

A self-contained QDialog over a ``studio.library`` index dict (already loaded by the caller —
the dialog does no file I/O of its own, so it shows an EMPTY library cleanly when the index is
missing/corrupt). It is PACER-FREE: it consumes only the plain entry dicts + the pure
``library.pb_series`` helper. Re-opening a recording is delegated to an injected
``open_recording(paths)`` callback (the app passes ``StudioWindow._load``), so this module never
imports the app.

Layout::

    ┌───────────────────────────────────────────────┐
    │  Date │ Track │ Best │ Theoretical             │  ← sortable table (one row / recording)
    │  …      …       …      …                        │     missing-file rows greyed + disabled
    ├───────────────────────────────────────────────┤
    │  PB progression — <track>   [best-vs-date plot] │  ← pyqtgraph mini-chart for the selected
    ├───────────────────────────────────────────────┤     row's track (best lap vs recording date)
    │                              [Open]   [Close]   │
    └───────────────────────────────────────────────┘

Date/Best/Theoretical sort numerically via ``_NumItem``; Track sorts as text. The Open button +
a double-click re-open the selected row's recording (disabled for a missing/junk row).
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import library as _library
from . import theme
from ._signal import fmt_time
from .theme import C

# Column layout — index → header. Date/Best/Theoretical sort numerically (a key in NUM_ROLE);
# Track sorts as text.
_COL_DATE, _COL_TRACK, _COL_BEST, _COL_THEO = range(4)
_HEADERS = ["Date", "Track", "Best lap", "Theoretical"]

NUM_ROLE = Qt.UserRole          # numeric sort key on a cell (date epoch / seconds)
PATHS_ROLE = Qt.UserRole + 1    # the entry's file path list (on the Date cell)
TRACK_ROLE = Qt.UserRole + 2    # the entry's track name, raw (on the Date cell)
MISSING_ROLE = Qt.UserRole + 3  # True if the recording's file(s) are missing (on the Date cell)

# A PlotDataItem pen/brush for the PB line + its markers (amber accent, the app's primary).
_PB_PEN = pg.mkPen(C.accent, width=2)
_PB_BRUSH = pg.mkBrush(C.accent)


class _NumItem(QTableWidgetItem):
    """Table cell sorting on its NUM_ROLE numeric key; None compares as +inf so it sorts last."""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401 (Qt sort hook)
        a = self.data(NUM_ROLE)
        b = other.data(NUM_ROLE)
        a = float("inf") if a is None else a
        b = float("inf") if b is None else b
        return a < b


def _entry_missing(entry: dict) -> bool:
    """True iff none of the recording's path(s) exist on disk (any one surviving chapter is enough
    to re-open); no recorded paths counts as missing."""
    paths = entry.get("paths") or []
    return not any(os.path.exists(p) for p in paths)


def _entry_junk(entry: dict) -> bool:
    """True iff `entry` has no track or no valid laps — nothing to chart/open, so the dialog
    greys + quarantines it."""
    return not entry.get("track") or not entry.get("lap_count")


def _date_sort_key(date: str | None) -> float | None:
    """A sortable numeric key for a "YYYY-MM-DD" date string: its ordinal (days). Lexical order
    of an ISO date already equals chronological order, but a numeric key keeps the _NumItem path
    uniform with the time columns. None (no date) → None (sorts last)."""
    if not date:
        return None
    try:
        y, m, d = (int(x) for x in date.split("-"))
        return float(datetime.date(y, m, d).toordinal())
    except (ValueError, TypeError):
        return None


def _epoch_seconds(date: str) -> float | None:
    """UTC epoch SECONDS at midnight of a "YYYY-MM-DD" date — the x value for the PB chart's
    DateAxisItem (which expects POSIX timestamps). None on a malformed date."""
    try:
        y, m, d = (int(x) for x in date.split("-"))
        dt = datetime.datetime(y, m, d, tzinfo=datetime.UTC)
        return dt.timestamp()
    except (ValueError, TypeError):
        return None


class LibraryDialog(QDialog):
    """The File ▸ Library… dialog. `index` is a loaded ``studio.library`` index dict;
    `open_recording` is called with an entry's `paths` list to re-open it (the app passes its
    guarded `_load`). The dialog closes itself before re-opening so the reload happens against
    the main window, not behind a modal."""

    def __init__(self, index: dict, open_recording: Callable[[list[str]], None],
                 parent=None):
        super().__init__(parent)
        self.setWindowTitle("pacer studio — session library")
        self.resize(720, 560)
        self._index = index
        self._open_recording = open_recording
        self._entries = list(index.get("entries", []))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        title = QLabel(f"{len(self._entries)} analyzed recording(s)")
        title.setProperty("role", "PanelHeader")
        root.addWidget(title)

        # ----- the sortable recordings table
        self.table = QTableWidget(len(self._entries), len(_HEADERS))
        self.table.setHorizontalHeaderLabels(_HEADERS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.SingleSelection)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(_COL_TRACK, QHeaderView.Stretch)
        for col in (_COL_DATE, _COL_BEST, _COL_THEO):
            hdr.setSectionResizeMode(col, QHeaderView.ResizeToContents)
        self._fill_rows()
        self.table.setSortingEnabled(True)
        # Newest-first so the auto-selected (first usable) row is the most recent recording.
        self.table.sortItems(_COL_DATE, Qt.DescendingOrder)
        self.table.itemSelectionChanged.connect(self._on_selection)
        self.table.itemDoubleClicked.connect(lambda _it: self._open_selected())
        root.addWidget(self.table, 3)

        # ----- per-track PB-progression mini-chart (best lap vs recording date)
        self._pb_title = QLabel("PB progression")
        self._pb_title.setProperty("role", "PanelHeader")
        root.addWidget(self._pb_title)
        self.pb_plot = pg.PlotWidget(axisItems={"bottom": pg.DateAxisItem(orientation="bottom")})
        self.pb_plot.setBackground(C.surface)
        self.pb_plot.setMinimumHeight(150)
        self.pb_plot.setLabel("left", "best lap (s)")
        self.pb_plot.getAxis("left").enableAutoSIPrefix(False)
        self.pb_plot.showGrid(x=True, y=True, alpha=0.12)
        for side in ("left", "bottom"):
            ax = self.pb_plot.getAxis(side)
            ax.setPen(C.border)
            ax.setTextPen(C.text_dim)
            ax.setTickFont(theme.mono_font(11))
        # ONE reusable curve item (line + markers); its data is swapped per selected track.
        self._pb_curve = pg.PlotDataItem(
            pen=_PB_PEN, symbol="o", symbolSize=7,
            symbolBrush=_PB_BRUSH, symbolPen=pg.mkPen(C.surface, width=1))
        self.pb_plot.addItem(self._pb_curve)
        # Centred in-chart empty-state label, shown when <2 points to plot (see _show_pb).
        # Anchored to the view centre so it stays put as the range changes.
        self._pb_empty = pg.TextItem(color=C.text_dim, anchor=(0.5, 0.5))
        self._pb_empty.setParentItem(self.pb_plot.getPlotItem().getViewBox())
        self._pb_empty.setVisible(False)
        root.addWidget(self.pb_plot, 2)

        # ----- buttons
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        self.open_btn = QPushButton("Open")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        # Auto-select the most recent usable recording (none if all quarantined).
        self._select_first_usable_row()

    def _select_first_usable_row(self):
        """Select the first row (in the current sort order) whose DATE cell is NOT flagged disabled
        (MISSING_ROLE) — i.e. a present, non-junk recording. No-op (leaves nothing selected) when
        every row is quarantined, so the PB chart + Open button stay in their empty/disabled state.
        Called once at construction; the PB chart's <2-point empty-state covers the no-selection."""
        for r in range(self.table.rowCount()):
            date_item = self.table.item(r, _COL_DATE)
            if date_item is not None and not bool(date_item.data(MISSING_ROLE)):
                self.table.selectRow(r)
                return
        # Nothing usable: refresh the chart explicitly to its empty-state (no selection signal
        # fires when no row gets selected).
        self._on_selection()

    # ------------------------------------------------------------------ table build
    def _fill_rows(self):
        """Populate one row per entry. The DATE cell carries the row's metadata (paths / track /
        missing flag) in its data roles; a missing-file row is disabled + greyed across all
        columns. Sorting is OFF here (re-enabled by the caller) so insertion order is preserved
        while filling."""
        dim = QBrush(QColor(C.text_muted))
        for r, e in enumerate(self._entries):
            missing = _entry_missing(e)
            junk = _entry_junk(e)
            disabled = missing or junk
            date = e.get("date")
            track = e.get("track")
            best = e.get("best")
            theo = e.get("theoretical")

            date_item = _NumItem(date or "—")
            date_item.setData(NUM_ROLE, _date_sort_key(date))
            date_item.setData(PATHS_ROLE, list(e.get("paths") or []))
            date_item.setData(TRACK_ROLE, track)
            # MISSING_ROLE doubles as the "not openable / not auto-selectable" flag — set for a
            # file-missing OR a quarantined junk row, so _on_selection / _open_selected guard both.
            date_item.setData(MISSING_ROLE, disabled)

            track_item = QTableWidgetItem(track or "unknown track")

            best_item = _NumItem(fmt_time(best) if best is not None else "—")
            best_item.setData(NUM_ROLE, best)
            theo_item = _NumItem(fmt_time(theo) if theo is not None else "—")
            theo_item.setData(NUM_ROLE, theo)

            # A junk row says so; a present-but-missing-file row keeps its established label.
            suffix = "  (no laps)" if junk else "  (file missing)" if missing else ""

            items = (date_item, track_item, best_item, theo_item)
            for col, it in enumerate(items):
                if disabled:
                    it.setForeground(dim)
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                    if col == _COL_TRACK:
                        it.setText(f"{track or 'unknown track'}{suffix}")
                self.table.setItem(r, col, it)

    # ------------------------------------------------------------------ selection
    def _selected_date_item(self) -> QTableWidgetItem | None:
        """The DATE cell of the current selection (the metadata-bearing cell), or None."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), _COL_DATE)

    def _on_selection(self):
        """A row was selected: refresh the PB chart for its track; enable Open only for a usable
        (present, non-junk) recording."""
        item = self._selected_date_item()
        if item is None:
            self.open_btn.setEnabled(False)
            self._show_pb(None)
            return
        missing = bool(item.data(MISSING_ROLE))
        self.open_btn.setEnabled(not missing)
        self._show_pb(item.data(TRACK_ROLE))

    def _show_pb(self, track: str | None):
        """Plot best-lap-vs-date for `track`: line for >=2 dated bests, a framed single marker for
        1, empty-state for 0."""
        if not track:
            self._pb_curve.setData([], [])
            self._pb_title.setText("PB progression")
            self._set_pb_empty("Select a recording to see its track's PB progression")
            return
        series = _library.pb_series(self._index, track)
        xs, ys = [], []
        for date, best in series:
            x = _epoch_seconds(date)
            if x is not None:
                xs.append(x)
                ys.append(best)
        self._pb_curve.setData(xs, ys)
        if len(ys) >= 2:
            self._pb_title.setText(
                f"PB progression — {track}  ({fmt_time(min(ys))} best over {len(ys)} sessions)")
            self._set_pb_empty(None)
            self.pb_plot.enableAutoRange()
            self.pb_plot.autoRange()
        elif len(ys) == 1:
            self._pb_title.setText(f"PB progression — {track}  (1 session: {fmt_time(ys[0])})")
            self._frame_single_point(xs[0], ys[0])
            self._set_pb_empty("Not enough sessions on this track yet to chart progression")
        else:
            self._pb_title.setText(f"PB progression — {track}  (no dated best laps)")
            self._set_pb_empty("Not enough sessions on this track yet to chart progression")

    def _set_pb_empty(self, message: str | None):
        """Show (or hide on None) the centred empty-state label; re-centred each call as the range
        changes."""
        if not message:
            self._pb_empty.setVisible(False)
            return
        self._pb_empty.setText(message)
        self._pb_empty.setVisible(True)
        rect = self.pb_plot.getPlotItem().getViewBox().viewRect()
        self._pb_empty.setPos(rect.center())

    def _frame_single_point(self, x: float, y: float):
        """Set a small PADDED axis range around a single (x, y) point so it's framed centrally (a
        bare ``setData`` of one point with autorange leaves a degenerate zero-width range)."""
        self.pb_plot.disableAutoRange()
        day = 86400.0
        self.pb_plot.setXRange(x - day, x + day, padding=0)
        self.pb_plot.setYRange(y - 1.0, y + 1.0, padding=0)

    # ------------------------------------------------------------------ open
    def _open_selected(self):
        """Re-open the selected recording via the injected callback (the app's `_load`). Closes
        the dialog first so the reload runs against the main window. No-op for a missing-file row
        (Open is disabled there, and double-click is guarded here too)."""
        item = self._selected_date_item()
        if item is None or bool(item.data(MISSING_ROLE)):
            return
        paths = item.data(PATHS_ROLE)
        if not paths:
            return
        self.accept()
        self._open_recording(list(paths))
