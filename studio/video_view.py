"""VideoView: the GoPro clip, with a play/pause button and a scrub slider.

Emits `positionChanged(seconds)` as it plays (drives the map marker + plot cursor),
and exposes `seek(seconds)` so the map/plots can drive the video. Sync stays in Python
via QMediaPlayer.positionChanged (the spike confirmed frame-accurate QVideoSink.startTime
is also available if sub-frame precision is ever needed).
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


class VideoView(QWidget):
    positionChanged = Signal(float)  # seconds on the media clock

    def __init__(self, path: str | None):
        super().__init__()
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

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(lambda ms: self.player.setPosition(ms))

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
        self.player.durationChanged.connect(self.slider.setMaximum)
        self.player.playbackStateChanged.connect(self._on_state)

        if path:
            self.player.setSource(QUrl.fromLocalFile(os.path.abspath(path)))

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
        self.player.setPosition(int(seconds * 1000))

    def set_readout(self, text: str):
        self.readout.setText(text)

    def _on_position(self, ms: int):
        self.slider.blockSignals(True)
        self.slider.setValue(ms)
        self.slider.blockSignals(False)
        self.positionChanged.emit(ms / 1000.0)

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸ Pause" if playing else "▶ Play")
