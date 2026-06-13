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

Sorting: a ``QTableWidget`` with ``setSortingEnabled`` and a numeric sort key (Qt.UserRole) on
the date/best/theoretical cells so they order by VALUE not text (e.g. "1:08.408" sorts as
68.408 s; a missing value sorts last). The Open button + a double-click re-open the selected
row's recording — disabled for a row whose file(s) are missing (a greyed, non-selectable entry).
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
    """A cell that sorts on its numeric key (Qt.UserRole), with a missing value (None) sorting
    LAST. Simpler than the lap-table variant — the library never reverses blanks per direction;
    a None just compares as +inf so it sinks to the bottom ascending (acceptable for a library
    list where the meaningful rows are the ones WITH a value)."""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401 (Qt sort hook)
        a = self.data(NUM_ROLE)
        b = other.data(NUM_ROLE)
        a = float("inf") if a is None else a
        b = float("inf") if b is None else b
        return a < b


def _entry_missing(entry: dict) -> bool:
    """True iff NONE of the recording's path(s) exist on disk — the row is then greyed and not
    openable. (Any one surviving chapter is enough to re-open; the load path discovers siblings.)
    An entry with no recorded paths counts as missing (nothing to open)."""
    paths = entry.get("paths") or []
    return not any(os.path.exists(p) for p in paths)


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
        self.table.sortItems(_COL_DATE, Qt.AscendingOrder)
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

        # Select the first row (if any) so the PB chart + Open button initialise sensibly.
        if self.table.rowCount():
            self.table.selectRow(0)

    # ------------------------------------------------------------------ table build
    def _fill_rows(self):
        """Populate one row per entry. The DATE cell carries the row's metadata (paths / track /
        missing flag) in its data roles; a missing-file row is disabled + greyed across all
        columns. Sorting is OFF here (re-enabled by the caller) so insertion order is preserved
        while filling."""
        dim = QBrush(QColor(C.text_muted))
        for r, e in enumerate(self._entries):
            missing = _entry_missing(e)
            date = e.get("date")
            track = e.get("track")
            best = e.get("best")
            theo = e.get("theoretical")

            date_item = _NumItem(date or "—")
            date_item.setData(NUM_ROLE, _date_sort_key(date))
            date_item.setData(PATHS_ROLE, list(e.get("paths") or []))
            date_item.setData(TRACK_ROLE, track)
            date_item.setData(MISSING_ROLE, missing)

            track_item = QTableWidgetItem(track or "unknown track")

            best_item = _NumItem(fmt_time(best) if best is not None else "—")
            best_item.setData(NUM_ROLE, best)
            theo_item = _NumItem(fmt_time(theo) if theo is not None else "—")
            theo_item.setData(NUM_ROLE, theo)

            items = (date_item, track_item, best_item, theo_item)
            for col, it in enumerate(items):
                if missing:
                    # Greyed + not selectable/enabled: the file is gone, so it can't be opened.
                    it.setForeground(dim)
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                    if col == _COL_TRACK:
                        it.setText(f"{track or 'unknown track'}  (file missing)")
                self.table.setItem(r, col, it)

    # ------------------------------------------------------------------ selection
    def _selected_date_item(self) -> QTableWidgetItem | None:
        """The DATE cell of the current selection (the metadata-bearing cell), or None."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), _COL_DATE)

    def _on_selection(self):
        """A row was selected: refresh the PB chart for that row's track and enable Open only for
        a present (non-missing) recording."""
        item = self._selected_date_item()
        if item is None:
            self.open_btn.setEnabled(False)
            self._show_pb(None)
            return
        missing = bool(item.data(MISSING_ROLE))
        self.open_btn.setEnabled(not missing)
        self._show_pb(item.data(TRACK_ROLE))

    def _show_pb(self, track: str | None):
        """Plot the PB progression (best lap vs date) for `track` from the index. With <1 dated
        best the curve clears (nothing to place on the time axis) and the title notes it."""
        if not track:
            self._pb_curve.setData([], [])
            self._pb_title.setText("PB progression")
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
        elif len(ys) == 1:
            self._pb_title.setText(f"PB progression — {track}  (1 session: {fmt_time(ys[0])})")
        else:
            self._pb_title.setText(f"PB progression — {track}  (no dated best laps)")

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
