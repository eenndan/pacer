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
        assert view.compare.corner_anchor() == "C3"
        assert view.video.seam.caption.text() == "lap 13 vs lap 1 · from C3 entry"
        msg = win.statusBar().currentMessage()
        assert msg == (f"Comparing lap 13 ({v.pass_s:.2f}) with your typical pass, lap 1 "
                       f"({v.typical_pass_s:.2f}): 0.08 s quicker through C3 · C or Esc: back to "
                       "lap 13"), msg
        assert "fastest" not in msg.lower()
        assert view.pass_subject() is None, "compare is not a pass: nothing stale to export"
        # Esc leaves it: lap 13 alone, the whole lap and track
        QTest.keyClick(win, Qt.Key_Escape)
        _settle()
        assert not view._comparing() and view.plots.selected_lap_ids() == [12]
        assert view.table.selected_lap_ids() == [12] and not view.pass_zoomed()
        assert view.compare.corner_anchor() is None
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
        assert compare.toolTip() == ("Lap 11's pass through C2 is level with your typical pass "
                                     "(within 0.07 s): nothing to tell apart"), compare.toolTip()
        assert not view.compare_pass_with_typical(10, 2) and not view._comparing()
        assert "level with your typical pass" in win.statusBar().currentMessage()
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
        assert view.compare.corner_anchor() == "C1"
        assert view.video.seam.caption.text().endswith("· from C1 entry")
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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\nALL {len(tests)} QUICKEST-PASS TESTS PASSED")
