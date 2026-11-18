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
    fixed PER LAP (the next round step above that lap's largest |Δ|, printed at both ends — the bar
    never clips; one slow lap does not flatten the others on a whole-recording export). INSIDE TIMING PRECISION (`library.within_timing_precision`,
    0.07 s) the bar and the tower's Δ are the neutral white, not gain/loss: decided on the PRINTED
    value (`theme.format_delta_value`), as the Laps table's Δbest and the compare seam decide, so a
    "+0.07" never reads level while another "+0.07" reads lost. Not drawn on the best lap (a
    self-zero), where the tower carries the `★ BEST` mark;
  * the BIG SPEED (replaces the speed readout): `export_video._readout_runs` — the nearest 10 Hz
    sample, never interpolated (timeline.py: "a speed is a measurement"), in the app's km/h or mph;
  * the MINIMAP is the Classic inset at `layout.MAP_H_FRAC` of the short side (it was 0.22), so the
    kart dot, its tail and the ghost ring ride it unchanged; `MapLabelsElement` adds the corners'
    C-labels at the apexes the app's map labels (`corner_map_markers`), under the dot;
  * the LAST LINE (a whole-recording export only): the running clock cannot show a lap's final
    time there — the crossing falls between two frames, so the lap's last frame is up to a frame
    short and the next is already the next lap (QA, #565). For `LAST_HOLD_S` after each crossing
    a separate sub-plate under the tower reads "LAST  0:46.207  Δbest +0.215": the finished
    lap's Time and Δbest cells as the Laps table prints them (`fmt_time`,
    `lap_table.delta_best_gaps`, `fmt_signed(·, 3)`), neutral inside the printed 0.07 s, `★` on
    the best lap, no Δ under provisional timing (the column's dashes). Its Δ is ALWAYS to the best
    lap, so it always says "Δbest" — under a tower reading "Δref" an unnamed gap would read as one
    to the reference; where the label does not fit the plate (the tower's own width) the line is
    the time alone. Below the corner labels' 11 px floor (`_LABEL_MIN_PX`) there is no LAST line
    at all. Only for a lap the table has a row for (not an excluded or too-brief piece, so never
    the out-lap's "previous lap"), and never on a single-lap export, where nothing follows. It is
    drawn with or without the tower: after the recording's last lap, or a lap followed by an
    excluded crossing, the tower goes and the LAST line stands alone under the lap's header strip
    ("LAP 14"), so it never reads as debris. The running clock above it never stops: a frozen
    clock would be a false readout;
  * the HONESTY STAMP is the Classic one — the same lines from `data_quality.burned_timing_stamp`,
    painted by the same `_paint_stamp` at the same size — moved to hang under the tower instead of
    under the strip (the seam's `occupies`); it is never dropped and never shrunk;
  * THE PLATES: every text sits on one (`_plate`), the Δ bar's scale and baseline name included.
    The two Classic texts this style keeps, the stamp and the g-meter's |g| readout, get a backing
    plate painted UNDER them (`paint_under`), so their strings, faces and places stay Classic's.
    The minimap's C-labels are the exception: a plate would hide the line they name, so they carry
    a heavier outline. tests/test_export_broadcast.py holds every text (the corner cards, an Extra
    shared with Classic, aside) to WCAG contrast over sky, grass, white, grey, black and noise.

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
from collections import OrderedDict
from dataclasses import replace

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QImage, QPainter, QPainterPath, QPen

from studio import library, theme, units
from studio._signal import fmt_signed, fmt_time, lap_label
from studio.export_palette import EXPORT
from studio.overlay import LAYER_MAP, LAYER_TOP, STAMP, ElementContext, layout
from studio.overlay.corner_cards import baseline_word

STYLE_CLASSIC = "classic"
STYLE_BROADCAST = "broadcast"
PRECISION_S = library.PB_PRECISION_S
_SCALE_STEPS = (0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0)
# The plates' opacity, held by the PLATE ALONE: a text's fill against the plate blended over a
# flat background, no halo counted (tests/test_export_broadcast.py, over white, sky, grass, grey,
# black). The weakest pair is the tower's red Δ (#FF4D4D, 18 px at 720p, so 4.5:1) over white:
# 3.17:1 at 200, 4.21 at 220; 4.5 needs 226. At 230 it is 4.73.
_PLATE_ALPHA = 230
_PENDING_DELTA = ""
_CACHE_MAX = 192        # cached text images: a lap's speeds and Δ strings, recent first
_LABEL_MIN_PX = 11.0   # the C-labels' legibility floor: below it a label is noise
_LABEL_HALO_EM = 0.20  # the C-labels' outline half-width, against their face
LAST_HOLD_S = 2.0      # how long the LAST line stays after a crossing
_LAST_DIGITS = 0.56    # the LAST line's faces, against its plate's height
_LAST_TAG = 0.44       # "LAST" and "Δbest": the tower's muted label, smaller


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
    """Whether a burned Δ reads level (the neutral white): THE burned overlays' one rule, also
    called by the Classic strip and the compare pill (`export_video.burned_delta_colour`)."""
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


def last_lap_rows(session, palette) -> dict[int, tuple[str, str, str]]:
    """{lap id: (time, Δ text, Δ colour)} for every lap the Laps table has a row for: its Time
    cell, and its Δbest cell by the table's own rule (`delta_best_gaps`) — `★` on the best lap, ""
    with no best (provisional timing), neutral inside the printed 0.07 s."""
    from studio import export_video as ev
    from studio import lap_table
    rows = {int(r["idx"]): float(r["time"]) for r in session.lap_rows()}
    verified = bool(getattr(session, "timing_verified", True))
    best = session.best_lap_id() if rows and verified else None
    gaps = lap_table.delta_best_gaps(rows, best)
    star, star_colour = ev.strip_tail(None, True, palette)
    out = {}
    for lap, t in rows.items():
        if lap not in gaps:
            delta, colour = "", EXPORT.text
        elif lap == best:
            delta, colour = star.split()[0], star_colour
        else:
            g = gaps[lap]
            delta = fmt_signed(g, 3)
            colour = (EXPORT.neutral if library.within_timing_precision(g)
                      else ev.export_delta_colour(g, palette))
        out[lap] = (fmt_time(t), delta, colour)
    return out


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


_PAD_STEPS = (1.0, 0.75, 0.5, 0.25, 0.0)


def backing_plate(inner: QRectF, pad_l: float, pad_r: float, pad_v: float, others,
                  area: QRectF, gap: float) -> QRectF:
    """`inner` grown by its padding (left, right, and top and bottom), keeping `gap` from every
    rect in `others` and inside `area`: where that does not fit, the vertical padding goes first,
    in quarters, then the horizontal — so a plate aligned with its neighbour's edge stays aligned
    while there is any room. With no room at all, `inner` itself cut to `area` (a stamp wider than
    the frame is cut there, `layout.resolve`)."""
    for fh in _PAD_STEPS:
        for fv in _PAD_STEPS:
            r = inner.adjusted(-pad_l * fh, -pad_v * fv, pad_r * fh, pad_v * fv)
            if area.contains(r) and not any(r.adjusted(-gap, -gap, gap, gap).intersects(o)
                                            for o in others):
                return r
    return inner.intersected(area)


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
        # to them, and each lap's bar scale to that lap's largest |Δ| (PER LAP: on a whole-recording
        # export one slow lap would otherwise flatten every other lap's bar to a sliver).
        heads, clocks, deltas, speeds = [], [], [], []
        max_abs: dict[int | None, float] = {}
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
                max_abs[vals.lap_id] = max(max_abs.get(vals.lap_id, 0.0),
                                           abs(printed_delta(vals.delta_s)))
        self._scales = {lap: delta_scale(v) for lap, v in max_abs.items()}
        # The LAST line's holds (whole-recording only): (crossing, lap) on the telemetry clock, for
        # each table lap whose crossing falls inside the render.
        self._last_rows: dict[int, tuple[str, str, str]] = {}
        self._holds: list[tuple[float, int]] = []
        if spec.follow_laps:
            rows = last_lap_rows(s, self._palette)
            tt0 = ev.overlay_values_at(s, float(spec.t0), spec).tt
            tt1 = ev.overlay_values_at(s, float(spec.t1), spec).tt
            for lap in sorted(rows):
                win = s.lap_window(lap)
                if win is not None and tt0 is not None and tt1 is not None \
                        and tt0 <= win[1] < tt1:
                    self._holds.append((float(win[1]), lap))
                    self._last_rows[lap] = rows[lap]
        # --- sizes (fractions of the short side, layout.py) and faces
        L = layout
        self._head_h, self._prog_h = L.TOWER_HEAD * u, L.TOWER_PROGRESS * u
        self._time_h, self._delta_h = L.TOWER_TIME * u, L.TOWER_DELTA * u
        self._pad = L.TOWER_PAD * u
        self._f_head = ev._font(self._head_h * 0.62, bold=True)
        self._f_time = ev._font(self._time_h * 0.80, bold=True)
        self._f_delta = ev._font(self._delta_h * 0.64, bold=True)
        self._last_h, self._last_gap = L.TOWER_LAST * u, L.TOWER_LAST_GAP * u
        # The line's smallest face is its tag; below the labels' floor the line is noise: none.
        self.last_px = self._last_h * _LAST_TAG
        if self.last_px < _LABEL_MIN_PX:
            self._holds, self._last_rows = [], {}
        self._f_last = ev._font(self._last_h * _LAST_DIGITS, bold=True)
        self._f_last_tag = ev._font(self.last_px, bold=True)
        self._last_sp = 0.012 * u
        tower_w = 2 * self._pad + max(
            [_adv(self._f_head, t) for t in heads] + [_adv(self._f_time, t) for t in clocks]
            + [_adv(self._f_delta, t) for t in deltas] + [0.18 * u])
        # The LAST plate is the tower's width, which may grow for it only as far as the tower's
        # neighbours leave room (the g-meter; in landscape the top-centre Δ bar): a row whose
        # "Δbest +0.215" does not fit is the time alone (an unnamed Δ would read as the tower's
        # baseline); one whose time alone does not fit is dropped.
        m, gap = L.MARGIN * u, L.GAP * u
        room = ctx.plates["gmeter"].x() - gap - m
        if ctx.out_w >= ctx.out_h:
            room = min(room, (ctx.out_w - L.DBAR_W * u) / 2.0 - gap - m)
        room = max(room, tower_w)
        fitted_rows = {}
        for lap, row in self._last_rows.items():
            for cand in (row, (row[0], "", EXPORT.text)):
                if 2 * self._pad + self._last_width(cand) <= room:
                    fitted_rows[lap] = cand
                    break
        self._holds = [h for h in self._holds if h[1] in fitted_rows]
        self._last_rows = fitted_rows
        tower_w = max([tower_w] + [2 * self._pad + self._last_width(r)
                                   for r in fitted_rows.values()])
        self._main_h = L.BROADCAST["tower"].h * u
        tower_h = self._main_h + ((self._last_gap + self._last_h) if self._holds else 0.0)
        sp_h = L.SPEED_H * u
        self._f_speed = ev._font(sp_h * 0.80, bold=True)
        self._f_unit = ev._font(sp_h * 0.26, bold=True)
        self._unit_label = units.speed_label(self._unit)
        self._sp_gap = 0.006 * u
        self._sp_num_w = max((_adv(self._f_speed, t) for t in speeds), default=0.0)
        speed_w = 2 * L.SPEED_PAD * u + self._sp_num_w + self._sp_gap + _adv(self._f_unit,
                                                                            self._unit_label)
        # --- the layout (layout.py), clear of the Classic plates this style keeps; every slot has
        # fallbacks, and only if none fits does the export fail here, by name (`build`). The
        # honesty stamp is the Classic block (`stamp_block`): its size, and its offset from the
        # strip it hangs under, carried over to the tower — same lines, face and size.
        strip, stamp = ctx.plates["strip"], ctx.plates[STAMP]
        boxes = L.resolve(
            L.table(ctx.out_w, ctx.out_h), L.ORDER, ctx.out_w, ctx.out_h,
            {"tower": (tower_w, tower_h), "stamp": (stamp.width(), stamp.height()),
             "speed": (speed_w, sp_h)},
            {n: _box(ctx.plates[n]) for n in ("gmeter", "map")},
            hang={"stamp": (stamp.x() - strip.x(), stamp.y() - strip.bottom())})
        moved = _rect(boxes["stamp"])
        self.boxes = boxes
        self.tower, self.dbar, self.speed = (_rect(boxes[n]) for n in ("tower", "dbar", "speed"))
        # What the elements prepared after this one read as the plates (the corner cards: they keep
        # clear of the tower and the bar, and stand on the "readout" at its Classic size, which
        # sizes them) — that slot moved up to the big speed's top, so a card lands above it.
        readout = QRectF(ctx.plates["readout"])
        readout.moveTop(self.speed.y())
        self.occupies = {"strip": self.tower.united(self.dbar), "readout": readout, STAMP: moved}
        self.under = self._under_plates(ctx, moved)
        self._prerender()
        self._last_imgs = {lap: self._last_image(r) for lap, r in self._last_rows.items()}
        # Bounded (least recently used out): a whole-recording export at 4K once reached 81 MB.
        self._cache: OrderedDict[tuple[tuple[str, str], ...], QImage] = OrderedDict()
        self._glyphs: dict[str, tuple] = {}
        fm = QFontMetricsF(self._f_time)
        self._glyph_h = fm.height() + 2 * math.ceil(3 * 2.4 * self._k) + 2
        return True

    def _under_plates(self, ctx: ElementContext, stamp: QRectF) -> dict[str, QRectF]:
        """The backing plates of the two Classic texts this style keeps, painted UNDER them
        (`paint_under`): the honesty stamp (a sub-plate under the tower, its left edge the
        tower's, the same inset at its right) and the g-meter's |g| readout (fitted to its widest
        string, in the dial's own readout band). Each keeps the LAST line's gap from every other
        element, shrinking its padding first; neither moves a glyph."""
        u = float(min(ctx.out_w, ctx.out_h))
        pad, gap = layout.TOWER_PAD * u, layout.TOWER_LAST_GAP * u
        g, mp = ctx.plates["gmeter"], ctx.plates["map"]
        out = {}
        if stamp.height() > 0:
            area = ctx.safe if ctx.safe.contains(stamp) else QRectF(0, 0, ctx.out_w, ctx.out_h)
            inset = stamp.x() - self.tower.x()      # the stamp's hang: the strip's ink inset
            inset = inset if inset > 0 else pad
            out["stamp"] = backing_plate(stamp, inset, inset, pad / 2.0,
                                         (self.tower, self.dbar, self.speed, g, mp), area, gap)
        if getattr(ctx.session, "has_gmeter", False) and g.height() > 0:
            from studio import gmeter_overlay as gm
            _cx, _cy, r = gm.dial_geom(g.width(), g.height())
            band = gm.readout_rect(g.width(), g.height()).translated(g.topLeft())
            tw = max(_adv(gm._font(gm._readout_px(r), bold=True), t)
                     for t in ("0.0 g", gm._NO_VALUE))
            text = QRectF(band.center().x() - tw / 2.0, band.y(), tw, band.height())
            out["gmeter"] = backing_plate(text, pad, pad, 0.0, (self.tower, self.dbar, self.speed,
                                                                stamp, mp), g, 0.0)
        # The Δ bar's plate: its box, 2 x k wider each side where the layout leaves room (never
        # taller: above it is the margin), so its end labels sit clear of the rounded corners.
        side = 2.0 * self._k
        self.dbar_plate = backing_plate(self.dbar, side, side, 0.0,
                                        (self.tower, self.speed, stamp, g, mp), ctx.safe, gap)
        return out

    def paint_under(self, p: QPainter, vals) -> None:
        """The backing plates of the Classic texts kept (`_under_plates`), before any is drawn."""
        for at, img in self._under_imgs:
            p.drawImage(at, img)

    def _prerender(self) -> None:
        ev, k = self._ev, self._k
        self._under_imgs = []
        for r in self.under.values():
            img, p = _image(r.width(), r.height())
            _plate(p, QRectF(1, 1, r.width(), r.height()), k)
            p.end()
            self._under_imgs.append((QPointF(r.x() - 1, r.y() - 1), img))
        # The tower's plate, its amber header band and the progress line's track (the main plate:
        # a LAST line, when the export has one, is its own sub-plate under it).
        r = self.tower
        img, p = _image(r.width(), self._main_h)
        box = QRectF(1, 1, r.width(), self._main_h)
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
        # The header strip alone (a LAST line without its tower): the amber band, rounded.
        img, p = _image(r.width(), self._head_h)
        band = QRectF(1, 1, r.width(), self._head_h)
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(EXPORT.accent, 240))
        p.drawRoundedRect(band, 7 * k, 7 * k)
        p.end()
        self._header_img = img
        # The Δ bar: one image per lap scale in use (`_bar_image`).
        r = self.dbar
        lab_h = r.height() * 0.42
        self._lab_h = lab_h
        self._bar = QRectF(r.x(), r.y() + lab_h, r.width(), r.height() - lab_h)
        self._bars: dict[float, QImage] = {}
        self.bar_labels: dict[float, tuple[str, str, str]] = {}
        for scale in sorted(set(self._scales.values())):
            self._bar_image(scale)
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

    def _bar_image(self, scale: float) -> QImage:
        """The bar's track, the precision band round the centre, `scale` at both ends and the
        baseline's name over the centre — drawn once per scale (scales are a few round steps)."""
        img = self._bars.get(scale)
        if img is not None:
            return img
        ev, k, r, lab_h = self._ev, self._k, self.dbar, self._lab_h
        pl = self.dbar_plate
        img, p = _image(pl.width(), pl.height())
        # ONE plate under the scale, the baseline name and the bar (haloed alone over sky the
        # labels measured 1.4:1 at 720p, tests/test_export_broadcast.py), as dark as the tower's;
        # the track inside it is the plate's own hairline, not a second plate.
        _plate(p, QRectF(1, 1, pl.width(), pl.height()), k)
        dx = r.x() - pl.x()
        bar = QRectF(1 + dx, 1 + lab_h, r.width(), r.height() - lab_h)
        p.setBrush(Qt.NoBrush)
        p.setPen(QPen(theme.qcolor(EXPORT.text, 55), 1.0 * k))
        p.drawRoundedRect(bar.adjusted(2 * k, 1 * k, -2 * k, -2 * k), 4 * k, 4 * k)
        band = bar.width() / 2.0 * PRECISION_S / scale
        p.setPen(Qt.NoPen)
        p.setBrush(theme.qcolor(EXPORT.text, 46))
        p.drawRect(QRectF(bar.center().x() - band, bar.y() + 2 * k, 2 * band,
                          bar.height() - 4 * k))
        f = ev._font(lab_h * 0.86, bold=True)
        fm = QFontMetricsF(f)
        base = 1 + lab_h - fm.descent() - 1.0 * k
        sc = f"{scale:g}"
        left, right = f"−{sc} s", f"+{sc} s"
        ev._draw_text(p, QPointF(bar.x() + 4 * k, base), left, f, EXPORT.text_dim, halo=1.6 * k)
        ev._draw_text(p, QPointF(bar.right() - 4 * k - fm.horizontalAdvance(right), base), right,
                      f, EXPORT.text_dim, halo=1.6 * k)
        ev._draw_text(p, QPointF(bar.center().x() - fm.horizontalAdvance(self._name) / 2, base),
                      self._name, f, EXPORT.text, halo=1.6 * k)
        p.end()
        self._bars[scale] = img
        self.bar_labels[scale] = (left, self._name, right)
        return img

    def _last_runs(self, row) -> list[tuple[str, object, str]]:
        """The LAST line's runs: (text, font, colour)."""
        time_text, delta, colour = row
        runs = [("LAST", self._f_last_tag, EXPORT.text_dim), (time_text, self._f_last, EXPORT.text)]
        if delta and delta != "★":
            runs.append(("Δbest", self._f_last_tag, EXPORT.text_dim))
        if delta:
            runs.append((delta, self._f_last, colour))
        return runs

    def _last_width(self, row) -> float:
        runs = self._last_runs(row)
        return sum(_adv(f, t) for t, f, _c in runs) + self._last_sp * (len(runs) - 1)

    def _last_image(self, row) -> QImage:
        """One lap's LAST sub-plate, drawn once: the plate the tower's width, its runs on one
        baseline."""
        k, w, h = self._k, self.tower.width(), self._last_h
        img, p = _image(w, h)
        _plate(p, QRectF(1, 1, w, h), k)
        fm = QFontMetricsF(self._f_last)
        base = 1 + (h + fm.ascent() - fm.descent()) / 2.0
        x = 1 + self._pad
        for text, font, colour in self._last_runs(row):
            self._ev._draw_text(p, QPointF(x, base), text, font, colour, halo=1.8 * k)
            x += _adv(font, text) + self._last_sp
        p.end()
        return img

    def last_lap(self, vals) -> tuple[int, tuple[str, str, str]] | None:
        """(lap, (time, Δ, colour)) of the LAST line on this frame, or None: within
        `LAST_HOLD_S` after a table lap's crossing, once the frame is past it."""
        tt = vals.tt
        if tt is None or not self._holds:
            return None
        for t_cross, lap in reversed(self._holds):
            if t_cross <= tt:
                if tt - t_cross < LAST_HOLD_S and vals.lap_id != lap:
                    return lap, self._last_rows[lap]
                return None
        return None

    def scale_of(self, lap_id) -> float:
        """The bar's half-range on `lap_id`'s frames."""
        return self._scales.get(lap_id, _SCALE_STEPS[0])

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

    def _glyph(self, ch: str):
        """(left pad, advance, halo image, fill image) of one clock character, drawn once."""
        g = self._glyphs.get(ch)
        if g is None:
            fm = QFontMetricsF(self._f_time)
            halo = 2.4 * self._k
            pad = math.ceil(3 * halo)
            w = fm.horizontalAdvance(ch) + 2 * pad
            base = QPointF(1 + pad, 1 + pad + fm.ascent())
            under, p = _image(w, self._glyph_h - 2)
            self._ev._draw_text(p, base, ch, self._f_time, EXPORT.halo, halo=halo)
            p.end()
            over, p = _image(w, self._glyph_h - 2)
            self._ev._draw_text(p, base, ch, self._f_time, EXPORT.text, halo=0.0, shadow=None)
            p.end()
            g = self._glyphs[ch] = (pad, fm.horizontalAdvance(ch), under, over)
        return g

    def _text(self, runs: tuple[tuple[str, str], ...], font, h: float, halo: float) -> QImage:
        """`runs` ((text, colour), …) drawn once, haloed, left to right in an image `h` tall;
        cached by the runs."""
        img = self._cache.get(runs)
        if img is not None:
            self._cache.move_to_end(runs)
        else:
            fm = QFontMetricsF(font)
            img, p = _image(sum(fm.horizontalAdvance(t) for t, _c in runs) + 4 * halo, h)
            x = 1 + 2 * halo
            for text, colour in runs:
                self._ev._draw_text(p, QPointF(x, 1 + (h + fm.ascent() - fm.descent()) / 2),
                                    text, font, colour, halo=halo)
                x += fm.horizontalAdvance(text)
            p.end()
            self._cache[runs] = img
            if len(self._cache) > _CACHE_MAX:
                self._cache.popitem(last=False)
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
        k = self._k
        head, clock, delta, colour, frac, bar, speed = self.state(vals)
        if head is not None:
            self._paint_tower(p, head, clock, delta, colour, frac)
        last = self.last_lap(vals)
        if last is not None:
            y = self.tower.y() + self._main_h + self._last_gap
            if head is None:
                # No tower (the recording's last lap, or an excluded crossing, follows): the
                # finished lap's header strip over the line, so it is not a stray plate.
                p.drawImage(QPointF(self.tower.x() - 1, y - self._head_h - self._last_gap - 1),
                            self._header_img)
                p.drawImage(QPointF(self.tower.x() - 1, y - self._head_h - self._last_gap - 1),
                            self._head(f"LAP {lap_label(last[0])}"))
            p.drawImage(QPointF(self.tower.x() - 1, y - 1), self._last_imgs[last[0]])
        # --- the Δ bar (not on the best lap: the tower's ★ BEST says why), on this lap's scale
        if bar is not None:
            scale = self.scale_of(vals.lap_id)
            p.drawImage(QPointF(self.dbar_plate.x() - 1, self.dbar_plate.y() - 1),
                        self._bar_image(scale))
            b = self._bar
            half = b.width() / 2.0
            w = half * max(-1.0, min(1.0, bar / scale))
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

    def _paint_tower(self, p: QPainter, head, clock, delta, colour, frac) -> None:
        """The tower, only while there is a lap to name: outside one (pit time on a whole-recording
        export) it draws nothing at all."""
        k = self._k
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
            # From pre-rendered glyphs: every halo first, then every fill, so a glyph's halo never
            # cuts into its neighbour's fill. Tabular figures: each digit has one advance.
            x = round(t.x() + self._pad)
            gy = round(y + (self._time_h - self._glyph_h) / 2.0) - 1.0
            at = []
            for ch in clock:
                g = self._glyph(ch)
                at.append((QPointF(x - 1.0 - g[0], gy), g))
                x += g[1]
            for pt, g in at:
                p.drawImage(pt, g[2])
            for pt, g in at:
                p.drawImage(pt, g[3])
        if delta:
            halo = 2.0 * k
            name = self._name
            runs = (((name, EXPORT.text_dim), (delta[len(name):], colour))
                    if delta.startswith(name) else ((delta, colour),))
            img = self._text(runs, self._f_delta, self._delta_h, halo)
            p.drawImage(QPointF(t.x() + self._pad - 1 - 2 * halo, y + self._time_h - 1 - 1.0 * k),
                        img)


class MapLabelsElement:
    """The corners' C-labels on the (Broadcast-sized) minimap, at the apexes the app's map labels
    (`corner_map_markers`, the best lap's trace), pushed outward from the circuit's middle — which
    keeps most labels off the line, not all (on a concave stretch the outside can be track too).
    Never below `_LABEL_MIN_PX` (11 px): smaller, a label is noise, so a frame that small gets
    labels at the floor. One small image per label; under the kart dot (`LAYER_MAP`)."""
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
        px = max(14.0 * k, _LABEL_MIN_PX)
        font = ev._font(px, bold=True)
        fm = QFontMetricsF(font)
        # One small image per label (a blit costs its area: one image over the whole map measured
        # ~1 ms a 4K frame), whole-pixel placed so the blit is a straight copy. An OUTLINE, not a
        # plate: a plate here would hide the racing line it names. Its width is a share of the
        # face (`_LABEL_HALO_EM`): the old 1.8 x k was 1.2 px at 720p, 2.0:1 over sky.
        halo = max(1.8 * k, _LABEL_HALO_EM * px)
        self.label_points, self._labels = {}, []
        for label, q in pts:
            dx, dy = q.x() - cx, q.y() - cy
            n = math.hypot(dx, dy) or 1.0
            c = QPointF(q.x() + dx / n * 15 * k, q.y() + dy / n * 15 * k)
            self.label_points[label] = c
            w = fm.horizontalAdvance(label) + 4 * halo
            h = fm.height() + 4 * halo
            img, p = _image(w, h)
            ev._draw_text(p, QPointF(1 + 2 * halo, 1 + 2 * halo + fm.ascent()), label, font,
                          EXPORT.text_dim, halo=halo)
            p.end()
            self._labels.append((QPointF(round(c.x() - w / 2) - 1.0,
                                         round(c.y() - h / 2) - 1.0), img))
        return True

    def paint(self, p: QPainter, vals) -> None:
        for at, img in self._labels:
            p.drawImage(at, img)


def _box(r: QRectF) -> layout.Box:
    return layout.Box(r.x(), r.y(), r.width(), r.height())


def _rect(b: layout.Box) -> QRectF:
    return QRectF(b.x, b.y, b.w, b.h)
