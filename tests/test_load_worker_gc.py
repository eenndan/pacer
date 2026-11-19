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

What it does not cover, by design: garbage made while a load runs, and the other worker threads
(export, demo resolve) — the comment at the collect says so.
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


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LOAD-WORKER GC TESTS PASSED")
