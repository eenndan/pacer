"""StudioWindow: the persistent chrome that loads sessions and swaps in a fresh CentralView per
load; the panel layout lives in CentralView."""

from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QBuffer, QIODevice, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QActionGroup, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import (
    chapters,
    demo,
    export_data,
    export_video,
    library,
    prefs,
    sidecar,
    theme,
    track_db,
    units,
)
from .central_view import CentralView
from .coaching_panel import OpportunitiesDialog
from .help_dialog import AboutDialog, PrivacyDialog, ShortcutsDialog
from .library_dialog import LibraryDialog
from .session import DEFAULT_SAMPLE, Session, fmt_time


class _VideoExportWorker(QThread):
    """QThread wrapper running export_video.Renderer off the UI thread, forwarding frame progress
    and a final ok/message via queued signals. cancel() cooperatively stops the render; a
    failed/cancelled run drops the partial output."""

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
        """Drop a partially-written output so cancel/error leaves no broken MP4."""
        try:
            if os.path.exists(self._spec.out_path):
                os.remove(self._spec.out_path)
        except OSError:
            pass


class _SessionLoadWorker(QThread):
    """QThread wrapper running the ~1.4–4 s synchronous Session.load(paths) off the UI thread, so the
    window stays responsive (the "Loading telemetry…" placeholder shows) instead of freezing on every
    open/reload. Session.load is pure compute (numpy + pacer C++; creates no Qt objects) so it is safe
    off-thread; the resulting Session is a plain object handed back via a queued signal.

    Each worker carries the `token` of the _load that started it; the window's completion slots ignore
    any result whose token is stale (a newer _load superseded it), so a second drag-drop can't apply an
    older load destructively. Per-sample ingest is Python/GIL-held; the numpy/g-meter portions release
    the GIL — the win is the non-blocking, cancellable, supersede-safe load, not full parallelism."""

    loaded = Signal(int, list, object)   # (token, paths, session)
    failed = Signal(int, list, object)   # (token, paths, exception)

    def __init__(self, token: int, paths: list[str]):
        super().__init__()
        self._token = token
        self._paths = list(paths)

    def run(self):
        try:
            session = Session.load(self._paths)
        except Exception as exc:  # noqa: BLE001 - surface ANY load failure to the GUI thread
            self.failed.emit(self._token, self._paths, exc)
            return
        self.loaded.emit(self._token, self._paths, session)


class _WelcomeView(QWidget):
    """First-run / no-recording empty state — the product's tagline made literal: drop a GoPro
    recording onto the window, or open one. `on_open` runs the file picker, `on_demo` resolves and
    loads a real demo lapping recording (and re-shows this state with an honest message if the demo
    can't be fetched). An optional `error` line is shown when this stands in for a failed first
    load. The buttons are exposed (`open_btn`/`demo_btn`) for tests."""

    def __init__(self, on_open, on_demo, error: str | None = None, parent=None):
        super().__init__(parent)
        root = QVBoxLayout(self)
        root.setAlignment(Qt.AlignCenter)
        root.setSpacing(14)

        title = QLabel("Pacer")
        title.setProperty("role", "WelcomeTitle")
        title.setAlignment(Qt.AlignCenter)
        subtitle = QLabel("Drop a GoPro recording here — or open one — to get your laps.")
        subtitle.setProperty("role", "WelcomeSubtitle")
        subtitle.setAlignment(Qt.AlignCenter)
        subtitle.setWordWrap(True)
        root.addWidget(title)
        root.addWidget(subtitle)

        buttons = QHBoxLayout()
        buttons.setAlignment(Qt.AlignCenter)
        self.open_btn = QPushButton("Open recording…")
        self.open_btn.setProperty("variant", "primary")
        self.open_btn.setDefault(True)
        self.open_btn.clicked.connect(on_open)
        self.demo_btn = QPushButton("Open demo")
        self.demo_btn.clicked.connect(on_demo)
        buttons.addWidget(self.open_btn)
        buttons.addWidget(self.demo_btn)
        root.addLayout(buttons)

        if error:
            err = QLabel(error)
            err.setProperty("role", "WelcomeError")
            err.setAlignment(Qt.AlignCenter)
            err.setWordWrap(True)
            root.addWidget(err)


class StudioWindow(QMainWindow):
    # Emitted after every load settles (on the UI thread, after _on_session_loaded /
    # _on_load_failed have run) — a clean way for tests/smoke to wait for the now-async load.
    loadFinished = Signal()

    def __init__(self, paths: list[str], full: bool = False, demo_unavailable: bool = False):
        super().__init__()
        self.resize(1440, 900)
        # "Drop a GoPro, get your laps": files dropped on the window load through the guarded path.
        self.setAcceptDrops(True)
        # The one session-scoped central view, swapped in fresh per load; None until first load.
        self.view = None
        # Async-load bookkeeping: a monotonically increasing token stamps each _load; the completion
        # slots ignore any worker result whose token is stale (a newer _load superseded it). All
        # in-flight workers are held in a set so no QThread is GC'd mid-run (a superseded worker keeps
        # running to completion, then drops itself out); _load_worker is the current one.
        self._load_token = 0
        self._load_worker = None
        self._load_workers = set()
        self._pending_load = None  # single-flight: the latest queued (token, paths) while a load runs
        self._tick_timer = None  # created on the first _build_ui; reused across reloads (window-owned)
        # Persisted on the window so the View-menu choice survives a reload (passed into each view).
        self._consistency_visible = False
        # Speed display unit (km/h default), loaded from the persisted prefs so the choice survives
        # a relaunch; passed into each fresh CentralView + the video/coaching exports.
        self._speed_unit = prefs.speed_unit()
        self._build_menu()
        self._build_shortcuts()
        # --full on the CLI auto-discovers the first file's sibling chapters; explicit multiple
        # paths are used as-is.
        if full and len(paths) == 1:
            paths = chapters.discover_siblings(paths[0])
        # Launched with no recording -> the welcome empty state rather than a blank/auto-demo window.
        if paths:
            self._load(paths)
        elif demo_unavailable:
            # `--demo` was requested but the demo couldn't be resolved (offline / download failed):
            # show the welcome state with an honest message rather than silently launching the
            # lapless bundled sample (which reads as a broken app).
            self._show_welcome(error="Demo clip unavailable — check your connection and retry, "
                                     "or drop your own GoPro .mp4 to get your laps.")
        else:
            self._show_welcome()

    # ----------------------------------------------------------- drag-and-drop / welcome
    @staticmethod
    def _dropped_mp4s(mime) -> list[str]:
        """The local .mp4 paths in a drag's mime data (sorted so chapter siblings load in order);
        [] if the drag carries no MP4 file URLs."""
        if not mime.hasUrls():
            return []
        out = [u.toLocalFile() for u in mime.urls()]
        return sorted(p for p in out if p and p.lower().endswith(".mp4"))

    def dragEnterEvent(self, event):
        """Accept a drag only if it carries at least one .mp4 (so the cursor shows it's droppable)."""
        if self._dropped_mp4s(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Load the dropped GoPro file(s) through the guarded _load path. Multiple files are loaded
        as one recording (chapter siblings); unrelated drops are user error."""
        paths = self._dropped_mp4s(event.mimeData())
        if paths:
            event.acceptProposedAction()
            self._load(paths)

    def _show_welcome(self, error: str | None = None):
        """Install the no-recording welcome empty state (also the first-load-failure fallback)."""
        self._paths = getattr(self, "_paths", [])
        self.setWindowTitle("pacer studio")
        self.setCentralWidget(_WelcomeView(self._open_file, self._open_demo, error, parent=self))
        if getattr(self, "_full_action", None) is not None:
            self._full_action.setEnabled(False)

    def _open_demo(self):
        """Welcome-screen "Open demo": load a real demo lapping recording if one is resolvable
        (env / cache / a one-time release download — see studio.demo). If it can't be resolved
        (offline / download failed), DON'T silently load the bundled sample clip — it has zero real
        laps, so the user would land in a blank-looking studio that reads as broken. Instead keep the
        welcome screen and say so honestly, so they can retry or open their own footage."""
        path = demo.resolve_demo_recording()
        if path is None:
            self._show_welcome(error="Demo clip unavailable — check your connection and retry, "
                                     "or drop your own GoPro .mp4 to get your laps.")
            return
        self._load([path])

    # ------------------------------------------------------------------ loading
    def _load(self, paths: list[str]):
        """Load (or reload) the session for `paths` OFF the UI thread, then (in _on_session_loaded)
        build a fresh CentralView and swap it in. The window keeps the load orchestration +
        `session`/`_paths`; each panel captures `session` at construction.

        Session.load is a ~1.4–4 s synchronous call, so it runs on a worker QThread: the placeholder
        shows immediately and the window stays responsive. SINGLE-FLIGHT: only ONE load runs at a
        time — a superseding _load (e.g. a second drag-drop) is QUEUED rather than run concurrently
        (no point loading two recordings at once, and serializing keeps the supersede ordering
        clean). It shows the placeholder, bumps the token, and starts when the current worker
        finishes; the older in-flight result is ignored by token (see the completion slots)."""
        print("studio: loading telemetry…", flush=True)
        # Show the placeholder so the window isn't a black void during the load (the load no longer
        # blocks the event loop, so the placeholder also stays live/paintable throughout).
        self._show_loading_placeholder(paths)
        # Bump the token: any in-flight worker started by a previous _load is now stale and its
        # result will be ignored when it finishes.
        self._load_token += 1
        token = self._load_token
        # Single-flight: if a worker is already running, remember only the LATEST request and start
        # it when the current one finishes — no point running two loads at once, and queuing keeps
        # the supersede ordering clean.
        if self._load_worker is not None and self._load_worker.isRunning():
            self._pending_load = (token, list(paths))
            return
        self._start_load_worker(token, paths)

    def _start_load_worker(self, token: int, paths: list[str]):
        """Spawn the single in-flight load worker for `token` (see _load's single-flight rule)."""
        worker = _SessionLoadWorker(token, paths)
        self._load_worker = worker  # the current worker
        self._load_workers.add(worker)  # hold the in-flight worker so it isn't GC'd mid-load
        worker.loaded.connect(self._on_session_loaded)
        worker.failed.connect(self._on_load_failed_async)
        # On finish, drop the worker + launch any queued pending load (single-flight serialization).
        worker.finished.connect(lambda w=worker: self._on_worker_finished(w))
        worker.start()

    def _on_worker_finished(self, worker):
        """A load worker's QThread finished: drop it from the in-flight set, then (single-flight)
        start the most recent QUEUED load if one is pending and still current."""
        self._load_workers.discard(worker)
        if self._load_worker is worker:
            self._load_worker = None
        pending = self._pending_load
        if pending is not None:
            self._pending_load = None
            token, paths = pending
            if token == self._load_token:  # still the latest request — run it now
                self._start_load_worker(token, paths)

    def _drain_load_workers(self, deadline_s: float = 60.0):
        """Let any in-flight load worker finish before teardown, bounded so this can never hang on a
        stuck worker. Pump the event loop in short slices (so the worker's queued completion signals
        — incl. _on_worker_finished launching a still-pending load — can drain) and wait briefly per
        worker, giving up after `deadline_s`. The token is bumped past every in-flight worker, so
        whatever they emit is ignored regardless."""
        app = QApplication.instance()
        start = time.monotonic()
        while any(w.isRunning() for w in list(self._load_workers)):
            if app is not None:
                app.processEvents()
            for w in list(self._load_workers):
                if w.isRunning():
                    w.wait(20)  # short slices, bounded by the deadline below (never an unbounded wait)
            if time.monotonic() - start > deadline_s:
                break

    def closeEvent(self, event):
        """Drain any in-flight load worker so a QThread isn't destroyed mid-run on window close (Qt
        would warn/crash). Uses the bounded drain so close can never hang on a stuck worker. The
        token is already bumped past any in-flight worker, so its result is ignored regardless."""
        self._pending_load = None  # don't start a queued load during teardown
        self._drain_load_workers()
        super().closeEvent(event)

    def _on_session_loaded(self, token: int, paths: list[str], session):
        """Successful load completion (on the UI thread, via a queued signal): commit the session and
        build the UI. Ignores a STALE result — a newer _load superseded this one, so applying it
        would clobber the current (good) session. This is the EXACT former post-load body of _load."""
        if token != self._load_token:
            return  # superseded by a newer load; drop this result
        self.session = session
        # Commit _paths only after a successful load, so a failed reload leaves both self.session
        # and _paths pointing at the still-good recording (every _paths consumer stays in sync).
        self._paths = list(paths)
        n_ch = len(self.session.chapters) if self.session.chapters else 1
        print(f"studio: {self.session.point_count()} points, "
              f"{self.session.lap_count()} laps, {n_ch} chapter(s).", flush=True)

        # Restore the user's saved start/sector lines (written only on a user edit) before the UI
        # is built, so every panel is constructed against the restored segmentation. Applied first
        # so the segmentation is final before any notice below is decided.
        self._sidecar_path = sidecar.sidecar_path(paths[0]) if paths else None
        notice = None
        data = sidecar.load(self._sidecar_path) if self._sidecar_path else None
        if data is not None:
            if session.apply_timing_lines_latlon(data["start"], data["sectors"],
                                                 confirmed=data["confirmed"]):
                print(f"studio: restored saved timing lines from "
                      f"{os.path.basename(self._sidecar_path)}", flush=True)
            else:
                notice = ("saved timing lines don't match this recording — "
                          "reverted to the auto-fitted start line")
        elif session.track_name is None and session.lap_count() > 0:
            # Unknown track: the start line was auto-fitted, so lap times are arbitrary until the
            # user drags it into place. To register the track: studio/dev/print_track_entry.py.
            notice = ("unknown track — start/finish line was auto-fitted; "
                      "drag it into place to fix lap timing")

        # A zero-valid-lap load renders every panel blank; surface a notice. Highest priority —
        # supersedes the notices above (a 0-lap recording has no lap timing to fix either way).
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

        # Record this recording in the local session library (see _update_library).
        self._update_library(paths)
        self.loadFinished.emit()

    def _on_load_failed_async(self, token: int, paths: list[str], exc: Exception):
        """Failed load completion (on the UI thread, via a queued signal): drop a STALE result, else
        surface the error via the existing _on_load_failed (welcome-state fallback on first load,
        good session kept on a reload failure)."""
        if token != self._load_token:
            return  # superseded by a newer load; drop this result
        self._on_load_failed(paths, exc)
        self.loadFinished.emit()

    def _show_loading_placeholder(self, paths: list[str]):
        """Immediate visual feedback while Session.load runs on a worker thread: install a centered
        "Loading telemetry…" card, show the window, and force one synchronous paint so it appears
        right away. Replaced by the real UI in _build_ui."""
        label = chapters.recording_label(paths)
        placeholder = QLabel(f"Loading telemetry…\n\n{label}" if label else "Loading telemetry…")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setWordWrap(True)
        self.setCentralWidget(placeholder)
        if not self.isVisible():
            self.show()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _on_load_failed(self, paths: list[str], exc: Exception):
        """A session load failed (missing / not-a-GoPro / no-GPS file). Show a clear, non-fatal error
        in PLAIN LANGUAGE (never the raw Python class name as the headline — that reads as amateur)
        and keep the app open. If a session was already loaded (this was a reload, e.g. "Load full
        recording"), the working UI is LEFT INTACT — only the dialog shows. On the very first load
        there is no UI yet, so install the welcome empty state so the window still opens.

        The raw `type(exc).__name__: exc` is logged to the console and tucked behind the dialog's
        "Show details" — diagnostics for a bug report, not the user-facing message."""
        offending = paths[0] if paths else "(no file)"
        detail = f"{type(exc).__name__}: {exc}"
        message = self._load_failure_message(paths, exc)
        print(f"studio: failed to load {offending}: {detail}", flush=True)
        box = QMessageBox(QMessageBox.Critical, "pacer studio — could not load recording",
                          f"{message}\n\n{offending}\n\n"
                          "The previously loaded session (if any) is unchanged.", parent=self)
        # Raw exception text lives in the collapsible details, not the headline.
        box.setDetailedText(detail)
        box.exec()
        # First-load failure: no central widget yet — show the welcome empty state (with the plain
        # message) so the window stays open and the user can drop/open another recording.
        if not hasattr(self, "session"):
            # Seed _paths for the failed-first-load case (nothing else has set it, yet readers like
            # "Load full recording" stay reachable). A failed reload keeps the good _paths instead.
            self._paths = list(paths)
            self._show_welcome(error=f"{message}\n\n{offending}")

    @staticmethod
    def _load_failure_message(paths: list[str], exc: Exception) -> str:
        """Map a load failure to a plain-language sentence (no raw Python class name). The load path
        raises in a few distinguishable ways:

          * a non-GoPro / no-GPMF file — GPMFSource's ctor throws RuntimeError("Failed to open
            file: …") when OpenMP4Source can't find a GPMF track (also covers a file that isn't a
            valid MP4 at all). We split this from a genuinely-missing file by checking the path.
          * a missing / unreadable path — the file isn't on disk (or an OSError reading it).
          * anything else — a generic, honest fallback (still no class name up front).

        A recording that OPENS but has zero GPS fixes does NOT raise — it loads as a 0-valid-lap
        session (see _on_session_loaded's in-panel empty state), so it never reaches here."""
        offending = paths[0] if paths else None
        if offending is not None and not os.path.isfile(offending):
            return "Couldn't open the file — it may have been moved or deleted."
        text = str(exc).lower()
        if isinstance(exc, OSError):
            return "Couldn't open the file."
        if isinstance(exc, RuntimeError) and "open file" in text:
            # GPMFSource couldn't find a GPMF/GPS track in this MP4.
            return "This doesn't look like a GoPro recording with GPS metadata."
        # Unknown cause — honest generic message; the raw class name stays in the details/log only.
        return "Couldn't read telemetry from this recording — it may be corrupt or unsupported."

    def _build_ui(self):
        """Atomic swap: dispose the outgoing view, build a fresh CentralView for the just-loaded
        session (all session-scoped construction lives in its __init__), and setCentralWidget it.
        The window keeps only the persistent chrome below (tick timer, ref-chip, the "Load full
        recording" enablement), which survives the swap.

        Disposing the outgoing view first stops its decoder + closes the g-meter overlay before the
        central widget is replaced."""
        old_view = getattr(self, "view", None)
        if old_view is not None:
            old_view.dispose()  # stop the old decoder + close its g-meter overlay before the swap
        # The view holds a read alias of session + the paths (banner) + the sidecar path.
        self.view = CentralView(self.session, self._paths, self._sidecar_path,
                                self._consistency_visible, parent=self,
                                speed_unit=getattr(self, "_speed_unit", units.DEFAULT_UNIT))
        # Keep Edit ▸ Undo's enabled state in sync with the session's undo stack as lines are dragged.
        self.view.timingEdited.connect(self._sync_edit_menu)
        self._sync_edit_menu()  # a fresh load has no prior edit -> Undo disabled
        self.setCentralWidget(self.view)
        # One ~30 Hz tick timer for the window's lifetime, created once and reused across reloads (a
        # second would double the tick rate); the swap just re-points which view tick() drives.
        if self._tick_timer is None:
            self._tick_timer = QTimer(self)
            self._tick_timer.setInterval(33)  # ~30 Hz
            self._tick_timer.timeout.connect(self._tick)
            self._tick_timer.start()

        self._sync_full_recording_action()
        # The permanent status-bar chip naming the active cross-recording reference, created once
        # and hidden until a reference is loaded.
        if getattr(self, "_ref_chip", None) is None:
            self._ref_chip = QLabel("")
            self._ref_chip.setProperty("role", "BarLabel")
            self.statusBar().addPermanentWidget(self._ref_chip)
        self._update_reference_status()

    def _tick(self):
        """The ~30 Hz timer slot, delegating to the current view's tick(); no-op before first load."""
        view = getattr(self, "view", None)
        if view is not None:
            view.tick()

    # ----------------------------------------------------- menu bar / information architecture
    def _build_menu(self):
        """Build the File / Coaching / View / Help menus on the persistent menu bar (survives the
        central-widget swap)."""
        menu = self.menuBar().addMenu("&File")
        self._open_action = menu.addAction("Open…")
        self._open_action.setShortcut(QKeySequence.Open)
        self._open_action.triggered.connect(self._open_file)
        # Re-open recent recordings (see _sync_recent_menu).
        self._recent_menu = menu.addMenu("Open Recent")
        self._recent_menu.aboutToShow.connect(self._sync_recent_menu)
        self._sync_recent_menu()  # seed it once so it's populated before its first open
        self._full_action = menu.addAction("Load full recording")
        self._full_action.setToolTip(
            "Discover this recording's sibling chapters and load them as one continuous session")
        self._full_action.triggered.connect(self._load_full_recording)
        # File ▸ Export: the data-export actions (writers in export_data.py); greyed until a
        # session is loaded (synced on aboutToShow).
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
        # F9 video export: burns the overlays onto the footage (renderer in export_video.py).
        self._export_video_action = menu.addAction("Export overlay video…")
        self._export_video_action.setToolTip(
            "Render the selected lap with the on-screen overlays burned in (g-meter, Δ/speed, "
            "map inset, lap strip) to a shareable MP4")
        self._export_video_action.triggered.connect(self._export_overlay_video)
        self._export_video_action.setEnabled(False)
        # File ▸ Library: the full browse + per-track PB chart over the session-library index.
        menu.addSeparator()
        self._library_action = menu.addAction("Library…")
        self._library_action.setToolTip(
            "Browse your analyzed recordings (date / track / best lap / theoretical best), "
            "re-open any of them, and see per-track PB progression")
        self._library_action.triggered.connect(self._open_library)
        # Save the current placed start/sector lines as a named, reusable track in the database,
        # so a future recording at this location auto-detects with these timing lines in place.
        # Enabled (in _sync_export_menu) only when the session has usable timing lines.
        self._save_track_action = menu.addAction("Save as track…")
        self._save_track_action.setToolTip(
            "Promote this recording's start/finish (and sector) lines into a named track in your "
            "database, so the next recording at this circuit auto-detects them")
        self._save_track_action.triggered.connect(self._save_as_track)
        self._save_track_action.setEnabled(False)  # no session yet at construction time

        # Edit menu: Undo the last timing-line edit (Cmd+Z). Dragging the start/finish (or a sector)
        # line immediately re-segments AND overwrites the sidecar — a slightly-wrong nudge would
        # otherwise silently destroy the good timing lines + the PB / session-best baseline with no
        # way back. Undo restores the previous lines through the same re-segment/apply path (see
        # CentralView.undo_timing_lines). Disabled until there's a prior edit in this session.
        edit_menu = self.menuBar().addMenu("&Edit")
        self._undo_action = edit_menu.addAction("Undo timing-line edit")
        self._undo_action.setShortcut(QKeySequence.Undo)  # Cmd+Z on macOS
        self._undo_action.setToolTip(
            "Revert the last start/finish or sector-line drag (re-segments and restores the "
            "previous lap timing + session-best baseline)")
        self._undo_action.triggered.connect(self._undo_timing)
        self._undo_action.setEnabled(False)  # no session / no edit yet
        edit_menu.aboutToShow.connect(self._sync_edit_menu)

        # Coaching menu: the comparison / coaching surface (reference load/clear/compare +
        # Opportunities). Named "Coaching" to match the product positioning and the docs/docstrings
        # (studio/README.md, coaching_panel.py) — its items are all coaching/analysis surfaces.
        coaching_menu = self.menuBar().addMenu("&Coaching")
        self._ref_action = coaching_menu.addAction("Load reference recording…")
        self._ref_action.setToolTip(
            "Pick another recording of the SAME track; its best lap becomes the Δ / map / table "
            "reference (instead of this session's own best lap)")
        self._ref_action.triggered.connect(self._load_reference_file)
        self._clear_ref_action = coaching_menu.addAction("Clear reference")
        self._clear_ref_action.setToolTip("Revert the Δ / map / table reference to this "
                                          "session's own best lap")
        self._clear_ref_action.triggered.connect(self._clear_reference)
        self._clear_ref_action.setEnabled(False)
        # Cross-recording video compare (pane A = this lap, pane B = the reference's lap); distinct
        # from the same-recording "Compare videos" toggle. Enabled only when a reference is loaded.
        self._cross_compare_action = coaching_menu.addAction("Compare vs reference recording")
        self._cross_compare_action.setToolTip(
            "Side-by-side: this recording's lap (left) vs the loaded reference recording's lap "
            "(right), each playing its own footage. Load a reference recording first.")
        self._cross_compare_action.triggered.connect(self._enter_cross_compare)
        self._cross_compare_action.setEnabled(False)
        # F10 Opportunities: top-3 corners by time lost vs your own best lap (recomputed per open).
        coaching_menu.addSeparator()
        self._opportunities_action = coaching_menu.addAction("Opportunities…")
        self._opportunities_action.setToolTip(
            "Where to find time vs your own best lap: the top-3 corners by realistic time lost "
            "(median of your clean laps), each with the measured reason and a jump-to.")
        self._opportunities_action.triggered.connect(self._open_opportunities)

        # F6 View ▸ Show consistency panel (unchecked by default; choice persists across reloads).
        view_menu = self.menuBar().addMenu("&View")
        self._consistency_action = view_menu.addAction("Show consistency panel")
        self._consistency_action.setCheckable(True)
        self._consistency_action.setChecked(self._consistency_visible)
        self._consistency_action.setToolTip(
            "Show the consistency strip under the lap table: the lap-time trend sparkline and the "
            "top-5 most inconsistent corners.")
        self._consistency_action.toggled.connect(self._on_consistency_toggled)

        # View ▸ Units: the speed display unit (km/h default). Two mutually-exclusive checkable
        # items in a QActionGroup; flipping one persists the choice + refreshes the open views live.
        units_menu = view_menu.addMenu("Units")
        units_menu.setToolTip("Speed display unit (km/h ↔ mph). Distances stay in metres.")
        self._unit_group = QActionGroup(self)
        self._unit_group.setExclusive(True)
        self._unit_actions: dict[str, object] = {}
        for unit, label in ((units.KMH, "km/h"), (units.MPH, "mph")):
            act = units_menu.addAction(label)
            act.setCheckable(True)
            act.setChecked(self._speed_unit == unit)
            act.setData(unit)
            self._unit_group.addAction(act)
            self._unit_actions[unit] = act
            act.triggered.connect(lambda checked, u=unit: checked and self._on_unit_selected(u))

        # Help menu: the shortcut reference (also F1 / ?) and an About card (help_dialog.py).
        help_menu = self.menuBar().addMenu("&Help")
        self._shortcuts_action = help_menu.addAction("Keyboard shortcuts")
        self._shortcuts_action.setShortcut(QKeySequence(Qt.Key_F1))
        self._shortcuts_action.setToolTip(
            "List the keyboard shortcuts and the key drag interactions (chart scrub, start/finish "
            "line)")
        self._shortcuts_action.triggered.connect(self._show_shortcuts)
        self._privacy_action = help_menu.addAction("Your data && privacy")
        self._privacy_action.setToolTip(
            "What pacer stores on this Mac (all local/offline) and how to remove it")
        self._privacy_action.triggered.connect(self._show_privacy)
        self._about_action = help_menu.addAction("About pacer studio")
        self._about_action.setToolTip("What pacer studio is and what it does")
        self._about_action.triggered.connect(self._show_about)

    def _show_shortcuts(self):
        """Help ▸ Keyboard shortcuts (also F1 / ?): the read-only shortcut reference."""
        ShortcutsDialog(self).exec()

    def _show_about(self):
        """Help ▸ About pacer studio: the small themed About card (name / tagline / blurb)."""
        AboutDialog(self).exec()

    def _show_privacy(self):
        """Help ▸ Your data & privacy: the local-data disclosure card (what pacer stores + how to
        remove it). All local/offline; the copy lives in help_dialog.PRIVACY_PARAGRAPHS."""
        PrivacyDialog(self).exec()

    # ----------------------------------------------------- timing-line undo (Edit ▸ Undo)
    def _sync_edit_menu(self):
        """Enable Edit ▸ Undo only when the current session has a prior timing-line edit to revert.
        Connected to the Edit menu's aboutToShow AND refreshed live via the view's timingEdited
        signal (so the shortcut's enabled state tracks each drag), so neither _load nor _on_lines
        needs to reach into the menu. getattr-guarded — _build_ui can run before _build_menu in a
        partial test harness (test_central_view_realqt builds the UI without the menu bar)."""
        action = getattr(self, "_undo_action", None)
        if action is None:
            return
        session = getattr(self, "session", None)
        action.setEnabled(bool(session is not None and session.can_undo_timing()))

    def _undo_timing(self):
        """Edit ▸ Undo (Cmd+Z): revert the last timing-line edit via the current view. No-op when
        nothing is loaded or there's no prior edit (the action is disabled there too)."""
        view = getattr(self, "view", None)
        if view is None:
            return
        if view.undo_timing_lines():
            self.statusBar().showMessage("reverted the last start/finish-line edit")

    # ----------------------------------------------------- keyboard shortcuts
    def _build_shortcuts(self):
        """Window-level playback shortcuts: Space (play/pause), M (mute), G (g-meter overlay),
        C (compare mode). Parented to the window so they survive every view swap; handlers resolve
        the current video dynamically (via _video_do). G / C go through the button's click() so a
        disabled button makes its shortcut a no-op. ←/→ stepping is handled in keyPressEvent, not
        here, so the lap table keeps its arrow navigation."""
        def shortcut(key, handler):
            sc = QShortcut(QKeySequence(key), self)
            sc.setContext(Qt.WindowShortcut)
            sc.activated.connect(handler)

        shortcut(Qt.Key_Space, lambda: self._video_do(lambda v: v.toggle()))
        shortcut(Qt.Key_M, lambda: self._video_do(lambda v: v.toggle_mute()))
        shortcut(Qt.Key_G, lambda: self._video_do(lambda v: v.gmeter_btn.click()))
        shortcut(Qt.Key_C, lambda: self._video_do(lambda v: v.compare_btn.click()))
        # ? → shortcut reference (keep in sync with help_dialog.SHORTCUT_GROUPS).
        shortcut(Qt.Key_Question, self._show_shortcuts)

    def _video_do(self, fn):
        """Run `fn` against the current VideoView, resolved at call time (since _build_ui swaps it);
        no-op before the first load."""
        view = getattr(self, "view", None)
        if view is not None:
            fn(view.video)

    def keyPressEvent(self, event):
        """←/→ step the video ±1 s (Shift ±5 s). Handled here, not as a QShortcut, so the lap table
        keeps arrow nav; keyPressEvent only fires when the focus widget didn't use the key."""
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
        """Upsert the just-loaded recording into the local session-library index. Fully guarded: a
        library write must never disrupt a load. Skips the bundled DEFAULT_SAMPLE and any recording
        with no valid laps (a junk row the library would surface forever)."""
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
        guarded `_load` path; the dialog reads the index defensively (empty when missing). The
        privacy controls (forget one recording / clear the library) are injected here — the dialog
        stays pacer-free + file-op-free, the app owns the index write + sidecar delete."""
        dlg = LibraryDialog(library.load(), open_recording=self._load, parent=self,
                            forget_recording=self._forget_recording,
                            clear_library=self._clear_library)
        dlg.exec()

    def _forget_recording(self, entry: dict) -> dict:
        """Privacy "forget this recording": drop `entry` from the library index AND delete its
        per-video `.pacer.json` timing-line sidecar, then return the fresh index (for the dialog to
        re-render). The media file is NEVER touched. Fully guarded — a failed index write or a
        missing/locked sidecar just logs; the deletion uses os.remove behind an existence check +
        try/except (never a shell rm)."""
        index = library.load()
        library.remove(index, entry.get("fingerprint"))
        try:
            library.save(index)
        except OSError as exc:
            print(f"studio: could not update the library index ({exc!r}).", flush=True)
        # Delete the recording's sidecar (resolved from the FIRST recorded chapter path — the same
        # stem the sidecar was written under). Guarded end-to-end.
        paths = entry.get("paths") or []
        if paths:
            try:
                side = sidecar.sidecar_path(paths[0])
                if os.path.exists(side):
                    os.remove(side)
                    print(f"studio: deleted timing-line sidecar {os.path.basename(side)}",
                          flush=True)
            except OSError as exc:
                print(f"studio: could not delete the sidecar ({exc!r}).", flush=True)
        return library.load()

    def _clear_library(self) -> dict:
        """Privacy "clear library": wipe the whole app-support index (only the library history of
        what/where you recorded). The media files + their `.pacer.json` sidecars are left untouched.
        Returns the fresh (empty) index for the dialog to re-render. Guarded — a failed write logs
        and returns the current index unchanged."""
        try:
            library.clear()
        except OSError as exc:
            print(f"studio: could not clear the library index ({exc!r}).", flush=True)
        return library.load()

    # Open Recent: recently analyzed recordings (most-recent-first), each re-opened via the guarded
    # `_load`. Sourced from the session-library index rather than a separate MRU list.
    _RECENT_LIMIT = 8

    def _recent_entries(self) -> list[dict]:
        """Open Recent candidates: openable library entries (real track + laps, file present),
        most-recent-first by date, capped at _RECENT_LIMIT. Guarded: any failure yields []."""
        try:
            entries = library.load().get("entries", [])
        except Exception as exc:  # noqa: BLE001 — the recents list is additive; never break the menu
            print(f"studio: Open Recent unavailable ({exc!r}).", flush=True)
            return []
        usable = [
            e for e in entries
            if e.get("track") and e.get("lap_count")
            and any(os.path.exists(p) for p in (e.get("paths") or []))
        ]
        # Newest first; missing date sorts last.
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
        """Coaching ▸ Opportunities…: open the read-only opportunities dialog, built from a
        FRESH session.coaching_opportunities() (recomputed each open — zero per-tick cost; the
        per-lap inputs it composes are already cached). The dialog handles its own friendly
        excluded state when there are too few clean laps. Each row's Go button routes to
        `_jump_to_opportunity` (corner select + best-lap entry seek). No-op if the FIRST load
        failed (no session yet) — defensive, like the export actions' enabled-state gate."""
        if getattr(self, "session", None) is None:
            return
        opps = self.session.coaching_opportunities()
        # D4: the best lap's per-corner braking-point comparison, keyed by cid so the dialog can
        # append the ESTIMATED "brake ~N m later" line to a corner's reason. Empty when no g signal.
        # Shared with the persistent panel via session.coaching_brake_points (one source).
        brake_points = self.session.coaching_brake_points()
        dlg = OpportunitiesDialog(opps, jump_to=self._jump_to_opportunity,
                                  brake_points=brake_points, parent=self,
                                  speed_unit=self._speed_unit)
        dlg.exec()

    def _jump_to_opportunity(self, cid: int, _entry_dist: float):
        """Jump-to for an opportunity row: select corner `cid` on the best lap (map apex ring +
        Corners view) and seek the video to the best lap's entry to that corner. No-op if there's
        no best lap or the corner/entry can't be resolved."""
        best = self.session.best_lap_id()
        if best is None:
            return
        view = self.view
        # Programmatic select (not a user-select) so it doesn't re-enter the seek-on-select path —
        # we own the seek below, to the corner entry rather than the lap start.
        view.table.select([best])
        view._on_laps_selected([best])
        if not view.corners_btn.isChecked():
            view.corners_btn.setChecked(True)
        view.map.highlight_corner(cid)
        target = self.session.corners.corner_entry_media_time(best, cid)
        if target is not None:
            view.video.seek(target)
            # Seed auto-follow to the seek's lap so the post-seek tick isn't a lap-change edge.
            view._playback.followed_lap = self.session.lap_at_time(target)

    def _on_consistency_toggled(self, on: bool):
        """View ▸ Show consistency panel: remember the choice on the window (survives a reload) and
        delegate the show/hide to the view. No-op before the first load."""
        self._consistency_visible = bool(on)
        view = getattr(self, "view", None)
        if view is not None:
            view.set_consistency_visible(self._consistency_visible)

    def _on_unit_selected(self, unit: str):
        """View ▸ Units: remember the chosen speed unit on the window (survives a reload), PERSIST
        it (guarded — a write failure must never disrupt the app), and refresh the open views
        live. No behaviour change when re-selecting the current unit."""
        unit = units.normalize_unit(unit)
        if unit == self._speed_unit:
            return
        self._speed_unit = unit
        try:
            prefs.set_speed_unit(unit)
        except OSError as exc:
            print(f"studio: could not persist speed unit ({exc!r}).", flush=True)
        view = getattr(self, "view", None)
        if view is not None:
            view.set_speed_unit(unit)

    # ----------------------------------------------------------- data export (F11)
    # File ▸ Export Qt side (the writers are Qt-free in export_data.py).
    def _sync_export_menu(self):
        """Grey the Export submenu + the video-export action out until a session is loaded.
        Connected to the File menu's aboutToShow (synced as the menu opens), so neither _load nor
        the failed-load path needs to reach into the menu."""
        has = hasattr(self, "session")
        self._export_menu.setEnabled(has)
        self._export_video_action.setEnabled(has)
        # Save-as-track needs USABLE timing lines (≥1 valid lap means the start line actually
        # segments this trace — the lines are worth promoting to a reusable track).
        self._save_track_action.setEnabled(self._can_save_track())

    def _can_save_track(self) -> bool:
        """True iff the current session has usable timing lines to promote into a track: a session
        is loaded, it has valid laps (the start line really segments this trace), and the trace
        carries a location to anchor detection on. Guarded — any failure means 'not saveable'."""
        if not hasattr(self, "session"):
            return False
        try:
            return bool(self.session.valid_lap_ids()) and self.session.point_count() > 0
        except Exception:  # noqa: BLE001 — the guard must never raise out of a menu sync
            return False

    def _save_as_track(self):
        """File ▸ Save as track…: promote the current start/sector lines (lat/lon) into a named
        track in the database, so a future recording at this location auto-detects them. Fully
        guarded — a DB write must never disrupt the session (mirror library.upsert_and_save's
        defensive style)."""
        if not self._can_save_track():  # defensive: action fired with nothing usable loaded
            self.statusBar().showMessage("no usable timing lines to save as a track")
            return
        suggested = self.session.track_name or chapters.recording_label(self._paths) or ""
        name, ok = QInputDialog.getText(
            self, "Save as track", "Track name:", text=suggested)
        name = name.strip()
        if not ok or not name:
            return
        try:
            centroid, bbox = self.session.track_location()
            start, sectors = self.session.timing_lines_latlon()
            entry = track_db.make_entry(name, centroid, start, sectors, bbox=bbox)
            track_db.save_track(entry)
        except (OSError, ValueError) as exc:
            print(f"studio: could not save track {name!r}: {exc}", flush=True)
            self.statusBar().showMessage(f"could not save track: {exc}")
            return
        # The freshly-saved track now wins detection for THIS session's name on the next load —
        # and it makes the timing VERIFIED (a named track is a trusted start line), so refresh the
        # derived views to drop the provisional banner / muting and restore the purple session-bests.
        self.session.track_name = name
        if getattr(self, "view", None) is not None:
            self.view.refresh_timing_trust()
        self.statusBar().showMessage(f"saved track '{name}' — future recordings here auto-detect it")
        print(f"studio: saved track {name!r} to the track database", flush=True)

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
        """Run a writer (`write()`) under an OSError guard; on failure show a warning dialog +
        statusbar note. Returns True on success."""
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
    # File ▸ Export overlay video Qt side (renderer is event-loop-free in export_video.py).

    # Resolution maps to OverlayConfig.out_height (never upscales past source; "Source" is a huge
    # sentinel clamped back to source height); quality maps to OverlayConfig.quality.
    # "1080p" resolution + "High" quality is the default.
    _EXPORT_RES_OPTIONS = [
        ("720p", 720), ("1080p", 1080), ("1440p", 1440), ("Source (no downscale)", 99999),
    ]
    _EXPORT_QUALITY_OPTIONS = [
        ("High — larger file", "high"), ("Standard — smaller file", "standard"),
    ]

    def _ask_export_options(self, lap: int):
        """Modal resolution + quality picker returning an export_video.OverlayConfig, or None on
        cancel. The last choice is remembered on the window."""
        dlg = QDialog(self)
        dlg.setWindowTitle(f"Export overlay video — lap {lap}")
        dlg.setMinimumWidth(400)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QLabel(f"Export overlay video — lap {lap}")
        header.setProperty("role", "PanelHeader")
        root.addWidget(header)

        body = QWidget(dlg)
        col = QVBoxLayout(body)
        col.setContentsMargins(16, 14, 16, 14)
        col.setSpacing(10)
        root.addWidget(body)

        desc = QLabel("Burns the overlays into your footage: g-meter, Δ / speed, map inset and the "
                      "lap strip.")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color: {theme.C.text_dim};")
        col.addWidget(desc)

        # lap_time is a cheap pacer-free accessor (no ffprobe).
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

        # States the target height + never-upscale rule (no ffprobe here); matches output_size().
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
        # Burn the current display unit into the overlay so the export matches the on-screen readout.
        return export_video.OverlayConfig(out_height=out_height, quality=quality,
                                          speed_unit=self._speed_unit)

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
        # Resolve the lap window to its chapter file(s) + local seek; refuses a bad window with a
        # ValueError rather than launching a doomed ffmpeg.
        try:
            spec = export_video.build_lap_spec(self.session, out, lap, config=config)
        except ValueError as exc:
            QMessageBox.warning(self, "Export overlay video",
                                f"This lap can't be exported:\n{exc}")
            return
        self._run_video_export(spec, lap)

    def _run_video_export(self, spec, lap: int):
        """Run the render on a worker QThread behind a cancellable modal dialog. Starts indeterminate
        ("Preparing…"), flips to a determinate bar on the first frame's progress."""
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
        """Coaching ▸ "Load reference recording…": pick another recording (same track) whose best lap
        becomes the Δ / map / table reference. The picked file's chapters are chained, then handed to
        Session.load_reference. On a guard refusal the local best lap is kept and the reason shown."""
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
        """Coaching ▸ "Clear reference": drop the cross-recording reference; everything reverts to the
        session's own best lap."""
        if not hasattr(self, "session") or not self.session.has_reference():
            return
        self.session.clear_reference()
        # Drop the sticky "prefer cross-recording compare" preference so a later compare toggle
        # enters same-recording compare (there's no reference left).
        view = getattr(self, "view", None)
        if view is not None:
            view.compare.clear_prefer_cross()
        self._apply_reference_change()

    def _enter_cross_compare(self):
        """Coaching ▸ "Compare vs reference recording": enter the cross-recording video compare —
        pane A = this recording's current/selected lap, pane B = the reference recording's lap, each
        playing its own footage. No-op (with a notice) if no reference is loaded."""
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
        """Refresh every "vs best" surface after the reference was loaded or cleared, and update the
        menu + status chip. The reference replaces the local best lap as the Δ / map / sector /
        per-corner baseline, so it refreshes the same panels a re-segment does (via the shared seam)."""
        # reselect: default-select in single mode, keep the pinned pair while comparing.
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
    # --full/--chaptered chain a single file's sibling chapters (see StudioWindow).
    full = "--full" in argv or "--chaptered" in argv
    # No path on the CLI -> open to the welcome empty state (the demo is one click from there).
    paths = [a for a in argv if not a.startswith("-")]
    # --demo: open a real demo lapping recording on startup (resolved via env/cache/release
    # download; see studio.demo). This is the packaged-app first-run path. If the demo can't be
    # resolved (offline / download failed) we do NOT fall back to the bundled gpmf clips — they have
    # zero real laps, so a first-run user would see a blank-looking studio. StudioWindow shows the
    # honest "demo unavailable" welcome state instead.
    demo_startup = False
    if not paths and "--demo" in argv:
        path = demo.resolve_demo_recording()
        if path is not None:
            paths = [path]
        else:
            demo_startup = True  # demo requested but unavailable — open the welcome state honestly
    app = QApplication(sys.argv)
    # Apply the dark "Refined Minimal" design system BEFORE constructing any widgets, so the
    # default font/palette and the pyqtgraph background are in place when the panels are built.
    theme.register_fonts()
    theme.apply_theme(app)
    window = StudioWindow(paths, full=full, demo_unavailable=demo_startup)
    window.show()
    return app.exec()
