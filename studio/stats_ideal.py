"""IDEAL LAP, a Stats-page section: the theoretical best, what it says is still on the table, what
that minimum was taken over, and the DECOMPOSITION — which segments the gap lives in, on which lap
you drove each one, and how repeatable it is. A row click rings the corner on the map. A section of
`stats_panel.StatsView`: build, refresh and copy here; the page places it (see `stats_common`).
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QAbstractItemView, QTableWidgetItem

from . import theme
from .stats_common import (
    RING_ROLE,
    ROW_HEIGHT,
    ReportTable,
    num_item,
    section_heading,
    set_target_tile,
)
from .widgets import DASH, Tile, WrapLabel

# Every clause of the old copy — "the sum of the session-best sector splits", "the purple cells on
# the Laps tab", "shown only with sector lines", "without them it degenerates to the best lap time"
# — described a number that no longer exists. Sector lines do not touch the ideal any more (driven
# through set_timing_lines at 0/1/2/3 lines, ideal_total is byte-identical), and the ideal is
# strictly faster than the best lap whenever two laps donate. The one clause that was true and
# load-bearing is kept verbatim: A REFERENCE TARGET, NOT A LAP YOU DROVE.
#
# ...and BOTH ideal tooltips now close with the same paragraph, because the thing they were both
# missing is the same fact about both numbers: they are ORDER STATISTICS. The line under the tiles
# states the sample; this states why the sample is part of the answer, which is the half a caption
# cannot carry — the brief for this disclosure was "the number where a reader sees it, the
# mechanism on hover".
#
# Every figure is measured, not asserted: `ideal_total` over random subsets of each recording's
# clean laps (20,000 draws per N) falls 0.160 / 0.178 / 0.273 / 0.490 / 0.742 s per doubling of lap
# count on the owner's five working-set rows (T16b, re-measured on the built-in Sandown Park line by
# Q2), and on Sandown 3h's three chapters it is still falling 0.252 s per doubling over the last one
# measured (50→62 laps) — nothing is being
# approached. Over six start-line positions per recording the detected corner count moved 11↔12 on
# D24 and 7↔8 on Sandown_09_05_2026, and the headline gap by up to +69 % (measured before #300 on
# recordings no longer here, not since). The full table and its sources are in
# corner_model.IdealSample; this is the version a reader gets on hover.
IDEAL_SAMPLE_TOOLTIP = (
    "\n\nIt is a MINIMUM over the clean laps counted under the tiles, so it is partly a measure "
    "of how many laps you recorded: measured on the owner's recordings, it falls 0.16–0.74 s "
    "per doubling of lap count and keeps falling — there is no floor it settles on. It also "
    "moves when the corners are re-detected, which happens every time you drag the start/finish "
    "line. Compare it with another session only when the two have a similar lap count and "
    "corner count.")
THEORETICAL_TOOLTIP = ("Theoretical best — your quickest time through each corner and each "
                       "straight, stitched into one lap. A reference target, not a lap you "
                       "drove: no single lap was this fast all the way round, but every piece "
                       "of it is a piece you drove. The table below says where it lives."
                       + IDEAL_SAMPLE_TOOLTIP)
IDEAL_GAP_TOOLTIP = ("What the theoretical best says is still on the table: your best lap minus "
                     "the stitched ideal. It is time you have already demonstrated, one segment "
                     "at a time, on laps you drove — not a simulation and not a lap record."
                     + IDEAL_SAMPLE_TOOLTIP)
# "As fast as this lap", not "Laps as fast" (§5.6). The count is laps that drove the segment at
# least as fast as THE SUBJECT LAP — the tooltip always said so, the header did not, and a reader
# comparing "20" against a gain naturally reads it as "20 laps already matched the ideal, so why is
# this a gain?". Measured before changing it: this table's columns are ResizeToContents, so the
# longer header widens its own column (96 -> 131 px) and the table still fits its pane (367 px of
# header in a 369 px viewport).
# Copy #10 (QA 2026-09-26): "Laps this fast" — shorter, and still says what the count is of (the
# tooltip names the subject lap).
IDEAL_COLUMNS = ["Segment", "Gain (s)", "Laps this fast", "Best on lap"]
# A row under this is not advice — it is a rounding difference between two laps of the same
# corner, and a plan is not 25 rows long. The remainder is never hidden: the note under the table
# states how many segments were left out and what they are worth, so the rows on screen and the
# tile above them still add up (see IdealSection.refresh).
IDEAL_GAIN_FLOOR = 0.05
IDEAL_TOOLTIP = (
    "Where the theoretical best lives: per segment of the corner/straight partition, the time "
    "your BEST lap gives away against your quickest time through it, the lap that set that "
    "quickest time, and how many of your clean laps drove that segment at least as fast as your "
    "best lap did. The gains over EVERY segment sum exactly to the gap under the tile above.\n\n"
    "Ranked by gain × the share of laps that already matched it — so a smaller gain you make "
    "routinely sits above a bigger one you made once. Both factors are columns, so you can check "
    "the order by eye. Click a row to ring that corner on the map.")


class IdealSection:
    """The IDEAL LAP heading, its two stitched-target tiles (`t_theoretical`, `t_gap`), the `sample`
    line under them, the decomposition `table` and its remainder `note`. `on_ring(cid)` is the
    page's `corner_clicked` — a row click rings that corner on the map, None on deselect."""

    def __init__(self, on_ring):
        self._on_ring = on_ring
        self.heading = section_heading("IDEAL LAP")
        self.t_theoretical = Tile("theoretical best")
        self.t_theoretical.setToolTip(THEORETICAL_TOOLTIP)
        self.t_gap = Tile("on the table · vs your best")
        self.t_gap.setToolTip(IDEAL_GAP_TOOLTIP)
        # WHAT THE TWO TILES ABOVE WERE MINIMISED OVER — the line this block was missing.
        #
        # Both numbers are order statistics: `-1.49 s from 65 laps` and `-0.95 s from 5 laps` are
        # the SAME DRIVING on D24's three chapters, measured over random subsets. Without the
        # counts a reader has no way to know that, and the app was inviting exactly that mistake —
        # the Library's Ideal-lap column holds two rows 0.57 s apart for no reason but lap count.
        #
        # IT SITS BETWEEN THE TILES AND THE TABLE, not in a tooltip and not only in the remainder
        # note under the table: the tiles are the surface a reader takes the number off, and the
        # note is ~10 rows further down a page that is already ~1400 px tall here. The remainder
        # note keeps the arithmetic ("these N segments hold X of the Y"); this keeps the sample, so
        # neither says the other's sentence twice.
        #
        # A WrapLabel at the app's prose measure, same construction as `note` — a paragraph
        # the column has to make room for, capped so a maximized 1728 px dashboard does not set it
        # to 130 characters a line, and NOT a longer tile caption: at 1280x800 the Stats body's
        # minimum width is 444 px against a 445 px viewport, so a caption that grows its grid
        # column by a single pixel puts a horizontal scrollbar on the page.
        self.sample = WrapLabel("")
        self.sample.setProperty("role", "Note")
        self.sample.setFont(theme.ui_font(theme.CAPTION))
        self.sample.setMaximumWidth(theme.EMPTY_MEASURE_PX)
        self.table = ReportTable(IDEAL_COLUMNS, ROW_HEIGHT)
        self.table.setToolTip(IDEAL_TOOLTIP)
        # Interactive like CORNERS / BRAKING / STRAIGHTS — a row of a table headed "where it
        # lives" that cannot show you where is half an answer. Same corner_clicked pathway
        # (maximize-aware in CentralView); a STRAIGHT row rings the corner FEEDING it, which is
        # the rule the STRAIGHTS table already follows.
        #
        # NOT SORTABLE, and that is the one place it departs from those three. The row order IS
        # the content here — it is the ranking the tooltip explains — and a header click would
        # silently replace an argued order with an arbitrary one while the tooltip kept claiming
        # the first.
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFocusPolicy(Qt.ClickFocus)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        # The arithmetic the table cannot show: a top-N list under a total is a contradiction
        # unless the remainder is named. Filled per refresh, hidden with the section.
        #
        # A WrapLabel (not QLabel + setWordWrap) because it is a PARAGRAPH the column has to make
        # room for — the trap widgets.WrapLabel exists for, and the reason three other surfaces
        # painted their second line outside their own box. Capped at the app's prose measure and
        # left UN-ALIGNED in the layout, the pair of moves stats_panel's zero-lap prose documents: a
        # maximized dashboard is 1728 px wide and an uncapped note would set ~130 characters to
        # the line.
        self.note = WrapLabel("")
        self.note.setProperty("role", "Note")
        self.note.setFont(theme.ui_font(theme.CAPTION))
        self.note.setMaximumWidth(theme.EMPTY_MEASURE_PX)

    def widgets(self) -> tuple:
        """Reading order. The TUPLE is the row of the two tiles: the page lays it out on its own
        reflowing tile grid (`StatsView._mount`), because tile layout is the page's."""
        return (self.heading, (self.t_theoretical, self.t_gap), self.sample, self.table,
                self.note)

    def tables(self) -> tuple:
        return (self.table,)

    def refresh(self, session):
        """The IDEAL LAP block: the theoretical best, what it says is on the table, and the
        DECOMPOSITION — which segments that gap lives in, on which lap you drove each one, and
        how repeatable it is.

        THE GATE IS NOT THE OLD ONE, and that is the point of this block. The tile used to hide
        when `sector_count() == 0`, which was right while the "theoretical best" was a sum of
        best SECTOR splits — with no sector line a lap is one sector and the sum degenerated to
        the best lap time. It is a corner/straight composite now, so sector lines have nothing to
        do with it (driven through set_timing_lines at 0/1/2/3 lines the total is byte-identical),
        and `sector_count()` is 0 on every recording the owner has: the old gate hid the corrected
        number on all five of them. The condition that actually means "this would be a duplicate
        of a lap you already see" is `ideal_donor_lap_id()` — one lap won every segment, so the
        ideal IS that lap — and that is what hides it now, along with the no-composite case
        (`ideal_total() is None`: no corner detected, so the "partition" is the whole lap).

        The tiles take `set_target_tile`'s PROVISIONAL/DEGRADED mute: both are synthesized, so
        both share the lap timing's authority. The TABLE does not, for the same reason the
        CORNERS and STRAIGHTS reports beside it do not — its cells are measured segment times
        and differences between them, and the page's own trust banner already qualifies the page.
        """
        sb = getattr(session, "ideal_segment_bests", lambda: None)()
        best_id = session.best_lap_id() if hasattr(session, "best_lap_id") else None
        single = (session.ideal_donor_lap_id()
                  if hasattr(session, "ideal_donor_lap_id") else None)
        rows = sb.decomposition(best_id) if (sb is not None and best_id is not None) else None
        has = sb is not None and single is None and rows is not None
        self._set_visible(has)
        if not has:
            self.table.setRowCount(0)
            self.note.setText("")
            self.sample.setText("")
            return
        total = sb.total
        # THE SAMPLE, ON BOTH TILES AND IN THE LINE UNDER THEM. `sample` is the one accessor
        # (corner_model.IdealSample) so this block and the hero's `vs ideal` chip cannot answer
        # "how many laps is this over" differently.
        #
        # The CAPTION carries the lap count and the tooltip carries the mechanism, which is the
        # split this page already uses for its other sampled target: `median · 65 clean laps`
        # names its own n on the tile and explains it on hover. Only the theoretical tile's
        # caption grows — `on the table · vs your best` is at the width the grid column already
        # affords, and the line below carries the count for both.
        # BOTH STRINGS COME OFF `IdealSample` ITSELF (caption / sentence), not from a local
        # f-string. The laps.csv trailer and the exported HTML report print the same two, and a
        # leaving-the-app surface has no tooltip to fall back on when it drifts — so the disclosure
        # is defined on the value object that owns the counts (§5.4).
        smp = sb.sample
        set_target_tile(self.t_theoretical, total, THEORETICAL_TOOLTIP, session,
                        caption=smp.caption())
        self.sample.setText(smp.sentence())
        # The best lap's time READ OFF THE COMPOSITE, not off `session.lap_time`. They are the
        # same number — `corners.segment_times` asserts a lap's segments sum exactly to its lap
        # time, which is the guarantee the whole composite stands on — and taking it from the same
        # matrix the gains come from makes `gap == Σ gains` true by construction rather than by
        # agreement between two accessors. The tile and the table cannot drift.
        gap = float(sb.times[sb.lap_ids.index(best_id)].sum()) - total
        # SIGNED, and negative, because that is the direction the number moves your lap time —
        # the same convention the hero's `vs ideal` chip and the trend tile use. Formatted with
        # the ASCII sign f-strings produce, not a typographic minus: this is a MONO tile and the
        # app's one measured mark-in-a-mono-face defect is exactly that substitution.
        set_target_tile(self.t_gap, gap, IDEAL_GAP_TOOLTIP, session, text=f"{-gap:+.2f} s")

        shown = [r for r in rows if r.gain >= IDEAL_GAIN_FLOOR]
        rest = [r for r in rows if r.gain < IDEAL_GAIN_FLOOR]
        t = self.table
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(shown))
        for r, row in enumerate(shown):
            name = QTableWidgetItem(row.label)
            name.setData(RING_ROLE, row.ring_cid)
            cells = [name, num_item(f"{row.gain:.2f}"), num_item(f"{row.beat} / {row.n}"),
                     # 1-BASED, the app-wide display rule for a lap number (the ★ best lap, the
                     # Vmax tile's caption, the sparkline's x axis). None only on a POINT segment,
                     # which carries no gain and therefore never clears the floor above.
                     num_item(DASH if row.donor is None else str(row.donor + 1))]
            # WHY THIS ROW IS HERE, on the row. Neither visible column is sorted — the order is
            # their PRODUCT — and the app has been here before: the CORNERS table's loss column is
            # marked by σ × median-loss and read "as if it were ordered by its own numbers and was
            # not", which is recorded there as a defect a reader is given no way out of. The way
            # out is the arithmetic, per row, in the reader's own numbers.
            tip = (f"{row.label}: your best lap gave away {row.gain:.2f} s here. "
                   f"{row.beat} of {row.n} clean laps drove it at least as fast as your best lap "
                   f"did"
                   + ("" if row.donor is None else f"; the quickest was lap {row.donor + 1}")
                   + f". Ranked {r + 1} of {len(shown)} by {row.gain:.2f} × {row.beat}/{row.n} = "
                     f"{row.priority:.3f}.")
            for c, cell in enumerate(cells):
                cell.setToolTip(tip)
                t.setItem(r, c, cell)
        t.blockSignals(False)
        t.fit()
        # The remainder, always — the tile above states the WHOLE gap and the table shows part of
        # it, so without this line the page would print a total and a list that do not add up
        # (the same class of defect the Coaching headline's B12 rounding note records).
        #
        # AND IT ADDS UP IN THE READER'S OWN NUMBERS, which is the rule the Coaching headline
        # (B12) and the retired digest tile (L5-02) wrote down and this line was breaking. The
        # cells are printed at 2 dp; summing the RAW floats made the note disagree with the column above it on 4 of 4
        # real recordings — D24 3 chapters printed 0.21 0.21 0.13 0.12 0.08 0.17 0.13 0.10 0.11
        # 0.14 (= 1.40) over a sentence saying 1.39, so a reader adding the visible cells and the
        # stated remainder got 1.65 under a tile printing -1.64 s. Sandown chapter 1 was off the
        # other way (0.92 printed, 0.94 stated). The three numbers here are therefore:
        #   shown_s — the SUM OF THE PRINTED CELLS, each rounded exactly as the table rounds it;
        #   gap_s   — the TILE's own number, rounded exactly as set_target_tile rounds it;
        #   rest_s  — whatever closes the arithmetic, BY CONSTRUCTION rather than by agreement.
        # `rest_s` is a difference, not a second sum, for that last reason: summing the sub-floor
        # rows at 2 dp would give a number that happens to close on today's data and would not on
        # tomorrow's. A rounding penny can therefore land in the remainder — that is where it is
        # cheapest, since those rows are not on screen to contradict it.
        shown_s = round(sum(round(r.gain, 2) for r in shown), 2)
        gap_s = round(gap, 2)
        rest_s = round(gap_s - shown_s, 2)
        # The "Stitched from N of your M clean laps" sentence used to open this note and now opens
        # `sample` above the table, where it sits with the tiles it qualifies instead of
        # under ten rows of decomposition. One sentence, one place: printing the sample twice on
        # one block is how two surfaces drift apart.
        #
        # BOTH COUNTS CAN BE 1, and each clause agrees with its own (K2): a one-row plan printed
        # "These 1 segments hold", and a one-row remainder — 96 of the 240 composites
        # tests/test_stats_ideal.py sweeps — "the other 1 hold … between them, … each".
        n_shown, n_rest = len(shown), len(rest)
        lead = (f"This {n_shown} segment holds" if n_shown == 1
                else f"These {n_shown} segments hold")
        tail = (f"holds {rest_s:.2f} s, under {IDEAL_GAIN_FLOOR:.2f} s" if n_rest == 1
                else f"hold {rest_s:.2f} s between them, under {IDEAL_GAIN_FLOOR:.2f} s each")
        self.note.setText(
            f"{lead} {shown_s:.2f} s of the {gap_s:.2f} s; the other {n_rest} {tail}. Ranked by "
            "gain × how often you have already matched your best lap there.")

    def _set_visible(self, on: bool) -> None:
        """Show/hide the IDEAL LAP block as a UNIT — heading, both tiles, the sample line, the
        table and the remainder note. A tile left behind under a hidden heading is the defect this
        exists to prevent (it is what the old SECTORS placement would have produced the moment the
        two gates disagreed), and a sample line describing a composite that is not on screen is
        the same defect one widget over."""
        self.heading.setVisible(on)
        self.t_theoretical.setVisible(on)
        self.t_gap.setVisible(on)
        self.sample.setVisible(on)
        self.table.setVisible(on)
        self.note.setVisible(on)

    def _on_row_selected(self):
        """A decomposition row rings the corner it names — the corner itself for a corner row,
        the corner FEEDING it for a straight (SegmentBests.ring_cid). Same corner_clicked
        pathway as the other three tables."""
        rows = self.table.selectionModel().selectedRows()
        if rows:
            item = self.table.item(rows[0].row(), 0)
            self._on_ring(item.data(RING_ROLE) if item else None)
        else:
            self._on_ring(None)
