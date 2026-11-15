"""The Lap Race card: the overlay on the map's top-left that says what the dots are doing.

Painted, like the map key it shares the canvas with (map_view._MapLegend): one widget, no child
controls, no stylesheet — theme tokens and faces only, digits in the tabular face so a column
holds still while its numbers change every tick. It renders a `race_controller.CardModel` and
emits intents; it owns no race state and imports neither `pacer` nor the session.

WHAT EACH LINE IS (the honesty copy):
  * the caption says what the dots are, in one line: each clean lap on its own clock;
  * a ROW is a pack (`lap_race.Pack`): laps the timing cannot split. Only its leading lap has a
    position; the row names its coloured laps (and the selected one) and counts the grey ones,
    then the pack's "interval to leader" and its spread. Nothing calls a lap inside a pack a race
    position;
  * the fixed-reference line is the selected lap against the BEST lap ("L4 vs best L13"), which
    never changes reference when the leader does, and reads "≈" inside the tie threshold;
  * on a provisional start line the rows give way to the reason the race is not ranked; on an
    estimated clock the clock is named with the widened threshold; laps not raced are counted.
"""
from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPen, QPixmap, QRegion
from PySide6.QtWidgets import QWidget

from . import motion, theme
from ._signal import fmt_signed, fmt_time, lap_label
from .lap_table import printed_ms
from .race_controller import CAPTION, CardModel, PackRow, readout_text
from .theme import CHART_SERIES, C, icon

_PAD = theme.SPACE_S
_ROW_H = 18
_TITLE_H = 20
_BAR_H = 12
_BTN = 20
_POS_W = 22
_INT_W = 64
_SPREAD_W = 44
_CHIP_DOT = 7
_RING = QColor(C.text)
#: While the race runs the card shows a snapshot at most this old (s of wall time): its numbers are
#: true AT the time its title prints, and change at a rate they can be read at. Measured on the
#: 36-lap session: re-rendering every 30 Hz tick, the rows changed on 99 of 120 ticks at 2x.
CARD_REFRESH_S = 0.1


def interval_text(row: PackRow, over: bool, lap_times: dict[int, float]) -> str:
    """A pack's interval cell. While running: 'leader', or the pack's interval to the leader (2 dp,
    the core's place-matched Δ). At the finish the column is "time / interval": the winner's lap
    time, and every other pack's gap built from the milliseconds the lap times PRINT
    (`lap_table.printed_ms`, 3 dp), so it is the Laps table's Δbest string to the digit whenever the
    winner is the session best — two raw doubles can differ from it on a rounding edge."""
    if row.position == 1:
        lead = lap_times.get(row.laps[0])
        return fmt_time(lead) if over and lead is not None else "leader"
    if over and row.laps[0] in lap_times and lap_times:
        winner = min(lap_times.values())
        return f"{fmt_signed((printed_ms(lap_times[row.laps[0]]) - printed_ms(winner)) / 1000, 3)} s"
    return f"+{row.interval_s:.2f} s"


def interval_header(over: bool) -> str:
    return "time / interval" if over else "interval to leader"


def spread_text(row: PackRow) -> str:
    """The pack's spread (last member's interval minus the leader's); blank for a lone lap."""
    return "" if len(row.laps) == 1 else f"{row.spread_s:.2f} s"


class RaceCard(QWidget):
    """The painted race card. Signals carry the user's intent to the controller."""

    playToggled = Signal()
    restartRequested = Signal()
    speedCycled = Signal()
    closeRequested = Signal()
    scrubbed = Signal(float)        # fraction of the race, 0..1

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model: CardModel | None = None
        self.colours: dict[int, int] = {}      # lap id → series slot (the coloured five)
        self.lap_times: dict[int, float] = {}   # the field's lap times (the finish column)
        self._cache: QPixmap | None = None     # the card as painted, re-rendered on a change
        self._cache_key = None
        self._title_font = theme.ui_font(theme.PANEL_HEADER, theme.W_SEMIBOLD)
        self._font = theme.ui_font(theme.CAPTION)
        self._num_font = theme.mono_font(theme.CAPTION)
        self._fm = QFontMetrics(self._font)
        self._num_fm = QFontMetrics(self._num_font)
        self._icons: dict = {}
        self._hits: dict[str, QRectF] = {}
        self._scrubbing = False
        self.renders = 0                        # pixmap renders (a test reads it)
        # The pack-row slide (_slide_rows): row index by leading lap, offsets (px) at its start.
        self._row_index: dict[int, int] = {}
        self._slide: dict[int, float] = {}
        self._slide_p = 1.0
        self._slide_tr: motion.Transition | None = None
        # The card's measure is its caption's, one line, plus its padding and a step of air: the
        # widest fixed text it carries, so the columns below are sized from the face, not typed.
        self._w = self._fm.horizontalAdvance(CAPTION) + 2 * _PAD + theme.SPACE_XL
        self.setMouseTracking(True)
        self.hide()

    # ------------------------------------------------------------------ model
    def configure(self, colours: dict[int, int], lap_times: dict[int, float]) -> None:
        """Per-race constants: which laps are coloured (and in which slot), every lap's time."""
        self.colours = dict(colours)
        self.lap_times = dict(lap_times)
        self._cache = None
        self._row_index = {}                    # a new race's first rows appear, never slide in

    def show_model(self, model: CardModel) -> None:
        """Take a tick's model. The card is a translucent child over the map's viewport, so it is
        repainted whenever the map is — every tick — and painting its ~20 text runs cost ~1-2 ms of
        that. So everything but the race clock in the title and the progress bar's fill (both
        change every tick, both one draw call) is a pixmap re-rendered only when what it shows
        changed — the rows, the readout, the play state — and blitted otherwise.

        WHILE IT RUNS the card takes a new snapshot at most every CARD_REFRESH_S of wall time
        (race time × speed): between snapshots it keeps showing the last one whole — title clock,
        bar and rows of ONE instant, so no number on it is ever from a different time than its
        title says. A pause, a scrub, a speed or selection change, or the finish refreshes at once."""
        snap = self.model
        if (snap is not None and model.playing and snap.playing
                and not model.over and model.speed == snap.speed
                and model.selected == snap.selected
                and 0.0 <= model.t - snap.t < CARD_REFRESH_S * model.speed):
            return
        self.model = model
        self._slide_rows(model)
        h = self._height(model)
        if (self._w, h) != (self.width(), self.height()):
            self.setFixedSize(self._w, h)
        key = self._content_key(model)
        if key != self._cache_key:
            self._cache_key = key
            self._cache = None
        self.update()

    # ------------------------------------------------------------------ row slide
    @staticmethod
    def _rows_top() -> float:
        """Where the first pack row starts (below the title, caption, bar and column header)."""
        return float(_PAD + _TITLE_H + _ROW_H + _BAR_H + 4 + _ROW_H)

    def _slide_rows(self, model: CardModel) -> None:
        """When packs re-form, each row that is still on the card (named by its leading lap)
        slides from where it is SHOWN now to its new place over motion.ROW_SLIDE_MS, instead of
        teleporting. A new snapshot mid-slide starts the next slide from the rows' shown places.
        The rows are strips of the one cached card (paintEvent), so a slide costs a few blits a
        frame and no re-render; with motion off the end — no offsets — applies at once."""
        rows = () if model.unranked_reason else model.rows
        new = {r.laps[0]: k for k, r in enumerate(rows)}
        old, self._row_index = self._row_index, new
        live = self._slide_tr is not None and self._slide_tr.running
        p = self._slide_tr.progress() if live else 1.0
        offsets = {}
        for lap, k in new.items():
            if lap in old:
                shown = (old[lap] - k) * _ROW_H + self._slide.get(lap, 0.0) * (1.0 - p)
                if abs(shown) > 0.5:
                    offsets[lap] = shown
        if self._slide_tr is not None:
            self._slide_tr.cancel()
            self._slide_tr = None
        self._slide, self._slide_p = {}, 1.0
        if not offsets:
            return
        self._slide, self._slide_p = offsets, 0.0

        def step(q: float):
            self._slide_p = q
            # While the race runs, the map repaints the card every tick anyway: no extra paints.
            if self.model is None or not self.model.playing:
                self.update()

        def end():
            self._slide, self._slide_p, self._slide_tr = {}, 1.0, None
            self.update()

        tr = motion.start(self, motion.ROW_SLIDE_MS, step, end)
        self._slide_tr = None if tr.done else tr

    def sliding(self) -> bool:
        """True while pack rows are mid-slide (tests read it)."""
        return bool(self._slide)

    def hideEvent(self, event):  # noqa: N802 (Qt override)
        if self._slide_tr is not None:
            self._slide_tr.finish()
        super().hideEvent(event)

    def _blit(self, p: QPainter) -> None:
        """The cached card — whole, or, mid-slide, with each pack row's strip at its sliding place
        (the rows band under them filled with the plate, so a row leaving a slot leaves no hole)."""
        cache = self._cache
        if not self._slide or self.model is None:
            p.drawPixmap(0, 0, cache)
            return
        dpr = cache.devicePixelRatio()
        w = float(self.width())
        top = self._rows_top()
        band = QRectF(0, top, w, _ROW_H * len(self._row_index))
        outside = QRegion(self.rect()).subtracted(QRegion(band.toAlignedRect()))
        p.save()
        p.setClipRegion(outside)
        p.drawPixmap(0, 0, cache)
        p.setClipRect(band)
        plate = QColor(C.surface)
        plate.setAlpha(232)
        p.fillRect(band.adjusted(1, 0, -1, 0), plate)
        keep = 1.0 - self._slide_p
        for lap, k in sorted(self._row_index.items(), key=lambda kv: kv[1]):
            y = top + k * _ROW_H
            src = QRectF(0, y * dpr, w * dpr, _ROW_H * dpr)
            p.drawPixmap(QRectF(0, y + self._slide.get(lap, 0.0) * keep, w, _ROW_H), cache, src)
        p.restore()

    def _content_key(self, m: CardModel):
        return (tuple(self.lines()[1:]), m.over, m.playing, m.speed, m.selected, self.width(),
                self.height(), round(self.devicePixelRatioF(), 3), theme.active_palette())

    def lines(self) -> list[str]:
        """Every text line the card paints right now, top to bottom (tests and captures read it)."""
        m = self.model
        if m is None:
            return []
        out = [self._title(m), CAPTION]
        if m.unranked_reason:
            out.append(m.unranked_reason)
        else:
            out.append(f"pack · {interval_header(m.over)} · spread")
            room = self._w - 2 * _PAD - _POS_W - _INT_W - _SPREAD_W
            for r in m.rows:
                chips, more = self.fit_chips(r, m.selected, room)
                out.append(" ".join([str(r.position), *(f"L{lap_label(i)}" for i in chips),
                                     *([f"+{more}"] if more else []),
                                     interval_text(r, m.over, self.lap_times),
                                     spread_text(r)]).strip())
            if m.hidden_packs:
                out.append(self._more_text(m))
        ro = readout_text(m)
        if ro:
            out.append(ro)
        if m.clock_estimated:
            out.append(self._clock_text(m))
        if m.excluded:
            out.append(m.excluded)
        return out

    @staticmethod
    def _title(m: CardModel) -> str:
        return "LAP RACE · FINAL" if m.over else f"LAP RACE · {m.t:4.1f} s"

    @staticmethod
    def _more_text(m: CardModel) -> str:
        return f"{m.hidden_packs} more pack" + ("" if m.hidden_packs == 1 else "s")

    @staticmethod
    def _clock_text(m: CardModel) -> str:
        return f"Timing from the video clock: ties within {m.tie_s:.2f} s"

    def _wrapped_h(self, text: str, width: float) -> int:
        r = self._fm.boundingRect(QRectF(0, 0, width, 1000).toRect(),
                                  int(Qt.TextWordWrap | Qt.AlignLeft), text)
        return r.height() + 2

    def _height(self, m: CardModel) -> int:
        inner = self._w - 2 * _PAD
        h = _PAD + _TITLE_H + _ROW_H + _BAR_H + 4
        if m.unranked_reason:
            h += self._wrapped_h(m.unranked_reason, inner) + 4
        else:
            h += _ROW_H * (1 + len(m.rows) + (1 if m.hidden_packs else 0))
        if readout_text(m):
            h += _ROW_H + 2
        if m.clock_estimated:
            h += self._wrapped_h(self._clock_text(m), inner)
        if m.excluded:
            h += self._wrapped_h(m.excluded, inner)
        return h + _PAD

    # ------------------------------------------------------------------ paint
    def _pixmap(self, name: str, colour: str):
        key = (name, colour, round(self.devicePixelRatioF(), 3))
        px = self._icons.get(key)
        if px is None:
            px = icon(name, color=colour).pixmap(QSize(theme.ICON_PX, theme.ICON_PX),
                                                 self.devicePixelRatioF())
            self._icons[key] = px
        return px

    def _button(self, p: QPainter, key: str, rect: QRectF, glyph: str | None = None,
                text: str | None = None) -> None:
        self._hits[key] = rect
        p.setPen(QPen(QColor(C.border), 1))
        p.setBrush(QBrush(QColor(C.surface_hover)))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), theme.RADIUS_S, theme.RADIUS_S)
        if glyph:
            px = self._pixmap(glyph, C.text)
            w, h = px.width() / px.devicePixelRatio(), px.height() / px.devicePixelRatio()
            p.drawPixmap(QPointF(rect.center().x() - w / 2, rect.center().y() - h / 2), px)
        if text:
            p.setFont(self._num_font)
            p.setPen(QPen(QColor(C.text)))
            p.drawText(rect, int(Qt.AlignCenter), text)

    def paintEvent(self, _event):
        if self.model is None:
            return
        if self._cache is None or self._cache.size() != self.size() * self.devicePixelRatioF():
            dpr = self.devicePixelRatioF()
            px = QPixmap(self.size() * dpr)
            px.setDevicePixelRatio(dpr)
            px.fill(Qt.transparent)
            painter = QPainter(px)
            self._paint(painter, self.model)
            painter.end()
            self._cache = px
            self.renders += 1
        p = QPainter(self)
        self._blit(p)
        self._paint_live(p, self.model)
        p.end()

    def _paint_live(self, p: QPainter, m: CardModel) -> None:
        """The two things that change every tick, over the cached card: the title (race clock)
        and the progress bar's fill."""
        p.setRenderHint(QPainter.Antialiasing, True)
        inner = self._w - 2 * _PAD
        x0, y = float(_PAD), float(_PAD)
        p.setFont(self._title_font)
        p.setPen(QPen(QColor(C.text_dim)))
        p.drawText(QRectF(x0, y, inner, _TITLE_H), int(Qt.AlignVCenter | Qt.AlignLeft),
                   self._title(m))
        bar = QRectF(x0, y + _TITLE_H + _ROW_H + 3, inner, _BAR_H - 6)
        frac = 0.0 if m.duration <= 0 else min(max(m.t / m.duration, 0.0), 1.0)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C.accent)))
        p.drawRoundedRect(QRectF(bar.x(), bar.y(), max(bar.width() * frac, 6.0), bar.height()),
                          3, 3)

    def _paint(self, p: QPainter, m: CardModel) -> None:
        p.setRenderHint(QPainter.Antialiasing, True)
        plate = QColor(C.surface)
        plate.setAlpha(232)
        p.setBrush(QBrush(plate))
        p.setPen(QPen(QColor(C.border), 1))
        p.drawRoundedRect(QRectF(0.5, 0.5, self.width() - 1, self.height() - 1),
                          theme.RADIUS_M, theme.RADIUS_M)
        inner = self._w - 2 * _PAD
        x0, y = float(_PAD), float(_PAD)
        # Title row: identity + race clock (painted live, _paint_live), then the transport
        # (restart · play/pause · speed · ×).
        bx = self._w - _PAD - _BTN
        self._button(p, "close", QRectF(bx, y, _BTN, _BTN), glyph="ph.x")
        bx -= _BTN + 4 + 10
        self._button(p, "speed", QRectF(bx, y, _BTN + 10, _BTN), text=f"{m.speed:g}×")
        bx -= _BTN + 4
        self._button(p, "play", QRectF(bx, y, _BTN, _BTN),
                     glyph="ph.pause-fill" if m.playing else "ph.play-fill")
        bx -= _BTN + 4
        self._button(p, "restart", QRectF(bx, y, _BTN, _BTN), glyph="ph.arrow-counter-clockwise")
        y += _TITLE_H
        p.setFont(self._font)
        p.setPen(QPen(QColor(C.text_dim)))
        p.drawText(QRectF(x0, y, inner, _ROW_H), int(Qt.AlignVCenter | Qt.AlignLeft), CAPTION)
        y += _ROW_H
        # Progress: race time over the slowest lap's; press or drag to scrub.
        bar = QRectF(x0, y + 3, inner, _BAR_H - 6)
        self._hits["bar"] = QRectF(x0, y, inner, _BAR_H)
        p.setPen(Qt.NoPen)
        p.setBrush(QBrush(QColor(C.border)))
        p.drawRoundedRect(bar, 3, 3)        # the track; its fill is live (_paint_live)
        y += _BAR_H + 4
        if m.unranked_reason:
            h = self._wrapped_h(m.unranked_reason, inner)
            p.setFont(self._font)
            p.setPen(QPen(QColor(theme.PROVISIONAL_COLOR)))
            p.drawText(QRectF(x0, y, inner, h), int(Qt.TextWordWrap | Qt.AlignLeft),
                       m.unranked_reason)
            y += h + 4
        else:
            y = self._paint_rows(p, m, x0, y, inner)
        ro = readout_text(m)
        if ro:
            y += 2
            p.setFont(self._num_font)
            near = m.to_best is None or abs(m.to_best) < m.tie_s or m.selected == m.best
            tone = None if near else theme.delta_colour(m.to_best)
            p.setPen(QPen(QColor(tone or C.text)))
            p.drawText(QRectF(x0, y, inner, _ROW_H), int(Qt.AlignVCenter | Qt.AlignLeft), ro)
            y += _ROW_H
        p.setFont(self._font)
        for text, colour in ((self._clock_text(m) if m.clock_estimated else None, C.text_dim),
                             (m.excluded, C.text_muted)):
            if text:
                h = self._wrapped_h(text, inner)
                p.setPen(QPen(QColor(colour)))
                p.drawText(QRectF(x0, y, inner, h), int(Qt.TextWordWrap | Qt.AlignLeft), text)
                y += h

    def _paint_rows(self, p: QPainter, m: CardModel, x0: float, y: float, inner: float) -> float:
        chips_x = x0 + _POS_W
        int_x = x0 + inner - _SPREAD_W - _INT_W
        spread_x = x0 + inner - _SPREAD_W
        p.setFont(self._font)
        p.setPen(QPen(QColor(C.text_muted)))
        p.drawText(QRectF(chips_x, y, int_x - chips_x, _ROW_H),
                   int(Qt.AlignVCenter | Qt.AlignLeft), "pack")
        p.drawText(QRectF(int_x - 40, y, _INT_W + 40, _ROW_H),
                   int(Qt.AlignVCenter | Qt.AlignRight), interval_header(m.over))
        p.drawText(QRectF(spread_x, y, _SPREAD_W, _ROW_H),
                   int(Qt.AlignVCenter | Qt.AlignRight), "spread")
        y += _ROW_H
        for r in m.rows:
            if r.selected:
                hl = QColor(C.surface_active)
                p.setPen(Qt.NoPen)
                p.setBrush(QBrush(hl))
                p.drawRoundedRect(QRectF(x0 - 3, y, inner + 6, _ROW_H), 3, 3)
            p.setFont(self._num_font)
            p.setPen(QPen(QColor(C.text_dim)))
            p.drawText(QRectF(x0, y, _POS_W - 6, _ROW_H), int(Qt.AlignVCenter | Qt.AlignRight),
                       str(r.position))
            cx = chips_x
            p.setFont(self._font)
            chips, more = self.fit_chips(r, m.selected, int_x - chips_x)
            for lap in chips:
                slot = self.colours.get(lap)
                fill = QColor(CHART_SERIES[slot % len(CHART_SERIES)] if slot is not None
                              else C.text_muted)
                cy = y + _ROW_H / 2
                p.setPen(QPen(_RING, 1.5) if lap == m.selected else Qt.NoPen)
                p.setBrush(QBrush(fill))
                p.drawEllipse(QPointF(cx + _CHIP_DOT / 2 + 1, cy), _CHIP_DOT / 2, _CHIP_DOT / 2)
                label = f"L{lap_label(lap)}"
                tw = self._fm.horizontalAdvance(label)
                p.setPen(QPen(QColor(C.text)))
                p.drawText(QRectF(cx + _CHIP_DOT + 4, y, tw + 2, _ROW_H),
                           int(Qt.AlignVCenter | Qt.AlignLeft), label)
                cx += _CHIP_DOT + 4 + tw + 8
            if more:
                p.setPen(QPen(QColor(C.text_dim)))
                p.drawText(QRectF(cx, y, int_x - cx, _ROW_H), int(Qt.AlignVCenter | Qt.AlignLeft),
                           f"+{more}")
            p.setFont(self._num_font)
            p.setPen(QPen(QColor(C.text)))
            p.drawText(QRectF(int_x, y, _INT_W, _ROW_H), int(Qt.AlignVCenter | Qt.AlignRight),
                       interval_text(r, m.over, self.lap_times))
            p.setPen(QPen(QColor(C.text_dim)))
            p.drawText(QRectF(spread_x, y, _SPREAD_W, _ROW_H),
                       int(Qt.AlignVCenter | Qt.AlignRight), spread_text(r))
            y += _ROW_H
        if m.hidden_packs:
            p.setFont(self._font)
            p.setPen(QPen(QColor(C.text_muted)))
            p.drawText(QRectF(chips_x, y, inner, _ROW_H), int(Qt.AlignVCenter | Qt.AlignLeft),
                       self._more_text(m))
            y += _ROW_H
        return y

    def _chip_w(self, lap: int) -> float:
        return _CHIP_DOT + 4 + self._fm.horizontalAdvance(f"L{lap_label(lap)}") + 8

    def fit_chips(self, row: PackRow, selected: int | None, room: float) -> tuple[list[int], int]:
        """The chips that fit in `room` px with the "+N" count after them: chips drop from the end
        (the slowest named lap first), never the selected lap; every dropped lap joins the count."""
        chips = list(row.chips)
        def need(cs, more):
            return sum(self._chip_w(c) for c in cs) + (
                self._fm.horizontalAdvance(f"+{more}") if more else 0)
        while chips and need(chips, len(row.laps) - len(chips)) > room:
            drop = next((c for c in reversed(chips) if c != selected), None)
            if drop is None:
                break
            chips.remove(drop)
        return chips, len(row.laps) - len(chips)

    # ------------------------------------------------------------------ input
    def _hit(self, pos) -> str | None:
        for key, rect in self._hits.items():
            if rect.contains(QPointF(pos)):
                return key
        return None

    def _scrub_to(self, x: float) -> None:
        bar = self._hits.get("bar")
        if bar is not None and bar.width() > 0:
            self.scrubbed.emit(min(max((x - bar.x()) / bar.width(), 0.0), 1.0))

    def mousePressEvent(self, event):
        # Swallowed whatever it hits: a press that fell through would start a pan of the map.
        event.accept()
        key = self._hit(event.position())
        if key == "bar":
            self._scrubbing = True
            self._scrub_to(event.position().x())
        elif key is not None:
            {"play": self.playToggled, "restart": self.restartRequested,
             "speed": self.speedCycled, "close": self.closeRequested}[key].emit()

    def mouseMoveEvent(self, event):
        event.accept()
        if self._scrubbing:
            self._scrub_to(event.position().x())
            return
        self.setCursor(Qt.PointingHandCursor if self._hit(event.position()) else Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        event.accept()
        self._scrubbing = False
