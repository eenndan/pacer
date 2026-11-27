"""The committed GoPro clips in `3rdparty/gpmf-parser`, and the ONE way a test asks for them.

THE PROBLEM THIS FIXES (2026-11-27). A fresh worktree — every agent's — has the submodule
directory and nothing in it. The ~18 files that read its clips handled that five ways: a named
failure, a bare FileNotFoundError deep in a test, a "SKIP" line in a run that then PASSED, a
loader error, and two HANGS: `test_studio_features` and `test_plan_in_reach` built a window whose
load failed, and `app.py`'s `_on_load_failed` opened a modal box that waits forever offscreen —
CTest sat out its 480 s timeout. CI always checks the submodule out (`submodules: recursive`), so
a skip there could only ever hide a broken checkout: a missing clip is a FAILURE here, never a
skip, and it fails at import, before any window exists, saying what to run.

Every file that reads a clip calls `require` (or `require_file`) at module level, before it
builds anything.
"""
from __future__ import annotations

import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SUBMODULE = os.path.join(_REPO, "3rdparty", "gpmf-parser")
SAMPLES = os.path.join(SUBMODULE, "samples")
FIX = "run `git submodule update --init --recursive`"


class SamplesMissing(AssertionError):
    """The submodule is not checked out (or not at a commit that has this file)."""


def require_file(rel: str) -> str:
    """The absolute path of `rel` inside `3rdparty/gpmf-parser`; raises SamplesMissing if absent."""
    path = os.path.join(SUBMODULE, rel)
    if os.path.isfile(path):
        return path
    try:
        empty = not os.listdir(SUBMODULE)
    except OSError:
        empty = True
    if empty:
        raise SamplesMissing(f"3rdparty/gpmf-parser is empty — {FIX} (this test reads {rel})")
    raise SamplesMissing(f"3rdparty/gpmf-parser has no {rel} at {path}: the submodule is not at "
                         f"the commit this tree pins — {FIX}")


def require(name: str) -> str:
    """The absolute path of the sample clip `name` (`hero6.mp4`, …); raises SamplesMissing if absent."""
    return require_file(os.path.join("samples", name))


def require_dir() -> str:
    """The samples directory, for a test that walks every clip in it; raises if it is not there."""
    require("hero6.mp4")   # the clip every walker reads: present means the checkout is there
    return SAMPLES
