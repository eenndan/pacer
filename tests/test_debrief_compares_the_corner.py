"""The debrief's ranked rows offer the docked Coaching row's Compare (cool/debrief-compares-the-corner).

WHY. "Show me that corner: my best pass against a typical pass, on video and on the traces" was
three actions from the debrief (Esc, the Coaching tab, Compare C1) while the docked row had it in
one, and the debrief's own Jump shows the best lap against itself: every Δ a dash. And Esc dropped
the driver on Laps, away from the page he had been reading. RUL-7 keeps LAP-first buttons off the
debrief; a row's Compare is CORNER-first — it shows the loss that row names, at that corner — and is
allowed (coordinator ruling, wave 27, to be recorded in the 0.30.0 DECISIONS section).

Pinned on the real window at 1280x800 over the three-habit demo circuit (three ranked rows):
  1. every ranked row offers Compare beside its Jump, enabled, the plain style; only the first
     row's Jump is the amber primary; the page grows no scrollbar and cuts no row;
  2. the keyboard reaches it in reading order (Jump, then its Compare) and Space presses it
     (#739's rule);
  3. its click lands exactly what the docked row's Compare lands (the same signal, the same
     `compare_at_corner`), and the row's reason stays on screen: the lap panel stays on Coaching;
  4. Esc from a debrief with a ranked corner lands on Coaching too, the remembered tab untouched.

Run: python tests/test_debrief_compares_the_corner.py
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402
from test_debrief_landing import _fresh_app_support, _open, _settle, _two_recordings  # noqa: E402

_APP = themed_app()
_COACHING_TAB = 3


def _window():
    from studio.app import StudioWindow
    win = StudioWindow([])
    win.resize(1280, 800)
    win.show()
    return win


def _close(win) -> None:
    win.close()
    win.deleteLater()
    _APP.processEvents()


def test_each_ranked_row_offers_compare_beside_jump_and_it_fits():
    from PySide6.QtWidgets import QAbstractScrollArea

    from studio import coaching_panel as cp
    with tempfile.TemporaryDirectory(prefix="debrief_cmp_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = _window()
        try:
            _open(win, a)
            v = win.view
            page, t = v.opportunities, v.opportunities.table
            assert v.is_debrief() and t.rowCount() == len(page.shortlist_cids()) >= 2, (
                v.is_debrief(), t.rowCount())
            assert not t.isColumnHidden(cp._PANEL_COL_CMP), "the debrief shows no Compare"
            for r, opp in enumerate(page._all_rows[:t.rowCount()]):
                cell, cmp_ = t.cellWidget(r, cp._PANEL_COL_GO), t.cellWidget(r, cp._PANEL_COL_CMP)
                assert opp.evidence.ranked, opp.cid
                assert cmp_.isVisible() and cmp_.isEnabled() and cmp_.text() == "Compare", r
                assert cmp_.accessibleName() == f"Compare at C{opp.cid}"
                assert cmp_.property("variant") is None, "Compare is the plain button"
                assert (cell.jump.property("variant") == "primary") == (r == 0), (
                    "only the first ranked row's Jump is the primary", r)
                jx = cell.jump.mapTo(win, cell.jump.rect().topRight()).x()
                assert cmp_.mapTo(win, cmp_.rect().topLeft()).x() > jx, "Compare after Jump"
            # The layout gains no scrollbar and cuts no row at 1280x800.
            bars = [type(sa).__name__ for sa in win.findChildren(QAbstractScrollArea)
                    if sa.isVisible() and (sa.verticalScrollBar().isVisible()
                                           or sa.horizontalScrollBar().isVisible())]
            assert not bars, bars
            rows = sum(t.rowHeight(r) for r in range(t.rowCount()))
            assert rows <= t.viewport().height(), (rows, t.viewport().height())
            bottom = t.mapTo(win, t.rect().bottomLeft()).y()
            assert bottom <= win.statusBar().mapTo(win, win.statusBar().rect().topLeft()).y()
        finally:
            _close(win)
    print(f"ok: {t.rowCount()} debrief rows, each Jump + Compare, no scrollbar at 1280x800")


def test_the_keyboard_reaches_compare_after_jump_and_space_presses_it():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from studio import coaching_panel as cp
    with tempfile.TemporaryDirectory(prefix="debrief_cmp_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = _window()
        try:
            _open(win, a)
            v = win.view
            page, t = v.opportunities, v.opportunities.table
            jump = t.cellWidget(0, cp._PANEL_COL_GO).jump
            cmp_ = t.cellWidget(0, cp._PANEL_COL_CMP)
            got = []
            page.compare_requested.connect(lambda tp, lost: got.append((tp.cid, lost)))
            win.activateWindow()
            jump.setFocus(Qt.MouseFocusReason)
            _settle(0.1)
            # One real Tab press from the row's Jump: the window's own Tab road (KeyboardPress).
            QTest.keyClick(_APP.focusWidget() or win, Qt.Key_Tab)
            _settle(0.1)
            assert _APP.focusWidget() is cmp_, ("Tab from Jump did not reach its Compare",
                                                _APP.focusWidget())
            QTest.keyClick(cmp_, Qt.Key_Space)
            _settle(0.3)
            assert got and got[0][0] == page.shortlist_cids()[0], got
        finally:
            _close(win)
    print("ok: Tab from the row's Jump reaches its Compare; Space presses it")


def test_compare_lands_the_docked_compare_and_keeps_the_reason_on_screen():
    from studio import coaching
    from studio import coaching_panel as cp
    with tempfile.TemporaryDirectory(prefix="debrief_cmp_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = _window()
        try:
            _open(win, a)
            v, s = win.view, win.session
            page, t = v.opportunities, v.opportunities.table
            assert v.is_debrief()
            top = page._all_rows[0]
            reason = t.item(0, cp._PANEL_COL_REASON).text()
            tp = s.typical_passes()[top.cid]
            # The docked row's Compare is the same signal (into `compare_at_corner`): no second road.
            landed, notices = [], []
            page.compare_requested.connect(lambda tp_, lost: landed.append((tp_, lost)))
            v.statusNotice.connect(notices.append)
            t.cellWidget(0, cp._PANEL_COL_CMP).click()
            _settle(0.3)
            assert landed == [(tp, coaching.lost_text(top))], landed
            c = v.compare
            assert c.active and (c.lap_a, c.lap_b) == (tp.lap_id, tp.best_lap_id), (c.lap_a, c.lap_b)
            assert notices and notices[-1].startswith(cp.compare_status(tp, coaching.lost_text(top))
                                                      ), notices
            assert not v.is_debrief() and v._maximized_panel is None, "the grid is back"
            # WHY HE IS THERE stays on screen: the docked Coaching page, its row and its reason.
            assert v.tab_bar.currentIndex() == _COACHING_TAB, v.tab_bar.currentIndex()
            assert t.isVisible() and t.item(0, cp._PANEL_COL_REASON).text() == reason
            assert not t.visualItemRect(t.item(0, cp._PANEL_COL_REASON)).isEmpty()
        finally:
            _close(win)
    print(f"ok: the debrief's C{top.cid} Compare lands lap {tp.lap_id} vs lap {tp.best_lap_id}, "
          f"the reason {reason!r} on screen")


def test_esc_from_a_ranked_debrief_lands_on_coaching():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    from studio import prefs
    with tempfile.TemporaryDirectory(prefix="debrief_cmp_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        prefs.set_lap_panel_tab(1)
        win = _window()
        try:
            _open(win, a)
            v = win.view
            assert v.is_debrief() and v.opportunities.shortlist_cids()
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.2)
            assert not v.is_debrief() and v._maximized_panel is None
            assert v.tab_bar.currentIndex() == _COACHING_TAB, v.tab_bar.currentIndex()
            assert prefs.lap_panel_tab() == 1, "the landing's Esc overwrote the remembered tab"
        finally:
            _close(win)
    print("ok: Esc from a ranked debrief lands on Coaching; the remembered tab stays Corners")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} DEBRIEF-COMPARE TESTS PASSED")
