"""CentralView: the session-scoped central widget for ONE loaded recording.

Owns the panels — video / map / plots / the TABBED lap panel (Laps · Corners · Stats · Coaching,
one QTabBar over one QStackedWidget; every page full-height) / diff_box / chapter banner — the
compare + scrub controllers, the shared PlaybackState and the per-frame ``tick()`` — all built
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

import os

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from . import chapters, sidecar, theme, units
from .coaching_panel import OpportunitiesPanel
from .compare_controller import CompareController
from .lap_table import CornerTable, LapTable
from .map_view import MapView
from .playback_state import PlaybackState
from .plots_view import PlotsView
from .scrub_controller import ScrubController
from .session import fmt_time
from .stats_panel import StatsView
from .video_view import VideoView

# The maximize-button glyphs. DELIBERATELY DISTINCT from the video transport's fullscreen ⤢ button
# (ph.arrows-out / ph.arrows-in — "fill the SCREEN"): the corners glyphs read as "fill this WINDOW
# quadrant", a different action, so the two never collide on the video header. Maximize glyph while
# the panel is in the grid, restore glyph while it's maximized.
# The charts panel's section label, per Δ baseline (see plots_view.deltaBaselineChanged), in two
# lengths. The bar names the baseline the LOWER CHART actually draws, which is NOT the hero
# readout's own reference — so when the bar is cramped it drops the decorative "SPEED · " half and
# keeps the naming (see _fit_plots_header for the full yield order).
_PLOTS_LABEL_BEST = "SPEED · Δ TO BEST"
_PLOTS_LABEL_IDEAL = "SPEED · Δ TO IDEAL"
_PLOTS_CHIP_BEST = "Δ BEST"
_PLOTS_CHIP_IDEAL = "Δ IDEAL"
_MAXIMIZE_GLYPH = "ph.corners-out"   # expand this panel to fill the window
_RESTORE_GLYPH = "ph.corners-in"     # shown while maximized — click/Esc to restore the grid
# The header maximize button is sized like the video transport's icon buttons so the whole app's
# header controls read as one family (video_view._ICON_PX / _ICON_BTN).
_HDR_ICON_PX = 15
# 24 px tall, not 22: these are the ONLY always-visible affordance that restores a maximized panel,
# and 26x22 sat under the 24x24 hit-target floor. The header bar's 4 px vertical margins already
# had the room, so the headers do not grow.
_HDR_ICON_BTN = QSize(26, 24)

# The widest readouts the hero #DiffBox can ever render (theme.format_ideal_readout /
# format_delta_speed at their longest realistic values), used to derive its layout floor below.
_HERO_TEMPLATES = (
    "Δideal +10.00 s     188 km/h",   # leading with Δ-to-ideal — the default reference
    "Δ -10.00 s ▼     188 km/h",      # leading with Δ-to-best, plus its direction arrow
)
_HERO_PAD_PX = 20   # the QSS's `#DiffBox { padding: 2px 8px }` (16) + a rounding px per side


def _hero_min_width() -> int:
    """Layout floor for the hero Δ/speed readout: the widest text it can show + its QSS padding.

    A QLabel never elides — it HARD-CLIPS — so without a floor the charts header's proportional
    squeeze eats characters off the live number itself. Measured in the font the QSS actually
    PAINTS #DiffBox in (the mono stack at HERO/600); theme.mono_font() resolves to Inter+tnum and
    measures ~80 px narrower, which would under-size the floor by exactly that much."""
    families = [name.strip(' "') for name in theme.MONO_STACK.split(",")]
    f = QFont()
    f.setFamilies(families)
    f.setPixelSize(theme.HERO)
    f.setWeight(theme.W_SEMIBOLD)
    fm = QFontMetrics(f)
    return max(fm.horizontalAdvance(t) for t in _HERO_TEMPLATES) + _HERO_PAD_PX


class CentralView(QWidget):
    """Session-scoped central widget for one loaded recording (see module docstring)."""

    # Emitted after any timing-line change (a user drag OR an Undo) so the window can refresh the
    # Edit ▸ Undo action's enabled state from the session's undo stack.
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

    # ------------------------------------------------------------------ lifecycle
    def showEvent(self, event):
        """First show: restore the persisted grid-splitter sizes (deferred one event-loop turn
        so the splitters carry their REAL post-layout sizes — a pre-show setSizes gets warped
        by min-size clamping at the unshown widget's tiny default geometry)."""
        super().showEvent(event)
        pending = getattr(self, "_pending_grid_sizes", None)
        if pending is not None:
            self._pending_grid_sizes = None
            # Twice, idempotently: once right after this event burst, once after the deferred
            # top-level layout passes (which re-split by stretch factor and would otherwise
            # override the restore of whichever splitter they touch last).
            QTimer.singleShot(0, lambda: self._apply_grid_sizes(pending))
            QTimer.singleShot(120, lambda: self._apply_grid_sizes(pending))

    def _apply_grid_sizes(self, stored: list):
        """Apply persisted [main, left, right] splitter sizes. Each list is applied only if it
        matches its splitter's section count AND every section is strictly positive — a
        stale/corrupt pref falls back cleanly to the built-in defaults (already set).

        The positivity test is what stops a prefs.json written by a build that still let a drag
        collapse a column (see _layout_panels) from RESURRECTING the deleted panel on every
        relaunch: a stored [1432, 0] used to pass the old count/non-negative/sum>0 guard and
        reopen the window with MAP and CHARTS off screen. Rejecting the one bad list leaves the
        other two splitters free to restore, so a user only loses the layout that was unusable."""
        for splitter, sizes in zip(
                (self._main_splitter, self._left_splitter, self._right_splitter),
                stored, strict=False):
            if (isinstance(sizes, (list, tuple)) and len(sizes) == splitter.count()
                    and all(isinstance(v, (int, float)) and v > 0 for v in sizes)):
                splitter.setSizes([int(v) for v in sizes])

    def dispose(self):
        """Stop decoder(s) + close the g-meter overlay before this view is dropped on reload. Called
        by StudioWindow on the outgoing view. No-op if no video."""
        video = getattr(self, "video", None)
        if video is not None:
            video.stop_all()

    # --------------------------------------------------------- panel container helpers
    @staticmethod
    def _panel(title: str, *contents) -> QWidget:
        """Wrap content under a .PanelHeader label. Each entry is a widget or a (widget, stretch) tuple."""
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        header = QLabel(title)
        header.setProperty("role", "PanelHeader")
        lay.addWidget(header)
        for c in contents:
            if isinstance(c, tuple):
                lay.addWidget(c[0], c[1])
            else:
                lay.addWidget(c)
        return panel

    @staticmethod
    def _header_bar(*segments) -> QWidget:
        """.PanelHeader-styled strip holding widgets (map/charts headers). Segments: a widget, an int
        (addStretch), or a (widget, stretch)."""
        bar = QWidget()
        bar.setProperty("role", "PanelHeader")
        row = QHBoxLayout(bar)
        row.setContentsMargins(8, 4, 8, 4)
        row.setSpacing(8)
        for seg in segments:
            if isinstance(seg, int):
                row.addStretch(seg)
            elif isinstance(seg, tuple):
                row.addWidget(seg[0], seg[1])
            else:
                row.addWidget(seg)
        return bar

    def _maximize_button(self) -> QPushButton:
        """A small right-aligned header button that maximizes/restores its panel — the VISIBLE
        affordance for the same action as double-clicking the header. Styled like the video
        transport's icon buttons so every header control reads as one family. Wired to its panel
        later by _wire_maximize_button (the panel container doesn't exist yet when the header is
        built); its glyph/tooltip track the maximized state via _sync_maximize_buttons."""
        btn = QPushButton()
        btn.setIconSize(QSize(_HDR_ICON_PX, _HDR_ICON_PX))
        btn.setFixedSize(_HDR_ICON_BTN)
        btn.setFlat(True)
        btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        btn.setIcon(theme.icon(_MAXIMIZE_GLYPH))
        btn.setToolTip("Maximize panel (or double-click the header) — Esc / click again to restore")
        return btn

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
    def _headered(header: QWidget, *contents) -> QWidget:
        """Stack a header widget above contents (like _panel but with a widget header). Each entry is
        a widget or a (widget, stretch) tuple."""
        panel = QWidget()
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(header)
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
        # Only offer the g-meter toggle when a g signal was computed (IMU present).
        self.video.set_gmeter_source(self.session.gmeter_source(),
                                     self.session.gmeter_long_source())
        self.video.gmeter_btn.setEnabled(self.session.has_gmeter)
        if not self.session.has_gmeter:
            self.video.gmeter_btn.setToolTip("No accelerometer data in this recording")
        self.map = MapView(self.session)
        # Corner labels pushed from here so MapView stays a pure consumer of marker tuples.
        self.map.set_corners(self.session.corners.corner_map_markers())
        self.plots = PlotsView(self.session)
        self.table = LapTable(self.session)
        # Corners mode: a 2nd table stacked under the same panel (per-corner rows for the selected lap).
        self.corner_table = CornerTable(self.session)
        # B4: a Corners row rings its apex on the map, like the Stats CORNERS and Coaching
        # rows — the same maximize-aware handler, so the three surfaces behave identically.
        self.corner_table.corner_clicked.connect(self._on_stats_corner_clicked)
        self._corner_lap: int | None = None  # the lap the Corners view describes
        # Stats mode: the 3rd page — the session-statistics dashboard (studio/stats_panel.py).
        self.stats_view = StatsView(self.session)

        # Always-on Δ/speed readout for the current moment (hero #DiffBox; Δ colour set per-tick).
        # By default it LEADS with Δ-to-IDEAL (the moat number: how far off your own achievable lap
        # you are, right here) rather than Δ-to-best; the small ideal_readout_btn flips it to
        # Δ-to-best, and whichever number isn't shown lives in the box's tooltip — no information is
        # removed, just re-prioritized (see _update_diff_box).
        # Placeholder text before the first tick — uses the display unit so it never flashes km/h
        # when the app is set to mph (the live readout re-renders on the first _update_diff_box).
        self.diff_box = QLabel(f"Δideal —    — {units.speed_label(self._speed_unit)}")
        self.diff_box.setObjectName("DiffBox")
        # LEFT-aligned, not centred: a centred QLabel that is squeezed clips at BOTH ends, so the
        # leading "Δ" was the first thing lost. Left alignment spends any residual clip on the tail.
        self.diff_box.setAlignment(Qt.AlignVCenter | Qt.AlignLeft)
        self.diff_box.setFont(theme.mono_font(theme.HERO, theme.W_SEMIBOLD))
        # …and the hero number never gives up characters at all: an explicit minimum sized to the
        # widest readout. On a QLabel setMinimumWidth SETS the layout minimum (qSmartMinSize takes an
        # explicit minimum over the size hint) — the same lever plots_label uses to volunteer itself
        # as the header's first casualty.
        self.diff_box.setMinimumWidth(_hero_min_width())
        self._diff_colour = None  # last applied Δ-value colour (per-tick recolor guard)
        # Last (speed, lap) the readout rendered — so toggling the reference re-renders without a tick.
        self._last_diff_speed: float | None = None
        self._last_diff_lap: int | None = None
        # Checked (default) → Δ-to-ideal leads; unchecked → Δ-to-best leads. A small labelled toggle
        # so the reference of the hero number is always explicit and one click to swap.
        self.ideal_readout_btn = QPushButton("vs ideal")
        self.ideal_readout_btn.setCheckable(True)
        self.ideal_readout_btn.setChecked(True)
        self.ideal_readout_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.ideal_readout_btn.setToolTip(
            "Hero readout reference: ON = Δ to your THEORETICAL IDEAL — the best you've driven at "
            "each point on track, stitched together into a synthetic curve (not a single drivable "
            "lap); OFF = Δ to your best single lap. The other number is always in the readout's "
            "tooltip.")
        self.ideal_readout_btn.toggled.connect(self._on_ideal_readout_toggled)

        # Chapter banner above the video; shown only for multi-chapter sessions.
        self.chapter_label = QLabel("")
        self.chapter_label.setObjectName("ChapterBanner")
        self.chapter_label.setAlignment(Qt.AlignCenter)
        self._seam_loading = False  # True while a chapter is reopening at a seam (banner hint)
        self._update_chapter_label(self.video.current_chapter())
        self.video.chapterChanged.connect(self._update_chapter_label)
        # Brief "loading next chapter…" hint on the banner during a seam reopen.
        self.video.seamLoading.connect(self._on_seam_loading)
        self.chapter_label.setVisible(self.video.is_multi)

        # VIDEO panel: a header BAR (title + right-aligned maximize button) so the panel-maximize
        # affordance sits on the header like the other three; the ⤢ *fullscreen-video* button stays
        # on the transport bar below (a DIFFERENT action — fill the SCREEN, not this window quadrant).
        video_label = QLabel("VIDEO")
        video_label.setProperty("role", "BarLabel")
        self._video_max_btn = self._maximize_button()
        video_header = self._header_bar(video_label, 1, self._video_max_btn)
        video_panel = self._headered(video_header, self.chapter_label, (self.video, 1))

        # LAP panel: ONE native tab bar (Laps · Corners · Stats · Coaching) over a QStackedWidget
        # — the page switcher IS the tab bar, and every page gets the panel's FULL height. This
        # replaced the checkable-button pseudo-tabs + the two min/max-capped under-table strips
        # (coaching + consistency), whose aggregate minimum could exceed the panel's allocation
        # (over-constrained: nothing was resizable and everything starved). Tab index == stack
        # index, 1:1. Digits 1-4 select tabs (window-level shortcuts).
        self.tab_bar = QTabBar()
        self.tab_bar.setDocumentMode(True)
        self.tab_bar.setExpanding(False)
        self.tab_bar.setDrawBase(False)
        # B2: on a narrow window the bar offers SCROLL BUTTONS rather than sliding a tab under the
        # ⛶ button. Elide must stay OFF: the QSS gives every tab `padding: 6px 10px`, which Qt
        # deducts a SECOND time when it derives SE_TabBarTabText from the tab rect, so the text rect
        # comes out a few px NARROWER than the label's own advance and ElideRight then elides all
        # four names unconditionally, at ANY width ("La…", "Corners ·…", "St…", "Coac…"). Scroll
        # buttons also drop minimumSizeHint to ~half the sizeHint, so pin a Minimum h-policy: a
        # layout may grow the bar but never squeeze it below the width its four names need.
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
        self.quality_badge = QLabel("ESTIMATED")
        self.quality_badge.setObjectName("QualityBadge")
        self.quality_badge.setVisible(False)
        # The coaching page: the top opportunities (corner · time lost · reason), full height —
        # no strip, no collapse, no height cap. A corner-row click ring-highlights its apex on
        # the map (the Jump-to-corner detail action stays in the modal dialog).
        self.opportunities = OpportunitiesPanel(self.session)
        self.opportunities.corner_clicked.connect(self.map.highlight_corner)
        # Stats CORNERS/BRAKING/STRAIGHTS rows → the same apex ring, but maximize-aware: restore
        # the grid first — a ring on a zero-width collapsed map helps no one (the N10 rule).
        self.stats_view.corner_clicked.connect(self._on_stats_corner_clicked)
        self.table_stack = QStackedWidget()
        self.table_stack.addWidget(self.table)          # index 0 — Laps (default)
        self.table_stack.addWidget(self.corner_table)   # index 1 — Corners
        self.table_stack.addWidget(self.stats_view)     # index 2 — Stats
        self.table_stack.addWidget(self.opportunities)  # index 3 — Coaching
        rows_h = self.table.table.verticalHeader().defaultSectionSize()
        self.table_stack.setMinimumHeight(rows_h * 5 + 56)  # ~5-row floor so a drag can't zero it
        self._table_max_btn = self._maximize_button()
        table_header = self._header_bar(self.tab_bar, self.quality_badge, 1,
                                        self._table_max_btn)
        table_panel = self._headered(table_header, (self.table_stack, 1))

        # MAP header: title (left) + the line-channel dropdown / snap / sector controls (right);
        # handlers live in MapView. The rainbow channel is a LABELLED combo (Off · Speed · Δ · Grip)
        # so Grip is discoverable, not a blind 4th cycle step.
        for b in (self.map.snap_btn, self.map.add_sector_btn, self.map.reset_sectors_btn):
            b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.map.rainbow_combo.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        # A QPushButton centre-clips its label rather than eliding it, so a squeezed map header
        # painted "ld sect" / "et sec" — non-words — with nothing to hover for the real meaning.
        # The charts column's honest minimum (see _measure_plots_budget) now keeps this bar wide
        # enough to paint all three in full; these tooltips are the belt to that braces, and the
        # DESTRUCTIVE one (Reset sectors wipes the user's lines) must never be a mystery word.
        # Set here rather than in map_view because this file already owns how these controls are
        # mounted and sized into the header.
        for b, tip in ((self.map.add_sector_btn,
                        "Add sector: drop another sector line on the map. Sector splits then "
                        "appear per lap in the Laps table (session-best splits in purple)."),
                       (self.map.reset_sectors_btn,
                        "Reset sectors: remove every sector line you have placed on this "
                        "recording. The start/finish line is not affected.")):
            b.setToolTip(tip)
        # (snap_btn and rainbow_combo already carry MapView's own tooltips — left alone.)
        map_label = QLabel("MAP")
        map_label.setProperty("role", "BarLabel")
        self._map_max_btn = self._maximize_button()
        map_header = self._header_bar(map_label, 1, self.map.rainbow_combo, self.map.snap_btn,
                                      self.map.add_sector_btn, self.map.reset_sectors_btn,
                                      self._map_max_btn)
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
        self.quality_banner.setToolTip(
            "Timing accuracy is degraded for this recording. On an older GoPro without GPS9 "
            "(Hero 5/6/7) the lap times come from the video clock, which runs ~0.1% fast and "
            "compresses every lap; and when many GPS fixes are rejected the positions are less "
            "accurate. The lap times are still shown (and de-emphasized), but treat them as "
            "estimates — they are most reliable on a GPS9 camera (Hero 9 and newer).")
        # Both tiers live in ONE strip container (a single bottom hairline; the two lines stack
        # tight), so the map sees a compact strip whether one or both concerns apply.
        self._trust_strip = QWidget()
        self._trust_strip.setObjectName("TrustStrip")
        strip_lay = QVBoxLayout(self._trust_strip)
        strip_lay.setContentsMargins(0, 0, 0, 0)
        strip_lay.setSpacing(0)
        strip_lay.addWidget(self.provisional_banner)
        strip_lay.addWidget(self.quality_banner)
        map_panel = self._headered(map_header, (self._trust_strip, 0), (self.map, 1))

        # CHARTS consolidated bar: section label (left) · the Δ/speed readout (centre) · the x-mode toggle (right).
        # The Δ half of this label tracks the chart's actual baseline (plots_view flips to
        # Δ-to-ideal when the best lap is selected alone) — a static "Δ TO BEST" would contradict
        # the axis. Text owned entirely by _fit_plots_header, driven by deltaBaselineChanged.
        self._plots_label = QLabel(_PLOTS_LABEL_BEST)
        plots_label = self._plots_label
        plots_label.setProperty("role", "BarLabel")
        # No layout minimum of its own: the fit pass hands it exactly the width of whichever of its
        # two lengths it chose, so the bar's derived minimum never pins the main splitter.
        plots_label.setMinimumWidth(0)
        # Which baseline the LOWER CHART is drawing (plots_view.deltaBaselineChanged). Not the same
        # thing as the hero readout's reference, which is the ideal_readout_btn's business — the bar
        # showing one while the chart draws the other is exactly what made it ambiguous.
        self._plots_baseline_ideal = False
        self.plots.x_mode_combo.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.plots.ideal_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.plots.brake_throttle_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        # The two chart toggles yield their TEXT (falling back to their icon, with the full label in
        # the tooltip + the accessible name) rather than being squeezed under it: a QPushButton
        # centre-clips, so the old 34 px floors painted "Brake/Thrott" and "Ideal la" with no
        # ellipsis and no way back to the meaning. Captured before the fit pass ever strips them —
        # re-reading text() later would latch "" as the label. Real floors are set from the
        # measured icon-only widths in _measure_plots_budget.
        self._plots_toggles = tuple(
            (btn, btn.text()) for btn in (self.plots.brake_throttle_btn, self.plots.ideal_btn))
        for btn, name in self._plots_toggles:
            btn.setAccessibleName(name)          # what the icon-only form no longer says out loud
        # The x-axis combo was the one control in the bar with NO tooltip, so "x: dista" was the
        # only cue for what it does. It is also the one control the fit pass never shrinks (its
        # whole meaning is the word), so it is pinned at its natural width below. Set here, not in
        # plots_view, because this file mounts and sizes the control.
        self.plots.x_mode_combo.setToolTip(
            "Chart x-axis: DISTANCE around the lap (the same corner lines up across laps) or TIME "
            "into the lap (matches the video clock).")
        self._plots_max_btn = self._maximize_button()
        plots_header = self._header_bar(plots_label, 1, (self.diff_box, 0),
                                        self.ideal_readout_btn, 1,
                                        self.plots.brake_throttle_btn, self.plots.ideal_btn,
                                        self.plots.x_mode_combo, self._plots_max_btn)
        # Watch the bar's width so the fit pass can re-spend its budget (and re-measure it when the
        # theme changes every font under it). None = not measured yet; see _measure_plots_budget.
        self._plots_budget: dict | None = None
        self._fitting = False
        self._plots_header_widget = plots_header
        plots_header.installEventFilter(self)
        plots_panel = self._headered(plots_header, (self.plots, 1))

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

        # Explicit column minimums: without them each column inherits its WIDEST header bar's
        # minimumSizeHint (the charts' hero Δ readout + buttons ≈ 980 px), which silently
        # pinned the main splitter — the lap panel could never be dragged wider than ~390 px
        # and a persisted layout could not restore.
        #
        # "Squeezed headers degrade gracefully" was the assumption, and it was false: 360 px is
        # below the hero readout's own 391 px floor, so at that minimum QHBoxLayout resolved the
        # shortfall by letting the children OVERLAP — the amber "vs ideal" chip painted straight
        # through the Δ number, and the controls past it painted "ake," / "Ide" / "x: (". The right
        # column's real floor is now the charts bar's own tightest-tier need, measured from the
        # painted fonts in _measure_plots_budget and applied by _fit_plots_header (it can only be
        # measured once the QSS has been polished onto these widgets, which is after this runs).
        # This 360 is the pre-measurement seed.
        left.setMinimumWidth(280)
        right.setMinimumWidth(360)

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setStretchFactor(0, 40)
        main.setStretchFactor(1, 60)
        # 515/917 sums to 1432 = the real usable width at the 1440 default window (1440 minus the
        # 8px splitter handle). The charts column's MEASURED minimum is 675 — it was 917 before the
        # header learned to hide its decorative label and shrink its controls — so 917 is a comfort
        # target with headroom, not a floor, and the lap panel takes the rest: enough width for
        # every Laps column with no horizontal scrollbar. The old [576, 864] was aspirational — the
        # right column's hidden minimum overrode it to ~[394, 1046] on every launch. The explicit
        # column minimums above let the USER trade either way; only the default has to be clip-free.
        main.setSizes([515, 917])
        # Those column minimums are only a floor if Qt is told to honour them. With Qt's default
        # childrenCollapsible, a drag that overshoots a minimum COLLAPSES the section to 0 instead
        # of clamping: past about +740 px one ordinary drag of the main handle deleted the whole
        # right column (MAP + CHARTS), the 400 ms debounce below persisted [1432, 0] to prefs.json,
        # and every relaunch reopened with those panels gone — recoverable only via the 8 px handle
        # now pinned against the window's own resize hot zone. Non-collapsible turns that same drag
        # into a polite clamp at [1072, 360]. (Maximize still needs a real 0 — see _collapse_sizes.)
        for splitter in (main, left, right):
            splitter.setChildrenCollapsible(False)
        # The user's persisted grid layout (a drag used to be lost on every reload, which read
        # as "the panels cannot be resized") is applied on FIRST SHOW, not here: before the
        # window sizes this widget, the splitters sit at tiny defaults and min-size clamping
        # would distort the restored ratios. See showEvent/_apply_grid_sizes.
        self._pending_grid_sizes = (self._initial_grid_sizes
                                    if self._initial_grid_sizes
                                    and len(self._initial_grid_sizes) == 3 else None)
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
        bounds: list[float] = []
        for lid in self.session.valid_lap_ids():
            w = self.session.lap_window(lid)
            if w is not None:
                bounds.extend(w)
        self.video.set_lap_ticks(bounds)
        # The slider + ←/→ seek pane A; in compare distance-lock the same move to pane B (the hook
        # no-ops outside compare, so wiring it once here is safe).
        self.video.set_compare_seek_fanout(self.compare.fanout_seek_b)
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
        """Catch a double-click on any registered panel header and toggle that panel's maximize,
        and a resize (or a re-style) of the charts header so it can re-spend its width budget.
        Everything else passes through untouched (return the base implementation)."""
        if obj is getattr(self, "_plots_header_widget", None):
            if event.type() == QEvent.Resize:
                self._fit_plots_header()
                return False      # observe only — the header still lays itself out normally
            if event.type() in (QEvent.StyleChange, QEvent.ApplicationFontChange):
                # Every number in the budget is font-derived and the QSS supplies the painted
                # fonts, so a theme applied AFTER this view was built invalidates the measurement.
                self._plots_budget = None
                self._fit_plots_header()
                return False
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
        self.stats_view.set_speed_unit(unit)
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
          * the hero Δ readout — re-style from the last moment.

        Cheap: a handful of in-place repaints / redraws, no session recompute."""
        self.table.refresh()
        self.corner_table.refresh()
        self.map.refresh_palette()
        self.opportunities.refresh()
        self.stats_view.refresh_palette()
        self.plots.refresh_palette()
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

    # --------------------------------------------------------- chapter banner
    def _update_chapter_label(self, chapter_index: int):
        """Banner text: the recording label plus, for a chaptered session, the current chapter.
        Suppressed while a seam reopen is in flight (the "loading next chapter…" hint owns the banner
        until the next chapter has presented, at which point _on_seam_loading(False) restores this)."""
        if getattr(self, "_seam_loading", False):
            return
        label = chapters.recording_label(self._paths)
        if self.video.is_multi:
            self.chapter_label.setText(
                f"{label}  —  {chapters.format_chapter(chapter_index, len(self.session.chapters))}")
        else:
            self.chapter_label.setText(label)

    def _on_seam_loading(self, loading: bool):
        """Show/clear a brief "loading next chapter…" hint on the chapter banner during a seam
        reopen. On (EndOfMedia → reopen): a clearly-styled hint so the momentary hitch reads as
        intentional. Off (next chapter loaded + resumed): restore the normal current-chapter text.
        chapterChanged fires during the switch, so it's gated on _seam_loading to not clobber this."""
        self._seam_loading = bool(loading)
        if loading:
            self.chapter_label.setText("loading next chapter…")
        else:
            self._update_chapter_label(self.video.current_chapter())

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
        is paused, so the seek decodes + presents without playing. Seed applied_t so the next tick
        sees it as already-applied. No-op when there's no valid best lap."""
        best = self.session.best_lap_id()
        if best is None:
            return
        window = self.session.lap_window(best)
        if window is None:
            return
        # Nudge past lap start (see _on_laps_selected) so the ms-quantized seek lands inside the lap.
        target = window[0] + theme.LAP_SEEK_NUDGE_S
        self.video.seek(target)          # paused decode → presents the best lap's start frame
        self._playback.latest_t = target
        self._playback.applied_t = target
        # Drive the playhead/readout/marker directly so the t=0 state matches the shown frame
        # without waiting for a positionChanged the seek may not emit synchronously.
        self._apply_position(target)

    def _on_user_select(self, ids):
        # A genuine user click in the lap table also jumps the video to that lap (F1).
        self._on_laps_selected(ids, seek=True)

    # --------------------------------------------------------- lap-panel tabs
    def _apply_table_mode(self, index: int):
        """One tab = one page: mirror the tab index onto the stack, point the Corners page at
        the current selection lazily on entry (an unvisited Corners page costs nothing), and
        let the window persist the choice."""
        self.table_stack.setCurrentIndex(index)
        if index == 1:
            self.corner_table.set_lap(self._corner_lap)
        self._update_table_header()
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
        self.map.highlight_corner(cid)

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

    def _measure_plots_budget(self) -> dict:
        """Measure the charts bar's width budget ONCE per style, from every item's FULL-text
        metrics. _fit_plots_header spends it (and derives the charts column's floor from it).

        Measured rather than assumed, and cached rather than re-derived, for three reasons:
          * the QSS supplies the painted fonts (the hero is styled in the mono stack at HERO/600),
            so every number here only exists after the widgets are polished;
          * the fit pass REWRITES the text it is measuring — deriving the tier from live sizeHints
            would feed the stripped text straight back into the layout (shorter text → smaller hint
            → narrower box → shorter text), a shrink loop;
          * the arithmetic the old fit pass did by hand was wrong in both directions. Qt gives a
            QBoxLayout spacing only BETWEEN non-empty items, and both `addStretch` spacers report
            empty, so the fixed chrome is margins + spacing×(visible items − 1) — 56 px for the six
            controls, 64 px with the label — while the old constant summed the ⛶ button's 45 px
            sizeHint against its setFixedSize of 26 and double-counted the label's spacing. It
            asked for 1040 px where the layout needs 1013 (measured: the label reappeared at a
            1041 px bar).
        Invalidated on a style/font change; see eventFilter."""
        row = self._plots_header_widget.layout()
        label = self._plots_label
        for w in (label, self.diff_box, self.ideal_readout_btn, self.plots.x_mode_combo,
                  self._plots_max_btn, *(b for b, _n in self._plots_toggles)):
            w.ensurePolished()

        def _with_text(widget, text):
            """sizeHint width the widget WOULD have at `text`, current text restored."""
            was = widget.text()
            widget.setText(text)
            px = widget.sizeHint().width()
            widget.setText(was)
            return px

        combo_px = self.plots.x_mode_combo.sizeHint().width()
        icons_px = {btn: _with_text(btn, "") for btn, _name in self._plots_toggles}
        text_px = {btn: _with_text(btn, name) for btn, name in self._plots_toggles}
        # Real floors, so the header's own minimumSizeHint stops lying: a toggle may shrink to its
        # icon and no further, and the combo never shrinks at all (its whole meaning is the word).
        for btn, floor in icons_px.items():
            btn.setMinimumWidth(floor)
        self.plots.x_mode_combo.setMinimumWidth(combo_px)
        margins = row.contentsMargins()
        budget = {
            "chrome": margins.left() + margins.right(),
            "spacing": row.spacing(),
            # Never yields: the live number's floor + its reference toggle + the ⛶ button (fixed).
            "fixed": (self.diff_box.minimumWidth() + self.ideal_readout_btn.sizeHint().width()
                      + _HDR_ICON_BTN.width()),
            "text": sum(text_px.values()) + combo_px,
            "icons": sum(icons_px.values()) + combo_px,
            "labels": {t: _with_text(label, t) for t in
                       (_PLOTS_LABEL_BEST, _PLOTS_LABEL_IDEAL, _PLOTS_CHIP_BEST, _PLOTS_CHIP_IDEAL)},
        }
        self._plots_budget = budget
        return budget

    @staticmethod
    def _plots_header_need(budget: dict, label_text: str, icons: bool) -> int:
        """Width the charts bar needs for one configuration: six controls, plus the label when it
        carries text, plus margins and the spacing Qt puts between the non-empty items."""
        items = 6 + (1 if label_text else 0)
        return (budget["chrome"] + budget["spacing"] * (items - 1) + budget["fixed"]
                + budget["icons" if icons else "text"]
                + (budget["labels"][label_text] if label_text else 0))

    def _fit_plots_header(self):
        """Spend the charts bar's width on MEANING first, then on control text.

        The bar carries two different Δ references — the hero readout's (its own toggle's business)
        and the one the LOWER CHART is actually drawing — so the label naming the chart's baseline
        is the item that keeps the panel unambiguous, not decoration. #122 made it the bar's first
        casualty; #125 then handed it that meaning without moving it up the yield order, so at every
        width the app ships at it lost to two button labels whose meaning is already in their
        tooltips. The order below inverts that, and the toggles finally fall back to their icon —
        the behaviour this file has claimed since #122 and never implemented — instead of being
        centre-clipped mid-word:

            SPEED · Δ TO BEST + full control text  →  + icon-only toggles  →  Δ BEST + icons  →
            icons alone (the column floor, so this last tier is only reachable by a drag)

        Pure function of the bar's width and the cached budget, so no tier can chase its own
        output; re-entrancy guarded because setting the text re-activates the layout."""
        header = getattr(self, "_plots_header_widget", None)
        label = getattr(self, "_plots_label", None)
        if header is None or label is None or self._fitting:
            return
        self._fitting = True
        try:
            budget = self._plots_budget or self._measure_plots_budget()
            # The charts COLUMN may never be dragged narrower than the bar's tightest tier: that is
            # what stops the header's children overlapping each other (and the hero readout
            # painting through them) at the column minimum. Applied here rather than at
            # measurement time because the bar's first resize can precede _layout_panels, and
            # re-checked every pass because a no-op setMinimumWidth is cheaper than being wrong.
            column = getattr(self, "_right_splitter", None)
            floor = self._plots_header_need(budget, "", icons=True)
            if column is not None and column.minimumWidth() != floor:
                column.setMinimumWidth(floor)
            ideal = self._plots_baseline_ideal
            full = _PLOTS_LABEL_IDEAL if ideal else _PLOTS_LABEL_BEST
            chip = _PLOTS_CHIP_IDEAL if ideal else _PLOTS_CHIP_BEST
            tiers = ((full, False), (full, True), (chip, True), ("", True))
            for text, icons in tiers:
                if header.width() >= self._plots_header_need(budget, text, icons):
                    break                       # else: fall through to the last (floor) tier
            label.setText(text)
            label.setVisible(bool(text))
            for btn, name in self._plots_toggles:
                btn.setText("" if icons else name)
        finally:
            self._fitting = False

    def _set_delta_baseline_label(self, ideal: bool):
        """Keep the charts bar honest about which baseline the lower chart is drawing (plots_view
        swaps to Δ-to-ideal when the best lap is selected alone). The fit pass owns the text —
        the ideal wording is longer, so the bar may have to re-spend its budget."""
        if getattr(self, "_plots_label", None) is not None:
            self._plots_baseline_ideal = bool(ideal)
            self._fit_plots_header()

    def _update_table_header(self):
        """The Corners tab always names WHICH lap its per-corner rows describe — directly on
        the tab text ("Corners · L7", 1-based like every lap number in the app; the old mode
        label showed the raw 0-based id). Defensive getattr for a partially-built view."""
        bar = getattr(self, "tab_bar", None)
        if bar is None:
            return
        lap = getattr(self, "_corner_lap", None)
        bar.setTabText(1, f"Corners · L{lap + 1}" if lap is not None else "Corners")
        # B1: the header bar was laid out BEFORE this load-time rename widened the tab bar's
        # size hint, and a QTabBar doesn't re-request layout on its own — so the last tab
        # ("Coaching") shipped clipped to "Coach" until the user happened to click a tab.
        bar.updateGeometry()
        header = bar.parentWidget()
        if header is not None and header.layout() is not None:
            header.layout().activate()

    def _on_laps_selected(self, ids, seek=False):
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
        self.plots.set_playhead_time(t)
        self._apply_readout(t)

    def _apply_readout(self, t: float):
        # Resolve lap + trace index ONCE per tick and reuse below.
        lap_id = self.session.lap_at_time(t)   # F3: which lap is on the video
        i = self.session.index_at_time(t)      # nearest trace sample (marker + speed)
        self.map.set_marker_index(i)           # F3: red marker (same point set_playhead_time chose)
        self._follow_current_lap(lap_id, t)  # charts auto-follow the playhead's lap (vs best)
        self.table.set_current_lap(lap_id)
        self.map.set_current_lap(lap_id)  # highlight the current lap's trace on the map
        sp = float(self.session.tv[i]) if i is not None else None  # F2: speed km/h at that index
        # C6: under-video strip = timecode (+chapter) only; the live Δ/speed/lap lives once in the
        # hero #DiffBox.
        self.video.set_readout(self._transport_readout(t))
        self._update_diff_box(t, sp, lap_id)
        # Gate the g_at_time lookup on the overlay being visible. In compare the pair pins each
        # pane's g-meter lap scope, so skip the per-tick primary pin to keep one driver.
        if not self._comparing():
            self.video.set_gmeter_lap(lap_id)
        if self.video.is_gmeter_visible():
            self.video.set_g(self.session.g_at_time(t))

    def _transport_readout(self, t: float) -> str:
        """The under-video TIMECODE strip (C6): the media position, plus the current chapter when
        the recording spans several (the one piece of video-specific context not surfaced anywhere
        else). Deliberately does NOT echo speed / Δ / lap — those live in the hero #DiffBox, the
        single source of the live moment."""
        chs = self.session.chapters
        if chs is not None and chs.is_multi:
            return f"{fmt_time(t)}   ·   {chapters.format_chapter(chs.chapter_at(t), len(chs))}"
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

    def _on_ideal_readout_toggled(self, _on: bool):
        """Flip the hero readout between leading with Δ-to-ideal (checked) and Δ-to-best (unchecked),
        then re-render it for the current moment so the swap is immediate (not deferred to the next
        tick)."""
        self._update_diff_box(self._playback.applied_t, self._last_diff_speed,
                              self._last_diff_lap)

    def _update_diff_box(self, t: float, sp: float | None, lap_id: int | None):
        """Refresh the hero Δ/speed box for the current moment. By default it LEADS with Δ-to-IDEAL
        (the moat number — how far off the driver's own achievable lap they are, right here) via
        theme.format_ideal_readout; the ideal_readout_btn flips it to lead with Δ-to-best (the
        export overlay's theme.format_delta_speed). The number NOT leading is shown in the box's
        tooltip, so both are always one read/hover away (no information removed, just re-prioritized).

        Both deltas are cheap per-tick scalars on the already-resolved lap: delta_at_lap (Δ-to-best)
        and delta_to_ideal_at (Δ-to-ideal, grid-based + memoized envelope), so the ~30 Hz path adds
        only two O(log n) np.interps."""
        # Stash the moment so a toggle can re-render without a tick (see _on_ideal_readout_toggled).
        self._last_diff_speed, self._last_diff_lap = sp, lap_id
        d_best = self.session.delta_at_lap(lap_id, t) if lap_id is not None else None
        d_ideal = self.session.delta_to_ideal_at(lap_id, t) if lap_id is not None else None
        if self.ideal_readout_btn.isChecked():
            text, sem_colour = theme.format_ideal_readout(d_ideal, sp, lap_id, self._speed_unit)
            tip = f"Δ to your best lap here: {theme.format_delta_run(d_best)}"
            # IA-03: this readout is the largest text in the window, and on the BEST lap it is a
            # structural null — the ideal is the best-of-each-point stitched from the driver's own
            # laps, so the lap that formed most of it can barely differ from it (measured over the
            # whole of one real best lap: max |Δideal| = 0.08 s). The app opens with the playhead
            # in exactly that lap. Rather than let 16 px of "+0.00" read as an achievement, say
            # WHY it is zero — appended, never replacing the Δ-to-best number the box promises to
            # keep one hover away.
            if lap_id is not None and lap_id == self.session.best_lap_id():
                tip += ("\nThis IS your best lap, and the ideal is stitched from your own best "
                        "sections, so Δideal stays near zero here by construction — pick another "
                        "lap, or switch this readout to Δ-to-best, for a number that moves.")
        else:
            text, sem_colour = theme.format_delta_speed(d_best, sp, lap_id, self._speed_unit)
            tip = f"Δ to your IDEAL achievable lap here: Δideal {theme.format_delta_value(d_ideal)}"
        colour = sem_colour or theme.C.text
        self.diff_box.setText(text)
        self.diff_box.setToolTip(tip)
        # Only restyle when the colour changes (avoids a per-tick stylesheet re-layout).
        if colour != getattr(self, "_diff_colour", None):
            self._diff_colour = colour
            self.diff_box.setStyleSheet(f"QLabel#DiffBox {{ color: {colour}; }}")

    # ------------------------------------------------------------- compare-state access
    def _comparing(self) -> bool:
        """True iff compare mode is on. Defensive: tolerates the controller not yet built (the
        unit-test CentralView.__new__ path)."""
        compare = getattr(self, "compare", None)
        return compare is not None and compare.active

    # ------------------------------------------------------------- driving channels (F5)
    def _refresh_sector_lines(self, mode: str | None = None):
        """F2: push the sector boundary positions (start/finish + each sector line) to the charts
        for the current axis mode. Computed via session (the s×best_distance / time-into-lap
        axis), so plots_view stays pacer-free. Called on launch, after a sector edit, and when
        the dist/time mode flips (positions' units change)."""
        mode = mode or self.plots.axis_mode()
        self.plots.set_sector_lines(self.session.sector_plot_positions(mode))
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
        self.table.refresh()
        # set_corners re-pushes the corner labels AND clears any stale highlight, so it runs after
        # refresh_overlays and before the corner consumers below.
        self.map.refresh_overlays()
        self.map.set_corners(self.session.corners.corner_map_markers())
        self.corner_table.refresh()
        # The persistent coaching front-door: recompute the top-3 opportunities (the clean-lap set /
        # corner losses shift on a re-segmentation; recomputed per build, never on the 30 Hz tick).
        self.opportunities.refresh()
        self.stats_view.refresh()
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
        # The provisional-timing banner tracks the trust state (a re-segment can confirm it).
        self.refresh_timing_trust()

    def refresh_timing_trust(self):
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
        recording — the common good case — so the map reads identically to today). Run from the
        rebuild seam. The lap table's muting + the map's dashed cue refresh on their own rebuilds.
        Defensive: a CentralView.__new__'d for a unit test drives the seam without building the
        banner widgets, so the session reads stay behind the widget-presence guard (as before)."""
        strip = getattr(self, "_trust_strip", None)
        if strip is None:  # partially-built view (no banner chrome) — nothing to refresh
            return
        provisional = not self.session.timing_verified
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
        badge.setText("ESTIMATED" if quality.media_clock else "GPS LOW")
        badge.setToolTip(quality.detail())

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
        if self.session.valid_lap_ids():
            self.session.push_timing_history()  # pre-edit state, so a bad drag is undoable
        if self._comparing():
            self.video.set_compare_enabled(False)  # un-checks -> compareToggled(False) -> exit
        self.session.set_timing_lines(start, sectors)
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
            print("studio: timing lines NOT saved — that placement leaves no complete lap; "
                  "the last saved lines are unchanged (Edit ▸ Undo reverts the edit)", flush=True)
            return
        start, sectors = self.session.timing_lines_latlon()
        try:
            sidecar.save(path, self.session.track_name, start, sectors,
                         confirmed=self.session.timing_user_confirmed)
        except OSError as exc:
            print(f"studio: could not write timing-line sidecar {path}: {exc}", flush=True)
            return
        print(f"studio: timing lines saved to {os.path.basename(path)}", flush=True)

    def undo_timing_lines(self) -> bool:
        """Undo the last timing-line edit (Edit ▸ Undo / Cmd+Z). Restores the prior lines through
        Session.undo_timing_lines (which replays them through the same re-segment/apply path, so
        the segmentation + PB/session-best baseline recompute identically), then re-draws the map
        handles, rebuilds the derived views, and re-persists the restored lines to the sidecar.
        No-op (returns False) when there's no prior edit to undo. Compare mode is torn down first
        (a re-segment shifts lap ids, invalidating any pinned pair) — mirrors _on_lines."""
        if not self.session.undo_timing_lines():
            return False
        if self._comparing():
            self.video.set_compare_enabled(False)
        # The session lines are already restored; pull the map's draggable handles onto them WITHOUT
        # re-emitting timing_lines_changed (that would re-push the undone state onto the stack).
        self.map.reload_timing_lines()
        self.rebuild_derived_views(reselect=True)
        self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
        self._save_sidecar()  # persist the restored lines so a reload sees the undone state
        self.timingEdited.emit()
        return True
