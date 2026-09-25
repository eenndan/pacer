"""pytest over tests/, for LOCAL iteration: `pixi run python -m pytest tests/test_x.py -k <name>`.

CTest runs every file as a plain script (`python tests/test_x.py`), and that is the gate; pytest is
not (ARCH-7: studio/docs/refused-2026-09.md §18 has the measurements). This file makes a by-hand
pytest run as safe as that script run, and makes it run the same tests:

* THE JAIL. `studio/app_support.py` jails a process CTest started (PACER_APP_SUPPORT_JAIL is on
  every registration) or one whose entry script is a file in tests/. Under pytest the entry script
  is pytest's own `__main__.py`, so without this line a by-hand run resolves the owner's REAL
  app-support directory. It is set here, before pytest imports any test module.
  tests/test_app_support_jail.py pins it.
* What a registration's environment gives each file: offscreen Qt, and the built bindings first on
  sys.path (the repo's C++ `pacer/` directory would otherwise shadow them).
* What each file's own runner does: leave out its FOOTAGE_CHECKS, SOAK_CHECKS and VIDEOTOOLBOX_CHECKS
  (each runs as its own `footage.` / `soak.` / `videotoolbox.<check>` registration; the soak alone
  is ~113 s), and run a test that
  names `monkeypatch_restore` inside its module's `_Restore()` (the three export test files).
"""
import os
import sys

import pytest

os.environ.setdefault("PACER_APP_SUPPORT_JAIL", "1")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_BINDINGS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "bindings", "pacer")
if _BINDINGS not in sys.path:
    sys.path.insert(0, _BINDINGS)


def _by_registration(item) -> bool:
    """A check its file's runner leaves out because CTest runs it under its own name."""
    module = getattr(item, "module", None)
    own = (getattr(module, "FOOTAGE_CHECKS", ()) + getattr(module, "SOAK_CHECKS", ())
           + getattr(module, "VIDEOTOOLBOX_CHECKS", ()))
    return getattr(item, "obj", None) in own


def pytest_collection_modifyitems(config, items):
    left_out = [item for item in items if _by_registration(item)]
    if left_out:
        config.hook.pytest_deselected(items=left_out)
        items[:] = [item for item in items if not _by_registration(item)]


@pytest.fixture
def monkeypatch_restore(request):
    """What the export files' runners do for a test that names this argument: run it inside the
    module's `_Restore()`, which puts back every module global its mocks replace."""
    with request.module._Restore():
        yield request.module.monkeypatch_restore
