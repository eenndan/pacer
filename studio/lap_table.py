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

from PySide6.QtCore import QEvent, QItemSelection, QItemSelectionModel, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLabel,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import theme, units
from ._signal import fmt_time, lap_label

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
# L3: the most laps that can be overlaid on the speed/Δ charts at once. Beyond this the speed-plot
# legend silently overflows/truncates (laps past ~13 get no entry) and the curves blanket each other,
# so a larger selection is TRIMMED to the fastest MAX_COMPARE_LAPS — a visible cap in the table (the
# excess rows deselect), never a silent chart-side drop. 6 is the sensible side-by-side count.
MAX_COMPARE_LAPS = 6
PROVISIONAL_COLOR = QColor(theme.PROVISIONAL_COLOR)  # muted text for unverified timing
# A short, non-duplicative hint: the actionable "drag the start/finish line" call-to-action lives
# once on the map (the on-canvas cue + the trust strip), so the table tooltip just points there
# rather than repeating the whole sentence a third time.
PROVISIONAL_TOOLTIP = "Provisional timing — see the map to set the start/finish line."
# Degraded TIMING ACCURACY (the data-quality axis, orthogonal to the start-line trust above): the
# start line is fine but the per-sample clock is estimated (media-clock fallback) or many fixes
# were rejected, so the lap Time / S-split cells are demoted — muted like provisional, but the
# best/purple authority is NOT suppressed (the bests are still valid RELATIVE to each other; only
# the absolute timing accuracy is degraded). The tooltip copy is CLOCK-AWARE and derives from the
# SHARED data_quality.TimingQuality.detail() (the same source the map banner + header chip read),
# so the table and map can never disagree and the wording doesn't overclaim "estimated" on a
# true-clock recording whose only concern is rejected fixes (M3). A blank fallback keeps the lighter
# test doubles (which expose no timing_quality) working.
_ESTIMATED_TIMING_FALLBACK = ("Timing accuracy degraded — these times may be less accurate "
                              "(see the data-quality note over the map).")


def estimated_timing_tooltip(timing_quality) -> str:
    """The degraded-timing Time/footer tooltip for `timing_quality`, from the shared clock-aware
    detail() so the table matches the map banner + header chip. Falls back to a generic line if a
    (test-double) session exposes no detail()/summary()."""
    detail = getattr(timing_quality, "detail", None)
    text = detail() if callable(detail) else ""
    return text or _ESTIMATED_TIMING_FALLBACK
COLUMNS = ["Lap", "Time", "Dist (m)", "Entry (km/h)"]
_ENTRY_COL = len(COLUMNS) - 1  # the Entry-speed column (last base column); its header names the unit


def _lap_col_tips(unit: str | None) -> list[str]:
    """Full meaning + units per base header, shown on hover (1:1 with COLUMNS). A narrow panel
    ELIDES a header (see _fit_columns), so every header must carry its own full text somewhere —
    the same contract the Corners table has had since its headers were abbreviated."""
    u = units.speed_label(unit)
    return [
        "Lap number (▶ playing · ★ session best · ⚠ GPS dropout)",
        "Lap time, measured between start/finish crossings",
        "Lap distance (m), measured between start/finish crossings",
        f"Speed at the start/finish crossing ({u})",
    ]
# COLUMN SIZING (P5). The data columns are CONTENT-TIGHT and one blank trailing SPACER column
# absorbs every leftover pixel, so a wide panel keeps the real columns adjacent + left-packed
# instead of flinging the last one to the far right across a dead band (what setStretchLastSection
# did: on a wide panel the Entry column ballooned past 300px with its values pinned right).
# The spacer holds no cells; it exists purely to eat the slack (and to carry the alternating row
# stripe + row selection across the full table width). It is ALWAYS the last column, so it moves
# as the dynamic S-split columns come and go — see _n_real_cols / _apply_column_sizing.
SPACER_HEADER = ""
# How far the spacer may COLLAPSE when the panel has no slack to give it. Qt's default minimum
# section size (~17px here) is enough to push a table that would otherwise just fit into a
# horizontal scrollbar — the spacer must never be the thing that summons one. It only floors
# hand-dragged sections (the data columns size themselves), so a few pixels is safe.
MIN_SECTION_PX = 4
# The Lap column is Interactive with a fixed start width (the CornerTable precedent below), NOT
# ResizeToContents: its text carries the ▶ current-lap marker, which appears/disappears every lap
# during playback and would make a content-sized column visibly jitter. The width fits the widest
# decorated label — "▶ ★ 100 ⚠" — with room to spare. It is the row IDENTITY column, so the squeeze
# pass below never takes a pixel off it; only the GROW pass lets it share a wide panel's surplus.
LAP_COL_PX = 92
# COLUMN FITTING (QA L2-06 / L3-03 / L3-04). Content-tight columns alone fail at BOTH ends of the
# size range:
#   * too wide — a maximized Laps panel parked 1050 of its 1432px in the blank spacer, leaving a
#     78.6% empty screen (four columns totalling 382px);
#   * too narrow — the default 447px quadrant could not hold the Corners table's 501px of columns
#     (the "Grip (est)" header painted "Gr", 0 of 12 grip cells readable) nor the 609px the app's
#     own "Add sector" button creates (S2 and S3 at ZERO visible pixels).
# So both tables now FIT their columns to the panel: grow into surplus width up to MAX_DATA_COL_PX,
# and give slack back down to each column's own CELL width when the panel is short. Headers elide
# before values do — every header carries its full text as a tooltip (_lap_col_tips /
# _corner_col_tips), a cell's value has nowhere else to go.
# The cap is what keeps P5's finding fixed: the old stretched-last-section ballooned ONE column past
# 300px with its values pinned to the far right. 240px is wide enough to fill a maximized quadrant
# and narrow enough to keep every header over its own number.
MAX_DATA_COL_PX = 240


def _shrink_to(widths: list[int], floors: list[int], excess: int):
    """Take `excess` px off `widths` in place, proportionally to each column's slack above its
    floor, never below the floor. Stops early when every column is at its floor (the panel is
    genuinely too narrow — the horizontal scrollbar covers the rest)."""
    while excess > 0:
        idx = [i for i in range(len(widths)) if widths[i] > floors[i]]
        if not idx:
            return
        pool = sum(widths[i] - floors[i] for i in idx)
        moved = 0
        for i in idx:
            take = min(widths[i] - floors[i], max(1, round(excess * (widths[i] - floors[i]) / pool)),
                       excess - moved)
            widths[i] -= take
            moved += take
            if moved == excess:
                break
        if not moved:
            return
        excess -= moved


def _grow_to(widths: list[int], caps: list[int], surplus: int):
    """Share `surplus` px across `widths` in place, evenly, each column capped at its own cap.
    Leftover (every column capped) is the caller's — the lap table parks it in the blank spacer."""
    while surplus > 0:
        idx = [i for i in range(len(widths)) if widths[i] < caps[i]]
        if not idx:
            return
        moved = 0
        for i in idx:
            give = min(caps[i] - widths[i], max(1, surplus // len(idx)), surplus - moved)
            widths[i] += give
            moved += give
            if moved == surplus:
                break
        if not moved:
            return
        surplus -= moved


def fit_columns(natural: list[int], floors: list[int], caps: list[int], avail: int) -> list[int]:
    """The widths `natural` columns should take in `avail` px: grown (capped) when there is spare
    room, squeezed toward `floors` when there is not, unchanged when it already fits. Pure
    arithmetic — no Qt — so the layout contract is unit-testable."""
    widths = list(natural)
    total = sum(widths)
    if total > avail:
        _shrink_to(widths, floors, total - avail)
    elif total < avail:
        _grow_to(widths, caps, avail - total)
    return widths


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

# The two stitched targets that used to sit in a SESSION-BESTS footer below this table
# (theoretical best / best rolling) now live on the Stats page, each beside the data it is derived
# from: `stats_panel.t_theoretical` inside SECTORS (whose bests it sums) and `t_rolling` in PACE.
# The footer cost this grid 63px — two lap rows — on every recording, could not be collapsed or
# hidden, and repeated numbers the Stats page was already the home for.


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


class _ExcludedStrip(QWidget):
    """The ⊘ excluded-laps strip container whose WHOLE surface is a click target: a left-click
    toggles it between the collapsed one-liner and the full list (via the injected ``on_click``).
    A plain, muted info strip — deliberately NOT a sortable lap row (a short excluded lap injected
    as a row would sort to the top as the 'fastest')."""

    def __init__(self, on_click):
        super().__init__()
        self._on_click = on_click
        self.setCursor(Qt.PointingHandCursor)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._on_click()
            event.accept()
            return
        super().mousePressEvent(event)


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
        # How many S-split columns the last refresh() drew, so a NEW one can be scrolled into view
        # once (L3-04) without every other refresh yanking the horizontal scroll.
        self._n_splits_shown = 0

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(_columns(self._speed_unit))
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # P5: no stretched last section — the data columns are content-tight and the blank trailing
        # spacer column takes the slack (see SPACER_HEADER / _apply_column_sizing). The Lap column's
        # start width is set once here so a later user drag survives every refresh().
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.horizontalHeader().setMinimumSectionSize(MIN_SECTION_PX)
        # QHeaderView defaults to ElideNone, so a squeezed header CENTRE-CLIPS its own label
        # ("Dist (m)" -> "ist (m", "Grip (est)" -> "rip (est") with no cue that anything is
        # missing — the exact silent-truncation shape L3-03 filed. Elide it instead; the full text
        # is on the header tooltip (_lap_col_tips / _corner_col_tips).
        self.table.horizontalHeader().setTextElideMode(Qt.ElideRight)
        # Qt falls back to the DEFAULT section size for a Stretch section when there's nothing left
        # to stretch (a table already wide enough to scroll), which would append a phantom 100px of
        # empty scroll range past the last data column. Every data column sizes itself, so the
        # default only ever applies to the spacer — make it collapse instead.
        self.table.horizontalHeader().setDefaultSectionSize(MIN_SECTION_PX)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Interactive)
        self.table.horizontalHeader().resizeSection(0, LAP_COL_PX)
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
        self.table.viewport().installEventFilter(self)   # re-fit on every real width change

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
        # Stretch 1 on the lap grid: the excluded strip keeps its compact size and every extra
        # pixel of panel height becomes visible lap rows (without this, Qt split spare height
        # between them and the grid never grew).
        lay.addWidget(self._stack, 1)
        lay.addWidget(self._build_excluded_strip())
        self.refresh()

    # ------------------------------------------------------------------ build
    def _build_excluded_strip(self) -> QWidget:
        """A muted strip listing laps LEFT OUT of the times/bests by the median band (see
        EXCLUDED_MARK). COLLAPSED by default to a single muted one-liner ("⊘ N excluded ▸"); a
        click on the header expands it to the full per-lap list and back. Hidden entirely when there
        are none (the clean, common case), so it adds no chrome to a normal recording. Kept OUT of
        the sortable table on purpose — a short excluded lap injected as a row would sort to the top
        as the 'fastest' and re-create the very confusion the band filter removes.

        The header is a click target (a plain muted info strip, NOT a sortable lap row): clicking it
        toggles the collapse. The whole strip's VISIBILITY is a separate, orthogonal concern — the
        View ▸ Show excluded laps menu toggle (set_excluded_visible) and the auto-hide when there
        are no excluded laps — so a menu-hidden strip stays hidden regardless of collapse state."""
        strip = _ExcludedStrip(self._toggle_excluded_collapsed)
        strip.setObjectName("LapExcludedStrip")
        strip.setStyleSheet(
            f"QWidget#LapExcludedStrip {{ border-top: 1px solid {theme.C.border}; }}")
        box = QVBoxLayout(strip)
        box.setContentsMargins(10, 6, 10, 8)
        box.setSpacing(2)
        # The collapsed one-liner header ("⊘ N excluded ▸"): muted, uppercase section type, with the
        # ▸/▾ chevron glyph telling which way a click goes. Text is set live by _refresh_excluded.
        self._excluded_header = QLabel("")
        self._excluded_header.setProperty("role", "BarLabel")
        self._excluded_header.setToolTip(EXCLUDED_TOOLTIP)
        self._excluded_body = QLabel("")
        self._excluded_body.setWordWrap(True)
        self._excluded_body.setToolTip(EXCLUDED_TOOLTIP)
        # Muted + italic — the provisional/degraded treatment used everywhere else for
        # de-emphasised timing, so "not counted" reads consistently.
        self._excluded_body.setStyleSheet(
            f"color: {theme.PROVISIONAL_COLOR}; font-style: italic;")
        box.addWidget(self._excluded_header)
        box.addWidget(self._excluded_body)
        self._excluded_strip = strip
        # Two orthogonal flags: the median band produced excluded laps AND the user hasn't hidden the
        # strip via the View menu. Default collapsed (the one-liner) + shown (when there are any).
        self._excluded_collapsed = True     # the ⊘ N excluded ▸ one-liner (click to expand)
        self._excluded_menu_visible = True  # the View ▸ Show excluded laps toggle
        strip.setVisible(False)
        return strip

    def _toggle_excluded_collapsed(self):
        """Header-click handler: flip the excluded strip between the collapsed one-liner and the
        full per-lap list, then re-render. A plain info-strip affordance — it never selects a lap
        or touches the sortable table."""
        self._excluded_collapsed = not self._excluded_collapsed
        self._refresh_excluded()

    def set_excluded_visible(self, on: bool):
        """View ▸ Show excluded laps: fully show/hide the ⊘ excluded strip (header included). Kept
        distinct from the header's own collapse: this is the "hide it entirely" menu toggle, so a
        hidden strip stays hidden regardless of collapse state (and the strip is still auto-hidden
        when the session has no excluded laps). Driven by the window's persisted choice."""
        self._excluded_menu_visible = bool(on)
        self._refresh_excluded()

    def _refresh_excluded(self):
        """Populate / hide the excluded-laps strip from Session.excluded_lap_rows (getattr-guarded
        so the lighter test doubles, which don't expose it, simply show no strip). COLLAPSED (the
        default) shows just the "⊘ N excluded ▸" one-liner; EXPANDED shows one line per excluded lap
        ("Lap 47 — 0:59.091 · 921 m"), capped at EXCLUDED_MAX_SHOWN with a "+N more" tail. The whole
        strip hides when there are none OR when the View-menu toggle hid it."""
        rows = getattr(self.session, "excluded_lap_rows", lambda: [])()
        # Shown only when there ARE excluded laps AND the user hasn't hidden the strip via the menu.
        self._excluded_strip.setVisible(bool(rows) and self._excluded_menu_visible)
        if not rows:
            self._excluded_header.setText("")
            self._excluded_body.clear()
            return
        # The one-liner header: count + a chevron pointing the way a click goes (▸ expand / ▾ collapse).
        chevron = "▸" if self._excluded_collapsed else "▾"
        self._excluded_header.setText(f"{EXCLUDED_MARK} {len(rows)} excluded {chevron}")
        # The body (the full list) shows only when expanded; collapsed, the one-liner header is all.
        self._excluded_body.setVisible(not self._excluded_collapsed)
        if self._excluded_collapsed:
            self._excluded_body.clear()
            return
        # 1-based lap number (lap_label) so the excluded strip matches the table's Lap column.
        lines = [f"Lap {lap_label(r['idx'])} — {fmt_time(r['time'])} · {r['dist']:.0f} m"
                 for r in rows]
        if len(lines) > EXCLUDED_MAX_SHOWN:
            hidden = len(lines) - EXCLUDED_MAX_SHOWN
            lines = [*lines[:EXCLUDED_MAX_SHOWN], f"+{hidden} more"]
        self._excluded_body.setText("\n".join(lines))


    def _n_split_cols(self) -> int:
        """Number of S-split columns: sector_count()+1 if any sector lines, else 0."""
        n = self.session.sector_count()
        return n + 1 if n else 0

    def _n_real_cols(self) -> int:
        """The DATA columns: the base COLUMNS + today's S-splits. The blank spacer column sits at
        exactly this index (it's always last), so every caller stays right as sectors change."""
        return len(COLUMNS) + self._n_split_cols()

    def _apply_column_sizing(self):
        """Panel-fitted data columns + a blank spacer holding whatever they leave. Re-applied after
        every column-count change: Qt gives newly-added sections the header's default mode, so a
        fresh S-column (or a shifted spacer) would otherwise keep the old sizing.

        EVERY section is Interactive, spacer included, so _fit_columns can set an explicit width (a
        ResizeToContents section refuses one, and a Stretch spacer falls back to its own size hint
        — 42-53px of dead scroll range — exactly when the data columns are already overflowing)."""
        hdr = self.table.horizontalHeader()
        for c in range(self._n_real_cols() + 1):
            hdr.setSectionResizeMode(c, QHeaderView.Interactive)
        self._fit_columns()

    def _column_budget(self) -> tuple[list[int], list[int], list[int]]:
        """(natural, floors, caps) for the data columns.

        `natural` is what a column needs to show its header AND its values uncut;
        `floors` is what its VALUES alone need (a squeezed header elides and falls back to the
        tooltip _lap_col_tips gives it — a number has no such fallback);
        `caps` bound the grow pass. The Lap column is the row identity: pinned at LAP_COL_PX so
        the ▶/★/⚠ markers never jitter or clip, and never squeezed."""
        hdr = self.table.horizontalHeader()
        real = self._n_real_cols()
        cells = [self.table.sizeHintForColumn(c) if self.table.rowCount() else 0
                 for c in range(real)]
        natural = [max(hdr.sectionSizeHint(c), cells[c]) for c in range(real)]
        floors = [max(MIN_SECTION_PX, cells[c]) for c in range(real)]
        natural[0] = floors[0] = LAP_COL_PX
        return natural, floors, [max(n, MAX_DATA_COL_PX) for n in natural]

    def _fit_columns(self):
        """Size the data columns to the panel (see MAX_DATA_COL_PX). Runs after every refresh and
        on every viewport resize, so maximizing the panel widens the DATA instead of the blank
        spacer (L2-06) and a narrow panel keeps its S-split columns on screen (L3-04)."""
        hdr = self.table.horizontalHeader()
        real = self._n_real_cols()
        # MIN_SECTION_PX is reserved for the spacer: it must never be the section that summons a
        # horizontal scrollbar, so the data columns are fitted to the width that is left after it.
        width = self.table.viewport().width()
        avail = width - MIN_SECTION_PX
        if avail <= 0 or real <= 0:
            return
        natural, floors, caps = self._column_budget()
        fitted = fit_columns(natural, floors, caps, avail)
        for c, w in enumerate(fitted):
            hdr.resizeSection(c, w)
        # The spacer takes EXACTLY the slack the capped data columns left (P5: keep them adjacent
        # and left-packed instead of flinging the last one across a dead band), and collapses to
        # MIN_SECTION_PX when there is none.
        hdr.resizeSection(real, max(MIN_SECTION_PX, width - sum(fitted)))

    def eventFilter(self, obj, event):
        # The table's VIEWPORT, not this widget: the Corners/Laps pages are laid out inside a tab
        # stack, so a page can be given its real width without this container ever seeing a
        # resizeEvent — which is how the corner columns came to be fitted to Qt's stock 640px
        # default and overflowed the 447px quadrant by MORE than before the fit existed.
        if obj is self.table.viewport() and event.type() == QEvent.Resize:
            self._fit_columns()
            self._keep_selection_visible()
        return super().eventFilter(obj, event)

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
        # The blank SPACER column is appended LAST (after any S-splits) — it absorbs the panel's
        # leftover width so the data columns stay adjacent and left-packed (P5).
        n_splits = self._n_split_cols()
        headers = [*_columns(self._speed_unit),
                   *(f"S{i + 1}" for i in range(n_splits)), SPACER_HEADER]

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
            # (text, numeric-sort-key) per column. The Lap cell DISPLAYS the 1-based lap number
            # (lap_label) but KEEPS the 0-based lap id as its numeric sort key, so the column
            # still sorts by true lap order and the ★/▶/⚠ markers (keyed on lap_id) are unchanged.
            cells: list[tuple[str, float]] = [
                (lap_label(lap_id), float(lap_id)),
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
        # Losing sector lines SHRINKS the column count, and setColumnCount only drops the TRAILING
        # sections — so the old S-column items can survive in what is now the blank spacer column
        # (the fill above writes the data columns only). Empty it, or stale splits would show under
        # a blank header.
        spacer = self._n_real_cols()
        for r in range(self.table.rowCount()):
            if self.table.item(r, spacer) is not None:
                self.table.takeItem(r, spacer)
        self.table.blockSignals(False)
        # Full header text lives on the header itself (a narrow panel elides the label — L3-04).
        for c, tip in enumerate(_lap_col_tips(self._speed_unit)):
            self.table.horizontalHeaderItem(c).setToolTip(tip)
        for i in range(n_splits):
            self.table.horizontalHeaderItem(len(COLUMNS) + i).setToolTip(
                f"Sector {i + 1} split (s) — time between this lap's sector lines")
        # Set the section RESIZE MODES now that the rows are in; the widths themselves are fitted
        # at the end of refresh(), once _apply_highlights has finished rewriting the S-split text.
        self._apply_column_sizing()
        # Re-apply the user's chosen sort (lap-ascending by default) on the freshly-filled rows.
        # A remembered S-column can VANISH when sector lines are removed (and must never be the
        # blank spacer) — fall back to lap order rather than sorting a gone/blank column.
        if self._sort_col >= self._n_real_cols():
            self._sort_col, self._sort_order = 0, Qt.AscendingOrder
        # Tell _NumItem the direction first so blanks land LAST after any descending reversal.
        _NumItem._descending = self._sort_order == Qt.DescendingOrder
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(self._sort_col, self._sort_order)
        self._best_split = best_split  # cached so re-highlight after a sort needn't recompute
        # dropout lap ids, keyed by lap id so the ⚠ flag follows the lap across sorts
        self._dropout_ids = self.session.dropout_lap_ids()
        self._apply_highlights()
        # Fit AFTER the highlights: they append the ★ best-sector mark to S-split cells, which is
        # part of the width those columns need.
        self._fit_columns()
        if n_splits > self._n_splits_shown:
            self._reveal_last_split()
        self._n_splits_shown = n_splits
        # The excluded-laps strip follows every refresh — i.e. also after a timing-line edit
        # re-segments the laps (which shifts both the valid and the excluded sets).
        self._refresh_excluded()

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

    # Two DIFFERENT column sets, for the two orthogonal trust axes (they used to be one, which is
    # how Dist and Entry came to render at full confidence on a session whose start line was a
    # guess — QA L3-02):
    #
    #  * _start_line_cols — every value the start/finish line places: the lap Time AND the S-splits
    #    (durations between crossings), Dist (the distance BETWEEN crossings) and Entry (the speed
    #    AT a crossing). Move the line and all four change. So PROVISIONAL timing demotes all four.
    #  * _clock_cols — the DURATIONS only. The degraded-clock axis is about how well time itself is
    #    measured; it has no bearing on an odometer distance or a GPS speed sample, so it must not
    #    demote Dist/Entry.
    def _start_line_cols(self) -> set[int]:
        return set(range(1, self._n_real_cols()))

    def _clock_cols(self) -> set[int]:
        return {1, *(len(COLUMNS) + i for i in range(self._n_split_cols()))}

    def _apply_highlights(self):
        """Re-apply ALL row/cell highlights keyed by lap id, so they survive any sort:
          * green foreground on every cell of the overall best lap,
          * purple foreground+bold on each per-column session-best split cell (F5),
          * the ▶ prefix + bold Lap cell for the current (playing) lap.
        The blue selection is Qt's own row background and is left to the selection model.

        TIMING TRUST: when the session's timing is PROVISIONAL (start line auto-fitted, not
        user-confirmed — see Session.timing_verified) EVERY start-line-derived cell — lap Time, the
        S-splits, Dist and Entry (see _start_line_cols) — is de-emphasized (muted + italic, with the
        'provisional' tooltip) and BOTH "best" authority cues are suppressed — no purple
        session-best splits and no green best-lap — since a 'best' measured against an arbitrary
        start line is meaningless. Only the Lap number, which the line cannot move, stays normal.
        Verified timing renders as before.

        DATA QUALITY (orthogonal — Session.timing_quality): a media-clock-fallback recording or one
        whose GPS quality gate rejected many fixes ALSO mutes the DURATION cells (_clock_cols: Time
        + the S-splits) with an 'estimated' tooltip, but leaves Dist/Entry alone (an odometer
        distance and a GPS speed sample don't get worse when the clock does) and does NOT suppress
        the bests — the start line is trusted, so the bests stay valid RELATIVE to each other; only
        the absolute timing accuracy is degraded. A normal GPS9, clean-fix recording (the common
        case) leaves both axes untouched."""
        rows = self.table.rowCount()
        if not rows:
            return
        verified = self.session.timing_verified
        degraded = self.session.timing_quality.degraded
        # The degraded-timing cell tooltip, from the shared clock-aware copy (matches the map banner
        # + header chip); computed once — it's the same for every estimated cell.
        estimated_note = estimated_timing_tooltip(self.session.timing_quality)
        # Overall best lap = the valid lap with the min time (foreground green on all cells) —
        # suppressed entirely while the timing is provisional (but NOT for a merely-degraded clock).
        best_lap = self.session.best_lap_id() if verified else None
        # Cache the best-lap id so the ★ mark on the Lap cell (applied via _lap_cell_text) tracks it;
        # None while provisional so no ★ paints a meaningless "best" against an arbitrary start line.
        self._best_lap_id = best_lap
        n_splits = self._n_split_cols()
        best_split = self._best_split
        start_line_cols = self._start_line_cols()
        clock_cols = self._clock_cols()
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
                provisional_cell = not verified and c in start_line_cols
                # A degraded-clock DURATION cell mutes too, but only when NOT already provisional
                # (provisional is the stronger demotion + suppresses the bests; degraded keeps them).
                estimated_cell = verified and degraded and c in clock_cols
                muted_cell = provisional_cell or estimated_cell
                # base off-white; green (best lap) / muted (provisional or estimated timing) / purple.
                # BEST-LAP wins the COLOUR over degraded muting: on a degraded (but verified)
                # recording the estimated grey would otherwise repaint the best lap's Time cell —
                # the one cell answering "which lap was fastest" — so it reads like every other row.
                # Keep the best-lap green there (the italic + estimated tooltip below still carry the
                # accuracy cue). Provisional muting still wins: it suppresses the bests entirely, so
                # `is_best` is already False under provisional (best_lap is None while unverified).
                if muted_cell and not (estimated_cell and is_best):
                    item.setForeground(PROVISIONAL_COLOR)
                else:
                    item.setForeground(best_color if is_best else BASE_COLOR)
                # Muted+italic on any demoted timing cell (the best-lap cell stays green but still
                # italic, keeping the estimated cue); the dropout tooltip wins (it flags a per-lap
                # issue), else the provisional note, else the estimated-timing note, else clear.
                theme.apply_provisional_style(item, muted_cell)
                item.setToolTip(DROPOUT_TOOLTIP if is_dropout
                                else PROVISIONAL_TOOLTIP if provisional_cell
                                else estimated_note if estimated_cell else "")
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
        return f"{prefix}{best}{lap_label(lap_id)}{suffix}"  # 1-based display number

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
        # The blank trailing SPACER column holds no cells, so a click on it can't order anything —
        # it would just park the sort indicator on an empty header and forget the user's sort.
        # Bounce the indicator back to the live sort column (which re-applies that sort).
        if col >= self._n_real_cols():
            self.table.horizontalHeader().setSortIndicator(self._sort_col, self._sort_order)
            return
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
        # Build ONE QItemSelection over every matching row and apply it in a single model call.
        # A per-row selectRow() loop would REPLACE the selection each call under ExtendedSelection
        # (each acts like a plain click), leaving only the last row selected — so a multi-lap
        # select() must go through the selection model at once (L3: the cap re-applies via this).
        want = set(idxs)
        model = self.table.model()
        sel = QItemSelection()
        first = None
        for r in range(self.table.rowCount()):
            if self._lap_id(r) in want:
                idx = model.index(r, 0)
                sel.select(idx, idx)
                if first is None:
                    first = idx
        self.table.blockSignals(True)
        sm = self.table.selectionModel()
        sm.clearSelection()
        if not sel.isEmpty():
            sm.select(sel, QItemSelectionModel.Select | QItemSelectionModel.Rows)
        self.table.blockSignals(False)
        self._keep_selection_visible()

    def _reveal_last_split(self):
        """L3-04: the map's "Add sector" button silently created columns that landed ENTIRELY off
        the default quadrant (S2 and S3 at zero visible pixels), with nothing but a 1.55:1
        scrollbar to say so. The fit above claws most of that back; scroll only when the newest S
        column is STILL completely off screen, because every pixel of that scroll comes off the Lap
        column — trading the row's identity for a split that is already partly readable is a bad
        deal. Fires once per column-count increase: a refresh from a sort or a selection must never
        yank the horizontal scroll."""
        last = self._n_real_cols() - 1
        if not self.table.rowCount() or last < 0:
            return
        x = self.table.columnViewportPosition(last)
        if x < self.table.viewport().width():
            return                                          # already at least partly on screen
        v = self.table.verticalScrollBar().value()          # a purely HORIZONTAL scroll
        self.table.scrollTo(self.table.model().index(0, last), QAbstractItemView.EnsureVisible)
        self.table.verticalScrollBar().setValue(v)

    def _keep_selection_visible(self):
        """IA-02: a PROGRAMMATIC selection has to be scrolled to, or it isn't a selection the user
        can see. The app pre-selects the best lap at launch and draws four panels from it, but on a
        21-lap session that row sat 150px BELOW the viewport at every window size, with the vertical
        scrollbar still at 0 — the panel that OWNS the selection painted no highlighted row at all.

        Centre it: the laps either side are the context that makes the selected one mean something.
        Only when it is not already fully visible, so re-fitting or a resize never yanks a table the
        user has scrolled deliberately — and the horizontal offset is restored afterwards, because
        scrolling to a column-0 cell would otherwise undo the S-column scroll refresh() just made."""
        rows = sorted({i.row() for i in self.table.selectionModel().selectedRows()})
        if not rows:
            return
        idx = self.table.model().index(rows[0], 0)
        rect = self.table.visualRect(idx)
        if rect.isValid() and self.table.viewport().rect().contains(rect):
            return
        h = self.table.horizontalScrollBar().value()
        self.table.scrollTo(idx, QAbstractItemView.PositionAtCenter)
        self.table.horizontalScrollBar().setValue(h)

    def selected_lap_ids(self) -> list[int]:
        """The lap ids of the currently-selected rows (sorted). Read-only — used to restore the
        chart overlay to the table's selection when compare mode is turned off."""
        return sorted({self._lap_id(idx.row())
                       for idx in self.table.selectionModel().selectedRows()})

    def _capped_selection(self, ids: list[int]) -> list[int]:
        """L3: trim an over-large selection to the fastest MAX_COMPARE_LAPS laps so the charts never
        overlay more than can legibly draw (the legend truncates past ~13, and the curves blanket
        each other). Fewer than the cap passes through unchanged. Falls back to a plain head-slice if
        a (test-double) session exposes no lap_time."""
        if len(ids) <= MAX_COMPARE_LAPS:
            return ids
        lap_time = getattr(self.session, "lap_time", None)
        if callable(lap_time):
            # Keep the fastest cap laps (ties broken by lap id for a stable, deterministic pick).
            ranked = sorted(ids, key=lambda lid: (lap_time(lid), lid))
            return sorted(ranked[:MAX_COMPARE_LAPS])
        return ids[:MAX_COMPARE_LAPS]

    def _on_selection(self):
        ids = self.selected_lap_ids()
        capped = self._capped_selection(ids)
        if capped != ids:
            # Re-apply the trimmed selection so the deselected rows visibly clear (no silent
            # chart-side drop). select() blocks signals, so re-emit the capped set ourselves.
            self.select(capped)
        self.laps_selected.emit(capped)


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
# Corner identity column start width: "C12 ⟳" + the "Corner" header, fully readable (C3 —
# the old Stretch mode crushed this row-identity column to a 42px sliver at default width).
CORNER_NAME_COL_PX = 88


class CornerTable(QWidget):
    """Corners-mode table: one row per detected corner for the selected lap.

    Session-best corner time is purple+bold; Δ columns use the shared delta colour.
    Read-only/unsorted — track order is the meaning. A row click emits ``corner_clicked``
    so the app can ring that corner's apex on the map (B4: the Stats CORNERS and Coaching
    rows already did this — these rows were the odd surface out)."""

    # Clicked corner cid -> the map apex-ring highlight (wired in central_view).
    corner_clicked = Signal(object)

    def __init__(self, session: Session):
        super().__init__()
        self.session = session
        self._lap_id: int | None = None
        self._cids: list[int] = []   # row -> corner cid, set in refresh() (B4 map-ring click)
        # Speed display unit (km/h default); app pushes the persisted choice via set_speed_unit.
        # Drives the Apex/Δ apex/Entry/Exit value conversion + the per-column tooltips' unit name.
        self._speed_unit = units.DEFAULT_UNIT
        self.table = QTableWidget(0, len(CORNER_COLUMNS))
        self.table.setHorizontalHeaderLabels(CORNER_COLUMNS)
        self._apply_corner_tips()
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        # Column sizing (UI-scrutiny C3+B5): the old col-0 Stretch let Qt crush the row's
        # IDENTITY column to a 42px "orne" sliver at the default panel width (the "all 8 fit"
        # assumption rotted as columns grew) and balloon it to a 959px void when maximized.
        # Now: col 0 starts at a readable CORNER_NAME_COL_PX (fits "C12 ⟳" + the header) and every
        # column is Interactive so _fit_columns can size the set to the panel.
        # QA L3-03: leaving the overflow to the h-scrollbar was not enough — at the DEFAULT 447px
        # quadrant these 8 columns wanted 501px, so "Grip (est)" started at x=422 and 0 of 12 grip
        # cells rendered a readable value, behind a scrollbar handle at 1.55:1 contrast. The fit
        # gives the slack back (headers elide to their tooltips; values never do).
        hdr = self.table.horizontalHeader()
        for c in range(len(CORNER_COLUMNS)):
            hdr.setSectionResizeMode(c, QHeaderView.Interactive)
        hdr.resizeSection(0, CORNER_NAME_COL_PX)
        hdr.setTextElideMode(Qt.ElideRight)   # never centre-clip a squeezed header — see LapTable
        self.table.cellClicked.connect(self._on_cell_clicked)
        self.table.viewport().installEventFilter(self)   # re-fit on every real width change
        # B6: the table is deliberately unsortable (track order IS the meaning) — no pressed
        # feedback on headers that do nothing.
        hdr.setSectionsClickable(False)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setDefaultSectionSize(28)
        self._num_font = theme.mono_font(theme.TABLE)
        # Empty state (was a bare header grid — the one surface without one): says WHY there
        # are no rows (no lap selected vs no corners detected) instead of a silent void.
        self.empty = QLabel("")
        self.empty.setProperty("role", "EmptyState")
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setWordWrap(True)
        self.empty.setVisible(False)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.table)
        lay.addWidget(self.empty, 1)

    def _on_cell_clicked(self, row: int, _col: int):
        """B4: ring the clicked row's corner on the map — the same corner_clicked pathway the
        Stats CORNERS and Coaching rows use, so all three surfaces behave identically."""
        if 0 <= row < len(self._cids):
            self.corner_clicked.emit(self._cids[row])

    def _column_budget(self) -> tuple[list[int], list[int], list[int]]:
        """(natural, floors, caps) for the corner columns — see LapTable._column_budget. Column 0
        is the row identity, so it opens at CORNER_NAME_COL_PX; unlike the lap number it carries no
        moving marker, so it may be squeezed back to its own cell width ("C12 ⟳") when the panel
        is short — 24 of the 54px the default quadrant is missing come from there, the rest from
        the "Grip (est)" header, whose full wording is already in its tooltip."""
        hdr = self.table.horizontalHeader()
        n = self.table.columnCount()
        cells = [self.table.sizeHintForColumn(c) if self.table.rowCount() else 0 for c in range(n)]
        natural = [max(hdr.sectionSizeHint(c), cells[c]) for c in range(n)]
        floors = [max(MIN_SECTION_PX, cells[c]) for c in range(n)]
        if natural:
            natural[0] = max(natural[0], CORNER_NAME_COL_PX)
        return natural, floors, [max(x, MAX_DATA_COL_PX) for x in natural]

    def _fit_columns(self):
        """Size the 8 corner columns to the panel — every column on screen at the default quadrant,
        every column sharing the width when the panel is maximized."""
        hdr = self.table.horizontalHeader()
        avail = self.table.viewport().width()
        if avail <= 0 or not self.table.columnCount():
            return
        natural, floors, caps = self._column_budget()
        for c, w in enumerate(fit_columns(natural, floors, caps, avail)):
            hdr.resizeSection(c, w)

    def eventFilter(self, obj, event):
        # See LapTable.eventFilter: this page lives in a tab stack, so the container's own
        # resizeEvent is not a reliable signal that the table finally has its real width.
        if obj is self.table.viewport() and event.type() == QEvent.Resize:
            self._fit_columns()
        return super().eventFilter(obj, event)

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
        # Empty state: name the reason (no selected lap vs nothing detected) — a bare grid
        # reads as broken. The table hides so the message owns the pane.
        self.table.setVisible(bool(stats))
        self.empty.setVisible(not stats)
        if not stats:
            self.empty.setText(
                "Select a lap to see its corners." if not ok else
                "No corners detected for this session yet — corner analysis needs a few "
                "clean laps of track shape.")
        corner_list = self.session.corners.corner_list() if stats else []
        bests = self.session.corners.corner_session_bests() if stats else []
        # Per-corner grip utilisation (%); [] when there's no g signal → the column shows a dash.
        grip = self.session.driving.lap_corner_grip(self._lap_id) if stats else []
        self.table.setRowCount(len(stats))
        self._cids = [c.cid for c in corner_list]  # row -> cid for the map-ring click (B4)
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
        # Fit once the rows are in — the column widths depend on the values just written.
        self._fit_columns()
