"""CORNER CARDS: as the kart leaves each corner, a card for two seconds — "C2 · min 41.3 km/h ·
+0.12 s vs best" — near the bottom-left of a single-lap export.

THE NUMBERS ARE THE CORNERS TABLE'S, read from the SAME source it reads and never recomputed:
`session.corners.lap_corner_stats(lap)` gives each corner's minimum speed (`apex_speed`, the
table's Apex column) and its time against the Δ baseline (`delta`, the table's Δbest / Δref
column: the Δ against the SAME CORNER ON THE BEST LAP, not the session-best corner time the ★
marks), printed by the same `fmt_signed(·, 2)` — so the card's time IS that cell's text — and the
minimum speed printed as the table prints its Apex cell (one decimal, the export's km/h or mph).
tests/test_export_corner_cards.py holds both strings equal to the table's.

THE RULES (`card_rows`, the one function the render and the tests read):
  * a corner NOT MATCHED ON TRACK for this lap (`lap_corner_resolved` False: an edge was
    interpolated) gets NO card — its time is the error a minimum over interpolated windows selects
    for, and the table mutes it for the same reason;
  * inside timing precision (`library.within_timing_precision`, |Δ| < 0.07 s — the app's one rule
    for comparing two timed spans), decided on the PRINTED Δ as the compare seam and the Laps
    table's Δbest decide (a raw 0.0674 prints "+0.07", and the table's cell must not read "+0.07"
    in the loss colour beside a card saying level), the card reads "≈ best" in the neutral
    white, no sign; beyond it, the signed time in the export's gain/loss colours for its palette.
    Not the Stats ▼ step: the design lane measured that 95 % of cards would then read "≈";
  * on the Δ BASELINE lap itself (the best lap; or the reference lap, when a reference loaded from
    this same recording is the baseline) every Δ is a self-zero: the card carries the minimum
    speed only — the table dashes the same cells;
  * with a cross-recording REFERENCE loaded, the table's column is "Δref" against that lap, so the
    card says "vs ref" (and "≈ ref") — never "vs best", which would then be false — and its level
    step is the table's Δref step, `stats.corner_delta_step(True)` (one lap's corner time against
    one lap of another recording reads up to that far apart with nothing changed,
    cool/reference-lap-corner-floor) — at the one reference, `library.REFERENCE_RECORDING`, that is
    the timing precision step too (cool/one-reference-level);
  * under PROVISIONAL timing the cards keep their Δ, as the Corners table does there: a corner time
    is measured between two corner edges, not from the start line, and the lap strip burning its
    Δ on the same frame is qualified by the honesty stamp the frame always carries.

TIMING: a card appears at the corner's EXIT — the corner model's own entry time
(`corner_entry_media_time`, the telemetry clock) plus the corner's time, which is exactly the exit
edge the corner's time was measured to — and holds `HOLD_S`, fading in and out over `FADE_S` (a
few frames; opacity only). Cards never overlap: a later exit replaces the card on screen.

PRE-RENDERED: each card is drawn ONCE in `prepare` into a small image, and `paint` only blits the
one on screen (the design measured 6.0 ms per 4K frame drawn live against 0.10 ms blitted). The
look is the Classic plates' own: the readout's pill (its fill, edge and radius), `_font` (Inter,
bold, tabular figures) and the haloed `_draw_text`, the corner id in the strip's amber. Each card's
pill is fitted to its own text (cards never share the screen); they share a left edge, placed for
the widest.

PLACEMENT: left-aligned with the speed readout and just above it, then moved up past any Classic
plate it would touch (the minimap is wide in 9:16); it must land inside the safe area clear of
every plate. Where it cannot, the cards SIT THE EXPORT OUT rather than fail it or burn a card
over another plate: an extra is never worth the whole file, and the finished box says so
(`skipped`, collected by `overlay.build`). A fallback: no frame size the dialog offers reaches it
today (tests/test_export_broadcast.py walks them); QA #565/#558 once failed an export here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFontMetricsF, QImage, QPainter, QPen

from studio import data_quality, library, theme, units
from studio._signal import fmt_signed
from studio.export_palette import EXPORT
from studio.overlay import LAYER_TOP, STAMP, ElementContext
from studio.stats import corner_delta_step

PRECISION_S = library.PB_PRECISION_S   # inside it, "≈ best" (the dialog's tooltip prints it)
HOLD_S = 2.0      # how long a card stays up after its corner's exit
FADE_S = 0.2      # its fade in and out: six frames at 30 fps
# Sizes against the card's own height (= the speed readout's), as the Classic plates size theirs.
_ID_FONT = 0.54   # "C2": the strip's run size, in the strip's amber
_RUN_FONT = 0.50  # the speed and the Δ
_UNIT_FONT = 0.34  # "km/h": the readout's unit label size
_PAD = 0.36       # inner left/right padding
_GAP_K = 12.0     # between two runs, in k units (k = card height / 44, as the plates scale)
_ABOVE_K = 10.0   # the clearance to the readout and every other plate, in the frame's k


@dataclass(frozen=True)
class Card:
    """One corner's card: what it says and when. `t_exit` is on the telemetry clock (`vals.tt`)."""
    cid: int
    t_exit: float
    label: str                # "C2"
    speed: str                # "41.3" (the export's unit, as the table's Apex cell prints it)
    unit: str                 # "km/h" / "mph"
    delta: str | None         # "+0.12 s vs best" / "≈ best"; None on the baseline lap
    colour: str | None        # the delta run's colour


def _printed(d: float) -> float:
    """The Δ as the card and the Corners table print it (`fmt_signed(d, 2)`, whose minus is U+2212),
    read back: the value the precision gate decides on."""
    return float(fmt_signed(d, 2).replace("−", "-"))


def _lap_name(lap_id: int) -> str:
    return f"Lap {lap_id + 1}"


def baseline_word(session) -> str:
    """What the Corners table's Δ column is against: "ref" with a cross-recording reference
    loaded (the table's "Δref"), else "best" (its "Δbest")."""
    has_reference = getattr(session, "has_reference", None)
    return "ref" if callable(has_reference) and has_reference() else "best"


def level_step(session) -> float:
    """The step under which a card reads "≈ best" / "≈ ref": the Corners table's own
    (`stats.corner_delta_step`) — the timing step against a lap of this recording; with a
    reference from ANOTHER recording loaded, the reference-lap corner step or the timing step,
    whichever is wider."""
    own = getattr(session, "reference_is_own_recording", None)
    other = baseline_word(session) == "ref" and not (callable(own) and own())
    return corner_delta_step(other)


def is_baseline_lap(session, lap_id: int, stats) -> bool:
    """True when `lap_id` IS the Δ baseline, so every Δ is a self-zero — the Corners table's own
    test (`CornerTable._shows_the_baseline`): all deltas exactly zero AND the lap is the reference
    lap (a reference from this same recording) or, with no reference, the best lap."""
    if not stats or not all(st.delta == 0.0 and st.apex_speed_delta == 0.0 for st in stats):
        return False
    if baseline_word(session) == "ref":
        own = getattr(session, "reference_is_own_recording", None)
        ref_lap = getattr(session, "reference_lap_id", None)
        return bool(callable(own) and own() and callable(ref_lap) and ref_lap() == lap_id)
    return lap_id == session.best_lap_id()


def card_rows(session, lap_id: int, unit: str | None, palette: str | None) -> list[Card]:
    """Every card `lap_id`'s export will show, in exit order (see the module doc for the rules)."""
    cm = session.corners
    stats = cm.lap_corner_stats(lap_id)
    resolved = cm.lap_corner_resolved(lap_id)
    if not stats:
        return []
    word = baseline_word(session)
    baseline = is_baseline_lap(session, lap_id, stats)
    step = level_step(session)
    # A ⚠ lap (kept out of the bests) is no gain or loss against the best: its gaps print neutral,
    # as the strip's and the bar's do (export_video._frame_strip_runs).
    uncounted = lap_id in data_quality.not_counted_ids(session)
    # Deferred, like every export_video read here: export_video imports this package.
    from studio import export_video as ev
    out = []
    for i, st in enumerate(stats):
        if i >= len(resolved) or not resolved[i]:
            continue                          # not matched on track: no card
        entry = cm.corner_entry_media_time(lap_id, st.cid)
        if entry is None:
            continue
        if baseline:
            delta, colour = None, None
        elif abs(_printed(st.delta)) < step:
            delta, colour = f"≈ {word}", EXPORT.neutral
        else:
            delta = f"{fmt_signed(st.delta, 2)} s vs {word}"
            colour = EXPORT.neutral if uncounted else ev.export_delta_colour(st.delta, palette)
        out.append(Card(cid=st.cid, t_exit=float(entry) + float(st.time), label=f"C{st.cid}",
                        speed=f"{units.convert_speed(st.apex_speed, unit):.1f}",
                        unit=units.speed_label(unit), delta=delta, colour=colour))
    out.sort(key=lambda c: c.t_exit)
    return out


def unavailable_reason(session, lap_id: int | None, follow_laps: bool = False) -> str | None:
    """Why `lap_id`'s export can show no corner card, as a sentence for the dialog — or None when
    it can. `lap_id` None asks only the session-wide rules (the All-laps scope)."""
    if follow_laps:
        return "The full-session export follows every lap; corner cards need one lap."
    corners = getattr(session, "corners", None)
    try:
        has_corners = bool(corners.corner_list()) if corners is not None else False
    except Exception:  # noqa: BLE001 — a model that cannot answer has no corners to show
        has_corners = False
    if not has_corners:
        return "No corners were detected in this session."
    if lap_id is None:
        return None
    if not any(corners.lap_corner_resolved(int(lap_id)) or ()):
        return f"No corner of {_lap_name(int(lap_id))} was matched on track."
    return None


def _runs(card: Card) -> list[tuple[str, float, str]]:
    """The card's text runs left to right: (text, font size as a fraction of height, colour)."""
    runs = [(card.label, _ID_FONT, EXPORT.accent), (f"min {card.speed}", _RUN_FONT, EXPORT.text),
            (card.unit, _UNIT_FONT, EXPORT.text_dim)]
    if card.delta is not None:
        runs += [("·", _RUN_FONT, EXPORT.text_dim), (card.delta, _RUN_FONT, card.colour)]
    return runs


def card_width(card: Card, h: float) -> float:
    """The plate width `card` needs at height `h`: its runs, their gaps and the padding."""
    from studio import export_video as ev
    k = h / 44.0
    w = sum(QFontMetricsF(ev._font(h * f, bold=True)).horizontalAdvance(t)
            for t, f, _c in _runs(card))
    gaps = len(_runs(card)) - 1
    # The unit sits closer to its number, as on the readout.
    return 2 * _PAD * h + w + (gaps - 1) * _GAP_K * k + 4.0 * k


def render_card(card: Card, w: float, h: float) -> QImage:
    """`card` drawn once on its plate: the readout's pill (fill, edge, radius) in a transparent
    image one pixel bigger all round than the plate, so the edge's antialiasing is kept."""
    from studio import export_video as ev
    k = h / 44.0
    img = QImage(int(math.ceil(w)) + 2, int(math.ceil(h)) + 2, QImage.Format_ARGB32_Premultiplied)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)
    box = QRectF(1.0, 1.0, w, h)
    p.setBrush(theme.qcolor(EXPORT.halo, 165))
    p.setPen(QPen(theme.qcolor(EXPORT.text, 55), 1.0 * k))
    p.drawRoundedRect(box, 9 * k, 9 * k)
    # One shared baseline: the runs differ in size, and a centred run per size would step.
    fm = QFontMetricsF(ev._font(h * _RUN_FONT, bold=True))
    base_y = box.y() + (h + fm.ascent() - fm.descent()) / 2.0
    x = box.x() + _PAD * h
    for i, (text, frac, colour) in enumerate(_runs(card)):
        font = ev._font(h * frac, bold=True)
        ev._draw_text(p, QPointF(x, base_y), text, font, colour,
                      halo=(2.2 if frac >= _RUN_FONT else 1.8) * k)
        x += QFontMetricsF(font).horizontalAdvance(text)
        x += 4.0 * k if i == 1 else _GAP_K * k      # "min 41" hugs its "km/h"
    p.end()
    return img


def place(size_w: float, size_h: float, plates, safe: QRectF, k: float) -> QRectF:
    """The card's rect: left-aligned with the readout and just above it, moved up past any plate
    it would touch. Raises when no such place is inside the safe area."""
    gap = _ABOVE_K * k
    readout = plates["readout"]
    r = QRectF(readout.x(), readout.y() - gap - size_h, size_w, size_h)
    for _ in range(len(plates) + 1):
        hit = [q for q in plates.values() if q.intersects(r.adjusted(-gap, -gap, gap, gap))]
        if not hit:
            break
        r.moveTop(min(q.y() for q in hit) - gap - size_h)
    clear = not any(q.intersects(r) for q in plates.values())
    if not (clear and safe.contains(r)):
        raise ValueError(f"no room for a {size_w:.0f}x{size_h:.0f} card clear of the overlay's "
                         f"plates in this frame")
    return r


SKIPPED_TOO_SMALL = "Corner cards did not fit this frame size, so this export has none."
# A sector's pass clip (export_video.PassSubject with a stats.sector_key) keeps to its own stretch
# and its own time, as a corner's keeps to its own card: the checkbox is off with this said.
SECTOR_PASS_REASON = "A sector clip shows the sector's time; corner cards are for a corner's pass."


class CornerCardsElement:
    name = "corner_cards"
    layer = LAYER_TOP
    skipped: str | None = None

    def prepare(self, ctx: ElementContext) -> bool:
        spec = ctx.spec
        if spec.follow_laps or spec.lap_id is None:
            return False
        cfg = spec.config
        self.cards = card_rows(ctx.session, int(spec.lap_id), cfg.speed_unit, cfg.palette)
        # A pass clip (export_video.PassSubject) shows its own corner's card only: the previous
        # corner's exit is at or before the clip's first frame, and its card would open the clip.
        # A sector's pass (a negative key) matches no card: SECTOR_PASS_REASON.
        subject = getattr(spec, "pass_subject", None)
        if subject is not None:
            self.cards = [c for c in self.cards if c.cid == subject.cid]
        if not self.cards:
            return False
        h = float(ctx.plates["readout"].height())
        w = max(card_width(c, h) for c in self.cards)
        avoid = {n: q for n, q in ctx.plates.items() if n in ("gmeter", "map", "readout", "strip",
                                                              STAMP)}
        try:
            self.rect = place(w, h, avoid, ctx.safe, ctx.k)
        except ValueError:
            self.skipped = SKIPPED_TOO_SMALL
            return False
        # Whole pixels, so the blit is a straight copy (no resampling between frames); rounded UP,
        # so the plate (one pixel in from the image's edge) never leaves the placed rect's corner.
        self._at = QPointF(math.ceil(self.rect.x()) - 1.0, math.ceil(self.rect.y()) - 1.0)
        self.images = [render_card(c, card_width(c, h), h) for c in self.cards]
        self._exits = [c.t_exit for c in self.cards]
        return True

    def active(self, tt: float | None) -> tuple[int, float] | None:
        """(index of the card on screen, its opacity) at telemetry time `tt`, or None. The latest
        exit at or before `tt` wins, so a later card replaces an earlier one."""
        if tt is None:
            return None
        i = None
        for j, t in enumerate(self._exits):
            if t <= tt:
                i = j
            else:
                break
        if i is None:
            return None
        age = tt - self._exits[i]
        if age >= HOLD_S:
            return None
        return i, max(0.0, min(1.0, age / FADE_S, (HOLD_S - age) / FADE_S))

    def paint(self, p: QPainter, vals) -> None:
        got = self.active(vals.tt)
        if got is None:
            return
        i, alpha = got
        if alpha <= 0.0:
            return
        p.setOpacity(alpha)
        p.drawImage(self._at, self.images[i])
