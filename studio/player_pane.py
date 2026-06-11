"""PlayerPane: one self-contained single-lap video player (the reusable unit of the player).

Extracted verbatim from the original VideoView so it can be composed: a PlayerPane owns ONE
QMediaPlayer + QVideoWidget + QAudioOutput plus an attached g-meter overlay, and presents a
(possibly chaptered) recording as ONE continuous video on a GLOBAL session clock. VideoView is
now a thin shell that wraps exactly one PlayerPane and adds the transport row + slider; Phase B
will compose two panes side by side. The single-lap behaviour is byte-for-byte the legacy one.

A recording can be a single file OR a chaptered multi-file recording (a long GoPro session
split at a size limit into chapters that are contiguous in time). QMediaPlayer plays exactly
ONE source, so to present a multi-chapter recording as one continuous video this pane keeps
the ordered chapter list + cumulative offsets (a `chapters.ChapterMap`) and:

  * The emitted position is in GLOBAL session time (0..sum-of-durations), so the telemetry sync
    (cursor, map marker, plots, readout) sees one continuous clock.
  * `seek(global_t)` maps the global time to (chapter i, local_t); if chapter i isn't the
    current source it SWITCHES the source to chapter i, then seeks to local_t.
  * On `EndOfMedia` for chapter i it auto-loads chapter i+1 and keeps playing from 0, so
    playback flows ACROSS chapters with no user action (a brief reopen hitch at the seam is
    expected — QMediaPlayer reopens the file).
  * `positionChanged` (a LOCAL media position) is converted to global (+offset of the current
    chapter) before being emitted.

For a single-file recording the ChapterMap has one entry at offset 0, so global == local and
behaviour is exactly the legacy single-source path.

THE G-METER OVERLAY (load-bearing macOS behaviour — preserved exactly)
----------------------------------------------------------------------
The classic friction-circle g-meter is drawn ON the video as a frameless translucent TOP-LEVEL
window (not a plain child) so the window-server composites it ABOVE the QVideoWidget's native
video surface — a child widget is painted behind that surface on macOS and never shows on
screen. The pane pins it to the video's TOP-RIGHT corner in GLOBAL screen coords, sized as a
fraction of the video, and keeps it there as the video moves/resizes. Keeping it pinned needs
SEVEN hooks, all preserved here: installEventFilter on the video widget (Move/Resize), the
pane's own moveEvent / resizeEvent / showEvent / hideEvent / closeEvent, and the per-tick
re-pin inside set_g (a top-level window does NOT follow the parent when the WHOLE app window is
dragged, so it must be re-pinned from the ~30 Hz tick). Driven by the app via set_g / set_lap.
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

# Lap-window stop tolerance (seconds). The emitted global position is quantized to whole ms
# (QMediaPlayer.position() is ms) and frames land ~16 ms apart at 60 fps, so the playhead can
# step from just-before to just-after the window end without ever reporting it exactly. We fire
# the stop as soon as the position reaches within this tolerance of the end, so a lap end can't
# be overshot by a frame nor under-resolved by the ms quantization. Far smaller than a frame.
_WINDOW_STOP_TOL_S = 0.002

# Bounded-resume watchdog for the cross-chapter seam. The reopen is normally near-instant once the
# switch gate lets the deferred seek+resume fire on the genuine load (~0.1 s on the 12 GB chapters),
# but a recording/disk hiccup must NEVER leave the player hung. If a pending cross-chapter seek
# hasn't been applied within this budget after the source switch, the watchdog force-applies it
# (seek + resume) regardless of the status sequence, so playback always resumes within a bound.
_SEAM_RESUME_WATCHDOG_MS = 8000

# ----------------------------------------------------------------- headless / CI seam
# PACER_NO_MEDIA=1 swaps the pane's media triplet (QMediaPlayer + QAudioOutput + QVideoWidget)
# for the inert stand-ins below, at CONSTRUCTION time. Everything else — the ChapterMap, the
# deferred-seek/seam state machine, the lap-window clamp, the g-meter overlay, every signal the
# shell wires — is built exactly as in production. Purpose: the CI E2E smoke
# (`python -m studio.dev._smoke --no-video`) builds the full StudioWindow on a headless runner
# with no media/audio devices, where opening the real ffmpeg/AVFoundation pipeline blocks
# indefinitely. The flag is read once per pane construction (never on a playback path), and the
# production path is byte-identical when the variable is unset.


class _NullMediaPlayer(QObject):
    """Inert QMediaPlayer stand-in (PACER_NO_MEDIA=1): exposes the same four signals PlayerPane
    wires (they simply never fire), a no-op transport, and a permanent StoppedState — no decoder
    or media pipeline is ever created. deleteLater comes from QObject."""

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
        # Enum ACCESS on the real class is safe headless — only instantiating the backend isn't.
        return QMediaPlayer.PlaybackState.StoppedState


class _NullAudioOutput(QObject):
    """Inert QAudioOutput stand-in (PACER_NO_MEDIA=1): remembers the muted flag (the shell's
    mute toggle reads it back) and never opens an audio device. Starts muted, like production."""

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
    durationChanged = Signal("qlonglong")  # re-emits the inner QMediaPlayer.durationChanged (ms), so
    # the transport shell can listen to the pane (not the private QMediaPlayer) for the slider range.
    # Signature matches QMediaPlayer.durationChanged(qlonglong) so the re-emit connects as a slot.
    # True while a cross-chapter source switch is reopening the next chapter (the shell shows a
    # brief "loading next chapter…" indicator so a seam hitch reads as intentional); False once the
    # next chapter has loaded and the deferred seek+resume has been applied.
    seamLoading = Signal(bool)

    def __init__(self, source: str | chapters.ChapterMap | None):
        super().__init__()
        # Normalise the source into a ChapterMap (single file => a one-entry map at offset 0).
        # `None` (no video) leaves _chapters None and the player source unset.
        self._chapters: chapters.ChapterMap | None = None
        if isinstance(source, chapters.ChapterMap):
            self._chapters = source
        elif source:
            # A lone path with no media-duration table: durations aren't known here, so use a
            # 0-duration single-entry map. Global==local for one chapter regardless of duration,
            # so playback/seek is correct; only `total_duration` (unused for one file) is 0.
            self._chapters = chapters.ChapterMap([source], [0.0])

        self._current_chapter = 0  # index into self._chapters.chapters of the loaded source
        # A seek requested while the source is still loading (setSource is async): applied when
        # the media reaches LoadedMedia. (chapter_index, local_seconds, resume_playing). This
        # also covers the auto-advance (EndOfMedia -> load next chapter at local 0, keep playing),
        # so a stale EndOfMedia from the old source while a switch is pending is safely ignored.
        self._pending: tuple[int, float, bool] | None = None
        # A source switch is in flight: setSource() is async AND the Qt/FFmpeg backend SYNCHRONOUSLY
        # re-emits the OLD source's leftover statuses (a spurious LoadedMedia, and a stale EndOfMedia)
        # the instant setSource is called — BEFORE it has begun parsing the new file. Honouring the
        # pending seek+play on that spurious LoadedMedia consumes _pending, then the REAL load (which
        # always passes through LoadingMedia) resets the player and the seek+play is silently
        # discarded — the player sits idle forever (the chapter-seam stall). So a switch is gated:
        # we ignore loaded/end statuses until the load genuinely begins (a LoadingMedia transition,
        # or a real load completion arriving after the synchronous burst), then apply _pending once.
        self._switching = False
        # Set when the user deliberately pauses DURING a seam reopen (a switch is in flight / a
        # deferred seek+resume is pending): the deferred resume captured the play-state from BEFORE
        # the pause, so honouring it on the genuine load (or via the watchdog) would override the
        # user's pause and resume playback they explicitly stopped. This flag makes _apply_pending
        # skip the play() when the user paused mid-reopen, so a deliberate pause survives the seam.
        self._user_paused_during_reopen = False
        self._latest_global = 0.0  # last emitted global time (for current_global_time())
        # Compare-mode lap window: when set to (start_global, end_global) the pane plays "time
        # into lap" — it pauses + clamps at `end_global` instead of running to the end of the
        # session. None in normal mode (whole session, behaviour unchanged). The window spans
        # GLOBAL time so a lap that STARTS in one chapter and ENDS in the next still stops at the
        # right instant: cross-chapter auto-advance stays enabled WHILE inside the window, and the
        # stop fires only when the emitted global position reaches `end_global` in whatever chapter
        # that lands in. See _on_position.
        self._lap_window: tuple[float, float] | None = None

        # Headless / CI seam (see the _Null* stand-ins above): PACER_NO_MEDIA=1 builds the pane
        # with an inert media triplet instead of the decoder/audio/video-surface stack. Read once,
        # here, at construction — no playback path ever re-checks it.
        no_media = os.environ.get("PACER_NO_MEDIA") == "1"
        self.video = QWidget() if no_media else QVideoWidget()
        # Classic friction-circle g-meter, drawn ON the video. It is a frameless translucent
        # TOP-LEVEL window (not a plain child) so the window-server composites it ABOVE the
        # QVideoWidget's native video surface — a child widget is painted behind that surface on
        # macOS and never shows on screen. The pane pins it to the video's TOP-RIGHT corner in
        # GLOBAL screen coords, sized as a fraction of the video, and keeps it there as the video
        # moves/resizes. Driven by app.set_g at the ~30 Hz tick from session.g_at_time.
        self.gmeter = GMeterOverlay(self)
        self.gmeter.hide()  # off by default; the toggle reveals it
        self._gmeter_on = False
        # Keep the overlay window pinned to the video corner: the QVideoWidget's resize/move
        # changes its on-screen rect, and dragging the whole app window moves it too. We watch
        # the video widget (resize/move) here; the pane's own move/resize are caught in our
        # moveEvent/resizeEvent (a top-level overlay doesn't follow the parent automatically).
        self.video.installEventFilter(self)
        if no_media:
            self.player = _NullMediaPlayer()
            self.audio = _NullAudioOutput()
        else:
            self.player = QMediaPlayer()
            # F4: real audio output with a mute toggle. DEFAULT = muted (this is a telemetry tool —
            # avoid a surprise blast of 4K clip audio on launch). A reasonable volume is set so the
            # un-mute button is immediately audible; the toggle flips QAudioOutput.isMuted().
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
        # Re-emit duration changes from the pane so the transport shell can wire to the pane's
        # public signal rather than reaching into the private QMediaPlayer.
        self.player.durationChanged.connect(self.durationChanged)

        # Bounded-resume watchdog: a one-shot armed when a cross-chapter switch defers a seek+resume,
        # disarmed the instant the seek is applied on the genuine load. If it ever fires, the reopen
        # took longer than the budget (a disk/recording hiccup) — force-apply the pending seek+resume
        # so playback can NEVER hang at a seam. Parented to the pane so it's torn down with it.
        self._seam_watchdog = QTimer(self)
        self._seam_watchdog.setSingleShot(True)
        self._seam_watchdog.setInterval(_SEAM_RESUME_WATCHDOG_MS)
        self._seam_watchdog.timeout.connect(self._on_seam_watchdog)

        if self._chapters is not None:
            # Initial load is NOT a source replacement: don't arm the switch gate (there are no
            # leftover statuses to suppress, and no deferred seek to arm the watchdog — a gate left
            # armed because the first load skipped LoadingMedia would hang the pane forever).
            self._set_source(0, switching=False)

    # ------------------------------------------------------------- source mgmt
    @property
    def is_multi(self) -> bool:
        return self._chapters is not None and self._chapters.is_multi

    @property
    def total_duration(self) -> float:
        """Total session duration in seconds (sum of chapter durations), or 0 if unknown."""
        return self._chapters.total_duration if self._chapters is not None else 0.0

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

    def _set_source(self, index: int, switching: bool = True):
        """Load chapter `index` as the player's source (no seek/play here — callers arrange the
        post-load seek via self._pending, applied once the NEW media has genuinely loaded).

        `switching` arms the switch gate (_switching): a source REPLACEMENT must ignore the OLD
        source's leftover LoadedMedia/EndOfMedia that the backend re-emits synchronously inside
        setSource(), before it parses the new file (see _switching / the gate). The INITIAL load has
        no prior source, so it passes switching=False — arming the gate there would HANG the pane
        permanently if the first load skips the LoadingMedia transition that disarms it (there is no
        deferred seek on the initial load, so the watchdog isn't armed to rescue it either)."""
        if self._chapters is None:
            return
        index = min(max(index, 0), len(self._chapters) - 1)
        self._current_chapter = index
        # Arm the switch gate BEFORE setSource so the synchronous spurious statuses it emits are
        # ignored — but ONLY for an actual source replacement; the initial load must not gate (it
        # has no leftover statuses to suppress and no watchdog to un-stick it).
        self._switching = switching
        path = self._chapters.chapters[index].path
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self.chapterChanged.emit(index)
        # If this switch defers a seek+resume, arm the bounded-resume watchdog so a stuck reopen
        # can't hang playback. Restarted here (idempotent) and stopped when _pending is applied.
        if self._pending is not None:
            self._seam_watchdog.start()

    # ------------------------------------------------------------- transport
    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play(self):
        # An explicit play() is the latest user intent and overrides a pause made earlier DURING a
        # seam reopen: clear the "user paused mid-reopen" flag so the deferred seek+resume (and the
        # watchdog) honour PLAY when the genuine load lands. Without this, a pause-then-play during a
        # reopen stayed paused — the flag set by the pause survived and _apply_pending skipped the
        # resume, so play-after-pause-during-reopen never resumed.
        self._user_paused_during_reopen = False
        # If a lap window is set and the pane is parked at/after the window end (it auto-paused
        # there on the last play), a bare play() would be a dead no-op — the playhead is already at
        # the stop point, so it pauses again on the very next position tick. Seek back to the window
        # start first so Play actually rolls the lap again. (Normal mode / mid-window: unchanged.)
        win = self._lap_window
        if win is not None and self.current_global_time() >= win[1] - _WINDOW_STOP_TOL_S:
            self.seek(win[0])
        self.player.play()

    def pause(self):
        # If the user pauses while a seam reopen is in flight, remember it so the deferred
        # seek+resume (which captured the play-state from before this pause) doesn't override the
        # pause and resume playback on the genuine load / watchdog.
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
                # A source switch to THIS chapter is in flight (its real load hasn't arrived yet):
                # an immediate setPosition would land on the old, not-yet-replaced media (or be
                # discarded when the real load resets the player) AND clearing _pending here would
                # drop the deferred seam seek+resume — re-introducing the chapter-seam stall. So
                # FOLD the new local target into the deferred seek instead of bypassing the gate,
                # preserving the captured resume intent so playback still resumes on the real load.
                resume = self._pending[2] if self._pending is not None else self.is_playing()
                self._pending = (index, local, resume)
            else:
                self.player.setPosition(int(local * 1000))
        else:
            # Switch source; defer the seek (and a resume if currently playing) to LoadedMedia.
            self._pending = (index, local, self.is_playing())
            self._set_source(index)

    # ------------------------------------------------------------- lap window (compare mode)
    def set_lap_window(self, start_global: float, end_global: float):
        """Confine playback to ONE lap's GLOBAL [start, end] window (compare mode's "time into
        lap"): the pane plays at 1× from the lap start and pauses + clamps at the lap end instead
        of running to the session's end. Cross-chapter auto-advance is UNAFFECTED — a lap that
        spans a chapter seam still plays across it (the EndOfMedia handler keeps advancing while
        inside the window); only the window END triggers the stop, in whatever chapter it lands in.
        Caller seeks the pane to the lap start (a hair in) separately."""
        self._lap_window = (float(start_global), float(end_global))

    def clear_lap_window(self):
        """Drop the lap window — back to whole-session playback (normal mode). Behaviour after
        this is byte-identical to a pane that never had a window set."""
        self._lap_window = None

    # ------------------------------------------------------------- g-meter overlay
    def set_gmeter_visible(self, on: bool):
        """Show/hide the friction-circle g-meter overlay (driven by the shell's toggle button)."""
        self._gmeter_on = bool(on)
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.show()
            self.gmeter.raise_()
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
        change of the video widget or this pane)."""
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.raise_()

    def set_g(self, g):
        """Feed the current (lateral_g, longitudinal_g, total_g) to the overlay (None blanks the
        live dot). A no-op repaint cost when the overlay is hidden, so the app can call it every
        tick unconditionally. Also keeps the overlay window pinned to the video corner: a top-level
        window does NOT follow the parent when the WHOLE app window is dragged (no child moveEvent),
        so re-pin from this existing ~30 Hz tick (cheap — _position_gmeter only moves it if the
        corner actually changed)."""
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.set_g(g)

    def set_gmeter_source(self, source: str):
        self.gmeter.set_source(source)

    def set_gmeter_lap(self, lap_id):
        """Tell the overlay which lap is being driven so its max-G envelope resets at the lap
        boundary (per-lap grip-usage scope). A no-op repaint cost when the overlay is hidden."""
        self.gmeter.set_lap(lap_id)

    def _position_gmeter(self):
        """Pin the overlay window to the video's TOP-RIGHT corner, in GLOBAL screen coords (the
        overlay is a top-level window, so it does NOT inherit the video's coordinate space), sized
        as a FRACTION of the video so it scales on resize. Called on show and whenever the video
        widget or this pane moves/resizes."""
        vw, vh = self.video.width(), self.video.height()
        # Width = a fraction of the video, clamped to a sensible legible range; height follows the
        # overlay's aspect. Cap both to the available video area so it never overflows a tiny video.
        w = int(min(max(vw * _OVERLAY_FRAC, _OVERLAY_MIN_W), _OVERLAY_MAX_W,
                    max(vw - 2 * _OVERLAY_PAD, 1)))
        h = int(min(w * _OVERLAY_ASPECT, max(vh - 2 * _OVERLAY_PAD, 1)))
        # Top-right corner of the video widget, mapped to the screen.
        corner = self.video.mapToGlobal(QPoint(vw - w - _OVERLAY_PAD, _OVERLAY_PAD))
        rect = QRect(corner.x(), corner.y(), w, h)
        # Only move it if the target actually changed — _position_gmeter runs on the 30 Hz tick, so
        # an unconditional setGeometry every frame would needlessly churn (and can flicker).
        if self.gmeter.geometry() != rect:
            self.gmeter.setGeometry(rect)

    # --- the SEVEN hooks that keep the top-level overlay pinned to the video corner ---
    def eventFilter(self, obj, event):
        # (1) Keep the g-meter overlay window pinned to the QVideoWidget's corner as the video
        # moves or resizes (the layout/splitters resize it; the native surface can also emit Move).
        if obj is self.video and event.type() in (QEvent.Resize, QEvent.Move):
            self._sync_gmeter()
        return super().eventFilter(obj, event)

    # (2)/(3) Re-pin the overlay window when this pane itself moves/resizes (e.g. the user drags
    # the main window or moves a splitter) — a top-level overlay doesn't track its parent.
    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_gmeter()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_gmeter()

    def showEvent(self, event):
        super().showEvent(event)
        # (4) Re-show + re-pin the overlay if it's meant to be on (it was hidden in hideEvent while
        # the pane was hidden); a bare _sync_gmeter would reposition but not bring it back.
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.show()
            self.gmeter.raise_()

    def hideEvent(self, event):
        # (5) When the pane is hidden (e.g. window minimised / reload teardown) hide the overlay
        # window too, so a detached top-level can't linger on screen. _gmeter_on is left ON so
        # showEvent brings it back.
        super().hideEvent(event)
        if self._gmeter_on:
            self.gmeter.hide()

    def closeEvent(self, event):
        # (6) Ensure the top-level overlay window is destroyed with the pane (it has no parent
        # window to close it). Belt-and-braces for the reload teardown.
        self.gmeter.close()
        super().closeEvent(event)
    # (7) the per-tick re-pin lives in set_g above (the ~30 Hz tick re-pins on app-window drags).

    def stop(self):
        """Stop the decoder AND close the overlay window — clean disposal for a reload / Phase B
        pane teardown (the overlay is a top-level window with no parent window to close it)."""
        self._seam_watchdog.stop()  # no stray seam timer firing into a torn-down player
        self.player.stop()
        self.gmeter.close()

    def dispose(self):
        """Fully release this pane's decoder + audio + overlay for a compare teardown / reload.

        The QMediaPlayer and QAudioOutput are plain attributes (NOT Qt children of the pane), so
        deleting the pane alone does NOT free them — they would linger until Python GC, holding a
        decoder open. So: stop the decoder, detach the video/audio sinks, close the overlay window,
        and explicitly deleteLater() the player + audio so the FFmpeg decoder + the audio device
        are released promptly. The pane widget itself is deleteLater'd by the caller. Idempotent."""
        self._seam_watchdog.stop()  # no stray seam timer firing into a deleted player
        try:
            self.player.stop()
            self.player.setVideoOutput(None)
            self.player.setAudioOutput(None)
        except RuntimeError:
            pass  # already torn down
        self.gmeter.close()
        self.gmeter.deleteLater()
        self.player.deleteLater()
        self.audio.deleteLater()

    # ------------------------------------------------------------- player events
    def _on_position(self, ms: int):
        """Local media position -> global session time. The emitted position is global, so all
        telemetry sync sees one continuous clock spanning every chapter.

        Compare mode (a lap window is set): once the GLOBAL position reaches the window END
        (within a sub-frame tolerance — the position is ms-quantized and frames land ~16 ms apart,
        so it can step from just-before to just-after the end without reporting it exactly), pause
        and clamp the EMITTED position to the end so the pane parks exactly on the lap's last frame
        rather than overshooting. The cross-chapter machine is untouched: this only stops at the
        window end, wherever that falls — auto-advance still carries a seam-straddling lap across
        the chapter boundary up TO that end."""
        global_s = self._offset() + ms / 1000.0
        win = self._lap_window
        if win is not None and global_s >= win[1] - _WINDOW_STOP_TOL_S:
            # Reached (or stepped just past) the lap end: stop here and report the clamped end so
            # downstream sync never sees a time beyond the lap. Pausing is idempotent.
            if self.is_playing():
                self.player.pause()
            global_s = win[1]
        self._latest_global = global_s
        self.positionChanged.emit(global_s)

    def _on_media_status(self, status):
        """Apply a deferred cross-chapter seek once the new source has GENUINELY loaded, and
        auto-advance to the next chapter at end-of-media so playback flows across the whole
        recording.

        THE CHAPTER-SEAM GATE (_switching). setSource() is async, but the Qt/FFmpeg backend
        SYNCHRONOUSLY re-emits the OLD source's leftover statuses the instant setSource is called —
        a spurious LoadedMedia and a stale EndOfMedia — BEFORE it begins parsing the new file. The
        real load then arrives a beat later as LoadingMedia -> LoadedMedia/BufferedMedia. If we
        honour _pending on that spurious LoadedMedia we consume it, the subsequent real load resets
        the player, and the seek+resume play() is silently discarded — playback never resumes (the
        seam stall). So while _switching is set we IGNORE every status until LoadingMedia confirms
        the real load has begun; only then is the next LoadedMedia/BufferedMedia the genuine one we
        apply _pending on. The stale EndOfMedia is likewise ignored while switching."""
        if self._switching:
            # The real load always passes through LoadingMedia; that transition marks the end of the
            # synchronous spurious burst. Open the gate there and wait for the genuine load.
            if status == QMediaPlayer.MediaStatus.LoadingMedia:
                self._switching = False
            # Ignore the spurious LoadedMedia/EndOfMedia emitted before the real load begins.
            return

        loaded = status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        )
        if loaded and self._pending is not None:
            index, local, resume = self._pending
            if index == self._current_chapter:
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
        # Honour a deliberate pause made DURING the reopen: the captured resume intent predates that
        # pause, so a user who paused mid-seam stays paused (combine both — only resume if the
        # original intent was to play AND the user did not pause during the reopen).
        if resume and not self._user_paused_during_reopen:
            self.player.play()
        self._user_paused_during_reopen = False
        # Seam reopen finished: drop the "loading next chapter…" indicator.
        self.seamLoading.emit(False)

    def _on_seam_watchdog(self):
        """Bounded-resume fallback: the deferred cross-chapter seek wasn't applied within the budget
        (a slow/hiccuping reopen). Force-apply it so playback resumes regardless of the status
        sequence — playback must NEVER hang at a seam. No-op if _pending was already cleared."""
        if self._pending is None:
            return
        index, local, resume = self._pending
        # Only force-apply if the intended chapter is in fact the loaded source (it is, since
        # _set_source set _current_chapter synchronously); apply the seek+resume regardless of gate.
        if index == self._current_chapter:
            self._apply_pending(local, resume)

    def _on_end_of_media(self):
        """Chapter i reached its end. If there's a next chapter, load it and continue playing from
        0 (seamless auto-advance); otherwise it's the true end of the session — leave it paused at
        the end. The reopen is fast (the gate in _on_media_status makes the deferred seek+resume
        fire on the GENUINE load), but signal seamLoading so the shell can show a brief, clearly
        styled "loading next chapter…" hint for the reopen window."""
        if self._chapters is None:
            return
        nxt = self._current_chapter + 1
        if nxt < len(self._chapters):
            # Auto-advance: keep playing into the next chapter from its start.
            self.seamLoading.emit(True)
            self._pending = (nxt, 0.0, True)
            self._set_source(nxt)

    def _on_state(self, state):
        # Re-emitted as playbackStateChanged for the shell to update its transport icon.
        self.playbackStateChanged.emit(state)
