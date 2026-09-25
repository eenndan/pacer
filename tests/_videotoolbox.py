"""VideoToolbox checks: the export paths only the Apple media engine runs, each its own CTest entry.

THE PROBLEM THIS FIXES (HEALTH-4, 2026-09-25). The owner's exports run h264_videotoolbox, the
`-hwaccel videotoolbox` decode and, since #412, the decode relay that switches itself on over that
hardware decode. CI's runner has no VideoToolbox. The checks of those paths each opened with
`if not ev.videotoolbox_usable(): print("skip …"); return`, so in CI they reported PASSED having
run nothing; the relay tests forced `_relay_ok` on over a SOFTWARE decode; and `pixi run test-fast`
leaves the whole of `test_export_video` out. So the path he exports through was checked only by a
full local `pixi run test`, and nothing in any gate's output said where it had not run.

So each such check is now a registration of its own, `videotoolbox.<check>`, run as
`python tests/<file>.py --videotoolbox <check>` with SKIP_RETURN_CODE 77 (tests/CMakeLists.txt,
`add_videotoolbox_test`). Where VideoToolbox is missing the check raises `VideoToolboxMissing`,
`run` exits 77, and CTest lists it as SKIPPED, by name, in CI. On this Mac it runs, in `test-fast`
as well as `test`. A file's ordinary run leaves its VIDEOTOOLBOX_CHECKS out, so they cannot pad
its count; the software half of a split check stays there, where CI runs it.
tests/test_videotoolbox_checks.py holds the registrations to the declarations, one to one.
"""
from __future__ import annotations

import sys
import traceback

# tests/CMakeLists.txt sets this as SKIP_RETURN_CODE on every `add_videotoolbox_test` registration.
SKIP_RETURN_CODE = 77
FLAG = "--videotoolbox"


class VideoToolboxMissing(Exception):
    """This machine cannot run a VideoToolbox check. Never caught by the check."""


def need(available: bool, what: str) -> None:
    """Raise `VideoToolboxMissing` unless `available`; `what` names the missing piece."""
    if not available:
        raise VideoToolboxMissing(f"no {what} on this machine (CI's runner has no VideoToolbox)")


def requested(argv: list[str] | None = None) -> bool:
    """Whether this process was started as one VideoToolbox registration (`--videotoolbox <check>`)."""
    return FLAG in (sys.argv if argv is None else argv)


def run(checks, argv: list[str] | None = None, call=None) -> int:
    """Run the ONE check `--videotoolbox <name>` names; return the exit code: 0 passed, 1 failed,
    `SKIP_RETURN_CODE` when it raised `VideoToolboxMissing`, 2 for a name that is not one of
    `checks` (a registration that points at nothing must not pass). `call(fn)` runs it — a file
    passes its own wrapper when a check needs one (the export files' `_Restore`)."""
    argv = sys.argv if argv is None else argv
    by_name = {fn.__name__: fn for fn in checks}
    i = argv.index(FLAG)
    name = argv[i + 1] if i + 1 < len(argv) else ""
    fn = by_name.get(name)
    if fn is None:
        print(f"FAIL {FLAG} {name!r}: not a VideoToolbox check of this file (it has {sorted(by_name)})")
        return 2
    try:
        (call or (lambda f: f()))(fn)
    except VideoToolboxMissing as exc:
        print(f"SKIPPED {name}: {exc}")
        return SKIP_RETURN_CODE
    except Exception as exc:  # noqa: BLE001 — any failure of the check is the check failing
        traceback.print_exc()
        print(f"FAIL {name}: {exc}")
        return 1
    print(f"ok  {name}")
    return 0
