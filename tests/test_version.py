"""The release version lives in THREE files, and nothing used to check that they agree.

`studio/__init__.py:__version__` is the canonical one — the About card shows it and
`packaging/pacer.spec` regex-reads it into the .app's CFBundleVersion. But two `pyproject.toml`
files carry it as well and neither can import the canonical one: the root one is pip metadata AND
the string `packaging/build_macos.sh` names the .dmg from, and `bindings/pacer/pyproject.toml` is
the bindings package's own metadata. The release recipe in AGENTS.md named only two of the three
for four releases' worth of history, and a `grep __version__ tests/` returned nothing — so a bump
that missed a file would have shipped a `Pacer-Studio-<a>.dmg` whose About card read `<b>`, and
nothing in CI would have said a word.

Each site is read the way ITS OWN consumer reads it, not with one canonical parser:

  * `studio/__init__.py`  — `pacer.spec`'s literal regex, off disk, AND `import studio`. The spec
    falls back to "0.0.0" when its regex misses, silently, so "the regex still matches" is half
    the assertion and "it matches what the interpreter sees" is the other half.
  * `pyproject.toml`      — `build_macos.sh`'s `grep -m1 '^version' | sed -E 's/.*"(.*)".*/\\1/'`
    pipeline, reimplemented here, plus `tomllib` as the pip-metadata reading. Both, because the
    shell one is line-oriented and would happily read a commented-out or a second `version =`.
  * `bindings/pacer/pyproject.toml` — `tomllib`.

Plus the two release-recipe steps that travel with a bump: the changelog's newest released section
must BE this version, and every released section must have its compare link at the foot.

Pure stdlib — no Qt, no pacer, no telemetry file. Run:  python tests/test_version.py
"""
import os
import re
import sys
import tomllib

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _REPO)

_SEMVER = re.compile(r"^\d+\.\d+\.\d+$")


def _repo(*parts):
    return os.path.join(_REPO, *parts)


def _read(*parts):
    with open(_repo(*parts), encoding="utf-8") as fh:
        return fh.read()


# --------------------------------------------------------------- the three sites, read as shipped
def _spec_regex_version():
    """Exactly what packaging/pacer.spec:_studio_version() does — its regex, off disk.

    Returns None where the spec would silently return its "0.0.0" fallback."""
    m = re.search(r'__version__\s*=\s*["\']([^"\']+)["\']', _read("studio", "__init__.py"))
    return m.group(1) if m else None


def _build_macos_sh_version():
    """Exactly what packaging/build_macos.sh:31 does — the string the .dmg is NAMED from:

        grep -m1 '^version' pyproject.toml | sed -E 's/.*"(.*)".*/\\1/'
    """
    for line in _read("pyproject.toml").splitlines():
        if line.startswith("version"):                       # grep -m1 '^version'
            m = re.match(r'.*"(.*)".*', line)                # sed -E 's/.*"(.*)".*/\1/'
            return m.group(1) if m else None
    return None


def _toml_version(*parts):
    with open(_repo(*parts), "rb") as fh:
        return tomllib.load(fh)["project"]["version"]


def canonical_version():
    """studio/__init__.py's `__version__`, read OFF DISK the way `packaging/pacer.spec` reads it.

    Deliberately not `import studio`: the disk text is what gets stamped into the .app, and an
    import can serve a stale `__pycache__` entry (the pyc is invalidated on the source's mtime and
    SIZE, and one digit of a version is a same-size edit). The import is checked separately, below,
    against this."""
    v = _spec_regex_version()
    assert v is not None, (
        "packaging/pacer.spec's regex no longer matches studio/__init__.py — the spec would "
        "silently stamp the .app 0.0.0")
    return v


def test_the_three_version_sites_agree():
    import studio

    canonical = canonical_version()
    assert _SEMVER.match(canonical), f"studio/__init__.py __version__ is not x.y.z: {canonical!r}"

    # 1. The canonical file: what the spec stamps into the .app == what the About card imports.
    assert studio.__version__ == canonical, (
        f"`import studio` sees {studio.__version__!r} where packaging/pacer.spec's regex reads "
        f"{canonical!r} off disk — the About card and the .app's CFBundleVersion would disagree. "
        f"(If you just edited studio/__init__.py, this can also be a stale studio/__pycache__.)")

    # 2. pip metadata + the name build_macos.sh gives the .dmg.
    root_toml = _toml_version("pyproject.toml")
    root_sh = _build_macos_sh_version()
    assert root_sh == root_toml, (
        f"build_macos.sh's grep|sed reads {root_sh!r} from pyproject.toml where tomllib reads "
        f"{root_toml!r} — the .dmg would be named from the wrong line")
    assert root_toml == canonical, (
        f"pyproject.toml version = {root_toml!r} != studio/__init__.py __version__ {canonical!r}. "
        f"build_macos.sh would write Pacer-Studio-{root_toml}.dmg holding an app whose About "
        f"card reads {canonical}.")

    # 3. The bindings package — the site AGENTS.md's release recipe used to omit.
    bindings = _toml_version("bindings", "pacer", "pyproject.toml")
    assert bindings == canonical, (
        f"bindings/pacer/pyproject.toml version = {bindings!r} != studio/__init__.py "
        f"__version__ {canonical!r}")

    print(f"test_the_three_version_sites_agree OK ({canonical})")


# ------------------------------------------------------------------- the changelog moves with it
_RELEASED_HEADING = re.compile(r"^## \[(\d+\.\d+\.\d+)\]", re.MULTILINE)


def test_changelog_newest_release_is_this_version():
    """A bump and the `[Unreleased]` -> `[x.y.z] — date` retitle are one step of the recipe.

    `[Unreleased]` is deliberately ignored: between releases it sits above the newest released
    section, and the newest RELEASED section is the one the version must equal."""
    changelog = _read("CHANGELOG.md")
    released = _RELEASED_HEADING.findall(changelog)
    assert released, "CHANGELOG.md has no `## [x.y.z]` release heading"
    assert released[0] == canonical_version(), (
        f"CHANGELOG.md's newest release section is [{released[0]}] but studio/__init__.py "
        f"__version__ is {canonical_version()} — retitle `[Unreleased]` or fix the bump")
    print(f"test_changelog_newest_release_is_this_version OK ([{released[0]}])")


def test_every_released_section_has_its_compare_link():
    """The other half of the retitle: the link definitions at the foot. A `## [0.2.0]` heading with
    no `[0.2.0]:` definition renders as literal brackets on GitHub and in every markdown viewer."""
    changelog = _read("CHANGELOG.md")
    defined = set(re.findall(r"^\[(\d+\.\d+\.\d+)\]:\s*\S+", changelog, re.MULTILINE))
    for version in _RELEASED_HEADING.findall(changelog):
        assert version in defined, (
            f"CHANGELOG.md has a `## [{version}]` heading with no `[{version}]:` link definition "
            f"at the foot of the file")
    print(f"test_every_released_section_has_its_compare_link OK ({len(defined)} links)")


# ----------------------------------------------------- the changelog's shape, and its fragments
# Board review 2026-09-23 (R7 / OPS-2 / ARCH-2b): `[Unreleased]` had grown to 108 entries of a
# median 7 lines (0.1.0: 2; 0.2.0: 5), including a fourth group, Engineering, that the convention
# never had, and 54 % of September's PRs edited CHANGELOG.md, which made it the commonest merge
# collision. Nothing policed either. From 0.2.0 on, a PR writes `changes/<branch-slug>.md` and the
# release folds them in (studio/dev/changelog.py, which owns the rules these tests apply).
_CHANGES = _repo("changes")
# The two defects the shape guard exists for: a 4-line entry, and a group the convention lacks.
_PLANTED_SECTION = """
### Fixed

- A fixed thing that goes on
  and on
  and on
  for four lines (#1)

### Engineering

- An internal refactor (#2)
"""
_GOOD_FRAGMENT = "### Fixed\n\n- A lift is no longer read as a brake\n"


def _changelog_module():
    from studio.dev import changelog
    return changelog


def test_changelog_sections_after_0_2_0_keep_the_short_shape():
    """[Unreleased] and every release after 0.2.0: only Highlights / Added / Changed / Fixed, each
    entry at most 3 lines of at most 100 characters, every Added/Changed/Fixed entry ending in its
    `(#PR)`, an intro of at most 3 lines. 0.2.0 and 0.1.0 are history and stay as released."""
    cl = _changelog_module()
    text = _read("CHANGELOG.md")
    problems = cl.changelog_problems(text)
    assert not problems, (
        "CHANGELOG.md sections after 0.2.0 must keep the short shape (the long form belongs in the "
        "PR description; see studio/dev/changelog.py):\n  " + "\n  ".join(problems))
    # Not a check over nothing: [Unreleased] is shaped and has entries, and 0.2.0 is not shaped.
    shaped = [name for name, _h, _e in cl.sections(text) if cl.is_shaped(name)]
    assert "Unreleased" in shaped and "0.2.0" not in shaped, shaped
    lines = text.splitlines()
    head, end = next((h, e) for name, h, e in cl.sections(text) if name == "Unreleased")
    _intro, groups, _p = cl._parse(lines[head + 1:end], head + 2)
    entries = sum(len(es) for _g, _n, es in groups)
    assert entries >= 1, "[Unreleased] parsed to no entries — the parser no longer reads it"
    # Both directions: the checker fails the two defects it exists for.
    planted = cl.section_problems(_PLANTED_SECTION.splitlines())
    assert any("an entry of 4 lines" in p for p in planted), planted
    assert any("`### Engineering`" in p for p in planted), planted
    print(f"test_changelog_sections_after_0_2_0_keep_the_short_shape OK ({shaped}, "
          f"{entries} entries in [Unreleased], {end - head} lines)")


def test_every_changelog_fragment_parses():
    """Every `changes/*.md` is one or more Added / Changed / Fixed groups of bullets of at most 2
    lines — what the fold can place. A malformed one would stop the release step, so it fails here,
    in the PR that wrote it."""
    cl = _changelog_module()
    assert os.path.isdir(_CHANGES), "changes/ is gone: every PR's changelog fragment lives there"
    paths = sorted(p for p in os.listdir(_CHANGES) if p.endswith(".md"))
    bad = []
    for name in paths:
        try:
            cl.parse_fragment(_read("changes", name), f"changes/{name}")
        except ValueError as err:
            bad.append(str(err))
    assert not bad, "malformed changelog fragment(s):\n" + "\n".join(bad)
    # Both directions: a good fragment parses, and each shape of a bad one is refused by name.
    assert cl.parse_fragment(_GOOD_FRAGMENT) == {"Fixed": [["- A lift is no longer read as a brake"]]}
    for broken, why in (("- a bullet with no group\n", "a bullet before any"),
                        ("### Engineering\n\n- an internal refactor\n", "`### Engineering`"),
                        ("### Fixed\n\n- one\n  two\n  three\n", "an entry of 3 lines"),
                        ("### Fixed\n\nprose, not a bullet\n", "prose inside"),
                        ("A heading-less intro\n\n### Added\n\n- x\n", "text before the first")):
        try:
            cl.parse_fragment(broken)
        except ValueError as err:
            assert why in str(err), (why, str(err))
        else:
            raise AssertionError(f"parse_fragment accepted a malformed fragment: {broken!r}")
    print(f"test_every_changelog_fragment_parses OK ({len(paths)} fragment(s) in changes/)")


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def test_the_fold_round_trips_fragments_into_the_changelog():
    """The release step, on a copy of the real changelog: fragments land at the top of their
    groups, in the changelog's own shape, and are deleted; a release gets its heading, a fresh
    [Unreleased] and its compare link; a malformed fragment or an entry with no PR writes nothing."""
    import tempfile
    cl = _changelog_module()
    real = _read("CHANGELOG.md")
    history = real[real.index("\n## [0.2.0]"):]
    with tempfile.TemporaryDirectory() as tmp:
        _write(os.path.join(tmp, "CHANGELOG.md"), real)
        _write(os.path.join(tmp, "changes", "a-fix.md"),
               "### Fixed\n\n- A lift is no longer read as a brake: strings of one-sample blips\n"
               "  counted as braking (#9001)\n")
        _write(os.path.join(tmp, "changes", "b-feature.md"),
               "### Changed\n\n- One name for grip (#9002)\n\n### Added\n\n- A new thing (#9002)\n")
        text, _target, problems = cl.fold(tmp, write=True)
        assert not problems, problems
        assert _read_abs(tmp, "CHANGELOG.md") == text and not os.listdir(os.path.join(tmp, "changes"))
        assert text.endswith(history), "the fold touched a section older than [Unreleased]"
        before = real[real.index("## [Unreleased]"):real.index("\n## [0.2.0]")]
        body = text[text.index("## [Unreleased]"):text.index("\n## [0.2.0]")]
        added = {"Added": "- A new thing (#9002)", "Changed": "- One name for grip (#9002)",
                 "Fixed": "- A lift is no longer read as a brake: strings of one-sample blips "
                          "counted as braking (#9001)"}
        for group, first in added.items():   # each at the top of its group, above the old top
            old_top = before.split(f"### {group}\n\n", 1)[1].split("\n", 1)[0]
            assert f"### {group}\n\n{first}\n{old_top}\n" in body, (group, first, old_top)
        # ...and nothing else moved: taking the three lines out again gives back the section as it was.
        assert [ln for ln in body.split("\n") if ln not in added.values()] == before.split("\n")

        # A malformed fragment folds nothing, and neither does an entry whose PR is unknown (no git
        # history in a temporary directory to find it from): both leave every file as it was.
        _write(os.path.join(tmp, "changes", "c-bad.md"), "- a bullet with no group\n")
        try:
            cl.fold(tmp, write=True)
        except ValueError as err:
            assert "changes/c-bad.md" in str(err), str(err)
        else:
            raise AssertionError("the fold accepted a malformed fragment")
        _write(os.path.join(tmp, "changes", "c-bad.md"), "### Fixed\n\n- No PR number here\n")
        _t, _s, problems = cl.fold(tmp, write=True)
        assert any("does not end in its PR number" in p for p in problems), problems
        assert _read_abs(tmp, "CHANGELOG.md") == text and os.path.exists(
            os.path.join(tmp, "changes", "c-bad.md")), "a refused fold wrote something"

        # The release form: a new heading, a fresh [Unreleased] above it, and both compare links.
        _write(os.path.join(tmp, "changes", "c-bad.md"), "### Fixed\n\n- Now with its PR (#9003)\n")
        released, target, problems = cl.fold(tmp, release="9.9.9", date="2030-01-02", write=True)
        assert not problems, problems
        assert target.startswith("## [9.9.9] — 2030-01-02\n"), target[:40]
        assert "\n## [Unreleased]\n\n## [9.9.9] — 2030-01-02\n" in released
        assert "- Now with its PR (#9003)" in target and "- A new thing (#9002)" in target
        links = re.findall(r"^\[(Unreleased|9\.9\.9)\]: (\S+)$", released, re.MULTILINE)
        assert links == [("Unreleased", "https://github.com/eenndan/pacer/compare/v9.9.9...HEAD"),
                         ("9.9.9", "https://github.com/eenndan/pacer/compare/v0.2.0...v9.9.9")], links
        assert _RELEASED_HEADING.findall(released)[:2] == ["9.9.9", "0.2.0"]
    print("test_the_fold_round_trips_fragments_into_the_changelog OK")


def _read_abs(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def test_the_fold_finds_a_fragments_pr_from_the_merge_that_added_it():
    """A fragment need not know its PR number: on `main` the first-parent commit that added it is
    GitHub's "Merge pull request #N from …". Driven through a real merge in a scratch repository."""
    import subprocess
    import tempfile
    cl = _changelog_module()
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
                   GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                   GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")

        def git(*args):
            subprocess.run(["git", "-C", tmp, "-c", "commit.gpgsign=false", *args], env=env,
                           check=True, capture_output=True)

        _write(os.path.join(tmp, "CHANGELOG.md"), _read("CHANGELOG.md"))
        git("init", "-q", "-b", "main")
        git("add", "CHANGELOG.md")
        git("commit", "-q", "-m", "base")
        git("checkout", "-q", "-b", "f1/some-fix")
        _write(os.path.join(tmp, "changes", "f1-some-fix.md"), "### Fixed\n\n- A fix with no number\n")
        git("add", "changes")
        git("commit", "-q", "-m", "the fix, with its fragment")
        # Off main, on the branch that wrote it, there is no merge to name the PR yet.
        assert cl.merged_pr(tmp, os.path.join(tmp, "changes", "f1-some-fix.md")) is None
        git("checkout", "-q", "main")
        git("merge", "-q", "--no-ff", "-m", "Merge pull request #4242 from eenndan/f1/some-fix",
            "f1/some-fix")
        # A squash merge lands as one commit on main whose subject GitHub ends with "(#N)".
        _write(os.path.join(tmp, "changes", "f2-squashed.md"), "### Added\n\n- A squashed feature\n")
        git("add", "changes")
        git("commit", "-q", "-m", "F2: a squashed feature (#4243)")
        text, _target, problems = cl.fold(tmp, write=True)
        assert not problems, problems
        assert "### Fixed\n\n- A fix with no number (#4242)\n" in text
        assert "### Added\n\n- A squashed feature (#4243)\n" in text
    print("test_the_fold_finds_a_fragments_pr_from_the_merge_that_added_it OK")


# ------------------------------------------------------------------------------ the product name
# THE NAME CONVENTION (written down beside APP_NAME in studio/__init__.py): three forms, one job each.
#   FORMAL   "Pacer Studio": APP_NAME, the macOS bundle, the .dmg, the application/display names,
#            window and About titles, the landing page's product name, exported file headers.
#   SHORT    "Pacer": the product in a sentence, the welcome headline, the README's title.
#   WORDMARK "pacer": the share card's logotype only.
# What had drifted was a FOURTH form, lowercase "pacer" in 34 user-visible sentences (U5).
_FORMAL, _SHORT, _WORDMARK = "Pacer Studio", "Pacer", "pacer"
# The formal name in any casing or joiner ("pacer studio", "Pacer studio", "PacerStudio"...). Only
# two spellings are legal: the name itself, and its hyphenated form inside the .dmg FILE name.
_FORMAL_ANY = re.compile(r"pacer[ _-]?studio", re.IGNORECASE)
_DMG_STEM = _FORMAL.replace(" ", "-") + "-"
# A lowercase `pacer` used as the product's NAME inside a user-visible string. Not a path, a file
# name, an identifier or a module (`pacer.json`, `.../pacer/`, `pacer::Laps`, `_pacer`).
_LOWERCASE_NAME = re.compile(r"(?<![\w./~-])pacer(?![\w./:-])")
# Where a lowercase `pacer` in a string literal is NOT the product name — each a decision with a
# reason, capped at today's count so a NEW lowercase name in the file still fails.
_LOWERCASE_ALLOWED = {
    # Identifiers: the app-support folder name and the Qt organisation (an identifier, not a label).
    "app_support.py": 1,
    "app.py": 1,
    # An exported provenance CSV's metadata KEY ("pacer provenance"): a file-format field a
    # spreadsheet or script may already key on, not prose.
    "provenance.py": 1,
    # The WORDMARK form: the share card's logotype, set as a graphic.
    "share_card.py": 1,
}


def _app_name_off_disk():
    m = re.search(r'^APP_NAME\s*=\s*"([^"]+)"', _read("studio", "__init__.py"), re.MULTILINE)
    assert m, "studio/__init__.py no longer declares APP_NAME = \"...\""
    return m.group(1)


def _prose_literals(path):
    """Every non-docstring string constant in one module (f-string parts included)."""
    import ast
    tree = ast.parse(_read(path), path)
    docs = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docs.add(id(first.value))
    return [n for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def _misspelt_formal(text):
    """Every spelling of the formal name in `text` other than the name itself or the .dmg stem."""
    out = []
    for m in _FORMAL_ANY.finditer(text):
        if m.group(0) == _FORMAL or text[m.start():m.end() + 1] == _DMG_STEM:
            continue
        out.append(m.group(0))
    return out


def test_the_product_name_follows_its_three_form_convention():
    """U5. The formal name is the one the bundle already carries, and every surface that states the
    product's IDENTITY says it; a sentence says the short form; the share card's logotype is the
    only lowercase one; and no fourth spelling (a mis-cased formal name, a lowercase name in a
    sentence) can creep back."""
    import studio
    formal = _app_name_off_disk()
    assert formal == _FORMAL and studio.APP_NAME == _FORMAL, (
        f"APP_NAME is {formal!r}; the product's formal name is {_FORMAL!r}. Renaming the product "
        "is the owner's decision, not a refactor")

    # FORMAL — the .app (bundle, executable, and the two Info.plist keys the menu bar and Finder
    # read), the .dmg, CI's bundle check, the running app's own names, and the landing page.
    spec = _read("packaging", "pacer.spec")
    for key in ("CFBundleName", "CFBundleDisplayName"):
        m = re.search(rf'"{key}":\s*"([^"]+)"', spec)
        assert m and m.group(1) == _FORMAL, f"pacer.spec {key} = {m and m.group(1)!r}"
    names = re.findall(r'^\s*name="([^"]+)"', spec, re.MULTILINE)
    assert names == [_FORMAL, _FORMAL, f"{_FORMAL}.app"], f"pacer.spec EXE/COLLECT/BUNDLE {names}"
    sh = _read("packaging", "build_macos.sh")
    m = re.search(r'^APP_NAME="([^"]+)"', sh, re.MULTILINE)
    assert m and m.group(1) == _FORMAL, f"build_macos.sh APP_NAME={m and m.group(1)!r}"
    assert f"/dist/{_DMG_STEM}${{VERSION}}.dmg" in sh, "build_macos.sh's .dmg name drifted"
    ci = re.search(r'APP="dist/([^"]+)\.app"', _read(".github", "workflows", "ci.yml"))
    assert ci and ci.group(1) == _FORMAL, f"ci.yml checks dist/{ci and ci.group(1)}.app"
    app_py = _read("studio", "app.py")
    for call in ("setApplicationName", "setApplicationDisplayName"):
        m = re.search(rf'app\.{call}\((?:"([^"]+)"|APP_NAME)\)', app_py)
        assert m and m.group(1) in (None, _FORMAL), f"app.py {call}({m and m.group(1)!r})"
    page = _read("docs", "index.html")
    title = re.search(r"<title>([^<]+)</title>", page).group(1)
    og = re.search(r'property="og:title" content="([^"]+)"', page).group(1)
    brand = re.search(r'<a class="brand"[^>]*aria-label="([^"]+)"', page).group(1)
    for what, text in (("<title>", title), ("og:title", og)):
        assert text.startswith(f"{_FORMAL} — "), f"docs/index.html {what} is {text!r}"
    assert brand == _FORMAL, f"docs/index.html brand aria-label is {brand!r}"

    # SHORT — the README's title.
    readme_h1 = _read("README.md").splitlines()[0]
    assert readme_h1 == f"# {_SHORT}", f"README.md opens {readme_h1!r}"

    # WORDMARK — the share card's logotype (and, by the allow-list below, nowhere else).
    card = [n.value for n in _prose_literals(os.path.join("studio", "share_card.py"))]
    assert _WORDMARK in card, "the share card no longer sets its lowercase logotype"

    # NO FOURTH SPELLING, anywhere a user or the build reads (CHANGELOG is history, exempt).
    bad = []
    public = ["README.md"]
    for top in ("docs", "packaging", ".github"):
        for dirpath, _dirs, files in os.walk(_repo(top)):
            public += [os.path.relpath(os.path.join(dirpath, fn), _REPO) for fn in files
                       if fn.endswith((".md", ".html", ".sh", ".spec", ".yml", ".yaml", ".py"))]
    for rel in public:
        bad += [f"{rel}: {w!r}" for w in _misspelt_formal(_read(rel))]
    lower = {}
    for fn in sorted(os.listdir(_repo("studio"))):
        if not fn.endswith(".py"):
            continue
        for node in _prose_literals(os.path.join("studio", fn)):
            bad += [f"studio/{fn}:{node.lineno}: {w!r}" for w in _misspelt_formal(node.value)]
            for _ in _LOWERCASE_NAME.findall(node.value):
                lower.setdefault(fn, []).append((node.lineno, node.value[:60]))
    assert not bad, f"the formal name is spelled some other way: {bad}"
    over = {fn: hits for fn, hits in lower.items() if len(hits) > _LOWERCASE_ALLOWED.get(fn, 0)}
    assert not over, (
        f"the product is spelled lowercase in user-visible text — a sentence says {_SHORT!r}, a "
        f"title or an export says APP_NAME: {over}")
    print(f"test_the_product_name_follows_its_three_form_convention OK (formal {_FORMAL!r}, short "
          f"{_SHORT!r}, wordmark {_WORDMARK!r}; {sum(len(v) for v in lower.values())} allowed "
          f"lowercase literals in {sorted(lower)})")


def _prose(text):
    """`text` without its code: fenced blocks, inline code spans, <code>/<pre> and URLs. What is
    left is what a reader reads as words. Identifiers keep their own spelling (`pacer`, the
    bindings package; `~/Library/Application Support/pacer`), so they are not the product's name."""
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    text = re.sub(r"`[^`\n]*`", "", text)
    text = re.sub(r"<(code|pre)\b[^>]*>.*?</\1>", "", text, flags=re.S)
    return re.sub(r"https?://\S+", "", text)


def test_the_published_prose_never_spells_the_product_lowercase():
    """U5's fourth form, outside the app. The convention test above checks the README and docs/
    only for a mis-spelt FORMAL name. It checks the lowercase product name ("pacer keeps its own
    marks") only in studio/'s string literals, so a lowercase name in the README's prose passed.
    The README is the first page of the repository, and it carried exactly one.

    Scope: the README and every Markdown/HTML page under docs/, with code stripped. CHANGELOG is
    history and stays exempt, as above."""
    pages = ["README.md"]
    for dirpath, _dirs, files in os.walk(_repo("docs")):
        pages += [os.path.relpath(os.path.join(dirpath, fn), _REPO) for fn in files
                  if fn.endswith((".md", ".html"))]
    bad = []
    for rel in sorted(pages):
        text = _prose(_read(rel))
        for m in _LOWERCASE_NAME.finditer(text):
            bad.append(f"{rel}: …{text[max(0, m.start() - 40):m.end() + 25]!r}")
    assert not bad, (f"the product is spelled lowercase in published prose — a sentence says "
                     f"{_SHORT!r}: {bad}")
    print(f"test_the_published_prose_never_spells_the_product_lowercase OK ({len(pages)} pages)")


# ------------------------------------------------------------------------------------- runner
def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} version-consistency tests passed")


if __name__ == "__main__":
    _run_all()
