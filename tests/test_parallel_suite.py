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

Pure tomllib + ast: no Qt, no bindings, no ctest.

Run: python tests/test_parallel_suite.py
"""
import ast
import glob
import os
import re
import shlex
import tomllib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.join(_REPO, "tests")

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


if __name__ == "__main__":
    test_the_suite_tasks_default_to_parallel()
    test_no_ctest_task_passes_its_own_parallel_flag()
    test_every_task_regex_names_real_registrations()
    test_the_ci_decode_world_runs_every_real_player_test()
    print("\nOK: the suite runs in parallel by default, a run can still be made serial, and every "
          "task's list names real registrations")
