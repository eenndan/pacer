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
  * the player's one-shot clip end: it pauses there, and any seek / play / pause drops it.
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
        md.make(_DEMO["path"], render=False)
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


def _window():
    from studio.app import StudioWindow
    for k in ("critical", "warning", "information"):
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: QMessageBox.Ok))
    win = StudioWindow([_demo_path()])
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
    """Replace the player's clip call with a recorder: (start, end) per play."""
    plays = []
    view.video.play_clip = lambda start, end: plays.append((start, end))
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
        assert not t.verticalScrollBar().isVisible() and not t.horizontalScrollBar().isVisible()
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
        # the clip pauses itself at its end: the zoom STAYS
        view.video.clipEnded.emit()
        _settle()
        _inside(view, clip, 1.0)
        assert _corner_framed(view, 1) and not view.map._view_fitted
        # play on past the clip → whole lap, whole track
        view._playback.latest_t = clip[1] + 1.0
        view.tick()
        assert _whole_lap(view) and _whole_track(view, fitted)
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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\nALL {len(tests)} QUICKEST-PASS TESTS PASSED")
