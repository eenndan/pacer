"""The D24 golden gate must be HERMETIC — its fingerprint may not depend on machine state.

`studio.dev.golden_session_dump` is the canonical byte-identical equivalence gate for every
Session/timing/geometry/delta change: dump on main, dump on the branch, compare at eps 1e-9. That
only means anything if the dump is a pure function of the recording.

It was not. `Session.load` resolves `track_name` through `tracks.detect_track` ->
`track_db.detect`, which read the developer's live `~/Library/Application Support/pacer/
tracks.json`. A saved track there makes the loader adopt the stored start line instead of
auto-fitting one, and flips `_track_admits_reference` from the geometric path to the by-name path.
Measured on a 1.2 GB recording: two runs of IDENTICAL code, differing only in whether a track had
been saved, disagreed on 15,655 of 35,082 leaves — 45%, for no code change. The dump redirected
`library` (so it never wrote to the user's app-support) but not `track_db`, which it only ever
read.

So the invariant is not "redirect the seams that are written to", it is "redirect them all". This
test enforces that the redirect list stays complete as `studio` grows: any module that grows an
`_app_support_dir` seam must be added to the dump, or the gate silently stops being a gate again.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_golden_hermetic.py
"""
import ast
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DUMP = ROOT / "studio" / "dev" / "golden_session_dump.py"


def seams_in_studio() -> set[str]:
    """Every `studio.<mod>` that defines its own `_app_support_dir` — i.e. every module with a
    private path into the user's Application Support directory."""
    found = set()
    for path in sorted((ROOT / "studio").glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.FunctionDef) and node.name == "_app_support_dir":
                found.add(path.stem)
    return found


def seams_redirected_by_dump() -> set[str]:
    """Every module the dump reassigns `_app_support_dir` on, whether written as a direct
    attribute assignment or through a loop over a tuple of modules (the form it uses today)."""
    tree = ast.parse(DUMP.read_text(encoding="utf-8"))
    found = set()
    # A loop variable is not a module: `for _mod in (...): _mod._app_support_dir = ...` must be
    # credited to the tuple's elements, never to `_mod` itself.
    loop_vars = {n.target.id for n in ast.walk(tree)
                 if isinstance(n, ast.For) and isinstance(n.target, ast.Name)}

    def module_name(node) -> str | None:
        """`studio.track_db` / `track_db` -> "track_db"."""
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            return node.attr if node.value.id == "studio" else None
        if isinstance(node, ast.Name):
            return node.id
        return None

    for node in ast.walk(tree):
        # `mod._app_support_dir = ...`
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Attribute) and tgt.attr == "_app_support_dir":
                    name = module_name(tgt.value)
                    if name and name not in loop_vars:
                        found.add(name)
        # `for _mod in (studio.a, studio.b): _mod._app_support_dir = ...`
        if isinstance(node, ast.For) and isinstance(node.iter, (ast.Tuple, ast.List)):
            assigns = [n for n in ast.walk(node) if isinstance(n, ast.Assign)
                       for t in n.targets
                       if isinstance(t, ast.Attribute) and t.attr == "_app_support_dir"]
            if assigns:
                for elt in node.iter.elts:
                    name = module_name(elt)
                    if name:
                        found.add(name)
    return found


def test_dump_redirects_every_app_support_seam():
    declared = seams_in_studio()
    redirected = seams_redirected_by_dump()
    assert declared, "no _app_support_dir seams found — the AST scan is broken, not the dump"
    missing = declared - redirected
    assert not missing, (
        f"studio.dev.golden_session_dump does not redirect {sorted(missing)}. Every module with "
        f"an _app_support_dir seam must be redirected, or the golden fingerprint depends on the "
        f"developer's own app-support state and the gate stops meaning anything. Found seams: "
        f"{sorted(declared)}; redirected: {sorted(redirected)}.")


def test_seam_redirect_actually_takes_effect():
    """The AST check proves the source SAYS it redirects; this proves the assignment works — the
    seam is resolved through the module attribute at call time, not captured at import."""
    import tempfile

    from studio import track_db
    original = track_db._app_support_dir
    try:
        tmp = tempfile.mkdtemp(prefix="pacer-hermetic-test-")
        track_db._app_support_dir = lambda: tmp
        assert track_db.db_path().startswith(tmp), (
            f"patching track_db._app_support_dir did not move db_path() ({track_db.db_path()}) — "
            f"the dump's redirect would be a no-op and the gate would still read the real DB")
        assert track_db.load()["tracks"] == [], "a fresh redirected DB must start empty"
    finally:
        track_db._app_support_dir = original


if __name__ == "__main__":
    test_dump_redirects_every_app_support_seam()
    test_seam_redirect_actually_takes_effect()
    print("OK: golden dump redirects every app-support seam, and the redirect takes effect")
