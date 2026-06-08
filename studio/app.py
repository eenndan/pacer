"""StudioWindow: assembles the panels and wires the cross-panel sync.

Layout (resizable splitters):
    ┌──────────────┬───────────────────────────┐
    │  VideoView   │   MapView (track + lines) │
    ├──────────────┼───────────────────────────┤
    │  LapTable    │   PlotsView (speed/delta) │
    └──────────────┴───────────────────────────┘
"""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from . import chapters, theme
from .lap_table import LapTable
from .map_view import MapView
from .plots_view import PlotsView
from .session import DEFAULT_SAMPLE, Session, fmt_time
from .video_view import VideoView

# When a lap is selected we seek a few ms INTO it rather than onto its exact start, so the
# whole-ms seek quantization can't land the playback position just before the (contiguous) lap
# boundary and resolve to the previous lap. Far smaller than a frame; invisible in a ~70 s lap.
_LAP_SEEK_NUDGE_S = 0.010


class StudioWindow(QMainWindow):
    def __init__(self, paths: list[str], full: bool = False):
        super().__init__()
        self.resize(1440, 900)
        self._tick_timer = None  # created on the first _build_ui; reused across reloads
        self._build_menu()
        # If opt-in full-recording was requested on the CLI, discover the sibling chapters of the
        # FIRST opened file up front. With explicit multiple paths the user already chose the
        # chain, so don't auto-discover; without the flag the DEFAULT is unchanged (just `paths`).
        if full and len(paths) == 1:
            paths = chapters.discover_siblings(paths[0])
        self._load(paths)

    # ------------------------------------------------------------------ loading
    def _load(self, paths: list[str]):
        """Load (or reload) the session for `paths` and (re)build the whole UI + wiring. Used at
        startup and by the "Load full recording" action (which reloads with the discovered
        sibling chapters). Tearing the central widget down and rebuilding keeps the panels — each
        of which captures `session` at construction — simple and free of stale references."""
        self._paths = list(paths)
        print("studio: loading telemetry…", flush=True)
        self.session = Session.load(paths)
        n_ch = len(self.session.chapters) if self.session.chapters else 1
        print(f"studio: {self.session.laps.point_count()} points, "
              f"{self.session.lap_count()} laps, {n_ch} chapter(s).", flush=True)

        label = chapters.recording_label(paths)
        self.setWindowTitle(f"pacer studio — {label}" if label else "pacer studio")
        self._build_ui()

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

    def _build_ui(self):
        # On a reload ("Load full recording"), tear down the previous VideoView's pane(s) — stop
        # the decoder AND close the g-meter overlay window — so neither lingers after the old
        # widget tree is replaced by setCentralWidget below.
        old_video = getattr(self, "video", None)
        if old_video is not None:
            old_video.stop_all()
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
        self.plots = PlotsView(self.session)
        self.table = LapTable(self.session)

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
        self._update_chapter_label(self.video.current_chapter())
        self.video.chapterChanged.connect(self._update_chapter_label)
        # Only show the banner for a real (>1 chapter) chaptered session; a single file is
        # exactly as before (no banner clutter).
        self.chapter_label.setVisible(self.video.is_multi)

        # Each panel gets a flush "section header" strip (uppercase, dimmed) above its content.
        # The video panel stacks the chapter banner under its plain text header; the table is
        # wrapped fresh. The MAP and PLOTS panels use WIDGET header bars (below) so the right
        # column's chrome collapses into the headers instead of full-width rows between the panels.
        video_panel = self._panel("VIDEO", self.chapter_label, (self.video, 1))
        table_panel = self._panel("LAPS", (self.table, 1))

        # MAP header: title (left) + the sector buttons (right-aligned, compact) — moved OFF the
        # full-width row that used to sit between the map and the charts. Their handlers/signal
        # wiring (re-segmentation) are unchanged; only the mount point moved.
        for b in (self.map.add_sector_btn, self.map.reset_sectors_btn):
            b.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        map_label = QLabel("MAP")
        map_label.setProperty("role", "BarLabel")
        map_header = self._header_bar(map_label, 1,
                                      self.map.add_sector_btn, self.map.reset_sectors_btn)
        map_panel = self._headered(map_header, (self.map, 1))

        # CHARTS consolidated bar (replaces the old separate panel-header row + full-width DiffBox
        # row + the combo's own row): section label (left) · the emphasized Δ/speed readout
        # (centre) · the x-mode toggle relocated from plots_view (right). The toggle keeps its
        # modeChanged wiring; the readout keeps its per-tick recolor. ~2 rows of height reclaimed.
        plots_label = QLabel("SPEED · Δ TO BEST")
        plots_label.setProperty("role", "BarLabel")
        self.plots.x_mode.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)
        plots_header = self._header_bar(plots_label, 1, (self.diff_box, 0), 1, self.plots.x_mode)
        plots_panel = self._headered(plots_header, (self.plots, 1))

        left = QSplitter(Qt.Vertical)
        left.addWidget(video_panel)
        left.addWidget(table_panel)
        left.setSizes([540, 360])

        # Rebalance the right column: the charts (the analytical core) get the MAJORITY — map ~40%
        # / charts ~60%. The map only needs enough to read the (now-tighter) track clearly.
        right = QSplitter(Qt.Vertical)
        right.addWidget(map_panel)
        right.addWidget(plots_panel)
        right.setStretchFactor(0, 40)
        right.setStretchFactor(1, 60)
        right.setSizes([360, 540])

        main = QSplitter(Qt.Horizontal)
        main.addWidget(left)
        main.addWidget(right)
        main.setSizes([580, 860])
        self.setCentralWidget(main)

        # --- cross-panel wiring ---
        # positionChanged fires in the video decode/present path; it must do almost nothing
        # (just record the latest time). A steady ~30 Hz timer applies the map/plot/readout
        # update off that path, so heavy repaints never starve frame presentation.
        self._latest_t = 0.0
        self._applied_t: float | None = None
        self.video.positionChanged.connect(self._on_position)
        # One ~30 Hz tick timer for the window's lifetime; on reload reuse it (a second timer
        # would double the tick rate and fire into the now-rebuilt panels).
        if getattr(self, "_tick_timer", None) is None:
            self._tick_timer = QTimer(self)
            self._tick_timer.setInterval(33)  # ~30 Hz
            self._tick_timer.timeout.connect(self._tick)
            self._tick_timer.start()
        self.map.seek_requested.connect(self.video.seek)
        self.map.timing_lines_changed.connect(self._on_lines)
        self.table.laps_selected.connect(self._on_user_select)

        # --- compare videos (Phase B) ---
        # Two equal side-by-side video panes, behind the explicit "Compare videos" toggle.
        # Compare is OFF by default and the toggle is enabled only when there are >=2 valid laps.
        # The PRIMARY (left) pane keeps driving ALL telemetry exactly as today; the SECONDARY
        # (right) pane is video-only. While compare is on, auto-follow's lap re-point is SUSPENDED
        # so the pinned panes/charts don't thrash as the playhead crosses lap boundaries.
        self._compare = False
        self._compare_a: int | None = None  # primary (left) lap id
        self._compare_b: int | None = None  # secondary (right) lap id
        self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
        self.video.compareToggled.connect(self._on_compare_toggled)
        self.video.paneRepointRequested.connect(self._on_pane_repoint)

        # --- plot cursor scrub (a fine, lap-scoped scrubber; the full-video slider stays) ---
        # Dragging either plot cursor seeks the video WITHIN the current lap. plots_view emits
        # the raw plot-x + which axis it came from; we convert (session, pacer-side) to a media
        # time, clamp it to the lap, throttle the seek to ≤1 per tick, pause while dragging and
        # resume iff it was playing. See _on_scrub_*.
        self._scrub_lap: int | None = None      # the lap captured at grab; the drag is scoped to it
        self._scrub_target: float | None = None  # latest requested media time (coalesced)
        self._scrub_pending = False              # a new target awaits the next tick's seek
        self._scrub_was_playing = False          # restore playback on release iff it was playing
        # Compare-mode scrub: the drag is distance-locked, so it parks BOTH panes on the SAME
        # track position. The same plot-x converts to each lap's own global media time; each
        # pane's seek is coalesced to <=1/tick reusing the gate (the single-pane fields above are
        # the PRIMARY pane's; these add the SECONDARY pane's). Only used while compare is on.
        self._scrub_target_b: float | None = None
        self._scrub_pending_b = False
        self.plots.scrubStarted.connect(self._on_scrub_started)
        self.plots.scrubMoved.connect(self._on_scrub_moved)
        self.plots.scrubEnded.connect(self._on_scrub_ended)
        # Auto-follow: the charts always show whichever lap the playhead is in (current vs best).
        # When the playhead crosses into a NEW lap (playing OR scrubbing), the speed + delta
        # charts switch to that lap, keeping the best lap as the reference overlay. We key off
        # the playhead's lap, so a single O(1) edge check per tick (in _apply_readout) drives it;
        # we only re-select on the actual lap CHANGE so it never thrashes. _followed_lap is the
        # lap the charts are currently following; seeded from _select_default below.
        self._followed_lap: int | None = None
        # F2: keep the sector boundary guide lines on the charts in sync. plots_view stays
        # pacer-free, so app computes the boundary x-positions via session for the current
        # axis mode and pushes them; recompute when the mode flips (the positions' units change).
        self.plots.modeChanged.connect(self._refresh_sector_lines)

        self._select_default()
        self._refresh_sector_lines()  # draw any sectors present on launch (none by default)
        self._sync_full_recording_action()

    # ----------------------------------------------------- multi-chapter UI / opt-in
    def _build_menu(self):
        """The opt-in UI action: File ▸ "Load full recording" discovers the sibling chapters of
        the currently-opened file and reloads the whole session as one chaptered recording.
        Disabled when there's nothing more to load (already multi-chapter, or no siblings on
        disk, or a non-GoPro clip)."""
        menu = self.menuBar().addMenu("&File")
        self._full_action = menu.addAction("Load full recording")
        self._full_action.setToolTip(
            "Discover this recording's sibling chapters and load them as one continuous session")
        self._full_action.triggered.connect(self._load_full_recording)

    def _sync_full_recording_action(self):
        """Enable "Load full recording" only when the current session is a SINGLE opened chapter
        that actually has sibling chapters on disk to chain (so the opt-in does something)."""
        can = False
        if len(self._paths) == 1:
            sibs = chapters.discover_siblings(self._paths[0])
            can = len(sibs) > 1
        self._full_action.setEnabled(can)

    def _load_full_recording(self):
        """Opt-in: chain the opened chapter's siblings into one full recording and reload."""
        if len(self._paths) != 1:
            return
        sibs = chapters.discover_siblings(self._paths[0])
        if len(sibs) > 1:
            print(f"studio: loading full recording — {len(sibs)} chapters.", flush=True)
            self._load(sibs)

    def _update_chapter_label(self, chapter_index: int):
        """Banner text: the recording label plus, for a chaptered session, the current chapter."""
        label = chapters.recording_label(self._paths)
        if self.video.is_multi:
            self.chapter_label.setText(f"{label}  —  chapter {chapter_index + 1} of "
                                       f"{len(self.session.chapters)}")
        else:
            self.chapter_label.setText(label)

    def _select_default(self):
        """Pre-select the two fastest laps so speed + a real delta-to-best show on launch.

        Also clears the auto-follow state: on launch nothing is "current" yet, and after a
        re-segmentation (_on_lines) the lap ids have shifted, so the next playhead movement must
        be free to re-establish the follow on the now-current lap (a stale id would suppress the
        edge). This multi-lap default overlay is simply replaced once the playhead enters a lap."""
        self._followed_lap = None
        rows = sorted(self.session.lap_rows(), key=lambda r: r["time"])
        ids = [r["idx"] for r in rows[:2]]
        self.table.select(ids)
        self._on_laps_selected(ids)

    def _on_user_select(self, ids):
        # A genuine user click in the lap table also jumps the video to that lap (F1).
        self._on_laps_selected(ids, seek=True)

    def _on_laps_selected(self, ids, seek=False):
        # The table multi-selection drives the PLOTS only; the map's current-lap overlay
        # follows the video position (and thus selection, since F1 seeks into the lap).
        self.plots.set_laps(ids)
        # F1 seeks ONLY on user selection — not on programmatic re-select from
        # _select_default()/_on_lines(), or dragging a timing line would yank the video.
        if seek and ids:
            # Seek a hair INTO the selected lap, not onto its exact start. Laps are contiguous
            # (lap N's finish == lap N+1's start) and the player quantizes the seek to whole ms
            # (setPosition takes int(seconds*1000)), so a seek to the exact boundary lands a few
            # tenths of a ms BELOW it — which then resolves to the PREVIOUS lap and makes the
            # ▶ marker / map / auto-follow jump back one lap (the reported "clicking a lap selects
            # a different lap" bug). Nudging by _LAP_SEEK_NUDGE_S (a few ms, imperceptible in a
            # ~70 s lap) guarantees the ms-quantized playback position lands INSIDE the lap, so
            # lap_at_time(position) == the lap the user clicked.
            target = self.session.laps.start_timestamp(min(ids)) + _LAP_SEEK_NUDGE_S
            self.video.seek(target)
            # Don't let the auto-follow collapse a just-made (possibly multi-lap) comparison the
            # instant the seek's positionChanged lands: seed _followed_lap to the lap the seek
            # resolves into, so the immediate post-seek tick is NOT an edge. The user keeps the
            # selected overlay while paused; once PLAYBACK MOVES ON into a different lap, the edge
            # fires and the charts collapse to [current, best] (the locked behaviour).
            self._followed_lap = self.session.lap_at_time(target)

    def _on_position(self, t: float):
        # Runs in the video event path — keep it trivial so frame presentation isn't starved.
        self._latest_t = t

    def _tick(self):
        # Steady ~30 Hz. While the user is scrubbing a plot cursor, the source of truth is the
        # drag, not playback: issue at most ONE coalesced seek per tick to the latest dragged
        # target, and DON'T apply the (stale / seek-driven) playback position — that gating is
        # what prevents the drag↔positionChanged feedback loop from oscillating.
        if self._scrub_target is not None:
            if self._scrub_pending:
                self._scrub_pending = False
                self.video.seek(self._scrub_target)  # PRIMARY pane
            # Compare mode: the same drag is distance-locked across both panes — fan the coalesced
            # seek out to the SECONDARY pane too (its own lap's global time, computed in _moved).
            if self._compare and self._scrub_pending_b:
                self._scrub_pending_b = False
                if self._scrub_target_b is not None:
                    self.video.seek_pane(1, self._scrub_target_b)
            self._compare_tick()  # keep the secondary g + Δ badges live while scrubbing
            return
        # Normal playback: apply an update only when the position actually advanced.
        if self._latest_t != self._applied_t:
            self._applied_t = self._latest_t
            self._apply_position(self._applied_t)
        # Compare mode: the secondary pane is video-only (no _on_position), so feed its g + update
        # both panes' Δ badges from its own current position here, every tick (O(1) np.interp).
        if self._compare:
            self._compare_tick()

    def _compare_tick(self):
        """Per-tick compare upkeep (O(1)): feed the SECONDARY pane its own-lap g, and refresh each
        pane's "Δ vs other" badge at that pane's current track position. The PRIMARY pane's g and
        telemetry are still driven by the single-valued _apply_readout path — this never touches
        _latest_t/_applied_t, so the primary telemetry stays exactly as today."""
        a, b = self._compare_a, self._compare_b
        if a is None or b is None:
            return
        t_a = self.video.current_pane_time(0)  # primary pane's global time
        t_b = self.video.current_pane_time(1)  # secondary pane's global time (read once)
        # Secondary g (the primary's g comes from _apply_readout). A no-op if the overlay is off.
        self.video.set_pane_g(1, self.session.g_at_time(t_b))
        # Each pane's Δ vs the OTHER lap, at that pane's own current track position.
        self._set_pane_badge(0, self.session.delta_between(a, b, t_a))
        self._set_pane_badge(1, self.session.delta_between(b, a, t_b))

    def _set_pane_badge(self, side: int, d: float | None):
        """Format + colour a pane's "Δ vs other" badge (+behind / −ahead vs the other pane's lap)."""
        if d is None:
            self.video.set_pane_badge(side, "Δ —", None)
        else:
            colour = theme.C.ahead if d <= 0 else theme.C.behind
            self.video.set_pane_badge(side, f"Δ {d:+.2f} s", colour)

    def _apply_position(self, t: float):
        self.map.set_marker_time(t)
        self.plots.set_cursor_time(t)
        self._apply_readout(t)

    def _apply_readout(self, t: float):
        lap_id = self.session.lap_at_time(t)  # F3: which lap is on the video
        self._follow_current_lap(lap_id, t)  # charts auto-follow the playhead's lap (vs best)
        self.table.set_current_lap(lap_id)
        self.map.set_current_lap(lap_id)  # highlight the current lap's trace on the map
        sp = self.session.speed_at_time(t)  # F2: time / speed / lap readout
        speed = f"{sp:.1f}" if sp is not None else "-"
        lap = lap_id if lap_id is not None else "-"
        self.video.set_readout(f"t = {fmt_time(t)}   speed = {speed} km/h   lap {lap}")
        self._update_diff_box(t, sp)
        # g-meter overlay: feed the vehicle-frame g at the current media time (a cheap lookup) and
        # the current lap (so the max-G envelope resets at the lap boundary, showing THIS lap's
        # grip). A no-op when the overlay is hidden; None outside a usable region blanks the dot.
        self.video.set_gmeter_lap(lap_id)
        self.video.set_g(self.session.g_at_time(t))

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
        if getattr(self, "_compare", False):
            return
        if lap_id is None or lap_id == self._followed_lap:
            return  # hold on no-lap regions; only act on a genuine change to a new valid lap
        self._followed_lap = lap_id
        # Keep the best lap as the reference overlay; current lap first so it's the primary curve.
        best = self.session.best_lap_id()
        ids = [lap_id] if best is None or best == lap_id else [lap_id, best]
        self.table.select(ids)   # programmatic (signals blocked) → no seek, won't fight playback
        self.plots.set_laps(ids)
        # During a scrub-across-boundary, set_laps→refresh re-places the cursor via set_cursor_time
        # which is a no-op mid-drag; re-place it from the dragged time so the cursor stays put in
        # the now-current lap (resolving the old "scrub dead off the displayed lap" caveat too).
        if self.plots.is_dragging():
            self.plots.place_cursors_at_time(t)

    def _update_diff_box(self, t: float, sp: float | None):
        """Refresh the always-on Δ/speed box for the current moment (priority: Δ-to-best in
        seconds). Δ comes from session.delta_at_time (same normalized-distance alignment as the
        delta plot, so the box and the cursor on the curve agree). Outside a valid lap Δ is —."""
        d = self.session.delta_at_time(t)
        if d is None:
            delta_txt = "Δ —"
        else:
            # +behind / −ahead vs best, at the same track position.
            delta_txt = f"Δ {d:+.2f} s"
        speed_txt = f"{sp:.0f} km/h" if sp is not None else "— km/h"
        # Colour cue: green when up on best (ahead), red when down (behind), primary text when no
        # delta. The card's surface bg / font / border come from the global QSS (#DiffBox); a
        # per-widget `color` rule merges over it and overrides ONLY the foreground (no per-tick
        # background/border re-layout cost), and only when the colour actually changes.
        colour = theme.C.text if d is None else (theme.C.ahead if d <= 0 else theme.C.behind)
        self.diff_box.setText(f"{delta_txt}     {speed_txt}")
        if colour != getattr(self, "_diff_colour", None):
            self._diff_colour = colour
            self.diff_box.setStyleSheet(f"QLabel#DiffBox {{ color: {colour}; }}")

    # ------------------------------------------------------------- plot scrub
    def _on_scrub_started(self):
        """Grab: scope the scrub to the lap the playhead is currently in (compare mode: to the
        pinned pair A/B); pause playback, remembering whether it was playing so we can resume on
        release."""
        # In compare mode the scrub is distance-locked to the pinned pair (the drag parks BOTH
        # panes on the same track position), not to a single playhead lap.
        self._scrub_lap = (self._compare_a if self._compare
                           else self.session.lap_at_time(self._applied_t or 0.0))
        self._scrub_was_playing = self.video.is_playing()
        if self._scrub_was_playing:
            self.video.pause()
        self._scrub_target = None
        self._scrub_pending = False
        self._scrub_target_b = None
        self._scrub_pending_b = False

    def _on_scrub_moved(self, x: float, mode: str):
        """Drag: convert the raw plot-x (in `mode`'s axis) to a media time within the captured
        current lap, clamped to that lap. Store it as the latest target (the tick coalesces the
        actual seek to ≤1/tick) and immediately re-place BOTH cursors + the map marker + the
        readout from that single clamped time, so the line snaps to the lap boundary and every
        view stays in sync without waiting on the (throttled, async) seek."""
        lap = self._scrub_lap
        if lap is None:  # not inside a valid lap (lead-in / between laps) — no-op
            return
        best_d = self.session.best_lap_total_distance()
        t = self.session.media_time_at_plot_x(lap, x, mode, best_distance=best_d)
        if t is None:
            return
        self._scrub_target = t
        self._scrub_pending = True
        if self._compare and self._compare_b is not None:
            # Distance-locked: the SAME dragged plot-x is a track position; convert it to the
            # SECONDARY lap's own global media time so both panes park on the same spot. Coalesced
            # to <=1 seek/tick via _scrub_pending_b (the secondary's gate), exactly like the primary.
            t_b = self.session.media_time_at_plot_x(self._compare_b, x, mode, best_distance=best_d)
            if t_b is not None:
                self._scrub_target_b = t_b
                self._scrub_pending_b = True
        # Drive every view from the one clamped truth (plots ignore the playback tick mid-drag).
        # The cursor on the charts follows the PRIMARY pane's lap position `t`.
        self.plots.place_cursors_at_time(t)
        self.map.set_marker_time(t)
        self._apply_readout(t)

    def _on_scrub_ended(self):
        """Release: issue a final seek to the last target (so the frame matches the cursor even
        if the last move coalesced out), resume playback iff it was playing at grab, then clear
        the scrub state so the normal playback→cursor sync resumes."""
        target = self._scrub_target
        target_b = self._scrub_target_b
        self._scrub_target = None
        self._scrub_pending = False
        self._scrub_target_b = None
        self._scrub_pending_b = False
        if target is not None:
            self.video.seek(target)  # PRIMARY pane
            self._applied_t = target  # keep current-lap/readout consistent until the seek lands
        if self._compare and target_b is not None:
            self.video.seek_pane(1, target_b)  # SECONDARY pane (final distance-locked park)
        if self._scrub_was_playing:
            self.video.play()  # fans out to both panes in compare mode
        self._scrub_was_playing = False
        self._scrub_lap = None

    # ------------------------------------------------------------- compare videos (Phase B)
    def _lap_caption(self, lap_id: int) -> str:
        """Per-pane caption "lap N · m:ss.mmm" for a lap id, marking the best lap with a ★, so the
        user can confirm which lap is loaded in each pane without opening the picker."""
        star = " ★" if lap_id == self.session.best_lap_id() else ""
        return f"lap {lap_id} · {fmt_time(self.session.laps.lap_time(lap_id))}{star}"

    def _lap_choice_labels(self, lap_ids: list[int]) -> list[str]:
        """Picker item labels "lap N  (m:ss.mmm)" (★ on the best lap) so picking the right lap
        doesn't require guessing. Parallel to `lap_ids`; computed once per (re)seed, not per tick."""
        best = self.session.best_lap_id()
        return [f"lap {lid}  ({fmt_time(self.session.laps.lap_time(lid))})"
                f"{'  ★' if lid == best else ''}" for lid in lap_ids]

    def _seek_pane_to_lap_start(self, side: int, lap_id: int):
        """Seek one pane to a hair INTO its lap (the _LAP_SEEK_NUDGE_S nudge keeps the ms-quantized
        position inside the lap, mirroring the lap-table seek), so it parks on the lap's start."""
        window = self.session.lap_window(lap_id)
        if window is not None:
            self.video.seek_pane(side, window[0] + _LAP_SEEK_NUDGE_S)

    def _reset_pair_to_start(self):
        """The user's main pain was getting both videos to start together. So EVERY change to the
        compared pair (enter-compare, either picker repoint, any pair change) ends here: PAUSE BOTH
        panes and re-seek BOTH to their lap's start line — not just the side that changed — so the
        two videos are always realigned at S/F and ready to roll together on the next Play. This
        clears any lingering mid-lap position on the untouched pane (the "one video mid-lap, the
        other at start" state). Pausing first so the seek lands on a stopped decoder; the primary's
        seek drives the chart cursor / map marker to the primary's S/F via the normal tick path."""
        a, b = self._compare_a, self._compare_b
        if a is None or b is None:
            return
        self.video.pause()  # fans out to BOTH panes; idempotent if already paused
        self._seek_pane_to_lap_start(0, a)  # PRIMARY -> its lap S/F
        self._seek_pane_to_lap_start(1, b)  # SECONDARY -> its lap S/F

    def _on_compare_toggled(self, on: bool):
        """The "Compare videos" toggle flipped. On enter: seed (A,B) = (current/primary lap, best)
        — default current-vs-best (if they coincide, pick the next-fastest as B) — build the two
        panes, seek each to its lap start, drive the chart overlay with [A,B], and SUSPEND
        auto-follow's lap re-point. On exit: restore the single pane, re-enable auto-follow, and
        restore the table-driven chart selection."""
        if on:
            self._enter_compare()
        else:
            self._exit_compare()

    def _enter_compare(self):
        valid = self.session.valid_lap_ids()
        if len(valid) < 2:
            return  # the toggle should be disabled, but guard anyway
        best = self.session.best_lap_id()
        # A = the lap the playhead is currently in, else the primary table selection, else best.
        a = self.session.lap_at_time(self._applied_t or 0.0)
        if a is None or a not in valid:
            sel = [lid for lid in self.plots._lap_ids if lid in valid]
            a = sel[0] if sel else (best if best in valid else valid[0])
        # B = best; if A already is best, pick the next-fastest valid lap as B.
        b = best if best is not None and best in valid else None
        if b is None or b == a:
            others = sorted((lid for lid in valid if lid != a),
                            key=self.session.laps.lap_time)
            b = others[0] if others else a
        self._compare = True
        self._compare_a, self._compare_b = a, b
        wa, wb = self.session.lap_window(a), self.session.lap_window(b)
        if wa is None or wb is None:
            self._compare = False
            return
        self.video.set_compare(a, b, wa, wb, self._lap_caption(a), self._lap_caption(b),
                                valid, self._lap_choice_labels(valid))
        # Each pane plays "time into lap": reset the pair to its lap starts, PAUSED, so both videos
        # are aligned at S/F and roll together on the next Play (no auto-play on enter).
        self._reset_pair_to_start()
        # The pair drives the chart overlay (A primary curve, B reference) and each pane's g scope.
        self.plots.set_laps([a, b])
        self.video.set_pane_gmeter_lap(0, a)
        self.video.set_pane_gmeter_lap(1, b)
        # Suspend auto-follow: freeze _followed_lap on A so the per-tick edge check never re-points
        # the charts while compare is on (also gated by self._compare in _follow_current_lap).
        self._followed_lap = a

    def _exit_compare(self):
        self._compare = False
        self._compare_a = self._compare_b = None
        self.video.exit_compare()
        # Restore the table-driven chart selection + re-enable auto-follow (a fresh edge will
        # re-establish the followed lap on the next playhead movement).
        self._followed_lap = None
        ids = self.table.selected_lap_ids()
        if ids:
            self.plots.set_laps(ids)
        else:
            self._select_default()

    def _on_pane_repoint(self, side: int, lap_id: int):
        """A pane's lap picker repointed that side to `lap_id`: re-seed its lap window + caption,
        re-seek it to the new lap start, and refresh the chart overlay + g scope. The OTHER pane is
        untouched. Drives the [A,B] pair that feeds the charts + the per-tick Δ badges."""
        if not self._compare:
            return
        valid = self.session.valid_lap_ids()
        if lap_id not in valid:
            return
        if side == 0:
            self._compare_a = lap_id
        else:
            self._compare_b = lap_id
        window = self.session.lap_window(lap_id)
        if window is None:
            return
        self.video.reseed_pane(side, lap_id, window, self._lap_caption(lap_id),
                               valid, self._lap_choice_labels(valid))
        self.video.set_pane_gmeter_lap(side, lap_id)
        # Realign the WHOLE pair at S/F, PAUSED — not just the side that changed — so the two
        # videos never end up "one mid-lap, the other at start". This clears the other pane's
        # lingering position too and leaves both ready to roll together on the next Play.
        self._reset_pair_to_start()
        # Refresh the chart overlay with the new pair; freeze auto-follow on the (new) primary lap.
        if self._compare_a is not None and self._compare_b is not None:
            self.plots.set_laps([self._compare_a, self._compare_b])
            self._followed_lap = self._compare_a

    def _refresh_sector_lines(self, mode: str | None = None):
        """F2: push the sector boundary positions (start/finish + each sector line) to the charts
        for the current axis mode. Computed via session (the s×best_distance / time-into-lap
        axis), so plots_view stays pacer-free. Called on launch, after a sector edit, and when
        the dist/time mode flips (positions' units change)."""
        mode = mode or self.plots.axis_mode()
        self.plots.set_sector_lines(self.session.sector_plot_positions(mode))

    def _on_lines(self, start, sectors):
        # Re-segmentation shifts lap ids, so any pinned compare pair is now stale — leave compare
        # mode first (also tears the 2nd pane down), then re-segment and rebuild the default view.
        if self._compare:
            self.video.set_compare_enabled(False)  # un-checks -> compareToggled(False) -> exit
        self.session.set_timing_lines(start, sectors)
        self.table.refresh()
        # Re-segmentation shifted lap ids + cleared the per-lap gap-fill cache; redraw the map
        # overlays so their measured/inferred segments match the new segmentation.
        self.map.refresh_overlays()
        self._select_default()
        # F2: the sector lines changed — update the chart guide lines live.
        self._refresh_sector_lines()
        # The valid-lap count may have changed — re-evaluate whether compare can be offered.
        self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    # Opt-in full-recording: discover & chain ALL sibling chapters of the single opened file.
    # DEFAULT (no flag) is unchanged — only the passed file(s) load. Explicit multiple paths
    # still chain exactly those, in order, regardless of the flag.
    full = "--full" in argv or "--chaptered" in argv
    paths = [a for a in argv if not a.startswith("-")] or [DEFAULT_SAMPLE]
    app = QApplication(sys.argv)
    # Apply the dark "Refined Minimal" design system BEFORE constructing any widgets, so the
    # default font/palette and the pyqtgraph background are in place when the panels are built.
    theme.register_fonts()
    theme.apply_theme(app)
    window = StudioWindow(paths, full=full)
    window.show()
    return app.exec()
