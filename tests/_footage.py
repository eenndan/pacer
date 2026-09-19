"""Real-footage checks: where one finds its recording, and how its ABSENCE is reported.

THE PROBLEM THIS FIXES (G1, 2026-09-19). Twelve checks in four test files re-measure something on
a real recording. Each one, finding no recording, printed a "skip" line and RETURNED — and its
file's runner then counted it as a pass: `ok  test_real_render_smoke_if_ffmpeg_and_media`, "ALL 84
export-video tests passed", CTest `Passed`. While `~/Desktop/D24` existed that was a convenience;
the day it was gone, every real-footage check in the repo became a no-op that reported green, and
nothing in any gate's output said so.

So a real-footage check is now its OWN CTest registration, `footage.<check>`, run as
`python tests/<file>.py --footage <check>`, with `SKIP_RETURN_CODE` set to `SKIP_RETURN_CODE`
below. Without its recording the check raises `FootageMissing`, `run` exits with that code, and
CTest prints the check by name under "The following tests did not run: … (Skipped)" — on every
run, CI included, which has no footage at all and so stays green. The file's ordinary run leaves
these checks out: they are not in its count, so they can no longer pad it.

WHICH ABSENCE IS A SKIP AND WHICH IS A FAILURE. A default that is not on this machine is a skip. A
recording the operator NAMED through a variable that is not there is a failure: they asked for real
coverage, and a typo that quietly skipped would be the same no-op this exists to end. That was
already the contract of `PACER_MEASURED_FIGURES_DIR` and `PACER_IDEAL_TABLE_MP4`; it is now
`PACER_GOLDEN_MP4`'s too.

THE VARIABLES (AGENTS.md, "Real-footage checks", has the table):
  * `PACER_GOLDEN_MP4` — THE recording, for every check that holds on any recording (the golden
    dump too). Default `~/Desktop/D24/GX020060.MP4`; see `studio/dev/footage.py` for why the
    default was not moved when D24 went. `PACER_GOLDEN_REF_MP4` names the second, different
    recording the one cross-recording check needs.
  * `PACER_IDEAL_TABLE_MP4` and `PACER_MEASURED_FIGURES_DIR` — the checks that re-measure a
    PUBLISHED table. They cannot take "a recording": each row is a named recording (and a chapter
    selection sibling discovery cannot express), so they keep their own variables and no default.

A check that raises `FootageMissing` from a file's ORDINARY run is not caught there — it fails that
file, naming itself. That is the tripwire for a new footage check nobody registered.
"""
from __future__ import annotations

import os
import sys
import traceback

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO not in sys.path:
    sys.path.insert(0, _REPO)

from studio.dev import footage as _dev  # noqa: E402

# tests/CMakeLists.txt sets this as SKIP_RETURN_CODE on every `add_footage_test` registration, and
# tests/test_footage_checks.py reads it back from CTest. 77 is the automake convention for "skipped".
SKIP_RETURN_CODE = 77
FLAG = "--footage"


class FootageMissing(Exception):
    """A real-footage check cannot run for want of its recording. Never caught by the check."""

    def __init__(self, reason: str):
        super().__init__(
            f"{reason}. This is a real-footage check: list it in its file's FOOTAGE_CHECKS and "
            f"register it with add_footage_test in tests/CMakeLists.txt, so its absence is reported "
            f"as SKIPPED rather than read as a pass (tests/_footage.py)")
        self.reason = reason


def _named_file(env: str, default: str) -> str:
    path, named = _dev.resolve(env, default)
    if os.path.isfile(path):
        return path
    if named:
        raise AssertionError(
            f"{env}={os.environ.get(env)!r} names {path}, which is not a file here. A recording "
            f"you point a check at has to exist — unset {env} to report this check as skipped")
    raise FootageMissing(f"{path} is not on this machine ({env} is unset and that is its default)")


def recording() -> str:
    """The recording `PACER_GOLDEN_MP4` names (or its default), which must exist."""
    return _named_file(_dev.RECORDING_ENV, _dev.RECORDING_DEFAULT)


def reference() -> str:
    """The second recording `PACER_GOLDEN_REF_MP4` names (or its default), which must exist."""
    return _named_file(_dev.REFERENCE_ENV, _dev.REFERENCE_DEFAULT)


def recording_list(env: str, what: str) -> list[str]:
    """A comma-separated list of chapter files `env` names. No default: unset is a skip, and a
    listed file that is missing is a failure."""
    raw = os.environ.get(env, "").strip()
    if not raw:
        raise FootageMissing(f"{env} is not set — it names {what}")
    paths = [os.path.expanduser(p.strip()) for p in raw.split(",") if p.strip()]
    missing = [p for p in paths if not os.path.isfile(p)]
    assert not missing, f"{env} names files that are not here: {missing}"
    return paths


def directory(env: str, what: str) -> str:
    """The folder `env` names. No default: unset is a skip, and a named folder that is missing is a
    failure."""
    raw = os.environ.get(env, "").strip()
    if not raw:
        raise FootageMissing(f"{env} is not set — it names {what}")
    path = os.path.expanduser(raw)
    assert os.path.isdir(path), f"{env}={raw!r} names {path}, which is not a folder here"
    return path


def requested(argv: list[str] | None = None) -> bool:
    """Whether this process was started as one footage registration (`--footage <check>`)."""
    return FLAG in (sys.argv if argv is None else argv)


def run(checks, argv: list[str] | None = None) -> int:
    """Run the ONE real-footage check `--footage <name>` names; return the process exit code.

    0 when it ran and passed, 1 when it failed, `SKIP_RETURN_CODE` when it raised `FootageMissing`,
    2 when the name is not one of `checks` (a registration that points at nothing must not pass).
    The SKIPPED line names the check and the reason, for anyone reading the test's own output."""
    argv = sys.argv if argv is None else argv
    by_name = {fn.__name__: fn for fn in checks}
    i = argv.index(FLAG)
    name = argv[i + 1] if i + 1 < len(argv) else ""
    fn = by_name.get(name)
    if fn is None:
        print(f"FAIL {FLAG} {name!r}: not a real-footage check of this file "
              f"(it has {sorted(by_name)})")
        return 2
    try:
        fn()
    except FootageMissing as exc:
        print(f"SKIPPED {name}: {exc.reason}")
        return SKIP_RETURN_CODE
    except Exception as exc:  # noqa: BLE001 — any failure of the check is the check failing
        traceback.print_exc()
        print(f"FAIL {name}: {exc}")
        return 1
    print(f"ok  {name}")
    return 0
