"""The plan stays in reach after the debrief (QA round 3, 2026-09-26: JOURNEY-1, JOURNEY-5).

WHY. The first-open debrief is the one screen that holds the plan: the PB, "Compare with your
previous PB", the focus list and the top corners. Measured on the real window at the owner's layout
(1440x831, his grid), one tab click later all of it was out of reach:
  * the Coaching page is 719x222 there, its top three rows are reserved first, and the focus
    block's budget is 0, so the list (and the "Both dry?" the next check waits on) was hidden;
  * Coaching ▸ Show focus list only switched to that same tab, so the list stayed hidden;
  * "Compare with your previous PB" lived on the debrief and on a PB card that leaves after 6 s,
    and what was left was three steps: Load reference…, find 30 Aug's folder, Compare vs reference;
  * and that detour re-pointed File ▸ Open at 30 Aug's folder (JOURNEY-5).

Pinned here:
  1. a list the page has no room for keeps ONE line — its corners, a pending "Both dry?" and a
     click that shows it whole — which never takes the first row, never appears for the empty
     invitation, and leaves the budget contract of tests/test_coaching_panel_layout.py alone;
  2. Show focus list, and the line's click, show the list whole at his layout (the page maximized);
  3. Coaching ▸ Compare with your previous PB, enabled while this session's PB knows the row it
     beat, runs the same load as the debrief's button;
  4. a reference pick leaves File ▸ Open's folder where it was.

Run: python tests/test_plan_in_reach.py   (~20 s)
"""
import os
import sys
import tempfile

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _qtapp import themed_app  # noqa: E402

_APP = themed_app()

from test_coaching_panel_layout import _panel, _rows, _whole_rows  # noqa: E402
from test_debrief_landing import _fresh_app_support, _open, _settle, _two_recordings  # noqa: E402

from studio import focus, session_record, theme  # noqa: E402

# The owner's window and grid (prefs.json, 09-26), and the Coaching page they give him.
HIS_WINDOW = (1440, 831)
HIS_GRID = [[755, 749], [610, 304], [349, 565]]
HIS_PAGE = (719, 222)


def _pending_report() -> focus.Report:
    """30 Aug's three corners re-measured on 19 Sep, with no session record on either day: every
    verdict waits on "Both dry?" (the loop as the owner's next Sandown session meets it)."""
    items = [focus.FocusItem(cid=c, direction=1, enter_frac=0.1 * c, exit_frac=0.1 * c + 0.05,
                             median_s=4.0, iqr_s=0.1, n_laps=36, time_lost=0.2, reason="",
                             reach="", fingerprint="GX0065", date="2026-08-30", lap_total=737.0,
                             verified=True, degraded=False, n_of=36) for c in (7, 5, 3)]
    now = {"list_track": "Sandown Park", "track": "Sandown Park", "fingerprint": "GX0068",
           "date": "2026-09-19", "lap_total": 737.0, "verified": True, "degraded": False}
    sample = focus.CornerSample(median=4.0, iqr=0.1, n_laps=36, n_of=36)
    return focus.verdict(items, now, [sample] * 3, session_record.empty_store())


def _pump(n=6):
    for _ in range(n):
        _APP.processEvents()


# ------------------------------------------------------------------------ 1. the one line
def test_a_list_the_page_has_no_room_for_keeps_one_line():
    report = _pending_report()
    assert focus.mark_dry_prompt(report)[0] == "Both dry?", focus.mark_dry_prompt(report)
    p = _panel(_rows(6), HIS_PAGE)
    alone = _whole_rows(p.table)
    p.set_focus_report(report)
    _pump()
    assert p.focus_block.isHidden(), (
        "the fixture must reach the owner's case: a page with no room for the block", p.size())
    line = getattr(p, "focus_line", None)
    assert line is not None and line.isVisible(), (
        "a focus list the page has no room for vanished: nothing on the page names it")
    assert line.text() == "Focus list: C7 · C5 · C3 — Both dry?", line.text()
    # One control tall, and never at the first row's expense.
    m = p._focus_line_row.layout().contentsMargins()
    assert p._focus_line_row.height() <= theme.CTRL_H + m.top() + m.bottom(), \
        p._focus_line_row.height()
    whole = _whole_rows(p.table)
    assert whole >= min(alone, 1), (alone, whole)
    fired = []
    p.focus_show_requested.connect(lambda: fired.append(1))
    line.click()
    assert fired == [1], "the line's click must ask for the list, whole"
    # It settles: nothing flips between two further passes.
    before = (line.isVisible(), p.focus_block.isVisible(), p.table.viewport().height())
    _pump(12)
    assert (line.isVisible(), p.focus_block.isVisible(), p.table.viewport().height()) == before
    assert p.focus_block.full_text() in p.summary_label.toolTip(), "the block is still one hover away"

    # Where the block has room, the block and not the line.
    p.resize(900, 800)
    _pump()
    assert not p.focus_block.isHidden() and not line.isVisible(), (p.focus_block.isHidden(),
                                                                   line.isVisible())
    # The empty list's invitation and a dormant page get no line: there is no list to name.
    for empty in (focus.Report(track="Sandown Park"), None):
        q = _panel(_rows(6), HIS_PAGE)
        q.set_focus_report(empty)
        _pump()
        assert not q.focus_line.isVisible(), (empty, q.focus_line.text())
    print(f"ok one line: {line.text()!r} at {HIS_PAGE}, {whole} of {alone} whole rows kept")


# ------------------------------------------------------------------------ the real window
def _window(recording):
    from studio import prefs
    from studio.app import StudioWindow

    prefs.set_grid_sizes(HIS_GRID)
    win = StudioWindow([])
    win.resize(*HIS_WINDOW)
    win.show()
    _open(win, recording)
    return win


def _close(win):
    win.close()
    win.deleteLater()
    _settle(0.1)


def _click_tab(view, i):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    tabs = view.tab_bar
    QTest.mouseClick(tabs, Qt.LeftButton, Qt.NoModifier, tabs.tabRect(i).center())
    _settle(0.3)


def test_show_focus_list_shows_the_list_at_his_layout():
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    with tempfile.TemporaryDirectory(prefix="reach_") as folder, _fresh_app_support():
        a, _b = _two_recordings(folder)
        win = _window(a)
        try:
            view, panel = win.view, win.view.opportunities
            block = panel.focus_block
            assert view.is_debrief() and not block.isHidden(), "the debrief shows the list"
            _click_tab(view, 0)            # one tab click ends the debrief
            _click_tab(view, 3)
            assert not view.is_debrief() and view._maximized_panel is None
            assert block.isHidden(), ("the fixture must reach the owner's case: no room for the "
                                      "block in his grid", panel.size())
            win._focus_show_action.trigger()
            _settle(0.3)
            assert view.tab_bar.currentIndex() == 3 and not block.isHidden(), (
                "Coaching ▸ Show focus list left the list hidden", panel.size())
            assert view._maximized_panel is view._table_panel
            assert all(not lb.isHidden() for lb in block.lines if lb.text()), block.full_text()
            win._focus_show_action.trigger()   # "show" twice still shows
            _settle(0.2)
            assert view._maximized_panel is view._table_panel and not block.isHidden()
            QTest.keyClick(win, Qt.Key_Escape)
            _settle(0.3)
            assert view._maximized_panel is None and block.isHidden()
            line = panel.focus_line
            assert line.isVisible() and line.text().startswith("Focus list: C"), line.text()
            line.click()
            _settle(0.3)
            assert view._maximized_panel is view._table_panel and not block.isHidden()
            assert not line.isVisible(), "the line and the block are never both on the page"
        finally:
            _close(win)
    print("ok Show focus list: the page maximized with the list whole; the line's click too")


def test_the_pb_compare_has_a_menu_door_while_a_previous_pb_is_known():
    with tempfile.TemporaryDirectory(prefix="reach_") as folder, _fresh_app_support():
        a, b = _two_recordings(folder)
        win = _window(a)
        try:
            act = getattr(win, "_pb_compare_action", None)
            assert act is not None, "no Coaching ▸ Compare with your previous PB"
            assert act.parent() is win._ref_action.parent(), "not in the Coaching menu"
            win._sync_coaching_menu()
            assert win.library_ctl.previous_pb is None     # a first session beat nothing
            assert not act.isEnabled() and "no new personal best" in act.text(), act.text()
            win.library_ctl.previous_pb = {"fingerprint": "GX9002", "track": win.session.track_name,
                                           "best": 60.0, "paths": [b]}
            win._sync_coaching_menu()
            assert act.isEnabled() and act.text() == "Compare with your previous PB", act.text()
            started = []
            win._start_reference_load = lambda paths: started.append(list(paths))
            act.trigger()
            assert started == [[b]], ("not the debrief button's load of the row it beat", started)
        finally:
            _close(win)
    print("ok PB compare: a Coaching menu door, gated on the row this session's PB beat")


def test_a_reference_pick_leaves_file_open_where_it_was():
    from PySide6.QtWidgets import QFileDialog

    from studio import prefs

    with tempfile.TemporaryDirectory(prefix="reach_") as folder, _fresh_app_support():
        a, b = _two_recordings(folder)
        win = _window(a)
        today = os.path.dirname(a)
        prefs.set_last_dir(today)
        picker = QFileDialog.getOpenFileName
        asked = []
        QFileDialog.getOpenFileName = staticmethod(
            lambda *args, **kw: asked.append(args[2]) or (b, ""))
        try:
            started = []
            win._start_reference_load = lambda paths: started.append(list(paths))
            win._ref_action.trigger()
            assert started and asked == [today], (started, asked)
            assert prefs.last_dir() == today, (
                "a reference pick re-pointed File ▸ Open at the reference's folder",
                prefs.last_dir())
        finally:
            QFileDialog.getOpenFileName = picker
            _close(win)
    print("ok JOURNEY-5: File ▸ Open still starts among today's footage after a reference pick")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} PLAN-IN-REACH TESTS PASSED")
