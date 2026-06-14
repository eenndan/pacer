"""Offline video-overlay export (F9): burn the telemetry overlays onto the GoPro footage and
mux out a shareable MP4.

WHAT THIS IS — AND IS NOT
-------------------------
A self-contained OFFLINE renderer. It runs its OWN frame-by-frame render loop driven by a caller
that pumps `Renderer.run_chunk` (so the UI stays responsive + cancellable) — it has NO dependency
on the live Qt event loop, the VideoView, the player, or any running app state. It is also
`pacer`-FREE: like the other analysis/IO modules (export_data.py, corners.py), it is fed entirely
by a `Session` (the same accessors the live app reads at each tick) and never imports the compiled
bindings. It DOES use QPainter/QImage to composite — that is pure off-screen 2-D drawing, not an
event loop — so the burned-in overlays are pixel-for-pixel the same widgets the app shows.

DECODE / COMPOSITE / MUX (the ffmpeg-rawvideo-pipe approach)
-----------------------------------------------------------
The project already shells out to nothing but ffmpeg (added as a pixi dep for this feature), and a
raw-video pipe is the simplest robust path that needs no extra Python codec dependency (PyAV is not
in the env). Two ffmpeg processes bracket a Python compositing loop:

  1. DECODE: `ffmpeg -ss t0 -i src -t dur -vf scale=W:H -pix_fmt rgb24 -f rawvideo pipe:1`
     trims the source to exactly the selected lap's media-time window, scales to the output size,
     and streams raw RGB frames to our stdout pipe. We read W*H*3 bytes per frame.
  2. COMPOSITE: each frame's bytes become a QImage (Format_RGB888); a QPainter paints the overlay
     elements (g-meter dial, Δ/speed box, track-map inset + marker, lap/sector strip) at the
     frame's MEDIA TIME — reading the SAME Session/gmeter accessors the live readout uses.
  3. MUX: `ffmpeg -f rawvideo -i pipe:0 -ss t0 -i src -t dur -map 0:v -map 1:a -c:v libx264
     -c:a aac out.mp4` reads our composited RGB frames from stdin, re-encodes H.264, and carries
     the source AUDIO trimmed to the SAME window (so the export keeps engine/track sound, in sync).

The decode and mux fps are PINNED to one chosen output fps so frame N out lines up with frame N in;
the audio `-ss`/`-t` on the source uses the identical window, so duration and A/V sync match the
lap to within a frame.

SCOPE (v1): ONE selected lap. Full-session export and compare-pair side-by-side are Phase 2
(see studio/PLAN.md). A cancellable progress flow is driven by the caller (app.py owns the dialog).

The numbers burned in are EXACT in the sense that matters: `overlay_values_at` reads
`session.index_at_time` → `session.tv[i]` for speed, `session.lap_at_time`+`delta_at_lap` for Δ,
and `session.g_at_time` for the g dot — the very calls app._apply_readout makes — so a frame grab
at media time t shows what the app shows at t.
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
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPen, QPolygonF

from . import gmeter_overlay
from ._signal import fmt_time
from .theme import C

# --------------------------------------------------------------------------- ffmpeg discovery
# Resolved lazily so importing this module never requires ffmpeg (the unit tests mock the
# subprocess; only a real render needs the binaries). The pixi env puts them on PATH for the app.
FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"


def ffmpeg_available() -> bool:
    """True iff both ffmpeg and ffprobe are resolvable on PATH — gate a real render (and the
    real-render test) on this so an env without ffmpeg degrades to a clear message instead of a
    crash."""
    return shutil.which(FFMPEG) is not None and shutil.which(FFPROBE) is not None


# --------------------------------------------------------------------------- encoder selection
# The headline GPU offload: on Apple Silicon, ffmpeg's `h264_videotoolbox` encoder runs the H.264
# encode on the Apple media engine (the dedicated video hardware) instead of the CPU. That both
# makes the encode itself faster AND — crucially for this composite pipeline — frees the CPU cores
# the software libx264 encoder was contending for with our QPainter loop. It is quality-via-BITRATE
# (no CRF), so we target a generous bitrate that stays visually clean at 1080p. We KEEP libx264 as a
# robust fallback: a VideoToolbox encode session can fail at runtime on some pixel-format / size
# combinations, so we (a) probe it once at startup and (b) if the probe or a real encode fails,
# transparently fall back to libx264 so the feature never breaks.
VT_H264 = "h264_videotoolbox"
SW_H264 = "libx264"

# Target H.264 bitrate (bits/s) as bits-per-pixel-per-frame so the VideoToolbox stream stays clean
# at any size/fps. ~0.10 bpp is a comfortably high 1080p60 setting (~12.4 Mbit/s) that keeps the
# burned-in overlay text + the footage crisp; VideoToolbox is bitrate-driven so we err generous
# (storage is cheap — it's a shareable clip, not an archive master). Floor keeps tiny test sizes
# from getting a starved bitrate.
_BITS_PER_PIXEL = 0.10
_MIN_VT_BITRATE = 2_000_000


def vt_target_bitrate(out_w: int, out_h: int, fps: float) -> int:
    """A sensible VideoToolbox target bitrate (bits/s) for an out_w x out_h @ fps stream — bits per
    pixel per frame, floored. Used only for the hardware encoder (libx264 stays CRF-driven)."""
    bits = int(out_w * out_h * max(fps, 1.0) * _BITS_PER_PIXEL)
    return max(bits, _MIN_VT_BITRATE)


def videotoolbox_encoder_available() -> bool:
    """True iff ffmpeg lists the `h264_videotoolbox` encoder (it's compiled in). A cheap static
    capability check — NOT proof a hardware session will open, which `videotoolbox_usable` confirms
    with a real tiny encode. Cached so repeated exports don't re-shell ffmpeg."""
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
# Output presets. 1080p default (the brief): a shareable size that re-encodes fast enough while
# staying crisp. Width is derived from the source aspect at render time (so a 16:9 4K source maps
# to 1920x1080, but a different aspect keeps its shape) — `height` is the controlling dimension.
@dataclass(frozen=True)
class OverlayConfig:
    """Layout + output knobs for the export. All overlay placements are FRACTIONS of the frame so
    the composition scales with `out_height`. The defaults reproduce the app's corner placements
    (g-meter top-right, readout bottom-left, map inset bottom-right, lap strip top-left)."""
    out_height: int = 1080            # controlling output dimension (width follows source aspect)
    fps: float | None = None          # explicit output fps; None = source fps, then fps_cap applies
    # Cap the output fps. A telemetry overlay reads identically at 30 fps as at 59.94 — the dial,
    # Δ box and map move smoothly — but 30 fps HALVES the frame count, so it ~halves every per-frame
    # cost (decode + composite + encode). GoPro footage is typically 59.94/60; capping to 30 is the
    # single cheapest large speed-up with negligible perceived loss for an overlay clip. Set to None
    # to keep the full source rate. (If `fps` is set explicitly, that wins and the cap is ignored.)
    fps_cap: float | None = 30.0
    # Video encoder: "auto" picks the Apple media-engine encoder (h264_videotoolbox) when a real
    # hardware session opens on this machine, else libx264; force "libx264" / "videotoolbox" to
    # override. VideoToolbox offloads the H.264 encode to the GPU/media engine (frees CPU cores).
    encoder: str = "auto"
    # Hardware-accelerated DECODE via VideoToolbox. "auto" enables it whenever VideoToolbox is the
    # encoder too (so BOTH the decode and the encode run on the media engine, leaving the CPU for the
    # parallel composite — the configuration that rescues a core-starved machine); True/False force
    # it. Neutral on wall-time on a fast box; a big CPU relief on a slow one.
    hwaccel_decode: str | bool = "auto"
    # Retained for backward compatibility / explicit API, but the renderer is now SINGLE-THREADED
    # by design (see Renderer): VideoToolbox is process-isolated and frees the CPU, so an in-line
    # paint keeps up at 30 fps and we avoid the fragile parallel-pipeline deadlock surface that
    # could hang the GUI export. This field is accepted but does not spin up a paint pool.
    workers: int | None = None
    # No-progress WATCHDOG (seconds). If the frame counter does not advance for this long, the
    # render is presumed WEDGED (a hung VideoToolbox session / stuck pipe) and is aborted cleanly
    # (ffmpeg killed, threads joined, a RenderTimeoutError surfaced) — then retried ONCE on the
    # software encoder. This is what makes an infinite hang structurally impossible. Generous
    # enough that a merely-slow machine never trips it (a 1080p frame composites in tens of ms;
    # even a stalled-then-recovering encoder gets 30 s of grace).
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
# A long GoPro recording is split (at a file-SIZE limit, not at a lap) into CHAPTERS that are
# contiguous on ONE global media clock — chapter i covers global [offset_i, offset_i+dur_i). The
# rest of the app treats the recording as one session by laying the chapters on that global axis
# (studio/chapters.ChapterMap) and the video player seeks across them by mapping a global time to
# (chapter file, local time). The EXPORT must do the SAME: a lap's window is a GLOBAL window, but
# ffmpeg can only `-ss` INTO A SINGLE FILE's own (local) clock. Seeking with a global t0 into the
# FIRST chapter file (the old bug) lands PAST that file's end for any lap outside chapter 1, so
# ffmpeg decodes ZERO frames and the export produces nothing (an empty progress bar / a 0-byte
# clip). `VideoSource` is the resolved, file-local source ffmpeg actually reads.
#
# Two shapes, both reusing the ChapterMap's global<->local arithmetic (we never reinvent it):
#   * a SINGLE chapter file when the whole window lies inside one chapter — ffmpeg `-i file` with
#     `local = global - chapter.offset` for the seek;
#   * a CONCAT of the spanned chapter files when the window crosses a chapter SEAM (a lap can
#     straddle a boundary) — ffmpeg's concat demuxer plays them back-to-back as one continuous
#     stream so frames + audio flow across the seam. The first spanned chapter carries a concat
#     `inpoint` at the window's local start within it, so the concatenated stream BEGINS at the lap
#     start (a fast keyframe seek, not a decode-from-file-0 scan, and — unlike a plain `-ss` before
#     a concat input, which does not seek reliably — it actually lands there); the stream's local
#     clock is then 0 at the global t0.
#
# `time_offset` is the global->local shift: a frame's GLOBAL media time t maps to this source's own
# clock as `t - time_offset`. For a single-chapter source it is the chapter's global offset; for a
# seam/concat source it is t0 (the inpoint put the lap start at the concatenation's t=0). For a
# plain single-file recording the offset is 0 and global == local, so the legacy single-file path is
# exactly preserved.
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
        """The ffmpeg input portion: a concat-demuxer input when spanning a seam, else a plain
        `-i <file>`. Placed where the old `-i src_path` was in the decode/encode argv."""
        if self.concat_list_path is not None:
            # `-safe 0` allows absolute paths in the list; the concat demuxer presents the listed
            # chapter files as ONE continuous stream so a seam-crossing window decodes unbroken.
            return ["-f", "concat", "-safe", "0", "-i", self.concat_list_path]
        return ["-i", self.probe_path]

    def cleanup(self) -> None:
        """Remove the temp concat-list file (best-effort), if this source wrote one."""
        if self.concat_list_path is not None:
            try:
                os.remove(self.concat_list_path)
            except OSError:
                pass


def single_file_source(path: str) -> VideoSource:
    """A VideoSource for a plain single file (no chapters): global == local, offset 0. Used to
    synthesize a source from a bare `src_path` so the legacy single-file export path is unchanged."""
    return VideoSource(probe_path=path, time_offset=0.0)


def resolve_video_source(chapter_map, t0: float, t1: float,
                         tmp_dir: str | None = None) -> VideoSource:
    """Resolve the GLOBAL window [t0, t1) to the file-local ffmpeg source via a `ChapterMap`
    (studio/chapters.ChapterMap) — the SAME global<->local mapping the video player seeks with, so
    the export reads the EXACT footage the player shows for that window. `chapter_map` may be None
    or a single-chapter map (a plain recording): then there's one file and global == local.

    Single chapter  -> `-i <that chapter file>`, time_offset = the chapter's global offset.
    Spans a seam    -> a concat demuxer over every chapter from the start chapter through the end
                       chapter, with an `inpoint` on the first one at the window's local start so
                       the concatenation BEGINS at the lap (a fast keyframe seek into the seam);
                       time_offset = t0 (the lap start is the concatenation's t=0). The concat list
                       is written under `tmp_dir` (default the system temp dir); the caller frees it
                       via `VideoSource.cleanup()`.

    Raises ValueError if `chapter_map` has no chapters."""
    chs = list(getattr(chapter_map, "chapters", []) or [])
    if not chs:
        raise ValueError("resolve_video_source needs a ChapterMap with at least one chapter")
    i0 = chapter_map.chapter_at(t0)
    # The end is half-open; nudge a window that ends exactly on a seam back into the chapter it
    # actually played, so a lap ending at offset_{k+1} doesn't pull in a needless extra chapter.
    i1 = chapter_map.chapter_at(max(t0, t1 - 1e-6))
    start = chs[i0]
    if i1 <= i0:
        # Whole window inside ONE chapter: seek that file at local = global - chapter.offset.
        return VideoSource(probe_path=start.path, time_offset=float(start.offset))
    # Spans a seam: concat the chapters [i0 .. i1] so frames/audio flow across the boundary. The
    # FIRST spanned chapter gets a concat `inpoint` at the window's local start within it, so the
    # concatenated stream BEGINS at the lap start (a fast keyframe seek, NOT a decode-from-file-0
    # scan) and runs continuously into the next chapter — `inpoint` is what makes the seam seek both
    # correct AND fast (a plain `-ss` before a concat input does not seek reliably). With the stream
    # starting at the lap, the source's local clock is 0 at the global t0, so time_offset = t0 and
    # the decode/encode seek with `-ss 0`.
    span = chs[i0:i1 + 1]
    inpoint = max(0.0, t0 - start.offset)        # local start within the first spanned chapter
    tmp = tmp_dir or os.environ.get("TMPDIR") or "/tmp"
    _fd, list_path = _mk_concat_list(span, tmp, first_inpoint=inpoint)
    return VideoSource(probe_path=start.path, time_offset=float(t0),
                       concat_list_path=list_path)


def _mk_concat_list(chapters_span, tmp_dir: str,
                    first_inpoint: float | None = None) -> tuple[int, str]:
    """Write an ffmpeg concat-demuxer list file for `chapters_span` (each `file '<abspath>'`,
    single-quotes in the path escaped per the concat syntax) into `tmp_dir`, returning (fd, path).
    The chapters are listed in order so the demuxer presents them as one stream. `first_inpoint`
    (seconds) adds a concat `inpoint` directive after the FIRST file so the concatenated stream
    starts at that local time within the first chapter (the lap start) — a fast keyframe seek into
    the seam rather than a decode-from-zero scan."""
    import tempfile
    fd, list_path = tempfile.mkstemp(prefix="pacer_export_concat_", suffix=".txt", dir=tmp_dir)
    lines = []
    for idx, c in enumerate(chapters_span):
        ap = os.path.abspath(c.path)
        # The concat demuxer's quoting: a literal ' inside a single-quoted token is '\''.
        esc = ap.replace("'", "'\\''")
        lines.append(f"file '{esc}'\n")
        if idx == 0 and first_inpoint and first_inpoint > 0:
            # inpoint trims the FIRST file's start so the concatenation begins at the lap (the next
            # chapters play from their own start, so frames flow across the seam unbroken).
            lines.append(f"inpoint {first_inpoint:.6f}\n")
    with os.fdopen(fd, "w") as f:
        f.write("".join(lines))
    return fd, list_path


# --------------------------------------------------------------------------- export spec
@dataclass
class ExportSpec:
    """Everything a render needs, resolved up front so the render loop is pure mechanism.

    `t0`/`t1` are the GLOBAL media-clock window (seconds) to export — normally a lap's window from
    `lap_window_for_export`. `lap_id` is the lap whose Δ baseline + sector strip are shown (and
    whose g-meter envelope scope is pinned). `out_path` the MP4 to write; `config` the
    layout/output knobs.

    The VIDEO SOURCE is `source` — a `VideoSource` that resolves the global window to the correct
    chapter file(s) + the file-local seek offset (see VideoSource / resolve_video_source). For a
    chaptered recording app.py builds it from `session.chapters`; for a plain single file it is the
    file with offset 0. `src_path` is retained for backward compatibility / a single-file caller:
    if `source` is omitted it is synthesized from `src_path` (offset 0). All ffmpeg seeking uses
    the source-LOCAL time (`t0/t1 - source.time_offset`), never the global t0, which is what fixes
    the chaptered-export 'past EOF, zero frames' hang."""
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
    """The MEDIA-clock (t0, t1) window to export for `lap_id`, or None if the lap is unusable.

    This is exactly `Session.lap_window` (start_timestamp, start+lap_time) — the SAME half-open
    window `lap_at_time` resolves, so every frame in [t0, t1) reports this lap. Kept as a named
    helper (rather than inlining lap_window) because the export is the one place the window's
    semantics are load-bearing for A/V sync, and so the math is unit-testable without ffmpeg."""
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


def resolve_workers(workers: int | None) -> int:
    """How many parallel COMPOSITE (paint) worker threads to use. None/0 = auto: a small pool sized
    to the machine (cpu_count-1, clamped to [1, 4]) — enough to overlap painting with decode+encode
    without oversubscribing the cores VideoToolbox/ffmpeg also want. An explicit positive value is
    honoured (1 = the in-line single-threaded paint)."""
    if workers and workers > 0:
        return int(workers)
    cpu = os.cpu_count() or 2
    return max(1, min(4, cpu - 1))


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
    w += w & 1                      # make even
    h += h & 1
    return max(w, 2), max(h, 2)


def build_decode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float,
                     hwaccel: bool = False) -> list[str]:
    """The DECODE ffmpeg argv: seek to the source-LOCAL t0 BEFORE the input (fast keyframe seek)
    and AGAIN trim by duration, scale to (out_w, out_h), force the constant output `fps`, emit
    rgb24 rawvideo to stdout. `-an`/`-sn`/`-dn` drop audio/subs/data — we only want the video here.

    The seek uses `spec.local_t0` (the global t0 shifted into the resolved source's own clock), and
    the input is `spec.source.input_args()` — a single chapter file, or the concat demuxer over the
    chapters a seam-crossing lap spans. This is what makes a chaptered export read the RIGHT footage
    (the old code seeked a GLOBAL t0 into the first chapter file, landing past its end for any lap
    outside chapter 1 -> zero frames).

    `hwaccel` adds `-hwaccel videotoolbox` BEFORE the input so the Apple media engine decodes the
    (HEVC/H.264) source instead of the CPU. On GoPro HEVC that moves the decode — a ~6-core software
    job — onto the hardware, freeing those cores for the parallel composite + (if used) leaving the
    encode untouched. It barely changes wall-time on a fast multi-core machine (software HEVC decode
    is already threaded) but is a large CPU relief on a core-starved one, which is exactly where the
    export was 'too slow to use'."""
    hw = ["-hwaccel", "videotoolbox"] if hwaccel else []
    return [
        FFMPEG, "-nostdin", "-loglevel", "error",
        *hw,
        "-ss", f"{spec.local_t0:.6f}", *spec.source.input_args(), "-t", f"{spec.duration:.6f}",
        "-vf", f"scale={out_w}:{out_h},fps={fps:.6f}",
        "-an", "-sn", "-dn",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]


def _video_codec_args(encoder: str, out_w: int, out_h: int, fps: float) -> list[str]:
    """The `-c:v ...` portion of the encode argv for the resolved `encoder`:

      * h264_videotoolbox — the Apple media-engine (GPU) encoder. Quality is bitrate-driven, so we
        pass a generous target (vt_target_bitrate) + a matching cap; `-allow_sw 1` lets ffmpeg fall
        back to VideoToolbox's own software path rather than erroring if a HW session can't open;
        `-realtime 0` favours quality over latency (this is an offline export, not a live stream).
        `-color_range tv` silences the "range not set" note and pins MPEG/limited range.
      * libx264 — the software fallback: veryfast/CRF 20, the original visually-lossless setting.

    Both end yuv420p + faststart so the MP4 is broadly playable and streams (moov atom up front)."""
    if encoder == VT_H264:
        br = vt_target_bitrate(out_w, out_h, fps)
        return [
            "-c:v", VT_H264,
            "-b:v", str(br), "-maxrate", str(br), "-bufsize", str(br * 2),
            "-allow_sw", "1", "-realtime", "0",
            "-pix_fmt", "yuv420p", "-color_range", "tv", "-movflags", "+faststart",
        ]
    return [
        "-c:v", SW_H264, "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
    ]


def build_encode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float,
                     encoder: str = SW_H264) -> list[str]:
    """The MUX/ENCODE ffmpeg argv: input 0 is our composited rgb24 rawvideo on stdin (we declare
    its size + rate); input 1 is the SOURCE again, seek-trimmed to the same source-LOCAL window for
    its AUDIO. Map our video + the source audio, encode H.264 with the chosen `encoder`
    (h264_videotoolbox GPU offload or libx264) and AAC. `-shortest` guards against a fractional-frame
    audio overrun.

    The audio seek/input MIRROR the decode's: `spec.local_t0` + the same `spec.source.input_args()`
    (single file or concat list), so the audio is the SAME chapter span as the video and stays in
    sync across a seam — not a global t0 into the first chapter (the old chaptered-export bug)."""
    return [
        FFMPEG, "-nostdin", "-loglevel", "error", "-y",
        # input 0: raw composited video from our pipe
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", f"{fps:.6f}",
        "-i", "pipe:0",
        # input 1: source audio, same source-LOCAL window (mirrors the decode's input + seek)
        "-ss", f"{spec.local_t0:.6f}", *spec.source.input_args(), "-t", f"{spec.duration:.6f}",
        "-map", "0:v:0", "-map", "1:a:0?",
        *_video_codec_args(encoder, out_w, out_h, fps),
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


class _MapInset:
    """Precomputed track-map inset: the whole-session trace + the selected lap's line projected
    into a fixed inset box ONCE (the track shape doesn't change) and BAKED into a cached RGBA layer
    so each frame only has to blit that layer + place the moving marker. Mirrors MapView's look at a
    glance — faint full trace + the selected lap's line + a coral marker dot.

    Why the cache matters: the full-session trace is tens of thousands of points; antialiased
    `drawPolyline` over it costs ~20 ms PER call, and it (plus the lap line) was being re-rasterized
    on EVERY exported frame — ~40 ms/frame, which alone dominated the render (a 4 K-source 1080p lap
    export ran at ~18 fps, several minutes for one lap). The static art never changes between frames;
    baking it once and blitting (a sub-millisecond copy) drops the map cost to ~nothing and makes the
    render decode-bound instead. The marker dot is the only per-frame draw left."""

    def __init__(self, session, box: QRectF, lap_id: int):
        self._box = box
        xs = np.asarray(session.tx, dtype=float)
        ys = np.asarray(session.ty, dtype=float)
        self._ok = len(xs) >= 2 and len(ys) >= 2
        if not self._ok:
            return
        # Fit the trace bbox into the box with a small pad, preserving aspect; flip Y (screen down).
        pad = 0.10
        x0, x1 = float(xs.min()), float(xs.max())
        y0, y1 = float(ys.min()), float(ys.max())
        sx = (x1 - x0) or 1.0
        sy = (y1 - y0) or 1.0
        bw = box.width() * (1 - 2 * pad)
        bh = box.height() * (1 - 2 * pad)
        scale = min(bw / sx, bh / sy)
        # centre the scaled track in the box
        cx_off = box.x() + box.width() / 2 - scale * (x0 + x1) / 2
        cy_off = box.y() + box.height() / 2 + scale * (y0 + y1) / 2  # +: undo the Y flip below

        def proj(px, py):
            return QPointF(cx_off + scale * px, cy_off - scale * py)

        self._proj = proj
        trace = QPolygonF([proj(px, py) for px, py in zip(xs, ys, strict=True)])
        # the selected lap's own line (drawn brighter); fall back to the full trace if degenerate.
        lap_poly = None
        got = session._lap_trace_xyt(lap_id) if hasattr(session, "_lap_trace_xyt") else None
        if got is not None:
            lx, ly, _ = got
            if len(lx) >= 2:
                lap_poly = QPolygonF([proj(px, py) for px, py in zip(lx, ly, strict=True)])
        self._xs, self._ys = xs, ys
        # --- bake the static layers (backdrop + full trace + lap line) into a cached RGBA image,
        # sized to the WHOLE frame so we can blit it at (0, 0) each frame with the box-coordinate
        # projection already correct. Painted ONCE here; `paint` only copies it + draws the marker.
        self._layer = self._bake_layer(box, trace, lap_poly)

    @staticmethod
    def _bake_layer(box: QRectF, trace: QPolygonF, lap_poly) -> QImage:
        """Render the unchanging map art (box backdrop + faint full trace + selected-lap line) once
        into a transparent full-frame-sized ARGB32 image. The polylines are drawn in the same frame
        coordinates the projection produced, so a plain (0, 0) blit lands them exactly where the old
        per-frame draws did — pixel-identical, minus the ~40 ms/frame cost."""
        # The image only needs to span up to the inset box's bottom-right corner; size it to that so
        # a 4 K-aspect frame doesn't allocate a needlessly huge buffer when the inset sits mid-frame.
        w = max(1, int(np.ceil(box.right())) + 2)
        h = max(1, int(np.ceil(box.bottom())) + 2)
        layer = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
        layer.fill(Qt.transparent)
        p = QPainter(layer)
        p.setRenderHint(QPainter.Antialiasing, True)
        # box backdrop
        p.setBrush(_c(C.surface, 180))
        p.setPen(QPen(_c(C.border_strong, 160), 1.2))
        p.drawRoundedRect(box, 8, 8)
        # faint full trace
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(_c(C.text_muted, 120), 1.4))
        p.drawPolyline(trace)
        # selected lap line (amber accent)
        if lap_poly is not None:
            p.setPen(QPen(_c(C.accent, 235), 2.2))
            p.drawPolyline(lap_poly)
        p.end()
        return layer

    def paint(self, p: QPainter, marker_index: int | None) -> None:
        if not self._ok:
            return
        # blit the baked static layer (backdrop + full trace + lap line) — a sub-ms copy that
        # replaces the per-frame re-rasterization of the (huge) trace polyline.
        p.drawImage(0, 0, self._layer)
        # marker dot (warm coral, matches MapView.MARKER_COLOR = C.behind) — the only moving element.
        if marker_index is not None and 0 <= marker_index < len(self._xs):
            m = self._proj(float(self._xs[marker_index]), float(self._ys[marker_index]))
            p.setPen(Qt.NoPen)
            p.setBrush(_c(C.behind, 255))
            p.drawEllipse(m, 5.0, 5.0)
            p.setPen(QPen(_c(C.canvas, 200), 1.0))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(m, 5.0, 5.0)


def _paint_readout(p: QPainter, box: QRectF, vals: OverlayValues) -> None:
    """The always-on Δ / speed readout card (bottom-left). Same content + three-way Δ colour the
    app's diff box uses (theme.delta_colour): "Δ +0.42 s    138 km/h"."""
    from . import theme
    p.setBrush(_c(C.surface, 205))
    p.setPen(QPen(_c(C.border_strong, 170), 1.2))
    p.drawRoundedRect(box, 8, 8)
    pad = box.height() * 0.22
    inner = box.adjusted(pad, 0, -pad, 0)
    delta_txt = "Δ —" if vals.delta_s is None else f"Δ {vals.delta_s:+.2f} s"
    speed_txt = "— km/h" if vals.speed_kmh is None else f"{vals.speed_kmh:.0f} km/h"
    colour = theme.delta_colour(vals.delta_s) or C.text
    fnt = _font(box.height() * 0.46, bold=True)
    p.setFont(fnt)
    # Δ in the cue colour, speed in primary text — two draws so they can differ in colour.
    p.setPen(QPen(_c(colour)))
    p.drawText(inner, Qt.AlignVCenter | Qt.AlignLeft, delta_txt + "     ")
    fm_w = p.fontMetrics().horizontalAdvance(delta_txt + "     ")
    p.setPen(QPen(_c(C.text)))
    p.drawText(inner.adjusted(fm_w, 0, 0, 0), Qt.AlignVCenter | Qt.AlignLeft, speed_txt)


def _paint_strip(p: QPainter, box: QRectF, session, vals: OverlayValues, t0: float) -> None:
    """The lap / sector strip (top-left): the lap label + elapsed-into-lap time, with a progress
    fill marking how far through the lap (by time) the playhead is — a compact at-a-glance bar."""
    p.setBrush(_c(C.surface, 195))
    p.setPen(QPen(_c(C.border_strong, 160), 1.0))
    p.drawRoundedRect(box, 6, 6)
    if vals.lap_id is None:
        return
    win = session.lap_window(vals.lap_id)
    if win is not None:
        ls, le = win
        frac = 0.0 if le <= ls else max(0.0, min(1.0, (vals.t - ls) / (le - ls)))
        fill = QRectF(box.x(), box.y(), box.width() * frac, box.height())
        p.setBrush(_c(C.accent, 70))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(fill, 6, 6)
        elapsed = max(0.0, vals.t - ls)
    else:
        elapsed = max(0.0, vals.t - t0)
    p.setPen(QPen(_c(C.text)))
    p.setFont(_font(box.height() * 0.52, bold=True))
    label = f"LAP {vals.lap_id}   {fmt_time(elapsed)}"
    p.drawText(box.adjusted(box.height() * 0.4, 0, -box.height() * 0.2, 0),
               Qt.AlignVCenter | Qt.AlignLeft, label)


class OverlayPainter:
    """Composites the overlay elements onto each decoded frame. Built ONCE per export (it caches
    the static map-inset geometry + a headless g-meter dial that it drives frame-to-frame with the
    SAME set_lap/set_g sequence the live tick uses, so the burned dial's EMA/envelope evolve
    identically). `paint_frame` mutates the passed QImage in place."""

    def __init__(self, session, spec: ExportSpec, out_w: int, out_h: int):
        self._session = session
        self._spec = spec
        self._w, self._h = out_w, out_h
        cfg = spec.config
        m = cfg.margin_frac * out_h
        # g-meter: square in the TOP-RIGHT.
        gside = cfg.gmeter_frac * out_h
        self._g_rect = QRectF(out_w - m - gside, m, gside, gside)
        # map inset: BOTTOM-RIGHT.
        mw, mh = cfg.map_w_frac * out_w, cfg.map_h_frac * out_h
        self._map = _MapInset(session, QRectF(out_w - m - mw, out_h - m - mh, mw, mh), spec.lap_id)
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
        """Advance the dial ONE tick (sequential, order-dependent — the EMA/envelope accumulate)
        and return an immutable `DialState` snapshot of the resulting filtering state. The render is
        single-threaded, so this just runs in line with the paint; the snapshot split (advance →
        paint-from-snapshot) is kept because it cleanly separates the order-dependent numeric step
        from the stateless drawing and keeps the paint a pure function of its args."""
        self.feed_g(vals)
        return self._dial._dial_state()

    def paint_frame_with_state(self, img: QImage, vals: OverlayValues, dial_state) -> None:
        """Paint all overlay elements onto `img` (an RGB frame at the output size) from a PRECOMPUTED
        `dial_state`. Touches no shared mutable state — a pure function of (img, vals, dial_state).
        `img` is mutated in place."""
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        # g-meter dial: paint into its rect via the SHARED paint routine + the snapshot of the
        # headless dial's filtering state (identical to the on-screen widget).
        p.save()
        p.translate(self._g_rect.topLeft())
        gmeter_overlay.paint_dial(p, self._g_rect.width(), self._g_rect.height(), dial_state)
        p.restore()
        self._map.paint(p, vals.marker_index)
        _paint_readout(p, self._readout_rect, vals)
        _paint_strip(p, self._strip_rect, self._session, vals, self._spec.t0)
        p.end()

    def paint_frame(self, img: QImage, vals: OverlayValues) -> None:
        """Paint all overlay elements onto `img`, advancing the g-meter dial first (so its
        dot/envelope reflect this frame). The simple sequential path — kept for the single-threaded
        render + callers that drive one frame at a time."""
        self.paint_frame_with_state(img, vals, self.advance_and_snapshot(vals))


def _paint_packed_frame(painter: OverlayPainter, out_w: int, out_h: int, raw: bytes,
                        vals: OverlayValues, dial) -> bytes:
    """Composite one decoded rgb24 frame and return the painted bytes PACKED at out_w*3.

    `raw` is one frame PACKED at out_w*3 as ffmpeg emits it; we own a writable copy, wrap it in a
    QImage and paint the overlays from the PRECOMPUTED `dial` snapshot. QImage scanlines are
    4-byte-aligned, so when out_w*3 isn't a multiple of 4 the image carries per-row padding — we
    view the (h, bytesPerLine) buffer and keep the first 3*out_w columns so the bytes handed to the
    encoder are tightly packed (without this a non-4-aligned width would shear every row + desync
    the stream). Free of shared mutable state, so a pool of threads can run it on distinct frames
    concurrently (Qt releases the GIL during rasterization, so the paints actually overlap)."""
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
    """Raised when the render makes NO frame progress for `watchdog_timeout` seconds — i.e. a
    stage WEDGED (a VideoToolbox session that hangs instead of exiting, a stuck pipe, a decoder
    that stopped emitting). Unlike `_EncodeError` (a process that *failed* with a non-zero exit),
    a wedge never "fails", so without this watchdog the export would hang forever (the user's
    symptom). `run` catches it to retry ONCE on the software encoder, then surfaces it as a clear
    error rather than hanging."""


class NoFramesError(RuntimeError):
    """Raised when the decode produced ZERO usable frames — the render "finished" without ever
    advancing the bar. This is the chaptered-export failure mode: a seek that lands past the
    resolved source's end emits no frames, so the loop hits an immediate short read and would
    otherwise report a SILENT 0-frame 'success' (an empty MP4, a dialog that never moved). Treating
    it as a clear error — rather than success or a 30 s watchdog wait — is the defense-in-depth that
    makes 'nothing happened for 2 minutes' impossible even if a bad window slips past the up-front
    guard. The up-front `guard_validate_window` normally catches this first; this is the backstop
    for a source whose duration ffprobe couldn't read."""


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
    """Continuously drain an ffmpeg process's stderr on a daemon thread, keeping only the TAIL.

    Why this exists: ffmpeg writes progress/warnings/errors to stderr, and an OS pipe buffer is
    only ~64 KB. The render loop blocks reading the DECODER's stdout and writing the ENCODER's
    stdin; if either ffmpeg fills its stderr pipe in the meantime and nothing is draining it, that
    ffmpeg BLOCKS on write(stderr) → the whole pipeline deadlocks (and no test that mocks the
    subprocess can catch it). Draining stderr off-thread makes that impossible regardless of how
    chatty ffmpeg gets. We retain a bounded tail so a non-zero exit can still be explained."""

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
        """SINGLE-THREADED pump: composite up to `n` frames in order; return True when the render is
        COMPLETE (outputs finalized). Reads one frame's bytes per iteration from the decoder, paints
        it (advancing the dial sequentially), and writes it to the encoder's stdin.

        This is THE render engine — `run()` simply pumps it to completion (under a no-progress
        watchdog). It is deliberately single-threaded: VideoToolbox runs the H.264 encode on the
        Apple media engine (process-isolated, off the CPU), so an in-line QPainter composite keeps
        up comfortably at the 30 fps default while avoiding the parallel-pipeline deadlock surface
        that could wedge the GUI export. The caller (a QThread) can drive this in chunks for a
        responsive, cancellable progress dialog.

        On a wedge: if the supervisor (see `_start_supervisor`) killed the ffmpeg processes because
        the export stalled or the user cancelled, the blocked `stdout.read`/`stdin.write` returns a
        short read / raises BrokenPipe; we then translate that into the right exception
        (RenderTimeoutError / CancelledError / _EncodeError) so the render never hangs."""
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
        """Spawn the daemon SUPERVISOR thread that makes an infinite hang structurally impossible.

        The render loop blocks on pipe I/O (`stdout.read` / `stdin.write`); a wedged stage (a
        VideoToolbox session that hangs instead of exiting, a stuck pipe, a decoder that stopped)
        would block it FOREVER — there is no timeout on a pipe read/write, and a cancel flag the
        loop only polls *between* frames can't interrupt a write that never returns. The supervisor
        runs alongside and, the instant it sees either condition, KILLS the ffmpeg processes:

          * NO-PROGRESS WATCHDOG: `frames_done` hasn't advanced for `watchdog_timeout` seconds →
            abort "timeout" (then `run` retries once on the software encoder);
          * CANCEL: the caller's `cancel()` returned True → abort "cancel".

        Killing the processes unblocks the loop's read/write at the OS level (EOF / SIGPIPE), and
        `_raise_if_aborted` turns that into a typed exception. A zero/none `watchdog_timeout`
        disables only the stall check (cancel still works)."""
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
                # No-progress watchdog. Armed from render START (here), NOT from the first frame,
                # and deliberately NOT gated on `self._started`: a render that wedges during SETUP
                # (ffmpeg launch) or on the FIRST decode read — e.g. a seek that yields zero frames
                # while ffmpeg slowly demuxes a huge file — would otherwise sit on an empty bar with
                # nothing to catch it (the chaptered-export symptom). `_last_progress_t` is reset
                # only when a real frame is written, so "N s elapsed with 0 frames produced" trips
                # the abort just like a mid-render stall.
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
        """Render to completion (the call the GUI worker makes). `progress(done, total)` is invoked
        as frames are written; `cancel()` -> True aborts cleanly (raises CancelledError after the
        pipes are torn down). Returns a RenderResult.

        ROBUSTNESS — two independent guards so the export NEVER hangs and NEVER silently breaks:
          * a no-progress WATCHDOG (the supervisor) aborts a WEDGED render (a hung VideoToolbox
            session / stuck pipe makes no progress and never "fails", so only a watchdog catches
            it) — then we retry ONCE on the software encoder;
          * a VideoToolbox encode that *fails* with a non-zero exit (`_EncodeError`) also retries
            ONCE on libx264, for a machine where a hardware session won't open.
        Either retry re-runs from a fresh, libx264-forced Renderer (a clean reset of the pipes)."""
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
    """Build the `ExportSpec` for `lap_id`, resolving the VIDEO SOURCE from the session's chapters
    so the export reads the RIGHT footage regardless of which chapter(s) the lap's GLOBAL window
    lands in. Raises ValueError if the lap has no usable window.

    Source resolution (the chaptered-export fix), reusing the player's global<->local mapping:
      * `session.chapters` (a ChapterMap) present -> map the global window to a single chapter file
        (or a concat of the chapters a seam-crossing lap spans) via `resolve_video_source`;
      * no ChapterMap -> the plain single file (`src_path`, else `session.video_path`), offset 0.

    The caller OWNS the returned spec's `source` lifecycle and must call `spec.source.cleanup()`
    when done (it may have written a temp concat-list file). app.py builds the spec this way so it
    can run the Renderer off the UI thread behind a progress dialog."""
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
    """Convenience: build the chapter-aware ExportSpec for `lap_id`'s window and render it to
    completion. Raises ValueError if the lap has no usable window. Used by the headless render path
    + tests; the app builds the spec itself (via `build_lap_spec`) so it can run the Renderer off
    the UI thread with a progress dialog. `src_path` is the fallback single-file source when the
    session has no ChapterMap."""
    spec = build_lap_spec(session, out_path, lap_id, config=config, src_path=src_path)
    try:
        return Renderer(session, spec).run(progress=progress, cancel=cancel)
    finally:
        spec.source.cleanup()
