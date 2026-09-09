"""The dev harnesses must be HERMETIC — their output may not depend on machine state.

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

So the invariant is not "redirect the seams that are written to", it is "redirect them all".

**The same hole was open in the harnesses that build a real window.** `ui_capture`, `_smoke` and
`media_capture` each redirected `library` alone. Measured on `GX020060.MP4`: a capture reads
`prefs` six times while `StudioWindow.__init__` runs — `lap_panel_tab`, `grid_sizes`,
`excluded_visible`, `speed_unit`, `colorblind_palette` and `map_key_collapsed` — and all six
captured PNGs differ byte-for-byte between an operator on defaults and one running mph plus the
colour-blind palette. Zero writes; every one of them a read, which is exactly why "redirect what
is written" missed them. They now share `studio.dev._jail.divert_app_support`.

This test enforces that the redirect stays complete as `studio` grows: any module that gains an
`_app_support_dir` seam must be added to the jail helper (and to the dump's own list), or the
gate and the harnesses silently stop being hermetic again.

Run:  QT_QPA_PLATFORM=offscreen python tests/test_golden_hermetic.py
"""
import ast
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

DEV = ROOT / "studio" / "dev"
DUMP = DEV / "golden_session_dump.py"
JAIL = DEV / "_jail.py"
JAIL_CALL = "divert_app_support"

# The dev tools that build a real Session or a real StudioWindow, and so read the seams. Each must
# either redirect every seam itself or delegate to the jail helper. `golden_session_dump` is listed
# because it is the gate; the other three because their output is looked at (or published).
HARNESSES = ("golden_session_dump.py", "ui_capture.py", "_smoke.py", "media_capture.py")


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


def seams_redirected_by(path: pathlib.Path) -> set[str]:
    """Every module `path` reassigns `_app_support_dir` on, whether written as a direct attribute
    assignment or through a loop over a tuple of modules (the form the dump and the jail use)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
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


def calls_the_jail(path: pathlib.Path) -> bool:
    """Does this tool delegate to `_jail.divert_app_support(...)`? Matches the call, not the
    import, so a tool that imports the helper and forgets to call it still fails."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            fn = node.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
            if name == JAIL_CALL:
                return True
    return False


def test_jail_helper_redirects_every_app_support_seam():
    """The shared helper is the single place the seam list lives — it must be complete."""
    declared = seams_in_studio()
    redirected = seams_redirected_by(JAIL)
    assert declared, "no _app_support_dir seams found — the AST scan is broken, not the jail"
    missing = declared - redirected
    assert not missing, (
        f"studio/dev/_jail.py does not redirect {sorted(missing)}. Every module with an "
        f"_app_support_dir seam must be redirected there, or every harness that delegates to it "
        f"reads the operator's own state. Found seams: {sorted(declared)}; "
        f"redirected: {sorted(redirected)}.")


def test_dump_redirects_every_app_support_seam():
    declared = seams_in_studio()
    redirected = seams_redirected_by(DUMP)
    assert declared, "no _app_support_dir seams found — the AST scan is broken, not the dump"
    missing = declared - redirected
    assert not missing, (
        f"studio.dev.golden_session_dump does not redirect {sorted(missing)}. Every module with "
        f"an _app_support_dir seam must be redirected, or the golden fingerprint depends on the "
        f"developer's own app-support state and the gate stops meaning anything. Found seams: "
        f"{sorted(declared)}; redirected: {sorted(redirected)}.")


def test_every_window_building_harness_is_jailed():
    """Each harness either redirects every seam itself or calls the shared helper. Reading a seam
    is enough to break it — `prefs` is never written by these tools and still changed every pixel
    of `ui_capture`'s output — so "it only reads" is not an exemption."""
    declared = seams_in_studio()
    for name in HARNESSES:
        path = DEV / name
        assert path.exists(), f"{name} is listed as a harness but does not exist"
        missing = declared - seams_redirected_by(path)
        assert not missing or calls_the_jail(path), (
            f"studio/dev/{name} leaves {sorted(missing)} pointing at the user's real "
            f"~/Library/Application Support/pacer. Call "
            f"`_jail.divert_app_support(...)` before building the window, or redirect every seam "
            f"in the tool itself.")


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


def test_jail_moves_every_seam_and_adopts_an_existing_jail():
    """Behavioural half of the helper: every seam lands in the returned dir, and a jail already
    installed by an outer harness is adopted, not replaced (replacing it would move the app's
    state out from under a write-jail that is watching the first directory)."""
    import tempfile

    from studio import demo, library, prefs, track_db
    from studio.dev import _jail

    mods = (demo, library, prefs, track_db)
    originals = {m: m._app_support_dir for m in mods}
    try:
        jail = _jail.divert_app_support("pacer-jail-test-")
        for m in mods:
            assert os.path.abspath(m._app_support_dir()) == os.path.abspath(jail.dir), (
                f"{m.__name__} still resolves to {m._app_support_dir()} after the jail")
        assert prefs.prefs_path().startswith(jail.dir), "prefs.json is still the operator's own"
        assert jail.created, "a fresh jail must report itself as owned by this run"

        # Second call: the existing jail wins, a fresh temp dir is NOT created, and the caller is
        # told it does not own the directory — `_smoke` rmtree's what it owns.
        outer = tempfile.mkdtemp(prefix="pacer-outer-jail-")
        library._app_support_dir = lambda: outer
        adopted = _jail.divert_app_support("pacer-jail-test-")
        assert os.path.abspath(adopted.dir) == os.path.abspath(outer), (
            f"an existing jail must be adopted, not replaced (got {adopted.dir}, wanted {outer})")
        assert not adopted.created, (
            "an adopted jail must not be reported as created — a caller that cleans up would "
            "delete the outer harness's directory")
        for m in mods:
            assert os.path.abspath(m._app_support_dir()) == os.path.abspath(outer)
    finally:
        for m, fn in originals.items():
            m._app_support_dir = fn


if __name__ == "__main__":
    test_jail_helper_redirects_every_app_support_seam()
    test_dump_redirects_every_app_support_seam()
    test_every_window_building_harness_is_jailed()
    test_seam_redirect_actually_takes_effect()
    test_jail_moves_every_seam_and_adopts_an_existing_jail()
    print("OK: the jail covers every app-support seam, every harness uses it, and it takes effect")
