"""Where pacer keeps its state on disk — and the rule that keeps every test and tool out of it.

Seven stores live in one directory, ``~/Library/Application Support/pacer``: the library index,
prefs, saved tracks, marks, the focus list, session records and the demo cache — and so does the
session log (``logsetup``, ``logs/pacer.log``), which the app writes on every start. Each of those
modules keeps its own ``_app_support_dir()`` seam (tests still patch it, and ``tests/test_golden_hermetic``
finds every one by AST); every one of those seams resolves through ``resolve()`` here.

WHY THIS MODULE EXISTS (H8). On 2026-09-17 a routine ``ctest`` run wrote a ``stadium`` row from the
two-lap synthetic fixture into the owner's real ``library.json``. The test that wrote it patched no
seam; its write had been dead code only because a test double used to raise first, and a correct fix
to that double made it live. Every jail in the repo was an in-process attribute patch — opt-in per
file, and invisible to any child process — so nothing stopped it. The rule is now environmental:

  * ``PACER_APP_SUPPORT_DIR`` set: every seam resolves there. It is inherited by child processes, and
    ``studio/dev/_jail.py`` exports it whenever it jails a harness.
  * ``PACER_APP_SUPPORT_JAIL`` set, OR the process's entry script is a file in this checkout's
    ``tests/`` directory: the first resolution makes a fresh temp dir, exports it as
    ``PACER_APP_SUPPORT_DIR`` (so children share it) and removes it when the process exits.
    ``tests/CMakeLists.txt`` sets the flag on every registration, which jails a whole ctest process
    tree from its root; the ``tests/`` rule covers a test file an agent runs by hand. A test process
    also exports the flag as soon as this module is imported, so a ``python -c`` child it spawns is
    jailed even if the parent never resolved a path itself.
  * Neither: the real directory. This is the app's own path, and the only one a user's run takes —
    ``python -m studio`` enters through ``studio/__main__.py`` and the frozen .app through its bundle
    executable, neither of which is in ``tests/``. ``tests/test_app_support_jail.py`` pins both sides.

Stdlib only: no pacer, no Qt, and no studio import, so every store can import it.
"""
from __future__ import annotations

import atexit
import os
import shutil
import sys
import tempfile
import threading

DIR_ENV = "PACER_APP_SUPPORT_DIR"
JAIL_ENV = "PACER_APP_SUPPORT_JAIL"

_APP_DIR_NAME = "pacer"
_TESTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests")
_lock = threading.Lock()


def real_dir() -> str:
    """The user's own app-support directory (``~/Library/Application Support/pacer``)."""
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", _APP_DIR_NAME)


def _entry_script_is_a_repo_test() -> bool:
    """True iff this process was started as ``python <this checkout>/tests/<file>.py``."""
    argv0 = sys.argv[0] if sys.argv else ""
    if not argv0 or argv0 in ("-c", "-m") or not os.path.isfile(argv0):
        return False
    return os.path.dirname(os.path.realpath(argv0)) == os.path.realpath(_TESTS_DIR)


def resolve() -> str:
    """The directory every store reads and writes, by the rule in the module doc."""
    explicit = os.environ.get(DIR_ENV)
    if explicit:
        return explicit
    if not (os.environ.get(JAIL_ENV) or _entry_script_is_a_repo_test()):
        return real_dir()
    with _lock:
        explicit = os.environ.get(DIR_ENV)       # another thread got here first
        if explicit:
            return explicit
        jail = tempfile.mkdtemp(prefix="pacer-app-support-jail-")
        os.environ[DIR_ENV] = jail
        # Only the process that MADE the jail removes it; a child that inherited it never does.
        atexit.register(shutil.rmtree, jail, True)
        return jail


if _entry_script_is_a_repo_test():
    os.environ.setdefault(JAIL_ENV, "1")
