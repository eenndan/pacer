"""NO TEST MAY BE ABLE TO REACH THE OWNER'S REAL APP-SUPPORT DIRECTORY — in-process or in a child.

WHAT HAPPENED (H8, 2026-09-17). A `stadium` row from the two-lap synthetic fixture was written into
the owner's real `~/Library/Application Support/pacer/library.json` during a routine `ctest` run:

    {"fingerprint": "stadium", "track": null, "date": "2025-08-30", "lap_count": 2,
     "best": 37.95491178391123, "theoretical": 32.79511092416341, "verified": false,
     "paths": ["/tmp/stadium.MP4"]}

The writer was `test_load_failure.py`'s `test_auto_fit_notice_retracts_when_the_start_line_is_placed`.
It diverts no app-support seam. It emits `timingEdited`, the window refreshes its library entry, and
that upserts. On `main` the write was dead code only by ACCIDENT: the shared realqt fixture's `_Laps`
double had no point API, so building the entry raised AttributeError before `upsert_and_save` was
reached. A lane that completed the double (a correct fix to a test stand-in) made the write live,
and the next `ctest` put the row into the owner's library. Reproduced under a throwaway HOME, the row
it writes is field-for-field identical to the leaked one, down to the float bits of `best`.

Measured across all 116 CTest registrations, each run alone under its own throwaway HOME with the
completed double: exactly ONE writes app-support state (that one). `test_compare_lifecycle`, whose
three subprocesses were the first suspect, and `test_export_video` wrote nothing, and nothing in
their children touches a store. `pixi run smoke` wrote nothing either. So the leak was not a child
process. But the jail had the hole the suspicion pointed at anyway: every jail in this repo was an
in-process attribute patch (`mod._app_support_dir = lambda: tmp`), which a child process never
inherits, and nothing at all stopped a test that forgot to patch.

THE RULE NOW (`studio/app_support.py`), checked here in every form a test process can take:
  * `PACER_APP_SUPPORT_DIR` set -> every seam resolves there. It is an ENVIRONMENT variable, so a
    child inherits it; `studio/dev/_jail.py` now exports it as well as patching.
  * `PACER_APP_SUPPORT_JAIL` set, or the entry script is a file in this checkout's `tests/` -> the
    first resolution makes a fresh temp dir, exports it as `PACER_APP_SUPPORT_DIR`, and removes it
    at exit. `tests/CMakeLists.txt` sets the flag on EVERY registration, so a whole ctest process
    tree is jailed from its root, including a `python -c` child spawned before anything resolved.
  * Neither -> the real directory. The app itself is unchanged, and the last test pins that.

Every child here gets a throwaway HOME, so even a run of this file against a tree WITHOUT the fix
resolves paths only inside that throwaway and cannot reach the owner's directory. Nothing here
writes a store. The one file written is the session log (E2): the app opens it on every start, so
it is checked by a child that really opens it — into its jail, or, in the control, into its
throwaway HOME.

Run: python tests/test_app_support_jail.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
sys.path.insert(0, _REPO)
sys.path.insert(0, _TESTS)

from test_golden_hermetic import seams_in_studio  # noqa: E402

# Spelled here rather than imported from studio.app_support, so this file still reaches its
# assertions (and fails on them, with a reason) on a tree that has no such module.
_DIR_ENV = "PACER_APP_SUPPORT_DIR"
_JAIL_ENV = "PACER_APP_SUPPORT_JAIL"
_PROBE_FLAG = "--resolve-probe"
# `logsetup` is not a store, but it WRITES there: the session log, `<app-support>/logs/pacer.log`
# (E2). Listed so a tree that loses its seam fails here by name rather than by omission.
_KNOWN_SEAMS = {"demo", "focus", "library", "logsetup", "marks", "prefs", "session_record",
                "track_db"}


def _real_dir() -> str:
    """The owner's real app-support dir for THIS process's HOME, spelled as the stores spell it."""
    return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "pacer")


def _seams() -> list[str]:
    seams = seams_in_studio()
    assert _KNOWN_SEAMS <= seams, f"the AST seam scan lost a known store: {sorted(seams)}"
    return sorted(seams)


# A child that RESOLVES every seam and prints where each one points. It calls no store function, so
# it writes nothing whatever the answer is.
_RESOLVE_SRC = r"""
import importlib, json, os, sys
sys.path.insert(0, {repo!r})
seams = {{n: importlib.import_module("studio." + n)._app_support_dir() for n in {seams!r}}}
print("H8-RESOLVE " + json.dumps({{
    "real": os.path.join(os.path.expanduser("~"), "Library", "Application Support", "pacer"),
    "seams": seams}}))
"""


def _parse(out: str, err: str = "") -> dict:
    for line in out.splitlines():
        if line.startswith("H8-RESOLVE "):
            return json.loads(line[len("H8-RESOLVE "):])
    raise AssertionError(f"the child printed no resolution.\nstdout: {out[-2000:]}\nstderr: {err[-3000:]}")


def _child_env(home: str, *, keep_jail: bool = False, keep_dir: bool = False) -> dict:
    """This process's environment for a child with HOME at a throwaway dir. The two jail variables
    are dropped unless asked for, so each test states exactly which one it is relying on."""
    env = dict(os.environ, HOME=home)
    if not keep_jail:
        env.pop(_JAIL_ENV, None)
    if not keep_dir:
        env.pop(_DIR_ENV, None)
    return env


def _run(argv: list[str], env: dict, cwd: str | None = None) -> dict:
    proc = subprocess.run(argv, env=env, cwd=cwd, capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, (
        f"child exited {proc.returncode}: {argv}\nstdout: {proc.stdout[-2000:]}\n"
        f"stderr: {proc.stderr[-3000:]}")
    return _parse(proc.stdout, proc.stderr)


def _escaped(resolution: dict) -> list[str]:
    real = os.path.abspath(resolution["real"])
    return sorted(n for n, d in resolution["seams"].items() if os.path.abspath(d) == real)


# ------------------------------------------------------------------------------ the build
def test_every_ctest_registration_carries_the_jail():
    """THE BUILD: every test CTest registers runs with `PACER_APP_SUPPORT_JAIL=1`, so its whole
    process tree is jailed from the root, whatever the test itself remembers to patch."""
    ctest = os.environ.get("PACER_CTEST_COMMAND") or shutil.which("ctest") or os.path.join(
        _REPO, ".pixi", "envs", "default", "bin", "ctest")
    build = os.environ.get("PACER_CTEST_DIR") or os.path.join(_REPO, "build", "Release")
    assert os.path.isfile(ctest) and os.path.isdir(build), (
        f"cannot list the registered tests (ctest={ctest}, build dir={build}). Run this through "
        "ctest, or build first — this check must not pass by finding nothing to check")
    proc = subprocess.run([ctest, "--test-dir", build, "--show-only=json-v1"], capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr[-2000:]
    tests = json.loads(proc.stdout)["tests"]
    assert len(tests) >= 100, f"only {len(tests)} registrations listed — the listing is broken"
    unjailed = []
    for t in tests:
        env = next((p["value"] for p in t.get("properties", []) if p["name"] == "ENVIRONMENT"), [])
        pinned = [e for e in env if e.startswith(_DIR_ENV + "=")]
        if f"{_JAIL_ENV}=1" not in env or any(
                os.path.abspath(e.split("=", 1)[1]) == os.path.abspath(_real_dir()) for e in pinned):
            unjailed.append(t["name"])
    assert not unjailed, (
        f"{len(unjailed)} of {len(tests)} CTest registrations run WITHOUT {_JAIL_ENV}=1, so a test "
        f"among them that forgets to patch a seam writes the owner's real library: "
        f"{', '.join(unjailed[:12])}{' …' if len(unjailed) > 12 else ''}. The loop at the end of "
        "tests/CMakeLists.txt adds it to every registration; a test registered after that loop, or "
        "in another CMakeLists, misses it.")
    print(f"test_every_ctest_registration_carries_the_jail OK ({len(tests)} registrations)")


# ------------------------------------------------------------------------------ in-process
def test_this_test_process_cannot_resolve_the_real_directory():
    """THE INCIDENT'S SHAPE: a test process that patched nothing. Every seam must resolve away from
    the owner's directory, and all of them to ONE place, so a store written by one module is the
    store another module reads back."""
    real = os.path.abspath(_real_dir())
    import importlib
    resolved = {n: importlib.import_module(f"studio.{n}")._app_support_dir() for n in _seams()}
    escaped = sorted(n for n, d in resolved.items() if os.path.abspath(d) == real)
    assert not escaped, (
        f"this test process resolves {escaped} to the real {real} — a test here that forgot to "
        "patch a seam would write the owner's own files. That is exactly how the `stadium` row "
        "reached library.json.")
    assert len(set(resolved.values())) == 1, f"the seams disagree on the jail: {resolved}"
    print("test_this_test_process_cannot_resolve_the_real_directory OK")


# ------------------------------------------------------------------------------ children
def test_a_python_c_child_of_a_test_cannot_resolve_it():
    """THE FORM test_compare_lifecycle SPAWNS: `sys.executable -c <src>`. Its argv names no test
    file, so only the environment can jail it. Two cases: the flag alone (the child makes its own
    jail), and the parent's already-resolved dir (the child must share it, not make another)."""
    home = tempfile.mkdtemp(prefix="pacer-h8-home-")
    try:
        src = _RESOLVE_SRC.format(repo=_REPO, seams=_seams())
        flag_only = _run([sys.executable, "-c", src], _child_env(home, keep_jail=True))
        assert not _escaped(flag_only), (
            f"a `python -c` child of a test, holding only this process's jail flag, resolves "
            f"{_escaped(flag_only)} to the real directory: {flag_only}")

        import importlib
        mine = importlib.import_module("studio.library")._app_support_dir()
        shared = _run([sys.executable, "-c", src], _child_env(home, keep_jail=True, keep_dir=True))
        assert set(shared["seams"].values()) == {mine}, (
            f"a child inheriting this process's jail resolves elsewhere: {shared['seams']} vs {mine}")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("test_a_python_c_child_of_a_test_cannot_resolve_it OK")


def _probe():
    """`python tests/test_app_support_jail.py --resolve-probe`: stand in for a test file an agent
    runs by hand. Resolve every seam here, then in a `-c` grandchild that inherits this process's
    environment, and print both."""
    import importlib
    seams = _seams()
    here = {n: importlib.import_module(f"studio.{n}")._app_support_dir() for n in seams}
    proc = subprocess.run([sys.executable, "-c", _RESOLVE_SRC.format(repo=_REPO, seams=seams)],
                          capture_output=True, text=True, timeout=300)
    grandchild = _parse(proc.stdout, proc.stderr)
    print("H8-RESOLVE " + json.dumps({"real": _real_dir(), "seams": here,
                                      "grandchild": grandchild}))


def test_a_test_file_run_by_hand_outside_ctest_is_jailed():
    """`PYTHONPATH=bindings/pacer python tests/test_x.py` is how most agents run one file, and no
    CMake environment reaches that. A script in this checkout's tests/ is jailed by where it lives,
    and passes the jail on to its own children. The jail dir it made is gone after it exits."""
    home = tempfile.mkdtemp(prefix="pacer-h8-home-")
    try:
        got = _run([sys.executable, os.path.abspath(__file__), _PROBE_FLAG], _child_env(home))
        assert not _escaped(got), (
            f"a test file run by hand, outside ctest, resolves {_escaped(got)} to the real "
            f"directory: {got['seams']}")
        assert not _escaped(got["grandchild"]), (
            f"a test file run by hand is jailed, but its `python -c` child is not: "
            f"{got['grandchild']['seams']}")
        jail = set(got["seams"].values())
        assert len(jail) == 1 and set(got["grandchild"]["seams"].values()) == jail, (
            f"parent and child disagree on the jail: {got}")
        assert not os.path.exists(jail.pop()), "the jail a test process made outlived the process"
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("test_a_test_file_run_by_hand_outside_ctest_is_jailed OK")


def test_a_pytest_run_over_tests_is_jailed():
    """ARCH-7. `pixi run python -m pytest tests/test_x.py -k name` enters through pytest's own
    `__main__.py`, which is not in tests/, so the by-hand rule above never fires for it: without
    tests/conftest.py setting the jail, such a run resolved the REAL directory (seen with this test
    and the conftest line removed, under a throwaway HOME). The child runs pytest over a real test
    file in-process, as `-c` (no entry script either), then resolves every seam."""
    home = tempfile.mkdtemp(prefix="pacer-h8-home-")
    try:
        target = os.path.join(_TESTS, "test_gearing.py")
        src = (f"import pytest\nrc = pytest.main(['-q', '--collect-only', {target!r}])\n"
               "assert rc == 0, f'pytest exited {rc}'\n"
               + _RESOLVE_SRC.format(repo=_REPO, seams=_seams()))
        got = _run([sys.executable, "-c", src], _child_env(home), cwd=_REPO)
        assert not _escaped(got), (
            f"a pytest run over tests/ resolves {_escaped(got)} to the real directory: is "
            f"tests/conftest.py still setting {_JAIL_ENV}? {got['seams']}")
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("test_a_pytest_run_over_tests_is_jailed OK")


def test_a_dev_harness_jail_reaches_the_processes_it_spawns():
    """`studio/dev/_jail.divert_app_support` used to patch module attributes only, so a child
    process of a jailed harness resolved the real directory. It must now export the jail."""
    home = tempfile.mkdtemp(prefix="pacer-h8-home-")
    try:
        seams = _seams()
        src = (
            "import json, subprocess, sys\n"
            f"sys.path.insert(0, {_REPO!r})\n"
            "from studio.dev import _jail\n"
            "jail = _jail.divert_app_support('pacer-h8-harness-')\n"
            f"src = {_RESOLVE_SRC.format(repo=_REPO, seams=seams)!r}\n"
            "p = subprocess.run([sys.executable, '-c', src], capture_output=True, text=True)\n"
            "print(p.stdout, end='')\n"
            "print('H8-JAIL ' + jail.dir)\n")
        proc = subprocess.run([sys.executable, "-c", src], env=_child_env(home), capture_output=True,
                              text=True, timeout=300)
        assert proc.returncode == 0, proc.stderr[-3000:]
        child = _parse(proc.stdout, proc.stderr)
        jail = next(ln[len("H8-JAIL "):] for ln in proc.stdout.splitlines() if ln.startswith("H8-JAIL "))
        assert not _escaped(child), (
            f"a process spawned by a harness jailed with _jail.divert_app_support resolves "
            f"{_escaped(child)} to the real directory — the jail stopped at the process boundary")
        assert set(child["seams"].values()) == {jail}, (
            f"the harness's child resolves outside the harness's jail {jail}: {child['seams']}")
        shutil.rmtree(jail, ignore_errors=True)
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("test_a_dev_harness_jail_reaches_the_processes_it_spawns OK")


# ------------------------------------------------------------------------------ the session log
# A child that OPENS the session log for real, the way the app's startup does, writes one line to
# it, and reports where it went. Stdlib only, no Qt: `studio.logsetup` imports nothing else.
_LOG_SRC = r"""
import json, logging, os, sys
sys.path.insert(0, {repo!r})
from studio import logsetup
path = logsetup.configure()
logging.getLogger("studio.library").warning("H8-LOG-PROBE")
for h in logging.getLogger().handlers:
    h.flush()
text = open(path, encoding="utf-8").read() if path else ""
print("H8-LOG " + json.dumps({{"path": path, "wrote": "H8-LOG-PROBE" in text,
                              "real": os.path.join(os.path.expanduser("~"), "Library",
                                                   "Application Support", "pacer")}}))
"""


def _log_child(env: dict) -> dict:
    proc = subprocess.run([sys.executable, "-c", _LOG_SRC.format(repo=_REPO)], env=env,
                          capture_output=True, text=True, timeout=300)
    assert proc.returncode == 0, f"log child exited {proc.returncode}:\n{proc.stderr[-3000:]}"
    line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("H8-LOG ")), None)
    assert line, f"the log child printed nothing:\n{proc.stdout[-2000:]}\n{proc.stderr[-3000:]}"
    return json.loads(line[len("H8-LOG "):])


def _under(path: str, directory: str) -> bool:
    path, directory = os.path.realpath(path), os.path.realpath(directory)
    return path == directory or path.startswith(directory + os.sep)


def test_the_session_log_cannot_reach_the_real_directory():
    """E2. The app now WRITES a file on every start — the session log — so it is held to the rule
    by a real write, not only a resolved path: in this process, and in a child that actually
    opens it holding nothing but the jail flag. The child's HOME is a throwaway, and after it exits
    that HOME's real app-support directory must not exist at all (no `logs/`, no anything).

    The control is the same child with no jail: its log MUST land in its HOME's real directory.
    That proves the probe can see a real-directory write when there is one, and that the app's
    own log still goes where the user will look for it."""
    from studio import library, logsetup
    here = logsetup.log_path()
    assert not _under(here, _real_dir()), (
        f"this test process would write its session log to {here}, inside the real {_real_dir()}")
    jail = library._app_support_dir()
    assert here == os.path.join(jail, "logs", "pacer.log"), (
        f"the log resolves outside this process's jail {jail}: {here}")

    home = tempfile.mkdtemp(prefix="pacer-h8-home-")
    try:
        jailed = _log_child(_child_env(home, keep_jail=True))
        assert jailed["path"] and jailed["wrote"], f"the jailed child opened no log: {jailed}"
        assert not _under(jailed["path"], jailed["real"]), (
            f"a jailed child wrote its session log into the real directory: {jailed}")
        assert not os.path.exists(os.path.join(home, "Library", "Application Support", "pacer")), (
            f"a jailed child created its HOME's real app-support dir: {os.listdir(home)}")

        control = _log_child(_child_env(home))
        want = os.path.join(control["real"], "logs", "pacer.log")
        assert control["path"] == want and control["wrote"], (
            f"with no jail, the app's log went to {control['path']}, not {want}: {control}")
        assert os.path.isfile(want), "the control child's log is not where it said"
    finally:
        shutil.rmtree(home, ignore_errors=True)
    print("test_the_session_log_cannot_reach_the_real_directory OK")


# ------------------------------------------------------------------------------ the control
def test_the_app_itself_still_uses_the_real_directory():
    """NEGATIVE CONTROL, and the half that protects the owner the other way: a jail that misfired
    in the app would give it an empty library and quietly lose everything written to it. With no
    jail variable and an entry point outside tests/ — a `-c` process, and a script in some other
    directory, the way `python -m studio` and the frozen .app run — every seam is the real one."""
    home = tempfile.mkdtemp(prefix="pacer-h8-home-")
    elsewhere = tempfile.mkdtemp(prefix="pacer-h8-app-")
    try:
        src = _RESOLVE_SRC.format(repo=_REPO, seams=_seams())
        script = os.path.join(elsewhere, "app_entry.py")
        with open(script, "w", encoding="utf-8") as f:
            f.write(src)
        for argv in ([sys.executable, "-c", src], [sys.executable, script]):
            got = _run(argv, _child_env(home), cwd=elsewhere)
            assert _escaped(got) == _seams(), (
                f"with no jail requested, {argv[1]} resolves away from the real directory: "
                f"{got['seams']} (real: {got['real']})")
    finally:
        shutil.rmtree(home, ignore_errors=True)
        shutil.rmtree(elsewhere, ignore_errors=True)
    print("test_the_app_itself_still_uses_the_real_directory OK")


def _run_all():
    test_every_ctest_registration_carries_the_jail()
    test_this_test_process_cannot_resolve_the_real_directory()
    test_a_python_c_child_of_a_test_cannot_resolve_it()
    test_a_test_file_run_by_hand_outside_ctest_is_jailed()
    test_a_pytest_run_over_tests_is_jailed()
    test_a_dev_harness_jail_reaches_the_processes_it_spawns()
    test_the_session_log_cannot_reach_the_real_directory()
    test_the_app_itself_still_uses_the_real_directory()
    print("ALL APP-SUPPORT JAIL TESTS PASSED")


if __name__ == "__main__":
    if _PROBE_FLAG in sys.argv[1:]:
        _probe()
    else:
        _run_all()
