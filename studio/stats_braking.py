"""BRAKING, a Stats-page section: braking repeatability and commitment, one row per corner with
a matched brake event — the cross-lap scatter of the brake-onset point, the commit %, and the
ESTIMATED metres you could brake later. A row click rings the corner on the map. A section of
`stats_panel.StatsView`: build, refresh and copy here; the page places it (see `stats_common`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView

from . import driving, theme
from .lap_table import NUM_ROLE, _NumItem
from .stats_common import ROW_HEIGHT, ReportTable, keep_blanks_last, section_heading
from .widgets import DASH

BRAKE_COLUMNS = ["Corner", "n", "Onset σ m", "Span m", "Commit %", "m later"]
BRAKING_TOOLTIP = ("Braking repeatability per corner, over the clean laps: the cross-lap "
                   "scatter of your brake-onset POINT (σ and max−min span, metres, compared "
                   "in the reference lap's odometer) plus commitment — the median event's "
                   "peak decel as a % of the session's demonstrated maximum — and the "
                   "ESTIMATED median metres you could brake later (the D4 brake-point "
                   "model). Corners with no matched brake event are omitted. A lap counts at a "
                   "corner only where it was matched to your best lap's line on track at the "
                   "corner's entry and exit — the rule the CORNERS table counts by — because a "
                   "brake point is read inside that window; so n can be fewer than the clean laps "
                   "that braked there. Honesty floor: "
                   "10 Hz GPS quantizes the onset by ~1.5 m — a σ at or below that is "
                   "measurement, not driving. Click a row to ring the corner on the map.\n\n"
                   "COMMIT % IS A RATIO INSIDE ONE CHANNEL. Both halves of it — the event's peak "
                   "and the \"demonstrated maximum\" it is divided by, the "
                   f"{driving.AMAX_PCT:g}th percentile of every event peak in the session — are "
                   "measured on the UNWINDOWED detection series. That is NOT the smoothed "
                   "\"peak braking g\" tile in SPEED · G, which is a different filter of the same "
                   "axis and reads lower; dividing by that one instead would inflate every "
                   "number in this column.")


class BrakingSection:
    """The BRAKING heading and table (`heading`, `table`). `on_ring(cid)` is the page's
    `corner_clicked` — a row click rings that corner on the map, None on deselect."""

    def __init__(self, on_ring):
        self._on_ring = on_ring
        self.heading = section_heading("BRAKING")
        self.table = ReportTable(BRAKE_COLUMNS, ROW_HEIGHT)
        self.table.setToolTip(BRAKING_TOOLTIP)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFocusPolicy(Qt.ClickFocus)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.horizontalHeader().sortIndicatorChanged.connect(keep_blanks_last)
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)

    def widgets(self) -> tuple:
        return (self.heading, self.table)

    def tables(self) -> tuple:
        return (self.table,)

    def refresh(self, session):
        """The BRAKING table: one row per corner WITH a matched brake event (an unbraked
        kink adds noise, not signal). Same sort/click idiom as the CORNERS table."""
        report = [r for r in (getattr(session, "brake_report", list)() or []) if r.n > 0]
        has = bool(report)
        self.heading.setVisible(has)
        self.table.setVisible(has)
        if not has:
            self.table.setRowCount(0)
            return
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.table
        t.setSortingEnabled(False)
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, bc in enumerate(report):
            name = _NumItem(f"C{bc.cid}")
            name.setData(NUM_ROLE, bc.cid)
            t.setItem(r, 0, name)
            t.setItem(r, 1, cell(bc.n, "{:d}"))
            t.setItem(r, 2, cell(bc.sigma_m, "{:.1f}"))
            t.setItem(r, 3, cell(bc.span_m, "{:.1f}"))
            t.setItem(r, 4, cell(bc.commit_pct, "{:.0f}"))
            t.setItem(r, 5, cell(bc.metres_later_med, "{:+.1f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        t.fit()

    def _on_row_selected(self):
        """A BRAKING-table row is a corner too — emit the same corner_clicked the CORNERS
        table does (one map-ring pathway, maximize-aware in CentralView)."""
        rows = self.table.selectionModel().selectedRows()
        if rows:
            item = self.table.item(rows[0].row(), 0)
            self._on_ring(item.data(NUM_ROLE) if item else None)
        else:
            self._on_ring(None)
