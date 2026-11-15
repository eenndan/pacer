"""trace_paint — the one painter for a lap's speed-coloured line on a still picture.

Two surfaces draw a single lap's trace with a QPainter: the share card's map (`share_card.
lap_map_png`, a 1080-px social image) and the debrief hero (`coaching_panel.DebriefHero`, a widget
pixmap). They used to carry two copies of the same code — the bucket fetch, the flat-trace
fallback, the north-up fit, the dropout skip — and only one of them had a speed key or corner
rings. Both now call this module, so a fix to the line is a fix to both pictures:

  * `lap_trace` — a lap's local-metre trace and its per-segment speed buckets off the live map's
    own `map_render.rainbow_channel`, with the flat-trace fallback and the key's two ends;
  * `TraceFit` / `fit_box` — the north-up, aspect-true fit of the trace into a pixel rect;
  * `paint_trace` — the line itself: an optional casing pass, then one pen per segment on the
    palette's ramp, no chord across a GPS dropout, and the lap CLOSED where its two ends meet. A
    lap starts and ends on the start line, but its two passes over it are not the same point
    (a few tenths of a metre apart on real footage): drawn open, the line showed a kink there;
  * `finish_at` / `paint_finish_mark` — the chequered start/finish mark and the arrow pointing the
    way the laps run, from `map_render.finish_mark_geometry`, the live map's own geometry;
  * `paint_speed_key`, `label_rect`, `paint_ring` — the key, the corner-label placement and the
    focus ring both pictures share.

Qt (QPainter) is its drawing library, not its UI: it builds no widget. It reads the session only
through `lap_channels`, `start_line`, `tx`/`ty` and `corners.corner_map_markers`, and imports no
`pacer` (tests/test_layering.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QLinearGradient, QPainter, QPen, QPolygonF

from . import map_render, theme
from .theme import C

# The longest closing chord the line draws between a lap's last and first sample. The two passes
# over the start line are a few tenths of a metre apart on real footage and ~0.07 m on the
# synthetic demo; a lap
# whose ends are farther apart than this is not a closed loop, and no chord is invented for it.
CLOSE_GAP_M = 3.0


@dataclass(frozen=True)
class LapTrace:
    """One lap's drawable trace: local metres, per-segment speed buckets (-1 = a GPS dropout), and
    the speed key's two measured ends ("" when the speed is flat)."""

    xs: np.ndarray
    ys: np.ndarray
    seg: np.ndarray
    speed_lo: str
    speed_hi: str


def lap_trace(session, lap_id, unit: str | None = None) -> LapTrace | None:
    """`lap_id`'s trace on the live map's speed ramp (`map_render.rainbow_channel`, GPS-dropout
    gaps included, so the colour-blind palette recolours it too), or None when the lap has fewer
    than two positioned samples. Exceptions from the session propagate: each caller has its own
    degrade-quietly rule. A flat speed trace is one colour, the middle of the ramp."""
    ch = session.lap_channels(lap_id)
    xs, ys = np.asarray(ch["x_m"], float), np.asarray(ch["y_m"], float)
    got = map_render.rainbow_channel("speed", ch["t_telemetry_s"], xs, ys, ch["speed_kmh"],
                                     ch["dist_m"], None, None, unit)
    if (np.isfinite(xs) & np.isfinite(ys)).sum() < 2:
        return None
    if got is not None and got[0] is not None:
        return LapTrace(xs, ys, np.asarray(got[0]), got[1], got[2])
    return LapTrace(xs, ys, np.full(len(xs) - 1, theme.MAP_RAINBOW_N // 2), "", "")


def corner_apexes(markers) -> dict[int, tuple[float, float]]:
    """cid -> (x, y) apex from `corners.corner_map_markers()` rows ("C4", x, y, direction)."""
    out = {}
    for label, x, y, _d in markers:
        if str(label).startswith("C") and str(label)[1:].isdigit():
            out[int(str(label)[1:])] = (float(x), float(y))
    return out


@dataclass(frozen=True)
class TraceFit:
    """Local metres → pixels: north up (local y runs up, rows run down), centred on (cx, cy)."""

    scale: float
    cx: float
    cy: float
    xm: float   # the trace's bounding-box centre, local metres
    ym: float

    def px(self, xs, ys):
        return self.cx + self.scale * (xs - self.xm), self.cy - self.scale * (ys - self.ym)

    def point(self, x: float, y: float) -> QPointF:
        return QPointF(self.cx + self.scale * (x - self.xm), self.cy - self.scale * (y - self.ym))


def bounds(xs, ys) -> tuple[float, float, float, float]:
    """(x0, x1, y0, y1) over the positioned samples."""
    ok = np.isfinite(xs) & np.isfinite(ys)
    return float(xs[ok].min()), float(xs[ok].max()), float(ys[ok].min()), float(ys[ok].max())


def fit_box(xs, ys, box: QRectF, inset: float) -> TraceFit | None:
    """The largest aspect-true fit of the trace into `box` less `inset` on every side, centred."""
    x0, x1, y0, y1 = bounds(xs, ys)
    scale = min((box.width() - 2 * inset) / ((x1 - x0) or 1.0),
                (box.height() - 2 * inset) / ((y1 - y0) or 1.0))
    if scale <= 0:
        return None
    return TraceFit(scale, box.center().x(), box.center().y(), (x0 + x1) / 2, (y0 + y1) / 2)


def paint_trace(p: QPainter, tr: LapTrace, fit: TraceFit, line_px: float, *,
                casing: QColor | None = None, casing_px: float = 0.0, close: bool = True):
    """Paint the lap's line: a `casing` pass first when given (a canvas-coloured pen `casing_px`
    wider, which keeps the colours crisp where a trace crosses itself), then one round-capped pen
    per segment in its speed bucket's colour. No chord across a GPS dropout (the live map's rule).
    `close` adds the chord from the last sample to the first when they are within CLOSE_GAP_M, in
    the last segment's colour — the lap's two passes over the start line, joined. Returns the
    (px, py) pixel arrays."""
    ok = np.isfinite(tr.xs) & np.isfinite(tr.ys)
    px, py = fit.px(tr.xs, tr.ys)
    seg = tr.seg
    chord = None
    # Not across a dropout at either end: a lap whose first or last segment is a GPS gap has no
    # measured position to join there, and the chord would bridge the gap the line skips.
    if close and len(seg) and ok[0] and ok[-1] and int(seg[-1]) >= 0 and int(seg[0]) >= 0:
        if math.hypot(tr.xs[-1] - tr.xs[0], tr.ys[-1] - tr.ys[0]) <= CLOSE_GAP_M:
            chord = (QPointF(px[-1], py[-1]), QPointF(px[0], py[0]), int(seg[-1]))
    if casing is not None:
        pen = QPen(casing, line_px + casing_px)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        for i in range(len(seg)):
            if seg[i] >= 0 and ok[i] and ok[i + 1]:
                p.drawLine(QPointF(px[i], py[i]), QPointF(px[i + 1], py[i + 1]))
        if chord is not None:
            p.drawLine(chord[0], chord[1])
    colours = [QColor(c) for c in theme.rainbow_colors(theme.MAP_RAINBOW_N)]
    for i in range(len(seg)):
        b = int(seg[i])
        if b < 0 or not (ok[i] and ok[i + 1]):
            continue        # a GPS dropout, or a sample with no position: no chord across it
        pen = QPen(colours[b], line_px)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(QPointF(px[i], py[i]), QPointF(px[i + 1], py[i + 1]))
    if chord is not None:
        pen = QPen(colours[chord[2]], line_px)
        pen.setCapStyle(Qt.RoundCap)
        p.setPen(pen)
        p.drawLine(chord[0], chord[1])
    return px, py


def finish_at(session, xs, ys):
    """(x, y, ux, uy): where this lap crosses the start line and the way it runs there, or None.

    On the lap's OWN trace first, so the mark sits exactly where the line's two passes meet (the
    closing chord) — and the live map's all-laps crossing as the fallback. Same function
    (`map_render.start_crossing`), same majority rule for the direction. A lap's first sample lies
    ON the line, a rounding error to either side of it, so the lap is searched with its
    second-to-last sample (the approach to the line) in front: that step crosses it whichever side
    the first sample fell."""
    try:
        seg = session.start_line
        xs, ys = np.asarray(xs, float), np.asarray(ys, float)
        if len(xs) > 2 and math.hypot(xs[-1] - xs[0], ys[-1] - ys[0]) <= CLOSE_GAP_M:
            xs, ys = np.r_[xs[-2], xs], np.r_[ys[-2], ys]
        at = map_render.start_crossing(xs, ys, seg)
        if at is None:
            at = map_render.start_crossing(session.tx, session.ty, seg)
        return at
    except Exception:  # noqa: BLE001 — a picture without its mark beats no picture
        return None


def finish_mark_rect(c: QPointF, scale: float = 1.0) -> QRectF:
    """The px box the mark (squares and arrow) can reach about its centre, for label clearance."""
    _b, _s, _a, reach = map_render.finish_mark_geometry(1.0, 0.0)
    r = reach * scale
    return QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)


def paint_finish_mark(p: QPainter, c: QPointF, ux: float, uy: float, scale: float = 1.0) -> None:
    """The live map's start/finish mark at `c` (px), `scale` times its live size: the dark backing,
    the light chequer squares, and the light arrow along (ux, uy) with a dark rim — the brake
    glyphs' answer to a light ink over the colour-blind ramp's light middle."""
    backing, squares, arrow, _reach = map_render.finish_mark_geometry(ux, uy)

    def poly(pts):
        return QPolygonF([QPointF(c.x() + x * scale, c.y() + y * scale) for x, y in pts])

    p.save()
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(C.canvas))
    p.drawPolygon(poly(backing))
    p.setBrush(QColor(C.text))
    for q in squares:
        p.drawPolygon(poly(q))
    p.setPen(QPen(QColor(C.canvas), theme.line_width(1) * scale))
    p.drawPolygon(poly(arrow))
    p.restore()


def paint_speed_key(p: QPainter, x: float, base: float, lo: str, hi: str, font,
                    ramp_px: float, ramp_h: float = 4.0, gap: float = theme.SPACE_S) -> float:
    """The line's key on `base` from `x`: the low end, the palette's ramp, the high end ("38",
    ramp, "71 km/h") in the muted ink. Returns its width."""
    fm = QFontMetrics(font)
    p.setFont(font)
    lo_w = fm.horizontalAdvance(lo)
    p.setPen(QColor(C.text_muted))
    p.drawText(QPointF(x, base), lo)
    x0 = x + lo_w + gap
    ramp = QRectF(x0, base - fm.ascent() / 2 - ramp_h / 2, ramp_px, ramp_h)
    grad = QLinearGradient(ramp.topLeft(), ramp.topRight())
    cols = theme.rainbow_colors(theme.MAP_RAINBOW_N)
    for i, col in enumerate(cols):
        grad.setColorAt(i / (len(cols) - 1), QColor(col))
    p.setPen(Qt.NoPen)
    p.setBrush(grad)
    p.drawRoundedRect(ramp, ramp_h / 2, ramp_h / 2)
    p.setPen(QColor(C.text_muted))
    p.drawText(QPointF(x0 + ramp_px + gap, base), hi)
    return x0 - x + ramp_px + gap + fm.horizontalAdvance(hi)


def label_rect(c: QPointF, ang: float, tw: float, th: float, placed: list[QRectF], box: QRectF,
               gap: float, line: tuple[np.ndarray, np.ndarray, float] | None = None) -> QRectF:
    """A corner label's plate: outward from the trace's centroid (`ang`), then the other seven
    compass points, the first clear of every placed plate and inside `box`; when none is, the one
    that overlaps least (its area over other plates, plus its area outside the box). `gap` is the
    apex → label-centre distance. `line` = (px, py, half-width) also prices the plate's overlap
    with the drawn line (the share card names every corner, so a name must not sit on the track);
    without it the placement is exactly the hero's."""
    best, best_cost = QRectF(), math.inf
    if line is not None:      # the line's samples and its segment midpoints: ≤ ~5 px apart
        lx, ly, half = line
        ok = np.isfinite(lx) & np.isfinite(ly)
        lx, ly = lx[ok], ly[ok]
        lx, ly = np.r_[lx, (lx[:-1] + lx[1:]) / 2], np.r_[ly, (ly[:-1] + ly[1:]) / 2]
    for k in range(8):
        a = ang + (k + 1) // 2 * (math.pi / 4) * (1 if k % 2 else -1)
        # The centre sits the gap out less a little on each axis (6 px across, 4 px down), so the
        # plate's near edge, not its centre, lands just clear of the ring.
        lc = QPointF(c.x() + math.cos(a) * (gap + tw / 2 - 6),
                     c.y() + math.sin(a) * (gap + th / 2 - 4))
        rect = QRectF(lc.x() - tw / 2, lc.y() - th / 2, tw, th)
        cost = (sum(_area(rect.intersected(r)) for r in placed)
                + _area(rect) - _area(rect.intersected(box)))
        if line is not None:
            on = ((lx > rect.left() - half) & (lx < rect.right() + half)
                  & (ly > rect.top() - half) & (ly < rect.bottom() + half))
            cost += 4.0 * half * half * int(on.sum())
        if cost <= 0:
            return rect
        if cost < best_cost:
            best, best_cost = rect, cost
    return best


def _area(r: QRectF) -> float:
    return max(r.width(), 0.0) * max(r.height(), 0.0)


def paint_ring(p: QPainter, c: QPointF, radius: float, rim: QColor, rim_px: float,
               ring_px: float) -> None:
    """A focus corner's ring: filled and rimmed in `rim` (the line stops at it rather than crossing
    it), then the accent ring — the colour of the first Jump."""
    p.setPen(QPen(rim, rim_px))
    p.setBrush(rim)
    p.drawEllipse(c, radius, radius)
    p.setPen(QPen(QColor(C.accent), ring_px))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(c, radius, radius)
