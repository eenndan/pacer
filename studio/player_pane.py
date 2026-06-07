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

from PySide6.QtCore import QEvent, QPoint, QRect, QUrl, Signal
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


class PlayerPane(QWidget):
    """One single-lap player: its own decoder + video surface + audio + g-meter overlay.

    Emits `positionChanged(global_seconds)` as it plays and `chapterChanged(index)` when the
    source switches; exposes the chapter-aware `seek(global_seconds)` so the shell / map / plots
    can drive it. All playback state lives here; the shell owns only the transport chrome."""

    positionChanged = Signal(float)  # GLOBAL seconds on the session clock
    chapterChanged = Signal(int)     # current chapter index (for the UI label)
    playbackStateChanged = Signal(object)  # forwards QMediaPlayer.PlaybackState to the shell

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
        self._latest_global = 0.0  # last emitted global time (for current_global_time())
        # Compare-mode lap window: when set to (start_global, end_global) the pane plays "time
        # into lap" — it pauses + clamps at `end_global` instead of running to the end of the
        # session. None in normal mode (whole session, behaviour unchanged). The window spans
        # GLOBAL time so a lap that STARTS in one chapter and ENDS in the next still stops at the
        # right instant: cross-chapter auto-advance stays enabled WHILE inside the window, and the
        # stop fires only when the emitted global position reaches `end_global` in whatever chapter
        # that lands in. See _on_position.
        self._lap_window: tuple[float, float] | None = None

        self.video = QVideoWidget()
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
        self.player = QMediaPlayer()
        # F4: real audio output with a mute toggle. DEFAULT = muted (this is a telemetry tool —
        # avoid a surprise blast of 4K clip audio on launch). A reasonable volume is set so the
        # un-mute button is immediately audible; the toggle flips QAudioOutput.isMuted().
        self.audio = QAudioOutput()
        self.audio.setVolume(0.6)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)

        # The pane is JUST the video surface; the transport chrome lives in the shell.
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)

        self.player.positionChanged.connect(self._on_position)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        if self._chapters is not None:
            self._set_source(0)

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

    def _set_source(self, index: int):
        """Load chapter `index` as the player's source (no seek/play here — callers arrange the
        post-load seek via self._pending, applied on LoadedMedia)."""
        if self._chapters is None:
            return
        index = min(max(index, 0), len(self._chapters) - 1)
        self._current_chapter = index
        path = self._chapters.chapters[index].path
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))
        self.chapterChanged.emit(index)

    # ------------------------------------------------------------- transport
    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def play(self):
        self.player.play()

    def pause(self):
        self.player.pause()

    def toggle(self):
        if self.is_playing():
            self.player.pause()
        else:
            self.player.play()

    def set_playback_rate(self, rate: float = 1.0):
        """Passthrough to QMediaPlayer.setPlaybackRate (Phase B may roll a pane faster/slower;
        default 1.0 keeps today's behaviour)."""
        self.player.setPlaybackRate(rate)

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
            self._pending = None
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
        self.player.stop()
        self.gmeter.close()

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
        """Apply a deferred cross-chapter seek once the new source has loaded, and auto-advance
        to the next chapter at end-of-media so playback flows across the whole recording."""
        loaded = status in (
            QMediaPlayer.MediaStatus.LoadedMedia,
            QMediaPlayer.MediaStatus.BufferedMedia,
        )
        if loaded and self._pending is not None:
            index, local, resume = self._pending
            if index == self._current_chapter:
                self._pending = None
                self.player.setPosition(int(local * 1000))
                if resume:
                    self.player.play()
            return

        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            self._on_end_of_media()

    def _on_end_of_media(self):
        """Chapter i reached its end. If there's a next chapter, load it and continue playing from
        0 (seamless auto-advance); otherwise it's the true end of the session — leave it paused at
        the end. A brief reopen hitch at the seam is expected (QMediaPlayer reopens the file)."""
        if self._chapters is None:
            return
        nxt = self._current_chapter + 1
        if nxt < len(self._chapters):
            # Auto-advance: keep playing into the next chapter from its start.
            self._pending = (nxt, 0.0, True)
            self._set_source(nxt)

    def _on_state(self, state):
        # Re-emitted as playbackStateChanged for the shell to update its transport icon.
        self.playbackStateChanged.emit(state)
