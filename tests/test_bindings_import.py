"""Every documented way to start Python here imports the REAL bindings, with no PYTHONPATH (EVAL-1).

WHY THIS EXISTS. On 2026-09-26 the README's own command, `pixi run studio -- --demo`, failed on
every fresh clone. It showed "Couldn't read telemetry from this recording — it may be corrupt",
said about a sha256-checked download. From the repo root, `import pacer` resolved to a NAMESPACE
package with two portions: the C++ `pacer/` source directory, and the `site-packages/pacer/` that
the build filled with the compiled `.so` and nothing else. A namespace package imports without
error and has no `Laps`, so the load died later with AttributeError. The editable install's finder
never runs, because it sits after the path finder on `sys.meta_path` and a namespace portion
satisfies the path finder first. Measured on a fresh env after `pixi run build`, it failed from
the repo root, from a subdirectory and from outside the repo alike.

Nothing caught it, because every gate set `PYTHONPATH=bindings/pacer`: CTest, the smoke task, CI's
smoke step and every QA probe. On the dev Mac a stale Jun 4 `site-packages/pacer/__init__.py` hid it
from the one person who runs `pixi run studio`. So this test strips PYTHONPATH from its children,
as a fresh clone's shell has none, and starts the env's own interpreter:
  * from the repo root, where `pixi run studio` and `python -m studio` start. The C++ `pacer/`
    directory is on `sys.path` there;
  * from a subdirectory, where `pixi run python …` keeps the caller's directory;
  * from outside the repo: an IDE, a script, PyInstaller's spec.
Each child must get a REGULAR package (`__file__` set) with `Laps` and `GPMFSource`. The copy it
gets must also be byte-identical to the in-tree `bindings/pacer/pacer/` (`__init__.py`,
`__init__.pyi`, the `.so`). A deploy that copies part of the package, or a stale stub left from an
older install, fails here by name.

The fix it holds is in bindings/pacer/CMakeLists.txt: the build deploys the whole package into
site-packages, and a regular package beats any number of namespace portions. No Qt, no telemetry
file. CTest runs it after `pixi run build`, which is what makes the deployed copy current.
"""
import filecmp
import json
import os
import subprocess
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_IN_TREE = os.path.join(_REPO, "bindings", "pacer", "pacer")

# What the child reports: where `pacer` came from, and whether it is the real thing.
_PROBE = ("import json, pacer; print(json.dumps({'file': getattr(pacer, '__file__', None), "
          "'path': list(getattr(pacer, '__path__', [])), 'Laps': hasattr(pacer, 'Laps'), "
          "'GPMFSource': hasattr(pacer, 'GPMFSource')}))")


def _fresh_env() -> dict:
    """This process's environment minus what a fresh clone's shell does not have. CTest injects
    PYTHONPATH=bindings/pacer into THIS process, which is exactly the variable that hid the bug."""
    return {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONSAFEPATH")}


def _import_pacer_from(cwd: str) -> dict:
    out = subprocess.run([sys.executable, "-c", _PROBE], cwd=cwd, env=_fresh_env(),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, (f"`import pacer` failed from {cwd} with no PYTHONPATH "
                                 f"(rc={out.returncode}):\n{out.stderr[-2000:]}")
    return json.loads(out.stdout.strip().splitlines()[-1])


def _assert_real_bindings(where: str, got: dict):
    assert got["file"] is not None, (
        f"from {where} with no PYTHONPATH, `import pacer` is a NAMESPACE package over "
        f"{got['path']}: the build did not deploy a whole package into site-packages "
        f"(bindings/pacer/CMakeLists.txt), so `pixi run studio` fails to load any recording")
    assert got["Laps"] and got["GPMFSource"], (
        f"from {where}, `import pacer` found {got['file']} without Laps/GPMFSource: {got}")


def test_repo_root_imports_the_real_bindings():
    """`pixi run studio` and `python -m studio` start here, with the C++ `pacer/` on sys.path."""
    _assert_real_bindings("the repo root", _import_pacer_from(_REPO))
    print("test_repo_root_imports_the_real_bindings OK")


def test_a_subdirectory_imports_the_real_bindings():
    """`pixi run python …` keeps the caller's directory, so a relative PYTHONPATH broke here."""
    _assert_real_bindings("studio/", _import_pacer_from(os.path.join(_REPO, "studio")))
    print("test_a_subdirectory_imports_the_real_bindings OK")


def test_outside_the_repo_imports_the_real_bindings():
    """An IDE, a script or PyInstaller's spec: the env's interpreter, nowhere near the repo."""
    with tempfile.TemporaryDirectory() as elsewhere:
        _assert_real_bindings("outside the repo", _import_pacer_from(elsewhere))
    print("test_outside_the_repo_imports_the_real_bindings OK")


def test_the_deployed_package_is_the_one_this_build_wrote():
    """The package a PYTHONPATH-less run imports is byte for byte `bindings/pacer/pacer/`: the
    `__init__.py`, the stubs and the compiled module. The dev Mac carried a 13,146 B stub from Jun 4
    beside a current `.so`, a mixed package that only this comparison can see."""
    got = _import_pacer_from(_REPO)
    _assert_real_bindings("the repo root", got)
    deployed = os.path.dirname(got["file"])
    names = sorted(n for n in os.listdir(_IN_TREE)
                   if n in ("__init__.py", "__init__.pyi") or n.startswith("_pacer."))
    assert "__init__.py" in names and "__init__.pyi" in names, names
    assert any(n.startswith("_pacer.") for n in names), (
        f"no compiled module in {_IN_TREE}: run `pixi run build` first ({names})")
    # (When the interpreter imports the in-tree package itself, nothing can have drifted.)
    same = os.path.realpath(deployed) == os.path.realpath(_IN_TREE)
    stale = [] if same else [
        n for n in names if not os.path.exists(os.path.join(deployed, n))
        or not filecmp.cmp(os.path.join(_IN_TREE, n), os.path.join(deployed, n), shallow=False)]
    # A deploy copies only when its inputs change, so a copy edited behind the build's back stays
    # put; deleting it makes the next build put it back (the copies are declared byproducts).
    assert not stale, (f"{deployed} differs from {_IN_TREE} in {stale}: the build's site-packages "
                       f"deploy (bindings/pacer/CMakeLists.txt) is missing or did not run. Delete "
                       f"those files from {deployed} and run `pixi run build`.")
    print("test_the_deployed_package_is_the_one_this_build_wrote OK")


def _run_all():
    test_repo_root_imports_the_real_bindings()
    test_a_subdirectory_imports_the_real_bindings()
    test_outside_the_repo_imports_the_real_bindings()
    test_the_deployed_package_is_the_one_this_build_wrote()
    print("all bindings-import tests OK")


if __name__ == "__main__":
    _run_all()
