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

  * a dropout lap timed QUICKER than the best (the table prints "−0.600": the best is the quickest
    clean lap) is not a tie and not level: a short hatched block hanging BELOW the zero line,
    named in the legend "lap 3 −0.60 s: quicker, GPS dropout (not counted)".

THE HEIGHT RULE (measured by looking; see the PR). A linear scale from a zero lap time is useless:
a 1:00 lap and a 1:01 lap are 98 % the same bar. So the scale is ANCHORED AT THE BEST LAP: the
plot's base is the zero gap (labelled "best"), a bar's height is its gap to the best — so heights
are proportional to gaps — and the best lap itself is a tick and a ★ on that base, with its time in
a call-out. A timed lap within a pixel of the base (a tie, a +0.001 s lap) is still drawn
`MIN_DRAW_PX` tall so it does not vanish. The top of the plot is the Laps table's own Δbest bar scale (`lap_table.delta_bar_scale`): the
slowest clean lap inside Tukey's upper fence (Q3 + 1.5 IQR of the clean gaps; the plain maximum
under 4 clean laps). One spin or pit lap therefore cannot squash every honest bar to a sliver: a
lap past the fence is CLIPPED at the top, drawn with the table's break mark, the cap line is
dashed and labelled with the gap it stands for, and the legend names each clipped lap with its real
gap (CLIPPED, never "cut": the same card marks a lap that cut the track, "left the line"). The top never stands for less than `MIN_SPAN_S` (0.5 s): without that floor a tight session
is stretched to fill the plot, and three laps 0.19 s apart looked like a 2 s spread. Gaps are taken
between the PRINTED milliseconds (`lap_table.printed_ms`), so the strip and the table agree to
the digit. Measured on the demo (14 laps: fence 1.26 s, one lap clipped), a synthetic 3-lap session
(0.19 s spread: the floor holds the top at 0.5 s) and a 62-lap one with a spin (fence 0.49 s,
the spin clipped, every other bar readable).

COLOUR. The best lap wears the palette's best-lap hue and a ★ (one lap: a second lap at the same
printed time is "level", not a second best). A lap whose gap is inside timing
precision (`library.within_timing_precision`, the table's TONE_LEVEL rule) wears the SAME hue,
lighter — level with the best as far as the timing can tell, never coloured as slower. Every other
lap is neutral: its height already says how much slower it was, and a wall of the behind hue would
shout what the bars say quietly.

THE MEDIAN. A dashed line at the median of the CLEAN laps' gaps to the best (with an even count,
the mean of the middle two, which no lap need have), spelled in the hero row as what it is:
"MEDIAN OF 13 CLEAN LAPS +0.68 s off the best", or "≈ best" when it is inside timing precision —
from `MEDIAN_MIN_LAPS` clean laps up (the median of three laps is one of the bars, drawn again).
The line breaks around the best lap's call-out rather than strike its time through. No trend line, no consistency score, no advice: a strip of lap
times does not say why a lap was slow.

60+ LAPS. Bars thin out to the slot; the axis labels only the best, the first, the last and every
10th lap (every lap up to `LABEL_ALL_MAX`), dropping a label that would touch a kept one. A dropout
bar keeps a ~35 % fill under its hatch so a thin one still reads as a bar.

THE LEGEND NEVER PASSES THE FOOTER (`legend_plan`). Each entry is fitted to the row (24 then 22 px,
else elided); four or more clipped or quicker laps become a count ("5 laps clipped at the +0.59 s line");
the excluded reasons fold into "N laps left out" when the full line would not fit; the plot's base
lifts (to `PLOT_BASE_MIN`) to make room for more rows; and only then are entries dropped, in
`LEGEND_PRIORITY` order: the grey "height" key first, then the level key, then the best key (the
★ call-out names the best lap anyway), then the dropout, quicker, clipped and excluded facts, and the
too-brief sentence last.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QImage, QPainter, QPen

from . import data_quality, share_card, theme, units
from ._signal import exclusion_summary, fmt_signed, lap_label, plural, too_brief_note
from .lap_table import (
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
KIND_OFF_LINE = "off_line"  # a timed lap that left the line the other laps drive (⚠): hatched
KIND_GAP = "chapter_gap"  # a timed lap across a missing chapter (⚠): hatched, its own legend
#: `data_quality.lap_warning_kinds` kind → its bar kind.
_KIND_BAR = {data_quality.WARN_DROPOUT: KIND_DROPOUT, data_quality.WARN_OFF_LINE: KIND_OFF_LINE,
             data_quality.WARN_CHAPTER_GAP: KIND_GAP}
KIND_EXCLUDED = "excluded"  # left out of the times (the table's ⊘ strip): a marked slot, no height
TONE_BEST = "best"

TONE_QUICKER = "quicker"    # a dropout lap timed quicker than the best: not counted, not a tie
# The plot's base IS the zero gap, so heights are proportional to gaps (a lap twice as far off the
# best is twice as tall). An earlier 8 % stub under the best put the base below zero and drew a
# +0.215 s lap at 2.97x the best's bar, a 5.85 ratio as 4.21. The best lap is a tick + ★ on the base.
MIN_DRAW_PX = 4.0      # a timed lap within a pixel of the base still shows (a tie, a +0.001 s lap)
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
    tone: str              # TONE_BEST / TONE_LEVEL / TONE_BEHIND / TONE_QUICKER; "" excluded
    height: float          # 0..1 of the plot, = gap / scale (0 for the best and excluded laps)
    clipped: bool          # past the fence: drawn at full height with a break mark


@dataclass(frozen=True)
class LapStripData:
    track: str
    date: str
    best_time: str
    best_lap_id: int | None
    bars: tuple[StripBar, ...]
    scale_s: float | None        # the gap the plot's top stands for (the table's bar scale)
    median_gap_s: float | None   # the clean laps' median gap to the best; None under MEDIAN_MIN_LAPS clean laps
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
    """Table rows without a ⚠ (a GPS dropout, or a lap that left the line) — the laps a
    comparison stands on (the menu gate)."""
    try:
        rows = session.lap_rows()
        drop = data_quality.lap_warnings(session)
    except Exception:  # noqa: BLE001 — a menu sync must never raise
        return 0
    return sum(1 for r in rows if r["idx"] not in drop)


def median_figure(median_gap_s: float) -> str:
    """The median gap as printed: "+0.68 s", or "≈ best" when it is inside timing precision — a
    figure finer than the timing can resolve would claim a difference it cannot see."""
    if within_timing_precision(median_gap_s):
        return "≈ best"
    return fmt_signed(median_gap_s, 2, "s")


def best_median_line(median_gap_s: float, clean_laps: int) -> str:
    """The median said as what it is: the median of the CLEAN laps' gaps to the best (with an even
    count, the mean of the middle two — a value no lap need have)."""
    fig = median_figure(median_gap_s)
    where = "level with the best" if fig == "≈ best" else f"{fig} off the best"
    return f"median of {plural(clean_laps, 'clean lap')}: {where}"


def lapstrip_data(session, *, unit: str | None = None) -> LapStripData:
    """The strip's bars and strings from Session accessors (see the module doc for each rule)."""
    unit = units.normalize_unit(unit)
    card = share_card.card_data(session, unit=unit)
    rows = list(session.lap_rows())
    # The ⚠ set, each lap under its ONE kind (`lap_warning_kinds`), as laps.csv flags it.
    kinds = data_quality.lap_warning_kinds(session)
    dropouts = set(kinds)
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
        kind = _KIND_BAR.get(kinds.get(lid, ""), KIND_LAP)
        gap = gaps.get(lid)
        if gap is None:
            bars.append(StripBar(lid, lap_label(lid), kind, None, TONE_BEHIND, 0.0, False))
            continue
        clipped = bool(scale) and gap > scale * (1 + 1e-9)
        frac = min(max(gap, 0.0) / scale, 1.0) if scale else 0.0
        # A NEGATIVE gap is only possible for a dropout lap (the best is the quickest CLEAN lap):
        # the table prints it ("−0.600") and calls it a time this lap cannot really have. It is
        # its own tone, never a tie with the best and never level.
        tone = (TONE_BEST if lid == best_id
                else TONE_QUICKER if gap < 0 and not within_timing_precision(gap)
                else TONE_LEVEL if within_timing_precision(gap) else TONE_BEHIND)
        bars.append(StripBar(lid, lap_label(lid), kind, gap, tone, frac, clipped))

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
        stamp=card.stamp, provenance=card.provenance, blocked=card.blocked)


# --------------------------------------------------------------------------- Qt rendering
PAD = 72
RIGHT = CARD_W - PAD
PLOT_TOP = 452
PLOT_BASE_MAX = 1060   # the plot's base with a one-row legend
PLOT_BASE_MIN = 992    # ...lifted at most this far so a longer legend still fits
SCALE_COL = 104        # the gridline labels' column, right of the bars
MAX_BAR_W = 64
FOOTER_Y = CARD_H - 56            # the wordmark's baseline
LEGEND_LIMIT = FOOTER_Y - 56      # no legend baseline below this: clear of the 40 px wordmark
LEGEND_ROW = 34
LEGEND_GAP_BELOW_AXIS = 86        # plot base -> the legend's first baseline
LEGEND_PX = (24, 22)              # an entry's fit steps before it is elided
_SW, _SW_GAP, _ENTRY_GAP = 18.0, 10.0, 28.0

# Which legend entries go first when the legend cannot fit even with the plot lifted, lowest value
# first. The explainers go before the facts: the grey "height" key, then the level and best keys
# (the ★ call-out already names the best lap), then the per-lap facts. The too-brief sentence —
# the one line that accounts for crossings the picture cannot show at all — is not in the table:
# `legend_plan` drops it only once every entry here has gone.
LEGEND_PRIORITY = {TONE_BEHIND: 0, TONE_LEVEL: 1, TONE_BEST: 2, KIND_DROPOUT: 3, KIND_OFF_LINE: 3,
                   KIND_GAP: 3,
                   TONE_QUICKER: 4,
                   "clipped": 5, KIND_EXCLUDED: 6}


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


def legend_entries(data: LapStripData, *, short: bool = False) -> list[tuple[str, str]]:
    """(style, words) for every mark this card shows, in reading order. A style absent from the
    session has no entry, so the legend never names a thing the picture does not contain. `short`
    folds the excluded reasons into a count, for when the full line cannot fit."""
    timed = [b for b in data.bars if b.kind != KIND_EXCLUDED]
    out = [(TONE_BEST, "best lap")]
    if any(b.tone == TONE_LEVEL for b in timed):
        out.append((TONE_LEVEL, f"level with the best (under {PB_PRECISION_S:.2f} s)"))
    if any(b.tone == TONE_BEHIND and b.kind == KIND_LAP for b in timed):
        out.append((TONE_BEHIND, "height = time off the best"))
    if any(b.kind == KIND_DROPOUT and b.tone != TONE_QUICKER for b in timed):
        out.append((KIND_DROPOUT, "GPS dropout: time less reliable"))
    if any(b.kind == KIND_OFF_LINE and b.tone != TONE_QUICKER for b in timed):
        out.append((KIND_OFF_LINE, "left the line the other laps drive: not counted"))
    if any(b.kind == KIND_GAP and b.tone != TONE_QUICKER for b in timed):
        out.append((KIND_GAP, "timed across a missing chapter: not counted"))
    # One entry per ⚠ reason among the quicker laps. Two reasons on one line ("…: quicker, GPS
    # dropout or left the line (not counted)") never said which lap was which, and ran past the
    # row: the fit elided it to "(not co…". Each kind's line names its own laps; the legend's flow
    # gives each its own row when they do not share one.
    quick = [b for b in timed if b.tone == TONE_QUICKER]
    for kind, why in ((KIND_DROPOUT, "GPS dropout"), (KIND_OFF_LINE, "left the line"),
                      (KIND_GAP, "across a missing chapter")):
        laps = [b for b in quick if b.kind == kind]
        if laps:
            who = (", ".join(f"lap {b.label} {fmt_signed(b.gap_s or 0.0, 2, 's')}" for b in laps)
                   if len(laps) <= 3 else plural(len(laps), "lap"))
            out.append((TONE_QUICKER, f"{who}: quicker, {why} (not counted)"))
    clipped = [b for b in timed if b.clipped]
    if clipped and data.scale_s:
        # The clipped laps BY NAME with their real gap, up to three: the bar no longer says it.
        # "Clipped", not "cut": a cut lap on this card is one that left the line (above).
        who = (", ".join(f"lap {b.label} {fmt_signed(b.gap_s or 0.0, 2, 's')}" for b in clipped)
               if len(clipped) <= 3 else plural(len(clipped), "lap"))
        out.append(("clipped", f"{who} clipped at the +{data.scale_s:.2f} s line"))
    n_ex = sum(1 for b in data.bars if b.kind == KIND_EXCLUDED)
    if n_ex:
        out.append((KIND_EXCLUDED, f"{plural(n_ex, 'lap')} left out" if short
                    else f"left out: {data.excluded_note}"))
    return out


@dataclass(frozen=True)
class LegendItem:
    style: str
    text: str     # as drawn (fitted / elided)
    px: int       # font pixel size
    x: float      # the swatch's left (or the sentence's left)
    y: float      # baseline
    w: float      # swatch + gap + text advance


@dataclass(frozen=True)
class LegendPlan:
    plot_base: float
    items: tuple[LegendItem, ...]
    dropped: tuple[str, ...]      # styles left off because nothing else could make room


def _fit_px(text: str, avail: float) -> tuple[str, int, float]:
    """`text` at the first LEGEND_PX size that fits `avail`, else elided at the smallest."""
    for px in LEGEND_PX:
        fm = QFontMetrics(_font(px))
        if fm.horizontalAdvance(text) <= avail:
            return text, px, fm.horizontalAdvance(text)
    fm = QFontMetrics(_font(LEGEND_PX[-1]))
    t = fm.elidedText(text, Qt.ElideRight, int(avail))
    return t, LEGEND_PX[-1], fm.horizontalAdvance(t)


def legend_plan(data: LapStripData) -> LegendPlan:
    """Where every legend entry goes, and how far the plot's base must lift for it. Never below
    LEGEND_LIMIT and never past the margins: each entry is fitted to the row (the excluded reasons
    fold into a count first), the plot lifts up to PLOT_BASE_MIN, and only then are entries dropped
    in LEGEND_PRIORITY order."""
    width = RIGHT - PAD
    text_avail = width - _SW - _SW_GAP

    def entries(short: bool) -> list[tuple[str, str, int, float]]:
        out = []
        for style, words in legend_entries(data, short=short):
            text, px, tw = _fit_px(words, text_avail)
            out.append((style, text, px, _SW + _SW_GAP + tw))
        return out

    full = entries(False)
    # the excluded reasons fold into "N laps left out" rather than be elided
    if any(s == KIND_EXCLUDED and t.endswith("…") for s, t, _, _ in full):
        full = entries(True)
    brief = _fit_px(data.too_brief, width) if data.too_brief else None
    max_rows = 1 + int((LEGEND_LIMIT - (PLOT_BASE_MIN + LEGEND_GAP_BELOW_AXIS)) // LEGEND_ROW)

    def flow(items):
        rows: list[list] = [[]]
        x = 0.0
        for it in items:
            if rows[-1] and x + it[3] > width:
                rows.append([])
                x = 0.0
            rows[-1].append((x, it))
            x += it[3] + _ENTRY_GAP
        return rows if rows[0] else []

    kept, dropped = list(full), []
    keep_brief = brief is not None
    while True:
        rows = flow(kept)
        n_rows = len(rows) + (1 if keep_brief else 0)
        if n_rows <= max_rows:
            break
        # drop the lowest-priority entry still on (the too-brief sentence ranks last)
        cands = [(LEGEND_PRIORITY.get(s, 0), i) for i, (s, *_r) in enumerate(kept)]
        if cands:
            _, i = min(cands)
            dropped.append(kept.pop(i)[0])
        else:
            keep_brief = False
            dropped.append("too_brief")
    base = min(PLOT_BASE_MAX, LEGEND_LIMIT - LEGEND_GAP_BELOW_AXIS - (n_rows - 1) * LEGEND_ROW)
    y = base + LEGEND_GAP_BELOW_AXIS
    items = []
    for r in rows:
        for x, (style, text, px, w) in r:
            items.append(LegendItem(style, text, px, PAD + x, y, w))
        y += LEGEND_ROW
    if keep_brief and brief is not None:
        text, px, tw = brief
        items.append(LegendItem("too_brief", text, px, PAD, y, tw))
    return LegendPlan(base, tuple(items), tuple(dropped))


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
    plan = legend_plan(data)
    base = plan.plot_base

    # --- header: the lap card's, line for line (stamp first so the title can make room for it)
    stamp_w = 0
    if data.stamp:
        stamp_txt = f"{data.stamp} {theme.ESTIMATED_MARK}"
        stamp_font = _font(26, theme.W_SEMIBOLD)
        p.setFont(stamp_font)
        stamp_w = p.fontMetrics().horizontalAdvance(stamp_txt)
        _draw_text(p, 0, 128, stamp_txt, stamp_font, theme.C.accent, align_right_at=RIGHT)
    title, title_font = _fit_title(p, data.track,
                                   (RIGHT - (stamp_w + 24) if data.stamp else RIGHT) - PAD)
    _draw_text(p, PAD, 128, title, title_font, theme.C.text)
    timed = sum(1 for b in data.bars if b.kind != KIND_EXCLUDED)
    sub = f"every lap, in the order driven · {timed} timed"
    sub, sub_font = _fit_line(p, f"{data.date} · {sub}" if data.date else sub, RIGHT - PAD,
                              (30, 26, 24))
    _draw_text(p, PAD, 176, sub, sub_font, theme.C.text_dim)

    # --- hero row: the best lap and, when there is one, the clean laps' median gap
    _draw_text(p, PAD, 248, "BEST LAP", _font(28, theme.W_SEMIBOLD), theme.C.text_muted)
    _draw_text(p, PAD, 348, data.best_time, _font(96, theme.W_SEMIBOLD), theme.C.text)
    p.setFont(_font(96, theme.W_SEMIBOLD))
    time_w = p.fontMetrics().horizontalAdvance(data.best_time)
    if data.best_lap_id is not None:
        _draw_text(p, PAD + time_w + 24, 348, f"★ lap {lap_label(data.best_lap_id)}",
                   _font(34, theme.W_SEMIBOLD), best_hue)
    if data.median_gap_s is not None:
        fig = median_figure(data.median_gap_s)
        # the label says whose median: the CLEAN laps' gaps, which with an even count no lap has
        _draw_text(p, 0, 248, f"MEDIAN OF {plural(data.clean_laps, 'clean lap').upper()}",
                   _font(28, theme.W_SEMIBOLD), theme.C.text_muted, align_right_at=RIGHT)
        _draw_text(p, 0, 300, fig, _font(40, theme.W_SEMIBOLD), theme.C.text, align_right_at=RIGHT)
        if fig != "≈ best":
            _draw_text(p, 0, 340, "off the best", _font(26), theme.C.text_dim,
                       align_right_at=RIGHT)

    # --- the plot: base = zero gap, so a bar's height is its gap
    n = max(1, len(data.bars))
    plot_l, plot_r = PAD, RIGHT - SCALE_COL
    plot_h = base - PLOT_TOP
    slot = (plot_r - plot_l) / n
    bar_w = max(2.0, slot * 0.72)
    if bar_w > MAX_BAR_W:   # a short session: bars at their widest, the group centred
        bar_w, slot = MAX_BAR_W, MAX_BAR_W / 0.6
    x0 = plot_l + ((plot_r - plot_l) - slot * n) / 2
    centres = [x0 + slot * (i + 0.5) for i in range(len(data.bars))]

    def y_of(h: float) -> float:
        return base - h * plot_h

    def gap_y(g: float) -> float:
        return y_of(g / data.scale_s) if data.scale_s else base

    grid_font = _font(24)
    grid_pen = QPen(QColor(theme.C.border), 1.0)
    # The scale column's labels, placed by priority so two never overprint (the median first, then
    # "best" at the base, then the cap, then the gridlines): (y, text, font, colour).
    scale_labels: list[tuple[float, str, QFont, str]] = []
    clipped_any = any(b.clipped for b in data.bars)
    median_y = None
    if data.median_gap_s is not None and data.scale_s:
        median_y = gap_y(min(max(data.median_gap_s, 0.0), data.scale_s))
        scale_labels.append((median_y, "median", _font(24, theme.W_SEMIBOLD), theme.C.text))
    scale_labels.append((base, "best", _font(24, theme.W_SEMIBOLD), best_hue))
    grid_ys = []
    if data.scale_s:
        cap_pen = QPen(QColor(theme.C.text_muted if clipped_any else theme.C.border), 1.5)
        if clipped_any:   # the cap is dashed when a lap is clipped by it
            cap_pen.setStyle(Qt.DashLine)
        p.setPen(cap_pen)
        p.drawLine(QPointF(plot_l, y_of(1.0)), QPointF(plot_r, y_of(1.0)))
        scale_labels.append((y_of(1.0), f"+{data.scale_s:.2f} s", grid_font,
                             theme.C.text_dim if clipped_any else theme.C.text_muted))
        step = _nice_step(data.scale_s)
        k = 1
        while k * step < data.scale_s * 0.9:
            grid_ys.append(gap_y(k * step))
            scale_labels.append((grid_ys[-1], f"+{k * step:g} s", grid_font, theme.C.text_muted))
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
                 TONE_BEHIND: QColor(theme.C.text_muted), TONE_QUICKER: QColor(theme.C.text_muted)}
    for i, b in enumerate(data.bars):
        cx = centres[i]
        if b.kind == KIND_EXCLUDED:
            r = max(7.0, min(bar_w / 2 + 2, 12.0))
            _paint_excluded(p, cx, base - r - 3, 2 * r)
            continue
        fill = tone_fill.get(b.tone, tone_fill[TONE_BEHIND])
        if b.tone == TONE_BEST:
            continue   # its tick and ★ go on last, above everything
        if b.tone == TONE_QUICKER:   # hangs BELOW the zero line: quicker than the best, uncounted
            rect = QRectF(cx - bar_w / 2, base + 2, bar_w, 12)
            _paint_hatched(p, rect, fill)
            continue
        top = min(y_of(b.height), base - MIN_DRAW_PX)
        rect = QRectF(cx - bar_w / 2, top, bar_w, base - top)
        if b.kind in (KIND_DROPOUT, KIND_OFF_LINE, KIND_GAP):
            _paint_hatched(p, rect, fill)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(fill)
            radius = min(4.0, bar_w / 4, (base - top) / 2)
            p.drawRoundedRect(rect, radius, radius)
        if b.clipped:   # the table's break mark: two canvas cuts across the bar's top
            p.setPen(QPen(QColor(theme.C.canvas), 3.0))
            for dy in (14, 22):
                p.drawLine(QPointF(cx - bar_w / 2 - 1, top + dy + 4),
                           QPointF(cx + bar_w / 2 + 1, top + dy - 4))

    # the zero line, drawn over the bars' feet
    p.setPen(grid_pen)
    p.drawLine(QPointF(plot_l, base + 0.5), QPointF(plot_r, base + 0.5))

    # --- the best lap: a tick on the zero line and a ★ over it; its time in a call-out above the
    # tallest bar beneath (or clear above the plot), joined by a leader
    best_i = next((i for i, b in enumerate(data.bars) if b.tone == TONE_BEST), None)
    callout_rect = None
    if best_i is not None:
        bx = centres[best_i]
        tick_w = max(bar_w + 8, 14.0)
        p.setPen(QPen(QColor(best_hue), 4.0, Qt.SolidLine, Qt.RoundCap))
        p.drawLine(QPointF(bx - tick_w / 2, base - 1), QPointF(bx + tick_w / 2, base - 1))
        star_font = _font(26, theme.W_SEMIBOLD)
        p.setFont(star_font)
        sw = p.fontMetrics().horizontalAdvance("★")
        star_base = base - 10
        _draw_text(p, int(bx - sw / 2), int(star_base), "★", star_font, best_hue)
        callout = f"★ {data.best_time}"
        cfont = _font(28, theme.W_SEMIBOLD)
        p.setFont(cfont)
        cw = p.fontMetrics().horizontalAdvance(callout)
        cx = min(max(bx, plot_l + cw / 2), plot_r - cw / 2)
        under = [data.bars[j].height for j, c in enumerate(centres)
                 if abs(c - cx) <= cw / 2 + slot / 2]
        # never lower than clear above the ★ on the base (a tie or level laps under it are short)
        y_text = min(y_of(max(under)) - 18, star_base - 64)
        if y_text < PLOT_TOP + 36:   # it would sit on the cap line: lift it clear above the plot
            y_text = PLOT_TOP - 16
        # ...and never ON the median line: lift it above the line if the two would meet
        if median_y is not None and y_text - 28 <= median_y + 6 and y_text + 8 >= median_y - 6:
            y_text = median_y - 14
        callout_rect = QRectF(cx - cw / 2 - 8, y_text - 30, cw + 16, 40)
        p.setPen(QPen(QColor(best_hue), 1.5))
        if star_base - 28 - y_text > 20:
            p.drawLine(QPointF(bx, y_text + 8), QPointF(bx, star_base - 28))
        _draw_text(p, int(cx - cw / 2), int(y_text), callout, cfont, best_hue)

    # the median line, broken around the call-out so it never strikes the best lap's time through
    if median_y is not None:
        pen = QPen(QColor(theme.C.text), 1.5)
        pen.setStyle(Qt.DashLine)
        p.setPen(pen)
        spans = [(plot_l, plot_r)]
        if callout_rect is not None and callout_rect.top() <= median_y <= callout_rect.bottom():
            spans = [(plot_l, callout_rect.left()), (callout_rect.right(), plot_r)]
        for a, b2 in spans:
            if b2 > a:
                p.drawLine(QPointF(a, median_y), QPointF(b2, median_y))

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
        best = b.tone == TONE_BEST
        _draw_text(p, int(left), int(base) + 38, b.label,
                   _font(24, theme.W_SEMIBOLD if best else theme.W_REGULAR),
                   best_hue if best else theme.C.text_dim)
    _draw_text(p, 0, int(base) + 38, "lap", afont, theme.C.text_muted, align_right_at=RIGHT)

    # --- legend (legend_plan: fitted, never past the footer)
    _paint_legend(p, plan, tone_fill, best_hue)

    # --- footer: the lap card's wordmark (the lowercase logotype, set as a graphic: the WORDMARK
    # form of the name convention in studio/__init__.py) and its right-hand line
    mark_font = _font(40, theme.W_SEMIBOLD)
    wordmark = "pacer"
    _draw_text(p, PAD, FOOTER_Y, wordmark, mark_font, theme.C.accent)
    p.setFont(mark_font)
    foot_avail = RIGHT - PAD - p.fontMetrics().horizontalAdvance(wordmark) - 24
    foot, foot_font = _fit_line(p, data.provenance or "race telemetry", foot_avail, (28, 24, 22))
    _draw_text(p, 0, FOOTER_Y, foot, foot_font, theme.C.text_muted, align_right_at=RIGHT)
    p.end()
    return img


def _paint_hatched(p: QPainter, rect: QRectF, colour: QColor) -> None:
    """A GPS-dropout mark: a ~35 % fill under the hatch, so a thin bar at 60+ laps still reads as
    a bar, and the hatch says its time is less reliable."""
    under = QColor(colour)
    under.setAlphaF(0.35)
    p.setPen(Qt.NoPen)
    p.setBrush(under)
    p.drawRect(rect)
    p.setPen(QPen(colour, 1.5))
    p.setBrush(QBrush(colour, Qt.BDiagPattern))
    p.drawRect(rect.adjusted(0.75, 0.75, -0.75, -0.75))


def _paint_excluded(p: QPainter, cx: float, cy: float, d: float) -> None:
    """A slot's ⊘: a ring with a slash on a canvas disc (so it reads over the neighbouring bars'
    feet), muted — a lap that happened and was left out."""
    r = max(4.0, d / 2)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(theme.C.canvas))
    p.drawEllipse(QPointF(cx, cy), r + 2, r + 2)
    p.setPen(QPen(QColor(theme.C.text_dim), 2.0))
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(cx, cy), r, r)
    k = r * math.sqrt(0.5)
    p.drawLine(QPointF(cx - k, cy + k), QPointF(cx + k, cy - k))


def _paint_legend(p: QPainter, plan: LegendPlan, tone_fill: dict, best_hue: str) -> None:
    for it in plan.items:
        font = _font(it.px)
        if it.style == "too_brief":
            _draw_text(p, int(it.x), int(it.y), it.text, font, theme.C.text_muted)
            continue
        box = QRectF(it.x, it.y - _SW + 2, _SW, _SW)
        if it.style == KIND_EXCLUDED:
            _paint_excluded(p, box.center().x(), box.center().y(), _SW * 0.9)
        elif it.style in (KIND_DROPOUT, KIND_OFF_LINE, KIND_GAP, TONE_QUICKER):
            _paint_hatched(p, box, tone_fill[TONE_BEHIND])
        elif it.style == "clipped":
            p.setPen(Qt.NoPen)
            p.setBrush(tone_fill[TONE_BEHIND])
            p.drawRect(box)
            p.setPen(QPen(QColor(theme.C.canvas), 3.0))
            p.drawLine(QPointF(box.left() - 1, box.center().y() + 4),
                       QPointF(box.right() + 1, box.center().y() - 4))
        elif it.style == TONE_BEST:
            p.setPen(QPen(QColor(best_hue), 4.0, Qt.SolidLine, Qt.RoundCap))
            p.drawLine(QPointF(box.left() + 1, box.bottom() - 2),
                       QPointF(box.right() - 1, box.bottom() - 2))
            _draw_text(p, int(box.left() + 1), int(box.bottom() - 6), "★", _font(16), best_hue)
        else:
            p.setPen(Qt.NoPen)
            p.setBrush(tone_fill[it.style])
            p.drawRoundedRect(box, 3, 3)
        _draw_text(p, int(it.x + _SW + _SW_GAP), int(it.y), it.text, font, theme.C.text_dim)


def lapstrip_to_png(image: QImage) -> bytes:
    """In-memory PNG bytes (the lap card's encoder)."""
    return share_card.card_to_png(image)


def save_lapstrip_png(image: QImage, path: str) -> None:
    """Write exactly `path`; OSError when Qt cannot (QImage.save returns False, never raises)."""
    if not image.save(path, "PNG"):
        raise OSError(f"could not write {path}")
