"""StudioWindow: assembles the panels and wires the cross-panel sync.

Layout (resizable splitters):
    ┌──────────────┬───────────────────────────┐
    │  VideoView   │   MapView (track + lines) │
    ├──────────────┼───────────────────────────┤
    │  LapTable    │   PlotsView (speed/delta) │
    └──────────────┴───────────────────────────┘
"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QBuffer, QEvent, QIODevice, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import chapters, export_data, export_video, library, sidecar, theme
from .coaching_panel import OpportunitiesDialog
from .compare_controller import CompareController
from .consistency_panel import ConsistencyPanel
from .help_dialog import AboutDialog, ShortcutsDialog
from .lap_table import CornerTable, LapTable
from .library_dialog import LibraryDialog
from .map_view import MapView
from .plots_view import PlotsView
from .scrub_controller import ScrubController
from .session import DEFAULT_SAMPLE, Session, fmt_time
from .video_view import VideoView


class _VideoExportWorker(QThread):
    """Runs an export_video.Renderer to completion on a worker thread, reporting frame progress
    and a final ok/message back to the GUI thread via queued signals (F9). Keeps the heavy
    decode/composite/mux loop OFF the UI thread so the progress dialog stays responsive and
    cancellable. The renderer is pacer-free + event-loop-free; this thin QThread is the only Qt
    threading glue, and it lives in app.py (not the renderer) so export_video stays GUI-agnostic.

    Cancellation: `cancel()` (called from the GUI thread when the dialog's Cancel is hit) flips a
    flag the render loop + its supervisor poll — a cooperative stop that tears the ffmpeg pipes
    down cleanly even if the loop is blocked mid-write (the supervisor kills the pipes to unblock
    it). The partial output file is removed so a cancel leaves nothing half-written.

    Never hangs: the renderer runs a no-progress WATCHDOG (see export_video.Renderer) — if a stage
    wedges (a stuck VideoToolbox session / pipe) and no frame is written for ~30 s, the export is
    aborted and surfaced as an error here, then retried once on the software encoder, rather than
    hanging the dialog forever."""

    progress = Signal(int, int)              # (frames_done, frames_total)
    finished_export = Signal(bool, str)      # (ok, message)  message="cancelled" / an error text

    def __init__(self, session, spec):
        super().__init__()
        self._session = session
        self._spec = spec
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def run(self):
        try:
            renderer = export_video.Renderer(self._session, self._spec)
            renderer.run(progress=lambda d, t: self.progress.emit(d, t),
                         cancel=lambda: self._cancelled)
            self.finished_export.emit(True, "")
        except export_video.CancelledError:
            self._cleanup_partial()
            self.finished_export.emit(False, "cancelled")
        except Exception as exc:  # surfaced in a dialog by the GUI thread
            self._cleanup_partial()
            self.finished_export.emit(False, str(exc))

    def _cleanup_partial(self):
        """Best-effort: drop a partially-written output so a cancel/error leaves no broken MP4."""
        try:
            if os.path.exists(self._spec.out_path):
                os.remove(self._spec.out_path)
        except OSError:
            pass


class StudioWindow(QMainWindow):
    def __init__(self, paths: list[str], full: bool = False):
        super().__init__()
        self.resize(1440, 900)
        self._tick_timer = None  # created on the first _build_ui; reused across reloads
        # F6: the consistency panel is HIDDEN by default; the View menu toggle (built below) flips
        # this. Held on the window (not the rebuilt central widget) so the user's choice survives a
        # reload. The check item + the panel's visibility are re-synced to it in _build_ui.
        self._consistency_visible = False
        self._build_menu()
        self._build_shortcuts()
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
        print("studio: loading telemetry…", flush=True)
        # Kill the "black-void launch": Session.load is a ~4 s SYNCHRONOUS call, and on the first
        # launch it runs before the window is ever shown — so the user stares at nothing for seconds
        # (and every "Load full recording" reload hard-freezes the window). Before blocking, show the
        # window with a lightweight centered "Loading telemetry…" placeholder and force ONE paint, so
        # there is always immediate visual feedback. Full threading of the load is out of scope; a
        # visible loading state is enough. Replaced by the real UI in _build_ui once the load returns.
        self._show_loading_placeholder(paths)
        # Guard the load: a missing / corrupt / no-GPS file must NOT crash the app on launch. On
        # failure show a clear error (the offending path + reason) and leave the window open so the
        # user can act, rather than letting the exception propagate out of __init__ and kill the app.
        try:
            session = Session.load(paths)
        except Exception as exc:  # noqa: BLE001 - surface ANY load failure as a user-facing error
            self._on_load_failed(paths, exc)
            return
        self.session = session
        # D2: commit _paths ONLY after a successful load. A failed RELOAD leaves self.session (the
        # still-good session) untouched, so _paths must stay pointing at that good recording too —
        # otherwise every _paths consumer (window title, the export source/label, "Load full
        # recording"/Library sync) silently desyncs from the loaded session, contradicting the
        # error dialog's "the previously loaded session is unchanged." On a successful first load or
        # reload, this is the correct new value; _on_load_failed seeds a value for a failed FIRST load.
        self._paths = list(paths)
        n_ch = len(self.session.chapters) if self.session.chapters else 1
        print(f"studio: {self.session.point_count()} points, "
              f"{self.session.lap_count()} laps, {n_ch} chapter(s).", flush=True)

        # Timing-line sidecar: restore the user's previously-saved start/sector lines (absolute
        # lat/lon, written ONLY on a user edit — see _save_sidecar) before the UI is built, so
        # every panel is constructed against the restored segmentation. A plain load never
        # WRITES the sidecar. A corrupt/foreign sidecar (its lines segment to zero valid laps)
        # is reverted to the auto-fitted lines with a non-fatal notice. The path is assigned
        # only after a SUCCESSFUL load, so a failed reload leaves the old session writing to
        # its own (old) sidecar.
        self._sidecar_path = sidecar.sidecar_path(paths[0]) if paths else None
        notice = None
        data = sidecar.load(self._sidecar_path) if self._sidecar_path else None
        # Apply the sidecar FIRST so the segmentation (and thus the valid-lap count the E1 check
        # below reads) is final before any notice is decided. A foreign sidecar that segments to
        # zero valid laps is reverted with its own notice — but the E1 0-lap check below overrides
        # it, as a 0-lap recording has no lap timing to fix either way.
        if data is not None:
            if session.apply_timing_lines_latlon(data["start"], data["sectors"]):
                print(f"studio: restored saved timing lines from "
                      f"{os.path.basename(self._sidecar_path)}", flush=True)
            else:
                notice = ("saved timing lines don't match this recording — "
                          "reverted to the auto-fitted start line")
        elif session.track_name is None and session.lap_count() > 0:
            # Unknown track (no registry match): the start line was auto-fitted (the
            # pick_random_start fallback in Session.load), not the real start/finish, so
            # lap times are arbitrary until the user drags it into place. One line, non-
            # fatal; suppressed when a sidecar restored the user's own lines above. To add
            # the track to the registry: studio/dev/print_track_entry.py.
            notice = ("unknown track — start/finish line was auto-fitted; "
                      "drag it into place to fix lap timing")

        # E1: a "successful" load with ZERO valid laps (short clip / no GPS lock / never-completed
        # lap) renders every panel blank — indistinguishable from a broken app. Surface a clear,
        # non-fatal notice (the in-panel empty states are added in LapTable/PlotsView). Highest
        # priority — a 0-lap recording has no lap timing to restore or drag into place, so this
        # message supersedes the sidecar-revert / unknown-track notices set above. Read AFTER the
        # sidecar apply so it reflects the FINAL segmentation.
        if not session.valid_lap_ids():
            notice = ("no complete laps detected in this recording — the GPS may not have "
                      "locked, or the recording is too short")

        label = chapters.recording_label(paths)
        self.setWindowTitle(f"pacer studio — {label}" if label else "pacer studio")
        self._build_ui()
        # One-line, non-fatal: the statusbar mirrors the console "studio:" notice style.
        if notice:
            print(f"studio: {notice}", flush=True)
            self.statusBar().showMessage(notice)
        else:
            self.statusBar().clearMessage()

        # F8 session library: record this recording in the local index (date / track / lap
        # count / best / theoretical / paths) for the Library… dialog + PB progression. Done
        # LAST — after the UI is built and shown — so it can never slow or risk the load; and
        # fully guarded so a failure to write the index (read-only app-support dir, disk full,
        # …) only logs a warning and never disrupts the app. A missing/empty index just starts
        # one; a corrupt index self-heals (library.load returns an empty index).
        self._update_library(paths)

    def _show_loading_placeholder(self, paths: list[str]):
        """Immediate visual feedback for the ~4 s blocking Session.load (the worst first impression
        was a black void / a frozen window during it). Install a centered "Loading telemetry…" card
        as the central widget, SHOW the window if it isn't visible yet, and force a single synchronous
        paint so it actually appears BEFORE the load blocks the event loop. Cheap and robust — no
        thread; the placeholder is replaced by the real UI in _build_ui when the load returns.

        Defensive: a unit-test StudioWindow.__new__'d without a QApplication has no app to paint —
        QApplication.instance() is then None, so the processEvents nudge is simply skipped."""
        label = chapters.recording_label(paths)
        placeholder = QLabel(f"Loading telemetry…\n\n{label}" if label else "Loading telemetry…")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setWordWrap(True)
        self.setCentralWidget(placeholder)
        if not self.isVisible():
            self.show()
        # Force one paint so the placeholder is on screen before Session.load blocks the loop. Without
        # this the setCentralWidget/show only schedule a paint that never runs until after the load.
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _on_load_failed(self, paths: list[str], exc: Exception):
        """A session load failed (missing / corrupt / no-GPS file). Show a clear, non-fatal error
        (offending path + reason) and keep the app open. If a session was already loaded (this was a
        reload, e.g. "Load full recording"), the working UI is LEFT INTACT — only the dialog shows.
        On the very first load there is no UI yet, so install a minimal empty-state placeholder so
        the window still opens (rather than crashing out of __init__)."""
        offending = paths[0] if paths else "(no file)"
        reason = f"{type(exc).__name__}: {exc}"
        print(f"studio: failed to load {offending}: {reason}", flush=True)
        QMessageBox.critical(
            self, "pacer studio — could not load recording",
            f"Could not load the recording:\n\n{offending}\n\n{reason}\n\n"
            "The file may be missing, corrupt, or contain no GPS data. "
            "The previously loaded session (if any) is unchanged.")
        # First-load failure: no central widget yet — show an empty state so the window stays open.
        if not hasattr(self, "session"):
            # D2: seed _paths for the FIRST-load-failure path only. _load no longer assigns _paths
            # before the guarded load, so on a failed first load nothing else has set it — readers
            # that stay reachable (the still-enabled "Load full recording" action) must find a
            # value. A failed RELOAD takes the branch below instead and leaves the good _paths intact.
            self._paths = list(paths)
            self.setWindowTitle("pacer studio — no recording loaded")
            placeholder = QLabel(
                "No recording loaded.\n\n"
                f"Could not load:\n{offending}\n\n{reason}")
            placeholder.setAlignment(Qt.AlignCenter)
            placeholder.setWordWrap(True)
            self.setCentralWidget(placeholder)

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
        # collapsed) so the lap table owns the whole table panel — the View ▸ "Show consistency
        # panel" check item (unchecked by default, wired in _build_menu) brings it back and refreshes
        # its stats. Hidden via setVisible(False), which drops it from the table panel's layout
        # entirely (the table stack keeps all the height), so the lap-table layout is intact.
        self.consistency.setVisible(self._consistency_visible)
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
        self.setCentralWidget(main)

        # Focus / maximize: double-clicking ANY panel's header strip toggles that quadrant to fill
        # the window (collapse the other splitter sections) and double-clicking again restores the
        # grid — "focus charts" / "focus video" for free, no new menus. The four panels + the
        # splitters they live in are stashed on the window so the handler (a) survives the
        # central-widget rebuild in _build_ui — a reload re-stashes fresh widgets — and (b) can map a
        # header back to its panel + outer column. Each panel's header is the FIRST child added in
        # _panel/_headered; we filter double-clicks on it. Any in-flight maximize is cleared on a
        # rebuild so the new grid starts un-maximized with its fresh default sizes.
        self._main_splitter = main
        self._left_splitter = left
        self._right_splitter = right
        self._video_panel = video_panel
        self._table_panel = table_panel
        self._map_panel = map_panel
        self._plots_panel = plots_panel
        self._maximized_panel = None          # the currently-maximized panel, or None
        self._saved_splitter_sizes = None     # (main, left, right) sizes captured at maximize
        # Fresh routing map for THIS build's headers (a reload's old headers belong to the disposed
        # tree and must not resolve); each _install_header_dblclick call adds one entry.
        self._header_routes = {}
        self._install_header_dblclick(video_panel, left, main)
        self._install_header_dblclick(table_panel, left, main)
        self._install_header_dblclick(map_panel, right, main)
        self._install_header_dblclick(plots_panel, right, main)

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
        # The map marker drag no longer emits a seek per mouse-move; the app's tick drains a
        # coalesced ONE-per-tick seek via map.take_marker_seek() (see _tick).
        self.map.timing_lines_changed.connect(self._on_lines)
        self.table.laps_selected.connect(self._on_user_select)

        # --- compare videos (Phase B) + plot-cursor scrub: the two heavy behavioural clusters ---
        # The compare-mode per-tick + enter/exit orchestration and the lap-scoped scrub coalescing
        # live in two injected, Qt-light, unit-testable collaborators (compare_controller.py /
        # scrub_controller.py). StudioWindow constructs + wires them and forwards signals + the
        # per-tick branches; the controllers OWN their state. Behaviour is byte-identical.
        #
        # Compare: two equal side-by-side video panes behind the explicit "Compare videos" toggle
        # (OFF by default, enabled only with >=2 valid laps). The PRIMARY (left) pane keeps driving
        # ALL telemetry exactly as today; the SECONDARY (right) pane is video-only. While compare
        # is on, auto-follow's lap re-point is SUSPENDED (the controller freezes _followed_lap via
        # the injected hook) so the pinned panes/charts don't thrash across lap boundaries.
        self.compare = CompareController(
            self.session, self.video, self.plots, self.table,
            set_followed_lap=self._set_followed_lap,
            select_default=self._select_default,
            get_applied_t=lambda: self._applied_t,
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
            get_applied_t=lambda: self._applied_t,
            set_applied_t=self._set_applied_t,
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
        # we only re-select on the actual lap CHANGE so it never thrashes. _followed_lap is the
        # lap the charts are currently following; seeded from _select_default below.
        self._followed_lap: int | None = None
        # F2: keep the sector boundary guide lines on the charts in sync. plots_view stays
        # pacer-free, so app computes the boundary x-positions via session for the current
        # axis mode and pushes them; recompute when the mode flips (the positions' units change).
        self.plots.modeChanged.connect(self._refresh_sector_lines)

        self._select_default()
        # Poster frame: seek the PRIMARY pane a hair into the best lap WHILE PAUSED so the (largest)
        # video quadrant shows a real frame at launch instead of a black void, and the map marker /
        # charts / readout are all populated and consistent with that frame. A paused seek decodes
        # and presents the frame without playing audio. Done after _select_default() so the chart
        # selection is already in place. Skipped cleanly when there's no valid lap (poster_seek
        # checks best_lap_id()), so a 0-lap session still launches.
        self._poster_seek()
        self._refresh_sector_lines()  # draw any sectors present on launch (none by default)
        self._sync_full_recording_action()
        # F7: the permanent status-bar chip showing which cross-recording reference is active.
        # Created ONCE (it lives on the persistent QMainWindow status bar, which _build_ui's
        # central-widget teardown doesn't touch) and hidden until a reference is loaded. A
        # primary reload builds a fresh Session with no reference, so re-sync (hide) it here.
        if getattr(self, "_ref_chip", None) is None:
            self._ref_chip = QLabel("")
            self._ref_chip.setProperty("role", "BarLabel")
            self.statusBar().addPermanentWidget(self._ref_chip)
        self._update_reference_status()

    # ----------------------------------------------------- panel focus / maximize
    def _install_header_dblclick(self, panel: QWidget, column: QSplitter, main: QSplitter):
        """Make a panel's HEADER strip double-click-to-maximize. The header is the first child of
        the panel's layout (the `_panel` text label or the `_headered`/`_header_bar` widget — both
        carry the `PanelHeader` role), so we install an event filter on it and route a double-click
        to `_toggle_panel_maximized`. We remember each header's (panel, column, main) routing in a
        per-build dict so eventFilter — one method for all four — knows which quadrant fired.

        Rebuilt-safe: _build_ui re-seeds an empty `_header_routes` before calling this for the
        fresh widgets each load, so a stale header from the disposed tree can never resolve.
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

        The saved sizes live on the WINDOW (not the rebuilt central widget); a fresh _build_ui
        resets _maximized_panel to None, so a reload always starts from the un-maximized grid."""
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

    # ----------------------------------------------------- menu bar / information architecture
    def _build_menu(self):
        """Build the menu bar: File / Analyse / View / Help. The IA splits the menus by INTENT —
        File owns getting recordings in and data out (Open, Open Recent, Load full recording,
        Export ▸, Export overlay video, Library), while Analyse gathers the comparison/coaching
        surface (reference recording load/clear, cross-recording compare, the Opportunities
        summary). View + Help are unchanged. Every action keeps its original handler + disabled-
        state sync — this method only regroups them and adds the Open Recent submenu.

        Also the opt-in multi-chapter action: File ▸ "Load full recording" discovers the sibling
        chapters of the currently-opened file and reloads the whole session as one chaptered
        recording (disabled when there's nothing more to load — already multi-chapter, no siblings
        on disk, or a non-GoPro clip)."""
        menu = self.menuBar().addMenu("&File")
        self._open_action = menu.addAction("Open…")
        self._open_action.setShortcut(QKeySequence.Open)
        self._open_action.triggered.connect(self._open_file)
        # Open Recent ▸ — re-open any recently analyzed recording straight from the menu, without a
        # trip through the Library dialog. Populated lazily from the session-library index on
        # aboutToShow (so it always reflects the latest loads + on-disk state); each entry re-opens
        # through the SAME guarded _load path the Library dialog uses. See _sync_recent_menu.
        self._recent_menu = menu.addMenu("Open Recent")
        self._recent_menu.aboutToShow.connect(self._sync_recent_menu)
        self._sync_recent_menu()  # seed it once so it's populated before its first open
        self._full_action = menu.addAction("Load full recording")
        self._full_action.setToolTip(
            "Discover this recording's sibling chapters and load them as one continuous session")
        self._full_action.triggered.connect(self._load_full_recording)
        # F11: File ▸ Export — the three data-export actions (writers in studio/export_data.py,
        # pacer-free; this menu owns the save dialogs + the report's widget grabs). Greyed out
        # until a session is loaded — the enabled state is synced when the File menu opens, so
        # the load path needn't know the menu exists. Nothing is EVER written without one of
        # these actions plus a confirmed save dialog (a plain load writes no export files).
        self._export_menu = menu.addMenu("Export")
        self._export_laps_action = self._export_menu.addAction("Lap times (CSV)…")
        self._export_laps_action.setToolTip(
            "One row per lap: time, distance, entry speed, sector splits, per-corner metrics")
        self._export_laps_action.triggered.connect(self._export_laps_csv)
        self._export_channels_action = self._export_menu.addAction("Lap channels (CSV)…")
        self._export_channels_action.setToolTip(
            "Per-sample channels of the selected lap: time, position, distance, speed, g")
        self._export_channels_action.triggered.connect(self._export_channels_csv)
        self._export_report_action = self._export_menu.addAction("Session report (HTML)…")
        self._export_report_action.setToolTip(
            "A one-page self-contained report: session stats, lap table, map + chart snapshots")
        self._export_report_action.triggered.connect(self._export_report)
        self._export_menu.setEnabled(False)  # no session yet at construction time
        menu.aboutToShow.connect(self._sync_export_menu)
        # F9 video-overlay export: a SEPARATE File-menu entry (not inside the data-export submenu)
        # that burns the telemetry overlays onto the footage → a shareable MP4. Deliberately a
        # dormant, additive menu entry — the whole renderer lives in studio/export_video.py and is
        # only reached through this action, so the feature can't touch the live app path. Greyed
        # out (and re-synced on aboutToShow) until a session is loaded.
        self._export_video_action = menu.addAction("Export overlay video…")
        self._export_video_action.setToolTip(
            "Render the selected lap with the on-screen overlays burned in (g-meter, Δ/speed, "
            "map inset, lap strip) to a shareable MP4")
        self._export_video_action.triggered.connect(self._export_overlay_video)
        self._export_video_action.setEnabled(False)
        # F8 session library: a local index of every analyzed recording (date / track / best /
        # theoretical) with per-track PB progression + quick re-open. The Open Recent submenu above
        # is the one-click fast path into this same index; the dialog is the full browse + chart.
        menu.addSeparator()
        self._library_action = menu.addAction("Library…")
        self._library_action.setToolTip(
            "Browse your analyzed recordings (date / track / best lap / theoretical best), "
            "re-open any of them, and see per-track PB progression")
        self._library_action.triggered.connect(self._open_library)

        # ----- Analyse menu: the comparison / coaching surface, grouped by INTENT (rather than
        # scattered through File). It collects the F7 cross-recording reference cluster (load /
        # clear / compare) and the F10 Opportunities summary. Built once on the persistent menu
        # bar; the actions keep their original handlers + disabled-state syncs (_update_reference_
        # status drives Clear / Compare enablement exactly as before — only the parent menu changed).
        analyse_menu = self.menuBar().addMenu("&Analyse")
        # F7 cross-recording reference: load ANOTHER recording and overlay/compare against its
        # best lap (race a friend's GoPro file).
        self._ref_action = analyse_menu.addAction("Load reference recording…")
        self._ref_action.setToolTip(
            "Pick another recording of the SAME track; its best lap becomes the Δ / map / table "
            "reference (instead of this session's own best lap)")
        self._ref_action.triggered.connect(self._load_reference_file)
        self._clear_ref_action = analyse_menu.addAction("Clear reference")
        self._clear_ref_action.setToolTip("Revert the Δ / map / table reference to this "
                                          "session's own best lap")
        self._clear_ref_action.triggered.connect(self._clear_reference)
        self._clear_ref_action.setEnabled(False)
        # F7 Phase B: cross-recording VIDEO compare — pane A = this recording's lap, pane B = the
        # reference recording's lap playing its OWN footage + telemetry. DISTINCT from the existing
        # same-recording "Compare videos" toggle (which compares two laps of THIS recording); that
        # toggle stays intact. Enabled only when a reference is loaded (synced in
        # _update_reference_status).
        self._cross_compare_action = analyse_menu.addAction("Compare vs reference recording")
        self._cross_compare_action.setToolTip(
            "Side-by-side: this recording's lap (left) vs the loaded reference recording's lap "
            "(right), each playing its own footage. Load a reference recording first.")
        self._cross_compare_action.triggered.connect(self._enter_cross_compare)
        self._cross_compare_action.setEnabled(False)
        # F10 auto coaching summary: the post-load "Opportunities" dialog — the top-3 corners by
        # realistic time lost vs your own best lap, each with the dominant measured reason + a
        # jump-to. Read-only; recomputed from the session each time it's opened. (Folded in from the
        # former single-item Coaching menu — it's an analysis surface, so it belongs here.)
        analyse_menu.addSeparator()
        self._opportunities_action = analyse_menu.addAction("Opportunities…")
        self._opportunities_action.setToolTip(
            "Where to find time vs your own best lap: the top-3 corners by realistic time lost "
            "(median of your clean laps), each with the measured reason and a jump-to.")
        self._opportunities_action.triggered.connect(self._open_opportunities)

        # F6: a View menu with the "Show consistency panel" check item (UNCHECKED by default — the
        # panel is hidden on launch). Toggling it shows/hides the consistency strip under the lap
        # table and refreshes its stats when shown. Lives on the persistent menu bar (untouched by
        # the central-widget rebuild), so the user's choice survives a reload; _build_ui re-syncs the
        # live panel's visibility to this item's state.
        view_menu = self.menuBar().addMenu("&View")
        self._consistency_action = view_menu.addAction("Show consistency panel")
        self._consistency_action.setCheckable(True)
        self._consistency_action.setChecked(self._consistency_visible)
        self._consistency_action.setToolTip(
            "Show the consistency strip under the lap table: the lap-time trend sparkline and the "
            "top-5 most inconsistent corners.")
        self._consistency_action.toggled.connect(self._on_consistency_toggled)

        # Help menu (rightmost): the discoverable surface for an otherwise-invisible interaction
        # model — the playback toggles, ±-stepping, chart-cursor scrub and the draggable map
        # start/finish line have no on-screen hint. Two read-only dialogs (studio/help_dialog.py):
        # the keyboard-shortcut reference (also bound to F1 / ? in _build_shortcuts) and an About
        # card. Additive — like the other menus it's built once on the persistent QMainWindow menu
        # bar, so it survives the central-widget rebuild and needs no per-load wiring.
        help_menu = self.menuBar().addMenu("&Help")
        self._shortcuts_action = help_menu.addAction("Keyboard shortcuts")
        self._shortcuts_action.setShortcut(QKeySequence(Qt.Key_F1))
        self._shortcuts_action.setToolTip(
            "List the keyboard shortcuts and the key drag interactions (chart scrub, start/finish "
            "line)")
        self._shortcuts_action.triggered.connect(self._show_shortcuts)
        self._about_action = help_menu.addAction("About pacer studio")
        self._about_action.setToolTip("What pacer studio is and what it does")
        self._about_action.triggered.connect(self._show_about)

    def _show_shortcuts(self):
        """Help ▸ Keyboard shortcuts (also F1 / ?): the themed, read-only shortcut reference.
        Built fresh + modal each time — it carries no app state, so there's nothing to refresh."""
        ShortcutsDialog(self).exec()

    def _show_about(self):
        """Help ▸ About pacer studio: the small themed About card (name / tagline / blurb)."""
        AboutDialog(self).exec()

    # ----------------------------------------------------- keyboard shortcuts
    def _build_shortcuts(self):
        """Window-level playback shortcuts: Space (play/pause), M (mute), G (g-meter overlay),
        C (compare mode). Created ONCE in __init__ and parented to the WINDOW, so they survive
        every central-widget rebuild.

        Handlers dereference `self.video` DYNAMICALLY (via _video_do) — _build_ui replaces the
        VideoView on every reload (File ▸ Open… / Load full recording), so capturing the widget
        at shortcut-creation time would leave the shortcuts driving a disposed player.

        The checkable toggles (G / C) go through their QPushButton's click() so the button's
        checked state, icon and QSS stay in sync with the keyboard path, and a DISABLED button
        (no IMU data / <2 valid laps) makes its shortcut a no-op for free (click() respects
        the enabled state). Space / M call the same VideoView methods the buttons are wired to.

        ←/→ stepping is deliberately NOT a QShortcut: a window-level shortcut CONSUMES the key
        before the focus widget sees it (verified offscreen), which would break lap-table row
        navigation. Arrows are handled in keyPressEvent instead, which only receives keys the
        focus widget did not use."""
        def shortcut(key, handler):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(handler)

        shortcut(Qt.Key_Space, lambda: self._video_do(lambda v: v.toggle()))
        shortcut(Qt.Key_M, lambda: self._video_do(lambda v: v.toggle_mute()))
        shortcut(Qt.Key_G, lambda: self._video_do(lambda v: v.gmeter_btn.click()))
        shortcut(Qt.Key_C, lambda: self._video_do(lambda v: v.compare_btn.click()))
        # ? opens the shortcut reference (F1 is set on the Help-menu action itself). The shortcut
        # text in that dialog (studio/help_dialog.py SHORTCUT_GROUPS) is the documented twin of
        # these bindings + the ←/→ stepping in keyPressEvent — keep them in sync.
        shortcut(Qt.Key_Question, self._show_shortcuts)

    def _video_do(self, fn):
        """Run `fn` against the CURRENT VideoView, resolved at ACTIVATION time (not capture
        time): _build_ui swaps self.video on reload. No-op before the first successful load
        (a failed first load leaves no `video` attribute — the shortcuts must not crash)."""
        video = getattr(self, "video", None)
        if video is not None:
            fn(video)

    def keyPressEvent(self, event):
        """←/→ step the video ±1 s (Shift: ±5 s), clamped + compare-aware via VideoView.step.

        Handled HERE rather than as QShortcuts: a window-level shortcut would consume the
        arrows before the focus widget sees them, breaking lap-table navigation. A key event
        reaches the main window only when the focus widget did NOT handle it — exactly the
        wanted policy: the lap table (and any combo box) keeps its own arrow navigation;
        everywhere else the arrows step the video. (The transport buttons + slider are
        Qt.NoFocus, so clicking them can't capture the arrows either.)"""
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            step = 5.0 if event.modifiers() & Qt.ShiftModifier else 1.0
            sign = 1.0 if event.key() == Qt.Key_Right else -1.0
            self._video_do(lambda v: v.step(sign * step))
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_file(self):
        """File ▸ Open…: pick a GoPro MP4 and reload through the guarded _load path."""
        start_dir = os.path.dirname(self._paths[0]) if getattr(self, "_paths", None) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Open recording", start_dir, "GoPro recordings (*.MP4 *.mp4)")
        if path:
            self._load([path])

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

    # ----------------------------------------------------------- session library (F8)
    def _update_library(self, paths: list[str]):
        """Upsert the just-loaded recording into the local session-library index. FULLY GUARDED:
        any failure (entry build, or an unwritable app-support dir) is swallowed with a warning
        — a library write must NEVER disrupt a load. Called post-UI from _load (see there).

        SKIP non-recordings: the bundled ``DEFAULT_SAMPLE`` (launched with no file) and any
        recording with no valid laps would otherwise persist a junk row (0 laps, null track) that
        the library dialog then surfaces forever. A library of *analyzed* sessions only wants rows
        with at least one real lap, so an empty/unsegmented open is not indexed."""
        if any(os.path.abspath(p) == os.path.abspath(DEFAULT_SAMPLE) for p in paths):
            return
        if not self.session.valid_lap_ids():
            return
        try:
            entry = self.session.library_entry(paths)
            library.upsert_and_save(entry)
        except Exception as exc:  # noqa: BLE001 — the index is additive; never break a load
            print(f"studio: session library not updated ({exc!r}).", flush=True)

    def _open_library(self):
        """File ▸ Library…: open the session-library dialog (a sortable list of analyzed
        recordings + per-track PB progression). Re-opening an entry routes back through the
        guarded `_load` path; the dialog reads the index defensively (empty when missing)."""
        dlg = LibraryDialog(library.load(), open_recording=self._load, parent=self)
        dlg.exec()

    # Open Recent: a handful of recently analyzed recordings (most-recent-first), each re-opening
    # through the SAME guarded `_load` path the Library dialog uses. The "recents" source is the
    # session-library index — re-using its single source of truth rather than tracking a second
    # MRU list — so a recording shows up the moment it's indexed (post-load) and disappears if its
    # file is later moved/deleted.
    _RECENT_LIMIT = 8

    def _recent_entries(self) -> list[dict]:
        """The Open Recent candidates: USABLE library entries (file present, real track + laps —
        the same "openable row" test the Library dialog applies), ordered most-recent-first by the
        recording date, capped at _RECENT_LIMIT. Junk rows (no track / no laps — e.g. the legacy
        bundled-sample row) and entries whose every chapter path is gone are skipped, since neither
        is openable. Fully guarded: any failure reading the index yields an empty list (the menu
        then shows its disabled "(none)" item) — Open Recent must never break the menu bar."""
        try:
            entries = library.load().get("entries", [])
        except Exception as exc:  # noqa: BLE001 — the recents list is additive; never break the menu
            print(f"studio: Open Recent unavailable ({exc!r}).", flush=True)
            return []
        usable = [
            e for e in entries
            # openable == has a real track + at least one lap (not a junk row) AND at least one
            # chapter path still on disk (any one is enough; _load discovers the siblings).
            if e.get("track") and e.get("lap_count")
            and any(os.path.exists(p) for p in (e.get("paths") or []))
        ]
        # Most-recent-first by recording date ("YYYY-MM-DD" sorts chronologically as text); a
        # missing date sorts last. Mirrors the Library dialog's default date-descending order.
        usable.sort(key=lambda e: e.get("date") or "", reverse=True)
        return usable[:self._RECENT_LIMIT]

    def _recent_label(self, entry: dict) -> str:
        """A one-line Open Recent label: ``<track> — <best>  (<date>)`` from a library entry,
        gracefully degrading when a field is absent (an unknown-track or undated row)."""
        track = entry.get("track") or "unknown track"
        best = entry.get("best")
        parts = [track]
        if best is not None:
            parts.append(f"— {fmt_time(best)}")
        date = entry.get("date")
        if date:
            parts.append(f"({date})")
        return "  ".join(parts)

    def _sync_recent_menu(self):
        """Rebuild the Open Recent submenu from the current library index. Called on the submenu's
        aboutToShow (so it always reflects the latest loads + on-disk state) and once at build time.
        Each entry re-opens via the guarded `_load` path with its recorded chapter paths. An empty
        recents list shows a single disabled "(none)" placeholder so the submenu is never blank."""
        self._recent_menu.clear()
        entries = self._recent_entries()
        if not entries:
            none_action = self._recent_menu.addAction("(none)")
            none_action.setEnabled(False)
            return
        for entry in entries:
            paths = list(entry.get("paths") or [])
            action = self._recent_menu.addAction(self._recent_label(entry))
            action.setToolTip(os.path.basename(paths[0]) if paths else "")
            # Bind THIS entry's paths into the slot (default-arg capture — a loop-closure over
            # `paths` would re-open whichever entry is last). Re-open through the same guarded
            # `_load` the Library dialog / File ▸ Open use, so the load guards + sidecar restore
            # + library upsert all apply identically.
            action.triggered.connect(lambda checked=False, p=paths: self._load(p))

    # -------------------------------------------------- auto coaching summary (F10)
    def _open_opportunities(self):
        """Analyse ▸ Opportunities…: open the read-only opportunities dialog, built from a
        FRESH session.coaching_opportunities() (recomputed each open — zero per-tick cost; the
        per-lap inputs it composes are already cached). The dialog handles its own friendly
        excluded state when there are too few clean laps. Each row's Go button routes to
        `_jump_to_opportunity` (corner select + best-lap entry seek). No-op if the FIRST load
        failed (no session yet) — defensive, like the export actions' enabled-state gate."""
        if getattr(self, "session", None) is None:
            return
        opps = self.session.coaching_opportunities()
        dlg = OpportunitiesDialog(opps, jump_to=self._jump_to_opportunity, parent=self)
        dlg.exec()

    def _jump_to_opportunity(self, cid: int, _entry_dist: float):
        """Jump-to for an opportunity row: select corner `cid` (map apex ring + the Corners view
        on the BEST lap) and seek the video to the BEST lap's ENTRY to that corner.

        Reuses the existing corner-select + seek paths: the best lap is selected in the lap
        table (so the Corners view + charts describe it, exactly like a user lap-click), the
        Corners view is shown, the map rings the apex (MapView.highlight_corner, the consistency
        panel's cue), and the video seeks to corner_entry_media_time(best, cid) — an absolute
        media time, fed straight to video.seek like the lap-select seek. No-op if there's no best
        lap or the corner/entry can't be resolved (a degenerate session)."""
        best = self.session.best_lap_id()
        if best is None:
            return
        # Select the best lap (programmatic select, NOT a user-select, so it doesn't re-enter the
        # seek-on-select path — we own the seek below, to the corner entry rather than the lap
        # start). This repoints the Corners view + charts onto the best lap.
        self.table.select([best])
        self._on_laps_selected([best])
        # Show the Corners view so the selected corner's per-corner row is visible (the toggle
        # also updates the table header). Idempotent if already in Corners mode.
        if not self.corners_btn.isChecked():
            self.corners_btn.setChecked(True)
        # Ring the corner's apex on the map (display-only cue, same as the consistency panel).
        self.map.highlight_corner(cid)
        # Seek the video to the best lap's entry to this corner.
        target = self.session.corner_entry_media_time(best, cid)
        if target is not None:
            self.video.seek(target)
            # Seed auto-follow to the lap the seek lands in, so the immediate post-seek tick
            # isn't treated as a lap-change edge (mirrors the lap-select seek's handling).
            self._followed_lap = self.session.lap_at_time(target)

    def _on_consistency_toggled(self, on: bool):
        """View ▸ "Show consistency panel": show/hide the consistency strip under the lap table.
        The choice is remembered on the window so it survives a reload. Showing it refreshes its
        stats first (it may have been built for an old session, or never shown). No-op before the
        first successful load (no panel yet)."""
        self._consistency_visible = bool(on)
        panel = getattr(self, "consistency", None)
        if panel is None:
            return
        if self._consistency_visible:
            panel.refresh()  # ensure the shown stats are current for this session
        panel.setVisible(self._consistency_visible)

    # ----------------------------------------------------------- data export (F11)
    # File ▸ Export: the writers live in studio/export_data.py (pacer-free, Qt-free); this
    # cluster owns the Qt side — enabled-state sync, the QFileDialog save prompts (cancel ⇒
    # nothing written), and the widget→PNG grabs for the report. Scoped to the File-menu
    # region: no other part of the app knows exports exist.
    def _sync_export_menu(self):
        """Grey the Export submenu + the video-export action out until a session is loaded.
        Connected to the File menu's aboutToShow (synced as the menu opens), so neither _load nor
        the failed-load path needs to reach into the menu."""
        has = hasattr(self, "session")
        self._export_menu.setEnabled(has)
        self._export_video_action.setEnabled(has)

    def _export_default(self, suffix: str) -> str:
        """Default save path: next to the recording, named `<stem><suffix>` (e.g.
        `GX010060_laps.csv`). Falls back to just the suffix-derived name in the CWD when
        nothing is loaded from a real path (the bundled sample)."""
        first = self._paths[0] if getattr(self, "_paths", None) else ""
        stem = os.path.splitext(os.path.basename(first))[0]
        return os.path.join(os.path.dirname(first), f"{stem}{suffix}")

    def _export_save_path(self, title: str, suffix: str, filt: str) -> str | None:
        """One save prompt; None when the user cancels (⇒ the caller writes nothing)."""
        path, _ = QFileDialog.getSaveFileName(self, title, self._export_default(suffix), filt)
        return path or None

    def _export_lap_id(self) -> int | None:
        """The lap the channels CSV describes: the PRIMARY selected/followed lap (the same
        lap the Corners view tracks), falling back to the best lap. None when the session
        has no usable lap at all."""
        lap = getattr(self, "_corner_lap", None)
        return lap if lap is not None else self.session.best_lap_id()

    def _export_laps_csv(self):
        if not hasattr(self, "session"):  # defensive: action fired with nothing loaded
            return
        path = self._export_save_path("Export lap times", "_laps.csv", "CSV files (*.csv)")
        if not path:
            return
        if self._run_export(lambda: export_data.write_laps_csv(path, self.session), path):
            self.statusBar().showMessage(f"exported {os.path.basename(path)}")

    def _export_channels_csv(self):
        if not hasattr(self, "session"):
            return
        lap = self._export_lap_id()
        if lap is None:
            self.statusBar().showMessage("no valid lap to export channels for")
            return
        path = self._export_save_path(f"Export lap {lap} channels",
                                      f"_lap{lap}_channels.csv", "CSV files (*.csv)")
        if not path:
            return
        if self._run_export(lambda: export_data.write_channels_csv(path, self.session, lap), path):
            self.statusBar().showMessage(f"exported {os.path.basename(path)}")

    def _export_report(self):
        if not hasattr(self, "session"):
            return
        path = self._export_save_path("Export session report", "_report.html",
                                      "HTML files (*.html)")
        if not path:
            return
        # Snapshot the map + charts as they are on screen right now (QWidget.grab) — the
        # report writer itself stays Qt-free and just embeds the bytes.
        images = [("Track map", self._grab_png(self.map)),
                  ("Speed · Δ to best", self._grab_png(self.plots))]
        if self._run_export(lambda: export_data.write_report_html(
                path, self.session,
                source_label=chapters.recording_label(self._paths) or "session",
                images=images), path):
            self.statusBar().showMessage(f"exported {os.path.basename(path)}")

    def _run_export(self, write, path: str) -> bool:
        """Run a data-export writer (`write()` — a 0-arg closure over the chosen path) under an
        OSError guard, mirroring the video-export error path: a read-only folder / full disk /
        removed volume must surface a user dialog, NOT throw a raw exception out of the triggered()
        slot (silent abort, or a crash on some Qt builds). Returns True on success so the caller
        shows its "exported …" status; on OSError shows a warning dialog + a statusbar note and
        returns False. The writers in studio/export_data.py stay Qt-free — the guard lives here."""
        try:
            write()
        except OSError as exc:
            QMessageBox.warning(self, "Export failed",
                                f"Could not write {os.path.basename(path)}:\n{exc}")
            self.statusBar().showMessage(f"export failed: {exc}")
            return False
        return True

    @staticmethod
    def _grab_png(widget) -> bytes:
        """Render a live widget to PNG bytes (QWidget.grab → QImage → in-memory PNG) for
        the report's embedded snapshots."""
        image = widget.grab().toImage()
        buf = QBuffer()
        buf.open(QIODevice.WriteOnly)
        image.save(buf, "PNG")
        return bytes(buf.data())

    # ------------------------------------------------- video-overlay export (F9)
    # File ▸ "Export overlay video…": render the selected lap with the overlays burned onto the
    # footage. The renderer (studio/export_video.py) is pacer-free and event-loop-free; this
    # cluster owns the Qt side only — the quality picker, the save dialog, a QThread so the render
    # runs OFF the UI thread, and a cancellable QProgressDialog. Nothing is written without the save
    # dialog.

    # Export-quality picker options. RESOLUTION maps to OverlayConfig.out_height (output_size never
    # upscales past the source, and clamps "Source" — a huge sentinel — back to the source height);
    # QUALITY maps to OverlayConfig.quality (the encoder bitrate/CRF knob). "Source" resolution +
    # "High" quality is the default (== the prior fixed behaviour at 1080p, but source-res aware).
    _EXPORT_RES_OPTIONS = [
        ("720p", 720), ("1080p", 1080), ("1440p", 1440), ("Source (no downscale)", 99999),
    ]
    # Quality combo labels spell out the trade-off (bitrate ⇒ file size) so the picker isn't a
    # bare "High / Standard" guess; the second tuple element is still the OverlayConfig.quality key.
    _EXPORT_QUALITY_OPTIONS = [
        ("High — larger file", "high"), ("Standard — smaller file", "standard"),
    ]

    def _ask_export_options(self, lap: int):
        """A small modal picker (resolution + quality) shown before the save dialog; returns an
        `export_video.OverlayConfig`, or None if the user cancels. The last choice is remembered on
        the window (`self._export_res_idx` / `self._export_quality_idx`) so a repeat export defaults
        to it. Two combos in a QDialog — lighter than a custom widget, and the only export-specific
        UI this feature adds. The chrome around the two combos — a flush PanelHeader, a one-line
        description of what gets burned in, the lap + its duration, and a live "what you'll get"
        resolution hint — exists so a flagship shareable-MP4 export doesn't read as a debug prompt;
        none of it touches the return contract."""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Export overlay video — lap {lap}")
        dlg.setMinimumWidth(400)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(0, 0, 0, 0)          # the header strip runs flush to the edges …
        root.setSpacing(0)

        # Flush PanelHeader strip naming the lap — same surface bg + hairline as every panel/dialog
        # header, so the modal sits inside the app's visual language rather than as a bare OS dialog.
        header = QLabel(f"Export overlay video — lap {lap}")
        header.setProperty("role", "PanelHeader")
        root.addWidget(header)

        # Body wrapper carries the comfortable padding (the header is full-bleed above it).
        body = QWidget(dlg)
        col = QVBoxLayout(body)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(10)
        root.addWidget(body)

        # One-line description of what's burned into the footage (the overlays the renderer paints).
        desc = QLabel("Burns the overlays into your footage: g-meter, Δ / speed, map inset and the "
                      "lap strip.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.C.text_dim};")
        col.addWidget(desc)

        # Which lap + its length, so the export's scope is unambiguous. lap_time is a cheap pacer-free
        # accessor (no ffprobe), so the duration is free here; fall back gracefully if it's missing.
        dur = self.session.lap_time(lap) if hasattr(self, "session") else float("nan")
        lap_line = QLabel(f"Lap {lap}  ·  {fmt_time(dur)}")
        lap_line.setStyleSheet(f"color: {theme.C.text_dim};")
        col.addWidget(lap_line)

        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(8)
        res_combo = QComboBox(dlg)
        for label, _h in self._EXPORT_RES_OPTIONS:
            res_combo.addItem(label)
        res_combo.setCurrentIndex(getattr(self, "_export_res_idx", 1))   # default 1080p
        q_combo = QComboBox(dlg)
        for label, _q in self._EXPORT_QUALITY_OPTIONS:
            q_combo.addItem(label)
        q_combo.setCurrentIndex(getattr(self, "_export_quality_idx", 0))  # default High
        form.addRow("Resolution", res_combo)
        form.addRow("Quality", q_combo)
        col.addLayout(form)

        # Live "what you'll get" hint. We don't ffprobe the source here (too heavy for a picker), so
        # the hint states the TARGET height and the never-upscale rule rather than exact pixels —
        # the same contract output_size() enforces ("Source" / a target above the source keeps the
        # native resolution; nothing is ever upscaled).
        hint = QLabel("")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.C.text_muted};")
        col.addWidget(hint)

        def _update_hint():
            h = self._EXPORT_RES_OPTIONS[res_combo.currentIndex()][1]
            if h >= 99999:
                hint.setText("Output: source resolution (never upscaled).")
            else:
                hint.setText(f"Output: up to {h}p tall, source aspect — never upscaled past source.")
        res_combo.currentIndexChanged.connect(_update_hint)
        _update_hint()

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, dlg)
        buttons.button(QDialogButtonBox.Ok).setText("Export")
        buttons.accepted.connect(dlg.accept)
        buttons.rejected.connect(dlg.reject)
        col.addWidget(buttons)
        if dlg.exec() != QDialog.Accepted:
            return None
        ri, qi = res_combo.currentIndex(), q_combo.currentIndex()
        self._export_res_idx, self._export_quality_idx = ri, qi   # remember for next time
        out_height = self._EXPORT_RES_OPTIONS[ri][1]
        quality = self._EXPORT_QUALITY_OPTIONS[qi][1]
        return export_video.OverlayConfig(out_height=out_height, quality=quality)

    def _export_overlay_video(self):
        if not hasattr(self, "session"):
            return
        if not export_video.ffmpeg_available():
            QMessageBox.warning(self, "Export overlay video",
                                "ffmpeg was not found. The video export needs ffmpeg/ffprobe on "
                                "PATH (they ship with the pixi environment).")
            return
        src = self._paths[0] if getattr(self, "_paths", None) else ""
        if not src or not os.path.exists(src):
            QMessageBox.warning(self, "Export overlay video",
                                "This session has no source video file to render onto.")
            return
        lap = self._export_lap_id()  # the primary/selected lap, falling back to the best lap
        win = export_video.lap_window_for_export(self.session, lap) if lap is not None else None
        if win is None:
            self.statusBar().showMessage("no usable lap to export video for")
            return
        # Pick resolution + quality FIRST (so a cancel here writes nothing), then the save path.
        config = self._ask_export_options(lap)
        if config is None:
            return
        out = self._export_save_path(f"Export overlay video — lap {lap}",
                                     f"_lap{lap}_overlay.mp4", "MP4 video (*.mp4)")
        if not out:
            return
        # Build the spec with the VIDEO SOURCE resolved from the session's chapters: a lap's GLOBAL
        # window is mapped to the correct chapter file (or a concat over the chapters a seam-crossing
        # lap spans) + the file-LOCAL seek, the SAME global<->local mapping the player seeks with. A
        # bad/empty/past-end window is refused here with a clear message rather than launching a
        # doomed ffmpeg (the chaptered-export 'empty bar' fix). The worker owns the spec's source
        # lifecycle (it cleans up any temp concat list when the render ends). The picked
        # `config` carries the chosen out_height + quality.
        try:
            spec = export_video.build_lap_spec(self.session, out, lap, config=config)
        except ValueError as exc:
            QMessageBox.warning(self, "Export overlay video",
                                f"This lap can't be exported:\n{exc}")
            return
        self._run_video_export(spec, lap)

    def _run_video_export(self, spec, lap: int):
        """Run the render on a worker QThread behind a cancellable modal progress dialog. The
        dialog's Cancel sets the worker's flag (polled by the render loop's `cancel` callback);
        the worker reports (done, total) frames back to the dialog over a queued signal.

        The dialog starts in a "Preparing…" BUSY state (an indeterminate 0/0 bar + a label) so the
        setup phase — ffprobe, the encoder probe, ffmpeg launch, the first decoded frame — never
        reads as a silent, stuck empty bar; it flips to a real 0→total determinate bar the moment
        the first frame's progress arrives."""
        dlg = QProgressDialog(f"Preparing lap {lap} overlay video…", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Export overlay video")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)  # with max=0 too, Qt renders an indeterminate "busy" bar

        worker = _VideoExportWorker(self.session, spec)
        self._video_worker = worker  # keep a ref so the thread isn't GC'd mid-render
        started = {"first": False}

        def on_progress(done: int, total: int):
            if total > 0:
                if not started["first"]:
                    # First real frame: switch from the busy "Preparing…" bar to a determinate one.
                    started["first"] = True
                    dlg.setLabelText(f"Rendering lap {lap} overlay video…")
                dlg.setMaximum(total)
                dlg.setValue(done)

        def on_done(ok: bool, message: str):
            dlg.reset()
            worker.wait()
            self._video_worker = None
            spec.source.cleanup()  # free any temp concat-list file the chapter resolution wrote
            if ok:
                self.statusBar().showMessage(f"exported {os.path.basename(spec.out_path)}")
            elif message == "cancelled":
                self.statusBar().showMessage("video export cancelled")
            else:
                QMessageBox.warning(self, "Export overlay video",
                                    f"The render failed:\n{message}")

        worker.progress.connect(on_progress)
        worker.finished_export.connect(on_done)
        dlg.canceled.connect(worker.cancel)
        worker.start()
        dlg.exec()

    # ----------------------------------------------- cross-recording reference (F7)
    def _load_reference_file(self):
        """File ▸ "Load reference recording…": pick another recording (same track) whose best lap
        becomes the Δ / map / table reference. The chapters of the picked file are chained (like
        "Load full recording") so the reference is the whole recording, then handed to
        Session.load_reference. On a guard refusal (different track / no valid laps) the local best
        lap is kept and the reason is shown — non-fatal."""
        if not hasattr(self, "session"):
            return
        start_dir = os.path.dirname(self._paths[0]) if getattr(self, "_paths", None) else ""
        path, _ = QFileDialog.getOpenFileName(
            self, "Load reference recording", start_dir, "GoPro recordings (*.MP4 *.mp4)")
        if not path:
            return
        paths = chapters.discover_siblings(path)
        print(f"studio: loading reference recording — {len(paths)} chapter(s)…", flush=True)
        reason = self.session.load_reference(paths)
        if reason is not None:
            print(f"studio: reference not loaded — {reason}", flush=True)
            QMessageBox.information(self, "pacer studio — reference not loaded", reason)
            return
        self._apply_reference_change()

    def _clear_reference(self):
        """File ▸ "Clear reference": drop the cross-recording reference; everything reverts to the
        session's own best lap (the dormant state)."""
        if not hasattr(self, "session") or not self.session.has_reference():
            return
        self.session.clear_reference()
        # D5: the reference is gone — drop the sticky "prefer cross-recording compare" preference so
        # a later compare toggle enters SAME-recording compare (there's no reference to compare to).
        if hasattr(self, "compare"):
            self.compare.clear_prefer_cross()
        self._apply_reference_change()

    def _enter_cross_compare(self):
        """File ▸ "Compare vs reference recording" (F7 Phase B): enter the cross-recording video
        compare — pane A = this recording's current/selected lap, pane B = the reference
        recording's lap, each playing its own footage. No-op (with a notice) if no reference
        recording is loaded. The existing same-recording "Compare videos" toggle is unaffected; the
        compare button reflects the two-pane stage either way."""
        if not hasattr(self, "session") or self.session.reference_session() is None:
            QMessageBox.information(
                self, "pacer studio — no reference recording",
                "Load a reference recording first (File ▸ Load reference recording…), then "
                "compare against it.")
            return
        if not self.compare.enter_cross():
            QMessageBox.information(
                self, "pacer studio — cross-recording compare unavailable",
                "The reference recording's lap could not be set up for compare.")

    def _apply_reference_change(self):
        """Refresh every "vs best" surface after the reference was loaded OR cleared, and update
        the menu + status chip. The reference replaces the local best lap as the Δ / map-overlay /
        sector-guide / per-corner-Δ baseline, so the same panels a re-segment refreshes are
        rebuilt here (minus the actual re-segmentation — the PRIMARY laps are unchanged)."""
        # The lap table's per-corner Δ columns + the Corners view read against the (now changed)
        # baseline; the map's faint reference line switches to/from the reference racing line.
        self.table.refresh()
        self.corner_table.refresh()
        self.map.refresh_overlays()
        # Rebuild the delta/speed charts (the baseline curve + the x-axis scale changed) and the
        # sector guide lines on the rescaled axis.
        if self._comparing():
            # Compare mode draws its own pinned [A,B] pair; just refresh its overlay in place.
            self.plots.refresh()
        else:
            self._select_default()
        self._refresh_sector_lines()
        self._update_reference_status()

    def _update_reference_status(self):
        """Reflect the active reference in the menu (enable Clear) + the permanent status-bar chip
        (the persistent which-reference-is-active indicator). Dormant: the chip is hidden and the
        statusbar is exactly as before."""
        active = hasattr(self, "session") and self.session.has_reference()
        if hasattr(self, "_clear_ref_action"):
            self._clear_ref_action.setEnabled(active)
        # F7 Phase B: the cross-recording video compare needs both a reference AND its retained live
        # Session (Phase A could load a data-only reference; the compare needs the footage). Enable
        # only when both are present.
        if hasattr(self, "_cross_compare_action"):
            can_cross = active and self.session.reference_session() is not None
            self._cross_compare_action.setEnabled(can_cross)
        chip = getattr(self, "_ref_chip", None)
        if chip is None:
            return
        if active:
            chip.setText(f"  ▶ reference: {self.session.reference_label()}  ")
            chip.setVisible(True)
        else:
            chip.setVisible(False)

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

    def _poster_seek(self):
        """Park the PRIMARY video pane on the best lap's first frame at launch (and after a reload),
        so the largest quadrant isn't a black void before the user touches anything — and so the
        map marker / charts / hero readout all reflect a real moment INSIDE a lap (not lead-in).

        Seek a hair INTO the lap (theme.LAP_SEEK_NUDGE_S past its start) for the same reason
        _on_laps_selected does — a seek to the exact contiguous-lap boundary ms-quantizes a touch
        below it and resolves to the PREVIOUS lap. The pane is freshly constructed and never played,
        so it is already paused; the seek decodes + presents the frame without starting playback or
        audio. Seed _applied_t so the very next tick's "did the position advance" check sees the
        poster position as already-applied (the readout/marker are driven directly here).

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
        self._latest_t = target
        self._applied_t = target
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
        Defensive getattrs: a StudioWindow.__new__'d for a unit test drives
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
        # lap). Defensive: a __new__'d test window without the views has no map/plots to push to.
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
            # instant the seek's positionChanged lands: seed _followed_lap to the lap the seek
            # resolves into, so the immediate post-seek tick is NOT an edge. The user keeps the
            # selected overlay while paused; once PLAYBACK MOVES ON into a different lap, the edge
            # fires and the charts collapse to [current, best] (the locked behaviour).
            self._followed_lap = self.session.lap_at_time(target)

    def _on_position(self, t: float):
        # Runs in the video event path — keep it trivial so frame presentation isn't starved.
        self._latest_t = t

    def _tick(self):
        # Steady ~30 Hz. Drain a coalesced MAP MARKER-DRAG seek first (one per tick, not per
        # mouse-move): the marker stashes its latest dragged time and the resulting seek drives the
        # normal playback→tick sync that re-places the marker/cursor/readout.
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
        if self._latest_t != self._applied_t:
            self._applied_t = self._latest_t
            self._apply_position(self._applied_t)
        # Compare mode: the secondary pane is video-only (no _on_position), so feed its g + update
        # both panes' Δ badges from its own current position here, every tick (O(1) np.interp).
        if self.compare.active:
            self.compare.tick()

    def _set_followed_lap(self, lap_id: int | None):
        """Setter for the auto-follow state, injected into the compare controller (entering/leaving
        compare freezes/clears _followed_lap). Auto-follow itself stays on StudioWindow (it's part
        of the playback tick path); the compare controller only needs to nudge this one field."""
        self._followed_lap = lap_id

    def _set_applied_t(self, t: float | None):
        """Setter for the playback position cursor, injected into the scrub controller (on release
        it seeds _applied_t to the final target so current-lap/readout stay consistent until the
        seek lands). The tick loop still owns the normal _latest_t→_applied_t advance."""
        self._applied_t = t

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
        if lap_id is None or lap_id == self._followed_lap:
            return  # hold on no-lap regions; only act on a genuine change to a new valid lap
        self._followed_lap = lap_id
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
        if d is None:
            delta_txt = "Δ —"
        else:
            # +behind / −ahead vs best, at the same track position.
            delta_txt = f"Δ {d:+.2f} s"
        # Honest no-lap state: outside a valid lap (lead-in / between laps / cool-down) the hero box
        # reads "— km/h" rather than a misleading lead-in speed (the old "12 km/h at t=0" first
        # impression). A real speed is shown only WHILE a lap is current. With the launch poster-seek
        # landing inside the best lap, t=0 now shows the best lap's real speed instead of "—".
        speed_txt = f"{sp:.0f} km/h" if (sp is not None and lap_id is not None) else "— km/h"
        # Colour cue: the shared three-way rule (theme.delta_colour) — green when meaningfully up
        # on best, red when down, and the primary text colour when there's no delta OR it's dead
        # even (|Δ| within ±theme.DELTA_EVEN_EPS_S; an exact 0 used to read GREEN). The card's
        # surface bg / font / border come from the global QSS (#DiffBox); a per-widget `color`
        # rule merges over it and overrides ONLY the foreground (no per-tick background/border
        # re-layout cost), and only when the colour actually changes.
        colour = theme.delta_colour(d) or theme.C.text
        self.diff_box.setText(f"{delta_txt}     {speed_txt}")
        if colour != getattr(self, "_diff_colour", None):
            self._diff_colour = colour
            self.diff_box.setStyleSheet(f"QLabel#DiffBox {{ color: {colour}; }}")

    # ------------------------------------------------------------- compare-state access
    # The scrub + compare behavioural clusters live in self.scrub / self.compare (constructed in
    # _build_ui). StudioWindow keeps the shared single-driver telemetry path (_apply_readout /
    # _update_diff_box) and the auto-follow tick logic (_follow_current_lap / _followed_lap), both
    # of which gate on whether compare is on — funnel that through one defensive accessor.
    def _comparing(self) -> bool:
        """True iff compare mode is on. Defensive: tolerates the controller not being constructed
        yet (e.g. a StudioWindow.__new__'d for a unit test that drives _follow_current_lap directly
        without building the UI) — mirrors the old `getattr(self, "_compare", False)` guard."""
        compare = getattr(self, "compare", None)
        return compare is not None and compare.active

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

    def _on_lines(self, start, sectors):
        # Re-segmentation shifts lap ids, so any pinned compare pair is now stale — leave compare
        # mode first (also tears the 2nd pane down), then re-segment and rebuild the default view.
        if self._comparing():
            self.video.set_compare_enabled(False)  # un-checks -> compareToggled(False) -> exit
        self.session.set_timing_lines(start, sectors)
        self.table.refresh()
        # Re-segmentation shifted lap ids + cleared the per-lap gap-fill cache; redraw the map
        # overlays so their measured/inferred segments match the new segmentation.
        self.map.refresh_overlays()
        # The corner model was invalidated with the per-lap caches: re-detect + re-push the
        # map's corner labels and rebuild the Corners view (its lap id is range-guarded; the
        # _select_default below re-points it at the new selection).
        self.map.set_corners(self.session.corner_map_markers())
        self.corner_table.refresh()
        # F6: lap set / splits / corner stats all shifted with the segmentation — rebuild the
        # consistency strip (set_corners above already cleared any stale corner highlight).
        self.consistency.refresh()
        # F5: the driving channels were invalidated with the corner model; re-push the brake
        # glyphs / coast bands. _select_default below re-points the primary lap, but its id may
        # be unchanged across the re-segment (so _set_corner_lap would early-out) while the
        # underlying channels DID change — so refresh explicitly here, mirroring corner_table.
        self._refresh_driving_channels()
        self._select_default()
        # F2: the sector lines changed — update the chart guide lines live.
        self._refresh_sector_lines()
        # The valid-lap count may have changed — re-evaluate whether compare can be offered.
        self.video.set_compare_enabled(len(self.session.valid_lap_ids()) >= 2)
        # Persist the user's edit (this handler fires only on a drag release / sector add or
        # reset — never on a plain load), so the hand-tuned lines survive an app restart.
        self._save_sidecar()

    def _save_sidecar(self):
        """Write the current timing lines to the recording's sidecar JSON (absolute lat/lon
        via session.timing_lines_latlon). Called ONLY from _on_lines — i.e. only on a genuine
        user edit — so an untouched session never creates or rewrites the file. Best-effort:
        an unwritable folder just logs and the lines still apply for this run."""
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
