"""A first launch says it is slow before it is slow, and a later launch says nothing (EVAL-7).

WHY THIS EXISTS. On a fresh clone `pixi run studio -- --demo` printed the build's progress and then
nothing for 14.5 s (QA round 3, 2026-09-26; 13.3 s in the lane's own clone), and a visitor cannot
tell a first launch from a hang. The wait is `python -m studio` importing the app in an environment
nothing has run in yet. Timed on a fresh clone on 2026-09-27, it took 17.0 s, against 0.6 s the
second time, and it is mostly native libraries loading for the first time. Compiling every module
to bytecode is 1.5 s of it. So `studio/__main__.py` prints FIRST_LAUNCH_NOTE before that import,
and only on a first launch, which it reads off this package's bytecode cache.

Each test starts the env's own interpreter the way `python -m studio` starts, from the repo root,
with an empty PYTHONPYCACHEPREFIX of its own. The empty prefix is exactly a fresh clone's state for
that check: no cached bytecode anywhere. The child imports `studio.__main__` and not `-m studio`,
so it runs everything up to `main()` and opens no window.
  * The first run must print the note, and before the slow import. `-X importtime` writes one
    stderr line as each module finishes loading. With both streams on one pipe, the note must come
    before the first line for a Qt module.
  * A second run with the same prefix finds the cache the first run wrote, and must print nothing.

The same file is also the .app's entry, and there it runs differently. PyInstaller runs the script
that `packaging/pacer.spec` hands `Analysis` as a TOP-LEVEL script, with no parent package, so a
relative import in it raises. `from .app import main` did exactly that, and every build from June
to QA round 4 (REG2-1) died at launch. No test saw it, because each one imported the entry as the
package module `studio.__main__`. So one test runs the spec's entry file the way the bootloader
does: by path, in a fresh interpreter, from a directory that is not the repo, with `sys.frozen`
set. Its run_name is not "__main__", so it stops short of `main()` and opens no window.
No telemetry, no window, no network.
"""
import os
import re
import subprocess
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)


def _launch(prefix: str) -> list[str]:
    """Output lines (stdout and stderr, in the order written) of one launch up to `main()`."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    env.update(PYTHONPYCACHEPREFIX=prefix, QT_QPA_PLATFORM="offscreen")
    out = subprocess.run([sys.executable, "-X", "importtime", "-c", "import studio.__main__"],
                         cwd=_REPO, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, timeout=240)
    assert out.returncode == 0, f"the launch failed (rc {out.returncode}):\n{out.stdout[-3000:]}"
    return out.stdout.splitlines()


def _note() -> str:
    import studio.__main__ as entry  # the note is the module's own constant, not a copy

    return entry.FIRST_LAUNCH_NOTE


def test_a_first_launch_says_so_before_the_slow_import():
    note = _note()
    with tempfile.TemporaryDirectory(prefix="pacer-first-launch-") as prefix:
        lines = _launch(prefix)
        said = [i for i, line in enumerate(lines) if line == note]
        assert said, (f"a first launch printed no '{note}' line: a fresh clone waits ~15 s on a "
                      f"silent terminal (studio/__main__.py)")
        qt = [i for i, line in enumerate(lines) if "PySide6.Qt" in line and "import time" in line]
        assert qt, "the child's -X importtime output names no Qt module: this check lost its subject"
        assert said[0] < qt[0], (f"the note came after Qt had loaded (line {said[0]} vs {qt[0]}): "
                                 f"it has to be printed BEFORE the import it announces")
        again = _launch(prefix)
        assert note not in again, "a second launch, with the first one's cache in place, said it again"
    print("test_a_first_launch_says_so_before_the_slow_import OK")


def test_a_frozen_app_never_says_so():
    """The .app ships compiled, so it has no first-launch cost to announce."""
    import studio.__main__ as entry

    had = getattr(sys, "frozen", None)
    sys.frozen = True
    try:
        assert entry.say_if_first_launch() is False
    finally:
        if had is None:
            del sys.frozen
        else:
            sys.frozen = had
    print("test_a_frozen_app_never_says_so OK")


def _frozen_entry() -> str:
    """The script the .app runs: the first path in pacer.spec's `Analysis([...])`, off disk."""
    with open(os.path.join(_REPO, "packaging", "pacer.spec"), encoding="utf-8") as f:
        m = re.search(r"\bAnalysis\(\s*\[\s*_repo\(([^)]*)\)", f.read())
    assert m, ("packaging/pacer.spec has no `Analysis([_repo(...)` entry any more: this check "
               "lost its subject; point _frozen_entry at the script the spec now bundles")
    parts = [p.strip().strip("\"'") for p in m.group(1).split(",")]
    entry = os.path.join(_REPO, *parts)
    assert os.path.isfile(entry), f"pacer.spec's entry {entry} does not exist"
    return entry


def test_the_entry_runs_as_a_top_level_script():
    """The bundle's entry resolves `main` with no parent package, the way the .app runs it."""
    entry = _frozen_entry()
    child = ("import runpy, sys\n"
             "sys.frozen = True\n"  # what the bootloader sets; the entry reads it
             f"sys.path.insert(0, {_REPO!r})\n"  # pacer.spec's pathex: `studio` is importable
             f"g = runpy.run_path({entry!r}, run_name='__pacer_frozen_entry__')\n"
             "import studio.app\n"
             "print('main is studio.app.main:', g.get('main') is studio.app.main)\n")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    with tempfile.TemporaryDirectory(prefix="pacer-frozen-entry-") as cwd:
        out = subprocess.run([sys.executable, "-c", child], cwd=cwd, env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             timeout=240)
    assert out.returncode == 0, (f"{os.path.relpath(entry, _REPO)} failed as a top-level script "
                                 f"(rc {out.returncode}), which is how the .app runs it:\n"
                                 f"{out.stdout[-3000:]}")
    assert "main is studio.app.main: True" in out.stdout, (
        f"the entry ran but its `main` is not studio.app.main:\n{out.stdout[-3000:]}")
    assert _note() not in out.stdout, "the frozen entry printed the first-launch note"
    print("test_the_entry_runs_as_a_top_level_script OK")


def _run_all():
    test_a_first_launch_says_so_before_the_slow_import()
    test_a_frozen_app_never_says_so()
    test_the_entry_runs_as_a_top_level_script()
    print("all first-launch tests OK")


if __name__ == "__main__":
    _run_all()
