"""Where the app's diagnostics go: stderr, as always, plus a rotating session log under app-support.

THE HOLE THIS FILLS. `prefs`, `library`, `track_db`, `marks`, `focus`, `session_record`, the
library controller and the window all warn through `logging.getLogger(__name__)`: a migrated
index, a dropped malformed entry, a backup that could not be written, a `tracks.json` newer than
this build. `app.install_excepthook` routes Qt's own C++ warnings and every unhandled exception
into the same place. Until this module, that place was stderr alone, and a `.app` launched from
Finder has its stderr on /dev/null. So in the one build a user runs, every one of those lines was
gone before anyone could ask for it.

TWO DESTINATIONS, DELIBERATELY:

  * **stderr, unchanged.** What a developer running `pixi run studio` already watches, in the
    format `logging.basicConfig` gave it. Anything else would split the dev console in two.
  * **`<app-support>/logs/pacer.log`, rotating at 512 KB with two backups.** The only one of the
    two a shipped build has, and the one a crash dialog can point at. Each record is flushed as it
    is written, so it is on disk before the process that wrote it dies, however it dies.

THE PATH GOES THROUGH THE SAME SEAM AS EVERY STORE. `_app_support_dir` below resolves through
`app_support.resolve()`, so a jailed process (every CTest registration, every `tests/` file,
every `studio/dev/_jail` harness) writes its log into the jail and never into the user's real
directory. `tests/test_app_support_jail.py` resolves this seam in every form a test process takes,
and `tests/test_golden_hermetic.py` holds the jail helper and the golden dump to redirecting it.

BEST EFFORT BY CONSTRUCTION. The file half fails in exactly the conditions the app already has to
survive: an app-support directory it cannot write, a full disk. Logging is diagnostics, and
diagnostics may never be what stops the app starting. A log that cannot be OPENED leaves stderr
alone and says so there, once. A log that stops being WRITABLE later (the disk fills mid-session)
does the same on its first failed write, then stays quiet: the stdlib's own `handleError` would
print a fresh traceback to stderr for every record after it.

BOUNDED BY CONSTRUCTION. Three files of at most `MAX_BYTES` each, 1.5 MB in all. Two stdlib edges
would let a file run past its cap, and both are closed here. `RotatingFileHandler` compares a
record's length in CHARACTERS against a cap in BYTES, and this app's messages carry em dashes
(3 bytes each in UTF-8). And a single record longer than the cap is written whole after the
rollover. `tests/test_session_log.py` measures the total. (A native crash's stack, below, is the
one write the cap does not see: it is the last thing the process does, and the next run's first
record rolls the file over.)

A NATIVE CRASH LANDS HERE TOO. Everything above is `logging`, and a SIGSEGV or SIGABRT inside Qt
or the C++ core never reaches it: the process is gone before any handler runs. The repo has
post-mortemed at least six Qt-lifetime SIGSEGVs, and each left no Python stack anywhere.
`configure` therefore points `faulthandler` at this same file, every thread's stack included, so
the log the crash dialog names ends with the Python stack of the crash that killed the run.
faulthandler writes from a signal handler, to a file descriptor, so it gets a stream of its own
(`_crash_stream`): the handler's stream is closed by every rollover and by a failed write, and a
closed descriptor can be reused by the next `open()` — the stack would land in whatever file got
that number. And every rollover re-arms it onto the new live file, since a stream opened before
it follows the renamed one to `pacer.log.1` and, two rollovers on, to a deleted file. A process
that already enabled faulthandler itself (a developer's `PYTHONFAULTHANDLER`, a test harness that
wants the stack in its own output) is left as it is.

Stdlib only (no Qt, no pacer), so the layering contract holds and any module may import it.
"""
from __future__ import annotations

import faulthandler
import logging
import os
import sys
from logging.handlers import RotatingFileHandler

from . import APP_NAME, __version__, app_support

LOG_DIR_NAME = "logs"
LOG_FILENAME = "pacer.log"
# A breadcrumb trail for a bug report, not an audit log. The cap is the brief's 512 KB x 2 backups:
# three files, 1.5 MB at most on the user's disk, whatever the app does.
MAX_BYTES = 512 * 1024
BACKUP_COUNT = 2
# One record can be at most this many characters in the FILE (stderr gets it whole). A traceback
# from this app runs a few KB; this keeps a pathological one (a repr of a whole array) from
# evicting the whole log, and keeps every record well under MAX_BYTES so the cap above is exact.
MAX_RECORD_CHARS = 32 * 1024

# stderr keeps the exact format `basicConfig` gave it, so the dev console is unchanged. The file
# adds the date and time: it outlives the run, and its records span several.
STDERR_FORMAT = "%(levelname)s %(name)s: %(message)s"
FILE_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Names on the root logger's handlers: how `configure` recognises its own work.
_FILE_HANDLER_NAME = "pacer-session-log"
_STDERR_HANDLER_NAME = "pacer-stderr"

_log = logging.getLogger(__name__)

# `configure` is idempotent: main() calls it once, but a test (or any second entry point) may call
# it again, and without this each call would add a handler and every line would repeat.
_configured = False
_active_path: str | None = None
# The stream faulthandler writes a native crash's stack into (`_arm_crash_stream`), or None while
# this module has not armed it. Held here so it is never collected, and closed only once
# faulthandler has been moved off it.
_crash_stream = None


def _app_support_dir() -> str:
    """The app-support directory the log lives under. Resolved at call time through
    `app_support.resolve`, like every store's seam, so a jailed process logs into its jail."""
    return app_support.resolve()


def log_path() -> str:
    """Where the session log is written (whether or not it could be opened)."""
    return os.path.join(_app_support_dir(), LOG_DIR_NAME, LOG_FILENAME)


def active_log_path() -> str | None:
    """The log file actually being written, or None: not configured yet, not openable, or it
    stopped accepting writes. What a dialog may quote. Naming a file nothing is being written to
    would be worse than naming none."""
    return _active_path


def display_path(path: str) -> str:
    """`path` as a user would type it into Finder's Go to Folder (⇧⌘G): the home directory spelled
    `~`. Also keeps the account name out of a screenshot of the dialog that shows it."""
    home = os.path.expanduser("~")
    if path == home or path.startswith(home + os.sep):
        return "~" + path[len(home):]
    return path


class _FileFormatter(logging.Formatter):
    """The file's format, with a record cut to MAX_RECORD_CHARS (the traceback included)."""

    def format(self, record: logging.LogRecord) -> str:
        text = super().format(record)
        if len(text) > MAX_RECORD_CHARS:
            cut = len(text) - MAX_RECORD_CHARS
            text = f"{text[:MAX_RECORD_CHARS]} … [{cut} more characters not logged]"
        return text


def _arm_crash_stream(path: str) -> bool:
    """Point faulthandler at `path` (every thread's stack on a fatal signal), through an unbuffered
    append stream of its own, and close the one it used before. False, with faulthandler left as
    it was, when the file cannot be opened. Best effort, like the rest of this module: a missing
    crash stack must never be what stops the app starting.

    The order is the point: faulthandler is moved onto the new descriptor BEFORE the old one is
    closed, so at no instant does it hold a descriptor that a concurrent `open()` could be given."""
    global _crash_stream
    try:
        stream = open(path, "ab", buffering=0)
    except OSError:
        return False
    try:
        faulthandler.enable(file=stream, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        stream.close()
        return False
    previous, _crash_stream = _crash_stream, stream
    if previous is not None:
        previous.close()
    return True


class _SessionLogHandler(RotatingFileHandler):
    """A RotatingFileHandler that measures in bytes and gives up quietly."""

    def __init__(self, path: str) -> None:
        super().__init__(path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
        self.failed = False

    def doRollover(self) -> None:
        """The stdlib's rollover, then faulthandler follows the live file (see the module doc):
        its stream is still open on the file this just renamed to `pacer.log.1`."""
        super().doRollover()
        if _crash_stream is not None and self.stream is not None:
            _arm_crash_stream(self.baseFilename)

    def shouldRollover(self, record: logging.LogRecord) -> bool:
        """The stdlib's rule, measured in the bytes the file will actually grow by. It compares
        `tell()` (bytes) with `len(msg)` (characters), so a record full of em dashes could carry a
        file past its cap by up to twice its own length."""
        if os.path.exists(self.baseFilename) and not os.path.isfile(self.baseFilename):
            return False               # never roll anything but a regular file (the stdlib's rule)
        if self.stream is None:
            self.stream = self._open()
        pos = self.stream.tell()
        if not pos:
            return False               # never roll an empty file (the stdlib's gh-116263 rule)
        size = len((self.format(record) + self.terminator).encode(self.encoding or "utf-8"))
        return pos + size > self.maxBytes

    def emit(self, record: logging.LogRecord) -> None:
        if not self.failed:
            super().emit(record)

    def handleError(self, record: logging.LogRecord) -> None:
        """The first write that fails (a full disk, a directory taken away) turns the file off
        and says so on stderr, once. The stdlib's version prints a traceback for EVERY record
        after it, which on a full disk is every record for the rest of the session."""
        global _active_path
        if self.failed:
            return
        self.failed = True
        exc = sys.exc_info()[1]
        _active_path = None
        try:
            self.close()
        except Exception:  # noqa: BLE001 — closing a file that just failed may fail too
            pass
        # NOT `_log.warning(...)`. Since Python 3.13 a logging call made while any logger on this
        # thread is still handling a record is dropped without a trace (`Logger._tls`, a class-wide
        # recursion guard), and this runs inside exactly such a call. Measured: the notice never
        # reached stderr. So the record is built here and handed to the root's OTHER handlers
        # directly, which is where it would have gone.
        notice = _log.makeRecord(
            _log.name, logging.WARNING, __file__, 0,
            "session log: could not write %s (%s) — logging to stderr only from here",
            (self.baseFilename, exc), None)
        for other in logging.getLogger().handlers:
            if other is not self and notice.levelno >= other.level:
                other.handle(notice)


def configure(level: int = logging.INFO) -> str | None:
    """Send every `logging` record to stderr and to the session log. Returns the log's path, or
    None when only stderr could be set up. Idempotent.

    On the ROOT logger, because every module logs through a `getLogger(__name__)` that propagates
    there, as do the Qt message handler and the excepthook in `app`. The stderr handler is added
    only when the root has none (the rule the `basicConfig` call this replaces had), so a harness
    that already captures logging is not given a second copy of every line."""
    global _configured, _active_path
    if _configured:
        return _active_path
    _configured = True
    root = logging.getLogger()
    root.setLevel(level)
    # A windowed build can have no stderr at all (sys.stderr is None): the file is then the only
    # destination, which is the case it exists for.
    if not root.handlers and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.set_name(_STDERR_HANDLER_NAME)
        stream.setFormatter(logging.Formatter(STDERR_FORMAT))
        root.addHandler(stream)

    path = log_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        handler = _SessionLogHandler(path)
    except OSError as exc:
        _log.warning("session log: could not open %s (%s) — logging to stderr only", path, exc)
        return None
    handler.set_name(_FILE_HANDLER_NAME)
    handler.setFormatter(_FileFormatter(FILE_FORMAT))
    root.addHandler(handler)
    _active_path = path
    # A native crash's Python stack goes to the same file (the module doc) — unless the process
    # already enabled faulthandler itself, which is a choice of where that stack goes.
    if _crash_stream is not None or not faulthandler.is_enabled():
        _arm_crash_stream(path)
    # The first line of every run: which build wrote what follows, which is the first thing a bug
    # report needs, and where the file is, for the developer watching stderr.
    _log.info("%s %s (pid %d) — session log at %s", APP_NAME, __version__, os.getpid(),
              display_path(path))
    return path
