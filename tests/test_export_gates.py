"""Regression tests for the QA-sweep export/library findings in studio/app.py (batch B09 + B07).

  * L12-02 (HIGH) — the overlay-MP4 export was the ONE shareable output exempt from the timing-
    trust verdict. Measured on a provisional recording: card_data().blocked True, both lap-card
    actions disabled, "Export overlay video…" still enabled — and it rendered a 40.6 MB / 696-frame
    clip with "LAP 1  0:13.100" burned in and no honesty marker anywhere in the frame. It now reads
    the SAME card_data verdict; it warns rather than refuses (a provisional clip is still useful for
    reviewing your own footage), so the tests assert the QUESTION, its default, and that a Cancel
    stops the export before the options dialog.

  * L1-03 — a recording with zero valid laps still exported: "Lap times (CSV)" wrote a 76-byte
    header-only file and the status bar reported success, in a window whose panels read "No complete
    laps found in this recording." 4 of 7 export actions were enabled, and 3 of 3 disabled actions
    described their feature rather than the reason. All four data exports now gate on has_laps and
    carry a REASON tooltip while off.

  * L12-04 — the report embedded the raw live map grab, so the exported document carried the coral
    video marker and the orange start-line drag handles (436 px from the raw grab, 4298 from the
    card's clean one). It now uses a report-flavoured grab: grab_clean's chrome suppression with the
    "Map key" put BACK (the report's only legend for its own glyphs) minus the "Drag = …" row.

  * L12-07 — the options dialog sold a file-size trade-off in six labels and stated no size at all,
    on a choice measured 3.01x apart. The hint now quantifies both combos.

  * L12-08 — the "remembered" preset was window-instance state (`getattr(self, '_export_res_idx')`),
    so it reset on every relaunch while every other UI choice persisted. Now in prefs.

  * L11-08 — "Reveal in Finder" discarded openUrl()'s bool and said nothing either way, while its
    peer "Back up…" reported both outcomes.

  * D2-B (this wave) — the overlay export never told you it finished. _run_video_export had ZERO
    coverage, which is exactly why: the only test touching the GUI path built a VideoExportWorker
    directly and skipped the dialog. Measured on the real window: after a SUCCESSFUL export the
    modal was still up, its label still read "Rendering lap 4 overlay video…", its bar had snapped
    back to empty (value -1 of 700 — `reset()` under `setAutoClose(False)` resets the bar but does
    not hide) and its one button still read "Cancel", so dismissing your finished export told you
    you cancelled it. The success line was painted BEHIND that WindowModal dialog and expired in 6 s.
    All three outcomes now take the modal down, and the five failure dialogs name the product in
    their BODY (macOS drops a QMessageBox title — the load path was fixed for this, the export
    never was).

  * PR #153's handoff — track_db refuses to overwrite a different circuit stored under the same
    name; the confirm that turns that refusal into a question lives here.

Fake sessions throughout (the duck-typed surface these entry points reach through), so no pacer, no
telemetry file and no render. Run: QT_QPA_PLATFORM=offscreen python tests/test_export_gates.py
"""
import json
import math
import os
import sys
import tempfile
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# The four persistence seams, diverted into one temp tree BEFORE any window exists: these tests
# WRITE prefs (the export preset) and the track DB (save-as-track), so without this they would
# rewrite the user's own preferences and tracks.json.
from studio import library, prefs, sidecar, track_db  # noqa: E402

_SEAMS = tempfile.mkdtemp(prefix="pacer-test-export-gates-")
for _mod, _name in ((prefs, "prefs"), (library, "library"), (track_db, "track_db")):
    _dir = os.path.join(_SEAMS, _name)
    os.makedirs(_dir, exist_ok=True)
    _mod._app_support_dir = (lambda d=_dir: d)
sidecar.sidecar_path = lambda _p, _d=_SEAMS: os.path.join(_d, "test.pacer.json")

from PySide6.QtCore import QBuffer, QIODevice, QThread, QTimer, Signal  # noqa: E402
from PySide6.QtGui import QDesktopServices, QImage  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QInputDialog,
    QLabel,
    QMessageBox,
    QProgressBar,
    QProgressDialog,
    QPushButton,
    QWidget,
)

_APP = QApplication.instance() or QApplication([])

from studio import app as studio_app  # noqa: E402
from studio import (  # noqa: E402
    coaching,
    data_quality,
    export_controller,  # noqa: E402
    export_data,
    export_video,
)
from studio.app import APP_NAME, StudioWindow  # noqa: E402
from studio.export_controller import ExportController  # noqa: E402

# The File ▸ Export data actions L1-03 is about, by the attribute the window keeps them on.
# "Copy stats summary" (N13) joined them: it publishes the same numbers the HTML report does, so
# it takes the same has_laps gate and the same REASON tooltip — L1-03's finding was that a new
# export action added later would be the one that silently skipped both.
DATA_EXPORTS = ("_export_laps_action", "_export_channels_action", "_export_report_action",
                "_copy_stats_action", "_export_video_action")
CARD_EXPORTS = ("_share_card_action", "_copy_card_action")


class FakeSession:
    """The Session surface the export entry points + share_card.card_data reach through."""

    def __init__(self, *, laps=(0, 1), verified=True, degraded=False, track="Daytona MK",
                 centroid=(52.0, -0.78)):
        self.track_name = track
        self.timing_verified = verified
        # THE CONSTANTS, not hand-typed literals. `"media_fallback"` is not
        # `MEDIA_CLOCK_FALLBACK` ("media_clock_fallback"), so `degraded=True` built a session whose
        # `.degraded` was False — no caller passes it today, which is exactly why it went
        # unnoticed, and the next test to use it would have passed vacuously.
        self.timing_quality = data_quality.TimingQuality(
            clock=(data_quality.MEDIA_CLOCK_FALLBACK if degraded
                   else data_quality.GPS9_TRUECLOCK), dropped_fraction=0.0)
        self._laps = list(laps)
        self._centroid = centroid

    def adopt_track(self, name):
        """Session.adopt_track — the seam File ▸ Save as track… uses instead of assigning
        track_name, so the name records the lines it certifies. This fake's timing_verified is a
        plain flag, so there is nothing else to model."""
        self.track_name = name

    def valid_lap_ids(self):
        return list(self._laps)

    def best_lap_id(self):
        return self._laps[0] if self._laps else None

    def lap_time(self, _lap_id):
        return 23.231

    def lap_window(self, _lap_id):
        return (0.0, 23.231)

    def ideal_total(self):
        return 22.9

    def ideal_donor_lap_id(self):
        # None = the ideal is stitched from more than one lap (the normal case). card_data reads
        # this to decide whether the card has an honest gap to state; a fake that omits it makes
        # `_share_card_blocked` swallow the AttributeError and grey the card action out.
        return None

    def session_date(self):
        return "2026-09-01"

    def point_count(self):
        return 4096

    def coaching_opportunities(self):
        return coaching.Opportunities(enough=False, n_laps=len(self._laps), median_lap_id=None,
                                      rows=[])

    def track_location(self):
        return self._centroid, None

    def timing_lines_latlon(self):
        lat, lon = self._centroid
        return [[lat, lon], [lat + 0.001, lon + 0.001]], []


def _window(session=None, *, paths=("/nonexistent/GX010099.MP4",)):
    """A real StudioWindow with the real menu bar (so every gated QAction and its tooltip is the
    production one) but no load: the welcome screen, then the fake session dropped in. `session=None`
    leaves the window session-less."""
    win = StudioWindow([])
    win.resize(1200, 800)
    if session is not None:
        win.session = session
        win._paths = list(paths)
    return win


# ============================================================ L1-03 — the has-laps gate
def test_a_zero_lap_recording_disables_every_data_export_with_a_reason():
    """No complete laps ⇒ nothing to write. All four data exports (and both card actions) go off,
    and each one's tooltip states the REASON, not the feature. On main all four stayed enabled."""
    win = _window(FakeSession(laps=()))
    win.exports.sync_menu()

    for name in DATA_EXPORTS + CARD_EXPORTS:
        action = getattr(win, name)
        assert not action.isEnabled(), f"{name} ({action.text()!r}) is offered with no valid lap"
        tip = action.toolTip()
        assert tip == ExportController._NO_LAPS_REASON, f"{name} tooltip is not the reason: {tip!r}"
        assert "No complete laps" in tip and "start/finish line" in tip, tip

    # And the feature description comes BACK with the laps — the reason must not be sticky.
    win.session = FakeSession()
    win.exports.sync_menu()
    for name in DATA_EXPORTS + CARD_EXPORTS:
        action = getattr(win, name)
        assert action.isEnabled(), f"{name} stayed disabled on a session with laps"
        assert action.toolTip() == action.property("featureTip"), name
        assert "No complete laps" not in action.toolTip(), name
    win.hide()
    print("test_a_zero_lap_recording_disables_every_data_export_with_a_reason OK")


def test_a_zero_lap_export_writes_nothing_and_says_why():
    """The click-time backstop: triggering the action anyway must not produce a header-only file
    with a success message. On main this wrote 76 bytes and reported 'exported save.out'."""
    win = _window(FakeSession(laps=()))
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "laps.csv")
        orig = QFileDialog.getSaveFileName
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
        try:
            win.exports.export_laps_csv()
            win.exports.export_report()
        finally:
            QFileDialog.getSaveFileName = orig
        assert not os.path.exists(out), "a 0-lap session still wrote an export file"
    message = win.statusBar().currentMessage()
    assert "no complete laps" in message.lower(), repr(message)
    assert "exported" not in message.lower(), repr(message)
    win.hide()
    print("test_a_zero_lap_export_writes_nothing_and_says_why OK")


# ============================================================ L12-02 — one trust verdict
class _Warnings:
    """Records every QMessageBox.warning and answers with a fixed button."""

    def __init__(self, answer):
        self.answer = answer
        self.seen = []

    def __call__(self, _parent, title, text, *_args, **_kw):
        self.seen.append((title, text))
        return self.answer

    @property
    def trust(self):
        return [t for _title, t in self.seen if "provisional" in t]


def _drive_video_export(win, warnings):
    """Run _export_overlay_video with ffmpeg + the source file faked present and the options dialog
    stubbed, returning True iff the export got as far as asking for resolution/quality."""
    reached = []
    orig_warning, orig_available = QMessageBox.warning, export_video.ffmpeg_available
    orig_ask = ExportController._ask_export_options
    QMessageBox.warning = staticmethod(warnings)
    export_video.ffmpeg_available = lambda: True
    ExportController._ask_export_options = lambda _self, _lap: reached.append(1)
    try:
        win.exports.export_overlay_video()
    finally:
        QMessageBox.warning = orig_warning
        export_video.ffmpeg_available = orig_available
        ExportController._ask_export_options = orig_ask
    return bool(reached)


def test_the_mp4_export_obeys_the_same_trust_verdict_as_the_lap_card():
    """One decision, every shareable output. On a session card_data() blocks, the overlay export
    must not proceed silently: it asks, defaults to Cancel, and a Cancel stops it before the options
    dialog. On main it went straight through to a render."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "GX010099.MP4")
        open(src, "wb").close()
        win = _window(FakeSession(verified=False), paths=(src,))
        win.exports.sync_menu()
        assert win._share_card_blocked(), "the fixture is not actually blocked"
        for name in CARD_EXPORTS:
            assert not getattr(win, name).isEnabled(), f"{name} is enabled on a blocked session"
        # The video action stays available on purpose — the honesty lives in the confirm.
        assert win._export_video_action.isEnabled()

        cancelled = _Warnings(QMessageBox.Cancel)
        assert not _drive_video_export(win, cancelled), \
            "a blocked session reached the options dialog without asking"
        assert len(cancelled.trust) == 1, cancelled.seen
        text = cancelled.trust[0]
        assert "auto-fitted" in text and "Save it as a track" in text, text
        assert "video export cancelled" in win.statusBar().currentMessage()

        # Confirmed: the driver may still have their provisional clip.
        confirmed = _Warnings(QMessageBox.Yes)
        assert _drive_video_export(win, confirmed), "a confirmed export was still blocked"
        assert len(confirmed.trust) == 1, confirmed.seen

        # Inverse control: a VERIFIED session is never asked.
        win.session = FakeSession(verified=True)
        quiet = _Warnings(QMessageBox.Cancel)
        assert _drive_video_export(win, quiet), "a verified session was stopped"
        assert quiet.trust == [], quiet.seen
        win.hide()
    print("test_the_mp4_export_obeys_the_same_trust_verdict_as_the_lap_card OK")


# ============================================================ D2-B — the export's terminal state
class _FakeVideoWorker(QThread):
    """VideoExportWorker's whole signal surface with no thread, no Renderer and no ffmpeg.

    It deliberately does NOT run: nothing is emitted until the driver below asks, from inside the
    modal loop — which is where the real worker's queued signals land, since `dlg.exec()` is the
    only event loop running while a render is in flight."""

    progress = Signal(int, int)
    finished_export = Signal(bool, str)

    def __init__(self, _session, _spec):
        super().__init__()
        self.cancels = 0

    def cancel(self):
        self.cancels += 1

    def start(self):            # never spawn a thread
        pass

    def wait(self, *_a, **_k):
        return True


class _FakeSpec:
    """ExportSpec's two attributes _run_video_export touches: where it wrote, and the temp
    concat-list file the chapter resolution may have made."""

    class _Source:
        def __init__(self):
            self.cleanups = 0

        def cleanup(self):
            self.cleanups += 1

    def __init__(self, out_path):
        self.out_path = out_path
        self.source = self._Source()


def _visible_state(win, dlg):
    """Everything a user can actually see about an in-flight/finished export."""
    bar = dlg.findChild(QProgressBar)
    return {
        "dialog_up": bool(dlg.isVisible()),
        "button": dlg.findChild(QPushButton).text(),
        "label": dlg.findChild(QLabel).text(),
        "bar": (bar.value(), bar.maximum()),
        "status": win.statusBar().currentMessage(),
    }


def _run_export_to_completion(win, *, ok, message="", lap=2, click_cancel=False,
                              click_reveal=False, openurl=True,
                              out_path="/tmp/GX010099_lap3_overlay.mp4"):
    """Drive the REAL _run_video_export end to end with the render faked out.

    QDialog.exec is swapped for a driver that emits the worker's progress and its final
    ok/message from inside the modal loop, so the completion handler runs exactly where it runs
    in production. Returns (mid_render, at_the_end, modals, worker, spec)."""
    spec = _FakeSpec(out_path)
    made, modals, mid = [], [], {}
    orig_worker, orig_exec = export_controller.VideoExportWorker, QDialog.exec
    orig_box_exec, orig_warning = QMessageBox.exec, QMessageBox.warning
    orig_openurl = QDesktopServices.openUrl
    end = {}

    def _box_exec(box, *_a, **_k):
        modals.append({"kind": "box", "title": box.windowTitle(), "body": box.text(),
                       # what the box tucks behind "Show Details…" — where a raw encoder tail
                       # belongs, and where a test has to be able to see it.
                       "details": box.detailedText(),
                       "icon": box.icon(),
                       "buttons": {b.text(): box.buttonRole(b) for b in box.buttons()},
                       "default": box.defaultButton().text() if box.defaultButton() else None})
        if click_reveal:
            for b in box.buttons():
                if b.text() == "Reveal in Finder":
                    b.click()
        return 0

    def _warning(_parent, title, text, *_a, **_k):
        modals.append({"kind": "warning", "title": title, "body": text})
        return QMessageBox.Ok

    def _exec(dlg):
        if not isinstance(dlg, QProgressDialog):
            return orig_exec(dlg)
        worker = made[-1]
        worker.progress.emit(0, 700)
        worker.progress.emit(350, 700)          # a determinate bar, part-way through
        mid.update(_visible_state(win, dlg))
        if click_cancel:
            dlg.cancel()                        # exactly what pressing the button does
            dlg.canceled.emit()
        worker.finished_export.emit(ok, message)
        end.update(_visible_state(win, dlg))
        return QDialog.Accepted

    def _worker(session, sp):
        made.append(_FakeVideoWorker(session, sp))
        return made[-1]

    export_controller.VideoExportWorker = _worker
    QDialog.exec = _exec
    QMessageBox.exec = _box_exec
    QMessageBox.warning = staticmethod(_warning)
    QDesktopServices.openUrl = staticmethod(lambda _url, a=openurl: a)
    try:
        win.statusBar().clearMessage()
        win.exports._run_video_export(spec, lap)
    finally:
        export_controller.VideoExportWorker = orig_worker
        QDialog.exec = orig_exec
        QMessageBox.exec = orig_box_exec
        QMessageBox.warning = orig_warning
        QDesktopServices.openUrl = orig_openurl
    return mid, end, modals, made[-1], spec


def test_a_finished_video_export_takes_the_modal_down_and_says_so():
    """The owner's report: "when video is finished exporting, there must be a clean message - now
    it does not say anything and the button keeps saying 'cancel'."

    Measured on main, after a SUCCESSFUL export: dialog_up True, label 'Rendering lap 4 overlay
    video…', bar (-1, 700) — empty, as if the render had restarted — button 'Cancel'. The user had
    to press Cancel to dismiss the thing they had just waited two minutes for, and the one success
    signal was painted behind that modal."""
    win = _window(FakeSession())
    mid, end, modals, worker, spec = _run_export_to_completion(win, ok=True)

    # While it runs, nothing changes: a determinate bar and a real Cancel.
    assert mid["dialog_up"] and mid["bar"] == (350, 700) and mid["button"] == "Cancel", mid
    assert "Rendering" in mid["label"], mid

    # ...and the moment it finishes, the modal is gone. That is the whole fix.
    assert not end["dialog_up"], (
        f"the progress dialog outlived the render: {end}")
    assert end["bar"] != (-1, 700), (
        f"reset() blanked the bar under a stale label instead of closing: {end}")
    assert worker.cancels == 0, "taking the dialog down fired cancel() on a finished worker"
    assert spec.source.cleanups == 1, "the temp concat-list file was not cleaned up"

    # One completion message, in the app's own vocabulary, naming the product in the BODY.
    assert len(modals) == 1 and modals[0]["kind"] == "box", modals
    box = modals[0]
    assert box["icon"] == QMessageBox.Information, box["icon"]
    assert APP_NAME in box["body"], box["body"]
    assert "GX010099_lap3_overlay.mp4" in box["body"], box["body"]
    assert "lap 3" in box["body"], f"the box names the lap by its 0-based id: {box['body']!r}"

    # By ROLE, never by index: QDialogButtonBox reorders per platform (macOS puts the accept
    # button rightmost, the Qt layout puts it first), so a positional assertion here would be a
    # different test on every OS.
    assert box["buttons"].get("Done") == QMessageBox.AcceptRole, box["buttons"]
    assert box["buttons"].get("Reveal in Finder") == QMessageBox.ActionRole, box["buttons"]
    assert "Cancel" not in box["buttons"], (
        f"the terminal state still offers a Cancel: {list(box['buttons'])}")
    assert box["default"] == "Done", box["default"]

    # The status line the app already emitted, now in the clear rather than behind a modal — and
    # still the same lower-case "exported <basename>" every other export writes.
    assert end["status"] == "exported GX010099_lap3_overlay.mp4", end["status"]
    win.hide()
    print("test_a_finished_video_export_takes_the_modal_down_and_says_so OK")


def test_the_finished_export_reveals_in_finder_and_reports_both_outcomes():
    """L11-08's rule, applied to the new affordance: openUrl can decline, and nothing else on
    screen changes when it does. The idiom is the SHARED _reveal_in_finder, not a third copy.

    It reveals the containing FOLDER: openUrl on the .mp4 itself would play it in QuickTime, which
    is not what "Reveal in Finder" means."""
    win = _window(FakeSession())
    for answer, expected in ((True, "revealed"), (False, "could not open")):
        _mid, end, _modals, _worker, _spec = _run_export_to_completion(
            win, ok=True, click_reveal=True, openurl=answer,
            out_path="/tmp/pacer-reveal/GX010099_lap3_overlay.mp4")
        assert expected in end["status"], f"openUrl -> {answer}: {end['status']!r}"
        assert "/tmp/pacer-reveal" in end["status"], end["status"]
        assert ".mp4" not in end["status"], (
            f"the reveal opened the file, not its folder: {end['status']!r}")
    # Not clicking it leaves the plain confirmation.
    _mid, end, _modals, _w, _s = _run_export_to_completion(win, ok=True)
    assert end["status"].startswith("exported "), end["status"]
    win.hide()
    print("test_the_finished_export_reveals_in_finder_and_reports_both_outcomes OK")


def test_a_cancelled_video_export_takes_the_modal_down_and_says_so():
    """Cancel already hid the dialog (QProgressDialog::cancel() force-hides regardless of
    autoClose), so this pins the half that was NOT broken against the fix breaking it — including
    that the completion handler's disconnect is a no-op on an already-cancelled run, and that no
    completion box appears for a file that was never written."""
    win = _window(FakeSession())
    _mid, end, modals, worker, spec = _run_export_to_completion(
        win, ok=False, message="cancelled", click_cancel=True)
    assert not end["dialog_up"], end
    assert worker.cancels == 1, f"the Cancel button did not reach the worker: {worker.cancels}"
    assert end["status"] == "video export cancelled", end["status"]
    assert modals == [], f"a cancelled export raised a dialog: {modals}"
    assert spec.source.cleanups == 1
    win.hide()
    print("test_a_cancelled_video_export_takes_the_modal_down_and_says_so OK")


def test_a_failed_video_export_takes_the_modal_down_and_names_the_product():
    """On main the failure box came up IN FRONT of a progress dialog still reading "Rendering…",
    and said "The render failed: …" with no title (macOS drops it) and no product name anywhere."""
    win = _window(FakeSession())
    _mid, end, modals, _worker, spec = _run_export_to_completion(
        win, ok=False, message="ffmpeg exited with code 1")
    assert not end["dialog_up"], f"the failure box was raised over a live progress dialog: {end}"
    assert len(modals) == 1 and modals[0]["kind"] == "box", modals
    body, details = modals[0]["body"], modals[0]["details"]
    assert APP_NAME in body, f"the export failure never names {APP_NAME}: {body!r}"
    # THE RAW TAIL MOVED OUT OF THE BODY (§7.5). "ffmpeg exited with code 1" is a diagnostic, not
    # an explanation: the body now names the case and a next action, and the encoder's own words
    # live behind Details where a bug report can still reach them.
    assert "ffmpeg exited with code 1" not in body, (
        "the body is quoting the encoder at the user again", body)
    assert "ffmpeg exited with code 1" in details, details
    assert "encoder stopped partway" in body, body
    # The title is NOT asserted: macOS drops a QMessageBox window title (windowTitle() is '' even
    # when set), which is exactly why the product name has to be in the body — see the same note
    # on the crash reporter in app._show_error_report.
    assert spec.source.cleanups == 1
    win.hide()
    print("test_a_failed_video_export_takes_the_modal_down_and_names_the_product OK")


def test_the_real_modal_loop_unwinds_when_a_finished_export_opens_its_box():
    """The four tests above swap QDialog.exec, so none of them exercises the thing the fix actually
    turns on: `dlg.hide()` called from a slot running INSIDE a live `dlg.exec()`, with a nested
    QMessageBox opened immediately afterwards.

    It is `QDialog::setVisible(false)` that exits a modal event loop — `close()` would work too but
    routes through QProgressDialog::closeEvent, which EMITS canceled(). If either half of that ever
    stopped holding, the app would hang on every successful export while every assertion above
    stayed green. So this runs the REAL loop and delivers the worker's completion through a QTimer,
    exactly as the real QThread's queued signal arrives — nothing else can deliver it, since
    dlg.exec() is the only event loop running during a render.

    A 4 s watchdog force-hides the dialog, so a regression is a failed assertion instead of a suite
    that hangs for ctest's 1500 s default."""
    win = _window(FakeSession())
    spec = _FakeSpec("/tmp/GX010099_lap3_overlay.mp4")
    boxes, watchdog = [], {"fired": False}

    class _TimerWorker(_FakeVideoWorker):
        """start() schedules the emissions instead of running them, which is what starting a real
        QThread amounts to from this side: they land once an event loop is spinning."""

        def start(self):
            QTimer.singleShot(0, lambda: self.progress.emit(350, 700))
            QTimer.singleShot(0, lambda: self.finished_export.emit(True, ""))

    def _bark():
        watchdog["fired"] = True
        for d in win.findChildren(QProgressDialog):
            d.hide()

    orig_worker, orig_box_exec = export_controller.VideoExportWorker, QMessageBox.exec
    export_controller.VideoExportWorker = _TimerWorker
    QMessageBox.exec = lambda box, *_a, **_k: boxes.append(box.text()) or 0
    QTimer.singleShot(4000, _bark)
    try:
        win.exports._run_video_export(spec, 2)          # a REAL dlg.exec(), no stub
    finally:
        export_controller.VideoExportWorker = orig_worker
        QMessageBox.exec = orig_box_exec

    assert not watchdog["fired"], (
        "dlg.exec() did not return when the finished export hid the dialog — the export would hang")
    assert len(boxes) == 1 and APP_NAME in boxes[0], boxes
    assert [d.isVisible() for d in win.findChildren(QProgressDialog)] == [False], \
        "a progress dialog was left visible after the real loop unwound"
    assert win.statusBar().currentMessage() == "exported GX010099_lap3_overlay.mp4", \
        win.statusBar().currentMessage()
    win.hide()
    print("test_the_real_modal_loop_unwinds_when_a_finished_export_opens_its_box OK")


def _video_export_dialog_bodies(win, session, src):
    """Every message the overlay export can raise, driven through the REAL entry points."""
    seen = []
    orig_warning, orig_available = QMessageBox.warning, export_video.ffmpeg_available
    orig_ask, orig_save = ExportController._ask_export_options, ExportController._export_save_path
    orig_build = export_video.build_lap_spec
    orig_run = ExportController._run_video_export
    QMessageBox.warning = staticmethod(
        lambda _p, title, text, *a, **k: (seen.append((title, text)), QMessageBox.Cancel)[-1])
    try:
        win.session = session
        # 1. no ffmpeg on PATH
        export_video.ffmpeg_available = lambda: False
        win._paths = [src]
        win.exports.export_overlay_video()
        # 2. a session with no source video file to render onto
        export_video.ffmpeg_available = lambda: True
        win._paths = []
        win.exports.export_overlay_video()
        # 3. the provisional-timing confirm (a question, but the same titleless box)
        win._paths = [src]
        win.session = FakeSession(verified=False)
        win.exports.export_overlay_video()
        # 4. build_lap_spec refuses the window
        win.session = session
        # A real ExportChoice, not a bare sentinel: _export_overlay_video reads BOTH of its fields
        # (the config goes to build_lap_spec, the lead widens the window before the source is
        # resolved), so a stand-in that is not one would fail on the plumbing rather than on the
        # dialog this section is measuring.
        ExportController._ask_export_options = lambda _s, _lap: studio_app.ExportChoice(
            config=export_video.OverlayConfig(), lead=0.0)
        ExportController._export_save_path = lambda _s, *a, **k: "/tmp/pacer-unwritten.mp4"
        ExportController._run_video_export = lambda *a, **k: None
        def _boom(*_a, **_k):
            raise ValueError("the lap window falls outside the footage")
        export_video.build_lap_spec = _boom
        win.exports.export_overlay_video()
    finally:
        QMessageBox.warning = orig_warning
        export_video.ffmpeg_available = orig_available
        ExportController._ask_export_options = orig_ask
        ExportController._export_save_path = orig_save
        ExportController._run_video_export = orig_run
        export_video.build_lap_spec = orig_build
    return seen


def test_every_video_export_dialog_names_the_product_in_its_body():
    """D2-10, applied to the export. macOS DROPS a QMessageBox's window title, so a body that does
    not name the product leaves an unattributed sentence in an untitled box — measured empty in all
    five of the load path's failure cases, which is why that path carries the name in its body.
    All five of the export's own dialogs said "Export overlay video" in the title and named nothing
    in the body. The fifth (the render failure) is covered by the test above, which drives the
    worker; these four are the ones reachable before a render starts."""
    with tempfile.TemporaryDirectory() as td:
        src = os.path.join(td, "GX010099.MP4")
        open(src, "wb").close()
        win = _window(FakeSession(), paths=(src,))
        bodies = _video_export_dialog_bodies(win, FakeSession(), src)
    assert len(bodies) == 4, [t for t, _b in bodies]
    for title, body in bodies:
        assert APP_NAME in body, f"{title!r} names nothing in its body: {body!r}"
        assert APP_NAME in title, f"{title!r} does not name the product either"
    # The case messages themselves are untouched — this is a naming line in front of them.
    joined = "\n".join(b for _t, b in bodies)
    assert "ffmpeg/ffprobe on PATH" in joined, joined
    assert "no source video file" in joined, joined
    assert "auto-fitted" in joined and "Save it as a track" in joined, joined
    assert "falls outside the footage" in joined, joined
    win.hide()
    print("test_every_video_export_dialog_names_the_product_in_its_body OK")


# ============================================================ L12-01 — the report's unit
def test_the_report_is_written_in_the_display_unit_and_the_csv_is_not():
    """_export_report threads the window's live speed unit into write_report_html; _export_laps_csv
    passes none, so the CSV keeps its canonical SI headers. On main the report took no unit at all."""
    win = _window(FakeSession())
    win.view = SimpleNamespace(map=QWidget(), plots=QWidget())
    win._speed_unit = "mph"
    seen = {}
    orig_report, orig_laps = export_data.write_report_html, export_data.write_laps_csv
    orig_dialog = QFileDialog.getSaveFileName
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "r.html")
        QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (out, ""))
        export_data.write_report_html = lambda *a, **k: seen.update(report=k)
        export_data.write_laps_csv = lambda *a, **k: seen.update(laps=(a[2:], k))
        try:
            win.exports.export_report()
            win.exports.export_laps_csv()
        finally:
            export_data.write_report_html = orig_report
            export_data.write_laps_csv = orig_laps
            QFileDialog.getSaveFileName = orig_dialog
    assert seen["report"].get("unit") == "mph", seen["report"]
    assert seen["laps"] == ((), {}), f"the CSV writer was given a unit: {seen['laps']}"
    win.hide()
    print("test_the_report_is_written_in_the_display_unit_and_the_csv_is_not OK")


# ============================================================ L12-04 — a document's map grab
class _FakeLegend(QWidget):
    """The map key's surface the report grab touches: a row list, a relayout, a visibility flag —
    AND ITS TWO COLLAPSE INPUTS, which this stand-in did not have.

    `_MapLegend` has had them since PR #190: `_collapsed` is the USER's choice and is PERSISTED to
    prefs.json, `_fits` is whether the canvas is tall enough for the full plate, and
    `painted_collapsed()` is the OR of the two. A fake with no collapse concept cannot fail the
    thing this section tests, so `test_the_report_map_keeps_its_key_…` went on passing while one
    click on the plate, ever, shipped every later report a title-only key."""

    _ROWS = (("marker", "Video position"), ("brake", "Brake point"),
             ("corner", "Corner apex (C#)"), ("start", "Drag = start / sector line"))

    def __init__(self, collapsed=False, fits=True):
        super().__init__()
        self.relayouts = 0
        self._collapsed = collapsed   # the user's persisted choice
        self._fits = fits             # room on the canvas for the full plate

    def painted_collapsed(self):
        return self._collapsed or not self._fits

    def _relayout(self):
        self.relayouts += 1


class _FakeMap(QWidget):
    """A grab-able widget with the MapView contract the two clean grabs use."""

    def __init__(self, collapsed=False, fits=True):
        super().__init__()
        self.resize(60, 40)
        self._map_key = _FakeLegend(collapsed=collapsed, fits=fits)
        self._map_key.setParent(self)
        self._map_key.show()
        self.at_grab = []
        self.repins = 0

    def _reposition_key(self):
        self.repins += 1

    def grab_clean(self):
        from contextlib import contextmanager

        @contextmanager
        def _ctx():
            self._map_key.hide()
            try:
                yield self
            finally:
                self._map_key.show()
        return _ctx()

    def grab(self):
        self.at_grab.append((self._map_key.isHidden(), tuple(self._map_key._ROWS),
                             self._map_key.painted_collapsed()))
        return super().grab()


def test_the_report_map_keeps_its_key_and_loses_the_interaction_chrome():
    """The report grab must sit between the two existing ones: grab_clean's chrome suppression, but
    with the explanatory key present — minus the row describing a drag. Everything is restored
    afterwards, so the live map is untouched. On main _grab_report_map_png did not exist and the
    report embedded the raw grab."""
    win = _window(FakeSession())
    fake = _FakeMap()
    png = win._grab_report_map_png(fake)
    assert png[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    assert len(fake.at_grab) == 1, fake.at_grab
    hidden, rows, painted_collapsed = fake.at_grab[0]
    assert not hidden, "the report grab hid the map key a document needs"
    assert not painted_collapsed, "the report grab kept the key but painted it collapsed"
    labels = [label for _kind, label in rows]
    assert "Brake point" in labels and "Corner apex (C#)" in labels, labels
    assert not any(kind == "start" for kind, _label in rows), labels

    # Restored: the class rows are back (no instance shadow), the plate was re-laid out and
    # re-pinned, and the key's live visibility is whatever grab_clean's own finally set.
    assert "_ROWS" not in fake._map_key.__dict__, "the row shadow leaked onto the live legend"
    assert fake._map_key._ROWS == _FakeLegend._ROWS, fake._map_key._ROWS
    assert fake._map_key.relayouts == 2 and fake.repins == 2, \
        (fake._map_key.relayouts, fake.repins)
    assert not fake._map_key.isHidden(), "the live map key stayed hidden after the grab"
    win.hide()
    print("test_the_report_map_keeps_its_key_and_loses_the_interaction_chrome OK")


def test_the_report_map_opens_a_key_the_user_collapsed_on_screen():
    """SW4-01: an on-screen PREFERENCE must not decide what a document says.

    PR #190 made the map key's collapse persist. Before it, collapse reset on every launch and on
    every recording, so a report exported from a fresh window always carried the full key; after
    it, one click on the plate — ever — put a 34 px `› Map key` title bar into every report from
    then on, in a document whose own map paints brake triangles and corner-apex dots with nothing
    anywhere explaining them. Measured through the app's own grab on the real MapView: 88 px and
    three rows with the preference open, 34 px and the title alone with it closed.

    So the grab opens the plate, and puts the user's choice back — the toggle callback that writes
    prefs.json is never invoked, so `prefs.map_key_collapsed()` cannot move either."""
    win = _window(FakeSession())
    fake = _FakeMap(collapsed=True)
    win._grab_report_map_png(fake)
    _hidden, rows, painted_collapsed = fake.at_grab[0]
    assert not painted_collapsed, (
        "the exported document got the collapsed plate the SCREEN was set to")
    assert len(rows) == 3, rows
    assert fake._map_key._collapsed is True, (
        "the export changed the user's on-screen collapse choice")
    win.hide()
    print("test_the_report_map_opens_a_key_the_user_collapsed_on_screen OK")


def test_the_report_map_leaves_the_short_canvas_fallback_alone():
    """...and the other half: `_fits` is PHYSICS, not a preference, so the grab must not override
    it. One drag of the map/charts splitter takes the canvas to 72 px (also the window's own
    973x528 minimum), where the three-row plate wants 88 px + an 8 px inset. #190's fallback paints
    the title-only plate there rather than letting Qt cut the plate at the canvas edge; forcing it
    open for the export would hand the document exactly that clipped plate. Measured: the whole map
    figure is 917x96 in that state, so the plate would be 92 % of it and still not fit."""
    win = _window(FakeSession())
    fake = _FakeMap(collapsed=False, fits=False)
    win._grab_report_map_png(fake)
    _hidden, _rows, painted_collapsed = fake.at_grab[0]
    assert painted_collapsed, (
        "the grab forced a plate open on a canvas with no room for it — the clipped-plate defect")
    assert fake._map_key._fits is False
    win.hide()
    print("test_the_report_map_leaves_the_short_canvas_fallback_alone OK")


def test_the_report_map_grab_survives_a_map_without_the_contract():
    """A bare widget (no grab_clean, no key) still exports — chrome must never fail an export."""
    win = _window(FakeSession())
    bare = QWidget()
    bare.resize(20, 20)
    assert win._grab_report_map_png(bare)[:8] == b"\x89PNG\r\n\x1a\n"
    win.hide()
    print("test_the_report_map_grab_survives_a_map_without_the_contract OK")


# ============================================================ W5-02 — a document, not a screen
def _png_bytes(w, h):
    """A real PNG of a known pixel size (what a grab hands the width helper)."""
    image = QImage(w, h, QImage.Format_RGB32)
    image.fill(0x202020)
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    image.save(buf, "PNG")
    return bytes(buf.data())


def test_the_report_figure_width_divides_out_the_screens_pixel_ratio():
    """`QWidget.grab()` renders at the screen's device pixel ratio, so the same panel comes back
    917 px wide on a non-Retina Mac and 1834 px on a Retina one; embedded with no width, the
    browser laid the figure out at that DEVICE width and the exported document described the
    machine (+22 % figures, 168 px longer page). The helper states the LOGICAL width instead —
    always <= the PNG's own, so a browser only ever downsamples. On main it did not exist."""
    win = _window(FakeSession())
    png = _png_bytes(1834, 558)
    win.devicePixelRatioF = lambda: 1.0
    assert win._report_image_width(png) == 1834
    win.devicePixelRatioF = lambda: 2.0
    assert win._report_image_width(png) == 917
    win.devicePixelRatioF = lambda: 0.0          # never divide by a nonsense ratio
    assert win._report_image_width(png) == 1834
    win.devicePixelRatioF = lambda: 1.0
    assert win._report_image_width(b"not a png at all") is None  # ⇒ no attribute, as before
    win.hide()
    print("test_the_report_figure_width_divides_out_the_screens_pixel_ratio OK")


def test_the_report_export_states_a_layout_width_for_every_figure():
    """End to end: _export_report hands the writer (title, png, width) per figure, and at DPR 1
    that width is the panel's own logical width — so the same export from a Retina machine lays
    out identically. On main the images were bare (title, png) pairs."""
    win = _window(FakeSession())
    map_w, plots_w = QWidget(), QWidget()
    map_w.resize(240, 100)
    plots_w.resize(240, 180)
    win.view = SimpleNamespace(map=map_w, plots=plots_w)
    seen = {}
    orig_report, orig_dialog = export_data.write_report_html, QFileDialog.getSaveFileName
    with tempfile.TemporaryDirectory() as td:
        QFileDialog.getSaveFileName = staticmethod(
            lambda *a, **k: (os.path.join(td, "r.html"), ""))
        export_data.write_report_html = lambda *a, **k: seen.update(k)
        try:
            win.exports.export_report()
        finally:
            export_data.write_report_html = orig_report
            QFileDialog.getSaveFileName = orig_dialog
    images = seen["images"]
    assert [t for t, *_ in images] == ["Track map", "Speed · Δ to best"], images
    for (title, png, width), widget in zip(images, (map_w, plots_w), strict=True):
        assert png[:8] == b"\x89PNG\r\n\x1a\n", title
        natural = QImage.fromData(png, "PNG").width()
        assert width == round(natural / win.devicePixelRatioF()) == widget.width(), \
            (title, width, natural, widget.width())
    win.hide()
    print("test_the_report_export_states_a_layout_width_for_every_figure OK")


# ============================================================ L12-07 — put a number on it
def test_the_options_hint_quantifies_the_size_and_the_work():
    """The dialog sold "larger file"/"smaller file" and stated no size. The hint now names a size,
    the frame count and the resolved encoder — and the presets differ by a real multiple."""
    win = _window(FakeSession())
    high = win.exports._export_size_hint(23.231, 1080, "high")
    standard = win.exports._export_size_hint(23.231, 720, "standard")
    for text in (high, standard):
        assert "MB" in text and "frames to render" in text, text
        assert any(enc in text for enc in (export_video.VT_H264, export_video.SW_H264)), text
    mb = [float(t.split()[1]) for t in (high, standard)]
    assert mb[0] > mb[1] * 2, f"1080p/High is not measurably bigger than 720p/Standard: {mb}"
    # Frames are exact (ceil(duration x fps)), not an estimate.
    assert f"{int(23.231 * 30) + 1} frames" in high, high
    # Nothing honest to say without a pixel count or a duration: say nothing.
    assert win.exports._export_size_hint(23.231, 99999, "high") == ""
    assert win.exports._export_size_hint(0.0, 1080, "high") == ""
    win.hide()
    print("test_the_options_hint_quantifies_the_size_and_the_work OK")


def _clear_export_preset():
    """Drop the persisted export preset so a guard can start from the app's own defaults.

    The preset is a REAL preference (redirected to this file's temp tree at the top), shared by
    every test in the process and by every run on a machine where the user has ever chosen one.
    A test that assumes an unstored state has to say so — and clear it."""
    data = prefs.load()
    for key in (ExportController._PREF_EXPORT_RES, ExportController._PREF_EXPORT_QUALITY,
                ExportController._PREF_EXPORT_LEAD, ExportController._PREF_EXPORT_SCOPE,
                ExportController._PREF_EXPORT_ASPECT, ExportController._PREF_EXPORT_FIT,
                ExportController._PREF_EXPORT_CONTENT):
        data.pop(key, None)
    prefs.save(data)


def _run_options_dialog(win, on_dialog):
    """Open the real _ask_export_options with QDialog.exec replaced by `on_dialog`."""
    orig = QDialog.exec
    QDialog.exec = on_dialog
    try:
        return win.exports._ask_export_options(0)
    finally:
        QDialog.exec = orig


def _combo(dlg, label):
    """The options dialog's combo for a form row, found by that row's LABEL.

    W10-06's successor. These guards used to index into `dlg.findChildren(QComboBox)` — `[0]` is
    Resolution, `[1]` is Quality — which is a positional assumption about a form that grows: a row
    inserted anywhere but the end silently re-points every index, and the tests then assert about a
    control nobody meant to drive. (The Run-up / run-off row was appended last, so the old indices
    would still have been right; that is luck, not a guarantee.) Matching the visible label is what
    makes the lookup say which control it means."""
    form = dlg.findChild(QFormLayout)
    assert form is not None, "the export options dialog has no form layout"
    for row in range(form.rowCount()):
        item = form.itemAt(row, QFormLayout.LabelRole)
        if item is not None and item.widget() is not None and item.widget().text() == label:
            field = form.itemAt(row, QFormLayout.FieldRole)
            assert field is not None and isinstance(field.widget(), QComboBox), label
            return field.widget()
    labels = [form.itemAt(r, QFormLayout.LabelRole).widget().text() for r in range(form.rowCount())]
    raise AssertionError(f"no {label!r} row in the export options dialog (rows: {labels})")


def test_the_hint_refreshes_on_the_quality_combo_too():
    """Only Resolution was wired to _update_hint, so changing the quality — the choice the copy
    sells hardest — left the estimate stale.

    W10-06 — this used to open the dialog and go straight to `setCurrentIndex(1)`, assuming it
    started at High. The preset is a real persisted preference (studio.prefs), so once ANY earlier
    test (or any user who has ever picked "Standard") has stored index 1, that call is a no-op and
    the guard asserts a change nothing requested: green in the file's hand-written order, red in
    any other, and blind to the regression it exists for on a machine where the preference is
    already Standard. It now clears the two preset keys and drives each combo off a KNOWN index,
    so the verdict does not depend on what ran before it."""
    _clear_export_preset()
    win = _window(FakeSession())
    texts = []

    def on_dialog(dlg):
        hint = [w for w in dlg.findChildren(QLabel) if "Output:" in w.text()][0]
        res, quality, lead = (_combo(dlg, "Resolution"), _combo(dlg, "Quality"),
                              _combo(dlg, "Run-up / run-off"))
        # The starting state is asserted, not assumed — the default preset is the top of each list.
        assert (res.currentIndex(), quality.currentIndex(), lead.currentIndex()) == (1, 0, 0), (
            "with no stored preset the dialog must open at 1080p/High/no run-up, got "
            f"{(res.currentIndex(), quality.currentIndex(), lead.currentIndex())}")
        texts.append(hint.text())
        quality.setCurrentIndex(1)            # Quality: High -> Standard (a real transition)
        assert quality.currentIndex() == 1
        texts.append(hint.text())
        res.setCurrentIndex(0)                # Resolution: 1080p -> 720p (a real transition)
        assert res.currentIndex() == 0
        texts.append(hint.text())
        lead.setCurrentIndex(2)               # Run-up: none -> 10 s (a real transition)
        assert lead.currentIndex() == 2
        texts.append(hint.text())
        return QDialog.Rejected

    _run_options_dialog(win, on_dialog)
    assert texts[0] != texts[1], f"the quality combo left the hint unchanged: {texts[0]!r}"
    assert texts[1] != texts[2], f"the resolution combo left the hint unchanged: {texts[1]!r}"
    # The run-up makes the clip LONGER, so it moves both numbers the size line quantifies. It was
    # the third combo added to a hint that only two were wired to; the size estimate would have
    # gone on describing an unpadded clip.
    assert texts[2] != texts[3], f"the run-up combo left the hint unchanged: {texts[2]!r}"
    win.hide()
    print("test_the_hint_refreshes_on_the_quality_combo_too OK")


def test_the_hint_never_promises_a_run_up_the_footage_cannot_give():
    """The size line is derived from a DURATION, and with a run-up that duration is no longer
    `lap_time`. Deriving it as `lap_time + 2 * lead` would be wrong at exactly the place a user
    notices — the first lap, where the recording has nothing before the start line to give.

    This fake's lap runs 0.000 .. 23.231, so a 10 s run-up can only be a 10 s run-OFF. The hint
    goes through `export_video.lap_window_for_export`, the same funnel the render resolves its
    window with, so it reports 33.231 s and not 43.231."""
    win = _window(FakeSession())
    assert win.exports._export_clip_seconds(0, 0.0) == 23.231
    assert win.exports._export_clip_seconds(0, 10.0) == 33.231, win.exports._export_clip_seconds(0, 10.0)
    # and the frame count in the size line follows it (ceil(duration x 30))
    hint = win.exports._export_size_hint(win.exports._export_clip_seconds(0, 10.0), 1080, "high")
    assert f"{int(33.231 * 30) + 1} frames" in hint, hint
    # A lap the session cannot place has no honest duration, so the hint says nothing rather than
    # guessing — the same contract _export_size_hint already has for "Source" and for a NaN lap.
    class _Unplaceable(FakeSession):
        def lap_window(self, _lap_id):
            return None

    unplaceable = _window(_Unplaceable())
    assert math.isnan(unplaceable.exports._export_clip_seconds(0, 5.0))
    assert unplaceable.exports._export_size_hint(unplaceable.exports._export_clip_seconds(0, 5.0), 1080, "high") \
        == ""
    unplaceable.hide()
    win.hide()
    print("test_the_hint_never_promises_a_run_up_the_footage_cannot_give OK "
          "(23.231 s + 10 s run-up/run-off = 33.231 s, not 43.231 s)")


# ============================================================ L12-08 — remember it for real
def test_the_export_preset_survives_a_new_window():
    """The picker claimed to remember the choice; it was window-instance state that died with the
    window. It is a preference now, like the unit and the palette."""
    picked = _window(FakeSession())
    choice = _run_options_dialog(picked, lambda dlg: (
        _combo(dlg, "Resolution").setCurrentIndex(0),
        _combo(dlg, "Quality").setCurrentIndex(1),
        _combo(dlg, "Run-up / run-off").setCurrentIndex(2),
        QDialog.Accepted)[-1])
    picked.hide()
    # The accepted choice carries the run-up out of the dialog as SECONDS, not as an index — it is
    # what widens the export window, so a picker that stored it and forgot to return it would
    # remember a preference the render never applies.
    assert choice.lead == 10.0, choice
    stored = json.load(open(prefs.prefs_path(), encoding="utf-8"))
    assert stored.get(ExportController._PREF_EXPORT_RES) == 0, stored
    assert stored.get(ExportController._PREF_EXPORT_QUALITY) == 1, stored
    assert stored.get(ExportController._PREF_EXPORT_LEAD) == 2, stored

    seen = {}

    def on_reopen(dlg):
        seen["idx"] = (_combo(dlg, "Resolution").currentIndex(),
                       _combo(dlg, "Quality").currentIndex(),
                       _combo(dlg, "Run-up / run-off").currentIndex())
        return QDialog.Rejected

    fresh = _window(FakeSession())          # a different StudioWindow, as after a relaunch
    _run_options_dialog(fresh, on_reopen)
    assert seen["idx"] == (0, 1, 2), \
        f"a fresh window reopened on {seen['idx']}, not the saved preset"
    fresh.hide()
    print("test_the_export_preset_survives_a_new_window OK")


def test_a_garbage_stored_preset_falls_back_to_the_default():
    """Guarded accessor: an out-of-range index from an older build opens on the default rather than
    raising out of a dialog the user just asked for."""
    win = _window(FakeSession())
    prefs.set(ExportController._PREF_EXPORT_RES, 99)
    prefs.set(ExportController._PREF_EXPORT_QUALITY, "high")
    prefs.set(ExportController._PREF_EXPORT_LEAD, -1)
    seen = {}

    def on_dialog(dlg):
        seen["idx"] = (_combo(dlg, "Resolution").currentIndex(),
                       _combo(dlg, "Quality").currentIndex(),
                       _combo(dlg, "Run-up / run-off").currentIndex())
        return QDialog.Rejected

    _run_options_dialog(win, on_dialog)
    assert seen["idx"] == (1, 0, 0), seen
    prefs.set(ExportController._PREF_EXPORT_RES, 1)
    prefs.set(ExportController._PREF_EXPORT_QUALITY, 0)
    prefs.set(ExportController._PREF_EXPORT_LEAD, 0)
    win.hide()
    print("test_a_garbage_stored_preset_falls_back_to_the_default OK")


# ============================================================ L11-08 — say something either way
def test_reveal_in_finder_reports_both_outcomes():
    """The button asks a system handler that can decline, and nothing else on screen changes when it
    does. On main the bool was discarded and the status bar stayed empty in both directions."""
    win = _window(FakeSession())
    orig = QDesktopServices.openUrl
    try:
        for answer, expected in ((True, "revealed"), (False, "could not open")):
            QDesktopServices.openUrl = staticmethod(lambda _url, a=answer: a)
            win.statusBar().clearMessage()
            win._reveal_library()
            message = win.statusBar().currentMessage()
            assert expected in message, f"openUrl -> {answer}: {message!r}"
            assert os.path.dirname(library.library_path()) in message, message
    finally:
        QDesktopServices.openUrl = orig
    win.hide()
    print("test_reveal_in_finder_reports_both_outcomes OK")


# ============================================================ PR #153's handoff — the confirm
class _Questions:
    def __init__(self, answer):
        self.answer = answer
        self.seen = []

    def __call__(self, _parent, title, text, *_args, **_kw):
        self.seen.append((title, text))
        return self.answer


def _save_as_track(win, name, answer=None):
    """Drive File ▸ Save as track… with the name dialog + any replace confirm stubbed."""
    asked = _Questions(answer if answer is not None else QMessageBox.No)
    prompts = []
    orig_text, orig_question = QInputDialog.getText, QMessageBox.question
    QInputDialog.getText = staticmethod(
        lambda _p, _t, label, *a, **k: (prompts.append(label), (name, True))[-1])
    QMessageBox.question = staticmethod(asked)
    try:
        win._save_as_track()
    finally:
        QInputDialog.getText = orig_text
        QMessageBox.question = orig_question
    return asked, prompts[0]


def test_save_as_track_asks_before_it_replaces_a_different_circuit():
    """PR #153 made track_db REFUSE a silent overwrite and left the confirm here. A name reused for
    a circuit far away must ask, keep the stored lines on No, and say which happened."""
    db = track_db.db_path()
    if os.path.exists(db):
        os.remove(db)
    first = _window(FakeSession(track=None, centroid=(52.0, -0.78)))
    asked, _prompt = _save_as_track(first, "My Circuit")
    assert asked.seen == [], "saving a brand-new name asked a question"
    stored = track_db.load()["tracks"][0]["start"]
    assert "saved track 'My Circuit'" in first.statusBar().currentMessage()
    first.hide()

    # A different circuit (~0.7 deg of latitude ≈ 78 km) under the SAME name.
    other = _window(FakeSession(track=None, centroid=(52.7, -0.78)))
    asked, _prompt = _save_as_track(other, "My Circuit", QMessageBox.No)
    assert len(asked.seen) == 1, asked.seen
    title, text = asked.seen[0]
    assert title == "Replace track" and "km from here" in text, (title, text)
    assert track_db.load()["tracks"][0]["start"] == stored, "a declined confirm still overwrote"
    assert "kept the track already saved" in other.statusBar().currentMessage()

    asked, _prompt = _save_as_track(other, "My Circuit", QMessageBox.Yes)
    assert len(asked.seen) == 1, asked.seen
    assert track_db.load()["tracks"][0]["start"] != stored, "a confirmed replace did not write"
    assert "replaced track 'My Circuit'" in other.statusBar().currentMessage()
    other.hide()
    os.remove(db)
    print("test_save_as_track_asks_before_it_replaces_a_different_circuit OK")


def test_save_as_track_names_the_provisional_line_in_its_prompt():
    """Promoting auto-fitted lines is the documented remedy for provisional timing, so this must not
    block — but the save makes them the trusted line for every future recording here, which is worth
    one sentence. A verified session gets the plain prompt."""
    db = track_db.db_path()
    if os.path.exists(db):
        os.remove(db)
    win = _window(FakeSession(verified=False, track=None, centroid=(41.0, 2.0)))
    _asked, prompt = _save_as_track(win, "Provisional Place")
    assert "auto-fitted" in prompt and "check it on the map" in prompt, prompt
    win.session = FakeSession(verified=True, track=None, centroid=(41.0, 2.0))
    _asked, prompt = _save_as_track(win, "Provisional Place")
    assert prompt == "Track name:", prompt
    win.hide()
    os.remove(db)
    print("test_save_as_track_names_the_provisional_line_in_its_prompt OK")


# ============================================ the picker's new rows (scope, shape, alpha contents)
def test_every_picker_row_is_a_value_the_renderer_actually_handles():
    """The dialog's combos are built from tables in the controller, and every entry has to be a
    vocabulary word `export_video` knows — otherwise a row the user can choose is a render the
    module cannot build. Asserted against the module's own constants rather than the strings, so
    renaming one in a single place fails here instead of shipping."""
    scopes = [v for _label, v in ExportController._EXPORT_SCOPE_OPTIONS]
    assert scopes == [export_video.SCOPE_THIS_LAP, export_video.SCOPE_BEST_LAP,
                      export_video.SCOPE_ALL_LAPS, export_video.SCOPE_SESSION], scopes
    aspects = [v for _label, v in ExportController._EXPORT_ASPECT_OPTIONS]
    assert aspects == [export_video.ASPECT_SOURCE, export_video.ASPECT_9_16,
                       export_video.ASPECT_1_1], aspects
    for aspect in aspects:
        assert aspect in export_video.ASPECT_RATIOS, aspect
    fits = [v for _label, v in ExportController._EXPORT_FIT_OPTIONS]
    assert fits == [export_video.FIT_CROP, export_video.FIT_FIT], fits
    contents = [v for _label, v in ExportController._EXPORT_CONTENT_OPTIONS]
    assert contents == [ExportController._EXPORT_CONTENT_COMPOSITE, export_video.ALPHA_PRORES,
                        export_video.ALPHA_PNG], contents
    # The scope row leads, because it is the one that decides whether this takes 90 s or 30 min.
    assert ExportController._EXPORT_SCOPE_OPTIONS[0][1] == export_video.SCOPE_THIS_LAP
    # And every row reads as a choice rather than a code: a distinct, non-empty label per entry.
    for table in (ExportController._EXPORT_SCOPE_OPTIONS, ExportController._EXPORT_ASPECT_OPTIONS,
                  ExportController._EXPORT_FIT_OPTIONS, ExportController._EXPORT_CONTENT_OPTIONS):
        labels = [label for label, _v in table]
        assert all(len(label) > 3 for label in labels), labels
        assert len(set(labels)) == len(labels), f"two rows read the same: {labels}"
        assert not any(label in {v for _lab, v in table} for label in labels), \
            f"a label is just its value spelled out: {labels}"
    print("ok picker: every row is a value the renderer handles")


def test_the_estimate_knows_a_vertical_frame_is_not_a_landscape_one():
    """The size line used to assume 16:9 for the width. A 9:16 or 1:1 output has a different pixel
    count for the same resolution row, and the estimate has to use it or it under-states a square
    export by 44 %."""
    ctl = ExportController.__new__(ExportController)
    assert ctl._estimate_frame_size(1080, export_video.ASPECT_SOURCE) == (1920, 1080)
    assert ctl._estimate_frame_size(1080, export_video.ASPECT_9_16) == (1080, 1920)
    assert ctl._estimate_frame_size(1080, export_video.ASPECT_1_1) == (1080, 1080)
    print("ok estimate: the frame size follows the chosen shape")


def test_an_alpha_export_is_warned_about_in_gigabytes_before_it_starts():
    """A user reported a 30-minute transparent ProRes export at 140 GB after six hours. The row is
    worth offering; offering it without a number is the trap. Half an hour of 1080p ProRes 4444
    must come out in GB and must be several times the H.264 estimate beside it."""
    ctl = ExportController.__new__(ExportController)
    half_hour = 30 * 60.0
    h264 = ctl._export_size_hint(half_hour, 1080, "high", export_video.ASPECT_SOURCE,
                                 ExportController._EXPORT_CONTENT_COMPOSITE)
    prores = ctl._export_size_hint(half_hour, 1080, "high", export_video.ASPECT_SOURCE,
                                   export_video.ALPHA_PRORES)
    png = ctl._export_size_hint(half_hour, 1080, "high", export_video.ASPECT_SOURCE,
                                export_video.ALPHA_PNG)
    assert "GB" in prores, prores
    assert "ProRes 4444" in prores and "PNG sequence" in png, (prores, png)

    def gb(text):
        n = float(text.split("About ")[1].split(" ")[0])
        return n if "GB" in text.split("—")[0] else n / 1000.0
    # ProRes against the PNG sequence is the MEASURED 0.876 / 0.311 = 2.8x, so the guard is
    # 2x — tight enough to catch the two constants being swapped, loose enough not to pin
    # a measurement to its third digit.
    assert gb(prores) > gb(h264) and gb(prores) > 2 * gb(png), (h264, prores, png)

    # AGAINST WHICH H.264, THOUGH? The composited estimate follows the encoder this MACHINE
    # resolves, and the two answers are a world apart: VideoToolbox is bitrate-targeted at 0.10
    # bits/px/frame, so ProRes is ~9x it, while the libx264 fallback is CRF-driven and measured at
    # 0.68, which ProRes only beats by a third. The warning has to be worth reading on the machine
    # that lands on the HARDWARE encoder, so that is the comparison pinned here — computed from
    # the module's own bitrate function rather than from whichever encoder this test box has.
    vt_bits = export_video.vt_target_bitrate(1920, 1080, 30.0,
                                             export_video.quality_params("high")[0])
    vt_gb = vt_bits * half_hour / 8 / 1e9
    prores_gb = 1920 * 1080 * 30.0 * export_video.PRORES_4444_BPP * half_hour / 8 / 1e9
    assert prores_gb > 5 * vt_gb, (
        f"ProRes 4444 is {prores_gb:.1f} GB against {vt_gb:.1f} GB for a hardware-encoded H.264 "
        "of the same window — if that ratio ever stops being large the warning stops earning "
        "its place")
    # An All-laps batch is estimated as the WHOLE batch — the number the user is deciding about.
    one = ctl._export_size_hint(90.0, 1080, "high", export_video.ASPECT_SOURCE,
                                ExportController._EXPORT_CONTENT_COMPOSITE, files=1)
    many = ctl._export_size_hint(90.0, 1080, "high", export_video.ASPECT_SOURCE,
                                 ExportController._EXPORT_CONTENT_COMPOSITE, files=20)
    assert gb(many) > 15 * gb(one), (one, many)
    print("ok estimate: an alpha export announces its gigabytes, a batch its total")


# ================================================================ the batch renders behind ONE modal
def test_an_all_laps_batch_renders_every_file_behind_one_dialog():
    """One decision, one modal, N files. A dialog that came down and went back up per lap would be
    N chances to lose the queue and no way to cancel the rest — so the queue drives the SAME
    QProgressDialog from the first file to the last, and the completion card names the count and
    the folder rather than listing forty file names."""
    win = _window(FakeSession())
    specs = [_FakeSpec(f"/tmp/ride_lap{i}.mp4") for i in (1, 2, 3)]
    made, modals = [], []
    orig_worker, orig_exec = export_controller.VideoExportWorker, QDialog.exec
    orig_box_exec = QMessageBox.exec
    seen_labels = []

    def _exec(dlg):
        if not isinstance(dlg, QProgressDialog):
            return orig_exec(dlg)
        # Drive every queued worker to a clean finish from inside the modal loop, exactly where
        # the completion handler runs in production.
        while made:
            worker = made.pop(0)
            worker.progress.emit(10, 100)
            seen_labels.append(dlg.findChild(QLabel).text())
            worker.finished_export.emit(True, "")
        return QDialog.Accepted

    export_controller.VideoExportWorker = lambda session, sp: (made.append(
        _FakeVideoWorker(session, sp)) or made[-1])
    QDialog.exec = _exec
    QMessageBox.exec = lambda box, *_a, **_k: modals.append(box.text()) or 0
    try:
        win.statusBar().clearMessage()
        win.exports._run_video_export(specs)
    finally:
        export_controller.VideoExportWorker = orig_worker
        QDialog.exec = orig_exec
        QMessageBox.exec = orig_box_exec

    assert len(seen_labels) == 3, f"one worker per spec, in order: {seen_labels}"
    assert all("3" in lab for lab in seen_labels), f"the label counts the batch: {seen_labels}"
    assert all(sp.source.cleanups == 1 for sp in specs), "every spec's temp concat list is freed"
    assert len(modals) == 1, f"ONE completion card for the batch, not three: {modals}"
    assert "3 overlay videos" in modals[0], modals[0]
    assert win.statusBar().currentMessage() == "exported 3 overlay videos", \
        win.statusBar().currentMessage()
    win.hide()
    print("ok batch: N files, one modal, one completion card")


def test_cancelling_a_batch_stops_the_queue_rather_than_the_current_file():
    """A Cancel that let the queue carry on to lap 5 after the user pressed it on lap 4 would be a
    cancel button that does not cancel."""
    win = _window(FakeSession())
    specs = [_FakeSpec(f"/tmp/ride_lap{i}.mp4") for i in (1, 2, 3)]
    made, started = [], []
    orig_worker, orig_exec = export_controller.VideoExportWorker, QDialog.exec
    orig_box_exec = QMessageBox.exec

    def _exec(dlg):
        if not isinstance(dlg, QProgressDialog):
            return orig_exec(dlg)
        worker = made[0]
        worker.progress.emit(10, 100)
        dlg.canceled.emit()                 # exactly what pressing the button does
        worker.finished_export.emit(True, "")   # the file in flight still finishes
        return QDialog.Accepted

    def _mk(session, sp):
        made.append(_FakeVideoWorker(session, sp))
        started.append(sp.out_path)
        return made[-1]

    export_controller.VideoExportWorker = _mk
    QDialog.exec = _exec
    QMessageBox.exec = lambda box, *_a, **_k: 0
    try:
        win.exports._run_video_export(specs)
    finally:
        export_controller.VideoExportWorker = orig_worker
        QDialog.exec = orig_exec
        QMessageBox.exec = orig_box_exec

    assert started == ["/tmp/ride_lap1.mp4"], f"the queue kept going after Cancel: {started}"
    assert all(sp.source.cleanups == 1 for sp in specs), "a cancelled batch still frees every spec"
    win.hide()
    print("ok batch: Cancel stops the queue, not just the file in flight")


def test_a_png_sequence_is_asked_for_as_a_folder_and_a_mov_as_a_file():
    """A PNG sequence's output is a DIRECTORY. Asking for it with a save-FILE prompt would hand
    back a file name the renderer then has to reinterpret as a folder; one output, one prompt that
    means it. The ProRes row keeps the file prompt, with a .mov suffix rather than .mp4."""
    ctl = ExportController.__new__(ExportController)
    composite = export_video.OverlayConfig()
    prores = export_video.OverlayConfig(overlay_only=True,
                                        alpha_codec=export_video.ALPHA_PRORES)
    assert ctl._video_out_suffix(SimpleNamespace(config=composite))[0].endswith(".mp4")
    suffix, filt = ctl._video_out_suffix(SimpleNamespace(config=prores))
    assert suffix.endswith(".mov") and "alpha" in filt.lower(), (suffix, filt)
    print("ok picker: a sequence asks for a folder, a .mov for a file")


def _run_all():
    test_a_zero_lap_recording_disables_every_data_export_with_a_reason()
    test_a_zero_lap_export_writes_nothing_and_says_why()
    test_the_mp4_export_obeys_the_same_trust_verdict_as_the_lap_card()
    test_a_finished_video_export_takes_the_modal_down_and_says_so()
    test_the_finished_export_reveals_in_finder_and_reports_both_outcomes()
    test_a_cancelled_video_export_takes_the_modal_down_and_says_so()
    test_a_failed_video_export_takes_the_modal_down_and_names_the_product()
    test_the_real_modal_loop_unwinds_when_a_finished_export_opens_its_box()
    test_every_video_export_dialog_names_the_product_in_its_body()
    test_the_report_is_written_in_the_display_unit_and_the_csv_is_not()
    test_the_report_map_keeps_its_key_and_loses_the_interaction_chrome()
    test_the_report_map_opens_a_key_the_user_collapsed_on_screen()
    test_the_report_map_leaves_the_short_canvas_fallback_alone()
    test_the_report_map_grab_survives_a_map_without_the_contract()
    test_the_report_figure_width_divides_out_the_screens_pixel_ratio()
    test_the_report_export_states_a_layout_width_for_every_figure()
    test_the_options_hint_quantifies_the_size_and_the_work()
    test_the_hint_refreshes_on_the_quality_combo_too()
    test_the_hint_never_promises_a_run_up_the_footage_cannot_give()
    test_the_export_preset_survives_a_new_window()
    test_a_garbage_stored_preset_falls_back_to_the_default()
    test_reveal_in_finder_reports_both_outcomes()
    test_save_as_track_asks_before_it_replaces_a_different_circuit()
    test_save_as_track_names_the_provisional_line_in_its_prompt()
    test_every_picker_row_is_a_value_the_renderer_actually_handles()
    test_the_estimate_knows_a_vertical_frame_is_not_a_landscape_one()
    test_an_alpha_export_is_warned_about_in_gigabytes_before_it_starts()
    test_an_all_laps_batch_renders_every_file_behind_one_dialog()
    test_cancelling_a_batch_stops_the_queue_rather_than_the_current_file()
    test_a_png_sequence_is_asked_for_as_a_folder_and_a_mov_as_a_file()
    print("ALL OK")


if __name__ == "__main__":
    _run_all()
