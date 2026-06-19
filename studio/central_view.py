"""CentralView: the session-scoped central widget for a single loaded recording.

F7 atomic-swap ownership move. Before this object, StudioWindow used a SPLIT-LIFETIME model: the
session-scoped panels (video/map/plots/table/corner_table/consistency/diff_box/chapter banner), the
compare + scrub controllers and the F5 PlaybackState were torn down and rebuilt INLINE on every
reload (the old ``_build_ui``), while the persistent chrome (menu actions, the ~30 Hz tick timer,
the statusbar ref-chip, the window shortcuts) lived on the window. That split forced scattered
defensive ``getattr(self, "x", None)`` guards on the window and a "resolve ``self.video``
dynamically" idiom, because the window held references that the rebuild could invalidate at any
time.

CentralView ends the split: it OWNS every session-scoped widget + the two controllers + the
PlaybackState + the per-frame ``tick()`` work, ALL built atomically in ``__init__``. StudioWindow
holds ONE ``self.view``, builds a fresh CentralView per load and ``setCentralWidget(self.view)``
atomically. There is no in-place teardown/rebuild any more — a reload constructs a NEW CentralView
and the old one is disposed + dropped as a unit, so a window reference into the view can never
become stale in the middle of a rebuild. The persistent chrome talks to session-scoped widgets
THROUGH ``self.view`` (e.g. ``self.view.video``, ``self.view.session``), which is the single swap
point that replaced the scattered guards.

session / _paths split (the lowest-churn correct cut): StudioWindow keeps the LOAD ORCHESTRATION —
``Session.load``, the sidecar restore, the notices, ``_paths``, the title, the library upsert — and
the persistent chrome (menus, shortcuts, the tick QTimer, the ref-chip). It hands the loaded
``session`` (plus the recording ``paths`` for the chapter banner and the ``sidecar_path`` for the
timing-line save) into the CentralView constructor. ``self.view.session`` is the read alias the
chrome resolves through. The tick TIMER stays on the window (persistent, created once, reused); the
window's ``_tick`` simply delegates to ``self.view.tick()``, the per-frame drain/scrub/apply/compare
work that moved here.

Build order (atomic, in ``__init__`` — byte-identical to the old ``_build_ui`` sequence):
  1) ``_construct_panels``  — every panel widget + its header strip + the table stack;
  2) ``_layout_panels``     — the 2x2 nested splitter grid + maximize filters + the QWidget layout;
  3) ``_wire_signals``      — the cross-panel signal/slot wiring + the PlaybackState;
  4) ``_build_controllers`` — the compare + scrub controllers, cross-injected;
  then the derived-views seed (``rebuild_derived_views(reselect=True)``), the poster seek, and
  ``set_consistency_visible`` to re-sync the panel to the window-held user choice. The ref-chip /
  "Load full recording" enablement / reference-status sync stay on the window (chrome) and run after
  the swap.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import chapters, sidecar, theme
from .compare_controller import CompareController
from .consistency_panel import ConsistencyPanel
from .lap_table import CornerTable, LapTable
from .map_view import MapView
from .playback_state import PlaybackState
from .plots_view import PlotsView
from .scrub_controller import ScrubController
from .session import fmt_time
from .video_view import VideoView


class CentralView(QWidget):
    """The session-scoped central widget for ONE loaded recording (see the module docstring).
    Built atomically per load by StudioWindow; owns the panels, the compare/scrub controllers, the
    shared PlaybackState and the per-frame ``tick()``. The persistent chrome on the window reaches
    every session-scoped widget through ``window.view`` — this object is the single swap point."""

    # Re-emitted up to the window so the persistent statusbar / menus can react WITHOUT the window
    # reaching into the view's internals. Currently the window drives those directly through
    # ``self.view`` (the chip/menu sync stays on the window after the swap), so no consumer is wired
    # yet; the signal exists as the clean seam if a future chrome reaction needs it.
    referenceChanged = Signal()

    def __init__(self, session, paths: list[str], sidecar_path: str | None,
                 consistency_visible: bool, parent: QWidget | None = None):
        super().__init__(parent)
        # session / _paths split: the view holds a READ ALIAS of the window-owned session + the
        # recording paths (banner text) + the sidecar path (timing-line save). The window keeps the
        # canonical copies + the load orchestration; the view never reassigns `session`.
        self.session = session
        self._paths = list(paths)
        self._sidecar_path = sidecar_path
        # F6: the consistency panel's visibility is a persistent USER choice held on the window (it
        # survives a reload). The window passes it in; we apply it to the freshly-built panel below
        # and the window flips it later via set_consistency_visible.
        self._consistency_visible = consistency_visible

        # Atomic build — the exact ordering the old StudioWindow._build_ui ran, now wholly inside the
        # constructor so a fresh CentralView is fully wired before the window swaps it in. There is no
        # old-video teardown here (a NEW view has no old video); the window disposes the OUTGOING view
        # before constructing this one (see StudioWindow / CentralView.dispose).
        # 1) build every panel widget + its header strip + the table stack; 2) assemble the splitter
        # grid + maximize filters + the layout; 3) wire the cross-panel signals + PlaybackState; 4)
        # construct + cross-inject the compare/scrub controllers.
        self._construct_panels()
        self._layout_panels()
        self._wire_signals()
        self._build_controllers()

        # --- seed default state (must run after the panels + controllers exist) ---
        # Build the full set of session-derived views through the shared seam (same sequence a
        # re-segmentation / reference change uses): selects the two fastest laps, draws the map
        # overlays + corners, the corner/consistency panels, the driving channels, and any sector
        # guides present on launch (none by default). Replaces the old partial inline set
        # (_select_default + _refresh_sector_lines).
        self.rebuild_derived_views(reselect=True)
        # Poster frame: seek the PRIMARY pane a hair into the best lap WHILE PAUSED so the (largest)
        # video quadrant shows a real frame at launch instead of a black void, and the map marker /
        # charts / readout are all populated and consistent with that frame. A paused seek decodes
        # and presents the frame without playing audio. Done after the rebuild above so the chart
        # selection is already in place. Skipped cleanly when there's no valid lap (poster_seek
        # checks best_lap_id()), so a 0-lap session still launches.
        self._poster_seek()
        # F6 default-hidden re-sync: apply the window-held consistency choice to the fresh panel. The
        # panel was built shown; this hides it (the default) or shows + refreshes it to match the
        # user's persisted toggle — identical to the old _build_ui's setVisible + the toggle handler.
        self._apply_consistency_visible(refresh=False)

    # ------------------------------------------------------------------ lifecycle
    def dispose(self):
        """Tear the session-scoped resources down before this view is dropped on a reload: stop the
        decoder(s) and close the g-meter overlay window (VideoView.stop_all disposes both panes +
        the overlay). Called by StudioWindow on the OUTGOING view right BEFORE it builds + swaps in
        the new one, preserving the old reload's "old video stop_all + g-meter overlay close before
        the central widget is replaced" ordering. Defensive: a partially-built view (shouldn't
        happen) without a video is simply skipped."""
        video = getattr(self, "video", None)
        if video is not None:
            video.stop_all()

    # --------------------------------------------------------- panel container helpers
    @staticmethod
    def _panel(title: str, *contents) -> QWidget:
        """Wrap panel content under a flush "section header" label (the `.PanelHeader` QSS role:
        small uppercase dimmed strip). Each `contents` entry is either a widget (added with no
        stretch) or a `(widget, stretch)` tuple. Purely a header + tight container — it changes
        no behaviour and adds no per-tick cost."""
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
        """A flush header strip styled like `.PanelHeader` (surface bg, bottom hairline) that holds
        WIDGETS rather than plain text — used for the map header (title + right-aligned sector
        buttons) and the charts' consolidated bar (label · readout · toggle). Each `segments` entry
        is a widget, an int stretch (inserts `addStretch(n)`), or a `(widget, stretch)` tuple. The
        bar carries the `PanelHeader` role so its background/border/typography match the plain
        headers; child widgets keep their own QSS (buttons, combo, the #DiffBox)."""
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

    @staticmethod
    def _headered(header: QWidget, *contents) -> QWidget:
        """Stack a custom `header` widget above panel `contents` (same tight container as `_panel`
        but with a widget header instead of a text label). Each `contents` entry is a widget or a
        `(widget, stretch)` tuple."""
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
        """Build every panel widget (video/map/plots/table/corner_table/consistency/diff_box +
        the chapter banner), relocate the map/charts/table chrome into WIDGET header strips, and
        assemble the four panel containers (self._video_panel/_table_panel/_map_panel/_plots_panel)
        that _layout_panels then drops into the splitter grid. Sets every panel-level self.*
        attribute the rest of the view reads. Pure construction — no layout, no cross-panel
        signal wiring (those are _layout_panels / _wire_signals)."""
        # The VideoView is driven by the session's ChapterMap so the slider spans the whole
        # session and playback switches sources / auto-advances across chapters.
        self.video = VideoView(self.session.chapters or self.session.video_path)
        # g-meter overlay: label its source (accl/gps) and only offer the toggle when a g signal
        # was actually computed (IMU present). The overlay itself is pacer-free — g comes from
        # session via self.video.set_g at the tick.
        self.video.set_gmeter_source(self.session.gmeter_source())
        self.video.gmeter_btn.setEnabled(self.session.has_gmeter)
        if not self.session.has_gmeter:
            self.video.gmeter_btn.setToolTip("No accelerometer data in this recording")
        self.map = MapView(self.session)
        # Corner labels on the map (F-corner): pushed from here so MapView stays a pure
        # consumer of (label, x, y, direction) tuples — the model lives in session/corners.
        self.map.set_corners(self.session.corner_map_markers())
        self.plots = PlotsView(self.session)
        self.table = LapTable(self.session)
        # Corners mode (F-corner): a second table stacked under the same panel — rows = the
        # detected corners for the selected lap (time, Δ vs best, apex/entry/exit speeds).
        # The header toggle below flips the stack; Laps mode itself is untouched.
        self.corner_table = CornerTable(self.session)
        self._corner_lap: int | None = None  # the lap the Corners view describes

        # Always-on Δ/speed readout for the CURRENT playback/scrub moment (Δ-to-best is the
        # priority). Owned here (values come from session); it's the EMPHASIZED centre element of
        # the charts' consolidated header bar (built below), so it never overlaps the curves.
        # plots_view stays pacer-free — it knows nothing about this readout. Base styling
        # (mono/tabular ~22px, transparent on the bar's surface) comes from the global QSS via
        # objectName "DiffBox"; only the Δ-value COLOUR is driven per-tick.
        self.diff_box = QLabel("Δ —    — km/h")
        self.diff_box.setObjectName("DiffBox")
        self.diff_box.setAlignment(Qt.AlignCenter)
        self.diff_box.setFont(theme.mono_font(theme.HERO, theme.W_SEMIBOLD))
        self._diff_colour = None  # last applied Δ-value colour (per-tick recolor guard)

        # Multi-chapter status banner above the video: shows the recording label and, for a
        # chaptered session, which chapter is currently playing (updated via chapterChanged).
        # Slim themed strip via objectName "ChapterBanner" (surface bg, dimmed, accent left rule).
        self.chapter_label = QLabel("")
        self.chapter_label.setObjectName("ChapterBanner")
        self.chapter_label.setAlignment(Qt.AlignCenter)
        self._seam_loading = False  # True while a chapter is reopening at a seam (banner hint)
        self._update_chapter_label(self.video.current_chapter())
        self.video.chapterChanged.connect(self._update_chapter_label)
        # Brief, clearly-styled "loading next chapter…" hint on the banner during a seam reopen, so a
        # momentary hitch reads as intentional rather than a freeze. Reuses the ChapterBanner strip.
        self.video.seamLoading.connect(self._on_seam_loading)
        # Only show the banner for a real (>1 chapter) chaptered session; a single file is
        # exactly as before (no banner clutter).
        self.chapter_label.setVisible(self.video.is_multi)

        # Each panel gets a flush "section header" strip (uppercase, dimmed) above its content.
        # The video panel stacks the chapter banner under its plain text header; the table is
        # wrapped fresh. The MAP and PLOTS panels use WIDGET header bars (below) so the right
        # column's chrome collapses into the headers instead of full-width rows between the panels.
        video_panel = self._panel("VIDEO", self.chapter_label, (self.video, 1))

        # TABLE panel: a widget header bar (like the map's) holding the LAPS/CORNERS mode
        # label + the Corners toggle, above a QStackedWidget of the two tables. Laps mode
        # (index 0, the default) is the untouched LapTable; toggling to Corners and back
        # leaves it byte-identical (the stack only changes which page is visible).
        self._table_label = QLabel("LAPS")
        self._table_label.setProperty("role", "BarLabel")
        self.corners_btn = QPushButton("Corners")
        self.corners_btn.setCheckable(True)
        self.corners_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.corners_btn.setToolTip(
            "Per-corner analysis of the selected lap: time-in-corner, Δ vs the best lap, "
            "apex/entry/exit speeds. Corners are detected from the track's own curvature.")
        self.corners_btn.toggled.connect(self._on_corners_toggled)
        self.table_stack = QStackedWidget()
        self.table_stack.addWidget(self.table)         # index 0 — Laps (default)
        self.table_stack.addWidget(self.corner_table)  # index 1 — Corners
        table_header = self._header_bar(self._table_label, 1, self.corners_btn)
        # F6: the compact collapsible CONSISTENCY strip under the lap table — lap-time trend
        # sparkline + the top-5 inconsistent corners (ranked by σ × median loss). Clicking a
        # corner row ring-highlights its apex on the map and does NOTHING else (read-only;
        # no lap selection / seek). It owns its own header (with the collapse chevron), so
        # it mounts as one widget below the table stack.
        self.consistency = ConsistencyPanel(self.session)
        self.consistency.corner_clicked.connect(self.map.highlight_corner)
        # F6 default-hidden: the consistency strip is OFF by default (a real hide, not just
        # collapsed) so the lap table owns the whole table panel — applied via
        # _apply_consistency_visible AFTER the build (the panel is built shown). Hidden via
        # setVisible(False), which drops it from the table panel's layout entirely (the table stack
        # keeps all the height), so the lap-table layout is intact.
        # Stop Consistency from crushing the lap table (the broken state: enabling it left ~1 lap
        # row). Two guards: (1) the table stack gets a MIN HEIGHT of ~5 lap rows + header so it can
        # never be squeezed below a usable size; (2) the table stack + the consistency strip share a
        # VERTICAL SPLITTER (not a fixed-height child) so the strip is resizable — enabling it
        # shrinks/scrolls the (max-capped, min-bounded) consistency strip, never the table. The
        # splitter's stretch heavily favours the table; the consistency section keeps its compact
        # default. A collapsed/hidden strip gives ALL the height back to the table, as before.
        rows_h = self.table.table.verticalHeader().defaultSectionSize()
        self.table_stack.setMinimumHeight(rows_h * 5 + 56)  # ~5 rows + column header + footer
        table_body = QSplitter(Qt.Vertical)
        table_body.addWidget(self.table_stack)
        table_body.addWidget(self.consistency)
        table_body.setStretchFactor(0, 1)   # the lap table takes any extra height
        table_body.setStretchFactor(1, 0)   # the consistency strip keeps its compact size
        table_body.setCollapsible(0, False)  # never collapse the lap table away
        table_panel = self._headered(table_header, (table_body, 1))

        # MAP header: title (left) + the rainbow-channel cycle, snap toggle and sector buttons
        # (right-aligned, compact) — moved OFF the full-width row that used to sit between the
        # map and the charts. Their handlers/signal wiring (re-segmentation, opt-in snap, the F3
        # rainbow rebuild) live in MapView; this is just the mount point.
        for b in (self.map.rainbow_btn, self.map.snap_btn,
                  self.map.add_sector_btn, self.map.reset_sectors_btn):
            b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        map_label = QLabel("MAP")
        map_label.setProperty("role", "BarLabel")
        map_header = self._header_bar(map_label, 1, self.map.rainbow_btn, self.map.snap_btn,
                                      self.map.add_sector_btn, self.map.reset_sectors_btn)
        map_panel = self._headered(map_header, (self.map, 1))

        # CHARTS consolidated bar (replaces the old separate panel-header row + full-width DiffBox
        # row + the combo's own row): section label (left) · the emphasized Δ/speed readout
        # (centre) · the x-mode toggle relocated from plots_view (right). The toggle keeps its
        # modeChanged wiring; the readout keeps its per-tick recolor. ~2 rows of height reclaimed.
        plots_label = QLabel("SPEED · Δ TO BEST")
        plots_label.setProperty("role", "BarLabel")
        self.plots.x_mode_combo.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        plots_header = self._header_bar(plots_label, 1, (self.diff_box, 0), 1, self.plots.x_mode_combo)
        plots_panel = self._headered(plots_header, (self.plots, 1))

        # Stash the four panel containers on the view: _layout_panels drops them into the splitter
        # grid and the maximize routing maps a header back to its panel + outer column.
        self._video_panel = video_panel
        self._table_panel = table_panel
        self._map_panel = map_panel
        self._plots_panel = plots_panel

    # ----------------------------------------------------- build phase 2: layout
    def _layout_panels(self):
        """Assemble the 2x2 nested splitter grid from the panels _construct_panels built: a left
        column (video over table), a right column (map over charts), and the main horizontal split,
        each with its Phase 2 rebalanced sizes/stretch. Installs the double-click-to-maximize header
        filters, seeds the maximize state, and sets THIS widget's layout to the main splitter. Keeps
        the splitter refs (self._main_splitter/_left_splitter/_right_splitter) the maximize code
        reads."""
        video_panel = self._video_panel
        table_panel = self._table_panel
        map_panel = self._map_panel
        plots_panel = self._plots_panel

        # Rebalanced defaults — the #1 layout complaint was the inverted space/value ratio: the
        # single VIDEO frame (the least information-dense panel) got the most area while the
        # analytical charts (the product's core) were cramped. We give the analytical core room and
        # leave the video clearly usable but not dominant. Left column = video over table, split
        # ~52/48 so enabling Consistency has a healthy lap table to share (the video used to swallow
        # ~66%); stretch factors keep that ratio on a vertical resize.
        left = QSplitter(Qt.Vertical)
        left.addWidget(video_panel)
        left.addWidget(table_panel)
        left.setStretchFactor(0, 52)
        left.setStretchFactor(1, 48)
        left.setSizes([440, 400])

        # Right column: the charts (the analytical core) get the MAJORITY — map ~38% / charts ~62%.
        # The map only needs enough to read the track clearly; every extra pixel goes to the curves.
        right = QSplitter(Qt.Vertical)
        right.addWidget(map_panel)
        right.addWidget(plots_panel)
        right.setStretchFactor(0, 38)
        right.setStretchFactor(1, 62)
        right.setSizes([320, 520])

        # Main horizontal split: hand the analytical RIGHT column the larger share — left (video +
        # table) ~40% / right (map + charts) ~60% — flipping the old video-biased 46/54. Stretch
        # factors keep the ratio on a horizontal resize (so the charts, not the video, take the
        # growth). The video stays comfortably readable at ~40% of a 1440-wide window.
        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setStretchFactor(0, 40)
        main.setStretchFactor(1, 60)
        main.setSizes([576, 864])
        # The main splitter IS this widget's body. The old StudioWindow.setCentralWidget(main) is
        # replaced by setCentralWidget(self.view) on the window; here the grid is the view's layout.
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(main)

        # Focus / maximize: double-clicking ANY panel's header strip toggles that quadrant to fill
        # the window (collapse the other splitter sections) and double-clicking again restores the
        # grid — "focus charts" / "focus video" for free, no new menus. The four panels + the
        # splitters they live in are stashed on the view so the handler can map a header back to its
        # panel + outer column. Each panel's header is the FIRST child added in _panel/_headered; we
        # filter double-clicks on it. A fresh view always starts un-maximized with its default sizes.
        self._main_splitter = main
        self._left_splitter = left
        self._right_splitter = right
        self._maximized_panel = None          # the currently-maximized panel, or None
        self._saved_splitter_sizes = None     # (main, left, right) sizes captured at maximize
        # Fresh routing map for THIS view's headers; each _install_header_dblclick call adds one entry.
        self._header_routes = {}
        self._install_header_dblclick(video_panel, left, main)
        self._install_header_dblclick(table_panel, left, main)
        self._install_header_dblclick(map_panel, right, main)
        self._install_header_dblclick(plots_panel, right, main)

    # ----------------------------------------------------- build phase 3: signals
    def _wire_signals(self):
        """All the cross-panel signal/slot connections + the per-tick scaffolding they feed. Run
        after the panels exist and the grid is mounted; the compare/scrub controllers (which also
        wire signals) are built next in _build_controllers.

        The ~30 Hz tick TIMER is NOT here: it lives on the persistent StudioWindow (created once,
        reused across reloads) and delegates to self.tick(). A fresh CentralView gets a fresh
        PlaybackState (the whole view is rebuilt atomically, so there is no live controller holding
        an old instance to keep valid — that reuse was an artefact of the in-place rebuild)."""
        # --- cross-panel wiring ---
        # positionChanged fires in the video decode/present path; it must do almost nothing
        # (just record the latest time). A steady ~30 Hz timer applies the map/plot/readout
        # update off that path, so heavy repaints never starve frame presentation.
        #
        # F5: the per-frame playback / scrub / auto-follow cursor (latest_t, applied_t, followed_lap)
        # lives in ONE shared PlaybackState object instead of three loose attributes + a callback web
        # into the controllers. Construct it here (before _build_controllers, which hands the SAME
        # instance to both controllers); they read/write it directly. A fresh view always builds a
        # fresh PlaybackState at the default cursor (latest_t=0, applied_t/followed_lap=None).
        self._playback = PlaybackState()
        self.video.positionChanged.connect(self._on_position)
        # The map marker drag no longer emits a seek per mouse-move; the app's tick drains a
        # coalesced ONE-per-tick seek via map.take_marker_seek() (see tick()).
        self.map.timing_lines_changed.connect(self._on_lines)
        self.table.laps_selected.connect(self._on_user_select)

    # ----------------------------------------------------- build phase 4: controllers
    def _build_controllers(self):
        """Construct the CompareController + ScrubController, hand each the SHARED PlaybackState
        (built in _wire_signals, above) + cross-inject them (each queries the other), and wire their
        compare/scrub signals + per-tick feeds. Also wires the sector-line modeChanged hook, which the
        original block ran here — AFTER the controllers — so the order is preserved exactly. Run last
        in the wiring phase, before the derived-views seed."""
        # --- compare videos (Phase B) + plot-cursor scrub: the two heavy behavioural clusters ---
        # The compare-mode per-tick + enter/exit orchestration and the lap-scoped scrub coalescing
        # live in two injected, Qt-light, unit-testable collaborators (compare_controller.py /
        # scrub_controller.py). CentralView constructs + wires them and forwards signals + the
        # per-tick branches; the controllers OWN their state. Behaviour is byte-identical.
        #
        # Compare: two equal side-by-side video panes behind the explicit "Compare videos" toggle
        # (OFF by default, enabled only with >=2 valid laps). The PRIMARY (left) pane keeps driving
        # ALL telemetry exactly as today; the SECONDARY (right) pane is video-only. While compare
        # is on, auto-follow's lap re-point is SUSPENDED (the controller freezes followed_lap on the
        # SHARED PlaybackState) so the pinned panes/charts don't thrash across lap boundaries.
        self.compare = CompareController(
            self.session, self.video, self.plots, self.table,
            playback=self._playback,  # F5: the shared cursor (reads applied_t, writes followed_lap)
            select_default=self._select_default,
            map_view=self.map,  # F4: the compare ghost (lap B's kart) on the track map
            # F5: refresh the brake glyphs whenever the compared pair changes (both laps in
            # compare; the current lap on exit) — reuses the compare machinery, no new sync.
            on_pair_changed=self._refresh_driving_channels,
        )
        # Scrub: a fine, lap-scoped scrubber (the full-video slider stays). Dragging either plot
        # cursor seeks the video WITHIN the current lap; plots_view emits the raw plot-x + which
        # axis it came from and the controller converts it (via session) to a clamped media time,
        # throttles the seek to <=1 per tick, pauses while dragging and resumes iff it was playing.
        # In compare mode the drag is distance-locked across both panes.
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
        # C8: feed the scrub slider its MoTeC-style lap-ruler ticks — every valid lap's start/end
        # position on the GLOBAL clock, so the transport bar shows at a glance where each lap sits in
        # the session. lap_window is (start, start+lap_time); the slider de-dups back-to-back
        # boundaries that map to the same pixel. Re-fed on each (re)load with the freshly segmented laps.
        bounds: list[float] = []
        for lid in self.session.valid_lap_ids():
            w = self.session.lap_window(lid)
            if w is not None:
                bounds.extend(w)
        self.video.set_lap_ticks(bounds)
        # D1: the global scrub slider + ←/→ arrows seek pane A only; in compare mode distance-lock
        # the SAME move to pane B so the pair never desyncs. The hook self-guards on compare being
        # active (fanout_seek_b no-ops outside compare), so wiring it once here is safe in single mode.
        self.video.set_compare_seek_fanout(self.compare.fanout_seek_b)
        self.video.compareToggled.connect(self.compare.on_toggled)
        self.video.paneRepointRequested.connect(self.compare.on_pane_repoint)
        self.plots.scrubStarted.connect(self.scrub.on_started)
        self.plots.scrubMoved.connect(self.scrub.on_moved)
        self.plots.scrubEnded.connect(self.scrub.on_ended)
        # Auto-follow: the charts always show whichever lap the playhead is in (current vs best).
        # When the playhead crosses into a NEW lap (playing OR scrubbing), the speed + delta
        # charts switch to that lap, keeping the best lap as the reference overlay. We key off
        # the playhead's lap, so a single O(1) edge check per tick (in _apply_readout) drives it;
        # we only re-select on the actual lap CHANGE so it never thrashes. The followed lap is the
        # `followed_lap` field of the shared PlaybackState (built + reset to None in _wire_signals,
        # above, before this method runs); _select_default re-seeds it below.
        # F2: keep the sector boundary guide lines on the charts in sync. plots_view stays
        # pacer-free, so app computes the boundary x-positions via session for the current
        # axis mode and pushes them; recompute when the mode flips (the positions' units change).
        self.plots.modeChanged.connect(self._refresh_sector_lines)

    # ----------------------------------------------------- panel focus / maximize
    def _install_header_dblclick(self, panel: QWidget, column: QSplitter, main: QSplitter):
        """Make a panel's HEADER strip double-click-to-maximize. The header is the first child of
        the panel's layout (the `_panel` text label or the `_headered`/`_header_bar` widget — both
        carry the `PanelHeader` role), so we install an event filter on it and route a double-click
        to `_toggle_panel_maximized`. We remember each header's (panel, column, main) routing in a
        per-build dict so eventFilter — one method for all four — knows which quadrant fired.

        Fresh-per-view: __init__ re-seeds an empty `_header_routes` before calling this for the
        fresh widgets, so a header from a disposed view can never resolve here.
        Defensive: a header-less panel (shouldn't happen) is simply skipped."""
        item = panel.layout().itemAt(0)
        header = item.widget() if item is not None else None
        if header is None:
            return
        self._header_routes[header] = (panel, column, main)
        header.installEventFilter(self)

    def eventFilter(self, obj, event):
        """Catch a double-click on any registered panel header and toggle that panel's maximize.
        Everything else passes through untouched (return the base implementation)."""
        if (event.type() == QEvent.MouseButtonDblClick
                and obj in getattr(self, "_header_routes", {})):
            panel, _column, _main = self._header_routes[obj]
            self._toggle_panel_maximized(panel)
            return True
        return super().eventFilter(obj, event)

    def _toggle_panel_maximized(self, panel: QWidget):
        """Toggle `panel` between filling the window and the normal 2x2 grid. MAXIMIZE: snapshot the
        three splitters' current sizes, then collapse every section EXCEPT the one holding `panel` —
        in its own column AND the main splitter's other column — so the panel takes the whole
        central area. RESTORE (called on the maximized panel, or any panel while one is maximized):
        put the snapshotted sizes back. Robust to a panel that isn't in the current grid (a no-op)
        and to being driven programmatically (the verify harness calls this directly).

        The saved sizes live on the VIEW; a fresh CentralView starts from the un-maximized grid."""
        routes = getattr(self, "_header_routes", {})
        # Resolve the panel's owning COLUMN from its header route — the panel itself isn't a dict
        # key, so scan the values (only four entries; trivial). The main splitter is read directly
        # off self below, so we only need the column here.
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
        self._main_splitter.setSizes([full_w, 0] if in_left else [0, full_w])
        # The owning column: keep the panel's section, collapse its sibling. video/map are index 0,
        # table/charts are index 1 in their respective columns.
        top_panels = (self._video_panel, self._map_panel)
        full_h = sum(column.sizes()) or column.height()
        column.setSizes([full_h, 0] if panel in top_panels else [0, full_h])
        self._maximized_panel = panel

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

    # --------------------------------------------------------- consistency panel (F6)
    def set_consistency_visible(self, on: bool):
        """Show/hide the consistency strip under the lap table. Driven by the persistent View ▸
        "Show consistency panel" check item on the WINDOW (the choice is held on the window so it
        survives a reload). Records the choice on the view too (so a later rebuild applies it) and,
        when showing, refreshes the strip's stats first (it may have been built for an old session,
        or never shown). This is the moved body of the old StudioWindow._on_consistency_toggled."""
        self._consistency_visible = bool(on)
        self._apply_consistency_visible(refresh=self._consistency_visible)

    def _apply_consistency_visible(self, *, refresh: bool):
        """Apply the current `_consistency_visible` choice to the live panel: optionally refresh its
        stats (when being shown) then set its visibility. Shared by the build-time default-hide
        (refresh=False) and the View-toggle (refresh iff showing). Defensive: a partially-built view
        without the panel is a no-op."""
        panel = getattr(self, "consistency", None)
        if panel is None:
            return
        if refresh and self._consistency_visible:
            panel.refresh()  # ensure the shown stats are current for this session
        panel.setVisible(self._consistency_visible)

    # --------------------------------------------------------- chapter banner
    def _update_chapter_label(self, chapter_index: int):
        """Banner text: the recording label plus, for a chaptered session, the current chapter.
        Suppressed while a seam reopen is in flight (the "loading next chapter…" hint owns the banner
        until the next chapter has presented, at which point _on_seam_loading(False) restores this)."""
        if getattr(self, "_seam_loading", False):
            return
        label = chapters.recording_label(self._paths)
        if self.video.is_multi:
            self.chapter_label.setText(f"{label}  —  chapter {chapter_index + 1} of "
                                       f"{len(self.session.chapters)}")
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
        """Park the PRIMARY video pane on the best lap's first frame at launch (and after a reload),
        so the largest quadrant isn't a black void before the user touches anything — and so the
        map marker / charts / hero readout all reflect a real moment INSIDE a lap (not lead-in).

        Seek a hair INTO the lap (theme.LAP_SEEK_NUDGE_S past its start) for the same reason
        _on_laps_selected does — a seek to the exact contiguous-lap boundary ms-quantizes a touch
        below it and resolves to the PREVIOUS lap. The pane is freshly constructed and never played,
        so it is already paused; the seek decodes + presents the frame without starting playback or
        audio. Seed applied_t (on the shared PlaybackState) so the very next tick's "did the position
        advance" check sees the poster position as already-applied (the readout/marker are driven
        directly here).

        Graceful no-lap edge: if there is no valid best lap there is nothing to poster, so skip the
        seek entirely (the 0-valid-lap session still launches — just on a black/first frame)."""
        best = self.session.best_lap_id()
        if best is None:
            return
        window = self.session.lap_window(best)
        if window is None:
            return
        target = window[0] + theme.LAP_SEEK_NUDGE_S
        self.video.seek(target)          # paused decode → presents the best lap's start frame
        self._playback.latest_t = target
        self._playback.applied_t = target
        # Populate the chart playhead + readout / map marker directly from the poster time so the
        # t=0 state is consistent with the shown frame immediately (the same work a playback tick
        # does), without waiting for a positionChanged tick the seek may not emit synchronously.
        self._apply_position(target)

    def _on_user_select(self, ids):
        # A genuine user click in the lap table also jumps the video to that lap (F1).
        self._on_laps_selected(ids, seek=True)

    # --------------------------------------------------------- corners view (F-corner)
    def _on_corners_toggled(self, on: bool):
        """Flip the table panel between Laps mode (the untouched LapTable) and Corners mode.
        The corner table is (re)pointed at the current selection lazily on entry, so an
        unused Corners view costs nothing."""
        self.table_stack.setCurrentIndex(1 if on else 0)
        if on:
            self.corner_table.set_lap(self._corner_lap)
        self._update_table_header()

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

    def _update_table_header(self):
        """The table panel's mode label: "LAPS", or "CORNERS · LAP n" while in Corners mode
        (so it is always explicit WHICH lap the per-corner rows describe)."""
        if self.corners_btn.isChecked():
            lap = self._corner_lap
            self._table_label.setText(f"CORNERS · LAP {lap}" if lap is not None else "CORNERS")
        else:
            self._table_label.setText("LAPS")

    def _on_laps_selected(self, ids, seek=False):
        # The table multi-selection drives the PLOTS only; the map's current-lap overlay
        # follows the video position (and thus selection, since F1 seeks into the lap).
        self.plots.set_laps(ids)
        # Corners view follows the primary selected lap (ids[0]: the lowest-id selection —
        # the same lap a user-click seek jumps to — or the fastest from _select_default).
        self._set_corner_lap(ids[0] if ids else None)
        # F1 seeks ONLY on user selection — not on programmatic re-select from
        # _select_default()/_on_lines(), or dragging a timing line would yank the video.
        if seek and ids:
            # Seek a hair INTO the selected lap, not onto its exact start. Laps are contiguous
            # (lap N's finish == lap N+1's start) and the player quantizes the seek to whole ms
            # (setPosition takes int(seconds*1000)), so a seek to the exact boundary lands a few
            # tenths of a ms BELOW it — which then resolves to the PREVIOUS lap and makes the
            # ▶ marker / map / auto-follow jump back one lap (the reported "clicking a lap selects
            # a different lap" bug). Nudging by theme.LAP_SEEK_NUDGE_S (a few ms, imperceptible
            # in a ~70 s lap) guarantees the ms-quantized playback position lands INSIDE the lap,
            # so lap_at_time(position) == the lap the user clicked.
            target = self.session.lap_window(min(ids))[0] + theme.LAP_SEEK_NUDGE_S
            self.video.seek(target)
            # Don't let the auto-follow collapse a just-made (possibly multi-lap) comparison the
            # instant the seek's positionChanged lands: seed followed_lap to the lap the seek
            # resolves into, so the immediate post-seek tick is NOT an edge. The user keeps the
            # selected overlay while paused; once PLAYBACK MOVES ON into a different lap, the edge
            # fires and the charts collapse to [current, best] (the locked behaviour).
            self._playback.followed_lap = self.session.lap_at_time(target)

    # --------------------------------------------------------- per-frame tick path
    def _on_position(self, t: float):
        # Runs in the video event path — keep it trivial so frame presentation isn't starved.
        self._playback.latest_t = t

    def tick(self):
        """The per-frame (~30 Hz) work, called by StudioWindow's persistent tick timer (which stays
        on the window so it is created once and reused across reloads — only the delegation target,
        this fresh view, changes on a swap). This is the moved body of the old StudioWindow._tick."""
        # Drain a coalesced MAP MARKER-DRAG seek first (one per tick, not per mouse-move): the
        # marker stashes its latest dragged time and the resulting seek drives the normal
        # playback→tick sync that re-places the marker/cursor/readout.
        self.scrub.drain_marker_seek()
        # While the user is scrubbing a plot cursor, the source of truth is the drag, not playback:
        # the scrub controller issues at most ONE coalesced seek per tick to the latest dragged
        # target (both panes in compare) and applies the cursor/marker/readout once, and the
        # (stale / seek-driven) playback position is NOT applied — that gating is what prevents the
        # drag↔positionChanged feedback loop from oscillating.
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

    # F5: the auto-follow / playback-cursor SETTERS are gone — the compare controller writes
    # `followed_lap` and the scrub controller seeds `applied_t` DIRECTLY on the shared PlaybackState
    # (self._playback), which replaced the injected set_followed_lap / set_applied_t callbacks. The
    # tick loop still owns the normal latest_t→applied_t advance (in tick(), above).

    def _apply_position(self, t: float):
        self.plots.set_playhead_time(t)
        self._apply_readout(t)

    def _apply_readout(self, t: float):
        # Resolve the two per-tick searches ONCE and reuse them everywhere below (the lap that
        # contains t, and the nearest trace index at t) — they used to be recomputed two more
        # times each tick (delta_at_time re-ran lap_at_time; the playhead + speed lookups each
        # re-ran index_at_time).
        lap_id = self.session.lap_at_time(t)   # F3: which lap is on the video
        i = self.session.index_at_time(t)      # nearest trace sample (marker + speed)
        self.map.set_marker_index(i)           # F3: red marker (same point set_playhead_time chose)
        self._follow_current_lap(lap_id, t)  # charts auto-follow the playhead's lap (vs best)
        self.table.set_current_lap(lap_id)
        self.map.set_current_lap(lap_id)  # highlight the current lap's trace on the map
        sp = float(self.session.tv[i]) if i is not None else None  # F2: speed km/h at that index
        # C6: the under-video strip is now ONLY the transport timecode — the live MOMENT (Δ · speed ·
        # lap) lives once, in the hero #DiffBox in the charts header, so the two can no longer
        # duplicate OR disagree (the old strip read "speed 72.6" while the box read "73"). On a
        # multi-chapter recording it also names the current chapter (e.g. "1/3"), which IS video-
        # specific and not shown anywhere else; for a single file it is just the timecode.
        self.video.set_readout(self._transport_readout(t))
        self._update_diff_box(t, sp, lap_id)
        # g-meter overlay: feed the vehicle-frame g at the current media time (a cheap lookup) and
        # the current lap (so the max-G envelope resets at the lap boundary, showing THIS lap's
        # grip). Gate the g_at_time lookup on the overlay being visible (off by default) so the
        # searchsorted+hypot is skipped entirely when nothing consumes it.
        #
        # In COMPARE mode the panes' g-meter lap scope is PINNED to the chosen pair (each pane via
        # set_pane_gmeter_lap once on enter/repoint), so the normal-mode per-tick primary pin would
        # double-drive the primary's envelope and fight that fixed scope. Skip it here so the scope
        # is driven by exactly one path — mirroring the same early-out in _follow_current_lap.
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
            return f"{fmt_time(t)}   ·   chapter {chs.chapter_at(t) + 1}/{len(chs)}"
        return fmt_time(t)

    def _follow_current_lap(self, lap_id: int | None, t: float):
        """Auto-follow the playhead's lap on the speed + delta charts (current lap vs best).

        On a lap-change EDGE — `lap_id` is a valid lap that differs from the one the charts are
        currently following — switch the charts to show [current lap, best] and update the lap
        table highlight/selection to match, so the table ▶/selection, the map overlay and the
        plots all agree on the current lap. Only acts on the actual edge (O(1) check per tick, a
        refresh only on the change) so it never thrashes, and it works while PLAYING or SCRUBBING
        (we key off the playhead's lap, not the play state).

        Graceful edges:
          * `lap_id is None` (lead-in / between laps / cool-down) → HOLD the last followed lap;
            never blank the charts.
          * The table selection is updated via the programmatic `select()` path (signals blocked),
            so it does NOT emit `laps_selected` and therefore cannot trigger a user-seek that would
            fight playback. (Select-lap→seek is gated to genuine user clicks via _on_user_select.)
        """
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

    def _update_diff_box(self, t: float, sp: float | None, lap_id: int | None):
        """Refresh the always-on Δ/speed box for the current moment (priority: Δ-to-best in
        seconds). Δ comes from session.delta_at_lap with the already-resolved `lap_id` (same
        normalized-distance alignment as the delta plot, so the box and the cursor on the curve
        agree) — reusing lap_id instead of re-resolving lap_at_time. Outside a valid lap Δ is —."""
        d = self.session.delta_at_lap(lap_id, t) if lap_id is not None else None
        # Text + colour come from the ONE shared formatter (theme.format_delta_speed), the single
        # source of truth this readout shares with the burned-in export overlay so the shareable MP4
        # can't say something different from the live box. The formatter owns: the "Δ +0.00 s" run,
        # the honest no-lap "— km/h" (Phase-0: outside a valid lap we show no misleading lead-in
        # speed), the five-space gap, and the three-way Δ colour. `colour` is None when there is no
        # semantic cue (no delta / dead-even), which we resolve to the box's primary text colour.
        text, sem_colour = theme.format_delta_speed(d, sp, lap_id)
        # Colour cue: the shared three-way rule — green when meaningfully up on best, red when down,
        # and the primary text colour when there's no delta OR it's dead even (|Δ| within
        # ±theme.DELTA_EVEN_EPS_S; an exact 0 used to read GREEN). The card's surface bg / font /
        # border come from the global QSS (#DiffBox); a per-widget `color` rule merges over it and
        # overrides ONLY the foreground (no per-tick background/border re-layout cost), and only when
        # the colour actually changes.
        colour = sem_colour or theme.C.text
        self.diff_box.setText(text)
        if colour != getattr(self, "_diff_colour", None):
            self._diff_colour = colour
            self.diff_box.setStyleSheet(f"QLabel#DiffBox {{ color: {colour}; }}")

    # ------------------------------------------------------------- compare-state access
    # The scrub + compare behavioural clusters live in self.scrub / self.compare (constructed in
    # _build_controllers). CentralView keeps the shared single-driver telemetry path (_apply_readout
    # / _update_diff_box) and the auto-follow tick logic (_follow_current_lap / the shared
    # PlaybackState's followed_lap), both of which gate on whether compare is on — funnel that
    # through one defensive accessor.
    def _comparing(self) -> bool:
        """True iff compare mode is on. Defensive: tolerates the controller not being constructed
        yet (e.g. a CentralView.__new__'d for a unit test that drives _follow_current_lap directly
        without building the UI) — mirrors the old `getattr(self, "_compare", False)` guard."""
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
        the best lap is green (theme.SERIES_BEST), every other lap cycles theme.CHART_SERIES by
        its draw-order index `k` — so a brake glyph always sits on its own lap's curve colour
        (and compare's two laps stay distinguishable, like the curves)."""
        if lap_id == self.session.best_lap_id():
            return theme.SERIES_BEST
        return theme.CHART_SERIES[k % len(theme.CHART_SERIES)]

    def _refresh_driving_channels(self):
        """F5: push the brake glyphs (map + speed chart) and shaded coasting spans (speed chart)
        for the lap(s) currently shown — the current/selected lap normally, BOTH laps in compare
        (the Circuit Tools braking-zone comparison). Pure consumer of session.lap_brake_* /
        lap_coasting_* so the views stay pacer-free; cheap (the channels are cached per
        segmentation). Called on load, lap-selection change, axis-mode flip, compare enter/exit/
        repoint, and re-segmentation."""
        # The laps to annotate, in the SAME order the charts draw them, so the colours line up.
        if self._comparing() and self.compare.lap_a is not None and self.compare.lap_b is not None:
            lap_ids = [self.compare.lap_a, self.compare.lap_b]
        elif self._corner_lap is not None:
            lap_ids = [self._corner_lap]
        else:
            lap_ids = []
        mode = self.plots.axis_mode()
        map_markers, brake_plot, coast_plot = [], [], []
        for k, lid in enumerate(lap_ids):
            colour = self._driving_lap_colour(lid, k)
            map_markers.append((self.session.lap_brake_map_markers(lid), colour))
            brake_plot.append((self.session.lap_brake_plot_positions(lid, mode), colour))
            coast_plot.append((self.session.lap_coasting_plot_spans(lid, mode), colour))
        self.map.set_brake_markers(map_markers)
        self.plots.set_brake_markers(brake_plot)
        self.plots.set_coasting_spans(coast_plot)

    # ------------------------------------------------------------- the shared rebuild seam
    def rebuild_derived_views(self, *, reselect: bool = True):
        """THE single seam that rebuilds every session-DERIVED surface from the current Session.

        A re-segmentation (_on_lines), a reference load/clear (the window's _apply_reference_change)
        and the initial build tail (__init__) all change the baseline the same panels read against
        (the laps, the per-corner Δ, the racing-line overlay, the brake/coast channels, the sector
        guides), so they MUST refresh the identical union of views, in the identical order — three
        hand-maintained copies of this sequence had already DRIFTED (the reference path silently
        omitted set_corners + the driving channels). Each call site keeps only its own SPECIFIC
        extras inline (set_timing_lines / set_compare_enabled / _save_sidecar for the segmentation
        edit; _update_reference_status for the reference change; the one-time chip/sync setup at
        build) and routes everything shared through here.

        Ordering is load-bearing and preserved from the (more complete) _on_lines sequence:
          • table first (the lap rows + Δ columns);
          • map.refresh_overlays then map.set_corners — set_corners re-pushes the corner labels AND
            clears any stale corner highlight, so it runs before the consumers below;
          • corner_table + consistency (both read the now-current corner model / lap set);
          • _refresh_driving_channels — re-push the brake glyphs / coast bands EXPLICITLY here,
            because the selection step below can early-out (a re-segment may leave the primary lap
            id unchanged so _set_corner_lap short-circuits) while the underlying channels DID change;
          • the selection step LAST-but-one: _select_default() to re-pick the two fastest laps
            (reselect=True), OR plots.refresh() to redraw the pinned [A,B] pair in place while
            comparing (reselect=False) — a re-select would collapse the comparison;
          • _refresh_sector_lines after the selection (it re-derives the brake glyphs for the
            now-current selection in the current axis mode)."""
        self.table.refresh()
        # Re-segmentation / reference change shifts the measured-vs-inferred map segments and the
        # faint reference line; redraw the overlays, then re-push the corner labels (set_corners
        # also clears any stale corner highlight) so the corner consumers below read fresh state.
        self.map.refresh_overlays()
        self.map.set_corners(self.session.corner_map_markers())
        self.corner_table.refresh()
        # F6: the lap set / splits / per-corner σ shifted with the baseline — rebuild the strip.
        self.consistency.refresh()
        # F5: the driving channels were invalidated with the corner model; re-push them HERE (the
        # selection below may early-out on an unchanged primary-lap id while the channels changed).
        self._refresh_driving_channels()
        if reselect:
            self._select_default()
        else:
            # Compare mode draws its own pinned [A,B] pair; refresh that overlay in place rather
            # than re-selecting the two fastest laps (which would tear the comparison down).
            self.plots.refresh()
        # F2: the sector guide lines + their units track the (possibly rescaled) axis and the new
        # selection, so refresh them AFTER the selection is in place.
        self._refresh_sector_lines()

    # ------------------------------------------------------------- timing-line edits
    def _on_lines(self, start, sectors):
        # Re-segmentation shifts lap ids, so any pinned compare pair is now stale — leave compare
        # mode first (also tears the 2nd pane down), then re-segment and rebuild the default view.
        if self._comparing():
            self.video.set_compare_enabled(False)  # un-checks -> compareToggled(False) -> exit
        self.session.set_timing_lines(start, sectors)
        # Re-segmentation shifted lap ids, cleared the per-lap gap-fill cache and invalidated the
        # corner model + driving channels — rebuild every derived view through the shared seam,
        # re-selecting the two fastest laps (compare was just exited above, so reselect=True).
        self.rebuild_derived_views(reselect=True)
        # The valid-lap count may have changed — re-evaluate whether compare can be offered.
        self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
        # Persist the user's edit (this handler fires only on a drag release / sector add or
        # reset — never on a plain load), so the hand-tuned lines survive an app restart.
        self._save_sidecar()

    def _save_sidecar(self):
        """Write the current timing lines to the recording's sidecar JSON (absolute lat/lon
        via session.timing_lines_latlon). Called ONLY from _on_lines — i.e. only on a genuine
        user edit — so an untouched session never creates or rewrites the file. Best-effort:
        an unwritable folder just logs and the lines still apply for this run. The sidecar PATH was
        resolved by the window's load orchestration and passed in at construction."""
        path = getattr(self, "_sidecar_path", None)
        if not path:
            return
        start, sectors = self.session.timing_lines_latlon()
        try:
            sidecar.save(path, self.session.track_name, start, sectors)
        except OSError as exc:
            print(f"studio: could not write timing-line sidecar {path}: {exc}", flush=True)
            return
        print(f"studio: timing lines saved to {os.path.basename(path)}", flush=True)
