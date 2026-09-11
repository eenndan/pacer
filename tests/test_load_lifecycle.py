"""Load / reference LIFECYCLE tests — the five riskiest untested user-facing paths, the
success-path strand, and the reference-load guards that only existed in a docstring.

Every test here drives a REAL StudioWindow through its PRODUCTION slots. That is the whole point:
the paths below are the ones where "the code obviously does X" and the code did not, and a mirror
test (one that re-implements the body next to the body) would have agreed with the bug.

WHAT EACH PINS, AND HOW IT USED TO FAIL

  * the load-SUCCESS strand (§3.4) — `_on_session_loaded` commits `self.session`, then calls
    `_build_ui()` inside try/**finally** with no except. A raising `CentralView.__init__` left the
    session committed, `self.view` None, the control-less "Loading telemetry…" card up forever and
    `loadFinished` never emitted; and because the slot runs on a QUEUED connection, PySide prints
    the traceback and returns, so nothing else in the app learns the load ended. This is the exact
    shape of the #164 brick, on the one door that was never shut. The FAILURE path was hardened
    against it explicitly — its twin was not. Same for the two other unguarded `_build_ui()` calls
    (`_cancel_load`, `_on_load_failed`), where the rebuild IS the recovery.

  * the reference-load guards (§3.5) — `_start_reference_load`'s docstring promised single-flight
    and supersede; the body bumped a token and started a worker unconditionally, so N rapid picks
    ran N concurrent full `Session.load`s. And `_apply_reference_change` dereferenced `self.view`
    unguarded, so a reference landing during a primary RELOAD (view already disposed by the loading
    card) raised inside a queued slot. Zero tests referenced `_load_reference_file` /
    `_on_reference_load_failed` / `_clear_reference` / `_enter_cross_compare`.

  * closeEvent mid-load and mid-EXPORT (§3.7.1) — `_video_worker` was held on an attribute and
    NOWHERE else, so `closeEvent`'s drain (which exists precisely so no QThread is destroyed
    mid-run) walked past the one worker that can still be running minutes later.

  * `_load_full_recording` (§3.7.2) — zero references anywhere; it is a reload-while-loaded, the
    #164 shape.

  * `_forget_recording` (§3.7.3) — the existing tests in test_data_safety.py re-implement the body
    beside it. This one EXECUTES the real method and asserts the ORDERING that makes it safe
    (remove → save → _disable_sidecar_if_open → os.remove) plus the invariant that the media file
    is never touched.

  * `_on_worker_finished`'s queued restart (§3.7.4) — "open B while A is loading" silently dropping
    B would ship green.

  * `stats_panel._on_ideal_row_selected` (§3.7.5) — the newest interactive surface; its content was
    exhaustively tested and its row selection never once driven.

  * the loading card's determinism (§3.8) — see
    `test_the_loading_card_is_deterministic_while_the_event_loop_runs` for the probe verdict.

Real Qt + real worker QThreads, so run offscreen:
    QT_QPA_PLATFORM=offscreen PACER_NO_MEDIA=1 python tests/test_load_lifecycle.py
"""
import faulthandler
import os
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The inert media triplet (no decoder/audio device) — set BEFORE importing the studio widgets.
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import Signal  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox, QWidget  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import library, prefs  # noqa: E402

# ------------------------------------------------------------------ user-data quarantine
# NOTHING in this file may touch the real ~/Library/Application Support/pacer. Both stores are
# diverted for the whole module BEFORE any window is built: `_on_session_loaded` upserts into the
# library on every successful load, and a window's __init__ reads prefs. The temp dir is held open
# for the process lifetime on purpose — a window built in one test can still write during another's
# teardown.
_SANDBOX = tempfile.TemporaryDirectory(prefix="pacer-lifecycle-")
library._app_support_dir = lambda: _SANDBOX.name
prefs._app_support_dir = lambda: _SANDBOX.name

import test_central_view_realqt as _realqt  # noqa: E402

from studio import app as app_mod
from studio import export_controller  # noqa: E402
from studio import sidecar as sidecar_mod  # noqa: E402
from studio import workers as workers_mod  # noqa: E402
from studio.overlays import WelcomeView  # noqa: E402

# Every dialog raised, as (title, text). BOTH shapes have to be intercepted or the suite blocks
# forever on a real modal: the load/build failures build a QMessageBox INSTANCE and call exec() on
# it (so the instance method is the seam), while the reference paths call the STATIC helpers, which
# construct and exec entirely in C++ where a patched instance method is never reached.
#
# The instance dialogs record no title on purpose: macOS DROPS a QMessageBox's window title, so
# windowTitle() is '' for all of them — which is exactly why those dialogs carry the product name in
# their BODY (see _on_load_failed), and why the body is what the assertions read.
DIALOGS = []


def _capture_exec(self, *_a, **_k):
    DIALOGS.append((self.windowTitle(), self.text()))
    return QMessageBox.StandardButton.Ok


QMessageBox.exec = _capture_exec
QMessageBox.exec_ = _capture_exec
for _name in ("information", "warning", "critical", "question"):
    setattr(QMessageBox, _name,
            staticmethod(lambda _parent, title, text, *_a, **_k:
                         (DIALOGS.append((title, text)), QMessageBox.StandardButton.Ok)[1]))


# ------------------------------------------------------------------ fixtures
def _window(build_menu: bool = False):
    """A REAL StudioWindow over the synthetic 2-lap session (real `_build_ui`, real CentralView,
    real tick timer) plus the async-load bookkeeping the real `__init__` installs — the shared
    fixture builds the window through `__new__`, so those attributes have to be seeded here.

    `build_menu` adds the real menu bar, for the tests that assert on action enablement."""
    win, view = _realqt._studiowindow_with_view(build_menu=build_menu)
    win._load_token = 0
    win._load_worker = None
    win._load_workers = set()
    win._pending_load = None
    win._loading_token = None
    win._placeholder_timer = None
    win._demo_worker = None
    win._drop_notice = None
    win._timing_restore_failed = False
    win._timing_restore_unreadable = False
    win._tracks_unreadable = False
    win._notice = None
    win._ref_load_token = 0
    win._ref_load_worker = None
    win._pending_reference_load = None
    win._loading_card = None
    win._loading_headline = None
    return win, view


def _stub_session(**extra):
    """A plain object satisfying everything `_on_session_loaded` reads off a freshly loaded Session
    (nothing here is under test — the LIFECYCLE is)."""
    s = SimpleNamespace(
        chapters=None,
        point_count=lambda: 1234,
        lap_count=lambda: 2,
        restore_saved_timing_lines=lambda _p: None,
        adopt_timing_history=lambda _prev: None,
        valid_lap_ids=lambda: set(),        # keeps it out of the library (a 0-lap recording)
    )
    for k, v in extra.items():
        setattr(s, k, v)
    return s


class _RaisingView:
    """A CentralView stand-in whose construction fails — the "partially-valid session / a panel
    tripping on a degenerate channel" case, which is the only way this ever happens in the wild and
    the only way to drive it deterministically here."""

    def __init__(self, *_a, **_k):
        raise ValueError("central-view-blew-up")


class _StubMarksPanel(QWidget):
    """The Marks page's stand-in: the five intents `_build_ui` connects, and nothing else. The page
    itself owns no store (the window does), so a stub really is just its signals."""

    markActivated = Signal(str)
    addRequested = Signal()
    editRequested = Signal(str)
    deleteRequested = Signal(str)
    extendRequested = Signal(str)


class _StubView(QWidget):
    """A CentralView stand-in that BUILDS: a real QWidget (so `setCentralWidget` is the production
    one) carrying the four signals `_build_ui` connects — and the Marks page whose five it connects
    too. `set_marks` is deliberately ABSENT: `_refresh_marks` gates on `hasattr(view, "set_marks")`,
    so leaving it off keeps this stub out of the marks surface entirely while still proving the
    wiring does not raise."""

    timingEdited = Signal()
    lapTabChanged = Signal(int)
    gridSizesChanged = Signal(object)
    videoFocusChanged = Signal(bool)

    def __init__(self, session, paths, sidecar_path, parent=None, **_kw):
        super().__init__()
        self.session = session
        self._sidecar_path = sidecar_path
        self.paths = list(paths)
        self.disposed = False
        self.marks_panel = _StubMarksPanel(self)

    def dispose(self):
        self.disposed = True

    def tick(self):
        """The window's ~30 Hz timer drives whatever view is current — including this one."""


def _pump(deadline_s, until):
    end = time.time() + deadline_s
    while time.time() < end and not until():
        _APP.processEvents()
        time.sleep(0.004)
    return until()


class _StubLoad:
    """Context manager swapping `workers.Session` for a stub whose `load` takes `delay_s` and then
    returns `result(paths)` (or raises it). Records every call's paths, and the peak number of
    concurrent loads — which is what makes "single-flight" a falsifiable claim rather than a
    docstring.

    `delay_s` may be a callable of `paths`, for the tests that need one load to overtake another."""

    def __init__(self, result, delay_s=0.0):
        self._result, self._delay = result, delay_s
        self.calls, self.peak, self._live = [], 0, 0

    def __enter__(self):
        self._orig = workers_mod.Session
        outer = self

        class _Stub:
            @staticmethod
            def load(paths, *_a, **_k):
                outer.calls.append(list(paths))
                outer._live += 1
                outer.peak = max(outer.peak, outer._live)
                try:
                    delay = outer._delay
                    time.sleep(delay(list(paths)) if callable(delay) else delay)
                    out = outer._result(list(paths))
                finally:
                    outer._live -= 1
                if isinstance(out, BaseException):
                    raise out
                return out
        workers_mod.Session = _Stub
        return self

    def __exit__(self, *_exc):
        workers_mod.Session = self._orig
        return False


class _SwapView:
    """Context manager swapping `app.CentralView` for `cls`."""

    def __init__(self, cls):
        self._cls = cls

    def __enter__(self):
        self._orig = app_mod.CentralView
        app_mod.CentralView = self._cls
        return self

    def __exit__(self, *_exc):
        app_mod.CentralView = self._orig
        return False


def _teardown(win):
    win._drain_load_workers()
    win.close()
    _APP.processEvents()
    win.deleteLater()
    _APP.processEvents()


# ==================================================== §3.4 · the load-SUCCESS path can strand
def test_a_raising_view_build_on_a_first_load_lands_on_the_welcome_state():
    """FIRST load, view build raises: the window must end on the welcome empty state with an honest
    message, must NOT keep a session no surface can render, and must still emit `loadFinished`.

    Before the guard this left the loading card up forever: `_on_session_loaded` had committed the
    session, `_build_ui` raised past the try/finally, and the queued-slot machinery swallowed it."""
    win, _view = _window()
    # Start from "nothing loaded": drop the fixture's session + view, exactly as a cold launch.
    win._dispose_view()
    del win.session
    win._show_welcome()
    DIALOGS.clear()
    finished = []
    win.loadFinished.connect(lambda: finished.append(True))

    with _StubLoad(lambda paths: _stub_session()), _SwapView(_RaisingView):
        win._load(["/tmp/pacer-lifecycle-first.MP4"])
        assert _pump(30.0, lambda: bool(finished)), "the load never reported finished"

    try:
        assert isinstance(win.centralWidget(), WelcomeView), type(win.centralWidget()).__name__
        assert win.view is None, "a failed build must not leave a half-view behind"
        assert not hasattr(win, "session"), \
            "a session no surface can render must not stay committed (the menus would go live on it)"
        assert win._sidecar_path is None, "the sidecar link outlived the session it belonged to"
        assert finished == [True], finished
        # The dialog blames the app, not the file: Session.load SUCCEEDED here.
        assert DIALOGS, "the strand raised no dialog at all"
        assert "couldn't build its session view" in DIALOGS[-1][1], DIALOGS[-1][1]
        assert "bug in Pacer" in DIALOGS[-1][1], DIALOGS[-1][1]
        assert "unchanged" not in DIALOGS[-1][1], "there was no previous session to keep"
    finally:
        _teardown(win)
    print("test_a_raising_view_build_on_a_first_load_lands_on_the_welcome_state OK")


def test_a_raising_view_build_on_a_reload_rolls_back_to_the_working_session():
    """RELOAD, view build raises: the working session comes BACK — and WHOLE, which is a stronger
    claim than it looks and was not free.

    `_on_session_loaded` commits far more than `self.session` before it builds the view: `_paths`,
    the sidecar path, the WINDOW TITLE, and the four flags `_session_notice` derives the untimed
    status line from. A rollback that restores only session/paths/sidecar leaves the good session
    titled after the recording that FAILED, under that recording's notices — and because the status
    line is derived state rather than a message, a false "saved timing lines couldn't be read" is
    then re-derived forever, on every timing edit, for the rest of the session.

    So the doomed load below flips EVERY one of those, and the assertions read them all back. A
    rollback, not a retry: the incoming session is the thing that could not be shown, so re-running
    `_build_ui` on it would raise again."""
    win, view = _window()
    good_session, good_paths = win.session, list(win._paths)
    win._sidecar_path = "/tmp/pacer-lifecycle-good.pacer.json"
    good_sidecar = win._sidecar_path
    win.setWindowTitle(f"{app_mod.APP_NAME} — GOOD-RECORDING")
    good_title = win.windowTitle()
    win._timing_restore_failed = False
    win._timing_restore_unreadable = False
    win._tracks_unreadable = False
    win._drop_notice = None
    DIALOGS.clear()
    finished = []
    win.loadFinished.connect(lambda: finished.append(True))

    # The doomed session trips EVERY notice flag on its way in: an unreadable sidecar, an unreadable
    # track DB, and a multi-drop notice. None of them describe the session that survives.
    doomed = _stub_session(
        restore_saved_timing_lines=lambda _p: sidecar_mod.UNREADABLE)

    class _RaiseForDoomed(_StubView):
        def __init__(self, session, *a, **k):
            if session is doomed:
                raise ValueError("central-view-blew-up")
            super().__init__(session, *a, **k)

    orig_unreadable = app_mod.track_db.unreadable
    app_mod.track_db.unreadable = lambda: True
    try:
        with _StubLoad(lambda paths: doomed), _SwapView(_RaiseForDoomed):
            win._load(["/tmp/pacer-lifecycle-doomed-GX010077.MP4"],
                      drop_notice="Dropped 2 recordings — opened the doomed one.")
            assert _pump(30.0, lambda: bool(finished)), "the load never reported finished"

        assert win.session is good_session, "the doomed session was left committed"
        assert list(win._paths) == good_paths, win._paths
        assert win._sidecar_path == good_sidecar, win._sidecar_path
        assert win.view is not None and win.centralWidget() is win.view, \
            "the rollback never put a view back on screen"
        assert win.view is not view, "the rollback must build a FRESH view, not resurrect the old one"
        assert finished == [True], finished
        assert DIALOGS and "unchanged" in DIALOGS[-1][1], DIALOGS

        # (1) The title is part of the commit, so it is part of the rollback. It used to read
        #     "Pacer Studio — doomed-GX010077.MP4" over the good session.
        assert win.windowTitle() == good_title, (
            f"the window is titled after the recording that FAILED: {win.windowTitle()!r}")
        assert "GX010077" not in win.windowTitle(), win.windowTitle()

        # (2) ...and so are the notice flags. Asserted at the flags AND at the sentence they derive,
        #     because the sentence is what the user actually reads.
        assert win._timing_restore_unreadable is False, "the failed load's sidecar flag stuck"
        assert win._tracks_unreadable is False, "the failed load's track-DB flag stuck"
        assert win._timing_restore_failed is False
        assert win._drop_notice is None, "the failed load's drop notice stuck"
        notice = win._session_notice() or ""
        for wrong in (app_mod.SIDECAR_UNREADABLE_NOTICE, app_mod.TRACKS_UNREADABLE_NOTICE,
                      "Dropped 2 recordings"):
            assert wrong not in notice, f"the restored session carries the failed load's notice: {notice!r}"
    finally:
        app_mod.track_db.unreadable = orig_unreadable
        _teardown(win)
    print("test_a_raising_view_build_on_a_reload_rolls_back_to_the_working_session OK")


def test_the_recovered_welcome_screen_stops_advertising_the_session_only_menus():
    """The recovery paths reach the welcome screen FROM a loaded session, which no other caller
    does — so they were the first to leave Session statistics, the excluded-laps toggle,
    Opportunities and the reference picker enabled over a window with no session at all.

    The handlers all early-return, so this is advertising rather than a crash — EXCEPT that a
    disabled QAction's shortcut is inert too, which is the entire reason ⌘⇧S is gated on the action
    (L1-06). Enabled, it stays a live keystroke that silently does nothing.

    Driven through the worst branch: the build raises AND the rollback build raises too, so the
    window ends on the welcome state with the session dropped."""
    win, _view = _window(build_menu=True)
    actions = ("_stats_action", "_excluded_action", "_opportunities_action", "_ref_action")
    for name in actions:
        assert getattr(win, name).isEnabled(), f"{name} should start enabled (a session is loaded)"
    finished = []
    win.loadFinished.connect(lambda: finished.append(True))

    # _RaisingView raises for EVERY session, so the rollback rebuild fails too.
    with _StubLoad(lambda paths: _stub_session()), _SwapView(_RaisingView):
        win._load(["/tmp/pacer-lifecycle-menus.MP4"])
        assert _pump(30.0, lambda: bool(finished)), "the load never reported finished"

    try:
        assert isinstance(win.centralWidget(), WelcomeView), type(win.centralWidget()).__name__
        assert not hasattr(win, "session")
        for name in actions:
            assert not getattr(win, name).isEnabled(), \
                f"{name} is still enabled (and its shortcut still live) on the welcome screen"
        assert not win._full_action.isEnabled(), "Load full recording stayed enabled"
    finally:
        _teardown(win)
    print("test_the_recovered_welcome_screen_stops_advertising_the_session_only_menus OK")


def test_cancel_on_the_loading_card_with_a_raising_rebuild_still_hands_a_window_back():
    """`_cancel_load`'s `_build_ui()` IS the hand-back. Unguarded, a raise there left the card up
    with a Cancel button that had just been clicked and done nothing — the precise state the method
    exists to prevent. The consolation is the welcome screen; the requirement is that it is
    reachable."""
    win, _view = _window()
    win._show_loading_placeholder(["/tmp/pacer-lifecycle-slow.MP4"], on_cancel=lambda: None)
    assert win.view is None, "the loading card is supposed to dispose the view it replaces"
    token = win._load_token

    with _SwapView(_RaisingView):
        win._cancel_load(token)

    try:
        assert isinstance(win.centralWidget(), WelcomeView), type(win.centralWidget()).__name__
        assert not hasattr(win, "session"), "an unshowable session stayed committed after a cancel"
        assert "couldn't restore" in win.statusBar().currentMessage(), \
            win.statusBar().currentMessage()
    finally:
        _teardown(win)
    print("test_cancel_on_the_loading_card_with_a_raising_rebuild_still_hands_a_window_back OK")


def test_a_failed_reload_whose_rebuild_raises_stops_promising_the_session_is_unchanged():
    """`_on_load_failed`'s rebuild is the same hand-back, and the dialog beside it PROMISES "your
    loaded session is unchanged". If the rebuild raises, that promise is false and the card is
    still up — so the promise is withdrawn and the welcome state goes on screen."""
    win, _view = _window()
    win._show_loading_placeholder(["/tmp/pacer-lifecycle-bad.MP4"], on_cancel=lambda: None)
    DIALOGS.clear()

    with _SwapView(_RaisingView):
        win._on_load_failed(["/tmp/pacer-lifecycle-bad.MP4"], RuntimeError("Failed to open file: x"))

    try:
        assert DIALOGS, "the load failure raised no dialog at all"
        assert "unchanged" not in DIALOGS[0][1], \
            "the dialog promised an unchanged session the window could not show"
        assert isinstance(win.centralWidget(), WelcomeView), type(win.centralWidget()).__name__
        assert not hasattr(win, "session")
    finally:
        _teardown(win)
    print("test_a_failed_reload_whose_rebuild_raises_stops_promising_the_session_is_unchanged OK")


# ==================================================== §3.5 · the reference-load lifecycle
def test_reference_loads_are_single_flight_and_the_supersede_is_queued_not_concurrent():
    """The docstring's promise, made falsifiable: three rapid picks must never run two full
    `Session.load`s at once, and the one that is finally adopted must be the LAST one picked."""
    win, _view = _window()
    adopted = []
    win.session.set_reference_session = lambda ref, source_label="": (adopted.append(ref), None)[1]
    win._apply_reference_change = lambda: None

    refs = {p: _stub_session() for p in ("/tmp/ref-A.MP4", "/tmp/ref-B.MP4", "/tmp/ref-C.MP4")}
    with _StubLoad(lambda paths: refs[paths[0]], delay_s=0.30) as stub:
        win._start_reference_load(["/tmp/ref-A.MP4"])
        first_worker = win._ref_load_worker
        win._start_reference_load(["/tmp/ref-B.MP4"])
        win._start_reference_load(["/tmp/ref-C.MP4"])
        # B was overtaken by C before either ran: only the LATEST request is ever queued.
        assert win._pending_reference_load is not None, "the supersede was not queued"
        assert win._pending_reference_load[1] == ["/tmp/ref-C.MP4"], win._pending_reference_load
        assert win._ref_load_worker is first_worker, \
            "a second reference worker was started while the first was still running"
        assert _pump(60.0, lambda: bool(adopted) and win._ref_load_worker is None), \
            f"the queued reference load never ran (calls={stub.calls})"

    try:
        assert stub.peak == 1, f"{stub.peak} concurrent reference Session.loads ran; single-flight is 1"
        assert stub.calls == [["/tmp/ref-A.MP4"], ["/tmp/ref-C.MP4"]], stub.calls
        assert adopted == [refs["/tmp/ref-C.MP4"]], "the adopted reference is not the last one picked"
        assert win._pending_reference_load is None, "the queue was not drained"
    finally:
        _teardown(win)
    print("test_reference_loads_are_single_flight_and_the_supersede_is_queued_not_concurrent OK")


def test_a_reference_landing_during_a_primary_reload_does_not_raise_in_its_queued_slot():
    """A reference finishing while the primary is RELOADING arrives with the loading card up and
    `self.view` already None (installing the card is what disposes the view). `_apply_reference_change`
    dereferenced it unguarded, so the apply raised inside a queued slot — printed to stderr and
    otherwise invisible. The apply is now view-guarded; the window chrome still updates, and the
    reference is on the SESSION, so the view the reload is about to build carries it anyway."""
    win, _view = _window()
    win._update_reference_status = lambda: None  # the fixture's chip stub; restore a real recorder
    chrome = []
    win._update_reference_status = lambda: chrome.append(True)
    win.session.set_reference_session = lambda ref, source_label="": None

    win._show_loading_placeholder(["/tmp/pacer-lifecycle-reload.MP4"], on_cancel=lambda: None)
    assert win.view is None, "the loading card is supposed to dispose the view it replaces"

    try:
        win._on_reference_loaded(win._ref_load_token, ["/tmp/ref.MP4"], _stub_session())
        assert chrome, "the window chrome must still be updated with no view on screen"
        # The other two view-dereferencing reference paths, in the same state.
        win.session.has_reference = lambda: True
        win.session.clear_reference = lambda: None
        win._clear_reference()
        win.session.reference_session = lambda: _stub_session()
        DIALOGS.clear()
        win._enter_cross_compare()
        assert DIALOGS and "compare unavailable" in DIALOGS[-1][0], DIALOGS
    finally:
        _teardown(win)
    print("test_a_reference_landing_during_a_primary_reload_does_not_raise_in_its_queued_slot OK")


def test_a_reference_that_lands_while_a_new_recording_is_opening_is_refused_not_swallowed():
    """A reference is attached to a SESSION, so one that lands while a PRIMARY load is in flight has
    nowhere to go: `self.session` is the outgoing session, and the reload replaces it.

    Measured before this guard: the reference was adopted, the chip appeared, and both vanished the
    instant the new session was committed — the user's pick simply gone, with nothing said. (On main
    this same scenario CRASHED in the queued slot instead; the view guard fixed the crash and left
    the silence.) It is refused up front now, deterministically, through the same channel every other
    reference refusal uses — rather than gambling the user's action on whether the reload happens to
    fail, which is the only outcome in which attaching it would have been worth anything.

    Driven end to end: a 0.8 s primary load overtaken by a 0.05 s reference load."""
    win, _view = _window()
    adopted = []
    win.session.set_reference_session = lambda ref, source_label="": (adopted.append(ref), None)[1]
    DIALOGS.clear()
    finished = []
    win.loadFinished.connect(lambda: finished.append(True))
    primary, ref = "/tmp/lifecycle-primary.MP4", "/tmp/lifecycle-ref.MP4"

    with _StubLoad(lambda paths: _stub_session(),
                   delay_s=lambda p: 0.8 if p[0] == primary else 0.05), _SwapView(_StubView):
        win._load([primary])
        win._start_reference_load([ref])
        assert _pump(30.0, lambda: bool(DIALOGS)), "the reference never reported back"
        # The refusal lands WHILE the primary is still reading — that is the whole scenario.
        assert win._loading_token is not None, "the primary load had already settled; race not driven"
        assert _pump(30.0, lambda: bool(finished)), "the primary load never settled"

    try:
        assert adopted == [], "the reference was attached to a session about to be replaced"
        assert "reference not loaded" in DIALOGS[0][0], DIALOGS
        assert "discarded" in DIALOGS[0][1], DIALOGS[0][1]
        # And the primary load landed normally: this must cost the OPEN nothing.
        assert win.view is not None and win.centralWidget() is win.view
        assert list(win._paths) == [primary], win._paths
    finally:
        _teardown(win)
    print("test_a_reference_that_lands_while_a_new_recording_is_opening_is_refused_not_swallowed OK")


def test_a_reference_load_failure_surfaces_the_reason_and_keeps_the_local_best_lap():
    """The reference feature is ADDITIVE: a reference that fails to load must cost the user nothing.
    Driven through the real worker, so the failure travels the real queued `failed` signal."""
    win, view = _window()
    adopted = []
    win.session.set_reference_session = lambda ref, source_label="": (adopted.append(ref), None)[1]
    DIALOGS.clear()

    with _StubLoad(lambda paths: RuntimeError("Failed to open file: /tmp/ref-bad.MP4")):
        win._start_reference_load(["/tmp/ref-bad.MP4"])
        assert _pump(30.0, lambda: bool(DIALOGS)), "the reference failure never surfaced"

    try:
        assert "reference not loaded" in DIALOGS[-1][0], DIALOGS
        assert adopted == [], "a failed reference must not be adopted"
        assert win.view is view and win.centralWidget() is view, "the session's view was disturbed"
        # A STALE failure (an older, superseded pick) is dropped in silence.
        DIALOGS.clear()
        win._on_reference_load_failed(win._ref_load_token - 1, ["/tmp/ref-old.MP4"], RuntimeError("x"))
        assert DIALOGS == [], "a superseded reference failure raised a dialog"
    finally:
        _teardown(win)
    print("test_a_reference_load_failure_surfaces_the_reason_and_keeps_the_local_best_lap OK")


# ==================================================== §3.7.1 · closeEvent mid-load and mid-export
def test_closing_the_window_mid_load_joins_the_worker_instead_of_destroying_it():
    """`closeEvent` drains so no QThread is destroyed while it runs. Zero assertions existed on it."""
    win, _view = _window()
    with _StubLoad(lambda paths: _stub_session(), delay_s=0.6), _SwapView(_StubView):
        win._load(["/tmp/pacer-lifecycle-closing.MP4"])
        worker = win._load_worker
        assert worker is not None and worker.isRunning(), "the load worker never started"
        win.close()                       # mid-load, exactly as ⌘Q during an open
        assert not worker.isRunning(), "closeEvent returned with the load QThread still running"
        assert worker.isFinished(), "the worker was abandoned rather than joined"
    _APP.processEvents()
    win.deleteLater()
    _APP.processEvents()
    print("test_closing_the_window_mid_load_joins_the_worker_instead_of_destroying_it OK")


def test_closing_the_window_mid_export_cancels_and_joins_the_render_thread():
    """§3.7.1's SUSPECTED item, driven. `_video_worker` was held on an attribute and nowhere else,
    so the drain never saw it and quitting mid-render destroyed a live QThread. It is in the drained
    set now — and cancelled FIRST, so joining a render costs about a frame instead of the minutes a
    full export takes."""
    win, _view = _window()
    frames = {"n": 0}

    class _SlowRenderer:
        def __init__(self, _session, _spec):
            pass

        def run(self, progress=None, cancel=None):
            for i in range(2000):
                if cancel is not None and cancel():
                    raise workers_mod.export_video.CancelledError()
                frames["n"] = i
                if progress is not None:
                    progress(i, 2000)
                time.sleep(0.005)

    with tempfile.TemporaryDirectory() as d:
        out = os.path.join(d, "lap.mp4")
        spec = SimpleNamespace(out_path=out, source=SimpleNamespace(cleanup=lambda: None))
        orig_renderer = workers_mod.export_video.Renderer
        orig_exec = export_controller.QProgressDialog.exec
        workers_mod.export_video.Renderer = _SlowRenderer
        # The real modal would spin a nested loop; return immediately so the render runs on with the
        # dialog up, which is exactly the state a ⌘Q lands in.
        export_controller.QProgressDialog.exec = lambda self: 0
        try:
            win.exports._run_video_export(spec, lap=1)
            worker = win._video_worker
            assert worker is not None and worker.isRunning(), "the render worker never started"
            assert worker in win._load_workers, \
                "the render QThread is not in the set closeEvent drains — quitting destroys it live"
            assert _pump(10.0, lambda: frames["n"] > 2), "the render never got going"
            t0 = time.monotonic()
            win.close()
            elapsed = time.monotonic() - t0
            assert not worker.isRunning(), "closeEvent returned with the render QThread still running"
            assert worker.isFinished(), "the render thread was abandoned rather than joined"
            # Cancelled, not waited out: the full 2000-frame render is ~10 s.
            assert elapsed < 5.0, f"closeEvent waited {elapsed:.1f} s — it did not cancel the render"
            assert not os.path.exists(out), "a cancelled render left its partial MP4 behind"
        finally:
            workers_mod.export_video.Renderer = orig_renderer
            export_controller.QProgressDialog.exec = orig_exec
    _APP.processEvents()
    win.deleteLater()
    _APP.processEvents()
    print("test_closing_the_window_mid_export_cancels_and_joins_the_render_thread OK")


# ==================================================== §3.7.2 · Load full recording
def test_load_full_recording_reloads_the_whole_recording_over_a_loaded_session():
    """The status bar's own "Load full recording" is a RELOAD-WHILE-LOADED — the #164 shape — and
    had zero references anywhere in the suite. Driven end to end: the chapters are discovered, the
    reload runs off-thread, and the window ends on the full set."""
    win, view = _window()
    win._paths = ["/tmp/GX010099.MP4"]
    sibs = ["/tmp/GX010099.MP4", "/tmp/GX020099.MP4"]
    orig_discover = app_mod.chapters.discover_siblings
    app_mod.chapters.discover_siblings = lambda _p: list(sibs)
    finished = []
    win.loadFinished.connect(lambda: finished.append(True))

    try:
        with _StubLoad(lambda paths: _stub_session()) as stub, _SwapView(_StubView):
            win._load_full_recording()
            assert _pump(30.0, lambda: bool(finished)), "the full-recording reload never settled"
        assert stub.calls == [sibs], stub.calls
        assert list(win._paths) == sibs, win._paths
        assert win.view is not None and win.view is not view, "the reload never swapped the view"
        assert win.centralWidget() is win.view
        assert view.disposed if hasattr(view, "disposed") else True
    finally:
        app_mod.chapters.discover_siblings = orig_discover
        _teardown(win)
    print("test_load_full_recording_reloads_the_whole_recording_over_a_loaded_session OK")


def test_load_full_recording_is_a_no_op_when_the_whole_recording_is_already_open():
    """The gate: `_chapter_subset()` is the ONE source of "you are looking at part of a recording",
    read both by the notice that says so and by the item that fixes it. Nothing to chain, no load."""
    win, _view = _window()
    win._paths = ["/tmp/GX010099.MP4"]
    orig_discover = app_mod.chapters.discover_siblings
    app_mod.chapters.discover_siblings = lambda _p: ["/tmp/GX010099.MP4"]
    try:
        with _StubLoad(lambda paths: _stub_session()) as stub:
            win._load_full_recording()
            _APP.processEvents()
        assert stub.calls == [], stub.calls
    finally:
        app_mod.chapters.discover_siblings = orig_discover
        _teardown(win)
    print("test_load_full_recording_is_a_no_op_when_the_whole_recording_is_already_open OK")


# ==================================================== §3.7.3 · the REAL forget ordering
def test_forget_recording_executes_the_real_remove_save_disable_delete_ordering():
    """The existing coverage re-implements this body next to the body; this EXECUTES it.

    The ordering is the safety property. `_disable_sidecar_if_open` must run while the sidecar is
    still on disk (so the live session stops pointing at it BEFORE the unlink, and a passive timing
    nudge cannot re-create the file mid-delete), and the index write must already have landed. Both
    are asserted from inside the real call, not inferred from the end state — and the media file is
    asserted untouched, which is the promise the whole feature rests on."""
    with tempfile.TemporaryDirectory() as d:
        media = os.path.join(d, "GX010042.MP4")
        with open(media, "w") as f:
            f.write("not really a video, but it must survive")
        side = sidecar_mod.sidecar_path(media)
        with open(side, "w") as f:
            f.write("{}")

        def _entry(stem, paths):
            return {"fingerprint": library.fingerprint(stem), "stem": stem, "track": "Stadium",
                    "date": "2026-01-01", "lap_count": 3, "best": 60.0, "theoretical": 59.0,
                    "paths": paths}

        stem = os.path.splitext(os.path.basename(media))[0]
        fp = library.fingerprint(stem)
        keep_fp = library.fingerprint("GX010099")
        library.save({"version": library.VERSION,
                      "entries": [_entry(stem, [media]),
                                  _entry("GX010099", [os.path.join(d, "GX010099.MP4")])]})

        win, _view = _window()
        win._sidecar_path = side
        win.view._sidecar_path = side
        seen = {}
        real_disable = win._disable_sidecar_if_open

        def _spy_disable(path):
            # Snapshot the world AT the disable, which is the point the ordering claim is about.
            seen["sidecar_still_on_disk"] = os.path.exists(path)
            seen["index_on_disk"] = [e["fingerprint"] for e in library.load()["entries"]]
            real_disable(path)
        win._disable_sidecar_if_open = _spy_disable

        try:
            index = win._forget_recording({"fingerprint": fp, "paths": [media]})

            assert seen.get("sidecar_still_on_disk") is True, \
                "the sidecar was unlinked BEFORE the live session was de-linked from it"
            assert fp not in seen["index_on_disk"], \
                "the index removal had not been saved by the time the sidecar was touched"
            assert [e["fingerprint"] for e in index["entries"]] == [keep_fp], index
            assert not os.path.exists(side), "the sidecar was not deleted"
            assert os.path.exists(media), "THE MEDIA FILE WAS TOUCHED — it never may be"
            assert win._sidecar_path is None and win.view._sidecar_path is None, \
                "the open session still points at the deleted sidecar"
            # Idempotent: forgetting it again (sidecar already gone) must not raise.
            win._forget_recording({"fingerprint": fp, "paths": [media]})
        finally:
            _teardown(win)
    print("test_forget_recording_executes_the_real_remove_save_disable_delete_ordering OK")


# ==================================================== §3.7.4 · the queued second load
def test_opening_b_while_a_is_loading_actually_loads_b():
    """Single-flight QUEUES the second open and `_on_worker_finished` restarts it. A regression that
    silently dropped B would ship green: the window would still hold a coherent session (A's), the
    token guard would still be honoured, and nothing would look wrong except that the recording the
    user asked for last never opened."""
    win, _view = _window()
    finished = []
    win.loadFinished.connect(lambda: finished.append(True))
    sessions = {"/tmp/lifecycle-A.MP4": _stub_session(), "/tmp/lifecycle-B.MP4": _stub_session()}

    with _StubLoad(lambda paths: sessions[paths[0]], delay_s=0.35) as stub, _SwapView(_StubView):
        win._load(["/tmp/lifecycle-A.MP4"])
        assert win._load_worker is not None and win._load_worker.isRunning()
        win._load(["/tmp/lifecycle-B.MP4"])      # while A is still reading
        assert win._pending_load is not None, "B was not queued"
        assert _pump(60.0, lambda: list(win._paths) == ["/tmp/lifecycle-B.MP4"]), \
            f"B never loaded (calls={stub.calls}, paths={win._paths})"

    try:
        assert stub.peak == 1, f"{stub.peak} concurrent primary loads ran; single-flight is 1"
        assert stub.calls == [["/tmp/lifecycle-A.MP4"], ["/tmp/lifecycle-B.MP4"]], stub.calls
        assert win.session is sessions["/tmp/lifecycle-B.MP4"], "A's result was applied over B's"
        assert win._pending_load is None
        # A's result was DROPPED by the token guard, so only B's load reached the UI.
        assert finished == [True], f"a superseded load was applied too: {finished}"
    finally:
        _teardown(win)
    print("test_opening_b_while_a_is_loading_actually_loads_b OK")


# ==================================================== §3.7.5 · the ideal-lap row selection
def test_selecting_an_ideal_decomposition_row_rings_the_corner_it_names():
    """The IDEAL LAP decomposition table's rows are clickable — the same `corner_clicked` pathway the
    other three tables use — and the click had never been driven. Content-only coverage cannot catch
    a broken selection model, an unset RING_ROLE, or a handler reading the wrong column."""
    from studio import stats_panel

    view, _s, _t0, _t1 = _realqt._real_central_view()
    try:
        table = view.stats_view.ideal_table
        assert table.rowCount() > 0, "the stadium fixture must yield an ideal-lap decomposition"
        seen = []
        view.stats_view.corner_clicked.connect(seen.append)

        table.selectRow(0)
        _APP.processEvents()
        expected = table.item(0, 0).data(stats_panel.RING_ROLE)
        assert seen == [expected], (seen, expected)
        assert expected is not None, "the row names no corner to ring"

        # Clearing the selection is the other half of the handler, and it must not raise.
        table.clearSelection()
        _APP.processEvents()
        assert seen[-1] is None, seen
    finally:
        view.hide()
        view.deleteLater()
        _APP.processEvents()
    print("test_selecting_an_ideal_decomposition_row_rings_the_corner_it_names OK")


# ==================================================== §3.8 · the loading card's determinism
def test_the_loading_card_is_deterministic_while_the_event_loop_runs():
    """§3.8 reported the card appearing on 6 of 8 instrumented reloads and never on 2, all of them
    3x past the 400 ms arm. PROBED, and the mechanism is the DRIVER, not the app.

    The card is armed by a 400 ms single-shot QTimer and cancelled by `_on_session_loaded`. Both the
    timeout and the worker's `loaded` signal are delivered by the event loop — and within ONE
    `processEvents()` pass, the posted queued signal is delivered BEFORE the expired timer's timeout.
    So a driver whose next pump lands after the load has already completed cancels a card it was
    owed, and one that pumps at any point between 400 ms and completion shows it. Measured over 8
    reloads of a 1.3 s stubbed load: tight pumping 8/8, 100 ms slices 8/8, 500 ms slices 8/8,
    1.45 s slices 0/8, one sleep past the load 0/8 — and in every skipped run the timer was already
    past its deadline when `_on_session_loaded` cancelled it.

    A running app pumps continuously — `QApplication.exec()` IS the loop, so it dispatches whenever
    there is anything to dispatch — and the deferred card is deterministic there, which is what this
    pins. (On a RELOAD, which is the only case with a deferred card at all, the window's own ~30 Hz
    tick timer is also live and forces a pass every 33 ms. That is a second guarantee, not the one
    the argument rests on: the tick timer is created in `_build_ui`, so it does not exist during a
    FIRST load — where `_arm_loading_placeholder` shows the card immediately and there is no timer
    to lose.) Nothing in `_arm_loading_placeholder` was changed: the 0/8 case needs a UI thread that
    is not running its event loop, and a card cannot paint on one of those either.

    HARNESS NOTE for the next lane: a driver that waits on a load with coarse sleeps will see no
    loading card. Pump in slices well under the load duration."""
    load_s = 0.8
    win, _view = _window()
    installed, done = [], []
    real_show = win._show_loading_placeholder
    win._show_loading_placeholder = lambda *a, **k: (installed.append(True), real_show(*a, **k))[1]
    win.loadFinished.connect(lambda: done.append(True))

    try:
        with _StubLoad(lambda paths: _stub_session(), delay_s=load_s), _SwapView(_StubView):
            for i in range(3):
                installed.clear()
                done.clear()
                assert win.centralWidget() is win.view, "the reload branch needs a live view on screen"
                win._load([f"/tmp/pacer-lifecycle-card-{i}.MP4"])
                # Tight pumping — a running event loop, not a sleeping driver.
                assert _pump(30.0, lambda: bool(done)), f"reload {i} never settled"
                assert installed == [True], (
                    f"reload {i}: a {load_s:.1f} s load, {app_mod.LOAD_PLACEHOLDER_MS} ms arm, "
                    f"and no loading card")
    finally:
        win._show_loading_placeholder = real_show
        _teardown(win)
    print("test_the_loading_card_is_deterministic_while_the_event_loop_runs OK")


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        print(f"START {t.__name__}", flush=True)
        t()
    print(f"\nALL {len(tests)} LOAD-LIFECYCLE TESTS PASSED", flush=True)


if __name__ == "__main__":
    # Hang watchdog (the house pattern for the Qt/event-loop suites): ctest buffers stdout until a
    # test ENDS, so a hang otherwise surfaces nothing. Dump every thread's stack at 180 s.
    faulthandler.enable()
    faulthandler.dump_traceback_later(180, repeat=True, exit=False)
    _run_all()
