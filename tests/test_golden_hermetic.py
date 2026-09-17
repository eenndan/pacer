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

**The gate's second credibility problem was the opposite direction: it blamed the recording for
its own environment.** Run as AGENTS.md documents it — no PYTHONPATH — `import pacer` resolved to
the repo's C++ `pacer/` directory (a namespace portion with no `GPMFSource`) and the tool printed
"not readable as GPMF … a partial copy, or a file some tool overwrote" about the owner's intact
11.9 GB recording. On a machine where a dev tool really had destroyed 11.9 GB of footage, that is
the most alarming sentence this software can produce, and it was wrong twice: wrong about the
cause, and wrong to implicate the file at all. The preflight tests below hold the line that a
problem with THIS RUN is reported as one.

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

    from studio import app_support, demo, library, prefs, track_db
    from studio.dev import _jail

    mods = (demo, library, prefs, track_db)
    originals = {m: m._app_support_dir for m in mods}
    env_before = os.environ.get(app_support.DIR_ENV)
    try:
        # Since H8 a test process is never un-jailed (studio/app_support.py), so the process's own
        # jail is what a harness finds first, and it is adopted like any outer jail.
        own = _jail.divert_app_support("pacer-jail-test-")
        assert not own.created and os.path.abspath(own.dir) != os.path.abspath(_jail._REAL_DIR), (
            f"a test process's own jail must be adopted, not replaced: {own}")

        # The FRESH case, as a harness outside tests/ meets it: the library seam reports the real
        # directory. Only the path string is compared — divert repoints every seam before returning
        # and nothing in this test writes a store.
        library._app_support_dir = lambda: _jail._REAL_DIR
        jail = _jail.divert_app_support("pacer-jail-test-")
        for m in mods:
            assert os.path.abspath(m._app_support_dir()) == os.path.abspath(jail.dir), (
                f"{m.__name__} still resolves to {m._app_support_dir()} after the jail")
        assert prefs.prefs_path().startswith(jail.dir), "prefs.json is still the operator's own"
        assert jail.created, "a fresh jail must report itself as owned by this run"
        assert os.environ.get(app_support.DIR_ENV) == jail.dir, (
            "the jail patched this process but did not export itself to the processes it starts")

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
        if env_before is None:
            os.environ.pop(app_support.DIR_ENV, None)
        else:
            os.environ[app_support.DIR_ENV] = env_before


# ---------------------------------------------------------------------------------------------
# THE GATE MUST NEVER REPORT A PROBLEM WITH THIS RUN AS A VERDICT ON THE RECORDING.
#
# The eight bytes below are the smallest thing `chapters.probe_mp4` accepts as an MP4 container
# (`00 00 00 14 'ftyp'`), so the preflight gets all the way to the parser on a file that is not
# video and cannot be mistaken for anyone's footage.
_FAKE_MP4 = b"\x00\x00\x00\x14ftypisom"

# The sentences the preflight is not allowed to say about a file it only failed to OPEN. Each one
# asserts a HISTORY the tool cannot know — that bytes were destroyed, or that a copy was cut short.
_GUESSES = ("overwrote", "overwritten", "partial copy", "incomplete")


def _fake_recording(tmp: str) -> str:
    """A GoPro-named, container-shaped file in `tmp`. Never a path under the owner's Desktop."""
    path = os.path.join(tmp, "GX020060.MP4")
    with open(path, "wb") as f:
        f.write(_FAKE_MP4)
    return path


def test_preflight_blames_the_environment_when_the_bindings_are_missing():
    """A missing `pacer` is a fact about THIS PROCESS. Reported as one — never as damage to the
    recording, and never with advice ("set PACER_GOLDEN_MP4 to a complete recording") that sends
    the operator looking for a fresh copy of a file that was fine all along."""
    import tempfile

    from studio.dev import golden_session_dump as dump

    def no_bindings():
        raise dump.BindingsUnavailable(
            "`import pacer` resolved to a namespace package with no GPMFSource")

    with tempfile.TemporaryDirectory(prefix="pacer-preflight-") as tmp:
        path = _fake_recording(tmp)
        msg = dump.preflight(path, opener_factory=no_bindings)

    assert msg, "a run with no usable bindings must fail the gate, not fingerprint whatever loads"
    low = msg.lower()
    for guess in _GUESSES:
        assert guess not in low, (
            f"the missing-bindings message accuses the recording ({guess!r}): {msg!r}. An import "
            f"problem must never present as data loss — this is the one message whose false "
            f"positive tells the owner a tool ate their footage.")
    assert "bindings" in low and "pythonpath=bindings/pacer" in low, (
        f"the missing-bindings message must name the real cause and the fix: {msg!r}")


def test_preflight_states_a_parse_failure_without_guessing_why():
    """"This file did not parse as GPMF" is a fact the tool measured. "Some tool overwrote it" is
    a story about how it got that way, which the tool cannot see and must not tell."""
    import tempfile

    from studio.dev import golden_session_dump as dump

    def parser_refuses(path):
        raise RuntimeError(f"Failed to open file: {path}")

    with tempfile.TemporaryDirectory(prefix="pacer-preflight-") as tmp:
        path = _fake_recording(tmp)
        msg = dump.preflight(path, opener_factory=lambda: parser_refuses)

    assert msg, "an unparseable recording must fail the gate"
    low = msg.lower()
    assert "did not parse as gpmf" in low, f"the parse failure must be stated as itself: {msg!r}"
    assert path in msg, f"the failing path must be named: {msg!r}"
    for guess in _GUESSES:
        assert guess not in low, (
            f"the parse-failure message guesses at a cause ({guess!r}): {msg!r}")


def test_documented_golden_command_reaches_the_parser_without_pythonpath():
    """AGENTS.md's real-media workflow is `pixi run python -m studio.dev.golden_session_dump …`
    with NO PYTHONPATH, and before this it could not work as written.

    The repo's C++ `pacer/` directory and the cmake-deployed `site-packages/pacer/` both lack an
    `__init__.py`, so they are PEP 420 namespace portions: `import pacer` succeeded and had no
    `GPMFSource`, and the resulting AttributeError was printed as

        FATAL: ~/Desktop/D24/GX020060.MP4 exists but is not readable as GPMF
        (module 'pacer' has no attribute 'GPMFSource') — a partial copy, or a file some tool
        overwrote.

    about an intact 11.9 GB recording. Driven as a SUBPROCESS with PYTHONPATH stripped, because
    that is the only way to reproduce the resolution order the operator actually gets; CTest
    injects `PYTHONPATH=bindings/pacer` into this very process."""
    import subprocess
    import tempfile

    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    with tempfile.TemporaryDirectory(prefix="pacer-preflight-") as tmp:
        env["PACER_GOLDEN_MP4"] = _fake_recording(tmp)
        proc = subprocess.run(
            [sys.executable, "-m", "studio.dev.golden_session_dump",
             os.path.join(tmp, "out.json")],
            cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=300)

    err = proc.stderr
    low = err.lower()
    assert "has no attribute" not in err, (
        f"the documented command still resolves `pacer` to a namespace package — it must find the "
        f"built bindings on its own:\n{err}")
    for guess in _GUESSES:
        assert guess not in low, (
            f"the documented command blamed the recording for an import problem ({guess!r}):\n"
            f"{err}")
    assert "did not parse as gpmf" in low, (
        f"the fake container should have reached the real parser and been reported as a parse "
        f"failure; got exit {proc.returncode}:\n{err}\n{proc.stdout}")


if __name__ == "__main__":
    test_jail_helper_redirects_every_app_support_seam()
    test_dump_redirects_every_app_support_seam()
    test_every_window_building_harness_is_jailed()
    test_seam_redirect_actually_takes_effect()
    test_jail_moves_every_seam_and_adopts_an_existing_jail()
    test_preflight_blames_the_environment_when_the_bindings_are_missing()
    test_preflight_states_a_parse_failure_without_guessing_why()
    test_documented_golden_command_reaches_the_parser_without_pythonpath()
    print("OK: the jail covers every app-support seam, every harness uses it, it takes effect, "
          "and the gate's preflight never reports an import problem as damage to the recording")
