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

The slider + emitted position are GLOBAL session ms (multi-chapter summed); the pane maps
global<->chapter and switches sources under the hood. In compare mode the slider spans lap A's
window via the primary pane's clamp.
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

# Horizontal inset (px) inside each compare cell so the native QVideoWidget surface (which on macOS
# composites above sibling chrome) doesn't swallow the splitter handle's mouse events.
_PANE_INSET = 5

# Floor width (px) for each pane's lap picker (the sole home of the lap text) so it never clips;
# AdjustToContents grows it past this for a wider current item.
_PICKER_MIN_W = 150


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


class _LapRulerSlider(QSlider):
    """Horizontal QSlider that also paints lap-boundary tick marks over the groove (MoTeC-style lap
    ruler). Ticks are global-ms boundaries fed via `set_lap_ticks`; only painting is extended — seek
    wiring is the base slider's. Each tick maps to x via the style's groove rect +
    sliderPositionFromValue (the handle's own geometry), so ticks and handle agree."""

    _TICK_H = 10   # px tall (centred on the groove), a touch taller than the 8px groove so it reads

    def __init__(self, orientation):
        super().__init__(orientation)
        self._lap_ticks: list[int] = []  # boundary values in slider units (ms), sorted/unique

    def set_lap_ticks(self, values: list[int]) -> None:
        """Set lap-boundary ticks (global ms); out-of-range values clamp at paint, empty clears.
        Repaints."""
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
        # handle-travel span = groove minus handle width (matches sliderPositionFromValue)
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        handle = self.style().subControlRect(
            QStyle.CC_Slider, opt, QStyle.SC_SliderHandle, self)
        span = groove.width() - handle.width()
        x0 = groove.x() + handle.width() // 2
        cy = groove.center().y()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, False)
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
    """Compare-pane chrome: a strip (fixed role caption · lap picker · Δ badge) above the PlayerPane.
    Owns no playback state. The lap identity lives ONLY in the picker; the caption is a fixed role
    word, the badge yields width first. Selecting a lap emits `repointRequested(lap_id)`."""

    repointRequested = Signal(int)  # the newly-picked lap id for this side

    def __init__(self, pane: PlayerPane, side: int):
        super().__init__()
        self.pane = pane
        self.side = side
        self._lap_ids: list[int] = []
        self._labels: list[str] = []   # last-applied picker item labels (guards the repopulate)

        # fixed role word; Fixed size so the picker (not it) grows
        self.caption = QLabel("THIS LAP" if side == PRIMARY else "REFERENCE")
        self.caption.setObjectName("PaneCaption")
        self.caption.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.caption.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Preferred)

        # sole home of lap identity; floor width + AdjustToContents so it never clips
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
        self._badge_colour: str | None = None
        self._badge_text = "Δ —"   # last-applied badge text (guards the per-tick setText)

        # caption, picker (grows), a flexible gap, then the badge pinned right — the gap absorbs
        # spare width and collapses first, so no identifying text clips when width is tight.
        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        strip.addWidget(self.caption)
        strip.addWidget(self.picker)
        strip.addStretch(1)
        strip.addWidget(self.badge)

        lay = QVBoxLayout(self)
        # horizontal inset so the native video surface doesn't swallow the splitter handle (see _PANE_INSET).
        lay.setContentsMargins(_PANE_INSET, 0, _PANE_INSET, 0)
        lay.setSpacing(0)
        lay.addLayout(strip)
        lay.addWidget(self.pane, 1)

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

    def set_caption(self, text: str):
        """Compat shim: the app passes rich "lap N · time" text; show it as the role caption's
        TOOLTIP (the label stays the fixed role word — identity lives in the picker)."""
        self.caption.setToolTip(text)

    def set_badge(self, text: str, colour: str | None):
        """Set the Δ badge text/colour (app-driven per tick), guarded: re-apply only on an actual
        change so a stable compare view does zero per-tick label work (setText relayout / QSS
        re-parse)."""
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
        # Remembered so the lazy secondary pane can open the SAME ChapterMap.
        self._source = source
        # The PRIMARY pane owns the decode/overlay stack and is ALWAYS the telemetry driver.
        self.pane = PlayerPane(source)
        self.pane.positionChanged.connect(self._on_pane_position)
        self.pane.chapterChanged.connect(self.chapterChanged)
        self.pane.playbackStateChanged.connect(self._on_state)
        # Forward only the PRIMARY pane's seam-reopen hint (the secondary is video-only).
        self.pane.seamLoading.connect(self.seamLoading)

        # True iff the two-pane stage is mounted (the view's layout state; the secondary pane + cells
        # exist only while this is on).
        self._two_panes = False
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
        self._gmeter_visible = False
        self.secondary: PlayerPane | None = None
        # the source the live secondary opened on (normally self._source; the reference ChapterMap
        # for cross-recording compare); a source change rebuilds the secondary.
        self._secondary_source: object = None
        self._cell_a: _PaneCell | None = None   # primary cell wrapper (compare mode)
        self._cell_b: _PaneCell | None = None   # secondary cell wrapper (compare mode)
        self._splitter: QSplitter | None = None
        self._compare_enabled = False           # ≥2 valid laps (set by app via set_compare_enabled)
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
        # with >=2 laps. Appearance/meaning: _set_compare_btn_state. Emits compareToggled; the app
        # calls back set_compare/exit_compare (which own _two_panes + the panes).
        self.compare_btn = QPushButton()
        self.compare_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.compare_btn.setFixedSize(_ICON_BTN)
        self.compare_btn.setCheckable(True)
        self.compare_btn.setEnabled(False)
        self.compare_btn.toggled.connect(self._on_compare_toggled)
        self._set_compare_btn_state(False)

        # global-ms scrub slider over the whole session (multi-chapter summed); _LapRulerSlider
        # paints lap ticks.
        self.slider = _LapRulerSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.setToolTip("Seek — click or drag · ←/→ step 1 s · Shift+←/→ 5 s")
        self.slider.setSingleStep(1000)   # wheel/←→ step 1s
        self.slider.setPageStep(5000)     # page step 5s
        self.slider.sliderMoved.connect(self._on_slider_moved)
        # groove clicks are actionTriggered not sliderMoved — route them through the same clamped seek.
        self.slider.actionTriggered.connect(self._on_slider_action)
        if self.pane.total_duration > 0:
            self.slider.setRange(0, int(self.pane.total_duration * 1000))
        self.pane.durationChanged.connect(self._on_duration)

        # The transport controls must never take keyboard focus, or they'd swallow Space/arrows and
        # break the window-level shortcuts (mouse interaction needs no focus).
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
        """Pause each pane only if actually playing. pause() on a never-played (Stopped) pane makes
        the next play() restart from 0, discarding a seek-to-S/F — so the compare reset uses this to
        keep each pane parked at its lap start."""
        if self.pane.is_playing():
            self.pane.pause()
        if self._two_panes and self.secondary is not None and self.secondary.is_playing():
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
        """Drive the compare toggle's OFF/ON appearance (glyph accent + tooltip). Only called on a
        state change, never per tick."""
        self.compare_btn.setIcon(theme.icon("ph.columns", color=theme.C.accent if on else None))
        self.compare_btn.setToolTip(
            "Comparing two laps' videos side-by-side — click to exit (C)" if on else
            "Compare two laps' videos side-by-side (C) — needs ≥2 valid laps")

    def _sync_compare_btn(self, on: bool):
        """Sync the toggle's checked state + appearance to the live layout WITHOUT re-emitting
        compareToggled — a live signal would re-enter _on_compare_toggled and run a conflicting
        second enter/exit (corrupts cross-recording pane B). A genuine user click still routes
        through _on_compare_toggled."""
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

        # Swap the stage layout to the splitter (the primary pane re-parents into _cell_a).
        if not self._two_panes:
            self._stage_lay.removeWidget(self.pane)
            self._stage_lay.addWidget(self._splitter, 1)
            self.secondary.show()
            self._splitter.show()
        # equalize now and again next event-loop turn (setSizes needs a real width — see _equalize_panes)
        self._equalize_panes()
        QTimer.singleShot(0, self._equalize_panes)
        self._two_panes = True
        self._sync_compare_btn(True)

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
        if (self._two_panes and self.secondary is not None
                and self._compare_seek_fanout is not None):
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
