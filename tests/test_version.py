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
