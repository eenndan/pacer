"""LapTable: lap times / distances / entry speed. Multi-select rows to compare laps.

Cells sort by their numeric Qt.UserRole key, not text (so "1:08.408" sorts as 68.408 s).
Row/cell highlights are keyed by lap id so they survive sorts: ▶ playing marker, green best
lap, blue Qt selection, purple per-sector session-best cells, ⚠ GPS-dropout flag. The
SESSION-BESTS footer is plain labels below the table, immune to sort/selection.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme
from ._signal import fmt_time

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

BASE_COLOR = QColor(theme.C.text)             # default row text
BEST_COLOR = QColor(theme.C.ahead)            # overall best lap
BEST_SECTOR_COLOR = QColor(theme.C.best)      # per-column session-best split
CURRENT_PREFIX = "▶ "  # current (playing) lap marker
DROPOUT_SUFFIX = " ⚠"  # GPS-dropout lap (low-confidence)
DROPOUT_TOOLTIP = "GPS dropout in this lap — its time, distance and map are less reliable."
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]
# Columns 1.. (everything but the Lap column) hold numerics: right-align + tabular font so the
# digits column-align. The Lap column stays left/default.
NUMERIC_COL_START = 1
NUM_ROLE = Qt.UserRole  # the numeric sort key stored on every cell
LAP_ROLE = Qt.UserRole + 1  # the lap id (stable across sorts), stored on the Lap cell

# (title, accessor s->value|None, tooltip) for the SESSION-BESTS footer tiles. Values come from
# Session (theoretical_best / best_rolling_lap) so the footer and the purple per-sector cells share
# one computation and can't disagree. The callable accessor (vs a method-name string) makes a
# renamed Session method a load-time error, not a silent footer miss.
FOOTER_ROWS = (
    ("Theoretical", lambda s: s.theoretical_best(),
     "Theoretical best — sum of the session-best sector splits (the purple cells): the lap "
     "you'd drive by stitching every best sector together. With no sector lines this equals "
     "the best lap time."),
    ("Best rolling", lambda s: s.best_rolling_lap(),
     "Best rolling — the fastest single complete loop regardless of where it starts: the "
     "minimum time from passing any track position to passing it again one lap later (windows "
     "spanning a GPS-dropout ⚠ lap are excluded)."),
)


def _is_blank(v) -> bool:
    """A cell key is "blank" when it's absent or NaN (a partial lap with fewer splits)."""
    return v is None or (isinstance(v, float) and math.isnan(v))


class _NumItem(QTableWidgetItem):
    """A table cell that sorts by a numeric key (Qt.UserRole), not its text. Blank/NaN keys sort
    LAST in BOTH directions: LapTable sets `_descending` before each sort so blanks survive Qt's
    descending reversal."""

    _descending = False  # active sort direction, set by LapTable before each sort

    def __lt__(self, other: QTableWidgetItem) -> bool:  # noqa: D401 (Qt sort hook)
        a = self.data(NUM_ROLE)
        b = other.data(NUM_ROLE)
        a_blank = _is_blank(a)
        b_blank = _is_blank(b)
        if a_blank or b_blank:
            if a_blank and b_blank:
                return False  # two blanks: equal, stable order
            # Flip the blank ordering by direction so blanks land LAST after Qt's descending reversal.
            if a_blank:        # self is the blank
                return self._descending
            return not self._descending  # other is the blank, self is real
        return float(a) < float(b)


class LapTable(QWidget):
    laps_selected = Signal(object)  # list[int]

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._current_lap = None  # the lap on the video (independent of selection)
        # Highlight caches filled by refresh(): per-column best splits + dropout lap ids.
        self._best_split: list = []
        self._dropout_ids: set = set()

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self._num_font = theme.mono_font(theme.TABLE)
        # Default sort = lap# ascending; remembered across refreshes, re-applied after each sort.
        self._sort_col = 0
        self._sort_order = Qt.AscendingOrder
        self.table.setSortingEnabled(True)
        hdr = self.table.horizontalHeader()
        hdr.setSortIndicatorShown(True)
        hdr.setSortIndicator(self._sort_col, self._sort_order)
        hdr.sortIndicatorChanged.connect(self._on_sorted)
        self.table.itemSelectionChanged.connect(self._on_selection)

        # Empty state: zero valid laps would show a blank grid, so stack a placeholder and flip to
        # it in refresh().
        self._empty = QLabel(
            "No complete laps in this recording.\n\n"
            "The GPS may not have locked, or the recording is too short to "
            "cross the start/finish line.")
        self._empty.setProperty("role", "EmptyState")
        self._empty.setAlignment(Qt.AlignCenter)
        self._empty.setWordWrap(True)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.table)   # index 0: the populated table
        self._stack.addWidget(self._empty)  # index 1: the empty-state placeholder

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self._stack)
        lay.addWidget(self._build_footer())
        self.refresh()

    # ------------------------------------------------------------------ build
    def _build_footer(self) -> QWidget:
        """Build the SESSION-BESTS footer: a section divider over one stat tile per FOOTER_ROWS
        entry (dim caption + hero value). Plain labels below the table so values can never
        sort/select. Values use neutral text (purple is reserved for the sector-best cells)."""
        footer = QWidget()
        footer.setObjectName("LapTableFooter")
        # hairline + surface bg so it reads as a designed footer
        footer.setStyleSheet(
            f"QWidget#LapTableFooter {{ border-top: 1px solid {theme.C.border}; "
            f"background-color: {theme.C.surface}; }}")
        outer = QVBoxLayout(footer)
        outer.setContentsMargins(10, 6, 10, 8)
        outer.setSpacing(4)
        # Section divider: the small uppercase dimmed header type (the panel's BarLabel role) so
        # the block announces itself the way every other panel section does.
        header = QLabel("SESSION BESTS")
        header.setProperty("role", "BarLabel")
        header.setToolTip("Reference targets composed from this session's best sectors / loops "
                          "— not lap times you actually drove.")
        outer.addWidget(header)

        tiles = QHBoxLayout()
        tiles.setContentsMargins(0, 0, 0, 0)
        tiles.setSpacing(20)
        hero_num = theme.mono_font(theme.HERO - 5, theme.W_SEMIBOLD)  # a clear step up from 13px
        self._footer_values: list[QLabel] = []
        for title, _accessor, tip in FOOTER_ROWS:  # _accessor (the value callable) used in _refresh_footer
            tile = QVBoxLayout()
            tile.setContentsMargins(0, 0, 0, 0)
            tile.setSpacing(0)
            caption = QLabel(title)
            caption.setStyleSheet(
                f"color: {theme.C.text_dim}; font-size: {theme.CAPTION}px;")
            caption.setToolTip(tip)
            value = QLabel(fmt_time(float("nan")))
            value.setFont(hero_num)
            value.setStyleSheet(f"color: {theme.C.text};")  # neutral, not the sector-best purple
            value.setToolTip(tip)
            tile.addWidget(caption)
            tile.addWidget(value)
            tiles.addLayout(tile)
            self._footer_values.append(value)
        tiles.addStretch(1)
        outer.addLayout(tiles)
        return footer

    def _refresh_footer(self):
        """Rewrite footer values from Session; None → em-dash."""
        for (_title, accessor, _tip), label in zip(FOOTER_ROWS, self._footer_values,
                                                   strict=True):
            v = accessor(self.session)
            label.setText(fmt_time(v if v is not None else float("nan")))

    def _n_split_cols(self) -> int:
        """Number of S-split columns: sector_count()+1 if any sector lines, else 0."""
        n = self.session.sector_count()
        return n + 1 if n else 0

    def refresh(self):
        rows = self.session.lap_rows()

        # E1: flip to the centred empty-state placeholder when there are no laps to show (else the
        # populated table). Done first so the panel never flashes a blank grid; the footer below
        # the stack refreshes to em-dashes on its own (every accessor returns None with no laps).
        self._stack.setCurrentIndex(1 if not rows else 0)

        # N sector lines split each lap into N+1 sub-sectors; show one split column per
        # sub-sector (none by default = today's 4 columns). Column count depends on this,
        # so set the headers here — refresh() runs on selection and after sectors change.
        n_splits = self._n_split_cols()
        headers = COLUMNS + [f"S{i + 1}" for i in range(n_splits)]

        # Per-lap splits + per-column session-best (same accessor the footer sums, so cells/footer agree).
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
        # dropout lap ids, keyed by lap id so the ⚠ flag follows the lap across sorts
        self._dropout_ids = self.session.dropout_lap_ids()
        self._apply_highlights()
        # The summary footer (theoretical best / best rolling) follows every refresh — i.e.
        # also after a timing-line edit re-segments the laps.
        self._refresh_footer()

    # ------------------------------------------------------------- highlights
    def _lap_id(self, r: int) -> int:
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
                # base off-white; green/purple override below
                item.setForeground(BEST_COLOR if is_best else BASE_COLOR)
                # dropout row tooltip
                item.setToolTip(DROPOUT_TOOLTIP if is_dropout else "")
            # per-sector best → purple+bold (outranks green for this cell)
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
        """Full-rebuild path: rewrite every Lap cell's ▶ prefix/bold for the current lap (after
        refresh/sort, where row identities may have changed). set_current_lap has the per-tick
        two-row fast path."""
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
        """Mark the lap playing on the video (no effect on selection). Fast path: only the old and
        new current-lap rows are touched."""
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


# ===================================================================== Corners mode
# Rows = detected corners (track order), cols = the selected lap's per-corner metrics vs the best
# lap (session.lap_corner_stats). A separate widget stacked with LapTable; shares only the module
# display constants. Headers are abbreviated so all 8 columns fit the narrow panel — dropped units
# move to per-column header tooltips (CORNER_COL_TIPS).
CORNER_COLUMNS = ["Corner", "Time", "Δ best", "Apex", "Δ apex", "Entry", "Exit", "Grip"]
# Full meaning + units per header, shown on hover (1:1 with CORNER_COLUMNS).
CORNER_COL_TIPS = [
    "Detected corner in track order (⟲ left / ⟳ right)",
    "Time spent in the corner (seconds)",
    "Δ vs the best lap's same corner (seconds; − is faster)",
    "Apex (minimum) speed through the corner (km/h)",
    "Δ apex speed vs the best lap (km/h; + is faster)",
    "Corner entry speed (km/h)",
    "Corner exit speed (km/h)",
    "Friction-circle grip utilisation: median |g| vs the lap envelope (%)",
]
CORNER_DIR_GLYPH = {1: "⟲", -1: "⟳"}  # left / right (turn sense), shown after the C-label


class CornerTable(QWidget):
    """Corners-mode table: one row per detected corner for the selected lap.

    Session-best corner time is purple+bold; Δ columns use the shared delta colour.
    Read-only/unsorted — track order is the meaning."""

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._lap_id: int | None = None
        self.table = QTableWidget(0, len(CORNER_COLUMNS))
        self.table.setHorizontalHeaderLabels(CORNER_COLUMNS)
        for c, tip in enumerate(CORNER_COL_TIPS):
            if tip:
                self.table.horizontalHeaderItem(c).setToolTip(tip)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Corner name stretches; numeric columns size to content so all 8 fit with no scrollbar.
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.Stretch)
        for c in range(1, len(CORNER_COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self._num_font = theme.mono_font(theme.TABLE)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)

    def set_lap(self, lap_id: int | None):
        """Show the corners of `lap_id` (None clears). No-op when unchanged — called per
        selection change AND from the auto-follow edge, so it must be cheap when idle."""
        if lap_id == self._lap_id:
            return
        self._lap_id = lap_id
        self.refresh()

    def refresh(self):
        """Rebuild the rows from the session's corner model (e.g. after a timing-line edit
        re-segmented the laps and the corner set/stats were recomputed)."""
        # Range-guard the lap id: a re-segmentation can shrink the lap count while this view
        # still holds the previous selection (app re-selects right after; until then, empty).
        ok = self._lap_id is not None and 0 <= self._lap_id < self.session.lap_count()
        stats = self.session.corners.lap_corner_stats(self._lap_id) if ok else []
        corner_list = self.session.corners.corner_list() if stats else []
        bests = self.session.corners.corner_session_bests() if stats else []
        # Per-corner grip utilisation (%); [] when there's no g signal → the column shows a dash.
        grip = self.session.driving.lap_corner_grip(self._lap_id) if stats else []
        self.table.setRowCount(len(stats))
        for r, st in enumerate(stats):
            c = corner_list[r]
            grip_pct = f"{grip[r] * 100:.0f}" if r < len(grip) else "–"
            cells: list[tuple[str, str | None]] = [
                (f"{c.label} {CORNER_DIR_GLYPH.get(c.direction, '')}", None),
                (f"{st.time:.2f}", None),
                (f"{st.delta:+.2f}", theme.delta_colour(st.delta)),
                (f"{st.apex_speed:.1f}", None),
                # Apex-speed Δ: FASTER through the corner is better, so the shared Δ colour
                # rule (negative = green) is applied to the NEGATED speed delta.
                (f"{st.apex_speed_delta:+.1f}", theme.delta_colour(-st.apex_speed_delta)),
                (f"{st.entry_speed:.1f}", None),
                (f"{st.exit_speed:.1f}", None),
                (grip_pct, None),
            ]
            is_best = bool(bests) and r < len(bests) and abs(st.time - bests[r]) < 1e-9
            for col, (text, colour) in enumerate(cells):
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col >= NUMERIC_COL_START:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setFont(self._num_font)
                # session-best corner time: purple+bold, outranks the Δ colour
                if col == 1 and is_best:
                    item.setForeground(BEST_SECTOR_COLOR)
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif colour:
                    item.setForeground(QColor(colour))
                else:
                    item.setForeground(BASE_COLOR)
                self.table.setItem(r, col, item)
