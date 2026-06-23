"""CentralView: the session-scoped central widget for ONE loaded recording.

Owns the panels (video/map/plots/table/corner_table/consistency/diff_box/chapter banner), the
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

from PySide6.QtCore import QEvent, Qt
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
    """Session-scoped central widget for one loaded recording (see module docstring)."""

    def __init__(self, session, paths: list[str], sidecar_path: str | None,
                 consistency_visible: bool, parent: QWidget | None = None):
        super().__init__(parent)
        # Read aliases of window-owned state; the view never reassigns these.
        self.session = session
        self._paths = list(paths)
        self._sidecar_path = sidecar_path
        self._consistency_visible = consistency_visible

        # Atomic build: panels -> layout -> signals -> controllers.
        self._construct_panels()
        self._layout_panels()
        self._wire_signals()
        self._build_controllers()

        # Seed session-derived views (selects two fastest laps).
        self.rebuild_derived_views(reselect=True)
        # Poster the best-lap first frame so the video isn't a black void at launch.
        self._poster_seek()
        # Apply the window-held consistency-visible choice to the fresh panel.
        self._apply_consistency_visible(refresh=False)

    # ------------------------------------------------------------------ lifecycle
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
        self.video.set_gmeter_source(self.session.gmeter_source())
        self.video.gmeter_btn.setEnabled(self.session.has_gmeter)
        if not self.session.has_gmeter:
            self.video.gmeter_btn.setToolTip("No accelerometer data in this recording")
        self.map = MapView(self.session)
        # Corner labels pushed from here so MapView stays a pure consumer of marker tuples.
        self.map.set_corners(self.session.corner_map_markers())
        self.plots = PlotsView(self.session)
        self.table = LapTable(self.session)
        # Corners mode: a 2nd table stacked under the same panel (per-corner rows for the selected lap).
        self.corner_table = CornerTable(self.session)
        self._corner_lap: int | None = None  # the lap the Corners view describes

        # Always-on Δ/speed readout for the current moment (hero #DiffBox; Δ colour set per-tick).
        self.diff_box = QLabel("Δ —    — km/h")
        self.diff_box.setObjectName("DiffBox")
        self.diff_box.setAlignment(Qt.AlignCenter)
        self.diff_box.setFont(theme.mono_font(theme.HERO, theme.W_SEMIBOLD))
        self._diff_colour = None  # last applied Δ-value colour (per-tick recolor guard)

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

        video_panel = self._panel("VIDEO", self.chapter_label, (self.video, 1))

        # TABLE panel: a header bar (mode label + Corners toggle) above a QStackedWidget of the two
        # tables (Laps at index 0, Corners at index 1).
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
        # F6: the collapsible consistency strip under the lap table (trend sparkline + top-5
        # inconsistent corners); a corner-row click ring-highlights its apex on the map only.
        self.consistency = ConsistencyPanel(self.session)
        self.consistency.corner_clicked.connect(self.map.highlight_corner)
        # Consistency strip shares a vertical splitter with the table stack so enabling it shrinks the
        # (min/max-capped) strip, never the lap table; table stack gets a ~5-row min-height so it stays
        # usable.
        rows_h = self.table.table.verticalHeader().defaultSectionSize()
        self.table_stack.setMinimumHeight(rows_h * 5 + 56)  # ~5 rows + column header + footer
        table_body = QSplitter(Qt.Vertical)
        table_body.addWidget(self.table_stack)
        table_body.addWidget(self.consistency)
        table_body.setStretchFactor(0, 1)   # the lap table takes any extra height
        table_body.setStretchFactor(1, 0)   # the consistency strip keeps its compact size
        table_body.setCollapsible(0, False)  # never collapse the lap table away
        table_panel = self._headered(table_header, (table_body, 1))

        # MAP header: title (left) + the rainbow/snap/sector buttons (right); handlers live in MapView.
        for b in (self.map.rainbow_btn, self.map.snap_btn,
                  self.map.add_sector_btn, self.map.reset_sectors_btn):
            b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        map_label = QLabel("MAP")
        map_label.setProperty("role", "BarLabel")
        map_header = self._header_bar(map_label, 1, self.map.rainbow_btn, self.map.snap_btn,
                                      self.map.add_sector_btn, self.map.reset_sectors_btn)
        map_panel = self._headered(map_header, (self.map, 1))

        # CHARTS consolidated bar: section label (left) · the Δ/speed readout (centre) · the x-mode toggle (right).
        plots_label = QLabel("SPEED · Δ TO BEST")
        plots_label.setProperty("role", "BarLabel")
        self.plots.x_mode_combo.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.plots.ideal_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        self.plots.brake_throttle_btn.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        plots_header = self._header_bar(plots_label, 1, (self.diff_box, 0), 1,
                                        self.plots.brake_throttle_btn, self.plots.ideal_btn,
                                        self.plots.x_mode_combo)
        plots_panel = self._headered(plots_header, (self.plots, 1))

        # Stash the four panel containers for _layout_panels.
        self._video_panel = video_panel
        self._table_panel = table_panel
        self._map_panel = map_panel
        self._plots_panel = plots_panel

    # ----------------------------------------------------- build phase 2: layout
    def _layout_panels(self):
        """Assemble the 2x2 nested splitter grid, install the dblclick-to-maximize header filters,
        set this widget's layout to the main splitter."""
        video_panel = self._video_panel
        table_panel = self._table_panel
        map_panel = self._map_panel
        plots_panel = self._plots_panel

        # Layout favours the analytical core over the video: left column ~40% / right ~60%, and
        # within them the table and charts get the majority.
        left = QSplitter(Qt.Vertical)
        left.addWidget(video_panel)
        left.addWidget(table_panel)
        left.setStretchFactor(0, 52)
        left.setStretchFactor(1, 48)
        left.setSizes([440, 400])

        right = QSplitter(Qt.Vertical)
        right.addWidget(map_panel)
        right.addWidget(plots_panel)
        right.setStretchFactor(0, 38)
        right.setStretchFactor(1, 62)
        right.setSizes([320, 520])

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setStretchFactor(0, 40)
        main.setStretchFactor(1, 60)
        main.setSizes([576, 864])
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
        self.video.paneRepointRequested.connect(self.compare.on_pane_repoint)
        self.plots.scrubStarted.connect(self.scrub.on_started)
        self.plots.scrubMoved.connect(self.scrub.on_moved)
        self.plots.scrubEnded.connect(self.scrub.on_ended)
        # F2: keep the sector guide lines in sync; plots_view is pacer-free, so we compute the
        # boundary positions via session and recompute when the axis mode flips (units change).
        self.plots.modeChanged.connect(self._refresh_sector_lines)

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
        Everything else passes through untouched (return the base implementation)."""
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
        """Show/hide the consistency strip; refreshes its stats when showing. Driven by the
        persistent View-menu item on the window."""
        self._consistency_visible = bool(on)
        self._apply_consistency_visible(refresh=self._consistency_visible)

    def _apply_consistency_visible(self, *, refresh: bool):
        """Apply _consistency_visible to the live panel (refresh its stats first when showing).
        No-op for a partially-built view without the panel."""
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
            return f"{fmt_time(t)}   ·   chapter {chs.chapter_at(t) + 1}/{len(chs)}"
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

    def _update_diff_box(self, t: float, sp: float | None, lap_id: int | None):
        """Refresh the Δ/speed box for the current moment (Δ-to-best priority). Text + colour from
        the shared theme.format_delta_speed (same formatter as the export overlay)."""
        d = self.session.delta_at_lap(lap_id, t) if lap_id is not None else None
        text, sem_colour = theme.format_delta_speed(d, sp, lap_id)
        colour = sem_colour or theme.C.text
        self.diff_box.setText(text)
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
        the best lap is green (theme.SERIES_BEST), every other lap cycles theme.CHART_SERIES by
        its draw-order index `k` — so a brake glyph always sits on its own lap's curve colour
        (and compare's two laps stay distinguishable, like the curves)."""
        if lap_id == self.session.best_lap_id():
            return theme.SERIES_BEST
        return theme.CHART_SERIES[k % len(theme.CHART_SERIES)]

    def _refresh_driving_channels(self):
        """Push brake glyphs (map + speed chart) and coast spans for the shown lap(s) — both laps in
        compare, the current/selected lap otherwise, in the chart's draw order so colours line up."""
        if self._comparing() and self.compare.lap_a is not None and self.compare.lap_b is not None:
            lap_ids = [self.compare.lap_a, self.compare.lap_b]
        elif self._corner_lap is not None:
            lap_ids = [self._corner_lap]
        else:
            lap_ids = []
        mode = self.plots.axis_mode()
        map_markers, brake_plot, coast_plot, bt_plot = [], [], [], []
        for k, lid in enumerate(lap_ids):
            colour = self._driving_lap_colour(lid, k)
            map_markers.append((self.session.lap_brake_map_markers(lid), colour))
            brake_plot.append((self.session.lap_brake_plot_positions(lid, mode), colour))
            coast_plot.append((self.session.lap_coasting_plot_spans(lid, mode), colour))
            # D3: the synthetic brake/throttle band (its own red/green fill, not the lap colour).
            xs, inten = self.session.lap_brake_throttle_plot(lid, mode)
            if xs is not None:
                bt_plot.append((xs, inten))
        self.map.set_brake_markers(map_markers)
        self.plots.set_brake_markers(brake_plot)
        self.plots.set_coasting_spans(coast_plot)
        self.plots.set_brake_throttle(bt_plot)

    # ------------------------------------------------------------- the shared rebuild seam
    def rebuild_derived_views(self, *, reselect: bool = True):
        """The single seam that rebuilds every session-derived surface (table, map overlays/corners,
        corner table, consistency, driving channels, selection, sector lines) in a load-bearing
        order — three drifted copies were unified here. Shared by re-segmentation, a reference
        load/clear, and the initial build; each call site keeps only its own extras inline."""
        self.table.refresh()
        # set_corners re-pushes the corner labels AND clears any stale highlight, so it runs after
        # refresh_overlays and before the corner consumers below.
        self.map.refresh_overlays()
        self.map.set_corners(self.session.corner_map_markers())
        self.corner_table.refresh()
        self.consistency.refresh()
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

    # ------------------------------------------------------------- timing-line edits
    def _on_lines(self, start, sectors):
        # Re-segmentation shifts lap ids: exit compare (stale pair), re-segment, rebuild all derived
        # views (reselect=True), re-check compare availability, persist the edit.
        if self._comparing():
            self.video.set_compare_enabled(False)  # un-checks -> compareToggled(False) -> exit
        self.session.set_timing_lines(start, sectors)
        self.rebuild_derived_views(reselect=True)
        self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
        self._save_sidecar()

    def _save_sidecar(self):
        """Write the timing lines to the recording's sidecar JSON. Called only from _on_lines (a
        genuine user edit), so an untouched session never creates the file. Best-effort: an
        unwritable folder just logs."""
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
