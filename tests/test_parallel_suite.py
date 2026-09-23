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

What makes parallel runs safe is pinned elsewhere: each test process resolves its own app-support
jail (tests/test_app_support_jail.py), no test builds a fixed path in the shared $TMPDIR
(tests/test_temp_isolation.py), and the real-footage checks share RESOURCE_LOCK footage, so they
still run one at a time (tests/test_footage_checks.py).

Pure tomllib: no Qt, no bindings, no ctest.

Run: python tests/test_parallel_suite.py
"""
import os
import re
import tomllib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: The ctest tasks that default to parallel. `test-footage` is deliberately not one: its checks
#: share one RESOURCE_LOCK, so a level there would change nothing but how its command reads.
PARALLEL_TASKS = ("test", "test-fast")
#: Every task whose command is a ctest run — none may carry its own -j, or appending one fails.
CTEST_TASKS = ("test", "test-fast", "test-footage", "golden")

_LEVEL_ENV = "CTEST_PARALLEL_LEVEL"
_J_FLAG = re.compile(r"(^|\s)(-j\S*|--parallel\S*)(\s|$)")


def _tasks() -> dict:
    with open(os.path.join(_REPO, "pyproject.toml"), "rb") as f:
        return tomllib.load(f)["tool"]["pixi"]["tasks"]


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
    """The negative control is the pattern matching every shape it must catch and none it must not,
    so a rewrite of it cannot pass by matching nothing."""
    for shape in ("ctest -j", "ctest -j8 -E x", "ctest --parallel 4", "ctest --parallel=4"):
        assert _J_FLAG.search(shape), f"the flag pattern no longer recognises {shape!r}"
    for shape in ("ctest -E 'test_export_video|test_compare_lifecycle'", "ctest -L footage"):
        assert not _J_FLAG.search(shape), f"the flag pattern flags an innocent command {shape!r}"
    tasks = _tasks()
    bad = [f"`{name}`: {tasks[name]['cmd']!r}" for name in CTEST_TASKS
           if _J_FLAG.search(tasks[name]["cmd"])]
    assert not bad, ("a ctest task passes its own -j, so appending one fails in ctest 4 — set "
                     f"{_LEVEL_ENV} in the task's env instead:\n  " + "\n  ".join(bad))
    print(f"test_no_ctest_task_passes_its_own_parallel_flag OK ({len(CTEST_TASKS)} tasks)")


if __name__ == "__main__":
    test_the_suite_tasks_default_to_parallel()
    test_no_ctest_task_passes_its_own_parallel_flag()
    print("\nOK: the suite runs in parallel by default and a run can still be made serial")
