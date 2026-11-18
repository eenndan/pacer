"""CI's EXPORT TIER cannot skip a test it should have run.

Owner ruling 2026-11-18: "run the slow export tests only when export code changes, always on main".
The video-export suites carry the CTest label `export` (tests/CMakeLists.txt, `_export` and the
`videotoolbox.*` checks); on a pull request, CI leaves them out unless the pull request changes a
path in .github/export-tier-paths.txt (.github/export-tier.sh decides; .github/workflows/ci.yml
wires it). A false "run" costs minutes, a false "skip" lets a broken export reach main, so this
file holds the skip side shut:

  * EVERY EXPORT TEST IS LABELLED OR EXEMPT BY NAME. A tests/test_export_*.py is in `_export` or in
    EXEMPT below with its reason, never both, and every `videotoolbox.*` registration carries the
    label. A new export suite cannot ride on every PR unnoticed, nor slip out of the tier silently.
  * THE PATH LIST COVERS WHAT THE LABELLED TESTS IMPORT. Every import of every labelled test file —
    module-level and function-local, transitively through studio/ and the tests' helpers — is a
    file that must match a line of the path list; `import pacer` (the compiled core) needs the
    build lines. This is a static closure: a module reached only through importlib or a subprocess
    is not in it (none is today), which is why the list is whole directories, not this closure.
  * THE SCRIPT CI RUNS DECIDES AS THIS FILE READS THE LIST, on synthetic diffs: an overlay or a
    `_signal.py` change runs the tier; a docs-only or a non-export-test change skips it; an empty
    diff runs it. A `studio/coaching.py` change RUNS it: studio/session.py imports coaching, and
    every export test builds a Session.
  * WHAT CTEST REGISTERED AGREES (run under ctest): `-L export` is exactly the expected set, and it
    and `-LE export` partition the suite.

Qt-free, no bindings; ctest only for the last check, through PACER_CTEST_COMMAND.

Run: python tests/test_ci_tiers.py
"""
import ast
import fnmatch
import json
import os
import re
import shutil
import subprocess
from collections import deque

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_TESTS = os.path.join(_REPO, "tests")
_PATHS = os.path.join(_REPO, ".github", "export-tier-paths.txt")
_SCRIPT = os.path.join(_REPO, ".github", "export-tier.sh")
_CMAKE = os.path.join(_TESTS, "CMakeLists.txt")
_CI = os.path.join(_REPO, ".github", "workflows", "ci.yml")

#: test_export_* files that are NOT in the tier, each with why. They run on every pull request.
EXEMPT = {
    "test_export_data": "the CSV and session-report writers: text, no video, ~2 s in CI",
    "test_export_disclosures": "what the CSV and share card say about the ideal: text, ~3 s in CI",
}
#: What `import pacer` loads: the compiled core, rebuilt from these.
_CORE_LINES = ("pacer/*", "bindings/*", "3rdparty/*", "CMakeLists.txt")


def _patterns(path=_PATHS) -> list[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = re.sub(r"\s", "", line.split("#", 1)[0])
            if line:
                out.append(line)
    return out


def _covered(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns)


def _cmake_list(name: str) -> list[str]:
    with open(_CMAKE, encoding="utf-8") as f:
        text = f.read()
    m = re.search(rf"^set\({name}\b(.*?)\)", text, re.S | re.M)
    assert m, f"tests/CMakeLists.txt has no set({name} ...)"
    return re.sub(r"#[^\n]*", "", m.group(1)).split()


def _videotoolbox_registrations() -> list[str]:
    with open(_CMAKE, encoding="utf-8") as f:
        return [f"videotoolbox.{c}" for c in
                re.findall(r"^add_videotoolbox_test\(\s*\S+\s+(\S+)\s*\)", f.read(), re.M)]


# ---- the import closure ---------------------------------------------------------------------
def _module_file(dotted: str):
    base = os.path.join(_REPO, *dotted.split("."))
    for cand in (os.path.join(base, "__init__.py"), base + ".py"):
        if os.path.isfile(cand):
            return cand
    return None


def _imported(path: str):
    """Every dotted name a file imports, anywhere in it (function-local too: they run as well),
    with `from X import y` also yielding X.y, which is a module when y is a submodule."""
    pkg = os.path.relpath(os.path.dirname(path), _REPO).split(os.sep)
    in_tests = pkg == ["tests"]
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = pkg[: len(pkg) - node.level + 1]
                mod = ".".join(base + ([node.module] if node.module else []))
            else:
                mod = node.module or ""
            names = [mod] + [f"{mod}.{a.name}" for a in node.names]
        else:
            continue
        for name in names:
            # A test imports its helpers (`_synthetic`) off the tests/ directory on sys.path.
            if in_tests and _module_file(f"tests.{name}"):
                name = f"tests.{name}"
            yield name


def _closure(starts: list[str]) -> tuple[dict[str, str | None], bool]:
    """{repo-relative file: the file that imported it}, and whether anything imports `pacer`."""
    parent: dict[str, str | None] = {s: None for s in starts}
    todo, core = deque(starts), False
    while todo:
        path = todo.popleft()
        for name in _imported(path):
            parts = name.split(".")
            core = core or parts[0] == "pacer"
            if parts[0] not in ("studio", "tests"):
                continue
            for i in range(1, len(parts) + 1):        # a package's __init__ runs first
                f = _module_file(".".join(parts[:i]))
                if f and f not in parent:
                    parent[f] = path
                    todo.append(f)
    rel = {os.path.relpath(k, _REPO): (os.path.relpath(v, _REPO) if v else None)
           for k, v in parent.items()}
    return rel, core


def _chain(closure: dict, f: str) -> str:
    out = [f]
    while closure[out[-1]]:
        out.append(closure[out[-1]])
    return " <- ".join(out)


def _labelled_files() -> list[str]:
    return [os.path.join(_TESTS, f"{n}.py") for n in _cmake_list("_export")]


# ---- the checks -----------------------------------------------------------------------------
def test_every_export_test_is_labelled_or_exempt():
    labelled = set(_cmake_list("_export"))
    files = {n[:-3] for n in os.listdir(_TESTS) if re.fullmatch(r"test_export_\w+\.py", n)}
    problems = [f"{n}: neither in tests/CMakeLists.txt's `_export` nor in EXEMPT here — label it "
                "(it then runs on a PR only when the PR touches the export paths) or exempt it "
                "with a reason (it then runs on every PR)" for n in sorted(files - labelled - set(EXEMPT))]
    problems += [f"{n}: both labelled and exempt" for n in sorted(labelled & set(EXEMPT))]
    problems += [f"EXEMPT names {n}, which is not a tests/{n}.py" for n in sorted(set(EXEMPT) - files)]
    problems += [f"EXEMPT gives {n} no reason" for n, why in EXEMPT.items() if not why.strip()]
    with open(_CMAKE, encoding="utf-8") as f:
        text = f.read()
    m = re.search(r"function\(add_videotoolbox_test.*?endfunction\(\)", text, re.S)
    assert m, "tests/CMakeLists.txt lost add_videotoolbox_test"
    if not re.search(r"LABELS\s+\"[^\"]*\bexport\b", m.group(0)):
        problems.append("add_videotoolbox_test no longer sets LABELS export: the videotoolbox.* "
                        "checks would run on every PR")
    assert _videotoolbox_registrations(), "no add_videotoolbox_test line was found"
    assert not problems, "the export tier's label is incomplete:\n  " + "\n  ".join(problems)
    print(f"test_every_export_test_is_labelled_or_exempt OK ({len(labelled)} labelled files + "
          f"{len(_videotoolbox_registrations())} videotoolbox.*, {len(EXEMPT)} exempt)")


def _uncovered(patterns: list[str]) -> list[str]:
    closure, core = _closure(_labelled_files())
    missing = [f"{f}   (imported via {_chain(closure, f)})"
               for f in sorted(closure) if not _covered(f, patterns)]
    if core:
        missing += [f"the build line {line!r} (the labelled tests import pacer, the compiled core)"
                    for line in _CORE_LINES if line not in patterns]
    return missing


def test_the_path_list_covers_everything_the_labelled_tests_import():
    patterns = _patterns()
    for p in patterns:
        assert not set(p) & set("?[]{}!"), (
            f"{p!r}: only `*` is a wildcard here (bash [[ == ]] and fnmatch agree on it alone)")
        literal = p.split("*", 1)[0]
        assert os.path.exists(os.path.join(_REPO, literal)) or os.path.isdir(
            os.path.dirname(os.path.join(_REPO, literal))), f"{p!r} matches nothing in the repo"
    missing = _uncovered(patterns)
    assert not missing, (
        "a labelled export test imports a file no line of .github/export-tier-paths.txt matches, so "
        "a PR changing only it would skip the tests that exercise it — add a line:\n  "
        + "\n  ".join(missing))
    closure, _ = _closure(_labelled_files())
    app = [f for f in closure if f.startswith("studio" + os.sep) and f.endswith(".py")
           and os.sep + "dev" + os.sep not in f]
    print(f"test_the_path_list_covers_everything_the_labelled_tests_import OK ({len(closure)} files "
          f"in the closure, {len(app)} of them app modules; {len(patterns)} lines)")


def test_the_guard_fails_when_a_line_is_dropped():
    """The negative control: the closure check is not vacuous. Without the app line, or without the
    overlay's coverage, it names the files and the chain that reaches them."""
    patterns = _patterns()
    without_app = [p for p in patterns if p != "studio/*.py"]
    missing = _uncovered(without_app)
    for f in ("studio/_signal.py", "studio/export_video.py", "studio/overlay/broadcast.py",
              "studio/coaching.py"):
        assert any(m.startswith(f.replace("/", os.sep) + " ") for m in missing), (
            f"dropping studio/*.py did not report {f}: the closure check is not looking")
    assert any("the build line" in m for m in _uncovered([p for p in patterns if p != "pacer/*"]))
    print(f"test_the_guard_fails_when_a_line_is_dropped OK (without studio/*.py: {len(missing)} "
          "files reported)")


#: Synthetic diffs -> the verdict CI must reach.
_DIFFS = {
    ("studio/overlay/broadcast.py",): "run",
    ("studio/_signal.py",): "run",
    ("studio/coaching.py",): "run",              # session.py imports it; every export test loads one
    ("docs/ACCURACY.md", "changes/cool-x.md", "README.md"): "skip",
    ("tests/test_coaching.py",): "skip",
    ("tests/test_export_data.py",): "run",       # over-match: the glob is test_export_*.py
    ("studio/docs/refused-2026-09.md",): "skip",
    ("tests/_synthetic.py",): "run",
    ("pixi.lock",): "run",
    (".github/workflows/ci.yml",): "run",
    ("pacer/laps/laps.cpp",): "run",
    (): "run",                                   # nothing read: cannot tell
}


def test_the_ci_script_decides_as_the_list_reads():
    bash = shutil.which("bash")
    assert bash, "no bash: CI's decision script cannot be exercised here"
    patterns = _patterns()
    for diff, want in _DIFFS.items():
        proc = subprocess.run([bash, _SCRIPT], input="".join(p + "\n" for p in diff),
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode == 0, f"{diff}: the script exited {proc.returncode}: {proc.stderr}"
        lines = proc.stdout.strip().splitlines()
        assert len(lines) == 1, f"{diff}: the script printed {lines}, not one verdict line"
        got = lines[0].split(":", 1)[0]
        assert got == want, f"{diff}: the script said {lines[0]!r}, expected {want}"
        if diff:
            assert (want == "run") == any(_covered(p, patterns) for p in diff), (
                f"{diff}: this file and the script read the list differently")
    print(f"test_the_ci_script_decides_as_the_list_reads OK ({len(_DIFFS)} synthetic diffs)")


def test_ci_wires_the_tier_only_into_pull_requests():
    with open(_CI, encoding="utf-8") as f:
        text = f.read()
    step = re.search(r"- name: \"?Export tier.*?(?=\n      - name:)", text, re.S)
    assert step, "ci.yml has no `Export tier` step"
    body = step.group(0)
    assert "if: github.event_name == 'pull_request'" in body, (
        "the tier step must run only on a pull request: main, tags, manual and weekly runs run all")
    assert ".github/export-tier.sh" in body and "-LE export" in body
    assert "GITHUB_STEP_SUMMARY" in body, "a skipped tier must say so in the run's summary"
    test = re.search(r"- name: Test \(ctest.*?(?=\n      - name:)", text, re.S)
    assert test and "steps.tier.outputs.ctest_args" in test.group(0), (
        "the Test step no longer takes the tier's ctest arguments")
    assert text.index("Export tier") < text.index("- name: Test (ctest"), (
        "the tier is decided after the test step ran")
    print("test_ci_wires_the_tier_only_into_pull_requests OK")


def test_ctest_registered_the_label_as_expected():
    ctest, build = os.environ.get("PACER_CTEST_COMMAND"), os.environ.get("PACER_CTEST_DIR")
    if not (ctest and build):
        print("test_ctest_registered_the_label_as_expected: not under ctest — run it through ctest "
              "for this check (the other checks above ran)")
        return

    def names(*args):
        proc = subprocess.run([ctest, "--test-dir", build, "--show-only=json-v1", *args],
                              capture_output=True, text=True, timeout=120)
        assert proc.returncode == 0, proc.stderr[-2000:]
        return {t["name"] for t in json.loads(proc.stdout)["tests"]}

    every, inside, outside = names(), names("-L", "export"), names("-LE", "export")
    want = set(_cmake_list("_export")) | set(_videotoolbox_registrations())
    assert inside == want, (f"ctest -L export differs from the expected set: extra "
                            f"{sorted(inside - want)}, missing {sorted(want - inside)}")
    assert not inside & outside and inside | outside == every, "-L / -LE export do not partition"
    print(f"test_ctest_registered_the_label_as_expected OK ({len(inside)} + {len(outside)} = "
          f"{len(every)} registrations)")


if __name__ == "__main__":
    test_every_export_test_is_labelled_or_exempt()
    test_the_path_list_covers_everything_the_labelled_tests_import()
    test_the_guard_fails_when_a_line_is_dropped()
    test_the_ci_script_decides_as_the_list_reads()
    test_ci_wires_the_tier_only_into_pull_requests()
    test_ctest_registered_the_label_as_expected()
    print("\nOK: every export test is labelled or exempt, the CI path list covers what they import, "
          "and CI's script decides as the list reads")
