"""Entry point: `python -m studio [GoPro.MP4 ...]` (also `pixi run studio`)."""

import importlib.util
import os
import sys

# THE FIRST LAUNCH IS SLOW, AND IT USED TO BE SILENT (QA EVAL-7, 2026-09-26). On a fresh clone,
# `pixi run studio -- --demo` printed the build's progress and then nothing for 14.5 s, until the
# session log's first line. It is the import below, in an environment nothing has run in yet —
# timed on a fresh clone (2026-09-27): numpy 2.3 s, Qt's core modules 5.1 s, Qt Multimedia 2.8 s,
# pyqtgraph 4.8 s, the rest 2.0 s; 17.0 s, against 0.6 s the second time. Compiling bytecode is
# not the cost (every module, into an empty PYTHONPYCACHEPREFIX: 1.5 s); loading the native
# libraries for the first time is. So a first launch says so before it pays for it. "First" is
# read off this package's bytecode cache, which a fresh clone lacks and the first launch writes
# (`cache_from_source` honours PYTHONPYCACHEPREFIX): a later launch says nothing, and neither does
# a frozen .app. tests/test_first_launch_says_so.py.
FIRST_LAUNCH_NOTE = "studio: first launch — Qt loads for the first time, about 15 s…"


def say_if_first_launch() -> bool:
    """Print FIRST_LAUNCH_NOTE when this package has never been compiled here; True if it did."""
    app = os.path.join(os.path.dirname(os.path.abspath(__file__)), "app.py")
    if getattr(sys, "frozen", False) or os.path.exists(importlib.util.cache_from_source(app)):
        return False
    print(FIRST_LAUNCH_NOTE, flush=True)
    return True


say_if_first_launch()

# Absolute, not `from .app`: the .app's bootloader runs this file as a top-level script with no
# parent package (packaging/pacer.spec's Analysis entry), where a relative import raises (REG2-1).
from studio.app import main  # noqa: E402  (after the note: this import IS the wait it announces)

if __name__ == "__main__":
    sys.exit(main())
