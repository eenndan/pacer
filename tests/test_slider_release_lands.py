"""The transport slider lands where it is let go, after any gesture (QA 2026-09-26 round 3, JOURNEY-2).

WHAT THE JOURNEY SHOWED. Jump to C1, play the best lap un-muted, scrub with the chart cursor, then
drag the transport handle while the video plays. On SD19 (GX010068) he let go at 2191.392 s and at
2177.696 s, and the video resumed at ~2246.0 s both times.

WHAT IT WAS, measured with the real player on SD19 with every seek-path call logged (qa3b probes):
  * NOT a stale target. The release landed that drag's own last pointer target every time
    (`PlayerPane._drag_held` == the last `sliderMoved` value). "The same 2246.0 s both times" was the
    pointer: the probe began its +120 px drag where its chart scrub always parked (1837 s), so it let
    go over the same pixel. Without the chart scrub it landed at 2218.6 s, 81.9 s past the handle.
  * The HANDLE was wrong. While it was held, every playhead report still went into the slider
    (`VideoView._on_pane_position` -> `QSlider.setValue`, which moves the handle too), and #422's
    drag keeps one seek in flight with the newest target held behind it, so the playhead runs several
    moves behind the pointer. A report after the last move left the handle on the lagging playhead:
    13.7-81.9 s behind the pointer on SD19, up to 102.5 s off on MK, 99.4 s on Sandown 3h (that one
    muted). Un-muted (#419) it is likely: the clock reports on through a seek, 33-39 times in a 1.5 s
    drag against 8 muted. The release then landed, correctly, at the pointer: "a minute past where
    he let go".

THE RULE PINNED HERE: a held handle belongs to the pointer. A playhead report moves the telemetry,
not the handle, and the release lands the handle's value (+-1 frame), whatever gesture came before:
chart scrub, map drag, slider drag, lap click, Jump, compare enter and exit. After each gesture the
panes hold no drag target and no pending seek, so nothing earlier can be replayed by the release.

A STILL HOLD (QA 2026-09-28, REG2-2). Once a drag's last seek had landed, nothing was held, so the
release had nothing to land, and a playing picture ran on under a still pointer: 1.21 s past the
handle on MK after a hold (1.30 s in compare, 1.42 s after a chart scrub; the excess was the hold
time). The handle now takes the chart scrub's contract: the press pauses both panes, and the release
resumes iff they were playing at the press, unless the handle sits at lap A's end in compare.

The fake is test_player_seek_present's `_FFmpegLikePlayer` (Qt 6.11's FFmpeg backend as measured),
plus the behaviours this needs: `_report`, a playhead report arriving while the handle is held (a
playing pane reports while a seek's frame is still decoding, as the real one does un-muted), and
`_clock`, a playing clock running on. The window is test_central_view_realqt's
`_studiowindow_with_view`: the real StudioWindow and CentralView over the synthetic two-lap session,
so every gesture runs through its production handler.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pyqtgraph as pg  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# Both fixtures live in sibling test modules. The window fixture's module sets PACER_NO_MEDIA=1 at
# import (its panes are inert); these panes are the FFmpeg-like fake, so the flag goes again below.
from test_central_view_realqt import _studiowindow_with_view  # noqa: E402
from test_player_seek_present import _ffmpeg_like_backend  # noqa: E402

from studio import chapters  # noqa: E402
from studio.video_view import PaneSpec, VideoView  # noqa: E402

os.environ.pop("PACER_NO_MEDIA", None)

_FRAME_S = 1 / 59.94             # one frame of his 59.94 fps footage
_PLAYING = QMediaPlayer.PlaybackState.PlayingState
_LOADING = QMediaPlayer.MediaStatus.LoadingMedia


def _report(fake, ms: int) -> None:
    """A playhead report while the handle is held, `ms` past the in-flight seek, in any state: an
    un-muted clock runs on through a seek, and it is what put the held handle back on the lagging
    playhead (#438)."""
    fake._pos += int(ms)
    fake.positionChanged.emit(fake._pos)


def _clock(fake, ms: int) -> None:
    """The playing clock runs on by `ms`."""
    assert fake.playbackState() == _PLAYING, "only a playing pane reports on its own"
    _report(fake, ms)


def _hold_still(panes, s: float = 1.0) -> None:
    """The pointer holds still for `s`: every pane still playing runs on (what main did)."""
    for pane in panes:
        if pane.is_playing():
            _clock(pane.player, int(s * 1000))


def _present_all(panes) -> None:
    """Every frame on its way reaches the screen, which also lets a held drag target go."""
    for _ in range(8):
        for pane in panes:
            pane.player.present()
        _APP.processEvents()


def _fake_view(n_chapters: int = 2) -> VideoView:
    """A VideoView over the fake, its first chapter loaded and presented, paused (global == media)."""
    cmap = chapters.ChapterMap([f"/nonexistent/GX0{i + 1}0000.MP4" for i in range(n_chapters)],
                               [100.0] * n_chapters)
    with _ffmpeg_like_backend():
        view = VideoView(cmap)
    fake = view.pane.player
    fake.finish_load()
    fake.pause()
    fake.present()
    return view


def _settle(view) -> None:
    """Let the last gesture finish as the real player would: every load lands, every frame on its way
    reaches the screen (which lets a held drag target go), and the tick drains."""
    for _ in range(40):
        busy = False
        for pane in view.video._panes():
            if pane.player.mediaStatus() == _LOADING:
                pane.player.finish_load()
                busy = True
            busy = pane.player.present() or busy
        _APP.processEvents()
        view.tick()
        if not busy:
            return
    raise AssertionError("the panes never settled: a drag seek keeps re-issuing itself")


def _assert_nothing_stale(view, after: str) -> None:
    """No gesture may leave a target behind for a later release to replay."""
    for side, pane in enumerate(view.video._panes()):
        assert pane._drag_held is None and pane._pending is None and not pane._drag_inflight, (
            f"after {after}, pane {'AB'[side]} still holds a target: held={pane._drag_held} "
            f"pending={pane._pending} in-flight={pane._drag_inflight}")
    assert not view.scrub.is_active, f"after {after}, the chart scrub is still active"


def _landed(pane) -> float:
    """Where the pane's last seek landed, on the global clock (the fake's landed setPositions are local)."""
    return pane._offset() + pane.player.landed[-1] / 1000.0


def _placed(pane, t: float) -> float:
    """Where the pane places a seek to `t`, on the global clock: through its own chapter map, which
    clamps to the recording (the synthetic session's map ends before its second lap does, so pane B's
    targets there land on the map's last millisecond)."""
    index, local = pane._chapters.to_local(pane._clock().to_media(t))
    return pane._chapters.chapters[index].offset + int(local * 1000) / 1000.0


def _release_lands_on_the_handle(view, after: str, playing: bool, hold: bool = False) -> list[str]:
    """Drag the handle over three targets — the first seek in flight, the newer ones held behind it —
    let the playhead report once, release, and return how each pane missed the handle (none: []).
    `hold` (REG2-2): every drag frame lands first, then the pointer holds still for a second."""
    v = view.video
    sl = v.slider
    if playing != v.is_playing():
        v.toggle()
    assert v.is_playing() == playing
    lo, hi = sl.minimum(), sl.maximum()
    # away from wherever the handle is, so every move is a seek
    fracs = (0.25, 0.4, 0.55) if sl.value() > (lo + hi) / 2 else (0.75, 0.6, 0.45)
    targets = [lo + int((hi - lo) * f) for f in fracs]
    b_targets: list[float] = []
    if v.secondary is not None:
        real_b = v.seek_pane_dragged
        v.seek_pane_dragged = lambda side, t: (b_targets.append(t), real_b(side, t))
    how = f"after {after}, {'playing' if playing else 'paused'}{', held still' if hold else ''}"
    missed = []
    try:
        sl.setSliderDown(True)               # what QSlider's press on the handle does...
        for ms in targets:
            sl.setSliderPosition(ms)         # ...and each pointer move
        assert v.pane._drag_held is not None, "the first seek is decoding; the newer targets wait"
        if playing:
            _report(v.pane.player, 50)       # un-muted, the clock reports on from the in-flight seek
        handle = sl.value() / 1000.0
        if abs(handle - targets[-1] / 1000.0) > _FRAME_S:
            missed.append(f"{how}: the held handle read {handle:.3f} s with the pointer at "
                          f"{targets[-1] / 1000:.3f} s")
        if hold:
            _settle(view)                    # the drag's frames all land: nothing is held any more
            if any(p.is_playing() for p in v._panes()):
                missed.append(f"{how}: the picture played on under the held handle")
            _hold_still(v._panes())
        sl.setSliderDown(False)              # the release
        _settle(view)
    finally:
        if v.secondary is not None:
            del v.seek_pane_dragged
    if v.is_playing() != playing:
        missed.append(f"{how}: {'paused' if playing else 'playing'} after the release")
    got = _landed(v.pane)
    if abs(got - handle) > _FRAME_S:
        missed.append(f"{how}: let go at {handle:.3f} s, landed at {got:.3f} s")
    if v.secondary is not None:
        assert b_targets, "compare: pane B was not dragged along"
        got_b, want_b = _landed(v.secondary), _placed(v.secondary, b_targets[-1])
        if abs(got_b - want_b) > _FRAME_S:
            missed.append(f"{how}: pane B landed at {got_b:.3f} s, not at its distance-locked "
                          f"{b_targets[-1]:.3f} s (placed at {want_b:.3f} s)")
    for side, pane in enumerate(v._panes()):
        # the picture itself, against where its last seek landed (one clock for both sides)
        ran = pane.current_global_time() - pane._clock().to_telemetry(_landed(pane))
        if abs(ran) > _FRAME_S:
            missed.append(f"{how}: pane {'AB'[side]}'s picture is {ran:+.3f} s from its last seek")
    if v.is_playing():
        v.toggle()
    _settle(view)
    _assert_nothing_stale(view, f"{after} + a handle drag")
    return missed


# ------------------------------------------------------------------ the gestures, each through its handler
def _chart_scrub(win, view):
    line = view.plots.cur_speed
    x0, x1 = view.plots.p_speed.viewRange()[0]
    for f in (0.3, 0.4, 0.5, 0.6):
        line.setValue(x0 + (x1 - x0) * f)
        line.sigDragged.emit(line)            # PlotsView -> scrubStarted / scrubMoved
        view.tick()                           # the coalesced drag seek
    line.sigPositionChangeFinished.emit(line)  # -> scrubEnded: the final, deliberate seek


def _map_drag(win, view):
    s = view.session
    lap = s.lap_at_time(view.video.pane.current_global_time())
    w0, w1 = s.lap_window(lap if lap is not None else s.best_lap_id())
    for f in (0.2, 0.45, 0.7):
        i = s.index_at_time(w0 + (w1 - w0) * f)
        view.map.marker.setPos(pg.Point(float(s.tx[i]), float(s.ty[i])))   # the user's marker drag
        view.tick()                           # drains one marker seek (seek_dragged); no final seek


def _slider_drag(win, view):
    sl = view.video.slider
    lo, hi = sl.minimum(), sl.maximum()
    sl.setSliderDown(True)
    for f in (0.8, 0.65, 0.55):
        sl.setSliderPosition(lo + int((hi - lo) * f))
    sl.setSliderDown(False)                   # released with targets still held


def _lap_click(win, view):
    view._on_user_select([view.session.valid_lap_ids()[-1]])   # a genuine table click (F1 seek)


def _jump(win, view):
    win._jump_to_opportunity(list(view.corner_table._cids)[0], 0.0)


def _compare_toggle(win, view):
    view.video.compare_btn.click()            # enter; pane B's lap-start seek waits for its load


_GESTURES = (
    ("the open", None),
    ("a chart scrub", _chart_scrub),
    ("a map drag", _map_drag),
    ("a handle drag", _slider_drag),
    ("a lap click", _lap_click),
    ("a Jump", _jump),
    ("entering compare", _compare_toggle),
    ("a chart scrub in compare", _chart_scrub),
    ("a handle drag in compare", _slider_drag),
    ("leaving compare", _compare_toggle),
)


# ------------------------------------------------------------------ tests
def test_a_playhead_report_leaves_the_held_handle_under_the_pointer():
    """JOURNEY-2 in one pane: playing, the drag's first seek still decoding and two newer targets held,
    the clock reports once. On main that report moved the handle back onto the playhead, and the
    release landed at the pointer, 4 s past the handle."""
    view = _fake_view()
    fake, sl = view.pane.player, view.slider
    view.play()
    sl.setSliderDown(True)
    for ms in (10_000, 12_000, 14_000):
        sl.setSliderPosition(ms)
    _report(fake, 50)
    assert (sl.value(), sl.sliderPosition()) == (14_000, 14_000), (
        f"a playhead report moved the held handle to {sl.value()} ms; the pointer is at 14000 "
        "(JOURNEY-2)")
    assert abs(view.pane.current_global_time() - 10.05) < 1e-6, (
        "the telemetry must still follow the picture while the handle is held")
    sl.setSliderDown(False)
    assert fake.landed[-1] == 14_000, f"the release landed {fake.landed[-1]} ms, not the handle's 14000"
    _clock(fake, 50)
    assert sl.value() == 14_050, "once let go, the handle follows the playhead again"
    view.stop_all()
    print("test_a_playhead_report_leaves_the_held_handle_under_the_pointer OK")


def test_a_drag_across_a_chapter_seam_lands_on_the_handle():
    """Sandown 3h has three chapters: let go in the next chapter while the in-flight seek still plays
    in this one — the release switches the source and lands the handle's value on the load."""
    view = _fake_view()
    pane, fake, sl = view.pane, view.pane.player, view.slider
    view.seek(90.0)
    fake.present()
    view.play()
    sl.setSliderDown(True)
    for ms in (95_000, 130_000, 140_000):     # chapter 1 in flight, then two targets in chapter 2
        sl.setSliderPosition(ms)
    _report(fake, 50)
    handle = sl.value() / 1000.0
    sl.setSliderDown(False)
    fake.finish_load()                        # chapter 2's load lands the deferred seek
    fake.present()
    assert pane.current_chapter() == 1, f"still in chapter {pane.current_chapter() + 1}"
    assert abs(_landed(pane) - handle) <= _FRAME_S, (
        f"let go at {handle:.3f} s across the seam, landed at {_landed(pane):.3f} s")
    assert pane.is_playing(), "a playing drag must go on playing from where it was let go"
    view.stop_all()
    print("test_a_drag_across_a_chapter_seam_lands_on_the_handle OK")


def test_a_still_hold_while_playing_lands_on_the_handle():
    """REG2-2 in one pane: the drag's frames all land (nothing is held), then the pointer holds still
    for a second. On main the picture played on under the handle and the release, which lands only a
    HELD target, left it there: 1.000 s past the handle. Space pressed during the hold decides what
    the release does, and must not start the picture under the held handle either."""
    for start_playing, space in ((True, False), (False, True)):
        view = _fake_view()
        pane, sl = view.pane, view.slider
        if start_playing:
            view.play()
        how = "playing" if start_playing else "paused, then Space while held"
        sl.setSliderDown(True)
        for ms in (10_000, 12_000, 14_000):
            sl.setSliderPosition(ms)
        _present_all([pane])
        assert pane._drag_held is None and not pane._drag_inflight, "every drag frame has landed"
        if space:
            view.toggle()                     # the Space key (StudioWindow.toggle_playback)
        played_while_held = pane.is_playing()
        _hold_still([pane])
        handle = sl.value() / 1000.0
        sl.setSliderDown(False)
        _present_all([pane])
        off = pane.current_global_time() - handle
        assert abs(off) <= _FRAME_S, (
            f"{how}: held still at {handle:.3f} s for 1 s; the release left the picture {off:+.3f} s "
            "from the handle (REG2-2)")
        assert not played_while_held, f"{how}: the picture must wait under a held handle"
        assert pane.is_playing(), f"{how}: the release must play on from the handle"
        view.stop_all()
    print("test_a_still_hold_while_playing_lands_on_the_handle OK")


def test_a_compare_release_at_the_lap_end_stays_parked():
    """Compare, playing, the handle let go at lap A's end: both panes stay parked on their laps' last
    frames, as playing into the end leaves them. Resuming there would rewind them to their lap
    starts (PlayerPane.play re-rolls a pane parked at its window end). The rule reads the HANDLE,
    since pane A's own clock can still lag an in-flight seek; pane B needs no rule of its own, because
    under the normalized distance lock its end coincides with A's."""
    view = _fake_view()
    with _ffmpeg_like_backend():
        view.set_compare(PaneSpec(0, (10.0, 20.0), "A", choices=[0]),
                         PaneSpec(1, (30.0, 40.0), "B", choices=[1]))
    view.set_compare_seek_fanout(lambda t, dragged=False: (
        view.seek_pane_dragged if dragged else view.seek_pane)(1, t + 20.0))
    panes = view._panes()
    view.secondary.player.finish_load()
    for side, pane in enumerate(panes):
        pane.seek(12.0 + 20.0 * side)
    view.play()
    _present_all(panes)
    assert all(p.is_playing() for p in panes)
    sl = view.slider
    sl.setSliderDown(True)
    for ms in (15_000, 18_000, sl.maximum()):
        sl.setSliderPosition(ms)
    _present_all(panes)
    sl.setSliderDown(False)
    _present_all(panes)
    for side, (pane, end) in enumerate(zip(panes, (20.0, 40.0), strict=True)):
        got = pane.current_global_time()
        assert abs(got - end) <= _FRAME_S and not pane.is_playing(), (
            f"pane {'AB'[side]}: let go at its lap end {end:.3f} s, it is at {got:.3f} s and "
            f"{'playing' if pane.is_playing() else 'paused'}")
    view.stop_all()
    print("test_a_compare_release_at_the_lap_end_stays_parked OK")


def test_after_each_gesture_the_release_lands_on_the_handle():
    """The journey's gestures, each through its production handler, then a handle drag playing, one
    playing and held still (REG2-2), and one paused: nothing stale survives a gesture, and each release
    lands on the handle (+-1 frame), pane B on its distance-locked target. Before #438 every PLAYING
    release missed, after each of the ten; before REG2-2's fix every held-still one did."""
    missed: list[str] = []
    with _ffmpeg_like_backend():
        win, view = _studiowindow_with_view()
        try:
            win._tick_timer.stop()            # the test ticks by hand, so every drain is ordered
            _settle(view)
            for after, gesture in _GESTURES:
                if gesture is not None:
                    gesture(win, view)
                    _settle(view)
                _assert_nothing_stale(view, after)
                for playing, hold in ((True, False), (True, True), (False, False)):
                    missed += _release_lands_on_the_handle(view, after, playing, hold)
            assert not view.compare.active, "the sequence ends out of compare"
        finally:
            view.dispose()
            win.hide()
    assert not missed, (f"{len(missed)} releases missed the handle (JOURNEY-2: a playhead report moved "
                        "the held handle back to the lagging playhead):\n  " + "\n  ".join(missed))
    print("test_after_each_gesture_the_release_lands_on_the_handle OK")


def test_a_step_key_waits_while_the_handle_is_held():
    """The arrow keys step from the PLAYHEAD. While the handle is held the pointer owns the transport:
    a step then queued a target the handle did not show, and the release landed it."""
    view = _fake_view(1)
    fake, sl = view.pane.player, view.slider
    sl.setSliderDown(True)
    sl.setSliderPosition(40_000)              # in flight
    sl.setSliderPosition(50_000)              # held
    view.step(+1.0)                           # the → key, mid-drag
    sl.setSliderDown(False)
    fake.present()
    fake.present()
    assert fake.landed[-1] == 50_000, f"landed {fake.landed[-1]} ms; the handle was let go at 50000"
    view.step(+1.0)                           # let go, the key steps again
    assert fake.landed[-1] == 51_000, fake.landed
    view.stop_all()
    print("test_a_step_key_waits_while_the_handle_is_held OK")


if __name__ == "__main__":
    test_a_playhead_report_leaves_the_held_handle_under_the_pointer()
    test_a_drag_across_a_chapter_seam_lands_on_the_handle()
    test_a_still_hold_while_playing_lands_on_the_handle()
    test_a_compare_release_at_the_lap_end_stays_parked()
    test_after_each_gesture_the_release_lands_on_the_handle()
    test_a_step_key_waits_while_the_handle_is_held()
    print("test_slider_release_lands: all OK")
