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
import sys
import threading
import time
from dataclasses import dataclass, field, replace

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
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

from . import gmeter_overlay, theme, units
from ._signal import fmt_time, lap_label
from .export_palette import EXPORT


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
    (== Session.lap_window: start, start+lap_time) widened by `lead_in` seconds of run-up and
    `lead_out` of run-off, clamped to the footage. None if the lap window itself is unusable.
    Unpadded (the default) this is exactly `Session.lap_window`, unchanged.

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
        `frame_times` size the render for frames that do not exist; the decoder's short read is
        treated by `run_chunk` as a clean finish (`produced > 0`), so the export "succeeds" with a
        clip shorter than asked for and a progress bar that stops before 100 %.

    Neither clamp can shrink the LAP: they only take back padding that ran off the end of the
    recording, so the first and last laps of a session export with as much run-up/run-off as
    exists and no less of the lap."""
    win = session.lap_window(lap_id)
    if win is None:
        return None
    lap_t0, lap_t1 = float(win[0]), float(win[1])
    if not (lap_t1 > lap_t0):
        return None
    t0 = min(lap_t0, max(0.0, lap_t0 - max(0.0, float(lead_in))))
    t1 = lap_t1 + max(0.0, float(lead_out))
    total = footage_duration(session)
    if total is not None:
        t1 = max(lap_t1, min(t1, total))
    return t0, t1


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


def overlay_values_at(session, t: float, spec: ExportSpec | None = None) -> OverlayValues:
    """Resolve the overlay values at media time `t` the SAME way app._apply_readout does:

      * lap        = session.lap_at_time(t)
      * marker idx = session.index_at_time(t)        (nearest trace sample)
      * speed km/h = session.tv[idx]                 (the per-sample km/h array)
      * Δ-to-best  = session.delta_at_lap(lap, t)    (normalized-distance vs the best/ref lap)
      * g          = session.g_at_time(t)            (kart-frame lat/long/total in g)

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
    outside the lap and are the reason the padding shows live footage with live numbers."""
    if spec is None:
        lap_id = session.lap_at_time(t)
        started, finished, clock_t = lap_id is not None, False, t
    else:
        lap_id = spec.lap_id
        started = t >= spec.lap_t0 - 1e-9
        finished = t >= spec.lap_t1
        clock_t = min(max(t, spec.lap_t0), max(spec.lap_t0, spec.lap_t1 - _LAP_CLOCK_EPS))
    i = session.index_at_time(t)
    speed = float(session.tv[i]) if i is not None and len(session.tv) else None
    delta = session.delta_at_lap(lap_id, clock_t) if (lap_id is not None and started) else None
    g = session.g_at_time(t) if getattr(session, "has_gmeter", False) else None
    return OverlayValues(t=t, lap_id=lap_id, speed_kmh=speed, delta_s=delta, g=g, marker_index=i,
                         lap_started=started, lap_finished=finished)


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
    """DECODE argv: -ss spec.local_t0 before the input + -t duration, scale to out_w x out_h, force
    the constant `fps`, emit rgb24 rawvideo to stdout; -an/-sn/-dn drop non-video. Input is
    spec.source.input_args() (chapter file or concat span); the source-LOCAL seek is what makes a
    chaptered export read the right footage. `hwaccel` adds `-hwaccel videotoolbox` to offload the
    decode (a big CPU relief on a core-starved machine).

    That `-ss` is ffmpeg's ACCURATE input seek: it lands on the keyframe at or before the target and
    then decodes forward and discards up to it, so the first frame out is the frame at t0 rather
    than the keyframe's. Which is the whole reason the seam branch stopped positioning itself with a
    concat `inpoint` — `inpoint` has no such second half, and left the picture a GOP ahead."""
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
    encode H.264 (`encoder`) + AAC, `-af apad -shortest`.

    `-shortest` ENDS THE CLIP WITH THE SHORTEST STREAM, AND ON THE LAST FRAME OF A RECORDING THAT
    IS THE AUDIO. A GoPro chapter's audio track can be a hair shorter than its video: measured on
    D24 chapter 3, video 1590.005083 s vs audio 1589.994667 s — 10.4 ms, which is the
    container duration `ChapterMap.total_duration` is built from minus the audio's own. So a
    run-off clamped to the end of the footage asks for 16.000 s of a track that holds 15.989 s,
    `-shortest` cuts the output there, and the muxer drops the last VIDEO frame we had already
    composited and written. Measured, real ffmpeg, 480p: requested 480 frames, wrote 480, the file
    held 479 (15.989 s, −0.011 s) — and reproduced identically at 483→482, 510→509 and
    2100→2099, i.e. the loss is exactly one frame whenever `t1 == total_duration`, on the muxer's
    side of the pipe rather than the decoder's.

    `apad` pads the audio with silence, which makes the VIDEO the shortest stream — and the video's
    length is the one this export PROMISED (`frame_times`, the progress bar's denominator and
    `RenderResult.frames` are all that count). The two flags belong together: `apad` alone would
    run forever, `-shortest` alone truncates to whichever input ran out first. Measured after:
    480/480 frames, 16.000000 s, on the same windows; an interior window is unchanged (720/720,
    24.000000 s) and a source with NO audio stream is unaffected (the `-map 1:a:0?` is optional and
    the filter has nothing to run on)."""
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
        "-af", "apad", "-shortest",
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
_READOUT_PAD_FRAC = 0.26      # readout: left/right inner padding, as a fraction of the pill HEIGHT
_READOUT_GAP_K = 4.0          # readout: hero number -> unit label, in k units (k = height / 44)
_STRIP_PAD_L_FRAC = 0.42      # strip: the ink starts further in — the amber progress fill runs
_STRIP_PAD_R_FRAC = 0.20      #        under it, and a flush label would sit on the fill's edge
_RUN_GAP_K = 16.0             # the gap between two separate RUNS (elapsed time -> Δ), in k units

# What the strip carries INSTEAD of a Δ when the exported lap is the session's best (see
# `strip_tail`). "★" is in the shipped face — verified by rendering it through `_draw_text`, the
# export's own QPainterPath.addText path, not by asking the font.
_BEST_MARK = "★ BEST"

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
    lead-in the clip's own t0 would count the run-up)."""
    if vals.lap_id is None:
        return None
    win = session.lap_window(vals.lap_id)
    if win is not None:
        ls, le = win
        span = le - ls
        elapsed = 0.0 if span <= 0 else min(max(vals.t - ls, 0.0), span)
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

      * the speed budget masked `tt < spec.t1` while the per-frame lookup is
        `session.index_at_time` — `np.searchsorted`, a CEILING. Every frame past the last in-window
        sample reads the first sample AT OR AFTER `t1`, which the mask excluded: at 10 Hz GPS /
        30 fps, the last ~2-3 frames of every clip. Constructed (99.4 km/h inside `[0, 10)`,
        142 km/h from the sample at `t = 10.0`): the budget said `('99',)` and 2 frames burned
        `142`, painting +9.57 px of ink right of the readout pill's right edge, measured on the
        composite.
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
        runs = _strip_runs(session, vals, spec.lap_t0, spec.is_best, spec.config.palette)
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
        # Both pills are FITTED to the widest text this export WILL burn — enumerated once here by
        # replaying the render's own per-frame lookup over its own frame times, never re-derived
        # and never per frame (a pill that breathed as the speed gained a digit would be worse than
        # one that is too wide). See `_burned_runs` and the HUD pill geometry block.
        speeds, labels, tails = _burned_runs(session, spec, self._fps)
        # readout: BOTTOM-LEFT.
        rh = max(cfg.readout_h_frac * out_h, 22.0)
        self._readout_rect = QRectF(
            m, out_h - m - rh,
            readout_pill_width(rh, speeds, units.speed_label(cfg.speed_unit)), rh)
        # lap strip: TOP-LEFT.
        sh = max(cfg.strip_h_frac * out_h, 20.0)
        self._strip_rect = QRectF(m, m, strip_pill_width(sh, labels, tails), sh)
        # The g-meter dial's FILTERING STATE, driven exactly like the live overlay so the burned
        # dial matches the screen (incl. the axis-provenance tag: IMU lateral · GPS longitudinal,
        # not a bare source name).
        #
        # A `DialFilter`, NOT a `GMeterOverlay`: this constructor runs on `VideoExportWorker`'s
        # QThread, and `GMeterOverlay` is a frameless translucent top-level QWidget. Creating and
        # destroying one off the GUI thread is undefined behaviour in Qt — the exact SIGSEGV shape
        # tests/test_compare_lifecycle.py exists for — and every export did it twice on the VT →
        # libx264 fallback path. The render path never wanted the widget: it only calls the free
        # `gmeter_overlay.paint_dial` with a `DialState` snapshot, which the filter provides.
        # tests/test_export_thread_safety.py holds this line to it.
        self._dial = gmeter_overlay.DialFilter()
        _src = session.gmeter_source() if hasattr(session, "gmeter_source") else "accl"
        _long = session.gmeter_long_source() if hasattr(session, "gmeter_long_source") else None
        self._dial.set_source(_src, _long)
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
        # export=True -> the vivid, no-box, big-number dial; scale_k from the dial's own size (1.0
        # at the ~280 px 1080p dial) so its strokes/glyphs track the output resolution.
        gmeter_overlay.paint_dial(p, self._g_rect.width(), self._g_rect.height(), dial_state,
                                  export=True, scale_k=self._g_rect.width() / 280.0)
        p.restore()
        self._map.paint(p, vals.marker_index)
        _paint_readout(p, self._readout_rect, vals, self._spec.config.speed_unit)
        # The strip's fallback elapsed origin is the LAP's start, not the clip's — with a lead-in
        # those differ, and a session with no lap window would otherwise count the run-up.
        _paint_strip(p, self._strip_rect, self._session, vals, self._spec.lap_t0,
                     self._spec.config.palette, self._spec.is_best)
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
        # The painter is handed the SAME fps, because its pill budget replays these very frame
        # times to learn what it will draw (see `_burned_runs`).
        self._painter = OverlayPainter(session, spec, self._out_w, self._out_h, self._fps)
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
            vals = overlay_values_at(self._session, float(self._times[self._i]), self._spec)
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
    best = getattr(session, "best_lap_id", None)
    best_id = best() if callable(best) else None
    return ExportSpec(out_path=out_path, lap_id=lap_id, t0=t0, t1=t1,
                      source=source, config=config or OverlayConfig(),
                      is_best=best_id is not None and int(best_id) == int(lap_id),
                      lead_in=lap_t0 - t0, lead_out=t1 - lap_t1)


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
