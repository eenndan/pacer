"""COASTING, a Stats-page section: where the session's coasting HAPPENS, by place — every clean
lap's coasting split over the corner/straight partition, ranked, with "vs top" saying place by place
whether the laps can separate it from the first row, and the note saying what that order is worth.
A row click rings the place on the map (a straight rings the corner feeding it). A section of
`stats_panel.StatsView`: build, refresh and copy here; the page places it (see `stats_common`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView

from . import driving, theme
from . import stats as stats_service
from ._signal import plural
from .lap_table import NUM_ROLE, _NumItem
from .stats_common import (
    _DRIVING_COAST,
    RING_ROLE,
    ROW_HEIGHT,
    ReportTable,
    keep_blanks_last,
    section_heading,
)
from .widgets import WrapLabel

# COASTING: where the session's coasting HAPPENS, by place (`Session.coast_report`). The coast
# paragraph is `_DRIVING_COAST` itself, not a paraphrase: the window is the one quantity #275 found
# wrong and #279/#297 had to re-state on every surface, and it applies on BOTH g paths (the coast
# series is rebuilt from the lap's own speed either way), so one sentence serves every recording.
COAST_COLUMNS = ["Where", "s / lap", "Laps", "Share %", "vs top"]
# The "vs top" cell: what the laps say about this place against the one with the most coasting.
# Words, not a tint — which rows are level is the table's one claim, and it has to survive a
# colour-blind palette and a screen reader.
COAST_TOP, COAST_TIED, COAST_LESS = "top", "tied", "less"
# A place holding less than the detector's shortest coast (driving.MIN_COAST_S) on one lap in five
# is listed by count, not by row — the heading says how many. On the owner's five recordings that
# leaves 7-14 rows holding 96.5-99.2 % of the coasting, against 12-20 places with any at all.
COAST_LIST_MIN_S = driving.MIN_COAST_S / 5
COAST_NAMES_MAX = 6       # the note names this many tied places, then counts the rest
COASTING_TOOLTIP = (
    "Where the coasting is. Every clean lap's coasting is split over the corner/straight "
    "partition — the pieces the STRAIGHTS table is cut from, each corner's edges projected onto "
    "that lap — and read off that lap's own clock, so a coast running out of a corner into the "
    "straight is split at the edge, never counted twice. s / lap is the session's coasting in that "
    "place divided by the clean laps, so the column adds up to the MEAN coasting per lap, not the "
    "median the DRIVING tile shows; Laps counts the clean laps that coasted there at all.\n\n"
    # #339 kept this table counting every lap ON PURPOSE (CornerModel.lap_corner_resolved has the
    # measurement) and said so only in code. It shares the STRAIGHTS table's pieces and says so one
    # sentence up, so a reader would take the STRAIGHTS table's lap rule with them.
    "Unlike the CORNERS and STRAIGHTS tables, it keeps every clean lap — including a lap whose "
    "corner edge could not be matched to your best lap's line on track and was interpolated. "
    "Leaving those laps out piece by piece would stop the column adding up to the laps' "
    "coasting; the cost is that, next to an interpolated edge, that lap's coasting may be split "
    "at the wrong point.\n\n"
    "This is where the coasting HAPPENS, not where it costs time. Coaching's “coasting” "
    "reason is a different number: how much longer your typical lap coasts in a corner than your "
    "best lap does.\n\n"
    "The order is a ranking only where the laps can separate it. vs top says, place by place, "
    f"whether a paired sign-flip test over the clean laps separates it from the first row at "
    f"p < {stats_service.COAST_LEAD_ALPHA:g}: \u201c{COAST_TOP}\u201d is a first row that "
    f"separates from every other place, \u201c{COAST_TIED}\u201d a place the laps cannot tell "
    f"apart from it (the first row too, when anything is), \u201c{COAST_LESS}\u201d one they "
    "can. Click a row to ring the place on the map (a straight rings the corner feeding it).\n\n"
    + _DRIVING_COAST)


def _name_list(names: list[str], limit: int = COAST_NAMES_MAX) -> str:
    """"C1, C3 and C6" / "C1, C3, C6, C4, C5, C10 and 5 more"."""
    if len(names) > limit:
        return f"{', '.join(names[:limit])} and {len(names) - limit} more"
    return names[0] if len(names) == 1 else f"{', '.join(names[:-1])} and {names[-1]}"


def coast_note(report) -> str:
    """The line under the COASTING table: what the order in it is worth.

    A LEAD THE LAPS CANNOT SEPARATE IS SAID TO BE ONE. #311 refused to crown a coaching corner the
    measurement could not separate from the next, and the same shape is here on both D24
    recordings: the leader ties with 10 other places on 0060 and 7 on 0062, and the two recordings
    put different corners on top. A table sorted by a column always has a first row; this sentence
    is what stops the first row reading as a finding when it is not."""
    n = report.n_laps
    laps = plural(n, "clean lap")
    # `laps` is already "1 clean lap" at one lap; the determiner in front of it has to follow, or
    # the two sentences below read "these 1 clean lap" (K2).
    these = "this" if n == 1 else "these"
    places = report.places
    if not places:
        return f"No coasting was detected on the {laps}."
    lead = places[0]
    if len(places) == 1:
        return f"All the coasting on the {laps} is in {lead.label}: {lead.s_per_lap:.2f} s a lap."
    if report.lead_separable:
        nxt = places[1]
        return (f"{lead.label} holds the most coasting — {lead.s_per_lap:.2f} s a lap, more than "
                f"{nxt.label} ({nxt.s_per_lap:.2f} s) or anywhere else by a margin {these} {laps} "
                f"can separate.")
    tied = [p for p in places if p.tied]
    lo = min(p.s_per_lap for p in tied)
    return (f"No one place leads: {_name_list([p.label for p in tied])} are tied — between "
            f"{lo:.2f} and {lead.s_per_lap:.2f} s of coasting a lap, and {these} {laps} cannot "
            f"put them in order.")


class CoastingSection:
    """The COASTING heading, its place `table` and the `note` saying what the order is worth.
    `on_ring(cid)` is the page's `corner_clicked` — a row click rings the place on the map (a
    straight rings the corner feeding it), None on deselect."""

    def __init__(self, on_ring):
        self._on_ring = on_ring
        self.heading = section_heading("COASTING")
        self.heading.setToolTip(COASTING_TOOLTIP)
        self.table = ReportTable(COAST_COLUMNS, ROW_HEIGHT)
        self.table.setToolTip(COASTING_TOOLTIP)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFocusPolicy(Qt.ClickFocus)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.horizontalHeader().sortIndicatorChanged.connect(
            keep_blanks_last)
        # Column 0 sorts by RANK (most coasting first), not by track order: the question this
        # table answers is "where", and its "vs top" column and the note under it say what that
        # order is worth. (Opening on a numeric column instead would put Qt's indicator over a
        # right-aligned header label.)
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        self.note = WrapLabel()
        self.note.setProperty("role", "TableNote")

    def widgets(self) -> tuple:
        return (self.heading, self.table, self.note)

    def tables(self) -> tuple:
        return (self.table,)

    def refresh(self, session):
        """The COASTING table + its note. Hidden outright without a report (no corners, no clean
        lap, or no g signal — no coasting instrument to report on); a session that simply did not
        coast keeps the heading and says so in the note instead of showing an empty grid."""
        report = getattr(session, "coast_report", lambda: None)()
        has = report is not None
        self.heading.setVisible(has)
        self.note.setVisible(has)
        listed = [p for p in report.places if p.s_per_lap >= COAST_LIST_MIN_S] if has else []
        self.table.setVisible(bool(listed))
        if not has:
            self.table.setRowCount(0)
            return
        unlisted = len(report.places) - len(listed)
        self.heading.setText(
            f"COASTING · {unlisted} {'place' if unlisted == 1 else 'places'} under "
            f"{COAST_LIST_MIN_S:.2f} s/lap not listed"
            if unlisted else "COASTING")
        self.note.setText(coast_note(report))
        mono = theme.mono_font(theme.TABLE)

        def cell(text: str, key):
            item = _NumItem(text)
            item.setData(NUM_ROLE, key)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.table
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(listed))
        for r, p in enumerate(listed):
            name = _NumItem(p.label)
            name.setData(NUM_ROLE, r)             # sort key: the rank
            name.setData(RING_ROLE, p.ring_cid)   # the corner the map rings
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(f"{p.s_per_lap:.2f}", p.s_per_lap))
            t.setItem(r, 2, cell(f"{p.laps}/{report.n_laps}", p.laps))
            t.setItem(r, 3, cell(f"{p.share * 100.0:.0f}", p.share))
            if not p.tied:
                t.setItem(r, 4, cell(COAST_LESS, 2))
            elif report.lead_separable:
                t.setItem(r, 4, cell(COAST_TOP, 0))
            else:
                t.setItem(r, 4, cell(COAST_TIED, 1))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        t.fit()

    def _on_row_selected(self):
        """A COASTING row rings its place on the map — a corner rings itself, a straight the corner
        feeding it — through the same corner_clicked pathway as the other tables."""
        rows = self.table.selectionModel().selectedRows()
        if rows:
            item = self.table.item(rows[0].row(), 0)
            self._on_ring(item.data(RING_ROLE) if item else None)
        else:
            self._on_ring(None)
