"""VideoView: the thin player SHELL — transport chrome around exactly ONE PlayerPane.

The single-lap player stack (QMediaPlayer + QVideoWidget + QAudioOutput, the ChapterMap-based
source-switching seek, the deferred cross-chapter seek, the EndOfMedia auto-advance, and the
g-meter overlay) now lives in `player_pane.PlayerPane`. VideoView keeps ONLY the transport row
(play/pause/mute/g-meter icon buttons + a GLOBAL-time scrub slider + the #Readout label) and a
layout holding one PlayerPane, and re-exposes the SAME public API the app already drives
(`seek`, `play`, `pause`, `is_playing`, `current_chapter`, `is_multi`, `set_g`, `set_readout`,
`set_gmeter_source`, `set_gmeter_lap`, the `gmeter_btn`, and `positionChanged`/`chapterChanged`),
so app.py is unchanged apart from the new `stop_all()` teardown hook. Phase B composes two panes;
Phase A keeps the single-lap behaviour byte-for-byte identical.

A recording can be a single file OR a chaptered multi-file recording. The slider + the emitted
position are in GLOBAL session time (0..sum-of-durations), so the telemetry sync (cursor, map
marker, plots, readout) sees one continuous clock; the pane maps global<->chapter time and
switches sources / auto-advances across chapters under the hood.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import chapters, theme
from .player_pane import PlayerPane

# Phosphor (qtawesome `ph` prefix) glyphs for the transport bar, themed via theme.icon.
_ICON_PX = 18                       # glyph render size inside the buttons
_ICON_BTN = QSize(32, 30)           # compact square-ish icon button


class VideoView(QWidget):
    positionChanged = Signal(float)  # GLOBAL seconds on the session clock (forwarded from the pane)
    chapterChanged = Signal(int)     # current chapter index (forwarded from the pane)

    def __init__(self, source: str | chapters.ChapterMap | None):
        super().__init__()
        # The single PlayerPane owns the whole decode/overlay stack; the shell drives it.
        self.pane = PlayerPane(source)
        # Forward the pane's signals so the app wires to VideoView exactly as before.
        self.pane.positionChanged.connect(self._on_pane_position)
        self.pane.chapterChanged.connect(self.chapterChanged)
        self.pane.playbackStateChanged.connect(self._on_state)

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

        # The slider spans the WHOLE session (global ms 0..total). For a multi-chapter recording
        # its range is the summed duration; for a single file it's the file's own duration. The
        # value is always GLOBAL ms.
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 0)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        if self.pane.total_duration > 0:
            self.slider.setRange(0, int(self.pane.total_duration * 1000))
        # A per-chapter duration arrives as each source loads; keep the slider spanning the WHOLE
        # session (the ChapterMap total when known, else this lone file's own duration).
        self.pane.player.durationChanged.connect(self._on_duration)

        row = QHBoxLayout()
        row.addWidget(self.play_btn)
        row.addWidget(self.mute_btn)
        row.addWidget(self.gmeter_btn)
        row.addWidget(self.slider, 1)

        self.readout = QLabel("")  # F2: time / speed / current lap, driven by app
        self.readout.setObjectName("Readout")  # caption style, dimmed, tabular (global QSS)
        self.readout.setAlignment(Qt.AlignCenter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.pane, 1)
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
        self.pane.play()

    def pause(self):
        self.pane.pause()

    def toggle(self):
        self.pane.toggle()

    def seek(self, seconds: float):
        """Seek to a GLOBAL session time — routed through the pane's chapter-aware seek."""
        self.pane.seek(seconds)

    def stop_all(self):
        """Tear down the pane(s): stop the decoder AND close the g-meter overlay window. Called on
        a reload ("Load full recording") before the old widget tree is replaced."""
        self.pane.stop()

    def set_readout(self, text: str):
        self.readout.setText(text)

    # ------------------------------------------------------------- audio (mute)
    def toggle_mute(self):
        """F4: flip the audio mute state and update the button icon/tooltip."""
        muted = not self.pane.is_muted()
        self.pane.set_muted(muted)
        self.mute_btn.setIcon(theme.icon("ph.speaker-simple-x" if muted
                                         else "ph.speaker-simple-high"))
        self.mute_btn.setToolTip("Audio muted — click to unmute" if muted
                                 else "Audio on — click to mute")

    # ------------------------------------------------------------- g-meter overlay (drives pane)
    def _on_gmeter_toggled(self, on: bool):
        """Recolour the g-meter glyph to the accent when the overlay is active (the QSS already
        tints the button background on :checked). Separate from set_gmeter_visible so the icon
        recolour stays a pure visual concern."""
        self.gmeter_btn.setIcon(theme.icon("ph.gauge", color=theme.C.accent if on
                                           else theme.C.text))

    def set_gmeter_visible(self, on: bool):
        """Show/hide the friction-circle g-meter overlay (the toggle button) on the pane."""
        self.pane.set_gmeter_visible(on)
        if self.gmeter_btn.isChecked() != self.pane.is_gmeter_visible():
            self.gmeter_btn.setChecked(self.pane.is_gmeter_visible())

    def set_g(self, g):
        """Feed the current (lateral_g, longitudinal_g, total_g) to the pane's overlay (None blanks
        the live dot). A no-op when the overlay is hidden, so the app can call it every tick."""
        self.pane.set_g(g)

    def set_gmeter_source(self, source: str):
        self.pane.set_gmeter_source(source)

    def set_gmeter_lap(self, lap_id):
        """Tell the overlay which lap is being driven so its max-G envelope resets at the lap
        boundary (per-lap grip-usage scope). A no-op repaint cost when the overlay is hidden."""
        self.pane.set_gmeter_lap(lap_id)

    # ------------------------------------------------------------- pane <-> shell wiring
    def _on_pane_position(self, global_s: float):
        """The pane advanced (global session seconds): track the slider (global ms) and forward
        the position to the app for the telemetry sync."""
        self.slider.blockSignals(True)
        self.slider.setValue(int(global_s * 1000))
        self.slider.blockSignals(False)
        self.positionChanged.emit(global_s)

    def _on_slider_moved(self, ms: int):
        # The slider value is GLOBAL ms — route it through the pane's chapter-aware seek.
        self.seek(ms / 1000.0)

    def _on_duration(self, ms: int):
        """A per-chapter duration arrives as each source loads. Keep the slider spanning the WHOLE
        session: when the ChapterMap already knows the total (multi-chapter, durations from the
        GPMF), use that; otherwise (a lone file with unknown duration) fall back to this file's
        own duration so the single-file slider still works."""
        if self.pane.total_duration > 0:
            self.slider.setMaximum(int(self.pane.total_duration * 1000))
        else:
            self.slider.setMaximum(ms)

    def _on_state(self, state):
        playing = state == QMediaPlayer.PlaybackState.PlayingState
        self.play_btn.setIcon(theme.icon("ph.pause-fill" if playing else "ph.play-fill"))
