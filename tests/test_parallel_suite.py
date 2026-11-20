"""THE SUITE RUNS IN PARALLEL BY DEFAULT, AND A RUN CAN STILL BE MADE SERIAL.

`pixi run test` and `pixi run test-fast` ran one test at a time until 2026-09-23 — one core of an
8-core machine, while the fast loop grew from 29 s to ~420 s. They now take their parallelism from
`CTEST_PARALLEL_LEVEL` in the task's `env` (pyproject.toml has the measurements and the reason for
the level). This file pins the two halves of that choice:

  * THE DEFAULT IS PARALLEL. A task that loses the variable silently goes back to one core, and
    nothing else would notice: every test still passes, just about three times slower.
  * THE COMMAND LINE CARRIES NO `-j`. ctest 4 rejects a second `-j` outright ("'-j' given invalid
    value '4 -j2'"), so a `-j` baked into the task would turn `pixi run test-fast -j1` — the way to
    rule a parallel interaction in or out — into an error. The variable is only a default, and one
    appended `-jN` overrides it (measured: CTEST_PARALLEL_LEVEL=4 plus `-j1` runs one at a time).
    Which tasks are ctest runs is read off their commands, so a new one cannot dodge the rule.

THE LISTS IN THE TASKS DO NOT ROT (PROCESS-6, 2026-09-29). `test-core`, `test-ciworld` and
`golden` select registrations by name with `-R`, and `test-fast` drops two with `-E`. A misspelt or
renamed name selects nothing, and ctest still exits 0 — so `test-core` could quietly lose a suite
of its mutant cover, and `-E` could quietly stop excluding. Every such name must be a
tests/<name>.py (the same fail-don't-skip rule as the exceptions table in tests/CMakeLists.txt).
And `test-ciworld`, CI's software-decode world run locally, lists exactly the registrations that
build a real QMediaPlayer — every test file that pops PACER_NO_MEDIA outside its footage checks —
so a new real-player test cannot be left out of it, and a file that stops needing the decoder
cannot linger in it.

What makes parallel runs safe is pinned elsewhere: each test process resolves its own app-support
jail (tests/test_app_support_jail.py), no test builds a fixed path in the shared $TMPDIR
(tests/test_temp_isolation.py), and the real-footage checks share RESOURCE_LOCK footage, so they
still run one at a time (tests/test_footage_checks.py).

A HANG SAYS WHERE IT IS (2026-11-20). A test killed at its timeout used to leave only `***Timeout
480.11 sec` in the log (#649 lost a CI timeout that way). tests/_hang_dump.py, armed in every
Python registration by tests/_site/sitecustomize.py, prints the running test and every thread's
stack shortly before the timeout and changes nothing else. The checks below run children armed
with a 2.2 s ceiling against each hang shape — a Python sleep, a lock deadlock, a Qt event loop
nobody quits, and a C call holding the GIL (faulthandler's own thread then dumps) — and a child that
finishes in time, which must print nothing extra; and they hold every Python registration to the
arming, and the ceiling to ctest's own `--timeout`.

Stdlib (tomllib + ast) but for two things: the hang children import QtCore, and one check lists
this build's registrations through ctest (PACER_CTEST_COMMAND, as test_app_support_jail does).

Run: python tests/test_parallel_suite.py
"""
import ast
import glob
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.join(_REPO, "tests")
sys.path.insert(0, _TESTS)

import _hang_dump  # noqa: E402

#: The ctest tasks that default to parallel. `test-footage` is deliberately not one: its checks
#: share one RESOURCE_LOCK, so a level there would change nothing but how its command reads.
PARALLEL_TASKS = ("test", "test-fast", "test-core", "test-ciworld")
#: The task that runs CI's software-decode world over every real-player registration.
CIWORLD_TASK = "test-ciworld"

_LEVEL_ENV = "CTEST_PARALLEL_LEVEL"
_J_FLAG = re.compile(r"(^|\s)(-j\S*|--parallel\S*)(\s|$)")
#: ctest's name-selecting flags; `-L`/`-LE` select labels, not names.
_NAME_FLAGS = ("-R", "-E", "--tests-regex", "--exclude-regex")
_PLAIN_NAME = re.compile(r"test_\w+")
#: A textual superset of the idiom _pops_no_media recognises, to skip parsing the other files.
_MAYBE_POPS = re.compile(r"(pop\(\s*|del\s+os\.environ\[\s*)[\"']PACER_NO_MEDIA[\"']")


def _tasks() -> dict:
    with open(os.path.join(_REPO, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)["tool"]["pixi"]["tasks"]


def _argv(task) -> list[str]:
    """A task's command as the shell will split it (pixi takes a string, a list or a table)."""
    cmd = task if isinstance(task, (str, list)) else task.get("cmd", "")
    return list(cmd) if isinstance(cmd, list) else shlex.split(cmd)


def _is_ctest(argv: list[str]) -> bool:
    """A ctest run is a command with a `ctest` TOKEN — behind `caffeinate -si` or not. A task that
    only names a test file (`python tests/…`) is not one."""
    return any(os.path.basename(tok) == "ctest" for tok in argv)


def _ctest_tasks(tasks: dict) -> dict[str, list[str]]:
    return {name: _argv(t) for name, t in tasks.items() if _is_ctest(_argv(t))}


def _selected_names(argv: list[str]) -> list[tuple[str, str]]:
    """(flag, name) for every name a `-R`/`-E` regex selects. The regex must be a plain alternation
    of names — `^(a|b)$`, `a|b` or a bare `a` — or it comes back whole, and fails the name check:
    a pattern cannot be checked against the files."""
    out = []
    for flag, regex in zip(argv, argv[1:], strict=False):
        if flag in _NAME_FLAGS:
            body = regex.removeprefix("^").removesuffix("$")
            if body.startswith("(") and body.endswith(")"):
                body = body[1:-1]
            out += [(flag, part) for part in body.split("|")]
    return out


def _unreal_names(argv: list[str]) -> list[str]:
    return [f"{flag} {name!r}" for flag, name in _selected_names(argv)
            if not (_PLAIN_NAME.fullmatch(name)
                    and os.path.isfile(os.path.join(_TESTS, f"{name}.py")))]


def _pops_no_media(node: ast.AST) -> bool:
    """`os.environ.pop("PACER_NO_MEDIA"…)` or `del os.environ["PACER_NO_MEDIA"]` — the idiom a test
    uses to get the real media triplet back where its registration or a sibling module set the
    inert one (studio/player_pane.py)."""
    if isinstance(node, ast.Call):
        f = node.func
        return (isinstance(f, ast.Attribute) and f.attr == "pop" and ast.unparse(f.value) ==
                "os.environ" and bool(node.args) and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == "PACER_NO_MEDIA")
    if isinstance(node, ast.Delete):
        return any(isinstance(t, ast.Subscript) and ast.unparse(t.value) == "os.environ"
                   and isinstance(t.slice, ast.Constant) and t.slice.value == "PACER_NO_MEDIA"
                   for t in node.targets)
    return False


def _builds_a_real_player(source: str) -> bool:
    """Whether a test file's ORDINARY registration builds a real QMediaPlayer: it pops
    PACER_NO_MEDIA anywhere outside the functions its FOOTAGE_CHECKS names. A footage check is its
    own registration (tests/_footage.py), needs a recording CI never has, and so never meets CI's
    decode world."""
    tree = ast.parse(source)
    footage = set()
    for stmt in tree.body:
        if (isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "FOOTAGE_CHECKS"
                                                 for t in stmt.targets)
                and isinstance(stmt.value, (ast.Tuple, ast.List))):
            footage |= {e.id for e in stmt.value.elts if isinstance(e, ast.Name)}
    todo: list[ast.AST] = list(tree.body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in footage:
            continue
        if _pops_no_media(node):
            return True
        todo.extend(ast.iter_child_nodes(node))
    return False


def _real_player_tests() -> set[str]:
    found = set()
    for path in glob.glob(os.path.join(_TESTS, "test_*.py")):
        with open(path, encoding="utf-8") as f:
            source = f.read()
        # parse only the files that could match: parsing every test file took 2.3 s under load
        if _MAYBE_POPS.search(source) and _builds_a_real_player(source):
            found.add(os.path.splitext(os.path.basename(path))[0])
    return found


def test_the_suite_tasks_default_to_parallel():
    """Each parallel task sets a whole-number level above one. An empty value would mean "every
    core" to ctest, which is not the measured choice (two lanes at -j8 timed out), and "1" is the
    serial suite back again."""
    tasks = _tasks()
    problems = []
    for name in PARALLEL_TASKS:
        level = (tasks[name].get("env") or {}).get(_LEVEL_ENV)
        if level is None:
            problems.append(f"`{name}` sets no {_LEVEL_ENV}: it runs one test at a time again")
        elif not (level.isdigit() and int(level) > 1):
            problems.append(f"`{name}` sets {_LEVEL_ENV}={level!r}, not a level above one")
    assert not problems, "the suite tasks lost their parallel default:\n  " + "\n  ".join(problems)
    print(f"test_the_suite_tasks_default_to_parallel OK ({', '.join(PARALLEL_TASKS)})")


def test_no_ctest_task_passes_its_own_parallel_flag():
    """The negative controls are the patterns matching every shape they must catch and none they
    must not, so a rewrite of either cannot pass by matching nothing."""
    for shape in ("ctest -j", "ctest -j8 -E x", "ctest --parallel 4", "ctest --parallel=4"):
        assert _J_FLAG.search(shape), f"the flag pattern no longer recognises {shape!r}"
    for shape in ("ctest -E 'test_export_video|test_compare_lifecycle'", "ctest -L footage"):
        assert not _J_FLAG.search(shape), f"the flag pattern flags an innocent command {shape!r}"
    for shape in ("ctest -R x", "caffeinate -si ctest -L soak", "/opt/bin/ctest --test-dir b"):
        assert _is_ctest(shlex.split(shape)), f"{shape!r} is a ctest run the derivation misses"
    for shape in ("python tests/test_x.py", "python -m studio.dev._smoke", "ruff check .",
                  "python tests/ctest_notes.py"):
        assert not _is_ctest(shlex.split(shape)), f"{shape!r} is not a ctest run"
    tasks = _tasks()
    ctest = _ctest_tasks(tasks)
    missing = [n for n in (*PARALLEL_TASKS, "golden") if n not in ctest]
    assert not missing, f"the ctest-task derivation lost {missing}; it found only {sorted(ctest)}"
    bad = [f"`{name}`: {shlex.join(argv)!r}" for name, argv in ctest.items()
           if _J_FLAG.search(shlex.join(argv))]
    assert not bad, ("a ctest task passes its own -j, so appending one fails in ctest 4 — set "
                     f"{_LEVEL_ENV} in the task's env instead:\n  " + "\n  ".join(bad))
    print(f"test_no_ctest_task_passes_its_own_parallel_flag OK ({len(ctest)} tasks: "
          f"{', '.join(sorted(ctest))})")


def test_every_task_regex_names_real_registrations():
    """Every name a ctest task's `-R`/`-E` selects is a tests/<name>.py, golden's bare `-R` included.
    The plant: one misspelt letter in test-core's list is caught, and so is a wildcard, which could
    not be checked against the files at all."""
    assert _selected_names(["ctest", "-R", "^(test_a|test_b)$"]) == [("-R", "test_a"),
                                                                    ("-R", "test_b")]
    assert _selected_names(["ctest", "-R", "test_golden_synthetic"]) == [
        ("-R", "test_golden_synthetic")]
    tasks = _tasks()
    ctest = _ctest_tasks(tasks)
    core = ctest["test-core"]
    planted = [a.replace("test_golden_synthetic", "test_golden_synthetc") for a in core]
    assert "-R 'test_golden_synthetc'" in _unreal_names(planted), _unreal_names(planted)
    wildcard = [a.replace("test_stats", "test_stat.*") for a in core]
    assert "-R 'test_stat.*'" in _unreal_names(wildcard), _unreal_names(wildcard)
    named = {task: _selected_names(argv) for task, argv in ctest.items()}
    for task in ("test-core", "test-ciworld", "golden", "test-fast"):
        assert named[task], f"`{task}` selects no registration by name: {shlex.join(ctest[task])!r}"
    bad = [f"`{task}` {problem}" for task, argv in ctest.items() for problem in _unreal_names(argv)]
    assert not bad, ("a task selects a registration that does not exist, so ctest runs nothing for "
                     "it and still exits 0 — fix the name, or drop it:\n  " + "\n  ".join(bad))
    total = sum(len(v) for v in named.values())
    print(f"test_every_task_regex_names_real_registrations OK ({total} names over "
          f"{sum(1 for v in named.values() if v)} tasks)")


def test_the_ci_decode_world_runs_every_real_player_test():
    """`test-ciworld` names exactly the registrations that build a real QMediaPlayer. The plants:
    the recognisers see a pop at module level, inside a test and as a `del`, and ignore a set, a
    setdefault and a pop inside a footage check."""
    real = [
        'import os\nos.environ.pop("PACER_NO_MEDIA", None)\n',
        "import os\ndef test_x():\n    os.environ.pop('PACER_NO_MEDIA')\n",
        'import os\ndel os.environ["PACER_NO_MEDIA"]\n',
    ]
    inert = [
        'import os\nos.environ["PACER_NO_MEDIA"] = "1"\n',
        'import os\nos.environ.setdefault("PACER_NO_MEDIA", "1")\n',
        'import os\nos.environ.pop("PACER_SOAK", None)\n',
        ('import os\nos.environ["PACER_NO_MEDIA"] = "1"\ndef test_real():\n'
         '    os.environ.pop("PACER_NO_MEDIA", None)\nFOOTAGE_CHECKS = (test_real,)\n'),
    ]
    for src in real:
        assert _MAYBE_POPS.search(src) and _builds_a_real_player(src), (
            f"a real-player test the scan misses:\n{src}")
    for src in inert:
        assert not _builds_a_real_player(src), f"an inert test the scan counts as real:\n{src}"
    found = _real_player_tests()
    assert found, "no test file pops PACER_NO_MEDIA: the scan is broken, or the idiom changed"
    listed = {name for _, name in _selected_names(_ctest_tasks(_tasks())[CIWORLD_TASK])}
    problems = [f"{n} builds a real QMediaPlayer (it pops PACER_NO_MEDIA) but `{CIWORLD_TASK}` "
                "does not run it: add it to the task's -R list" for n in sorted(found - listed)]
    problems += [f"{n} is in `{CIWORLD_TASK}` but its registration never pops PACER_NO_MEDIA, so "
                 "it builds only the inert triplet and CI's decode world changes nothing there"
                 for n in sorted(listed - found)]
    assert not problems, "test-ciworld is out of step:\n  " + "\n  ".join(problems)
    print(f"test_the_ci_decode_world_runs_every_real_player_test OK ({', '.join(sorted(found))})")


# ------------------------------------------------------------------------------ hang dumps
#: The child's ceiling, as PACER_HANG_DUMP gives it: the dump lands at 1.98 s, the backup at 2.09 s.
_CEILING = "2.2"
#: How long each hang shape lasts before ending by itself, so a child never needs killing.
_HANG_S = 5
#: The hang shapes, each a test function run by a runner like the suite's own. Every hanging call
#: carries `# HANG` (the line the dump must point at); QtCore is imported before the clock matters.
_SHAPES_SRC = '''\
import os, sys, threading, time
from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer

HANG_S = {hang}


def test_sleeps_in_python():
    time.sleep(HANG_S)  # HANG


def test_deadlocks_on_two_locks():
    a, b = threading.Lock(), threading.Lock()
    a.acquire()
    b_held = threading.Event()

    def holder():
        with b:
            b_held.set()
            a.acquire(timeout=HANG_S)  # HELPER

    t = threading.Thread(target=holder, name="lock-holder")
    t.start()
    b_held.wait()
    b.acquire(timeout=HANG_S)  # HANG
    a.release()
    t.join()


def test_spins_a_qt_event_loop_nobody_quits():
    app = QCoreApplication.instance() or QCoreApplication([])
    loop = QEventLoop()
    QTimer.singleShot(HANG_S * 1000, loop.quit)  # only so the probe ends; the dump comes first
    loop.exec()  # HANG
    del app


def test_holds_the_gil_in_a_c_call():
    import ctypes, ctypes.util
    libc = ctypes.PyDLL(ctypes.util.find_library("c"))  # a PyDLL call keeps the GIL
    libc.sleep(HANG_S)  # HANG


def test_finishes_in_time():
    print("PACER_HANG_DUMP left for children:", os.environ.get("PACER_HANG_DUMP"))


if __name__ == "__main__":
    print("ok  test_before_it")
    globals()[sys.argv[1]]()
    print("ok  " + sys.argv[1])
'''
#: A stand-in for ctest: a parent whose own command line carries the `--timeout`, spawning the
#: test with PACER_HANG_DUMP=ctest (set for the child only, so this parent arms nothing itself).
_FAKE_CTEST_SRC = ("import os, subprocess, sys\n"
                   "env = dict(os.environ, PACER_HANG_DUMP='ctest')\n"
                   "sys.exit(subprocess.call(sys.argv[1:4], env=env))\n")
_shape_runs: dict[str, tuple[int, str, str, float]] = {}
_shape_script: list[str] = []


def _hang_line(src: str, func: str, marker: str = "# HANG") -> int:
    lines = src.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith(f"def {func}("))
    return next(i for i in range(start, len(lines)) if lines[i].endswith(marker)) + 1


def _child_env(ceiling: str | None) -> dict:
    """This environment with tests/_site first on PYTHONPATH, as a registration has it, and the
    hang-dump variable set to `ceiling` (or absent)."""
    env = {k: v for k, v in os.environ.items() if k != _hang_dump.ENV}
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(_TESTS, "_site")] + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    if ceiling is not None:
        env[_hang_dump.ENV] = ceiling
    return env


def _run_shapes() -> tuple[str, dict[str, tuple[int, str, str, float]]]:
    """Run every shape at once (the wall time is one hang, ~5 s): armed by the variable, and twice
    more under a fake ctest, with and without `--timeout`. Cached: two checks read it. Returns the
    script's path (gone by then) and, per run, (exit status, stdout, stderr, seconds)."""
    if _shape_runs:
        return _shape_script[0], _shape_runs
    workdir = tempfile.mkdtemp(prefix="pacer-hang-dump-")
    script = os.path.join(workdir, "test_hang_shapes.py")
    _shape_script.append(script)
    with open(script, "w", encoding="utf-8") as f:
        f.write(_SHAPES_SRC.format(hang=_HANG_S))
    runs = {name: [sys.executable, script, name] for name in (
        "test_sleeps_in_python", "test_deadlocks_on_two_locks",
        "test_spins_a_qt_event_loop_nobody_quits", "test_holds_the_gil_in_a_c_call",
        "test_finishes_in_time")}
    procs = {name: (subprocess.Popen(argv, env=_child_env(_CEILING), stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True), time.monotonic())
             for name, argv in runs.items()}
    for key, extra in (("ctest --timeout", ["--timeout", _CEILING]), ("ctest, no --timeout", [])):
        argv = [sys.executable, "-c", _FAKE_CTEST_SRC, *runs["test_sleeps_in_python"], *extra]
        procs[key] = (subprocess.Popen(argv, env=_child_env(None), stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, text=True), time.monotonic())
    try:
        for key, (proc, t0) in procs.items():
            out, err = proc.communicate(timeout=120)
            _shape_runs[key] = (proc.returncode, out, err, time.monotonic() - t0)
    finally:
        for proc, _t0 in procs.values():
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        shutil.rmtree(workdir, ignore_errors=True)
    return script, _shape_runs


def test_a_hang_prints_the_running_test_and_every_thread_before_the_timeout():
    """Each hang shape, armed with a 2.2 s ceiling, writes the running test, the hanging line and
    every thread's stack to stderr, once, before the ceiling — and the child still runs to its own
    end with exit 0: the dump neither exits nor signals. A Qt event loop and a lock wait release the
    GIL, so the Python watchdog names the test; a C call that keeps it leaves the dump to
    faulthandler's own thread, whose stack still names the test. A child that finishes in time
    prints nothing extra, and its children inherit no arming."""
    script, runs = _run_shapes()
    src = _SHAPES_SRC.format(hang=_HANG_S)
    problems = []
    threads = {"test_sleeps_in_python": 2, "test_deadlocks_on_two_locks": 3,
               "test_spins_a_qt_event_loop_nobody_quits": 2}
    for name, n_threads in threads.items():
        rc, out, err, secs = runs[name]
        line = _hang_line(src, name)
        want = [f"{_hang_dump.BANNER}: still running",
                f"{_hang_dump.BANNER}: running test: {name} ({script}:{line})",
                f'File "{script}", line {line} in {name}', f"{_hang_dump.BANNER}: end"]
        if name == "test_deadlocks_on_two_locks":
            want.append(f'line {_hang_line(src, "holder", "# HELPER")} in holder')
        mine = [f"{name}: stderr lacks {w!r}" for w in want if w not in err]
        stacks = err.count("Thread 0x") + err.count("Current thread 0x")
        if stacks != n_threads:
            mine.append(f"{name}: {stacks} thread stacks, want {n_threads} (the test's threads "
                        "and the watchdog's)")
        if err.count(f"{_hang_dump.BANNER}: running test") != 1 or "Timeout (" in err:
            mine.append(f"{name}: not exactly one dump (the backup must be cancelled)")
        if rc != 0 or not out.rstrip().endswith(f"ok  {name}") or secs < _HANG_S:
            mine.append(f"{name}: the dump changed the run: exit {rc} after {secs:.1f} s, "
                        f"stdout {out[-200:]!r}")
        problems += mine + ([f"{name} stderr:\n{err[-3000:]}"] if mine else [])

    name = "test_holds_the_gil_in_a_c_call"
    rc, out, err, secs = runs[name]
    line = _hang_line(src, name)
    if not (err.startswith("Timeout (0:00:02") and f"line {line} in {name}" in err
            and _hang_dump.BANNER not in err and rc == 0 and secs >= _HANG_S):
        problems.append(f"{name}: faulthandler's own thread did not dump the GIL-held hang "
                        f"alone (exit {rc}, {secs:.1f} s):\n{err[-3000:]}")

    rc, out, err, _secs = runs["test_finishes_in_time"]
    if rc != 0 or err != "" or "left for children: None" not in out:
        problems.append(f"a child that finishes in time changed: exit {rc}, stderr {err!r}, "
                        f"stdout {out!r}")
    assert not problems, "\n".join(problems)
    print("test_a_hang_prints_the_running_test_and_every_thread_before_the_timeout OK")


def test_the_dump_beats_ctests_own_timeout():
    """The ceiling is the one that kills the test: a registration's TIMEOUT, else the running
    ctest's `--timeout` read off the parent's command line (CTest exports neither). With no
    `--timeout` nothing kills the test, so nothing is armed. The dump sits 10 % (at most 30 s)
    under the ceiling, so it lands well before ctest's kill at 480 s and at 3600 s."""
    assert _hang_dump.deadlines(480) == (450.0, 465.0), _hang_dump.deadlines(480)
    assert _hang_dump.deadlines(3600) == (3570.0, 3585.0), _hang_dump.deadlines(3600)
    for line, want in (
            ("ctest --test-dir build/Release --output-on-failure --timeout 480", 480.0),
            ("ctest --timeout=33 -R '^test_x$'", 33.0),
            ("ctest --timeout 10 --timeout 44", 44.0),  # measured: ctest 4.4 computes 44
            ("ctest --test-dir build/Release -R test_golden_synthetic", None),
            ("ctest --timeout 0", None), ("", None)):
        got = _hang_dump.ctest_timeout(line)
        assert got == want, f"{line!r}: read {got}, want {want}"

    _script, runs = _run_shapes()
    rc, _out, err, _secs = runs["ctest --timeout"]
    want = f"before its {float(_CEILING):g} s timeout (ctest --timeout)"
    assert rc == 0 and want in err and "running test: test_sleeps_in_python" in err, (
        f"a test under a parent with `--timeout {_CEILING}` did not dump against it (exit "
        f"{rc}):\n{err[-3000:]}")
    rc, _out, err, _secs = runs["ctest, no --timeout"]
    assert rc == 0 and err == "", f"with no --timeout something was armed:\n{err[-3000:]}"
    print("test_the_dump_beats_ctests_own_timeout OK")


def test_every_python_registration_arms_the_hang_dump():
    """THE BUILD: every registration that runs a Python file has tests/_site first on PYTHONPATH
    and PACER_HANG_DUMP set to its own TIMEOUT, or to `ctest` where it has none — so a new test,
    footage check or soak is covered with no edit."""
    ctest = os.environ.get("PACER_CTEST_COMMAND") or shutil.which("ctest") or os.path.join(
        _REPO, ".pixi", "envs", "default", "bin", "ctest")
    build = os.environ.get("PACER_CTEST_DIR") or os.path.join(_REPO, "build", "Release")
    assert os.path.isfile(ctest) and os.path.isdir(build), (
        f"cannot list the registered tests (ctest={ctest}, build dir={build}). Run this through "
        "ctest, or build first — this check must not pass by finding nothing to check")
    proc = subprocess.run([ctest, "--test-dir", build, "--show-only=json-v1"], capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    python = [t for t in json.loads(proc.stdout)["tests"]
              if any(str(c).endswith(".py") for c in t.get("command", []))]
    assert len(python) >= 100, f"only {len(python)} Python registrations listed — listing broken"
    site = os.path.realpath(os.path.join(_TESTS, "_site"))
    wrong = []
    for t in python:
        props = {p["name"]: p["value"] for p in t.get("properties", [])}
        values = [e.split("=", 1)[1] for e in props.get("ENVIRONMENT", [])
                  if e.startswith(_hang_dump.ENV + "=")]
        timeout = props.get("TIMEOUT")
        armed = len(values) == 1 and (values[0] == _hang_dump.CTEST if timeout is None
                                      else float(values[0]) == float(timeout))
        prepended = any(m.startswith("PYTHONPATH=path_list_prepend:")
                        and os.path.realpath(m.split(":", 1)[1]) == site
                        for m in props.get("ENVIRONMENT_MODIFICATION", []))
        if not (armed and prepended):
            wrong.append(f"{t['name']} ({values}, TIMEOUT {timeout}, prepends _site: {prepended})")
    assert not wrong, (
        f"{len(wrong)} of {len(python)} Python registrations are not armed for a hang dump: "
        f"{', '.join(wrong[:8])}. The HANG DUMPS loop near the end of tests/CMakeLists.txt arms "
        "every registration above it")
    assert os.path.isfile(os.path.join(site, "sitecustomize.py"))
    print(f"test_every_python_registration_arms_the_hang_dump OK ({len(python)} registrations)")


if __name__ == "__main__":
    test_the_suite_tasks_default_to_parallel()
    test_no_ctest_task_passes_its_own_parallel_flag()
    test_every_task_regex_names_real_registrations()
    test_the_ci_decode_world_runs_every_real_player_test()
    test_a_hang_prints_the_running_test_and_every_thread_before_the_timeout()
    test_the_dump_beats_ctests_own_timeout()
    test_every_python_registration_arms_the_hang_dump()
    print("\nOK: the suite runs in parallel by default, a run can still be made serial, every "
          "task's list names real registrations, and a hang prints where it is before its timeout")
