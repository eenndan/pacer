"""VideoView: the GoPro clip(s), with a play/pause button and a scrub slider.

A recording can be a single file OR a chaptered multi-file recording (a long GoPro session
split at a size limit into chapters that are contiguous in time). QMediaPlayer plays exactly
ONE source, so to present a multi-chapter recording as one continuous video this view keeps
the ordered chapter list + cumulative offsets (a `chapters.ChapterMap`) and:

  * The slider + the emitted position are in GLOBAL session time (0..sum-of-durations), so
    the telemetry sync (cursor, map marker, plots, readout) sees one continuous clock.
  * `seek(global_t)` maps the global time to (chapter i, local_t); if chapter i isn't the
    current source it SWITCHES the source to chapter i, then seeks to local_t.
  * On `EndOfMedia` for chapter i it auto-loads chapter i+1 and keeps playing from 0, so
    playback flows ACROSS chapters with no user action (a brief reopen hitch at the seam is
    expected — QMediaPlayer reopens the file).
  * `positionChanged` (a LOCAL media position) is converted to global (+offset of the current
    chapter) before being emitted.

For a single-file recording the ChapterMap has one entry at offset 0, so global == local and
behaviour is exactly the legacy single-source path.

Emits `positionChanged(global_seconds)` as it plays, and exposes `seek(global_seconds)` so the
map/plots can drive the video. Sync stays in Python via QMediaPlayer.positionChanged.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import chapters, theme
from .gmeter_overlay import GMeterOverlay

# Phosphor (qtawesome `ph` prefix) glyphs for the transport bar, themed via theme.icon.
_ICON_PX = 18                       # glyph render size inside the buttons
_ICON_BTN = QSize(32, 30)           # compact square-ish icon button

# The g-meter overlay sits in the TOP-RIGHT corner of the video, sized as a FRACTION of the
# video widget (not a fixed size) so it scales with the window and never dominates the frame.
_OVERLAY_FRAC = 0.22        # target width = this fraction of the video width
_OVERLAY_ASPECT = 1.12      # height / width (a touch taller than wide: title + dial + numbers)
_OVERLAY_MIN_W = 120        # don't shrink below something legible
_OVERLAY_MAX_W = 240        # don't grow huge on a very wide window
_OVERLAY_PAD = 12           # px inset from the video corner


class VideoView(QWidget):
    positionChanged = Signal(float)  # GLOBAL seconds on the session clock
    chapterChanged = Signal(int)     # current chapter index (for the UI label)

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

        self.video = QVideoWidget()
        # Classic friction-circle g-meter, drawn ON the video. It is a frameless translucent
        # TOP-LEVEL window (not a plain child) so the window-server composites it ABOVE the
        # QVideoWidget's native video surface — a child widget is painted behind that surface on
        # macOS and never shows on screen. The VideoView pins it to the video's TOP-RIGHT corner
        # in GLOBAL screen coords, sized as a fraction of the video, and keeps it there as the
        # video moves/resizes. Driven by app.set_g at the ~30 Hz tick from session.g_at_time.
        self.gmeter = GMeterOverlay(self)
        self.gmeter.hide()  # off by default; the toggle reveals it
        self._gmeter_on = False
        # Keep the overlay window pinned to the video corner: the QVideoWidget's resize/move
        # changes its on-screen rect, and dragging the whole app window moves it too. We watch
        # the video widget (resize/move) here; VideoView's own move/resize are caught in our
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

        # Compact Phosphor-icon transport buttons (no text). Icons are themed via theme.icon and
        # set ONCE per state change in the existing handlers — never on the playback tick.
        self.play_btn = QPushButton()
        self.play_btn.setIcon(theme.icon("ph.play-fill"))
        self.play_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.play_btn.setFixedSize(_ICON_BTN)
        self.play_btn.setToolTip("Play / pause")
        self.play_btn.clicked.connect(self.toggle)

        # F4: mute/unmute toggle. speaker-x while muted (default), speaker-high while audible.
        self.mute_btn = QPushButton()
        self.mute_btn.setIcon(theme.icon("ph.speaker-simple-x"))
        self.mute_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.mute_btn.setFixedSize(_ICON_BTN)
        self.mute_btn.setToolTip("Audio muted — click to unmute")
        self.mute_btn.clicked.connect(self.toggle_mute)

        # g-meter show/hide toggle (the friction-circle overlay on the video). Checkable: the QSS
        # :checked rule tints the button accent; we also recolour the GLYPH to C.accent when on.
        self.gmeter_btn = QPushButton()
        self.gmeter_btn.setIcon(theme.icon("ph.gauge"))
        self.gmeter_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.gmeter_btn.setFixedSize(_ICON_BTN)
        self.gmeter_btn.setCheckable(True)
        self.gmeter_btn.setToolTip("Show/hide the g-meter overlay")
        self.gmeter_btn.toggled.connect(self._on_gmeter_toggled)
        self.gmeter_btn.toggled.connect(self.set_gmeter_visible)

        # The slider spans the WHOLE session (global ms 0..total). For a multi-chapter recording
        # its range is the summed duration; for a single file it's the file's own duration. The
        # value is always GLOBAL ms.
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        if self._chapters is not None and self._chapters.total_duration > 0:
            self.slider.setRange(0, int(self._chapters.total_duration * 1000))

        row = QHBoxLayout()
        row.addWidget(self.play_btn)
        row.addWidget(self.mute_btn)
        row.addWidget(self.gmeter_btn)
        row.addWidget(self.slider, 1)

        self.readout = QLabel("")  # F2: time / speed / current lap, driven by app
        self.readout.setObjectName("Readout")  # caption style, dimmed, tabular (global QSS)
        self.readout.setAlignment(Qt.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.video, 1)
        lay.addLayout(row)
        lay.addWidget(self.readout)

        self.player.positionChanged.connect(self._on_position)
        self.player.durationChanged.connect(self._on_duration)
        self.player.playbackStateChanged.connect(self._on_state)
        self.player.mediaStatusChanged.connect(self._on_media_status)

        if self._chapters is not None:
            self._set_source(0)

    # ------------------------------------------------------------- source mgmt
    @property
    def is_multi(self) -> bool:
        return self._chapters is not None and self._chapters.is_multi

    def current_chapter(self) -> int:
        return self._current_chapter

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

    def toggle(self):
        if self.is_playing():
            self.player.pause()
        else:
            self.player.play()

    def toggle_mute(self):
        """F4: flip the audio mute state and update the button icon/tooltip."""
        muted = not self.audio.isMuted()
        self.audio.setMuted(muted)
        self.mute_btn.setIcon(theme.icon("ph.speaker-simple-x" if muted
                                         else "ph.speaker-simple-high"))
        self.mute_btn.setToolTip("Audio muted — click to unmute" if muted
                                 else "Audio on — click to mute")

    def _on_gmeter_toggled(self, on: bool):
        """Recolour the g-meter glyph to the accent when the overlay is active (the QSS already
        tints the button background on :checked). Separate from set_gmeter_visible so the icon
        recolour stays a pure visual concern."""
        self.gmeter_btn.setIcon(theme.icon("ph.gauge", color=theme.C.accent if on
                                           else theme.C.text))

    # ------------------------------------------------------------- g-meter overlay
    def set_gmeter_visible(self, on: bool):
        """Show/hide the friction-circle g-meter overlay (the toggle button)."""
        self._gmeter_on = bool(on)
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.show()
            self.gmeter.raise_()
        else:
            self.gmeter.hide()
        if self.gmeter_btn.isChecked() != self._gmeter_on:
            self.gmeter_btn.setChecked(self._gmeter_on)

    def _sync_gmeter(self):
        """Re-pin the overlay window to the video corner if it's on (cheap; called on any geometry
        change of the video widget or this view)."""
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
        widget or this view moves/resizes."""
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

    def eventFilter(self, obj, event):
        # Keep the g-meter overlay window pinned to the QVideoWidget's corner as the video moves
        # or resizes (the layout/splitters resize it; the native surface can also emit Move).
        if obj is self.video and event.type() in (QEvent.Resize, QEvent.Move):
            self._sync_gmeter()
        return super().eventFilter(obj, event)

    # Re-pin the overlay window when this view itself moves/resizes (e.g. the user drags the main
    # window or moves a splitter) — a top-level overlay doesn't track its parent automatically.
    def moveEvent(self, event):
        super().moveEvent(event)
        self._sync_gmeter()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_gmeter()

    def showEvent(self, event):
        super().showEvent(event)
        # Re-show + re-pin the overlay if it's meant to be on (it was hidden in hideEvent while the
        # view was hidden); a bare _sync_gmeter would reposition but not bring it back.
        if self._gmeter_on:
            self._position_gmeter()
            self.gmeter.show()
            self.gmeter.raise_()

    def hideEvent(self, event):
        # When the video view is hidden (e.g. window minimised / reload teardown) hide the overlay
        # window too, so a detached top-level can't linger on screen. _gmeter_on is left ON so
        # showEvent brings it back.
        super().hideEvent(event)
        if self._gmeter_on:
            self.gmeter.hide()

    def closeEvent(self, event):
        # Ensure the top-level overlay window is destroyed with the view (it has no parent window
        # to close it). Belt-and-braces for the reload teardown.
        self.gmeter.close()
        super().closeEvent(event)

    def is_playing(self) -> bool:
        return self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def pause(self):
        self.player.pause()

    def play(self):
        self.player.play()

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

    def _on_slider_moved(self, ms: int):
        # The slider value is GLOBAL ms — route it through the chapter-aware seek.
        self.seek(ms / 1000.0)

    def set_readout(self, text: str):
        self.readout.setText(text)

    # ------------------------------------------------------------- player events
    def _on_position(self, ms: int):
        """Local media position -> global session time. The slider + emitted position are global,
        so all telemetry sync sees one continuous clock spanning every chapter."""
        global_s = self._offset() + ms / 1000.0
        self.slider.blockSignals(True)
        self.slider.setValue(int(global_s * 1000))
        self.slider.blockSignals(False)
        self.positionChanged.emit(global_s)

    def _on_duration(self, ms: int):
        """A per-chapter duration arrives as each source loads. Keep the slider spanning the WHOLE
        session: when the ChapterMap already knows the total (multi-chapter, durations from the
        GPMF), use that; otherwise (a lone file with unknown duration) fall back to this file's
        own duration so the single-file slider still works."""
        if self._chapters is not None and self._chapters.total_duration > 0:
            self.slider.setMaximum(int(self._chapters.total_duration * 1000))
        else:
            self.slider.setMaximum(ms)

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
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(theme.icon("ph.pause-fill" if playing else "ph.play-fill"))
