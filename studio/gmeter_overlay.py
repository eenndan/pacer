"""GMeterOverlay: the classic friction-circle g-meter, painted ON the video.

A transparent child widget of the QVideoWidget, drawn in a corner with a semi-transparent
backdrop so it stays readable over footage. The template is the classic g-g diagram:

  * concentric rings labelled 0.5 / 1.0 / 1.5 g,
  * a crosshair: horizontal axis = LATERAL g (left/right), vertical axis = LONGITUDINAL g
    (UP = acceleration, DOWN = braking — the racing-driver convention),
  * a floating dot whose ANGLE is the force DIRECTION and whose RADIUS is the force MAGNITUDE
    (g). A short fading trail shows the recent path; a faint peak-hold ring marks the session's
    largest combined g so far while playing.
  * a numeric readout: lateral g, longitudinal g, and total (combined) g.

This widget is `pacer`-free: it knows nothing about the telemetry source. The app feeds it
`(lateral_g, longitudinal_g, total_g)` from `session.g_at_time` at the existing ~30 Hz tick
(a cheap precomputed lookup — no per-frame computation). `set_g(None)` blanks the dot/readout
(outside a valid lap / no IMU). A toggle controls visibility.

Sign convention (matches session.g_at_time): +lateral = turning LEFT, +longitudinal =
accelerating. We map +lateral to the RIGHT on screen (the dot swings toward the OUTSIDE of the
corner — i.e. the direction the driver feels thrown, which is the classic g-meter reading) and
+longitudinal to UP.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget

# Visual scale: the outer ring is this many g (so 1.5 g sits at 0.75 of the radius and a 2 g
# spike still lands inside the widget). Tunable; the ring labels follow _RINGS.
_FULL_SCALE_G = 2.0
_RINGS = (0.5, 1.0, 1.5)         # labelled rings (g)
_TRAIL_LEN = 18                  # recent dot positions kept for the fading trail
_MARGIN = 14                     # px padding inside the widget for the ring + labels


def _font(pt: float, bold: bool = False) -> QFont:
    f = QFont()
    f.setPointSizeF(pt)
    f.setBold(bold)
    return f


class GMeterOverlay(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        # Transparent, click-through child painted on top of the video.
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setMinimumSize(180, 210)
        self._lat = 0.0
        self._long = 0.0
        self._total = 0.0
        self._have = False             # is there a current g reading to draw the dot for?
        self._trail: list[tuple[float, float]] = []  # recent (lat, long) for the fade trail
        self._peak = 0.0               # peak combined g seen since the last reset (peak-hold)
        self._source = "accl"

    # ------------------------------------------------------------------ data in
    def set_g(self, g: tuple[float, float, float] | None) -> None:
        """Push the current (lateral_g, longitudinal_g, total_g). None blanks the live dot
        (keeps the template). Records the trail + peak-hold. Triggers a repaint."""
        if g is None:
            if self._have:
                self._have = False
                self.update()
            return
        lat, lon, total = g
        self._lat, self._long, self._total = lat, lon, total
        self._have = True
        self._trail.append((lat, lon))
        if len(self._trail) > _TRAIL_LEN:
            self._trail.pop(0)
        if total > self._peak:
            self._peak = total
        self.update()

    def set_source(self, source: str) -> None:
        """Label which sensor drives the meter ("accl" or "gps"); shown small in the corner."""
        self._source = source
        self.update()

    def reset_peak(self) -> None:
        self._peak = 0.0
        self._trail.clear()
        self.update()

    # ------------------------------------------------------------------ painting
    def _geom(self):
        """Centre + radius of the dial, fit to the widget with room for labels."""
        w, h = self.width(), self.height()
        # Reserve a strip at the bottom for the numeric readout.
        dial_h = h - 46
        r = (min(w, dial_h) - 2 * _MARGIN) / 2.0
        r = max(r, 10.0)
        cx = w / 2.0
        cy = _MARGIN + r
        return cx, cy, r

    def _to_screen(self, cx, cy, r, lat, lon):
        """Map (lateral_g, longitudinal_g) to a screen point. +lateral -> RIGHT, +long -> UP.
        Clamped to the dial circle so a huge spike stays on the edge (direction preserved)."""
        scale = r / _FULL_SCALE_G
        dx = lat * scale          # +lateral to the right
        dy = -lon * scale         # +longitudinal up (screen y grows downward)
        d = math.hypot(dx, dy)
        if d > r:
            dx, dy = dx / d * r, dy / d * r
        return cx + dx, cy + dy

    def paintEvent(self, _event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        cx, cy, r = self._geom()

        # --- semi-transparent backdrop so the dial reads over bright footage ---
        backdrop = QRectF(2, 2, self.width() - 4, self.height() - 4)
        p.setBrush(QColor(10, 12, 16, 150))
        p.setPen(QPen(QColor(255, 255, 255, 40), 1))
        p.drawRoundedRect(backdrop, 10, 10)

        # --- concentric rings + labels ---
        grid = QColor(210, 220, 235, 110)
        p.setBrush(Qt.NoBrush)
        for gval in _RINGS:
            rr = r * (gval / _FULL_SCALE_G)
            p.setPen(QPen(grid, 1.2))
            p.drawEllipse(QPointF(cx, cy), rr, rr)
        # outer boundary ring (full scale)
        p.setPen(QPen(QColor(230, 238, 250, 160), 1.6))
        p.drawEllipse(QPointF(cx, cy), r, r)

        # ring labels (along the upper-right diagonal so they don't sit on the axes)
        p.setPen(QPen(QColor(200, 210, 225, 150)))
        p.setFont(_font(7.5))
        for gval in _RINGS:
            rr = r * (gval / _FULL_SCALE_G)
            lx = cx + rr * 0.707 + 2
            ly = cy - rr * 0.707 - 1
            p.drawText(QPointF(lx, ly), f"{gval:g}")

        # --- crosshair axes ---
        axis = QColor(210, 220, 235, 90)
        p.setPen(QPen(axis, 1.0))
        p.drawLine(QPointF(cx - r, cy), QPointF(cx + r, cy))
        p.drawLine(QPointF(cx, cy - r), QPointF(cx, cy + r))
        # axis hint letters: ACC (up), BRK (down), L / R (sides)
        p.setPen(QPen(QColor(170, 185, 205, 170)))
        p.setFont(_font(7.0, bold=True))
        p.drawText(QRectF(cx - 18, cy - r - 1, 36, 12), Qt.AlignCenter, "ACCEL")
        p.drawText(QRectF(cx - 18, cy + r - 11, 36, 12), Qt.AlignCenter, "BRAKE")

        # --- peak-hold ring (faint) ---
        if self._peak > 0.05:
            pr = min(self._peak, _FULL_SCALE_G) / _FULL_SCALE_G * r
            p.setPen(QPen(QColor(255, 209, 102, 120), 1.0, Qt.DashLine))
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), pr, pr)

        # --- fading trail ---
        n = len(self._trail)
        for k, (lat, lon) in enumerate(self._trail):
            tx, ty = self._to_screen(cx, cy, r, lat, lon)
            alpha = int(20 + 90 * (k + 1) / max(n, 1))
            rad = 1.5 + 2.0 * (k + 1) / max(n, 1)
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(6, 214, 160, alpha))
            p.drawEllipse(QPointF(tx, ty), rad, rad)

        # --- the floating dot (direction = angle, magnitude = radius) ---
        if self._have:
            dx, dy = self._to_screen(cx, cy, r, self._lat, self._long)
            # a short line from centre to the dot makes the direction unmistakable
            p.setPen(QPen(QColor(6, 214, 160, 160), 1.6))
            p.drawLine(QPointF(cx, cy), QPointF(dx, dy))
            # glowing dot
            grad = QRadialGradient(QPointF(dx, dy), 9)
            grad.setColorAt(0.0, QColor(120, 255, 210, 255))
            grad.setColorAt(1.0, QColor(6, 214, 160, 60))
            p.setPen(Qt.NoPen)
            p.setBrush(grad)
            p.drawEllipse(QPointF(dx, dy), 7, 7)
            p.setBrush(QColor(255, 255, 255, 235))
            p.drawEllipse(QPointF(dx, dy), 3, 3)

        # --- numeric readout ---
        ry = cy + r + 8
        p.setPen(QPen(QColor(235, 240, 248, 235)))
        p.setFont(_font(8.5, bold=True))
        if self._have:
            lat_dir = "R" if self._lat >= 0 else "L"
            lon_dir = "accel" if self._long >= 0 else "brake"
            line1 = f"lat {abs(self._lat):.2f}g {lat_dir}   long {abs(self._long):.2f}g {lon_dir}"
            line2 = f"total {self._total:.2f} g"
        else:
            line1 = "lat —   long —"
            line2 = "total — g"
        p.drawText(QRectF(4, ry, self.width() - 8, 16), Qt.AlignCenter, line1)
        p.setFont(_font(9.5, bold=True))
        p.setPen(QPen(QColor(6, 214, 160, 245)))
        p.drawText(QRectF(4, ry + 15, self.width() - 8, 18), Qt.AlignCenter, line2)

        # source tag (tiny, bottom-left)
        p.setPen(QPen(QColor(150, 165, 185, 160)))
        p.setFont(_font(6.5))
        p.drawText(QRectF(8, self.height() - 14, 60, 12), Qt.AlignLeft, self._source.upper())

        p.end()
