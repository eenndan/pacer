"""Regression tests for the Coaching page's layout + units (QA-sweep batch B24).

Four findings, all on ``studio/coaching_panel.py``:

  * L5-06 — at the app's own minimum window the Coaching page is a 280x196 panel with a 270 px
    viewport, and the three numeric columns took 198 of it unconditionally: the reason cell (the
    only column carrying prose) fell back to its header's own 100 px size hint, overflowed the
    viewport into a HORIZONTAL scrollbar over a table that could not show one whole row, and the
    header painted as a hard-clipped "How to find" — 82 px advancing inside a 100 px section, so
    the clip is the QSS padding and a naive width test PASSES it. All four headers also carried an
    empty tooltip, so there was nothing to hover for the full label either.
  * L5-08 — maximized, the page was 3 rows in 808 px (78 % dead canvas as measured after #B23's row
    growth; the sweep filed 79/83 %) while the model had 11 corners ranked and the modal fitted all
    11 in a third of the area. And every header was centred by `defaultAlignment` over left-aligned
    cells, so "How to find it" sat 611 px from the sentence it labels.
  * L5-09 — the ±σ column printed a bare "±0.12" while `coaching.reason_sentence` spells the
    IDENTICAL statistic "σ 0.12 s" — and since the model batch dropped summarize()'s top_n gate,
    both forms now meet on the shipped dialog (3 of 11 rows on the D24 three-chapter fixture). That
    column is now "Done it?" (a count over its denominator); the RULE it established — a table cell
    never states a number without the unit or sample that makes it checkable — is what is pinned.
  * L5-10 — the ESTIMATED brake-point hint is derived from `apex − d` under CONSTANT-DECEL,
    straight-line braking, which the friction circle only affords on the APPROACH. On D24's C10 the
    optimum lands at 870.6 m — 59 m inside an 811.6..891.1 m corner window, 19.4 m before the apex —
    so the cell asked for "Brake ~50 m later" beside its own measured "~0.36 s longer on the
    brakes". (The sweep's headline arithmetic, "50.4 m is 2.1 s of travel, 7x the 0.30 s", conflates
    travel time with time LOST and is deliberately not repeated here — the evidence is geometric.)

Every layout assertion here is on PIXELS (`rowHeight`, `columnWidth`, `sectionSize`, a real
`fontMetrics` advance against the section's own chrome) — never on `strings().elided`, which models
a single-line right-elide and reports these wrapped cells wrongly in both directions.

Run: QT_QPA_PLATFORM=offscreen python tests/test_coaching_panel_layout.py
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QStyle,
    QStyleOptionViewItem,
)

_APP = QApplication.instance() or QApplication([])

from studio import coaching, theme  # noqa: E402
from studio.coaching_panel import (  # noqa: E402
    _COL_CORNER,
    _COL_LOST,
    _PANEL_COL_REACH,
    _PANEL_COL_REASON,
    BRAKE_HINT_MAX_PAST_TURN_IN_M,
    PANEL_TOP_N,
    REASON_MIN_PX,
    OpportunitiesPanel,
    _brake_point_hint,
    _fit_reason_rows,
    _header_chrome_px,
)

theme.apply_theme(_APP)

# The panel geometry the app's OWN minimum window produces, measured on the real StudioWindow with
# the QA harness on fixture F.B: window 1047x434 -> Coaching page 280x196 -> table viewport 270 px.
# KEPT AT 280 THOUGH THE APP NO LONGER GOES THAT NARROW. Deleting the left column's hand-written
# 280 px minimum (it REPLACED the lap panel's honest 292 and squeezed the tab bar into scroll
# arrows — see tests/test_design_system.py check 4b) moved this floor to 292, so 280 is now 12 px
# STRICTER than anything a user can produce. That is the safe direction for a layout test, and
# widening it to match would be loosening a pin for no gain.
MIN_PANEL = (280, 196)

# The D24 C10 geometry the finding rests on (best lap 19, single chapter). All metres are the
# REFERENCE (best-lap) odometer, the frame BOTH Opportunity.entry_dist and BrakeHabit carry.
C10_ENTER, C10_EXIT, C10_APEX = 811.6, 891.1, 890.0
C10_ACTUAL, C10_OPTIMAL = 820.2, 870.6


def _reason(kind=coaching.REASON_BRAKING, sigma=0.12):
    return coaching.Reason(kind=kind, contribution=0.05, apex_speed_deficit=2.4,
                           brake_extra_s=0.36, coast_extra_s=0.0, sigma=sigma)


def _evidence(i: int) -> coaching.Evidence:
    """Real per-corner evidence, alternating the two reach states — the "Done it?" cell's width and
    the reason sentence's extra clause both come from here, and both drive the row heights and
    column budget this whole batch is measured in."""
    reach_laps = 4 if i % 2 else 1
    return coaching.Evidence(
        n_laps=8, reach_laps=reach_laps,
        reach=coaching.REACH_REPEAT if reach_laps > 1 else coaching.REACH_RARE,
        iqr=0.02, abstain=coaching.ABSTAIN_NONE)


def _rows(n: int) -> list[coaching.Opportunity]:
    """n ranked corners, descending loss, each with a genuinely long reason sentence (the wrapped
    cell is what drives the row height this whole batch is measured in)."""
    kinds = (coaching.REASON_BRAKING, coaching.REASON_APEX, coaching.REASON_LINE)
    return [coaching.Opportunity(
        cid=i + 1, direction=(1 if i % 2 else -1), time_lost=0.20 - 0.01 * i,
        entry_dist=100.0 * i, reason=_reason(kinds[i % 3], sigma=0.10 + 0.01 * i),
        phases=coaching.PhaseLoss(entry=0.05, apex=0.03, exit=0.01),
        evidence=_evidence(i)) for i in range(n)]


class _Session:
    """The two accessors the panel reads, nothing else (it is a pacer-free view)."""

    def __init__(self, rows, brake_points=None):
        # The theme rides on the summary in production (summarize computes it once), so build it
        # the same way here — a fixture with no theme never exercises the block above the table.
        self._opps = coaching.Opportunities(enough=True, n_laps=8, median_lap_id=3, rows=rows,
                                            theme=coaching.session_theme(rows))
        self._bps = brake_points or {}

    def coaching_opportunities(self):
        return self._opps

    def coaching_brake_points(self):
        return self._bps


def _panel(rows, size, brake_points=None) -> OpportunitiesPanel:
    """A real OpportunitiesPanel laid out at `size`, settled.

    The explicit 1x1 minimums stand in for the grid splitter: in the app the Coaching page is a
    splitter child that really is squeezed to 280x196 at the window's own minimum, whereas a
    free-standing widget cannot shrink past the table's minimumSizeHint (376 px here)."""
    p = OpportunitiesPanel(_Session(rows, brake_points))
    for w in (p, p.body, p.table):
        w.setMinimumSize(1, 1)
    p.resize(*size)
    p.show()
    for _ in range(4):
        _APP.processEvents()
    return p


# ------------------------------------------------------------------------------- L5-06
def test_narrow_panel_spends_its_width_on_the_prose():
    """L5-06: at the app's own minimum the columns must fit the viewport — no horizontal scrollbar
    — and the reason column must not be starved by the "Done it?" cell.

    On main: colw [64, 78, 56, 100] = 298 px inside a 270 px viewport, so BOTH scrollbars showed and
    the prose column got 100 px. "Done it?" is a glance cue whose content the reason sentence also
    spells out in words ("You have already done this — 4 of 8 laps"), so it is the one that
    yields."""
    p = _panel(_rows(6), MIN_PANEL)
    t = p.table
    widths = [t.columnWidth(c) for c in range(4)]
    viewport = t.viewport().width()

    assert t.isColumnHidden(_PANEL_COL_REACH), (
        "'Done it?' must yield when the reason cannot reach REASON_MIN_PX", widths, viewport)
    assert sum(widths) <= viewport, (
        "the columns must fit the viewport — a horizontal scrollbar hides the numeric columns the "
        "row is identified by", widths, viewport)
    assert not t.horizontalScrollBar().isVisible(), "no horizontal scrollbar at the app's minimum"
    assert widths[_PANEL_COL_REASON] > 100, (
        "the prose column must beat the header-size-hint fallback it used to sit at", widths)
    assert widths[_PANEL_COL_REASON] == viewport - widths[_COL_CORNER] - widths[_COL_LOST], (
        "the reason takes every pixel 'Done it?' freed", widths, viewport)
    print(f"test_narrow_panel_spends_its_width_on_the_prose OK "
          f"(reason {widths[_PANEL_COL_REASON]}px in a {viewport}px viewport, 'Done it?' dropped)")


def test_wide_panel_keeps_the_reach_column():
    """The "Done it?" drop is a BUDGET, not a deletion: once the reason clears REASON_MIN_PX the
    column is back. (This is the guard against 'fixing' L5-06 by simply removing a column.)"""
    p = _panel(_rows(6), (900, 600))
    t = p.table
    assert not t.isColumnHidden(_PANEL_COL_REACH), "'Done it?' must return when there is room"
    assert t.columnWidth(_PANEL_COL_REASON) >= REASON_MIN_PX, t.columnWidth(_PANEL_COL_REASON)
    print("test_wide_panel_keeps_the_reach_column OK "
          f"(reason {t.columnWidth(_PANEL_COL_REASON)}px, 'Done it?' shown)")


def test_reason_header_never_paints_clipped():
    """L5-06: the header label must fit the width the STYLE paints into, or carry an ellipsis.

    The trap the sweep flagged: "How to find it" advances 82 px inside a 100 px section, so a naive
    `advance <= sectionSize` test PASSES while the pixels show a hard-clipped "How to find" — the
    QSS `QHeaderView::section` padding lives inside the section. So measure against the section's
    own chrome (its size hint minus the label's advance), which is what the fix elides against."""
    full = OpportunitiesPanel._COLUMNS[_PANEL_COL_REASON]
    for size in (MIN_PANEL, (360, 400), (520, 500), (1200, 800)):
        p = _panel(_rows(6), size)
        t = p.table
        hdr = t.horizontalHeader()
        item = t.horizontalHeaderItem(_PANEL_COL_REASON)
        chrome = _header_chrome_px(t, _PANEL_COL_REASON, item.text())
        avail = hdr.sectionSize(_PANEL_COL_REASON) - chrome
        painted = hdr.fontMetrics().horizontalAdvance(item.text())
        assert painted <= avail, (
            f"the header is clipped at {size}: {painted}px of '{item.text()}' in {avail}px "
            f"(section {hdr.sectionSize(_PANEL_COL_REASON)}px, chrome {chrome}px)")
        if item.text() != full:
            assert item.text().endswith("…"), ("a shortened header must SAY so", item.text())
            assert full in item.toolTip(), (
                "a shortened header must keep the full label on hover", item.toolTip())
        assert item.toolTip(), ("every header explains itself on hover", item.toolTip())
    print("test_reason_header_never_paints_clipped OK (4 widths)")


def test_every_header_carries_a_tooltip():
    """L5-06: all four panel headers had an EMPTY tooltip, so a clipped header had no escape."""
    p = _panel(_rows(6), MIN_PANEL)
    tips = [p.table.horizontalHeaderItem(c).toolTip() for c in range(4)]
    assert all(tips), tips
    print("test_every_header_carries_a_tooltip OK (4/4)")


# ------------------------------------------------------------------------------- L5-08
def test_page_fills_its_height_with_the_ranking():
    """L5-08: PANEL_TOP_N is the FLOOR, not a ceiling.

    Maximized (a 1432x808 panel on the real window) the page showed 3 rows in 786 px of table while
    the model had 11 ranked — 78 % dead canvas. Assert on PIXELS: a tall panel must fill most of its
    viewport, and must show more than the shortlist when the ranking has more."""
    tall = _panel(_rows(11), (1200, 800))
    t = tall.table
    used = sum(t.rowHeight(r) for r in range(t.rowCount()))
    avail = t.viewport().height()
    assert t.rowCount() > PANEL_TOP_N, (
        "a tall page must show more of the ranking than the shortlist", t.rowCount())
    # The remaining dead canvas is bounded by the MODEL now, not by a literal 3: either every ranked
    # row is on screen, or the next one genuinely would not fit.
    assert t.rowCount() == 11 or used + used / t.rowCount() > avail, (t.rowCount(), used, avail)
    shortlist_px = sum(t.rowHeight(r) for r in range(PANEL_TOP_N))
    assert used > 3 * shortlist_px, (
        "the page must occupy materially more canvas than the shortlist did", used, shortlist_px)
    print(f"test_page_fills_its_height_with_the_ranking OK ({t.rowCount()} of 11 rows, "
          f"{used}px used where the shortlist used {shortlist_px}px of {avail}px)")


def test_short_page_never_drops_below_the_shortlist():
    """The floor holds in the other direction: a page too short for one row still lists the
    shortlist (scrolling to reach a row beats not having it at all)."""
    p = _panel(_rows(11), MIN_PANEL)
    assert p.table.rowCount() == PANEL_TOP_N, p.table.rowCount()
    assert sum(p.table.rowHeight(r) for r in range(p.table.rowCount())) > \
        p.table.viewport().height(), "this fixture is only meaningful while the rows overflow"
    print(f"test_short_page_never_drops_below_the_shortlist OK ({p.table.rowCount()} rows)")


# One real D24 0062 reason sentence, verbatim — it is the string that exposed the gap below, and
# it straddles it only because it is exactly this long.
_D24_REASON = ("brake later / shorter (~0.32 s longer on the brakes), and it carries to the apex."
               " You have rarely done this — 2 of 24 laps.")


def _painter_text_rect(t, col):
    """The rect the DELEGATE really lays text out in for `col` — SE_ItemViewItemText, then the
    further `PM_FocusFrameHMargin + 1` per side that QCommonStyle's viewItemDrawText applies."""
    opt = QStyleOptionViewItem()
    opt.initFrom(t)
    opt.features = QStyleOptionViewItem.HasDisplay
    opt.rect = QRect(0, 0, t.columnWidth(col) - (1 if t.showGrid() else 0), 100)
    tr = t.style().subElementRect(QStyle.SE_ItemViewItemText, opt, t)
    inset = 2 * (t.style().pixelMetric(QStyle.PM_FocusFrameHMargin, opt, t) + 1)
    return tr.width() - inset, 100 - tr.height()


def test_a_reason_row_is_tall_enough_for_where_the_glyphs_actually_land():
    """L5-03, one layer further in — and this one cost a sentence its last word on the shipped page.

    The row-height fit measured the wrap at ``SE_ItemViewItemText``'s width. QCommonStyle's own
    ``viewItemDrawText`` then insets THAT rect by a further ``PM_FocusFrameHMargin + 1`` per side
    before laying the text out — 6 px on this style. Six pixels is not a rounding difference at this
    column's widths: measured on D24 0062, a reason cell 376 px by the old arithmetic is 370 px to
    the painter, and the row's sentence wraps to two lines at 376 and three at 370. The row was
    pinned two lines tall, painted its third line as "…", and the driver lost the end of the
    sentence with no action of their own.

    Asserted as the INVARIANT (every row clears the painter's own requirement) plus a proof that the
    test has teeth (somewhere in the sweep the naive measurement really is a line short)."""
    naive_would_fail, widths = 0, list(range(600, 921, 8))
    for w in widths:
        size = (w, 600)
        p = _panel(_rows(3), size)
        t = p.table
        for r in range(t.rowCount()):
            t.item(r, _PANEL_COL_REASON).setText(_D24_REASON)
        _fit_reason_rows(t, _PANEL_COL_REASON)
        avail, pad_v = _painter_text_rect(t, _PANEL_COL_REASON)
        fm = t.fontMetrics()
        need = fm.boundingRect(QRect(0, 0, avail, 0), Qt.TextWordWrap,
                               _D24_REASON).height() + pad_v
        opt = QStyleOptionViewItem()
        opt.initFrom(t)
        opt.features = QStyleOptionViewItem.HasDisplay
        opt.rect = QRect(0, 0, t.columnWidth(_PANEL_COL_REASON), 100)
        section = t.style().subElementRect(QStyle.SE_ItemViewItemText, opt, t).width()
        naive = fm.boundingRect(QRect(0, 0, section, 0), Qt.TextWordWrap,
                                _D24_REASON).height() + pad_v
        if naive < need:
            naive_would_fail += 1
        for r in range(t.rowCount()):
            assert t.rowHeight(r) >= need, (
                "a reason row must fit the text where the PAINTER lays it out, not where the "
                "section says it could", size, r, t.rowHeight(r), need,
                t.columnWidth(_PANEL_COL_REASON))
    assert naive_would_fail, (
        "no sweep width straddles the measure/paint gap any more — this test proves nothing until "
        "the fixture or the widths are re-chosen")
    print(f"test_a_reason_row_is_tall_enough_for_where_the_glyphs_actually_land OK "
          f"({naive_would_fail} of {len(widths)} widths would have dropped a line)")


def test_the_theme_block_never_squeezes_out_the_ranking():
    """The theme leads the page, and it YIELDS — the vertical twin of the ±σ/"Done it?" budget.

    MEASURED before the budget existed: the three-line summary wanted 159 of the 196 px the app's
    own minimum gives this page, so the layout handed the table a viewport 0 px TALL. A headline
    about a ranked list, with the ranked list gone. It sheds its second action, then its first,
    then itself, and whatever it sheds is on the header strip's tooltip, so the theme is demoted
    and never deleted.

    The block must also come BACK when the page grows: a fit that shows the full block only until
    the first squeeze is a ratchet, which is the failure mode widgets.WrapLabel's own docstring is
    about (measure from a cleared state, never from the last answer)."""
    p = _panel(_rows(6), MIN_PANEL)
    tb = p.theme_block
    assert tb.full_text(), "this fixture must actually produce a theme"
    assert tb.isHidden(), "the theme must yield rather than displace the ranking it is about"
    assert p.table.viewport().height() > 0 and p.table.rowCount() >= 1, (
        "the ranked list must survive the page's own minimum",
        p.table.viewport().height(), p.table.rowCount())
    assert tb.full_text() in p.summary_label.toolTip(), (
        "a shed theme must stay reachable on the header strip", p.summary_label.toolTip())

    # Roomy: the whole block, both actions, and it still leaves the table most of the page.
    p.resize(900, 800)
    for _ in range(6):
        _APP.processEvents()
    assert not p.theme_block.isHidden()
    shown = [lb for lb in p.theme_block.actions if not lb.isHidden()]
    assert len(shown) == 2, [lb.text() for lb in p.theme_block.actions]
    assert p.theme_block.height() < 800 * 0.35, p.theme_block.height()

    # ...and shrinking then re-growing gets every line back (no ratchet).
    p.resize(*MIN_PANEL)
    for _ in range(6):
        _APP.processEvents()
    assert p.theme_block.isHidden()
    p.resize(900, 800)
    for _ in range(6):
        _APP.processEvents()
    assert not p.theme_block.isHidden()
    assert len([lb for lb in p.theme_block.actions if not lb.isHidden()]) == 2
    print("test_the_theme_block_never_squeezes_out_the_ranking OK "
          f"(minimum: block hidden, table {p.table.rowCount()} rows; roomy: 2 actions back)")


def test_every_header_sits_over_its_own_column():
    """L5-08: `defaultAlignment` centres every header. At a maximized 1220 px reason column that put
    "How to find it" 611 px from the left-aligned sentence it labels. Each header must take its own
    column's alignment — numbers right with their right-aligned cells, prose left with its cells."""
    p = _panel(_rows(6), (1200, 800))
    t = p.table
    for col, want in ((_COL_CORNER, Qt.AlignLeft), (_COL_LOST, Qt.AlignRight),
                      (_PANEL_COL_REACH, Qt.AlignRight), (_PANEL_COL_REASON, Qt.AlignLeft)):
        got = int(t.horizontalHeaderItem(col).textAlignment())
        assert got & int(want), (col, got, int(want))
        assert not got & int(Qt.AlignHCenter), ("a centred header floats off its data", col, got)
    # ...and the reason header's alignment really does match its own cells' (left, not right).
    assert not int(t.item(0, _PANEL_COL_REASON).textAlignment()) & int(Qt.AlignRight)
    print("test_every_header_sits_over_its_own_column OK")


# ------------------------------------------------------------------------------- L5-09
def test_reach_cell_never_states_a_count_without_its_denominator():
    """L5-09's rule, on the column that replaced ±σ: a bare number in a table cell is unreadable,
    and this one is a COUNT, so its denominator is what the unit was. "Yes · 4/8" is checkable;
    "Yes · 4" is a number the reader cannot place, and "Yes" alone is a claim with no evidence.

    Both halves are asserted: the word (which is the glance cue that changes the instruction) and
    the `k/n` (which is the honesty rule — never a count without the sample it came out of)."""
    p = _panel(_rows(6), (900, 600))
    words = {"Yes", "Rarely", "Never"}
    for r in range(p.table.rowCount()):
        text = p.table.item(r, _PANEL_COL_REACH).text()
        word, _, count = text.partition(" · ")
        assert word in words, ("the reach cell must lead with its one-word answer", r, text)
        num, _, den = count.partition("/")
        assert num.isdigit() and den.isdigit() and int(den) > 0, (
            "the reach cell must carry the count AND the sample it came out of", r, text)
    # An UNMEASURED row (no per-lap times behind it) states nothing rather than inventing a count.
    bare = coaching.Opportunity(cid=9, direction=1, time_lost=0.07, entry_dist=800.0,
                                reason=_reason(coaching.REASON_LINE, sigma=0.24))
    p2 = _panel([bare], (900, 600))
    assert p2.table.item(0, _PANEL_COL_REACH).text() == "—", \
        p2.table.item(0, _PANEL_COL_REACH).text()
    print("test_reach_cell_never_states_a_count_without_its_denominator OK "
          f"({p.table.item(0, _PANEL_COL_REACH).text()!r}; unmeasured -> em-dash)")


# ------------------------------------------------------------------------------- L5-10
def _bp(cid=10, actual=C10_ACTUAL, optimal=C10_OPTIMAL, n_laps=38):
    """The corner's braking HABIT over the clean laps (the cross-lap medians the hint reads)."""
    return coaching.BrakeHabit(cid=cid, n_laps=n_laps, metres_later=optimal - actual,
                               optimal_brake_dist=optimal, actual_brake_dist=actual,
                               q25_m=optimal - actual - 4.0, q75_m=optimal - actual + 4.0)


def test_brake_hint_is_suppressed_when_its_target_is_inside_the_corner():
    """L5-10: the constant-decel optimum is straight-line physics, which only holds on the APPROACH.

    D24 C10: the "latest sustainable brake point" lands at 870.6 m — 59.0 m past an 811.6 m turn-in
    in a 79.6 m corner window, 19.4 m before the apex — and the cell asked to brake 50.4 m later.
    More than one brake zone (coaching.BRAKE_APPROACH_M) past turn-in, the estimate is outside its
    own domain and shows no metres."""
    assert BRAKE_HINT_MAX_PAST_TURN_IN_M == coaching.BRAKE_APPROACH_M
    past = C10_OPTIMAL - C10_ENTER
    assert past > BRAKE_HINT_MAX_PAST_TURN_IN_M, past
    assert C10_ENTER < C10_OPTIMAL < C10_APEX < C10_EXIT, "the filed geometry, restated"
    assert _brake_point_hint(_bp(), C10_ENTER) is None, "C10's 50 m hint must not be shown"

    # ...while a brake point that is still on the approach keeps its hint (the gate must not delete
    # the feature: measured on D24 it fires on 3 of the 11 ranked corners).
    near = _bp(cid=12, actual=973.7, optimal=980.7)
    assert _brake_point_hint(near, 972.4) == "Brake ~7 m later into C12 (est)"
    # ...and the pre-existing noise floor and the no-geometry call both still behave.
    assert _brake_point_hint(_bp(cid=1, actual=100.0, optimal=101.0), 95.0) is None
    assert _brake_point_hint(_bp(), None) is not None, "no turn-in supplied -> the gate is skipped"
    print(f"test_brake_hint_is_suppressed_when_its_target_is_inside_the_corner OK "
          f"(C10 optimum {past:.1f} m past turn-in > {BRAKE_HINT_MAX_PAST_TURN_IN_M:.0f} m)")


def test_reason_cell_drops_the_metres_and_names_the_target():
    """The cell-level consequence: the C10-shaped row shows its MEASURED reason sentence and no
    metres, while a sane row keeps the hint AND names its target against the corner's turn-in
    (the tooltip used to give two bare lap-odometer marks, "~871 m" / "~820 m")."""
    deep = coaching.Opportunity(cid=10, direction=-1, time_lost=0.0706, entry_dist=C10_ENTER,
                                reason=_reason(coaching.REASON_BRAKING))
    ok = coaching.Opportunity(cid=12, direction=1, time_lost=0.034, entry_dist=972.4,
                              reason=_reason(coaching.REASON_BRAKING))
    p = _panel([deep, ok], (900, 600),
               brake_points={10: _bp(), 12: _bp(cid=12, actual=973.7, optimal=980.7)})
    deep_cell = p.table.item(0, _PANEL_COL_REASON)
    ok_cell = p.table.item(1, _PANEL_COL_REASON)
    assert "Brake ~" not in deep_cell.text(), deep_cell.text()
    assert "longer on the brakes" in deep_cell.text(), deep_cell.text()
    assert "Brake ~7 m later into C12" in ok_cell.text(), ok_cell.text()
    assert "past the turn-in" in ok_cell.toolTip(), ok_cell.toolTip()
    assert " m; you brake at ~" not in ok_cell.toolTip(), (
        "the tooltip must name the target against the turn-in, not two raw odometer marks",
        ok_cell.toolTip())
    print("test_reason_cell_drops_the_metres_and_names_the_target OK")


def _run_all():
    test_narrow_panel_spends_its_width_on_the_prose()
    test_wide_panel_keeps_the_reach_column()
    test_reason_header_never_paints_clipped()
    test_every_header_carries_a_tooltip()
    test_page_fills_its_height_with_the_ranking()
    test_short_page_never_drops_below_the_shortlist()
    test_a_reason_row_is_tall_enough_for_where_the_glyphs_actually_land()
    test_the_theme_block_never_squeezes_out_the_ranking()
    test_every_header_sits_over_its_own_column()
    test_reach_cell_never_states_a_count_without_its_denominator()
    test_brake_hint_is_suppressed_when_its_target_is_inside_the_corner()
    test_reason_cell_drops_the_metres_and_names_the_target()
    test_the_dialogs_jump_buttons_are_not_clipped_at_its_own_default_size()
    print("ALL COACHING PANEL LAYOUT TESTS OK")


def test_the_dialogs_jump_buttons_are_not_clipped_at_its_own_default_size():
    """§6.4: at the size the dialog opens itself at (920x380), every amber Jump button was
    flat-cut on its right edge — the rounding sliced off into the scrollbar gutter.

    Measured on the real dialog with D24's nine opportunities: the GO column came out 89 px from
    `ResizeToContents`, the last cell's visualRect was x=791 w=88, and the button was painted at
    x=799 keeping its 88 px minimum — running to 887 against an 880 px viewport. `ResizeToContents`
    sizes a column from the cell widget's HINT and knows nothing about the inset the view then
    paints that widget inside.

    `_budget_action_column` asks the painter instead of guessing a style metric: it compares the
    widget's geometry with the cell it landed in and adds the difference. This test drives the real
    dialog at the real default and asserts no button crosses its own cell."""
    from studio.coaching_panel import OpportunitiesDialog

    opps = coaching.Opportunities(enough=True, n_laps=8, median_lap_id=3, rows=_rows(9))
    dlg = OpportunitiesDialog(opps, jump_to=lambda *a: None, brake_points={}, speed_unit="kmh")
    dlg.show()
    for _ in range(8):
        _APP.processEvents()
    try:
        t = dlg.table
        last = t.columnCount() - 1
        vp = t.viewport().width()
        worst = 0
        for r in range(t.rowCount()):
            btn = t.cellWidget(r, last)
            if btn is None:
                continue
            cell = t.visualRect(t.model().index(r, last))
            worst = max(worst, (btn.geometry().right() + 1) - (cell.right() + 1))
            assert btn.geometry().right() + 1 <= vp, (
                f"row {r}: the Jump button runs {btn.geometry().right() + 1 - vp} px past the "
                f"viewport — it is being painted into the scrollbar gutter")
        assert worst <= 0, (f"a Jump button overhangs its own cell by {worst} px", worst)
    finally:
        dlg.deleteLater()
        _APP.processEvents()
    print(f"ok jump-clip: no button crosses its cell at {dlg.width()}x{dlg.height()}")


if __name__ == "__main__":
    _run_all()
