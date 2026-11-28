"""CentralView: the session-scoped central widget for ONE loaded recording.

Owns the panels — video / map / plots / the TABBED lap panel (Laps · Corners · Stats · Coaching ·
Marks, one QTabBar over one QStackedWidget; every page full-height) / diff_box / chapter banner
— the compare + scrub controllers, the shared PlaybackState and the per-frame ``tick()`` — all built
atomically in ``__init__``. StudioWindow holds one ``self.view`` and ``setCentralWidget()``s a
fresh CentralView per load (the old one disposed + dropped as a unit), so a window reference into
the view can never go stale mid-rebuild. The persistent chrome reaches session-scoped widgets
through ``self.view`` (e.g. ``self.view.video``).

session / _paths split: the window keeps the load orchestration + ``session``/``_paths`` and hands
the loaded ``session`` (plus the recording ``paths`` for the banner and the ``sidecar_path`` for the
timing-line save) into the constructor. The ~30 Hz tick TIMER stays on the window and delegates to
``self.view.tick()``.
"""

from __future__ import annotations

import contextlib
import logging
import math
import os
import time
from typing import NamedTuple

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QCursor,
    QFontMetrics,
    QFontMetricsF,
    QGuiApplication,
    QPainter,
)
from PySide6.QtWidgets import (
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from . import chapters, data_quality, motion, plots_view, sidecar, theme, units
from ._signal import PRINT_DECIMALS, lap_label, plural
from .coaching_panel import OpportunitiesPanel, compare_status
from .compare_controller import CompareController
from .lap_table import (
    CORNERS_VIEW_QUICKEST,
    CornersPage,
    LapTable,
    durations_muted,
    is_selection_click,
)
from .map_view import MapView
from .marks_panel import MarksPanel
from .overlay import broadcast
from .playback_state import PlaybackState
from .plots_view import PlotsView
from .race_controller import RaceController
from .race_ladder import RaceCard
from .scrub_controller import ScrubController
from .session import fmt_time
from .stats import is_level, sector_col, sector_key, with_luck
from .stats_panel import StatsView
from .stats_trust import TIMING_TERM
from .video_view import VideoView
from .widgets import (
    ActionChip,
    PanelHeader,
    PanelToolbar,
    ToggleButton,
    chip,
    icon_button,
    set_tone,
)

# The maximize-button glyphs. DELIBERATELY DISTINCT from the video transport's fullscreen ⤢ button
# (ph.arrows-out / ph.arrows-in — "fill the SCREEN"): the corners glyphs read as "fill this WINDOW
# quadrant", a different action, so the two never collide on the video header. Maximize glyph while
# the panel is in the grid, restore glyph while it's maximized.
# The charts panel's identity, per Δ baseline (see plots_view.deltaBaselineChanged). The label
# names the baseline the LOWER CHART actually draws, which is NOT the hero readout's own reference
# — so it is the item that keeps the panel unambiguous, not decoration.
#
# It used to exist in TWO lengths, because identity and the panel's controls shared one width
# budget and a degradation ladder chose between them at every resize. The controls now live in a
# PanelToolbar of their own, so there is exactly one wording per baseline and it is always painted
# in full: the charts panel's minimum width is set by this label plus the hero readout, and the
# splitter cannot be dragged below it.
#
# QA-W2R-03: there are THREE baselines, not two — with a cross-recording reference loaded this bar
# used to paint "Δ TO BEST" over a curve measured against another recording's lap. "REF" rather
# than the recording's name because THIS LABEL IS NOW PART OF THE COLUMN'S FLOOR: every px it takes
# is a px the user can no longer drag the charts column below. The recording is named in full by
# the label's tooltip, by the chart legend under it and by the permanent status chip.
_PLOTS_LABEL_BEST = "SPEED · Δ TO BEST"
_PLOTS_LABEL_IDEAL = "SPEED · Δ TO IDEAL"
_PLOTS_LABEL_REF = "SPEED · Δ TO REF"
# kind (plots_view.DELTA_BASELINE_*) -> the bar's wording for it.
_PLOTS_BASELINE_LABELS = {
    plots_view.DELTA_BASELINE_BEST: _PLOTS_LABEL_BEST,
    plots_view.DELTA_BASELINE_IDEAL: _PLOTS_LABEL_IDEAL,
    plots_view.DELTA_BASELINE_REFERENCE: _PLOTS_LABEL_REF,
}
_MAXIMIZE_GLYPH = "ph.corners-out"   # expand this panel to fill the window
# What a Coaching row's Compare line ends with: C or Esc leaves it as one video (both laps stay
# on the charts, as C has always left it).
COACHING_COMPARE_KEYS = " · C or Esc: back to one video"


def typical_compare_status(v, label: str) -> str:
    """The status line of "compare this pass with my typical pass": both laps with their own
    printed times, and the gap between those two printed times (`PassVsTypical.gap_s`), quicker or
    slower — never "fastest", and no cause (an apex or exit speed column beside it was refused as
    noise: discovery 6, B (d)). "As timed": the gap is these two passes' measurement, not a verdict
    on the pass — the quickest of many is partly a lucky one (`PassAnchor.held_neutral`)."""
    d = PRINT_DECIMALS
    way = "quicker" if v.gap_s > 0 else "slower"
    return (f"Comparing lap {lap_label(v.lap_id)} ({v.pass_s:.{d}f}) with your typical pass, lap "
            f"{lap_label(v.typical_lap_id)} ({v.typical_pass_s:.{d}f}): {abs(v.gap_s):.{d}f} s "
            f"{way} through {label} as timed · C or Esc: back to lap {lap_label(v.lap_id)}")


# Where the pass being studied was played from (`CentralView.pass_subject`): the corner walk from a
# Quickest passes row steps to the next corner's QUICKEST pass, from anywhere else along the lap.
PASS_FROM_QUICKEST = "quickest"
# A sector pass plays, zooms, loops, walks, exports and compares with its typical pass like a
# corner pass.
# What a held status line depends on (`CentralView._hold_status`): the pass being studied, or a
# compare at one corner.
_HELD_PASS = "pass"
_HELD_CORNER = "corner"
_RESTORE_GLYPH = "ph.corners-in"     # shown while maximized — click/Esc to restore the grid
# The two constants that used to live here (_HDR_ICON_PX 15, _HDR_ICON_BTN 26x24) are gone: an
# icon button's size is theme.ICON_BTN and its glyph theme.ICON_PX, applied by widgets.icon_button.
# The old pair never described what shipped anyway — a stylesheet min-height stood these buttons at
# 26x28, which is neither the value written here nor any size the app declared.

# The longest realistic values the hero #DiffBox is sized for (see _hero_templates / _hero_min_width
# below): two digits of Δ before the point and a three-digit speed.
_HERO_WIDEST_DELTA_S = 88.88
_HERO_WIDEST_SPEED_KMH = 188.0

# WHY THE HERO READOUT NEEDS NO APOLOGY ON THE BEST LAP ANY MORE. A font-size census of every
# visible text-bearing widget at the FIRST PAINTED FRAME of a real three-chapter drop ranks exactly
# ONE surface above 13 px: this readout, 22 px semibold at [830, 537, 391, 30], against 167 surfaces
# at 13 px and 8 at 11. It used to read `Δideal +0.00 s     73 km/h` there and carried a note saying
# why — the ideal was the pointwise minimum of the laps' cumulative-elapsed curves, which at the
# flag is identically the best lap time, so the app's largest number was a structural null in the
# state the app always opens in (D4-08). The ideal is now the corner/straight partition composite,
# which is strictly faster than any single lap, so that note is gone: on the DEFAULT selection (the
# session best) the hero climbs from 0.00 to the whole gap by the flag — 1.37 s on D24 one chapter
# and 1.49 s on three, peaking 0.02 s and 0.01 s above that just before it (+1.14 s on Sandown when
# that was measured, before #300). There is nothing left to excuse.
#
# The OTHER reference still needs its note, and that one is not a defect: Δ-to-best on the best lap
# is exactly 0.000 for the whole lap, because the reference IS the lap. Switching to it is the one
# gesture that makes this readout stop moving, so the note names the action that does work.
#
# What is deliberately NOT done here: swapping the readout for the selected lap's TIME while the
# playhead has not moved. It is not "one branch in the same label": #DiffBox has a single QSS
# `font-size`, and its layout floor (_hero_min_width below) is the label's own sizeHint over the
# formatters' widest plain-text output, so a second type step inside it means rich text, a second
# template set and a re-derived floor.
#
# What IS done, since board review UX-9b, is the same template read at the lap's END while the
# playhead still sits where the poster seek parked it (`_hero_delta_time`): the arrival frame's
# `Δideal +0.00 s` became the lap's total (+0.69 s on SD_30_08), the number the Δ trace climbs to.
# The objection this note used to make — a headline that changes what it MEANS on the first pixel
# of scrub — is answered on the surface that can: the tooltip says it is the whole lap and that it
# follows the playhead from the first move (_HERO_AT_REST_NOTE).
_HERO_AT_REST_NOTE = (
    "\nThis is your whole lap's total: the playhead has not moved yet. Play, scrub or pick a "
    "moment and it follows the playhead.")
# How far the playhead may sit from the poster seek's time and still be "at rest": the player's
# echo of a seek comes back millisecond-quantized; anything a person does moves it further.
HERO_REST_SLACK_S = 0.1
#: How long after an auto-repeated arrow key the hero follows the playhead directly (no roll): the
#: repeat comes every ~33 ms and its seek is applied on the next tick, so 0.25 s covers both.
CONTINUOUS_HOLD_S = 0.25
# VIEW-7 (QA 2026-09-25): at rest the Δ is the WHOLE LAP's total while the speed beside it was the
# lap's START (75 km/h under "Δideal +1.48 s" on MK_18_09) — two instants on one line. At rest the
# speed slot says whose number the Δ is instead; the speed returns with the first move.
HERO_AT_REST_TAG = "whole lap"
# The same slot during a Coaching row's Compare (`_corner_readout`): the Δ is counted from the
# corner's entry, and the slot says so ("Δbest +0.14 s     since C4 entry"), painted as the at-rest
# tag is. In the speed's place so the line keeps the floor `_hero_min_width` sized: measured at the
# hero face, "Δbest −10.14 s ▼     since C12 entry" paints 334 px in the 338 px the 354 px floor
# leaves inside its padding, with the tag at the speed's 15 px (it paints at 13 px).
HERO_CORNER_TAG = "since {corner} entry"


def _is_hero_tag(tail: str) -> bool:
    """Is the hero's tail a label about the Δ (at rest, or a corner compare's) rather than a value?"""
    return tail == HERO_AT_REST_TAG or (tail.startswith("since ") and tail.endswith(" entry"))
_BEST_LAP_BEST_NOTE = (
    "\nThis IS your best lap, so it is the reference this Δ is measured against: it reads exactly "
    "zero for the whole lap. Pick another lap for a number that moves.")

# The `vs ideal` chip's own tooltip, held as a constant so the states that take the chip away can
# APPEND their reason to it and restore it verbatim afterwards — the same contract
# plots_view._set_control_enabled has with the three chart controls.
_IDEAL_CHIP_TIP = (
    "Hero readout reference: ON = Δ to your IDEAL LAP — your quickest time through each "
    "corner and each straight, stitched together into one synthetic lap (not a single lap you "
    "have driven); OFF = Δ to your best single lap. The other number is always in the readout's "
    "tooltip.")
# WHAT THAT IDEAL WAS MINIMISED OVER, appended per session by _sync_ideal_readout.
#
# The hero is the app's largest text and the only ideal surface with no prose beside it, so a
# reader who never opens Stats meets Δideal here and nowhere else. The number is an order
# statistic: on Sandown 3h's three chapters the same driving reads a 0.61 s gap over 5 laps and a
# 1.27 s gap over 62 (random subsets, 20,000 draws per N; corner_model.IdealSample), and the corner
# count it is partitioned by moved 11↔12 with the start/finish line on D24. Stats says so under its
# tiles; here it goes on the chip's hover, which is where the reference is chosen.
#
# APPENDED to _IDEAL_CHIP_TIP, never replacing it — same contract as the two unavailable-state
# reasons below, and tests/test_central_view_realqt.py pins the constant's own honesty words
# ("stitched together", "not a single") on this tooltip.
_IDEAL_CHIP_SAMPLE = (
    "\n\nOn this recording it is stitched from {donors} of your {laps} clean laps across "
    "{corners}. It is a minimum over those laps, so it falls as you record more of them "
    "(0.16–0.74 s per doubling of lap count on the owner's recordings) and moves when dragging "
    "the start/finish line changes which corners Pacer finds. The Stats page prints both counts "
    "under it.")

# THE SIXTH SURFACE. #211 gave the Δ chart, the `Ideal lap` toggle, the Stats IDEAL LAP block, the
# laps.csv trailer, the share card and the Library cell ONE gate — `ideal_donor_lap_id() is not
# None`, i.e. one lap was quickest through every corner and every straight, so the "ideal" IS that
# lap — and left the hero, the largest text in the app, ungated. Measured on the owner's real
# one-valid-lap recording (Sandown chapter 3, where ideal == best == 23.231227933627977 exactly):
# the hero printed `Δideal +0.00 s` for 2 324 of 2 324 samples of the lap, above a panel header
# reading `SPEED · Δ TO BEST`, a greyed-out `Ideal lap` toggle and a Stats page with no IDEAL LAP
# section — while the chip beside it went on describing a lap "stitched together … (not a single
# lap you have driven)", which is the opposite of the truth there. `+0.00` against the ideal is
# the app saying "you are level with your ideal lap": the exact sentence the share card was
# rewritten to stop printing. #211 argued the hero "already explains why on hover"; measured, it
# does not — `_BEST_LAP_BEST_NOTE` is appended only in the UNCHECKED branch and it explains
# Δ-to-BEST being zero, not Δideal.
#
# ...and a fourth state nobody enumerated: no corner partition at all, so there is no composite to
# reference. The hero degraded to `Δideal —` on its own there (honest, but empty) while the chip
# still offered a reference that does not exist. Both states now hand the hero to Δ-to-best, which
# is a number that moves, and say why on the chip and in the hover.
_IDEAL_ONE_LAP_REASON = (
    "Unavailable on this recording: one lap was quickest through every corner and every straight, "
    "so your ideal lap IS that lap and a Δ against it would read zero the whole way round. The "
    "readout is measured against your best lap instead. It comes back as soon as a different lap "
    "is quickest somewhere.")
_IDEAL_NONE_REASON = (
    "Unavailable on this recording: Pacer found no corners here, so there is no corner/straight "
    "partition to stitch an ideal lap from. The readout is measured against your best lap "
    "instead.")
# The same two facts as the hero's own hover — where the alternative would be printing
# `Δideal +0.00 s` in a tooltip, which is the claim the chip was gated for making, one level down.
_IDEAL_HOVER_REASONS = {
    "one_lap": ("No Δ to an ideal lap here: one lap was quickest through every corner and every "
                "straight, so your ideal lap IS that lap."),
    "none": ("No Δ to an ideal lap here: Pacer found no corners on this recording, so there is "
             "nothing to stitch one from."),
}

# THE DISPLAY CLAMP, SAID OUT LOUD ON SCREEN. `theme.format_ideal_run` floors the printed Δideal
# at 0 and the reasoning is written down beside its sweep table — over a segment, and over the
# lap, you cannot be ahead of the ideal, so a two-way ramp would flash the "ahead" hue on the
# app's largest text for a tenth of a second to report something that is not true at any
# granularity the ideal is defined on. But the clamp was stated ONLY in that source comment, and
# the Δ chart 130 px below draws the same quantity UNCLAMPED (deliberately — there a sub-zero
# excursion has a track position to belong to). On the DEFAULT screen those two surfaces can
# disagree: with the best lap selected the chart is baselined to the ideal, and on D24 one chapter
# (GX010062) the best lap's raw value is negative on 258 of 6 878 frames at 10 ms, 190 of them past
# the dead band, drawing the curve 1.4 px below its own y = 0 line (-0.0199 s in a [-0.127, +1.494]
# view over a 114.7 px box) while the hero above it prints `+0.00`. Select any other lap and it is
# the floor table's -0.280 s. (This note first measured it on SD_30_08: 297 of 1 308 frames and
# 8.9 px, which still reproduces on the tree before T13 — but those 1 308 frames were a 13.07 s
# PIECE of a 46 s lap. On its real laps the best lap never goes below zero.) One clause on the hover
# closes that, and it appears exactly when the clamp is doing visible work:
# |raw| > theme.DELTA_EVEN_EPS_S, the same dead band every other Δ surface snaps to zero, so it
# stays silent on the float noise that makes 40.7 % of a one-donor recording's samples "negative" at
# -0.0004 s.
_IDEAL_CLAMPED_NOTE = (
    "\nRight here you are {raw:.2f} s up on the lap that donated this segment — not up on the "
    "ideal, which you cannot be over a whole segment. The readout floors at +0.00; the Δ chart "
    "draws the raw value, which is why its curve can dip below its own zero line while this "
    "reads zero.")


_log = logging.getLogger("studio.central_view")


class UndoOutcome(NamedTuple):
    """What an Edit ▸ Undo actually restored: whether the START/FINISH line moved, and how the
    sector-line COUNT changed (+2 = two lines came back, -1 = an added line went away).

    Exists because the window used to print "reverted the last start/finish-line edit" for every
    undo, including the ones that only put sector lines back and left the start line exactly where
    it was (QA W3-03). A NamedTuple, so an outcome is always truthy and the None no-op stays falsy."""

    start_moved: bool
    sector_delta: int


# How far (m) either endpoint of the start line must move for an undo to count as having moved it.
# Restores round-trip through lat/lon, which wobbles the local metres by ~µm; anything a user did is
# orders of magnitude larger.
_START_MOVED_M = 0.05


def _start_moved(before, after) -> bool:
    """Whether a start line given as two (lat, lon) endpoint pairs actually moved between the two
    states — flat-earth metres, which is exact enough over the tens of metres in question."""
    try:
        for (a_lat, a_lon), (b_lat, b_lon) in zip(before, after, strict=True):
            dy = (b_lat - a_lat) * 111320.0
            dx = (b_lon - a_lon) * 111320.0 * math.cos(math.radians(a_lat))
            if math.hypot(dx, dy) > _START_MOVED_M:
                return True
    except (TypeError, ValueError):  # a malformed/absent pair: don't claim a move we can't see
        return False
    return False


def _start_seg_moved(before, after) -> bool:
    """Whether a timing edit MOVED the start line: `before`/`after` in local metres (anything with
    .x1/.y1/.x2/.y2), moved when an endpoint shifted more than `_START_MOVED_M`. The same line drawn
    end-to-start is not a move, so the endpoints are compared both ways round.

    It decides whether an edit is the start line's confirmation (`_on_lines`), which is why its
    unknown answers the opposite way to `_start_moved`: with no line on either side to compare,
    the edit keeps what every edit did before QA r4 CODE-1 (it confirms), rather than an unseeable
    state quietly withholding a drag's confirmation."""
    try:
        a = ((before.x1, before.y1), (before.x2, before.y2))
        b = ((after.x1, after.y1), (after.x2, after.y2))
    except AttributeError:  # None or not a line: no comparison, so the edit confirms as it did
        return True

    def spread(p, q):
        return max(math.hypot(p[0][0] - q[0][0], p[0][1] - q[0][1]),
                   math.hypot(p[1][0] - q[1][0], p[1][1] - q[1][1]))

    return min(spread(a, b), spread(a, b[::-1])) > _START_MOVED_M


def compare_ended_notice(ids) -> str:
    """The status line a lap pick that ended compare leaves (CentralView._pick_leaves_compare):
    lower-case and status-bar shaped like undo_summary, naming the laps the window shows now."""
    which = ", ".join(lap_label(i) for i in sorted(ids))
    return f"compare ended — showing lap{'s' if len(ids) > 1 else ''} {which}"


def undo_summary(outcome: UndoOutcome) -> str:
    """The ONE sentence naming what an undo restored, lower-case and status-bar shaped.

    Single-sourced here because two surfaces say it in the same frame — the window's status bar and
    the map's on-canvas plate — and this finding is precisely about one gesture being named four
    different ways (QA W3-03). Sector counts are named outright: "put back 2 sector lines" is the
    only phrasing that tells a user with both kinds of edit on the stack which one just came back."""
    n = abs(outcome.sector_delta)
    lines = f"{n} sector line{'s' if n != 1 else ''}"
    if outcome.start_moved and outcome.sector_delta:
        return "reverted the last timing-line edit"
    if outcome.start_moved:
        return "reverted the last start/finish-line edit"
    if outcome.sector_delta > 0:
        return f"put back {lines}"
    if outcome.sector_delta < 0:
        return f"removed {lines}"
    return "reverted the last timing-line edit"  # a restore that changed nothing visible


def _hero_templates() -> tuple[str, ...]:
    """The widest readouts the hero #DiffBox can render, written BY the formatters that render them
    (both references, both signs, every speed unit), so a format change cannot leave the floor
    sized for a string the readout no longer prints. The digit VALUES are irrelevant — the face is
    tabular — only how many there are: two before the point, three of speed."""
    out = []
    for unit in units.UNITS:
        out.append(theme.format_ideal_readout(_HERO_WIDEST_DELTA_S, _HERO_WIDEST_SPEED_KMH, 0,
                                              unit)[0])
        out.append(_at_rest(out[-1]))
        for d in (_HERO_WIDEST_DELTA_S, -_HERO_WIDEST_DELTA_S):
            for ref in (False, True):
                out.append(_name_hero_baseline(
                    theme.format_delta_speed(d, _HERO_WIDEST_SPEED_KMH, 0, unit)[0], ref))
                out.append(_at_rest(out[-1]))
    return tuple(out)


def _at_rest(readout: str) -> str:
    """A hero readout with its speed slot replaced by HERO_AT_REST_TAG (VIEW-7): the formatters join
    the Δ and the speed with five spaces, and at rest the Δ is the lap's and the speed is not."""
    return readout.rsplit("     ", 1)[0] + "     " + HERO_AT_REST_TAG


# A GAP THE TIMING CANNOT TELL FROM ZERO IS NOT A COLOUR, ON THE APP'S LARGEST NUMBER TOO
# (cool/delta-readout-level). The readout coloured any |Δ| over theme.DELTA_EVEN_EPS_S (5 ms), so
# the demo's best lap at 35 % read "Δideal +0.05 s" in red, while the compare seam (#552) and every
# burned overlay (Classic, Broadcast, the compare pill: #611) draw the same quantity neutral under
# the timing step. It now asks the overlays' own rule, `overlay.broadcast.is_level` (the PRINTED
# value against `library.within_timing_precision`, 0.07 s), called, never re-typed — so "+0.07" is
# coloured and two equal strings never look different. The 0.07 s step applies on an estimated
# clock too, as it does on the seam and the exports this readout sits beside: they draw the same
# Δ-along-the-lap, and a readout that switched to the Lap race's 0.15 s there would disagree with
# the seam under it in compare. Inside the step the number and its sign still print, without the
# ▲/▼ (a direction arrow beside a neutral number would claim what the colour disclaims), as the
# seam does.
def _level_readout(text: str, sem_colour: str | None, value: float | None
                   ) -> tuple[str, str | None]:
    """`(text, colour)` as the hero shows them: neutral and arrow-less when `value` prints level."""
    if sem_colour is None or not broadcast.is_level(value):
        return text, sem_colour
    arrow = theme.delta_arrow(value)
    return (text.replace(f" {arrow}", "", 1) if arrow else text), None


def _hero_min_width(label: QLabel) -> int:
    """Layout floor for the hero Δ/speed readout: the width Qt ITSELF asks for the widest text the
    label can show, in the font and padding it really paints with.

    A QLabel never elides — it HARD-CLIPS — so without a floor the charts header's proportional
    squeeze eats characters off the live number itself. And the floor has to be the label's own
    `sizeHint`, not a text advance plus a counted pad: the layout gives the box
    max(sizeHint, minimum), so a floor below the widest sizeHint is a box that WIDENS when the
    number gets long. That is what shipped — an advance over the mono stack + 20 px made a 391 px
    floor under a 400 px sizeHint, and the hero grew 9 px the moment Δideal reached 10 s."""
    label.ensurePolished()          # the QSS padding is part of the answer
    shown = label.text()
    try:
        widest = 0
        for text in _hero_templates():
            label.setText(text)
            widest = max(widest, label.sizeHint().width())
    finally:
        label.setText(shown)
    return widest


# The hero's Δ-to-baseline run when the chip is off, NAMED like its sibling `Δideal`: the shared
# formatter (export overlay, byte-pinned) prints a bare `Δ +0.37 s`, which on the best lap read
# `Δ 0.00 s` under a panel titled "Δ TO IDEAL" beside a strip reading `Δ to ideal +0.085 s` — an
# unnamed Δ the eye assigns to the panel's baseline, and then they disagree. With a cross-recording
# reference loaded the number is measured against THAT lap (`delta_at_lap`), so it says `Δref`.
_HERO_BASE_NAMES = {False: "Δbest", True: "Δref"}


def _name_hero_baseline(readout: str, reference: bool) -> str:
    """`format_delta_speed`'s readout with its leading bare `Δ` replaced by the baseline's name."""
    return _HERO_BASE_NAMES[reference] + readout[1:] if readout.startswith("Δ ") else readout


class _HeroReadout(QLabel):
    """The hero #DiffBox: ONE label (so `text()`, the width floor and every reader stay as they
    were) that paints its Δ run in the gain/loss colour and the speed after it in the label's own
    neutral text colour. It used to be one colour for the whole line, so a positive Δ turned the
    SPEED red too, as if 45 km/h were a loss. The split is the formatters' own five-space join.

    THE Δ IS THE VALUE; WHAT FOLLOWS IT IS SECONDARY (cool/delta-readout-level). The tail — the
    speed, or "whole lap" at rest — was painted at the Δ's HERO size and weight, so "whole lap"
    read as a second value. It now paints a step down, on the Δ's baseline, and the Δ run alone
    keeps the hero face: the LIVE SPEED at ``theme.EMPHASIS``, regular, in ``C.text`` — still a
    glance while scrubbing (13 px dim was readable, measured in review, not glanceable) — and
    "whole lap", a label about the Δ rather than a value, at ``theme.BODY`` in ``C.text_dim``. The
    width floor still measures the whole line in the hero face (`_hero_min_width`), so the smaller
    tail can only leave room, never clip."""

    def __init__(self, text: str):
        super().__init__(text)
        self._delta_colour: str | None = None
        self._speed_font = theme.mono_font(theme.EMPHASIS, theme.W_REGULAR)
        self._label_font = theme.mono_font(theme.BODY, theme.W_REGULAR)

    def tail_style(self, tail: str) -> tuple:
        """(font, colour) the run after the Δ paints in: a tag ("whole lap", "since C4 entry")
        dim, the speed in text."""
        if _is_hero_tag(tail):
            return self._label_font, theme.C.text_dim
        return self._speed_font, theme.C.text

    def sizeHint(self):  # noqa: N802 (Qt override)
        """QLabel's hint, except with a corner compare's tag: QLabel prices the whole line in the
        hero face, and "since C4 entry" there is 392 px of the 354 px floor, so the box grew 38 px
        and shoved the charts header for as long as the compare held. Painted at its own size the
        line fits the floor (HERO_CORNER_TAG's note), so the box keeps it. The at-rest tag keeps
        QLabel's hint: `_hero_min_width` measures that one, and the floor must stay what it was."""
        return self._keep_floor(super().sizeHint())

    def minimumSizeHint(self):  # noqa: N802 (Qt override)
        """As sizeHint: the header's layout reads this one too (measured, 392 px without it)."""
        return self._keep_floor(super().minimumSizeHint())

    def _keep_floor(self, hint):
        tail = self.text().partition("     ")[2]
        if tail != HERO_AT_REST_TAG and _is_hero_tag(tail) and self.minimumWidth() > 0:
            hint.setWidth(min(hint.width(), self.minimumWidth()))
        return hint

    def delta_colour(self) -> str | None:
        """The colour the Δ run paints in, or None (all neutral)."""
        return self._delta_colour

    def set_delta_colour(self, colour: str | None):
        if colour != self._delta_colour:
            self._delta_colour = colour
            self.update()

    def paintEvent(self, event):  # noqa: N802 (Qt override)
        head, sep, tail = self.text().partition("     ")
        if not sep:
            super().paintEvent(event)
            return
        fm = QFontMetrics(self.font())
        # Where QLabel itself would start the text: its contents rect, less its margin and its
        # INDENT — which, left at the default -1 on a framed label (the QSS box makes it one), is
        # half an "x" (QLabelPrivate::documentRect). Without it the run sits 6 px left of where
        # the same text paints with no Δ colour.
        indent = self.indent()
        if indent < 0:
            indent = fm.horizontalAdvance("x") // 2 if self.frameWidth() > 0 else 0
        m = self.margin()
        rect = self.contentsRect().adjusted(m + indent, m, -m, -m)
        p = QPainter(self)
        p.setFont(self.font())
        align = self.alignment()
        p.setPen(QColor(self._delta_colour) if self._delta_colour
                 else self.palette().color(self.foregroundRole()))
        p.drawText(rect, align, head)
        # The tail starts at the FRACTIONAL advance Qt itself lays the whole line out at, and sits
        # on the head's baseline (vertically centred as QLabel centres the head's own line).
        hm = QFontMetricsF(self.font())
        rect = QRectF(rect)
        x = rect.left() + hm.horizontalAdvance(head + sep)
        baseline = rect.top() + (rect.height() - hm.height()) / 2 + hm.ascent()
        font, colour = self.tail_style(tail)
        p.setFont(font)
        p.setPen(QColor(colour))
        p.drawText(QPointF(x, baseline), tail)
        p.end()


@contextlib.contextmanager
def _busy():
    """Wait cursor for the duration of a blocking re-segmentation.

    Every timing-line gesture — a start/finish drag release, Add sector, Reset sectors, ⌘Z —
    funnels into a synchronous `set_timing_lines` + `rebuild_derived_views`, which re-segments the
    whole session and recomputes every per-lap cache. On a 66-lap three-chapter recording that
    measured **450–527 ms** for a drag release, 494–500 ms for Add sector and 469–485 ms for Reset
    sectors, during which the window was frozen with **no affordance whatsoever** — no cursor
    change, no status line, nothing. The same gestures cost ~190 ms on a one-chapter session, so
    the cost scales with the session and the big recordings are exactly where a user does this
    work.

    The two notice-posting gestures make it worse rather than better: `_add_sector` and
    `_reset_sectors` call `_emit()` and only THEN `_post_notice()`, so their explanation appears
    *after* the half-second of dead UI it was meant to cover.

    Qt applies an override cursor to the platform immediately, so it is visible even though the
    event loop never turns during the work — which is the point: the alternative (moving the
    recompute off-thread) is a far larger change to the app's most correctness-critical path.
    `restoreOverrideCursor` is in a `finally` so an exception mid-re-segment cannot strand the
    application in a permanent wait cursor."""
    QGuiApplication.setOverrideCursor(QCursor(Qt.CursorShape.WaitCursor))
    try:
        yield
    finally:
        QGuiApplication.restoreOverrideCursor()


class _MapRaceButton(ToggleButton):
    """The map toolbar's "Lap race" button: View ▸ Lap race's visible twin. It holds no race state
    of its own — the window binds it to that QAction (`app._RaceButtonBinding`), so its check,
    enabled state and tooltip are the menu item's.

    IT IS NOT IN THE TOOLBAR'S LAYOUT. PanelToolbar pins every control at its own width, so the
    bar's honest need IS the map panel's minimum width: 552 px, one pixel under the 553 px the
    charts panel sets for the column (test_map_key holds that). Any laid-out control — even a 0 px
    minimum, since the row's spacing still counts — would raise the window's own minimum. So the
    button sits in the empty left-hand stretch the right-aligned run leaves, placed on every bar
    resize/relayout, and is the bar's ONE control to step aside when that stretch is too narrow
    (the panel near its floor, ~650 px): the run is the map's only way to its own controls, while
    the race keeps R, the View menu and ⌘K. Nothing is ever clipped or pushed."""

    def __init__(self, bar: QWidget):
        super().__init__("Lap race", glyph="ph.flag", parent=bar)
        self.setAccessibleName("Lap race")
        self._bar = bar
        bar.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self._bar and event.type() in (QEvent.Resize, QEvent.LayoutRequest,
                                                 QEvent.Show):
            self.place()
        return False

    def place(self) -> None:
        """Left-align it in the bar's stretch at the controls' height, or hide it if it would not
        fit there with one spacing to spare."""
        bar, row = self._bar, self._bar.layout()
        w = self.sizeHint().width()
        room = bar.width() - row.sizeHint().width()     # the stretch the right-hand run leaves
        fits = room >= w + row.spacing()
        if fits:
            self.setGeometry(row.contentsMargins().left(), (bar.height() - theme.CTRL_H) // 2,
                             w, theme.CTRL_H)
        self.setVisible(fits)



def typical_luck_reason(lap: str, label: str, v) -> str:
    """Why the QUICKEST pass `v` (a `stats.PassVsTypical` level only through its luck term) is not
    compared with its typical pass. The bound and the luck print to the millisecond: at two
    decimals a 0.09 s gap read "within 0.09 s" beside a bound of 0.092, and a small luck "0.00"."""
    bound = with_luck(v.level_s, v.luck_s)
    return (f"Lap {lap}'s pass through {label} is within {bound:.3f} s of your typical pass as "
            f"timed: the quickest of {plural(v.n, 'pass', 'passes')} is expected to read about "
            f"{v.luck_s:.3f} s quicker than typical by luck alone")

class CentralView(QWidget):
    """Session-scoped central widget for one loaded recording (see module docstring)."""

    # Emitted after any timing-line change (a user drag OR an Undo) so the window can refresh the
    # Edit ▸ Undo action's enabled state from the session's undo stack.
    #: §7.5 — True when the last sidecar write FAILED, read by StudioWindow._session_notice so a
    #: placement that could not be persisted says so instead of looking saved.
    sidecar_write_failed = False

    timingEdited = Signal()
    # Emitted when the user switches the lap panel's tab (Laps/Corners/Stats/Coaching), so the
    # window can persist the choice across reloads.
    lapTabChanged = Signal(int)
    # Emitted (debounced) after the user drags any of the three GRID splitters, with the
    # [main, left, right] sizes — the window persists them so a layout survives reloads.
    gridSizesChanged = Signal(list)
    # Emitted (True = entering) when the user toggles VIDEO FOCUS (the ⤢ button / a double-click on
    # the video). The view has already maximized the video panel into the grid; the window responds
    # by going fullscreen (True) / normal (False) so the maximized video fills the whole SCREEN with
    # no window chrome — the "fullscreen video" gesture, built on the proven maximize + native-
    # fullscreen paths (no risky reparenting of the live media surface).
    videoFocusChanged = Signal(bool)
    # A one-line, just-happened fact for the WINDOW's status bar, from a panel that has no channel
    # to it (U2: LapTable.selection_capped — a multi-select trimmed to the charts' lap cap).
    statusNotice = Signal(str)
    # ...and its retraction: a sentence posted by `_hold_status` that stopped being true (the pass
    # or the corner compare it named was left). The window clears it only if it is still showing.
    statusRetracted = Signal(str)
    # A pass's right-click "Export this pass…": (lap, cid, label) -> the window's overlay export,
    # opened on that pass (ExportController.export_pass_video, the ⌘K row's own door).
    passExportRequested = Signal(int, int, str)
    # The Lap Race started (True) or ended (False) — the window keeps its menu check in step.
    raceChanged = Signal(bool)

    def __init__(self, session, paths: list[str], sidecar_path: str | None,
                 parent: QWidget | None = None,
                 speed_unit: str | None = None, excluded_visible: bool = True,
                 lap_tab: int = 0, grid_sizes: list | None = None):
        super().__init__(parent)
        # Read aliases of window-owned state; the view never reassigns these.
        self.session = session
        self._paths = list(paths)
        self._sidecar_path = sidecar_path
        # The excluded strip lives inside the Laps page; its View-menu choice is persisted by
        # the window and passed into each fresh view (see _apply_excluded_visible).
        self._excluded_visible = excluded_visible
        # The lap panel's persisted tab (Laps 0 / Corners 1 / Stats 2 / Coaching 3) + the
        # persisted grid-splitter sizes ([main, left, right]; None = the built-in defaults).
        self._initial_lap_tab = int(lap_tab)
        self._initial_grid_sizes = grid_sizes
        # Speed display unit (km/h default). The window passes the persisted choice and pushes
        # later flips via set_speed_unit; the hero #DiffBox reads it, sub-views hold their own copy.
        self._speed_unit = units.normalize_unit(speed_unit)

        # Atomic build: panels -> layout -> signals -> controllers.
        self._construct_panels()
        # Seed each speed-bearing sub-view's unit BEFORE the first rebuild fills them (set the
        # field directly — set_speed_unit would trigger a redundant pre-build refresh). Two side
        # effects were applied at the km/h default in the sub-views' own __init__ and are NOT
        # re-run by the rebuild below, so they must be re-applied by hand here: the plots y-axis
        # label, and the Corners table's per-column header tooltips. Every other unit-bearing
        # string (the Laps headers, the map legend, Stats, Coaching) is rebuilt from _speed_unit
        # by the refresh that rebuild_derived_views triggers, so it needs nothing.
        for w in (self.plots, self.table, self.corner_table, self.map, self.opportunities,
                  self.stats_view):
            w._speed_unit = self._speed_unit
        self.plots._apply_speed_axis_label()
        self.corner_table._apply_corner_tips()
        self._layout_panels()
        self._wire_signals()
        self._build_controllers()

        # Seed session-derived views (selects two fastest laps).
        self.rebuild_derived_views(reselect=True)
        # Poster the best-lap first frame so the video isn't a black void at launch.
        self._poster_seek()
        # Apply the window-held declutter choice (the excluded strip inside the Laps page) and
        # restore the persisted lap-panel tab (a no-op re-select for the default Laps).
        self._apply_excluded_visible()
        if 0 <= self._initial_lap_tab < self.tab_bar.count():
            self.tab_bar.setCurrentIndex(self._initial_lap_tab)

    # ------------------------------------------------------------- the Stats page accessor
    @property
    def stats_view(self) -> StatsView:
        """The Stats page — GUARANTEED CURRENT, whoever is asking.

        The page renders lazily (P1: `StatsView.refresh_when_shown`; half of every 396 ms rebuild
        on a 65-lap recording went into a page nobody was looking at). The hazard that buys is
        that some reader picks up a figure that silently predates the last start-line drag — and
        this page and its DATA TRUST card are where the app makes its honesty claims, so that
        would be strictly worse than the 200 ms.

        SO THE GUARD IS STRUCTURAL, NOT A CONVENTION. The widget's only attribute name outside
        `stats_panel.py` is this property, the raw `self._stats` is touched only by the handful of
        methods that build it or re-render it on the next line (pinned by `tests/test_stats.py`), and
        anything that says `view.stats_view` — the data-quality chip's `reveal_trust`, the corner
        table's click wiring, `studio/dev/media_capture.py`'s page grab, every test — gets the
        debt paid before the object is handed over. No caller has to remember; the other route to
        the page's contents (Qt painting it) is closed by `StatsView.showEvent`.

        A `CentralView.__new__`'d unit fixture has no panels, so this raises `AttributeError`
        exactly as a missing plain attribute would, and `getattr(view, "stats_view", None)` still
        answers None."""
        stats = self.__dict__.get("_stats")
        if stats is None:
            raise AttributeError("stats_view")
        stats.flush_if_stale()
        return stats

    # ------------------------------------------------------------------ lifecycle
    def showEvent(self, event):
        """First show: restore the persisted grid-splitter sizes — SYNCHRONOUSLY here, and then
        twice more on timers.

        THE FIRST PASS IS THE ONE THE USER SEES. Both restores used to be deferred, and a
        returning user (anyone who has ever dragged a splitter) therefore watched their studio
        paint the BUILT-IN DEFAULT layout first, fully rendered with their real data, and then
        jump. Measured on the real three-chapter drop with a persisted `main=[700, 732]`, sampling
        the window composite and all three splitters on the wall clock at a 0.10 ms median
        interval from `loadFinished`: the default `main=[515, 917]` was painted at +53 ms and held
        for **78 ms** — the `singleShot(0)` did not run until +108 ms, because a zero-timer is
        still queued BEHIND the first layout/paint burst of a freshly installed 66-lap view. Every
        panel then moved at once: left column +185 px wide, video −94 px tall, map +185 px right
        and +180 px taller (a full axis re-tick), charts +180 px down. `overlays.PBToast` already
        knew — it defers itself 120 ms with a comment saying the restore MOVES the lap panel. The
        toast waited; the panels did not.

        The pre-show `setSizes` this used to warn about is a different moment: `_layout_panels`
        runs before the widget has any geometry at all, and a restore there IS warped by min-size
        clamping. By the time a ShowEvent is delivered the splitters already carry their real
        post-layout extents (measured: 1440x854, `main=[515, 917]` at showEvent ENTRY), so the
        synchronous pass lands undistorted and the first frame is the user's own layout.

        BOTH DEFERRED PASSES STAY, and they are not belt-and-braces: they are the correctness net
        for the case the synchronous pass cannot serve — a view shown before its top-level has
        activated its layout (no geometry yet), and the deferred top-level layout passes that
        re-split by stretch factor and would otherwise override whichever splitter they touch
        last. `_apply_grid_sizes` is idempotent, so running it three times costs three clamped
        `setSizes` on already-correct splitters and changes nothing."""
        super().showEvent(event)
        pending = getattr(self, "_pending_grid_sizes", None)
        if pending is not None:
            self._pending_grid_sizes = None
            self._apply_grid_sizes(pending)
            # Then twice, idempotently: once right after this event burst, once after the deferred
            # top-level layout passes (which re-split by stretch factor and would otherwise
            # override the restore of whichever splitter they touch last).
            # OWNED TIMERS, never the static `QTimer.singleShot` (§3.9). The static form keeps the
            # lambda — and the `self` it captures — alive with no connection to this widget's
            # lifetime: a reload inside 120 ms destroys the view's C++ object and the timer then
            # fires `_apply_grid_sizes` on it, which raises RuntimeError straight into the crash
            # reporter. A `QTimer(self)` dies with its parent, so the same reload cancels it.
            # Same pattern (and the same reason) as `overlays.py`'s auto-dismiss timer.
            for delay in (0, 120):
                t = QTimer(self)
                t.setSingleShot(True)
                t.timeout.connect(lambda p=pending: self._apply_grid_sizes(p))
                t.start(delay)
                self._grid_restore_timers.append(t)
        # The debrief's maximize waits for THIS moment (show_debrief), after the synchronous
        # restore: only now do the splitters hold the layout Esc must return to.
        if getattr(self, "_debrief_pending", False):
            self._debrief_pending = False
            self._maximize_debrief()

    def _apply_grid_sizes(self, stored: list):
        """Apply persisted [main, left, right] splitter sizes. Each list is applied only if it
        matches its splitter's section count AND every section is strictly positive — a
        stale/corrupt pref falls back cleanly to the built-in defaults (already set).

        The positivity test is what stops a prefs.json written by a build that still let a drag
        collapse a column (see _layout_panels) from RESURRECTING the deleted panel on every
        relaunch: a stored [1432, 0] used to pass the old count/non-negative/sum>0 guard and
        reopen the window with MAP and CHARTS off screen. Rejecting the one bad list leaves the
        other two splitters free to restore, so a user only loses the layout that was unusable.

        WHILE A PANEL IS MAXIMIZED the restore goes into the grid it will return to, not onto the
        splitters. The debrief maximizes the lap panel at first show, and the two deferred passes
        above land after that; applied to the splitters they put the grid back on screen under a
        panel still marked maximized (measured with the maximize one step earlier, at the load's
        tail: this pass took the main splitter from [1432, 0] to [700, 732])."""
        splitters = (self._main_splitter, self._left_splitter, self._right_splitter)
        saved = getattr(self, "_saved_splitter_sizes", None)
        into = list(saved) if (getattr(self, "_maximized_panel", None) is not None
                               and saved is not None) else None
        for i, (splitter, sizes) in enumerate(zip(splitters, stored, strict=False)):
            if (isinstance(sizes, (list, tuple)) and len(sizes) == splitter.count()
                    and all(isinstance(v, (int, float)) and v > 0 for v in sizes)):
                if into is not None:
                    into[i] = [int(v) for v in sizes]
                else:
                    splitter.setSizes([int(v) for v in sizes])
        if into is not None:
            self._saved_splitter_sizes = tuple(into)

    def dispose(self):
        """Stop decoder(s) + close the g-meter overlay before this view is dropped on reload. Called
        by StudioWindow on the outgoing view. No-op if no video."""
        for kind in (_HELD_PASS, _HELD_CORNER):
            self._release_status(kind)    # a reload: nothing this view said about its state holds
        self.end_lap_race()
        self._finish_diff_roll()          # no readout frame from a view on its way out
        video = getattr(self, "video", None)
        if video is not None:
            video.stop_all()

    # --------------------------------------------------------- panel container helpers
    # `_header_bar` and a text-only `_panel` used to live here. Both are gone: studio.widgets.
    # PanelHeader is the ONE header, it declares its own height, and it takes the identity as a
    # string OR a widget — which is exactly the difference the two helpers used to encode. (The
    # text-only variant was already dead: none of the four quadrants had used it since the map and
    # charts headers grew controls.)

    @staticmethod
    def _maximize_button() -> QPushButton:
        """A small right-aligned header button that maximizes/restores its panel — the VISIBLE
        affordance for the same action as double-clicking the header. One `widgets.icon_button`,
        the same object the video transport is built from, so every icon button in the app is one
        size. Wired to its panel later by _wire_maximize_button (the panel container doesn't exist
        yet when the header is built); its glyph/tooltip track the maximized state via
        _sync_maximize_buttons."""
        return icon_button(
            _MAXIMIZE_GLYPH,
            tooltip="Maximize panel (or double-click the header) — Esc / click again to restore")

    def _wire_maximize_button(self, btn: QPushButton, panel: QWidget) -> None:
        """Connect a header maximize button to its panel's toggle + register it so its glyph/tooltip
        can be synced to the maximized state. Called once the panel container exists."""
        buttons = self.__dict__.setdefault("_maximize_buttons", {})
        buttons[btn] = panel
        btn.clicked.connect(lambda: self._toggle_panel_maximized(panel))

    def _sync_maximize_buttons(self) -> None:
        """Point every header maximize button at the current state: the panel that IS maximized shows
        the restore glyph (accent-tinted) + a restore tooltip; the others show the maximize glyph.
        No-op before the buttons/state are built (phase 1 vs phase 2). Called on every toggle/restore."""
        buttons = getattr(self, "_maximize_buttons", None)
        if not buttons:
            return
        maxed = getattr(self, "_maximized_panel", None)
        for btn, panel in buttons.items():
            is_max = panel is maxed
            btn.setIcon(theme.icon(_RESTORE_GLYPH if is_max else _MAXIMIZE_GLYPH,
                                   color=theme.C.accent if is_max else None))
            btn.setToolTip(
                "Restore panel to the grid (or Esc / double-click the header)" if is_max else
                "Maximize panel (or double-click the header) — Esc / click again to restore")

    @staticmethod
    def _headered(header: PanelHeader, *contents, toolbar: PanelToolbar | None = None) -> QWidget:
        """Assemble one panel: PanelHeader, then its PanelToolbar if it has one, then the contents.

        The header is always layout item 0 — _install_header_dblclick resolves a panel's header
        that way, and the double-click-to-maximize gesture belongs to the identity row, not to a
        row of buttons where every click would land on a control anyway. Each content entry is a
        widget or a (widget, stretch) tuple."""
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
        if toolbar is not None:
            lay.addWidget(toolbar)
        for c in contents:
            if isinstance(c, tuple):
                lay.addWidget(c[0], c[1])
            else:
                lay.addWidget(c)
        return panel

    # ----------------------------------------------------- build phase 1: panels
    def _construct_panels(self):
        """Build every panel widget + its header strip + the table stack, and set the panel-level
        self.* attrs (_video/_table/_map/_plots_panel). No layout, no signal wiring."""
        # The VideoView is driven by the session's ChapterMap so the slider spans the whole
        # session and playback auto-advances across chapters.
        self.video = VideoView(self.session.chapters or self.session.video_path)
        # Only offer the g-meter toggle when a g signal was computed (IMU present). A REFUSED
        # accelerometer (gmeter.axis_check) is named on the tooltip that already names the g source:
        # without it the GPS sentence said "No usable accelerometer" — on hero8, the one reason that
        # was not the reason. getattr-guarded: a Session double with no axis accessor has nothing
        # refused.
        axis = getattr(self.session, "gmeter_axis", lambda: None)()
        refusal = axis.refusal() if axis is not None else None
        self.video.set_gmeter_source(self.session.gmeter_source(),
                                     self.session.gmeter_long_source(), refusal)
        self.video.gmeter_btn.setEnabled(self.session.has_gmeter)
        if not self.session.has_gmeter:
            self.video.gmeter_btn.setToolTip(
                f"This recording's accelerometer was not used: {refusal}" if refusal
                else "No accelerometer data in this recording")
        self.map = MapView(self.session)
        # Corner labels pushed from here so MapView stays a pure consumer of marker tuples.
        self.map.set_corners(self.session.corners.corner_map_markers())
        self.plots = PlotsView(self.session)
        self.table = LapTable(self.session)
        # Corners mode: a 2nd table stacked under the same panel (per-corner rows for the selected lap).
        # ...behind a two-button switch: "Lap N" (that table) or "Quickest passes" (the quickest
        # pass through each corner, one click from the video). `corner_table` stays the Lap view.
        self.corners_page = CornersPage(self.session)
        self.corner_table = self.corners_page.lap_view
        self.corners_page.quickest.pass_requested.connect(self._play_quickest_row)
        self.corners_page.view_changed.connect(self._on_corners_view_changed)
        # Its row says "playing lap 7 …" only while that clip runs (VideoView.play_clip's end).
        self.video.clipEnded.connect(self._on_clip_ended)
        self.video.liveReadoutShown.connect(self._on_live_readout_shown)
        # ...and again while Loop replays it, and "· looping" while Loop is on.
        self.video.clipStarted.connect(self._on_clip_started)
        self.video.loopChanged.connect(self._on_pass_loop_changed)
        # B4: a Corners row rings its apex on the map, like the Stats CORNERS and Coaching
        # rows — the same maximize-aware handler, so the three surfaces behave identically.
        self.corner_table.corner_clicked.connect(self._on_corner_row_clicked)
        self._corner_lap: int | None = None  # the lap the Corners view describes
        # Stats mode: the 3rd page — the session-statistics dashboard (studio/stats_panel.py).
        # UNDERSCORED, and the `stats_view` property below is the only way to it: the page renders
        # lazily (P1), and the property is where the debt is paid. See its docstring.
        self._stats = StatsView(self.session)

        # Always-on Δ/speed readout for the current moment (hero #DiffBox; Δ colour set per-tick).
        # By default it LEADS with Δ-to-IDEAL (the moat number: how far off your own achievable lap
        # you are, right here) rather than Δ-to-best; the small ideal_readout_btn flips it to
        # Δ-to-best, and whichever number isn't shown lives in the box's tooltip — no information is
        # removed, just re-prioritized (see _update_diff_box).
        # Placeholder text before the first tick — uses the display unit so it never flashes km/h
        # when the app is set to mph (the live readout re-renders on the first _update_diff_box).
        self.diff_box = _HeroReadout(f"Δideal —    — {units.speed_label(self._speed_unit)}")
        self.diff_box.setObjectName("DiffBox")
        # LEFT-aligned, not centred: a centred QLabel that is squeezed clips at BOTH ends, so the
        # leading "Δ" was the first thing lost. Left alignment spends any residual clip on the tail.
        self.diff_box.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.diff_box.setFont(theme.mono_font(theme.HERO, theme.W_SEMIBOLD))
        # …and the hero number never gives up characters at all: an explicit minimum sized to the
        # widest readout. On a QLabel setMinimumWidth SETS the layout minimum (qSmartMinSize takes an
        # explicit minimum over the size hint) — the same lever plots_label uses to volunteer itself
        # as the header's first casualty.
        self.diff_box.setMinimumWidth(_hero_min_width(self.diff_box))
        self._diff_colour = None  # last applied Δ-value colour (per-tick recolor guard)
        # The Δ roll (_roll_diff): the lead value shown, the running roll and its current value.
        self._diff_shown: float | None = None
        self._diff_roll = None
        self._diff_rolling_value: float | None = None
        # Last (speed, lap) the readout rendered — so toggling the reference re-renders without a tick.
        self._last_diff_speed: float | None = None
        self._last_diff_lap: int | None = None
        # Checked (default) → Δ-to-ideal leads; unchecked → Δ-to-best leads. A CHIP, not a plain
        # button: it does not act on the charts, it NAMES what the hero number beside it is measured
        # against — the same job the lap panel's quality badge does for the lap times — so it wears
        # the chip pill rather than the button rectangle its two neighbours (Brake/Throttle, Ideal
        # lap, which really do turn overlays on) wear. It stays a checkable QPushButton because it
        # is genuinely interactive; only its LOOK is shared with the static chips. See the
        # [role="Chip"] rule in theme.py.
        self.ideal_readout_btn = ToggleButton("vs ideal", checked=True)
        self.ideal_readout_btn.setProperty("role", "Chip")
        self.ideal_readout_btn.setToolTip(_IDEAL_CHIP_TIP)
        self.ideal_readout_btn.toggled.connect(self._on_ideal_readout_toggled)
        # Which of the three ideal states this session is in — "stitched" (the normal case),
        # "one_lap" or "none". Decided ONCE per rebuild in _sync_ideal_readout, because
        # _update_diff_box reads it at ~30 Hz and must not re-ask the session. Seeded here so the
        # readout is safe before the first rebuild (and on the __new__ unit-test path).
        self._ideal_state = "stitched"
        # The user's own choice of reference, remembered across a state that takes the chip away
        # (the same "restore exactly what it said before" contract _set_control_enabled has).
        self._ideal_readout_wanted = True

        # Which chapter the video is in — a CHIP in the panel's identity row, shown only for a
        # multi-chapter session. It was a full-width banner strip ABOVE the video: a fifth band in
        # a panel that already stacked four, printing the recording label the window TITLE carries
        # and the chapter the transport timecode printed again 300 px below. A chip in the `status`
        # slot is what PanelHeader has that slot FOR — a live fact about what this panel is showing
        # right now, sitting beside the word that names the panel.
        self.chapter_label = chip("")
        self._seam_loading = False  # True while a chapter is reopening at a seam (the chip's hint)
        self._update_chapter_label(self.video.current_chapter())
        self.video.chapterChanged.connect(self._update_chapter_label)
        # Brief "loading next chapter…" hint on the chip during a seam reopen.
        self.video.seamLoading.connect(self._on_seam_loading)
        self.chapter_label.setVisible(self.video.is_multi)

        # ...and whether the panel is holding ONE video or TWO. Entering compare replaces the video
        # with a two-pane stage, re-ranges the scrub bar from the whole session to a single lap and
        # empties its lap ruler, and the identity row used to go on reading exactly "VIDEO" —
        # "compare" appeared nowhere in the window but on the transport button and its tooltip.
        self._compare_chip = chip("COMPARING", tone="warn")
        self._compare_chip.setVisible(False)
        self.video.compareModeChanged.connect(self._on_compare_mode_changed)

        # VIDEO panel: identity + ⛶, no toolbar — the transport controls belong ON the player, and
        # the ⤢ *fullscreen-video* button stays with them (a DIFFERENT action from ⛶: fill the
        # SCREEN, not this window quadrant).
        self._video_max_btn = self._maximize_button()
        self._video_header = PanelHeader("VIDEO",
                                         status=(self.chapter_label, self._compare_chip),
                                         trailing=self._video_max_btn)
        video_panel = self._headered(self._video_header, (self.video, 1))

        # LAP panel: ONE native tab bar (Laps · Corners · Stats · Coaching · Marks) over a QStackedWidget
        # — the page switcher IS the tab bar, and every page gets the panel's FULL height. This
        # replaced the checkable-button pseudo-tabs + the two min/max-capped under-table strips
        # (coaching + consistency), whose aggregate minimum could exceed the panel's allocation
        # (over-constrained: nothing was resizable and everything starved). Tab index == stack
        # index, 1:1. Digits 1-5 select tabs (window-level shortcuts).
        self.tab_bar = QTabBar()
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        # B2: on a narrow window the bar offers SCROLL BUTTONS rather than sliding a tab under the
        # ⛶ button. Elide must stay OFF: the QSS gives every tab `padding: 6px 10px`, which Qt
        # deducts a SECOND time when it derives SE_TabBarTabText from the tab rect, so the text rect
        # comes out a few px NARROWER than the label's own advance and ElideRight then elides every
        # name unconditionally, at ANY width ("La…", "Corners ·…", "St…", "Coac…"). Scroll
        # buttons also drop minimumSizeHint to ~half the sizeHint (133 against 240), so pin a
        # Minimum h-policy: a layout may grow the bar but never squeeze it below the width its own
        # names need.
        #
        # THE ARROWS ARE THE FALLBACK, NOT THE ANSWER. That h-policy makes the header's own minimum
        # follow the bar, and the honest way for the tabs to fit is for the panel to be at least
        # that wide — which it now always is, because the left column no longer overrides its
        # derived floor with a smaller literal (see _layout_panels). Kept switched on because
        # "240 px fits" is a font-stack metric, not a fact: a translation or a differently-resolved
        # face moves it, and a clipped tab name with no way to reach it is worse than a scroll
        # arrow. THE 133/240 PAIR IS FOUR TABS' — #269's Marks page made it five, so read those two
        # numbers as the floor they were measured at rather than as today's figure; the mechanism
        # is derived at runtime and is what tests/test_design_system.py holds. The arrows themselves
        # are sized by theme's QTabBar::scroller rule (they shipped 21 px wide and overlapping by 11).
        self.tab_bar.setUsesScrollButtons(True)
        self.tab_bar.setElideMode(Qt.ElideNone)
        self.tab_bar.setSizePolicy(QSizePolicy.Minimum, QSizePolicy.Fixed)
        self.tab_bar.setFocusPolicy(Qt.NoFocus)
        for name, tip in (
            ("Laps", "Every valid lap: times, distance, entry speed and sector splits "
                     "(session-best splits in purple). Press 1."),
            ("Corners", "Per-corner analysis of the ONE lap you select (named on the tab): "
                        "time-in-corner, that lap's Δ vs the best lap, apex/entry/exit speeds. "
                        "Follows your selection. Corners are detected from the track's own "
                        "curvature. Press 2."),
            ("Stats", "Session statistics: totals, the pace distribution, top speed, peak g, "
                      "the g-g friction circle, corner/braking/straights reports and the "
                      "data-trust card. Press 3; ⌘⇧S opens it full-window."),
            # IA-01: the two tooltips used to promise the SAME baseline with no scope word, so the
            # Coaching page reading a different number from Corners looked like a defect. Corners is
            # per-lap, Coaching is the whole session's median — each names its own scope.
            ("Coaching", "Your top opportunities across the WHOLE session: the corners losing the "
                         "most time vs your own best lap, taken as the median over your clean "
                         "laps, with the measured reason for each. Does NOT follow your lap "
                         "selection — the Corners tab is the per-lap view. Press 4."),
            # The FIFTH tab, and the only one that is not a view of a measurement: everything left
            # of it is what the app computed, this one is what you concluded. Measured, the page
            # costs the window's minimum width 955 → 984 px (26 of it this tab) and its minimum
            # height nothing at all — see marks_panel for the whole budget.
            ("Marks", "What you wrote down against this recording, plus what Pacer detected — GPS "
                      "dropouts, laps left out of your times, and stretches of degraded GPS. "
                      "B drops a mark at the playhead; , and . jump between them. Press 5."),
        ):
            idx = self.tab_bar.addTab(name)
            self.tab_bar.setTabToolTip(idx, tip)
        self.tab_bar.currentChanged.connect(self._apply_table_mode)
        # Small data-quality chip in the panel header, shown next to the tabs only when
        # Session.timing_quality is degraded, so the lap times carry a visible cue right where
        # they're read. Its LABEL + TOOLTIP are set live by _refresh_quality_badge from the shared
        # timing_quality copy (clock-aware: "ESTIMATED" only on the media-clock fallback, "GPS LOW"
        # on a true-clock recording whose only concern is rejected fixes — the old static
        # "ESTIMATED" overclaimed on true-clock footage, M3). Hidden on a clean GPS9 recording.
        #
        # IT OPENS THE ROW THAT EXPLAINS IT (N15). It was a static QLabel "so it adds no tab stop
        # and announces itself to assistive tech as text rather than as a button that does
        # nothing" — which rules out a button with no action, and this one has one: Stats ▸ DATA
        # TRUST, scrolled to and marking the Timing row, whose value now says what the chip's word
        # costs (show_data_trust). So it is a button, and still only a tab stop while it is SHOWN
        # — a hidden widget is not in the Tab ring, so a clean recording's keyboard path is what
        # it always was. An ActionChip rather than a bare QPushButton because the window's Space
        # shortcut would otherwise swallow the key that presses it (see widgets.ActionChip).
        self.quality_badge = ActionChip("ESTIMATED", tone="warn")
        self.quality_badge.setVisible(False)
        self.quality_badge.clicked.connect(self.show_data_trust)
        # …and beside it, the SESSION RECORD chip: the conditions and tyre age the driver wrote
        # down for this recording (studio/session_record.py). Same argument as the quality badge,
        # one step further out — that chip says how far to trust these lap times, this one says
        # what they were set in, and both belong where the times are READ rather than three
        # dialogs away. "Dry · MG Yellow #3, 42 laps" over a lap grid is the difference between a
        # column of numbers and a session.
        #
        # SHOWN ONLY WHEN THERE IS A RECORD. The empty state of this chip is its absence: a
        # permanent "no session record" pill over every lap grid would nag on every surface, on
        # every load, about a form the File menu already offers. Fed by the app
        # (LibraryController.update_record_chip) rather than read here, so this view keeps its
        # one job — the app owns the app-support stores.
        self.record_chip = chip("")
        self.record_chip.setVisible(False)
        # The coaching page: the top opportunities (corner · time lost · reason), full height —
        # no strip, no collapse, no height cap. A corner-row click ring-highlights its apex on
        # the map (the Jump-to-corner detail action stays in the modal dialog).
        self.opportunities = OpportunitiesPanel(self.session)
        self.opportunities.corner_clicked.connect(self._ring_corner)
        self.opportunities.compare_requested.connect(self.compare_at_corner)
        # Stats CORNERS/BRAKING/STRAIGHTS rows → the same apex ring, but maximize-aware: restore
        # the grid first — a ring on a zero-width collapsed map helps no one (the N10 rule).
        self.stats_view.corner_clicked.connect(self._on_stats_corner_clicked)
        self.stats_view.moment_jump.connect(self._on_stats_moment_jump)
        self.stats_view.pass_requested.connect(self._on_grid_pass_requested)   # a grid cell
        # Right-click on a row or cell that names one pass: "Play this pass" / "Export this pass…".
        for widget, resolve in (
                (self.corners_page.quickest.table, self._quickest_menu_pass),
                (self.corner_table.table, self._corner_row_menu_pass),
                (self.stats_view.corner_grid_table, self._grid_menu_pass)):
            widget.setContextMenuPolicy(Qt.CustomContextMenu)
            widget.customContextMenuRequested.connect(
                lambda pos, w=widget, r=resolve: self._show_pass_menu(w, r(w, pos), pos))
        self.table_stack = QStackedWidget()
        # The Marks page: the driver's own annotations + the derived ones (studio/marks.py). It owns
        # no store and no session — the window feeds it the merged list and acts on its signals, the
        # same split the coaching page uses.
        self.marks_panel = MarksPanel()
        self.table_stack.addWidget(self.table)          # index 0 — Laps (default)
        self.table_stack.addWidget(self.corners_page)   # index 1 — Corners
        self.table_stack.addWidget(self.stats_view)     # index 2 — Stats
        self.table_stack.addWidget(self.opportunities)  # index 3 — Coaching
        self.table_stack.addWidget(self.marks_panel)    # index 4 — Marks
        rows_h = self.table.table.verticalHeader().defaultSectionSize()
        self.table_stack.setMinimumHeight(rows_h * 5 + 56)  # ~5-row floor so a drag can't zero it
        self._table_max_btn = self._maximize_button()
        # The TABS are this panel's identity — they name the page you are on — and the quality badge
        # is a STATUS chip about the data under them, not a control, so it rides in the header's
        # status slot beside the tabs rather than in a toolbar. (It is clickable now, but its one
        # action is to explain its own status, which does not make it a verb.) Putting it in a
        # toolbar would have given this panel a 32 px control row to hold one chip that is hidden on
        # every clean GPS9 recording; keeping it beside the tabs also keeps the warning adjacent to
        # the lap times it qualifies. This PANEL gets no toolbar at all — though one of its PAGES
        # does: the Marks list carries its own (add · edit · extend · delete, plus a filter and a
        # search), because it is the one page with verbs. A page-level bar appears with its page
        # and leaves the other four bare, where a panel-level one would put a control row over four
        # grids that have nothing to put in it.
        self._table_header = PanelHeader(self.tab_bar,
                                         status=(self.quality_badge, self.record_chip),
                                         trailing=self._table_max_btn)
        table_panel = self._headered(self._table_header, (self.table_stack, 1))

        # MAP: identity + ⛶ in the header, every control in a toolbar of its own. Handlers live in
        # MapView; the rainbow channel is a LABELLED combo (Off · Speed · Δ · Grip) so Grip is
        # discoverable, not a blind 4th cycle step.
        #
        # A QPushButton centre-clips its label rather than eliding it, so a squeezed map header
        # painted "ld sect" / "et sec" — non-words — with nothing to hover for the real meaning. The
        # PanelToolbar pins each control at its sizeHint width (QSizePolicy.Fixed), so that squeeze
        # is now refused by the layout rather than absorbed by the glyphs; these tooltips are the
        # belt to that braces, and the DESTRUCTIVE one (Reset sectors wipes the user's lines) must
        # never be a mystery word. Set here rather than in map_view because this file owns how these
        # controls are mounted into the panel.
        for b, tip in ((self.map.add_sector_btn,
                        "Add sector: drop another sector line on the map. Sector splits then "
                        "appear per lap in the Laps table (session-best splits in purple)."),
                       (self.map.reset_sectors_btn,
                        "Reset sectors: remove every sector line you have placed on this "
                        "recording. The start/finish line is not affected.")):
            b.setToolTip(tip)
        # (snap_btn and rainbow_combo already carry MapView's own tooltips — left alone.)
        # No GPS trace = nothing on the map to place a line on: the line tools go off, their hover
        # saying why (they used to answer with a success over an empty map). Fixed per session —
        # a new recording builds a new view.
        if data_quality.no_start_line(self.session):
            for b, name in ((self.map.snap_btn, "Snap to track"),
                            (self.map.add_sector_btn, "Add sector"),
                            (self.map.reset_sectors_btn, "Reset sectors")):
                b.setEnabled(False)
                b.setToolTip(f"{name}: {data_quality.NO_LAPS_NO_GPS_BODY}")
        self._map_max_btn = self._maximize_button()
        self._map_header = PanelHeader("MAP", trailing=self._map_max_btn)
        self._map_toolbar = PanelToolbar(self.map.rainbow_combo, self.map.snap_btn,
                                         self.map.add_sector_btn, self.map.reset_sectors_btn)
        # The Lap race's visible entry (it had none: View ▸ Lap race, R or ⌘K only), on the bar of
        # the panel it is drawn on. ph.flag: the race card has no glyph of its own for the race,
        # and the bundled Phosphor 1.3 has no checkered flag.
        self.race_btn = _MapRaceButton(self._map_toolbar)
        # Trust strip over the map: ONE compact strip, two tiers of concern, so it never eats a
        # third of the ≤320px map (the common first-run case: unknown track + older GoPro would
        # stack two word-wrapped banners). The ACTIONABLE line leads (provisional_banner — "place
        # the start line", amber CTA style); the INFORMATIONAL data-quality line (quality_banner —
        # media-clock / low-GPS FYI, calmer #InfoBanner style) sits under it as a quiet sub-note.
        # Each is a single compact line (full detail in the tooltip), each shown only when its
        # concern applies, so when just one applies the strip reads exactly as before. See
        # refresh_timing_trust for the show/hide logic. The on-canvas dashed cue in MapView is the
        # primary, most-direct call-to-action for the provisional concern.
        #
        # ACTIONABLE tier (amber CTA): drag the start/finish line to fix the timing.
        self.provisional_banner = QLabel(
            "Lap timing is unverified — drag the start/finish line on the map to where a lap begins.")
        self.provisional_banner.setObjectName("ProvisionalBanner")
        self.provisional_banner.setWordWrap(False)
        self.provisional_banner.setToolTip(
            "The start/finish line was auto-fitted because this track isn't in the database, so "
            "every lap time, split and 'best' is measured from an arbitrary point. Drag the line "
            "on the map to where a lap begins to fix the timing; it's then remembered for this "
            "recording. Save it as a track (File ▸ Save as track…) so future recordings here "
            "auto-detect it.")
        # INFORMATIONAL tier (calmer #InfoBanner, NOT a call-to-action): timing ACCURACY — the
        # SECOND, orthogonal concern (media-clock fallback / low GPS quality). A pure FYI: there's
        # nothing to "do", so it wears the quiet informational style rather than the amber CTA one.
        # Shown (one compact line summarising the active concern[s]) only when the timing quality is
        # degraded; a normal GPS9, clean-fix recording keeps it hidden, so the map reads identically.
        self.quality_banner = QLabel("")
        self.quality_banner.setObjectName("InfoBanner")
        self.quality_banner.setWordWrap(False)
        self.quality_banner.setVisible(False)
        # The media-clock clause quotes data_quality's one bound, the figure the banner line
        # itself carries (it used to say the clock "runs fast and compresses every lap"; the
        # measured rate difference is ~27 ppm — see data_quality.MEDIA_CLOCK_LAP_ERROR).
        self.quality_banner.setToolTip(
            "Timing accuracy is degraded for this recording. On a GoPro without GPS9 "
            "(Hero 5 through Hero 10, the Fusion and the original Max) the lap times come from "
            "the video clock, which places each GPS fix only to about ±0.05 s, a few further, so a "
            f"lap can read {data_quality.MEDIA_CLOCK_LAP_ERROR} off; and when many GPS fixes are "
            "rejected the positions are less accurate. The lap times are still shown (and "
            "de-emphasized), but "
            "treat them as estimates — they are most reliable on a GPS9 camera, which by GoPro's "
            "spec means a Hero 11, Hero 13, MAX2 or MISSION 1 (tested on a Hero 13).")
        # Both tiers live in ONE strip container (a single bottom hairline; the two lines stack
        # tight), so the map sees a compact strip whether one or both concerns apply.
        self._trust_strip = QWidget()
        self._trust_strip.setObjectName("TrustStrip")
        strip_lay = QVBoxLayout(self._trust_strip)
        strip_lay.setContentsMargins(0, 0, 0, 0)
        strip_lay.setSpacing(0)
        strip_lay.addWidget(self.provisional_banner)
        strip_lay.addWidget(self.quality_banner)
        map_panel = self._headered(self._map_header, (self._trust_strip, 0), (self.map, 1),
                                   toolbar=self._map_toolbar)
        # Tab in VISUAL order: "Lap race" sits left of the toolbar's right-aligned run but was built
        # after it, so Tab visited it after Reset sectors. Moved to just after the header's button,
        # i.e. before the colour combo (one panel now, so both are in one focus chain).
        QWidget.setTabOrder(self._map_max_btn, self.race_btn)

        # CHARTS: identity + the hero Δ/speed readout + ⛶ in the header; every control below it.
        #
        # This panel is the reason the app has a header/toolbar split at all. Its bar used to carry
        # the identity label, the 391 px hero readout, the hero's reference toggle, two chart
        # toggles, the x-axis combo and ⛶ in ONE row, inside a quadrant that is 917 px at the app's
        # own 1440x900 default — over-subscribed by construction. Surviving that took a four-tier
        # degradation ladder (_measure_plots_budget / _fit_plots_header and a resize eventFilter)
        # that traded the panel's identity away for control text: full label -> icon-only toggles ->
        # short chip -> NO LABEL AT ALL. The ladder is deleted; the controls have a row of their own
        # and identity never competes with them again.
        #
        # The label tracks the chart's actual baseline (plots_view flips to Δ-to-ideal when the best
        # lap is selected alone) — a static "Δ TO BEST" would contradict the axis — via
        # _set_delta_baseline_label on deltaBaselineChanged.
        self._plots_label = QLabel(_PLOTS_LABEL_BEST)
        self._plots_label.setProperty("role", "BarLabel")
        # Every wording abbreviates ("Δ TO REF" most of all), so the hover carries the sentence —
        # including WHICH recording, which no header-sized wording has room for. Seeded here so the
        # label is hoverable before the first deltaBaselineChanged arrives.
        self._plots_label.setToolTip(self._delta_baseline_tip())
        # Which baseline the LOWER CHART is drawing (plots_view.deltaBaselineChanged), as a KIND.
        # Not the same thing as the hero readout's reference, which is the ideal_readout_btn's
        # business — the bar showing one while the chart draws the other is exactly what made it
        # ambiguous.
        self._plots_baseline_kind = plots_view.DELTA_BASELINE_BEST
        # The two chart toggles keep the accessible name the icon-only tier used to depend on: the
        # tooltips still quote it, tests still assert it, and it costs nothing to state out loud
        # what a toggle is. They no longer ever lose their text.
        for btn in (self.plots.brake_throttle_btn, self.plots.ideal_btn):
            btn.setAccessibleName(btn.text())
        # The x-axis combo was the one control in the bar with NO tooltip, so "x: dista" was the
        # only cue for what it does. Set here, not in plots_view, because this file mounts the
        # control into the panel.
        self.plots.x_mode_combo.setToolTip(
            "Chart x-axis: DISTANCE around the lap (the same corner lines up across laps) or TIME "
            "into the lap (matches the video clock).")
        self._plots_max_btn = self._maximize_button()
        self._plots_header = PanelHeader(self._plots_label, status=(self.diff_box,),
                                         trailing=self._plots_max_btn)
        # ideal_readout_btn leads the toolbar: it is the control that decides which reference the
        # hero readout above it leads with, so it sits directly under the number it governs.
        # The two combos are ONE IDEA — what the x-range is, and what to report over it — so they
        # go in as a tuple (PanelToolbar draws a group at SPACE_XS while the bar is SPACE_S), which
        # is what stops the row reading as four unrelated controls.
        self._plots_toolbar = PanelToolbar(self.ideal_readout_btn,
                                           self.plots.brake_throttle_btn, self.plots.ideal_btn,
                                           (self.plots.x_mode_combo, self.plots.stat_combo))
        plots_panel = self._headered(self._plots_header, (self.plots, 1),
                                     toolbar=self._plots_toolbar)

        # Stash the four panel containers for _layout_panels.
        self._video_panel = video_panel
        self._table_panel = table_panel
        self._map_panel = map_panel
        self._plots_panel = plots_panel

        # Wire each header's maximize button to its panel now that the containers exist (the buttons
        # were built into the headers above). Same action as the dblclick-header filter, made visible.
        self._maximize_buttons: dict[QPushButton, QWidget] = {}
        self._wire_maximize_button(self._video_max_btn, video_panel)
        self._wire_maximize_button(self._table_max_btn, table_panel)
        self._wire_maximize_button(self._map_max_btn, map_panel)
        self._wire_maximize_button(self._plots_max_btn, plots_panel)

    # ----------------------------------------------------- build phase 2: layout
    def _layout_panels(self):
        """Assemble the 2x2 nested splitter grid, install the dblclick-to-maximize header filters,
        set this widget's layout to the main splitter."""
        video_panel = self._video_panel
        table_panel = self._table_panel
        map_panel = self._map_panel
        plots_panel = self._plots_panel

        # Layout favours the analytical core over the video: left column ~40% / right ~60%, and
        # within them the tabbed lap panel and the charts get the majority. The video's default
        # is ~16:9 at the default column width; the reclaimed height goes to the tabs.
        left = QSplitter(Qt.Vertical)
        left.addWidget(video_panel)
        left.addWidget(table_panel)
        left.setStretchFactor(0, 46)
        left.setStretchFactor(1, 54)
        left.setSizes([390, 450])

        right = QSplitter(Qt.Vertical)
        right.addWidget(map_panel)
        right.addWidget(plots_panel)
        right.setStretchFactor(0, 38)
        right.setStretchFactor(1, 62)
        right.setSizes([320, 520])

        # NEITHER COLUMN TAKES AN EXPLICIT MINIMUM. Both floors are the ones Qt derives from the
        # panels themselves.
        #
        # "Squeezed headers degrade gracefully" was the assumption, and it was false. The right
        # column used to be pinned at 360 px — below the hero readout's own 391 px floor — so at
        # that minimum QHBoxLayout resolved the shortfall by letting the children OVERLAP: the amber
        # "vs ideal" chip painted straight through the Δ number and the controls past it painted
        # "ake," / "Ide" / "x: (". The fix at the time was a measured budget that computed the bar's
        # tightest survivable tier and pushed it back in here as a minimumWidth.
        #
        # An explicit setMinimumWidth is the wrong instrument for that, and this is why: qSmartMinSize
        # takes a widget's EXPLICIT minimum over its minimumSizeHint, so a number set here does not
        # merge with what the children actually need — it REPLACES it, and any shortfall comes out of
        # the children's glyphs. The honest minimum is the one Qt derives from the panels themselves,
        # and now that the controls sit in their own toolbar it is a number worth accepting: the
        # column clamps at its widest row: the charts toolbar, 553 px at 1280x800 (the charts header
        # — identity label + the hero's 330 px floor + ⛶ — needs 496 since the hero paints in Inter +
        # tnum). That is LESS than the 675-759 px the ladder used to compute, so the user can drag the
        # lap panel WIDER than before, not narrower.
        #
        # THE LEFT COLUMN CARRIED THE SAME DEFECT, under a comment asserting it did not ("the left
        # column's 280 is unchanged and unrelated — nothing in it is over-subscribed"). It was
        # over-subscribed by 12 px: the lap panel's honest minimum is 292 (SPACE_S + the tab bar's
        # own 240 + SPACE_S + the 28 px ⛶ + SPACE_S), and 280 REPLACED it. The shortfall came out of
        # the glyphs exactly as the paragraph above predicts — the tab bar was handed 228 px for a
        # 240 px identity, so Qt raised its two scroll arrows and hid part of "Coaching" behind
        # them. Four tabs at ElideNone in a 280 px panel is not a constraint the app had; it is a
        # constraint the number invented. The derived floor was 292 then and costs the window 12 px
        # of minimum width, which is the price of the lap panel always being able to say what page
        # you are on. It is DERIVED, so #269's fifth tab (Marks) moved it without an edit here —
        # 240/292 are the four-tab measurement this defect was found at, not a current budget.
        # (theme.py's QTabBar::scroller rule fixes the arrows themselves, for the sizes a
        # different font stack can still reach.)

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setStretchFactor(0, 40)
        main.setStretchFactor(1, 60)
        # 515/917 sums to 1432 = the real usable width at the 1440 default window (1440 minus the
        # 8px splitter handle). The charts column's honest minimum is ~569 — it was 917 before the
        # header learned to shed its controls into a toolbar — so 917 is a comfort target with
        # headroom, not a floor, and the lap panel takes the rest: enough width for every Laps
        # column with no horizontal scrollbar. The old [576, 864] was aspirational — the right
        # column's hidden minimum overrode it to ~[394, 1046] on every launch. The column minimums
        # let the USER trade either way; only the default has to be clip-free.
        main.setSizes([515, 917])
        # Those column minimums are only a floor if Qt is told to honour them. With Qt's default
        # childrenCollapsible, a drag that overshoots a minimum COLLAPSES the section to 0 instead
        # of clamping: past about +740 px one ordinary drag of the main handle deleted the whole
        # right column (MAP + CHARTS), the 400 ms debounce below persisted [1432, 0] to prefs.json,
        # and every relaunch reopened with those panels gone — recoverable only via the 8 px handle
        # now pinned against the window's own resize hot zone. Non-collapsible turns that same drag
        # into a polite clamp at the columns' minimums. (Maximize still needs a real 0 — see
        # _collapse_sizes.)
        for splitter in (main, left, right):
            splitter.setChildrenCollapsible(False)
        # The user's persisted grid layout (a drag used to be lost on every reload, which read
        # as "the panels cannot be resized") is applied on FIRST SHOW, not here: before the
        # window sizes this widget, the splitters sit at tiny defaults and min-size clamping
        # would distort the restored ratios. See showEvent/_apply_grid_sizes.
        self._pending_grid_sizes = (self._initial_grid_sizes
                                    if self._initial_grid_sizes
                                    and len(self._initial_grid_sizes) == 3 else None)
        # The first-show restore's two deferred passes live here (see showEvent): OWNED timers, so
        # a reload inside their window cancels them with this widget instead of firing into a
        # deleted C++ object.
        self._grid_restore_timers: list[QTimer] = []
        # Debounced persistence: splitterMoved fires continuously during a drag, so coalesce
        # into one gridSizesChanged after the drag goes quiet (the window writes prefs).
        self._grid_save_timer = QTimer(self)
        self._grid_save_timer.setSingleShot(True)
        self._grid_save_timer.setInterval(400)
        self._grid_save_timer.timeout.connect(
            lambda: self.gridSizesChanged.emit(
                [main.sizes(), left.sizes(), right.sizes()]))
        for splitter in (main, left, right):
            splitter.splitterMoved.connect(
                lambda *_a: self._grid_save_timer.start())
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(main)

        # Double-clicking a panel header maximizes that quadrant (toggle to restore); the handler
        # maps a header back to its panel + column via _header_routes.
        self._main_splitter = main
        self._left_splitter = left
        self._right_splitter = right
        self._maximized_panel = None          # the currently-maximized panel, or None
        self._saved_splitter_sizes = None     # (main, left, right) sizes captured at maximize
        # The first-open debrief (show_debrief): on, waiting for the first show to maximize, and
        # the tab it returns to. `_tab_quiet` keeps its tab flips out of the persisted preference.
        self._debrief = False
        self._debrief_pending = False
        self._debrief_return_tab = None
        self._tab_quiet = False
        self._header_routes = {}
        self._install_header_dblclick(video_panel, left, main)
        self._install_header_dblclick(table_panel, left, main)
        self._install_header_dblclick(map_panel, right, main)
        self._install_header_dblclick(plots_panel, right, main)

    # ----------------------------------------------------- build phase 3: signals
    def _wire_signals(self):
        """Cross-panel signal/slot wiring + the shared PlaybackState (the controllers built next
        read/write it). The ~30 Hz tick timer lives on StudioWindow and delegates to self.tick()."""
        # positionChanged is on the decode/present path, so it must do almost nothing (just record
        # the latest time); the ~30 Hz tick applies the map/plot/readout off that path.
        self._playback = PlaybackState()
        self.video.positionChanged.connect(self._on_position)
        self.map.timing_lines_changed.connect(self._on_lines)
        self.table.laps_selected.connect(self._on_user_select)
        self.table.selection_capped.connect(self.statusNotice)
        self.table.slow_corner_clicked.connect(self._on_slow_corner_clicked)   # the "▼ C5" cue
        self.table.slow_pass_requested.connect(self._on_slow_pass_requested)  # ...plays its pass
        # Video focus (the ⤢ button / a double-click on the video): toggle the "fill the screen"
        # gesture. False until the user asks for it.
        self._video_focused = False
        self.video.videoFocusRequested.connect(self.toggle_video_focus)

    # ----------------------------------------------------- build phase 4: controllers
    def _build_controllers(self):
        """Build the compare + scrub controllers (shared PlaybackState, cross-injected) and wire
        their signals + per-tick feeds."""
        # Compare: side-by-side panes behind the toggle; primary drives telemetry, secondary is
        # video-only. While comparing, auto-follow's lap re-point is suspended so the pinned
        # panes/charts don't thrash across lap boundaries.
        self.compare = CompareController(
            self.session, self.video, self.plots, self.table,
            playback=self._playback,  # F5: the shared cursor (reads applied_t, writes followed_lap)
            select_default=self._select_default,
            map_view=self.map,  # F4: the compare ghost (lap B's kart) on the track map
            on_pair_changed=self._refresh_driving_channels,
        )
        # Scrub: dragging a plot cursor seeks within the current lap (<=1 seek/tick); distance-locked
        # across both panes in compare.
        self.scrub = ScrubController(
            self.session, self.video, self.plots, self.map,
            apply_readout=self._apply_readout,
            playback=self._playback,  # F5: the shared cursor (reads + seeds applied_t on release)
        )
        # Mutually referential: scrub queries compare's on/off + pinned (A,B) for the distance-lock;
        # compare bypasses its (t_a,t_b) early-out while a scrub drag is in flight.
        self.compare.set_scrub(self.scrub)
        self.scrub.set_compare(self.compare)

        self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
        # Feed the slider each valid lap's start/end on the global clock as lap-ruler ticks.
        self._push_lap_ruler()
        # …and the same bar's GPS-quality strip, from the SAME session. It is pushed here, beside
        # the lap ticks, because both annotate the scrub axis and both are session facts the view
        # is handed rather than derives.
        self.video.set_quality_timeline(self.session.quality_timeline)
        # The slider + ←/→ seek pane A; in compare distance-lock the same move to pane B (the hook
        # no-ops outside compare, so wiring it once here is safe).
        self.video.set_compare_seek_fanout(self.compare.fanout_seek_b)
        # FIRST: a played pass's zoom ends before compare draws its pair (either way the toggle goes).
        self.video.compareToggled.connect(lambda _on: self.end_pass_zoom())
        # The player says when the pass stops being its subject (a seek away, a lap pick, Compare,
        # a Lap race, Esc): the sentence that named the pass goes with it (`_hold_status`).
        self.video.pane.passChanged.connect(self._on_pass_maybe_left)
        self.video.compareToggled.connect(self._on_corner_compare_left)
        self.compare.on_anchor_dropped = self._on_anchor_dropped
        self.video.compareToggled.connect(self.compare.on_toggled)
        # Compare owns the two-pane stage, which the video-focus maximize can't sensibly frame, so a
        # compare-ON exits video focus first (wired AFTER on_toggled so compare has already entered
        # when this runs, and set_video_focus's compare-guard no longer blocks the exit).
        self.video.compareToggled.connect(self._exit_video_focus_on_compare)
        self.video.paneRepointRequested.connect(self.compare.on_pane_repoint)
        self.plots.scrubStarted.connect(self.scrub.on_started)
        self.plots.scrubMoved.connect(self.scrub.on_moved)
        self.plots.scrubEnded.connect(self.scrub.on_ended)
        # F2: keep the sector guide lines in sync; plots_view is pacer-free, so we compute the
        # boundary positions via session and recompute when the axis mode flips (units change).
        self.plots.modeChanged.connect(self._refresh_sector_lines)
        self.plots.deltaBaselineChanged.connect(self._set_delta_baseline_label)
        self._hovered_corner: str | None = None   # see _on_corner_hovered
        self.plots.cornerHovered.connect(self._on_corner_hovered)
        self.map.cornerHovered.connect(self._on_corner_hovered)
        # The driver took one view over by hand: it stays theirs (each view already let go of its
        # own zoom, so its half of end_pass_zoom is a no-op) and the other goes back.
        self.plots.heldFrameTaken.connect(self.end_pass_zoom)
        self.map.passZoomTaken.connect(self.end_pass_zoom)
        self._build_race()

    def _build_race(self):
        """The Lap Race (studio/race_controller.py): its card floats on the map's top-left, its
        clock rides this view's tick. Compare ends it (the two cannot share the map), and a Laps
        table click during a race re-points its emphasis."""
        self.race_card = RaceCard(self.map.widget)
        self.race_card.move(theme.SPACE_S, theme.SPACE_S)
        self.map.attach_race_card(self.race_card)    # so the map's clean grabs leave it out
        self.race = RaceController(self.session, self.map, self.race_card,
                                   pause_video=self.video.leave_pass,
                                   on_change=self.raceChanged.emit)
        self.race_card.playToggled.connect(self.race.toggle_play)
        self.race_card.restartRequested.connect(self.race.restart)
        self.race_card.speedCycled.connect(self.race.cycle_speed)
        self.race_card.scrubbed.connect(self.race.scrub)
        self.race_card.closeRequested.connect(self.end_lap_race)
        self.video.compareToggled.connect(lambda on: self.end_lap_race() if on else None)
        self.table.laps_selected.connect(
            lambda ids: self.race.set_selected(ids[0]) if ids and self.race.active else None)

    # ----------------------------------------------------- panel focus / maximize
    def _install_header_dblclick(self, panel: QWidget, column: QSplitter, main: QSplitter):
        """Install a dblclick-to-maximize event filter on the panel's header (first layout child)
        and record its (panel, column, main) route. No-op for a header-less panel."""
        item = panel.layout().itemAt(0)
        header = item.widget() if item is not None else None
        if header is None:
            return
        self._header_routes[header] = (panel, column, main)
        header.installEventFilter(self)

    def eventFilter(self, obj, event):
        """Catch a double-click on any registered panel header and toggle that panel's maximize.
        Everything else passes through untouched (return the base implementation).

        This filter used to have a second job: watching the charts header's Resize / StyleChange so
        the width-budget fit pass could re-run. Both the pass and its trigger are gone — the charts
        controls live in a PanelToolbar, so the header's contents no longer depend on its width."""
        if (event.type() == QEvent.MouseButtonDblClick
                and obj in getattr(self, "_header_routes", {})):
            panel, _column, _main = self._header_routes[obj]
            self._toggle_panel_maximized(panel)
            return True
        return super().eventFilter(obj, event)

    def _toggle_panel_maximized(self, panel: QWidget):
        """Toggle panel between filling the window and the 2x2 grid: maximize snapshots the splitter
        sizes then collapses the other sections; restore puts them back. No-op if panel isn't in the
        grid; safe to drive programmatically."""
        routes = getattr(self, "_header_routes", {})
        # Resolve the panel's owning column by scanning the route values (only four entries).
        column = None
        for p, c, _m in routes.values():
            if p is panel:
                column = c
                break
        if column is None:  # panel not part of the current grid — nothing to do
            return

        if self._maximized_panel is panel:
            # RESTORE: this panel is currently maximized → put the saved grid sizes back.
            self._restore_splitter_sizes()
            return
        if self._maximized_panel is not None:
            # A DIFFERENT panel is maximized → restore the grid first, then maximize this one fresh
            # from the true (un-collapsed) sizes (so re-maximizing doesn't snapshot a collapsed grid).
            self._restore_splitter_sizes()

        # MAXIMIZE. Snapshot the live sizes so restore is exact, then drive each splitter so only the
        # section(s) leading to `panel` keep height/width and the rest collapse to 0.
        self._saved_splitter_sizes = (self._main_splitter.sizes(),
                                      self._left_splitter.sizes(),
                                      self._right_splitter.sizes())
        in_left = column is self._left_splitter
        # Main split: keep the column that holds `panel`, collapse the other to 0.
        full_w = sum(self._main_splitter.sizes()) or self._main_splitter.width()
        self._collapse_sizes(self._main_splitter, [full_w, 0] if in_left else [0, full_w])
        # The owning column: keep the panel's section, collapse its sibling. video/map are index 0,
        # table/charts are index 1 in their respective columns.
        top_panels = (self._video_panel, self._map_panel)
        full_h = sum(column.sizes()) or column.height()
        self._collapse_sizes(column, [full_h, 0] if panel in top_panels else [0, full_h])
        self._maximized_panel = panel
        self._sync_maximize_buttons()  # this panel's button now shows the restore glyph

    # ----------------------------------------------------- transient overlays
    #: A panel body must be at least this wide/tall before an overlay is put inside it — a
    #: collapsed section reports 0, and a section a user has dragged nearly shut is not somewhere
    #: to hide a card that deletes itself. One CTRL_H is the smallest thing this app draws.
    _OVERLAY_MIN = theme.CTRL_H

    def overlay_anchor(self) -> QWidget:
        """The widget a transient, session-level card (the PB toast) should sit INSIDE.

        WHICH PANEL, AND WHY IT IS NOT THE MAP. The PB moment is a LAP fact — "1:02.418, 0.317 s
        faster than your previous best" — with two lap actions on it (save the lap card, open this
        track's PB progression). It was landing on the map only because "top-centre of the window"
        happened to point there. The map is also the worst possible host: it is the one panel whose
        every pixel is the DATUM (the racing line, the corner markers, the draggable start/finish
        handles and the map key), it cannot scroll, and its body starts under a header AND a
        toolbar. So the anchor is `table_stack` — the lap panel's body, the surface whose Laps page
        holds the ★ session-best row this card is announcing, whose rows scroll, and which is the
        one quadrant with a page for each thing the toast could send you to.

        (The status bar was the other candidate and it cannot hold this card: it is one line, and
        the toast is a title, a wrapped sentence and TWO actions of which the primary one — "Share
        your PB →" — is the whole retention loop the moment exists for. A one-line status message
        would delete the feature, not move it.)

        FALLBACK, because a panel can be COLLAPSED. Maximizing any other quadrant drives the lap
        panel's splitter section to 0 (see _collapse_sizes), and an overlay inside a zero-height
        widget is an overlay nobody sees. So a maximized panel lends its own body instead, and if
        even that is unavailable the view itself is returned — the card then sits at the bottom of
        the grid, which is still below every header. Returns a widget, never a rect, so the caller
        can map it into whatever coordinate space it is placing in."""
        def usable(w) -> bool:
            # isHidden(), not isVisible(): the second is False for every widget in a window that
            # has not been shown yet, which would send the very first toast to the fallback.
            if w is None or w.isHidden():
                return False
            # ...and its OWN size is not enough either. A maximized panel collapses its siblings'
            # SPLITTER SECTION, not their widgets: measured with the map maximized, the lap panel
            # still reported 280x453 while sitting at (-1, -430) — laid out, sized, and entirely
            # off the top of the window. So the question is how much of it the window can SHOW.
            shown = QRect(w.mapTo(self, QPoint(0, 0)), w.size()).intersected(self.rect())
            return (shown.width() >= self._OVERLAY_MIN
                    and shown.height() >= self._OVERLAY_MIN)

        body = getattr(self, "table_stack", None)
        if usable(body):
            return body
        maxed = getattr(self, "_maximized_panel", None)
        # Each entry is a panel's BODY, not the panel: `self.map` and `self.plots` are the content
        # widgets and exclude their panel's header and toolbar by construction. `self.video` is NOT
        # the equivalent — it carries the transport bars under the picture — so the video panel
        # lends its stage (see VideoView.stage), or the card lands on the transport.
        content = {self._video_panel: self.video.stage, self._table_panel: body,
                   self._map_panel: self.map, self._plots_panel: self.plots}.get(maxed)
        return content if usable(content) else self

    @staticmethod
    def _collapse_sizes(splitter: QSplitter, sizes: list[int]):
        """setSizes for the maximize path, where a 0-width/height section is INTENDED.

        The grid splitters are non-collapsible so no user drag can delete a panel, and Qt applies
        that flag inside setSizes too — a plain setSizes([1432, 0]) clamps to the column minimum
        (measured: [1076, 360]), which would leave a "maximized" panel still sharing the window
        with its sibling. So lift the flag just for this call. Qt latches the collapse on the
        layout struct, so the 0 survives restoring the flag and every later window resize."""
        splitter.setChildrenCollapsible(True)
        splitter.setSizes(sizes)
        splitter.setChildrenCollapsible(False)

    def _restore_splitter_sizes(self):
        """Put the pre-maximize grid sizes back (the inverse of _toggle_panel_maximized's collapse)
        and clear the maximized state. No-op when nothing is maximized / no snapshot exists."""
        sizes = self._saved_splitter_sizes
        if sizes is None:
            return
        self._main_splitter.setSizes(sizes[0])
        self._left_splitter.setSizes(sizes[1])
        self._right_splitter.setSizes(sizes[2])
        self._maximized_panel = None
        self._saved_splitter_sizes = None
        self._sync_maximize_buttons()  # every button reverts to the maximize glyph
        # Every way back to the grid (Esc, ⛶, a header double-click, a Jump, another maximize)
        # ends the debrief the same way: the page is ordinary again, on the remembered tab.
        if getattr(self, "_debrief", False):
            self._end_debrief(restore_tab=True)

    # ----------------------------------------------------- video focus ("fullscreen video")
    def toggle_video_focus(self):
        """Toggle "video focus" — make the video fill the whole SCREEN (a normal-player gesture),
        reached from the ⤢ transport button OR a double-click on the video content, and exited by
        either of those again (or Esc, via the window).

        Approach (the ROBUST one, no risky reparenting of the live media surface): MAXIMIZE the video
        panel into the existing grid via the proven `_toggle_panel_maximized(self._video_panel)` AND
        ask the window to go native-fullscreen (videoFocusChanged). Together the video fills the
        screen with no chrome; the inverse restores the exact pre-focus grid + window state. Both
        paths already restore cleanly (the maximize snapshots/restores splitter sizes; native
        fullscreen is symmetric), and the g-meter overlay — pinned to the video corner by the pane's
        own hooks — re-pins on the resulting resize.

        DISABLED while comparing: compare owns the two-pane stage + the maximize machinery would only
        grow the video quadrant of the grid, not the compare stage — so keep the gesture single-video
        only (the simplest sane behaviour). No-op then."""
        self.set_video_focus(not self._video_focused)

    def set_video_focus(self, on: bool):
        """Enter (True) / exit (False) video focus. Idempotent; a no-op enter while comparing (see
        toggle_video_focus). Drives the panel maximize + the window fullscreen together and reflects
        the resulting state onto the ⤢ button."""
        on = bool(on)
        if on == self._video_focused:
            return
        if on and self._comparing():
            return  # single-video only — compare owns the stage
        # MAXIMIZE/RESTORE the video panel in the grid (the proven path; toggles to the same state).
        # Guarded: only drive the maximize when it actually needs to change so a stale snapshot can't
        # leak (enter maximizes iff not already maximized to the video; exit restores iff it is).
        if on and self._maximized_panel is not self._video_panel:
            self._toggle_panel_maximized(self._video_panel)
        elif not on and self._maximized_panel is self._video_panel:
            self._toggle_panel_maximized(self._video_panel)
        self._video_focused = on
        self.video.set_video_focus_visual(on)   # reflect onto the ⤢ button (no re-emit)
        self.videoFocusChanged.emit(on)          # window goes fullscreen / normal

    def is_video_focused(self) -> bool:
        """True while video focus is active (the video is filling the screen)."""
        return self._video_focused

    def _exit_video_focus_on_compare(self, on: bool):
        """Leave video focus when compare turns ON (the compare stage can't be framed by the
        single-video maximize). No-op on compare-OFF or when focus wasn't active."""
        if on and self._video_focused:
            self.set_video_focus(False)

    # --------------------------------------------------------- unit / palette fan-out
    def set_speed_unit(self, unit: str):
        """Switch the speed display unit live across every session-derived surface: the plots
        speed axis, the lap + corner tables, and the hero #DiffBox readout. Driven by the
        persistent View ▸ Units toggle on the window. No-op if unchanged."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        self.plots.set_speed_unit(unit)
        self.table.set_speed_unit(unit)
        self.corner_table.set_speed_unit(unit)
        self.map.set_speed_unit(unit)
        self.opportunities.set_speed_unit(unit)
        # RAW `_stats`, not the property: this pushes the new unit and re-renders on the next
        # statement, so flushing first would spend 200 ms drawing the page in the unit the user
        # just left. StatsView stores the unit whether or not it renders now.
        self._stats.set_speed_unit(unit)
        # Re-render the readout in place from the last stashed moment (no tick needed).
        self._update_diff_box(self._playback.applied_t, self._last_diff_speed,
                              self._last_diff_lap)

    def refresh_palette(self):
        """Re-render EVERY surface that carries an ahead/behind or best colour after the active
        semantic palette changed (the window has already called theme.set_palette), so a colour-blind
        flip recolours the whole app live with no reload:

          * lap / corner tables — recompute their best + Δ foregrounds;
          * the map — re-pen its rainbow + legend;
          * the Coaching page — its time-lost cells go through theme.delta_colour, so a
            re-render re-colours them (the CPO gap: the coaching front-door stayed red/green);
          * the Stats page — best-lap ★ tint, purple sector bests, the trend sparkline's PB hue;
          * the charts — the brake/throttle band (behind/ahead fills) + the synthetic ideal-lap line
            (best-sector hue) read the palette at draw time, so a refresh re-pens them;
          * the F5 DRIVING CHANNELS — the brake-point glyphs on the chart AND on the map trace are
            drawn from a CACHED (positions, colour, lap_id) tuple that plots_view/map_view were last
            pushed, so — unlike the curves beside them — they cannot follow the flip on their own.
            They have to be re-pushed through _refresh_driving_channels(), whose _driving_lap_colour
            resolves the hue per call;
          * the hero Δ readout — re-style from the last moment.

        COMPLETE SEAM, not a call list to extend at each call site (the pattern refresh_timing_trust
        already follows): flip the palette, call this, and the whole frame agrees. The glyph layer
        was the hole — after View ▸ Colour-blind-safe cues the best lap's curve turned colour-blind
        blue while its own brake markers stayed the standard palette's green, 2,735 px of a hue that
        palette does not contain, on the chart and the map at once. It repaired itself the moment the
        user happened to touch the lap table (a selection re-pushes the channels), which is exactly
        why no test that drives the table ever caught it (QA W4-03).

        Cheap: a handful of in-place repaints / redraws, no session recompute."""
        self.table.refresh()
        self.corner_table.refresh()
        self._refresh_quickest()
        self.map.refresh_palette()
        self.opportunities.refresh()
        self._stats.refresh_palette()   # raw: it re-renders (or defers) on its own — see above
        self.plots.refresh_palette()
        # AFTER plots.refresh_palette(): that re-pens the curves and redraws the glyph items from
        # their cached colour, so the re-push has to land on top of it, not before it.
        self._refresh_driving_channels()
        # Force a Δ-box restyle even if the number is unchanged: the palette flip changes the colour
        # for the SAME delta, so clear the cached colour first.
        self._diff_colour = None
        self._update_diff_box(self._playback.applied_t, self._last_diff_speed,
                              self._last_diff_lap)

    def set_excluded_visible(self, on: bool):
        """Fully show/hide the ⊘ excluded-laps strip from the persistent View-menu item on the
        window. Independent of the strip's own collapsed/expanded one-liner: a menu-hidden strip
        stays hidden regardless (and the strip is still auto-hidden when the session has no excluded
        laps — see LapTable._refresh_excluded)."""
        self._excluded_visible = bool(on)
        self._apply_excluded_visible()

    def _apply_excluded_visible(self):
        """Push _excluded_visible to the lap table's excluded strip. No-op for a partially-built
        view without the table."""
        table = getattr(self, "table", None)
        if table is None:
            return
        table.set_excluded_visible(self._excluded_visible)

    # --------------------------------------------------------- chapter chip / compare chip
    def _update_chapter_label(self, chapter_index: int):
        """Chip text: which chapter the video is in. Suppressed while a seam reopen is in flight
        (the "loading next chapter…" hint owns the chip until the next chapter has presented, at
        which point _on_seam_loading(False) restores this).

        It no longer repeats the RECORDING label. That string is in the window title, permanently,
        and the chip is a qualifier on the word VIDEO beside it — "which chapter", not "which
        file". The transport timecode dropped its own copy of the chapter in the same edit, so the
        fact is now printed once in the window instead of twice."""
        if getattr(self, "_seam_loading", False):
            return
        self.chapter_label.setText(
            chapters.format_chapter(chapter_index, len(self.session.chapters))
            if self.video.is_multi else "")

    def _on_seam_loading(self, loading: bool):
        """Show/clear a brief "loading next chapter…" hint on the chapter chip during a seam
        reopen. On (EndOfMedia → reopen): the amber trust tone, so the momentary hitch reads as
        intentional. Off (next chapter loaded + resumed): restore the normal current-chapter text.
        chapterChanged fires during the switch, so it's gated on _seam_loading to not clobber this."""
        self._seam_loading = bool(loading)
        if loading:
            self.chapter_label.setText("loading next chapter…")
            set_tone(self.chapter_label, "warn")
        else:
            set_tone(self.chapter_label, None)
            self._update_chapter_label(self.video.current_chapter())

    def _on_compare_mode_changed(self, on: bool):
        """The video panel mounted (or dropped) its two-pane compare stage: show the COMPARING chip
        in the identity row, and put the two lap pickers in the tab chain where they belong.

        The pickers are the reason this is not simply `setVisible`. The compare cells are built
        LAZILY and Qt appends a new child to the END of the top-level focus chain, so the two
        controls 40 px under the video panel's own ⛶ were tab stops 16 and 17 of 17 — sixteen
        presses away. setTabOrder puts each one immediately after the widget named first."""
        self._compare_chip.setVisible(bool(on))
        # LOOK-13's hero rule ("in compare, lead with the chart's baseline") depends on THIS flag,
        # and the only re-render it had was a baseline CHANGE: entering compare on a two-lap
        # selection keeps the chart on Δ-to-best, so a paused window kept `Δideal` under
        # `SPEED · Δ TO BEST` until the playhead moved (PRE-1, cooler-2026-11 QA).
        if getattr(self, "_playback", None) is not None and hasattr(self, "_last_diff_speed"):
            self._update_diff_box(self._playback.applied_t, self._last_diff_speed,
                                  self._last_diff_lap)
        picker_a, picker_b = self.video.compare_pickers()
        if on and picker_a is not None and picker_b is not None:
            self.setTabOrder(self._video_max_btn, picker_a)
            self.setTabOrder(picker_a, picker_b)

    # --------------------------------------------------------- selection / poster
    def _select_default(self):
        """Pre-select the two fastest laps so speed + a real delta-to-best show on launch.

        Also clears the auto-follow state: on launch nothing is "current" yet, and after a
        re-segmentation (_on_lines) the lap ids have shifted, so the next playhead movement must
        be free to re-establish the follow on the now-current lap (a stale id would suppress the
        edge). This multi-lap default overlay is simply replaced once the playhead enters a lap."""
        self._playback.followed_lap = None
        rows = sorted(self.session.lap_rows(), key=lambda r: r["time"])
        ids = [r["idx"] for r in rows[:2]]
        self.table.select(ids)
        self._on_laps_selected(ids)

    def _poster_seek(self):
        """Poster the best-lap first frame so the largest quadrant isn't a black void at launch, and
        the map marker / charts / readout reflect a real moment inside a lap. The freshly-built pane
        is still LOADING (and would be StoppedState after): PlayerPane.seek holds the target until
        the media loads, then presents it paused, so the first ▶ plays the best lap (QA VIEW-1).
        Seed applied_t so the next tick sees it as already-applied. No-op when there's no valid
        best lap."""
        best = self.session.best_lap_id()
        if best is None:
            return
        window = self.session.lap_window(best)
        if window is None:
            return
        # Nudge past lap start (see _on_laps_selected) so the ms-quantized seek lands inside the lap.
        target = window[0] + theme.LAP_SEEK_NUDGE_S
        self.video.seek(target)          # lands on load, shown paused (PlayerPane.seek)
        self._playback.latest_t = target
        self._playback.applied_t = target
        self._hero_rest_t = target       # the hero reads the lap's total until this moves
        # Drive the playhead/readout/marker directly so the t=0 state matches the shown frame
        # without waiting for a positionChanged the seek may not emit synchronously.
        self._apply_position(target)

    def _on_user_select(self, ids):
        # A genuine user click in the lap table also jumps the video to that lap (F1).
        self._drop_owned_ring()
        self._forget_quickest_cycle()   # another lap picked: the Quickest row goes back to its count
        self.end_pass_zoom()
        self._on_laps_selected(ids, seek=True)

    # ONE OWNED RING, for the two surfaces whose ring belongs to a lap rather than to a corner row:
    # a slow lap's "▼ C5" cue and a MOMENTS Jump. Either pins through `_pin_owned_ring`, and the
    # next lap the user selects or the next Jump takes it down through `_drop_owned_ring` — only
    # while the map's pin is still that one (a selection change reaches here before the cue's own
    # click, so a click on the cue first drops the old ring, then pins its own). A ring a
    # Corners/Stats row pinned since is that row's and stays. Neither rings under a Lap Race:
    # corner rings are off on its canvas (#561).
    def _on_slow_corner_clicked(self, cid: int):
        self._pin_owned_ring(cid)

    def _pin_owned_ring(self, cid: int):
        if self.is_racing():
            return
        self._ring_corner(cid)
        self._owned_ring = f"C{int(cid)}"

    def _drop_owned_ring(self):
        ring = getattr(self, "_owned_ring", None)
        self._owned_ring = None
        if ring is not None and self.map.pinned_corner() == ring:
            self._ring_corner(None)

    # --------------------------------------------------------- lap-panel tabs
    def _apply_table_mode(self, index: int):
        """One tab = one page: mirror the tab index onto the stack, point the Corners page at
        the current selection lazily on entry (an unvisited Corners page costs nothing), and
        let the window persist the choice."""
        self.table_stack.setCurrentIndex(index)
        if index == 1:
            self.corner_table.set_lap(self._corner_lap)
            # Key 2 onto the remembered Quickest view: its rows take the keys at once.
            page = getattr(self, "corners_page", None)
            if page is not None and page.view() == CORNERS_VIEW_QUICKEST:
                page.quickest.focus_first()
        self._update_table_header()
        if getattr(self, "_debrief", False) and index != 3:
            self._end_debrief(restore_tab=False)   # the driver chose another page
            # ...and left the landing, so the grid comes back exactly as Esc brings it back. Ending
            # the debrief alone kept the lap panel maximized: a click on Laps gave a full-window
            # table with no video, map or charts, and the "Esc returns…" line went with the lead
            # (QA LOOK-1). `_debrief` is already False, so the restore cannot end it a second time.
            if getattr(self, "_maximized_panel", None) is self._table_panel:
                self._restore_splitter_sizes()
        if not getattr(self, "_tab_quiet", False):  # the debrief's own flips are not a choice
            self.lapTabChanged.emit(index)

    def select_lap_tab(self, index: int):
        """Select a lap-panel tab by index (Laps 0 / Corners 1 / Stats 2 / Coaching 3) — the
        window's digit-shortcut + restore entry point. Out-of-range indexes are ignored."""
        if 0 <= index < self.tab_bar.count():
            self.tab_bar.setCurrentIndex(index)

    def _on_stats_corner_clicked(self, cid):
        """A Stats CORNERS-table row click → ring that corner's apex on the map. If the lap
        panel is maximized (the full-window dashboard), restore the grid first so the map
        the ring paints on actually has pixels; a deselect (None) just clears the ring."""
        if cid is not None and getattr(self, "_maximized_panel", None) is self._table_panel:
            self._restore_splitter_sizes()
        self._ring_corner(cid)

    def _on_stats_moment_jump(self, lap_id: int, cid):
        """A Stats MOMENTS row's Jump: select `lap_id` as a Laps-table click does (plots, Corners
        lap, and the video to the lap — or, with `cid`, to that lap's entry to the corner, the
        coaching Jump's seek) and ring `cid` on the map. Maximize-aware like a corner row; it stays
        on the Stats tab, so the next moment is one click away."""
        if getattr(self, "_maximized_panel", None) is self._table_panel:
            self._restore_splitter_sizes()
        self._drop_owned_ring()
        self._forget_quickest_cycle()
        self.end_pass_zoom()
        self.table.select([lap_id])
        entry = (self.session.corners.corner_entry_media_time(lap_id, cid)
                 if cid is not None else None)
        self._on_laps_selected([lap_id], seek=entry is None)
        if entry is not None:
            self.video.seek(entry)
            self._playback.followed_lap = self.session.lap_at_time(entry)
        if cid is not None:
            self._pin_owned_ring(cid)

    def _refresh_quickest(self):
        """Corners ▸ Quickest passes follows every rebuild the Lap view does. getattr-guarded: a
        test double may carry a corner_table and no page."""
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.refresh_quickest()
        # A line drag or a re-segment can MOVE the studied pass (a sector's lines, a corner's
        # edges): the player would still hold the old clip while the readout names the new time,
        # so a pass whose clip is no longer the one held is left, as any other leaving leaves it.
        live = self._live_subject()
        if live is not None:
            held = self._pass_subject[3]
            now = self.session.stretch_clip(live[0], live[1])
            if now is None or max(abs(now[0] - held[0]), abs(now[1] - held[1])) > 1e-6:
                self.video.end_pass()
                self._forget_quickest_cycle()
        self._on_pass_maybe_left()   # a re-segment can take the studied pass's corner away

    def _forget_quickest_cycle(self):
        """The driver went somewhere else (another lap, a Jump): the pass being studied is
        forgotten everywhere — the Quickest row's count, ⌘K's export row, the corner walk."""
        self._pass_subject = None
        self._release_status(_HELD_PASS)
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.quickest.forget_cycle()

    # ------------------------------------------------------------- sentences true for a while
    # A status line that describes a STATE — "Playing lap 13 through C3 … · Esc: whole lap · ⌘K:
    # export this pass", "Comparing lap 13 … · C or Esc: back to lap 13" — is true only while that
    # state holds; the 6 s timer would leave it naming keys that no longer do what it says. ONE
    # mechanism for both: the sentence is posted through `_hold_status` with what it depends on,
    # and the place that sees the state end calls `_release_status`, which takes it down at once
    # (only if it is still the line showing: a newer message is never wiped).
    def _hold_status(self, kind: str, text: str) -> None:
        self._held_status = (kind, text)
        self.statusNotice.emit(text)

    def _release_status(self, kind: str) -> None:
        held = getattr(self, "_held_status", None)
        if held is not None and held[0] == kind:
            self._held_status = None
            self.statusRetracted.emit(held[1])

    def _on_pass_maybe_left(self, on: bool = False) -> None:
        """The player dropped (or swapped) its pass: if no pass is being studied any more, the
        sentence that named it is no longer true."""
        if not on and self.pass_subject() is None:
            self._release_status(_HELD_PASS)

    def _on_clip_ended(self):
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.quickest.set_clip_running(False)
        if getattr(self, "_pass_zoom", None) is not None:
            self._pass_zoom_parked = True    # the next tick's position is where it stopped

    def _on_clip_started(self):
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.quickest.set_clip_running(True)

    def _on_pass_loop_changed(self, on: bool):
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.quickest.set_looping(on)

    def _on_corners_view_changed(self, _view: str):
        self._update_table_header()

    def _play_quickest_row(self, lap_id: int, cid: int, status: str) -> None:
        """A Quickest passes row's play: its pass, and its status line says the pass it names can
        be exported (⌘K lists "Export the C3 pass — lap 13…" exactly while the row names it)."""
        self.play_corner_pass(lap_id, cid, status, export_hint=True, origin=PASS_FROM_QUICKEST)

    # ------------------------------------------------------------- the pass being studied
    # ONE source of truth for "the pass being studied": (lap, corner, where it was played from),
    # set by `play_corner_pass` — the only door every entry point goes through (a Quickest row, a
    # Corners lap row, a CORNERS BY LAP cell, the slow-lap cue, the corner walk, ⌘K) — and
    # forgotten with the Quickest cycle (`_forget_quickest_cycle`: a lap pick, a Jump) or a
    # re-segment. The zoom (#618) has its own, shorter life (the playhead inside the clip); ⌘K's
    # "Export the C5 pass — lap 6…" and the corner walk read this.
    def pass_subject(self) -> tuple[int, int, str] | None:
        """(lap, cid, label) of the pass being studied, or None.

        THE RULE, one for every surface that reads it (⌘K's export row, the right-click menu, the
        corner walk; the zoom, Loop and Slow follow the same pass in the player): the pass is the
        subject from the moment it is played until it is LEFT — a seek away from it, a lap pick, a
        Jump, Compare, a Lap race, ▶ playing on past it, Esc, a reload or a re-segment that takes
        its corner away. The clip merely ENDING (the auto-pause 1 s after the exit) does not leave
        it: he is looking at the corner he just watched, and can still export, loop or walk on
        from it. The player is the authority on "left" (`PlayerPane.pass_clip`, #621): the subject
        holds only while the player still holds that same pass."""
        live = self._live_subject()
        return None if live is None else (live[0], live[1], self._corner_label(live[1]))

    def _live_subject(self) -> tuple[int, int, str] | None:
        """(lap, cid, origin) of `pass_subject`'s pass while the rule above holds, else None."""
        subject = getattr(self, "_pass_subject", None)
        if subject is None:
            return None
        lap, cid, origin, clip = subject
        held = self.video.pane.pass_clip()
        if held is None or abs(held[0] - clip[0]) > 1e-6:
            return None
        if not self.session.has_stretch(cid):   # a re-segment or a sector edit took it away
            return None
        return lap, cid, origin

    def _corner_label(self, cid: int) -> str:
        """"C3", or "S2" for a sector's key (`Session.stretch_label`)."""
        return self.session.stretch_label(cid)

    def _uncounted_why(self, lap_id: int, otherwise: str) -> str:
        """Why `lap_id` has no counted cell, in the words the Quickest hover uses: its ⚠ kind
        (`data_quality.lap_warning_kinds`), else ⊘ excluded, else `otherwise`."""
        kind = data_quality.lap_warning_kinds(self.session).get(lap_id)
        if kind is not None:
            return f"⚠ {data_quality.WARN_REASON[kind]}"
        excluded = getattr(self.session, "excluded_lap_ids", list)() or ()
        return "⊘ excluded" if lap_id in excluded else otherwise

    def lap_pass_refusal(self, lap_id: int, cid: int) -> str | None:
        """Why lap `lap_id`'s pass through corner `cid` cannot be played as a measured pass, or
        None when it can. The rule is the CORNERS BY LAP grid's: a lap that is a row of it (⊘
        excluded and ⚠ laps are not: `_uncounted_why` names which), with a time through the corner, matched on track at
        both edges — the cells the Quickest view counts. A pass is never played on a time the
        grid itself declines to compare. A sector's (`cid` a stats.sector_key) is the SPLITS
        grid's: a row of it with that split."""
        label, lap = self._corner_label(cid), lap_label(lap_id)
        if sector_col(cid) is not None:
            if self.session.split_matrix() is None:
                return f"No sector splits to play here: {label} needs a few clean laps first"
            if self.session.stretch_time(lap_id, cid) is None:
                why = self._uncounted_why(lap_id, "blank")
                return f"Lap {lap} has no counted {label} split ({why})"
            if self.session.stretch_clip(lap_id, cid) is None:
                return f"Lap {lap}'s {label} has no clip"
            return None
        matrix = self.session.corner_matrix()
        if matrix is None or cid not in matrix.cids:
            return f"No corner times to play here: {label} needs a few clean laps first"
        if lap_id not in matrix.lap_ids:
            return (f"Lap {lap} is not counted in the corner times "
                    f"({self._uncounted_why(lap_id, 'not a row of the grid')}), "
                    "so its passes are not played")
        r, c = matrix.lap_ids.index(lap_id), matrix.cids.index(cid)
        if matrix.cells[r][c] is None:
            return f"Lap {lap} has no time through {label}"
        if not matrix.resolved[r][c]:
            return (f"Lap {lap}'s pass through {label} was not matched on track at both edges, so "
                    "its time is not precise enough to play as a pass")
        if self.session.corners.pass_clip(lap_id, cid) is None:
            return f"Lap {lap}'s pass through {label} has no clip"
        return None

    def play_lap_pass(self, lap_id: int, cid: int) -> bool:
        """Play lap `lap_id`'s OWN pass through corner `cid` — a Corners lap row, a CORNERS BY LAP
        cell, the slow-lap cue, the corner walk. Its status names that lap and its own time (the
        grid's cell), never "quickest". Refused, with the reason on the status line, when
        `lap_pass_refusal` says so."""
        why = self.lap_pass_refusal(lap_id, cid)
        if why is not None:
            self.statusNotice.emit(why)
            return False
        t = self.session.stretch_time(lap_id, cid)
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.quickest.forget_cycle()     # a Quickest row no longer names what plays
        status = (f"Playing lap {lap_label(lap_id)} through {self._corner_label(cid)} "
                  f"({t:.{PRINT_DECIMALS}f})")
        return self.play_corner_pass(lap_id, cid, status, export_hint=True)

    def _on_corner_row_clicked(self, cid: int):
        """Corners ▸ Lap N corners, a row click: ring the corner as before (maximize-aware), and
        play the lap's own pass through it — the same click a Quickest passes row answers with."""
        self._on_stats_corner_clicked(cid)
        lap = getattr(self, "_corner_lap", None)
        if lap is not None and cid is not None and not is_selection_click():
            self.play_lap_pass(lap, cid)

    def _on_grid_pass_requested(self, lap_id: int, cid: int):
        """A CORNERS BY LAP cell click plays its pass; a ⌘ / ⇧ click is a selection gesture."""
        if not is_selection_click():
            self.play_lap_pass(lap_id, cid)

    # The right-click menu on a row / cell that names ONE pass. Each resolver answers (lap, cid,
    # play) for the point clicked — `play` the gesture a left click on it makes — or None.
    def _quickest_menu_pass(self, table, pos):
        row = table.indexAt(pos).row()
        q = self.corners_page.quickest
        lap = q.shown_lap(row)
        if lap is None:
            return None
        return lap, q.passes()[row].cid, lambda: q.play_shown(row)

    def _corner_row_menu_pass(self, table, pos):
        cid = self.corner_table.cid_at(table.indexAt(pos).row())
        lap = getattr(self, "_corner_lap", None)
        if cid is None or lap is None:
            return None
        return lap, cid, lambda: self.play_lap_pass(lap, cid)

    def _grid_menu_pass(self, table, pos):
        index = table.indexAt(pos)
        got = self.stats_view.grid_pass_at(index.row(), index.column())
        if got is None:
            return None
        lap, cid = got
        return lap, cid, lambda: self.play_lap_pass(lap, cid)

    def pass_menu(self, target) -> QMenu | None:
        """The menu for `target` = (lap, cid, play): "Play this pass" and "Export this pass…" (the
        overlay export opened on that pass, #619). A per-lap pass the grid declines to compare is
        greyed out in both, with the reason as the menu's tip."""
        if target is None:
            return None
        lap, cid, play = target
        menu = QMenu(self)
        menu.setToolTipsVisible(True)
        play_action = menu.addAction("Play this pass")
        play_action.triggered.connect(play)
        export_action = menu.addAction("Export this pass…")
        label = self._corner_label(cid)
        export_action.triggered.connect(lambda: self.passExportRequested.emit(lap, cid, label))
        compare_action = menu.addAction("Compare with my typical pass")
        compare_action.triggered.connect(lambda: self.compare_pass_with_typical(lap, cid))
        why = self.lap_pass_refusal(lap, cid)
        if why is not None:
            for action in (play_action, export_action):
                action.setEnabled(False)
                action.setToolTip(why)
        # Greyed out with its own reason: level with the typical pass, or it IS the typical pass.
        why_not = self.typical_compare_refusal(lap, cid)
        if why_not is not None:
            compare_action.setEnabled(False)
            compare_action.setToolTip(why_not)
        else:
            v = self.session.pass_vs_typical(lap, cid)
            compare_action.setToolTip(
                f"Lap {lap_label(lap)} and your typical pass through {label}, lap "
                f"{lap_label(v.typical_lap_id)}, side by side from the lead-in")
        return menu

    def _show_pass_menu(self, table, target, pos):
        menu = self.pass_menu(target)
        if menu is not None:
            self._run_menu(menu, table.viewport().mapToGlobal(pos))
            menu.deleteLater()

    def _run_menu(self, menu: QMenu, at: QPoint) -> None:
        """Pop `menu` up at `at` and wait for the choice (the one modal step: a seam for tests)."""
        menu.exec(at)

    def _on_slow_pass_requested(self, lap_id: int, cid: int):
        """The Laps table's "▼ C5" cue: play the culprit corner's pass on that lap (the row click
        under it has already selected the lap and sought its start; this moves on to the corner)."""
        self.play_lap_pass(lap_id, cid)

    def step_corner_pass(self, step: int) -> bool:
        """The corner walk (⌥← / ⌥→): ONE pass per press, never more (§31: no reel). From the pass
        being studied, the previous / next corner — on the same lap, or, when that pass came from
        Corners ▸ Quickest passes, that corner's quickest pass. With none, the corner before /
        after the playhead on its lap. Corners a lap cannot play are stepped over; at either end of
        the lap it stops and says so; on a lap with no pass at all it says the refusal's own why.
        A walk stays in the group it started in: from a SECTOR pass it steps the sectors
        (S2 → S3), from a corner pass or the playhead the corners."""
        subject = self._live_subject()   # a pass that was LEFT walks from the playhead instead
        noun = "corner"
        order = [c.cid for c in self.session.corners.corner_list()]
        if subject is not None and sector_col(subject[1]) is not None:
            noun = "sector"
            order = [sector_key(c) for c in range(self.session.effective_sector_count() + 1)]
        if not order:
            # With no complete lap the corner list is empty too, but the reason is the laps: the
            # sentence every other surface uses for that state, not "no corners".
            self.statusNotice.emit(data_quality.NO_LAPS_HEADLINE if not self.session.valid_lap_ids()
                                   else "No corners on this track yet")
            return False
        if subject is not None:
            lap, here, origin = subject
            k = order.index(here) + step
            candidates = ([] if not 0 <= k < len(order) else
                          order[k:] if step > 0 else order[k::-1])
        else:
            lap = self.session.lap_at_time(self._playback.applied_t)
            origin = None
            if lap is None:
                self.statusNotice.emit("Pick a lap first: the corner walk steps through one lap")
                return False
            t, corners = self._playback.applied_t, self.session.corners
            entries = [(cid, corners.corner_entry_media_time(lap, cid)) for cid in order]
            if step > 0:
                candidates = [cid for cid, e in entries if e is not None and e > t + 0.5]
            else:
                candidates = [cid for cid, e in reversed(entries) if e is not None and e < t - 0.5]
        if origin == PASS_FROM_QUICKEST:
            page = getattr(self, "corners_page", None)
            for cid in candidates:
                if page is not None and page.quickest.play_named(page.quickest.row_of(cid)):
                    return True
        else:
            for cid in candidates:
                if self.lap_pass_refusal(lap, cid) is None:
                    return self.play_lap_pass(lap, cid)
            why = self.lap_pass_refusal(lap, (candidates or order)[0])
            if why is not None and all(self.lap_pass_refusal(lap, c) is not None for c in order):
                # This lap has no pass to play at all (1-2 laps: corner times need a few clean
                # ones), so "the last one with a pass" would be untrue: say the refusal's own why.
                self.statusNotice.emit(why)
                return False
        edge, way = ("first", "earlier") if step < 0 else ("last", "later")
        if origin == PASS_FROM_QUICKEST:
            # This walk crosses laps (each corner's quickest pass), so "on lap N" would be untrue.
            label = self._corner_label(here)
            said = (f"{label} is the {edge} {noun}" if not candidates else
                    f"{label} is the {edge} {noun} with a quickest pass to play")
        else:
            said = f"this is the {edge} one with a pass on lap {lap_label(lap)}"
        self.statusNotice.emit(f"No {way} {noun} to play: {said}")
        return False

    def play_corner_pass(self, lap_id: int, cid: int, status: str = "", *,
                         export_hint: bool = False, origin: str = "lap") -> bool:
        """The Corners tab's Quickest passes: show lap `lap_id`'s pass through corner `cid` and PLAY
        it. The lap is selected as a Stats MOMENTS Jump selects it (charts, map ring), the video
        seeks to the clip start (`CornerModel.pass_clip`: 2 s before the entry, or the previous
        corner's exit if later) and plays, and the player pauses itself 1 s after the exit
        (`VideoView.play_clip`; any seek, play or pause of the driver's own drops that end).

        A Lap race in progress ends and a compare is LEFT first: one video, one lap, and the row's
        click means "show me this pass" wherever the panes were. False when the pass has no clip."""
        clip = self.session.stretch_clip(lap_id, cid)   # a corner's, or a sector's (stats.sector_key)
        if clip is None:
            return False
        if self.is_racing():
            self.end_lap_race()
        if self._comparing():
            self.compare.exit()
        if getattr(self, "_maximized_panel", None) is self._table_panel:
            self._restore_splitter_sizes()
        self._drop_owned_ring()
        self.table.select([lap_id])
        self._on_laps_selected([lap_id], seek=False)
        self.video.play_pass(*clip)   # Loop / Slow, if on, stay on for this pass
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.quickest.set_clip_running(True)
        self._playback.followed_lap = self.session.lap_at_time(clip[0])
        if sector_col(cid) is None:      # a sector has no apex to ring: the map zoom frames it
            self._pin_owned_ring(cid)
        self._zoom_to_pass(lap_id, cid, clip)
        self._pass_subject = (int(lap_id), int(cid), origin, (float(clip[0]), float(clip[1])))
        self._on_live_readout_shown()   # the fullscreen readout names the new subject at once
        if status:
            self._hold_status(_HELD_PASS, self._pass_status(status, export_hint))
        return True

    def _pass_status(self, status: str, export_hint: bool) -> str:
        """THE ONE PLACE a played pass's status line is built: the row's own sentence, then the
        way back while the zoom is on (where his eyes are; the chart readout says it too), then
        the export ⌘K offers for the pass a Quickest row names. Measured at 1280×800 on the demo:
        the longest of the 21 row states is 786 px of the 1280 px bar, so nothing is elided."""
        parts = [status]
        if self.pass_zoomed():
            parts.append("Esc: whole lap")
        if export_hint:
            parts.append("⌘K: export this pass")
        return " · ".join(parts)

    # How far outside a played pass's clip the playhead may report before its zoom ends: the
    # clip's own pause lands within a frame of its end, and a seek quantises to the millisecond.
    _PASS_ZOOM_SLACK_S = 0.25
    # ...and how late the clip's own pause may land and still be "that pass": the real player
    # parked 5-57 ms past the end on the demo; 4K on a loaded Mac can report later. The first
    # position after the clip ended, up to this far past its end, becomes the zoom's end.
    _PASS_PARK_MAX_S = 2.0
    # A played SECTOR frames its line-to-line window with a tenth of its width either side, not the
    # corner's whole width: the demo's sectors with 2-4 lines are 6-18 s of a 45 s lap, so a
    # corner's margin would put most of the lap (or past its ends) on the chart again.
    _SECTOR_FRAME_CONTEXT = 0.1

    # A PLAYED PASS ZOOMS ONTO ITS CORNER: the charts frame the corner's window as `N` and a
    # Coaching Compare do (`PlotsView.frame_span`, one rule) and the map frames the clip's stretch
    # of the played lap. One rule to undo it, the one a driver can predict: the zoom lasts while
    # that pass is the subject — the playhead inside its clip. It stays when the clip pauses itself
    # (he is studying the corner); a lap pick, a Jump, a scrub or play past the clip, Compare, a
    # Lap race, a re-segment or Esc put the whole lap and the whole track back. Taking either view
    # over by hand (a pan, a wheel zoom, `N`, Fit) leaves that view where he put it and gives the
    # other back. Nothing is computed: a window and a box, set once; the tick only compares t.
    def _zoom_to_pass(self, lap_id: int, cid: int, clip: tuple[float, float]):
        window = self.session.stretch_plot_window(cid, self.plots.axis_mode())
        if window is not None:
            self.plots.hold_frame(*window, 1.0 if sector_col(cid) is None else
                                  self._SECTOR_FRAME_CONTEXT)
        self.map.zoom_to_pass(*clip)
        if window is not None or self.map.pass_zoomed():
            self._pass_zoom = (float(clip[0]), float(clip[1]))
            self._pass_zoom_seen = False     # armed by the first tick inside the clip
            self._pass_zoom_parked = False   # set by the clip's end (_on_clip_ended)

    def pass_zoomed(self) -> bool:
        return getattr(self, "_pass_zoom", None) is not None

    def end_pass_zoom(self) -> bool:
        """The whole lap on the charts and the whole track on the map. False when no zoom was on."""
        if getattr(self, "_pass_zoom", None) is None:
            return False
        self._pass_zoom = None
        self.plots.release_frame()
        self.map.unzoom_pass()
        return True

    def escape_pass(self) -> bool:
        """Esc on a played pass: ONE meaning, "back to the whole lap" — the zoom ends AND the pass
        stops being the subject (Loop and Slow off, the speed his again, the row's "playing" /
        "looping" gone). False when no pass zoom was on (Esc then means whatever comes next)."""
        if not self.end_pass_zoom():
            return False
        self.video.end_pass()
        return True

    def _check_pass_zoom(self, t: float):
        """Per tick, two comparisons: the playhead left the pass (a seek, a step, play on past the
        clip's end) → the zoom ends. Armed only once the playhead has been INSIDE the clip, so the
        tick that still reports where the video was before the clip's own seek landed is ignored.
        The first tick after the clip ended is where the pause landed: however late, up to
        _PASS_PARK_MAX_S past the end, it becomes the zoom's end, so the pause he stops to study
        the corner at can never end it (a seek away from the clip lands further, or before it)."""
        zoom = self._pass_zoom
        slack = self._PASS_ZOOM_SLACK_S
        if self._pass_zoom_parked:
            self._pass_zoom_parked = False
            if zoom[1] < t <= zoom[1] + self._PASS_PARK_MAX_S:
                zoom = self._pass_zoom = (zoom[0], t)
        inside = zoom[0] - slack <= t <= zoom[1] + slack
        if inside:
            self._pass_zoom_seen = True
        elif self._pass_zoom_seen:
            self.end_pass_zoom()

    def compare_at_corner(self, tp, lost: str) -> bool:
        """A Coaching row's Compare: SEE the loss the row names. Compare `tp.lap_id` (the lap whose
        pass at corner `tp.cid` is the typical one, pane A) against the best lap (pane B), both
        panes at their own entry to the corner (Jump's seek target), the charts framed on the
        corner as `N` frames a loss, and the corner ringed on the map. It took ~10 actions before:
        Jump put the best lap alone at the corner, C then compared the two FASTEST laps (0.008 s
        apart at the demo's C1 against the row's +0.36 s), and the typical pass never appeared.

        The gates are C's: nothing below two valid laps (the toggle's own enablement), and a Lap
        race in progress ends first (one map, one story — the same end compareToggled triggers).
        A compare already on is left first, and Esc or a lap pick leaves this one as any other.
        False when nothing changed."""
        cid, a, b = tp.cid, tp.lap_id, tp.best_lap_id
        d = PRINT_DECIMALS
        caption = (f"lap {lap_label(a)} ({tp.pass_s:.{d}f}) vs best lap {lap_label(b)} "
                   f"({tp.best_pass_s:.{d}f}) · {self._corner_label(cid)}")
        # The held gap is neutral, as a typical-pass compare's is: the best lap's pass, on a row
        # ranked by its loss, is partly a lucky one (`PassAnchor.held_neutral`).
        if not self._enter_pass_pair(cid, a, b, tp.pass_s, tp.best_pass_s, caption,
                                     held_neutral=True):
            return False
        corners = self.session.corners
        for side, lap in ((0, a), (1, b)):
            entry = corners.corner_entry_media_time(lap, cid)
            if entry is not None:
                self.video.seek_pane(side, entry)
        window = self.session.corner_plot_window(cid, self.plots.axis_mode())
        if window is not None:
            self.plots.frame_span(*window)
        # The keys that leave it, as the typical-pass compare names them (measured at 1280x800 on
        # the demo: the longest of the seven rows is 1003 px of the bar's 1263).
        self._hold_status(_HELD_CORNER, compare_status(tp, lost) + COACHING_COMPARE_KEYS)
        # The hero reads this corner from its entry, as the seam does (`_corner_readout`).
        self._corner_readout_cid = int(cid)
        self._refresh_diff_box()
        return True

    def _enter_pass_pair(self, cid: int, a: int, b: int, pass_a: float, pass_b: float,
                         caption: str, sector_level_s: float | None = None,
                         held_neutral: bool = False) -> bool:
        """Compare lap `a` (pane A, the charts' cursor) with lap `b` AT stretch `cid`, a corner or
        a sector (`stats.sector_key`, whose split step is `sector_level_s`): C's gates (two valid
        laps; a Lap race ends first), the grid restored, any compare or pass zoom left, the pair
        entered, a corner ringed, and both Δ badges and the seam anchored at the pass
        (`compare_controller.PassAnchor`: nothing in the lead-in, the gap since the entry inside
        it, the passes' printed gap held after the exit) under `caption`, the pair named for the
        whole comparison. `pass_a` / `pass_b` are the two passes' times through it;
        `held_neutral` shows the held gap as measured, never coloured (a typical-pass compare and
        a Coaching row's Compare, `PassAnchor.held_neutral`). False when nothing changed."""
        if not self.video.compare_btn.isEnabled():
            return False
        if self.is_racing():
            self.end_lap_race()
        if getattr(self, "_maximized_panel", None) is not None:
            self._restore_splitter_sizes()     # the panes, charts and map are what it shows
        if self._comparing():
            self.compare.exit()
        self.end_pass_zoom()     # enter_pair below does not toggle the button the zoom listens to
        if not self.compare.enter_pair(a, b):
            return False
        sides = []
        for lap, pass_s in ((a, pass_a), (b, pass_b)):
            edges = self.session.stretch_edges(lap, cid)
            clip = self.session.stretch_clip(lap, cid)
            sides.append(None if edges is None or clip is None else (*edges, *clip, pass_s))
        if None not in sides:
            # A sector's Δ is neutral inside the split's own step (`sector_level_s`, the one its
            # compare is offered past); a corner keeps the seam's timing step.
            self.compare.set_pass_anchor(
                self._corner_label(cid), caption, *sides, held_neutral=held_neutral, key=cid,
                **({} if sector_level_s is None else {"noun": "sector",
                                                      "level_s": sector_level_s}))
        if sector_col(cid) is None:      # a sector has no apex to ring
            self._ring_corner(cid)
        return True

    # ------------------------------------------------------------- this pass vs the typical one
    # "What did I do differently?" in one step: THIS pass (pane A, the charts' cursor) beside the
    # corner's typical pass (pane B) — Coaching's typical, the same `typical_pass_lap`, with this
    # lap where the best lap stands — both panes from the lead-in, playing once and pausing at their
    # own clip ends, the charts and the map zoomed on the corner as a played pass is (#618). Offered
    # only where the two printed times are NOT level under the timing step (`stats.is_level`): six
    # of the demo's seven quickest passes tie with the next lap, and a level pair has no difference
    # to look for. Leaving: C, the compare button, Esc or a lap pick — and the charts go back to
    # this lap alone (`_on_corner_compare_left`). Loop and Slow are off in it: compare is not a
    # pass (#621's `set_compare` ends the pass), and two panes looping on different lengths would
    # drift apart. A SECTOR's pass (a Quickest passes sector row, `stats.sector_key`) is the same
    # compare, beside the SPLITS grid's typical split, offered past the split's own wider step
    # (`stats.SECTOR_LEVEL_S`): on the demo with 2, 3 or 4 sector lines, at S1 alone — every
    # other sector's quickest is level with its typical (that step's block in stats.py).
    def typical_compare_refusal(self, lap_id: int, cid: int) -> str | None:
        """Why lap `lap_id`'s pass through `cid` cannot be compared with the typical pass, or None."""
        why = self.lap_pass_refusal(lap_id, cid)
        if why is not None:
            return why
        label, lap = self._corner_label(cid), lap_label(lap_id)
        v = self.session.pass_vs_typical(lap_id, cid)
        if v is None and sector_col(cid) is not None:     # its split is counted (lap_pass_refusal)
            return (f"{label} has too few counted splits for a typical one (it needs a median of "
                    "several laps)")
        if v is None:
            matrix = self.session.corner_matrix()
            col = matrix.cids.index(cid) if matrix is not None and cid in matrix.cids else None
            if col is not None and lap_id in matrix.lap_ids:
                row = matrix.lap_ids.index(lap_id)
                if matrix.cells[row][col] is None or not matrix.resolved[row][col]:
                    return (f"Lap {lap}'s pass through {label} is not counted in the corner "
                            "times (not matched on track), so there is no typical pass to set "
                            "beside it")
            return (f"{label} has too few counted passes for a typical one (it needs "
                    "a median of several laps matched on track)")
        if v.is_typical:
            return (f"Lap {lap}'s pass ({v.pass_s:.{PRINT_DECIMALS}f}) is your typical pass "
                    f"through {label}: there is nothing typical to set beside it")
        if v.level and is_level(v.gap_s, v.level_s):   # what was measured: under the step, as timed
            return (f"Lap {lap}'s pass through {label} is within {v.level_s:.2f} s of your typical "
                    "pass as timed")
        if v.level:   # past the step, inside the step plus the quickest pass's luck (stats)
            return typical_luck_reason(lap, label, v)
        if not self.video.compare_btn.isEnabled():
            return "Compare needs two laps"
        return None

    def typical_compare_title(self, lap_id: int, cid: int) -> str | None:
        """⌘K's row for the pass being studied, "Compare lap 13 through C3 with typical lap 1",
        or None where `typical_compare_refusal` refuses it."""
        if self.typical_compare_refusal(lap_id, cid) is not None:
            return None
        v = self.session.pass_vs_typical(lap_id, cid)
        return (f"Compare lap {lap_label(lap_id)} through {self._corner_label(cid)} with typical "
                f"lap {lap_label(v.typical_lap_id)}")

    def compare_pass_with_typical(self, lap_id: int, cid: int) -> bool:
        """Compare lap `lap_id`'s pass through `cid`, a corner or a sector, with its typical pass
        (see above). Refused, with the reason on the status line, where `typical_compare_refusal`
        says so."""
        why = self.typical_compare_refusal(lap_id, cid)
        if why is not None:
            self.statusNotice.emit(why)
            return False
        v = self.session.pass_vs_typical(lap_id, cid)
        clip_a = self.session.stretch_clip(lap_id, cid)
        clip_b = self.session.stretch_clip(v.typical_lap_id, cid)
        if clip_a is None or clip_b is None:
            self.statusNotice.emit(f"Lap {lap_label(v.typical_lap_id)}'s pass has no clip")
            return False
        self._forget_quickest_cycle()          # the pass being studied is left for the pair
        d, label = PRINT_DECIMALS, self._corner_label(cid)
        caption = (f"lap {lap_label(lap_id)} ({v.pass_s:.{d}f}) vs typical lap "
                   f"{lap_label(v.typical_lap_id)} ({v.typical_pass_s:.{d}f}) · {label}")
        if not self._enter_pass_pair(cid, lap_id, v.typical_lap_id, v.pass_s, v.typical_pass_s,
                                     caption, v.level_s if sector_col(cid) is not None else None,
                                     held_neutral=True):
            return False
        page = getattr(self, "corners_page", None)
        if page is not None:
            # The Quickest highlight names the pass compared, not one picked before it: the arrow
            # keys move the highlight, and ⌘K then compares the pass still studied.
            page.quickest.highlight_pass(lap_id, cid)
        self.video.seek_pane(0, clip_a[0])
        self.video.seek_pane(1, clip_b[0])
        self.video.play()
        # Each pane pauses itself at its OWN clip end (1 s after its exit), once.
        self.video.pane.set_clip_end(clip_a[1])
        if self.video.secondary is not None:
            self.video.secondary.set_clip_end(clip_b[1])
        self._zoom_to_pass(lap_id, cid, clip_a)
        self._corner_compare = (int(lap_id), int(v.typical_lap_id))
        self._hold_status(_HELD_CORNER, typical_compare_status(v, label))
        return True

    def _on_anchor_dropped(self):
        """The compare stopped being "this corner" — left (C, Esc, the button, a lap pick, a
        Quickest row, ⌥→, a Coaching Compare), a pane repointed, or a pane played or scrubbed out
        of its clip. ONE place for every way: the corner-compare state goes, and the status line
        that named the pair is cleared at once rather than lingering its 6 s over a new view."""
        self._corner_compare = None
        self._release_status(_HELD_CORNER)
        if getattr(self, "_corner_readout_cid", None) is not None:
            self._corner_readout_cid = None
            self._refresh_diff_box()      # the lap-start Δ again, without waiting for a move

    def _refresh_diff_box(self) -> None:
        """Re-render the hero for the current moment (no tick needed)."""
        if getattr(self, "_playback", None) is not None:
            self._update_diff_box(self._playback.applied_t, self._last_diff_speed,
                                  self._last_diff_lap)

    def _corner_readout(self, t: float):
        """(corner label, Δ, neutral) while a Coaching row's Compare holds its corner
        (`compare_at_corner`), else None: the hero then reads the Δ since that corner's entry —
        the seam's number (`CompareController.anchored_delta`), so the big readout agrees in sign
        and size with the row that sent him there. It read the Δ since the lap start: "Δbest
        −0.06 s" at C4's entry under a row saying +0.15 s, and +0.08 s at its exit (wave 27).
        Only that compare: a typical-pass compare's pane B is not the best lap, so its seam is not
        a Δbest."""
        cid = getattr(self, "_corner_readout_cid", None)
        compare = getattr(self, "compare", None)
        anchor = None if compare is None or cid is None else compare.anchor
        if anchor is None or anchor.key != cid or not self._comparing():
            return None
        got = compare.anchored_delta(t)
        return None if got is None else (anchor.label, *got)

    def _on_corner_compare_left(self, on: bool):
        """compareToggled: a compare of this pass with the typical one was left (C, the button,
        Esc, a lap pick). The charts go back to this lap alone — unless the driver just picked
        other laps, whose selection is the one that stands."""
        pair = getattr(self, "_corner_compare", None)
        if on or pair is None:
            return
        self._corner_compare = None
        if sorted(self.table.selected_lap_ids()) == sorted(pair):
            self.table.select([pair[0]])
            self.plots.set_laps([pair[0]])
            self._set_corner_lap(pair[0])

    def leave_corner_compare(self) -> bool:
        """Esc on a compare AT A PASS — this pass (a corner's or a sector's) beside the typical
        one, or a Coaching row's Compare — leaves it, as C does. False when none is on: a plain
        whole-lap compare (C, the button) has no pass anchor, and Esc there keeps its meaning (nothing to back out of)."""
        if not self._comparing() or self.compare.pass_anchor() is None:
            return False
        self.video.compare_btn.click()      # compareToggled(False): the exit, then the restore
        return True

    # ------------------------------------------------------------- the hovered corner
    # ONE corner state across three surfaces. Hovering a corner label on the speed chart, or a
    # corner's label/apex dot on the map, emits `cornerHovered`; this view keeps the one hovered
    # corner and pushes it to both (the map rings it, the chart emphasises its label). With
    # nothing hovered, the chart follows the corner a Corners/Stats/Coaching row rang. No view
    # holds a reference to another; every push is from a mouse event or a row click, never a tick.
    def _ring_corner(self, cid):
        """A corner-row click: ring it on the map (the pinned ring) and let the chart follow."""
        self.map.highlight_corner(cid)
        self._push_corner_emphasis()

    def _on_corner_hovered(self, label):
        if label == self._hovered_corner:
            return
        self._hovered_corner = label
        self.map.set_hovered_corner(label)
        self._push_corner_emphasis()

    def _push_corner_emphasis(self):
        self.plots.set_corner_emphasis(self._hovered_corner or self.map.pinned_corner())

    def show_data_trust(self):
        """The data-quality chip's action: open the Stats page on its DATA TRUST card, with the
        Timing row — the one that says what ESTIMATED / GPS LOW / NO GPS costs — scrolled into view,
        marked, and holding keyboard focus (StatsView.reveal_trust).

        The page is switched through the TAB BAR, exactly as a click on "Stats" or the 3 key does,
        so the stack, the persisted tab choice and the page's own show-time reflow all follow from
        the one path they always have. It does NOT maximize: the chip lives in this panel's header,
        and the reader asked why the lap times beside it are qualified, not for the dashboard."""
        self.tab_bar.setCurrentIndex(2)
        self.stats_view.reveal_trust(TIMING_TERM)

    def show_stats_maximized(self):
        """One action to the full-window statistics dashboard (View ▸ Session statistics):
        select the Stats tab and maximize the lap panel. Invoked again while already showing
        it, it restores the grid (a true toggle, mirroring the ⤢ button); the page itself
        stays on Stats — the tab bar flips it back."""
        if self.tab_bar.currentIndex() == 2 and self._maximized_panel is self._table_panel:
            self._restore_splitter_sizes()
            return
        self.tab_bar.setCurrentIndex(2)
        if self._maximized_panel is not self._table_panel:
            self._toggle_panel_maximized(self._table_panel)

    def show_coaching_maximized(self):
        """Coaching ▸ Opportunities: the Coaching page full-window, where every row also has room
        for its Entry·Apex·Exit bars and its Jump — the page that replaced the modal copy of this
        ranking. A toggle exactly like ``show_stats_maximized``: invoked again while showing it,
        it restores the grid."""
        if self.tab_bar.currentIndex() == 3 and self._maximized_panel is self._table_panel:
            self._restore_splitter_sizes()
            return
        self.tab_bar.setCurrentIndex(3)
        if self._maximized_panel is not self._table_panel:
            self._toggle_panel_maximized(self._table_panel)

    def show_focus_list(self):
        """Coaching ▸ Show focus list, and the Coaching page's one-line list: the page maximized,
        which is where the focus block always has room (QA JOURNEY-1: at the owner's grid the page
        is 719x222, its top rows are reserved first, and the block's budget there is 0 — the item
        switched to that tab and the list stayed hidden). Unlike ``show_coaching_maximized`` it is
        NOT a toggle: "show" run twice still shows. Esc or ⛶ gives the grid back."""
        self.tab_bar.setCurrentIndex(3)
        if self._maximized_panel is not self._table_panel:
            self._toggle_panel_maximized(self._table_panel)

    # ----------------------------------------------------- the first-open debrief (PS-B1)
    def show_debrief(self, pb_line: str | None, promoted: list[int],
                     compare: bool = False, saved: bool = True) -> None:
        """Land on the debrief: the Coaching page, maximized the way ``show_coaching_maximized``
        does it, with its lead and shortlist (``OpportunitiesPanel.set_debrief``; `compare` offers
        "Compare with your previous PB" beside a new PB's line, and `saved` False words the lead
        for the demo's unsaved list). Called only from ``StudioWindow._land_on_debrief``, which
        shows ``LibraryController.landing`` — a first open kept (``opened_new``), not on degraded
        timing, with corners ranked — and is reached two ways: at a load's tail
        (``StudioWindow._on_session_loaded``, after ``update_library``), which covers a whole new
        recording on a trusted line and every open of the demo that has a valid lap; and from
        ``LibraryController.refresh_library_entry`` (``_land_deferred_verdict``), fired by the
        start-line drag (``timingEdited``) and by File ▸ Save as track…, when the untrusted line
        a new recording's verdict waited for is trusted, or a library row the load could not
        write (``WRITE_FAILED``) is written on that retry.

        This and ``_end_debrief`` are the only writers of the debrief's on/off, here and on the
        page (``OpportunitiesPanel.set_debrief``), so the two cannot disagree; a lead decided
        while the debrief is up goes through ``update_debrief_lead``.

        It is a landing, not a choice, so none of it is remembered: the tab flips are quiet (the
        persisted lap-panel tab stays the driver's), and every way back to the grid returns to the
        tab the view opened on (``_end_debrief``). The maximize waits for the view's first show
        when it has not had one — the window calls this at the load's tail, ~80 ms before it,
        when the splitters are still 100x30 placeholders a snapshot would make Esc restore."""
        self._debrief_return_tab = self.tab_bar.currentIndex()
        self._debrief = True
        self._quiet_tab(3)
        self.opportunities.set_debrief(True, pb_line, promoted, compare, saved)
        if self.isVisible():
            self._maximize_debrief()
        else:
            self._debrief_pending = True

    def update_debrief_lead(self, pb_line: str | None, promoted: list[int],
                            compare: bool = False, saved: bool = True) -> bool:
        """Give the open debrief a lead decided after it landed (a first open's PB standing that
        waited for File ▸ Save as track… to name the circuit, ``_land_named_verdict``), in place:
        no second landing, no tab flip. A no-op outside the debrief — a panel-only on would leave
        a page ``_end_debrief`` never ends. True when the debrief took it."""
        if not self.is_debrief():
            return False
        self.opportunities.set_debrief(True, pb_line, promoted, compare, saved)
        return True

    def is_debrief(self) -> bool:
        return getattr(self, "_debrief", False)

    def _maximize_debrief(self) -> None:
        if self._debrief and self._maximized_panel is not self._table_panel:
            self._toggle_panel_maximized(self._table_panel)

    def _end_debrief(self, restore_tab: bool) -> None:
        """The page is ordinary again; `restore_tab` puts the lap panel back on the tab the view
        opened on, unless the driver has already moved it."""
        self._debrief = False
        self._debrief_pending = False
        self.opportunities.set_debrief(False)
        back = self._debrief_return_tab
        if restore_tab and back is not None and back != 3 and self.tab_bar.currentIndex() == 3:
            self._quiet_tab(back)

    def _quiet_tab(self, index: int) -> None:
        """Select a lap-panel tab without telling the window to persist it."""
        self._tab_quiet = True
        try:
            self.tab_bar.setCurrentIndex(index)
        finally:
            self._tab_quiet = False

    def _set_corner_lap(self, lap_id: int | None):
        """Track the lap the Corners view describes — the PRIMARY selected/followed lap.
        Cheap when nothing changed; the table itself only refills on a real lap change.
        Defensive getattrs: a CentralView.__new__'d for a unit test drives
        _follow_current_lap without building the UI (the _comparing() idiom)."""
        if lap_id == getattr(self, "_corner_lap", None):
            return
        self._corner_lap = lap_id
        table = getattr(self, "corner_table", None)
        if table is not None:
            table.set_lap(lap_id)
            self._update_table_header()
        # F5: the primary lap changed → refresh its brake glyphs / coast bands. Skipped while
        # comparing (the compare pair drives the glyphs via on_pair_changed, not the primary
        # lap). Defensive: a __new__'d test view without the views has no map/plots to push to.
        if getattr(self, "map", None) is not None and not self._comparing():
            self._refresh_driving_channels()

    def _delta_baseline_tip(self) -> str:
        """The charts label's hover: the lower chart's baseline spelled out, naming the reference
        RECORDING that no wording short enough for the header has room for. Shares its wording with
        the Δ axis's own tooltip (plots_view._delta_axis_tip) so the two can't drift.
        getattr-guarded: a re-label can arrive on a partially-built view."""
        plots = getattr(self, "plots", None)
        return plots._delta_axis_tip() if plots is not None else ""

    def _set_delta_baseline_label(self, kind: str):
        """Keep the charts header honest about which baseline the lower chart is drawing: the local
        best, the synthetic ideal (plots_view swaps to it when the best lap is selected alone), or
        the cross-recording REFERENCE. `kind` is a plots_view.DELTA_BASELINE_* value; an unknown one
        falls back to "best" rather than raising inside a signal handler.

        One wording per baseline, painted in full — there is no tier to choose any more. What used
        to make this a fit pass was three baselines' worth of text competing with six controls for
        one row's width; the controls have their own row now."""
        label = getattr(self, "_plots_label", None)
        if label is None:
            return
        self._plots_baseline_kind = (
            kind if kind in _PLOTS_BASELINE_LABELS else plots_view.DELTA_BASELINE_BEST)
        label.setText(_PLOTS_BASELINE_LABELS[self._plots_baseline_kind])
        label.setToolTip(self._delta_baseline_tip())
        # LOOK-13: in compare the hero follows this baseline (see _update_diff_box), so a change of
        # baseline re-renders it for the current moment — a paused compare gets no tick to do it.
        playback = getattr(self, "_playback", None)
        if playback is not None and hasattr(self, "_last_diff_speed"):
            self._update_diff_box(playback.applied_t, self._last_diff_speed, self._last_diff_lap)

    def _update_table_header(self):
        """The Corners tab always names WHICH lap its per-corner rows describe — directly on
        the tab text ("Corners · L7", 1-based like every lap number in the app; the old mode
        label showed the raw 0-based id). Defensive getattr for a partially-built view."""
        bar = getattr(self, "tab_bar", None)
        if bar is None:
            return
        lap = getattr(self, "_corner_lap", None)
        page = getattr(self, "corners_page", None)
        if page is not None:
            page.set_lap_text(lap)
        if page is not None and page.view() == CORNERS_VIEW_QUICKEST:
            bar.setTabText(1, "Corners · quickest")
        else:
            bar.setTabText(1, f"Corners · L{lap + 1}" if lap is not None else "Corners")
        # B1: the header bar was laid out BEFORE this load-time rename widened the tab bar's
        # size hint, and a QTabBar doesn't re-request layout on its own — so the last tab
        # ("Coaching") shipped clipped to "Coach" until the user happened to click a tab.
        bar.updateGeometry()
        header = bar.parentWidget()
        if header is not None and header.layout() is not None:
            header.layout().activate()

    def _on_laps_selected(self, ids, seek=False):
        # A lap pick during compare leaves compare first; picking the compared pair itself, or
        # nothing, is not a pick (see _pick_leaves_compare).
        if self._comparing() and not self._pick_leaves_compare(ids):
            return
        # The table multi-selection drives the PLOTS only; the map's current-lap overlay
        # follows the video position (and thus selection, since F1 seeks into the lap).
        self.plots.set_laps(ids)
        # Corners view follows the primary selected lap (ids[0]: the lowest-id selection —
        # the same lap a user-click seek jumps to — or the fastest from _select_default).
        primary = ids[0] if ids else None
        primary_changed = primary != getattr(self, "_corner_lap", None)
        self._set_corner_lap(primary)
        # L4: the speed-chart brake glyphs now cover EVERY overlaid lap, so a selection change that
        # keeps the same PRIMARY lap (which _set_corner_lap early-outs on) must still refresh them.
        # Only when _set_corner_lap didn't already refresh (primary unchanged), and not while
        # comparing (the pinned pair drives glyphs via on_pair_changed).
        if (not primary_changed and getattr(self, "map", None) is not None
                and not self._comparing()):
            self._refresh_driving_channels()
        # F1 seeks ONLY on user selection — not on programmatic re-select from
        # _select_default()/_on_lines(), or dragging a timing line would yank the video.
        if seek and ids:
            # Nudge past the lap start: laps are contiguous and setPosition quantizes to whole ms,
            # so an exact-boundary seek lands in the PREVIOUS lap (the click-selects-wrong-lap bug);
            # theme.LAP_SEEK_NUDGE_S keeps it inside.
            target = self.session.lap_window(min(ids))[0] + theme.LAP_SEEK_NUDGE_S
            self.video.seek(target)
            # Seed followed_lap to the seek's lap so the immediate post-seek tick isn't a lap-change
            # edge that would collapse a just-made multi-lap comparison.
            self._playback.followed_lap = self.session.lap_at_time(target)

    # --------------------------------------------------------- per-frame tick path
    def _on_position(self, t: float):
        # Runs in the video event path — keep it trivial so frame presentation isn't starved.
        self._playback.latest_t = t

    def tick(self):
        """Per-frame (~30 Hz) work, called by StudioWindow's persistent tick timer."""
        # A Lap Race owns the map and its own clock: the video is paused, and a playhead move made
        # meanwhile (a Laps-table click) is applied by the first tick after the race ends.
        race = getattr(self, "race", None)
        if race is not None and race.active:
            # The map left the screen (video focus, another panel maximized): the race has nothing
            # to draw on, so it ends rather than drive an invisible field under Space and Esc.
            if not self.map_on_screen():
                self.end_lap_race()
            else:
                race.tick()
                return
        # Drain a coalesced map marker-drag seek first (one per tick, not per mouse-move).
        self.scrub.drain_marker_seek()
        # While scrubbing, the drag is source of truth: one coalesced seek/tick, skip the playback
        # apply (prevents the drag↔positionChanged feedback loop from oscillating).
        if self.scrub.is_active:
            self.scrub.apply_tick()
            self.compare.tick()  # keep the secondary g + Δ badges live while scrubbing
            return
        # Normal playback: apply an update only when the position actually advanced.
        if self._playback.latest_t != self._playback.applied_t:
            self._playback.applied_t = self._playback.latest_t
            self._apply_position(self._playback.applied_t)
        # Compare mode: the secondary pane is video-only (no _on_position), so feed its g + update
        # both panes' Δ badges from its own current position here, every tick (O(1) np.interp).
        if self.compare.active:
            self.compare.tick()

    def _apply_position(self, t: float):
        if getattr(self, "_pass_zoom", None) is not None:
            self._check_pass_zoom(t)
        self.plots.set_playhead_time(t)
        self._apply_readout(t)

    def _apply_readout(self, t: float):
        # Resolve lap + trace index ONCE per tick and reuse below.
        lap_id = self.session.lap_at_time(t)   # F3: which lap is on the video
        i = self.session.index_at_time(t)      # nearest trace sample (the speed readout)
        self.map.set_playhead_time(t)          # F3: red marker, interpolated between samples
        self._follow_current_lap(lap_id, t)  # charts auto-follow the playhead's lap (vs best)
        self.table.set_current_lap(lap_id)
        self.map.set_current_lap(lap_id)  # highlight the current lap's trace on the map
        sp = float(self.session.tv[i]) if i is not None else None  # F2: speed km/h at that index
        # C6: under-video strip = timecode (+chapter) only; the live Δ/speed/lap lives once in the
        # hero #DiffBox.
        self.video.set_readout(self._transport_readout(t))
        self._update_diff_box(t, sp, lap_id)
        if self.video.live_readout_shown():
            self._push_live_lap(t, lap_id)
        # Gate the g_at_time lookup on the overlay being visible. In compare the pair pins each
        # pane's g-meter lap scope, so skip the per-tick primary pin to keep one driver.
        if not self._comparing():
            self.video.set_gmeter_lap(lap_id)
        if self.video.is_gmeter_visible():
            self.video.set_g(self.session.g_at_time(t))

    def _push_lap_ruler(self):
        """The video's lap ruler: each valid lap's start/end as ticks, and per lap its hover line and
        the ★ / ▼ its Laps-table Lap cell wears (`LapTable.ruler_marks`, one source). Re-pushed by
        the rebuild seam and the timing-trust seam, the two that re-decide those marks."""
        video, table = getattr(self, "video", None), getattr(self, "table", None)
        if video is None:
            return
        marks = table.ruler_marks() if table is not None else {}
        verified = getattr(self.session, "timing_verified", True)
        bounds: list[float] = []
        segments = []
        for lid in self.session.valid_lap_ids():
            w = self.session.lap_window(lid)
            if w is None:
                continue
            bounds.extend(w)
            mark = marks.get(lid, "")
            # A slow lap's hover leads with where its time went, in the words its cue cell's hover
            # uses (`LapTable.slow_note_text` = `story.row_text`), on a line of its own.
            if mark == "★":
                note = " · ★ session best"
            elif mark:
                why = table.slow_note_text(lid) if table is not None else ""
                note = f" · {mark.split()[0]} slow lap" + (f"\n{why}" if why else "")
            else:
                note = ""
            head, _nl, why = note.partition("\n")
            tip = (f"Lap {lap_label(lid)} · {fmt_time(self.session.lap_time(lid))}{head}"
                   + ("" if verified else " · provisional timing") + (f"\n{why}" if why else ""))
            segments.append((w[0], w[1], mark, tip))
        video.set_lap_ticks(bounds)
        video.set_lap_segments(segments)

    def _push_live_lap(self, t: float, lap_id: int | None):
        """The fullscreen readout's lap line and pass line (its Δ run comes from _set_diff_text).

        THE LAP LINE IS THE EXPORT STRIP'S LABEL, "LAP 7   0:23.415": the lap the playhead is in
        and the time since its line, clamped into the lap (export_video._strip_runs), "No lap"
        outside every lap as a follow-laps export says. Muted exactly when the Laps table mutes
        its lap times (`durations_muted`: provisional timing or a degraded clock), so the two never
        treat the same time differently and nothing here is shown that the table withholds. The Δ
        line keeps the hero's colour rule (the 0.07 s step on every clock, as the seam and the
        exports). The pass line names the pass the video is playing, with that lap's own time
        through the corner (`lap_corner_stats`, the Quickest passes view's numbers)."""
        if lap_id is None:
            lap = "No lap"
        else:
            win = self.session.lap_window(lap_id)
            elapsed = 0.0 if win is None else min(max(t - win[0], 0.0), win[1] - win[0])
            lap = f"LAP {lap_label(lap_id)}   {fmt_time(elapsed)}"
        dim = durations_muted(self.session)   # the Laps table's own rule for these times
        self.video.set_live_lap(lap, dim, self._live_pass_line())

    def _live_pass_line(self) -> str:
        """The pass line: the ONE subject (`pass_subject`, whatever started it — a Quickest row, a
        Corners row, a Stats cell, the ▼ cue, ⌥←/⌥→) with that lap's own time through the corner;
        "" once the pass is left, kept while the clip merely auto-paused."""
        subject = self.pass_subject()
        if subject is None:
            return ""
        lap_id, cid, label = subject
        played = (lap_id, cid)
        cache = getattr(self, "_pass_line_cache", None)
        if cache is None or cache[0] != played:
            if sector_col(cid) is not None:
                secs = self.session.stretch_time(lap_id, cid)   # the lap's SPLITS cell
            else:
                corners = self.session.corners
                idx = next((i for i, c in enumerate(corners.corner_list()) if c.cid == cid), None)
                stats = corners.lap_corner_stats(lap_id) if idx is not None else []
                secs = stats[idx].time if idx is not None and idx < len(stats) else None
            took = "" if secs is None or math.isnan(secs) else f" ({secs:.2f})"
            cache = self._pass_line_cache = (played, f"{label} · lap {lap_label(lap_id)}{took}")
        return cache[1] + (" · looping" if self.video.pane.is_looping() else "")

    def _on_live_readout_shown(self):
        t = self._playback.applied_t
        if t is None or not self.video.live_readout_shown():
            return
        self._push_live_lap(t, self.session.lap_at_time(t))

    def _transport_readout(self, t: float) -> str:
        """The transport TIMECODE (C6): the media position, and only that. Deliberately does NOT
        echo speed / Δ / lap — those live in the hero #DiffBox, the single source of the live
        moment — and no longer echoes the CHAPTER either: that is a property of the panel, not of
        the instant, and it is a chip in the panel's identity row now. Keeping both copies would
        have put the same string twice in one panel AND made this label the transport bar's widest
        child (274 px of a 408 px bar at the window's own minimum), which is the width that decides
        whether the timecode can sit inline beside the ▶ it describes at all."""
        return fmt_time(t)

    def _follow_current_lap(self, lap_id: int | None, t: float):
        """Auto-follow the playhead's lap on the charts (current vs best). Acts only on a real
        lap-change edge (O(1)/tick); holds the last lap on None regions; suspended while comparing.
        Table select() is programmatic so it never triggers a user-seek that would fight playback."""
        # Compare mode pins the panes + charts to the chosen pair, so SUSPEND the auto-follow
        # re-point: the playhead crossing a lap boundary must not thrash the pinned [A,B] overlay.
        if self._comparing():
            return
        if lap_id is None or lap_id == self._playback.followed_lap:
            return  # hold on no-lap regions; only act on a genuine change to a new valid lap
        self._playback.followed_lap = lap_id
        # Keep the best lap as the reference overlay; current lap first so it's the primary curve.
        best = self.session.best_lap_id()
        ids = [lap_id] if best is None or best == lap_id else [lap_id, best]
        self.table.select(ids)   # programmatic (signals blocked) → no seek, won't fight playback
        self.plots.set_laps(ids)
        self._set_corner_lap(lap_id)  # the Corners view follows the playhead's lap too
        # During a scrub-across-boundary, set_laps→refresh re-places the cursor via
        # set_playhead_time (force=False), which is a no-op mid-drag; re-place it from the dragged
        # time (force=True) so the cursor stays put in the now-current lap (resolving the old
        # "scrub dead off the displayed lap" caveat too).
        if self.plots.is_dragging():
            self.plots.set_playhead_time(t, force=True)

    def _on_ideal_readout_toggled(self, on: bool):
        """Flip the hero readout between leading with Δ-to-ideal (checked) and Δ-to-best (unchecked),
        then re-render it for the current moment so the swap is immediate (not deferred to the next
        tick). A USER flip is also remembered, so a state that takes the chip away and gives it back
        restores the reference they chose rather than the default (_sync_ideal_readout blocks this
        signal for its own programmatic flips, which is what keeps the two apart)."""
        self._ideal_readout_wanted = bool(on)
        self._update_diff_box(self._playback.applied_t, self._last_diff_speed,
                              self._last_diff_lap)

    def _sync_ideal_readout(self):
        """The ONE owner of the `vs ideal` chip's enabled/checked state, and of the hero's
        knowledge of which ideal state this session is in — the gate #211 applied to five surfaces
        and not to this one (see _IDEAL_ONE_LAP_REASON above).

        Called from rebuild_derived_views, so it tracks a re-segmentation / reference load exactly
        as _sync_chart_controls does for the three chart controls, and reads its two session facts
        ONCE per rebuild rather than on the ~30 Hz tick. `ideal_segment_bests` is memoized on the
        corner service, so both reads are O(1) after the first.

        getattr-guarded on both accessors: CentralView is built over duck-typed sessions in the
        view tests, and a session that cannot answer "is there an ideal" has no ideal."""
        donor = getattr(self.session, "ideal_donor_lap_id", lambda: None)()
        total = getattr(self.session, "ideal_total", lambda: None)()
        self._ideal_state = ("none" if total is None else
                            "one_lap" if donor is not None else "stitched")
        ok = self._ideal_state == "stitched"
        btn = self.ideal_readout_btn
        # APPENDED, never replaced: the chip's own tooltip is the only thing naming what it is.
        # In the working state the appendix is the SAMPLE (_IDEAL_CHIP_SAMPLE); in the two
        # unavailable ones it is the reason there is nothing to reference. Both come off the same
        # getattr-guarded read as the state above — a duck-typed view-test session that cannot
        # answer `ideal_sample` simply gets the constant, exactly as it does for the other reads.
        smp = getattr(self.session, "ideal_sample", lambda: None)() if ok else None
        if smp is not None:
            # `corners` through `plural`: ONE corner is a legal partition (see _signal.plural),
            # and this printed "across 1 corners".
            appendix = _IDEAL_CHIP_SAMPLE.format(donors=smp.donors, laps=smp.laps,
                                                 corners=plural(smp.corners, "corner"))
        elif ok:
            appendix = ""
        else:
            appendix = "\n\n" + (_IDEAL_ONE_LAP_REASON if self._ideal_state == "one_lap"
                                 else _IDEAL_NONE_REASON)
        btn.setToolTip(_IDEAL_CHIP_TIP + appendix)
        btn.setEnabled(ok)
        # Programmatic, so it must not be mistaken for the user's own choice (see the handler).
        want = self._ideal_readout_wanted and ok
        if btn.isChecked() != want:
            blocked = btn.blockSignals(True)
            btn.setChecked(want)
            btn.blockSignals(blocked)
        # Re-render for the current moment once the playback state exists (it does not yet on the
        # first rebuild, which runs inside __init__ before the poster seek).
        if getattr(self, "_playback", None) is not None:
            self._update_diff_box(self._playback.applied_t, self._last_diff_speed,
                                  self._last_diff_lap)

    def _ideal_clamp_note(self, d_ideal: float | None) -> str:
        """The one clause that says the printed Δideal is FLOORED, shown exactly when the floor is
        doing visible work (see _IDEAL_CLAMPED_NOTE). Empty otherwise — a note on every frame would
        be noise about a number that is usually positive."""
        if d_ideal is None or d_ideal >= -theme.DELTA_EVEN_EPS_S:
            return ""
        return _IDEAL_CLAMPED_NOTE.format(raw=abs(float(d_ideal)))

    def _update_diff_box(self, t: float, sp: float | None, lap_id: int | None):
        """Refresh the hero Δ/speed box for the current moment. By default it LEADS with Δ-to-IDEAL
        (the moat number — how far off the driver's own achievable lap they are, right here) via
        theme.format_ideal_readout; the ideal_readout_btn flips it to lead with Δ-to-best (the
        export overlay's theme.format_delta_speed). The number NOT leading is shown in the box's
        tooltip, so both are always one read/hover away (no information removed, just re-prioritized).

        Both deltas are cheap per-tick scalars on the already-resolved lap: delta_at_lap (Δ-to-best)
        and delta_to_ideal_at (Δ-to-ideal, grid-based + memoized envelope), so the ~30 Hz path adds
        only two O(log n) np.interps.

        THE LEAD IS GATED ON THERE BEING AN IDEAL TO LEAD WITH, and on the STATE rather than on the
        chip: the chip is the affordance (disabled + unchecked by _sync_ideal_readout), this is the
        gate. Keyed on the chip alone, one programmatic setChecked would put `Δideal +0.00 s` back
        on the app's largest surface on a recording where the ideal is a lap the driver drove."""
        # Stash the moment so a toggle can re-render without a tick (see _on_ideal_readout_toggled).
        self._last_diff_speed, self._last_diff_lap = sp, lap_id
        t_delta = self._hero_delta_time(t, lap_id)
        d_best = self.session.delta_at_lap(lap_id, t_delta) if lap_id is not None else None
        d_ideal = self.session.delta_to_ideal_at(lap_id, t_delta) if lap_id is not None else None
        on_best = lap_id is not None and lap_id == self.session.best_lap_id()
        stitched = self._ideal_state == "stitched"
        # LOOK-13 (QA 2026-09-26): in compare the header over the charts reads "SPEED · Δ TO BEST"
        # while the hero kept leading with Δideal — two baselines side by side. While comparing,
        # the hero leads with the baseline the lower chart is drawing.
        chart_best = (self._comparing()
                      and self._plots_baseline_kind != plots_view.DELTA_BASELINE_IDEAL)
        reference = self.session.has_reference()
        unit = self._speed_unit
        corner = self._corner_readout(t_delta)
        if corner is not None:
            # A Coaching row's Compare: the Δ since the corner's entry against the best lap (pane
            # B), the seam's number by the seam's rule — none in the lead-in, the held gap after
            # the exit, neutral where the seam is — and the speed slot names the datum.
            label, lead, neutral = corner
            lead_kind = ("corner", label)
            tag = HERO_CORNER_TAG.format(corner=label)

            def render(v):
                run = theme.format_delta_run(v, arrow=not neutral)
                return (_name_hero_baseline(f"{run}     {tag}", False),
                        None if neutral else theme.delta_colour(v))

            text, sem_colour = render(lead)
            tip = (f"Δ to your best lap since {label}'s entry, as the seam under the videos counts "
                   f"it: no number before the entry, the gap through {label} held after its exit. "
                   "C or Esc leaves the compare, and this goes back to the Δ since the lap start.")
        elif stitched and self.ideal_readout_btn.isChecked() and not chart_best:
            lead, lead_kind = d_ideal, "ideal"

            def render(v):
                return theme.format_ideal_readout(v, sp, lap_id, unit)

            text, sem_colour = render(lead)
            # `sigil=False`: this sentence already says "Δ", and the run used to add its own —
            # "Δ to your best lap here: Δ +0.00 s" (§5.6). With a reference loaded `d_best` is
            # measured against the reference lap, so the sentence says so.
            whose = "the reference lap" if reference else "your best lap"
            tip = (f"Δ to {whose} here: {theme.format_delta_run(d_best, sigil=False)}"
                   + self._ideal_clamp_note(d_ideal))
        else:
            lead, lead_kind = d_best, "best"

            def render(v):
                txt, col = theme.format_delta_speed(v, sp, lap_id, unit)
                return _name_hero_baseline(txt, reference), col

            text, sem_colour = render(lead)
            # theme.format_ideal_run, not a local f-string over format_delta_value: that spelling
            # dropped the unit, so the hovered number read `Δideal +1.05` beside a hero that says
            # `Δideal +1.05 s`, and it skipped the display clamp the hero applies. And in the two
            # states with no stitched ideal it is not called at all — it would render
            # `Δideal +0.00 s`, the claim this whole gate exists to stop, one level down in a
            # tooltip where nothing else would contradict it.
            tip = (f"Δ to your ideal lap here: {theme.format_ideal_run(d_ideal)}"
                   + self._ideal_clamp_note(d_ideal)) if stitched else \
                _IDEAL_HOVER_REASONS[self._ideal_state]
            # ...and the SAME honesty for the other reference, which had none. Δ-to-best on the
            # best lap is not "near" zero, it is zero: this lap IS the reference. See the note.
            # Not with a cross-recording reference loaded: then the Δ is against THAT lap and
            # moves on the best lap like on any other, so the note would be false.
            if on_best and not reference:
                tip += _BEST_LAP_BEST_NOTE
        at_rest = t_delta != t
        # WHAT the lead number is a reading of. A roll counts between two readings of the SAME
        # quantity on the same lap, so every intermediate value is one that quantity really took
        # somewhere between the two playhead positions (Δ is continuous along a lap). A change of
        # baseline (Δideal / Δbest / Δref, which reference, compare's chart-best), of LAP (a
        # different lap's curve: no path joins the two readings) or of the at-rest reading cuts
        # straight to the new value — a blend of two quantities is a number that never existed.
        ref_id = getattr(self.session, "reference_label", lambda: None)() if reference else None
        baseline = (lead_kind, ref_id, chart_best, lap_id, at_rest)
        if at_rest:
            tip += _HERO_AT_REST_NOTE
        self.diff_box.setToolTip(tip)

        def paint(v):
            txt, col = (text, sem_colour) if v is lead else render(v)
            self._set_diff_text(_at_rest(txt) if at_rest else txt, col, v)

        self._roll_diff(lead, paint, baseline)

    def _set_diff_text(self, text: str, sem_colour: str | None, value: float | None = None):
        text, sem_colour = _level_readout(text, sem_colour, value)
        self.diff_box.setText(text)
        self.video.set_live_delta(text, sem_colour)   # the fullscreen readout says the same
        # The colour goes on the Δ run ONLY (the speed stays the label's neutral text colour), and
        # only when it changes — no per-tick stylesheet re-layout.
        colour = sem_colour or theme.C.text
        if colour != getattr(self, "_diff_colour", None):
            self._diff_colour = colour
            self.diff_box.set_delta_colour(sem_colour)

    def _still_moment(self) -> bool:
        """True when what just changed was a discrete user move — a seek, a single arrow press —
        the only changes that animate (motion.py). Not playback, not a chart scrub, a marker drag
        or a held arrow key (continuous: the readout must follow the hand), not compare or a Lap
        race."""
        # getattr-guarded like the view's other reads: view tests build CentralView over doubles.
        marker = getattr(getattr(self, "map", None), "marker", None)
        playing = getattr(getattr(self, "video", None), "is_playing", lambda: False)()
        scrub = getattr(self, "scrub", None)
        held = time.monotonic() < getattr(self, "_continuous_until", 0.0)
        return not (playing or held or (scrub is not None and scrub.is_active)
                    or self._comparing() or self.is_racing()
                    or bool(getattr(marker, "moving", False)))

    def _finish_diff_roll(self):
        roll = getattr(self, "_diff_roll", None)
        if roll is not None:
            roll.finish()

    def note_continuous_gesture(self):
        """A held arrow key (auto-repeat) is moving the playhead: for CONTINUOUS_HOLD_S the readout
        follows directly, like a scrub, instead of restarting a roll on every repeat."""
        self._continuous_until = time.monotonic() + CONTINUOUS_HOLD_S

    def _roll_diff(self, lead: float | None, paint, baseline=None):
        """Show `lead` through `paint(value)`: directly, or — on a discrete jump of at least
        motion.ROLL_MIN_JUMP_S at a still moment, under an unchanged `baseline` (same quantity,
        same lap: see _update_diff_box) — counted from the value on screen to it over
        motion.DELTA_ROLL_MS. Every intermediate frame is the SAME formatter fed an intermediate
        value, so the sign and its colour switch together at the zero crossing, and the last frame
        is `paint(lead)`: exactly what the direct path prints. The ~30 Hz playback tick takes the
        direct branch on a float compare and starts nothing."""
        roll = getattr(self, "_diff_roll", None)
        shown = getattr(self, "_diff_shown", None)
        if roll is not None and roll.running:
            shown = self._diff_rolling_value
        same = baseline == getattr(self, "_diff_baseline", None)
        self._diff_baseline = baseline
        jump = (same and lead is not None and shown is not None
                and abs(lead - shown) >= motion.ROLL_MIN_JUMP_S)
        if roll is not None:
            roll.cancel()          # a newer value wins; a roll never queues behind another
            self._diff_roll = None
        if not (jump and self._still_moment()):
            self._diff_shown = lead
            paint(lead)
            return
        start = shown
        self._diff_rolling_value = start

        def step(p: float):
            self._diff_rolling_value = start + (lead - start) * p
            paint(self._diff_rolling_value)

        def end():
            self._diff_roll = None
            self._diff_shown = lead
            paint(lead)

        self._diff_shown = lead
        # Frame 0, painted NOW: the old number (same baseline, same lap — a change of either cut
        # above) with the new moment's speed.
        paint(start)
        tr = motion.start(self.diff_box, motion.DELTA_ROLL_MS, step, end)
        self._diff_roll = None if tr.done else tr

    def _hero_delta_time(self, t: float, lap_id: int | None) -> float:
        """When the hero's deltas are read: `t`, or — while the playhead is still where the
        poster seek parked it — the END of the lap, so the hero reads the lap's total.

        The poster seek puts the playhead on the best lap's first frame, so the largest number on
        the default screen read `Δideal +0.00 s` on every load, and the lap's answer (+0.69 s to
        the ideal on SD_30_08) was readable only off the end of the Δ trace (board review UX-9b).
        The first real move — a play, a seek, a scrub, a lap click elsewhere — ends the rest for
        good. "Moved" allows HERO_REST_SLACK_S for the millisecond the player's echo of the seek
        can come back off by."""
        rest = getattr(self, "_hero_rest_t", None)
        if rest is None:
            return t
        if abs(t - rest) > HERO_REST_SLACK_S:
            self._hero_rest_t = None
            return t
        window = self.session.lap_window(lap_id) if lap_id is not None else None
        return window[1] if window is not None else t

    # ------------------------------------------------------------- compare-state access
    def _comparing(self) -> bool:
        """True iff compare mode is on. Defensive: tolerates the controller not yet built (the
        unit-test CentralView.__new__ path)."""
        compare = getattr(self, "compare", None)
        return compare is not None and compare.active

    def _pick_leaves_compare(self, ids) -> bool:
        """A lap pick arrived while comparing — a Laps-table click, ⌘-click or arrow key, a slow
        lap's "▼ C5" cue, a MOMENTS or Coaching Jump: every one of them reaches _on_laps_selected.
        It used to move the charts alone, so the panes and the seam went on naming the old pair
        above charts of the new lap: two stories in one window. A pick now LEAVES compare first,
        through the button, as a Lap race does (one map, one story); the pane pickers are the way
        to change the pair. True when it left; False for the pair's own laps (the charts already
        show them) or an empty selection, which leave the comparison as it is."""
        a, b = self.compare.lap_a, self.compare.lap_b
        shown = [a] if self.compare.cross else [a, b]
        if not ids or sorted(ids) == sorted(shown):
            return False
        self.video.compare_btn.click()      # compareToggled(False) → CompareController.exit
        if self._comparing():
            return False                    # the exit did not happen: apply nothing over the pair
        self.statusNotice.emit(compare_ended_notice(ids))
        return True

    def leave_compare_for_seek(self, t: float) -> None:
        """A seek that is not a lap pick — a Marks row, the , / . mark keys — about to land at `t`.
        Inside pane A's lap it is pane A's own seek, and compare stays. Anywhere else (pane B's
        lap too: pane A cannot show it, and the seam would go on naming a pair the picture left)
        it is a pick of the lap `t` is in: compare ends through the same rule, the window shows
        that lap, and the caller seeks. Between laps it ends compare on the pair's laps."""
        if not self._comparing():
            return
        lap = self.session.lap_at_time(t)
        if lap is not None and lap == self.compare.lap_a:
            return
        if lap is not None and lap in self.session.valid_lap_ids():
            self.table.select([lap])
            self._on_laps_selected([lap])   # the pick: leaves compare, says so, no seek
            return
        ids = self.table.selected_lap_ids()
        self.video.compare_btn.click()
        if not self._comparing():
            self.statusNotice.emit(compare_ended_notice(ids))

    # ------------------------------------------------------------- driving channels (F5)
    # --------------------------------------------------------- Lap Race
    def is_racing(self) -> bool:
        race = getattr(self, "race", None)
        return race is not None and race.active

    def can_race(self) -> bool:
        """True when this session fields at least two raceable laps (`Session.race_tracks`)."""
        race = getattr(self, "race", None)
        return race is not None and race.available()

    def map_on_screen(self) -> bool:
        """True when the track map is actually on screen: shown, and not collapsed to nothing by a
        maximized panel or video focus (both take the map's splitter cell to 0 px)."""
        mp = getattr(self, "map", None)
        return (mp is not None and mp.isVisible() and mp.width() > 0 and mp.height() > 0
                and not self.is_video_focused())

    def start_lap_race(self, speed: float = 1.0, speeds: tuple[float, ...] = (1.0,)) -> bool:
        """Every clean lap leaves the line together on the map. Leaves compare first (one map, one
        story), pauses the video, and emphasises the lap on screen (the playhead's, else the Laps
        table's first selected). `speed` / `speeds` are the race's starting speed and its ladder:
        the window passes the transport's, so this module never reads the playback rate
        (tests/test_playback_rate.py keeps the sync path ignorant of it)."""
        if self.is_racing() or not self.can_race() or not self.map_on_screen():
            return self.is_racing()
        self.end_pass_zoom()              # the race owns the map's view (its fit, or Follow)
        if self._comparing():
            self.video.compare_btn.click()
        lap = self.session.lap_at_time(self._playback.applied_t)
        if lap is None:
            lap = getattr(self, "_corner_lap", None)
        self._finish_diff_roll()          # the race starts on a still readout
        return self.race.enter(selected=lap, speed=speed, rates=speeds)

    def end_lap_race(self):
        """Leave the race; the map, marker and selection are as they were on entry."""
        race = getattr(self, "race", None)
        if race is not None:
            race.end()

    def toggle_lap_race(self, speed: float = 1.0, speeds: tuple[float, ...] = (1.0,)) -> bool:
        if self.is_racing():
            self.end_lap_race()
            return False
        return self.start_lap_race(speed, speeds)

    def _refresh_sector_lines(self, mode: str | None = None):
        """F2: push the sector boundary positions (start/finish + each sector line) to the charts
        for the current axis mode. Computed via session (the s×best_distance / time-into-lap
        axis), so plots_view stays pacer-free. Called on launch, after a sector edit, and when
        the dist/time mode flips (positions' units change)."""
        mode = mode or self.plots.axis_mode()
        self.plots.set_sector_lines(self.session.sector_plot_positions(mode))
        self.plots.set_corner_labels(self.session.corner_plot_positions(mode))  # same axis, C1…
        # The chart x-axis units changed with the mode too, so the F5 brake glyphs / coast bands
        # need re-pushing in the new mode's units (same reason as the sector lines).
        self._refresh_driving_channels()

    def _driving_lap_colour(self, lap_id: int, k: int):
        """The glyph colour for a lap's brake points, matching the speed chart's curve colour:
        the best lap takes theme.best_lap_colour() (green, or blue in the colour-blind palette —
        resolved per call so it follows the flip exactly as the curve does), every other lap cycles
        theme.CHART_SERIES by its draw-order index `k` — so a brake glyph always sits on its own
        lap's curve colour (and compare's two laps stay distinguishable, like the curves)."""
        if lap_id == self.session.best_lap_id():
            return theme.best_lap_colour()
        return theme.CHART_SERIES[k % len(theme.CHART_SERIES)]

    def _refresh_driving_channels(self):
        """Push brake glyphs + coast spans + the synthetic brake/throttle band for the shown lap(s).

        The MAP shows one racing line (current/primary lap, or both in compare), so its brake markers
        stay scoped to that same lap set. The SPEED CHART overlays every SELECTED lap, so L4 pushes a
        brake-glyph series PER plotted lap, each tagged with its lap id so the glyphs ride that lap's
        OWN curve in plots_view (not the nearest neighbour's trough). Coast spans + the estimated
        brake/throttle band stay on the primary/compare set — the band is a single estimated backdrop
        and stacked coast shading across many laps would be unreadable. Draw order sets the colours."""
        if self._comparing() and self.compare.lap_a is not None and self.compare.lap_b is not None:
            map_ids = [self.compare.lap_a, self.compare.lap_b]
        elif self._corner_lap is not None:
            map_ids = [self._corner_lap]
        else:
            map_ids = []
        mode = self.plots.axis_mode()
        map_markers, coast_plot, bt_plot = [], [], []
        for k, lid in enumerate(map_ids):
            colour = self._driving_lap_colour(lid, k)
            map_markers.append((self.session.driving.lap_brake_map_markers(lid), colour))
            coast_plot.append((self.session.driving.lap_coasting_plot_spans(lid, mode), colour))
            # D3: the synthetic brake/throttle band (its own red/green fill, not the lap colour).
            xs, inten = self.session.driving.lap_brake_throttle_plot(lid, mode)
            if xs is not None:
                bt_plot.append((xs, inten))
        # L4: the speed-chart brake glyphs cover every OVERLAID lap (compare's pinned pair, else the
        # multi-select). Each series is tagged with its lap id so plots_view anchors it to that lap's
        # own cached curve; a lap not actually drawn there is harmlessly skipped (no cached curve).
        plot_ids = map_ids if self._comparing() else self.plots.selected_lap_ids()
        brake_plot = [
            (self.session.driving.lap_brake_plot_positions(lid, mode),
             self._driving_lap_colour(lid, k), lid)
            for k, lid in enumerate(plot_ids)
        ]
        self.map.set_brake_markers(map_markers)
        self.plots.set_brake_markers(brake_plot)
        self.plots.set_coasting_spans(coast_plot)
        self.plots.set_brake_throttle(bt_plot)

    # ------------------------------------------------------------- the shared rebuild seam
    def rebuild_derived_views(self, *, reselect: bool = True):
        """The single seam that rebuilds every session-derived surface (table, map overlays/corners,
        corner table, opportunities, stats, driving channels, selection, sector lines) in a
        load-bearing order — three drifted copies were unified here. Shared by re-segmentation, a
        reference load/clear, and the initial build; each call site keeps only its own extras inline."""
        # A race's tracks describe the laps as they were: a re-segment ends it before anything moves.
        self.end_lap_race()
        self.end_pass_zoom()     # ...and so does a pass's zoom: its window is the old corners'
        self._pass_line_cache = None   # ...and the readout's pass time, read off the old corners
        self.table.refresh()
        self._push_lap_ruler()         # the ruler's laps and ★ / ▼ follow the table's
        # set_corners re-pushes the corner labels AND clears any stale highlight, so it runs after
        # refresh_overlays and before the corner consumers below.
        self.map.refresh_overlays()
        self.map.set_corners(self.session.corners.corner_map_markers())
        self._hovered_corner = None          # set_corners dropped both rings; the chart follows
        self.map.forget_corner_hover()       # ...and a pointer still on a label re-names it
        self.plots.forget_corner_hover()
        self._push_corner_emphasis()
        self.corner_table.refresh()
        self._refresh_quickest()
        # The persistent coaching front-door: recompute the top-3 opportunities (the clean-lap set /
        # corner losses shift on a re-segmentation; recomputed per build, never on the 30 Hz tick).
        self.opportunities.refresh()
        # P1: the Stats page renders only when it can be seen. On a hidden page this marks it
        # stale and returns — half this seam's 396 ms on a 65-lap recording, spent on one of five
        # stack pages. Nothing can read the stale page: `CentralView.stats_view` and
        # `StatsView.showEvent` both flush first. Raw `_stats`, since the property would flush the
        # very render we are about to redo.
        self._stats.refresh_when_shown()
        # The hero's reference chip: which of the three ideal states this session is in can change
        # under a re-segmentation (a start-line drag re-partitions and re-picks the donors), so it
        # is decided here with the rest of the session-derived state, beside the Stats IDEAL LAP
        # block that gates on the same fact. Runs before the selection step below, so the first
        # _update_diff_box of the new state already knows it.
        self._sync_ideal_readout()
        # Re-push driving channels explicitly: the selection step below can early-out on an
        # unchanged primary-lap id while the channels did change.
        self._refresh_driving_channels()
        if reselect:
            self._select_default()
        else:
            # Compare draws its own pinned [A,B] pair; refresh in place (a re-select would tear it down).
            self.plots.refresh()
        # After the selection: the sector lines + their units track the axis and new selection.
        self._refresh_sector_lines()
        # The provisional-timing banner tracks the trust state (a re-segment can confirm it). Only
        # the BANNER tier here: the table, the map cue and the stats page are the very surfaces this
        # method just rebuilt, so the full refresh_timing_trust() seam would redo all three.
        self._refresh_trust_banners()

    def refresh_timing_trust(self):
        """Refresh EVERY surface keyed on the session's timing-trust flags, for the callers that
        flip those flags WITHOUT re-segmenting: the trust strip over the map, the map canvas's own
        provisional cue, the Laps table's provisional muting/★, and the Stats page's banner + muted
        tiles.

        WHY it drives all four rather than just the strip. `timing_verified` normally flips inside a
        re-segmentation (a start-line drag), and rebuild_derived_views rebuilds every consumer on
        its way past — so for years the strip was the only thing left to refresh here. File ▸ Save
        as track… broke that assumption: it promotes the current lines into a named track, which
        makes the session Verified with no re-segmentation and nothing rebuilt. The strip cleared
        while the canvas still painted "lap timing provisional" and the Laps table still rendered
        the lap in provisional italics with the ★ withheld — three surfaces, two verdicts, one
        frame (QA W7-03). Fixing only that call site would rot at the next such path, so the seam
        itself now owns the whole set: flip a trust flag, call this, and the frame agrees.

        The rebuild seam calls _refresh_trust_banners() instead (it has just rebuilt the other
        three); every other caller wants this one."""
        self.end_lap_race()      # the race's ranked/unranked verdict was read at entry
        self._refresh_trust_banners()
        # Each guarded: a CentralView.__new__'d for a unit test has none of these children.
        table = getattr(self, "table", None)
        if table is not None:
            table.refresh_timing_trust()
            self._push_lap_ruler()       # ★ is withheld / given back with the table's
        mp = getattr(self, "map", None)
        if mp is not None:
            mp.refresh_provisional_cue()
        stats = getattr(self, "_stats", None)
        if stats is not None:
            stats.refresh_when_shown()   # raw + P1-deferred, as in rebuild_derived_views

    def _refresh_trust_banners(self):
        """Refresh the ONE trust strip over the map from the session's two orthogonal axes, as two
        tiers within a single compact strip:

          * ACTIONABLE — the provisional-timing (start-line TRUST) line, shown iff the timing is
            unverified (auto-fitted, not user-confirmed — see Session.timing_verified). Amber CTA
            style. A drag that confirms the timing clears it; also refreshed after saving as a track.
          * INFORMATIONAL — the data-quality (timing ACCURACY) line, shown iff Session.timing_quality
            is degraded (media-clock fallback / low GPS quality). Calmer FYI style; a single compact
            summary line (full detail in its tooltip), so the strip stays ~one or two lines even when
            both concerns apply.

        The whole strip is hidden when neither concern applies (a normal GPS9, verified-track
        recording — the common good case — so the map reads identically to today).

        The BANNER tier only — the other trust-keyed surfaces (the lap table's muting, the map's
        dashed cue, the Stats page) belong to refresh_timing_trust, which calls this first. Called
        directly ONLY from the rebuild seam, which has just rebuilt those three anyway; anything
        that flips a trust flag on its own calls refresh_timing_trust instead.

        Defensive: a CentralView.__new__'d for a unit test drives the seam without building the
        banner widgets, so the session reads stay behind the widget-presence guard (as before)."""
        strip = getattr(self, "_trust_strip", None)
        if strip is None:  # partially-built view (no banner chrome) — nothing to refresh
            return
        # No GPS trace, no line to drag: the amber call to action would sit directly above the
        # quality line saying no lap can be timed (data_quality.no_start_line).
        provisional = (not self.session.timing_verified
                       and not data_quality.no_start_line(self.session))
        quality = self.session.timing_quality
        degraded = quality.degraded
        # The map banner + the table header chip read the SAME shared summary/detail off
        # timing_quality (data_quality.summary()/detail()), so the two surfaces can never disagree
        # on wording or on the rejected-fix % (the M3 fix). The chip label/tooltip are also
        # clock-aware: it only claims "estimated" on the media-clock fallback.
        self.provisional_banner.setVisible(provisional)
        self.quality_banner.setText(quality.summary())
        self.quality_banner.setVisible(degraded)
        self._refresh_quality_badge(quality)
        # The strip (its shared hairline) shows only while a concern is live.
        strip.setVisible(provisional or degraded)

    def _refresh_quality_badge(self, quality) -> None:
        """Drive the table-header data-quality chip from the shared timing_quality copy so it agrees
        with the map banner. Clock-aware: "ESTIMATED" only when the times really are estimated (the
        media-clock fallback); a true-clock recording whose only concern is rejected fixes reads
        "GPS LOW" — the old static "ESTIMATED" overclaimed on true-clock footage (M3). Hidden on a
        clean recording. Guarded so a partially-built view (no chip) is a no-op."""
        badge = getattr(self, "quality_badge", None)
        if badge is None:
            return
        badge.setVisible(quality.degraded)
        if not quality.degraded:
            return
        # THREE words for three states, not two. The no-GPS verdict (karma.mp4: not one fix) used
        # to fall through to "GPS LOW" — a chip saying the GPS was poor on a recording that has
        # none, opening a DATA TRUST row that says "no GPS fixes survived". The word is shared
        # with the exported report and clipboard summary (data_quality.timing_word), so a file
        # leaving the app names the clock the way this chip does.
        badge.setText(data_quality.timing_word(quality))
        # The hover says where a click goes as well as what the chip means — it is the only place a
        # pointer user learns that the pill is not just a label.
        badge.setToolTip(f"{quality.detail()}\n\nClick to see this in DATA TRUST on the Stats page.")

    def set_marks(self, marks: list[dict] | None, suppressed: int = 0) -> None:
        """Push the merged mark list (`marks.merge`) to BOTH of its surfaces at once — the scrub
        bar's band and the Marks page.

        ONE CALL, on purpose. The band and the list are two renderings of one list, and a feature
        whose two surfaces are fed separately is a feature with two chances to disagree about what
        is marked; that is the defect class this whole surface is held to. `suppressed` is how many
        degraded stretches were too short to mark — the list states it, since the list is where a
        count could otherwise be read as complete."""
        self.video.set_marks(marks)
        self.marks_panel.set_marks(marks, suppressed)

    def set_session_record(self, record: dict | None) -> None:
        """Show (or hide) the lap panel's session-record chip for `record` — the app's push of what
        the driver wrote down about this session (``studio.session_record``).

        The CHIP is one clause: the conditions tag and the tyre age, which are the two that decide
        whether these lap times are comparable with another day's. Everything else in the record —
        pressures, gearing, chassis, notes — is in the hover, because a header chip is read at a
        glance and a five-clause pill is not. None (or an empty record) hides it entirely; see
        where the chip is built for why its empty state is its absence.

        Takes a plain dict, so this view stays out of the app-support stores and the whole surface
        is testable by handing it a record."""
        chip_w = getattr(self, "record_chip", None)
        if chip_w is None:
            return
        # Imported here rather than at module scope: this view has no other business with the
        # persistence layer, and the chip is a display of what it is handed.
        from . import session_record
        # …and the kart number between them: for an arrive-and-drive driver it is the kart fact
        # that changes from one session to the next (UX-6).
        headline = "  ·  ".join(c for c in (session_record.conditions_text(record),
                                            session_record.kart_no_text(record),
                                            session_record.tyre_text(record)) if c)
        full = session_record.summary_line(record)
        if not full:
            chip_w.setVisible(False)
            return
        # A record can hold ONLY a setup (no conditions, no tyres) — then the headline above is
        # empty and the kart clause carries the chip, so it never shows a blank pill over a record
        # that says something.
        chip_w.setText(headline or full)
        notes = ((record or {}).get("notes") or "").strip()
        chip_w.setToolTip(f"Session record — {full}" + (f"\n\n{notes}" if notes else "")
                          + "\n\nFile ▸ Session record… to edit")
        chip_w.setVisible(True)

    # ------------------------------------------------------------- timing-line edits
    def _on_lines(self, start, sectors):
        # Re-segmentation shifts lap ids: snapshot the PRIOR lines for Undo, exit compare (stale
        # pair), re-segment, rebuild all derived views (reselect=True), re-check compare
        # availability, persist the edit.
        #
        # Both of those RECORDS (the undo snapshot here, the sidecar below) are gated on the
        # segmentation being one the load-time revert guard would accept, i.e. one that leaves at
        # least one valid lap. Session.apply_timing_lines_latlon rejects a zero-lap placement on
        # the way back in, and undo_timing_lines deliberately does NOT consume a snapshot the
        # guard refuses — so recording a zero-lap state does harm and no good in both stores.
        #
        # Undo specifically: a start-line drag is TWO edits (one per endpoint handle), and the
        # FIRST release can already empty the lap set. The second push then snapshotted that
        # zero-lap intermediate, and Cmd+Z peeked it forever — measured three presses, all no-ops,
        # leaving the user stranded in an empty session with a lit but dead Undo. Skipping the
        # push keeps the last GOOD placement on top of the stack, so one Cmd+Z recovers.
        #
        # The whole body is synchronous and costs ~0.5 s on a big session, so it runs under a wait
        # cursor — see _busy().
        #
        # Only an edit that MOVED the start line confirms the timing (QA r4 CODE-1). Add sector and
        # Clear sectors arrive here with the start line untouched, and confirming on them turned a
        # sector edit at an unknown circuit into the answer to "drag it into place": a verified
        # Library row and the first-open verdict, decided on the loader's own fit. user_confirm
        # False never un-confirms (a sector edit after a drag stays Verified); Save as track is
        # the confirmation that needs no move.
        prior = getattr(self.session, "start_line", None)
        with _busy():
            if self.session.valid_lap_ids():
                self.session.push_timing_history()  # pre-edit state, so a bad drag is undoable
            if self._comparing():
                self.video.set_compare_enabled(False)  # un-checks -> compareToggled(False) -> exit
            self.session.set_timing_lines(start, sectors,
                                          user_confirm=_start_seg_moved(prior, start))
            self.rebuild_derived_views(reselect=True)
            self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
            self._save_sidecar()
        self.timingEdited.emit()  # let the window refresh the Edit ▸ Undo enablement

    def _save_sidecar(self):
        """Write the timing lines to the recording's sidecar JSON. Called only from _on_lines (a
        genuine user edit) and undo_timing_lines, so an untouched session never creates the file.
        Best-effort: an unwritable folder just logs.

        NEVER persists a placement that leaves no valid lap. Session.apply_timing_lines_latlon's
        revert guard throws exactly that sidecar away on the next open, so writing one cannot help
        and actively destroys: it REPLACED the user's last good saved line with 221 bytes the
        loader always rejects, pinning the recording to provisional ("saved timing lines don't
        match this recording") with a line no surface shows and nothing clears. Keeping the last
        good file instead means quitting after a bad drag reopens on the placement that worked.
        The edit itself still stands on screen — this refuses the disk copy, not the gesture."""
        path = getattr(self, "_sidecar_path", None)
        if not path:
            return
        if not self.session.valid_lap_ids():
            _log.warning("timing lines NOT saved — that placement leaves no complete lap; the last "
                         "saved lines are unchanged (Edit ▸ Undo reverts the edit)")
            return
        start, sectors = self.session.timing_lines_latlon()
        try:
            sidecar.save(path, self.session.track_name, start, sectors,
                         confirmed=self.session.timing_user_confirmed)
        except OSError:
            # §7.5: this used to be a print, and a print is not a surface. The drag stands on
            # screen either way, so a failed write is INDISTINGUISHABLE from a good one until the
            # user reopens the recording and finds their line gone. The flag is read by
            # `StudioWindow._session_notice` (which is already re-decided on every `timingEdited`),
            # so the fact stays on the status bar for as long as it is true.
            self.sidecar_write_failed = True
            _log.exception("could not write timing-line sidecar %s", path)
            return
        self.sidecar_write_failed = False
        _log.info("timing lines saved to %s", os.path.basename(path))

    def undo_timing_lines(self) -> UndoOutcome | None:
        """Undo the last timing-line edit (Edit ▸ Undo / Cmd+Z). Restores the prior lines through
        Session.undo_timing_lines (which replays them through the same re-segment/apply path, so
        the segmentation + PB/session-best baseline recompute identically), then re-draws the map
        handles, rebuilds the derived views, and re-persists the restored lines to the sidecar.
        No-op (returns None) when there's no prior edit to undo. Compare mode is torn down first
        (a re-segment shifts lap ids, invalidating any pinned pair) — mirrors _on_lines.

        Returns an ``UndoOutcome`` naming WHAT came back, measured across the restore, so the
        window can confirm the gesture it actually performed instead of the one string it used to
        print for every undo (QA W3-03). Truthy on success, falsy (None) on the no-op, so the
        `if view.undo_timing_lines():` callers read as before."""
        before_start, before_sectors = self.session.timing_lines_latlon()
        with _busy():  # ~440 ms on a 66-lap session, same re-segment path as _on_lines
            if not self.session.undo_timing_lines():
                return None
            if self._comparing():
                self.video.set_compare_enabled(False)
            # The session lines are already restored; pull the map's draggable handles onto them
            # WITHOUT re-emitting timing_lines_changed (that would re-push the undone state).
            self.map.reload_timing_lines()
            self.rebuild_derived_views(reselect=True)
            self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
            self._save_sidecar()  # persist the restored lines so a reload sees the undone state
        after_start, after_sectors = self.session.timing_lines_latlon()
        outcome = UndoOutcome(start_moved=_start_moved(before_start, after_start),
                              sector_delta=len(after_sectors) - len(before_sectors))
        # The map's own plate is where the user was TOLD to press ⌘Z ("2 sector lines cleared —
        # Edit ▸ Undo timing-line edit (⌘Z) puts them back."), and it outlives the undo by ~5.95 s
        # of its 6 s (QA W3-04). Swap it for what just happened, so the canvas cannot keep
        # instructing a key that has already been pressed — and so the plate and the status bar
        # (same sentence, composed by the window from the same outcome) agree in one frame.
        mp = getattr(self, "map", None)
        if mp is not None:
            mp.retract_notice(undo_summary(outcome).capitalize() + ".")
        self.timingEdited.emit()
        return outcome

    def revert_timing_to_fitted(self) -> bool:
        """Edit ▸ Revert start/finish line: put the start line back on the loader's own placement
        (Session.revert_timing_to_fitted) and take it through the SAME road as an undo — the map's
        handles, every derived view, the sidecar. False when there was nothing to revert.

        The trust surfaces are refreshed explicitly on top of the rebuild: the revert un-confirms
        the timing, and on a session that is Verified through a TRACK NAME the rebuild's banner-only
        refresh would leave the map's canvas cue and the Laps table disagreeing with it."""
        with _busy():
            if not self.session.revert_timing_to_fitted():
                return False
            if self._comparing():
                self.video.set_compare_enabled(False)
            self.map.reload_timing_lines()
            self.rebuild_derived_views(reselect=True)
            self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
            self._save_sidecar()
        self.refresh_timing_trust()
        self.timingEdited.emit()
        return True
