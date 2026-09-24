"""The type-check gate's allow-list (ARCH-8), held to its ratchet.

`pixi run typecheck` runs pyright (pinned in pixi.lock) in basic mode over an EXPLICIT list of
Qt-free core modules, `pyrightconfig.json`'s "include". CI runs it before the test step. Pyright is
the check itself. This file pins what pyright cannot see about its own input:

  * the list is sorted, with no duplicates, so two PRs that each add a module touch different
    lines and a reviewer reads one list;
  * every listed file exists. Pyright prints a note for an include that matches nothing and still
    exits 0 (measured on 1.1.414), so a renamed module would leave the gate without a word;
  * every listed module is in the Qt-free core: `studio/*.py` outside test_layering's QT_REACHING.
    Over the whole of studio/ pyright reports 1,101 errors (2026-09-24), mostly PySide6/pyqtgraph
    stub noise, and that is why the gate is scoped;
  * every Qt-free core module is EITHER listed OR named in NOT_YET below, never both. That is the
    ratchet: a module moves from NOT_YET into the list in the PR that makes it clean, NOT_YET only
    shrinks, and a NEW Qt-free module must be classified. It joins the list in the PR that creates
    it; putting it in NOT_YET instead is a choice the diff shows;
  * the settings that make a green run mean something (basic mode, the binding stubs on
    extraPaths, the locked env as the venv, the env's Python version), the task, the exact pyright
    pin, and the CI step running before the tests.

To take a module off NOT_YET: `pixi run typecheck studio/<module>.py` lists its errors. Fix them with
annotation or narrowing changes that alter no behaviour (`pixi run golden` unmoved), then move the
name from NOT_YET into the include list.

Pure stdlib: no Qt, no pacer, no pyright. Run:  python tests/test_typecheck_config.py
"""
import json
import os
import re
import sys
import tomllib

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
sys.path.insert(0, _TESTS)
from test_layering import QT_REACHING, _modules  # the one definition of "Qt-free"

# Qt-free core modules that pyright (basic) does not pass yet. This set only shrinks. 23 modules
# with 161 errors between them when the gate landed (2026-09-24).
NOT_YET = {
    "coaching", "corner_model", "corners", "driving_channels", "export_data", "focus", "gmeter",
    "library", "marks", "prefs", "session", "session_record", "stats", "track_db",
}

_ENTRY = re.compile(r"studio/(\w+)\.py")


def _read(*parts):
    with open(os.path.join(_REPO, *parts), encoding="utf-8") as fh:
        return fh.read()


def _config() -> dict:
    """pyrightconfig.json without its full-line // comments (pyright reads JSONC, json does not)."""
    text = _read("pyrightconfig.json")
    return json.loads("\n".join(ln for ln in text.splitlines() if not ln.lstrip().startswith("//")))


def _core() -> set[str]:
    return set(_modules()) - QT_REACHING


def allow_list_problems(include, not_yet, core, exists) -> list[str]:
    """Every way `include` + `not_yet` break the ratchet, as sentences. [] means none."""
    problems = []
    if include != sorted(include):
        problems.append("the include list is not sorted")
    for entry in sorted({e for e in include if include.count(e) > 1}):
        problems.append(f"{entry} is listed {include.count(entry)} times")
    listed = set()
    for entry in dict.fromkeys(include):
        m = _ENTRY.fullmatch(entry)
        if not m:
            problems.append(f"{entry!r} is not a studio/<module>.py path")
        elif not exists(entry):
            problems.append(f"{entry} does not exist: pyright skips it and still exits 0, so the "
                            "module has left the gate. Point the entry at its new name")
        elif m.group(1) not in core:
            problems.append(f"{entry} reaches Qt (test_layering.QT_REACHING). The gate is scoped "
                            "to the Qt-free core, where a clean run is not stub noise")
        else:
            listed.add(m.group(1))
    for name in sorted(listed & not_yet):
        problems.append(f"{name} is type-checked AND in NOT_YET: delete it from NOT_YET")
    for name in sorted(not_yet - core):
        problems.append(f"NOT_YET names {name}, which is not a Qt-free core module (renamed, "
                        "deleted, or it reaches Qt now): remove it")
    for name in sorted(core - listed - not_yet):
        problems.append(f"studio/{name}.py is a Qt-free core module that is neither type-checked "
                        "nor in NOT_YET: add it to pyrightconfig.json's include and keep "
                        "`pixi run typecheck` green (a new module joins the gate in the PR that "
                        "creates it)")
    return problems


def _exists(entry):
    return os.path.isfile(os.path.join(_REPO, entry))


def test_the_allow_list_keeps_its_ratchet():
    include, core = _config()["include"], _core()
    problems = allow_list_problems(include, NOT_YET, core, _exists)
    assert not problems, "pyrightconfig.json / NOT_YET:\n  " + "\n  ".join(problems)
    print(f"test_the_allow_list_keeps_its_ratchet OK: {len(include)} of {len(core)} Qt-free core "
          f"modules type-checked, {len(NOT_YET)} not yet")


def test_each_rule_catches_its_planted_break():
    """Every sentence above, planted once on a copy of the real inputs and caught by name."""
    include, core = list(_config()["include"]), _core()
    first, second = include[0], include[1]
    name = _ENTRY.fullmatch(second).group(1)
    planted = {
        "unsorted": ([second, first] + include[2:], NOT_YET, core),
        "duplicate": (include[:2] + [second] + include[2:], NOT_YET, core),
        "missing": (sorted(include + ["studio/zz_renamed_away.py"]), NOT_YET, core),
        "qt": (sorted(include + ["studio/app.py"]), NOT_YET, core),
        "both": (include, NOT_YET | {name}, core),
        "stale": (include, NOT_YET | {"zz_gone"}, core),
        "unclassified": ([first] + include[2:], NOT_YET, core),
    }
    expect = {
        "unsorted": "the include list is not sorted",
        "duplicate": f"{second} is listed 2 times",
        "missing": "studio/zz_renamed_away.py does not exist",
        "qt": "studio/app.py reaches Qt",
        "both": f"{name} is type-checked AND in NOT_YET",
        "stale": "NOT_YET names zz_gone",
        "unclassified": f"studio/{name}.py is a Qt-free core module that is neither",
    }
    for key, (inc, todo_set, qt_free) in planted.items():
        caught = allow_list_problems(inc, todo_set, qt_free, _exists)
        assert len(caught) == 1 and caught[0].startswith(expect[key]), (key, caught)
    print(f"test_each_rule_catches_its_planted_break OK: {len(planted)} planted breaks, "
          "each caught alone")


def test_the_settings_that_make_a_green_run_mean_something():
    cfg = _config()
    assert cfg["typeCheckingMode"] == "basic", cfg["typeCheckingMode"]
    # Without the stubs every `pacer.X` is Unknown, and a misspelt binding passes silently.
    assert "bindings/pacer" in cfg["extraPaths"], cfg["extraPaths"]
    assert os.path.isfile(os.path.join(_REPO, "bindings", "pacer", "pacer", "__init__.pyi"))
    # The locked env, not whichever python is first on PATH.
    assert (cfg["venvPath"], cfg["venv"]) == (".pixi/envs", "default"), cfg
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    assert cfg["pythonVersion"] == running, (
        f"pyrightconfig.json checks as Python {cfg['pythonVersion']} but the env runs {running}")
    print(f"test_the_settings_that_make_a_green_run_mean_something OK: basic, Python {running}")


def test_the_task_the_pin_and_the_ci_step():
    with open(os.path.join(_REPO, "pyproject.toml"), "rb") as fh:
        pixi = tomllib.load(fh)["tool"]["pixi"]
    task = pixi["tasks"]["typecheck"]
    assert task["cmd"] == "pyright", task
    # The conda wrapper otherwise asks pypi.org for a newer version on every run.
    assert task["env"]["PYRIGHT_PYTHON_IGNORE_WARNINGS"] == "1", task
    pin = pixi["dependencies"]["pyright"]
    assert re.fullmatch(r"==\d+\.\d+\.\d+", pin), f"pyright must be pinned exactly, not {pin!r}"
    ci = _read(".github", "workflows", "ci.yml")
    check, tests = ci.find("run: pixi run typecheck\n"), ci.find("run: pixi run test\n")
    assert 0 <= check < tests, "CI must run `pixi run typecheck` in a step before the tests"
    print(f"test_the_task_the_pin_and_the_ci_step OK: pyright {pin[2:]}, before the tests in CI")


def _run_all():
    tests = [
        test_the_allow_list_keeps_its_ratchet,
        test_each_rule_catches_its_planted_break,
        test_the_settings_that_make_a_green_run_mean_something,
        test_the_task_the_pin_and_the_ci_step,
    ]
    for fn in tests:
        fn()
    print(f"\n{len(tests)} type-check config tests passed")


if __name__ == "__main__":
    _run_all()
