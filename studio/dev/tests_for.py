"""Which test files to run for a change to some studio modules — an inner-loop aid, not a gate.

    pixi run python -m studio.dev.tests_for studio/data_quality.py [studio/other.py …]

WHY. studio/README.md's module map names each module's HOME test, the file its change extends
first, and nothing more: `data_quality` alone is imported by about 40 test files, so running the
home test after editing it checks a fraction of what reads it. This prints, per run:

  * the test files that import the modules DIRECTLY (an AST walk over every `import` in
    tests/test_*.py, at any depth, so a function-level import counts), as a ready
    `ctest -R '^(…)$'` and a pytest line;
  * how many test files reach them TRANSITIVELY, through other studio modules or the tests/
    helpers (`_synthetic`, `_qtapp`, …), which is what a full `pixi run test` would exercise;
  * the summed time of each set, when this checkout has a source for it: CTest's own per-test
    cost record, `build/Release/Testing/Temporary/CTestCostData.txt` (a running average of past
    runs, in seconds, early exits included, so a floor; printed with the record's date). Without
    one it prints no time: a timing it cannot cite, it does not invent.

It reads only source files, imports nothing it lists (stdlib only, no Qt, no pacer), and changes
nothing. Imports it cannot see — `importlib.import_module("studio.x")`, a subprocess running
`-m studio.x` — are not counted, so the transitive set is a floor. `pixi run test` stays the gate.
"""
from __future__ import annotations

import argparse
import ast
import functools
import os
import sys
import time

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
COST_FILE = os.path.join("build", "Release", "Testing", "Temporary", "CTestCostData.txt")


def module_name(path: str, repo: str = _REPO) -> str:
    """`studio/overlay/ghost.py` -> `studio.overlay.ghost`; a package's `__init__.py` -> the package."""
    rel = os.path.relpath(os.path.abspath(path), repo)
    if not rel.endswith(".py") or rel.startswith(".."):
        raise ValueError(f"{path}: not a .py file inside the repo")
    parts = rel[:-3].split(os.sep)
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def imported_names(source: str, own: str = "") -> set[str]:
    """Every dotted module name an `import` in `source` can bind, at any depth. For
    `from a.b import c` both `a.b` and `a.b.c` are returned (c may be a submodule); a relative
    import is resolved against `own`, the importing module's dotted name."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                pkg = own.split(".")[:-node.level] if own else []
                base = ".".join(pkg + ([base] if base else []))
            if base:
                names.add(base)
            names.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
    return names


def _sources(repo: str) -> dict[str, tuple[str, str]]:
    """{dotted name: (kind, path)} for every studio module and every tests/*.py. Tests import
    their helpers by bare name (`import _synthetic`), since tests/ is on their path."""
    out: dict[str, tuple[str, str]] = {}
    for root, dirs, files in os.walk(os.path.join(repo, "studio")):
        dirs[:] = [d for d in dirs if d != "__pycache__"]
        for f in files:
            if f.endswith(".py"):
                p = os.path.join(root, f)
                out[module_name(p, repo)] = ("studio", p)
    tests = os.path.join(repo, "tests")
    for f in sorted(os.listdir(tests)):
        if f.endswith(".py"):
            out[f[:-3]] = ("test" if f.startswith("test_") else "helper", os.path.join(tests, f))
    return out


@functools.lru_cache(maxsize=4)  # one parse of ~350 files per process (~2 s)
def graph(repo: str = _REPO) -> tuple[dict[str, set[str]], dict[str, tuple[str, str]]]:
    """{module: the known modules it imports}, over studio/ and tests/; and the source map."""
    srcs = _sources(repo)
    edges: dict[str, set[str]] = {}
    for name, (_kind, path) in srcs.items():
        with open(path, encoding="utf-8") as f:
            text = f.read()
        try:
            got = imported_names(text, name)
        except SyntaxError:
            got = set()
        # Importing `a.b.c` runs the packages `a` and `a.b` first: they are imported too.
        got |= {".".join(g.split(".")[:i]) for g in got for i in range(1, g.count(".") + 1)}
        edges[name] = {g for g in got if g in srcs and g != name}
    return edges, srcs


def tests_for(paths: list[str], repo: str = _REPO) -> dict:
    """The direct and transitive test files for the modules at `paths` (see the module doc)."""
    targets = {module_name(p, repo) for p in paths}
    edges, srcs = graph(repo)
    unknown = sorted(t for t in targets if t not in srcs)
    if unknown:
        raise ValueError(f"not a studio or tests module: {', '.join(unknown)}")
    tests = sorted(n for n, (kind, _p) in srcs.items() if kind == "test")
    direct = [t for t in tests if edges[t] & targets]
    reach = set(targets)                      # every module that leads to a target
    changed = True
    while changed:
        changed = False
        for name, deps in edges.items():
            if name not in reach and deps & reach:
                reach.add(name)
                changed = True
    transitive = [t for t in tests if t in reach and t not in targets]
    return {"targets": sorted(targets), "direct": direct, "transitive": transitive}


def costs(repo: str = _REPO) -> dict[str, float] | None:
    """{registration: seconds} from CTest's cost record, or None when this checkout has none."""
    path = os.path.join(repo, COST_FILE)
    if not os.path.isfile(path):
        return None
    out: dict[str, float] = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.startswith("---"):
                break                           # below it: the names of the last failures
            parts = line.split()
            if len(parts) == 3:
                try:
                    out[parts[0]] = float(parts[2])
                except ValueError:
                    pass
    return out


def _seconds(names: list[str], cost: dict[str, float] | None) -> str:
    if cost is None:
        return "no time: this checkout has no " + COST_FILE
    known = [cost[n] for n in names if n in cost]
    gap = len(names) - len(known)
    return (f"~{sum(known):.0f} s summed (CTest's recorded average"
            + (f"; {gap} with no record)" if gap else ")"))


def report(paths: list[str], repo: str = _REPO) -> str:
    got = tests_for(paths, repo)
    cost = costs(repo)
    direct, transitive = got["direct"], got["transitive"]
    lines = [f"{', '.join(got['targets'])}:",
             f"  {len(direct)} test files import it directly — {_seconds(direct, cost)}"]
    if direct:
        lines += [f"  pixi run ctest --test-dir build/Release -R '^({'|'.join(direct)})$' "
                  "--output-on-failure",
                  "  pixi run python -m pytest " + " ".join(f"tests/{t}.py" for t in direct)]
    lines.append(f"  {len(transitive)} test files reach it transitively (through studio modules "
                 f"or tests/ helpers; direct included) — {_seconds(transitive, cost)}")
    if cost is not None:
        # Measured 2026-11-27 on the owner's checkout: a record last written 11 days earlier put
        # test_coaching at 0.004 s, the average of every run CTest ever started, quick refusals
        # included. So the date goes beside the sum, and the sum is called what it is.
        when = time.strftime("%Y-%m-%d", time.localtime(os.path.getmtime(
            os.path.join(repo, COST_FILE))))
        lines.append(f"  Times: {COST_FILE} as of {when}, CTest's running average over every "
                     "past run (early exits too): a floor, not a forecast.")
    lines.append("  An inner-loop aid, not a gate: `pixi run test` is the gate.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m studio.dev.tests_for", description=__doc__.split(
        "\n")[0])
    ap.add_argument("files", nargs="+", help="studio/<module>.py files (or tests/ helpers)")
    args = ap.parse_args(argv)
    try:
        print(report(args.files))
    except ValueError as exc:
        print(f"tests_for: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
