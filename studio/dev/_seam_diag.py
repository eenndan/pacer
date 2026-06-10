"""Throwaway seam-crossing diagnostic: load the full 0060 recording, seek to just before
chapter 0's end, play, and timestamp the ENTIRE media-status / playback-state / position
sequence across the chapter seam (EndOfMedia -> reopen next chapter -> resume).

Run: env -u VIRTUAL_ENV pixi run python -m studio.dev._seam_diag [--near SECONDS_BEFORE_END]

It runs OFFSCREEN but with the real GStreamer/FFmpeg decode (the offscreen platform still
decodes; only the surface is headless), so the reopen latency it measures is the real one.
Writes a timeline to /tmp/claude/seam_fix/timeline.txt and prints PASS/timings."""
from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtMultimedia import QMediaPlayer, QVideoSink  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from studio import chapters  # noqa: E402
from studio.session import _read_gpmf  # noqa: E402
from studio.video_view import VideoView  # noqa: E402

PATHS = [f"/Users/daniil/Desktop/D24/GX0{i}0060.MP4" for i in (1, 2, 3)]
OUT = "/tmp/claude/seam_fix/timeline.txt"

_t0 = time.monotonic()
_log: list[str] = []


def L(msg: str):
    line = f"[{time.monotonic() - _t0:7.3f}s] {msg}"
    _log.append(line)
    print(line, flush=True)


def main():
    near = 3.0
    if "--near" in sys.argv:
        near = float(sys.argv[sys.argv.index("--near") + 1])

    L("reading GPMF durations...")
    _, _, _, durations = _read_gpmf(PATHS)
    cm = chapters.ChapterMap(PATHS, durations)
    ch0_end = cm.chapters[0].offset + cm.chapters[0].duration
    L(f"durations={[round(d, 2) for d in durations]} ch0_end={ch0_end:.3f}")

    app = QApplication(sys.argv)
    view = VideoView(cm)
    pane = view.pane

    # Instrument the pane's player.
    state_names = {
        QMediaPlayer.PlaybackState.StoppedState: "Stopped",
        QMediaPlayer.PlaybackState.PlayingState: "Playing",
        QMediaPlayer.PlaybackState.PausedState: "Paused",
    }
    status_names = {
        QMediaPlayer.MediaStatus.NoMedia: "NoMedia",
        QMediaPlayer.MediaStatus.LoadingMedia: "LoadingMedia",
        QMediaPlayer.MediaStatus.LoadedMedia: "LoadedMedia",
        QMediaPlayer.MediaStatus.StalledMedia: "StalledMedia",
        QMediaPlayer.MediaStatus.BufferedMedia: "BufferedMedia",
        QMediaPlayer.MediaStatus.BufferingMedia: "BufferingMedia",
        QMediaPlayer.MediaStatus.EndOfMedia: "EndOfMedia",
        QMediaPlayer.MediaStatus.InvalidMedia: "InvalidMedia",
    }

    seam = {"end_fired": None, "set_source_called": None, "first_frame_ch1": None,
            "first_play_ch1": None}

    pane.player.mediaStatusChanged.connect(
        lambda s: L(f"mediaStatus -> {status_names.get(s, s)} (chapter={pane.current_chapter()})"))
    pane.player.playbackStateChanged.connect(
        lambda s: L(f"playbackState -> {state_names.get(s, s)}"))
    pane.player.errorOccurred.connect(
        lambda e, msg: L(f"ERROR {e}: {msg}"))

    # Wrap _on_end_of_media + _set_source to timestamp them.
    orig_end = pane._on_end_of_media
    orig_set = pane._set_source

    def end_hook():
        seam["end_fired"] = time.monotonic() - _t0
        L(f">>> _on_end_of_media (current_chapter={pane.current_chapter()})")
        orig_end()

    def set_hook(i):
        seam["set_source_called"] = time.monotonic() - _t0
        L(f">>> _set_source({i})")
        orig_set(i)

    pane._on_end_of_media = end_hook
    pane._set_source = set_hook
    # rewire mediaStatus signal (it bound the original methods at __init__)
    pane.player.mediaStatusChanged.disconnect(pane._on_media_status)

    def status_router(status):
        # Replicate _on_media_status but routing through our hooked methods.
        loaded = status in (QMediaPlayer.MediaStatus.LoadedMedia,
                            QMediaPlayer.MediaStatus.BufferedMedia)
        if loaded and pane._pending is not None:
            index, local, resume = pane._pending
            if index == pane.current_chapter():
                pane._pending = None
                pane.player.setPosition(int(local * 1000))
                if resume:
                    seam["first_play_ch1"] = time.monotonic() - _t0
                    L(f">>> applying _pending seek to {local:.2f}s + play (resume={resume})")
                    pane.player.play()
            return
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            pane._on_end_of_media()

    pane.player.mediaStatusChanged.connect(status_router)

    # First-frame detection on chapter 1 via the video sink.
    sink = QVideoSink()
    pane.player.setVideoSink(sink)

    def on_frame(_frame):
        if pane.current_chapter() == 1 and seam["first_frame_ch1"] is None and seam["end_fired"]:
            seam["first_frame_ch1"] = time.monotonic() - _t0
            L(f"*** FIRST FRAME of chapter 1 presented (chapter={pane.current_chapter()})")

    sink.videoFrameChanged.connect(on_frame)

    # Wait for chapter 0 to load, seek near its end, play.
    def begin():
        L(f"seeking to ch0_end - {near}s = {ch0_end - near:.3f}s, then play()")
        view.seek(ch0_end - near)
        view.play()

    QTimer.singleShot(2500, begin)

    # Bail out: give it up to 90s to cross the seam.
    deadline = 90.0

    def watchdog():
        elapsed = time.monotonic() - _t0
        ff = seam["first_frame_ch1"]
        if ff is not None:
            resume_latency = ff - seam["end_fired"]
            L(f"=== RESUMED chapter 1: first frame {resume_latency:.2f}s after EndOfMedia ===")
            finish()
        elif elapsed > deadline:
            L(f"=== TIMEOUT: chapter 1 did NOT present a frame within {deadline:.0f}s "
              f"(end_fired={seam['end_fired']}, set_source={seam['set_source_called']}, "
              f"first_play={seam['first_play_ch1']}, chapter={pane.current_chapter()}) ===")
            finish()

    def finish():
        # Print a few positionChanged samples already captured.
        open(OUT, "w").write("\n".join(_log) + "\n")
        L(f"wrote {OUT}")
        app.quit()

    wd = QTimer()
    wd.setInterval(250)
    wd.timeout.connect(watchdog)
    wd.start()

    # Log positionChanged (global) sparsely so we can confirm monotonicity across the seam.
    last_logged = {"t": -1.0}

    def on_pos(g):
        if abs(g - last_logged["t"]) >= 0.5 or pane.current_chapter() == 1:
            last_logged["t"] = g
            L(f"positionChanged(global={g:.3f}) chapter={pane.current_chapter()}")

    view.positionChanged.connect(on_pos)

    app.exec()


if __name__ == "__main__":
    main()
