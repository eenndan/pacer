"""Load-path regression tests — the QA-sweep load/error findings (L10-01, L10-02, L10-04, L10-06,
MAP-06), all against the REAL StudioWindow load path.

What each pins, and why it could regress:

  * `_load_failure_message` (L10-04) — every ctor failure below GPMFSource surfaces as the SAME
    RuntimeError("Failed to open file: …"), so five structurally different malformed inputs used to
    collapse onto TWO headlines and a truncated REAL GoPro chapter was reported as "not a GoPro
    recording". The classifier is a pure `staticmethod`, so the whole table is driven here through
    the ACTUAL exceptions `Session.load` raises — no Qt, no window, no dialog.
  * a failed RELOAD (L10-01/L10-06) — the load runs off the UI thread, so `_load` swapping the live
    view out for the "Loading telemetry…" card left the window stranded on an endless card with 0
    controls when the load then failed, while the dialog claimed the previous session was fine.
    Driven end-to-end: a real window over a loaded session, a real `_load` of a missing file, a real
    worker thread, the real failure slot.
  * the multi-recording drop (L10-02) — the warning naming the recordings that were NOT opened was a
    6 s transient that the load it started overwrote after 2.5-3.6 s, and `_dropped_mp4s` sorted, so
    "the first recording" was the alphabetically first rather than the first dropped.
  * the auto-fit notice (MAP-06) — decided ONCE at load, so it survived byte-identical across the
    very start/finish drag that answered it.

Real Qt + a real worker QThread, so run offscreen:
    QT_QPA_PLATFORM=offscreen python tests/test_load_failure.py
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

from PySide6.QtCore import QMimeData, QUrl  # noqa: E402
from PySide6.QtWidgets import QAbstractButton, QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])

# The real two-lap synthetic StudioWindow fixture (StudioWindow.__new__ + the production _build_ui),
# reused rather than re-derived — see tests/test_central_view_realqt.py.
import test_central_view_realqt as _realqt  # noqa: E402

from studio.app import LOAD_PLACEHOLDER_MS, StudioWindow  # noqa: E402
from studio.central_view import CentralView  # noqa: E402
from studio.session import Session  # noqa: E402

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


def _malformed(root):
    """The five structurally different malformed inputs, built under `root`."""
    out = {"missing": os.path.join(root, "NoSuchRecording.MP4")}  # deliberately not created
    out["text"] = os.path.join(root, "NotAVideo.MP4")
    with open(out["text"], "w", encoding="utf-8") as f:
        f.write("a plain text file the user renamed to .MP4\n" * 40)
    out["empty"] = os.path.join(root, "Empty.mp4")
    with open(out["empty"], "wb"):
        pass
    out["folder"] = os.path.join(root, "AFolder.MP4")
    os.makedirs(out["folder"], exist_ok=True)
    # A truncated GoPro chapter: a real GoPro NAME whose bytes are an unreadable prefix. The name is
    # what separates "your copy is incomplete" from "that isn't a GoPro file".
    out["truncated"] = os.path.join(root, "GX010098.MP4")
    with open(out["truncated"], "wb") as f:
        f.write(b"\x00\x00\x00\x18ftypmp41" + b"\x00" * 4096)
    return out


# ============================================================ L10-04 · failure classification
def test_failure_message_distinguishes_five_malformed_inputs():
    """Five structurally different malformed inputs -> five DISTINCT, case-appropriate headlines,
    each naming a next action. Driven through the exceptions Session.load ACTUALLY raises (they are
    all the same RuntimeError, which is the whole point: the path has to be inspected)."""
    with tempfile.TemporaryDirectory() as root:
        bad = _malformed(root)
        heads = {}
        for key, path in bad.items():
            try:
                Session.load([path])
                exc = None
            except Exception as e:  # noqa: BLE001 — mirrors SessionLoadWorker.run
                exc = e
            assert exc is not None, f"{key} unexpectedly loaded"
            heads[key] = StudioWindow._load_failure_message([path], exc)

        assert len(set(heads.values())) == 5, heads
        # Each headline must name ITS case, not a neighbour's.
        assert "folder" in heads["folder"].lower(), heads["folder"]
        assert "0 bytes" in heads["empty"], heads["empty"]
        assert "moved" in heads["missing"], heads["missing"]
        # The regression that started this: a truncated REAL GoPro chapter read "doesn't look like a
        # GoPro recording". It is a GoPro file; only the copy is incomplete.
        assert "is a GoPro file" in heads["truncated"], heads["truncated"]
        assert "doesn't look like a GoPro recording" in heads["text"], heads["text"]
        # Every message ends by telling the user what to do next.
        for key, msg in heads.items():
            assert any(w in msg for w in ("Open it", "open the", "open the original", "Copy it",
                                          "copy it")), f"{key}: no next action in {msg!r}"
    print("test_failure_message_distinguishes_five_malformed_inputs OK")


def test_failure_message_generic_branch_is_reachable():
    """The "may be corrupt or unsupported" fallback fired 0/5 on the malformed inputs above — it is
    the honest catch-all for an UNEXPECTED exception, not dead code. An existing, readable file that
    fails for some other reason must reach it (and never leak the Python class name)."""
    with tempfile.TemporaryDirectory() as root:
        path = os.path.join(root, "GX010097.MP4")
        with open(path, "wb") as f:
            f.write(b"\x00" * 64)
        msg = StudioWindow._load_failure_message([path], ValueError("some numpy blow-up"))
        assert "may be corrupt or unsupported" in msg, msg
        assert "ValueError" not in msg, msg
        # No path at all (the "(no file)" case) must not raise.
        assert StudioWindow._load_failure_message([], RuntimeError("x"))
    print("test_failure_message_generic_branch_is_reachable OK")


# ============================================================ L10-01 / L10-06 · a failed reload
def test_failed_reload_keeps_then_restores_the_working_ui():
    """A reload that fails must leave the user with the session they had — not an endless loading
    card. Two guarantees in one real run:

      * L10-06 — the working UI is STILL on screen the instant the reload starts (the card is
        deferred LOAD_PLACEHOLDER_MS, and the load is off-thread, so a fast one never blanks it);
      * L10-01 — after the failure the central widget is a real CentralView with its lap rows and
        its controls back, and the dialog's reassurance is one that is actually true."""
    win, view = _window()
    win.resize(1440, 900)
    win.show()
    _APP.processEvents()
    rows = view.table.table.rowCount()
    assert rows == 2, rows

    DIALOGS.clear()
    done = {"v": False}
    win.loadFinished.connect(lambda: done.__setitem__("v", True))
    with tempfile.TemporaryDirectory() as root:
        win._load([os.path.join(root, "NoSuchRecording.MP4")])
        # L10-06: nothing was blanked — the loading card is ARMED, not installed. (Checked before
        # pumping: the worker's failure is a queued signal, so no slot has run yet.)
        assert win.centralWidget() is win.view, type(win.centralWidget()).__name__
        assert win._placeholder_timer is not None
        _APP.processEvents()
        assert win.centralWidget() is win.view, type(win.centralWidget()).__name__
        assert _pump(30.0, lambda: done["v"]), "the failed load never settled"
    # The load settled well inside the grace period, so the card never went up and the timer is
    # disarmed (nothing can blank the window after the fact).
    assert win._placeholder_timer is None

    # L10-01: a real, populated, interactive CentralView — not a bare placeholder.
    central = win.centralWidget()
    assert isinstance(central, CentralView), type(central).__name__
    assert central is win.view
    assert central.table.table.rowCount() == rows
    assert len([b for b in central.findChildren(QAbstractButton) if b.isVisible()]) > 0
    # ... and the good session is still the one the window points at.
    assert win.session.lap_count() == 2
    # The dialog says the true thing, and no longer hedges with "(if any)".
    assert DIALOGS, "no failure dialog was raised"
    text = DIALOGS[-1][1]
    assert "Your loaded session is unchanged." in text, text
    assert "if any" not in text, text
    win.close()
    _APP.processEvents()
    print("test_failed_reload_keeps_then_restores_the_working_ui OK")


def test_failed_reload_rebuilds_the_ui_when_the_loading_card_did_go_up():
    """The slow-load half of L10-01: once the card HAS replaced the view (a load that outran
    LOAD_PLACEHOLDER_MS), the failure slot must rebuild the session's UI. Drives the same production
    slot with the card already installed, so the rebuild path is covered even though the deferred
    card makes it rare."""
    win, view = _window()
    win.resize(1440, 900)
    win.show()
    _APP.processEvents()
    rows = view.table.table.rowCount()

    win._show_loading_placeholder(["/tmp/stadium.MP4"])
    _APP.processEvents()
    assert win.centralWidget() is not win.view  # stranded, exactly as the sweep found it

    DIALOGS.clear()
    win._on_load_failed(["/nope/NoSuchRecording.MP4"], RuntimeError("Failed to open file: nope"))
    central = win.centralWidget()
    assert isinstance(central, CentralView), type(central).__name__
    assert central is win.view
    assert central.table.table.rowCount() == rows
    assert DIALOGS and "Your loaded session is unchanged." in DIALOGS[-1][1]
    win.close()
    _APP.processEvents()
    print("test_failed_reload_rebuilds_the_ui_when_the_loading_card_did_go_up OK")


def test_first_load_failure_has_no_previous_session_line():
    """On a FIRST load there is no previous session, so the reassurance must not be stated at all
    (it used to read "The previously loaded session (if any) is unchanged" — meaningless here and
    false on a reload). The welcome empty state still comes up so the window stays usable."""
    win, _view = _window()
    del win.session  # the pre-first-load state
    win.view = None
    DIALOGS.clear()
    win._on_load_failed(["/nope/NoSuchRecording.MP4"], RuntimeError("Failed to open file: nope"))
    assert DIALOGS, "no failure dialog was raised"
    text = DIALOGS[-1][1]
    assert "unchanged" not in text, text
    assert "Couldn't find that file" in text, text
    assert win.centralWidget() is not None  # the welcome empty state
    win.close()
    _APP.processEvents()
    print("test_first_load_failure_has_no_previous_session_line OK")


# ============================================================ L10-02 · the multi-recording drop
def test_dropped_mp4s_preserves_drop_order():
    """"The FIRST recording dropped" has to mean the first one DROPPED. _dropped_mp4s sorted, so a
    drag whose first file sorted late silently opened someone else's recording."""
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in
                  ("/z/GX010099.MP4", "/a/GX010001.MP4", "/a/GX020001.MP4")])
    got = StudioWindow._dropped_mp4s(mime)
    assert [os.path.basename(p) for p in got] == ["GX010099.MP4", "GX010001.MP4", "GX020001.MP4"], got
    # Non-MP4 urls are still filtered out, and a url-less drag is still [].
    mime2 = QMimeData()
    mime2.setUrls([QUrl.fromLocalFile("/a/notes.txt"), QUrl.fromLocalFile("/a/GX010001.mp4")])
    assert [os.path.basename(p) for p in StudioWindow._dropped_mp4s(mime2)] == ["GX010001.mp4"]
    assert StudioWindow._dropped_mp4s(QMimeData()) == []
    print("test_dropped_mp4s_preserves_drop_order OK")


def test_multi_drop_warning_survives_the_load_it_started():
    """The warning naming the recordings that were NOT opened must still be discoverable after the
    load settles — it used to be a transient that _on_session_loaded overwrote."""
    win, _view = _window()
    win._drop_notice = ("Dropped 3 recordings — opened recording 0062. "
                        "Open the others one at a time.")
    notice = win._apply_session_notice()
    assert "Dropped 3 recordings" in notice, notice
    assert win.statusBar().currentMessage() == notice
    # Composed with (not replaced by) the timing-trust notice when both apply.
    win.session.track_name = None
    win.session._timing_user_confirmed = False
    notice = win._apply_session_notice()
    assert "Dropped 3 recordings" in notice and "unknown track" in notice, notice
    # A load that carries no drop warning clears the previous one.
    win._drop_notice = None
    win.session.track_name = "StadiumLoop"
    assert win._apply_session_notice() is None
    assert win.statusBar().currentMessage() == ""
    win.close()
    _APP.processEvents()
    print("test_multi_drop_warning_survives_the_load_it_started OK")


# ============================================================ MAP-06 · the notice is re-decided
def test_auto_fit_notice_retracts_when_the_start_line_is_placed():
    """The "auto-fitted; drag it into place" line was byte-identical across the drag that answered
    it, because the decision ran once at load. It must now be re-decided from the SAME seam that
    rebuilds the derived views (the view's timingEdited), off the SAME predicate the map's trust
    surfaces read (session.timing_verified)."""
    win, view = _window()
    win.session.track_name = None          # unknown track: the start line was auto-fitted
    win.session._timing_user_confirmed = False
    before = win._apply_session_notice()
    assert before is not None and "auto-fitted" in before, before
    assert win.statusBar().currentMessage() == before
    assert win.session.timing_verified is False

    # The user drags the start/finish line: _on_lines confirms the timing and emits timingEdited.
    win.session.confirm_timing()
    view.timingEdited.emit()
    _APP.processEvents()
    assert win.session.timing_verified is True
    assert win.statusBar().currentMessage() == "", win.statusBar().currentMessage()
    assert win._notice is None

    # ... and an undo that un-confirms the timing brings it back (the same seam, both directions).
    win.session._timing_user_confirmed = False
    view.timingEdited.emit()
    _APP.processEvents()
    assert win.statusBar().currentMessage() == before
    win.close()
    _APP.processEvents()
    print("test_auto_fit_notice_retracts_when_the_start_line_is_placed OK")


def test_zero_lap_notice_still_supersedes_the_timing_notice():
    """Priority is unchanged: a 0-valid-lap session says so instead of asking for a start-line drag
    (there is no lap timing to fix either way)."""
    win, _view = _window()
    win.session.track_name = None
    win.session._timing_user_confirmed = False
    win.session.valid_lap_ids = lambda: []
    notice = win._apply_session_notice()
    assert "no complete laps detected" in notice, notice
    assert "auto-fitted" not in notice, notice
    win.close()
    _APP.processEvents()
    print("test_zero_lap_notice_still_supersedes_the_timing_notice OK")


def _run_all():
    assert LOAD_PLACEHOLDER_MS > 0
    test_failure_message_distinguishes_five_malformed_inputs()
    test_failure_message_generic_branch_is_reachable()
    test_failed_reload_keeps_then_restores_the_working_ui()
    test_failed_reload_rebuilds_the_ui_when_the_loading_card_did_go_up()
    test_first_load_failure_has_no_previous_session_line()
    test_dropped_mp4s_preserves_drop_order()
    test_multi_drop_warning_survives_the_load_it_started()
    test_auto_fit_notice_retracts_when_the_start_line_is_placed()
    test_zero_lap_notice_still_supersedes_the_timing_notice()
    print("all load-failure tests OK")


if __name__ == "__main__":
    _run_all()
