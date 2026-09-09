"""One place for the dev harnesses to jail every `~/Library/Application Support/pacer` seam.

`studio.dev.golden_session_dump` learned this the expensive way and states the rule in its own
block: **the invariant is not "redirect the seams that are written to", it is "redirect them
all"**. It had diverted `library` (the one the load *writes*) and left `track_db` (the one the
load only *reads*) live — and two runs of identical code disagreed on 15,655 of 35,082 leaves,
45%, because a track had been saved in between.

The window-building harnesses had the same hole for the same reason. `ui_capture`, `_smoke` and
`media_capture` each diverted `library` alone, so their output depended on the operator's own
preferences. Measured on `GX020060.MP4` before this module existed: a capture reads `prefs` six
times at construction — `lap_panel_tab`, `grid_sizes`, `excluded_visible`, `speed_unit`,
`colorblind_palette` (`app.py:266-275`) and `map_key_collapsed` (`map_view.py:1092`) — and
**all six PNGs differ** byte-for-byte between an operator with defaults and one running mph +
the colour-blind palette. Nothing was written; every one of those was a read. A visual-QA tool
whose pixels change with who ran it is not a regression tool.

`tests/test_golden_hermetic.py` pins the seam list here against an AST scan of `studio/`, so a
module that grows a fifth `_app_support_dir` turns the suite red instead of quietly escaping.
"""
from __future__ import annotations

import os
import tempfile
from typing import NamedTuple

# The user's real app-support dir, spelled exactly as the store modules spell it. Used only to
# recognise an un-diverted seam — nothing here ever writes to it.
_REAL_DIR = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "pacer")


class Jail(NamedTuple):
    """Where the seams now point, and whether WE made that directory.

    `created=False` means an outer jail was adopted — a caller that cleans up after itself
    (`_smoke` rmtree's its dir) must not delete a directory it does not own.
    """

    dir: str
    created: bool


def divert_app_support(prefix: str) -> Jail:
    """Point every studio app-support seam at one throwaway temp dir; return it.

    Call BEFORE building any window: the load-time library upsert and the prefs reads in
    `StudioWindow.__init__` both resolve the seam at call time, so patching afterwards is too
    late for exactly the reads that matter.

    If the seams are ALREADY diverted (an outer QA write-jail, a test fixture), the existing
    directory is adopted rather than replaced — moving the app's state out from under a jail
    that is watching the first directory would defeat it. The check is behavioural (where does
    the seam resolve to *now*), not a `__name__ == "<lambda>"` sniff, so it holds for a jail
    installed with a `def`, a `partial` or a `monkeypatch`.
    """
    from studio import demo, library, prefs, track_db

    current = library._app_support_dir()
    already_diverted = os.path.abspath(current) != os.path.abspath(_REAL_DIR)
    target = current if already_diverted else tempfile.mkdtemp(prefix=prefix)
    for _mod in (demo, library, prefs, track_db):
        _mod._app_support_dir = lambda t=target: t  # type: ignore[attr-defined]
    return Jail(target, not already_diverted)
