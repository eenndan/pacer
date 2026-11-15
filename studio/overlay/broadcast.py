"""The BROADCAST overlay style: an option in the export dialog's Style row (Classic stays the
default, and byte-identical). It reads like a race broadcast graphic — a timing tower top-left, a
Δ bar top-centre, a big speed bottom-centre, a bigger minimap with its corners labelled — and it
says nothing the Classic overlay does not: every figure is the Classic figure, read through the
Classic function, and only drawn differently.

WHAT EACH PART SHOWS, AND WHERE THE NUMBER COMES FROM:
  * the TOWER (replaces the lap strip): the lap, its clock and its Δ — the three runs of the Classic
    strip, from `export_video._strip_runs`, the function the strip paints and its pill budget
    measures. The lap clock at the finish IS the lap time the Laps table prints. The Δ's baseline is
    NAMED as the app names it (`Δbest`, or `Δref` with a cross-recording reference loaded: the
    hero readout's names since 0.8.0; the export's Δ is `delta_at_lap`, never the ideal). On the
    best lap the strip's `★ BEST` mark stands in for it, as in Classic. A thin amber line under the
    header is the strip's progress fill;
  * the Δ BAR (with the tower, replaces the strip): the same Δ as a bar from the centre, on a scale
    fixed for the whole export (the next round step above the largest |Δ| it will show, printed at
    both ends — the bar never clips). INSIDE TIMING PRECISION (`library.within_timing_precision`,
    0.07 s) the bar and the tower's Δ are the neutral white, not gain/loss: decided on the PRINTED
    value (`theme.format_delta_value`), as the Laps table's Δbest and the compare seam decide, so a
    "+0.07" never reads level while another "+0.07" reads lost. Not drawn on the best lap (a
    self-zero), where the tower carries the `★ BEST` mark;
  * the BIG SPEED (replaces the speed readout): `export_video._readout_runs` — the nearest 10 Hz
    sample, never interpolated (timeline.py: "a speed is a measurement"), in the app's km/h or mph;
  * the MINIMAP is the Classic inset at `layout.MAP_H_FRAC` of the short side (it was 0.22), so the
    kart dot, its tail and the ghost ring ride it unchanged; `MapLabelsElement` adds the corners'
    C-labels at the apexes the app's map labels (`corner_map_markers`), under the dot;
  * the HONESTY STAMP is the Classic one — the same lines from `data_quality.burned_timing_stamp`,
    painted by the same `_paint_stamp` at the same size — moved to hang under the tower instead of
    under the strip (the seam's `occupies`); it is never dropped and never shrunk.

PRE-RENDERED: everything that does not change within an export (the plates, the header and its
lap, the bar's track, its scale and its baseline name, the speed's unit) is drawn ONCE in `prepare`
into images; a frame blits them and draws only what moves — the clock live, the progress and bar
fills as two rectangles, and the speed and Δ texts from a per-string cache (a speed holds for
three frames, a two-decimal Δ for many). The design lane measured live painting at 27 ms a 4K
frame, +45 % on an export; the budget is the Classic overlay's own time + 1.2 ms
(tests/test_export_broadcast.py and the PR carry the measurement).

POSITIONS ARE DATA: `studio/overlay/layout.py` (tokens + a table with a portrait override).
Faces are the export's own (`export_video._font`: Inter, bold, tabular figures).
"""
from __future__ import annotations

import math
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from studio import library, theme, units
from studio.export_palette import EXPORT
from studio.overlay import LAYER_MAP, LAYER_TOP, STAMP, ElementContext, layout
from studio.overlay.corner_cards import baseline_word

STYLE_CLASSIC = "classic"
STYLE_BROADCAST = "broadcast"
PRECISION_S = library.PB_PRECISION_S
_SCALE_STEPS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0)
_PLATE_ALPHA = 200
_PENDING_DELTA = ""


def styled(config, style: str):
    """`config` for `style`: Classic returns it untouched (the same object); Broadcast prepends its
    two elements and makes the minimap bigger."""
    if style != STYLE_BROADCAST:
        return config
    return replace(config, map_h_frac=layout.MAP_H_FRAC,
                   elements=(BroadcastElement.name, MapLabelsElement.name, *config.elements))


def baseline_name(session) -> str:
    """The Δ's baseline as the app's hero readout names it: `Δref` with a cross-recording reference
    loaded, else `Δbest` (the export's Δ is `delta_at_lap`; it is never the ideal)."""
    return "Δ" + baseline_word(session)


def printed_delta(d: float) -> float:
    """The Δ as it is printed (`theme.format_delta_value`, U+2212 minus), read back: the value the
    precision gate decides on."""
    return float(theme.format_delta_value(d).replace("−", "-"))


def is_level(d: float | None) -> bool:
    return d is not None and library.within_timing_precision(printed_delta(d))


def tower_texts(label: str, tail: str, name: str) -> tuple[str, str, str]:
    """The Classic strip's runs as the tower's three rows: ("LAP 14", "0:33.599", "Δbest +0.53").
    The strip's label is "LAP n   m:ss.mmm" (`_strip_runs`); its tail "Δ +0.53", `★ BEST` or ""."""
    head, _sep, clock = label.partition("   ")
    delta = name + tail[1:] if tail.startswith("Δ ") else tail
    return head, clock, delta


def delta_scale(max_abs: float) -> float:
    """The bar's half-range: the first round step at or above the largest |Δ| it will show."""
    return next((s for s in _SCALE_STEPS if s >= max_abs), math.ceil(max_abs / 60.0) * 60.0)


def _image(w: float, h: float) -> tuple[QImage, QPainter]:
    img = QImage(int(math.ceil(w)) + 2, int(math.ceil(h)) + 2, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    return img, p


def _plate(p: QPainter, r: QRectF, k: float) -> None:
    p.setBrush(theme.qcolor(EXPORT.halo, _PLATE_ALPHA))
    p.setPen(QPen(theme.qcolor(EXPORT.text, 55), 1.0 * k))
    p.drawRoundedRect(r, 7 * k, 7 * k)


def _adv(font, text: str) -> float:
    return QFontMetricsF(font).horizontalAdvance(text)


class BroadcastElement:
    name = "broadcast"
    layer = LAYER_TOP
    replaces = ("strip", "readout")

    # ------------------------------------------------------------------ once per export
    def prepare(self, ctx: ElementContext) -> bool:
        from studio import export_video as ev  # deferred: export_video imports this package
        self._ev = ev
        s, spec = ctx.session, ctx.spec
        cfg = spec.config
        self._session, self._spec, self._palette, self._unit = s, spec, cfg.palette, cfg.speed_unit
        self._name = baseline_name(s)
        u = float(min(ctx.out_w, ctx.out_h))
        self._k = ctx.k
        # Every string the export will burn, from the render's own frames through the Classic
        # run builders (as the Classic pill budget does): the tower and the speed plate are fitted
        # to them, and the bar's scale to the largest |Δ|.
        heads, clocks, deltas, speeds = [], [], [], []
        max_abs = 0.0
        times = ev.frame_times(spec.t0, spec.t1, ctx.fps)
        for t in (times if len(times) else [spec.t0]):
            vals = ev.overlay_values_at(s, float(t), spec)
            speeds.append(ev._readout_runs(vals, self._unit)[0])
            runs = ev._strip_runs(s, vals, spec.lap_t0, spec.is_best_at(vals.lap_id),
                                  self._palette)
            if runs is None:
                continue
            h, c, d = tower_texts(runs[0], runs[1], self._name)
            heads.append(h)
            clocks.append(c)
            deltas.append(d)
            if vals.lap_started and vals.delta_s is not None:
                max_abs = max(max_abs, abs(printed_delta(vals.delta_s)))
        self._scale = delta_scale(max_abs)
        # --- sizes (fractions of the short side, layout.py) and faces
        L = layout
        self._head_h, self._prog_h = L.TOWER_HEAD * u, L.TOWER_PROGRESS * u
        self._time_h, self._delta_h = L.TOWER_TIME * u, L.TOWER_DELTA * u
        self._pad = L.TOWER_PAD * u
        self._f_head = ev._font(self._head_h * 0.62, bold=True)
        self._f_time = ev._font(self._time_h * 0.80, bold=True)
        self._f_delta = ev._font(self._delta_h * 0.64, bold=True)
        tower_w = 2 * self._pad + max(
            [_adv(self._f_head, t) for t in heads] + [_adv(self._f_time, t) for t in clocks]
            + [_adv(self._f_delta, t) for t in deltas] + [0.18 * u])
        tower_h = L.BROADCAST["tower"].h * u
        sp_h = L.SPEED_H * u
        self._f_speed = ev._font(sp_h * 0.80, bold=True)
        self._f_unit = ev._font(sp_h * 0.26, bold=True)
        self._unit_label = units.speed_label(self._unit)
        self._sp_gap = 0.006 * u
        self._sp_num_w = max((_adv(self._f_speed, t) for t in speeds), default=0.0)
        speed_w = 2 * L.SPEED_PAD * u + self._sp_num_w + self._sp_gap + _adv(self._f_unit,
                                                                            self._unit_label)
        # --- the layout: the tower first (the stamp hangs under it), then the rest, clear of the
        # Classic plates this style keeps — or the export fails here, by name (`build`).
        slots = L.table(ctx.out_w, ctx.out_h)
        keep = {n: _box(ctx.plates[n]) for n in ("gmeter", "map")}
        boxes = L.resolve(slots, ["tower"], ctx.out_w, ctx.out_h, {"tower": (tower_w, tower_h)},
                          keep)
        tw = boxes["tower"]
        strip, stamp = ctx.plates["strip"], ctx.plates[STAMP]
        # The Classic stamp block (`stamp_block`) translated from under the strip to under the
        # tower: same lines, same face, same size — only the place moves.
        moved = QRectF(stamp).translated(tw.x - strip.x(), tw.bottom - strip.bottom())
        keep[STAMP] = _box(moved)
        boxes = L.resolve(slots, ["dbar", "speed"], ctx.out_w, ctx.out_h,
                          {"speed": (speed_w, sp_h)}, keep, placed=boxes)
        self.boxes = boxes
        self.tower, self.dbar, self.speed = (_rect(boxes[n]) for n in ("tower", "dbar", "speed"))
        # What the elements prepared after this one read as the plates (the corner cards: they keep
        # clear of the tower and the bar, and stand on the "readout" at its Classic size, which
        # sizes them) — that slot moved up to the big speed's top, so a card lands above it.
        readout = QRectF(ctx.plates["readout"])
        readout.moveTop(self.speed.y())
        self.occupies = {"strip": self.tower.united(self.dbar), "readout": readout, STAMP: moved}
        self._prerender()
        self._cache: dict[tuple[tuple[str, str], ...], QImage] = {}
        return True

    def _prerender(self) -> None:
        ev, k = self._ev, self._k
        # The tower's plate, its amber header band and the progress line's track.
        r = self.tower
        img, p = _image(r.width(), r.height())
        box = QRectF(1, 1, r.width(), r.height())
        _plate(p, box, k)
        clip = QPainterPath()
        clip.addRoundedRect(box, 7 * k, 7 * k)
        p.setClipPath(clip)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(EXPORT.accent, 240))
        p.drawRect(QRectF(box.x(), box.y(), box.width(), self._head_h))
        p.setBrush(theme.qcolor(EXPORT.accent, 70))
        p.drawRect(QRectF(box.x(), box.y() + self._head_h, box.width(), self._prog_h))
        p.end()
        self._tower_img = img
        self._heads: dict[str, QImage] = {}
        # The Δ bar: its track, the precision band round the centre, the scale at both ends and
        # the baseline's name over the centre.
        r = self.dbar
        img, p = _image(r.width(), r.height())
        lab_h = r.height() * 0.42
        bar = QRectF(1, 1 + lab_h, r.width(), r.height() - lab_h)
        self._bar = bar.translated(r.x() - 1, r.y() - 1)
        _plate(p, bar, k)
        half = bar.width() / 2.0
        band = half * PRECISION_S / self._scale
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(EXPORT.text, 46))
        p.drawRect(QRectF(bar.center().x() - band, bar.y() + 2 * k, 2 * band,
                          bar.height() - 4 * k))
        f = ev._font(lab_h * 0.86, bold=True)
        fm = QFontMetricsF(f)
        base = 1 + lab_h - fm.descent() - 1.0 * k
        sc = f"{self._scale:g}"
        ev._draw_text(p, QPointF(bar.x() + 4 * k, base), f"−{sc} s", f, EXPORT.text_dim,
                      halo=1.6 * k)
        right = f"+{sc} s"
        ev._draw_text(p, QPointF(bar.right() - 4 * k - fm.horizontalAdvance(right), base), right,
                      f, EXPORT.text_dim, halo=1.6 * k)
        ev._draw_text(p, QPointF(bar.center().x() - fm.horizontalAdvance(self._name) / 2, base),
                      self._name, f, EXPORT.text, halo=1.6 * k)
        p.end()
        self._dbar_img = img
        # The speed: its plate and its unit.
        r = self.speed
        img, p = _image(r.width(), r.height())
        _plate(p, QRectF(1, 1, r.width(), r.height()), k)
        fm = QFontMetricsF(self._f_speed)
        self._sp_base = (r.height() + fm.ascent() - fm.descent()) / 2.0
        # The number and its unit as one group, centred on the plate; the number is right-aligned
        # on the group's widest number, so the unit never moves.
        group = self._sp_num_w + self._sp_gap + _adv(self._f_unit, self._unit_label)
        num_right = 1 + (r.width() - group) / 2.0 + self._sp_num_w
        ev._draw_text(p, QPointF(num_right + self._sp_gap, 1 + self._sp_base), self._unit_label,
                      self._f_unit, EXPORT.text_dim, halo=1.8 * k)
        p.end()
        self._speed_img = img
        self._num_right = r.x() - 1 + num_right

    def _head(self, text: str) -> QImage:
        img = self._heads.get(text)
        if img is None:
            img, p = _image(self.tower.width(), self._head_h)
            fm = QFontMetricsF(self._f_head)
            self._ev._draw_text(p, QPointF(1 + self._pad, 1 + (self._head_h + fm.ascent()
                                                               - fm.descent()) / 2.0),
                                text, self._f_head, EXPORT.halo, halo=0.0, shadow=None)
            p.end()
            self._heads[text] = img
        return img

    def _text(self, runs: tuple[tuple[str, str], ...], font, h: float, halo: float) -> QImage:
        """`runs` ((text, colour), …) drawn once, haloed, left to right in an image `h` tall;
        cached by the runs."""
        img = self._cache.get(runs)
        if img is None:
            fm = QFontMetricsF(font)
            img, p = _image(sum(fm.horizontalAdvance(t) for t, _c in runs) + 4 * halo, h)
            x = 1 + 2 * halo
            for text, colour in runs:
                self._ev._draw_text(p, QPointF(x, 1 + (h + fm.ascent() - fm.descent()) / 2),
                                    text, font, colour, halo=halo)
                x += fm.horizontalAdvance(text)
            p.end()
            self._cache[runs] = img
        return img

    # ------------------------------------------------------------------ once per frame
    def state(self, vals):
        """What this frame shows: (head, clock, Δ row, Δ row colour, progress, bar Δ or None,
        speed). The Classic run builders decide every string."""
        ev, spec = self._ev, self._spec
        is_best = spec.is_best_at(vals.lap_id)
        runs = ev._strip_runs(self._session, vals, spec.lap_t0, is_best, self._palette)
        speed = ev._readout_runs(vals, self._unit)[0]
        if runs is None:
            return None, None, _PENDING_DELTA, None, 0.0, None, speed
        label, tail, colour, frac = runs
        head, clock, delta = tower_texts(label, tail, self._name)
        bar = None
        if tail.startswith("Δ ") and vals.delta_s is not None:
            bar = float(vals.delta_s)
            if is_level(bar):
                colour = EXPORT.neutral
        return head, clock, delta, colour, frac, bar, speed

    def paint(self, p: QPainter, vals) -> None:
        ev, k = self._ev, self._k
        head, clock, delta, colour, frac, bar, speed = self.state(vals)
        # --- the tower
        t = self.tower
        p.drawImage(QPointF(t.x() - 1, t.y() - 1), self._tower_img)
        if head:
            p.drawImage(QPointF(t.x() - 1, t.y() - 1), self._head(head))
        if frac > 0:
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(EXPORT.accent_bright, 255))
            p.drawRect(QRectF(t.x(), t.y() + self._head_h, t.width() * frac, self._prog_h))
        y = t.y() + self._head_h + self._prog_h
        if clock:
            fm = QFontMetricsF(self._f_time)
            ev._draw_text(p, QPointF(t.x() + self._pad,
                                     y + (self._time_h + fm.ascent() - fm.descent()) / 2.0),
                          clock, self._f_time, EXPORT.text, halo=2.4 * k)
        if delta:
            halo = 2.0 * k
            name = self._name
            runs = (((name, EXPORT.text_dim), (delta[len(name):], colour))
                    if delta.startswith(name) else ((delta, colour),))
            img = self._text(runs, self._f_delta, self._delta_h, halo)
            p.drawImage(QPointF(t.x() + self._pad - 1 - 2 * halo, y + self._time_h - 1 - 1.0 * k),
                        img)
        # --- the Δ bar (not on the best lap: the tower's ★ BEST says why)
        if bar is not None:
            p.drawImage(QPointF(self.dbar.x() - 1, self.dbar.y() - 1), self._dbar_img)
            b = self._bar
            half = b.width() / 2.0
            w = half * max(-1.0, min(1.0, bar / self._scale))
            p.setPen(Qt.NoPen)
            p.setBrush(theme.qcolor(colour, 235))
            p.drawRect(QRectF(b.center().x(), b.y() + 3 * k, w, b.height() - 6 * k).normalized())
            p.setBrush(theme.qcolor(EXPORT.text, 255))
            p.drawRect(QRectF(b.center().x() - 1.2 * k, b.y() + 1 * k, 2.4 * k, b.height() - 2 * k))
        # --- the big speed, right-aligned on its tabular figures so the unit never moves
        s = self.speed
        p.drawImage(QPointF(s.x() - 1, s.y() - 1), self._speed_img)
        halo = 2.6 * k
        img = self._text(((speed, EXPORT.text),), self._f_speed, s.height(), halo)
        p.drawImage(QPointF(self._num_right - (img.width() - 2 - 4 * halo) - 1 - 2 * halo,
                            s.y() - 1), img)


class MapLabelsElement:
    """The corners' C-labels on the (Broadcast-sized) minimap, at the apexes the app's map labels
    (`corner_map_markers`, the best lap's trace), pushed outward from the circuit's middle so a
    label never sits on the line. Pre-rendered into one image; under the kart dot (`LAYER_MAP`)."""
    name = "broadcast_map"
    layer = LAYER_MAP

    def prepare(self, ctx: ElementContext) -> bool:
        from studio import export_video as ev
        corners = getattr(ctx.session, "corners", None)
        if ctx.map_point is None or corners is None:
            return False
        try:
            marks = corners.corner_map_markers()
        except Exception:  # noqa: BLE001 — a model that cannot answer has no labels to show
            return False
        if not marks:
            return False
        k = ctx.k
        pts = [(label, ctx.map_point(x, y)) for label, x, y, _d in marks]
        cx = sum(q.x() for _l, q in pts) / len(pts)
        cy = sum(q.y() for _l, q in pts) / len(pts)
        font = ev._font(14.0 * k, bold=True)
        fm = QFontMetricsF(font)
        r = QRectF(ctx.map_rect).adjusted(-30 * k, -30 * k, 30 * k, 30 * k)
        img, p = _image(r.width(), r.height())
        self.label_points = {}
        for label, q in pts:
            dx, dy = q.x() - cx, q.y() - cy
            n = math.hypot(dx, dy) or 1.0
            c = QPointF(q.x() + dx / n * 15 * k, q.y() + dy / n * 15 * k)
            self.label_points[label] = c
            ev._draw_text(p, QPointF(c.x() - r.x() + 1 - fm.horizontalAdvance(label) / 2,
                                     c.y() - r.y() + 1 + (fm.ascent() - fm.descent()) / 2),
                          label, font, EXPORT.text_dim, halo=1.8 * k)
        p.end()
        self._img, self._at = img, QPointF(r.x() - 1, r.y() - 1)
        return True

    def paint(self, p: QPainter, vals) -> None:
        p.drawImage(self._at, self._img)


def _box(r: QRectF) -> layout.Box:
    return layout.Box(r.x(), r.y(), r.width(), r.height())


def _rect(b: layout.Box) -> QRectF:
    return QRectF(b.x, b.y, b.w, b.h)
