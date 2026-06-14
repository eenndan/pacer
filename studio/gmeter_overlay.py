"""GMeterOverlay: a subtle "G meter" dial painted OVER the video (felt-force convention).

A frameless, translucent, always-on-top top-level window pinned to the QVideoWidget's TOP-RIGHT
corner and sized as a fraction of the video, drawn with a faint backdrop so it sits subtly over
footage. It shows the inertial reaction the DRIVER'S BODY feels (not the raw acceleration
vector), a translucent red max-G envelope of the grip used this lap, and the peak g reached in
each cardinal direction.

Why a top-level window and not a plain child widget: on macOS a QVideoWidget renders through a
NATIVE surface (its own QWindow) that the window-server composites independently of Qt's z-order
for ordinary ("alien", non-native) child widgets. A child overlay therefore lands in the parent's
backing store and is painted OVER by the native video layer — invisible on screen even though it
exists in the widget tree. Giving the overlay its own native top-level window makes the
window-server composite it as a separate layer ABOVE the video surface, so it shows over live
footage. The VideoView keeps it pinned to the video's corner on move/resize/show.

THE FELT-FORCE CONVENTION (the pointer = what the driver's body feels, not the accel vector)
-------------------------------------------------------------------------------------------
`session.g_at_time` reports the kart-frame ACCELERATION in g (validated in studio/gmeter.py):
  +lateral      = turning LEFT,     -lateral      = turning RIGHT
  +longitudinal = ACCELERATING,     -longitudinal = BRAKING
The body feels the inertial REACTION — the opposite of the acceleration. We map that felt force
onto the dial so it reads like the g-meter in a car:
  * BRAKING (long<0)  -> pointer UP    (you're thrown forward / "up" on the meter)
  * ACCELERATING      -> pointer DOWN
  * turning RIGHT     -> pointer LEFT  (you're thrown to the left)
  * turning LEFT      -> pointer RIGHT
Screen mapping (screen +x = right, screen +y = down):
  dx =  +lateral * scale        (left-turn -> right; right-turn -> left)
  dy =  +longitudinal * scale   (braking long<0 -> up; accelerating long>0 -> down)
i.e. the pointer is the felt force −(accel) expressed in the dial's up=forward-thrown frame.

HELMET-SHAKE FILTERING (the GoPro is chin-mounted; the accel carries head/mount jitter)
---------------------------------------------------------------------------------------
The gross g is good (lateral cross-check r≈0.90) but a chin mount adds high-frequency shake on
top of the real vehicle g. So the DOT is driven by a short exponential moving average of the
felt-force g (smooth but still responsive to real braking/cornering), and the ENVELOPE / peak-g
NUMBERS use that filtered signal PLUS a high-percentile (robust) peak so a single shake spike
can't blow the envelope or the cardinal numbers out to a spurious huge value.

MAX-G ENVELOPE (the red blob) + cardinal peaks
----------------------------------------------
Filtered felt-force points are accumulated and their swept area is filled in translucent red
(a convex hull of the points) — the grip envelope used in the current scope. The four cardinal
numbers are the robust peak felt-g reached forward/back/left/right. Scope defaults to the
CURRENT LAP and resets at the lap boundary (`set_lap` drives this); change `_RESET_ON_LAP` or
call `reset_envelope()` for other scopes.

This widget is `pacer`-free: it knows nothing about the telemetry source. The app feeds it
`set_g((lateral_g, longitudinal_g, total_g))` and `set_lap(lap_id)` at the ~30 Hz tick. The
convention flip + filtering are DISPLAY concerns and live here; the validated g values in
`session`/`gmeter.py` are untouched.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
    QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from .theme import C


def _c(token: str, alpha: int | None = None) -> QColor:
    """A QColor from a theme hex token, optionally overriding the alpha (0-255). Lets the overlay
    paint with the design tokens while keeping its hand-tuned per-element translucency."""
    col = QColor(token)
    if alpha is not None:
        col.setAlpha(alpha)
    return col

# Visual scale: the outer ring is this many g (so a 1.0 g corner sits well inside and a ~1.5 g
# spike still lands within the dial). The labelled rings follow _RINGS.
_FULL_SCALE_G = 1.6
_RINGS = (0.5, 1.0)              # labelled rings (g)

# Pointer low-pass: exponential moving average factor per ~30 Hz sample. Smaller = smoother but
# laggier. ~0.30 gives a ~0.1 s time-constant — tames chin-mount/helmet shake while staying
# responsive to a real brake/turn transition.
_DOT_EMA_ALPHA = 0.30

# Envelope robustness: we keep a rolling window of recent FILTERED felt-force points and grow
# the per-direction peaks only from a high PERCENTILE of the magnitude in each direction, so a
# lone helmet-shake spike never sets a cardinal peak or pushes the hull out. The hull is built
# from the filtered points themselves (already de-spiked by the EMA + the percentile gate).
_PEAK_PERCENTILE = 90.0          # cardinal peak = this percentile of recent felt-g (robust)
_PEAK_WINDOW = 90                # samples (~3 s at 30 Hz) feeding the percentile peak
_ENVELOPE_MAX_PTS = 240          # cap on hull input points per scope (ring buffer)
_RESET_ON_LAP = True             # reset the envelope + peaks at each lap boundary


def _font(pt: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


@dataclass
class DialState:
    """The pure DRAW state of the g-meter dial — everything `paint_dial` needs, decoupled from
    the live QWidget. The live overlay snapshots its own fields into one of these per paint
    (`GMeterOverlay._dial_state`); the OFFLINE video exporter (studio/export_video.py) drives a
    headless `GMeterOverlay` exactly like the live tick and snapshots the same way, so the burned-
    in dial is byte-identical to the on-screen one for the same g/lap history. Felt-force axes:
    +fx = thrown right, +fy(down) = thrown back (accel), -fy(up) = thrown forward (brake)."""
    fx: float = 0.0                    # filtered felt-force lateral (= +lateral g)
    fy: float = 0.0                    # filtered felt-force longitudinal (= +longitudinal g)
    have: bool = False                 # is there a live g reading to draw the dot for?
    hull_pts: list[tuple[float, float]] = field(default_factory=list)  # filtered felt points
    peak_fwd: float = 0.0              # braking  (felt UP)
    peak_back: float = 0.0             # accel    (felt DOWN)
    peak_left: float = 0.0             # turning right (felt LEFT)
    peak_right: float = 0.0            # turning left  (felt RIGHT)
    source: str = "accl"               # which sensor drives the meter ("accl"/"gps")


def dial_geom(w: float, h: float):
    """Centre + radius of the dial inside a (w, h) box. A slim title strip up top; the cardinal
    numbers sit just outside the outer ring, so reserve a uniform margin for them. Shared by the
    live widget (`GMeterOverlay._geom`) and the offline renderer so both lay out identically."""
    title_h = 18
    margin = 18                       # room for the cardinal peak numbers outside the ring
    dial_top = title_h
    dial_h = h - title_h
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


# --------------------------------------------------------------------------- export palette
# Vivid, opaque colours for the EXPORT render of the dial (burned over BRIGHT outdoor footage),
# kept separate from the dim-on-dark live theme tokens (class C). Mirrors studio.export_video.EXPORT
# so the burned g-meter matches the rest of the burned HUD; defined locally so this shared,
# pacer-free module needs no import from the exporter. The LIVE overlay never uses these — it keeps
# the C.* tokens — so its on-screen look is byte-identical to before.
_EX_TEXT = "#FFFFFF"
_EX_HALO = "#0A0C10"          # dark outline/shadow under every bright element
_EX_ACCENT = "#FFB21E"        # envelope amber (brighter + saturated vs C.accent)
_EX_ACCENT_HI = "#FFD34D"     # dot glow highlight
_EX_GRID = "#FFFFFF"          # rings / crosshair (white at moderate alpha)


def _draw_text_outlined(p: QPainter, rect: QRectF, flags, text: str, font: QFont,
                        colour: str, halo: float = 2.2) -> None:
    """Draw `text` aligned within `rect` (Qt alignment flags) with a dark OUTLINE under a bright
    fill — the EXPORT legibility treatment so a burned label reads over bright sky AND dark tarmac.
    Used only by the export branch of `paint_dial`; the live widget draws plain text as before."""
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
    """Dial centre + radius for the EXPORT render. Like `dial_geom` but reserves a LARGER margin so
    the (much bigger) cardinal peak numbers sit clearly outside the ring, and drops the live widget's
    title strip so the dial fills more of the box (the export has no 'G meter' caption-bar look).
    Separate from `dial_geom` so the LIVE widget's geometry — and its tests — are unchanged."""
    margin = 0.20 * min(w, h)          # room for the larger outlined cardinal numbers
    r = max((min(w, h) - 2 * margin) / 2.0, 8.0)
    return w / 2.0, h / 2.0, r


def paint_dial(p: QPainter, w: float, h: float, st: DialState,
               export: bool = False, scale_k: float = 1.0) -> None:
    """Paint the g-meter dial (backdrop, rings, max-G envelope, cardinal peaks, live dot, source
    tag) into the painter's current coordinate system, sized to a (w, h) box at the origin. This
    is the EXTRACTED body of the live overlay's old paintEvent — the single source of the dial's
    look, used by both `GMeterOverlay.paintEvent` (snapshotting `self`) and the offline video
    exporter (snapshotting a headless overlay). No widget state is touched here.

    `export=False` (the default — what the LIVE widget passes) renders EXACTLY as before, so the
    on-screen overlay is byte-identical. `export=True` renders the EXPORT variant for burning over
    bright footage: NO backdrop box, white high-contrast rings/crosshair, a brighter envelope, a
    bigger glowing dot, and SUBSTANTIALLY larger outlined cardinal-g numbers (the roadmap's
    "bigger g-meter numbers, no box, more vivid"). `scale_k` scales the export's line widths/dot/
    glyphs so the dial looks right at any `out_height` (1.0 ≈ a ~280 px dial at 1080p)."""
    p.setRenderHint(QPainter.Antialiasing, True)
    if export:
        _paint_dial_export(p, w, h, st, scale_k)
        return
    cx, cy, r = dial_geom(w, h)

    # --- faint, subtle backdrop (more see-through than a solid panel) ---
    # C.canvas (the darkest neutral) at low alpha so the video shows through; a hairline
    # border in C.border_strong (also dimmed) frames it without drawing attention.
    backdrop = QRectF(1, 1, w - 2, h - 2)
    p.setBrush(_c(C.canvas, 150))
    p.setPen(QPen(_c(C.border_strong, 90), 1))
    p.drawRoundedRect(backdrop, 12, 12)

    # --- title: "G meter" ---
    p.setPen(QPen(_c(C.text_dim, 220)))
    p.setFont(_font(8.5, bold=True))
    p.drawText(QRectF(0, 3, w, 14), Qt.AlignHCenter | Qt.AlignVCenter, "G meter")

    # --- concentric rings (thin, clean) ---
    p.setBrush(Qt.NoBrush)
    for gval in _RINGS:
        rr = r * (gval / _FULL_SCALE_G)
        p.setPen(QPen(_c(C.border, 200), 1.0))   # inner grid circles — dim
        p.drawEllipse(QPointF(cx, cy), rr, rr)
    # outer boundary ring — a touch stronger than the inner grid circles
    p.setPen(QPen(_c(C.border_strong, 220), 1.2))
    p.drawEllipse(QPointF(cx, cy), r, r)
    # faint crosshair guides (tick/axis marks) — muted
    p.setPen(QPen(_c(C.text_muted, 90), 0.8))
    p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
    p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))

    # --- filled max-G envelope (translucent blob = grip used this lap) ---
    # Recoloured from red to the accent amber: outline C.accent, fill C.accent at low alpha so
    # the grip envelope reads clearly over footage without dominating.
    if len(st.hull_pts) >= 3:
        hull = _convex_hull(st.hull_pts)
        if len(hull) >= 3:
            poly = QPolygonF([QPointF(*dial_to_screen(cx, cy, r, hx, hy))
                              for (hx, hy) in hull])
            path = QPainterPath()
            path.addPolygon(poly)
            path.closeSubpath()
            p.setPen(QPen(_c(C.accent, 170), 1.2))
            p.setBrush(_c(C.accent, 56))
            p.drawPath(path)

    # --- cardinal peak-g numbers (robust max felt-g per direction) ---
    p.setFont(_font(8.0, bold=True))
    p.setPen(QPen(_c(C.text_dim, 230)))   # axis value labels — dimmed off-white
    off = 11
    # forward (braking) at top, back (accel) at bottom, left/right on the sides
    p.drawText(QRectF(cx - 22, cy - r - off - 6, 44, 12), Qt.AlignCenter, f"{st.peak_fwd:.1f}")
    p.drawText(QRectF(cx - 22, cy + r + off - 6, 44, 12), Qt.AlignCenter, f"{st.peak_back:.1f}")
    p.drawText(QRectF(cx - r - off - 22, cy - 6, 44, 12), Qt.AlignRight | Qt.AlignVCenter,
               f"{st.peak_left:.1f}")
    p.drawText(QRectF(cx + r + off - 22, cy - 6, 44, 12), Qt.AlignLeft | Qt.AlignVCenter,
               f"{st.peak_right:.1f}")

    # --- the live dot (NO line from centre to the dot — just a soft glow + dot) ---
    # An accent (amber) glow fading out, with a bright off-white (C.text) core so the felt-
    # force pointer is clearly visible over the footage and reads as the live indicator.
    if st.have:
        dx, dy = dial_to_screen(cx, cy, r, st.fx, st.fy)
        grad = QRadialGradient(QPointF(dx, dy), 8)
        grad.setColorAt(0.0, _c(C.accent, 235))
        grad.setColorAt(1.0, _c(C.accent, 0))
        p.setPen(Qt.NoPen)
        p.setBrush(grad)
        p.drawEllipse(QPointF(dx, dy), 7, 7)
        p.setBrush(_c(C.text, 245))
        p.drawEllipse(QPointF(dx, dy), 2.6, 2.6)

    # --- source tag (tiny, bottom-right) ---
    p.setPen(QPen(_c(C.text_muted, 150)))
    p.setFont(_font(6.0))
    p.drawText(QRectF(w - 44, h - 13, 40, 11), Qt.AlignRight, st.source.upper())


def _paint_dial_export(p: QPainter, w: float, h: float, st: DialState, k: float) -> None:
    """The EXPORT g-dial: no backdrop box, vivid white high-contrast rings, a brighter amber grip
    envelope, BIG outlined cardinal-g numbers, and a bigger glowing felt-force dot — all carrying a
    dark halo so they read over bright sky AND dark tarmac. Layout uses `_export_dial_geom` (bigger
    number margin, no title strip). `k` scales strokes/glyphs with the output height."""
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
        # A frameless, translucent, always-on-top TOOL window — its own native layer so the
        # window-server composites it ABOVE the QVideoWidget's native video surface (a plain
        # child widget would be hidden behind that surface on macOS). `parent` is kept only for
        # ownership/lifetime; the window is positioned in GLOBAL screen coords by the VideoView.
        super().__init__(parent, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint)
        # Transparent, click-through window painted on top of the video.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setMinimumSize(120, 140)
        # Live felt-force pointer (filtered) in g; dial axes are felt: +x = thrown right,
        # +y(down) = thrown back (accelerating), -y(up) = thrown forward (braking).
        self._fx = 0.0                 # filtered felt-force lateral (= +lateral g)
        self._fy = 0.0                 # filtered felt-force longitudinal (= +longitudinal g)
        self._have = False             # is there a current g reading to draw the dot for?
        self._ema_init = False         # has the EMA been seeded yet?
        self._source = "accl"
        # Envelope + robust peaks, accumulated over the current scope (default: this lap).
        self._hull_pts: list[tuple[float, float]] = []     # filtered felt points (ring buffer)
        self._recent: list[tuple[float, float]] = []       # rolling window for percentile peaks
        # Cardinal peak felt-g: forward (brake), back (accel), left, right (all >= 0).
        self._peak_fwd = 0.0           # braking  (felt UP)
        self._peak_back = 0.0          # accel    (felt DOWN)
        self._peak_left = 0.0          # turning right (felt LEFT)
        self._peak_right = 0.0         # turning left  (felt RIGHT)
        self._lap: int | None = None   # current lap id (for the per-lap reset)

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
        # Felt-force convention: pointer is the inertial reaction the body feels (see module doc).
        #   felt x = +lateral      (turn left -> right on the dial; turn right -> left)
        #   felt y = +longitudinal (braking long<0 -> up; accelerating long>0 -> down)
        fx, fy = lat, lon
        # Shake low-pass (EMA) for a smooth dot that tracks vehicle g, not head/mount jitter.
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
        """Grow the per-lap envelope + robust cardinal peaks from the FILTERED felt point.

        Robustness to chin-mount shake (two layers): the cardinal numbers track a high PERCENTILE
        of the recent filtered magnitude in each direction (not the instantaneous max), so a lone
        spike that slips through the EMA can't set a peak; and the hull point is CLAMPED to those
        robust per-direction peaks before it's added, so a single spike can never push the blob
        past the robust envelope either. (The EMA already de-spikes the dot itself.)"""
        self._recent.append((fx, fy))
        if len(self._recent) > _PEAK_WINDOW:
            self._recent.pop(0)
        # Per-direction robust peak from the rolling window: the felt extent reached in each of
        # the four cardinals, taken at _PEAK_PERCENTILE so a single shake sample doesn't win.
        # Single pass partitions the window into the four sign-split lists (was four separate
        # comprehensions, each re-iterating _recent); the per-list _pct is unchanged so the peaks
        # are byte-identical.
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
        # Clamp the hull candidate to the robust per-direction peaks so one spike can't balloon the
        # blob (the peaks themselves are percentile-gated). A tiny margin keeps the dot inside.
        hx = min(fx, self._peak_right) if fx >= 0 else max(fx, -self._peak_left)
        hy = min(fy, self._peak_back) if fy >= 0 else max(fy, -self._peak_fwd)
        self._hull_pts.append((hx, hy))
        if len(self._hull_pts) > _ENVELOPE_MAX_PTS:
            self._hull_pts.pop(0)

    def set_lap(self, lap_id: int | None) -> None:
        """Tell the meter which lap is being driven. When it CHANGES to a new valid lap (and
        _RESET_ON_LAP), the envelope + cardinal peaks reset so the blob shows THIS lap's grip
        usage. `None` (lead-in / between laps) is held — never resets — so the envelope persists
        across the brief no-lap gaps."""
        if lap_id is None or lap_id == self._lap:
            return
        if _RESET_ON_LAP and self._lap is not None:
            self.reset_envelope()
        self._lap = lap_id

    def set_source(self, source: str) -> None:
        """Label which sensor drives the meter ("accl" or "gps"); shown small in the corner."""
        self._source = source
        self.update()

    def reset_envelope(self) -> None:
        """Clear the accumulated max-G envelope + cardinal peaks (new scope, e.g. a new lap), AND
        re-seed the live-dot EMA so the filtered pointer starts fresh on the new scope's first
        sample instead of carrying the previous lap's filtered value (which would make the dot drift
        in from the old lap's position on a per-lap reset)."""
        self._hull_pts.clear()
        self._recent.clear()
        self._peak_fwd = self._peak_back = self._peak_left = self._peak_right = 0.0
        # Re-seed the dot EMA: the next set_g seeds _fx/_fy from its own value (no carry-over).
        self._ema_init = False
        self._fx = self._fy = 0.0
        self.update()

    # ------------------------------------------------------------------ painting
    def _geom(self):
        """Centre + radius of the dial (delegates to the shared `dial_geom`). Kept as a thin
        method so the existing offscreen tests that call `ov._geom()` / `ov._to_screen(...)`
        keep working unchanged."""
        return dial_geom(self.width(), self.height())

    def _to_screen(self, cx, cy, r, fx, fy):
        """Felt-force point -> dial pixel (delegates to the shared `dial_to_screen`)."""
        return dial_to_screen(cx, cy, r, fx, fy)

    def _dial_state(self) -> DialState:
        """Snapshot the live filtering state into a pure `DialState` for `paint_dial`. The live
        paint path and the offline exporter both render through the same function from such a
        snapshot, so what's burned into the video matches what's on screen."""
        return DialState(
            fx=self._fx, fy=self._fy, have=self._have, hull_pts=list(self._hull_pts),
            peak_fwd=self._peak_fwd, peak_back=self._peak_back,
            peak_left=self._peak_left, peak_right=self._peak_right, source=self._source)

    def paintEvent(self, _event):
        p = QPainter(self)
        paint_dial(p, self.width(), self.height(), self._dial_state())
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
