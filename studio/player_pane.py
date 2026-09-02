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

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QTimer, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QVBoxLayout, QWidget

from . import chapters
from .gmeter_overlay import GMeterOverlay

# The g-meter overlay sits in the TOP-RIGHT corner of the video, sized as a FRACTION of the
# video widget (not a fixed size) so it scales with the window and never dominates the frame.
_OVERLAY_FRAC = 0.22        # target width = this fraction of the video width
_OVERLAY_ASPECT = 1.12      # height / width (a touch taller than wide: title + dial + numbers)
_OVERLAY_MIN_W = 120        # don't shrink below something legible
_OVERLAY_MAX_W = 240        # don't grow huge on a very wide window
_OVERLAY_PAD = 12           # px inset from the video corner

# Lap-end stop tolerance (s): position is ms-quantized and frames ~16 ms apart, so clamp within this
# sub-frame of the window end (else it can step from just-before to just-after without reporting it).
_WINDOW_STOP_TOL_S = 0.002

# Budget after a cross-chapter switch before the watchdog force-applies the deferred seek+resume, so
# a disk/recording hiccup at a seam can never leave the player hung.
_SEAM_RESUME_WATCHDOG_MS = 8000

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


class _NullAudioOutput(QObject):
    """Inert QAudioOutput stand-in (PACER_NO_MEDIA=1): remembers the muted flag, no device. Starts
    muted, like production."""

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
        # user paused mid-reopen: makes _apply_pending skip the resume so a deliberate pause survives
        # the seam (the deferred resume captured the pre-pause state).
        self._user_paused_during_reopen = False
        self._latest_global = 0.0  # last emitted global time (for current_global_time())
        # compare-mode lap window (start_global, end_global): the pane pauses+clamps at end instead
        # of the session end; None in normal mode. See _on_position.
        self._lap_window: tuple[float, float] | None = None

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
            self.audio.setVolume(0.6)
            self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)   # no-ops on the null player
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

    def _source_is_chapter(self, index: int) -> bool:
        """True iff the player's current source is chapter `index`'s file (the genuine target load
        landed, not a leftover from an earlier _set_source). Compares absolute paths; the headless
        null player has no source() (synchronous, raceless) so it reports True."""
        if self._chapters is None:
            return True
        source = getattr(self.player, "source", None)
        if source is None:
            return True  # null/inert player (headless): no async load to race
        loaded = source().toLocalFile()
        if not loaded:
            return True  # no resolvable source URL — don't block the legacy apply path
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
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self.chapterChanged.emit(index)
        # arm the bounded-resume watchdog if this switch defers a seek (idempotent; stopped on apply).
        if self._pending is not None:
            self._seam_watchdog.start()

    # ------------------------------------------------------------- transport
    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play(self):
        # explicit play overrides a pause made mid-seam: honour PLAY on the genuine load (else
        # play-after-pause-during-reopen never resumes).
        self._user_paused_during_reopen = False
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
        self.player.pause()

    # ------------------------------------------------------------- audio (mute)
    def is_muted(self) -> bool:
        return self.audio.isMuted()

    def set_muted(self, muted: bool):
        self.audio.setMuted(bool(muted))

    def seek(self, seconds: float):
        """Seek to a GLOBAL session time. Maps to (chapter, local); if that's the current source
        seek directly, else switch source and apply the local seek once the new media has loaded
        (preserving the current play/pause state)."""
        if self._chapters is None:
            self.player.setPosition(int(seconds * 1000))
            return
        index, local = self._chapters.to_local(seconds)
        if index == self._current_chapter:
            if self._switching or self._pending is not None:
                # switch to THIS chapter still in flight: fold the new target into the deferred seek
                # (a direct setPosition would hit the old media / be reset), preserving the resume intent.
                resume = self._pending[2] if self._pending is not None else self.is_playing()
                self._pending = (index, local, resume)
            else:
                self.player.setPosition(int(local * 1000))
        else:
            # switch source; defer the seek (+ a resume if playing) to LoadedMedia.
            # Flag the seam BEFORE _set_source, exactly as the EndOfMedia auto-advance does: this is
            # the same reopen, so it owes the same honesty. Without it the shell's chapterChanged
            # handler renames the banner to a chapter that has NOT loaded yet, with no busy
            # affordance; the hint suppresses that rename and _apply_pending clears it.
            self.seamLoading.emit(True)
            self._pending = (index, local, self.is_playing())
            self._set_source(index)

    # ------------------------------------------------------------- lap window (compare mode)
    def set_lap_window(self, start_global: float, end_global: float):
        """Confine playback to a lap GLOBAL [start, end]: pause+clamp at end instead of session end.
        Cross-chapter auto-advance still spans seams inside the window."""
        self._lap_window = (float(start_global), float(end_global))

    def clear_lap_window(self):
        """Drop the lap window (back to whole-session playback)."""
        self._lap_window = None

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
        """Re-pin the overlay window to the video corner if it's on (cheap; called on any geometry
        change of the video widget, this pane, or the app window)."""
        if self._gmeter_on:
            self._position_gmeter()
            if self.gmeter.isVisible():
                self.gmeter.raise_()

    def set_g(self, g):
        """Feed (lat_g, long_g, total_g) to the overlay (None blanks the dot); no-op cost when
        hidden so call every tick. Also re-pins from this ~30 Hz tick (a top-level overlay does not
        track app-window drags)."""
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.set_g(g)

    def set_gmeter_source(self, source: str, long_source: str | None = None):
        self.gmeter.set_source(source, long_source)

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
        if self.video.isHidden() or vw <= 0 or vh <= 0:
            return False
        top = self.window()
        if top is None or top is self.video:
            return True
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

    def hideEvent(self, event):
        super().hideEvent(event)
        # hide the overlay so a detached top-level can't linger; _gmeter_on stays on for showEvent.
        if self._gmeter_on:
            self.gmeter.hide()

    def closeEvent(self, event):
        self.gmeter.close()   # the overlay has no parent window to close it
        super().closeEvent(event)

    def stop(self):
        """Stop the decoder AND close the overlay window — clean disposal for a reload / Phase B
        pane teardown (the overlay is a top-level window with no parent window to close it)."""
        self._seam_watchdog.stop()  # no stray seam timer firing into a torn-down player
        self.player.stop()
        self.gmeter.close()

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
                     lambda: self.player.stop(),
                     lambda: self.player.setVideoOutput(None),
                     lambda: self.player.setAudioOutput(None),
                     lambda: self.gmeter.close(),
                     lambda: self.gmeter.deleteLater(),
                     lambda: self.player.deleteLater(),
                     lambda: self.audio.deleteLater()):
            try:
                step()
            except RuntimeError:
                pass  # already torn down

    # ------------------------------------------------------------- player events
    def _on_position(self, ms: int):
        """Local media position -> global session time (so all telemetry sync sees one clock).
        Compare mode: at the window end (within tolerance) pause and clamp the emitted position to
        the end so the pane parks on the lap's last frame; cross-chapter auto-advance is untouched
        (it carries a seam-straddling lap up to that end)."""
        global_s = self._offset() + ms / 1000.0
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
        load began, then apply _pending on the next genuine LoadedMedia."""
        if self._switching:
            # the real load always passes through LoadingMedia: open the gate there.
            if status == QMediaPlayer.MediaStatus.LoadingMedia:
                self._switching = False
            return  # ignore the spurious statuses emitted before the real load

        loaded = status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        )
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
        """Apply the deferred cross-chapter seek (to `local` seconds) and optionally resume play,
        clearing _pending + the seam state. Shared by the genuine-load path and the watchdog so the
        resume is identical however it's triggered."""
        self._pending = None
        self._switching = False
        self._seam_watchdog.stop()
        self.player.setPosition(int(local * 1000))
        # resume only if the original intent was to play AND the user did not pause mid-reopen.
        if resume and not self._user_paused_during_reopen:
            self.player.play()
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

    def _on_state(self, state):
        self.playbackStateChanged.emit(state)
