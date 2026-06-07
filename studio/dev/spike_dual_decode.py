#!/usr/bin/env python3
"""P0 spike: can TWO QMediaPlayer decoders sustain 4K60 1x playback SIMULTANEOUSLY?

This de-risks Phase B of the side-by-side "compare two laps" feature. Phase B wants both
panes rolling at 1x at the same time (time-into-lap playback). The prior video-sync spike
(studio/dev/spike_video_sync.py) only ran ~10-40 s on ONE decoder, which is too short to
surface THERMAL THROTTLE on a sustained dual-4K60 load. This script runs BOTH decoders for
as long a window as practical and measures the SUSTAINED presented-frame rate per player, so
we can see whether the rate decays over a minutes-long run (throttle) or holds at ~native fps.

Method
------
  * Open two QMediaPlayer, each with its own QVideoWidget (real on-screen present path, not
    headless — throttle shows up under the real GPU present load) AND its own QVideoSink so
    we get a videoFrameChanged tick per PRESENTED frame to count. (The widget is the real
    output; we attach a sink purely as a frame counter via setVideoSink, which still drives
    the widget on macOS.)
  * Play both at rate 1.0 simultaneously. Every WINDOW seconds, print the per-player presented
    fps for that window so a decaying trend (throttle) is visible across the whole run.
  * Then, with both decoders still alive, do a few seek-to-presented-frame latency probes on
    one player: setPosition(t), and measure wall time until the FIRST frame whose presentation
    timestamp is at/after t arrives. This sets the scrub-settle expectation for Phase B.

VERDICT line: GO if both players hold ~native fps for the whole run without a downward trend;
NO-GO if either throttles/drops materially (Phase B must then default to "scrub-only stepped
compare" — pause the secondary during primary playback).

Run (real media, on-screen so the GPU present path is exercised — do NOT force offscreen):
    pixi run python -m studio.dev.spike_dual_decode \
        /Users/daniil/Desktop/D24/GX010060.MP4 /Users/daniil/Desktop/D24/GX020060.MP4 [secs]

`secs` (optional) is the target sustained-window duration; default 150 s (~2.5 min). The two
paths default to the 0060 chapters on the Desktop if omitted.
"""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QTimer, QUrl
from PySide6.QtMultimedia import QMediaPlayer, QVideoFrame
from PySide6.QtMultimediaWidgets import QVideoWidget
from PySide6.QtWidgets import QApplication, QHBoxLayout, QWidget

_DEFAULT_A = "/Users/daniil/Desktop/D24/GX010060.MP4"
_DEFAULT_B = "/Users/daniil/Desktop/D24/GX020060.MP4"
_DEFAULT_SECS = 150.0       # ~2.5 min sustained window target
_WINDOW = 5.0               # per-window fps report cadence (seconds)


class _Decoder:
    """One QMediaPlayer + QVideoWidget + QVideoSink, counting presented frames.

    The QVideoSink's videoFrameChanged fires once per frame the pipeline presents, so the
    count over a wall-clock window is the SUSTAINED presented fps — the number a throttle
    would erode."""

    def __init__(self, path: str, label: str):
        self.label = label
        self.path = path
        self.widget = QVideoWidget()
        self.player = QMediaPlayer()
        # Drive the on-screen widget for the real present load AND read its sink for frame
        # counting. On macOS setVideoOutput(widget) routes through the widget's OWN QVideoSink;
        # to also get a python-side per-frame tick we connect THAT sink's videoFrameChanged.
        self.player.setVideoOutput(self.widget)
        self.widget.videoSink().videoFrameChanged.connect(self._on_frame)
        self.total_frames = 0
        self.window_frames = 0
        self.last_pts_us = -1
        self.duration_ms = 0
        self.player.durationChanged.connect(self._on_duration)

    def _on_duration(self, ms: int):
        self.duration_ms = ms

    def _on_frame(self, frame: QVideoFrame):
        self.total_frames += 1
        self.window_frames += 1
        st = frame.startTime()  # microseconds, presentation timestamp (or -1)
        if st is not None and st >= 0:
            self.last_pts_us = st

    def take_window(self) -> int:
        n = self.window_frames
        self.window_frames = 0
        return n

    def start(self):
        self.player.setSource(QUrl.fromLocalFile(os.path.abspath(self.path)))
        self.player.setPlaybackRate(1.0)
        self.player.play()


def _seek_latency_probe(dec: _Decoder, targets_ms: list[int]) -> list[tuple[float, float]]:
    """With the decoder alive, seek to each target and measure scrub-settle: wall seconds from
    setPosition until the FIRST presented frame at/after the target arrives — i.e. the time for
    the new playhead frame to actually show (what a Phase B distance-locked scrub will feel like).

    Returns (latency_s, landed_pts_s) per target. We keep the player PLAYING across the seek so
    the pipeline reliably presents frames forward from the seek point (a paused seek only emits
    the nearest keyframe, which may sit before the target and then stall). Drives a nested event
    loop per probe. NaN latency = no qualifying frame within the safety window."""
    from PySide6.QtCore import QEventLoop

    results: list[tuple[float, float]] = []
    for tgt in targets_ms:
        loop = QEventLoop()
        state = {"t0": 0.0, "dt": None, "pts": float("nan")}

        def on_frame(frame: QVideoFrame, _tgt=tgt, _state=state, _loop=loop):
            st = frame.startTime()
            # First frame whose presentation timestamp is at/after the seek target = the playhead
            # has settled on the requested moment.
            if st is not None and st >= 0 and st >= _tgt * 1000 and _state["dt"] is None:
                _state["dt"] = time.perf_counter() - _state["t0"]
                _state["pts"] = st / 1e6
                _loop.quit()

        conn = dec.widget.videoSink().videoFrameChanged.connect(on_frame)
        QTimer.singleShot(5000, loop.quit)  # safety net
        dec.player.play()                    # play across the seek so frames flow forward
        state["t0"] = time.perf_counter()
        dec.player.setPosition(tgt)
        loop.exec()
        dec.widget.videoSink().videoFrameChanged.disconnect(conn)
        results.append((state["dt"] if state["dt"] is not None else float("nan"),
                        state["pts"]))
    return results


def main() -> int:
    # Line-buffer stdout so the per-window trend + the final VERDICT survive a redirect/pipe
    # (block buffering can drop the last block when the process exits straight after printing).
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, OSError):
        pass
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    path_a = args[0] if len(args) > 0 else _DEFAULT_A
    path_b = args[1] if len(args) > 1 else _DEFAULT_B
    secs = float(args[2]) if len(args) > 2 else _DEFAULT_SECS

    for p in (path_a, path_b):
        if not os.path.exists(p):
            print(f"FAIL: media not found: {p}")
            return 2

    app = QApplication(sys.argv)

    # Two panes side by side in one window — the real composited present path.
    win = QWidget()
    win.setWindowTitle("dual-decode spike")
    win.resize(1280, 380)
    lay = QHBoxLayout(win)
    lay.setContentsMargins(0, 0, 0, 0)

    dec_a = _Decoder(path_a, "A")
    dec_b = _Decoder(path_b, "B")
    lay.addWidget(dec_a.widget, 1)
    lay.addWidget(dec_b.widget, 1)
    win.show()

    print("P0 dual-decode spike — sustained 1x on TWO 4K60 decoders")
    print(f"  A: {path_a}")
    print(f"  B: {path_b}")
    print(f"  target window: {secs:.0f} s, report every {_WINDOW:.0f} s\n")

    dec_a.start()
    dec_b.start()

    # Per-window fps trend, collected so we can judge a decaying (throttling) trend at the end.
    history: list[tuple[float, float, float]] = []  # (elapsed_s, fps_a, fps_b)
    state = {"t0": time.perf_counter(), "last": time.perf_counter()}

    def report():
        now = time.perf_counter()
        dt = now - state["last"]
        state["last"] = now
        elapsed = now - state["t0"]
        fa = dec_a.take_window() / dt if dt > 0 else 0.0
        fb = dec_b.take_window() / dt if dt > 0 else 0.0
        history.append((elapsed, fa, fb))
        print(f"  t={elapsed:6.1f}s   A: {fa:5.1f} fps   B: {fb:5.1f} fps")
        if elapsed >= secs:
            timer.stop()
            _finish()

    def _finish():
        print()
        # Seek-latency probe with BOTH decoders still alive (probe A, mid-clip targets).
        dur = dec_a.duration_ms or 60_000
        targets = [int(dur * f) for f in (0.2, 0.5, 0.8)]
        print("seek-to-presented-frame latency (both decoders alive), probing A:")
        probes = _seek_latency_probe(dec_a, targets)
        for tgt, (lat, pts) in zip(targets, probes, strict=True):
            if lat == lat:  # not NaN
                print(f"  seek -> {tgt/1000:6.1f}s : {lat*1000:6.0f} ms  (landed {pts:7.1f}s)")
            else:
                print(f"  seek -> {tgt/1000:6.1f}s : (no qualifying frame within 5 s)")
        good_lats = [lat for lat, _ in probes if lat == lat]
        mean_lat = sum(good_lats) / len(good_lats) if good_lats else float("nan")

        # --- judge the sustained trend ---
        # Ignore the first window (startup/buffering). Compare the early-steady-state mean vs the
        # late mean; a material drop = throttle. "native fps" ~= 60 (4K60); accept >= ~55 as held.
        body = history[1:] if len(history) > 1 else history
        fas = [h[1] for h in body]
        fbs = [h[2] for h in body]
        n = len(body)
        early = body[: max(1, n // 3)]
        late = body[-max(1, n // 3):]
        early_a = sum(h[1] for h in early) / len(early)
        early_b = sum(h[2] for h in early) / len(early)
        late_a = sum(h[1] for h in late) / len(late)
        late_b = sum(h[2] for h in late) / len(late)
        mean_a = sum(fas) / len(fas) if fas else 0.0
        mean_b = sum(fbs) / len(fbs) if fbs else 0.0
        min_a = min(fas) if fas else 0.0
        min_b = min(fbs) if fbs else 0.0

        print()
        print("=== SUSTAINED-FPS SUMMARY (excluding the first startup window) ===")
        print(f"  ran for ~{history[-1][0]:.0f} s of wall time across {n} windows")
        print(f"  A: mean {mean_a:5.1f} fps  min {min_a:5.1f}  early {early_a:5.1f} -> late {late_a:5.1f}")
        print(f"  B: mean {mean_b:5.1f} fps  min {min_b:5.1f}  early {early_b:5.1f} -> late {late_b:5.1f}")
        print(f"  seek latency (both alive): mean {mean_lat*1000:.0f} ms over {len(good_lats)} probes")

        # GO criteria: both players hold near native fps with no material late-run decay.
        HOLD = 55.0          # fps floor to count as "holding ~native 60"
        DECAY_FRAC = 0.10    # >10% early->late drop counts as throttling
        held_a = late_a >= HOLD and (early_a <= 0 or late_a >= early_a * (1 - DECAY_FRAC))
        held_b = late_b >= HOLD and (early_b <= 0 or late_b >= early_b * (1 - DECAY_FRAC))
        go = held_a and held_b
        print()
        if go:
            print("VERDICT: GO — sustained dual 4K60 1x decode HELD ~native fps with no throttle. "
                  "Phase B may default to both panes playing at 1x simultaneously.")
        else:
            why = []
            if not held_a:
                why.append(f"A late {late_a:.1f} fps")
            if not held_b:
                why.append(f"B late {late_b:.1f} fps")
            print("VERDICT: NO-GO — sustained dual 4K60 1x decode THROTTLED/DROPPED "
                  f"({', '.join(why)}; floor {HOLD:.0f} fps). Phase B must default to "
                  "'scrub-only stepped compare' (pause the secondary during primary playback).")
        app.quit()

    timer = QTimer()
    timer.setInterval(int(_WINDOW * 1000))
    timer.timeout.connect(report)
    timer.start()
    # Hard safety net: quit ~30 s after the target even if a report stalls.
    QTimer.singleShot(int((secs + 30) * 1000), app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
