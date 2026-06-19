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

from PySide6.QtCore import QBuffer, QIODevice, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from . import chapters, export_data, export_video, library, sidecar, theme
from .central_view import CentralView
from .coaching_panel import OpportunitiesDialog
from .help_dialog import AboutDialog, ShortcutsDialog
from .library_dialog import LibraryDialog
from .session import DEFAULT_SAMPLE, Session, fmt_time


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
        # F7: the ONE session-scoped central view (a fresh CentralView per load, swapped in
        # atomically by _build_ui). None until the first successful load — the persistent chrome
        # (shortcuts / tick / menus) all guard on it being present and reach session-scoped widgets
        # THROUGH it (self.view.video / self.view.session / …), the single swap point.
        self.view = None
        self._tick_timer = None  # created on the first _build_ui; reused across reloads (window-owned)
        # F6: the consistency panel is HIDDEN by default; the View menu toggle (built below) flips
        # this. Held on the WINDOW (not the swapped-in central view) so the user's choice survives a
        # reload; it is passed into each fresh CentralView, which applies it to the new panel.
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
        """Load (or reload) the session for `paths`, then (F7) build a FRESH CentralView and swap it
        in atomically (_build_ui). Used at startup and by the "Load full recording" action (which
        reloads with the discovered sibling chapters). The window keeps the load orchestration +
        `session`/`_paths`; each panel captures `session` at construction, so building a brand-new
        view per load keeps them simple and free of stale references."""
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

    def _build_ui(self):
        """F7 atomic swap: build a FRESH CentralView for the just-loaded session and install it as
        the central widget in one move. This replaced the old ~230-line in-place teardown/rebuild
        (`_construct_panels`/`_layout_panels`/`_wire_signals`/`_build_controllers` + the maximize
        machinery) — all of that session-scoped construction now lives atomically inside
        CentralView.__init__, so there is no longer a window-side rebuild that can leave a reference
        stale mid-flight. The window keeps ONLY the persistent chrome work here (the tick timer, the
        statusbar ref-chip, the "Load full recording" enablement) which lives on the QMainWindow and
        survives the swap.

        Reload ordering preserved EXACTLY: dispose the OUTGOING view FIRST (its VideoView.stop_all
        stops the decoder + closes the g-meter overlay window) BEFORE the new view is constructed and
        swapped in — the same "old video stop_all + g-meter overlay close before the central widget
        is replaced" the old reload did. CentralView.__init__ then runs the identical seed sequence
        (rebuild_derived_views(reselect=True) → poster seek → consistency-visible re-sync); the
        chrome-side seed (ref-chip + _sync_full_recording_action + _update_reference_status) runs
        below, after the swap, just as before."""
        old_view = getattr(self, "view", None)
        if old_view is not None:
            old_view.dispose()  # stop the old decoder + close its g-meter overlay before the swap
        # Build the new session-scoped view atomically (panels + controllers + PlaybackState + the
        # derived-views seed + poster seek all happen in its __init__), then swap it in as ONE unit.
        # The window keeps the canonical session / _paths; the view holds a read alias + the paths
        # (banner) + the sidecar path (timing-line save), per the documented session/_paths split.
        self.view = CentralView(self.session, self._paths, self._sidecar_path,
                                self._consistency_visible, parent=self)
        self.setCentralWidget(self.view)
        # One ~30 Hz tick timer for the WINDOW's lifetime; created once and REUSED across reloads (a
        # second timer would double the tick rate). It delegates to the current view's tick() — the
        # swap above just re-points which view that is. Kept on the persistent window (not the view)
        # so it is never torn down by a reload.
        if self._tick_timer is None:
            self._tick_timer = QTimer(self)
            self._tick_timer.setInterval(33)  # ~30 Hz
            self._tick_timer.timeout.connect(self._tick)
            self._tick_timer.start()

        self._sync_full_recording_action()
        # F7: the permanent status-bar chip showing which cross-recording reference is active.
        # Created ONCE (it lives on the persistent QMainWindow status bar, which the central-widget
        # swap doesn't touch) and hidden until a reference is loaded. A primary reload builds a fresh
        # Session with no reference, so re-sync (hide) it here.
        if getattr(self, "_ref_chip", None) is None:
            self._ref_chip = QLabel("")
            self._ref_chip.setProperty("role", "BarLabel")
            self.statusBar().addPermanentWidget(self._ref_chip)
        self._update_reference_status()

    def _tick(self):
        """The persistent ~30 Hz timer's slot — delegates the per-frame drain/scrub/apply/compare
        work to the CURRENT session-scoped view (CentralView.tick). The timer lives on the window
        and is reused across reloads; only `self.view` changes on a swap, so this always drives the
        live view. Defensive no-op before the first successful load (no view yet — a failed first
        load leaves none)."""
        view = getattr(self, "view", None)
        if view is not None:
            view.tick()

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
        every central-view swap.

        Handlers dereference the CURRENT video DYNAMICALLY (via _video_do → self.view.video) —
        _build_ui swaps in a fresh CentralView (with a fresh VideoView) on every reload (File ▸
        Open… / Load full recording), so capturing the widget at shortcut-creation time would leave
        the shortcuts driving a disposed player.

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
        """Run `fn` against the CURRENT VideoView, resolved at ACTIVATION time (not capture time)
        THROUGH the live central view: _build_ui swaps in a fresh CentralView (and thus a fresh
        self.view.video) on reload. This is the generalised dynamic-resolution idiom — it now
        resolves `self.view.video` (the single swap point) instead of a window-held `self.video`.
        No-op before the first successful load (a failed first load leaves no `view` — the shortcuts
        must not crash)."""
        view = getattr(self, "view", None)
        if view is not None:
            fn(view.video)

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
        lap or the corner/entry can't be resolved (a degenerate session). The session-scoped panels
        (table / corners button / map / video / the shared cursor) are reached through self.view —
        the single swap point."""
        best = self.session.best_lap_id()
        if best is None:
            return
        view = self.view
        # Select the best lap (programmatic select, NOT a user-select, so it doesn't re-enter the
        # seek-on-select path — we own the seek below, to the corner entry rather than the lap
        # start). This repoints the Corners view + charts onto the best lap.
        view.table.select([best])
        view._on_laps_selected([best])
        # Show the Corners view so the selected corner's per-corner row is visible (the toggle
        # also updates the table header). Idempotent if already in Corners mode.
        if not view.corners_btn.isChecked():
            view.corners_btn.setChecked(True)
        # Ring the corner's apex on the map (display-only cue, same as the consistency panel).
        view.map.highlight_corner(cid)
        # Seek the video to the best lap's entry to this corner.
        target = self.session.corner_entry_media_time(best, cid)
        if target is not None:
            view.video.seek(target)
            # Seed auto-follow to the lap the seek lands in, so the immediate post-seek tick
            # isn't treated as a lap-change edge (mirrors the lap-select seek's handling).
            view._playback.followed_lap = self.session.lap_at_time(target)

    def _on_consistency_toggled(self, on: bool):
        """View ▸ "Show consistency panel": show/hide the consistency strip under the lap table.
        The choice is remembered on the WINDOW (self._consistency_visible) so it survives a reload —
        a fresh CentralView is seeded with it at construction. The actual show/hide + stats refresh
        is the view's job (set_consistency_visible). No-op before the first successful load (no view
        yet)."""
        self._consistency_visible = bool(on)
        view = getattr(self, "view", None)
        if view is not None:
            view.set_consistency_visible(self._consistency_visible)

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
        has no usable lap at all. The primary lap lives on the central view (self.view._corner_lap);
        resolved through it, with a defensive getattr for the no-view (failed-first-load) case."""
        view = getattr(self, "view", None)
        lap = getattr(view, "_corner_lap", None) if view is not None else None
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
        # report writer itself stays Qt-free and just embeds the bytes. The panels are reached
        # through the live central view.
        images = [("Track map", self._grab_png(self.view.map)),
                  ("Speed · Δ to best", self._grab_png(self.view.plots))]
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
        # The compare controller lives on the live central view.
        view = getattr(self, "view", None)
        if view is not None:
            view.compare.clear_prefer_cross()
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
        # The compare controller lives on the live central view.
        if not self.view.compare.enter_cross():
            QMessageBox.information(
                self, "pacer studio — cross-recording compare unavailable",
                "The reference recording's lap could not be set up for compare.")

    def _apply_reference_change(self):
        """Refresh every "vs best" surface after the reference was loaded OR cleared, and update
        the menu + status chip. The reference replaces the local best lap as the Δ / map-overlay /
        sector-guide / per-corner-Δ baseline, so the same panels a re-segment refreshes are
        rebuilt here (minus the actual re-segmentation — the PRIMARY laps are unchanged).

        Stays on the WINDOW: it is triggered by the persistent Analyse-menu actions (load / clear
        reference) and ends in _update_reference_status, which drives the menu enablement + the
        persistent status-bar chip. The session-derived refresh itself is the view's shared seam
        (self.view.rebuild_derived_views), gated on the view's compare state."""
        # Routed through the shared rebuild seam so a reference change refreshes the IDENTICAL union
        # a re-segmentation does. This FIXES a real latent drift: the old hand-maintained sequence
        # here omitted map.set_corners() and _refresh_driving_channels(), so loading/clearing a
        # reference (which DOES change the per-corner Δ baseline) left the corner-map markers and
        # the brake/coast glyphs stale — they now refresh too. reselect mirrors the old branch:
        # _select_default() in single mode, plots.refresh() (keep the pinned pair) while comparing.
        self.view.rebuild_derived_views(reselect=not self.view._comparing())
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
