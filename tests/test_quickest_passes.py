"""Corners ▸ Quickest passes: the quickest pass through each corner, the laps level with it, and the
click that plays it.

The request: "show fastest time per corner and on which lap it happened … quickly jump to that
moment in footage". On the demo the quickest pass is a tie at six corners of seven
(refused-2026-09 §32), so the view names the quickest lap and how many are level with it, and a
second click on the same row plays the next level lap. These tests pin:

  * `stats.quickest_passes` (Qt-free, synthetic grids): ties, a lone quickest, the estimated-clock
    step, laps not counted, too few laps — and that its time IS Stats ▸ CORNERS Best;
  * the view on the real window over the demo (PACER_NO_MEDIA): the two-button switch, the
    remembered view, the tab title, the rows, a row's activation reaching the play path with the
    right lap / corner / clip, again → the next level lap, wrapping, the edge states, ★ withheld on
    provisional timing, ⌘K's per-corner rows;
  * the player's one-shot clip end: it pauses there, and any seek / play / pause drops it;
  * each row's strip of every counted pass (`stats.pass_strip`): the row's own cells, the level
    band at its true width, the typical, the ▼ off the scale, the ring on the lap playing, the
    room it may take beside the words, and no repaint on a playback tick.
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import prefs, stats, theme  # noqa: E402
from studio.library import PB_PRECISION_S  # noqa: E402

theme.apply_theme(_APP)

# ------------------------------------------------------------------ the Qt-free function


def _grid(cols, resolved=None, ids=None):
    """A CornerMatrix from per-lap rows of corner times (`cols[lap][corner]`)."""
    ids = list(range(len(cols))) if ids is None else ids
    res = resolved or [[True] * len(cols[0]) for _ in cols]
    return stats.corner_matrix(ids, list(range(1, len(cols[0]) + 1)), cols, res)


def test_a_tie_names_the_quickest_and_lists_the_level_laps_quickest_first():
    rows = [[3.050, 2.0], [3.000, 2.5], [3.069, 2.6], [3.071, 2.7], [3.020, 2.8], [3.2, 2.9],
            [3.070, 3.0]]
    qp = stats.quickest_passes(_grid(rows))
    c1, c2 = qp.passes
    assert (c1.time_s, c1.lap_id) == (3.000, 1)
    # 3.020 (+0.020), 3.050 (+0.050), 3.069 (+0.069) are level; 3.070 (EXACTLY the step) and
    # 3.071 are not — the millisecond boundary the session moments use (see the boundary test)
    assert c1.level_laps == (4, 0, 2), c1
    assert c1.next_gap_s == 0.02 and not c1.alone
    assert c1.play_order() == (1, 4, 0, 2)
    # C2: lap 0 is 0.5 s clear — a lone quickest
    assert c2.lap_id == 0 and c2.level_laps == () and c2.alone and c2.next_gap_s == 0.5
    assert qp.level_s == PB_PRECISION_S and qp.not_counted == ()


def test_the_estimated_clock_widens_the_level_step_to_the_lap_races():
    rows = [[3.00], [3.10], [3.149], [3.151], [3.4]]
    plain = stats.quickest_passes(_grid(rows)).passes[0]
    est = stats.quickest_passes(_grid(rows), clock_estimated=True)
    assert plain.level_laps == () and plain.alone
    assert est.level_s == 0.15 and est.passes[0].level_laps == (1, 2), est


def test_laps_not_counted_are_never_named_and_are_reported():
    rows = [[2.0, 3.0], [2.1, 3.1], [2.2, 3.2], [2.3, 3.3], [2.4, 3.4]]
    res = [[False, True], [True, True], [True, True], [True, True], [True, True]]
    # lap 0's C1 is the raw minimum but not matched on track: lap 1 is the quickest counted pass
    qp = stats.quickest_passes(_grid(rows, res, ids=[0, 1, 2, 3, 5]), lap_ids=[0, 1, 2, 3, 4, 5])
    assert qp.passes[0].lap_id == 1 and qp.passes[0].n_counted == 4
    assert qp.passes[1].lap_id == 0
    # lap 0 (an unmatched corner) and lap 4 (not a grid row: ⊘ / ⚠) are not counted
    assert qp.not_counted == (0, 4), qp.not_counted


def test_a_corner_with_no_counted_pass_names_nothing():
    rows = [[2.0, 3.0]] * 5
    res = [[False, True]] * 5
    qp = stats.quickest_passes(_grid(rows, res))
    assert qp.passes[0].lap_id is None and qp.passes[0].play_order() == ()
    assert not qp.passes[0].alone


def test_too_few_laps_is_no_answer():
    assert _grid([[2.0]] * (stats.MATRIX_MIN_LAPS - 1)) is None
    assert stats.quickest_passes(None) is None


def test_the_quickest_is_stats_corners_best_for_every_corner():
    """One quantity, one source: the view's time is the CORNERS table's Best, every corner."""
    s = _demo_session()
    best = {r.cid: r.best_s for r in s.corner_report()}
    qp = s.quickest_passes()
    assert len(qp.passes) == len(best) == 7
    for p in qp.passes:
        assert p.time_s == best[p.cid], (p, best[p.cid])

# ------------------------------------------------------------------ the demo, real window

_DEMO: dict = {}


def _demo_path() -> str:
    if "path" not in _DEMO:
        from studio.dev import make_demo as md
        _DEMO["dir"] = tempfile.TemporaryDirectory(prefix="quickest_demo_")
        _DEMO["path"] = os.path.join(_DEMO["dir"].name, "GX019000.MP4")
        _DEMO["truth"] = md.make(_DEMO["path"], render=False)
    return _DEMO["path"]


def _demo_session():
    if "session" not in _DEMO:
        from studio.session import Session
        _DEMO["session"] = Session.load([_demo_path()])
    return _DEMO["session"]


def _settle(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


def _window(path=None):
    from studio.app import StudioWindow
    for k in ("critical", "warning", "information"):
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: QMessageBox.Ok))
    win = StudioWindow([path or _demo_path()])
    deadline = time.time() + 60
    while win.view is None and time.time() < deadline:
        _settle(0.02)
    assert win.view is not None, "the demo did not load"
    win.resize(1440, 900)
    win.show()
    _settle(0.2)
    QTest.keyClick(win, Qt.Key_Escape)   # leave the debrief landing
    _settle(0.1)
    return win


def _close(win):
    win.close()
    win.deleteLater()
    _APP.processEvents()


def _record_plays(view):
    """Replace the player's clip call with a recorder: (start, end) per play. The pane still TAKES
    the pass (`set_clip`, what play_clip ends with), so the pass is the player's subject as it is
    after a real play — the inert player just never moves."""
    plays = []

    def record(start, end):
        plays.append((start, end))
        view.video.pane.set_clip(start, end)
    view.video.play_clip = record
    return plays


def test_the_switch_the_rows_and_the_tab_title():
    prefs.set_corners_view("lap")
    win = _window()
    try:
        view = win.view
        page = view.corners_page
        view.select_lap_tab(1)
        _settle()
        assert page.bar.isVisible() and page.view() == "lap"
        assert view.tab_bar.tabText(1).startswith("Corners · L")
        # the left chip names a VIEW too: "Lap 13 corners", the lap the tab title names
        assert page.lap_btn.text() == view.tab_bar.tabText(1).replace("Corners · L", "Lap ") + \
            " corners"
        page.quickest_btn.click()
        _settle()
        assert page.view() == "quickest" and prefs.corners_view() == "quickest"
        assert view.tab_bar.tabText(1) == "Corners · quickest"
        assert page.stack.currentWidget() is page.quickest
        assert page.level_label.text() == "Level = within 0.07 s"
        t = page.quickest.table
        got = [[t.item(r, c).text() for c in range(4)] for r in range(t.rowCount())]
        assert got == [
            ["C1", "3.00", "7", "5 more laps"],
            ["C2", "2.62", "11", "10 more laps"],
            ["C3", "2.32", "13", "5 more laps"],
            ["C4", "3.02", "8", "7 more laps"],
            ["C5", "3.52 ★", "10", "none · next +0.071"],
            ["C6", "2.80", "13", "10 more laps"],
            ["C7", "3.42", "2", "5 more laps"],
        ], got
        # nothing overflows: no row below the fold (the overlaid bar would show) and no column cut
        assert t.verticalScrollBar().maximum() == 0 and not page.quickest.rows_bar.isVisible()
        assert not t.horizontalScrollBar().isVisible()
        assert "laps 9, 8, 12, 10, 13" in t.item(0, 3).toolTip()
        assert "fastest" not in " ".join(t.item(r, c).toolTip().lower() + t.item(r, c).text()
                                         for r in range(7) for c in range(4))
        page.lap_btn.click()
        _settle()
        assert prefs.corners_view() == "lap" and page.stack.currentWidget() is page.lap_view
    finally:
        _close(win)


def test_the_view_is_remembered_across_a_new_window():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        page = win.view.corners_page
        assert page.view() == "quickest" and page.quickest_btn.isChecked()
        assert page.quickest.table.rowCount() == 7
        assert win.view.tab_bar.tabText(1) == "Corners · quickest"
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_row_plays_its_pass_then_the_next_level_lap_and_wraps():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        q = view.corners_page.quickest
        plays = _record_plays(view)
        s = win.session
        view.select_lap_tab(1)
        _settle()
        # a click on C3's row: lap 13 (id 12), its clip, the map ring, the lap selected
        q.table.cellClicked.emit(2, 1)
        _settle()
        assert plays[-1] == s.corners.pass_clip(12, 3), plays
        start, end = plays[-1]
        entry = s.corners.corner_entry_media_time(12, 3)
        leave = s.corners.corner_edge_media_time(12, 3, "exit")
        assert abs((entry - start) - 2.0) < 1e-9 and abs((end - leave) - 1.0) < 1e-9
        assert view.table.selected_lap_ids() == [12]
        assert view.map.pinned_corner() == "C3"
        assert win.statusBar().currentMessage().startswith(
            "Playing lap 13 (2.32) through C3 — 5 more laps are level here: click C3 again")
        # again → the next level lap (7, id 6), named in the row and on the status bar
        q.table.cellClicked.emit(2, 3)
        _settle()
        assert plays[-1] == s.corners.pass_clip(6, 3)
        # the lap playing shows its OWN pass, not the quickest's 2.32 beside it (review fix 2)
        assert q.table.item(2, 3).text() == "playing lap 7 (2.35) · 2 of 6"
        assert q.table.item(2, 2).text() == "13", "the Lap cell stays the quickest's lap"
        assert win.statusBar().currentMessage().startswith(
            "Playing lap 7 (2.35) through C3, level pass 2 of 6"), win.statusBar().currentMessage()
        # Return on the keyboard plays too, and the order wraps after the sixth
        q.table.setFocus()
        for _ in range(4):
            QTest.keyClick(q.table, Qt.Key_Return)
        assert "click C3 again to start over" in win.statusBar().currentMessage()
        QTest.keyClick(q.table, Qt.Key_Return)
        assert plays[-1] == s.corners.pass_clip(12, 3) and len(plays) == 7
        # ↓ moves to C4 and Return plays ITS quickest (lap 8), not the next of C3's
        QTest.keyClick(q.table, Qt.Key_Down)
        QTest.keyClick(q.table, Qt.Key_Return)
        assert plays[-1] == s.corners.pass_clip(7, 4)
        # C2's previous corner ends inside the 2 s lead-in: the clip starts at C1's exit
        q.activate(1)
        c1_exit = s.corners.corner_edge_media_time(10, 1, "exit")
        assert plays[-1][0] == c1_exit, (plays[-1], c1_exit)
        # the ▶ button is the same path
        q.table.cellWidget(4, 4).click()
        assert plays[-1] == s.corners.pass_clip(9, 5)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_level_boundary_is_the_session_moments_own():
    """Exactly at the step is NOT level, 1 ms inside is, 1 ms outside is not — on both steps, and
    it is the same function `story` names its ties by (one rule, review fix 9)."""
    from studio import story
    assert story._level is stats.is_level
    for step, est in ((PB_PRECISION_S, False), (0.15, True)):
        ms = round(step * 1000)
        rows = [[3.000], [3.000 + (ms - 1) / 1000], [3.000 + ms / 1000], [3.000 + (ms + 1) / 1000],
                [3.5]]
        qp = stats.quickest_passes(_grid(rows), clock_estimated=est).passes[0]
        assert qp.level_laps == (1,), (step, qp)
        assert qp.level_times == (3.000 + (ms - 1) / 1000,)
        for gap, want in (((ms - 1) / 1000, True), (ms / 1000, False), ((ms + 1) / 1000, False)):
            assert stats.is_level(gap, step) is want, (step, gap)


def test_a_row_leaves_compare_first():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        plays = _record_plays(view)
        view.video.compare_btn.click()
        _settle()
        assert view._comparing()
        # C3's quickest is lap 13, one of the compared pair. Measured: a one-lap pick of either
        # lap still leaves compare through `_pick_leaves_compare` (only the pair's own two laps, or
        # a cross pair's lap A, keep it), so the row's own exit is what makes the click say ONE
        # thing: the pass it plays, never a "compare ended — showing lap 13" line flashed first.
        pair = (view.compare.lap_a, view.compare.lap_b)
        assert 12 in pair, pair
        said = []
        view.statusNotice.connect(said.append)
        view.corners_page.quickest.activate(2)
        _settle()
        assert not view._comparing() and plays == [win.session.corners.pass_clip(12, 3)]
        assert len(said) == 1 and said[0].startswith("Playing lap 13 (2.32) through C3"), said
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_row_ends_a_lap_race():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        plays = _record_plays(view)
        assert view.start_lap_race(), "the demo could not start a Lap race"
        _settle()
        assert view.is_racing()
        view.corners_page.quickest.activate(4)
        _settle()
        assert not view.is_racing() and plays == [win.session.corners.pass_clip(9, 5)]
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_cell_says_playing_only_while_the_clip_runs():
    """Review fix 5: "playing lap 7 (2.35) · 2 of 6" while the clip runs; "lap 7 (2.35) · 2 of 6"
    once it has ended (the auto-pause) or been left; the count again once another lap is picked."""
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        q = view.corners_page.quickest
        plays = _record_plays(view)
        q.activate(2)
        q.activate(2)
        assert len(plays) == 2
        assert q.table.item(2, 3).text() == "playing lap 7 (2.35) · 2 of 6"
        view.video.clipEnded.emit()          # the player paused at the clip end / was left
        _settle()
        assert q.table.item(2, 3).text() == "lap 7 (2.35) · 2 of 6"
        view.table.laps_selected.emit([3])   # the driver picks lap 4 in the Laps table
        _settle()
        assert q.table.item(2, 3).text() == "5 more laps"
        q.activate(2)                        # ...and C3 starts from its quickest again
        assert plays[-1] == win.session.corners.pass_clip(12, 3)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_real_pane_reports_the_clip_ending_once():
    pane = _pane()
    ended = []
    pane.clipEnded.connect(lambda: ended.append(True))
    pane.seek(10.0)
    pane.play()
    pane.set_clip_end(15.0)
    pane._on_position(15_000)
    assert ended == [True]
    pane.seek(3.0)                       # no clip left to end
    assert ended == [True]
    pane.set_clip_end(20.0)
    pane.seek(4.0)                       # a scrub ends this one
    assert ended == [True, True]


def test_showing_the_quickest_view_hands_it_the_keys():
    """Review fix 8: the switch, key 2 onto the remembered view, and the command all leave the
    table focused with a current row, so the arrow keys and Return work at once."""
    prefs.set_corners_view("lap")
    win = _window()
    try:
        view = win.view
        page = view.corners_page
        plays = _record_plays(view)
        view.select_lap_tab(1)
        _settle()
        page.quickest_btn.click()
        _settle()
        t = page.quickest.table
        assert t.hasFocus() and t.currentRow() == 0, (t.hasFocus(), t.currentRow())
        assert plays == [], "showing the view plays nothing"
        QTest.keyClick(t, Qt.Key_Down)
        QTest.keyClick(t, Qt.Key_Return)
        assert plays == [win.session.corners.pass_clip(10, 2)], plays
        # key 2 onto the remembered Quickest view, from another tab
        view.select_lap_tab(0)
        view.table.table.setFocus()
        _settle()
        QTest.keyClick(win, Qt.Key_2)
        _settle()
        assert view.tab_bar.currentIndex() == 1 and t.hasFocus()
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_coaching_jump_shows_the_lap_view():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        page = win.view.corners_page
        assert page.view() == "quickest"
        win._jump_to_opportunity(3, 0.0)
        _settle()
        assert win.view.tab_bar.currentIndex() == 1
        assert page.view() == "lap" and page.stack.currentWidget() is page.lap_view
        assert prefs.corners_view() == "quickest", "a jump navigates; it does not choose the view"
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_rebuild_refreshes_the_view_and_the_command_rows():
    """M8: a rebuild (a re-segment, a reference change) re-reads the grid — on screen at once, and
    off screen before the next time it is shown or the command palette lists it."""
    from studio import command_palette

    def titles(win):
        return [e.title for e in command_palette.entries(win) if e.title.startswith("Quickest ")]

    for shown in ("quickest", "lap"):
        prefs.set_corners_view(shown)
        win = _window()
        try:
            s, view = win.session, win.view
            assert "Quickest C3 — lap 13" in titles(win)
            orig = s.consistency_lap_ids
            s.consistency_lap_ids = lambda orig=orig: [i for i in orig() if i != 12]
            try:
                view.rebuild_derived_views()
                _settle()
                got = titles(win)
                assert "Quickest C3 — lap 7" in got and "Quickest C3 — lap 13" not in got, (shown,
                                                                                         got)
                view.corners_page.set_view("quickest", remember=False)
                t = view.corners_page.quickest.table
                assert t.item(2, 2).text() == "7", (shown, t.item(2, 2).text())
            finally:
                s.consistency_lap_ids = orig
        finally:
            _close(win)
    prefs.set_corners_view("lap")


def test_a_tie_says_why_it_has_no_star():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        t = win.view.corners_page.quickest.table
        assert t.item(2, 1).toolTip() == ("5 more laps are level within 0.07 s, so no ★: here it "
                                          "marks a quickest pass no other lap matches."), \
            t.item(2, 1).toolTip()
        assert "no other lap took this corner" in t.item(4, 1).toolTip()   # the lone C5
        hdr = t.horizontalHeaderItem(3)
        assert hdr.textAlignment() & Qt.AlignLeft, "Level with sits over its left-aligned words"
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_provisional_timing_withholds_the_star():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = win.view.corners_page.quickest
        cls = type(win.session)
        saved = cls.timing_verified
        cls.timing_verified = property(lambda _self: False)
        try:
            q.refresh()
            assert q.table.item(4, 1).text() == "3.52", q.table.item(4, 1).text()
            assert q.table.item(4, 3).text() == "none · next +0.071"
        finally:
            cls.timing_verified = saved
        q.refresh()
        assert q.table.item(4, 1).text() == "3.52 ★"
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_too_few_laps_and_no_corners():
    from studio.lap_table import QUICKEST_NEEDS_LAPS, CornersPage
    s = _demo_session()
    saved = s.quickest_passes
    s.quickest_passes = lambda: None
    try:
        page = CornersPage(s)
        page.set_view("quickest", remember=False)
        assert page.bar.isVisibleTo(page) and page.quickest.empty.isVisibleTo(page)
        assert page.quickest.empty.findChild(type(page.level_label)) is not None
        texts = [w.text() for w in page.quickest.empty.findChildren(type(page.level_label))]
        assert QUICKEST_NEEDS_LAPS in texts and any("needs 5 clean laps" in t for t in texts)
        assert not page.level_label.isVisibleTo(page)
    finally:
        s.quickest_passes = saved
    corners = s.corners
    saved_list = corners.corner_list
    corners.corner_list = lambda: []
    try:
        page = CornersPage(s)
        page.set_view("quickest", remember=False)
        assert not page.bar.isVisibleTo(page), "no corners: no switch"
        assert page.view() == "lap" and page.stack.currentWidget() is page.lap_view
    finally:
        corners.corner_list = saved_list


def test_cmd_k_lists_one_quickest_row_per_corner_and_runs_it():
    from studio import command_palette
    prefs.set_corners_view("lap")
    win = _window()
    try:
        plays = _record_plays(win.view)
        rows = [e for e in command_palette.entries(win) if e.title.startswith("Quickest ")]
        assert [e.title for e in rows] == [
            "Quickest C1 — lap 7", "Quickest C2 — lap 11", "Quickest C3 — lap 13",
            "Quickest C4 — lap 8", "Quickest C5 — lap 10", "Quickest C6 — lap 13",
            "Quickest C7 — lap 2"]
        rows[2].run()
        _settle()
        assert plays[-1] == win.session.corners.pass_clip(12, 3)
        # run again: still the lap the command NAMES (a row click would cycle to lap 7)
        rows[2].run()
        _settle()
        assert plays[-1] == win.session.corners.pass_clip(12, 3), plays
        assert win.view.corners_page.quickest.playing() == (3, 12)
        assert win.view.tab_bar.currentIndex() == 1
        assert win.view.corners_page.view() == "quickest"
        assert prefs.corners_view() == "lap", "a command shows the view, it does not choose it"
    finally:
        _close(win)

# ------------------------------------------------------------------ the clip end


class _Player:
    """Enough of a QMediaPlayer to watch the pane pause."""

    def __init__(self):
        self.state = QMediaPlayer.PlaybackState.StoppedState

    def play(self):
        self.state = QMediaPlayer.PlaybackState.PlayingState

    def pause(self):
        self.state = QMediaPlayer.PlaybackState.PausedState

    def playbackState(self):
        return self.state

    def setPosition(self, _ms):
        pass


def _pane():
    from studio.player_pane import PlayerPane
    pane = PlayerPane(None)
    pane.player = _Player()
    return pane


def test_the_clip_end_pauses_once_and_lets_playback_go_on():
    pane = _pane()
    pane.seek(10.0)
    pane.play()
    pane.set_clip_end(15.0)
    pane._on_position(14_000)
    assert pane.is_playing()
    pane._on_position(15_000)
    assert not pane.is_playing() and pane.clip_end() is None
    pane.play()                     # ▶ after the clip carries on from there
    pane._on_position(16_000)
    assert pane.is_playing()


def test_any_seek_play_or_pause_drops_the_clip_end():
    """The driver's own transport: a scrub, the slider, ←/→, a lap pick (all seek), Space / ▶."""
    for act in ("seek", "pause", "play", "seek_dragged"):
        pane = _pane()
        pane.play()
        pane.set_clip_end(15.0)
        if act == "seek":
            pane.seek(20.0)
        elif act == "seek_dragged":
            pane.seek_dragged(12.0)
        elif act == "pause":
            pane.pause()
        else:
            pane.play()
        pane.player.play()   # playing again by the backend's own hand, not through pane.play
        pane._on_position(15_500)
        assert pane.clip_end() is None and pane.is_playing(), act

# ------------------------------------------------------------------ the pass zoom
# A played pass zooms the charts onto its corner (the frame `N` and a Coaching Compare use:
# `corner_plot_window` with its own width either side) and the map onto the clip's stretch of the
# lap; the zoom lasts while that pass is the subject (DECISIONS §18 listed "the chart shows the
# whole lap" as open; discovery: the demo's C5 is 5.5 % of the chart's width).


def _chart_x(view):
    lo, hi = view.plots.p_speed.getViewBox().viewRange()[0]
    return lo, hi


def _map_rect(view):
    (x0, x1), (y0, y1) = view.map.plot.getViewBox().viewRange()
    return x0, x1, y0, y1


def _whole_lap(view):
    lo, hi = _chart_x(view)
    fit = view.plots._fit_x_range
    return abs(lo - fit[0]) < 1e-6 and abs(hi - fit[1]) < 1e-6


def _corner_framed(view, cid):
    x0, x1 = view.session.corner_plot_window(cid, view.plots.axis_mode())
    lo, hi = _chart_x(view)
    pad = x1 - x0
    return abs(lo - (x0 - pad)) < 1e-6 and abs(hi - (x1 + pad)) < 1e-6


def _whole_track(view, fitted):
    x0, x1, y0, y1 = _map_rect(view)
    f0, f1, g0, g1 = fitted
    return view.map._view_fitted and abs((x1 - x0) - (f1 - f0)) < 1e-6 and abs(y0 - g0) < 1e-6


def _play(view, row):
    view.corners_page.quickest.activate(row)
    _settle()
    cid, lap = view.corners_page.quickest.playing()
    return cid, lap, view.session.corners.pass_clip(lap, cid)


def _inside(view, clip, frac=0.5):
    """The tick at a point inside the clip, as a playing pane would report it."""
    t = clip[0] + frac * (clip[1] - clip[0])
    view._playback.latest_t = t
    view.tick()
    return t


def test_a_played_pass_zooms_the_charts_and_the_map_onto_its_corner():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        s = win.session
        view.select_lap_tab(1)
        _settle()
        fitted = _map_rect(view)
        assert _whole_lap(view)
        cid, lap, clip = _play(view, 4)                    # C5, lap 10
        assert (cid, lap) == (5, 9)
        assert _corner_framed(view, 5), (_chart_x(view), s.corner_plot_window(5, "distance"))
        # the corner fills a third of the chart (it was 5.5 % of the whole lap)
        lo, hi = _chart_x(view)
        w0, w1 = s.corner_plot_window(5, "distance")
        assert abs((w1 - w0) / (hi - lo) - 1 / 3) < 1e-6
        # the map frames the clip's stretch of lap 10: every point of it inside, the view smaller
        x0, x1, y0, y1 = _map_rect(view)
        f0, f1, g0, g1 = fitted
        assert (x1 - x0) < 0.75 * (f1 - f0) and (y1 - y0) < 0.75 * (g1 - g0)
        import numpy as np
        tt = np.asarray(s.tt)
        sel = (tt >= clip[0]) & (tt <= clip[1])
        xs, ys = np.asarray(s.tx)[sel], np.asarray(s.ty)[sel]
        assert xs.min() > x0 and xs.max() < x1 and ys.min() > y0 and ys.max() < y1
        # ...clear of the map key (left) and the Fit button (right): the free columns
        from PySide6.QtCore import QPointF
        m = view.map
        vbm = m.plot.getViewBox()

        def px(x, y):
            return m.widget.mapFromScene(vbm.mapViewToScene(QPointF(float(x), float(y)))).x()
        left, right = px(xs.min(), ys[xs.argmin()]), px(xs.max(), ys[xs.argmax()])
        assert m._map_key.isVisibleTo(m.widget) and m.fit_btn.isVisibleTo(m.widget)
        assert left > m._map_key.geometry().right(), (left, m._map_key.geometry())
        assert right < m.fit_btn.geometry().left(), (right, m.fit_btn.geometry())
        # the way back where his eyes are: the status line names it
        assert win.statusBar().currentMessage().endswith(
            "next · Esc: whole lap · ⌘K: export this pass"), win.statusBar().currentMessage()
        # ...the marker, at the pass's middle, and the corner's ring are on the canvas
        _inside(view, clip)
        _settle()
        mx, my = view.map.marker.pos().x(), view.map.marker.pos().y()
        assert x0 < mx < x1 and y0 < my < y1
        assert view.map.pinned_corner() == "C5"
        # the visible ways back: the map's Fit, and the chart's readout names Esc
        assert view.map.fit_btn.isVisibleTo(view.map)
        assert view.plots.window_label.text().endswith("Esc: whole lap"), \
            view.plots.window_label.text()
        # again on the row → the next level lap's... C5 has none: C3's next level lap re-frames
        cid, lap, clip = _play(view, 2)
        assert (cid, lap) == (3, 12) and _corner_framed(view, 3)
        cid, lap, clip2 = _play(view, 2)
        assert (cid, lap) == (3, 6) and _corner_framed(view, 3) and clip2 != clip
        # Esc: the whole lap and the whole track, the map's own fit (not the C5 box it came from)
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert _whole_lap(view) and _whole_track(view, fitted)
        assert not view.map.fit_btn.isVisibleTo(view.map)
        assert not view.plots.window_label.text().endswith("Esc: whole lap")
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_zoom_stays_through_the_clip_and_its_pause_and_ends_outside_it():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        view.select_lap_tab(1)
        _settle()
        fitted = _map_rect(view)
        cid, lap, clip = _play(view, 0)
        assert (cid, lap) == (1, 6)
        # the tick still reporting where the video was before the clip's seek landed: ignored
        view._playback.latest_t = clip[0] - 30.0
        view.tick()
        assert _corner_framed(view, 1) and view.pass_zoomed()
        for frac in (0.0, 0.3, 0.6, 1.0):
            _inside(view, clip, frac)
            assert _corner_framed(view, 1), frac
        # a lead-in that starts in the lap before crosses the line, and the charts re-draw for
        # the new lap (`_follow_current_lap`): the frame holds through the refresh
        view.plots.set_laps([lap - 1, 12])
        assert _corner_framed(view, 1)
        view.plots.set_laps([lap, 12])
        # a backend that reports the frame just past the end BEFORE the pause lands: still that pass
        view._playback.latest_t = clip[1] + 0.1
        view.tick()
        assert _corner_framed(view, 1) and view.pass_zoomed()
        # the clip pauses itself, and the pause lands late (4K on a loaded Mac): the zoom STAYS
        view.video.clipEnded.emit()
        _settle()
        view._playback.latest_t = clip[1] + 0.5
        view.tick()
        assert _corner_framed(view, 1) and not view.map._view_fitted and view.pass_zoomed()
        for _ in range(3):                    # ...parked there, tick after tick
            view._playback.applied_t = None
            view.tick()
        assert view.pass_zoomed()
        # play on past the clip → whole lap, whole track
        view._playback.latest_t = clip[1] + 1.0
        view.tick()
        assert _whole_lap(view) and _whole_track(view, fitted)
        # a pause that lands only 0.1 s late keeps it too
        _cid, _lap, clip = _play(view, 0)
        _inside(view, clip)
        view.video.clipEnded.emit()
        view._playback.latest_t = clip[1] + 0.1
        view.tick()
        assert view.pass_zoomed() and _corner_framed(view, 1)
        view.end_pass_zoom()
        # the clip ending because he seeked AWAY (the pane drops the end on a seek) still ends it
        _cid, _lap, clip = _play(view, 0)
        _inside(view, clip)
        view.video.clipEnded.emit()
        view._playback.latest_t = clip[1] + 30.0
        view.tick()
        assert not view.pass_zoomed() and _whole_lap(view)
        # a scrub/seek back before the clip also ends it
        _cid, _lap, clip = _play(view, 0)        # again on C1: its next level lap
        _inside(view, clip)
        view._playback.latest_t = clip[0] - 3.0
        view.tick()
        assert _whole_lap(view) and _whole_track(view, fitted)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_every_restore_path_puts_the_whole_lap_and_track_back():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        view.select_lap_tab(1)
        _settle()
        fitted = _map_rect(view)

        def zoomed():
            _cid, _lap, clip = _play(view, 4)
            _inside(view, clip)
            assert _corner_framed(view, 5) and not view.map._view_fitted
            return clip

        restores = {
            "a lap pick": lambda: view.table.laps_selected.emit([3]),
            "a MOMENTS Jump": lambda: view._on_stats_moment_jump(3, 2),
            "compare": lambda: view.video.compare_btn.click(),
            "a Lap race": lambda: view.start_lap_race(),
            "a re-segment": lambda: view.rebuild_derived_views(),
            "the axis flip": lambda: view.plots.x_mode_combo.setCurrentIndex(1),
        }
        for name, act in restores.items():
            zoomed()
            act()
            _settle()
            if view.is_racing():
                view.end_lap_race()
            if view._comparing():
                view.video.compare_btn.click()
            if view.plots.axis_mode() == "time":
                assert _whole_lap(view), name
                view.plots.x_mode_combo.setCurrentIndex(0)
                _settle()
            assert _whole_lap(view) and _whole_track(view, fitted), name
            assert not view.pass_zoomed(), name

        # TAKING a view over by hand: that view stays where he put it, the other goes back
        zoomed()
        vb = view.plots.p_speed.getViewBox()
        vb.setXRange(580.0, 620.0, padding=0)
        vb.sigRangeChangedManually.emit([True, False])     # what a wheel zoom / drag emits
        _settle()
        lo, hi = _chart_x(view)
        assert abs(lo - 580.0) < 1e-6 and abs(hi - 620.0) < 1e-6, "his zoom stays"
        assert _whole_track(view, fitted) and not view.pass_zoomed()
        view.plots._reset_x_range()
        zoomed()
        view.map.plot.getViewBox().sigRangeChangedManually.emit([True, True])
        _settle()
        assert _whole_lap(view) and not view.pass_zoomed() and not view.map._view_fitted
        view.map.fit_btn.click()
        _settle()
        zoomed()
        view.map.fit_btn.click()                           # the map's Fit
        _settle()
        assert _whole_lap(view) and _whole_track(view, fitted) and not view.pass_zoomed()
        zoomed()
        view.plots.jump_to_next_loss()                     # N: the tour's own zoom
        _settle()
        assert not view.pass_zoomed() and _whole_track(view, fitted)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_cmd_k_plays_a_pass_zoomed_and_compare_at_a_corner_does_not_hold_or_zoom_the_map():
    from studio import command_palette
    prefs.set_corners_view("lap")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        fitted = _map_rect(view)
        rows = [e for e in command_palette.entries(win) if e.title.startswith("Quickest ")]
        rows[5].run()                                      # "Quickest C6 — lap 13"
        _settle()
        assert _corner_framed(view, 6) and view.pass_zoomed() and not view.map._view_fitted
        # a Coaching Compare over the zoom: it ends the pass zoom, frames ITS corner as before
        # (not held: a lap pick or N moves it as ever) and leaves the map on the whole track
        tp = win.session.typical_passes()[1]
        assert view.compare_at_corner(tp, "+0.36 s")
        _settle()
        assert view._comparing() and not view.pass_zoomed() and not view.plots.frame_held()
        assert _corner_framed(view, 1) and _whole_track(view, fitted)
        # ...and a Lap race over a zoom draws on the whole track (Follow off, the race's own view)
        view.video.compare_btn.click()
        _settle()
        rows[5].run()
        _settle()
        assert view.pass_zoomed() and view.start_lap_race()
        _settle()
        assert not view.map.pass_zoomed() and not view.plots.frame_held()
        view.end_lap_race()
        _settle()
        assert _whole_track(view, fitted) and _whole_lap(view)
    finally:
        _close(win)


def test_the_tick_inside_a_zoomed_pass_moves_neither_view():
    """Perf invariant 4 (the tick is cheap lookups): while the playhead runs through the clip, no
    tick re-frames the charts or re-ranges the map — the zoom is set once, on the play."""
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        _cid, _lap, clip = _play(view, 4)
        calls = []
        orig_frame, orig_range = view.plots.frame_span, view.map.plot.getViewBox().setRange
        view.plots.frame_span = lambda *a: (calls.append("frame"), orig_frame(*a))
        view.map.plot.getViewBox().setRange = lambda *a, **k: (calls.append("map"),
                                                               orig_range(*a, **k))
        for i in range(60):
            _inside(view, clip, i / 60)
        assert calls == [] and view.pass_zoomed(), calls
    finally:
        _close(win)
        prefs.set_corners_view("lap")


# ------------------------------------------------------------------ every corner time plays its pass
# (cool/play-every-pass) A Corners lap row, a Stats ▸ CORNERS BY LAP cell and the Laps table's
# "▼ C5" cue PLAY that lap's pass through the corner, exactly as a Quickest row plays its own —
# the lead-in, the pause after the exit, the zoom, one status builder — and ⌥← / ⌥→ walk the
# corners one pass per press. One "pass being studied" behind all of them: the zoom, ⌘K's export
# row and the walk follow a pass started anywhere.


def _cell(win, lap, cid):
    m = win.session.corner_matrix()
    return m.cells[m.lap_ids.index(lap)][m.cids.index(cid)]


def _export_rows(win):
    return [t for t, _c in win.quickest_pass_commands() if t.startswith("Export the")]


def _patched_matrix(win, *, unresolved=None, drop_lap=None):
    """The session's grid with one cell unmatched on track, or one lap not a row (⊘ / ⚠)."""
    import dataclasses
    real = win.session.corner_matrix

    def patched():
        m = real()
        resolved = [list(row) for row in m.resolved]
        if unresolved is not None:
            lap, cid = unresolved
            resolved[m.lap_ids.index(lap)][m.cids.index(cid)] = False
        m = dataclasses.replace(m, resolved=resolved)
        if drop_lap is not None:
            k = m.lap_ids.index(drop_lap)
            m = dataclasses.replace(
                m, lap_ids=[i for i in m.lap_ids if i != drop_lap],
                cells=[row for i, row in enumerate(m.cells) if i != k],
                resolved=[row for i, row in enumerate(m.resolved) if i != k])
        return m
    win.session.corner_matrix = patched
    return real


def test_a_corners_lap_row_plays_that_laps_own_pass():
    prefs.set_corners_view("lap")
    win = _window()
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        view.table.laps_selected.emit([5])           # the driver picks lap 6 (the slow one)
        view.select_lap_tab(1)
        _settle()
        assert view.corners_page.view() == "lap"
        row = [view.corner_table.cid_at(r) for r in range(7)].index(5)
        view.corner_table.table.cellClicked.emit(row, 1)
        _settle()
        assert plays[-1] == s.corners.pass_clip(5, 5), plays
        assert view.map.pinned_corner() == "C5", "the ring the row click always made stays"
        assert _corner_framed(view, 5) and view.pass_zoomed()
        msg = win.statusBar().currentMessage()
        assert msg == (f"Playing lap 6 through C5 ({_cell(win, 5, 5):.2f}) · Esc: whole lap · "
                       "⌘K: export this pass"), msg
        assert "quickest" not in msg.lower()
        # the lap's own time, as the Corners row and the grid print it (6.45, the spin)
        assert f"({_cell(win, 5, 5):.2f})" == "(6.45)"
        assert view.pass_subject() == (5, 5, "C5")
        assert _export_rows(win) == ["Export the C5 pass — lap 6…"]
        assert view.corners_page.quickest.playing() is None, "no Quickest row names this pass"
    finally:
        _close(win)


def test_a_corners_by_lap_cell_plays_its_pass_and_a_cell_it_cannot_play_says_why():
    prefs.set_corners_view("lap")
    win = _window()
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        view.show_stats_maximized() if hasattr(view, "show_stats_maximized") else None
        _settle()
        stats = view.stats_view
        grid = stats.corner_grid_table     # (the hand over playable cells: its own test below)
        m = s.corner_matrix()
        lead = len(stats_panel_lead())
        r, c = m.lap_ids.index(8), m.cids.index(3)          # lap 9, C3
        assert "Click to play this lap's pass" in grid.item(r, c + lead).toolTip()
        grid.cellClicked.emit(r, c + lead)
        _settle()
        assert plays[-1] == s.corners.pass_clip(8, 3)
        assert getattr(view, "_maximized_panel", None) is None, "the grid came back to show it"
        assert win.statusBar().currentMessage().startswith(
            f"Playing lap 9 through C3 ({_cell(win, 8, 3):.2f})")
        assert view.pass_zoomed() and view.pass_subject() == (8, 3, "C3")
        assert _export_rows(win) == ["Export the C3 pass — lap 9…"]
        # the lead columns (the lap, its time) play nothing
        n = len(plays)
        grid.cellClicked.emit(r, 0)
        assert len(plays) == n
        # a cell matched on track at one edge only: not played, and the status line says why
        _patched_matrix(win, unresolved=(8, 4))
        grid.cellClicked.emit(r, m.cids.index(4) + lead)
        assert len(plays) == n
        assert "not matched on track at both edges" in win.statusBar().currentMessage()
        # a lap that is no row of the grid (⊘ excluded / ⚠ dropout): not played either
        _patched_matrix(win, drop_lap=2)
        assert not view.play_lap_pass(2, 1) and len(plays) == n
        assert "not counted in the corner times" in win.statusBar().currentMessage()
    finally:
        _close(win)


def stats_panel_lead():
    from studio.stats_panel import CORNER_GRID_LEAD_COLUMNS
    return CORNER_GRID_LEAD_COLUMNS


def test_the_slow_lap_cue_plays_its_corner_and_a_plain_row_click_goes_to_the_lap_start():
    win = _window()
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        seeks = []
        view.video.seek = lambda t: seeks.append(t)
        view.select_lap_tab(0)
        _settle()
        t = view.table.table
        row = next(r for r in range(t.rowCount()) if view.table._lap_id(r) == 5)
        assert "▼ C5" in t.item(row, 0).text()
        # a plain click on another column: the lap's start, nothing played
        t.selectRow(row)
        view.table.laps_selected.emit([5])
        t.cellClicked.emit(row, 1)
        _settle()
        assert plays == [] and seeks and abs(seeks[-1] - s.lap_window(5)[0]) < 0.1
        # the cue: lap 6's C5, from its lead-in (27.6 s after the lap's start on the demo)
        t.cellClicked.emit(row, 0)
        _settle()
        assert plays == [s.corners.pass_clip(5, 5)], plays
        assert s.corners.corner_entry_media_time(5, 5) - s.lap_window(5)[0] > 20
        assert view.map.pinned_corner() == "C5"
        assert win.statusBar().currentMessage().startswith("Playing lap 6 through C5 (6.45)")
        assert view.pass_zoomed() and _export_rows(win) == ["Export the C5 pass — lap 6…"]
    finally:
        _close(win)


def test_the_corner_walk_plays_one_pass_per_press():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        # from a per-lap pass: the same lap, the next / previous corner
        assert view.play_lap_pass(3, 3)
        win._next_corner_action.trigger()
        assert plays[-1] == s.corners.pass_clip(3, 4) and view.pass_subject() == (3, 4, "C4")
        win._prev_corner_action.trigger()
        win._prev_corner_action.trigger()
        assert plays[-1] == s.corners.pass_clip(3, 2) and len(plays) == 4
        assert win.statusBar().currentMessage().startswith("Playing lap 4 through C2 (")
        # at the lap's last corner it stops and says so — no reel, no wrap
        assert view.play_lap_pass(3, 7)
        n = len(plays)
        assert not view.step_corner_pass(+1) and len(plays) == n
        assert "the last one with a pass on lap 4" in win.statusBar().currentMessage()
        # from a Quickest row: the next corner's QUICKEST pass (C4 → lap 8), the row's own status
        view.select_lap_tab(1)
        _settle()
        q = view.corners_page.quickest
        q.activate(q.row_of(3))
        win._next_corner_action.trigger()
        assert plays[-1] == s.corners.pass_clip(7, 4) and q.playing() == (4, 7)
        assert win.statusBar().currentMessage().startswith("Playing lap 8 (3.02) through C4")
        # with nothing being studied: the corner after the playhead, on its lap
        view._forget_quickest_cycle()
        assert view.pass_subject() is None
        c2_exit = s.corners.corner_edge_media_time(10, 2, "exit")
        view._playback.applied_t = c2_exit + 0.5
        win._next_corner_action.trigger()
        assert plays[-1] == s.corners.pass_clip(10, 3), plays[-1]
        # the keys are the menu actions ⌘K harvests, and the card names them
        assert win._next_corner_action.shortcut().toString() == "Alt+Right"
        assert win._prev_corner_action.shortcut().toString() == "Alt+Left"
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_right_click_offers_play_and_export_for_the_pass_a_row_or_cell_names():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        assert view.passExportRequested.disconnect(win.exports.export_pass_video), \
            "the window wires the menu's export to the ⌘K row's own export path"
        exported = []
        view.passExportRequested.connect(lambda *a: exported.append(a))
        view.select_lap_tab(1)
        _settle()
        q = view.corners_page.quickest
        pos = q.table.visualRect(q.table.model().index(4, 1)).center()   # C5's row
        menu = view.pass_menu(view._quickest_menu_pass(q.table, pos))
        names = [a.text() for a in menu.actions()]
        assert names == ["Play this pass", "Export this pass…", "Compare with my typical pass"], \
            names
        menu.actions()[0].trigger()
        assert plays[-1] == s.corners.pass_clip(9, 5) and q.playing() == (5, 9)
        menu.actions()[1].trigger()
        assert exported == [(9, 5, "C5")]
        # the CORNERS BY LAP cell's menu, and a cell it cannot play greys both out with the reason
        grid = view.stats_view.corner_grid_table
        lead = len(stats_panel_lead())
        m = s.corner_matrix()
        pos = grid.visualRect(grid.model().index(m.lap_ids.index(4), m.cids.index(2) + lead)).center()
        menu = view.pass_menu(view._grid_menu_pass(grid, pos))
        menu.actions()[1].trigger()
        assert exported[-1] == (4, 2, "C2")
        _patched_matrix(win, unresolved=(4, 2))
        menu = view.pass_menu(view._grid_menu_pass(grid, pos))
        assert not any(a.isEnabled() for a in menu.actions())
        assert "not matched on track" in menu.actions()[0].toolTip()
        # a Corners lap row's menu names its lap's pass
        view.corners_page.set_view("lap", remember=False)
        view.table.laps_selected.emit([6])
        _settle()
        ct = view.corner_table.table
        menu = view.pass_menu(view._corner_row_menu_pass(ct, ct.visualRect(
            ct.model().index(0, 0)).center()))
        menu.actions()[0].trigger()
        assert plays[-1] == s.corners.pass_clip(6, 1)
        # nothing under the pointer: no menu
        assert view.pass_menu(view._grid_menu_pass(grid, grid.viewport().rect().bottomRight()
                                                   + grid.viewport().rect().bottomRight())) is None
    finally:
        _close(win)
        prefs.set_corners_view("lap")


# ------------------------------------------------------------------ review round 1 (#624)


def _click(table, row, col, button=Qt.LeftButton, mods=Qt.NoModifier):
    """A REAL click on a table cell: the viewport's mouse events, as the driver's would arrive."""
    pos = table.visualRect(table.model().index(row, col)).center()
    QTest.mouseClick(table.viewport(), button, mods, pos)
    _settle(0.05)


def test_a_held_corner_walk_key_plays_one_pass_not_the_lap():
    """§31: one press, one pass. A held ⌥→ is one press and a train of auto-repeats; before this
    round the six repeats walked C2 → C7 in a third of a second."""
    from PySide6.QtCore import QEvent
    from PySide6.QtGui import QKeyEvent
    win = _window()
    try:
        view = win.view
        plays = _record_plays(view)
        assert not win._next_corner_action.autoRepeat() and not win._prev_corner_action.autoRepeat()
        assert view.play_lap_pass(3, 1)
        view.select_lap_tab(0)
        view.table.table.setFocus()
        _settle()
        w = QApplication.focusWidget()
        n = len(plays)
        QTest.keyPress(w, Qt.Key_Right, Qt.AltModifier)
        _settle(0.03)
        for _ in range(6):
            QApplication.sendEvent(w, QKeyEvent(QEvent.KeyPress, Qt.Key_Right, Qt.AltModifier,
                                                "", True))
            _settle(0.03)
        QTest.keyRelease(w, Qt.Key_Right, Qt.AltModifier)
        assert len(plays) == n + 1 and view.pass_subject() == (3, 2, "C2"), (len(plays) - n,
                                                                            view.pass_subject())
    finally:
        _close(win)


def test_the_corner_walk_keys_leave_a_text_field_its_word_moves():
    """⌥← / ⌥→ in a focused text field (the Marks note, the palette's search) move a word, as on
    every Mac text field — they do not walk the corners from under the caret."""
    from PySide6.QtWidgets import QLineEdit
    win = _window()
    try:
        view = win.view
        plays = _record_plays(view)
        assert view.play_lap_pass(3, 3)
        edit = QLineEdit(view)
        edit.setText("hello world")
        edit.show()
        edit.setFocus()
        _settle()
        edit.setCursorPosition(11)
        QTest.keyClick(edit, Qt.Key_Left, Qt.AltModifier)
        _settle()
        assert edit.cursorPosition() == 6 and len(plays) == 1, (edit.cursorPosition(), plays)
        QTest.keyClick(edit, Qt.Key_Right, Qt.AltModifier)
        _settle()
        assert edit.cursorPosition() == 11 and len(plays) == 1
        edit.hide()
        edit.deleteLater()
    finally:
        _close(win)


def test_a_command_or_shift_click_is_a_selection_never_a_play():
    """⌘-click and ⇧-click are SELECTION gestures on every surface that plays a pass: they must
    not throw away the driver's multi-lap selection to play something. On the cue the ⌘-click
    still adds the lap and rings its corner, as before this PR."""
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)
        view.select_lap_tab(0)
        _settle()
        t = view.table.table
        row_of = {view.table._lap_id(r): r for r in range(t.rowCount())}
        _click(t, row_of[2], 1)                              # lap 3 picked plainly
        _click(t, row_of[5], 0, mods=Qt.ControlModifier)     # ⌘-click lap 6's "▼ C5" cue
        assert plays == [] and sorted(view.table.selected_lap_ids()) == [2, 5], (
            plays, view.table.selected_lap_ids())
        assert view.map.pinned_corner() == "C5", "the cue's ring, as before"
        _click(t, row_of[5], 0, mods=Qt.ShiftModifier)
        assert plays == []
        # ...and the cue's plain click still plays
        _click(t, row_of[5], 0)
        assert plays == [s.corners.pass_clip(5, 5)]
        n = len(plays)
        # a Quickest row, a Corners lap row, a CORNERS BY LAP cell
        view.select_lap_tab(1)
        _settle()
        q = view.corners_page.quickest
        for mods in (Qt.ControlModifier, Qt.ShiftModifier):
            _click(q.table, 2, 1, mods=mods)
        view.corners_page.set_view("lap", remember=False)
        _settle()
        for mods in (Qt.ControlModifier, Qt.ShiftModifier):
            _click(view.corner_table.table, 0, 1, mods=mods)
        view.select_lap_tab(2)
        _settle()
        grid = view.stats_view.corner_grid_table
        lead = len(stats_panel_lead())
        for mods in (Qt.ControlModifier, Qt.ShiftModifier):
            _click(grid, 3, lead + 1, mods=mods)
        assert len(plays) == n, plays[n:]
        _click(grid, 3, lead + 1)                            # the plain click plays
        assert len(plays) == n + 1
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_right_click_shows_the_menu_and_plays_nothing():
    """A REAL right-click (the press and the context-menu event) on a Quickest row, a Corners row
    and a grid cell opens the menu and plays nothing by itself."""
    from PySide6.QtGui import QContextMenuEvent
    prefs.set_corners_view("quickest")
    win = _window()
    shown = []
    try:
        view = win.view
        view._run_menu = lambda menu, _at: shown.append([a.text() for a in menu.actions()])
        plays = _record_plays(view)
        view.table.laps_selected.emit([4])
        view.select_lap_tab(1)
        _settle()

        def right_click(table, row, col):
            pos = table.visualRect(table.model().index(row, col)).center()
            QTest.mouseClick(table.viewport(), Qt.RightButton, Qt.NoModifier, pos)
            QApplication.sendEvent(table.viewport(), QContextMenuEvent(
                QContextMenuEvent.Mouse, pos, table.viewport().mapToGlobal(pos)))
            _settle(0.05)
        right_click(view.corners_page.quickest.table, 2, 1)
        view.corners_page.set_view("lap", remember=False)
        _settle()
        right_click(view.corner_table.table, 1, 1)
        view.select_lap_tab(2)
        _settle()
        right_click(view.stats_view.corner_grid_table, 3, len(stats_panel_lead()) + 2)
        assert plays == [], plays
        assert shown == [["Play this pass", "Export this pass…",
                          "Compare with my typical pass"]] * 3, shown
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_cycled_quickest_rows_menu_exports_the_lap_it_names():
    """The row reads "playing lap 7 (2.35) · 2 of 6": its menu's pass is lap 7's, the same one
    ⌘K's export row names — not the quickest lap 13 beside it."""
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        plays = _record_plays(view)
        view.passExportRequested.disconnect(win.exports.export_pass_video)
        exported = []
        view.passExportRequested.connect(lambda *a: exported.append(a))
        view.select_lap_tab(1)
        _settle()
        q = view.corners_page.quickest
        q.activate(2)
        q.activate(2)                                        # → lap 7, 2 of 6
        assert q.table.item(2, 3).text().endswith("lap 7 (2.35) · 2 of 6")
        pos = q.table.visualRect(q.table.model().index(2, 1)).center()
        menu = view.pass_menu(view._quickest_menu_pass(q.table, pos))
        menu.actions()[1].trigger()
        assert exported == [(6, 3, "C3")], exported
        assert _export_rows(win) == ["Export the C3 pass — lap 7…"]
        menu.actions()[0].trigger()                          # "Play this pass": lap 7 again
        assert plays[-1] == win.session.corners.pass_clip(6, 3) and q.playing() == (3, 6)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_pass_being_studied_lives_until_it_is_left_not_until_its_clip_ends():
    """THE RULE (`CentralView.pass_subject`): from play until the pass is LEFT. The auto-pause at
    the clip's end keeps it (export, walk on); Esc, Compare, a Lap race, a lap pick and ▶ playing
    on leave it, and then ⌘K offers no export and the walk steps from the playhead."""
    win = _window()
    try:
        view, s = win.view, win.session
        plays = _record_plays(view)

        def studied():
            assert view.play_lap_pass(3, 3)
            _settle()
            assert view.pass_subject() == (3, 3, "C3")

        # the clip ends by itself (the auto-pause 1 s after the exit): still the subject
        studied()
        view.video.pane.pause()
        view.video.clipEnded.emit()
        _settle()
        assert view.pass_subject() == (3, 3, "C3")
        assert _export_rows(win) == ["Export the C3 pass — lap 4…"]
        win._next_corner_action.trigger()
        assert plays[-1] == s.corners.pass_clip(3, 4), "the walk goes on from the pass he watched"

        leaves = {
            "Esc": lambda: QTest.keyClick(win, Qt.Key_Escape),
            "Compare": lambda: view.video.compare_btn.click(),
            "a Lap race": lambda: view.start_lap_race(),
            "a lap pick": lambda: view.table.laps_selected.emit([9]),
            "▶ playing on": lambda: view.video.pane.play(),
        }
        for name, leave in leaves.items():
            studied()
            leave()
            _settle()
            assert view.pass_subject() is None, name
            assert _export_rows(win) == [], (name, _export_rows(win))
            if view.is_racing():
                view.end_lap_race()
            if view._comparing():
                view.video.compare_btn.click()
            _settle()
        # after Esc the walk steps from the playhead, not back to lap 4
        studied()
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        view._playback.applied_t = s.corners.corner_edge_media_time(10, 2, "exit") + 0.5
        win._next_corner_action.trigger()
        assert plays[-1] == s.corners.pass_clip(10, 3), plays[-1]
    finally:
        _close(win)


def test_the_hand_shows_only_over_cells_that_play():
    from PySide6.QtCore import QEvent, QPointF
    from PySide6.QtGui import QMouseEvent
    win = _window()
    try:
        view = win.view
        view.select_lap_tab(2)
        _settle()
        grid = view.stats_view.corner_grid_table
        lead = len(stats_panel_lead())

        def hover(row, col):
            pos = QPointF(grid.visualRect(grid.model().index(row, col)).center())
            QApplication.sendEvent(grid.viewport(), QMouseEvent(
                QEvent.MouseMove, pos, grid.viewport().mapToGlobal(pos), Qt.NoButton,
                Qt.NoButton, Qt.NoModifier))
            return grid.viewport().cursor().shape()
        assert hover(3, lead + 1) == Qt.PointingHandCursor
        assert hover(3, 0) == Qt.ArrowCursor and hover(3, 1) == Qt.ArrowCursor
        _patched_matrix(win, unresolved=(3, 2))
        view.stats_view._corner_matrix = win.session.corner_matrix()
        assert hover(3, lead + 1) == Qt.ArrowCursor, "an unmatched cell does not play"
    finally:
        _close(win)


# ------------------------------------------------------------------ every lap's time, at a glance
# (cool/quickest-sparklines) Each row's strip: every counted pass through the corner as a dot on
# the row's own time scale, quickest at the left, the level band at its true width, the typical
# dashed, a ▼ cell off the scale, the lap the row's cycle names ringed. Read off the SAME
# `stats.quickest_passes` result as the row (one source), shown only where the words keep room.


def test_the_strip_is_the_rows_own_counted_cells_band_and_typical():
    from studio.stats import STRIP_MIN_SPAN_STEPS, pass_strip
    rows = [[3.050, 2.0], [3.000, 2.5], [3.069, 2.6], [3.300, 2.7], [3.020, 2.8], [3.2, 2.9],
            [9.000, 3.0]]
    res = [[True, True]] * 5 + [[False, True], [True, True]]
    qp = stats.quickest_passes(_grid(rows, res))
    c1 = qp.passes[0]
    # every COUNTED pass in lap order: lap 5's unmatched cell is not drawn
    assert c1.marks == ((0, 3.050), (1, 3.000), (2, 3.069), (3, 3.300), (4, 3.020), (6, 9.000))
    assert c1.typical_s == _grid(rows, res).medians[0]
    assert c1.behind_laps == (6,), "the grid's ▼ (9.000 s) is the strip's only off-scale mark"
    s = pass_strip(c1, qp.level_s)
    assert (s.lo, s.hi) == (3.000, 3.300), "quickest → slowest ON-scale pass"
    assert abs(s.band_x - PB_PRECISION_S / 0.300) < 1e-12, "the band is the step at true width"
    assert abs(s.typical_x - (c1.typical_s - 3.0) / 0.3) < 1e-12
    by_lap = {m.lap_id: m for m in s.marks}
    assert [m.lap_id for m in s.marks] == [0, 1, 2, 3, 4, 6]
    assert by_lap[1].x == 0.0 and by_lap[3].x == 1.0
    assert by_lap[6].behind and by_lap[6].x == 1.0 and not by_lap[6].level
    # LEVEL is the row's own rule (play_order, is_level on the printed ms), never the pixel
    assert {m.lap_id for m in s.marks if m.level} == set(c1.play_order()) == {1, 4, 0, 2}
    assert all(m.x <= s.band_x + 1e-9 for m in s.marks if m.level)
    # EXACTLY the step behind is not level (the text's rule, on the printed ms), even though its raw
    # gap (3.070 − 3.000 = 0.06999…) is not more than the step: a pixel rule would call it level
    edge = pass_strip(stats.quickest_passes(_grid([[3.000], [3.070], [3.069], [3.3], [3.4]]))
                      .passes[0], PB_PRECISION_S)
    by_lap = {m.lap_id: m for m in edge.marks}
    assert by_lap[2].level and not by_lap[1].level, edge
    # a tight corner: every pass inside one step → the span floors at two steps, the band at half
    wide = pass_strip(qp.passes[1], qp.level_s)
    # C2: lap 6's 3.0 is the grid's ▼ (0.30 over the median), so the scale stops at 2.9
    assert qp.passes[1].alone and qp.passes[1].behind_laps == (6,)
    assert abs((wide.hi - wide.lo) - 0.9) < 1e-12
    flat = stats.quickest_passes(_grid([[2.00], [2.01], [2.02], [2.03], [2.04]]))
    fs = pass_strip(flat.passes[0], flat.level_s)
    assert abs((fs.hi - fs.lo) - STRIP_MIN_SPAN_STEPS * PB_PRECISION_S) < 1e-12
    assert abs(fs.band_x - 1 / STRIP_MIN_SPAN_STEPS) < 1e-12
    # on an estimated clock the band widens with the step, on the same cells
    est = stats.quickest_passes(_grid(rows, res), clock_estimated=True)
    assert abs(pass_strip(est.passes[0], est.level_s).band_x - 0.15 / 0.300) < 1e-12
    # nothing counted, nothing drawn
    none = stats.quickest_passes(_grid([[2.0, 3.0]] * 5, [[False, True]] * 5))
    assert none.passes[0].marks == () and pass_strip(none.passes[0], none.level_s) is None


def test_the_demo_strips_show_the_lone_quickest_the_habit_and_the_ties():
    """Not forced, read: C5's quickest alone short of every other dot; C1's planted habit as two
    clusters far apart; the ties as several dots inside the band."""
    s = _demo_session()
    qp = s.quickest_passes()
    strips = {p.cid: stats.pass_strip(p, qp.level_s) for p in qp.passes}
    for p in qp.passes:
        assert len(strips[p.cid].marks) == p.n_counted == 14
        assert p.typical_s is not None
    c5 = strips[5]
    others = [m.x for m in c5.marks if m.lap_id != 9]
    assert qp.passes[4].alone and min(others) > c5.band_x, "C5's quickest stands alone"
    xs = sorted(m.x for m in strips[1].marks)
    gaps = [b - a for a, b in zip(xs, xs[1:], strict=False)]
    big = max(gaps)
    assert big > 0.7 and gaps.index(big) == 5, ("C1: six quick passes, a gap of most of the "
                                                f"scale, eight slow ones: {xs}")
    for cid in (1, 2, 3, 4, 6, 7):
        inside = [m for m in strips[cid].marks if m.level]
        assert len(inside) >= 6 and all(m.x <= strips[cid].band_x + 1e-9 for m in inside)
    # lap 6's spin at C5 and C6 is the grid's ▼: off the scale, not squashing the rest
    assert {m.lap_id for c in (5, 6) for m in strips[c].marks if m.behind} == {5}


def _strip_table(win, w, h, maximised=False):
    view = win.view
    win.resize(w, h)
    _settle(0.3)
    view.select_lap_tab(1)
    if maximised != (view._maximized_panel is view._table_panel):
        view._toggle_panel_maximized(view._table_panel)
    _settle(0.3)
    return view.corners_page.quickest


def _painted_whole(t, row, col):
    """Whether cell (row, col)'s text is painted WHOLE, measured as Qt paints it: the style's text
    rect less its text margin, against the text's fractional advance (and its elision). A size
    hint is not the paint: it passed while the cell read "… · 11 of…" (the review of #627)."""
    from PySide6.QtGui import QFontMetricsF
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    idx = t.model().index(row, col)
    opt = QStyleOptionViewItem()
    opt.initFrom(t.viewport())
    opt.widget = t
    t.itemDelegateForIndex(idx).initStyleOption(opt, idx)
    opt.rect = t.visualRect(idx)
    style = t.style()
    avail = (style.subElementRect(QStyle.SE_ItemViewItemText, opt, t).width()
             - 2 * (style.pixelMetric(QStyle.PM_FocusFrameHMargin, None, t) + 1))
    fm = QFontMetricsF(opt.font)
    return (fm.horizontalAdvance(opt.text) <= avail
            and fm.elidedText(opt.text, Qt.ElideRight, avail) == opt.text), (opt.text, avail)


def _strip_room_everywhere():
    """The fit, at whatever device pixel ratio this process runs: docked at 1280×800 and 1440×900
    and in a maximised panel, every row's "Level with" words painted whole in every state — its
    count, each level lap cycled, playing, and with Loop on — no scrollbar, the strip shown beside
    them (stepping aside under Loop). Returns the strip widths."""
    from studio.lap_table import QUICKEST_LEVEL_COL, QUICKEST_STRIP_COL
    prefs.set_corners_view("quickest")
    win = _window()
    widths = {}
    try:
        t = win.view.corners_page.quickest.table
        for size in ((1440, 900, False), (1280, 800, False), (1440, 900, True),
                     (1280, 800, True)):
            q = _strip_table(win, *size)
            assert not t.isColumnHidden(QUICKEST_STRIP_COL), (size, t.viewport().width(),
                                                               q._level_need_px)
            widths[size] = t.columnWidth(QUICKEST_STRIP_COL)
            assert t.rowCount() == 7
            states = [(None, False, False)] + [
                ((qp.cid, k), running, looping) for qp in q.passes()
                for k in range(len(qp.play_order())) for running in (False, True)
                for looping in (False, True)]
            longest = ""
            for cycle, running, looping in states:
                q._cycle, q._running = cycle, running
                q.set_looping(looping)
                q._paint_playing()
                assert t.isColumnHidden(QUICKEST_STRIP_COL) == looping, size
                for r in range(7):
                    whole, why = _painted_whole(t, r, QUICKEST_LEVEL_COL)
                    assert whole, (size, cycle, running, looping, why)
                    longest = max(longest, why[0], key=len)
                assert t.verticalScrollBar().maximum() == 0, size    # every row in view...
                assert not q.rows_bar.isVisible(), size              # ...so no bar says otherwise
                assert not t.horizontalScrollBar().isVisible(), size
            assert longest.endswith("· 11 of 11 · looping"), longest
            q.set_looping(False)
            q._cycle, q._running = None, False
            q._paint_playing()
    finally:
        _close(win)
        prefs.set_corners_view("lap")
    return widths


def test_the_strip_shows_only_where_the_words_keep_their_room():
    """Docked at 1280×800 and 1440×900, and in a maximised panel, the strip takes what the words
    leave. Never a scrollbar, never an elided word, in any state a row prints (C2 cycled to its
    11th level lap is the longest); while Loop runs it steps aside and the longer "· looping" words
    fit too. The words never move when it shows or hides."""
    from studio.lap_table import (
        QUICKEST_LEVEL_COL,
        QUICKEST_PLAY_COL,
        QUICKEST_STRIP_COL,
        STRIP_MAX_PX,
        STRIP_MIN_PX,
        QuickestPassTable,
    )
    widths = _strip_room_everywhere()
    assert all(STRIP_MIN_PX <= w for w in widths.values()), widths
    assert widths[(1440, 900, True)] == widths[(1280, 800, True)] == STRIP_MAX_PX, widths
    # between the words and ▶: showing it never moves the words
    q = QuickestPassTable(_demo_session())
    hdr = q.table.horizontalHeader()
    assert hdr.visualIndex(QUICKEST_STRIP_COL) == hdr.visualIndex(QUICKEST_PLAY_COL) - 1
    assert hdr.visualIndex(QUICKEST_LEVEL_COL) == QUICKEST_LEVEL_COL
    assert q.table.horizontalHeaderItem(QUICKEST_STRIP_COL).text() == "Every lap"
    print("strip widths", widths)


def test_the_words_are_whole_at_two_device_pixels_per_point_too():
    """The same fit at QT_SCALE_FACTOR=2 (a Retina screen's device pixel ratio), in a child
    process: the ratio is fixed when the QApplication starts."""
    import subprocess
    code = ("import sys; sys.path.insert(0, 'tests'); import test_quickest_passes as T; "
            "print('widths', T._strip_room_everywhere())")
    # The child is jailed in its own right (AGENTS.md rule 2): it builds a window and writes prefs.
    env = {**os.environ, "QT_SCALE_FACTOR": "2", "QT_QPA_PLATFORM": "offscreen",
           "PACER_APP_SUPPORT_JAIL": "1", "PACER_NO_MEDIA": "1"}
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    r = subprocess.run([sys.executable, "-c", code], cwd=repo, env=env, capture_output=True,
                       text=True, timeout=300)
    assert r.returncode == 0, (r.stdout[-2000:], r.stderr[-4000:])
    assert "widths {" in r.stdout, r.stdout[-2000:]


def _paint_cell(delegate, table, row, col, margin=12):
    """Paint one cell through the delegate into an image with `margin` px of transparency round
    it: (image, the cell's rect inside it)."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem
    w, h = table.columnWidth(col), table.rowHeight(row)
    img = QImage(w + 2 * margin, h + 2 * margin, QImage.Format_ARGB32)
    img.fill(0)
    opt = QStyleOptionViewItem()
    opt.initFrom(table.viewport())
    opt.state &= ~QStyle.State_HasFocus      # no current-cell outline: only what the strip paints
    cell = QRect(margin, margin, w, h)
    opt.rect = QRect(cell)
    opt.widget = table
    p = QPainter(img)
    delegate.paint(p, opt, table.model().index(row, col))
    p.end()
    return img, cell


def test_the_strip_paints_each_mark_where_its_time_is_and_nothing_outside_its_cell():
    from PySide6.QtCore import QPointF, QRectF
    from PySide6.QtGui import QColor

    from studio.lap_table import (
        QUICKEST_STRIP_COL,
        STRIP_DOT_R,
        STRIP_PLAYING_ROLE,
        STRIP_RING_PX,
        STRIP_ROLE,
        strip_geometry,
    )
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900, maximised=True)
        t = q.table
        rect = QRectF(t.visualRect(t.model().index(0, QUICKEST_STRIP_COL)))
        for r, qp in enumerate(q.passes()):
            strip, star_lap = t.item(r, QUICKEST_STRIP_COL).data(STRIP_ROLE)
            assert strip == stats.pass_strip(qp, q.level_s()), "one source: the row's own result"
            lay = strip_geometry(strip, rect)
            band, typical, marks, rad = lay.band, lay.typical, lay.marks, lay.r
            assert len(marks) == qp.n_counted and lay.piled == 0, (r, len(marks), lay.piles)
            pad = theme.SPACE_XS + STRIP_DOT_R + STRIP_RING_PX
            gutter = 2 * STRIP_DOT_R + theme.SPACE_XS if qp.behind_laps else 0
            span_px = rect.width() - 2 * pad - gutter        # the row's scale, lo → hi
            assert band.left() == rect.left() + pad
            # each dot at its own time on the row's scale — a pass NOT level pushed clear of the
            # band (one radius at most) — the quickest at the band's left edge, ▼ in the gutter
            for m, c in marks:
                want = band.left() + m.x * span_px
                if m.behind:
                    want = band.left() + span_px + gutter
                elif not m.level:
                    assert c.x() - rad >= band.right() - 1e-9, (r, m, "a not-level dot on the band")
                    assert c.x() - want <= rad + 1e-9, (r, m)
                    want = max(want, band.right() + rad)
                assert abs(c.x() - want) < 1e-9, (r, m)
            assert next(c.x() for m, c in marks if m.lap_id == qp.lap_id) == band.left()
            on_scale = [c.x() for m, c in marks if not m.behind]
            for m, c in marks:
                if m.behind:   # the gutter: a ▼ never sits on the slowest dot (C5)
                    assert c.x() - max(on_scale) >= 2 * rad, (r, c.x(), max(on_scale))
            # the band in px is the step on this row's scale: band_px / span_px == step / span
            assert abs(band.width() / span_px - q.level_s() / (strip.hi - strip.lo)) < 1e-9
            assert abs(typical - (band.left() + strip.typical_x * span_px)) < 1e-9
            assert all(rect.contains(c) for _m, c in marks)
            assert star_lap == (qp.lap_id if qp.alone else None), r
        # paint the cycled C3 row: the ring is round the lap the cycle names, nothing leaks out
        q._cycle, q._running = (3, 1), True
        q._paint_playing()
        assert t.item(2, QUICKEST_STRIP_COL).data(STRIP_PLAYING_ROLE) == 6
        img, cell = _paint_cell(q.strip_delegate, t, 2, QUICKEST_STRIP_COL)
        for y in range(img.height()):
            for x in range(img.width()):
                if not cell.contains(x, y):
                    assert img.pixel(x, y) == 0, f"painted outside the cell at {x},{y}"
        strip = t.item(2, QUICKEST_STRIP_COL).data(STRIP_ROLE)[0]
        marks = strip_geometry(strip, QRectF(cell)).marks
        centre = next(c for m, c in marks if m.lap_id == 6)
        # a pass NOT level is a hollow ring (shape, not only a grey), a level one a filled dot:
        # the C3 row's isolated ones, read at their centres
        def isolated(m, c):
            return all(o is m or (o_c - c).manhattanLength() > 8 for o, o_c in marks)
        hollow = [c for m, c in marks if not m.level and not m.behind and isolated(m, c)]
        filled = [c for m, c in marks if m.level and isolated(m, c)]
        assert hollow and filled, "the C3 row has isolated dots of both kinds"
        for c in hollow:
            px = QColor.fromRgba(img.pixel(int(c.x()), int(c.y())))
            assert px.alpha() < 64 or px.lightness() < 100, ("a hollow ring's centre", px.name())
        for c in filled:
            px = QColor.fromRgba(img.pixel(int(c.x()), int(c.y())))
            assert px.lightness() > 150, ("a level dot is filled", px.name())
        accent = QColor(theme.C.accent)

        def accent_near(c):
            return any(QColor(img.pixel(int(c.x()) + dx, int(c.y()) + dy)).red() > 200
                       and abs(QColor(img.pixel(int(c.x()) + dx, int(c.y()) + dy)).hue()
                               - accent.hue()) < 12
                       for dx in range(-5, 6) for dy in range(-5, 6))
        assert accent_near(centre), "lap 7 (2 of 6) is ringed"
        far = [c for m, c in marks if abs(c.x() - centre.x()) > 12]
        assert far and not any(accent_near(QPointF(c)) for c in far), "and no other dot"
        # the lone C5 quickest takes the purple; withheld below
        img5, cell5 = _paint_cell(q.strip_delegate, t, 4, QUICKEST_STRIP_COL)
        s5 = t.item(4, QUICKEST_STRIP_COL).data(STRIP_ROLE)[0]
        c9 = next(c for m, c in strip_geometry(s5, QRectF(cell5)).marks if m.lap_id == 9)
        assert QColor(img5.pixel(int(c9.x()), int(c9.y()))).name() == \
            QColor(theme.best_sector_colour()).name()
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_provisional_timing_withholds_the_strips_purple():
    from studio.lap_table import QUICKEST_STRIP_COL, STRIP_ROLE
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900, maximised=True)
        cls = type(win.session)
        saved = cls.timing_verified
        cls.timing_verified = property(lambda _self: False)
        try:
            q.refresh()
            assert all(q.table.item(r, QUICKEST_STRIP_COL).data(STRIP_ROLE)[1] is None
                       for r in range(7))
        finally:
            cls.timing_verified = saved
        q.refresh()
        assert q.table.item(4, QUICKEST_STRIP_COL).data(STRIP_ROLE)[1] == 9
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_playback_tick_never_repaints_the_strips_and_a_new_lap_repaints_one():
    from studio.lap_table import QUICKEST_STRIP_COL
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900, maximised=True)
        view = win.view
        _record_plays(view)
        _cid, _lap, clip = _play(view, 2)
        _settle(0.2)
        before = q.strip_delegate.paints
        assert not q.table.isColumnHidden(QUICKEST_STRIP_COL) and before > 0
        for i in range(60):
            _inside(view, clip, i / 60)
            _APP.processEvents()
        _settle(0.1)
        assert q.strip_delegate.paints == before, (before, q.strip_delegate.paints)
        q.activate(2)                       # the next level lap: its row's strip re-rings
        _settle(0.2)
        assert 1 <= q.strip_delegate.paints - before <= 2, q.strip_delegate.paints - before
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_the_strip_says_its_dots_in_words():
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QHelpEvent

    from studio.lap_table import QUICKEST_STRIP_COL
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900, maximised=True)
        t = q.table
        c5 = t.item(4, QUICKEST_STRIP_COL)
        words = c5.data(Qt.AccessibleTextRole)
        assert words == c5.toolTip() == ("14 counted laps through C5: the quickest alone, the next "
                                         "0.071 s slower; typical 3.61; 1 lap ▼ far slower, off "
                                         "the scale at the right."), words
        assert t.item(1, QUICKEST_STRIP_COL).toolTip().startswith(
            "14 counted laps through C2: 11 level with the quickest (within 0.07 s)")
        assert "fastest" not in " ".join(t.item(r, QUICKEST_STRIP_COL).toolTip().lower()
                                         for r in range(7))
        assert t.item(4, 3).text() == "none · next +0.071", "the row's words are unchanged"
        # a hover names the nearest dot's lap and its own time
        shown = []
        from studio import lap_table
        saved = lap_table.QToolTip.showText
        lap_table.QToolTip.showText = staticmethod(lambda _p, text, *_a: shown.append(text))
        try:
            rect = t.visualRect(t.model().index(4, QUICKEST_STRIP_COL))
            pos = QPoint(rect.left() + 9, rect.center().y())
            ev = QHelpEvent(QHelpEvent.ToolTip, pos, t.viewport().mapToGlobal(pos))
            QApplication.sendEvent(t.viewport(), ev)
        finally:
            lap_table.QToolTip.showText = saved
        assert shown and shown[0].startswith("Lap 10: 3.52 · the quickest\n14 counted laps"), shown
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def _crowded(lay):
    """Pairs of dot centres on one strip closer than a dot's width that no pile owns up to."""
    import math
    out = []
    for i, (m1, c1) in enumerate(lay.marks):
        for m2, c2 in lay.marks[i + 1:]:
            close = math.hypot(c1.x() - c2.x(), c1.y() - c2.y()) < 2 * lay.r - 1e-9
            if close and m1.lap_id not in lay.piles and m2.lap_id not in lay.piles:
                out.append((m1.lap_id, m2.lap_id))
    return out


def _sixty_lap_passes(seed=7, laps=60):
    """A synthetic 60-lap session's Quickest passes (the owner's real sessions run 38-65 laps):
    seven corners, each a tight cluster (σ 0.012-0.08 s) round its own typical."""
    import numpy as np
    rng = np.random.default_rng(seed)
    base = [3.0, 2.6, 2.3, 3.0, 3.5, 2.8, 3.4]
    sig = [0.012, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08]
    cols = [[b + abs(rng.normal(0, s)) for b, s in zip(base, sig, strict=True)]
            for _ in range(laps)]
    return stats.quickest_passes(_grid(cols))


def test_every_dot_has_a_place_or_its_pile_is_said():
    """No two dots on a row drawn closer than a dot's width unless the row's words say a pile is
    there and its hover names the laps. The demo needs no pile at any width it is shown at; a
    60-lap session's tight corners take the smaller pile stack, and what still overflows is said."""
    from PySide6.QtCore import QPointF, QRectF

    from studio.lap_table import (
        QUICKEST_STRIP_COL,
        STRIP_MIN_PX,
        STRIP_PILE_R,
        strip_geometry,
        strip_mark_tip,
        strip_words,
    )
    qp = _demo_session().quickest_passes()
    for w in (STRIP_MIN_PX, 76, 93, 192):
        for p in qp.passes:
            lay = strip_geometry(stats.pass_strip(p, qp.level_s), QRectF(0, 0, w, theme.GRID_ROW_H))
            assert lay.piled == 0 and not _crowded(lay), (w, p.cid, lay.r)
    # a pass exactly the step behind: not level, so a ring clear of the band
    edge = stats.pass_strip(stats.quickest_passes(_grid([[3.000], [3.070], [3.069], [3.3], [3.4]]))
                            .passes[0], PB_PRECISION_S)
    lay = strip_geometry(edge, QRectF(0, 0, 76, theme.GRID_ROW_H))
    c = {m.lap_id: cc for m, cc in lay.marks}
    assert c[1].x() - lay.r >= lay.band.right() - 1e-9 and c[2].x() <= lay.band.right() + 1e-9
    # sixty laps
    many = _sixty_lap_passes()
    radii = set()
    for p in many.passes:
        s = stats.pass_strip(p, many.level_s)
        lay = strip_geometry(s, QRectF(0, 0, 76, theme.GRID_ROW_H))
        radii.add(lay.r)
        assert len(lay.marks) == 60 and not _crowded(lay), (p.cid, _crowded(lay))
        assert all(QRectF(0, 0, 76, theme.GRID_ROW_H).contains(cc) for _m, cc in lay.marks)
        words = strip_words(f"C{p.cid}", p, s, many.level_s, lay.piled)
        assert ("drawn on top of another dot" in words) == bool(lay.piled), words
        for lap, under in lay.piles.items():
            m, cc = next((m, cc) for m, cc in lay.marks if m.lap_id == lap)
            assert under and lay.pile_at(QPointF(cc), lap) == under
            tip = strip_mark_tip(m, p, under)
            assert f"{len(under) + 1} laps here" in tip, tip
    assert STRIP_PILE_R in radii, radii   # sixty laps overflow the first stack at every corner
    # ...and the view says it in the cell's words at the width it has
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900)   # at 1280×800 docked sixty laps' longer words hide it
        saved = win.session.quickest_passes
        win.session.quickest_passes = lambda: many
        try:
            q.refresh()
            _settle(0.1)
            t = q.table
            assert not t.isColumnHidden(QUICKEST_STRIP_COL)
            said = [t.item(r, QUICKEST_STRIP_COL).toolTip() for r in range(7)]
            cell = QRectF(0, 0, t.columnWidth(QUICKEST_STRIP_COL), theme.GRID_ROW_H)
            for r, p in enumerate(many.passes):
                piled = strip_geometry(stats.pass_strip(p, many.level_s), cell).piled
                assert ("drawn on top" in said[r]) == bool(piled), (r, piled, said[r])
                assert t.item(r, QUICKEST_STRIP_COL).data(Qt.AccessibleTextRole) == said[r]
        finally:
            win.session.quickest_passes = saved
    finally:
        _close(win)
        prefs.set_corners_view("lap")


# ------------------------------------------------------------------ the strip's layout, once
# The strip's layout was recomputed on every paint of every cell, and its dot search was O(laps²):
# ~80 ms per repaint of the table at 70 laps, a splitter drag at ~5 fps. It is now laid out once
# per strip and cell size (the delegate's cache, cell-local) and its search looks only at the dots
# within reach on x. Neither may move a pixel.


def _flat(lay):
    """A StripLayout as exact floats (QPointF/QRectF `==` is fuzzy)."""
    return (lay.band.getRect(), lay.typical, lay.r, sorted(lay.piles.items()),
            [(m.lap_id, c.x(), c.y()) for m, c in lay.marks])


def _full_search_strip_geometry(strip, rect):
    """`strip_geometry` as it was before the windowed search: every candidate place tested
    against EVERY dot already placed. The reference the windowed search must equal exactly."""
    import math

    from PySide6.QtCore import QPointF, QRectF

    from studio import lap_table as LT
    pad = theme.SPACE_XS + LT.STRIP_DOT_R + LT.STRIP_RING_PX
    gutter = 2 * LT.STRIP_DOT_R + theme.SPACE_XS if any(m.behind for m in strip.marks) else 0.0
    left, width = rect.left() + pad, max(rect.width() - 2 * pad - gutter, 1.0)
    inset = theme.SPACE_XS
    band = QRectF(left, rect.top() + inset, strip.band_x * width, rect.height() - 2 * inset)
    typical = None if strip.typical_x is None else left + strip.typical_x * width
    mid = rect.center().y()
    order = sorted(range(len(strip.marks)), key=lambda j: (strip.marks[j].x, j))
    for r, stack in ((LT.STRIP_DOT_R, LT.STRIP_STACK), (LT.STRIP_PILE_R, LT.STRIP_PILE_STACK)):
        centres, piles = {}, {}
        for i in order:
            m = strip.marks[i]
            x = left + width + gutter if m.behind else left + m.x * width
            if not m.behind and not m.level:
                x = max(x, band.right() + r)

            def clear(lv, x=x, r=r, centres=centres):
                y = mid + lv * 2 * r
                return min((math.hypot(c.x() - x, c.y() - y) for c in centres.values()),
                           default=math.inf)
            lv = next((lv for lv in stack if clear(lv) >= 2 * r - 1e-9), None)
            if lv is None:
                lv = max(stack, key=clear)
                piles[i] = ()
            centres[i] = QPointF(x, mid + lv * 2 * r)
        lay = LT.StripLayout(band, typical, [(m, centres[i]) for i, m in enumerate(strip.marks)],
                             r, piles)
        if not piles:
            break
    return LT.StripLayout(lay.band, lay.typical, lay.marks, lay.r,
                          {strip.marks[i].lap_id: lay.pile_at(lay.marks[i][1],
                                                              strip.marks[i].lap_id)
                           for i in lay.piles})


def test_the_windowed_dot_search_places_every_dot_where_the_full_search_did():
    """Bit for bit, piles included: the demo, and synthetic 60- and 70-lap sessions (which pile at
    the narrow widths), at the widths the strip takes, at the cell's origin and off it."""
    from PySide6.QtCore import QRectF

    from studio.lap_table import STRIP_MAX_PX, STRIP_MIN_PX, strip_geometry
    sessions = [_demo_session().quickest_passes(), _sixty_lap_passes(),
                _sixty_lap_passes(seed=3, laps=70)]
    piled = 0
    for qps in sessions:
        for p in qps.passes:
            s = stats.pass_strip(p, qps.level_s)
            for w in (STRIP_MIN_PX, 75, 76, 92, 93, 131, STRIP_MAX_PX):
                for rect in (QRectF(0, 0, w, theme.GRID_ROW_H),
                             QRectF(517, 233, w - 1, theme.GRID_ROW_H - 1)):
                    got = strip_geometry(s, rect)
                    assert _flat(got) == _flat(_full_search_strip_geometry(s, rect)), (p.cid, w)
                    piled += got.piled
    assert piled, "the synthetic sessions reach the pile path"


def _strip_cache_is_fresh(q):
    """Every layout the strip delegate holds is of a strip a row shows NOW and equals a fresh
    `strip_geometry` at its size, and each row's painted size is among them."""
    from PySide6.QtCore import QRectF

    from studio.lap_table import QUICKEST_STRIP_COL, STRIP_ROLE, strip_geometry
    t, d = q.table, q.strip_delegate
    strips = [data[0] for r in range(t.rowCount())
              if (data := t.item(r, QUICKEST_STRIP_COL).data(STRIP_ROLE))]
    assert strips
    for s, sizes in d._layouts.values():
        assert any(s is x for x in strips), "a layout of a strip no row shows any more"
        for (w, h), lay in sizes.items():
            assert _flat(lay) == _flat(strip_geometry(s, QRectF(0, 0, w, h))), (w, h)
    grid = 1 if t.showGrid() else 0
    painted = (t.columnWidth(QUICKEST_STRIP_COL) - grid, t.rowHeight(0) - grid)
    for s in strips:
        assert painted in d._layouts[id(s)][1], (painted, list(d._layouts[id(s)][1]))


def _hover_strips(q):
    """A hover over every strip cell (its tip uses the cached layout too); returns the tips."""
    from PySide6.QtCore import QPoint
    from PySide6.QtGui import QHelpEvent

    from studio import lap_table
    from studio.lap_table import QUICKEST_STRIP_COL
    t, shown = q.table, []
    saved = lap_table.QToolTip.showText
    lap_table.QToolTip.showText = staticmethod(lambda _p, text, *_a: shown.append(text))
    try:
        for r in range(t.rowCount()):
            rect = t.visualRect(t.model().index(r, QUICKEST_STRIP_COL))
            pos = QPoint(rect.left() + 9, rect.center().y())
            QApplication.sendEvent(t.viewport(), QHelpEvent(QHelpEvent.ToolTip, pos,
                                                            t.viewport().mapToGlobal(pos)))
    finally:
        lap_table.QToolTip.showText = saved
    return shown


def test_an_unchanged_table_repaints_its_strips_without_laying_them_out_again():
    """The hard gate: repaints and hovers of an unchanged table compute no layout; a new width
    computes each row's once more, and what is held is always a fresh computation's equal."""
    from studio.lap_table import QUICKEST_STRIP_COL
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900)
        t, d = q.table, q.strip_delegate
        assert not t.isColumnHidden(QUICKEST_STRIP_COL)
        t.viewport().repaint()
        _strip_cache_is_fresh(q)
        laid, painted = d.layouts, d.paints
        for _ in range(3):
            t.viewport().repaint()
        assert len(_hover_strips(q)) == t.rowCount() == 7
        q._strip_words_at_width()
        assert d.paints >= painted + 3 * 7, (painted, d.paints)
        assert d.layouts == laid, f"an unchanged table laid out {d.layouts - laid} strips again"
        # a narrower window narrows the strip: each row is laid out at the new width, once
        wide = t.columnWidth(QUICKEST_STRIP_COL)
        q = _strip_table(win, 1280, 800)
        assert t.columnWidth(QUICKEST_STRIP_COL) != wide and not t.isColumnHidden(
            QUICKEST_STRIP_COL)
        t.viewport().repaint()
        _strip_cache_is_fresh(q)
        assert d.layouts > laid
        laid = d.layouts
        t.viewport().repaint()
        _hover_strips(q)
        assert d.layouts == laid
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_resegment_a_line_edit_and_a_reload_lay_the_strips_out_afresh():
    """Every rebuild path drops the layouts of strips no row shows any more, and what each row
    then paints is a fresh computation's equal."""
    from studio.lap_table import QUICKEST_STRIP_COL, STRIP_ROLE
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900, maximised=True)
        t, view, s = q.table, win.view, win.session
        t.viewport().repaint()
        _strip_cache_is_fresh(q)
        before = t.item(2, QUICKEST_STRIP_COL).data(STRIP_ROLE)[0]
        # a re-segment (here: lap 12 left out of the counted laps, as an exclusion does)
        orig = s.consistency_lap_ids
        s.consistency_lap_ids = lambda orig=orig: [i for i in orig() if i != 12]
        try:
            view.rebuild_derived_views()
            _settle()
            assert t.item(2, QUICKEST_STRIP_COL).data(STRIP_ROLE)[0] is not before
            t.viewport().repaint()
            _strip_cache_is_fresh(q)
        finally:
            s.consistency_lap_ids = orig
        # a sector-line edit, through the map's own signal (its sidecar write stubbed), from
        # three lines mid-straight (test_quickest_sectors' placement)
        truth = _DEMO["truth"]
        start, _ = s.timing_lines_latlon()
        assert s.apply_timing_lines_latlon(start, [truth.line_at(sum(truth.circuit.straights[j]) / 2)
                                                   for j in (1, 3, 5)], confirmed=True)
        view.rebuild_derived_views()
        _settle()
        view._save_sidecar = lambda: None
        view.map.timing_lines_changed.emit(s.start_line, list(s.sector_lines)[:2])
        _settle()
        assert q.sectors is not None and t.rowCount() == 10
        t.viewport().repaint()
        _strip_cache_is_fresh(q)
        # a reload: a new view, a new table, its own layouts
        old = win.view
        win._load(list(win._paths))
        deadline = time.time() + 60
        while (win.view is old or win.view is None) and time.time() < deadline:
            _settle(0.02)
        q = _strip_table(win, 1440, 900, maximised=True)
        assert q.strip_delegate is not old.corners_page.quickest.strip_delegate
        q.table.viewport().repaint()
        _strip_cache_is_fresh(q)
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def _render_strip(delegate, table, row, at):
    """Paint strip cell `row` through `delegate` at `at` (a QPoint) with the table's own cell
    size, into a transparent image; returns the cell's pixels."""
    from PySide6.QtCore import QRect
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtWidgets import QStyle, QStyleOptionViewItem

    from studio.lap_table import QUICKEST_STRIP_COL
    grid = 1 if table.showGrid() else 0
    w = table.columnWidth(QUICKEST_STRIP_COL) - grid
    h = table.rowHeight(row) - grid
    img = QImage(at.x() + w, at.y() + h, QImage.Format_ARGB32)
    img.fill(0)
    opt = QStyleOptionViewItem()
    opt.initFrom(table.viewport())
    opt.state &= ~QStyle.State_HasFocus
    opt.rect = QRect(at.x(), at.y(), w, h)
    opt.widget = table
    p = QPainter(img)
    delegate.paint(p, opt, table.model().index(row, QUICKEST_STRIP_COL))
    p.end()
    return img.copy(opt.rect)


def test_a_cached_strip_paints_the_same_pixels_as_one_laid_out_afresh():
    """At 1280×800 and 1440×900 docked: every demo row painted where the table puts it, from the
    cached cell-local layout (twice: the second paint is all cache), equals the uncached path —
    laid out afresh in the cell's own rect in the viewport and drawn untranslated, as every paint
    was before the cache. The ringed lap and the lone quickest's purple included."""
    from PySide6.QtCore import QPoint, QRectF
    from PySide6.QtWidgets import QStyledItemDelegate

    from studio.lap_table import (
        QUICKEST_STRIP_COL,
        STRIP_PLAYING_ROLE,
        STRIP_ROLE,
        _PassStripDelegate,
        paint_group_rule,
        strip_geometry,
    )

    class Fresh(_PassStripDelegate):
        def paint(self, painter, option, index):
            QStyledItemDelegate.paint(self, painter, option, index)
            paint_group_rule(painter, option, index)
            strip, star_lap = index.data(STRIP_ROLE)
            rect = QRectF(option.rect)
            painter.save()
            painter.setClipRect(option.rect)
            self.draw(painter, strip_geometry(strip, rect), rect, star_lap,
                      index.data(STRIP_PLAYING_ROLE))
            painter.restore()

    prefs.set_corners_view("quickest")
    win = _window()
    try:
        for size in ((1280, 800), (1440, 900)):
            q = _strip_table(win, *size)
            t = q.table
            q._cycle, q._running = (3, 1), True    # C3 cycled: its strip rings lap 6
            q._paint_playing()
            assert not t.isColumnHidden(QUICKEST_STRIP_COL)
            fresh = Fresh(t)
            for r in range(t.rowCount()):
                at = t.visualRect(t.model().index(r, QUICKEST_STRIP_COL)).topLeft() + QPoint(3, 5)
                _render_strip(q.strip_delegate, t, r, at)
                laid = q.strip_delegate.layouts
                cached = _render_strip(q.strip_delegate, t, r, at)
                assert q.strip_delegate.layouts == laid
                assert cached == _render_strip(fresh, t, r, at), (size, r)
            q._cycle, q._running = None, False
            q._paint_playing()
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_seventy_laps_repaint_the_table_without_laying_a_strip_out():
    """A 70-lap session (synthetic) at 1440×900: once laid out, a repaint of the whole table lays
    out no strip — on main every repaint redid seven O(laps²) layouts, 65-85 ms. The time bound is
    loose (a loaded CI runner); the counter is the gate."""
    import statistics

    from studio.lap_table import QUICKEST_STRIP_COL, STRIP_ROLE
    many = _sixty_lap_passes(seed=3, laps=70)
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900)
        saved = win.session.quickest_passes
        win.session.quickest_passes = lambda: many
        try:
            q.refresh()
            _settle(0.1)
            t, d = q.table, q.strip_delegate
            assert not t.isColumnHidden(QUICKEST_STRIP_COL)
            assert len(t.item(0, QUICKEST_STRIP_COL).data(STRIP_ROLE)[0].marks) == 70
            t.viewport().repaint()
            _strip_cache_is_fresh(q)
            laid, painted = d.layouts, d.paints
            ms = []
            for _ in range(7):
                _APP.processEvents()
                t0 = time.perf_counter()
                t.viewport().repaint()
                ms.append((time.perf_counter() - t0) * 1e3)
            assert d.paints - painted == 7 * 7 and d.layouts == laid, (d.paints, d.layouts)
            print("70-lap full-table repaint, ms", [round(m, 1) for m in ms])
            assert statistics.median(ms) < 30, ms
        finally:
            win.session.quickest_passes = saved
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_click_on_the_strip_plays_the_row_and_a_tie_is_never_called_the_quickest():
    from PySide6.QtCore import QPoint, QRectF
    from PySide6.QtGui import QHelpEvent

    from studio import lap_table
    from studio.lap_table import QUICKEST_STRIP_COL, STRIP_ROLE, strip_geometry
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        q = _strip_table(win, 1440, 900)
        t = q.table
        plays = _record_plays(win.view)
        _click(t, 2, QUICKEST_STRIP_COL)                 # a REAL click on C3's strip
        assert plays and plays[-1] == win.session.corners.pass_clip(12, 3), plays
        # hover C2's quickest (lap 11): a tie of 11, so "level", never "the quickest"
        rect = t.visualRect(t.model().index(1, QUICKEST_STRIP_COL))
        lay = strip_geometry(t.item(1, QUICKEST_STRIP_COL).data(STRIP_ROLE)[0], QRectF(rect))
        c = next(c for m, c in lay.marks if m.lap_id == 10)
        shown = []
        saved = lap_table.QToolTip.showText
        lap_table.QToolTip.showText = staticmethod(lambda _p, text, *_a: shown.append(text))
        try:
            pos = QPoint(round(c.x()), round(c.y()))
            QApplication.sendEvent(t.viewport(), QHelpEvent(QHelpEvent.ToolTip, pos,
                                                            t.viewport().mapToGlobal(pos)))
        finally:
            lap_table.QToolTip.showText = saved
        assert shown and shown[0].startswith(
            "Lap 11: 2.62 · the lap the Quickest column names; 10 more are level"), shown
        assert "· the quickest" not in shown[0], shown
    finally:
        _close(win)
        prefs.set_corners_view("lap")


# ------------------------------------------------------------------ this pass vs the typical pass
# (cool/compare-this-pass) "What did I do differently?" in one step: the pass being studied (pane
# A) beside the corner's typical pass (pane B) — Coaching's typical, the same `typical_pass_lap` —
# both from the lead-in, playing once, the charts and map on the corner. Offered only where the two
# printed times are not level under the timing step.


def test_the_typical_pass_is_coachings_and_level_pairs_are_not_offered():
    """Qt-free: `Session.pass_vs_typical` picks Coaching's typical lap (the same
    `CornerMatrix.typical_pass`, this lap where the best lap stands); 4 of the demo's 7 quickest
    passes clear the 0.07 s step against it; a lap whose pass IS the typical one says so."""
    s = _demo_session()
    tps = s.typical_passes()
    offered = []
    for q in s.quickest_passes().passes:
        v = s.pass_vs_typical(q.lap_id, q.cid)
        # the lap Coaching's Compare puts in pane A at every corner of the demo
        assert v.typical_lap_id == tps[q.cid].lap_id, (q.cid, v, tps[q.cid])
        assert v.typical_lap_id != q.lap_id and not v.is_typical
        if not v.level:
            offered.append(q.cid)
    assert offered == [1, 3, 5, 7], offered
    c3 = s.pass_vs_typical(12, 3)
    assert (c3.typical_lap_id, round(c3.gap_s, 2)) == (0, 0.08) and not c3.level, c3
    # lap 4's C1 pass is the corner's typical one (Coaching compares it with the best lap's)
    assert tps[1].lap_id == 3 and s.pass_vs_typical(3, 1).is_typical


def test_compare_this_pass_with_the_typical_pass_at_the_corner():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view, s = win.view, win.session
        _record_plays(view)
        seeks = []
        real_seek_pane = view.video.seek_pane

        def seek_pane(side, t):
            seeks.append((side, t))
            real_seek_pane(side, t)
        view.video.seek_pane = seek_pane
        view.select_lap_tab(1)
        _settle()
        q = view.corners_page.quickest
        q.activate(q.row_of(3))                              # lap 13's C3, the quickest
        _settle()
        assert view.pass_subject() == (12, 3, "C3")
        # the right-click menu offers it beside Play / Export
        pos = q.table.visualRect(q.table.model().index(2, 1)).center()
        menu = view.pass_menu(view._quickest_menu_pass(q.table, pos))
        names = [a.text() for a in menu.actions()]
        assert names == ["Play this pass", "Export this pass…", "Compare with my typical pass"]
        assert menu.actions()[2].isEnabled()
        menu.actions()[2].trigger()
        _settle()
        v = s.pass_vs_typical(12, 3)
        assert view._comparing() and (view.compare.lap_a, view.compare.lap_b) == (12, 0)
        assert v.typical_lap_id == s.typical_passes()[3].lap_id, "Coaching's typical pass"
        clip_a, clip_b = s.corners.pass_clip(12, 3), s.corners.pass_clip(0, 3)
        assert seeks[-2:] == [(0, clip_a[0]), (1, clip_b[0])], seeks[-2:]
        assert view.video.pane.clip_end() == clip_a[1]
        assert view.video.secondary.clip_end() == clip_b[1]
        assert view.pass_zoomed() and _corner_framed(view, 3) and not view.map._view_fitted
        # the readouts count from the corner's entry, and the seam says so
        assert view.compare.pass_anchor() == "C3"
        # the pair named for the whole comparison (the status line expires in 6 s)
        assert view.video.seam.caption.text() == "lap 13 (2.32) vs typical lap 1 (2.40) · C3"
        msg = win.statusBar().currentMessage()
        assert msg == (f"Comparing lap 13 ({v.pass_s:.2f}) with your typical pass, lap 1 "
                       f"({v.typical_pass_s:.2f}): 0.08 s quicker through C3 as timed · C or Esc: "
                       "back to lap 13"), msg
        tip = view.video.seam.toolTip()
        assert "the difference between these two passes as timed" in tip, tip
        assert "partly luck" in tip and "Coloured only beyond timing precision" not in tip, tip
        assert "fastest" not in msg.lower()
        assert view.pass_subject() is None, "compare is not a pass: nothing stale to export"
        # Esc leaves it: lap 13 alone, the whole lap and track
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view._comparing() and view.plots.selected_lap_ids() == [12]
        assert view.table.selected_lap_ids() == [12] and not view.pass_zoomed()
        assert view.compare.pass_anchor() is None
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_level_or_typical_passes_are_not_compared_and_say_why():
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        view.select_lap_tab(1)
        _settle()
        q = view.corners_page.quickest
        pos = q.table.visualRect(q.table.model().index(1, 1)).center()      # C2: level
        compare = view.pass_menu(view._quickest_menu_pass(q.table, pos)).actions()[2]
        assert not compare.isEnabled()
        assert compare.toolTip() == ("Lap 11's pass through C2 is within 0.07 s of your typical "
                                     "pass as timed"), compare.toolTip()
        assert not view.compare_pass_with_typical(10, 2) and not view._comparing()
        assert "within 0.07 s of your typical pass" in win.statusBar().currentMessage()
        assert not view.compare_pass_with_typical(3, 1)                     # lap 4 IS C1's typical
        assert "is your typical pass through C1" in win.statusBar().currentMessage()
        # ⌘K: no compare row while a level pass is studied, one while C3's quickest is
        q.activate(q.row_of(2))
        _settle()
        assert not [t for t, _c in win.quickest_pass_commands() if t.startswith("Compare")]
        q.activate(q.row_of(3))
        _settle()
        rows = {t: c for t, c in win.quickest_pass_commands() if t.startswith("Compare")}
        assert list(rows) == ["Compare lap 13 through C3 with typical lap 1"], list(rows)
        rows["Compare lap 13 through C3 with typical lap 1"]()
        _settle()
        assert view._comparing() and (view.compare.lap_a, view.compare.lap_b) == (12, 0)
        # C leaves it (C keeps its meaning): back to lap 13 alone
        win.toggle_compare()
        _settle()
        assert not view._comparing() and view.plots.selected_lap_ids() == [12]
        # a lap pick leaves it and the PICK stands
        rows = {t: c for t, c in win.quickest_pass_commands() if t.startswith("Compare")}
        assert rows == {}, "the pass was left with the compare"
        assert view.compare_pass_with_typical(12, 3)
        view.table.laps_selected.emit([4])
        _settle()
        assert not view._comparing() and view.plots.selected_lap_ids() == [4]
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_coaching_compare_counts_from_the_corner_and_jump_says_how_its_pass_stands():
    win = _window()
    try:
        view, s = win.view, win.session
        tp = s.typical_passes()[1]
        assert view.compare_at_corner(tp, "+0.36 s")
        _settle()
        assert view.compare.pass_anchor() == "C1"
        assert view.video.seam.caption.text() == (
            f"lap 4 ({tp.pass_s:.2f}) vs best lap 13 ({tp.best_pass_s:.2f}) · C1")
        view.video.compare_btn.click()
        _settle()
        # Jump shows the BEST lap's pass, which is not always a good one there: it says how it
        # stands. The demo's C5 (discovery 6: best lap 3.615 vs typical 3.605, quickest 3.522)
        # is LEVEL with the typical at print — 3.61 and 3.61 — not "slower" as the raw figures read;
        # C1's best-lap pass is quicker than the typical one by 0.36 s
        v5 = s.pass_vs_typical(12, 5)
        assert v5.level and win._jump_pass_clause(12, 5, "C5") == (
            f"; its C5 pass ({v5.pass_s:.2f}) is level with your typical pass (lap "
            f"{v5.typical_lap_id + 1}, {v5.typical_pass_s:.2f})")
        v1 = s.pass_vs_typical(12, 1)
        assert v1.gap_s > 0 and not v1.level
        assert win._jump_pass_clause(12, 1, "C1").startswith(
            f"; its C1 pass ({v1.pass_s:.2f}) is quicker than your typical pass (lap 4, ")
        assert win._jump_pass_clause(3, 1, "C1") == (
            f"; its C1 pass ({s.pass_vs_typical(3, 1).pass_s:.2f}) is your typical pass there")
    finally:
        _close(win)


# ---------------------------------------------- review round 1 (#626): the corner readout rule


def _panes_at(view, t_a, t_b):
    """Put the two compare panes at telemetry times (t_a, t_b), as playing panes report them, and
    run the compare tick: (seam text, pane A badge, pane B badge, seam caption)."""
    view.video.pane.current_global_time = lambda: t_a
    view.video.secondary.current_global_time = lambda: t_b
    view.compare._compare_last_t = None
    view.compare.tick()
    v = view.video
    return (v.seam.value.text(), v._cell_a.badge.text(), v._cell_b.badge.text(),
            v.seam.caption.text())


def _edges(s, lap, cid):
    c = s.corners
    return (c.corner_edge_media_time(lap, cid, "enter"), c.corner_edge_media_time(lap, cid, "exit"),
            c.pass_clip(lap, cid))


def _check_corner_readout(view, s, a, b, cid, held, caption, held_neutral=False):
    """The readout rule through one corner compare of lap `a` (pane A) with lap `b` (pane B).
    `held_neutral`: a "Compare with my typical pass", whose held gap is printed as measured but
    never coloured nor arrowed (`PassAnchor.held_neutral`); otherwise coloured as before."""
    from studio import theme
    from studio.library import within_timing_precision
    ea, xa, ca = _edges(s, a, cid)
    eb, xb, cb = _edges(s, b, cid)
    # the lead-in: no number, the corner still named
    seam, ba, bb, cap = _panes_at(view, ca[0] + 0.05, cb[0] + 0.05)
    assert (seam, ba, bb) == ("Δ —", "Δ —", "Δ —"), (seam, ba, bb)
    assert cap == caption, cap
    # the entry: zero, neutral
    seam, ba, bb, _ = _panes_at(view, ea, eb)
    assert "0.00" in seam and "0.00" in ba and "0.00" in bb, (seam, ba, bb)
    # inside: a number
    seam, ba, bb, _ = _panes_at(view, (ea + xa) / 2, (eb + xb) / 2)
    assert "—" not in seam and "—" not in ba, (seam, ba)
    # after the exit (the tail, then both paused at their clip ends): HELD at the passes' printed
    # gap, the gap the status line names; pane B mirrors it
    for ta, tb in ((xa + 0.2, xb + 0.2), (ca[1] + 0.03, cb[1] + 0.05)):
        seam, ba, bb, _ = _panes_at(view, ta, tb)
        colours = (view.video._cell_a._badge_colour, view.video._cell_b._badge_colour,
                   view.video.seam.value.colour())
        if held_neutral:
            assert ba == theme.format_delta_run(held, arrow=False), (ba, held)
            assert bb == theme.format_delta_run(-held, arrow=False), (bb, held)
            assert seam == theme.format_delta_run(held, arrow=False), (seam, held)
            assert colours == (None, None, None), colours
            continue
        assert ba == theme.format_delta_run(held), (ba, held)
        assert bb == theme.format_delta_run(-held), (bb, held)
        arrow = not within_timing_precision(held)
        assert seam == theme.format_delta_run(held, arrow=arrow), (seam, held)
        assert colours[:2] == (theme.delta_colour(held), theme.delta_colour(-held)), colours
    assert view.compare.pass_anchor() == f"C{cid}"
    # a pane scrubbed out of its clip: the whole lap's Δ again, under the plain caption
    t_out = ca[1] + 10.0                         # ten seconds on, still on lap a, far past C#
    seam, ba, _bb, cap = _panes_at(view, t_out, xb + 0.2)
    assert view.compare.pass_anchor() is None
    assert cap == f"lap {a + 1} vs lap {b + 1}", cap
    assert ba == theme.format_delta_run(s.delta_between(a, b, t_out)), ba


def test_the_corner_readout_is_blank_before_the_entry_live_inside_and_held_after():
    """#626 review P1: the anchored readout was subtracted everywhere — before the corner, after
    it, and after a scrub to the other end of the lap ("lap 6 vs lap 9 · from C5 entry −0.67 s ▲"
    in green for a pass 2.84 s SLOWER). The rule (`compare_controller.PassAnchor`), for this
    compare and for Coaching's Compare, which share it."""
    from studio.stats import printed_gap_s
    win = _window()
    try:
        view, s = win.view, win.session
        _record_plays(view)
        assert view.compare_pass_with_typical(5, 5)                   # lap 6's spin at C5
        _settle()
        v = s.pass_vs_typical(5, 5)
        assert -v.gap_s == printed_gap_s(v.pass_s, v.typical_pass_s) == 2.84
        _check_corner_readout(view, s, 5, 8, 5, 2.84,
                              "lap 6 (6.45) vs typical lap 9 (3.61) · C5", held_neutral=True)
        assert not view._comparing() or view.compare.pass_anchor() is None
        view.video.compare_btn.click() if view._comparing() else None
        _settle()
        # the Coaching Compare: the typical lap (pane A) against the best lap (pane B) at C1. Its
        # held gap is neutral too (DECISIONS §21 open item): the best lap's pass on a row ranked
        # by that loss is partly luck — on habit-free seeds 1-8, 8 of the 12 gaps the seam would
        # have coloured were truly under the step (studio/dev/probes/coaching_compare_held_gap)
        tp = s.typical_passes()[1]
        assert view.compare_at_corner(tp, "+0.36 s")
        _settle()
        held = printed_gap_s(tp.pass_s, tp.best_pass_s)
        assert abs(held) >= 0.07, "C1's held gap is past the step: it was coloured before"
        tip = view.video.seam.toolTip()
        assert "as timed" in tip and "your best lap's) is partly luck" in tip, tip
        _check_corner_readout(view, s, tp.lap_id, tp.best_lap_id, 1, held,
                              f"lap 4 ({tp.pass_s:.2f}) vs best lap 13 ({tp.best_pass_s:.2f}) · C1",
                              held_neutral=True)
        # ...while a plain C compare keeps the colour rule
        view.video.compare_btn.click()
        _settle()
        win.toggle_compare()
        _settle()
        assert view._comparing() and view.compare.pass_anchor() is None
        assert "Coloured only beyond timing precision" in view.video.seam.toolTip()
    finally:
        _close(win)


def test_the_held_gap_is_the_status_lines_gap_for_every_offered_pair():
    from studio.stats import printed_gap_s
    win = _window()
    try:
        view, s = win.view, win.session
        _record_plays(view)
        m = s.corner_matrix()
        offered = 0
        for lap in m.lap_ids:
            for cid in m.cids:
                if view.typical_compare_refusal(lap, cid) is not None:
                    continue
                v = s.pass_vs_typical(lap, cid)
                assert view.compare_pass_with_typical(lap, cid)
                held = view.compare._anchor.sides[0][5]
                said = win.statusBar().currentMessage()
                way = "slower" if held > 0 else "quicker"
                assert f": {abs(held):.2f} s {way} through C{cid}" in said, (lap, cid, held, said)
                assert held == printed_gap_s(v.pass_s, v.typical_pass_s) == -v.gap_s
                offered += 1
        assert offered >= 17, offered
    finally:
        _close(win)


def test_leaving_a_corner_compare_any_way_clears_it_and_its_status():
    from studio.app import LOOP_OFF_IN_COMPARE
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        bar = win.statusBar()
        # Esc: the status line goes at once, not 6 s later
        assert view.compare_pass_with_typical(12, 3)
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view._comparing() and bar.currentMessage() == ""
        # L in a corner compare says Loop is off in a compare
        assert view.compare_pass_with_typical(12, 3)
        win.toggle_pass_loop()
        assert bar.currentMessage() == LOOP_OFF_IN_COMPARE
        # a pane repoint is not "this corner" any more (M4): the anchor and the state go
        view.compare.on_pane_repoint(1, 6)
        _settle()
        assert view.compare.pass_anchor() is None and view._corner_compare is None
        assert view.video.seam.caption.text() == "lap 13 vs lap 7"
        view.video.compare_btn.click()
        _settle()
        # left through a Quickest row (compare.exit, no compareToggled): nothing stale stays, so
        # Esc in a LATER plain compare does not "restore" the old lap
        assert view.compare_pass_with_typical(12, 3)
        view.select_lap_tab(1)
        _settle()
        view.corners_page.quickest.activate(4)
        _settle()
        assert not view._comparing() and view._corner_compare is None
        view.table.laps_selected.emit([3])
        _settle()
        win.toggle_compare()
        _settle()
        pair = (view.compare.lap_a, view.compare.lap_b)
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert view._comparing() and (view.compare.lap_a, view.compare.lap_b) == pair
        win.toggle_compare()
        _settle()
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_c_keeps_its_whole_lap_meaning_while_a_pass_is_studied():
    """C and the Compare button are one action: a whole-lap compare from the start line, whatever
    is being studied (#626 review: mutant M8 survived)."""
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view = win.view
        _record_plays(view)
        view.select_lap_tab(1)
        _settle()
        view.corners_page.quickest.activate(2)            # lap 13's C3: offered against typical
        _settle()
        assert view.pass_subject() == (12, 3, "C3")
        win.toggle_compare()
        _settle()
        assert view._comparing() and view.compare.pass_anchor() is None
        assert getattr(view, "_corner_compare", None) is None
        assert view.video.seam.caption.text() == (
            f"lap {view.compare.lap_a + 1} vs lap {view.compare.lap_b + 1}")
        win.toggle_compare()
        _settle()
    finally:
        _close(win)
        prefs.set_corners_view("lap")


# ---------------------------------------------- the 0.17.0 QA: small defects between the features


def test_leaving_a_pass_takes_its_status_line_down_at_once():
    """A played pass's line ("… · Esc: whole lap · ⌘K: export this pass") stayed up to 6 s after
    the pass, its zoom and ⌘K's export row were gone. Every way of leaving it takes the line down
    at once — the mechanism the corner compare's line uses (`CentralView._hold_status`) — and a
    newer message is never wiped by it."""
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view, bar = win.view, win.statusBar()
        _record_plays(view)

        def play():
            v = win.view
            v.select_lap_tab(1)
            _settle()
            q = v.corners_page.quickest
            q.activate(q.row_of(3))
            _settle()
            said = bar.currentMessage()
            assert v.pass_subject() is not None and said.endswith(
                "Esc: whole lap · ⌘K: export this pass"), said
            return said

        def esc():
            QTest.keyClick(win, Qt.Key_Escape)

        def race():
            win._race_action.trigger()

        def jump():
            win._jump_to_opportunity(5, 0.0)

        for name, leave in (("Esc", esc), ("a lap pick", lambda: view.table.laps_selected.emit([4])),
                            ("C", win.toggle_compare), ("a Lap race", race), ("a Jump", jump)):
            said = play()
            leave()
            _settle()
            assert view.pass_subject() is None, name
            assert bar.currentMessage() != said, (name, bar.currentMessage())
            if name == "a Jump":       # the Jump's own line is newer, and stays
                assert bar.currentMessage().startswith("jumped to C5"), bar.currentMessage()
            if view._comparing():
                win.toggle_compare()
            if view.is_racing():
                view.end_lap_race()
            _settle()
        # a re-segment that takes the studied corner away. A real line edit cannot remove a demo
        # corner (they come from the best lap's curvature), and a corner set shrunk under the
        # whole rebuild breaks the tables' row indexing, so this drives the step of the rebuild
        # (`rebuild_derived_views` → `_refresh_quickest`) that re-reads the corners, with C3 gone
        # and the Quickest page's own repaint stubbed: the player still holds the clip, but it is
        # no longer a pass through a corner, and its line goes
        said = play()
        corners, page = win.session.corners, view.corners_page
        real_list, real_refresh = corners.corner_list, page.refresh_quickest
        corners.corner_list = lambda: [c for c in real_list() if c.cid != 3]
        page.refresh_quickest = lambda: None
        try:
            assert view.video.pane.pass_clip() is not None
            view._refresh_quickest()
            _settle()
            assert view.pass_subject() is None
            assert bar.currentMessage() != said, bar.currentMessage()
        finally:
            corners.corner_list, page.refresh_quickest = real_list, real_refresh
        # a newer message is never taken down by the pass it outlived
        said = play()
        bar.showMessage("something else", 6000)
        esc()
        _settle()
        assert bar.currentMessage() == "something else"
        # a reload: the old view's line goes with it
        said = play()
        old = win.view
        win._load([_demo_path()])
        deadline = time.time() + 60
        while (win.view is None or win.view is old) and time.time() < deadline:
            _settle(0.02)
        assert win.view is not None and win.view is not old
        assert bar.currentMessage() != said, bar.currentMessage()
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_a_quickest_walk_ends_on_the_corner_not_on_a_lap():
    """⌥→ from a Quickest row plays each corner's QUICKEST pass, on whichever lap it is: at the
    end it said "the last one with a pass on lap 2", a lap the walk had left long before."""
    prefs.set_corners_view("quickest")
    win = _window()
    try:
        view, bar = win.view, win.statusBar()
        _record_plays(view)
        view.select_lap_tab(1)
        _settle()
        q = view.corners_page.quickest
        q.activate(q.row_of(1))
        _settle()
        assert not view.step_corner_pass(-1)
        assert bar.currentMessage() == "No earlier corner to play: C1 is the first corner"
        for _ in range(6):
            assert view.step_corner_pass(+1)
        assert view.pass_subject()[1] == 7
        assert not view.step_corner_pass(+1)
        said = bar.currentMessage()
        assert said == "No later corner to play: C7 is the last corner", said
        assert "lap" not in said
        # a per-lap walk keeps its lap (test_the_corner_walk_plays_one_pass_per_press)
        assert view.play_lap_pass(3, 7) and not view.step_corner_pass(+1)
        assert bar.currentMessage() == ("No later corner to play: this is the last one with a "
                                        "pass on lap 4")
    finally:
        _close(win)
        prefs.set_corners_view("lap")


def test_esc_leaves_either_corner_compare_and_keeps_its_meaning_in_a_whole_lap_one():
    """Esc left this-pass-vs-typical (its line says "C or Esc") but not a Coaching row's Compare,
    which C leaves. Now Esc leaves any compare AT A CORNER exactly as C does; in a whole-lap compare
    (C, the button) it still does nothing, as on main."""
    win = _window()
    try:
        view, s, bar = win.view, win.session, win.statusBar()
        _record_plays(view)
        tp = s.typical_passes()[1]
        # what C does to a Coaching Compare: one video again, both laps still on the charts
        assert view.compare_at_corner(tp, "+0.36 s")
        _settle()
        win.toggle_compare()
        _settle()
        by_c = (view._comparing(), view.table.selected_lap_ids(), view.plots.selected_lap_ids())
        # ...and Esc does the same, and takes its line down
        assert view.compare_at_corner(tp, "+0.36 s")
        _settle()
        said = bar.currentMessage()
        assert said.startswith("C1 · lap 4") and said.endswith(" · C or Esc: back to one video"), said
        # the Esc order holds: a maximised panel is restored first, the compare stays
        view._toggle_panel_maximized(view._map_panel)
        _settle()
        assert view._maximized_panel is view._map_panel
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert view._maximized_panel is None and view._comparing()
        assert view.compare.pass_anchor() == "C1"
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view._comparing() and view.compare.pass_anchor() is None
        assert (view._comparing(), view.table.selected_lap_ids(),
                view.plots.selected_lap_ids()) == by_c
        assert bar.currentMessage() == ""
        # this pass vs the typical one: Esc leaves it (back to lap 13), as its line says
        assert view.compare_pass_with_typical(12, 3)
        _settle()
        assert "C or Esc: back to lap 13" in bar.currentMessage()
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view._comparing() and view.table.selected_lap_ids() == [12]
        # a whole-lap compare: Esc is not its way out (C is), unchanged
        view.table.laps_selected.emit([3])
        _settle()
        win.toggle_compare()
        _settle()
        assert view._comparing() and view.compare.pass_anchor() is None
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert view._comparing()
        win.toggle_compare()
        _settle()
    finally:
        _close(win)


def test_each_strip_mark_says_what_is_true_of_it_on_a_tie():
    """The hover called every level pass "level with the quickest" — the named lap included, level
    with itself. Each mark now says what is true of IT."""
    from studio.lap_table import strip_mark_tip
    qp = _demo_session().quickest_passes()
    c2 = next(p for p in qp.passes if p.cid == 2)            # lap 11 named, 10 more level
    marks = {m.lap_id: m for m in stats.pass_strip(c2, qp.level_s).marks}
    named = strip_mark_tip(marks[c2.lap_id], c2, (), qp.level_s)
    assert named == (f"Lap 11: {marks[10].time_s:.2f} · the lap the Quickest column names; "
                     f"{len(c2.level_laps)} more are level"), named
    other = c2.level_laps[0]
    tip = strip_mark_tip(marks[other], c2, (), qp.level_s)
    assert tip == (f"Lap {other + 1}: {marks[other].time_s:.2f} · level with lap 11 (within "
                   f"{qp.level_s:.2f} s)"), tip
    assert "Quickest column" not in tip and "quickest" not in tip
    slower = next(m for m in marks.values() if not m.level and not m.behind)
    assert strip_mark_tip(slower, c2, (), qp.level_s).endswith("· slower than the band")
    c5 = next(p for p in qp.passes if p.cid == 5)            # a lone quickest
    m5 = next(m for m in stats.pass_strip(c5, qp.level_s).marks if m.lap_id == c5.lap_id)
    assert strip_mark_tip(m5, c5, (), qp.level_s).endswith("· the quickest")
    # one more level lap: singular
    one = stats.quickest_passes(_grid([[3.000], [3.040], [3.3], [3.4], [3.5]])).passes[0]
    m = next(m for m in stats.pass_strip(one, PB_PRECISION_S).marks if m.lap_id == one.lap_id)
    assert strip_mark_tip(m, one).endswith("; 1 more is level"), strip_mark_tip(m, one)


# ------------------------------------------------------------------ too few laps: say why

def _few_laps_path(laps: int) -> str:
    """A synthetic recording of the demo's circuit with `laps` timed laps (0 or 1): the real
    loader on real GPMF, as the discovery round's "one" / "zero" cases. The stock driver's
    cold-tyre slice needs three laps, so a 0-lap one drives at form 1.0 everywhere."""
    key = f"few{laps}"
    if key not in _DEMO:
        import numpy as np

        from studio.dev import make_demo as md
        from studio.dev import synth_gopro as sg
        _DEMO[key + "dir"] = tempfile.TemporaryDirectory(prefix=f"quickest_{key}_")
        orig = sg._driver

        def flat(rng, circuit, n):
            nearest, grip, brake = orig(rng, circuit, max(n, 3))
            return nearest, np.ones_like(grip[:n]), np.ones_like(brake[:n])
        sg._driver = flat if laps < 3 else orig
        try:
            rec = sg.generate(os.path.join(_DEMO[key + "dir"].name, "rec"), laps=laps,
                              chapters=1, origin=md.DEMO_ORIGIN)
        finally:
            sg._driver = orig
        (_DEMO[key],) = rec.paths
    return _DEMO[key]


def test_the_corner_walk_on_one_lap_says_why_it_has_nothing_to_play():
    """Corner times need a few clean laps, so on a one-lap session NO pass plays. ⌥→ said "No
    later corner to play: this is the last one with a pass on lap 1" — untrue, and the real reason
    thrown away. It now says the refusal's own sentence; a lap with passes keeps its "last one"
    (test_a_quickest_walk_ends_on_the_corner_not_on_a_lap)."""
    win = _window(_few_laps_path(1))
    try:
        view, bar = win.view, win.statusBar()
        assert len(win.session.valid_lap_ids()) == 1
        lap = win.session.lap_at_time(view._playback.applied_t)
        assert lap is not None, "the playhead opens on the one lap"
        first = win.session.corners.corner_list()[0].cid
        for step in (+1, -1):
            assert not view.step_corner_pass(step)
            said = bar.currentMessage()
            assert said == view.lap_pass_refusal(lap, first), said
            assert said.startswith("No corner times to play here:") and "with a pass" not in said
        win._next_corner_action.trigger()                   # ⌥→ / ⌘K, the same words
        assert bar.currentMessage().startswith("No corner times to play here:")
    finally:
        _close(win)


def test_the_corner_walk_with_no_complete_lap_says_so():
    """No complete lap: the corner list is empty too, and the walk said "No corners on this track
    yet" on a known 7-corner track. It says what every other surface says for that state."""
    from studio import data_quality
    win = _window(_few_laps_path(0))
    try:
        assert win.session.valid_lap_ids() == []
        for step in (+1, -1):
            assert not win.view.step_corner_pass(step)
            assert win.statusBar().currentMessage() == data_quality.NO_LAPS_HEADLINE
    finally:
        _close(win)


def test_a_key_on_a_disabled_transport_button_says_its_reason():
    """C (and ⌘K "Toggle compare mode") clicked the Compare button even while it was disabled,
    below two valid laps: nothing happened and nothing was said, the reason living only on the
    button's tooltip. The status line now says that tooltip — one sentence, one source. G, the
    same one-line shape, does the same for a g-meter toggle a recording cannot have."""
    win = _window(_few_laps_path(1))
    try:
        v, bar = win.view.video, win.statusBar()
        assert not v.compare_btn.isEnabled()
        bar.clearMessage()
        QTest.keyClick(win, Qt.Key_C)
        _settle()
        assert not v.compare_btn.isChecked() and not win.view._comparing()
        assert bar.currentMessage() == v.compare_btn.toolTip(), bar.currentMessage()
        assert "needs ≥2 valid laps" in bar.currentMessage()
        bar.clearMessage()
        v.gmeter_btn.setEnabled(False)                       # what a recording with no g gets
        v.gmeter_btn.setToolTip("No accelerometer data in this recording")
        was = v.gmeter_btn.isChecked()
        win.toggle_gmeter()
        assert v.gmeter_btn.isChecked() == was
        assert bar.currentMessage() == "No accelerometer data in this recording"
    finally:
        _close(win)


def test_f_and_its_command_while_comparing_say_why_the_video_cannot_fill_the_screen():
    """F (and ⌘K "Make the video fill the screen") while comparing two laps did nothing and said
    nothing: the ⤢ button is disabled then (compare owns the two-pane stage) and the reason lived
    only on its tooltip. Both now put that tooltip on the status line, as C and G do — one
    sentence, one source — and neither enters video focus."""
    from studio import help_dialog
    win = _window()
    try:
        view, bar = win.view, win.statusBar()
        v = view.video
        v.compare_btn.click()
        _settle()
        assert view._comparing() and not v.fullscreen_btn.isEnabled()
        reason = v.fullscreen_btn.toolTip()
        assert reason == "Make the video fill the screen (F) — not while comparing two laps"
        bar.clearMessage()
        QTest.keyClick(win, Qt.Key_F)
        _settle()
        assert bar.currentMessage() == reason, repr(bar.currentMessage())
        assert not view.is_video_focused() and view._comparing()
        bar.clearMessage()
        row = next(c for c in help_dialog.COMMANDS if c.run == "toggle_video_focus")
        getattr(win, row.run)()                     # what the ⌘K row runs
        _settle()
        assert bar.currentMessage() == reason, repr(bar.currentMessage())
        assert not view.is_video_focused()
        # ...and out of compare F does what it always did, saying nothing
        v.compare_btn.click()
        _settle()
        assert not view._comparing() and v.fullscreen_btn.isEnabled()
        bar.clearMessage()
        QTest.keyClick(win, Qt.Key_F)
        _settle()
        assert view.is_video_focused() and bar.currentMessage() == ""
        QTest.keyClick(win, Qt.Key_F)
        _settle()
        assert not view.is_video_focused()
    finally:
        _close(win)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\nALL {len(tests)} QUICKEST-PASS TESTS PASSED")
