"""PlayerPane: one self-contained single-lap video player.

Owns one QMediaPlayer + QVideoWidget + QAudioOutput plus a g-meter overlay, and presents a
(possibly chaptered) recording as ONE continuous video on a GLOBAL session clock:
  * `positionChanged(global_s)` emits global time (local + the current chapter's offset), so the
    telemetry sync sees one clock.
  * `seek(global_t)` maps to (chapter, local); a different chapter switches the source first.
  * EndOfMedia auto-advances to the next chapter (play from 0).
A single file is a one-chapter map at offset 0, so global == local.

The friction-circle g-meter is the CANONICAL home of the top-level-window overlay trick: it's drawn
as a frameless TOP-LEVEL window (not a child) because on macOS the window-server composites a child
BEHIND the QVideoWidget's native video surface, so it never shows. The pane pins it to the video's
top-right corner in global screen coords and keeps it there via several hooks (see the SEVEN hooks
below + the per-tick re-pin in set_g, since a top-level window doesn't follow the parent on an
app-window drag).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QFontMetrics, QPainter, QPen
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import chapters, media_clock, theme
from .gmeter_overlay import GMeterOverlay

# The g-meter overlay sits in the TOP-RIGHT corner of the video, sized as a FRACTION of the
# video widget (not a fixed size) so it scales with the window and never dominates the frame.
_OVERLAY_FRAC = 0.22        # target width = this fraction of the video width
# height / width. A touch taller than wide because the card is the dial plus the band under it
# that holds the |g| readout — the title strip and the ring of cardinal numbers this used to name
# are both gone (see gmeter_overlay, review §6.5). The RATIO is unchanged: GMeterOverlay still
# floors itself at 120x140, and the redesign spends the room it freed on a bigger dial rather than
# on a differently-shaped card.
_OVERLAY_ASPECT = 1.12
_OVERLAY_MIN_W = 120        # don't shrink below something legible
_OVERLAY_MAX_W = 240        # don't grow huge on a very wide window
_OVERLAY_PAD = 12           # px inset from the video corner

# Lap-end stop tolerance (s): position is ms-quantized and frames ~16 ms apart, so clamp within this
# sub-frame of the window end (else it can step from just-before to just-after without reporting it).
_WINDOW_STOP_TOL_S = 0.002

# Budget after a cross-chapter switch before the watchdog force-applies the deferred seek+resume, so
# a disk/recording hiccup at a seam can never leave the player hung.
_SEAM_RESUME_WATCHDOG_MS = 8000

# A DRAG keeps one seek in flight (seek_dragged). The next target goes when the in-flight seek's frame
# reaches the screen, or after this long if none does (a seek that presents nothing — mid-load, the
# headless player — must not freeze the drag). Well above the slowest paused seek measured, because a
# timer that fires first supersedes a seek about to land — the failure this exists to end: up to
# 693 ms between drag frames on Sandown 3h's 4K HEVC (P-A p9), 460-560 ms on the synthetic GoPro.
_DRAG_SEEK_RELEASE_MS = 1000


@dataclass(frozen=True)
class ReadoutLines:
    """What the live readout over a fullscreen video says — text only, every run already formatted
    by the surface it mirrors (see CentralView._push_live_lap / _set_diff_text):

      * `lap`   — "LAP 7   0:23.415" (the export strip's label: lap, then the lap clock), or "No lap";
                  `lap_dim` when lap timing is provisional (the Laps table's muted rows);
      * `delta` — the hero readout's Δ run, its level rule and colour (`delta_colour`, None = neutral)
                  applied by the hero's own `_level_readout`; `tail` — its speed or "whole lap";
      * `pass_` — during a played pass: "C3 · lap 7 (2.35)", "· looping" while Loop is on."""

    lap: str = ""
    lap_dim: bool = False
    delta: str = ""
    delta_colour: str | None = None
    tail: str = ""
    pass_: str = ""


class LiveReadout(QWidget):
    """The live readout's WINDOW: numbers only, over the video while it fills the screen.

    A frameless top-level window for the g-meter's reason (module docstring): a child is
    composited BEHIND the native video surface on macOS. Pinned to the video's TOP-LEFT corner,
    opposite the g-meter's top-right: on a helmet or kart GoPro the track ahead is the middle of
    the frame and the bottom is the steering wheel, hands and nose — what slow motion is for —
    while the top corners are sky, trees and the visor's edge. Mirrors the dial's plate (C.surface
    at 168/255 with the theme hairline) so it reads over bright footage. Repaints only when a
    printed run changes, and resizes only when a line's LENGTH does (the face is monospaced)."""

    _PAD = theme.SPACE_S

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self._lines = ReadoutLines()
        self._small = theme.mono_font(theme.EMPHASIS, theme.W_REGULAR)
        self._big = theme.mono_font(theme.HERO)
        self._shape: tuple = ()
        self._hint: QSize | None = None

    def lines(self) -> ReadoutLines:
        return self._lines

    def set_lines(self, lines: ReadoutLines) -> None:
        if lines == self._lines:
            return
        self._lines = lines
        shape = (len(lines.lap), len(lines.delta), len(lines.tail), len(lines.pass_))
        if shape != self._shape:
            self._shape = shape
            self._hint = None      # re-measured once, then reused every tick (sizeHint)
        self.update()

    def _rows(self):
        """(font, [(text, colour)...]) per painted row — the one layout paint and size share."""
        ln = self._lines
        rows = [(self._small, [(ln.lap, theme.C.text_dim if ln.lap_dim else theme.C.text)])]
        if ln.delta or ln.tail:
            rows.append((self._big, [(ln.delta, ln.delta_colour or theme.C.text),
                                     ("  " + ln.tail if ln.tail else "", theme.C.text)]))
        if ln.pass_:
            rows.append((self._small, [(ln.pass_, theme.C.accent)]))
        return rows

    def sizeHint(self):  # noqa: N802 (Qt override)
        if self._hint is None:
            self._hint = self._measure()
        return self._hint

    def _measure(self) -> QSize:
        w = h = 0
        for font, runs in self._rows():
            fm = QFontMetrics(font)
            w = max(w, sum(fm.horizontalAdvance(t) for t, _c in runs))
            h += fm.height()
        return QSize(w + 2 * self._PAD, h + 2 * self._PAD)

    def paintEvent(self, _event):  # noqa: N802 (Qt override)
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setBrush(theme.qcolor(theme.C.surface, 168))
        p.setPen(QPen(theme.qcolor(theme.C.border, 200), 1))
        p.drawRoundedRect(QRectF(1, 1, self.width() - 2, self.height() - 2),
                          theme.RADIUS_M, theme.RADIUS_M)
        y = self._PAD
        for font, runs in self._rows():
            fm = QFontMetrics(font)
            p.setFont(font)
            x = self._PAD
            for text, colour in runs:
                p.setPen(theme.qcolor(colour))
                p.drawText(x, y + fm.ascent(), text)
                x += fm.horizontalAdvance(text)
            y += fm.height()
        p.end()


def _resolve_notify_overrides(audio: QAudioOutput) -> None:
    """Have PySide look up this output's connectNotify/disconnectNotify Python overrides NOW, on the
    GUI thread, so that no FFmpeg engine thread ever has to (QA 2026-09-26, LIFE-1).

    Attached to a player, the output is connected to by the engine's AudioRenderer, and an
    ASYNCHRONOUS teardown — stop(), the dispose() of a reload, setAudioOutput(None) — destroys that
    renderer on its own thread, where ~QObject calls the output's disconnectNotify while holding one
    of Qt's pooled signal-slot mutexes. For an object PySide constructed, that virtual is the
    generated wrapper, which the FIRST time takes the GIL to look for a Python override and then
    answers from a per-object cache. Sampled on MK, the GIL held 5 s after the first stop() or
    dispose() of an un-muted pane: the QFFmpeg::AudioRenderer thread sat in take_gil under
    QAudioOutputWrapper::disconnectNotify in 4 of 4 runs — a thread waiting for the GIL with a Qt
    mutex held, the precondition of the hang sampled on 2026-09-25. One connect + disconnect here,
    while this thread holds the GIL anyway, fills both caches: the same runs park 0 of 4. (A chapter
    switch is no such teardown: setSource joins the engine inside the call, with the GIL released.)
    This leans on shiboken's per-object cache, measured on PySide 6.11.1;
    tests/test_player_audio.py pins the call, not the cache."""
    def _noop(*_args):
        pass
    audio.volumeChanged.connect(_noop)
    audio.volumeChanged.disconnect(_noop)


# ----------------------------------------------------------------- headless / CI seam
# PACER_NO_MEDIA=1 swaps the media triplet for the inert _Null* stand-ins below (headless CI smoke);
# built identically otherwise. Read once at construction.


class _NullMediaPlayer(QObject):
    """Inert QMediaPlayer stand-in (PACER_NO_MEDIA=1): same signals (never fire), no-op transport,
    permanent StoppedState."""

    positionChanged = Signal("qlonglong")
    playbackStateChanged = Signal(object)
    mediaStatusChanged = Signal(object)
    durationChanged = Signal("qlonglong")

    def setSource(self, url):
        pass

    def setPosition(self, ms):
        pass

    def play(self):
        pass

    def pause(self):
        pass

    def stop(self):
        pass

    def setVideoOutput(self, output):
        pass

    def setAudioOutput(self, output):
        pass

    def playbackState(self):
        # enum access needs no backend
        return QMediaPlayer.PlaybackState.StoppedState

    def setPlaybackRate(self, rate):
        self._rate = float(rate)

    def playbackRate(self):
        return getattr(self, "_rate", 1.0)


class _NullAudioOutput(QObject):
    """Inert QAudioOutput stand-in (PACER_NO_MEDIA=1): remembers the muted flag, no device. Starts
    muted, like production. Same signals (never fire), so code that connects to the real output's —
    _resolve_notify_overrides, when a test builds the non-headless pane with this stand-in — runs."""

    volumeChanged = Signal(float)
    mutedChanged = Signal(bool)
    deviceChanged = Signal()

    def __init__(self):
        super().__init__()
        self._muted = True

    def setVolume(self, volume):
        pass

    def setMuted(self, muted):
        self._muted = bool(muted)

    def isMuted(self):
        return self._muted


class PlayerPane(QWidget):
    """One single-lap player: its own decoder + video surface + audio + g-meter overlay.

    Emits `positionChanged(global_seconds)` as it plays and `chapterChanged(index)` when the
    source switches; exposes the chapter-aware `seek(global_seconds)` so the shell / map / plots
    can drive it. All playback state lives here; the shell owns only the transport chrome."""

    positionChanged = Signal(float)  # GLOBAL seconds on the session clock
    chapterChanged = Signal(int)     # current chapter index (for the UI label)
    playbackStateChanged = Signal(object)  # forwards QMediaPlayer.PlaybackState to the shell
    # re-emits inner QMediaPlayer.durationChanged(ms) so the shell wires to the pane, not the
    # private player. Signature matches QMediaPlayer.durationChanged(qlonglong).
    durationChanged = Signal("qlonglong")
    # True while a cross-chapter switch is reopening the next chapter (shell shows a brief loading hint).
    seamLoading = Signal(bool)
    # A double-click landed on the VIDEO CONTENT itself (not a panel header) — the shell turns this
    # into the "make the video fill the screen" (video-focus) toggle, like a normal video player.
    videoDoubleClicked = Signal()
    # A pass's clip is over: its end was reached (and the pane paused there) or dropped by the
    # driver's own seek / play / pause. Emitted once per clip, by the `_clip_end` setter.
    clipEnded = Signal()
    # ...and its twin: a clip end was armed (a pass started, or Loop replayed one that had ended).
    clipStarted = Signal()
    # The PASS is the subject (True) or no longer is (False): see `_pass`. Drives the transport's
    # Loop / Slow toggles, which exist only while there is a pass to loop or slow down.
    passChanged = Signal(bool)
    # Loop went on / off (`set_loop`), by the toggle or because the pass stopped being the subject.
    loopChanged = Signal(bool)

    def __init__(self, source: str | chapters.ChapterMap | None):
        super().__init__()
        # Normalise the source into a ChapterMap (single file => a one-entry map at offset 0).
        # `None` (no video) leaves _chapters None and the player source unset.
        self._chapters: chapters.ChapterMap | None = None
        if isinstance(source, chapters.ChapterMap):
            self._chapters = source
        elif source:
            # lone path: a 0-duration single-entry map (global == local for one chapter; only the
            # unused total_duration is 0).
            self._chapters = chapters.ChapterMap([source], [0.0])

        self._current_chapter = 0  # loaded chapter index
        # deferred (chapter, local, resume) seek applied on genuine load; also drives EndOfMedia
        # auto-advance.
        self._pending: tuple[int, float, bool] | None = None
        # source switch in flight: gate out the OLD source's spurious LoadedMedia/EndOfMedia (see
        # _on_media_status / THE CHAPTER-SEAM GATE).
        self._switching = False
        # True only inside our own player.setSource(): the backend re-emits the OLD source's
        # statuses from within that call, so nothing arriving then is the new source's load.
        self._in_set_source = False
        # A drag's seek still on its way to the screen, and the newest target waiting behind it
        # (seek_dragged: one in flight, the latest wins).
        self._drag_inflight = False
        self._drag_held: float | None = None
        # user paused mid-reopen: makes _apply_pending skip the resume so a deliberate pause survives
        # the seam (the deferred resume captured the pre-pause state).
        self._user_paused_during_reopen = False
        self._latest_global = 0.0  # last emitted global time (for current_global_time())
        # compare-mode lap window (start_global, end_global): the pane pauses+clamps at end instead
        # of the session end; None in normal mode. See _on_position.
        self._lap_window: tuple[float, float] | None = None
        # A ONE-SHOT clip end (global telemetry time): playback pauses once there and the end is
        # dropped. Set by `set_clip_end` after a pass's seek + play (the Corners tab's Quickest
        # passes); any later seek, play or pause drops it, so a driver who scrubs, presses ▶ or
        # picks a lap is never paused by a clip he has left. NOT the lap window: that one is
        # compare's, rewinds on play and clamps the reported position, none of which a clip wants.
        self.__clip_end: float | None = None
        # THE PASS (global start, end) the driver is watching — longer-lived than the clip end: it
        # survives the pause at the clip's end and a pause of his own, so Loop can still replay it,
        # and ends with anything that goes somewhere else (a seek of his, ▶ without Loop, a new
        # clip, compare). `_loop` replays it from `start` each time playback reaches `end`, until
        # the pass ends; it never moves on to another pass (refused-2026-09 §31: no reel).
        self.__pass: tuple[float, float] | None = None
        self._loop = False
        # Set by a loop's own seek back to the start: until a position before the end arrives,
        # a late report from the old position must not restart the loop a second time.
        self._loop_rewinding = False
        # Media playback rate (1.0 = real time). Remembered because a SOURCE SWITCH does not carry
        # it: the rate is a property of the QMediaPlayer's current media on the FFmpeg backend, so
        # a chapter seam would silently drop a slow-motion review back to 1x. Re-applied in
        # _apply_pending, which is the one place a genuine load lands (seam + watchdog alike).
        self._rate = 1.0

        # PACER_NO_MEDIA=1 builds the pane with the inert media triplet (see the _Null* stand-ins).
        no_media = os.environ.get("PACER_NO_MEDIA") == "1"
        self.video = QWidget() if no_media else QVideoWidget()
        # g-meter: frameless top-level window pinned to the video corner (see module docstring for
        # the macOS why).
        self.gmeter = GMeterOverlay(self)
        self.gmeter.hide()  # off by default; the toggle reveals it
        # The USER's intent (the toggle button), NOT the overlay's on-screen state: the dial also
        # has to stand down whenever the video can't host it (a collapsed panel), and come back
        # when it can. _position_gmeter is the single authority over what's actually shown.
        self._gmeter_on = False
        # The live readout (LiveReadout): the same kind of top-level window, same pinning hooks, at
        # the video's top-left. `_readout_on` is the VIEW's decision (fullscreen video + the key),
        # not whether it is on screen right now — _position_readout decides that.
        self.live_readout = LiveReadout(self)
        self.live_readout.hide()
        self._readout_on = False
        # watch the video widget's move/resize to re-pin the overlay (the pane's own move/resize are
        # caught in moveEvent/resizeEvent; the top-level window's in _watch_top_level).
        self.video.installEventFilter(self)
        self._watched_top: QWidget | None = None
        if no_media:
            self.player = _NullMediaPlayer()
            self.audio = _NullAudioOutput()
        else:
            self.player = QMediaPlayer()
            # muted by default (telemetry tool — no surprise 4K-clip audio); volume preset so un-mute
            # is audible.
            self.audio = QAudioOutput()
            _resolve_notify_overrides(self.audio)   # before any engine thread can disconnect from it
            self.audio.setVolume(0.6)
            self.audio.setMuted(True)
        # The audio output is attached on the first UN-mute (set_muted), not here: attached, it gives
        # the FFmpeg engine an audio-renderer thread (and opens the device), and a muted pane — pane
        # B always, the primary by default — would never use either; once seeks present paused,
        # every pane builds its engine at load. The renderer's one hazard, its asynchronous teardown
        # calling back into Python from its own thread (stop(), a reload's dispose(), a detach — not
        # a chapter switch, which joins the engine synchronously), is closed at construction by
        # _resolve_notify_overrides, so an un-mute is safe on every path that later tears it down.
        self._audio_attached = False
        self.player.setVideoOutput(self.video)

        # The pane is JUST the video surface; the transport chrome lives in the shell.
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)

        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.durationChanged.connect(self.durationChanged)  # re-emit on the pane's signal

        # Bounded-resume watchdog: a one-shot armed when a cross-chapter switch defers a seek+resume,
        # force-applying it if the reopen exceeds the budget so a seam can never hang.
        self._seam_watchdog = QTimer(self)
        self._seam_watchdog.setSingleShot(True)
        self._seam_watchdog.setInterval(_SEAM_RESUME_WATCHDOG_MS)
        self._seam_watchdog.timeout.connect(self._on_seam_watchdog)

        # The drag's release: the in-flight seek's frame reaching the video sink, or this timer.
        self._drag_release = QTimer(self)
        self._drag_release.setSingleShot(True)
        self._drag_release.setInterval(_DRAG_SEEK_RELEASE_MS)
        self._drag_release.timeout.connect(self._release_drag)
        sink = getattr(self.video, "videoSink", None)    # the headless stand-in has no sink
        self._sink = sink() if sink is not None else None
        self._frames_watched = False   # the sink is listened to only while a drag seek is in flight

        if self._chapters is not None:
            # initial load is not a replacement — don't gate (no spurious statuses, and a gate left
            # armed with no watchdog would hang).
            self._set_source(0, switching=False)

    # ------------------------------------------------------------- source mgmt
    @property
    def is_multi(self) -> bool:
        return self._chapters is not None and self._chapters.is_multi

    @property
    def total_duration(self) -> float:
        """Total session duration in seconds (sum of chapter durations), or 0 if unknown."""
        return self._chapters.total_duration if self._chapters is not None else 0.0

    def chapter_count(self) -> int:
        """Number of chapters in this pane's recording (1 for a single file, 0 if no source)."""
        return len(self._chapters) if self._chapters is not None else 0

    def chapter_duration(self, index: int) -> float:
        """The GPMF/metadata-track duration (seconds) of chapter `index`, or 0 if unknown/out of
        range. Built the global offset table; can differ from the real video-track duration
        QMediaPlayer reports (the slider range reconciles the two)."""
        if self._chapters is None or not (0 <= index < len(self._chapters)):
            return 0.0
        return self._chapters.chapters[index].duration

    def current_chapter(self) -> int:
        return self._current_chapter

    def current_global_time(self) -> float:
        """The pane's current position on the GLOBAL session clock, in seconds."""
        return self._latest_global

    def _offset(self) -> float:
        """Global start (seconds) of the currently loaded chapter."""
        if self._chapters is None:
            return 0.0
        return self._chapters.chapters[self._current_chapter].offset

    def _source_is_chapter(self, index: int, strict: bool = False) -> bool:
        """True iff the player's current source is chapter `index`'s file (the genuine target load
        landed, not a leftover from an earlier _set_source). Compares absolute paths; the headless
        null player has no source() (synchronous, raceless) so it reports True — or False when
        `strict`: the seam gate opens on a LoadedMedia only with POSITIVE proof (_on_media_status)."""
        if self._chapters is None:
            return not strict
        source = getattr(self.player, "source", None)
        if source is None:
            return not strict  # null/inert player (headless): no async load to race
        loaded = source().toLocalFile()
        if not loaded:
            return not strict  # no resolvable source URL — don't block the legacy apply path
        want = os.path.abspath(self._chapters.chapters[index].path)
        return os.path.abspath(loaded) == want

    def _set_source(self, index: int, switching: bool = True):
        """Load chapter `index` (no seek/play; callers defer the post-load seek via _pending).
        `switching` arms the gate that suppresses the old source's spurious statuses on a
        REPLACEMENT; the initial load passes False (no leftover statuses, and a gate left armed with
        no watchdog would hang)."""
        if self._chapters is None:
            return
        index = min(max(index, 0), len(self._chapters) - 1)
        self._current_chapter = index
        self._switching = switching   # arm the gate before setSource (replacement only)
        path = self._chapters.chapters[index].path
        self._in_set_source = True    # the OLD source's statuses re-emitted in here are not our load
        try:
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        finally:
            self._in_set_source = False
        self.chapterChanged.emit(index)
        # arm the bounded-resume watchdog if this switch defers a seek (idempotent; stopped on apply).
        if self._pending is not None:
            self._seam_watchdog.start()

    # ------------------------------------------------------------- transport
    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play(self):
        if self._loop and self.__pass is not None:
            self._clip_end = self.__pass[1]   # ▶ on a paused loop: the loop runs on
        else:
            self._end_pass()                  # ▶ after (or out of) a pass carries on with the lap
        self._resume()

    def _resume(self):
        # explicit play overrides a pause made mid-seam: honour PLAY on the genuine load (else
        # play-after-pause-during-reopen never resumes).
        self._user_paused_during_reopen = False
        if self._pending is not None:
            # ▶ while a seek waits for its source to load (the open's poster, a paused chapter
            # jump): play from THAT target once it lands, not from wherever the load starts.
            index, local, _resume = self._pending
            self._pending = (index, local, True)
        # parked at the lap end (auto-paused): rewind to the window start so Play re-rolls the lap
        # (else a bare play() pauses again on the next tick).
        win = self._lap_window
        if win is not None and self.current_global_time() >= win[1] - _WINDOW_STOP_TOL_S:
            self.seek(win[0])
        self.player.play()

    def pause(self):
        # pause mid-reopen: remember it so the deferred resume does not override it (see _apply_pending).
        if self._switching or self._pending is not None:
            self._user_paused_during_reopen = True
        # The clip stops running, but the PASS stays the subject: paused on it, Loop can still
        # replay it, and a paused loop resumes on ▶ (see play).
        self._clip_end = None
        self.player.pause()

    # ------------------------------------------------------------- playback rate (slow motion)
    def playback_rate(self) -> float:
        """The pane's media playback rate (1.0 = real time)."""
        return self._rate

    def set_playback_rate(self, rate: float):
        """Play at `rate` x real time (0.25 = quarter speed). Idempotent.

        THE TELEMETRY LOCK IS UNAFFECTED, and that is a property of where the lock is taken rather
        than of anything done here: the map marker / chart cursor / readout are driven off
        `positionChanged` — the decoder's OWN reported media position — through PlaybackState, not
        off a wall clock the app integrates. A rate change moves the same positions past the same
        30 Hz tick more slowly; every consumer downstream still reads the decoder's media position,
        crossed once to telemetry in `_on_position`, and looks the telemetry up at it. Nothing here
        converts between wall time and media time, so there is no conversion to get wrong."""
        rate = float(rate)
        if rate <= 0:
            return
        self._rate = rate
        self.player.setPlaybackRate(rate)

    # ------------------------------------------------------------- audio (mute)
    def is_muted(self) -> bool:
        return self.audio.isMuted()

    def set_muted(self, muted: bool):
        self.audio.setMuted(bool(muted))
        if not muted and not self._audio_attached:
            # first un-mute: only now does the engine get an audio renderer (see __init__). It stays
            # attached after a re-mute: detaching would be one more asynchronous renderer teardown,
            # and a muted output plays nothing anyway.
            self._audio_attached = True
            self.player.setAudioOutput(self.audio)

    def _clock(self):
        """This pane's telemetry->media conversion — the recording's own, off the ChapterMap it was
        opened with (so a cross-recording pane B converts with the REFERENCE recording's clock, not
        the primary's). IDENTITY when there is no map or no fitted clock."""
        return media_clock.clock_of(self._chapters)

    def seek(self, seconds: float):
        """Seek to a GLOBAL session time ON THE TELEMETRY CLOCK. Maps to (chapter, local); if that's
        the current source seek directly, else switch source and apply the local seek once the new
        media has loaded (preserving the current play/pause state).

        THIS PANE IS THE APP'S SEAM BETWEEN THE TWO CLOCKS, and it is the only one: every caller —
        the lap-seek, the scrub drag, the slider, the compare fan-out — speaks the telemetry clock
        the rest of the app speaks, and the conversion to media time happens HERE, once, on the way
        into the media player (and back again in `_on_position`). Before this, a lap start was
        handed to the player as if it were a media time, which by the end of a long recording put
        the picture ~0.2 s away from the moment asked for — and, within that distance of a chapter
        boundary, could resolve to the wrong chapter file. See studio/media_clock.py.

        EVERY SEEK LANDS AND IS SHOWN (QA 2026-09-25, VIEW-1..3; tests/test_player_seek_present.py).
        Qt 6.11's FFmpeg backend DROPS a setPosition made while the source is still loading — the
        open's poster seek and compare pane B's lap-start seek both are — so such a seek waits in
        _pending for the load, exactly as a chapter switch's does. And a seek in StoppedState moves
        the position but presents no frame (every fresh source is Stopped), so a seek that does not
        resume play ends paused: _present_paused."""
        self._drag_held = None   # a deliberate seek drops a drag target still waiting its turn
        self._end_pass()         # ...and the pass, its clip end and its loop: he went elsewhere
        self._seek_to(seconds)

    def _seek_to(self, seconds: float):
        """`seek`'s body without its "the driver went somewhere else": the loop's own rewind."""
        if self._chapters is None:
            self.player.setPosition(int(seconds * 1000))
            return
        index, local = self._chapters.to_local(self._clock().to_media(seconds))
        if index == self._current_chapter:
            if self._switching or self._pending is not None or self._media_loading():
                # switch to THIS chapter still in flight, or its media still loading: fold the new
                # target into the deferred seek (a direct setPosition would hit the old media / be
                # dropped by the loading one), preserving the resume intent.
                resume = self._pending[2] if self._pending is not None else self.is_playing()
                self._pending = (index, local, resume)
            else:
                self.player.setPosition(int(local * 1000))
                self._present_paused()
        else:
            # switch source; defer the seek (+ a resume if playing) to LoadedMedia.
            # Flag the seam BEFORE _set_source, exactly as the EndOfMedia auto-advance does: this is
            # the same reopen, so it owes the same honesty. Without it the shell's chapterChanged
            # handler renames the banner to a chapter that has NOT loaded yet, with no busy
            # affordance; the hint suppresses that rename and _apply_pending clears it.
            self.seamLoading.emit(True)
            self._pending = (index, local, self.is_playing())
            self._set_source(index)

    def seek_dragged(self, seconds: float):
        """A DRAG's seek (the chart scrub, the map-dot drag): at most ONE in flight, the latest
        target wins. A paused seek on 4K HEVC takes 136-410 ms to reach the screen, and the drag used
        to re-seek every ~33 ms tick — each seek superseded before its frame existed, so a 1 s drag
        showed ONE frame (QA VIEW-4). Newer targets now wait for the in-flight seek's frame (or
        _DRAG_SEEK_RELEASE_MS) and only the newest is sent, so the picture follows the drag."""
        if self._drag_inflight:
            self._drag_held = float(seconds)
            return
        self._drag_inflight = True
        self._watch_frames(True)
        self._drag_release.start()
        self.seek(seconds)

    def finish_drag(self):
        """The drag is over (the transport handle let go): a target still held behind the in-flight
        seek goes NOW, as a deliberate seek. Waiting for the in-flight frame first would put the
        final picture a whole extra seek late, for a frame it would only replace. Nothing held: the
        in-flight seek IS the final one, and is left to land."""
        if self._drag_held is not None:
            self.seek(self._drag_held)

    def _media_loading(self) -> bool:
        """True while the current source has not loaded yet — when the FFmpeg backend drops a
        setPosition. The headless null player has no mediaStatus and loads nothing."""
        status = getattr(self.player, "mediaStatus", None)
        return status is not None and status() in (QMediaPlayer.MediaStatus.NoMedia,
                                                    QMediaPlayer.MediaStatus.LoadingMedia)

    def _present_paused(self):
        """Show the frame at the player's position when nothing else will: in StoppedState — the
        state of every fresh source, at the open and after each chapter switch — a seek presents
        NO frame, and pause() is what decodes and shows it (measured, Qt 6.11 FFmpeg; ▶ afterwards
        plays from there). A paused or playing player already presents: left alone."""
        if self.player.playbackState() == QMediaPlayer.PlaybackState.StoppedState:
            self.player.pause()

    # ------------------------------------------------------------- lap window (compare mode)
    def set_lap_window(self, start_global: float, end_global: float):
        """Confine playback to a lap GLOBAL [start, end]: pause+clamp at end instead of session end.
        Cross-chapter auto-advance still spans seams inside the window."""
        self._end_pass()   # compare is two laps, not a pass: a loop never runs inside it
        self._lap_window = (float(start_global), float(end_global))

    def clear_lap_window(self):
        """Drop the lap window (back to whole-session playback)."""
        self._lap_window = None

    # ------------------------------------------------------------- clip end (a pass's clip)
    def set_clip_end(self, end_global: float):
        """Pause once playback reaches GLOBAL `end_global`, then forget it (see `_clip_end`).
        Call it AFTER the clip's own seek and play, which drop any earlier end."""
        self._clip_end = float(end_global)

    def clip_end(self) -> float | None:
        return self._clip_end

    def set_clip(self, start_global: float, end_global: float):
        """A pass's clip: `set_clip_end(end)`, and the pass [start, end] becomes the subject that
        Loop replays (see `_pass`). Call it AFTER the clip's own seek and play, as set_clip_end.
        The end is clamped inside the recording: a pass whose "1 s after the exit" runs past the
        last frame would otherwise sit at EndOfMedia with Loop lit and never come round."""
        end = float(end_global)
        media_end = self._media_end()
        if media_end is not None:
            end = min(end, media_end - _WINDOW_STOP_TOL_S)
        self._clip_end = end
        self._pass = (float(start_global), end)

    def _media_end(self) -> float | None:
        """The recording's last instant on the telemetry clock, or None when unknown: the chapter
        table's total, or the player's own duration once the last chapter is loaded (the two
        can differ: chapter_duration)."""
        if self._chapters is None:
            return None
        ends = [self._chapters.total_duration] if self._chapters.total_duration > 0 else []
        duration = getattr(self.player, "duration", None)
        if duration is not None and self._current_chapter == len(self._chapters) - 1:
            ms = duration()
            if ms > 0:
                ends.append(self._offset() + ms / 1000.0)
        return self._clock().to_telemetry(min(ends)) if ends else None

    def pass_clip(self) -> tuple[float, float] | None:
        """The pass being watched (global start, end), or None."""
        return self.__pass

    def is_looping(self) -> bool:
        return self._loop

    def set_loop(self, on: bool) -> bool:
        """Loop the pass: each time playback reaches its end it goes back to its start and plays
        on, until the pass ends (a seek of the driver's, ▶ without Loop, a new clip, compare).
        Turned on while not playing it plays — from where the playhead is when that is inside the
        pass (paused on a frame of it), else from the start (parked at its end). Turned off, the
        pass plays out and pauses at its end, the clip's own rule. False when there is no pass."""
        on = bool(on)
        if on and self.__pass is None:
            return False
        if on == self._loop:
            return True
        self._loop = on
        self._loop_rewinding = False
        self.loopChanged.emit(on)
        if on:
            start, end = self.__pass
            if not self.is_playing():
                t = self._latest_global
                if self._pending is None and not start <= t < end - _WINDOW_STOP_TOL_S:
                    self._seek_to(start)
                self._resume()
            self._clip_end = end
        return True

    def end_pass(self):
        """The pass stops being the subject without a seek (VideoView.leave_pass)."""
        self._end_pass()

    def _end_pass(self):
        """The driver went somewhere else: the clip end, the pass and its loop all go."""
        self._clip_end = None
        self._pass = None

    @property
    def _pass(self) -> tuple[float, float] | None:
        return self.__pass

    @_pass.setter
    def _pass(self, clip: tuple[float, float] | None) -> None:
        # The one place the pass starts or stops being the subject, so the one place that tells
        # the transport (passChanged) and drops a loop that has no pass left to replay.
        was = self.__pass is not None
        self.__pass = clip
        if clip is None and self._loop:
            self._loop = False
            self._loop_rewinding = False
            self.loopChanged.emit(False)
        if was or clip is not None:
            self.passChanged.emit(clip is not None)

    @property
    def _clip_end(self) -> float | None:
        return self.__clip_end

    @_clip_end.setter
    def _clip_end(self, end: float | None) -> None:
        # Every site that ends a clip writes None here (the end reached, a seek, play or pause),
        # so this is the one place that can tell the view the clip is over.
        ended = self.__clip_end is not None and end is None
        started = self.__clip_end is None and end is not None
        self.__clip_end = end
        if ended:
            self.clipEnded.emit()
        elif started:
            self.clipStarted.emit()

    # ------------------------------------------------------------- g-meter overlay
    def set_gmeter_visible(self, on: bool):
        """Record the user's g-meter toggle. Whether the dial is actually on screen is decided by
        _position_gmeter — turning it on over a collapsed video shows nothing until the video is
        back, rather than parking a stray always-on-top window off-screen."""
        self._gmeter_on = bool(on)
        if self._gmeter_on:
            self._watch_top_level()
            self._position_gmeter()
        else:
            self.gmeter.hide()

    def is_gmeter_visible(self) -> bool:
        return self._gmeter_on

    def sync_gmeter(self):
        """Public: re-pin the g-meter overlay to the video corner if it's on (a no-op when the
        overlay is hidden). The transport/compare layer calls this after a geometry change it owns
        (e.g. a splitter-handle drag) without reaching into the pane's private re-pin."""
        self._sync_gmeter()

    def _sync_gmeter(self):
        """Re-pin the overlay windows to the video corners if they're on (cheap; called on any
        geometry change of the video widget, this pane, or the app window)."""
        if self._gmeter_on:
            self._position_gmeter()
            if self.gmeter.isVisible():
                self.gmeter.raise_()
        if self._readout_on:
            self._position_readout()

    # ------------------------------------------------------------- live readout overlay
    def set_live_readout_visible(self, on: bool):
        """The view's decision to show the readout (see `_readout_on`); on screen only while the
        video can host it beside the dial (_readout_target_rect)."""
        self._readout_on = bool(on)
        if self._readout_on:
            self._watch_top_level()
            self._position_readout()
        else:
            self.live_readout.hide()

    def is_live_readout_on(self) -> bool:
        return self._readout_on

    def set_live_readout(self, lines: ReadoutLines):
        """Per tick while on: hand the readout its runs (it repaints only on a change) and re-pin
        it, as set_g does for the dial (a top-level window does not follow an app-window drag)."""
        if not self._readout_on:
            return
        self.live_readout.set_lines(lines)
        self._position_readout()

    def _readout_target_rect(self) -> QRect | None:
        """The readout's rect in GLOBAL coords at the video's top-left, or None when the video is
        off screen or too narrow to hold it AND the dial's corner beside it — it never covers the
        g-meter, which keeps the top-right (the dial's own width is reserved even while it is off,
        so turning G on can never land the dial on the numbers)."""
        if not self._video_is_on_screen():
            return None
        vw, vh = self.video.width(), self.video.height()
        hint = self.live_readout.sizeHint()
        dial_w = int(min(max(vw * _OVERLAY_FRAC, _OVERLAY_MIN_W), _OVERLAY_MAX_W))
        if hint.width() + dial_w + 3 * _OVERLAY_PAD > vw or hint.height() + 2 * _OVERLAY_PAD > vh:
            return None
        corner = self.video.mapToGlobal(QPoint(_OVERLAY_PAD, _OVERLAY_PAD))
        return QRect(corner.x(), corner.y(), hint.width(), hint.height())

    def _position_readout(self):
        rect = self._readout_target_rect()
        if rect is None:
            self.live_readout.hide()
            return
        if self.live_readout.geometry() != rect:
            self.live_readout.setGeometry(rect)
        if not self.live_readout.isVisible():
            self.live_readout.show()
            self.live_readout.raise_()

    def set_g(self, g):
        """Feed (lat_g, long_g, total_g) to the overlay (None blanks the dot); no-op cost when
        hidden so call every tick. Also re-pins from this ~30 Hz tick (a top-level overlay does not
        track app-window drags)."""
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.set_g(g)

    # (No `set_gmeter_source` here any more. The dial's axis provenance was a tag on its face; it
    # is now one sentence on the g-meter toggle's tooltip, which VideoView owns — so the fact never
    # needs to reach a pane. See video_view.set_gmeter_source and review §6.5.)

    def set_gmeter_lap(self, lap_id):
        """Tell the overlay which lap drives it so its max-G envelope resets at the lap boundary."""
        self.gmeter.set_lap(lap_id)

    def _video_is_on_screen(self) -> bool:
        """True when the video widget is genuinely displayed, not merely `isVisible()`.

        A collapsed panel — what the ⛶ maximize button and the double-click-header gesture do by
        zeroing a splitter section — does NOT leave a tidy zero-sized widget. Measured on the real
        window: maximizing the lap table gave `video 1432x0`, while maximizing the charts left the
        video a healthy `280x471` inside a column Qt had simply MOVED to `pos=(-1, -855)`, with
        every widget still `isVisible()` and no zero-sized ancestor anywhere. Both mapped to a
        global corner far outside the window, so the dial was parked at negative screen
        coordinates (-251, 37) / (149, -787) as a stray always-on-top window instead of standing
        down with its video.

        So the test is: the video is on screen when it has a real size AND its global rect
        actually intersects the app window's. That catches the moved-off column and the zeroed one
        alike, and — unlike `visibleRegion()`, the other candidate — it can be confused neither by
        the QVideoWidget's native macOS surface nor by the video child obscuring its own parent."""
        vw, vh = self.video.width(), self.video.height()
        if self.video.isHidden() or not self.video.isVisible() or vw <= 0 or vh <= 0:
            return False    # (isVisible: the pane itself, or a panel above it, was hidden)
        top = self.window()
        if top is None or top is self.video:
            return True
        # A hidden, closed or minimised window hosts nothing: its geometry still intersects
        # itself, and the next tick's re-pin used to show both overlay windows over the desktop.
        if not top.isVisible() or top.isMinimized():
            return False
        top_left = self.video.mapToGlobal(QPoint(0, 0))
        return top.geometry().intersects(QRect(top_left.x(), top_left.y(), vw, vh))

    def _gmeter_target_rect(self) -> QRect | None:
        """The overlay's pinned rect in GLOBAL screen coords, or None when the video cannot host
        a legible dial — the video is off screen (see _video_is_on_screen) or smaller than the
        overlay's own minimum plus its corner inset. The old code clamped the dial to whatever
        space there was, so a 1 px-tall video still got a 240x140 window (the overlay's
        minimumSize floors it) hanging outside the video."""
        if not self._video_is_on_screen():
            return None
        vw, vh = self.video.width(), self.video.height()
        w = int(min(max(vw * _OVERLAY_FRAC, _OVERLAY_MIN_W), _OVERLAY_MAX_W))
        h = int(w * _OVERLAY_ASPECT)
        # Ask for the size the overlay will ACTUALLY take: its own minimumSize floors whatever we
        # set (_OVERLAY_ASPECT puts the smallest dial at 120x134 against a 120x140 minimum), so an
        # unclamped target can never equal gmeter.geometry() and _position_gmeter's "only move on
        # change" guard re-issues setGeometry on every tick for a size Qt then clamps straight back.
        w = max(w, self.gmeter.minimumWidth())
        h = max(h, self.gmeter.minimumHeight())
        if w + 2 * _OVERLAY_PAD > vw or h + 2 * _OVERLAY_PAD > vh:
            return None
        corner = self.video.mapToGlobal(QPoint(vw - w - _OVERLAY_PAD, _OVERLAY_PAD))
        return QRect(corner.x(), corner.y(), w, h)

    def _position_gmeter(self):
        """Pin the overlay to the video's top-right corner (global screen coords) — or hide it when
        the video can't host it. The SINGLE authority over the dial's on-screen state, so a
        collapsed panel takes the dial with it and restoring the panel brings it straight back."""
        rect = self._gmeter_target_rect()
        if rect is None:
            self.gmeter.hide()
            return
        # only move on change — runs every 30 Hz tick.
        if self.gmeter.geometry() != rect:
            self.gmeter.setGeometry(rect)
        if not self.gmeter.isVisible():
            self.gmeter.show()
            self.gmeter.raise_()

    def _watch_top_level(self):
        """Watch the APP WINDOW for move/resize so the overlay follows it.

        A child widget receives no Move event when its top-level is dragged across the screen, and
        the only other re-pin — the ~30 Hz one in set_g — runs solely when playback advances. So
        dragging the window while PAUSED used to strand the dial at its old screen coordinates,
        detached from the video and floating over whatever was underneath. Re-resolved on show
        because the pane is built before it is parented into the window."""
        top = self.window()
        if top is self._watched_top:
            return
        if self._watched_top is not None:
            self._watched_top.removeEventFilter(self)
        self._watched_top = top
        if top is not None and top is not self:
            top.installEventFilter(self)

    # --- hooks keeping the top-level overlay pinned to the video corner (+ the per-tick re-pin in set_g) ---
    def eventFilter(self, obj, event):
        # re-pin on video widget resize/move, and on the APP WINDOW's (a child gets no Move when
        # its top-level is dragged, and the per-tick re-pin stops while playback is paused).
        if obj in (self.video, self._watched_top) and event.type() in (QEvent.Resize, QEvent.Move):
            self._sync_gmeter()
        # A double-click on the video CONTENT (the QVideoWidget surface / its null stand-in) is the
        # "make the video fill the screen" gesture — emit it for the shell to route (mirrors the
        # panel-header dblclick-to-maximize, but on the video itself, like a normal player).
        elif obj is self.video and event.type() == QEvent.MouseButtonDblClick:
            self.videoDoubleClicked.emit()
            return True
        return super().eventFilter(obj, event)

    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_gmeter()   # re-pin (top-level overlay does not track parent)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_gmeter()   # re-pin (top-level overlay does not track parent)

    def showEvent(self, event):
        super().showEvent(event)
        # The pane is built before it is parented into the window, so this is where the top-level
        # to follow becomes known. _position_gmeter re-shows the dial (hideEvent hid it).
        self._watch_top_level()
        if self._gmeter_on:
            self._position_gmeter()
        if self._readout_on:
            self._position_readout()

    def hideEvent(self, event):
        super().hideEvent(event)
        # hide the overlay so a detached top-level can't linger; _gmeter_on stays on for showEvent.
        if self._gmeter_on:
            self.gmeter.hide()
        self.live_readout.hide()

    def closeEvent(self, event):
        self.gmeter.close()   # the overlay has no parent window to close it
        self.live_readout.close()
        super().closeEvent(event)

    def stop(self):
        """Stop the decoder AND close the overlay window — clean disposal for a reload / Phase B
        pane teardown (the overlay is a top-level window with no parent window to close it)."""
        self._seam_watchdog.stop()  # no stray seam timer firing into a torn-down player
        self._drag_release.stop()
        self.player.stop()
        self.gmeter.close()
        self.live_readout.close()

    def dispose(self):
        """Release decoder + audio + overlay (player/audio are NOT Qt children of the pane, so they
        must be deleteLater-d explicitly or the FFmpeg decoder lingers until GC). Idempotent."""
        # Every step is inside the guard, including the watchdog stop and the overlay close. They
        # were outside it, and _seam_watchdog is a QTimer CHILD of this pane: when the pane's C++
        # object had already been deleted, its very first statement raised out of dispose(), out of
        # _build_ui, and left the window stranded on the loading card. A teardown that can raise is
        # a teardown that strands the caller — belt and braces behind app._dispose_view's own guard.
        # Every step is a lambda, not a bound method: resolving `self._seam_watchdog.stop` on a
        # deleted C++ object raises at ATTRIBUTE ACCESS, which a tuple of bound methods would do
        # outside the guard — reintroducing the very crash this is here to contain.
        for step in (lambda: self._seam_watchdog.stop(),
                     lambda: self._drag_release.stop(),
                     lambda: self.player.stop(),
                     lambda: self.player.setVideoOutput(None),
                     lambda: self.player.setAudioOutput(None),
                     lambda: self.gmeter.close(),
                     lambda: self.gmeter.deleteLater(),
                     lambda: self.live_readout.close(),
                     lambda: self.live_readout.deleteLater(),
                     lambda: self.player.deleteLater(),
                     lambda: self.audio.deleteLater()):
            try:
                step()
            except RuntimeError:
                pass  # already torn down

    # ------------------------------------------------------------- player events
    def _on_position(self, ms: int):
        """Local media position -> global session time ON THE TELEMETRY CLOCK (so all telemetry
        sync sees one clock — the same one `seek` accepts, and the one every Session series is
        indexed by). Compare mode: at the window end (within tolerance) pause and clamp the emitted
        position to the end so the pane parks on the lap's last frame; cross-chapter auto-advance is
        untouched (it carries a seam-straddling lap up to that end)."""
        global_s = self._clock().to_telemetry(self._offset() + ms / 1000.0)
        clip = self.__pass
        if self._loop and clip is not None:
            # Looping: the end sends playback back to the start, still playing. One rewind per
            # lap of the loop: reports from before the seek lands are still at or past the end.
            if self._loop_rewinding:
                self._loop_rewinding = global_s >= clip[1] - _WINDOW_STOP_TOL_S
            elif global_s >= clip[1] - _WINDOW_STOP_TOL_S and self.is_playing():
                self._loop_rewinding = True
                self._seek_to(clip[0])
        else:
            end = self._clip_end
            if end is not None and global_s >= end - _WINDOW_STOP_TOL_S and self.is_playing():
                self._clip_end = None
                self.player.pause()
        win = self._lap_window
        if win is not None and global_s >= win[1] - _WINDOW_STOP_TOL_S:
            # reached the lap end: pause and report the clamped end (idempotent).
            if self.is_playing():
                self.player.pause()
            global_s = win[1]
        self._latest_global = global_s
        self.positionChanged.emit(global_s)

    def _on_media_status(self, status):
        """Apply the deferred seek once the new source genuinely loads; auto-advance at EndOfMedia.

        THE CHAPTER-SEAM GATE (_switching): setSource is async but the backend synchronously re-emits
        the OLD source's leftover statuses (a spurious LoadedMedia + stale EndOfMedia) BEFORE parsing
        the new file; the real load then arrives as LoadingMedia -> LoadedMedia. Honouring _pending
        on the spurious LoadedMedia consumes it and the real load discards the seek+resume -> the
        seam stall. So while switching we ignore every status until LoadingMedia confirms the real
        load began, then apply _pending on the next genuine LoadedMedia.

        The real load does NOT always pass through LoadingMedia: a switch made while the previous
        source is itself still loading (the open's poster seek into chapter 2) gets none — the
        backend de-duplicates the status — and the gate used to wait for the 8 s watchdog (QA
        VIEW-3). So a LoadedMedia also opens it when it is provably the target's: not re-emitted
        inside our own setSource, and the player reporting the target chapter's file (measured: the
        leftover LoadedMedia still names the OLD file)."""
        loaded = status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        )
        if self._switching:
            if status == QMediaPlayer.MediaStatus.LoadingMedia:
                self._switching = False  # the real load began: the next genuine load applies _pending
                return
            if not (loaded and not self._in_set_source
                    and self._source_is_chapter(self._current_chapter, strict=True)):
                return  # ignore the spurious statuses emitted before the real load
            self._switching = False  # the target's own load, with no LoadingMedia before it

        if loaded and self._pending is not None:
            index, local, resume = self._pending
            # Match the loaded FILE, not just the index: on a fresh pane a chapter-0 load can open the
            # gate while _current_chapter is already a later target, so an index-only check would seek
            # the wrong file.
            if index == self._current_chapter and self._source_is_chapter(index):
                self._apply_pending(local, resume)
            return

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._on_end_of_media()

    def _apply_pending(self, local: float, resume: bool):
        """Apply the deferred seek (to `local` seconds) and resume play, or show the frame paused,
        clearing _pending + the seam state. Shared by the genuine-load path and the watchdog so the
        resume is identical however it's triggered."""
        self._pending = None
        self._switching = False
        self._seam_watchdog.stop()
        self.player.setPosition(int(local * 1000))
        # Re-apply the rate onto the freshly-loaded media (see _rate): a cross-chapter seam is a
        # new source, and a source carries no rate of its own. Unconditional rather than guarded on
        # `!= 1.0` — the guard would be a second place that has to know the default.
        self.player.setPlaybackRate(self._rate)
        # resume only if the original intent was to play AND the user did not pause mid-reopen;
        # otherwise the freshly-loaded (Stopped) source must be paused, or it shows nothing.
        if resume and not self._user_paused_during_reopen:
            self.player.play()
        else:
            self._present_paused()
        self._user_paused_during_reopen = False
        self.seamLoading.emit(False)   # seam reopen finished

    def _on_seam_watchdog(self):
        """Bounded-resume fallback: force-apply a deferred seek the genuine load never applied, so a
        seam never hangs. No-op if already cleared."""
        if self._pending is None:
            return
        index, local, resume = self._pending
        # chapter set synchronously by _set_source; apply regardless of gate.
        if index == self._current_chapter:
            self._apply_pending(local, resume)

    def _on_end_of_media(self):
        """Chapter end: auto-advance to the next chapter (play from 0) via a deferred seek, or stay
        paused at the true session end. Emits seamLoading(True) for the shell's brief reopen hint."""
        if self._chapters is None:
            return
        nxt = self._current_chapter + 1
        if nxt < len(self._chapters):
            self.seamLoading.emit(True)
            self._pending = (nxt, 0.0, True)
            self._set_source(nxt)
        elif self._loop and self.__pass is not None:
            # the recording ran out inside a looping pass (its end is clamped in set_clip, but a
            # last frame can stop short of it): come round, as the end itself would
            self._loop_rewinding = True
            self._seek_to(self.__pass[0])
            self._resume()

    def _on_state(self, state):
        self.playbackStateChanged.emit(state)

    def _watch_frames(self, on: bool):
        """Listen to the video sink only while a drag seek is in flight. Connected for good, every
        frame of playback would cross into Python on the GUI thread — the present path the ~30 Hz
        tick is kept off (studio/README.md, perf invariant 4)."""
        if self._sink is None or on == self._frames_watched:
            return
        self._frames_watched = on
        if on:
            self._sink.videoFrameChanged.connect(self._on_video_frame)
        else:
            self._sink.videoFrameChanged.disconnect(self._on_video_frame)

    def _on_video_frame(self, frame):
        """A frame reached the video sink: the in-flight drag seek has landed (seek_dragged). A
        bound method, so a frame the backend delivers from its render thread is queued onto ours."""
        if self._drag_inflight and frame.isValid():
            self._release_drag()

    def _release_drag(self):
        """Let the next drag seek go: the newest target held behind the in-flight one, if any."""
        self._drag_release.stop()
        self._drag_inflight = False
        held, self._drag_held = self._drag_held, None
        if held is not None:
            self.seek_dragged(held)
        else:
            self._watch_frames(False)   # the drag is over: back off the present path
