"""LapTable: lap times / distances / entry speed. Multi-select rows to compare laps.

Cells sort by their numeric Qt.UserRole key, not text (so "1:08.408" sorts as 68.408 s).
Row/cell highlights are keyed by lap id so they survive sorts: ▶ playing marker, green best
lap, blue Qt selection, purple per-sector session-best cells, ⚠ GPS-dropout flag. The
SESSION-BESTS footer is plain labels below the table, immune to sort/selection. A muted ⊘
EXCLUDED strip below the table lists substantial laps the median band left out of the
times/bests (a mis-segmented short/long lap, an out-lap, or an in-lap) — kept out of the
sortable rows so a short excluded lap can't sort to the top as the "fastest".
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

from . import theme, units
from ._signal import fmt_time

if TYPE_CHECKING:  # the injected session — typed for readers, not imported at runtime
    from .session import Session

BASE_COLOR = QColor(theme.C.text)             # default row text
# The two "best" foregrounds are PALETTE-DEPENDENT (green/purple by default, blue/teal in the colour-
# blind palette), so they're resolved per-refresh via theme.best_lap_colour()/best_sector_colour()
# rather than frozen at import — a palette flip then recolours the cells on the next refresh().
CURRENT_PREFIX = "▶ "  # current (playing) lap marker
DROPOUT_SUFFIX = " ⚠"  # GPS-dropout lap (low-confidence)
# NON-COLOUR redundancy for the "best" cues so they read without the green/purple hue (colour
# blindness / greyscale): a ★ marks the overall best lap's Lap cell and each session-best split
# cell. Paired with the existing bold, the star carries the meaning independent of colour.
BEST_LAP_MARK = "★ "     # prefixes the best lap's Lap cell (after any ▶ current marker)
BEST_SECTOR_MARK = " ★"  # suffixes a session-best split cell's value
DROPOUT_TOOLTIP = "GPS dropout in this lap — its time, distance and map are less reliable."
# EXCLUDED laps: substantial laps the median band left OUT of the times / bests (a mis-segmented
# short/long lap, an out-lap, or an in-lap). They're shown in a muted strip BELOW the table rather
# than injected as rows — a short excluded lap would otherwise sort to the top as the "fastest" row
# and re-create the exact confusion the band filter removes. ⊘ reads as "left out" (distinct from
# the ⚠ dropout flag, which marks a lap that IS still counted).
EXCLUDED_MARK = "⊘"
EXCLUDED_TOOLTIP = (
    "These laps were left out of your times, bests and coaching. Their distance is off this "
    "session's median lap — usually a mis-segmented start/finish crossing, an out-lap, or an "
    "in-lap. If a real lap was dropped, drag the start/finish line on the map.")
EXCLUDED_MAX_SHOWN = 6  # cap the listed laps; the rest collapse to a "+N more" line
PROVISIONAL_COLOR = QColor(theme.PROVISIONAL_COLOR)  # muted text for unverified timing
# A short, non-duplicative hint: the actionable "drag the start/finish line" call-to-action lives
# once on the map (the on-canvas cue + the trust strip), so the table tooltip just points there
# rather than repeating the whole sentence a third time.
PROVISIONAL_TOOLTIP = "Provisional timing — see the map to set the start/finish line."
# Degraded TIMING ACCURACY (the data-quality axis, orthogonal to the start-line trust above): the
# start line is fine but the per-sample clock is estimated (media-clock fallback) or many fixes
# were rejected, so the lap Time / S-split cells are estimated — muted like provisional, but the
# best/purple authority is NOT suppressed (the bests are still valid RELATIVE to each other; only
# the absolute timing accuracy is degraded).
ESTIMATED_TIMING_TOOLTIP = ("Timing accuracy degraded — these times are estimated and may be less "
                            "accurate (see the data-quality note over the map).")
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]
_ENTRY_COL = len(COLUMNS) - 1  # the Entry-speed column (last base column); its header names the unit


def _columns(unit: str | None) -> list[str]:
    """The base column headers with the Entry column named in the current speed unit
    ("Entry (km/h)" / "Entry (mph)"). Length is invariant, so every len(COLUMNS) offset holds."""
    cols = list(COLUMNS)
    cols[_ENTRY_COL] = f"Entry ({units.speed_label(unit)})"
    return cols
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
        # Speed display unit (km/h default); the app pushes the persisted choice via set_speed_unit.
        # Drives the Entry column header + the Entry value conversion (a display-only concern —
        # session.lap_rows still returns km/h).
        self._speed_unit = units.DEFAULT_UNIT
        # Highlight caches filled by refresh(): per-column best splits + dropout lap ids + the
        # overall-best lap id (so the ★ best-lap mark on the Lap cell survives sorts/current-lap
        # rewrites, which go through _lap_cell_text).
        self._best_split: list = []
        self._dropout_ids: set = set()
        self._best_lap_id = None

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(_columns(self._speed_unit))
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
        lay.addWidget(self._build_excluded_strip())  # between the table and the SESSION-BESTS footer
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

    def _build_excluded_strip(self) -> QWidget:
        """A muted strip listing laps LEFT OUT of the times/bests by the median band (see
        EXCLUDED_MARK). Hidden entirely when there are none (the clean, common case), so it adds
        no chrome to a normal recording. Kept OUT of the sortable table on purpose — a short
        excluded lap injected as a row would sort to the top as the 'fastest' and re-create the
        very confusion the band filter removes."""
        strip = QWidget()
        strip.setObjectName("LapExcludedStrip")
        strip.setStyleSheet(
            f"QWidget#LapExcludedStrip {{ border-top: 1px solid {theme.C.border}; }}")
        box = QVBoxLayout(strip)
        box.setContentsMargins(10, 6, 10, 8)
        box.setSpacing(2)
        header = QLabel(f"{EXCLUDED_MARK} EXCLUDED")
        header.setProperty("role", "BarLabel")  # the same small uppercase dimmed section type
        header.setToolTip(EXCLUDED_TOOLTIP)
        self._excluded_body = QLabel("")
        self._excluded_body.setWordWrap(True)
        self._excluded_body.setToolTip(EXCLUDED_TOOLTIP)
        # Muted + italic — the provisional/degraded treatment used everywhere else for
        # de-emphasised timing, so "not counted" reads consistently.
        self._excluded_body.setStyleSheet(
            f"color: {theme.PROVISIONAL_COLOR}; font-style: italic;")
        box.addWidget(header)
        box.addWidget(self._excluded_body)
        self._excluded_strip = strip
        strip.setVisible(False)
        return strip

    def _refresh_excluded(self):
        """Populate / hide the excluded-laps strip from Session.excluded_lap_rows (getattr-guarded
        so the lighter test doubles, which don't expose it, simply show no strip). One line per
        excluded lap ("Lap 47 — 0:59.091 · 921 m"), capped at EXCLUDED_MAX_SHOWN with a "+N more"
        tail; the whole strip hides when there are none."""
        rows = getattr(self.session, "excluded_lap_rows", lambda: [])()
        self._excluded_strip.setVisible(bool(rows))
        if not rows:
            self._excluded_body.clear()
            return
        lines = [f"Lap {r['idx']} — {fmt_time(r['time'])} · {r['dist']:.0f} m" for r in rows]
        if len(lines) > EXCLUDED_MAX_SHOWN:
            hidden = len(lines) - EXCLUDED_MAX_SHOWN
            lines = [*lines[:EXCLUDED_MAX_SHOWN], f"+{hidden} more"]
        self._excluded_body.setText("\n".join(lines))

    def _refresh_footer(self):
        """Rewrite footer values from Session; None → em-dash. The SESSION-BESTS tiles (theoretical
        best / best rolling) are reference targets stitched from the session's best splits/loops, so
        they share the lap timing's authority: while the timing is PROVISIONAL (arbitrary start
        line) OR the clock is DEGRADED (media-clock / low-GPS estimate) they're muted + italic with
        the matching tooltip, restored to the normal hero value once Verified AND high-quality."""
        provisional = not self.session.timing_verified
        degraded = self.session.timing_quality.degraded
        muted = provisional or degraded
        note = PROVISIONAL_TOOLTIP if provisional else ESTIMATED_TIMING_TOOLTIP
        for (_title, accessor, tip), label in zip(FOOTER_ROWS, self._footer_values,
                                                   strict=True):
            v = accessor(self.session)
            label.setText(fmt_time(v if v is not None else float("nan")))
            font = label.font()
            font.setItalic(muted)
            label.setFont(font)
            colour = theme.PROVISIONAL_COLOR if muted else theme.C.text
            label.setStyleSheet(f"color: {colour};")
            label.setToolTip(f"{note}\n\n{tip}" if muted else tip)

    def _n_split_cols(self) -> int:
        """Number of S-split columns: sector_count()+1 if any sector lines, else 0."""
        n = self.session.sector_count()
        return n + 1 if n else 0

    def set_speed_unit(self, unit: str):
        """Switch the Entry-speed display unit live: re-header + re-fill (converts the Entry cells).
        No-op if unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.refresh()

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
        headers = _columns(self._speed_unit) + [f"S{i + 1}" for i in range(n_splits)]

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
                # Entry speed: convert km/h → the display unit for BOTH the shown text and the
                # numeric sort key so ordering matches what's on screen (identity for km/h).
                (f"{units.convert_speed(row['entry'], self._speed_unit):.1f}",
                 units.convert_speed(float(row["entry"]), self._speed_unit)),
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
        # The excluded-laps strip + summary footer (theoretical best / best rolling) follow every
        # refresh — i.e. also after a timing-line edit re-segments the laps (which shifts both the
        # valid and the excluded sets).
        self._refresh_excluded()
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

    # The timing columns (Time + the S-split columns) — the cells whose authority depends on the
    # start/finish line. Provisional timing mutes exactly these (Dist/Entry are line-independent).
    def _timing_cols(self) -> set[int]:
        return {1, *(len(COLUMNS) + i for i in range(self._n_split_cols()))}

    def _apply_highlights(self):
        """Re-apply ALL row/cell highlights keyed by lap id, so they survive any sort:
          * green foreground on every cell of the overall best lap,
          * purple foreground+bold on each per-column session-best split cell (F5),
          * the ▶ prefix + bold Lap cell for the current (playing) lap.
        The blue selection is Qt's own row background and is left to the selection model.

        TIMING TRUST: when the session's timing is PROVISIONAL (start line auto-fitted, not
        user-confirmed — see Session.timing_verified) the timing columns (lap Time + the S-split
        cells) are de-emphasized (muted + italic, with the 'provisional' tooltip) and BOTH "best"
        authority cues are suppressed — no purple session-best splits and no green best-lap — since
        a 'best' measured against an arbitrary start line is meaningless. The Dist/Entry columns,
        which don't depend on the start line, stay normal. Verified timing renders as before.

        DATA QUALITY (orthogonal — Session.timing_quality): a media-clock-fallback recording or one
        whose GPS quality gate rejected many fixes ALSO mutes the timing cells (an 'estimated'
        tooltip), but does NOT suppress the bests — the start line is trusted, so the bests stay
        valid RELATIVE to each other; only the absolute timing accuracy is degraded. A normal GPS9,
        clean-fix recording (the common case) leaves both axes untouched."""
        rows = self.table.rowCount()
        if not rows:
            return
        verified = self.session.timing_verified
        degraded = self.session.timing_quality.degraded
        # Overall best lap = the valid lap with the min time (foreground green on all cells) —
        # suppressed entirely while the timing is provisional (but NOT for a merely-degraded clock).
        best_lap = self.session.best_lap_id() if verified else None
        # Cache the best-lap id so the ★ mark on the Lap cell (applied via _lap_cell_text) tracks it;
        # None while provisional so no ★ paints a meaningless "best" against an arbitrary start line.
        self._best_lap_id = best_lap
        n_splits = self._n_split_cols()
        best_split = self._best_split
        timing_cols = self._timing_cols()
        # Palette-dependent "best" foregrounds, resolved per-refresh so a colour-blind-palette flip
        # recolours the cells (green→blue best lap, purple→teal best sector) on the next refresh().
        best_color = QColor(theme.best_lap_colour())
        best_sector_color = QColor(theme.best_sector_colour())

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
                provisional_cell = not verified and c in timing_cols
                # A degraded-clock timing cell mutes too, but only when NOT already provisional
                # (provisional is the stronger demotion + suppresses the bests; degraded keeps them).
                estimated_cell = verified and degraded and c in timing_cols
                muted_cell = provisional_cell or estimated_cell
                # base off-white; green (best lap) / muted (provisional or estimated timing) / purple.
                if muted_cell:
                    item.setForeground(PROVISIONAL_COLOR)
                else:
                    item.setForeground(best_color if is_best else BASE_COLOR)
                # Muted+italic on any demoted timing cell; the dropout tooltip wins (it flags a
                # per-lap issue), else the provisional note, else the estimated-timing note, else clear.
                theme.apply_provisional_style(item, muted_cell)
                item.setToolTip(DROPOUT_TOOLTIP if is_dropout
                                else PROVISIONAL_TOOLTIP if provisional_cell
                                else ESTIMATED_TIMING_TOOLTIP if estimated_cell else "")
            # per-sector best → purple+bold + a ★ mark (outranks green for this cell) — but ONLY on
            # verified timing; a "validated best" on an arbitrary start line would mislead. The ★ is
            # the NON-COLOUR redundancy (bold alone is weak); the split text is rebuilt from the
            # stored numeric key each pass so the mark toggles cleanly across sorts (no double-★).
            for i in range(n_splits):
                c = len(COLUMNS) + i
                item = self.table.item(r, c)
                if item is None:
                    continue
                key = item.data(NUM_ROLE)
                target = best_split[i] if i < len(best_split) else None
                font = item.font()
                is_best_split = (verified and target is not None and key is not None
                                 and math.isfinite(float(key))
                                 and abs(float(key) - target) < 1e-9)
                if key is not None and math.isfinite(float(key)):
                    base = f"{float(key):.2f}"
                    item.setText(base + BEST_SECTOR_MARK if is_best_split else base)
                if is_best_split:
                    item.setForeground(best_sector_color)
                    font.setBold(True)
                else:
                    font.setBold(False)
                item.setFont(font)
        self.table.blockSignals(False)
        self._apply_current_lap()

    def _lap_cell_text(self, lap_id, on: bool) -> str:
        """The Lap-cell text for `lap_id`: a '▶ ' prefix when it's the current (playing) lap, a '★ '
        mark when it's the overall best lap (the NON-COLOUR redundancy for the green best-lap row —
        reads without hue), and a trailing ' ⚠' low-confidence marker on a GPS-dropout lap. The ▶
        current marker leads the ★ so the playing lap is always identifiable first."""
        prefix = CURRENT_PREFIX if on else ""
        best = BEST_LAP_MARK if lap_id == self._best_lap_id else ""
        suffix = DROPOUT_SUFFIX if lap_id in self._dropout_ids else ""
        return f"{prefix}{best}{lap_id}{suffix}"

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
# move to per-column header tooltips (_corner_col_tips).
CORNER_COLUMNS = ["Corner", "Time", "Δ best", "Apex", "Δ apex", "Entry", "Exit",
                  theme.estimated_label("Grip")]


def _corner_col_tips(unit: str | None) -> list[str]:
    """Full meaning + units per header, shown on hover (1:1 with CORNER_COLUMNS). The four speed
    tips name the current display unit ("km/h" / "mph"); the rest are unit-independent."""
    u = units.speed_label(unit)
    return [
        "Detected corner in track order (⟲ left / ⟳ right)",
        "Time spent in the corner (seconds)",
        "Δ vs the best lap's same corner (seconds; − is faster)",
        f"Apex (minimum) speed through the corner ({u})",
        f"Δ apex speed vs the best lap ({u}; + is faster)",
        f"Corner entry speed ({u})",
        f"Corner exit speed ({u})",
        # ESTIMATED, not measured: the friction circle mixes the noisier longitudinal axis, so this is
        # lateral-dominant. Numerator and divisor share the SAME validated axes (clean GPS-derived
        # longitudinal + IMU lateral). Normalised to the SESSION envelope (not each lap's own peak) so a
        # slow lap reads genuinely lower; ~100% means at this session's grip limit (it can read a little
        # over when a corner sits just past the robust p98 envelope).
        "Grip utilisation (ESTIMATED): median combined |g| in the corner vs the session friction-circle "
        "envelope (%). Estimated from the clean GPS-derived longitudinal + IMU lateral g; ~100% = at the "
        "session's grip limit. Normalised session-wide so a slower lap reads lower.",
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
        # Speed display unit (km/h default); app pushes the persisted choice via set_speed_unit.
        # Drives the Apex/Δ apex/Entry/Exit value conversion + the per-column tooltips' unit name.
        self._speed_unit = units.DEFAULT_UNIT
        self.table = QTableWidget(0, len(CORNER_COLUMNS))
        self.table.setHorizontalHeaderLabels(CORNER_COLUMNS)
        self._apply_corner_tips()
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

    def _apply_corner_tips(self):
        """(Re)apply the per-column header tooltips for the current speed unit."""
        for c, tip in enumerate(_corner_col_tips(self._speed_unit)):
            if tip:
                self.table.horizontalHeaderItem(c).setToolTip(tip)

    def set_speed_unit(self, unit: str):
        """Switch the corner speed display unit live: re-tooltip + re-fill the speed cells. No-op
        if unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self._apply_corner_tips()
        self.refresh()

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
            # Speeds convert km/h → the display unit at the cell boundary (identity for km/h);
            # apex Δ is a speed difference so it scales by the same factor. Δ COLOURS keep the raw
            # km/h delta (sign/magnitude threshold is unit-agnostic — a factor never flips it).
            u = self._speed_unit
            conv = units.convert_speed
            cells: list[tuple[str, str | None]] = [
                (f"{c.label} {CORNER_DIR_GLYPH.get(c.direction, '')}", None),
                (f"{st.time:.2f}", None),
                (f"{st.delta:+.2f}", theme.delta_colour(st.delta)),
                (f"{conv(st.apex_speed, u):.1f}", None),
                # Apex-speed Δ: FASTER through the corner is better, so the shared Δ colour
                # rule (negative = green) is applied to the NEGATED speed delta.
                (f"{conv(st.apex_speed_delta, u):+.1f}", theme.delta_colour(-st.apex_speed_delta)),
                (f"{conv(st.entry_speed, u):.1f}", None),
                (f"{conv(st.exit_speed, u):.1f}", None),
                (grip_pct, None),
            ]
            is_best = bool(bests) and r < len(bests) and abs(st.time - bests[r]) < 1e-9
            for col, (text, colour) in enumerate(cells):
                # session-best corner time also carries the ★ non-colour mark (matches the lap
                # table's session-best split cells) so "this is the best" reads without the hue.
                if col == 1 and is_best:
                    text = text + BEST_SECTOR_MARK
                item = QTableWidgetItem(text)
                item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                if col >= NUMERIC_COL_START:
                    item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                    item.setFont(self._num_font)
                # session-best corner time: palette best-sector colour + bold, outranks the Δ colour
                if col == 1 and is_best:
                    item.setForeground(QColor(theme.best_sector_colour()))
                    font = item.font()
                    font.setBold(True)
                    item.setFont(font)
                elif colour:
                    item.setForeground(QColor(colour))
                else:
                    item.setForeground(BASE_COLOR)
                self.table.setItem(r, col, item)
