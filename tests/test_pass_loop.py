"""Loop a corner pass and watch it in slow motion.

After v0.16.0 a Corners ▸ Quickest passes row plays its pass (lead-in → 1 s after the exit) once
and pauses at its end. A driver studying a corner wants it again, and slower, without re-clicking.
So while a pass is what the video plays, the transport shows two toggles: Loop (L) replays the pass
from its lead-in each time it ends, and Slow plays it at 0.25×, giving back the exact rate it
replaced when the pass ends. These tests pin:

  * the player (`PlayerPane`, a stand-in QMediaPlayer): the loop rewinds once per lap of the loop,
    across a chapter seam too; Loop turned on after the pass ended (or while paused on it); a
    paused loop resumes on ▶; every way of leaving the pass ends the loop, and ▶ without Loop
    carries on with the lap as before;
  * the real window over the demo (PACER_NO_MEDIA): the toggles come and go with the pass; Slow's
    rate and its exact restore; a speed the driver picks himself is his; Loop and Slow are light
    switches that stay on for any row picked next; a lap pick, compare, the Lap race and ←/→ end
    the loop; the Lap race starts at his own speed; the row says "· looping" only while Loop is
    on; the toggles move nothing already on the bar; L, its status line with no pass, the ? card
    and ⌘K. And a pass running past the recording's end still loops.

The loop never moves on to another pass or lap (refused-2026-09 §31: no reel). Measured on the
demo's real QMediaPlayer in the PR, not here: CI's software-decode world cannot time frames.
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

from studio import prefs, theme  # noqa: E402

theme.apply_theme(_APP)

# ------------------------------------------------------------------ the player

_PLAYING = QMediaPlayer.PlaybackState.PlayingState


class _Player:
    """Enough of a QMediaPlayer to watch the pane play, pause, seek and switch its source."""

    def __init__(self):
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.positions: list[int] = []
        self.url = None

    def play(self):
        self.state = _PLAYING

    def pause(self):
        self.state = QMediaPlayer.PlaybackState.PausedState

    def playbackState(self):
        return self.state

    def setPosition(self, ms):
        self.positions.append(int(ms))

    def setPlaybackRate(self, _rate):
        pass

    def setSource(self, url):
        self.url = url

    def duration(self):
        return getattr(self, "duration_ms", 0)

    def source(self):
        return self.url


def _pane(chapter_map=None):
    from studio.player_pane import PlayerPane
    pane = PlayerPane(chapter_map)
    pane.player = _Player()
    return pane


def _playing_pass(start=10.0, end=15.0):
    """A pane playing the pass [start, end], as VideoView.play_clip leaves it."""
    pane = _pane()
    pane.seek(start)
    pane.play()
    pane.set_clip(start, end)
    return pane


def test_the_loop_goes_back_to_the_start_once_per_lap_of_it():
    pane = _playing_pass()
    assert pane.set_loop(True) and pane.is_looping()
    for lap in range(10):
        pane._on_position(14_000)
        pane._on_position(15_000)                  # the end: back to the start, still playing
        assert pane.player.positions[-1] == 10_000 and pane.is_playing(), lap
        pane._on_position(15_020)                  # a late report from before the seek landed
        pane._on_position(10_040)                  # ...then the start
    # one rewind per lap, no drift: ten times exactly to the clip's own start
    assert pane.player.positions == [10_000] * 11, pane.player.positions
    assert pane.pass_clip() == (10.0, 15.0) and pane.clip_end() == 15.0


def test_without_loop_the_pass_pauses_at_its_end_and_stays_the_subject():
    pane = _playing_pass()
    passes = []
    pane.passChanged.connect(passes.append)
    pane._on_position(15_000)
    assert not pane.is_playing() and pane.clip_end() is None
    assert pane.pass_clip() == (10.0, 15.0) and passes == [], "parked on its end: still the pass"


def test_loop_after_the_pass_ended_replays_it_from_the_lead_in():
    pane = _playing_pass()
    started = []
    pane.clipStarted.connect(lambda: started.append(True))
    pane._on_position(15_000)                      # the clip's own pause at its end
    pane.set_loop(True)
    assert pane.player.positions[-1] == 10_000 and pane.is_playing()
    assert pane.clip_end() == 15.0 and started == [True]


def test_loop_while_paused_on_the_pass_plays_on_from_there():
    pane = _playing_pass()
    pane._on_position(12_500)
    pane.pause()                                   # paused on a frame of the pass
    assert pane.pass_clip() is not None
    seeks = len(pane.player.positions)
    pane.set_loop(True)
    assert pane.is_playing() and len(pane.player.positions) == seeks, "no jump back"


def test_a_paused_loop_resumes_on_play_and_keeps_looping():
    pane = _playing_pass()
    pane.set_loop(True)
    pane.pause()
    assert pane.is_looping() and pane.pass_clip() is not None and pane.clip_end() is None
    pane.play()
    assert pane.is_playing() and pane.clip_end() == 15.0 and pane.is_looping()
    pane._on_position(15_000)
    assert pane.player.positions[-1] == 10_000


def test_no_pass_no_loop():
    pane = _pane()
    assert not pane.set_loop(True) and not pane.is_looping()


def test_every_way_of_leaving_the_pass_ends_the_loop():
    """A scrub, the slider, ←/→, a lap pick (all seek), a drag, compare's lap window, the Lap race
    (end_pass), and ▶ when Loop is off."""
    for act in ("seek", "seek_dragged", "lap_window", "end_pass"):
        pane = _playing_pass()
        pane.set_loop(True)
        loops, passes = [], []
        pane.loopChanged.connect(loops.append)
        pane.passChanged.connect(passes.append)
        if act == "seek":
            pane.seek(30.0)
        elif act == "seek_dragged":
            pane.seek_dragged(12.0)
        elif act == "lap_window":
            pane.set_lap_window(0.0, 50.0)
        else:
            pane.end_pass()
        assert not pane.is_looping() and pane.pass_clip() is None, act
        assert loops == [False] and passes == [False], (act, loops, passes)
        pane.player.play()
        pane._on_position(15_000)
        assert pane.is_playing(), f"{act}: nothing loops or pauses any more"
    pane = _playing_pass()
    pane.pause()
    pane.play()                                    # ▶ without Loop: carry on with the lap
    assert pane.pass_clip() is None and not pane.set_loop(True)


def test_a_pass_at_the_end_of_the_recording_loops_instead_of_sitting_at_its_end():
    """"1 s after the exit" can run past the last frame: the end is clamped inside the media,
    and if the recording still runs out first, EndOfMedia comes round the loop too."""
    from PySide6.QtCore import QUrl

    from studio import chapters
    a = os.path.abspath("GXEND.MP4")
    pane = _pane(chapters.ChapterMap([a], [20.0]))
    pane.player.setSource(QUrl.fromLocalFile(a))
    pane.player.duration_ms = 19_950               # the player's own last frame, a bit short
    pane.seek(17.0)
    pane.play()
    pane.set_clip(17.0, 21.0)                      # runs 1 s past the recording
    start, end = pane.pass_clip()
    assert start == 17.0 and 19.94 < end < 19.95, end
    pane.set_loop(True)
    pane._on_position(19_949)                      # the clamped end: round again
    assert pane.player.positions[-1] == 17_000 and pane.is_playing()
    pane._on_position(17_100)
    pane.player.pause()                            # the media ran out before the end arrived
    pane._on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert pane.player.positions[-1] == 17_000 and pane.is_playing() and pane.is_looping()
    assert pane.player.positions.count(17_000) == 3


def test_a_pass_across_the_chapter_seam_loops_back_into_the_first_chapter():
    from PySide6.QtCore import QUrl

    from studio import chapters
    a, b = os.path.abspath("GXA.MP4"), os.path.abspath("GXB.MP4")
    seam = 100.0
    pane = _pane(chapters.ChapterMap([a, b], [seam, 60.0]))
    pane.player.setSource(QUrl.fromLocalFile(a))   # what the pane's own first load opened
    start, end = seam - 2.0, seam + 3.0           # lead-in in chapter 1, exit in chapter 2
    pane.seek(start)
    pane.play()
    pane.set_clip(start, end)
    pane.set_loop(True)
    loaded, loading = (QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.LoadingMedia)
    for lap in range(3):
        pane._on_position(1_000)                   # chapter 1, past the start
        pane._on_media_status(QMediaPlayer.MediaStatus.EndOfMedia)   # the seam: on to chapter 2
        assert pane.current_chapter() == 1
        pane._on_media_status(loading)
        pane._on_media_status(loaded)
        assert pane.is_playing() and pane.player.positions[-1] == 0
        pane._on_position(3_000)                   # chapter 2's 3.0 s = the pass's end
        # back to the lead-in, in chapter 1: a source switch, resumed playing when it loads
        assert pane.current_chapter() == 0 and pane._pending == (0, start, True), lap
        pane._on_media_status(loading)
        pane._on_media_status(loaded)
        assert pane.player.positions[-1] == round(start * 1000) and pane.is_playing(), lap
        assert pane.is_looping() and pane.pass_clip() == (start, end)


# ------------------------------------------------------------------ the demo, real window

_DEMO: dict = {}


def _demo_path() -> str:
    if "path" not in _DEMO:
        from studio.dev import make_demo as md
        _DEMO["dir"] = tempfile.TemporaryDirectory(prefix="pass_loop_demo_")
        _DEMO["path"] = os.path.join(_DEMO["dir"].name, "GX019000.MP4")
        md.make(_DEMO["path"], render=False)
    return _DEMO["path"]


def _settle(seconds=0.1):
    end = time.time() + seconds
    while time.time() < end:
        _APP.processEvents()
        time.sleep(0.005)


def _window():
    from studio.app import StudioWindow
    for k in ("critical", "warning", "information"):
        setattr(QMessageBox, k, staticmethod(lambda *a, **_kw: QMessageBox.Ok))
    prefs.set_corners_view("quickest")
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
    win.view.select_lap_tab(1)
    _settle()
    return win


def _close(win):
    win.close()
    win.deleteLater()
    _APP.processEvents()
    prefs.set_corners_view("lap")


def _toggles(video):
    return video.loop_btn.isVisible(), video.slow_btn.isVisible()


def test_the_toggles_come_and_go_with_the_pass():
    win = _window()
    try:
        video, q = win.view.video, win.view.corners_page.quickest
        assert _toggles(video) == (False, False), "nothing to loop before a pass"
        q.activate(q.row_of(3))
        _settle()
        assert _toggles(video) == (True, True)
        assert video.pane.pass_clip() == win.session.corners.pass_clip(12, 3)
        video.pane.pause()                         # paused on it: still the pass
        assert _toggles(video) == (True, True)
        video.seek(5.0)                            # a seek of his own: gone
        assert _toggles(video) == (False, False)
    finally:
        _close(win)


def test_slow_plays_the_pass_at_a_quarter_and_gives_back_the_exact_rate():
    win = _window()
    try:
        video, q = win.view.video, win.view.corners_page.quickest
        video.set_playback_rate(2.0)
        q.activate(q.row_of(3))
        video.slow_btn.click()
        assert video.playback_rate() == 0.25 and video.rate_combo.currentText() == "0.25×"
        assert video.pane.playback_rate() == 0.25
        video.slow_btn.click()                     # off: back to 2×
        assert video.playback_rate() == 2.0 and video.rate_combo.currentText() == "2×"
        video.slow_btn.click()
        video.seek(5.0)                            # leaving the pass gives it back too
        assert video.playback_rate() == 2.0 and not video.slow_btn.isChecked()
        # a speed he picks himself while Slow is on is his: nothing is restored over it
        q.activate(q.row_of(5))
        video.slow_btn.click()
        video.rate_combo.setCurrentIndex(1)        # 0.5×, by hand
        assert not video.slow_btn.isChecked() and video.playback_rate() == 0.5
        video.seek(5.0)
        assert video.playback_rate() == 0.5
        q.activate(q.row_of(5))
        video.slow_btn.click()
        video.step_playback_rate(+1)               # ] while Slow is on
        assert not video.slow_btn.isChecked() and video.playback_rate() == 0.5
    finally:
        _close(win)


def test_loop_and_slow_are_light_switches_across_every_row():
    """Loop and Slow stay on for the next pass picked — the same row again (the next level lap)
    or another corner's row — and the row that is playing says "· looping"; only leaving the
    pass turns them off and gives the speed back. Nothing advances by itself (§31)."""
    win = _window()
    try:
        s = win.session
        video, q = win.view.video, win.view.corners_page.quickest
        row = q.row_of(3)
        q.activate(row)
        video.loop_btn.click()
        video.slow_btn.click()
        assert video.pane.is_looping() and video.playback_rate() == 0.25
        assert q.table.item(row, 3).text() == "playing lap 13 (2.32) · 1 of 6 · looping"
        q.activate(row)                            # again: the next level lap, still looped, slow
        _settle()
        assert video.pane.pass_clip() == s.corners.pass_clip(6, 3)
        assert video.pane.is_looping() and video.loop_btn.isChecked()
        assert video.playback_rate() == 0.25 and video.slow_btn.isChecked()
        assert q.table.item(row, 3).text() == "playing lap 7 (2.35) · 2 of 6 · looping"
        row4 = q.row_of(4)
        q.activate(row4)                           # another corner: looped and slow too
        _settle()
        assert video.pane.pass_clip() == s.corners.pass_clip(7, 4)
        assert video.pane.is_looping() and video.loop_btn.isChecked()
        assert video.playback_rate() == 0.25 and video.slow_btn.isChecked()
        assert q.table.item(row4, 3).text() == "playing lap 8 (3.02) · 1 of 8 · looping"
        assert "looping" not in q.table.item(row, 3).text()
        video.seek(5.0)                            # leaving the pass: both off, 1× back
        assert not video.pane.is_looping() and not video.loop_btn.isChecked()
        assert video.playback_rate() == 1.0 and not video.slow_btn.isChecked()
    finally:
        _close(win)


def test_the_row_stops_saying_looping_when_loop_goes_off_or_the_pass_is_left():
    win = _window()
    try:
        video, q = win.view.video, win.view.corners_page.quickest
        row = q.row_of(3)
        q.activate(row)
        QTest.keyClick(win, Qt.Key_L)
        assert q.table.item(row, 3).text().endswith(" · looping")
        QTest.keyClick(win, Qt.Key_L)              # Loop off: the pass plays on, not looped
        assert q.table.item(row, 3).text() == "playing lap 13 (2.32) · 1 of 6"
        QTest.keyClick(win, Qt.Key_L)
        video.seek(5.0)                            # left: no "looping" either
        assert "looping" not in q.table.item(row, 3).text()
    finally:
        _close(win)


def test_the_toggles_take_the_free_space_and_move_nothing():
    """The pass toggles sit right of the stretch: every control already on the bar keeps its x
    when they appear, at 1280×800 and at the window's minimum width, with the demo's timecode and
    with a three-hour recording's (the widest the readout gets)."""
    win = _window()
    try:
        video, q = win.view.video, win.view.corners_page.quickest
        fixed = (video.play_btn, video.mute_btn, video.rate_combo, video.readout,
                 video.gmeter_btn, video.compare_btn, video.fullscreen_btn)
        for size, clock in (((1280, 800), None), ((win.minimumWidth(), 800), None),
                            ((1280, 800), "178:12.000"), ((win.minimumWidth(), 800), "178:12.000")):
            video.seek(5.0)
            win.resize(*size)
            if clock:
                video.set_readout(clock)
            _settle(0.2)
            before = [w.mapTo(win, w.rect().topLeft()).x() for w in fixed]
            q.activate(q.row_of(3))
            if clock:
                video.set_readout(clock)
            _settle(0.2)
            assert _toggles(video) == (True, True)
            during = [w.mapTo(win, w.rect().topLeft()).x() for w in fixed]
            assert during == before, (size, before, during)
            for b in (video.loop_btn, video.slow_btn):
                x = b.mapTo(win, b.rect().topLeft()).x()
                assert video.readout.mapTo(win, video.readout.rect().topRight()).x() < x \
                    < video.gmeter_btn.mapTo(win, video.gmeter_btn.rect().topLeft()).x(), size
        assert video.slow_btn.text() == "Slow"
        assert video.loop_btn.accessibleName() and video.slow_btn.accessibleName()
    finally:
        _close(win)


def test_the_lap_race_starts_at_his_own_speed_not_at_slow():
    win = _window()
    try:
        view = win.view
        video, q = view.video, view.corners_page.quickest
        video.set_playback_rate(2.0)
        q.activate(q.row_of(3))
        video.slow_btn.click()
        assert video.playback_rate() == 0.25
        win.toggle_lap_race()
        _settle()
        assert view.is_racing()
        assert video.playback_rate() == 2.0 and view.race.speed == 2.0, view.race.speed
        assert video.pane.pass_clip() is None and not video.slow_btn.isChecked()
        view.end_lap_race()
    finally:
        _close(win)


def test_a_lap_pick_compare_the_lap_race_and_a_step_end_the_loop():
    win = _window()
    try:
        view = win.view
        video, q = view.video, view.corners_page.quickest
        for leave in ("lap pick", "compare", "lap race", "step"):
            q.activate(q.row_of(3))
            video.loop_btn.click()
            assert video.pane.is_looping(), leave
            if leave == "lap pick":
                view.table.laps_selected.emit([3])
            elif leave == "compare":
                video.compare_btn.click()
            elif leave == "lap race":
                assert view.start_lap_race(), "the demo could not start a Lap race"
            else:
                QTest.keyClick(win, Qt.Key_Right)
            _settle()
            assert not video.pane.is_looping() and video.pane.pass_clip() is None, leave
            assert _toggles(video) == (False, False), leave
            if view.is_racing():
                view.end_lap_race()
            if view._comparing():
                view.compare.exit()
            _settle()
    finally:
        _close(win)


def test_the_l_key_and_its_line_with_no_pass():
    from studio.app import NO_PASS_TO_LOOP
    win = _window()
    try:
        video, q = win.view.video, win.view.corners_page.quickest
        QTest.keyClick(win, Qt.Key_L)
        assert win.statusBar().currentMessage() == NO_PASS_TO_LOOP
        assert not video.loop_btn.isChecked()
        q.activate(q.row_of(3))
        QTest.keyClick(win, Qt.Key_L)
        assert video.pane.is_looping() and video.loop_btn.isChecked()
        QTest.keyClick(win, Qt.Key_L)
        assert not video.pane.is_looping() and not video.loop_btn.isChecked()
        assert video.pane.pass_clip() is not None, "Loop off: the pass plays out, still the pass"
    finally:
        _close(win)


def test_the_card_and_cmd_k_offer_loop():
    from studio import command_palette, help_dialog
    rows = [c for c in help_dialog.COMMANDS if c.run == "toggle_pass_loop"]
    assert len(rows) == 1 and rows[0].key == "L" and rows[0].group == "Playback"
    win = _window()
    try:
        entry = next(e for e in command_palette.entries(win) if e.title == rows[0].title)
        assert entry.key == "L" and entry.enabled
        win.view.corners_page.quickest.activate(0)
        entry.run()
        assert win.view.video.pane.is_looping()
    finally:
        _close(win)


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("ok", t.__name__)
    print(f"\nALL {len(tests)} PASS-LOOP TESTS PASSED")
