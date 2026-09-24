"""NO FAILURE REPORT BYPASSES THE SESSION LOG (RISK-6): no `print()` in an `except` handler, and
none in a function handed a caught exception, anywhere in the shipped app (`studio/`, not
`studio/dev/`).

WHY. #368 gave the app a session log (`<app-support>/logs/pacer.log`, studio/logsetup.py)
because a `.app` launched from Finder has its stdout and stderr on /dev/null. The log hooks
`logging`, `sys.excepthook`, `threading.excepthook` and Qt's own messages — not stdout. The
2026-09-23 board review's AST audit (`print_audit.py`) then found 54 `except` handlers that
reported ONLY through `print()`: the user's own library index, session records and marks that
could not be restored, forgotten or cleared; a g-meter or rotation channel disabled at load; a
session view that could not be built; a session record not saved. The load-failure slot printed
its detail line and nothing else, and the two worst failures (the view build, an export) printed
their tracebacks with `traceback.print_exc()`. In the build a user runs, all of it went nowhere.
They report through `logging.getLogger(__name__)` now; this file keeps it that way.

THE THREE SHAPES HELD:
  * a `print(...)` anywhere inside an `except` handler — a nested function or lambda included,
    since it runs on the handler's behalf;
  * the same bypass spelled differently: `traceback.print_exc()` / `print_exception()` /
    `print_stack()` / `print_tb()` / `print_last()`, or `sys.stdout.write` / `sys.stderr.write`;
  * a `print(...)` in a function HANDED the caught exception — a parameter annotated `Exception`
    or `BaseException`: the worker thread caught it, the slot is what reports it
    (`_on_load_failed`, `_on_reference_load_failed`, `_recover_from_build_failure`,
    `Renderer._raise_if_disk_full`).

`studio/dev/` is exempt: its probes and CLIs print by design, to the terminal that ran them.

Every rule is first shown to FIRE on a planted source (the negative control), and the walk is
shown to reach the shipped modules, so a checker that matches nothing cannot pass.

Run: python tests/test_log_completeness.py
"""
import ast
import os
import sys

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
_STUDIO = os.path.join(_REPO, "studio")
_EXEMPT = os.path.join(_STUDIO, "dev")

_TRACEBACK_PRINTERS = {"print_exc", "print_exception", "print_stack", "print_tb", "print_last"}
_EXCEPTION_TYPES = {"Exception", "BaseException"}


def _bypass(call: ast.Call) -> str | None:
    """What `call` is when it writes past `logging` ('print', 'traceback.print_exc',
    'sys.stderr.write', …), else None."""
    f = call.func
    if isinstance(f, ast.Name) and (f.id == "print" or f.id in _TRACEBACK_PRINTERS):
        return f.id
    if not isinstance(f, ast.Attribute):
        return None
    if f.attr in _TRACEBACK_PRINTERS and isinstance(f.value, ast.Name) and f.value.id == "traceback":
        return f"traceback.{f.attr}"
    stream = f.value
    if (f.attr == "write" and isinstance(stream, ast.Attribute) and stream.attr in ("stdout", "stderr")
            and isinstance(stream.value, ast.Name) and stream.value.id == "sys"):
        return f"sys.{stream.attr}.write"
    return None


def _is_exception_annotation(ann: ast.expr | None) -> bool:
    """`Exception`, `BaseException`, either `| None`, `Optional[...]`, or the quoted form."""
    if ann is None:
        return False
    if isinstance(ann, ast.Constant) and isinstance(ann.value, str):
        try:
            ann = ast.parse(ann.value, mode="eval").body
        except SyntaxError:
            return False
    names = {n.id for n in ast.walk(ann) if isinstance(n, ast.Name)}
    names |= {n.attr for n in ast.walk(ann) if isinstance(n, ast.Attribute)}
    return bool(names & _EXCEPTION_TYPES)


def violations(source: str, filename: str = "<planted>") -> list[tuple[int, str]]:
    """(line, what) for every write past `logging` in `source`, by the three shapes above."""
    found: set[tuple[int, str]] = set()
    for node in ast.walk(ast.parse(source, filename)):
        if isinstance(node, ast.ExceptHandler):
            for sub in ast.walk(node):
                if isinstance(sub, ast.Call) and (what := _bypass(sub)):
                    found.add((sub.lineno, f"{what}() inside an except handler"))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
            if any(_is_exception_annotation(p.annotation) for p in params):
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Call) and (what := _bypass(sub)):
                        found.add((sub.lineno, f"{what}() in {node.name}(), which is handed a "
                                               f"caught exception"))
    return sorted(found)


def shipped_modules() -> list[str]:
    """Every .py file of the shipped app: `studio/` and its packages, minus `studio/dev/`."""
    out = []
    for dirpath, dirnames, filenames in os.walk(_STUDIO):
        dirnames[:] = sorted(d for d in dirnames if d != "__pycache__"
                             and os.path.join(dirpath, d) != _EXEMPT)
        out += [os.path.join(dirpath, f) for f in sorted(filenames) if f.endswith(".py")]
    return out


# One of each shape, and one of each thing that is NOT a violation. `# FLAG` marks the lines the
# checker must report — exactly those, no more.
_PLANTED = '''
import logging, sys, traceback
_log = logging.getLogger(__name__)


def fine(path):
    print("loading", path)                    # outside any handler: not this file's rule
    try:
        open(path)
    except OSError as exc:
        _log.warning("could not open %s (%r)", path, exc)


def fine_slot(exc: Exception):
    _log.warning("failed", exc_info=exc)


def planted(path):
    try:
        open(path)
    except OSError as exc:
        print(f"studio: could not open ({exc!r}).", flush=True)  # FLAG
    except Exception:
        traceback.print_exc()  # FLAG
        sys.stderr.write("boom")  # FLAG
    try:
        open(path)
    except ValueError:
        try:
            open(path)
        except KeyError:
            print("nested")  # FLAG
        later = lambda: print("deferred")  # FLAG
        return later


def planted_slot(token: int, exc: BaseException | None):
    print(f"reference not loaded: {exc}")  # FLAG


def planted_quoted(exc: "Exception"):
    print(exc)  # FLAG
'''


def test_each_shape_is_caught_on_a_planted_source():
    """The negative control: the checker reports exactly the planted lines."""
    expected = [i for i, line in enumerate(_PLANTED.splitlines(), 1) if line.endswith("# FLAG")]
    got = violations(_PLANTED)
    assert [line for line, _ in got] == expected, (
        f"the checker reported lines {[line for line, _ in got]}, the plant has {expected}:\n"
        + "\n".join(f"  {line}: {what}" for line, what in got))
    kinds = {what.split("(")[0] for _, what in got}
    assert kinds == {"print", "traceback.print_exc", "sys.stderr.write"}, kinds
    print(f"test_each_shape_is_caught_on_a_planted_source OK ({len(got)} planted, all caught)")


def test_the_walk_reaches_the_shipped_app_and_skips_dev():
    mods = shipped_modules()
    rel = {os.path.relpath(p, _REPO) for p in mods}
    for must in ("studio/app.py", "studio/session.py", "studio/library_controller.py",
                 "studio/logsetup.py", "studio/workers.py"):
        assert must in rel, f"{must} is not in the walk: {sorted(rel)[:10]}…"
    under_dev = sorted(p for p in rel if p.startswith("studio/dev/"))
    assert not under_dev, f"the walk reached studio/dev/: {under_dev[:5]}"
    assert os.path.isdir(_EXEMPT), "studio/dev/ moved: the exemption above exempts nothing"
    print(f"test_the_walk_reaches_the_shipped_app_and_skips_dev OK ({len(mods)} modules)")


def test_no_failure_report_in_the_shipped_app_bypasses_the_log():
    bad = []
    for path in shipped_modules():
        with open(path, encoding="utf-8") as f:
            source = f.read()
        bad += [f"{os.path.relpath(path, _REPO)}:{line}: {what}"
                for line, what in violations(source, path)]
    assert not bad, (
        f"{len(bad)} failure report(s) go to stdout/stderr, which a Finder-launched .app sends "
        f"to /dev/null and the session log never sees. Report through "
        f"`logging.getLogger(__name__)` (warning / error, `exc_info=True` for a broad catch):\n  "
        + "\n  ".join(bad))
    print("test_no_failure_report_in_the_shipped_app_bypasses_the_log OK")


def _run_all():
    tests = [
        test_each_shape_is_caught_on_a_planted_source,
        test_the_walk_reaches_the_shipped_app_and_skips_dev,
        test_no_failure_report_in_the_shipped_app_bypasses_the_log,
    ]
    defined = sorted(k for k, v in globals().items() if k.startswith("test_") and callable(v))
    assert sorted(t.__name__ for t in tests) == defined, "a test_ function is not in _run_all"
    for t in tests:
        t()
    print(f"\nALL {len(tests)} LOG-COMPLETENESS TESTS PASSED")


if __name__ == "__main__":
    sys.exit(_run_all())
