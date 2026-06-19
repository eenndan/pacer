"""VideoView: the player SHELL — transport chrome around ONE PlayerPane, or (compare mode) TWO.

The single-lap player stack (QMediaPlayer + QVideoWidget + QAudioOutput, the ChapterMap-based
source-switching seek, the deferred cross-chapter seek, the EndOfMedia auto-advance, and the
g-meter overlay) lives in `player_pane.PlayerPane`. VideoView keeps the transport row
(play/pause/mute/g-meter/compare icon buttons + a GLOBAL-time scrub slider + the #Readout label)
and a STAGE area that holds either ONE pane (normal) or TWO equal panes side-by-side in a
horizontal QSplitter (compare mode). It re-exposes the SAME public API the app already drives
(`seek`, `play`, `pause`, `is_playing`, `current_chapter`, `is_multi`, `set_g`, `set_readout`,
`set_gmeter_source`, `set_gmeter_lap`, the `gmeter_btn`, `positionChanged`/`chapterChanged`,
`stop_all`).

COMPARE MODE (Phase B — purely additive behind the explicit toggle)
-------------------------------------------------------------------
A checkable "Compare videos" toggle (off by default; enabled only when ≥2 valid laps) shows two
EQUAL video panes. The PRIMARY (left) pane is the existing `self.pane` and drives ALL telemetry
exactly as today — its `positionChanged` still feeds the app. The SECONDARY (right) pane is
created LAZILY on toggle-on (its own source = the session ChapterMap), is VIDEO-ONLY (its
`positionChanged` is NOT forwarded to the app), is always muted, and is torn down (stop +
deleteLater the player+audio, .close() its g-meter overlay) on toggle-off and on any reload, so
no decoder/overlay leaks. Each pane shows a fixed ROLE caption ("THIS LAP" / "REFERENCE"), a
compact lap picker that carries the lap IDENTITY ("lap N (m:ss.mmm)", ★ on the best lap) and
repoints that side (emits `paneRepointRequested`), and a "Δ vs other" badge the app updates. The
caption stays the role only — the lap+time lives once, in the picker, so the identifying text is
never split/duplicated and never cramped out of the narrow strip. Play/pause/mute fan out to
BOTH panes; the g-meter toggle applies per-pane (both default off).

EVERY change to the compared pair (enter, either picker repoint) re-seeks BOTH panes to their
lap's start line and PAUSES both, so the two videos are always realigned at S/F and ready to roll
together on the next Play — the app owns this reset (see _reset_pair_to_start).

A recording can be a single file OR a chaptered multi-file recording. The slider + the emitted
position are in GLOBAL session time (0..sum-of-durations); the pane maps global<->chapter time and
switches sources / auto-advances across chapters under the hood. The primary pane drives the
slider/readout; in compare mode the slider spans each lap's window via the primary pane's clamp.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (
    QComboBox,
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

# Horizontal inset (px) inside each compare cell so the native QVideoWidget surface doesn't cover
# the splitter handle strip (the native surface composites above sibling chrome on macOS, so a flush
# video would swallow the handle's mouse events and the split couldn't be dragged).
_PANE_INSET = 5

# Floor width (px) for each pane's lap picker so the lap IDENTITY it carries ("lap N (m:ss.mmm)",
# plus a "  ★" on the best lap) is fully legible even at the narrow default compare split — the
# picker is the single home of the lap text now, so it must never clip. AdjustToContents lets it
# grow past this when the current item is wider; this is just the no-clip minimum.
_PICKER_MIN_W = 150


@dataclass
class PaneSpec:
    """F8b: everything ONE compare pane needs, bundled per side — replaces the ~11 per-side
    positional params `set_compare` used to spread across the two panes (lap_a/lap_b, window_a/
    window_b, caption_a/caption_b, lap_choices/pane_b_choices, lap_choice_labels/
    pane_b_choice_labels, pane_b_source). With a spec per side `set_compare(pane_a, pane_b)`
    treats both panes symmetrically and the cross-vs-same difference becomes simply how pane B's
    spec was built (its `source`/`choices`/`choice_labels`).

    Fields:
      * lap_id        — the lap this pane shows (selects the picker entry, never emitting a repoint).
      * window        — (start_global, end_global) lap window on this pane's OWN clock; the pane is
                        confined to it and pane A's window also confines the global scrub slider.
      * caption       — the app's rich "lap N · m:ss.mmm" (or cross "<rec> · lap N · time") text;
                        surfaced as the pane caption's TOOLTIP (the fixed ROLE word stays the label).
      * source        — this pane's media source (ChapterMap/path). None reuses the PRIMARY
                        recording's source (`self._source`); an explicit source plays a DIFFERENT
                        recording (F7 Phase B cross-recording compare). The PRIMARY pane is never
                        rebuilt, so pane A's source is conventionally None (it always = self._source).
      * choices       — the lap ids the pane's picker lists (cross-recording locks pane B to the
                        single reference lap; same-recording both panes list the session's valid laps).
      * choice_labels — parallel display labels for `choices` (None falls back to "lap {id}").
    """
    lap_id: int
    window: tuple[float, float]
    caption: str
    source: object = None
    choices: list[int] = field(default_factory=list)
    choice_labels: list[str] | None = None


class _LapRulerSlider(QSlider):
    """The transport scrub slider, drawn with subtle LAP-BOUNDARY tick marks over the groove so it
    doubles as a MoTeC-style lap ruler — at a glance you can see where each lap starts/ends along
    the session and scrub straight to one. Behaviour is a plain horizontal QSlider (the app's seek
    wiring is unchanged); only the painting is extended.

    The ticks are GLOBAL-time boundaries in the slider's own value units (ms): the app feeds the
    sorted, de-duplicated lap start/end positions via `set_lap_ticks` (derived from the session lap
    windows mapped onto the slider range). We paint AFTER the base groove/handle so the marks sit on
    top of the groove but the handle still reads clearly over them. Each boundary value is mapped to
    an x pixel via the style's groove rect + sliderPositionFromValue (the same geometry the handle
    uses), so the ticks line up exactly with where the handle lands at that time."""

    # Tick visuals: a thin near-full-height mark inside the groove band, dim so it reads as a ruler
    # cue and never competes with the amber fill / handle. Drawn at ~55 % alpha of the dim text.
    _TICK_H = 10   # px tall (centred on the groove), a touch taller than the 8px groove so it reads

    def __init__(self, orientation):
        super().__init__(orientation)
        self._lap_ticks: list[int] = []  # boundary values in slider units (ms), sorted/unique

    def set_lap_ticks(self, values: list[int]) -> None:
        """Set the lap-boundary tick positions (slider-value units, i.e. global ms). Values outside
        the current range are tolerated (clamped at paint); a repaint is requested so the ruler
        updates immediately on (re)load / compare enter/exit. Empty clears the ruler."""
        self._lap_ticks = sorted({int(v) for v in values})
        self.update()

    def _groove_rect(self):
        """The style's groove rect for this slider — the band the handle travels in. Used to map a
        boundary value to an x pixel exactly as the handle is placed, so ticks and handle agree."""
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        return self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderGroove, self)

    def paintEvent(self, ev):
        super().paintEvent(ev)  # base groove + sub/add-page fill + handle (themed QSS) first
        lo, hi = self.minimum(), self.maximum()
        if not self._lap_ticks or hi <= lo:
            return
        groove = self._groove_rect()
        # Span the handle CAN travel: the groove minus the handle's own length, so the mapped x for
        # a value matches where the handle's centre lands (sliderPositionFromValue uses this span).
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        x0 = groove.x() + handle.width() // 2
        cy = groove.center().y()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)  # crisp 1px ruler marks
        pen = QPen(QColor(theme.C.text_dim))
        pen.setWidth(1)
        painter.setPen(pen)
        painter.setOpacity(0.55)
        seen = set()
        for v in self._lap_ticks:
            cv = min(max(v, lo), hi)
            x = x0 + QStyle.sliderPositionFromValue(lo, hi, cv, span, opt.upsideDown)
            if x in seen:        # collapse boundaries that map to the same pixel (back-to-back laps)
                continue
            seen.add(x)
            painter.drawLine(x, cy - self._TICK_H // 2, x, cy + self._TICK_H // 2)
        painter.end()


class _PaneCell(QWidget):
    """A compare-mode pane wrapper: a caption strip (ROLE caption · lap picker · Δ badge) above the
    PlayerPane's video. Pure chrome — it owns no playback state; the PlayerPane it wraps does.

    The strip is laid out so the lap IDENTITY is never cramped: the caption is a SHORT fixed role
    word ("THIS LAP" / "REFERENCE"), the LAP picker (which already lists "lap N (m:ss.mmm)") gets a
    floor width and grows to fit its current text, and the Δ badge — the most disposable item — is
    the one that yields width first. The lap+time therefore lives in exactly ONE place (the picker),
    so the old strip (caption "lap N · m:ss.mmm" + a picker that ALSO showed lap+time) no longer
    duplicates the identity nor truncates either copy in the narrow default split.

    The lap picker is a compact combo of valid laps; selecting one emits `repointRequested(lap_id)`
    which VideoView forwards to the app, tagged with this cell's side index."""

    repointRequested = Signal(int)  # the newly-picked lap id for this side

    def __init__(self, pane: PlayerPane, side: int):
        super().__init__()
        self.pane = pane
        self.side = side
        self._lap_ids: list[int] = []
        self._labels: list[str] = []   # last-applied picker item labels (guards the repopulate)

        # ROLE caption only — a short fixed word per side. The lap+time identity used to live here
        # too ("lap N · m:ss.mmm"), duplicating the picker and crowding the narrow strip until both
        # copies truncated; now the identity lives ONLY in the picker, so the caption just names the
        # pane's role and never competes for width (set_caption ignores the app's lap-text on purpose).
        self.caption = QLabel("THIS LAP" if side == PRIMARY else "REFERENCE")
        self.caption.setObjectName("PaneCaption")
        self.caption.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        # The fixed role word never needs to shrink — pin it to its own size so the picker (not the
        # caption) is what grows into the spare width, and so a long badge can't squeeze the role.
        self.caption.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        # Compact lap picker: lists valid laps; repoints this side without touching the other. It is
        # the SOLE home of the lap identity ("lap N (m:ss.mmm)"), so it must never clip its current
        # text: a floor width keeps it legible at the default split, and AdjustToContents lets it
        # grow to its current item so the selected "lap N (m:ss.mmm)" is shown in full, not elided.
        self.picker = QComboBox()
        self.picker.setToolTip("Pick the lap shown in this pane")
        self.picker.setMinimumWidth(_PICKER_MIN_W)
        self.picker.setSizeAdjustPolicy(QComboBox.AdjustToContents)
        self.picker.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.picker.currentIndexChanged.connect(self._on_pick)

        # "Δ vs other" badge — app drives the text + colour per tick (transparent inline label). It
        # is the FIRST thing to yield width (the identifying caption/picker outrank it), so it can't
        # push the lap text out of the strip; its short "Δ +0.00 s" form fits comfortably regardless.
        self.badge = QLabel("Δ —")
        self.badge.setObjectName("PaneBadge")
        self.badge.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self.badge.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)
        self._badge_colour: str | None = None
        self._badge_text = "Δ —"   # last-applied badge text (guards the per-tick setText)

        # Order: role caption, lap picker (grows), a flexible gap, then the Δ badge pinned right.
        # The stretch sits between the identity (caption+picker) and the badge so any spare width is
        # absorbed by the GAP — the picker keeps its content size and the badge keeps its own — and
        # when width is tight the gap collapses first, before any identifying text would clip.
        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        strip.addWidget(self.caption)
        strip.addWidget(self.picker)
        strip.addStretch(1)
        strip.addWidget(self.badge)

        lay = QVBoxLayout(self)
        # A small horizontal inset so the pane's NATIVE QVideoWidget surface doesn't butt right up
        # to the splitter handle: on macOS the native video surface composites ABOVE sibling chrome,
        # so a flush video would cover the thin handle strip and swallow its mouse events (the handle
        # then won't drag). The inset keeps a clear strip beside the handle for it to receive drags.
        lay.setContentsMargins(_PANE_INSET, 0, _PANE_INSET, 0)
        lay.setSpacing(0)
        lay.addLayout(strip)
        lay.addWidget(self.pane, 1)

    def set_lap_choices(self, lap_ids: list[int], current: int,
                        labels: list[str] | None = None):
        """(Re)populate the picker with `lap_ids` (shown with `labels` if given — the app builds
        them with the lap time + a ★ on the best lap, e.g. "lap 25  (1:08.325)") and select
        `current` WITHOUT emitting a repoint (a programmatic re-seed must not look like a user pick).

        Skips the (expensive) clear+repopulate when the ids+labels are UNCHANGED: only the current
        selection is re-pinned. A repoint re-seeds both panes' pickers each time, so guarding the
        rebuild avoids a per-repoint QComboBox churn when the lap set hasn't actually changed."""
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

    def set_caption(self, text: str):
        """Compat shim: the app still hands a "lap N · m:ss.mmm" string here (and for cross-recording
        compare, "<recording> · lap N · time"), but the caption is now a fixed ROLE word and the lap
        identity lives solely in the picker — so we DON'T paint that text into the strip (it would
        re-duplicate the picker and re-crowd it). We surface the app's richer text as the caption's
        TOOLTIP instead, so hovering the role still reveals the full lap/recording detail. The
        wiring stays intact; this is a presentation choice, keeping the strip to role + picker + Δ."""
        self.caption.setToolTip(text)

    def set_badge(self, text: str, colour: str | None):
        """Set the "Δ vs other" badge text + (only when it changes) its colour — driven per tick by
        the app. BOTH are guarded against a no-op re-apply: the colour re-apply (a QSS re-parse +
        relayout) only fires when the colour FLIPS, and the text setText (which relays out the
        label) only fires when the text actually changes — so a PAUSED / stable compare view does
        zero label work per tick (the addressable interaction lag), mirroring #DiffBox's guard."""
        if text != self._badge_text:
            self._badge_text = text
            self.badge.setText(text)
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
    compareToggled = Signal(bool)    # the "Compare videos" toggle flipped (app seeds/tears down)
    # A pane's lap picker was used: (side, lap_id) — app repoints that side (lap+window+caption+
    # chart overlay + badge). side is PRIMARY (0) or SECONDARY (1).
    paneRepointRequested = Signal(int, int)

    def __init__(self, source: str | chapters.ChapterMap | None):
        super().__init__()
        # Remember the source so the lazy secondary pane can open the SAME ChapterMap.
        self._source = source
        # The single PRIMARY PlayerPane owns the whole decode/overlay stack; the shell drives it.
        # It is ALWAYS the telemetry driver (its positionChanged feeds the app).
        self.pane = PlayerPane(source)
        self.pane.positionChanged.connect(self._on_pane_position)
        self.pane.chapterChanged.connect(self.chapterChanged)
        self.pane.playbackStateChanged.connect(self._on_state)
        # Forward the PRIMARY pane's seam-reopen hint so the app can show a brief "loading next
        # chapter…" indicator. Only the primary drives chrome (the secondary is video-only).
        self.pane.seamLoading.connect(self.seamLoading)

        # Compare-mode LAYOUT flag: True iff the two-pane (side-by-side) stage is mounted. This is
        # the view's own layout state; the StudioWindow's semantic compare ownership lives in
        # StudioWindow._compare (distinct concept, distinct name). The secondary pane + cells exist
        # ONLY while this is on (lazy).
        self._two_panes = False
        # The PRIMARY pane's lap window (start_global, end_global) while in compare mode, or None
        # in single-video mode. The global scrub slider is CONFINED to this window in compare mode
        # so dragging it can't escape lap A or step the primary past the lap. NOTE: the window alone
        # does NOT keep the pair in sync — the slider/arrows seek pane A only, so pane B would freeze
        # while A jumps unless the seek is fanned out. That fan-out is `_compare_seek_fanout`, set by
        # the app to distance-lock the same move to pane B (see _on_slider_moved). None window =>
        # the slider spans the whole session, exactly as before.
        self._lap_window: tuple[float, float] | None = None
        # D1: in compare mode the global slider + ←/→ arrows (both routed through _on_slider_moved)
        # seek the PRIMARY pane only. This hook, injected by the app (set_compare_seek_fanout), is
        # called with the primary's new global time so the app can distance-lock the SAME move to
        # pane B (CompareController.fanout_seek_b) — without it the two videos desync. None outside
        # compare / before wiring; cleared on exit_compare.
        self._compare_seek_fanout: object = None
        # D6: the REAL per-chapter video-track durations (ms) QMediaPlayer reports via
        # durationChanged, keyed by chapter index. The slider RANGE was sized off the GPMF
        # metadata-track total (pane.total_duration) alone, but on GoPro files the telemetry track
        # can end before/after the video track, so the handle pinned early / overshot while the time
        # readout was correct. We track the observed video durations here and range the slider to the
        # LARGER of the GPMF total and the observed video total (see _on_duration), so the handle
        # spans the whole playable video.
        self._chapter_video_ms: dict[int, int] = {}
        # Last g-meter source + visible state, captured so a LAZILY-created secondary pane can be
        # seeded with them on entry (toggling g-meter ON then entering compare must show the overlay
        # on BOTH panes with the right source). set by set_gmeter_source / set_gmeter_visible.
        self._gmeter_source: str | None = None
        self._gmeter_visible = False
        self.secondary: PlayerPane | None = None
        # The media source the live secondary pane was opened on (None until compare is entered).
        # Normally `self._source` (same recording); for the F7 Phase B cross-recording compare it
        # is the REFERENCE recording's ChapterMap. Tracked so a source change (same-recording ↔
        # cross-recording, or a primary reload) rebuilds the secondary on the right footage.
        self._secondary_source: object = None
        self._cell_a: _PaneCell | None = None   # primary cell wrapper (compare mode)
        self._cell_b: _PaneCell | None = None   # secondary cell wrapper (compare mode)
        self._splitter: QSplitter | None = None
        self._compare_enabled = False           # ≥2 valid laps (set by app via set_compare_enabled)
        # GLOBAL-time lap-boundary positions (seconds) for the slider's MoTeC-style lap ruler, fed by
        # the app (set_lap_ticks) and re-applied whenever the slider range changes (compare confines
        # it to one lap, so the ruler is cleared there and restored on exit). None until the app loads.
        self._lap_boundaries_s: list[float] = []

        # Compact Phosphor-icon transport buttons (no text). Icons are themed via theme.icon and
        # set ONCE per state change in the existing handlers — never on the playback tick.
        self.play_btn = QPushButton()
        self.play_btn.setIcon(theme.icon("ph.play-fill"))
        self.play_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.play_btn.setFixedSize(_ICON_BTN)
        self.play_btn.setToolTip("Play / pause (Space)")
        self.play_btn.clicked.connect(self.toggle)

        # F4: mute/unmute toggle. speaker-x while muted (default), speaker-high while audible.
        self.mute_btn = QPushButton()
        self.mute_btn.setIcon(theme.icon("ph.speaker-simple-x"))
        self.mute_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.mute_btn.setFixedSize(_ICON_BTN)
        self.mute_btn.setToolTip("Audio muted — click to unmute (M)")
        self.mute_btn.clicked.connect(self.toggle_mute)

        # g-meter show/hide toggle (the friction-circle overlay on the video). Checkable: the QSS
        # :checked rule tints the button accent; we also recolour the GLYPH to C.accent when on.
        self.gmeter_btn = QPushButton()
        self.gmeter_btn.setIcon(theme.icon("ph.gauge"))
        self.gmeter_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.gmeter_btn.setFixedSize(_ICON_BTN)
        self.gmeter_btn.setCheckable(True)
        self.gmeter_btn.setToolTip("Show/hide the g-meter overlay (G)")
        self.gmeter_btn.toggled.connect(self._on_gmeter_toggled)
        self.gmeter_btn.toggled.connect(self.set_gmeter_visible)

        # "Compare videos" toggle (Phase B): an ICON-ONLY checkable button in the SAME transport
        # vocabulary as play/mute/g-meter (one compact 32×30 square, a Phosphor glyph, meaning in the
        # tooltip) — no wide text button breaking the row's rhythm. The `ph.columns` two-pane glyph
        # reads as "show a 2nd video beside this one"; checked it goes amber via the shared QSS
        # :checked rule (mirroring the g-meter toggle), and the glyph is recoloured to C.accent so the
        # ON state is unmistakable. Off by default; enabled only when ≥2 valid laps (app drives the
        # enable flag). The toggle itself only flips _two_panes + emits compareToggled — the app owns
        # the lap-pair seeding and calls back into set_compare/exit_compare.
        self.compare_btn = QPushButton()
        self.compare_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.compare_btn.setFixedSize(_ICON_BTN)
        self.compare_btn.setCheckable(True)
        self.compare_btn.setEnabled(False)
        self.compare_btn.toggled.connect(self._on_compare_toggled)
        self._set_compare_btn_state(False)

        # The slider spans the WHOLE session (global ms 0..total). For a multi-chapter recording
        # its range is the summed duration; for a single file it's the file's own duration. The
        # value is always GLOBAL ms. It is a _LapRulerSlider so lap-boundary tick marks (fed by the
        # app via set_lap_ticks) render over the groove — the scrub bar doubles as a lap ruler.
        self.slider = _LapRulerSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setToolTip("Seek — click or drag · ←/→ step 1 s · Shift+←/→ 5 s")
        # Useful step sizes (the value is GLOBAL ms; the defaults are 1/10 ms — imperceptible):
        # a wheel notch steps 1 s (matching ←/→); a groove-click page on non-absolute-jump styles
        # steps 5 s (the macOS style jumps the handle straight to the click instead).
        self.slider.setSingleStep(1000)
        self.slider.setPageStep(5000)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        # Click-to-seek: a groove click is an ACTION (absolute jump on macOS, page step on other
        # styles), not a drag, so it never reaches sliderMoved — actionTriggered routes it through
        # the same clamped seek path (see _on_slider_action), so clicking the groove actually seeks.
        self.slider.actionTriggered.connect(self._on_slider_action)
        if self.pane.total_duration > 0:
            self.slider.setRange(0, int(self.pane.total_duration * 1000))
        self.pane.durationChanged.connect(self._on_duration)

        # Keyboard UX: the transport controls + slider must NEVER take keyboard focus — once
        # clicked they would swallow Space/arrows (button activation / slider single-steps) and
        # break the window-level shortcuts. Mouse interaction (clicks, slider drags) needs no
        # focus, so this costs nothing.
        for w in (self.play_btn, self.mute_btn, self.gmeter_btn, self.compare_btn, self.slider):
            w.setFocusPolicy(Qt.NoFocus)

        row = QHBoxLayout()
        row.addWidget(self.play_btn)
        row.addWidget(self.mute_btn)
        row.addWidget(self.gmeter_btn)
        row.addWidget(self.compare_btn)
        row.addWidget(self.slider, 1)

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
        lay.addWidget(self._stage, 1)
        lay.addLayout(row)
        lay.addWidget(self.readout)

    # ------------------------------------------------------------- public API (drives the pane)
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
        if self._two_panes and self.secondary is not None:
            self.secondary.play()

    def pause(self):
        self.pane.pause()
        if self._two_panes and self.secondary is not None:
            self.secondary.pause()

    def pause_if_playing(self):
        """Pause each pane ONLY if it is actually playing. Pausing a freshly-loaded pane that has
        never played leaves QMediaPlayer in StoppedState, and a subsequent play() from StoppedState
        restarts from position 0 — discarding a seek-to-S/F. So the compare reset uses this instead
        of pause(): it stops a playing pane (which then resumes from the seek on play) but never
        disturbs an already-stopped/paused pane, keeping its seeked lap-start position intact."""
        if self.pane.is_playing():
            self.pane.pause()
        if self._two_panes and self.secondary is not None and self.secondary.is_playing():
            self.secondary.pause()

    def toggle(self):
        # Drive both panes from the PRIMARY's state so they stay in lockstep (a single source of
        # truth for the transport icon, which follows the primary).
        if self.pane.is_playing():
            self.pause()
        else:
            self.play()

    def seek(self, seconds: float):
        """Seek to a GLOBAL session time — routed through the PRIMARY pane's chapter-aware seek.
        (In compare mode the app seeks each pane to its own lap target via seek_pane.)"""
        self.pane.seek(seconds)

    def step(self, seconds: float):
        """Step the playhead by ±`seconds` (keyboard ←/→ = ±1 s, Shift = ±5 s). The target is
        clamped to the slider's range — the whole session normally, lap A's window in compare
        mode (see _set_slider_window) — and routed through the SAME path as a slider move, so
        the compare-mode window confinement applies for free (one seek path, one clamp)."""
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
        """The live panes — always the primary, plus the secondary while in compare mode. Lets the
        SIMPLE fan-outs (both panes treated identically) loop instead of repeating the
        primary-then-if-secondary pattern; per-pane special cases (e.g. mute) stay explicit."""
        return [p for p in (self.pane, self.secondary) if p is not None]

    def stop_all(self):
        """Tear down the pane(s) for a reload ("Load full recording"): the whole VideoView is
        replaced afterwards, so FULLY dispose both panes — stop the decoder, detach the video/audio
        sinks, close the g-meter overlay window, and deleteLater the player+audio. The PRIMARY pane
        must be disposed too (not just .stop()'d): its QMediaPlayer + QAudioOutput are plain
        attributes, not Qt children of the discarded widget tree, so a bare stop() would leak the
        FFmpeg decoder + the audio device until Python GC. dispose() mirrors the secondary teardown."""
        self._teardown_secondary()
        self.pane.dispose()

    def set_readout(self, text: str):
        self.readout.setText(text)

    def set_lap_ticks(self, boundaries_s: list[float]) -> None:
        """Feed the slider its lap-boundary RULER ticks: GLOBAL-time lap start/end positions in
        seconds (the app derives them from the session lap windows). Stored so they can be re-applied
        whenever the slider RANGE changes (compare enter/exit re-confine it), and converted to the
        slider's value units (ms) for painting. Only drawn while the slider spans the whole session
        (single-video mode); in compare the range is pinned to one lap's window, where lap ticks are
        meaningless, so they're cleared (re-applied on exit)."""
        self._lap_boundaries_s = list(boundaries_s)
        self._apply_lap_ticks()

    def _apply_lap_ticks(self) -> None:
        """(Re)push the stored lap boundaries onto the slider as ms ticks — but only in single-video
        mode (the whole-session range). In compare mode the slider is confined to lap A's window, so
        clear the ruler there."""
        if self._two_panes or not getattr(self, "_lap_boundaries_s", None):
            self.slider.set_lap_ticks([])
        else:
            self.slider.set_lap_ticks([int(round(s * 1000)) for s in self._lap_boundaries_s])

    # ------------------------------------------------------------- compare toggle / enablement
    def set_compare_enabled(self, enabled: bool):
        """Enable the "Compare videos" toggle only when ≥2 valid laps exist (app drives this).
        When it goes disabled while compare is ON (e.g. a reload to a session with <2 laps), the
        button un-checks, which tears compare down via the toggled handler."""
        self._compare_enabled = bool(enabled)
        self.compare_btn.setEnabled(self._compare_enabled)
        if not self._compare_enabled and self.compare_btn.isChecked():
            self.compare_btn.setChecked(False)  # -> _on_compare_toggled(False) tears down

    def _set_compare_btn_state(self, on: bool):
        """Drive the ICON-ONLY compare toggle's OFF/ON appearance — in the same vocabulary as the
        g-meter toggle. The `ph.columns` glyph is constant (it always means "two-pane compare"); ON
        it is recoloured to C.accent and the shared QSS :checked rule tints the button amber so the
        active state is obvious without a width-breaking text label. The meaning lives in the tooltip.
        Only called on a state change (enter/exit + the initial build) — never per tick."""
        self.compare_btn.setIcon(theme.icon("ph.columns", color=theme.C.accent if on else None))
        self.compare_btn.setToolTip(
            "Comparing two laps' videos side-by-side — click to exit (C)" if on else
            "Compare two laps' videos side-by-side (C) — needs ≥2 valid laps")

    def _sync_compare_btn(self, on: bool):
        """Sync the compare toggle's CHECKED state + labeled OFF/ON appearance to the live two-pane
        layout, WITHOUT re-emitting compareToggled. set_compare / exit_compare are themselves driven
        from the app's compare orchestration; flipping the button with signals live would re-enter
        on_toggled and run a SECOND, conflicting enter()/exit() — fatal for enter_cross, whose
        set_compare would otherwise trigger a same-recording enter() that REBUILDS pane B on the
        PRIMARY source (the cross-recording reference footage is lost). So block the toggled signal
        for the programmatic sync; a genuine USER click still routes through _on_compare_toggled."""
        if self.compare_btn.isChecked() != on:
            self.compare_btn.blockSignals(True)
            self.compare_btn.setChecked(on)
            self.compare_btn.blockSignals(False)
        self._set_compare_btn_state(on)

    def _on_compare_toggled(self, on: bool):
        """The toggle flipped: swap its labeled OFF/ON appearance and emit compareToggled so the app
        seeds the lap pair (enter) or restores single-pane (exit). The actual pane build/teardown
        happens in set_compare / exit_compare, which the app calls back."""
        self._set_compare_btn_state(bool(on))
        self.compareToggled.emit(bool(on))

    def set_compare(self, pane_a: PaneSpec, pane_b: PaneSpec):
        """Enter (or re-seed) compare mode: swap the single-pane stage for a horizontal QSplitter
        of TWO equal PlayerPanes. The PRIMARY pane is the existing self.pane (telemetry driver);
        the SECONDARY pane is created LAZILY here on first entry, always muted, video-only (its
        positionChanged is NOT forwarded to the app).

        F8b: each side is now ONE `PaneSpec` (lap_id, window, caption, source, choices,
        choice_labels) instead of ~11 per-side positional params spread across the two panes — the
        two panes are seeded symmetrically and the cross-vs-same difference is simply how pane B's
        spec was built.

        `pane_b.source` is the SECONDARY pane's media source (a ChapterMap or path): None reuses
        the PRIMARY recording's source (`self._source`) — same-recording compare, byte-identical to
        before; an explicit source plays a DIFFERENT recording in pane B (F7 Phase B cross-recording
        video compare). If it differs from the source the live secondary opened on, the secondary
        pane is REBUILT on the new source (its splitter cell re-wrapped). `pane_b.choices` /
        `pane_b.choice_labels` drive pane B's lap picker — cross-recording locks it to the single
        reference lap. `pane_a.source` is conventionally None: the PRIMARY pane is never rebuilt, so
        it always plays `self._source`.

        Each pane gets its lap_window + caption + lap-picker choices; the app seeks each pane to
        its lap start separately. Re-calling this while already in compare mode just re-seeds the
        windows/captions/pickers (used after a picker repoint) WITHOUT rebuilding the splitter."""
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
                self.secondary.set_gmeter_source(self._gmeter_source)
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
            # Make the handle a real DRAG target: a visible width, no pane collapse (a collapsed
            # pane swallows the handle), and opaque (live) resize so the drag tracks. Each cell
            # gets an Expanding/Ignored size policy so neither pane's native QVideoWidget size hint
            # can PIN the split (the QVideoWidget reports an aspect-ratio hint that otherwise fights
            # an equal 50/50). Stretch factors keep the two panes sharing space 1:1 on any resize.
            self._splitter.setHandleWidth(8)
            self._splitter.setChildrenCollapsible(False)
            self._splitter.setOpaqueResize(True)
            for cell in (self._cell_a, self._cell_b):
                cell.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Expanding)
            self._splitter.setStretchFactor(0, 1)
            self._splitter.setStretchFactor(1, 1)
            self._equalize_panes()
            # Re-pin BOTH g-meter overlays when the user drags the splitter handle: a top-level
            # overlay does not follow its pane on a splitter resize unless the pane gets a
            # resizeEvent — which it does — but the handle drag can move a pane without the
            # window-server emitting a Move to the native video surface, so nudge both panes'
            # overlay re-pin from the splitter geometry change too (belt-and-braces).
            self._splitter.splitterMoved.connect(self._on_splitter_moved)

        # Swap the stage layout to the splitter (the primary pane re-parents into _cell_a).
        if not self._two_panes:
            self._stage_lay.removeWidget(self.pane)
            self._stage_lay.addWidget(self._splitter, 1)
            self.secondary.show()
            self._splitter.show()
        # Equalize AFTER the splitter is shown/laid out: setSizes applied before the splitter has a
        # width doesn't stick (the ratio is computed against a zero width), so re-split 50/50 from
        # the splitter's ACTUAL width on entry and defer one more equalize to the next event-loop
        # turn (when the first real layout has given the splitter its on-screen width).
        self._equalize_panes()
        QTimer.singleShot(0, self._equalize_panes)
        self._two_panes = True
        self._sync_compare_btn(True)

        # Seed each pane's lap window + caption + picker FROM ITS SPEC (symmetric per side). The app
        # seeks the panes to their starts. Pane B's choice list is whatever its spec carries — an
        # explicit cross-recording locked-to-reference list, or the session's valid laps for a
        # same-recording compare (the caller builds the spec accordingly).
        self.pane.set_lap_window(*pane_a.window)
        self.secondary.set_lap_window(*pane_b.window)
        self._cell_a.set_caption(pane_a.caption)
        self._cell_b.set_caption(pane_b.caption)
        self._cell_a.set_lap_choices(pane_a.choices, pane_a.lap_id, pane_a.choice_labels)
        self._cell_b.set_lap_choices(pane_b.choices, pane_b.lap_id, pane_b.choice_labels)
        # Confine the global scrub slider to lap A's window: its value is GLOBAL ms, so range it to
        # [start_a, end_a] so dragging it can never escape lap A or step the primary past the lap
        # (both panes stay aligned within the window). Re-applied on every (re)seed so a primary
        # repoint updates the slider bounds too.
        self._set_slider_window(pane_a.window)
        self._apply_lap_ticks()  # confined to one lap now -> the whole-session lap ruler is cleared

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
        if not self._two_panes:
            return
        self._two_panes = False
        self._sync_compare_btn(False)
        # Restore the single-pane stage: pull the primary pane out of its cell, drop the splitter.
        if self._splitter is not None:
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
        """Split the two compare panes 50/50 from the splitter's ACTUAL current width. setSizes with
        fixed counts ([1000,1000]) only sets a RATIO that Qt re-normalizes against the real width on
        the first layout — fine in principle, but applied before the splitter has any width the ratio
        is computed against zero and the panes can come up unequal. Computing both halves from the
        live width (falling back to the ratio when the width isn't known yet) makes the equal split
        reliable on entry; called again on the next event-loop turn once the real width is in."""
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

    def set_gmeter_source(self, source: str):
        # Remember the source so a LAZILY-created secondary pane can be seeded with it on entry
        # (set_compare), so the overlay reads the right sensor label on BOTH panes.
        self._gmeter_source = source
        for pane in self._panes():
            pane.set_gmeter_source(source)

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
        """Confine the global scrub slider to a GLOBAL-time [start, end] window (compare mode's lap
        A): the value is GLOBAL ms, so set the range to the window so a drag can't escape the lap.
        Re-pin the current value into the new range. None range bug-guard: an empty/inverted window
        falls back to a single point so the slider stays valid."""
        self._lap_window = (float(window[0]), float(window[1]))
        lo = int(window[0] * 1000)
        hi = max(int(window[1] * 1000), lo)
        self.slider.blockSignals(True)
        self.slider.setRange(lo, hi)
        self.slider.setValue(min(max(self.slider.value(), lo), hi))
        self.slider.blockSignals(False)

    def set_compare_seek_fanout(self, fn) -> None:
        """D1: inject the compare-mode fan-out hook (the app's CompareController.fanout_seek_b). It
        is called from _on_slider_moved with the primary pane's new global time so the slider/arrow
        seek is distance-locked to pane B too. None disables it (single-video mode)."""
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
        # D1: fan the SAME move out to pane B (distance-locked) so the slider/arrows can't desync the
        # pair. Only active in compare mode (the hook is set on enter, cleared on exit) and only when
        # the secondary pane is live. Done AFTER the primary seek so both panes move on the one input.
        if (self._two_panes and self.secondary is not None
                and self._compare_seek_fanout is not None):
            self._compare_seek_fanout(t)

    def _on_slider_action(self, _action: int):
        """Click-to-seek: a groove click is an ACTION, not a drag, so it never reached sliderMoved
        and only nudged the handle. Route the freshly-computed sliderPosition through the same
        clamped seek path as a drag. EVERY action seeks, including SliderMove: on the macOS style a
        groove click is an ABSOLUTE jump emitted as actionTriggered(SliderMove) with no sliderMoved
        (the press isn't a drag yet — setSliderDown happens after), on Fusion-like styles it's a
        page step, and a wheel scroll is a SliderMove too. No double-seek is possible: a handle
        DRAG emits only sliderMoved (mouseMove never goes through triggerAction)."""
        self._on_slider_moved(self.slider.sliderPosition())

    def _on_duration(self, ms: int):
        """A per-chapter REAL video-track duration arrives as each source loads (QMediaPlayer
        durationChanged, ms). Keep the slider spanning the WHOLE session. In compare mode the slider
        is confined to lap A's window, so a per-chapter duration must NOT widen it.

        D6: the slider range was sized off the GPMF metadata-track total (pane.total_duration) alone,
        discarding `ms` whenever that was > 0. But on GoPro files the telemetry track can end before
        or after the VIDEO track, so the handle pinned early (telemetry shorter) or overshot
        (telemetry longer) while the readout time stayed correct. Reconcile instead: record this
        chapter's observed video duration, then range the slider to the LARGER of the GPMF total and
        the observed video total (summed across chapters, falling back to each chapter's GPMF
        duration for any not yet loaded). A lone file with an unknown GPMF total (total_duration == 0)
        still just uses its own observed `ms`, exactly as before."""
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
        """D6: the whole-session slider max (ms) = the LARGER of the GPMF metadata total and the
        OBSERVED video total. The observed total sums each chapter's real video-track duration where
        QMediaPlayer has reported it (durationChanged), falling back to that chapter's GPMF duration
        for any chapter not yet loaded — so the total is always defined across all chapters. Taking
        the max means the handle spans the whole playable video when the telemetry track is shorter
        than the video (the early-pin case), while a LONGER telemetry track keeps the GPMF total (no
        regression). Returns the GPMF total when nothing has been observed yet (slider still valid)."""
        gpmf_total_ms = int(self.pane.total_duration * 1000)
        n = max(self.pane.chapter_count(), 1)
        observed_total_ms = sum(
            self._chapter_video_ms.get(i, int(self.pane.chapter_duration(i) * 1000))
            for i in range(n))
        return max(gpmf_total_ms, observed_total_ms)

    def _on_state(self, _state):
        # The transport glyph reflects whether EITHER pane is playing: in compare mode the two panes
        # auto-pause at their (different) lap ends, so following only the primary would let the glyph
        # lie (show "pause" while the secondary still rolls, or vice versa). Recompute from both
        # panes' live state — `_state` is ignored (it's only the trigger). Both panes' playbackState
        # are observed (primary always; secondary when it exists, wired in set_compare).
        playing = self.pane.is_playing() or (
            self.secondary is not None and self.secondary.is_playing())
        self.play_btn.setIcon(theme.icon("ph.pause-fill" if playing else "ph.play-fill"))
