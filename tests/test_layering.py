"""Layering-contract test — the load-bearing architecture invariant, in BOTH directions.

**Direction 1 (pacer).** Only the four data/pipeline modules — `session`, `load`, `ingest`,
`tracks` — may `import pacer` (the C++ core). Every studio VIEW / controller / helper stays
**pacer-free** and goes through `Session`.

**Direction 2 (Qt).** The mirror rule, stated in `studio/_signal.py` ("this module is numpy-only
so [it] can be shared without dragging a pacer (or Qt) import anywhere") and in ~6 `studio/README`
rows ("pacer-free AND Qt-free", "Qt-free pure-numpy core", …): the analysis/pipeline layer imports
no Qt, so it stays importable, testable and reasonable-about headlessly. Only the view /
Qt-infrastructure modules in `ALLOWED_QT` may reach PySide6.

Both contracts hold today with zero violations, but until this file only the FIRST was enforced:
an agent could `import pacer` into a view, or `from PySide6.QtCore import QTimer` into
`session.py` / `stats.py` / `corners.py`, and every other gate (build, ruff, the offscreen widget
tests) would still pass — silently eroding the layering from whichever side happened to be
unguarded.

Two ways a module reaches Qt, and both are pinned:
  * DIRECTLY — it names PySide6 (or pyqtgraph/shiboken6, which are Qt) in an import. `ALLOWED_QT`.
  * TRANSITIVELY — it imports a studio module that does. A direct-only scan would wave through
    `session.py` importing `theme`, which is the same breakage by one more hop. `QT_REACHING`.

Both sets are pinned by EXACT equality (like `ALLOWED` below), so an allow-list that quietly stops
being true fails too and the sets stay honest.

This test walks the source with `ast` (no import side-effects, no Qt, no pacer, no telemetry file,
sub-100 ms). `studio/dev/` tools are standalone scripts, not part of the app, and are intentionally
out of scope for both directions — they are Qt/pacer harnesses by nature (6 of them import Qt:
`_smoke`, `denoise_check`, `make_icon`, `media_capture`, `spike_video_sync`, `ui_capture`).

**The map.** `studio/README.md` is the module map AGENTS.md sends every agent to, and it states
this contract per module (its Imports column), so it is held to the same sets: one row per
`studio/*.py` (a module FAMILY — the Stats page's `stats_*.py` — shares its head's row, each member
linked there by name), the layer each row claims is the one pinned here, the test it names exists,
and the map stays a map — it had grown to 130,636 characters of measurement history before ARCH-9
moved that to `studio/docs/module-notes.md`. Run:
    python tests/test_layering.py
"""
import ast
import os
import re

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STUDIO = os.path.join(_REPO, "studio")

# The ONLY top-level studio/*.py modules permitted to import the pacer core (the data/pipeline
# layer). If a deliberate new pipeline module joins them, add it here in the same PR.
ALLOWED = {"session", "load", "ingest", "tracks"}

# Qt lives behind more than one distribution name: pyqtgraph, shiboken6 and qtawesome ARE Qt
# (importing any of them pulls a binding in — qtawesome via qtpy), so a module can't dodge the
# contract by importing the charting or icon layer instead. `theme` imports qtawesome lazily and
# already imports PySide6 outright, so listing it closes a future hole, not a live one.
QT_ROOTS = {"PySide6", "shiboken6", "pyqtgraph", "qtawesome", "PyQt5", "PyQt6", "qtpy"}

# The ONLY top-level studio/*.py modules permitted to import Qt DIRECTLY — the view layer plus the
# three Qt-infrastructure modules. Everything absent from this set is data/analysis/persistence and
# must stay Qt-free. Three members are not windows and are here on purpose:
#   * export_video / export_compare / share_card — offline, event-loop-free RENDERERS. They use
#     QPainter/QImage as a rasterizer to burn overlays into an MP4 / a PNG; they construct no
#     window and run off the UI thread. Qt is their drawing library, not their UI.
#     `export_compare` is `export_video`'s two-pane subclass and is here for exactly the reason
#     that one is: it paints a frame, and it is the module `export_video` would have grown into.
#   * workers — QThread/QObject wrappers (QtCore only), the seam that carries loads off the UI thread.
#   * export_controller — the export cluster lifted out of `app` (§7.1). It is VIEW code by every
#     test this file applies: it opens the file dialogs, the options modal, the progress dialog and
#     the failure/completion boxes, and it holds a back-reference to the window it belongs to. It is
#     here for the same reason `app` is, and it made this set BIGGER by one only because a chunk of
#     `app` moved out — nothing gained a Qt import that did not already have one.
# `theme` and `widgets` are the shared Qt style/primitive layer every view sits on.
#   * session_record_dialog — the session-record editor (the form for conditions / tyres / setup).
#     A dialog like `library_dialog` and by the same test: it builds widgets and opens a confirm.
#     Its STORE, `session_record`, is Qt-free persistence and is deliberately absent from this set.
#   * provenance_panel — the "inspect this number" panel. The SAME split, and it is the whole point
#     of that feature rather than a convention it happens to follow: `provenance` is the Qt-free
#     value layer (windows, raw-fix tables, the method sentences, the re-derivation) and is absent
#     from this set, while this module only renders one of those values into labels and grids. A
#     provenance panel that reached for the data itself would be reverse-engineering where a number
#     came from, which is exactly what the feature exists to replace.
#   * marks_panel — the Marks page + the mark editor. The SAME split again, and the one where it
#     matters most: `marks` is the Qt-free half (the store, the schema, the chapter-relative anchor
#     arithmetic and the derived marks) and is deliberately absent from this set, while this module
#     renders a list and emits intents — it owns no store and writes no file. A view that reached
#     into ~/Library/Application Support would be a view that can lose a driver's notes.
#   * track_dialog — the saved-tracks manager (rename / delete a circuit). The SAME split once more,
#     and for the highest stakes in the app: `track_db` is the Qt-free store that owns the refusals,
#     the .bak copy and the atomic write, and is absent from this set, while this module renders a
#     list, asks for a name and shows the store's refusal. It deletes the user's own durable
#     history, so the code that decides whether a delete is allowed must not live in a widget.
ALLOWED_QT = {
    "app", "central_view", "coaching_panel", "command_palette", "export_compare",
    "export_controller",
    "export_video", "gmeter_overlay", "help_dialog", "lap_table", "library_controller",
    "library_dialog", "map_view",
    "marks_panel", "overlays", "player_pane", "plots_view", "provenance_panel",
    "session_record_dialog", "share_card", "stats_braking", "stats_common", "stats_ideal",
    "stats_panel", "stats_straights", "stats_trust", "theme", "track_dialog", "video_view",
    "widgets", "workers",
}

# Every module from which Qt is REACHABLE through studio's own import graph = ALLOWED_QT plus the
# three modules that reach it in one more hop. All three are deliberate and measured:
#   * __main__ — the entry point; it imports `app` by definition.
#   * compare_controller — needs `theme.format_delta_run` / `theme.delta_colour` (the app's single
#     Δ formatter) and video_view's `PaneSpec` dataclass; its OWN code is Qt-free.
#   * map_render — pure numpy except for one constant, `from .theme import MAP_RAINBOW_N`.
# The last two are the reason this second check exists: a direct-import scan calls them Qt-free
# (their own source names no Qt), but `import studio.map_render` really does load PySide6. Shrink
# this set — don't grow it: anything NEW here is a data-layer module that just gained a view import.
QT_REACHING = ALLOWED_QT | {"__main__", "compare_controller", "map_render"}

_CONTRACT = ("the Qt-free data core (studio/_signal.py's module doc + the studio/README rows): "
             "analysis / pipeline / persistence modules import no Qt so they stay headless")


def _modules() -> list[str]:
    """Every top-level studio module name (studio/dev is out of scope — see the module doc)."""
    return sorted(fn[:-3] for fn in os.listdir(_STUDIO) if fn.endswith(".py"))


def _parse(name: str) -> ast.Module:
    path = os.path.join(_STUDIO, name + ".py")
    return ast.parse(open(path, encoding="utf-8").read(), filename=path)


def _imports_pacer(path: str) -> bool:
    """True if the module imports `pacer` (or a `pacer.` submodule) at any depth."""
    tree = ast.parse(open(path, encoding="utf-8").read(), filename=path)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(a.name == "pacer" or a.name.startswith("pacer.") for a in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod == "pacer" or mod.startswith("pacer."):
                return True
    return False


def _type_checking_only(tree: ast.Module) -> set[int]:
    """`id()`s of the nodes inside `if TYPE_CHECKING:` blocks. Those imports never execute, so a
    view type annotated for the type-checker is NOT a runtime Qt dependency (scrub_controller
    imports MapView/PlotsView/VideoView this way and genuinely stays Qt-free at import time)."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            name = (test.id if isinstance(test, ast.Name) else
                    test.attr if isinstance(test, ast.Attribute) else None)
            if name == "TYPE_CHECKING":
                for stmt in node.body:
                    out.update(id(sub) for sub in ast.walk(stmt))
    return out


def _import_names(node: ast.AST) -> list[str]:
    """The dotted module names one import statement pulls in, with every studio spelling
    normalised to `studio.<module>`. All five reach the same module and all five must be seen —
    `from studio import theme` is the one an agent is most likely to write, and it hides the
    module name in `node.names` where a naive `node.module` read drops it entirely
    (`test_the_import_scanner_sees_every_studio_spelling` pins this)."""
    if isinstance(node, ast.Import):                     # import studio.theme
        return [a.name for a in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level:                                   # from . import theme / from .theme import X
            if node.module:
                return ["studio." + node.module]
            return ["studio." + a.name for a in node.names]
        if node.module == "studio":                      # from studio import theme
            return ["studio." + a.name for a in node.names]
        return [node.module or ""]                       # from studio.theme import X / anything else
    return []


def _scan(name: str, mods: set[str]) -> tuple[bool, set[str]]:
    """`(imports_qt_directly, studio_modules_imported_at_runtime)` for one studio module.
    Function-level (deferred) imports COUNT — a module that reaches a view only from inside a
    function still owns that dependency. `if TYPE_CHECKING:` imports do not (they never run)."""
    tree = _parse(name)
    skip = _type_checking_only(tree)
    qt, deps = False, set()
    for node in ast.walk(tree):
        if id(node) in skip or not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for dotted in _import_names(node):
            head, _, rest = dotted.partition(".")
            if head in QT_ROOTS:
                qt = True
            elif head == "studio" and rest.split(".")[0] in mods:
                deps.add(rest.split(".")[0])
    return qt, deps - {name}


def test_only_the_data_layer_imports_pacer():
    importers = {
        fn[:-3]
        for fn in os.listdir(_STUDIO)
        if fn.endswith(".py") and fn != "__init__.py"
        and _imports_pacer(os.path.join(_STUDIO, fn))
    }
    extra = importers - ALLOWED      # a view / controller / helper reached into pacer
    missing = ALLOWED - importers    # an allow-listed module no longer imports pacer
    assert not extra, (
        f"pacer-free contract broken: {sorted(extra)} import the pacer core but are NOT the "
        f"data/pipeline layer {sorted(ALLOWED)}. Keep views/controllers/helpers pacer-free (go "
        f"through Session), or — if this is a deliberate new pipeline module — add it to ALLOWED.")
    assert not missing, (
        f"allow-list drift: {sorted(missing)} no longer import pacer — drop it from ALLOWED so the "
        f"contract stays exact.")
    print(f"test_only_the_data_layer_imports_pacer OK — pacer imported by exactly {sorted(importers)}")


def test_only_the_view_layer_imports_qt():
    """The reverse direction: exactly `ALLOWED_QT` may name PySide6/pyqtgraph/shiboken6."""
    mods = set(_modules())
    importers = {m for m in mods if _scan(m, mods)[0]}   # `__init__` included: it is a leaf by rule
    extra = importers - ALLOWED_QT      # a data/analysis module reached into Qt
    missing = ALLOWED_QT - importers    # an allow-listed view no longer imports Qt
    assert not extra, (
        f"Qt-free contract broken: studio/{sorted(extra)[0]}.py imports Qt but is not part of the "
        f"view layer {sorted(ALLOWED_QT)} (offenders: {sorted(extra)}). This breaks {_CONTRACT}. "
        f"Move the Qt code into a view and keep the module numpy-only, or — if this really is a "
        f"new view / Qt-infrastructure module — add it to ALLOWED_QT in the same PR.")
    assert not missing, (
        f"allow-list drift: {sorted(missing)} no longer import Qt — drop them from ALLOWED_QT so "
        f"the contract stays exact (a stale allow-list silently re-opens the door).")
    print(f"test_only_the_view_layer_imports_qt OK — Qt imported by exactly {sorted(importers)}")


def test_the_data_core_does_not_reach_qt_through_a_studio_import():
    """Transitive closure: `import studio.<m>` must not load Qt for any data-layer module.

    The direct scan above cannot see this — `session.py` gaining `from . import theme` names no Qt
    at all yet drags the whole toolkit in. Walks studio's own import graph and pins the set of
    modules from which Qt is reachable."""
    mods = set(_modules())
    scanned = {m: _scan(m, mods) for m in mods}

    def reaches_qt(m: str, seen: set[str]) -> bool:
        if m in seen:
            return False
        seen.add(m)
        direct, deps = scanned[m]
        return direct or any(reaches_qt(d, seen) for d in deps)

    def why(m: str) -> list[str]:
        return sorted(d for d in scanned[m][1] if reaches_qt(d, set()))

    reaching = {m for m in mods if reaches_qt(m, set())}
    extra = reaching - QT_REACHING
    missing = QT_REACHING - reaching
    assert not extra, (
        "Qt-free contract broken transitively: " + "; ".join(
            f"studio/{m}.py imports {why(m)}, which import(s) Qt" for m in sorted(extra)) +
        f". The module names no Qt itself, but `import studio.{sorted(extra)[0]}` loads PySide6, "
        f"which breaks {_CONTRACT}. Take the value from a Qt-free module (or move it to one) — or, "
        f"if the module genuinely belongs to the view layer, add it to QT_REACHING in the same PR.")
    assert not missing, (
        f"allow-list drift: Qt is no longer reachable from {sorted(missing)} — drop them from "
        f"QT_REACHING so the contract stays exact.")
    print(f"test_the_data_core_does_not_reach_qt_through_a_studio_import OK — Qt reachable from "
          f"exactly {len(reaching)} modules, {sorted(reaching - ALLOWED_QT)} only indirectly")


def test_the_import_scanner_sees_every_studio_spelling():
    """A guard is only as good as its scanner, so pin the scanner itself.

    Python spells "this module imports studio.theme" five ways, and an earlier cut of
    `_import_names` read `node.module` for the absolute forms — which is the literal string
    `"studio"` for `from studio import theme`, hiding the module name in `node.names`. That one
    spelling therefore contributed NO edge: `from studio import theme` in `bests.py` passed this
    file green while `import studio.bests` really did load PySide6. It is also the spelling an
    agent writing new code is most likely to reach for. Each case below is a real breach in
    disguise; all five must normalise to the same edge."""
    cases = {
        "from . import theme": "relative, module in names",
        "from .theme import MAP_RAINBOW_N": "relative, module in .module",
        "from studio import theme": "absolute package form — the one that used to escape",
        "from studio.theme import MAP_RAINBOW_N": "absolute, module in .module",
        "import studio.theme": "plain import",
    }
    for src, why in cases.items():
        node = ast.parse(src).body[0]
        assert _import_names(node) == ["studio.theme"], (
            f"import scanner blind spot ({why}): `{src}` normalised to {_import_names(node)}, not "
            f"['studio.theme'] — a module could reach Qt through this spelling and the transitive "
            f"check above would never see the edge.")
    # Multiple names on one line, and a non-module attribute, both behave.
    both = ast.parse("from studio import theme, units").body[0]
    assert _import_names(both) == ["studio.theme", "studio.units"]
    attr = ast.parse("from studio import APP_NAME").body[0]      # a constant, not a module …
    assert _import_names(attr) == ["studio.APP_NAME"]            # … dropped later: not in `mods`
    print(f"test_the_import_scanner_sees_every_studio_spelling OK — {len(cases)} spellings, one edge")


_TESTS_DIR = os.path.join(_REPO, "tests")

# A runner that enumerates its tests from the module namespace rather than by name — a CALL of any
# of these — cannot strand one, so those files are exempt from the reachability rule below. A call,
# not a substring: the substring test exempted six explicit-runner files, this one among them (it
# names the spellings as data), and so let a test added here go uncalled (ARCH-7).
_AUTO_DISCOVERY = ("globals", "vars", "dir", "getmembers")


def _enumerates_its_namespace(tree) -> bool:
    """True iff the module calls `globals()` / `vars()` / `dir()` / `getmembers(...)` somewhere."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None) or getattr(node.func, "attr", "")
            if name in _AUTO_DISCOVERY and (name == "getmembers" or not node.args):
                return True
    return False


def _stranded_tests(path) -> list[str]:
    """Top-level `def test_*` in `path` that NOTHING outside the test bodies ever names.

    Every test file in this repo is a plain script run as `python tests/<file>.py` — nothing runs
    under pytest, so a test is executed only if its own file's runner CALLS it. A name is counted
    as referenced if it appears as a `Name` load or as a string literal (a table-driven runner may
    list its tests as strings) anywhere in the module EXCEPT inside a `test_*` body — a test that
    only calls itself is still stranded."""
    tree = ast.parse(open(path, encoding="utf-8").read())
    defs = [n.name for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")]
    if not defs:
        return []
    referenced = set()
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            continue
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                referenced.add(sub.id)
            elif isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                referenced.add(sub.value)
    return [d for d in defs if d not in referenced]


def test_every_declared_test_is_actually_reachable_from_its_runner():
    """A TEST NOBODY CALLS IS NOT A PASSING TEST, AND THE SUITE CANNOT TELL THE DIFFERENCE.

    `tests/CMakeLists.txt` runs each file as `python tests/<file>.py`. NOTHING here runs under
    pytest (the file's old `foreach(pytest IN ITEMS …)` loop only ever set PYTHONPATH), so
    collection-by-convention does not happen and a `def test_…` that the file's own `_run_all()`
    never calls simply never executes. It costs nothing, breaks nothing, and reports nothing; the
    suite still says 110/110.

    WHAT THIS CAUGHT, all four written as the regression test for a specific fix and none of them
    ever run: `test_ia01_corners_and_coaching_tabs_declare_different_scopes`,
    `test_the_dialogs_jump_buttons_are_not_clipped_at_its_own_default_size`,
    `test_l9_02_overlay_target_rect_agrees_with_the_dials_own_minimum` and
    `test_driving_group_states_the_coasting_instrument`. The last one did not even pass: wired up,
    it failed, because it substring-tested a raw sentence against an HTML document that escapes
    apostrophes. Three of the four guard a fix that is still correct — but nothing was checking."""
    stranded = {}
    scanned = 0
    for name in sorted(os.listdir(_TESTS_DIR)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        path = os.path.join(_TESTS_DIR, name)
        if _enumerates_its_namespace(ast.parse(open(path, encoding="utf-8").read())):
            continue          # runner enumerates the namespace; it cannot strand a test
        scanned += 1
        missing = _stranded_tests(path)
        if missing:
            stranded[name] = missing
    assert not stranded, (
        "test functions defined but never called by their file's runner — they never run:\n"
        + "\n".join(f"  {f}: {', '.join(v)}" for f, v in sorted(stranded.items())))
    print(f"test_every_declared_test_is_actually_reachable_from_its_runner OK — "
          f"{scanned} explicit-runner files, 0 stranded")


# pytest's own fixtures that stay on under tests/pytest.ini (`caplog` and `cache` go with the two
# plugins it turns off). Three files' runners pass a hand-made `monkeypatch` of pytest's shape.
_PYTEST_FIXTURES = {"monkeypatch", "tmp_path", "tmp_path_factory", "tmpdir", "tmpdir_factory",
                    "capsys", "capsysbinary", "capfd", "capfdbinary", "recwarn", "request",
                    "pytestconfig", "record_property", "record_testsuite_property"}


def _conftest_fixtures() -> set[str]:
    """What a pytest run can pass a test: tests/conftest.py's fixtures and pytest's own."""
    tree = ast.parse(open(os.path.join(_TESTS_DIR, "conftest.py"), encoding="utf-8").read())
    return _PYTEST_FIXTURES | {n.name for n in tree.body if isinstance(n, ast.FunctionDef)
                               and any("fixture" in ast.unparse(d) for d in n.decorator_list)}


def _pytest_parity_problems(name: str, src: str, fixtures: set[str]) -> list[str]:
    """Where `python -m pytest tests/<name>` and `python tests/<name>` would run different tests.

    pytest (tests/pytest.ini: `python_functions = test_*`) collects every module-level `test_*` the
    module ends up with, including one defined AFTER the `if __name__ == "__main__":` block and one
    imported from elsewhere, and every `class Test*`; it errors on a parameter no fixture provides.
    A `globals()` runner calls only what exists when `__main__` runs, and an explicit one what it
    names (the reachability check above)."""
    tree = ast.parse(src)
    main = next((n.lineno for n in tree.body if isinstance(n, ast.If)
                 and "__main__" in ast.unparse(n.test)), None)
    problems = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test_"):
            if main is not None and node.lineno > main:
                problems.append(f"{name}: {node.name} is defined after the __main__ block (line "
                                f"{main}), so the file's runner never calls it")
            params = [a.arg for a in node.args.posonlyargs + node.args.args]
            for arg in params[:len(params) - len(node.args.defaults)]:
                if arg not in fixtures:
                    problems.append(f"{name}: {node.name}({arg}) — tests/conftest.py has no "
                                    f"`{arg}` fixture, so pytest errors on it")
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            problems.append(f"{name}: class {node.name} — pytest collects it, no runner here does")
        elif isinstance(node, ast.ImportFrom):
            problems += [f"{name}: imports {a.asname or a.name} — pytest runs it here too"
                         for a in node.names if (a.asname or a.name).startswith("test_")]
    return problems


def test_pytest_and_each_files_runner_run_the_same_tests():
    """ARCH-7. pytest is here for LOCAL iteration (`pixi run python -m pytest tests/test_x.py -k
    name`); CTest still runs each file as a script. The two must run one set of tests, or a green
    `-k` loop proves nothing about the gate, and the gate can hide a test the loop runs.

    Measured when this was written (2026-09-25, every file run both ways): pytest collected 2,140
    tests and the `__main__` blocks ran 2,124; the 16 between them were exactly the footage and
    soak checks, which tests/conftest.py deselects as each runner leaves them out.

    The class the reachability check above cannot see: the 87 `globals()`-runner files are exempt
    from it, and in one of them a `def test_…` defined below the `__main__` block never runs. That
    happened: before c91f121, `tests/test_stats.py`'s block sat at line 2043, and the three
    "the page fits its pane" tests below it had never run once."""
    fixtures = _conftest_fixtures()
    assert "monkeypatch_restore" in fixtures, f"the conftest scan lost its fixture: {fixtures}"
    problems, scanned = [], 0
    for name in sorted(os.listdir(_TESTS_DIR)):
        if name.startswith("test_") and name.endswith(".py"):
            scanned += 1
            src = open(os.path.join(_TESTS_DIR, name), encoding="utf-8").read()
            problems += _pytest_parity_problems(name, src, fixtures)
    assert not problems, ("pytest and the file's own runner would run different tests:\n  "
                          + "\n  ".join(problems))
    # Both directions, on planted sources: each divergence is caught, by name, and nothing else.
    planted = ('from _synthetic import test_shared\n'
               'def test_fine(monkeypatch_restore, k=1):\n    pass\n'
               'def test_needs(a_fixture_nobody_defines):\n    pass\n'
               'class TestThing:\n    pass\n'
               'if __name__ == "__main__":\n'
               '    [v() for k, v in sorted(globals().items()) if k.startswith("test_")]\n'
               'def test_below_main():\n    pass\n')
    caught = _pytest_parity_problems("planted.py", planted, fixtures)
    assert [p.split(" — ")[0].split(" is ")[0] for p in caught] == [
        "planted.py: imports test_shared", "planted.py: test_needs(a_fixture_nobody_defines)",
        "planted.py: class TestThing", "planted.py: test_below_main"], caught
    print(f"test_pytest_and_each_files_runner_run_the_same_tests OK — {scanned} files, "
          f"{len(fixtures)} fixtures, 4/4 planted divergences caught")


_MAP = os.path.join(_STUDIO, "README.md")
# ARCH-9: the map was 130,636 characters when it went back to one line per module, and 28 % of
# September's PRs edited it. A row is a map entry; what a module does in detail, and why, goes in
# its docstring or its section of studio/docs/module-notes.md.
MAP_MAX_CHARS = 20_000
MAP_ROW_MAX_CHARS = 200
# A module link in a row's first cell: `[text](name.py)`.
_MAP_LINK = r"\[([^\]]+)\]\(([^)]+)\.py\)"


def _layer(name: str) -> str:
    """The Imports word the map must print for a module, read off the three pinned sets."""
    if name in ALLOWED:
        return "pacer"
    if name in ALLOWED_QT:
        return "Qt"
    return "→Qt" if name in QT_REACHING else "—"


def _map_problems(text: str) -> list[str]:
    """Everything wrong with a module map, one sentence each — empty when the map is right."""
    problems, rows = [], {}
    if len(text) > MAP_MAX_CHARS:
        problems.append(f"the map is {len(text):,} characters, over {MAP_MAX_CHARS:,}: move the "
                        f"detail to the module's docstring or studio/docs/module-notes.md")
    for line in text.splitlines():
        if not line.startswith("| ["):
            continue
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip().strip("|"))]
        # A FAMILY row: after its own module, the first cell may link further modules of the same
        # family — the Stats page's `stats_*.py` sections after `stats_panel.py` (ARCH-3 splits it
        # one section per module, and a row per section would not fit under MAP_MAX_CHARS). Each
        # member is a ONE-WORD link to a module sharing the head's prefix, so its file name is in
        # the raw text a grep lands on and no prose hides in a link; the row cap counts the row
        # without those links (a member costs its link, not a row); every member gets every check.
        links = list(re.finditer(_MAP_LINK, cells[0]))
        if (len(cells) != 4 or not links or links[0].start() != 0
                or re.sub(_MAP_LINK, "", cells[0]).strip(" ·")):
            problems.append(f"not a map row (module | responsibility | imports | test): {line[:90]}")
            continue
        name, (_does, imports, tests) = links[0].group(2), cells[1:]
        family = name.split("_", 1)[0] + "_"
        for m in links[1:]:
            if not re.fullmatch(r"\w+", m.group(1)) or not m.group(2).startswith(family):
                problems.append(f"{name}.py's row links {m.group(0)}: a family member is a "
                                f"one-word link to a {family}*.py module")
        width = len(line) - sum(len(m.group(0)) for m in links[1:])
        if width > MAP_ROW_MAX_CHARS:
            problems.append(f"{name}.py's row is {width} characters, over {MAP_ROW_MAX_CHARS}: "
                            f"one line of responsibility; the rest goes to its notes section")
        for member in [m.group(2) for m in links]:
            if member in rows:
                problems.append(f"{member}.py has two rows")
            rows[member] = line
            if member in _modules() and imports != _layer(member):
                problems.append(f"{member}.py: the map says it imports {imports!r}, the layering "
                                f"sets above say {_layer(member)!r}")
        named = re.findall(r"`([^`]+)`", tests)
        if tests != "—" and not named:
            problems.append(f"{name}.py: the Test cell names no test file ({tests!r})")
        for t in named:
            if not os.path.exists(os.path.join(_TESTS_DIR, t + ".py")):
                problems.append(f"{name}.py: the map's test tests/{t}.py does not exist")
    for name in sorted(set(_modules()) - set(rows)):
        problems.append(f"studio/{name}.py has no row in studio/README.md")
    for name in sorted(set(rows) - set(_modules())):
        problems.append(f"the map has a row for {name}.py, which is not a studio module")
    return problems


def test_every_studio_module_is_on_the_map_at_its_real_layer():
    """ONE ROW PER MODULE, AND THE LAYER IT CLAIMS IS THE ONE PINNED ABOVE.

    AGENTS.md sends every agent to `studio/README.md` for the module map. Four modules had no row
    in it (`provenance`, `provenance_panel`, `__init__`, `__main__`) and nothing noticed, while the
    rows that did exist had grown to a median 781 characters of measurement history. Both
    directions are checked, like the allow-lists: a module without a row fails, and so does a row
    for a module that no longer exists."""
    text = open(_MAP, encoding="utf-8").read()
    problems = _map_problems(text)
    assert not problems, "studio/README.md, the module map:\n  " + "\n  ".join(problems)
    rows = sum(line.startswith("| [") for line in text.splitlines())
    print(f"test_every_studio_module_is_on_the_map_at_its_real_layer OK — {rows} rows, "
          f"{len(text):,} characters")


def test_the_map_check_fails_on_each_planted_defect():
    """A GUARD THAT CANNOT FAIL IS DECORATION. Each defect below is planted into the real map, and
    the check has to name it."""
    real = open(_MAP, encoding="utf-8").read()
    row = next(line for line in real.splitlines() if line.startswith("| [map_render.py]"))
    plants = {
        "a module with no row": real.replace(row + "\n", ""),
        "a row for a module that does not exist": real.replace(
            row, row + "\n| [ghost.py](ghost.py) | Nothing | — | `test_layering` |"),
        "a module with two rows": real.replace(row, row + "\n" + row),
        "a row claiming the wrong layer": real.replace(row, row.replace("| →Qt |", "| — |")),
        "a row naming a test that does not exist": real.replace(
            row, row.replace("`test_map_render`", "`test_map_rendr`")),
        "a row that grew a history": real.replace(row, row.replace(" | →Qt |", " " + "x" * 80 + " | →Qt |")),
        "a row missing a cell": real.replace(row, row.replace(" | →Qt", "")),
        "a map that grew": real + "\n" + "x" * MAP_MAX_CHARS,
        # ...and the FAMILY row (the Stats page's): each member is held to what a row is.
        "a family member dropped from its row": real.replace(" [ideal](stats_ideal.py)", ""),
        "a family member that does not exist": real.replace(
            "[ideal](stats_ideal.py)", "[ideal](stats_ideal.py) [ghost](stats_ghost.py)"),
        "a family member outside its family": real.replace(
            "[ideal](stats_ideal.py)", "[ideal](stats_ideal.py) [theme](theme.py)"),
        "prose hidden in a member link": real.replace(
            "[ideal](stats_ideal.py)", "[the ideal lap tiles and table](stats_ideal.py)"),
        "a family member listed twice": real.replace(
            "[ideal](stats_ideal.py)", "[ideal](stats_ideal.py) [ideal](stats_ideal.py)"),
    }
    for what, text in plants.items():
        assert text != real, f"the plant for {what} changed nothing — the map's row moved"
        assert _map_problems(text), f"the map check passed a map with {what}"
    print(f"test_the_map_check_fails_on_each_planted_defect OK — {len(plants)} plants, each caught")


if __name__ == "__main__":
    test_only_the_data_layer_imports_pacer()
    test_only_the_view_layer_imports_qt()
    test_the_data_core_does_not_reach_qt_through_a_studio_import()
    test_the_import_scanner_sees_every_studio_spelling()
    test_every_declared_test_is_actually_reachable_from_its_runner()
    test_pytest_and_each_files_runner_run_the_same_tests()
    test_every_studio_module_is_on_the_map_at_its_real_layer()
    test_the_map_check_fails_on_each_planted_defect()
    print("\n8 layering tests passed")
