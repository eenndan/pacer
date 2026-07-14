"""Shareable lap card (image): the zero-hardware viral output.

A single portrait PNG a kart racer posts after a good session — the one social artifact no
competitor (RaceChrono / TrackAddict / AiM / Garmin) ships. It composes the session's headline
numbers into a tidy, palette- and unit-honouring card: track + date, the best lap (hero), the
Δ-to-ideal (the honest per-point envelope, NOT a drivable lap), the #1 coaching opportunity, a
speed-coloured map thumbnail, and a subtle "pacer" wordmark.

TWO LAYERS, deliberately split so the numbers are testable without Qt:

  * ``card_data(session, *, unit)`` — a PURE function reading ONLY Session accessors (no Qt, no
    new analysis). Returns ``CardData`` with everything the card shows plus the HONESTY verdict
    (``blocked`` / ``stamp``): a card must NEVER present provisional-start-line or data-quality-
    degraded timing as a verified brag. app.py greys the actions out when ``blocked`` and stamps
    the render when ``stamp`` is set — this module decides, the views obey.
  * ``render_card(data, map_png, *, palette)`` — the Qt composition (QImage + QPainter). It takes
    the map thumbnail as PNG BYTES (app.py grabs the live MapView widget via the SAME
    QWidget.grab → QImage → PNG path the HTML report uses), so the pure layer stays Qt-free and
    the render never reinvents map rendering.

Honesty rules (single-sourced here so both the menu action and the toast obey them):
  * PROVISIONAL start line (``not session.timing_verified``) OR no valid best lap ⇒ ``blocked``
    (no shareable card — an unverified lap time is not a brag).
  * DATA-QUALITY DEGRADED timing (``session.timing_quality.degraded``, e.g. media-clock drift /
    low GPS) ⇒ a card, but STAMPED "estimated timing" so the number is never passed off as exact.

Palette + units: the Δ-to-ideal and the opportunity colour route through ``theme``'s palette
accessors (so the colour-blind option recolours the card too); the apex-speed deficit in the
reason sentence and any speed reads honour the active km/h ↔ mph unit.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap

from . import coaching, theme, units
from ._signal import fmt_time

# The card is a portrait-ish social image. 1080×1350 is Instagram's 4:5 portrait — the most
# forgiving crop across feeds/stories/chat, and big enough for a legible hero number.
CARD_W = 1080
CARD_H = 1350

# The honest label for the Δ-to-ideal: the ideal is the per-point lower envelope of the clean
# laps (session.ideal_total), NOT a lap anyone actually drove — kept consistent with the plots'
# "SYNTHETIC theoretical ideal … Not a single drivable lap" wording (D1/#53).
IDEAL_LABEL = "vs your ideal lap"
IDEAL_SUBLABEL = "synthetic best-at-each-point — not a single drivable lap"


def hero_delta_line(gap: float) -> str:
    """The Δ-to-ideal hero line, one coherent voice across both branches.

    A positive gap reads as time still on the table ("+0.31 s vs your ideal lap"); a gap at (or
    below) the even-epsilon means the best lap already sits ON the synthetic per-point envelope, so
    it reads plainly as "level with your ideal lap" — never the doubled "on your ideal lap vs your
    ideal lap" template bug the two-branch string concat used to produce."""
    if gap > theme.DELTA_EVEN_EPS_S:
        return f"+{gap:.2f} s {IDEAL_LABEL}"
    return "level with your ideal lap"


@dataclass(frozen=True)
class TopOpp:
    """The #1 coaching opportunity as the card shows it (already resolved to display strings)."""

    corner_label: str    # "C4 ⟳" — corner id + turn-direction glyph
    time_lost_s: float   # median s lost vs the best lap's same corner (> 0)
    reason: str          # the human, numbers-only reason sentence (unit-aware, ESTIMATED-safe)


@dataclass(frozen=True)
class CardData:
    """Everything the shareable card renders — pure display values off Session accessors.

    ``blocked`` (no shareable card) and ``stamp`` (an honesty stamp to burn on) carry the trust
    verdict so the menu action and the render read ONE decision. When ``blocked`` is True the
    other fields may be placeholders (the card is never built)."""

    track: str            # the track name, or "Unknown track"
    date: str             # "YYYY-MM-DD" or "" (GPS5 stream / empty session)
    best_time: str        # the best lap, m:ss.mmm (or "—")
    best_lap_id: int | None
    delta_to_ideal_s: float | None  # best_time − ideal_total ≥ 0 (how far off the envelope), or None
    unit: str             # the active speed unit id (km/h default) — for any speed reads
    top_opp: TopOpp | None          # the #1 opportunity, or None (< MIN_LAPS clean laps / none losing)
    blocked: bool         # True ⇒ do NOT render a card (provisional / no valid lap)
    stamp: str            # "" or an honesty stamp to burn on the card ("estimated timing")


def _top_opportunity(session, unit: str) -> TopOpp | None:
    """The single biggest coaching opportunity as a display row, or None when the session has too
    few clean laps or no corner is losing time. Reuses the CANONICAL coaching model + sentence
    (no new analysis) and the lap table's direction glyph, so the card can't drift from the panel.
    Fully guarded — a card must never be broken by a coaching hiccup."""
    try:
        from .lap_table import CORNER_DIR_GLYPH
        opps = session.coaching_opportunities()
        if not (opps.enough and opps.rows):
            return None
        opp = opps.rows[0]  # rows are ranked biggest-loss first
        glyph = CORNER_DIR_GLYPH.get(opp.direction, "")
        label = f"C{opp.cid} {glyph}".strip()
        return TopOpp(corner_label=label, time_lost_s=float(opp.time_lost),
                      reason=coaching.reason_sentence(opp, unit))
    except Exception:  # noqa: BLE001 — the card degrades to "no opportunity", never crashes
        return None


def card_data(session, *, unit: str | None = None) -> CardData:
    """Assemble the card's display values from Session accessors ONLY (pure; no Qt, no new math).

    Honesty verdict (see the module doc): ``blocked`` when the timing is PROVISIONAL (unverified
    start line) or there is no valid best lap — an unverified lap time is not a brag. ``stamp`` is
    set (but the card still renders) when the timing is data-quality DEGRADED, so the number is
    shown honestly as estimated. The Δ-to-ideal is ``best_time − ideal_total`` (≥ 0, how far the
    best lap is off the synthetic per-point envelope), labelled honestly by the caller."""
    unit = units.normalize_unit(unit)
    track = (session.track_name or "Unknown track")

    best_id = session.best_lap_id()
    best_time = fmt_time(session.lap_time(best_id)) if best_id is not None else "—"

    # Δ-to-ideal: how far the best lap is off the synthetic lower-envelope ("ideal") lap. Positive
    # (the best lap can't beat the envelope it helped form). None when no ideal can be built.
    delta_ideal = None
    ideal_total = session.ideal_total()
    if best_id is not None and ideal_total is not None:
        gap = float(session.lap_time(best_id)) - float(ideal_total)
        delta_ideal = gap if gap > 0 else 0.0

    # HONESTY: an unverified (provisional) start line or no valid lap ⇒ no shareable card.
    provisional = not session.timing_verified
    blocked = provisional or best_id is None
    # A data-quality-degraded (media-clock drift / low-GPS) time still renders, but stamped.
    stamp = "estimated timing" if session.timing_quality.degraded else ""

    return CardData(
        track=track,
        date=session.session_date() or "",
        best_time=best_time,
        best_lap_id=best_id,
        delta_to_ideal_s=delta_ideal,
        unit=unit,
        top_opp=_top_opportunity(session, unit),
        blocked=blocked,
        stamp=stamp,
    )


# --------------------------------------------------------------------------- Qt rendering
def _font(size: int, weight: QFont.Weight = theme.W_REGULAR) -> QFont:
    """A card font at an explicit PIXEL size (the card is a fixed-pixel canvas, so it must not
    scale with the screen's point DPI). Uses the theme's Inter face + fallback stack."""
    f = theme.ui_font(size, weight)
    f.setPixelSize(size)
    return f


# Title auto-fit: try these pixel sizes (biggest first) before falling back to eliding the smallest.
_TITLE_PX_STEPS = (58, 50, 44)

# Map plate: the thumbnail scales to this width; the plate's height then hugs the scaled thumbnail
# (L5 — a wide landscape grab no longer letterboxes into a fixed-tall plate), within these bounds.
MAP_PLATE_W = CARD_W - 2 * 72     # plate width == the card content width (pad = 72)
MAP_PLATE_H_MAX = 512
MAP_PLATE_H_MIN = 300
MAP_PLATE_INNER = 24              # inner margin between the thumbnail and the plate edge


def map_plate_height(thumb_w: int, thumb_h: int) -> int:
    """L5: the map plate's height for a thumbnail of native size (thumb_w × thumb_h). The thumbnail
    is fit to the plate WIDTH (aspect-preserved); the plate is then made just tall enough to hug it
    (plus the inner margin), clamped to [MIN, MAX]. A wide landscape map → a short plate (little dead
    space); a near-square/portrait map → the full-height plate. Pure, so the geometry is testable."""
    if thumb_w <= 0 or thumb_h <= 0:
        return MAP_PLATE_H_MAX
    avail_w = MAP_PLATE_W - MAP_PLATE_INNER
    scaled_h = thumb_h * (avail_w / thumb_w)
    scaled_h = min(scaled_h, MAP_PLATE_H_MAX - MAP_PLATE_INNER)  # never taller than the max plate
    return int(min(MAP_PLATE_H_MAX, max(MAP_PLATE_H_MIN, scaled_h + MAP_PLATE_INNER)))


def _fit_title(p: QPainter, text: str, avail: int) -> tuple[str, QFont]:
    """Fit the track title into `avail` px of header width (M10). Try progressively smaller title
    fonts; the first that fits whole wins (a moderately long name just shrinks). If even the
    smallest still overruns, elide-right at that size so the tail is cut with an ellipsis instead of
    smearing off the edge / into the stamp. Short names take the first (biggest) size unchanged."""
    if avail <= 0:
        avail = 1
    font = _font(_TITLE_PX_STEPS[-1], theme.W_SEMIBOLD)
    for px in _TITLE_PX_STEPS:
        font = _font(px, theme.W_SEMIBOLD)
        p.setFont(font)
        if p.fontMetrics().horizontalAdvance(text) <= avail:
            return text, font
    # Still too wide at the smallest step -> elide at that size.
    p.setFont(font)
    return p.fontMetrics().elidedText(text, Qt.ElideRight, avail), font


def _draw_text(p: QPainter, x: int, y: int, text: str, font: QFont, colour: str,
               *, align_right_at: int | None = None) -> None:
    """Draw one baseline-anchored line; when ``align_right_at`` is given the text is right-aligned
    to that x (for the value column). ``y`` is the text baseline."""
    p.setFont(font)
    p.setPen(QColor(colour))
    if align_right_at is not None:
        fm = p.fontMetrics()
        x = align_right_at - fm.horizontalAdvance(text)
    p.drawText(x, y, text)


def render_card(data: CardData, map_png: bytes | None = None, *,
                palette: str | None = None) -> QImage:
    """Render the shareable lap card to a ``CARD_W×CARD_H`` ARGB QImage (never rendered when
    ``data.blocked`` — the caller guards that). ``map_png`` is the live MapView grabbed to PNG
    bytes (app.py's existing widget→PNG path); it is composited as the speed-coloured hero
    thumbnail (skipped cleanly when absent). ``palette`` selects the semantic hues so the card
    matches the app's active (incl. colour-blind) palette; restored on exit."""
    prev_palette = theme.active_palette()
    if palette is not None:
        theme.set_palette(palette)
    try:
        return _paint(data, map_png)
    finally:
        theme.set_palette(prev_palette)


def _paint(data: CardData, map_png: bytes | None) -> QImage:
    img = QImage(CARD_W, CARD_H, QImage.Format_ARGB32)
    img.fill(QColor(theme.C.canvas))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.setRenderHint(QPainter.TextAntialiasing, True)

    pad = 72
    right = CARD_W - pad

    # --- honesty stamp (data-quality degraded): a small "estimated timing (est)" chip up top so
    # the number is never passed off as exact. Palette-neutral amber, right-aligned in the header.
    # Drawn BEFORE the title so we can measure it and reserve its width when eliding the title.
    stamp_w = 0
    if data.stamp:
        stamp_txt = f"{data.stamp} {theme.ESTIMATED_MARK}"
        stamp_font = _font(26, theme.W_SEMIBOLD)
        p.setFont(stamp_font)
        stamp_w = p.fontMetrics().horizontalAdvance(stamp_txt)
        _draw_text(p, 0, 128, stamp_txt, stamp_font, theme.C.accent, align_right_at=right)

    # --- header: track + date ---
    # M10: fit a long user-typed track name to the width left of the stamp (a ~24px gap keeps it from
    # touching the stamp), auto-shrinking the 58px font a step or two first so a moderately long name
    # still reads big before it has to be cut, then eliding what still overruns. `_save_as_track`
    # accepts any length, so the flagship short name is unchanged while "Silverstone International
    # Circuit Grand Prix Layout" no longer smears into the stamp.
    title_avail = (right - (stamp_w + 24) if data.stamp else right) - pad
    title, title_font = _fit_title(p, data.track, title_avail)
    _draw_text(p, pad, 128, title, title_font, theme.C.text)
    if data.date:
        _draw_text(p, pad, 176, data.date, _font(30), theme.C.text_dim)

    # --- hero: the best lap, big ---
    _draw_text(p, pad, 240, "BEST LAP", _font(28, theme.W_SEMIBOLD), theme.C.text_muted)
    _draw_text(p, pad, 360, data.best_time, _font(132, theme.W_SEMIBOLD), theme.C.text)

    # --- Δ-to-ideal: how far off the achievable envelope, honestly labelled ---
    if data.delta_to_ideal_s is not None:
        # A gap of 0 reads even (green/ahead), a positive gap reads "time left" (behind hue).
        gap = data.delta_to_ideal_s
        colour = theme.behind_colour() if gap > theme.DELTA_EVEN_EPS_S else theme.ahead_colour()
        gap_txt = hero_delta_line(gap)
        _draw_text(p, pad, 424, gap_txt, _font(38, theme.W_SEMIBOLD), colour)
        _draw_text(p, pad, 460, IDEAL_SUBLABEL, _font(24), theme.C.text_muted)

    # --- map thumbnail (speed-coloured): composited from the grabbed MapView PNG ---
    # L5: a wide landscape MapView grab fits the plate by WIDTH, so a fixed-tall (512 px) plate
    # letterboxed the thumbnail with ~40% vertical dead space. Instead size the plate to the scaled
    # thumbnail (map_plate_height): scale to the plate width first, then make the plate exactly as
    # tall as that scaled map needs so the rainbow line fills the plate top-to-bottom.
    map_top = 512
    map_h = MAP_PLATE_H_MAX
    scaled: QPixmap | None = None
    if map_png:
        pm = QPixmap()
        if pm.loadFromData(map_png, "PNG") and not pm.isNull():
            map_h = map_plate_height(pm.width(), pm.height())
            # Fit to the (now plate-hugging) rect, preserving aspect.
            scaled = pm.scaled(MAP_PLATE_W - MAP_PLATE_INNER, map_h - MAP_PLATE_INNER,
                               Qt.KeepAspectRatio, Qt.SmoothTransformation)
    map_rect = QRectF(pad, map_top, MAP_PLATE_W, map_h)
    p.setPen(QColor(theme.C.border))
    p.setBrush(QColor(theme.C.surface))
    p.drawRoundedRect(map_rect, 20, 20)
    if scaled is not None:
        # Centre the (now plate-hugging) thumbnail; still never upscaled past the plate.
        dx = int(map_rect.x() + (map_rect.width() - scaled.width()) / 2)
        dy = int(map_rect.y() + (map_rect.height() - scaled.height()) / 2)
        p.drawPixmap(dx, dy, scaled)

    # --- #1 coaching opportunity (ESTIMATED-safe reason sentence) ---
    opp_top = map_top + map_h + 56
    if data.top_opp is not None:
        opp = data.top_opp
        _draw_text(p, pad, opp_top, "BIGGEST OPPORTUNITY", _font(28, theme.W_SEMIBOLD),
                   theme.C.text_muted)
        _draw_text(p, pad, opp_top + 60, f"{opp.corner_label}", _font(46, theme.W_SEMIBOLD),
                   theme.C.text)
        # the time lost reads in the "behind" hue (time given away)
        _draw_text(p, 0, opp_top + 60, f"+{opp.time_lost_s:.2f} s", _font(46, theme.W_SEMIBOLD),
                   theme.behind_colour(), align_right_at=right)
        _draw_text(p, pad, opp_top + 110, opp.reason, _font(30), theme.C.text_dim)
    else:
        _draw_text(p, pad, opp_top, "Drive a few more clean laps for coaching tips.",
                   _font(28), theme.C.text_muted)

    # --- wordmark (subtle, bottom) ---
    _draw_text(p, pad, CARD_H - 56, "pacer", _font(40, theme.W_SEMIBOLD), theme.C.accent)
    _draw_text(p, 0, CARD_H - 56, "race telemetry", _font(28), theme.C.text_muted,
               align_right_at=right)

    p.end()
    return img


def card_to_png(image: QImage) -> bytes:
    """Encode a rendered card QImage to in-memory PNG bytes (the same QBuffer path app.py's report
    grab uses) — for the File ▸ Export save + tests that assert non-empty output."""
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())
