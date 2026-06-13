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

import shutil
import subprocess
import threading
from dataclasses import dataclass, field

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
    fps: float | None = None          # output fps; None = keep the source frame rate
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


# --------------------------------------------------------------------------- export spec
@dataclass
class ExportSpec:
    """Everything a render needs, resolved up front so the render loop is pure mechanism.

    `t0`/`t1` are the MEDIA-clock window (seconds) to export — normally a lap's window from
    `lap_window_for_export`. `lap_id` is the lap whose Δ baseline + sector strip are shown (and
    whose g-meter envelope scope is pinned). `src_path` is the source MP4; `out_path` the MP4 to
    write. `config` carries the layout/output knobs."""
    src_path: str
    out_path: str
    lap_id: int
    t0: float
    t1: float
    config: OverlayConfig = field(default_factory=OverlayConfig)

    @property
    def duration(self) -> float:
        return max(0.0, self.t1 - self.t0)


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


def build_decode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float) -> list[str]:
    """The DECODE ffmpeg argv: seek to t0 BEFORE the input (fast keyframe seek) and AGAIN trim by
    duration, scale to (out_w, out_h), force the constant output `fps`, emit rgb24 rawvideo to
    stdout. `-an`/`-sn`/`-dn` drop audio/subs/data — we only want the video frames here."""
    return [
        FFMPEG, "-nostdin", "-loglevel", "error",
        "-ss", f"{spec.t0:.6f}", "-i", spec.src_path, "-t", f"{spec.duration:.6f}",
        "-vf", f"scale={out_w}:{out_h},fps={fps:.6f}",
        "-an", "-sn", "-dn",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1",
    ]


def build_encode_cmd(spec: ExportSpec, out_w: int, out_h: int, fps: float) -> list[str]:
    """The MUX/ENCODE ffmpeg argv: input 0 is our composited rgb24 rawvideo on stdin (we declare
    its size + rate); input 1 is the SOURCE again, seek-trimmed to the same [t0, t0+dur) window for
    its AUDIO. Map our video + the source audio, encode H.264 (yuv420p, +faststart for streaming)
    and AAC. `-shortest` guards against a fractional-frame audio overrun."""
    return [
        FFMPEG, "-nostdin", "-loglevel", "error", "-y",
        # input 0: raw composited video from our pipe
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{out_w}x{out_h}", "-r", f"{fps:.6f}",
        "-i", "pipe:0",
        # input 1: source audio, same window
        "-ss", f"{spec.t0:.6f}", "-i", spec.src_path, "-t", f"{spec.duration:.6f}",
        "-map", "0:v:0", "-map", "1:a:0?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart",
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

    def paint_frame(self, img: QImage, vals: OverlayValues) -> None:
        """Paint all overlay elements onto `img` (an RGB frame at the output size). Advances the
        g-meter dial state first (so its dot/envelope reflect this frame), then draws."""
        self.feed_g(vals)
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setRenderHint(QPainter.TextAntialiasing, True)
        # g-meter dial: paint into its rect via the SHARED paint routine + a snapshot of the
        # headless dial's filtering state (identical to the on-screen widget).
        p.save()
        p.translate(self._g_rect.topLeft())
        gmeter_overlay.paint_dial(p, self._g_rect.width(), self._g_rect.height(),
                                  self._dial._dial_state())
        p.restore()
        self._map.paint(p, vals.marker_index)
        _paint_readout(p, self._readout_rect, vals)
        _paint_strip(p, self._strip_rect, self._session, vals, self._spec.t0)
        p.end()


# --------------------------------------------------------------------------- the renderer
class CancelledError(Exception):
    """Raised inside the render loop when the caller's cancel callback returns True."""


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
        src_w, src_h, src_fps = probe_video_size(spec.src_path)
        self._out_w, self._out_h = output_size(src_w, src_h, spec.config)
        self._fps = float(spec.config.fps or src_fps)
        self._times = frame_times(spec.t0, spec.t1, self._fps)
        self._painter = OverlayPainter(session, spec, self._out_w, self._out_h)
        self._dec: subprocess.Popen | None = None
        self._enc: subprocess.Popen | None = None
        self._dec_err: _StderrDrainer | None = None
        self._enc_err: _StderrDrainer | None = None
        self._i = 0
        self._frame_bytes = self._out_w * self._out_h * 3
        self._started = False
        self._done = False

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
            build_decode_cmd(self._spec, self._out_w, self._out_h, self._fps),
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self._enc = subprocess.Popen(
            build_encode_cmd(self._spec, self._out_w, self._out_h, self._fps),
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        # Drain BOTH ffmpeg stderrs off-thread so neither can ever block on a full stderr pipe while
        # the single-threaded loop is busy on the decode-stdout / encode-stdin pipes (deadlock guard).
        # (getattr-guarded so a mock Popen without a stderr attribute is simply not drained.)
        dec_se = getattr(self._dec, "stderr", None)
        enc_se = getattr(self._enc, "stderr", None)
        self._dec_err = _StderrDrainer(dec_se) if dec_se is not None else None
        self._enc_err = _StderrDrainer(enc_se) if enc_se is not None else None
        self._started = True

    def run_chunk(self, n: int = 24) -> bool:
        """Composite up to `n` frames; return True when the whole render is COMPLETE (and the
        outputs are finalized). The caller loops calling this until it returns True (or cancels via
        `cancel`). Reads exactly one frame's bytes per iteration from the decoder, paints it, and
        writes it to the encoder's stdin."""
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
                # Decoder ran dry (it may emit one fewer frame than our ceil estimate at the tail).
                self._finish()
                return True
            img = self._composite(float(self._times[self._i]), raw)
            stdin.write(img)
            self._i += 1
        return False

    def _composite(self, t: float, raw: bytes) -> bytes:
        """Paint the overlays for media time `t` onto one decoded rgb24 frame (`raw`, PACKED at
        out_w*3 as ffmpeg emits it) and return the painted frame's bytes, again PACKED at out_w*3
        for the encoder. QImage scanlines are 4-byte-aligned, so when out_w*3 isn't a multiple of
        4 the image carries per-row padding — we build a row-strided ndarray over the buffer and
        slice the padding off, so the bytes written back are tightly packed (without this, a
        non-4-aligned width would shear every row and desync the stream)."""
        w, h = self._out_w, self._out_h
        # Own a writable, packed copy of the decoded bytes for the painter to draw over.
        buf = bytearray(raw)
        img = QImage(buf, w, h, 3 * w, QImage.Format_RGB888)
        vals = overlay_values_at(self._session, t)
        self._painter.paint_frame(img, vals)
        bpl = img.bytesPerLine()
        if bpl == 3 * w:
            return bytes(buf)                       # already packed — no padding to strip
        # strided -> packed: view (h, bpl) bytes, keep the first 3*w columns of each row.
        arr = np.frombuffer(img.constBits(), dtype=np.uint8, count=bpl * h).reshape(h, bpl)
        return arr[:, : 3 * w].tobytes()

    def run(self, progress=None, cancel=None, chunk: int = 48) -> RenderResult:
        """Pump `run_chunk` to completion. `progress(done, total)` is called after each chunk;
        `cancel()` -> True aborts (raises CancelledError after tearing the pipes down). Returns a
        RenderResult on success."""
        try:
            while not self.run_chunk(chunk):
                if cancel is not None and cancel():
                    self.cancel()
                    raise CancelledError("export cancelled")
                if progress is not None:
                    progress(self._i, len(self._times))
            if progress is not None:
                progress(self._i, len(self._times))
        except CancelledError:
            raise
        except Exception:
            self.cancel()
            raise
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
            raise RuntimeError(f"ffmpeg encode failed ({enc.returncode}): "
                               f"{enc_err.decode('utf-8', 'replace')[-800:]}")

    def cancel(self) -> None:
        """Kill both ffmpeg processes and mark the render done (best-effort teardown for the
        cancel path / an error). Safe to call more than once. The stderr drainers are daemon
        threads draining pipes that close when the processes die, so they wind down on their own."""
        self._done = True
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


def render_lap(session, src_path: str, out_path: str, lap_id: int,
               config: OverlayConfig | None = None,
               progress=None, cancel=None) -> RenderResult:
    """Convenience: build the ExportSpec for `lap_id`'s window and render it to completion. Raises
    ValueError if the lap has no usable window. Used by the headless render path + tests; the app
    builds the spec itself so it can run the Renderer off the UI thread with a progress dialog."""
    win = lap_window_for_export(session, lap_id)
    if win is None:
        raise ValueError(f"lap {lap_id} has no usable export window")
    spec = ExportSpec(src_path=src_path, out_path=out_path, lap_id=lap_id,
                      t0=win[0], t1=win[1], config=config or OverlayConfig())
    return Renderer(session, spec).run(progress=progress, cancel=cancel)
