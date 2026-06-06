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
        self.audio = QAudioOutput()
        self.audio.setVolume(0.0)  # muted by default
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)

        self.play_btn = QPushButton("▶ Play")
        self.play_btn.setFixedWidth(90)
        self.play_btn.clicked.connect(self.toggle)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(lambda ms: self.player.setPosition(ms))

        row = QHBoxLayout()
        row.addWidget(self.play_btn)
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
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
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
