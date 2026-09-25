"""CORNERS, a Stats-page section: corner by corner over the clean laps — session-best / median / σ
time, the median loss against each corner's own Best (the three erratic-and-slow corners marked),
apex speeds and grip — with the phase-share tile over it (where the corner loss goes: entry · apex
· exit) and the reconciliation line under it that ties this table's loss to the Coaching tab's and
to the ideal lap's. A row click rings the corner on the map; a right-click on a Best cell opens the
provenance inspector. A section of `stats_panel.StatsView`: build, refresh and copy here; the page
places it (see `stats_common`).
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QAbstractItemView, QMenu

from . import provenance, provenance_panel, theme, units
from ._signal import plural

# The Coaching panel's OWN row filter and top-N, imported (not re-implemented) so the CORNERS note
# quotes the Coaching tab's ranking and totals exactly (`stats_straights` does the same for its
# note) — L5-02.
from .coaching_panel import PANEL_TOP_N, _ranked_shown
from .lap_table import NUM_ROLE, _NumItem, set_corner_direction
from .stats_common import ROW_HEIGHT, ReportTable, keep_blanks_last, section_heading
from .widgets import DASH, Tile, WrapLabel

# "Med loss", not "Med loss vs best corner": MEASURED, not chosen. The header section is 100 px
# and the face needs 56 px for "Med loss", 104 px for "…vs best" and 148 px for "…vs best corner",
# while the header already asks for 800 px inside a 636 px viewport — so both longer labels elide
# to a "Med loss vs…" that names nothing. The baseline is named where there IS room: the caption
# under the table (built by `CornersSection._note_text`) and the table tooltip.
#: The Best column's index — the one cell in this table the provenance inspector can explain
#: (see CornersSection._on_context_menu). Derived from the list below rather than typed, so a
#: column inserted before it moves the menu with it.
_CORNER_BEST_COL = 1

# ONE NAME FOR GRIP, the Corners tab's (`lap_table.CORNER_COLUMNS`) and the map's: it is the same
# `driving.corner_grip` reading, and until #350's follow-up this column alone called it "Grip %".
# The % moved to the section heading, the way the Corners tab puts it in its unit caption.
# Measured on Sandown 3h and MK_18_09: the header's ink is 42 -> 57 px, the column 85 -> 101 px and
# the table 718 -> 734 px. That costs no column at the 1260 / 1420 / 1900 px dashboard widths (same
# composition, nothing hidden). In a quadrant this table already scrolls, and 16 px more of it does.
CORNER_COLUMNS = ["Corner", "Best", "Median", "σ (s)", "Med loss", "Apex best", "Apex med",
                  theme.estimated_label("Grip")]
WORST_TINT_N = 3          # the top-N inconsistency-score corners get the loss cell marked
# ...and MARKED, not merely tinted. The cue used to be hue and nothing else — tinted and plain
# cells were identical in size, weight, family, alignment and format, and carried the same tooltip
# — while the ranking is by σ × median-loss, a PRODUCT that is not a column on screen. So the
# column read as if it were ordered by its own numbers and was not: on D24 a tinted +0.09 (C11) sat
# directly under a plain +0.11 (C10), and a plain +0.10 (C7) beat the tinted +0.09. A reader with
# no colour, or with the colour and no explanation, was given a contradiction either way.
#
# THE MARK IS ▲, NOT ⚠, AND THAT IS THE WHOLE POINT. ⚠ is the app's DISTRUST glyph: the lap grid
# hangs it on a GPS-dropout lap, the DATA TRUST card on a caveated term, and both mean "this
# number may not be sound". These three cells mean the opposite — they are the corners with the
# most time available, the three the driver should practise FIRST. Marking the app's best news
# with its "don't trust this" glyph told a reader the loudest advertised gains were the flagged
# ones. ▲ is upside, points at the number, and costs nothing to adopt: the reason the old mark
# reused ⚠ was font coverage ("no new codepoint arrives"), and ▲ is in the same measured ledger
# (tests/test_glyph_vocabulary.py's _IN_THE_FACE, asserted against the SHIPPED face).
#
# It is a PREFIX, deliberately: this column is
# fixed-decimal and right-aligned, so right alignment IS decimal alignment (a property measured and
# kept), and a trailing mark would push three of twelve numbers out of the decimal column. Prefixed,
# it hangs to the left of an untouched right edge. The character stays TEXT rather than becoming a
# theme.icon() pixmap because Inter draws it (tests/test_glyph_vocabulary.py measures exactly that)
# and because a cell's icon slot paints at the cell's LEFT edge, a whole column away from the
# right-aligned number it would be marking.
WORST_LOSS_MARK = "▲ "
CORNERS_TOOLTIP = ("Corner-by-corner over the clean laps: session-best / median / σ "
                   "time-in-corner, the median loss VS THE BEST ANYONE DID IN THAT CORNER "
                   "(this column's own Best cell — not your best lap's corner, which is what the "
                   "Coaching page measures against and why its numbers are smaller), apex speeds "
                   "and median grip utilization (ESTIMATED, % of the session's grip envelope). "
                   # One row per corner, so this column is the one grip surface whose only on-screen
                   # comparison is the unsupported one — and it sorts. The shared sentence says so,
                   # and where the supported comparison lives (theme.GRIP_COMPARE_NOTE).
                   f"{CORNER_COLUMNS[-1]}: {theme.GRIP_COMPARE_NOTE} The Corners tab shows it lap "
                   "by lap. "
                   "Every column counts only the laps whose corner was matched to your best lap's "
                   "line on track at entry AND exit: an interpolated corner can be tenths of a "
                   "second out, so it is left out (hover Best or Median for how many laps count), "
                   "and a corner no lap matched shows dashes rather than a guess. "
                   + provenance.CORNER_MATCH_DRIFT + " "
                   f"The worst 3 loss cells are marked {WORST_LOSS_MARK.strip()} and "
                   "tinted — ranked by σ × median-loss (erratic AND slow), which is why the marked "
                   "cells are not simply this column's three largest numbers; hover one for its "
                   "own score. It is a consistency ranking, not the Coaching tab's list of what "
                   "to work on (time lost against your best lap), so the two need not agree. "
                   "Click a row to ring the corner's apex on the map; click a column header to "
                   "sort.")


def _corner_count_tip(report) -> str:
    """The CORNERS table's Best/Median hover when not every lap counted: how many did, and why the
    rest did not. "" when every lap was matched on track (nothing to disclose) or the report never
    counted its laps (`n_laps` None). A corner NO lap matched says so instead of explaining a dash
    as missing data (C4: see stats.corner_report for the measurement)."""
    n, of = report.n, getattr(report, "n_laps", None)
    if of is None or n >= of:
        return ""
    if n == 0:
        return (f"No time for C{report.cid}: on none of the {plural(of, 'clean lap')} could its "
                "entry and exit both be matched to your best lap's line on track, and an "
                "interpolated corner time can be tenths of a second out.")
    them = "it is" if of - n == 1 else "they are"   # one lap left out is "it" (K2)
    return (f"Over the {n} of {of} clean laps matched on track at C{report.cid}'s entry and exit. "
            f"On the other {of - n} the corner was interpolated between its neighbours, which can "
            f"put its time tenths of a second out, so {them} left out of this whole row.")


class CornersSection:
    """The CORNERS heading, the phase-share tile (`t_phase`), the corner `table` and the
    reconciliation `note` under it. `on_ring(cid)` is the page's `corner_clicked` — a row click
    rings that corner on the map, None on deselect. `session_of()` returns the page's CURRENT
    session, which a right-click on a Best cell inspects: the page's session can be swapped under
    a live page, and the menu reads it at the click, as it did when it was the page's own slot."""

    def __init__(self, on_ring, session_of):
        self._on_ring = on_ring
        self._session_of = session_of
        self.heading = section_heading("CORNERS")
        # The phase-loss headline: where the session's corner time goes (entry/apex/exit),
        # from the per-lap aligned thirds decomposition — coach-grade, and computed, not
        # modeled. Hidden with the section / without phase data.
        # "WHERE BOTH WERE MATCHED": since C4 (#331) `Session.phase_report` counts a lap's triple
        # only where that lap AND the best lap it is subtracted from were matched on track at the
        # corner's edges. This sentence opened "Every clean lap's" from before that.
        phase_tip = ("Each clean lap's Δt-vs-best through each corner — where the lap and your "
                     "best lap were both matched on track at its entry and exit, the laps the "
                     "CORNERS table counts — split into "
                     "equal-distance entry / apex / exit thirds (the same decomposition the "
                     "coaching reasons use), medianed per corner, positive parts summed. "
                     "Seconds = what a typical lap gives away in that phase across the whole "
                     "track; hover a corner's loss cell for its own triple.")
        # ONE TILE, NOT THREE (R11 / PS-5). The three were one fact — how the corner loss splits —
        # printed as three headline numbers, and their seconds summed to one more "time on the
        # table" on a page that already had five (PS-2). The shares stay on the face in track
        # order; the seconds behind them are on the hover.
        self._phase_tip = phase_tip
        self.t_phase = Tile("of corner loss · entry · apex · exit")
        self.t_phase.setToolTip(phase_tip)
        self.table = ReportTable(CORNER_COLUMNS, ROW_HEIGHT)
        self.table.setToolTip(CORNERS_TOOLTIP)
        # The corner-direction arrow in column 0 paints at the app's ICON_PX rather than at the
        # style's PM_SmallIconSize (see lap_table.CornerTable for the same statement). It fits the
        # ROW_HEIGHT with 4 px either side.
        self.table.setIconSize(QSize(theme.ICON_PX, theme.ICON_PX))
        # Unlike the other stats tables this one is interactive: row-select → map ring,
        # header-click → sort (numeric via _NumItem, the lap-table idiom).
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setFocusPolicy(Qt.ClickFocus)
        self.table.itemSelectionChanged.connect(self._on_row_selected)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._on_context_menu)
        self.table.horizontalHeader().sortIndicatorChanged.connect(
            keep_blanks_last)
        # Explicit initial indicator: TRACK ORDER (corner id ascending). Without this, Qt's
        # untouched default indicator is column-0 DESCENDING and the first fill's
        # setSortingEnabled(True) would silently reverse the track.
        self.table.horizontalHeader().setSortIndicator(0, Qt.AscendingOrder)
        # THE RECONCILIATION LINE. This page and the Coaching tab both print a per-corner "loss"
        # and they are 3.8x apart in total on the owner's own recording (D24 0060: 3.93 s here,
        # 1.02 s there, and corner by corner from 1.3x to 2450x), because they answer different
        # questions: this column is measured against each corner's own Best — the quickest anyone
        # went through it, usually not your best lap — and Coaching is measured against your best
        # lap. Both are correct; neither said which it was, one tab apart, under the same word.
        # The header has no room to say it (see CORNER_COLUMNS), and a tooltip is not the face, so
        # it is said here, with the numbers computed live rather than baked.
        self.note = WrapLabel()
        self.note.setProperty("role", "TableNote")

    def widgets(self) -> tuple:
        """Reading order. The TUPLE is the phase-share tile's row: the page lays it out on its own
        reflowing tile grid (`StatsView._mount`), because tile layout is the page's."""
        return (self.heading, (self.t_phase,), self.table, self.note)

    def tables(self) -> tuple:
        return (self.table,)

    def _note_text(self, session, report) -> str:
        """The one line that connects this page's answers to each other, live.

        Every number here is READ, never baked: the same fix the digest tile had, for the same
        reason — an empirical range typed into shipping copy is right on the recording it was
        measured on and quietly wrong on the next one. The Coaching total costs one
        `coaching_opportunities()` (~3-6 ms on the 38-lap D24 pair, on a refresh path that runs on
        load / re-segment / undo, never per tick); the alternative is printing a number this page
        cannot check, which is how two surfaces drift apart in the first place.
        """
        total = sum(r.median_loss_s for r in report if r.median_loss_s is not None)
        parts = [f"Med loss is measured against each corner's own Best above — the quickest "
                 f"anyone went through it, which is usually not your best lap's corner. "
                 f"Summed, that is {total:.2f} s."]
        # C4: which cells this table counted, when that is not all of them — the one thing that
        # makes its numbers differ from a surface that counts every lap (stats.corner_report).
        counted = [(r.n, r.n_laps) for r in report if getattr(r, "n_laps", None) is not None]
        left_out = sum(of - n for n, of in counted)
        if left_out:
            was, are = ("was", "is") if left_out == 1 else ("were", "are")
            parts.append(
                f"Only corners matched on track count: {sum(n for n, _ in counted)} of "
                f"{sum(of for _, of in counted)} lap × corner times here; the other {left_out} "
                f"{was} interpolated between matched points, and {are} shown muted lap by lap.")
            untimed = [f"C{r.cid}" for r in report if getattr(r, "n_laps", None) and r.n == 0]
            if untimed:
                parts.append(f"No lap matched {', '.join(untimed)} on track, so "
                             f"{'it has' if len(untimed) == 1 else 'they have'} no times.")
        opp_fn = getattr(session, "coaching_opportunities", None)
        opp = opp_fn() if opp_fn is not None else None
        # The Coaching tab's own totals are over its RANKED rows (the corners that survived its
        # per-corner evidence gate), so this reconciliation quotes the same set — a "totals X s"
        # here that included abstained corners would not reconcile with the page it names.
        rows = _ranked_shown(opp) if getattr(opp, "enough", False) else []
        if rows:
            top = rows[:PANEL_TOP_N]
            # Since #339 Coaching counts a cell only where the lap AND the best lap matched that
            # corner on track — the rule this table counts by — so when this table left some out,
            # the sentence says Coaching did too: a reader who has just been told cells were left
            # out here would otherwise have to guess whether the total beside it counts them.
            # (It said the OPPOSITE until W1. COASTING still counts every cell, deliberately —
            # see CornerModel.lap_corner_resolved — and is not named here.)
            same = ", leaving out the same interpolated times," if left_out else ""
            parts.append(
                f"The Coaching tab measures the SAME corners against your best lap{same} and "
                f"totals {sum(r.time_lost for r in rows):.2f} s, "
                f"{sum(round(r.time_lost, 2) for r in top):.2f} s of it in its top {len(top)}.")
        gap = self._ideal_gap(session)
        if gap is not None:
            parts.append(f"Your ideal lap is {gap:.2f} s under your best.")
        parts.append("Different baselines, different questions — not three estimates of one.")
        return " ".join(parts)

    @staticmethod
    def _ideal_gap(session) -> float | None:
        """best lap − ideal total, or None when either half is missing (the honesty rule: no
        number rather than a 0.00 that reads as a measurement)."""
        ideal_fn = getattr(session, "ideal_total", None)
        best_fn = getattr(session, "best_lap_id", None)
        time_fn = getattr(session, "lap_time", None)
        if ideal_fn is None or best_fn is None or time_fn is None:
            return None
        ideal = ideal_fn()
        best_id = best_fn()
        if ideal is None or best_id is None:
            return None
        best = time_fn(best_id)
        return None if best is None else float(best) - float(ideal)

    def refresh(self, session, unit, u_label):
        report = getattr(session, "corner_report", list)() or []
        has = bool(report)
        self.heading.setVisible(has)
        self.table.setVisible(has)
        self.note.setVisible(has)
        phase = (getattr(session, "phase_report", lambda: None)() if has else None)
        phase_rows = (dict(zip(phase.cids, phase.rows, strict=True))
                      if phase is not None else {})
        self._refresh_phase_tiles(phase)
        if not has:
            self.table.setRowCount(0)
            self.note.setText("")
            return
        # The Grip column's % lives here, as it does in the Corners tab's unit caption: the header
        # carries the name every grip surface shares (see CORNER_COLUMNS).
        self.heading.setText(f"CORNERS · speeds in {u_label} · grip %")
        self.note.setText(self._note_text(session, report))
        # The worst corners by σ × median-loss get their loss cell MARKED and tinted in the
        # "behind" hue — erratic AND slow, a consistency ranking and not Coaching's. Capped at
        # WORST_TINT_N and at half the field: a tint that covers every row highlights nothing.
        k = min(WORST_TINT_N, max(1, len(report) // 2))
        ranked = sorted(report, key=lambda r: -r.score)[:k]
        worst = {r.cid: r for r in ranked if r.score > 0}
        behind = QColor(theme.behind_colour())
        mono = theme.mono_font(theme.TABLE)

        def cell(val, fmtstr):
            item = _NumItem(fmtstr.format(val) if val is not None else DASH)
            item.setData(NUM_ROLE, val)
            item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            item.setFont(mono)
            return item

        t = self.table
        t.setSortingEnabled(False)   # Qt requirement: never fill a live-sorting table
        t.blockSignals(True)
        t.clearSelection()
        t.setRowCount(len(report))
        for r, cr in enumerate(report):
            # The direction goes in the cell's ICON slot (lap_table.set_corner_direction), so the
            # sort key and the text stay the bare corner number.
            name = set_corner_direction(_NumItem(f"C{cr.cid}"), cr.direction)
            name.setData(NUM_ROLE, cr.cid)   # numeric key: C10 must not sort before C2
            t.setItem(r, 0, name)
            best, median = cell(cr.best_s, "{:.2f}"), cell(cr.median_s, "{:.2f}")
            count_tip = _corner_count_tip(cr)
            if count_tip:
                best.setToolTip(count_tip)
                median.setToolTip(count_tip)
            t.setItem(r, 1, best)
            t.setItem(r, 2, median)
            t.setItem(r, 3, cell(cr.sigma_s, "{:.2f}"))
            loss = cell(cr.median_loss_s, "+{:.2f}")
            # The tooltip is built in the SAME branch as the cue, so the reason can never be
            # missing from a cell that carries the mark. Two independent lines when both apply:
            # WHY THIS CELL IS MARKED (the ranking score, which is not a column on screen — a
            # reader comparing the marked +0.09 with the plain +0.11 above it has no other way to
            # find out) and the corner's own phase triple.
            tips = []
            wr = worst.get(cr.cid)
            if wr is not None:
                loss.setForeground(behind)
                loss.setText(WORST_LOSS_MARK + loss.text())
                # ONE marked corner is what a layout of three corners or fewer gets (`k` above),
                # and it is not "one of the 1" (K2).
                which = (f"One of the {len(worst)} most erratic-and-slow corners"
                         if len(worst) > 1 else "The most erratic-and-slow corner")
                tips.append(
                    f"{which} — ranked by "
                    f"σ × median loss = {wr.sigma_s:.2f} × {wr.median_loss_s:.2f} = "
                    f"{wr.score:.3f} s², not by this column alone, and not the Coaching tab's "
                    "ranking (time lost against your best lap).")
            tri = phase_rows.get(cr.cid)
            if tri is not None:
                # The corner's own phase matrix, on hover — where INSIDE this corner the
                # typical lap loses (positive = slower than best over that third).
                tips.append(f"Median vs best — entry {tri[0]:+.2f} · "
                            f"apex {tri[1]:+.2f} · exit {tri[2]:+.2f} s")
            if tips:
                loss.setToolTip("\n".join(tips))
            t.setItem(r, 4, loss)
            t.setItem(r, 5, cell(units.convert_speed(cr.apex_best_kmh, unit)
                                 if cr.apex_best_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 6, cell(units.convert_speed(cr.apex_median_kmh, unit)
                                 if cr.apex_median_kmh is not None else None, "{:.1f}"))
            t.setItem(r, 7, cell(cr.grip_median * 100.0
                                 if cr.grip_median is not None else None, "{:.0f}"))
        t.blockSignals(False)
        t.setSortingEnabled(True)
        t.fit()

    def _refresh_phase_tiles(self, phase):
        """The where-the-time-goes headline tile: the percent of the lost corner time per phase,
        entry · apex · exit, with the seconds behind each on the hover. Hidden when there is no
        phase data (no corners / no best / nothing lost)."""
        share = getattr(phase, "share", None)
        fr = share.fracs() if share is not None else None
        if fr is None:
            self.t_phase.setVisible(False)
            return
        secs = (share.entry_s, share.apex_s, share.exit_s)
        self.t_phase.setVisible(True)
        self.t_phase.set(" · ".join(f"{f * 100.0:.0f}" for f in fr) + " %")
        self.t_phase.setToolTip(
            "Lost on entry {:.1f} s · at the apex {:.1f} s · on exit {:.1f} s.\n\n".format(*secs)
            + self._phase_tip)

    def _on_context_menu(self, pos):
        """Right-click the CORNERS table's Best cell → "Inspect this number…".

        ONLY that cell. The third of the app's three inspectable numbers is the corner best, and
        offering the menu on Median or σ would advertise an inspection `provenance.py` has no
        builder for — the scope is three numbers, and the menu is where that is either honest or
        not. The cid comes from the row's own name item for the same reason
        `_on_row_selected` reads it there: this table sorts."""
        item = self.table.itemAt(pos)
        session = self._session_of()
        if item is None or session is None or item.column() != _CORNER_BEST_COL:
            return
        name = self.table.item(item.row(), 0)
        cid = name.data(NUM_ROLE) if name is not None else None
        prov = session.corner_best_provenance(int(cid)) if cid is not None else None
        if prov is None:
            return
        menu = QMenu(self.table)
        act = menu.addAction(provenance_panel.MENU_LABEL)
        act.setToolTip("Show the raw GPS fixes, the method and the window this number came from")
        if menu.exec(self.table.viewport().mapToGlobal(pos)) is act:
            provenance_panel.open_for(prov, self.table.window())

    def _on_row_selected(self):
        """Emit the selected row's corner cid (None on deselect) — read from the row's own
        item (sorting reorders rows, so a row→cid list would go stale)."""
        rows = self.table.selectionModel().selectedRows()
        if rows:
            item = self.table.item(rows[0].row(), 0)
            self._on_ring(item.data(NUM_ROLE) if item else None)
        else:
            self._on_ring(None)
