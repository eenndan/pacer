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
    both forms now meet on the shipped dialog (3 of 11 rows on the D24 three-chapter fixture).
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
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import coaching, theme  # noqa: E402
from studio.coaching_panel import (  # noqa: E402
    _COL_CORNER,
    _COL_LOST,
    _PANEL_COL_REASON,
    _PANEL_COL_SIGMA,
    BRAKE_HINT_MAX_PAST_TURN_IN_M,
    PANEL_TOP_N,
    REASON_MIN_PX,
    OpportunitiesPanel,
    _brake_point_hint,
    _header_chrome_px,
)

theme.apply_theme(_APP)

# The panel geometry the app's OWN minimum window produces, measured on the real StudioWindow with
# the QA harness on fixture F.B: window 1047x434 -> Coaching page 280x196 -> table viewport 270 px.
MIN_PANEL = (280, 196)

# The D24 C10 geometry the finding rests on (best lap 19, single chapter). All metres are the best
# lap's own odometer, which is the frame BOTH Opportunity.entry_dist and BrakePoint carry.
C10_ENTER, C10_EXIT, C10_APEX = 811.6, 891.1, 890.0
C10_ACTUAL, C10_OPTIMAL = 820.2, 870.6


def _reason(kind=coaching.REASON_BRAKING, sigma=0.12):
    return coaching.Reason(kind=kind, contribution=0.05, apex_speed_deficit=2.4,
                           brake_extra_s=0.36, coast_extra_s=0.0, sigma=sigma)


def _rows(n: int) -> list[coaching.Opportunity]:
    """n ranked corners, descending loss, each with a genuinely long reason sentence (the wrapped
    cell is what drives the row height this whole batch is measured in)."""
    kinds = (coaching.REASON_BRAKING, coaching.REASON_APEX, coaching.REASON_LINE)
    return [coaching.Opportunity(
        cid=i + 1, direction=(1 if i % 2 else -1), time_lost=0.20 - 0.01 * i,
        entry_dist=100.0 * i, reason=_reason(kinds[i % 3], sigma=0.10 + 0.01 * i),
        phases=coaching.PhaseLoss(entry=0.05, apex=0.03, exit=0.01)) for i in range(n)]


class _Session:
    """The two accessors the panel reads, nothing else (it is a pacer-free view)."""

    def __init__(self, rows, brake_points=None):
        self._opps = coaching.Opportunities(enough=True, n_laps=8, median_lap_id=3, rows=rows)
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
def test_narrow_panel_spends_its_width_on_the_prose_not_on_sigma():
    """L5-06: at the app's own minimum the columns must fit the viewport — no horizontal scrollbar
    — and the reason column must not be starved by ±σ.

    On main: colw [64, 78, 56, 100] = 298 px inside a 270 px viewport, so BOTH scrollbars showed and
    the prose column got 100 px. ±σ is a secondary signal (the "be consistent here (σ 0.12 s)"
    reason spells it out in words), so it is the one that yields."""
    p = _panel(_rows(6), MIN_PANEL)
    t = p.table
    widths = [t.columnWidth(c) for c in range(4)]
    viewport = t.viewport().width()

    assert t.isColumnHidden(_PANEL_COL_SIGMA), (
        "±σ must yield when the reason cannot reach REASON_MIN_PX", widths, viewport)
    assert sum(widths) <= viewport, (
        "the columns must fit the viewport — a horizontal scrollbar hides the numeric columns the "
        "row is identified by", widths, viewport)
    assert not t.horizontalScrollBar().isVisible(), "no horizontal scrollbar at the app's minimum"
    assert widths[_PANEL_COL_REASON] > 100, (
        "the prose column must beat the header-size-hint fallback it used to sit at", widths)
    assert widths[_PANEL_COL_REASON] == viewport - widths[_COL_CORNER] - widths[_COL_LOST], (
        "the reason takes every pixel ±σ freed", widths, viewport)
    print(f"test_narrow_panel_spends_its_width_on_the_prose_not_on_sigma OK "
          f"(reason {widths[_PANEL_COL_REASON]}px in a {viewport}px viewport, ±σ dropped)")


def test_wide_panel_keeps_sigma():
    """The ±σ drop is a BUDGET, not a deletion: once the reason clears REASON_MIN_PX the column is
    back. (This is the guard against 'fixing' L5-06 by simply removing a column.)"""
    p = _panel(_rows(6), (900, 600))
    t = p.table
    assert not t.isColumnHidden(_PANEL_COL_SIGMA), "±σ must return when there is room for it"
    assert t.columnWidth(_PANEL_COL_REASON) >= REASON_MIN_PX, t.columnWidth(_PANEL_COL_REASON)
    print(f"test_wide_panel_keeps_sigma OK (reason {t.columnWidth(_PANEL_COL_REASON)}px, ±σ shown)")


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


def test_every_header_sits_over_its_own_column():
    """L5-08: `defaultAlignment` centres every header. At a maximized 1220 px reason column that put
    "How to find it" 611 px from the left-aligned sentence it labels. Each header must take its own
    column's alignment — numbers right with their right-aligned cells, prose left with its cells."""
    p = _panel(_rows(6), (1200, 800))
    t = p.table
    for col, want in ((_COL_CORNER, Qt.AlignLeft), (_COL_LOST, Qt.AlignRight),
                      (_PANEL_COL_SIGMA, Qt.AlignRight), (_PANEL_COL_REASON, Qt.AlignLeft)):
        got = int(t.horizontalHeaderItem(col).textAlignment())
        assert got & int(want), (col, got, int(want))
        assert not got & int(Qt.AlignHCenter), ("a centred header floats off its data", col, got)
    # ...and the reason header's alignment really does match its own cells' (left, not right).
    assert not int(t.item(0, _PANEL_COL_REASON).textAlignment()) & int(Qt.AlignRight)
    print("test_every_header_sits_over_its_own_column OK")


# ------------------------------------------------------------------------------- L5-09
def test_sigma_cell_states_its_unit():
    """L5-09: "±0.12" is seconds and must say so — the reason sentence in the SAME row renders the
    identical statistic as "σ 0.12 s", and the Time lost cell beside it already prints "+0.13 s"."""
    p = _panel(_rows(6), (900, 600))
    for r in range(p.table.rowCount()):
        text = p.table.item(r, _PANEL_COL_SIGMA).text()
        assert text.endswith(" s"), ("the ±σ cell must carry its unit", r, text)
    # The state where both forms meet on one row: a REASON_LINE row spells σ out in the sentence.
    line = coaching.Opportunity(cid=9, direction=1, time_lost=0.07, entry_dist=800.0,
                                reason=_reason(coaching.REASON_LINE, sigma=0.24))
    p2 = _panel([line] + _rows(3), (900, 600))
    cell, sentence = p2.table.item(0, _PANEL_COL_SIGMA).text(), p2.table.item(0, 3).text()
    assert "σ 0.24 s" in sentence, sentence
    assert cell == "±0.24 s", (cell, sentence)
    print(f"test_sigma_cell_states_its_unit OK ({cell!r} beside {sentence!r})")


# ------------------------------------------------------------------------------- L5-10
def _bp(cid=10, actual=C10_ACTUAL, optimal=C10_OPTIMAL):
    return SimpleNamespace(cid=cid, actual_brake_dist=actual, optimal_brake_dist=optimal,
                           metres_later=optimal - actual, a_max_g=0.77, peak_decel_g=0.8)


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
    test_narrow_panel_spends_its_width_on_the_prose_not_on_sigma()
    test_wide_panel_keeps_sigma()
    test_reason_header_never_paints_clipped()
    test_every_header_carries_a_tooltip()
    test_page_fills_its_height_with_the_ranking()
    test_short_page_never_drops_below_the_shortlist()
    test_every_header_sits_over_its_own_column()
    test_sigma_cell_states_its_unit()
    test_brake_hint_is_suppressed_when_its_target_is_inside_the_corner()
    test_reason_cell_drops_the_metres_and_names_the_target()
    print("ALL COACHING PANEL LAYOUT TESTS OK")


if __name__ == "__main__":
    _run_all()
