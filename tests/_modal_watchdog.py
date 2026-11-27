"""Modal watchdog: a dialog nobody closes names itself and is rejected, so its test fails fast.

THE PROBLEM (the 2026-11 campaign lost hours to it, several times). Offscreen, `QMessageBox.exec()`
and every other `QDialog.exec()` block in a nested event loop until something closes the dialog.
A test that did not expect the dialog (a new warning on a path it drives, a refusal it did not
stub) therefore waits out its CTest timeout, 480 s, and the log says only `***Timeout`. The hang
dump (tests/_hang_dump.py) names the test and the `exec()` line 30 s before that, but not WHAT the
dialog said, which is the one thing that tells you why it opened.

WHAT IT DOES. A low-frequency timer on the GUI thread (every `POLL_MS`) looks at
`QApplication.activeModalWidget()`. When the same `QDialog` has been the active modal for
`THRESHOLD_S` seconds (20; `PACER_MODAL_WATCHDOG_S` overrides it for a slow machine, 0 turns the
watchdog off), it writes to stderr a banner naming the running test, the dialog's class, title and
text (a QMessageBox's text and informative text; any other dialog's label texts), then every
thread's Python stack, and REJECTS the dialog: `exec()` returns, the test carries on down its
cancelled branch, and the process exits 1 however that ends (an `atexit` handler), so the
registration FAILS in ~20 s with the dialog's words in its log instead of timing out at 480 s.
Once one has tripped, any later modal is rejected on the next poll, so a retry loop cannot rebuild
the hang. A `QProgressDialog` is never judged: it is the app's own long-running export, closed by
the work it shows, not a question waiting for a human.

HOW IT IS ARMED. In every Python CTest registration: tests/_hang_dump.py `arm_from_environment()`
(run at start-up by tests/_site/sitecustomize.py) calls `start()`, which waits for the first
`import PySide6.QtWidgets` (a `sys.meta_path` hook, so a test without widgets gets no thread) and
then for the `QApplication`, and arms the timer on that application's own thread. By hand,
`tests/_qtapp.themed_app()` arms it directly, so the files that take their app from there are
covered under a bare `python tests/test_x.py` too. `PACER_MODAL_WATCHDOG_DEBUG=1` prints, at exit,
that it was armed and the longest any modal dialog stayed open, for checking a test's margin.

WHY 20 S. Every test here that drives a modal closes it from a `QTimer` or a stub within a few
hundred ms; the test files that do so were each run with the debug line on when this landed (the
pull request lists them). A legitimate dialog would have to sit open, untouched, for 20 s.
"""
from __future__ import annotations

import atexit
import faulthandler
import os
import sys
import threading
import time

ENV = "PACER_MODAL_WATCHDOG_S"
DEBUG_ENV = "PACER_MODAL_WATCHDOG_DEBUG"
THRESHOLD_S = 20.0
POLL_MS = 1000
BANNER = "==== MODAL WATCHDOG"
THREAD_NAME = "pacer-modal-watchdog"
# Bound at import, before any test runs: a test that stubs `time.monotonic` with a scripted clock
# must not have its values consumed by the watchdog.
_clock = time.monotonic

_state: dict = {"timer": None, "seen": None, "since": 0.0, "longest": (0.0, ""), "tripped": [],
                "exit_hook": False, "started": False}


def threshold() -> float:
    """Seconds a modal dialog may stay open untouched: `PACER_MODAL_WATCHDOG_S`, else 20. 0 = off."""
    raw = os.environ.get(ENV, "").strip()
    try:
        return max(0.0, float(raw)) if raw else THRESHOLD_S
    except ValueError:
        return THRESHOLD_S


def _write(text: str) -> None:
    try:
        os.write(2, text.encode(errors="replace"))
    except OSError:
        pass


def describe(w) -> str:
    """The dialog in a few lines: class, title, and what it says."""
    from PySide6.QtWidgets import QLabel, QMessageBox

    lines = [f"class: {type(w).__module__}.{type(w).__qualname__}",
             f"title: {w.windowTitle()!r}"]
    if isinstance(w, QMessageBox):
        lines += [f"text: {w.text()!r}", f"informativeText: {w.informativeText()!r}"]
    else:
        labels = [lbl.text() for lbl in w.findChildren(QLabel) if lbl.text()]
        lines.append(f"labels: {labels[:6]!r}")
    return "\n".join(f"{BANNER}: {ln}" for ln in lines)


def _running_test() -> str:
    try:
        hd = sys.modules.get("_hang_dump")
        if hd is None:
            import _hang_dump as hd  # importing arms nothing: `running_test` is a frame walk
        return hd.running_test()
    except Exception as exc:  # noqa: BLE001 - a name is a courtesy; the report goes out without it
        return f"unknown ({exc!r})"


def _poll() -> None:
    """One look at the active modal, on the GUI thread. Never raises into the event loop."""
    try:
        import shiboken6
        from PySide6.QtWidgets import QApplication, QDialog, QProgressDialog

        w = QApplication.activeModalWidget()
        if not isinstance(w, QDialog) or isinstance(w, QProgressDialog):
            _state["seen"] = None
            return
        # Keyed by its C++ address, never by a reference: holding the wrapper would keep a
        # Python-owned dialog alive past the test's own gc (tests/test_dialog_census.py counts).
        key = shiboken6.getCppPointer(w)[0]
        now = _clock()
        if key != _state["seen"]:
            _state["seen"], _state["since"] = key, now
        open_s = now - _state["since"]
        if open_s > _state["longest"][0]:
            _state["longest"] = (open_s, type(w).__name__)
        limit = threshold()
        if _state["tripped"] and key != _state["tripped"][-1]:
            _write(f"{BANNER}: after the first, another modal is rejected at once:\n{describe(w)}\n")
        elif not (limit > 0 and open_s >= limit):
            return
        else:
            _write(f"\n{BANNER}: a modal dialog has been open {open_s:.0f} s and nothing closed it "
                   f"(limit {limit:g} s, {ENV})\n{BANNER}: running test: {_running_test()}\n"
                   f"{describe(w)}\n{BANNER}: every thread's stack follows, most recent call "
                   "first; the dialog is then rejected and this process exits 1\n")
            faulthandler.dump_traceback(file=2, all_threads=True)
            _write(f"{BANNER}: end\n")
        _state["tripped"].append(key)
        _state["seen"] = None
        w.reject()
        del w
    except Exception as exc:  # noqa: BLE001
        _write(f"{BANNER}: poll failed: {exc!r}\n")


def _at_exit() -> None:
    if os.environ.get(DEBUG_ENV) == "1":
        secs, cls = _state["longest"]
        _write(f"{BANNER}: armed={_state['timer'] is not None}; longest modal dialog open "
               f"{secs:.1f} s{f' ({cls})' if cls else ''}; limit {threshold():g} s\n")
    if _state["tripped"]:
        _write(f"{BANNER}: {len(_state['tripped'])} dialog(s) rejected: exit status 1\n")
        for stream in (sys.stdout, sys.stderr):
            try:
                if stream is not None:
                    stream.flush()
            except Exception:  # noqa: BLE001
                pass
        os._exit(1)


def _hook_exit() -> None:
    # Registered as early as possible: atexit runs last-in first-out, so this one runs after the
    # handlers registered later (the jail's cleanup, PySide's), and its os._exit skips none of them.
    if not _state["exit_hook"]:
        _state["exit_hook"] = True
        atexit.register(_at_exit)


def arm(app=None) -> bool:
    """Arm the timer on `app` (default: the running QApplication). Call on the GUI thread.
    Idempotent; False when off, already armed, or there is no QApplication."""
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    app = app or QApplication.instance()
    if _state["timer"] is not None or not isinstance(app, QApplication) or threshold() <= 0:
        return False
    _hook_exit()
    timer = QTimer(app)
    timer.setObjectName(THREAD_NAME)
    timer.setInterval(POLL_MS)
    timer.timeout.connect(_poll)
    timer.start()
    _state["timer"] = timer
    return True


def _wait_for_app() -> None:
    """Thread body: wait for the QApplication, then hand `arm` to its thread and end."""
    try:
        while True:
            # The hook fires before the module is loaded: until it is, it has no QApplication.
            qapp = getattr(sys.modules.get("PySide6.QtWidgets"), "QApplication", None)
            app = qapp.instance() if qapp is not None else None
            if qapp is not None and isinstance(app, qapp):
                from PySide6.QtCore import QTimer

                QTimer.singleShot(0, app, arm)  # runs on the app's thread, at its next event
                return
            if _state["timer"] is not None:
                return
            time.sleep(0.25)
    except Exception as exc:  # noqa: BLE001
        _write(f"{BANNER}: not armed: {exc!r}\n")


class _OnQtWidgets:
    """A `sys.meta_path` entry that only notices the first `import PySide6.QtWidgets`."""

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PySide6.QtWidgets":
            try:
                sys.meta_path.remove(self)
            except ValueError:
                pass
            threading.Thread(target=_wait_for_app, name=THREAD_NAME, daemon=True).start()
        return None  # the real finders load it


def start() -> None:
    """Arm for this whole process (a CTest registration): once Qt widgets are imported and the
    QApplication exists. Nothing happens in a process that never imports QtWidgets."""
    if _state["started"] or threshold() <= 0:
        return
    _state["started"] = True
    _hook_exit()
    if "PySide6.QtWidgets" in sys.modules:
        threading.Thread(target=_wait_for_app, name=THREAD_NAME, daemon=True).start()
    else:
        sys.meta_path.insert(0, _OnQtWidgets())
