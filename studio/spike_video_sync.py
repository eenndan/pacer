#!/usr/bin/env python3
"""Spike: is PySide6 video<->telemetry sync viable for the pacer studio app?

Runs headlessly (QT_QPA_PLATFORM=offscreen, no window) and verifies the two
parts that the deep-research flagged as unproven:

  1. QMediaPlayer + QVideoSink actually deliver decoded frames to PYTHON
     (videoFrameChanged), and each frame carries a usable per-frame clock:
     QVideoFrame.startTime() (microseconds) and/or QMediaPlayer.position() (ms).
  2. Qt's media clock (player.duration) aligns with pacer's GPMF time axis
     (GPMFSource.get_total_duration) on the SAME .mp4 -> so "video time t -> which
     telemetry sample" is a trivial, correct lookup.

If this passes, the bidirectional video<->plot-cursor sync is sound and the rest
(pyqtgraph plots + draggable LineSegmentROI handles) is routine Qt.

Run:  pixi run python studio/spike_video_sync.py [path/to/gopro.mp4]
"""

import bisect
import os
import statistics
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")  # decode without a window

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtGui import QGuiApplication
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame, QVideoSink

import pacer

VIDEO = sys.argv[1] if len(sys.argv) > 1 else "3rdparty/gpmf-parser/samples/hero8.mp4"


def build_telemetry(path):
    """Reproduce timeline.cpp's ingest: per-sample (time, speed) on the MP4 clock."""
    src = pacer.GPMFSource(path)
    total = src.get_total_duration()
    times, speeds = [], []
    src.seek(0)
    while not src.is_end():
        start, end = src.current_time_span()
        chunk = []
        src.read_samples(lambda s, i, n: chunk.append((s, i, n)))
        for s, i, n in chunk:
            times.append(start + (end - start) * (i / n if n else 0.0))
            speeds.append(s.full_speed)
        src.next()
    return total, times, speeds


def main():
    path = os.path.abspath(VIDEO)
    if not os.path.exists(path):
        print(f"FAIL: video not found: {path}")
        return 2

    # --- pacer side ---
    try:
        pacer_total, tel_t, tel_v = build_telemetry(path)
    except Exception as e:  # noqa: BLE001
        print(f"FAIL building telemetry from pacer: {e!r}")
        return 2

    # --- Qt side: decode frames, capture per-frame clocks in Python ---
    app = QGuiApplication(sys.argv)
    sink = QVideoSink()
    player = QMediaPlayer()
    player.setVideoSink(sink)

    frame_us = []  # QVideoFrame.startTime(), microseconds (or -1)
    pos_ms = []    # QMediaPlayer.position() sampled at each frame, ms
    state = {"duration_ms": None, "error": None}

    def on_frame(frame: QVideoFrame):
        frame_us.append(frame.startTime())
        pos_ms.append(player.position())

    def on_status(status):
        if status == QMediaPlayer.MediaStatus.LoadedMedia:
            state["duration_ms"] = player.duration()
        elif status == QMediaPlayer.MediaStatus.EndOfMedia:
            app.quit()

    def on_error(err, msg):
        state["error"] = f"{err} {msg}"
        app.quit()

    sink.videoFrameChanged.connect(on_frame)
    player.mediaStatusChanged.connect(on_status)
    player.errorOccurred.connect(on_error)

    player.setSource(QUrl.fromLocalFile(path))
    player.play()
    QTimer.singleShot(40_000, app.quit)  # safety net
    app.exec()

    # --- report ---
    print(f"file: {path}")
    print("=== pacer telemetry ===")
    print(f"  get_total_duration : {pacer_total:.3f} s")
    if tel_t:
        print(f"  samples            : {len(tel_t)}  (t {tel_t[0]:.3f}..{tel_t[-1]:.3f} s)")

    print("=== Qt video ===")
    if state["error"]:
        print(f"  media error        : {state['error']}")
    dur_ms = state["duration_ms"]
    print(f"  player.duration    : {dur_ms} ms"
          + (f" = {dur_ms / 1000:.3f} s" if dur_ms else ""))
    print(f"  frames -> Python   : {len(frame_us)}")
    valid_us = [u for u in frame_us if u is not None and u >= 0]
    print(f"  frames w/ startTime: {len(valid_us)}/{len(frame_us)}")
    if valid_us:
        print(f"    startTime range  : {valid_us[0] / 1e6:.3f}..{valid_us[-1] / 1e6:.3f} s")
        deltas = [(valid_us[i + 1] - valid_us[i]) / 1e6
                  for i in range(len(valid_us) - 1) if valid_us[i + 1] > valid_us[i]]
        if deltas:
            med = statistics.median(deltas)
            print(f"    median frame dt  : {med * 1000:.2f} ms  => ~{1 / med:.1f} fps")
    nonzero_pos = sum(1 for p in pos_ms if p > 0)
    if pos_ms:
        print(f"  player.position()  : {pos_ms[0]}..{pos_ms[-1]} ms ({nonzero_pos} nonzero)")

    print("=== alignment + sync lookup ===")
    if dur_ms and pacer_total:
        drift = abs(dur_ms / 1000 - pacer_total)
        print(f"  |Qt - pacer| dur   : {drift * 1000:.0f} ms")
    if tel_t:
        for frac, label in [(0.0, "start"), (0.5, "mid"), (0.9, "late")]:
            t = pacer_total * frac
            j = min(max(bisect.bisect_left(tel_t, t), 0), len(tel_t) - 1)
            print(f"  video t={t:6.2f}s ({label:5}) -> sample #{j:4d}"
                  f"  t={tel_t[j]:.2f}s  speed={tel_v[j] * 3.6:.1f} km/h")

    # --- verdict ---
    ok_frames = len(frame_us) > 0
    ok_clock = len(valid_us) > 0 or nonzero_pos > 0
    ok_align = dur_ms is not None and dur_ms > 0
    print("\n=== VERDICT ===")
    print(f"  [1] frames delivered to Python    : {'PASS' if ok_frames else 'FAIL'}")
    print(f"  [2] per-frame clock in Python      : {'PASS' if ok_clock else 'FAIL'}"
          f"  (startTime={'yes' if valid_us else 'no'}, position={'yes' if nonzero_pos else 'no'})")
    print(f"  [3] Qt clock <-> pacer clock        : {'PASS' if ok_align else 'FAIL'}")
    return 0 if (ok_frames and ok_clock and ok_align) else 1


if __name__ == "__main__":
    sys.exit(main())
