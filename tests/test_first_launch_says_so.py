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
No telemetry, no window, no network.
"""
import os
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


def _run_all():
    test_a_first_launch_says_so_before_the_slow_import()
    test_a_frozen_app_never_says_so()
    print("all first-launch tests OK")


if __name__ == "__main__":
    _run_all()
