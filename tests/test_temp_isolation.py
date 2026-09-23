"""FIXED-NAME TEMP PATHS COLLIDE BETWEEN CONCURRENT RUNS — no test may build one.

`$TMPDIR` on this machine is `/tmp/claude-501`: keyed on the uid, SHARED by every agent session
and every developer shell on the box. A test that writes `os.path.join($TMPDIR, "pacer_cmp_a.mp4")`
therefore does not name a file of its own — it names the same file in every run on the machine.

WHAT THAT COST. `tests/test_export_compare.py` wrote three such clips and deleted them in a
`finally`. Two lanes running the suite at once did not merely overwrite each other's bytes: one
lane's teardown REMOVED a clip the other lane's `ffprobe` was still decoding, so the second lane
died inside `probe_video_size` with a failure that reads exactly like a real export defect and
nothing like the Mac-sleep `Timeout` the usual triage knows. It was diagnosed twice, by two
different agents (PRs #296 and #298), at the cost of a full diagnosis each time. ctest now runs
four tests at once (`CTEST_PARALLEL_LEVEL`, pyproject.toml), so two registrations of ONE run can
collide as well as two runs — and it stays intermittent either way: a green run proves nothing
about it, which is why this gate reads the source instead.

WHAT THIS TEST PROVES, EXACTLY. That no scanned file joins the shared temp root with a name it
chose itself, i.e. that two runs allocate DISTINCT PATHS. It does NOT prove the absence of a race:
it reads the source, not the filesystem. Two runs that each allocate a `tempfile.mkdtemp()`
directory cannot collide by construction, and that is the property being pinned — nothing more.

INERT `/tmp/...` STRING LITERALS ARE DELIBERATELY NOT FLAGGED. About forty of them are stand-in
paths handed to code that never opens them (`_FakeSpec("/tmp/ride_lap1.mp4")`, `ChapterMap(["/v/A.
MP4"])`), and there is no `open("/tmp` or `makedirs("/tmp` anywhere under `tests/`. Flagging a
string that is never opened would bury the real sites in noise.

`studio/dev/` is scanned alongside `tests/` because the probes write REVIEW ARTEFACTS — PNGs, JSON
dumps — into the same shared directory, and two lanes overwriting a screenshot is a silent wrong
answer (you read the other lane's picture and believe it) rather than a crash.

Pure AST: no Qt, no bindings, no telemetry file, no temp files of its own.

Run: python tests/test_temp_isolation.py
"""
import ast
import os

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Scanned, relative to the repo root. Both are run-from-source directories; app code under
# `studio/` is out of scope here (it takes its temp dir from the caller and names files through
# `tempfile.mkstemp`, which is unique per call).
SCANNED_DIRS = ("tests", "studio/dev")

# The environment variable that names the SHARED temp root.
_SHARED_ENV = "TMPDIR"

# Files that still violate, each with the reason. This carve-out is pinned in BOTH directions:
# a file that stops violating fails `test_the_pending_carve_out_is_still_true`, so an entry cannot
# quietly outlive the defect it documents.
PENDING = {
    "tests/test_export_video.py": (
        "work package X3 owns this file and was in flight when this gate landed — it holds about "
        "twenty `f9_*` fixed names. DELETE THIS ENTRY in the same commit that gives that file a "
        "private temp root; this gate fails until you do."),
}

# The files fixed with this gate. Checked POSITIVELY as well: not joining the shared root is
# necessary but not sufficient, and an edit that drops the private root while keeping the joins
# out of sight (say, by inlining a path) should still fail.
PRIVATE_ROOT_FILES = (
    "tests/test_export_compare.py",
    "tests/test_export_seam.py",
    "tests/test_export_padding.py",
)

_PRIVATE_ROOT_CALLS = ("TemporaryDirectory", "mkdtemp", "NamedTemporaryFile", "mkstemp")


# ============================================================================== the scanner
def _is_shared_root(node, names) -> bool:
    """Does `node` evaluate to the process-SHARED temp root, rather than a directory of this run?

    The three spellings in this repo plus their derivatives: a name bound to any of them, and
    anything called ON one (`os.environ.get("TMPDIR", "/tmp").rstrip("/")` is still the shared
    root)."""
    if isinstance(node, ast.Name):
        return node.id in names
    if isinstance(node, ast.Subscript):                      # os.environ["TMPDIR"]
        return isinstance(node.slice, ast.Constant) and node.slice.value == _SHARED_ENV
    if isinstance(node, ast.Call):
        fn = node.func
        called = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
        if called in ("get", "getenv"):                      # os.environ.get / os.getenv
            first = node.args[0] if node.args else None
            if isinstance(first, ast.Constant) and first.value == _SHARED_ENV:
                return True
        if called == "gettempdir":                           # tempfile.gettempdir()
            return True
        if isinstance(fn, ast.Attribute):                    # a method call on a shared root
            return _is_shared_root(fn.value, names)
    return False


def _shared_names(tree) -> set[str]:
    """Every local name bound to the shared root, to a fixed point (`TMP = os.environ.get(...)`,
    then `tmp = TMP`)."""
    names: set[str] = set()
    while True:
        grew = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and _is_shared_root(node.value, names):
                for tgt in node.targets:
                    if isinstance(tgt, ast.Name) and tgt.id not in names:
                        names.add(tgt.id)
                        grew = True
        if not grew:
            return names


def _is_os_path_join(fn) -> bool:
    return (isinstance(fn, ast.Attribute) and fn.attr == "join"
            and isinstance(fn.value, ast.Attribute) and fn.value.attr == "path")


def violations_in_source(src: str, rel: str) -> list[tuple[str, int, str]]:
    """Every place `src` builds a path UNDER the shared root: `os.path.join(root, ...)`,
    `root + "/sub"`, `Path(root) / "f"`, and f-strings interpolating it."""
    tree = ast.parse(src)
    names = _shared_names(tree)
    found: list[tuple[str, int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_os_path_join(node.func):
            if node.args and _is_shared_root(node.args[0], names):
                found.append((rel, node.lineno, ast.unparse(node)))
        elif isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Div)):
            left = node.left
            if isinstance(left, ast.Call) and any(_is_shared_root(a, names) for a in left.args):
                found.append((rel, node.lineno, ast.unparse(node)))   # pathlib.Path(tmp) / "x"
            elif _is_shared_root(left, names):
                found.append((rel, node.lineno, ast.unparse(node)))
        elif isinstance(node, ast.JoinedStr):
            if any(_is_shared_root(v.value, names)
                   for v in node.values if isinstance(v, ast.FormattedValue)):
                found.append((rel, node.lineno, ast.unparse(node)))
    return found


def all_violations() -> list[tuple[str, int, str]]:
    """Every scanned file, PENDING included — the carve-out is applied by the caller, so an
    inventory can be printed in full."""
    found: list[tuple[str, int, str]] = []
    for rel_dir in SCANNED_DIRS:
        root = os.path.join(_REPO, *rel_dir.split("/"))
        for name in sorted(os.listdir(root)):
            if not name.endswith(".py"):
                continue
            with open(os.path.join(root, name), encoding="utf-8") as fh:
                found += violations_in_source(fh.read(), f"{rel_dir}/{name}")
    return found


# ================================================================================ the gates
def test_no_scanned_file_builds_a_fixed_path_in_the_shared_tmpdir():
    """THE GATE. Every path a test writes has to be unique to the run that writes it."""
    offenders = [v for v in all_violations() if v[0] not in PENDING]
    assert not offenders, (
        "these build a path under the SHARED $TMPDIR, so two concurrent runs write — and delete "
        "— the same file:\n"
        + "\n".join(f"  {f}:{ln}  {src}" for f, ln, src in offenders)
        + "\n\nAllocate a temp directory PER RUN instead, at module scope:\n"
          '    _TMP = tempfile.TemporaryDirectory(prefix="pacer-test-<file>-")\n'
          "    TMP = _TMP.name\n"
          "and join the same filenames into that. The object is held at module scope so its "
          "finalizer removes the directory when the process exits.")
    print(f"test_no_scanned_file_builds_a_fixed_path_in_the_shared_tmpdir OK — "
          f"{len(SCANNED_DIRS)} directories scanned, {len(PENDING)} carve-out(s)")


def test_the_pending_carve_out_is_still_true():
    """A carve-out that has been fixed must be DELETED, not left to rot. Every entry has to name a
    file that exists and still violates — otherwise the list quietly stops describing the tree and
    the next file added to it inherits an allow-list nobody re-checks."""
    found = {v[0] for v in all_violations()}
    for rel, why in sorted(PENDING.items()):
        path = os.path.join(_REPO, *rel.split("/"))
        assert os.path.exists(path), f"PENDING names {rel}, which does not exist"
        assert rel in found, (
            f"{rel} is listed as PENDING but no longer builds a fixed path under the shared "
            f"$TMPDIR. Delete its entry from PENDING in tests/test_temp_isolation.py.\n"
            f"The entry read: {why}")
    print(f"test_the_pending_carve_out_is_still_true OK — {len(PENDING)} entry(ies), all still "
          f"violating")


def test_the_scanner_still_recognises_a_collision_it_is_shown():
    """THE NEGATIVE CONTROL. A scanner that silently stops matching passes the gate above for
    every file at once, which is the failure mode an AST check is most prone to. Each shape below
    is one that really occurred in this repo."""
    bad = {
        "join with a literal": 'import os\np = os.path.join(os.environ.get("TMPDIR", "/tmp"), '
                               '"pacer_cmp_a.mp4")\n',
        "join through a name": 'import os\nTMP = os.environ.get("TMPDIR", "/tmp")\n'
                               'p = os.path.join(TMP, "pacer_seam_a.mp4")\n',
        "name of a name":      'import os\nTMP = os.environ.get("TMPDIR", "/tmp")\ntmp = TMP\n'
                               'p = os.path.join(tmp, "out.mp4")\n',
        "f-string":            'import os\ntmp = os.environ["TMPDIR"]\np = f"{tmp}/out.mp4"\n',
        "string concatenation": 'import os\n'
                                'd = os.environ.get("TMPDIR", "/tmp").rstrip("/") + "/denoise"\n',
        "gettempdir":          'import os, tempfile\n'
                               'p = os.path.join(tempfile.gettempdir(), "shot.png")\n',
        "pathlib":             'from pathlib import Path\nimport os\n'
                               'p = Path(os.environ["TMPDIR"]) / "out.mp4"\n',
    }
    for label, src in bad.items():
        assert violations_in_source(src, "<probe>"), f"the scanner missed the {label} shape:\n{src}"

    good = {
        "private directory": 'import os, tempfile\nTMP = tempfile.mkdtemp(prefix="pacer-test-")\n'
                             'p = os.path.join(TMP, "pacer_cmp_a.mp4")\n',
        "inert stand-in":    'spec = _FakeSpec("/tmp/ride_lap1.mp4")\n',
        "shared root handed to a unique-name allocator":
            'import os, tempfile\n'
            'fd, p = tempfile.mkstemp(dir=os.environ.get("TMPDIR", "/tmp"))\n',
    }
    for label, src in good.items():
        hits = violations_in_source(src, "<probe>")
        assert not hits, f"the scanner flagged the {label} shape, which is safe: {hits}"
    print(f"test_the_scanner_still_recognises_a_collision_it_is_shown OK — {len(bad)} colliding "
          f"shapes caught, {len(good)} safe shapes passed")


def test_the_fixed_export_tests_allocate_their_own_temp_root():
    """The positive half: each file this gate landed with must ASK for a private directory. Not
    joining the shared root is satisfied by a file that writes no temp files at all, so a future
    edit that drops the `TemporaryDirectory` and inlines paths would otherwise pass."""
    for rel in PRIVATE_ROOT_FILES:
        path = os.path.join(_REPO, *rel.split("/"))
        tree = ast.parse(open(path, encoding="utf-8").read())
        calls = {n.func.attr if isinstance(n.func, ast.Attribute) else getattr(n.func, "id", "")
                 for n in ast.walk(tree) if isinstance(n, ast.Call)}
        assert calls & set(_PRIVATE_ROOT_CALLS), (
            f"{rel} allocates no private temp path — it must take its temp root from "
            f"tempfile ({', '.join(_PRIVATE_ROOT_CALLS)}), not from the shared $TMPDIR.")
    print(f"test_the_fixed_export_tests_allocate_their_own_temp_root OK — "
          f"{len(PRIVATE_ROOT_FILES)} files")


if __name__ == "__main__":
    test_no_scanned_file_builds_a_fixed_path_in_the_shared_tmpdir()
    test_the_pending_carve_out_is_still_true()
    test_the_scanner_still_recognises_a_collision_it_is_shown()
    test_the_fixed_export_tests_allocate_their_own_temp_root()
    print("\nOK: every run allocates its own temp paths")
