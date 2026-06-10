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
no decoder/overlay leaks. Each pane shows a caption ("lap N · m:ss.mmm", ★ on the best lap), a
compact lap picker to repoint that side (its items carry the lap time too; emits
`paneRepointRequested`), and a "Δ vs other" badge the app updates. Play/pause/mute fan out to
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

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSplitter,
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


class _PaneCell(QWidget):
    """A compare-mode pane wrapper: a caption strip (caption · lap picker · Δ badge) above the
    PlayerPane's video. Pure chrome — it owns no playback state; the PlayerPane it wraps does.
    The lap picker is a compact combo of valid laps; selecting one emits `repointRequested(lap_id)`
    which VideoView forwards to the app, tagged with this cell's side index."""

    repointRequested = Signal(int)  # the newly-picked lap id for this side

    def __init__(self, pane: PlayerPane, side: int):
        super().__init__()
        self.pane = pane
        self.side = side
        self._lap_ids: list[int] = []
        self._labels: list[str] = []   # last-applied picker item labels (guards the repopulate)

        self.caption = QLabel("")
        self.caption.setObjectName("PaneCaption")
        self.caption.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)

        # Compact lap picker: lists valid laps; repoints this side without touching the other.
        self.picker = QComboBox()
        self.picker.setToolTip("Pick the lap shown in this pane")
        self.picker.currentIndexChanged.connect(self._on_pick)

        # "Δ vs other" badge — app drives the text + colour per tick (transparent inline label).
        self.badge = QLabel("Δ —")
        self.badge.setObjectName("PaneBadge")
        self.badge.setAlignment(Qt.AlignVCenter | Qt.AlignRight)
        self._badge_colour: str | None = None
        self._badge_text = "Δ —"   # last-applied badge text (guards the per-tick setText)

        strip = QHBoxLayout()
        strip.setContentsMargins(0, 0, 0, 0)
        strip.setSpacing(6)
        strip.addWidget(self.caption)
        strip.addWidget(self.picker)
        strip.addStretch(1)
        strip.addWidget(self.badge)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
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
        self.caption.setText(text)

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
        # so dragging it can't escape lap A or desync the pair (both panes step within lap A's
        # window). None => the slider spans the whole session, exactly as before.
        self._lap_window: tuple[float, float] | None = None
        # Last g-meter source + visible state, captured so a LAZILY-created secondary pane can be
        # seeded with them on entry (toggling g-meter ON then entering compare must show the overlay
        # on BOTH panes with the right source). set by set_gmeter_source / set_gmeter_visible.
        self._gmeter_source: str | None = None
        self._gmeter_visible = False
        self.secondary: PlayerPane | None = None
        self._cell_a: _PaneCell | None = None   # primary cell wrapper (compare mode)
        self._cell_b: _PaneCell | None = None   # secondary cell wrapper (compare mode)
        self._splitter: QSplitter | None = None
        self._compare_enabled = False           # ≥2 valid laps (set by app via set_compare_enabled)

        # Compact Phosphor-icon transport buttons (no text). Icons are themed via theme.icon and
        # set ONCE per state change in the existing handlers — never on the playback tick.
        self.play_btn = QPushButton()
        self.play_btn.setIcon(theme.icon("ph.play-fill"))
        self.play_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.play_btn.setFixedSize(_ICON_BTN)
        self.play_btn.setToolTip("Play / pause")
        self.play_btn.clicked.connect(self.toggle)

        # F4: mute/unmute toggle. speaker-x while muted (default), speaker-high while audible.
        self.mute_btn = QPushButton()
        self.mute_btn.setIcon(theme.icon("ph.speaker-simple-x"))
        self.mute_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.mute_btn.setFixedSize(_ICON_BTN)
        self.mute_btn.setToolTip("Audio muted — click to unmute")
        self.mute_btn.clicked.connect(self.toggle_mute)

        # g-meter show/hide toggle (the friction-circle overlay on the video). Checkable: the QSS
        # :checked rule tints the button accent; we also recolour the GLYPH to C.accent when on.
        self.gmeter_btn = QPushButton()
        self.gmeter_btn.setIcon(theme.icon("ph.gauge"))
        self.gmeter_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.gmeter_btn.setFixedSize(_ICON_BTN)
        self.gmeter_btn.setCheckable(True)
        self.gmeter_btn.setToolTip("Show/hide the g-meter overlay")
        self.gmeter_btn.toggled.connect(self._on_gmeter_toggled)
        self.gmeter_btn.toggled.connect(self.set_gmeter_visible)

        # "Compare videos" toggle (Phase B): a LABELED checkable button that reveals a 2nd, equal
        # video pane side-by-side. Off by default; enabled only when ≥2 valid laps (app drives the
        # enable flag). The toggle itself only flips _two_panes + emits compareToggled — the app owns
        # the lap-pair seeding and calls back into set_compare/exit_compare. Its label + fill swap
        # between states in _set_compare_btn_state so it's obvious both what it does (adds a 2nd
        # comparison video) and whether it's currently on ("⧉ Compare" ghost → "Comparing ✕" amber).
        self.compare_btn = QPushButton()
        self.compare_btn.setIconSize(QSize(_ICON_PX, _ICON_PX))
        self.compare_btn.setFixedHeight(_ICON_BTN.height())
        self.compare_btn.setCheckable(True)
        self.compare_btn.setEnabled(False)
        self.compare_btn.toggled.connect(self._on_compare_toggled)
        self._set_compare_btn_state(False)

        # The slider spans the WHOLE session (global ms 0..total). For a multi-chapter recording
        # its range is the summed duration; for a single file it's the file's own duration. The
        # value is always GLOBAL ms.
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        if self.pane.total_duration > 0:
            self.slider.setRange(0, int(self.pane.total_duration * 1000))
        self.pane.durationChanged.connect(self._on_duration)

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

    def _cell_for(self, side: int) -> "_PaneCell | None":
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
        """Drive the labeled compare toggle's appearance for its OFF/ON state. OFF: a ghost/neutral
        "⧉ Compare" (columns glyph + text) that reads as "click to add a 2nd comparison video". ON:
        an accent-FILLED "Comparing ✕" (the variant=primary amber fill via the QSS) with a small
        close affordance so it's obviously active and reads as "click to exit". Only called on a
        state change (enter/exit + the initial build) — never per tick."""
        if on:
            self.compare_btn.setIcon(theme.icon("ph.columns", color=theme.C.on_accent))
            self.compare_btn.setText(" Comparing  ✕")
            self.compare_btn.setToolTip("Comparing two laps' videos — click to exit")
            self.compare_btn.setProperty("variant", "primary")
        else:
            self.compare_btn.setIcon(theme.icon("ph.columns"))
            self.compare_btn.setText(" Compare")
            self.compare_btn.setToolTip(
                "Compare two laps' videos side-by-side (needs ≥2 valid laps)")
            self.compare_btn.setProperty("variant", "")
        # A dynamic-property change needs an explicit style re-polish to take effect (Qt caches
        # the resolved QSS until the property is re-evaluated). Done only on the state flip.
        self.compare_btn.style().unpolish(self.compare_btn)
        self.compare_btn.style().polish(self.compare_btn)

    def _on_compare_toggled(self, on: bool):
        """The toggle flipped: swap its labeled OFF/ON appearance and emit compareToggled so the app
        seeds the lap pair (enter) or restores single-pane (exit). The actual pane build/teardown
        happens in set_compare / exit_compare, which the app calls back."""
        self._set_compare_btn_state(bool(on))
        self.compareToggled.emit(bool(on))

    def set_compare(self, lap_a: int, lap_b: int,
                    window_a: tuple[float, float], window_b: tuple[float, float],
                    caption_a: str, caption_b: str,
                    lap_choices: list[int], lap_choice_labels: list[str] | None = None):
        """Enter (or re-seed) compare mode: swap the single-pane stage for a horizontal QSplitter
        of TWO equal PlayerPanes. The PRIMARY pane is the existing self.pane (telemetry driver);
        the SECONDARY pane is created LAZILY here on first entry (its own source = the session
        ChapterMap), always muted, video-only (its positionChanged is NOT forwarded to the app).

        Each pane gets its lap_window + caption + lap-picker choices; the app seeks each pane to
        its lap start separately. Re-calling this while already in compare mode just re-seeds the
        windows/captions/pickers (used after a picker repoint) WITHOUT rebuilding the splitter."""
        # Lazily create the secondary pane + the splitter on first entry.
        if self.secondary is None:
            self.secondary = PlayerPane(self._source)
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
            self._splitter.setSizes([1000, 1000])  # two EQUAL panes
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
        self._two_panes = True
        if self.compare_btn.isChecked() != self._two_panes:
            self.compare_btn.setChecked(self._two_panes)

        # Seed each pane's lap window + caption + picker. The app seeks the panes to their starts.
        self.pane.set_lap_window(*window_a)
        self.secondary.set_lap_window(*window_b)
        self._cell_a.set_caption(caption_a)
        self._cell_b.set_caption(caption_b)
        self._cell_a.set_lap_choices(lap_choices, lap_a, lap_choice_labels)
        self._cell_b.set_lap_choices(lap_choices, lap_b, lap_choice_labels)
        # Confine the global scrub slider to lap A's window: its value is GLOBAL ms, so range it to
        # [start_a, end_a] so dragging it can never escape lap A or step the primary past the lap
        # (both panes stay aligned within the window). Re-applied on every (re)seed so a primary
        # repoint updates the slider bounds too.
        self._set_slider_window(window_a)

    def reseed_pane(self, side: int, lap_id: int, window: tuple[float, float],
                    caption: str, lap_choices: list[int],
                    lap_choice_labels: list[str] | None = None):
        """Repoint ONE pane (after its lap picker was used): update its lap window + caption +
        keep the picker selection in sync. The app re-seeks this pane to its new lap start and
        refreshes the chart overlay + Δ badge. Used so a repoint never disturbs the other pane."""
        pane = self._pane_for(side)
        cell = self._cell_for(side)
        if pane is None or cell is None:
            return
        pane.set_lap_window(*window)
        cell.set_caption(caption)
        cell.set_lap_choices(lap_choices, lap_id, lap_choice_labels)
        # A PRIMARY repoint changes lap A's window — re-confine the global scrub to the new window
        # so the slider keeps tracking the (telemetry-driving) primary pane within its lap.
        if side == PRIMARY:
            self._set_slider_window(window)

    def exit_compare(self):
        """Leave compare mode: tear the secondary pane down (stop + deleteLater player+audio,
        .close() overlay) and restore the single-pane stage at the PRIMARY's current position.
        The primary pane keeps decoding the whole session again (its lap window is cleared)."""
        if not self._two_panes:
            return
        self._two_panes = False
        if self.compare_btn.isChecked():
            self.compare_btn.setChecked(False)
        # Restore the single-pane stage: pull the primary pane out of its cell, drop the splitter.
        if self._splitter is not None:
            self._stage_lay.removeWidget(self._splitter)
        # Reparent the primary pane back into the stage (out of _cell_a) BEFORE deleting the cells.
        self._stage_lay.addWidget(self.pane, 1)
        self.pane.show()
        self.pane.clear_lap_window()  # whole session again — normal mode, behaviour unchanged
        # Drop the slider's lap-A confinement and restore the whole-session range.
        self._lap_window = None
        if self.pane.total_duration > 0:
            self.slider.setRange(0, int(self.pane.total_duration * 1000))
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
        if sec is None:
            return
        sec.dispose()         # stop decoder + detach sinks + deleteLater player/audio/overlay
        sec.setParent(None)
        sec.deleteLater()     # schedule the pane widget itself for deletion on the event loop

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
        self.mute_btn.setToolTip("Audio muted — click to unmute" if muted
                                 else "Audio on — click to mute")

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

    def _on_slider_moved(self, ms: int):
        # The slider value is GLOBAL ms — route it through the PRIMARY pane's chapter-aware seek.
        # In compare mode clamp to lap A's window so a drag can't desync the pair or escape the lap.
        if self._lap_window is not None:
            lo, hi = self._lap_window
            ms = min(max(ms, int(lo * 1000)), int(hi * 1000))
        self.seek(ms / 1000.0)

    def _on_duration(self, ms: int):
        """A per-chapter duration arrives as each source loads. Keep the slider spanning the WHOLE
        session (the ChapterMap total when known, else this lone file's own duration). In compare
        mode the slider is confined to lap A's window, so a per-chapter duration must NOT widen it."""
        if self._lap_window is not None:
            return  # compare mode: the range is pinned to lap A's window (see _set_slider_window)
        if self.pane.total_duration > 0:
            self.slider.setMaximum(int(self.pane.total_duration * 1000))
        else:
            self.slider.setMaximum(ms)

    def _on_state(self, _state):
        # The transport glyph reflects whether EITHER pane is playing: in compare mode the two panes
        # auto-pause at their (different) lap ends, so following only the primary would let the glyph
        # lie (show "pause" while the secondary still rolls, or vice versa). Recompute from both
        # panes' live state — `_state` is ignored (it's only the trigger). Both panes' playbackState
        # are observed (primary always; secondary when it exists, wired in set_compare).
        playing = self.pane.is_playing() or (
            self.secondary is not None and self.secondary.is_playing())
        self.play_btn.setIcon(theme.icon("ph.pause-fill" if playing else "ph.play-fill"))
