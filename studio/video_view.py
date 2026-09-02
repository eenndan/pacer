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

Transport layout: the scrub bar has its own full-width row under the video (a media-player
transport, not a control squeezed in beside five buttons) and the buttons sit under it. Every
piece of chrome here is width-budgeted rather than assumed to fit — see `_LapRulerSlider.tick_plan`
(the ruler decimates to the pixels it has) and `_PaneCell._fit_strip` (the compare strip picks a
form that fits instead of letting Qt overlap the boxes).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
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

# Phosphor (qtawesome `ph` prefix) glyphs for the transport bar, themed via theme.icon.
_ICON_PX = 18                       # glyph render size inside the buttons
_ICON_BTN = QSize(32, 30)           # compact square-ish icon button

# 0 = primary (left, drives telemetry); 1 = secondary (right, video-only). Used by the lap-picker
# repoint signal so app knows which side to repoint.
PRIMARY, SECONDARY = 0, 1

# Horizontal inset (px) inside each compare cell so the native QVideoWidget surface (which on macOS
# composites above sibling chrome) doesn't swallow the splitter handle's mouse events.
_PANE_INSET = 5

# Width budget (px) for each pane's lap picker — the SOLE home of the lap text. The floor is the
# picker's OWN content width (set_lap_choices measures it), never a magic number, so the lap TIME
# can't be elided away; _PICKER_MAX_W caps a pathological label so one picker can't pin the strip.
_PICKER_MIN_W = 150
_PICKER_MAX_W = 260
# Gap (px) between the compare strip's children, on both of its rows.
_STRIP_SPACING = 6

# The scrub bar is the video panel's primary hit target and the lap ruler's canvas: it gets its own
# full-width row under the video, a >=24px handle and a >=26px widget (the 24px hit floor), which
# also buys the ruler the travel it needs to stay readable on a 65-lap session.
_SLIDER_H = 26
_SLIDER_HANDLE = 24
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
        # The decimation step (and so the tooltip) is a function of the range and the width; both
        # change under the ruler as chapters load and the panel is resized.
        self.rangeChanged.connect(lambda *_: self._refresh_tooltip())
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


class _PaneCell(QWidget):
    """Compare-pane chrome: a strip (fixed role caption · lap picker · Δ badge) above the PlayerPane.
    Owns no playback state. The lap identity lives ONLY in the picker; the caption is a fixed role
    word, the badge yields width first. Selecting a lap emits `repointRequested(lap_id)`.

    THE STRIP IS BUDGETED, NOT WISHED FOR (`_fit_strip`). Three width-inflexible children in one
    QHBoxLayout demanded 316 px inside the 243 px a pane gets at the app's own default window size,
    and Qt resolves that shortfall by OVERLAPPING the boxes — the Δ badge painted on top of the lap
    time, unrecoverably. So the strip is a QGridLayout that measures what it has and picks a form
    that fits: one row while all three fit, otherwise the picker drops to a full-width second row
    (role + Δ above it), and if even that is too narrow the role word goes to its short form. The
    lap time is the one thing that never yields."""

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

        # fixed role word; Fixed size so the picker (not it) grows
        self.caption = QLabel(self._role_full)
        self.caption.setObjectName("PaneCaption")
        self.caption.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.caption.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self.caption.setToolTip(self._role_full)

        # sole home of lap identity; the width floor is re-derived from its own content in
        # set_lap_choices so the lap TIME can never be elided out of it.
        self.picker = QComboBox()
        self.picker.setToolTip("Pick the lap shown in this pane")
        self.picker.setMinimumWidth(_PICKER_MIN_W)
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

        # Columns: 0 caption · 1 picker · 2 elastic gap · 3 badge (pinned right). Which cells are
        # occupied is _fit_strip's call; the gap column carries the stretch in both forms.
        self._strip = QGridLayout()
        self._strip.setContentsMargins(0, 0, 0, 0)
        self._strip.setHorizontalSpacing(_STRIP_SPACING)
        self._strip.setVerticalSpacing(2)
        self._strip.setColumnStretch(2, 1)
        self._two_row: bool | None = None
        self._apply_strip_rows(False)

        lay = QVBoxLayout(self)
        # horizontal inset so the native video surface doesn't swallow the splitter handle (see _PANE_INSET).
        lay.setContentsMargins(_PANE_INSET, 0, _PANE_INSET, 0)
        lay.setSpacing(0)
        lay.addLayout(self._strip)
        lay.addWidget(self.pane, 1)

    # ------------------------------------------------------------------ the strip's width budget
    def _apply_strip_rows(self, two_row: bool) -> None:
        """Mount the strip's three children in the one-row or two-row form. Idempotent (a no-op when
        the form is already the live one), so a resize storm re-parents nothing."""
        if two_row == self._two_row:
            return
        self._two_row = two_row
        for wdg in (self.caption, self.picker, self.badge):
            self._strip.removeWidget(wdg)
        self._strip.addWidget(self.caption, 0, 0)
        self._strip.addWidget(self.badge, 0, 3)
        if two_row:
            self._strip.addWidget(self.picker, 1, 0, 1, 4)   # full width on its own row
        else:
            self._strip.addWidget(self.picker, 0, 1)

    def _caption_w(self, text: str) -> int:
        """What the caption label would be WIDE if it carried `text` (its QSS padding included)."""
        fm = self.caption.fontMetrics()
        pad = self.caption.sizeHint().width() - fm.horizontalAdvance(self.caption.text())
        return fm.horizontalAdvance(text) + max(pad, 0)

    def _fit_strip(self) -> None:
        """Pick the strip form that FITS the width this cell actually has — the fix for the badge
        painting over the lap time. Depends only on the cell width and font metrics (never on the
        children's current geometry), so it converges in one pass and cannot oscillate.

        The ladder, in the order things yield: one row → the picker takes a full-width second row →
        the role word goes short → the role word goes (it is still in the tooltip, and pane A is
        always the left one). The lap time and the Δ never yield."""
        avail = self.width() - 2 * _PANE_INSET
        if avail <= 0:
            return
        gaps = 3 * _STRIP_SPACING          # the grid keeps its column spacing even where a cell is empty
        full = self._caption_w(self._role_full)
        short = self._caption_w(self._role_short)
        badge = self.badge.sizeHint().width()
        picker = self.picker.minimumWidth()
        one_row = avail >= full + picker + badge + gaps
        if one_row or avail >= full + badge + gaps:
            role, shown = self._role_full, True
        elif avail >= short + badge + gaps:
            role, shown = self._role_short, True
        else:
            role, shown = self._role_short, False   # too narrow even for "REF" beside the Δ
        if self.caption.text() != role:
            self.caption.setText(role)
        if self.caption.isVisibleTo(self) != shown:
            self.caption.setVisible(shown)
        self._apply_strip_rows(not one_row)

    def resizeEvent(self, ev):
        super().resizeEvent(ev)
        self._fit_strip()

    def showEvent(self, ev):
        super().showEvent(ev)
        self._fit_strip()   # first real width + a polished (QSS padding applied) caption

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
            # Re-derive the width floor from the content itself: AdjustToContents sizes the hint to
            # the WIDEST item (frame + arrow included), so this is exactly the width at which the
            # lap time stops being elided. Capped, so one long label can't pin the whole strip.
            self.picker.setMinimumWidth(
                max(_PICKER_MIN_W, min(self.picker.sizeHint().width(), _PICKER_MAX_W)))
        if current in self._lap_ids:
            idx = self._lap_ids.index(current)
            if self.picker.currentIndex() != idx:
                self.picker.setCurrentIndex(idx)
        self.picker.blockSignals(False)
        self._fit_strip()

    def set_caption(self, text: str):
        """Compat shim: the app passes rich "lap N · time" text; show it as the role caption's
        TOOLTIP (the label stays the fixed role word — identity lives in the picker). The tooltip
        also carries the FULL role word, which the label itself drops at narrow widths."""
        self.caption.setToolTip(f"{self._role_full} — {text}" if text else self._role_full)

    def set_badge(self, text: str, colour: str | None):
        """Set the Δ badge text/colour (app-driven per tick), guarded: re-apply only on an actual
        change so a stable compare view does zero per-tick label work (setText relayout / QSS
        re-parse). A changed Δ can change the badge's width, so re-fit the strip with it."""
        if text != self._badge_text:
            self._badge_text = text
            self.badge.setText(text)
            self._fit_strip()
        if colour != self._badge_colour:
            self._badge_colour = colour
            if colour is None:
                self.badge.setStyleSheet("")
            else:
                self.badge.setStyleSheet(f"QLabel#PaneBadge {{ color: {colour}; }}")

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
        self.play_btn = QPushButton()
        self.play_btn.setIcon(theme.icon("ph.play-fill"))
        self.play_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.play_btn.setFixedSize(_ICON_BTN)
        self.play_btn.setToolTip("Play / pause (Space)")
        self.play_btn.clicked.connect(self.toggle)

        # mute/unmute toggle. speaker-x while muted (default), speaker-high while audible.
        self.mute_btn = QPushButton()
        self.mute_btn.setIcon(theme.icon("ph.speaker-simple-x"))
        self.mute_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.mute_btn.setFixedSize(_ICON_BTN)
        self.mute_btn.setToolTip("Audio muted — click to unmute (M)")
        self.mute_btn.clicked.connect(self.toggle_mute)

        # g-meter show/hide toggle. Checkable: QSS :checked tints the button; the glyph also goes accent.
        self.gmeter_btn = QPushButton()
        self.gmeter_btn.setIcon(theme.icon("ph.gauge"))
        self.gmeter_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.gmeter_btn.setFixedSize(_ICON_BTN)
        self.gmeter_btn.setCheckable(True)
        self.gmeter_btn.setToolTip("Show/hide the g-meter overlay (G)")
        self.gmeter_btn.toggled.connect(self._on_gmeter_toggled)
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
        self.compare_btn = QPushButton("Compare")
        self.compare_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.compare_btn.setFixedHeight(_ICON_BTN.height())
        self.compare_btn.setCheckable(True)
        self.compare_btn.setEnabled(False)
        self.compare_btn.toggled.connect(self._set_compare_btn_appearance)  # glyph follows checked
        self.compare_btn.toggled.connect(self.compareToggled)               # emit the user intent
        self._set_compare_btn_appearance(False)

        # "Fullscreen video" toggle (⤢): make the video fill the whole screen, like a normal player.
        # A pure INPUT (mirrors compare_btn): a click just emits videoFocusRequested; CentralView owns
        # the enter/exit and reflects the resulting on/off appearance back via set_video_focus_visual.
        # It is DISABLED while the compare stage is mounted, because CentralView refuses the gesture
        # there: a checkable button whose click is silently refused latches into a checked state it
        # did not earn (indistinguishable from a genuinely-on toggle).
        self.fullscreen_btn = QPushButton()
        self.fullscreen_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.fullscreen_btn.setFixedSize(_ICON_BTN)
        self.fullscreen_btn.setCheckable(True)
        self.fullscreen_btn.clicked.connect(self.videoFocusRequested)  # a genuine click = the intent
        self._set_fullscreen_btn_appearance(False)
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
        # The panel's primary hit target: a 24px handle in a 26px widget clears the 24px floor, and
        # the widget-level rule only overrides the handle's box (the themed groove/colours cascade).
        self.slider.setMinimumHeight(_SLIDER_H)
        self.slider.setStyleSheet(
            f"QSlider::handle:horizontal {{ width: {_SLIDER_HANDLE}px;"
            f" height: {_SLIDER_HANDLE}px; margin: -{(_SLIDER_HANDLE - 8) // 2}px 0;"
            f" border-radius: {_SLIDER_HANDLE // 2}px; }}")
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

        # The scrub bar gets its OWN full-width row under the video, the way every media player lays
        # a transport out. Sharing one row with the buttons cost it ~200px of travel (16.4 s per
        # pixel on a 65-lap session, an unreadable lap ruler) and left no width for a text label on
        # the compare button.
        row = QHBoxLayout()
        row.addWidget(self.play_btn)
        row.addWidget(self.mute_btn)
        row.addWidget(self.gmeter_btn)
        row.addWidget(self.compare_btn)
        row.addWidget(self.fullscreen_btn)
        row.addStretch(1)

        self.readout = QLabel("")  # F2: time / speed / current lap, driven by app
        self.readout.setObjectName("Readout")  # caption style, dimmed, tabular (global QSS)
        self.readout.setAlignment(Qt.AlignCenter)

        # The STAGE holds the video surface(s): one pane normally, a 2-pane splitter in compare
        # mode. Its layout is rebuilt on enter/exit compare; everything else (transport, readout)
        # is untouched. In single mode the primary pane sits directly in the stage layout.
        self._stage = QWidget()
        self._stage_lay = QVBoxLayout(self._stage)
        self._stage_lay.setContentsMargins(0, 0, 0, 0)
        self._stage_lay.addWidget(self.pane, 1)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(self._stage, 1)
        lay.addWidget(self.slider)
        lay.addLayout(row)
        lay.addWidget(self.readout)

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

    def _set_compare_btn_appearance(self, on: bool):
        """Drive the compare toggle's OFF/ON appearance (glyph accent + tooltip) to track its checked
        state. Wired to the button's `toggled` and re-applied by _set_compare_visual; never per tick."""
        self.compare_btn.setIcon(theme.icon("ph.columns", color=theme.C.accent if on else None))
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
        self._set_compare_btn_appearance(on)

    # ------------------------------------------------------------- fullscreen-video toggle
    def _set_fullscreen_btn_appearance(self, on: bool):
        """Drive the ⤢ button's OFF/ON glyph + tooltip to track video-focus state. `ph.arrows-in`
        (contract) reads as "exit fullscreen" while on; `ph.arrows-out` (expand) as "enter". While
        the gesture is unavailable (compare mode) the tooltip says WHY, in the same shape as the
        compare button's own "— needs ≥2 valid laps"."""
        self.fullscreen_btn.setIcon(theme.icon(
            "ph.arrows-in" if on else "ph.arrows-out",
            color=theme.C.accent if on else None))
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
        self._set_fullscreen_btn_appearance(self.fullscreen_btn.isChecked())

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
        self._set_fullscreen_btn_appearance(on)

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
            # Real drag handle: 8px, no collapse, opaque resize. Ignored/Expanding cells stop the
            # QVideoWidget aspect hint pinning the split; 1:1 stretch keeps 50/50.
            self._splitter.setHandleWidth(8)
            self._splitter.setChildrenCollapsible(False)
            self._splitter.setOpaqueResize(True)
            for cell in (self._cell_a, self._cell_b):
                cell.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            self._splitter.setStretchFactor(0, 1)
            self._splitter.setStretchFactor(1, 1)
            self._equalize_panes()
            # also re-pin overlays on handle drag (belt-and-braces; the native surface may not emit a Move).
            self._splitter.splitterMoved.connect(self._on_splitter_moved)

        # Swap the stage layout to the splitter (the primary pane re-parents into _cell_a). Guarded on
        # the DERIVED mounted state so a re-seed (already two-pane) doesn't re-swap.
        if not self._panes_mounted():
            self._stage_lay.removeWidget(self.pane)
            self._stage_lay.addWidget(self._splitter, 1)
            self.secondary.show()
            self._splitter.show()
        # equalize now and again next event-loop turn (setSizes needs a real width — see _equalize_panes)
        self._equalize_panes()
        QTimer.singleShot(0, self._equalize_panes)
        # Reflect the controller's compare-ON state onto the button (no re-emit; see _set_compare_visual).
        self._set_compare_visual(True)

        # Seed each pane's window + caption + picker from its spec (the app seeks the panes to their starts).
        self.pane.set_lap_window(*pane_a.window)
        self.secondary.set_lap_window(*pane_b.window)
        self._cell_a.set_caption(pane_a.caption)
        self._cell_b.set_caption(pane_b.caption)
        self._cell_a.set_lap_choices(pane_a.choices, pane_a.lap_id, pane_a.choice_labels)
        self._cell_b.set_lap_choices(pane_b.choices, pane_b.lap_id, pane_b.choice_labels)
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
        self._apply_lap_ticks()  # whole-session range again -> restore the lap ruler
        self._teardown_secondary()
        # Drop the cell wrappers + splitter (the primary pane has been reparented out of _cell_a).
        for w in (self._cell_a, self._cell_b, self._splitter):
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._cell_a = self._cell_b = self._splitter = None
        self._sync_fullscreen_enabled()  # single video again -> the ⤢ gesture is back

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

    def _equalize_panes(self):
        """Split the two panes 50/50 from the splitter's live width (falls back to a [1000,1000]
        ratio before any width is known)."""
        if self._splitter is None or self._splitter.count() < 2:
            return
        w = self._splitter.width()
        if w > 0:
            handle = self._splitter.handleWidth()
            half = max((w - handle) // 2, 1)
            self._splitter.setSizes([half, w - handle - half])
        else:
            self._splitter.setSizes([1000, 1000])

    def _on_splitter_moved(self, _pos: int, _index: int):
        """Re-pin BOTH g-meter overlays after a splitter-handle drag (each pane re-pins its own
        overlay to its video corner; cheap no-op when an overlay is hidden)."""
        for pane in self._panes():
            pane.sync_gmeter()

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
    def _on_gmeter_toggled(self, on: bool):
        """Recolour the g-meter glyph to the accent when the overlay is active (the QSS already
        tints the button background on :checked)."""
        self.gmeter_btn.setIcon(theme.icon("ph.gauge", color=theme.C.accent if on
                                           else theme.C.text))

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
