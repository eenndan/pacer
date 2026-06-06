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

from PySide6.QtCore import Qt, QUrl, Signal
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

from . import chapters


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
        self.player = QMediaPlayer()
        # F4: real audio output with a mute toggle. DEFAULT = muted (this is a telemetry tool —
        # avoid a surprise blast of 4K clip audio on launch). A reasonable volume is set so the
        # un-mute button is immediately audible; the toggle flips QAudioOutput.isMuted().
        self.audio = QAudioOutput()
        self.audio.setVolume(0.6)
        self.audio.setMuted(True)
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)

        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setFixedWidth(90)
        self.play_btn.clicked.connect(self.toggle)

        # F4: mute/unmute toggle. Shows 🔇 while muted (default), 🔊 while audible.
        self.mute_btn = QPushButton("🔇")
        self.mute_btn.setFixedWidth(44)
        self.mute_btn.setToolTip("Audio muted — click to unmute")
        self.mute_btn.clicked.connect(self.toggle_mute)

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
        row.addWidget(self.slider, 1)

        self.readout = QLabel("")  # F2: time / speed / current lap, driven by app
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
        self.mute_btn.setText("🔇" if muted else "🔊")
        self.mute_btn.setToolTip("Audio muted — click to unmute" if muted
                                 else "Audio on — click to mute")

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
        self.play_btn.setText("⏸ Pause" if playing else "▶ Play")
