"""Load-AFFORDANCE regression tests — the QA-sweep findings about what the app offers the user
WHILE it is loading (L10-03, L10-06, L10-08, L10-10), all against the REAL StudioWindow.

What each pins, and why it could regress:

  * the demo fetch (L10-03) — "Open demo" resolved the clip inline in the clicked slot, and that
    resolve falls through to urlopen + a streaming copy. The window froze with the welcome screen
    still painted, the button still enabled: 0 of ~125 expected 16 ms timer ticks were delivered and
    nothing on screen said the click had been received. Driven end-to-end with a slow resolver, so
    the assertion is "the event loop kept running AND the button said what it was doing", not "a
    QThread exists somewhere".
  * the loading card's Cancel (L10-06) — the card carried ZERO controls, so the app's longest
    routine wait was the one thing a user could not back out of (its own video export has had a
    Cancel all along). Driven through the production slots: a real _load, the real deferred-card
    slot, a real click on the real button.
  * the zero-lap sentence (L10-08) — four phrasings of one fact in a single frame, with the status
    bar restating the lap table's reason almost verbatim. Asserted ACROSS the two surfaces (bar vs
    the lap table's own empty-state label), so it fails the moment they drift apart again.
  * the folder drop (L10-10) — dropping the folder of chapters a GoPro card hands you was a total
    silent no-op: the filter returned [], so the drag was never even accepted.

Real Qt + a real worker QThread, so run offscreen:
    QT_QPA_PLATFORM=offscreen python tests/test_load_affordances.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# The inert media triplet (no decoder/audio device) — set BEFORE importing the studio widgets.
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QTimer, QUrl  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QMessageBox,
    QProgressBar,
    QPushButton,
)

_APP = QApplication.instance() or QApplication([])

# The real two-lap synthetic StudioWindow fixture (StudioWindow.__new__ + the production _build_ui),
# reused rather than re-derived — see tests/test_central_view_realqt.py.
import test_central_view_realqt as _realqt  # noqa: E402

from studio import app as app_mod  # noqa: E402
from studio import data_quality  # noqa: E402
from studio import workers as workers_mod  # noqa: E402
from studio.app import StudioWindow  # noqa: E402
from studio.central_view import CentralView  # noqa: E402
from studio.overlays import WelcomeView  # noqa: E402

# Every dialog the load path raised, as (title, text) — the app calls exec() on a QMessageBox
# INSTANCE, so the instance method is what has to be recorded (patching the class's static helpers
# misses it entirely and the test would block forever).
DIALOGS = []


def _capture_exec(self, *_a, **_k):
    DIALOGS.append((self.windowTitle(), self.text()))
    return QMessageBox.StandardButton.Ok


QMessageBox.exec = _capture_exec
QMessageBox.exec_ = _capture_exec


def _window():
    """A real StudioWindow over the synthetic 2-lap session, plus the async-load bookkeeping the
    real __init__ installs (the shared fixture builds the window via __new__)."""
    win, view = _realqt._studiowindow_with_view()
    win._load_token = 0
    win._load_worker = None
    win._load_workers = set()
    win._pending_load = None
    win._loading_token = None
    win._placeholder_timer = None
    win._demo_worker = None
    win._drop_notice = None
    win._timing_restore_failed = False
    win._notice = None
    win._ref_load_token = 0
    win._ref_load_worker = None
    return win, view


def _pump(deadline_s, until):
    end = time.time() + deadline_s
    while time.time() < end and not until():
        _APP.processEvents()
        time.sleep(0.004)
    return until()


# ============================================================ L10-03 · the demo fetch
def test_open_demo_keeps_the_event_loop_alive_and_says_it_is_working():
    """"Open demo" must not run its network resolve on the UI thread. With a deliberately slow
    resolver the click has to RETURN, the 16 ms heartbeat has to keep being delivered while the
    fetch runs, and the button has to be visibly busy — the three things that were all absent."""
    slow_s = 0.6

    def _slow_resolve():
        time.sleep(slow_s)
        return "/demo/pacer-demo-lap.mp4"

    app_mod.demo.resolve_demo_recording = _slow_resolve
    win = StudioWindow([])
    try:
        cw = win.centralWidget()
        assert isinstance(cw, WelcomeView), type(cw).__name__
        loaded = []
        win._load = lambda paths, **kw: loaded.append(list(paths))

        # A 16 ms heartbeat standing in for every timer the running app has (the ~30 Hz tick, the
        # progress bar's own animation): if the UI thread is blocked, none of these are delivered.
        ticks = {"n": 0}
        beat = QTimer(win)
        beat.setInterval(16)
        beat.timeout.connect(lambda: ticks.__setitem__("n", ticks["n"] + 1))
        beat.start()

        t0 = time.monotonic()
        cw.demo_btn.click()
        click_ms = (time.monotonic() - t0) * 1000.0
        # The click RETURNED while the fetch is still running...
        assert click_ms < slow_s * 1000 * 0.5, f"the click blocked for {click_ms:.0f} ms"
        # ... and while it runs the button is unmistakably busy (it used to stay enabled, undepressed
        # and unchanged — nothing on screen distinguished "working" from "ignored your click").
        assert not cw.demo_btn.isEnabled(), "the demo button must be disabled while it fetches"
        assert "Fetching" in cw.demo_btn.text(), cw.demo_btn.text()

        assert _pump(30.0, lambda: bool(loaded)), "the demo resolve never settled"
        assert loaded == [["/demo/pacer-demo-lap.mp4"]], loaded
        # The heartbeat kept beating THROUGH the fetch. 0.6 s at 16 ms is ~37 ticks; assert well
        # clear of the noise floor, because the number to beat is ZERO.
        expected = int(slow_s * 1000 / 16)
        assert ticks["n"] >= expected // 3, f"{ticks['n']} ticks delivered during the fetch"
        beat.stop()
    finally:
        win.close()
        _APP.processEvents()
        win.deleteLater()
    print("test_open_demo_keeps_the_event_loop_alive_and_says_it_is_working OK")


def test_open_demo_ignores_a_result_the_user_already_overtook():
    """A demo fetch that lands after the user gave up and opened their own recording must not yank
    the window to the demo. Same token rule a superseded session load obeys."""
    app_mod.demo.resolve_demo_recording = lambda: "/demo/pacer-demo-lap.mp4"
    win = StudioWindow([])
    try:
        loaded = []
        win._load = lambda paths, **kw: loaded.append(list(paths))
        win._open_demo()
        win._load_token += 1          # stand in for the recording the user opened meanwhile
        assert _pump(30.0, lambda: win._demo_worker is None), "the demo fetch never finished"
        assert loaded == [], loaded
    finally:
        win.close()
        _APP.processEvents()
        win.deleteLater()
    print("test_open_demo_ignores_a_result_the_user_already_overtook OK")


# ============================================================ L10-06 · a cancellable load
class _SlowSession:
    """Stands in for Session inside the worker so the read is still running when Cancel is clicked
    (a missing file fails in microseconds — faster than the card it is supposed to be cancelled
    from). Raises, so a result that ISN'T dropped would raise a dialog the test can see."""

    @staticmethod
    def load(paths, *_a, **_k):
        time.sleep(1.0)
        raise RuntimeError(f"Failed to open file: {paths[0] if paths else ''}")


def _sip_alive(obj) -> bool:
    """True while `obj`'s C++ object still exists (PySide6 raises RuntimeError on a deleted one)."""
    try:
        obj.objectName()
    except RuntimeError:
        return False
    return True


def test_the_loading_card_disposes_the_view_it_replaces():
    """W2R-01: an ordinary second File ▸ Open over a working session left the window PERMANENTLY on
    "Loading telemetry…" — the freshly-loaded session unreachable behind a card carrying nothing
    but a Cancel, and no way back short of quitting.

    The ORDERING is the bug. `setCentralWidget(card)` takes ownership of the widget it replaces, so
    by the time `_build_ui` ran its documented first act — "dispose the outgoing view" — that view
    was already destroyed. `PlayerPane.dispose()` touched `self._seam_watchdog`, a QTimer child of
    the destroyed pane, and the RuntimeError propagated out of `_on_session_loaded`, past
    `_build_ui`'s own `setCentralWidget(new view)` and past `_update_reference_status()`.

    So the contract is: the card disposes the view it is about to replace, WHILE it is still alive,
    and clears `self.view`. Only a load slower than LOAD_PLACEHOLDER_MS raises that card, which is
    why nothing caught it: the failure path (L10-01, #136) and the Cancel path (L10-06) were both
    covered — the SUCCESS path through the same card was not.
    """
    win, view = _window()
    win.resize(1440, 900)
    win.show()
    _APP.processEvents()
    assert win.centralWidget() is view

    disposed = {"n": 0}
    real_dispose = view.dispose
    view.dispose = lambda: (disposed.__setitem__("n", disposed["n"] + 1), real_dispose())

    win._show_loading_placeholder(["/tmp/GX010099.MP4"], on_cancel=lambda: None)

    assert disposed["n"] == 1, \
        "the loading card replaced the live view without disposing it first — its decoder and " \
        "g-meter overlay outlive it, and _build_ui's own dispose then runs on a dead object"
    assert win.view is None, \
        "self.view still points at the view the card just took ownership of; _build_ui will " \
        "dispose a deleted C++ object and strand the window on the card"
    assert win.centralWidget() is not view
    win.close()
    _APP.processEvents()
    print("test_the_loading_card_disposes_the_view_it_replaces OK")


def test_build_ui_survives_a_view_qt_already_deleted():
    """Belt and braces for the same crash. Even if something replaces the central widget without
    going through _dispose_view, the teardown must not raise: a dispose that can raise is a
    dispose that strands the window, which is precisely how a reload used to brick it.

    Driven at the level that actually failed — PlayerPane.dispose() with its _seam_watchdog
    destroyed — because the fix there is subtle: the guarded steps have to be LAMBDAS, since
    resolving `self._seam_watchdog.stop` on a deleted object raises at attribute access, outside
    any try that holds bound methods.
    """
    win, view = _window()
    pane = view.video.pane
    # shiboken6.delete, not deleteLater: the deferred delete needs a real event-loop pass, and a
    # test that silently left the object ALIVE would assert nothing at all.
    import shiboken6
    shiboken6.delete(pane._seam_watchdog)
    assert not _sip_alive(pane._seam_watchdog), \
        "the watchdog is still alive — this test would pass on the unguarded teardown"

    pane.dispose()          # must not raise
    win._dispose_view()     # nor must the window-level teardown
    assert win.view is None
    win.close()
    _APP.processEvents()
    print("test_build_ui_survives_a_view_qt_already_deleted OK")


def test_loading_card_offers_a_cancel_that_hands_the_session_back():
    """The loading card must carry a Cancel, and it must return the user to the session they had —
    with the in-flight load's result dropped, not applied late over the top of it."""
    win, view = _window()
    win.resize(1440, 900)
    win.show()
    _APP.processEvents()
    rows = view.table.table.rowCount()
    assert rows == 2, rows
    DIALOGS.clear()

    settled = {"v": False}
    win.loadFinished.connect(lambda: settled.__setitem__("v", True))
    # A load slow enough to still be running when the card goes up. Only the READ is stood in for:
    # the real SessionLoadWorker QThread, the real _load, the real deferred-card slot and the real
    # token bookkeeping all run.
    real_session = workers_mod.Session
    workers_mod.Session = _SlowSession
    try:
        win._load(["/tmp/GX010099.MP4"])
        token = win._load_token
        # Drive the production slot the deferred timer fires (LOAD_PLACEHOLDER_MS later), so the
        # card goes up exactly as it does on a load that outruns the grace period.
        win._on_placeholder_due(token, list(win._paths))
        card = win.centralWidget()
        assert card is not win.view, "the loading card should be up"
        assert card.findChild(QProgressBar, "LoadingBar") is not None

        cancel = card.findChild(QPushButton, "LoadingCancel")
        assert cancel is not None, "the loading card must offer a Cancel"
        assert cancel.isEnabled() and cancel.isVisible()
        cancel.click()

        # The session is back — a real, populated CentralView, not a card.
        central = win.centralWidget()
        assert isinstance(central, CentralView), type(central).__name__
        assert central is win.view
        assert central.table.table.rowCount() == rows
        assert win.session.lap_count() == 2
        assert "cancelled" in win.statusBar().currentMessage().lower(), \
            win.statusBar().currentMessage()
        assert win._load_token != token, "cancel must supersede the in-flight load"

        # ... and when the cancelled load finally settles, its result is DROPPED: no dialog, no
        # second swap, the same view still on screen.
        assert _pump(30.0, lambda: not win._load_workers), "the cancelled worker never finished"
        _APP.processEvents()
    finally:
        workers_mod.Session = real_session
    assert DIALOGS == [], DIALOGS
    assert win.centralWidget() is win.view
    assert not settled["v"], "a cancelled load must not report as settled"
    win.close()
    _APP.processEvents()
    print("test_loading_card_offers_a_cancel_that_hands_the_session_back OK")


def test_cancel_before_any_session_returns_to_the_welcome_state():
    """Cancelling the very FIRST load has no session to hand back, so it must leave a usable window
    (the welcome empty state) rather than the card it was cancelled from."""
    win = StudioWindow([])
    try:
        win._show_loading_placeholder(["/tmp/GX010099.MP4"],
                                      on_cancel=lambda: win._cancel_load(win._load_token))
        card = win.centralWidget()
        cancel = card.findChild(QPushButton, "LoadingCancel")
        assert cancel is not None
        cancel.click()
        assert isinstance(win.centralWidget(), WelcomeView), type(win.centralWidget()).__name__
        assert "cancelled" in win.statusBar().currentMessage().lower()
    finally:
        win.close()
        _APP.processEvents()
        win.deleteLater()
    print("test_cancel_before_any_session_returns_to_the_welcome_state OK")


# ============================================================ L10-08 · one sentence, one source
def test_zero_lap_sentence_is_the_same_on_the_bar_as_in_the_panel():
    """The status bar and the lap table state the same fact in the same frame, so they must state it
    in the SAME words — and the bar must not also restate the table's reason. Asserted across the
    two real surfaces, so a fresh phrasing in either one fails here."""
    win, view = _window()
    win.session.valid_lap_ids = lambda: []
    notice = win._apply_session_notice()

    panel_headline = view.table._empty.text().split("\n")[0]
    assert notice == panel_headline, f"bar {notice!r} vs panel {panel_headline!r}"
    # The reason belongs to the panel (which has room for it); the bar used to carry its own copy.
    assert "GPS may not have locked" in view.table._empty.text()
    assert "GPS may not have locked" not in notice, notice
    # And it comes from ONE place, so the next surface has something to adopt.
    assert notice == data_quality.NO_LAPS_HEADLINE, notice
    assert data_quality.NO_LAPS_REASON and data_quality.NO_LAPS_NEXT_ACTION
    assert data_quality.NO_LAPS_REASON != data_quality.NO_LAPS_NEXT_ACTION
    # The map's clause is the only one that names a next action — it must survive single-sourcing.
    assert "drag the start/finish line" in data_quality.NO_LAPS_NEXT_ACTION
    win.close()
    _APP.processEvents()
    print("test_zero_lap_sentence_is_the_same_on_the_bar_as_in_the_panel OK")


# ============================================================ L10-10 · dropping a folder
def _mime(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    return mime


class _FakeDrag:
    """The two calls StudioWindow.dragEnterEvent makes on the event (the same stand-in
    tests/test_studio_features.py uses for the drop path)."""

    def __init__(self, mime):
        self._mime = mime
        self.accepted = False

    def mimeData(self):
        return self._mime

    def acceptProposedAction(self):
        self.accepted = True


def test_dropping_a_folder_of_chapters_opens_it():
    """A GoPro card hands the user a FOLDER of chapters, and the welcome copy invites "a GoPro
    recording" — so the folder has to be droppable. It expands one level; grouping stays
    chapters.group_into_recordings' job."""
    with tempfile.TemporaryDirectory() as root:
        folder = os.path.join(root, "SD_30_08_26")
        os.makedirs(folder)
        for name in ("GX010065.MP4", "GX020065.MP4", "GX030065.MP4", "notes.txt"):
            with open(os.path.join(folder, name), "wb") as f:
                f.write(b"\x00" * 16)
        os.makedirs(os.path.join(folder, "Nested.MP4"))  # a .MP4-NAMED subfolder is not a recording

        got = StudioWindow._dropped_mp4s(_mime([folder]))
        assert [os.path.basename(p) for p in got] == \
            ["GX010065.MP4", "GX020065.MP4", "GX030065.MP4"], got

        # The drag is accepted, so macOS shows a droppable cursor instead of the reject one.
        win = StudioWindow([])
        try:
            event = _FakeDrag(_mime([folder]))
            win.dragEnterEvent(event)
            assert event.accepted, "a folder of chapters must be an accepted drag"

            # A folder with nothing to open is still refused — the cursor saying "no" is correct.
            empty = os.path.join(root, "Empty")
            os.makedirs(empty)
            assert StudioWindow._dropped_mp4s(_mime([empty])) == []
            event2 = _FakeDrag(_mime([empty]))
            win.dragEnterEvent(event2)
            assert not event2.accepted
        finally:
            win.close()
            _APP.processEvents()
            win.deleteLater()

        # Mixed drags still work, and files still keep their DROP order (L10-02 stays fixed).
        loose = os.path.join(root, "GX010099.MP4")
        with open(loose, "wb") as f:
            f.write(b"\x00" * 16)
        mixed = StudioWindow._dropped_mp4s(_mime([loose, folder]))
        assert [os.path.basename(p) for p in mixed] == \
            ["GX010099.MP4", "GX010065.MP4", "GX020065.MP4", "GX030065.MP4"], mixed
    print("test_dropping_a_folder_of_chapters_opens_it OK")


def _run_all():
    test_open_demo_keeps_the_event_loop_alive_and_says_it_is_working()
    test_open_demo_ignores_a_result_the_user_already_overtook()
    test_the_loading_card_disposes_the_view_it_replaces()
    test_build_ui_survives_a_view_qt_already_deleted()
    test_loading_card_offers_a_cancel_that_hands_the_session_back()
    test_cancel_before_any_session_returns_to_the_welcome_state()
    test_zero_lap_sentence_is_the_same_on_the_bar_as_in_the_panel()
    test_dropping_a_folder_of_chapters_opens_it()
    print("all load-affordance tests OK")


if __name__ == "__main__":
    _run_all()
