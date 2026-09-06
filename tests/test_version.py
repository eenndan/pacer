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


# ------------------------------------------------------------------------------------- runner
def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"ok  {fn.__name__}")
    print(f"\n{len(fns)} version-consistency tests passed")


if __name__ == "__main__":
    _run_all()
