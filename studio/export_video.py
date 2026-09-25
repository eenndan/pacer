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

HONESTY: this is the most public artifact the app produces and the one least able to explain
itself, so a recording whose timing is provisional / estimated / GPS-degraded burns the app's own
WORDS for that under the lap strip (`_paint_stamp`). It carries no `[e]`/`[p]`/`[u]`/`[b]` code —
a frame has no key to decode one — and it does NOT block the export the way the share card does;
`data_quality`'s burned-frame block owns both decisions and their reasons.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import queue
import shutil
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, replace
from fractions import Fraction
from typing import NamedTuple

import numpy as np
from PySide6.QtCore import QPointF, QRect, QRectF, Qt
from PySide6.QtGui import (
    QFont,
    QFontMetricsF,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)

from . import data_quality, gmeter_overlay, theme, units
from ._signal import fmt_time, lap_label
from .export_palette import EXPORT
from .gapfill import GAP_TIME_S
from .timeline import trace_point_at

_log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- ffmpeg discovery
# Resolved at import so importing this module never *runs* ffmpeg (the unit tests mock the
# subprocess; only a real render needs the binaries). The pixi env puts them on PATH for the app.
#
# Resolution order (so a bundled, PATH-less macOS .app still finds them):
#   1. PACER_FFMPEG / PACER_FFPROBE env vars      — set by the PyInstaller runtime hook
#      (packaging/pacer.spec) to the binaries bundled inside the .app, and overridable by hand.
#   2. a binary sitting next to the frozen executable or under sys._MEIPASS (PyInstaller onedir/
#      onefile) — the fallback if the runtime hook didn't run for some reason.
#   3. the bare name "ffmpeg"/"ffprobe" resolved on PATH — the dev/pixi path (unchanged).
# A frozen .app has NO PATH ffmpeg, hence steps 1-2; in a normal checkout neither bundle marker is
# set so this is exactly the old PATH lookup.
def _resolve_binary(name: str, env_var: str) -> str:
    override = os.environ.get(env_var)
    if override:
        return override
    # PyInstaller sets sys.frozen + sys._MEIPASS (the unpacked bundle dir). Look for a binary
    # bundled alongside the app's resources before falling back to PATH.
    if getattr(sys, "frozen", False):
        roots = [getattr(sys, "_MEIPASS", None), os.path.dirname(sys.executable)]
        for root in roots:
            if not root:
                continue
            cand = os.path.join(root, name)
            if os.path.isfile(cand) and os.access(cand, os.X_OK):
                return cand
    return name


FFMPEG = _resolve_binary("ffmpeg", "PACER_FFMPEG")
FFPROBE = _resolve_binary("ffprobe", "PACER_FFPROBE")


def ffmpeg_available() -> bool:
    """True iff both ffmpeg and ffprobe are runnable. `shutil.which` resolves a bare name on PATH
    AND validates an absolute path (the bundled/overridden case) is an executable file."""
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


# --------------------------------------------------------------------------- output shape + scope
# THE FOUR VOCABULARIES THE PICKER SPEAKS, declared here rather than in the dialog, because every
# one of them changes what the RENDERER does and the dialog is only where they are chosen. The
# controller builds its combo rows from these tuples, so a row that exists is a value this module
# handles, by construction.

# The output's SHAPE. "source" keeps the footage's own aspect (height-controlled) and is what
# every export before this produced; the other two are the two shapes social video actually asks
# for. There is no 4:5 or 4:3 here yet — `frame_geometry` takes a ratio, so adding one is a row.
ASPECT_SOURCE = "source"
ASPECT_9_16 = "9:16"
ASPECT_1_1 = "1:1"
# name -> width/height. "source" has no fixed ratio (it reads the footage), hence the None.
ASPECT_RATIOS = {ASPECT_SOURCE: None, ASPECT_9_16: 9 / 16, ASPECT_1_1: 1.0}

# How the source frame meets a shape it does not have. CROP fills the output and throws away the
# sides; FIT keeps the whole picture and pads. Both are wanted: a cropped 9:16 is what a phone
# feed wants, a fitted one is what you use when the thing that matters is at the edge of frame.
FIT_CROP = "crop"
FIT_FIT = "fit"

# Overlay-only (alpha) output formats.
ALPHA_PRORES = "prores"    # ProRes 4444 in a .mov, yuva444p10le — the NLE-native choice
ALPHA_PNG = "png"          # a DIRECTORY of RGBA PNG frames — the universal one

# WHAT gets rendered. The first three are all per-lap renders and share every code path (BEST is
# THIS_LAP with the lap resolved from `session.best_lap_id()`, ALL is one of those per valid lap);
# only SESSION renders a window that is not a lap, and it is the one scope whose overlay has to
# FOLLOW the laps rather than name one (`ExportSpec.follow_laps`).
SCOPE_THIS_LAP = "lap"
SCOPE_BEST_LAP = "best"
SCOPE_ALL_LAPS = "all"
SCOPE_SESSION = "session"


# --------------------------------------------------------------------------- configuration
# 1080p default; the frame's shape comes from `aspect` + `frame_fit` (see `frame_geometry`).
#
# EVERY OVERLAY DIMENSION IS A FRACTION OF THE OUTPUT'S SHORT SIDE, and that denominator is the
# whole reason this export can be 9:16 at all. It used to be the output HEIGHT — a choice that is
# invisible while every export is 16:9 landscape (there, short side == height, so nothing below
# changes by a pixel) and falls over the moment the frame is taller than it is wide: on a
# 1080x1920 vertical clip a height-fractioned g-meter is 0.26*1920 = 499 px across a frame only
# 1080 px wide, i.e. 46 % of the picture, and the map inset's `map_w_frac * out_w` by
# `map_h_frac * out_h` box comes out 238x422 — a portrait slot for a roughly-square kart circuit.
#
# The short side is also what makes a FUTURE source shape work without a second decision: 4:3 and
# 5.3K land on the same rule, and a 1:1 export is the case where width and height agree.
OVERLAY_REF_SHORT_SIDE = 1080.0   # the reference: scale k == 1.0 on any export whose short side is 1080


def overlay_unit(out_w: int, out_h: int) -> float:
    """The overlay's LENGTH UNIT for an out_w x out_h frame: the short side. Every overlay size
    below is a fraction of this, and every stroke width scales with `it / OVERLAY_REF_SHORT_SIDE`.

    Short side rather than height, area or diagonal because the overlay is a set of corner
    elements whose legibility budget is how much of the NARROW axis they eat: a dial sized off the
    long side of a 9:16 frame swallows the picture, and one sized off the long side of an
    ultra-wide frame vanishes. For every 16:9-or-wider landscape export — which is every export
    this app has ever produced — this returns the height, unchanged."""
    return float(min(max(int(out_w), 1), max(int(out_h), 1)))


@dataclass(frozen=True)
class OverlayConfig:
    """Layout + output knobs for the export. All overlay placements are FRACTIONS of the output's
    SHORT SIDE (`overlay_unit`) so the composition holds at any frame shape. The defaults
    reproduce the app's corner placements (g-meter top-right, readout bottom-left, map inset
    bottom-right, lap strip top-left)."""
    out_height: int = 1080            # target SHORT side of the output (never upscaled past source)
    quality: str = "high"             # "high"/"standard"; resolved to encoder numbers by quality_params
    fps: float | None = None          # explicit output fps; None = source fps, then fps_cap applies
    # Cap the output fps: a telemetry overlay reads identically at 30 as at 59.94 but 30 ~halves
    # every per-frame cost. None keeps the source rate; an explicit `fps` overrides the cap.
    fps_cap: float | None = 30.0
    encoder: str = "auto"             # "auto"/"libx264"/"videotoolbox" (see resolve_encoder)
    hwaccel_decode: str | bool = "auto"  # "auto" pairs the hw decode with the hw encoder (see resolve_hwaccel_decode)
    # Output SHAPE: "source" (the source's own aspect, height-controlled — the historic behaviour),
    # "9:16" (vertical) or "1:1" (square). See `frame_geometry` / ASPECT_CHOICES.
    aspect: str = ASPECT_SOURCE
    # How the SOURCE FRAME meets a non-source aspect: "crop" fills the frame and takes the centre
    # out of the footage; "fit" keeps the whole picture and pads the rest. Ignored for "source".
    frame_fit: str = FIT_CROP
    # OVERLAY-ONLY: composite onto a TRANSPARENT canvas and skip the source decode entirely, for
    # finishing in Resolve/Premiere. `alpha_codec` picks ProRes 4444 (a .mov) or a PNG sequence
    # (a directory of frames). Ignored unless `overlay_only`.
    overlay_only: bool = False
    alpha_codec: str = ALPHA_PRORES   # "prores" | "png"
    # 1 = the single-threaded pump (read -> paint -> write in series; the fallback, and what the
    # mocked-pipeline tests pin). Anything else = the PIPELINED pump: the decode read and the
    # encode write each get a thread of their own around the one painting thread (see
    # `_FramePool`). The painting itself stays on ONE thread whatever this says, because PySide6
    # holds the GIL through every QPainter call: painting MK lap 14 on 1/2/4/6 threads measured
    # 127.6 / 136.2 / 128.0 / 123.5 fps at 1080p.
    workers: int | None = None
    # No-progress WATCHDOG: if the frame counter doesn't advance the render is presumed WEDGED
    # (hung VT session / stuck pipe), aborted cleanly, then retried ONCE on libx264 — what makes an
    # infinite hang impossible. THE LIMIT IS NOT A CONSTANT, because a constant is wrong at both
    # ends: big enough never to kill a legitimately slow render, it is far too slack to catch a
    # wedge in a fast one.
    #
    # MEASURED, 11 real exports of both D24 recordings (1080p and 2160p, h264_videotoolbox and
    # libx264, interior laps and laps crossing a chapter seam), watchdog disabled so the true
    # distribution was not censored:
    #
    #   worst gap between two consecutive frames   0.783 s  (0062 lap 41, 1080p libx264)
    #   ...every other run                        <=0.246 s
    #   worst gap from render start to frame 1     0.674 s  (ffmpeg spawn + the seek)
    #   median per frame           0.018 s (1080p VT) .. 0.064 s (2160p libx264)
    #
    # So four times the pixels moves the MEDIAN by ~2.4x and the TAIL barely at all: the tail is a
    # scheduling transient, not a function of the frame. That is why the floor below is a floor and
    # not a formula — 10 s is 12.8x the worst stall and 14.8x the worst startup gap ever measured
    # here, and it catches a wedge three times sooner than the flat 30 s it replaces.
    #
    # `watchdog_frame_multiple` is what makes it SCALE: the effective limit is also that multiple of
    # the render's OWN mean cost per frame so far, so a render on slower footage, a bigger frame or
    # a loaded machine raises its own limit and can never be killed for being slow. At every
    # configuration measured above the floor governs (2160p libx264: 60 x 0.064 = 3.8 s < 10 s);
    # the multiple only takes over past ~0.167 s per frame, which is 2.6x the slowest ever seen.
    watchdog_timeout: float = 10.0
    watchdog_frame_multiple: float = 60.0
    # g-meter dial: a square pinned to the TOP-RIGHT, side = this fraction of the SHORT SIDE.
    gmeter_frac: float = 0.26
    margin_frac: float = 0.022        # uniform inset from the frame edge for all elements
    # track-map inset: bottom-right box, `map_h_frac` of the SHORT SIDE tall and as wide as the
    # track needs (`_inset_width`), capped at `map_max_aspect` times its own height.
    #
    # THE CAP USED TO BE `map_w_frac * out_w` AND WAS ALREADY THIS NUMBER IN DISGUISE. With
    # map_w_frac == map_h_frac, `map_w_frac * out_w` is exactly the box height times the FRAME's
    # aspect — 16:9 by construction on a 16:9 export, which the old comment said out loud. Naming
    # it as the box's own aspect cap reproduces the shipped 1080p cap to the pixel (0.22 * 1080 *
    # 16/9 == 0.22 * 1920) and stops a vertical frame from handing a square circuit a slot 238 px
    # wide and 422 tall.
    map_h_frac: float = 0.22
    map_max_aspect: float = 16 / 9
    # readout box (Δ / speed): bottom-left; sized to its text, this is the font height fraction.
    readout_h_frac: float = 0.040
    # lap/sector strip: a slim bar across the TOP-LEFT.
    strip_h_frac: float = 0.040
    # Speed display unit for the burned-in readout ("kmh"/"mph"); km/h default. The app passes the
    # persisted choice so the export matches what's on screen. Speed VALUES stay km/h — converted
    # only at the paint boundary (units.convert_speed).
    speed_unit: str = units.DEFAULT_UNIT
    # Active semantic palette (theme.PALETTE_STANDARD / PALETTE_COLORBLIND) baked into the burned-in
    # Δ cue's hue axis. The app passes the user's live colour-blind choice so the EXPORTED clip — the
    # artifact they share — follows it (the render runs on a worker QThread that must not read the
    # global theme.active_palette() live; export_semantic_pair resolves the vivid EXPORT pair from it).
    palette: str = theme.PALETTE_STANDARD


# --------------------------------------------------------------------------- chaptered source
# A chaptered GoPro recording lays its files on one global media clock (chapter i covers global
# [offset_i, offset_i+dur_i); studio/chapters.ChapterMap). ffmpeg can only `-ss` into a SINGLE
# file's own (local) clock, so a global window resolves to either one chapter file (offset =
# chapter.offset) or a CONCAT over a seam-crossing span. The concat demuxer plays the spanned files
# back-to-back as ONE stream whose clock starts at the FIRST spanned chapter, so both branches take
# the same shift: local = global - offset_of_the_first_file.
#
# EVERY LIST ENTRY CARRIES ITS `duration`, AND THAT IS LOAD-BEARING — IT IS WHAT MAKES THE CONCAT
# STREAM SEEKABLE AT ALL. Without a declared duration for every file the concat demuxer refuses to
# seek (`concat_seek` bails; ffmpeg logs "could not seek to position ..."), and an `-ss` before the
# input degrades into decoding the whole span from its start. The picture is still RIGHT — ffmpeg's
# accurate seek discards its way there — but on the real 3x1729 s D24 recording half a second of
# picture out of lap 22's window cost 663 s undeclared against 1.2 s declared. Correct and
# unshippable is still unshippable.
#
# AND THEY ARE THE RIGHT DURATIONS TO DECLARE. They are the SAME numbers ChapterMap accumulates its
# offsets from, so the concat stream's clock is the app's global clock by construction. Letting
# ffmpeg re-derive them per file would be the inconsistent choice: the single-chapter branch seeks
# `t0 - offset_i` with offsets built from these, so a span that placed its seam anywhere else would
# disagree with the chapter branch about where chapter i+1 begins. On D24 the two agree to
# 0.027 / 0.027 / 1.055 ms of the video streams — well inside one 16.7 ms frame — and the span hands
# over to the next chapter at +0.0000 s of the declared seam, measured frame by frame across it.
#
# WHAT THIS REPLACED, AND WHY IT HAD TO GO. The first spanned chapter used to carry a concat
# `inpoint` at the window's local start, with `time_offset = t0` so both ffmpeg commands seeked with
# `-ss 0`. `inpoint` is KEYFRAME-GRANULAR: the decoded picture began at the keyframe at or before
# the requested instant, i.e. up to one GOP early, while `frame_times` stamped every overlay frame
# from t0 exactly. The burned-in numbers therefore described a moment up to a second later than the
# picture under them, on every seam-crossing export (D24 laps 22 and 47), at any padding. Measured
# by exact-pixel comparison against the chapter's own frames (mean |delta| = 0.000 against a median
# of 34.8-44.9): lap 22 started -0.956 s early with no run-up and -0.950 s with 10 s of it; lap 47,
# whose start sat closer to a keyframe, -0.193 s and -0.186 s. The audio came out with it (-0.977 /
# -0.200 s), so the clip was internally in sync and only the OVERLAY was wrong. With the durations
# declared, ffmpeg's ordinary accurate `-ss` lands on the same frame the single-chapter branch
# lands on: +0.005..+0.031 s, which is the render's own fps grid and is what a single-chapter lap
# has always returned.
#
# `time_offset` is the global->local shift (local = global - time_offset): the first spanned
# chapter's offset for BOTH chaptered branches, 0 for a plain single file (global == local).
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
      * spans a seam   -> the concat demuxer over chapters [i0..i1], every entry carrying its
                          `duration` (what makes the span seekable — see the section comment above);
                          time_offset = chapters[i0].offset, exactly as for a single chapter, so the
                          same accurate `-ss local` seeks both branches. The concat list is written
                          under `tmp_dir`; the caller frees it via cleanup().
    Raises ValueError if `chapter_map` has no chapters.

    BOTH BRANCHES NOW MEAN THE SAME THING BY `local`, which is the point: the concat stream's clock
    starts where chapter i0 starts, so `t0 - offset_i0` addresses the same instant in the span as it
    does in the file. The old concat branch instead made the stream begin AT the lap (via a
    keyframe-granular `inpoint`) and seeked with `-ss 0`, which is how the picture came out up to a
    GOP ahead of the overlay stamped on it."""
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
    # Concat the spanned chapters as one stream that starts where chapter i0 starts, so the seek is
    # the same `global - offset_i0` the single-chapter branch uses.
    span = chs[i0:i1 + 1]
    tmp = tmp_dir or os.environ.get("TMPDIR") or "/tmp"
    list_path = _mk_concat_list(span, tmp)
    return VideoSource(probe_path=start.path, time_offset=float(start.offset),
                       concat_list_path=list_path)


def _mk_concat_list(chapters_span, tmp_dir: str) -> str:
    """Write an ffmpeg concat-demuxer list file for `chapters_span` into `tmp_dir`; returns its path.

    Every entry declares the chapter's own `duration`. That is not decoration: the concat demuxer
    only supports SEEKING when the duration of every file in the list is known up front, and an
    `-ss` into a span without them collapses into decoding the span from its beginning — 663 s
    against 1.2 s for the same half-second of D24 picture. Declaring them also pins the concat
    stream's clock to the very numbers `ChapterMap` accumulated its offsets from.

    A chapter whose duration is unknown (0.0 — a map built without media durations, where the global
    offsets are already degenerate) suppresses the directives for the whole span rather than
    declaring a zero-length file. That costs speed and nothing else: the undeclared seek still lands
    on the exact frame (measured, exact-pixel, on a synthetic span), it just decodes its way there.
    A slow correct seek beats a fast wrong one."""
    import tempfile
    fd, list_path = tempfile.mkstemp(prefix="pacer_export_concat_", suffix=".txt", dir=tmp_dir)
    durations = [float(getattr(c, "duration", 0.0) or 0.0) for c in chapters_span]
    declare = all(d > 0.0 for d in durations)
    lines = []
    for c, dur in zip(chapters_span, durations, strict=True):
        ap = os.path.abspath(c.path)
        # concat-demuxer quoting: a literal ' inside a single-quoted token is '\''.
        esc = ap.replace("'", "'\\''")
        lines.append(f"file '{esc}'\n")
        if declare:
            lines.append(f"duration {dur:.6f}\n")
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
    g-meter scope are shown.

    `is_best` = the exported lap IS the session's best lap, i.e. the lap every Δ is measured
    against. It is a VERDICT about this export rather than a layout knob, which is why it lives
    here and not on the frozen `OverlayConfig`: `build_lap_spec` resolves it once from
    `session.best_lap_id()`, and the compositor reads it every frame. See `_paint_strip` for what
    it changes and why it has to.

    `lead_in`/`lead_out` = the run-up and run-off actually APPLIED (seconds), so `t0`/`t1` are the
    RENDERED window and the LAP's own window is `[lap_t0, lap_t1)` inside it. They are the applied
    amounts and not the requested ones because `lap_window_for_export` clamps the padding to the
    footage: on the first and last laps of a recording there is less run-up/run-off to be had than
    was asked for, and every consumer here needs the truth rather than the request. They live on
    the spec for the same reason `is_best` does — the WINDOW is the spec's job; `OverlayConfig`
    carries layout/output knobs."""
    out_path: str
    lap_id: int
    t0: float
    t1: float
    src_path: str = ""
    config: OverlayConfig = field(default_factory=OverlayConfig)
    source: VideoSource | None = None
    is_best: bool = False
    lead_in: float = 0.0
    lead_out: float = 0.0
    # FULL-SESSION scope: the overlay FOLLOWS the laps instead of naming one. Every other scope
    # renders a single lap and `lap_id` is the lap it is OF — a fact each frame repeats, which is
    # what a padded export needs (a lead-in sits inside lap N-1 and must still say lap N). A whole
    # recording has no such lap, so here `lap_at_time` answers per frame, exactly as the live
    # readout does, and `lap_id` is only a hint for the map inset (None = draw the whole session).
    follow_laps: bool = False
    # The session's best lap, resolved once. `is_best` answers "is THIS export's lap the best one"
    # and cannot: in follow mode the answer changes 40 times in one file.
    best_lap_id: int | None = None

    def __post_init__(self):
        # Back-compat: a caller that passed only `src_path` (the legacy single-file API + the
        # mocked/real tests) gets a single-file source at offset 0 (global == local, unchanged).
        if self.source is None:
            if not self.src_path:
                raise ValueError("ExportSpec needs either a `source` or a `src_path`")
            self.source = single_file_source(self.src_path)
        elif not self.src_path:
            self.src_path = self.source.probe_path
        # Negative padding would put the lap window OUTSIDE the rendered one; clamp rather than
        # raise, so a bad caller degrades to "no padding" instead of failing an export.
        self.lead_in = max(0.0, float(self.lead_in))
        self.lead_out = max(0.0, float(self.lead_out))

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)

    @property
    def lap_t0(self) -> float:
        """Media time of the START LINE — where the rendered window begins once the lead-in is
        taken off. Equals `t0` for an unpadded export."""
        return self.t0 + self.lead_in

    @property
    def lap_t1(self) -> float:
        """Media time of the FINISH LINE (half-open, like `Session.lap_window`). Equals `t1` for
        an unpadded export."""
        return self.t1 - self.lead_out

    @property
    def lap_duration(self) -> float:
        """The LAP's own length — what the strip's clock counts up to, and what every Δ/lap
        budget is measured over. Not `duration`, which is the whole rendered clip."""
        return max(0.0, self.lap_t1 - self.lap_t0)

    @property
    def local_t0(self) -> float:
        """The file-LOCAL seek time (what ffmpeg `-ss` gets): the global t0 shifted into the
        resolved source's own clock. For a single-file/offset-0 source this equals `t0`."""
        return self.t0 - self.source.time_offset

    def is_best_at(self, lap_id: int | None) -> bool:
        """Whether the lap a FRAME is showing is the session's best — the `★ BEST` mark's
        condition. For a single-lap export this is the spec's own verdict and never varies; in
        follow mode it is re-asked per frame against the lap that frame is in."""
        if not self.follow_laps:
            return self.is_best
        return (self.best_lap_id is not None and lap_id is not None
                and int(self.best_lap_id) == int(lap_id))

    @property
    def is_png_sequence(self) -> bool:
        """True when `out_path` names a DIRECTORY of frames rather than a file — the one output
        of this module that is not a single artifact, so every path that creates, cleans up or
        reports on the output has to ask."""
        return bool(self.config.overlay_only and self.config.alpha_codec == ALPHA_PNG)

    def output_frame(self, probe) -> tuple[int, int, float]:
        """(out_w, out_h, fps) this spec renders at, from the source's real frame as `probe`
        (`probe_video_size`'s shape) reports it — the same `frame_geometry` / `resolve_fps` the
        `Renderer` resolves, so a size estimate describes the frame that gets written."""
        src_w, src_h, src_fps = probe(self.source.probe_path)
        geo = frame_geometry(src_w, src_h, self.config)
        return geo.out_w, geo.out_h, resolve_fps(self.config, src_fps)


# --------------------------------------------------------------------------- clock conversion
# EVERY TIME IN THIS MODULE IS A MEDIA TIME — ffmpeg seeks with it, `ChapterMap` resolves the source
# file from it, and `frame_times` stamps the frames with it. Everything a Session states is a
# TELEMETRY (GPS9 true-clock) time. The two drift apart by up to 0.22 s over a long recording, and
# the trace's timestamps are a further ~0.46 s behind the picture (see studio/media_clock.py), so
# the module converts at exactly two places: `lap_window_for_export` takes the lap window INTO
# media time, and the per-frame lookups take a frame's time BACK before they index the session.
# Both go through these two helpers, which duck-type the conversion so the dozens of stand-in
# sessions in the tests (and any session with no clock) mean "no conversion". The helpers carry
# BOTH corrections because the session's own map does — the burned clip and the live view are the
# same conversion, so a frame grab can still be checked against the app.
def _media_time(session, t: float) -> float:
    """A Session telemetry time -> the media time to seek the picture to."""
    fn = getattr(session, "media_time", None)
    return float(fn(t)) if callable(fn) else float(t)


def _telemetry_time(session, t: float) -> float:
    """A frame's media time -> the telemetry time to ask this session about."""
    fn = getattr(session, "telemetry_time", None)
    return float(fn(t)) if callable(fn) else float(t)


# --------------------------------------------------------------------------- trim math
def footage_duration(session) -> float | None:
    """The FOOTAGE's total length on the global media clock, or None when the session cannot state
    one (no `ChapterMap` — the single-file and duck-typed sessions).

    Deliberately NOT `session.tt[-1]`: `load._clean` trims the stationary lead-in and the cool-down
    out of the TELEMETRY, so the trace spans strictly less time than the video does. Bounding a
    lead-in by the telemetry would refuse run-up footage that plainly exists — the footage bound is
    the chapter table's own cumulative duration."""
    chapter_map = getattr(session, "chapters", None)
    total = getattr(chapter_map, "total_duration", None) if chapter_map is not None else None
    try:
        total = float(total)
    except (TypeError, ValueError):
        return None
    return total if total > 0 else None


def lap_window_for_export(session, lap_id: int, lead_in: float = 0.0,
                          lead_out: float = 0.0) -> tuple[float, float] | None:
    """The MEDIA-clock (t0, t1) window to RENDER for `lap_id`: the lap's own window
    (`Session.lap_window`, CONVERTED onto the media clock) widened by `lead_in` seconds of run-up
    and `lead_out` of run-off, clamped to the footage. None if the lap window itself is unusable.

    THE CONVERSION IS THE POINT OF THIS BEING A FUNNEL. `Session.lap_window` answers on the GPS9
    true clock — the clock lap times are validated on — and this module seeks the picture, which
    runs on the media clock. Handing the one to the other is what made the last lap of an
    84-minute recording start ~5 frames early at the default 30 fps (~10 at 59.94), growing with
    position through the recording; see studio/media_clock.py. The padding and the footage clamp
    then happen in media time, which is also the clock `footage_duration` speaks — before this,
    `t1` was compared against the chapter table's total in the wrong units.

    THE PADDING HAPPENS IN THIS FUNNEL, and it happens BEFORE `resolve_video_source` ever sees the
    window. Source resolution is what picks the chapter file and the concat span, so a lead-in that
    reaches back across a chapter seam has to widen the window first or it resolves against the
    wrong file. Both callers (`build_lap_spec` and app.py's pre-flight check) come through here,
    which is what keeps the app's guard and the spec build describing the same window.

    THE CLAMPS ARE NOT COSMETIC — each one is a wrong clip that would otherwise ship:

      * a negative `t0` is silently wrong rather than loudly wrong. It asks for footage before the
        recording began: `chapter_at` clamps to chapter 0, so the source resolves happily and ffmpeg
        decodes from wherever it can, with every frame stamped |t0| seconds early — the overlay and
        the picture desynced for the entire clip. `guard_validate_window` refuses it twice over (the
        GLOBAL test, and now the source-LOCAL one on both branches), but a refused export is still a
        failed export; the clamp is what makes the first lap of a recording simply export with less
        run-up than was asked for.
      * there is no upper bound anywhere else. A `t1` past the end of the footage makes
        `frame_times` size the render for frames that do not exist. `run_chunk` now REFUSES a
        decode that runs dry more than a frame short of the plan (see `TruncatedRenderError`) —
        before that it took any short read with `produced > 0` as a clean finish, so such an export
        "succeeded" with a clip shorter than asked for and a bar that stopped before 100 %.

    Neither clamp can shrink the LAP: they only take back padding that ran off the end of the
    recording, so the first and last laps of a session export with as much run-up/run-off as
    exists and no less of the lap."""
    win = session.lap_window(lap_id)
    if win is None:
        return None
    lap_t0 = _media_time(session, float(win[0]))
    lap_t1 = _media_time(session, float(win[1]))
    if not (lap_t1 > lap_t0):
        return None
    t0 = min(lap_t0, max(0.0, lap_t0 - max(0.0, float(lead_in))))
    t1 = lap_t1 + max(0.0, float(lead_out))
    total = footage_duration(session)
    if total is not None:
        t1 = max(lap_t1, min(t1, total))
    return t0, t1


def frame_count(t0: float, t1: float, fps: float) -> int:
    """How many output frames a [t0, t1) window holds at `fps`: the number of frame STARTS inside
    the half-open window, i.e. ceil(duration*fps). The plan every other number here is derived
    from — `frame_times`, the progress bar's denominator, and the clip length both ffmpeg commands
    are asked for."""
    if fps <= 0:
        raise ValueError("fps must be positive")
    return max(int(np.ceil((t1 - t0) * fps - 1e-9)), 0)


def clip_seconds(t0: float, t1: float, fps: float) -> float:
    """The clip length ffmpeg is ASKED for: exactly `frame_count` frames, never the raw window.

    THIS IS THE FIX FOR "ONE FRAME SHORT", AND IT IS THE SAME NUMBER ON BOTH SIDES OF THE PIPE.
    A lap window is a real-valued span, so `duration*fps` is almost never a whole number — and the
    two ends of the pipeline rounded the leftover differently:

      * the DECODER trims to the requested duration in the OUTPUT frame's own timebase, which
        ROUNDS: a 2046.07-frame window delivered 2046 frames against a plan of 2047, so the render
        hit a short read and the bar stopped one frame below its own total. Measured across 96
        windows on both D24 recordings (interior and seam-crossing, 25 / 30 / 59.94 fps): every
        window whose fractional frame was below ~0.5 came up one frame short, 18 of 96;
      * the MUXER dropped the last frame whenever it ENDED past the audio, which a duration-trimmed
        audio input guaranteed for any fractional window: 0060 lap 17 planned 2047, wrote 2047,
        and the file held 2046 (video 68.200 s vs audio 68.229 s) on VideoToolbox AND libx264, at
        30 fps AND 59.94 — the case review §4.5 reported and read as a decoder short read.

    Asking both ends for `n/fps` instead makes the request land exactly on a frame boundary: the
    decoder's rounding has nothing to round, and the last frame no longer ends past the audio. No
    frame is invented to get there — frame n-1 starts at `t0 + (n-1)/fps`, which is inside the lap
    window by construction, so it is picture the window always contained."""
    return frame_count(t0, t1, fps) / float(fps)


def frame_times(t0: float, t1: float, fps: float) -> np.ndarray:
    """The media-clock timestamp of each output frame for a [t0, t1) window at `fps`: ceil(dur*fps)
    frames starting at t0, spaced 1/fps apart, so the i-th frame we composite is stamped with the
    time ffmpeg decoded it from. Used to drive the per-frame overlay lookups and to size the
    progress bar. `clip_seconds` asks ffmpeg for exactly these frames and no others."""
    return t0 + np.arange(frame_count(t0, t1, fps)) / fps


def resolve_fps(cfg: OverlayConfig, src_fps: float) -> float:
    """The output fps for the render: an explicit `cfg.fps` wins; otherwise the source rate, then
    `cfg.fps_cap` caps it (so a 59.94 fps GoPro exports at 30 by default — half the frames, half the
    work, no perceptible loss for a telemetry overlay). Never exceeds the source rate (capping up
    would only duplicate frames). Guards a non-positive source by falling back to the cap/30.

    AN OVERLAY-ONLY RENDER DIVIDES THE SOURCE RATE INSTEAD OF CAPPING IT. That file goes back over
    the footage in an editor, frame against frame, and 30.000 over 60000/1001 footage cannot: the
    two clocks part by 0.1 %, a frame of drift every 33 s of overlay — 77 ms by the end of the
    owner's 77.5 s lap-14 clip, 2.7 s over a 45-minute session. Dividing by the smallest whole
    number that brings the source under the cap keeps every overlay frame ON a source frame
    (59.94 -> 29.97, 50 -> 25, 119.88 -> 29.97; a rate already under the cap is kept), which is
    also what lets it carry the footage's timecode (`plan_source_sync`). A composite has its
    footage burned in, so there is nothing to line up and it keeps the plain cap."""
    if cfg.fps:
        return float(cfg.fps)
    fps = float(src_fps) if src_fps and src_fps > 0 else (cfg.fps_cap or 30.0)
    if cfg.fps_cap:
        cap = float(cfg.fps_cap)
        if cfg.overlay_only and src_fps and src_fps > 0:
            fps = fps / max(1, math.ceil(fps / cap - 1e-9))
        else:
            fps = min(fps, cap)
    return fps


def rate_arg(fps: float) -> str:
    """`fps` as the rate string ffmpeg is handed: the exact rational when `fps` is one with a
    denominator of at most 1001 (every NTSC rate), else six decimals. "29.970030" is NOT 30000/1001
    to ffmpeg — it approximates the decimal — and a timecode track needs the rate it counts in."""
    exact = Fraction(fps).limit_denominator(1001)
    if abs(float(exact) - fps) < 1e-9:
        return f"{exact.numerator}/{exact.denominator}"
    return f"{fps:.6f}"


# --------------------------------------------------------------------------- NLE sync (overlay-only)
# An overlay-only file exists to be laid back over its footage in Resolve/Premiere, and an editor
# can only line two clips up by what they carry. The owner's lap-14 ProRes carried nothing: no
# timecode, 30/1 against 60000/1001 footage, and no word from the app about where in the footage it
# began. The GoPro file does carry a clock — a `tmcd` track: MK_18_09_26 chapter 1 starts at
# 10:51:06:25 and chapter 2 at 11:24:10:25, NDF, 60 frames to the TC-second (each 1.001 s long);
# chapter 1 is 1985.984 s = 119,040 frames = 33:04:00 of timecode, which is exactly the gap between
# the two. So the render snaps its first frame onto a source frame the overlay's own count can name,
# and writes that name into the file.
@dataclass(frozen=True)
class SourceClock:
    """A source file's exact frame rate and the timecode of its first frame (None: it has none)."""
    rate: Fraction
    timecode: str | None


@dataclass(frozen=True)
class SourceSync:
    """Where an overlay-only render sits in its footage. `t0` is the snapped window start on the
    global media clock (frame 0 of the overlay); `source_name`/`source_frame`/`local_start` say
    which frame of which file that is — the FIRST spanned chapter, whose clock a seam-crossing
    window keeps counting on, exactly as the next chapter's own timecode does. `timecode` is the
    overlay's start in its own frame count, what gets embedded, and `source_timecode` the same
    instant in the footage's count (what an editor sees on the source clip); both None when the
    source has no NDF timecode this rate can name."""
    t0: float
    source_name: str
    source_frame: int
    local_start: float
    rate: Fraction
    timecode: str | None = None
    source_timecode: str | None = None


def timecode_frames(tc: str, count: int) -> int | None:
    """An NDF `HH:MM:SS:FF` timecode as frames since midnight at `count` frames per TC-second.
    None for drop-frame (`;`/`,` — GoPro writes NDF, and DF counting is a different sum) and for
    anything malformed."""
    if not tc or ";" in tc or "," in tc or count <= 0:
        return None
    parts = tc.replace(".", ":").split(":")
    if len(parts) != 4:
        return None
    try:
        h, m, s, f = (int(x) for x in parts)
    except ValueError:
        return None
    if not (0 <= m < 60 and 0 <= s < 60 and 0 <= f < count and h >= 0):
        return None
    return ((h * 60 + m) * 60 + s) * count + f


def format_timecode(frames: int, count: int) -> str:
    """Frames since midnight at `count` per TC-second as NDF `HH:MM:SS:FF`, wrapped at 24 h."""
    frames %= 24 * 3600 * count
    secs, f = divmod(frames, count)
    return f"{secs // 3600:02d}:{secs // 60 % 60:02d}:{secs % 60:02d}:{f:02d}"


_SOURCE_CLOCK_CACHE: dict[tuple[str, int, int], SourceClock | None] = {}


def probe_source_clock(path: str) -> SourceClock | None:
    """The source's exact video rate and start timecode, via one ffprobe, cached per file (path,
    size, mtime). None when it cannot be asked; a missing tag is a clock with `timecode=None`.
    The video stream's own tag is preferred and a `tmcd` data stream's is the fallback — GoPro
    stamps both with the same value."""
    key = _video_size_key(path)
    if key is not None and key in _SOURCE_CLOCK_CACHE:
        return _SOURCE_CLOCK_CACHE[key]
    clock = None
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-show_entries",
             "stream=codec_type,r_frame_rate:stream_tags=timecode", "-of", "json", path],
            capture_output=True, text=True, timeout=20, check=True).stdout
        streams = json.loads(out).get("streams", [])
        video = [st for st in streams if st.get("codec_type") == "video"]
        if video:
            rate = Fraction(str(video[0]["r_frame_rate"]))
            tags = [st.get("tags", {}).get("timecode") for st in video + streams]
            tc = next((t for t in tags if t), None)
            clock = SourceClock(rate, tc) if rate > 0 else None
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError,
            ZeroDivisionError, AttributeError):
        clock = None
    if key is not None:
        _SOURCE_CLOCK_CACHE[key] = clock
    return clock


def plan_source_sync(spec: ExportSpec, clock: SourceClock | None, fps: float) -> SourceSync | None:
    """Snap an overlay-only window onto its footage and name its start. None when the render rate
    is not a whole division of the source rate (an explicit fps can ask for that) or the source's
    clock is unknown — the window then renders exactly as asked, and says nothing.

    The first frame is the source frame AT OR BEFORE `spec.t0` (a lap keeps its first instant),
    moved back further to the nearest frame the overlay's count can name: at 29.97 over 59.94 the
    overlay's timecode counts every second source frame, so only the frames whose own 60-count
    number is even have a 30-count name. At the very start of the footage it moves forward
    instead. Without a timecode any source frame will do. Either way every later overlay frame n
    sits on source frame `source_frame + n*k`, `k` the division."""
    if clock is None or not (fps > 0):
        return None
    k = round(float(clock.rate) / fps)
    if k < 1 or abs(float(clock.rate) / k - fps) > 1e-6:
        return None
    local = max(0.0, spec.t0 - spec.source.time_offset)
    # +1e-6 frame: a window that already starts on a frame (a retry's spec) stays on it.
    j = int(math.floor(local * clock.rate + 1e-6))
    count = round(float(clock.rate))
    start = timecode_frames(clock.timecode, count) if clock.timecode else None
    tc = src_tc = None
    if start is not None and count % k == 0:
        j -= (start + j) % k
        if j < 0:
            j += k
        tc = format_timecode((start + j) // k, count // k)
        src_tc = format_timecode(start + j, count)
    local0 = Fraction(j) / clock.rate
    return SourceSync(t0=spec.source.time_offset + float(local0),
                      source_name=os.path.basename(spec.source.probe_path),
                      source_frame=j, local_start=float(local0), rate=clock.rate / k,
                      timecode=tc, source_timecode=src_tc)


def _rate_words(rate) -> str:
    """29.97 / 25 / 23.976 — a frame rate the way an editor writes it."""
    return f"{float(rate):.3f}".rstrip("0").rstrip(".")


def sync_sentence(sync: SourceSync | None, png: bool = False) -> str:
    """The export-finished box's line saying where an overlay-only file starts in its footage, or
    "" when there is nothing to say (a composite, or a render that could not be placed)."""
    if sync is None:
        return ""
    if sync.source_timecode is None:
        return (f"Starts {fmt_time(sync.local_start)} into {sync.source_name} (source frame "
                f"{sync.source_frame}); the footage has no timecode to embed.")
    where = f"Starts at {sync.source_timecode} in {sync.source_name}"
    if png:
        return f"{where}. A PNG sequence cannot carry timecode, so place its first frame there."
    return (f"{where} — timecode embedded ({sync.timecode} at the overlay's "
            f"{_rate_words(sync.rate)} fps).")


# --------------------------------------------------------------------------- per-frame values
# A lap window is half-open, so the finish instant itself is not IN the lap. The lap clock is
# clamped a hair short of it when a lead-out freezes the clock/Δ at their final values, rather than
# asking `delta_at_lap` for a time the lap does not contain.
_LAP_CLOCK_EPS = 1e-3


@dataclass
class OverlayValues:
    """The telemetry values shown for ONE frame at media time `t` — exactly what the live readout
    shows at t (so a frame grab can be cross-checked against the app). `speed_kmh` is None only
    when there is no trace; `delta_s` is None before the start line; `g` is None when there's no
    IMU signal.

    `lap_started`/`lap_finished` place the frame against the EXPORTED lap. They matter only for a
    padded export, and they default to "the lap is running" so an unpadded frame is described
    exactly as it always was. Everything LAP-scoped (the clock, the progress fill, the Δ, the
    g-envelope) is gated on them; everything TIME-scoped (the speed, the g dot, the map marker) is
    not, because those are valid at any media time and blanking them over footage where the kart is
    plainly doing 88 would be a lie."""
    t: float
    lap_id: int | None
    speed_kmh: float | None
    delta_s: float | None
    g: tuple[float, float, float] | None
    marker_index: int | None
    lap_started: bool = True
    lap_finished: bool = False
    # The TELEMETRY time this frame is a picture of (`_telemetry_time(t)`, lag correction
    # included) — what the map inset interpolates its marker at and ends its tail on. None on a
    # values object built by hand, which then draws the marker at `marker_index` with no tail.
    tt: float | None = None


def overlay_values_at(session, t: float, spec: ExportSpec | None = None) -> OverlayValues:
    """Resolve the overlay values for the frame at MEDIA time `t`, the SAME way app._apply_readout
    does — with `tt = session.telemetry_time(t)`, the instant that frame is a picture OF:

      * lap        = session.lap_at_time(tt)
      * marker idx = session.index_at_time(tt)       (nearest trace sample)
      * speed km/h = session.tv[idx]                 (the per-sample km/h array)
      * Δ-to-best  = session.delta_at_lap(lap, tt)   (normalized-distance vs the best/ref lap)
      * g          = session.g_at_time(tt)           (kart-frame lat/long/total in g)

    THE CONVERSION IS THE SECOND HALF OF THE FIX `lap_window_for_export` starts. Once the window is
    on the media clock, `frame_times` stamps every frame with a media time — and a session's series
    are all indexed on the telemetry clock, so asking them with a frame time directly would put the
    error straight back, now spread over the numbers instead of the seek. Sessions with no clock
    (the stand-ins here, a GPS5 camera) convert by identity, so nothing about them changes.

    Single-sourcing these here keeps the burned-in numbers identical to the app's, and makes the
    per-frame lookup unit-testable against a synthetic Session (no Qt, no ffmpeg).

    WITH A `spec`, THE LAP IS THE EXPORTED ONE — resolved from `spec.lap_id`, never from
    `lap_at_time(t)`. That is not a shortcut, it is the whole correctness of a padded export: laps
    are CONTIGUOUS, so every frame of a lead-in sits inside lap N−1 and `lap_at_time` answers N−1
    for all of it. Left alone the strip would read `LAP 11  1:02.4` under a nearly-full progress
    bar and then snap to `LAP 12  0:00.0`, and `delta_at_lap` would hand back the wrong lap's
    baseline. The clip is of lap N; it says lap N throughout, marked pending until the line.

    The lap CLOCK is clamped into the lap, so a lead-out freezes the Δ at the gap the lap actually
    finished on rather than extrapolating past the flag. Speed, marker and g are read at the real
    `t` — `index_at_time` and `gmeter.at_time` both clamp and never return None, so they are valid
    outside the lap and are the reason the padding shows live footage with live numbers.

    A FOLLOW-LAPS spec (the full-session scope) takes the no-spec branch deliberately. There the
    clip is not OF a lap, so pinning one would be the wrong lie in the other direction: every
    frame names the lap it is actually in, out-laps and pit time read as "no lap", and the clock
    restarts at each line — which is what the live readout does over the same footage."""
    # The frame's own instant on the clock every session series is indexed by. The window
    # comparisons below stay in MEDIA time (spec.lap_t0/lap_t1 are media, like `t`); only the
    # lookups cross over.
    tt = _telemetry_time(session, t)
    if spec is None or spec.follow_laps:
        lap_id = session.lap_at_time(tt)
        started, finished, clock_t = lap_id is not None, False, tt
    else:
        lap_id = spec.lap_id
        started = t >= spec.lap_t0 - 1e-9
        finished = t >= spec.lap_t1
        clock_t = _telemetry_time(
            session, min(max(t, spec.lap_t0), max(spec.lap_t0, spec.lap_t1 - _LAP_CLOCK_EPS)))
    i = session.index_at_time(tt)
    speed = float(session.tv[i]) if i is not None and len(session.tv) else None
    delta = session.delta_at_lap(lap_id, clock_t) if (lap_id is not None and started) else None
    g = session.g_at_time(tt) if getattr(session, "has_gmeter", False) else None
    return OverlayValues(t=t, lap_id=lap_id, speed_kmh=speed, delta_s=delta, g=g, marker_index=i,
                         lap_started=started, lap_finished=finished, tt=tt)


# --------------------------------------------------------------------------- ffmpeg commands
def _even(n: float) -> int:
    """Round UP to an even integer >= 2. Every encoder this module can reach refuses odd
    dimensions in a chroma-subsampled pixel format, and the 4:2:0 ones refuse them outright."""
    n = max(2, int(round(n)))
    return n + (n & 1)


def _even_down(n: float) -> int:
    """Round DOWN to an even integer >= 2 — for a rectangle that must stay INSIDE something else.
    A crop is the case: it is bounded by the source frame, and rounding a full-width crop up by a
    pixel on an odd-width source asks ffmpeg for a rectangle larger than the picture it is cut
    from, which is an error rather than a clamp."""
    n = max(2, int(n))
    return n - (n & 1)


@dataclass(frozen=True)
class FrameGeometry:
    """The output frame this export writes, and the filter chain that makes the source fill it.

    `out_w`/`out_h` are the final, EVEN pixel dimensions. `scale_filter` is the `-vf` chain
    (without the trailing `fps=`) that turns a decoded source frame into one output frame:
    a plain `scale` for the source's own aspect, a cover-then-crop for CROP, a
    contain-then-pad for FIT."""
    out_w: int
    out_h: int
    scale_filter: str


def frame_geometry(src_w: int, src_h: int, cfg: OverlayConfig) -> FrameGeometry:
    """Resolve the output frame for a `src_w x src_h` source under `cfg.aspect` / `cfg.frame_fit`.

    `cfg.out_height` is the target SHORT SIDE. On the historic source-aspect path the short side
    of a landscape frame IS its height, so that spelling changes nothing there and the function
    returns exactly what `output_size` always did. For a 9:16 or 1:1 output it is what makes
    "1080p" mean the 1080 the user pictures — a 1080x1920 vertical clip, not a 608x1080 one.

    NEVER UPSCALING IS A PER-MODE QUESTION, not one rule, because the two modes use different
    source pixels:

      * CROP takes the largest `aspect`-shaped rectangle out of the source, so what bounds the
        output is that RECTANGLE's short side. A 9:16 crop of a 1920x1080 frame is 608x1080 of
        real pixels, so 1080p-vertical is refused on 1080p footage and lands at 608x1080 — while
        the same request on 4K footage crops 1215x2160 and downscales to a clean 1080x1920. That
        is the honest answer: a phone-shaped clip out of a landscape camera has one eighth the
        pixels of the frame it came from, and pretending otherwise just ships a blurry file.
      * FIT keeps the whole picture, so what bounds the output is the axis the picture SPANS —
        the width when the source is the wider shape (letterbox), the height when it is taller
        (pillarbox).

    The bars are painted BLACK. The one documented manual pipeline for this rendered telemetry
    onto a solid blue and chroma-keyed it in Premiere to get it rescaled for 9:16; the point of
    doing the reflow in the renderer is that there is nothing left to key."""
    src_w, src_h = int(src_w), int(src_h)
    want = max(2, int(cfg.out_height))
    ratio = ASPECT_RATIOS.get(cfg.aspect)
    if ratio is None or not (src_w > 0 and src_h > 0):
        # SOURCE ASPECT — the historic path, byte-for-byte: height controls, width follows.
        h = min(want, src_h) if src_h else want
        w = int(round(src_w * (h / src_h))) if src_h else h * 16 // 9
        w, h = _even(w), _even(h)
        return FrameGeometry(w, h, f"scale={w}:{h}")
    src_aspect = src_w / src_h
    if cfg.frame_fit == FIT_FIT:
        # The picture spans the WIDTH when it is the wider shape, else the HEIGHT.
        if src_aspect >= ratio:
            w = _even(min(want if ratio <= 1 else want * ratio, src_w))
            h = _even(w / ratio)
        else:
            h = _even(min(want if ratio >= 1 else want / ratio, src_h))
            w = _even(h * ratio)
        # Contain, then pad the remainder. `force_divisible_by=2` rounds the contained picture
        # DOWN to even under `decrease`, so it can never come out a pixel LARGER than the frame it
        # is being padded into — a `pad` smaller than its input is a hard ffmpeg error, not a crop.
        return FrameGeometry(w, h, f"scale={w}:{h}:force_original_aspect_ratio=decrease:"
                                   f"force_divisible_by=2,"
                                   f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
    # CROP: the largest `ratio`-shaped rectangle inside the source bounds the output.
    crop_w, crop_h = (src_h * ratio, float(src_h)) if src_aspect > ratio else (float(src_w), src_w / ratio)
    short = min(want, int(min(crop_w, crop_h)))
    w, h = (_even(short), _even(short / ratio)) if ratio <= 1 else (_even(short * ratio), _even(short))
    # CROP FIRST, THEN SCALE, and the order is worth three times the render.
    #
    # The obvious chain is ffmpeg's own idiom — `scale=W:H:force_original_aspect_ratio=increase`
    # to cover the frame, then `crop=W:H` to take the centre. It is also a trap on a source larger
    # than the output: covering a 1080x1920 frame from 4K footage means scaling the WHOLE
    # 3840x2160 picture UP to 3413x1920 and then throwing 68 % of it away. Measured over the same
    # 20 s D24 window at 1080p, VideoToolbox available: 57.3 s that way against 12-21 s for every
    # other shape in the same run. Cropping in SOURCE pixels first hands the scaler a 1215x2160
    # picture instead of a 3413x1920 one, and the crop itself is close to free.
    #
    # `crop` is centred by default, and the rectangle is even on both axes so the scale that
    # follows has no half-pixel to resolve.
    return FrameGeometry(w, h, f"crop={_even_down(crop_w)}:{_even_down(crop_h)},scale={w}:{h}")


def output_size(src_w: int, src_h: int, cfg: OverlayConfig) -> tuple[int, int]:
    """Output (W, H) — `frame_geometry`'s dimensions, kept as its own name because the size is
    what most callers (the renderer, the size estimate, the tests) actually want."""
    geo = frame_geometry(src_w, src_h, cfg)
    return geo.out_w, geo.out_h


def build_decode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float,
                     hwaccel: bool = False, scale_filter: str | None = None,
                     start_frame: int = 0) -> list[str]:
    """DECODE argv: -ss spec.local_t0 before the input + -t duration, fit the source into
    out_w x out_h (`scale_filter`, from `frame_geometry` — a plain scale, a cover-and-crop or a
    contain-and-pad), force the constant `fps`, emit rgb24 rawvideo to stdout; -an/-sn/-dn drop
    non-video. Input is spec.source.input_args() (chapter file or concat span); the source-LOCAL
    seek is what makes a chaptered export read the right footage. `hwaccel` adds
    `-hwaccel videotoolbox` to offload the decode (a big CPU relief on a core-starved machine).

    That `-ss` is ffmpeg's ACCURATE input seek: it lands on the keyframe at or before the target and
    then decodes forward and discards up to it, so the first frame out is the frame at t0 rather
    than the keyframe's. Which is the whole reason the seam branch stopped positioning itself with a
    concat `inpoint` — `inpoint` has no such second half, and left the picture a GOP ahead.

    THE FULL-RANGE TRAP CANNOT REACH THE ENCODER FROM HERE, AND THAT IS ARCHITECTURE RATHER THAN
    LUCK. A hardware encoder refuses full-range `yuvj420p` input, and the D24 fixture IS exactly
    that — `pix_fmt=yuvj420p, color_range=pc`, measured with ffprobe — so every render this module
    has been verified against was fed a full-range source. It never gets that far: this command's
    `-pix_fmt rgb24` converts at the DECODE boundary, the encoder's input is our rgb24 pipe, and
    `_video_codec_args` names its own output `-pix_fmt yuv420p` (+ `-color_range tv` on
    VideoToolbox). The four shapes rendered off that fixture all came back `yuv420p`. What would
    reawaken the trap is a single-process `-i src -c:v h264_videotoolbox` chain, which is the shape
    this pipeline deliberately is not; `tests/test_export_video.py` pins the two ends of it.

    Adding `format=yuv420p` to this chain would not harden anything and would cost something real:
    it would subsample chroma on the way to rgb24, throwing away half the colour resolution the
    overlay is then composited against, to protect a boundary that no longer exists.

    THE `fps` FILTER RUNS FIRST, so the scale/crop/pad chain only ever sees the frames the render
    keeps. It picks frames by timestamp alone and everything after it is a per-frame function, so
    the order cannot change a byte: measured on MK_18_09_26's lap 14 (4K 59.94 -> 1080p 30), the
    rgb24 stream's md5 over 300 frames was identical both ways, on the hardware and the software
    decode alike. What it changes is the work: a 59.94 fps source used to be scaled at 59.94 and
    then have half of it thrown away, and this decode is what a 1080p export waits on (60.1 ->
    69.9 fps from the decoder alone).

    `start_frame` is the same decode entered `start_frame` frames in — a relay decoder's (see
    `_DecodeRelay`). Its seek is the plan's own `-ss` plus exactly `start_frame / fps`, added in
    whole microseconds (the unit ffmpeg parses `-ss` into) rather than re-derived from a float, so
    both decodes see the same timestamps shifted by a whole number of frames; the relay only asks
    for a `start_frame` that makes that sum exact (`_relay_step`)."""
    hw = ["-hwaccel", "videotoolbox"] if hwaccel else []
    scale = scale_filter or f"scale={out_w}:{out_h}"
    n = frame_count(spec.t0, spec.t1, fps)
    seek = f"{spec.local_t0:.6f}"
    length = clip_seconds(spec.t0, spec.t1, fps)
    if start_frame:
        us = round(float(seek) * 1e6) + start_frame * 1_000_000 // round(fps)
        seek = f"{us // 1_000_000}.{us % 1_000_000:06d}"
        n -= start_frame
        length = n / float(fps)
    return [
        FFMPEG, "-nostdin", "-loglevel", "error",
        *hw,
        "-ss", seek, *spec.source.input_args(),
        # THE PLAN'S OWN LENGTH, not the raw window — see `clip_seconds`. Asking for the window's
        # 68.2286 s made ffmpeg's output trim round 2046.86 frames to 2046 and the render ran dry
        # one frame early; asking for 2047/30 s lands on the frame boundary. `-frames:v` then pins
        # the count from the other side, so the decode can never overrun the plan either.
        "-t", f"{length:.6f}",
        "-vf", f"fps={fps:.6f},{scale}",
        "-frames:v", str(n),
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

    Both end yuv420p + faststart so the MP4 is broadly playable and streams (moov atom up front).

    NEITHER PATH MAY EVER CARRY `-qp`, AND THAT IS NOT STYLE. Measured on this machine's
    ffmpeg 7.1.1 (sandbox off, so a real VideoToolbox session opens): the same 2 s source encoded
    with `-qp 12` and `-qp 32` produced files of 136,189 bytes EACH — the identical size, at both
    ends of a scale meant to span most of the quality range, i.e. h264_videotoolbox accepts the
    option, reports nothing, and ignores it. (Two runs at the SAME `-qp` differ in their stream
    hash — VT is not bit-deterministic run to run — so size, not md5, is what shows this.) The
    encoder does have a working constant-quality knob, `-q:v` on an inverse 1-100 scale: 20 / 50 /
    80 gave 42,768 / 75,774 / 187,394 bytes. This module is bitrate-targeted (`-b:v` + `-maxrate`
    + `-bufsize`) and so was never exposed to the trap; `tests/test_export_video.py` pins that no
    `-qp` creeps in later. See `alpha_codec_args` for the one path that has no bitrate at all."""
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


# --------------------------------------------------------------------- overlay-only (alpha) output
# ProRes 4444 carries an alpha plane; nothing else an NLE reliably imports does, short of a PNG
# sequence. TWO encoders write it, and which one runs is the difference between a 4K overlay
# rendering at 16 fps and at twice that:
#
#   VT_PRORES  Apple's own ProRes encoder through VideoToolbox — the media engine where the Mac has
#              one, Apple's software codec where it does not (`-allow_sw 1`, as on the H.264 path).
#              It takes `bgra`, which is what the painter can hand it without a conversion.
#   SW_PRORES  ffmpeg's `prores_ks`, the one that takes the 10-bit planar `yuva444p10le`. Portable,
#              and the fallback, but it is the bottleneck of every ProRes export it runs.
#
# MEASURED, M1 Pro, ffmpeg 7.1.1, 180 frames of 4K `testsrc2`, output discarded: generating the
# source alone 0.56 s; prores_ks 11.04 s; VideoToolbox on the media engine 1.16 s; VideoToolbox
# forced onto Apple's SOFTWARE ProRes (`-require_sw 1`) 1.69 s. So even a Mac with no ProRes
# hardware gains about seven times over prores_ks, which is why `-allow_sw` is on.
#
# THE HARDWARE PATH HAS TO EARN ITS PLACE ON EACH MACHINE (`prores_videotoolbox_usable`): a
# session has to open AND a half-transparent white pixel has to come back white at alpha ~128.
# An encoder that premultiplied would hand an NLE the grey halo `_paint_alpha_frame` warns about,
# and nothing about the file would say so. Apple's encoder keeps the alpha straight on both its
# paths here (white@128 -> 255,255,255,129; grey 96@128 -> 96,96,96,129 on the software one).
VT_PRORES = "prores_videotoolbox"
SW_PRORES = "prores_ks"
_PRORES_ENCODER = SW_PRORES        # the portable encoder, and what `alpha_codec_args` defaults to
# What we ASK prores_ks for. What comes back out reads as `yuva444p12le`, and that is not a
# mismatch to chase: the ProRes 4444 bitstream stores 12 bits, so ffmpeg's decoder advertises the
# 12-bit format for every 4444 stream whatever it was encoded from. The `yuva` prefix — the part
# that matters — is there either way, and a decoded frame measures 97.7 % fully transparent with
# an alpha range of 0..255, i.e. a real, straight (non-premultiplied) alpha plane. The same holds
# for VideoToolbox's output, which reads back as `yuva444p12le`, profile 4444, `ap4h` too.
ALPHA_PIX_FMT = "yuva444p10le"
# BOTH ProRes paths are converted AND labelled Rec. 709, and the two halves of that are one fix.
# VideoToolbox turns `bgra` into Y'CbCr with the BT.709 matrix whatever the stream is tagged, and
# ffmpeg converted with BT.601 for prores_ks; both left the file untagged. So the two encoders'
# files disagreed on every saturated colour — an opaque (200,30,10) read back from VideoToolbox's
# untagged file as (186,13,13), from prores_ks's as itself — and an NLE, which reads an untagged HD
# stream as 709, got prores_ks's wrong instead. Tagging the frames 709 before the encoder makes
# ffmpeg's own conversion use 709 too and writes the label into the stream: (200,30,10) now comes
# back exact from both encoders, in ffmpeg and in anything that honours the tag. A fallback that
# silently changed the colours of the overlay would not be a fallback.
_PRORES_REC709 = "setparams=colorspace=bt709:color_primaries=bt709:color_trc=bt709"
_PRORES_ALPHA_BITS = 16            # a full 16-bit alpha plane; the encoder's own default
_PNG_PATTERN = "overlay_%06d.png"  # frame files inside the chosen directory
# Bits per pixel per frame for the two alpha outputs, MEASURED on the frames this renderer
# actually produces — a 10 s window of a real D24 lap, rendered three times at three sizes:
#
#     short side      720        1080       1440
#     ProRes 4444   1.118      0.876      0.745   bits/px/frame
#     PNG sequence  0.401      0.311      0.263
#
# The 1080p column is what these carry, because 1080p is the default and the spread across the
# whole range is 1.5x — an estimate the dialog already prefixes with "About".
#
# APPLE'S OWN NUMBER WOULD HAVE BEEN SIX TIMES TOO BIG. ProRes 4444 is specified at 330 Mbit/s for
# 1080p29.97, i.e. 5.30 bits/px/frame, and that is the figure behind the user report of a
# 30-minute transparent export at 140 GB. It does not describe THIS frame: an overlay-only frame
# measured 97.7 % fully transparent, and ProRes codes that nearly for free. Estimating a
# 30-minute 1080p overlay from the spec sheet says 74 GB; from the measurement, 12 GB. Still
# worth a warning — it is roughly nine times the H.264 export beside it — but a warning that is
# six times over-stated is one users learn to ignore.
#
# THE TWO PRORES ENCODERS DO NOT WRITE THE SAME BYTES, and the hardware one is the default, so it
# has its own figure. Rendered frame for frame through both (the real renderer, MK_18_09_26 best
# lap and SD_19_09_26 best lap with 5 s of run-up, every frame of each):
#
#                        prores_ks   prores_videotoolbox   ratio
#     MK 1080p (2325 f)    0.881          0.907            1.030
#     SD 1080p (1705 f)    0.920          0.949            1.032
#     MK 2160p (2325 f)    0.610          0.632            1.036
#
# Apple's encoder spends about 3 % more on the same frame at every size; PRORES_VT_4444_BPP is
# the D24-measured figure above scaled by that ratio, so the two keep one 1080p basis.
PRORES_4444_BPP = 0.876
PRORES_VT_4444_BPP = 0.903
PNG_SEQUENCE_BPP = 0.311


def alpha_codec_args(alpha_codec: str, encoder: str = SW_PRORES) -> list[str]:
    """The `-c:v ...` portion for an OVERLAY-ONLY render — the one output path with no bitrate and
    no CRF, so there is no quality level to resolve and nothing for the `-qp` trap documented in
    `_video_codec_args` to be silently swallowed by.

    ProRes 4444 is intra-only and quantiser-driven by profile alone; the PNG sequence is
    lossless. Both keep every pixel's alpha, which is the entire point: this is a track to lay
    over the original footage in Resolve or Premiere, not a picture of one. `encoder` picks which
    ProRes encoder writes it (`resolve_alpha_encoder`); anything but VT_PRORES means prores_ks.

    `apl0` — what Resolve/Premiere look at to call it a real ProRes — is prores_ks's `-vendor`
    option; Apple's encoder writes `apl0` into every frame header itself (counted in the output:
    two frames, two `apl0`, from either encoder), so it has no such option to pass."""
    if alpha_codec == ALPHA_PNG:
        return ["-c:v", "png", "-pix_fmt", "rgba"]
    if encoder == VT_PRORES:
        return [
            "-c:v", VT_PRORES, "-profile:v", "4444", "-pix_fmt", "bgra", "-allow_sw", "1",
            "-vf", _PRORES_REC709,
        ]
    return [
        "-c:v", SW_PRORES, "-profile:v", "4444",
        "-pix_fmt", ALPHA_PIX_FMT, "-alpha_bits", str(_PRORES_ALPHA_BITS),
        "-vendor", "apl0",
        "-vf", _PRORES_REC709,
    ]


def alpha_input_pix_fmt(encoder: str) -> str:
    """The rawvideo format the painter hands an overlay-only encode: `bgra` for VideoToolbox, whose
    own input format it is (so ffmpeg converts nothing), `rgba` for everything else. Qt paints the
    two as `Format_ARGB32` and `Format_RGBA8888`, both straight alpha (`_paint_alpha_frame`)."""
    return "bgra" if encoder == VT_PRORES else "rgba"


def prores_alpha_round_trip(encoder: str) -> bool:
    """Encode one small frame through `encoder` exactly as a render would, decode it back, and say
    whether the alpha came back STRAIGHT: a half-transparent white pixel still white (every channel
    >= 245) at alpha 128 +- 4, and an opaque red still red (the channel order). A session that will
    not open, a premultiplying encoder and a decoder that fails are all simply "no".

    The file lives in a private temporary directory for the length of the call."""
    import tempfile

    side = 64                                   # the H.264 probe's size; small enough to be free
    frame = np.zeros((side, side, 4), np.uint8)
    frame[: side // 2] = (255, 255, 255, 128)   # the probe pixel: white, half transparent
    frame[side // 2:] = (220, 30, 20, 255)      # opaque red: R and B must not trade places
    fmt = alpha_input_pix_fmt(encoder)
    if fmt == "bgra":
        frame = frame[..., [2, 1, 0, 3]]
    with tempfile.TemporaryDirectory(prefix="pacer-prores-probe-") as tmp:
        out = os.path.join(tmp, "probe.mov")
        enc = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
             "-f", "rawvideo", "-pix_fmt", fmt, "-s", f"{side}x{side}", "-r", "30", "-i", "pipe:0",
             *alpha_codec_args(ALPHA_PRORES, encoder), out],
            input=np.ascontiguousarray(frame).tobytes() * 2, capture_output=True, timeout=30)
        if enc.returncode != 0:
            return False
        dec = subprocess.run(
            [FFMPEG, "-hide_banner", "-loglevel", "error", "-nostdin", "-i", out,
             "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"],
            capture_output=True, timeout=30)
    if dec.returncode != 0 or len(dec.stdout) != side * side * 4:
        return False
    px = np.frombuffer(dec.stdout, np.uint8).reshape(side, side, 4).astype(int)
    white, red = px[side // 4, side // 2], px[3 * side // 4, side // 2]
    straight = min(white[:3]) >= 245 and abs(white[3] - 128) <= 4
    ordered = red[0] >= 190 and red[2] <= 70 and red[3] >= 250
    return bool(straight and ordered)


def prores_videotoolbox_usable() -> bool:
    """True iff VideoToolbox's ProRes encoder is listed, a session opens AND it keeps a straight
    alpha (`prores_alpha_round_trip`). Cached — a fixed property of the machine, like
    `videotoolbox_usable`. Costs one tiny encode + decode (~0.3 s) the first time."""
    cached = getattr(prores_videotoolbox_usable, "_cached", None)
    if cached is not None:
        return cached
    ok = False
    if shutil.which(FFMPEG) is not None:
        try:
            listed = subprocess.run([FFMPEG, "-hide_banner", "-encoders"],
                                    capture_output=True, text=True, timeout=20).stdout
            ok = VT_PRORES in listed and prores_alpha_round_trip(VT_PRORES)
        except (OSError, subprocess.SubprocessError, ValueError):
            ok = False
    prores_videotoolbox_usable._cached = ok  # type: ignore[attr-defined]
    return ok


def resolve_alpha_encoder(choice: str) -> str:
    """Which encoder writes a ProRes 4444 overlay — the alpha twin of `resolve_encoder`, reading
    the SAME `OverlayConfig.encoder` words so one knob means one thing on every path:

      * "auto" (and the hardware words) -> VT_PRORES when `prores_videotoolbox_usable` says so
      * "libx264"/"software"/"sw"/"x264"/"cpu" -> always prores_ks (the render's retry uses this)

    Unlike the H.264 path, a forced "videotoolbox" still has to pass the probe: an alpha that
    comes back premultiplied is a wrong file, not a slower one."""
    c = (choice or "auto").lower()
    if c in ("libx264", "software", "sw", "x264", "cpu"):
        return SW_PRORES
    return VT_PRORES if prores_videotoolbox_usable() else SW_PRORES


# The encoder a failed or wedged HARDWARE render retries on, ONCE, as the `OverlayConfig.encoder`
# word that forces it. Only a hardware encoder has an entry: a software one failing again would
# fail the same way, and PNG has no second encoder at all.
_SOFTWARE_RETRY = {VT_H264: "libx264", VT_PRORES: "software"}


def png_sequence_pattern(out_dir: str) -> str:
    """The ffmpeg output pattern for a PNG-sequence export into `out_dir`. A sequence's "output"
    is a DIRECTORY rather than a file, and this is the one place that turns one into the other."""
    return os.path.join(out_dir, _PNG_PATTERN)


def build_overlay_only_encode_cmd(spec: ExportSpec, out_w: int, out_h: int,
                                  fps: float, encoder: str = SW_PRORES,
                                  timecode: str | None = None) -> list[str]:
    """ENCODE argv for an overlay-only render: input 0 = our RGBA rawvideo on stdin, and THAT IS
    THE ONLY INPUT. There is no source decode and no audio.

    NO AUDIO IS A DECISION, not an omission. The artifact is an overlay TRACK: the editor already
    has the footage this was rendered against, and it carries the audio. A second copy of the same
    audio riding on the overlay is a track to mute in every project it lands in — and a PNG
    sequence could not carry one at all, so including it for ProRes alone would make the two alpha
    formats mean different things.

    It is also why this path is CHEAPER than the composited one rather than more expensive despite
    ProRes's data rate: nothing is decoded, scaled or muxed against a source. The only work is the
    QPainter composite that both paths do anyway, plus the encode.

    `encoder` is the ProRes encoder the render resolved (`resolve_alpha_encoder`); it decides the
    pipe's pixel format too, because the painter fills the pipe in the encoder's own layout.

    `timecode` (`SourceSync.timecode`) becomes the .mov's `tmcd` track, counted at the exact rate
    `rate_arg` hands the input — the one thing an editor can line this file up by. A PNG sequence
    has nowhere to keep one, so it is never passed there; the finished box says where it starts
    instead (`sync_sentence`)."""
    png = spec.config.alpha_codec == ALPHA_PNG
    out = png_sequence_pattern(spec.out_path) if png else spec.out_path
    enc = SW_PRORES if png or encoder != VT_PRORES else VT_PRORES
    tc = ["-metadata", f"timecode={timecode}"] if timecode and not png else []
    return [
        FFMPEG, "-nostdin", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", alpha_input_pix_fmt(enc), "-s", f"{out_w}x{out_h}",
        "-r", rate_arg(fps), "-i", "pipe:0",
        "-map", "0:v:0",
        *alpha_codec_args(spec.config.alpha_codec, enc),
        *tc,
        out,
    ]


def build_encode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float,
                     encoder: str = SW_H264, timecode: str | None = None) -> list[str]:
    """MUX argv: input 0 = our rgb24 rawvideo on stdin; input 1 = the source audio over the SAME
    source-LOCAL window (mirrors the decode, so audio stays in sync across a seam). Map video+audio,
    encode H.264 (`encoder`) + AAC, the audio padded to exactly the plan's `clip_seconds`
    (`-af apad=whole_dur=<clip>`). There is NO `-shortest`, and that is the point of E4.

    NOTHING HERE MAY CUT THE VIDEO, BECAUSE THE VIDEO'S LENGTH IS THE ONE THIS EXPORT PROMISED
    (`frame_times`, the progress bar's denominator and `RenderResult.frames` are all that count).
    Without `-shortest` ffmpeg ends the file when BOTH inputs have ended: the video when we close
    the pipe after the last composited frame, the audio when its input `-t` runs out. So each
    stream is bounded by the plan and neither can shorten the other.

    `-shortest` USED TO BE HERE, AND IT CUT THE LAST FRAME OF A RECORDING (#205, F2). A GoPro
    chapter's audio track can be a hair shorter than its video: D24 chapter 3, video 1590.005083 s
    vs audio 1589.994667 s; MK_18_09_26 chapter 2, 697.346650 s vs 697.344000 s. So a run-off
    clamped to the end of the footage asked for audio that was not there, `-shortest` let the AUDIO
    end the clip, and the muxer dropped the last video frame we had already composited and written
    (480 -> 479, 483 -> 482, 510 -> 509, 2100 -> 2099 on D24). `-af apad` papered over that by
    padding the audio with silence WITHOUT END, so that the video was always the shorter stream.

    AND THAT ENDLESS PAD OVERFLOWED AN FFMPEG QUEUE (E4, root cause found by #365). `-shortest`
    holds each stream in a sync queue until the others catch up. Behind a VideoToolbox encode whose
    opening frames arrive slowly, the audio side is let loose into that queue and never released,
    and endless silence fills it until its FIFO refuses a write (ffmpeg's debug log counted 131,071
    audio frames queued, 11,128 s of them): ffmpeg 7.1 dies with "No space left on device", exit
    228, with the disk nearly empty. Measured through the real renderer on MK_18_09_26 with the
    opening frames throttled to 3 fps: laps 3 and 13 failed at frame 32, lap 5 passed (it is a
    race). Un-throttled, no single export failed, but two 4K exports run at once failed 2 of 14
    and four at once 4 of 4 (#365). libx264 never overflowed it, even fed at 0.5 fps. #365 made the
    failure cost one libx264 re-render. Without `-shortest` no stream limits that queue, and
    ffmpeg's debug log shows it handing each audio frame on the moment it arrives: nothing piles up.

    BOUNDING THE PAD ALONE WOULD NOT HAVE HELD. With `-shortest` kept and `apad=whole_dur`, the same
    throttled start passed on a 20 s lap and on a 2,000 s window of Sandown 3h 2026, and overflowed
    at frame 53 on a 3,000 s one: the queue then fills with the export's REAL audio, and 131,072
    AAC frames is only 2,796 s of it. A whole-session export runs 45 min on MK and three hours on
    Sandown. Without `-shortest` the same 3,000 s window ran clean.

    `whole_dur` IS WHAT KEEPS THE AUDIO TO THE LAST FRAME. The audio input stops where the source's
    track stops, which at the end of a recording is a few ms before the picture; `apad=whole_dur`
    pads it back to exactly `clip_seconds` and no further, so the file's audio ends on the last
    frame. Before E4 it ended on the last WHOLE AAC frame instead: 16.320 s against a 16.333 s
    picture on MK's last chapter. Everywhere else the output is unchanged: on libx264, at 360p, MK
    lap 7 (interior) and lap 13 (across the chapter seam) came out packet-for-packet identical to
    the `-shortest` command in both streams; on MK's last chapter every video packet and the first
    764 of the old file's 766 audio packets were identical, and the new file's audio runs one
    packet further, to the last frame.

    AND `-t` IS AN INPUT OPTION, IN FRONT OF THE `-i` IT BOUNDS. Written after the input it is an
    OUTPUT option that caps the whole file, and under `-shortest` it cut the padded audio back to
    the raw window, which then dropped the last video frame of every fractional window (six of six
    D24 exports, VideoToolbox and libx264, 30 and 59.94 fps: 0060 lap 17 planned 2047, muxed 2046;
    lap 24 across a seam 2098 -> 2097). In front of the `-i` it bounds the audio INPUT at the
    plan's own `clip_seconds`, which is now also what ends the audio stream. A source with NO audio
    stream is unaffected: the `-map 1:a:0?` is optional and the filter has nothing to run on.

    An OVERLAY-ONLY spec hands straight over to `build_overlay_only_encode_cmd`, which has neither
    a source input nor an audio map — the dispatch lives here so the renderer asks one question.
    `timecode` is for that path only: a composite carries its footage, not a clock to line it by."""
    if spec.config.overlay_only:
        return build_overlay_only_encode_cmd(spec, out_w, out_h, fps, encoder, timecode)
    clip = clip_seconds(spec.t0, spec.t1, fps)
    return [
        FFMPEG, "-nostdin", "-loglevel", "error", "-y",
        # input 0: raw composited video from our pipe
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", f"{fps:.6f}",
        "-i", "pipe:0",
        # input 1: source audio, same source-LOCAL window (mirrors the decode's input + seek).
        # `-t` is an INPUT option here — before the `-i` it belongs to — and it is what ends the
        # audio stream at the plan's length (see above).
        "-ss", f"{spec.local_t0:.6f}", "-t", f"{clip:.6f}",
        *spec.source.input_args(),
        "-map", "0:v:0", "-map", "1:a:0?",
        *_video_codec_args(encoder, out_w, out_h, fps, spec.config.quality),
        "-c:a", "aac", "-b:a", "192k",
        # Padded to exactly the clip, and no `-shortest`: nothing may cut the video, and nothing
        # unbounded may enter ffmpeg's sync queue (E4).
        "-af", f"apad=whole_dur={clip:.6f}",
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


# --------------------------------------------------------------------------- what the picker may know
# The export picker has to put a size and a time on "Source (no downscale)", and for that it needs
# the footage's frame, which nothing loaded before it knows: the session is telemetry, and the
# frame lives in the video stream. One ffprobe answers it — 39-46 ms, measured three times on each
# of the four working-set recordings — and the encoder probes cost ~0.2 s each once per run. None
# of that may run on the dialog's thread, so `warm_export_probes` runs it behind it and the dialog
# reads only these caches, refreshing its hint when they fill.
#
# Keyed on the file's path, size and mtime: a recording replaced under the same name is asked
# again. A file that could not be asked is remembered as None, so nobody waits on it twice. The
# renderer never reads this cache — it probes its own source, as it always has.
_VIDEO_SIZE_CACHE: dict[tuple[str, int, int], tuple[int, int, float] | None] = {}


def _video_size_key(path: str) -> tuple[str, int, int] | None:
    try:
        st = os.stat(path)
    except (OSError, TypeError, ValueError):
        return None
    return os.path.abspath(path), st.st_size, st.st_mtime_ns


def known_video_size(path: str | None) -> tuple[int, int, float] | None:
    """The source's (width, height, fps) if `remember_video_size` has already asked; never runs
    ffprobe itself."""
    key = _video_size_key(path) if path else None
    return _VIDEO_SIZE_CACHE.get(key) if key is not None else None


def remember_video_size(path: str | None) -> tuple[int, int, float] | None:
    """`probe_video_size`, once per file, into the cache `known_video_size` reads. None when the
    file cannot be asked (missing, unreadable, no ffprobe)."""
    key = _video_size_key(path) if path else None
    if key is None:
        return None
    if key not in _VIDEO_SIZE_CACHE:
        try:
            _VIDEO_SIZE_CACHE[key] = probe_video_size(key[0])
        except (OSError, subprocess.SubprocessError, RuntimeError, ValueError):
            _VIDEO_SIZE_CACHE[key] = None
    return _VIDEO_SIZE_CACHE[key]


def known_alpha_encoder(choice: str = "auto") -> str | None:
    """`resolve_alpha_encoder(choice)` if it can answer WITHOUT probing, else None (the probe has
    not run yet)."""
    c = (choice or "auto").lower()
    if c in ("libx264", "software", "sw", "x264", "cpu"):
        return SW_PRORES
    cached = getattr(prores_videotoolbox_usable, "_cached", None)
    if cached is None:
        return None
    return VT_PRORES if cached else SW_PRORES


def export_probes_ready(src_path: str | None) -> bool:
    """True once everything the picker's hint reads has been asked: the source's frame (when there
    is a file to ask) and which ProRes encoder this machine gets."""
    key = _video_size_key(src_path) if src_path else None
    return known_alpha_encoder() is not None and (key is None or key in _VIDEO_SIZE_CACHE)


def warm_export_probes(src_path: str | None) -> threading.Thread | None:
    """Fill what `export_probes_ready` waits for on a daemon thread, so the picker never waits on
    ffprobe or a VideoToolbox session. Returns the thread, or None when nothing is left to learn."""
    if export_probes_ready(src_path):
        return None

    def warm() -> None:
        try:
            if src_path:
                remember_video_size(src_path)
            prores_videotoolbox_usable()
        except Exception:  # noqa: BLE001 — a hint that cannot be warmed is simply not refined
            _log.debug("export probe warm-up failed", exc_info=True)

    thread = threading.Thread(target=warm, daemon=True, name="export-probe-warmup")
    thread.start()
    return thread


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
      * the GLOBAL t0 is negative (a window that starts before the recording), or
      * the source-LOCAL seek time is negative (a window before the source's start), or
      * the source-LOCAL seek time lands at/after the probed source duration (with a small
        epsilon) — i.e. the seek is past the end of the file(s), which decodes nothing.

    THE TWO t0 CHECKS ARE NOT REDUNDANT, THEY FAIL DIFFERENTLY. The LOCAL one now covers both
    chaptered branches — a concat span takes `time_offset = chapters[i0].offset` like a single
    chapter, so `local_t0` is a real seek into the span and a negative one is visible (it was 0 BY
    CONSTRUCTION while the span carried an `inpoint` and seeked with `-ss 0`, which is exactly why
    the global test was added). The GLOBAL one still fires first and says the true thing: a window
    that starts before the recording is a window whose OVERLAY is wrong, not merely a seek that
    misses. It also catches a source whose `time_offset` is negative or absent, where a negative
    global t0 can still produce a non-negative local one. `lap_window_for_export` clamps the padded
    window so neither can arise from the app; this is the backstop for a caller that builds a spec
    by hand.

    The duration check is skipped silently if ffprobe couldn't read a duration (we don't block a
    render on an unreadable probe) — and it had been skipping on EVERY seam-crossing export, because
    `ffprobe -show_entries format=duration` answers "N/A" for a concat list carrying an `inpoint`.
    Declaring the per-file durations gives it a real number (3459.456054 s over the two D24 chapters
    lap 22 spans), so on the branch the guard was written for it now runs. This is a FAST,
    message-bearing failure — distinct from the no-progress watchdog, which catches a render that
    launches but then wedges."""
    if spec.duration <= 0:
        raise ValueError(
            f"export window is empty (t0={spec.t0:.3f}, t1={spec.t1:.3f}); nothing to render")
    if spec.t0 < -1e-3:
        raise ValueError(
            f"export window starts before the recording (t0={spec.t0:.3f}s < 0) — a lead-in was "
            f"not clamped to the footage; every frame would be stamped {abs(spec.t0):.3f}s early")
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


# --------------------------------------------------------------------------- output size + free space
# libx264 is CRF-driven, so unlike VideoToolbox it has no target bitrate to read off: these are
# bits per pixel per frame MEASURED on this renderer's own output, per CRF the quality presets use.
# They lived in the export dialog while the dialog was their only reader; the free-space guard
# below reads them too, so they live here, where one table serves the size the picker PROMISES and
# the size the guard REQUIRES.
X264_BPP = {20: 0.68, 23: 0.51}
X264_BPP_FALLBACK = 0.60          # an unknown CRF sits between the two measured points

# THE FLOOR THE FREE-SPACE GUARD REQUIRES, as a fraction of `estimate_output_bytes`, per codec.
# Refusing an export that would have fitted is the one failure the guard must not add, so each
# floor sits BELOW every real file measured against the estimate. Measured 2026-09-23: 41 real
# renders plus a 19-file All-laps batch of MK_18_09_26 and SD_19_09_26, both 4K 59.94 HEVC GoPro
# footage, written to $TMPDIR and sized there — best lap, median lap, a 60 s window with the kart
# PARKED (the most compressible picture the recordings hold) and MK's whole 44.7-minute session,
# at 720p / 1080p / source 4K, both presets, every shape and fit, the compare frame. File bytes
# over the central estimate:
#
#     codec          n     min     max    what moves it                   floor
#     VideoToolbox  40   0.707   0.800    nothing much: rate-controlled    0.60
#     libx264       13   0.124   0.731    the picture (CRF): 5.9x spread   0.06
#     ProRes 4444    4   0.710   1.074    resolution (1080p 1.03-1.07,     0.55
#                                          4K 0.71-0.74)
#     PNG sequence   3   0.661   1.042    resolution (1080p 1.01-1.04,     0.50
#                                          4K 0.66)
#
# VIDEOTOOLBOX lands ~0.70 of its stated target in the video stream on every picture measured —
# parked footage and a flying lap agree to the third digit (0.732 / 0.732) — plus the AAC track,
# so its floor is tight. LIBX264 is a different animal: CRF spends what the picture asks for, and
# `X264_BPP` (measured on D24's footage) is 1.4-8x above what this footage asks for; its floor is
# low because the fallback encoder's real size is simply not predictable to better than that. THE
# TWO ALPHA OUTPUTS shrink per pixel as the frame grows (the overlay's strokes scale with the short
# side, the transparent area with its square — ~short_side^-0.5 across 1080p..4K); their floors
# leave room for a 5.3K source, one step past the largest measured. The PNG figure is apparent
# bytes: each frame file also rounds up to a 4 KiB block (+1.9-2.3 KB a frame measured, up to
# +2.8 % of the estimate), which only ever makes the real usage LARGER, so the floor leaves it out.
#
# The ratios in the table are against the FLAT 1080p figure the estimate used until E5. Since E5
# `estimate_output_bytes` applies that measured ~short_side^-0.5 itself, so the 4K alpha files
# land ~1.0 of their estimate instead of ~0.7, and the same floors now sit further below them.
#
# PRORES THROUGH VIDEOTOOLBOX sits in the same band against its own central figure
# (`PRORES_VT_4444_BPP`): 1.004 and 1.051 at 1080p, 0.700 at 2160p against the flat figure (0.990
# with the short-side scaling), on the three renders measured there — so it keeps prores_ks's floor.
FREE_SPACE_FLOOR_FRACTION = {
    VT_H264: 0.60, SW_H264: 0.06, ALPHA_PRORES: 0.55, ALPHA_PNG: 0.50,
    SW_PRORES: 0.55, VT_PRORES: 0.55,
}

# RENDER THROUGHPUT, frames per second of the WHOLE pipeline (decode, composite, encode) per path,
# at a 1080p and a 2160p output frame — what the picker's time estimate is derived from, never a
# promise: the progress dialog times the real render live and its ETA is the one to trust.
# MEASURED 2026-09-25 on the M1 Pro (6P+2E) this app is developed on, through the real renderer on
# MK_18_09_26's best lap with ±5 s (4K 59.94 HEVC source, rendered at the 30 fps cap), steady-state
# rate after the first quarter — and RE-MEASURED the same day after #412 made the renderer 1.3-2x
# faster (pipelined pump, in-place paint, the 1080p decode relay), which this table had not followed:
# the picker went on quoting the old pipeline's times, up to twice what a render then took. Load
# average 4.5-12 while measuring (the render's own threads count), so an idle Mac is a little faster:
#
#     path                              1080p    2160p     (before #412)
#     h264_videotoolbox (footage)        87.8     49.5     61.2   22.8
#     libx264 (footage)                  41.9     17.6     33.7   15.4
#     prores_videotoolbox (overlay)     131.2     63.4     96.6   35.4
#     prores_ks (overlay)                58.4     16.0     56.0   15.7
#     PNG sequence (overlay)            127.5     51.8     96.1   31.5
#
# Full renders land on these rates: the owner's 2325-frame 2160p footage lap took 51.0 s end to end
# on VideoToolbox (45.6 fps, start-up included, against 49.5 steady) and his 2025-frame 1080p one
# 26.0 s. Between and beyond the two sizes the cost of a frame is taken as linear in its pixel count.
RENDER_FPS = {
    VT_H264: (87.8, 49.5), SW_H264: (41.9, 17.6),
    VT_PRORES: (131.2, 63.4), SW_PRORES: (58.4, 16.0), ALPHA_PRORES: (58.4, 16.0),
    ALPHA_PNG: (127.5, 51.8),
}
_RENDER_FPS_AT_PX = (1920 * 1080, 3840 * 2160)


def estimate_render_seconds(out_w: int, out_h: int, frames: int, codec: str) -> float | None:
    """About how long `frames` frames of `out_w x out_h` take to render on `codec`'s path, from
    `RENDER_FPS`; None for a path with no measurement or a degenerate frame. A frame's cost is
    interpolated linearly in its pixels between the two measured sizes and held to at least a
    quarter of the 1080p cost below them."""
    rates = RENDER_FPS.get(codec)
    if rates is None or frames <= 0 or out_w <= 0 or out_h <= 0:
        return None
    (px_lo, px_hi), (fps_lo, fps_hi) = _RENDER_FPS_AT_PX, rates
    cost_lo, cost_hi = 1.0 / fps_lo, 1.0 / fps_hi
    per_frame = cost_lo + (cost_hi - cost_lo) * (out_w * out_h - px_lo) / (px_hi - px_lo)
    return frames * max(per_frame, 0.25 * cost_lo)


def output_codec(config: OverlayConfig) -> str:
    """What will actually write this config's pixels: ALPHA_PNG for a sequence, the ProRes encoder
    `resolve_alpha_encoder` picks for an alpha .mov, else the H.264 encoder `resolve_encoder` picks
    — each on THIS machine (a VideoToolbox probe runs once; the answer is cached)."""
    if config.overlay_only:
        if config.alpha_codec == ALPHA_PNG:
            return ALPHA_PNG
        return resolve_alpha_encoder(config.encoder)
    return resolve_encoder(config.encoder)


def estimate_output_bytes(out_w: int, out_h: int, fps: float, seconds: float,
                          quality: str | None, codec: str) -> int:
    """About how many bytes a render of `seconds` at `out_w x out_h @ fps` writes with `codec`
    (VT_H264 / SW_H264 / ALPHA_PRORES / ALPHA_PNG). The VIDEO stream only: the composited path also
    carries ~24 KB/s of AAC, left out because it only ever makes the real file bigger.

    A CENTRAL estimate, the number the picker prints after "About". How far real files land from
    it, and why, is measured at `FREE_SPACE_FLOOR_FRACTION`. Zero for a degenerate window."""
    if not (seconds > 0) or out_w <= 0 or out_h <= 0:
        return 0
    rate = max(float(fps), 1.0)
    # The alpha outputs' figures are 1080p ones, and an overlay costs LESS per pixel on a bigger
    # frame: its strokes grow with the short side, its transparent area with the square. Measured,
    # bits/px/frame against (1080 / short side) ** 0.5: prores_ks 1.118 / 0.876 / 0.745 at
    # 720 / 1080 / 1440 (predicted 1.073 / - / 0.759), 0.881 -> 0.610 from 1080p to 2160p on MK
    # (x0.692, predicted x0.707), VideoToolbox 0.907 -> 0.632 (x0.697); PNG 0.401 / 0.311 / 0.263
    # (predicted 0.381 / - / 0.269). Without it "Source" on 4K footage promised 2.2 GB for the
    # owner's lap and wrote 1.52; with it the estimate says 1.54.
    alpha_scale = (OVERLAY_REF_SHORT_SIDE / max(min(out_w, out_h), 1)) ** 0.5
    if codec in (ALPHA_PRORES, SW_PRORES):
        bits_per_s = out_w * out_h * rate * PRORES_4444_BPP * alpha_scale
    elif codec == VT_PRORES:
        bits_per_s = out_w * out_h * rate * PRORES_VT_4444_BPP * alpha_scale
    elif codec == ALPHA_PNG:
        bits_per_s = out_w * out_h * rate * PNG_SEQUENCE_BPP * alpha_scale
    else:
        bpp, crf = quality_params(quality)
        if codec == VT_H264:
            bits_per_s = vt_target_bitrate(out_w, out_h, rate, bpp)
        else:
            bits_per_s = out_w * out_h * rate * X264_BPP.get(crf, X264_BPP_FALLBACK)
    return int(bits_per_s * seconds / 8)


class _SpecPlan(NamedTuple):
    est: int        # central estimate, bytes
    codec: str      # what writes it (VT_H264 / SW_H264 / ALPHA_PRORES / ALPHA_PNG)
    frames: int     # how many frames it writes


def _spec_plan(spec, probe) -> _SpecPlan | None:
    """What one queued render will write, or None when the spec cannot say — a duck-typed
    stand-in, an unreadable source."""
    try:
        out_w, out_h, fps = spec.output_frame(probe)
        codec = output_codec(spec.config)
        est = estimate_output_bytes(out_w, out_h, fps, clip_seconds(spec.t0, spec.t1, fps),
                                    spec.config.quality, codec)
        return _SpecPlan(est, codec, frame_count(spec.t0, spec.t1, fps))
    except Exception:  # noqa: BLE001 — an estimate that cannot be made is simply not made
        return None


def estimate_spec_bytes(spec, probe=None) -> int | None:
    """The central estimate for one queued render, from the spec and the SOURCE'S real frame
    (`probe`, default an ffprobe), or None when the spec cannot say. None contributes nothing to a
    requirement: an unknown is never a reason to refuse."""
    plan = _spec_plan(spec, probe or probe_video_size)
    return plan.est if plan is not None else None


def fmt_bytes(n: float) -> str:
    """A byte count the way the export dialog and Finder say it: decimal units, MB below a
    gigabyte, one decimal place above."""
    mb = float(n) / 1e6
    return f"{mb / 1000:.1f} GB" if mb >= 1000 else f"{mb:.0f} MB"


def _volume_capacity_for_important_usage(path: str) -> int | None:
    """What macOS itself says can be written to `path`'s volume for something the user asked for:
    the free space PLUS the purgeable space (caches, optimised iCloud copies, local snapshots) the
    system gives back on demand. It is the "available" figure Finder shows. None anywhere this
    cannot be asked (not macOS, no CoreFoundation, a volume that does not answer).

    WHY NOT `statvfs` ALONE. It counts only the blocks free right now, and on APFS that is the
    SMALLER number: measured on this Mac on 2026-09-23, 95.17 GB free by `statvfs` against
    97.64 GB for important usage — 2.46 GB of purgeable space `statvfs` does not see. The gap is
    largest exactly when the disk is nearly full, which is the only time this guard speaks, so a
    guard reading `statvfs` alone would refuse exports the system would have made room for."""
    import ctypes
    try:
        cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
        key = ctypes.c_void_p.in_dll(cf, "kCFURLVolumeAvailableCapacityForImportantUsageKey")
    except (OSError, ValueError):
        return None
    cf.CFURLCreateFromFileSystemRepresentation.restype = ctypes.c_void_p
    cf.CFURLCreateFromFileSystemRepresentation.argtypes = [
        ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_bool]
    cf.CFURLCopyResourcePropertyForKey.restype = ctypes.c_bool
    cf.CFURLCopyResourcePropertyForKey.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    cf.CFNumberGetValue.restype = ctypes.c_bool
    cf.CFNumberGetValue.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p]
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    raw = os.fsencode(path)
    url = cf.CFURLCreateFromFileSystemRepresentation(None, raw, len(raw), True)
    if not url:
        return None
    value = ctypes.c_void_p()
    try:
        if not cf.CFURLCopyResourcePropertyForKey(url, key, ctypes.byref(value), None) \
                or not value.value:
            return None
        out = ctypes.c_int64()
        ok = cf.CFNumberGetValue(value, 4, ctypes.byref(out))   # 4 = kCFNumberSInt64Type
        cf.CFRelease(value)
        return int(out.value) if ok and out.value >= 0 else None
    finally:
        cf.CFRelease(url)


def free_bytes(path: str, purgeable: bool = True) -> int | None:
    """Bytes that can be written to the volume that will hold `path` — asked of its nearest
    EXISTING parent, because the output is not written yet and neither may its folder be. The
    larger of `statvfs`'s free blocks and macOS's own capacity for important usage (see
    `_volume_capacity_for_important_usage`).

    `purgeable=False` asks for the free blocks alone. That is the question AFTER a write has
    failed: purgeable space is what macOS gives back on demand, and a write that has just been
    refused for lack of room is the proof that it had not been given back to that writer. Before
    a render (`guard_free_space`) the larger figure is right, because refusing an export the
    system would have made room for is the failure that guard must not add.

    None when the volume cannot be queried at all, and the caller must then refuse NOTHING: an
    unknown is not a full disk, and a network or removable volume that does not answer `statvfs`
    is still a perfectly good place to export to."""
    probe = os.path.abspath(path)
    while not os.path.isdir(probe):
        parent = os.path.dirname(probe)
        if parent == probe:
            return None
        probe = parent
    try:
        free = shutil.disk_usage(probe).free
    except OSError:
        free = None
    if not purgeable:
        return free
    important = _volume_capacity_for_important_usage(probe)
    known = [v for v in (free, important) if v is not None]
    return max(known) if known else None


def _reclaimable_bytes(spec, frames: int | None) -> int:
    """Bytes this render will FREE by overwriting what is already at its output, which the free
    space it needs does not have to cover.

    The save dialog asks before replacing a file, and ffmpeg's `-y` truncates it the moment the
    encode opens it, so re-exporting a lap over the previous export of it needs only the
    DIFFERENCE. Leaving this out would refuse exactly the re-export a user tries first on a full
    disk. A PNG sequence overwrites the frames of an earlier sequence that share its names
    (`overlay_000001.png` onwards), so those count and nothing else in the folder does."""
    out = getattr(spec, "out_path", "") or ""
    try:
        if getattr(spec, "is_png_sequence", False):
            if not os.path.isdir(out) or not frames:
                return 0
            prefix, suffix = _PNG_PATTERN.split("%06d")
            total = 0
            with os.scandir(out) as it:
                for entry in it:
                    name = entry.name
                    digits = name[len(prefix):-len(suffix)]
                    if (name.startswith(prefix) and name.endswith(suffix) and len(digits) == 6
                            and digits.isdigit() and 1 <= int(digits) <= frames
                            and entry.is_file(follow_symlinks=False)):
                        total += entry.stat(follow_symlinks=False).st_size
            return total
        if os.path.isfile(out) and not os.path.islink(out):
            return os.path.getsize(out)
    except OSError:
        return 0
    return 0


class InsufficientSpaceError(RuntimeError):
    """The destination plainly cannot hold this export, so it was REFUSED BEFORE A FRAME WAS
    DECODED. Its own type because of what it promises: nothing was written — so the worker must
    not "clean up a partial output", which here would be a previous export the user chose to
    replace. A disk that fills MID-render is a different failure (`is_out_of_space`), and it does
    leave a partial file to remove."""


# What `is_refused_for_space` matches on, so the dialog's sentence and the error text cannot drift
# apart (the same idiom as `_TRUNCATED_MARKER`). Deliberately none of `_NO_SPACE_MARKERS`: a
# refusal is not a disk that ran out mid-render, and the dialog says different things for the two.
_REFUSED_FOR_SPACE_MARKER = "not enough for even the smallest"


def is_refused_for_space(message: str) -> bool:
    """True when a failure is `guard_free_space`'s up-front refusal."""
    return _REFUSED_FOR_SPACE_MARKER in (message or "").casefold()


def floor_bytes(est: int, codec: str) -> int:
    """The least a render estimated at `est` bytes on `codec` can come out at: the floor below
    every real file measured (`FREE_SPACE_FLOOR_FRACTION`). What the pre-flight guard requires,
    and what the renderer holds a disk to when ffmpeg says it ran out."""
    return int(est * FREE_SPACE_FLOOR_FRACTION.get(codec, min(FREE_SPACE_FLOOR_FRACTION.values())))


def _fmt_have_and_need(free: int, need: int) -> tuple[str, str]:
    """`free` and `need` for one sentence, never rounded to the same figure: "2.1 GB free — not
    enough for 2.1 GB" is a sentence nobody can use."""
    have, floor = fmt_bytes(free), fmt_bytes(need)
    if have == floor:
        have, floor = f"{free / 1e6:,.0f} MB", f"{need / 1e6:,.0f} MB"
    return have, floor


def guard_free_space(specs, probe=None) -> None:
    """Refuse an export its destination PLAINLY cannot hold, before any frame is decoded.

    The whole QUEUE, summed: an All-laps batch writes one file per lap into one folder, and a
    check per file would let the first nineteen through and refuse the twentieth after twenty
    minutes of rendering. Specs are grouped by the folder they write into and each group is held to
    the free space of its own volume.

    WHAT IT REQUIRES IS A FLOOR, NOT THE ESTIMATE. The failure this guard must never introduce is
    refusing an export that would have fitted, so it asks only for `FREE_SPACE_FLOOR_FRACTION` of
    the central estimate — below every real file measured against it — less whatever the render
    reclaims by overwriting a previous one. Between the floor and the real size the render simply
    runs, and a disk that fills anyway is caught mid-render by `is_out_of_space`, which stays the
    backstop. `free_bytes` returning None (a volume that cannot be asked) refuses nothing.

    Raises `InsufficientSpaceError` with a sentence naming how much is needed, how much is free,
    and where."""
    cache: dict[str, tuple[int, int, float]] = {}
    ask = probe or probe_video_size

    def cached(path: str):
        if path not in cache:
            cache[path] = ask(path)
        return cache[path]

    groups: dict[str, dict] = {}
    for spec in specs:
        plan = _spec_plan(spec, cached)
        if plan is None or not plan.est:
            continue
        out = os.path.abspath(spec.out_path)
        folder = out if getattr(spec, "is_png_sequence", False) else os.path.dirname(out)
        group = groups.setdefault(folder, {"est": 0, "floor": 0.0, "reclaim": 0, "files": 0})
        group["est"] += plan.est
        group["floor"] += plan.est * FREE_SPACE_FLOOR_FRACTION.get(
            plan.codec, min(FREE_SPACE_FLOOR_FRACTION.values()))
        group["reclaim"] += _reclaimable_bytes(spec, plan.frames)
        group["files"] += 1
    for folder, group in groups.items():
        need = int(group["floor"]) - group["reclaim"]
        if need <= 0:
            continue
        free = free_bytes(folder)
        if free is None or free >= need:
            continue
        what = (f"These {group['files']} files would take about {fmt_bytes(group['est'])} "
                f"together" if group["files"] > 1
                else f"This export would take about {fmt_bytes(group['est'])}")
        have, floor = _fmt_have_and_need(free, need)
        raise InsufficientSpaceError(
            f"{what}, and the disk holding {folder} has {have} free — "
            f"{_REFUSED_FOR_SPACE_MARKER} the export could come out at ({floor}).")


# --------------------------------------------------------------------------- compositing
_c = theme.qcolor  # QColor from a theme hex token (+ optional alpha) — shared home in theme.py


def _font(px: float, bold: bool = False) -> QFont:
    """The one face every burned-in overlay element is drawn in, at an explicit PIXEL size (the
    export is a fixed-pixel canvas, so it must never scale with a screen's point DPI) and with
    TABULAR FIGURES.

    IT NEVER JOINED THE TABULAR-FIGURES FIX. This was a bare ``QFont()`` carrying zero feature
    tags, so while #196/#197 gave every column-aligning surface in the app one digit advance, the
    file the user exports kept nine (5.28..8.42 px at 13 px). ``_paint_readout`` places everything
    after the hero speed with ``x += fm_big.horizontalAdvance(speed_num)``, so the unit label and
    the Δ cue SLID 15.2 px between two 3-digit speeds at 1080p — 30.3 px at 4K — and the lap
    strip's elapsed time did the same. Numbers jittering under a fixed label is exactly what
    tabular figures exist to prevent, and here it is burned into a file that cannot be re-rendered.

    Routed through ``theme.mono_font`` rather than re-deriving the tag here, so the export follows
    the app's own fallback ORDER (Inter+tnum → Inter → the mono stack only when Inter is absent).
    The face does not move on the normal path: ``QApplication``'s font is Inter and so is
    ``mono_font``'s, so the tag is the only difference. ``bold`` stays a ``setBold`` rather than a
    weight argument, so every existing export weight is byte-identical."""
    size = max(1, int(round(px)))
    f = theme.mono_font(size)
    f.setPixelSize(size)
    f.setBold(bold)
    return f


# --------------------------------------------------------------------------- export palette
# The export palette (opaque, high-contrast colours for burning over bright footage — the live
# theme.C is dim-on-dark and washes out) lives in `export_palette.EXPORT`, imported above and
# re-exported here so `export_video.EXPORT` keeps working. It moved there because `gmeter_overlay`
# needs five of the same colours for the burned-in dial and cannot import this module (this module
# imports IT), so it carried a hand-synced second copy — see that module's docstring.
#
# Legibility comes from a dark halo under every glyph/line (see _draw_text / _stroke_polyline),
# which is what lets the g-meter and map drop their grey backdrops.

# Colour-blind-safe EXPORT semantics: the SAME vivid-for-legibility intent as EXPORT.ahead/behind,
# but on a deuteranopia-safe blue/orange hue axis (matching the interactive PALETTE_COLORBLIND). A
# colour-blind user's EXPORTED clip — the artifact they share — must not stay red/green just because
# the export keeps its own punchy palette; we swap only the hue axis, not the vividness. Blue =
# ahead/gaining, orange = behind/losing (distinct under red-green colour blindness AND greyscale).
_EXPORT_AHEAD_CB = "#2E90FF"    # ahead / gaining — vivid blue
_EXPORT_BEHIND_CB = "#FF9A1F"   # behind / losing — vivid orange


def export_semantic_pair(palette: str | None = None) -> tuple[str, str]:
    """The (ahead, behind) vivid EXPORT colour pair for `palette` (theme.active_palette() when None):
    the punchy green/red for the standard palette, the deuteranopia-safe blue/orange for the
    colour-blind palette. The ONE place the export's ahead/behind hue axis is chosen, so the burned
    overlay follows the user's colour-blind option while keeping its high-contrast look."""
    name = palette or theme.active_palette()
    if name == theme.PALETTE_COLORBLIND:
        return _EXPORT_AHEAD_CB, _EXPORT_BEHIND_CB
    return EXPORT.ahead, EXPORT.behind


def export_delta_colour(d: float | None, palette: str | None = None) -> str:
    """Export 3-way delta colour in the vivid EXPORT palette: theme.delta_colour decides the 3-way
    ahead/behind/even split (its even-dead-band stays the single source), then we map ahead/behind
    to the vivid EXPORT pair for `palette` (the active palette when None). Standard → punchy
    green/red; colour-blind → the vivid blue/orange axis, so a colour-blind user's EXPORTED clip
    follows their choice while keeping the high-contrast, footage-legible look. An exported clip has
    no live toggle, so the palette is baked in at render time (threaded via OverlayConfig.palette)."""
    sem = theme.delta_colour(d)
    if sem is None:
        return EXPORT.neutral
    ahead, behind = export_semantic_pair(palette)
    # ahead == faster == negative Δ (theme.delta_colour already applied the even dead-band).
    return ahead if d < 0 else behind


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


def _inset_width(session, lap_id: int | None, height: float, max_width: float) -> float:
    """How wide the map inset needs to be to hold `lap_id`'s trace at `height`, capped at
    `max_width`. Falls back to the cap when the trace is missing or degenerate (§6.6d).

    `lap_id=None` measures the WHOLE SESSION's trace — the full-session scope, whose inset draws
    the recording rather than a lap. Same question, one line up the same arrays."""
    try:
        if lap_id is None:
            xs, ys = session.tx, session.ty
        else:
            xs, ys = session.lap_trace_xy(lap_id)
    except Exception:  # noqa: BLE001 — an overlay must never fail an export
        return max_width
    if xs is None or ys is None or len(xs) < 2:
        return max_width
    w = float(np.ptp(np.asarray(xs, float)))
    h = float(np.ptp(np.asarray(ys, float)))
    if not (w > 0 and h > 0):
        return max_width
    return float(min(max_width, max(height * (w / h), height * 0.5)))


# --------------------------------------------------------------------------- map marker + tail
# The comet tail is a TIME span: the path driven over the last `_MAP_TAIL_S` seconds. It used to be
# `24 * k` lap points, which was 2.4 s at 1080p on the 10 Hz GPS (median sample interval 100.00 ms
# on MK_18_09_26 and SD_19_09_26 alike) and twice that at 4K, where k is 2. The stroke still scales
# with k; how much of the lap the tail shows does not.
_MAP_TAIL_S = 2.4


def trace_tail(times, xs, ys, t: float, span: float = _MAP_TAIL_S,
               gap_s: float = GAP_TIME_S) -> tuple[np.ndarray, np.ndarray]:
    """The path over `(t - span, t]`, oldest first, ENDING AT `trace_point_at(t)` — the marker — so
    the tail can only ever trail the dot it belongs to. Both ends are interpolated, so its length
    in time is `span` on every frame rather than stepping at the GPS rate. It is CUT at the last
    dropout inside the span (see `trace_point_at`), for the same reason the marker holds there.
    Empty arrays for an empty trace; a single point when the marker is all there is."""
    head = trace_point_at(times, xs, ys, t, gap_s)
    if head is None:
        return np.empty(0), np.empty(0)
    back = trace_point_at(times, xs, ys, t - span, gap_s)
    lo = int(np.searchsorted(times, t - span, side="right"))
    hi = int(np.searchsorted(times, t, side="right"))
    px = np.concatenate(([back[0]], np.asarray(xs[lo:hi], float), [head[0]]))
    py = np.concatenate(([back[1]], np.asarray(ys[lo:hi], float), [head[1]]))
    stamps = np.concatenate(([back[2]], np.asarray(times[lo:hi], float), [head[2]]))
    cut = np.nonzero(np.diff(stamps) > gap_s)[0]
    if len(cut):
        px, py = px[cut[-1] + 1:], py[cut[-1] + 1:]
    return px, py


def _ink_rect(img: QImage) -> QRect | None:
    """The smallest rect holding every pixel of the premultiplied `img` with any alpha at all, or
    None when it is fully transparent. Exact, not estimated: the blit that uses it must reach every
    inked pixel, so it is read off the image rather than derived from the geometry that drew it."""
    w, h, bpl = img.width(), img.height(), img.bytesPerLine()
    px = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
    alpha = px[:, : 4 * w].reshape(h, w, 4)[:, :, 3 if sys.byteorder == "little" else 0]
    rows = np.flatnonzero(alpha.any(axis=1))
    if not len(rows):
        return None
    cols = np.flatnonzero(alpha.any(axis=0))
    return QRect(int(cols[0]), int(rows[0]), int(cols[-1] - cols[0] + 1),
                 int(rows[-1] - rows[0] + 1))


class _MapInset:
    """Track-map inset for the export: the exported lap's racing line is projected once and baked
    into a cached RGBA layer (re-rasterizing it per frame dominated render cost); each frame blits
    the layer + draws a glowing marker with a short comet tail. A degenerate lap trace falls back to
    the full-session trace line so the inset is never empty.

    THE MARKER AND ITS TAIL ARE BOTH READ OFF THE SESSION TRACE AT THE FRAME'S TELEMETRY TIME. The
    tail used to be the LAP line's points up to `marker_index / len(session) * len(lap)` — a
    session fraction applied to a lap. For MK lap 14 of 22 that fraction sits near 0.6 all clip
    long, so the owner's 4K export drew its tail parked on the bottom-right straight, a median 241
    px from a marker that meanwhile held three frames and jumped (the nearest 10 Hz sample, drawn
    at 30 fps). Reading both from one trace at one time is what makes them agree, in the run-up,
    the run-off and the full-session scope alike, with no lap to map through."""

    def __init__(self, session, box: QRectF, lap_id: int | None, scale_k: float = 1.0):
        self._box = box
        self._k = max(0.5, float(scale_k))   # size scale (1.0 at 1080p; see OverlayPainter)
        xs = np.asarray(session.tx, dtype=float)
        ys = np.asarray(session.ty, dtype=float)
        self._ok = len(xs) >= 2 and len(ys) >= 2
        if not self._ok:
            return
        # The exported lap's own line — the ONLY line drawn. The full-session arrays are the
        # fallback line, and the trace the marker and its tail are read from.
        # Through Session's PUBLIC `lap_trace_xy` (what the map highlight reads), not the private
        # `_lap_trace_xyt` this once reached into: the old hasattr guard was vestigial — `session.tx`
        # is read unguarded four lines up. The None / <2-point fallbacks are the degenerate-lap path.
        lx = ly = None
        # `lap_id is None` is the full-session scope: there is no single racing line to highlight,
        # so the fallback below — the whole trace — IS the line, which is the right picture for a
        # clip that runs over every lap.
        got = session.lap_trace_xy(lap_id) if lap_id is not None else None
        if got is not None:
            glx, gly = got
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
        # The whole trace in FRAME pixels, projected once: the projection is affine, so
        # interpolating here is interpolating in metres. `_tt` is None for a stand-in session
        # without per-sample times, which then gets the sampled marker and no tail.
        self._px = cx_off + scale * xs
        self._py = cy_off - scale * ys
        tt = getattr(session, "tt", None)
        self._tt = (np.asarray(tt, dtype=float)
                    if tt is not None and len(tt) == len(xs) else None)
        fx, fy = (lx, ly) if lx is not None else (xs, ys)
        line_poly = QPolygonF([proj(px, py) for px, py in zip(fx, fy, strict=True)])
        # --- bake the static lap line into a cached RGBA image (no box, no full trace), sized to
        # the inset's bottom-right corner. Painted ONCE; `paint` only blits it + draws the marker.
        self._layer = self._bake_layer(box, line_poly, self._k)
        self._layer_ink = _ink_rect(self._layer)

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

    def marker_and_tail(self, tt: float | None,
                        marker_index: int | None = None) -> tuple[QPointF | None, QPolygonF]:
        """The marker's frame point and its tail polyline (ending on it) for the frame at
        TELEMETRY time `tt`. Without a time — or a trace without times — the marker falls back to
        the `marker_index` sample and there is no tail, since a tail is a span of time."""
        if tt is not None and self._tt is not None:
            tx, ty = trace_tail(self._tt, self._px, self._py, float(tt))
            if not len(tx):
                return None, QPolygonF()
            tail = QPolygonF([QPointF(float(x), float(y)) for x, y in zip(tx, ty, strict=True)])
            return QPointF(float(tx[-1]), float(ty[-1])), tail
        if marker_index is None or not (0 <= marker_index < len(self._xs)):
            return None, QPolygonF()
        return QPointF(float(self._px[marker_index]), float(self._py[marker_index])), QPolygonF()

    def paint(self, p: QPainter, tt: float | None, marker_index: int | None = None,
              opaque: bool = False) -> None:
        if not self._ok:
            return
        # Blit the baked lap line. The layer runs from the frame's origin to the inset's far
        # corner — at 4K a 33 MB image blended in full on every frame — while its ink is one
        # corner of it. Onto an OPAQUE frame (the composite's rgb24) a fully transparent pixel
        # blends to exactly the pixel under it, so the blit copies the ink's rect alone: 1.7 ms a
        # 4K frame saved, and 0 of 62 MK frames differed in a byte. Onto the overlay-only canvas
        # it is NOT the same: Qt blends a straight-alpha canvas by premultiplying and
        # un-premultiplying every pixel it visits, which rewrites the nearly transparent edge
        # pixels of the g-dial painted before it (20 of them on MK's 4K frame 0, a channel moving
        # by up to 255 at alpha ~1). That path keeps the full blit rather than change a pixel.
        if opaque and self._layer_ink is not None:
            p.drawImage(self._layer_ink.topLeft(), self._layer, self._layer_ink)
        elif not opaque:
            p.drawImage(0, 0, self._layer)
        m, tail = self.marker_and_tail(tt, marker_index)
        if m is None:
            return
        k = self._k
        # --- short comet TAIL: the last `_MAP_TAIL_S` of driving, ending on the marker, so the
        # direction of travel + recent path read at a glance.
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


# --------------------------------------------------------------------------- HUD pill geometry
# Both bottom-left readout and top-left lap strip are PILLS FITTED TO THEIR OWN INK.
#
# They used to be `max(out_w * 0.30, 260)` and `max(out_w * 0.26, 220)` — a fraction of the FRAME,
# with no relation at all to what they hold. Measured on the shipped face, the readout's content
# filled 34.8 % of its pill at 720p, 35.6 % at 1080p and 35.1 % at 2160p, and the strip's 46-47 %
# at all three: a HUD element two and a half times the size of what it says, at every resolution
# the export offers. That is what made the frame read as boxed rather than composed.
#
# The padding fractions below are exactly the padding the two painters already inset their content
# by; lifting them out of the painters is what makes the box and the text agree by construction
# rather than by coincidence. Both widths are resolved ONCE per export (OverlayPainter.__init__)
# from the widest string the export can burn, never per frame — a pill that breathed as the speed
# gained a digit would be worse than one that is too wide.
#
# THE PILLS FIT THEIR INK WITH ZERO SLACK, so "the widest string the export can burn" has to be a
# FACT, not an estimate. It is: `_burned_runs` runs the render's own per-frame lookup over the
# render's own frame times and through the painters' own run builders, so the set of strings the
# budget measures IS the set the compositor will draw. Two functions sampling the same series with
# two different conventions is what this replaced, and it had cost a pixel overflow twice over —
# see `_burned_runs`.
# The pills' LEGIBILITY FLOORS in pixels. Everything else about the overlay is a fraction, and
# these deliberately are not: below about this height the tabular digits stop resolving whatever
# the frame is, so it is an absolute of the medium rather than a proportion of the composition.
# They bind only on outputs far smaller than the smallest this app offers (0.040 * 550 px of short
# side), which in practice means the test renders — the two values differ because the two pills
# have always carried these, and unifying them would move pixels for no reason anyone asked for.
_MIN_READOUT_H_PX = 22.0
_MIN_STRIP_H_PX = 20.0

# The gap kept between two elements sharing a row once they have to be checked against each other,
# in units of the margin — the same inset the frame edge uses, so a stacked layout reads as the
# same composition rather than a different one.
_ROW_GAP_MARGINS = 1.0


def _unstack_row(left: QRectF, right: QRectF, out_w: float, margin: float,
                 downward: bool) -> QRectF:
    """Return `right`, moved off `left` if the two cannot share their row at `out_w` wide.

    The overlay is four corner-anchored elements, which is a composition exactly as long as the
    two that share an edge still fit beside each other. Three of the four widths are MEASUREMENTS
    — the pills are fitted to the widest string this export will burn, the map inset to the
    track's own bounding box — so whether they fit is not something a fraction can promise, and on
    a narrow frame (the 9:16 case, and any future one) it is where a corner layout stops being
    one. Hence a test rather than a constant, and hence no comment here claiming a size "fits".

    `downward` says which way the right-hand element escapes: the TOP row's g-meter drops below
    the strip, the BOTTOM row's map inset rises above the readout. Either way it stays pinned to
    its own edge, so the composition keeps its corners."""
    gap = _ROW_GAP_MARGINS * margin
    if left.right() + gap <= right.left():
        return right
    dy = (left.height() + gap) if downward else -(right.height() + gap)
    return QRectF(right.x(), right.y() + dy, right.width(), right.height())


_READOUT_PAD_FRAC = 0.26      # readout: left/right inner padding, as a fraction of the pill HEIGHT
_READOUT_GAP_K = 4.0          # readout: hero number -> unit label, in k units (k = height / 44)
_STRIP_PAD_L_FRAC = 0.42      # strip: the ink starts further in — the amber progress fill runs
_STRIP_PAD_R_FRAC = 0.20      #        under it, and a flush label would sit on the fill's edge
_RUN_GAP_K = 16.0             # the gap between two separate RUNS (elapsed time -> Δ), in k units

# What the strip carries INSTEAD of a Δ when the exported lap is the session's best (see
# `strip_tail`). "★" is in the shipped face — verified by rendering it through `_draw_text`, the
# export's own QPainterPath.addText path, not by asking the font.
_BEST_MARK = "★ BEST"

# ------------------------------------------------------------------ the honesty stamp
# THE MOST PUBLIC ARTIFACT THIS APP PRODUCES, AND THE LEAST ABLE TO CAVEAT ITSELF. #272 gave the
# tables that leave the app a citable quality vocabulary ([e]/[p]/[u]/[b] + a key) and named this
# module as the one genuine leaving-the-app surface still carrying nothing. Its decision was PER
# SURFACE FAMILY before per marker, and a burned frame is its own family: no key, no hover, no
# margin for a legend, watched by people who have never seen pacer. So it takes NO CODES — a `[p]`
# in the corner of a video is a letter the viewer cannot decode and the file cannot explain — and
# instead burns the app's own WORDS, under the lap strip, for the conditions that qualify something
# the frame actually shows. `data_quality.burned_timing_stamp` owns the whole decision (which
# conditions, in which order, in which words, and which two are refused and why); this file owns
# only where the ink lands.
#
# A frame with nothing to disclose burns nothing — no empty pill, no "verified" badge. The clean
# case is byte-identical to every export before this, which is the same rule the exports' quality
# key follows (a legend on a file that carries no codes teaches the reader the codes are decoration).
_STAMP_H_FRAC = 0.40      # stamp line height as a fraction of the STRIP pill's height
_STAMP_GAP_FRAC = 0.34    # gap below the strip pill before the first stamp line, ditto
_STAMP_LINE_K = 1.18      # line pitch as a multiple of the stamp font's own height


def stamp_block(strip: QRectF, lines) -> QRectF:
    """The rect the stamp's `lines` occupy under the `strip` pill (empty QRectF for no lines).

    PURE GEOMETRY, and it exists as its own function for the reason `strip_pill_width` does: the
    painter draws here and `OverlayPainter.__init__` has to know the same box to keep the corner
    composition honest. The top-left element is a strip pill PLUS whatever hangs under it, and it is
    their UNION the g-meter has to clear on a narrow frame — measuring that in two places is how a
    dropped dial ends up sitting on a disclosure."""
    if not lines:
        return QRectF(strip.x(), strip.bottom(), 0.0, 0.0)
    font = _font(strip.height() * _STAMP_H_FRAC, bold=True)
    fm = QFontMetricsF(font)
    pitch = fm.height() * _STAMP_LINE_K
    top = strip.bottom() + strip.height() * _STAMP_GAP_FRAC
    width = max(fm.horizontalAdvance(t) for t in lines)
    return QRectF(strip.x() + strip.height() * _STRIP_PAD_L_FRAC, top, width,
                  pitch * len(lines))


def _paint_stamp(p: QPainter, strip: QRectF, box: QRectF, lines) -> None:
    """Burn the honesty lines under the lap strip: the app's amber caution hue, haloed, no pill.

    NO BACKING PILL, unlike the two runs above it, and that is this module's own rule rather than a
    saving: legibility here comes from the dark halo under every glyph (see `_draw_text`), which is
    exactly what lets the g-meter and the map inset drop their grey backdrops. A third box in the
    top-left corner would make the overlay read as boxed again, which is the thing the fitted pills
    were measured into existence to undo.

    AMBER because that is the hue the app warns in and the hue this palette already reserves for
    it: the lap panel's data-quality chip is `tone="warn"`, and `EXPORT.accent` is that amber made
    opaque for bright footage. It is deliberately the same accent the progress fill uses — the
    stamp qualifies the clock that fill is running under."""
    if not lines:
        return
    font = _font(strip.height() * _STAMP_H_FRAC, bold=True)
    fm = QFontMetricsF(font)
    pitch = fm.height() * _STAMP_LINE_K
    k = strip.height() / 44.0
    for i, text in enumerate(lines):
        y = box.y() + i * pitch + fm.ascent()
        _draw_text(p, QPointF(box.x(), y), text, font, EXPORT.accent, halo=1.8 * k)


# What the strip's clock reads before the START LINE, on an export with a lead-in. Rendered through
# `fmt_time` on a non-finite time rather than typed out, because the em dash for "no value yet" is
# already this codebase's convention (`fmt_time`, `theme.speed_number`, `format_delta_value`) and a
# second spelling of it here would be a second convention. A frozen `0:00.000` was the alternative
# and reads as a broken clock; this reads as a lap that has not started, which is what it is.
_PENDING_TIME = fmt_time(float("nan"))


def readout_pill_width(pill_h: float, speed_texts, unit_label: str) -> float:
    """The bottom-left readout pill's width at pill height `pill_h`: the padding, the widest hero
    speed string, the gap, the unit label, and the padding again.

    PURE GEOMETRY — it measures the strings it is handed and decides nothing about WHICH strings
    those are. `_burned_runs` owns that, and hands it the exact set the compositor will draw (the
    em dash included, when and only when a frame draws one). It used to add
    `theme.speed_number(None, None)` here unconditionally as insurance against a set that was only
    an estimate; with an enumerated set that insurance would just be a wider pill than the export
    can fill."""
    k = pill_h / 44.0
    fm_big = QFontMetricsF(_font(pill_h * 0.74, bold=True))
    fm_unit = QFontMetricsF(_font(pill_h * 0.34, bold=True))
    hero = max((fm_big.horizontalAdvance(t) for t in speed_texts), default=0.0)
    return (2.0 * pill_h * _READOUT_PAD_FRAC + hero + _READOUT_GAP_K * k
            + fm_unit.horizontalAdvance(unit_label))


def strip_pill_width(pill_h: float, labels, tails) -> float:
    """The top-left lap strip's width at pill height `pill_h`: the left padding (which the amber
    progress fill runs under), the widest "LAP n   m:ss.mmm" the lap can produce, the run gap, the
    widest Δ (or the `★ BEST` mark), and the right padding.

    Both runs are measured in the SAME face at the SAME size the painter draws them in, so the
    box cannot disagree with the ink. Like `readout_pill_width` this is pure geometry over the
    strings `_burned_runs` enumerated; the widest label and the widest tail need not come from the
    same frame, and summing the two maxima is the conservative direction."""
    k = pill_h / 44.0
    fm = QFontMetricsF(_font(pill_h * 0.54, bold=True))
    label_w = max((fm.horizontalAdvance(t) for t in labels), default=0.0)
    tail_w = max((fm.horizontalAdvance(t) for t in tails if t), default=0.0)
    gap = _RUN_GAP_K * k if tail_w else 0.0
    return pill_h * _STRIP_PAD_L_FRAC + label_w + gap + tail_w + pill_h * _STRIP_PAD_R_FRAC


def strip_tail(delta_s: float | None, is_best: bool = False,
               palette: str | None = None) -> tuple[str, str]:
    """What follows the elapsed time in the lap strip, and the colour to draw it in.

    Best lap -> the `★ BEST` mark in the export accent, and NO Δ at all. The export defaults to
    the best lap and `delta_at_lap` compares a lap against its own curve, so today's clip burns
    `Δ +0.00` for its entire length with nothing in frame saying why. The app has copy for exactly
    this (central_view: "This IS your best lap, so it is the reference this Δ is measured
    against… Pick another lap for a number that moves") — but a video has no tooltip to hover, so
    the mark has to BE the explanation.

    Any other lap -> the shared tight Δ run in the vivid ahead/behind colour for `palette`.
    `theme.format_delta_run(units=False, arrow=False)` and `export_delta_colour` stay the
    formatter and the colour decision, shared with the live readout so the two cannot drift (that
    includes theme's `-0.00` dead band, which the export must never burn into a delivered file).
    `arrow=False` is deliberate — the accessibility ▲/▼ lives on the interactive readout."""
    if is_best:
        return _BEST_MARK, EXPORT.accent
    return (theme.format_delta_run(delta_s, units=False, arrow=False),
            export_delta_colour(delta_s, palette))


# ------------------------------------------------------------------ what each frame actually says
# The two functions below are THE decision about which strings a frame carries. Both painters call
# them, and so does the pill budget (`_burned_runs`) — which is the whole point: a budget that
# re-derives the strings from the same series under a different sampling convention is a budget
# that can disagree with the ink, and it did, twice. See `_burned_runs` for both numbers.
def _readout_runs(vals: OverlayValues, unit: str | None) -> tuple[str, str]:
    """The two runs `_paint_readout` draws for one frame: (hero speed number, unit label).
    `theme.speed_number` is the shared formatter the live #DiffBox uses (real speed only while a
    lap is current, else an em dash); `unit` converts + names it (km/h default)."""
    return theme.speed_number(vals.speed_kmh, vals.lap_id, unit), units.speed_label(unit)


def _strip_runs(session, vals: OverlayValues, lap_t0: float, is_best: bool,
                palette: str | None) -> tuple[str, str, str, float] | None:
    """The runs `_paint_strip` draws for one frame: (label, tail, tail colour, progress fraction),
    or None when there is no lap to name.

    EVERYTHING HERE IS LAP-SCOPED, so it is all gated on `vals.lap_started`: through a lead-in the
    strip names the exported lap with a pending clock, an empty fill and no tail. The elapsed time
    is clamped into [0, span] at BOTH ends — identical to the unclamped form for every frame
    between the lines, and the difference is exactly what stops a lead-out's clock overrunning the
    lap. `lap_t0` is the LAP's start, used only when the session cannot supply a lap window (with a
    lead-in the clip's own t0 would count the run-up).

    THE ELAPSED TIME IS MEASURED ON THE TELEMETRY CLOCK. `vals.t` is a frame's media time and
    `session.lap_window` answers on the true clock, so the subtraction has to happen in one of them
    — and it has to be the one the lap was TIMED on, or the burned-in clock would not reach the lap
    time at the flag. The no-window branch below keeps both terms in media time instead."""
    if vals.lap_id is None:
        return None
    win = session.lap_window(vals.lap_id)
    if win is not None:
        ls, le = win
        span = le - ls
        tt = _telemetry_time(session, vals.t)
        elapsed = 0.0 if span <= 0 else min(max(tt - ls, 0.0), span)
        frac = 0.0 if span <= 0 else elapsed / span
    else:
        frac = 0.0
        elapsed = max(0.0, vals.t - lap_t0)
    clock = fmt_time(elapsed) if vals.lap_started else _PENDING_TIME
    label = f"LAP {lap_label(vals.lap_id)}   {clock}"
    tail, colour = strip_tail(vals.delta_s, is_best, palette) if vals.lap_started else ("", "")
    return label, tail, colour, frac


def _burned_runs(session, spec: ExportSpec, fps: float) -> tuple[list[str], list[str], list[str]]:
    """Every string this export will burn: (hero speeds, strip labels, strip tails).

    THE BUDGET ASKS THE PAINTER RATHER THAN RE-DERIVING. It walks the render's own frame times
    (`frame_times`, at the fps the render resolved), reads each one through the render's own
    per-frame lookup (`overlay_values_at`, with the spec, so the lap and its clamps are the
    exported lap's), and formats through the painters' own run builders. There is no second
    sampling convention left to disagree with, which is what a pill fitted with ZERO slack needs.

    It replaced four estimators, and two of them were wrong at the edges of the window:

      * the speed budget masked `tt < spec.t1` while the per-frame lookup was
        `session.index_at_time` — then `np.searchsorted`, a CEILING. Every frame past the last
        in-window sample read the first sample AT OR AFTER `t1`, which the mask excluded: at 10 Hz
        GPS / 30 fps, the last ~2-3 frames of every clip. Constructed (99.4 km/h inside `[0, 10)`,
        142 km/h from the sample at `t = 10.0`): the budget said `('99',)` and 2 frames burned
        `142`, painting +9.57 px of ink right of the readout pill's right edge, measured on the
        composite. (The lookup is the NEAREST sample now, `timeline.nearest_sample`; the frames
        within half a GPS period of `t1` still read the sample at `t1`, so the mask would still
        miss them.)
      * the Δ budget sampled `np.linspace(lap_t0, lap_t1, 128, endpoint=False)` and so never asked
        about `lap_t1 - _LAP_CLOCK_EPS` — the exact instant a lead-out FREEZES the clock and the Δ
        on. Constructed (Δ reaching 9.9995 s at the flag): the budget fitted `Δ ±9.92` and the
        run-off held `Δ +10.00` for 300 frames — +6.59 px outside the strip pill for 10.00 s,
        drawn as an unclipped QPainterPath, so it kept its glyph and lost the dark backing that is
        the pill's entire reason to exist.

    Cost is not a reason to estimate instead: measured on a real 90.6 s D24 lap with 10 s of
    padding, all 2719 frames resolve in 28.5 ms — 0.03 % of that clip's render.

    Returns lists, not sets, so the order is the frames' order and a failure is reproducible. A
    window with no frames (only reachable by building a painter by hand — `guard_validate_window`
    refuses an empty window before the renderer builds one) still measures its first instant, so
    the pills always have a width."""
    times = frame_times(spec.t0, spec.t1, fps)
    if not len(times):
        times = np.asarray([spec.t0], dtype=float)
    speeds, labels, tails = [], [], []
    for t in times:
        vals = overlay_values_at(session, float(t), spec)
        speeds.append(_readout_runs(vals, spec.config.speed_unit)[0])
        runs = _strip_runs(session, vals, spec.lap_t0, spec.is_best_at(vals.lap_id),
                           spec.config.palette)
        if runs is not None:
            labels.append(runs[0])
            tails.append(runs[1])
    return speeds, labels, tails


def _paint_readout(p: QPainter, box: QRectF, vals: OverlayValues,
                   unit: str | None = None) -> None:
    """Bottom-left SPEED readout: a hero speed number + a small unit label ("km/h"/"mph"), haloed,
    on a slim dark pill fitted to that text (`readout_pill_width`). `unit` (km/h default) converts
    the speed number + names the unit — matching what's on screen.

    THE Δ IS NOT HERE ANY MORE. It used to sit one run right of "km/h", inside the SPEED box,
    where the only thing it could read as was a qualifier on the speed. A Δ measures TIME, so it
    now follows the elapsed time in the lap strip — same formatter, same colour rule, one section
    up. See `_paint_strip` / `strip_tail`."""
    k = box.height() / 44.0   # the readout box is ~44 px tall at 1080p; scale radii/strokes with it
    p.setBrush(_c(EXPORT.halo, 165))
    p.setPen(QPen(_c(EXPORT.text, 55), 1.0 * k))
    p.drawRoundedRect(box, 9 * k, 9 * k)
    pad = box.height() * _READOUT_PAD_FRAC
    inner = box.adjusted(pad, 0, -pad, 0)
    # --- HERO speed: big number + small unit ---
    # `_readout_runs` decides both strings, and the pill budget asks IT what this export will draw
    # — so the box the text lands in was measured on this very string.
    speed_num, unit_label = _readout_runs(vals, unit)
    big = _font(box.height() * 0.74, bold=True)
    unit_font = _font(box.height() * 0.34, bold=True)
    fm_big = QFontMetricsF(big)
    base_y = inner.y() + (inner.height() + fm_big.ascent() - fm_big.descent()) / 2.0
    x = inner.x()
    _draw_text(p, QPointF(x, base_y), speed_num, big, EXPORT.text, halo=2.4 * k)
    # Placed by ACCUMULATING ADVANCES, which is only stable because `_font` is tabular — see
    # tests/test_export_typography.py, which pins on composited pixels that everything after the
    # hero number holds still across speeds. Removing the Δ run must not change that property.
    x += fm_big.horizontalAdvance(speed_num) + _READOUT_GAP_K * k
    fm_unit = QFontMetricsF(unit_font)
    _draw_text(p, QPointF(x, base_y - (fm_big.ascent() - fm_unit.ascent()) * 0.15),
               unit_label, unit_font, EXPORT.text_dim, halo=1.8 * k)


def _paint_strip(p: QPainter, box: QRectF, session, vals: OverlayValues, t0: float,
                 palette: str | None = None, is_best: bool = False) -> None:
    """Lap/sector strip (top-left): "LAP n   m:ss.mmm" then the Δ, over a vivid amber time-progress
    fill, on the same slim dark pill as the readout (fitted to its text — `strip_pill_width`).

    THE Δ LIVES HERE, after the elapsed time, because that is the section it belongs to: it is a
    time measurement, not a speed one. `palette` picks the vivid green/red vs blue/orange hue axis;
    `is_best` replaces it with the `★ BEST` mark (see `strip_tail`, which owns both decisions).

    `_strip_runs` owns WHAT this frame says — the lap-scoped gating, the clamped clock and the
    tail — because the pill budget has to ask the same question and get the same answer. `t0` is
    the LAP's start, used only when the session cannot supply a lap window."""
    k = box.height() / 44.0
    p.setBrush(_c(EXPORT.halo, 165))
    p.setPen(QPen(_c(EXPORT.text, 55), 1.0 * k))
    p.drawRoundedRect(box, 8 * k, 8 * k)
    runs = _strip_runs(session, vals, t0, is_best, palette)
    if runs is None:
        return
    label, tail, colour, frac = runs
    font = _font(box.height() * 0.54, bold=True)
    inner = box.adjusted(box.height() * _STRIP_PAD_L_FRAC, 0,
                         -box.height() * _STRIP_PAD_R_FRAC, 0)
    # Measured before anything is drawn, because the Δ's position decides where the progress
    # fill's TRACK ends. The Δ is placed by accumulating the label's advance — the same
    # tabular-figures guarantee the readout relies on, so it cannot slide as the clock ticks.
    tail_x = inner.x() + QFontMetricsF(font).horizontalAdvance(label) + _RUN_GAP_K * k
    # THE FILL'S TRACK IS THE CLOCK'S SECTION, NOT THE WHOLE PILL. It ends midway through the gap
    # before the Δ, and that is a legibility requirement rather than a taste: measured on the
    # composited pixels, the amber fill lands at RGB(159,124,55), against which the vivid Δ
    # colours run down to 1.19:1 (standard "behind" red) and 1.21:1 (colour-blind "ahead" blue) —
    # while on the plain dark pill the same colours are 2.68:1 and 2.73:1. The Δ's whole job is to
    # carry a colour, and the bar it would sit on measures the clock beside it, so the bar stops
    # where the clock does. (The glyph SHAPE was never at risk: every run has its dark halo.)
    track_w = max(1.0, (tail_x - _RUN_GAP_K * k * 0.5) - box.x()) if tail else box.width()
    if frac > 0:
        # progress fill clipped to the pill so the rounded corners stay clean.
        clip = QPainterPath()
        clip.addRoundedRect(box, 8 * k, 8 * k)
        p.save()
        p.setClipPath(clip)
        p.setBrush(_c(EXPORT.accent, 120))
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(box.x(), box.y(), track_w * frac, box.height()))
        p.restore()
    _text_at(p, inner, Qt.AlignVCenter | Qt.AlignLeft, label, font, EXPORT.text, halo=2.2 * k)
    if not tail:
        return
    _text_at(p, QRectF(tail_x, inner.y(), max(1.0, inner.right() - tail_x), inner.height()),
             Qt.AlignVCenter | Qt.AlignLeft, tail, font, colour, halo=2.2 * k)


class OverlayPainter:
    """Composites the overlay elements onto each decoded frame. Built ONCE per export (it caches
    the static map-inset geometry + a headless g-meter `DialFilter` that it drives frame-to-frame
    with the SAME set_lap/set_g sequence the live tick uses, so the burned dial's EMA/envelope
    evolve identically). `paint_frame_with_state` mutates the passed QImage in place.

    NO QWidget IS CONSTRUCTED HERE, or anywhere else this class and `Renderer` reach: an export
    runs on `VideoExportWorker`'s QThread, and Qt widgets may only be created and destroyed on the
    GUI thread. QImage/QPixmap/QPainter are fine (paint devices, not widgets).
    tests/test_export_thread_safety.py enforces it."""

    def __init__(self, session, spec: ExportSpec, out_w: int, out_h: int, fps: float):
        self._session = session
        self._spec = spec
        self._w, self._h = out_w, out_h
        cfg = spec.config
        # The render's OWN frame rate, and it is REQUIRED: the pill budget below resolves the exact
        # strings this export will burn by walking this export's frame times, so a painter built at
        # one fps and pumped at another would be fitted to a different set of frames than it draws.
        self._fps = float(fps)
        # THE OVERLAY'S LENGTH UNIT IS THE FRAME'S SHORT SIDE (`overlay_unit`), and every size
        # below is a fraction of it. On any 16:9-or-wider landscape export the short side IS the
        # height, so this reproduces the shipped composition to the pixel; on a 9:16 or 1:1 frame
        # it is what stops a height-fractioned dial from eating half the picture.
        self._u = overlay_unit(out_w, out_h)
        # Global size scale for the export overlays: 1.0 whenever the short side is 1080, growing
        # and shrinking with it so line widths, the g-dot, the map marker + glyph outlines all look
        # right from 720p through 4K. The g-dial + map-inset paint take this `k`; the readout/strip
        # self-scale from their own box height.
        self._k = self._u / OVERLAY_REF_SHORT_SIDE
        m = cfg.margin_frac * self._u
        # g-meter: square in the TOP-RIGHT.
        gside = cfg.gmeter_frac * self._u
        self._g_rect = QRectF(out_w - m - gside, m, gside, gside)
        # map inset: BOTTOM-RIGHT, and AS WIDE AS THE TRACK NEEDS rather than a fixed 16:9 box.
        #
        # The box used to be `map_w_frac * out_w` by `map_h_frac * out_h` — 16:9 by construction,
        # the fractions being equal and the frame not square — while a kart circuit is roughly
        # square. Measured on D24 (bbox 209 x 197 m, aspect 1.06): the box came out 422x238 at
        # 1080p and the fitted track drew at 252x238, so **40% of the inset was empty at every
        # resolution**, 170 px of reserved frame at 1080p burned over the footage for nothing.
        #
        # The height fraction still sets the size; the WIDTH is whatever that height needs at the
        # track's own aspect, capped at `map_max_aspect` so a genuinely wide circuit cannot grow
        # the inset past what the composition was designed for. That cap is the OLD cap said
        # plainly: `map_w_frac * out_w` with equal fractions is the box height times the frame's
        # aspect, which on the only shape this used to render was 16:9. A degenerate/absent trace
        # falls back to the capped box.
        map_lap = None if spec.follow_laps else spec.lap_id
        mh = cfg.map_h_frac * self._u
        mw = _inset_width(session, map_lap, mh, mh * cfg.map_max_aspect)
        map_rect = QRectF(out_w - m - mw, out_h - m - mh, mw, mh)
        # Both pills are FITTED to the widest text this export WILL burn — enumerated once here by
        # replaying the render's own per-frame lookup over its own frame times, never re-derived
        # and never per frame (a pill that breathed as the speed gained a digit would be worse than
        # one that is too wide). See `_burned_runs` and the HUD pill geometry block.
        speeds, labels, tails = _burned_runs(session, spec, self._fps)
        # readout: BOTTOM-LEFT.
        rh = max(cfg.readout_h_frac * self._u, _MIN_READOUT_H_PX)
        self._readout_rect = QRectF(
            m, out_h - m - rh,
            readout_pill_width(rh, speeds, units.speed_label(cfg.speed_unit)), rh)
        # lap strip: TOP-LEFT.
        sh = max(cfg.strip_h_frac * self._u, _MIN_STRIP_H_PX)
        self._strip_rect = QRectF(m, m, strip_pill_width(sh, labels, tails), sh)
        # ...and, under it, the honesty stamp — resolved ONCE here for the same reason the pills
        # are: it is session-scoped, so a per-frame lookup could only reproduce the same answer
        # while giving it a chance to differ. [] on a clean recording, and then nothing is drawn.
        self._stamp_lines = data_quality.burned_timing_stamp(session)
        self._stamp_rect = stamp_block(self._strip_rect, self._stamp_lines)
        # AND THEN THE ROWS ARE CHECKED AGAINST THE FRAME THEY LANDED IN. Every rect above is
        # anchored to a corner, which is only a composition while the two elements sharing an edge
        # still fit side by side — and how wide they are is a MEASUREMENT (a pill fitted to its
        # own text, an inset fitted to the track), not something this file can predict. A narrow
        # frame is where that runs out, so the overlap is tested rather than assumed.
        #
        # THE TOP-LEFT ELEMENT IS THE PILL *PLUS* THE STAMP, and the g-meter has to clear their
        # UNION. Passing the pill alone would drop the dial by exactly the pill's height on a
        # narrow frame and land it on the disclosure — a caveat with a g-dial painted over it is
        # worse than no caveat, because it looks deliberate.
        self._g_rect = _unstack_row(self._strip_rect.united(self._stamp_rect), self._g_rect,
                                    out_w, m, downward=True)
        map_rect = _unstack_row(self._readout_rect, map_rect, out_w, m, downward=False)
        # The inset projects its track into ABSOLUTE frame coordinates and bakes the line once, so
        # it is built after its rect is final rather than moved afterwards.
        self._map = _MapInset(session, map_rect, map_lap, scale_k=self._k)
        # The g-meter dial's FILTERING STATE, driven exactly like the live overlay so the burned
        # dial matches the screen.
        #
        # A `DialFilter`, NOT a `GMeterOverlay`: this constructor runs on `VideoExportWorker`'s
        # QThread, and `GMeterOverlay` is a frameless translucent top-level QWidget. Creating and
        # destroying one off the GUI thread is undefined behaviour in Qt — the exact SIGSEGV shape
        # tests/test_compare_lifecycle.py exists for — and every export did it twice on the VT →
        # libx264 fallback path. The render path never wanted the widget: it only calls the free
        # `gmeter_overlay.paint_dial` with a `DialState` snapshot, which the filter provides.
        # tests/test_export_thread_safety.py holds this line to it.
        # The dial no longer carries its axis provenance (it was a 6.5 px tag the export never
        # burned in anyway; it is a sentence on the on-screen toggle's tooltip now — review §6.5),
        # so there is nothing to seed here beyond the filtering itself.
        self._dial = gmeter_overlay.DialFilter()
        # --- lap-scoped envelope bookkeeping (see feed_g / advance_and_snapshot) ---
        self._fed_before_line = False        # g was pushed while the lap was still pending
        self._crossed_line = False           # the start line has been reached
        self._lap_end_state = None           # the dial state at the last frame INSIDE the lap

    def feed_g(self, vals: OverlayValues) -> None:
        """Advance the headless g-meter dial by one tick with this frame's lap + g — the same
        order app._apply_readout feeds it (set_gmeter_lap then set_g), so the envelope resets on
        the lap boundary and the EMA dot tracks identically to the live meter.

        THE ENVELOPE HAS TO BE RESET AT THE START LINE, EXPLICITLY. `DialFilter.set_lap` resets
        it only when it is already holding a lap (`self.lap is not None`) — a deliberate rule for
        the live meter, where None means "between laps, keep what you have". In a padded export
        that rule bakes the run-up into the lap's peaks: the lead-in is fed `set_g` (the dot must
        stay live over footage the kart is plainly driving), so its g accumulates into the hull,
        and the one `set_lap` at the line then finds `self.lap is None` and skips the reset. The
        peaks the clip burns for the lap would include the braking that happened before it."""
        if vals.lap_started and not self._crossed_line:
            self._crossed_line = True
            if self._fed_before_line:
                self._dial.reset_envelope()   # drop the run-up's hull/peaks; re-seed the dot EMA
        if vals.lap_started:
            self._dial.set_lap(vals.lap_id)
        else:
            self._fed_before_line = True
        self._dial.set_g(vals.g)

    def advance_and_snapshot(self, vals: OverlayValues):
        """Advance the dial one tick (order-dependent: EMA/envelope accumulate) and return an
        immutable `DialState` snapshot.

        THE ENVELOPE PAINTED IS THE EXPORTED LAP'S, and only its: empty before the start line,
        live between the lines, and FROZEN at the finish. The dot is never gated — it is the live
        signal at this media time, and the padding exists to show live footage.

        The freeze is the same honesty argument as the reset. The dial's four cardinal numbers are
        read as "the peak g of this lap"; a 10 s run-off is where you brake hardest and turn into
        the pit lane, and letting that grow the numbers would end the clip on a peak the lap never
        produced. Frozen, the peaks are identical at every frame from the line onward whether the
        export carries 0 s of padding or 10 s."""
        self.feed_g(vals)
        st = self._dial.snapshot()
        if not vals.lap_started:
            return replace(st, hull_pts=[], peak_fwd=0.0, peak_back=0.0,
                           peak_left=0.0, peak_right=0.0)
        if vals.lap_finished and self._lap_end_state is not None:
            end = self._lap_end_state
            return replace(st, hull_pts=end.hull_pts, peak_fwd=end.peak_fwd,
                           peak_back=end.peak_back, peak_left=end.peak_left,
                           peak_right=end.peak_right)
        self._lap_end_state = st
        return st

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
        # export=True -> the vivid, no-box, big-number dial; scale_k is the dial's size against the
        # dial it was designed at, DERIVED from the same fraction that sized it rather than typed
        # as the ~280 px that fraction happens to come to at 1080p. A comment claiming a pixel
        # count "is" a fraction of something is exactly the kind that rots the first time the
        # fraction moves; this one cannot disagree with `_g_rect` because it is made of it.
        gmeter_overlay.paint_dial(
            p, self._g_rect.width(), self._g_rect.height(), dial_state, export=True,
            scale_k=self._g_rect.width() / (self._spec.config.gmeter_frac * OVERLAY_REF_SHORT_SIDE))
        p.restore()
        self._map.paint(p, vals.tt, vals.marker_index, opaque=not img.hasAlphaChannel())
        _paint_readout(p, self._readout_rect, vals, self._spec.config.speed_unit)
        # The strip's fallback elapsed origin is the LAP's start, not the clip's — with a lead-in
        # those differ, and a session with no lap window would otherwise count the run-up.
        # `is_best_at` is asked per frame because in the full-session scope the answer changes at
        # every start line; for a single-lap export it is the spec's one verdict, as before.
        _paint_strip(p, self._strip_rect, self._session, vals, self._spec.lap_t0,
                     self._spec.config.palette, self._spec.is_best_at(vals.lap_id))
        # ...and the honesty stamp under it, on EVERY frame including the lead-in and the run-off:
        # what it qualifies is the recording, not a moment in it, and a caveat that came and went
        # would be a caveat a single-frame grab or a re-cut could drop.
        _paint_stamp(p, self._strip_rect, self._stamp_rect, self._stamp_lines)
        p.end()


def _paint_packed_frame(painter: OverlayPainter, out_w: int, out_h: int, raw,
                        vals: OverlayValues, dial):
    """Composite one rgb24 frame and return it tightly PACKED at out_w*3. QImage scanlines are
    4-byte-aligned, so for a non-4-aligned out_w*3 we strip each row's trailing padding (otherwise
    every row shears + the stream desyncs).

    A BYTEARRAY IS PAINTED IN PLACE and handed back as it is — the pipelined pump's pool buffer,
    which is the whole frame's only copy. Immutable `bytes` (the serial pump's read) is copied once
    to be paintable. The two used to cost a copy in and another out: 2 x 24.9 MB a 4K frame."""
    buf = raw if isinstance(raw, bytearray) else bytearray(raw)
    img = QImage(buf, out_w, out_h, 3 * out_w, QImage.Format_RGB888)
    painter.paint_frame_with_state(img, vals, dial)
    bpl = img.bytesPerLine()
    if bpl == 3 * out_w:
        return buf                                   # already packed — no padding to strip
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * out_h).reshape(out_h, bpl)
    return arr[:, : 3 * out_w].tobytes()


def _paint_alpha_frame(painter: OverlayPainter, out_w: int, out_h: int,
                       vals: OverlayValues, dial, bgra: bool = False, buf=None):
    """Composite ONE overlay-only frame onto a fully transparent canvas and return RGBA (or, with
    `bgra`, BGRA) bytes packed at out_w*4 — the overlay with nothing under it, which is what an NLE
    wants to lay over the original footage.

    THE FORMAT FOLLOWS THE ENCODER'S PIPE (`alpha_input_pix_fmt`). ffmpeg's `rgba` rawvideo is
    byte-ordered R,G,B,A, which is `Format_RGBA8888`; Qt's `Format_ARGB32` is a host-endian 32-bit
    word, i.e. B,G,R,A on a little-endian machine — exactly the `bgra` VideoToolbox's ProRes
    encoder takes, so that path paints into it and ffmpeg converts nothing. Mixing the two up
    swaps red and blue in every exported frame and nothing about the file says so. NON-premultiplied
    either way, deliberately: premultiplied is what Qt composites in, but a half-transparent white
    halo stored premultiplied and READ as straight is a grey halo.

    There is no `raw` parameter because there is no decode; this is the whole saving the
    overlay-only path claims.

    `buf` (a bytearray of out_w*out_h*4, the pipelined pump's pool buffer) is the canvas itself
    when given: cleared, painted in place and returned, with no image to allocate and no copy out.
    A four-byte pixel is always 4-aligned, so its rows are packed exactly as `tobytes` packs them."""
    fmt = QImage.Format_ARGB32 if bgra else QImage.Format_RGBA8888
    img = QImage(buf, out_w, out_h, 4 * out_w, fmt) if buf is not None else QImage(out_w, out_h, fmt)
    img.fill(Qt.transparent)
    painter.paint_frame_with_state(img, vals, dial)
    if buf is not None:
        return buf
    bpl = img.bytesPerLine()
    arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * out_h).reshape(out_h, bpl)
    return arr[:, : 4 * out_w].tobytes()


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


# A decode that stops EARLY used to be a success as long as it had emitted at least one frame, and
# ffmpeg gives no other signal: a source truncated to half its bytes decoded 117 of 240 planned
# frames and STILL EXITED 0 (measured, synthetic clip, `[mov,mp4...] partial file` on stderr and
# rc=0). So the exit code cannot be the test and the COUNT has to be. One frame of slack is kept
# because a frame is the resolution of the plan's own arithmetic — with `clip_seconds` asking on a
# frame boundary the measured shortfall is 0 on every D24 window, and a stray boundary frame on
# footage this has not seen is not worth failing an export over. Anything larger is a clip that
# quietly ends before it should, which is the thing the user cannot see and would publish.
_TAIL_FRAME_SLACK = 1
# What `is_truncated_footage` matches on, so the plain-language dialog and the error text cannot
# drift apart (the same idiom as `_NO_SPACE_MARKERS`).
_TRUNCATED_MARKER = "the footage ran out"


class TruncatedRenderError(RuntimeError):
    """The decoder ran dry with frames still to render — the footage stopped before the window did.

    Distinct from `NoFramesError` (which is zero frames, a window that maps onto no footage at all):
    here a real clip was rendered and it is SHORTER than what was asked for, which is exactly the
    failure a "clean finish" hid."""


def is_truncated_footage(message: str) -> bool:
    """True when a render failure is the footage ending early rather than an encoder problem — the
    hook the export dialog's plain-language sentence hangs on."""
    return _TRUNCATED_MARKER in (message or "").casefold()


# ffmpeg reports a full disk through its own writer, so what arrives here is a stderr tail, not an
# errno. These are the spellings of errno 28, matched case-folded because the encoder, the muxer
# and the OS each phrase it differently.
#
# THEY ARE WHAT FFMPEG SAYS, NOT WHAT HAPPENED. ffmpeg 7.1 prints the same strerror(28) when a
# queue INSIDE ITSELF fills up. Until E4, `build_encode_cmd` paired `-shortest` with an endless
# `apad`, so the audio was silence without end, held in the `-shortest` sync queue until the video
# caught up. When the first second of video reached VideoToolbox slowly, the audio side was let
# loose and flooded that queue. The queue is a FIFO that grows only to 1 MiB of pointers, 131,072
# frames. When it is full its write returns AVERROR(ENOSPC), and ffmpeg dies with
#     [af#0:1 @ …] Error sending frames to consumers: No space left on device
# while the disk still has 94.5 GB free (measured 2026-09-23). The file written by then is under
# 1 MB. That the limit is a frame count is measured: doubling apad's frame size doubled the
# audio queued at the failure, 10,288 s -> 20,277 s.
# E4 took `-shortest` out of the mux, and with it the queue that overflowed. The words stay a
# claim all the same — ffmpeg has other internal queues, and the rule costs nothing when they are
# true — so they only decide that the DISK GETS ASKED (`Renderer._disk_full_sentence`). Only the
# disk's answer makes a failure a full disk (`is_out_of_space`).
_NO_SPACE_MARKERS = ("no space left on device", "enospc", "disk full", "not enough space")


def reports_no_space(message: str) -> bool:
    """True when an encoder's stderr tail CLAIMS the disk is out of room: errno 28, spelled out.

    A claim, not a verdict (see `_NO_SPACE_MARKERS`). What it buys is a question to the disk, and
    only the disk's answer (`is_out_of_space`) may stop the libx264 retry or tell the user their
    disk is full."""
    return any(m in (message or "").casefold() for m in _NO_SPACE_MARKERS)


# What `is_out_of_space` matches on: the first words of the sentence `DiskFullError` carries, which
# the renderer writes only AFTER the disk has been asked and was full. No ffmpeg message contains
# it, so an encoder tail alone can never pass for a full disk again.
_DISK_FULL_MARKER = "there's no room left on the disk holding"


class DiskFullError(RuntimeError):
    """An encode stopped saying there is no space, AND the disk holding its output, asked at that
    moment, cannot hold the export. Its message is a sentence for the user. It names the folder,
    the free space it measured and the least the export needs, and the encoder's own tail follows
    it for Details."""


def is_out_of_space(message: str) -> bool:
    """True when a failure is a FULL DISK: the renderer asked the disk and it had no room.

    This is the difference between one failed render and two. `run()` falls back from the hardware
    encoder to libx264 whenever a VideoToolbox encode fails — which is right for a codec/session
    problem and useless when the disk is full: the retry re-renders the WHOLE clip on the slower
    software encoder and then fails for exactly the same reason, minutes later. The user's disk
    does not get emptier while they wait.

    IT USED TO BE THE TEXT ALONE (`reports_no_space`), and the text lies: a slow VideoToolbox
    render printed ffmpeg's "No space left on device" with 91 GB free, lost its working libx264
    retry, and told the user their disk was full. Now it is keyed on `DiskFullError`'s sentence,
    which exists only when the disk agreed."""
    return _DISK_FULL_MARKER in (message or "").casefold()


def disk_full_sentence(message: str) -> str:
    """The user-facing sentence of a `DiskFullError` message, without the encoder tail that follows
    it (which belongs behind Details). The renderer writes it as the first paragraph."""
    return (message or "").strip().split("\n\n", 1)[0].strip()


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
    # Where an overlay-only file starts in its footage (None for a composite): what the finished
    # box states, and the timecode the file carries.
    sync: SourceSync | None = None


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


# --------------------------------------------------------------------------- the pipelined pump
# THE RENDER USED TO RUN ITS THREE STAGES IN SERIES on one thread: read a frame off the decoder,
# paint it, write it to the encoder, and only then ask for the next one. ffmpeg decodes and encodes
# in processes of their own, so two of the three stages never needed the painter's CPU — the loop
# just never let them overlap it. The pipelined pump gives the read and the write a thread each,
# so decoder, painter and encoder all work at once and a frame costs the SLOWEST stage, not the sum.
#
# THE PAINT STAYS ON ONE THREAD: PySide6 holds the GIL through every QPainter call (see
# `OverlayConfig.workers`). The two I/O threads overlap it only while they are in the kernel, which
# is why the decoder's stream is a SOCKET and not a pipe: a pipe hands a reader at most 64 KB per
# syscall, so a 25 MB 4K frame is ~400 returns into Python, each waiting out the painter's GIL
# switch interval. Measured on MK lap 14 (decode + paint, no encode): a reader thread on the pipe
# gave 35.0 fps at 4K against 33.4 serial; on a socket read with MSG_WAITALL — one syscall a
# frame — 48.4. A blocking pipe WRITE is already one syscall (it returns once every byte is in),
# so the encoder keeps its pipe.
#
# NOTHING HERE MAY BLOCK WITHOUT A WAY OUT. The parallel pump this module had before a6fbf39 wedged
# the GUI export forever when the encoder stopped draining its pipe. Every wait on the painting
# thread polls `abort` (the supervisor's verdict), and the supervisor's kill is what frees the two
# I/O threads: a killed decoder reads as end-of-stream, a killed encoder fails the write.
_PIPE_FRAMES = 4      # frame buffers in flight: being read, queued, being painted, being written
_PIPE_POLL_S = 0.1    # how often a blocked wait on the painting thread re-checks the abort flag
_SOCK_BUFFER = 8 << 20  # asked of both socket ends; the kernel caps it (8 MB max on macOS)


class _FramePool:
    """The render's frame buffers: `count` of them, allocated once and reused for every frame, so
    the pump's memory is `count * frame_bytes` — 4 x 24.9 MB = 99.5 MB for a 4K composite, 4 x
    33.2 MB = 132.7 MB for a 4K overlay-only — however long the clip, and no frame allocates."""

    def __init__(self, frame_bytes: int, count: int):
        self._free: queue.Queue = queue.Queue()
        bufs = [bytearray(frame_bytes) for _ in range(count)]
        self._ids = {id(b) for b in bufs}
        for b in bufs:
            self._free.put(b)

    def take(self, stop) -> bytearray | None:
        """A free buffer, or None once `stop()` says the render is over."""
        while True:
            try:
                return self._free.get(timeout=_PIPE_POLL_S)
            except queue.Empty:
                if stop():
                    return None

    def give(self, buf) -> None:
        """Return `buf` — ignored unless it is one of this pool's own (a subclass may paint into a
        frame of its own; the pool must neither grow nor adopt an object it cannot read into)."""
        if id(buf) in self._ids:
            self._free.put(buf)


class _FrameReader:
    """Reads the decoder's frames on a thread of its own into pool buffers, in stream order. `get`
    returns None at the end of the stream, which is also what a killed decoder reads as."""

    def __init__(self, read_into, pool: _FramePool, frame_bytes: int, ahead: int | None = None):
        self._read_into = read_into          # (writable memoryview) -> bytes actually read
        self._pool = pool
        self._fb = frame_bytes
        self._ready: queue.Queue = queue.Queue()   # bounded by the pool (and `ahead`, if given)
        # At most `ahead` frames read and not yet taken — what keeps one relay decoder from
        # holding every buffer of a pool it shares (see `_DecodeRelay`).
        self._credit = threading.Semaphore(ahead) if ahead else None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._pump, daemon=True, name="export-read")
        self._thread.start()

    def _pump(self) -> None:
        try:
            while True:
                if self._credit is not None:
                    while not self._credit.acquire(timeout=_PIPE_POLL_S):
                        if self._stop.is_set():
                            return
                buf = self._pool.take(self._stop.is_set)
                if buf is None:
                    return
                if self._read_into(memoryview(buf)) < self._fb:
                    self._pool.give(buf)
                    return
                self._ready.put(buf)
        except (OSError, ValueError):
            return            # the stream was closed under us (teardown): an end of stream too
        finally:
            self._ready.put(None)

    def get(self, abort, timeout: float | None = None) -> bytearray | None:
        """The next frame, or None at the end of the stream, once `abort()` is true, or after
        `timeout` seconds without one."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            try:
                buf = self._ready.get(timeout=_PIPE_POLL_S)
            except queue.Empty:
                if abort() or (deadline is not None and time.monotonic() > deadline):
                    return None
                continue
            if buf is None:
                self._ready.put(None)      # the end stays the end for any later caller
            elif self._credit is not None:
                self._credit.release()
            return buf

    def close(self, unblock) -> None:
        """Stop reading: `unblock()` must wake a read in progress (a socket shutdown does). The
        frames it had read and nobody took go back to the pool."""
        self._stop.set()
        unblock()
        self._thread.join(timeout=5.0)
        while True:
            try:
                buf = self._ready.get_nowait()
            except queue.Empty:
                return
            if buf is not None:
                self._pool.give(buf)


class _FrameWriter:
    """Writes painted frames to the encoder on a thread of its own, in the order they were put. A
    failed write is kept (`error`) and raised on the painting thread at its next `put` or `drain`,
    the same OSError the serial pump's own write would have raised. Every frame goes back to the
    pool written or not, so nothing upstream can starve waiting for a buffer."""

    def __init__(self, write, pool: _FramePool, depth: int):
        self._write = write
        self._pool = pool
        self._q: queue.Queue = queue.Queue(maxsize=depth)
        self._discard = False
        self.error: BaseException | None = None
        self._thread = threading.Thread(target=self._pump, daemon=True, name="export-write")
        self._thread.start()

    def _pump(self) -> None:
        while True:
            try:
                buf = self._q.get(timeout=_PIPE_POLL_S)
            except queue.Empty:
                if self._discard:
                    return            # a teardown that could not queue the end marker
                continue
            if buf is None:
                return
            if self.error is None and not self._discard:
                try:
                    self._write(buf)
                except (OSError, ValueError) as exc:      # BrokenPipeError is an OSError
                    self.error = exc
            self._pool.give(buf)

    def put(self, buf, abort) -> None:
        while True:
            if self.error is not None:
                self._pool.give(buf)
                raise self.error
            try:
                self._q.put(buf, timeout=_PIPE_POLL_S)
                return
            except queue.Full:
                if abort():
                    self._pool.give(buf)
                    raise BrokenPipeError("the render was aborted with frames still queued") from None

    def close(self, abort, discard: bool = False) -> None:
        """Let the thread finish what is queued (or, with `discard`, drop it) and end. Waits only
        while `abort()` is false: after an abort the killed encoder fails the write in progress."""
        self._discard = self._discard or discard
        while self._thread.is_alive():
            try:
                self._q.put(None, timeout=_PIPE_POLL_S)
                break
            except queue.Full:
                if abort() or self._discard:
                    self._discard = True
                    break
        while self._thread.is_alive():
            self._thread.join(_PIPE_POLL_S)
            if abort() or self._discard:
                break

    def drain(self, abort) -> None:
        """Wait until every frame put so far is in the encoder's pipe; raise a failed write."""
        self.close(abort)
        if self.error is not None:
            raise self.error


# --------------------------------------------------------------------------- the decode relay
# ONE VIDEOTOOLBOX SESSION IS LATENCY-BOUND, and at 1080p it is what the export waits on: ffmpeg's
# VT hwaccel hands the media engine one frame and waits for it before sending the next, so a
# second session runs largely in the first one's idle time. MK lap 14 at 1080p, 400 frames each:
# one decoder 58.3 fps; two, on the clip's two halves, 138.8 fps between them. (Two SOFTWARE
# decoders gave 72.0 -> 82.0: they share the same cores, so the relay is for the hardware decode.)
#
# So the clip is decoded as a RELAY of turns. The decoder running a turn is read live while the
# next one, started `_RELAY_LEAD` frames before its own turn, decodes ahead into the pool. Each is
# the same decode entered further in (`build_decode_cmd`'s `start_frame`), and NONE IS TRUSTED ON
# ARITHMETIC: both decoders deliver the frames around the seam and they must match byte for byte,
# on frames that all differ from one another (a still picture would match at any offset and
# prove nothing). Any doubt — a mismatch, a repeated frame, a relay decoder that ended or stalled
# — and the relay stops handing over: the decoder already running carries on to the end of the
# clip, which is exactly the one decode this module has always run. It can cost speed, never a
# pixel.
_RELAY_TURN = 120        # frames a decoder runs before the next takes over (4 s at 30 fps)
_RELAY_LEAD = 3          # frames a relay decoder starts before its turn: the seam check's first half
_RELAY_CHECK = 2         # frames past the seam both decoders still deliver: its second half
_RELAY_AHEAD = 40        # frames the next decoder may buffer: 40 x 6.2 MB = 249 MB at 1080p
_RELAY_MAX_FRAME = 1920 * 1080 * 3   # the relay's buffers are sized for frames up to 1080p
_RELAY_WAIT_S = 5.0      # how long the seam check waits on the next decoder before dropping it


class _RelayLeg:
    """One decoder of the relay: its process, its socket and a reader filling pool buffers."""

    def __init__(self, first: int, proc, sock, pool: _FramePool, frame_bytes: int):
        self.first = first            # the clip frame its first delivered frame is
        self.proc, self.sock = proc, sock
        self.reader = _FrameReader(lambda view: _recv_frame(sock, view), pool, frame_bytes,
                                   ahead=_RELAY_AHEAD)

    def stop(self) -> None:
        for step in (self.proc.kill, lambda: self.sock.shutdown(socket.SHUT_RDWR)):
            try:
                step()
            except OSError:
                pass
        self.reader.close(lambda: None)
        try:
            self.proc.wait(timeout=5)
        except Exception:  # noqa: BLE001 - a reaped/odd process must not fail the teardown
            pass
        self.sock.close()


class _DecodeRelay:
    """The composite's frames, in order, from a relay of decoders (see `_RELAY_TURN`). Drop-in for
    `_FrameReader` on the painting thread: `get` hands out one clip frame after another."""

    def __init__(self, spawn, first_proc, first_sock, n: int, pool: _FramePool,
                 frame_bytes: int):
        self._spawn = spawn           # start_frame -> (proc, sock): the same decode, entered later
        self._n, self._pool, self._fb = n, pool, frame_bytes
        self._legs = [_RelayLeg(0, first_proc, first_sock, pool, frame_bytes)]
        self._i = 0                   # clip frames handed out
        self._seam = _RELAY_TURN      # where the next leg takes over
        self._window: list[bytes] = []   # digests of the frames checked at this seam
        self.handovers = 0            # seams crossed on a relay decoder (for the tests)
        self.gave_up = False          # a seam failed its check; the running decoder finishes
        self._lock = threading.Lock()   # `procs` is read by the supervisor thread
        self._start_next()

    def procs(self) -> list:
        with self._lock:
            return [leg.proc for leg in self._legs]

    def _start_next(self) -> None:
        """Spawn the next leg, if the clip has a turn left that is worth one."""
        if self.gave_up or self._seam + _RELAY_TURN // 2 >= self._n:
            return
        proc, sock = self._spawn(self._seam - _RELAY_LEAD)
        with self._lock:
            self._legs.append(_RelayLeg(self._seam - _RELAY_LEAD, proc, sock, self._pool,
                                        self._fb))

    def _give_up(self) -> None:
        with self._lock:
            dropped = self._legs[1:]
            del self._legs[1:]
        for leg in dropped:
            leg.stop()
        self.gave_up = True

    def _check(self, buf, abort) -> bool:
        """Frame `self._i` from BOTH legs: equal, and unlike every frame checked before it."""
        other = self._legs[1].reader.get(abort, timeout=_RELAY_WAIT_S)
        if other is None:
            return False
        same = other == buf
        self._pool.give(other)
        digest = hashlib.sha1(buf).digest()
        if not same or digest in self._window:
            return False
        self._window.append(digest)
        return True

    def get(self, abort) -> bytearray | None:
        buf = self._legs[0].reader.get(abort)
        if buf is None:
            return None               # the end of the stream (or an abort): the pump decides
        if len(self._legs) > 1 and self._i >= self._legs[1].first:
            if not self._check(buf, abort):
                self._give_up()
            elif self._i == self._seam + _RELAY_CHECK - 1:
                # Every frame around the seam matched: the next leg is this decode, aligned.
                with self._lock:
                    done = self._legs.pop(0)
                done.stop()
                self.handovers += 1
                self._window.clear()
                self._seam += _RELAY_TURN
                self._start_next()
        self._i += 1
        return buf

    def close(self, unblock=None) -> None:
        with self._lock:
            legs, self._legs = self._legs, []
        for leg in legs:
            leg.stop()


def _grow_socket_buffer(sock, option: int) -> None:
    """Ask for a big socket buffer, stepping down to what the kernel allows (best effort: the
    reader's MSG_WAITALL is correct at any size; a bigger buffer only lets ffmpeg run further
    ahead of a painter that is busy)."""
    size = _SOCK_BUFFER
    while size >= 64 << 10:
        try:
            sock.setsockopt(socket.SOL_SOCKET, option, size)
            return
        except OSError:
            size //= 2


def _recv_frame(sock, view) -> int:
    """Fill `view` from the decoder's socket: MSG_WAITALL makes it one syscall a frame, made
    without the GIL. Returns the bytes read — fewer than asked only at the end of the stream."""
    want, got = len(view), 0
    while got < want:
        n = sock.recv_into(view[got:], want - got, socket.MSG_WAITALL)
        if n == 0:
            break
        got += n
    return got


def _stream_read_into(stream, view) -> int:
    """Fill `view` from a file-like stream (a stand-in decoder's): `readinto` where it has one."""
    readinto = getattr(stream, "readinto", None)
    if readinto is not None:
        return readinto(view) or 0
    data = stream.read(len(view))
    view[:len(data)] = data
    return len(data)


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
        # OVERLAY-ONLY renders no footage, but it still SIZES itself from the footage: the frame it
        # produces is meant to be laid back over that footage in an NLE, so it has to be the same
        # shape and the same rate. The probe above is the only thing the source is asked for.
        self._overlay_only = bool(spec.config.overlay_only)
        geo = self._resolve_geometry(src_w, src_h)
        self._out_w, self._out_h, self._scale_filter = geo.out_w, geo.out_h, geo.scale_filter
        self._fps = resolve_fps(spec.config, src_fps)
        # OVERLAY-ONLY: put frame 0 on a source frame the file's timecode can name, and keep the
        # plan for the encoder's `tmcd` and the finished box (`plan_source_sync`). The lap's own
        # start line does not move: the run-up absorbs the snap, at most two source frames.
        self._sync: SourceSync | None = None
        if self._overlay_only:
            self._sync = plan_source_sync(spec, probe_source_clock(spec.source.probe_path),
                                          self._fps)
            if self._sync is not None and self._sync.t0 != spec.t0:
                spec = replace(spec, t0=self._sync.t0, lead_in=spec.lap_t0 - self._sync.t0)
                self._spec = spec
        self._times = frame_times(spec.t0, spec.t1, self._fps)
        # The painter is handed the SAME fps, because its pill budget replays these very frame
        # times to learn what it will draw (see `_burned_runs`).
        self._painter = self._make_painter()
        # Resolve the encoder ONCE (probes VideoToolbox). `_encoder` is the concrete ffmpeg -c:v
        # name actually used: the H.264 one for a composite, the ProRes one for an alpha .mov,
        # "png" for a sequence.
        if not self._overlay_only:
            self._encoder = resolve_encoder(spec.config.encoder)
        elif spec.config.alpha_codec == ALPHA_PNG:
            self._encoder = "png"
        else:
            self._encoder = resolve_alpha_encoder(spec.config.encoder)
        # A failed HARDWARE encode retries once in software, on a stand-in for the SAME file:
        # h264_videotoolbox -> libx264, prores_videotoolbox -> prores_ks. Never across formats —
        # retrying an alpha export on a software H.264 would turn a failed alpha export into a
        # successful opaque one, which is worse than the failure.
        self._fallback_allowed = self._encoder in _SOFTWARE_RETRY
        self._hwaccel = resolve_hwaccel_decode(spec.config.hwaccel_decode, self._encoder)
        self._dec: subprocess.Popen | None = None
        self._enc: subprocess.Popen | None = None
        self._dec_err: _StderrDrainer | None = None
        self._enc_err: _StderrDrainer | None = None
        self._i = 0
        self._frame_bytes = self._out_w * self._out_h * 3
        self._started = False
        self._done = False
        # Frames the decoder never delivered, inside the one-frame boundary slack. The progress
        # bar's DENOMINATOR at the end, so a render that finished honestly one frame short reads as
        # finished rather than stalling at 2046 of 2047 with nothing more coming.
        self._ran_short = 0
        # --- watchdog / abort plumbing (a supervisor thread can break a wedged blocking I/O) ---
        self._watchdog_timeout = float(getattr(spec.config, "watchdog_timeout", 10.0) or 0.0)
        self._watchdog_multiple = float(getattr(spec.config, "watchdog_frame_multiple", 60.0) or 0.0)
        self._last_progress_t = 0.0            # monotonic time of the last frame written
        self._render_t0 = 0.0                  # monotonic time the pump started (the rate's origin)
        self._stall_limit_used = 0.0           # the limit that actually fired, for the message
        self._aborted: str | None = None       # set by the supervisor: "cancel" | "timeout"
        self._supervisor: threading.Thread | None = None
        self._supervisor_stop = threading.Event()
        # The progress callback `_run_chunked` is pumping with, so `run_chunk` can report the
        # frames-complete moment from inside the loop (see `_report_frames_done`).
        self._progress_cb = None
        # --- the pipelined pump (see `_FramePool`); all None on the serial one ---
        self._pipelined = self._PIPELINED and spec.config.workers != 1
        self._pool: _FramePool | None = None
        self._reader: _FrameReader | None = None
        self._writer: _FrameWriter | None = None
        self._dec_sock: socket.socket | None = None

    # A subclass whose frames do not come straight off `self._dec` one `_frame_bytes` read at a time
    # (the compare render assembles two decoders into one frame, indexed by `self._i`) sets this
    # False and keeps the serial pump, whatever `OverlayConfig.workers` says.
    _PIPELINED = True

    def _is_aborted(self) -> bool:
        return self._aborted is not None

    # ----------------------------------------------------------------- extension seams
    # The six hooks below exist for ONE subclass: `export_compare.CompareRenderer`, which renders
    # two distance-locked laps into one frame. Everything that makes this class hard to get right
    # — the stall watchdog, the cooperative cancel, the teardown order that stops a decoder from
    # blocking on a closed pipe, the VideoToolbox->libx264 retry, the out-of-space refusal — is
    # identical for one pane or two, and a second renderer that copied it would be a second place
    # for all of those to rot. What genuinely differs is five things: the frame's SHAPE, WHO paints
    # it, WHERE its pixels come from, HOW a frame is assembled, and WHICH processes the render owns
    # (plus which class a retry must respawn as). So those are the hooks and the rest is shared.
    # Each default here reproduces the single-lap behaviour exactly.

    def _resolve_geometry(self, src_w: int, src_h: int) -> FrameGeometry:
        """The OUTPUT frame + the `-vf` chain the decode is asked for. For a single-lap export the
        chain produces exactly that frame; a compare render returns the whole two-pane frame but a
        filter that produces ONE PANE, because each of its decoders fills half the picture."""
        return frame_geometry(src_w, src_h, self._spec.config)

    def _make_painter(self):
        """The object that paints the overlays onto each composited frame."""
        return OverlayPainter(self._session, self._spec, self._out_w, self._out_h, self._fps)

    def _read_source_frame(self) -> bytes | None:
        """The pixels under frame `self._i`, PACKED at `out_w*3`, or None at end-of-stream.

        None is the short-read signal `run_chunk` turns into either a clean finish or
        `NoFramesError`; the overlay-only path has no decoder and returns empty bytes forever,
        because there its frame count is `frame_times` and nothing else."""
        if self._overlay_only:
            return b""
        raw = self._dec.stdout.read(self._frame_bytes)
        return raw if raw and len(raw) == self._frame_bytes else None

    def _compose_frame(self, raw: bytes) -> bytes:
        """Frame `self._i` ready for the encoder: look its telemetry up, advance the dial, paint."""
        vals = overlay_values_at(self._session, float(self._times[self._i]), self._spec)
        dial = self._painter.advance_and_snapshot(vals)
        return self._paint_packed(raw, vals, dial)

    def _extra_procs(self) -> tuple:
        """Subprocesses this render owns BEYOND the encoder and the single decoder. The watchdog's
        kill, `cancel()` and the teardown all sweep these too, so a subclass's extra decoder can
        never outlive the render or wedge it. Here: the decode relay's other decoders."""
        relay = self._reader
        if isinstance(relay, _DecodeRelay):
            return tuple(p for p in relay.procs() if p is not self._dec)
        return ()

    def _respawn(self, spec: ExportSpec) -> Renderer:
        """A fresh renderer of THIS class for the libx264 retry — `type(self)` rather than
        `Renderer` so a compare render that trips a VideoToolbox failure retries as a compare
        render instead of silently becoming a single-pane one."""
        return type(self)(self._session, spec)

    @property
    def total_frames(self) -> int:
        return len(self._times)

    @property
    def fps(self) -> float:
        return self._fps

    def _start(self) -> None:
        # No decoder at all on the overlay-only path — that IS the path. A PNG sequence also needs
        # its directory to exist before ffmpeg writes the first frame into it.
        if self._overlay_only:
            if self._spec.is_png_sequence:
                os.makedirs(self._spec.out_path, exist_ok=True)
        else:
            cmd = build_decode_cmd(self._spec, self._out_w, self._out_h, self._fps, self._hwaccel,
                                   self._scale_filter)
            if self._pipelined:
                self._dec, self._dec_sock = self._spawn_socket_decoder(cmd)
            else:
                self._dec = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        sync = getattr(self, "_sync", None)
        self._enc = subprocess.Popen(
            build_encode_cmd(self._spec, self._out_w, self._out_h, self._fps, self._encoder,
                             timecode=sync.timecode if sync is not None else None),
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        # Drain BOTH ffmpeg stderrs off-thread so neither can ever block on a full stderr pipe while
        # the loop is busy on the decode-stdout / encode-stdin pipes (deadlock guard).
        # (getattr-guarded so a mock Popen without a stderr attribute is simply not drained.)
        dec_se = getattr(self._dec, "stderr", None)
        enc_se = getattr(self._enc, "stderr", None)
        self._dec_err = _StderrDrainer(dec_se) if dec_se is not None else None
        self._enc_err = _StderrDrainer(enc_se) if enc_se is not None else None
        if self._pipelined:
            self._start_pipeline()
        self._started = True

    def _spawn_socket_decoder(self, cmd: list[str], stderr=subprocess.PIPE):
        """(process, our socket end): the decoder, writing its rgb24 frames into one end of a
        socketpair (see `_FramePool` for why a socket). ffmpeg's `pipe:1` is a plain write() to fd
        1, which a socket takes as it takes a pipe — and a killed decoder closes it the same way,
        reading as end-of-stream."""
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        _grow_socket_buffer(ours, socket.SO_RCVBUF)
        _grow_socket_buffer(theirs, socket.SO_SNDBUF)
        try:
            proc = subprocess.Popen(cmd, stdout=theirs.fileno(), stderr=stderr)
        except BaseException:
            ours.close()
            raise
        finally:
            theirs.close()
        return proc, ours

    def _spawn_relay_leg(self, start_frame: int):
        """A relay decoder: this render's decode, entered `start_frame` frames in. Its stderr is
        not kept — any failure of it only ends the relay (see `_DecodeRelay`)."""
        cmd = build_decode_cmd(self._spec, self._out_w, self._out_h, self._fps, self._hwaccel,
                               self._scale_filter, start_frame=start_frame)
        return self._spawn_socket_decoder(cmd, stderr=subprocess.DEVNULL)

    def _relay_ok(self) -> bool:
        """Whether the decode runs as a relay (`_DecodeRelay`): the HARDWARE decode of a composite
        (the one a second session speeds up), at a whole-number rate whose turn and lead are whole
        microseconds (the relay's seek is then exact), with frames up to 1080p (what its buffers
        are sized for) and at least two turns to share."""
        fps = round(self._fps)
        return (bool(self._hwaccel) and not self._overlay_only and fps > 0
                and abs(self._fps - fps) < 1e-9
                and (_RELAY_TURN * 1_000_000) % fps == 0
                and (_RELAY_LEAD * 1_000_000) % fps == 0
                and self._frame_bytes <= _RELAY_MAX_FRAME
                and len(self._times) >= 2 * _RELAY_TURN)

    def _start_pipeline(self) -> None:
        """The buffer pool and the two I/O threads around the painting thread."""
        assert self._enc is not None and self._enc.stdin is not None
        frame_bytes = self._out_w * self._out_h * (4 if self._overlay_only else 3)
        relay = self._pipelined and not self._overlay_only and self._relay_ok()
        # The relay's next decoder buffers up to `_RELAY_AHEAD` frames of its own on top of the
        # pump's; two more keep the running decoder from ever waiting on it for a buffer.
        self._pool = _FramePool(frame_bytes,
                                _PIPE_FRAMES + (_RELAY_AHEAD + 2 if relay else 0))
        # The encoder is looked up per write, as the serial pump's own `self._enc.stdin.write`
        # does, so whatever process `self._enc` is — the one the supervisor kills — is the one fed.
        self._writer = _FrameWriter(lambda buf: self._enc.stdin.write(buf), self._pool, depth=1)
        if self._overlay_only:
            return
        # A real Popen handed our socket exposes no stdout of its own; a stand-in process (the
        # tests' doubles) serves its frames from one, and is read from there.
        stdout = getattr(self._dec, "stdout", None)
        if stdout is not None:
            read = lambda view: _stream_read_into(stdout, view)  # noqa: E731
        elif relay:
            self._reader = _DecodeRelay(self._spawn_relay_leg, self._dec, self._dec_sock,
                                        len(self._times), self._pool, self._frame_bytes)
            return
        else:
            sock = self._dec_sock
            read = lambda view: _recv_frame(sock, view)  # noqa: E731
        self._reader = _FrameReader(read, self._pool, self._frame_bytes)

    def _stop_pipeline(self, drain: bool) -> None:
        """End the I/O threads: with `drain`, every frame already painted reaches the encoder
        first; without it (cancel / a failure), whatever is queued is dropped. Idempotent.

        The drain is BOUNDED, like the encoder reap in `_finish`: the pump flushes while the
        supervisor still watches (`_flush_frames`), so a wedge there becomes RenderTimeoutError;
        this is the teardown's own backstop, reached with the supervisor already gone."""
        writer, self._writer = self._writer, None
        if writer is not None:
            deadline = time.monotonic() + 30.0
            writer.close(lambda: self._is_aborted() or time.monotonic() > deadline,
                         discard=not drain)
        reader, self._reader = self._reader, None
        sock, self._dec_sock = self._dec_sock, None

        def unblock() -> None:
            if sock is not None:
                try:
                    sock.shutdown(socket.SHUT_RDWR)   # wakes a recv in progress; close would not
                except OSError:
                    pass

        if isinstance(reader, _DecodeRelay):
            _log.info("decode relay: %d of the clip's seams handed over%s", reader.handovers,
                      "; a seam check failed, the running decoder finished the clip"
                      if reader.gave_up else "")
        if reader is not None:
            reader.close(unblock)
        else:
            unblock()
        if sock is not None:
            sock.close()

    @property
    def encoder(self) -> str:
        """The concrete ffmpeg video encoder this render resolved to (h264_videotoolbox or
        libx264). Useful for tests / a status line that wants to report the GPU offload."""
        return self._encoder

    def run_chunk(self, n: int = 24) -> bool:
        """The pump: composite up to `n` frames in order (read frame -> paint -> write to encoder).
        Returns True when complete. THE render engine; `run()` pumps it under the watchdog. On the
        pipelined pump the read and the write happen on their own threads (see `_FramePool`) and
        this thread paints; frames still leave in order, and the code below reads the same either
        way. A short read = clean end (or NoFramesError); a supervisor kill turns the blocked
        read/write into the right typed exception (RenderTimeoutError / CancelledError /
        _EncodeError) so the render never hangs."""
        if self._done:
            return True
        if not self._started:
            self._start()
        assert self._enc is not None
        assert self._enc.stdin is not None
        assert self._dec is not None or self._overlay_only
        for _ in range(n):
            if self._i >= len(self._times):
                try:
                    self._flush_frames()        # pipelined: the queued frames reach the encoder
                except (BrokenPipeError, OSError):
                    self._encode_pipe_broke()
                # EVERY FRAME IS WRITTEN, AND THE FILE IS NOT FINISHED. `_finish` closes the
                # encoder's stdin and waits for ffmpeg to write the trailer, which is 1.6-5.7 s of
                # real work on D24 (12.1 % of one 2160p export's wall clock) with nothing left to
                # count. Reporting the completed count HERE is what lets the dialog say so: the bar
                # reaches its own maximum exactly when the last frame is written, and the caption
                # switches to naming the write. Without this the last report is whatever the final
                # CHUNK boundary happened to be — 2016 of 2047 on a real lap, because 2047 is not a
                # multiple of 48 — so the bar froze just short of the end for the whole mux.
                self._report_frames_done()
                self._finish()
                return True
            raw = self._next_source_frame()
            if raw is None:
                # A short read means ONE of four things:
                #   * the supervisor killed the decoder on a stall/cancel -> abort loudly;
                #   * the decoder emitted ZERO frames (an empty/past-EOF seek) -> that's not a
                #     success, it's the chaptered-export failure: surface NoFramesError so the user
                #     gets a clear message instead of an empty clip + a dialog that never moved;
                #   * it stopped a FRAME short of the plan -> the boundary slack (see
                #     _TAIL_FRAME_SLACK); finish, and let the bar finish at the count that was
                #     really written rather than sitting one frame below its own total;
                #   * it stopped further short than that -> the footage ran out mid-clip. THAT USED
                #     TO BE A SUCCESS. `produced > 0` was the whole test, so a source truncated to
                #     half its bytes exported half a lap, reported "export finished", and left a
                #     bar stopped at 117 of 240 as the only sign.
                #
                # An overlay-only render has no decoder and therefore no short read: its frame
                # count is `frame_times` and nothing else, so the loop simply runs to it.
                self._raise_if_aborted()
                try:
                    self._flush_frames()    # what was painted is written, as on the serial pump
                except (BrokenPipeError, OSError):
                    self._encode_pipe_broke()
                produced = self._i
                planned = len(self._times)
                self._finish()
                if produced == 0:
                    raise NoFramesError(
                        "the video export produced no frames — the source/lap window may be "
                        "invalid (it does not map onto any footage). Nothing was written.")
                if planned - produced > _TAIL_FRAME_SLACK:
                    raise TruncatedRenderError(
                        f"{_TRUNCATED_MARKER} {produced / self._fps:.3f} s into a "
                        f"{planned / self._fps:.3f} s clip ({produced} of {planned} frames) — the "
                        "recording may be damaged, or still being copied from the camera.")
                self._ran_short = planned - produced
                return True
            try:
                self._emit(raw, self._compose_frame(raw))
            except (BrokenPipeError, OSError):
                self._encode_pipe_broke()
            self._i += 1
            self._last_progress_t = time.monotonic()   # fed the watchdog: a frame made it out
        return False

    def _next_source_frame(self):
        """`_read_source_frame`, or on the pipelined pump the reader thread's next frame (a pool
        buffer the painter may paint in place), or None at the end of the stream."""
        if self._reader is not None:
            return self._reader.get(self._is_aborted)
        return self._read_source_frame()

    def _emit(self, raw, frame) -> None:
        """Hand the painted `frame` to the encoder: written here on the serial pump, queued for the
        writer thread on the pipelined one (which blocks only while `_PIPE_FRAMES` are in flight).
        A frame painted into a buffer of its own leaves the source buffer free for the reader."""
        if self._writer is None:
            self._enc.stdin.write(frame)
            return
        if frame is not raw and self._pool is not None:
            self._pool.give(raw)
        self._writer.put(frame, self._is_aborted)

    def _flush_frames(self) -> None:
        """Block until every frame painted so far is in the encoder's pipe (a no-op on the serial
        pump, where each one already is). Raises the writer's OSError if a write failed."""
        if self._writer is not None:
            self._writer.drain(self._is_aborted)

    def _encode_pipe_broke(self):
        """The encoder stopped accepting input. Distinguish a watchdog/cancel kill (the supervisor
        broke the pipe to escape a wedge) from a genuine encoder failure."""
        self._raise_if_aborted()
        self._finish()              # reap; its non-zero-exit branch raises _EncodeError
        raise _EncodeError(self._encoder, "ffmpeg encode pipe broke") from None

    def _raise_if_aborted(self) -> None:
        """If the supervisor aborted the render (stall watchdog or cancel), raise the matching
        exception so a killed-pipe read/write becomes a clear, typed failure instead of a silent
        early finish or a bare BrokenPipeError."""
        if self._aborted == "timeout":
            # The limit the supervisor actually applied, not the floor it was configured with — the
            # two differ whenever the render's own pace raised it (see `_stall_limit`).
            limit = self._stall_limit_used or self._watchdog_timeout
            raise RenderTimeoutError(
                f"video export stalled: no frame written for {limit:.0f}s "
                f"(encoder={self._encoder}) — the render was aborted to avoid hanging")
        if self._aborted == "cancel":
            raise CancelledError("export cancelled")

    def _paint_packed(self, raw: bytes, vals: OverlayValues, dial) -> bytes:
        """Paint the overlays for one decoded rgb24 frame (`raw`, PACKED at out_w*3) from a
        precomputed dial snapshot, and return the painted bytes PACKED at out_w*3 for the encoder.

        On the overlay-only path there is no `raw` and the frame goes out as RGBA over
        transparency instead — same painter, same per-frame values, four bytes a pixel. On the
        pipelined pump that canvas is a pool buffer, painted in place and written from there."""
        if self._overlay_only:
            buf = None
            if self._pool is not None:
                buf = self._pool.take(self._is_aborted)
                if buf is None:
                    self._raise_if_aborted()
            return _paint_alpha_frame(self._painter, self._out_w, self._out_h, vals, dial,
                                      bgra=alpha_input_pix_fmt(self._encoder) == "bgra", buf=buf)
        return _paint_packed_frame(self._painter, self._out_w, self._out_h, raw, vals, dial)

    def _report_frames_done(self) -> None:
        """Tell the caller every planned frame is written, before the encoder is finalized."""
        if self._progress_cb is not None:
            self._progress_cb(self._i, len(self._times))

    def _stall_limit(self) -> float:
        """How long this render may go without writing a frame before it is presumed wedged.

        The configured `watchdog_timeout` is a FLOOR, and the limit also scales with the render's
        own mean cost per frame so far — so the guard is derived from the work in front of it
        rather than from a constant that has to be right for a 1080p hardware encode and a
        loaded-machine 4K software one at the same time. See `OverlayConfig.watchdog_timeout` for
        the measurements both numbers come from.

        Zero disables the stall check entirely (cancel still works). Before the first frame there
        is no rate to measure, so the floor stands — which is what must cover the ffmpeg spawn and
        the seek (worst measured: 0.674 s)."""
        floor = self._watchdog_timeout
        if floor <= 0 or self._watchdog_multiple <= 0:
            return floor
        if self._i <= 0 or self._render_t0 <= 0:
            return floor
        per_frame = (self._last_progress_t - self._render_t0) / self._i
        return max(floor, self._watchdog_multiple * per_frame)

    def _start_supervisor(self, cancel) -> None:
        """Daemon supervisor: polls every 0.5s; aborts "cancel" if `cancel()` returns True, or
        "timeout" if no frame for `_stall_limit()` s. Kills ffmpeg so the blocked pipe I/O returns;
        `_raise_if_aborted` then raises the typed error. A zero/none timeout disables only the stall
        check (cancel still works)."""
        if self._supervisor is not None:
            return
        self._last_progress_t = self._render_t0 = time.monotonic()
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
                limit = self._stall_limit()
                if limit > 0 and not self._done:
                    if time.monotonic() - self._last_progress_t > limit:
                        self._stall_limit_used = limit
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
        for proc in (self._enc, self._dec, *self._extra_procs()):
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
        """Render to completion. `progress(done, total)` / `cancel()` callbacks. A VideoToolbox
        encode (H.264 or ProRes) that fails (`_EncodeError`) or wedges (RenderTimeoutError) retries
        ONCE on a fresh software-forced Renderer (`_SOFTWARE_RETRY`); otherwise surfaces a clear
        RuntimeError. Returns a RenderResult."""
        try:
            return self._run_chunked(progress, cancel, chunk)
        except (_EncodeError, RenderTimeoutError) as exc:
            is_encode_fail = isinstance(exc, _EncodeError) and exc.encoder == self._encoder
            is_vt_wedge = isinstance(exc, RenderTimeoutError)
            # NEVER fall back on a full disk: the software retry is a whole second render that
            # cannot succeed. Surface it now, while the failure is still cheap. But a full disk is
            # what the DISK says, not what ffmpeg's text says (see `_NO_SPACE_MARKERS`).
            self._raise_if_disk_full(exc)
            if not (self._fallback_allowed and (is_encode_fail or is_vt_wedge)):
                # Not a VT-recoverable case → surface a clear error (never a hang).
                raise RuntimeError(str(exc)) from exc
            # VideoToolbox failed OR wedged → retry once in software with an identical spec.
            _log.warning("%s failed (%s); retrying once on %s", self._encoder,
                         str(exc).splitlines()[0][:200], _SOFTWARE_RETRY[self._encoder])
            self.cancel()
            sw_cfg = replace(self._spec.config, encoder=_SOFTWARE_RETRY[self._encoder])
            sw_spec = replace(self._spec, config=sw_cfg)
            retry = self._respawn(sw_spec)
            try:
                return retry._run_chunked(progress, cancel, chunk)
            except _EncodeError as exc2:
                # The retry is the LAST attempt, so a disk that really filled during it still
                # has to be named as one. The same question, asked about the retry's own file.
                retry._raise_if_disk_full(exc2)
                raise

    def _raise_if_disk_full(self, exc: BaseException) -> None:
        """Raise `DiskFullError` when `exc` says there was no space AND the disk agrees.

        Otherwise return, and let the caller treat the failure like any other encoder failure: the
        VideoToolbox one retries on libx264, and nothing downstream may call it a full disk. That
        includes a volume that cannot be asked. An unknown is not a full disk, which is the rule
        the pre-flight guard keeps too, and it costs at most one retry that fails the same way."""
        if not reports_no_space(str(exc)):
            return
        sentence = self._disk_full_sentence()
        if sentence is None:
            _log.warning("the encoder reported no space left, but the disk holding %s has room "
                         "or cannot be asked; not treating it as a full disk", self._spec.out_path)
            return
        self.cancel()
        raise DiskFullError(f"{sentence}\n\n{exc}") from exc

    def _disk_full_sentence(self) -> str | None:
        """The sentence for a FULL disk, or None when the disk holding the output has room for this
        export, or cannot be asked.

        Asked at the moment of failure, before the worker removes the partial file. It uses the
        free blocks alone (`free_bytes(purgeable=False)`): the write that just failed shows that
        purgeable space had not been freed for it. "Room" means room for the least this whole
        export can come out at (`floor_bytes`), not for its central estimate.
        - Genuinely full: the free space is about zero, less than any export needs.
        - ffmpeg's false alarm: the free space is what the pre-flight guard already accepted,
          minus the under-a-megabyte partial file.
        - The partial file is not credited back. It is what the disk DID hold before it ran out,
          and a retry that truncated it would need all of that space again and more."""
        spec = self._spec
        out = os.path.abspath(spec.out_path)
        png = bool(getattr(spec, "is_png_sequence", False))
        folder = out if png else os.path.dirname(out)
        free = free_bytes(folder, purgeable=False)
        if free is None:
            return None
        # The encoder names the codec on every path — h264_videotoolbox / libx264, the ProRes
        # encoder (whose files differ in size), or "png" — except that a sequence says ALPHA_PNG.
        codec = ALPHA_PNG if (self._overlay_only and png) else self._encoder
        est = estimate_output_bytes(self._out_w, self._out_h, self._fps,
                                    clip_seconds(spec.t0, spec.t1, self._fps),
                                    spec.config.quality, codec)
        need = floor_bytes(est, codec)
        if free >= need:
            return None
        have, least = _fmt_have_and_need(free, need)
        return (f"There's no room left on the disk holding {folder}: it had {have} free when the "
                f"export stopped, and this export needs at least {least}.")

    def _run_chunked(self, progress, cancel, chunk: int) -> RenderResult:
        """Pump `run_chunk` to completion under the supervisor (watchdog + cancel). Single-threaded:
        decode → paint → encode in series, one chunk at a time, with progress reported after each."""
        self._progress_cb = progress
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
                # The FINAL report counts what was written. Mid-render the denominator is the plan
                # (that is what a bar is for); at the end, a render that stopped inside the
                # one-frame slack would otherwise leave the bar parked below its own total forever.
                progress(self._i, len(self._times) - self._ran_short)
        except (CancelledError, _EncodeError, RenderTimeoutError):
            self.cancel()
            raise
        except Exception:
            self.cancel()
            raise
        finally:
            self._stop_supervisor()
        return RenderResult(self._spec.out_path, self._i, self._out_w, self._out_h,
                            self._fps, self._spec.duration, getattr(self, "_sync", None))

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
        # Pipelined: every frame already painted reaches the encoder before its stdin closes, and
        # the reader stops (its socket shutdown is what a decoder still mid-stream reads as EOF).
        self._stop_pipeline(drain=True)
        enc = self._enc
        decs = [p for p in (self._dec, *self._extra_procs()) if p is not None]
        # Stop reading the decoders so they unblock and exit (one may still be mid-stream at our
        # tail — a compare render's two panes almost never run out on the same frame).
        for dec in decs:
            if dec.stdout is not None:
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
        for dec in decs:
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
        for proc in (self._enc, self._dec, *self._extra_procs()):
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
        # After the kill, so neither I/O thread can still be blocked on a live process.
        self._stop_pipeline(drain=False)
        for drainer in (self._enc_err, self._dec_err):
            if drainer is not None:
                drainer.join(timeout=1.0)


def build_lap_spec(session, out_path: str, lap_id: int,
                   config: OverlayConfig | None = None,
                   src_path: str | None = None,
                   lead_in: float = 0.0, lead_out: float = 0.0) -> ExportSpec:
    """Build the `ExportSpec` for `lap_id`, resolving the VIDEO SOURCE from the session's chapters.
    Raises ValueError if the lap has no usable window. Source:
      * `session.chapters` (a ChapterMap) present -> resolve_video_source (single chapter or a concat
        over a seam-crossing span);
      * no ChapterMap -> the plain single file (`src_path`, else `session.video_path`), offset 0.
    The caller OWNS the returned spec's `source` and must call `spec.source.cleanup()` when done.

    It also resolves the BEST-LAP VERDICT (`ExportSpec.is_best`). The exporter had never once
    called `session.best_lap_id()`, and the app's video export defaults to the best lap — so the
    shared MP4 burned `Δ +0.00` across its whole length, against a baseline that was the lap
    itself, with nothing in frame saying so. Resolved here, at the one place a lap becomes an
    export, rather than at paint time: a per-frame `best_lap_id()` would be the same answer a
    thousand times over. A session that does not expose the accessor (the duck-typed ones the
    tests use) simply gets `False` — the Δ, exactly as before.

    `lead_in`/`lead_out` widen the RENDERED window by that many seconds of run-up and run-off. They
    are applied here, in `lap_window_for_export`, BEFORE the source is resolved — a lead-in that
    reaches back over a chapter seam has to be part of the window the concat span is chosen from.
    What lands on the spec is what the footage could actually give (the funnel clamps at the
    recording's edges), which is why the applied amounts are re-derived from the two windows rather
    than copied from the arguments."""
    lap_win = lap_window_for_export(session, lap_id)
    if lap_win is None:
        raise ValueError(f"lap {lap_id} has no usable export window")
    lap_t0, lap_t1 = lap_win
    t0, t1 = lap_window_for_export(session, lap_id, lead_in, lead_out)   # never None here
    chapter_map = getattr(session, "chapters", None)
    if chapter_map is not None and getattr(chapter_map, "chapters", None):
        source = resolve_video_source(chapter_map, t0, t1)
    else:
        path = src_path or getattr(session, "video_path", None)
        if not path:
            raise ValueError("session has no video source to export")
        source = single_file_source(path)
    best_id = _best_lap_id(session)
    return ExportSpec(out_path=out_path, lap_id=lap_id, t0=t0, t1=t1,
                      source=source, config=config or OverlayConfig(),
                      is_best=best_id is not None and int(best_id) == int(lap_id),
                      best_lap_id=best_id,
                      lead_in=lap_t0 - t0, lead_out=t1 - lap_t1)


def _best_lap_id(session) -> int | None:
    """The session's best lap, or None when the session cannot say (the duck-typed sessions the
    tests build). One accessor, guarded once, because three spec builders now want it."""
    best = getattr(session, "best_lap_id", None)
    try:
        return best() if callable(best) else None
    except Exception:  # noqa: BLE001 — a missing best lap must never fail an export
        return None


def _video_source_for(session, t0: float, t1: float, src_path: str | None):
    """Resolve the GLOBAL window to a VideoSource the way every scope needs it: through the
    session's ChapterMap when it has one, else the plain single file. Lifted out of
    `build_lap_spec` when the session scope needed exactly the same three lines."""
    chapter_map = getattr(session, "chapters", None)
    if chapter_map is not None and getattr(chapter_map, "chapters", None):
        return resolve_video_source(chapter_map, t0, t1)
    path = src_path or getattr(session, "video_path", None)
    if not path:
        raise ValueError("session has no video source to export")
    return single_file_source(path)


# --------------------------------------------------------------------------- export scope
def build_session_spec(session, out_path: str,
                       config: OverlayConfig | None = None,
                       src_path: str | None = None) -> ExportSpec:
    """Build the `ExportSpec` for the WHOLE RECORDING — every lap, the out-lap, the pit time and
    the cool-down, as one file whose overlay follows the laps (`follow_laps`).

    The window is the FOOTAGE's, not the telemetry's: `footage_duration` reads the chapter table's
    own cumulative duration, while `session.tt[-1]` has had `load._clean` take the stationary
    lead-in and the cool-down off it. Bounding a full-session export by the trace would silently
    drop real footage off both ends of a clip whose entire promise is that it is the whole thing.

    There is no padding argument, and that is not an omission: padding exists to give a LAP room
    at the timing line, and this window already runs from the first frame to the last.

    Raises ValueError when the session cannot state a footage duration (a duck-typed or
    single-file session with no ChapterMap) — a full-session render needs an end, and guessing one
    from the telemetry is the mistake above."""
    total = footage_duration(session)
    if total is None:
        raise ValueError("this recording cannot state its full length (no chapter table), so a "
                         "full-session export has no end to render to")
    source = _video_source_for(session, 0.0, total, src_path)
    best_id = _best_lap_id(session)
    # `lap_id` is a HINT here and only the map inset reads it: None means "draw the whole
    # session's trace", which is the right picture for a clip that runs over every lap.
    return ExportSpec(out_path=out_path, lap_id=-1, t0=0.0, t1=float(total),
                      source=source, config=config or OverlayConfig(),
                      follow_laps=True, best_lap_id=best_id)


def scope_lap_ids(session, scope: str, lap_id: int | None) -> list[int]:
    """Which laps `scope` renders: the selected one, the best one, or every VALID lap. Empty for
    the full-session scope, which renders no lap in particular, and empty when the scope asks for
    a lap the session cannot supply.

    "Every valid lap" is `session.valid_lap_ids()` where the session offers it — the SAME set the
    lap table and every analytic use, so an All-laps batch cannot include the mis-segmented short
    lap the rest of the app excludes. A session without the accessor falls back to every lap that
    has a usable window."""
    if scope == SCOPE_SESSION:
        return []
    if scope == SCOPE_BEST_LAP:
        best = _best_lap_id(session)
        return [int(best)] if best is not None else []
    if scope == SCOPE_ALL_LAPS:
        valid = getattr(session, "valid_lap_ids", None)
        if callable(valid):
            try:
                return [int(i) for i in valid()]
            except Exception:  # noqa: BLE001 — fall through to the window scan
                pass
        count = getattr(session, "lap_count", None)
        try:
            n = int(count() if callable(count) else (count or 0))
        except Exception:  # noqa: BLE001 — no lap count -> no laps to batch
            return []
        return [i for i in range(n) if lap_window_for_export(session, i) is not None]
    return [int(lap_id)] if lap_id is not None else []


def lap_output_path(out_path: str, lap_id: int) -> str:
    """The per-lap file name an All-laps batch writes for `lap_id`, from the single path the user
    chose: `ride.mp4` + lap 7 -> `ride_lap7.mp4`. A PNG-sequence export has no extension to keep
    (its "file" is a directory), so the suffix simply lands on the end.

    One file per lap is the whole point of the scope, and the alternative — asking for N save
    locations — is N dialogs to dismiss. `lap_label` is the app's own lap numbering, so the file
    name matches what the lap table calls the lap."""
    stem, ext = os.path.splitext(out_path)
    return f"{stem}_lap{lap_label(lap_id)}{ext}"


def build_scope_specs(session, out_path: str, scope: str, lap_id: int | None = None,
                      config: OverlayConfig | None = None, src_path: str | None = None,
                      lead: float = 0.0) -> list[ExportSpec]:
    """Every `ExportSpec` a scope renders, in order — one for the three lap scopes, one PER LAP for
    All laps, one for the whole session. The caller owns each spec's `source` and must
    `cleanup()` it.

    This is where the export stopped being "the selected lap". Rendering 90 seconds instead of
    thirty minutes is the single largest thing that can be done about export time, and it needed
    no encoder work at all — just the ability to say which 90 seconds.

    `lead` is the run-up/run-off applied to EACH lap render and is ignored for the session scope
    (see `build_session_spec`). On an All-laps batch it is applied per file, which means a
    non-zero lead really does put the end of lap N-1 at the head of lap N's file and the start of
    lap N+1 at its tail — footage from a neighbouring lap, in a file named for this one. That is
    the artefact a competitor's per-lap export ships silently; here the picker says it in words
    before the render starts, the overlay marks the run-up as pending rather than running, and
    the default is no padding at all."""
    if scope == SCOPE_SESSION:
        return [build_session_spec(session, out_path, config=config, src_path=src_path)]
    ids = scope_lap_ids(session, scope, lap_id)
    if not ids:
        raise ValueError("this recording has no lap to export for that scope")
    multi = len(ids) > 1
    return [build_lap_spec(session, lap_output_path(out_path, i) if multi else out_path, i,
                           config=config, src_path=src_path, lead_in=lead, lead_out=lead)
            for i in ids]


def render_lap(session, src_path: str, out_path: str, lap_id: int,
               config: OverlayConfig | None = None,
               progress=None, cancel=None,
               lead_in: float = 0.0, lead_out: float = 0.0) -> RenderResult:
    """Build the lap spec and render to completion (headless/tests). Raises ValueError if the lap
    window is unusable; `src_path` is the fallback single-file source when the session has no
    ChapterMap. `lead_in`/`lead_out` add run-up/run-off seconds (see `lap_window_for_export`)."""
    spec = build_lap_spec(session, out_path, lap_id, config=config, src_path=src_path,
                          lead_in=lead_in, lead_out=lead_out)
    try:
        return Renderer(session, spec).run(progress=progress, cancel=cancel)
    finally:
        spec.source.cleanup()
