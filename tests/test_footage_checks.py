"""A real-footage check without its recording is REPORTED AS SKIPPED, by name — never read as a pass.

G1, measured 2026-09-19, the day `~/Desktop/D24` went: eleven checks in four files re-measure
something on a real recording, and every one of them, finding none, printed a "skip" line and
returned. Their runners counted that as a pass (`ok  test_real_render_smoke_if_ffmpeg_and_media`,
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
  * footage is found only through `tests/_footage.py`, and the golden dump reads the same variable.

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
# pyproject.toml for a recording path, a footage variable or a skip branch (the PR has the table).
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
    ("test_measured_figures.py", "test_the_beat_rate_table_matches_the_footage"),
    ("test_measured_figures.py", "test_the_focus_tables_match_the_footage"),
)

# Every variable that points a check at footage. Unset in the negative control, so what it measures
# is "no footage", not "whatever the developer running it happens to have exported".
_FOOTAGE_ENVS = (dev_footage.RECORDING_ENV, dev_footage.REFERENCE_ENV,
                 "PACER_IDEAL_TABLE_MP4", "PACER_MEASURED_FIGURES_DIR")
# The variables the consolidation retired. A test reading one would be pointed nowhere.
_RETIRED_ENVS = ("PACER_REAL_MP4", "PACER_D24_MEDIA")


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
    """The environment CTest gives a footage registration, on a machine with no footage at all."""
    env = {k: v for k, v in os.environ.items() if k not in _FOOTAGE_ENVS + _RETIRED_ENVS}
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
    `footage.<check>`, the command runs THAT check of THAT file, and SKIP_RETURN_CODE is the
    runner's. Without the property CTest reads the skip code as a plain failure; without the
    registration the check never runs at all."""
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
    saved = {k: os.environ.get(k) for k in _FOOTAGE_ENVS + ("HOME",)}
    try:
        with tempfile.TemporaryDirectory(prefix="pacer-g1-named-") as home:
            for k in _FOOTAGE_ENVS:
                os.environ.pop(k, None)
            os.environ["HOME"] = home
            for fn in (_footage.recording, _footage.reference):
                try:
                    fn()
                except _footage.FootageMissing as exc:
                    assert home in exc.reason and "default" in exc.reason, exc.reason
                else:
                    raise AssertionError(f"{fn.__name__}() found a recording under an empty HOME")
            for fn, args in ((_footage.recording_list, ("PACER_IDEAL_TABLE_MP4", "chapters")),
                             (_footage.directory, ("PACER_MEASURED_FIGURES_DIR", "a folder"))):
                try:
                    fn(*args)
                except _footage.FootageMissing:
                    pass
                else:
                    raise AssertionError(f"{fn.__name__}{args} did not skip with its variable unset")

            ghost = os.path.join(home, "Desktop", "GX010064.MP4")
            for env, fn, args in ((dev_footage.RECORDING_ENV, _footage.recording, ()),
                                  (dev_footage.REFERENCE_ENV, _footage.reference, ()),
                                  ("PACER_IDEAL_TABLE_MP4", _footage.recording_list,
                                   ("PACER_IDEAL_TABLE_MP4", "chapters")),
                                  ("PACER_MEASURED_FIGURES_DIR", _footage.directory,
                                   ("PACER_MEASURED_FIGURES_DIR", "a folder"))):
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


def _run_all():
    test_the_runner_tells_a_pass_a_failure_and_a_skip_apart()
    test_footage_missing_raised_outside_the_runner_names_the_fix()
    test_every_footage_check_that_existed_when_d24_went_is_still_one()
    test_every_footage_check_is_registered_to_report_a_skip()
    test_a_footage_check_without_its_recording_is_reported_skipped_by_name()
    test_a_named_recording_that_is_missing_fails_rather_than_skips()
    test_footage_is_found_only_through_the_helper()
    test_the_golden_dump_reads_the_same_variable_and_default()
    print("\nfootage-check tests passed")


if __name__ == "__main__":
    _run_all()
