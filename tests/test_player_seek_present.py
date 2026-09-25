"""Every seek the player takes must land AND put its frame on screen — at open, in compare, across a
chapter, and while dragging (QA 2026-09-25, VIEW-1..4).

WHAT THE OWNER SAW (v0.4.1, his four recordings): the video was black on every open, and the first ▶
played from 0:00 of chapter 1 — the paddock — while the table, map and charts showed the ★ best lap;
the UI then fell to "no lap". Compare's pane "LAP B" played the start of the recording. A paused jump
across a chapter left the picture black until ▶, and on the two recordings whose best lap is in
chapter 2 the chip read "loading next chapter…" for 7.5 s after a 0.2 s load. A 1 s scrub drag showed
ONE frame.

WHY — measured on Qt 6.11's FFmpeg backend with a bare QMediaPlayer (the rules `_FFmpegLikePlayer`
below copies, each one observed, not assumed):
  * `setPosition` while the source is LoadingMedia is DROPPED. The open's poster seek and pane B's
    lap-start seek are both issued into a source that has not loaded, so both players sat at 0.
  * a seek in StoppedState moves the position but presents NO frame; `pause()` presents it. Every
    fresh source is StoppedState, so a seek that lands after a load shows nothing until ▶.
  * `setSource` re-emits the OLD source's LoadedMedia from inside the call, source() still naming the
    old file; a switch made while the previous source is itself still loading emits no LoadingMedia
    at all (the backend de-duplicates it) — so a seam gate waiting for LoadingMedia waits for the
    8 s watchdog.
  * the historical premise "pause() on a never-played pane makes the next play() restart from 0" is
    false here: pause-then-seek-then-play started at the seek (`CompareController._reset_pair_to_start`).
  * a paused seek takes 136-410 ms to present on his 4K HEVC (VIEW p9), so a drag that re-seeks every
    ~33 ms tick supersedes each seek before its frame exists.
  * presenting paused builds the FFmpeg engine at LOAD, audio-renderer thread included when an audio
    output is attached; that thread's teardown was sampled deadlocked against the GUI thread
    (_AUDIO_DEADLOCK below), so a muted pane attaches no audio output.

TWO HALVES. The fake-player tests pin the state machine exactly, fast, and fail on the unfixed pane
with the defect named. The real-player tests drive the production widgets over a REAL offscreen
QMediaPlayer on the synthetic GoPro (`studio/dev/synth_gopro.py`: 2 chapters, 59.94 fps, a GoPro
`tmcd` track, the owner's MK timecode) and read the picture where it really goes: the QVideoWidget's
video sink (`videoSink().videoFrameChanged`, each frame's `startTime`). `QWidget.grab()` cannot see a
QVideoWidget's native surface — it shows black whatever is playing — so it would pass or fail blind.
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.pop("PACER_NO_MEDIA", None)          # these tests are about the media player
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QSize, QUrl, Signal  # noqa: E402
from PySide6.QtMultimedia import (  # noqa: E402
    QMediaPlayer,
    QVideoFrame,
    QVideoFrameFormat,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import chapters, player_pane, theme  # noqa: E402
from studio.player_pane import PlayerPane  # noqa: E402

_S = QMediaPlayer.MediaStatus
_P = QMediaPlayer.PlaybackState


# ================================================================== the fake: Qt 6.11 FFmpeg, as measured
class _FFmpegLikePlayer(QObject):
    """A QMediaPlayer double with the FFmpeg backend's MEASURED behaviour (see the module docstring).
    Asynchrony is explicit: `finish_load()` is the backend's async load landing, `present()` the
    decoder finishing a frame, which it pushes into the pane's REAL QVideoSink — so the pane observes
    presentation exactly as it does in production."""

    positionChanged = Signal("qlonglong")
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    durationChanged = Signal("qlonglong")
    PlaybackState = QMediaPlayer.PlaybackState      # player_pane names its enums through the class
    MediaStatus = QMediaPlayer.MediaStatus

    def __init__(self):
        super().__init__()
        self._source = QUrl()
        self._status = _S.NoMedia
        self._state = _P.StoppedState
        self._pos = 0
        self._requested = None       # play/pause asked for while loading: applied after the load
        self._decoding = None        # the position of a frame on its way to the screen
        self._sink = None
        self.landed = []             # every setPosition that LANDED (a dropped one is not here)

    # ---- wiring the pane does
    def setVideoOutput(self, out):
        self._sink = out.videoSink() if out is not None and hasattr(out, "videoSink") else None

    def setAudioOutput(self, _out):
        pass

    def setPlaybackRate(self, rate):
        self._rate = rate

    def playbackRate(self):
        return getattr(self, "_rate", 1.0)

    def source(self):
        return self._source

    def mediaStatus(self):
        return self._status

    def playbackState(self):
        return self._state

    def position(self):
        return self._pos

    # ---- status/state changes are de-duplicated, as QPlatformMediaPlayer does
    def _set_status(self, status):
        if status != self._status:
            self._status = status
            self.mediaStatusChanged.emit(status)

    def _set_state(self, state):
        if state != self._state:
            self._state = state
            self.playbackStateChanged.emit(state)

    # ---- transport
    def setSource(self, url):
        if self._status not in (_S.NoMedia, _S.LoadingMedia):
            # stop() first: StoppedState + a LoadedMedia while source() still names the OLD file
            self._set_state(_P.StoppedState)
            self._set_status(_S.LoadedMedia)
        self._source = url
        self._pos, self._decoding, self._requested = 0, None, None
        if self._sink is not None:
            self._sink.setVideoFrame(QVideoFrame())  # the old picture goes: the black VIEW saw
        self._set_status(_S.LoadingMedia)            # none at all if the old source was loading

    def finish_load(self):
        assert self._status == _S.LoadingMedia, "nothing is loading"
        self._set_status(_S.LoadedMedia)
        requested, self._requested = self._requested, None
        if requested is not None:
            requested()

    def setPosition(self, ms):
        if self._status == _S.LoadingMedia:
            return                                   # dropped
        self._pos = int(ms)
        self.landed.append(self._pos)
        self.positionChanged.emit(self._pos)
        self._set_status(_S.LoadedMedia)
        if self._state != _P.StoppedState:
            self._decoding = self._pos               # a Stopped seek decodes and presents nothing

    def pause(self):
        if self._status == _S.LoadingMedia:
            self._requested = self.pause
            return
        self._set_state(_P.PausedState)
        self._decoding = self._pos                   # pause() presents the frame at the position

    def play(self):
        if self._status == _S.LoadingMedia:
            self._requested = self.play
            return
        self._set_state(_P.PlayingState)
        self._decoding = self._pos

    def stop(self):
        self._set_state(_P.StoppedState)

    # ---- what reaches the screen
    def present(self) -> bool:
        if self._decoding is None or self._sink is None:
            return False
        frame = QVideoFrame(QVideoFrameFormat(QSize(4, 4), QVideoFrameFormat.PixelFormat.Format_BGRA8888))
        frame.setStartTime(self._decoding * 1000)    # µs, local to the source, as the backend's
        self._decoding = None
        self._sink.setVideoFrame(frame)
        return True

    def shown_ms(self):
        frame = self._sink.videoFrame()
        return frame.startTime() // 1000 if frame.isValid() else None


@contextlib.contextmanager
def _ffmpeg_like_backend():
    real = player_pane.QMediaPlayer, player_pane.QAudioOutput
    player_pane.QMediaPlayer, player_pane.QAudioOutput = _FFmpegLikePlayer, player_pane._NullAudioOutput
    try:
        yield
    finally:
        player_pane.QMediaPlayer, player_pane.QAudioOutput = real


def _pane():
    """A freshly-built pane over a two-chapter recording (100 s each, no fitted clock, so global ==
    media) — its first chapter still LOADING, as it is when CentralView posters it."""
    cmap = chapters.ChapterMap(["/nonexistent/GX010000.MP4", "/nonexistent/GX020000.MP4"], [100.0, 100.0])
    with _ffmpeg_like_backend():
        pane = PlayerPane(cmap)
    return pane, pane.player


# ================================================================== the fake-player tests
def test_a_seek_made_while_the_source_loads_lands_on_the_load_and_is_shown():
    """VIEW-1 (MK, SD30): the open's poster seek is issued into a source that is still loading."""
    pane, fake = _pane()
    assert fake.mediaStatus() == _S.LoadingMedia
    pane.seek(30.0)
    fake.finish_load()
    fake.present()
    assert fake.position() == 30_000, (
        f"the seek made while the source loaded was dropped: the player sits at {fake.position()} ms, "
        "so the first ▶ plays from 0:00 (VIEW-1)")
    assert fake.shown_ms() == 30_000, (
        f"the player is at the target but shows {fake.shown_ms()}: a seek in StoppedState presents "
        "nothing — the black video at open (VIEW-1)")
    assert fake.playbackState() == _P.PausedState
    print("test_a_seek_made_while_the_source_loads_lands_on_the_load_and_is_shown OK")


def test_play_after_the_open_starts_at_the_poster_not_at_zero():
    """VIEW-1: the first ▶ after the open plays the best lap the UI is showing."""
    pane, fake = _pane()
    pane.seek(30.0)
    fake.finish_load()
    fake.present()
    pane.play()
    fake.present()
    assert fake.playbackState() == _P.PlayingState
    assert fake.shown_ms() == 30_000, f"▶ started at {fake.shown_ms()} ms, not at the poster (30 000)"
    print("test_play_after_the_open_starts_at_the_poster_not_at_zero OK")


def test_play_pressed_before_the_load_plays_from_the_queued_target():
    """An impatient ▶ during the open's load: it must play from the poster, once the media exists."""
    pane, fake = _pane()
    pane.seek(30.0)
    pane.play()
    fake.finish_load()
    fake.present()
    assert fake.playbackState() == _P.PlayingState, "the ▶ pressed during the load was lost"
    assert fake.shown_ms() == 30_000, f"playing from {fake.shown_ms()} ms, not the poster (30 000)"
    print("test_play_pressed_before_the_load_plays_from_the_queued_target OK")


def test_an_open_seek_into_chapter_two_lands_on_the_load_not_the_watchdog():
    """VIEW-3 (SD19, Sandown 3h): the best lap is in chapter 2, so the open's poster seek switches the
    source while chapter 1 is still loading — and the backend emits no LoadingMedia for that switch."""
    pane, fake = _pane()
    chip = []
    pane.seamLoading.connect(chip.append)
    pane.seek(150.0)                     # chapter 2, 50 s in
    assert chip == [True]
    fake.finish_load()                   # chapter 2's genuine load, with no LoadingMedia before it
    fake.present()
    assert pane._pending is None, (
        "the seam gate ignored chapter 2's genuine LoadedMedia (it waits for a LoadingMedia that never "
        "comes), so only the 8 s watchdog would apply the seek — 7.5 s of 'loading next chapter…' (VIEW-3)")
    assert chip == [True, False], f"the chip must clear on the load, got {chip}"
    assert not pane._seam_watchdog.isActive()
    assert (fake.position(), fake.shown_ms()) == (50_000, 50_000), (fake.position(), fake.shown_ms())
    print("test_an_open_seek_into_chapter_two_lands_on_the_load_not_the_watchdog OK")


def test_a_paused_jump_across_a_chapter_shows_its_frame():
    """VIEW-3: paused in chapter 1, a lap click / scrub lands in chapter 2 — the old source's leftover
    LoadedMedia must not apply it, and the frame must be shown once the new source loads."""
    pane, fake = _pane()
    fake.finish_load()
    fake.pause()                         # he has played and paused once: a Paused, loaded chapter 1
    pane.seek(10.0)
    fake.present()
    assert fake.shown_ms() == 10_000
    pane.seek(150.0)                     # into chapter 2, paused
    assert pane._pending == (1, 50.0, False), (
        f"the OLD source's LoadedMedia (re-emitted inside setSource) consumed the seek: {pane._pending}")
    fake.finish_load()
    fake.present()
    assert fake.position() == 50_000
    assert fake.shown_ms() == 50_000, (
        f"chapter 2 loaded at the target but shows {fake.shown_ms()}: the switch left the player in "
        "StoppedState and nothing paused it (VIEW-3)")
    assert fake.playbackState() == _P.PausedState, "a paused jump must stay paused"
    print("test_a_paused_jump_across_a_chapter_shows_its_frame OK")


def test_a_seek_on_a_loaded_never_played_pane_shows_its_frame():
    """VIEW-1/3: a lap click before the first ▶ — the loaded source is still StoppedState."""
    pane, fake = _pane()
    fake.finish_load()
    pane.seek(20.0)
    fake.present()
    assert fake.shown_ms() == 20_000, (
        f"shows {fake.shown_ms()} after a seek on a never-played pane: StoppedState presents nothing")
    print("test_a_seek_on_a_loaded_never_played_pane_shows_its_frame OK")


def test_a_drag_keeps_one_seek_in_flight_and_the_latest_target_wins():
    """VIEW-4: while a drag's seek decodes, newer targets wait; the newest goes when its frame is
    shown — or when the release timer fires, so a seek that never presents cannot freeze the drag.
    A deliberate seek (the scrub's release, a lap click) drops a target still waiting."""
    pane, fake = _pane()
    fake.finish_load()
    fake.pause()
    fake.present()
    fake.landed.clear()
    pane.seek_dragged(10.0)
    pane.seek_dragged(11.0)
    pane.seek_dragged(12.0)
    assert fake.landed == [10_000], f"a drag seek superseded the one still decoding: {fake.landed}"
    fake.present()                       # 10 s is on screen: the NEWEST held target goes, 11 s never
    assert fake.landed == [10_000, 12_000], fake.landed
    fake.present()
    assert fake.landed == [10_000, 12_000], "nothing was held, so nothing more may be issued"
    assert not pane._frames_watched, (
        "the drag is over, but the pane still takes every presented frame into Python on the GUI "
        "thread — playback's present path (studio/README.md, perf invariant 4)")

    pane.seek_dragged(13.0)              # a seek that presents nothing...
    pane.seek_dragged(14.0)
    deadline = time.monotonic() + 3 * player_pane._DRAG_SEEK_RELEASE_MS / 1000
    while fake.landed[-1] != 14_000 and time.monotonic() < deadline:
        _APP.processEvents()
        time.sleep(0.005)
    assert fake.landed[-2:] == [13_000, 14_000], f"...must not hold the drag forever: {fake.landed}"

    pane.seek_dragged(15.0)              # held behind 14 s
    pane.seek(20.0)                      # the release's final, deliberate seek
    fake.present()
    assert fake.landed[-1] == 20_000 and 15_000 not in fake.landed, (
        f"a stale drag target landed after the deliberate seek: {fake.landed}")
    print("test_a_drag_keeps_one_seek_in_flight_and_the_latest_target_wins OK")


# ================================================================== the real player (synthetic GoPro)
_REAL: dict = {}
_FRAME_TOL_S = 0.05          # one 59.94 fps frame is 16.7 ms; the frame shown for a seek starts at/before it


def _real_fixture() -> dict:
    """A 2-chapter synthetic GoPro (59.94 fps, GoPro timecode) and its Session — built once."""
    if _REAL:
        return _REAL
    from studio.dev import synth_gopro as sg
    from studio.session import Session
    tmp = tempfile.TemporaryDirectory(prefix="pacer-seek-present-")
    rec = sg.generate(os.path.join(tmp.name, "rec"), laps=3, chapters=2, frames_per_payload=60,
                      timecode="10:51:06:25")
    paths = chapters.discover_siblings(rec.paths[0])
    assert paths == rec.paths, (paths, rec.paths)
    session = Session.load(paths)
    assert len(session.chapters) == 2 and len(session.valid_lap_ids()) >= 2, (
        len(session.chapters), session.valid_lap_ids())
    _REAL.update(tmp=tmp, paths=paths, session=session)
    return _REAL


def _pump_until(pred, timeout_s: float) -> bool:
    end = time.monotonic() + timeout_s
    while time.monotonic() < end:
        _APP.processEvents()
        if pred():
            return True
        time.sleep(0.005)
    return bool(pred())


class _Picture:
    """Every frame a pane's video sink receives, as GLOBAL media seconds (chapter offset + the frame's
    own start time) — the picture itself, not the player's claim about its position.

    It starts from the frame ALREADY on the sink, because that is on screen too. A software decoder
    shows a fresh pane's paused frame ~40 ms after its load — compare's pane B had shown lap B's
    start (media 53.971 for 53.983) 44 ms after the click, inside the one processEvents() before the
    test could hook that brand-new pane, and a recorder counting only LATER frames failed CI, which
    has no VideoToolbox, with "pane B never showed lap 1's start". VideoToolbox took 361 ms."""

    def __init__(self, pane):
        self.pane = pane
        self.frames: list[float] = []
        sink = pane.video.videoSink()
        sink.videoFrameChanged.connect(self._on_frame)
        self._on_frame(sink.videoFrame())   # invalid (nothing shown yet) is ignored

    def _on_frame(self, frame):
        if frame.isValid():
            self.frames.append(self.pane._offset() + frame.startTime() / 1e6)

    def shows(self, media_s: float) -> bool:
        return any(abs(f - media_s) <= _FRAME_TOL_S for f in self.frames)


def _media(session, t: float) -> float:
    return float(session.media_clock.to_media(t))


def test_real_player_open_shows_the_best_lap_and_play_starts_there():
    """The whole owner-visible flow on a real decoder: open (CentralView's best-lap poster), ▶, compare
    (pane B at lap B), then a paused jump into the other chapter."""
    from studio.central_view import CentralView
    fx = _real_fixture()
    s = fx["session"]
    view = CentralView(s, fx["paths"], sidecar_path=None)
    view.resize(1280, 800)
    view.show()
    try:
        pane = view.video.pane
        picture = _Picture(pane)
        best = s.best_lap_id()
        target = s.lap_window(best)[0] + theme.LAP_SEEK_NUDGE_S
        m_best = _media(s, target)
        t0 = time.monotonic()
        shown = _pump_until(lambda: picture.shows(m_best), 10.0)
        assert shown, (
            f"open: the best lap's frame (media {m_best:.3f} s, chapter "
            f"{s.chapters.to_local(m_best)[0] + 1}) never reached the screen — frames {picture.frames[:5]}, "
            f"player {pane.player.position()} ms {pane.player.playbackState()} (VIEW-1/3)")
        print(f"  open: best lap {best + 1} (chapter {pane.current_chapter() + 1}) shown after "
              f"{1e3 * (time.monotonic() - t0):.0f} ms")
        assert view.chapter_label.text() != "loading next chapter…", "the seam chip outlived the load"

        picture.frames.clear()
        view.video.play()
        _pump_until(lambda: len(picture.frames) >= 10, 5.0)
        view.video.pause()
        view.tick()
        assert picture.frames, "▶ presented nothing"
        assert abs(picture.frames[0] - m_best) <= 0.1, (
            f"▶ started at media {picture.frames[0]:.3f} s, not at the best lap ({m_best:.3f} s) (VIEW-1)")
        assert s.lap_at_time(view._playback.applied_t) == best, (
            f"after ▶ the UI is in lap {s.lap_at_time(view._playback.applied_t)}, not the best {best}")

        # ---- compare: pane B is a NEW player whose lap-start seek is issued while it loads (VIEW-2)
        view.video.compare_btn.click()
        _APP.processEvents()
        sec = view.video.secondary
        assert view.compare.active and sec is not None
        assert sec.player.audioOutput() is None, (
            "pane B (always muted) has an audio output: its FFmpeg audio-renderer thread's teardown "
            "on leaving compare can deadlock against the GUI thread (see _AUDIO_DEADLOCK below)")
        picture_b = _Picture(sec)
        lap_b = view.compare.lap_b
        m_b = _media(s, s.lap_window(lap_b)[0] + theme.LAP_SEEK_NUDGE_S)
        assert _pump_until(lambda: picture_b.shows(m_b), 10.0), (
            f"compare: pane B never showed lap {lap_b + 1}'s start (media {m_b:.3f} s) — frames "
            f"{picture_b.frames[:5]}, player {sec.player.position()} ms (VIEW-2)")
        picture_b.frames.clear()
        view.video.play()
        _pump_until(lambda: len(picture_b.frames) >= 10, 5.0)
        view.video.pause()
        assert picture_b.frames and abs(picture_b.frames[0] - m_b) <= 0.1, (
            f"compare ▶: pane B started at {picture_b.frames[:1]}, not lap B ({m_b:.3f} s) (VIEW-2)")
        view.video.compare_btn.click()
        _APP.processEvents()
        assert not view.compare.active

        # ---- a paused jump into the OTHER chapter (VIEW-3)
        here = pane.current_chapter()
        other = 1 - here
        ch = s.chapters.chapters[other]
        m_jump = ch.offset + 0.5 * ch.duration
        t_jump = float(s.media_clock.to_telemetry(m_jump))
        assert not view.video.is_playing()
        picture.frames.clear()
        view.video.seek(t_jump)
        assert _pump_until(lambda: picture.shows(m_jump), 10.0), (
            f"a paused jump from chapter {here + 1} into chapter {other + 1} showed nothing — frames "
            f"{picture.frames[:5]}, {pane.player.playbackState()} (VIEW-3)")
        assert not view.video.is_playing(), "a paused jump must stay paused"
    finally:
        view.video.stop_all()
        view.deleteLater()
        _APP.processEvents()
    print("test_real_player_open_shows_the_best_lap_and_play_starts_there OK")


def test_real_player_fresh_pane_seeks_land_in_either_chapter():
    """The two open shapes on a real decoder, whichever chapter the synthetic best lap falls in: a
    seek into the loading chapter (MK, SD30), and into the OTHER chapter while the first still loads
    (SD19, Sandown 3h) — shown, and the seam chip cleared by the load, not by the 8 s watchdog."""
    s = _real_fixture()["session"]
    for chapter in (0, 1):
        ch = s.chapters.chapters[chapter]
        m = ch.offset + 0.4 * ch.duration
        pane = PlayerPane(s.chapters)
        pane.resize(320, 180)
        pane.show()
        try:
            picture = _Picture(pane)
            chip = []
            pane.seamLoading.connect(chip.append)
            t0 = time.monotonic()
            pane.seek(float(s.media_clock.to_telemetry(m)))
            assert _pump_until(lambda p=picture, m=m: p.shows(m), 10.0), (
                f"a seek into chapter {chapter + 1} made while the pane loaded never showed its frame "
                f"(media {m:.3f} s): frames {picture.frames[:5]}, player {pane.player.position()} ms "
                f"{pane.player.playbackState()}")
            took = time.monotonic() - t0
            if chapter == 1:
                assert chip[:1] == [True] and chip[-1] is False, chip
                assert took < 4.0, f"chapter 2 took {took:.1f} s: the seam gate waited for the watchdog"
            print(f"  fresh pane, chapter {chapter + 1}: shown after {1e3 * took:.0f} ms")
            if chapter == 0:
                # _AUDIO_DEADLOCK. Presenting paused builds the FFmpeg engine at load, so an attached
                # QAudioOutput would give every pane an audio-renderer thread from the open. Its
                # teardown (a chapter switch, leaving compare) disconnects from the Python-made
                # QAudioOutput, whose PySide disconnectNotify waits for the GIL under Qt's
                # signal-slot lock — sampled deadlocked against a GUI-thread connect on MK. So a
                # muted pane carries no audio output; un-muting attaches it and playback goes on.
                assert pane.player.audioOutput() is None, "a muted pane must not attach its audio"
                pane.set_muted(False)
                assert pane.player.audioOutput() is pane.audio and not pane.is_muted()
                n = len(picture.frames)
                pane.play()
                assert _pump_until(lambda p=picture, n=n: len(p.frames) >= n + 5, 5.0), (
                    "playback stalled after the first un-mute attached the audio output")
                pane.pause()
        finally:
            pane.dispose()
            pane.deleteLater()
            _APP.processEvents()
    print("test_real_player_fresh_pane_seeks_land_in_either_chapter OK")


if __name__ == "__main__":
    test_a_seek_made_while_the_source_loads_lands_on_the_load_and_is_shown()
    test_play_after_the_open_starts_at_the_poster_not_at_zero()
    test_play_pressed_before_the_load_plays_from_the_queued_target()
    test_an_open_seek_into_chapter_two_lands_on_the_load_not_the_watchdog()
    test_a_paused_jump_across_a_chapter_shows_its_frame()
    test_a_seek_on_a_loaded_never_played_pane_shows_its_frame()
    test_a_drag_keeps_one_seek_in_flight_and_the_latest_target_wins()
    test_real_player_open_shows_the_best_lap_and_play_starts_there()
    test_real_player_fresh_pane_seeks_land_in_either_chapter()
    print("test_player_seek_present: all OK")
