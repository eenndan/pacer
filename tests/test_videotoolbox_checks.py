"""Every VideoToolbox check is a registration of its own, and one that cannot run never reads as a pass.

The export paths the owner uses — h264_videotoolbox, the ProRes VideoToolbox encoder, the decode relay
over `-hwaccel videotoolbox` — cannot run in CI, and their checks used to return early and report
PASSED there (HEALTH-4, 2026-09-25). Each is now `videotoolbox.<check>` (tests/_videotoolbox.py),
SKIPPED by name without VideoToolbox. This holds the three things that make that true:
  * a file's VIDEOTOOLBOX_CHECKS and the `add_videotoolbox_test` lines are the same set — a check
    declared but not registered would never run anywhere; one registered but not declared exits 2;
  * `run` maps a check that cannot run to the skip code, a failure to 1 and a pass to 0;
  * `pixi run test-fast` does not exclude them: on the dev Mac they are part of the inner loop.
"""
import ast
import os
import re
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _videotoolbox  # noqa: E402


def _declared() -> set[tuple[str, str]]:
    """(file, check) for every name in a module-level `VIDEOTOOLBOX_CHECKS = (...)`, read with ast."""
    found = set()
    for name in sorted(os.listdir(HERE)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        tree = ast.parse(open(os.path.join(HERE, name), encoding="utf-8").read())
        defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name)
                    and node.targets[0].id == "VIDEOTOOLBOX_CHECKS"):
                for elt in node.value.elts:
                    assert isinstance(elt, ast.Name) and elt.id in defs, (
                        f"{name}: VIDEOTOOLBOX_CHECKS names {ast.unparse(elt)}, not a function of it")
                    found.add((name[:-3], elt.id))
    return found


def _cmake() -> str:
    return open(os.path.join(HERE, "CMakeLists.txt"), encoding="utf-8").read()


def _registered(cmake: str) -> set[tuple[str, str]]:
    return set(re.findall(r"^add_videotoolbox_test\((\w+) (\w+)\)", cmake, re.M))


def test_every_videotoolbox_check_is_registered_under_its_own_name():
    declared, registered = _declared(), _registered(_cmake())
    assert len(declared) >= 6, f"the VideoToolbox checks have gone missing: {sorted(declared)}"
    assert declared == registered, (
        f"declared, not registered (it would never run): {sorted(declared - registered)}; "
        f"registered, not declared (it would exit 2): {sorted(registered - declared)}")
    body = re.search(r"function\(add_videotoolbox_test file check\)(.*?)endfunction\(\)",
                     _cmake(), re.S)
    assert body and f"SKIP_RETURN_CODE {_videotoolbox.SKIP_RETURN_CODE}" in body.group(1), (
        "add_videotoolbox_test must set the skip code, or a check that cannot run FAILS in CI")
    jail = _cmake().index("KEEP THIS LAST")
    assert all(_cmake().index(f"add_videotoolbox_test({f} {c})") < jail for f, c in registered)
    print(f"ok {len(declared)} VideoToolbox checks, each registered once, skip code set")


def test_a_check_that_cannot_run_is_skipped_never_passed():
    def cannot():
        _videotoolbox.need(False, "VideoToolbox session")

    def broken():
        raise AssertionError("wrong picture")

    def fine():
        pass
    checks = (cannot, broken, fine)
    codes = {fn.__name__: _videotoolbox.run(checks, ["x", _videotoolbox.FLAG, fn.__name__])
             for fn in checks}
    assert codes == {"cannot": _videotoolbox.SKIP_RETURN_CODE, "broken": 1, "fine": 0}, codes
    assert _videotoolbox.run(checks, ["x", _videotoolbox.FLAG, "typo"]) == 2
    print(f"ok exit codes {codes}")


def test_test_fast_runs_them():
    tasks = tomllib.load(open(os.path.join(ROOT, "pyproject.toml"), "rb"))["tool"]["pixi"]["tasks"]
    cmd = tasks["test-fast"]["cmd"]
    m = re.search(r"-E '([^']*)'", cmd)
    excluded = re.compile(m.group(1)) if m else None
    hit = [c for _f, c in _declared() if excluded and excluded.search(f"videotoolbox.{c}")]
    assert not hit, f"test-fast's -E excludes VideoToolbox checks, so the inner loop skips: {hit}"
    print("ok test-fast includes every videotoolbox.* check")


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as exc:  # noqa: BLE001
            failed += 1
            import traceback
            traceback.print_exc()
            print(f"FAIL {t.__name__}: {exc}")
    if failed:
        print(f"\n{failed}/{len(tests)} videotoolbox-check tests FAILED")
        sys.exit(1)
    print(f"\nALL {len(tests)} videotoolbox-check tests passed")
