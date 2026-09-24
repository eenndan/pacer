"""The `--demo` recording: a SYNTHETIC session — generated, not filmed — that anyone can open.

WHY IT EXISTS. An evaluator who cloned the repo could not see Pacer do anything without their own
GoPro track footage (board review UX-1, CEO-2): the one bundled clip has no laps, and `--demo` pointed
at a release asset that was never uploaded. The owner has not consented to publishing his footage,
so the demo is built from nothing: `synth_gopro`'s fictional circuit, driven by a simulated kart,
written as a single-chapter HERO13-shaped .mp4. No person, kart, place or camera footage is in it.

WHAT IS IN THE FILE:
  * telemetry — `synth_gopro.build` at DEMO_SEED: DEMO_LAPS flying laps between an out-lap and an
    in-lap (one of them slow), GPS9 at the clean end of a HERO13's noise, ACCL/GYRO/GRAV/CORI as one
    rigid-body motion, the measured 0.46 s GPS lag and 27 ppm media clock. One chapter, because the
    release asset is one file;
  * a picture — RENDERED, and labelled on every frame "SYNTHETIC DEMO SESSION — Generated, not
    filmed.", with a running timecode (the video's own clock) and a top-down map of the circuit with
    the kart's TRUE position on it. The picture and the IMU share the media clock, so the dot on the
    video and the cursor on Pacer's map should meet wherever you scrub: video sync you can see;
  * a VERIFIED start/finish line — the circuit is the built-in track `DEMO_TRACK_NAME`
    (`studio/track_db.py` SEED), whose line sits square across the middle of the main straight. An
    unknown circuit would load with an auto-fitted, provisional line; and the unknown-track
    heuristic puts its line at the peak-speed point, where laps start braking, which #371 measured
    costing up to 13 ms a lap on this very circuit.

WHERE (DEMO_ORIGIN). The open Atlantic, like `synth_gopro.ORIGIN`, but ~135 km from it: the built-in
track detects within `track_db.DETECT_RADIUS_M` (1.5 km), so the test recording at ORIGIN stays an
UNKNOWN track (it exists to exercise the unknown-track path) while the demo is a known one. Nothing
real can sit inside either radius.

DETERMINISTIC: the same code gives the same telemetry bytes; the video bytes also depend on the
ffmpeg/x264/freetype build (the pixi env pins them), so a rebuild on another machine may differ in
its picture and hence its sha256 — `studio/demo.py` pins the sha256 of the PUBLISHED file.

Run (the output must not exist yet — nothing is ever overwritten):
    pixi run make-demo -- --out <new-file.mp4>
    pixi run make-demo -- --print-track-entry     # the SEED entry this circuit implies
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

import numpy as np

from studio.dev import synth_gopro as sg

DEMO_SEED = sg.DEFAULT_SEED
DEMO_LAPS = 14
DEMO_ORIGIN = (46.0, -31.0)       # deg: open Atlantic, ~135 km from synth_gopro.ORIGIN
DEMO_TRACK_NAME = "Synthetic demo circuit"
# The start/finish line's half-length (m each side of the centreline): the unknown-track
# heuristic's own 15 m (load._HEURISTIC_HALF_M), so the line spans any GPS scatter across the straight.
LINE_HALF_M = 15.0

# The truth the published demo (`demo-data-v1`) was generated with: every flying lap's true time, ms,
# at the built-in line. If the generator changes, the published file does not — so
# tests/test_demo_session.py fails here until the demo is re-published (a new release tag,
# `studio/demo.py` re-pinned) or the change is kept away from the demo's parameters.
PUBLISHED_LAP_MS = (45684, 45289, 45391, 44966, 45355, 50590, 45300, 45331, 45183, 45234, 45258,
                    45161, 44956, 45253)

# ------------------------------------------------------------------------------ the picture
W, H = 960, 540
_BG = np.array((22, 26, 34), np.uint8)
_TRACK = np.array((74, 82, 98), np.float32)
_KART = np.array((255, 176, 32), np.float32)
_MAP = (500, 40, 920, 500)        # x0, y0, x1, y1 of the map's box, px
_TRACK_HALF_PX = 4.0
_KART_R_PX = 8.0
_FONTS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")
# (text, font, px, colour, x, y) — drawn by ffmpeg's drawtext on every frame, in the app's own font.
_LINES = (
    ("SYNTHETIC DEMO SESSION", "Inter-SemiBold.ttf", 34, "0xFFB020", 40, 44),
    ("Generated, not filmed.", "Inter-Medium.ttf", 27, "0xE8ECF2", 40, 94),
    ("A simulated kart on a fictional circuit", "Inter-Regular.ttf", 19, "0x9AA3B2", 40, 158),
    ("in the open Atlantic. No real person,", "Inter-Regular.ttf", 19, "0x9AA3B2", 40, 186),
    ("place, kart or camera footage.", "Inter-Regular.ttf", 19, "0x9AA3B2", 40, 214),
    ("The dot marks where the kart truly is.", "Inter-Regular.ttf", 19, "0x9AA3B2", 40, 266),
    ("video time", "Inter-Regular.ttf", 17, "0x9AA3B2", 40, 404),
    ("made by studio/dev/make_demo.py", "Inter-Regular.ttf", 14, "0x5C6474", 40, 500),
)
_TIMECODE = ("Inter-Medium.ttf", 46, "0xE8ECF2", 40, 428)
# An IDR every 5 s (150 frames), not a GoPro's one per payload: here an I-frame is ~23 KB and a
# P-frame ~0.2 KB, so one per payload made the session 22.5 MB against 7.9 MB (measured over a moving
# 30 s span, 2026-09-24), and a seek at 960x540 decodes the <= 149 frames in between in milliseconds.
_X264 = {"preset": "slow", "crf": "24", "keyint": 150}


def _map_transform(circuit):
    """(scale px/m, x offset, y offset) fitting the circuit into _MAP, north up."""
    x0, y0, x1, y1 = _MAP
    ex, ey = np.ptp(circuit.x), np.ptp(circuit.y)
    scale = 0.92 * min((x1 - x0) / ex, (y1 - y0) / ey)
    ox = (x0 + x1) / 2 - scale * (circuit.x.min() + ex / 2)
    oy = (y0 + y1) / 2 + scale * (circuit.y.min() + ey / 2)
    return scale, ox, oy


def _disc(buf, cx, cy, r):
    """Max-composite an antialiased disc of radius `r` px at (cx, cy) into the alpha plane `buf`."""
    xa, xb = int(cx - r - 1), int(cx + r + 2)
    ya, yb = int(cy - r - 1), int(cy + r + 2)
    yy, xx = np.mgrid[ya:yb, xa:xb]
    a = np.clip(r + 0.5 - np.hypot(xx + 0.5 - cx, yy + 0.5 - cy), 0.0, 1.0)
    np.maximum(buf[ya:yb, xa:xb], a, out=buf[ya:yb, xa:xb])


def _background(circuit):
    """The frame without the kart: the dark slate and the circuit, drawn once."""
    scale, ox, oy = _map_transform(circuit)
    alpha = np.zeros((H, W), np.float32)
    px, py = ox + scale * circuit.x, oy - scale * circuit.y
    step = max(1, int(0.5 / (scale * sg.DS)))            # a stamp every half pixel
    for cx, cy in zip(px[::step], py[::step], strict=True):
        _disc(alpha, cx, cy, _TRACK_HALF_PX)
    img = _BG.astype(np.float32) * (1 - alpha[..., None]) + _TRACK * alpha[..., None]
    return np.round(img).astype(np.uint8)


def render_video(path: str, truth, first_payload: int, n_payloads: int, ffmpeg: str) -> None:
    """One chapter's picture for `synth_gopro.generate(video=...)`: the labelled slate, the running
    timecode and the kart's true position, FRAMES_PER_PAYLOAD frames per payload at 29.97 fps. A
    frame at media time m shows the kart where it truly was at m / (1 + ppm): the IMU's clock, which
    GPS9 lags by `truth.gps_lag_s` (Pacer measures and removes that lag)."""
    bg = _background(truth.circuit)
    scale, ox, oy = _map_transform(truth.circuit)
    n = n_payloads * sg.FRAMES_PER_PAYLOAD
    media = (first_payload * sg.FRAMES_PER_PAYLOAD + np.arange(n)) * 1001 / 30000
    s_abs, _, _ = sg._kinematics(truth, media / (1.0 + truth.media_ppm * 1e-6))
    kx, ky, _, _ = truth.circuit.at(s_abs)
    kx, ky = ox + scale * kx, oy - scale * ky
    with tempfile.TemporaryDirectory(prefix="make_demo_") as tmp:
        # drawtext reads its fonts and texts from files named in the filter graph; copying them
        # beside the graph keeps every path free of the characters a graph would need escaped.
        filters = []
        for i, (text, font, size, colour, x, y) in enumerate(_LINES):
            shutil.copy(os.path.join(_FONTS, font), os.path.join(tmp, font))
            with open(os.path.join(tmp, f"line{i}.txt"), "w", encoding="utf-8") as f:
                f.write(text)
            filters.append(f"drawtext=fontfile={font}:textfile=line{i}.txt:expansion=none:"
                           f"fontsize={size}:fontcolor={colour}:x={x}:y={y}")
        font, size, colour, x, y = _TIMECODE
        filters.append(f"drawtext=fontfile={font}:text='%{{pts\\:hms}}':"
                       f"fontsize={size}:fontcolor={colour}:x={x}:y={y}")
        cmd = [ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error",
               "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-framerate", "30000/1001",
               "-i", "-", "-vf", ",".join(filters), "-frames:v", str(n),
               "-c:v", "libx264", "-preset", _X264["preset"], "-crf", _X264["crf"], "-pix_fmt", "yuv420p",
               # No B-frames, so presentation time is decode time and the copied trak needs no
               # composition offsets.
               "-x264-params", f"keyint={_X264['keyint']}:min-keyint={_X264['keyint']}"
                               ":scenecut=0:bframes=0:threads=4",
               "-video_track_timescale", "30000", "-an", "-n", os.path.abspath(path)]
        proc = subprocess.Popen(cmd, cwd=tmp, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        frame = bg.copy()
        r = int(_KART_R_PX) + 2
        try:
            for i in range(n):
                cx, cy = float(kx[i]), float(ky[i])
                ya, yb, xa, xb = int(cy) - r, int(cy) + r + 1, int(cx) - r, int(cx) + r + 1
                win = np.zeros((yb - ya, xb - xa), np.float32)
                _disc(win, cx - xa, cy - ya, _KART_R_PX)
                a = win[..., None]
                frame[ya:yb, xa:xb] = np.round(bg[ya:yb, xa:xb] * (1 - a) + _KART * a)
                proc.stdin.write(frame.data)
                frame[ya:yb, xa:xb] = bg[ya:yb, xa:xb]      # restore for the next frame
            proc.stdin.close()
        except BrokenPipeError:
            pass
        err = proc.stderr.read().decode(errors="replace")
        if proc.wait() != 0:
            raise RuntimeError(f"ffmpeg failed rendering the demo picture: {err.strip()}")


# ------------------------------------------------------------------------------ the recording
def main_straight_mid(truth) -> float:
    """Lap distance (m) of the middle of the main straight — where the built-in line sits."""
    return sum(truth.circuit.straights[0]) / 2.0


def simulate():
    """The demo's ground truth (no files): the circuit, the kart's motion, the clocks."""
    return sg.simulate(DEMO_SEED, DEMO_LAPS, origin=DEMO_ORIGIN)


def track_entry(truth=None) -> dict:
    """The track-database entry the demo circuit implies — `studio/track_db.py` ships it as a
    built-in: the detection centroid is the circuit's bbox centre (how `load` anchors a trace), the
    bbox its extent, the start/finish line square across the middle of the main straight. Rounded to
    1e-7 deg (~1 cm), GPS9's own resolution."""
    truth = truth or simulate()
    c = truth.circuit
    lat, lon = sg.to_latlon(c.x, c.y, truth.origin)
    line = truth.line_at(main_straight_mid(truth), LINE_HALF_M)

    def r(v):
        return round(float(v), 7)

    return {"name": DEMO_TRACK_NAME,
            "centroid": [r((lat.min() + lat.max()) / 2), r((lon.min() + lon.max()) / 2)],
            "bbox": [r(lat.min()), r(lon.min()), r(lat.max()), r(lon.max())],
            "start": [[r(p[0]), r(p[1])] for p in line],
            "sectors": []}


def make(out_path: str, render: bool = True, ffmpeg: str | None = None):
    """Write the demo recording to `out_path` (which must not exist) and return its truth.
    `render=False` gives the same telemetry under the flat placeholder picture — seconds instead
    of minutes, for tests that never decode a frame."""
    if os.path.lexists(out_path):
        raise FileExistsError(f"{out_path} exists; make_demo never writes over anything")
    with tempfile.TemporaryDirectory(prefix="make_demo_rec_") as tmp:
        rec = sg.generate(os.path.join(tmp, "rec"), DEMO_SEED, DEMO_LAPS, chapters=1,
                          origin=DEMO_ORIGIN, ffmpeg=ffmpeg,
                          video=render_video if render else None)
        (src,) = rec.paths
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        with open(src, "rb") as fin, open(out_path, "xb") as fout:
            shutil.copyfileobj(fin, fout)
    return rec.truth


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", help="the NEW .mp4 to write (refused if it exists)")
    ap.add_argument("--print-track-entry", action="store_true",
                    help="print the built-in track entry the demo circuit implies, and exit")
    args = ap.parse_args(argv)
    if args.print_track_entry:
        print(json.dumps(track_entry(), indent=1))
        return 0
    if not args.out:
        ap.error("--out is required")
    truth = make(args.out)
    laps = truth.lap_times(track_entry(truth)["start"])
    print(f"{args.out}\n  {os.path.getsize(args.out):,} bytes  sha256 {sha256(args.out)}\n"
          f"  {len(laps)} laps, true lap times (s) at the built-in line: "
          f"{np.round(laps, 3).tolist()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
