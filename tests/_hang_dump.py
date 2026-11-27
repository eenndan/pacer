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
export, so it is read off the command line of the nearest `ctest` among the process's ancestors
(the parent, under `pixi run test`), the last `--timeout` winning as it does in ctest. THE RULE
when that finds none (`pixi run golden`, a bare `ctest -R x`): there is no timeout to beat, so
nothing is armed. `PACER_HANG_DUMP_DEBUG=1` says on stderr what was armed, or why nothing was.
The variable is removed once read, so only the registration's own process arms; the Pythons it
spawns do not. The same call starts tests/_modal_watchdog.py (a modal dialog left open for 20 s
names itself and fails the test), whatever the timeout.
By hand: `pixi run ctest --test-dir build/Release -R '^test_x$' --timeout 60` arms at ~54 s. Under
pytest, tests/conftest.py calls the same function: `PACER_HANG_DUMP=60 pixi run python -m pytest -s
…` (`-s`, or pytest captures the dump into the test's report).

WHEN. `deadlines()`: the banner and dump at the timeout less a margin of 10 %, at least 1 s and at
most 30 s (450 s of 480, 3570 s of 3600), which covers the interpreter's start-up under load and
the dump itself.

TWO WATCHDOGS, ONE DUMP. A Python timer thread writes the banner (it can read the frames, so it can
name the test), calls `faulthandler.dump_traceback(all_threads=True)` and cancels the second, a
`faulthandler.dump_traceback_later` three quarters into the margin (472.5 s of 480): a timer
thread a loaded machine wakes late still dumps first. That one is faulthandler's own C
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
DEBUG_ENV = "PACER_HANG_DUMP_DEBUG"  # =1: say on stderr what was armed, or why nothing was
MARGIN_FRACTION = 0.10
MIN_MARGIN_S = 1.0
MAX_MARGIN_S = 30.0
MAX_ANCESTORS = 8

_TIMEOUT_ARG = re.compile(r"(?:^|\s)--timeout(?:=|\s+)(\d+(?:\.\d*)?)(?=\s|$)")
#: (ceiling in seconds, where it came from) once armed, else None: what this process will beat.
armed: tuple[float, str] | None = None


def deadlines(timeout: float) -> tuple[float, float]:
    """(banner + dump, faulthandler's backup), in seconds after arming, for a `timeout` ceiling.
    The backup sits three quarters of the way into the margin, so a Python watchdog that a loaded
    machine wakes late still has a quarter of the margin to dump first (CI, 2026-11-20: with the
    backup at half of a 0.22 s margin, a late wake left the dump to faulthandler)."""
    margin = min(MAX_MARGIN_S, max(MIN_MARGIN_S, timeout * MARGIN_FRACTION), timeout / 2)
    return timeout - margin, timeout - margin / 4


def ctest_timeout(command_line: str) -> float | None:
    """The `--timeout` a ctest command line gives (the last one, as ctest reads it), or None.
    ctest treats 0 as no timeout."""
    found = _TIMEOUT_ARG.findall(command_line or "")
    secs = float(found[-1]) if found else 0.0
    return secs if secs > 0 else None


def _darwin_argv(libc, pid: int) -> list[str] | None:
    import ctypes
    ctl_kern, kern_argmax, kern_procargs2 = 1, 8, 49  # <sys/sysctl.h>
    argmax, size = ctypes.c_int(0), ctypes.c_size_t(ctypes.sizeof(ctypes.c_int))
    if libc.sysctl((ctypes.c_int * 2)(ctl_kern, kern_argmax), 2, ctypes.byref(argmax),
                   ctypes.byref(size), None, 0) != 0:
        return None
    buf, size = ctypes.create_string_buffer(argmax.value), ctypes.c_size_t(argmax.value)
    if libc.sysctl((ctypes.c_int * 3)(ctl_kern, kern_procargs2, pid), 3, buf,
                   ctypes.byref(size), None, 0) != 0:
        return None
    # Layout: int argc, the executable path, NUL padding, then argc NUL-terminated args.
    raw = buf.raw[:size.value]
    argc = int.from_bytes(raw[:4], sys.byteorder)
    args = raw[4:].partition(b"\0")[2].lstrip(b"\0").split(b"\0")[:argc]
    return [a.decode(errors="replace") for a in args]


def _darwin_ppid(libc, pid: int) -> int | None:
    import ctypes
    proc_pidtbsdinfo, bsdinfo_size = 3, 136  # <sys/proc_info.h>: struct proc_bsdinfo
    buf = ctypes.create_string_buffer(bsdinfo_size)
    if libc.proc_pidinfo(pid, proc_pidtbsdinfo, ctypes.c_uint64(0), buf, bsdinfo_size) \
            != bsdinfo_size:
        return None
    return int.from_bytes(buf.raw[16:20], sys.byteorder)  # pbi_ppid, the fifth uint32


def ctest_ancestor() -> tuple[int, str] | None:
    """(pid, command line) of the nearest ancestor process that is a `ctest`, or None. Normally
    the parent (ctest starts each test itself, under `caffeinate -si` and `pixi run` above it),
    but a wrapper between them would put it further up. macOS: the KERN_PROCARGS2 sysctl and
    proc_pidinfo (no subprocess: a sandbox that refuses to exec /bin/ps allows these); Linux:
    /proc."""
    try:
        if sys.platform.startswith("linux"):
            def argv(pid):
                with open(f"/proc/{pid}/cmdline", "rb") as f:
                    return [a.decode(errors="replace") for a in f.read().split(b"\0") if a]

            def ppid(pid):
                with open(f"/proc/{pid}/stat", encoding="utf-8") as f:
                    return int(f.read().rpartition(")")[2].split()[1])
        elif sys.platform == "darwin":
            import ctypes
            import ctypes.util
            libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)

            def argv(pid):
                return _darwin_argv(libc, pid)

            def ppid(pid):
                return _darwin_ppid(libc, pid)
        else:
            return None
        pid = os.getppid()
        for _ in range(MAX_ANCESTORS):
            if pid is None or pid <= 1:
                return None
            args = argv(pid)
            if args and os.path.basename(args[0]) == CTEST:
                return pid, " ".join(args)
            pid = ppid(pid)
    except Exception:  # noqa: BLE001  - no ctest found means no timeout found: nothing armed
        return None
    return None


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
    global armed
    if armed is not None or not timeout > 0 or sys.__stderr__ is None:
        return False
    fd = sys.__stderr__.fileno()
    first, last = deadlines(timeout)
    faulthandler.dump_traceback_later(last, repeat=False, file=fd, exit=False)
    timer = threading.Timer(first, _report,
                            args=(timeout, first, last, source, fd, time.monotonic()))
    timer.name = THREAD_NAME
    timer.daemon = True
    timer.start()
    armed = (timeout, source)
    return True


def _start_modal_watchdog() -> None:
    """The same registrations also get tests/_modal_watchdog.py, whatever their timeout: a dialog
    left open names itself and is rejected long before any dump is due. Loaded by path, as
    sitecustomize loads this file, and under its own name, so `tests/_qtapp.py` finds it armed."""
    try:
        mod = sys.modules.get("_modal_watchdog")
        if mod is None:
            import importlib.util
            path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_modal_watchdog.py")
            spec = importlib.util.spec_from_file_location("_modal_watchdog", path)
            if spec is None or spec.loader is None:
                return
            mod = importlib.util.module_from_spec(spec)
            sys.modules["_modal_watchdog"] = mod
            spec.loader.exec_module(mod)
        mod.start()
    except Exception as exc:  # noqa: BLE001  - a failure to arm costs the watchdog, never the run
        os.write(2, f"tests/_hang_dump.py: modal watchdog not armed: {exc!r}\n".encode())


def arm_from_environment() -> float | None:
    """Arm from `PACER_HANG_DUMP` (see the module docstring) and remove it from the environment.
    Returns the timeout armed against, or None when nothing was armed. With
    PACER_HANG_DUMP_DEBUG=1 it says on stderr which, and why."""
    value = os.environ.pop(ENV, "").strip()
    if not value:
        return None
    _start_modal_watchdog()
    timeout: float | None = None
    if value == CTEST:
        found = ctest_ancestor()
        timeout = ctest_timeout(found[1]) if found else None
        source = (f"ctest --timeout, pid {found[0]}" if timeout else
                  f"no --timeout on ctest pid {found[0]}" if found else "no ctest ancestor")
    else:
        try:
            timeout, source = float(value), f"{ENV}={value}"
        except ValueError:
            source = f"{ENV}={value!r} is neither seconds nor {CTEST!r}"
    ok = timeout is not None and arm(timeout, source)
    if os.environ.get(DEBUG_ENV) == "1":
        said = (f"armed against {timeout:g} s ({source}): dump at {deadlines(timeout)[0]:g} s"
                if ok and timeout is not None else f"not armed ({source})")
        os.write(2, f"{BANNER} {said}\n".encode())
    return timeout if ok else None
