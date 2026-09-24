"""THE SESSION LOG (E2): every record the app logs also lands in a file that outlives the process.

WHAT WAS MISSING. `prefs`, `library`, `track_db`, `marks`, `focus`, `session_record`, the library
controller, the central view and the window all warn through `logging`, and `app.install_excepthook`
routes Qt's C++ warnings there too. The only destination was stderr (`logging.basicConfig`), and a
`.app` launched from Finder has its stderr on /dev/null. The unhandled-exception traceback did not
even go through `logging`: the excepthook printed it with `sys.__excepthook__`, straight to that
same stderr. So in the build a user actually runs, a crash left nothing behind to attach to a
report, and neither did any of the warnings leading up to it.

WHAT THIS FILE HOLDS `studio.logsetup` TO:
  * two destinations — stderr, in the format it always had, and `<app-support>/logs/pacer.log`;
  * the path goes through the app-support seam, so a jailed run logs into its jail
    (tests/test_app_support_jail.py covers the real-directory side of that);
  * the REAL startup path (`app.install_excepthook`, exactly what `main()` calls) puts a warning
    from every logger family, a real Qt C++ warning and an unhandled thread exception's traceback
    into the file — and each is on disk before the process dies, because the child here ends by
    SIGKILLing itself, so no flush, `atexit` or `logging.shutdown` ever runs;
  * a NATIVE crash (a real SIGSEGV, raised in C) leaves every thread's Python stack in that same
    file, after two rollovers as well, while a process that enabled faulthandler itself keeps it;
  * best effort: an unwritable log dir, and a file that stops accepting writes mid-session (a real
    kernel refusal, via RLIMIT_FSIZE), each leave the app on stderr and say so exactly once;
  * idempotent: configuring twice does not double a line;
  * bounded: three files of at most 512 KB each, however long or multi-byte the records are;
  * the crash dialog names the file it is writing, and names nothing when there is none.

Run: QT_QPA_PLATFORM=offscreen PYTHONPATH=bindings/pacer python tests/test_session_log.py
"""
import faulthandler
import io
import json
import logging
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
sys.path.insert(0, _REPO)
sys.path.insert(0, _TESTS)

os.environ["PACER_NO_MEDIA"] = "1"
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

_APP = QApplication.instance() or QApplication([])

from studio import APP_NAME, __version__, logsetup  # noqa: E402
from studio import app as studio_app  # noqa: E402

_BINDINGS = os.path.join(_REPO, "bindings", "pacer")


# ------------------------------------------------------------------------------ harness
class _FreshLogging:
    """Run `logsetup.configure` as if for the first time in this process, against `app_support`
    (a directory this test owns), with stderr captured. `configure` is idempotent by design, so
    its one-shot state and the root logger's handlers are put back exactly as found afterwards."""

    def __init__(self, app_support: str):
        self.app_support = app_support
        self.stderr = io.StringIO()

    def __enter__(self):
        root = logging.getLogger()
        self._handlers, self._level = list(root.handlers), root.level
        self._fault_was_on = faulthandler.is_enabled()
        for h in self._handlers:
            root.removeHandler(h)
        self._seam = logsetup._app_support_dir
        logsetup._app_support_dir = lambda: self.app_support
        logsetup._configured, logsetup._active_path = False, None
        self._sys_stderr, sys.stderr = sys.stderr, self.stderr
        return self

    def file_text(self) -> str:
        with open(logsetup.log_path(), encoding="utf-8") as f:
            return f.read()

    def __exit__(self, *_exc):
        sys.stderr = self._sys_stderr
        root = logging.getLogger()
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
        for h in self._handlers:
            root.addHandler(h)
        root.setLevel(self._level)
        logsetup._app_support_dir = self._seam
        logsetup._configured, logsetup._active_path = False, None
        # configure() pointed faulthandler at this block's log. Disarm it, so nothing after the
        # block (a later test, a crash of this process) writes into a file that is gone.
        if logsetup._crash_stream is not None:
            faulthandler.disable()
            logsetup._crash_stream.close()
            logsetup._crash_stream = None
            if self._fault_was_on:
                faulthandler.enable()
        return False


def _child_env(app_support: str, home: str) -> dict:
    """A child jailed to `app_support` explicitly (so the directory outlives it for inspection),
    with a throwaway HOME, so even a child that escaped its jail could only reach a throwaway.
    Without a faulthandler of the developer's own (`PYTHONFAULTHANDLER`, or the dev mode that
    implies it): a process that enabled one keeps it, so the crash tests would not test the app's."""
    env = dict(os.environ, HOME=home, PACER_APP_SUPPORT_DIR=app_support, PACER_NO_MEDIA="1",
               QT_QPA_PLATFORM="offscreen")
    env.pop("PYTHONFAULTHANDLER", None)
    env.pop("PYTHONDEVMODE", None)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (_BINDINGS, env.get("PYTHONPATH")) if p)
    return env


def _real_dir_under(home: str) -> str:
    return os.path.join(home, "Library", "Application Support", "pacer")


# ------------------------------------------------------------------------------ the two halves
def test_configure_opens_the_log_under_the_app_support_seam():
    with tempfile.TemporaryDirectory(prefix="pacer-e2-") as tmp, _FreshLogging(tmp) as fl:
        path = logsetup.configure()
        expected = os.path.join(tmp, "logs", "pacer.log")
        assert path == expected, f"the log opened at {path}, not under the seam ({expected})"
        assert logsetup.active_log_path() == path
        assert os.path.isfile(path), "configure() returned a path but no file exists there"
        assert not os.path.realpath(path).startswith(os.path.realpath(_REPO) + os.sep), path
        first = fl.file_text().splitlines()[0]
        # The first line of a run says which build wrote what follows.
        assert APP_NAME in first and __version__ in first, first
    print("test_configure_opens_the_log_under_the_app_support_seam OK")


def test_a_record_reaches_stderr_unchanged_and_the_file_with_a_timestamp():
    with tempfile.TemporaryDirectory(prefix="pacer-e2-") as tmp, _FreshLogging(tmp) as fl:
        logsetup.configure()
        logging.getLogger("studio.library").warning("library: dropped 3 malformed entries")
        err, text = fl.stderr.getvalue(), fl.file_text()
    # stderr keeps the exact format basicConfig gave the dev console.
    assert "WARNING studio.library: library: dropped 3 malformed entries\n" in err, err
    line = next(ln for ln in text.splitlines() if "dropped 3 malformed" in ln)
    assert line.endswith("WARNING studio.library: library: dropped 3 malformed entries"), line
    assert line[:4].isdigit() and line[4] == "-", f"the file line carries no date: {line!r}"
    print("test_a_record_reaches_stderr_unchanged_and_the_file_with_a_timestamp OK")


def test_configure_is_idempotent():
    with tempfile.TemporaryDirectory(prefix="pacer-e2-") as tmp, _FreshLogging(tmp) as fl:
        first = logsetup.configure()
        handlers = list(logging.getLogger().handlers)
        assert len(handlers) == 2, f"expected stderr + file on the root, got {handlers}"
        again = [logsetup.configure(), logsetup.configure()]
        assert again == [first, first], (first, again)
        assert logging.getLogger().handlers == handlers, (
            f"a second configure() added handlers: {logging.getLogger().handlers}")
        logging.getLogger("studio.prefs").warning("idempotence-token")
        err, text = fl.stderr.getvalue(), fl.file_text()
    assert err.count("idempotence-token") == 1, f"stderr doubled the line:\n{err}"
    assert text.count("idempotence-token") == 1, f"the file doubled the line:\n{text}"
    print("test_configure_is_idempotent OK")


def test_an_existing_handler_is_not_given_a_second_stderr_copy():
    """The rule the `basicConfig` call it replaces had: a root logger that already has a handler
    (a harness capturing logging) gets the file, not another stream."""
    with tempfile.TemporaryDirectory(prefix="pacer-e2-") as tmp, _FreshLogging(tmp):
        mine = logging.StreamHandler(io.StringIO())
        logging.getLogger().addHandler(mine)
        logsetup.configure()
        names = [h.get_name() for h in logging.getLogger().handlers]
    assert names == [None, "pacer-session-log"], names
    print("test_an_existing_handler_is_not_given_a_second_stderr_copy OK")


# ------------------------------------------------------------------------------ the real startup
_STARTUP_CHILD = r"""
import json, logging, os, signal, sys, threading
sys.path.insert(0, {repo!r})
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QApplication
app = QApplication([])
from studio import app as studio_app, focus, library, logsetup, marks, prefs, session_record, track_db
studio_app.install_excepthook()                  # exactly what main() calls, once a QApplication exists
# One real warning per store family: each file was seeded one schema version ahead of this build.
prefs.load(); library.load(); track_db.load(); marks.load(); focus.load(); session_record.load()
# The two families whose warnings need a failing write or a real session; their loggers are what
# is under test here, and the records they emit go through the same root handlers.
logging.getLogger("studio.library_controller").warning("E2-PROBE library_controller family")
logging.getLogger("studio.central_view").warning("E2-PROBE central_view family")
# A failure that used to be a print() in its except handler (RISK-6), through the real handler: a
# demo fetch that fails (a file:// URL to nothing — no network).
from studio import demo
demo._try_download_demo(os.path.join(os.environ["PACER_APP_SUPPORT_DIR"], "demo", "e2.mp4"),
                        url="file:///nonexistent-e2-probe/demo.mp4")
# A REAL Qt C++ warning (Qt's own "QPainter::end: Painter not active" from qpainter.cpp), through
# the qInstallMessageHandler install_excepthook installed.
QPainter().end()
# An unhandled exception on a plain thread: threading.excepthook -> the app's reporter, log-only.
def _boom():
    raise RuntimeError("E2-PROBE thread boom")
t = threading.Thread(target=_boom, name="e2-probe-thread"); t.start(); t.join()
print("E2-CHILD " + json.dumps({{"active": logsetup.active_log_path()}}), flush=True)
# No flush, no atexit, no logging.shutdown: whatever is in the file now was on disk already.
os.kill(os.getpid(), signal.SIGKILL)
"""

_SEEDS = {
    "prefs.json": {"version": 99},
    "library.json": {"version": 99, "entries": []},
    "tracks.json": {"version": 99, "tracks": []},
    "marks.json": {"version": 99, "recordings": {}},
    "focus.json": {"version": 99, "lists": []},
    "session_records.json": {"version": 99, "records": {}},
}

# (logger name, a fragment of that family's own message) — every family the app logs through.
_FAMILIES = [
    ("studio.prefs", "prefs: file is version 99, newer than this build's"),
    ("studio.library", "library: index is version 99, newer than this build's"),
    ("studio.track_db", "is schema version 99, not this build's"),
    ("studio.marks", "marks: store is version 99, newer than this build's"),
    ("studio.focus", "focus: store is version 99, newer than this build's"),
    ("studio.session_record", "session records: store is version 99, newer than this build's"),
    ("studio.library_controller", "E2-PROBE library_controller family"),
    ("studio.central_view", "E2-PROBE central_view family"),
    ("studio.demo", "demo download failed"),
]


def test_the_real_startup_path_puts_every_family_in_the_file_before_the_process_dies():
    home = tempfile.mkdtemp(prefix="pacer-e2-home-")
    jail = tempfile.mkdtemp(prefix="pacer-e2-jail-")
    try:
        for name, body in _SEEDS.items():
            with open(os.path.join(jail, name), "w", encoding="utf-8") as f:
                json.dump(body, f)
        proc = subprocess.run([sys.executable, "-c", _STARTUP_CHILD.format(repo=_REPO)],
                              env=_child_env(jail, home), capture_output=True, text=True,
                              timeout=300)
        assert proc.returncode == -9, (
            f"the child was meant to die by SIGKILL, exited {proc.returncode}\n"
            f"stdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-4000:]}")
        report = next((json.loads(ln[len("E2-CHILD "):]) for ln in proc.stdout.splitlines()
                       if ln.startswith("E2-CHILD ")), None)
        assert report is not None, f"the child reported nothing:\n{proc.stderr[-4000:]}"
        log = os.path.join(jail, "logs", "pacer.log")
        assert report["active"] == log, f"the app logged to {report['active']}, not {log}"
        assert os.path.isfile(log), f"no session log in the jail: {os.listdir(jail)}"
        with open(log, encoding="utf-8") as f:
            text = f.read()
        missing = [n for n, frag in _FAMILIES if f" {n}: " not in text or frag not in text]
        assert not missing, f"these logger families never reached the file: {missing}\n{text}"
        # ...and stderr still gets them: two destinations, not a move.
        not_on_stderr = [n for n, frag in _FAMILIES if frag not in proc.stderr]
        assert not not_on_stderr, f"stderr lost {not_on_stderr}:\n{proc.stderr[-4000:]}"
        # A real Qt C++ warning, through the existing message handler.
        assert "WARNING studio.app: Qt: QPainter::end: Painter not active" in text, (
            f"the Qt C++ warning never reached the file:\n{text}")
        # The crash itself: the traceback, not only the one-line notice.
        assert "ERROR studio.app: unhandled exception" in text, (
            f"the unhandled exception never reached the file:\n{text}")
        assert "Traceback (most recent call last)" in text, f"no traceback in the file:\n{text}"
        assert "RuntimeError: E2-PROBE thread boom" in text, text
        assert "error report not shown (off the GUI thread (e2-probe-thread))" in text, text
        # The throwaway HOME's real app-support directory was never created, logs/ least of all.
        assert not os.path.exists(_real_dir_under(home)), (
            f"the child wrote under its HOME's real app-support dir: {os.listdir(home)}")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(jail, ignore_errors=True)
    print(f"test_the_real_startup_path_puts_every_family_in_the_file_before_the_process_dies OK "
          f"({len(_FAMILIES)} families + Qt + a thread traceback, read after SIGKILL)")


# ------------------------------------------------------------------------------ a native crash
# A SIGSEGV inside Qt or the C++ core never reaches `logging`: the process is gone first. So each
# child below crashes for real — `ctypes.string_at(0)` reads address 0 from C — and the stack has
# to be in the file anyway. What the children change is only the ENDING: the fatal signals are
# pointed at libc's `_exit` before faulthandler is armed, so once faulthandler has written the
# stack and called the handler it replaced, the child exits with the signal number as its status
# instead of being killed by it. A process killed by SIGSEGV leaves a macOS crash report in
# ~/Library/Logs/DiagnosticReports on every run of the suite; one that exits does not (measured).
_EXIT_ON_FAULT = r"""
import ctypes, signal
_libc = ctypes.CDLL(None)
_libc.signal.argtypes = [ctypes.c_int, ctypes.c_void_p]
_libc.signal.restype = ctypes.c_void_p
for _sig in (signal.SIGSEGV, signal.SIGBUS):
    _libc.signal(_sig, ctypes.cast(_libc._exit, ctypes.c_void_p).value)
"""
_FAULT_CODES = (signal.SIGSEGV, signal.SIGBUS)   # the exit status `_exit(signum)` leaves
_FATAL = "Fatal Python error: "

_NATIVE_CRASH_CHILD = _EXIT_ON_FAULT + r"""
import json, sys, threading
sys.path.insert(0, {repo!r})
from PySide6.QtWidgets import QApplication
app = QApplication([])
from studio import app as studio_app, logsetup
studio_app.install_excepthook()                  # exactly what main() calls, once a QApplication exists
parked = threading.Event()
def e2_probe_parked_thread():
    parked.wait()
threading.Thread(target=e2_probe_parked_thread, name="e2-probe-parked", daemon=True).start()
def e2_probe_native_crash():
    ctypes.string_at(0)                          # strlen(NULL), in C: a real SIGSEGV
def e2_probe_crashing_caller():
    e2_probe_native_crash()
print("E2-CHILD " + json.dumps({{"active": logsetup.active_log_path()}}), flush=True)
e2_probe_crashing_caller()
print("E2-CHILD survived the crash", flush=True)
"""


def _run_crash_child(code: str, jail: str, home: str) -> subprocess.CompletedProcess:
    proc = subprocess.run([sys.executable, "-c", code.format(repo=_REPO)],
                          env=_child_env(jail, home), capture_output=True, text=True, timeout=300)
    assert proc.returncode in _FAULT_CODES, (
        f"the child was meant to crash (exit {_FAULT_CODES} through _exit), exited "
        f"{proc.returncode}\nstdout: {proc.stdout[-2000:]}\nstderr: {proc.stderr[-4000:]}")
    assert "survived the crash" not in proc.stdout, proc.stdout
    return proc


def _read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def test_a_native_crash_leaves_every_threads_python_stack_in_the_session_log():
    home = tempfile.mkdtemp(prefix="pacer-e2-home-")
    jail = tempfile.mkdtemp(prefix="pacer-e2-jail-")
    try:
        proc = _run_crash_child(_NATIVE_CRASH_CHILD, jail, home)
        log = os.path.join(jail, "logs", "pacer.log")
        report = next((json.loads(ln[len("E2-CHILD "):]) for ln in proc.stdout.splitlines()
                       if ln.startswith("E2-CHILD {")), None)
        assert report is not None and report["active"] == log, (report, proc.stderr[-4000:])
        text = _read(log)
        assert _FATAL in text, (
            f"a native crash left no Python stack in the session log — faulthandler is not "
            f"pointed at it:\n{text[-3000:]}")
        crash = text[text.index(_FATAL):]
        # The crashing thread, innermost frame first, then its caller: the stack of THE crash.
        inner, outer = crash.find("in e2_probe_native_crash"), crash.find("in e2_probe_crashing_caller")
        assert -1 < inner < outer, f"the crashing thread's frames are missing or out of order:\n{crash}"
        # all_threads=True: the thread that was merely parked is in it too.
        assert "in e2_probe_parked_thread" in crash, f"only the crashing thread was dumped:\n{crash}"
        # In the SAME file the dialog names, after this run's first line, not in a file of its own.
        assert text.index(f"{APP_NAME} {__version__}") < text.index(_FATAL), text[-3000:]
        assert sorted(os.listdir(os.path.join(jail, "logs"))) == ["pacer.log"], (
            os.listdir(os.path.join(jail, "logs")))
        assert not os.path.exists(_real_dir_under(home)), (
            f"the child wrote under its HOME's real app-support dir: {os.listdir(home)}")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(jail, ignore_errors=True)
    print(f"test_a_native_crash_leaves_every_threads_python_stack_in_the_session_log OK "
          f"(exit {proc.returncode}; both threads' stacks in pacer.log)")


# A stream opened on the live file follows it through the rename a rollover does: after one it is
# on pacer.log.1, after two on pacer.log.2 — and at the third, on a deleted file. The stack of a
# crash late in a long session has to land where the dialog points, in the live file.
_ROTATED_CRASH_CHILD = _EXIT_ON_FAULT + r"""
import logging, sys
sys.path.insert(0, {repo!r})
from studio import logsetup
path = logsetup.configure()
for h in logging.getLogger().handlers:           # 1.4 MB of filler: the file only, not the pipe
    if h.get_name() == "pacer-stderr":
        h.setLevel(logging.ERROR)
log = logging.getLogger("studio.library")
for i in range(2 * logsetup.MAX_BYTES // 1000 + 300):
    log.warning("E2-PROBE filler %05d %s", i, "x" * 1000)
def e2_probe_crash_after_rollover():
    ctypes.string_at(0)
e2_probe_crash_after_rollover()
print("E2-CHILD survived the crash", flush=True)
"""


def test_after_a_rollover_the_crash_stack_still_lands_in_the_live_log():
    home = tempfile.mkdtemp(prefix="pacer-e2-home-")
    jail = tempfile.mkdtemp(prefix="pacer-e2-jail-")
    try:
        _run_crash_child(_ROTATED_CRASH_CHILD, jail, home)
        logs = os.path.join(jail, "logs")
        files = sorted(os.listdir(logs))
        assert files == ["pacer.log", "pacer.log.1", "pacer.log.2"], (
            f"the child did not roll the log over twice ({files}), so this tests nothing")
        live, older = _read(os.path.join(logs, "pacer.log")), [
            f for f in files[1:] if _FATAL in _read(os.path.join(logs, f))]
        assert not older, (
            f"the crash stack went to {older}: faulthandler stayed on the file a rollover renamed")
        assert _FATAL in live and "in e2_probe_crash_after_rollover" in live, (
            f"no crash stack in the live log:\n{live[-3000:]}")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(jail, ignore_errors=True)
    print("test_after_a_rollover_the_crash_stack_still_lands_in_the_live_log OK "
          "(two rollovers, the stack in pacer.log)")


# A process that enabled faulthandler BEFORE the log was configured chose where that stack goes: a
# developer's PYTHONFAULTHANDLER, or a test file whose own `faulthandler.enable()` puts a crash in
# the ctest output (test_studio_features, test_load_lifecycle — and the former calls
# install_excepthook). configure() must not move it into a jailed file nobody reads.
_OWN_FAULTHANDLER_CHILD = _EXIT_ON_FAULT + r"""
import faulthandler, json, sys
sys.path.insert(0, {repo!r})
faulthandler.enable()                            # to stderr, as a harness does
from studio import logsetup
print("E2-CHILD " + json.dumps({{"path": logsetup.configure()}}), flush=True)
def e2_probe_harness_crash():
    ctypes.string_at(0)
e2_probe_harness_crash()
print("E2-CHILD survived the crash", flush=True)
"""


def test_a_faulthandler_the_process_already_enabled_keeps_its_destination():
    home = tempfile.mkdtemp(prefix="pacer-e2-home-")
    jail = tempfile.mkdtemp(prefix="pacer-e2-jail-")
    try:
        proc = _run_crash_child(_OWN_FAULTHANDLER_CHILD, jail, home)
        log = os.path.join(jail, "logs", "pacer.log")
        assert os.path.isfile(log), "the log itself was not opened"
        assert _FATAL in proc.stderr and "in e2_probe_harness_crash" in proc.stderr, (
            f"the process's own faulthandler lost the stack:\n{proc.stderr[-3000:]}")
        assert _FATAL not in _read(log), "configure() took over a faulthandler it did not enable"
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(jail, ignore_errors=True)
    print("test_a_faulthandler_the_process_already_enabled_keeps_its_destination OK")


# ------------------------------------------------------------------------------ best effort
def test_an_unwritable_log_dir_leaves_the_app_on_stderr_and_says_so_once():
    """The file half fails where the rest of the app already has to survive. It must degrade,
    never raise, and say so exactly once on the half that still works — then a real StudioWindow
    must still build, and the crash dialog must not name a file that was never opened."""
    tmp = tempfile.mkdtemp(prefix="pacer-e2-ro-")
    logs = os.path.join(tmp, "logs")
    os.mkdir(logs)
    os.chmod(logs, stat.S_IRUSR | stat.S_IXUSR)            # r-x: pacer.log cannot be created
    try:
        with _FreshLogging(tmp) as fl:
            first = logsetup.configure()
            second = logsetup.configure()
            logging.getLogger("studio.track_db").warning("still-reaches-stderr")
            win = studio_app.StudioWindow([])              # the app starts: the welcome state
            _APP.processEvents()
            built = win.centralWidget() is not None
            box = _crash_box()
            win.close()
            err = fl.stderr.getvalue()
    finally:
        os.chmod(logs, stat.S_IRWXU)
        shutil.rmtree(tmp, ignore_errors=True)
    assert first is None and second is None, (first, second)
    assert logsetup.active_log_path() is None
    assert err.count("session log: could not open") == 1, f"not said exactly once:\n{err}"
    assert "still-reaches-stderr" in err, err
    assert not os.path.exists(os.path.join(logs, "pacer.log"))
    assert built, "the window did not build with an unwritable log dir"
    assert studio_app.CRASH_LOG_LINE not in box.text(), box.text()
    print("test_an_unwritable_log_dir_leaves_the_app_on_stderr_and_says_so_once OK")


# A file that opened and then stops taking writes: the kernel refuses them (RLIMIT_FSIZE with
# SIGXFSZ ignored makes a write past the limit fail with EFBIG, the way a full disk fails one with
# ENOSPC). The stdlib's handleError would print a "--- Logging error ---" traceback for EVERY
# record from there on.
_WRITE_FAILURE_CHILD = r"""
import json, logging, os, resource, signal, sys
sys.path.insert(0, {repo!r})
from studio import logsetup
path = logsetup.configure()
signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
limit = os.path.getsize(path) + 400
resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
log = logging.getLogger("studio.library")
for i in range(40):
    log.warning("E2-PROBE line %d %s", i, "x" * 60)
print("E2-CHILD " + json.dumps({{"path": path, "active": logsetup.active_log_path(),
                                "size": os.path.getsize(path), "limit": limit}}), flush=True)
"""


def test_a_log_that_stops_accepting_writes_degrades_once_not_every_line():
    home = tempfile.mkdtemp(prefix="pacer-e2-home-")
    jail = tempfile.mkdtemp(prefix="pacer-e2-jail-")
    try:
        proc = subprocess.run([sys.executable, "-c", _WRITE_FAILURE_CHILD.format(repo=_REPO)],
                              env=_child_env(jail, home), capture_output=True, text=True,
                              timeout=120)
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(jail, ignore_errors=True)
    assert proc.returncode == 0, f"exit {proc.returncode}\n{proc.stderr[-4000:]}"
    report = next(json.loads(ln[len("E2-CHILD "):]) for ln in proc.stdout.splitlines()
                  if ln.startswith("E2-CHILD "))
    err = proc.stderr
    assert report["path"] is not None and report["size"] <= report["limit"], report
    assert err.count("session log: could not write") == 1, f"not said exactly once:\n{err[-4000:]}"
    assert "--- Logging error ---" not in err, (
        f"the stdlib's per-record error dump is back:\n{err[-4000:]}")
    assert "E2-PROBE line 39" in err, "the records after the failure no longer reach stderr"
    assert report["active"] is None, f"the dialog would still name a dead log: {report}"
    print("test_a_log_that_stops_accepting_writes_degrades_once_not_every_line OK")


# ------------------------------------------------------------------------------ bounded
def test_the_log_never_grows_past_three_files_of_512_kb():
    """Rotation is the bound: 512 KB x (1 + 2 backups) = 1.5 MB. Two stdlib edges would each let a
    file past its cap, and both are driven here — records full of em dashes (3 bytes each in
    UTF-8; the stdlib's rollover test counts characters) and one record four times the cap."""
    cap = logsetup.MAX_BYTES * (1 + logsetup.BACKUP_COUNT)
    worst: dict = {"total": 0, "file": 0, "at": None}
    with tempfile.TemporaryDirectory(prefix="pacer-e2-") as tmp, _FreshLogging(tmp):
        logsetup.configure()
        logs = os.path.join(tmp, "logs")
        log = logging.getLogger("studio.marks")

        def _measure(at):
            # After EVERY record, not just at the end: a file that overshoots rotates out of sight
            # a few hundred records later, and "never" is the claim.
            sizes = {f: os.path.getsize(os.path.join(logs, f)) for f in os.listdir(logs)}
            if sum(sizes.values()) > worst["total"]:
                worst.update(total=sum(sizes.values()), at=at)
            worst["file"] = max(worst["file"], *sizes.values())
            return sizes

        dashes = "—" * 700
        for i in range(2500):
            log.warning("rotation %05d %s", i, dashes if i % 3 else "ascii " * 80)
            _measure(i)
            if i == 1200:
                log.warning("huge %s", "z" * (4 * logsetup.MAX_BYTES))
                _measure("huge")
        log.warning("the newest record")
        sizes = _measure("end")
        files = sorted(sizes)
        with open(os.path.join(logs, "pacer.log"), encoding="utf-8") as f:
            newest = f.read()
    assert files == ["pacer.log", "pacer.log.1", "pacer.log.2"], (
        f"rotation left {files} — the bound needs it to have rotated, and no further than 2")
    assert worst["file"] <= logsetup.MAX_BYTES, (
        f"a log file reached {worst['file']} bytes, past its {logsetup.MAX_BYTES}-byte cap")
    assert worst["total"] <= cap, (
        f"the log reached {worst['total']} bytes on disk (after record {worst['at']}) > {cap}")
    assert "the newest record" in newest, "the live file lost the newest record"
    print(f"test_the_log_never_grows_past_three_files_of_512_kb OK "
          f"(at most {worst['total']} bytes on disk, one file at most {worst['file']}; cap {cap})")


# ------------------------------------------------------------------------------ where the user sees it
def _crash_box():
    try:
        raise ValueError("E2 dialog probe")
    except ValueError:
        et, ev, tb = sys.exc_info()
    saved = QMessageBox.exec
    QMessageBox.exec = lambda self, *a, **k: 0
    try:
        return studio_app._show_error_report(et, ev, tb)
    finally:
        QMessageBox.exec = saved


def test_the_crash_dialog_names_the_log_it_is_writing_and_the_traceback_is_in_it():
    with tempfile.TemporaryDirectory(prefix="pacer-e2-") as tmp, _FreshLogging(tmp) as fl:
        path = logsetup.configure()
        box = _crash_box()
        body = box.text()
        box.close()
        # The excepthook, as a slot failure reaches it: the traceback goes to the file.
        orig_show, reported = studio_app._show_error_report, set(studio_app._REPORTED)
        studio_app._show_error_report = lambda *a: None
        try:
            try:
                raise KeyError("E2-traceback-token")
            except KeyError:
                studio_app._excepthook(*sys.exc_info())
        finally:
            studio_app._show_error_report = orig_show
            studio_app._REPORTED.clear()
            studio_app._REPORTED.update(reported)
        text = fl.file_text()
    assert studio_app.CRASH_LOG_LINE in body, f"the crash dialog does not point at the log: {body!r}"
    assert logsetup.display_path(path) in body, f"the dialog does not name {path}: {body!r}"
    assert "ERROR studio.app: unhandled exception" in text, (
        f"the excepthook's traceback never reached the file:\n{text}")
    assert "Traceback (most recent call last)" in text and "E2-traceback-token" in text, text
    # And with no log being written, it names nothing.
    assert logsetup.active_log_path() is None
    assert studio_app.CRASH_LOG_LINE not in _crash_box().text()
    print("test_the_crash_dialog_names_the_log_it_is_writing_and_the_traceback_is_in_it OK")


def test_the_dialog_spells_the_home_directory_as_finder_does():
    home = os.path.expanduser("~")
    real = os.path.join(home, "Library", "Application Support", "pacer", "logs", "pacer.log")
    # (Built with os.path.join: a "~/…" literal in a test file reads as hard-coded footage to
    # test_footage_checks.)
    assert logsetup.display_path(real) == os.path.join(
        "~", "Library", "Application Support", "pacer", "logs", "pacer.log")
    assert logsetup.display_path("/private/tmp/x/pacer.log") == "/private/tmp/x/pacer.log"
    assert logsetup.display_path(home + "other/pacer.log") == home + "other/pacer.log"
    print("test_the_dialog_spells_the_home_directory_as_finder_does OK")


def _run_all():
    tests = [
        test_configure_opens_the_log_under_the_app_support_seam,
        test_a_record_reaches_stderr_unchanged_and_the_file_with_a_timestamp,
        test_configure_is_idempotent,
        test_an_existing_handler_is_not_given_a_second_stderr_copy,
        test_the_real_startup_path_puts_every_family_in_the_file_before_the_process_dies,
        test_a_native_crash_leaves_every_threads_python_stack_in_the_session_log,
        test_after_a_rollover_the_crash_stack_still_lands_in_the_live_log,
        test_a_faulthandler_the_process_already_enabled_keeps_its_destination,
        test_an_unwritable_log_dir_leaves_the_app_on_stderr_and_says_so_once,
        test_a_log_that_stops_accepting_writes_degrades_once_not_every_line,
        test_the_log_never_grows_past_three_files_of_512_kb,
        test_the_crash_dialog_names_the_log_it_is_writing_and_the_traceback_is_in_it,
        test_the_dialog_spells_the_home_directory_as_finder_does,
    ]
    defined = sorted(k for k, v in globals().items() if k.startswith("test_") and callable(v))
    assert sorted(t.__name__ for t in tests) == defined, "a test_ function is not in _run_all"
    for t in tests:
        t()
    print(f"\nALL {len(tests)} SESSION-LOG TESTS PASSED")


if __name__ == "__main__":
    _run_all()
