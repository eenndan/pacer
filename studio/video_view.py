"""VideoView: transport chrome (play/pause/mute/g-meter/compare + global-ms scrub slider + readout)
around ONE PlayerPane, or TWO equal panes in a horizontal QSplitter (compare mode).

The single-lap player stack (decode/seek/auto-advance/g-meter overlay) lives in
`player_pane.PlayerPane`; VideoView is the shell and re-exposes the public API the app drives
(seek/play/pause/set_g/set_readout/positionChanged/...).

Compare mode (behind an explicit toggle, enabled only with >=2 valid laps): the PRIMARY (left) pane
is `self.pane` and drives ALL telemetry; the SECONDARY (right) is created lazily, is video-only (its
positionChanged is never forwarded), always muted, and disposed on exit/reload. Each pane shows a
fixed ROLE caption + a lap picker (the SOLE home of lap identity, emits paneRepointRequested) + a
"Δ vs other" badge. Play/pause/mute fan out to both panes.

VideoView is a DUMB layout renderer for compare: it holds NO 'are we comparing' flag. CompareController
is the single source of truth for that semantic state; the view derives its own layout fact (is the
two-pane stage mounted?) from the live widget tree (_panes_mounted) and is driven by the imperative
verbs set_compare(pane_a, pane_b) (enter/re-seed) and exit_compare() (leave). A user toggle of the
compare button only EMITS compareToggled (the intent); the controller decides and calls those verbs
back, which also reflect the authoritative checked/appearance state onto the button (no two-way sync).

The slider + emitted position are GLOBAL session ms (multi-chapter summed); the pane maps
global<->chapter and switches sources under the hood. In compare mode the slider spans lap A's
window via the primary pane's clamp.

Transport layout: TWO BARS under the video, both on the app's bar system (`PanelHeader`'s surface
+ hairline, `SPACE_S` gutters, `TOOLBAR_H`) — the scrub bar has a row to itself (a media-player
transport, not a control squeezed in beside five buttons) and the transport is a `PanelToolbar`
holding two GROUPS around its stretch: playback (▶ 🔇) and the timecode on the left, the view
toggles (g-meter · Compare · ⤢) on the right. Before this, the video panel was the only control
zone in the window that was not on a bar: three rows at 26/28/21 px painted on the window canvas
with a 0 px gutter, against six other bars that agreed to the pixel.

Every piece of chrome here is width-budgeted rather than assumed to fit — see
`_LapRulerSlider.tick_plan` (the ruler decimates to the pixels it has) and `_PaneStrip` (the
compare strip picks a form that fits instead of letting Qt overlap the boxes).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSlider,
    QSplitter,
    QStyle,
    QStyleOptionSlider,
    QVBoxLayout,
    QWidget,
)

from . import chapters, theme
from .player_pane import PlayerPane
from .widgets import PanelToolbar, ToggleButton, icon_button

# The transport bar's own icon-button size constants (_ICON_PX 18 / _ICON_BTN 32x30) are gone: the
# app has ONE icon button now (widgets.icon_button, theme.ICON_BTN, theme.ICON_PX), and these were
# one of the two families it replaced. The transport row loses 4x2 px of button and gains agreement
# with the "Compare" label beside it, which the 30 px height had been silently overriding to 30
# while every other control in the app stood at CTRL_H.

# 0 = primary (left, drives telemetry); 1 = secondary (right, video-only). Used by the lap-picker
# repoint signal so app knows which side to repoint.
PRIMARY, SECONDARY = 0, 1

# The scrub bar is the video panel's primary hit target and the lap ruler's canvas: it gets its own
# full-width row under the video, a HIT_MIN handle and a widget one sub-step taller (the 24px hit
# floor), which also buys the ruler the travel it needs to stay readable on a 65-lap session. The
# HANDLE's geometry moved to the theme (QSlider#ScrubBar) with the rest of the slider chrome; it was
# the app's one dimensional stylesheet built inside a view.
_SLIDER_H = theme.HIT_MIN + theme.SPACE_XXS
# Seek tooltip; the ruler appends what the ticks mean (see _LapRulerSlider._refresh_tooltip).
_SEEK_TIP = "Seek — click or drag · ←/→ step 1 s · Shift+←/→ 5 s"


@dataclass
class PaneSpec:
    """Per-side bundle for one compare pane.

      * lap_id        — picker entry to select (never emitting a repoint).
      * window        — (start, end) on this pane's clock; pane A's also confines the scrub slider.
      * caption       — rich lap text, shown as the caption TOOLTIP (the fixed ROLE word is the label).
      * source        — this pane's media source (ChapterMap/path). None reuses the PRIMARY source
                        (`self._source`); an explicit source plays a DIFFERENT recording (cross-
                        recording compare). Pane A's source is conventionally None.
      * choices       — the lap ids the picker lists (cross-recording locks pane B to the reference).
      * choice_labels — parallel labels for `choices` (None -> "lap {id}").
    """
    lap_id: int
    window: tuple[float, float]
    caption: str
    source: object = None
    choices: list[int] = field(default_factory=list)
    choice_labels: list[str] | None = None


def _ordinal(n: int) -> str:
    """1st / 2nd / 3rd / 5th — for the lap ruler's "every Nth lap" tooltip."""
    suffix = "th" if n % 100 in (11, 12, 13) else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix}"


class _LapRulerSlider(QSlider):
    """Horizontal QSlider that also paints lap-boundary tick marks over the groove (MoTeC-style lap
    ruler). Ticks are global-ms boundaries fed via `set_lap_ticks`; only painting is extended — seek
    wiring is the base slider's. Each tick maps to x via the style's groove rect +
    sliderPositionFromValue (the handle's own geometry), so ticks and handle agree.

    The ruler is DECIMATED to the width it has (`tick_plan`): a long session (65 laps over ~500 px)
    painted one line per boundary collapses into a 4 px-pitch hatch where no lap is identifiable, so
    the plan drops to every 2nd/5th/10th... lap until the ticks are at least `_MIN_PITCH` apart,
    promotes every `_MAJOR_EVERY`-th survivor to a taller major tick, and brackets the lap the
    playhead is inside in accent. The tooltip says which of those you are looking at.

    The bracket is drawn only when a lap is WIDER than the handle plus a margin: on a 65-lap session
    one lap is ~7 px, so the two accent lines would be painted under the 24 px handle, marking
    nothing while claiming to. There the ruler stays a ruler and the readout keeps naming the lap."""

    _TICK_H = 10        # minor tick, centred on the groove (a touch taller than the 8px groove)
    _MAJOR_H = 16       # every _MAJOR_EVERY-th drawn tick, so the eye can count laps along the bar
    _BRACKET_H = 18     # the current lap's two boundaries, in accent
    _MIN_PITCH = 9      # px: closer than this and the ruler reads as a hatch, so decimate
    _MAJOR_EVERY = 5    # promote every 5th DRAWN tick
    # Decimation steps, in laps. Kept "nice" so a tick is always every Nth lap a driver can count.
    _STEPS = (1, 2, 5, 10, 20, 25, 50, 100, 200)

    def __init__(self, orientation):
        super().__init__(orientation)
        self._lap_ticks: list[int] = []  # boundary values in slider units (ms), sorted/unique
        self._span_note = ""             # what this bar currently spans, when that is not obvious
        # The decimation step (and so the tooltip) is a function of the range and the width; both
        # change under the ruler as chapters load and the panel is resized.
        self.rangeChanged.connect(lambda *_: self._refresh_tooltip())
        self._refresh_tooltip()

    def set_span_note(self, note: str) -> None:
        """Say WHAT THIS BAR MEASURES when that changes under the user (empty = the obvious case).

        Entering compare re-ranges the slider from the whole session to one lap — 1729 s to 68.7 s
        on the fixture — and clears every one of its 22 ruler ticks, and the bar looked identical
        before and after: same length, same handle, same groove, and the only visible difference
        was that the tooltip's "the ticks mark every lap boundary" clause quietly disappeared. This
        is the smallest honest fix and it uses the instrument the ruler already uses for exactly
        this job: the tooltip is where this bar says what it is showing. The decimation ladder and
        the `bracketable` gate are untouched — they are measured and correct."""
        note = str(note or "")
        if note != self._span_note:
            self._span_note = note
            self._refresh_tooltip()

    def set_lap_ticks(self, values: list[int]) -> None:
        """Set lap-boundary ticks (global ms); out-of-range values clamp at paint, empty clears.
        Repaints."""
        self._lap_ticks = sorted({int(v) for v in values})
        self._refresh_tooltip()
        self.update()

    def _groove_rect(self):
        """The style's groove rect for this slider — the band the handle travels in. Used to map a
        boundary value to an x pixel exactly as the handle is placed, so ticks and handle agree."""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)

    def _travel(self):
        """(x0, span, handle_w): where value `minimum()` sits and how many pixels the handle travels
        — the mapping every x below is built on, taken from the handle's own style geometry."""
        groove = self._groove_rect()
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle = self.style().subControlRect(QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        return groove.x() + handle.width() // 2, groove.width() - handle.width(), handle.width()

    def _x_for(self, value: int) -> int:
        """A slider value as an x pixel, exactly where the handle would place it."""
        lo, hi = self.minimum(), self.maximum()
        x0, span, _ = self._travel()
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return x0 + QStyle.sliderPositionFromValue(lo, hi, min(max(value, lo), hi), span,
                                                   opt.upsideDown)

    def _tick_xs(self) -> list[int]:
        """Every lap boundary as an x pixel (clamped into range, de-duplicated, ascending)."""
        if not self._lap_ticks or self.maximum() <= self.minimum():
            return []
        return sorted({self._x_for(v) for v in self._lap_ticks})

    def tick_plan(self) -> dict:
        """What paintEvent will actually draw, as x pixels — the single source for the painting, the
        tooltip and the regression test:

          {"step": every Nth lap boundary kept, "minor": [x...], "major": [x...],
           "current": (x_start, x_end) | None, "bracketable": bool}

        `minor`/`major` are disjoint and at least `_MIN_PITCH` apart, so the drawn tick count can
        never exceed span/_MIN_PITCH however many laps the session has. `bracketable` is whether a
        typical lap is wide enough for the current-lap bracket to clear the handle (it is a property
        of the session + width, not of the playhead, so the tooltip can rely on it)."""
        xs = self._tick_xs()
        plan = {"step": 1, "minor": [], "major": [], "current": None, "bracketable": False}
        if not xs:
            return plan
        spans = [b - a for a, b in zip(xs, xs[1:], strict=False)]
        # A bracket is only honest when its two lines fall OUTSIDE the handle covering the playhead.
        _, _, handle_w = self._travel()
        room = handle_w + 2 * self._MIN_PITCH
        plan["bracketable"] = bool(spans) and sorted(spans)[len(spans) // 2] >= room
        # The lap the playhead is inside: the boundary pair bracketing the current value.
        cur = None
        if plan["bracketable"]:
            vx = self._x_for(self.value())
            for a, b in zip(xs, xs[1:], strict=False):
                if a <= vx <= b and b - a >= room:
                    cur = (a, b)
                    break
        plan["current"] = cur
        # Smallest "nice" step whose kept ticks clear _MIN_PITCH (the last step is the fallback).
        step = self._STEPS[-1]
        for cand in self._STEPS:
            kept = xs[::cand]
            pitches = [b - a for a, b in zip(kept, kept[1:], strict=False)]
            if not pitches or min(pitches) >= self._MIN_PITCH:
                step = cand
                break
        plan["step"] = step
        # Greedy pitch guarantee on top of the index decimation (laps are not equally long, and a
        # chapter seam or an out-of-range clamp can still bunch two survivors together).
        drawn: list[int] = []
        for x in xs[::step]:
            if drawn and x - drawn[-1] < self._MIN_PITCH:
                continue
            if cur is not None and min(abs(x - cur[0]), abs(x - cur[1])) < self._MIN_PITCH:
                continue        # the accent bracket already marks this spot
            drawn.append(x)
        plan["minor"] = [x for i, x in enumerate(drawn) if i % self._MAJOR_EVERY]
        plan["major"] = [x for i, x in enumerate(drawn) if not i % self._MAJOR_EVERY]
        return plan

    def _refresh_tooltip(self) -> None:
        """Say what the ticks ARE — a bare hatch of lines over a seek bar reads as decoration, and
        at a decimated step the user has to be told they are not seeing every lap. The bracket
        clause is added only when a bracket can actually be drawn at this width."""
        tip = _SEEK_TIP
        if self._span_note:
            tip = f"{tip} · {self._span_note}"
        if self._lap_ticks:
            plan = self.tick_plan()
            step = plan["step"]
            which = "every lap boundary" if step == 1 else f"every {_ordinal(step)} lap boundary"
            tip = f"{tip} · the ticks mark {which}"
            if plan["bracketable"]:
                tip = f"{tip}; the lap you are in is bracketed in amber"
        if tip != self.toolTip():
            self.setToolTip(tip)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._refresh_tooltip()   # the decimation step is a function of the width

    def paintEvent(self, ev):
        super().paintEvent(ev)  # base groove + sub/add-page fill + handle (themed QSS) first
        plan = self.tick_plan()
        if not (plan["minor"] or plan["major"] or plan["current"]):
            return
        cy = self._groove_rect().center().y()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)

        def bars(xs, height, colour, opacity, width=1):
            pen = QPen(QColor(colour))
            pen.setWidth(width)
            painter.setPen(pen)
            painter.setOpacity(opacity)
            for x in xs:
                painter.drawLine(x, cy - height // 2, x, cy + height // 2)

        bars(plan["minor"], self._TICK_H, theme.C.text_dim, 0.55)
        bars(plan["major"], self._MAJOR_H, theme.C.text_dim, 0.85)
        if plan["current"] is not None:
            bars(plan["current"], self._BRACKET_H, theme.C.accent, 1.0, width=2)
        painter.end()


class _PaneStrip(QWidget):
    """One compare pane's IDENTITY BAR: the role word · the lap picker · the Δ badge.

    It is the compare-mode twin of `widgets.PanelHeader` and wears exactly its chrome — the themed
    `role="PanelHeader"` surface + bottom hairline, `SPACE_S / SPACE_XXS` margins, `SPACE_S`
    spacing, every child vertically centred, a DECLARED height. Before this it was two loose labels
    and a combo dropped into a bare grid: `QLabel#PaneCaption` wore a square surface fill,
    `QLabel#PaneBadge` beside it composited the window CANVAS, the three children stood 21/28/21 px
    tall with no vertical alignment, and the container's 5 px inset / 6 px spacing were off the
    spacing scale — one strip, two backgrounds, three heights and two invented numbers.

    WHY IT IS NOT LITERALLY A `PanelHeader`, which is what the measurement proposed. PanelHeader is
    ONE declared row, and these three children do not fit one row at any width the app can be
    driven to except maximized. Measured under the shipped theme, with the app's own labels:
    `THIS LAP` 50 px + the lap picker's 168 px content + `Δ -0.19 s` 83 px + 5 x SPACE_S of margins
    and spacing = **333 px** (347 for `REFERENCE`), against a compare cell of **253 px** at the
    app's default 1440x900, **224** at 1280x800 and **200** at the window's own 973x528 minimum.
    Only the maximized panel (713 px) fits. Deleting the width budget in favour of one row would
    have restored exactly the L8-01 defect it was written for — Qt resolves a shortfall by
    OVERLAPPING the boxes, painting the Δ on top of the lap time — or, if the picker's minimum is
    capped to the deficit instead, elided the lap TIME out of the one control that carries it, at
    every window size the app ships at.

    So the budget survives, and it is now the only thing that varies: two DECLARED forms, chosen
    ONCE for both panes (see `VideoView._fit_strips`) so the two videos always start at the same y.

      * one row  — `theme.PANEL_HDR_H`, the height of the panel header this is the pane-level twin
        of, which also clears the CTRL_H picker inside it.
      * two rows — role + Δ on a label row, the picker full-width beneath it: the same SPACE_XXS
        margins and row gap, plus one CTRL_H control. A derivation, not a constant, so the label
        row is whatever this app's font stack actually resolves (the shape `theme.focus_pad` and
        `widgets.space_at_least` already use).

    The lap time and the Δ never yield; the role word yields to its short form, then goes (it is
    always in the tooltip, and pane A is always the left one).
    """

    #: Columns: 0 role · 1 picker · 2 elastic gap · 3 Δ (pinned right). Which cells are occupied is
    #: `set_two_row`'s call; the gap column carries the stretch in both forms.
    _GAP_COL = 2

    def __init__(self, role_label: QLabel, picker: QComboBox, badge: QLabel, parent=None):
        super().__init__(parent)
        # The themed bar: surface + the bottom hairline every other bar in the window wears...
        self.setProperty("role", "PanelHeader")
        # ...AND THE BAR HAS TO BE TOLD TO PAINT IT. QStyleSheetStyle::polish sets
        # WA_StyledBackground only for a BARE QWidget (`metaObject() == QWidget::staticMetaObject`);
        # a subclass — which this is — honours neither the background nor the border and says
        # nothing. That silently flattened four panel headers and both toolbars once (#185).
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.role_label, self.picker, self.badge = role_label, picker, badge
        grid = QGridLayout(self)
        grid.setContentsMargins(theme.SPACE_S, theme.SPACE_XXS, theme.SPACE_S, theme.SPACE_XXS)
        grid.setHorizontalSpacing(theme.SPACE_S)
        grid.setVerticalSpacing(theme.SPACE_XXS)
        grid.setColumnStretch(self._GAP_COL, 1)
        self._grid = grid
        self._two_row: bool | None = None
        self.set_two_row(False)

    def height_for(self, two_row: bool) -> int:
        """The strip's DECLARED height in each form — see the class prose for why there are two."""
        if not two_row:
            return theme.PANEL_HDR_H
        return (2 * theme.SPACE_XXS + self.role_label.sizeHint().height()
                + theme.SPACE_XXS + theme.CTRL_H)

    def set_two_row(self, two_row: bool) -> None:
        """Mount the three children in the one-row or two-row form and pin the matching height.
        Idempotent, so a resize storm re-parents nothing."""
        if two_row == self._two_row:
            return
        self._two_row = two_row
        for wdg in (self.role_label, self.picker, self.badge):
            self._grid.removeWidget(wdg)
        # AlignVCenter is load-bearing, for the reason PanelHeader._add gives: without it a grid
        # stretches any child whose vertical policy allows it to the full row height, which is how
        # a 21 px caption, a 28 px combo and a 21 px badge shipped as three different heights on
        # what is meant to be one line.
        self._grid.addWidget(self.role_label, 0, 0, Qt.AlignVCenter)
        self._grid.addWidget(self.badge, 0, 3, Qt.AlignVCenter)
        if two_row:
            self._grid.addWidget(self.picker, 1, 0, 1, 4, Qt.AlignVCenter)
        else:
            self._grid.addWidget(self.picker, 0, 1, Qt.AlignVCenter)
        self.setFixedHeight(self.height_for(two_row))

    @property
    def two_row(self) -> bool | None:
        return self._two_row


class _PaneCell(QWidget):
    """Compare-pane chrome: a `_PaneStrip` identity bar above the PlayerPane. Owns no playback
    state. The lap identity lives ONLY in the picker; the role label is a fixed word, the badge
    yields width first. Selecting a lap emits `repointRequested(lap_id)`.

    THE STRIP IS FULL-BLEED IN THE CELL and the VIDEO is what carries the horizontal inset, which
    is a change of which child pays for what. The inset exists so the native QVideoWidget surface
    (which on macOS composites above sibling chrome) cannot swallow the splitter handle's mouse
    events — that is a property of the VIDEO, never of a QLabel — and while the strip paid it too,
    pane A's role word sat 5 px from the panel edge against the `VIDEO` label's 8 px directly above
    it. Full-bleed, each pane's strip is a panel header one level down: its identity is SPACE_S
    from its own pane's left edge and its Δ SPACE_S from the right, exactly as `VIDEO` and ⛶ are.
    """

    repointRequested = Signal(int)  # the newly-picked lap id for this side

    # Role caption per side: (full, short). The short form is used only when the full one cannot fit
    # beside the Δ badge; the full role is always in the caption's tooltip.
    _ROLES = {PRIMARY: ("THIS LAP", "THIS"), SECONDARY: ("REFERENCE", "REF")}

    def __init__(self, pane: PlayerPane, side: int):
        super().__init__()
        self.pane = pane
        self.side = side
        self._lap_ids: list[int] = []
        self._labels: list[str] = []   # last-applied picker item labels (guards the repopulate)
        self._role_full, self._role_short = self._ROLES[side]

        # The fixed role word. A BarLabel — the app's "a label inside a bar" role, transparent,
        # because the bar behind it now provides the surface. It was a `#PaneCaption`, whose rule
        # painted a surface-coloured square around the word while the identically-typed Δ beside it
        # was transparent and composited canvas: one strip, two backgrounds.
        self.caption = QLabel(self._role_full)
        self.caption.setProperty("role", "BarLabel")
        self.caption.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.caption.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        # BOTH panes' role words reserve the width of the WIDEST of them, so the two strips share
        # one column grid: in a side-by-side comparison the pickers beside them then start at the
        # same x and end at the same x, instead of pane A's floating 14 px right of pane B's
        # because "THIS LAP" is shorter than "REFERENCE".
        self.caption.setMinimumWidth(max(
            self.caption.fontMetrics().horizontalAdvance(full)
            for full, _short in self._ROLES.values()))
        self.caption.setToolTip(self._role_full)

        # sole home of lap identity; the width floor is re-derived in set_lap_choices from its own
        # content AND from the cell it has to fit inside (see picker_room).
        self.picker = QComboBox()
        self.picker.setToolTip("Pick the lap shown in this pane")
        self.picker.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.picker.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.picker.currentIndexChanged.connect(self._on_pick)

        # app-driven Δ; Fixed, yields width first (can't push the lap text out)
        self.badge = QLabel("Δ —")
        self.badge.setObjectName("PaneBadge")
        self.badge.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.badge.setToolTip("This lap against the other pane at the same point")
        self._badge_colour: str | None = None
        self._badge_text = "Δ —"   # last-applied badge text (guards the per-tick setText)

        self.strip = _PaneStrip(self.caption, self.picker, self.badge)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.strip)
        # The VIDEO carries the handle inset, not the whole cell (see the class prose). A LAYOUT,
        # not a wrapper widget: the theme's base rule paints every bare QWidget canvas-coloured.
        #
        # THE TWO LINES BELOW ARE IN THIS ORDER ON PURPOSE, and swapping them crashed the app.
        # `QLayout::addWidget` delegates the move to `QLayout::addChildWidget`, which does TWO
        # things: it pulls the widget out of whatever layout currently holds it
        # (`removeWidgetRecursively`), and it reparents it onto `parentWidget()`. On a row that has
        # not been mounted yet that second half CANNOT RUN — a free-standing QLayout has no
        # parentWidget — so the call did only the first half: it silently deleted the PRIMARY
        # pane's item out of the LIVE `_stage_lay` (measured: `indexOf(pane)` 0 -> -1,
        # `_stage_lay.count()` 1 -> 0) and left the pane still parented to the old stage, in no
        # layout at all, until `addLayout`'s `reparentChildWidgets` finished the move a few
        # statements later. Qt prints nothing. Entering compare 200 times through that half-move
        # SIGSEGVd 5 of 6 processes, inside PySide's per-type metaobject lookup, on the next
        # `PlayerPane(...)` (S5-01); mounting the row first is 0 of 12. Adding the pane to a row
        # that already knows its parent is one atomic, complete move — which is exactly what this
        # line did before the row existed.
        video_row = QHBoxLayout()
        video_row.setContentsMargins(theme.SPACE_XS, 0, theme.SPACE_XS, 0)
        video_row.setSpacing(0)
        lay.addLayout(video_row, 1)
        video_row.addWidget(self.pane)

    # ------------------------------------------------------------------ the strip's width budget
    @property
    def two_row(self) -> bool | None:
        return self.strip.two_row

    def _caption_w(self, text: str) -> int:
        """What the role label would be WIDE if it carried `text` — never below the reserved
        widest-role width, which is the column both panes share."""
        return max(self.caption.fontMetrics().horizontalAdvance(text),
                   self.caption.minimumWidth())

    def strip_need(self) -> int:
        """The width this cell's strip needs to hold all three children on ONE row: the role word,
        the picker's own content, the Δ, and the bar's five SPACE_S gaps (two margins + three
        spacings, the middle one collapsed onto the elastic column). `VideoView._fit_strips` takes
        the max over both panes so the two never disagree about the form."""
        return (self._caption_w(self._role_full) + self.picker.sizeHint().width()
                + self.badge.sizeHint().width() + 5 * theme.SPACE_S)

    def apply_strip_form(self, two_row: bool) -> None:
        """Mount the form `VideoView` chose for BOTH panes, and drop the role word to its short
        form (then hide it) if even the two-row strip cannot spare its width."""
        gaps = 5 * theme.SPACE_S
        avail = self.width()
        badge = self.badge.sizeHint().width()
        if avail >= self._caption_w(self._role_full) + badge + gaps:
            role, shown = self._role_full, True
        elif avail >= self._caption_w(self._role_short) + badge + gaps:
            role, shown = self._role_short, True
        else:
            role, shown = self._role_short, False   # too narrow even for "REF" beside the Δ
        if self.caption.text() != role:
            self.caption.setText(role)
        if self.caption.isVisibleTo(self) != shown:
            self.caption.setVisible(shown)
        self.strip.set_two_row(two_row)

    def floor_width(self) -> int:
        """The narrowest this pane can be and still SAY WHAT IT IS: the short role word beside the
        Δ, inside the bar's own five gaps — the last rung of `apply_strip_form`'s ladder, below
        which the role word is hidden and the pane stops identifying itself.

        WIDTH-INDEPENDENT on purpose (font metrics and the badge's own hint, nothing else), which
        `minimumSizeHint` is not: the strip's minimum SHRINKS as the cell narrows, because
        `fit_alone` re-derives the picker's floor from `picker_room`, which is a function of the
        cell's current width. Clamping a drag against that ratchets — measured, a pane walked down
        to 63 px against a 187 px hint, one drag step at a time. See `VideoView._clamp_panes`."""
        return (self._caption_w(self._role_short) + self.badge.sizeHint().width()
                + 5 * theme.SPACE_S)

    def picker_room(self) -> int:
        """How much width THIS cell can give its picker — the cap that replaced a constant.

        The shipped floor was `max(150, min(sizeHint, 260))` and Qt honours an explicit
        `minimumWidth` OVER the space the layout has (qSmartMinSize takes it in place of
        minimumSizeHint), so in cross-recording compare the combo stood 260 px wide inside a 254 px
        pane: it painted 21 px past its own cell with the drop-arrow sliced off by the panel edge,
        and at 1280x800 its minimum dragged the whole strip wider than the cell and clipped
        `REFERENCE` to `REFEREN` and the Δ badge to `ame lap`. A constant cannot know how much room
        a pane has; the pane can. Below its content width the combo ELIDES its current item, which
        is a QComboBox's own graceful behaviour and leaves the full label in the popup and in the
        tooltip — where the QLabels beside it would have silently clipped mid-word."""
        if self.strip.two_row:
            # the picker owns a row of its own: only the bar's two margins are spent on it
            return max(self.width() - 2 * theme.SPACE_S, 0)
        return max(self.width() - (5 * theme.SPACE_S + self.badge.sizeHint().width()
                                   + (self._caption_w(self.caption.text())
                                      if self.caption.isVisibleTo(self) else 0)), 0)

    def set_picker_width(self, floor: int) -> None:
        """Pin the picker's width floor to the one `VideoView` derived for BOTH panes."""
        if self.picker.minimumWidth() != floor:
            self.picker.setMinimumWidth(floor)

    def fit_alone(self) -> None:
        """Re-fit this cell against its own width — the resize path, before the sibling cell is
        known to exist (a secondary is created lazily). VideoView._fit_strips re-runs across the
        pair as soon as there is a pair."""
        self.apply_strip_form(bool(self.strip.two_row))
        self.set_picker_width(min(self.picker.sizeHint().width(), self.picker_room()))

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self.fit_alone()

    def showEvent(self, ev):
        super().showEvent(ev)
        self.fit_alone()

    # ------------------------------------------------------------------ content
    def set_lap_choices(self, lap_ids: list[int], current: int,
                        labels: list[str] | None = None):
        """(Re)populate the picker with `lap_ids`/`labels` (labels default "lap {id}") and select
        `current` WITHOUT emitting a repoint. Skips the clear+repopulate when ids+labels are
        unchanged (avoids per-repoint QComboBox churn)."""
        ids = list(lap_ids)
        labels = list(labels) if labels is not None else [f"lap {lid}" for lid in ids]
        self.picker.blockSignals(True)
        if ids != self._lap_ids or labels != self._labels:
            self._lap_ids = ids
            self._labels = labels
            self.picker.clear()
            for lid, text in zip(ids, labels, strict=True):  # parallel by construction
                self.picker.addItem(text, lid)
        if current in self._lap_ids:
            idx = self._lap_ids.index(current)
            if self.picker.currentIndex() != idx:
                self.picker.setCurrentIndex(idx)
        self.picker.blockSignals(False)
        # AdjustToContents sizes the HINT to the widest item (frame + arrow included) — the width at
        # which the lap time stops being elided — and picker_room caps that against the cell. The
        # tooltip carries the current item in full, so an elided combo is still readable without
        # opening it.
        self.picker.setToolTip(f"Pick the lap shown in this pane — {self.picker.currentText()}"
                               if self.picker.currentText() else "Pick the lap shown in this pane")
        self.fit_alone()

    def set_caption(self, text: str):
        """Compat shim: the app passes rich "lap N · time" text (cross-recording compare prefixes
        the reference RECORDING); show it as the role label's TOOLTIP — the label stays the fixed
        role word, identity lives in the picker. The tooltip also carries the FULL role word, which
        the label itself drops at narrow widths."""
        self.caption.setToolTip(f"{self._role_full} — {text}" if text else self._role_full)

    def set_badge(self, text: str, colour: str | None):
        """Set the Δ badge text/colour (app-driven per tick), guarded: re-apply only on an actual
        change so a stable compare view does zero per-tick label work (setText relayout / QSS
        re-parse). A changed Δ can change the badge's width, so re-fit the picker beside it."""
        if text != self._badge_text:
            self._badge_text = text
            self.badge.setText(text)
            self.fit_alone()
        if colour != self._badge_colour:
            self._badge_colour = colour
            # ONE call, both branches: an empty sheet clears any previous tint back to the themed
            # #PaneBadge colour. (A per-tick colour MERGE over a role is one of the handful of
            # setStyleSheet calls the control-vocabulary phase deliberately kept — see
            # tests/test_inline_styles.py, which lists them by owner.)
            self.badge.setStyleSheet(
                f"QLabel#PaneBadge {{ color: {colour}; }}" if colour else "")

    def _on_pick(self, index: int):
        if 0 <= index < len(self._lap_ids):
            self.repointRequested.emit(self._lap_ids[index])


class VideoView(QWidget):
    positionChanged = Signal(float)  # GLOBAL seconds on the session clock (forwarded from the pane)
    chapterChanged = Signal(int)     # current chapter index (forwarded from the PRIMARY pane)
    seamLoading = Signal(bool)       # PRIMARY pane is reopening the next chapter at a seam (app hint)
    compareToggled = Signal(bool)    # user INTENT: the compare button flipped (CompareController owns
                                     # the decision and calls back set_compare / exit_compare)
    # A pane's lap picker was used: (side, lap_id) — app repoints that side (lap+window+caption+
    # chart overlay + badge). side is PRIMARY (0) or SECONDARY (1).
    paneRepointRequested = Signal(int, int)
    # User asked to toggle "video focus" — make the video fill the whole screen (the ⤢ transport
    # button OR a double-click on the video content). CentralView owns the enter/exit; the shell only
    # forwards the intent (mirrors compareToggled's input-only contract).
    videoFocusRequested = Signal()
    # The two-pane stage was mounted / dropped — a LAYOUT fact, emitted on the transition only, for
    # the shell to reflect in the panel's identity row (the COMPARING chip) and in the tab chain.
    # Not a state mirror: CompareController remains the single source of truth for 'are we
    # comparing', and this says only what the view now holds.
    compareModeChanged = Signal(bool)

    def __init__(self, source: str | chapters.ChapterMap | None):
        super().__init__()
        # Remembered so the lazy secondary pane can open the SAME ChapterMap.
        self._source = source
        # The PRIMARY pane owns the decode/overlay stack and is ALWAYS the telemetry driver.
        self.pane = PlayerPane(source)
        self.pane.positionChanged.connect(self._on_pane_position)
        self.pane.chapterChanged.connect(self.chapterChanged)
        self.pane.playbackStateChanged.connect(self._on_state)
        # Forward only the PRIMARY pane's seam-reopen hint (the secondary is video-only).
        self.pane.seamLoading.connect(self.seamLoading)
        # A double-click on the PRIMARY video content requests video focus (make the video fill the
        # screen). Only the primary drives it; the secondary is video-only and never toggles focus.
        # Gated on the ⤢ button's enabled state so the gesture and the button agree about when the
        # request is even available (compare mode refuses it).
        self.pane.videoDoubleClicked.connect(self._on_video_double_clicked)

        # PRIMARY pane's lap window while in compare mode, else None. Confines the scrub slider to
        # lap A; the pair stays in sync only via _compare_seek_fanout (the window alone doesn't).
        self._lap_window: tuple[float, float] | None = None
        # compare-mode seek fan-out to pane B (app-set); None outside compare. See _on_slider_moved.
        self._compare_seek_fanout: object = None
        # observed per-chapter video-track durations (ms); the slider ranges to the larger of these
        # and the GPMF total (see _on_duration / _whole_session_max_ms).
        self._chapter_video_ms: dict[int, int] = {}
        # last g-meter source + visibility, so a lazily-created secondary pane is seeded on entry.
        self._gmeter_source: str | None = None
        self._gmeter_long_source: str | None = None
        self._gmeter_visible = False
        self.secondary: PlayerPane | None = None
        # the source the live secondary opened on (normally self._source; the reference ChapterMap
        # for cross-recording compare); a source change rebuilds the secondary.
        self._secondary_source: object = None
        self._cell_a: _PaneCell | None = None   # primary cell wrapper (compare mode)
        self._cell_b: _PaneCell | None = None   # secondary cell wrapper (compare mode)
        self._splitter: QSplitter | None = None
        # lap-boundary positions (seconds) for the slider's lap ruler; re-applied on range changes.
        self._lap_boundaries_s: list[float] = []

        # Phosphor-icon transport buttons; icons set once per state change, never on the playback tick.
        # Play and mute are plain icon buttons (a press, not a state you can see in the button);
        # the other three are ToggleButtons, which own the "recolour the glyph on toggled" wiring
        # that used to be re-written at each of these call sites.
        self.play_btn = icon_button("ph.play-fill", tooltip="Play / pause (Space)")
        self.play_btn.clicked.connect(self.toggle)

        # mute/unmute toggle. speaker-x while muted (default), speaker-high while audible. NOT a
        # ToggleButton: it is not checkable — muted is the default state, so a checked-when-muted
        # button would open latched amber, and the glyph swap already says which state it is in.
        self.mute_btn = icon_button("ph.speaker-simple-x",
                                    tooltip="Audio muted — click to unmute (M)")
        self.mute_btn.clicked.connect(self.toggle_mute)

        # g-meter show/hide toggle. Checkable: QSS :checked tints the button; the glyph also goes accent.
        self.gmeter_btn = ToggleButton(glyph="ph.gauge", icon_only=True,
                                       tooltip="Show/hide the g-meter overlay (G)")
        self.gmeter_btn.toggled.connect(self.set_gmeter_visible)

        # Icon-only compare toggle (same transport vocab as g-meter). Off by default, enabled only
        # with >=2 laps. The button is a pure INPUT: a user click just emits compareToggled (the
        # intent), and CompareController — the single source of truth for 'are we comparing' — calls
        # back set_compare / exit_compare, which render the layout AND reflect the authoritative
        # checked/appearance state onto this button (see _set_compare_visual). The button never holds
        # or mirrors compare state itself.
        # It carries a TEXT LABEL, not just a glyph: compare is one of the app's three headline
        # capabilities and "compare" appeared in no visible string anywhere in the window — only in
        # this button's tooltip, which a user has to already suspect the feature exists to find.
        self.compare_btn = ToggleButton("Compare", glyph="ph.columns")
        self.compare_btn.setEnabled(False)
        self.compare_btn.toggled.connect(self._sync_compare_tooltip)        # wording follows checked
        self.compare_btn.toggled.connect(self.compareToggled)               # emit the user intent
        self._sync_compare_tooltip(False)

        # "Fullscreen video" toggle (⤢): make the video fill the whole screen, like a normal player.
        # A pure INPUT (mirrors compare_btn): a click just emits videoFocusRequested; CentralView owns
        # the enter/exit and reflects the resulting on/off appearance back via set_video_focus_visual.
        # It is DISABLED while the compare stage is mounted, because CentralView refuses the gesture
        # there: a checkable button whose click is silently refused latches into a checked state it
        # did not earn (indistinguishable from a genuinely-on toggle).
        # `glyph_on` rather than a re-tint: `ph.arrows-in` (contract) reads as "exit fullscreen"
        # while on and `ph.arrows-out` (expand) as "enter", so the state survives without colour.
        self.fullscreen_btn = ToggleButton(glyph="ph.arrows-out", glyph_on="ph.arrows-in",
                                           icon_only=True)
        self.fullscreen_btn.clicked.connect(self.videoFocusRequested)  # a genuine click = the intent
        self._sync_fullscreen_tooltip()
        # F is the ⤢ button's key (it had none anywhere in the app — only a click or a double-click
        # on the video). Routed through the BUTTON, like app.py's G/C, so a disabled button (compare
        # mode) makes the key a no-op instead of a silently-refused state change.
        self._focus_shortcut = QShortcut(QKeySequence(Qt.Key_F), self)
        self._focus_shortcut.setContext(Qt.WindowShortcut)
        self._focus_shortcut.activated.connect(self._on_focus_shortcut)

        # global-ms scrub slider over the whole session (multi-chapter summed); _LapRulerSlider
        # paints lap ticks.
        self.slider = _LapRulerSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setSingleStep(1000)   # wheel/←→ step 1s
        self.slider.setPageStep(5000)     # page step 5s
        # The panel's primary hit target: a HIT_MIN handle in a widget one sub-step taller clears the
        # pointer floor. The handle's own geometry is the theme's QSlider#ScrubBar rule now (the
        # themed groove/colours already cascaded); it was the last dimensional stylesheet the app
        # built inside a view, and the one number in it that could not be a token — half of 24 — is
        # theme.pill_radius(HIT_MIN).
        self.slider.setObjectName("ScrubBar")
        self.slider.setMinimumHeight(_SLIDER_H)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        # groove clicks are actionTriggered not sliderMoved — route them through the same clamped seek.
        self.slider.actionTriggered.connect(self._on_slider_action)
        if self.pane.total_duration > 0:
            self.slider.setRange(0, int(self.pane.total_duration * 1000))
        self.pane.durationChanged.connect(self._on_duration)

        # The transport controls must never take keyboard focus, or they'd swallow Space/arrows and
        # break the window-level shortcuts (mouse interaction needs no focus).
        for w in (self.play_btn, self.mute_btn, self.gmeter_btn, self.compare_btn,
                  self.fullscreen_btn, self.slider):
            w.setFocusPolicy(Qt.NoFocus)

        self.readout = QLabel("")  # the media TIMECODE, driven by the app
        self.readout.setObjectName("Readout")  # caption style, dimmed, tabular (global QSS)
        # LEFT, not centred: it is now inline beside the ▶ it describes, not a full-width footer
        # band whose centre drifted to x=716 of a 1432 px bar when the panel was maximized.
        self.readout.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        # The scrub bar gets its OWN full-width row under the video, the way every media player lays
        # a transport out. Sharing one row with the buttons cost it ~200px of travel (16.4 s per
        # pixel on a 65-lap session, an unreadable lap ruler) and left no width for a text label on
        # the compare button. That row is now A BAR: it wears the same themed surface + hairline,
        # the same SPACE_S gutter and the same TOOLBAR_H as the map's and the charts' toolbars, so
        # the groove and its HIT_MIN handle stop running edge to edge against the panel border while
        # the `VIDEO` label 36 px above sits inset 8.
        self.scrub_row = QWidget()
        self.scrub_row.setProperty("role", "PanelHeader")
        # Redundant TODAY — a BARE QWidget gets WA_StyledBackground from QStyleSheetStyle::polish —
        # and set anyway, because the day this becomes a subclass it silently stops painting and
        # nothing says so. That is how four panel headers and both toolbars went flat in #185.
        self.scrub_row.setAttribute(Qt.WA_StyledBackground, True)
        self.scrub_row.setFixedHeight(theme.TOOLBAR_H)
        scrub_lay = QHBoxLayout(self.scrub_row)
        scrub_lay.setContentsMargins(theme.SPACE_S, theme.SPACE_XXS,
                                     theme.SPACE_S, theme.SPACE_XXS)
        scrub_lay.setSpacing(0)
        scrub_lay.addWidget(self.slider, 1, Qt.AlignVCenter)

        # The transport, as ONE PanelToolbar with TWO GROUPS around its stretch. Shipped, the five
        # buttons sat at a uniform SPACE_XS in a bare layout at x=0 on the window canvas, so two
        # PLAYBACK controls and three VIEW toggles read as one undifferentiated 4 px run — and
        # maximized they stayed a 211 px cluster in the bottom-left corner of a 1432 px panel with
        # the timecode centred 716 px away. Grouped, the gap says which controls belong together:
        # SPACE_XS within a group, the bar's own SPACE_S between them.
        self.transport = PanelToolbar(
            (self.gmeter_btn, self.compare_btn, self.fullscreen_btn),
            leading=((self.play_btn, self.mute_btn), self.readout))

        # The STAGE holds the video surface(s): one pane normally, a 2-pane splitter in compare
        # mode. Its layout is rebuilt on enter/exit compare; everything else (the two bars) is
        # untouched. In single mode the primary pane sits directly in the stage layout.
        self._stage = QWidget()
        self._stage_lay = QVBoxLayout(self._stage)
        self._stage_lay.setContentsMargins(0, 0, 0, 0)
        self._stage_lay.addWidget(self.pane, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        # ZERO, because the bands meet at their own hairlines now, exactly as a panel's header meets
        # its toolbar meets its content. The 4 px it was put canvas between four bands that had no
        # rule between any of them.
        lay.setSpacing(0)
        lay.addWidget(self._stage, 1)
        lay.addWidget(self.scrub_row)
        lay.addWidget(self.transport)

    # ------------------------------------------------------------- public API (drives the pane)
    def _panes_mounted(self) -> bool:
        """True iff the two-pane compare stage is currently mounted — DERIVED from the live widget
        tree (the splitter is in the stage layout), not a mirrored flag. The secondary pane + cells
        exist only while this is on; the splitter is built and stage-swapped on enter, dropped on
        exit. CompareController owns the SEMANTIC 'are we comparing' state; this is purely the view's
        own layout fact, read by the fan-out/g-meter helpers that act on whichever panes are live."""
        return (self._splitter is not None
                and self._stage_lay.indexOf(self._splitter) != -1)

    @property
    def stage(self) -> QWidget:
        """The video panel's BODY — the picture (one pane, or the two-pane compare splitter), with
        none of this view's own chrome in it.

        Public because a transient overlay has to be able to ask for it. `CentralView.overlay_anchor`
        hands the PB toast a panel's body, and for map and charts that is `self.map` / `self.plots`
        — the content widget, headers and toolbars excluded by construction. `self.video` is not the
        equivalent: it is the panel body PLUS the transport, so a card placed SPACE_M above its
        bottom edge lands on the transport bar. (It always did — on 87 px of scrub row, buttons and
        readout — but none of those was a declared `PanelToolbar`, so nothing could see it.)"""
        return self._stage

    @property
    def is_multi(self) -> bool:
        return self.pane.is_multi

    def current_chapter(self) -> int:
        return self.pane.current_chapter()

    def is_playing(self) -> bool:
        return self.pane.is_playing()

    def play(self):
        """Play — fans out to BOTH panes in compare mode (each rolls from its own lap start)."""
        self.pane.play()
        if self.secondary is not None:
            self.secondary.play()

    def pause(self):
        self.pane.pause()
        if self.secondary is not None:
            self.secondary.pause()

    def pause_if_playing(self):
        """Pause each pane only if actually playing. pause() on a never-played (Stopped) pane makes
        the next play() restart from 0, discarding a seek-to-S/F — so the compare reset uses this to
        keep each pane parked at its lap start."""
        if self.pane.is_playing():
            self.pane.pause()
        if self.secondary is not None and self.secondary.is_playing():
            self.secondary.pause()

    def toggle(self):
        # Drive both panes from the PRIMARY's state so they stay in lockstep.
        if self.pane.is_playing():
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float):
        """Seek to global session time via the primary pane's chapter-aware seek."""
        self.pane.seek(seconds)

    def step(self, seconds: float):
        """Step ±`seconds`, clamped to the slider range, through the slider-move seek path (so
        compare-window confinement applies)."""
        ms = int((self.pane.current_global_time() + seconds) * 1000)
        self._on_slider_moved(min(max(ms, self.slider.minimum()), self.slider.maximum()))

    def seek_pane(self, side: int, seconds: float):
        """Seek ONE pane (PRIMARY/SECONDARY) to a global time — used by the distance-locked scrub
        and the picker repoint so each pane parks on its own lap's track position independently."""
        pane = self._pane_for(side)
        if pane is not None:
            pane.seek(seconds)

    def current_pane_time(self, side: int) -> float:
        """The current global time of one pane (PRIMARY/SECONDARY), for the per-tick badge/g feed."""
        pane = self._pane_for(side)
        return pane.current_global_time() if pane is not None else 0.0

    def _pane_for(self, side: int) -> PlayerPane | None:
        if side == PRIMARY:
            return self.pane
        return self.secondary

    def _cell_for(self, side: int) -> _PaneCell | None:
        """The compare-mode cell wrapper for a side (mirrors _pane_for). None outside compare."""
        return self._cell_a if side == PRIMARY else self._cell_b

    def _panes(self) -> list[PlayerPane]:
        """The live panes: primary always, plus the secondary while in compare mode (for fan-outs
        that treat both identically)."""
        return [p for p in (self.pane, self.secondary) if p is not None]

    def stop_all(self):
        """Fully dispose both panes for a reload (the whole VideoView is replaced after): dispose()
        not stop(), or the FFmpeg decoder + audio device leak — player/audio are plain attrs, not Qt
        children of the discarded widget tree."""
        self._teardown_secondary()
        self.pane.dispose()

    def set_readout(self, text: str):
        self.readout.setText(text)

    def set_lap_ticks(self, boundaries_s: list[float]) -> None:
        """Store the lap-boundary ruler ticks (global seconds) and (re)apply them — re-applied on
        range changes. Shown only in single-video mode (cleared in compare; see _apply_lap_ticks)."""
        self._lap_boundaries_s = list(boundaries_s)
        self._apply_lap_ticks()

    def _apply_lap_ticks(self) -> None:
        """(Re)push the stored lap boundaries onto the slider as ms ticks — but only in single-video
        mode (the whole-session range). In compare mode the slider is confined to lap A's window, so
        clear the ruler there."""
        if self._panes_mounted() or not getattr(self, "_lap_boundaries_s", None):
            self.slider.set_lap_ticks([])
        else:
            self.slider.set_lap_ticks([int(round(s * 1000)) for s in self._lap_boundaries_s])

    # ------------------------------------------------------------- compare toggle / enablement
    def set_compare_enabled(self, enabled: bool):
        """Enable the "Compare videos" toggle only when ≥2 valid laps exist (app drives this).
        When it goes disabled while compare is ON (e.g. a reload to a session with <2 laps), the
        button un-checks, which emits compareToggled(False) so CompareController tears compare down
        (and calls back exit_compare to restore the single-pane layout)."""
        self.compare_btn.setEnabled(bool(enabled))
        if not enabled and self.compare_btn.isChecked():
            self.compare_btn.setChecked(False)  # -> compareToggled(False) -> controller exits

    def _sync_compare_tooltip(self, on: bool):
        """Drive the compare toggle's OFF/ON WORDING to track its checked state. The glyph's accent
        is the ToggleButton's own job now; only the sentence is left here, because it names the
        gesture rather than the state. Wired to `toggled` and re-applied by _set_compare_visual;
        never per tick."""
        self.compare_btn.setToolTip(
            "Comparing two laps' videos side-by-side — click to exit (C)" if on else
            "Compare two laps' videos side-by-side (C) — needs ≥2 valid laps")

    def _set_compare_visual(self, on: bool):
        """Reflect CompareController's compare state onto the button WITHOUT re-emitting compareToggled
        — a live emit would re-enter the controller's on_toggled and run a conflicting second
        enter/exit (the PR#81 re-entrancy that corrupted cross-recording pane B). Called only from the
        layout verbs (set_compare / exit_compare), i.e. when the controller — the single source of
        truth — drives the layout; a genuine user click reaches the controller via the button's own
        live `toggled`. This is the controller→view reflection, not a view-owned flag mirror."""
        if self.compare_btn.isChecked() != on:
            self.compare_btn.blockSignals(True)
            self.compare_btn.setChecked(on)
            self.compare_btn.blockSignals(False)
            # blockSignals suppressed the ToggleButton's own `toggled`-driven repaint, so the glyph
            # is re-applied explicitly here — the reflection path is the one place a checked state
            # changes without the button hearing about it.
            self.compare_btn.refresh_glyph()
        self._sync_compare_tooltip(on)

    # ------------------------------------------------------------- fullscreen-video toggle
    def _sync_fullscreen_tooltip(self):
        """Drive the ⤢ button's tooltip to track video-focus state (the glyph swap + accent is the
        ToggleButton's). While the gesture is unavailable (compare mode) the tooltip says WHY, in
        the same shape as the compare button's own "— needs ≥2 valid laps"."""
        on = self.fullscreen_btn.isChecked()
        if not self.fullscreen_btn.isEnabled():
            tip = "Make the video fill the screen (F) — not while comparing two laps"
        elif on:
            tip = "Exit fullscreen video (F, Esc, or double-click the video)"
        else:
            tip = "Make the video fill the screen (F, or double-click the video)"
        self.fullscreen_btn.setToolTip(tip)

    def _sync_fullscreen_enabled(self):
        """The ⤢ gesture is single-video only (CentralView refuses it while comparing — compare owns
        the two-pane stage). Reflect that as the button's ENABLED state, so a refused click can no
        longer leave the button latched checked: Qt toggles a checkable button before anyone can
        refuse the intent, and the resulting checked tint is pixel-identical to a genuinely-on
        toggle. Also drops any stale checked state on the way in (no re-emit)."""
        available = not self._panes_mounted()
        if self.fullscreen_btn.isEnabled() != available:
            self.fullscreen_btn.setEnabled(available)
        if not available and self.fullscreen_btn.isChecked():
            self.fullscreen_btn.blockSignals(True)
            self.fullscreen_btn.setChecked(False)
            self.fullscreen_btn.blockSignals(False)
            self.fullscreen_btn.refresh_glyph()   # blockSignals suppressed the button's own repaint
        self._sync_fullscreen_tooltip()

    def _on_focus_shortcut(self):
        """F → the ⤢ button's own click (so the disabled state gates the key too)."""
        if self.fullscreen_btn.isEnabled():
            self.fullscreen_btn.click()

    def _on_video_double_clicked(self):
        """Double-click on the video = the same intent as ⤢, and available exactly when it is."""
        if self.fullscreen_btn.isEnabled():
            self.videoFocusRequested.emit()

    def set_video_focus_visual(self, on: bool):
        """Reflect CentralView's video-focus state onto the ⤢ button WITHOUT re-emitting
        videoFocusRequested (a live emit would re-enter the toggle). Called only by the shell when it
        drives the enter/exit — mirrors _set_compare_visual's controller→view reflection."""
        if self.fullscreen_btn.isChecked() != on:
            self.fullscreen_btn.blockSignals(True)
            self.fullscreen_btn.setChecked(on)
            self.fullscreen_btn.blockSignals(False)
            self.fullscreen_btn.refresh_glyph()   # blockSignals suppressed the button's own repaint
        self._sync_fullscreen_tooltip()

    def set_compare(self, pane_a: PaneSpec, pane_b: PaneSpec):
        """Enter or re-seed compare mode: swap the single pane for a 2-pane QSplitter. pane_a is the
        existing self.pane (telemetry driver); the SECONDARY is created lazily, muted, video-only.
        pane_b.source None = same recording, else a DIFFERENT recording in pane B (a source change
        rebuilds the secondary); its choices/labels drive pane B's picker (cross-recording locks it
        to the reference). Each pane gets its window + caption + picker; the app seeks each to its
        lap start. Re-calling in compare mode just re-seeds (after a repoint), no splitter rebuild."""
        # The secondary pane's media source: an explicit cross-recording source, else the primary's.
        sec_source = pane_b.source if pane_b.source is not None else self._source
        # If the live secondary opened on a DIFFERENT source (same-recording ↔ cross-recording, or
        # a primary reload), tear it (and its splitter cell) down so it is rebuilt on the new
        # footage below. `_teardown_secondary` only drops the pane; drop the stale _cell_b too.
        if self.secondary is not None and self._secondary_source is not sec_source:
            self._teardown_secondary()
            if self._cell_b is not None and self._splitter is not None:
                self._cell_b.setParent(None)
                self._cell_b.deleteLater()
                self._cell_b = None
        # Lazily create the secondary pane on first entry (or after a source-change teardown).
        if self.secondary is None:
            self.secondary = PlayerPane(sec_source)
            self._secondary_source = sec_source
            self.secondary.set_muted(True)  # secondary audio ALWAYS muted (telemetry tool)
            # IMPORTANT: do NOT connect the secondary's positionChanged to _on_pane_position —
            # it must NEVER reach the app's telemetry sync. It is video-only.
            # Seed the fresh secondary with the ACTIVE g-meter source + visibility so toggling the
            # g-meter ON *then* entering compare shows the overlay on BOTH panes with the right
            # source (the secondary missed the earlier set_gmeter_source / set_gmeter_visible).
            if self._gmeter_source is not None:
                self.secondary.set_gmeter_source(self._gmeter_source, self._gmeter_long_source)
            self.secondary.set_gmeter_visible(self._gmeter_visible)
            # Wire the secondary's playback state so the transport glyph reflects BOTH panes (they
            # auto-pause at different lap ends; the glyph must not lie — see _on_state).
            self.secondary.playbackStateChanged.connect(self._on_state)
        # The secondary's cell wrapper: (re)created here when missing — either first entry (with the
        # splitter below) or after a source-change rebuilt the secondary while the splitter lives.
        if self._cell_b is None and self._splitter is not None:
            self._cell_b = _PaneCell(self.secondary, SECONDARY)
            self._cell_b.repointRequested.connect(
                lambda lid: self.paneRepointRequested.emit(SECONDARY, lid))
            self._splitter.insertWidget(SECONDARY, self._cell_b)
            self._equalize_panes()
            self._cell_b.show()
        if self._splitter is None:
            self._cell_a = _PaneCell(self.pane, PRIMARY)
            self._cell_b = _PaneCell(self.secondary, SECONDARY)
            self._cell_a.repointRequested.connect(
                lambda lid: self.paneRepointRequested.emit(PRIMARY, lid))
            self._cell_b.repointRequested.connect(
                lambda lid: self.paneRepointRequested.emit(SECONDARY, lid))
            self._splitter = QSplitter(Qt.Horizontal)
            self._splitter.addWidget(self._cell_a)
            self._splitter.addWidget(self._cell_b)
            # Real drag handle, no collapse, opaque resize. Ignored/Expanding cells stop the
            # QVideoWidget aspect hint pinning the split; 1:1 stretch keeps 50/50. The width is the
            # TOKEN, not the literal 8 it duplicated — the QSS draws this handle's grip from
            # theme.SPLITTER_HANDLE_PX, so a literal here is a second copy of one number.
            self._splitter.setHandleWidth(theme.SPLITTER_HANDLE_PX)
            # setChildrenCollapsible(False) is kept for what it does say — no double-click collapse
            # — but it does NOT carry the floor on its own here; `_clamp_panes` does. See there.
            self._splitter.setChildrenCollapsible(False)
            self._splitter.setOpaqueResize(True)
            for cell in (self._cell_a, self._cell_b):
                cell.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            self._splitter.setStretchFactor(0, 1)
            self._splitter.setStretchFactor(1, 1)
            self._equalize_panes()
            # also re-pin overlays on handle drag (belt-and-braces; the native surface may not emit a Move).
            self._splitter.splitterMoved.connect(self._on_splitter_moved)
            # ...and keep an even split even when the STAGE resizes — see eventFilter for why the
            # hook is the splitter's own resize and not this view's.
            self._splitter.installEventFilter(self)

        # Swap the stage layout to the splitter (the primary pane re-parents into _cell_a). Guarded on
        # the DERIVED mounted state so a re-seed (already two-pane) doesn't re-swap.
        entering = not self._panes_mounted()
        if entering:
            self._stage_lay.removeWidget(self.pane)
            self._stage_lay.addWidget(self._splitter, 1)
            self.secondary.show()
            self._splitter.show()
        # equalize now and again next event-loop turn (setSizes needs a real width — see _equalize_panes)
        self._equalize_panes()
        QTimer.singleShot(0, self._equalize_panes)
        # Reflect the controller's compare-ON state onto the button (no re-emit; see _set_compare_visual).
        self._set_compare_visual(True)
        # The panel now holds TWO videos where it held one, its scrub bar spans a lap instead of the
        # session and its lap ruler is empty — say so in the identity row. On the TRANSITION only:
        # a re-seed after a picker repoint is not a mode change.
        if entering:
            self.compareModeChanged.emit(True)

        # Seed each pane's window + caption + picker from its spec (the app seeks the panes to their starts).
        self.pane.set_lap_window(*pane_a.window)
        self.secondary.set_lap_window(*pane_b.window)
        self._cell_a.set_caption(pane_a.caption)
        self._cell_b.set_caption(pane_b.caption)
        self._cell_a.set_lap_choices(pane_a.choices, pane_a.lap_id, pane_a.choice_labels)
        self._cell_b.set_lap_choices(pane_b.choices, pane_b.lap_id, pane_b.choice_labels)
        self._fit_strips()   # the picker contents just changed -> re-pick ONE form for both panes
        # Confine the global scrub slider to lap A's window so a drag can't escape the lap.
        self._set_slider_window(pane_a.window)
        self._apply_lap_ticks()  # confined to one lap now -> the whole-session lap ruler is cleared
        self._sync_fullscreen_enabled()  # the ⤢ gesture is refused while comparing — say so

    def reseed_pane(self, side: int, spec: PaneSpec):
        """Repoint ONE pane (after its lap picker was used) from its new `PaneSpec`: update its lap
        window + caption + keep the picker selection in sync. The app re-seeks this pane to its new
        lap start and refreshes the chart overlay + Δ badge. Used so a repoint never disturbs the
        other pane. F8b: takes the same per-side `PaneSpec` as `set_compare` (only the side's lap/
        window/caption/picker change — never the media source, which a repoint keeps)."""
        pane = self._pane_for(side)
        cell = self._cell_for(side)
        if pane is None or cell is None:
            return
        pane.set_lap_window(*spec.window)
        cell.set_caption(spec.caption)
        cell.set_lap_choices(spec.choices, spec.lap_id, spec.choice_labels)
        self._fit_strips()   # a new widest item can change which strip form both panes take
        # A PRIMARY repoint changes lap A's window — re-confine the global scrub to the new window
        # so the slider keeps tracking the (telemetry-driving) primary pane within its lap.
        if side == PRIMARY:
            self._set_slider_window(spec.window)

    def exit_compare(self):
        """Leave compare mode: tear the secondary pane down (stop + deleteLater player+audio,
        .close() overlay) and restore the single-pane stage at the PRIMARY's current position.
        The primary pane keeps decoding the whole session again (its lap window is cleared)."""
        if not self._panes_mounted():
            return
        # Reflect the controller's compare-OFF state onto the button (no re-emit; see _set_compare_visual).
        self._set_compare_visual(False)
        # Restore the single-pane stage: pull the primary pane out of its cell, drop the splitter.
        self._stage_lay.removeWidget(self._splitter)
        # Reparent the primary pane back into the stage (out of _cell_a) BEFORE deleting the cells.
        self._stage_lay.addWidget(self.pane, 1)
        self.pane.show()
        self.pane.clear_lap_window()  # whole session again — normal mode, behaviour unchanged
        # Drop the slider's lap-A confinement and restore the whole-session range. D6: use the
        # reconciled video/GPMF max (not the GPMF total alone) so the handle still spans the whole
        # playable video after leaving compare.
        self._lap_window = None
        full_ms = self._whole_session_max_ms()
        if full_ms > 0:
            self.slider.setRange(0, full_ms)
        self.slider.set_span_note("")    # the whole session again, which needs no saying
        self._apply_lap_ticks()  # whole-session range again -> restore the lap ruler
        self._teardown_secondary()
        # Drop the cell wrappers + splitter (the primary pane has been reparented out of _cell_a).
        for w in (self._cell_a, self._cell_b, self._splitter):
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._cell_a = self._cell_b = self._splitter = None
        self._sync_fullscreen_enabled()  # single video again -> the ⤢ gesture is back
        self.compareModeChanged.emit(False)  # drop the identity row's COMPARING chip

    def _teardown_secondary(self):
        """STOP + close the secondary pane's overlay and schedule the pane (its player+audio) for
        deletion, so no decoder or detached top-level overlay window leaks. No-op if there is no
        secondary. Leaves self.secondary None so the next enter-compare creates a fresh one."""
        sec = self.secondary
        self.secondary = None
        self._secondary_source = None  # the next (re)create re-records the source it opens on
        if sec is None:
            return
        sec.dispose()         # stop decoder + detach sinks + deleteLater player/audio/overlay
        sec.setParent(None)
        sec.deleteLater()     # schedule the pane widget itself for deletion on the event loop

    def compare_pickers(self) -> tuple[QComboBox | None, QComboBox | None]:
        """The two compare lap pickers, or (None, None) outside compare — so the shell can put them
        in the tab chain right after the video panel's own ⛶ instead of wherever Qt appended them.
        The cells are built LAZILY and Qt adds a new child at the END of the top-level focus chain,
        which made these the LAST two of seventeen tab stops in the window: sixteen presses to reach
        a control 40 px below where the user started."""
        if self._cell_a is None or self._cell_b is None:
            return None, None
        return self._cell_a.picker, self._cell_b.picker

    def _fit_strips(self) -> None:
        """Choose ONE strip form and apply it to BOTH panes, from whichever needs more room.

        Per-cell fitting is exactly how the two panes came to disagree: the form depends on the role
        word's width (`THIS LAP` 50 px against `REFERENCE` 64) and on each picker's own widest item
        (166 against 150), so at some widths pane A sat on one row while pane B sat on two — two
        side-by-side videos starting at different y, in a UI whose entire job is that they line up.
        Decided here, from data that does not depend on the current form (font metrics and content
        size hints), so it converges in one pass and cannot oscillate."""
        cells = [c for c in (self._cell_a, self._cell_b) if c is not None]
        if len(cells) < 2:
            return
        two_row = any(c.width() < c.strip_need() for c in cells)
        for c in cells:
            c.apply_strip_form(two_row)
        # ...and ONE picker width for both, for the same reason. Each picker used to floor on its
        # OWN widest item, so pane A (which lists the ★ best lap) stood 166 px against pane B's 150
        # — a 16 px difference between two controls that are the same control on either side of a
        # comparison, and 164 px in cross-recording compare. The pair takes the wider content need,
        # capped by the tighter pane's room.
        want = min(max(c.picker.sizeHint().width() for c in cells),
                   min(c.picker_room() for c in cells))
        for c in cells:
            c.set_picker_width(want)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_strips()   # the cells' width changed with the panel's

    def eventFilter(self, obj, event):
        """An EVEN split stays even across a resize.

        `_equalize_panes` ran only on ENTRY, so from then on Qt redistributed the stage's width
        delta by its own rules and the two panes drifted: swept at 1 px from the window's own 973
        minimum to 1500, 30 of 528 widths left the panes 2 px apart rather than the 1 px that is
        unavoidable arithmetic (two integers cannot sum to an odd number and be equal).

        THE HOOK IS THE SPLITTER'S OWN RESIZE, not this view's. `VideoView.resizeEvent` runs before
        the layout has given the splitter its new width, so equalizing there divides the OLD width
        and Qt then redistributes the difference — measured, doing it there turned 30 bad widths
        into 264 of 528. Here the splitter already has its new geometry.

        Gated on the panes CURRENTLY being even, so a deliberate drag to 30/70 survives every later
        resize (measured: 0.299 / 0.301 / 0.300 / 0.301 across 1000-1500 px). Re-equalizing
        unconditionally would throw the user's own split away, which is a worse defect than the
        pixel it fixes."""
        if obj is self._splitter and event.type() == QEvent.Resize and self._panes_even():
            self._equalize_panes()
        return super().eventFilter(obj, event)

    def _panes_even(self) -> bool:
        """Are the two panes still on the 50/50 split the stage opens at (within the odd pixel)?
        The question `eventFilter` asks before re-imposing it."""
        if self._splitter is None or self._splitter.count() < 2:
            return False
        sizes = self._splitter.sizes()
        return len(sizes) == 2 and abs(sizes[0] - sizes[1]) <= 1

    def _clamp_panes(self) -> None:
        """Keep a dragged pane at or above its own minimum — the floor `setChildrenCollapsible`
        cannot carry here.

        `setChildrenCollapsible(False)` refuses to collapse a child below its MINIMUM SIZE, and the
        cells are `QSizePolicy.Ignored` (deliberately: it is what stops the native QVideoWidget's
        aspect hint pinning the split). `qSmartMinSize` reads Ignored as "this widget has no width
        opinion" and returns 0 before it consults anything else, so the promise was vacuous and one
        real handle drag put a pane at 0 px — measured [253, 254] -> [507, 0] at 1440x900 — with no
        window resize, maximize, restore or repoint bringing it back.

        A CLAMP rather than `setMinimumWidth` on the cells, because a minimum propagates: pinning
        the cells at a 199 px floor raised the video panel's minimum width from 312 to 406 and the
        central widget's from 873 to 967, spent for a floor the user only meets at the very end of
        a drag. The clamp costs the layout nothing — measured, the panel's minimum is the same 312
        comparing as it is with one video — and cannot make a window un-resizable.

        The floor is each cell's `floor_width` — the width at which its identity bar stops being
        able to name the pane — and NOT its `minimumSizeHint`, which is a function of the cell's
        current width and therefore ratchets under a drag (measured: a pane walked down to 63 px
        against a 187 px hint). It yields when the whole stage cannot afford both floors (the panes
        then just split what there is), so this can never demand width that does not exist."""
        sp = self._splitter
        if sp is None or sp.count() < 2 or self._cell_a is None or self._cell_b is None:
            return
        sizes = sp.sizes()
        if len(sizes) != 2:
            return
        total = sizes[0] + sizes[1]
        lo = max(self._cell_a.floor_width(), 0)
        hi = max(self._cell_b.floor_width(), 0)
        a = total // 2 if lo + hi > total else min(max(sizes[0], lo), total - hi)
        if [a, total - a] != sizes:
            sp.setSizes([a, total - a])   # setSizes does not re-emit splitterMoved: no recursion

    def _equalize_panes(self):
        """Split the two panes 50/50 from the splitter's live width (falls back to a [1000,1000]
        ratio before any width is known).

        THE ODD PIXEL IS ARITHMETIC, NOT A CHOICE: two integer widths cannot sum to an odd number
        and be equal, so at a splitter width where `w - handle` is odd one pane is 1 px wider than
        the other. Measured, that is the whole of the panes' geometric asymmetry — the 16 px the
        two PICKERS used to differ by was not arithmetic at all (each floored on its own widest
        item) and is fixed at its source, in _PaneCell."""
        if self._splitter is None or self._splitter.count() < 2:
            return
        w = self._splitter.width()
        if w > 0:
            handle = self._splitter.handleWidth()
            half = max((w - handle) // 2, 1)
            self._splitter.setSizes([half, w - handle - half])
            self._fit_strips()
        else:
            self._splitter.setSizes([1000, 1000])

    def _on_splitter_moved(self, _pos: int, _index: int):
        """Re-pin BOTH g-meter overlays after a splitter-handle drag (each pane re-pins its own
        overlay to its video corner; cheap no-op when an overlay is hidden), and re-fit the strips
        for the cells' new widths."""
        self._clamp_panes()   # first: the strips below fit to whatever width survives the clamp
        for pane in self._panes():
            pane.sync_gmeter()
        self._fit_strips()

    # ------------------------------------------------------------- audio (mute)
    def toggle_mute(self):
        """F4: flip the PRIMARY audio mute state and update the button icon/tooltip. The secondary
        pane stays ALWAYS muted (a telemetry tool — never two audio streams at once)."""
        muted = not self.pane.is_muted()
        self.pane.set_muted(muted)
        # Secondary is always muted; never unmute it.
        if self.secondary is not None:
            self.secondary.set_muted(True)
        self.mute_btn.setIcon(theme.icon("ph.speaker-simple-x" if muted
                                         else "ph.speaker-simple-high"))
        self.mute_btn.setToolTip("Audio muted — click to unmute (M)" if muted
                                 else "Audio on — click to mute (M)")

    # ------------------------------------------------------------- g-meter overlay (drives pane)
    # `_on_gmeter_toggled` (recolour the glyph to the accent while the overlay is on) is gone: that
    # is what a ToggleButton IS, and it was one of the seven copies of it.
    def set_gmeter_visible(self, on: bool):
        """Show/hide the friction-circle g-meter overlay (the toggle button). Applies PER-PANE:
        both panes' overlays toggle together (each defaults off); the secondary's stays muted.
        The visible state is remembered so a LAZILY-created secondary (entering compare AFTER the
        toggle was switched on) is seeded with it on creation (see set_compare)."""
        self._gmeter_visible = bool(on)
        for pane in self._panes():
            pane.set_gmeter_visible(on)

    def is_gmeter_visible(self) -> bool:
        """True if the g-meter overlay is currently shown (the toggle is on). Lets the app SKIP the
        per-tick g_at_time lookup entirely when nothing consumes it (the overlay is off by default)."""
        return self._gmeter_visible

    def set_g(self, g):
        """Feed the current g to the PRIMARY pane's overlay (None blanks the dot). A no-op when the
        overlay is hidden, so the app can call it every tick. The SECONDARY pane's g is fed
        separately by the app (set_pane_g) from its own lap position in compare mode."""
        self.pane.set_g(g)

    def set_pane_g(self, side: int, g):
        """Feed one pane's g overlay (compare mode: the app feeds the secondary its own-lap g)."""
        pane = self._pane_for(side)
        if pane is not None:
            pane.set_g(g)

    def set_gmeter_source(self, source: str, long_source: str | None = None):
        # Remember the source so a LAZILY-created secondary pane can be seeded with it on entry
        # (set_compare), so the overlay reads the right sensor label on BOTH panes. `long_source`
        # is the longitudinal-axis provenance (GPS speed-derivative), tagged distinctly from the
        # IMU lateral axis.
        self._gmeter_source = source
        self._gmeter_long_source = long_source
        for pane in self._panes():
            pane.set_gmeter_source(source, long_source)

    def set_gmeter_lap(self, lap_id):
        """Tell the PRIMARY overlay which lap is being driven (per-lap max-G envelope scope). In
        compare mode the panes' lap scope is fixed for the session, so the app pins each pane's
        lap via set_pane_gmeter_lap once on enter/repoint rather than per tick."""
        self.pane.set_gmeter_lap(lap_id)

    def set_pane_gmeter_lap(self, side: int, lap_id):
        pane = self._pane_for(side)
        if pane is not None:
            pane.set_gmeter_lap(lap_id)

    def set_pane_badge(self, side: int, text: str, colour: str | None):
        """Set a pane's "Δ vs other" badge (compare mode, app-driven per tick)."""
        cell = self._cell_for(side)
        if cell is not None:
            cell.set_badge(text, colour)

    # ------------------------------------------------------------- pane <-> shell wiring
    def _on_pane_position(self, global_s: float):
        """The PRIMARY pane advanced (global seconds): track the slider and forward the position to
        the app for the telemetry sync. ONLY the primary pane is connected here — the secondary's
        positionChanged is never wired, so it can never drive the map/cursor/readout."""
        self.slider.blockSignals(True)
        self.slider.setValue(int(global_s * 1000))
        self.slider.blockSignals(False)
        self.positionChanged.emit(global_s)

    def _set_slider_window(self, window: tuple[float, float]):
        """Confine the global-ms scrub slider to [start, end] (compare lap A) and re-pin the current
        value. An inverted/empty window collapses to a point so the slider stays valid."""
        self._lap_window = (float(window[0]), float(window[1]))
        lo = int(window[0] * 1000)
        hi = max(int(window[1] * 1000), lo)
        self.slider.blockSignals(True)
        self.slider.setRange(lo, hi)
        self.slider.setValue(min(max(self.slider.value(), lo), hi))
        self.slider.blockSignals(False)
        # ...and say so: this bar now spans ONE LAP, not the session (see set_span_note).
        self.slider.set_span_note("this bar spans the compared lap, not the whole session")

    def set_compare_seek_fanout(self, fn) -> None:
        """Inject the compare-mode fan-out hook: called from _on_slider_moved with the primary's new
        global time so the seek is distance-locked to pane B. None disables it (single-video mode)."""
        self._compare_seek_fanout = fn

    def _on_slider_moved(self, ms: int):
        # The slider value is GLOBAL ms — route it through the PRIMARY pane's chapter-aware seek.
        # In compare mode clamp to lap A's window so a drag can't escape the lap or step the primary
        # past it.
        if self._lap_window is not None:
            lo, hi = self._lap_window
            ms = min(max(ms, int(lo * 1000)), int(hi * 1000))
        t = ms / 1000.0
        self.seek(t)  # PRIMARY pane
        # fan the same move out to pane B (distance-locked); only in compare mode, after the primary seek.
        if self.secondary is not None and self._compare_seek_fanout is not None:
            self._compare_seek_fanout(t)

    def _on_slider_action(self, _action: int):
        """Route a groove click/wheel (actionTriggered, every action — never reaches sliderMoved)
        through the same clamped seek as a drag. No double-seek: a handle drag emits only
        sliderMoved, never triggerAction."""
        self._on_slider_moved(self.slider.sliderPosition())

    def _on_duration(self, ms: int):
        """A per-chapter real video-track duration arrives as each source loads (durationChanged,
        ms). Record it and keep the slider spanning the whole session (see _whole_session_max_ms);
        in compare mode the range is pinned to lap A's window, so don't widen it."""
        if self._lap_window is not None:
            return  # compare mode: the range is pinned to lap A's window (see _set_slider_window)
        # Record the real video duration for whichever chapter just loaded (current source).
        if ms > 0:
            self._chapter_video_ms[self.pane.current_chapter()] = ms
        if self.pane.total_duration <= 0:
            # Lone file with no known GPMF duration: the observed video duration is the whole span.
            self.slider.setMaximum(ms)
            return
        self.slider.setMaximum(self._whole_session_max_ms())

    def _whole_session_max_ms(self) -> int:
        """Whole-session slider max (ms) = max(GPMF metadata total, observed video total). Observed
        sums each chapter's real video duration, falling back to its GPMF duration when not yet
        loaded. The max means the handle spans the whole playable video even when the telemetry track
        is shorter than the video (the early-pin case), without regressing a longer telemetry track."""
        gpmf_total_ms = int(self.pane.total_duration * 1000)
        n = max(self.pane.chapter_count(), 1)
        observed_total_ms = sum(
            self._chapter_video_ms.get(i, int(self.pane.chapter_duration(i) * 1000))
            for i in range(n))
        return max(gpmf_total_ms, observed_total_ms)

    def _on_state(self, _state):
        # Glyph follows EITHER pane (they auto-pause at different lap ends, so following only the
        # primary would lie). _state is ignored — recompute from both.
        playing = self.pane.is_playing() or (
            self.secondary is not None and self.secondary.is_playing())
        self.play_btn.setIcon(theme.icon("ph.pause-fill" if playing else "ph.play-fill"))
