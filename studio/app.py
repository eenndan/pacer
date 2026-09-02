"""StudioWindow: the persistent chrome that loads sessions and swaps in a fresh CentralView per
load; the panel layout lives in CentralView."""

from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

from PySide6.QtCore import QBuffer, QEvent, QIODevice, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QActionGroup, QDesktopServices, QIcon, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import (
    APP_NAME,
    chapters,
    data_quality,
    demo,
    export_data,
    export_video,
    library,
    prefs,
    share_card,
    sidecar,
    theme,
    track_db,
    units,
)
from ._signal import lap_label
from .central_view import CentralView
from .coaching_panel import OpportunitiesDialog
from .help_dialog import AboutDialog, PrivacyDialog, ShortcutsDialog
from .library_dialog import LibraryDialog
from .overlays import PBToast, WelcomeView
from .session import DEFAULT_SAMPLE, fmt_time
from .workers import DemoResolveWorker, SessionLoadWorker, VideoExportWorker

# Help ▸ Report a problem… opens this GitHub new-issue page (the only support channel; no crash
# reporting / telemetry — nothing is sent without the user opening this).
ISSUES_URL = "https://github.com/eenndan/pacer/issues/new"
# How long a transient status-bar confirmation stays up (ms). Every showMessage here reports
# something that JUST happened ("saved…", "reverted…"); with no timeout Qt leaves the last one
# up indefinitely, so minutes later the bar still asserts a stale fact about the session (B20).
STATUS_MS = 6000
# How long a RELOAD may run before the working UI is replaced by the "Loading telemetry…" card
# (ms). The load runs off the UI thread, so a live session stays usable while its successor is
# read; blanking it instantly meant even a 0.36 s reload flashed the whole window to an
# indeterminate card with zero controls — and a reload that FAILED inside this window left the
# card up forever (QA L10-01/L10-06). The FIRST load has nothing to keep on screen, so it shows
# the card immediately (see _arm_loading_placeholder).
LOAD_PLACEHOLDER_MS = 400


def _show_error_report(exc_type, exc, tb):
    """Show a themed "Something went wrong" dialog for an otherwise-unhandled exception, with the
    traceback tucked behind a collapsible Details and a button that opens the Report-a-problem page
    (the same ISSUES_URL as Help ▸ Report a problem…). Factored out of the excepthook so a test can
    call it directly (with QDesktopServices.openUrl monkeypatched) without actually raising.

    The full traceback is logged to stderr by the excepthook (dev console trace kept); here it is the
    user-facing surface — a plain-language headline + the raw class name only inside Details, matching
    the load-failure dialog's tone. Returns the dialog so a caller/test can inspect it."""
    import traceback

    summary = f"{exc_type.__name__}: {exc}" if exc is not None else exc_type.__name__
    detail = "".join(traceback.format_exception(exc_type, exc, tb))
    box = QMessageBox(
        QMessageBox.Critical, f"{APP_NAME} — something went wrong",
        f"Something went wrong and {APP_NAME} hit an unexpected error.\n\n"
        "The app is still running — you can keep working, but if this keeps happening, "
        "please report it so it can be fixed.\n\n"
        f"{summary}")
    # The BODY has to carry the product name. macOS drops a QMessageBox's window title — the
    # constructor's title argument above leaves windowTitle() == '' here, and setting it explicitly
    # does not change that — so on the one surface that shows up when the app is already
    # misbehaving, the headline is the ONLY naming, and it used to say "pacer" (QA L1-11).
    # The traceback is diagnostics for a bug report — behind the collapsible Details, not the headline.
    box.setDetailedText(detail)
    box.addButton(QMessageBox.Close)
    report_btn = box.addButton("Report a problem…", QMessageBox.ActionRole)
    box.setDefaultButton(QMessageBox.Close)
    box.exec()
    if box.clickedButton() is report_btn:
        QDesktopServices.openUrl(QUrl(ISSUES_URL))
    return box


def _excepthook(exc_type, exc, tb):
    """Top-level sys.excepthook (installed by main() AFTER the QApplication exists): log the full
    traceback to stderr AND, if a QApplication is running, surface it in a themed Report-a-problem
    dialog rather than silently dying. Without this, an unhandled exception in ANY signal handler (a
    start-line drag, a scrub tick, a menu action, the g-meter repaint) propagates to Qt's default
    handler, which on PySide6 prints to a stderr the user never sees and can silently kill the app
    with zero dialog and zero path to Report-a-problem.

    PySide6 caveat: exceptions raised inside a Qt slot are routed to sys.excepthook on modern PySide6
    (older versions swallowed them entirely); installing sys.excepthook is the correct, documented
    approach — so we do exactly that rather than over-engineering a per-slot wrapper.

    Defensive by construction: a handler that itself throws is worse than none, so the whole body is
    guarded — on any failure it falls back to the default excepthook. It does NOT sys.exit: a
    recoverable slot error should leave the app alive where it can."""
    try:
        # Keep the console trace for devs (and for the pre-/no-QApplication headless/CLI case, this is
        # the whole behaviour — a normal stderr traceback).
        sys.__excepthook__(exc_type, exc, tb)
        app = QApplication.instance()
        if app is None:
            return  # pre-QApplication / headless: the stderr trace above is the correct behaviour
        _show_error_report(exc_type, exc, tb)
    except Exception:  # noqa: BLE001 — a crashing excepthook is worse than none; degrade gracefully
        try:
            sys.__excepthook__(exc_type, exc, tb)
        except Exception:  # noqa: BLE001 — last resort: never let the handler itself propagate
            pass


def install_excepthook():
    """Install the top-level exception handler. Called from main() AFTER the QApplication exists so a
    running app surfaces the themed dialog; a headless/CLI failure (no QApplication) still prints a
    normal stderr traceback via the default hook."""
    sys.excepthook = _excepthook


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
        # Deferred loading placeholder (LOAD_PLACEHOLDER_MS): the token of the load still waiting for
        # a result, plus the single-shot timer that installs the card if it is still waiting.
        self._loading_token = None
        self._placeholder_timer = None
        # The in-flight demo-clip fetch (welcome ▸ Open demo), which reaches the network and so runs
        # off the UI thread like every other multi-second load. None when nothing is fetching.
        self._demo_worker = None
        # The ONE untimed status-bar line describing the loaded session (see _session_notice): the
        # multi-drop warning carried through the load that started it, whether a sidecar restore was
        # rejected, and the last notice actually put on the bar (so a stale one can be retracted).
        self._drop_notice = None
        self._timing_restore_failed = False
        self._notice = None
        # Reference (cross-recording compare) load bookkeeping — the reference Session.load is the SAME
        # ~1.4–4 s synchronous compute as the primary open, so it too runs on a SessionLoadWorker (a
        # freeze here was the worst kind: it hit the moat "race a friend's GoPro" path). Its own token
        # supersedes/ignores a stale reference result; a reference load never runs concurrently with a
        # primary load or a second reference load (see _load_reference_file).
        self._ref_load_token = 0
        self._ref_load_worker = None
        self._tick_timer = None  # created on the first _build_ui; reused across reloads (window-owned)
        # Persisted lap-panel state, loaded from prefs so the choices survive a relaunch and
        # passed into each fresh CentralView: the active tab (Laps/Corners/Stats/Coaching), the
        # grid-splitter sizes (None until the user drags one), and the excluded-strip choice
        # (the strip lives inside the Laps page). Each is guarded to a safe value inside prefs.
        self._lap_panel_tab = prefs.lap_panel_tab()
        self._grid_sizes = prefs.grid_sizes()
        self._excluded_visible = prefs.excluded_visible()
        # Speed display unit (km/h default), loaded from the persisted prefs so the choice survives
        # a relaunch; passed into each fresh CentralView + the video/coaching exports.
        self._speed_unit = prefs.speed_unit()
        # Colour-blind-safe semantic palette (default off = the original red/green cues). Applied to
        # the global theme palette selector at startup so every surface built below (and the View-
        # menu checkmark) reflects the persisted choice; the toggle lives in View ▸ Accessibility.
        self._colorblind = prefs.colorblind_palette()
        theme.set_palette(theme.PALETTE_COLORBLIND if self._colorblind else theme.PALETTE_STANDARD)
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
        """The local .mp4 paths in a drag's mime data, IN DROP ORDER; [] if the drag carries no MP4
        file URLs.

        A dropped FOLDER is expanded one level to the .MP4 files directly inside it. A GoPro card
        hands the user a folder of chapters, and the welcome copy invites "a GoPro recording" — yet
        dropping that folder was a total silent no-op: the filter returned [], so dragEnterEvent
        never accepted the drag and dropEvent never ran (QA L10-10). One level is deliberate: the
        recording GROUPING is chapters.group_into_recordings' job (and discover_siblings still
        chains the rest), so this only has to turn "the folder" into "the files in it". A folder
        with no .MP4s inside still yields [] and is still refused — the drag cursor saying no is
        the correct answer there.

        Order is preserved deliberately: _open_recordings opens the FIRST recording dropped, and
        sorting here silently made that the ALPHABETICALLY first instead (QA L10-02). Nothing
        downstream needs a sorted list — chapters.group_into_recordings orders each recording's
        chapters by chapter index, and _open_recordings re-orders the merged set again. The entries
        WITHIN one expanded folder are sorted, because os.listdir order is arbitrary and the user
        dropped no order for them.
        """
        if not mime.hasUrls():
            return []
        out: list[str] = []
        for url in mime.urls():
            p = url.toLocalFile()
            if not p:
                continue
            if os.path.isdir(p):
                try:
                    names = sorted(os.listdir(p))
                except OSError:  # unreadable folder: nothing to offer, same as an empty one
                    continue
                entries = [os.path.join(p, n) for n in names if n.lower().endswith(".mp4")]
                out += [e for e in entries if os.path.isfile(e)]  # never a .MP4-named subfolder
            elif p.lower().endswith(".mp4"):
                out.append(p)
        return out

    def dragEnterEvent(self, event):
        """Accept a drag only if it carries at least one .mp4 (so the cursor shows it's droppable)."""
        if self._dropped_mp4s(event.mimeData()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        """Load the dropped GoPro file(s) through the guarded _load path.

        Files are first GROUPED into distinct recordings (chapters.group_into_recordings — same
        folder + GoPro recording id NNNN = one recording). Dropping the chapters of ONE recording
        loads it exactly as before; dropping SEVERAL unrelated recordings must NOT fold them onto one
        clock (which fabricates bogus laps), so we open only the FIRST and say so — the user opens the
        rest one at a time (a batch-import queue is a noted follow-up, not this)."""
        paths = self._dropped_mp4s(event.mimeData())
        if not paths:
            return
        event.acceptProposedAction()
        self._open_recordings(paths)

    def _open_recordings(self, paths: list[str]):
        """Group `paths` into recordings and load the first, never merging unrelated recordings.

        Expands the chosen recording's full on-disk chapter set via discover_siblings (so a single
        opened chapter still chains its siblings, matching --full / File ▸ Open), then loads that ONE
        recording. On a multi-recording drop it surfaces a clear, non-modal status message naming what
        was opened. Shared by dropEvent (and any future multi-selection open path).

        That warning is CARRIED THROUGH the load (drop_notice) rather than left to expire on its own:
        it used to be a 6 s transient that _on_session_loaded overwrote after 2.5-3.6 s, so the one
        message naming the recordings that were NOT opened was erased by the load it had just started
        and nothing in the window mentioned them again (QA L10-02)."""
        groups = chapters.group_into_recordings(paths)
        if not groups:
            return
        first = groups[0]
        # Expand the chosen recording's full chapter set. group_into_recordings only saw the dropped
        # paths; discover_siblings finds the rest of THIS recording's chapters on disk. UNION the two
        # (dropped chapters ∪ discovered siblings), ordered by chapter index and de-duped, so neither
        # an explicitly-dropped chapter nor an on-disk sibling is ever lost — and dropping the two
        # chapters of one recording loads exactly those two (the common case stays unchanged).
        to_load = chapters.order_chapters(first + chapters.discover_siblings(first[0]))
        drop_notice = None
        if len(groups) > 1:
            drop_notice = (
                f"Dropped {len(groups)} recordings — opened {chapters.recording_label(to_load)}. "
                "Open the others one at a time.")
        self._load(to_load, drop_notice=drop_notice)
        if drop_notice:
            # Transient here so a FAILED load doesn't strand it; the post-load notice re-states it
            # untimed (see _session_notice), so the fact stays on screen for the whole session.
            self.statusBar().showMessage(drop_notice, STATUS_MS)

    def _show_welcome(self, error: str | None = None):
        """Install the no-recording welcome empty state (also the first-load-failure fallback)."""
        self._paths = getattr(self, "_paths", [])
        self.setWindowTitle(APP_NAME)
        self.setCentralWidget(WelcomeView(self._open_file, self._open_demo, error, parent=self))
        if getattr(self, "_full_action", None) is not None:
            self._full_action.setEnabled(False)

    def _open_demo(self):
        """Welcome-screen "Open demo": resolve a real demo lapping recording OFF the UI thread
        (env / cache / a one-time release download — see studio.demo), then load it.

        The resolve used to run inline in this slot, so a first run with no cache did a network
        fetch on the UI thread: the window froze with the welcome screen still painted, the button
        still enabled and undepressed, no cursor change and no message — nothing on screen said the
        click had been received (QA L10-03). It runs on a DemoResolveWorker now; the button says
        what it is doing, and the loading card comes up if the fetch is slow enough to need it.

        If it can't be resolved (offline / download failed), DON'T silently load the bundled sample
        clip — it has zero real laps, so the user would land in a blank-looking studio that reads as
        broken. Instead keep the welcome screen and say so honestly, so they can retry or open their
        own footage."""
        if self._demo_worker is not None and self._demo_worker.isRunning():
            return  # already fetching; the button is disabled, but never start a second fetch
        self._set_demo_busy(True)
        self._arm_demo_placeholder()
        worker = DemoResolveWorker(self._load_token)
        self._demo_worker = worker
        self._load_workers.add(worker)  # hold it so the QThread isn't GC'd mid-fetch; drained on close
        worker.resolved.connect(self._on_demo_resolved)
        worker.finished.connect(lambda w=worker: self._on_demo_worker_finished(w))
        worker.start()

    def _set_demo_busy(self, busy: bool):
        """Reflect an in-flight demo fetch on the welcome screen: the button says what it is doing
        and stops accepting clicks. This is the affordance the synchronous version had none of —
        and it is on screen for the whole fetch, not just after it. No-op once the welcome view has
        been replaced (the load it started is now the thing on screen)."""
        btn = getattr(self.centralWidget(), "demo_btn", None)
        if btn is None:
            return
        btn.setEnabled(not busy)
        btn.setText("Fetching the demo clip…" if busy else "Open demo")

    def _arm_demo_placeholder(self):
        """Install the loading card only if the demo fetch is still running LOAD_PLACEHOLDER_MS
        later — the same grace period a reload gets. A cached/env demo resolves in microseconds and
        goes straight into a real load, so the card would only ever be a flash there; a cold first
        run downloads a clip and genuinely needs it."""
        self._cancel_placeholder_timer()
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(self._on_demo_placeholder_due)
        self._placeholder_timer = timer
        timer.start(LOAD_PLACEHOLDER_MS)

    def _on_demo_placeholder_due(self):
        """LOAD_PLACEHOLDER_MS elapsed with the demo fetch still running: say so on the card the
        load itself would use, so the wait reads as one continuous operation."""
        self._placeholder_timer = None
        if self._demo_worker is not None and self._demo_worker.isRunning():
            self._show_loading_placeholder([], title="Fetching the demo clip…")

    def _on_demo_resolved(self, token: int, path):
        """The demo resolved (on the UI thread, via a queued signal): load it, or re-show the
        welcome state with an honest message. A result is DROPPED if a load started while the fetch
        was running (the user opened their own recording rather than waiting) — same token rule the
        session loads use."""
        self._cancel_placeholder_timer()
        if token != self._load_token:
            return  # superseded: something else is loading, don't yank the window to the demo
        self._set_demo_busy(False)
        if path is None:
            self._show_welcome(error="Demo clip unavailable — check your connection and retry, "
                                     "or drop your own GoPro .mp4 to get your laps.")
            return
        self._load([path])

    def _on_demo_worker_finished(self, worker):
        """The demo fetch's QThread finished: drop it from the in-flight set (see _open_demo)."""
        self._load_workers.discard(worker)
        if self._demo_worker is worker:
            self._demo_worker = None

    # ------------------------------------------------------------------ loading
    def _load(self, paths: list[str], drop_notice: str | None = None):
        """Load (or reload) the session for `paths` OFF the UI thread, then (in _on_session_loaded)
        build a fresh CentralView and swap it in. The window keeps the load orchestration +
        `session`/`_paths`; each panel captures `session` at construction.

        `drop_notice` is a one-line fact about HOW this load was requested (today: the
        multi-recording drop warning) that must outlive the load rather than be overwritten by it —
        see _session_notice. Every other caller passes none, which clears any previous one.

        Session.load is a ~1.4–4 s synchronous call, so it runs on a worker QThread: the placeholder
        shows immediately and the window stays responsive. SINGLE-FLIGHT: only ONE load runs at a
        time — a superseding _load (e.g. a second drag-drop) is QUEUED rather than run concurrently
        (no point loading two recordings at once, and serializing keeps the supersede ordering
        clean). It shows the placeholder, bumps the token, and starts when the current worker
        finishes; the older in-flight result is ignored by token (see the completion slots)."""
        print("studio: loading telemetry…", flush=True)
        self._drop_notice = drop_notice
        # Bump the token: any in-flight worker started by a previous _load is now stale and its
        # result will be ignored when it finishes.
        self._load_token += 1
        token = self._load_token
        self._loading_token = token  # cleared by whichever completion slot applies this result
        # Show the placeholder so the window isn't a black void during the load (the load no longer
        # blocks the event loop, so the placeholder also stays live/paintable throughout) — but not
        # OVER a working session until the load proves slow.
        self._arm_loading_placeholder(token, paths)
        # Single-flight: if a worker is already running, remember only the LATEST request and start
        # it when the current one finishes — no point running two loads at once, and queuing keeps
        # the supersede ordering clean.
        if self._load_worker is not None and self._load_worker.isRunning():
            self._pending_load = (token, list(paths))
            return
        self._start_load_worker(token, paths)

    def _arm_loading_placeholder(self, token: int, paths: list[str]):
        """Decide WHEN the "Loading telemetry…" card replaces what's on screen.

        FIRST load — immediately: there is nothing else to show (and _show_loading_placeholder is
        what brings the window up). RELOAD over a working session — only if the load is still
        running LOAD_PLACEHOLDER_MS later. The load is off-thread, so the loaded session stays fully
        usable meanwhile; blanking it the instant a reload starts meant a 0.36 s reload flashed the
        whole window to a control-less card, and a reload that failed in that window left the card
        up forever with the session unreachable (QA L10-01/L10-06)."""
        self._cancel_placeholder_timer()
        if self.view is None or self.centralWidget() is not self.view:
            self._show_loading_placeholder(paths, on_cancel=lambda: self._cancel_load(token))
            return
        timer = QTimer(self)
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_placeholder_due(token, list(paths)))
        self._placeholder_timer = timer
        timer.start(LOAD_PLACEHOLDER_MS)

    def _on_placeholder_due(self, token: int, paths: list[str]):
        """LOAD_PLACEHOLDER_MS elapsed: install the loading card only if THIS load is still the
        current one and still waiting for its worker (a result that already landed cleared
        _loading_token, and a superseding _load bumped it)."""
        self._placeholder_timer = None
        if token == self._load_token and self._loading_token == token:
            self._show_loading_placeholder(paths, on_cancel=lambda: self._cancel_load(token))

    def _cancel_placeholder_timer(self):
        """Stop the pending loading-card timer, if any (a load settled, was superseded, or the
        window is closing)."""
        timer = getattr(self, "_placeholder_timer", None)  # guarded: closeEvent runs in partial harnesses
        self._placeholder_timer = None
        if timer is not None:
            timer.stop()

    def _start_load_worker(self, token: int, paths: list[str]):
        """Spawn the single in-flight load worker for `token` (see _load's single-flight rule)."""
        worker = SessionLoadWorker(token, paths)
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
        """Let any in-flight load worker (or the demo fetch, which is held in the same set) finish
        before teardown, bounded so this can never hang on a stuck worker. Pump the event loop in
        short slices (so the worker's queued completion signals — incl. _on_worker_finished
        launching a still-pending load — can drain) and wait briefly per worker, giving up after
        `deadline_s`. The token is bumped past every in-flight worker, so whatever they emit is
        ignored regardless."""
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
        self._cancel_placeholder_timer()  # no loading card can appear mid-teardown
        # Bump the load token past every in-flight worker so nothing that lands mid-teardown is
        # applied — in particular a demo fetch that resolves now must not kick off a whole new load
        # into a window that is closing (_on_demo_resolved drops it on the same token rule).
        self._load_token += 1
        # Bump the reference token past any in-flight reference worker too, so a reference load that
        # finishes mid-teardown is ignored (its set_reference_session apply is dropped by the token
        # guard) — matching how the primary load's token already supersedes any in-flight worker.
        self._ref_load_token += 1
        self._drain_load_workers()
        super().closeEvent(event)

    def _on_session_loaded(self, token: int, paths: list[str], session):
        """Successful load completion (on the UI thread, via a queued signal): commit the session and
        build the UI. Ignores a STALE result — a newer _load superseded this one, so applying it
        would clobber the current (good) session. This is the EXACT former post-load body of _load."""
        if token != self._load_token:
            return  # superseded by a newer load; drop this result
        self._loading_token = None
        self._cancel_placeholder_timer()  # the result beat the card: never blank the window now
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
        self._timing_restore_failed = False
        data = sidecar.load(self._sidecar_path) if self._sidecar_path else None
        if data is not None:
            if session.apply_timing_lines_latlon(data["start"], data["sectors"],
                                                 confirmed=data["confirmed"]):
                print(f"studio: restored saved timing lines from "
                      f"{os.path.basename(self._sidecar_path)}", flush=True)
            else:
                self._timing_restore_failed = True

        label = chapters.recording_label(paths)
        self.setWindowTitle(f"{APP_NAME} — {label}" if label else APP_NAME)
        self._build_ui()
        # One-line, non-fatal: the statusbar mirrors the console "studio:" notice style.
        notice = self._apply_session_notice()
        if notice:
            print(f"studio: {notice}", flush=True)

        # Record this recording in the local session library (see _update_library) and, if this
        # session's best lap beats the track's prior PB on verified timing, celebrate it.
        moment = self._update_library(paths)
        if moment is not None:
            self._show_pb_moment(moment)
        self.loadFinished.emit()

    def _session_notice(self) -> str | None:
        """The ONE untimed status-bar line for the CURRENTLY loaded session, derived from live state
        so it can be re-decided at any time (not just at load).

        Composed, highest concern first, from:
          * no valid laps — every panel renders blank, so say so. The bar states the SHARED
            headline (data_quality.NO_LAPS_HEADLINE) and stops there: it used to author its own
            fourth phrasing of the fact and restate the lap table's reason almost verbatim, so one
            frame carried four wordings of one sentence (QA L10-08). The reason and the "drag the
            start/finish line" next action belong to the panels that have room for them. Supersedes
            the timing notices (a 0-lap recording has no lap timing to fix either way);
          * timing TRUST — a start line that is neither a detected track nor user-placed makes every
            lap time arbitrary. Keyed on `session.timing_verified`, the SAME predicate the map's
            trust strip and provisional cue read, so the bar retracts the "drag it into place" line
            in the same beat the map does. It used to be decided ONCE at load from `track_name`, so
            it survived byte-identical across the very drag that answered it (QA MAP-06);
          * the multi-drop warning carried in from _open_recordings (QA L10-02), appended so the
            recordings that were NOT opened stay named for the life of the session.
        getattr-guarded for the partial test harnesses that build a window via __new__."""
        session = getattr(self, "session", None)
        if session is None:
            return None
        notice = None
        if not session.valid_lap_ids():
            notice = data_quality.NO_LAPS_HEADLINE
        elif not session.timing_verified:
            if getattr(self, "_timing_restore_failed", False):
                notice = ("saved timing lines don't match this recording — "
                          "reverted to the auto-fitted start line")
            else:
                # The start line was auto-fitted, so lap times are arbitrary until the user drags it
                # into place. To register the track: studio/dev/print_track_entry.py.
                notice = ("unknown track — start/finish line was auto-fitted; "
                          "drag it into place to fix lap timing")
        drop_notice = getattr(self, "_drop_notice", None)
        return " · ".join(p for p in (notice, drop_notice) if p) or None

    def _apply_session_notice(self) -> str | None:
        """Put the current _session_notice on the status bar and return it.

        DELIBERATELY untimed (unlike the transient confirmations, B20): it describes the LOADED
        SESSION and stays true until that changes. Only ever retracts a notice of OUR OWN — a
        transient confirmation someone else wrote is left to expire on its own timer.

        Connected to the view's timingEdited (in _build_ui) so a timing-line drag or undo re-decides
        it from the same seam that rebuilds the derived views."""
        notice = self._session_notice()
        bar = self.statusBar()
        previous = getattr(self, "_notice", None)
        if notice:
            if bar.currentMessage() != notice:
                bar.showMessage(notice)
        elif previous and bar.currentMessage() == previous:
            bar.clearMessage()
        self._notice = notice
        return notice

    def _on_load_failed_async(self, token: int, paths: list[str], exc: Exception):
        """Failed load completion (on the UI thread, via a queued signal): drop a STALE result, else
        surface the error via the existing _on_load_failed (welcome-state fallback on first load,
        good session kept on a reload failure)."""
        if token != self._load_token:
            return  # superseded by a newer load; drop this result
        self._loading_token = None
        self._cancel_placeholder_timer()  # the failure beat the card: never blank the window now
        self._on_load_failed(paths, exc)
        self.loadFinished.emit()

    def _show_loading_placeholder(self, paths: list[str], title: str | None = None,
                                  on_cancel=None):
        """Immediate visual feedback while Session.load runs on a worker thread: install a centered
        "Loading telemetry…" card, show the window, and force one synchronous paint so it appears
        right away. Replaced by the real UI in _build_ui.

        `title` overrides the headline (the demo fetch shows its own on the same card, so a cold
        first run reads as one continuous wait rather than two unrelated ones). `on_cancel`, when
        given, adds the Cancel button: the card used to carry ZERO controls, so the app's longest
        routine wait was the one thing in it a user could not back out of — while its own video
        export has offered both a determinate bar and a Cancel all along (QA L10-06). The bar stays
        indeterminate: Session.load reports no progress, and a bar that invents one would be a lie."""
        label = chapters.recording_label(paths)
        headline = title or "Loading telemetry…"
        container = QWidget()
        v = QVBoxLayout(container)
        v.setAlignment(Qt.AlignCenter)
        v.setSpacing(18)
        title_label = QLabel(f"{headline}\n\n{label}" if label else headline)
        title_label.setProperty("role", "LoadingTitle")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setWordWrap(True)
        v.addWidget(title_label, 0, Qt.AlignCenter)
        bar = QProgressBar()
        bar.setObjectName("LoadingBar")
        bar.setRange(0, 0)          # indeterminate: self-animates, no timer to leak, dies with the widget
        bar.setTextVisible(False)
        bar.setFixedWidth(220)
        v.addWidget(bar, 0, Qt.AlignCenter)
        if on_cancel is not None:
            cancel = QPushButton("Cancel")
            cancel.setObjectName("LoadingCancel")
            cancel.clicked.connect(on_cancel)
            v.addWidget(cancel, 0, Qt.AlignCenter)
        self.setCentralWidget(container)
        if not self.isVisible():
            self.show()
        app = QApplication.instance()
        if app is not None:
            app.processEvents()

    def _cancel_load(self, token: int):
        """Cancel on the loading card: stop WAITING for load `token` and hand the window back.

        The read is not interrupted — Session.load is one synchronous call inside the worker with
        no cooperative checkpoint — so the worker runs to completion and its result is dropped by
        the token guard, exactly as a superseding _load's result already is. What the user gets back
        immediately is the session they had (rebuilt through the same path a failed reload uses), or
        the welcome state when there was nothing loaded yet. Ignores a stale click: the card on
        screen belongs to a load that has already settled or been superseded."""
        if token != self._load_token:
            return
        self._load_token += 1        # the in-flight result is now stale and will be dropped
        self._loading_token = None
        self._pending_load = None    # and nothing queued behind it starts either
        self._cancel_placeholder_timer()
        if getattr(self, "session", None) is not None and self.view is not None:
            self._build_ui()
            message = "Load cancelled — kept the recording already open."
        else:
            self._show_welcome()
            message = "Load cancelled."
        self.statusBar().showMessage(message, STATUS_MS)

    def _on_load_failed(self, paths: list[str], exc: Exception):
        """A session load failed (missing / not-a-GoPro / no-GPS file). Show a clear, non-fatal error
        in PLAIN LANGUAGE (never the raw Python class name as the headline — that reads as amateur)
        and keep the app open. On the very first load there is no UI yet, so install the welcome
        empty state so the window still opens.

        A failed RELOAD hands the working session back BEFORE the dialog claims it is unchanged.
        Since the load went off-thread, _load may already have swapped the live view out for the
        loading card, and NOTHING else rebuilt it: the window was stranded on an endless "Loading
        telemetry…" card with 0 controls and the session unreachable, while the dialog said the
        previous session was fine (QA L10-01). _build_ui's first act is to dispose the outgoing
        view, so re-running it is a clean swap; the deferred card (_arm_loading_placeholder) means
        a fast failure never disturbs the view at all and this rebuild is skipped.

        The raw `type(exc).__name__: exc` is logged to the console and tucked behind the dialog's
        "Show details" — diagnostics for a bug report, not the user-facing message."""
        offending = paths[0] if paths else "(no file)"
        detail = f"{type(exc).__name__}: {exc}"
        message = self._load_failure_message(paths, exc)
        print(f"studio: failed to load {offending}: {detail}", flush=True)
        reload_failed = hasattr(self, "session")
        view = getattr(self, "view", None)  # guarded: partial harnesses set session without a view
        if reload_failed and view is not None and self.centralWidget() is not view:
            # The loading card is up over a still-good session: put the session's UI back.
            self._build_ui()
            self._apply_session_notice()
        # The reassurance is only stated where it is TRUE (and now verifiable on screen behind the
        # dialog); a first-load failure has no previous session, so the line is dropped there.
        tail = "\n\nYour loaded session is unchanged." if reload_failed else ""
        box = QMessageBox(QMessageBox.Critical, f"{APP_NAME} — could not load recording",
                          f"{message}\n\n{offending}{tail}", parent=self)
        # Raw exception text lives in the collapsible details, not the headline.
        box.setDetailedText(detail)
        box.exec()
        # First-load failure: no central widget yet — show the welcome empty state (with the plain
        # message) so the window stays open and the user can drop/open another recording.
        if not reload_failed:
            # Seed _paths for the failed-first-load case (nothing else has set it, yet readers like
            # "Load full recording" stay reachable). A failed reload keeps the good _paths instead.
            self._paths = list(paths)
            self._show_welcome(error=f"{message}\n\n{offending}")

    @staticmethod
    def _load_failure_message(paths: list[str], exc: Exception) -> str:
        """Map a load failure to a plain-language sentence that names the CASE and a next action (no
        raw Python class name).

        Every ctor failure below GPMFSource surfaces as the same RuntimeError("Failed to open
        file: …"), so the exception alone cannot tell a folder from an empty file from a truncated
        real GoPro chapter — five structurally different malformed inputs collapsed onto two
        headlines, and a truncated REAL GoPro chapter was reported as "not a GoPro recording"
        (QA L10-04). The path is therefore inspected first, cheapest/most-certain check first:

          * a directory the user aimed at instead of the chapters inside it;
          * a path that isn't there at all;
          * a 0-byte file (an interrupted copy off the SD card);
          * an OSError — present but unreadable (permissions, still copying);
          * opens but carries no GPMF/GPS track — split by whether the NAME is a GoPro chapter name
            (a truncated/incomplete copy of real footage) or not (the wrong file entirely);
          * anything else — a generic, honest fallback (the raw class name stays in the details/log).

        Pure + static: no Qt, no window state, so the whole table is unit-testable (tests/
        test_load_failure.py). A recording that OPENS but has zero GPS fixes does NOT raise — it
        loads as a 0-valid-lap session (see _session_notice), so it never reaches here."""
        offending = paths[0] if paths else None
        if offending is None:
            return ("Couldn't read telemetry from this recording — it may be corrupt or "
                    "unsupported. Try copying it off the SD card again.")
        if os.path.isdir(offending):
            return "That's a folder, not a recording — open the .MP4 files inside it."
        if not os.path.exists(offending):
            return ("Couldn't find that file — it may have been moved, renamed or deleted. "
                    "Open it again from where it is now.")
        try:
            empty = os.path.getsize(offending) == 0
        except OSError:
            empty = False
        if empty:
            return "That file is empty (0 bytes) — copy it off the camera's SD card again."
        if isinstance(exc, OSError):
            return ("Couldn't read that file — check it has finished copying and that you have "
                    "permission to open it.")
        if isinstance(exc, RuntimeError) and "open file" in str(exc).lower():
            # GPMFSource couldn't find a GPMF/GPS track in this MP4.
            if chapters.parse_gopro_name(offending) is not None:
                return ("This is a GoPro file, but its telemetry track couldn't be read — the copy "
                        "is probably incomplete. Copy it off the SD card again.")
            return ("This doesn't look like a GoPro recording with GPS metadata — open the "
                    "original .MP4 the camera wrote.")
        # Unknown cause — honest generic message; the raw class name stays in the details/log only.
        return ("Couldn't read telemetry from this recording — it may be corrupt or unsupported. "
                "Try copying it off the SD card again.")

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
                                parent=self,
                                speed_unit=getattr(self, "_speed_unit", units.DEFAULT_UNIT),
                                excluded_visible=self._excluded_visible,
                                lap_tab=self._lap_panel_tab,
                                grid_sizes=self._grid_sizes)
        # Keep Edit ▸ Undo's enabled state in sync with the session's undo stack as lines are dragged.
        self.view.timingEdited.connect(self._sync_edit_menu)
        # Re-decide the untimed load notice from the same seam that rebuilds the derived views, so
        # placing the start/finish line retracts the "drag it into place" line instead of leaving it
        # asserting a state the drag just answered (QA MAP-06).
        self.view.timingEdited.connect(self._apply_session_notice)
        # Persist the lap-panel tab + any grid-splitter drag across reloads/relaunches.
        self.view.lapTabChanged.connect(self._on_lap_tab_changed)
        self.view.gridSizesChanged.connect(self._on_grid_sizes_changed)
        # Video focus (⤢ / double-click the video): the view maximized the video panel; the window
        # goes native-fullscreen (True) / normal (False) so the video fills the whole screen.
        self.view.videoFocusChanged.connect(self._on_video_focus_changed)
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
        # The session-only menu items (and their shortcuts) come alive with the view, not on the
        # next menu pull-down.
        self._sync_coaching_menu()
        self._sync_view_menu()
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
        # The shareable lap card (image): the one-tap social output. Two actions — save the PNG,
        # or copy it to the clipboard to paste straight into a chat. Greyed (in _sync_export_menu)
        # when there's no VERIFIED lap to brag about (an unverified/provisional time never becomes
        # a bragging card); the render itself honesty-stamps a data-quality-degraded time.
        self._export_menu.addSeparator()
        self._share_card_action = self._export_menu.addAction("Lap card (image)…")
        self._share_card_action.setToolTip(
            "Save a shareable lap card (PNG): your best lap, Δ to your ideal, the #1 corner to "
            "find time, and a speed map — the one-tap thing to post after a good session")
        self._share_card_action.triggered.connect(self._export_share_card)
        self._copy_card_action = self._export_menu.addAction("Copy lap card")
        self._copy_card_action.setToolTip(
            "Copy the shareable lap card image to the clipboard — paste it straight into a chat")
        self._copy_card_action.triggered.connect(self._copy_share_card)
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
        # Data portability: reveal the app-support folder that holds library.json (so the durable
        # index is findable), and back it up to a chosen file — turning it from an unrecoverable
        # blob into something the user can copy/restore. (Also surfaced in the Library dialog.)
        self._reveal_library_action = menu.addAction("Reveal library in Finder")
        self._reveal_library_action.setToolTip(
            "Open the folder that holds your library index (library.json), so you can find, copy "
            "or back it up yourself")
        self._reveal_library_action.triggered.connect(self._reveal_library)
        self._backup_library_action = menu.addAction("Back up library…")
        self._backup_library_action.setToolTip(
            "Save a copy of your library index (library.json) to a location you choose")
        self._backup_library_action.triggered.connect(self._backup_library)
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
        coaching_menu.aboutToShow.connect(self._sync_coaching_menu)
        self._ref_action = coaching_menu.addAction("Load reference recording…")
        self._ref_action.setToolTip(
            "Race a friend's GoPro: pick another recording of this track (yours or a friend's) to "
            "compare side-by-side — its best lap becomes the Δ / map / table reference instead of "
            "this session's own best lap. The track is matched by GPS location, so it works even "
            "when the track isn't in the database.")
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
        # F10 Opportunities: every corner ranked by time lost vs your own best lap (recomputed
        # per open; the Coaching TAB carries the top-3 shortlist).
        coaching_menu.addSeparator()
        self._opportunities_action = coaching_menu.addAction("Opportunities…")
        self._opportunities_action.setToolTip(
            "Where to find time vs your own best lap: every corner ranked by realistic time lost "
            "(median of your clean laps), each with the measured reason and a jump-to.")
        self._opportunities_action.triggered.connect(self._open_opportunities)

        # Left-column declutter (the "calm default"): fully show/hide the coaching panel and the
        # excluded strip. Mirrors the consistency toggle EXACTLY — a checkable QAction whose state is
        # persisted on the window (via prefs) and applied to each fresh CentralView on reload. These
        # HIDE the whole panel (header included); the in-place chevron on each panel only collapses
        # its body. Both default SHOWN (the calm default keeps the re-open header visible) — coaching
        # ships collapsed, excluded ships as its own one-liner.
        view_menu = self.menuBar().addMenu("&View")
        view_menu.aboutToShow.connect(self._sync_view_menu)
        # Whole-window full screen (the native macOS ⌘⌃F): a checkable toggle whose text flips
        # Enter/Exit. The macOS green traffic-light already gives native fullscreen for a QMainWindow;
        # this is the menu item + keyboard shortcut on top of it, kept in sync via changeEvent. Esc
        # also exits (keyPressEvent). Parented to the persistent window so it survives every view swap.
        self._fullscreen_action = view_menu.addAction("Enter Full Screen")
        self._fullscreen_action.setShortcut(QKeySequence.FullScreen)  # ⌘⌃F on macOS
        self._fullscreen_action.setToolTip(
            "Show pacer full screen (⌘⌃F). Press Esc or ⌘⌃F again to exit.")
        self._fullscreen_action.triggered.connect(self._toggle_fullscreen)
        # One-action route to the full-window statistics dashboard: flip the lap panel to its
        # Stats page + maximize it (CentralView.show_stats_maximized; a second trigger restores
        # the grid). Parented to the persistent window; a no-op before a session is loaded.
        self._stats_action = view_menu.addAction("Session statistics")
        self._stats_action.setShortcut(QKeySequence("Ctrl+Shift+S"))  # ⌘⇧S on macOS
        self._stats_action.setToolTip(
            "Open the session-statistics dashboard full-window (⌘⇧S): totals, pace "
            "distribution, top speed, peak g, the g-g friction circle, brake/coast totals "
            "and a per-lap table. Press again (or ⤢) to restore the grid.")
        self._stats_action.triggered.connect(self._show_session_statistics)
        view_menu.addSeparator()
        # The lap panel's pages are REAL tabs now (Laps · Corners · Stats · Coaching, digits
        # 1-4) — the old show/hide toggles for the coaching + consistency strips died with the
        # strips themselves. Only the excluded strip (inside the Laps page) keeps a toggle.
        self._excluded_action = view_menu.addAction("Show excluded laps")
        self._excluded_action.setCheckable(True)
        self._excluded_action.setChecked(self._excluded_visible)
        self._excluded_action.setToolTip(
            "Show the ⊘ excluded-laps strip under the lap table — laps the median band left out of "
            "your times and bests (a mis-segmented, out- or in-lap). Only ever shown when there are "
            "any; click its header to expand the list.")
        self._excluded_action.toggled.connect(self._on_excluded_toggled)

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

        # View ▸ Colour-blind-safe cues: swap the red/green delta + best colours for a blue/orange
        # deuteranopia-safe pair across the delta readout, lap table and rainbow map. Off by default
        # (no change for existing users); the choice persists across relaunches (prefs).
        self._colorblind_action = view_menu.addAction("Colour-blind-safe cues")
        self._colorblind_action.setCheckable(True)
        self._colorblind_action.setChecked(self._colorblind)
        self._colorblind_action.setToolTip(
            "Swap the red/green ahead/behind + best-lap colours for a colour-blind-safe blue/orange "
            "palette (across the Δ readout, lap table and track-map rainbow). The non-colour cues "
            "(± sign, ▲/▼ arrows, ★ best marks) are always shown regardless of this setting.")
        self._colorblind_action.toggled.connect(self._on_colorblind_toggled)

        # Help menu: the shortcut reference (also F1 / ?) and an About card (help_dialog.py).
        #
        # ELLIPSIS CONVENTION (recorded because a QA pass inferred the wrong one and filed against
        # it — QA L1-10): a trailing "…" means the command needs MORE INFORMATION before it can
        # complete (Apple's rule), NOT "it opens a dialog". So "Open…", "Save as track…" and
        # "Back up library…" carry one — each raises a picker — while "Keyboard shortcuts",
        # "Your data && privacy" and "About {APP_NAME}" correctly do not: they open a card that
        # asks the user for nothing. "Enter/Exit Full Screen" and "About …" keep macOS's own
        # Title-case wording for the two items every Mac app shares; the sentence case elsewhere
        # is the house style. The one real outlier is Coaching ▸ "Opportunities…", an informational
        # ranking that needs no input — left alone here only because coaching_panel.py's in-app
        # pointer copy names it with the ellipsis; fixing the pair is a one-line follow-up.
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
        self._about_action = help_menu.addAction(f"About {APP_NAME}")
        self._about_action.setToolTip(f"What {APP_NAME} is and what it does")
        self._about_action.triggered.connect(self._show_about)
        self._report_action = help_menu.addAction("Report a problem…")
        self._report_action.setToolTip(
            "Open a new issue on GitHub (include your GoPro model/firmware and what you were doing)")
        self._report_action.triggered.connect(self._report_problem)

        # Seed the session-dependent enablement once, so the welcome screen opens with the
        # session-only items already greyed (and their shortcuts inert) rather than waiting for
        # the user to pull the menu down. _build_ui re-runs both after every load.
        self._sync_coaching_menu()
        self._sync_view_menu()

    def _sync_coaching_menu(self):
        """Grey Coaching's session-only items out until a session is loaded (the Coaching menu's
        aboutToShow, mirroring _sync_export_menu / _sync_edit_menu). Both handlers early-return
        with no session, so before this the welcome screen offered a reference picker and an
        Opportunities item that did literally nothing (QA L1-06). Clear reference / Compare vs
        reference stay owned by _apply_reference_change — a reference cannot exist without a
        session, so they are already off here."""
        has = hasattr(self, "session")
        for name in ("_ref_action", "_opportunities_action"):
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(has)

    def _sync_view_menu(self):
        """Grey View's session-only items out until a view exists (the View menu's aboutToShow).
        Full screen, Units and the colour-blind palette all work on the welcome screen and stay
        enabled; ⌘⇧S Session statistics and the excluded-laps toggle both need a CentralView, and
        a disabled QAction's shortcut is inert too — which is what makes ⌘⇧S stop being a silent
        no-op before the first load (QA L1-06)."""
        has_view = getattr(self, "view", None) is not None
        for name in ("_stats_action", "_excluded_action"):
            action = getattr(self, name, None)
            if action is not None:
                action.setEnabled(has_view)

    def _show_shortcuts(self):
        """Help ▸ Keyboard shortcuts (also F1 / ?): the read-only shortcut reference."""
        ShortcutsDialog(self).exec()

    def _show_about(self):
        """Help ▸ About pacer studio: the small themed About card (name / tagline / blurb)."""
        AboutDialog(self).exec()

    def _report_problem(self):
        """Help ▸ Report a problem…: open the GitHub new-issue page in the browser — a support
        channel so a user who hits one of the confidently-wrong-input cases has somewhere to go
        (there is no crash reporting / telemetry; nothing is sent automatically)."""
        QDesktopServices.openUrl(QUrl(ISSUES_URL))

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
            self.statusBar().showMessage("reverted the last start/finish-line edit", STATUS_MS)

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
        # 1-4 → the lap panel's tabs (Laps · Corners · Stats · Coaching); no-op before a load.
        for digit, tab in ((Qt.Key_1, 0), (Qt.Key_2, 1), (Qt.Key_3, 2), (Qt.Key_4, 3)):
            shortcut(digit, lambda t=tab: self._select_lap_tab(t))
        # ? → shortcut reference (keep in sync with help_dialog.SHORTCUT_GROUPS).
        shortcut(Qt.Key_Question, self._show_shortcuts)

    def _video_do(self, fn):
        """Run `fn` against the current VideoView, resolved at call time (since _build_ui swaps it);
        no-op before the first load."""
        view = getattr(self, "view", None)
        if view is not None:
            fn(view.video)

    def _select_lap_tab(self, index: int):
        """Digit shortcut 1-4 → the lap panel's tab, resolved at call time; no-op before the
        first load (the persisted choice still seeds the next view)."""
        view = getattr(self, "view", None)
        if view is not None:
            view.select_lap_tab(index)

    # ----------------------------------------------------- full screen (window + video focus)
    def _toggle_fullscreen(self):
        """View ▸ Enter/Exit Full Screen (⌘⌃F): flip the whole window between fullscreen and normal.
        The menu text + the native state stay in sync via changeEvent (which fires for both this and
        the green traffic-light button / a video-focus exit)."""
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def _show_session_statistics(self):
        """View ▸ Session statistics (⌘⇧S): one action to the full-window statistics dashboard
        — flip the lap panel to its Stats page and maximize it (a second trigger restores the
        grid; CentralView.show_stats_maximized owns the toggle). No-op on the welcome screen
        (no session view yet)."""
        view = getattr(self, "view", None)
        if view is not None and hasattr(view, "show_stats_maximized"):
            view.show_stats_maximized()

    def _sync_fullscreen_action_text(self):
        """Keep the View menu item's text (Enter/Exit Full Screen) matching the window's real state —
        driven from changeEvent so it tracks the menu item, the green button, ⌘⌃F, Esc and a
        video-focus exit alike. Guarded: changeEvent can fire before the menu is built."""
        action = getattr(self, "_fullscreen_action", None)
        if action is not None:
            action.setText("Exit Full Screen" if self.isFullScreen() else "Enter Full Screen")

    def _on_video_focus_changed(self, focused: bool):
        """The current view toggled VIDEO FOCUS (⤢ / double-click the video): it has already
        maximized the video panel; put the WINDOW into fullscreen (True) / normal (False) so the
        maximized video fills the whole screen with no chrome. Only ever touches the window's own
        fullscreen state — the panel maximize/restore lives entirely in the view."""
        if focused:
            if not self.isFullScreen():
                self.showFullScreen()
        else:
            if self.isFullScreen():
                self.showNormal()

    def changeEvent(self, event):
        """Track window-state changes so the View menu's Enter/Exit text (and, when the user leaves
        fullscreen by any means while video focus is on, the view's focus state) stay consistent —
        whichever path toggled fullscreen (menu, ⌘⌃F, the green button, Esc, or the ⤢ gesture)."""
        if event.type() == QEvent.WindowStateChange:
            self._sync_fullscreen_action_text()
            # If the user left fullscreen by a native means (green button / window manager) while
            # video focus was on, the video is still panel-maximized — restore it so the two never
            # drift apart. set_video_focus(False) is a no-op when focus wasn't active.
            view = getattr(self, "view", None)
            if (view is not None and not self.isFullScreen()
                    and getattr(view, "is_video_focused", lambda: False)()):
                view.set_video_focus(False)
        super().changeEvent(event)

    def _escape_out(self) -> bool:
        """Back out of ONE "something fills the frame" state, innermost first; True if Esc was used.

        Three states can be entered independently and Esc has to leave whichever is on:

        * VIDEO FOCUS (⤢ / double-click the video) owns BOTH a maximized video panel and a
          fullscreen window, so it must be tested first — either of the branches below would
          otherwise undo half of it and leave the pair adrift.
        * A MAXIMIZED PANEL (⛶ / double-click a header) collapses the grid and never touches the
          window state. Gating the whole Esc branch on isFullScreen() is why this state was inert
          while four surfaces — the Shortcuts card and all four ⛶ tooltips — promised Esc restores
          it (QA L1-02). The window is the only viable owner: CentralView is Qt.NoFocus, so a
          keyPressEvent there would never receive the key.
        * WINDOW FULLSCREEN (⌘⌃F / the green button) is the outermost, so it goes last."""
        view = getattr(self, "view", None)
        if view is not None and getattr(view, "is_video_focused", lambda: False)():
            view.set_video_focus(False)     # also leaves fullscreen
            return True
        if view is not None and getattr(view, "_maximized_panel", None) is not None:
            view._restore_splitter_sizes()  # the inverse of the ⛶ collapse; the view owns the snapshot
            return True
        if self.isFullScreen():
            self.showNormal()
            return True
        return False

    def keyPressEvent(self, event):
        """←/→ step the video ±1 s (Shift ±5 s). Esc backs out of every "one thing fills the frame"
        state — video focus, a maximized panel, window fullscreen. Handled here, not as a QShortcut,
        so the lap table keeps arrow nav; keyPressEvent only fires when the focus widget didn't use
        the key."""
        if event.key() == Qt.Key_Escape and self._escape_out():
            event.accept()
            return
        if event.key() in (Qt.Key_Left, Qt.Key_Right):
            step = 5.0 if event.modifiers() & Qt.ShiftModifier else 1.0
            sign = 1.0 if event.key() == Qt.Key_Right else -1.0
            self._video_do(lambda v: v.step(sign * step))
            event.accept()
            return
        super().keyPressEvent(event)

    def _open_file(self):
        """File ▸ Open…: pick a GoPro MP4 and reload through the guarded _load path.

        Starts the dialog in the persisted last-opened folder (a track-day user's footage lives in one
        place), falling back to the current recording's folder and then nowhere. On a successful open
        the picked file's folder is remembered for next time."""
        start_dir = self._open_start_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Open recording", start_dir, "GoPro recordings (*.MP4 *.mp4)")
        if path:
            prefs.set_last_dir(os.path.dirname(path))
            self._load([path])

    def _open_start_dir(self) -> str:
        """The folder the Open / reference dialogs should start in: the persisted last-opened folder
        if it still exists, else the current recording's folder, else "" (today's fallback)."""
        remembered = prefs.last_dir()
        if remembered:
            return remembered
        return os.path.dirname(self._paths[0]) if getattr(self, "_paths", None) else ""

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
    def _update_library(self, paths: list[str]) -> dict | None:
        """Upsert the just-loaded recording into the local session-library index. Fully guarded: a
        library write must never disrupt a load. Skips the bundled DEFAULT_SAMPLE and any recording
        with no valid laps (a junk row the library would surface forever).

        Returns the "new personal best" MOMENT (a library.pb_moment dict) or None. The moment is
        decided against the index AS IT IS BEFORE THIS SESSION IS UPSERTED (so the recording being
        added can't be its own prior PB), and ONLY when the timing is VERIFIED and NOT data-quality
        degraded — a PB against an arbitrary provisional start line is meaningless, and a PB whose
        absolute timing the app itself calls ESTIMATED (media-clock / low GPS) isn't one to
        celebrate, so we never celebrate either. The caller shows the celebratory banner from the
        returned moment; a library-write failure still returns the moment (the comparison already
        succeeded)."""
        if any(os.path.abspath(p) == os.path.abspath(DEFAULT_SAMPLE) for p in paths):
            return None
        if not self.session.valid_lap_ids():
            return None
        moment = None
        try:
            entry = self.session.library_entry(paths)
            # Decide the PB moment against the PRIOR index (before the upsert), gated on BOTH timing
            # axes — a provisional/unconfirmed start line makes the lap number meaningless, and a
            # data-quality-degraded (media-clock / low-GPS ESTIMATED) time isn't one to celebrate
            # (library.pb_moment_for returns None for either).
            prior_index = library.load()
            moment = library.pb_moment_for(
                self.session.timing_verified, prior_index, entry.get("track"), entry.get("best"),
                degraded=self.session.timing_quality.degraded)
            library.upsert_and_save(entry)
        except Exception as exc:  # noqa: BLE001 — the index is additive; never break a load
            print(f"studio: session library not updated ({exc!r}).", flush=True)
        return moment

    def _show_pb_moment(self, moment: dict):
        """Show the transient "new personal best!" toast for a ``library.pb_moment`` result. Fully
        guarded — a celebration must never disrupt a load. The toast's "See your progress →" link
        opens the Library dialog's per-track PB-progression chart (the retention surface), and it
        auto-dismisses. Held on the window (self._pb_toast) so a rapid reload replaces the old one."""
        try:
            title, body = library.pb_moment_text(moment, fmt_time)
            old = getattr(self, "_pb_toast", None)
            if old is not None:
                old.dismiss()
            # Offer the one-tap share only when the card is actually shareable (verified lap) —
            # a PB moment is verified timing by construction, but stay honest via the same verdict.
            on_share = None if self._share_card_blocked() else self._share_pb_card
            toast = PBToast(title, body, on_progress=self._open_library,
                             on_share=on_share, parent=self)
            self._pb_toast = toast
            toast.show_for(self)
        except Exception as exc:  # noqa: BLE001 — a celebration must never break a load
            print(f"studio: personal-best moment not shown ({exc!r}).", flush=True)

    def _open_library(self):
        """File ▸ Library…: open the session-library dialog (a sortable list of analyzed
        recordings + per-track PB progression). Re-opening an entry routes back through the
        guarded `_load` path; the dialog reads the index defensively (empty when missing). The
        privacy controls (forget one recording / clear the library) are injected here — the dialog
        stays pacer-free + file-op-free, the app owns the index write + sidecar delete."""
        dlg = LibraryDialog(library.load(), open_recording=self._load, parent=self,
                            forget_recording=self._forget_recording,
                            clear_library=self._clear_library,
                            reveal_library=self._reveal_library,
                            backup_library=self._backup_library)
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
                # If the forgotten recording is the one CURRENTLY loaded, the live session still
                # holds this sidecar path — clear it FIRST so a passive timing nudge can't re-write
                # the file we're about to delete (an explicit re-save could re-establish it later).
                self._disable_sidecar_if_open(side)
                if os.path.exists(side):
                    os.remove(side)
                    print(f"studio: deleted timing-line sidecar {os.path.basename(side)}",
                          flush=True)
            except OSError as exc:
                print(f"studio: could not delete the sidecar ({exc!r}).", flush=True)
        return library.load()

    def _disable_sidecar_if_open(self, forgotten_side: str) -> None:
        """When the recording being forgotten IS the currently-loaded session, null the live sidecar
        path on both the window and the central view so a subsequent passive timing nudge can't
        RE-CREATE the just-deleted ``.pacer.json`` (``CentralView._save_sidecar`` no-ops on an empty
        path). The window's ``_sidecar_path`` is also cleared so any rebuilt view stays de-linked.
        Matched by resolved sidecar path (chapter-invariant to how the sidecar was written); a
        no-match (forgetting a DIFFERENT recording) leaves the open session untouched."""
        live = getattr(self, "_sidecar_path", None)
        if not live or os.path.abspath(live) != os.path.abspath(forgotten_side):
            return
        self._sidecar_path = None
        view = getattr(self, "view", None)
        if view is not None:
            view._sidecar_path = None
        print("studio: cleared the open recording's sidecar link after forgetting it", flush=True)

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

    def _reveal_library(self) -> None:
        """Data portability: open the app-support FOLDER that holds ``library.json`` in Finder, so
        the durable index is findable/copyable. Reveals the DIRECTORY (created lazily on the first
        save; ``os.makedirs`` here so a never-saved library still opens to an existing folder rather
        than a Finder error). Guarded — a failed open just logs."""
        directory = os.path.dirname(library.library_path())
        try:
            os.makedirs(directory, exist_ok=True)
        except OSError as exc:
            print(f"studio: could not open the library folder ({exc!r}).", flush=True)
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(directory))

    def _backup_library(self) -> None:
        """Data portability: copy ``library.json`` to a user-chosen path (``QFileDialog`` →
        ``shutil.copy2``, preserving mtime). No-op with a gentle notice when there's no library yet
        (nothing analyzed) or the user cancels the dialog. Guarded — a failed copy just informs the
        user via the status bar (a backup failure must never disrupt the app)."""
        src = library.library_path()
        if not os.path.exists(src):
            self.statusBar().showMessage("no library to back up yet — analyze a recording first", STATUS_MS)
            return
        dest, _ = QFileDialog.getSaveFileName(
            self, "Back up library", os.path.join(os.path.expanduser("~"), "library.json"),
            "Library index (*.json)")
        if not dest:
            return  # user cancelled
        try:
            shutil.copy2(src, dest)
        except OSError as exc:
            print(f"studio: could not back up the library ({exc!r}).", flush=True)
            self.statusBar().showMessage(f"could not back up the library: {exc}", STATUS_MS)
            return
        self.statusBar().showMessage(f"library backed up to {dest}", STATUS_MS)

    # Open Recent: recently analyzed recordings (most-recent-first), each re-opened via the guarded
    # `_load`. Sourced from the session-library index rather than a separate MRU list.
    _RECENT_LIMIT = 8

    def _recent_entries(self) -> list[dict]:
        """Open Recent candidates: openable library entries (valid laps, file present),
        most-recent-first by date, capped at _RECENT_LIMIT. Guarded: any failure yields [].
        An UNKNOWN-TRACK recording is a candidate like any other (it re-opens fine, and
        `_recent_label` already names it "unknown track") — matching library_dialog._entry_junk."""
        try:
            entries = library.load().get("entries", [])
        except Exception as exc:  # noqa: BLE001 — the recents list is additive; never break the menu
            print(f"studio: Open Recent unavailable ({exc!r}).", flush=True)
            return []
        usable = [
            e for e in entries
            if e.get("lap_count")
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
        # The Corners tab shows the per-corner rows for the jump target. This is NAVIGATION, not a
        # preference: without the guard the jump silently overwrote (and persisted) whichever tab
        # the user had chosen, so quitting from a jump reopened the app on Corners (QA L5-07).
        self._jumping = True
        try:
            view.select_lap_tab(1)
        finally:
            self._jumping = False
        view.map.highlight_corner(cid)
        self._reveal_jump_corner(cid)
        target = self.session.corners.corner_entry_media_time(best, cid)
        if target is not None:
            view.video.seek(target)
            # Seed auto-follow to the seek's lap so the post-seek tick isn't a lap-change edge.
            view._playback.followed_lap = self.session.lap_at_time(target)

    def _reveal_jump_corner(self, cid: int):
        """Point at the row the jump landed on: scroll corner `cid` to the middle of the Corners
        grid, make it the current cell, and say on the status bar which corner and which lap the
        table is now showing.

        The Corners grid is deliberately NoSelection (track order is the meaning), so before this
        a Jump arrived on a 12-row table with nothing indicating WHICH row you had clicked — and,
        because the jump selects the session best, on a Δ column that is all dashes against itself.
        Naming the destination is what makes those dashes read as an answer instead of a refutation
        (QA L5-07). Fully guarded: a navigation nicety must never break the jump."""
        view = getattr(self, "view", None)
        table = getattr(getattr(view, "corner_table", None), "table", None)
        if table is None:
            return
        try:
            row = view.corner_table._cids.index(cid)
            item = table.item(row, 0)
            if item is None:
                return
            table.setCurrentCell(row, 0)
            table.scrollToItem(item, QAbstractItemView.PositionAtCenter)
            label = item.text().split()[0]      # "C9 ⟳" -> "C9" (drop the direction glyph)
            self.statusBar().showMessage(
                f"jumped to {label} — Corners is showing lap "
                f"{lap_label(self.session.best_lap_id())}, this session's best", STATUS_MS)
        except (ValueError, AttributeError, IndexError):
            return  # the corner set moved under us (a re-segment); the map ring already landed

    def _on_lap_tab_changed(self, index: int):
        """The lap panel's tab changed (a tab click, a digit shortcut, or ⌘⇧S): remember +
        persist (guarded) so the panel reopens on the same page after a reload/relaunch.

        A jump-to NAVIGATES the panel for the user (Coaching ▸ Opportunities ▸ Go); that is not the
        user choosing a page, so it must not overwrite the persisted preference (QA L5-07)."""
        if getattr(self, "_jumping", False):
            return
        self._lap_panel_tab = int(index)
        try:
            prefs.set_lap_panel_tab(self._lap_panel_tab)
        except OSError as exc:
            print(f"studio: could not persist the lap-panel tab ({exc!r}).", flush=True)

    def _on_grid_sizes_changed(self, sizes: list):
        """A grid splitter was dragged (debounced in the view): remember + persist (guarded)
        the [main, left, right] sizes so the user's layout survives reloads/relaunches."""
        self._grid_sizes = sizes
        try:
            prefs.set_grid_sizes(sizes)
        except OSError as exc:
            print(f"studio: could not persist the grid layout ({exc!r}).", flush=True)

    def _on_excluded_toggled(self, on: bool):
        """View ▸ Show excluded laps: remember + persist (guarded) the choice, and delegate the
        strip's show/hide to the view. No-op before the first load."""
        self._excluded_visible = bool(on)
        try:
            prefs.set_excluded_visible(self._excluded_visible)
        except OSError as exc:
            print(f"studio: could not persist excluded-strip visibility ({exc!r}).", flush=True)
        view = getattr(self, "view", None)
        if view is not None:
            view.set_excluded_visible(self._excluded_visible)

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

    def _on_colorblind_toggled(self, on: bool):
        """View ▸ Colour-blind-safe cues: flip the global semantic palette (theme.set_palette),
        persist the choice (guarded — a write failure must never disrupt the app), and re-render the
        open view's delta / lap table / rainbow map in the new palette. No-op before the first load
        keeps the global palette in sync so the next-built view adopts it."""
        self._colorblind = bool(on)
        theme.set_palette(theme.PALETTE_COLORBLIND if on else theme.PALETTE_STANDARD)
        try:
            prefs.set_colorblind_palette(self._colorblind)
        except OSError as exc:
            print(f"studio: could not persist colour-blind palette ({exc!r}).", flush=True)
        view = getattr(self, "view", None)
        if view is not None:
            view.refresh_palette()

    # ----------------------------------------------------------- data export (F11)
    # File ▸ Export Qt side (the writers are Qt-free in export_data.py).
    def _sync_export_menu(self):
        """Grey the Export submenu + the video-export action out until a session is loaded.
        Connected to the File menu's aboutToShow (synced as the menu opens), so neither _load nor
        the failed-load path needs to reach into the menu."""
        has = hasattr(self, "session")
        self._export_menu.setEnabled(has)
        self._export_video_action.setEnabled(has)
        # The shareable lap card is enabled only when there's a VERIFIED lap to brag about (an
        # unverified/provisional start line makes the lap time meaningless — never a bragging
        # card). card_data owns the verdict (blocked); we mirror it onto both card actions.
        card_ok = has and not self._share_card_blocked()
        self._share_card_action.setEnabled(card_ok)
        self._copy_card_action.setEnabled(card_ok)
        # Save-as-track needs USABLE timing lines (≥1 valid lap means the start line actually
        # segments this trace — the lines are worth promoting to a reusable track).
        self._save_track_action.setEnabled(self._can_save_track())

    def _share_card_blocked(self) -> bool:
        """True when a shareable lap card must NOT be offered (no valid/verified lap). Reads the
        pure card_data verdict; any failure means 'not shareable' (grey it out, never crash)."""
        if not hasattr(self, "session"):
            return True
        try:
            return share_card.card_data(self.session, unit=self._speed_unit).blocked
        except Exception:  # noqa: BLE001 — a menu sync must never raise
            return True

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
            self.statusBar().showMessage("no usable timing lines to save as a track", STATUS_MS)
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
            self.statusBar().showMessage(f"could not save track: {exc}", STATUS_MS)
            return
        # The freshly-saved track now wins detection for THIS session's name on the next load —
        # and it makes the timing VERIFIED (a named track is a trusted start line), so refresh the
        # derived views to drop the provisional banner / muting and restore the purple session-bests.
        self.session.track_name = name
        if getattr(self, "view", None) is not None:
            self.view.refresh_timing_trust()
        self.statusBar().showMessage(f"saved track '{name}' — future recordings here auto-detect it", STATUS_MS)
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
            self.statusBar().showMessage(f"exported {os.path.basename(path)}", STATUS_MS)

    def _export_channels_csv(self):
        if not hasattr(self, "session"):
            return
        lap = self._export_lap_id()
        if lap is None:
            self.statusBar().showMessage("no valid lap to export channels for", STATUS_MS)
            return
        path = self._export_save_path(f"Export lap {lap_label(lap)} channels",
                                      f"_lap{lap_label(lap)}_channels.csv", "CSV files (*.csv)")
        if not path:
            return
        if self._run_export(lambda: export_data.write_channels_csv(path, self.session, lap), path):
            self.statusBar().showMessage(f"exported {os.path.basename(path)}", STATUS_MS)

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
            self.statusBar().showMessage(f"exported {os.path.basename(path)}", STATUS_MS)

    def _run_export(self, write, path: str) -> bool:
        """Run a writer (`write()`) under an OSError guard; on failure show a warning dialog +
        statusbar note. Returns True on success."""
        try:
            write()
        except OSError as exc:
            QMessageBox.warning(self, "Export failed",
                                f"Could not write {os.path.basename(path)}:\n{exc}")
            self.statusBar().showMessage(f"export failed: {exc}", STATUS_MS)
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

    # ------------------------------------------------ shareable lap card (image)
    # File ▸ Export ▸ "Lap card (image)…" / "Copy lap card" + the PB-toast one-tap share. The
    # numbers come from share_card.card_data (pure Session accessors); the speed-map thumbnail is
    # the SAME live-MapView→PNG grab the HTML report uses (no reinvented rendering). Honesty lives
    # in card_data (blocked ⇒ never built; stamped ⇒ "estimated timing" burned on).
    def _build_share_card(self):
        """Render the shareable lap card to a QImage from the current session, or None when the
        session is blocked (provisional / no valid lap) or a session/view is missing. The map
        thumbnail is grabbed from the live MapView (best-effort — a grab failure just drops the
        thumbnail, the card still renders). Palette + unit follow the app's active choices."""
        if not hasattr(self, "session") or getattr(self, "view", None) is None:
            return None
        data = share_card.card_data(self.session, unit=self._speed_unit)
        if data.blocked:
            return None
        try:
            map_png = self._grab_clean_map_png(self.view.map)
        except Exception as exc:  # noqa: BLE001 — the thumbnail is optional; never fail the card
            print(f"studio: lap-card map thumbnail not grabbed ({exc!r}).", flush=True)
            map_png = None
        return share_card.render_card(data, map_png, palette=theme.active_palette())

    def _grab_clean_map_png(self, map_view) -> bytes:
        """Grab the MapView to PNG for the SHARE card with its dev "Map key" legend (and any other
        pure-interaction chrome) suppressed — that overlay belongs on the live app, never on a
        social share image. Uses the MapView's ``grab_clean`` context to hide + restore the chrome
        around the same widget→PNG path the report uses; falls back to the plain grab for a bare
        widget (tests) that has no such context. The speed colouring is untouched."""
        grab_clean = getattr(map_view, "grab_clean", None)
        if grab_clean is None:
            return self._grab_png(map_view)
        with grab_clean():
            return self._grab_png(map_view)

    def _export_share_card(self):
        """File ▸ Export ▸ "Lap card (image)…": render the card and save it as a PNG."""
        image = self._build_share_card()
        if image is None:
            self.statusBar().showMessage("no verified lap to make a shareable card for", STATUS_MS)
            return
        path = self._export_save_path("Export lap card", "_lap_card.png", "PNG images (*.png)")
        if not path:
            return
        if self._run_export(lambda: self._save_card_png(image, path), path):
            self.statusBar().showMessage(f"saved {os.path.basename(path)}", STATUS_MS)

    @staticmethod
    def _save_card_png(image, path: str) -> None:
        """Write the card QImage to `path` as PNG; raise OSError on failure so _run_export shows
        the standard warning (QImage.save returns False rather than raising)."""
        if not image.save(path, "PNG"):
            raise OSError(f"could not write {path}")

    def _copy_share_card(self):
        """File ▸ Export ▸ "Copy lap card": render the card and put it on the clipboard, so the
        user can paste it straight into a chat. clipboard() is injected-testable (monkeypatched in
        the offscreen test); guarded so a clipboard hiccup never crashes the app."""
        image = self._build_share_card()
        if image is None:
            self.statusBar().showMessage("no verified lap to make a shareable card for", STATUS_MS)
            return
        try:
            QApplication.clipboard().setImage(image)
        except Exception as exc:  # noqa: BLE001 — a clipboard failure must not disrupt the app
            print(f"studio: lap card not copied ({exc!r}).", flush=True)
            self.statusBar().showMessage("could not copy the lap card", STATUS_MS)
            return
        self.statusBar().showMessage("lap card copied — paste it into a chat", STATUS_MS)

    def _share_pb_card(self):
        """The PB toast's one-tap share: save the shareable lap card at the personal-best moment
        (the peak-pride surface). Routes through the same save path as the menu action."""
        self._export_share_card()

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
        dlg.setWindowTitle(f"Export overlay video — lap {lap_label(lap)}")
        dlg.setMinimumWidth(400)

        root = QVBoxLayout(dlg)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QLabel(f"Export overlay video — lap {lap_label(lap)}")
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
        lap_line = QLabel(f"Lap {lap_label(lap)}  ·  {fmt_time(dur)}")
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
        # Burn the current display unit + semantic palette into the overlay so the export matches the
        # on-screen readout (incl. the colour-blind Δ hue axis — the exported clip is the shared
        # artifact, so it must follow the user's colour-blind choice, not stay red/green).
        return export_video.OverlayConfig(out_height=out_height, quality=quality,
                                          speed_unit=self._speed_unit,
                                          palette=theme.active_palette())

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
            self.statusBar().showMessage("no usable lap to export video for", STATUS_MS)
            return
        # Pick resolution + quality FIRST (so a cancel here writes nothing), then the save path.
        config = self._ask_export_options(lap)
        if config is None:
            return
        out = self._export_save_path(f"Export overlay video — lap {lap_label(lap)}",
                                     f"_lap{lap_label(lap)}_overlay.mp4", "MP4 video (*.mp4)")
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
        dlg = QProgressDialog(f"Preparing lap {lap_label(lap)} overlay video…", "Cancel", 0, 0, self)
        dlg.setWindowTitle("Export overlay video")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)
        dlg.setAutoClose(False)
        dlg.setAutoReset(False)
        dlg.setValue(0)  # with max=0 too, Qt renders an indeterminate "busy" bar

        worker = VideoExportWorker(self.session, spec)
        self._video_worker = worker  # keep a ref so the thread isn't GC'd mid-render
        started = {"first": False}

        def on_progress(done: int, total: int):
            if total > 0:
                if not started["first"]:
                    # First real frame: switch from the busy "Preparing…" bar to a determinate one.
                    started["first"] = True
                    dlg.setLabelText(f"Rendering lap {lap_label(lap)} overlay video…")
                dlg.setMaximum(total)
                dlg.setValue(done)

        def on_done(ok: bool, message: str):
            dlg.reset()
            worker.wait()
            self._video_worker = None
            spec.source.cleanup()  # free any temp concat-list file the chapter resolution wrote
            if ok:
                self.statusBar().showMessage(f"exported {os.path.basename(spec.out_path)}", STATUS_MS)
            elif message == "cancelled":
                self.statusBar().showMessage("video export cancelled", STATUS_MS)
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
        becomes the Δ / map / table reference. The picked file's chapters are chained, then loaded OFF
        the UI thread (same ~1.4–4 s Session.load as the primary open — running it synchronously here
        froze the window on the moat cross-recording-compare path). On completion the loaded Session is
        adopted via set_reference_session + _apply_reference_change on the UI thread; on a guard refusal
        or a load failure the local best lap is kept and the reason surfaces (a status line + notice),
        never a freeze."""
        if not hasattr(self, "session"):
            return
        start_dir = self._open_start_dir()
        path, _ = QFileDialog.getOpenFileName(
            self, "Load reference recording", start_dir, "GoPro recordings (*.MP4 *.mp4)")
        if not path:
            return
        prefs.set_last_dir(os.path.dirname(path))
        paths = chapters.discover_siblings(path)
        self._start_reference_load(paths)

    def _start_reference_load(self, paths: list[str]):
        """Spawn the off-thread reference Session.load for `paths` (the file-picker-free half of
        _load_reference_file, so tests drive it without a dialog). Reuses the primary open's
        SessionLoadWorker; a lightweight status-bar "Loading reference…" replaces the whole-view
        placeholder (the primary session stays shown). GUARD: a reference load must not run alongside a
        primary load or a second reference load — bump the reference token (so a still-running older
        reference worker's result is ignored) and, if one is already in flight, supersede it rather than
        launch a concurrent second load."""
        print(f"studio: loading reference recording — {len(paths)} chapter(s)…", flush=True)
        self.statusBar().showMessage("Loading reference…", STATUS_MS)
        # Bump the token: any in-flight reference worker started earlier is now stale; its result is
        # ignored when it finishes (see _on_reference_loaded / _on_reference_load_failed).
        self._ref_load_token += 1
        token = self._ref_load_token
        worker = SessionLoadWorker(token, paths)
        self._ref_load_worker = worker
        self._load_workers.add(worker)  # hold it so the QThread isn't GC'd mid-load (shared drain set)
        worker.loaded.connect(self._on_reference_loaded)
        worker.failed.connect(self._on_reference_load_failed)
        worker.finished.connect(lambda w=worker: self._on_reference_worker_finished(w))
        worker.start()

    def _on_reference_worker_finished(self, worker):
        """A reference load worker's QThread finished: drop it from the shared in-flight set."""
        self._load_workers.discard(worker)
        if self._ref_load_worker is worker:
            self._ref_load_worker = None

    def _on_reference_loaded(self, token: int, paths: list[str], ref):
        """Reference load succeeded (UI thread, queued signal): adopt the loaded Session as the
        reference (the guard + apply half — set_reference_session — that load_reference already splits
        out) and refresh the derived views. Ignores a STALE result (a newer reference load superseded
        this one). A guard refusal keeps the local best lap and surfaces the reason."""
        if token != self._ref_load_token:
            return  # superseded by a newer reference load; drop this result
        if not hasattr(self, "session"):
            return  # the primary session went away while the reference loaded — nothing to attach to
        reason = self.session.set_reference_session(
            ref, source_label=chapters.recording_label(paths))
        if reason is not None:
            print(f"studio: reference not loaded — {reason}", flush=True)
            self.statusBar().clearMessage()
            QMessageBox.information(self, f"{APP_NAME} — reference not loaded", reason)
            return
        self.statusBar().clearMessage()
        self._apply_reference_change()

    def _on_reference_load_failed(self, token: int, paths: list[str], exc: Exception):
        """Reference load failed (UI thread, queued signal): drop a STALE result, else surface the
        reason without a freeze and keep the local best lap (the feature is additive). Mirrors
        load_reference's own could-not-load message, just off-thread."""
        if token != self._ref_load_token:
            return  # superseded by a newer reference load; drop this result
        reason = f"could not load the reference recording ({type(exc).__name__}: {exc})"
        print(f"studio: reference not loaded — {reason}", flush=True)
        self.statusBar().clearMessage()
        QMessageBox.information(self, f"{APP_NAME} — reference not loaded", reason)

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
                self, f"{APP_NAME} — no reference recording",
                "Load a reference recording first (File ▸ Load reference recording…), then "
                "compare against it.")
            return
        # The compare controller lives on the live central view.
        if not self.view.compare.enter_cross():
            QMessageBox.information(
                self, f"{APP_NAME} — cross-recording compare unavailable",
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
            label = self.session.reference_label()
            # A geometry-matched reference (unknown track name, matched by GPS location) is a valid
            # overlay but UNVERIFIED: both start lines may be provisional, so the aligned Δ phase can
            # be off. Flag it with the shared PROVISIONAL trust-tier colour (no hardcoded colour) and
            # a short caveat tooltip; a confirmed same-named match is unchanged (no caveat, no tint).
            if self.session.reference_match_is_geometric():
                chip.setText(f"  ▶ reference: {label} · matched by location — unverified  ")
                chip.setStyleSheet(f"color: {theme.PROVISIONAL_COLOR};")
                chip.setToolTip(
                    "This reference was matched to your session by GPS location, not a confirmed "
                    "track name. The overlay is valid, but set BOTH recordings' start/finish lines "
                    "for exact Δ alignment.")
            else:
                chip.setText(f"  ▶ reference: {label}  ")
                chip.setStyleSheet("")   # clear any prior caveat tint (chip is reused across loads)
                chip.setToolTip("")
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
    app = QApplication([sys.argv[0], *argv])  # keep argv[0] as the program name
    # Brand the running app: the Dock/window icon and the app name Qt reports. NOTE the macOS
    # menu-bar showing "Python" in a NON-FROZEN dev run is expected — AppKit reads the menu-bar
    # app name from the running bundle's Info.plist (here the python interpreter's) before Qt can
    # override it. The shipped .app sets CFBundleName="Pacer Studio" (packaging/pacer.spec) so the
    # product is correct; we do NOT pull in pyobjc/Foundation just to fix a cosmetic dev-only label.
    app.setApplicationName("Pacer Studio")
    app.setApplicationDisplayName("Pacer Studio")
    app.setOrganizationName("pacer")  # additive; the library.json path is built from a hard-coded _APP_DIR_NAME, so this cannot move it
    _icon = Path(__file__).resolve().parent / "assets" / "pacer.icns"
    if _icon.exists():
        app.setWindowIcon(QIcon(str(_icon)))  # dev Dock + window proxy icon; frozen Dock icon comes from the bundle
    # Install the top-level exception handler now the QApplication exists: an unhandled exception in
    # any signal handler surfaces a themed Report-a-problem dialog (and a stderr trace) instead of
    # silently killing the app. Must be AFTER QApplication() so the handler can show a dialog.
    install_excepthook()
    # Apply the dark "Refined Minimal" design system BEFORE constructing any widgets, so the
    # default font/palette and the pyqtgraph background are in place when the panels are built.
    theme.register_fonts()
    theme.apply_theme(app)
    window = StudioWindow(paths, full=full, demo_unavailable=demo_startup)
    window.show()
    return app.exec()
