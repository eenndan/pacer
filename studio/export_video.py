"""Offline video-overlay export: burn the telemetry overlays onto the GoPro footage and mux a
shareable MP4.

A self-contained OFFLINE renderer with its own frame-by-frame loop (the caller pumps
`Renderer.run_chunk` for a responsive, cancellable UI); it has no dependency on the live Qt event
loop and, like the other analysis/IO modules, is fed entirely by a `Session` (never the compiled
bindings). QPainter/QImage compositing is pure off-screen drawing, so the burned-in overlays match
the live widgets.

Pipeline (raw-video pipe, the simplest path needing no extra Python codec dep):
  1. DECODE: `ffmpeg -ss t0 -i src -t dur -vf scale=W:H -pix_fmt rgb24 -f rawvideo pipe:1` — trim to
     the lap's media-time window, scale, stream W*H*3 bytes/frame to our stdout.
  2. COMPOSITE: each frame -> QImage (RGB888); a QPainter paints the overlays at the frame's media
     time, reading the same Session/gmeter accessors the live readout uses.
  3. MUX: `ffmpeg -f rawvideo -i pipe:0 -ss t0 -i src -t dur -map 0:v -map 1:a ...` re-encodes our
     frames + the source audio over the same window.

Decode/mux fps are PINNED to one output fps for A/V sync (frame N out == frame N in). Scope: ONE
selected lap. `overlay_values_at` mirrors app._apply_readout, so a frame grab at t shows what the
app shows at t.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from . import gmeter_overlay, theme
from ._signal import fmt_time

# --------------------------------------------------------------------------- ffmpeg discovery
# Resolved lazily so importing this module never requires ffmpeg (the unit tests mock the
# subprocess; only a real render needs the binaries). The pixi env puts them on PATH for the app.
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def ffmpeg_available() -> bool:
    """True iff both ffmpeg and ffprobe are on PATH."""
    return shutil.which(FFMPEG) is not None and shutil.which(FFPROBE) is not None


# --------------------------------------------------------------------------- encoder selection
# VT_H264 = the Apple media-engine HW encoder (bitrate-driven; offloads the encode + frees CPU for
# the composite). SW_H264 = the libx264 CRF fallback, used when a VT session won't open — probed
# once at startup and retried on a runtime encode failure.
VT_H264 = "h264_videotoolbox"
SW_H264 = "libx264"

# Target VideoToolbox bitrate as bits-per-pixel-per-frame: ~0.10 bpp ~= 12.4 Mbit/s at 1080p60,
# floored at _MIN_VT_BITRATE so tiny test sizes aren't starved.
_BITS_PER_PIXEL = 0.10
_MIN_VT_BITRATE = 2_000_000

# Quality presets: a level maps to BOTH encoder knobs (a VideoToolbox bpp + a matching libx264 CRF)
# so the choice means the same on either encoder. "high" = visually-lossless (0.10 bpp / CRF 20);
# "standard" = leaner (~0.06 bpp / CRF 23).
_QUALITY_PRESETS = {
    "standard": {"bpp": 0.060, "crf": 23},
    "high": {"bpp": _BITS_PER_PIXEL, "crf": 20},
}
_DEFAULT_QUALITY = "high"


def quality_params(quality: str | None) -> tuple[float, int]:
    """(bits-per-pixel, crf) for a quality level ("standard"/"high"); unknown -> the "high" default.
    The ONE place the quality picker's level becomes concrete encoder numbers, so both encoder
    paths (VideoToolbox bitrate / libx264 CRF) and the tests reason about it in one spot."""
    preset = _QUALITY_PRESETS.get((quality or _DEFAULT_QUALITY).lower(),
                                  _QUALITY_PRESETS[_DEFAULT_QUALITY])
    return preset["bpp"], preset["crf"]


def vt_target_bitrate(out_w: int, out_h: int, fps: float, bpp: float = _BITS_PER_PIXEL) -> int:
    """A sensible VideoToolbox target bitrate (bits/s) for an out_w x out_h @ fps stream — `bpp`
    bits per pixel per frame, floored. `bpp` comes from the chosen quality level (quality_params);
    the default preserves the original 0.10-bpp 'high' bitrate. Used only for the hardware encoder
    (libx264 is CRF-driven — see quality_params for its matching CRF)."""
    bits = int(out_w * out_h * max(fps, 1.0) * bpp)
    return max(bits, _MIN_VT_BITRATE)


def videotoolbox_encoder_available() -> bool:
    """True iff ffmpeg lists `h264_videotoolbox` (compiled in). Cached. (See videotoolbox_usable for
    whether a session actually opens.)"""
    cached = getattr(videotoolbox_encoder_available, "_cached", None)
    if cached is not None:
        return cached
    ok = False
    if shutil.which(FFMPEG) is not None:
        try:
            out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                                 capture_output=True, text=True, timeout=20).stdout
            ok = VT_H264 in out
        except (OSError, subprocess.SubprocessError):
            ok = False
    videotoolbox_encoder_available._cached = ok  # type: ignore[attr-defined]
    return ok


def videotoolbox_usable() -> bool:
    """Confirm a VideoToolbox H.264 hardware session ACTUALLY opens on this machine by running a
    tiny real encode of a synthetic clip through `h264_videotoolbox`. Static encoder presence
    (videotoolbox_encoder_available) does not guarantee a session opens — it can fail on pixel
    format / size / a busy media engine — so this runtime probe gates auto-selection. Cached (a
    fixed machine capability)."""
    cached = getattr(videotoolbox_usable, "_cached", None)
    if cached is not None:
        return cached
    ok = False
    if videotoolbox_encoder_available():
        try:
            # 64x64, 2 frames — minimal but real; -f null discards the muxed output.
            r = subprocess.run(
                [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin",
                 "-f", "lavfi", "-i", "testsrc=size=64x64:rate=2:duration=1",
                 "-c:v", VT_H264, "-pix_fmt", "yuv420p", "-frames:v", "2",
                 "-f", "null", "-"],
                capture_output=True, timeout=30)
            ok = r.returncode == 0
        except (OSError, subprocess.SubprocessError):
            ok = False
    videotoolbox_usable._cached = ok  # type: ignore[attr-defined]
    return ok


def resolve_encoder(choice: str) -> str:
    """Resolve an encoder `choice` to a concrete ffmpeg `-c:v` name. The ONE place encoder policy
    lives, so the app/tests can reason about (and override) the choice:

      * "auto"  -> h264_videotoolbox if a real VT session opens here, else libx264 (the safe SW path)
      * "videotoolbox"/"h264_videotoolbox"/"gpu"/"hw"/"vt" -> the VT encoder if merely COMPILED IN
        (caller forced it; the render's libx264 fallback still covers a session that won't open)
      * "libx264"/"software"/"x264"/"cpu"/"sw" -> always libx264

    Anything unrecognized falls back to "auto" semantics."""
    c = (choice or "auto").lower()
    if c in ("libx264", "software", "sw", "x264", "cpu"):
        return SW_H264
    if c in ("videotoolbox", "h264_videotoolbox", "vt", "hw", "gpu"):
        return VT_H264 if videotoolbox_encoder_available() else SW_H264
    return VT_H264 if videotoolbox_usable() else SW_H264


def videotoolbox_decode_available() -> bool:
    """True iff ffmpeg lists `videotoolbox` as a hardware-acceleration method (so `-hwaccel
    videotoolbox` is accepted). Cached; cheap (`ffmpeg -hwaccels`)."""
    cached = getattr(videotoolbox_decode_available, "_cached", None)
    if cached is not None:
        return cached
    ok = False
    if shutil.which(FFMPEG) is not None:
        try:
            out = subprocess.run([FFMPEG, "-hide_banner", "-hwaccels"],
                                 capture_output=True, text=True, timeout=20).stdout
            ok = "videotoolbox" in out
        except (OSError, subprocess.SubprocessError):
            ok = False
    videotoolbox_decode_available._cached = ok  # type: ignore[attr-defined]
    return ok


def resolve_hwaccel_decode(choice: str | bool, encoder: str) -> bool:
    """Whether to add `-hwaccel videotoolbox` to the decode. `True`/`False` force it; "auto" turns
    it ON when the export is ALSO using the VideoToolbox encoder (so the whole decode+encode runs on
    the media engine, freeing the CPU for the parallel composite — the configuration that unblocks a
    core-starved machine) AND ffmpeg advertises the videotoolbox hwaccel. Forcing True still checks
    availability so an env without it just decodes in software rather than erroring."""
    if choice is True:
        return videotoolbox_decode_available()
    if choice is False:
        return False
    c = str(choice or "auto").lower()
    if c in ("0", "false", "no", "off", "software", "sw", "cpu", "none"):
        return False
    if c in ("1", "true", "yes", "on", "videotoolbox", "vt", "hw", "gpu"):
        return videotoolbox_decode_available()
    # "auto": pair the hw decode with the hw encoder.
    return encoder == VT_H264 and videotoolbox_decode_available()


# --------------------------------------------------------------------------- configuration
# 1080p default; width follows the source aspect at render time (height is the controlling dim).
@dataclass(frozen=True)
class OverlayConfig:
    """Layout + output knobs for the export. All overlay placements are FRACTIONS of the frame so
    the composition scales with `out_height`. The defaults reproduce the app's corner placements
    (g-meter top-right, readout bottom-left, map inset bottom-right, lap strip top-left)."""
    out_height: int = 1080            # controlling output dimension (width follows source aspect)
    quality: str = "high"             # "high"/"standard"; resolved to encoder numbers by quality_params
    fps: float | None = None          # explicit output fps; None = source fps, then fps_cap applies
    # Cap the output fps: a telemetry overlay reads identically at 30 as at 59.94 but 30 ~halves
    # every per-frame cost. None keeps the source rate; an explicit `fps` overrides the cap.
    fps_cap: float | None = 30.0
    encoder: str = "auto"             # "auto"/"libx264"/"videotoolbox" (see resolve_encoder)
    hwaccel_decode: str | bool = "auto"  # "auto" pairs the hw decode with the hw encoder (see resolve_hwaccel_decode)
    # No-op (kept for back-compat); the renderer is single-threaded.
    workers: int | None = None
    # No-progress WATCHDOG (seconds): if the frame counter doesn't advance for this long the render
    # is presumed WEDGED (hung VT session / stuck pipe), aborted cleanly, then retried ONCE on
    # libx264 — what makes an infinite hang impossible. Generous so a merely-slow machine never trips.
    watchdog_timeout: float = 30.0
    # g-meter dial: a square pinned to the TOP-RIGHT, side = this fraction of frame height.
    gmeter_frac: float = 0.26
    margin_frac: float = 0.022        # uniform inset from the frame edge for all elements
    # track-map inset: bottom-right box, this fraction of frame width / height.
    map_w_frac: float = 0.22
    map_h_frac: float = 0.22
    # readout box (Δ / speed): bottom-left; sized to its text, this is the font height fraction.
    readout_h_frac: float = 0.040
    # lap/sector strip: a slim bar across the TOP-LEFT.
    strip_h_frac: float = 0.040


# --------------------------------------------------------------------------- chaptered source
# A chaptered GoPro recording lays its files on one global media clock (chapter i covers global
# [offset_i, offset_i+dur_i); studio/chapters.ChapterMap). ffmpeg can only `-ss` into a SINGLE
# file's own (local) clock, so a global window resolves to either one chapter file (offset =
# chapter.offset) or a CONCAT over a seam-crossing span. The concat demuxer plays the spanned files
# back-to-back as one stream; the first chapter carries a concat `inpoint` at the window's local
# start (a fast keyframe seek that, unlike a plain `-ss` before a concat input, actually lands),
# making that stream begin at the lap so its local clock is 0 at the global t0.
#
# `time_offset` is the global->local shift (local = global - time_offset): the chapter offset for a
# single chapter, t0 for a concat span, 0 for a plain single file (global == local).
@dataclass(frozen=True)
class VideoSource:
    """The file-local ffmpeg source for an export, resolved from the global window via a
    ChapterMap. `input_args()` is the ffmpeg `-i ...` portion (a single `-i file`, or the concat
    demuxer over a written list file); `probe_path` is the concrete file ffprobe reads for the
    stream size/fps; `time_offset` converts a GLOBAL media time to this source's own clock
    (`local = global - time_offset`). `cleanup()` removes any temp concat-list file."""
    probe_path: str                 # the concrete file ffprobe reads (size/fps)
    time_offset: float              # global -> local shift: local = global - time_offset
    concat_list_path: str | None = None   # set iff this is a concat-demuxer source (seam case)

    def input_args(self) -> list[str]:
        """ffmpeg input args: the concat demuxer over the span, else `-i <file>`."""
        if self.concat_list_path is not None:
            return ["-f", "concat", "-safe", "0", "-i", self.concat_list_path]  # -safe 0: abs paths
        return ["-i", self.probe_path]

    def cleanup(self) -> None:
        """Remove the temp concat-list file (best-effort), if this source wrote one."""
        if self.concat_list_path is not None:
            try:
                os.remove(self.concat_list_path)
            except OSError:
                pass


def single_file_source(path: str) -> VideoSource:
    """A VideoSource for a plain single file (no chapters): global == local, offset 0."""
    return VideoSource(probe_path=path, time_offset=0.0)


def resolve_video_source(chapter_map, t0: float, t1: float,
                         tmp_dir: str | None = None) -> VideoSource:
    """Resolve the GLOBAL window [t0, t1) to a VideoSource via a `ChapterMap`:
      * single chapter -> `-i <that file>`, time_offset = chapter.offset;
      * spans a seam   -> a concat demuxer over chapters [i0..i1] with an `inpoint` at the lap start
                          on the first; time_offset = t0 (the lap is the concatenation's t=0). The
                          concat list is written under `tmp_dir`; the caller frees it via cleanup().
    Raises ValueError if `chapter_map` has no chapters."""
    chs = list(getattr(chapter_map, "chapters", []) or [])
    if not chs:
        raise ValueError("resolve_video_source needs a ChapterMap with at least one chapter")
    i0 = chapter_map.chapter_at(t0)
    # End is half-open: nudge a window ending exactly on a seam back into the chapter it played, so a
    # lap ending at offset_{k+1} doesn't pull in a needless extra chapter.
    i1 = chapter_map.chapter_at(max(t0, t1 - 1e-6))
    start = chs[i0]
    if i1 <= i0:
        return VideoSource(probe_path=start.path, time_offset=float(start.offset))
    # Concat the spanned chapters; inpoint trims the first to the lap start (fast keyframe seek), so
    # the stream begins at t0 -> time_offset = t0 and decode/encode seek with -ss 0.
    span = chs[i0:i1 + 1]
    inpoint = max(0.0, t0 - start.offset)        # local start within the first spanned chapter
    tmp = tmp_dir or os.environ.get("TMPDIR") or "/tmp"
    list_path = _mk_concat_list(span, tmp, first_inpoint=inpoint)
    return VideoSource(probe_path=start.path, time_offset=float(t0),
                       concat_list_path=list_path)


def _mk_concat_list(chapters_span, tmp_dir: str,
                    first_inpoint: float | None = None) -> str:
    """Write an ffmpeg concat-demuxer list file for `chapters_span` into `tmp_dir`; returns its
    path. `first_inpoint` (seconds) adds an `inpoint` after the first file (lap-start keyframe
    seek)."""
    import tempfile
    fd, list_path = tempfile.mkstemp(prefix="pacer_export_concat_", suffix=".txt", dir=tmp_dir)
    lines = []
    for idx, c in enumerate(chapters_span):
        ap = os.path.abspath(c.path)
        # concat-demuxer quoting: a literal ' inside a single-quoted token is '\''.
        esc = ap.replace("'", "'\\''")
        lines.append(f"file '{esc}'\n")
        if idx == 0 and first_inpoint and first_inpoint > 0:
            lines.append(f"inpoint {first_inpoint:.6f}\n")
    with os.fdopen(fd, "w") as f:
        f.write("".join(lines))
    return list_path


# --------------------------------------------------------------------------- export spec
@dataclass
class ExportSpec:
    """Everything a render needs, resolved up front so the render loop is pure mechanism.

    `t0`/`t1` = the GLOBAL media-clock window (a lap). `source` resolves it to the right chapter
    file(s) + a local seek offset; `src_path` is a single-file back-compat shortcut (synthesized into
    `source` if `source` is omitted). ffmpeg seeks with the source-LOCAL time (`t0/t1 -
    source.time_offset`), never the global t0. `lap_id` is the lap whose Δ baseline + sector strip +
    g-meter scope are shown."""
    out_path: str
    lap_id: int
    t0: float
    t1: float
    src_path: str = ""
    config: OverlayConfig = field(default_factory=OverlayConfig)
    source: VideoSource | None = None

    def __post_init__(self):
        # Back-compat: a caller that passed only `src_path` (the legacy single-file API + the
        # mocked/real tests) gets a single-file source at offset 0 (global == local, unchanged).
        if self.source is None:
            if not self.src_path:
                raise ValueError("ExportSpec needs either a `source` or a `src_path`")
            self.source = single_file_source(self.src_path)
        elif not self.src_path:
            self.src_path = self.source.probe_path

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)

    @property
    def local_t0(self) -> float:
        """The file-LOCAL seek time (what ffmpeg `-ss` gets): the global t0 shifted into the
        resolved source's own clock. For a single-file/offset-0 source this equals `t0`."""
        return self.t0 - self.source.time_offset


# --------------------------------------------------------------------------- trim math
def lap_window_for_export(session, lap_id: int) -> tuple[float, float] | None:
    """The MEDIA-clock (t0, t1) window for `lap_id` (== Session.lap_window: start, start+lap_time),
    or None if unusable. Half-open, so every frame in [t0, t1) reports this lap."""
    win = session.lap_window(lap_id)
    if win is None:
        return None
    t0, t1 = win
    if not (t1 > t0):
        return None
    return float(t0), float(t1)


def frame_times(t0: float, t1: float, fps: float) -> np.ndarray:
    """The media-clock timestamp of each output frame for a [t0, t1) window at `fps`. ffmpeg's
    rawvideo output emits ceil(duration*fps) frames starting at t0 spaced 1/fps apart; we mirror
    that so the i-th frame we composite is stamped with the time ffmpeg decoded it from. Used to
    drive the per-frame overlay lookups and to size the progress bar."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    n = int(np.ceil((t1 - t0) * fps - 1e-9))
    n = max(n, 0)
    return t0 + np.arange(n) / fps


def resolve_fps(cfg: OverlayConfig, src_fps: float) -> float:
    """The output fps for the render: an explicit `cfg.fps` wins; otherwise the source rate, then
    `cfg.fps_cap` caps it (so a 59.94 fps GoPro exports at 30 by default — half the frames, half the
    work, no perceptible loss for a telemetry overlay). Never exceeds the source rate (capping up
    would only duplicate frames). Guards a non-positive source by falling back to the cap/30."""
    if cfg.fps:
        return float(cfg.fps)
    fps = float(src_fps) if src_fps and src_fps > 0 else (cfg.fps_cap or 30.0)
    if cfg.fps_cap:
        fps = min(fps, float(cfg.fps_cap))
    return fps


# --------------------------------------------------------------------------- per-frame values
@dataclass
class OverlayValues:
    """The telemetry values shown for ONE frame at media time `t` — exactly what the live readout
    shows at t (so a frame grab can be cross-checked against the app). `speed_kmh`/`delta_s` are
    None outside a valid lap; `g` is None when there's no IMU signal."""
    t: float
    lap_id: int | None
    speed_kmh: float | None
    delta_s: float | None
    g: tuple[float, float, float] | None
    marker_index: int | None


def overlay_values_at(session, t: float) -> OverlayValues:
    """Resolve the overlay values at media time `t` the SAME way app._apply_readout does:

      * lap        = session.lap_at_time(t)
      * marker idx = session.index_at_time(t)        (nearest trace sample)
      * speed km/h = session.tv[idx]                 (the per-sample km/h array)
      * Δ-to-best  = session.delta_at_lap(lap, t)    (normalized-distance vs the best/ref lap)
      * g          = session.g_at_time(t)            (kart-frame lat/long/total in g)

    Single-sourcing these here keeps the burned-in numbers identical to the app's, and makes the
    per-frame lookup unit-testable against a synthetic Session (no Qt, no ffmpeg)."""
    lap_id = session.lap_at_time(t)
    i = session.index_at_time(t)
    speed = float(session.tv[i]) if i is not None and len(session.tv) else None
    delta = session.delta_at_lap(lap_id, t) if lap_id is not None else None
    g = session.g_at_time(t) if getattr(session, "has_gmeter", False) else None
    return OverlayValues(t=t, lap_id=lap_id, speed_kmh=speed, delta_s=delta, g=g, marker_index=i)


# --------------------------------------------------------------------------- ffmpeg commands
def output_size(src_w: int, src_h: int, cfg: OverlayConfig) -> tuple[int, int]:
    """Output (W, H): height is `cfg.out_height`; width follows the source aspect, rounded to an
    EVEN number (libx264/yuv420p requires even dimensions). Never upscales past the source."""
    h = min(int(cfg.out_height), int(src_h)) if src_h else int(cfg.out_height)
    if src_h:
        w = int(round(src_w * (h / src_h)))
    else:
        w = h * 16 // 9
    w += w & 1
    h += h & 1
    return max(w, 2), max(h, 2)


def build_decode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float,
                     hwaccel: bool = False) -> list[str]:
    """DECODE argv: -ss spec.local_t0 before the input (keyframe seek) + -t duration, scale to
    out_w x out_h, force the constant `fps`, emit rgb24 rawvideo to stdout; -an/-sn/-dn drop
    non-video. Input is spec.source.input_args() (chapter file or concat span); the source-LOCAL
    seek is what makes a chaptered export read the right footage. `hwaccel` adds `-hwaccel
    videotoolbox` to offload the decode (a big CPU relief on a core-starved machine)."""
    hw = ["-hwaccel", "videotoolbox"] if hwaccel else []
    return [
        FFMPEG, "-nostdin", "-loglevel", "error",
        *hw,
        "-ss", f"{spec.local_t0:.6f}", *spec.source.input_args(), "-t", f"{spec.duration:.6f}",
        "-vf", f"scale={out_w}:{out_h},fps={fps:.6f}",
        "-an", "-sn", "-dn",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]


def _video_codec_args(encoder: str, out_w: int, out_h: int, fps: float,
                      quality: str = _DEFAULT_QUALITY) -> list[str]:
    """The `-c:v ...` portion of the encode argv for the resolved `encoder`, at the chosen `quality`
    level (the export-quality picker's bitrate knob; quality_params -> (bpp, crf)):

      * h264_videotoolbox — the Apple media-engine (GPU) encoder. Quality is bitrate-driven, so we
        pass the quality level's bpp target (vt_target_bitrate) + a matching cap; `-allow_sw 1` lets
        ffmpeg fall back to VideoToolbox's own software path rather than erroring if a HW session
        can't open; `-realtime 0` favours quality over latency (this is an offline export, not a
        live stream). `-color_range tv` silences the "range not set" note and pins MPEG/limited.
      * libx264 — the software fallback: veryfast + the quality level's CRF (20 high / 23 standard).

    Both end yuv420p + faststart so the MP4 is broadly playable and streams (moov atom up front)."""
    bpp, crf = quality_params(quality)
    if encoder == VT_H264:
        br = vt_target_bitrate(out_w, out_h, fps, bpp)
        return [
            "-c:v", VT_H264,
            "-b:v", str(br), "-maxrate", str(br), "-bufsize", str(br * 2),
            "-allow_sw", "1", "-realtime", "0",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-movflags", "+faststart",
        ]
    return [
        "-c:v", SW_H264, "-preset", "veryfast", "-crf", str(crf),
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]


def build_encode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float,
                     encoder: str = SW_H264) -> list[str]:
    """MUX argv: input 0 = our rgb24 rawvideo on stdin; input 1 = the source audio over the SAME
    source-LOCAL window (mirrors the decode, so audio stays in sync across a seam). Map video+audio,
    encode H.264 (`encoder`) + AAC, -shortest."""
    return [
        FFMPEG, "-nostdin", "-loglevel", "error", "-y",
        # input 0: raw composited video from our pipe
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", f"{fps:.6f}",
        "-i", "pipe:0",
        # input 1: source audio, same source-LOCAL window (mirrors the decode's input + seek)
        "-ss", f"{spec.local_t0:.6f}", *spec.source.input_args(), "-t", f"{spec.duration:.6f}",
        "-map", "0:v:0", "-map", "1:a:0?",
        *_video_codec_args(encoder, out_w, out_h, fps, spec.config.quality),
        "-c:a", "aac", "-b:a", "192k",
        "-shortest",
        spec.out_path,
    ]


def probe_video_size(src_path: str) -> tuple[int, int, float]:
    """(width, height, fps) of the source's first video stream via ffprobe. fps is parsed from the
    `r_frame_rate` rational (e.g. "60000/1001"). Raises on a missing/blank probe so a broken source
    fails loudly rather than rendering a 0-size frame."""
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height,r_frame_rate",
         "-of", "default=noprint_wrappers=1:nokey=1", src_path],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    if len(out) < 3:
        raise RuntimeError(f"ffprobe could not read video stream size from {src_path}")
    w, h = int(out[0]), int(out[1])
    num, _, den = out[2].partition("/")
    fps = float(num) / float(den) if den else float(num)
    return w, h, fps


def probe_source_duration(source: VideoSource) -> float | None:
    """The total media duration (seconds) of a resolved `VideoSource` — the single chapter file's
    duration, or the SUM across a concat span (the concat demuxer plays them as one stream, so its
    length is the sum). Returns None if ffprobe can't read it (then the window guard is skipped
    rather than refusing a render over an unreadable duration). Used only by the up-front
    window-sanity guard, so a failure here is non-fatal."""
    args = (["-f", "concat", "-safe", "0", "-i", source.concat_list_path]
            if source.concat_list_path is not None else ["-i", source.probe_path])
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", *args,
             "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1"],
            capture_output=True, text=True, check=True).stdout.strip()
        return float(out) if out and out.lower() != "n/a" else None
    except Exception:  # noqa: BLE001 - a non-fatal best-effort probe: any failure -> skip the guard
        # ffprobe missing/failed/blank, or (in a mocked render) subprocess is stubbed: don't block a
        # render on an unreadable duration — the watchdog + NoFramesError still backstop a bad window.
        return None


def guard_validate_window(spec: ExportSpec) -> None:
    """Refuse an obviously-invalid export window BEFORE launching ffmpeg, so a doomed render can
    never sit on an empty progress bar for minutes (the chaptered-export symptom: a global t0
    mapped past the resolved chapter's end -> zero frames).

    Raises ValueError when:
      * the window is empty (duration <= 0), or
      * the source-LOCAL seek time is negative (a window before the source's start), or
      * the source-LOCAL seek time lands at/after the probed source duration (with a small
        epsilon) — i.e. the seek is past the end of the file(s), which decodes nothing.

    The duration check is skipped silently if ffprobe couldn't read a duration (we don't block a
    render on an unreadable probe). This is a FAST, message-bearing failure — distinct from the
    no-progress watchdog, which catches a render that launches but then wedges."""
    if spec.duration <= 0:
        raise ValueError(
            f"export window is empty (t0={spec.t0:.3f}, t1={spec.t1:.3f}); nothing to render")
    local = spec.local_t0
    if local < -1e-3:
        raise ValueError(
            f"export window starts before the source (local t0={local:.3f}s < 0) — "
            f"the lap window does not map into the resolved video source")
    dur = probe_source_duration(spec.source)
    if dur is not None and local >= dur - 1e-3:
        raise ValueError(
            f"export window is past the end of the video source "
            f"(local seek {local:.1f}s >= source duration {dur:.1f}s) — the lap/window does not "
            f"map onto any footage in the resolved chapter(s); nothing would be rendered")


# --------------------------------------------------------------------------- compositing
def _c(token: str, alpha: int | None = None) -> QColor:
    col = QColor(token)
    if alpha is not None:
        col.setAlpha(alpha)
    return col


def _font(px: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPixelSize(max(1, int(round(px))))
    f.setBold(bold)
    return f


# --------------------------------------------------------------------------- export palette
# Export palette: opaque, high-contrast colours for burning over bright footage (the live theme.C
# is dim-on-dark and washes out). Legibility comes from a dark halo under every glyph/line (see
# _draw_text / _stroke_polyline), which is what lets the g-meter and map drop their grey backdrops.
class EXPORT:
    """Vivid, opaque, export-tuned colours for burning overlays onto BRIGHT footage. Separate from
    the live theme tokens (which are dim-on-dark). Hex strings; use these in the composite only."""

    # text / structure
    text = "#FFFFFF"            # primary readout text — pure white (max contrast over footage)
    text_dim = "#E6EAF0"        # secondary text (units / labels) — near-white, still bright
    halo = "#0A0C10"            # the dark outline/shadow colour under every bright element
    # accent (amber) — brighter + fully saturated vs the theme's #F5A623
    accent = "#FFB21E"          # primary accent: lap line, g-dial envelope, lap-strip fill
    accent_bright = "#FFD34D"   # highlight (g dot glow, marker ring)
    # semantics — PUNCHY, fully-saturated ahead/behind (theme's #5DD6A0/#E8746B are too soft here)
    ahead = "#26E07A"           # ahead / gaining — vivid green
    behind = "#FF4D4D"          # behind / losing — vivid red
    neutral = "#FFFFFF"         # dead-even Δ — white (no semantic colour)
    marker = "#FF5A36"          # map current-position marker — hot coral (pops on green/grey)
    grid = "#FFFFFF"            # g-dial rings / crosshair — white at moderate alpha (set per use)


# Remap theme.delta_colour semantic tokens to the punchier EXPORT palette (the 3-way decision stays
# in theme).
_DELTA_THEME_TO_EXPORT = {theme.C.ahead: EXPORT.ahead, theme.C.behind: EXPORT.behind}


def export_delta_colour(d: float | None) -> str:
    """Export 3-way delta colour: theme.delta_colour decides ahead/behind/even; we remap its token
    to the vivid EXPORT palette (a neutral/None Δ -> EXPORT white)."""
    sem = theme.delta_colour(d)
    return EXPORT.neutral if sem is None else _DELTA_THEME_TO_EXPORT[sem]


def _draw_text(p: QPainter, pos, text: str, font: QFont, colour: str,
               *, halo: float = 2.2, halo_alpha: int = 235,
               shadow: tuple[float, float] | None = (1.5, 1.5)) -> None:
    """Draw `text` at baseline `pos` (a QPointF) with a dark OUTLINE (and an optional offset drop
    SHADOW) under a bright fill, so it reads over BOTH a bright and a dark background — the single
    biggest legibility win for a burned overlay (the roadmap's headline ask).

    Implementation: build the glyph outline as a QPainterPath and stroke it with a wide dark pen
    (the halo) before filling it with `colour`. Stroking the path (vs re-drawing the text offset in
    N directions) gives a clean even outline at any size and is cheap (a handful of glyphs/frame).
    `halo` is the outline half-width in px; `shadow`, if given, lays a soft dark copy down-right
    first so the text also lifts off a busy mid-tone background."""
    path = QPainterPath()
    path.addText(pos, font, text)
    p.save()
    p.setRenderHint(QPainter.Antialiasing, True)
    if shadow is not None:
        sp = QPainterPath()
        sp.addText(QPointF(pos.x() + shadow[0], pos.y() + shadow[1]), font, text)
        p.setPen(Qt.NoPen)
        p.setBrush(_c(EXPORT.halo, 150))
        p.drawPath(sp)
    if halo > 0:
        pen = QPen(_c(EXPORT.halo, halo_alpha), halo * 2.0)
        pen.setJoinStyle(Qt.RoundJoin)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(_c(colour))
    p.drawPath(path)
    p.restore()


def _text_at(p: QPainter, rect: QRectF, flags, text: str, font: QFont, colour: str,
             **kw) -> float:
    """Lay out `text` within `rect` honouring `flags` (Qt alignment) and draw it OUTLINED via
    `_draw_text`. Returns the advance width of the text (so callers can place a following run).
    A thin wrapper that turns the boundingRect/alignment math into the baseline point `_draw_text`
    wants, so the rest of the code reads like ordinary `drawText` calls but gets the halo."""
    fm = QFontMetricsF(font)
    br = fm.boundingRect(text)
    w = fm.horizontalAdvance(text)
    if flags & Qt.AlignHCenter:
        x = rect.x() + (rect.width() - w) / 2.0
    elif flags & Qt.AlignRight:
        x = rect.right() - w
    else:
        x = rect.x()
    if flags & Qt.AlignVCenter:
        y = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2.0
    elif flags & Qt.AlignBottom:
        y = rect.bottom() - fm.descent()
    else:
        y = rect.y() + fm.ascent()
    # boundingRect can carry a small left bearing; nudge so left-aligned text starts at rect.x().
    _draw_text(p, QPointF(x - br.x() if (flags & Qt.AlignLeft) else x, y), text, font, colour, **kw)
    return w


def _stroke_polyline(p: QPainter, poly: QPolygonF, colour: str, width: float,
                     *, halo: float = 2.0, halo_alpha: int = 200) -> None:
    """Draw a polyline as a bright `colour` stroke over a wider dark HALO, so the racing line reads
    over both bright and dark ground without a backing box (the map-inset restyle). Two passes: the
    dark halo (width + 2*halo) first, then the bright line."""
    if poly.size() < 2:
        return
    p.setBrush(Qt.NoBrush)
    if halo > 0:
        hp = QPen(_c(EXPORT.halo, halo_alpha), width + 2 * halo)
        hp.setJoinStyle(Qt.RoundJoin)
        hp.setCapStyle(Qt.RoundCap)
        p.setPen(hp)
        p.drawPolyline(poly)
    lp = QPen(_c(colour), width)
    lp.setJoinStyle(Qt.RoundJoin)
    lp.setCapStyle(Qt.RoundCap)
    p.setPen(lp)
    p.drawPolyline(poly)


class _MapInset:
    """Track-map inset for the export: the exported lap's racing line is projected once and baked
    into a cached RGBA layer (re-rasterizing it per frame dominated render cost); each frame blits
    the layer + draws a glowing marker with a short comet tail. A degenerate lap trace falls back to
    the full-session trace line so the inset is never empty."""

    def __init__(self, session, box: QRectF, lap_id: int, scale_k: float = 1.0):
        self._box = box
        self._k = max(0.5, float(scale_k))   # size scale (1.0 at 1080p; see OverlayPainter)
        xs = np.asarray(session.tx, dtype=float)
        ys = np.asarray(session.ty, dtype=float)
        self._ok = len(xs) >= 2 and len(ys) >= 2
        if not self._ok:
            return
        # The exported lap's own line — the ONLY line drawn. We project it (and find the marker's
        # position along it for the tail). The full-session arrays are kept only as a fallback line
        # and to map a marker_index (which indexes the full trace) to a frame point.
        lx = ly = None
        got = session._lap_trace_xyt(lap_id) if hasattr(session, "_lap_trace_xyt") else None
        if got is not None:
            glx, gly, _ = got
            if len(glx) >= 2:
                lx, ly = np.asarray(glx, dtype=float), np.asarray(gly, dtype=float)
        # Fit the LAP's bbox (not the whole session) into the box so a single lap fills the inset;
        # fall back to the full-trace bbox when the lap line is degenerate.
        fitx, fity = (lx, ly) if lx is not None else (xs, ys)
        pad = 0.12
        x0, x1 = float(fitx.min()), float(fitx.max())
        y0, y1 = float(fity.min()), float(fity.max())
        sx = (x1 - x0) or 1.0
        sy = (y1 - y0) or 1.0
        bw = box.width() * (1 - 2 * pad)
        bh = box.height() * (1 - 2 * pad)
        scale = min(bw / sx, bh / sy)
        cx_off = box.x() + box.width() / 2 - scale * (x0 + x1) / 2
        cy_off = box.y() + box.height() / 2 + scale * (y0 + y1) / 2  # +: undo the Y flip below

        def proj(px, py):
            return QPointF(cx_off + scale * px, cy_off - scale * py)

        self._proj = proj
        self._xs, self._ys = xs, ys
        if lx is not None:
            line_poly = QPolygonF([proj(px, py) for px, py in zip(lx, ly, strict=True)])
            self._lap_pts = line_poly                       # for the comet tail
        else:
            line_poly = QPolygonF([proj(px, py) for px, py in zip(xs, ys, strict=True)])
            self._lap_pts = None
        # --- bake the static lap line into a cached RGBA image (no box, no full trace), sized to
        # the inset's bottom-right corner. Painted ONCE; `paint` only blits it + draws the marker.
        self._layer = self._bake_layer(box, line_poly, self._k)

    @staticmethod
    def _bake_layer(box: QRectF, line_poly: QPolygonF, k: float) -> QImage:
        """Bake the unchanging map art — JUST the exported lap's racing line, vivid amber over a dark
        halo, no backdrop box and no full-session trace — once into a transparent ARGB32 image (the
        per-frame marker is drawn over the blit). The halo (see `_stroke_polyline`) is what replaces
        the dropped box: the line reads on bright sky AND dark tarmac without a grey panel."""
        w = max(1, int(np.ceil(box.right())) + 4)
        h = max(1, int(np.ceil(box.bottom())) + 4)
        layer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        layer.fill(Qt.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.Antialiasing, True)
        # 3 passes for a self-contained glow so the line reads on any background: soft dark
        # underglow, dark outline, bright-white line (white keeps it distinct from the amber
        # dial/marker).
        soft = QPen(_c(EXPORT.halo, 130), 12.0 * k)
        soft.setJoinStyle(Qt.RoundJoin)
        soft.setCapStyle(Qt.RoundCap)
        p.setBrush(Qt.NoBrush)
        p.setPen(soft)
        p.drawPolyline(line_poly)
        _stroke_polyline(p, line_poly, EXPORT.text, 4.0 * k, halo=3.0 * k, halo_alpha=235)
        p.end()
        return layer

    def paint(self, p: QPainter, marker_index: int | None) -> None:
        if not self._ok:
            return
        # blit the baked lap line — a sub-ms copy.
        p.drawImage(0, 0, self._layer)
        if marker_index is None or not (0 <= marker_index < len(self._xs)):
            return
        m = self._proj(float(self._xs[marker_index]), float(self._ys[marker_index]))
        k = self._k
        # --- short comet TAIL: the last few lap points trailing the marker, fading out, so the
        # direction of travel + recent path read at a glance. Drawn only when we have the lap line.
        if self._lap_pts is not None and self._lap_pts.size() > 4:
            # find the lap point nearest the marker (the lap line and the marker share the proj),
            # then draw the preceding ~24 points as a fading bright tail.
            n = self._lap_pts.size()
            # marker_index is a full-trace index; map it to a fraction along the lap line.
            j = min(n - 1, max(0, int(round(marker_index / max(1, len(self._xs) - 1) * (n - 1)))))
            tail_len = max(2, int(round(24 * k)))
            j0 = max(0, j - tail_len)
            tail = QPolygonF([self._lap_pts.at(i) for i in range(j0, j + 1)])
            if tail.size() >= 2:
                # a hot amber comet over the white line shows the recent path + direction of travel;
                # a dark halo under it keeps it readable where the white line is bright too.
                hp = QPen(_c(EXPORT.halo, 180), 4.6 * k)
                hp.setJoinStyle(Qt.RoundJoin)
                hp.setCapStyle(Qt.RoundCap)
                p.setPen(hp)
                p.setBrush(Qt.NoBrush)
                p.drawPolyline(tail)
                tp = QPen(_c(EXPORT.accent_bright, 235), 3.2 * k)
                tp.setJoinStyle(Qt.RoundJoin)
                tp.setCapStyle(Qt.RoundCap)
                p.setPen(tp)
                p.drawPolyline(tail)
        # --- glowing marker: a soft radial glow, a hot-coral core, and a bright outer ring so it
        # is trackable over the green line AND a busy background (the bigger/brighter marker ask).
        glow_r = 11.0 * k
        grad = QRadialGradient(m, glow_r)
        grad.setColorAt(0.0, _c(EXPORT.accent_bright, 220))
        grad.setColorAt(0.5, _c(EXPORT.marker, 150))
        grad.setColorAt(1.0, _c(EXPORT.marker, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(m, glow_r, glow_r)
        # dark halo ring (reads on bright sky), then the hot core, then a white rim.
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_c(EXPORT.halo, 220), 1.6 * k))
        p.drawEllipse(m, 5.2 * k, 5.2 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(_c(EXPORT.marker, 255))
        p.drawEllipse(m, 4.6 * k, 4.6 * k)
        p.setPen(QPen(_c(EXPORT.text, 235), 1.4 * k))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(m, 4.6 * k, 4.6 * k)


def _paint_readout(p: QPainter, box: QRectF, vals: OverlayValues) -> None:
    """Bottom-left delta/speed readout: a hero speed number + small km/h unit and a vivid delta cue
    (export_delta_colour), all haloed, on a slim dark pill."""
    k = box.height() / 44.0   # the readout box is ~44 px tall at 1080p; scale radii/strokes with it
    p.setBrush(_c(EXPORT.halo, 165))
    p.setPen(QPen(_c(EXPORT.text, 55), 1.0 * k))
    p.drawRoundedRect(box, 9 * k, 9 * k)
    pad = box.height() * 0.26
    inner = box.adjusted(pad, 0, -pad, 0)
    # --- HERO speed: big number + small unit ---
    # theme.speed_number is the shared formatter the live #DiffBox uses (real km/h only while a lap
    # is current, else dash) — kept identical, no drift.
    speed_num = theme.speed_number(vals.speed_kmh, vals.lap_id)
    big = _font(box.height() * 0.74, bold=True)
    unit = _font(box.height() * 0.34, bold=True)
    fm_big = QFontMetricsF(big)
    base_y = inner.y() + (inner.height() + fm_big.ascent() - fm_big.descent()) / 2.0
    x = inner.x()
    _draw_text(p, QPointF(x, base_y), speed_num, big, EXPORT.text, halo=2.4 * k)
    x += fm_big.horizontalAdvance(speed_num) + 4 * k
    fm_unit = QFontMetricsF(unit)
    _draw_text(p, QPointF(x, base_y - (fm_big.ascent() - fm_unit.ascent()) * 0.15),
               "km/h", unit, EXPORT.text_dim, halo=1.8 * k)
    x += fm_unit.horizontalAdvance("km/h") + 16 * k
    # --- Δ cue: punchy vivid colour ---
    # theme.format_delta_run(units=False) = the export's tight "Δ +0.00" form (shared with the live
    # box, so no drift); colour from export_delta_colour.
    delta_txt = theme.format_delta_run(vals.delta_s, units=False)
    dcol = export_delta_colour(vals.delta_s)
    dfont = _font(box.height() * 0.50, bold=True)
    fm_d = QFontMetricsF(dfont)
    dy = inner.y() + (inner.height() + fm_d.ascent() - fm_d.descent()) / 2.0
    _draw_text(p, QPointF(x, dy), delta_txt, dfont, dcol, halo=2.2 * k)


def _paint_strip(p: QPainter, box: QRectF, session, vals: OverlayValues, t0: float) -> None:
    """Lap/sector strip (top-left): "LAP n  m:ss.mmm" with a vivid amber time-progress fill, on the
    same slim dark pill as the readout."""
    k = box.height() / 44.0
    p.setBrush(_c(EXPORT.halo, 165))
    p.setPen(QPen(_c(EXPORT.text, 55), 1.0 * k))
    p.drawRoundedRect(box, 8 * k, 8 * k)
    if vals.lap_id is None:
        return
    win = session.lap_window(vals.lap_id)
    if win is not None:
        ls, le = win
        frac = 0.0 if le <= ls else max(0.0, min(1.0, (vals.t - ls) / (le - ls)))
        if frac > 0:
            # progress fill clipped to the pill so the rounded corners stay clean.
            clip = QPainterPath()
            clip.addRoundedRect(box, 8 * k, 8 * k)
            p.save()
            p.setClipPath(clip)
            fill = QRectF(box.x(), box.y(), box.width() * frac, box.height())
            p.setBrush(_c(EXPORT.accent, 120))
            p.setPen(Qt.NoPen)
            p.drawRect(fill)
            p.restore()
        elapsed = max(0.0, vals.t - ls)
    else:
        elapsed = max(0.0, vals.t - t0)
    label = f"LAP {vals.lap_id}   {fmt_time(elapsed)}"
    inner = box.adjusted(box.height() * 0.42, 0, -box.height() * 0.2, 0)
    _text_at(p, inner, Qt.AlignVCenter | Qt.AlignLeft, label,
             _font(box.height() * 0.54, bold=True), EXPORT.text, halo=2.2 * k)


class OverlayPainter:
    """Composites the overlay elements onto each decoded frame. Built ONCE per export (it caches
    the static map-inset geometry + a headless g-meter dial that it drives frame-to-frame with the
    SAME set_lap/set_g sequence the live tick uses, so the burned dial's EMA/envelope evolve
    identically). `paint_frame_with_state` mutates the passed QImage in place."""

    def __init__(self, session, spec: ExportSpec, out_w: int, out_h: int):
        self._session = session
        self._spec = spec
        self._w, self._h = out_w, out_h
        cfg = spec.config
        # Global size scale for the export overlays: 1.0 at 1080p, growing/shrinking with the output
        # height so line widths, the g-dot, the map marker + glyph outlines all look right at 720p
        # through 4K (the brief's "sizes that scale with out_height"). The g-dial + map-inset paint
        # take this `k`; the readout/strip self-scale from their box height.
        self._k = out_h / 1080.0
        m = cfg.margin_frac * out_h
        # g-meter: square in the TOP-RIGHT.
        gside = cfg.gmeter_frac * out_h
        self._g_rect = QRectF(out_w - m - gside, m, gside, gside)
        # map inset: BOTTOM-RIGHT.
        mw, mh = cfg.map_w_frac * out_w, cfg.map_h_frac * out_h
        self._map = _MapInset(session, QRectF(out_w - m - mw, out_h - m - mh, mw, mh),
                              spec.lap_id, scale_k=self._k)
        # readout: BOTTOM-LEFT.
        rh = max(cfg.readout_h_frac * out_h, 22.0)
        self._readout_rect = QRectF(m, out_h - m - rh, max(out_w * 0.30, 260.0), rh)
        # lap strip: TOP-LEFT.
        sh = max(cfg.strip_h_frac * out_h, 20.0)
        self._strip_rect = QRectF(m, m, max(out_w * 0.26, 220.0), sh)
        # Headless g-meter dial, driven exactly like the live overlay so its filtering matches.
        self._dial = gmeter_overlay.GMeterOverlay()
        self._dial.set_source(session.gmeter_source() if hasattr(session, "gmeter_source") else "accl")

    def feed_g(self, vals: OverlayValues) -> None:
        """Advance the headless g-meter dial by one tick with this frame's lap + g — the same
        order app._apply_readout feeds it (set_gmeter_lap then set_g), so the envelope resets on
        the lap boundary and the EMA dot tracks identically to the live meter."""
        if vals.lap_id is not None:
            self._dial.set_lap(vals.lap_id)
        self._dial.set_g(vals.g)

    def advance_and_snapshot(self, vals: OverlayValues):
        """Advance the dial one tick (order-dependent: EMA/envelope accumulate) and return an
        immutable `DialState` snapshot."""
        self.feed_g(vals)
        return self._dial._dial_state()

    def paint_frame_with_state(self, img: QImage, vals: OverlayValues, dial_state) -> None:
        """Paint all overlay elements onto `img` (an RGB frame at the output size) from a precomputed
        `dial_state`. `img` is mutated in place."""
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        # g-meter dial: paint into its rect via the SHARED paint routine + the snapshot of the
        # headless dial's filtering state (identical to the on-screen widget).
        p.save()
        p.translate(self._g_rect.topLeft())
        # export=True -> the vivid, no-box, big-number dial; scale_k from the dial's own size (1.0
        # at the ~280 px 1080p dial) so its strokes/glyphs track the output resolution.
        gmeter_overlay.paint_dial(p, self._g_rect.width(), self._g_rect.height(), dial_state,
                                  export=True, scale_k=self._g_rect.width() / 280.0)
        p.restore()
        self._map.paint(p, vals.marker_index)
        _paint_readout(p, self._readout_rect, vals)
        _paint_strip(p, self._strip_rect, self._session, vals, self._spec.t0)
        p.end()


def _paint_packed_frame(painter: OverlayPainter, out_w: int, out_h: int, raw: bytes,
                        vals: OverlayValues, dial) -> bytes:
    """Composite one rgb24 frame and return bytes tightly PACKED at out_w*3. QImage scanlines are
    4-byte-aligned, so for a non-4-aligned out_w*3 we strip each row's trailing padding (otherwise
    every row shears + the stream desyncs)."""
    buf = bytearray(raw)
    img = QImage(buf, out_w, out_h, 3 * out_w, QImage.Format_RGB888)
    painter.paint_frame_with_state(img, vals, dial)
    bpl = img.bytesPerLine()
    if bpl == 3 * out_w:
        return bytes(buf)                            # already packed — no padding to strip
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * out_h).reshape(out_h, bpl)
    return arr[:, : 3 * out_w].tobytes()


# --------------------------------------------------------------------------- the renderer
class CancelledError(Exception):
    """Raised inside the render loop when the caller's cancel callback returns True."""


class RenderTimeoutError(RuntimeError):
    """No frame progress for `watchdog_timeout` s — a wedged stage that never "fails"; `run` retries
    once on libx264 then surfaces a clear error."""


class NoFramesError(RuntimeError):
    """The decode produced zero frames (a past-EOF/empty seek) — a silent 0-frame "success"
    otherwise. The up-front `guard_validate_window` normally catches this; this is the backstop for a
    source whose duration ffprobe couldn't read."""


class _EncodeError(RuntimeError):
    """Internal: a non-zero ENCODE exit, carrying the encoder name + stderr tail. `run` catches it
    to decide whether to fall back from h264_videotoolbox to libx264; if it escapes (no fallback) it
    is surfaced as a plain RuntimeError, so callers still see a clear 'ffmpeg encode failed' error."""

    def __init__(self, encoder: str, message: str):
        super().__init__(message)
        self.encoder = encoder


@dataclass
class RenderResult:
    out_path: str
    frames: int
    out_w: int
    out_h: int
    fps: float
    duration: float


class _StderrDrainer:
    """Drain an ffmpeg stderr on a daemon thread, keeping a bounded tail. Without this, a full
    (~64 KB) stderr pipe blocks ffmpeg while the loop is busy on the stdout/stdin pipes -> deadlock.
    tail() explains a non-zero exit."""

    def __init__(self, stream, tail_bytes: int = 8192):
        self._stream = stream
        self._tail_bytes = tail_bytes
        self._buf = bytearray()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._pump, daemon=True)
        self._thread.start()

    def _pump(self) -> None:
        try:
            for chunk in iter(lambda: self._stream.read(4096), b""):
                with self._lock:
                    self._buf.extend(chunk)
                    if len(self._buf) > self._tail_bytes:
                        del self._buf[: len(self._buf) - self._tail_bytes]
        except (OSError, ValueError):
            pass  # pipe closed underneath us during teardown — fine

    def tail(self) -> bytes:
        with self._lock:
            return bytes(self._buf)

    def join(self, timeout: float = 5.0) -> None:
        self._thread.join(timeout)


class Renderer:
    """Drives the decode → composite → mux pipeline frame by frame. The caller pumps `run_chunk`
    (e.g. from a QThread, or a chunked QTimer on the GUI thread) so the work can be cancelled and a
    progress bar updated; `run` is a convenience that pumps to completion (used by the tests + a
    headless render). All ffmpeg I/O is via subprocess PIPEs — no temp video files."""

    def __init__(self, session, spec: ExportSpec):
        self._session = session
        self._spec = spec
        # Probe the RESOLVED source file (a single chapter, or the first chapter of a concat span)
        # — never a bare global src_path. Then REFUSE an obviously-doomed window up front: if the
        # source-local seek time lands at/after the probed source duration, ffmpeg would decode zero
        # frames and the export would sit on an empty bar (the chaptered-export bug). Failing fast
        # with a clear message beats launching a render that can only produce nothing.
        src_w, src_h, src_fps = probe_video_size(spec.source.probe_path)
        guard_validate_window(spec)
        self._out_w, self._out_h = output_size(src_w, src_h, spec.config)
        self._fps = resolve_fps(spec.config, src_fps)
        self._times = frame_times(spec.t0, spec.t1, self._fps)
        self._painter = OverlayPainter(session, spec, self._out_w, self._out_h)
        # Resolve the encoder ONCE (probes VideoToolbox). `_encoder` is the concrete ffmpeg -c:v
        # name actually used; `_fallback_allowed` lets a failed VT encode retry on libx264.
        self._encoder = resolve_encoder(spec.config.encoder)
        self._fallback_allowed = self._encoder == VT_H264
        self._hwaccel = resolve_hwaccel_decode(spec.config.hwaccel_decode, self._encoder)
        self._dec: subprocess.Popen | None = None
        self._enc: subprocess.Popen | None = None
        self._dec_err: _StderrDrainer | None = None
        self._enc_err: _StderrDrainer | None = None
        self._i = 0
        self._frame_bytes = self._out_w * self._out_h * 3
        self._started = False
        self._done = False
        # --- watchdog / abort plumbing (a supervisor thread can break a wedged blocking I/O) ---
        self._watchdog_timeout = float(getattr(spec.config, "watchdog_timeout", 30.0) or 0.0)
        self._last_progress_t = 0.0            # monotonic time of the last frame written
        self._aborted: str | None = None       # set by the supervisor: "cancel" | "timeout"
        self._supervisor: threading.Thread | None = None
        self._supervisor_stop = threading.Event()

    @property
    def total_frames(self) -> int:
        return len(self._times)

    @property
    def frames_done(self) -> int:
        return self._i

    @property
    def out_size(self) -> tuple[int, int]:
        return self._out_w, self._out_h

    @property
    def fps(self) -> float:
        return self._fps

    def _start(self) -> None:
        self._dec = subprocess.Popen(
            build_decode_cmd(self._spec, self._out_w, self._out_h, self._fps, self._hwaccel),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._enc = subprocess.Popen(
            build_encode_cmd(self._spec, self._out_w, self._out_h, self._fps, self._encoder),
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        # Drain BOTH ffmpeg stderrs off-thread so neither can ever block on a full stderr pipe while
        # the loop is busy on the decode-stdout / encode-stdin pipes (deadlock guard).
        # (getattr-guarded so a mock Popen without a stderr attribute is simply not drained.)
        dec_se = getattr(self._dec, "stderr", None)
        enc_se = getattr(self._enc, "stderr", None)
        self._dec_err = _StderrDrainer(dec_se) if dec_se is not None else None
        self._enc_err = _StderrDrainer(enc_se) if enc_se is not None else None
        self._started = True

    @property
    def encoder(self) -> str:
        """The concrete ffmpeg video encoder this render resolved to (h264_videotoolbox or
        libx264). Useful for tests / a status line that wants to report the GPU offload."""
        return self._encoder

    def run_chunk(self, n: int = 24) -> bool:
        """Single-threaded pump: composite up to `n` frames in order (read frame -> paint -> write to
        encoder). Returns True when complete. THE render engine; `run()` pumps it under the watchdog.
        A short read = clean end (or NoFramesError); a supervisor kill turns the blocked
        read/write into the right typed exception (RenderTimeoutError / CancelledError /
        _EncodeError) so the render never hangs."""
        if self._done:
            return True
        if not self._started:
            self._start()
        assert self._dec is not None and self._enc is not None
        stdout = self._dec.stdout
        stdin = self._enc.stdin
        assert stdout is not None and stdin is not None
        for _ in range(n):
            if self._i >= len(self._times):
                self._finish()
                return True
            raw = stdout.read(self._frame_bytes)
            if not raw or len(raw) < self._frame_bytes:
                # A short read means ONE of three things:
                #   * the supervisor killed the decoder on a stall/cancel -> abort loudly;
                #   * the decoder reached the ceil-estimate tail AFTER emitting frames -> finish
                #     cleanly (the normal end of a render);
                #   * the decoder emitted ZERO frames (an empty/past-EOF seek) -> that's not a
                #     success, it's the chaptered-export failure: surface NoFramesError so the user
                #     gets a clear message instead of an empty clip + a dialog that never moved.
                self._raise_if_aborted()
                produced = self._i
                self._finish()
                if produced == 0:
                    raise NoFramesError(
                        "the video export produced no frames — the source/lap window may be "
                        "invalid (it does not map onto any footage). Nothing was written.")
                return True
            vals = overlay_values_at(self._session, float(self._times[self._i]))
            dial = self._painter.advance_and_snapshot(vals)
            try:
                stdin.write(self._paint_packed(raw, vals, dial))
            except (BrokenPipeError, OSError):
                # The encoder stopped accepting input. Distinguish a watchdog/cancel kill (the
                # supervisor broke the pipe to escape a wedge) from a genuine encoder failure.
                self._raise_if_aborted()
                self._finish()              # reap; its non-zero-exit branch raises _EncodeError
                raise _EncodeError(self._encoder, "ffmpeg encode pipe broke") from None
            self._i += 1
            self._last_progress_t = time.monotonic()   # fed the watchdog: a frame made it out
        return False

    def _raise_if_aborted(self) -> None:
        """If the supervisor aborted the render (stall watchdog or cancel), raise the matching
        exception so a killed-pipe read/write becomes a clear, typed failure instead of a silent
        early finish or a bare BrokenPipeError."""
        if self._aborted == "timeout":
            raise RenderTimeoutError(
                f"video export stalled: no frame written for {self._watchdog_timeout:.0f}s "
                f"(encoder={self._encoder}) — the render was aborted to avoid hanging")
        if self._aborted == "cancel":
            raise CancelledError("export cancelled")

    def _paint_packed(self, raw: bytes, vals: OverlayValues, dial) -> bytes:
        """Paint the overlays for one decoded rgb24 frame (`raw`, PACKED at out_w*3) from a
        precomputed dial snapshot, and return the painted bytes PACKED at out_w*3 for the encoder."""
        return _paint_packed_frame(self._painter, self._out_w, self._out_h, raw, vals, dial)

    def _start_supervisor(self, cancel) -> None:
        """Daemon supervisor: polls every 0.5s; aborts "cancel" if `cancel()` returns True, or
        "timeout" if no frame for `watchdog_timeout` s. Kills ffmpeg so the blocked pipe I/O returns;
        `_raise_if_aborted` then raises the typed error. A zero/none timeout disables only the stall
        check (cancel still works)."""
        if self._supervisor is not None:
            return
        self._last_progress_t = time.monotonic()
        self._supervisor_stop.clear()

        def supervise() -> None:
            while not self._supervisor_stop.wait(0.5):
                if self._done:
                    return
                if cancel is not None:
                    try:
                        if cancel():
                            self._abort("cancel")
                            return
                    except Exception:  # noqa: BLE001 - a bad cancel cb must not crash the guard
                        pass
                # Armed from render start (not first frame) so a setup/zero-frame wedge also trips it.
                if self._watchdog_timeout > 0 and not self._done:
                    if time.monotonic() - self._last_progress_t > self._watchdog_timeout:
                        self._abort("timeout")
                        return

        self._supervisor = threading.Thread(target=supervise, daemon=True,
                                            name="f9-export-supervisor")
        self._supervisor.start()

    def _abort(self, reason: str) -> None:
        """Record why the render is being aborted and KILL the ffmpeg processes so any blocked pipe
        read/write in the render loop returns at once. Called only from the supervisor."""
        if self._aborted is None:
            self._aborted = reason
        for proc in (self._enc, self._dec):
            if proc is None:
                continue
            try:
                proc.kill()
            except OSError:
                pass

    def _stop_supervisor(self) -> None:
        self._supervisor_stop.set()
        sup = self._supervisor
        if sup is not None and sup is not threading.current_thread():
            sup.join(timeout=2.0)
        self._supervisor = None

    def run(self, progress=None, cancel=None, chunk: int = 48) -> RenderResult:
        """Render to completion. `progress(done, total)` / `cancel()` callbacks. A VT encode that
        fails (`_EncodeError`) or wedges (RenderTimeoutError) retries ONCE on a fresh libx264-forced
        Renderer; otherwise surfaces a clear RuntimeError. Returns a RenderResult."""
        try:
            return self._run_chunked(progress, cancel, chunk)
        except (_EncodeError, RenderTimeoutError) as exc:
            is_encode_fail = isinstance(exc, _EncodeError) and exc.encoder == VT_H264
            is_vt_wedge = isinstance(exc, RenderTimeoutError) and self._encoder == VT_H264
            if not (self._fallback_allowed and (is_encode_fail or is_vt_wedge)):
                # Not a VT-recoverable case → surface a clear error (never a hang).
                raise RuntimeError(str(exc)) from exc
            # VideoToolbox failed OR wedged → retry once on libx264 with an identical spec.
            self.cancel()
            sw_cfg = replace(self._spec.config, encoder="libx264")
            sw_spec = replace(self._spec, config=sw_cfg)
            return Renderer(self._session, sw_spec)._run_chunked(progress, cancel, chunk)

    def _run_chunked(self, progress, cancel, chunk: int) -> RenderResult:
        """Pump `run_chunk` to completion under the supervisor (watchdog + cancel). Single-threaded:
        decode → paint → encode in series, one chunk at a time, with progress reported after each."""
        self._start_supervisor(cancel)
        try:
            while not self.run_chunk(chunk):
                # Cooperative cancel between chunks too (fast path for a non-blocked loop / mocks);
                # a cancel that lands mid-write is handled by the supervisor killing the pipe.
                if cancel is not None and cancel():
                    self.cancel()
                    raise CancelledError("export cancelled")
                if progress is not None:
                    progress(self._i, len(self._times))
            if progress is not None:
                progress(self._i, len(self._times))
        except (CancelledError, _EncodeError, RenderTimeoutError):
            self.cancel()
            raise
        except Exception:
            self.cancel()
            raise
        finally:
            self._stop_supervisor()
        return RenderResult(self._spec.out_path, self._i, self._out_w, self._out_h,
                            self._fps, self._spec.duration)

    def _finish(self) -> None:
        """Finalize: flush + close the encoder's stdin (signals EOF so it writes the trailer), then
        reap both processes, surfacing a non-zero encode exit with its stderr tail. The decoder's
        stdout is closed first so a decoder still emitting frames (we stopped early at the
        ceil-estimate tail) gets a SIGPIPE/EOF and exits instead of blocking. stderr is drained by
        the background drainers (started in `_start`), so we just `wait()` here — NOT communicate(),
        which would fight the drainer for the stderr pipe. Idempotent."""
        if self._done:
            return
        self._done = True
        enc, dec = self._enc, self._dec
        # Stop reading the decoder so it unblocks and exits (it may still be mid-stream at our tail).
        if dec is not None and dec.stdout is not None:
            try:
                dec.stdout.close()
            except OSError:
                pass
        # Close the encoder's stdin → EOF → it finishes muxing and exits. (Flush first so the last
        # frame isn't stranded in Python's buffer.)
        if enc is not None and enc.stdin is not None:
            try:
                enc.stdin.flush()
            except OSError:
                pass
            try:
                enc.stdin.close()
            except OSError:
                pass
        if enc is not None:
            try:
                enc.wait(timeout=30)
            except Exception:
                enc.kill()
                enc.wait()
        if dec is not None:
            try:
                dec.wait(timeout=10)
            except Exception:
                dec.kill()
        # Let the stderr drainers finish so their tails are complete before we read them.
        if self._dec_err is not None:
            self._dec_err.join()
        if self._enc_err is not None:
            self._enc_err.join()
        if enc is not None and enc.returncode not in (0, None):
            enc_err = self._enc_err.tail() if self._enc_err is not None else b""
            raise _EncodeError(self._encoder,
                               f"ffmpeg encode failed ({self._encoder}, rc={enc.returncode}): "
                               f"{enc_err.decode('utf-8', 'replace')[-800:]}")

    def cancel(self) -> None:
        """Kill both ffmpeg processes and mark the render done (best-effort teardown for the
        cancel path / an error). Safe to call more than once. Also stops the supervisor thread.
        The stderr drainers are daemon threads draining pipes that close when the processes die,
        so they wind down on their own."""
        self._done = True
        self._stop_supervisor()
        for proc in (self._enc, self._dec):
            if proc is None:
                continue
            try:
                proc.kill()
            except OSError:
                pass
            try:
                proc.wait(timeout=5)
            except Exception:
                pass
        for drainer in (self._enc_err, self._dec_err):
            if drainer is not None:
                drainer.join(timeout=1.0)


def build_lap_spec(session, out_path: str, lap_id: int,
                   config: OverlayConfig | None = None,
                   src_path: str | None = None) -> ExportSpec:
    """Build the `ExportSpec` for `lap_id`, resolving the VIDEO SOURCE from the session's chapters.
    Raises ValueError if the lap has no usable window. Source:
      * `session.chapters` (a ChapterMap) present -> resolve_video_source (single chapter or a concat
        over a seam-crossing span);
      * no ChapterMap -> the plain single file (`src_path`, else `session.video_path`), offset 0.
    The caller OWNS the returned spec's `source` and must call `spec.source.cleanup()` when done."""
    win = lap_window_for_export(session, lap_id)
    if win is None:
        raise ValueError(f"lap {lap_id} has no usable export window")
    t0, t1 = win
    chapter_map = getattr(session, "chapters", None)
    if chapter_map is not None and getattr(chapter_map, "chapters", None):
        source = resolve_video_source(chapter_map, t0, t1)
    else:
        path = src_path or getattr(session, "video_path", None)
        if not path:
            raise ValueError("session has no video source to export")
        source = single_file_source(path)
    return ExportSpec(out_path=out_path, lap_id=lap_id, t0=t0, t1=t1,
                      source=source, config=config or OverlayConfig())


def render_lap(session, src_path: str, out_path: str, lap_id: int,
               config: OverlayConfig | None = None,
               progress=None, cancel=None) -> RenderResult:
    """Build the lap spec and render to completion (headless/tests). Raises ValueError if the lap
    window is unusable; `src_path` is the fallback single-file source when the session has no
    ChapterMap."""
    spec = build_lap_spec(session, out_path, lap_id, config=config, src_path=src_path)
    try:
        return Renderer(session, spec).run(progress=progress, cancel=cancel)
    finally:
        spec.source.cleanup()
