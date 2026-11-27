"""Which `pacer` bindings a dev tool or a by-hand test run should import: this tree's, when built.

The tools and test harnesses that put `<tree>/bindings/pacer` first on `sys.path` (or on a child's
PYTHONPATH) do it so a run imports exactly what THIS tree's build wrote, ahead of the C++ `pacer/`
source directory at the repo root (a namespace portion with no `Laps`: tests/test_bindings_import.py
has the incident). That is right in a built tree, and the only case CTest and CI ever see.

An UNBUILT tree — a fresh agent worktree: no `.pixi`, nothing compiled — has the package's
`__init__.py` there but no `_pacer` extension beside it, so putting it first SHADOWED the env's
deployed, working copy and every run died with `No module named 'pacer._pacer'` (pytest at
collection; `test_golden_hermetic`, `test_session_log`). So the directory is used only when a
compiled `_pacer.*` is in it, which is the same test `test_bindings_import` makes of a build.
Otherwise the run imports the env's deployed `pacer`, and says so in one line on stderr: what it
imports was built from the tree the env was built from, so a C++ edit here is not in it.

Nothing here changes a built tree: there `tree_bindings` returns the directory, and every caller
inserts it exactly where it did before. Stdlib only — `conftest.py` imports it before anything else.
"""
from __future__ import annotations

import importlib.util
import os
import sys

# The line written when this process fell back (None until then): pytest repeats it in its summary.
NOTE: str | None = None


def _built(package_dir: str) -> bool:
    """A compiled `_pacer` extension sits in the package (`_pacer.cpython-313-darwin.so`, …)."""
    try:
        return any(n.startswith("_pacer.") and n.endswith((".so", ".pyd"))
                   for n in os.listdir(package_dir))
    except OSError:
        return False


def tree_bindings(root: str) -> str | None:
    """`<root>/bindings/pacer` when this tree's build has compiled the bindings into it, else None
    — after one line on stderr naming the `pacer` this process will import instead."""
    path = os.path.join(root, "bindings", "pacer")
    if _built(os.path.join(path, "pacer")):
        return path
    _note_fallback()
    return None


def _note_fallback() -> None:
    global NOTE
    if NOTE is not None:
        return
    try:
        spec = importlib.util.find_spec("pacer")
    except (ImportError, ValueError):
        spec = None
    origin = getattr(spec, "origin", None) if spec is not None else None
    if origin and os.path.basename(origin) == "__init__.py":
        line = (f"bindings not built in this tree — using the env's deployed pacer at "
                f"{os.path.dirname(origin)}; a C++ edit needs `pixi run build`")
    else:
        # A namespace portion (no origin) or nothing: `import pacer` will not give the bindings.
        line = ("bindings not built in this tree, and this env has no deployed pacer package "
                "either — run `pixi run build`")
    NOTE = line
    sys.stderr.write(line + "\n")
