"""Shareable lap-strip card (image): every lap of the session as a bar, in the order it was driven.

The lap card (`share_card`) says how good the best lap was; this one says how it sat among the
rest. Same canvas, margins, faces and palette as the lap card, so the two read as a pair, and the
same header and honesty verdict: both are READ from `share_card.card_data`, never re-derived, so a
session the lap card refuses (provisional start line) gets no strip either, and a session the lap
card stamps "estimated timing (est)" is stamped identically here.

TWO LAYERS, like the lap card:

  * ``lapstrip_data(session, *, unit)`` — pure (no widget, no painter): one ``StripBar`` per lap
    plus the header/footer strings. Testable with a duck-typed Session.
  * ``render_lapstrip(data, *, palette)`` — the QPainter composition on a QImage.

WHICH LAPS ARE BARS. Exactly the Laps table's rows (`Session.lap_rows`), in lap-id order:

  * a clean lap — a solid bar;
  * a lap with a GPS dropout (the table's ⚠ row) — a HATCHED bar at its own gap: it is a timed lap,
    but its time is less reliable, so it never reads as a plain slow lap;
  * an EXCLUDED lap (the table's ⊘ strip: an out-lap, an in-lap, a piece that does not end where it
    started, a stop) is not a table row and gets no bar height — its slot keeps its place in the
    driven order and carries a ⊘ mark, named in the legend with its reasons
    (`_signal.exclusion_summary`). Never dropped, never drawn as a slow lap;
  * a start/finish crossing too brief to be a lap is in neither list, so it is no slot at all; the
    legend says so in `_signal.too_brief_note`'s sentence (the Laps tab's own words).

THE HEIGHT RULE (measured by looking; see the PR). A linear scale from zero is useless: a 1:00 lap
and a 1:01 lap are 98 % the same bar. So the scale is ANCHORED AT THE BEST LAP: the best bar is a
fixed stub (`STUB`, so it is still a visible bar) and height above it is the lap's gap to the best.
The top of the plot is the Laps table's own Δbest bar scale (`lap_table.delta_bar_scale`): the
slowest clean lap inside Tukey's upper fence (Q3 + 1.5 IQR of the clean gaps; the plain maximum
under 4 clean laps). One spin or pit lap therefore cannot squash every honest bar to a sliver: a
lap past the fence is CLIPPED at the top, drawn with the table's break mark, the cap line is
dashed and labelled with the gap it stands for, and the legend names each cut lap with its real
gap. The top never stands for less than `MIN_SPAN_S` (0.5 s): without that floor a tight session
is stretched to fill the plot, and three laps 0.19 s apart looked like a 2 s spread. Gaps are taken
between the PRINTED milliseconds (`lap_table.printed_ms`), so the strip and the table agree to
the digit. Measured on the demo (14 laps: fence 1.26 s, one lap cut), a synthetic 3-lap session
(0.19 s spread: the floor holds the top at 0.5 s) and a 62-lap one with a spin (fence 0.49 s,
the spin cut, every other bar readable).

COLOUR. The best lap wears the palette's best-lap hue and a ★. A lap whose gap is inside timing
precision (`library.within_timing_precision`, the table's TONE_LEVEL rule) wears the SAME hue,
lighter — level with the best as far as the timing can tell, never coloured as slower. Every other
lap is neutral: its height already says how much slower it was, and a wall of the behind hue would
shout what the bars say quietly.

THE MEDIAN. A dashed line at the clean laps' median gap, spelled in the hero row as what it is:
"median lap +0.84 s off the best" — from `MEDIAN_MIN_LAPS` clean laps up (the median of three
laps is one of the bars, drawn again). No trend line, no consistency score, no advice: a strip of lap
times does not say why a lap was slow.

60+ LAPS. Bars thin out to the slot; the axis labels only the best, the first, the last and every
10th lap (every lap up to `LABEL_ALL_MAX`), dropping a label that would touch a kept one.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPen

from . import share_card, theme, units
from ._signal import exclusion_summary, fmt_signed, lap_label, too_brief_note
from .lap_table import (
    EXCLUDED_MARK,
    TONE_BEHIND,
    TONE_LEVEL,
    delta_bar_scale,
    printed_ms,
    too_brief_count,
)
from .library import PB_PRECISION_S, within_timing_precision

# The lap card's private type helpers, reused so the pair share one face, one fit and one baseline
# convention (share_card.py is owned by another change; these are imported, not copied).
from .share_card import _draw_text, _fit_line, _fit_title, _font

CARD_W, CARD_H = share_card.CARD_W, share_card.CARD_H

KIND_LAP = "lap"            # a clean, timed lap: solid bar
KIND_DROPOUT = "dropout"    # a timed lap with a GPS dropout (the table's ⚠): hatched bar
KIND_EXCLUDED = "excluded"  # left out of the times (the table's ⊘ strip): a marked slot, no height
TONE_BEST = "best"

STUB = 0.08            # the best lap's bar, as a fraction of the plot: still visibly a bar
# The least gap the plot's top may stand for. Without it a tight session is stretched to fill the
# plot: three laps 0.19 s apart drew the slowest at full height, the same picture as a 2 s spread.
# 0.5 s is about seven times the timing precision, so a gap at the precision is still a visible
# step (7 % of the height) while a session that IS tight looks tight.
MIN_SPAN_S = 0.5
MIN_CLEAN_LAPS = 2     # fewer clean laps than this and there is nothing to compare
# A median of three or four laps is one of those laps: its dashed line just re-drew a bar.
MEDIAN_MIN_LAPS = 5
LABEL_ALL_MAX = 20     # up to this many slots every lap is labelled; beyond, best/first/last/10s

# The menu's reason when the strip is greyed for want of laps (the other reasons are the lap card's).
FEW_LAPS_REASON = ("Fewer than two clean laps — a lap strip needs at least two laps to compare. "
                   "Drive a few more, or check the start/finish line on the map.")


@dataclass(frozen=True)
class StripBar:
    lap_id: int
    label: str             # lap_label: the 1-based number the table prints
    kind: str              # KIND_LAP / KIND_DROPOUT / KIND_EXCLUDED
    gap_s: float | None    # to the best lap, between printed ms; None for an excluded lap
    tone: str              # TONE_BEST / TONE_LEVEL / TONE_BEHIND; "" for an excluded lap
    height: float          # 0..1 of the plot (0 for an excluded lap)
    clipped: bool          # past the fence: drawn at full height with a break mark


@dataclass(frozen=True)
class LapStripData:
    track: str
    date: str
    best_time: str
    best_lap_id: int | None
    bars: tuple[StripBar, ...]
    scale_s: float | None        # the gap the plot's top stands for (the table's bar scale)
    median_gap_s: float | None   # the clean laps' median gap to the best; None under 3 clean laps
    clean_laps: int
    excluded_note: str           # "1 off the session median, …" or ""
    too_brief: str               # too_brief_note(...) or ""
    stamp: str                   # the lap card's own honesty stamp ("estimated timing") or ""
    provenance: str              # the lap card's footer provenance, when it has one, or ""
    blocked: bool                # the lap card's verdict: no shareable image at all

    @property
    def offered(self) -> bool:
        return not self.blocked and self.clean_laps >= MIN_CLEAN_LAPS


def clean_lap_count(session) -> int:
    """Table rows without a GPS dropout — the laps a comparison stands on (the menu gate)."""
    try:
        rows = session.lap_rows()
        drop = session.dropout_lap_ids()
    except Exception:  # noqa: BLE001 — a menu sync must never raise
        return 0
    return sum(1 for r in rows if r["idx"] not in drop)


def best_median_line(median_gap_s: float) -> str:
    """The median, said as what it is: how far the session's middle lap was off the best."""
    return f"median lap {fmt_signed(median_gap_s, 2, 's')} off the best"


def lapstrip_data(session, *, unit: str | None = None) -> LapStripData:
    """The strip's bars and strings from Session accessors (see the module doc for each rule)."""
    unit = units.normalize_unit(unit)
    card = share_card.card_data(session, unit=unit)
    rows = list(session.lap_rows())
    dropouts = set(session.dropout_lap_ids())
    excluded = list(session.excluded_lap_ids() or [])
    times = {int(r["idx"]): float(r["time"]) for r in rows}

    # The table's own gap rule (_fill_delta): printed ms against the best; no best while timing is
    # provisional — and then the lap card has already blocked the image.
    best_id = card.best_lap_id if card.best_lap_id in times else None
    gaps: dict[int, float] = {}
    if best_id is not None:
        best_ms = printed_ms(times[best_id])
        gaps = {lid: (printed_ms(t) - best_ms) / 1000 for lid, t in times.items()}
    clean = [g for lid, g in gaps.items() if lid not in dropouts] or list(gaps.values())
    fence = delta_bar_scale(clean) if gaps else None
    scale = max(fence, MIN_SPAN_S) if fence else None

    bars = []
    for lid in sorted(set(times) | set(excluded)):
        if lid not in times:
            bars.append(StripBar(lid, lap_label(lid), KIND_EXCLUDED, None, "", 0.0, False))
            continue
        kind = KIND_DROPOUT if lid in dropouts else KIND_LAP
        gap = gaps.get(lid)
        if gap is None:
            bars.append(StripBar(lid, lap_label(lid), kind, None, TONE_BEHIND, STUB, False))
            continue
        clipped = bool(scale) and gap > scale * (1 + 1e-9)
        frac = min(max(gap, 0.0) / scale, 1.0) if scale else 0.0
        tone = (TONE_BEST if lid == best_id
                else TONE_LEVEL if within_timing_precision(gap) else TONE_BEHIND)
        bars.append(StripBar(lid, lap_label(lid), kind, gap, tone,
                             STUB + (1 - STUB) * frac, clipped))

    clean_gaps = [g for lid, g in gaps.items() if lid not in dropouts]
    reasons = session.excluded_lap_reasons() if excluded else {}
    return LapStripData(
        track=card.track, date=card.date, best_time=card.best_time, best_lap_id=best_id,
        bars=tuple(bars), scale_s=scale,
        median_gap_s=(statistics.median(clean_gaps)
                      if len(clean_gaps) >= MEDIAN_MIN_LAPS else None),
        clean_laps=sum(1 for lid in times if lid not in dropouts),
        excluded_note=(exclusion_summary(reasons) or f"{len(excluded)} left out") if excluded else "",
        too_brief=too_brief_note(too_brief_count(session, len(times) + len(excluded))),
        stamp=card.stamp, provenance=getattr(card, "provenance", ""), blocked=card.blocked)


# --------------------------------------------------------------------------- Qt rendering
PAD = 72
RIGHT = CARD_W - PAD
PLOT_TOP = 452
PLOT_BASE = 1060
SCALE_COL = 104      # the gridline labels' column, right of the bars
MAX_BAR_W = 64


def _nice_step(scale: float) -> float:
    for step in (0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 60.0):
        if scale / step <= 4.0:
            return step
    return scale / 4


def axis_label_ids(bars: tuple[StripBar, ...], best_id: int | None) -> list[int]:
    """Which slots get a number under the axis (indices into `bars`): all of them up to
    LABEL_ALL_MAX, else the best, the first, the last and every 10th lap."""
    n = len(bars)
    if n <= LABEL_ALL_MAX:
        return list(range(n))
    keep = [0, n - 1] + [i for i, b in enumerate(bars) if b.lap_id == best_id]
    tens = [i for i, b in enumerate(bars) if int(b.label) % 10 == 0]
    return sorted(set(keep)) + [i for i in tens if i not in keep]


def render_lapstrip(data: LapStripData, *, palette: str | None = None) -> QImage:
    """The strip as a CARD_W×CARD_H ARGB QImage (the caller never renders a blocked one).
    `palette` selects the semantic hues like the lap card's; restored on exit."""
    prev = theme.active_palette()
    if palette is not None:
        theme.set_palette(palette)
    try:
        return _paint(data)
    finally:
        theme.set_palette(prev)


def _paint(data: LapStripData) -> QImage:
    img = QImage(CARD_W, CARD_H, QImage.Format_ARGB32)
    img.fill(QColor(theme.C.canvas))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    best_hue = theme.best_lap_colour()

    # --- header: the lap card's, line for line (stamp first so the title can make room for it)
    stamp_w = 0
    if data.stamp:
        stamp_txt = f"{data.stamp} {theme.ESTIMATED_MARK}"
        stamp_font = _font(26, theme.W_SEMIBOLD)
        p.setFont(stamp_font)
        stamp_w = p.fontMetrics().horizontalAdvance(stamp_txt)
        _draw_text(p, 0, 128, stamp_txt, stamp_font, theme.C.accent, align_right_at=RIGHT)
    title, title_font = _fit_title(p, data.track, (RIGHT - (stamp_w + 24) if data.stamp else RIGHT) - PAD)
    _draw_text(p, PAD, 128, title, title_font, theme.C.text)
    timed = sum(1 for b in data.bars if b.kind != KIND_EXCLUDED)
    sub = f"every lap, in the order driven · {timed} timed"
    _draw_text(p, PAD, 176, f"{data.date} · {sub}" if data.date else sub, _font(30), theme.C.text_dim)

    # --- hero row: the best lap and, when there is one, the median said as what it is
    _draw_text(p, PAD, 248, "BEST LAP", _font(28, theme.W_SEMIBOLD), theme.C.text_muted)
    best_lbl = f"★ lap {lap_label(data.best_lap_id)}" if data.best_lap_id is not None else ""
    _draw_text(p, PAD, 348, data.best_time, _font(96, theme.W_SEMIBOLD), theme.C.text)
    p.setFont(_font(96, theme.W_SEMIBOLD))
    time_w = p.fontMetrics().horizontalAdvance(data.best_time)
    if best_lbl:
        _draw_text(p, PAD + time_w + 24, 348, best_lbl, _font(34, theme.W_SEMIBOLD), best_hue)
    if data.median_gap_s is not None:
        _draw_text(p, 0, 248, "MEDIAN LAP", _font(28, theme.W_SEMIBOLD), theme.C.text_muted,
                   align_right_at=RIGHT)
        _draw_text(p, 0, 300, f"{fmt_signed(data.median_gap_s, 2, 's')}",
                   _font(40, theme.W_SEMIBOLD), theme.C.text, align_right_at=RIGHT)
        _draw_text(p, 0, 340, "off the best", _font(26), theme.C.text_dim, align_right_at=RIGHT)

    # --- the plot
    n = max(1, len(data.bars))
    plot_l, plot_r = PAD, RIGHT - SCALE_COL
    plot_h = PLOT_BASE - PLOT_TOP
    slot = (plot_r - plot_l) / n
    bar_w = max(2.0, slot * 0.72)
    if bar_w > MAX_BAR_W:   # a short session: bars at their widest, the group centred
        bar_w, slot = MAX_BAR_W, MAX_BAR_W / 0.6
    x0 = plot_l + ((plot_r - plot_l) - slot * n) / 2

    def y_of(h: float) -> float:
        return PLOT_BASE - h * plot_h

    def gap_y(g: float) -> float:
        return y_of(STUB + (1 - STUB) * (g / data.scale_s)) if data.scale_s else y_of(STUB)

    grid_font = _font(24)
    grid_pen = QPen(QColor(theme.C.border), 1.0)
    p.setPen(grid_pen)
    p.drawLine(QPointF(plot_l, PLOT_BASE + 0.5), QPointF(plot_r, PLOT_BASE + 0.5))
    # The scale column's labels, placed by priority so two never overprint (the median first, then
    # the cap, then "best", then the gridlines): (y-centre, text, font, colour).
    scale_labels: list[tuple[float, str, object, str]] = []
    clipped_any = any(b.clipped for b in data.bars)
    if data.median_gap_s is not None and data.scale_s:
        scale_labels.append((gap_y(min(data.median_gap_s, data.scale_s)), "median",
                             _font(24, theme.W_SEMIBOLD), theme.C.text))
    grid_ys = []
    if data.scale_s:
        # the cap: what the top of the plot stands for, dashed when a lap is cut by it
        cap_pen = QPen(QColor(theme.C.text_muted if clipped_any else theme.C.border), 1.5)
        if clipped_any:
            cap_pen.setStyle(Qt.DashLine)
        p.setPen(cap_pen)
        p.drawLine(QPointF(plot_l, y_of(1.0)), QPointF(plot_r, y_of(1.0)))
        scale_labels.append((y_of(1.0), f"+{data.scale_s:.2f} s", grid_font,
                             theme.C.text_dim if clipped_any else theme.C.text_muted))
    scale_labels.append((y_of(STUB), "best", grid_font, best_hue))
    if data.scale_s:
        step = _nice_step(data.scale_s)
        k = 1
        while k * step < data.scale_s * 0.9:
            y = gap_y(k * step)
            grid_ys.append(y)
            scale_labels.append((y, f"+{k * step:g} s", grid_font, theme.C.text_muted))
            k += 1
    p.setPen(grid_pen)
    for y in grid_ys:
        p.drawLine(QPointF(plot_l, y), QPointF(plot_r, y))
    taken: list[float] = []
    for y, text, font, colour in scale_labels:
        if any(abs(y - t) < 28 for t in taken):
            continue
        taken.append(y)
        _draw_text(p, 0, int(y) + 8, text, font, colour, align_right_at=RIGHT)

    level_hue = QColor(best_hue)
    level_hue.setAlphaF(0.5)
    tone_fill = {TONE_BEST: QColor(best_hue), TONE_LEVEL: level_hue,
                 TONE_BEHIND: QColor(theme.C.text_muted)}
    centres = []
    for i, b in enumerate(data.bars):
        cx = x0 + slot * (i + 0.5)
        centres.append(cx)
        if b.kind == KIND_EXCLUDED:
            r = max(5.0, min(bar_w / 2, 11.0))
            _paint_excluded(p, cx, PLOT_BASE - r - 4, 2 * r)
            continue
        top = y_of(b.height)
        rect = QRectF(cx - bar_w / 2, top, bar_w, PLOT_BASE - top)
        fill = tone_fill.get(b.tone, tone_fill[TONE_BEHIND])
        radius = min(4.0, bar_w / 4)
        if b.kind == KIND_DROPOUT:
            p.setPen(QPen(fill, 1.5))
            hatch = QBrush(fill, Qt.BDiagPattern)
            p.setBrush(hatch)
            p.drawRoundedRect(rect.adjusted(0.75, 0.75, -0.75, 0), radius, radius)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            p.drawRoundedRect(rect, radius, radius)
        if b.clipped:   # the table's break mark: two canvas cuts across the bar's top
            p.setPen(QPen(QColor(theme.C.canvas), 3.0))
            for dy in (14, 22):
                p.drawLine(QPointF(cx - bar_w / 2 - 1, top + dy + 4),
                           QPointF(cx + bar_w / 2 + 1, top + dy - 4))

    if data.median_gap_s is not None and data.scale_s:
        pen = QPen(QColor(theme.C.text), 1.5)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        y = gap_y(min(data.median_gap_s, data.scale_s))
        p.drawLine(QPointF(plot_l, y), QPointF(plot_r, y))

    # --- the best lap's call-out: its time over the tallest bar beneath it (above the plot when
    # that bar reaches the top), with a leader down to the best bar
    best_i = next((i for i, b in enumerate(data.bars) if b.tone == TONE_BEST), None)
    if best_i is not None:
        callout = f"★ {data.best_time}"
        cfont = _font(28, theme.W_SEMIBOLD)
        p.setFont(cfont)
        cw = p.fontMetrics().horizontalAdvance(callout)
        cx = min(max(centres[best_i], plot_l + cw / 2), plot_r - cw / 2)
        under = [data.bars[j].height for j, c in enumerate(centres)
                 if abs(c - cx) <= cw / 2 + slot / 2]
        y_text = y_of(max(under)) - 18
        if y_text < PLOT_TOP + 36:   # it would sit on the cap line: lift it clear above the plot
            y_text = PLOT_TOP - 16
        best_top = y_of(data.bars[best_i].height)
        p.setPen(QPen(QColor(best_hue), 1.5))
        if best_top - y_text > 26:
            p.drawLine(QPointF(centres[best_i], y_text + 8), QPointF(centres[best_i], best_top - 6))
        _draw_text(p, int(cx - cw / 2), int(y_text), callout, cfont, best_hue)

    # --- axis labels: lap numbers, thinned past LABEL_ALL_MAX, no two touching
    afont = _font(24)
    p.setFont(afont)
    fm = p.fontMetrics()
    placed: list[tuple[float, float]] = []
    for i in axis_label_ids(data.bars, data.best_lap_id):
        b = data.bars[i]
        w = fm.horizontalAdvance(b.label)
        left = min(max(centres[i] - w / 2, plot_l), plot_r - w)
        if any(left < hi + 10 and left + w + 10 > lo for lo, hi in placed):
            continue
        placed.append((left, left + w))
        colour = best_hue if b.tone == TONE_BEST else theme.C.text_dim
        _draw_text(p, int(left), PLOT_BASE + 34, b.label, _font(24, theme.W_SEMIBOLD
                   if b.tone == TONE_BEST else theme.W_REGULAR), colour)
    _draw_text(p, 0, PLOT_BASE + 34, "lap", afont, theme.C.text_muted, align_right_at=RIGHT)

    # --- legend: one entry per style ON this card, nothing for a style that is absent
    _paint_legend(p, data, tone_fill, clipped_any)

    # --- footer: the lap card's wordmark (the lowercase logotype, set as a graphic: the WORDMARK
    # form of the name convention in studio/__init__.py) and its right-hand line
    mark_font = _font(40, theme.W_SEMIBOLD)
    wordmark = "pacer"
    _draw_text(p, PAD, CARD_H - 56, wordmark, mark_font, theme.C.accent)
    p.setFont(mark_font)
    foot_avail = RIGHT - PAD - p.fontMetrics().horizontalAdvance(wordmark) - 24
    foot, foot_font = _fit_line(p, data.provenance or "race telemetry", foot_avail, (28, 24, 22))
    _draw_text(p, 0, CARD_H - 56, foot, foot_font, theme.C.text_muted, align_right_at=RIGHT)
    p.end()
    return img


def _paint_excluded(p: QPainter, cx: float, cy: float, d: float) -> None:
    """A slot's ⊘: a ring with a slash, muted — a lap that happened and was left out."""
    r = max(4.0, d / 2)
    p.setPen(QPen(QColor(theme.C.text_muted), 1.75))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r, r)
    k = r * math.sqrt(0.5)
    p.drawLine(QPointF(cx - k, cy + k), QPointF(cx + k, cy - k))


def legend_entries(data: LapStripData) -> list[tuple[str, str]]:
    """(style, words) for every bar style this card shows, in reading order. A style absent from
    the session has no entry, so the legend never names a thing the picture does not contain."""
    tones = {b.tone for b in data.bars if b.kind != KIND_EXCLUDED}
    kinds = {b.kind for b in data.bars}
    out = [(TONE_BEST, "best lap")]
    if TONE_LEVEL in tones:
        out.append((TONE_LEVEL, f"level with the best (under {PB_PRECISION_S:.2f} s)"))
    out.append((TONE_BEHIND, "height = time off the best"))
    if KIND_DROPOUT in kinds:
        out.append((KIND_DROPOUT, "GPS dropout: time less reliable"))
    cut = [b for b in data.bars if b.clipped]
    if cut and data.scale_s:
        # The cut laps BY NAME with their real gap (up to three): the bar no longer says it.
        who = (", ".join(f"lap {b.label} {fmt_signed(b.gap_s or 0.0, 2, 's')}" for b in cut)
               if len(cut) <= 3 else f"{len(cut)} laps")
        out.append(("clipped", f"{who}: cut at the +{data.scale_s:.2f} s line"))
    if KIND_EXCLUDED in kinds:
        out.append((KIND_EXCLUDED, f"{EXCLUDED_MARK} left out: {data.excluded_note}"))
    return out


def _paint_legend(p: QPainter, data: LapStripData, tone_fill: dict, clipped_any: bool) -> None:
    font = _font(24)
    p.setFont(font)
    fm = p.fontMetrics()
    x, y = float(PAD), PLOT_BASE + 86
    sw, gap = 18.0, 28.0
    for style, words in legend_entries(data):
        mark_text = words.removeprefix(f"{EXCLUDED_MARK} ")
        w = sw + 10 + fm.horizontalAdvance(mark_text)
        if x + w > RIGHT and x > PAD:
            x, y = float(PAD), y + 34
        box = QRectF(x, y - sw + 2, sw, sw)
        if style == KIND_EXCLUDED:
            _paint_excluded(p, box.center().x(), box.center().y(), sw * 0.9)
        elif style == KIND_DROPOUT:
            p.setPen(QPen(tone_fill[TONE_BEHIND], 1.5))
            p.setBrush(QBrush(tone_fill[TONE_BEHIND], Qt.BDiagPattern))
            p.drawRect(box.adjusted(0.75, 0.75, -0.75, -0.75))
        elif style == "clipped":
            p.setPen(Qt.NoPen)
            p.setBrush(tone_fill[TONE_BEHIND])
            p.drawRect(box)
            p.setPen(QPen(QColor(theme.C.canvas), 3.0))
            p.drawLine(QPointF(box.left() - 1, box.center().y() + 4),
                       QPointF(box.right() + 1, box.center().y() - 4))
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(tone_fill[style])
            p.drawRoundedRect(box, 3, 3)
        _draw_text(p, int(x + sw + 10), int(y), mark_text, font, theme.C.text_dim)
        x += w + gap
    if data.too_brief:
        y += 34
        _draw_text(p, PAD, int(y), data.too_brief, font, theme.C.text_muted)


def lapstrip_to_png(image: QImage) -> bytes:
    """In-memory PNG bytes (the lap card's encoder)."""
    return share_card.card_to_png(image)


def save_lapstrip_png(image: QImage, path: str) -> None:
    """Write exactly `path`; OSError when Qt cannot (QImage.save returns False, never raises)."""
    if not image.save(path, "PNG"):
        raise OSError(f"could not write {path}")
