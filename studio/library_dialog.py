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


def _entry_junk(entry: dict) -> bool:
    """True iff `entry` is a malformed/non-analysis row — no resolved track OR no valid laps. Such
    rows (e.g. the legacy bundled-sample row, or a recording that wouldn't segment) carry nothing to
    chart or compare, so the dialog QUARANTINES them: greyed + non-selectable + never auto-selected,
    so a library.json that already contains junk (from before the indexing fixes) still renders
    cleanly without manual cleanup. New loads no longer create such rows (app skips 0-lap opens)."""
    return not entry.get("track") or not entry.get("lap_count")


def _entry_disabled(entry: dict) -> bool:
    """True iff the row should be greyed + non-selectable — its file(s) are gone OR it's a junk row
    (no track / no laps). The two reasons are merged so selection, auto-select and the open guard
    all share one "is this row usable?" test."""
    return _entry_missing(entry) or _entry_junk(entry)


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
        # Newest-first: the most recent session is the one a user usually wants to look at, and it
        # also keeps the auto-selected row (the first USABLE one, below) the latest real recording
        # rather than the earliest — which used to land on a junk/legacy row and clear the chart.
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
        # An in-chart EMPTY-STATE label, centred in the view, shown whenever there are <2 points to
        # plot (no track, no dated bests, or a single session). Without it the chart shows bare
        # placeholder axes that read as "broken"; the message + a framed axis range read as "empty".
        # Anchored to the view centre (ignores data bounds) so it stays put as the range changes.
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

        # Auto-select the first USABLE row (a present, non-junk recording) under the newest-first
        # sort — i.e. the most recent real session — so the PB chart + Open button initialise with
        # data, never on a quarantined junk/missing row (those clear the chart and look broken). If
        # every row is quarantined, select nothing and the PB chart shows its empty-state.
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
                    # Greyed + not selectable/enabled: nothing to open (file gone) or nothing to
                    # chart (no track / no laps), so the row is quarantined, not auto-selected.
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
        """Plot the PB progression (best lap vs date) for `track` from the index. Three cases, each
        with a SENSIBLE chart (never bare placeholder axes that read as broken):
          * >=2 dated bests  → the line + markers, auto-ranged to the data, empty-state hidden;
          * exactly 1        → the single marker, framed by a small padded range so it doesn't sit
                               on the axis edge, plus a "one session so far" empty-state note;
          * 0 (no track, or no dated best laps) → curve cleared + an explicit in-chart message."""
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
            # Frame the lone point: a 1-day x window and a +/-1 s y window around it, so the marker
            # sits in the middle of the view rather than on the axis edge (autoRange degenerates on
            # a single point). The empty-state note explains there's nothing to chart YET.
            self._frame_single_point(xs[0], ys[0])
            self._set_pb_empty("Not enough sessions on this track yet to chart progression")
        else:
            self._pb_title.setText(f"PB progression — {track}  (no dated best laps)")
            self._set_pb_empty("Not enough sessions on this track yet to chart progression")

    def _set_pb_empty(self, message: str | None):
        """Show (or hide, on None) the centred in-chart empty-state label. Re-positioned to the
        view's centre on every call so it stays centred as the axis range changes underneath it."""
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
