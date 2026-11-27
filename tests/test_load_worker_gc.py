"""Cyclic garbage pending when a load starts is freed on the GUI thread, never on the load worker's.

Python's cyclic GC runs on whichever thread trips its threshold. Qt widgets held only by reference
cycles (LapTable's own closures hold it in some) crashed or deadlocked in ~QTableView when that
thread was the session-load worker's: test_studio_features' reentrant-load test did, 3 runs of 3,
on the branch that added Corners ▸ Quickest passes, with 441 cyclic Qt/studio objects from earlier
windows pending (main has the same hazard under a forced collect on the worker). The mitigation is
one `gc.collect()` on the GUI thread in `StudioWindow._start_load_worker`. This pins it,
deterministically and in well under a second: with automatic collection off, plant a cycle whose
finalizer records the thread that frees it, start a load through the real method with a worker
whose `run()` FORCES a full collection (the worst case: the threshold tripping at once), and
require the cycle to have died on the GUI thread. Without the collect it dies on the worker.

Every QThread worker the app starts goes through that door (`workers.start_worker`): the window's
load, the reference load, Library ▸ Update's rows, the demo fetch and each file of a video export,
one test each. What it does not cover, by design: garbage made while a worker runs, and the plain
threads an export starts — the docstring at the collect says so.
"""
import gc
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThread, Signal  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

_APP = QApplication.instance() or QApplication([])

import studio.app as studio_app  # noqa: E402


class _CollectingWorker(QThread):
    """Stands in for SessionLoadWorker: same signals, and a run() that is ONLY a full collection,
    recording what it freed — the moment the GC threshold trips on the worker."""

    loaded = Signal(int, list, object)
    failed = Signal(int, list, object)

    def __init__(self, token, paths):
        super().__init__()
        self.collected = None

    def cancel(self):
        pass

    def run(self):
        self.collected = gc.collect()


class _Cyclic:
    """A reference cycle whose death records the thread that ran it."""

    died_on: list = []

    def __init__(self):
        self.me = self

    def __del__(self):
        _Cyclic.died_on.append(threading.current_thread() is threading.main_thread())


def test_garbage_pending_at_a_load_dies_on_the_gui_thread():
    saved_worker = studio_app.SessionLoadWorker
    studio_app.SessionLoadWorker = _CollectingWorker
    win = studio_app.StudioWindow([])
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()          # nothing collects behind the test's back: only the two collects below
    try:
        _Cyclic.died_on.clear()
        _Cyclic()         # unreachable at once, freeable only by the cyclic GC
        win._start_load_worker(1, [])
        worker = win._load_worker
        deadline = time.time() + 10
        while not worker.isFinished() and time.time() < deadline:
            _APP.processEvents()
            time.sleep(0.005)
        assert worker.isFinished(), "the stand-in worker never finished"
        assert _Cyclic.died_on == [True], (
            "a cycle pending when the load started was freed on the load worker's thread "
            f"(died_on={_Cyclic.died_on}): the GUI-thread gc.collect() in _start_load_worker is gone")
    finally:
        if was_enabled:
            gc.enable()
        studio_app.SessionLoadWorker = saved_worker
        _APP.processEvents()
        win.deleteLater()
        _APP.processEvents()
    print("test_garbage_pending_at_a_load_dies_on_the_gui_thread OK")


def test_garbage_pending_at_a_reference_load_dies_on_the_gui_thread():
    """The reference recording's load (Use as reference, Compare with your previous PB) is a whole
    `Session.load` on its own worker, so it goes through the same door (`workers.start_worker`)."""
    saved_worker = studio_app.SessionLoadWorker
    studio_app.SessionLoadWorker = _CollectingWorker
    win = studio_app.StudioWindow([])
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        _Cyclic.died_on.clear()
        _Cyclic()
        win._start_reference_worker(1, [])
        worker = win._ref_load_worker
        deadline = time.time() + 10
        while not worker.isFinished() and time.time() < deadline:
            _APP.processEvents()
            time.sleep(0.005)
        assert worker.isFinished(), "the stand-in reference worker never finished"
        assert _Cyclic.died_on == [True], (
            "a cycle pending when the reference load started was freed on its worker's thread "
            f"(died_on={_Cyclic.died_on}): _start_reference_worker no longer starts through "
            "start_worker")
    finally:
        if was_enabled:
            gc.enable()
        studio_app.SessionLoadWorker = saved_worker
        _APP.processEvents()
        win.deleteLater()
        _APP.processEvents()
    print("test_garbage_pending_at_a_reference_load_dies_on_the_gui_thread OK")


class _CollectingRemeasure(QThread):
    """Stands in for RemeasureWorker (Library ▸ Update): its signal, and a run() that is ONLY a
    full collection."""

    measured = Signal(int, str, object, str)

    def __init__(self, token, fingerprint, paths):
        super().__init__()
        self._token = token

    def cancel(self):
        pass

    def run(self):
        gc.collect()


def test_garbage_pending_at_a_library_update_row_dies_on_the_gui_thread():
    """Library ▸ Update starts one loading worker per row — 40 rows, 40 threads — so it goes
    through the same door as the window's loads (`workers.start_worker`) and gets the same collect."""
    import tempfile
    from types import SimpleNamespace

    from studio import library_controller
    saved = library_controller.RemeasureWorker
    library_controller.RemeasureWorker = _CollectingRemeasure
    was_enabled = gc.isenabled()
    with tempfile.TemporaryDirectory() as d:
        clip = os.path.join(d, "GX010068.MP4")
        open(clip, "wb").close()
        updater = library_controller.IdealUpdater(SimpleNamespace(_load_workers=set()))
        gc.collect()
        gc.disable()
        try:
            _Cyclic.died_on.clear()
            _Cyclic()
            updater.start([{"fingerprint": "GX0068", "paths": [clip], "lap_count": 1}])
            deadline = time.time() + 10
            while updater.running() and time.time() < deadline:
                _APP.processEvents()
                time.sleep(0.005)
            assert not updater.running(), "the stand-in update never finished"
            assert _Cyclic.died_on == [True], (
                "a cycle pending when an update row started was freed on its worker's thread "
                f"(died_on={_Cyclic.died_on}): IdealUpdater no longer starts through start_worker")
        finally:
            if was_enabled:
                gc.enable()
            library_controller.RemeasureWorker = saved
            _APP.processEvents()
    print("test_garbage_pending_at_a_library_update_row_dies_on_the_gui_thread OK")


class _CollectingExport(QThread):
    """Stands in for VideoExportWorker: its two signals, and a run() that is ONLY a full collection
    — a render runs Python on its own thread for minutes, so the threshold trips there too."""

    progress = Signal(int, int)
    finished_export = Signal(bool, str)

    def __init__(self, session, spec, make_renderer=None):
        super().__init__()

    def cancel(self):
        pass

    def run(self):
        gc.collect()


def test_garbage_pending_at_a_video_export_dies_on_the_gui_thread():
    """Each file of a video export starts its worker through the same door as the loads
    (`workers.start_worker`): `start_load`'s docstring said the export "collects for itself", but
    the only collect near it is the HTML report's, on the GUI thread, which starts no worker."""
    from PySide6.QtWidgets import QDialog, QProgressDialog

    from studio import export_controller

    class _Spec:
        out_path = "/tmp/pacer-unwritten/lap.mp4"
        source = type("S", (), {"cleanup": lambda self: None})()

    made = []
    saved = (export_controller.VideoExportWorker, QDialog.exec)

    def _exec(dlg):
        if not isinstance(dlg, QProgressDialog):
            return saved[1](dlg)
        deadline = time.time() + 10
        while not made[0].isFinished() and time.time() < deadline:
            _APP.processEvents()
            time.sleep(0.005)
        made[0].finished_export.emit(False, "cancelled")     # the dialog's end, as a Cancel
        return QDialog.Accepted

    export_controller.VideoExportWorker = lambda *a: made.append(_CollectingExport(*a)) or made[-1]
    QDialog.exec = _exec
    win = studio_app.StudioWindow([])
    if not hasattr(win, "session"):
        win.session = None
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        _Cyclic.died_on.clear()
        _Cyclic()
        win.exports._run_video_export([_Spec()])
        assert made and made[0].isFinished(), "the stand-in export worker never ran"
        assert _Cyclic.died_on == [True], (
            "a cycle pending when a video export started was freed on its worker's thread "
            f"(died_on={_Cyclic.died_on}): _run_video_export no longer starts through "
            "start_worker")
    finally:
        if was_enabled:
            gc.enable()
        export_controller.VideoExportWorker, QDialog.exec = saved
        _APP.processEvents()
        win.deleteLater()
        _APP.processEvents()
    print("test_garbage_pending_at_a_video_export_dies_on_the_gui_thread OK")


class _CollectingDemo(QThread):
    """Stands in for DemoResolveWorker: its signal, and a run() that is ONLY a full collection."""

    resolved = Signal(int, object)

    def __init__(self, token):
        super().__init__()

    def run(self):
        gc.collect()


def test_garbage_pending_at_a_demo_fetch_dies_on_the_gui_thread():
    """The demo fetch (resolve, and download on a first open) is a worker too: same door."""
    saved = studio_app.DemoResolveWorker
    studio_app.DemoResolveWorker = _CollectingDemo
    win = studio_app.StudioWindow([])
    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        _Cyclic.died_on.clear()
        _Cyclic()
        win._open_demo()
        worker = win._demo_worker
        deadline = time.time() + 10
        while not worker.isFinished() and time.time() < deadline:
            _APP.processEvents()
            time.sleep(0.005)
        assert worker.isFinished(), "the stand-in demo worker never finished"
        assert _Cyclic.died_on == [True], (
            "a cycle pending when the demo fetch started was freed on its worker's thread "
            f"(died_on={_Cyclic.died_on}): _open_demo no longer starts through start_worker")
    finally:
        if was_enabled:
            gc.enable()
        studio_app.DemoResolveWorker = saved
        _APP.processEvents()
        win.deleteLater()
        _APP.processEvents()
    print("test_garbage_pending_at_a_demo_fetch_dies_on_the_gui_thread OK")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LOAD-WORKER GC TESTS PASSED")
