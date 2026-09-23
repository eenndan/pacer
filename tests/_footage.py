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
coverage, and a typo that quietly skipped would be the same no-op this exists to end.

THE DEFAULTS ARE THE DESKTOP WORKING SET (G2, 2026-09-23), chosen per check by what the check
NEEDS — the recordings and the reason for each are in `studio/dev/footage.py`:
  * `recording()` — `PACER_GOLDEN_MP4`, default MK_18_09_26: the checks that hold on ANY recording
    (the real renders, the chaptered render, the two Pedal-band checks) and the golden dump. MK is
    chaptered and is D24's own circuit.
  * `pair()` — `PACER_GOLDEN_MP4` + `PACER_GOLDEN_REF_MP4`, default SD_19_09_26 + Sandown 3h: the
    compare proof needs TWO recordings of ONE track, and MK is the only recording of its track.
  * `recording_sets()` — `PACER_IDEAL_TABLE_MP4`, default EVERY ROW of the ideal-lap table, and
    `directory()` — `PACER_MEASURED_FIGURES_DIR`, default the Desktop: the checks that re-measure a
    PUBLISHED table use exactly the recordings its rows name, found where the table says they are.
    A variable set to one row (or one folder) still re-measures just that.
Before G2 every default still named D24, on purpose, so all fourteen checks were reported skipped
until the tables were re-measured on the working set (T16b) — a default quietly moved to another
recording would have changed what every published number meant.

WHERE THEY RUN. `pixi run test-fast` sets `PACER_FOOTAGE_DEFAULTS=off`: in that run a default counts
as absent, so each check is reported SKIPPED by name, with a reason that says where it does run —
`pixi run test-footage` (just these) and `pixi run test` (the pre-PR gate, everything). A variable
the operator SET is still used there: `off` turns off the defaults, not the checks.

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

# `off` makes every DEFAULT recording count as absent in this run (pixi run test-fast sets it). Unset
# or blank uses them; any other value fails, so a misspelt `of` cannot quietly run or skip anything.
DEFAULTS_ENV = "PACER_FOOTAGE_DEFAULTS"
_DEFERRED = ("this run does not use the working-set defaults ({env}=off, which `pixi run test-fast` "
             "sets) — `pixi run test-footage` runs it on {what}")


class FootageMissing(Exception):
    """A real-footage check cannot run for want of its recording. Never caught by the check."""

    def __init__(self, reason: str):
        super().__init__(
            f"{reason}. This is a real-footage check: list it in its file's FOOTAGE_CHECKS and "
            f"register it with add_footage_test in tests/CMakeLists.txt, so its absence is reported "
            f"as SKIPPED rather than read as a pass (tests/_footage.py)")
        self.reason = reason


def _defaults_in_use(what: str) -> None:
    """Raise `FootageMissing` when this run turned the defaults off; `what` is the default it would
    have used, named in the skip line."""
    raw = os.environ.get(DEFAULTS_ENV, "").strip()
    if raw == "off":
        raise FootageMissing(_DEFERRED.format(env=DEFAULTS_ENV, what=what))
    assert not raw, f"{DEFAULTS_ENV}={raw!r}: the one value it takes is 'off'"


def _named_file(env: str, default: str) -> str:
    path, named = _dev.resolve(env, default)
    if named:
        if os.path.isfile(path):
            return path
        raise AssertionError(
            f"{env}={os.environ.get(env)!r} names {path}, which is not a file here. A recording "
            f"you point a check at has to exist — unset {env} to report this check as skipped")
    _defaults_in_use(path)
    if os.path.isfile(path):
        return path
    raise FootageMissing(f"{path} is not on this machine ({env} is unset and that is its default)")


def recording() -> str:
    """The recording `PACER_GOLDEN_MP4` names (or its default, MK_18_09_26), which must exist."""
    return _named_file(_dev.RECORDING_ENV, _dev.RECORDING_DEFAULT)


def pair() -> tuple[str, str]:
    """(primary, reference): two DIFFERENT recordings of one track, which must both exist —
    `PACER_GOLDEN_MP4` and `PACER_GOLDEN_REF_MP4`, defaults SD_19_09_26 and Sandown 3h. The primary's
    default is not `recording()`'s: MK has no second recording of its track to pair with.

    Both are resolved before either is skipped on: a reference the operator NAMED that is missing
    fails even when the primary's default is absent too."""
    found, absent = [], None
    for env, default in ((_dev.RECORDING_ENV, _dev.PAIR_RECORDING_DEFAULT),
                         (_dev.REFERENCE_ENV, _dev.REFERENCE_DEFAULT)):
        try:
            found.append(_named_file(env, default))
        except FootageMissing as exc:
            absent = absent or exc
    if absent is not None:
        raise absent
    return found[0], found[1]


def recording_sets(env: str, what: str, defaults) -> list[list[str]]:
    """The chapter lists a published-table check re-measures, one per row.

    `env` set: the ONE comma-separated chapter list it names; a listed file that is missing FAILS.
    `env` unset: every list in `defaults` — chapter paths relative to the Desktop, where the table's
    rows say they are — all of which must be here; one absent is a SKIP naming what is missing (a
    table half-re-measured is not the table)."""
    raw = os.environ.get(env, "").strip()
    if raw:
        paths = [os.path.expanduser(p.strip()) for p in raw.split(",") if p.strip()]
        missing = [p for p in paths if not os.path.isfile(p)]
        assert paths and not missing, f"{env} names files that are not here: {missing or raw!r}"
        return [paths]
    root = os.path.expanduser(_dev.DESKTOP)
    sets = [[os.path.join(root, p) for p in chapter_list] for chapter_list in defaults]
    assert sets and all(sets), f"no default for {env} — the check would measure nothing"
    _defaults_in_use(f"{what} under {root}")
    missing = sorted({p for s in sets for p in s if not os.path.isfile(p)})
    if missing:
        raise FootageMissing(f"{env} is unset, and its default — {what} under {root} — is not all "
                             f"on this machine: {len(missing)} missing, e.g. {missing[0]}")
    return sets


def directory(env: str, what: str, needs) -> str:
    """The folder a published-table check finds its lap sets under: the one `env` names, else the
    Desktop. `needs` are the files (relative to that folder) the check will open. From a NAMED
    folder, a missing one FAILS; from the default, it is a SKIP naming what is missing."""
    needs = sorted(set(needs))
    assert needs, f"{env}: a check has to say which files it needs, or a bare Desktop would pass"
    raw = os.environ.get(env, "").strip()
    if raw:
        path = os.path.expanduser(raw)
        assert os.path.isdir(path), f"{env}={raw!r} names {path}, which is not a folder here"
        missing = [n for n in needs if not os.path.isfile(os.path.join(path, n))]
        assert not missing, f"{env}={raw!r}: {what} is not all there — missing {missing}"
        return path
    path = os.path.expanduser(_dev.DESKTOP)
    _defaults_in_use(f"{what} under {path}")
    missing = [n for n in needs if not os.path.isfile(os.path.join(path, n))]
    if missing:
        raise FootageMissing(f"{env} is unset, and its default — {what} under {path} — is not all on "
                             f"this machine: {len(missing)} missing, e.g. {missing[0]}")
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
