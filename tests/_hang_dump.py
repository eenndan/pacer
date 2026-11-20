"""Hang dumps: a test about to hit its CTest timeout first prints where it is stuck.

THE PROBLEM (2026-11-20, #649). A test that hangs in CI is killed at its timeout, and the log said
only `***Timeout 480.11 sec`: CTest runs each file as a script, its stdout is block-buffered into a
pipe, and nothing printed a stack. `test_export_pass` timed out once in CI, never locally, and
nothing said which test function or which line it was in.

WHAT IT DOES. Shortly before the timeout, it writes to stderr (fd 2, so into the CTest log ahead of
`***Timeout`) a banner naming the running test function and its line, then the stack of EVERY
thread, then flushes the stdout the test had buffered. It does not exit or signal: the timeout
proceeds exactly as before, with the same exit status. A test that finishes in time prints nothing.

HOW IT IS ARMED. tests/CMakeLists.txt gives every Python registration `PACER_HANG_DUMP` and puts
tests/_site first on PYTHONPATH; the interpreter imports tests/_site/sitecustomize.py at start-up,
which calls `arm_from_environment()` here before the test file's first line runs. So no test file
carries any of it. `PACER_HANG_DUMP` is the registration's own TIMEOUT property in seconds (footage
checks, soaks), or `ctest`: its ceiling is the running ctest's `--timeout`, which CTest does not
export, so it is read off the parent process's command line, the last `--timeout` winning as
it does in ctest. THE RULE when that finds none (`pixi run golden`, a bare `ctest -R x`): there is
no timeout to beat, so nothing is armed. The variable is removed once read, so only the
registration's own process arms; the Pythons it spawns do not.
By hand: `pixi run ctest --test-dir build/Release -R '^test_x$' --timeout 60` arms at ~54 s. Under
pytest, tests/conftest.py calls the same function: `PACER_HANG_DUMP=60 pixi run python -m pytest -s
…` (`-s`, or pytest captures the dump into the test's report).

WHEN. `deadlines()`: the banner and dump at the timeout less a margin of 10 %, at most 30 s (450 s
of 480, 3570 s of 3600), which covers the interpreter's start-up under load and the dump itself.

TWO WATCHDOGS, ONE DUMP. A Python timer thread writes the banner (it can read the frames, so it can
name the test), calls `faulthandler.dump_traceback(all_threads=True)` and cancels the second, a
`faulthandler.dump_traceback_later` armed at half the margin. That one is faulthandler's own C
thread: it needs no GIL, so if the hang holds the GIL in a C call and the Python thread never runs,
the stacks are still printed, under faulthandler's own `Timeout (0:07:45)!` line and without the
banner: the test's name is then the `test_…` frame in the main thread's stack. (A test file that
arms faulthandler's timer itself replaces that backup with its own.)

REJECTED. `PYTHONFAULTHANDLER=1` / `-X faulthandler` print only on a fatal signal, never on a
hang. A SIGABRT before the timeout would dump, but turn a Timeout into a crash and change the exit
status. Unbuffering stdout (`-u`) slows every test to show the last line only. A RUN line per test
needs ~180 runner loops edited (each file has its own) or a profile hook on every call; the frame
walk at dump time names the test for free.
"""
from __future__ import annotations

import faulthandler
import os
import re
import sys
import threading
import time

ENV = "PACER_HANG_DUMP"
CTEST = "ctest"  # the value meaning: the ceiling is the parent ctest's --timeout
THREAD_NAME = "pacer-hang-dump"
BANNER = "==== HANG DUMP"
MARGIN_FRACTION = 0.10
MAX_MARGIN_S = 30.0

_TIMEOUT_ARG = re.compile(r"(?:^|\s)--timeout(?:=|\s+)(\d+(?:\.\d*)?)(?=\s|$)")
_armed = False


def deadlines(timeout: float) -> tuple[float, float]:
    """(banner + dump, faulthandler's backup), in seconds after arming, for a `timeout` ceiling."""
    margin = min(MAX_MARGIN_S, timeout * MARGIN_FRACTION)
    return timeout - margin, timeout - margin / 2


def ctest_timeout(command_line: str) -> float | None:
    """The `--timeout` a ctest command line gives (the last one, as ctest reads it), or None.
    ctest treats 0 as no timeout."""
    found = _TIMEOUT_ARG.findall(command_line or "")
    secs = float(found[-1]) if found else 0.0
    return secs if secs > 0 else None


def _parent_command_line() -> str:
    """The parent process's argv, space-joined, or "" when it cannot be read. macOS: the
    KERN_PROCARGS2 sysctl (no subprocess, and allowed where a sandbox refuses to exec /bin/ps);
    Linux: /proc."""
    ppid = os.getppid()
    try:
        if sys.platform.startswith("linux"):
            with open(f"/proc/{ppid}/cmdline", "rb") as f:
                return f.read().replace(b"\0", b" ").decode(errors="replace").strip()
        if sys.platform != "darwin":
            return ""
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        ctl_kern, kern_argmax, kern_procargs2 = 1, 8, 49  # <sys/sysctl.h>
        argmax, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
        if libc.sysctl((ctypes.c_int * 2)(ctl_kern, kern_argmax), 2, ctypes.byref(argmax),
                       ctypes.byref(size), None, 0) != 0:
            return ""
        buf, size = ctypes.create_string_buffer(argmax.value), ctypes.c_size_t(argmax.value)
        if libc.sysctl((ctypes.c_int * 3)(ctl_kern, kern_procargs2, ppid), 3, buf,
                       ctypes.byref(size), None, 0) != 0:
            return ""
        # Layout: int argc, the executable path, NUL padding, then argc NUL-terminated args.
        raw = buf.raw[:size.value]
        argc = int.from_bytes(raw[:4], sys.byteorder)
        args = raw[4:].partition(b"\0")[2].lstrip(b"\0").split(b"\0")[:argc]
        return " ".join(a.decode(errors="replace") for a in args)
    except Exception:  # noqa: BLE001  - no command line means no timeout found: nothing armed
        return ""


def running_test(frames: dict | None = None) -> str:
    """The innermost `test_…` function on the main thread's stack (else any thread's), with the
    line it is at — the test that is running, whatever helper or C call it is inside."""
    frames = sys._current_frames() if frames is None else frames
    main = threading.main_thread().ident
    names = {t.ident: t.name for t in threading.enumerate()}
    for ident in [main] + [i for i in frames if i != main]:
        f = frames.get(ident)
        while f is not None:
            if f.f_code.co_name.startswith("test_"):
                where = "" if ident == main else f" on thread {names.get(ident, ident)!r}"
                return f"{f.f_code.co_name} ({f.f_code.co_filename}:{f.f_lineno}){where}"
            f = f.f_back
    return "none: no test_… function on any thread's stack (module level, or the runner)"


def _report(timeout: float, first: float, last: float, source: str, fd: int, t0: float) -> None:
    if time.monotonic() - t0 >= last:
        return  # the GIL was held past the backup's deadline: faulthandler already dumped
    try:
        os.write(fd, (
            f"\n{BANNER}: still running {first:.1f} s after start-up, {timeout - first:.1f} s before "
            f"its {timeout:g} s timeout ({source})\n"
            f"{BANNER}: running test: {running_test()}\n"
            f"{BANNER}: every thread's stack follows, most recent call first; the timeout then "
            "proceeds as before\n").encode())
        faulthandler.dump_traceback(file=fd, all_threads=True)
        faulthandler.cancel_dump_traceback_later()
        os.write(fd, f"{BANNER}: end\n".encode())
    finally:
        try:  # last: if the test holds stdout's lock this blocks, and the dump is already out
            if sys.stdout is not None:
                sys.stdout.flush()
        except Exception:  # noqa: BLE001  - a dump must never raise into the test
            pass


def arm(timeout: float, source: str) -> bool:
    """Arm both watchdogs for a process whose ceiling is `timeout` seconds. Once per process."""
    global _armed
    if _armed or not timeout > 0 or sys.__stderr__ is None:
        return False
    fd = sys.__stderr__.fileno()
    first, last = deadlines(timeout)
    faulthandler.dump_traceback_later(last, repeat=False, file=fd, exit=False)
    timer = threading.Timer(first, _report,
                            args=(timeout, first, last, source, fd, time.monotonic()))
    timer.name = THREAD_NAME
    timer.daemon = True
    timer.start()
    _armed = True
    return True


def arm_from_environment() -> float | None:
    """Arm from `PACER_HANG_DUMP` (see the module docstring) and remove it from the environment.
    Returns the timeout armed against, or None when nothing was armed."""
    value = os.environ.pop(ENV, "").strip()
    if not value:
        return None
    if value == CTEST:
        timeout, source = ctest_timeout(_parent_command_line()), "ctest --timeout"
    else:
        try:
            timeout, source = float(value), f"{ENV}={value}"
        except ValueError:
            return None
    return timeout if timeout is not None and arm(timeout, source) else None
