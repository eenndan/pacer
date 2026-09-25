"""STRAIGHTS, a Stats-page section: straight-by-straight times over the clean laps (the
corner/straight partition), the trap speed at each straight's end, the exit Δ of the corner feeding
it, and the note naming the straight with the most exit leverage — and whether that is where the
Coaching tab starts. A row click rings the corner FEEDING the straight. A section of
`stats_panel.StatsView`: build, refresh and copy here; the page places it (see `stats_common`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView

from . import coaching, provenance, theme, units
from ._signal import plural

# The Coaching panel's OWN row filter, imported (not re-implemented) so the note quotes the
# Coaching tab's ranking exactly — L5-02.
from .coaching_panel import _ranked_shown
from .lap_table import NUM_ROLE, _NumItem
from .stats_common import RING_ROLE, ROW_HEIGHT, ReportTable, keep_blanks_last, section_heading
from .widgets import DASH, WrapLabel


def _straight_count_tip(n: int, of: int | None, what: str) -> str:
    """One STRAIGHTS column's hover when not every lap counted (C4), or "" when all did / the
    report never counted. `what` names the edges that column reads, e.g. "both ends of this
    straight"."""
    if of is None or n >= of:
        return ""
    if n == 0:
        return (f"No value: on none of the {plural(of, 'clean lap')} was {what} matched to your "
                "best lap's line on track, and an interpolated edge can put it well out.")
    them = "it is" if of - n == 1 else "they are"   # one lap left out is "it" (K2)
    return (f"Over the {n} of {of} clean laps matched on track at {what}. On the other {of - n} "
            "that edge was interpolated between neighbouring corners, which can put a time tenths "
            f"of a second and a speed several km/h out, so {them} left out.")


STRAIGHT_COLUMNS = ["Straight", "Best", "Median", "σ (s)", "Trap best", "Trap med", "Exit Δ"]
STRAIGHTS_TOOLTIP = ("Straight-by-straight over the clean laps (the corner/straight "
                     "partition — segments sum to the lap time exactly): best/median/σ "
                     "time, the trap speed at the straight's END, and Exit Δ — the "
                     "preceding corner's median exit speed vs your best lap's (+ is "
                     "faster). A slow exit costs time down the straight after it, which no "
                     "corner's own time contains: the note under the table names the straight "
                     "where exit deficit × time spread is largest. Trap speed doubles as a "
                     "gearing/engine-health proxy. "
                     "Each column counts only the laps whose corner edges IT reads were matched "
                     "to your best lap's line on track — the time both ends of the straight, the "
                     "trap speed its end, Exit Δ the corner before it — because an interpolated "
                     "edge can put a time tenths of a second and a speed several km/h out (hover "
                     "a cell for how many laps count). "
                     + provenance.CORNER_MATCH_DRIFT + " "
                     "Click a row to ring the corner feeding that straight.")
STRAIGHTS_NOTE_TOOLTIP = (
    "The straight whose preceding corner's exit deficit × the straight's median − best time is "
    "largest — measured, not modelled. It is time down the STRAIGHT after a slow exit, which the "
    "Coaching tab's ranking does not contain: Coaching ranks the time lost inside each corner "
    "against your best lap, and the corner/straight partition keeps the two apart (together they "
    "sum to the lap). So the two can name different corners without either being wrong; Coaching "
    "is the list of what to work on.")


class StraightsSection:
    """The STRAIGHTS heading, table and exit-leverage note (`heading`, `table`, `note`).
    `on_ring(cid)` is the page's `corner_clicked` — a row click rings the corner feeding that
    straight, None on deselect."""

    def __init__(self, on_ring):
        self._on_ring = on_ring
        self.heading = section_heading("STRAIGHTS")
        self.table = ReportTable(STRAIGHT_COLUMNS, ROW_HEIGHT)
        self.table.setToolTip(STRAIGHTS_TOOLTIP)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFocusPolicy(Qt.ClickFocus)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.horizontalHeader().sortIndicatorChanged.connect(keep_blanks_last)
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        # PS-2: the exit-leverage straight, said as what it measures under the table it summarizes
        # (COASTING's note does the same for its top place) — it was a "fix first" TILE, the most
        # imperative label in the app, naming a different corner from the Coaching tab's #1 on 3
        # of the 4 working-set recordings. See _note_text.
        self.note = WrapLabel()
        self.note.setProperty("role", "TableNote")
        self.note.setToolTip(STRAIGHTS_NOTE_TOOLTIP)

    def widgets(self) -> tuple:
        return (self.heading, self.table, self.note)

    def tables(self) -> tuple:
        return (self.table,)

    @staticmethod
    def _coaching_start(session) -> list[int]:
        """The corner(s) the Coaching tab starts with: its first ranked row, plus any ranked row
        its own theme cannot separate from it (`coaching.lead_ties` — "Start with C7 or C5"), so
        a sentence here names exactly what that page names. [] without a ranked row."""
        opp_fn = getattr(session, "coaching_opportunities", None)
        opp = opp_fn() if opp_fn is not None else None
        rows = _ranked_shown(opp) if getattr(opp, "enough", False) else []
        lead = getattr(rows[0], "cid", None) if rows else None
        if lead is None:
            return []
        return [r.cid for r in coaching.lead_ties(list(opp.rows), lead)] or [lead]

    def _note_text(self, session, top, unit, u_label) -> str:
        """The STRAIGHTS table's top exit-leverage row, said as what it measures, and whether it is
        where the Coaching tab starts. "" when no straight has any leverage.

        PS-2 (board review 2026-09-23): this was a tile captioned "fix first" — the most imperative
        label in the app — and measured on the real window it named a different corner from the
        Coaching tab's #1 on 3 of the 4 working-set recordings (SD_19_09: C2 vs C1, Sandown 3h: C4
        vs C1, MK: C7 vs C5; SD_30_08 agreed on C7). Neither is wrong: this is time down the
        straight after a slow exit, which Coaching's corner windows do not contain. So it names
        its own quantity, and the corner Coaching starts with, instead of a second instruction."""
        if top.leverage <= 0 or top.exit_delta_kmh is None:
            return ""
        exit_gap = abs(units.convert_speed(top.exit_delta_kmh, unit))
        text = (f"Most exit leverage: C{top.ring_cid} — your median exit is {exit_gap:.1f} "
                f"{u_label} under your best lap's, onto the {top.label} straight, which runs "
                f"+{top.median_s - top.best_s:.2f} s over its best (leverage is the one times "
                "the other).")
        start = self._coaching_start(session)
        if not start:
            return text
        if start == [top.ring_cid]:
            return f"{text} C{top.ring_cid} is also where the Coaching tab starts."
        # Named the way Coaching's own start-here line names a tie ("C1, C4, C7 or 1 more").
        named = [f"C{c}" for c in start[:coaching._TIE_NAME_CAP]]
        extra = len(start) - len(named)
        names = (f"{', '.join(named)} or {extra} more" if extra
                 else f"{', '.join(named[:-1])} or {named[-1]}" if len(named) > 1 else named[0])
        if top.ring_cid in start:
            return f"{text} The Coaching tab starts with {names} — this is one of them."
        return (f"{text} The Coaching tab starts with {names}: it ranks the time lost inside "
                "the corners, and a straight is outside every corner.")

    def refresh(self, session, unit, u_label):
        report = getattr(session, "straights_report", list)() or []
        # B8: a start line inside a corner section produces ~0-duration S/F stubs — noise
        # rows with no driving content (BRAKING already omits unmatched corners the same way).
        full_n = len(report)
        # C4: a straight NO lap matched at both ends has no time at all, which is not the same as
        # a ~0-duration one — it stays, as a row of dashes that says why.
        report = [st for st in report
                  if (st.n == 0 and st.n_laps)
                  or max(st.best_s or 0.0, st.median_s or 0.0) >= 0.05]
        stubs = full_n - len(report)
        has = bool(report)
        self.heading.setVisible(has)
        self.table.setVisible(has)
        if not has:
            self.note.setText("")
            self.note.setVisible(False)
            self.table.setRowCount(0)
            return
        # SAY HOW MANY ARE NOT LISTED (§5.6). The ideal-lap disclosure on this same page counts
        # the PARTITION ("across the 12 corners and 13 straights pacer found here") while this
        # table silently drops the ~0-duration S/F stubs a start line inside a corner section
        # produces — so one page said 13 and showed 11, with nothing anywhere reconciling them.
        # The count is derived from the same list, so the two can never drift apart again.
        dropped = f" · {stubs} too short to list" if stubs else ""
        self.heading.setText(f"STRAIGHTS · speeds in {u_label}{dropped}")
        note = self._note_text(session, max(report, key=lambda s: s.leverage),
                                         unit, u_label)
        self.note.setText(note)
        self.note.setVisible(bool(note))
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
        for r, st in enumerate(report):
            name = _NumItem(st.label)
            name.setData(NUM_ROLE, st.index)      # sort key: track order
            name.setData(RING_ROLE, st.ring_cid)  # the corner feeding this straight
            t.setItem(r, 0, name)
            cells = [
                cell(st.best_s, "{:.2f}"), cell(st.median_s, "{:.2f}"),
                cell(st.sigma_s, "{:.2f}"),
                cell(units.convert_speed(st.trap_best_kmh, unit)
                     if st.trap_best_kmh is not None else None, "{:.1f}"),
                cell(units.convert_speed(st.trap_median_kmh, unit)
                     if st.trap_median_kmh is not None else None, "{:.1f}"),
                cell(units.convert_speed(st.exit_delta_kmh, unit)
                     if st.exit_delta_kmh is not None else None, "{:+.1f}"),
            ]
            # How many laps each column counted, where that is not all of them (C4).
            of = getattr(st, "n_laps", None)
            tips = [_straight_count_tip(st.n, of, "both ends of this straight")] * 3 + [
                _straight_count_tip(st.n_trap if st.n_trap is not None else 0, of,
                                    "this straight's end")] * 2
            tips.append(_straight_count_tip(st.n_exit, of, f"C{st.ring_cid}'s exit")
                        if st.n_exit is not None else "")
            for col, (item, tip) in enumerate(zip(cells, tips, strict=True), start=1):
                if tip:
                    item.setToolTip(tip)
                t.setItem(r, col, item)
        t.blockSignals(False)
        t.setSortingEnabled(True)
        t.fit()

    def _on_row_selected(self):
        """A straight row rings the CORNER FEEDING it (its exit sets the straight's story);
        same corner_clicked pathway as the other tables."""
        rows = self.table.selectionModel().selectedRows()
        if rows:
            item = self.table.item(rows[0].row(), 0)
            self._on_ring(item.data(RING_ROLE) if item else None)
        else:
            self._on_ring(None)
