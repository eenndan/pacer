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
`_smoke`, `denoise_check`, `make_icon`, `media_capture`, `spike_video_sync`, `ui_capture`). Run:
    python tests/test_layering.py
"""
import ast
import os

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
ALLOWED_QT = {
    "app", "central_view", "coaching_panel", "command_palette", "export_compare",
    "export_controller",
    "export_video", "gmeter_overlay", "help_dialog", "lap_table", "library_dialog", "map_view",
    "marks_panel", "overlays", "player_pane", "plots_view", "provenance_panel",
    "session_record_dialog", "share_card", "stats_panel", "theme", "video_view",
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

# A runner that enumerates its tests from the module namespace rather than by name — any of these
# spellings — cannot strand one, so those files are exempt from the reachability rule below.
_AUTO_DISCOVERY = ("globals()", "vars()", "dir()", "getmembers")


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

    `tests/CMakeLists.txt` runs each file as `python tests/<file>.py`. Its `foreach(pytest IN
    ITEMS …)` loop is named `pytest` but only sets PYTHONPATH — NOTHING here runs under pytest, so
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
        if any(tok in open(path, encoding="utf-8").read() for tok in _AUTO_DISCOVERY):
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


if __name__ == "__main__":
    test_only_the_data_layer_imports_pacer()
    test_only_the_view_layer_imports_qt()
    test_the_data_core_does_not_reach_qt_through_a_studio_import()
    test_the_import_scanner_sees_every_studio_spelling()
    test_every_declared_test_is_actually_reachable_from_its_runner()
    print("\n5 layering tests passed")
