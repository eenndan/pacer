"""studio/dev/tests_for.py lists the test files a change to a studio module should run.

WHY A NEW FILE. The tool is new (cool/tests-for-module) and no existing test file covers a dev
tool's import graph: tests/test_layering.py holds the LAYERS of studio's imports, not which tests
read a module. studio/README.md's module map names one home test per module, and `data_quality`
alone is imported by about 40 test files; the tool is the answer to "which of them do I run?".

What is pinned, on a synthetic tree with known answers:
  * a function-level import counts as direct (the tool walks every `import`, not module level);
  * `from studio import a`, `import studio.a as x`, `from studio.a import f` and a relative
    `from .. import a` all name `studio.a`;
  * transitive reach goes through studio modules AND tests/ helpers (`import _helper`);
  * importing `studio.pkg.mod` imports the package `studio.pkg` too;
  * CTest's cost record is summed when present (its failed-test list below `---` ignored), and
    without one the report prints no time at all, rather than one it cannot cite;
  * a path that is not a module is refused with exit 2.
And on the real tree: every test file studio/README.md's module map names for a module is one the
tool finds reaching it, so the tool and the map cannot drift apart unnoticed.

Run: python tests/test_tests_for.py
"""
import contextlib
import io
import os
import re
import sys
import tempfile

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

from studio.dev import tests_for  # noqa: E402

_TREE = {
    "studio/__init__.py": "",
    "studio/a.py": "X = 1\n",
    "studio/b.py": "from studio import a\n",
    "studio/pkg/__init__.py": "",
    "studio/pkg/mod.py": "from .. import a\n",
    "studio/pkg/other.py": "Y = 2\n",
    "tests/_helper.py": "import studio.a as alias\n",
    "tests/test_fn_level.py": "def test_x():\n    from studio.a import X\n    assert X\n",
    "tests/test_via_b.py": "import studio.b\n",
    "tests/test_via_helper.py": "import _helper\n",
    "tests/test_pkg.py": "import studio.pkg.other\n",
    "tests/test_nothing.py": "import os\n",
}


def _tree(cost: str | None = None) -> str:
    root = tempfile.mkdtemp(prefix="pacer-tests-for-")
    for rel, text in _TREE.items():
        path = os.path.join(root, *rel.split("/"))
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    if cost is not None:
        path = os.path.join(root, tests_for.COST_FILE)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as f:
            f.write(cost)
    return root


def _rm(root: str) -> None:
    import shutil
    shutil.rmtree(root, ignore_errors=True)


def test_direct_and_transitive_on_a_tree_with_known_answers():
    root = _tree()
    try:
        got = tests_for.tests_for([os.path.join(root, "studio", "a.py")], root)
        assert got["direct"] == ["test_fn_level"], got
        assert got["transitive"] == ["test_fn_level", "test_via_b", "test_via_helper"], got
        pkg = tests_for.tests_for([os.path.join(root, "studio", "pkg", "__init__.py")], root)
        assert pkg["targets"] == ["studio.pkg"] and pkg["direct"] == ["test_pkg"], pkg
        mod = tests_for.tests_for([os.path.join(root, "studio", "pkg", "mod.py")], root)
        assert mod["direct"] == [] and mod["transitive"] == [], mod
    finally:
        _rm(root)
    print("test_direct_and_transitive_on_a_tree_with_known_answers OK")


def test_the_report_sums_ctests_cost_record_or_prints_no_time():
    cost = ("test_fn_level 4 1.5\ntest_via_b 2 2.0\ntest_via_helper 9 10.25\n"
            "---\ntest_via_b\n")
    root = _tree(cost)
    try:
        text = tests_for.report([os.path.join(root, "studio", "a.py")], root)
    finally:
        _rm(root)
    assert "1 test files import it directly — ~2 s summed" in text, text
    assert "3 test files reach it transitively" in text and "~14 s summed" in text, text
    assert "-R '^(test_fn_level)$'" in text and "pytest tests/test_fn_level.py" in text, text
    assert "not a gate" in text and "Times: " in text and " as of 20" in text, text
    root = _tree()
    try:
        text = tests_for.report([os.path.join(root, "studio", "a.py")], root)
    finally:
        _rm(root)
    assert " s summed" not in text and text.count("no time: this checkout has no") == 2, text
    print("test_the_report_sums_ctests_cost_record_or_prints_no_time OK")


def test_a_path_that_is_not_a_module_is_refused():
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = tests_for.main([os.path.join(_REPO, "studio", "no_such_module.py")])
    assert rc == 2 and "not a studio or tests module" in err.getvalue(), (rc, err.getvalue())
    print("test_a_path_that_is_not_a_module_is_refused OK")


def test_every_home_test_on_the_module_map_reaches_its_module():
    """The real tree: each `test_…` in a module row's Test cell of studio/README.md is among the
    test files the tool finds reaching that module."""
    with open(os.path.join(_REPO, "studio", "README.md"), encoding="utf-8") as f:
        rows = [ln for ln in f if ln.startswith("| [")]
    problems, checked = [], 0
    for row in rows:
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", row.strip().strip("|"))]
        module = re.match(r"\[[^\]]+\]\(([^)]+\.py)\)", cells[0]).group(1)
        homes = re.findall(r"`(test_\w+)`", cells[-1])
        if not homes:
            continue
        got = tests_for.tests_for([os.path.join(_REPO, "studio", module)])
        reach = set(got["transitive"])
        problems += [f"{module}: {h} does not import it, directly or through anything"
                     for h in homes if h not in reach]
        checked += len(homes)
    assert not problems, "\n".join(problems)
    print(f"test_every_home_test_on_the_module_map_reaches_its_module OK ({checked} home tests)")


if __name__ == "__main__":
    test_direct_and_transitive_on_a_tree_with_known_answers()
    test_the_report_sums_ctests_cost_record_or_prints_no_time()
    test_a_path_that_is_not_a_module_is_refused()
    test_every_home_test_on_the_module_map_reaches_its_module()
    print("ALL OK")
