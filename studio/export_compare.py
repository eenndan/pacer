"""Two-lap COMPARE export, locked to TRACK POSITION rather than to elapsed time.

The app has had side-by-side compare on screen since #20-22; it could never be exported, and an
exported side-by-side is the artifact people actually share. The criticism of the weak ones names
the missing half exactly: two clips started together are "the same thing as having two browser
windows next to each other" — because they are locked to the CLOCK. Ten seconds in, the faster lap
is at the next corner and the two pictures are of different places, so the frame stops being a
comparison at the moment it starts being interesting.

THE PRIMITIVE IS THE RESAMPLE. For each output frame this asks pane A where it is on the lap — the
normalized track fraction s = distance_into_lap / lap_total, the same axis the Δ engine and the
charts align on — and then asks pane B for the instant IT was at that same s. Both panes are
therefore always at the same corner, both clocks run visibly apart, and the gap between them IS the
delta. That is a resample of B onto A's frame grid, not an offset: the faster lap's footage is
consumed slightly fast through the parts it was quick in and slightly slow through the rest,
continuously, and no single time shift can stand in for it.

WHAT THE TWO LAPS BEING DIFFERENT LENGTHS MEANS, SAID PLAINLY. They always are — in seconds AND in
metres (a wide line round a kart track measures tens of metres longer than a tight one). Locking on
NORMALIZED distance is what makes that a non-issue: both laps traverse s in [0, 1] exactly once, so
the export starts with both panes on the start line and ends with both on the finish line, whatever
either lap measured. Neither pane can "run out" of lap, because the mapping is onto the other lap's
OWN window. The clip's length is lap A's time, because lap A is the lap the frames are stamped
from. What DOES vary is how fast B's footage is consumed, and that is the point.

The three things that CAN run out are handled, and none of them is the lap:
  * B's decoded stream ending early (a lap that sits at the very end of its footage) — the last
    decoded frame is HELD rather than the render failing, and `CompareRenderer.held_frames` counts
    it so a caller can report it;
  * B producing nothing at all — that is a broken export, and it raises `NoFramesError` exactly as
    pane A's own zero-frame case does rather than shipping a half-black clip;
  * either lap being degenerate (<2 points, a zero-length odometer) — refused before ffmpeg starts.

THE 765 ms DUAL-SEEK FINDING DOES NOT APPLY HERE, AND THAT WAS MEASURED RATHER THAN ASSUMED. Live
distance-lock was rejected in 2026-06 because seeking to a PRESENTED frame with two QMediaPlayer
decoders alive costs ~765 ms, so holding the lock during playback would have meant a corrective
seek per frame and the picture would strobe. An export never seeks per frame: it opens each pane
ONCE with an accurate `-ss` and then decodes LINEARLY, and the lock is applied by choosing which
already-decoded frame to use — dropping one when B is running slow against A, repeating one when it
is running fast. Two seeks for the whole render, not two per frame, and both of them before the
first frame is composited.

CROSS-RECORDING IS THE SAME CODE. Pane B may come from another Session entirely (the app's
cross-recording compare). Its lap window, its chapter table and — the part that is easy to get
wrong since #266 — its own telemetry->media clock all resolve against THAT session: `t_b` is
`session_b.media_time(...)`, never pane A's conversion. Two recordings' media clocks run at
different rates against their GPS clocks, so borrowing A's would put B's picture up to a fifth of a
second off its own overlay by the end of a long session.

AUDIO IS PANE A'S, ALONE, AND THAT IS A DECISION. Pane B's video is time-warped by the lock, so its
audio would have to be resampled by a continuously varying factor — which pitch-shifts the engine
note, the one thing in a kart video's audio that carries information. Two engine tracks at the same
corner but a second apart in time is mush at best. So the clip carries lap A's own audio, untouched
and in sync with lap A's picture, and pane B is silent. That falls out of inheriting pane A's
`build_encode_cmd`, `-af apad -shortest` and all.

Everything else — the stall watchdog, cooperative cancel, the VideoToolbox->libx264 retry, the
teardown that cannot wedge — is `export_video.Renderer`'s, reached through the extension seams
documented there rather than copied.

THE `_`-PREFIXED IMPORTS BELOW ARE DELIBERATE, and each one is a thing that must not exist twice.
`_media_time` / `_telemetry_time` are the app's only two clock crossings outside `Session`, and a
second duck-typed copy is how a stand-in session starts meaning something different in one exporter
than in the other. `_paint_readout`, `_draw_text`, `_text_at`, `_font` and `_c` are how a burned
overlay LOOKS; a compare clip drawing its speeds in another face would read as another app's
export. `_even` / `_even_down` are the encoder's even-dimension rule. This module is
`export_video`'s subclass, not its neighbour.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, replace

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QImage, QPainter, QPen

from . import theme, units
from ._signal import fmt_time, lap_label
from .export_palette import EXPORT
from .export_video import (
    FIT_CROP,
    FIT_FIT,
    ExportSpec,
    FrameGeometry,
    NoFramesError,
    OverlayConfig,
    OverlayValues,
    Renderer,
    VideoSource,
    _c,
    _draw_text,
    _even,
    _even_down,
    _font,
    _media_time,
    _paint_readout,
    _StderrDrainer,
    _telemetry_time,
    _text_at,
    build_decode_cmd,
    export_delta_colour,
    footage_duration,
    guard_validate_window,
    lap_window_for_export,
    overlay_unit,
    probe_video_size,
    readout_pill_width,
    resolve_video_source,
    single_file_source,
)

# How the two panes are arranged. STACK (over/under) is the default because two 16:9 panes stacked
# make a roughly 8:9 frame — a shape a phone shows nearly full-screen — and because the eye
# compares two pictures most easily across a shared horizontal edge. SIDE puts them left/right,
# the classic broadcast split and what a wide screen wants.
LAYOUT_STACK = "stack"
LAYOUT_SIDE = "side"
LAYOUT_CHOICES = (LAYOUT_STACK, LAYOUT_SIDE)

# `plot_x_at_media_time` returns s * best_distance and `media_time_at_plot_x` divides by the same
# number, so passing 1.0 on BOTH sides makes the plot-x coordinate literally the normalized track
# fraction s. The two laps' differing totals then cancel by construction rather than by a shared
# constant a caller has to remember to pass twice. (It must be non-zero: both accessors read a
# falsy `best_distance` as "no distance axis" and answer None.)
_UNIT_TOTAL = 1.0


# --------------------------------------------------------------------------- configuration
@dataclass(frozen=True)
class CompareConfig(OverlayConfig):
    """`OverlayConfig` plus the knobs a compare render adds.

    It SUBCLASSES rather than wraps so `dataclasses.replace` still returns a CompareConfig — which
    is what the inherited VideoToolbox->libx264 retry does to force the software encoder, and a
    retry that quietly dropped the layout back to the default would render a different clip from
    the one that failed.

    `out_height` means the PANE's short side here, not the frame's: a compare frame is two panes,
    and "1080p" plainly means each half is a 1080p picture rather than a 540p one. `aspect` /
    `frame_fit` and the overlay-only alpha knobs are inherited and NOT used — a compare pane's
    shape is dictated by the footage and the layout, and there is no overlay-only compare (an
    overlay track has no second picture to lock to)."""

    layout: str = LAYOUT_STACK
    # How pane B's picture meets a pane sized from pane A's footage. FIT (pad) is the default
    # because it is a no-op whenever the two sources share an aspect — every same-recording compare
    # and nearly every cross-recording one — and keeps the whole picture when they do not. CROP
    # fills the pane and takes the centre out of the footage instead.
    pane_fit: str = FIT_FIT
    # A compare clip's Δ is its entire message, so it carries the direction arrow the single-lap
    # strip tail leaves to the interactive readout: a shared MP4 has no tooltip to hover, and the
    # arrow is what survives greyscale and a colour-blind viewer who changed no settings.
    delta_arrow: bool = True


# --------------------------------------------------------------------------- frame geometry
@dataclass(frozen=True)
class CompareGeometry:
    """The output frame, the pane inside it, and the `-vf` chain that fills each pane.

    `filter_a`/`filter_b` each produce EXACTLY `pane_w x pane_h` rgb24, which is what lets the
    composite be two array slices rather than a second scaler."""

    out_w: int
    out_h: int
    pane_w: int
    pane_h: int
    filter_a: str
    filter_b: str
    layout: str

    def pane_origin(self, side: int) -> tuple[int, int]:
        """(x, y) of pane `side` (0 = A, 1 = B) within the output frame."""
        if side == 0:
            return (0, 0)
        return (self.pane_w, 0) if self.layout == LAYOUT_SIDE else (0, self.pane_h)

    def pane_rect(self, side: int) -> QRectF:
        x, y = self.pane_origin(side)
        return QRectF(float(x), float(y), float(self.pane_w), float(self.pane_h))


def pane_scale_filter(src_w: int, src_h: int, dst_w: int, dst_h: int,
                      fit: str = FIT_FIT) -> str:
    """The `-vf` chain turning a `src_w x src_h` decoded frame into EXACTLY `dst_w x dst_h`.

    FIT contains the whole picture and pads the remainder black; it degenerates into a plain scale
    whenever the two aspects already agree, which is the normal case. CROP takes the largest
    dst-shaped rectangle out of the SOURCE first and scales that — cropping before scaling rather
    than after is worth three times the render on 4K footage (the same ordering
    `export_video.frame_geometry` measured at 57.3 s against 12-21 s).

    `force_divisible_by=2` under `decrease` rounds the contained picture DOWN, so it can never come
    out a pixel LARGER than the frame it is padded into — a `pad` smaller than its input is a hard
    ffmpeg error rather than a crop."""
    if not (src_w > 0 and src_h > 0):
        return f"scale={dst_w}:{dst_h}"
    if fit == FIT_CROP:
        ratio = dst_w / dst_h
        crop_w, crop_h = ((src_h * ratio, float(src_h)) if (src_w / src_h) > ratio
                          else (float(src_w), src_w / ratio))
        return f"crop={_even_down(crop_w)}:{_even_down(crop_h)},scale={dst_w}:{dst_h}"
    return (f"scale={dst_w}:{dst_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,"
            f"pad={dst_w}:{dst_h}:(ow-iw)/2:(oh-ih)/2:color=black")


def compare_geometry(src_a: tuple[int, int], src_b: tuple[int, int],
                     cfg: CompareConfig) -> CompareGeometry:
    """Resolve the two-pane frame for sources `src_a` / `src_b` (each a (w, h)).

    THE PANE TAKES PANE A'S SHAPE and pane B is fitted into it. A frame whose halves are different
    shapes reads as two unrelated clips; making them agree is most of what makes the picture a
    comparison. Pane A is the reference in every other respect (the frame grid, the clock, the
    audio), so it is the reference here too.

    The pane's SHORT side is `cfg.out_height`, never upscaled past pane A's own footage — the same
    rule and the same reason as the single-lap export, applied one level down because here the
    frame is two panes."""
    aw, ah = int(src_a[0]), int(src_a[1])
    want = max(2, int(cfg.out_height))
    if aw <= 0 or ah <= 0:
        pane_w, pane_h = _even(want * 16 / 9), _even(want)
    elif aw >= ah:
        pane_h = _even(min(want, ah))
        pane_w = _even(pane_h * aw / ah)
    else:
        pane_w = _even(min(want, aw))
        pane_h = _even(pane_w * ah / aw)
    layout = cfg.layout if cfg.layout in LAYOUT_CHOICES else LAYOUT_STACK
    out_w, out_h = (2 * pane_w, pane_h) if layout == LAYOUT_SIDE else (pane_w, 2 * pane_h)
    return CompareGeometry(
        out_w=out_w, out_h=out_h, pane_w=pane_w, pane_h=pane_h,
        filter_a=pane_scale_filter(aw, ah, pane_w, pane_h, cfg.pane_fit),
        filter_b=pane_scale_filter(int(src_b[0]), int(src_b[1]), pane_w, pane_h, cfg.pane_fit),
        layout=layout)


# --------------------------------------------------------------------------- the distance lock
@dataclass(frozen=True)
class LockedTrack:
    """The per-output-frame answer to "where are both panes, and how far apart are they".

    Every array holds one entry per OUTPUT frame, on pane A's frame grid:
      * `fraction`   — s in [0, 1], the normalized track position BOTH panes are held at;
      * `t_b_media`  — pane B's media time, on pane B's session's OWN media clock;
      * `elapsed_a` / `elapsed_b` — seconds into each lap at that s. Their difference is the gap,
        and it is `Session.delta_between(lap_a, lap_b, t)` by construction: the same
        normalized-distance projection, spelled with the public cursor mappers.
    """

    fraction: np.ndarray
    t_b_media: np.ndarray
    elapsed_a: np.ndarray
    elapsed_b: np.ndarray
    speed_a: np.ndarray
    speed_b: np.ndarray

    @property
    def delta(self) -> np.ndarray:
        """Lap A's gap to lap B at the same track position: negative = A is ahead."""
        return self.elapsed_a - self.elapsed_b


def _lap_span(session, lap_id: int) -> tuple[float, float]:
    """A lap's TELEMETRY window as (start, span). Raises if the lap has no usable window."""
    win = session.lap_window(lap_id)
    if win is None:
        raise ValueError(f"lap {lap_label(lap_id)} has no window to compare")
    t0, t1 = float(win[0]), float(win[1])
    if not (t1 > t0):
        raise ValueError(f"lap {lap_label(lap_id)} has an empty window")
    return t0, t1 - t0


def _require_distance_axis(session, lap_id: int, t_probe: float) -> None:
    """Refuse a lap that cannot be placed on a distance axis — <2 points, or an odometer that never
    advances (a stationary "lap").

    IT HAS TO BE ASKED IN THE FORWARD DIRECTION, which is why this is its own step rather than a
    None check on the inverse. `plot_x_at_media_time` guards `dists[-1] <= 0` and answers None;
    `media_time_at_plot_x` has no such guard — it multiplies the fraction by a zero total and
    interpolates on a flat axis, which returns a perfectly ordinary-looking number. So a degenerate
    lap B would sail through the inverse and export as a pane frozen on its first frame, with a Δ
    that looks like a real measurement. Asked forwards, it is refused before any ffmpeg starts."""
    if session.plot_x_at_media_time(lap_id, float(t_probe), "distance", _UNIT_TOTAL) is None:
        raise ValueError(
            f"lap {lap_label(lap_id)} cannot be placed on a distance axis (too few points, or a "
            f"zero-length odometer) — there is no track position to lock to")


def _speed_at(session, tt: float) -> float | None:
    """km/h at telemetry time `tt`, through the SAME accessors the live readout and the single-lap
    export use, so both panes' numbers are the app's numbers."""
    tv = getattr(session, "tv", None)
    if tv is None or not len(tv):
        return None
    i = session.index_at_time(tt)
    return float(tv[i]) if i is not None else None


def lock_to_track(session_a, lap_a: int, session_b, lap_b: int, media_times_a) -> LockedTrack:
    """Lock lap B onto lap A's frame grid by TRACK POSITION.

    `media_times_a` are the output frames' media times on session A's clock (what `frame_times`
    produced). For each, this walks A's media time -> A's telemetry time -> the normalized track
    fraction s -> lap B's telemetry time at the SAME s -> pane B's media time on SESSION B's clock.

    THE THREE CLOCK CROSSINGS ARE THE WHOLE CORRECTNESS. A frame time is a MEDIA time and every
    Session series is indexed on the TELEMETRY (GPS9 true) clock; the two drift apart by up to
    0.22 s over a long recording, by a different amount in each recording. So A converts with A's
    clock, B converts back with B's own, and nothing in between ever mixes them.

    The cursor mappers clamp at both ends of a lap, so s lands in [0, 1] and `t_b` inside lap B's
    window with no special case — which is also the answer to "what if one lap is shorter": there
    is no running out, only a different amount of B's footage consumed per output frame.

    Raises ValueError when either lap is degenerate (the mappers answer None), rather than silently
    exporting a pane that never moves."""
    times_a = np.asarray(media_times_a, dtype=float)
    a_start, a_span = _lap_span(session_a, lap_a)
    b_start, b_span = _lap_span(session_b, lap_b)
    _require_distance_axis(session_a, lap_a, a_start)
    _require_distance_axis(session_b, lap_b, b_start)
    n = len(times_a)
    fraction = np.empty(n, dtype=float)
    t_b_media = np.empty(n, dtype=float)
    elapsed_a = np.empty(n, dtype=float)
    elapsed_b = np.empty(n, dtype=float)
    speed_a = np.full(n, np.nan, dtype=float)
    speed_b = np.full(n, np.nan, dtype=float)
    for i, t in enumerate(times_a):
        tt_a = _telemetry_time(session_a, float(t))
        s = session_a.plot_x_at_media_time(lap_a, tt_a, "distance", _UNIT_TOTAL)
        if s is None:
            raise ValueError(
                f"lap {lap_label(lap_a)} cannot be placed on a distance axis (too few points, or a "
                f"zero-length odometer) — there is no track position to lock to")
        s = min(max(float(s), 0.0), 1.0)
        tt_b = session_b.media_time_at_plot_x(lap_b, s, "distance", _UNIT_TOTAL)
        if tt_b is None:
            raise ValueError(
                f"lap {lap_label(lap_b)} cannot be placed on a distance axis (too few points, or a "
                f"zero-length odometer) — there is no track position to lock to")
        tt_b = float(tt_b)
        fraction[i] = s
        t_b_media[i] = _media_time(session_b, tt_b)
        elapsed_a[i] = min(max(tt_a - a_start, 0.0), a_span)
        elapsed_b[i] = min(max(tt_b - b_start, 0.0), b_span)
        sa = _speed_at(session_a, tt_a)
        sb = _speed_at(session_b, tt_b)
        if sa is not None:
            speed_a[i] = sa
        if sb is not None:
            speed_b[i] = sb
    return LockedTrack(fraction=fraction, t_b_media=t_b_media, elapsed_a=elapsed_a,
                       elapsed_b=elapsed_b, speed_a=speed_a, speed_b=speed_b)


# --------------------------------------------------------------------------- the spec
@dataclass
class CompareSpec(ExportSpec):
    """An `ExportSpec` for pane A, plus everything pane B needs.

    Inheriting is what lets the whole inherited pipeline — `build_decode_cmd`, `build_encode_cmd`,
    `guard_validate_window`, `local_t0`, the audio map — keep describing PANE A without a second
    vocabulary for the same things. `t_b0`/`t_b1` are lap B's media window on SESSION B's clock,
    which is a different clock from `t0`/`t1` whenever the panes come from different recordings;
    they are never compared with A's and only ever handed to B's own decoder.

    The caller owns both sources and must `cleanup()` when the render is done."""

    lap_b: int = -1
    source_b: VideoSource | None = None
    t_b0: float = 0.0
    t_b1: float = 0.0
    cross: bool = False
    label_a: str = ""
    label_b: str = ""

    def __post_init__(self):
        super().__post_init__()
        if self.source_b is None:
            raise ValueError("a compare export needs a video source for pane B")
        if self.config.overlay_only:
            # An overlay-only render composites onto transparency with no footage under it. There
            # is no second picture to lock to, so a "distance-locked overlay track" would be an
            # ordinary overlay track with a misleading name.
            raise ValueError("a compare export has no overlay-only (alpha) mode")
        if not self.label_a:
            self.label_a = f"LAP {lap_label(self.lap_id)}"
        if not self.label_b:
            self.label_b = f"LAP {lap_label(self.lap_b)}"

    def spec_b(self, tail: float = 0.0) -> ExportSpec:
        """Pane B's window as a plain `ExportSpec`, so B's decode is built by exactly the same
        `build_decode_cmd` (accurate `-ss`, concat span across a chapter seam, hwaccel) and
        refused by exactly the same `guard_validate_window` as pane A's.

        `tail` extends the decode a couple of frames past the finish line. `frame_times` emits
        `ceil(duration*fps)` frames while the lock's last frame index is `round(duration*fps)`, so
        a window whose frame count rounds UP asks for one frame more than an exact-length decode
        produces. Two frames of slack cost nothing and remove the whole edge case."""
        return ExportSpec(out_path="", lap_id=self.lap_b, t0=self.t_b0,
                          t1=self.t_b1 + max(0.0, float(tail)),
                          source=self.source_b, config=self.config)

    def cleanup(self) -> None:
        """Free both panes' temp concat lists. Idempotent."""
        for src in (self.source, self.source_b):
            if src is not None:
                src.cleanup()


def _source_for(session, t0: float, t1: float, src_path: str | None) -> VideoSource:
    """The VideoSource for a GLOBAL media window: through the session's ChapterMap when it has one
    (a single chapter, or a concat span across a seam), else the plain single file."""
    chapter_map = getattr(session, "chapters", None)
    if chapter_map is not None and getattr(chapter_map, "chapters", None):
        return resolve_video_source(chapter_map, t0, t1)
    path = src_path or getattr(session, "video_path", None)
    if not path:
        raise ValueError("session has no video source to export")
    return single_file_source(path)


def build_compare_spec(session_a, out_path: str, lap_a: int, lap_b: int,
                       session_b=None, config: CompareConfig | None = None,
                       src_path_a: str | None = None, src_path_b: str | None = None,
                       label_a: str = "", label_b: str = "") -> CompareSpec:
    """Build the `CompareSpec` for lap `lap_a` (pane A) against `lap_b` (pane B).

    `session_b` is pane B's Session — the same object for an ordinary compare, another recording's
    for a cross-recording one. Each pane's window comes from ITS OWN session through
    `lap_window_for_export`, the funnel that converts a telemetry lap window onto that recording's
    media clock and clamps it to that recording's footage; each pane's source is then resolved from
    ITS OWN chapter table.

    THERE IS NO LEAD-IN/LEAD-OUT ARGUMENT, DELIBERATELY. Padding is footage OUTSIDE the lap, where
    there is no track position to lock to: s would pin at 0 or 1 and pane B would simply freeze. A
    padded compare is a distance-locked clip with un-locked ends — the exact half-honest artifact
    this feature exists to replace.

    Raises ValueError if either lap has no usable window."""
    session_b = session_b if session_b is not None else session_a
    cfg = config or CompareConfig()
    win_a = lap_window_for_export(session_a, lap_a)
    if win_a is None:
        raise ValueError(f"lap {lap_label(lap_a)} has no usable export window")
    win_b = lap_window_for_export(session_b, lap_b)
    if win_b is None:
        raise ValueError(f"lap {lap_label(lap_b)} has no usable export window")
    a0, a1 = win_a
    b0, b1 = win_b
    source_a = _source_for(session_a, a0, a1, src_path_a)
    try:
        source_b = _source_for(session_b, b0, b1, src_path_b)
    except Exception:
        source_a.cleanup()
        raise
    return CompareSpec(out_path=out_path, lap_id=lap_a, t0=a0, t1=a1, source=source_a,
                       config=cfg, lap_b=lap_b, source_b=source_b, t_b0=b0, t_b1=b1,
                       cross=session_b is not session_a, label_a=label_a, label_b=label_b)


# --------------------------------------------------------------------------- pane B's stream
class _PaneStream:
    """Pane B's decoder, and the frame-stepping that IS the distance lock.

    ONE linear decode at the output fps, so B's stream is a uniform grid starting at its window's
    t0: frame j is the picture at `t0 + j/fps`. `frame_at(k)` walks FORWARD to frame k, dropping
    the frames in between (B running slow against A) or returning the frame it already holds (B
    running fast). k is monotone non-decreasing by construction — both laps' odometers are
    monotone, so the map from A's frames to B's is too — which is exactly why one forward pass
    suffices and no seeking is ever needed.

    At end of stream the last decoded frame is HELD rather than the render failing: B's window is
    clamped to its own footage, so a lap at the very end of a recording can come up a frame or two
    short, and throwing away an otherwise-correct 90-second clip over that is the wrong trade.
    `held` counts them so the caller can say so."""

    def __init__(self, spec: ExportSpec, pane_w: int, pane_h: int, fps: float,
                 hwaccel: bool, scale_filter: str):
        self._spec = spec
        self._pane_w = int(pane_w)
        self._pane_h = int(pane_h)
        self._bytes = int(pane_w) * int(pane_h) * 3
        self._fps = float(fps)
        self._hwaccel = bool(hwaccel)
        self._filter = scale_filter
        self.proc: subprocess.Popen | None = None
        self._err: _StderrDrainer | None = None
        self._k = -1                       # index of the frame currently held
        self._cur: bytes | None = None
        self._eof = False
        self.held = 0                      # output frames served after the stream ran out
        self.decoded = 0                   # frames actually read off the pipe

    @property
    def cmd(self) -> list[str]:
        return build_decode_cmd(self._spec, self._pane_w, self._pane_h, self._fps,
                                self._hwaccel, self._filter)

    def start(self) -> None:
        self.proc = subprocess.Popen(self.cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        # Drained off-thread for the same reason pane A's is: a chatty ffmpeg that fills its ~64 KB
        # stderr pipe would block while the render loop is busy on the frame pipes.
        stderr = getattr(self.proc, "stderr", None)
        self._err = _StderrDrainer(stderr) if stderr is not None else None

    def index_for(self, t_media: float) -> int:
        """Which frame of B's uniform stream shows media time `t_media`. Rounds to the NEAREST
        frame rather than flooring: flooring would bias every pane-B frame half a frame late, which
        at 30 fps is 16.7 ms of systematic lag added to a lock whose whole claim is that it has
        none."""
        return max(0, int(round((float(t_media) - self._spec.t0) * self._fps)))

    def frame_at(self, k: int) -> bytes | None:
        """The pane-B picture for stream frame `k`, or None if the stream never produced one."""
        stdout = self.proc.stdout if self.proc is not None else None
        while self._k < k and not self._eof and stdout is not None:
            raw = stdout.read(self._bytes)
            if not raw or len(raw) < self._bytes:
                self._eof = True
                break
            self._cur = raw
            self._k += 1
            self.decoded += 1
        if self._eof and self._k < k:
            self.held += 1
        return self._cur

    def close(self) -> None:
        """Release the stderr drainer. The render's own `_finish`/`cancel` reap the process — it is
        in `_extra_procs`, so the watchdog can kill it too."""
        if self._err is not None:
            self._err.join(timeout=1.0)


# --------------------------------------------------------------------------- the overlay
@dataclass(frozen=True)
class CompareFrame:
    """Everything ONE output frame says, resolved before the render starts.

    Both the drawn value and the drawn STRING are carried for the speeds: the painter wants the
    value (it formats through the app's own `theme.speed_number`, so the two exports cannot drift),
    and the pill budget wants the string, measured over every frame."""

    fraction: float
    delta: float
    caption_a: str
    caption_b: str
    speed_a_kmh: float | None
    speed_b_kmh: float | None
    speed_a: str
    speed_b: str
    delta_run: str


# Layout fractions, all of the PANE's short side (`overlay_unit`), so a pane composes the same way
# at any size and the two panes compose identically to each other.
_CAPTION_H_FRAC = 0.042       # the "LAP n   m:ss.mmm" pill
_READOUT_H_FRAC = 0.040       # the speed pill — the single-lap export's own fraction
_MARGIN_FRAC = 0.024
_DELTA_H_FRAC = 0.062         # the gap pill: the biggest element in the frame, because it is the point
_PROGRESS_H_FRAC = 0.009      # the track-position bar along the bottom edge
_PILL_PAD_FRAC = 0.34         # a pill's inner left/right padding, as a fraction of its height
_DELTA_GAP_K = 10.0           # "LAP 12 vs LAP 7" -> the Δ, in k units (k = pill height / 44)


class ComparePainter:
    """Paints the compare overlay onto a composited two-pane frame.

    EVERY PILL IS SIZED FROM THE STRINGS THE RENDER WILL ACTUALLY DRAW, measured once over all
    frames rather than estimated from the series behind them. The single-lap exporter learned that
    the hard way — a budget re-derived under a different sampling convention disagreed with the ink
    twice, by +9.57 px and +6.59 px — and a compare render makes it easy, because the lock is
    already precomputed and so is every string that depends on it. The widths are therefore exact
    AND constant, so no pill breathes as the numbers change.

    WHERE THINGS SIT IS DECIDED BY THE LAYOUT, not by taste: the gap pill goes ON the seam when the
    panes are stacked (it belongs to neither pane, and the seam is the one place that is true of),
    and at the bottom centre when they are side by side, where the seam runs through the middle of
    both pictures. In the side-by-side layout pane B's own elements mirror to the OUTER corner, so
    the centre stays clear for the gap.

    Qt paint devices only (QImage/QPainter): a render runs on a worker thread, where no QWidget may
    be constructed."""

    def __init__(self, geo: CompareGeometry, cfg: CompareConfig, frames: list[CompareFrame],
                 prefix: str):
        self._geo = geo
        self._cfg = cfg
        self._prefix = prefix
        unit = overlay_unit(geo.pane_w, geo.pane_h)
        self._unit = unit
        self._margin = unit * _MARGIN_FRAC
        self._caption_h = unit * _CAPTION_H_FRAC
        self._readout_h = unit * _READOUT_H_FRAC
        self._delta_h = unit * _DELTA_H_FRAC
        self._progress_h = max(1.0, unit * _PROGRESS_H_FRAC)
        self._caption_font = _font(self._caption_h * 0.54, bold=True)
        self._prefix_font = _font(self._delta_h * 0.34, bold=True)
        self._delta_font = _font(self._delta_h * 0.56, bold=True)
        # --- the pill budget: the max over this render's own strings ---
        fm_cap = QFontMetricsF(self._caption_font)
        self._caption_w = 2 * self._caption_h * _PILL_PAD_FRAC + max(
            (fm_cap.horizontalAdvance(t) for f in frames for t in (f.caption_a, f.caption_b)),
            default=0.0)
        self._readout_w = readout_pill_width(
            self._readout_h, [f.speed_a for f in frames] + [f.speed_b for f in frames],
            units.speed_label(cfg.speed_unit))
        k = self._delta_h / 44.0
        self._delta_w = (2 * self._delta_h * _PILL_PAD_FRAC
                         + QFontMetricsF(self._prefix_font).horizontalAdvance(prefix)
                         + _DELTA_GAP_K * k
                         + max((QFontMetricsF(self._delta_font).horizontalAdvance(f.delta_run)
                                for f in frames), default=0.0))

    # ------------------------------------------------------------------ geometry
    def _mirror(self, side: int) -> bool:
        """Whether pane `side`'s corner elements hug its OUTER edge. Only pane B, only side by
        side — where the gap pill straddles the seam and a left-aligned pane-B readout would sit
        under it."""
        return side == 1 and self._geo.layout == LAYOUT_SIDE

    def _corner(self, pane: QRectF, side: int, w: float, h: float, top: bool) -> QRectF:
        x = (pane.right() - self._margin - w) if self._mirror(side) else (pane.x() + self._margin)
        y = (pane.y() + self._margin) if top else (pane.bottom() - self._margin - h)
        return QRectF(x, y, w, h)

    def delta_box(self) -> QRectF:
        """Where the gap is stated. Public because the geometry is the composition decision and a
        test that checks the pill clears the other elements should read it, not re-derive it."""
        x = (self._geo.out_w - self._delta_w) / 2.0
        if self._geo.layout == LAYOUT_SIDE:
            y = self._geo.out_h - self._progress_h - self._margin - self._delta_h
        else:
            y = self._geo.pane_h - self._delta_h / 2.0
        return QRectF(x, y, self._delta_w, self._delta_h)

    # ------------------------------------------------------------------ painting
    def paint(self, img: QImage, frame: CompareFrame) -> None:
        p = QPainter(img)
        try:
            p.setRenderHint(QPainter.Antialiasing, True)
            self._paint_seam(p)
            panes = ((frame.caption_a, frame.speed_a_kmh), (frame.caption_b, frame.speed_b_kmh))
            for side, (caption, speed) in enumerate(panes):
                rect = self._geo.pane_rect(side)
                self._paint_caption(p, rect, side, caption)
                self._paint_speed(p, rect, side, speed)
            self._paint_delta(p, frame)
            self._paint_progress(p, frame.fraction)
        finally:
            p.end()

    def _paint_seam(self, p: QPainter) -> None:
        """The line between the panes — a bright hairline over a wider dark one, so it reads
        against both a pale sky and a dark hedge without a box behind it (the halo idea every other
        burned element uses)."""
        w = max(1.0, self._unit * 0.0022)
        if self._geo.layout == LAYOUT_SIDE:
            x = float(self._geo.pane_w)
            a, b = QPointF(x, 0.0), QPointF(x, float(self._geo.out_h))
        else:
            y = float(self._geo.pane_h)
            a, b = QPointF(0.0, y), QPointF(float(self._geo.out_w), y)
        p.setPen(QPen(_c(EXPORT.halo, 200), w * 3.0))
        p.drawLine(a, b)
        p.setPen(QPen(_c(EXPORT.text, 190), w))
        p.drawLine(a, b)

    def _pill(self, p: QPainter, box: QRectF) -> None:
        """The slim dark plate every run sits on — the same shape, alpha and corner radius as the
        single-lap export's readout and lap strip, so one clip does not look like another app's."""
        k = box.height() / 44.0
        p.setBrush(_c(EXPORT.halo, 165))
        p.setPen(QPen(_c(EXPORT.text, 55), 1.0 * k))
        p.drawRoundedRect(box, 8 * k, 8 * k)

    def _paint_caption(self, p: QPainter, pane: QRectF, side: int, text: str) -> None:
        """WHICH lap this pane is, and ITS OWN running clock. Two clocks at the same corner reading
        different times is the whole feature said without words; the Δ pill is the same statement
        as a number."""
        box = self._corner(pane, side, self._caption_w, self._caption_h, top=True)
        self._pill(p, box)
        pad = self._caption_h * _PILL_PAD_FRAC
        _text_at(p, box.adjusted(pad, 0, -pad, 0), Qt.AlignVCenter | Qt.AlignLeft, text,
                 self._caption_font, EXPORT.text, halo=2.2 * (self._caption_h / 44.0))

    def _paint_speed(self, p: QPainter, pane: QRectF, side: int, speed_kmh: float | None) -> None:
        box = self._corner(pane, side, self._readout_w, self._readout_h, top=False)
        # `_paint_readout` IS the single-lap export's bottom-left speed pill, reused rather than
        # re-drawn so the two exports cannot drift in how a speed is stated. It reads only
        # `speed_kmh` and `lap_id` off the values object; a non-None lap is what makes it print a
        # number instead of the no-lap em dash, and every frame of a compare export is inside both
        # laps by construction.
        vals = OverlayValues(t=0.0, lap_id=0, speed_kmh=speed_kmh, delta_s=None, g=None,
                             marker_index=None)
        _paint_readout(p, box, vals, self._cfg.speed_unit)

    def _paint_delta(self, p: QPainter, frame: CompareFrame) -> None:
        box = self.delta_box()
        self._pill(p, box)
        k = self._delta_h / 44.0
        pad = self._delta_h * _PILL_PAD_FRAC
        inner = box.adjusted(pad, 0, -pad, 0)
        fm_big = QFontMetricsF(self._delta_font)
        fm_small = QFontMetricsF(self._prefix_font)
        base = inner.y() + (inner.height() + fm_big.ascent() - fm_big.descent()) / 2.0
        # The prefix names WHICH pane the sign is about — a signed number between two pictures is
        # ambiguous without it, and the colour cannot say it either.
        _draw_text(p, QPointF(inner.x(), base - (fm_big.ascent() - fm_small.ascent()) * 0.5),
                   self._prefix, self._prefix_font, EXPORT.text_dim, halo=1.8 * k)
        x = inner.x() + fm_small.horizontalAdvance(self._prefix) + _DELTA_GAP_K * k
        _draw_text(p, QPointF(x, base), frame.delta_run, self._delta_font,
                   export_delta_colour(frame.delta, self._cfg.palette), halo=2.6 * k)

    def _paint_progress(self, p: QPainter, fraction: float) -> None:
        """The track-position bar: how far round the lap BOTH panes are. It is the lock made
        visible — ONE bar for two panes, because under the lock there is only one position."""
        y = self._geo.out_h - self._progress_h
        p.setPen(Qt.NoPen)
        p.setBrush(_c(EXPORT.halo, 170))
        p.drawRect(QRectF(0.0, y, float(self._geo.out_w), self._progress_h))
        p.setBrush(_c(EXPORT.accent, 225))
        p.drawRect(QRectF(0.0, y, self._geo.out_w * min(max(fraction, 0.0), 1.0),
                          self._progress_h))


def build_frames(spec: CompareSpec, lock: LockedTrack) -> list[CompareFrame]:
    """Every string and number the overlay will draw, for every frame, up front.

    Precomputing is not an optimisation: it is what lets the pill widths be the exact maximum over
    what is drawn instead of an estimate of it, and it is free here because the lock is already a
    precomputed array."""
    cfg = spec.config
    arrow = bool(getattr(cfg, "delta_arrow", True))
    unit = cfg.speed_unit
    frames = []
    for i in range(len(lock.fraction)):
        d = float(lock.delta[i])
        sa = _finite(lock.speed_a[i])
        sb = _finite(lock.speed_b[i])
        frames.append(CompareFrame(
            fraction=float(lock.fraction[i]),
            delta=d,
            caption_a=f"{spec.label_a}   {fmt_time(float(lock.elapsed_a[i]))}",
            caption_b=f"{spec.label_b}   {fmt_time(float(lock.elapsed_b[i]))}",
            speed_a_kmh=sa,
            speed_b_kmh=sb,
            # lap_id 0 (never None) so the formatter prints the number: every compare frame is
            # inside both laps, which is what the lock guarantees.
            speed_a=theme.speed_number(sa, 0, unit),
            speed_b=theme.speed_number(sb, 0, unit),
            delta_run=theme.format_delta_run(d, units=False, arrow=arrow),
        ))
    return frames


def _finite(v) -> float | None:
    return None if not np.isfinite(v) else float(v)


# --------------------------------------------------------------------------- the renderer
class CompareRenderer(Renderer):
    """The two-pane, distance-locked render.

    Everything hard is `Renderer`'s and reached through its documented seams: the stall watchdog,
    cooperative cancel, the VideoToolbox->libx264 retry, the teardown ordering that stops a decoder
    blocking on a closed pipe. What is added here is a second decoder, the resample that drives it,
    and a painter for two panes.

    TWO DECODERS, TWO SEEKS, FOR THE WHOLE RENDER. Pane A is the inherited decode; pane B is a
    `_PaneStream`, opened once with the same accurate `-ss` and then read strictly forward. The
    live compare mode could not work this way — holding the lock during PLAYBACK means a corrective
    seek per frame, measured at ~765 ms with two decoders alive — but an export does not play, it
    streams, and a stream can be resampled for free."""

    # Two frames of decode slack on pane B — see `CompareSpec.spec_b`.
    _B_TAIL_FRAMES = 2.0

    def __init__(self, session_a, spec: CompareSpec, session_b=None):
        self._session_b = session_b if session_b is not None else session_a
        # Set BEFORE the base constructor: `_extra_procs` is what the teardown and the watchdog
        # sweep, and it must answer "nothing yet" from the first moment this object exists rather
        # than raising on a half-built one.
        self._pane_b: _PaneStream | None = None
        # Pane B's window gets the SAME up-front refusal pane A's does. Without it, a bad B window
        # is discovered only as a decoder that emits nothing, minutes into a render.
        guard_validate_window(spec.spec_b())
        super().__init__(session_a, spec)

    # ---- the inherited seams ----
    def _resolve_geometry(self, src_w: int, src_h: int) -> FrameGeometry:
        b_w, b_h, _ = probe_video_size(self._spec.source_b.probe_path)
        self._geo = compare_geometry((src_w, src_h), (b_w, b_h), self._spec.config)
        # The inherited contract is (frame, filter). The FRAME is both panes; the FILTER produces
        # one — pane A's — because each decoder fills half the picture.
        return FrameGeometry(self._geo.out_w, self._geo.out_h, self._geo.filter_a)

    def _make_painter(self) -> ComparePainter:
        self._lock = lock_to_track(self._session, self._spec.lap_id,
                                   self._session_b, self._spec.lap_b, self._times)
        self._frames = build_frames(self._spec, self._lock)
        return ComparePainter(self._geo, self._spec.config, self._frames,
                              f"{self._spec.label_a} vs {self._spec.label_b}")

    def _extra_procs(self) -> tuple:
        proc = self._pane_b.proc if self._pane_b is not None else None
        return (proc,) if proc is not None else ()

    def _respawn(self, spec: ExportSpec) -> CompareRenderer:
        return type(self)(self._session, spec, self._session_b)

    def _start(self) -> None:
        self._pane_bytes = self._geo.pane_w * self._geo.pane_h * 3
        super()._start()
        self._pane_b = _PaneStream(self._b_spec(), self._geo.pane_w, self._geo.pane_h, self._fps,
                                   self._hwaccel, self._geo.filter_b)
        self._pane_b.start()

    def _b_spec(self) -> ExportSpec:
        """Pane B's decode window, with the two-frame tail clamped to B's own footage so the slack
        can never ask ffmpeg for picture that does not exist."""
        tail = self._B_TAIL_FRAMES / self._fps
        spec_b = self._spec.spec_b(tail)
        total = footage_duration(self._session_b)
        if total is not None and spec_b.t1 > total:
            spec_b = replace(spec_b, t1=max(self._spec.t_b1, float(total)))
        return spec_b

    def _read_source_frame(self) -> bytearray | None:
        """One frame of each pane, assembled into the two-pane frame.

        Returns a BYTEARRAY, not bytes, and that is worth a line: a 1920x2160 frame is 12.4 MB, and
        the composite -> paint -> encode path would otherwise copy it three times per frame (stack,
        `tobytes`, and the painter's own `bytearray`). Writing the two panes straight into one
        mutable buffer that `_compose_frame` then paints in place leaves ONE."""
        raw_a = self._dec.stdout.read(self._pane_bytes)
        if not raw_a or len(raw_a) < self._pane_bytes:
            return None
        raw_b = self._pane_b.frame_at(
            self._pane_b.index_for(float(self._lock.t_b_media[self._i])))
        if raw_b is None:
            # Pane B decoded NOTHING. That is not a tail-of-stream nicety to hold through, it is a
            # broken export — the failure pane A's zero-frame case raises, said in B's terms.
            raise NoFramesError(
                "the compare export's second pane produced no frames — lap "
                f"{lap_label(self._spec.lap_b)} does not map onto any footage. Nothing was written.")
        buf = bytearray(self._frame_bytes)
        pane_row = self._geo.pane_w * 3
        out = np.frombuffer(buf, dtype=np.uint8).reshape(self._out_h, self._out_w * 3)
        a = np.frombuffer(raw_a, dtype=np.uint8).reshape(self._geo.pane_h, pane_row)
        b = np.frombuffer(raw_b, dtype=np.uint8).reshape(self._geo.pane_h, pane_row)
        # Row-major, so pane B is a COLUMN range of every row side by side and a ROW range of the
        # frame over/under. Neither needs a per-pixel loop or a second scaler.
        if self._geo.layout == LAYOUT_SIDE:
            out[:, :pane_row] = a
            out[:, pane_row:] = b
        else:
            out[:self._geo.pane_h] = a
            out[self._geo.pane_h:] = b
        return buf

    def _compose_frame(self, raw: bytearray) -> bytes:
        """Paint the compare overlay onto the two-pane frame and hand back bytes PACKED at
        out_w*3 — QImage scanlines are 4-byte aligned, so a non-4-aligned row has to have its
        padding stripped or every row shears and the pipe desyncs."""
        buf = raw if isinstance(raw, bytearray) else bytearray(raw)
        img = QImage(buf, self._out_w, self._out_h, 3 * self._out_w, QImage.Format_RGB888)
        self._painter.paint(img, self._frames[self._i])
        bpl = img.bytesPerLine()
        if bpl == 3 * self._out_w:
            return bytes(buf)
        arr = np.frombuffer(img.constBits(), dtype=np.uint8,
                            count=bpl * self._out_h).reshape(self._out_h, bpl)
        return arr[:, : 3 * self._out_w].tobytes()

    def _finish(self) -> None:
        if self._done:
            return
        super()._finish()
        if self._pane_b is not None:
            self._pane_b.close()

    # ---- diagnostics ----
    @property
    def geometry(self) -> CompareGeometry:
        return self._geo

    @property
    def lock(self) -> LockedTrack:
        """The resample this render is using — the per-frame track fraction and pane-B media time.
        Exposed so a caller (or a test) can check the lock against the two sessions independently
        instead of trusting the picture."""
        return self._lock

    @property
    def held_frames(self) -> int:
        """How many output frames reused pane B's last decoded picture because B's stream ran out.
        0 for a healthy export; a handful only when lap B sits at the very end of its footage."""
        return self._pane_b.held if self._pane_b is not None else 0


def render_compare(session_a, out_path: str, lap_a: int, lap_b: int,
                   session_b=None, config: CompareConfig | None = None,
                   src_path_a: str | None = None, src_path_b: str | None = None,
                   label_a: str = "", label_b: str = "",
                   progress=None, cancel=None):
    """Build the compare spec and render it to completion (headless / tests / the app's worker).

    Returns the inherited `RenderResult`; both panes' temp sources are cleaned up either way."""
    spec = build_compare_spec(session_a, out_path, lap_a, lap_b, session_b=session_b,
                              config=config, src_path_a=src_path_a, src_path_b=src_path_b,
                              label_a=label_a, label_b=label_b)
    try:
        return CompareRenderer(session_a, spec, session_b).run(progress=progress, cancel=cancel)
    finally:
        spec.cleanup()
