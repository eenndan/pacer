"""GMeterOverlay: a subtle "G meter" dial painted over the video (felt-force convention).

Native-window trick: on macOS a QVideoWidget renders through a native surface the window-server
composites independently of Qt's z-order, so a plain child overlay is hidden behind the video.
This overlay is its own frameless top-level window, composited as a separate layer above the
video surface; the VideoView keeps it pinned to the video's corner.

Felt-force axes (the pointer is the inertial reaction the body feels, not the accel vector;
g_at_time's accel convention is +lateral=left, +long=accelerating):
  * braking      -> pointer UP
  * accelerating -> pointer DOWN
  * turning right -> pointer LEFT
  * turning left  -> pointer RIGHT
Screen mapping: dx = +lateral*scale, dy = +longitudinal*scale.

Chin-mount shake: the dot is an EMA of the felt-force g; the envelope + cardinal peaks use a
high-percentile (robust) peak so a single shake spike can't blow them out.

Envelope = a convex hull of accumulated filtered felt points (grip used this scope); the four
cardinal numbers are the robust peak felt-g per direction. Scope defaults to the current lap and
resets at the lap boundary (`_RESET_ON_LAP` / `reset_envelope()`).

`pacer`-free: the app feeds set_g + set_lap at the ~30 Hz tick. The convention flip + filtering
are display concerns and live here; the validated g values in gmeter.py are untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from . import theme
from .theme import C

_c = theme.qcolor  # QColor from a theme hex token (+ optional alpha) — shared home in theme.py

# Outer ring g; a 1.0 g corner sits well inside and a ~1.5 g spike still lands within the dial.
_FULL_SCALE_G = 1.6
_RINGS = (0.5, 1.0)              # labelled rings (g) — the values ARE drawn (see _legend_items)

_TAG_BAND_H = 13                 # live dial: bottom strip reserved for the source tag ALONE, so it
                                 # can never overprint the bottom cardinal number (dial_geom pays
                                 # for it out of the dial's height)
_EXPORT_TAG_FRAC = 0.055         # export dial: the same reserved strip, as a fraction of the box
_LEGEND_MIN_R = 26.0             # below this dial radius the in-dial legend is dropped (no room)
_LEGEND_PT_RANGE = (5.5, 8.0)    # live: direction captions + ring labels (see _legend_pt)
_TAG_PT = 6.5                    # live: source tag (was 6.0 at C.text_muted -> 2.2:1, a smear)

_TITLE = "G METER"               # live caption; the EXPORT dial deliberately drops it (see
                                 # _export_dial_geom) — it is the ONLY string that differs

# What each cardinal peak MEANS. The dial shows FELT force (see the module doc), so the pointer
# swings LEFT in a right-hand corner: the caption names the DRIVING INPUT behind the number, not
# the direction the pointer moved — that is the whole ambiguity a bare number leaves open.
_DIR_BRAKE = "BRAKE"             # top    — peak felt-forward g (braking)
_DIR_ACCEL = "ACCEL"             # bottom — peak felt-back g (accelerating)
_DIR_TURN_R = "TURN R"           # left   — felt LEFT, i.e. a RIGHT-hand corner
_DIR_TURN_L = "TURN L"           # right  — felt RIGHT, i.e. a LEFT-hand corner

_DOT_EMA_ALPHA = 0.30            # dot low-pass per 30Hz sample (~0.1s tc); tames chin-mount shake

_PEAK_PERCENTILE = 90.0          # cardinal peak = this percentile of recent felt-g (robust)
_PEAK_WINDOW = 90                # samples (~3 s at 30 Hz) feeding the percentile peak
_ENVELOPE_MAX_PTS = 240          # cap on hull input points per scope (ring buffer)
_RESET_ON_LAP = True             # reset the envelope + peaks at each lap boundary


def _font(pt: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


# Human names for the sensor behind each dial axis. The IMU (GoPro accelerometer) is trusted for
# LATERAL g (r~0.9); the LONGITUDINAL axis reads the GPS speed-derivative because the IMU forward
# axis is vibration-inflated (r~0.36). So the tag must name the axis provenance, not one source.
_SRC_NAME = {"accl": "IMU", "gps": "GPS"}


def source_label(lat_source: str, long_source: str | None = None) -> str:
    """The tiny corner tag naming the dial's axis provenance. Same source both axes -> that name
    ("IMU" / "GPS"); mixed (the usual IMU-lateral + GPS-longitudinal meter) -> "IMU lat · GPS long"
    so the braking/accel axis the driver reads is labelled by where it actually comes from — not a
    bare "ACCL" that misattributes the GPS-derived longitudinal to the IMU."""
    lat = _SRC_NAME.get(lat_source, lat_source.upper())
    if long_source is None or long_source == lat_source:
        return lat
    lon = _SRC_NAME.get(long_source, long_source.upper())
    return f"{lat} lat · {lon} long"


@dataclass
class DialState:
    """Pure draw state for paint_dial; snapshotted by both the live widget and the offline
    exporter so the burned dial matches the screen."""
    fx: float = 0.0
    fy: float = 0.0
    have: bool = False
    hull_pts: list[tuple[float, float]] = field(default_factory=list)
    peak_fwd: float = 0.0
    peak_back: float = 0.0
    peak_left: float = 0.0
    peak_right: float = 0.0
    source: str = "accl"


def dial_geom(w: float, h: float):
    """Centre + radius of the dial inside a (w, h) box. A slim title strip up top, a reserved
    `_TAG_BAND_H` strip at the bottom for the source tag, and a uniform margin for the cardinal
    peak numbers just outside the outer ring. Shared by the live widget (`GMeterOverlay._geom`)
    and the offline renderer so both lay out identically.

    The bottom band is what keeps the source tag off the bottom cardinal number: without it the
    two shared an 11 px band at the 120x140 minimum and the tag overprinted the number."""
    title_h = 18
    margin = 18                       # room for the cardinal peak numbers outside the ring
    dial_top = title_h
    dial_h = h - title_h - _TAG_BAND_H
    r = (min(w, dial_h) - 2 * margin) / 2.0
    r = max(r, 8.0)
    cx = w / 2.0
    cy = dial_top + dial_h / 2.0
    return cx, cy, r


def dial_to_screen(cx, cy, r, fx, fy):
    """Map a felt-force point (felt-x, felt-y in g) to a dial pixel. +felt-x -> RIGHT, +felt-y ->
    DOWN (accelerating); braking (felt-y<0) -> UP. Clamped to the dial circle so a big value stays
    on the rim with its direction preserved. Shared by the live widget + the offline renderer."""
    scale = r / _FULL_SCALE_G
    dx = fx * scale
    dy = fy * scale
    d = math.hypot(dx, dy)
    if d > r:
        dx, dy = dx / d * r, dy / d * r
    return cx + dx, cy + dy


def _legend_pt(r: float) -> float:
    """Live legend type size for a dial of radius `r`. The lower bound is what lets BOTH lateral
    captions sit side by side inside the 120x140 minimum overlay; it grows with the dial so a
    maximised video pane does not leave the legend stranded at caption size."""
    lo, hi = _LEGEND_PT_RANGE
    return min(hi, max(lo, r * 0.15))


def _legend_items(cx: float, cy: float, r: float, fm: QFontMetricsF):
    """Layout for the in-dial legend, as [(rect, alignment flags, text), ...].

    Two things a bare four-number dial cannot say:
      * WHICH DIRECTION each cardinal peak belongs to — four captions hugging the inside of the
        rim (the felt-force convention means the LEFT number is a RIGHT-hand corner, so the
        caption is essential, not decoration);
      * WHAT UNIT the numbers are in — carried by the labelled rings ("0.5 g" / "1.0 g"), which
        `_RINGS` has always been commented as having and which were never actually drawn.

    Ring labels sit on the up-right diagonal of their own ring (the one part of the face with no
    number, no crosshair and no resting dot) and are placed outermost-first: a label that would
    collide with one already placed is dropped, so a small dial carries the 1.0 g reference only
    and a large one carries both. Shared by the live and export painters so the burned-in dial
    names exactly what the screen names."""
    if r < _LEGEND_MIN_R:
        return []
    fh = fm.height()
    items = [
        (QRectF(cx - r, cy - r + fh * 0.55, 2 * r, fh),
         Qt.AlignHCenter | Qt.AlignVCenter, _DIR_BRAKE),
        (QRectF(cx - r, cy + r - fh * 1.55, 2 * r, fh),
         Qt.AlignHCenter | Qt.AlignVCenter, _DIR_ACCEL),
    ]
    # lateral captions share one row just BELOW the crosshair, hugging each rim: clear of the side
    # numbers (which sit at the crosshair) and clear of the dot's resting position at the centre.
    row_y = cy + fh * 0.90
    inset = max(4.0, r * 0.10)
    wl = fm.horizontalAdvance(_DIR_TURN_R)
    wr = fm.horizontalAdvance(_DIR_TURN_L)
    # Font metrics are platform-dependent: if the two captions cannot sit side by side with a real
    # gap, drop them rather than let them run into each other (BRAKE/ACCEL and the rings stay).
    if wl + wr + 2 * inset + fh * 0.4 <= 2 * r:
        items.append((QRectF(cx - r + inset, row_y, wl, fh),
                      Qt.AlignHCenter | Qt.AlignVCenter, _DIR_TURN_R))
        items.append((QRectF(cx + r - inset - wr, row_y, wr, fh),
                      Qt.AlignHCenter | Qt.AlignVCenter, _DIR_TURN_L))
    placed: list[QRectF] = []
    for gval in sorted(_RINGS, reverse=True):
        rr = r * (gval / _FULL_SCALE_G)
        text = f"{gval:.1f} g"
        tw = fm.horizontalAdvance(text)
        d = rr * 0.70710678                       # the ring's up-right 45-degree point
        rect = QRectF(cx + d - tw / 2.0, cy - d - fh / 2.0, tw, fh)
        if any(rect.intersects(prev) for prev in placed):
            continue                              # too little room between rings at this size
        items.append((rect, Qt.AlignHCenter | Qt.AlignVCenter, text))
        placed.append(rect.adjusted(-fh * 0.25, -fh * 0.25, fh * 0.25, fh * 0.25))
    return items


# --------------------------------------------------------------------------- export palette
# Export-render palette: vivid/opaque for burning over bright footage (live uses C.* tokens).
# Kept local to mirror export_video.EXPORT without importing it (this module is pacer-free).
_EX_TEXT = "#FFFFFF"
_EX_HALO = "#0A0C10"          # dark outline/shadow under every bright element
_EX_ACCENT = "#FFB21E"        # envelope amber (brighter + saturated vs C.accent)
_EX_ACCENT_HI = "#FFD34D"     # dot glow highlight
_EX_GRID = "#FFFFFF"          # rings / crosshair (white at moderate alpha)


def _draw_text_outlined(p: QPainter, rect: QRectF, flags, text: str, font: QFont,
                        colour: str, halo: float = 2.2) -> None:
    """Draw aligned `text` with a dark outline under a bright fill so a burned label reads over
    sky and tarmac."""
    fm = QFontMetricsF(font)
    w = fm.horizontalAdvance(text)
    if flags & Qt.AlignHCenter:
        x = rect.x() + (rect.width() - w) / 2.0
    elif flags & Qt.AlignRight:
        x = rect.right() - w
    else:
        x = rect.x()
    if flags & Qt.AlignVCenter:
        y = rect.y() + (rect.height() + fm.ascent() - fm.descent()) / 2.0
    else:
        y = rect.y() + fm.ascent()
    path = QPainterPath()
    path.addText(QPointF(x, y), font, text)
    p.save()
    pen = QPen(_c(_EX_HALO, 235), halo * 2.0)
    pen.setJoinStyle(Qt.RoundJoin)
    pen.setCapStyle(Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    p.setPen(Qt.NoPen)
    p.setBrush(_c(colour))
    p.drawPath(path)
    p.restore()


def _export_dial_geom(w: float, h: float):
    """Dial centre+radius for export: larger number margin, no title strip so the dial fills more
    of the box, and a reserved bottom strip (`_EXPORT_TAG_FRAC`) for the provenance tag so it
    cannot land on the bottom cardinal number."""
    tag_h = _EXPORT_TAG_FRAC * min(w, h)
    margin = 0.20 * min(w, h)          # room for the larger outlined cardinal numbers
    r = max((min(w, h - tag_h) - 2 * margin) / 2.0, 8.0)
    return w / 2.0, (h - tag_h) / 2.0, r


def _export_legend_font(k: float) -> QFont:
    """The export dial's legend type. Scales with the output height like every other export glyph;
    the floor keeps a 720p burn readable (it is outlined white-on-halo, so small still reads)."""
    return _font(max(6.0, 8.5 * k))


def _export_tag_font(k: float) -> QFont:
    """The export dial's provenance-tag type (a hair larger than the legend — it is the line that
    sources every number on the dial)."""
    return _font(max(6.5, 9.0 * k))


def paint_dial(p: QPainter, w: float, h: float, st: DialState,
               export: bool = False, scale_k: float = 1.0) -> None:
    """Paint the dial (backdrop, rings, legend, envelope, peaks, dot, source tag) sized to (w,h) at
    the origin. Single source for the live widget + the offline exporter; no widget state touched.

    Both modes paint the SAME set of labels — direction captions, labelled rings and the source
    tag (`_legend_items` + `st.source`). Only the styling differs; a label the screen shows and the
    burned-in export drops would leave a shared video making claims it cannot source.

    export=False = on-screen look; export=True = the burn-over-bright variant (no box, white rings,
    brighter envelope, bigger glowing dot, large outlined numbers). `scale_k` scales export
    strokes/glyphs to the output height (1.0 ≈ a ~280 px dial at 1080p).

    The two layers (static template + moving dot) are painted here in one pass so a one-shot
    caller (the offline exporter renders every frame fresh) stays byte-identical to the original.
    The live widget instead caches the static layer and re-blits it per frame (see
    `GMeterOverlay.paintEvent`); the split is a rendering-cost optimisation, not a visual change."""
    p.setRenderHint(QPainter.Antialiasing, True)
    if export:
        _paint_dial_export(p, w, h, st, scale_k)
        return
    _paint_dial_static(p, w, h, st)
    _paint_dial_dot(p, w, h, st)


def _paint_dial_static(p: QPainter, w: float, h: float, st: DialState) -> None:
    """The SLOW-CHANGING live dial layer: backdrop box, caption, rings, crosshair, grip envelope,
    legend (direction captions + labelled rings), cardinal peak numbers, source tag — everything
    except the per-frame moving dot. Identical frame-to-frame at a given size while the
    envelope/peaks/source are unchanged, so the live widget renders this once into a cached pixmap
    (keyed by size + palette + envelope-version) and re-blits it every tick, drawing only the dot
    on top."""
    cx, cy, r = dial_geom(w, h)

    # panel-grey backing (C.surface) + theme hairline so the dial reads as app chrome over footage
    backdrop = QRectF(1, 1, w - 2, h - 2)
    p.setBrush(_c(C.surface, 168))
    p.setPen(QPen(_c(C.border, 200), 1))
    p.drawRoundedRect(backdrop, 12, 12)

    # G METER caption, theme caption type
    p.setPen(QPen(_c(C.text_dim, 235)))
    title_f = _font(7.5, bold=True)
    title_f.setLetterSpacing(QFont.AbsoluteSpacing, 1.4)
    p.setFont(title_f)
    p.drawText(QRectF(0, 3, w, 14), Qt.AlignHCenter | Qt.AlignVCenter, _TITLE)

    # concentric rings: theme hairline, dimmed
    p.setBrush(Qt.NoBrush)
    for gval in _RINGS:
        rr = r * (gval / _FULL_SCALE_G)
        p.setPen(QPen(_c(C.border, 190), 1.0))   # inner grid circles — theme hairline, dim
        p.drawEllipse(QPointF(cx, cy), rr, rr)
    # outer boundary ring — the interactive/hover hairline (C.border_strong), a touch stronger
    p.setPen(QPen(_c(C.border_strong, 215), 1.2))
    p.drawEllipse(QPointF(cx, cy), r, r)
    # faint crosshair guides (tick/axis marks) — the muted tertiary text token, very low alpha
    p.setPen(QPen(_c(C.text_muted, 80), 0.8))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

    # grip envelope: low-alpha amber fill + brighter amber rim
    if len(st.hull_pts) >= 3:
        hull = _convex_hull(st.hull_pts)
        if len(hull) >= 3:
            poly = QPolygonF([QPointF(*dial_to_screen(cx, cy, r, hx, hy))
                              for (hx, hy) in hull])
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            p.setBrush(_c(C.accent, 38))             # quiet amber wash — lets the grid show through
            p.setPen(QPen(_c(C.accent_hover, 215), 1.4))  # bright amber rim = the grip envelope
            p.drawPath(path)

    # in-dial legend: the four direction captions + the labelled rings (which carry the unit).
    # Drawn after the envelope wash so the captions stay readable through it, before the peak
    # numbers so those stay the loudest text on the face.
    legend_f = _font(_legend_pt(r))
    p.setFont(legend_f)
    p.setPen(QPen(_c(C.text_dim, 195)))
    for rect, flags, text in _legend_items(cx, cy, r, QFontMetricsF(legend_f)):
        p.drawText(rect, flags, text)

    # cardinal peak-g numbers (robust max felt-g per direction)
    p.setFont(_font(8.0, bold=True))
    p.setPen(QPen(_c(C.text_dim, 235)))
    off = 11
    # forward (braking) at top, back (accel) at bottom, left/right on the sides
    p.drawText(QRectF(cx - 22, cy - r - off - 6, 44, 12), Qt.AlignCenter, f"{st.peak_fwd:.1f}")
    p.drawText(QRectF(cx - 22, cy + r + off - 6, 44, 12), Qt.AlignCenter, f"{st.peak_back:.1f}")
    p.drawText(QRectF(cx - r - off - 22, cy - 6, 44, 12), Qt.AlignRight | Qt.AlignVCenter,
               f"{st.peak_left:.1f}")
    p.drawText(QRectF(cx + r + off - 22, cy - 6, 44, 12), Qt.AlignLeft | Qt.AlignVCenter,
               f"{st.peak_right:.1f}")

    # source tag (bottom-right): names the dial's axis provenance (IMU lateral · GPS longitudinal),
    # not one source — `st.source` is already the display label (see source_label). It lives in the
    # `_TAG_BAND_H` strip dial_geom reserves for it, so it never lands on the bottom peak number;
    # C.text_dim (not text_muted) because at 6.5 pt the tertiary token was an illegible smear.
    p.setPen(QPen(_c(C.text_dim, 225)))
    p.setFont(_font(_TAG_PT))
    # a wider box so "IMU lat · GPS long" isn't clipped (still bottom-right anchored)
    p.drawText(QRectF(w - 96, h - _TAG_BAND_H, 92, _TAG_BAND_H - 1),
               Qt.AlignRight | Qt.AlignVCenter, st.source)


def _paint_dial_dot(p: QPainter, w: float, h: float, st: DialState) -> None:
    """The PER-FRAME live dial layer: the felt-force pointer (amber glow + dark-ringed off-white
    core), the only element that moves every ~30 Hz tick. Painted on top of the static layer."""
    if not st.have:
        return
    cx, cy, r = dial_geom(w, h)
    dx, dy = dial_to_screen(cx, cy, r, st.fx, st.fy)
    grad = QRadialGradient(QPointF(dx, dy), 8)
    grad.setColorAt(0.0, _c(C.accent_hover, 245))
    grad.setColorAt(1.0, _c(C.accent_hover, 0))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawEllipse(QPointF(dx, dy), 7, 7)
    p.setPen(QPen(_c(C.canvas, 220), 1.0))   # thin dark ring so the core reads off the glow
    p.setBrush(_c(C.text, 250))
    p.drawEllipse(QPointF(dx, dy), 2.6, 2.6)


def _paint_dial_export(p: QPainter, w: float, h: float, st: DialState, k: float) -> None:
    """The export g-dial: no backdrop box, white high-contrast rings, a brighter amber envelope,
    big outlined cardinal-g numbers, the same legend + provenance tag the live dial paints, and a
    bigger haloed dot. Layout via _export_dial_geom; `k` scales strokes/glyphs with the output
    height.

    No "G METER" title strip — that omission is deliberate (see `_export_dial_geom`), so the dial
    fills more of the box. The source tag is NOT part of that trade: it is the only thing on the
    burned-in dial that says where the numbers came from."""
    k = max(0.5, float(k))
    cx, cy, r = _export_dial_geom(w, h)

    # --- rings (white, high-contrast) with a dark halo so they read on bright sky too ---
    p.setBrush(Qt.NoBrush)
    for gval in _RINGS:
        rr = r * (gval / _FULL_SCALE_G)
        p.setPen(QPen(_c(_EX_HALO, 150), 3.0 * k))
        p.drawEllipse(QPointF(cx, cy), rr, rr)
        p.setPen(QPen(_c(_EX_GRID, 150), 1.4 * k))   # inner grid circles
        p.drawEllipse(QPointF(cx, cy), rr, rr)
    # outer boundary ring — brightest
    p.setPen(QPen(_c(_EX_HALO, 170), 4.2 * k))
    p.drawEllipse(QPointF(cx, cy), r, r)
    p.setPen(QPen(_c(_EX_GRID, 235), 2.2 * k))
    p.drawEllipse(QPointF(cx, cy), r, r)
    # crosshair guides (haloed white, subtle)
    p.setPen(QPen(_c(_EX_HALO, 130), 2.6 * k))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
    p.setPen(QPen(_c(_EX_GRID, 130), 1.1 * k))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

    # --- filled max-G envelope (grip used this lap): brighter amber, haloed outline ---
    if len(st.hull_pts) >= 3:
        hull = _convex_hull(st.hull_pts)
        if len(hull) >= 3:
            poly = QPolygonF([QPointF(*dial_to_screen(cx, cy, r, hx, hy))
                              for (hx, hy) in hull])
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            p.setPen(QPen(_c(_EX_HALO, 150), 3.4 * k))   # dark halo under the envelope edge
            p.setBrush(Qt.NoBrush)
            p.drawPath(path)
            p.setPen(QPen(_c(_EX_ACCENT, 235), 2.0 * k))
            p.setBrush(_c(_EX_ACCENT, 70))
            p.drawPath(path)

    # --- in-dial legend, MIRRORED from the live dial: same direction captions, same labelled
    # rings, same strings. The burned-in dial must not be more silent than the screen it came from.
    legend_f = _export_legend_font(k)
    for rect, flags, text in _legend_items(cx, cy, r, QFontMetricsF(legend_f)):
        _draw_text_outlined(p, rect, flags, text, legend_f, _EX_TEXT, halo=1.5 * k)

    # --- BIG cardinal peak-g numbers (robust max felt-g per direction), outlined ---
    fnt = _font(max(8.0, 13.0 * k), bold=True)
    off = 16 * k
    bw, bh = 56 * k, 22 * k
    _draw_text_outlined(p, QRectF(cx - bw / 2, cy - r - off - bh, bw, bh),
                        Qt.AlignCenter, f"{st.peak_fwd:.1f}", fnt, _EX_TEXT, halo=2.2 * k)
    _draw_text_outlined(p, QRectF(cx - bw / 2, cy + r + off, bw, bh),
                        Qt.AlignCenter, f"{st.peak_back:.1f}", fnt, _EX_TEXT, halo=2.2 * k)
    _draw_text_outlined(p, QRectF(cx - r - off - bw, cy - bh / 2, bw, bh),
                        Qt.AlignRight | Qt.AlignVCenter, f"{st.peak_left:.1f}", fnt, _EX_TEXT,
                        halo=2.2 * k)
    _draw_text_outlined(p, QRectF(cx + r + off, cy - bh / 2, bw, bh),
                        Qt.AlignLeft | Qt.AlignVCenter, f"{st.peak_right:.1f}", fnt, _EX_TEXT,
                        halo=2.2 * k)

    # --- provenance tag, in the strip _export_dial_geom reserves at the bottom. The exporter
    # already sets it ("IMU lat · GPS long") and snapshots it into DialState; painting it is what
    # makes the shared MP4 say where its numbers came from instead of showing bare digits.
    tag_h = _EXPORT_TAG_FRAC * min(w, h)
    tag_f = _export_tag_font(k)
    _draw_text_outlined(p, QRectF(0, h - tag_h, w, tag_h),
                        Qt.AlignHCenter | Qt.AlignVCenter, st.source, tag_f, _EX_TEXT,
                        halo=1.8 * k)

    # --- the live felt-force dot: a bigger soft glow + a dark-haloed bright core ---
    if st.have:
        dx, dy = dial_to_screen(cx, cy, r, st.fx, st.fy)
        gr = 13.0 * k
        grad = QRadialGradient(QPointF(dx, dy), gr)
        grad.setColorAt(0.0, _c(_EX_ACCENT_HI, 235))
        grad.setColorAt(0.6, _c(_EX_ACCENT, 150))
        grad.setColorAt(1.0, _c(_EX_ACCENT, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(dx, dy), gr, gr)
        p.setPen(QPen(_c(_EX_HALO, 220), 1.8 * k))   # dark ring so the dot reads on bright sky
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(dx, dy), 4.6 * k, 4.6 * k)
        p.setPen(Qt.NoPen)
        p.setBrush(_c(_EX_TEXT, 250))
        p.drawEllipse(QPointF(dx, dy), 4.0 * k, 4.0 * k)


class GMeterOverlay(QWidget):
    def __init__(self, parent: QWidget | None = None):
        # Frameless translucent top-level window so it composites above the native video surface
        # (a child widget would be hidden behind it on macOS); positioned by the VideoView.
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMinimumSize(120, 140)
        # Filtered felt-force pointer in g; axes: +x = thrown right, +y(down) = thrown back (accel),
        # -y(up) = thrown forward (brake). Peaks are the robust per-direction max felt-g (all >= 0).
        self._fx = 0.0
        self._fy = 0.0
        self._have = False
        self._ema_init = False
        self._source = source_label("accl")   # display label (see source_label); default IMU-only
        self._hull_pts: list[tuple[float, float]] = []     # filtered felt points (ring buffer)
        self._recent: list[tuple[float, float]] = []       # rolling window for percentile peaks
        self._peak_fwd = 0.0
        self._peak_back = 0.0
        self._peak_left = 0.0
        self._peak_right = 0.0
        self._lap: int | None = None
        # --- static-layer cache (a per-frame repaint blits this + draws only the moving dot) ---
        # `_env_version` bumps whenever ANY static-layer content changes (the grip envelope points,
        # the cardinal peaks, or the source tag). The cached pixmap is keyed by (size, palette,
        # env_version), so the convex hull + all the ring/backdrop/number drawing recompute exactly
        # ONCE per envelope change — not every ~30 Hz tick that only moves the dot.
        self._env_version = 0
        self._static_pixmap = None          # QPixmap | None
        self._static_key: tuple | None = None

    # ------------------------------------------------------------------ data in
    def set_g(self, g: tuple[float, float, float] | None) -> None:
        """Push the current kart-frame (lateral_g, longitudinal_g, total_g). None blanks the live
        dot (keeps the template + the accumulated envelope). Applies the felt-force convention and
        the shake low-pass, grows the envelope + robust cardinal peaks, and repaints."""
        if g is None:
            if self._have:
                self._have = False
                self.update()
            return
        lat, lon, _total = g
        # Felt-force convention (see module doc): felt x = +lateral, felt y = +longitudinal.
        fx, fy = lat, lon
        # Shake low-pass (EMA) so the dot tracks vehicle g, not head/mount jitter.
        if not self._ema_init:
            self._fx, self._fy, self._ema_init = fx, fy, True
        else:
            a = _DOT_EMA_ALPHA
            self._fx += a * (fx - self._fx)
            self._fy += a * (fy - self._fy)
        self._have = True
        self._accumulate(self._fx, self._fy)
        self.update()

    def _accumulate(self, fx: float, fy: float) -> None:
        """Grow per-lap envelope + robust cardinal peaks from the filtered felt point. Peaks use a
        percentile of the recent window and the hull point is clamped to them, so a lone shake spike
        can't balloon either."""
        self._recent.append((fx, fy))
        if len(self._recent) > _PEAK_WINDOW:
            self._recent.pop(0)
        # Robust peak per cardinal: percentile of the rolling window so a single shake sample can't win.
        right, left, back, fwd = [], [], [], []
        for px, py in self._recent:
            if px > 0:
                right.append(px)
            elif px < 0:
                left.append(-px)
            if py > 0:
                back.append(py)     # accelerating (felt down)
            elif py < 0:
                fwd.append(-py)     # braking (felt up)
        self._peak_right = max(self._peak_right, _pct(right, _PEAK_PERCENTILE))
        self._peak_left = max(self._peak_left, _pct(left, _PEAK_PERCENTILE))
        self._peak_back = max(self._peak_back, _pct(back, _PEAK_PERCENTILE))
        self._peak_fwd = max(self._peak_fwd, _pct(fwd, _PEAK_PERCENTILE))
        # Clamp the hull candidate to the robust per-direction peaks so one spike can't balloon the blob.
        hx = min(fx, self._peak_right) if fx >= 0 else max(fx, -self._peak_left)
        hy = min(fy, self._peak_back) if fy >= 0 else max(fy, -self._peak_fwd)
        self._hull_pts.append((hx, hy))
        if len(self._hull_pts) > _ENVELOPE_MAX_PTS:
            self._hull_pts.pop(0)
        # A new felt sample changed the hull points AND (possibly) the cardinal peaks — both live in
        # the cached static layer, so invalidate it. Bump unconditionally: cheap, and the peaks can
        # tick up on any sample. (Once the envelope is full the hull_pts *content* still shifts, so a
        # length-only key would go stale here — the version counter is the safe choice.)
        self._env_version += 1

    def set_lap(self, lap_id: int | None) -> None:
        """Set the current lap. A change to a new valid lap resets the envelope + peaks (when
        _RESET_ON_LAP); None (lead-in / between laps) is held so the envelope persists."""
        if lap_id is None or lap_id == self._lap:
            return
        if _RESET_ON_LAP and self._lap is not None:
            self.reset_envelope()
        self._lap = lap_id

    def set_source(self, source: str, long_source: str | None = None) -> None:
        """Label the dial's axis provenance, shown small in the corner. `source` is the lateral
        (IMU) source id ("accl"/"gps"); `long_source` the longitudinal one (the GPS speed-derivative
        when present) — the two usually differ, so the tag reads "IMU lat · GPS long" rather than a
        bare "ACCL" that would misattribute the GPS-derived braking axis to the IMU."""
        label = source_label(source, long_source)
        if label == self._source:
            return
        self._source = label
        self._env_version += 1   # the source tag lives in the cached static layer — invalidate it
        self.update()

    def reset_envelope(self) -> None:
        """Clear the envelope + cardinal peaks and re-seed the dot EMA so the pointer starts fresh
        on the new scope's first sample (no carry-over from the previous lap)."""
        self._hull_pts.clear()
        self._recent.clear()
        self._peak_fwd = self._peak_back = self._peak_left = self._peak_right = 0.0
        # Re-seed the dot EMA: the next set_g seeds _fx/_fy from its own value (no carry-over).
        self._ema_init = False
        self._fx = self._fy = 0.0
        self._env_version += 1   # envelope + peaks cleared → the cached static layer is stale
        self.update()

    # ------------------------------------------------------------------ painting
    def _geom(self):
        # thin delegate to dial_geom; kept for the offscreen tests
        return dial_geom(self.width(), self.height())

    def _to_screen(self, cx, cy, r, fx, fy):
        # thin delegate to dial_to_screen (tests)
        return dial_to_screen(cx, cy, r, fx, fy)

    def _dial_state(self) -> DialState:
        """Snapshot the live filtering state into a pure DialState for paint_dial (same snapshot
        the exporter renders from, so the burned dial matches the screen)."""
        return DialState(
            fx=self._fx, fy=self._fy, have=self._have, hull_pts=list(self._hull_pts),
            peak_fwd=self._peak_fwd, peak_back=self._peak_back,
            peak_left=self._peak_left, peak_right=self._peak_right, source=self._source)

    def _static_layer(self, st: DialState):
        """Return the cached static-dial QPixmap for the current size + palette + envelope-version,
        rendering it once on a miss. This is where the convex hull + the ring/backdrop/number
        drawing actually run — exactly once per envelope change, NOT once per ~30 Hz tick."""
        w, h = self.width(), self.height()
        dpr = self.devicePixelRatioF()
        key = (w, h, round(dpr, 4), theme.active_palette(), self._env_version)
        if self._static_pixmap is not None and self._static_key == key:
            return self._static_pixmap
        # Render the static layer once into a transparent pixmap at the widget's device pixel ratio
        # (so it blits back 1:1 with no scaling — pixel-identical to painting it directly).
        pm = QPixmap(int(round(w * dpr)), int(round(h * dpr)))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.transparent)
        sp = QPainter(pm)
        sp.setRenderHint(QPainter.Antialiasing, True)
        _paint_dial_static(sp, w, h, st)
        sp.end()
        self._static_pixmap = pm
        self._static_key = key
        return pm

    def paintEvent(self, _event):
        # Per-frame cost = blit the cached static layer + draw ONLY the moving dot. The backdrop,
        # rings, crosshair, caption, grip envelope (convex hull) and cardinal numbers are rendered
        # once into `_static_pixmap` and reused until the envelope/size/palette changes; only the
        # felt-force dot is re-drawn each ~30 Hz tick (the sole element that moves).
        st = self._dial_state()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.drawPixmap(0, 0, self._static_layer(st))
        _paint_dial_dot(p, self.width(), self.height(), st)
        p.end()


def _pct(vals, q):
    """The q-th percentile of `vals` (a robust peak), or 0.0 if empty. Pure-Python (no numpy in
    the per-tick paint path) — the lists are tiny (<= _PEAK_WINDOW)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return s[0]
    pos = (q / 100.0) * (len(s) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(s) - 1)
    frac = pos - lo
    return s[lo] + (s[hi] - s[lo]) * frac


def _convex_hull(points):
    """Andrew's monotone-chain convex hull of `points` (list of (x,y)). Returns the hull vertices
    CCW. O(n log n); n <= _ENVELOPE_MAX_PTS, recomputed per paint (cheap at these sizes)."""
    pts = sorted(set(points))
    if len(pts) <= 2:
        return pts

    def cross(o, a, b):
        return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

    lower = []
    for pt in pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], pt) <= 0:
            lower.pop()
        lower.append(pt)
    upper = []
    for pt in reversed(pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], pt) <= 0:
            upper.pop()
        upper.append(pt)
    return lower[:-1] + upper[:-1]
