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
from ._signal import fmt_signed, plural

# The Coaching panel's OWN row filter, imported (not re-implemented) so the note quotes the
# Coaching tab's ranking exactly — L5-02.
from .coaching_panel import _ranked_shown
from .lap_table import NUM_ROLE, _NumItem
from .stats_common import RING_ROLE, ROW_HEIGHT, ReportTable, keep_blanks_last, section_heading
from .stats_ideal import _give_back
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
                     "where exit deficit × time spread is largest, leaving out a best that its "
                     "lap gave back in the corners beside it. Trap speed doubles as a "
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
    "largest (its exit leverage: the one times the other) — measured, not modelled. It leaves "
    "out a straight whose best is the IDEAL LAP's minimum there when the lap that set it gave "
    "all of that time back in the corners beside it, by that table's own rule: a line trade-off "
    "or a misplaced GPS edge, not time a slow exit costs. \"Usual\" is the median over the clean "
    "laps. It is time down the STRAIGHT after a slow exit, which the Coaching tab's ranking does "
    "not contain: Coaching ranks the time lost inside each corner against your best lap, and the "
    "corner/straight partition keeps the two apart (together they sum to the lap). So the two "
    "can name different corners without either being wrong; Coaching is the list of what to "
    "work on.")


def _signed_1dp(value: float) -> str:
    """The Exit Δ cell: one decimal through the page's one signed formatter (true minus, LOOK-7)."""
    return fmt_signed(value, 1)

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
        self.note = WrapLabel()   # the exit-leverage note: see _note_text (PS-2)
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

    @staticmethod
    def _given_back(session, report) -> set[int]:
        """The straights whose best is the IDEAL LAP's own minimum there and whose lap gave all of
        it back beside it, by that table's rule (`stats_ideal._give_back`): one page must not rank
        a minimum its other block calls "not free time" (QA W2 REG-1, MK's C7 on lap 16's best)."""
        sb = getattr(session, "ideal_segment_bests", lambda: None)()
        best = session.best_lap_id() if hasattr(session, "best_lap_id") else None
        rows = sb.decomposition(best) if sb is not None and best is not None else None
        by_label = {st.label: st for st in report}
        return {st.index for row in rows or () if (st := by_label.get(row.label)) is not None
                and st.best_s == sb.bests[row.index]    # the same minimum, to the bit
                and _give_back(sb, row, best) and sb.donor_net(row.index, best)[0] <= 0}

    def _note_text(self, session, report, unit, u_label) -> str:
        """The top exit-leverage straight a lap kept (`_given_back`) as what it measures, those
        left out above it, and whether Coaching starts there; "" when none has leverage. PS-2: a
        "fix first" tile once, it names its own quantity — it named another corner than Coaching's
        #1 on 3 of 4 recordings, and neither is wrong: no corner's window holds the straight."""
        gone = self._given_back(session, report)
        ranked = sorted((st for st in report if st.leverage > 0 and st.exit_delta_kmh is not None),
                        key=lambda st: -st.leverage)
        top = next((st for st in ranked if st.index not in gone), None)
        above = [st.ring_cid for st in ranked[:ranked.index(top) if top else None]]
        why = ("the lap that set the straight's best gave all of that time back in the corners "
               "beside it.")
        if top is None:
            return f"No slow exit to rank: after {coaching.corner_names(above)}, {why}" \
                if above else ""
        exit_gap = abs(units.convert_speed(top.exit_delta_kmh, unit))
        # Copy #7 (QA 2026-09-26): what it costs, in words; "leverage" and "median" are named on
        # the hover (STRAIGHTS_NOTE_TOOLTIP).
        text = (f"Slow exit costing the most: C{top.ring_cid}. Your usual exit is {exit_gap:.1f} "
                f"{u_label} under your best lap's, and the {top.label} straight after it takes "
                f"{top.median_s - top.best_s:.2f} s longer than its best.")
        if above:
            text += (f" Not {coaching.corner_names(above)}: after "
                     f"{'it' if len(above) == 1 else 'each'}, {why}")
        start = self._coaching_start(session)
        if not start:
            return text
        if start == [top.ring_cid]:
            return f"{text} C{top.ring_cid} is also where the Coaching tab starts."
        # Named the way Coaching's own start-here line names a tie ("C1, C4, C7 or C6").
        names = coaching.corner_names(start)
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
        note = self._note_text(session, report, unit, u_label)
        self.note.setText(note)
        self.note.setVisible(bool(note))
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            text = (DASH if val is None else fmtstr(val) if callable(fmtstr)
                    else fmtstr.format(val))
            item = _NumItem(text)
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
                     if st.exit_delta_kmh is not None else None, _signed_1dp),
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
