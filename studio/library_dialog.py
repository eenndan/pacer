"""The session-library dialog (F8): browse analyzed recordings + per-track PB progression.

A self-contained QDialog over a ``studio.library`` index dict (already loaded by the caller —
the dialog does no file I/O of its own, so it shows an EMPTY library cleanly when the index is
missing/corrupt). It is PACER-FREE: it consumes only the plain entry dicts + the pure
``library.pb_series`` helper. Re-opening a recording is delegated to an injected
``open_recording(paths)`` callback (the app passes ``StudioWindow._load``), so this module never
imports the app.

Layout::

    ┌───────────────────────────────────────────────┐
    │  [search…]                    [track filter ▾] │  ← live filter row (track/date substring +
    ├───────────────────────────────────────────────┤     a per-track combo) so it scales to 50–200
    │  Date │ Track │ Best │ Theoretical             │  ← sortable table (one row / recording);
    │  …      …       …      …                        │     missing-file rows greyed + disabled; an
    ├───────────────────────────────────────────────┤     UNTRUSTWORTHY row carries a muted trust tag
    │  <selected track> · 12 sessions · best … · …    │  ← light cross-session progress summary line
    │  PB progression — <track>   [best-vs-date plot] │  ← pyqtgraph mini-chart for the selected
    ├───────────────────────────────────────────────┤     row's track (best lap vs recording date)
    │                              [Open]   [Close]   │
    └───────────────────────────────────────────────┘

Date/Best/Theoretical sort numerically via ``_NumItem``; Track sorts as text. The Open button +
a double-click re-open the selected row's recording (disabled for a missing/junk row).

TRUST (library schema v2): the table SHOWS every session, but an untrustworthy one (provisional
start line / estimated timing / GPS dropout — see ``library.trust_label``) gets a muted tag and is
EXCLUDED from the PB chart + progress summary, which read only ``library.pb_series`` /
``library.track_summary`` (the trustworthy subset). The dialog stays pacer-free — it consumes the
plain flags on the entry dicts and those pure helpers.
"""

from __future__ import annotations

import datetime
import os
from collections.abc import Callable

import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
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
FP_ROLE = Qt.UserRole + 4       # the entry's fingerprint key (on the Date cell), for forget/remove
FILTER_ROLE = Qt.UserRole + 5   # lower-cased "track date" haystack for the search box (on Date)

# The "all tracks" sentinel for the track-filter combo (index 0). A real track name never equals it.
_ALL_TRACKS = "All tracks"

# Privacy disclosure — a calm, factual note of what pacer stores locally and where. Surfaced in the
# Library dialog (this is where a user browsing their recorded history would look) and by
# Help ▸ Your data & privacy. Everything is on-disk and offline; nothing is uploaded — say so.
PRIVACY_NOTE = (
    "Everything pacer analyzes stays on this Mac — nothing is uploaded or shared. "
    "It stores your start/finish + sector lines in a small \"<name>.pacer.json\" file next to "
    "each video, and this library index (file paths, track names and GPS dates) under "
    "~/Library/Application Support/pacer. Right-click a recording to forget it, or use "
    "\"Clear library\" to wipe the whole index."
)

# A PlotDataItem pen/brush for the PB line + its markers (amber accent, the app's primary).
_PB_PEN = pg.mkPen(C.accent, width=2)
_PB_BRUSH = pg.mkBrush(C.accent)

# The progress-summary trend word per library.track_summary["trend"]. "single"/"none" add nothing
# (there's no trend to read from one/zero sessions) so they map to no word.
_TREND_WORD = {"improving": "improving", "stalled": "off your PB"}


def _plural(n: int, noun: str) -> str:
    """"1 session" / "3 sessions" — the summary line's one pluralization helper."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


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
                 parent=None,
                 forget_recording: Callable[[dict], dict] | None = None,
                 clear_library: Callable[[], dict] | None = None,
                 reveal_library: Callable[[], None] | None = None,
                 backup_library: Callable[[], None] | None = None):
        super().__init__(parent)
        self.setWindowTitle("pacer studio — session library")
        self.resize(720, 600)
        self._index = index
        self._open_recording = open_recording
        # Privacy controls (optional — the dialog degrades to browse-only when not injected, e.g. in
        # a bare test). Each callback OWNS the destructive act (index write + sidecar delete / index
        # wipe, all guarded in the app) and RETURNS the fresh index so the dialog re-renders from it.
        self._forget_recording = forget_recording
        self._clear_library = clear_library
        # Data-portability controls (optional). Reveal opens the app-support folder in Finder; back
        # up copies library.json to a chosen path. The app OWNS both file ops (dialog stays
        # pacer-free / file-op-free); neither mutates the index, so no re-render is needed.
        self._reveal_library = reveal_library
        self._backup_library = backup_library
        self._entries = list(index.get("entries", []))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._title = QLabel(f"{len(self._entries)} analyzed recording(s)")
        self._title.setProperty("role", "PanelHeader")
        root.addWidget(self._title)

        # ----- filter row: live search (track/date substring) + a per-track combo. Makes the
        # library usable at 50–200 sessions (the 4-column sortable table alone doesn't).
        filter_row = QHBoxLayout()
        filter_row.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search track or date…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self.search, 1)
        self.track_filter = QComboBox()
        self.track_filter.addItem(_ALL_TRACKS)
        for name in self._distinct_tracks():
            self.track_filter.addItem(name)
        self.track_filter.currentIndexChanged.connect(self._apply_filter)
        filter_row.addWidget(self.track_filter)
        root.addLayout(filter_row)

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
        # Right-click a row → "Forget this recording" (removes it from the index + deletes its
        # sidecar). Only wired when the forget callback is injected.
        if self._forget_recording is not None:
            self.table.setContextMenuPolicy(Qt.CustomContextMenu)
            self.table.customContextMenuRequested.connect(self._on_context_menu)
        root.addWidget(self.table, 3)

        # ----- light cross-session progress summary for the selected track (the 2nd/3rd-visit
        # hook: "N sessions · best … · M PBs · improving"). Reads library.track_summary (trustworthy
        # subset); honest — it never counts a provisional/estimated/dropout best as the best.
        self._summary = QLabel("")
        self._summary.setWordWrap(True)
        self._summary.setFont(theme.mono_font(11))
        self._summary.setStyleSheet(f"color: {C.text_dim};")
        root.addWidget(self._summary)

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

        # ----- privacy disclosure (calm, factual: it's all local/offline)
        privacy = QLabel(PRIVACY_NOTE)
        privacy.setWordWrap(True)
        privacy.setFont(theme.mono_font(11))
        privacy.setStyleSheet(f"color: {C.text_dim};")
        root.addWidget(privacy)

        # ----- buttons
        buttons = QHBoxLayout()
        # Clear the whole index (media + sidecars untouched) — left-aligned, away from Open/Close so
        # a destructive wipe isn't next to the everyday Open. Only shown when the callback is wired.
        if self._clear_library is not None:
            self.clear_btn = QPushButton("Clear library")
            self.clear_btn.setToolTip(
                "Forget every recording in this list (wipes the app-support index only; your video "
                "files and their .pacer.json sidecars are left untouched)")
            self.clear_btn.clicked.connect(self._on_clear_library)
            self.clear_btn.setEnabled(bool(self._entries))
            buttons.addWidget(self.clear_btn)
        # Data portability: reveal the index folder / back up library.json. Non-destructive, so no
        # confirm and always enabled when wired (there's always a folder to reveal, and back-up
        # informs the user when there's nothing to copy yet). Injected callbacks only.
        if self._reveal_library is not None:
            self.reveal_btn = QPushButton("Reveal in Finder")
            self.reveal_btn.setToolTip(
                "Open the folder that holds your library index (library.json)")
            self.reveal_btn.clicked.connect(lambda: self._reveal_library())
            buttons.addWidget(self.reveal_btn)
        if self._backup_library is not None:
            self.backup_btn = QPushButton("Back up…")
            self.backup_btn.setToolTip("Save a copy of your library index to a location you choose")
            self.backup_btn.clicked.connect(lambda: self._backup_library())
            buttons.addWidget(self.backup_btn)
        buttons.addStretch(1)
        self.open_btn = QPushButton("Open")
        self.open_btn.setEnabled(False)
        self.open_btn.clicked.connect(self._open_selected)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.reject)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(close_btn)
        root.addLayout(buttons)

        # Auto-select the most recent usable recording (none if all quarantined), then apply the
        # (initially empty) filter so the summary line + row visibility are in sync from the start.
        self._select_first_usable_row()
        self._apply_filter()

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

    # ------------------------------------------------------------------ filter
    def _distinct_tracks(self) -> list[str]:
        """The sorted distinct track names across the current entries (for the filter combo).
        Skips null-track (junk) rows — there's no track to filter on."""
        return sorted({e["track"] for e in self._entries if e.get("track")})

    def _apply_filter(self):
        """Hide rows that don't match the search text (track/date substring) AND the selected track
        filter. Live — wired to both the search box and the combo. Uses ``setRowHidden`` so the sort
        order / selection model stay intact; re-selects the first visible usable row afterward so the
        PB chart + summary track the visible set."""
        query = self.search.text().strip().lower() if hasattr(self, "search") else ""
        chosen = self.track_filter.currentText() if hasattr(self, "track_filter") else _ALL_TRACKS
        for r in range(self.table.rowCount()):
            date_item = self.table.item(r, _COL_DATE)
            if date_item is None:
                continue
            hay = date_item.data(FILTER_ROLE) or ""
            track = date_item.data(TRACK_ROLE)
            hidden = (bool(query) and query not in hay) or (
                chosen != _ALL_TRACKS and track != chosen)
            self.table.setRowHidden(r, hidden)
        # Keep a sensible selection: if the selected row got hidden (or none is selected), land on
        # the first VISIBLE usable row so the chart/summary reflect what's on screen.
        self._reselect_visible()

    def _reselect_visible(self):
        """Select the first VISIBLE, non-disabled row; clear the selection (→ empty chart/summary)
        when the filter leaves nothing usable on screen."""
        cur = self._selected_date_item()
        if cur is not None and not self.table.isRowHidden(cur.row()):
            return                                   # current selection still visible — keep it
        for r in range(self.table.rowCount()):
            if self.table.isRowHidden(r):
                continue
            date_item = self.table.item(r, _COL_DATE)
            if date_item is not None and not bool(date_item.data(MISSING_ROLE)):
                self.table.selectRow(r)
                return
        self.table.clearSelection()
        self._on_selection()                         # nothing visible/usable → empty state

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
            date_item.setData(FP_ROLE, e.get("fingerprint"))
            # MISSING_ROLE doubles as the "not openable / not auto-selectable" flag — set for a
            # file-missing OR a quarantined junk row, so _on_selection / _open_selected guard both.
            date_item.setData(MISSING_ROLE, disabled)
            # The search haystack: lower-cased "track date" so the box matches either substring.
            date_item.setData(FILTER_ROLE, f"{track or ''} {date or ''}".strip().lower())

            track_item = QTableWidgetItem(track or "unknown track")

            best_item = _NumItem(fmt_time(best) if best is not None else "—")
            best_item.setData(NUM_ROLE, best)
            theo_item = _NumItem(fmt_time(theo) if theo is not None else "—")
            theo_item.setData(NUM_ROLE, theo)

            # A junk row says so; a present-but-missing-file row keeps its established label. An
            # UNTRUSTWORTHY-but-openable row gets a muted trust tag (provisional/estimated/dropout)
            # so the user can see WHICH sessions the PB chart excludes — reusing the theme's trust
            # tier (italic + PROVISIONAL_COLOR, palette-safe).
            trust = None if disabled else _library.trust_label(e)
            suffix = ("  (no laps)" if junk else "  (file missing)" if missing
                      else f"  · {trust}" if trust else "")

            items = (date_item, track_item, best_item, theo_item)
            for col, it in enumerate(items):
                if disabled:
                    it.setForeground(dim)
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                    if col == _COL_TRACK:
                        it.setText(f"{track or 'unknown track'}{suffix}")
                elif col == _COL_TRACK and trust:
                    # Muted + italic across the row's Track cell so the tag reads as demoted, not an
                    # error. The row stays fully selectable/openable — it's just marked, not blocked.
                    it.setText(f"{track or 'unknown track'}{suffix}")
                    it.setForeground(QBrush(QColor(theme.PROVISIONAL_COLOR)))
                    theme.apply_provisional_style(it, True)
                self.table.setItem(r, col, it)

    # ------------------------------------------------------------------ selection
    def _selected_date_item(self) -> QTableWidgetItem | None:
        """The DATE cell of the current selection (the metadata-bearing cell), or None."""
        rows = self.table.selectionModel().selectedRows() if self.table.selectionModel() else []
        if not rows:
            return None
        return self.table.item(rows[0].row(), _COL_DATE)

    def _on_selection(self):
        """A row was selected: refresh the PB chart + progress summary for its track; enable Open
        only for a usable (present, non-junk) recording."""
        item = self._selected_date_item()
        if item is None:
            self.open_btn.setEnabled(False)
            self._show_pb(None)
            self._show_summary(None)
            return
        missing = bool(item.data(MISSING_ROLE))
        self.open_btn.setEnabled(not missing)
        track = item.data(TRACK_ROLE)
        self._show_pb(track)
        self._show_summary(track)

    def _show_summary(self, track: str | None):
        """Set the light cross-session progress line for `track` from ``library.track_summary``
        (trustworthy subset). Blank when there's no track selected; otherwise a compact honest read:
        ``"<track> · 12 sessions · best 68.42 (2026-06-14) · 3 PBs · improving"``. A track with no
        trustworthy dated best just reports its session count (nothing to boast yet)."""
        summary = _library.track_summary(self._index, track) if track else None
        if not summary:
            self._summary.setText("")
            return
        parts = [summary["track"], _plural(summary["sessions"], "session")]
        if summary["best"] is not None:
            best = fmt_time(summary["best"])
            date = f" ({summary['best_date']})" if summary["best_date"] else ""
            parts.append(f"best {best}{date}")
            parts.append(_plural(summary["pb_count"], "PB"))
            trend = _TREND_WORD.get(summary["trend"])
            if trend:
                parts.append(trend)
        self._summary.setText("  ·  ".join(parts))

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

    # ------------------------------------------------------------------ privacy: forget / clear
    def _on_context_menu(self, pos):
        """Right-click on a row → a small menu with "Forget this recording". No menu on empty space
        or when the forget callback isn't wired."""
        if self._forget_recording is None:
            return
        item = self.table.itemAt(pos)
        if item is None:
            return
        date_item = self.table.item(item.row(), _COL_DATE)
        if date_item is None:
            return
        menu = QMenu(self)
        act = menu.addAction("Forget this recording…")
        act.setToolTip(
            "Remove this recording from the library index and delete its .pacer.json timing-line "
            "sidecar. Your video file is not touched.")
        chosen = menu.exec(self.table.viewport().mapToGlobal(pos))
        if chosen is act:
            self._forget_row(date_item)

    def _forget_row(self, date_item: QTableWidgetItem):
        """Confirm, then forget the row: the injected callback removes the index entry + deletes its
        sidecar (guarded in the app) and returns the fresh index, from which the table re-renders."""
        fp = date_item.data(FP_ROLE)
        if not fp:
            return
        entry = next((e for e in self._entries if e.get("fingerprint") == fp), None)
        if entry is None:
            return
        track = entry.get("track") or "unknown track"
        date = entry.get("date") or "no date"
        ok = QMessageBox.question(
            self, "Forget this recording",
            f"Forget “{track}” ({date})?\n\n"
            "This removes it from the library and deletes its .pacer.json timing-line "
            "sidecar. Your video file is not touched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._forget_recording(entry)
        self._rerender()

    def _on_clear_library(self):
        """Confirm, then wipe the whole index via the injected callback (media + sidecars left
        untouched) and re-render to the empty state."""
        if self._clear_library is None or not self._entries:
            return
        ok = QMessageBox.question(
            self, "Clear library",
            f"Forget all {len(self._entries)} recording(s) from the library?\n\n"
            "This wipes the library index only — your video files and their .pacer.json "
            "sidecars are left untouched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._clear_library()
        self._rerender()

    def _rerender(self):
        """Rebuild the table + chart from ``self._index`` after a forget/clear. Rebuilds rather than
        surgically deleting one QTableWidget row so the sort keys / role data stay consistent."""
        self._entries = list(self._index.get("entries", []))
        self._title.setText(f"{len(self._entries)} analyzed recording(s)")
        self.table.setSortingEnabled(False)
        self.table.clearContents()
        self.table.setRowCount(len(self._entries))
        self._fill_rows()
        self.table.setSortingEnabled(True)
        self.table.sortItems(_COL_DATE, Qt.DescendingOrder)
        # Rebuild the track-filter combo (a forget/clear can change the distinct-track set); keep the
        # current pick if it still exists, else fall back to "All tracks". Block the change signal so
        # the rebuild doesn't re-trigger _apply_filter mid-render.
        prev = self.track_filter.currentText()
        self.track_filter.blockSignals(True)
        self.track_filter.clear()
        self.track_filter.addItem(_ALL_TRACKS)
        for name in self._distinct_tracks():
            self.track_filter.addItem(name)
        idx = self.track_filter.findText(prev)
        self.track_filter.setCurrentIndex(idx if idx >= 0 else 0)
        self.track_filter.blockSignals(False)
        if getattr(self, "clear_btn", None) is not None:
            self.clear_btn.setEnabled(bool(self._entries))
        self._select_first_usable_row()
        self._apply_filter()

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
