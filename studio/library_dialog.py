"""The session-library dialog (F8): browse analyzed recordings + per-track PB progression.

A self-contained QDialog over a ``studio.library`` index dict (already loaded by the caller —
the dialog does no file I/O of its own, so it shows an EMPTY library cleanly when the index is
missing/corrupt). It is PACER-FREE: it consumes only the plain entry dicts + the pure
``library.pb_series`` helper. Re-opening a recording is delegated to an injected
``open_recording(paths)`` callback (the app passes ``StudioWindow._load``), so this module never
imports the app.

Layout::

    ┌───────────────────────────────────────────────┐
    │  N analyzed recordings  (M of N when filtered) │  ← header count, of what is ON SCREEN
    │  [search…]                    [track filter ▾] │  ← live filter row (track/date substring + a
    ├───────────────────────────────────────────────┤     per-track combo, plus an Unknown-track
    │  Date │ Track │ Best │ Theoretical             │     bucket) so it scales to 50–200
    │  …      …       …      …                        │  ← sortable table (one row / recording);
    │  “No recordings match …” when the filter empties│     missing-file rows greyed + disabled; an
    ├───────────────────────────────────────────────┤     UNTRUSTWORTHY row carries a muted trust tag
    │  <selected track> · 12 sessions · best … · …    │  ← light cross-session progress summary line
    │  PB progression — <track>   [best-vs-date plot] │  ← pyqtgraph mini-chart for the selected
    ├───────────────────────────────────────────────┤     row's track (best lap vs recording date)
    │                              [Open]   [Close]   │
    └───────────────────────────────────────────────┘

SIZE: the TABLE is the reason this dialog exists, so it takes the pixels — the PB chart is held to a
150–200 px band (it yields first when space is tight and stops growing once it has enough), and the
dialog opens tall enough to browse a real library, clamped to the screen and replaced by the user's
own size once they resize it (persisted through ``studio.prefs``).

Date/Best/Theoretical sort numerically via ``_NumItem``; Track sorts as text. The Open button +
a double-click re-open the selected row's recording (disabled for a missing/junk row). Every time
this dialog prints a lap time — the Best/Theoretical cells, the summary line, the chart's left axis
(``_LapTimeAxis``) — it goes through ``_signal.fmt_time``, so one frame never carries two formats.

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
from PySide6.QtGui import QBrush, QColor, QGuiApplication
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

from . import APP_NAME, prefs, theme
from . import library as _library
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

# The track-filter combo's two sentinels (a real track name never equals either): "all tracks" at
# index 0, and an UNKNOWN-TRACK bucket appended when some recording's circuit isn't in the track
# registry. The registry ships with about one circuit, so those rows are the common case — without
# the bucket the combo simply cannot reach them (2 of 3 on the QA index).
_ALL_TRACKS = "All tracks"
_UNKNOWN_TRACK = "Unknown track"

# What a Track cell reads when the registry doesn't know the circuit — shown in the cell, matched by
# the search box, and the label the unknown-track filter bucket stands for.
_UNKNOWN_LABEL = "unknown track"

# Privacy disclosure — a calm, factual note of what pacer stores locally and where. Surfaced in the
# Library dialog (this is where a user browsing their recorded history would look) and by
# Help ▸ Your data & privacy. Everything is on-disk and offline; nothing is uploaded — say so.
PRIVACY_NOTE = (
    "Everything pacer analyzes stays on this Mac — nothing is uploaded or shared. "
    "It stores your start/finish + sector lines in a small \"<name>.pacer.json\" file next to "
    "each video, and under ~/Library/Application Support/pacer it keeps this library index (file "
    "paths, track names and GPS dates) and your saved tracks (tracks.json — each circuit's name "
    "and coordinates). Right-click a recording to forget it, or use \"Clear library\" to wipe the "
    "whole index — a copy of the index is kept beside it as library.json.bak, so a wipe can be "
    "undone. Your saved tracks are separate: \"Clear library\" leaves tracks.json untouched, and "
    "\"Back up…\" does not copy it."
)

# A PlotDataItem pen/brush for the PB line + its markers (amber accent, the app's primary).
_PB_PEN = pg.mkPen(C.accent, width=2)
_PB_BRUSH = pg.mkBrush(C.accent)

# The progress-summary trend word per library.track_summary["trend"]. "single"/"none" add nothing
# (there's no trend to read from one/zero sessions) so they map to no word.
_TREND_WORD = {"improving": "improving", "stalled": "off your PB"}


# The size the dialog opens at when the user has never resized it. The old 720x600 left the table a
# 139 px viewport — 4.6 rows of a 201-recording library, 2.3% of it — with the PB chart on its 150 px
# floor and a 4-line privacy paragraph, a filter row and a button row taking the rest: at 600 px
# everything is on a minimum and the layout's stretch factors never get to apply at all. 880x860
# gives the table 349 px (11.6 rows, 2.5x) with the chart at the top of its band, and it is the
# tallest round number that still opens UNCLAMPED on the smallest Mac this app targets (a 13" Air
# has ~931 px of available height; _SCREEN_MARGIN leaves 871). Anything smaller than that — an old
# 1280x800 panel, a half-height external display — is handled by _fit_to_screen rather than by
# opening a dialog taller than the screen.
_DEFAULT_SIZE = (880, 860)
# The PB chart's ceiling (its floor is setMinimumHeight(150) at the widget). It reads a handful of
# best-vs-date points and one empty-state sentence, so it has no use for more; without a ceiling it
# grew with every pixel the dialog gained, at the list's expense.
_PB_PLOT_MAX_H = 200
# Left over after clamping to the screen: room for the menu bar, the Dock and the window frame.
_SCREEN_MARGIN = 60
# Floors the clamp will not go below, so a screen that reports something tiny/bogus can never
# collapse the dialog (Qt then honours the layout's own minimum anyway).
_MIN_SIZE = (480, 420)


def _fit_to_screen(width: int, height: int, avail_w: int, avail_h: int) -> tuple[int, int]:
    """Clamp a desired dialog size to what a screen `avail_w` x `avail_h` can actually show. Pure
    (the caller supplies the screen's available geometry) so it is testable without a display, and
    applied to BOTH the default and a restored size — a size remembered on an external monitor must
    not open off-screen on the laptop panel. A non-positive available dimension (no screen) leaves
    that axis alone."""
    if avail_w > 0:
        width = min(width, max(_MIN_SIZE[0], avail_w - _SCREEN_MARGIN))
    if avail_h > 0:
        height = min(height, max(_MIN_SIZE[1], avail_h - _SCREEN_MARGIN))
    return int(width), int(height)


def _plural(n: int, noun: str) -> str:
    """"1 session" / "3 sessions" — the summary line's one pluralization helper."""
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _backup_when(mtime: float | None) -> str:
    """" taken 2026-09-03 00:12" for a ``library.backup_summary`` mtime, or "" when it has none —
    the clause that dates the backup in the Restore confirm. Formatting lives here, not in the
    pacer-free/display-agnostic library module, which hands back a raw POSIX timestamp."""
    if not mtime:
        return ""
    try:
        return " taken " + datetime.datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M")
    except (ValueError, OSError, OverflowError):
        return ""


class _NumItem(QTableWidgetItem):
    """Table cell sorting on its NUM_ROLE numeric key; None compares as +inf so it sorts last."""

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401 (Qt sort hook)
        a = self.data(NUM_ROLE)
        b = other.data(NUM_ROLE)
        a = float("inf") if a is None else a
        b = float("inf") if b is None else b
        return a < b


class _LapTimeAxis(pg.AxisItem):
    """The PB chart's left axis, rendering its seconds values as LAP TIMES through the app's one
    time formatter. A bare numeric axis printed "69" / "70.5" while the Best lap column and the
    progress summary in the SAME frame read "1:09.905" — two formats for one quantity."""

    def tickStrings(self, values, scale, spacing):  # noqa: N802 (pyqtgraph hook)
        return [fmt_time(v) for v in values]


def _entry_missing(entry: dict) -> bool:
    """True iff none of the recording's path(s) exist on disk (any one surviving chapter is enough
    to re-open); no recorded paths counts as missing."""
    paths = entry.get("paths") or []
    return not any(os.path.exists(p) for p in paths)


def _entry_junk(entry: dict) -> bool:
    """True iff `entry` has no valid laps — nothing to time, chart or open, so the dialog greys +
    quarantines it. An UNKNOWN TRACK is NOT junk: the track registry ships with about one circuit,
    so a recording it doesn't recognise is the COMMON case, and that recording still has real laps,
    a real best and a real file to re-open. It renders as "unknown track" with the row's usual trust
    tag (``library.trust_label`` → "provisional" while the start line is auto-fitted)."""
    return not entry.get("lap_count")


def _entry_name(entry: dict) -> str:
    """The recording's FILENAME — its first chapter's basename, i.e. what the user sees in Finder.
    Falls back to the stored first-chapter stem when the entry recorded no paths."""
    paths = entry.get("paths") or []
    if paths:
        return os.path.basename(paths[0])
    return entry.get("stem") or "this recording"


def _entry_tooltip(entry: dict) -> str:
    """Row hover text naming WHICH recording a row is: filename, full path, and the extra-chapter
    count for a multi-chapter recording. None of the four columns names a file (two same-day
    sessions on the same unknown track otherwise read as the same row) though the index carries
    both — this is the affordance Open Recent already gives its entries."""
    paths = entry.get("paths") or []
    lines = [_entry_name(entry)]
    if paths:
        lines.append(paths[0])
        if len(paths) > 1:
            lines.append(f"+ {_plural(len(paths) - 1, 'more chapter')}")
    return "\n".join(lines)


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
    the main window, not behind a modal.

    Every control that touches the FILESYSTEM is dependency-injected and optional — forget / clear /
    reveal / back up / restore, plus the `backup_info` query behind Restore… — so the dialog itself
    stays pacer-free and file-op-free (and therefore hermetic in tests), and any control whose
    callback is absent simply isn't built."""

    def __init__(self, index: dict, open_recording: Callable[[list[str]], None],
                 parent=None,
                 forget_recording: Callable[[dict], dict] | None = None,
                 clear_library: Callable[[], dict] | None = None,
                 reveal_library: Callable[[], None] | None = None,
                 backup_library: Callable[[], None] | None = None,
                 restore_library: Callable[[], dict] | None = None,
                 backup_info: Callable[[], dict | None] | None = None):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — session library")
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
        # RESTORE — the other half of "Back up…": `restore_library` puts the automatic
        # ``library.json.bak`` back (the app owns the file op and returns the fresh index, like
        # clear does), and `backup_info` reports what that backup holds (a ``library.backup_summary``
        # dict, or None when there is nothing restorable) so the confirm can name BOTH sides of the
        # swap. Data + action, the same split as `index` + `open_recording`; the dialog stays
        # file-op-free, which is also what keeps it hermetic in tests.
        self._restore_library = restore_library
        self._backup_info = backup_info
        self._backup = self._read_backup_info()
        self._entries = list(index.get("entries", []))

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        self._title = QLabel(_plural(len(self._entries), "analyzed recording"))
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

        # A filter that matches nothing hides every row, and a table of hidden rows is just blank
        # space — say so, and name the way back out.
        self._no_matches = QLabel("")
        self._no_matches.setProperty("role", "EmptyState")
        self._no_matches.setWordWrap(True)
        self._no_matches.setAlignment(Qt.AlignCenter)
        self._no_matches.setVisible(False)
        root.addWidget(self._no_matches)

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
        self.pb_plot = pg.PlotWidget(axisItems={
            "bottom": pg.DateAxisItem(orientation="bottom"),
            # Lap times, not decimal seconds — the same formatter the Best lap column uses, so the
            # axis and the table two rows above it read the same way. Hence no "(s)" in the label.
            "left": _LapTimeAxis(orientation="left")})
        self.pb_plot.setBackground(C.surface)
        self.pb_plot.setMinimumHeight(150)
        # …and a CEILING, so the chart lives in a fixed 150–200 px band. The floor alone decided the
        # whole layout: at 600 px tall everything was on its minimum (the stretch factors never got
        # to apply, and the table's share was 139 px = 4.6 rows), while every pixel the dialog gained
        # grew the chart too (234 px at 860, 370 px at 1200) to draw the same handful of dots. With
        # the ceiling in place the chart still yields FIRST when space is tight (stretch 2 vs the
        # table's 3) and stops growing once it has enough, so the list — the reason this dialog
        # exists — takes everything else: 11.6 rows at the new default, ~23 at 1200 px.
        self.pb_plot.setMaximumHeight(_PB_PLOT_MAX_H)
        self.pb_plot.setLabel("left", "best lap")
        self.pb_plot.getAxis("left").enableAutoSIPrefix(False)
        self.pb_plot.showGrid(x=True, y=True, alpha=0.12)
        # No pyqtgraph chrome on a read-only mini-chart: the hover "A" auto-range button and the
        # right-click plot menu are developer affordances, not part of this dialog.
        self.pb_plot.getPlotItem().hideButtons()
        self.pb_plot.setMenuEnabled(False)
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
        # Centred in-chart empty-state label, shown when <2 points to plot (see _show_pb). It is a
        # CHILD of the ViewBox, so it is positioned in the box's PIXEL space (_centre_pb_empty) and
        # re-centred on every resize — a data-space position would put it ~1.8e9 px off-screen on a
        # date axis, and a one-shot pixel position drifts ~150 px the first time the dialog resizes.
        vb = self.pb_plot.getPlotItem().getViewBox()
        self._pb_empty = pg.TextItem(color=C.text_dim, anchor=(0.5, 0.5))
        self._pb_empty.setParentItem(vb)
        self._pb_empty.setVisible(False)
        vb.sigResized.connect(lambda *_: self._centre_pb_empty())
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
                "files and their .pacer.json sidecars are left untouched). A copy of the index is "
                "kept as library.json.bak first")
            self.clear_btn.clicked.connect(self._on_clear_library)
            self.clear_btn.setEnabled(bool(self._entries))
            buttons.addWidget(self.clear_btn)
        # The other half of "Back up…": put the automatic library.json.bak back. Sits beside the wipe
        # it undoes. Disabled (not hidden) when there's no backup yet, so the way back is visible
        # BEFORE it is needed rather than appearing only once history is gone.
        if self._restore_library is not None:
            self.restore_btn = QPushButton("Restore…")
            self.restore_btn.clicked.connect(self._on_restore_library)
            buttons.addWidget(self.restore_btn)
            self._sync_restore_btn()
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
        # Size last, with the widget tree complete so the layout's own minimum is settled.
        self._apply_geometry()

    # ------------------------------------------------------------------ size
    def _apply_geometry(self):
        """Open at the user's remembered size when they have one, else at the default — both clamped
        to the screen this dialog is opening on. Records the size it opened at so ``done`` only
        persists a size the user actually CHANGED: a dialog that stores its own default on first
        close would freeze that default forever, and every future user of a never-resized library
        would be pinned to whatever this build shipped. Guarded end-to-end — a prefs failure must
        never stop the library opening."""
        try:
            remembered = prefs.library_size()
        except Exception as exc:  # noqa: BLE001 — an unreadable pref just means "use the default"
            print(f"studio: library size not restored ({exc!r}).", flush=True)
            remembered = None
        width, height = remembered or _DEFAULT_SIZE
        screen = self.screen() or QGuiApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            width, height = _fit_to_screen(width, height, avail.width(), avail.height())
        self.resize(width, height)
        self._opened_size = (self.width(), self.height())

    def done(self, result: int):
        """Remember a size the user changed on the way out — both Open (accept) and Close/Escape
        (reject) route through here — so a library enlarged to browse 200 recordings does not shrink
        back to the default on the next open. ``prefs.set_library_size`` is itself fully guarded."""
        if (self.width(), self.height()) != getattr(self, "_opened_size", None):
            prefs.set_library_size(self.width(), self.height())
        super().done(result)

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
        """The filter combo's track list: the sorted distinct track names, plus an UNKNOWN-TRACK
        bucket when any entry's circuit isn't in the registry. Those rows have no name to sort in,
        but they are real, openable recordings (see ``_entry_junk``) and were the only rows the
        combo could not reach at all."""
        names = sorted({e["track"] for e in self._entries if e.get("track")})
        if any(not e.get("track") for e in self._entries):
            names.append(_UNKNOWN_TRACK)
        return names

    def _apply_filter(self):
        """Hide rows that don't match the search text (track/date substring) AND the selected track
        filter. Live — wired to both the search box and the combo. Uses ``setRowHidden`` so the sort
        order / selection model stay intact; re-selects the first visible usable row afterward so the
        PB chart + summary track the visible set."""
        query = self.search.text().strip().lower() if hasattr(self, "search") else ""
        chosen = self.track_filter.currentText() if hasattr(self, "track_filter") else _ALL_TRACKS
        visible = 0
        for r in range(self.table.rowCount()):
            date_item = self.table.item(r, _COL_DATE)
            if date_item is None:
                continue
            hay = date_item.data(FILTER_ROLE) or ""
            track = date_item.data(TRACK_ROLE)
            if chosen == _ALL_TRACKS:
                track_ok = True
            elif chosen == _UNKNOWN_TRACK:
                track_ok = not track          # the bucket stands for every unnamed circuit
            else:
                track_ok = track == chosen
            hidden = (bool(query) and query not in hay) or not track_ok
            self.table.setRowHidden(r, hidden)
            visible += not hidden
        # The header and the empty-state describe what's ON SCREEN: a header still claiming "3
        # analyzed recordings" over a table filtered down to nothing is the dialog contradicting
        # itself, and blank space is not a "no matches" message.
        self._update_title(visible)
        self._show_no_matches(visible, query, chosen)
        # Keep a sensible selection: if the selected row got hidden (or none is selected), land on
        # the first VISIBLE usable row so the chart/summary reflect what's on screen.
        self._reselect_visible()

    def _update_title(self, visible: int | None = None):
        """The header count. Names the FILTERED subset ("0 of 3 analyzed recordings") whenever the
        filter is hiding rows, so the header can never assert a count the table doesn't show."""
        total = len(self._entries)
        whole = _plural(total, "analyzed recording")
        self._title.setText(whole if visible is None or visible == total
                            else f"{visible} of {whole}")

    def _show_no_matches(self, visible: int, query: str, chosen: str):
        """The filtered-to-nothing empty state. Only for a FILTERED empty table — an empty library
        is a different (and already handled) state, and gets no "no matches" sentence."""
        filtering = bool(query) or chosen != _ALL_TRACKS
        show = filtering and not visible and bool(self._entries)
        if show:
            term = self.search.text().strip() or chosen
            self._no_matches.setText(
                f"No recordings match “{term}”.\nClear the search or pick “{_ALL_TRACKS}” to see "
                f"all {_plural(len(self._entries), 'recording')}.")
        self._no_matches.setVisible(show)

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
            # An unknown-track row is keyed on the label it SHOWS, so typing what's on screen
            # reaches it (its `track` is null — there is nothing else to match).
            date_item.setData(
                FILTER_ROLE, f"{track or _UNKNOWN_LABEL} {date or ''}".strip().lower())

            # A junk row says so; a present-but-missing-file row keeps its established label. An
            # UNTRUSTWORTHY-but-openable row gets a muted trust tag (provisional/estimated/dropout)
            # so the user can see WHICH sessions the PB chart excludes — reusing the theme's trust
            # tier (italic + PROVISIONAL_COLOR, palette-safe).
            trust = None if disabled else _library.trust_label(e)
            suffix = ("  (no laps)" if junk else "  (file missing)" if missing
                      else f"  · {trust}" if trust else "")
            track_text = f"{track or _UNKNOWN_LABEL}{suffix}"

            track_item = QTableWidgetItem(track_text)

            best_item = _NumItem(fmt_time(best) if best is not None else "—")
            best_item.setData(NUM_ROLE, best)
            theo_item = _NumItem(fmt_time(theo) if theo is not None else "—")
            theo_item.setData(NUM_ROLE, theo)

            items = (date_item, track_item, best_item, theo_item)
            tooltip = _entry_tooltip(e)
            for col, it in enumerate(items):
                # Every cell hovers to the recording's file identity — the columns show only track +
                # date, so hovering anywhere on the row is what tells two same-day sessions apart.
                # Track is the one STRETCH column, so it is the one that elides (31 px of overflow
                # at the dialog's own 489 px minimum width): its tooltip LEADS with its own full
                # label, so the clipped tail is readable rather than merely truncated.
                it.setToolTip(f"{track_text}\n\n{tooltip}" if col == _COL_TRACK else tooltip)
                if disabled:
                    it.setForeground(dim)
                    it.setFlags(it.flags() & ~Qt.ItemIsEnabled & ~Qt.ItemIsSelectable)
                elif col == _COL_TRACK and trust:
                    # Muted + italic across the row's Track cell so the tag reads as demoted, not an
                    # error. The row stays fully selectable/openable — it's just marked, not blocked.
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
            # Only claim PBs once a later session has actually beaten one: the first session on a
            # track sets the bar rather than clearing it, and "0 PBs" would read as a failure.
            if summary["pb_count"]:
                parts.append(_plural(summary["pb_count"], "PB"))
            trend = _TREND_WORD.get(summary["trend"])
            if trend:
                parts.append(trend)
        self._summary.setText("  ·  ".join(parts))

    def _show_pb(self, track: str | None):
        """Plot best-lap-vs-date for `track`: line for >=2 dated bests, a framed single marker for
        1, empty-state for 0. No track means one of two DIFFERENT states — nothing selected, or a
        selected recording whose circuit the track database doesn't know (the common case for a new
        user, and now a selectable row) — so each gets its own sentence instead of asking the user
        to select what they already selected."""
        if not track:
            self._pb_curve.setData([], [])
            self._pb_title.setText("PB progression")
            self._set_pb_axes(False)
            self._set_pb_empty(
                "This recording's track isn't in your database yet, so there's nothing to chart"
                if self._selected_date_item() is not None
                else "Select a recording to see its track's PB progression")
            return
        series = _library.pb_series(self._index, track)
        xs, ys = [], []
        for date, best in series:
            x = _epoch_seconds(date)
            if x is not None:
                xs.append(x)
                ys.append(best)
        self._pb_curve.setData(xs, ys)
        self._set_pb_axes(bool(ys))
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

    def _set_pb_axes(self, plotted: bool):
        """Label the axes only while something is plotted, and drop the range when nothing is.
        ``_frame_single_point`` disables autorange, so a de-selected row otherwise leaves ITS
        numbers (67.771–69.771 s) ticking an empty grid — an axis describing a recording the dialog
        is no longer showing. The empty-state sentence is then the only thing in the plot."""
        for side in ("left", "bottom"):
            self.pb_plot.getAxis(side).setStyle(showValues=plotted)
        if not plotted:
            # A hard unit range, not autoRange(): with no data pyqtgraph's autorange KEEPS the old
            # bounds (67.771–69.771 → merely re-padded to 67.386–70.156). The plotting branches
            # each set their own range back, so nothing has to be restored here.
            self.pb_plot.getPlotItem().getViewBox().setRange(
                xRange=(0.0, 1.0), yRange=(0.0, 1.0), padding=0)

    def _set_pb_empty(self, message: str | None):
        """Show (or hide on None) the centred empty-state label."""
        if not message:
            self._pb_empty.setVisible(False)
            return
        self._pb_empty.setText(message)
        self._pb_empty.setVisible(True)
        self._centre_pb_empty()

    def _centre_pb_empty(self):
        """Put the empty-state label in the middle of the plot. Its pos() is read in the PARENT
        ITEM's (the ViewBox's) coordinates — PIXELS — so it must come from ``boundingRect()``, never
        from the data-space ``viewRect()``. Also wired to the ViewBox's ``sigResized`` so the label
        follows the box when the dialog is resized."""
        vb = self.pb_plot.getPlotItem().getViewBox()
        self._pb_empty.setPos(vb.boundingRect().center())

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
        # Lead with the FILENAME: track + date alone can't tell two same-day unknown-track sessions
        # apart, and this confirm deletes that recording's sidecar.
        ok = QMessageBox.question(
            self, "Forget this recording",
            f"Forget “{_entry_name(entry)}” — {track} ({date})?\n\n"
            "This removes it from the library and deletes its .pacer.json timing-line "
            "sidecar. Your video file is not touched.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._forget_recording(entry)
        self._rerender()

    def _read_backup_info(self) -> dict | None:
        """What the automatic backup holds (a ``library.backup_summary`` dict) via the injected
        query, or None when it isn't wired / there's nothing restorable. Guarded: a failing query
        just means "no restore offered", never a broken dialog."""
        if self._backup_info is None:
            return None
        try:
            info = self._backup_info()
        except Exception as exc:  # noqa: BLE001 — a backup query must never break the library
            print(f"studio: library backup not readable ({exc!r}).", flush=True)
            return None
        return info if isinstance(info, dict) and info.get("entries") else None

    def _sync_restore_btn(self):
        """Enable Restore… only when there IS something to restore, and say which state it's in —
        the tooltip carries the backup's size + date so the button explains itself before it is
        clicked (and explains its own greyed-out state when there is no backup yet)."""
        btn = getattr(self, "restore_btn", None)
        if btn is None:
            return
        info = self._backup
        btn.setEnabled(info is not None)
        if info is None:
            btn.setToolTip(
                "No library backup yet — one is kept automatically as library.json.bak whenever "
                "the library is cleared")
        else:
            btn.setToolTip(
                f"Put back the automatic backup{_backup_when(info.get('mtime'))} "
                f"({_plural(int(info['entries']), 'recording')}). The library you have now is kept "
                "as the backup, so you can swap back")

    def _on_clear_library(self):
        """Confirm, then wipe the whole index via the injected callback (media + sidecars left
        untouched) and re-render to the empty state. The confirm names what SURVIVES (video files,
        sidecars) and — since this is the one destructive control in the app — where the copy of the
        index itself goes, plus the way back: Restore… when it's wired, otherwise the .bak file the
        Reveal in Finder button two along opens the folder for."""
        if self._clear_library is None or not self._entries:
            return
        recovery = ("You can put it back with “Restore…”."
                    if self._restore_library is not None else
                    "“Reveal in Finder” opens the folder that holds it.")
        ok = QMessageBox.question(
            self, "Clear library",
            f"Forget all {_plural(len(self._entries), 'recording')} from the library?\n\n"
            "This wipes the library index only — your video files and their .pacer.json "
            "sidecars are left untouched.\n\n"
            f"A copy of the index is kept as library.json.bak first. {recovery}",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._clear_library()
        self._backup = self._read_backup_info()   # the wipe just created one
        self._rerender()

    def _on_restore_library(self):
        """Confirm, then put the automatic backup back via the injected callback and re-render. The
        confirm names BOTH sides — what is about to be replaced and what replaces it — because a
        restore is destructive in the other direction; it also says the current library becomes the
        backup, which is what makes this reversible."""
        if self._restore_library is None or not self._backup:
            return
        info = self._backup
        ok = QMessageBox.question(
            self, "Restore library",
            f"Replace this library ({_plural(len(self._entries), 'recording')}) with the backup"
            f"{_backup_when(info.get('mtime'))} "
            f"({_plural(int(info['entries']), 'recording')})?\n\n"
            "The library you have now is kept as the backup, so you can swap back. Your video "
            "files and their .pacer.json sidecars are not touched either way.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
        if ok != QMessageBox.Yes:
            return
        self._index = self._restore_library()
        self._backup = self._read_backup_info()   # the swap replaced it with what we just left
        self._rerender()

    def _rerender(self):
        """Rebuild the table + chart from ``self._index`` after a forget/clear. Rebuilds rather than
        surgically deleting one QTableWidget row so the sort keys / role data stay consistent."""
        self._entries = list(self._index.get("entries", []))
        self._update_title()                         # re-run with the visible count by _apply_filter
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
        self._sync_restore_btn()
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
