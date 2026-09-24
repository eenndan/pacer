"""A real-footage check without its recording is REPORTED AS SKIPPED, by name — never read as a pass.

G1, measured 2026-09-19, the day `~/Desktop/D24` went: eleven checks in four files (twelve once
#339 merged the same day) re-measure something on a real recording, and every one of them, finding
none, printed a "skip" line and returned. Their runners counted that as a pass (`ok  test_real_render_smoke_if_ffmpeg_and_media`,
"ALL 84 export-video tests passed", "7 ideal-sample-table checks passed") and CTest said `Passed`
for all four files. A green gate claimed real-footage coverage that nothing on the machine could
provide, and no line of any gate's output said so.

`tests/_footage.py` has the mechanism: each real-footage check is its own CTest registration,
`footage.<check>`, which exits with CTest's SKIP_RETURN_CODE when its recording is absent, so the
gate prints it under "The following tests did not run: … (Skipped)". This file holds that in place:

  * the runner tells pass, failure and skip apart (and refuses a name it does not know);
  * every check that existed when D24 went is still a registered footage check — a floor, so none
    can drift back into its file's ordinary run and pass vacuously again;
  * every footage check a file declares has exactly one registration, with the skip code, running
    that check — read from what CTest actually registered, not from the CMake text;
  * THE NEGATIVE CONTROL: every one of them, run exactly as CTest runs it under a HOME with no
    footage and no footage variable, exits with the skip code and prints its own name. On the tree
    before this change the same command ran the whole file and exited 0;
  * a recording the operator NAMED that is not there fails instead of skipping;
  * footage is found only through `tests/_footage.py`, and the golden dump reads the same variable;
  * every DEFAULT names a recording of the Desktop working set, never one that left the machine
    (G2: until then every default was on D24, so all fourteen checks could only ever skip);
  * `pixi run test-fast` reports each check SKIPPED by name even with its footage PRESENT, towards
    `pixi run test-footage`, which runs them by label — and a variable the operator set still runs.

Run:  QT_QPA_PLATFORM=offscreen PYTHONPATH=bindings/pacer python tests/test_footage_checks.py
"""
from __future__ import annotations

import ast
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile

_TESTS = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(_TESTS)
sys.path.insert(0, _TESTS)
sys.path.insert(0, _REPO)

import _footage  # noqa: E402

from studio.dev import footage as dev_footage  # noqa: E402

# Every real-footage check in the repo on 2026-09-19, found by searching tests/, studio/dev/ and
# pyproject.toml for a recording path, a footage variable or a skip branch (the PR has the table),
# plus the one #339 added that day.
# A FLOOR, not the list: a new footage check joins its file's FOOTAGE_CHECKS and needs no edit here.
_AT_G1 = (
    ("test_export_video.py", "test_real_render_smoke_if_ffmpeg_and_media"),
    ("test_export_video.py", "test_real_chaptered_non_first_chapter_render_if_media"),
    ("test_export_video.py", "test_real_render_quality_levels_if_media"),
    ("test_video_view_compare.py", "test_real_media_pane_b_is_reference_at_lap_start"),
    ("test_ideal_sample_table.py", "test_the_table_still_matches_the_app"),
    ("test_measured_figures.py", "test_the_coaching_tables_match_the_footage"),
    ("test_measured_figures.py", "test_the_floor_table_matches_the_footage"),
    ("test_measured_figures.py", "test_the_refusal_record_matches_the_footage"),
    ("test_measured_figures.py", "test_the_brake_habit_table_matches_the_footage"),
    # #339 (C5), merged while this was in review, added a twelfth with the same print-and-return.
    ("test_measured_figures.py", "test_the_brake_hint_gate_table_matches_the_footage"),
    ("test_measured_figures.py", "test_the_beat_rate_table_matches_the_footage"),
    ("test_measured_figures.py", "test_the_focus_tables_match_the_footage"),
)

# Every variable that points a check at footage. Unset in the negative control, so what it measures
# is "no footage", not "whatever the developer running it happens to have exported".
_FOOTAGE_ENVS = (dev_footage.RECORDING_ENV, dev_footage.REFERENCE_ENV,
                 "PACER_IDEAL_TABLE_MP4", "PACER_MEASURED_FIGURES_DIR", dev_footage.TIMING_ENV)
# The variables the consolidation retired. A test reading one would be pointed nowhere.
_RETIRED_ENVS = ("PACER_REAL_MP4", "PACER_D24_MEDIA")

# One call per lookup in tests/_footage.py, each with a default. The two table lookups get stand-in
# rows here; their real ones are the tables' own (`ROW_RECORDINGS`, `_LAP_SETS`).
_STAND_IN_SET = ("Stand-in Track/GX010001.MP4", "Stand-in Track/GX020001.MP4")
_LOOKUPS = (
    (_footage.recording, ()),
    (_footage.pair, ()),
    (_footage.recording_sets, ("PACER_IDEAL_TABLE_MP4", "every row", [list(_STAND_IN_SET)])),
    (_footage.directory, ("PACER_MEASURED_FIGURES_DIR", "the lap sets", _STAND_IN_SET)),
)


def _test_files() -> list[str]:
    return sorted(n for n in os.listdir(_TESTS) if n.startswith("test_") and n.endswith(".py"))


def _declared() -> list[tuple[str, str]]:
    """(file, check) for every name in a module-level `FOOTAGE_CHECKS = (...)`, read with ast."""
    out = []
    for name in _test_files():
        tree = ast.parse(open(os.path.join(_TESTS, name), encoding="utf-8").read())
        defs = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
        for node in tree.body:
            if (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "FOOTAGE_CHECKS"):
                assert isinstance(node.value, (ast.Tuple, ast.List)), f"{name}: FOOTAGE_CHECKS is not a literal"
                for elt in node.value.elts:
                    assert isinstance(elt, ast.Name) and elt.id in defs, (
                        f"{name}: FOOTAGE_CHECKS names {ast.unparse(elt)}, which is not a function "
                        "defined in that file")
                    out.append((name, elt.id))
    return out


def _registrations() -> dict[str, dict]:
    """What CTest actually registered: name -> {"command": [...], "properties": {name: value}}."""
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
    return {t["name"]: {"command": t.get("command", []),
                        "properties": {p["name"]: p["value"] for p in t.get("properties", [])}}
            for t in tests}


def _no_footage_env(home: str) -> dict:
    """The environment CTest gives a footage registration, on a machine with no footage at all —
    with the defaults IN USE, so what is measured is their absence, not `pixi run test-fast`'s
    `PACER_FOOTAGE_DEFAULTS=off` (which would skip every check whatever the machine holds)."""
    env = {k: v for k, v in os.environ.items()
           if k not in _FOOTAGE_ENVS + _RETIRED_ENVS + (_footage.DEFAULTS_ENV,)}
    env["HOME"] = home                      # every `~/Desktop/...` default now resolves into `home`
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONPATH"] = os.path.join(_REPO, "bindings", "pacer")
    env["PACER_APP_SUPPORT_JAIL"] = "1"
    return env


# ----------------------------------------------------------------------------- the runner
def test_the_runner_tells_a_pass_a_failure_and_a_skip_apart():
    """Three outcomes, three exit codes — and a name the file does not know is not a pass either."""
    def passes():
        pass

    def fails():
        raise AssertionError("the figure moved")

    def skips():
        raise _footage.FootageMissing("/nowhere/GX020060.MP4 is not on this machine")

    def run(name):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(io.StringIO()):
            code = _footage.run((passes, fails, skips), ["prog", "--footage", name])
        return code, buf.getvalue()

    assert run("passes") == (0, "ok  passes\n")
    code, out = run("fails")
    assert code == 1 and "FAIL fails: the figure moved" in out, (code, out)
    code, out = run("skips")
    assert code == _footage.SKIP_RETURN_CODE and code not in (0, 1), code
    assert out == "SKIPPED skips: /nowhere/GX020060.MP4 is not on this machine\n", out
    code, out = run("typo")
    assert code == 2 and "not a real-footage check" in out, (code, out)
    print("test_the_runner_tells_a_pass_a_failure_and_a_skip_apart OK")


def test_footage_missing_raised_outside_the_runner_names_the_fix():
    """The tripwire: a footage check left in its file's ordinary run raises there, uncaught, and
    the message says how to register it rather than only that footage is missing."""
    msg = str(_footage.FootageMissing("PACER_MEASURED_FIGURES_DIR is not set"))
    assert "FOOTAGE_CHECKS" in msg and "add_footage_test" in msg, msg
    print("test_footage_missing_raised_outside_the_runner_names_the_fix OK")


# ----------------------------------------------------------------------------- declared vs registered
def test_every_footage_check_that_existed_when_d24_went_is_still_one():
    """The floor. Un-declaring one of these puts it back in its file's ordinary run, where a
    missing recording is — at best — an exception, and at worst a vacuous pass again."""
    declared = set(_declared())
    lost = [c for c in _AT_G1 if c not in declared]
    assert not lost, (
        f"{len(lost)} real-footage check(s) are no longer declared in their file's FOOTAGE_CHECKS: "
        f"{lost}. Without that they are not registered as footage.<check>, and nothing reports "
        "them as skipped when their recording is absent")
    print(f"test_every_footage_check_that_existed_when_d24_went_is_still_one OK "
          f"({len(_AT_G1)} at G1, {len(declared)} declared now)")


def test_every_footage_check_is_registered_to_report_a_skip():
    """Declared <-> registered, one to one, read from `ctest --show-only=json-v1`: the name is
    `footage.<check>`, the command runs THAT check of THAT file, SKIP_RETURN_CODE is the
    runner's, and it holds RESOURCE_LOCK footage. Without the property CTest reads the skip code as
    a plain failure; without the registration the check never runs at all; without the lock the
    parallel suite runs these ffmpeg-heavy checks side by side."""
    regs = _registrations()
    declared = _declared()
    assert declared, "no file declares FOOTAGE_CHECKS — this check has gone vacuous"
    problems = []
    for fname, check in declared:
        reg = regs.get(f"footage.{check}")
        if reg is None:
            problems.append(f"{fname}::{check} has no registration — add "
                            f"`add_footage_test({fname[:-3]} {check})` to tests/CMakeLists.txt")
            continue
        cmd = reg["command"]
        if cmd[-3:] != [os.path.join(_TESTS, fname), _footage.FLAG, check]:
            problems.append(f"footage.{check} runs {cmd[1:]}, not {fname} --footage {check}")
        if reg["properties"].get("SKIP_RETURN_CODE") != _footage.SKIP_RETURN_CODE:
            problems.append(f"footage.{check} has SKIP_RETURN_CODE "
                            f"{reg['properties'].get('SKIP_RETURN_CODE')}, not {_footage.SKIP_RETURN_CODE}")
        # `pixi run test-footage` selects on this label (G2); without it the check never runs there.
        if "footage" not in (reg["properties"].get("LABELS") or []):
            problems.append(f"footage.{check} carries LABELS {reg['properties'].get('LABELS')}, "
                            "not `footage` — `pixi run test-footage` (`ctest -L footage`) skips it")
        # `pixi run test` runs four tests at once (CTEST_PARALLEL_LEVEL); the shared lock is what
        # keeps these checks — one re-encodes a whole lap twice — to one at a time.
        lock = reg["properties"].get("RESOURCE_LOCK") or []
        if "footage" not in ([lock] if isinstance(lock, str) else lock):
            problems.append(f"footage.{check} holds RESOURCE_LOCK {lock or None}, not `footage` — "
                            "a parallel `pixi run test` would run it beside the other heavy checks")
    names = {f"footage.{c}" for _, c in declared}
    orphans = sorted(n for n in regs if n.startswith("footage.") and n not in names)
    problems += [f"{n} is registered but no file declares it in FOOTAGE_CHECKS" for n in orphans]
    for name, reg in regs.items():
        if _footage.FLAG in reg["command"] and not name.startswith("footage."):
            problems.append(f"{name} passes {_footage.FLAG} but is not named footage.*")
    assert not problems, "real-footage registrations are out of step:\n  " + "\n  ".join(problems)
    print(f"test_every_footage_check_is_registered_to_report_a_skip OK ({len(declared)} checks)")


# ----------------------------------------------------------------------------- the negative control
def test_a_footage_check_without_its_recording_is_reported_skipped_by_name():
    """THE NEGATIVE CONTROL. Each declared check (and the G1 floor, so an empty declaration cannot
    make this vacuous), run the way its registration runs it, on a HOME with no footage and no
    footage variable set, must exit with the skip code and print `SKIPPED <its name>`.

    On the tree before this change the same command ignored `--footage`, ran the whole file, and
    exited 0 — `ok  test_real_render_smoke_if_ffmpeg_and_media` and "ALL 84 export-video tests
    passed" with no recording anywhere. That is the vacuous pass, stated as a failure here."""
    checks = sorted(set(_declared()) | set(_AT_G1))
    wrong = []
    with tempfile.TemporaryDirectory(prefix="pacer-g1-home-") as home:
        os.makedirs(os.path.join(home, "Desktop"))           # a Desktop, with nothing on it
        env = _no_footage_env(home)
        for fname, check in checks:
            proc = subprocess.run([sys.executable, os.path.join(_TESTS, fname), _footage.FLAG, check],
                                  cwd=_REPO, env=env, capture_output=True, text=True, timeout=300)
            out = proc.stdout
            if proc.returncode == _footage.SKIP_RETURN_CODE and f"SKIPPED {check}: " in out:
                continue
            vacuous = proc.returncode == 0
            said = [ln for ln in out.splitlines() if check in ln or "passed" in ln]
            tail = "\n      ".join(said[-3:] or (out + proc.stderr).strip().splitlines()[-4:])
            wrong.append(f"{fname} {_footage.FLAG} {check}: exit {proc.returncode}"
                         + (" — REPORTED AS A PASS with no recording anywhere" if vacuous else "")
                         + f"\n      {tail}")
    assert not wrong, (
        f"{len(wrong)} of {len(checks)} real-footage checks, run with no footage, are not reported "
        f"as skipped (exit {_footage.SKIP_RETURN_CODE} + `SKIPPED <name>`):\n  " + "\n  ".join(wrong))
    print(f"test_a_footage_check_without_its_recording_is_reported_skipped_by_name OK "
          f"({len(checks)} checks, each exit {_footage.SKIP_RETURN_CODE})")


def test_a_named_recording_that_is_missing_fails_rather_than_skips():
    """Skip only what nobody asked for. A default that is not here is a skip; a recording the
    operator NAMED that is not here is a mistake, and skipping it would be the same no-op again."""
    saved = {k: os.environ.get(k) for k in _FOOTAGE_ENVS + ("HOME", _footage.DEFAULTS_ENV)}
    try:
        with tempfile.TemporaryDirectory(prefix="pacer-g1-named-") as home:
            for k in _FOOTAGE_ENVS + (_footage.DEFAULTS_ENV,):
                os.environ.pop(k, None)
            os.environ["HOME"] = home
            os.makedirs(os.path.join(home, "Desktop"))      # a Desktop, with nothing on it
            for fn, args in _LOOKUPS:
                try:
                    fn(*args)
                except _footage.FootageMissing as exc:
                    assert home in exc.reason, exc.reason
                else:
                    raise AssertionError(f"{fn.__name__}{args} found footage under an empty HOME")

            ghost = os.path.join(home, "Desktop", "GX010064.MP4")
            for env, fn, args in ((dev_footage.RECORDING_ENV, _footage.recording, ()),
                                  (dev_footage.REFERENCE_ENV, _footage.pair, ()),
                                  *(("PACER_IDEAL_TABLE_MP4", fn, args) for fn, args in _LOOKUPS
                                    if fn is _footage.recording_sets),
                                  *(("PACER_MEASURED_FIGURES_DIR", fn, args) for fn, args in _LOOKUPS
                                    if fn is _footage.directory)):
                os.environ[env] = ghost
                try:
                    fn(*args)
                except _footage.FootageMissing as exc:
                    raise AssertionError(f"{env} NAMED {ghost}, which is not there, and the check "
                                         f"skipped instead of failing: {exc.reason}") from None
                except AssertionError as exc:
                    assert ghost in str(exc), f"the failure must name what was asked for: {exc}"
                else:
                    raise AssertionError(f"{env}={ghost} resolved to a file that does not exist")
                finally:
                    os.environ.pop(env, None)

            real = os.path.join(home, "rec.MP4")
            open(real, "wb").close()
            os.environ[dev_footage.RECORDING_ENV] = real
            assert _footage.recording() == real, "a named recording that exists must be used as named"
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("test_a_named_recording_that_is_missing_fails_rather_than_skips OK")


# ----------------------------------------------------------------------------- one way in
def _code_constants(tree: ast.Module):
    """String constants that are not docstrings."""
    docs = set()
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            b = n.body[0] if n.body else None
            if isinstance(b, ast.Expr) and isinstance(b.value, ast.Constant):
                docs.add(id(b.value))
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs:
            yield n


def test_footage_is_found_only_through_the_helper():
    """Every earlier real-footage check found its recording its own way — `expanduser` on a
    hard-coded `~/Desktop/D24/...`, behind its own variable — and skipped its own way. A test that
    does that again escapes both the one variable and the skip report, so no test file may."""
    offenders = []
    for name in _test_files():
        if name == os.path.basename(__file__):
            continue
        tree = ast.parse(open(os.path.join(_TESTS, name), encoding="utf-8").read())
        for n in ast.walk(tree):
            # `expanduser("~")` is HOME itself (the app-support jail's own check spells the real
            # directory that way); any other argument is a path under it, which is what footage is.
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                    and n.func.attr == "expanduser"
                    and not (len(n.args) == 1 and isinstance(n.args[0], ast.Constant)
                             and n.args[0].value == "~")):
                offenders.append(f"{name}:{n.lineno} expands a path under HOME — resolve footage "
                                 "through tests/_footage.py")
        for c in _code_constants(tree):
            if c.value.startswith("~/"):
                offenders.append(f"{name}:{c.lineno} hard-codes {c.value!r}")
            if c.value in _RETIRED_ENVS:
                offenders.append(f"{name}:{c.lineno} reads the retired {c.value}; it is "
                                 f"{dev_footage.RECORDING_ENV} now")
    assert not offenders, "real footage found outside tests/_footage.py:\n  " + "\n  ".join(offenders)
    print("test_footage_is_found_only_through_the_helper OK")


def test_the_golden_dump_reads_the_same_variable_and_default():
    """"One way to point the real-footage checks at a recording" includes the golden dump: its REAL
    is `studio.dev.footage.recording_path()`, not a second copy of the variable and the default."""
    src = open(os.path.join(_REPO, "studio", "dev", "golden_session_dump.py"), encoding="utf-8").read()
    tree = ast.parse(src)
    real = [n.value for n in tree.body if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "REAL" for t in n.targets)]
    assert len(real) == 1, f"expected one module-level REAL in the golden dump, found {len(real)}"
    call = real[0]
    assert (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
            and call.func.attr == "recording_path"), (
        f"golden_session_dump.REAL = {ast.unparse(call)} — it must be footage.recording_path(), "
        "so the dump and the real-footage tests cannot drift onto different recordings")
    for c in _code_constants(tree):
        assert not c.value.startswith("~/"), f"the golden dump hard-codes {c.value!r} again"
    print("test_the_golden_dump_reads_the_same_variable_and_default OK")


# ----------------------------------------------------------------------------- the defaults (G2)
def _desktop_folder(default: str) -> str:
    """The folder a `~/Desktop/<folder>/<chapter>` default names."""
    prefix = dev_footage.DESKTOP + "/"
    assert default.startswith(prefix), f"{default!r} is not a recording on the Desktop"
    return default[len(prefix):].split("/")[0]


def test_every_default_names_a_recording_still_on_the_machine():
    """G2. Every recording a footage check or the golden dump falls back on is one of the Desktop
    working set, never one `_stale.GONE` lists. A default on a recording that has left the machine
    turns its checks into skips on every run, for good, and a skip is not coverage: from 2026-09-19
    until G2 all fourteen checks and the dump were in exactly that state, their defaults on D24.

    Also held: the compare pair is two DIFFERENT recordings (one recording cannot tell pane B's file
    from pane A's), and the ideal-lap table's default rows name no recording that left either."""
    import _stale
    import test_ideal_sample_table as ideal

    defaults = {name: getattr(dev_footage, name)
                for name in ("RECORDING_DEFAULT", "PAIR_RECORDING_DEFAULT", "REFERENCE_DEFAULT")}
    problems = [f"studio/dev/footage.py {name} = {path!r} names {_desktop_folder(path)!r}, which "
                f"_stale.GONE says left the machine — every check reading it would only ever skip"
                for name, path in defaults.items() if _desktop_folder(path) in _stale.GONE]
    working_set = (dev_footage.SANDOWN_3H, dev_footage.SD_19_09, dev_footage.MK_18_09)
    problems += [f"{name} = {path!r} is not one of the working set {working_set}"
                 for name, path in defaults.items() if path not in working_set]
    pair = (defaults["PAIR_RECORDING_DEFAULT"], defaults["REFERENCE_DEFAULT"])
    if _desktop_folder(pair[0]) == _desktop_folder(pair[1]):
        problems.append(f"the compare pair's defaults are one recording: {pair}")
    problems += [f"the ideal-lap table's row {name!r} names {folder!r}, which left the machine"
                 for name, (folder, _files) in ideal.ROW_RECORDINGS.items() if folder in _stale.GONE]
    assert not problems, "footage defaults that cannot run here:\n  " + "\n  ".join(problems)
    print(f"test_every_default_names_a_recording_still_on_the_machine OK ({len(defaults)} defaults, "
          f"{len(ideal.ROW_RECORDINGS)} table rows)")


def _stand_in_desktop(home: str) -> list[str]:
    """Create an EMPTY stand-in, under `home`, for every file a default names — the recordings in
    studio/dev/footage.py, the ideal-lap table's rows and `_STAND_IN_SET` — so a lookup that goes on
    to open one opens nothing real. Returns what it made."""
    import test_ideal_sample_table as ideal

    cut = len(dev_footage.DESKTOP) + 1
    rel = {p[cut:] for p in (dev_footage.RECORDING_DEFAULT, dev_footage.PAIR_RECORDING_DEFAULT,
                             dev_footage.REFERENCE_DEFAULT)}
    rel |= {os.path.join(folder, f) for folder, files in ideal.ROW_RECORDINGS.values() for f in files}
    rel |= set(_STAND_IN_SET)
    made = []
    for r in sorted(rel):
        path = os.path.join(home, "Desktop", r)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()
        made.append(path)
    return made


def _one_check_per_lookup() -> list[tuple[str, str]]:
    """A declared footage check for each lookup in tests/_footage.py, found in its file's source."""
    by_lookup: dict[str, tuple[str, str] | None] = dict.fromkeys(
        ("_footage.recording()", "_footage.pair()", "_footage.recording_sets(", "_footage.directory("))
    for fname, check in _declared():
        src = open(os.path.join(_TESTS, fname), encoding="utf-8").read()
        for key, found in by_lookup.items():
            if found is None and key in src:
                by_lookup[key] = (fname, check)
    assert all(by_lookup.values()), f"no declared check uses one of the lookups: {by_lookup}"
    return sorted(set(by_lookup.values()))


def test_the_fast_loop_reports_a_present_default_skipped_and_still_runs_a_named_one():
    """`pixi run test-fast` sets PACER_FOOTAGE_DEFAULTS=off (G2): the defaults now name recordings
    that ARE on the dev Mac, and the fast loop reports the checks rather than paying minutes to run
    them. Off must mean exactly that and no more:
      * on a Desktop that HOLDS every default (empty stand-ins), each lookup returns its default
        when the variable is unset — a present default is used, which is the whole of G2;
      * with it off, each raises FootageMissing saying where the check does run;
      * a recording the operator NAMED is still used with it off (off is about defaults);
      * any other value fails rather than guessing;
      * and THE REGISTRATIONS THEMSELVES, one per lookup, run as CTest runs them under test-fast's
        environment with their footage present, exit with the skip code naming `test-footage`."""
    saved = {k: os.environ.get(k) for k in _FOOTAGE_ENVS + ("HOME", _footage.DEFAULTS_ENV)}
    checks = _one_check_per_lookup()
    try:
        with tempfile.TemporaryDirectory(prefix="pacer-g2-home-") as home:
            for k in _FOOTAGE_ENVS + (_footage.DEFAULTS_ENV,):
                os.environ.pop(k, None)
            os.environ["HOME"] = home
            made = _stand_in_desktop(home)
            desktop = os.path.join(home, "Desktop")
            used = {fn.__name__: fn(*args) for fn, args in _LOOKUPS}
            cut = len(dev_footage.DESKTOP) + 1
            assert used["recording"] == os.path.join(desktop, dev_footage.RECORDING_DEFAULT[cut:]), used
            assert all(p.startswith(desktop + os.sep) for p in used["pair"]), used
            assert used["recording_sets"] == [[os.path.join(desktop, p) for p in _STAND_IN_SET]], used
            assert used["directory"] == desktop, used

            os.environ[_footage.DEFAULTS_ENV] = "off"
            for fn, args in _LOOKUPS:
                try:
                    fn(*args)
                except _footage.FootageMissing as exc:
                    assert "test-footage" in exc.reason and _footage.DEFAULTS_ENV in exc.reason, exc.reason
                else:
                    raise AssertionError(f"{fn.__name__}{args} used a default with "
                                         f"{_footage.DEFAULTS_ENV}=off")
            os.environ[dev_footage.RECORDING_ENV] = made[0]
            assert _footage.recording() == made[0], "a NAMED recording must still run with defaults off"
            os.environ.pop(dev_footage.RECORDING_ENV)

            os.environ[_footage.DEFAULTS_ENV] = "of"
            try:
                _footage.recording()
            except AssertionError as exc:
                assert "'of'" in str(exc), exc
            else:
                raise AssertionError(f"{_footage.DEFAULTS_ENV}='of' was read as something")
            os.environ.pop(_footage.DEFAULTS_ENV)

            # One registration per lookup, exactly as `pixi run test-fast` runs it, footage present.
            env = _no_footage_env(home)
            env[_footage.DEFAULTS_ENV] = "off"
            wrong = []
            for fname, check in checks:
                proc = subprocess.run([sys.executable, os.path.join(_TESTS, fname), _footage.FLAG, check],
                                      cwd=_REPO, env=env, capture_output=True, text=True, timeout=300)
                line = next((ln for ln in proc.stdout.splitlines() if ln.startswith("SKIPPED")), "")
                if proc.returncode != _footage.SKIP_RETURN_CODE or "test-footage" not in line:
                    wrong.append(f"{fname} {check}: exit {proc.returncode}, "
                                 f"{line or (proc.stdout + proc.stderr).strip()[-300:]}")
            assert not wrong, ("with the defaults off and the footage PRESENT, these were not reported "
                               "skipped towards `pixi run test-footage`:\n  " + "\n  ".join(wrong))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("test_the_fast_loop_reports_a_present_default_skipped_and_still_runs_a_named_one OK "
          f"({len(_LOOKUPS)} lookups, {len(checks)} registrations)")


def test_a_timing_sheet_is_found_like_footage():
    """`_footage.timing_sheet` (B3) keeps these rules for the one input that is not a recording, the
    official timing sheet an accuracy row was locked to: a default that is not here is a SKIP naming
    it, `PACER_FOOTAGE_DEFAULTS=off` skips it towards test-footage, a sheet in a folder the operator
    NAMED must be there. And its default reaches the MAIN checkout's gitignored folder from a linked
    worktree — whose `.git` is a one-line pointer file — or a check reading one would skip in every
    agent's worktree on the very Mac that holds the sheet."""
    names = (dev_footage.TIMING_ENV, _footage.DEFAULTS_ENV)
    saved = {k: os.environ.get(k) for k in names}
    try:
        with tempfile.TemporaryDirectory(prefix="pacer-b3-timing-") as d:
            for k in names:
                os.environ.pop(k, None)
            main = os.path.join(d, "main")
            tree = os.path.join(main, ".claude", "worktrees", "agent-x")
            os.makedirs(os.path.join(main, ".git", "worktrees", "agent-x"))
            os.makedirs(tree)
            with open(os.path.join(tree, ".git"), "w", encoding="utf-8") as f:
                f.write(f"gitdir: {main}/.git/worktrees/agent-x\n")
            want = os.path.join(main, ".claude", "reference", "timing")
            got = (dev_footage.timing_dir(tree), dev_footage.timing_dir(main))
            assert got == (want, want), f"worktree and main checkout resolve {got}, not {want}"

            for env, value, needle in ((None, None, "no-such-sheet.csv"),
                                       (_footage.DEFAULTS_ENV, "off", "test-footage")):
                if env:
                    os.environ[env] = value
                try:
                    _footage.timing_sheet("no-such-sheet.csv")
                except _footage.FootageMissing as exc:
                    assert needle in exc.reason, exc.reason
                else:
                    raise AssertionError(f"an absent default sheet was not a skip ({env}={value})")

            os.environ[dev_footage.TIMING_ENV] = d          # NAMED — the defaults' being off is moot
            try:
                _footage.timing_sheet("no-such-sheet.csv")
            except AssertionError as exc:
                assert "no-such-sheet.csv" in str(exc), exc
            else:
                raise AssertionError("a sheet missing from a NAMED folder skipped instead of failing")
            open(os.path.join(d, "day.csv"), "w").close()
            assert _footage.timing_sheet("day.csv") == os.path.join(d, "day.csv")
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
    print("test_a_timing_sheet_is_found_like_footage OK")


def test_the_fast_loop_reports_footage_and_the_footage_task_runs_it():
    """Where the checks run, read off pyproject.toml the way pixi reads it. `test-fast` turns the
    defaults off and EXCLUDES no footage registration — an excluded test vanishes from ctest's
    output, and a check that cannot run has to say so (#341). `test-footage` selects them by label,
    under `caffeinate -si` (a Mac that sleeps mid-run reads as a hang, #282). `test`, the pre-PR
    gate, leaves the defaults on, so on the dev Mac it runs every one."""
    import re
    import tomllib

    with open(os.path.join(_REPO, "pyproject.toml"), "rb") as f:
        tasks = tomllib.load(f)["tool"]["pixi"]["tasks"]
    fast, full, foot = tasks["test-fast"], tasks["test"], tasks["test-footage"]
    problems = []
    if (fast.get("env") or {}).get(_footage.DEFAULTS_ENV) != "off":
        problems.append(f"test-fast does not set {_footage.DEFAULTS_ENV}=off: {fast.get('env')}")
    if _footage.DEFAULTS_ENV in (full.get("env") or {}):
        problems.append(f"test sets {_footage.DEFAULTS_ENV}: the pre-PR gate would skip real footage")
    footage = sorted(n for n in _registrations() if n.startswith("footage."))
    for pattern in re.findall(r"-E\s+'([^']*)'", fast["cmd"]):
        problems += [f"test-fast's -E {pattern!r} excludes {n}, so it vanishes from the report"
                     for n in footage if re.search(pattern, n)]
    if re.search(r"(^|\s)-LE(\s|$)", fast["cmd"]):
        problems.append(f"test-fast excludes a label: {fast['cmd']!r}")
    if not re.search(r"\s-L\s+footage(\s|$)", foot["cmd"]) or "caffeinate -si" not in foot["cmd"]:
        problems.append(f"test-footage must be `caffeinate -si ctest … -L footage`: {foot['cmd']!r}")
    assert not problems, "the footage tasks are out of step:\n  " + "\n  ".join(problems)
    print(f"test_the_fast_loop_reports_footage_and_the_footage_task_runs_it OK ({len(footage)} checks)")


def _run_all():
    test_the_runner_tells_a_pass_a_failure_and_a_skip_apart()
    test_footage_missing_raised_outside_the_runner_names_the_fix()
    test_every_footage_check_that_existed_when_d24_went_is_still_one()
    test_every_footage_check_is_registered_to_report_a_skip()
    test_a_footage_check_without_its_recording_is_reported_skipped_by_name()
    test_a_named_recording_that_is_missing_fails_rather_than_skips()
    test_footage_is_found_only_through_the_helper()
    test_the_golden_dump_reads_the_same_variable_and_default()
    test_every_default_names_a_recording_still_on_the_machine()
    test_the_fast_loop_reports_a_present_default_skipped_and_still_runs_a_named_one()
    test_a_timing_sheet_is_found_like_footage()
    test_the_fast_loop_reports_footage_and_the_footage_task_runs_it()
    print("\nfootage-check tests passed")


if __name__ == "__main__":
    _run_all()
