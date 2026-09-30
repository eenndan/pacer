"""vt_gop: would a 2 s keyframe interval buy VideoToolbox's H.264 exports quality per byte?
(APP-POLISH-4; the 2026-09-28 review's LEFT-26.)

WHY. `export_video._video_codec_args`' VideoToolbox branch passes no `-g`, so ffmpeg's `gop_size`
default of 12 becomes the encoder's MaxKeyFrameInterval: 150 keyframes a minute at 30 fps, where the
libx264 fallback runs x264's 250-frame default. At a fixed bitrate a keyframe costs several P-frames,
so a longer interval could put those bytes into the picture, or buy nothing and make every random
seek decode further. The decision rule was fixed before this ran; the verdict and its numbers are
written in `_video_codec_args`.

WHAT IT DOES, per source:
  1. ONE render of a <= 20 s window (the lap's first WINDOW_S) through the real `Renderer` at 1080p30
     "high" into a LOSSLESS ffv1 yuv420p intermediate (a probe-only swap of `_video_codec_args`), so
     the decode and the overlay are the export's own and are paid for once;
  2. encodes of that intermediate with `_video_codec_args(VT_H264, ...)`'s exact argv, `-g G`
     appended (G 0 = none, ffmpeg's 12), REPS each: VideoToolbox is not bit-deterministic;
  3. per encode: the yield (video-stream bits over `vt_target_bitrate` x duration), the keyframes
     (`-g` on VideoToolbox is a MAXIMUM, so they are counted, never assumed), and SSIM and PSNR
     against the intermediate, frame for frame;
  4. per G (first rep): the scrub cost, `_seek_cmd` at SEEKS seeded random frames and at frame 0
     (the baseline: a keyframe, nothing to decode past), every call interleaved in shuffled order
     across the Gs so the machine's load lands on all of them alike.

The sources: `synthetic`, a synth_gopro recording whose picture is 1920x1080 at 59.94 (the default
320x180 placeholder cannot make a 1080p export: the renderer never upscales), and `mk`, lap 14 of
MK_18_09_26 (the export-slowness reference lap), read only.

ARGV CONTRACT: a mode, and nothing that names an output.

    pixi run python -m studio.dev.probes.vt_gop synthetic
    pixi run python -m studio.dev.probes.vt_gop mk [RECORDING]
    pixi run python -m studio.dev.probes.vt_gop verdict SYNTH.json MK.json

RECORDING is INPUT and only ever opened for reading (default: MK_18_09_26's first chapter). Every
file the probe writes goes into ONE `mkdtemp` under $TMPDIR, refused if it resolves under ~/Desktop
or the volume has under MIN_FREE_BYTES free, and each is removed with `os.remove` before it exits.
The results are printed; the last line is their JSON. The recording's chapters are tripwired on
(size, mtime). `verdict` reads two such JSON files (inputs) and applies the rule. App-support is
jailed and the timing-line sidecar diverted before anything loads.
"""
from __future__ import annotations

import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from dataclasses import replace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("PACER_NO_MEDIA", "1")

from studio.dev._jail import divert_app_support  # noqa: E402

_JAIL = divert_app_support("vt_gop_")

from studio import chapters, sidecar, theme  # noqa: E402  (after the jail, as every load must be)
from studio import export_video as ev  # noqa: E402
from studio.dev import synth_gopro as sg  # noqa: E402

DESKTOP = os.path.realpath(os.path.expanduser("~/Desktop"))
MK_RECORDING = os.path.join(DESKTOP, "MK_18_09_26", "GX010067.MP4")
MK_LAP = 14
WINDOW_S = 20.0
OUT_H = 1080
QUALITY = "high"
GOPS = (0, 30, 60, 120)          # 0 = no -g: ffmpeg's gop_size default, 12
REPS = 3
SEEKS = 20
SEED = 20260930
MIN_FREE_BYTES = 8 * 1024 ** 3   # the MK intermediate is ~1 GB; the rest are tens of MB
# The lossless intermediate: ffv1 in the SAME yuv420p / limited range the VT argv asks for, so the
# encodes' own pixel conversion is a no-op and the metric's reference is exactly their input.
LOSSLESS = ["-c:v", "ffv1", "-level", "3", "-slices", "16", "-pix_fmt", "yuv420p",
            "-color_range", "tv"]

# The rule, fixed before measuring (APP-POLISH-4's binding amendments applied).
YIELD, YIELD_TOL = 0.70, 0.03
GAIN_SSIM, GAIN_PSNR = 0.002, 0.3          # the gain the real footage must show (either)
LOSS_SSIM, LOSS_PSNR = -0.0005, -0.05      # "no loss" on the synthetic (both)
SEEK_NET_MS, SEEK_GROSS_RATIO = 100.0, 2.0
USAGE = ("usage: python -m studio.dev.probes.vt_gop synthetic | mk [RECORDING] "
         "| verdict SYNTH.json MK.json")


def _run(cmd, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


# ------------------------------------------------------------------------------ where it may write
def _workdir() -> str:
    work = tempfile.mkdtemp(prefix="vt_gop_")
    real = os.path.realpath(work)
    if real == DESKTOP or real.startswith(DESKTOP + os.sep):
        raise SystemExit(f"refusing: the scratch directory {real} is under ~/Desktop")
    free = shutil.disk_usage(real).free
    if free < MIN_FREE_BYTES:
        os.rmdir(work)
        raise SystemExit(f"refusing: {free / 1e9:.1f} GB free under {real}, need "
                         f"{MIN_FREE_BYTES / 1e9:.1f}")
    return real


def _out(work: str, name: str) -> str:
    """A NEW path inside `work`: every file this probe writes is named through here."""
    path = os.path.realpath(os.path.join(work, name))
    if not path.startswith(work + os.sep) or path.startswith(DESKTOP + os.sep):
        raise SystemExit(f"refusing to write {path}: outside the probe's scratch directory")
    if os.path.lexists(path):
        raise SystemExit(f"refusing to write over {path}")
    return path


def _cleanup(work: str) -> None:
    """Remove every file and directory under `work`, then `work` (os.remove: `rm` is hook-denied)."""
    for root, dirs, files in os.walk(work, topdown=False):
        for f in files:
            os.remove(os.path.join(root, f))
        for d in dirs:
            os.rmdir(os.path.join(root, d))
    os.rmdir(work)


def _snapshot(paths) -> dict:
    return {p: (os.stat(p).st_size, os.stat(p).st_mtime_ns) for p in paths}


# ------------------------------------------------------------------------------ the two sources
def _synthetic_picture(path, truth, first_payload, n_payloads, ffmpeg):
    """`synth_gopro.generate(video=)`: 1920x1080 at 59.94, testsrc2's moving pattern. lavfi draws it
    at ~90 fps at this size, so two seconds are drawn once and looped (content repeats every 120
    frames, far beyond any reference an encoder keeps)."""
    loop = os.path.join(os.path.dirname(path), "testsrc2_loop.mkv")
    if not os.path.exists(loop):
        _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
              "testsrc2=s=1920x1080:r=60000/1001", "-frames:v", "120", "-c:v", "ffv1",
              "-pix_fmt", "yuv420p", "-n", loop])
    _run([ffmpeg, "-nostdin", "-hide_banner", "-loglevel", "error", "-stream_loop", "-1",
          "-i", loop, "-frames:v", str(n_payloads * 60), "-c:v", "libx264", "-preset", "ultrafast",
          "-crf", "16", "-pix_fmt", "yuv420p",
          "-x264-params", "keyint=60:min-keyint=60:scenecut=0:bframes=0",
          "-video_track_timescale", "60000", "-an", "-n", path])


def _synthetic_session(work: str):
    from studio.session import Session
    rec = sg.generate(_out(work, "rec"), laps=2, chapters=1, video=_synthetic_picture,
                      frames_per_payload=60)
    session = Session.load(chapters.discover_siblings(rec.paths[0]))
    lap = session.best_lap_id()
    if lap is None:
        raise SystemExit("the synthetic recording segmented into no valid lap")
    return session, int(lap), rec.paths


def _mk_session(recording: str):
    from studio.session import Session
    paths = chapters.discover_siblings(recording)
    return Session.load(paths), MK_LAP, paths


# ------------------------------------------------------------------------------ the measurements
def _render_intermediate(session, lap: int, out: str):
    """The lap's first WINDOW_S through the real Renderer, encoded lossless. Returns the Renderer's
    own (fps, out_w, out_h, frames, encoder it resolved)."""
    cfg = ev.OverlayConfig(out_height=OUT_H, quality=QUALITY)
    spec = ev.build_lap_spec(session, out, lap, config=cfg)
    spec = replace(spec, t1=min(spec.t1, spec.t0 + WINDOW_S), lead_out=0.0, ends_on_finish=False)
    source = spec.source
    assert source is not None, "build_lap_spec always resolves a source"
    real = ev._video_codec_args
    ev._video_codec_args = lambda *a, **k: list(LOSSLESS)       # type: ignore[assignment]
    try:
        renderer = ev.Renderer(session, spec)
        res = renderer.run()
    finally:
        ev._video_codec_args = real                             # type: ignore[assignment]
        source.cleanup()
    return res.fps, res.out_w, res.out_h, res.frames, renderer._encoder


def _vt_args(w: int, h: int, fps: float, g: int) -> list[str]:
    """`_video_codec_args`' exact VideoToolbox argv, plus `-g g` (none for g == 0)."""
    return ev._video_codec_args(ev.VT_H264, w, h, fps, QUALITY) + (["-g", str(g)] if g else [])


def _encode(inter: str, out: str, fps: float, w: int, h: int, g: int) -> float:
    t = time.perf_counter()
    _run([ev.FFMPEG, "-nostdin", "-loglevel", "error", "-r", f"{fps:.6f}", "-i", inter,
          "-map", "0:v:0", *_vt_args(w, h, fps, g), "-an", "-n", out])
    return time.perf_counter() - t


def _packets(path: str) -> tuple[int, int, int]:
    """(video-stream bytes, keyframes, frames) from the packets themselves."""
    rows = _run([ev.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries",
                 "packet=size,flags", "-of", "csv=p=0", path]).stdout.split()
    size = keys = 0
    for row in rows:
        s, flags = row.split(",")[:2]
        size += int(s)
        keys += "K" in flags
    return size, keys, len(rows)


def _quality(enc: str, inter: str, fps: float) -> dict:
    """SSIM (Y, All) and PSNR (y, average) of `enc` against `inter`, frame for frame."""
    graph = "[0:v]split[a0][a1];[1:v]split[b0][b1];[a0][b0]ssim;[a1][b1]psnr"
    err = _run([ev.FFMPEG, "-nostdin", "-hide_banner", "-r", f"{fps:.6f}", "-i", enc,
                "-r", f"{fps:.6f}", "-i", inter, "-lavfi", graph, "-f", "null", "-"]).stderr
    ssim = re.search(r"SSIM Y:([\d.]+).*All:([\d.]+)", err)
    psnr = re.search(r"PSNR y:([\d.]+).*average:([\d.]+)", err)
    if not ssim or not psnr:
        raise RuntimeError(f"no SSIM/PSNR in ffmpeg's output:\n{err[-2000:]}")
    return {"ssim_y": float(ssim[1]), "ssim": float(ssim[2]),
            "psnr_y": float(psnr[1]), "psnr": float(psnr[2])}


def _seek_cmd(t: float, path: str) -> list[str]:
    """THE SCRUB COST, as the rule names it: an accurate seek to `t` and one decoded frame, on the
    hardware decoder. `-ss` before `-i` lands on the keyframe at or before `t` and decodes forward."""
    return [ev.FFMPEG, "-nostdin", "-loglevel", "error", "-hwaccel", "videotoolbox",
            "-ss", f"{t:.6f}", "-i", path, "-frames:v", "1", "-f", "null", "-"]


def _seek_times(files: dict, fps: float, frames: int) -> dict:
    rng = random.Random(SEED)
    targets = [rng.randrange(frames) for _ in range(SEEKS)]
    for path in files.values():                                 # warm the file cache and VT
        for _ in range(2):
            _run(_seek_cmd(0.0, path))
    times = {g: {"target": [], "base": []} for g in files}
    for k in targets:
        calls = [(g, "target", (k + 0.5) / fps) for g in files] + [(g, "base", 0.0) for g in files]
        rng.shuffle(calls)
        for g, kind, t in calls:
            t0 = time.perf_counter()
            _run(_seek_cmd(t, files[g]))
            times[g][kind].append(1000.0 * (time.perf_counter() - t0))
    out = {}
    for g, tt in times.items():
        gross, base = statistics.median(tt["target"]), statistics.median(tt["base"])
        # Context, not the rule: the hardware decoder's cost per frame, from one whole decode net
        # of the same baseline. A seek's net cost is about this times the frames it decodes past
        # the keyframe, (G + 1) / 2 on average.
        t0 = time.perf_counter()
        _run([ev.FFMPEG, "-nostdin", "-loglevel", "error", "-hwaccel", "videotoolbox",
              "-i", files[g], "-f", "null", "-"])
        whole = 1000.0 * (time.perf_counter() - t0)
        out[g] = {"seek_gross_ms": gross, "seek_base_ms": base, "seek_net_ms": gross - base,
                  "decode_ms_per_frame": (whole - base) / frames}
    return out


def measure(mode: str, recording: str | None) -> dict:
    if not ev.videotoolbox_usable():
        raise SystemExit("refusing: no VideoToolbox H.264 session opens here (sandboxed?)")
    work = _workdir()
    sidecar.sidecar_path = lambda p: _out(work, "sidecar.pacer.json")   # type: ignore[assignment]
    before = None
    try:
        if mode == "synthetic":
            session, lap, _ = _synthetic_session(work)
            label = "synthetic GoPro (synth_gopro, 1920x1080 testsrc2 at 59.94)"
        else:
            assert recording is not None, "mk mode names its recording"
            paths = chapters.discover_siblings(recording)
            before = _snapshot(paths)
            session, lap, _ = _mk_session(recording)
            label = f"{os.path.basename(os.path.dirname(recording))} lap {lap}"
        inter = _out(work, "intermediate.mkv")
        fps, w, h, frames, encoder = _render_intermediate(session, lap, inter)
        target = ev.vt_target_bitrate(w, h, fps, ev.quality_params(QUALITY)[0])
        result = {"source": mode, "label": label, "lap": lap, "fps": fps, "size": [w, h],
                  "frames": frames, "render_encoder": encoder, "target_bps": target,
                  "intermediate_bytes": os.path.getsize(inter), "gops": {}}
        print(f"{label}: {frames} frames {w}x{h} @ {fps:g}, intermediate "
              f"{os.path.getsize(inter) / 1e6:.0f} MB, VT target {target:,} bit/s", flush=True)
        first = {}
        for rep in range(REPS):
            for g in GOPS:
                enc = _out(work, f"g{g}_r{rep}.mp4")
                secs = _encode(inter, enc, fps, w, h, g)
                size, keys, n = _packets(enc)
                if n != frames:
                    raise RuntimeError(f"{enc}: {n} frames, the intermediate has {frames}")
                q = _quality(enc, inter, fps)
                row = result["gops"].setdefault(str(g), {k: [] for k in (
                    "yield", "keys", "ssim", "ssim_y", "psnr", "psnr_y", "encode_s", "bytes")})
                row["yield"].append(size * 8 / (target * frames / fps))
                row["keys"].append(keys)
                row["bytes"].append(size)
                row["encode_s"].append(secs)
                for k, v in q.items():
                    row[k].append(v)
                print(f"  g={g or 'default':>7} rep {rep}: {size:,} B yield {row['yield'][-1]:.3f} "
                      f"keys {keys} SSIM {q['ssim']:.5f} PSNR {q['psnr']:.3f} ({secs:.1f} s)",
                      flush=True)
                if rep == 0:
                    first[str(g)] = enc
        result["load_before_seeks"] = os.getloadavg()
        for g, s in _seek_times(first, fps, frames).items():
            result["gops"][g].update(s)
        result["load_after_seeks"] = os.getloadavg()
        _table(result)
        return result
    finally:
        _cleanup(work)
        if before is not None and _snapshot(before) != before:
            raise SystemExit("TRIPWIRE: the recording's chapters changed during the run")


# ------------------------------------------------------------------------------ report and rule
def _mean(res: dict, g: int, key: str) -> float:
    return statistics.fmean(res["gops"][str(g)][key])


def _table(res: dict) -> None:
    minutes = res["frames"] / res["fps"] / 60.0
    print(f"\n{res['label']} — means over {REPS} encodes (seek: first encode, {SEEKS} targets)")
    print("  G        yield         keys/min  SSIM     PSNR dB  seek gross  net ms  "
          "decode ms/frame  encode s")
    for g in GOPS:
        r = res["gops"][str(g)]
        print(f"  {g or 'default':<8} {_mean(res, g, 'yield'):.3f} "
              f"[{min(r['yield']):.3f}-{max(r['yield']):.3f}]  "
              f"{_mean(res, g, 'keys') / minutes:6.1f}  {_mean(res, g, 'ssim'):.5f}  "
              f"{_mean(res, g, 'psnr'):7.3f}  {r['seek_gross_ms']:8.1f}  {r['seek_net_ms']:6.1f}  "
              f"{r['decode_ms_per_frame']:15.2f}  {_mean(res, g, 'encode_s'):6.2f}")
    print(f"  load average {res['load_before_seeks']} -> {res['load_after_seeks']}")


def verdict(synth: dict, mk: dict) -> tuple[bool, list[str]]:
    """The pre-stated rule, evaluated at the value that would ship: round(2 * fps)."""
    g = round(2 * mk["fps"])
    lines, ok = [], []

    def check(name: str, passed: bool, detail: str) -> None:
        ok.append(passed)
        lines.append(f"{'PASS' if passed else 'FAIL'}  {name}: {detail}")

    d_ssim = _mean(mk, g, "ssim") - _mean(mk, 0, "ssim")
    d_psnr = _mean(mk, g, "psnr") - _mean(mk, 0, "psnr")
    check("MK quality gain (SSIM >= +0.002 or PSNR >= +0.3 dB)",
          d_ssim >= GAIN_SSIM or d_psnr >= GAIN_PSNR, f"SSIM {d_ssim:+.5f}, PSNR {d_psnr:+.3f} dB")
    y = _mean(mk, g, "yield")
    check("MK yield within 0.70 +- 0.03", abs(y - YIELD) <= YIELD_TOL, f"{y:.3f}")
    s_ssim = _mean(synth, g, "ssim") - _mean(synth, 0, "ssim")
    s_psnr = _mean(synth, g, "psnr") - _mean(synth, 0, "psnr")
    s_y = _mean(synth, g, "yield") - _mean(synth, 0, "yield")
    check("synthetic no loss (SSIM >= -0.0005, PSNR >= -0.05 dB, |yield move| <= 0.03)",
          s_ssim >= LOSS_SSIM and s_psnr >= LOSS_PSNR and abs(s_y) <= YIELD_TOL,
          f"SSIM {s_ssim:+.5f}, PSNR {s_psnr:+.3f} dB, yield {s_y:+.3f}")
    for res in (synth, mk):
        r, r0 = res["gops"][str(g)], res["gops"]["0"]
        check(f"{res['source']} seek (net <= 100 ms, gross <= 2x default's)",
              r["seek_net_ms"] <= SEEK_NET_MS
              and r["seek_gross_ms"] <= SEEK_GROSS_RATIO * r0["seek_gross_ms"],
              f"net {r['seek_net_ms']:.1f} ms (default {r0['seek_net_ms']:.1f}, ratio "
              f"{r['seek_net_ms'] / max(r0['seek_net_ms'], 1e-9):.1f}x), gross "
              f"{r['seek_gross_ms']:.1f} vs {r0['seek_gross_ms']:.1f} ms "
              f"({r['seek_gross_ms'] / r0['seek_gross_ms']:.2f}x)")
    return all(ok), lines


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args[:1] == ["verdict"] and len(args) == 3:
        with open(args[1], encoding="utf-8") as f1, open(args[2], encoding="utf-8") as f2:
            synth, mk = json.load(f1), json.load(f2)
        adopt, lines = verdict(synth, mk)
        print("\n".join(lines))
        print(f"VERDICT: {'ADOPT -g round(2*fps)' if adopt else 'REFUSE: keep no -g'}")
        return 0
    if args == ["synthetic"]:
        recording = None
    elif args[:1] == ["mk"] and len(args) <= 2:
        recording = os.path.realpath(args[1] if len(args) == 2 else MK_RECORDING)
        if not os.path.isfile(recording):
            raise SystemExit(f"no recording at {recording}")
    else:
        raise SystemExit(USAGE)
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    theme.apply_theme(app)
    print(json.dumps(measure(args[0], recording)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
