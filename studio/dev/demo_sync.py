#!/usr/bin/env python3
"""Live demo: GoPro video <-> telemetry plot sync in pure Python (PySide6 + pyqtgraph).

A real window proving the studio app's core interaction:
  * left  : the GoPro .mp4 playing in a QVideoWidget (Play/Pause button).
  * right : speed-vs-time plotted from pacer's GPMF telemetry, with a draggable
            vertical cursor (pyqtgraph InfiniteLine).
  * sync  : video playback moves the cursor (player.positionChanged -> cursor);
            dragging the cursor seeks the video (cursor.sigDragged -> setPosition).
            All sync logic is Python -- no JavaScript, no server.

Run:  pixi run python studio/demo_sync.py [path/to/gopro.mp4]
(Defaults to the stationary hero8 test clip -- speed is ~flat, but the cursor
 still sweeps; pass one of YOUR GoPro files to see real speed variation.)
"""

import bisect
import sys

import pyqtgraph as pg
from PySide6.QtCore import Qt, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

import pacer

VIDEO = sys.argv[1] if len(sys.argv) > 1 else "3rdparty/gpmf-parser/samples/hero8.mp4"


def telemetry(path):
    """(time_s, speed_kmh) per GPS sample on the MP4 clock (mirrors timeline.cpp)."""
    src = pacer.GPMFSource(path)
    times, speeds = [], []
    src.seek(0)
    while not src.is_end():
        start, end = src.current_time_span()
        chunk = []
        src.read_samples(lambda s, i, n: chunk.append((s, i, n)))
        for s, i, n in chunk:
            times.append(start + (end - start) * (i / n if n else 0.0))
            speeds.append(s.full_speed * 3.6)
        src.next()
    return times, speeds


class SyncDemo(QWidget):
    def __init__(self, path):
        super().__init__()
        self.setWindowTitle(f"pacer studio — video↔telemetry sync  ({path})")
        self.resize(1180, 540)

        self.t, self.v = telemetry(path)

        # --- video (left) ---
        self.video = QVideoWidget()
        self.player = QMediaPlayer()
        self.audio = QAudioOutput()
        self.audio.setVolume(0.0)  # muted so the demo isn't noisy; bump to hear it
        self.player.setAudioOutput(self.audio)
        self.player.setVideoOutput(self.video)
        self.player.setSource(QUrl.fromLocalFile(path))

        self.play_btn = QPushButton("▶ Play")
        self.play_btn.clicked.connect(self.toggle)

        left = QVBoxLayout()
        left.addWidget(self.video, 1)
        left.addWidget(self.play_btn)

        # --- plot (right) ---
        pg.setConfigOptions(antialias=True)
        self.plot = pg.PlotWidget(title="Speed (km/h) vs time (s) — drag the cursor")
        self.plot.showGrid(x=True, y=True, alpha=0.3)
        self.plot.plot(self.t, self.v, pen=pg.mkPen("#39a0ed", width=2))
        self.cursor = pg.InfiniteLine(
            angle=90, movable=True, pen=pg.mkPen("#ff5252", width=2),
            label="{value:.2f}s", labelOpts={"position": 0.05, "color": "#ff5252"},
        )
        self.cursor.setValue(self.t[0] if self.t else 0.0)
        self.plot.addItem(self.cursor)
        self.readout = QLabel("—")
        self.readout.setAlignment(Qt.AlignCenter)

        right = QVBoxLayout()
        right.addWidget(self.plot, 1)
        right.addWidget(self.readout)

        root = QHBoxLayout(self)
        root.addLayout(left, 1)
        root.addLayout(right, 1)

        # --- bidirectional sync ---
        # video -> cursor (setValue emits sigPositionChanged, NOT sigDragged, so no loop)
        self.player.positionChanged.connect(self.on_position)
        self.player.playbackStateChanged.connect(self.on_state)
        # cursor -> video (only fires on real mouse drags)
        self.cursor.sigDragged.connect(self.on_drag)

    def toggle(self):
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setText("⏸ Pause" if playing else "▶ Play")

    def on_position(self, ms):
        t = ms / 1000.0
        self.cursor.setValue(t)
        self.update_readout(t)

    def on_drag(self):
        t = self.cursor.value()
        if self.t:
            t = min(max(t, 0.0), self.t[-1])
        self.player.setPosition(int(t * 1000))
        self.update_readout(t)

    def update_readout(self, t):
        if not self.t:
            return
        j = min(max(bisect.bisect_left(self.t, t), 0), len(self.t) - 1)
        self.readout.setText(f"t = {t:5.2f} s     speed = {self.v[j]:5.1f} km/h     sample #{j}")


def main():
    app = QApplication(sys.argv)
    w = SyncDemo(VIDEO)
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
